"""fl_pc_check: the music/audio half of the automatic real-PC verification (newsong, compose/perform, mix, finalize,
review, vocal, midi, handbook, catalog).

`checks(ns)` returns the Check dicts of the shared contract (see flh_pccheck.py): id, title, area, risk, needs,
minutes, run(ctx). Every check works only on projects it builds in ctx.sandbox (flh_new_song into the sandbox,
ctx.make_project) and opens them with ctx.open_in_fl, so the user's songs are never touched. Each run returns
{status, detail (plain words), evidence (small), fix_hint (for the developer), checklist}. Checks are independent:
any one can run alone; songs a check builds are cached per sandbox only as read-only copies (another check that
needs the same FL round trip reuses them, or builds its own when they're missing).

Thresholds and the notes written into test songs are module constants so a developer can tune them after a PC run.
"""
import contextlib
import os
import re
import shutil
import struct
import sys
import time

# ---------------------------------------------------------------------------------------------- tunables
POPUP_WAIT_S = 1.5          # how long to watch for FL popups after a load (missing samples, plugin errors)
SAVE_TRIES = 6              # FL ignores a save right after a load for a few seconds: retry
SILENT_LUFS = -50.0         # a capture quieter than this counts as "no sound"
PITCH_TOL = 1.0             # semitones: an 808 / slide note counts as "at the written pitch" within this
ATTACK_SKIP_S = 0.08        # s after an 808 starts that its pitch is not judged (the attack sweeps down onto the note)
SEED = 7                    # fixed seed: the same songs every run, so results compare between runs
GENRES = ("lofi", "trap", "house")
STAMP_RE = re.compile(r"[^0-9A-Za-z_-]+")

_CACHE = {}                 # sandbox -> {key: value}: read-only copies shared between checks of one run


# ---------------------------------------------------------------------------------------------- small helpers
class Skip(Exception):
    """Raised inside a check: skip with a plain how-to (e.g. no LABS installed, unattended and needs a person)."""
    def __init__(self, detail, how=None, evidence=None):
        super().__init__(detail)
        self.detail, self.how, self.evidence = detail, how, evidence or {}


def _inside_dir(path, folder):
    a = os.path.normcase(os.path.abspath(path)); b = os.path.normcase(os.path.abspath(folder)).rstrip("\\/")
    return a == b or a.startswith(b + os.sep)


def _mod(name):
    m = sys.modules.get(name)
    if m is None:
        import importlib
        m = importlib.import_module(name)
    return m


def _res(status, detail, evidence=None, fix_hint="", checklist=""):
    return {"status": status, "detail": detail, "evidence": _small(evidence or {}), "fix_hint": fix_hint,
            "checklist": checklist}


def _small(o, depth=0):
    """Evidence stays small: long strings cut, lists capped, nesting limited."""
    if isinstance(o, str):
        return o if len(o) <= 300 else o[:297] + "..."
    if isinstance(o, float):
        return round(o, 3)
    if isinstance(o, (list, tuple, set)):
        lst = list(o)
        out = [_small(x, depth + 1) for x in lst[:12]]
        return out + [f"... {len(lst) - 12} more"] if len(lst) > 12 else out
    if isinstance(o, dict):
        if depth > 3:
            return str(o)[:200]
        return {str(k): _small(v, depth + 1) for k, v in list(o.items())[:30]}
    return o


def _cache(ctx):
    return _CACHE.setdefault(os.path.abspath(ctx.sandbox), {})


def _sb(ctx, *parts):
    """A path inside the sandbox (folders made, write permission checked)."""
    p = os.path.join(ctx.sandbox, *parts)
    ctx.ns["writable"](p)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def _copy(ctx, src, *parts):
    dst = _sb(ctx, *parts)
    shutil.copyfile(src, dst)
    return dst


def _ask(ctx, prompt):
    """ctx.ask_user -> the answer, or raise Skip (unattended / the person said skip)."""
    if ctx.unattended:
        raise Skip("needs a person at the PC (unattended run)", how=prompt)
    a = ctx.ask_user(prompt)
    if a is None or str(a).strip().lower() in ("skip", "no", "n", "s"):
        raise Skip("skipped by the person at the PC" if a is not None else "no answer from the person at the PC",
                   how=prompt)
    return str(a)


def _person_first(ctx, how):
    """Checks that need a person: skip at once in an unattended run (before building or opening anything)."""
    if ctx.unattended:
        raise Skip("needs a person at the PC (unattended run)", how=how)


def _popups(ns):
    try:
        return [{"title": d.get("title"), "kind": d.get("kind"), "text": (d.get("text") or [])[:2],
                 "hwnd": d.get("hwnd")} for d in (ns["fl_dialogs"]() or [])]
    except Exception:                                             # noqa: BLE001 - not Windows / FL closed
        return []


def _open(ctx, path):
    """Open a sandbox project in FL -> popups that appeared during/after the load (titles + kinds)."""
    before = {p.get("hwnd") for p in _popups(ctx.ns)}
    ctx.open_in_fl(path)
    seen = {}
    t_end = time.time() + POPUP_WAIT_S
    while True:
        for p in _popups(ctx.ns):
            if p.get("hwnd") not in before:
                seen[p.get("hwnd")] = p
        if time.time() >= t_end:
            break
        time.sleep(min(0.5, max(0.0, t_end - time.time())))
    return [{k: v for k, v in p.items() if k != "hwnd"} for p in seen.values()]


def _fl_save(ctx, path):
    """Let FL save the open sandbox project (retries: FL ignores saves right after a load). -> True when it landed."""
    for i in range(SAVE_TRIES):
        try:
            if ctx.ns["_save_in_fl_and_wait"](path, wait_s=3.0 if i else 4.0):
                return True
        except Exception:                                         # noqa: BLE001 - bridge hiccup: try again
            pass
        time.sleep(1.0)
    return False


def _new_song(ctx, genre, label, **kw):
    """flh_new_song into the sandbox (never opened by the tool itself: ctx.open_in_fl does that) -> (path, result)."""
    folder = os.path.join(ctx.sandbox, "songs")
    ctx.ns["writable"](os.path.join(folder, "x.flp"))
    os.makedirs(folder, exist_ok=True)
    args = dict(genre=genre, name=label, folder=folder, open=False, seed=SEED)
    args.update(kw)
    r = ctx.call("flh_new_song", **args)
    p = r.get("project")
    if not p or not os.path.isfile(p):
        raise RuntimeError(f"flh_new_song made no project: {str(r)[:300]}")
    return p, r


def _journal(path):
    try:
        return _mod("flh_store").journal_get(path)
    except Exception:                                             # noqa: BLE001
        return {}


def _offsets(path):
    try:
        return _mod("flh_score").sounding_offsets(_journal(path))
    except Exception:                                             # noqa: BLE001
        return {}


def _role_channel(path, role):
    j = _journal(path)
    c = ((j.get("compose") or {}).get("channels") or {}).get(role)
    if isinstance(c, list):
        c = c[0]
    if c is None:
        c = next((int(k) for k, v in (j.get("channels") or {}).items() if (v or {}).get("role") == role
                  and str(k).isdigit()), None)
    return None if c is None else int(c)


def _first_pattern(ctx, path):
    """(pattern id, bar) of the first pattern clip in the playlist (a section block of a starter-less song)."""
    clips = [c for c in (ctx.call("flp_playlist", project=path).get("clips") or []) if c.get("kind") == "pattern"]
    if not clips:
        raise RuntimeError("the test song has no pattern clip in its playlist")
    c = min(clips, key=lambda c: c.get("bar") or 0)
    return int(c["pattern"]), float(c.get("bar") or 1)


def _tempo(ns, path):
    try:
        return float(ns["flp_info"](path).get("tempo_bpm") or 120.0)
    except Exception:                                             # noqa: BLE001
        return 120.0


def _listen(ctx, start_bar, end_bar, save=None):
    a = dict(start_bar=float(start_bar), end_bar=float(end_bar), max_seconds=40)
    if save:
        a["save"] = save
    return ctx.call("fl_listen", **a)


def _audio(path):
    import numpy as np
    x, fs, _ = _mod("flh_review").load_audio(path)
    x = np.asarray(x, dtype=float)
    return (x.mean(axis=1) if x.ndim == 2 else x), int(fs)


def _pitch(x, fs, fmin=25.0, fmax=400.0, info=None, skip_attack_s=0.0):
    """-> (times s, MIDI floats) of the voiced frames of a mono signal (flh_transcribe's YIN tracker).
    skip_attack_s: leave out the frames this long after the sound starts (an 808's attack sweeps down from well
    above the note it holds: PC 2026-10-23 measured 'an octave off' from 7 attack frames). info (a dict) gets the
    level and frame counts for the evidence: peak_dbfs, frames, voiced_all, attack_s."""
    import numpy as np
    tr = _mod("flh_transcribe").pitch_track(x, fs, fmin=fmin, fmax=fmax)
    f0 = np.asarray(tr["f0"], dtype=float); t = np.asarray(tr["t"], dtype=float)
    ok = np.asarray(tr["voiced"], dtype=bool) & (f0 > 0)
    rms = np.asarray(tr.get("rms_db", np.zeros(len(t))), dtype=float)
    on = float(t[int(np.argmax(rms > rms.max() - 20.0))]) if len(rms) else 0.0   # where the sound starts
    keep = ok & (t >= on + skip_attack_s) if skip_attack_s else ok
    if not keep.any() and ok.any():                               # all of it inside the attack: keep what there is
        keep = ok
    if info is not None:
        pk = float(np.abs(x).max()) if len(x) else 0.0
        info.update(peak_dbfs=round(float(20 * np.log10(max(pk, 1e-9))), 1), frames=int(len(t)),
                    voiced_all=int(ok.sum()), onset_s=round(on, 3), attack_skipped_s=skip_attack_s)
    return t[keep], 69 + 12 * np.log2(f0[keep] / 440.0)


def _band_db(x, fs, hz, t0=0.0, t1=None):
    """Energy (dB, relative) of x between t0..t1 s within half a semitone of hz: whether a partial is really there
    (the written fundamental of a low 808 vs its 2nd harmonic)."""
    import numpy as np
    a = int(t0 * fs); b = len(x) if t1 is None else min(len(x), int(t1 * fs))
    y = np.asarray(x[a:b], dtype=float)
    if len(y) < int(0.1 * fs) or hz <= 0:
        return None
    y = (y - y.mean()) * np.hanning(len(y))
    P = np.abs(np.fft.rfft(y, 1 << int(np.ceil(np.log2(len(y) * 2))))) ** 2
    f = np.arange(len(P)) * fs / (2 * (len(P) - 1))
    sel = (f >= hz * 2 ** (-1 / 24)) & (f <= hz * 2 ** (1 / 24))
    tot = float(P[(f > 20) & (f < 2000)].sum()) or 1e-30
    return round(10 * np.log10(max(float(P[sel].sum()), 1e-30) / tot), 1) if sel.any() else None


def _note_name(midi):
    n = int(round(midi))
    return ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][n % 12] + str(n // 12 - 1)


def _heard(r):
    w = (r or {}).get("whole") or {}
    return bool((r or {}).get("heard")) and w.get("lufs") is not None and w["lufs"] > SILENT_LUFS


def _take_away(ctx, paths, why):
    """Remove what a check made outside the sandbox, within the rules: the Recycle Bin where files_recycle allows it;
    inside fl-headless's own state folder, moved into the sandbox (the runner keeps / clears the sandbox); anything
    else stays and is returned (the caller reports it). -> [paths left behind]."""
    state = os.path.normcase(os.path.abspath(os.path.dirname(ctx.ns["STATE"])))
    left = []
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        try:
            ctx.ns["files_recycle"]([p])
            continue
        except Exception:                                         # noqa: BLE001 - not allowed there / not Windows
            pass
        if os.path.normcase(os.path.abspath(p)).startswith(state + os.sep):
            try:
                shutil.move(p, _sb(ctx, "removed", why, os.path.basename(p.rstrip("\\/"))))
                continue
            except Exception:                                     # noqa: BLE001
                pass
        left.append(p)
    return left


# ---------------------------------------------------------------------------------------------- file readback
def _readback(ns, path):
    """What a project file holds that newsong writes: channels (+ sample paths), mixer names/colors/routes, effects,
    playlist track names, markers."""
    d = ns["_load"](path); ev = ns["_events"](d)
    N = _mod("flh_newsong")
    out = {"samples": [], "mixer": {}, "effects": {}, "tracks": {}, "markers": [], "channels": 0}
    try:
        out["channels"] = len(_mod("flh_compose").channels_of(d, ev))
    except Exception:                                             # noqa: BLE001
        out["channels"] = sum(1 for e in ev if e["id"] == ns["EV_CHANNEL"])
    for e in ev:
        if e["id"] == ns["EV_SAMPLE_PATH"]:
            p = ns["_payload"](d, e)
            if p.strip(b"\x00"):
                raw = ns["_text16"](p)
                out["samples"].append((raw, ns["_expand_sample"](raw, path)))
    trk = [k for k, e in enumerate(ev) if e["id"] == ns["EV_MIXER_TRACK"]]
    pre_ids = (ns["EV_MIXER_COLOR"], ns["EV_MIXER_HASPROPS"], ns["EV_MIXER_NAME"])
    for t, k0 in enumerate(trk):
        a = k0
        while a > 0 and ev[a - 1]["id"] in pre_ids:
            a -= 1
        name = next((ns["_text16"](ns["_payload"](d, ev[k])) for k in range(a, k0) if ev[k]["id"] == ns["EV_MIXER_NAME"]),
                    None)
        col = next((ns["_payload"](d, ev[k])[:3].hex() for k in range(a, k0) if ev[k]["id"] == ns["EV_MIXER_COLOR"]), None)
        end = trk[t + 1] if t + 1 < len(trk) else len(ev)
        rt = next((ns["_payload"](d, ev[k]) for k in range(k0, end) if ev[k]["id"] == ns["EV_MIXER_ROUTE"]), b"")
        out["mixer"][t] = {"name": name, "color": col, "routes": [i for i, b in enumerate(rt) if b]}
    try:
        for f in ns["flp_list"](path).get("effects") or []:
            if f.get("track") is not None:
                out["effects"][(int(f["track"]), int(f.get("slot") or 0))] = f.get("plugin") or f.get("name")
    except Exception:                                             # noqa: BLE001
        pass
    for t, g in ns["_track_groups"](d, ev).items():                # FL writes 238 43 239 (the name last)
        if g["name"] is not None:
            out["tracks"][t] = g["name"]
    try:
        out["stray_markers"] = [(s["name"], s["pattern"]) for s in ns["_stray_markers"](d, ev)]
    except Exception:                                             # noqa: BLE001
        out["stray_markers"] = []
    try:
        out["markers"] = [(m["bar"], m["name"]) for m in ns["flp_info"](path).get("markers") or []]
    except Exception:                                             # noqa: BLE001
        pass
    return out


def _parses(ns, path):
    try:
        d = ns["_load"](path); ns["_events"](d)
        chk = ns.get("_check_complete")
        if chk:
            chk(d)
        return None
    except Exception as e:                                        # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def _roundtrip(ctx, genre, **kw):
    """Build a sandbox song, open it in FL, let FL save it. Cached per sandbox (read-only copies 'before FL' and
    'after FL save'), so the newsong checks share one FL round trip per genre but each can run alone."""
    key = "rt:" + genre + ":" + ",".join(f"{k}={v}" for k, v in sorted(kw.items()))
    c = _cache(ctx)
    hit = c.get(key)
    if hit and os.path.isfile(hit["pre"]) and (not hit["saved"] or os.path.isfile(hit["post"])):
        return hit
    ctx.say(f"new {genre} song in the sandbox, opening it in FL")
    path, res = _new_song(ctx, genre, f"check {genre}", **kw)
    pre = _copy(ctx, path, "readback", f"{genre} before FL.flp")
    t0 = time.time()
    pops = _open(ctx, path)
    open_s = round(time.time() - t0, 1)
    saved = _fl_save(ctx, path)
    post = _copy(ctx, path, "readback", f"{genre} after FL save.flp") if saved else None
    hit = {"path": path, "res": res, "pre": pre, "post": post, "saved": saved, "popups": pops, "open_s": open_s}
    c[key] = hit
    return hit


def _parse_mixer_result(res):
    """flh_new_song's mixer summary -> ({insert: (name, bus name)}, {bus/send name: track}, [(plugin, track, slot)]);
    the summary names slots as FL shows them (1-based), the file stores them 0-based."""
    mx = res.get("mixer") or {}
    ins, named, fx = {}, {}, []
    for s in mx.get("inserts") or []:
        m = re.match(r"^(\d+) (.+?) -> (.+)$", str(s))
        if m:
            ins[int(m.group(1))] = (m.group(2), m.group(3))
    for s in (mx.get("buses") or []) + (mx.get("sends") or []):
        m = re.match(r"^(\d+) (.+)$", str(s))
        if m:
            named[m.group(2)] = int(m.group(1))
    for s in mx.get("effects") or []:
        m = re.match(r"^(.+?) on (master|track (\d+)) slot (\d+)", str(s))
        if m:
            fx.append((m.group(1), 0 if m.group(2) == "master" else int(m.group(3)), int(m.group(4)) - 1))  # 1-based
    return ins, named, fx


# ============================================================================================== newsong checks
def c_newsong_opens(ctx):
    rows, bad = {}, []
    for g in GENRES:
        rt = _roundtrip(ctx, g)
        err = _parses(ctx.ns, rt["post"]) if rt["saved"] else "FL didn't save"
        a = _readback(ctx.ns, rt["pre"])
        b = _readback(ctx.ns, rt["post"]) if rt["saved"] else None
        rows[g] = {"popups": [p.get("title") for p in rt["popups"]], "saved": rt["saved"], "open_s": rt["open_s"],
                   "channels": [a["channels"], b["channels"] if b else None]}
        if rt["popups"]:
            bad.append(f"{g}: FL showed {', '.join(repr(p.get('title')) for p in rt['popups'])} while opening")
        if err:
            bad.append(f"{g}: {err}")
        elif b and b["channels"] != a["channels"]:
            bad.append(f"{g}: FL kept {b['channels']} of {a['channels']} channels")
    if bad:
        return _res("fail", "New songs didn't open cleanly in FL: " + "; ".join(bad), rows,
                    "flh_newsong: check the popup text (missing sample / unknown plugin / corrupt data); compare the "
                    "'before FL' and 'after FL save' copies in the sandbox readback folder with flp_diff")
    return _res("pass", f"{', '.join(GENRES)} songs opened in FL with no popups, FL saved them and every channel "
                        "is still there", rows)


def c_newsong_samples(ctx):
    rt = _roundtrip(ctx, "lofi")
    if not rt["saved"]:
        return _res("fail", "FL didn't save the test song, so the sample paths couldn't be read back",
                    {"song": os.path.basename(rt["path"])}, "bridge transport save; see core save checks")
    a, b = _readback(ctx.ns, rt["pre"]), _readback(ctx.ns, rt["post"])
    if not b["samples"]:
        return _res("fail", "After FL saved the song, no channel holds a sample file", {"before": len(a["samples"])},
                    "flh_newsong._sampler_block / EV_SAMPLE_PATH: FL dropped the paths we wrote")
    missing = [raw for raw, full in b["samples"] if not os.path.isfile(full)]
    factory = sum(1 for raw, _ in b["samples"] if "%FLStudioFactoryData%" in raw)
    changed = sorted({r for r, _ in a["samples"]} ^ {r for r, _ in b["samples"]})
    ev = {"samples": len(b["samples"]), "factory_refs": factory, "missing": missing[:5], "changed_by_fl": changed[:4]}
    if missing:
        return _res("fail", f"{len(missing)} of {len(b['samples'])} sample paths FL saved don't exist on this PC "
                            f"(e.g. {missing[0]})", ev,
                    "_expand_sample: %FLStudioFactoryData% should map to the FL install folder (fl_exe's folder); "
                    "check where FL keeps Data\\Patches\\Packs")
    return _res("pass", f"All {len(b['samples'])} samples load: FL saved their paths ({factory} via "
                        "%FLStudioFactoryData%) and every file exists" +
                (" (FL rewrote some paths, still valid)" if changed else ""), ev)


SPEED_TARGETS = {"new_beat_s": 120.0, "change_s": 15.0, "live_change_s": 3.0, "add_sound_s": 30.0, "preview_s": 30.0}


def c_speed_beat(ctx):
    """The user's waits, timed: a whole new beat (build + open in FL), a live change (mixer + channel names), a note
    change merged into one reload, adding a sound, and a faster-than-real-time preview - each against its target."""
    T = {}
    t0 = time.time()
    path, res = _new_song(ctx, "lofi", "speed check", vibe="dusty warm")
    T["build_s"] = round(time.time() - t0, 1)
    t1 = time.time(); ctx.open_in_fl(path); T["open_s"] = round(time.time() - t1, 1)
    T["new_beat_s"] = round(T["build_s"] + T["open_s"], 1)
    chans = sorted(int(k) for k in (_journal(path).get("channels") or {}) if str(k).isdigit())
    if not chans:
        raise RuntimeError("the speed-check song has no channels in its journal")
    ch0 = chans[0]
    t1 = time.time()
    r = ctx.call("flp_edit", project=path, ops=[
        {"tool": "flp_mixer", "tracks": [{"track": 1, "volume_db": -4}]},
        {"tool": "flp_style_channels", "channels": [{"channel": ch0, "name": "Speed Kick"}]}])
    T["live_change_s"] = round(time.time() - t1, 2)
    live = bool(r.get("live"))
    keys = _role_channel(path, "keys") or _role_channel(path, "chords") or (chans[-1] if len(chans) > 1 else ch0)
    t1 = time.time()
    ctx.call("flp_replace_part", project=path, output=path, channel=keys,
             notes=[[1, 1, "C4", 90], [1, 1, "Eb4", 85], [1, 1, "G4", 85], [2, 1, "Bb3", 90]])
    ctx.call("flh_sync")
    T["change_s"] = round(time.time() - t1, 1)
    smp = None
    try:
        hits = ctx.ns["sample_search"]("shaker", None, "lofi", limit=1)["results"]
        smp = hits[0]["path"] if hits else None
    except Exception:                                             # noqa: BLE001
        smp = None
    if smp:
        t1 = time.time()
        ctx.call("flp_load_sample", project=path, output=path, sample=smp)
        ctx.call("flh_sync")
        T["add_sound_s"] = round(time.time() - t1, 1)
    t1 = time.time()
    try:
        ctx.call("fl_preview")
        T["preview_s"] = round(time.time() - t1, 1)
    except Exception as e:                                        # noqa: BLE001
        T["preview_error"] = str(e)[:120]
    ev = dict(T, live=live, build_steps=res.get("steps_s"), targets=SPEED_TARGETS)
    slow = [f"{k} {T[k]:g} s (target {v:g})" for k, v in SPEED_TARGETS.items() if T.get(k) is not None and T[k] > v]
    fast = True
    try:
        fast = bool(ctx.ns["_fast_on"]())
    except Exception:                                             # noqa: BLE001
        pass
    if not live and fast:
        slow.append("the mixer / name change wasn't done live (it reloaded)")
    ev["fast_paths"] = fast
    if T.get("preview_error"):
        slow.append("preview failed: " + T["preview_error"])
    if slow:
        return _res("fail", "Too slow: " + "; ".join(slow), ev,
                    "fl_timings + build_steps show where the seconds went; live routes need the bridge "
                    "(_live_route_ok), merged edits need fast paths on (_fast_on)")
    return _res("pass", f"New beat {T['new_beat_s']:g} s, live change {T['live_change_s']:g} s, note change "
                        f"{T['change_s']:g} s" + (f", new sound {T['add_sound_s']:g} s" if "add_sound_s" in T else "") +
                (f", preview {T['preview_s']:g} s" if "preview_s" in T else "") + ": all within target", ev)


def c_sound_shape(ctx):
    """fl_sound_shape on a new song's keys: darker + dreamy, measured, level matched, then undone."""
    path, res = _new_song(ctx, "lofi", "check shape")
    _open(ctx, path)
    ch = _role_channel(path, "keys") or _role_channel(path, "chords") or _role_channel(path, "bass")
    if ch is None:
        raise Skip("the test song has no keys / chords / bass channel")
    r = ctx.call("fl_sound_shape", channel=ch, words="dark dreamy", amount="heavy")
    ev = {k: r.get(k) for k in ("instrument", "instrument_moves", "effects", "effects_skipped", "added_effects",
                                "measured", "level_matched", "result", "notes", "steps_s")}
    u = ctx.call("fl_sound_shape", channel=ch, undo=True)
    ev["undo"] = {k: u.get(k) for k in ("undone", "restored", "removed_effects", "problems")}
    if not r.get("effects") and not r.get("instrument_moves"):
        if any("Master" in n for n in r.get("notes") or []) and not r.get("instrument_moves"):
            raise Skip("the test song's channel plays straight into the Master (no mixer track of its own)",
                       evidence=ev)
        return _res("fail", "fl_sound_shape changed nothing on the channel", ev, "flh_sounddesign: maps / bridge")
    res_txt = " ".join(r.get("result") or [])
    if "achieved" not in res_txt:
        return _res("fail", f"The channel didn't measure darker after shaping: {res_txt or r.get('measured')}", ev,
                    "data/sound/shape.json 'dark' moves / plugin maps on this PC")
    if not u.get("undone") or u.get("problems"):
        return _res("fail", f"Undo didn't put it all back: {u.get('problems')}", ev, "flh_sounddesign._undo")
    return _res("pass", f"Shaped {r.get('instrument')} darker + dreamy ({res_txt}), level matched "
                        f"({r.get('level_matched') or 'no change needed'}), then undone", ev)


def c_newsong_mixer(ctx):
    rt = _roundtrip(ctx, "lofi")
    if not rt["saved"]:
        return _res("fail", "FL didn't save the test song, so the mixer couldn't be read back", {},
                    "bridge transport save")
    a, b = _readback(ctx.ns, rt["pre"]), _readback(ctx.ns, rt["post"])
    ins, named, _ = _parse_mixer_result(rt["res"])
    bad = []
    for t, (name, bus) in sorted(ins.items()):
        got = b["mixer"].get(t) or {}
        if got.get("name") != name:
            bad.append(f"insert {t} named {got.get('name')!r} (want {name!r})")
        want_to = named.get(bus, 0 if bus.lower() == "master" else None)
        if want_to is not None and got.get("routes") != [want_to]:
            bad.append(f"insert {t} routes to {got.get('routes')} (want [{want_to}] {bus})")
    for name, t in sorted(named.items(), key=lambda kv: kv[1]):
        if (b["mixer"].get(t) or {}).get("name") != name:
            bad.append(f"track {t} named {(b['mixer'].get(t) or {}).get('name')!r} (want {name!r})")
    lost_color = [t for t, v in a["mixer"].items() if v.get("color") and (b["mixer"].get(t) or {}).get("color") != v["color"]]
    if lost_color:
        bad.append(f"colors changed on tracks {lost_color[:6]}")
    ev = {"inserts": len(ins), "buses_sends": named, "problems": bad[:6]}
    if bad:
        return _res("fail", "After FL saved the song the mixer isn't as written: " + "; ".join(bad[:4]), ev,
                    "flh_newsong mixer writer (EV_MIXER_NAME 204 / COLOR 149 / ROUTE 235 before/after event 236): "
                    "diff the readback copies")
    return _res("pass", f"Mixer as planned after FL's save: {len(ins)} inserts named and routed to their buses, "
                        f"{len(named)} buses/sends named, colors kept", ev)


def c_newsong_effects(ctx):
    rows, bad = {}, []
    for g in ("lofi", "trap"):
        rt = _roundtrip(ctx, g)
        if not rt["saved"]:
            bad.append(f"{g}: FL didn't save"); continue
        b = _readback(ctx.ns, rt["post"])
        _, _, fx = _parse_mixer_result(rt["res"])
        got = {f"{t}/{s}": b["effects"].get((t, s)) for _, t, s in fx}
        rows[g] = got
        for plug, t, s in fx:
            have = b["effects"].get((t, s))
            if not have or _norm(have) != _norm(plug):
                bad.append(f"{g}: {plug} on track {t} slot {s} reads back as {have!r}")
        master = sorted((s, p) for (t, s), p in b["effects"].items() if t == 0)
        lim = _mod("flh_newsong")._is_limiter
        if master and not lim(master[-1][1] or ""):
            bad.append(f"{g}: the master's last effect is {master[-1][1]!r}, not a limiter")
    if bad:
        return _res("fail", "Effects from FL's plugin database didn't all load: " + "; ".join(bad[:4]), rows,
                    "flh_newsong effects: the plugin-database .fst (Presets\\Plugin database\\...) as a mixer slot "
                    "block; check the plugin header (track/slot) and that FL accepts it")
    return _res("pass", "Reverb, delay, EQ and the master limiter from FL's plugin database are on their mixer "
                        "slots after FL's save", rows)


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def c_newsong_playlist(ctx):
    rt = _roundtrip(ctx, "lofi")
    if not rt["saved"]:
        return _res("fail", "FL didn't save the test song", {}, "bridge transport save")
    a, b = _readback(ctx.ns, rt["pre"]), _readback(ctx.ns, rt["post"])
    snap = None
    try:
        snap = ctx.snapshot("new song playlist (track names + markers)")
    except Exception:                                             # noqa: BLE001
        pass
    lost_t = {t: n for t, n in a["tracks"].items() if b["tracks"].get(t) != n}
    lost_m = [m for m in a["markers"] if m not in b["markers"]]
    ev = {"tracks": dict(list(b["tracks"].items())[:8]), "markers": b["markers"][:10], "snapshot": snap}
    stray = [x for x in b.get("stray_markers") or [] if x not in (a.get("stray_markers") or [])]
    if stray:
        ev["moved_to_patterns"] = stray[:10]                      # FL took them as a pattern's own markers
    if lost_t or lost_m or not b["tracks"] or not b["markers"]:
        return _res("fail", f"Playlist names/markers changed by FL: tracks {lost_t or 'none'}, markers "
                            f"{lost_m or 'none'}" + ("" if b["markers"] else " (no markers at all)")
                    + (f"; FL kept {len(stray)} of them as pattern markers (pattern {stray[0][1]})" if stray else ""),
                    ev, "flh_newsong._style_tracks (238 43 239) / _markers at fl_headless_mcp._marker_slot "
                        "(148 33 34 .. 205 after the arrangement's 233 + track groups)")
    return _res("pass", f"Playlist tracks {', '.join(list(b['tracks'].values())[:4])}... and {len(b['markers'])} "
                        "section markers survive FL's save" + ("; snapshot saved for a look" if snap else ""), ev)


def _solo_song(ctx, genre, pick, notes_fn, label, **kw):
    """A starter-less song (silent apart from what we add) with notes on one channel in the first section block,
    opened in FL. pick(result, path) -> channel or None (-> Skip). notes_fn(channel, offset) -> flp_add_notes notes.
    -> (path, channel, result, bar of the block, bpm)."""
    path, res = _new_song(ctx, genre, label, starter=False, **kw)
    ch = pick(res, path)
    if ch is None:
        return path, None, res, None, None
    pid, bar = _first_pattern(ctx, path)
    off = _offsets(path).get(ch, 0)
    ctx.call("flp_add_notes", path=path, notes=notes_fn(ch, off), pattern=pid, channel=ch)
    _open(ctx, path)
    return path, ch, res, bar, _tempo(ctx.ns, path)


def _channel_by_line(res, words):
    for line in res.get("channels") or []:
        m = re.match(r"^(\d+) ", str(line))
        if m and any(w.lower() in str(line).split("[")[0].lower() for w in words):
            return int(m.group(1)), str(line)
    return None, None


CHORD = [60, 64, 67]


def _chord_notes(ch, off):
    return [{"note": n + off, "bar": 1, "beat": 0, "beats": 4, "velocity": 100} for n in CHORD]


def c_newsong_labs(ctx):
    cat = ctx.call("fl_sound_catalog")
    labs = ((cat.get("sources") or {}).get("LABS") or {})
    if not labs.get("found"):
        raise Skip("LABS / Splice INSTRUMENT isn't installed on this PC, so there's nothing to check",
                   how="install a free LABS pack (e.g. Soft Piano) in the Spitfire/Splice app, then run this check "
                       "again", evidence={"why": labs.get("why")})
    line = {}

    def pick(res, path):
        c, l = _channel_by_line(res, ("labs", "instrument"))
        line["l"] = l
        return c
    path, ch, res, bar, bpm = _solo_song(ctx, "lofi", pick, _chord_notes, "check labs")
    if ch is None:
        return _res("warn", "LABS is installed but the lofi recipe didn't put it on a channel",
                    {"channels": (res.get("channels") or [])[:6]},
                    "flh_newsong LABS pick: catalog finds LABS packs but the recipe fell back (see the skipped "
                    "reasons in the channel lines)")
    r = _listen(ctx, bar, bar + 1)
    ev = {"channel": line["l"], "lufs": (r.get("whole") or {}).get("lufs"), "heard": r.get("heard")}
    if not _heard(r):
        return _res("fail", "The LABS channel made no sound when its notes played", ev,
                    "flp_instrument_sound on a fresh INSTRUMENT from the plugin database (PC-VERIFY newsong.md 13): "
                    "the plugin state may need the pack/preset chunk")
    return _res("pass", "The LABS channel plays: a chord on it was heard in FL", ev)


def c_newsong_flex(ctx):
    """A new song's FLEX channel plays: the preset fl-headless picked from the installed packs (FLEX state with the
    pack/preset fields written, flh_flex.flex_fst) or, with no pack readable, FLEX's own default sound."""
    line = {}

    def pick(res, path):
        c, l = _channel_by_line(res, ("flex",))
        line["l"] = l
        return c
    for g in ("lofi", "trap"):
        path, ch, res, bar, bpm = _solo_song(ctx, g, pick, _chord_notes, f"check flex {g}")
        if ch is not None:
            break
    if ch is None:
        why = sorted({x.strip() for ln in res.get("channels") or [] for x in
                      (str(ln).split("[skipped:", 1)[1].rstrip("]").split(";") if "[skipped:" in str(ln) else [])
                      if "flex" in x.lower()})
        raise Skip("no FLEX channel was made (FLEX isn't installed, no FLEX preset of the parts' kinds was found, or "
                   "LABS / Sytrus / FL's own instruments covered every part)" + (f": {why[:3]}" if why else ""),
                   how="nothing to do; with FLEX installed and LABS missing the recipes use FLEX",
                   evidence={"flex_skips": why[:6]})
    preset = not re.search(r"(?i)flex \(its default", line["l"] or "")
    r = _listen(ctx, bar, bar + 1)
    ev = {"channel": line["l"], "lufs": (r.get("whole") or {}).get("lufs"), "heard": r.get("heard"),
          "loaded": "a FLEX preset picked by fl-headless" if preset else "FLEX's default sound"}
    if not _heard(r):
        if preset:
            return _res("fail", "The FLEX channel a new song made (a preset picked from the installed packs) made no "
                                "sound", ev,
                        "flh_flex.flex_fst: FLEX may not load the pack/preset written into its state on this FL / "
                        "FLEX version (template data/flex/flex_template.fst; open the song and look at FLEX's preset "
                        "name)")
        return _res("fail", "FLEX made no sound before a preset was picked: new songs would have a silent FLEX part",
                    ev, "flh_newsong FLEX fallback: load a FLEX preset .fst (Presets\\Plugin presets\\Generators\\FLEX) "
                        "instead of the bare plugin-database state")
    return _res("pass", ("The FLEX channel plays the preset a new song picked" if preset else
                         "FLEX plays its default sound before any preset is picked") +
                f" ({str(line['l']).split(' -> ')[0]})", ev)


def _bass_pick(res, path):
    return _role_channel(path, "bass")


def c_newsong_808_pitch(ctx):
    target = {}

    def notes(ch, off):
        return [{"note": target["sound"] + off, "bar": 1, "beat": 0, "beats": 4, "velocity": 110}]
    target["sound"] = 37                                           # C#2 (trap's default key root): where 808s live
    path, ch, res, bar, bpm = _solo_song(ctx, "trap", _bass_pick, notes, "check 808 pitch")
    if ch is None:
        raise Skip("the trap recipe made no 808/bass channel", how="check data/newsong recipes for trap")
    off = _offsets(path).get(ch, 0)
    plug = (((_journal(path).get("channels") or {}).get(str(ch))) or {}).get("plugin")
    if not off and not plug:                                       # a plugin 808 plays in key with no offset
        return _res("warn", "The 808 channel has no transpose offset in the journal: its sample pitch wasn't read, "
                            "so notes play at the sample's own pitch",
                    {"channel": ch, "journal": ((_journal(path).get("channels") or {}).get(str(ch))),
                     "line": next((l for l in res.get("channels") or [] if str(l).startswith(f"{ch} ")), None)},
                    "flh_newsong._sample_pitch on the 808 sample")
    wav = _sb(ctx, "audio", "808 pitch.flac")
    r = _listen(ctx, bar, bar + 1, save=wav)
    if not _heard(r):
        return _res("fail", "The 808 note made no sound in FL", {"channel": ch, "offset": off},
                    "check the sampler channel and its mixer route")
    x, fs = _audio(r.get("saved") or wav)
    x = x[: int(fs * 60 / bpm * 3)]
    lv = {}
    t, midi = _pitch(x, fs, 25.0, 300.0, info=lv, skip_attack_s=ATTACK_SKIP_S)
    src = "the sample's offset" if not plug else f"the plugin ({plug}) preset's own tuning"
    if len(midi) < 5:
        quiet = lv.get("peak_dbfs") is not None and lv["peak_dbfs"] < -30
        return _res("warn", "The 808 sounded but its pitch couldn't be measured (" +
                    (f"the recording peaks at only {lv['peak_dbfs']} dBFS: turn the Windows / FL master volume up"
                     if quiet else "too noisy / short") + ")",
                    dict(lv, voiced_frames=int(len(midi)), channel=ch, plugin=plug),
                    "listen: the 808 should play C#2 (written +offset)")
    import numpy as np
    med = float(np.median(midi)); want = target["sound"]; diff = med - want
    ev = {"channel": ch, "offset": off, "plugin": plug, "written": want + off, "should_sound": _note_name(want),
          "heard": _note_name(med), "diff_semitones": round(diff, 2), "voiced_frames": int(len(midi)),
          "spread_semitones": round(float(np.percentile(midi, 90) - np.percentile(midi, 10)), 2), **lv}
    if abs(diff) <= PITCH_TOL:
        return _res("pass", f"The 808 sounds at {_note_name(med)} as written (offset {off:+d} applied correctly)", ev)
    if min(abs(diff - 12), abs(diff + 12)) <= PITCH_TOL:
        # an octave either way: the spectrum of the held part says whether the written fundamental is really there
        hz = 440.0 * 2 ** ((want - 69) / 12); t0 = lv.get("onset_s", 0) + ATTACK_SKIP_S
        e_want, e_up, e_down = _band_db(x, fs, hz, t0), _band_db(x, fs, 2 * hz, t0), _band_db(x, fs, hz / 2, t0)
        ev.update(band_db={"written": e_want, "octave_up": e_up, "octave_down": e_down})
        other = e_up if diff > 0 else e_down
        # read high: the written fundamental is there (a low 808's fundamental is rarely 15 dB under its 2nd
        # harmonic; a sound really an octave up has nothing there). Read low: nothing real an octave below.
        tracker_err = e_want is not None and other is not None and (
            e_want >= other - 15.0 if diff > 0 else other < e_want - 20.0)
        if tracker_err:
            return _res("pass", f"The 808 holds {_note_name(want)} as written: the spectrum has the written "
                                f"fundamental ({e_want} dB vs {other} dB an octave {'up' if diff > 0 else 'down'}); "
                                f"the pitch tracker read {_note_name(med)} (an octave error of the tracker, not the "
                                "808)", ev, "flh_transcribe.pitch_track octave choice on low notes")
        return _res("fail", f"The 808 sounds an octave {'high' if diff > 0 else 'low'} ({_note_name(med)} for "
                            f"{_note_name(want)}; the spectrum agrees: {other} dB there vs {e_want} dB at the written "
                            f"note): {src} is off by an octave", ev,
                    "flh_newsong._sample_pitch octave of the 808 sample" if not plug else
                    "the recipe's 808 plugin preset plays an octave away from the written note: transpose it")
    return _res("fail", f"The 808 plays {_note_name(med)} where it should sound {_note_name(want)} (written "
                        f"{_note_name(want + off)}, {diff:+.1f} semitones off): {src} is wrong", ev,
                "flh_newsong._sample_pitch / journal channels.N.transpose: FL plays a sample's root on C5" if not plug
                else "the 808 plugin preset isn't tuned to the written note: record a transpose for it")


def c_compose_slide(ctx):
    lo, hi = 37, 40                                                # C#2 held, slide up to E2
    # short on purpose: factory 808 samples decay within 1-2 s; the glide takes half a beat starting on beat 1.75

    def notes(ch, off):
        return [{"note": lo + off, "bar": 1, "beat": 0, "beats": 2, "velocity": 110},
                {"note": hi + off, "bar": 1, "beat": 0.75, "beats": 0.5, "velocity": 110, "slide": True}]
    path, ch, res, bar, bpm = _solo_song(ctx, "trap", _bass_pick, notes, "check slide")
    if ch is None:
        raise Skip("the trap recipe made no 808/bass channel", how="check data/newsong recipes for trap")
    wav = _sb(ctx, "audio", "slide.flac")
    r = _listen(ctx, bar, bar + 1, save=wav)
    if not _heard(r):
        return _res("fail", "The 808 with a slide note made no sound", {"channel": ch}, "sampler channel / routing")
    import numpy as np
    x, fs = _audio(r.get("saved") or wav)
    beat = 60.0 / bpm
    lv = {}
    t, midi = _pitch(x[: int(fs * beat * 2.2)], fs, 25.0, 300.0, info=lv, skip_attack_s=ATTACK_SKIP_S)
    if len(midi) < 10:
        quiet = lv.get("peak_dbfs") is not None and lv["peak_dbfs"] < -30
        return _res("warn", "The slide sounded but its pitch couldn't be measured (" +
                    (f"the recording peaks at only {lv['peak_dbfs']} dBFS: turn the Windows / FL master volume up"
                     if quiet else "too noisy / short, or the 808 decays before the slide") + ")",
                    dict(lv, voiced_frames=int(len(midi)), channel=ch),
                    "listen: the held note should bend up 3 semitones after the first beat")
    t = t - lv.get("onset_s", 0.0)                                # beats counted from where the 808 starts
    early, late = t < beat * 0.65, (t > beat * 1.35) & (t < beat * 2.0)
    raw_start = float(np.median(midi[early])) if early.any() else None
    raw_end = float(np.median(midi[late])) if late.any() else None
    between = int(((midi > raw_start + 0.5) & (midi < raw_end - 0.5)).sum()) if raw_start and raw_end else 0
    ev = {"start": _note_name(raw_start) if raw_start else None, "end": _note_name(raw_end) if raw_end else None,
          "rise_semitones": round((raw_end or 0) - (raw_start or 0), 2) if raw_start and raw_end else None,
          "frames_in_between": between, "channel": ch, "voiced_frames": int(len(midi)), **lv}
    if raw_start is None or raw_end is None:
        return _res("warn", "Couldn't measure both ends of the slide", ev, "listen to the 808 in the sandbox song")
    rise = raw_end - raw_start
    if abs(rise - (hi - lo)) > PITCH_TOL:
        return _res("fail", f"The slide note didn't bend the held 808 by 3 semitones (measured {rise:+.1f})", ev,
                    "SLIDE flag 0x08 in the note record (flh_compose.SLIDE_FLAG / NOTE_SLIDE): FL may ignore it")
    if between < 3:
        return _res("fail", "The 808 jumped to the new pitch instead of gliding: the slide note plays as a plain note",
                    ev, "FL slide notes need the flag in the record AND the held note still sounding; check the flag bit")
    return _res("pass", f"The slide note glides the held 808 up {rise:.1f} semitones ({between} frames in between)",
                ev)


def c_compose_beat(ctx):
    path, res = _new_song(ctx, "trap", "check compose", starter=False)
    _open(ctx, path)
    r = ctx.call("flp_compose", project=path, what="beat", genre="trap", variations=2, seed=SEED)
    vs = r.get("variations") or []
    if r.get("verify_job"):
        try:
            ctx.call("fl_job", job=r["verify_job"], wait_s=60)
        except Exception:                                         # noqa: BLE001
            pass
    if len(vs) != 2 or not vs[0].get("listen"):
        return _res("fail", f"flp_compose made {len(vs)} variations (asked for 2)", {"keys": sorted(r)[:12]},
                    "flh_compose variations")
    lst = vs[0]["listen"]
    a = float(lst.get("start_bar") or 1)
    ln = _listen(ctx, a, min(float(lst.get("end_bar") or a + 2), a + 2))
    w = ln.get("whole") or {}
    ev = {"variations": [v.get("label") for v in vs], "reloaded": r.get("reloaded_in_fl"), "queued": r.get("queued"),
          "lufs": w.get("lufs"), "true_peak": w.get("true_peak_dbfs"), "clipped": w.get("clipped_samples")}
    if not _heard(ln):
        return _res("fail", "FL played the composed beat silently (or didn't get it)", ev,
                    "flp_compose live path: reloaded_in_fl / verify; check FL holds the new patterns")
    if (w.get("clipped_samples") or 0) > 0 or (w.get("true_peak_dbfs") or -99) > 0.0:
        return _res("warn", "The composed beat plays but clips at the output (true peak over 0 dBFS)", ev,
                    "starter levels: channel volumes / velocities of the trap beat too hot")
    return _res("pass", "flp_compose wrote 2 beat variations into the open song and FL plays them, no clipping", ev)


def c_compose_fpc(ctx):
    fst = (_mod("flh_newsong")._find_plugin("FPC") or {}).get("default")
    if not fst or not os.path.isfile(fst):
        raise Skip("FPC isn't in FL's plugin database on this PC", how="FL's plugin database lists FPC on every "
                   "edition: refresh it in FL's browser (Plugin database > Installed), then run again")
    gm = [36, 38, 42, 46]

    def pick(res, path):
        c = _role_channel(path, "chords")
        if c is None:
            return None
        ctx.call("flp_load_plugin", project=path, output=path, preset=fst, channel=c)
        return c

    def notes(ch, off):
        return [{"note": k, "bar": 1, "beat": i, "beats": 0.5, "velocity": 110} for i, k in enumerate(gm)]
    path, ch, res, bar, bpm = _solo_song(ctx, "trap", pick, notes, "check fpc")
    if ch is None:
        raise Skip("the trap recipe made no plugin channel to put FPC on", how="check data/newsong recipes")
    wav = _sb(ctx, "audio", "fpc.flac")
    r = _listen(ctx, bar, bar + 1, save=wav)
    if not _heard(r):
        return _res("fail", "FPC made no sound for GM keys 36/38/42/46", {"channel": ch},
                    "FPC loaded from the plugin database may come up with no kit: check its default state")
    x, fs = _audio(r.get("saved") or wav)
    beat = 60.0 / bpm
    hits = _key_hits(x, fs, [i * beat for i in range(len(gm))])
    ev = {"keys": gm, "channel": ch, "level_db": [h["level_db"] for h in hits], "peak_db": [h["peak_db"] for h in hits],
          "rise_db": [h["rise_db"] for h in hits]}
    silent = [gm[i] for i, h in enumerate(hits) if not h["sounds"]]
    if silent:
        return _res("fail", f"FPC pads on GM keys {silent} made no sound", ev,
                    "flh_compose FPC GM map (kick 36, snare 38, closed hat 42, open hat 46): FPC's default kit")
    top = max(h["peak_db"] for h in hits)
    soft = [gm[i] for i, h in enumerate(hits) if h["peak_db"] < top - QUIET_PAD_DB]
    if soft:                                                      # a sound, just a soft pad in FPC's default kit
        ev["soft_pads"] = soft                                    # (PC 2026-10-23: the open hat ~20 dB under the kick)
    return _res("pass", "FPC plays GM keys 36/38/42/46 (kick, snare, hats)" + (
        f"; key(s) {soft} are much softer than the kick in FPC's default kit" if soft else "")
        + "; listen once to confirm the right pads", ev)


QUIET_PAD_DB = 18.0                                               # a pad this far under the loudest: 'soft', not silent
HIT_FLOOR_DB = -60.0                                              # a hit's peak must be over this ...
HIT_RISE_DB = 6.0                                                 # ... and this far over what played just before it


def _key_hits(x, fs, starts_s, win_s=0.15, pre_s=0.04, search_s=0.12):
    """Did each hit at starts_s (seconds into x) sound? Judged against the silence / decay just before it, not an
    absolute level: a soft, long pad (FPC's open hat peaks ~-42 dBFS, its 150 ms RMS ~-46 dB: PC 2026-10-23) is a
    sound. -> [{"level_db" (RMS of the window), "peak_db", "rise_db" (onset RMS over the 40 ms before), "sounds"}]."""
    import numpy as np
    x = np.asarray(x, dtype=float)
    if x.ndim > 1:
        x = np.abs(x).max(axis=1)
    db = lambda v: float(20 * np.log10(v + 1e-9))
    # a speaker capture starts a little late or early (audio latency, PC 2026-10-23: an open hat measured 'no rise'
    # at its nominal time): find each hit's real onset near where it should be, the 5 ms frame with the biggest rise
    # over the frames before it, instead of trusting the nominal time
    hop = max(1, int(fs * 0.005))
    env = np.array([np.sqrt(np.mean(x[i:i + hop] ** 2)) for i in range(0, max(0, len(x) - hop) + 1, hop)] or [0.0])
    k_pre = max(1, int(pre_s / 0.005))
    edb = np.concatenate([np.full(k_pre, -180.0), 20 * np.log10(env + 1e-9)])   # silence before the capture

    def onset(t):
        lo = max(0, int((t - search_s) / 0.005)); hi = min(len(edb) - 1 - k_pre, int((t + search_s) / 0.005))
        best, bt = -999.0, t
        for i in range(lo, hi + 1):
            j = i + k_pre
            r = edb[j:j + 2].max() - edb[j - k_pre:j].mean()
            if r > best:
                best, bt = r, i * 0.005
        return bt
    out = []
    for t0 in starts_s:
        t = onset(t0) if search_s else t0
        a = int(fs * t); b = int(fs * (t + win_s)); seg = x[max(0, a - int(fs * 0.01)):b]
        pre = x[max(0, a - int(fs * pre_s)):max(0, a - int(fs * 0.005))]
        on = x[a:a + int(fs * pre_s)]
        if not len(seg):
            out.append({"level_db": -120.0, "peak_db": -120.0, "rise_db": 0.0, "sounds": False}); continue
        lvl = db(np.sqrt(np.mean(seg ** 2))); pk = db(np.max(np.abs(seg)))
        rise = db(np.sqrt(np.mean(on ** 2))) - (db(np.sqrt(np.mean(pre ** 2))) if len(pre) else -180.0) \
            if len(on) else 0.0
        out.append({"level_db": round(lvl, 1), "peak_db": round(pk, 1), "rise_db": round(min(rise, 99.0), 1),
                    "onset_shift_ms": round((t - t0) * 1000), "sounds": pk > HIT_FLOOR_DB and rise >= HIT_RISE_DB})
    return out


def _notes_snapshot(ns, path):
    d = ns["_load"](path); ev = ns["_events"](d)
    return {pid: sorted(tuple(r) for r in p["notes"]) for pid, p in ns["_patterns"](d, ev).items()}


def c_perform_undo(ctx):
    path, res = _new_song(ctx, "lofi", "check perform")
    _open(ctx, path)
    if not _fl_save(ctx, path):
        return _res("fail", "FL didn't save the test song before the perform", {}, "bridge transport save")
    before = _notes_snapshot(ctx.ns, path)
    ctx.call("flp_perform_setup", project=path, style="natural")
    p = ctx.call("flp_perform", project=path)
    ctx.call("flp_perform_undo", project=path)
    if not _fl_save(ctx, path):
        return _res("fail", "FL didn't save after the undo, so it couldn't be compared", {}, "bridge transport save")
    after = _notes_snapshot(ctx.ns, path)
    n = sum(len(v) for v in before.values())
    diff = [pid for pid in set(before) | set(after) if before.get(pid) != after.get(pid)]
    ev = {"notes": n, "patterns_differing": diff[:6], "perform_changed": (p.get("sections_changed") or [])[:6]}
    if diff:
        return _res("fail", f"After flp_perform + undo, FL's save differs from before in {len(diff)} pattern(s)", ev,
                    "flh_perform undo: compare the stored source (.perform_source.mid) with FL's save; slides / note "
                    "fields lost?")
    return _res("pass", f"flp_perform then undo leaves every one of {n} notes exactly as before (read from FL's save)",
                ev)


def c_template_folder(ctx):
    path, res = _new_song(ctx, "lofi", "check template", starter=False)
    stamp = STAMP_RE.sub("-", os.path.basename(os.path.normpath(ctx.sandbox)))[-24:]
    name = f"fl-headless check {stamp}"
    N = _mod("flh_newsong")
    tdir = N._fl_templates_dir()
    if not tdir:
        return _res("fail", "FL's user Templates folder wasn't found (no FL user data folder)", {},
                    "flh_newsong._fl_templates_dir / _fl_userdata: check flh_setup's user data path")
    state_dir = os.path.join(os.path.dirname(ctx.ns["STATE"]), "templates", name)
    made = []

    def undo():                                                   # safety net when the check stops half-way
        _take_away(ctx, made, "templates")
    ctx.cleanup(undo)
    r = ctx.call("flh_save_template", project=path, name=name, fl_templates=True)
    if os.path.isdir(state_dir):
        made.append(state_dir)
    dst = r.get("fl_template")
    if dst:
        made += [dst, os.path.dirname(dst)]                       # the .flp (recyclable), then its folder
    ev = {"fl_template": dst, "folder": tdir, "note": r.get("fl_template_note") or r.get("fl_template_error")}
    ok = bool(dst) and os.path.isfile(dst)
    inside = ok and os.path.normcase(os.path.abspath(dst)).startswith(os.path.normcase(os.path.abspath(tdir)))
    err = _parses(ctx.ns, dst) if ok else None
    left = _take_away(ctx, made, "templates")
    made.clear()
    ev["left_behind"] = left
    if not ok:
        return _res("fail", "flh_save_template couldn't put the template in FL's Templates folder", ev,
                    "flh_save_template fl_templates: writable() exception for <user data>\\Projects\\Templates")
    if not inside or err:
        return _res("fail", "The template landed outside FL's Templates folder or doesn't parse", ev,
                    "flh_newsong._fl_template_dest")
    tail = (f" Its folder is left with only fl-headless's marker file (fl-headless may only recycle .flp files there): "
            f"delete '{os.path.basename(left[0])}' in FL's Templates folder by hand." if left else "")
    return _res("pass", "FL's user Templates folder exists and is writable: a check template was saved there and "
                        "recycled again (whether File > New from template lists it is a look in FL)." + tail, ev,
                "files_recycle/writable: allow a Templates subfolder that holds the .fl-headless marker" if left
                else "")


# ============================================================================================== mix checks
def _plugin_song(ctx, label):
    """A silent sandbox song with Fruity Parametric EQ 2 (slot 0) and Fruity Limiter (slot 1) on its empty Vocals
    Bus insert, open in FL with the transport stopped -> (path, track) or raise Skip."""
    N = _mod("flh_newsong")
    peq = (N._find_plugin("Fruity Parametric EQ 2") or {}).get("default")
    lim = (N._find_plugin("Fruity Limiter") or {}).get("default")
    if not peq:
        raise Skip("Fruity Parametric EQ 2 isn't in FL's plugin database", how="refresh FL's plugin database in its "
                   "browser (Plugin database > Installed > Effects), then run again")
    path, res = _new_song(ctx, "trap", label, starter=False)
    _, named, _ = _parse_mixer_result(res)
    track = named.get("Vocals Bus") or max(named.values() or [1])
    ctx.call("flp_load_plugin", project=path, output=path, preset=peq, track=track, slot=0)
    if lim:
        ctx.call("flp_load_plugin", project=path, output=path, preset=lim, track=track, slot=1)
    _open(ctx, path)
    _stop(ctx)
    return path, track, bool(lim)


def _stop(ctx):
    ctx.ns["BRIDGE"].call("transport", action="stop")
    st = ctx.ns["BRIDGE"].call("state")
    if st.get("playing"):
        raise RuntimeError("FL still plays after stop: the knob checks never run while FL plays")


def _params(ctx, track, slot):
    r = ctx.call("fl_plugin_params", index=track, slot=slot)
    return r.get("plugin"), {p["param"]: p for p in (r.get("params") or [])}


class _Spy:
    """Records every plugin knob value fl-headless sends to FL while it is installed (a BRIDGE.call wrapper)."""
    def __init__(self, ns):
        self.B = ns["BRIDGE"]; self.sets = []; self.searches = []

    def __enter__(self):
        self._had = "call" in vars(self.B)
        self._orig = self.B.call
        orig = self._orig

        def spy(cmd, *a, **k):
            r = orig(cmd, *a, **k)
            if cmd == "plugin_set":
                self.sets.append((k.get("index"), k.get("slot"), k.get("param"), k.get("value"),
                                  r.get("text") if isinstance(r, dict) else None))
            elif cmd == "plugin_set_value":
                self.searches.append((k.get("index"), k.get("slot"), k.get("param"), k.get("target")))
            return r
        self.B.call = spy
        return self

    def __exit__(self, *exc):
        if self._had:
            self.B.call = self._orig
        else:
            vars(self.B).pop("call", None)                       # back to the class's own method
        return False


def _gain_params(plugin):
    """{param index: control key} of the map's gain-type controls (loud at their extremes)."""
    X = _mod("flh_mixeng")
    mp = X.plugin_map(plugin) or {}
    return {int(e["index"]): k for k, e in (mp.get("controls") or {}).items()
            if e.get("index") is not None and X.gain_type(k, e)}


def _restore_params(ctx, track, slot, before):
    for p, q in before.items():
        try:
            ctx.ns["BRIDGE"].call("plugin_set", index=track, slot=slot, param=p, value=float(q.get("value", 0.0)))
        except Exception:                                         # noqa: BLE001
            pass


def c_mix_knob_safety(ctx):
    path, track, has_lim = _plugin_song(ctx, "check knobs")
    X = _mod("flh_mixeng")
    plug, before = _params(ctx, track, 0)
    if not before:
        return _res("fail", "FL returned no parameters for the Parametric EQ 2 on the test insert", {"plugin": plug},
                    "bridge plugin_params (slot index / plugin not loaded from the .fst)")
    gains = _gain_params(plug or "Fruity Parametric EQ 2")
    want = {"band3_gain_db": -3.0, "band3_freq_hz": 1000.0}
    try:
        with _Spy(ctx.ns) as spy:
            r = ctx.call("fl_plugin_set_named", track=track, slot=0, values=want)
            r40 = None
            try:
                r40 = ctx.call("fl_plugin_set_named", track=track, slot=0, values={"band2_gain_db": 40})
            except Exception as e:                                # noqa: BLE001 - a refusal is fine
                r40 = {"refused": str(e)[:160]}
        _, after = _params(ctx, track, 0)
    finally:                                                      # inline, while this sandbox song is still open
        _restore_params(ctx, track, 0, before)
    mp = X.plugin_map(plug or "Fruity Parametric EQ 2") or {}
    ctl = mp.get("controls") or {}
    bad, got = [], {}
    for key, v in want.items():
        e = ctl.get(key) or {}
        idx = e.get("index")
        val, _u = X.parse_display((after.get(idx) or {}).get("text")) if idx is not None else (None, None)
        got[key] = (after.get(idx) or {}).get("text")
        tol = 0.3 if key.endswith("_db") else 0.05 * v
        if val is None or abs(val - v) > tol:
            bad.append(f"{key} reads {got[key]!r} (want {v:g})")
    top = lambda p, v: p in gains and v is not None and v >= 0.99 and (before.get(p) or {}).get("value", 0) < 0.99
    spikes = [(p, round(v, 3)) for (t, s_, p, v, _) in spy.sets if top(p, v)]
    searches = [p for (t, s_, p, _) in spy.searches if p in gains]
    at_max = [gains[p] for p, q in after.items() if top(p, q.get("value"))]
    b2 = ctl.get("band2_gain_db") or {}
    v40, _ = X.parse_display((after.get(b2.get("index")) or {}).get("text")) if b2.get("index") is not None else (None, None)
    ev = {"set": got, "ok": r.get("ok"), "plus40": (r40 or {}).get("notes") or (r40 or {}).get("refused") or v40,
          "knob_moves_sent": len(spy.sets), "gain_knob_at_max": spikes[:4] + at_max[:4], "display_searches": searches}
    if spikes or at_max or searches:
        return _res("fail", "A gain knob was sent to (or read at) its maximum while setting EQ values - this is the "
                            "loud-spike risk", ev,
                    "flh_mixeng.plugin_set_named: gain-type controls must use _set_local around the map's guess, never "
                    "the bridge's display search or the extremes")
    if v40 is not None and v40 > 12.5:
        bad.append(f"asking +40 dB left band 2 at {v40:+.1f} dB (should clamp to the safe maximum or refuse)")
    if bad:
        return _res("fail", "Named EQ settings didn't land: " + "; ".join(bad), ev,
                    "data/plugin_maps/fruity_parametric_eq_2.json (indices/curves) or run fl_plugin_map_learn")
    return _res("pass", "With FL stopped, named EQ values landed within tolerance (band 3 -3 dB at 1 kHz), +40 dB "
                        "was clamped/refused, and no gain knob ever went to its maximum", ev)


def c_mix_map_learn(ctx):
    path, track, _ = _plugin_song(ctx, "check map learn")
    X = _mod("flh_mixeng")
    plug, before = _params(ctx, track, 0)
    if not before:
        return _res("fail", "FL returned no parameters for the Parametric EQ 2", {"plugin": plug},
                    "bridge plugin_params")
    mp_path = os.path.join(X._user_map_dir(), X._slug(plug or "Fruity Parametric EQ 2") + ".json")
    old = None
    if os.path.isfile(mp_path):
        with open(mp_path, "rb") as f:
            old = f.read()

    def put_back():
        if old is not None:
            with open(mp_path, "wb") as f:
                f.write(old)
    ctx.cleanup(put_back)
    try:
        r = ctx.call("fl_plugin_map_learn", track=track, slot=0, background=False)
        if r.get("job") and not r.get("params_learned"):
            r = ctx.call("fl_job", job=r["job"], wait_s=300)
            r = r.get("result") or r
        _, after = _params(ctx, track, 0)
    except Exception:
        _restore_params(ctx, track, 0, before)                     # only on failure: a pass must not need it
        raise
    moved = [(q.get("name"), q.get("value"), (after.get(p) or {}).get("value")) for p, q in before.items()
             if not isinstance((after.get(p) or {}).get("value"), (int, float))
             or abs((after.get(p) or {}).get("value") - (q.get("value") or 0.0)) > 1e-4]
    ev = {"params_learned": r.get("params_learned"), "restored": r.get("restored"), "probe": r.get("probe"),
          "missing_named": (r.get("named_controls_missing") or [])[:6], "knobs_moved": moved[:4],
          "map_kept": None if old is not None else "the learned map stays (it's verified on this PC)"}
    if moved:
        _restore_params(ctx, track, 0, before)
        return _res("fail", f"After fl_plugin_map_learn {len(moved)} knob(s) didn't come back to their values", ev,
                    "flh_mixeng._learn restore: plugin_set with the value read before; check FL's value rounding")
    if not r.get("params_learned"):
        return _res("fail", "fl_plugin_map_learn learned no parameters", ev, "flh_mixeng._learn / bridge plugin_probe")
    note = f"; shipped names missing on this FL: {ev['missing_named']}" if ev["missing_named"] else ""
    return _res("warn" if ev["missing_named"] else "pass",
                f"fl_plugin_map_learn learned {r.get('params_learned')} parameters and every knob reads back exactly "
                f"as before{note}", ev,
                "correct data/plugin_maps/fruity_parametric_eq_2.json with the learned file" if note else "")


MAP_SLOTS = 7               # mapped effects per sandbox mixer track (FL has 10 slots; 2 tracks hold the 14 maps)
MAP_FIRST_SLOT = 2          # slots 2..8 of the song's own inserts


def c_mix_map_names(ctx):
    # PC-VERIFY: flp_load_plugin can put an effect on an empty insert above the song's own tracks; every mapped
    # effect shows its parameter names through plugin_params
    """Every stock effect that has a plugin map, loaded on 2 empty mixer tracks of a sandbox song: the map's FL
    names are checked against the live ones and corrected user maps are saved (fl_plugin_maps_check)."""
    N, X = _mod("flh_newsong"), _mod("flh_mixeng")
    names = sorted(X.plugin_maps())
    owned, not_owned = [], []
    for n in names:
        pre = (N._find_plugin(n) or {}).get("default")
        if pre and not N._fst_ok(pre):                     # FL 2026 lists some effects by name only: no state to
            pre = None                                     # load into a file (they're added live by hand)
        (owned if pre else not_owned).append((n, pre))
    if not owned:
        raise Skip("none of the mapped effects is in FL's plugin database", how="refresh FL's plugin database "
                   "(Plugin database > Installed > Effects), then run again")
    path, res = _new_song(ctx, "trap", "check map names", starter=False)
    ins, named, fx = _parse_mixer_result(res)
    # the song's own channel inserts: an empty track above them has no slot data in the file to load into
    # (PC 2026-10-21: 'mixer track 18 slot 0 not found'); their slots 2..8 are free in a new song
    used = {(t, s) for _p, t, s in fx}
    tracks = sorted(ins)[:-(-len(owned) // MAP_SLOTS)]
    placed = {}
    for i, (n, pre) in enumerate(owned[:len(tracks) * MAP_SLOTS]):
        t, s = tracks[i // MAP_SLOTS], MAP_FIRST_SLOT + i % MAP_SLOTS
        if (t, s) in used:
            continue
        ctx.call("flp_load_plugin", project=path, output=path, preset=pre, track=t, slot=s)
        placed[n] = (t, s)
    pops = _open(ctx, path)
    r = ctx.call("fl_plugin_maps_check", tracks=tracks, apply=True, full=True)
    rows = r.get("plugins") or []
    seen = {x.get("plugin") for x in rows}
    lost = [n for n in placed if n not in seen]
    unmatched = {x["plugin"]: [u["control"] for u in x.get("unmatched") or []] for x in rows if x.get("unmatched")}
    renamed = [f"{x['plugin']}: {y['control']} '{y['old']}' -> '{y['new']}'" for x in rows for y in x.get("renamed") or []]
    renamed += [f"{x['plugin']}: {y['control']} index {y['old']} -> {y['new']}" for x in rows
                for y in x.get("reindexed") or [] if y.get("old") is not None]           # a wrong index, not a new one
    new_idx = sum(1 for x in rows for y in x.get("reindexed") or [] if y.get("old") is None)
    ev = {"tracks": tracks, "loaded": len(placed), "checked": r.get("checked"), "renamed": renamed[:12],
          "unmatched": unmatched, "not_owned": [n for n, _ in not_owned], "not_seen_live": lost,
          "popups": pops[:3], "maps": len([x for x in rows if x.get("user_map")]),
          "live_unused": {x["plugin"]: (x.get("live_names_unused") or [])[:8] for x in rows if x.get("unmatched")}}
    tail = (f"; not in this edition (skipped): {', '.join(ev['not_owned'])}" if not_owned else "")
    if not rows or len(lost) == len(placed):
        return _res("fail", "The mapped effects loaded in the sandbox song, but none showed its parameters live", ev,
                    "flh_mixeng._effects_on (fl_plugin_params on the sandbox tracks) / flp_load_plugin track index")
    if lost or unmatched or renamed:
        bits = []
        if renamed:
            bits.append(f"{len(renamed)} control name(s) corrected in the user maps")
        if unmatched:
            bits.append(f"unmatched: {unmatched} (live names in evidence.live_unused)")
        if lost:
            bits.append(f"not seen live: {lost}")
        return _res("warn", f"Checked {len(rows)} plugin maps against FL's live names: " + "; ".join(bits) + tail, ev,
                    "commit the names back: python3 tools/plugin_maps_names.py --write (in the repo, with this PC's "
                    "state folder plugin_maps); fix unmatched controls in data/plugin_maps by hand")
    idx = (f"; {new_idx} index(es) the maps lacked were recorded (tools/plugin_maps_names.py --write ships them)"
           if new_idx else "")
    return _res("pass", f"Every control of {len(rows)} plugin maps matches FL's live parameter names{idx}{tail}", ev)


def c_mix_sends(ctx):
    path, res = _new_song(ctx, "lofi", "check sends", starter=False)
    ins, named, _ = _parse_mixer_result(res)
    src = next((t for t, (n, _b) in sorted(ins.items()) if n.lower() in ("keys", "pad", "guitar")), min(ins or [1]))
    dst = named.get("Reverb")
    if dst is None:
        raise Skip("the lofi recipe made no Reverb send track", how="check data/newsong recipes")
    _open(ctx, path)
    _stop(ctx)
    r = ctx.call("fl_mixer_set", track=src, send_db={str(dst): -6.0})
    info = ctx.call("fl_mixer_info", tracks=str(src))
    tr = next((t for t in info.get("tracks") or [] if t.get("index") == src), {})
    sends = tr.get("sends")
    ev = {"from": src, "to": dst, "sends": sends, "set_reply": (r.get("sends") if isinstance(r, dict) else None)}
    if sends is None:
        return _res("warn", "FL's mixer_info didn't report sends: update the bridge script to read them back", ev,
                    "fl_script mixer_info sends (needs tracks named)")
    s = next((x for x in sends if x.get("to") == dst), None)
    if not s:
        return _res("fail", f"The send from insert {src} to the Reverb ({dst}) isn't there after setting it", ev,
                    "bridge mixer_set send_levels: setRouteTo before setRouteToLevel")
    if s.get("level") is None:
        return _res("warn", "The send exists but this FL doesn't report send levels (getRouteToLevel, FL 2024+)", ev,
                    "")
    db = ctx.ns["_fader_db"](float(s["level"]))
    ev["read_db"] = round(db, 2)
    if abs(db + 6.0) > 1.0:
        return _res("fail", f"Send level read back {db:+.1f} dB (set -6 dB)", ev,
                    "_send_levels_arg: send knobs may not follow the fader curve (PC-VERIFY in _send_levels_arg)")
    return _res("pass", f"Send from insert {src} to the Reverb set to -6 dB and read back at {db:+.1f} dB", ev)


def _mutes(ctx):
    info = ctx.call("fl_mixer_info")
    return {t.get("index"): bool(t.get("muted")) for t in info.get("tracks") or []}


def c_mix_engineer(ctx):
    path, res = _new_song(ctx, "lofi", "check mix engineer")
    _open(ctx, path)
    sec = (_journal(path).get("sections") or [{}])
    a = float(next((s.get("bar") for s in sec if (s.get("name") or "").lower() not in ("intro",)), 1) or 1)
    r = ctx.call("fl_mix_engineer", project=path, apply=False, start_bar=a, end_bar=a + 2, background=False)
    if r.get("job") and "proposals" not in r:
        r = ctx.call("fl_job", job=r["job"], wait_s=240)
        r = r.get("result") or r
    ev = {"proposals": len(r.get("proposals") or []), "applied": r.get("applied"), "measure_s":
          (r.get("steps_s") or {}).get("measure"), "genre": r.get("genre")}
    if "proposals" not in r:
        return _res("fail", "fl_mix_engineer on live measurements returned no proposal list", ev,
                    "flh_mixeng live measuring (fl_mix_check)")
    if r.get("applied"):
        return _res("fail", "fl_mix_engineer applied moves although apply=false", ev, "flh_mixeng apply gate")
    return _res("pass", f"fl_mix_engineer measured the playing song and proposed {ev['proposals']} move(s) without "
                        "applying anything", ev)


def c_mix_masking(ctx):
    path, res = _new_song(ctx, "lofi", "check masking")
    _open(ctx, path)
    before = _mutes(ctx)
    sec = (_journal(path).get("sections") or [{}])
    a = float(next((s.get("bar") for s in sec if (s.get("name") or "").lower() not in ("intro",)), 1) or 1)
    r = ctx.call("fl_masking", live=True, project=path, start_bar=a, end_bar=a + 1, background=False)
    if r.get("job") and "findings" not in r:
        r = ctx.call("fl_job", job=r["job"], wait_s=300)
        r = r.get("result") or r
    after = _mutes(ctx)
    changed = [t for t in set(before) | set(after) if before.get(t) != after.get(t)]
    ev = {"findings": len(r.get("findings") or []), "note": r.get("note"), "mutes_changed": changed[:8]}
    if changed:
        return _res("fail", f"After live masking, mute states differ on tracks {changed[:6]}", ev,
                    "flh_mixeng live masking: restore every insert's mute in a finally block")
    return _res("pass", f"Live masking ran ({ev['findings']} finding(s)) and every mute is back as before", ev)


def c_mix_platform(ctx):
    path, res = _new_song(ctx, "house", "check platform", arrange=False)
    _open(ctx, path)
    wav = _sb(ctx, "audio", "platform preview.flac")
    ln = _listen(ctx, 1, 3, save=wav)
    if not _heard(ln):
        return _res("fail", "The house test song made no sound to measure", {}, "fl_listen / FL output device")
    r = ctx.call("fl_platform_preview", audio=ln.get("saved") or wav)
    names = [str(p.get("name") or p.get("platform") or "") for p in (r.get("platforms") or [])] or \
        [k for k in r if k[:1].isupper()]
    lufs = r.get("lufs") if r.get("lufs") is not None else (r.get("measured") or {}).get("lufs")
    want = (ln.get("whole") or {}).get("lufs")
    ev = {"platforms": names[:8], "lufs": lufs, "listen_lufs": want}
    if "spotify" not in str(r).lower():
        return _res("fail", "fl_platform_preview didn't list the streaming platforms", ev, "data/platforms.json")
    if lufs is not None and want is not None and abs(float(lufs) - float(want)) > 1.0:
        return _res("warn", f"Platform preview measured {lufs} LUFS, fl_listen {want}: more than 1 LU apart", ev,
                    "flh_mixeng.platform_preview vs audio_analyze loudness meters")
    return _res("pass", "fl_platform_preview read a short capture of the song and lists the platforms", ev)


# ============================================================================================== finalize / review
def _fl_restarted(ctx):
    """The command-line export closed FL and started it again on purpose: tell the runner (a new FL process is not a
    crash). Uses ctx.fl_restarted() when the runner offers it, else refreshes ctx.pids."""
    try:
        if callable(getattr(ctx, "fl_restarted", None)):
            ctx.fl_restarted()
        elif hasattr(ctx, "pids"):
            ctx.pids = list(ctx.ns["_fl_pids"]() or [])
    except Exception:                                             # noqa: BLE001
        pass


def _tiny_song(ctx):
    c = _cache(ctx)
    if c.get("tiny") and os.path.isfile(c["tiny"]):
        return c["tiny"]
    path, res = _new_song(ctx, "house", "check final", arrange=False)
    c["tiny"] = path
    return path


def c_finalize(ctx):
    st = ctx.call("fl_status")
    method = "in_fl" if st.get("render_in_fl") else None
    if not method:
        _ask(ctx, "fl_finalize needs FL's own export, which isn't enabled on this PC yet (the core render check "
                  "turns it on). The other way closes FL (your song is saved by FL first) and reopens it. Answer "
                  "'done' to allow FL to close for one tiny test render, or 'skip'")
        method = "cli"
    path = _tiny_song(ctx)
    _open(ctx, path)
    core = sys.modules.get("flh_pccheck")
    keep_in = core.sandbox_profile(ctx.sandbox) if core is not None and getattr(core, "H", None) is not None \
        else contextlib.nullcontext()                             # the run's sandbox profile (already active in a run):
    try:                                                          # the final never goes to the collection's folder
        with keep_in:
            r = ctx.call("fl_finalize", project=path, background=False, method=method)
    finally:
        if method == "cli":
            _fl_restarted(ctx)
    if r.get("job") and "finals" not in r:
        r = ctx.call("fl_job", job=r["job"], wait_s=600)
        r = r.get("result") or r
    fins = r.get("finals") or []
    f = fins[0] if fins and isinstance(fins[0], dict) else {}
    out = f.get("path")
    ev = {"method": r.get("render_method") or method, "final": os.path.basename(out or "") or None,
          "lufs": f.get("lufs"), "true_peak": f.get("true_peak"), "target": f.get("target"), "ok": r.get("ok")}
    if not out or not os.path.isfile(out) or f.get("error"):
        return _res("fail", f"fl_finalize made no final: {f.get('error') or r.get('error') or 'no file'}", ev,
                    "flh_finalize render step (render_method in evidence)")
    if not _inside_dir(out, ctx.sandbox):                         # a test final in the user's folders: a real bug
        return _res("fail", f"fl_finalize filed the test song's final outside the sandbox: {out}", dict(ev, path=out),
                    "flh_finalize _plan: the final folder must follow the song's own profile (flh_store.profile_for),"
                    " and pc_check's sandbox profile")
    _cache(ctx)["final"] = out
    try:                                                          # measured here, not just as the tool reports
        import numpy as np
        x, fs, bits = _mod("flh_review").load_audio(out)
        x = np.asarray(x, dtype=float)
        x2 = x if x.ndim == 2 else x[:, None]
        lufs = _mod("flh_review").integrated_lufs(x2, fs)
        tp = _mod("flh_finalize").true_peak_db(x2)
    except Exception as e:                                        # noqa: BLE001
        return _res("fail", f"The final file isn't readable: {e}", ev, "flh_finalize encoder / flac_lite")
    tg = f.get("target") or {"lufs": -14.0, "true_peak": -1.0}
    ev.update(seconds=round(len(x2) / fs, 1), bits=bits, measured_lufs=None if lufs is None else round(lufs, 2),
              measured_tp=round(float(tp), 2))
    bad = []
    if lufs is None or abs(float(lufs) - float(tg.get("lufs", -14))) > 1.0:
        bad.append(f"loudness {ev['measured_lufs']} LUFS measured (target {tg.get('lufs')})")
    if float(tp) > float(tg.get("true_peak", -1.0)) + 0.1:
        bad.append(f"true peak {ev['measured_tp']} dBTP measured (limit {tg.get('true_peak')})")
    if bad:
        return _res("fail", "The final is off target: " + "; ".join(bad), ev, "flh_finalize gain/limiter + verify")
    return _res("pass", f"fl_finalize ({ev['method']}) wrote a valid final; measured {ev['measured_lufs']} LUFS / "
                        f"{ev['measured_tp']} dBTP (on target)", ev)


def c_review(ctx):
    path = _tiny_song(ctx)
    audio = _cache(ctx).get("final")
    how = "the final from the finalize check"
    if not audio or not os.path.isfile(audio):
        _open(ctx, path)
        wav = _sb(ctx, "audio", "review capture.flac")
        ln = _listen(ctx, 1, 5, save=wav)
        if not _heard(ln):
            return _res("fail", "The test song made no sound to review", {}, "fl_listen / FL output device")
        audio, how = ln.get("saved") or wav, "a capture of the song playing in FL"
    r = ctx.call("fl_review", project=path, audio=audio, background=False)
    if r.get("job") and "issues" not in r:
        r = ctx.call("fl_job", job=r["job"], wait_s=240)
        r = r.get("result") or r
    ev = {"audio": how, "issues": len(r.get("issues") or []), "sections": len(r.get("sections") or [])}
    if r.get("issues") is None or not r.get("sections"):
        return _res("fail", "fl_review didn't return sections and an issue list", ev, "flh_review")
    return _res("pass", f"fl_review ran on {how}: {ev['sections']} sections, {ev['issues']} issue(s) listed", ev)


def c_access(ctx):
    path, res = _new_song(ctx, "lofi", "check describe")
    d = ctx.call("fl_describe", project=path)
    txt = d.get("text") or ""
    v = ctx.call("fl_visualize", kind="arrangement", project=path)
    a = ctx.call("fl_access")
    art = bool(re.search(r"[█▇▆▅▄▃▂▁░▒▓]{4,}|#{8,}|={10,}", txt))
    ev = {"describe_chars": len(txt), "bpm_named": "BPM" in txt, "alt_chars": len(v.get("alt") or ""),
          "access_options": sorted((a.get("options") or {}))[:7]}
    bad = []
    if len(txt) < 100 or "BPM" not in txt:
        bad.append("fl_describe text too short or without the tempo")
    if art:
        bad.append("fl_describe has ASCII art (screen readers)")
    if len(v.get("alt") or "") < 40:
        bad.append("fl_visualize gave no alt text")
    if not a.get("options"):
        bad.append("fl_access lists no options")
    if bad:
        return _res("fail", "; ".join(bad), ev, "flh_access")
    return _res("pass", "fl_describe, fl_visualize (with alt text) and fl_access work on a sandbox song", ev)


# ============================================================================================== vocal
def _vocal_song(ctx):
    path, res = _new_song(ctx, "lofi", "check vocal", starter=False)
    _open(ctx, path)
    return path


def c_vocal_setup(ctx):
    path = _vocal_song(ctx)
    r = ctx.call("fl_vocal_setup", project=path)
    t = r.get("track")
    info = ctx.call("fl_mixer_info", tracks=str(t)) if t is not None else {}
    tr = next((x for x in info.get("tracks") or [] if x.get("index") == t), {})
    au = r.get("audio") or {}
    ev = {"track": t, "picked": r.get("picked"), "armed": r.get("armed"), "read_armed": tr.get("armed"),
          "name": tr.get("name"), "device": au.get("device"), "buffer": au.get("buffer") or au.get("buffer_samples"),
          "rate": au.get("sample_rate"), "audio_readable": au.get("readable", bool(au.get("device")))}
    bad = []
    if t is None:
        bad.append("no insert picked")
    if tr and tr.get("name") != (r.get("mixer") or {}).get("name", "Vocal"):
        bad.append(f"the insert reads back as {tr.get('name')!r}")
    if r.get("armed") and tr and tr.get("armed") is False:
        bad.append("said armed but FL's insert isn't armed")
    if bad:
        return _res("fail", "fl_vocal_setup: " + "; ".join(bad), ev, "flh_vocal setup / bridge mixer_arm")
    if not r.get("armed"):
        return _res("warn", "fl_vocal_setup picked and named the insert but couldn't arm it (old bridge script?)", ev,
                    "fl_bridge_setup(action='update_script'); bridge mixer_arm")
    if not ev["audio_readable"] or not ev["device"]:
        return _res("warn", "Vocal insert set up and armed, but FL's audio device/buffer couldn't be read from the "
                            "registry", ev, "flh_vocal.audio_device_report value names (PC-VERIFY vocal.md 1.2)")
    return _res("pass", f"fl_vocal_setup picked insert {t}, named it, FL reads it armed; audio device "
                        f"{ev['device']} buffer {ev['buffer']} read", ev)


def c_vocal_level(ctx):
    _person_first(ctx, "run fl_pc_check with someone at the PC and a mic connected: the check asks them to speak "
                       "for 5 seconds")
    path = _vocal_song(ctx)
    s = ctx.call("fl_vocal_setup", project=path)
    t = s.get("track")
    _ask(ctx, f"Vocal level check: in FL's mixer select insert {t} ('Vocal') and pick your mic input at the top right. "
              "Then answer 'done' and right away speak or sing at your normal loudest for about 10 seconds (fl-headless "
              "listens for 5)")
    r = ctx.call("fl_vocal_level_check", seconds=5, track=t, project=path)
    ev = {"verdict": r.get("verdict"), "peak_db": r.get("peak_db") or r.get("peak_dbfs"), "method": r.get("method")}
    if str(r.get("verdict") or "").lower() in ("no signal", ""):
        return _res("fail", "The level check heard no signal from the mic on the vocal insert", ev,
                    "flh_vocal level: bridge mixer_peaks (getTrackPeaks while stopped, PC-VERIFY vocal.md 2.1)")
    return _res("pass", f"The mic was heard on the vocal insert: verdict '{r.get('verdict')}'", ev)


def c_vocal_record(ctx):
    _person_first(ctx, "run fl_pc_check with someone at the PC and a mic connected: FL records 2 bars into a sandbox "
                       "song")
    path = _vocal_song(ctx)
    s = ctx.call("fl_vocal_setup", project=path)
    t = s.get("track")
    _ask(ctx, f"Vocal recording: set your mic input on mixer insert {t} ('Vocal') at the top right of FL's mixer. "
              "Answer 'done': FL then records 2 bars (after a 1-bar pre-roll) of the sandbox song - make some sound "
              "into the mic while it records")
    t_rec = time.time()
    r = ctx.call("fl_vocal_record", start_bar=2, end_bar=4, takes=1, track=t, project=path, background=False)
    if r.get("job") and "takes" not in r:
        r = ctx.call("fl_job", job=r["job"], wait_s=120)
        r = r.get("result") or r
    rec_s = round(time.time() - t_rec, 1)
    takes = r.get("takes") or []
    p = (takes[0] or {}).get("path") if takes else None
    dirs = []
    try:
        dirs = [os.path.normcase(os.path.abspath(d)) for d in _mod("flh_vocal")._recorded_dirs(path)]
    except Exception:                                             # noqa: BLE001
        pass
    in_rec = bool(p) and any(os.path.normcase(os.path.abspath(p)).startswith(d) for d in dirs)
    ev = {"takes": len(takes), "file": os.path.basename(p or "") or None, "in_recorded_folder": in_rec,
          "warnings": (r.get("warnings") or [])[:3], "punch": r.get("punch"),
          # where the time went (PC 2026-10-23: 620 s for a 2-bar take): the tool, its steps, the person's wait
          "record_s": rec_s, "steps_s": r.get("steps_s"), "passes": r.get("passes"),
          "person_wait_s": round(getattr(ctx, "_person_s", 0.0) or 0.0, 1)}
    if r.get("edison"):
        ev["edison"] = r["edison"]
        if r.get("edison_loaded_by_fl"):
            ev["edison_loaded_by_fl"] = True
    if not p or not os.path.isfile(p):
        if r.get("edison"):
            return dict(_res("fail", "FL's recording went into Edison (FL's record button menu is set to record into "
                                     "Edison): no take file reached the playlist or FL's Recorded folder", ev,
                             "fl_vocal_record: FL's record mode (not a fl-headless setting)"),
                        next_step=(r.get("next_step") or "Right-click FL's Record button and pick 'Audio, into the "
                                   "playlist as an audio clip', remove Edison from the Vocal insert, and record again")
                        + "; then fl_pc_check(areas=['vocal.record_take'])")
        return _res("fail", "No recorded take file was found after recording 2 bars", ev,
                    "flh_vocal._recorded_dirs (PC-VERIFY where FL writes recordings) / FLH_RECORDED_DIR")
    if (takes[0] or {}).get("source") == "edison":
        return dict(_res("warn", f"FL recorded into Edison; fl-headless saved the take from Edison's Save As "
                                 f"({os.path.basename(p)})", ev, "fl_vocal_record: FL's record mode"),
                    next_step="right-click FL's Record button, pick 'Audio, into the playlist as an audio clip' so "
                              "takes land in the playlist")
    if not in_rec:
        return _res("warn", f"The take was found ({os.path.basename(p)}) but outside the folders fl-headless expects",
                    dict(ev, folder=os.path.dirname(p)), "add that folder to flh_vocal._recorded_dirs")
    return _res("pass", f"A 2-bar take was recorded and found in FL's Recorded folder ({os.path.basename(p)}; FL keeps "
                        "it there - delete it by hand if you like)", ev)


# ============================================================================================== midi
def c_midi_probe(ctx):
    path, res = _new_song(ctx, "lofi", "check capture", starter=False)
    _open(ctx, path)
    r = ctx.call("fl_capture_probe")
    ch = r.get("checks") or {}
    kb = ch.get("keyboards")
    ev = {"bridge": ch.get("bridge"), "project_saved": ch.get("project_saved"), "keyboards": kb,
          "popups": ch.get("popups"), "countdown_control": ch.get("countdown_control"),
          "loop_record": ch.get("loop_record"), "ready": r.get("ready")}
    if not ch.get("bridge") or ch.get("popups"):
        return _res("fail", "fl_capture_probe: the bridge didn't answer or FL shows a popup", ev,
                    "flh_transcribe capture probe")
    if not kb:
        return _res("warn", "Capture is ready, but no MIDI keyboard is connected (or FL doesn't list it)", ev,
                    "PC-VERIFY transcribe.md 0: checks.keyboards from FL's MIDI inputs")
    return _res("pass", f"fl_capture_probe sees the bridge, the saved sandbox song and keyboard(s) {kb}", ev)


def c_midi_capture(ctx):
    _person_first(ctx, "run fl_pc_check with someone at the PC and a MIDI keyboard connected: they play 2 bars")
    path, res = _new_song(ctx, "lofi", "check capture idea", starter=False)
    _open(ctx, path)
    _ask(ctx, "MIDI capture: answer 'done', then after FL's count-in (a few seconds) play any notes on your MIDI "
              "keyboard for about 10 seconds; 2 bars are recorded into a sandbox song")
    r = ctx.call("fl_capture_idea", bars=2)
    n = r.get("notes")
    n = len(n) if isinstance(n, list) else int(n or 0)
    ev = {"notes": n, "pattern": r.get("pattern_name") or r.get("pattern"), "restored": r.get("restored"),
          "why": r.get("nothing_recorded")}
    if not n:
        return _res("fail", "No notes arrived from the keyboard during the 2-bar capture", ev,
                    "flh_transcribe capture: FL's MIDI input enabled? record filter Notes? (transcribe.md 1)")
    return _res("pass", f"Captured {n} notes into '{ev['pattern']}' and FL's settings were restored", ev)


# ============================================================================================== handbook
def c_handbook_fetch(ctx):
    B = _mod("flh_handbook")
    home0 = B.HOME
    hb = os.path.join(ctx.sandbox, "handbook")                    # the check's own index, never the user's
    ctx.ns["writable"](os.path.join(hb, "x"))
    os.makedirs(hb, exist_ok=True)

    def back():
        B.HOME = home0
    ctx.cleanup(back)
    B.HOME = hb
    try:
        r1 = ctx.call("flh_handbook_build", max_pages=5, source="online", background=False)
        if r1.get("job"):
            r1 = (ctx.call("fl_job", job=r1["job"], wait_s=180).get("result") or {})
        r2 = ctx.call("flh_handbook_build", max_pages=5, source="online", refresh=True, background=False)
        if r2.get("job"):
            r2 = (ctx.call("fl_job", job=r2["job"], wait_s=180).get("result") or {})
        man = B._read_json(os.path.join(hb, r1.get("index") or "online", "manifest.json")) or {}
        titles = " ".join(str(v.get("title") or "") for v in (man.get("pages") or {}).values() if v.get("file"))
        q = (re.findall(r"[A-Za-z]{4,}", titles) or ["mixer"])[0]
        s = ctx.call("flh_handbook_search", query=q, k=8)
    finally:
        back()
    hits = s.get("results") or s.get("hits") or []
    manual = [h for h in hits if "manual" in str(h.get("src", "")).lower()] if isinstance(s.get("manual"), dict) else []
    ev = {"fetched": r1.get("fetched"), "failed": r1.get("failed"), "robots_blocked": r1.get("robots_blocked"),
          "requests": r1.get("requests"), "refresh_not_modified": r2.get("not_modified"),
          "refresh_fetched": r2.get("fetched"), "query": q, "hits": len(hits), "error": r1.get("error") or r2.get("error")}
    if r1.get("error") or not (r1.get("fetched") or 0):
        return _res("fail", f"Fetching 5 manual pages from image-line.com failed: {r1.get('error') or 'nothing fetched'}",
                    ev, "flh_handbook Fetcher / data/handbook/sources.json (TOC at Index_Frame_Left.html)")
    if r1.get("robots_blocked"):
        return _res("warn", "robots.txt blocked some manual pages", ev, "re-check robots.txt rules")
    if not manual:
        return _res("fail", f"The small index didn't answer a search for '{q}'", ev, "flh_handbook build_index/search")
    if not (r2.get("not_modified") or 0):
        return _res("warn", "Image-Line's server ignored ETag/Last-Modified on refetch (no 304s): full builds "
                            "re-download every page", ev, "handbook.md 4: report it; the build still works")
    return _res("pass", f"Fetched {r1.get('fetched')} manual pages politely, refetch got {r2.get('not_modified')} "
                        f"'not modified' answers, and search found '{q}'", ev)


# ============================================================================================== catalog
def c_catalog(ctx):
    r = ctx.call("fl_sound_catalog", refresh=True)
    src = r.get("sources") or {}
    smp = r.get("samples") or {}
    ed = (None, None)
    try:
        ed = _mod("flh_catalog").fl_edition()
    except Exception:                                             # noqa: BLE001
        pass
    stock_roots = (src.get("stock") or {}).get("roots") or []
    ev = {"plugin_db": stock_roots[:2], "stock_sounds": (r.get("by_source") or {}).get("stock"),
          "samples": smp.get("count"), "sample_roots": (smp.get("folders") or [])[:3],
          "LABS": (src.get("LABS") or {}).get("found"), "FLEX": (src.get("FLEX") or {}).get("found"),
          "edition": ed[0], "edition_from": ed[1]}
    bad = []
    if not (src.get("stock") or {}).get("found") or not any("plugin database" in str(x).lower() for x in stock_roots):
        bad.append("FL's plugin database wasn't found")
    if not smp.get("count"):
        bad.append("no factory samples found")
    if bad:
        return _res("fail", "fl_sound_catalog: " + "; ".join(bad), ev,
                    "flh_catalog roots: plugin database under the FL user data folder; factory packs under "
                    "<FL>\\Data\\Patches\\Packs (PC-VERIFY newsong.md 1)")
    warn = []
    if not ed[0]:
        warn.append("the FL edition isn't known (flh_setup(action='apply', fl_edition=...))")
    msg = (f"Catalog found FL's plugin database, {smp.get('count')} factory samples, LABS "
           f"{'yes' if ev['LABS'] else 'no'}, FLEX {'yes' if ev['FLEX'] else 'no'}, edition {ed[0] or 'unknown'}")
    return _res("warn" if warn else "pass", msg + ("; " + "; ".join(warn) if warn else ""), ev,
                "flh_setup edition question" if warn else "")


def c_icon_probe(ctx):
    out = _sb(ctx, "icon probe.flp")
    r = ctx.call("flh_icon_probe", output=out, start=0, count=40)
    pops = _open(ctx, out)
    snap = None
    try:
        snap = ctx.snapshot("icon probe: channel rack (name the icons you recognise)")
    except Exception:                                             # noqa: BLE001
        pass
    ev = {"project": os.path.basename(out), "channels": r.get("channels") or r.get("count"), "popups":
          [p.get("title") for p in pops], "snapshot": snap}
    if pops:
        return _res("fail", "FL showed a popup opening the icon probe project", ev, "flh_newsong icon probe writer")
    return _res("pass", "The icon probe project opens in FL. Optional by hand: look at the channel rack "
                        + ("snapshot" if snap else "(open the sandbox's 'icon probe.flp')") + " and write the icon "
                        "numbers you can name into data/templates/_icons.json", ev)


# ============================================================================================== the list
# ============================================================================= overnight additions (live, FL stays open)
def _chan_mutes(ctx):
    st = ctx.call("fl_state")
    return {int(c.get("index", -1)): bool(c.get("muted")) for c in (st.get("channels") or [])
            if int(c.get("index", -1)) >= 0}


def _in_fl_ok(ctx):
    if not ctx.call("fl_status").get("render_in_fl"):
        raise Skip("FL's own export isn't confirmed on this PC yet", how="run the render.in_fl check first")


def _job_result(ctx, r, key, wait_s=600):
    """A job's result: fl_job answers at most every ~40 s, so ask again until the job ends (PC 2026-10-21: the stems
    check read a still-running job as 'method None')."""
    if r.get("job") and key not in r:
        job, t_end = r["job"], time.time() + wait_s
        while True:
            j = ctx.call("fl_job", job=job, wait_s=min(40, max(1, t_end - time.time())))
            if j.get("status") not in ("running", "queued") or time.time() >= t_end:
                break
        r = j.get("result") or j
    return r


def c_preview_mutes(ctx):
    path, res = _new_song(ctx, "lofi", "check preview")
    _open(ctx, path)
    ch = _role_channel(path, "kick")
    if ch is None:
        ch = _role_channel(path, "bass")
    if ch is None:
        raise Skip("the test song has no kick or bass channel")
    before = _chan_mutes(ctx)
    others = sorted(i for i in before if i != ch)
    r = ctx.call("fl_preview", channels=[ch])
    rest = ctx.call("fl_preview", channels=others) if others else {}
    after = _chan_mutes(ctx)
    changed = sorted(i for i in set(before) | set(after) if before.get(i) != after.get(i))
    ev = {"channel": ch, "lufs": r.get("lufs"), "centroid_hz": r.get("centroid_hz"), "silent": r.get("silent"),
          "rest_lufs": rest.get("lufs"), "rest_centroid_hz": rest.get("centroid_hz"),
          "render_s": r.get("seconds_render"), "mutes_changed": changed}
    if changed:
        return _res("fail", f"After fl_preview, channel mutes differ on {changed[:6]}", ev,
                    "fl_preview: restore every live mute in its finally block")
    if r.get("lufs") is None or r.get("silent"):
        return _res("fail", f"fl_preview of channel {ch} came back silent: FL's export may ignore live mutes or the "
                            "channel doesn't play", ev, "fl_preview / _render_in_fl (checklists/core.md)")
    full = ctx.call("fl_preview")
    ev["full_lufs"] = full.get("lufs"); ev["full_centroid_hz"] = full.get("centroid_hz")
    # mutes ignored = the same whole mix three times (same level AND tone); one loud channel alone can measure
    # like the song, so the test is that the channel and the rest differ from each other (PC 2026-10-21)
    def same(a, b):
        return (a.get("lufs") is not None and b.get("lufs") is not None and abs(a["lufs"] - b["lufs"]) < 0.3 and
                abs((a.get("centroid_hz") or 0) - (b.get("centroid_hz") or 0)) <= 0.03 * max(1, b.get("centroid_hz") or 1))
    if rest and same(r, rest) and same(r, full):
        return _res("fail", f"Channel {ch} alone, the other channels and the whole song all measure the same: FL's "
                            "export ignored the live mutes", ev, "fl_preview muting (fl_channel_set) vs FL export")
    try:
        ctx.ns["_state"]({"in_fl_mutes_ok": True})
    except Exception:                                             # noqa: BLE001
        pass
    return _res("pass", f"fl_preview rendered channel {ch} alone in the open FL ({ev['lufs']} LUFS, "
                        f"{ev['centroid_hz']} Hz; the rest {ev['rest_lufs']} LUFS, {ev['rest_centroid_hz']} Hz) and put "
                        "every mute back", ev)


def c_stems_in_fl(ctx):
    _in_fl_ok(ctx)
    path, res = _new_song(ctx, "lofi", "check stems")
    _open(ctx, path)
    before = _chan_mutes(ctx)
    out = _sb(ctx, "stems", "x")[:-2]
    r = _job_result(ctx, ctx.call("fl_render_stems", project=path, output_dir=out, groups="roles",
                                  method="in_fl"), "stems")
    after = _chan_mutes(ctx)
    changed = sorted(i for i in set(before) | set(after) if before.get(i) != after.get(i))
    lv = r.get("levels") or []
    ev = {"method": r.get("method"), "stems": [x.get("stem") for x in lv], "silent": r.get("silent"),
          "lufs": {x.get("stem"): x.get("lufs") for x in lv}, "mutes_changed": changed,
          "fl_running": bool(ctx.call("fl_status").get("fl_running"))}
    if r.get("method") != "in_fl" or not ev["fl_running"]:
        ev["error"] = str(r.get("error") or "")[:300] or None
        return _res("fail", f"Stems didn't render in the open FL (method {r.get('method')}, fallback "
                            f"{r.get('fallback')})" + (f": {ev['error']}" if ev["error"] else ""), ev,
                    "_render_stems in_fl route")
    if changed:
        return _res("fail", f"After the stems, channel mutes differ on {changed[:6]}", ev,
                    "_render_stems: in_fl() must restore mutes in its finally block")
    loud = [x for x in lv if x.get("lufs") is not None and not x.get("silent")]
    if len(lv) < 2 or len(loud) < len(lv):
        return _res("fail", f"{len(lv)} stem(s), {len(lv) - len(loud)} silent: FL's export may not follow live "
                            "mutes", ev, "_render_stems in_fl route / fl_channel_set muted")
    import math
    mix = ctx.call("fl_preview").get("lufs")                     # the whole song, same export
    total = 10 * math.log10(sum(10 ** (x["lufs"] / 10) for x in loud))
    ev.update(mix_lufs=mix, stems_power_sum_lufs=round(total, 2))
    # disjoint stems add up to about the mix; stems that each hold the whole mix add up to mix + 10 log10(N)
    if mix is not None and total - mix > 10 * math.log10(len(loud)) - 1.0:
        return _res("fail", f"The stems add up to {total - mix:+.1f} dB over the mix: each one probably holds the "
                            "whole song (FL's export ignored the live mutes)", ev, "_render_stems in_fl route")
    try:
        ctx.ns["_state"]({"in_fl_mutes_ok": True})                # from now on stems stay in the open FL (auto)
    except Exception:                                             # noqa: BLE001
        pass
    return _res("pass", f"{len(lv)} stems rendered in the open FL ({', '.join(ev['stems'])}), each different, FL "
                        "stayed open and every mute is back; stems use this route from now on", ev)


def c_flac_export_read(ctx):
    _in_fl_ok(ctx)
    path = _tiny_song(ctx)
    _open(ctx, path)
    out = os.path.dirname(_sb(ctx, "flac", "x"))
    r = ctx.call("fl_render", project=path, output_dir=out, formats="flac", method="in_fl")
    r = _job_result(ctx, r, "rendered")
    f = next((x for x in (r.get("rendered") or []) if str(x).lower().endswith(".flac")), None)
    if not f or not os.path.isfile(f):
        return _res("fail", f"FL's FLAC export made no file ({str(r)[:160]})", {}, "fl_render in_fl flac")
    R = _mod("flh_review")
    real_ext = R._flac_external

    def _no_ext(p):
        raise RuntimeError("external decoder used")
    R._flac_external = _no_ext                                    # FL's own FLAC must decode without flac/ffmpeg
    try:
        import hashlib
        import numpy as np
        x, fs, bits = R.flac_load(f)
        stored = open(f, "rb").read()[26:42]
        v = np.round(x * (1 << (bits - 1))).astype("<i4")
        raw = np.ascontiguousarray(v.reshape(-1)).view(np.uint8).reshape(-1, 4)[:, :(bits + 7) // 8].tobytes()
        md5 = "none stored" if stored == bytes(16) else ("match" if hashlib.md5(raw).digest() == stored else "DIFFERS")
    except Exception as e:                                        # noqa: BLE001
        return _res("fail", f"FL's FLAC export can't be read by the built-in decoder: {e}",
                    {"file": os.path.basename(f)}, "flh_review FLAC decoder (LPC path)")
    finally:
        R._flac_external = real_ext
    ev = {"seconds": round(len(x) / fs, 1), "rate": fs, "bits": bits, "md5": md5,
          "peak": round(float(np.abs(x).max()), 3)}
    if md5 == "DIFFERS":
        return _res("fail", "FL's FLAC export decodes, but not bit exact (MD5 differs)", ev,
                    "flh_review _lpc_restore / _decode_batch")
    if ev["peak"] < 1e-4:
        return _res("fail", "FL's FLAC export decoded to silence", ev, "the test song / FL export settings")
    return _res("pass", f"FL's own FLAC export decodes with the built-in reader ({ev['seconds']} s, {bits}-bit, MD5 "
                        f"{md5})", ev)


def c_listen_timing(ctx):
    path = _tiny_song(ctx)
    _open(ctx, path)
    t = time.time()
    r = ctx.call("fl_listen", start_bar=1.0, seconds=2.0, max_seconds=20)
    took = round(time.time() - t, 1)
    ev = {"wall_s": took, "steps_s": r.get("steps_s"), "heard": r.get("heard")}
    if not _heard(r):
        return _res("fail", "fl_listen heard nothing", ev, "fl_listen / the output device")
    if took > 9:
        slow = max((r.get("steps_s") or {}).items(), key=lambda kv: kv[1], default=("?", 0))
        return _res("warn", f"fl_listen took {took} s for 2 s of music; the slowest step is {slow[0]} ({slow[1]} s)",
                    ev, "fl_listen steps (bridge round trips, queued-edit sync, tail wait)")
    return _res("pass", f"fl_listen of 2 s took {took} s in all", ev)


def c_fl_instrument(ctx):
    path, res = _new_song(ctx, "lofi", "check fl instrument")
    lines = [str(c) for c in (res.get("channels") or [])]
    ch = _role_channel(path, "bass")
    jch = ((_journal(path).get("channels") or {}).get(str(ch)) or {}) if ch is not None else {}
    line = next((c for c in lines if "DirectWave" in c or "FL instruments" in c), None) or \
        (f"{ch} {jch.get('sound') or 'bass'} (DirectWave)" if "directwave" in str(jch.get("plugin", "")).lower() else None)
    if ch is None or not line:
        raise Skip("this PC's new lofi song didn't pick one of FL's own instruments for the bass",
                   how="fl_sound_catalog(query='acoustic bass', type='instrument') shows whether FL's Packs/Instruments "
                       "are found")
    pops = _open(ctx, path)
    r = ctx.call("fl_preview", channels=[ch])
    ev = {"channel": line[:80], "lufs": r.get("lufs"), "silent": r.get("silent"), "popups": [p.get("title") for p in pops]}
    if pops:
        return _res("fail", f"FL showed {ev['popups']} while opening a song with an FL instrument preset", ev,
                    "flp_load_plugin of a Packs/Instruments .fst (DirectWave)")
    if r.get("lufs") is None or r.get("silent"):
        return _res("fail", "The FL instrument (DirectWave preset) bass is silent", ev,
                    "DirectWave .fst loading: edition / preset state")
    return _res("pass", f"FL's own instrument plays as the bass ({ev['lufs']} LUFS alone)", ev)


def c_instrument_convert(ctx):
    try:
        lib = ctx.call("flp_instrument_sounds")
    except Exception as e:                                        # noqa: BLE001
        raise Skip(f"no Splice INSTRUMENT library on this PC ({str(e)[:80]})", how="install a free LABS pack")
    silent = set((ctx.call("fl_status").get("silent_packs") or []))
    try:
        silent |= set((ctx.ns["_state"]().get("silent_packs") or {}).keys())
    except Exception:                                             # noqa: BLE001
        pass
    rows = [p for p in lib.get("packs") or [] if p.get("presets") and p["pack"] not in silent]
    if not rows:
        raise Skip("no INSTRUMENT pack with presets that's known to play", how="download a LABS pack (e.g. Soft Piano)")
    # a pack whose sounds are downloaded first (PC 2026-10-23: 'LABS - Glass Piano' had only presets: silent);
    # when none looks downloaded the check still runs (the folder test is a guess) and a silence then skips
    rows.sort(key=lambda p: (p.get("downloaded") is False, "piano" not in p["pack"].lower()))

    def pick(res, path):
        for line in res.get("channels") or []:
            mm = re.match(r"^(\d+) ", str(line)); low = str(line).lower()
            if mm and "instrument" not in low and "labs" not in low and \
                    any(w in low for w in ("chords", "keys", "pad", "flex", "sytrus")):
                return int(mm.group(1))
        return None

    def attempt(row, label):
        """-> ("skip"|"fail"|"silent"|"plays", evidence, message)"""
        pack = row["pack"]
        content = {"downloaded": row.get("downloaded"), "content_mb": row.get("content_mb")}
        path, ch, res, bar, bpm = _solo_song(ctx, "house", pick, _chord_notes, label)
        if ch is None:
            raise Skip("the test song has no synth chords channel to convert")
        try:
            r = ctx.call("flp_instrument_sound", project=path, output=path, channel=ch, pack=pack)
        except Exception as e:                                    # noqa: BLE001
            msg = str(e)
            if "INSTRUMENT" in msg and ("save" in msg.lower() or "flp_save_sound" in msg):
                raise Skip(f"no INSTRUMENT to copy from yet ({msg[:120]})",
                           how="put INSTRUMENT on any channel once and save it with flp_save_sound, then run this "
                               "again")
            return "fail", {"pack": pack}, f"flp_instrument_sound on a FLEX/synth channel failed: {msg[:200]}"
        _open(ctx, path)
        ln = _listen(ctx, bar, bar + 1)
        ev = {"pack": pack, "channel": ch, "from": r.get("made_instrument_from"), "heard": ln.get("heard"),
              "lufs": (ln.get("whole") or {}).get("lufs"), **content}
        if r.get("warning"):
            ev["tool_warning"] = str(r["warning"])[:200]
        return ("plays" if _heard(ln) else "silent"), ev, ""

    tried = []
    # a second pack tells a pack that won't load this way from a broken conversion (PC 2026-10-24: LABS - Glass
    # Piano, downloaded, played silent)
    for i, row in enumerate(rows[:2]):
        got, ev, msg = attempt(row, "check instrument convert" + (f" {i + 1}" if i else ""))
        if got == "fail":
            return _res("fail", msg, ev, "flp_instrument_sound auto-INSTRUMENT (_instrument_source + flp_load_plugin)")
        tried.append(ev)
        if got == "plays":
            break
        if ev.get("downloaded") is not False:
            _note_pack_silent(ctx, ev["pack"])
    ev = tried[-1]; pack = ev["pack"]
    if got == "plays":
        others = [t["pack"] for t in tried[:-1]]
        if ev.get("downloaded") is False:                          # plays although the folder test said no
            ev["content_test_wrong"] = True
        if others:
            ev["silent_first"] = tried[:-1]
            return _res("warn", f"A synth channel became INSTRUMENT with {pack} and plays (copied from {ev['from']}), "
                                f"but {', '.join(others)} played silent the same way (noted: new songs and the "
                                "catalog rank it down) - that pack doesn't load from a state edit", ev,
                        "flp_instrument_sound for that pack (preset name / version in the state)")
        return _res("pass", f"A synth channel became INSTRUMENT with {pack} and plays (copied from {ev['from']})"
                    + ("; note: its folder looked not downloaded - the download test is wrong here" if
                       ev.get("downloaded") is False else ""), ev)
    if all(t.get("downloaded") is False for t in tried):           # the packs, not the conversion: say so, skip
        try:
            where = ctx.ns["_instrument_pack_content"](os.path.join(lib.get("library") or "", pack))["parts"]
        except Exception:                                         # noqa: BLE001
            where = None
        raise Skip(f"'{pack}' played silent and its sounds look not downloaded ({ev.get('content_mb')} MB "
                   "besides its presets" + (f"; folder: {where}" if where else "") + "), so the conversion can't be "
                   "judged", how="download a LABS pack's sounds in INSTRUMENT / the Splice app "
                                 "(flp_instrument_sounds shows downloaded=true), then run this check again",
                   evidence=dict(ev, folder=where))
    ev = next((t for t in tried if t.get("downloaded") is not False), ev)
    return _res("fail", f"Channels turned into INSTRUMENT made no sound with {', '.join(t['pack'] for t in tried)}, "
                        f"although {ev['pack']}'s sounds look downloaded ({ev.get('content_mb')} MB)",
                dict(ev, tried=[t["pack"] for t in tried]),
                "flp_load_plugin of the copied INSTRUMENT state, then the pack edit (PC-VERIFY newsong.md)")


def _note_pack_silent(ctx, pack):
    """Record a pack that played silent after flp_instrument_sound (the catalog and new songs rank it down)."""
    try:
        st = ctx.ns["_state"]() or {}
        sp = dict(st.get("silent_packs") or {})
        sp[pack] = {"seen": time.strftime("%Y-%m-%d"), "project": "fl_pc_check instrument_convert"}
        ctx.ns["_state"]({"silent_packs": sp})
    except Exception:                                             # noqa: BLE001
        pass


def c_mp3_read(ctx):
    _in_fl_ok(ctx)
    path = _tiny_song(ctx)
    _open(ctx, path)
    out = os.path.dirname(_sb(ctx, "mp3", "x"))
    got = {}
    for fmt in ("mp3", "wav"):
        r = _job_result(ctx, ctx.call("fl_render", project=path, output_dir=out, formats=fmt, method="in_fl"),
                        "rendered")
        got[fmt] = next((x for x in (r.get("rendered") or []) if str(x).lower().endswith("." + fmt)), None)
    if not got["mp3"] or not os.path.isfile(got["mp3"]):
        return _res("fail", f"FL's MP3 export made no file ({got})", {}, "fl_render in_fl mp3")
    try:
        a = ctx.call("audio_info", path=got["mp3"]); b = ctx.call("audio_info", path=got["wav"])
    except Exception as e:                                        # noqa: BLE001
        return _res("fail", f"The MP3 couldn't be read with Windows Media Foundation: {e}",
                    {"file": os.path.basename(got["mp3"])}, "mf_audio (COM vtable indices / media type)")
    ev = {"mp3_lufs": a.get("lufs"), "wav_lufs": b.get("lufs"), "mp3_s": a.get("seconds"), "wav_s": b.get("seconds"),
          "rate": a.get("rate"), "channels": a.get("channels")}
    if a.get("lufs") is None or b.get("lufs") is None or abs(float(a["lufs"]) - float(b["lufs"])) > 1.0 or \
            abs(float(a.get("seconds") or 0) - float(b.get("seconds") or 0)) > 0.3:
        return _res("fail", "The MP3 decodes, but not like the WAV it came from (loudness or length differ)", ev,
                    "mf_audio decode (sample format / channels)")
    return _res("pass", f"MP3 read with Windows' own decoder: {ev['mp3_lufs']} LUFS vs {ev['wav_lufs']} for the WAV", ev)


def _check(cid, title, area, risk, needs, minutes, fn, checklist, timeout_s=None):
    tmo = timeout_s or int(minutes * 60 * 3 + 60)

    def run(ctx):
        try:
            with ctx.deadline(tmo):
                r = fn(ctx)
        except Skip as s:
            ev = dict(s.evidence)
            r = _res("skip", s.detail + (f". How to run it: {s.how}" if s.how else ""), ev, "")
        r.setdefault("checklist", checklist)
        if not r.get("checklist"):
            r["checklist"] = checklist
        r["evidence"] = _small(r.get("evidence") or {})
        return r
    run.__name__ = "run_" + cid.replace(".", "_")
    return {"id": cid, "title": title, "area": area, "risk": int(risk), "needs": set(needs), "minutes": minutes,
            "run": run, "timeout_s": tmo}


FLB = ("fl", "bridge")


def checks(ns):
    """The module checks (riskiest first within the list; the runner orders by risk)."""
    C = _check
    return [
        C("mix.knob_safety", "Named EQ values land and no gain knob jumps to max (FL stopped)", "mix", 5, FLB, 2,
          c_mix_knob_safety, "checklists/mixeng.md#7-knob-safety-fix-d"),
        C("mix.map_learn_restore", "Learning the EQ's map puts every knob back exactly", "mix", 5, FLB, 3,
          c_mix_map_learn, "checklists/mixeng.md#1-plugin-maps-all-shipped-maps-are-verified-false"),
        C("mix.map_names", "Every plugin map's FL names match the live effects (corrected user maps saved)", "mix", 4,
          FLB, 3, c_mix_map_names, "checklists/mixeng.md#1b-map-name-check"),
        C("newsong.fl_opens_clean", "New lofi / trap / house songs open in FL with no popups and survive FL's save",
          "newsong", 4, FLB, 3, c_newsong_opens, "checklists/newsong.md#c-flh_new_song"),
        C("finalize.tiny_final", "fl_finalize makes a valid final on target (tiny sandbox song)", "finalize", 4, FLB, 3,
          c_finalize, "checklists/finalize.md"),
        C("newsong.samples_resolve", "Factory sample paths resolve after FL loads and saves the song", "newsong", 3,
          FLB, 1, c_newsong_samples, "checklists/newsong.md#c-flh_new_song"),
        C("newsong.mixer_readback", "Mixer names, colors and routing as planned after FL's save", "newsong", 3, FLB, 1,
          c_newsong_mixer, "checklists/newsong.md#c-flh_new_song"),
        C("sound.shape", "fl_sound_shape makes a channel measurably darker + dreamy, level matched, undo restores",
          "sound", 3, FLB, 2, c_sound_shape, "checklists/newsong.md#f-sound-shaping"),
        C("speed.beat", "Timed: new beat < 2 min, live change < 3 s, note change < 15 s, new sound < 30 s, preview "
          "< 30 s", "speed", 3, FLB, 3, c_speed_beat, "checklists/newsong.md#e-speed"),
        C("newsong.plugin_db_effects", "Reverb / Delay / EQ / limiter from FL's plugin database load", "newsong", 3,
          FLB, 1, c_newsong_effects, "checklists/newsong.md#c-flh_new_song"),
        C("newsong.808_pitch", "The pitched 808 sounds at the written note (transpose offset)", "newsong", 3, FLB, 1,
          c_newsong_808_pitch, "checklists/newsong.md#d-templates-icons-welcome"),
        C("newsong.slide_glide", "808 slide notes glide (FL slide flag)", "newsong", 3, FLB, 1, c_compose_slide,
          "checklists/compose.md#flh_compose---pc-checks-windows--fl-studio"),
        C("newsong.compose_beat", "flp_compose beat + 2 variations: FL plays them, no clipping", "newsong", 3, FLB, 2,
          c_compose_beat, "checklists/compose.md#flh_compose---pc-checks-windows--fl-studio"),
        C("newsong.perform_undo", "flp_perform then undo leaves the notes identical (FL's save)", "newsong", 3, FLB, 1,
          c_perform_undo, "checklists/perform.md"),
        C("mix.send_levels", "A send level set to -6 dB reads back from FL", "mix", 3, FLB, 1, c_mix_sends,
          "checklists/mixeng.md#5-mix-engineer"),
        C("mix.masking_mutes", "Live masking restores every mute", "mix", 3, FLB, 2, c_mix_masking,
          "checklists/mixeng.md#3-masking"),
        C("vocal.setup_readback", "fl_vocal_setup picks, names and arms an insert; reads the audio device", "vocal", 3,
          FLB, 1, c_vocal_setup, "checklists/vocal.md#1-fl_vocal_setup"),
        C("vocal.record_take", "Record a 2-bar take and find it in FL's Recorded folder", "vocal", 3,
          FLB + ("mic", "user"), 2, c_vocal_record, "checklists/vocal.md#3-fl_vocal_record"),
        C("newsong.playlist_names", "Playlist track names and section markers survive FL's save", "newsong", 2, FLB, 1,
          c_newsong_playlist, "checklists/newsong.md#c-flh_new_song"),
        C("newsong.labs_instrument", "A LABS / INSTRUMENT channel plays (if installed)", "newsong", 2, FLB, 1,
          c_newsong_labs, "checklists/newsong.md#c-flh_new_song"),
        C("newsong.flex_default", "A new song's FLEX channel makes sound (its picked preset, or FLEX's default)",
          "newsong", 2, FLB, 1, c_newsong_flex,
          "checklists/newsong.md#c-flh_new_song"),
        C("newsong.fpc_gm_keys", "FPC plays the GM drum keys", "newsong", 2, FLB, 1, c_compose_fpc,
          "checklists/compose.md#flh_compose---pc-checks-windows--fl-studio"),
        C("newsong.template_folder", "FL's user Templates folder exists and takes a saved template", "newsong", 2, (),
          1, c_template_folder, "checklists/newsong.md#d-templates-icons-welcome"),
        C("mix.engineer_live", "fl_mix_engineer proposes moves from live measurements (nothing applied)", "mix", 2,
          FLB, 2, c_mix_engineer, "checklists/mixeng.md#5-mix-engineer"),
        C("review.final_review", "fl_review runs on a final (or a capture)", "review", 2, FLB, 2, c_review,
          "checklists/finalize.md"),
        C("vocal.level_check", "The mic is heard on the vocal insert (person speaks 5 s)", "vocal", 2,
          FLB + ("mic", "user"), 1, c_vocal_level, "checklists/vocal.md#2-fl_vocal_level_check"),
        C("midi.capture_idea", "Capture 2 bars played on a MIDI keyboard", "midi", 2,
          FLB + ("midi_keyboard", "user"), 1, c_midi_capture, "checklists/transcribe.md#1-timed-capture-fl_capture_ideabars4"),
        C("handbook.small_fetch", "Fetch 5 manual pages from image-line.com: robots, 304s, search", "handbook", 2,
          ("network",), 1, c_handbook_fetch, "checklists/handbook.md"),
        C("mix.platform_preview", "Platform preview on a short capture", "mix", 1, FLB, 1, c_mix_platform,
          "checklists/mixeng.md#6-platform-preview"),
        C("midi.capture_probe", "fl_capture_probe: bridge, saved song, keyboards", "midi", 1, FLB, 1, c_midi_probe,
          "checklists/transcribe.md#0-probe-first"),
        C("catalog.refresh", "Sound catalog finds FL's plugin database, factory samples, LABS/FLEX, edition",
          "catalog", 1, (), 1, c_catalog, "checklists/newsong.md#a-sample-catalog-flh_catalog"),
        C("catalog.icon_probe", "Channel icon probe opens in FL (naming icons is optional, by eye)", "catalog", 1, FLB, 1,
          c_icon_probe, "checklists/newsong.md#d-templates-icons-welcome"),
        C("review.access_describe", "fl_describe / fl_visualize / fl_access on a sandbox song", "review", 1, (), 1,
          c_access, "checklists/access.md"),
        C("render.preview_mutes", "fl_preview renders one channel in the open FL and puts every mute back", "render",
          3, FLB, 2, c_preview_mutes, "checklists/core.md"),
        C("render.stems_in_fl", "Stems in the open FL: each different, FL stays open, mutes back", "render", 3, FLB, 4,
          c_stems_in_fl, "checklists/core.md#stems-in-the-open-fl-pc-verify"),
        C("render.flac_export_read", "FL's own FLAC export decodes bit exact without flac/ffmpeg", "render", 3, FLB, 2,
          c_flac_export_read, "checklists/core.md"),
        C("render.mp3_read", "An MP3 (FL's own export) reads with Windows' decoder like its WAV", "render", 2, FLB, 2,
          c_mp3_read, "checklists/core.md"),
        C("newsong.fl_instrument", "A new song's FL instrument (DirectWave preset) plays", "newsong", 3, FLB, 2,
          c_fl_instrument, "checklists/newsong.md"),
        C("newsong.instrument_convert", "flp_instrument_sound turns a synth channel into a playing INSTRUMENT pack",
          "newsong", 2, FLB, 2, c_instrument_convert, "checklists/newsong.md"),
        C("review.listen_timing", "fl_listen of 2 s finishes quickly; steps_s says where the time goes", "review", 2,
          FLB, 1,
          c_listen_timing, "checklists/core.md#fl_listen-timing-added-overnight-pc-verify"),
    ]
