"""First-run index scans on a slow disk (flh_catalog sample index, flh_flex FLEX index): one build at a time even
when the warm-up and a tool call both need it, progress with an estimate through _job_progress, a caller arriving
mid-build gets the partial index with a 'building the sample index (n%)' note (or waits and is told it waited), and
the cached index is reused afterwards. Slow disk simulated with gates; no FL needed. Run:
python3 tests/test_index_progress.py"""
import json, os, shutil, struct, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = os.path.join(tmp, "roots"); os.makedirs(os.environ["FLH_ROOTS"])
os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local"); os.environ["FLH_NO_WARM"] = "1"
flex = os.path.join(tmp, "FLEX"); os.makedirs(os.path.join(flex, "Packs")); os.environ["FLH_FLEX_DIR"] = flex
lib = os.path.join(tmp, "Samples")
for pack in ("A Drums", "B Drums", "C Drums", "D Drums"):
    for n in ("Kick 1", "Snare 1", "Hat 1"):
        p = os.path.join(lib, pack, n + ".wav"); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(b"RIFF")

import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
for mod in ("flh_store", "flh_catalog", "flh_flex"):
    __import__(mod).register(ns)
import flh_catalog as C  # noqa: E402
import flh_flex as F  # noqa: E402

PROG = []
ns["_job_progress"] = lambda **f: PROG.append(f)
C.PROGRESS_EVERY_S = 0.0
C.sample_roots = lambda: [("profile sample_folders", lib)]

# ---- index_build: single flight, waiter told it waited, partial for a short wait
gate = threading.Event(); runs = []


def slow_fn(b):
    runs.append(1); b["partial"] = ["x"]
    C.build_tick(b, 40)
    gate.wait(5)
    return ["x", "y"]


th = threading.Thread(target=lambda: C.index_build("t", "test index", slow_fn)); th.start()
while not runs:
    time.sleep(0.01)
time.sleep(0.05)
res, b = C.index_build("t", "test index", slow_fn, wait_s=0.1)
assert res is None and b["partial"] == ["x"] and "building the test index (40%" in C.build_note(b, True), b
assert "part indexed so far" in C.build_note(b, True)
assert "t" in C.building() and C.building()["t"]["pct"] == 40
threading.Timer(1.1, gate.set).start()
res2, b2 = C.index_build("t", "test index", slow_fn)                      # waits for the other one
assert res2 == ["x", "y"] and b2 is None and len(runs) == 1, runs           # built once
assert "waited" in (C.take_note() or "") and C.take_note() is None
th.join()
assert any(p.get("eta_s") is not None and p["pct"] == 40 for p in PROG), PROG

# ---- the sample index: progress per folder, partial results mid-scan, then the cache reused
walk = C._walk_samples; wgate = threading.Event(); seen = []


def gated_walk(root, cap, missed=None, tick=None):
    def t(out, frac):
        tick(out, frac); seen.append(frac)
        if len(seen) == 3:                                                    # 2 of 4 packs read: the disk stalls
            wgate.wait(10)
    return walk(root, cap, missed, t if tick else None)


C._walk_samples = gated_walk; PROG.clear()
bt = threading.Thread(target=C._sample_catalog); bt.start()
while len(seen) < 3:
    time.sleep(0.01)
C.SAMPLES_WAIT_S = 0.2
r = C.sample_search("kick", features=False)
assert r.get("index_note", "").startswith("building the sample index (") and "%" in r["index_note"], r
assert r["results"] and all(os.sep + "A Drums" in x["path"] or os.sep + "B Drums" in x["path"]
                            for x in r["results"]), r                       # what's indexed so far
s = C.fl_sound_catalog(type="sample")
assert s["count"] == 6 and "building the sample index" in s["index_note"], s
wgate.set(); bt.join(10)
pcts = [p["pct"] for p in PROG if "sample index" in str(p.get("step"))]
assert pcts and pcts == sorted(pcts) and max(pcts) >= 50 and any(p.get("eta_s") is not None for p in PROG), PROG
C._walk_samples = walk
full = C._sample_catalog()
assert len(full["entries"]) == 12 and not full.get("partial"), full
assert os.path.isfile(C._samples_cache_path())
C._SAMPLE_MEMO.pop("cat", None)
again = C._sample_catalog()
assert again["_cached"] is True and len(again["entries"]) == 12               # the cache is reused afterwards
assert "index_note" not in C.sample_search("kick", features=False)
assert [e["n"] for e in C._build_samples()["entries"]] == [e["n"] for e in full["entries"]]   # same without b


# ---- the FLEX index: progress per pack read, partial presets mid-scan, cache reused
def pack(path, names):
    ents = [(n, b"P" * 8) for n in names]
    tab = b""
    for n, data in ents:
        raw = n.encode() + b"\0"
        tab += struct.pack(">I", len(raw)) + raw + struct.pack(">QQ", 0, len(data))
    open(path, "wb").write(struct.pack(">II", 2, len(ents)) + tab + b"".join(d for _, d in ents))


for i in range(4):
    pack(os.path.join(flex, "Packs", f"Pack {i}.flexpack"), [f"Preset {i}a.flexpreset", f"Preset {i}b.flexpreset"])
rp = F.read_pack; fgate = threading.Event(); nread = []


def slow_read(path, *a, **k):
    nread.append(path)
    if len(nread) == 3:
        fgate.wait(10)
    return rp(path, *a, **k)


F.read_pack = slow_read; PROG.clear()
ft = threading.Thread(target=F.index); ft.start()
t_end = time.time() + 20
while len(nread) < 3:
    assert ft.is_alive() and time.time() < t_end, "the FLEX index thread stopped before its 3rd pack"
    time.sleep(0.01)
time.sleep(0.05)
fl = ns["TOOLS"]["flex_list"][0](limit=50)
assert "building the FLEX preset index (" in fl.get("index_note", "") and fl["total"] == 4, fl   # 2 packs read
fgate.set(); ft.join(10); F.read_pack = rp
assert [p["pct"] for p in PROG if "FLEX" in str(p.get("step"))][:2] == [25, 50], PROG
F._MEMO.clear()
fl2 = ns["TOOLS"]["flex_list"][0](limit=50)
assert fl2["total"] == 8 and "index_note" not in fl2, fl2
F._MEMO.clear(); n0 = len(nread)
assert len(F.index()) == 8 and len(nread) == n0                               # every pack from the cache: none read

# ---- a job's own step comes back after an index call (it doesn't stay 'building the sample index (47%)')
PROG.clear(); ns["_job_progress_get"] = lambda: {"step": "check 3/4: lazy reload", "pct": 50, "at": 3.0}
C.index_build("t2", "test index", lambda b: (C.build_tick(b, 47, force=True), ["z"])[1])
assert PROG[0]["step"].startswith("building the test index") and PROG[-1] == {"step": "check 3/4: lazy reload",
                                                                               "pct": 50}, PROG
ns.pop("_job_progress_get")

shutil.rmtree(tmp, True)
print("ok")
