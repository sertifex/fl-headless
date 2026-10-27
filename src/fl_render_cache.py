"""Render cache, stems-once remix, partial renders and ending prediction for fl-headless.

A helper module (not an extension): the server binds it to its namespace (bind(ns)) and calls it from its render
paths. Pure file / numpy work - nothing here talks to FL; the server decides when FL renders.

- fingerprint(d): what in a project affects the sound, from the .flp bytes as FL saved them. Mixer faders and pans
  are kept apart ('levels') so a fader move doesn't invalidate cached stems; every other event is hashed, either
  into the part it belongs to (a channel's block and notes, a mixer track's span) or into a global part that every
  render depends on. Unknown or unattributable events always go to the global part (a false cache hit would be a
  wrong measurement; a false miss only costs one render).
- the cache: <state folder>/render_cache, an index plus one folder per entry, LRU by size (2 GB default,
  FLH_RENDER_CACHE_GB or state 'render_cache_gb'; 0 turns it off). Evicted entries go through the server's recycle
  helper. Renders written into the user's folders are referenced (size + mtime checked), never copied.
- remix: a stems set (one FL 'split mixer tracks' export) -> any fader / pan / mute state offline with numpy.
  Exact for faders and mutes when nothing non-linear sits after the changed track; the master chain (limiter) is
  never re-run, so a mix through master effects is reported as approximate.
- render_range / ending_check: checks that need only part of the song crop a cached full render, or render only
  that bar range (pre-roll for reverb tails, tempo map honored); ending rules are predicted from the notes first.
"""
import hashlib
import json
import os
import shutil
import struct
import threading
import time

H = None
FORMAT = 1                       # bump when the fingerprint changes: every old key misses
_LOCK = threading.RLock()
_MEM = {}                        # fingerprint memo: project bytes -> result (a few, for repeated keying)

# FL rewrites these on every save without the sound changing (work time / dates, title and comments text).
# PC-VERIFY: save an unchanged song twice in FL -> the same 'sound' key; list any other event that changes here.
VOLATILE_EVENTS = {237, 194, 195, 197, 198, 206, 207}
ATTRIBUTABLE_TYPES = (0, 2, 4)   # sampler, plugin, audio-clip channels (layers / automation stay global)
DEFAULT_CAP_GB = 2.0
PAN_SCALE = 6400.0
# PC-VERIFY: FL's mixer pan law. Assumed: a balance control (the far side's gain falls linearly, the near side stays
# at unity). Pan changes are reported as approximate until checked.
PAN_LAW = "balance"
ENDING_WINDOW_S = 45.0           # the last part of a song the ending rules look at
PREROLL_BARS = 2.0               # rendered before a range so reverbs and delays are in their steady state


def bind(ns):
    global H
    H = ns


def _sha(*parts):
    h = hashlib.sha1()
    for p in parts:
        if isinstance(p, str):
            p = p.encode("utf-8")
        h.update(struct.pack("<I", len(p))); h.update(p)
    return h.hexdigest()


# ============================================================================= fingerprint
def fingerprint(d):
    """{'sound', 'levels' {track: [vol_raw, pan_raw]}, 'inserts' {track: F}, 'chan_insert', 'routes', 'fx',
    'names', 'G', 'chan', 'mix', 'raw'} for project bytes d. raw=True: the file couldn't be parsed - only the
    whole-file hash is known (nothing per insert)."""
    key = d if isinstance(d, bytes) else bytes(d)                 # the content itself (its hash is kept on the
    with _LOCK:                                                   # object: no sha1 of the whole file per call)
        hit = _MEM.get(key)
        if hit:
            return hit
    try:
        fp = _fingerprint(d)
    except Exception:                                             # noqa: BLE001 - unknown layout: the whole file
        fp = {"sound": _sha("raw", str(FORMAT), d), "levels": {}, "inserts": {}, "chan_insert": {}, "routes": {},
              "fx": {}, "names": {}, "G": _sha("raw", d), "chan": {}, "mix": {}, "raw": True}
    with _LOCK:
        if len(_MEM) > 8:
            _MEM.clear()
        _MEM[key] = fp
    return fp


def _fingerprint(d):
    ev = H["_events"](d)
    types = H["_channel_types"](d, ev)
    EV_NOTES, EV_TRACK, EV_PARAMS = H["EV_NOTES"], H["EV_MIXER_TRACK"], H["EV_MIXER_PARAMS"]
    VOL, PAN = H["MIX_VOLUME"], H["MIX_PAN"]
    n = len(ev)
    owner = [None] * n
    pid_of = {}
    for kind, idx, a, b in H["_blocks"](d, ev):
        if kind == "channel" and types.get(idx) in ATTRIBUTABLE_TYPES:
            for k in range(a, b):
                owner[k] = ("c", idx)
        elif kind.startswith("pattern"):
            for k in range(a, b):
                pid_of[k] = idx
    tracks = [k for k, e in enumerate(ev) if e["id"] == EV_TRACK]
    pre = {H["EV_MIXER_COLOR"], H["EV_MIXER_HASPROPS"], H["EV_MIXER_NAME"]}
    starts = []
    for k in tracks:
        a = k
        while a > 0 and ev[a - 1]["id"] in pre and owner[a - 1] is None:
            a -= 1
        starts.append(a)
    spans = {}
    for t, a in enumerate(starts):
        if t + 1 < len(starts):
            b = starts[t + 1]
        else:                                                     # the last track: up to the mixer params event
            b = next((k for k in range(tracks[t] + 1, n) if ev[k]["id"] == EV_PARAMS), n)
        spans[t] = (a, b)
        for k in range(a, b):
            owner[k] = ("m", t)
    G = hashlib.sha1(); G.update(b"G%d" % FORMAT)
    chan, notes, mix, g_notes = {}, {}, {}, []
    levels, routes, fx = {}, {}, {}
    route_id = H["EV_MIXER_ROUTE"]; plug_name = H["EV_PLUGIN_NAME"]
    for k, e in enumerate(ev):
        eid = e["id"]
        if eid in VOLATILE_EVENTS:
            continue
        raw = d[e["off"]:e["end"]]
        o = owner[k]
        if eid == EV_NOTES and e["len"] % 24 == 0 and o is None:
            p = d[e["ps"]:e["end"]]; pid = pid_of.get(k, -1)
            for i in range(0, len(p), 24):
                rec = p[i:i + 24]; c = struct.unpack("<H", rec[6:8])[0]
                # FL / fl-headless may re-sort a pattern's records: the order doesn't change the sound
                (notes.setdefault(c, []) if types.get(c) in ATTRIBUTABLE_TYPES else g_notes).append(
                    struct.pack("<i", pid) + rec)
            continue
        if eid == EV_PARAMS and o is None and e["len"] % 12 == 0:
            p = d[e["ps"]:e["end"]]
            for i in range(0, len(p), 12):
                rec = p[i:i + 12]; code = struct.unpack("<H", rec[6:8])[0]
                if 0x7000 <= code < 0x7000 + (128 << 6):
                    t = (code - 0x7000) >> 6
                    if rec[4] in (VOL, PAN) and (code & 0x3F) == 0:
                        lv = levels.setdefault(t, [None, None])
                        lv[0 if rec[4] == VOL else 1] = struct.unpack("<i", rec[8:12])[0]
                        mix.setdefault(t, hashlib.sha1()).update(rec[:8])
                    else:
                        mix.setdefault(t, hashlib.sha1()).update(rec)
                else:
                    G.update(rec)
            continue
        if o is None:
            G.update(raw)
        elif o[0] == "c":
            chan.setdefault(o[1], hashlib.sha1()).update(raw)
        else:
            t = o[1]
            mix.setdefault(t, hashlib.sha1()).update(raw)
            if eid == route_id:
                routes[t] = [i for i, v in enumerate(d[e["ps"]:e["end"]]) if v and i != t]
            elif eid == plug_name:
                fx[t] = True
    for t in spans:
        if t and t not in routes:
            routes[t] = [0]
    try:
        ins = {c: (0 if v < 0 else v) for c, v in H["_channel_inserts"](d, ev).items()}
    except Exception:                                             # noqa: BLE001
        ins = {}
    try:
        names = H["_mixer_names"](d, ev)
    except Exception:                                             # noqa: BLE001
        names = {}
    for rec in sorted(g_notes):
        G.update(rec)
    cfull = {}
    for c in set(chan) | set(notes):
        cfull[c] = _sha("c", (chan.get(c) or hashlib.sha1()).hexdigest(), b"".join(sorted(notes.get(c) or [])))
    for c in sorted(cfull):                                       # a channel without a known insert: everyone's
        if c not in ins:
            G.update(cfull[c].encode())
    g = G.hexdigest()
    mixh = {t: h.hexdigest() for t, h in mix.items()}
    into = {}
    for t, dests in routes.items():
        for u in dests:
            into.setdefault(u, []).append(t)
    memo = {}

    def F(t, stack=()):
        if t in memo:
            return memo[t]
        if t in stack:                                            # FL forbids loops; never recurse forever
            return _sha("loop", str(t))
        parts = [g, mixh.get(t, "")]
        parts += sorted(cfull[c] for c in cfull if ins.get(c) == t)
        parts += sorted(F(u, stack + (t,)) for u in into.get(t, []))
        memo[t] = _sha("F", str(t), *parts)
        return memo[t]
    inserts = {t: F(t) for t in set(spans) | set(ins.values())}
    sound = _sha("sound", str(FORMAT), g, *[f"{c}:{cfull[c]}" for c in sorted(cfull)],
                 *[f"{t}:{mixh[t]}" for t in sorted(mixh)])
    return {"sound": sound, "levels": {t: v for t, v in sorted(levels.items())}, "inserts": inserts,
            "chan_insert": ins, "routes": routes, "fx": {t: True for t in fx}, "names": names, "G": g,
            "chan": cfull, "mix": mixh, "raw": False}


def levels_key(fp):
    return _sha("levels", json.dumps(sorted((int(t), v) for t, v in fp["levels"].items())))


def mix_key(fp, **opts):
    """Key of a whole-mix render (or a bar range of it): the sound, the levels and the options."""
    return _sha("mix", fp["sound"], levels_key(fp), json.dumps(opts, sort_keys=True, default=str))


def downstream(fp, starts):
    """Mixer tracks a signal entering `starts` passes through on its way to the master (incl. sends)."""
    seen, todo = set(), list(starts)
    while todo:
        t = todo.pop()
        if t in seen:
            continue
        seen.add(t)
        todo += [u for u in fp["routes"].get(t, [0] if t else []) if u not in seen]
    seen.add(0)
    return seen


def solo_key(fp, channels, **opts):
    """Key of a render where only `channels` play (the others muted): those channels, the mixer tracks on their way
    to the master (effects + levels) and the global part - an edit elsewhere doesn't change it."""
    chs = sorted(int(c) for c in channels)
    if fp.get("raw"):
        return _sha("solo-raw", fp["sound"], levels_key(fp), json.dumps(chs), json.dumps(opts, sort_keys=True))
    ins = fp["chan_insert"]
    if any(c not in ins for c in chs if c in fp["chan"]):
        path = set(fp["mix"]) | {0}                                # an unknown insert: the whole mixer
    else:
        path = downstream(fp, {ins[c] for c in chs if c in ins} or {0})
    lv = fp["levels"]
    return _sha("solo", fp["G"], *[f"{c}:{fp['chan'].get(c, '')}" for c in chs],
                *[f"{t}:{fp['mix'].get(t, '')}:{lv.get(t)}" for t in sorted(path)],
                json.dumps(opts, sort_keys=True, default=str))


def stems_key(fp, **opts):
    return _sha("stems", fp["sound"], json.dumps(opts, sort_keys=True, default=str))


# ============================================================================= the cache store
def cache_dir():
    return os.path.join(os.path.dirname(H["STATE"]), "render_cache")


def _index_path():
    return os.path.join(cache_dir(), "index.json")


def cap_bytes():
    v = os.environ.get("FLH_RENDER_CACHE_GB")
    if v is None:
        try:
            v = (H["_state"]() or {}).get("render_cache_gb")
        except Exception:                                         # noqa: BLE001
            v = None
    try:
        gb = float(v) if v is not None else DEFAULT_CAP_GB
    except (TypeError, ValueError):
        gb = DEFAULT_CAP_GB
    return int(max(0.0, gb) * (1 << 30))


_OFF = {"n": 0}                  # >0: bypassed (off()); fl_pc_check runs bypass it too (it must see FL render)


class off:
    """with fl_render_cache.off(): ... - every render inside really exports (checks of FL itself)."""
    def __enter__(self):
        _OFF["n"] += 1
        return self

    def __exit__(self, *a):
        _OFF["n"] -= 1
        return False


def enabled():
    if _OFF["n"] > 0:
        return False
    pc = __import__("sys").modules.get("flh_pccheck")
    if pc is not None and (getattr(pc, "_RUN", None) or {}).get("running"):
        return False
    return cap_bytes() > 0


def _read_index():
    try:
        with open(_index_path(), encoding="utf-8") as f:
            idx = json.load(f)
        if isinstance(idx, dict) and isinstance(idx.get("entries"), dict):
            return idx
    except (OSError, ValueError):
        pass
    return {"entries": {}, "v": FORMAT}


def _write_index(idx):
    H["_json_write"](_index_path(), idx)


class _Locked:
    def __enter__(self):
        _LOCK.acquire()
        try:
            os.makedirs(cache_dir(), exist_ok=True)
            self.fl = H["_file_lock"](_index_path() + ".lock"); self.fl.__enter__()
        except BaseException:                                     # not entered: __exit__ won't run - never leave
            _LOCK.release()                                       # the cache locked for every later render
            raise
        return self

    def __exit__(self, *a):
        try:
            self.fl.__exit__(*a)
        finally:
            _LOCK.release()
        return False


class _Held:
    def __init__(self, lk):
        self.lk = lk

    def __enter__(self):
        return self.lk

    def __exit__(self, *a):
        return self.lk.__exit__(*a)


def _try_locked():
    """_Locked() already entered (use it in a with), or None when the index lock can't be had (another fl-headless
    holds it past the timeout, the folder can't be made): the cache is an optimisation, so callers treat that as a
    miss instead of failing the render."""
    lk = _Locked()
    try:
        lk.__enter__()
    except OSError:
        return None
    return _Held(lk)


def _file_ok(f):
    try:
        st = os.stat(f["path"])
    except OSError:
        return False
    if st.st_size != f.get("size"):
        return False
    return f.get("own") or abs(st.st_mtime - float(f.get("mtime") or 0)) < 2.0


def get(key, route=None, exact=False):
    """The entry for key (any route, or the one route given) whose files are all still there - else None.
    exact: only an entry holding FL's own file (not a 16-bit measuring copy) - for renders handed to the user."""
    if not enabled():
        return None
    held = _try_locked()
    if held is None:                                              # index locked elsewhere past the timeout: a miss
        return None
    with held:
        idx = _read_index(); ents = idx["entries"]
        cands = [k for k in ents if k == key or k.startswith(key + "|")]
        for k in cands:
            e = ents[k]
            if route and e.get("route") not in (route, None):
                continue
            if exact and any(f.get("q") for f in e.get("files") or []):
                continue
            if all(_file_ok(f) for f in e.get("files") or []):
                e["used"] = time.time(); e["hits"] = int(e.get("hits", 0)) + 1
                _write_index(idx)
                return dict(e, key=k)
            ents.pop(k, None)                                     # a file went missing / changed: the entry is void
            _write_index(idx)
    return None


def put(key, kind, files, meta=None, route=None, project=None):
    """files: [{'src', 'name', 'mode': 'move'|'copy'|'external'|'measure', 'owned_src': bool}] -> the stored entry
    (or None when the cache is off / the files couldn't be stored). 'measure' = 16-bit with a gain (scale): half the
    disk of FL's 32-bit files, measurements unchanged to within 0.01 dB; 'external' = the user's file, referenced."""
    if not enabled():
        return None
    k = key + (f"|{route}" if route else "")
    folder = os.path.join(cache_dir(), hashlib.sha1(k.encode()).hexdigest()[:20])
    out = []
    try:
        os.makedirs(folder, exist_ok=True)
        for f in files:
            mode = f.get("mode", "copy"); src = f["src"]
            if mode == "external":
                st = os.stat(src)
                out.append({"path": os.path.abspath(src), "own": False, "size": st.st_size, "mtime": st.st_mtime,
                            "scale": 1.0, "name": f.get("name")})
                continue
            dst = os.path.join(folder, f.get("name") or os.path.basename(src))
            scale = 1.0
            if mode == "measure":
                scale = _store_measure(src, dst)
                if f.get("owned_src"):
                    _quiet_remove(src)
            elif mode == "move":
                shutil.move(src, dst)
            else:
                shutil.copy2(src, dst)
            out.append({"path": dst, "own": True, "size": os.path.getsize(dst), "scale": scale,
                        "name": f.get("name"), **({"q": True} if mode == "measure" else {})})
    except Exception:                                             # noqa: BLE001 - the cache is an optimisation
        _drop_dir(folder)
        return None
    ent = {"kind": kind, "route": route, "created": time.time(), "used": time.time(), "hits": 0,
           "project": os.path.basename(project) if project else None, "files": out, "meta": meta or {},
           "bytes": sum(f["size"] for f in out if f["own"])}
    held = _try_locked()
    if held is None:                                              # index locked elsewhere: not kept (a finished render
        _drop_dir(folder)                                         # never fails over the cache; no orphan folder)
        return None
    with held:
        idx = _read_index()
        prev = idx["entries"].get(k)
        if prev and any(f.get("q") for f in out) and not any(f.get("q") for f in prev.get("files") or []) and \
                all(_file_ok(f) for f in prev.get("files") or []):
            _drop_dir(folder)                                     # FL's own file is already kept: better than this
            return dict(prev, key=k)
        old = idx["entries"].pop(k, None)
        if old and old.get("files") and old["files"][0].get("own") and \
                os.path.dirname(old["files"][0]["path"]) != folder:
            _drop_dir(os.path.dirname(old["files"][0]["path"]))
        idx["entries"][k] = ent
        _evict(idx, keep=k)
        _write_index(idx)
    return dict(ent, key=k)


def drop(key):
    """Forget one entry (a render found to be of another state than its key says); its own files are removed."""
    with _Locked():
        idx = _read_index()
        e = idx["entries"].pop(key, None)
        if e is None:
            return False
        _write_index(idx)
    for f in e.get("files") or []:
        if f.get("own"):
            _drop_dir(os.path.dirname(f["path"]))
            break
    return True


def update_meta(key, **meta):
    with _Locked():
        idx = _read_index()
        for k, e in idx["entries"].items():
            if k == key or k.startswith(key + "|"):
                e.setdefault("meta", {}).update(meta)
        _write_index(idx)


def _evict(idx, keep=None):
    cap = cap_bytes(); ents = idx["entries"]
    total = sum(int(e.get("bytes") or 0) for e in ents.values())
    gone = []
    for k in sorted(ents, key=lambda k: ents[k].get("used", 0)):
        if total <= cap:
            break
        if k == keep:
            continue
        e = ents[k]
        if int(e.get("bytes") or 0) <= 0:
            continue
        dirs = sorted({os.path.dirname(f["path"]) for f in e.get("files") or [] if f.get("own")})
        if dirs and not _drop_dirs(dirs):
            break                                                 # couldn't recycle: keep everything as it is
        total -= int(e.get("bytes") or 0); gone.append(k)
    for k in gone:
        ents.pop(k, None)
    if keep in ents and total > cap and int(ents[keep].get("bytes") or 0) > cap:   # bigger than the whole cache
        e = ents.pop(keep)
        _drop_dirs(sorted({os.path.dirname(f["path"]) for f in e.get("files") or [] if f.get("own")}))
    return gone


def _drop_dirs(dirs):
    """Our own cache folders away - deleted, not recycled: they're fl-headless's own renders in its own state folder,
    made again on demand, and a Recycle Bin full of them keeps the disk just as full (PC 2026-10-26: the cache stood at
    5.3 GB against a 2 GB cap: recycling was refused outside the song folders, so nothing was ever evicted). Only
    folders inside the cache folder are ever touched; the user's files never go this way."""
    root = os.path.normcase(os.path.abspath(cache_dir()))
    dirs = [x for x in dirs if os.path.normcase(os.path.abspath(x)).startswith(root + os.sep) and os.path.isdir(x)]
    ok = True
    for x in dirs:
        shutil.rmtree(x, ignore_errors=True)
        ok = ok and not os.path.isdir(x)
    return ok


ORPHAN_AGE_S = 3600.0            # a folder the index doesn't know (a put that lost the index lock) goes after this


def trim():
    """Bring the cache within its cap (oldest-used first) and remove folders the index doesn't list (older than an
    hour). -> {"freed_mb", "entries_gone", "orphans_gone"}. Cheap when there's nothing to do."""
    held = _try_locked()
    if held is None:
        return {"freed_mb": 0.0, "entries_gone": 0, "orphans_gone": 0, "note": "cache busy"}
    with held:
        idx = _read_index()
        before = sum(int(e.get("bytes") or 0) for e in idx["entries"].values())
        gone = _evict(idx)
        if gone:
            _write_index(idx)
        known = {os.path.normcase(os.path.dirname(f["path"])) for e in idx["entries"].values()
                 for f in e.get("files") or [] if f.get("own")}
        orphans, freed_o = [], 0
        try:
            names = os.listdir(cache_dir())
        except OSError:
            names = []
        for nm in names:
            full = os.path.join(cache_dir(), nm)
            if not os.path.isdir(full) or os.path.normcase(full) in known:
                continue
            try:
                if time.time() - os.path.getmtime(full) < ORPHAN_AGE_S:
                    continue
                freed_o += sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(full) for f in fs)
            except OSError:
                continue
            orphans.append(full)
        if orphans:
            _drop_dirs(orphans)
        after = sum(int(e.get("bytes") or 0) for e in idx["entries"].values())
    return {"freed_mb": round((before - after + freed_o) / 1e6, 1), "entries_gone": len(gone), "orphans_gone": len(orphans)}


def _drop_dir(folder):
    _drop_dirs([folder])


def _quiet_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def status():
    try:
        trim()                                                    # over the cap (an older version never evicted)
    except Exception:                                             # noqa: BLE001 - a status never fails on it
        pass
    idx = _read_index(); ents = idx["entries"]
    by = {}
    for e in ents.values():
        b = by.setdefault(e.get("kind") or "?", {"entries": 0, "mb": 0.0, "hits": 0})
        b["entries"] += 1; b["mb"] = round(b["mb"] + int(e.get("bytes") or 0) / 1e6, 1); b["hits"] += int(e.get("hits", 0))
    return {"folder": cache_dir(), "cap_gb": round(cap_bytes() / (1 << 30), 2),
            "used_mb": round(sum(int(e.get("bytes") or 0) for e in ents.values()) / 1e6, 1), "by_kind": by}


def clear():
    with _Locked():
        idx = _read_index()
        dirs = sorted({os.path.dirname(f["path"]) for e in idx["entries"].values() for f in e.get("files") or []
                       if f.get("own")})
        ok = _drop_dirs(dirs)
        n = len(idx["entries"])
        if ok:
            idx["entries"] = {}
            _write_index(idx)
    return {"cleared": n if ok else 0, "recycled_folders": len(dirs) if ok else 0,
            **({} if ok else {"why": "the cache folders couldn't be recycled (nothing was deleted)"})}


# ============================================================================= audio in the cache
def _store_measure(src, dst):
    """src audio -> 16-bit WAV at dst, peak-normalized; returns the gain that restores the level."""
    import numpy as np
    x, fs, bits = H["_wav_load"](src)
    if bits == 16 and str(src).lower().endswith(".wav"):
        shutil.copy2(src, dst)
        return 1.0
    pk = float(np.abs(x).max()) if len(x) else 0.0
    scale = pk / 0.99 if pk > 1e-9 else 1.0
    import fl_audio_edit as AE
    AE.save(dst, x / scale, fs, 16)
    return scale


def load(f):
    """A cached file's samples at their real level: (x float64 (n, ch), fs)."""
    x, fs, _ = H["_wav_load"](f["path"])
    s = float(f.get("scale") or 1.0)
    return (x * s if s != 1.0 else x), fs


def file_named(entry, name):
    return next((f for f in entry.get("files") or [] if f.get("name") == name), None)


# ============================================================================= measuring
def measure(x, fs, sections=None):
    """lufs, peak, true peak, tone, width of x (+ per section [(name, start_s, end_s)])."""
    import numpy as np
    m = H["_measure"](x, fs)
    out = {"lufs": m["lufs"], "peak_dbfs": m["peak_dbfs"], "true_peak_dbfs": round(float(H["_true_peak"](x)), 2),
           "bands_db": m["bands_db"], "centroid_hz": m["centroid_hz"], "seconds": round(len(x) / fs, 1),
           "silent": bool(m["peak_dbfs"] < -70)}
    if sections:
        rows = []
        for name, a, b in sections:
            i, j = max(0, int(a * fs)), min(len(x), int(b * fs))
            if j - i >= int(0.4 * fs):
                seg = x[i:j]; L = H["_lufs"](seg, fs)
                rows.append({"name": name, "lufs": None if L is None else round(L, 1),
                             "peak_dbfs": round(20 * float(np.log10(float(np.abs(seg).max()) + 1e-12)), 1)})
        if rows:
            out["sections"] = rows
    return out


# ============================================================================= time: bars <-> seconds
def timeline(d):
    """(tick_to_s, bar_to_s, P, meters) through the project's tempo map and meter map."""
    ev = H["_events"](d); P = H["_ppq"](d)
    _notes, tempos = H["_song"](d, ev)
    tm = sorted(tempos) or [(0, 120.0)]
    meters = H["_meter_map"](d, ev)

    def tick_s(tick):
        s, prev_t, prev_b = 0.0, 0, tm[0][1]
        for t, b in tm:
            if t >= tick:
                break
            s += (t - prev_t) / P * 60.0 / max(prev_b, 1.0); prev_t, prev_b = t, b
        return s + (tick - prev_t) / P * 60.0 / max(prev_b, 1.0)

    def bar_s(bar):
        return tick_s(H["_bar_to_tick"](float(bar), meters, P))
    return tick_s, bar_s, P, meters


def sections_s(d):
    """[(name, start_s, end_s)] from the playlist markers (the song's sections), or []."""
    try:
        ev = H["_events"](d)
        tick_s, _, _, _ = timeline(d)
        mks = [m for m in H["_markers"](d, ev) if not m.get("signature") and m.get("name")]
        end = song_end_tick(d)
        out = []
        for i, mk in enumerate(mks):
            nxt = mks[i + 1]["tick"] if i + 1 < len(mks) else end
            if nxt and nxt > mk["tick"]:
                out.append((mk["name"], tick_s(mk["tick"]), tick_s(nxt)))
        return out
    except Exception:                                             # noqa: BLE001
        return []


def song_end_tick(d):
    ev = H["_events"](d)
    notes, _ = H["_song"](d, ev)
    end = max([n[0] + n[1] for n in notes] + [0])
    try:
        for _k, _o, r in H["_clips"](d, ev):
            end = max(end, struct.unpack("<I", r[0:4])[0] + struct.unpack("<I", r[8:12])[0])
    except Exception:                                             # noqa: BLE001
        pass
    return end


# ============================================================================= stems-once remix
def _lin(db):
    return 0.0 if db is None or db <= -149 else 10 ** (db / 20.0)


def fader_lin(raw):
    """A fader's stored value (x16000) -> linear gain (FL's measured fader curve). None: not stored (unity)."""
    if raw is None:
        return 1.0
    return _lin(H["_fader_db"](raw / 16000.0))


def pan_gains(raw):
    p = 0.0 if raw is None else max(-1.0, min(1.0, raw / PAN_SCALE))
    return (1.0 - max(0.0, p), 1.0 + min(0.0, p))                 # (left, right) - PAN_LAW 'balance'


def remix(entry, levels_now, mutes=None, want=("mix",)):
    """Offline mix from a stems set at `levels_now` ({track: [vol_raw, pan_raw]}) and optional mutes {track: True}.
    -> (x or None, fs, info {exact, why[], changed[]}). Only tracks that changed (and the master) are read.
    The master chain is linearized (its broadband gain at the rendered operating point) when it has effects."""
    import numpy as np
    meta = entry["meta"]; tr = {int(t): v for t, v in (meta.get("tracks") or {}).items()}
    mutes = {int(t) for t, v in (mutes or {}).items() if v}
    why, changed = [], []
    gain = {}
    for t, info in tr.items():
        if t == 0:
            continue
        old = info.get("level") or [None, None]; new = levels_now.get(t, levels_now.get(str(t))) or old
        g0, g1 = fader_lin(old[0]), (0.0 if t in mutes else fader_lin(new[0]))
        p0, p1 = pan_gains(old[1]), pan_gains(new[1])
        if g0 <= 1e-9 and g1 > 1e-9:
            why.append(f"track {t} was silent (fader down) when its stem was rendered: render again for it")
            return None, meta.get("fs"), {"exact": False, "why": why, "changed": [t], "unusable": True}
        if new[1] != old[1]:
            if min(p0) <= 1e-9 and min(p1) > 1e-9:
                why.append(f"track {t} was hard-panned when rendered: render again for it")
                return None, meta.get("fs"), {"exact": False, "why": why, "changed": [t], "unusable": True}
            why.append(f"track {t}: pan change (FL's pan law assumed: PC-VERIFY)")
        r = np.array([(g1 * p1[0]) / max(g0 * p0[0], 1e-12), (g1 * p1[1]) / max(g0 * p0[1], 1e-12)])
        if g1 <= 1e-12:
            r = np.zeros(2)
        if np.max(np.abs(r - 1.0)) > 1e-6:
            gain[t] = r; changed.append(t)
    mo = (tr.get(0) or {}).get("level") or [None, None]
    m_new = levels_now.get(0, levels_now.get("0")) or mo
    r0 = fader_lin(m_new[0]) / max(fader_lin(mo[0]), 1e-12)
    if 0 in mutes:
        r0 = 0.0
    if abs(r0 - 1.0) > 1e-6:
        changed.append(0)
    fs = meta.get("fs")
    cache = {}

    def stem(t):
        if t not in cache:
            f = file_named(entry, f"t{t}")
            if f is None:
                return None
            cache[t] = load(f)[0]
        return cache[t]
    routes = {int(t): [int(u) for u in (v.get("routes") or [0])] for t, v in tr.items() if int(t) != 0}
    order = _topo(routes)
    delta = {}
    for t in order:                                               # leaves first: what each track's output changes by
        d_in = delta.pop(("in", t), None)
        r = gain.get(t)
        if r is None and d_in is None:
            continue
        g_cur = fader_lin((levels_now.get(t) or tr[t].get("level") or [None])[0]) if t not in mutes else 0.0
        out = None
        if r is not None:
            s = stem(t)
            if s is None:
                why.append(f"no stem for track {t}"); continue
            out = s * (r - 1.0)
        if d_in is not None:
            if tr[t].get("fx"):
                why.append(f"track {t} ({tr[t].get('name') or 'bus'}) has effects: changes through it are linearized")
            pl, pr = pan_gains((levels_now.get(t) or tr[t].get("level") or [None, None])[1])
            through = d_in * g_cur * np.array([pl, pr])
            out = through if out is None else out + through
        dests = routes.get(t, [0])
        if len(dests) > 1:
            why.append(f"track {t} feeds {len(dests)} tracks: send levels assumed at unity")
        for u in dests:
            k = "master" if u == 0 else ("in", u)
            delta[k] = out if k not in delta else _add(delta[k], out)
    d_master = delta.get("master")
    info = {"exact": not why, "why": why, "changed": sorted(set(changed))}
    master = file_named(entry, "t0")
    m0 = load(master)[0] if master is not None else None
    if m0 is None:
        tops = [t for t, ds in routes.items() if 0 in ds]
        acc = None
        for t in tops:
            s = stem(t)
            if s is not None:
                acc = s.copy() if acc is None else _add(acc, s)
        if acc is None:
            return None, fs, dict(info, exact=False, why=why + ["no stems in the set"])
        m0 = acc * fader_lin(mo[0])
        if meta.get("master_fx"):
            info["exact"] = False
            why.append("approximate (master chain not re-run: FL wrote no master file)")
    fxg = 1.0
    if d_master is not None and meta.get("master_fx"):
        tops = [t for t, ds in routes.items() if 0 in ds]
        acc = None
        for t in tops:
            s = stem(t)
            if s is not None:
                acc = s.copy() if acc is None else _add(acc, s)
        if acc is not None:
            n = min(len(acc), len(m0))
            rin = float(np.sqrt(np.mean(acc[:n] ** 2))) * fader_lin(mo[0])
            fxg = float(np.sqrt(np.mean(m0[:n] ** 2))) / rin if rin > 1e-12 else 1.0
        info["exact"] = False
        why.append("approximate (master chain not re-run)")
    if d_master is None:
        y = m0 * r0
    else:
        y = _add(m0, d_master * fader_lin(mo[0]) * fxg) * r0
    info["why"] = why
    return y, fs, info


def units_from_stems(entry, units, levels_now):
    """{label: lufs} of balance units [(label, role, [channels], insert)] measured from a stems set: each unit = the
    sum of its channels' mixer tracks at the current faders (pre-bus, so bus/master effects don't color it).
    Units whose channels share a track with another unit (or have no stem) come back in 'missing'."""
    import numpy as np
    meta = entry["meta"]; ci = {int(c): int(t) for c, t in (meta.get("chan_insert") or {}).items()}
    out, missing = {}, []
    for label, _role, chs, _ins in units:
        mine = {int(c) for c in chs}
        ts = sorted({ci[c] for c in mine if c in ci})
        # separable: every channel of the unit has a track of its own (no channel outside the unit plays into it)
        if not ts or any(c not in ci for c in mine) or any(t == 0 or {c for c in ci if ci[c] == t} - mine for t in ts):
            missing.append(label); continue
        acc = None; fs = meta.get("fs")
        for t in ts:
            f = file_named(entry, f"t{t}")
            if f is None:
                acc = None; break
            x, fs = load(f)
            old = ((meta.get("tracks") or {}).get(str(t)) or {}).get("level") or [None, None]
            new = levels_now.get(t) or old
            g = fader_lin(new[0]) / max(fader_lin(old[0]), 1e-12) * _bus_gain(meta, t, levels_now)
            x = x * g
            acc = x if acc is None else _add(acc, x)
        if acc is None:
            missing.append(label); continue
        L = H["_lufs"](acc, fs)
        out[label] = None if L is None or float(np.abs(acc).max()) < 10 ** (-70 / 20) else round(float(L), 2)
    return out, missing


def _bus_gain(meta, t, levels_now):
    """The faders of the buses between track t and the master (one route each; sends ignored), so a part on a bus
    turned down measures as quiet as it plays. Bus effects aren't applied (stems are taken before them)."""
    tr = meta.get("tracks") or {}; g = 1.0; seen = {t}
    while True:
        routes = [int(u) for u in ((tr.get(str(t)) or {}).get("routes") or [0])]
        if len(routes) != 1 or routes[0] == 0 or routes[0] in seen:
            return g
        t = routes[0]; seen.add(t)
        lv = levels_now.get(t) or ((tr.get(str(t)) or {}).get("level")) or (meta.get("levels") or {}).get(str(t)) \
            or [None]
        g *= fader_lin(lv[0])


def _add(a, b):
    if len(a) == len(b):
        return a + b
    n = max(len(a), len(b))
    import numpy as np
    y = np.zeros((n, max(a.shape[1], b.shape[1])))
    y[:len(a), :a.shape[1]] += a; y[:len(b), :b.shape[1]] += b
    return y


def _topo(routes):
    """Mixer tracks, each after every track that feeds it (master excluded)."""
    order, seen = [], set()

    def visit(t, stack=()):
        if t in seen or t in stack:
            return
        for u, ds in routes.items():
            if t in ds:
                visit(u, stack + (t,))
        seen.add(t); order.append(t)
    for t in sorted(routes):
        visit(t)
    return order


def split_names(files, stem, names):
    """FL's split-export files -> {track: path}. FL names them after the export file and the mixer track
    ('<file>_<track name>.wav'; PC-VERIFY the exact pattern): matched on the track names from the project (and
    'Insert N' / 'Master'). Unmatched files are returned apart."""
    import re
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", str(s).lower())
    by_name = {}
    for t, nm in names.items():
        by_name.setdefault(norm(nm), []).append(int(t))
    out, left = {}, []
    st = norm(stem)
    for f in files:
        base = norm(os.path.splitext(os.path.basename(f))[0])
        rest = base[len(st):] if base.startswith(st) else base
        if rest in ("", "master"):
            out[0] = f; continue
        m = re.fullmatch(r"insert(\d+)", rest)
        if m:
            out[int(m.group(1))] = f; continue
        hit = [t for t in sorted(by_name.get(rest) or []) if t not in out]
        if hit and (len(by_name[rest]) == 1 or not re.search(r"\d$", rest)):   # shared names: the first free one
            out[hit[0]] = f; continue
        m = re.fullmatch(r"(.*?)(\d+)", rest)                     # 'Kick 2' when two tracks share a name
        if m and by_name.get(m.group(1)):
            cand = sorted(by_name[m.group(1)])
            i = int(m.group(2)) - 1
            if 0 <= i < len(cand) and cand[i] not in out:
                out[cand[i]] = f; continue
        left.append(f)
    return out, left


# ============================================================================= ending prediction
def ending_from_notes(d):
    """(hold_s, end_s, last_onset_s) of the song's last chord from the notes: how long it sounds from its attack
    (note lengths; releases and reverb tails come on top)."""
    ev = H["_events"](d)
    notes, _ = H["_song"](d, ev)
    if not notes:
        return None
    tick_s, _, P, _ = timeline(d)
    end = max(n[0] + n[1] for n in notes)
    on = max(n[0] for n in notes)
    chord = [n for n in notes if n[0] >= on - P // 4]               # notes of the last attack (within 1/16)
    last_on = min(n[0] for n in chord)
    return round(tick_s(end) - tick_s(last_on), 2), round(tick_s(end), 2), round(tick_s(last_on), 2)
