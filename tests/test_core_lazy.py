"""Live-first for big projects: load cost per project, lazy reloads (queue on a checked shadow copy, ONE save+reload
on sync, reads say so / read the queued state, edits made in FL meanwhile kept, auto-sync before FL needs the edits,
immediate writes take the queue first), flh_sync / modes / discard. Run: python3 tests/test_core_lazy.py"""
import json, os, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
T = m.TOOLS
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
src = os.path.join(tmp, "Big Song.flp"); shutil.copy(FIX, src)
call = lambda _tool, **kw: T[_tool][0](**kw)

# ---- load cost: rolling average per project; reload steps feed it
assert m.project_load_cost(src)["seconds"] is None
for s_ in (40, 50, 60, 70, 80, 90):
    m._note_load_cost(src, s_, "reload")
c = m.project_load_cost(src)
assert c["samples"] == 5 and c["seconds"] == 70.0 and c["last_s"] == 90.0, c       # last 5 only
m._json_write(m.LOAD_COSTS, {})
opened = []
m._tag_track = lambda: (3, "Bass"); m._tag_gone = lambda t, w, **k: (time.sleep(0.2), True)[1]
m._open_running_or_fail = lambda p, on_save="no": (opened.append(p), time.sleep(0.1))
st_ = m._state
_os = m._open_state; m._open_state = lambda p: "open"               # FL shows exactly this project
steps = m._reload_in_fl(src)
m._open_state = _os
assert "load" in steps and m.project_load_cost(src)["samples"] == 1 and m.project_load_cost(src)["source"] == "reload"
# fl_open of another project measures in the background (FL names it + the bridge sees its channels)
other = os.path.join(tmp, "Other.flp"); shutil.copy(FIX, other)
nch = m._fingerprint(other)["channels"]
m._fl_main_window = lambda: (1, "Other.flp - FL Studio 2026")
m.BRIDGE.call = lambda cmd, timeout=15.0, **a: {"channels": [{}] * nch} if cmd == "state" else {}
m._measure_load_later(other, time.time() - 12)
for _ in range(50):
    if m.project_load_cost(other)["seconds"]:
        break
    time.sleep(0.1)
assert 12 <= m.project_load_cost(other)["seconds"] < 20, m.project_load_cost(other)
print("load cost ok")

# ---- lazy mode: FL has the big project open, its reload costs 45 s
m._json_write(m.LOAD_COSTS, {}); m._note_load_cost(src, 45, "reload")
m.fl_running = lambda: True
m.fl_dialogs = lambda: []; m._fl_pids = lambda: [42]
m._open_project_guess = lambda: (src, ["Big Song - FL Studio 2026"])
assert m._reload_mode(src) == "lazy" and m._reload_mode(other) == "immediate"
reloads = []
fl_user_edit = {"tempo": None}


def fake_hot(project, fn):                     # FL saves first (with whatever the user did in FL), edit, reload
    if fl_user_edit["tempo"]:
        m.flp_set_tempo(project, fl_user_edit["tempo"])           # what FL's save writes: the user's change
    res = fn(); reloads.append(project)
    res["reloaded_in_fl"] = True; res["steps_s"] = {"save": 0.1, "load": 45}
    return res


m._hot_reload = fake_hot
orig = open(src, "rb").read()
notes = [[1, 1, "C4", 80], [2, 0.5, 64, 70], [3, 2, "G3", 60]]
r = call("flp_replace_part", project=src, output=src, channel=0, notes=notes)
assert r["queued"] is True and r["added"] == 3 and r["pending_ops"] == 1, r
assert r["output"] == src and m.PENDING_DIR not in json.dumps(r), r          # shadow path never leaks
assert open(src, "rb").read() == orig and not reloads                         # nothing touched FL or the file
r = call("flp_add_automation", project=src, output=src, name="Fades", track=1,
         points=[{"bar": 1, "db": -30}, {"bar": 5, "db": -12}])
assert r["queued"] and r["pending_ops"] == 2, r
r = call("flp_edit", project=src, ops=[{"tool": "flp_style_channels", "channels": [{"channel": 0, "name": "Queued"}]}])
assert r["queued"] and r["pending_ops"] == 3, r
# a bad edit fails NOW (checked on the shadow), nothing queued
try:
    call("flp_replace_part", project=src, output=src, channel=0, notes=[[1, 1, "H9", 80]])
    raise SystemExit("bad note accepted")
except Exception as e:
    assert "H9" in str(e) or "note" in str(e).lower(), e
assert len(m._pending_get(src)) == 3
# reads: content readers see the queued state; others say what's missing
n = call("flp_notes", path=src, channel=0)
assert n["pending_ops"] == 3 and "included" in n["pending_note"], n
assert m.PENDING_DIR not in json.dumps(n)
fades = [c for c in call("flp_playlist", project=src)["clips"] if c["name"] == "Fades"]
assert len(fades) == 1, fades                                                  # list mode reads the shadow
assert not [c for c in m.flp_playlist(src)["clips"] if c["name"] == "Fades"]   # the real file doesn't have it yet
h = call("flp_health", path=src)
assert h["pending_ops"] == 3 and "aren't in this result" in h["pending_note"], h
assert call("fl_status")["pending_ops"] == {"Big Song.flp": 3}
print("queue + reads ok")

# ---- sync: ONE save+reload, queued edits on top of what FL saved (the user changed the tempo in FL meanwhile)
fl_user_edit["tempo"] = 97
r = call("flh_sync")
assert len(reloads) == 1 and r["synced"][src]["ops"] == 3 and r["synced"][src]["reloaded_in_fl"], (reloads, r)
assert not m._pending_get(src) and not os.path.exists(os.path.dirname(m._shadow_path(src)))
info = m.flp_info(src)
assert abs(info["tempo_bpm"] - 97) < 0.01, info                                   # the user's FL edit survived
assert [c for c in m.flp_playlist(src)["clips"] if c["name"] == "Fades"]
assert len([x for x in m.flp_notes(src, channel=0)["notes"]]) >= 3
assert call("flh_sync")["note"] == "nothing was queued"
print("sync ok")

# ---- automatic sync before FL needs the edits (play), immediate writes take the queue first
fl_user_edit["tempo"] = None
call("flp_set_tempo", path=src, bpm=116)
assert len(m._pending_get(src)) == 1
m.BRIDGE.call = lambda cmd, timeout=15.0, **a: {"done": a.get("action")}
m.fl_transport("play")
assert len(reloads) == 2 and not m._pending_get(src)
call("flp_set_tempo", path=src, bpm=120)
fired = []
r = m._with_open_project(src, src, lambda: (fired.append(len(m._pending_get(src))), {"output": src})[1])
assert r["queued_ops_applied_first"] == 1 and len(reloads) == 3 and not m._pending_get(src), r
print("auto sync ok")

# ---- clip ids are never queued (FL renumbers them on save); writes elsewhere read the queued state
call("flp_set_tempo", path=src, bpm=124)
cid = m.flp_playlist(src)["clips"][0]["id"]
r = call("flp_playlist", project=src, action="mute", clips=[cid])
assert "queued" not in r and len(reloads) == 4 and not m._pending_get(src), r
call("flp_set_tempo", path=src, bpm=128)
copy = os.path.join(tmp, "copy.flp")
r = call("flp_set_tempo", path=src, bpm=100) if False else call("flp_style_channels", project=src, output=copy,
                                                                 channels=[{"channel": 0, "name": "Lead"}])
assert os.path.isfile(copy) and r["output"] == copy and r["pending_ops"] == 1, r
print("clip ids / elsewhere ok")

# ---- discard, modes, FL closed meanwhile, a queue that no longer applies
d = call("flh_sync", discard=True)
assert d["discarded"] == {src: ["flp_set_tempo"]} and not m._pending_get(src)
assert call("flh_sync", mode="immediate")["mode_set"] == "immediate" and m._reload_mode(src) == "immediate"
n0 = len(reloads)
call("flp_set_tempo", path=src, bpm=130)
assert len(reloads) == n0 + 1 and not m._pending_get(src)
call("flh_sync", mode="auto")
call("flp_set_tempo", path=src, bpm=132)
assert len(m._pending_get(src)) == 1
m.fl_running = lambda: False                                       # the user closed FL (it had saved)
r = call("flp_set_tempo", path=src, bpm=110)
assert "queued" not in r and not m._pending_get(src) and len(reloads) == n0 + 1      # applied to the file, no reload
assert m.flp_info(src)["tempo_bpm"] == 110
m.fl_running = lambda: True
m._pending_set(src, [{"tool": "flp_replace_part", "channel": 0, "notes": [[1, 1, "H9", 80]]}])
before = open(src, "rb").read()
try:
    call("flh_sync"); raise SystemExit("bad queue synced")
except RuntimeError as e:
    assert "nothing was changed" in str(e), e
assert open(src, "rb").read() == before and m._pending_get(src)                 # file intact, queue kept
try:
    m.fl_transport("play"); raise SystemExit("played with a broken queue")
except RuntimeError as e:
    assert "flh_sync(discard=true)" in str(e), e
call("flh_sync", discard=True)
print("discard / modes / closed ok")

# ---- switching projects: FL saves and leaves the big song, its queued edits go into the file (no reload);
# opening the song itself with edits queued = one sync reload
call("flp_set_tempo", path=src, bpm=141)
n0 = len(reloads); opened.clear()
m._stop_autoplay_later = lambda *a, **k: None; m._measure_load_later = lambda *a, **k: None
m._open_running_or_fail = lambda p, on_save="no": (opened.append(p), m.__dict__.__setitem__(
    "_open_project_guess", lambda: (p, [])))
m._fl_main_window = lambda: (1, "Big Song - FL Studio 2026")
r = m.fl_open(other)
assert opened == [other] and len(reloads) == n0 and not m._pending_get(src), (r, opened)
assert m.flp_info(src)["tempo_bpm"] == 141
m._open_project_guess = lambda: (src, ["Big Song - FL Studio 2026"])
call("flp_set_tempo", path=src, bpm=142)
r = m.fl_open(src)
assert r["via"].startswith("applying") and len(reloads) == n0 + 1 and not m._pending_get(src), r
print("switch ok")

# ---- flh_run sync=true; the queue survives a restart (it's a file)
call("flp_set_tempo", path=src, bpm=134)
assert json.load(open(m.PENDING_FILE))[m._ud_key(src)]["ops"][0]["tool"] == "flp_set_tempo"
res = m.flh_run([{"tool": "flp_info", "args": {"path": src}}], sync=True)
assert res["ok"] and res["synced"][src]["ops"] == 1 and not m._pending_get(src), res
print("ok")
