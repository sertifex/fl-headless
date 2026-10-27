"""Composer edits never start from a stale read of the open project (PC 2026-10-23: flp_compose 'read the file on
disk: FL is busy (checking that FL holds the last edit), so it wasn't asked to save first' - the reload after its
edit could then drop what had been changed in FL):
- the call waits for FL (a background check of the last edit steps aside), FL saves, THEN the tool reads - all inside
  the FL lock; FL's save isn't repeated by the tool's own 'save first' (nothing changed since);
- FL busy with a job, FL not saving, a file changed behind FL: nothing read, nothing changed, the reason said;
- dry runs / read-only tools still run on the file; a project FL doesn't show isn't waited for.
Run: python3 tests/test_fresh_read.py"""
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
TMP = tempfile.mkdtemp(prefix="flh_fresh_")
ROOT = os.path.join(TMP, "root"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = ROOT
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
import fl_headless_mcp as m  # noqa: E402

P = os.path.join(ROOT, "Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), P)
OTHER = os.path.join(ROOT, "Other.flp"); shutil.copy(P, OTHER)


def read(p):
    with open(p, "rb") as f:
        return f.read()


class FL:
    held = read(P) + b"FL-UNSAVED"; changed = 1; saves = 0; save_ok = True; shows = P


def bridge(cmd, timeout=15.0, **a):
    if cmd == "transport" and a.get("action") == "save":
        FL.saves += 1
        if FL.save_ok:
            with open(FL.shows, "wb") as f:
                f.write(FL.held)
            FL.changed = 0
        return {"done": "save"}
    if cmd == "state":
        return {"changed": FL.changed}
    return {}


m.BRIDGE.call = bridge
m.fl_running = lambda: True
m._fl_pids = lambda: [1]
title = lambda: f"{os.path.splitext(os.path.basename(FL.shows))[0]} - FL Studio 2026"
m._fl_windows = lambda: [(1, title())]
m._fl_main_window = lambda: (1, title())
m._open_project_guess = lambda: (FL.shows, [title()])
m.fl_dialogs = lambda: []
m._note_fl_holds(P, read(P))                                      # FL loaded the file as it is on disk

SEEN = []


def tool(project, dry_run=False):
    """Stands for the composer: asks FL to 'save first' itself when FL is free (as flh_compose's _fresh), reads."""
    if m._FL_LOCK.acquire(blocking=False):
        try:
            m._save_in_fl_and_wait(project)
        finally:
            m._FL_LOCK.release()
    SEEN.append(read(project))
    return {"read": len(SEEN)}


m.TOOLS["flp_test_compose"] = (tool, "test", m._schema(project=m._STR("p"), dry_run=m._BOOL("d", True)))
m.FRESH_READ_TOOLS.add("flp_test_compose")
m._install_fresh_reads(vars(m))
call = lambda **kw: m.TOOLS["flp_test_compose"][0](**kw)
assert getattr(m.TOOLS["flp_compose"][0], "__fresh_read__", False), "the composer's entry points are wrapped"

# 1) a background check of the last edit holds FL: the call waits (the check steps aside), FL saves, then it reads
released = threading.Event()


def check():
    m._FL_LOCK.acquire(); m._FL_HOLDER["what"] = "checking that FL holds the last edit"
    try:
        t0 = time.time()
        while not m._VERIFY_STOP.is_set() and time.time() - t0 < 5:
            time.sleep(0.01)
        time.sleep(0.2)                                           # steps aside at its next step
    finally:
        m._FL_HOLDER["what"] = None; m._FL_LOCK.release(); released.set()


th = threading.Thread(target=check); th.start(); time.sleep(0.05)
r = call(project=P)
assert released.is_set() and SEEN[-1] == FL.held and FL.saves == 1, (FL.saves, len(SEEN))   # one save, FL's state
th.join()

# 2) FL busy with a JOB (a render): nothing read or changed, the reason said
FL.held = read(P) + b"MORE"; FL.changed = 1; SEEN.clear(); FL.saves = 0
hold = threading.Event(); go = threading.Event()


def job():
    m._FL_LOCK.acquire(); m._FL_HOLDER["what"] = "job render-1 (fl_job)"
    hold.set(); go.wait(10)
    m._FL_HOLDER["what"] = None; m._FL_LOCK.release()


th = threading.Thread(target=job); th.start(); hold.wait(5)
try:
    call(project=P); raise SystemExit("must refuse while FL is busy")
except RuntimeError as e:
    assert "did nothing" in str(e) and "job render-1" in str(e), e
assert not SEEN and FL.saves == 0
r = call(project=P, dry_run=True)                                 # a dry run still reads the file (as before)
assert SEEN and SEEN[-1] != FL.held
go.set(); th.join(); SEEN.clear()

# 3) FL doesn't save: nothing read
FL.save_ok = False
try:
    call(project=P); raise SystemExit("must refuse: FL didn't save")
except RuntimeError as e:
    assert "didn't save" in str(e) and "did nothing" in str(e), e
assert not SEEN
FL.save_ok = True

# 4) the file changed behind FL (a restore) while FL has unsaved changes: refused, the file untouched
call(project=P); SEEN.clear()                                     # FL saved: holds = file
FL.changed = 1; FL.held = read(P) + b"LATER"
restored = read(OTHER)
with open(P, "wb") as f:
    f.write(restored)
try:
    call(project=P); raise SystemExit("must refuse")
except RuntimeError as e:
    assert "isn't what FL last loaded" in str(e) and "save_current=false" in str(e), e
assert read(P) == restored and not SEEN

# 5) FL shows another project: no wait, no save, the file is read
FL.shows = OTHER; FL.saves = 0
call(project=P)
assert FL.saves == 0 and SEEN[-1] == restored
shutil.rmtree(TMP, True)
print("ok")
