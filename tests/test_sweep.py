"""fl_sweep (flh_sweep) with fl_pc_check / fl_bench / the usage log simulated (their runners monkeypatched): the ranked
fix list (crash > fail > regression > warn > slow; new before old; riskier first), the compare with the previous sweep
(new fail / still failing / fixed / newly passing / newly warning; bench slower only beyond 15 % AND 2 s; renders up;
tool errors grew), first_seen carried over, keep-10 pruning that only touches our own sweep folders, a share zip
without paths / song / user names (fake identities) and without pictures unless share_pictures, dry_run writing
nothing, stop between steps, status / report, and a run off Windows (FL steps skipped with a note).
Run: python3 tests/test_sweep.py"""
import atexit, json, os, shutil, sys, tempfile, threading, zipfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
ROOT = os.path.join(tmp, "music"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = ROOT; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
os.environ["USERNAME"] = "Zoltan.Kovacs"
atexit.register(shutil.rmtree, tmp, True)
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
import flh_pccheck as PC  # noqa: E402
import flh_bench as B  # noqa: E402
import flh_sweep as S  # noqa: E402
for mod, tool in ((PC, "fl_pc_check"), (B, "fl_bench"), (S, "fl_sweep")):
    if tool not in ns["TOOLS"]:
        mod.register(ns)
assert "flh_sweep" in m.EXTENSIONS and "fl_sweep" in m.CROSS_PLATFORM
assert not m._touches_fl("fl_sweep"), "stop / status never wait for the FL lock the sweep's job holds"
STATE = os.path.dirname(m.STATE); SWEEPS = os.path.join(STATE, "sweeps")
SECRET = r"C:\Users\Zoltan.Kovacs\Music\Secret Song\Secret Song.flp"
SECRET_UNIX = os.path.join(ROOT, "Secret Song", "Secret Song.flp")


def sweep(**kw):
    r = S.fl_sweep(**kw)
    assert r.get("job"), r
    j = m.fl_job(r["job"], wait_s=60)
    assert j["status"] == "done", j
    return j["result"]


# ----------------------------------------------------------------------------- simulated pc_check / bench / usage
SC = {}
CALLS = []


def check(id_, status, risk=3, detail="", **kw):
    return dict({"id": id_, "title": id_.replace(".", " "), "area": id_.split(".")[0], "risk": risk, "status": status,
                 "detail": detail or status, "seconds": 3.0}, **kw)


USERS = []                                                        # the 'user' hand-through each run got


def fake_pc_run(sel, mod_err, unattended=False, keep_sandbox=False, user=None):
    CALLS.append(("pc", bool(unattended), len(sel)))
    USERS.append(("pc", dict(user) if isinstance(user, dict) else user))
    if isinstance(user, dict) and "original" not in user:        # the first run fills it with FL's song at its start
        user["original"] = SC.get("start_song", SECRET_UNIX)
    if SC.get("pc_block"):
        SC["pc_started"].set()
        assert PC._RUN["stop"].wait(10), "stop reaches the running check"
    d = os.path.join(STATE, "pc_check", "x%03d" % len(CALLS)); os.makedirs(d, exist_ok=True)
    pic = os.path.join(d, "01_slide.png"); open(pic, "wb").write(b"\x89PNG fake")
    rows = [dict(r) for r in SC["checks"]]
    for r in rows:
        if r.get("look") == "PIC":
            r["look"] = pic
    rep = {"checks": rows, "your_song": SECRET_UNIX, "summary": {}}
    for st in ("pass", "fail", "warn", "skip"):
        rep["summary"][st] = sum(1 for r in rows if r["status"] == st)
    jp, mp = os.path.join(d, "report.json"), os.path.join(d, "report.md")
    json.dump(rep, open(jp, "w")); open(mp, "w").write(f"# report\nyour song {SECRET_UNIX} and {SECRET}\n")
    return {"summary": rep["summary"], "report": jp, "report_md": mp,
            **({"stopped": "stopped with fl_pc_check(stop=true)"} if PC._RUN["stop"].is_set() else {}),
            **({"sandbox_not_recycled": SC["pc_left"]} if SC.get("pc_left") else {})}


def fake_bench_run(sel, keep=False, compare=None, cores=None, user=None):
    CALLS.append(("bench", cores))
    USERS.append(("bench", dict(user) if isinstance(user, dict) else user))
    if isinstance(user, dict) and "original" not in user:
        user["original"] = os.path.join(ROOT, "_fl-headless-check", "x", "icon probe_2.flp")   # what FL shows now
    rows = SC["slow" if cores else "normal"]
    d = B._bench_dir(); os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "2026%04d.json" % len(CALLS))
    data = {"speed": "slow (2 cores)" if cores else "normal", "jobs": rows, "your_song": SECRET_UNIX,
            "sandbox": os.path.join(ROOT, "_fl-headless-check", "bench-x")}
    json.dump(data, open(path, "w"))
    return {"summary": {"pass": sum(1 for j in rows if j["status"] == "pass")}, "jobs": rows, "file": path,
            "speed": data["speed"]}


def job(id_, status, seconds, renders=0, detail=None):
    return {"id": id_, "status": status, "seconds": seconds, "renders": renders, **({"detail": detail} if detail else {})}


PC._run = fake_pc_run
B._run = fake_bench_run
PC._quick_precheck = lambda: None
ns["fl_running"] = lambda: True
USAGE = {"tools": {}}
ns["_usage_totals"] = lambda: {"since": "2026-10-01", "tools": dict(USAGE["tools"])}
m.IS_WINDOWS = True

# ----------------------------------------------------------------------------- dry run: plan, minutes, writes nothing


def tree():
    out = []
    for dp, dn, fn in os.walk(tmp):
        out += [os.path.join(dp, f) for f in fn] + [os.path.join(dp, x) for x in dn]
    return sorted(out)


before = tree()
d = S.fl_sweep(dry_run=True)
assert d["dry_run"] and [p["step"] for p in d["plan"]] == ["pc_check", "bench_normal", "bench_slow", "usage", "report"]
pcm = d["plan"][0]["minutes"]; bm = d["plan"][1]["minutes"]; sm = d["plan"][2]["minutes"]
assert abs(d["estimated_minutes"] - (pcm + bm + sm + 0.1)) < 0.2 and sm > bm > 0 and pcm > 0, d
assert d["you_must_do"] == [] and "nothing" in d["you_must_do_note"] and d["compare_with"].startswith("nothing"), d
q = S.fl_sweep(dry_run=True, full=False, slow=False)
assert q["plan"][0]["checks"] < d["plan"][0]["checks"] and "bench_slow" not in [p["step"] for p in q["plan"]]
assert tree() == before, "dry_run writes nothing"
assert S.fl_sweep(stop=True)["stopped"] is False and S.fl_sweep(status=True)["last"] is None

# ----------------------------------------------------------------------------- sweep A
ns["_ERRORS"].append({"t": "x", "tool": "fl_render", "error": f"FileNotFoundError: {SECRET}",
                      "traceback": f'Traceback\n  File "{os.path.join(ROOT, "x", "fl_headless_mcp.py")}", line 3\n  '
                                   f'x\nFileNotFoundError: {SECRET}'})
SC.update(checks=[check("core.identity_save", "fail", 5, f"FL had {SECRET} open", fix_hint="see _open_project_guess",
                        checklist="checklists/core.md#identity"),
                  check("lazy.big_project", "fail", 4), check("files.slide_flag", "warn", 3, look="PIC"),
                  check("render.in_fl", "pass", 5), check("files.markers_meter", "pass", 2)],
          normal=[job("beat", "pass", 30.0, 1), job("rework_34", "pass", 100.0, 1), job("ending", "pass", 5.0),
                  job("mix_tweak", "pass", 50.0), job("knob", "fail", 4.0, 0, "no compressor preset")],
          slow=[job("beat", "pass", 100.0, 1), job("rework_34", "pass", 150.0, 1)])
USAGE["tools"] = {"fl_render": {"calls": 10, "errors": 3}, "flp_compose": {"calls": 5, "errors": 0}}
A = sweep()
assert [c[0] for c in CALLS] == ["pc", "bench", "bench"] and CALLS[0][1] is True and CALLS[2][1] == B.SLOW_CORES
# item 1: the song found by the first run is handed to the later runs (they never take what FL shows then)
assert USERS == [("pc", {}), ("bench", {"original": SECRET_UNIX}), ("bench", {"original": SECRET_UNIX})], USERS
assert not PC._RUN["running"] and not B._RUN["running"] and not S._RUN["running"]
ids = [t["id"] for t in A["top"]]
assert ids == ["check:core.identity_save", "check:lazy.big_project", "bench:normal:knob", "check:files.slide_flag",
               "tool:fl_render", "bench:slow_x:beat"], ids
kinds = [t["kind"] for t in A["top"]]
assert kinds == ["fail", "fail", "fail", "warn", "warn", "slow"], kinds
t0 = A["top"][0]
assert t0["fix_hint"] == "see _open_project_guess" and t0["checklist"] == "checklists/core.md#identity"
assert t0["evidence"]["row"] == "core.identity_save" and t0["evidence"]["report"].endswith("report.json")
assert A["top"][3]["evidence"]["pictures"][0].endswith("01_slide.png"), A["top"][3]
assert A["summary"]["by_kind"] == {"crash": 0, "fail": 3, "regression": 0, "warn": 2, "slow": 1}, A["summary"]
assert A["compare"]["against"] is None
STAMP_A = os.path.basename(A["paths"]["folder"])
assert all(t["first_seen"] == STAMP_A for t in A["top"])
for k in ("sweep_json", "sweep_md", "zip"):
    assert os.path.isfile(A["paths"][k]), k
md = open(A["paths"]["sweep_md"]).read()
assert len(md) < 6000 and "core.identity_save" in md and "## Next" in md
assert any("fix first: check:core.identity_save" in n for n in A["next"]), A["next"]

# ----------------------------------------------------------------------------- sweep B: the compare
SC.update(checks=[check("core.identity_save", "fail", 5), check("lazy.big_project", "pass", 4),
                  check("files.slide_flag", "warn", 3), check("render.in_fl", "fail", 5),
                  check("files.markers_meter", "warn", 2), check("files.backups", "pass", 2)],
          normal=[job("beat", "pass", 36.0, 1),          # +6 s, +20 %: slower
                  job("rework_34", "pass", 110.0, 2),     # +10 %: same time, but one render more
                  job("ending", "pass", 6.5),             # +30 % but +1.5 s: noise
                  job("mix_tweak", "pass", 56.0),         # +6 s but +12 %: noise
                  job("knob", "pass", 4.0)],
          slow=[job("beat", "pass", 60.0, 1), job("rework_34", "pass", 150.0, 1)])
USAGE["tools"] = {"fl_render": {"calls": 14, "errors": 5}, "flp_compose": {"calls": 9, "errors": 1},
                  "flp_info": {"calls": 3, "errors": 0}}
Bres = sweep()
c = Bres["compare"]
assert c["against"] == STAMP_A
assert c["checks"] == {"newly_failing": ["render.in_fl"], "still_failing": ["core.identity_save"],
                       "fixed": ["lazy.big_project"], "newly_passing": ["files.backups"],
                       "newly_warning": ["files.markers_meter"]}, c["checks"]
bc = {(r["speed"], r["job"]): r for r in c["bench"]}
assert bc[("normal", "beat")]["verdict"] == "slower" and bc[("normal", "rework_34")]["renders"] == "1 -> 2"
assert ("normal", "ending") not in bc and ("normal", "mix_tweak") not in bc, "within noise: not listed"
assert bc[("slow", "beat")]["verdict"] == "faster"
assert [g["tool"] for g in c["tool_errors_grew"]] == ["fl_render", "flp_compose"], c["tool_errors_grew"]
ids = [t["id"] for t in Bres["top"]]
assert ids == ["check:render.in_fl", "check:core.identity_save", "regress:normal:beat", "check:files.markers_meter",
               "tool:fl_render", "renders:normal:rework_34", "tool:flp_compose", "check:files.slide_flag"], ids
first = {t["id"]: t["first_seen"] for t in Bres["top"]}
STAMP_B = os.path.basename(Bres["paths"]["folder"])
assert first["check:core.identity_save"] == STAMP_A and first["check:files.slide_flag"] == STAMP_A
assert first["check:render.in_fl"] == STAMP_B and STAMP_B != STAMP_A
assert "check:lazy.big_project" not in ids and "bench:normal:knob" not in ids, "fixed items are gone"
full = S.fl_sweep(report="last")
assert full["report"] == Bres["paths"]["sweep_json"] and len(full["fixes"]) == len(ids)
assert S.fl_sweep(report=Bres["paths"]["folder"])["report"] == full["report"]
assert S.fl_sweep(report=STAMP_A)["summary"]["by_kind"]["fail"] == 3
st = S.fl_sweep(status=True)
assert st["running"] is False and st["last"] == full["report"] and len(st["top"]) == 5

# compare unit: thresholds exactly at the edges are noise; a job that failed before isn't compared
cur = {"bench": {"normal": {"jobs": [job("beat", "pass", 12.0), job("knob", "pass", 30.0), job("x", "pass", 23.1)]}}}
prv = {"stamp": "p", "bench": {"normal": {"jobs": [job("beat", "pass", 10.0), job("knob", "fail", 3.0),
                                                   job("x", "pass", 20.0)]}}}
rows = {r["job"]: r for r in S.compare(cur, prv)["bench"]}
assert rows["beat"]["verdict"] == "same" and rows["x"]["verdict"] == "slower", rows       # +2.0 s isn't > 2 s
assert rows["knob"]["verdict"].startswith("not compared")

# ----------------------------------------------------------------------------- the share zip: privacy
z = zipfile.ZipFile(Bres["paths"]["zip"])
names = z.namelist()
assert {"sweep.json", "sweep.md", "pc_check/report.md", "bench/normal.json", "bench/slow.json"} <= set(names), names
assert any(n.startswith("bug_report/") for n in names) and not any(n.startswith("pictures/") for n in names)
assert not any(n.endswith((".flp", ".wav", ".png", ".flac")) for n in names)
blob = "".join(z.read(n).decode("utf-8") for n in names)
low = blob.lower()
for bad in ("zoltan", "kovacs", "secret song", tmp.lower(), ROOT.lower(), "\\users\\"):
    assert bad not in low, (bad, [n for n in names if bad in z.read(n).decode().lower()])
assert "<path>" in blob and "<state>/pc_check" in blob and "fl_headless_mcp.py" in blob, "code file names kept"
assert not [f for f in os.listdir(os.path.join(STATE, "bug_reports")) if f.startswith("flh-bug-")], \
    "the bug report's own zip is folded into ours (one zip to share)"
assert S.share_scrub("see C:\\Users\\Ann\\My Beats\\Hit.flp, then") == "see <path>.flp, then"
assert S.share_scrub("at /home/ann/My Beats/Hit (v2).flp: bad").startswith("at <path>")

# share_pictures=true adds the checks' pictures
SC["checks"] = [check("files.slide_flag", "warn", 3, look="PIC")]
P = sweep(bench=False, slow=False, share_pictures=True)
assert any(n.startswith("pictures/") for n in zipfile.ZipFile(P["paths"]["zip"]).namelist())

# item 1: a sweep that started on FL's empty project hands None through (the benches don't fill it from FL); a
# sandbox that couldn't be recycled is a fix item with its path (scrubbed from the share zip)
USERS.clear(); SC["start_song"] = None
LEFT = os.path.join(ROOT, "_fl-headless-check", "20261023-120000")
SC["pc_left"] = {"path": LEFT, "why": "FL still has 'icon probe_2' from it open"}
Q = sweep()
assert USERS == [("pc", {}), ("bench", {"original": None}), ("bench", {"original": None})], USERS
qj = json.load(open(Q["paths"]["sweep_json"]))
it = next(f for f in qj["fixes"] if f["id"] == "cleanup:pc_check")
assert it["kind"] == "warn" and "icon probe_2" in it["what"] and it["evidence"]["path"] == LEFT, it
assert ROOT.lower() not in "".join(zipfile.ZipFile(Q["paths"]["zip"]).read(n).decode("utf-8")
                                   for n in zipfile.ZipFile(Q["paths"]["zip"]).namelist()).lower()
SC.pop("pc_left"); SC.pop("start_song")

# ----------------------------------------------------------------------------- keep 10: only our own folders
os.makedirs(os.path.join(SWEEPS, "my notes")); open(os.path.join(SWEEPS, "my notes", "a.txt"), "w").write("x")
os.makedirs(os.path.join(SWEEPS, "20200101-000000"))            # a <time> name but no sweep.json: not ours
open(os.path.join(SWEEPS, "readme.txt"), "w").write("keep me")
OUTSIDE = os.path.join(STATE, "20190101-000000"); os.makedirs(OUTSIDE)
open(os.path.join(OUTSIDE, "sweep.json"), "w").write("{}")
for _ in range(10):
    sweep(bench=False, slow=False)
own = S._own_dirs()
assert len(own) == 10, len(own)
assert os.path.basename(own[0]) != STAMP_A and not os.path.isdir(os.path.join(SWEEPS, STAMP_A)), "oldest pruned"
assert os.path.isfile(os.path.join(SWEEPS, "my notes", "a.txt")) and os.path.isdir(os.path.join(SWEEPS, "20200101-000000"))
assert os.path.isfile(os.path.join(SWEEPS, "readme.txt")) and os.path.isfile(os.path.join(OUTSIDE, "sweep.json"))
assert [S._order(os.path.basename(x)) for x in own] == sorted(S._order(os.path.basename(x)) for x in own)

# ----------------------------------------------------------------------------- crash: FL closed during a check
SC["checks"] = [check("render.in_fl", "fail", 5, "FL closed or crashed during 'render.in_fl'. boom")]
ns["fl_running"] = lambda: False
CALLS.clear()
Cr = sweep()
assert Cr["top"][0]["kind"] == "crash" and Cr["top"][0]["id"] == "check:render.in_fl", Cr["top"][:2]
assert any(t["id"].startswith("crash:fl_gone_before_bench") for t in Cr["top"]) and [c[0] for c in CALLS] == ["pc"]
ns["fl_running"] = lambda: True

# a step raising never sinks the sweep: a crash item
PC._run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("harness bug"))
Rx = sweep(bench=False, slow=False)
assert Rx["top"][0]["id"] == "crash:pc_check" and "harness bug" in Rx["top"][0]["what"], Rx["top"][0]
PC._run = fake_pc_run

# ----------------------------------------------------------------------------- stop between steps
SC.update(pc_block=True, pc_started=threading.Event(), checks=[check("render.in_fl", "pass", 5)])
CALLS.clear()
r = S.fl_sweep()
assert SC["pc_started"].wait(10)
s1 = S.fl_sweep(status=True)
assert s1["running"] and "pc_check" in (s1["step"] or ""), s1
assert S.fl_sweep(stop=True)["stopping"] == r["job"]
res = m.fl_job(r["job"], wait_s=30)["result"]
assert res["stopped"] and [c[0] for c in CALLS] == ["pc"], (res, CALLS)
assert not PC._RUN["stop"].is_set() and not PC._RUN["running"], "pc_check's stop flag cleared after the sweep"
SC["pc_block"] = False
assert S.fl_sweep(status=True)["running"] is False

# refused while fl_pc_check / fl_bench runs
PC._RUN["running"] = True
assert S.fl_sweep()["started"] is False
PC._RUN["running"] = False

# ----------------------------------------------------------------------------- what pc_check leaves for the bench
# PC 2026-10-23: bench mix_tweak failed inside the sweep and passed alone: the sweep kept fl_pc_check's 'running'
# flag on through the bench, which also switches the render cache off -> fl_preview could never reuse a render.
import fl_render_cache as RCm  # noqa: E402
SEEN = {}
SC.update(checks=[check("render.in_fl", "pass", 5)], normal=[job("mix_tweak", "pass", 9)], slow=[job("mix_tweak", "pass", 20)])


def spying_pc(*a, **k):
    SEEN["pc_running_in_checks"] = PC._RUN["running"]
    return fake_pc_run(*a, **k)


def spying_bench(sel, keep=False, compare=None, cores=None, user=None):
    SEEN.setdefault("bench", []).append({"pc_running": PC._RUN["running"], "cache_on": RCm.enabled(),
                                         "fl_bench": B.fl_bench(), "fl_pc_check": PC.fl_pc_check()})
    return fake_bench_run(sel, keep, compare, cores, user)
PC._run, B._run = spying_pc, spying_bench
ns["_scaled"] = lambda s, min_s=None: s
_cache_env = os.environ.pop("FLH_RENDER_CACHE_GB", None)
try:
    sweep()
finally:
    PC._run, B._run = fake_pc_run, fake_bench_run
assert SEEN["pc_running_in_checks"] is True, "the checks still run with the render cache off"
for b in SEEN["bench"]:
    assert b["pc_running"] is False and b["cache_on"] is True, SEEN
    assert b["fl_bench"]["started"] is False and "fl_sweep is running" in b["fl_bench"]["note"], b
    assert b["fl_pc_check"]["started"] is False and "fl_sweep is running" in b["fl_pc_check"]["note"], b
assert not PC._RUN["running"]

# fl_pc_check's sandbox songs: a background flush still waiting for FL is forgotten with the sandbox
sbx = os.path.join(ROOT, "_fl-headless-check", "20261023-000000")
m._AUTOSYNC["k-sbx"] = {"project": os.path.join(sbx, "songs", "x.flp"), "due": 0}
m._AUTOSYNC["k-user"] = {"project": SECRET_UNIX, "due": 0}
PC._forget_sandbox(sbx)
assert "k-sbx" not in m._AUTOSYNC and "k-user" in m._AUTOSYNC, m._AUTOSYNC
m._AUTOSYNC.clear()

# PC 2026-10-23: the slow bench ran 0 jobs (stopped in its preflight) and the sweep said nothing. Now: FL settles,
# one more try; when it still can't run, the stop reason is a fix item, in the summary and in 'next'.
S.SETTLE_S, S.SETTLE_POLL_S = 0.2, 0.05
ns["_fl_blocking_popup"] = lambda: None; ns["_fl_still_working"] = lambda: False; ns["_main_title"] = lambda: "x"
TRIES = []


def flaky_bench(sel, keep=False, compare=None, cores=None, user=None):
    TRIES.append(cores)
    if cores and SC.get("slow_stops", 0) > 0:
        SC["slow_stops"] -= 1
        return {"summary": {"pass": 0, "fail": 0, "skip": 0}, "jobs": [], "file": None, "speed": "slow (2 cores)",
                "stopped": "FL is still starting or loading a project: wait until its title shows the project"}
    return fake_bench_run(sel, keep, compare, cores, user)
B._run = flaky_bench
try:
    SC["slow_stops"] = 1                                          # stopped once: the retry runs the jobs
    R6 = sweep()
    assert TRIES == [None, 2, 2], TRIES
    assert R6["summary"]["bench"]["slow"] == {"pass": 1}, R6["summary"]
    sj = json.load(open(R6["paths"]["sweep_json"]))
    assert "FL is still starting" in sj["bench"]["slow"]["retry"]["first_try"], sj["bench"]["slow"]
    assert not any(f["id"] == "bench:slow:not_run" for f in sj["fixes"])
    TRIES.clear(); SC["slow_stops"] = 2                           # stopped twice: said, not hidden
    R7 = sweep()
    assert TRIES == [None, 2, 2], TRIES
    assert R7["summary"]["bench"]["slow"]["jobs"] == 0 and "still starting" in R7["summary"]["bench"]["slow"]["stopped"]
    sj = json.load(open(R7["paths"]["sweep_json"]))
    item = next(f for f in sj["fixes"] if f["id"] == "bench:slow:not_run")
    assert item["kind"] == "fail" and "ran no jobs" in item["what"], item
    assert any("bench (slow) ran no jobs" in n for n in R7["next"]), R7["next"]
finally:
    B._run = fake_bench_run
    if _cache_env is not None:
        os.environ["FLH_RENDER_CACHE_GB"] = _cache_env

# ----------------------------------------------------------------------------- off Windows: FL steps skipped
m.IS_WINDOWS = False
CALLS.clear()
W = sweep()
assert CALLS == [] and any("not Windows" in n for n in W["notes"]), W
assert "not Windows" in str(W["summary"]["pc_check"]) and "not Windows" in str(W["summary"]["bench"]["normal"])
assert os.path.isfile(W["paths"]["zip"]) and W["compare"]["against"], "usage + compare still run"
dw = S.fl_sweep(dry_run=True)
assert dw["estimated_minutes"] <= 0.2 and "not Windows" in dw["note"]
print("ok")
