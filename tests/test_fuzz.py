"""Fuzz the .flp READ tools with corrupted projects (no hypothesis dependency: own seeded generator).

Mutants of corpus projects (tools/make_corpus.py): bit flips, byte runs, truncation, insertion, deletion, event length
(varint) corruption, structure-valid payload garbage, extreme clip / note / header / tempo / marker fields, duplicated
and swapped events, plus a fixed set of hostile files. Every read tool must return a JSON-serializable result (no
NaN / numpy types) or raise a clean ValueError-style error; never hang (per-call timeout), never MemoryError (address
space capped), never write anything (only flp_export_midi's own output file may appear). Every 4th mutant also goes
through the MCP dispatcher (m.handle tools/call), which must never raise and must return valid JSON text.

Failures are grouped by signature (tool, exception type, innermost fl-headless function). Known bugs (KNOWN below,
ids from an internal write-up) print "KNOWN BUG <id>" and pass; a new signature fails the test with its repro.
  python3 tests/test_fuzz.py                       default run (~1 min)
  FLH_FUZZ_LONG=1 python3 tests/test_fuzz.py       every seed project, 6x the mutants
  FLH_NO_KNOWN=1 ...                               list known bugs as failures too
  python3 tests/test_fuzz.py --repro base 17 [tool]   rebuild one mutant and show the full traceback"""
import hashlib
import json
import os
import random
import re
import shutil
import struct
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src")); sys.path.insert(0, os.path.join(REPO, "tools"))
TMP = tempfile.mkdtemp(prefix="flh_fuzz_")
os.environ["FLH_ROOTS"] = TMP
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="flh_fuzz_state_")
import fl_headless_mcp as m  # noqa: E402
import make_corpus as mc  # noqa: E402

m.fl_running = lambda: False
LONG = os.environ.get("FLH_FUZZ_LONG") == "1"
NO_KNOWN = os.environ.get("FLH_NO_KNOWN") == "1"
TIMEOUT = float(os.environ.get("FLH_TOOL_TIMEOUT", "5"))
N_MUT = int(os.environ.get("FLH_FUZZ_N", "600" if LONG else "120"))
SEEDS = ["base", "empty", "one_channel", "automation_clips", "audio_clips", "timesig_markers", "tempo_automation",
         "zero_length"] + (["arrangements_3", "layers", "unknown_plugins", "names_unicode", "missing_samples",
                            "overlapping_clips", "patterns_1000", "ppq_960", "legacy_playlist60", "split_notes"]
                           if LONG else [])
STATE_DIR = os.path.dirname(m.STATE)
T0 = time.time()

# ------------------------------------------------------------------------------------------------- known bugs
# id -> regexes over the failure signature: tool, kind (exception type / Timeout / nonjson / wrote), where (innermost
# fl-headless function "file:function"). Write-ups, repro and suggested fixes: internal
KNOWN = {      # all fixed (FZ-01..FZ-19); add new ones here with the next FZ- id
}

READ_TOOLS = [
    ("flp_info", lambda p: {"path": p}), ("flp_list", lambda p: {"path": p}), ("flp_notes", lambda p: {"path": p}),
    ("flp_patterns", lambda p: {"path": p}), ("flp_playlist", lambda p: {"project": p}),
    ("flp_tempo_map", lambda p: {"path": p}), ("flp_health", lambda p: {"path": p}),
    ("fl_doctor", lambda p: {"project": p}), ("flp_score", lambda p: {"project": p}),
    ("flp_clashes", lambda p: {"project": p}), ("flp_project_info", lambda p: {"path": p}),
    ("flp_diff", lambda p: {"path": SEED_PATH[0], "other": p}),
    ("flp_export_midi", lambda p: {"path": p, "output": os.path.join(TMP, "out", "x.mid")}),
]
SEED_PATH = [None]


# ------------------------------------------------------------------------------------------------- mutators
def scan(d):
    """[(off, id, payload start, end, length-field start)] of the event stream; stops where it breaks."""
    try:
        hl = struct.unpack("<I", d[4:8])[0]
    except struct.error:
        return []
    i = 8 + hl + 8; out = []
    while i < len(d):
        o = i; eid = d[i]; i += 1; ls = i
        n = mc.size_of(eid)
        if n is None:
            n = s = 0
            while i < len(d):
                b = d[i]; i += 1; n |= (b & 0x7F) << s; s += 7
                if not b & 0x80:
                    break
        if i + n > len(d):
            break
        out.append((o, eid, i, i + n, ls)); i += n
    return out


EXTREME32 = [0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0xFFFFFFF0, 12345678]
EXTREME16 = [0, 1, 0x4FFF, 0x5000, 0x5001, 0x7FFF, 0xFFFF]


def _ev_of(rng, evs, ids):
    c = [e for e in evs if e[1] in ids]
    return rng.choice(c) if c else None


ALL_KINDS = ["flip", "flip", "bytes", "truncate", "insert", "delete", "varint", "varint", "payload", "payload", "clip",
             "clip", "note", "note", "header", "tempo", "marker", "dup", "swap", "multi"]
VALID_KINDS = ["payload", "clip", "clip", "note", "note", "tempo", "marker", "dup", "swap", "multi"]   # stream stays parseable


def mutate(d, rng, kinds=None):
    """One mutant of d -> (bytes, recipe text). kinds=VALID_KINDS keeps the event stream parseable."""
    b = bytearray(d); evs = scan(d)
    kind = rng.choice(kinds or ALL_KINDS)
    if kind == "multi":
        out, r = bytes(b), []
        for _ in range(rng.randint(2, 4)):
            out, rr = mutate(out, rng, [k for k in (kinds or ALL_KINDS) if k != "multi"]); r.append(rr)
        return out, "multi[" + "; ".join(r) + "]"
    if kind == "flip":
        n = rng.randint(1, 8); offs = [rng.randrange(len(b)) for _ in range(n)]
        for o in offs:
            b[o] ^= 1 << rng.randrange(8)
        return bytes(b), f"flip bits at {offs}"
    if kind == "bytes":
        o = rng.randrange(len(b)); n = rng.randint(1, 16); v = rng.choice([0, 0xFF, None])
        b[o:o + n] = bytes(v if v is not None else rng.randrange(256) for _ in range(min(n, len(b) - o)))
        return bytes(b), f"bytes {o}+{n} = {v}"
    if kind == "truncate":
        o = rng.choice([rng.randrange(len(b)), (rng.choice(evs)[2] if evs else 0) + rng.randint(-2, 2)])
        o = max(0, min(len(b), o))
        return bytes(b[:o]), f"truncate at {o}"
    if kind == "insert":
        o = rng.randrange(len(b) + 1); ins = bytes(rng.randrange(256) for _ in range(rng.randint(1, 64)))
        return bytes(b[:o] + ins + b[o:]), f"insert {len(ins)} at {o}"
    if kind == "delete":
        o = rng.randrange(len(b)); n = rng.randint(1, 200)
        return bytes(b[:o] + b[o + n:]), f"delete {o}+{n}"
    if not evs:
        return bytes(b[:max(0, len(b) - 1)]), "truncate last byte"
    if kind == "varint":
        e = _ev_of(rng, evs, range(192, 256)) or rng.choice(evs)
        o, eid, ps, end, ls = e
        if eid < 192:
            return bytes(b[:o] + bytes([rng.randrange(192, 256)]) + b[o + 1:]), f"event@{o} id {eid} -> data id"
        n = end - ps
        new = rng.choice([0, 1, n - 1, n + 1, n * 2, n + 7, 0xFFFFFFF, 2 ** 35, rng.randrange(1 << 20)])
        vi = mc.varint(max(0, new))
        if rng.random() < 0.2:
            vi = b"\xff" * rng.randint(5, 12) + b"\x01"             # an over-long varint
        return bytes(b[:ls] + vi + b[ps:]), f"event@{o} id {eid} length {n} -> {new}"
    if kind == "payload":
        e = rng.choice([x for x in evs if x[3] > x[2]] or evs)
        o, eid, ps, end, ls = e
        mode = rng.choice(["random", "zero", "ff"])
        fill = bytes((rng.randrange(256) if mode == "random" else (0 if mode == "zero" else 255)) for _ in range(end - ps))
        b[ps:end] = fill
        return bytes(b), f"payload of event@{o} id {eid} ({end - ps} bytes) = {mode}"
    if kind == "clip":
        e = _ev_of(rng, evs, (233,))
        if e is None or e[3] - e[2] < 88:
            return mutate(d, rng, kinds)
        o, eid, ps, end, ls = e
        r = ps + 88 * rng.randrange((end - ps) // 88)
        f = rng.choice([(0, "I"), (8, "I"), (24, "I"), (28, "I"), (6, "H"), (12, "H"), (18, "H"), (24, "f"), (32, "I")])
        v = rng.choice(EXTREME16 if f[1] == "H" else
                       ([float("nan"), float("inf"), -1e30, 1e30, -1.0] if f[1] == "f" else EXTREME32))
        struct.pack_into("<" + f[1], b, r + f[0], v)
        return bytes(b), f"clip record @{r} field +{f[0]} = {v}"
    if kind == "note":
        e = _ev_of(rng, evs, (224,))
        if e is None or e[3] - e[2] < 24:
            return mutate(d, rng, kinds)
        o, eid, ps, end, ls = e
        r = ps + 24 * rng.randrange((end - ps) // 24)
        f = rng.choice([(0, "I", EXTREME32), (12, "H", [0, 127, 128, 255, 0xFFFF]), (8, "I", EXTREME32),
                        (6, "H", EXTREME16), (19, "B", [0, 255]), (17, "B", [0, 255])])
        v = rng.choice(f[2]); struct.pack_into("<" + f[1], b, r + f[0], v)
        return bytes(b), f"note record @{r} field +{f[0]} = {v}"
    if kind == "header":
        f = rng.choice(["ppq", "ppq", "hl", "dtlen", "nch", "fmt", "magic"])
        if f == "ppq":
            v = rng.choice([0, 1, 3, 65535]); struct.pack_into("<H", b, 12, v)
        elif f == "hl":
            v = rng.choice([0, 2, 5, 7, 1000, 0xFFFFFFFF]); struct.pack_into("<I", b, 4, v)
        elif f == "dtlen":
            v = rng.choice([0, 1, 0xFFFFFFFF]); struct.pack_into("<I", b, 18, v)
        elif f == "nch":
            v = rng.choice([0, 65535]); struct.pack_into("<H", b, 10, v)
        elif f == "fmt":
            v = rng.choice([-1, 16, 32767]); struct.pack_into("<h", b, 8, v)
        else:
            v = "FLdt->XXXX"; b[14:18] = b"XXXX"
        return bytes(b), f"header {f} = {v}"
    if kind == "tempo":
        e = _ev_of(rng, evs, (156, 223))
        if e is None:
            return mutate(d, rng, kinds)
        o, eid, ps, end, ls = e
        if eid == 156:
            v = rng.choice([0, 1, 0xFFFFFFFF, 10, 999999999]); struct.pack_into("<I", b, ps, v)
            return bytes(b), f"project tempo = {v}"
        if end - ps >= 12:
            r = ps + 12 * rng.randrange((end - ps) // 12)
            b[r + 4] = 5; b[r + 7] = 0x40
            v = rng.choice([0, 1, 0xFFFFFFFF]); struct.pack_into("<I", b, r + 8, v)
            t = rng.choice([0, 0xFFFFFFFF, 7]); struct.pack_into("<I", b, r, t)
            return bytes(b), f"recorded tempo @{r}: tick {t} value {v}"
        return mutate(d, rng, kinds)
    if kind == "marker":
        e = _ev_of(rng, evs, (17, 18, 33, 34, 148))
        if e is None:
            return mutate(d, rng, kinds)
        o, eid, ps, end, ls = e
        if eid == 148:
            v = rng.choice([0x08000000, 0x0FFFFFFF, 0xFFFFFFFF]); struct.pack_into("<I", b, ps, v)
        else:
            v = rng.choice([0, 1, 255, 3, 7]); b[ps] = v
        return bytes(b), f"meter/marker event {eid}@{o} = {v}"
    if kind == "dup":
        o, eid, ps, end, ls = rng.choice(evs); k = rng.randint(1, 3)
        return bytes(b[:end] + bytes(b[o:end]) * k + b[end:]), f"duplicate event {eid}@{o} x{k}"
    if kind == "swap":
        i = rng.randrange(len(evs) - 1) if len(evs) > 1 else 0
        a, c = evs[i], evs[min(len(evs) - 1, i + rng.randint(1, 5))]
        if a is c:
            return mutate(d, rng, kinds)
        return (bytes(b[:a[0]] + b[c[0]:c[3]] + b[a[3]:c[0]] + b[a[0]:a[3]] + b[c[3]:]),
                f"swap events {a[1]}@{a[0]} and {c[1]}@{c[0]}")
    raise AssertionError(kind)


def hostile(base):
    """Fixed hostile files: (name, bytes)."""
    fl = b"FLhd" + struct.pack("<I", 6) + struct.pack("<hHH", 0, 0, 96)
    yield "empty file", b""
    yield "FLhd only", b"FLhd"
    yield "header no FLdt", fl
    yield "FLdt no events", fl + b"FLdt" + struct.pack("<I", 0)
    yield "one tempo event", fl + b"FLdt" + struct.pack("<I", 5) + bytes([156]) + struct.pack("<I", 120000)
    yield "ppq 0", fl[:12] + struct.pack("<H", 0) + base[14:]
    yield "header length 0xFFFFFFFF", base[:4] + struct.pack("<I", 0xFFFFFFFF) + base[8:]
    yield "random 4 KB after FLhd", fl + b"FLdt" + struct.pack("<I", 4096) + bytes(random.Random(1).randrange(256) for _ in range(4096))
    yield "data event claims 2^40 bytes", fl + b"FLdt" + struct.pack("<I", 8) + bytes([224]) + mc.varint(2 ** 40) + b"\x00"
    yield "endless varint", fl + b"FLdt" + struct.pack("<I", 40) + bytes([224]) + b"\xff" * 40
    yield "a WAV file", b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + b"\x00" * 24
    yield "1000 empty channels", fl + b"FLdt" + struct.pack("<I", 0) + b"".join(bytes([64]) + struct.pack("<H", i) + bytes([51, 0]) for i in range(1000))
    yield "notes event not multiple of 24", fl + b"FLdt" + struct.pack("<I", 0) + bytes([65, 1, 0, 224, 25]) + b"\x01" * 25
    yield "base doubled", base + base[22:]
    p = mc.Proj(base)                                             # FZ-03: a mixer plugin header with no payload
    next(e for e in p.mixer if e[0] == 212)[1] = b""
    yield "empty plugin header", p.bytes()
    p = mc.Proj(base)                                             # FZ-04: a mixer plugin block without its header
    k = next(i for i, e in enumerate(p.mixer) if e[0] == 212); del p.mixer[k]
    yield "plugin block without header", p.bytes()
    p = mc.Proj(base)                                             # FZ-05: project tempo 0, no recorded tempo
    next(e for e in p.head if e[0] == 156)[1] = struct.pack("<I", 0)
    for e in p.pdata[0]:
        if e[0] == 223:
            e[1] = b""
    yield "tempo 0", p.bytes()
    if LONG or NO_KNOWN:                                          # FZ-06 costs a few timeouts: long runs only
        p = mc.Proj(base); recs = p.clips()
        struct.pack_into("<I", recs[0], 8, 1 << 31); p.set_clips(recs)
        yield "pattern clip 2^31 ticks long", p.bytes()


# ------------------------------------------------------------------------------------------------- running
def tree_state():
    """{path: (size, mtime_ns)} of every file under FLH_ROOTS (minus the fuzz inputs) and the state folder."""
    out = {}
    for root in (TMP, STATE_DIR):
        for dp, dn, fn in os.walk(root):
            if os.path.abspath(dp).startswith(os.path.join(TMP, "mut")) or "pending" in dp:
                continue
            for f in fn:
                p = os.path.join(dp, f)
                if p == m._usage_file():                          # the dispatcher's own usage counts (throttled)
                    continue
                try:
                    st = os.stat(p); out[p] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    pass
    return out


def where(tb_obj):
    """Innermost fl-headless frame of a traceback -> 'file:function'."""
    last = "?"
    for fr in traceback.extract_tb(tb_obj):
        f = os.path.abspath(fr.filename)
        if f.startswith(REPO) and os.sep + "tests" + os.sep not in f and os.sep + "tools" + os.sep not in f:
            last = f"{os.path.basename(f)}:{fr.name}"
    return last


def strict_json(v):
    json.dumps(v, allow_nan=False)


def _no_const(x):
    raise ValueError(f"non-JSON constant {x}")


def run_tool(name, args, via):
    """-> (kind or None, detail, where, tb text)"""
    if via:
        try:
            with mc.time_limit(TIMEOUT):
                r = m.handle({"method": "tools/call", "params": {"name": name, "arguments": args}})
        except mc.Timeout as e:
            return "Timeout", str(e), where(e.__traceback__), traceback.format_exc()
        except BaseException as e:
            return "dispatcher raised " + type(e).__name__, str(e), where(e.__traceback__), traceback.format_exc()
        text = r["content"][-1]["text"]
        if r.get("isError"):
            et, _, msg = text.partition(": ")
            if mc.clean_error(et, msg):
                return None, "", "", ""
            err = list(m._ERRORS)[-1] if m._ERRORS else {}
            w = "?"
            for ln in reversed((err.get("traceback") or "").splitlines()):
                mm = re.search(r'File "([^"]+)", line \d+, in (\w+)', ln)
                if mm and os.path.abspath(mm.group(1)).startswith(REPO) and os.sep + "tests" + os.sep not in mm.group(1):
                    w = f"{os.path.basename(mm.group(1))}:{mm.group(2)}"; break
            return et, msg[:300], w, err.get("traceback", "")
        try:
            json.loads(text, parse_constant=_no_const)
        except ValueError as e:
            return "nonjson", f"dispatcher text: {e}", "dispatcher", ""
        return None, "", "", ""
    try:
        with mc.time_limit(TIMEOUT):
            res = m.TOOLS[name][0](**args)
    except mc.Timeout as e:
        return "Timeout", str(e), where(e.__traceback__), traceback.format_exc()
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt):
            raise
        if mc.clean_error(type(e).__name__, str(e)):
            return None, "", "", ""
        return type(e).__name__, str(e)[:300], where(e.__traceback__), traceback.format_exc()
    try:
        strict_json(res)
    except (TypeError, ValueError) as e:
        return "nonjson", str(e)[:200], name, ""
    return None, "", "", ""


FAILS = {}                                                        # signature -> [count, first recipe, detail, tb, order]
PER_TOOL = {}
ORDER = [0]                                                       # the running case's place in the full run's order


def record(sig, recipe, detail, tb):
    ent = FAILS.setdefault(sig, [0, recipe, detail, tb, ORDER[0]])
    ent[0] += 1
    if ORDER[0] < ent[4]:                                         # merged shards: the run order's first case is kept
        ent[1:5] = [recipe, detail, tb, ORDER[0]]
    PER_TOOL[sig[0]] = PER_TOOL.get(sig[0], 0) + 1


def fuzz_file(path, recipe, via_every=4, idx=0):
    SEED_PATH[0] = SEED_PATH[0] or path
    before = tree_state()
    for ti, (name, mk) in enumerate(READ_TOOLS):
        via = (idx + ti) % via_every == 0
        kind, detail, w, tb = run_tool(name, mk(path), via)
        if kind:
            record((name, kind, w), recipe, detail, tb)
    after = tree_state()
    out_ok = os.path.join(TMP, "out", "x.mid")
    new = sorted(p for p in set(after) - set(before) if p != out_ok)
    changed = sorted(p for p in set(after) & set(before) if after[p] != before[p] and p != out_ok)
    if new or changed:
        record(("any", "wrote", "files"), recipe, f"new {new[:3]} changed {changed[:3]}", "")
    if os.path.exists(out_ok):
        os.remove(out_ok)


# ------------------------------------------------------------------------------------------------- writers on mutants
def _wav():
    p = os.path.join(TMP, "w.wav")
    return p if os.path.isfile(p) else mc.wav(p, 0.25)


WRITE_TOOLS = [
    ("flp_add_notes", lambda s, o: dict(path=s, output=o, pattern=1, channel=0, notes=[{"note": "C4", "bar": 1}])),
    ("flp_replace_part", lambda s, o: dict(project=s, output=o, channel=1, notes=[[1, 1, 60, 90]])),
    ("flp_new_pattern", lambda s, o: dict(path=s, output=o, name="Fz", place_at_bar=2)),
    ("flp_playlist", lambda s, o: dict(project=s, output=o, action="place", pattern=1, bar=5, track=3)),
    ("flp_style_channels", lambda s, o: dict(project=s, output=o, channels=[{"channel": 0, "name": "Fz", "color": "#102030"}])),
    ("flp_set_tempo", lambda s, o: dict(path=o, bpm=99)),
    ("flp_tempo_write", lambda s, o: dict(project=s, output=o, points=[[1, 90], [2, 100]])),
    ("flp_add_automation", lambda s, o: dict(project=s, output=o, points=[{"bar": 1, "value": 0}, {"bar": 2, "value": 1}])),
    ("flp_add_audio_clip", lambda s, o: dict(project=s, output=o, audio=_wav(), bar=2)),
    ("flp_channel_glide", lambda s, o: dict(project=s, output=o, channel=0)),
    ("flp_remove_channels", lambda s, o: dict(project=s, output=o, unused=True)),
    ("flp_compose_write", lambda s, o: dict(project=s, output=o, pattern="Fz", notes=[[0, 0, 1, 60, 100, 0]], place=[1])),
]


def fuzz_writes(path, recipe, idx):
    """Writers on a parseable mutant: a result whose output reparses and still reads, or a clean refusal that leaves
    every file as it was; the input is never touched when the output is another file."""
    src_bytes = open(path, "rb").read()
    for ti, (name, mk) in enumerate(WRITE_TOOLS):
        out = os.path.join(TMP, "mut", "w_out.flp")
        in_place = name == "flp_set_tempo"
        if in_place:
            shutil.copyfile(path, out)
        elif os.path.exists(out):
            os.remove(out)
        kind, detail, w, tb = run_tool(name, mk(path, out), (idx + ti) % 4 == 0)
        if open(path, "rb").read() != src_bytes:
            record((name, "input modified", "-"), recipe, "the input changed", "")
            open(path, "wb").write(src_bytes)
        if kind:
            record((name, kind, w), recipe, detail, tb)
        if not os.path.exists(out):
            continue
        d1 = open(out, "rb").read()
        if kind is None and d1 != (src_bytes if in_place else b""):
            try:
                m._events(d1)
            except Exception as e:
                record((name, "output unparseable", type(e).__name__), recipe, str(e)[:200], "")
                continue
            for rn, rmk in READ_TOOLS[:7]:                        # the cheap readers must still read it
                k2, det2, w2, tb2 = run_tool(rn, rmk(out), False)
                if k2:
                    k0, *_x = run_tool(rn, rmk(path), False)
                    if not k0:                                    # the input read fine: the write broke it
                        record((name, "output breaks " + rn, w2), recipe, f"{k2}: {det2}", tb2)
        elif kind is not None and in_place and d1 != src_bytes:
            record((name, "refusal changed the file", "-"), recipe, detail, "")


def known_of(sig):
    if NO_KNOWN:
        return None
    for bid, k in KNOWN.items():
        if (re.fullmatch(k["tool"], sig[0]) and re.fullmatch(k["kind"], sig[1]) and re.fullmatch(k["where"], sig[2])):
            return bid
    return None


def build_seeds():
    files = mc.corpus_files(m, os.path.join(TMP, "corpus"))
    return {n: files[n]["path"] for n in SEEDS if n in files}


def repro_write(seed, i, tool=None):
    seeds = build_seeds(); d = open(seeds[seed], "rb").read()
    data, recipe = mutate(d, random.Random(f"w:{seed}:{i}"), VALID_KINDS)
    p = os.path.join(TMP, "mut", f"w_{seed}_{i}.flp"); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
    print(f"write mutant {seed}:{i}: {recipe} -> {p}")
    for name, mk in WRITE_TOOLS:
        if tool and name != tool:
            continue
        out = os.path.join(TMP, "mut", "w_out.flp")
        if name == "flp_set_tempo":
            shutil.copyfile(p, out)
        try:
            with mc.time_limit(TIMEOUT):
                r = m.TOOLS[name][0](**mk(p, out))
            print(f"{name}: ok {str(r)[:160]}")
        except BaseException as e:
            print(f"{name}: {type(e).__name__}: {str(e)[:200]}")
            if not mc.clean_error(type(e).__name__, str(e)):
                traceback.print_exc()


def repro(seed, i, tool=None):
    seeds = build_seeds(); d = open(seeds[seed], "rb").read(); SEED_PATH[0] = seeds[seed]
    if i < 0:
        name, data = list(hostile(d))[-i - 1]; recipe = f"hostile '{name}'"
    else:
        data, recipe = mutate(d, random.Random(f"{seed}:{i}"))
    p = os.path.join(TMP, "mut", f"{seed}_{i}.flp"); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
    print(f"mutant {seed}:{i}: {recipe} -> {p}")
    for name, mk in READ_TOOLS:
        if tool and name != tool:
            continue
        t = time.time()
        try:
            with mc.time_limit(TIMEOUT):
                r = m.TOOLS[name][0](**mk(p))
            strict_json(r); print(f"{name}: ok {time.time() - t:.2f}s")
        except BaseException as e:
            print(f"{name}: {type(e).__name__}: {str(e)[:200]} ({time.time() - t:.2f}s)")
            if not mc.clean_error(type(e).__name__, str(e)):
                traceback.print_exc()


def cases(seeds, shard=0, shards=1):
    """Every fuzz case of a run, in the run's order: (order, kind, seed, i, data, recipe); kind hostile / read / write.
    A mutant byte-identical to an earlier one of the same kind is left out (the same bytes take the same code paths:
    ~7 % of the mutants). shards > 1: only this shard's share (round robin per kind, so the shards stay balanced)."""
    out, seen, order = [], {"read": set(), "write": set()}, 0
    base = open(seeds["base"], "rb").read()
    for hi, (name, data) in enumerate(hostile(base)):
        if hi % shards == shard:
            out.append((order, "hostile", "base", hi, data, f"hostile '{name}' (repro: --repro base {-hi - 1})"))
        order += 1
    for kind, plan in (("read", [(sd, i) for sd in seeds for i in range(N_MUT)]),
                       ("write", [(sd, i) for sd in (("base", "automation_clips", "timesig_markers") if not LONG
                                                     else list(seeds)) for i in range(N_MUT // 2)])):
        k = 0
        for seed, i in plan:
            d = open(seeds[seed], "rb").read()
            if kind == "read":
                data, recipe = mutate(d, random.Random(f"{seed}:{i}"))
                recipe = f"{seed}:{i} {recipe} (repro: --repro {seed} {i})"
            else:
                data, recipe = mutate(d, random.Random(f"w:{seed}:{i}"), VALID_KINDS)
                recipe = f"w:{seed}:{i} {recipe} (repro: --repro-write {seed} {i})"
            h = hashlib.sha1(data).digest()
            if h in seen[kind]:
                continue
            seen[kind].add(h)
            if k % shards == shard:
                out.append((order, kind, seed, i, data, recipe))
            k += 1; order += 1
    return out


def run_cases(seeds, todo):
    """-> (read files, parseable write mutants, read seconds, write seconds)"""
    os.makedirs(os.path.join(TMP, "mut"), exist_ok=True); os.makedirs(os.path.join(TMP, "out"), exist_ok=True)
    n = nw = 0; t_r = t_w = 0.0
    for order, kind, seed, i, data, recipe in todo:
        ORDER[0] = order; t = time.time()
        if kind == "hostile":
            p = os.path.join(TMP, "mut", f"hostile_{i}.flp"); open(p, "wb").write(data)
            SEED_PATH[0] = seeds["base"]
            fuzz_file(p, recipe, idx=i); n += 1
        elif kind == "read":
            p = os.path.join(TMP, "mut", f"{seed}_{i}.flp")
            with open(p, "wb") as f:
                f.write(data)
            SEED_PATH[0] = seeds["base"]
            fuzz_file(p, recipe, idx=i); n += 1
            os.remove(p)
            m._LOAD_CACHE.clear()
        else:
            p = os.path.join(TMP, "mut", f"w_{seed}_{i}.flp")
            with open(p, "wb") as f:
                f.write(data)
            fuzz_writes(p, recipe, i); nw += 1
            os.remove(p)
            m._LOAD_CACHE.clear()
        if kind == "write":
            t_w += time.time() - t
        else:
            t_r += time.time() - t
    return n, nw, t_r, t_w


JOBS = max(1, int(os.environ.get("FLH_FUZZ_JOBS") or min(2, os.cpu_count() or 1)))


def shard_main(k, n_sh, out_path):
    """One shard in its own process (own FLH_ROOTS, state folder, temp tree): its cases, its failures as JSON."""
    mc.limit_memory(3.0)
    seeds = build_seeds()
    n, nw, t_r, t_w = run_cases(seeds, cases(seeds, k, n_sh))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"n": n, "nw": nw, "t_r": t_r, "t_w": t_w,
                   "fails": [[list(sig)] + v for sig, v in FAILS.items()], "per_tool": PER_TOOL}, f)
    shutil.rmtree(TMP, ignore_errors=True); shutil.rmtree(os.environ["LOCALAPPDATA"], ignore_errors=True)


def run_sharded(n_sh):
    """The cases split over n_sh processes (each fully isolated, like a run of its own); failures merged."""
    import subprocess
    env = {k: v for k, v in os.environ.items() if k not in ("FLH_ROOTS", "LOCALAPPDATA")}
    outs = [os.path.join(TMP, f"shard_{k}.json") for k in range(n_sh)]
    procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "--shard", str(k), str(n_sh), outs[k]],
                              env=env, stdin=subprocess.DEVNULL) for k in range(n_sh)]
    codes = [p.wait() for p in procs]
    if any(codes):
        raise SystemExit(f"fuzz shard(s) crashed: exit codes {codes}")
    n = nw = 0; t_r = t_w = 0.0
    for o in outs:
        r = json.load(open(o, encoding="utf-8"))
        n += r["n"]; nw += r["nw"]; t_r = max(t_r, r["t_r"]); t_w = max(t_w, r["t_w"])
        for sig, cnt, recipe, detail, tb, order in r["fails"]:
            sig = tuple(sig); ent = FAILS.setdefault(sig, [0, recipe, detail, tb, order])
            ent[0] += cnt
            if order < ent[4]:
                ent[1:5] = [recipe, detail, tb, order]
        for t, c in r["per_tool"].items():
            PER_TOOL[t] = PER_TOOL.get(t, 0) + c
    return n, nw, t_r, t_w


def main():
    if JOBS > 1:
        n, nw, t_r, t_w = run_sharded(JOBS)
    else:
        mc.limit_memory(3.0)
        seeds = build_seeds()
        n, nw, t_r, t_w = run_cases(seeds, cases(seeds))
    print(f"{n} files x {len(READ_TOOLS)} read tools in {t_r:.1f}s" + (f" ({JOBS} processes)" if JOBS > 1 else ""))
    print(f"{nw} parseable mutants x {len(WRITE_TOOLS)} writers in {t_w:.1f}s")
    new = []
    hits = {}
    for sig, (cnt, recipe, detail, tb, _o) in sorted(FAILS.items(), key=lambda kv: (-kv[1][0], kv[1][4])):
        bid = known_of(sig)
        if bid:
            hits.setdefault(bid, []).append((sig, cnt))
        else:
            new.append((sig, cnt, recipe, detail, tb))
    for bid, sigs in sorted(hits.items()):
        print(f"KNOWN BUG {bid}: " + "; ".join(f"{s[0]} {s[1]} @{s[2]} x{c}" for s, c in sigs[:4])
              + (f" (+{len(sigs) - 4} more)" if len(sigs) > 4 else ""))
    for bid in sorted(set(KNOWN) - set(hits)):
        print(f"known bug {bid} not seen in this run (fixed, or not reached at this mutant count)")
    by_tool = {}
    for (tool, kind, w), (cnt, *_r) in FAILS.items():
        by_tool[tool] = by_tool.get(tool, 0) + cnt
    if by_tool:
        print("failures by tool: " + ", ".join(f"{t} {c}" for t, c in sorted(by_tool.items(), key=lambda x: -x[1])))
    if new:
        for sig, cnt, recipe, detail, tb in new:
            print(f"\nNEW {sig[0]} | {sig[1]} | {sig[2]} | x{cnt}\n  first: {recipe}\n  {detail}")
            if os.environ.get("FLH_FUZZ_TB") == "1" and tb:
                print("  " + tb.strip().replace("\n", "\n  ")[-1500:])
        raise SystemExit(f"{len(new)} new failure signature(s)")
    print("ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--shard":
        shard_main(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    elif len(sys.argv) > 1 and sys.argv[1] == "--repro-write":
        repro_write(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None)
    elif len(sys.argv) > 1 and sys.argv[1] == "--repro":
        repro(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        main()
