"""A job holding FL and the merged-edit autosync; FL stuck on a foreign file dialog at fl_open.

- a merged (auto mode) edit made INSIDE a job that holds the FL lock goes into FL in the job's own thread (the
  autosync thread couldn't get the lock until the job ends); a foreground edit is still merged by the autosync
- the autosync's give-up clock doesn't run while a job holds FL, and its 'stayed busy' alert names the holder
- lazy mode: a live change's autosync saves FL but leaves queued edits for flh_sync
- fl_open refuses at once, naming the dialog, when FL shows a file dialog it didn't open (Edison's Save As)
Run: python3 tests/test_job_autosync.py"""
import os, shutil, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402

FIX = os.path.join(HERE, "corpus", "base.flp")
src = os.path.join(tmp, "Song.flp"); shutil.copy(FIX, src)
raw_tempo = getattr(m.flp_set_tempo, "__wrapped__", m.flp_set_tempo)
synced, scheduled = [], []
orig = {k: getattr(m, k) for k in ("_sync_pending", "_schedule_autosync", "_autosync_wanted", "_scaled",
                                   "fl_dialogs", "_open_in_running_fl", "_state", "_open_in_fl",
                                   "_save_in_fl_and_wait")}


def fake_sync(project=None, file_only=False):
    synced.append((project, threading.current_thread().name, m._FL_LOCK._is_owned()))
    ops = m._pending_get(project)
    m._pending_set(project, [])
    return {project: {"ops": len(ops), "reloaded_in_fl": True}}


m._sync_pending = fake_sync
m._schedule_autosync = lambda p, delay=None: scheduled.append(p)
m._autosync_wanted = lambda p: True


def lazy_edit(bpm):
    return m._run_lazy("flp_set_tempo", raw_tempo, {"path": src, "bpm": bpm}, "path", None, src)


# ---- inside a job holding FL: the merge goes in right away, in the job's thread
j = m._start_job("pccheck", lambda: lazy_edit(131), fl=True)
res = m.fl_job(j["job"], wait_s=20)
r = res.get("result") or {}
assert res.get("status") == "done", res
assert synced and synced[-1][0] == src and synced[-1][2], f"synced in the job's thread holding FL: {synced}"
assert not scheduled, f"no autosync thread left waiting for the job: {scheduled}"
assert r.get("queued") is False and r.get("reload", "").startswith("now") and "pending_ops" not in r, r
assert not m._pending_get(src), "nothing left queued"

# ---- a foreground edit (no job): merged by the autosync as before
synced.clear(); scheduled.clear()
with m._FL_LOCK:                                                  # a foreground tool holds FL too: still merged
    r = lazy_edit(132)
assert not synced and scheduled == [src], (synced, scheduled)
assert r.get("queued") is True and r.get("reload") == "merged", r
m._pending_set(src, [])

# ---- autosync: a job holding FL doesn't count toward the give-up; another holder does, and is named
m._schedule_autosync = orig["_schedule_autosync"]
m._scaled = lambda s: 1.0                                         # 'stayed busy' after 1 s (normally 10 min)
alerts = []
m._state = lambda d=None, *a, **k: (alerts.append(d) if isinstance(d, dict) else None) or {}


def hold(what, secs, ready):
    with m._FL_LOCK:
        m._FL_HOLDER["what"] = what; ready.set()
        time.sleep(secs)
        m._FL_HOLDER["what"] = None


def run_autosync(what, secs):
    synced.clear(); alerts.clear()
    m._pending_set(src, [{"tool": "flp_set_tempo", "bpm": 140}])
    k = m._ud_key(src)
    with m._AUTOSYNC_LOCK:
        m._AUTOSYNC[k] = {"due": time.time(), "project": os.path.abspath(src)}
    ready = threading.Event()
    h = threading.Thread(target=hold, args=(what, secs, ready)); h.start(); ready.wait(5)
    t = threading.Thread(target=m._autosync_run, args=(k,)); t.start()
    t.join(30); h.join(30)
    assert not t.is_alive()


run_autosync("job pccheck-9 (fl_job)", 2.5)
assert synced and synced[0][0] == src, f"waited for the job, then merged: {synced} {alerts}"
assert not any("stayed busy" in str(a.get("verify_alert", "")) for a in alerts), alerts
run_autosync("rendering in FL", 2.5)
al = [a.get("verify_alert") for a in alerts if a.get("verify_alert")]
assert not synced and al and "stayed busy" in al[0] and "rendering in FL" in al[0], (synced, al)
m._pending_set(src, [])

# ---- lazy mode: a live change's autosync saves FL, queued edits wait for flh_sync
m._autosync_wanted = lambda p: False
saved = []
m._open_in_fl = lambda p: True
m._save_in_fl_and_wait = lambda p, w=8.0: saved.append(p) or True
synced.clear()
m._pending_set(src, [{"tool": "flp_set_tempo", "bpm": 141}])
k = m._ud_key(src)
with m._AUTOSYNC_LOCK:
    m._AUTOSYNC[k] = {"due": time.time(), "project": os.path.abspath(src), "live_dirty": True}
t = threading.Thread(target=m._autosync_run, args=(k,)); t.start(); t.join(20)
assert saved == [src] and not synced and m._pending_get(src), (saved, synced)
m._pending_set(src, [])
for kk in ("_open_in_fl", "_save_in_fl_and_wait", "_state", "_scaled", "_autosync_wanted", "_sync_pending"):
    setattr(m, kk, orig[kk])

# ---- fl_open behind a foreign file dialog: refused at once, the dialog named, no Ctrl+O
edison = {"hwnd": 77, "title": "Save As", "class": "#32770", "kind": "save_as", "file_dialog": True,
          "text": ["untitled_2.wav", "Samples"], "buttons": ["Save", "Cancel"], "blocks_fl": True}
tried = []
m.fl_dialogs = lambda: [edison]
m._open_in_running_fl = lambda *a, **k: tried.append(a) or False
assert m._fl_blocking_popup()["hwnd"] == 77
try:
    m._open_running_or_fail(src)
    raise AssertionError("should refuse")
except RuntimeError as e:
    msg = str(e)
assert "'Save As'" in msg and "untitled_2.wav" in msg and not tried, (msg, tried)
# a non-file popup left over: the old path runs, and the final error still names what FL shows
box = {"hwnd": 78, "title": "Plugin error", "class": "TMsgForm", "kind": "other", "text": ["x"], "buttons": ["OK"],
       "blocks_fl": True}
m.fl_dialogs = lambda: [box]
m.fl_dialog = lambda *a, **k: {}
m.time.sleep, sleep0 = (lambda s: None), m.time.sleep
try:
    m._open_running_or_fail(src)
    raise AssertionError("should fail")
except RuntimeError as e:
    msg = str(e)
finally:
    m.time.sleep = sleep0
assert "Plugin error" in msg and len(tried) == 2, (msg, tried)
m.fl_dialogs = lambda: []
assert m._fl_blocking_popup() is None
print("ok")
