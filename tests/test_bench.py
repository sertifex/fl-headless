"""fl_bench (flh_bench) against a simulated FL: every sub-tool is mocked through ns["TOOLS"] (same schemas, so the
dispatcher's argument checks still run). Covers: dry run anywhere (plan, fixed order, estimate, unknown job), the
Windows-only refusal, a full run (fixed job order, 'job i/n' progress, steps_s and seconds measured, renders counted
from the server's render counters - also the background balance's, which needs the bench to let go of the FL lock -,
reused previews, the temporary bench profile active only inside the rule jobs and the user's profile untouched, the
JSON result saved, sandbox recycled, the user's song restored), failure isolation (a job raising doesn't stop the
others; the ending job then makes its own final), compare='last' and compare=[a, b], a balance that never starts,
FL gone mid-run, an interrupted earlier run recovered. Run: python3 tests/test_bench.py"""
import atexit, io, json, os, shutil, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
ROOT = os.path.join(tmp, "music"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = ROOT; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
atexit.register(shutil.rmtree, tmp, True)
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
import flh_bench as B  # noqa: E402
import flh_pccheck as PC  # noqa: E402
import flh_store  # noqa: E402
if "fl_pc_check" not in ns["TOOLS"]:
    PC.register(ns)
if "flh_profile" not in ns["TOOLS"]:
    flh_store.register(ns)
B.register(ns)
call = ns["_flh_call"]

# ----------------------------------------------------------------------------- dry run / refusal (not Windows)
m.IS_WINDOWS = False
d = call("fl_bench", {"dry_run": True})
assert d["dry_run"] and [j["id"] for j in d["jobs"]] == ["beat", "rework_34", "ending", "mix_tweak", "knob"], d
assert 7 <= d["estimated_minutes"] <= 12, d["estimated_minutes"]
d2 = call("fl_bench", {"dry_run": True, "jobs": ["knob", "beat"]})
assert [j["id"] for j in d2["jobs"]] == ["beat", "knob"], d2                  # always the fixed order
d3 = call("fl_bench", {"dry_run": True, "jobs": "ending"})
assert d3["jobs"][0]["minutes"] > 1 and d3["estimated_minutes"] > d2["estimated_minutes"] - 2, d3   # ending alone
try:
    call("fl_bench", {"dry_run": True, "jobs": ["warp"]}); raise SystemExit("unknown job must be refused")
except ValueError as e:
    assert "warp" in str(e)
try:
    call("fl_bench", {}); raise SystemExit("the run must need Windows")
except RuntimeError as e:
    assert "dry_run" in str(e)
assert "fl_bench" in m.listed_tools(), "fl_bench is listed"

# ----------------------------------------------------------------------------- the simulated FL
m.IS_WINDOWS = True
LOG = []; PROG = []
FL = {"running": True, "holds": None, "changed": False, "thresh": "0.0 dB", "comp": False, "balance": True,
      "compose_raises": False, "die_after_new_song": False, "lock_got": []}
USER = os.path.join(ROOT, "Mine", "Mine.flp"); os.makedirs(os.path.dirname(USER)); open(USER, "wb").write(b"FLhd-user")
FINAL_ROOT = os.path.join(ROOT, "Album"); os.makedirs(FINAL_ROOT)
flh_store.profile_set("My Songs", {"final_root": FINAL_ROOT, "final_name": "{song} (Piano) - FINAL.flac",
                                   "rules": {"min_ending_s": 40}, "fl_edition": "producer", "style": "piano-led"})
PROF_FILE = flh_store._profile_path("My Songs"); PROF_BYTES = open(PROF_FILE, "rb").read()
RESTORED = []
SANDBOXES = os.path.join(ROOT, B.SANDBOX_DIR)
PROFILES_SEEN = []                                                # the profile each sub-tool saw (item 2)


def in_sandbox_finals(p):
    return str((p or {}).get("final_root") or "").startswith(SANDBOXES + os.sep) and \
        os.path.basename(p["final_root"]) == "finals"


def see_profile(where):
    p = flh_store.profile_get()
    PROFILES_SEEN.append((where, p.get("name"), p.get("final_root"), p.get("final_name"), p.get("style"),
                          p.get("fl_edition")))


def log(*a):
    LOG.append(a)


def fake(name, fn):
    t = ns["TOOLS"][name]
    ns["TOOLS"][name] = (fn, t[1], t[2])


def bench_job():
    return next((j for j in ns["JOBS"].values() if j["job"].startswith("bench") and j["status"] == "running"), None)


def profile_is_bench():
    p = flh_store.profile_get()
    return p.get("name") == B.BENCH_PROFILE and p.get("rules") == B.BENCH_RULES and in_sandbox_finals(p) \
        and p.get("fl_edition") == "producer" and not p.get("final_name") and not p.get("style")
    # (the user's user-wide settings, the bench's rules, finals inside the sandbox, none of the collection's taste)


def new_song(genre=None, name=None, folder=None, seed=None, open=True, balance=True, starter=True, arrange=True,
             bpm=None, **kw):
    log("flh_new_song", genre, name, bool(open), bool(balance))
    see_profile("flh_new_song")
    p = os.path.join(folder, name, name + ".flp"); os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "wb") as f:                                    # (the argument 'open' hides the builtin here)
        f.write(b"FLhd-" + str(genre).encode())
    flh_store.journal_update(p, set={"genre": genre, "channels": {"3": {"role": "keys"}, "0": {"role": "kick"}}})
    time.sleep(0.05)
    out = {"project": p, "steps_s": {"build": 0.05}}
    if open:
        FL["holds"] = p
    if open and balance and FL["balance"]:
        def bal():
            got = ns["_FL_LOCK"].acquire(timeout=10)
            FL["lock_got"].append(got)
            try:
                if got:
                    time.sleep(0.3); ns["_note_export"]("in_fl", 1.0)
                ns["_AUTOSYNC_NEWS"].append(f"{os.path.basename(p)}: " + ("levels balanced in 0.3 s (1 export(s))"
                                                                          if got else "level balance stopped (busy)"))
            finally:
                if got:
                    ns["_FL_LOCK"].release()
        threading.Thread(target=bal, daemon=True, name="flh-balance").start()
        out["levels"] = "being balanced in the background"
    elif balance:
        out["levels"] = "balance pending: runs in the background the first time fl_open opens this song"
    if FL["die_after_new_song"]:
        FL["running"] = False
    return out


fake("flh_new_song", new_song)
fake("flp_playlist", lambda project, action="list", clips=None, **k: (log("flp_playlist", action),
                                                                      {"clips": [{"id": 1}]} if action == "list" else {})[1])
fake("flp_project_info", lambda path, output=None, **k: log("flp_project_info", k.get("time_sig_num")) or {"ok": 1})
fake("flp_edit", lambda project, ops, output=None: log("flp_edit", ops[0]["tool"], len(ops[0]["notes"])) or {"ok": 1})
fake("flp_tempo_write", lambda project, output=None, points=None, **k: log("flp_tempo_write", points) or {"ok": 1})


def compose(project, what, roles=None, bars=None, **k):
    log("flp_compose", what, tuple(roles or ()), bars)
    j = bench_job()
    PROG.append((j or {}).get("progress", {}).get("step"))
    if FL["compose_raises"]:
        raise RuntimeError("compose broke")
    time.sleep(0.1)
    return {"channels": {"low strings": 7, "strings": 8, "choir": 9}, "verified": True}


fake("flp_compose", compose)
fake("flp_perform_setup", lambda project, **k: log("flp_perform_setup", k.get("style")) or {"ok": 1})
fake("flp_perform", lambda project, **k: log("flp_perform") or {"ok": 1})


def extend(project, **k):
    log("flp_extend_ending", os.path.basename(project), profile_is_bench())
    return {"mode": "rules", "hold": {"main_s": 30.0}, "held_notes": 5}


fake("flp_extend_ending", extend)


def finalize(project=None, background=True, rules=None, **k):
    log("fl_finalize", os.path.basename(project), background, rules, profile_is_bench())
    see_profile("fl_finalize")
    time.sleep(0.1); ns["_note_export"]("in_fl", 2.0)
    out = os.path.join(os.path.dirname(project), "x - FINAL.flac"); open(out, "wb").write(b"fLaC")
    return {"ok": True, "render_method": "in_fl", "finals": [{"path": out, "rules": {"pass": True, "checks": [
        {"rule": "ending_length", "pass": True, "value": 22.0, "limit": 20.0},
        {"rule": "tail_ring", "pass": True, "value": 9.0, "limit": 5.0}]}}]}


fake("fl_finalize", finalize)
fake("fl_ending_check", lambda project, rules=None, render=True: log("fl_ending_check", render, rules) or
     {"predicted": "pass", "hold_s": 30.0, "need_hold_s": 27.0, "why": "from the notes"})
fake("fl_mixer_set", lambda track, **k: (log("fl_mixer_set", track, k.get("volume_db")), FL.update(changed=True),
                                         {"track": track})[2])


def preview(**k):
    log("fl_preview")
    see_profile("fl_preview")
    time.sleep(0.05)
    if FL["changed"]:
        FL["changed"] = False; ns["_note_export"]("in_fl", 1.0); return {"lufs": -14.0}
    ns["_note_reuse"](); return {"lufs": -14.0, "reused": "reused render"}


fake("fl_preview", preview)


def shape(channel, knobs=None, plugin=None, **k):
    log("fl_sound_shape", channel, json.dumps(knobs), plugin)
    FL["thresh"] = "-36.0 dB"
    return {"channel": channel, "set": [f"{plugin}: Threshold 0 dB -> -36.0 dB"]}


fake("fl_sound_shape", shape)
fake("fl_plugin_params", lambda index, slot=-1, filter=None: {"params": [{"param": 1, "name": "Threshold",
                                                                          "text": FL["thresh"]}]})
fake("flp_load_plugin", lambda project, output=None, preset=None, track=None, slot=None, **k:
     (log("flp_load_plugin", track, slot), FL.update(comp=True), {"output": output})[2])


def fl_state():
    fx = [{"slot": 0, "name": "Fruity Parametric EQ 2"}] + ([{"slot": 1, "name": "Fruity Compressor"}] if FL["comp"]
                                                             else [])
    return {"channels": [{"index": 0, "name": "Kick", "insert": 0}, {"index": 3, "name": "Keys", "insert": 4}],
            "mixer": [{"index": 0, "name": "Master", "volume_db": 0.0, "effects": [{"slot": 0, "name": "Fruity Limiter"}]},
                      {"index": 4, "name": "Keys", "volume_db": -1.0, "effects": fx}]}


ns["fl_state"] = fl_state
ns["fl_running"] = lambda: FL["running"]
ns["_fl_pids"] = lambda: [1234] if FL["running"] else []
ns["fl_dialogs"] = lambda: []
ns["files_recycle"] = lambda paths: ([shutil.rmtree(p, True) if os.path.isdir(p) else os.remove(p)
                                      for p in paths if os.path.exists(p)], {"recycled": paths})[1]
import flh_newsong as N  # noqa: E402
PRESET = os.path.join(tmp, "Fruity Compressor.fst"); open(PRESET, "wb").write(b"fst")
N._find_plugin = lambda name: {"default": PRESET} if name == "Fruity Compressor" else None
PC._quick_precheck = lambda: None


def opened(ctx, path):
    log("open", os.path.basename(path)); FL["holds"] = path; ctx.opened.append(path); time.sleep(0.02)
    return {"load_s": 0.02}


B._preflight = lambda ctx, user=None: {"original": USER, "title": "Mine - FL Studio", "pids": [1234]}
B._open = opened
ns["_open_project_guess"] = lambda: (FL["holds"], [])             # FL's answer: the project it holds
PC.RECYCLE_RETRY_S = 0.0


def restore(ctx, pre, fl_gone):
    RESTORED.append((pre["original"], bool(fl_gone), list(ctx.opened)))
    FL["holds"] = pre["original"]
    return {"ok": not fl_gone, "reopened": pre["original"]}


B._restore = restore


def run(args, wait=60):
    r = call("fl_bench", args)
    assert r.get("job"), r
    assert r["estimated_minutes"] > 0
    assert ns["_JOB_EVENTS"][r["job"]].wait(wait), "the bench job didn't finish"
    j = ns["JOBS"][r["job"]]
    assert j["status"] == "done", j
    return j["result"]


def by_id(res):
    return {x["id"]: x for x in res["jobs"]}


# ----------------------------------------------------------------------------- a full run: all pass
t0 = time.time()
res = run({})
J = by_id(res)
assert [x["id"] for x in res["jobs"]] == ["beat", "rework_34", "ending", "mix_tweak", "knob"], res
assert res["summary"] == {"pass": 5, "fail": 0, "skip": 0}, json.dumps(res, indent=1)[:3000]
saved = json.load(open(res["file"], encoding="utf-8"))
assert res["file"].startswith(os.path.join(os.path.dirname(ns["STATE"]), "bench")) and saved["jobs"], res["file"]
S = by_id(saved)
# order of the sub-tools (beat first; the fixture once; layers before perform before ending before final)
names = [x[0] for x in LOG]
assert names[0] == "flh_new_song" and LOG[0][1] == "trap" and LOG[0][3] and LOG[0][4], LOG[0]
seq = [n for n in names if n in ("flp_compose", "flp_perform_setup", "flp_perform", "flp_extend_ending", "fl_finalize",
                                 "fl_ending_check", "fl_mixer_set", "fl_preview", "fl_sound_shape")]
assert seq == ["flp_compose", "flp_perform_setup", "flp_perform", "flp_extend_ending", "fl_finalize", "fl_ending_check",
               "fl_mixer_set", "fl_preview", "fl_preview", "fl_sound_shape"], seq
assert [x for x in LOG if x[0] == "flp_compose"][0][1:] == ("layer", ("low strings", "strings", "choir"), "all")
assert [x for x in LOG if x[0] == "flp_project_info"][0][1] == 3                       # 3/4
assert [x for x in LOG if x[0] == "flp_edit"][0][2] > 100                             # 16 bars of chords + melody
assert len([x for x in LOG if x[0] == "flp_tempo_write"][0][1]) >= 2                   # a tempo change
assert PROG and PROG[0].startswith("job 2/5"), PROG                                    # fl_job shows 'job i/n'
# the beat: the balance ran (the bench let go of FL), its render counted, the time includes the balance
assert FL["lock_got"] == [True], FL["lock_got"]
b = J["beat"]
assert b["renders"] == 1 and b["seconds"] >= 0.3 and "balance" in S["beat"]["steps_s"], S["beat"]
assert "levels balanced" in (S["beat"].get("balance_news") or ""), S["beat"]
# rework: rules passed first time, one render, the bench profile active for ending + final; the final in the sandbox
w = J["rework_34"]
assert w["rules_first_time"] is True and w["renders"] == 1, w
assert set(S["rework_34"]["steps_s"]) >= {"compose_layers", "perform", "extend_ending", "finalize"}, S["rework_34"]
assert S["rework_34"]["setup_steps_s"].get("build_fixture") is not None, S["rework_34"]
assert S["rework_34"]["steps_s"]["compose_layers"] >= 0.1 and S["rework_34"]["steps_s"]["finalize"] >= 0.1
fz = [x for x in LOG if x[0] == "fl_finalize"][0]
assert fz[2] is False and fz[3] == B.BENCH_RULES and fz[4] is True, fz
assert [x for x in LOG if x[0] == "flp_extend_ending"][0][2] is True
assert S["rework_34"]["final"].startswith(S["rework_34"]["song"].rsplit(os.sep, 1)[0]), S["rework_34"]
# ending: prediction without a render, agrees with the final's rule check (reused from rework_34)
e = J["ending"]
assert e["predicted"] == "pass" and e["agree"] is True and e["renders"] == 0 and e["mode"].startswith("rework"), e
assert [x for x in LOG if x[0] == "fl_ending_check"][0][1] is False
# mix tweak: the first preview rendered, the second was reused
mx = S["mix_tweak"]
assert mx["renders"] == 1 and mx["preview_2"]["renders"] == 0 and mx["previews_reused"] == 1, mx
assert [x for x in LOG if x[0] == "fl_mixer_set"][0][1:] == (4, -3.0), "the Keys track, 2 dB down"
assert "open_in_fl" in mx.get("setup_steps_s", {}), mx                                 # FL held the waltz: reopened
# knob: no compressor at first -> one added to the channel's track (setup), set, read back
k = S["knob"]
assert k["landed"] is True and k["value_db"] == -36.0 and k["plugin"] == "Fruity Compressor", k
assert "add_compressor" in k["setup_steps_s"] and [x for x in LOG if x[0] == "flp_load_plugin"][0][1:] == (4, 1)
assert [x for x in LOG if x[0] == "fl_sound_shape"][0][1] == 3
# safety: the user's song restored, sandbox recycled, profile back and untouched, nothing in the user's final_root
assert RESTORED and RESTORED[-1][0] == USER and RESTORED[-1][2], RESTORED
assert res.get("restored", {}).get("ok") and not res.get("sandbox_kept")
assert not os.path.exists(saved["sandbox"]), saved["sandbox"]
assert ns["_state"]().get("collection") == "My Songs" and open(PROF_FILE, "rb").read() == PROF_BYTES
assert not os.path.exists(flh_store._profile_path(B.BENCH_PROFILE))
assert not os.path.exists(flh_store._profile_path(PC.CHECK_PROFILE)) and not ns["_state"]().get(PC.PROFILE_STATE)
assert os.listdir(FINAL_ROOT) == []
assert ns["_state"]().get("bench_run") is None
# item 2: every sub-tool of every job (not only the rule jobs) saw a sandbox profile: finals inside the sandbox, no
# final name / taste of the user's collection, the user's edition kept
assert PROFILES_SEEN and {w for w, *_ in PROFILES_SEEN} >= {"flh_new_song", "fl_preview", "fl_finalize"}, PROFILES_SEEN
for w, name, froot, fname, style, ed in PROFILES_SEEN:
    assert name in (PC.CHECK_PROFILE, B.BENCH_PROFILE) and froot and froot.startswith(saved["sandbox"] + os.sep), \
        (w, name, froot)
    assert fname is None and style is None and ed == "producer", (w, fname, style, ed)
assert len(json.dumps(res)) < 4000, len(json.dumps(res))                               # compact reply

# ----------------------------------------------------------------------------- failure isolation + compare='last'
time.sleep(1.1)                                                                       # a new timestamp
LOG.clear(); FL.update(compose_raises=True, comp=False, thresh="0.0 dB")
res2 = run({"compare": "last", "keep": True})
J2 = by_id(res2)
assert J2["rework_34"]["status"] == "fail" and "compose broke" in J2["rework_34"]["detail"], J2["rework_34"]
assert [J2[x]["status"] for x in ("beat", "ending", "mix_tweak", "knob")] == ["pass"] * 4, res2
assert J2["ending"]["mode"].startswith("own") and J2["ending"]["renders"] == 1, J2["ending"]   # its own final
assert "fl_finalize" in [x[0] for x in LOG] and "flp_perform" not in [x[0] for x in LOG]
cmp_ = res2["compare"]
rows = {r["job"]: r for r in cmp_["rows"]}
assert set(rows) == {"beat", "rework_34", "ending", "mix_tweak", "knob"}, rows
assert "delta_s" in rows["beat"] and rows["ending"]["delta_renders"] == 1 and "not like for like" in rows["ending"]["note"]
assert rows["rework_34"]["status"] == "pass -> fail" and "not in the totals" in rows["rework_34"]["note"], cmp_
tb = sum(rows[x]["before_s"] for x in ("beat", "ending", "mix_tweak", "knob"))
assert abs(cmp_["total"]["before_s"] - round(tb, 1)) <= 0.2, cmp_["total"]           # the failed job left out
assert res2.get("sandbox_kept") and os.path.isdir(res2["sandbox_kept"])
shutil.rmtree(res2["sandbox_kept"])
FL["compose_raises"] = False

# ----------------------------------------------------------------------------- compare two saved runs, anywhere
m.IS_WINDOWS = False
files = sorted(f for f in os.listdir(os.path.join(os.path.dirname(ns["STATE"]), "bench")) if f.endswith(".json"))
assert len(files) == 2, files
c2 = call("fl_bench", {"compare": files})
assert [r["job"] for r in c2["compare"]["rows"]] == ["beat", "rework_34", "ending", "mix_tweak", "knob"], c2
assert c2["compare"]["rows"][2]["delta_renders"] == 1
m.IS_WINDOWS = True

# ----------------------------------------------------------------------------- a balance that never starts
time.sleep(1.1)
FL["balance"] = False
res3 = run({"jobs": ["beat"]})
assert res3["jobs"][0]["status"] == "fail" and "didn't start" in res3["jobs"][0]["detail"], res3
FL["balance"] = True

# ----------------------------------------------------------------------------- the person must act first: stopped
time.sleep(1.1)
pf = B._preflight
def must_act(ctx, user=None):
    raise PC.UserMustAct("FL's project was never saved: save it, then run fl_pc_check() again")
B._preflight = must_act
res5 = run({"jobs": ["beat"]})
assert "fl_bench()" in res5["stopped"] and res5["jobs"] == [], res5
B._preflight = pf
pl = call("fl_bench", {"dry_run": True, "compare": "last"})
assert pl["compare_with"] != res5["file"] and pl["compare_with"].endswith(".json"), pl   # a stopped run isn't 'last'

# ----------------------------------------------------------------------------- FL gone mid-run; a cut-off run recovered
time.sleep(1.1)
old_sb = os.path.join(ROOT, B.SANDBOX_DIR, "bench-old"); os.makedirs(old_sb)
flh_store.profile_set(B.BENCH_PROFILE, {"rules": B.BENCH_RULES})                       # left active by the cut-off run
ns["_state"]({"bench_run": {"original": USER, "sandbox": old_sb, "started": "earlier", "collection_before": "My Songs"}})
FL["die_after_new_song"] = True
res4 = run({"jobs": ["beat", "mix_tweak", "knob"]})
J4 = by_id(res4)
assert J4["mix_tweak"]["status"] == "skip" and "FL wasn't running" in J4["mix_tweak"]["detail"], J4
assert J4["knob"]["status"] == "skip", J4
assert res4["recovered_previous_run"]["sandbox_recycled"] and not os.path.exists(old_sb), res4
assert ns["_state"]().get("collection") == "My Songs" and RESTORED[-1][1] is True
FL.update(die_after_new_song=False, running=True)

# ----------------------------------------------------------------------------- item 1: the user's song, the sandbox
# PC 2026-10-23: fl_sweep from FL's empty new project; pc_check left FL on its sandbox song 'icon probe_2', the
# benches recorded that file as "your song", reopened it at the end, and the pc_check sandbox was never recycled.
FL["holds"] = USER
time.sleep(1.1)
OLD = os.path.join(SANDBOXES, "20261023-100000", "icon probe_2.flp"); os.makedirs(os.path.dirname(OLD))
open(OLD, "wb").write(b"FLhd-old")
FL["holds"] = OLD                                                  # an earlier run left FL on its test song
B._preflight = lambda ctx, user=None: {"original": OLD, "title": "icon probe_2 - FL Studio 2026", "pids": [1234],
                                       "on_sandbox": OLD}
r6 = run({"jobs": ["beat"]})
assert RESTORED[-1][0] is None, RESTORED[-1]                       # never the sandbox file: an empty project
assert json.load(open(r6["file"]))["your_song"] == "a new empty project"
assert not os.path.exists(os.path.dirname(OLD)), "the earlier sandbox is recycled once FL has left it"
assert os.path.dirname(OLD) in (r6.get("earlier_sandboxes") or {}).get("recycled", []), r6
assert not os.path.exists(json.load(open(r6["file"]))["sandbox"]) and r6["restored"]["ok"], r6
# a sweep hands the ORIGINAL through: a later run never takes what an earlier one left in FL
OLD2 = os.path.join(SANDBOXES, "20261023-110000", "check final.flp"); os.makedirs(os.path.dirname(OLD2))
open(OLD2, "wb").write(b"FLhd-old2")
FL["holds"] = OLD2
B._preflight = lambda ctx, user=None: {"original": OLD2, "title": "check final - FL Studio 2026", "pids": [1234]}
u = {"original": USER}
time.sleep(1.1)
B._run(B._select(["beat"]), user=u)
assert RESTORED[-1][0] == USER and u == {"original": USER}, (RESTORED[-1], u)
u2 = {}                                                            # the first run of a sweep fills it (never a sandbox
FL["holds"] = OLD2                                                 # file)
time.sleep(1.1)
B._run(B._select(["beat"]), user=u2)
assert u2 == {"original": None} and RESTORED[-1][0] is None, (u2, RESTORED[-1])
shutil.rmtree(os.path.dirname(OLD2))
B._preflight = lambda ctx, user=None: {"original": USER, "title": "Mine - FL Studio", "pids": [1234]}
FL["holds"] = USER


def stuck_restore(ctx, pre, fl_gone):                              # claims ok; FL still holds the bench's song
    RESTORED.append((pre["original"], bool(fl_gone), list(ctx.opened)))
    return {"ok": True, "reopened": pre["original"]}


B._restore = stuck_restore
time.sleep(1.1)
r7 = run({"jobs": ["beat"]})
sb7 = json.load(open(r7["file"]))["sandbox"]
assert PC._inside(FL["holds"], sb7) and os.path.isdir(sb7), "FL holds the bench's song"
assert r7["restored"]["ok"] is False and "FL still shows" in r7["restored"]["why"], r7["restored"]
assert r7["sandbox_not_recycled"]["path"] == sb7 and "FL still has" in r7["sandbox_not_recycled"]["why"], r7
assert any(sb7 in n for n in r7.get("notes") or []), r7.get("notes")   # said, with the path
assert sb7 in ns["_state"]().get(PC.LEFTOVERS_STATE), "remembered for the next run"
B._restore = restore
time.sleep(1.1)
r8 = run({"jobs": ["beat"]})
assert sb7 in r8["earlier_sandboxes"]["recycled"] and not os.path.exists(sb7), r8
assert not ns["_state"]().get(PC.LEFTOVERS_STATE)
# the recycle: once more when the folder is still there (Windows held a file a moment), then said honestly
RC = {"n": 0}; real_rc = ns["files_recycle"]


def flaky_recycle(paths):
    RC["n"] += 1
    return {"recycled": []} if RC["n"] == 1 else real_rc(paths)     # the first call leaves the folder


ns["files_recycle"] = flaky_recycle
time.sleep(1.1)
r9 = run({"jobs": ["beat"]})
assert not r9.get("sandbox_not_recycled") and not os.path.exists(json.load(open(r9["file"]))["sandbox"]), r9
assert RC["n"] >= 2, RC
ns["files_recycle"] = lambda paths: {"recycled": []}                 # never goes
time.sleep(1.1)
r10 = run({"jobs": ["beat"]})
sb10 = json.load(open(r10["file"]))["sandbox"]
assert r10["sandbox_not_recycled"] == {"path": sb10, "why": "it was still there after files_recycle"}, r10
assert r10["restored"]["ok"] is True and os.path.isdir(sb10)
ns["files_recycle"] = real_rc
shutil.rmtree(sb10); ns["_state"]({PC.LEFTOVERS_STATE: None})

# ----------------------------------------------------------------------------- slow mode: FL on 2 cores (mocked kernel32)
assert B._pick_mask(0xFF, 2) == 0b101 and B._pick_mask(0x3, 2) is None and B._pick_mask(0x7, 2) == 0b11
assert B._pick_mask(0xF0, 2) == 0b1010000 and B._pick_mask(0xFFFF, 4) == 0b1010101


class FakeK32:
    def __init__(self):
        self.masks, self.calls, self.sys = {1234: 0xFF}, [], 0xFF

    def OpenProcess(self, acc, inherit, pid):
        self.calls.append(("open", acc, pid))
        return 0 if pid not in self.masks else 1000 + pid

    def GetProcessAffinityMask(self, h, pm, sm):
        pm._obj.value = self.masks[h - 1000]; sm._obj.value = self.sys
        return 1

    def SetProcessAffinityMask(self, h, mask):
        self.calls.append(("set", h - 1000, mask)); self.masks[h - 1000] = mask
        return 1

    def CloseHandle(self, h):
        self.calls.append(("close", h))
        return 1


K = FakeK32(); B._K32[:] = [K]; B.ADOPT_EVERY_S = 0.05
PIDS = [1234]; ns["_fl_pids"] = lambda: list(PIDS)
d = call("fl_bench", {"dry_run": True, "slow": True})
assert d["slow"]["cores"] == 2, d
assert call("fl_bench", {"dry_run": True, "cores": 3})["slow"]["cores"] == 3
assert "slow" not in call("fl_bench", {"dry_run": True})
try:
    call("fl_bench", {"dry_run": True, "cores": 99}); raise SystemExit("cores 99 must be refused")
except ValueError:
    pass
beat_job = B.JOBS[0]; beat_run = beat_job["run"]; SEEN = {}


def render_route_beat(ctx, st, M):
    SEEN["start"] = K.masks[1234]
    K.masks[5678] = 0xFF; PIDS.append(5678)                                    # the command-line render's FL
    t = time.time()
    while K.masks[5678] == 0xFF and time.time() - t < 3:
        time.sleep(0.02)
    SEEN["render"] = K.masks[5678]
    if FL.get("beat_raises"):
        K.masks[5678] = 0x3                                                    # e.g. the person in Task Manager
    if FL.get("beat_raises"):
        raise RuntimeError("beat broke")
    return beat_run(ctx, st, M)


beat_job["run"] = render_route_beat
time.sleep(1.1)
rs = run({"jobs": ["beat"], "slow": True, "compare": "last_normal"})
assert SEEN == {"start": 0b101, "render": 0b101}, SEEN                       # FL + the new FL limited during it
assert K.masks == {1234: 0xFF, 5678: 0xFF}, K.masks                            # both put back exactly
assert all(c[1] == 0x600 for c in K.calls if c[0] == "open")                   # SET|QUERY_INFORMATION only
assert {c[0] for c in K.calls} <= {"open", "set", "close"}
assert len([c for c in K.calls if c[0] == "open"]) == len([c for c in K.calls if c[0] == "close"])  # no leak
assert rs["speed"] == "slow (2 cores)" and sorted(rs["slow"]["restored"]["restored"]) == [1234, 5678], rs
assert json.load(open(rs["file"], encoding="utf-8"))["speed"] == "slow (2 cores)"
cs = rs["compare"]
assert cs["speed"] == ["normal", "slow (2 cores)"] and "budget PC" in cs["speed_note"] and "x" in cs["rows"][0], cs
assert ns["_state"]().get("bench_affinity") is None
# a job raising: still put back; a mask someone else changed meanwhile is left alone
time.sleep(1.1)
PIDS[:] = [1234]; del K.masks[5678]; FL["beat_raises"] = True
rs2 = run({"jobs": ["beat"], "cores": 2})
assert rs2["jobs"][0]["status"] == "fail" and K.masks[1234] == 0xFF and K.masks[5678] == 0x3, (rs2, K.masks)
assert rs2["slow"]["restored"] == {"restored": [1234], "left": [5678]}, rs2["slow"]
FL["beat_raises"] = False; beat_job["run"] = beat_run
# compare='last_slow' + two saved runs of different speeds anywhere
assert B._load_result("last_slow")["speed"].startswith("slow")
# a slow run cut off by a crash: the saved masks are put back (next start / next bench), gone pids skipped
PIDS[:] = [1234]; K.masks[1234] = 0b101
ns["_state"]({"bench_affinity": {"1234": [0xFF, 0b101], "4321": [0xFF, 0b11]}})
rr = B._restore_saved_affinity()
assert rr == {"restored": [1234], "gone": [4321]} and K.masks[1234] == 0xFF, rr
assert ns["_state"]().get("bench_affinity") is None and B._restore_saved_affinity() is None
# kernel32 unusable: the run is a normal one and says so
B._K32[:] = [None]
time.sleep(1.1)
rs3 = run({"jobs": ["beat"], "slow": True})
assert rs3.get("speed") is None and "couldn't limit" in json.dumps(json.load(open(rs3["file"])).get("notes")), rs3
B._K32[:] = [K]

print("ok")

# the compare names the steps whose time changed (PC 2026-10-24: rework_34 22 -> 55 s, which step?)
import flh_bench as _fb  # noqa: E402
_c = _fb.compare_runs({"jobs": [{"id": "rework_34", "status": "pass", "seconds": 22.0,
                                 "steps_s": {"layers": 5.0, "final": 10.0, "perform": 7.0}}]},
                      {"jobs": [{"id": "rework_34", "status": "pass", "seconds": 55.0,
                                 "steps_s": {"layers": 5.2, "final": 40.0, "perform": 7.0, "reload": 2.8}}]})
_sc = _c["rows"][0]["steps_changed"]
assert list(_sc) == ["final", "reload"] and _sc["final"] == [10.0, 40.0] and _sc["reload"] == [None, 2.8], _sc
print("bench step deltas ok")
