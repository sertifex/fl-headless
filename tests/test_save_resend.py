"""A save through FL that FL drops (right after a load FL ignores saves for a moment) is sent once more after
RESAVE_AFTER_S instead of waiting the whole time for nothing. PC 2026-10-24: fl_finalize's 'save FL first' waited
17 s, landed nothing, and the render went without the cache."""
import os, shutil, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_resave_"); os.environ["FLH_ROOTS"] = tmp
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402

src = os.path.join(HERE, "corpus", "base.flp")
p = os.path.join(tmp, "song.flp"); shutil.copy(src, p)
m.RESAVE_AFTER_S = 0.5
m._save_guard_why = lambda project, explicit=False: None
saves = []
class B:
    def call(self, what, **k):
        saves.append(k.get("action"))
        if len(saves) == 2:                       # FL ignores the first save, takes the second
            threading.Timer(0.1, lambda: os.utime(p, (time.time() + 5, time.time() + 5))).start()
        return {}
m.BRIDGE = B()
t = time.time()
assert m._save_in_fl_and_wait(p, 6.0) is True, "the second save should land"
assert saves == ["save", "save"] and time.time() - t < 4.0, (saves, time.time() - t)

saves.clear()
class Quick:
    def call(self, what, **k):
        saves.append(1); threading.Timer(0.05, lambda: os.utime(p, (time.time() + 5, time.time() + 5))).start()
        return {}
m.BRIDGE = Quick()
assert m._save_in_fl_and_wait(p, 6.0) is True and saves == [1], saves   # a save that lands is never repeated
print("ok")
