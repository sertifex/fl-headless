"""Write-roundtrip invariants over the robustness corpus (tools/make_corpus.py).

For every corpus project and every project writer that applies: the output reparses, everything the tool did not
intend to change is identical in meaning (canonical snapshot below: notes per pattern/channel, clips, channel events,
mixer, tempo, markers, project info), the input is untouched when the output is another file, refusals leave every
file untouched, results are JSON (no NaN), and the readers still read the output. Half of the calls go through the
MCP dispatcher (m.handle tools/call) so its wrapping (lazy-queue wrappers, open-project guards, error hooks) is
covered too. FL is mocked as not running; a last section runs a few writers in lazy mode (queued on the shadow
copy, applied by flh_sync) and checks they give the same file as running them at once.

Known bugs are xfailed: a matching failure prints "KNOWN BUG <id>" and the suite stays green; anything new fails.
Bug write-ups: internal (ids FZ-01..FZ-19).  FLH_FUZZ_LONG=1 adds the heavy projects (200 channels, 50k notes) to every op."""
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, os.path.join(REPO, "tools"))
TMP = tempfile.mkdtemp(prefix="flh_corpus_")
import atexit, shutil as _sh
atexit.register(_sh.rmtree, TMP, True)   # 380 MB per run: never leave it behind
os.environ["FLH_ROOTS"] = TMP
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="flh_state_")
import fl_headless_mcp as m  # noqa: E402
import make_corpus as mc  # noqa: E402

LONG = os.environ.get("FLH_FUZZ_LONG") == "1"
VERBOSE = os.environ.get("FLH_FUZZ_VERBOSE") == "1"
m.fl_running = lambda: False                                      # FL is not running in every test here
PB = 0x5000
T0 = time.time()

# ------------------------------------------------------------------------------------------------- known bugs
# id -> what it matches (op, project, failure kind: regexes). skip=True: an expensive repro (hang / OOM) that only runs
# with FLH_FUZZ_LONG=1 or FLH_NO_KNOWN=1. (Write-ups with repro and fix are an internal document.)
KNOWN = {      # all fixed (FZ-01..FZ-19); add new ones here with the next FZ- id
}
_seen_known = {}


def known(op, proj, kind):
    if os.environ.get("FLH_NO_KNOWN") == "1":                     # show every failure (for fixers)
        return None
    for bid, k in KNOWN.items():
        if re.fullmatch(k["op"], op) and re.fullmatch(k["proj"], proj) and re.search(k.get("kind", ".*"), kind):
            return bid
    return None


def known_skip(op, proj):
    if LONG or os.environ.get("FLH_NO_KNOWN") == "1":
        return None
    for bid, k in KNOWN.items():
        if k.get("skip") and re.fullmatch(k["op"], op) and re.fullmatch(k["proj"], proj):
            return bid
    return None


# ------------------------------------------------------------------------------------------------- snapshot
def _h(b):
    return hashlib.sha1(b).hexdigest()[:16] if len(b) > 64 else b.hex()


def snapshot(d):
    """Canonical, order-insensitive-where-FL-is meaning of a project -> {key: value}. Keys:
    hdr.*; proj.ev<id> (events before the first block); pat.<id>.notes.ch<c> (sorted note records),
    pat.<id>.ctrl.tempo / .ctrl.other (12-byte controller records), pat.<id>.ev<id>; ch.<index>.ev<id> (payloads in
    order, big ones hashed); links.ev226 / links.ev227; arr.<n>.clips.item<item> (sorted 88-byte records minus the
    clip id), arr.<n>.markers, arr.<n>.ev<id>; mix.<track>.ev<id>."""
    fmt, nch, ppq, ev = mc.parse(d)
    S = {"hdr.fmt": fmt, "hdr.ppq": ppq, "hdr.nch": nch}
    region, arr, mix = "proj", -1, -1

    def add(k, v):
        S.setdefault(k, []).append(v)
    for eid, p in ev:
        if eid == 64:
            region = f"ch.{struct.unpack('<H', p)[0]}"; S.setdefault(region + ".present", True); continue
        if eid == 65:
            region = f"pat.{struct.unpack('<H', p)[0]}"; S.setdefault(region + ".present", True); continue
        if eid == 99:
            arr += 1; region = f"arr.{arr}"; continue
        if eid == 236:
            mix += 1; region = f"mix.{mix}"
        if eid in (226, 227) and not region.startswith("ch."):
            add(f"links.ev{eid}", p.hex()); continue
        if region.startswith("pat.") and eid == 224 and len(p) % 24 == 0:
            S.setdefault(region + ".has_data", True)
            for i in range(0, len(p), 24):
                r = struct.unpack(mc.NOTE, p[i:i + 24]); add(f"{region}.notes.ch{r[2]}", r)
            continue
        if region.startswith("pat.") and eid == 223 and len(p) % 12 == 0:
            for i in range(0, len(p), 12):
                r = p[i:i + 12]
                add(f"{region}.ctrl." + ("tempo" if (r[4], r[7]) == (5, 0x40) else "other"), r.hex())
            continue
        if eid == 199 and "ver" not in S:
            S["ver"] = p.split(b".")[0].decode("ascii", "replace")
        size = clip_size(S.get("ver"), p) if region.startswith("arr.") and eid == 233 else None
        if size:
            for i in range(0, len(p), size):
                r = p[i:i + size]
                add(f"{region}.clips.item{struct.unpack('<H', r[6:8])[0]}", (r[:32] + r[36:] if size >= 88 else r).hex())
            continue
        if region.startswith("arr.") and eid in (148, 33, 34, 205):
            add(f"{region}.markers", (eid, p.hex())); continue
        add(f"{region}.ev{eid}", _h(p))
    out = {}
    for k, v in S.items():
        if isinstance(v, list) and (".notes." in k or ".ctrl." in k or ".clips." in k or k.startswith("links.ev227")):
            v = sorted(v)
        out[k] = tuple(v) if isinstance(v, list) else v
    return out


def clip_size(ver, p):
    """Playlist record size: FL 2024+ 88 bytes, FL 21 60, FL 20 and older 32 (PyFLP's layouts), the version's own
    layout first, and only when every record has the pattern base 0x5000 at +4 (independent of the server's code)."""
    try:
        major = int(ver)
    except (TypeError, ValueError):
        major = None
    pref = 88 if major is None or major >= 22 else 60 if major == 21 else 32
    for size in [pref] + [x for x in (88, 60, 32) if x != pref]:
        if p and len(p) % size == 0 and all(p[i + 4:i + 6] == b"\x00\x50" for i in range(0, len(p), size)):
            return size
    return None if p else 88


def diff_keys(a, b):
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def traits(S):
    chans = sorted({int(k.split(".")[1]) for k in S if k.startswith("ch.") and k.endswith(".present")})
    notes = {}
    for k, v in S.items():
        mm = re.fullmatch(r"pat\.(\d+)\.notes\.ch(\d+)", k)
        if mm:
            notes.setdefault(int(mm.group(1)), {})[int(mm.group(2))] = len(v)
    pats = sorted({int(k.split(".")[1]) for k in S if k.startswith("pat.")})
    placed = sorted({int(k.rsplit("item", 1)[1]) - PB for k in S if re.fullmatch(r"arr\.0\.clips\.item\d+", k)
                     and int(k.rsplit("item", 1)[1]) >= PB})
    with_notes = [p for p in pats if notes.get(p)]
    pl = [p for p in with_notes if p in placed]
    pid = (pl or with_notes or pats or [None])[0]
    ch_counts = {}
    for p, per in notes.items():
        for c, n in per.items():
            ch_counts[c] = ch_counts.get(c, 0) + n
    note_ch = max(ch_counts, key=lambda c: (ch_counts[c], -c)) if ch_counts else (chans[0] if chans else 0)
    pch = max(notes.get(pid, {0: 0}), key=lambda c: notes.get(pid, {}).get(c, 0)) if pid is not None else note_ch
    return {"chans": chans, "pats": pats, "notes": notes, "pid": pid, "ch": note_ch, "pch": pch, "placed": placed}


# ------------------------------------------------------------------------------------------------- calling tools
clean_error = mc.clean_error
TOOL_TIMEOUT = float(os.environ.get("FLH_TOOL_TIMEOUT", "20"))   # seconds per tool call on these small projects
mc.limit_memory(3.0)


class Call:
    __slots__ = ("ok", "res", "etype", "emsg", "tb", "via")


def call(name, args, via_dispatcher):
    c = Call(); c.via = "dispatcher" if via_dispatcher else "direct"; c.tb = ""
    if via_dispatcher:
        try:
            with mc.time_limit(TOOL_TIMEOUT):
                r = m.handle({"method": "tools/call", "params": {"name": name, "arguments": args}})
        except mc.Timeout as e:
            c.ok, c.res, c.etype, c.emsg = False, None, "Timeout", str(e)
            return c
        except Exception as e:                                    # handle() must never raise for tools/call
            c.ok, c.res, c.etype, c.emsg, c.tb = False, None, "DispatcherRaised:" + type(e).__name__, str(e), traceback.format_exc()
            return c
        text = r["content"][-1]["text"]
        if r.get("isError"):
            c.ok, c.res = False, None
            c.etype, _, c.emsg = text.partition(": ")
            return c
        c.ok = True; c.res = json.loads(text); c.etype = c.emsg = None
        return c
    try:
        with mc.time_limit(TOOL_TIMEOUT):
            c.res = m.TOOLS[name][0](**args)
        c.ok = True; c.etype = c.emsg = None
    except (Exception, mc.Timeout) as e:
        c.ok, c.res, c.etype, c.emsg, c.tb = False, None, type(e).__name__, str(e), traceback.format_exc()
    return c


# ------------------------------------------------------------------------------------------------- ops
def _new_ch(T):
    return (max(T["chans"]) + 1) if T["chans"] else 0


def _ch_not_in(T):
    old = set(T["chans"])
    return lambda k: k.startswith("ch.") and int(k.split(".")[1]) not in old


def _pat_not_in(T):
    old = set(T["pats"])
    return lambda k: k.startswith("pat.") and int(k.split(".")[1]) not in old


def _wav(path):
    if not os.path.isfile(path):
        mc.wav(path, 0.5)
    return path


def ops_for(T, src, out, work):
    """[(op id, tool, args, allowed [regex | callable], in_place, check(S0, S1, res) -> problem or None)]"""
    ch, pch, pid = T["ch"], T["pch"], T["pid"]
    last = T["chans"][-1] if T["chans"] else 0
    first = T["chans"][0] if T["chans"] else 0
    new_pat = (max(T["pats"]) + 1) if T["pats"] else 1
    O = []
    O.append(("replace_part", "flp_replace_part",
              dict(project=src, output=out, channel=ch, notes=[[1, 1, "C4", 90], [1.5, 0.5, "E4", 80], [3, 2, "G3", 70]]),
              [rf"pat\.\d+\.notes\.ch{ch}"], False, None))

    def chk_add(S0, S1, res):
        k = f"pat.{res['pattern']}.notes.ch{pch}"
        return None if len(S1.get(k, ())) == len(S0.get(k, ())) + 2 else f"{k}: {len(S0.get(k, ()))} -> {len(S1.get(k, ()))}"
    O.append(("add_notes", "flp_add_notes",
              dict(path=src, output=out, pattern=pid, channel=pch,
                   notes=[{"note": "G4", "bar": 1, "beat": 0, "beats": 1}, {"note": 70, "bar": 2, "beat": 1.5, "beats": 0.25}]),
              [rf"pat\.{pid if pid is not None else new_pat}\.notes\.ch{pch}", rf"pat\.{pid}\.has_data", _pat_not_in(T)],
              False, chk_add))
    O.append(("edit_notes", "flp_edit_notes",
              dict(path=src, output=out, pattern=pid, channel=pch, ops=[{"op": "transpose", "semitones": 2}]),
              [rf"pat\.{pid}\.notes\.ch{pch}"], False, None))
    O.append(("add_automation", "flp_add_automation",
              dict(project=src, output=out, points=[{"bar": 1, "value": 0.2}, {"bar": 3, "value": 0.7}], track=1),
              [_ch_not_in(T), r"links\.ev227", rf"arr\.0\.clips\.item{_new_ch(T)}", r"hdr\.nch"], False, None))

    def chk_newpat(S0, S1, res):
        k = f"arr.0.clips.item{PB + res['pattern']}"
        return None if len(S1.get(k, ())) == 1 else f"{k} has {len(S1.get(k, ()))} clips"
    O.append(("new_pattern", "flp_new_pattern",
              dict(path=src, output=out, name="Fuzz Pat ü", place_at_bar=2, length_bars=2),
              [_pat_not_in(T), rf"arr\.0\.clips\.item{PB + new_pat}"], False, chk_newpat))
    if pid is not None:
        O.append(("playlist_place", "flp_playlist",
                  dict(project=src, output=out, action="place", pattern=pid, bar=3, track=2, repeat=2),
                  [rf"arr\.0\.clips\.item{PB + pid}"], False,
                  lambda S0, S1, res, k=f"arr.0.clips.item{PB + pid}":
                  None if len(S1.get(k, ())) == len(S0.get(k, ())) + 2 else f"{k} count"))
    O.append(("playlist_remove", "flp_playlist", "REMOVE_FIRST", [r"arr\.0\.clips\.item\d+"], False, None))
    O.append(("playlist_mute", "flp_playlist", "MUTE_FIRST", [r"arr\.0\.clips\.item\d+"], False, None))
    O.append(("style_channels_last", "flp_style_channels",
              dict(project=src, output=out, channels=[{"channel": last, "name": "Fuzz Ж 🎹", "color": "#203040"}]),
              [rf"ch\.{last}\.ev(203|128)"], False, None))
    O.append(("style_channels_first", "flp_style_channels",
              dict(project=src, output=out, channels=[{"channel": first, "icon": 12}]),
              [rf"ch\.{first}\.ev155"], False, None))

    def chk_tempo(S0, S1, res):
        v = S1.get("proj.ev156")
        return None if v == (struct.pack("<I", 133500).hex(),) else f"proj tempo {v}"
    O.append(("set_tempo", "flp_set_tempo", dict(path=out, bpm=133.5), [r"proj\.ev156", r"pat\.\d+\.ctrl\.tempo"],
              True, chk_tempo))
    O.append(("tempo_write", "flp_tempo_write", dict(project=src, output=out, points=[[1, 100], [3, 140.25]]),
              [r"proj\.ev156", r"pat\.\d+\.ctrl\.tempo", _pat_not_in(T), "TEMPO_LANE"], False, None))
    O.append(("remove_channels_dry", "flp_remove_channels", dict(project=src, output=out, unused=True, dry_run=True),
              [], False, None))
    O.append(("remove_channels_unused", "flp_remove_channels", dict(project=src, output=out, unused=True),
              "REMAP", False, None))
    O.append(("load_sample", "flp_load_sample",
              dict(project=src, output=out, sample=_wav(os.path.join(TMP, "tone.wav")), name="Smp é"),
              [_ch_not_in(T), r"hdr\.nch", r"mix\.\d+\.ev(204|42|149)"], False, None))   # the free insert's name
    O.append(("compose_write", "flp_compose_write",
              dict(project=src, output=out, pattern="Fuzz Comp", notes=[[pch, 0, 1, 60, 100, 0], [pch, 1, 0.5, 64, 90, 1]],
                   place=[[2, 2]], track=3),
              [_pat_not_in(T), rf"arr\.0\.clips\.item{PB + new_pat}"], False, None))
    O.append(("compose_chords", "flp_compose", dict(project=out, what="chords", genre="pop", bars=4, seed=1),
              [r"pat\..*", _ch_not_in(T), r"arr\.\d+\.clips\..*", r"hdr\.nch", r"links\.ev227",
               r"mix\.\d+\.ev(204|42|149|235)"], True, None))       # a cloned part's own insert: name, color, route
    O.append(("perform", "flp_perform", dict(project=src, output=out, changes={"*": {"dyn": "+4"}}),
              [r"pat\..*", r"proj\.ev156", r"arr\.0\.clips\..*"], False, "SETUP_PERFORM"))
    glide_ch = next((c for c in T["chans"]), 0)
    O.append(("channel_glide", "flp_channel_glide", dict(project=src, output=out, channel=glide_ch, slide=300),
              [rf"ch\.{glide_ch}\.ev221"], False, None))
    O.append(("edit_batch", "flp_edit",
              dict(project=src, output=out, ops=[
                  {"tool": "flp_add_notes", "pattern": pid, "channel": pch, "notes": [{"note": "C5", "bar": 1, "beats": 2}]},
                  {"tool": "flp_style_channels", "channels": [{"channel": first, "name": "Batch é"}]},
                  {"tool": "flp_project_info", "title": "Batch title 漢字"}]),
              [rf"pat\.{pid}\.notes\.ch{pch}", rf"pat\.{pid}\.has_data", _pat_not_in(T), rf"ch\.{first}\.ev203",
               r"proj\.ev194"], False, None))
    O.append(("project_info", "flp_project_info", dict(path=src, output=out, title="Title Ж", genre="Genre é"),
              [r"proj\.ev(194|206)"], False, None))
    O.append(("add_audio_clip", "flp_add_audio_clip",
              dict(project=src, output=out, audio=_wav(os.path.join(TMP, "tone.wav")), bar=3, name="Clip"),
              [_ch_not_in(T), rf"arr\.0\.clips\.item{_new_ch(T)}", r"hdr\.nch"], False, "CHECK_AUDIO_BAR"))
    return O


def allowed_fn(allowed, T, S1, res):
    fns = []
    for a in allowed:
        if a == "TEMPO_LANE":                                     # the tempo lane's pattern and its clip
            lane = [int(k.split(".")[1]) for k, v in S1.items() if k.endswith(".ev193") and
                    any(bytes.fromhex(x) == mc.t16("Tempo") for x in v if isinstance(x, str) and len(x) < 64)]
            for p in lane:
                fns.append(lambda k, p=p: k.startswith(f"pat.{p}.") or k == f"arr.0.clips.item{PB + p}")
        elif callable(a):
            fns.append(a)
        else:
            fns.append(lambda k, a=a: re.fullmatch(a, k) is not None)
    return lambda k: any(f(k) for f in fns)


def check_remap(S0, S1, res, d0, d1):
    """flp_remove_channels: the remaining channels keep their events, notes and clips, renumbered in order."""
    removed = sorted(int(x["channel"]) if isinstance(x, dict) else int(x) for x in (res.get("removed") or []))
    if not removed:
        return [] if S0 == S1 or d0 == d1 else ["nothing removed but the file changed"]
    T0_, T1_ = traits(S0), traits(S1)
    keep = [c for c in T0_["chans"] if c not in removed]
    mp = {o: n for n, o in enumerate(keep)}
    probs = []
    if T1_["chans"] != list(range(len(keep))):
        probs.append(f"channels after: {T1_['chans']} (want 0..{len(keep) - 1})")
    for o, n in mp.items():
        a = {k.split(".", 2)[2]: v for k, v in S0.items() if k.startswith(f"ch.{o}.") and not k.endswith("ev94")}
        b = {k.split(".", 2)[2]: v for k, v in S1.items() if k.startswith(f"ch.{n}.") and not k.endswith("ev94")}
        if a != b:
            probs.append(f"channel {o}->{n} events changed: {sorted(x for x in set(a) | set(b) if a.get(x) != b.get(x))[:4]}")
    for k, v in S0.items():
        mm = re.fullmatch(r"(pat\.\d+)\.notes\.ch(\d+)", k)
        if mm and int(mm.group(2)) in mp:
            want = tuple(sorted(r[:2] + (mp[r[2]],) + r[3:] for r in v))
            if S1.get(f"{mm.group(1)}.notes.ch{mp[int(mm.group(2))]}") != want:
                probs.append(f"{k}: notes changed")
    other = lambda S: {k: v for k, v in S.items() if re.match(r"(mix\.|proj\.|arr\.\d+\.(markers|ev))", k)}
    if other(S0) != other(S1):
        probs.append("mixer/project/arrangement events changed: " + ", ".join(diff_keys(other(S0), other(S1))[:5]))
    return probs


READERS = [("flp_info", "path"), ("flp_list", "path"), ("flp_patterns", "path"), ("flp_playlist", "project"),
           ("flp_tempo_map", "path"), ("flp_health", "path"), ("fl_doctor", "project")]


SLOW_DOCTOR = {"notes_far"}                                       # FZ-06: fl_doctor takes seconds there


def read_all(path, heavy=False):
    out = {}
    for name, key in READERS:
        if heavy and not LONG and name == "fl_doctor":
            continue
        c = call(name, {key: path}, False)
        out[name] = (c.ok, c.etype, c.res)
    return out


# ------------------------------------------------------------------------------------------------- run
FAIL, KNOWN_HITS, STATS = [], {}, {"ops": 0, "ok": 0, "refused": 0, "skipped": 0}
PER_TOOL = {}


def problem(op, proj, kind, detail, c=None):
    bid = known(op, proj, kind)
    PER_TOOL.setdefault(op, [0, 0]); PER_TOOL[op][1 if bid else 0] += 1
    if bid:
        KNOWN_HITS.setdefault(bid, []).append(f"{proj}/{op}")
        return
    FAIL.append((proj, op, kind, detail, c.tb if c is not None and c.tb else ""))


def consistency(proj, path):
    """Readers must agree with each other about the same file (silent misparse detector)."""
    try:
        info = m.flp_info(path); pl = m.flp_playlist(path)["count"]
    except ValueError:
        return
    S = snapshot(open(path, "rb").read())
    my_clips = sum(len(v) for k, v in S.items() if re.fullmatch(r"arr\.0\.clips\.item\d+", k))
    raw_233 = sum(1 for k in S if re.fullmatch(r"arr\.\d+\.ev233", k))           # 233 events we couldn't read as 88s
    if raw_233:
        problem("readers", proj, "readers-disagree", f"playlist event isn't 88-byte records but readers parse it "
                f"(flp_info clips={info['clips']}, flp_playlist count={pl})")
    elif pl != my_clips:
        problem("readers", proj, "readers-disagree", f"flp_playlist lists {pl} clips, arrangement 0 has {my_clips}")
    else:
        all_pat = sum(len(v) for k, v in S.items() if re.fullmatch(r"arr\.\d+\.clips\.item\d+", k)
                      and int(k.rsplit("item", 1)[1]) >= PB)
        all_clips = sum(len(v) for k, v in S.items() if re.fullmatch(r"arr\.\d+\.clips\.item\d+", k))
        if info["clips"] != my_clips:                             # flp_info "clips" = every clip record (any kind)
            problem("readers", proj, "readers-disagree", f"flp_info counts {info['clips']} clips (all arrangements "
                    f"together: {all_clips}, pattern clips {all_pat}), flp_playlist/arrangement 0 has {my_clips}")


FULL_READS = [("flp_info", "path"), ("flp_list", "path"), ("flp_notes", "path"), ("flp_patterns", "path"),
              ("flp_playlist", "project"), ("flp_tempo_map", "path"), ("flp_health", "path"), ("fl_doctor", "project"),
              ("flp_score", "project"), ("flp_clashes", "project"), ("flp_project_info", "path"),
              ("flp_analyze_music", "path")]


def full_reads(proj, path):
    """Every read tool on the valid corpus project: a result or a clean refusal, JSON, in time; MIDI export valid."""
    for i, (name, key) in enumerate(FULL_READS):
        if known_skip("read:" + name, proj):
            KNOWN_HITS.setdefault(known_skip("read:" + name, proj), []).append(f"{proj}/read:{name} (not run)")
            continue
        c = call(name, {key: path}, i % 2 == 0)
        if c.ok:
            try:
                json.dumps(c.res, allow_nan=False)
            except (TypeError, ValueError) as e:
                problem("read:" + name, proj, "result not JSON", str(e))
        elif c.etype == "Timeout":
            problem("read:" + name, proj, "hang (timeout)", f"no result after {TOOL_TIMEOUT:g} s [{c.via}]")
        elif not clean_error(c.etype, c.emsg):
            problem("read:" + name, proj, f"crash {c.etype}", f"{c.etype}: {c.emsg} [{c.via}]", c)
    mid = os.path.join(TMP, "export", proj + ".mid"); os.makedirs(os.path.dirname(mid), exist_ok=True)
    c = call("flp_export_midi", {"path": path, "output": mid}, False)
    if c.ok:
        data = open(mid, "rb").read(); bad = []
        try:
            div, notes, _tm = m.midi_read(mid)
            bad = [n for n in notes if not (0 <= n[2] <= 127 and 0 <= n[3] <= 127)]
        except Exception as e:
            bad = [f"unreadable: {type(e).__name__}: {e}"]
        if bad or data[:4] != b"MThd":
            problem("read:flp_export_midi", proj, "invalid MIDI written", f"{len(bad)} bad notes, e.g. {bad[:2]}")
    elif c.etype == "Timeout" or not clean_error(c.etype, c.emsg):
        problem("read:flp_export_midi", proj, f"crash {c.etype}", f"{c.etype}: {c.emsg}", c)


def run_project(proj, path, heavy):
    d0 = open(path, "rb").read()
    S0 = snapshot(d0); T = traits(S0)
    consistency(proj, path)
    full_reads(proj, path)
    lite = heavy or (proj in SLOW_DOCTOR and not LONG)
    base_reads = read_all(path, lite)
    wdir = tempfile.mkdtemp(dir=TMP, prefix=proj[:20] + "_")
    for i, (op, tool, args, allowed, in_place, check) in enumerate(ops_for(T, "SRC", "OUT", wdir)):
        if heavy and not LONG and op in ("compose_chords", "perform", "edit_notes", "remove_channels_unused"):
            STATS["skipped"] += 1; continue
        bid = known_skip(op, proj)
        if bid:
            KNOWN_HITS.setdefault(bid, []).append(f"{proj}/{op} (not run: FLH_FUZZ_LONG=1 runs it)")
            STATS["skipped"] += 1; continue
        od = os.path.join(wdir, f"{i:02d}_{op}"); os.makedirs(od)
        src = os.path.join(od, "in.flp"); out = os.path.join(od, "out.flp")
        shutil.copyfile(path, src)
        if in_place:
            shutil.copyfile(path, out)
        T2 = traits(S0)
        spec = [o for o in ops_for(T2, src, out, od) if o[0] == op][0]
        args = spec[2]
        if args in ("REMOVE_FIRST", "MUTE_FIRST"):
            try:
                clips = m.flp_playlist(src)["clips"]
            except ValueError:
                clips = []
            if not clips:
                STATS["skipped"] += 1; continue
            args = dict(project=src, output=out, action="remove" if args == "REMOVE_FIRST" else "mute",
                        clips=[clips[0]["id"]])
            item = PB + clips[0]["pattern"] if "pattern" in clips[0] else clips[0]["channel"]
            allowed = [rf"arr\.0\.clips\.item{item}"]
        if check == "SETUP_PERFORM":
            cs = call("flp_perform_setup", {"project": src}, False)
            if not cs.ok:                                         # a refusal of setup is the perform refusal
                if cs.etype == "Timeout":
                    problem(op, proj, "hang (timeout) in flp_perform_setup", f"no result after {TOOL_TIMEOUT:g} s")
                elif not clean_error(cs.etype, cs.emsg):
                    problem(op, proj, f"crash {cs.etype} in flp_perform_setup", f"{cs.etype}: {cs.emsg}", cs)
                STATS["refused"] += 1; continue
            check = None
        via = (i + len(proj)) % 2 == 0
        before_src = open(src, "rb").read(); before_out = open(out, "rb").read() if os.path.exists(out) else None
        STATS["ops"] += 1
        t = time.time()
        c = call(tool, args, via)
        took = time.time() - t
        if VERBOSE:
            print(f"  {proj:22s} {op:24s} {'ok ' if c.ok else 'ERR'} {took:6.2f}s {c.via} {c.etype or ''} {(c.emsg or '')[:90]}")
        target = out if not in_place else out
        if not c.ok:
            if c.etype == "Timeout":
                problem(op, proj, "hang (timeout)", f"no result after {TOOL_TIMEOUT:g} s [{c.via}]")
            elif c.etype and c.etype.startswith("DispatcherRaised"):
                problem(op, proj, "dispatcher raised", f"{c.etype}: {c.emsg}", c)
            elif not clean_error(c.etype, c.emsg or ""):
                problem(op, proj, f"crash {c.etype}", f"{c.etype}: {c.emsg} [{c.via}]", c)
            else:
                STATS["refused"] += 1
                if proj == "base" and op != "remove_channels_unused":   # every op is valid on the plain FL project
                    problem(op, proj, "refused on the base project", f"{c.etype}: {c.emsg} [{c.via}]")
            if open(src, "rb").read() != before_src:
                problem(op, proj, "refusal changed the input", f"{c.etype}: {c.emsg}")
            now_out = open(out, "rb").read() if os.path.exists(out) else None
            if now_out != before_out and not (in_place and now_out == before_src):
                problem(op, proj, "refusal left a changed/partial output", f"{c.etype}: {c.emsg}")
            continue
        STATS["ok"] += 1
        res = c.res
        try:
            json.dumps(res, allow_nan=False)
        except (TypeError, ValueError) as e:
            problem(op, proj, "result not JSON", str(e))
        if not in_place and os.path.abspath(target) != os.path.abspath(src) and open(src, "rb").read() != before_src:
            problem(op, proj, "input modified", "the input changed although output is another file")
        if op == "remove_channels_dry":
            if open(src, "rb").read() != before_src or (os.path.exists(out) and open(out, "rb").read() != (before_out or b"")):
                problem(op, proj, "dry run wrote", "dry_run changed a file")
            continue
        if not os.path.exists(target):
            if op == "remove_channels_unused" and not res.get("removed"):
                continue
            problem(op, proj, "no output", f"result {str(res)[:200]}"); continue
        d1 = open(target, "rb").read()
        try:
            m._events(d1); S1 = snapshot(d1)
        except Exception as e:
            problem(op, proj, "output doesn't reparse", f"{type(e).__name__}: {e}"); continue
        if allowed == "REMAP":
            for pr in check_remap(S0, S1, res, d0, d1):
                problem(op, proj, "remap", pr)
        else:
            ok_key = allowed_fn(allowed, T, S1, res)
            bad = [k for k in diff_keys(S0, S1) if not ok_key(k)]
            if bad:
                problem(op, proj, "unintended change: " + ", ".join(bad[:6]), f"{len(bad)} keys; result {str(res)[:160]}")
            if not diff_keys(S0, S1) and op not in ("remove_channels_unused", "playlist_mute") and res.get("selected") != 0:
                problem(op, proj, "no change", f"tool reported success but nothing changed: {str(res)[:200]}")
        if callable(check):
            pr = check(S0, S1, res)
            if pr:
                problem(op, proj, "intended change missing", pr)
        if check == "CHECK_AUDIO_BAR":                           # bar 3 must mean bar 3 of the song's meter
            try:
                clip = [x for x in m.flp_playlist(target)["clips"] if x.get("channel") == res.get("channel")]
                if clip and abs(clip[0]["bar"] - 3.0) > 1e-6:
                    problem(op, proj, "intended change: clip at wrong bar",
                            f"asked bar 3, flp_playlist shows bar {clip[0]['bar']}")
            except ValueError:
                pass
        after = read_all(target, lite)
        for name, (ok0, et0, _r0) in base_reads.items():
            ok1, et1, _r1 = after[name]
            if ok0 and not ok1:
                problem(op, proj, f"output unreadable by {name}", f"{et1}")
            elif not ok1 and not clean_error(et1, ""):
                problem(op, proj, f"reader {name} crashes on output", et1)


def lazy_mode_check(path):
    """Writers queued in lazy mode (project 'open in FL') then applied by flh_sync give the same file as running
    them at once."""
    ops = [("flp_add_notes", lambda p: dict(path=p, pattern=1, channel=0, notes=[{"note": "D4", "bar": 1, "beats": 1}])),
           ("flp_style_channels", lambda p: dict(project=p, output=p, channels=[{"channel": 1, "name": "Lazy é"}])),
           ("flp_new_pattern", lambda p: dict(path=p, name="Lazy pat", place_at_bar=5)),
           ("flp_playlist", lambda p: dict(project=p, action="place", pattern=1, bar=9, track=4))]
    a = os.path.join(TMP, "lazy_a.flp"); b = os.path.join(TMP, "lazy_b.flp")
    shutil.copyfile(path, a); shutil.copyfile(path, b)
    for name, mk in ops:
        r = call(name, mk(a), True)
        assert r.ok, (name, r.etype, r.emsg)
    real = (m._open_in_fl, m._reload_mode)
    m._open_in_fl = lambda p: os.path.abspath(p) == os.path.abspath(b)
    m._reload_mode = lambda p: "lazy"
    try:
        before = open(b, "rb").read()
        for name, mk in ops:
            r = call(name, mk(b), True)
            if not r.ok:
                problem("lazy", "base", f"lazy {name} failed", f"{r.etype}: {r.emsg}"); return
            if not r.res.get("queued"):
                problem("lazy", "base", "lazy not queued", f"{name}: {str(r.res)[:200]}")
        if open(b, "rb").read() != before:
            problem("lazy", "base", "lazy wrote the real file", "queued edits must not touch the project file")
        info = call("flp_info", {"path": b}, True)
        if not info.ok or info.res.get("pending_ops") != len(ops):
            problem("lazy", "base", "shadow read", f"flp_info on a queued project: {str(info.res)[:200]}")
        m._open_in_fl = lambda p: False                           # FL "closed": the next call applies the queue
        r = call("flh_sync", {}, True)
        if not r.ok:
            problem("lazy", "base", "flh_sync failed", f"{r.etype}: {r.emsg}"); return
    finally:
        m._open_in_fl, m._reload_mode = real
    Sa, Sb = snapshot(open(a, "rb").read()), snapshot(open(b, "rb").read())
    if Sa != Sb:
        problem("lazy", "base", "lazy result differs from immediate", ", ".join(diff_keys(Sa, Sb)[:6]))


def main():
    files = mc.corpus_files(m, os.path.join(TMP, "corpus"), heavy=True)
    print(f"corpus: {len(files)} projects ({sum(1 for v in files.values() if v.get('heavy'))} heavy)")
    for proj, meta in files.items():
        t = time.time()
        run_project(proj, meta["path"], meta.get("heavy"))
        if VERBOSE or time.time() - t > 5:
            print(f"{proj}: {time.time() - t:.1f}s")
    lazy_mode_check(files["base"]["path"])
    for bid, hits in sorted(KNOWN_HITS.items()):
        print(f"KNOWN BUG {bid}: {len(hits)} hit(s), e.g. {', '.join(sorted(set(hits))[:4])}")
    for bid in sorted(set(KNOWN) - set(KNOWN_HITS)):
        print(f"known bug {bid} not seen any more (fixed? remove it from KNOWN)")
    print(f"ops {STATS['ops']}: ok {STATS['ok']}, refused cleanly {STATS['refused']}, skipped {STATS['skipped']}; "
          f"{time.time() - T0:.1f}s")
    if FAIL:
        groups = {}
        for proj, op, kind, detail, tb in FAIL:
            g = groups.setdefault((op, re.sub(r"\d+", "N", kind)), [[], detail, tb])
            g[0].append(proj)
        for (op, kind), (projs, detail, tb) in sorted(groups.items()):
            print(f"\nFAIL {op}: {kind}\n  projects ({len(projs)}): {', '.join(projs[:12])}"
                  f"{' ...' if len(projs) > 12 else ''}\n  e.g. {detail[:400]}")
            if tb and os.environ.get("FLH_FUZZ_TB") == "1":
                print("  " + tb.strip()[-1500:].replace("\n", "\n  "))
        raise SystemExit(f"{len(FAIL)} new failure(s) in {len(groups)} group(s)")
    print("ok")


if __name__ == "__main__":
    main()
