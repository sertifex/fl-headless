"""Editing the open project reloads it in place (no FL restart). Run: python tests/test_reload.py"""
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import fl_headless_mcp as m

tmp = tempfile.mkdtemp(); proj = os.path.join(tmp, "song.flp")
open(proj, "wb").write(b"v1")
log = []


loaded = {"n": 0}


def fake_call(cmd, timeout=15.0, **a):
    log.append((cmd, a.get("action")))
    if cmd == "state":                                            # FL autoplays once a load happened
        return {"playing": loaded["n"] > 0 and log.count(("transport", "stop")) < 2}
    if cmd == "transport" and a.get("action") == "save":       # FL saves a moment later
        threading.Timer(0.5, lambda: (time.sleep(0.01), os.utime(proj, (time.time() + 5, time.time() + 5)))).start()
    return {"done": a.get("action")}


m.BRIDGE.call = fake_call
opened = []
m.os.startfile = lambda p: opened.append(p)
m._state = lambda d: None
m.fl_running = lambda: True
m._open_in_running_fl = lambda p, timeout_s=8.0, on_save="no", **k: (opened.append(p), loaded.__setitem__("n", 1), True)[2]
m._fingerprint = lambda p: {"notes": open(p, "rb").read()}
m._verify_in_background = lambda *a: "verify-test"
m._tag_track = lambda: (3, 'Bass')
m._tag_gone = lambda t, w, **k: True
m._open_project_guess = lambda: (proj, "song.flp - FL Studio 2026")
m.fl_close = lambda save=True: (_ for _ in ()).throw(AssertionError("must not close FL"))


def edit():
    open(proj, "wb").write(b"v2")
    return {"output": proj}


r = m._with_open_project(proj, proj, edit)
assert r["reloaded_in_fl"] and opened == [proj], (r, opened)          # direct reopen, no blank-template detour
assert r["verify_job"] == "verify-test", r
assert open(proj, "rb").read() == b"v2"
assert ("transport", "save") in log and log[-1] == ("transport", "stop"), log   # stopped the autoplay
print("reload ok")

# FL running + Open dialog fails: must raise, never launch the file (a second FL)
m._open_in_running_fl = lambda p, timeout_s=8.0, on_save="no", **k: False
m.fl_dialogs = lambda: []
m._open_project_guess = lambda: (None, "")
m.os.startfile = lambda p: (_ for _ in ()).throw(AssertionError("launched a second FL"))
other = os.path.join(tmp, "other.flp"); open(other, "wb").write(b"x")
try:
    m.fl_open(other); raise SystemExit("fl_open should have raised")
except RuntimeError as e:
    assert "second FL" in str(e), e
print("one-FL guard ok")
