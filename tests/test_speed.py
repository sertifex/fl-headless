"""Speed paths (on by default on Windows, forced on here): a burst of edits to the open project goes into FL in ONE
save+reload a moment after the last one (merged, in the background, news with the next result); mixer and channel
name/color edits go into FL live (no file edit, no reload) and FL saves them into the file shortly after; a live op
that fails falls back to the file; queued edits keep live routes off; immediate mode keeps the old behaviour; a
flush that fails keeps the edits queued and says so. Run: python3 tests/test_speed.py"""
import json, os, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_speed_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ["FLH_COALESCE_S"] = "0.6"
import fl_headless_mcp as m  # noqa: E402

m.FAST_FORCE = True
src = os.path.join(tmp, "Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), src)
m.fl_running = lambda: True
m._open_state = lambda p: "open"
m._open_project_guess = lambda: (src, ["Song - FL Studio 2026"])
m.fl_dialogs = lambda: []
reloads, saves, batches, tempo_calls = [], [], [], []
fail = {"hot": False, "batch": False}


def fake_hot(project, fn):                                  # FL saves, the edit is written, FL reloads once
    if fail["hot"]:
        raise RuntimeError("FL is busy (a popup)")
    res = fn(); reloads.append(time.time())
    res["reloaded_in_fl"] = True
    return res


def fake_call(cmd, timeout=15.0, **a):
    if cmd == "batch":
        ops = json.loads(a["ops"]); batches.append(ops)
        for o in ops:                                     # script v7: a fader set lands at FL's curve position
            if o.get("op") == "mixer" and o.get("volume") is not None:
                faders[int(o["track"])] = float(o["volume"])
        if fail["batch"]:
            return {"done": len(ops) - 1, "results": ["ok"] * (len(ops) - 1) + [{"error": "x"}]}
        return {"done": len(ops), "results": ["ok"] * len(ops)}
    if cmd == "set_tempo":
        tempo_calls.append(a["bpm"]); return {"tempo": a["bpm"]}
    if cmd == "mixer_set":                                # the later read-back of a live fader set
        return {"track": a["track"], "volume": faders.get(int(a["track"]), 0.8)}
    return {}


faders = {}


m._hot_reload = fake_hot
m.BRIDGE.call = fake_call
m.SET_VERIFY_S = 0.0
m._save_in_fl_and_wait = lambda p, wait_s=8.0, stop=None: saves.append(p) or True


def call(_tool, **a):
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": _tool, "arguments": a}})
    t = r["content"][0]["text"]
    return (json.loads(t) if not r.get("isError") else {"error": t})


def settle(timeout=6.0):
    t = time.time()
    while time.time() - t < timeout:
        if not m._AUTOSYNC and not m._pending_get(src):
            return True
        time.sleep(0.05)
    return False


try:
    orig = open(src, "rb").read()
    # a burst of three edits: each answers at once, FL reloads ONCE after the last
    r1 = call("flp_replace_part", project=src, output=src, channel=0, notes=[[1, 1, "C4", 90]])
    time.sleep(0.3)
    r2 = call("flp_set_tempo", path=src, bpm=88)
    time.sleep(0.3)
    r3 = call("flp_add_automation", project=src, output=src, name="Fade", track=1,
              points=[{"bar": 1, "db": -30}, {"bar": 3, "db": -6}])
    assert r1["queued"] and r1["reload"] == "merged" and r3["pending_ops"] == 3, (r1, r3)
    assert open(src, "rb").read() == orig and not reloads                    # nothing yet: still merging
    assert settle(), m._AUTOSYNC
    assert len(reloads) == 1, reloads                                         # three edits, one reload
    d = m._load(src)
    assert m.flp_info(src)["tempo_bpm"] == 88 and m._pending_get(src) == []
    r = call("flp_info", path=src)
    assert r.get("in_fl_now") and "3 merged edit(s)" in r["in_fl_now"][0], r                  # said with the next result
    # live: mixer and channel name/color - no file edit, no reload; FL saves them into the file a moment later
    before = open(src, "rb").read(); n_rel = len(reloads)
    r = call("flp_mixer", project=src, output=src, tracks=[{"track": 1, "name": "Drums", "color": "#303A50",
                                                             "volume_db": -3, "route_to": [12]}])
    assert r.get("live") is True and r["reloaded_in_fl"] is False, r
    assert batches[-1][0] == {"op": "mixer", "track": 1, "name": "Drums", "color": "#303A50",
                              "volume": round(m._fader(-3), 6), "route_to": "12"}, batches[-1]   # FL's curve (v7)
    assert open(src, "rb").read() == before
    r = call("flp_style_channels", project=src, output=src, channels=[{"channel": 0, "name": "Kick", "color": "#402020"}])
    assert r.get("live") is True and batches[-1][0]["op"] == "channel", (r, batches[-1])
    assert settle() and saves and len(reloads) == n_rel, (saves, reloads)    # saved into the file, never reloaded
    r = call("flp_set_tempo", path=src, bpm=95)                              # the tempo too
    assert r.get("live") is True and tempo_calls[-1] == 95, r
    # one flp_edit of mixer + channel names + tempo: ONE live batch, no reload
    nb = len(batches)
    r = call("flp_edit", project=src, ops=[{"tool": "flp_mixer", "tracks": [{"track": 2, "volume_db": -6}]},
                                           {"tool": "flp_style_channels", "channels": [{"channel": 0, "name": "Kick 2"}]},
                                           {"tool": "flp_set_tempo", "bpm": 96}])
    assert r.get("live") is True and len(batches) == nb + 1 and len(batches[-1]) == 2 and tempo_calls[-1] == 96, r
    assert settle() and len(reloads) == n_rel, reloads
    # an icon only exists in the file: that edit is merged instead
    r = call("flp_style_channels", project=src, output=src, channels=[{"channel": 0, "icon": 12}])
    assert r.get("queued") and not r.get("live"), r
    # while file edits are queued, mixer edits queue too (order stays simple)
    r = call("flp_mixer", project=src, output=src, tracks=[{"track": 2, "name": "Bass"}])
    assert r.get("queued") and not r.get("live"), r
    assert settle() and len(reloads) == n_rel + 1, reloads                    # both in one reload
    # an flp_edit with a note edit in it goes through the file (merged), live ops and all
    r = call("flp_edit", project=src, ops=[{"tool": "flp_mixer", "tracks": [{"track": 2, "pan": 0.1}]},
                                           {"tool": "flp_replace_part", "channel": 0, "notes": [[1, 1, "D4", 90]]}])
    assert r.get("queued") and not r.get("live"), r
    assert settle() and len(reloads) == n_rel + 2, reloads
    n_rel = len(reloads)
    # a live op that FL refuses: the file route sets the same values (merged)
    fail["batch"] = True
    r = call("flp_mixer", project=src, output=src, tracks=[{"track": 3, "name": "Keys"}, {"track": 4, "pan": 0.2}])
    assert r.get("queued") and not r.get("live"), r
    fail["batch"] = False
    assert settle()
    # a flush that fails: the edits stay queued and the next result says so
    fail["hot"] = True
    r = call("flp_add_automation", project=src, output=src, name="Fade 2", track=2,
             points=[{"bar": 1, "db": -20}, {"bar": 2, "db": -6}])
    time.sleep(1.5)
    assert m._pending_get(src), "kept queued"
    r = call("flp_info", path=src)
    assert "couldn't be put into FL automatically" in r.get("ALERT", ""), r
    fail["hot"] = False
    r = call("flh_sync")
    assert r["synced"] and not m._pending_get(src), r
    # immediate mode: no merging, no live routes (the old behaviour)
    m._state({"reload_mode": "immediate"}); n_rel = len(reloads)
    r = call("flp_set_tempo", path=src, bpm=92)
    assert not r.get("queued") and len(reloads) == n_rel + 1, r
    r = call("flp_mixer", project=src, output=src, tracks=[{"track": 1, "name": "Drums 2"}])
    assert not r.get("live") and len(reloads) == n_rel + 2, r
    m._state({"reload_mode": None})
    # fast paths off (not Windows, no FLH_FAST): every edit reloads at once, as before
    m.FAST_FORCE = False; n_rel = len(reloads)
    r = call("flp_set_tempo", path=src, bpm=93)
    assert not r.get("queued") and len(reloads) == n_rel + 1, r
    # FL saving the file right while a tool reads it: the read waits for the complete file instead of failing
    import threading
    full = open(src, "rb").read()
    with open(src, "wb") as f:
        f.write(full[:len(full) // 2])                                      # FL halfway through its save
    threading.Timer(0.4, lambda: open(src, "wb").write(full)).start()
    m._forget_file(src)
    assert m._load(src) == full
    with open(src, "wb") as f:
        f.write(full[:100])                                                 # cut short and nobody writing: an error
    t = time.time()
    try:
        m._load(src); raise AssertionError("a cut-off file loaded")
    except ValueError as e:
        assert "incomplete" in str(e) and time.time() - t < 2.5, e
    open(src, "wb").write(full)
    # a job that drives FL starting another one (fl_pc_check -> fl_render_stems): the inner one runs in place
    # instead of waiting for the outer one's FL lock forever
    def outer():
        return m._start_job("stems", lambda: {"stems": ["a"]}, fl=True)
    j = m._start_job("pccheck", outer, fl=True)
    m._JOB_EVENTS[j["job"]].wait(5)
    assert m.JOBS[j["job"]]["status"] == "done" and m.JOBS[j["job"]]["result"] == {"stems": ["a"]}, m.JOBS[j["job"]]
finally:
    m.FAST_FORCE = None
    shutil.rmtree(tmp, True)
print("speed ok")
