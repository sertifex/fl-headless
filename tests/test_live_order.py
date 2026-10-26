"""Build speed + order (2.1.2): a big project's merged edits wait about as long as one reload costs (a song build's
burst of edits shares ONE reload), and live tools apply what's queued for FL first and never run while FL is still
loading. No FL needed. Run: python3 tests/test_live_order.py"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ.pop("FLH_COALESCE_S", None)
import fl_headless_mcp as m  # noqa: E402

m.FAST_FORCE = True
cost = {"s": None}
m.project_load_cost = lambda p: {"seconds": cost["s"]}
song = os.path.join(tmp, "Song.flp")
for c, want in ((None, m.COALESCE_S), (1.5, m.COALESCE_S), (12.0, 12.0), (45.0, 45.0), (300.0, m.COALESCE_MAX_S),
                (8.5, 8.5)):
    cost["s"] = c
    assert abs(m._coalesce_s(song) - want) < 1e-9, (c, m._coalesce_s(song), want)
m.FAST_FORCE = False
assert m._coalesce_s(song) == 0.0                                  # merging off: every edit reloads at once
m.FAST_FORCE = True

# live tools: a wait while FL is still loading; the queue stays queued (a build's edits share one reload) unless the
# tool targets what only a queued edit makes - then the queue goes in once and the tool runs again
calls = []
m._sync_before = lambda reason, project=None, file_only=False: calls.append(("sync", reason))
pend = {"q": {"x": {"ops": [1]}}}
m._pending_all = lambda: pend["q"]
busy = {"n": 2}


def load_busy(bridge_up=False):
    busy["n"] -= 1
    return busy["n"] >= 0


m._fl_load_busy = load_busy
m._wait_load_out = lambda project, wait_s=None, bridge_up=False: calls.append(("wait", project)) or 0.0
m._open_project_guess = lambda: (song, "Song")
m.IS_WINDOWS = True
live = m._fl_locked("fl_channel_set", lambda **k: calls.append(("ran", k)) or {"ok": True})
assert live(index=8, volume_db=0)["ok"]
assert [c[0] for c in calls] == ["wait", "ran"], calls                    # loading: waited; queue left alone
calls.clear(); busy["n"] = -5
tries = {"n": 0}


def needs_new(**k):
    tries["n"] += 1
    calls.append(("ran", k))
    if tries["n"] == 1:
        raise ValueError("no channel 8")
    return {"ok": True}


assert m._fl_locked("fl_channel_set", needs_new)(index=8)["ok"]
assert [c[0] for c in calls] == ["ran", "sync", "ran"], calls            # the queue went in, then again
calls.clear(); pend["q"] = {}
try:
    m._fl_locked("fl_mixer_set", lambda **k: (_ for _ in ()).throw(ValueError("no mixer track 99")))(track=99)
    raise SystemExit("a real missing track must still fail")
except ValueError:
    pass
assert not any(c[0] == "sync" for c in calls), calls                       # nothing queued: no sync, plain error
calls.clear()
m._fl_locked("fl_transport", lambda **k: calls.append(("ran", k)) or 1)(action="play")
assert [c[0] for c in calls] == ["ran"], calls                             # transport: not a live-ordered tool
assert m.LIVE_ORDERED_TOOLS <= set(m.TOOLS) | {"fl_organize", "fl_steps", "fl_batch"}, \
    sorted(m.LIVE_ORDERED_TOOLS - set(m.TOOLS))
print("ok")
