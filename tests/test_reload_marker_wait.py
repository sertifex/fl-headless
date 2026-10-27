"""The proven reload waits for FL to finish a load still running before stamping its marker, and stamps once more
when the file is still held. PC 2026-10-24 lazy.big_project: an untrusted fast reopen of a 150-channel song was still
loading when the marker was written: WinError 5, the reload unproven, every later save refused."""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_rmw_"); os.environ["FLH_ROOTS"] = tmp
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402

p = os.path.join(tmp, "Big.flp"); open(p, "wb").write(b"song")
seq = []
m._ping_ok = lambda *a, **k: True
m._wait_load_out = lambda project, wait_s=None, bridge_up=False, **k: (seq.append("wait"), 3.0)[1]
tries = iter([(None, "no marker could be written into the file (PermissionError: [WinError 5] Access is denied)"),
              ((1, "flh-abc", "", b"stamped"), None)])
m._reload_marker_stamp = lambda project, data: (seq.append("stamp"), next(tries))[1]
class Stop(Exception):
    pass
def via_blank(project, prior):
    raise Stop()
m._reload_via_blank = via_blank
m._reload_marker_put_back = lambda *a: True
m._stale_mark = lambda *a, **k: None
steps = {}
try:
    m._reload_marked(p, 1.0, steps, "test")
except Stop:
    pass
assert seq == ["wait", "stamp", "wait", "stamp"], seq
assert steps["load_out_wait"] == 3.0 and "WinError 5" in steps["marker_retry"] and "marker" not in steps, steps

# the direct reopen's tag wait goes on while FL is plainly loading a heavy song (cost known), not 6 s flat
import time
t0 = time.time()
class B:
    def call(self, *a, **k):
        if time.time() - t0 < 1.2:
            raise TimeoutError("FL busy loading")
        return {"name": "Kick"}
m.BRIDGE = B(); m._TAG_NOW.update(track=1, name="Kick ~flh1234")
m._fl_load_busy = lambda bridge_up=False, **k: True
m._fl_still_working = lambda: False
m._scaled = lambda s, min_s=None: float(s)
assert m._tag_gone(1, 0.3, cost=5.0) is True, "a reopen still loading was judged 'no reload'"
t0 = time.time() + 99
assert m._tag_gone(1, 0.3) is False                               # no cost known: the plain short wait
print("ok")
