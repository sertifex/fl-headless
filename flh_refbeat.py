"""flh_refbeat: fl_beat_from_reference - 'make a beat like this song' in ONE call.

What it does (each step reuses the server's own tools through ns):
  1. listen once: the reference is decoded ONCE (ns['_wav_load']: WAV/FLAC/Ogg/AIFF everywhere, MP3/M4A through
     Windows' own decoders) and that decode is served to audio_detect (tempo/key, on a short loud excerpt),
     audio_analyze (tone/loudness in 4 s windows) and audio_to_midi modes drums / chords / bass (given the samples,
     no second decode). The measurements are cached by the file's SHA-1 in <state folder>/refbeat/, so a second
     call on the same file (another genre, a dry run first) skips the listening.
  2. derive: tempo (double/half time settled for the genre: a trap reference read at 75 BPM is built at 150 with the
     half-time feel), key (the readings combined; relative major/minor settled by the chord loop), sections (louder
     vs quieter 8-bar phrases), per section the main chord loop (the chart simplified to its most common 1-8 bar
     cycle of 1-4 chords, mapped to the key), drum density (kicks / snares / hats per bar), sub weight (bands) and
     brightness (centroid) -> vibe words. genre=None guesses it from tempo, drums and tone (data table, said).
  3. build: flh_new_song(genre, bpm, key, vibe, open=False) (its starter beat + the genre's form, markers), then ONE
     flp_edit batch: flp_compose(what='beat', section, progression) per detected section (+ flp_adjust busier /
     emptier when the reference's drums are clearly denser / sparser), flp_arrange_song(mode='replace'). The new
     file isn't open in FL, so no FL reload happens. Then fl_open once (Windows) and the level balance once
     (flp_balance in the background; with open=false it stays pending until the song is first opened).

copy='feel' (default): tempo, key, the chord loop's harmony, drum density and tone carry over; every note is newly
written - the reference's melody, bassline and drum pattern are never reproduced note-for-note (a 'type beat').
copy='tempo': only tempo and key carry over (the genre's own progressions). There is no note-copying mode.

Data: the genre-guess table, vibe thresholds and limits are DEFAULTS below; data/refbeat/refbeat.json (shipped) and
<state folder>/refbeat/refbeat.json (user / community) override them key by key.

PC-VERIFY: see checklists/refbeat.md (an MP3 through Windows' decoders, a real trap / lofi / house reference: the
half-time choice, the chord loop by ear, the vibe words, the open + balance with FL)."""
import contextlib
import hashlib
import json
import math
import os
import re
import sys
import time

class _LazyNumpy:
    """numpy loads on first use (keeps server start fast on weak PCs); then the global `np` is numpy itself."""
    def __getattr__(self, name):
        import numpy
        globals()["np"] = numpy
        return getattr(numpy, name)


np = _LazyNumpy()

H = None
CACHE_VERSION = 2

DEFAULTS = {
    "max_analyze_s": 360,            # longer references: the first 6 minutes are analysed
    "detect_excerpt_s": 24,          # audio_detect's excerpt (its frame matrix grows with the length)
    "bass_excerpt_s": 40,            # the bass transcription's excerpt (the slowest mode)
    "tone_window_s": 4,
    "cache_keep": 40,
    "energy_split_db": 3.0,          # louder vs quieter phrases: this far apart or it's one section
    "phrase_bars": 8,
    "loop_agree": 0.8,
    "borrowed_min_share": 0.2,       # a chord outside the key stays only when it fills this share of the bars
    "busier_ratio": 1.4,             # reference drum hits per bar vs the composed beat's -> flp_adjust busier
    "emptier_ratio": 0.65,
    "vibe": {"dark_hz": 1100, "bright_hz": 2600, "heavy_sub_db": -10.0, "dusty_air_db": -26.0,
             "dusty_max_hz": 1800, "aggressive_crest_db": 9.0, "aggressive_min_hz": 1800, "warm_low_mid_db": -8.0},
    # genre guess: per-beat rates at the genre's tempo (center, width) and tone; prior = how common the request is
    "guess": {
        "trap":      {"prior": 1.0,  "kick": [0.45, 0.3], "snare": [0.25, 0.12], "hat": [3.0, 1.6], "sub": [-8, 5]},
        "drill-uk":  {"prior": 0.7,  "kick": [0.35, 0.25], "snare": [0.25, 0.12], "hat": [2.5, 1.2], "sub": [-8, 5]},
        "boombap":   {"prior": 0.8,  "kick": [0.6, 0.3], "snare": [0.5, 0.12], "hat": [2.0, 0.8], "sub": [-16, 6],
                      "centroid": [1600, 900]},
        "lofi":      {"prior": 0.9,  "kick": [0.55, 0.3], "snare": [0.5, 0.12], "hat": [2.0, 0.8], "sub": [-18, 6],
                      "centroid": [1100, 600]},
        "rnb":       {"prior": 0.7,  "kick": [0.6, 0.3], "snare": [0.5, 0.15], "hat": [2.0, 1.2], "sub": [-12, 6]},
        "pop":       {"prior": 0.8,  "kick": [0.75, 0.35], "snare": [0.5, 0.15], "hat": [2.0, 1.2], "sub": [-13, 6],
                      "centroid": [2200, 1000]},
        "house":     {"prior": 0.8,  "kick": [1.0, 0.12], "snare": [0.5, 0.25], "hat": [2.0, 1.2], "sub": [-11, 6]},
        "techno":    {"prior": 0.6,  "kick": [1.0, 0.12], "snare": [0.3, 0.3], "hat": [2.5, 1.5], "sub": [-11, 6]},
        "dnb":       {"prior": 0.6,  "kick": [0.4, 0.25], "snare": [0.5, 0.15], "hat": [2.5, 1.5], "sub": [-11, 6]},
        "afrobeats": {"prior": 0.6,  "kick": [0.8, 0.3], "snare": [0.4, 0.2], "hat": [3.0, 1.5], "sub": [-12, 6]},
        "reggaeton": {"prior": 0.6,  "kick": [1.0, 0.15], "snare": [0.75, 0.25], "hat": [2.0, 1.5], "sub": [-11, 6]},
        "phonk":     {"prior": 0.5,  "kick": [0.6, 0.3], "snare": [0.4, 0.2], "hat": [2.5, 1.5], "sub": [-8, 5]},
    },
    "estimate_s": {"new_song": 3.0, "batch": 1.5, "open": 25.0, "balance": 40.0, "analysis_per_min": 8.0},
}
_DATA = {}
DRUM_CLASS = {35: "kick", 36: "kick", 38: "snare", 40: "snare", 37: "snare", 42: "hat", 44: "hat", 46: "hat"}


# ============================================================================= small helpers
def _mod(name):
    m = sys.modules.get(name)
    if m is None:
        import importlib
        m = importlib.import_module(name)
    return m


def _state_dir():
    return os.path.join(os.path.dirname(H["STATE"]), "refbeat")


def data():
    """DEFAULTS with data/refbeat/refbeat.json (shipped) and <state>/refbeat/refbeat.json (user) on top."""
    files = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "refbeat", "refbeat.json")]
    try:
        files.append(os.path.join(_state_dir(), "refbeat.json"))
    except Exception:
        pass
    sig = tuple((f, os.path.getmtime(f)) for f in files if os.path.isfile(f))
    if _DATA.get("sig") == sig and "d" in _DATA:
        return _DATA["d"]
    d = json.loads(json.dumps(DEFAULTS))
    for f, _ in sig:
        try:
            with open(f, encoding="utf-8") as fh:
                extra = json.load(fh)
        except (OSError, ValueError):
            continue
        for k, v in extra.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and isinstance(d.get(k), dict):
                d[k].update(v)
            else:
                d[k] = v
    _DATA.update(sig=sig, d=d)
    return d


def _pkey(p):
    return os.path.normcase(os.path.abspath(str(p)))


def _file_hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _err(e):
    return f"{type(e).__name__}: {str(e)[:160]}"


@contextlib.contextmanager
def _serving(path, x, fs, bits=16):
    """While inside: ns['_wav_load'](path) returns these samples (the one decode), every other file decodes as usual.
    The server's audio tools look _wav_load up in the server namespace at call time, so they get the samples."""
    orig = H.get("_wav_load"); key = _pkey(path)

    def wl(p, *a, **k):
        if _pkey(p) == key:
            return x, fs, bits
        return orig(p, *a, **k)
    H["_wav_load"] = wl
    try:
        yield
    finally:
        if H.get("_wav_load") is wl:
            H["_wav_load"] = orig


def _serves(fn_name):
    """The tool reads _wav_load from the namespace we patch (so the decode is shared, not repeated)."""
    fn = H.get(fn_name)
    fn = getattr(fn, "__wrapped__", fn)
    return getattr(fn, "__globals__", None) is H


def _loudest(env_db, hop_s, span_s, total):
    """Start (s) of the loudest span_s window of the envelope."""
    n = max(1, int(span_s / hop_s))
    if len(env_db) <= n:
        return 0.0
    p = 10 ** (np.asarray(env_db) / 10.0)
    c = np.convolve(p, np.ones(n), "valid")
    return float(min(max(0.0, total - span_s), int(np.argmax(c)) * hop_s))


# ============================================================================= 1. listen (cached)
def _parse_chart(chart, bpm, bpb=4):
    """audio_to_midi's chord chart ('| Am | - | F G |', bars at its tempo, bar 1 at 0 s) -> [[t0, t1, label]]."""
    bar_s = bpb * 60.0 / float(bpm)
    bars = [b.strip() for b in str(chart or "").split("|")[1:]]
    segs = []
    prev = None
    for i, b in enumerate(bars):
        if not b and i == len(bars) - 1:
            break
        labs = [t for t in b.split() if t]
        if not labs or labs == ["-"]:
            labs = [prev] if prev else []
        t0 = i * bar_s
        for j, lab in enumerate(labs):
            a, z = t0 + j * bar_s / len(labs), t0 + (j + 1) * bar_s / len(labs)
            if segs and segs[-1][2] == lab and abs(segs[-1][1] - a) < 1e-6:
                segs[-1][1] = round(z, 3)
            else:
                segs.append([round(a, 3), round(z, 3), lab])
        if labs:
            prev = labs[-1]
    return segs


def _rows_to_times(rows, bpm, bpb=4, offset=0.0):
    bar_s = bpb * 60.0 / float(bpm)
    return [(offset + (float(r[0]) - 1.0) * bar_s, float(r[1]) * bar_s, int(r[2]), int(r[3])) for r in rows]


def analyze(reference, steps=None):
    """-> (measurements, cached). Measurements are genre-independent: what the reference IS."""
    steps = steps if steps is not None else {}
    path = str(reference)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    t = time.time()
    sha = _file_hash(path)
    cf = os.path.join(_state_dir(), sha[:24] + ".json")
    if os.path.isfile(cf):
        try:
            with open(cf, encoding="utf-8") as f:
                meas = json.load(f)
            if meas.get("v") == CACHE_VERSION and meas.get("sha1") == sha:
                try:
                    os.utime(cf, None)                            # newest-used stays when the cache is trimmed
                except OSError:
                    pass
                steps["analysis_cached"] = round(time.time() - t, 3)
                return meas, True
        except (OSError, ValueError):
            pass
    D = data()
    meas = {"v": CACHE_VERSION, "sha1": sha, "file": os.path.basename(path), "errors": {}, "steps_s": {}}
    ms = meas["steps_s"]
    t = time.time()
    # PC-VERIFY: an MP3 / M4A reference decodes through Windows Media Foundation (fl_audio_edit._media_load) once;
    # analysis_decode in steps_s stays a few seconds for a 3-4 min song and the result matches a WAV of it
    x, fs, bits = H["_wav_load"](path)                            # the ONE decode
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    fs = int(fs)
    meas["seconds"] = round(len(x) / fs, 2)
    cap = int(float(D["max_analyze_s"]) * fs)
    if len(x) > cap:
        x = x[:cap]
        meas["analysed_s"] = round(cap / fs, 1)
    mono = x.mean(1) if x.shape[1] > 1 else x[:, 0]
    if not np.isfinite(mono).all() or float(np.abs(mono).max() if len(mono) else 0) < 1e-5:
        raise ValueError("the reference is silent or damaged (no usable audio)")
    hop = max(1, int(fs * 0.1))
    nh = len(mono) // hop
    rms = np.sqrt((mono[:nh * hop] ** 2).reshape(nh, hop).mean(1) + 1e-12) if nh else np.zeros(1)
    meas["env_hop_s"] = round(hop / fs, 4)
    meas["env_db"] = [round(float(v), 1) for v in 20 * np.log10(rms + 1e-9)]
    ms["decode"] = round(time.time() - t, 2)
    # analysis copy (~22 kHz) shared by the transcriptions
    y, wfs = mono, fs
    try:
        tr = _mod("flh_transcribe")
        y2, f2 = tr.work_rate(mono, fs)
        if abs(f2 - round(f2)) < 1e-6:
            y, wfs = y2, int(round(f2))
    except Exception:
        pass
    a2m = (H["TOOLS"].get("audio_to_midi") or (None,))[0]
    # drums: the grid every other measurement lines up with
    drum_bpm = None
    t = time.time()
    try:
        if a2m is None:
            raise RuntimeError("audio_to_midi isn't available (flh_transcribe not loaded)")
        r = a2m(input=(y, wfs), mode="drums", full=True)
        bpm = float(r["tempo"]["bpm"])
        hits = {"kick": [], "snare": [], "hat": [], "perc": []}
        for t0, _, k, _v in _rows_to_times((r.get("notes") or {}).get("drums") or [], bpm):
            hits[DRUM_CLASS.get(k, "perc")].append(round(t0, 3))
        meas["drums"] = {"hits": hits, "confidence": (r.get("parts") or {}).get("drums", {}).get("confidence")}
        meas["grid"] = {"bpm": round(bpm, 2), "confidence": r["tempo"].get("confidence"),
                        "detected": r["tempo"].get("detected"), "alternatives": r["tempo"].get("alternatives")}
        if r["tempo"].get("detected") and (r["tempo"].get("confidence") or 0) >= 0.4:
            drum_bpm = bpm
    except Exception as e:
        meas["errors"]["drums"] = _err(e)
    ms["drums"] = round(time.time() - t, 2)
    t = time.time()
    try:
        if a2m is None:
            raise RuntimeError("audio_to_midi isn't available (flh_transcribe not loaded)")
        # no tempo= here: the chart's bars come from its own beat grid (a given tempo shifts the bar lines)
        r = a2m(input=(y, wfs), mode="chords", limit=1)
        ch = (r.get("parts") or {}).get("chords") or {}
        meas["chords"] = {"segments": _parse_chart(ch.get("chart"), r["tempo"]["bpm"]), "key": ch.get("key"),
                          "key_confidence": ch.get("key_confidence"), "confidence": ch.get("confidence")}
        if "grid" not in meas:
            meas["grid"] = {"bpm": round(float(r["tempo"]["bpm"]), 2), "confidence": r["tempo"].get("confidence"),
                            "detected": r["tempo"].get("detected"), "alternatives": r["tempo"].get("alternatives")}
    except Exception as e:
        meas["errors"]["chords"] = _err(e)
    ms["chords"] = round(time.time() - t, 2)
    t = time.time()
    try:
        if a2m is None:
            raise RuntimeError("audio_to_midi isn't available (flh_transcribe not loaded)")
        span = float(D["bass_excerpt_s"])
        a0 = _loudest(meas["env_db"], meas["env_hop_s"], span, len(mono) / fs)
        ia, ib = int(a0 * wfs), int(min(len(y), (a0 + span) * wfs))
        kw = {"tempo": drum_bpm} if drum_bpm else {}
        r = a2m(input=(y[ia:ib], wfs), mode="bass", full=True, **kw)
        rows = _rows_to_times((r.get("notes") or {}).get("bass") or [], r["tempo"]["bpm"], offset=a0)
        meas["bass"] = {"notes": [[round(a, 3), round(b, 3), k] for a, b, k, _v in rows],
                        "confidence": (r.get("parts") or {}).get("bass", {}).get("confidence"),
                        "range": (r.get("parts") or {}).get("bass", {}).get("range"), "from_s": round(a0, 2)}
    except Exception as e:
        meas["errors"]["bass"] = _err(e)
    ms["bass"] = round(time.time() - t, 2)
    t = time.time()
    try:
        if not _serves("audio_detect"):
            raise RuntimeError("audio_detect can't share the decode here (skipped rather than decoding again)")
        span = float(D["detect_excerpt_s"])
        a0 = _loudest(meas["env_db"], meas["env_hop_s"], span, len(y) / wfs)
        ex = np.asarray(y[int(a0 * wfs):int((a0 + span) * wfs)], dtype=np.float64)[:, None]
        with _serving(path, ex, wfs, bits):
            r = H["audio_detect"](path)
        meas["detect"] = {"bpm": r.get("bpm"), "alternatives": r.get("bpm_alternatives"), "key": r.get("key"),
                          "from_s": round(a0, 2)}
    except Exception as e:
        meas["errors"]["detect"] = _err(e)
    ms["detect"] = round(time.time() - t, 2)
    t = time.time()
    try:
        if not _serves("audio_analyze"):
            raise RuntimeError("audio_analyze can't share the decode here (skipped rather than decoding again)")
        w = int(D["tone_window_s"])
        with _serving(path, x, fs, bits):
            r = H["audio_analyze"](path, window_s=w)
        keep = ("lufs", "peak_dbfs", "crest_db", "bands_db", "centroid_hz", "width_db")
        meas["tone"] = {k: r["whole"].get(k) for k in keep}
        wins = []
        for s in r.get("sections") or []:
            mm = re.match(r"^(\d+):(\d\d)$", str(s.get("name") or ""))
            t0 = int(mm.group(1)) * 60 + int(mm.group(2)) if mm else len(wins) * w
            wins.append({"t0": t0, "t1": t0 + w, **{k: s.get(k) for k in ("lufs", "bands_db", "centroid_hz", "crest_db")}})
        meas["tone_windows"] = wins
    except Exception as e:
        meas["errors"]["tone"] = _err(e)
    ms["tone"] = round(time.time() - t, 2)
    if not any(k in meas for k in ("drums", "chords", "detect", "tone")):
        raise RuntimeError("every analysis step failed: " + "; ".join(f"{k}: {v}" for k, v in meas["errors"].items()))
    try:                                                          # own state folder only
        H["_json_write"](cf, meas)
        _trim_cache(int(D["cache_keep"]))
    except Exception:
        pass
    steps.update({f"analysis_{k}": v for k, v in ms.items()})
    return meas, False


def _trim_cache(keep):
    d = _state_dir()
    fs_ = sorted((os.path.join(d, f) for f in os.listdir(d) if re.match(r"^[0-9a-f]{24}\.json$", f)),
                 key=os.path.getmtime, reverse=True)
    for f in fs_[keep:]:
        try:
            os.remove(f)                                          # our own cache files in the state folder
        except OSError:
            pass


# ============================================================================= 2. derive
def _gauss(v, cw):
    c, w = float(cw[0]), max(1e-9, float(cw[1]))
    return math.exp(-0.5 * ((float(v) - c) / w) ** 2)


def _base_tempo(meas):
    det = (meas.get("detect") or {}).get("bpm")
    grid = meas.get("grid") or {}
    gb, gc = grid.get("bpm"), grid.get("confidence")
    base = float(det or gb or 0) or None
    agree = None
    if det and gb:
        r = float(gb) / float(det)
        agree = any(abs(r - f) / f < 0.03 for f in (0.5, 1.0, 2.0))
    conf = float(gc) if gc is not None else 0.5
    if agree is False:
        conf = min(conf, 0.45)
    elif agree is None:
        conf = min(conf, 0.7)
    return base, round(conf, 2), agree


def _rates(meas):
    """Drum hits per second over the stretch that has drums: {kick, snare, hat, perc}."""
    hits = (meas.get("drums") or {}).get("hits") or {}
    allh = sorted(t for v in hits.values() for t in v)
    if len(allh) < 8:
        return None
    span = max(4.0, allh[-1] - allh[0])
    return {k: len(v) / span for k, v in hits.items()}


def _tempo_choices(base, g):
    tmin, tmax = (g.get("tempo") or {}).get("min", 0), (g.get("tempo") or {}).get("max", 999)
    return [c for c in (base, base * 2, base / 2) if tmin * 0.97 <= c <= tmax * 1.03]


def _pick_tempo(base, g):
    """(bpm, how): the reading inside the genre's range nearest its default; else the nearest to the range."""
    tp = g.get("tempo") or {}
    tmin, tmax, tdef = tp.get("min", 0), tp.get("max", 999), tp.get("default") or 120
    if not base:
        return float(tdef), "genre default (no tempo measured)"
    ins = _tempo_choices(base, g)
    if ins:
        bpm = min(ins, key=lambda c: abs(math.log(c / tdef)))
    else:
        bpm = min((base, base * 2, base / 2), key=lambda c: min(abs(math.log(c / max(tmin, 1))), abs(math.log(c / tmax))))
    bpm = float(round(bpm)) if abs(bpm - round(bpm)) <= 1.0 else round(bpm, 1)
    how = "as read"
    if bpm > base * 1.5:
        how = (f"half-time feel: the reference reads {base:g} BPM, written at {bpm:g} ({g['name']} is felt in half time)"
               if tp.get("feel") == "half-time" else f"double time: read {base:g} BPM, {g['name']} sits at {bpm:g}")
    elif bpm < base / 1.5:
        how = f"half time: read {base:g} BPM, {g['name']} sits at {bpm:g}"
    if not ins:
        how += f" (outside {g['name']}'s usual {tmin}-{tmax} BPM)"
    return bpm, how


def guess_genre(meas):
    # PC-VERIFY: real references (a trap, a lofi, a house, a boom-bap song) are guessed as their genre or say the
    # right one as runner-up; the 'guess' table in DEFAULTS / data/refbeat/refbeat.json is tuned from those
    """[(genre, score 0-1, bpm)] best first, from tempo fit + drum rates per beat + sub weight + brightness."""
    cm = _mod("flh_compose")
    base, _, _ = _base_tempo(meas)
    rates = _rates(meas)
    tone = meas.get("tone") or {}
    sub = (tone.get("bands_db") or {}).get("sub")
    cen = tone.get("centroid_hz")
    out = []
    for name, spec in (data().get("guess") or {}).items():
        try:
            g = cm.genre_data(name)
        except Exception:
            continue
        tp = g.get("tempo") or {}
        cands = _tempo_choices(base, g) if base else [float(tp.get("default") or 120)]
        if not cands:
            continue
        best = None
        for c in cands:
            w = max(4.0, (tp.get("max", c) - tp.get("min", c)) / 2.0)
            logs, n = math.log(_gauss(c, [tp.get("default") or c, w]) + 1e-6), 1
            if rates:
                for part in ("kick", "snare", "hat"):
                    if part in spec:
                        logs += 1.5 * math.log(_gauss(rates.get(part, 0) * 60.0 / c, spec[part]) + 1e-4); n += 1.5
            if sub is not None and "sub" in spec:
                logs += math.log(_gauss(sub, spec["sub"]) + 1e-4); n += 1
            if cen and "centroid" in spec:
                logs += 0.7 * math.log(_gauss(cen, spec["centroid"]) + 1e-4); n += 0.7
            sc = math.exp(logs / n) * float(spec.get("prior", 0.7))
            if best is None or sc > best[0]:
                best = (sc, c)
        out.append((g["name"], round(best[0], 3), round(best[1], 1)))
    out.sort(key=lambda t: -t[1])
    return out


def _parse_label(lab):
    """'F#m7' -> (pc, 'm'|''|'dim') or None."""
    m = re.match(r"^([A-G])([#b]?)(.*)$", str(lab or "").strip())
    if not m:
        return None
    import fl_music
    pc = (fl_music.LETTER[m.group(1)] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12
    q = m.group(3).split("/")[0]
    if "dim" in q or "m7b5" in q or q.startswith(("ø", "°", "o")):
        return pc, "dim"
    if q.startswith(("m", "min", "-")) and not q.startswith(("maj", "M")):
        return pc, "m"
    return pc, ""


def _scale(key):
    root, mode, iv, fam = _mod("flh_compose").key_info(key)
    return root, [(root + i) % 12 for i in iv][:7], fam


def _diatonic_q(key, pc):
    root, sc, fam = _scale(key)
    if pc not in sc or len(sc) < 7:
        return None
    d = sc.index(pc)
    third = (sc[(d + 2) % 7] - pc) % 12
    fifth = (sc[(d + 4) % 7] - pc) % 12
    if fifth == 6:
        return "dim"
    return "m" if third == 3 else ""


def _chord_name(key, pc, q):
    cm = _mod("flh_compose")
    return cm._pc_name(pc, cm._flats_for(key)) + q


def combine_key(meas, loop_hint=None):
    """(key, confidence, alternative) from audio_detect's key list, the chord chart's key and the bass, with
    relative major/minor settled by which tonic chord the music sits on (time in the chart + the loop's start)."""
    cm = _mod("flh_compose")
    sources = []                                                  # (source, key, weight)
    for i, (k, c) in enumerate(((meas.get("detect") or {}).get("key") or [])[:3]):
        sources.append(("detect" if i == 0 else "detect_alt", k, float(c) * (1.0 if i == 0 else 0.6)))
    ch = meas.get("chords") or {}
    if ch.get("key"):
        sources.append(("chords", ch["key"], float(ch.get("key_confidence") or 0.5)))
    bass = (meas.get("bass") or {}).get("notes") or []
    if len(bass) >= 8:
        try:
            import fl_music
            kb = fl_music.detect_key([{"key": int(k), "len": max(1, int(d * 1000))} for _, d, k in bass], 2)
            sources.append(("bass", kb[0][0], 0.5 * float(kb[0][1])))
        except Exception:
            pass
    if not sources:
        return None, 0.0, None
    dur = {}
    for a_, b_, lab in ch.get("segments") or []:                 # chord time by (pc, quality)
        p = _parse_label(lab)
        if p:
            dur[p] = dur.get(p, 0.0) + (b_ - a_)
    tot = sum(dur.values()) or 1.0

    def family(k):                                                # the key and its relative, one family
        root, mode, iv, fam = cm.key_info(k)
        if fam == "minor":
            return [(f"{_chord_name(k, root, '')} minor", (root, "m")), (f"{_chord_name(k, (root + 3) % 12, '')} major",
                                                                          ((root + 3) % 12, ""))]
        return [(f"{_chord_name(k, root, '')} major", (root, "")), (f"{_chord_name(k, (root + 9) % 12, '')} minor",
                                                                    ((root + 9) % 12, "m"))]
    score, named = {}, {}
    for src, k, w in sources:
        try:
            fam_ = family(k)
        except Exception:
            continue
        for j, (kn, tonic) in enumerate(fam_):
            kk = cm.key_info(kn)[:2]
            named[kk] = named.get(kk, kn)
            score[kk] = score.get(kk, 0.0) + w * (1.0 if j == 0 else 0.8)
    for kk in score:
        root, mode = kk
        tonic = (root, "m" if "minor" in str(mode) else "")
        score[kk] += 0.8 * dur.get(tonic, 0.0) / tot + (0.3 if loop_hint and tuple(loop_hint) == tonic else 0.0)
    ranked = sorted(score.items(), key=lambda t: -t[1])
    top, s0 = ranked[0]
    s1 = ranked[1][1] if len(ranked) > 1 else 0.0
    top_name = named[top]
    top_fam = {cm.key_info(kn)[:2] for kn, _ in family(top_name)}
    agree = sum(1 for src, k, _ in sources if not src.endswith("_alt") and cm.key_info(k)[:2] in top_fam)
    main_src = sum(1 for src, _, _ in sources if not src.endswith("_alt"))
    conf = 0.35 + 0.35 * (s0 - s1) / max(s0, 1e-6) + 0.3 * agree / max(1, main_src)
    return top_name, round(max(0.1, min(0.95, conf)), 2), (named[ranked[1][0]] if len(ranked) > 1 else None)


def _bars(meas, bpm):
    """Bar grid at the build tempo: (t0 of bar 1, bar length s, number of bars)."""
    L = 240.0 / float(bpm)
    kicks = ((meas.get("drums") or {}).get("hits") or {}).get("kick") or []
    segs = (meas.get("chords") or {}).get("segments") or []
    first = kicks[0] if kicks else (segs[0][0] if segs else 0.0)
    t0 = float(first) % L
    dur = float(meas.get("analysed_s") or meas.get("seconds") or 0)
    return t0, L, max(1, int((dur - t0) // L))


def _bar_energy(meas, t0, L, n):
    env = np.asarray(meas.get("env_db") or [], float)
    hop = float(meas.get("env_hop_s") or 0.1)
    out = []
    for j in range(n):
        a, b = int((t0 + j * L) / hop), int((t0 + (j + 1) * L) / hop)
        seg = env[a:max(a + 1, b)]
        out.append(float(10 * np.log10(np.mean(10 ** (seg / 10)) + 1e-12)) if len(seg) else -120.0)
    return out


def detect_sections(meas, bpm, given=None):
    """[{level: main|second|intro|outro, bars: [bar indexes], t: [(a, b)], name?}] from louder/quieter phrases
    (or the caller's sections [{name, start_s, end_s}])."""
    D = data()
    t0, L, n = _bars(meas, bpm)
    if given:
        out = []
        for s in given:
            a, b = float(s.get("start_s", 0)), float(s.get("end_s", 0))
            bars = [j for j in range(n) if a - 1e-6 <= t0 + j * L and t0 + (j + 1) * L <= b + L * 0.5]
            if bars:
                out.append({"name": str(s.get("name") or f"part {len(out) + 1}"), "bars": bars, "given": True})
        en = _bar_energy(meas, t0, L, n)
        for s in out:
            s["energy_db"] = round(float(np.mean([en[j] for j in s["bars"]])), 1)
        return out, (t0, L, n)
    P = int(D["phrase_bars"]) if n >= 2 * int(D["phrase_bars"]) else (4 if n >= 8 else n)
    en = _bar_energy(meas, t0, L, n)
    blocks = [list(range(i, min(n, i + P))) for i in range(0, n, P)]
    if len(blocks) > 1 and len(blocks[-1]) < P / 2:
        blocks[-2] += blocks.pop()
    be = [float(np.mean([en[j] for j in b])) for b in blocks]
    lo, hi = min(be), max(be)
    level = ["main"] * len(blocks)
    if hi - lo >= float(D["energy_split_db"]) and len(blocks) >= 2:
        c1, c2 = lo, hi
        for _ in range(10):
            thr = (c1 + c2) / 2
            a_ = [e for e in be if e < thr]; b_ = [e for e in be if e >= thr]
            if not a_ or not b_:
                break
            c1, c2 = float(np.mean(a_)), float(np.mean(b_))
        thr = (c1 + c2) / 2
        if c2 - c1 >= float(D["energy_split_db"]):
            level = ["main" if e >= thr else "second" for e in be]
            fm = level.index("main"); lm = len(level) - 1 - level[::-1].index("main")
            for i in range(len(level)):                       # quiet phrases before / after all the loud ones
                if i < fm:
                    level[i] = "intro"
                elif i > lm:
                    level[i] = "outro"
    out = []
    for lv in ("main", "second", "intro", "outro"):
        idx = [i for i, v in enumerate(level) if v == lv]
        if idx:
            out.append({"level": lv, "bars": [j for i in idx for j in blocks[i]], "phrases": len(idx),
                        "energy_db": round(float(np.mean([be[i] for i in idx])), 1)})
    return out, (t0, L, n)


def _bar_chords(meas, key, t0, L, bars):
    segs = (meas.get("chords") or {}).get("segments") or []
    seq = []
    for j in bars:
        a, b = t0 + j * L, t0 + (j + 1) * L
        ov = {}
        for s0, s1, lab in segs:
            o = min(b, s1) - max(a, s0)
            if o > 0:
                ov[lab] = ov.get(lab, 0.0) + o
        lab = max(ov, key=ov.get) if ov and max(ov.values()) >= 0.3 * L else None
        seq.append(_parse_label(lab) if lab else None)
    return seq


def find_loop(seq, key):
    """Bar chords [(pc, q) | None] -> (loop [(name, bars)], agreement, notes): the most common 1-8 bar cycle
    with at most 4 chords, qualities mapped to the key (a chord outside it stays only when it's a real part)."""
    D = data()
    notes = []
    s = list(seq)
    for i in range(len(s)):                                       # no chord read: the previous one rings on
        if s[i] is None and i:
            s[i] = s[i - 1]
    for i in range(len(s) - 2, -1, -1):
        if s[i] is None:
            s[i] = s[i + 1]
    if not s or all(v is None for v in s):
        return None, 0.0, ["no chords read"]
    root, sc, fam = _scale(key)
    cnt = {}
    for v in s:
        cnt[v[0]] = cnt.get(v[0], 0) + 1
    mapped = []
    for i, (pc, q) in enumerate(s):
        dq = _diatonic_q(key, pc)
        if dq is None:
            if cnt[pc] / len(s) < float(D["borrowed_min_share"]):
                mapped.append(mapped[-1] if mapped else None)     # a passing misread: the previous chord
                continue
            mapped.append((pc, q))                            # borrowed: its own quality
            continue
        five = (root + 7) % 12
        mapped.append((pc, "" if (fam == "minor" and pc == five and q == "") else dq))
    for i in range(len(mapped) - 2, -1, -1):
        if mapped[i] is None:
            mapped[i] = mapped[i + 1]
    if any(v is None for v in mapped):
        mapped = [v for v in mapped if v is not None] or [(root, "m" if fam == "minor" else "")]
    n = len(mapped)
    cands = []
    for P in (1, 2, 4, 8, 16):
        if P > n:
            break
        pairs = n - P
        agree = (sum(1 for i in range(pairs) if mapped[i] == mapped[i + P]) / pairs) if pairs else 0.5
        loop = []
        for k in range(P):
            col = mapped[k::P]
            loop.append(max(set(col), key=lambda v: (col.count(v), -col.index(v))))
        runs = []
        for v in loop:
            if runs and runs[-1][0] == v:
                runs[-1][1] += 1
            else:
                runs.append([v, 1])
        if len(runs) > 1 and runs[0][0] == runs[-1][0]:           # the cycle wraps: one chord, not two
            runs[0][1] += runs.pop()[1]
        if len(runs) <= 4:
            cands.append((runs, agree, P))
    if not cands:
        return None, 0.0, ["no repeating chord cycle of up to 4 chords"]
    top = max(a for _, a, _ in cands)                             # the shortest cycle that repeats about as well
    best = next(c for c in cands if c[1] >= top - 0.05)
    runs, agree, P = best
    if len(runs) == 4 and P % 4 == 0 and P >= 4:
        each = P // 4
        if any(b != each for _, b in runs) and all(abs(b - each) <= max(1, each // 2) for _, b in runs):
            runs = [[v, each] for v, _ in runs]               # 2+2+3+1 is a misread 2+2+2+2 far more often than not
            notes.append(f"chord lengths evened out to {each} bar{'s' if each > 1 else ''} each (the chart was a bar off)")
    if any(_diatonic_q(key, v[0]) is None for v, _ in runs):
        notes.append("a chord outside the key is part of the loop (kept: it's a big share of the bars)")
    loop = [(_chord_name(key, pc, q), b) for (pc, q), b in runs]
    return loop, round(agree, 2), notes


def _density(meas, t0, L, bars):
    hits = (meas.get("drums") or {}).get("hits") or {}
    if not hits or not bars:
        return None
    spans = [(t0 + j * L, t0 + (j + 1) * L) for j in bars]
    out = {}
    for part in ("kick", "snare", "hat", "perc"):
        c = sum(1 for t in hits.get(part) or [] if any(a <= t < b for a, b in spans))
        out[part] = round(c / len(bars), 1)
    return out


def _tone_of(meas, t0, L, bars):
    wins = meas.get("tone_windows") or []
    spans = [(t0 + j * L, t0 + (j + 1) * L) for j in bars]
    sel = [w for w in wins if any(min(b, w["t1"]) - max(a, w["t0"]) > 0 for a, b in spans) and w.get("bands_db")]
    if not sel:
        return meas.get("tone") or None
    bands = {k: round(float(np.mean([w["bands_db"][k] for w in sel])), 1) for k in sel[0]["bands_db"]}
    lu = [w["lufs"] for w in sel if w.get("lufs") is not None]
    cr = [w["crest_db"] for w in sel if w.get("crest_db") is not None]
    return {"bands_db": bands, "centroid_hz": int(np.mean([w["centroid_hz"] for w in sel])),
            "lufs": round(float(np.mean(lu)), 1) if lu else None, "crest_db": round(float(np.mean(cr)), 1) if cr else None}


def vibe_words(tone):
    # PC-VERIFY: the thresholds give 'dark' / 'bright' / 'heavy' / 'dusty' that a listener agrees with on real mixes
    V = data()["vibe"]
    if not tone:
        return []
    b = tone.get("bands_db") or {}
    cen = tone.get("centroid_hz") or 0
    w = []
    if cen and cen < V["dark_hz"]:
        w.append("dark")
    elif cen > V["bright_hz"]:
        w.append("bright")
    if b.get("sub") is not None and b["sub"] >= V["heavy_sub_db"]:
        w.append("heavy")
    if b.get("air") is not None and b["air"] <= V["dusty_air_db"] and cen and cen < V["dusty_max_hz"]:
        w.append("dusty")
    if tone.get("crest_db") is not None and tone["crest_db"] < V["aggressive_crest_db"] and cen > V["aggressive_min_hz"]:
        w.append("aggressive")
    if not w or (len(w) < 2 and b.get("low_mid") is not None and b["low_mid"] >= V["warm_low_mid_db"]):
        if b.get("low_mid") is not None and b["low_mid"] >= V["warm_low_mid_db"]:
            w.append("warm")
    return w[:3]


def _genre_sections(g):
    """The genre form's section names other than intro/outro, loudest first (energy from the genre data)."""
    cm = _mod("flh_compose")
    form = ((g.get("form") or {}).get("default") or [])
    names = []
    for nm, _ in form:
        if str(nm).lower() not in ("intro", "outro") and nm not in names:
            names.append(nm)
    def energy(nm):
        try:
            return float((cm._section_spec(g, nm) or {}).get("energy", 0.5))
        except Exception:
            return 0.5
    return sorted(names, key=lambda nm: (-energy(nm), names.index(nm)))


def derive(meas, genre=None, sections=None, copy="feel"):
    """Measurements -> the build plan (genre, bpm, key, vibe, per-section progressions and densities) + guesses."""
    cm = _mod("flh_compose")
    guessed = []
    base, tconf, agree = _base_tempo(meas)
    ranking = None
    if genre:
        g = cm.genre_data(genre)
    else:
        ranking = guess_genre(meas)
        if not ranking:
            g = cm.genre_data("trap")
            guessed.append("genre trap: nothing measured to guess from (default)")
        else:
            g = cm.genre_data(ranking[0][0])
            guessed.append(f"genre {g['name']}: guessed from tempo, drums and tone (next: " +
                           ", ".join(f"{n} {s:.2f}" for n, s, _ in ranking[1:3]) + ") - give genre= to choose")
    bpm, how = _pick_tempo(base, g)
    if not base:
        guessed.append(f"tempo {bpm:g}: {how}")
    secs, (t0, L, n) = detect_sections(meas, bpm, sections)
    main = next((s for s in secs if s.get("level") == "main" or s.get("given")), secs[0] if secs else None)
    seq_main = _bar_chords(meas, "C major", t0, L, main["bars"]) if main else []
    hint = None
    if seq_main and any(seq_main):
        cnt = {}
        for v in seq_main:
            if v:
                cnt[v] = cnt.get(v, 0) + 1
        first = next(v for v in seq_main if v)
        hint = first if cnt.get(first, 0) * 2 >= max(cnt.values()) else max(cnt, key=cnt.get)
    key, kconf, kalt = combine_key(meas, hint)
    if not key:
        key = (g.get("keys") or {}).get("default") or "C minor"
        kconf = 0.0
        guessed.append(f"key {key}: the genre's default (no key measured)")
    gnames = _genre_sections(g)
    plan_secs = []
    for s in secs:
        if s.get("level") in ("intro", "outro"):
            continue
        if s.get("given"):
            nm = next((x for x in gnames if x.lower() == s["name"].lower()), None)
        else:
            nm = None
        plan_secs.append(dict(s, section=nm))
    ranked = sorted(plan_secs, key=lambda s: -s.get("energy_db", 0))
    free = [x for x in gnames if x not in {s["section"] for s in plan_secs if s["section"]}]
    for s in ranked:
        if not s["section"] and free:
            s["section"] = free.pop(0)
    plan_secs = [s for s in ranked if s["section"]]
    out_secs = []
    seen = {}
    for s in plan_secs:
        loop, agree_, lnotes = (None, 0.0, [])
        if copy == "feel" and meas.get("chords"):
            loop, agree_, lnotes = find_loop(_bar_chords(meas, key, t0, L, s["bars"]), key)
        prog = " ".join(f"{nm}:{b:g}" for nm, b in loop) if loop else None
        entry = {"section": s["section"], "bars_heard": len(s["bars"]), "energy_db": s.get("energy_db"),
                 "progression": prog, "loop_bars": int(sum(b for _, b in loop)) if loop else None,
                 "loop_agreement": agree_ if loop else None, "density": _density(meas, t0, L, s["bars"]),
                 "tone": _tone_of(meas, t0, L, s["bars"])}
        if lnotes:
            entry["notes"] = lnotes
        if s["section"] in seen:
            continue
        seen[s["section"]] = entry
        out_secs.append(entry)
    if len(out_secs) > 1 and out_secs[1]["progression"] == out_secs[0]["progression"] and copy == "feel":
        out_secs[1]["same_as"] = out_secs[0]["section"]
    if copy == "feel" and not any(e["progression"] for e in out_secs):
        guessed.append("chords: no clear loop read - the genre's own progressions are used")
    vibe = vibe_words((out_secs[0]["tone"] if out_secs else None) or meas.get("tone"))
    return {"genre": g["name"], "bpm": bpm, "tempo_how": how, "tempo_confidence": tconf, "tempo_sources_agree": agree,
            "base_bpm": base, "key": key, "key_confidence": kconf, "key_alternative": kalt, "vibe": vibe,
            "sections": out_secs, "bar_grid": {"first_bar_s": round(t0, 3), "bar_s": round(L, 3), "bars": n},
            "genre_ranking": ranking[:3] if ranking else None, "guessed": guessed}


# ============================================================================= 3. build
def _name_from(reference):
    stem = os.path.splitext(os.path.basename(str(reference)))[0]
    stem = re.sub(r"[^\w \-()&']+", " ", stem, flags=re.UNICODE)
    stem = re.sub(r"\s+", " ", stem).strip(" .-")[:40].strip()
    return f"{stem} Type Beat" if stem else None


def _short_sound(c):
    """'0 Kick: Dusty Kick SP -> insert 1 [why...]' -> '0 Kick: Dusty Kick SP' (the full list: flp_info)."""
    c = str(c).split(" -> insert")[0].split(" [")[0]
    return c[:70]


def _drum_hits_per_bar(dry):
    for w in dry.get("would_write") or []:
        if str(w.get("pattern") or "").lower().endswith("drums"):
            return float(w.get("notes") or 0) / max(1, int(dry.get("bars") or 8))
    return None


def _loop_text(prog):
    """'Am:2 F:2 C:2 G:2' -> 'Am F C G (2 bars each)'; uneven -> 'Am(2) F(1) ...'."""
    toks = [t.rsplit(":", 1) for t in str(prog).split()]
    lens = {b for _, b in toks}
    if len(lens) == 1:
        b = next(iter(lens))
        return " ".join(n for n, _ in toks) + f" ({b} bar{'s' if b != '1' else ''} each)"
    return " ".join(f"{n}({b})" for n, b in toks)


def _say_measured(meas, plan):
    m = {}
    if plan.get("base_bpm"):
        m["tempo"] = {"bpm": plan["base_bpm"], "confidence": plan["tempo_confidence"]}
    if (meas.get("chords") or {}).get("segments") or (meas.get("detect") or {}).get("key"):
        m["key"] = {"key": plan["key"], "confidence": plan["key_confidence"], "or": plan.get("key_alternative")}
    loops, dens = {}, {}
    for s in plan["sections"]:
        if s.get("progression"):
            loops[s["section"]] = _loop_text(s["progression"]) + (f" (repeats {s['loop_agreement']:.0%})"
                                                                   if s.get("loop_agreement") is not None else "")
        if s.get("density"):
            d = s["density"]
            dens[s["section"]] = f"{d['kick']:g} kicks, {d['snare']:g} snares, {d['hat']:g} hats per bar"
    if loops:
        m["chord_loop"] = loops
        cc = (meas.get("chords") or {}).get("confidence")
        if cc is not None:
            m["chords_confidence"] = cc
    if dens:
        m["drums"] = dens
    t = meas.get("tone")
    if t:
        b = t.get("bands_db") or {}
        m["tone"] = (f"{', '.join(plan['vibe']) or 'neutral'}: centroid {t.get('centroid_hz')} Hz, sub {b.get('sub')} dB, "
                     f"air {b.get('air')} dB (shares of the mix), {t.get('lufs')} LUFS")
    if meas.get("errors"):
        m["not_measured"] = {k: v[:100] for k, v in meas["errors"].items()}
    return m


COPY_NOTE = ("feel only: tempo, key, the chord loop's harmony, drum density and tone were carried over; every note is "
             "newly written - the reference's melody, bassline and drum pattern are not reproduced note-for-note "
             "(a 'type beat')")


def fl_beat_from_reference(reference, name=None, genre=None, sections=None, copy="feel", balance=None, open=True,
                           dry_run=False):
    T0 = time.time(); steps = {}
    if balance is None:                                   # a render nobody asked for: only when turned on
        balance = bool(H["auto_renders"]()) if H.get("auto_renders") else False
    mark = lambda s, t=[T0]: (steps.__setitem__(s, round(time.time() - t[0], 3)), t.__setitem__(0, time.time()))
    copy = str(copy or "feel").strip().lower()
    if copy not in ("feel", "tempo"):
        raise ValueError("copy: 'feel' (tempo, key, chord loop, density, tone; new notes) or 'tempo' (tempo + key "
                         "only). fl-headless never copies a reference's notes (melody, bassline, drum pattern)")
    if sections is not None and not (isinstance(sections, list) and all(isinstance(s, dict) for s in sections)):
        raise ValueError("sections: [{name, start_s, end_s}] (seconds in the reference)")
    meas, cached = analyze(reference, steps)
    mark("analysis")
    plan = derive(meas, genre, sections, copy)
    mark("derive")
    res = {"reference": os.path.basename(str(reference)), "analysis": "cached (same file as before)" if cached else "measured",
           "measured": _say_measured(meas, plan)}
    build = {"genre": plan["genre"], "bpm": plan["bpm"], "tempo": plan["tempo_how"], "key": plan["key"],
             "vibe": " ".join(plan["vibe"]) or None,
             "sections": [{k: s[k] for k in ("section", "progression", "same_as") if s.get(k)} for s in plan["sections"]]}
    if plan.get("genre_ranking"):
        build["genre_ranking"] = [f"{n} {s:.2f}" for n, s, _ in plan["genre_ranking"]]
    res["plan"] = build
    guessed = list(plan["guessed"])
    if plan.get("key_confidence", 1) < 0.5 and plan.get("key_alternative"):
        guessed.append(f"key {plan['key']} (could be {plan['key_alternative']}): low confidence")
    if plan.get("tempo_sources_agree") is False:
        guessed.append("tempo: the two readings disagree - check by ear (or give genre= for its range)")
    name = name or _name_from(reference)
    ES = data()["estimate_s"]
    will_open = bool(open) and bool(H.get("IS_WINDOWS"))
    if dry_run:
        est = {"new_song": ES["new_song"], "batch": ES["batch"]}
        if will_open:
            est["open"] = ES["open"]
        res["dry_run"] = True
        res["would"] = ([f"flh_new_song(genre='{plan['genre']}', bpm={plan['bpm']:g}, key='{plan['key']}', "
                         f"vibe='{build['vibe'] or ''}', name='{name}')"] +
                        [f"flp_edit: flp_compose(what='beat', section='{s['section']}', progression='{s['progression']}')"
                         for s in plan["sections"] if s.get("progression") and not s.get("same_as") and copy == "feel"] +
                        ["flp_arrange_song(mode='replace')"] + (["fl_open"] if will_open else []) +
                        (["flp_balance (background)" if will_open else "level balance pending until fl_open"]
                         if balance else []))
        res["estimate_s"] = dict(est, total=round(sum(est.values()), 1),
                                 balance_background=ES["balance"] if balance else 0)
        res["copy"] = COPY_NOTE if copy == "feel" else "tempo and key only; every note is newly written"
        if guessed:
            res["guessed"] = guessed
        res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
        return res
    warnings, notes = [], []
    ns_song = H["TOOLS"]["flh_new_song"][0]
    r = ns_song(genre=plan["genre"], bpm=plan["bpm"], key=plan["key"], name=name, vibe=" ".join(plan["vibe"]) or None,
                open=False, balance=bool(balance) and not will_open, arrange=True)
    project = r["project"]
    mark("new_song")
    if r.get("warning"):
        warnings += list(r["warning"]) if isinstance(r["warning"], list) else [str(r["warning"])]
    built = {"project": project, "sounds": [_short_sound(c) for c in r.get("channels") or []]}
    # ---- one batch: a beat per detected section with its loop (+ density), then the form re-laid
    ops, dens_notes = [], []
    seed0 = int(hashlib.sha1(meas.get("sha1", "").encode()).hexdigest()[:6], 16) % 90000 + 1000
    comp = H["TOOLS"]["flp_compose"][0]
    if copy == "feel":
        for i, s in enumerate(plan["sections"]):
            if not s.get("progression") or s.get("same_as"):
                continue
            op = {"tool": "flp_compose", "what": "beat", "section": s["section"], "progression": s["progression"],
                  "genre": plan["genre"], "key": plan["key"], "seed": seed0 + i}
            if s.get("loop_bars") and s["loop_bars"] > 8:
                op["bars"] = min(16, int(s["loop_bars"]))
            ops.append(op)
            d = s.get("density")
            if d:
                try:
                    dry = comp(**{k: v for k, v in op.items() if k != "tool"}, project=project, dry_run=True)
                    have = _drum_hits_per_bar(dry)
                    want = d["kick"] + d["snare"] + d["hat"]
                    if have and want:
                        ratio = want / have
                        word = ("busier" if ratio >= float(data()["busier_ratio"]) else
                                "emptier" if ratio <= float(data()["emptier_ratio"]) else None)
                        if word:
                            ops.append({"tool": "flp_adjust", "words": word, "section": s["section"]})
                            dens_notes.append(f"{s['section']}: reference ~{want:g} drum hits/bar vs {have:.0f} "
                                              f"composed -> {word}")
                except Exception:
                    pass
    if ops:
        ops.append({"tool": "flp_arrange_song", "mode": "replace", "genre": plan["genre"], "seed": seed0})
        edit = H["TOOLS"]["flp_edit"][0]
        er = None
        try:
            er = edit(project=project, ops=ops)
        except Exception as e:
            warnings.append(f"the per-section batch failed ({str(e)[:140]}); trying the main section alone")
            main_ops = [o for o in ops if o["tool"] == "flp_compose"][:1]
            try:
                er = edit(project=project, ops=main_ops + [ops[-1]])
                ops = main_ops + [ops[-1]]
            except Exception as e2:
                warnings.append(f"kept flh_new_song's own starter beat (genre progression): {str(e2)[:120]}")
                ops = []
        if er:
            got = []
            for o, x in zip(ops, er.get("results") or []):
                if o["tool"] == "flp_compose":
                    v = (x.get("variations") or [{}])[0]
                    got.append(f"{o['section']}: {v.get('about') or x.get('summary') or 'written'}")
                    if x.get("verified") is False:
                        warnings.append(f"{o['section']}: didn't read back as planned ({str(x.get('verify_problems'))[:100]})")
                elif o["tool"] == "flp_arrange_song":
                    built["form"] = " | ".join(f"{a.get('section')}@{a.get('bar', 0):g}" for a in x.get("sections") or [])
            built["beats"] = got
            built["batch"] = f"{len(ops)} edits in one flp_edit (one write, no FL reload: the new song isn't open)"
    if not built.get("form"):
        st = r.get("starter") or {}
        built["form"] = st.get("arranged") if isinstance(st.get("arranged"), str) else r.get("sections")
        if not ops:
            built["beats"] = [f"{st.get('section')}: {st.get('beat')}"] if st else []
    if dens_notes:
        built["density"] = dens_notes
    mark("batch")
    try:                                                          # the song remembers where it came from
        _mod("flh_store").journal_update(project, set={"reference": {
            "file": os.path.basename(str(reference)), "sha1": meas.get("sha1"), "copy": copy,
            "bpm_read": plan.get("base_bpm"), "key": plan["key"],
            "loops": {s["section"]: s["progression"] for s in plan["sections"] if s.get("progression")}}})
    except Exception:
        pass
    renders = 0
    if open:
        if will_open:
            # PC-VERIFY: FL opens the new song once (no second FL, no reload after the batch: the batch wrote the file
            # before FL saw it) and flp_balance's background pass starts once and reports with the next tool result
            try:
                o = H["fl_open"](project)
                built["opened"] = True
                if isinstance(o, dict) and o.get("warning"):
                    warnings.append(str(o["warning"])[:160])
            except Exception as e:
                built["opened"] = False
                warnings.append(f"saved but not opened in FL ({str(e)[:100]}): fl_open(project)")
            mark("open")
            if balance and built.get("opened"):
                try:
                    b = H["TOOLS"]["flp_balance"][0](project=project, genre=plan["genre"], background=True)
                    built["levels"] = "being balanced in the background (~40 s, holding FL); the result comes with the next tool result"
                    built["levels_note"] = b.get("note") if isinstance(b, dict) else None
                except Exception as e:
                    warnings.append(f"level balance didn't start ({str(e)[:100]}): flp_balance(project)")
                mark("balance_start")
        else:
            built["opened"] = False
            notes.append("not opened: FL runs on Windows only (the file is ready)")
    if balance and not built.get("levels"):
        built["levels"] = r.get("levels") or "balance pending: runs in the background when fl_open opens this song"
    res["built"] = {k: v for k, v in built.items() if v is not None}
    res["copy"] = COPY_NOTE if copy == "feel" else "tempo and key only; every note is newly written"
    res["renders"] = renders
    if guessed:
        res["guessed"] = guessed
    if notes + list(r.get("notes") or []):
        res["notes"] = notes + list(r.get("notes") or [])[:4]
    if warnings:
        res = {"ok": False, "warning": warnings, **res}
    res["listen"] = ("a human should listen: fl_listen the Hook/main section against the reference (feel, not notes); "
                     "flp_adjust(words='darker' | 'more bounce') to steer")
    steps["total"] = round(time.time() - T0, 3)
    res["steps_s"] = steps
    return res


# ============================================================================= registration
def register(ns):
    global H
    H = ns
    S, P = ns["_schema"], ns["_p"]
    STR, BOOL = ns["_STR"], ns["_BOOL"]
    ns["TOOLS"]["fl_beat_from_reference"] = (
        fl_beat_from_reference,
        "'Make a beat like this song' in ONE call: listens to the reference once (tempo + key with confidence, the "
        "main chord loop per louder/quieter section, kicks/snares/hats per bar, sub weight and brightness; cached by "
        "file hash, so a second call is instant), then builds a NEW song: flh_new_song (genre guessed from tempo, "
        "drums and tone unless given; half-time choice e.g. 75 -> 150 for trap), one flp_edit batch composing each "
        "section's beat on the detected loop + the arrangement, then opens it and balances the levels once. "
        "copy='feel': feel only - new notes, never the reference's melody/bassline/drum pattern note-for-note (a "
        "'type beat'); copy='tempo': tempo + key only. dry_run=true: the measurements and the plan with seconds.",
        S(reference=STR("the reference audio (WAV/FLAC/Ogg/AIFF; MP3/M4A on Windows)"),
          name=STR("song name (default '<file> Type Beat')", True),
          genre=STR("genre to build (default: guessed, said)", True),
          sections=P("array", "the reference's sections [{name, start_s, end_s}] (default: found by loudness)", True,
                     items={"type": "object"}),
          copy=STR("feel (default: tempo, key, chord loop, density, tone) | tempo (tempo + key only)", True),
          balance=BOOL("balance the levels with one export after opening (default: only when flh_auto_renders is on)", True),
          open=BOOL("open the song in FL (default true)", True),
          dry_run=BOOL("measure + plan only, nothing built", True)))
    ns.setdefault("CROSS_PLATFORM", set()).add("fl_beat_from_reference")   # analysis + build anywhere; open is Windows
    try:
        ns["TOOL_GROUPS"]["fl_beat_from_reference"] = "compose"
    except Exception:
        pass
    try:
        ns["LONG_HELP"]["fl_beat_from_reference"] = (
            "Typical: fl_beat_from_reference(reference='C:/Music/ref.mp3') or with genre='lofi'. dry_run=true first "
            "shows what was heard and the plan (the analysis is cached, so the real call then only builds). The "
            "result: measured (tempo/key with confidence, chord loop per section, drum density, tone), plan "
            "(genre, bpm with the half/double-time reason, key, vibe, sections), built (project, form, beats, "
            "sounds), guessed, steps_s. Listen and steer with flp_adjust.")
    except Exception:
        pass
