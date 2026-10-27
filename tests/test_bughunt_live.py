"""Bug hunt (live-FL layer): locks, jobs, autosync, bridge, renders. Each block is one confirmed bug's regression test.
Run: python3 tests/test_bughunt_live.py"""
import os, shutil, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402

FIX = os.path.join(HERE, "corpus", "base.flp")
ORIG = {k: getattr(m, k) for k in ("_state", "_autosync_wanted", "_sync_pending", "_scaled", "fl_running",
                                   "_open_in_fl", "_save_in_fl_and_wait")}


def restore():
    for k, v in ORIG.items():
        setattr(m, k, v)


# ---- 1. autosync: a queued project whose file is gone must not spin forever (it used to loop at full speed,
#         re-taking FL and appending 'merged edit(s) are in FL now' news each round)
song = os.path.join(tmp, "Gone.flp"); shutil.copy(FIX, song)
m._pending_set(song, [{"tool": "flp_set_tempo", "bpm": 133}])
os.remove(song)
alerts = []
m._state = lambda d=None, *a, **k: (alerts.append(d) if isinstance(d, dict) else None) or {}
m._autosync_wanted = lambda p: True
del m._AUTOSYNC_NEWS[:]
k = m._ud_key(song)
with m._AUTOSYNC_LOCK:
    m._AUTOSYNC[k] = {"due": time.time(), "project": os.path.abspath(song)}
t = threading.Thread(target=m._autosync_run, args=(k,), daemon=True); t.start(); t.join(5)
assert not t.is_alive(), f"autosync spins on a gone file ({len(m._AUTOSYNC_NEWS)} news so far)"
assert not any("are in FL now" in n for n in m._AUTOSYNC_NEWS), m._AUTOSYNC_NEWS[:3]
assert any("gone" in str(a.get("verify_alert", "")) for a in alerts), alerts
assert m._pending_get(song), "the queue is kept (flh_sync discard=true drops it)"
m._pending_set(song, []); del m._AUTOSYNC_NEWS[:]
restore()

# ---- 2. the background check: FL ignored two reloads, the edit is put back into the file - and FL still holds the
#         OLD copy. Its next save (fl_save, the next edit's save) must be refused, or that edit is silently lost.
song = os.path.join(tmp, "Kept.flp"); shutil.copy(FIX, song)
old = open(song, "rb").read(); pre = m._fingerprint(song)
raw_tempo = getattr(m.flp_set_tempo, "__wrapped__", m.flp_set_tempo)
raw_tempo(song, 97.0)
written = open(song, "rb").read(); want = m._fingerprint(song)
assert want != pre
saves = []


def fl_save_old(p, wait_s=8.0, stop=None):                      # FL's save: it still holds the old copy
    saves.append(p)
    with open(p, "wb") as f:
        f.write(old)
    return True


ks = ("_save_in_fl_and_wait", "_reload_in_fl", "_open_state", "fl_running", "_open_project_guess")
o2 = {k: getattr(m, k) for k in ks}
m._save_in_fl_and_wait = fl_save_old
m._reload_in_fl = lambda p: m._note_fl_holds(p) or {}
m._open_state = lambda p: "open"
m.fl_running = lambda: True
m._open_project_guess = lambda: (song, [])
calls = []
o_call = m.BRIDGE.call
m.BRIDGE.call = lambda cmd, timeout=15.0, **a: calls.append(cmd) or {}
o_sleep = m.time.sleep
m.time.sleep = lambda s: None
try:
    r = m._verify_loaded(song, written, want, was_playing=True, pre=pre, gen=None)
finally:
    m.time.sleep = o_sleep
assert r.get("verified") is False, r
assert open(song, "rb").read() == written, "the edit is back in the file"
assert m._stale_block(song), "FL holds the old copy: a save through FL must be refused now"
try:
    o_call("transport", action="save")                            # the bridge's own guard (every save)
    raise AssertionError("bridge save should be refused")
except RuntimeError as e:
    assert "older copy" in str(e), e
m.BRIDGE.call = o_call
m._stale_clear(song)
for k_, v_ in o2.items():
    setattr(m, k_, v_)


# ---- 3. slow PC: after the hot reload the wait for FL's bridge is scaled. A big project on a slow PC (factor 3)
#         answering after 50 s used to get no autoplay stop (FL plays after a load) and no check of the edit.
class FakeTime:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, float(s))

    def __getattr__(self, n):
        return getattr(time, n)


ft = FakeTime()
song = os.path.join(tmp, "Slow.flp"); shutil.copy(FIX, song)
orig_b = open(song, "rb").read()
raw_tempo(song, 101.0)
ks = ("time", "_reload_in_fl", "_verify_in_background", "_speed_factor")
o3 = {k: getattr(m, k) for k in ks}
m.time = ft
m._reload_in_fl = lambda p: {}
m._verify_in_background = lambda *a, **k: "verify-x"
m._speed_factor = lambda refresh=False: 3.0
t_ready = ft.t + 50.0
sent = []


def slow_call(cmd, timeout=15.0, **a):
    if cmd == "ping" and ft.t < t_ready:
        ft.t += float(timeout)
        raise TimeoutError("no reply")
    sent.append((cmd, a.get("action")))
    return {}


o_call = m.BRIDGE.call; m.BRIDGE.call = slow_call
try:
    res = m._reload_edited(song, {}, {}, orig_b, None, False, None)
finally:
    m.BRIDGE.call = o_call
    for k_, v_ in o3.items():
        setattr(m, k_, v_)
assert res.get("reloaded_in_fl") is True, res
assert ("transport", "stop") in sent, f"FL's autoplay after the load must be stopped: {sent}"
assert res.get("verify_job") == "verify-x", res

# ---- 4. the cross-process file lock (mailbox, state.json, pending queue): a lock that times out on Windows must
#         close its file handle (each timeout leaked one; a held lock file then can't be removed / replaced)
import types  # noqa: E402
opened = []
real_open = open


class Tracked:
    def __init__(self, *a, **k):
        self.f = real_open(*a, **k); self.closed_ = False; opened.append(self)

    def __getattr__(self, n):
        return getattr(self.f, n)

    def close(self):
        self.closed_ = True; self.f.close()


fake_msvcrt = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0,
                                    locking=lambda fd, mode, n: (_ for _ in ()).throw(OSError("held")))
o_name, o_sleep = m.os.name, m.time.sleep
sys.modules["msvcrt"] = fake_msvcrt
import builtins  # noqa: E402
builtins.open = Tracked
m.time.sleep = lambda s: None
m.os.name = "nt"
try:
    try:
        with m._file_lock(os.path.join(tmp, "x.lock")):
            raise AssertionError("must not get the lock")
    except TimeoutError:
        pass
finally:
    m.os.name = o_name; m.time.sleep = o_sleep; builtins.open = real_open; sys.modules.pop("msvcrt", None)
assert opened and all(f.closed_ for f in opened), "the lock file's handle is closed after a timeout"

# ---- 5. fl_capture: a recorder thread orphaned by a start() timeout (a slow / hung driver) must not write its late
#         error or format into the NEXT recording (it set rec._err: the next take reported the old failure)
import fl_capture as C  # noqa: E402
gate = threading.Event(); orphan_done = threading.Event()
FMT = {"rate": 48000, "channels": 2, "bits": 32, "is_float": True}


class FakeSession:
    n = 0

    def __init__(self):
        FakeSession.n += 1; self.k = FakeSession.n; self.fmt = None

    def open(self, device=None, init_stream=True):
        if self.k == 1:                                           # the first stream hangs, then fails late
            gate.wait(10)
            raise OSError("late failure of the orphaned stream")
        self.fmt = dict(FMT)

    def start(self):
        pass

    def drain(self, got):
        got.append(b"\x00" * 8 * 480)
        return 480

    def close(self):
        if self.k == 1:
            orphan_done.set()


class FastEvent(threading.Event):
    def wait(self, timeout=None):                                 # start()'s 10 s open timeout, made short
        return super().wait(None if timeout is None else min(timeout, 0.3))


oc = {k: getattr(C, k) for k in ("_Session", "_ole32", "_co_init", "threading")}
C._Session = FakeSession; C._ole32 = lambda: None; C._co_init = lambda ole: False
C.threading = types.SimpleNamespace(Event=FastEvent, Thread=threading.Thread)
try:
    rec = C.LoopbackRecorder()
    try:
        rec.start(); raise AssertionError("the hung open must time out")
    except OSError as e:
        assert "timed out" in str(e), e
    rec.start()                                                   # the next take opens fine
    time.sleep(0.1)
    gate.set(); orphan_done.wait(5)                               # ... and meanwhile the orphan fails late
    sr, x = rec.stop()
    assert sr == 48000 and len(x), (sr, x.shape)
    assert rec.error is None, f"the orphan's error leaked into this take: {rec.error!r}"
finally:
    for k_, v_ in oc.items():
        setattr(C, k_, v_)

# ---- 6. render cache: a cache index lock that couldn't be taken (another fl-headless held it past the 60 s timeout,
#         or the folder can't be made) must not leave the in-process lock held: every later render's cache lookup
#         (a job holding FL) then hung forever
import fl_render_cache as RC  # noqa: E402
if RC.H is None:
    RC.bind(vars(m))
real_fl = RC.H["_file_lock"]


class Refused:
    def __init__(self, path):
        pass

    def __enter__(self):
        raise TimeoutError("another fl-headless is using FL right now")

    def __exit__(self, *a):
        return False


RC.H["_file_lock"] = Refused
wav = os.path.join(tmp, "render.wav")
with open(wav, "wb") as f:
    f.write(b"RIFF" + b"\0" * 100)
try:                                                              # the cache is an optimisation: a refused index lock
    assert RC.get("k" * 40) is None                               # is a miss, and a finished render isn't reported
    assert RC.put("k" * 40, "render", [{"src": wav, "mode": "copy", "name": "mix.wav"}]) is None   # as failed
finally:
    RC.H["_file_lock"] = real_fl
left = [d for d in os.listdir(RC.cache_dir()) if os.path.isdir(os.path.join(RC.cache_dir(), d))]
assert not left, f"the copy made for an entry that couldn't be indexed is removed (no orphan folders): {left}"
box = {}
th = threading.Thread(target=lambda: box.update(r=RC.get("k" * 40)), daemon=True); th.start(); th.join(5)
assert not th.is_alive(), "the render cache stayed locked after a refused index lock (every later lookup hangs)"

# ---- 7. fl_listen: FL not answering 'play' (busy, a popup) or 'stop' must still stop the speaker recording - the
#         loopback thread used to keep recording forever (~1.3 GB of audio an hour in memory)
import numpy as np  # noqa: E402
recs = []


class FakeRec:
    def __init__(self, device=None):
        self.stopped = False; recs.append(self)

    def start(self):
        pass

    def recent_peak(self, seconds=0.25):
        return 0.0

    def stop(self):
        self.stopped = True
        return 48000, np.zeros((4800, 2), np.float32)


oc = {k: getattr(C, k) for k in ("available", "LoopbackRecorder")}
ks = ("fl_running", "_sync_before", "_open_bpb")
o7 = {k: getattr(m, k) for k in ks}
C.available = lambda: (True, "ok"); C.LoopbackRecorder = FakeRec
m.fl_running = lambda: True; m._sync_before = lambda *a, **k: None; m._open_bpb = lambda: 4
o_call = m.BRIDGE.call
for bad in ("play", "stop_after"):
    seen = {"stops": 0}

    def call(cmd, timeout=15.0, **a):
        act = a.get("action")
        if cmd == "transport" and act == "play" and bad == "play":
            raise TimeoutError("no reply from FL")
        if cmd == "transport" and act == "stop":
            seen["stops"] += 1
            if bad == "stop_after" and seen["stops"] > 1:
                raise TimeoutError("no reply from FL")
        if cmd == "song":
            return {"song_mode": True, "bar": 1.0}
        return {}
    m.BRIDGE.call = call
    recs.clear(); out = None
    try:
        out = m.fl_listen(seconds=0.3)
    except Exception:
        pass
    assert recs and recs[-1].stopped, f"{bad}: the speaker recording is stopped"
    if bad == "stop_after":
        assert out and "stop" in str(out.get("WARNING")), f"a failed stop is said, not hidden: {out}"
m.BRIDGE.call = o_call
for k_, v_ in oc.items():
    setattr(C, k_, v_)
for k_, v_ in o7.items():
    setattr(m, k_, v_)

# ---- 8. FL holds an OLDER copy than the file (a reload failed): its 'save changes?' (fl_close, a switch) must not
#         be answered Yes/Save/Enter through fl_dialog - FL would write the old copy over the newer edits
song = os.path.join(tmp, "Stale.flp"); shutil.copy(FIX, song)
m._stale_mark(song, "reload timed out")
ks = ("fl_dialogs", "_open_state", "fl_running", "_open_project_guess")
o8 = {k: getattr(m, k) for k in ks}
ask = {"hwnd": 5, "title": "Confirm", "class": "TMsgForm", "kind": "save_changes", "text": ["Save changes to Stale?"],
       "buttons": ["Yes", "No", "Cancel"], "blocks_fl": True}
m.fl_dialogs = lambda: [ask]; m._open_state = lambda p: "open"; m.fl_running = lambda: True
m._open_project_guess = lambda: (song, [])
try:
    for b in ("Yes", "save", "enter", "Ja"):
        try:
            m.fl_dialog("click", b, 5)
            raise AssertionError(f"{b}: must be refused")
        except (ValueError, RuntimeError) as e:
            assert "older copy" in str(e), (b, e)
finally:
    for k_, v_ in o8.items():
        setattr(m, k_, v_)
    m._stale_clear(song)

# ---- 9. flh_reload (new code, same session) keeps what FL is known to hold: the checksums behind 'FL holds an
#         OLDER copy than the file' (_FL_HOLDS / _FH_WROTE) and the last save-identity proof. Losing them let FL's
#         next save overwrite a file fl-headless wrote behind FL's back. (Run last: it swaps the live namespace.)
import io  # noqa: E402
song = os.path.join(tmp, "Behind.flp"); shutil.copy(FIX, song)
m._note_fl_holds(song)                                            # FL loaded it...
raw_tempo(song, 88.0)                                             # ... then the file was written behind FL's back
m._note_wrote(song, open(song, "rb").read())
assert m._fl_behind_file(song)
m._OPEN_PROOF.update(path=song, title="Behind - FL Studio", t=time.time())
o_send = m._send; m._send = lambda msg: None
buf = io.StringIO(); real_out = sys.stdout; sys.stdout = buf
try:
    m.flh_reload()
finally:
    sys.stdout = real_out; m._send = o_send
live = sys._flh_live
try:
    assert live["_FL_HOLDS"] is m._FL_HOLDS and live["_FH_WROTE"] is m._FH_WROTE, "FL-holds checksums kept"
    assert live["_OPEN_PROOF"] is m._OPEN_PROOF, "the identity proof kept"
    assert live["_fl_behind_file"](song), "after a reload FL is still known to hold the older copy"
finally:
    del sys._flh_live

# a background note about a sandbox file that's gone by the next tool result is dropped (PC 2026-10-24 bench)
del m._AUTOSYNC_NEWS[:]
gone = os.path.join(os.path.dirname(song), "Bench Trap.flp"); open(gone, "wb").write(b"x")
m._news_add(gone, "Bench Trap.flp: levels balanced"); m._news_add(song, "song: in FL now"); os.remove(gone)
assert m._pop_autosync_news() == ["song: in FL now"] and not m._AUTOSYNC_NEWS_PATH

print("ok")
