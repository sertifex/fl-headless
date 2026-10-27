"""fl_pc_check's _save_read: a refused save says why; a big project's save that lands late still counts.
PC 2026-10-24 lazy.big_project: 'FL didn't save ... within 15 s' hid the guard's reason."""
import os, shutil, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_sr_"); import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import flh_pccheck as pc  # noqa: E402
p = os.path.join(tmp, "Big.flp"); open(p, "wb").write(b"a")

why = {"v": None}; calls = []
def save(path, wait_s):
    calls.append(wait_s); return False
pc.H = {"_save_in_fl_and_wait": save, "_save_refused_why": lambda: why["v"]}
_sleep = time.sleep
time.sleep = lambda s: _sleep(min(s, 0.05))          # the retry pause and the settle polls, made quick

why["v"] = "not saved: FL shows Other.flp, not Big.flp"
try:
    pc._save_read(p); raise SystemExit("a refused save should raise")
except RuntimeError as e:
    assert "refused" in str(e) and "Other.flp" in str(e), e
assert calls == [15.0, 25.0], calls

why["v"] = None; calls.clear()
try:
    pc._save_read(p); raise SystemExit("no save should raise")
except RuntimeError as e:
    assert "didn't save" in str(e), e

calls.clear()                                     # the first save lands during the pause before the retry
def late(path, wait_s):
    calls.append(wait_s)
    if len(calls) == 1:
        open(p, "wb").write(b"bigger file")
    return False
pc.H["_save_in_fl_and_wait"] = late
assert pc._save_read(p) is True and calls == [15.0], calls
print("ok")
