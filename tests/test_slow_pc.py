"""Slow PCs ('tin cans'): one per-PC speed factor from what the server measured (render ratio, load costs, bridge
pings, late replies), FL-speed waits scaled by it (factor 1.0 = today's numbers), 'still working' extends a wait once
instead of failing, and a really dead FL still fails with the factor in the message.
Run: python3 tests/test_slow_pc.py"""
import json, os, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402

F = m.speed_factor_from
# ---- the factor from fake measurements
assert F() == (1.0, {}), F()                                      # nothing measured: a fast PC
assert F(render_ratio=0.3)[0] == 1.0                              # faster than the base: never below 1
assert F(render_ratio=1.0)[0] == 2.0
assert F(render_ratio=9.0)[0] == m.SPEED_SOURCE_CAP["render"]     # a heavy project alone: capped
assert F(load_s=[20, 25, 30])[0] == 2.5                           # median recent load / 10 s
assert F(load_s=[200, 300])[0] == m.SPEED_SOURCE_CAP["load"]
assert F(ping_ms=[900, 1000])[0] == 1.0                           # under 3 pings: no evidence
assert F(ping_ms=[500, 500, 9000])[0] == 2.0                      # median: one slow ping doesn't count
assert F(late=[3.0, 5.0])[0] == 4.0                               # smoothed, the newest weighs most
assert F(late=[50])[0] == m.SPEED_FACTOR_MAX                      # clamp 1..6
f, why = F(render_ratio=1.0, load_s=[30], ping_ms=[300] * 3, late=[1.5])
assert f == 3.0 and max(why, key=why.get) == "load", (f, why)     # the slowest evidence wins

# ---- cached factor: default 1.0, rises at once, falls halfway per change of evidence
ev = {"render_ratio": None, "load_s": [], "ping_ms": [], "late": []}
m._speed_evidence = lambda: dict(ev)
assert m._speed_factor(refresh=True) == 1.0
ev["late"] = [4.0]; assert m._speed_factor(refresh=True) == 4.0
ev["late"] = [4.0, 1.0]; assert m._speed_factor(refresh=True) == 3.25          # raw 2.5: halfway down
assert m._speed_factor(refresh=True) == 3.25                                   # same evidence: no drift
assert m._speed_factor() == 3.25                                               # cached
saved = json.load(open(m._speed_path()))
assert saved["f"] == 3.25, saved                                  # in fl-headless's own state folder
st = m.fl_status(); assert st["pc_speed"]["factor"] == 3.25, st.get("pc_speed")
assert m.fl_timings()["pc_speed"]["factor"] == 3.25

# ---- waits scale (factor 1 = identical numbers; polls never)
fac = [1.0]
m._speed_factor = lambda refresh=False: fac[0]
for s_ in (3.0, 8.0, 15.0, 30.0, 240):
    assert m._scaled(s_) == s_
assert m._scaled(1.0) == 1.0 and m._scaled(2.0) == 2.0 and m._job_wait_max() == m.JOB_WAIT_MAX_S
fac[0] = 4.0
assert m._scaled(15.0) == 60.0 and m._scaled(m.IN_FL_WAITS["export_dialog"]) == 60.0 and m._scaled(1.0) == 1.0
assert m._job_wait_max() == m.JOB_WAIT_CLIENT_S                   # never past what MCP clients wait for
assert m._speed_extra(15.0, 60.0) == 30.0 and m._speed_extra(1.0, 1.0) == 0.0  # once more, up to 6x in all

m.SPEED_MIN_SCALED_S = 0.1                                        # short waits in this test
m._bridge_state_light = lambda timeout=15.0: (_ for _ in ()).throw(TimeoutError("busy"))
for k, want in ((1.0, 0.4), (3.0, 1.2)):                          # _fl_load_seen: FL busy loading the whole time
    fac[0] = k; t0 = time.time(); assert m._fl_load_seen(0.4) is True
    took = time.time() - t0; assert want - 0.05 <= took < want + 0.4, (k, took)

# ---- a slow FL through the mailbox: answers after a fast PC's timeout, within the scaled one
mb = os.path.join(tmp, "mailbox"); os.makedirs(mb)
m._mailbox_dir = lambda: mb
m._mailbox_alive = lambda max_age=5.0: True
still = [False]
m._fl_still_working = lambda: still[0]


def fake_fl(delay, n=1):
    def run():
        req = os.path.join(mb, "req.json"); t_end = time.time() + 6
        while time.time() < t_end:
            try:
                q = json.load(open(req))
                break
            except (OSError, ValueError):
                time.sleep(0.01)
        else:
            return
        time.sleep(delay)
        with open(os.path.join(mb, "resp.json.tmp"), "w") as f:
            json.dump({"id": q["id"], "reply": "OK{'pong': 1}"}, f)
        os.replace(os.path.join(mb, "resp.json.tmp"), os.path.join(mb, "resp.json"))
    th = threading.Thread(target=run, daemon=True); th.start(); return th


fac[0] = 1.0                                                      # a fast PC's limit: the slow FL is missed
th = fake_fl(0.9)
try:
    m.BRIDGE.call("state", timeout=0.5); raise AssertionError("should time out")
except TimeoutError as e:
    assert "no reply from FL within 0.5s" in str(e) and "speed factor 1" in str(e), e
th.join()
m._SPEED["late"] = []; m._SPEED["t"] = 0.0
fac[0] = 2.5                                                      # this PC's factor: the same FL succeeds
th = fake_fl(0.9); t0 = time.time()
assert m.BRIDGE.call("state", timeout=0.5) == {"pong": 1}
assert 0.85 < time.time() - t0 < 1.25; th.join()
assert m._SPEED["late"] and m._SPEED["late"][-1][1] >= 1.8, m._SPEED["late"]   # evidence: this PC is slow

# ---- 'still working' is never an error: one extension while FL is visibly busy, with a note
fac[0] = 1.0; still[0] = True; m._pop_slow_notes()
th = fake_fl(0.8); t0 = time.time()
assert m.BRIDGE.call("state", timeout=0.5) == {"pong": 1}; th.join()
notes = m._pop_slow_notes()
assert notes and "FL is still working (slow PC)" in notes[0], notes
assert "FL is still working" in m.fl_speed_view()["last_slow"]["note"]

# ---- a truly dead FL still fails, the error says the factor and what was waited
still[0] = False; fac[0] = 3.0; t0 = time.time()
try:
    m.BRIDGE.call("state", timeout=0.4); raise AssertionError("dead FL must fail")
except TimeoutError as e:
    took = time.time() - t0
    assert "speed factor 3" in str(e) and "waited 1 s" in str(e) and "0.4 s" in str(e), e
    assert 1.15 < took < 1.6, took
still[0] = True; fac[0] = 1.0; t0 = time.time()                   # busy but never answering: extended ONCE only
try:
    m.BRIDGE.call("state", timeout=0.4); raise AssertionError("must fail after the one extension")
except TimeoutError as e:
    assert "extended once" in str(e) and 0.75 < time.time() - t0 < 1.2, (e, time.time() - t0)
still[0] = False

# ---- saves landing: _wait_saved_any waits factor x longer; a busy FL gets one extension
proj = os.path.join(tmp, "Song.flp"); open(proj, "wb").write(b"x" * 10)
before = m._file_sig(proj)
for k, want in ((1.0, 0.3), (3.0, 0.9)):
    fac[0] = k; t0 = time.time(); assert m._wait_saved_any({proj: before}, 0.3, cap_s=0.3) is None
    assert want - 0.05 < time.time() - t0 < want + 0.35, (k, time.time() - t0)
fac[0] = 1.0; still[0] = True; t0 = time.time()
assert m._wait_saved_any({proj: before}, 0.3, cap_s=0.3) is None
assert 0.55 < time.time() - t0 < 0.95, time.time() - t0; still[0] = False

# ---- an export file still growing at the deadline: waited on once more, not failed
m._window_alive = lambda h: False
out = os.path.join(tmp, "render.wav")


def grow():
    with open(out, "wb") as f:
        for _ in range(4):
            f.write(b"\0" * 4096); f.flush(); time.sleep(0.3)        # grows until ~1.2 s


fac[0] = 1.0; th = threading.Thread(target=grow, daemon=True); th.start(); t0 = time.time()
ok, why = m._wait_render_done(out, 1, 2.0, t0); th.join()     # at 2.0 s not yet still for 2 s: once more
assert ok and 2.9 < time.time() - t0 < 4.0, (ok, why, time.time() - t0)
ok, why = m._wait_render_done(os.path.join(tmp, "never.wav"), 1, 0.3, time.time())
assert not ok and "speed factor 1" in why, why
print("ok")
