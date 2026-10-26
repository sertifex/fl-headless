"""Edits of the open project: a writer that calls other writers inside (flp_extend_ending -> flp_playlist resize)
does ONE FL save and ONE reload, with no 30 s self-wait on the verify lock (PC 2026-10-22: edit step 31.2 s, reload
timed out); after a reload that failed nothing saves FL's old copy over the newer file (fl_save, the background
check, the merged-edit flush, fl_open's save, the next edit's save) until FL loaded it; a new edit arriving while a
merged reload runs doesn't wait for that reload, and the merged reload doesn't wait for the previous reload's
check. Run: python3 tests/test_reload_nested.py (no FL needed: FL is simulated)."""
import os, shutil, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_nested_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_shape  # noqa: E402
ns = vars(m)
flh_store.register(ns); flh_shape.register(ns)
m._install_live_first(ns)
m.FL_LOCK_WAIT_S = 6.0                       # the old self-wait would show as >= 6 s here (30 s on the PC)

p = os.path.join(tmp, "Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), p)
FL = {"mem": open(p, "rb").read(), "saves": 0, "loads": 0, "fail_load": False, "log": []}


def _bump(path):
    t = time.time() + FL["saves"] + FL["loads"] + 1
    os.utime(path, (t, t))


def fake_call(cmd, timeout=15.0, **a):
    FL["log"].append((cmd, a.get("action")))
    if cmd == "transport" and a.get("action") == "save":        # FL writes what it holds in memory
        FL["saves"] += 1
        with open(p, "wb") as f:
            f.write(FL["mem"])
        _bump(p)
    if cmd == "state":
        return {"playing": False}
    if cmd == "mixer_set":                                        # FL's mixer track names: what FL holds (a reload's
        t = int(a["track"]); ev = m._events(FL["mem"])            # marker is read back, then renamed back live)
        if a.get("name") is not None:
            FL["mem"] = m._mixer_name_bytes(FL["mem"], ev, t, a["name"]); ev = m._events(FL["mem"])
        return {"name": m._mixer_names(FL["mem"], ev).get(t) or f"Insert {t}"}
    return {}


def fake_open(path, on_save="no", **k):                         # FL's File > Open: it loads the file from disk
    if FL["fail_load"]:
        raise TimeoutError("FL didn't load the project in time (simulated)")
    FL["loads"] += 1; FL["mem"] = open(path, "rb").read()


m.BRIDGE.call = fake_call
m.fl_running = lambda: True
m.fl_dialogs = lambda: []
m._fl_pids = lambda: [42]
m._open_project_guess = lambda: (p, ["Song - FL Studio 2026"])
m._open_running_or_fail = fake_open
m._tag_track = lambda: (3, "Bass")
m._tag_gone = lambda t, w, **k: True
m._untag = lambda tr, orig: None
m._fl_main_window = lambda: (1, "Song - FL Studio 2026")
m.FAST_FORCE = False                                           # immediate mode: every edit reloads at once
os.environ.pop("FLH_FAST", None)
try:
    # ---- 1. nested writers: one save, one reload, no self-wait
    T0 = time.time()
    r = m.TOOLS["flp_extend_ending"][0](project=p, output=p, seconds=20)
    took = time.time() - T0
    assert took < 4.0, (took, r.get("steps_s"))
    assert FL["saves"] == 1 and FL["loads"] == 1, (FL["saves"], FL["loads"], r.get("steps_s"))
    assert r["reloaded_in_fl"] and r["clips_grown"] >= 1, r
    assert FL["mem"] == open(p, "rb").read(), "FL must hold the edited file"
    assert not m._VERIFY_LEAKS, m._VERIFY_LEAKS                     # no 'leaked lock' heal on the way
    print("nested writers ok", round(took, 2), "s")

    # ---- 2. the reload fails: the file holds the edit, FL the old copy -> nothing may save FL over it
    FL["fail_load"] = True
    before_mem = FL["mem"]
    r = m.TOOLS["flp_extend_ending"][0](project=p, output=p, seconds=10)
    assert r["reloaded_in_fl"] is False and r.get("fl_shows_old_version"), r
    edited = open(p, "rb").read()
    assert edited != before_mem and FL["mem"] == before_mem
    saves = FL["saves"]
    s = m.fl_save()
    assert s["saved"] is None and "older" in s["why"] and FL["saves"] == saves, s
    assert open(p, "rb").read() == edited                            # the edit survived fl_save
    assert m._save_in_fl_and_wait(p, 1.0) is False and FL["saves"] == saves     # verify / autosync / extensions
    assert open(p, "rb").read() == edited
    v = m._verify_loaded(p, edited, m._fingerprint(p), False, None)
    assert v["verified"] is None and FL["saves"] == saves and "older" in v["verify_note"], v
    try:
        m._prove_open(p); raise SystemExit("_prove_open saved over the newer file")
    except RuntimeError as e:
        assert "older" in str(e), e
    assert FL["saves"] == saves and open(p, "rb").read() == edited
    # the real bridge refuses such a save too (extensions that call it directly)
    try:
        m._Bridge.call(m.BRIDGE, "transport", action="save"); raise SystemExit("bridge saved over the newer file")
    except RuntimeError as e:
        assert "older" in str(e), e
    assert open(p, "rb").read() == edited
    # the next edit doesn't save FL first (it would undo the last one): it edits the file and reloads
    FL["fail_load"] = False
    r = m.TOOLS["flp_extend_ending"][0](project=p, output=p, seconds=5)
    assert FL["saves"] == saves and r["reloaded_in_fl"], (FL["saves"], saves, r)
    assert FL["mem"] == open(p, "rb").read() and "fl_held_older_copy" in r, r
    d = m._load(p); n, _ = m._song_x(d, m._events(d))
    assert m._stale_block(p) is None                                 # FL holds the file again: saves allowed
    assert m.fl_save()["saved"] == p and FL["saves"] == saves + 1
    print("no save over newer edits ok")

    # ---- 2b. fl_open of the stale project loads the file without saving FL first
    FL["fail_load"] = True
    m.TOOLS["flp_extend_ending"][0](project=p, output=p, seconds=4)
    FL["fail_load"] = False
    edited = open(p, "rb").read(); saves = FL["saves"]
    o = m.fl_open(p)
    assert FL["saves"] == saves and FL["mem"] == edited and m._stale_block(p) is None, (o, FL["saves"], saves)
    print("fl_open after a failed reload ok")

    # ---- 3. speed: a new edit while a merged flush runs doesn't wait for it; the flush doesn't wait for the check
    gate = threading.Event(); entered = threading.Event()
    real_hot = m._hot_reload

    def slow_hot(project, fn):                                   # the flush's save+reload takes a while in FL
        entered.set(); gate.wait(5.0)
        r = m._as_edit(fn)
        m._note_fl_holds(project)                                # (its simulated reload: FL holds the file now)
        return r
    sl = m.flp_info(p)["song_length_bars"]
    m._pending_set(p, [{"tool": "flp_set_song_length", "bars": sl + 3}])
    m._shadow(p)
    m._hot_reload = slow_hot
    th = threading.Thread(target=lambda: m._sync_pending(p)); th.start()
    assert entered.wait(3.0)
    t0 = time.time()                                             # a new edit while the flush is in FL: no wait
    r2 = m._run_lazy("flp_set_song_length", m.flp_set_song_length, {"path": p, "bars": sl + 5}, "path", None, p)
    waited = time.time() - t0
    gate.set(); th.join(5.0); m._hot_reload = real_hot
    assert waited < 1.0, waited
    assert r2.get("queued"), r2
    left = m._pending_get(p)
    assert [o.get("bars") for o in left] == [sl + 5], left            # the flushed edit left, the new one queued
    assert abs(m.flp_info(p)["song_length_bars"] - (sl + 3)) < 0.01   # the flushed edit is in the file
    sh, _ = m._shadow(p)                                              # rebuilt from the flushed file + the new edit
    assert abs(m.flp_info(sh)["song_length_bars"] - (sl + 5)) < 0.01
    m._pending_set(p, [])
    # the merged flush makes a running check of the previous reload step aside instead of waiting it out
    m.FL_LOCK_WAIT_S = 30.0
    m._PENDING_VERIFY.clear()
    edited = open(p, "rb").read(); fp = m._fingerprint(p)
    jid = m._verify_in_background(p, edited, dict(fp, notes="x"), False, dict(fp, notes="y"))
    time.sleep(0.3)                                                   # the check holds FL now, waiting to save
    t0 = time.time()
    m._verify_step_aside()
    with m._FL_LOCK:
        took = time.time() - t0
    m._JOB_EVENTS[jid].wait(3.0)
    assert took < 1.6, took
    assert m.JOBS[jid]["result"].get("verify_note") == "a newer edit checks it", m.JOBS[jid]
    print("no avoidable waits ok", round(waited, 2), round(took, 2))
finally:
    shutil.rmtree(tmp, True)
print("reload nested ok")
