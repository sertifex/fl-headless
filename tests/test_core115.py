"""1.15 core: instant jobs + progress, preemptible verify, previous-edit check, step timings, extension loader."""
import os, sys, tempfile, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp
import fl_headless_mcp as m  # noqa: E402

# jobs: fl_job returns the moment the job ends; progress is live
gate = threading.Event()
def work():
    m._job_progress(step="render", pct=10)
    gate.wait(5)
    m._job_progress(step="finish")
    return {"ok": 1, "steps_s": {"render": 1.0}}
j = m._start_job("t", work)["job"]
time.sleep(0.1)
lst = m.fl_job(wait_s=0)
assert any(x.get("progress", {}).get("step") == "render" for x in lst), lst
threading.Timer(0.3, gate.set).start()
t0 = time.time(); r = m.fl_job(wait_s=10)                 # the one running job is waited on
assert r["status"] == "done" and time.time() - t0 < 1.5, (r, time.time() - t0)
m._job_progress(step="x")                                 # outside a job: no-op
print("jobs ok")

# timings keep the step breakdown of the slowest and last call
m._timing_note("flp_edit", 5.0, {"steps_s": {"save": 1}})
m._timing_note("flp_edit", 96.0, {"steps_s": {"wait_prev_check": 40}})
m._timing_note("flp_edit", 3.0, {"steps_s": {"save": 0.2}})
row = [x for x in m.fl_timings()["tools"] if x["tool"] == "flp_edit"][0]
assert row["slowest_steps"] == {"wait_prev_check": 40} and row["last_steps"] == {"save": 0.2}, row
print("timings ok")

# a new edit stops a running background check at once and checks the previous edit itself
proj = os.path.join(tmp, "song.flp"); open(proj, "wb").write(b"v1")
alerts = []
m._state = lambda d=None: (alerts.append(d) if d else None) or {}
saves = {"n": 0}
def fake_call(cmd, timeout=15.0, **a):
    if cmd == "state":
        return {"playing": False}
    if cmd == "transport" and a.get("action") == "save":
        saves["n"] += 1
        threading.Timer(0.2, lambda: os.utime(proj, (time.time() + saves["n"], time.time() + saves["n"]))).start()
    return {}
m.BRIDGE.call = fake_call
m._reload_in_fl = lambda p: {"open_dialog": 0.1}
m._fingerprint = lambda p: {"notes": open(p, "rb").read()}
slow_started = threading.Event()
def slow_verify(project, written, want, was_playing, *a):        # FL ignoring saves: a long check
    slow_started.set()
    for _ in range(100):
        if m._VERIFY_STOP.is_set():
            return {"verified": None, "verify_note": "a newer edit checks it"}
        time.sleep(0.1)
    return {"verified": True}
m._verify_loaded = slow_verify
def edit(data):
    def fn():
        open(proj, "wb").write(data); return {"output": proj}
    return fn
r1 = m._hot_reload(proj, edit(b"v2"))
assert slow_started.wait(3)
t0 = time.time(); r2 = m._hot_reload(proj, edit(b"v3"))
assert time.time() - t0 < 3, time.time() - t0              # was up to 40 s
assert r2["steps_s"]["wait_prev_check"] < 2, r2
assert r2.get("previous_edit_held") is True, r2            # FL's save still had v2
assert not [a for a in alerts if a.get("verify_alert")], alerts
v1 = m.JOBS[r1["verify_job"]]; m._JOB_EVENTS[r1["verify_job"]].wait(3)
assert v1["result"]["verified"] is None, v1
# FL kept an older copy: the next edit's save notices it
m._PENDING_VERIFY[os.path.normcase(os.path.abspath(proj))] = {"want": {"notes": b"v9"}, "t": time.time(), "job": "x"}
r3 = m._hot_reload(proj, edit(b"v4"))
assert r3.get("previous_edit_held") is False and [a for a in alerts if a.get("verify_alert")], (r3, alerts)
m._VERIFY_STOP.set(); time.sleep(0.3)
print("verify ok")

# extension loader: a broken extension is reported, the rest load
st = m._EXT_STATUS
assert any(e["module"] == "flh_store" for e in st["loaded"]), st
ns = dict(vars(m)); ns["TOOLS"] = dict(m.TOOLS)
bad = os.path.join(os.path.dirname(m.__file__), "flh_catalog.py")
if not os.path.exists(bad):
    ns["EXTENSIONS"] = ("flh_store",)
st2 = m._load_extensions(ns)
assert "flh_profile" in ns["TOOLS"] and isinstance(st2["errors"], dict), st2
print("extensions ok")
