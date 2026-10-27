"""flh_flip: 'flip a sample' the way a hip hop producer does (soul chops, boom bap, lofi, stutters).

    fl_flip_sample(sample, project=None, genre=None, bpm=None, key=None, chops=6, bars=None, pitch='key',
                   style=None, drums=True, bass=True, seed=None, name=None, sample_bpm=None, open=True, dry_run=False,
                   keep=None, levels=True)

Pipeline (one read of the audio, numpy only):
  1. analyze   STFT once (2048 / hop 256): per frame RMS, band-normalised spectral flux (the same onset strength as
               audio_detect), spectral flatness, a 12-bin chroma (and a low one for the root), 2-4 kHz presence.
               Tempo from the flux autocorrelation (audio_detect's ~100 BPM prior), beat phase from the flux at the
               beats, key from _peak_chroma + fl_music.detect_key, onsets by peak-picking the flux, each refined in
               the waveform to the start of its attack.
  2. good bits candidate chops start on an onset (preferably on the sample's 8th-note grid) and end on a later onset
               or a quiet point near the length the style wants - never inside a held note. Each is scored from
               tonal clarity (chroma entropy), loudness, noisiness (flatness), attack strength, grid fit, a clean
               end and vocal-ish presence; noisy / quiet ones are dropped. A greedy pick keeps N chops that don't
               overlap and differ from each other (chroma similarity penalty, near-duplicates refused).
  3. flip      the pitch is changed by resampling (speed = pitch, length follows: classic sampler / chipmunk soul);
               'key' moves to the target key (inside the style's range); no time-stretch: chop lengths are picked in
               the source so that, at the new speed, they fit the song's grid. A few letter -> chop assignments of the
               style's loop (data/flip/styles.json) are scored (chop quality, slot fit, variety, root movement, cuts)
               and the best is kept - deterministic per seed.
  4. write     chop WAVs (only the ones the loop uses, 3 ms fade-in, 10-30 ms fade-out) in <song folder>/chops/<name>,
               one sampler channel per chop ('Chop 1'...; dark palette colors, own mixer insert), a 'Flip' pattern
               with one note per trigger at the sampler root (C5 = the chop as cut), drums under it (flp_compose
               what='drums') and bass on the chops' roots when the song has a bass channel. project=None makes a new
               song first (flh_new_song, starter=false). On the project open in FL the whole write is one save +
               reload (the tool is registered through _guard_open_project).
  2026-10 'it doesn't sound like a sample' fixes: classify() (melodic / vocal / drums) picks the style unless style=
  is given (data/flip/styles.json 'classes'); tonal chops use cut='ring' (end on the next real hit or where the ring
  has decayed RING_DB down; never inside a ringing chord; 40 ms fade), phrase styles prefer phrase-length chops;
  'loop' = phrases A B A C with a half-phrase + repeat in the last bar; 'loop-intact' (keep='loop') = the recorded
  loop on the grid + a roll at the end; set_levels() writes the chop inserts' faders (file level) so the chops sit
  lead_over_drums_db over the drums measured from the drum files at their notes; chords are named on their own root,
  spelled in the key, and the bass follows each chord inside a chop (the sample's own low note under an inversion).
Honest: which bits are 'good' is a measured guess - a person must audition; clearing a sample for release is the
user's job.
"""
import hashlib
import json
import math
import os
import re
import sys
import time

H = None
HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = {}
WIN, HOP = 2048, 256
ROOT_KEY = 60                         # FL's sampler plays a sample's own pitch on C5 (note 60)
FADE_IN_S, FADE_OUT_S = 0.003, 0.02
MAX_ANALYZE_S = 600.0


def _mod(name):
    m = sys.modules.get(name)
    if m is None:
        import importlib
        m = importlib.import_module(name)
    return m


def data():
    """data/flip/styles.json with a user copy (<state>/flip/styles.json) on top: styles merge by name."""
    files = [os.path.join(HERE, "data", "flip", "styles.json")]
    try:
        files.append(os.path.join(os.path.dirname(H["STATE"]), "flip", "styles.json"))
    except Exception:
        pass
    sig = tuple((f, os.path.getmtime(f)) for f in files if os.path.isfile(f))
    if _DATA.get("sig") == sig and "d" in _DATA:
        return _DATA["d"]
    d = {"styles": {}, "genre_styles": {}, "palette": ["#3A2A2A"], "default_style": "boombap"}
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


# ============================================================================= analysis
def _mono(x):
    return x.mean(1) if x.ndim == 2 and x.shape[1] > 1 else (x[:, 0] if x.ndim == 2 else x)


def _env(m, fs, ms=1.0):
    """Short-time RMS envelope at `ms` resolution -> (env, step samples)."""
    import numpy as np
    st = max(1, int(fs * ms / 1000.0))
    n = len(m) // st
    if n < 1:
        return np.zeros(1), st
    return np.sqrt(np.mean(m[:n * st].reshape(n, st) ** 2, axis=1)), st


def _frame_features(m, fs):
    """Per-frame features from one STFT pass in blocks (memory stays small for whole songs)."""
    import numpy as np
    n = (len(m) - WIN) // HOP + 1
    if n < 16:
        raise ValueError(f"the sample is too short to flip ({len(m) / fs:.2f} s): give at least ~1 s of audio")
    w = np.hanning(WIN).astype(np.float32)
    f = np.fft.rfftfreq(WIN, 1 / fs)
    nb = len(f)
    fl_band = (f >= 100) & (f < 8000)
    pres = (f >= 2000) & (f < 4000); mus = (f >= 200) & (f < 8000)
    edges = [0, 8, 24, 64, 160, 400, nb]
    out = {k: np.zeros(n, np.float32) for k in ("rms", "flat", "pres")}
    bflux = np.zeros((n, len(edges) - 1), np.float32)
    prev = None
    B = 1024
    for a in range(0, n, B):
        b = min(n, a + B)
        idx = (np.arange(a, b) * HOP)[:, None] + np.arange(WIN)[None, :]
        fr = m[idx].astype(np.float32)
        out["rms"][a:b] = np.sqrt(np.mean(fr ** 2, axis=1))
        S = np.abs(np.fft.rfft(fr * w, axis=1)).astype(np.float32)
        P = S ** 2 + 1e-12
        Pf = P[:, fl_band]
        out["flat"][a:b] = np.exp(np.mean(np.log(Pf), axis=1)) / np.mean(Pf, axis=1)
        out["pres"][a:b] = P[:, pres].sum(1) / P[:, mus].sum(1)
        Lg = np.log1p(S)
        if prev is not None:
            Lg2 = np.vstack([prev[None, :], Lg])
        else:
            Lg2 = np.vstack([Lg[:1], Lg])
        d = np.maximum(0, np.diff(Lg2, axis=0))
        for j, (lo, hi) in enumerate(zip(edges, edges[1:])):
            bflux[a:b, j] = d[:, lo:hi].sum(1)
        prev = Lg[-1]
    flux = np.zeros(n)
    for j in range(bflux.shape[1]):                               # each band normalised: kick and snare count alike
        sd = float(bflux[:, j].std())
        if sd > 0:
            flux += (bflux[:, j] - bflux[:, j].mean()) / sd
    out["flux"] = flux
    out["n"] = n
    out.update(_chroma_frames(m, fs))
    return out


CH_WIN, CH_HOP = 8192, 2048


def _chroma_frames(m, fs):
    """Chroma (55-3500 Hz) and a low chroma (45-300 Hz, for the root) on long frames: low notes need the resolution
    (a 2048 FFT can't tell E3 from F3). Each frame's noise floor (median) is taken off first."""
    import numpy as np
    win = CH_WIN if len(m) >= CH_WIN else 1 << max(8, int(math.log2(max(256, len(m)))))
    f = np.fft.rfftfreq(win, 1 / fs); nb = len(f)
    pcs = (np.round(12 * np.log2(np.maximum(f, 1.0) / 440.0) + 69).astype(int)) % 12
    band = (f >= 55) & (f < 3500); low = (f >= 45) & (f < 300)
    Cm = np.zeros((nb, 12), np.float32); Cm[np.flatnonzero(band), pcs[band]] = 1.0
    Lm = np.zeros((nb, 12), np.float32); Lm[np.flatnonzero(low), pcs[low]] = 1.0
    w = np.hanning(win).astype(np.float32)
    n = max(1, (len(m) - win) // CH_HOP + 1)
    ch = np.zeros((n, 12), np.float32); lo = np.zeros((n, 12), np.float32)
    for a in range(0, n, 256):
        b = min(n, a + 256)
        idx = (np.arange(a, b) * CH_HOP)[:, None] + np.arange(win)[None, :]
        fr = np.pad(m, (0, max(0, win - len(m))))[np.minimum(idx, max(len(m), win) - 1)].astype(np.float32)
        S = np.abs(np.fft.rfft(fr * w, axis=1)).astype(np.float32)
        S = np.maximum(0, S - np.median(S[:, band], axis=1, keepdims=True) * 2)
        ch[a:b] = S @ Cm; lo[a:b] = S @ Lm
    return {"chroma": ch, "low": lo, "ch_win": win}


def _tempo(flux, fps, lo=50.0, hi=200.0):
    """BPM from the onset-strength autocorrelation (audio_detect's method and ~100 BPM prior) -> (bpm, confidence)."""
    import numpy as np
    x = flux - flux.mean()
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, 2 * len(x))) ** 2)[:len(x)]
    if ac[0] <= 0:
        return None, 0.0
    prior = H["_tempo_prior"]
    ar = np.arange(len(ac))

    def score(bpm):
        lag = fps * 60 / bpm
        if lag + 1 >= len(ac):
            return -1e18
        v = np.interp(lag, ar, ac) + (0.5 * np.interp(2 * lag, ar, ac) if 2 * lag < len(ac) else 0)
        return v * prior(bpm)
    grid = np.arange(lo, hi + 0.5, 0.5)
    sc = np.array([score(b) for b in grid])
    if not np.isfinite(sc).any() or sc.max() <= -1e17:
        return None, 0.0
    b0 = float(grid[int(np.argmax(sc))])
    fine = np.arange(b0 - 1, b0 + 1.01, 0.05)
    b1 = float(fine[int(np.argmax([score(b) for b in fine]))])
    lag = fps * 60 / b1
    conf = float(np.clip(np.interp(lag, ar, ac) / ac[0], 0, 1))
    return round(b1, 2), conf


def _phase(flux, fps, bpm):
    """Time (s) of the first beat: the offset whose beats carry the most onset strength."""
    import numpy as np
    beat = fps * 60 / bpm
    ar = np.arange(len(flux))
    best, bp = -1e18, 0.0
    for p in np.arange(0, beat, 0.5):
        pos = np.arange(p, len(flux) - 1, beat)
        v = float(np.interp(pos, ar, flux).sum())
        if v > best:
            best, bp = v, p
    return (bp * HOP + WIN / 2) / fps / HOP


def _onsets(F, m, fs):
    """Onset cut points (s): flux peaks over a moving median, each moved back to where its attack starts."""
    import numpy as np
    fx = F["flux"]; n = len(fx); fps = fs / HOP
    k = np.ones(3) / 3; nov = np.convolve(fx, k, "same")
    wl = max(3, int(0.25 * fps))
    pad = np.pad(nov, (wl, wl), mode="edge")
    med = np.array([np.median(pad[i:i + 2 * wl + 1]) for i in range(0, n, 4)])
    med = np.repeat(med, 4)[:n]
    thr = med + 0.6 * max(float(nov.std()), 1e-9)
    gap = max(1, int(0.06 * fps))
    pk = [i for i in range(1, n - 1) if nov[i] > thr[i] and nov[i] >= nov[i - 1] and nov[i] >= nov[i + 1]]
    keep = []
    for i in sorted(pk, key=lambda i: -nov[i]):                    # strongest first, then a minimum gap
        if all(abs(i - j) >= gap for j in keep):
            keep.append(i)
    keep.sort()
    sm = np.ones(5) / 5                                            # 5 ms RMS on a 1 ms step: low notes don't wobble
    env_raw, st = _env(m, fs, 1.0)
    env_d, _ = _env(np.diff(m, prepend=m[:1]), fs, 1.0)            # the slope: attacks stand out over held notes
    envs = [np.sqrt(np.convolve(e ** 2, sm, "same")) for e in (env_raw, env_d)]
    out = []
    for i in keep:
        t = (i * HOP + WIN / 2) / fs
        a = max(0, int((t - 0.05) * fs / st)); b = min(len(envs[0]), int((t + 0.04) * fs / st) + 1)
        if b - a < 3:
            continue
        best = None
        for env in envs:                                           # the view where this attack rises the most
            seg = env[a:b]; p = int(np.argmax(seg)); pv = float(seg[p])
            q = int(np.argmin(seg[:p + 1])); base = float(seg[q])
            if best is None or pv / (base + 1e-9) > best[0]:
                best = (pv / (base + 1e-9), seg, p, pv, q, base)
        _, seg, p, pv, q, base = best
        thr = base + 0.3 * (pv - base)
        up = np.flatnonzero((seg[q:p] <= thr) & (seg[q + 1:p + 1] > thr))
        j = q + 1 + int(up[-1]) if len(up) else q                # the attack's last rise through 30 % of its height
        cut = (a + max(0, j - 2)) * st / fs
        out.append({"t": round(max(0.0, cut), 4), "frame": i, "z": float(nov[i] / max(float(nov.std()), 1e-9))})
    if len(F["rms"]) > 2 and (not out or out[0]["t"] > 0.05):      # sound from the very first sample: an onset at 0
        if float(F["rms"][:2].max()) > 0.1 * float(F["rms"].max()):
            out.insert(0, {"t": 0.0, "frame": 0, "z": 2.0})
    elif out and out[0]["t"] > 0 and len(m) > 1:                  # the file starts on the hit: cut at 0, not after it
        pre = float(np.abs(m[:max(1, int(min(out[0]["t"], 0.004) * fs))]).max())
        if pre > 0.05 * float(np.abs(m[:int(0.5 * fs)]).max()):
            out[0]["t"] = 0.0
    keep = []                                                      # a weak echo of a hit just after it is the same hit
    for o in out:
        if keep and o["t"] - keep[-1]["t"] < 0.09 and o["z"] < 0.35 * keep[-1]["z"]:
            continue
        keep.append(o)
    return keep


def _strong(ons):
    """The onsets that are real hits (not a ring's wobble): z over a share of the upper-quartile hit."""
    if not ons:
        return []
    zs = sorted((o["z"] for o in ons), reverse=True)
    # ... and over a share of the top hits: a sparse keys loop has ~10 real hits among ~150 wobbles, so its upper
    # quartile is a wobble, and a wobble (z ~1.8 next to hits at 8-12) used to end a ring chop mid-ring - a hole
    # before the next trigger (the 'gap at chop C')
    top = zs[min(len(zs) - 1, max(2, len(zs) // 20))]
    zmin = max(1.5, 0.3 * zs[min(len(zs) - 1, len(zs) // 4)], 0.2 * top)
    return [o for o in ons if o["z"] >= zmin or o["t"] <= 0.05]          # sound from the first sample is a hit


def _grid_fit(ons, bpm, phase=None):
    """How well the strong onsets sit on a beat grid (0..1), and the best phase (s) for it."""
    import numpy as np
    st = _strong(ons)
    if not st:
        return 0.0, phase or 0.0
    beat = 60.0 / bpm
    ts = np.array([o["t"] for o in st]); zs = np.array([min(o["z"], 12.0) for o in st])
    phs = [phase] if phase is not None else list(ts % beat)
    best = (-1.0, 0.0)
    for ph in phs:
        d = ((ts - ph) / beat) % 1.0
        d = np.minimum(d, 1 - d) * beat
        v = float((zs * np.exp(-(d / 0.035) ** 2)).sum() / zs.sum())
        if v > best[0]:
            best = (v, float(ph) % beat)
    return best


def _snap_loop_tempo(bpm, seconds, ons):
    """A loop is usually a whole number of bars: a tempo within 2.5 % of the measured one that makes the file exactly
    1/2/4/8/16/32 bars long wins when its grid fits the hits as well. -> (bpm, bars) or (None, None)."""
    best = None
    f0 = _grid_fit(ons, bpm)[0]
    for bars in (1, 2, 4, 8, 16, 32):
        for mult in (1.0, 2.0, 0.5):
            b = bars * 240.0 / seconds * mult
            if abs(b / bpm - 1) <= 0.025 and 50 <= b <= 200:
                f = _grid_fit(ons, b)[0]
                if f >= f0 - 0.03 and (best is None or abs(b / bpm - 1) < abs(best[0] / bpm - 1)):
                    best = (round(b, 2), round(b * seconds / 240.0))
    return best or (None, None)


def analyze(x, fs, sample_bpm=None):
    import numpy as np
    m = _mono(x)[:int(MAX_ANALYZE_S * fs)].astype(np.float64)
    F = _frame_features(m, fs)
    fps = fs / HOP
    if sample_bpm:
        bpm, conf, how = float(sample_bpm), 1.0, "given"
    else:
        bpm, conf = _tempo(F["flux"], fps)
        how = "measured"
        if bpm is None:
            bpm, conf, how = 90.0, 0.0, "not measurable (too short / no pulse): 90 assumed"
    ons = _onsets(F, m, fs)
    loop_bars = None
    if how == "measured":
        sb, nb = _snap_loop_tempo(bpm, len(m) / fs, ons)
        if sb:
            bpm, loop_bars = sb, nb
            how = f"measured, snapped to the file length ({nb:g} bars)"
            conf = max(conf, _grid_fit(ons, bpm)[0])
    ph = _phase(F["flux"], fps, bpm)
    fit_flux = _grid_fit(ons, bpm, ph)[0]
    fit_on, ph_on = _grid_fit(ons, bpm)
    if fit_on > fit_flux + 0.1:                                    # the hits disagree with the flux: trust the hits
        ph = ph_on
    try:
        ch = H["_peak_chroma"](m, fs)
        fl_music = _mod("fl_music")
        keys = fl_music.detect_key([{"key": pc, "len": float(ch[pc])} for pc in range(12)])
    except Exception:
        keys = []
    ref = float(np.percentile(20 * np.log10(F["rms"] + 1e-9), 95))
    return {"m": m, "fs": fs, "F": F, "bpm": bpm, "bpm_conf": round(conf, 2), "bpm_how": how, "phase": ph,
            "onsets": ons, "key": keys[0][0] if keys else None, "key_conf": keys[0][1] if keys else None,
            "ref_db": ref, "seconds": len(m) / fs, "loop_bars": loop_bars}


def classify(A):
    """What the sample is, from the whole file: 'melodic' (keys / piano / guitar / pad loop), 'vocal' (vocal / soul
    phrase) or 'drums' (drum loop). Measured: tonal clarity (chroma entropy per frame), noise-like share of the
    loudness (spectral flatness), vocal presence (2-4 kHz + steady pitch), hit density and how hits decay.
    -> {kind, why, numbers}."""
    import numpy as np
    F = A["F"]
    db = 20 * np.log10(F["rms"] + 1e-9)
    loud = db > A["ref_db"] - 30
    if not loud.any():
        loud = np.ones_like(db, bool)
    w = F["rms"][loud].astype(np.float64); w = w / (w.sum() + 1e-12)
    noisy = float((w * (F["flat"][loud] > 0.15)).sum())
    ch = F["chroma"].astype(np.float64)
    tot = ch.sum(1)
    ok = tot > np.percentile(tot, 40) if len(tot) > 4 else tot > 0
    if not ok.any():
        ok = tot >= 0
    p = ch[ok] / (tot[ok, None] + 1e-12)
    ent = -(p * np.log(p + 1e-12)).sum(1) / math.log(12)
    clar = float(np.clip((1 - np.median(ent)) / 0.55, 0, 1))
    nn = ch[ok] / (np.linalg.norm(ch[ok], axis=1, keepdims=True) + 1e-9)
    stab = float(np.median((nn[1:] * nn[:-1]).sum(1))) if len(nn) > 2 else 1.0
    pres = float((F["pres"][loud] * w).sum())
    vocal = float(np.clip((pres - 0.12) / 0.25, 0, 1) * np.clip((stab - 0.6) / 0.35, 0, 1))
    beat = 60.0 / A["bpm"]
    dens = len(_strong(A["onsets"])) / max(1.0, A["seconds"] / beat)       # hits per beat
    nums = {"tonal": round(clar, 2), "noisy": round(noisy, 2), "vocal": round(vocal, 2), "hits_per_beat": round(dens, 2)}
    if noisy > 0.45 or (clar < 0.12 and dens > 0.9):
        kind, why = "drums", f"mostly noise-like hits ({noisy:.0%} of the loudness), little pitch"
    elif vocal >= 0.35:
        kind, why = "vocal", f"vocal-like presence (2-4 kHz) on steady pitches ({vocal:.2f})"
    elif clar >= 0.1:
        kind = "melodic"
        sus = float(np.mean(np.diff(db[loud]) > -0.05)) if loud.sum() > 3 else 0.5
        what = "sustained: pad/strings-like" if dens < 0.15 and sus > 0.6 else "decaying hits: keys/piano/guitar-like"
        why = f"tonal (clarity {clar:.2f}), {noisy:.0%} noise-like, {dens:.2f} hits/beat ({what})"
    else:
        kind, why = "drums", f"untuned (clarity {clar:.2f})"
    return {"kind": kind, "why": why, "numbers": nums}


# ============================================================================= good bits
def _frames_of(A, a, b):
    fs = A["fs"]
    i0 = max(0, int((a * fs - WIN / 2) / HOP + 0.5)); i1 = min(A["F"]["n"], int((b * fs - WIN / 2) / HOP + 0.5))
    return i0, max(i0 + 1, i1)


def _norm(v):
    import numpy as np
    s = float(np.linalg.norm(v))
    return v / s if s > 0 else v


FLATS = "C Db D Eb E F Gb G Ab A Bb B".split()
SHARPS = "C C# D D# E F F# G G# A A# B".split()
MIXED = "C C# D Eb E F F# G Ab A Bb B".split()           # no key known: the common spellings


def _pc(name):
    """'Db' / 'C#' / 'Ab' -> pitch class."""
    n = str(name).strip()
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[n[0].upper()]
    for ch in n[1:]:
        base += 1 if ch == "#" else (-1 if ch == "b" else 0)
    return base % 12


def _spelling(key):
    """Note names for a key: flats in F Bb Eb Ab Db Gb major (D G C F Bb Eb minor), sharps in the sharp keys."""
    if not key:
        return MIXED
    try:
        r, fam = _key_parts(key)
    except Exception:
        return MIXED
    maj = r if fam == "major" else (r + 3) % 12
    if maj in (5, 10, 3, 8, 1, 6) and not (maj == 6 and "#" in str(key)):
        return FLATS
    if maj == 0:
        return MIXED
    return SHARPS


def _chord_best(ch, bass=None):
    """(root pc, quality, bass pc or None) of the best chord for 12 weights (fl_music.name_chord's search), or None.
    The root is the chord's own root - an inversion's bass note never becomes the root."""
    try:
        C = _mod("fl_music").CHORDS
    except Exception:
        return None
    mx = max(ch) if len(ch) else 0
    if mx <= 0:
        return None
    w = [float(v) if v >= 0.3 * mx else 0.0 for v in ch]
    total = sum(w)
    best = None
    for r in range(12):
        for q, iv in C.items():
            pcs = [(r + i) % 12 for i in iv]
            inside = sum(w[p] for p in pcs) / total
            missing = sum(1 for p in pcs if w[p] == 0) / len(pcs)
            sc = inside - 0.5 * missing - 0.02 * len(iv) + (0.15 if bass == r else 0)   # the bass note is usually
            if best is None or sc > best[0]:                                          # the root
                best = (sc, r, q)
    if best[0] < 0.4:
        return None
    _, r, q = best
    b = bass if bass is not None and bass != r and bass in [(r + i) % 12 for i in C[q]] else None
    return r, q, b


def chord_name(ch, bass=None, semis=0, key=None):
    """Chord name after a shift of `semis`, spelled in `key`: e.g. 'Dbmaj7', 'Fm7/Ab'."""
    cb = _chord_best(list(ch), bass)
    if not cb:
        return None
    r, q, b = cb; sp = _spelling(key)
    return sp[(r + semis) % 12] + q + (("/" + sp[(b + semis) % 12]) if b is not None else "")


def _chord(ch):
    return chord_name(ch)


def _seg_chroma(A, a, b):
    import numpy as np
    F = A["F"]; fs = A["fs"]
    j0 = max(0, int((a * fs - F["ch_win"] / 2) / CH_HOP + 0.5)); j1 = int((b * fs - F["ch_win"] / 2) / CH_HOP + 0.5)
    j0 = min(j0, len(F["chroma"]) - 1); j1 = min(len(F["chroma"]), max(j0 + 1, j1))
    ch = F["chroma"][j0:j1].sum(0).astype(np.float64); lowc = F["low"][j0:j1].sum(0).astype(np.float64)
    bass = _lowest_pc(A, a, b) if lowc.sum() > 0.15 * ch.sum() else None
    return ch, bass


def _lowest_pc(A, a, b):
    """The lowest clear note (40-300 Hz) just after a hit: a long FFT (2.7 Hz bins), the lowest peak at >= 30 % of
    the band's strongest - the bass note an ear hears, not the loudest low pitch class."""
    import numpy as np
    fs = A["fs"]; m = A["m"]
    n = 16384 if fs <= 48000 else 32768
    i0 = int((a + 0.03) * fs); i1 = min(len(m), int(b * fs), i0 + n)
    if i1 - i0 < 2048:
        return None
    seg = m[i0:i1] * np.hanning(i1 - i0)
    S = np.abs(np.fft.rfft(seg, n)); f = np.fft.rfftfreq(n, 1 / fs)
    band = np.flatnonzero((f >= 40) & (f < 300))
    if not len(band):
        return None
    sb = S[band]; mx = float(sb.max())
    if mx <= 0:
        return None
    pk = [i for i in range(1, len(sb) - 1) if sb[i] >= 0.3 * mx and sb[i] >= sb[i - 1] and sb[i] >= sb[i + 1]]
    if not pk:
        return None
    fr = float(f[band[pk[0]]])
    return int(round(12 * math.log2(fr / 440.0) + 69)) % 12


def seg_chord(A, a, b, semis=0, key=None):
    """(bass-line pc, chord name, chord root pc) of the audio between a and b (s), after the shift. The bass line
    plays the chord's root - or, under an inversion the sample itself plays (its low note is a chord tone, e.g.
    Dbmaj7/C), that low note, so the added bass never rubs against the sample's own bass."""
    import numpy as np
    ch, bass = _seg_chroma(A, a, b)
    cb = _chord_best(list(ch), bass)
    r = cb[0] if cb else (bass if bass is not None else int(np.argmax(ch)))
    line = cb[2] if cb and cb[2] is not None else r
    return (line + semis) % 12, chord_name(ch, bass, semis, key), (r + semis) % 12


def _features(A, a, b, start_z, end_kind, grid_dev):
    import numpy as np
    F = A["F"]; i0, i1 = _frames_of(A, a, b)
    rms = F["rms"][i0:i1].astype(np.float64)
    if not len(rms):
        return None
    db = 20 * np.log10(rms + 1e-9)
    wts = rms / (rms.sum() + 1e-12)
    fs = A["fs"]
    j0 = max(0, int((a * fs - F["ch_win"] / 2) / CH_HOP + 0.5)); j1 = int((b * fs - F["ch_win"] / 2) / CH_HOP + 0.5)
    j0 = min(j0, len(F["chroma"]) - 1); j1 = min(len(F["chroma"]), max(j0 + 1, j1))
    ch = F["chroma"][j0:j1].sum(0).astype(np.float64)
    p = ch / (ch.sum() + 1e-12)
    ent = float(-(p * np.log(p + 1e-12)).sum() / math.log(12))
    clar = float(np.clip((1 - ent) / 0.55, 0, 1))
    flat = float((F["flat"][i0:i1] * wts).sum())
    noisy = float((wts * (F["flat"][i0:i1] > 0.15)).sum())        # loudness share of noise-like frames
    seg_db = 10 * math.log10(float(np.mean(rms ** 2)) + 1e-18)
    loud = float(np.clip((seg_db - A["ref_db"] + 24) / 24, 0, 1))
    sil = float(np.mean(db < A["ref_db"] - 40))
    att = float(np.clip(start_z / 4.0, 0, 1))
    grid = float(np.clip(1 - grid_dev / 0.06, 0, 1))
    endc = 1.0 if end_kind == "onset" else 0.8
    cf = F["chroma"][j0:j1]
    if len(cf) > 2:
        nn = cf / (np.linalg.norm(cf, axis=1, keepdims=True) + 1e-9)
        stab = float(np.mean((nn[1:] * nn[:-1]).sum(1)))
    else:
        stab = 1.0
    pres = float((F["pres"][i0:i1] * wts).sum())
    vocal = float(np.clip((pres - 0.12) / 0.25, 0, 1) * np.clip((stab - 0.6) / 0.35, 0, 1))
    lowc = F["low"][j0:j1].sum(0)
    bass = int(np.argmax(lowc)) if lowc.sum() > 0.15 * ch.sum() else None
    cb = _chord_best(list(ch), bass)                              # the chord's own root, not its lowest note
    root = cb[0] if cb else (bass if bass is not None else int(np.argmax(ch)))
    score = (0.30 * clar + 0.20 * loud + 0.15 * (1 - min(1.0, flat / 0.2)) + 0.12 * att + 0.08 * grid +
             0.07 * endc + 0.08 * vocal) * (1 - sil)
    return {"clarity": round(clar, 2), "flatness": round(flat, 3), "noisy": round(noisy, 2), "loud": round(loud, 2),
            "silence": round(sil, 2), "attack": round(att, 2), "grid": round(grid, 2), "vocal": round(vocal, 2), "end_by": end_kind,
            "root": root, "bass": bass, "chroma": _norm(ch), "_ch": ch, "score": round(float(score), 3)}


RING_DB = 38.0                         # a tonal hit has rung out this far below its peak (or at the noise floor)
TAIL_DB = 24.0                         # cutting a ring this far down is fine (a 40 ms fade takes the rest)


def candidates(A, target_s, tol_frac=0.12, cut="short", beat_grid=False):
    """Chops: onset-to-onset (or onset-to-quiet-point) segments near each target length (s). -> (list, rejected).
    cut='ring' (tonal material): a chop starts on a real hit and ends on the next hit at/after the target length -
    it may hold several hits (a phrase) - or where the last hit's ring has decayed RING_DB below its peak / to the
    noise floor; it is never cut while a chord still rings (less than TAIL_DB down). cut='short': the classic stab
    (ends on any onset or at the first point 15 dB down)."""
    if cut == "ring":
        return _candidates_ring(A, target_s, tol_frac, beat_grid)
    import numpy as np
    ons = A["onsets"]; ts = np.array([o["t"] for o in ons]) if ons else np.zeros(0)
    beat = 60.0 / A["bpm"]; ph = A["phase"]
    env, st = _env(A["m"], A["fs"], 10.0)
    edb = 20 * np.log10(env + 1e-9)
    out, rej = [], {"noisy": 0, "quiet": 0, "held_note": 0}      # noisy = noise-like or untuned (drums only)
    for i, o in enumerate(ons):
        s = o["t"]
        g8 = beat / 2
        dev = abs(((s - ph) / g8) - round((s - ph) / g8)) * g8
        for L in target_s:
            tgt = s + L; tol = max(0.04, tol_frac * L)
            if tgt > A["seconds"] + 0.01:
                continue
            near = np.flatnonzero((ts > s + 0.05) & (np.abs(ts - tgt) <= tol))
            if len(near):
                e = float(ts[near[np.argmin(np.abs(ts[near] - tgt))]]); kind = "onset"
            else:
                k_ = A["fs"] / st
                a = int((tgt - tol) * k_); b = min(len(edb), int((tgt + tol) * k_) + 1)
                s0 = int(s * k_)
                if b - a < 2 or s0 >= a:
                    continue
                j = a + int(np.argmin(edb[a:b]))
                if edb[j] > float(edb[s0:a].max()) - 15:               # still loud there: a held note would be cut
                    rej["held_note"] += 1
                    continue
                e = j * st / A["fs"]; kind = "quiet"
            if e - s < 0.06:
                continue
            ft = _features(A, s, e, o["z"], kind, dev)
            if ft is None:
                continue
            if ft["flatness"] > 0.2 or ft["noisy"] > 0.3 or ft["clarity"] < 0.12:
                rej["noisy"] += 1
                continue
            if ft["loud"] <= 0.05 or ft["silence"] > 0.5:
                rej["quiet"] += 1
                continue
            ft.update(start=round(s, 4), end=round(e, 4), len_s=round(e - s, 4), want_s=round(L, 4))
            out.append(ft)
    return out, rej


def _ring_env(A):
    import numpy as np
    if "_edb" not in A:
        env, st = _env(A["m"], A["fs"], 10.0)
        edb = 20 * np.log10(env + 1e-9)
        A["_edb"] = (edb, st, float(np.percentile(edb, 5)))
    return A["_edb"]


def ring_end(A, t, limit=None):
    """Where the hit at `t` has rung out: RING_DB below its peak, or 3 dB over the noise floor (s), capped at limit."""
    import numpy as np
    edb, st, floor = _ring_env(A)
    k = A["fs"] / st
    a = int(t * k); b = min(len(edb), int((limit if limit is not None else A["seconds"]) * k))
    if b - a < 2:
        return limit if limit is not None else A["seconds"]
    pk_i = a + int(np.argmax(edb[a:min(b, a + 30)]))
    thr = max(float(edb[pk_i]) - RING_DB, floor + 3.0)
    j = np.flatnonzero(edb[pk_i:b] <= thr)
    return (pk_i + int(j[0])) / k if len(j) else (b / k)


def _candidates_ring(A, target_s, tol_frac, beat_grid):
    import numpy as np
    strong = _strong(A["onsets"])
    ts = np.array([o["t"] for o in strong]) if strong else np.zeros(0)
    beat = 60.0 / A["bpm"]; ph = A["phase"]
    edb, st, floor = _ring_env(A)
    k_ = A["fs"] / st
    out, rej = [], {"noisy": 0, "quiet": 0, "held_note": 0}
    g = beat if beat_grid else beat / 2
    for o in strong:
        s = o["t"]
        dev = abs(((s - ph) / g) - round((s - ph) / g)) * g
        for L in target_s:
            tol = max(0.04, tol_frac * L)
            lo, hi = s + L - tol, s + L + tol
            if lo > A["seconds"] + 0.01:
                continue
            near = ts[(ts > s + 0.05) & (ts >= lo) & (ts <= hi)]
            if len(near):
                # the next hit at/after the target length first: a chop a little longer than its slot is cut at the
                # slot (ringing through), one a little shorter leaves a hole before the next trigger
                after = near[near >= s + L - 0.03]
                e = float(after.min()) if len(after) else float(near[np.argmin(np.abs(near - (s + L)))])
                kind = "onset"
            else:
                inside = ts[(ts >= s) & (ts < lo)]
                last = float(inside.max()) if len(inside) else s
                re_ = ring_end(A, last, limit=min(A["seconds"], hi))
                if lo - 0.5 * L <= re_ <= hi:
                    e = re_; kind = "decay"
                else:
                    e = min(s + L, A["seconds"])
                    a_ = int(last * k_); b_ = int(e * k_)
                    if b_ >= len(edb) - 1:                       # the file ends: the last ring goes as far as it can
                        kind = "end"
                    elif b_ - a_ < 2 or float(edb[min(len(edb) - 1, b_)]) > float(edb[a_:b_].max()) - TAIL_DB:
                        rej["held_note"] += 1                    # a chord still rings there: don't cut it
                        continue
                    else:
                        kind = "tail"
            if e - s < 0.1:
                continue
            ft = _features(A, s, e, o["z"], "onset" if kind in ("onset", "decay", "end") else "quiet", dev)
            if ft is None:
                continue
            if ft["flatness"] > 0.2 or ft["noisy"] > 0.3 or ft["clarity"] < 0.12:
                rej["noisy"] += 1
                continue
            if ft["loud"] <= 0.05 or ft["silence"] > 0.6:
                rej["quiet"] += 1
                continue
            ft.update(start=round(s, 4), end=round(e, 4), len_s=round(e - s, 4), want_s=round(L, 4), end_by=kind,
                      hits=int(((ts >= s) & (ts < e - 0.05)).sum()))
            out.append(ft)
    return out, rej


def chop_hits(A, c, semis=0, key=None):
    """The chords inside a chop: [(offset s, bass-line pc, chord name, root pc)] after the shift - one per real hit
    (the bass follows each, not one note per chop)."""
    ts = [o["t"] for o in _strong(A["onsets"]) if c["start"] - 0.005 <= o["t"] < c["end"] - 0.1]
    if not ts or ts[0] > c["start"] + 0.05:
        ts = [c["start"]] + ts
    out = []
    for i, t in enumerate(ts):
        e = ts[i + 1] if i + 1 < len(ts) else c["end"]
        if e - t < 0.08:
            continue
        line, nm, r = seg_chord(A, t, e, semis, key)
        out.append((round(t - c["start"], 4), line, nm, r))
    return out


def pick(cands, n):
    """Greedy: best score minus a similarity penalty; no time overlap; near-duplicates (same chord again) refused."""
    import numpy as np
    chosen = []
    pool = sorted(cands, key=lambda c: -c["score"])
    while pool and len(chosen) < n:
        best, bv = None, -1e9
        for c in pool:
            if any(c["start"] < k["end"] - 0.005 and k["start"] < c["end"] - 0.005 for k in chosen):
                continue
            sim = max((float(np.dot(c["chroma"], k["chroma"])) for k in chosen), default=0.0)
            if sim > 0.985:
                continue
            same_root = any(k["root"] == c["root"] for k in chosen)
            v = c["score"] - 0.3 * sim - (0.05 if same_root else 0)
            if v > bv:
                best, bv = c, v
        if best is None:
            break
        chosen.append(best)
        pool = [c for c in pool if c is not best]                 # by identity: list.remove compares the dicts
        #                                                           (numpy chroma arrays: 'truth value ... ambiguous')
    for c in chosen:                                              # chord names only for the picks (it's slow-ish)
        if "chord" not in c:
            c["chord"] = chord_name(list(c["_ch"]), c.get("bass"))
    return chosen


# ============================================================================= flip: pitch, style, loop
PC = "C C# D D# E F F# G G# A A# B".split()


def _key_parts(key):
    root, mode, iv, fam = _mod("flh_compose").key_info(key)
    return root, fam


KEY_MAJ = "C Db D Eb E F F# G Ab A Bb B".split()
KEY_MIN = "C C# D Eb E F F# G G# A Bb B".split()


def _key_name(root, fam):
    return f"{(KEY_MIN if fam == 'minor' else KEY_MAJ)[root % 12]} {fam}"


def decide_pitch(pitch, style, det_key, target_key):
    """-> (semitones, new key name or None, why)."""
    sp = style.get("pitch") or {}
    dflt = int(sp.get("default", 0)); lo, hi = (sp.get("range") or [dflt, dflt])[:2]
    p = pitch if pitch is not None else "key"
    if isinstance(p, str) and re.fullmatch(r"[+-]?\d+(\.\d+)?", p.strip()):
        p = float(p)
    if isinstance(p, (int, float)) and not isinstance(p, bool):
        st = int(round(float(p))); why = f"{st:+d} st as asked"
    elif str(p).lower() == "up":
        st = int(hi) if hi > 0 else 3; why = f"up {st} st"
    elif str(p).lower() == "down":
        st = int(lo) if lo < 0 else -2; why = f"down {abs(st)} st"
    elif str(p).lower() == "key":
        st, why = dflt, (f"the style's {dflt:+d} st" if dflt else "kept at its own pitch")
        if target_key and det_key:
            tr, tf = _key_parts(target_key); dr, df = _key_parts(det_key)
            tmaj = tr if tf == "major" else (tr + 3) % 12              # compare relative majors: A minor = C major
            dmaj = dr if df == "major" else (dr + 3) % 12
            k0 = (tmaj - dmaj) % 12
            opts = [k0 + 12 * j for j in (-2, -1, 0, 1)]
            inr = [k for k in opts if lo <= k <= hi]
            st = min(inr or [k for k in opts if -6 <= k <= 6], key=lambda k: (abs(k - dflt), abs(k)))
            why = f"{st:+d} st: {det_key} -> {target_key}"
            if not inr and (lo != hi or dflt):
                why += f" (outside the style's {lo:+d}..{hi:+d}: the key won)"
    else:
        raise ValueError("pitch: 'key' | 'up' | 'down' | semitones (e.g. 3 or -2)")
    st = max(-12, min(12, st))
    new_key = None
    if det_key:
        dr, df = _key_parts(det_key)
        new_key = _key_name(dr + st, df)
    return st, new_key, why


def _style(style, genre):
    D = data()
    sname = str(style or D.get("genre_styles", {}).get(str(genre or "").lower()) or D.get("default_style") or "boombap")
    sname = sname.lower().replace("_", "-").replace(" ", "-")
    alias = {"chipmunk": "soul", "kanye": "soul", "callanswer": "call-answer", "call": "call-answer", "boom-bap": "boombap",
             "lo-fi": "lofi", "slow": "lofi"}
    sname = alias.get(sname, sname)
    st = (D.get("styles") or {}).get(sname)
    if not st:
        raise ValueError(f"no flip style '{style}' (have: {', '.join(sorted(D.get('styles') or {}))})")
    return sname, st


def _events(st, bars):
    loop = list(st.get("loop") or [])
    if not loop:
        raise ValueError("the style has no loop events")
    if bars <= 1:
        return [e for e in loop if float(e["at"]) < 4]
    if bars == 2:
        return loop
    turn = st.get("turn") or loop
    blocks = max(2, int(bars) // 2)                 # 2-bar blocks: the loop, again ..., the turn last (8 bars used
    out = []                                        # to stop after 4 bars and leave 4 bars of silence)
    for b in range(blocks):
        out += [dict(e, at=float(e["at"]) + 8 * b) for e in (turn if b == blocks - 1 else loop)]
    return out


def _letters(evs):
    seen = []
    for e in evs:
        b = str(e["chop"]).rstrip("'")
        if b not in seen:
            seen.append(b)
    return seen


def arrange(chops, st, bars, song_bpm, ratio, seed, tries=8):
    """Score `tries` letter -> chop assignments of the style loop; -> best plan (deterministic per seed)."""
    import numpy as np
    evs = sorted(_events(st, bars), key=lambda e: float(e["at"]))
    loop_beats = 4.0 * bars
    letters = _letters(evs)
    if not chops:
        raise ValueError("no usable chops were found in the sample (all noisy, quiet or cut inside a held note)")
    nat = [c["len_s"] / ratio * song_bpm / 60.0 for c in chops]      # chop length in song beats after the speed change
    scores = np.array([c["score"] for c in chops])
    plans = []
    for k in range(tries):
        rng = np.random.default_rng(int(seed) * 7919 + k)
        order = list(range(len(chops)))
        if k:                                                    # weighted shuffle: better chops more often first
            w = np.maximum(scores, 1e-3) ** 2
            order = list(rng.choice(len(chops), size=len(chops), replace=False, p=w / w.sum()))
        assign = {}
        for i, L in enumerate(letters):
            assign[L] = order[i % len(order)]
        used = set(assign.values())

        def variant(L):
            a = assign[L]
            rest = [j for j in range(len(chops)) if j not in used]
            if not rest:
                return a
            j = max(rest, key=lambda j: float(np.dot(chops[a]["chroma"], chops[j]["chroma"])))
            return j
        prime = {L: variant(L) for L in letters if any(str(e["chop"]) == L + "'" for e in evs)}
        trig = []
        for e in evs:
            lab = str(e["chop"]); base = lab.rstrip("'")
            ci = prime.get(base, assign[base]) if lab.endswith("'") else assign[base]
            at = float(e["at"])
            if at >= loop_beats:
                continue
            if e.get("stutter"):
                div = float(e.get("div", 0.25))
                for h in range(int(e["stutter"])):
                    if at + h * div < loop_beats - 1e-6:
                        trig.append({"at": at + h * div, "chop": ci, "div": div, "label": lab,
                                     "vel": max(60, int(e.get("vel", 100)) - 6 * h)})
            else:
                trig.append({"at": at, "chop": ci, "div": None, "label": lab, "vel": int(e.get("vel", 100))})
        trig.sort(key=lambda t: t["at"])
        for i, t in enumerate(trig):
            nxt = trig[i + 1]["at"] if i + 1 < len(trig) else loop_beats
            t["slot"] = round(nxt - t["at"], 6)
        main = [t for t in trig if t["div"] is None]
        if not main:
            continue
        fit = float(np.mean([min(nat[t["chop"]], t["slot"]) / t["slot"] for t in main]))
        cut = float(np.mean([max(0.0, nat[t["chop"]] - t["slot"]) / nat[t["chop"]] for t in main]))
        q = float(np.mean([chops[t["chop"]]["score"] for t in trig]))
        dist = sorted({t["chop"] for t in main})
        if len(dist) > 1:
            sims = [float(np.dot(chops[a]["chroma"], chops[b]["chroma"])) for i_, a in enumerate(dist) for b in dist[i_ + 1:]]
            var = float(np.clip((1 - np.mean(sims)) / 0.3, 0, 1)) * min(1.0, len(dist) / max(1, len(letters)))
        else:
            var = 0.0
        good = {0, 3, 4, 5, 7, 8, 9}
        mv = [(chops[b["chop"]]["root"] - chops[a["chop"]]["root"]) % 12 for a, b in zip(main, main[1:]) if a["chop"] != b["chop"]]
        flow = float(np.mean([m_ in good for m_ in mv])) if mv else 0.5
        total = 0.35 * q + 0.25 * fit + 0.15 * var + 0.10 * flow - 0.15 * cut
        plans.append({"try": k, "trig": trig, "assign": assign, "prime": prime, "score": round(total, 3),
                      "parts": {"quality": round(q, 2), "fit": round(fit, 2), "variety": round(var, 2),
                                "flow": round(flow, 2), "cut": round(cut, 2)}})
    if not plans:
        raise ValueError("the style's loop has no main hits")
    best = max(plans, key=lambda p: (p["score"], -p["try"]))
    best["tried"] = len(plans)
    best["loop_beats"] = loop_beats
    return best


def choose_style(style, genre, kind, intact, genre_given=True):
    """-> (style name, style, why). style= wins; else the genre's style when the sample's kind allows it, else the
    kind's default (a melodic loop that is already musical over its whole length keeps its loop intact)."""
    if style:
        sname, st = _style(style, genre)
        return sname, st, f"style='{sname}' as asked"
    D = data()
    gname, gst = _style(None, genre)
    cl = (D.get("classes") or {}).get(kind) or {}
    allowed = cl.get("allowed")
    if not allowed or (genre_given and gname in allowed
                       and not (kind == "melodic" and intact[0] and gname == cl.get("default"))):
        return gname, gst, f"the genre's style; a {kind} sample suits it"
    if cl.get("intact") and intact[0]:
        sname, st = _style(cl["intact"], genre)
        return sname, st, (f"a {kind} loop that is already musical ({intact[1]}): kept intact with a chopped "
                           f"variation at the phrase end (the genre's '{gname}' suits other material)")
    sname, st = _style(cl.get("default") or gname, genre)
    return sname, st, (f"a {kind} sample: '{sname}'" + (f" ({intact[1]})" if intact[1] else "") +
                       f" - the genre's '{gname}' suits other material")


def loop_grid(A):
    """(first downbeat s, source beat s, whole bars in the file after it)."""
    beat = 60.0 / A["bpm"]
    st = _strong(A["onsets"])
    t0 = A["phase"] % beat
    if st:                                       # loops start on the 1: the first real hit near the grid
        first = st[0]["t"]
        k = round((first - t0) / beat)
        if abs(first - (t0 + k * beat)) < 0.08:
            t0 = first
    nb = int((A["seconds"] - t0 + 0.05 * beat) / (4 * beat))
    return t0, beat, nb


def intact_quality(A):
    """Is the loop musical over its whole length? Each bar scored like a chop. -> (ok, why, bar scores)."""
    t0, beat, nb = loop_grid(A)
    if nb < 1:
        return False, "shorter than a bar", []
    if not (A["bpm_how"] == "given" or "snapped" in A["bpm_how"] or A["bpm_conf"] >= 0.35):
        return False, "its tempo is a weak guess (give sample_bpm=)", []
    sc, bad = [], 0
    for b in range(min(nb, 32)):
        a = t0 + b * 4 * beat
        ft = _features(A, a, a + 4 * beat, 4.0, "onset", 0.0)
        if ft is None:
            continue
        sc.append(ft["score"])
        if ft["flatness"] > 0.2 or ft["noisy"] > 0.3 or ft["silence"] > 0.5:
            bad += 1
    if not sc:
        return False, "no bars measured", []
    mean, low = sum(sc) / len(sc), min(sc)
    ok = bad == 0 and mean >= 0.45 and low >= 0.3
    why = f"{len(sc)} bars, quality {mean:.2f} (lowest {low:.2f})" + (f", {bad} noisy/silent" if bad else "")
    return ok, why, [round(v, 2) for v in sc]


def _snap_to_hit(A, t, win=0.04):
    st = [o["t"] for o in _strong(A["onsets"])]
    near = [x for x in st if abs(x - t) <= win]
    return min(near, key=lambda x: abs(x - t)) if near else t


def arrange_intact(A, st, bars, song_bpm, ratio, notes):
    """The loop as recorded, bar for bar on the song's grid, and the style's variation at the end of the loop
    (a hit then its roll). -> (chops, plan)."""
    t0, beat, nb = loop_grid(A)
    sps = 60.0 / song_bpm * ratio                       # source seconds per song beat
    q = beat / sps
    drift = abs(q - round(q)) > 0.02 or round(q) < 1
    src_beats = (A["seconds"] - t0) / sps
    src_loop = math.floor(src_beats / 4 + 0.05) * 4 if src_beats >= 4 else max(1, math.floor(src_beats))
    loop_beats = 4.0 * bars
    v = st.get("variation") or {}
    vb = min(float(v.get("beats", 2)), loop_beats / 2)
    hit_b = min(float(v.get("hit_beats", 1)), vb)
    div = float(v.get("div", 0.5)); nst = int(v.get("stutter", 2))
    end_intact = loop_beats - vb
    if drift:
        notes.append(f"the song's tempo ({song_bpm:g}) isn't the sample's ({A['bpm']:g} x the pitch): the loop is "
                     "re-triggered every bar so it stays on the grid (it may still drift inside a bar)")
    chops, idx, trig = [], {}, []

    def chop_of(src_a, src_b, label):
        a = _snap_to_hit(A, t0 + src_a * sps)
        b = min(A["seconds"], t0 + src_b * sps)
        b2 = _snap_to_hit(A, b)
        b = b2 if b2 <= b else b
        key = (round(a, 3), round(b, 3))
        if key not in idx:
            ft = _features(A, a, b, 4.0, "onset", 0.0) or {"score": 0.5, "root": 0, "chroma": None, "_ch": [0] * 12,
                                                           "clarity": 0, "loud": 0, "attack": 0, "vocal": 0, "grid": 1}
            ft.update(start=round(a, 4), end=round(b, 4), len_s=round(b - a, 4), end_by="grid", label=label)
            ft["chord"] = chord_name(list(ft["_ch"]), ft.get("bass"))
            idx[key] = len(chops); chops.append(ft)
        return idx[key]
    b = 0.0; n = 0
    while b < end_intact - 1e-6:
        pos = b % src_loop
        seg = min(end_intact - b, src_loop - pos)
        if drift:
            seg = min(seg, 4.0 - (b % 4.0))
        n += 1
        ci = chop_of(pos, pos + seg, "loop")
        trig.append({"at": b, "chop": ci, "div": None, "label": "loop", "vel": 100})
        b += seg
    vpos = end_intact % src_loop                       # the variation: the hit at that point of the loop, then a roll
    hit_t = t0 + vpos * sps
    st_on = [o["t"] for o in _strong(A["onsets"]) if hit_t - sps <= o["t"] <= hit_t + sps]
    hpos = ((min(st_on, key=lambda x: abs(x - hit_t)) - t0) / sps) if st_on else 0.0
    hci = chop_of(hpos, hpos + hit_b, "var")
    trig.append({"at": end_intact, "chop": hci, "div": None, "label": "var", "vel": 100})
    for h in range(nst):
        at = end_intact + hit_b + h * div
        if at < loop_beats - 1e-6:
            trig.append({"at": at, "chop": hci, "div": div, "label": "var", "vel": max(60, 96 - 6 * h)})
    trig.sort(key=lambda t: t["at"])
    for i, t in enumerate(trig):
        nxt = trig[i + 1]["at"] if i + 1 < len(trig) else loop_beats
        t["slot"] = round(nxt - t["at"], 6)
    assign = {}
    for t in trig:
        if t["div"] is None:
            assign.setdefault("L" if t["label"] == "loop" else "V", t["chop"])
    li = 1
    for t in trig:
        if t["label"] == "loop" and t["div"] is None and t["chop"] not in assign.values():
            li += 1; assign[f"L{li}"] = t["chop"]
    q_ = sum(c["score"] for c in chops) / len(chops)
    plan = {"try": 0, "trig": trig, "assign": assign, "prime": {}, "score": round(q_, 3), "tried": 1,
            "parts": {"quality": round(q_, 2), "kept_beats": round(end_intact, 2), "varied_beats": round(vb, 2)},
            "loop_beats": loop_beats, "src_loop_beats": src_loop}
    return chops, plan


# ============================================================================= audio out
def _fade(y, fs, fin=FADE_IN_S, fout=FADE_OUT_S):
    import numpy as np
    n = len(y)
    a = min(n // 4, max(2, int(fin * fs))); b = min(n // 3, max(2, int(fout * fs)))
    if a > 1:
        y[:a] *= (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, a)))[:, None]
    if b > 1:
        y[n - b:] *= (0.5 + 0.5 * np.cos(np.linspace(0, np.pi, b)))[:, None]
    return y


RING_FADE_S = 0.04                     # a tonal tail's fade at the very end (20-60 ms: no click, the ring kept)


def render_chop(x, fs, start, end, semis, max_s=None, fade_out=None):
    """The chop's audio: cut, re-pitched by resampling (speed = pitch, length / ratio), trimmed, faded (fade_out s at
    the very end; default 10-20 ms for a stab)."""
    import numpy as np
    AE = _mod("fl_audio_edit")
    r = 2.0 ** (semis / 12.0)
    pad = 256
    a = int(round(start * fs)); b = int(round(end * fs))
    a0, b0 = max(0, a - pad), min(len(x), b + pad)
    seg = np.asarray(x[a0:b0], dtype=np.float64)
    if seg.ndim == 1:
        seg = seg[:, None]
    if semis:
        y = AE.resample_to(seg, max(1, int(round(len(seg) / r))))
        lead = int(round((a - a0) / r)); body = int(round((b - a) / r))
    else:
        y = seg.copy(); lead = a - a0; body = b - a
    y = y[lead:lead + body].copy()
    if max_s is not None:
        y = y[:max(1, int(max_s * fs))]
    fo = fade_out if fade_out is not None else FADE_OUT_S
    return _fade(y, fs, fout=min(fo, max(0.01, len(y) / fs * 0.15)))


GAP_S = 0.03                           # a hole longer than this between a chop and the next trigger is audible


def ring_through(A, chops, plan, cut, ratio, song_bpm):
    """What each trigger of a chop plays: up to the next trigger (its slot) or as far as the chop has sound - never
    a hole the style didn't ask for. A tonal (cut='ring') chop that ended where its ring decayed keeps ringing in the
    source up to the next real hit ('_src_end'). A chop whose triggers have different slots used to be trimmed to
    the shortest one for all of them (a hole after the longer ones - the 'gap at chop C'): each other length now gets
    its own copy of the chop (plan['variant_of'], channel 'Chop 3b'), so no file rings into the next hit either."""
    st_on = [o["t"] for o in _strong(A["onsets"])] if A else []
    spb = 60.0 / song_bpm                                          # song seconds per beat
    tol = GAP_S / spb
    var = plan.setdefault("variant_of", {})
    for i in range(len(chops)):
        c = chops[i]
        c["_src_end"] = c["end"]
        if cut == "ring" and c.get("end_by") in ("decay", "tail") and A:
            nxt = [t for t in st_on if t > c["end"] + 0.01]
            c["_src_end"] = round(min(A["seconds"], (nxt[0] - 0.005) if nxt else A["seconds"]), 4)
        avail = (c["_src_end"] - c["start"]) / ratio / spb             # beats it can sound at the new speed
        mains = [t for t in plan["trig"] if t["chop"] == i and t["div"] is None]
        if not mains:
            c["_plays"] = avail
            continue
        lengths = {}                                               # needed length -> its triggers (first one first)
        for t in mains:
            need = min(avail, t["slot"])
            k = next((L for L in lengths if abs(L - need) <= tol), None)
            if k is None:
                lengths[need] = [t]
            else:
                lengths[k].append(t)
        order = list(lengths)
        c["_plays"] = order[0]
        for L in order[1:]:
            v = dict(c, _plays=L)
            chops.append(v); vi = len(chops) - 1; var[vi] = i
            for t in lengths[L]:
                t["chop"] = vi
    holes = []                                                     # what is still silent: the source has a new hit
    for t in plan["trig"]:                                         # sooner than the song's grid (tempo mismatch,
        if t["div"] is None:                                       # no time-stretch) or a stab style's own gaps
            h = (t["slot"] - chops[t["chop"]]["_plays"]) * spb
            if h > GAP_S:
                holes.append((round(t["at"], 3), int(round(h * 1000))))
    plan["holes"] = holes
    return chops


def numbering(plan):
    """[(chop index, stutter div or None)] in channel order (main chops first) and {chop index: Chop number}. A
    variant (the same chop cut to another length, plan['variant_of']) shares its chop's number ('Chop 3b')."""
    var = plan.get("variant_of") or {}
    keys = []
    for t in plan["trig"]:
        k = (t["chop"], t["div"])
        if k not in keys:
            keys.append(k)
    keys.sort(key=lambda k: (k[1] is not None, var.get(k[0], k[0]), k[0] in var, k[0], k[1] or 0))
    num, n = {}, 0
    for ci, div in keys:
        b = var.get(ci, ci)
        if b not in num:
            n += 1; num[b] = n
        num[ci] = num[b]
    return keys, num


def _chan_name(k, keys, num):
    ci, div = k
    nm = f"Chop {num[ci]}"
    if div is None:                                 # the same chop cut to another length: 'Chop 3b'
        same = [kk for kk in keys if kk[1] is None and num.get(kk[0]) == num[ci]]
        if len(same) > 1 and same.index(k) > 0:
            nm += "bcdefghijklmnop"[min(14, same.index(k) - 1)]
    if div is not None:
        nm += " stutter"
        if sum(1 for kk in keys if kk[0] == ci and kk[1] is not None) > 1:
            nm += f" 1-{int(round(4 / div))}"
    return nm


# ============================================================================= project writing
def _palette():
    D = data(); pal = list(D.get("palette") or ["#3A2A2A"])
    try:
        up = (_mod("flh_store").profile_get() or {}).get("palette") or {}
        v = up.get("chop") or up.get("flip") or up.get("sample")
        if isinstance(v, str):
            pal = [v]
        elif isinstance(v, list) and v:
            pal = [str(c) for c in v]
    except Exception:
        pass
    return pal


def _safe(name):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name)).strip().rstrip(".") or "sample"
    return s[:48]


def _chops_dir(folder, stem):
    base = os.path.join(folder, "chops", _safe(stem))
    for n in range(1, 200):
        p = base if n == 1 else f"{base} {n}"
        if not os.path.exists(p):
            return p
    raise RuntimeError("too many chop folders for this sample")


def _main_section(project):
    try:
        secs = (_mod("flh_store").journal_get(project) or {}).get("sections") or []
    except Exception:
        secs = []
    secs = sorted(secs, key=lambda s: s.get("bar", 0))
    for want in ("verse", "hook", "chorus", "main", "drop", "a"):
        for s in secs:
            if want in str(s.get("name", "")).lower():
                return s
    return next((s for s in secs if str(s.get("name", "")).lower() not in ("intro", "outro")), secs[0] if secs else None)


def _raw_tool(name):
    t = H["TOOLS"].get(name)
    f = t[0] if t else H.get(name)
    return getattr(f, "__wrapped__", f)


def write_flip(project, output, plan, chops, files, sample, genre, seed, drums, bass, section, song_bpm, notes, steps,
               t_mark, ratio=1.0, levels=True):
    """Channels + pattern (+ drums, bass) into `output`. files: {chop key: wav path}. -> written dict."""
    N = _mod("flh_newsong"); CM = _mod("flh_compose"); ST = _mod("flh_store")
    H["writable"](output)
    pal = _palette()
    d = H["_load"](project)
    keys, num = numbering(plan)
    chan, names = {}, []
    for i, k in enumerate(keys):
        ci, div = k
        nm = _chan_name(k, keys, num)
        ev = H["_events"](d)
        types = N._chan_types(d, ev)
        src = next((c for c, t in sorted(types.items()) if t == 0), None)
        free = N._free_insert(d, ev)
        col = pal[(num[ci] - 1) % len(pal)]
        d, ch = N._append_channel(d, N._sampler_block(d, ev, files[k], nm, color=col, insert=free or 0, src=src))
        chan[k] = ch
        names.append({"channel": ch, "name": nm, "insert": free or 0, "color": col, "file": files[k]})
    # PC-VERIFY: each 'Chop N' channel loads its WAV from <song>/chops/<sample>/ and plays it as cut on C5 (note 60)
    # PC-VERIFY: a chop channel copied from an existing sampler (envelope/filter came along) still plays the whole chop
    H["_save"](d, output)
    t_mark("channels")
    mix = [{"track": c["insert"], "name": c["name"], "color": c["color"]} for c in names if c["insert"]]
    if mix:
        try:
            H["flp_mixer"](output, output, mix)
        except Exception as e:
            notes.append(f"chop mixer inserts not named ({str(e)[:60]})")
    jch = {str(c["channel"]): {"role": "chop", "sample": os.path.abspath(sample)} for c in names}
    try:
        ST.journal_update(output, set={"channels": jch})
    except Exception as e:
        notes.append(f"journal not updated ({str(e)[:60]})")
    # ---- where the loop goes: the drums decide (they land in the section, or after the song)
    v = CM.project_view(output)
    loop_bars = plan["loop_beats"] / 4.0
    if section:
        start_bar, span = float(section["bar"]), float(section.get("end_bar", section["bar"] + 8)) - float(section["bar"])
    else:
        start_bar, span = (float(v["end_bar"]) if v["clips"] else 1.0), max(loop_bars, 8.0)
    span = max(loop_bars, math.floor(span / loop_bars) * loop_bars) if span >= loop_bars else loop_bars
    drum_res = None
    if drums:
        try:
            r = _raw_tool("flp_compose")(output, "drums", genre=genre, bars=int(span),
                                          section=(section or {}).get("name"), seed=seed)
            lst = ((r.get("variations") or [{}])[0].get("listen") or {})
            if lst.get("start_bar"):
                start_bar = float(lst["start_bar"])
            drum_res = {"bars": r.get("bars"), "channels": r.get("drum_channels")}
        except Exception as e:
            notes.append(f"no drums ({str(e)[:110]})")
        t_mark("drums")
    # ---- the Flip pattern: one note per trigger at the sampler root, its length = what the chop plays
    d = H["_load"](output); ev = H["_events"](d)
    pats = {p["name"].strip().lower() for p in H["_patterns"](d, ev).values()}
    pname = "Flip"
    for n in range(2, 100):
        if pname.lower() not in pats:
            break
        pname = f"Flip {n}"
    d, pid = N._new_pattern(d, pname, data().get("pattern_color") or "#3A2A44")
    H["_save"](d, output)
    rows = []
    for t in plan["trig"]:
        k = (t["chop"], t["div"])
        ln = t["div"] if t["div"] is not None else min(t["slot"], chops[t["chop"]]["_plays"])
        rows.append([chan[k], round(t["at"], 4), round(max(0.0625, ln), 4), ROOT_KEY, t["vel"]])
    bass_info = None
    if bass:
        try:
            jr = ST.journal_get(output) or {}
            bch = next((int(c) for c, x in sorted((jr.get("channels") or {}).items(), key=lambda kv: int(kv[0]))
                        if (x or {}).get("role") in ("bass", "808")), None)
            if bch is not None:
                off = _mod("flh_score").sounding_offsets(jr).get(bch, 0)
                main = [t for t in plan["trig"] if t["div"] is None]
                bpb = song_bpm / 60.0 / ratio                       # song beats per source second
                nb_ = 0
                for t in main:
                    c = chops[t["chop"]]
                    plays = min(t["slot"], c["_plays"])
                    hits = c.get("_hits") or [(0.0, (c["root"] + c["_semis"]) % 12, None, None)]
                    hb = [(round(h[0] * bpb * 4) / 4, h[1]) for h in hits]      # on the 16th grid
                    hb = [h for h in hb if h[0] < plays - 0.2]
                    for j, (ob, pc) in enumerate(hb):        # one bass note per chord inside the chop, on its root
                        if j and pc == hb[j - 1][1]:
                            continue
                        end = next((h[0] for h in hb[j + 1:] if h[1] != pc), plays)
                        note = (36 + pc if pc <= 4 else 24 + pc) + int(off or 0)
                        rows.append([bch, round(t["at"] + ob, 4), round(max(0.25, min(end - ob, 4.0) - 0.05), 4), note, 96])
                        nb_ += 1
                bass_info = {"channel": bch, "notes": nb_, "follows": "each chord inside the chops: its root (the "
                             "sample's own low note under an inversion like Dbmaj7/C)"}
        except Exception as e:
            notes.append(f"no bass ({str(e)[:80]})")
    reps = int(round(span / loop_bars))
    place = [[start_bar + i * loop_bars, loop_bars] for i in range(max(1, reps))]
    # PC-VERIFY: the Flip clips sit under the drum block in the playlist, and no chop overlaps the next one (each file
    # is trimmed to its slot: a long note never makes a chop ring into the next)
    w = CM.flp_compose_write(output, output, pid, notes=rows, place=place, length_bars=loop_bars)
    try:
        d = H["_load"](output)
        d, _ = N._style_tracks(d, [{"track": w["track"], "name": pname, "color": data().get("pattern_color") or "#3A2A44"}])
        H["_save"](d, output)
    except Exception:
        pass
    try:
        ST.journal_update(output, add={"notes": [f"flip of {os.path.basename(sample)}: pattern '{pname}', channels "
                                                 f"{', '.join(str(c['channel']) for c in names)}"]})
    except Exception:
        pass
    t_mark("pattern")
    lv = None
    if levels:
        try:
            lv = set_levels(output, sorted(set(chan.values())), start_bar, loop_bars, song_bpm, notes)
        except Exception as e:                                   # noqa: BLE001 - the flip is written; say so
            lv = {"skipped": f"levels not set ({type(e).__name__}: {str(e)[:80]})"}
        t_mark("levels")
    return {"levels": lv, "project": output, "channels": [{k_: c[k_] for k_ in ("channel", "name", "color")} for c in names],
            "pattern": pname, "placed": f"bars {start_bar:g}-{start_bar + reps * loop_bars:g} on playlist track "
                                         f"{w['track']} ({reps}x the {loop_bars:g}-bar loop)",
            "drums": drum_res, "bass": bass_info, "start_bar": start_bar}


# ============================================================================= levels (file level, FL closed)
def _load_audio(path, fs, cache):
    import numpy as np
    if path in cache:
        return cache[path]
    y = None
    try:
        p = os.path.expandvars(path)
        if "%" in p:
            try:
                p = p.replace("%FLStudioFactoryData%", os.path.dirname(H["fl_exe"]()))
            except Exception:
                pass
        if os.path.isfile(p):
            x, r, _ = H["_wav_load"](p)
            x = np.asarray(x, dtype=np.float64)
            if x.ndim == 1:
                x = x[:, None]
            if x.shape[1] == 1:
                x = np.repeat(x, 2, axis=1)
            x = x[:, :2]
            if r != fs:
                x = _mod("fl_audio_edit").resample_to(x, max(1, int(round(len(x) * fs / r))))
            y = x[:int(30 * fs)]
    except Exception:
        y = None
    cache[path] = y
    return y


def estimate_levels(output, chop_channels, start_bar, loop_bars, song_bpm, fs=44100):
    """LUFS of the drums, the bass and the chops as the song plays them over one loop, from the sample files at the
    pattern notes (velocity and the mixer faders applied; channel volume, plugins and FX not). -> dict."""
    import numpy as np
    N = _mod("flh_newsong"); ST = _mod("flh_store")
    d = H["_load"](output); ev = H["_events"](d); P = H["_ppq"](d)
    jr = ST.journal_get(output) or {}
    roles = {int(c): str((v or {}).get("role") or "").lower() for c, v in (jr.get("channels") or {}).items()
             if str(c).isdigit()}
    groups = {"drums": [c for c, r in roles.items() if r in N.DRUM_ROLES],
              "bass": [c for c, r in roles.items() if r in ("bass", "808", "sub")],
              "chops": list(chop_channels)}
    notes, _ = H["_song"](d, ev)
    a = (float(start_bar) - 1) * 4 * P; b = a + float(loop_bars) * 4 * P
    spp = 60.0 / song_bpm / P
    n_out = int((b - a) * spp * fs) + 2 * fs
    cache, out, ins = {}, {}, {}
    for grp, chs in groups.items():
        buf = np.zeros((n_out, 2)); used = 0; missing = []
        for c in chs:
            try:
                props = N._chan_props(d, ev, c)
                path = N._chan_sample_path(d, ev, c)
            except Exception:
                continue
            i_ = props.get("insert")
            ins[c] = i_
            y = _load_audio(path, fs, cache) if path else None
            if y is None:
                missing.append(c); continue
            g = 10 ** (H["_track_volume_db"](d, i_) / 20.0) if i_ else 1.0
            for pos, ln, ch, key, vel in notes:
                if ch != c or not (a <= pos < b):
                    continue
                k0 = int((pos - a) * spp * fs)
                z = y
                if grp == "bass" and key != ROOT_KEY:                # a sampler bass plays its sample at the note
                    ck = (path, key)
                    if ck not in cache:
                        r = 2.0 ** ((key - ROOT_KEY) / 12.0)
                        cache[ck] = _mod("fl_audio_edit").resample_to(y, max(1, int(len(y) / r)))
                    z = cache[ck]
                if grp != "drums":
                    z = z[:max(1, int(ln * spp * fs))]
                k1 = min(n_out, k0 + len(z))
                buf[k0:k1] += z[:k1 - k0] * g * (min(127, vel) / 100.0)
                used += 1
        lu = H["_lufs"](buf, fs) if used else None
        out[grp] = {"lufs": round(lu, 1) if lu is not None else None, "channels": chs, "notes": used,
                    "unreadable": missing}
    out["_inserts"] = ins
    out["_d"] = d
    return out


def set_levels(output, chop_channels, start_bar, loop_bars, song_bpm, notes, lead_db=None):
    """Chops ~lead_db over the drums (the sample is the lead), the bass under the chops: mixer insert faders written
    in the file (works with FL closed; the open project is saved/reloaded by the guard). -> levels dict."""
    N = _mod("flh_newsong")
    lead = float(lead_db if lead_db is not None else data().get("lead_over_drums_db", 4.0))
    L = estimate_levels(output, chop_channels, start_bar, loop_bars, song_bpm)
    d = L.pop("_d"); ins = L.pop("_inserts")
    cap = min(float(getattr(N, "FADER_MAX_DB", 5.6)), float(data().get("chop_fader_max_db", 3.0)))
    dr, ba, cp = L["drums"]["lufs"], L["bass"]["lufs"], L["chops"]["lufs"]
    res = {"how": "estimated from the sample files at the pattern's notes over one loop (no FL render; channel "
                  "volume, plugins and FX not counted): listen", "target": f"chops {lead:+g} dB over the drums"}
    if cp is None:
        res["skipped"] = "the chops couldn't be measured"
        return res
    chop_ins = sorted({ins.get(c) for c in chop_channels if ins.get(c)})
    if not chop_ins:
        res["skipped"] = "the chops play on the master (no free mixer insert): set their channel volume by ear"
        return res
    ref = dr
    if ref is None:
        try:
            tgt = float(N.BALANCE_MIX_LUFS) - 2.0
        except Exception:
            tgt = -16.0
        ref = tgt - lead
        res["drums"] = "not measured (no drum notes or unreadable drum files): the chops aim at " \
                       f"{tgt:g} LUFS instead"
    cur = {t: H["_track_volume_db"](d, t) for t in chop_ins}
    delta = (ref + lead) - cp
    new = {t: cur[t] + delta for t in chop_ins}
    over = max(0.0, max(new.values()) - cap)
    moves = []
    tuck = 0.0
    if over > 0:
        new = {t: v - over for t, v in new.items()}
        drum_ins = sorted({ins.get(c) for c in L["drums"]["channels"] if ins.get(c)})
        if drum_ins and dr is not None:
            tuck = over
            for t in drum_ins:
                moves.append({"track": t, "volume_db": round(max(-40.0, H["_track_volume_db"](d, t) - tuck), 2)})
        else:
            notes.append(f"the chops are {over:.1f} dB short of the target at the fader's top: turn the drums down")
    for t, v in new.items():
        moves.append({"track": t, "volume_db": round(max(-40.0, v), 2)})
    cp2 = cp + (delta - over); dr2 = (dr - tuck) if dr is not None else None
    bass_move = None
    if ba is not None:
        ba2 = ba - tuck
        want = cp2 - 4.0                              # the bass under the sample (it carries the low end, not the lead)
        b_ins = sorted({ins.get(c) for c in L["bass"]["channels"] if ins.get(c)})
        if ba2 > want + 0.5 and b_ins:
            for t in b_ins:
                moves.append({"track": t, "volume_db": round(max(-40.0, H["_track_volume_db"](d, t) - tuck - (ba2 - want)), 2)})
            bass_move = round(-(ba2 - want) - tuck, 1); ba2 = want
        elif tuck and b_ins:
            for t in b_ins:
                moves.append({"track": t, "volume_db": round(max(-40.0, H["_track_volume_db"](d, t) - tuck), 2)})
            bass_move = round(-tuck, 1)
        res["bass_lufs"] = round(ba2, 1)
    try:
        H["flp_mixer"](output, output, moves)
    except Exception as e:                           # noqa: BLE001 - e.g. the fader curve isn't known
        res["skipped"] = f"faders not written ({str(e)[:80]})"
        return res
    # PC-VERIFY: after the write the chops are clearly the loudest musical part (~3-6 dB over the drums) with FL's
    # own render; a copied sampler's channel volume (not counted here) may move this
    res.update({"chops_lufs": round(cp2, 1), "chop_fader_db": {str(t): round(max(-40.0, v), 1) for t, v in new.items()},
                "chop_change_db": round(delta - over, 1)})
    if dr2 is not None:
        res["drums_lufs"] = round(dr2, 1)
        res["chops_over_drums_db"] = round(cp2 - dr2, 1)
    if tuck:
        res["drums_tucked_db"] = round(-tuck, 1)
    if bass_move is not None:
        res["bass_change_db"] = bass_move
    unread = L["drums"]["unreadable"] + L["bass"]["unreadable"]
    if unread:
        res["unmeasured_channels"] = unread
    return res


# ============================================================================= the tool
def fl_flip_sample(sample, project=None, output=None, genre=None, bpm=None, key=None, chops=6, bars=None, pitch="key",
                   style=None, drums=True, bass=True, seed=None, name=None, sample_bpm=None, open=True, dry_run=False,
                   keep=None, levels=True):
    import numpy as np
    T0 = time.time(); steps = {}; notes = []
    clock = [T0]

    def mark(s):
        steps[s] = round(time.time() - clock[0], 3); clock[0] = time.time()
    if not sample or not os.path.isfile(sample):
        raise FileNotFoundError(f"sample not found: {sample}")
    if project and not os.path.isfile(project):
        raise FileNotFoundError(f"project not found: {project}")
    if bars is not None and int(bars) not in (1, 2, 4, 8):
        raise ValueError("bars: 1, 2, 4 or 8")
    if keep is not None and str(keep).lower() not in ("loop", "chops", "none", ""):
        raise ValueError("keep: 'loop' (the original loop mostly intact) or 'chops' (rearranged)")
    nchops = max(2, min(16, int(chops or 6)))
    jr = {}
    if project:
        try:
            jr = _mod("flh_store").journal_get(project) or {}
        except Exception:
            jr = {}
    genre_given = bool(genre or jr.get("genre"))
    genre = genre or jr.get("genre") or "boombap"
    if style:
        _style(style, genre)                                       # an unknown style fails before the audio is read
    x, fs, _bits = H["_wav_load"](sample)
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    if not x.size or float(np.abs(x).max()) < 1e-4:               # under -80 dBFS: nothing to flip (keep='loop' too)
        raise ValueError(f"the sample is silent (peak under -80 dBFS): {os.path.basename(sample)}")
    mark("load")
    A = analyze(x, fs, sample_bpm=sample_bpm)
    kind = classify(A)
    kp = str(keep or "").lower()
    if kp == "loop" and not style:
        style = "loop-intact"
    intact = intact_quality(A) if kind["kind"] == "melodic" or kp == "loop" else (False, "", [])
    if kp in ("chops", "none"):
        intact = (False, "keep='chops'", intact[2])
    sname, st, style_why = choose_style(style, genre, kind["kind"], intact[:2], genre_given)
    mark("analyze")
    if bars is None:
        bars = int(st.get("bars") or 2)
        if st.get("keep") == "loop":                               # the whole recorded loop (up to 8 bars)
            nb = loop_grid(A)[2]
            bars = max(b for b in (1, 2, 4, 8) if b <= max(1, nb))
    bars = int(bars)
    if seed is None:
        hs = hashlib.sha1(f"{os.path.basename(sample)}|{os.path.getsize(sample)}|{sname}".encode()).hexdigest()
        seed = int(hs[:8], 16) % 100000
    seed = int(seed)
    target_key = key or (jr.get("key") if project else None)
    semis, new_key, pwhy = decide_pitch(pitch, st, A["key"], target_key)
    ratio = 2.0 ** (semis / 12.0)
    # ---- the song's tempo
    try:
        g = _mod("flh_compose").genre_data(genre) or {}
        genre = g.get("name") or genre
    except Exception:
        raise ValueError(f"unknown genre '{genre}' (flh_genres lists them)") from None
    tmin, tmax = (g.get("tempo") or {}).get("min", 60), (g.get("tempo") or {}).get("max", 180)
    if project:
        try:
            song_bpm = float(_mod("flh_compose").project_view(project)["tempo"])
        except Exception:
            song_bpm = float(H["flp_info"](project).get("tempo_bpm") or 120)
        tempo_how = "the project's tempo"
        if bpm:
            notes.append("bpm is ignored for an existing project (its tempo is kept): the chops are cut to fit it")
    elif bpm:
        song_bpm, tempo_how = float(bpm), "given"
    else:
        song_bpm = A["bpm"] * ratio
        while song_bpm < tmin and song_bpm * 2 <= tmax:
            song_bpm *= 2
        while song_bpm > tmax and song_bpm / 2 >= tmin:
            song_bpm /= 2
        song_bpm = round(song_bpm, 2)
        tempo_how = (f"the sample's {A['bpm']:g} BPM" + (f" x{ratio:.3f} (the {semis:+d} st speed change)" if semis else "")
                     + (" folded into the genre's range" if abs(song_bpm - A["bpm"] * ratio) > 0.5 else ""))
    # ---- candidate lengths: song beats -> source seconds (chops fit the grid at the new speed, no stretch)
    cut = st.get("cut", "short") if kind["kind"] != "drums" else "short"
    want_beats = [float(b) for b in (st.get("chop_beats") or [1, 2])]
    target_s = sorted({round(b * 60.0 / song_bpm * ratio, 4) for b in want_beats})
    if st.get("keep") == "loop":
        cands, rej = None, {}
        picked, plan = arrange_intact(A, st, bars, song_bpm, ratio, notes)
        mark("pick")
    else:
        cands, rej = candidates(A, target_s, cut=cut, beat_grid=cut == "ring" and min(want_beats) >= 2)
        if cut == "ring" and want_beats[0] >= 2:                   # phrase styles: phrase-length chops first
            prim = round(want_beats[0] * 60.0 / song_bpm * ratio, 4)
            for c in cands:
                if abs(c["want_s"] - prim) < 1e-3:
                    c["score"] = round(c["score"] + 0.12, 3)
        picked = pick(cands, nchops)
        mark("pick")
        plan = arrange(picked, st, bars, song_bpm, ratio, seed)
    mark("arrange")
    out_key = target_key or new_key or A["key"]
    for c in picked:                                               # chords in the key they will sound in
        c["_hits"] = chop_hits(A, c, semis, out_key)
        c["chord"] = c["_hits"][0][2] if c["_hits"] else chord_name(list(c["_ch"]), c.get("bass"), semis, out_key)
        c["root"] = (c["_hits"][0][3] - semis) % 12 if c["_hits"] else c["root"]
    ring_through(A, picked, plan, cut, ratio, song_bpm)
    if cut == "ring" and plan.get("holes"):                        # a tonal flip should ring through: say where not
        hs = plan["holes"]
        notes.append(f"{len(hs)} chop(s) stop before the next trigger (longest {max(h[1] for h in hs)} ms, at beat "
                     f"{', '.join(f'{h[0]:g}' for h in hs[:4])}): the sample's next hit comes sooner than the song's "
                     "grid (no time-stretch) - match the song's tempo to the sample or time-stretch those chops in FL")
    for c in picked:
        c["_semis"] = semis
    n_base = len(picked) - len(plan.get("variant_of") or {})
    used = sorted({t["chop"] for t in plan["trig"]})
    letter_of = {}
    for L, ci in plan["assign"].items():
        letter_of.setdefault(ci, L)
    for L, ci in plan["prime"].items():
        letter_of.setdefault(ci, L + "'")
    for v, b in (plan.get("variant_of") or {}).items():
        if b in letter_of:
            letter_of[v] = letter_of[b]

    sp = _spelling(out_key)

    def why(c):
        bits = []
        if len(c.get("_hits") or []) > 1:
            pass                                                   # a phrase: its chords are listed one by one
        elif c["chord"]:
            bits.append(f"clear {c['chord']}" if c["clarity"] >= 0.35 else f"{c['chord']}-ish")
        elif c["clarity"] >= 0.35:
            bits.append(f"tonal ({sp[(c['root'] + semis) % 12]} root)")
        if c["loud"] >= 0.7:
            bits.append("loud")
        if c["attack"] >= 0.6:
            bits.append("strong attack")
        if c["vocal"] >= 0.5:
            bits.append("vocal-ish")
        if c["grid"] >= 0.8:
            bits.append("on the beat")
        if c.get("hits", 0) > 1:
            bits.append(f"{c['hits']} hits (a phrase)")
        bits.append({"onset": "ends on the next hit", "decay": "ends where the ring has died away",
                     "end": "rings to the end of the file", "tail": "ends in the quiet tail of its ring",
                     "grid": "the loop as recorded"}.get(c["end_by"], "ends at a quiet point"))
        return ", ".join(bits)
    _keys, cnum = numbering(plan)
    chop_rows = []
    for i in sorted(range(n_base), key=lambda i: (cnum.get(i, 99), i)):
        c = picked[i]
        row = {"chop": letter_of.get(i), "name": f"Chop {cnum[i]}" if i in cnum else None, "from_s": c["start"],
               "to_s": c["end"], "shift_st": semis, "root": sp[(c["root"] + semis) % 12],
               "plays_beats": round(c["_plays"], 3), "score": c["score"], "why": why(c)}
        if c.get("_src_end", c["end"]) > c["end"] + 1e-3:
            row["rings_to_s"] = round(c["_src_end"], 4)                # rings on past its cut, up to the next hit
        vs = [round(picked[v]["_plays"], 3) for v, b in (plan.get("variant_of") or {}).items() if b == i]
        if vs:
            row["also_plays_beats"] = vs                               # 'Chop 3b'...: the same chop, another length
        chs_ = [h[2] or sp[h[3]] for h in c.get("_hits") or []]
        if len(chs_) > 1:
            row["chords"] = " ".join(chs_[:8]) + (" ..." if len(chs_) > 8 else "")
        elif c.get("chord"):
            row["chord"] = c["chord"]
        if i not in used:
            row["unused"] = True
        chop_rows.append({k: v for k, v in row.items() if v is not None})
    pat = []
    for t in plan["trig"]:
        lab = letter_of.get(t["chop"], "?") if t["div"] is None else (letter_of.get(t["chop"], "?") + "~")
        if t["div"] is not None and pat and pat[-1].endswith(lab) and t["at"] - float(pat[-1].split(":")[0]) < 1.01:
            continue
        pat.append(f"{t['at']:g}:{lab}")
    measured = {"sample": os.path.basename(sample), "seconds": round(A["seconds"], 2),
                "tempo": A["bpm"], "tempo_how": A["bpm_how"] + (f" (confidence {A['bpm_conf']})" if A["bpm_how"] == "measured" else ""),
                "key": A["key"], "onsets": len(A["onsets"]), "candidates": len(cands) if cands is not None else None,
                "rejected": {k: v for k, v in rej.items() if v}}
    measured = {k: v for k, v in measured.items() if v is not None}
    measured["kind"] = kind["kind"]; measured["kind_why"] = kind["why"]
    if intact[1]:
        measured["loop_quality"] = intact[1]
    flip = {"style": sname, "style_why": style_why, "about": st.get("about"), "cut": cut, "pitch_st": semis, "pitch_why": pwhy,
            "speed": round(ratio, 4), "key": target_key or new_key, "song_bpm": song_bpm, "tempo_from": tempo_how,
            "bars": bars, "loop": " ".join(pat), "stutter": "~ = a 1/8-1/16 roll of that chop's head",
            "score": plan["score"], "score_parts": plan["parts"], "tried": plan["tried"], "seed": seed}
    if target_key and new_key and new_key.split()[0] != str(target_key).split()[0] and str(pitch).lower() == "key":
        flip["key_note"] = f"the chops sound in {new_key} (relative of {target_key})"
    if semis and abs(semis) > 7:
        notes.append(f"{semis:+d} st is a big jump: the timbre changes a lot (formants move with the speed)")
    listen = [st.get("listen") or "the loop should groove on its own before the drums",
              "each chop's start (no click, no clipped attack) and end (no cut-off word or note)",
              "the picks are a measured guess at the 'good bits': audition and swap a chop (re-run with another seed "
              "or style, or move a channel's sample start in FL)"]
    res = {"measured": measured, "flip": flip, "chops": chop_rows}
    if A["bpm_how"] == "measured" and A["bpm_conf"] < 0.2:
        notes.append("the sample's tempo is a weak guess (little pulse): give sample_bpm= if you know it")
    if abs(song_bpm - A["bpm"] * ratio) > 0.5 and abs(song_bpm / (A["bpm"] * ratio) - round(song_bpm / (A["bpm"] * ratio))) > 0.02 \
            and abs((A["bpm"] * ratio) / song_bpm - round((A["bpm"] * ratio) / song_bpm)) > 0.02:
        notes.append(f"the sample runs at {A['bpm'] * ratio:.1f} BPM after the pitch change, the song at {song_bpm:g}: "
                     "chops are cut to fit the grid (no time-stretch), so long phrases won't line up beyond one chop")
    notes.append("clearing the sample for a release is your responsibility")
    if dry_run:
        res["would_write"] = {"project": project or f"a new {genre} song (flh_new_song, starter=false)",
                              "chops": len(_keys), "channels": [_chan_name(k, _keys, cnum) for k in _keys],
                              "pattern": "Flip", "drums": bool(drums)}
        res["listen"] = listen; res["notes"] = notes
        steps["total"] = round(time.time() - T0, 3); res["steps_s"] = steps
        res["dry_run"] = True
        return res
    # ---- the song
    section = None
    created = None
    if not project:
        ns_new = H.get("flh_new_song") or _raw_tool("flh_new_song")
        nk = target_key or new_key or None
        try:
            if nk:
                _mod("flh_compose").key_info(nk)
        except Exception:
            nk = None
        r = ns_new(genre=genre, bpm=song_bpm, key=nk, name=name or f"{os.path.splitext(os.path.basename(sample))[0]} Flip",
                   starter=False, open=False, balance=False, seed=seed)
        project = r["project"]
        created = {"project": project, "genre": r.get("genre"), "bpm": r.get("bpm"), "key": r.get("key")}
        section = _main_section(project)
        mark("new_song")
    output = output or project
    H["writable"](output)
    folder = os.path.dirname(os.path.abspath(output))
    cdir = _chops_dir(folder, os.path.splitext(os.path.basename(sample))[0])
    H["writable"](os.path.join(cdir, "x.wav"))
    AE = _mod("fl_audio_edit")
    files = {}
    keys, num = numbering(plan)
    for k in keys:
        ci, div = k
        c = picked[ci]
        plays = (div if div is not None else c["_plays"]) * 60.0 / song_bpm
        y = render_chop(x, fs, c["start"], c.get("_src_end", c["end"]), semis, max_s=plays,
                        fade_out=RING_FADE_S if cut == "ring" and div is None else None)
        fn = _chan_name(k, keys, num)
        if div is not None and "1-" not in fn:
            fn += f" 1-{int(round(4 / div))}"
        fn += ".wav"
        p = os.path.join(cdir, fn)
        H["writable"](p)
        AE.save(p, y, fs, bits=24, clip=True)
        files[k] = p
    mark("chops")
    w = write_flip(project, output, plan, picked, files, sample, genre, seed, drums, bass, section, song_bpm, notes,
                   steps, mark, ratio=ratio, levels=levels)
    w["chops_folder"] = cdir
    w["files"] = [os.path.basename(f) for f in files.values()]
    if created:
        w["new_song"] = created
        if open and H.get("IS_WINDOWS"):
            try:
                H["fl_open"](output)
                w["opened"] = True
            except Exception as e:
                w["opened"] = False
                notes.append(f"saved but not opened in FL ({str(e)[:80]}): fl_open(project)")
        elif open:
            w["opened"] = False
            notes.append("not opened: FL runs on Windows only (the file is ready)")
        mark("open")
    if w.get("levels") is not None:
        res["levels"] = w.pop("levels")
    res["written"] = {k: v for k, v in w.items() if v is not None}
    res["listen"] = listen
    res["listen_at"] = {"start_bar": w["start_bar"], "end_bar": w["start_bar"] + plan["loop_beats"] / 4.0}
    res["notes"] = notes
    res["next"] = ("fl_listen(start_bar, end_bar) - a human should listen; flp_balance(project) evens the levels; "
                   "re-run with seed= / style= for another flip")
    steps["total"] = round(time.time() - T0, 3)
    res["steps_s"] = steps
    return res


# ============================================================================= registration
def register(ns):
    global H
    H = ns
    S, P = ns["_schema"], ns["_p"]
    STR, INT, BOOL, NUM, FLP = ns["_STR"], ns["_INT"], ns["_BOOL"], ns["_NUM"], ns["_FLP"]
    styles = ", ".join(sorted((data().get("styles") or {})))
    guarded = ns["_guard_open_project"](fl_flip_sample, "project", "output")

    def tool(**kw):                       # writing into a project = output (the open song: one FL save + reload);
        if kw.get("project") and not kw.get("dry_run") and not kw.get("output"):   # a dry run never touches FL
            kw["output"] = kw["project"]
        return guarded(**kw)
    tool.__wrapped__ = fl_flip_sample
    ns["TOOLS"]["fl_flip_sample"] = (
        tool,
        "'Flip this sample' like a producer: analyses the sample once (tempo - snapped to the loop's length, key, "
        "onsets, beat grid) and says what it is (melodic keys/guitar/pad loop, vocal/soul phrase or drum loop); the "
        "kind picks the style unless style= is given (" + styles + "): a melodic loop that is already musical stays "
        "intact on the grid with a chopped variation at its end (keep='loop'), else phrase chops rearranged (loop), "
        "vocals get chipmunk soul, drums stutter. Tonal chops keep each chord's ring (end on the next hit or the "
        "decay, 40 ms fade); stab styles (boombap, stutter) cut short. Re-pitches like a sampler (key = to the song's "
        "key). Writes the used chops as WAVs in <song>/chops/, one dark-colored sampler channel each, a 'Flip' "
        "pattern, drums and bass (on each chord's root) under it, and sets the chop inserts' faders so the sample "
        "leads (~4 dB over the drums, measured from the files; drums/bass tucked if needed). project omitted = a new "
        "song at the sample's tempo. dry_run=true: analysis + plan only. The picks are a guess: listen.",
        S(sample=STR("the sample (WAV/FLAC/Ogg/AIFF; MP3/M4A on Windows)"),
          project=P("string", "the .flp to add the flip to (default: a new song)", True),
          output=STR("write to this .flp instead of the project (default: the project)", True),
          genre=STR("genre for the style, drums and a new song (default: the song's, else boombap)", True),
          bpm=NUM("tempo of a new song (default: the sample's, after the pitch change)", True),
          key=STR("target key, e.g. 'F minor' (pitch='key' moves the chops there)", True),
          chops=INT("how many chops to pick (2-16, default 6)", True),
          bars=INT("loop length 1/2/4/8 bars (default: the style's; intact: the whole loop)", True),
          keep=STR("'loop' = original loop intact + end variation; 'chops' = rearranged", True),
          levels=BOOL("set chop faders so the sample leads over the drums (default true)", True),
          pitch=STR("'key' (default) | 'up' | 'down' | semitones like '3' or '-2'", True),
          style=STR(styles + " (default: the genre's)", True),
          drums=BOOL("drums under the flip (default true)", True),
          bass=BOOL("bass on the chops' roots when the song has a bass channel (default true)", True),
          seed=INT("another seed = another pick of chops for the loop", True),
          name=STR("new song name (default '<sample> Flip')", True),
          sample_bpm=NUM("the sample's own tempo if you know it (default: measured)", True),
          open=BOOL("open a new song in FL (default true)", True),
          dry_run=BOOL("analysis + plan only, nothing written", True)))
    ns.setdefault("_OPEN_PROJECT_GUARD", {})["fl_flip_sample"] = ("project", "output")
    ns["fl_flip_sample"] = fl_flip_sample
    ns.setdefault("CROSS_PLATFORM", set()).add("fl_flip_sample")
    try:
        ns["TOOL_GROUPS"]["fl_flip_sample"] = "compose"
    except Exception:
        pass
    try:
        ns["LONG_HELP"]["fl_flip_sample"] = (
            "Typical: fl_flip_sample(sample='C:/Samples/loop.wav', genre='rnb') makes a new song (the sample's kind "
            "picks the style; keep='chops' rearranges a melodic loop instead of keeping it intact); "
            "fl_flip_sample(sample=..., project=<song.flp>, style='soul') adds a flip to a song. dry_run=true first "
            "shows the measured tempo/key, the chops (start/end s, why chosen) and the loop. Re-run with another seed "
            "for other picks. The result says what to listen for; the picks are a guess.")
    except Exception:
        pass
