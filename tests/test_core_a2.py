"""Fix wave A2 regressions: trial-box detection, collection names, old backups, verify honesty, the verify-stop flag,
flh_run vs. jobs, file-only jobs, read-only tools during jobs, link messages, user roots, argument checks, unknown
tools, $prev aliases, sampler names, no-op writes. Run: python3 tests/test_core_a2.py"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="flh_a2_")
R = os.path.join(TMP, "root"); os.makedirs(R)
os.environ["FLH_ROOTS"] = R
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
import fl_headless_mcp as m  # noqa: E402
import flh_store  # noqa: E402
ns = vars(m)


def copy(name, sub=""):
    d = os.path.join(R, sub); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name); shutil.copy(FIX, p); return p


def call(name, **a):
    r = m.handle({"method": "tools/call", "params": {"name": name, "arguments": a}})
    return r.get("isError", False), r["content"][-1]["text"]


# ---- F5: the trial 'Save anyway' box is recognized by its own words, never by a project's name
m._fl_main_window = lambda: (1, "Demo Song - FL Studio 2026")
for text, buttons in ((["Save changes to Demo 2?"], ["Yes", "No", "Cancel"]), (["Save changes to 'Beat demo'?"], []),
                      (["Save changes to trial run?"], []), (["Save changes to Demo Song?"], []),
                      (["Save changes to Demo Version 2?"], []), (["Änderungen an Toch speichern?"], ["Ja", "Nein"]),
                      (["Wijzigingen in toch opslaan?"], ["Ja", "Nee"]), (["Salvare le modifiche a comunque?"], [])):
    assert not m._is_save_anyway({"title": "Confirm", "text": text, "buttons": buttons}), text
for text, buttons in ((["Save anyway?"], []), (["x"], ["Save anyway", "Cancel"]), (["x"], ["Trotzdem speichern"]),
                      (["This project uses plugins in demo mode."], ["OK"]),
                      (["Save changes to Song? Some plugins run in demo mode."], [])):
    assert m._is_save_anyway({"title": "FL Studio", "text": text, "buttons": buttons}), text
assert m._has_anyway_word("Save anyway") and not m._has_anyway_word("yes") and not m._has_anyway_word("no")
print("trial box ok")

# ---- F7: normal collection names work; unusable ones say why
flh_store.register(ns)
d = flh_store._dir(); os.makedirs(d, exist_ok=True)
for name in ("Mike's Songs", "Album (2025)", "Rock & Roll", "Lo-Fi, Vol 2", "Beats #3"):
    json.dump({"name": name, "final_root": "X"}, open(os.path.join(d, name + ".json"), "w"))
    m._state({"collection": name})
    assert flh_store.profile_get().get("final_root") == "X", name
assert flh_store.profile_set("Rock & Roll (live)", {"style": "x"})["style"] == "x"
for bad in ("a/b", "..", "CON", "x.", "a:b", "x" * 80):
    try:
        flh_store.safe_name(bad); raise SystemExit(f"accepted {bad!r}")
    except ValueError:
        pass
m._state({"collection": "a/b"})
assert "can't be read" in flh_store.flh_profile()["note"]
m._profile_file("Rock & Roll")
print("names ok")

# ---- F8: flat 1.16 backups move into the per-project layout (or legacy), never orphaned
song = copy("song.flp"); d1 = copy("dup.flp", "a"); copy("dup.flp", "b")
os.makedirs(m.BACKUPS, exist_ok=True)
for n in ("20261001-120000-123-song.flp", "20261001-120000-124-dup.flp", "20261001-120000-125-gone.flp"):
    shutil.copy(FIX, os.path.join(m.BACKUPS, n))
m._MIGRATED["done"] = False
vs = m.flp_versions(song)
assert len(vs) == 1 and "legacy" not in vs[0] and m._backup_dir(song) in vs[0]["backup"], vs
vd = m.flp_versions(d1)
assert len(vd) == 1 and vd[0].get("legacy"), vd
r = m.flp_restore(d1, vd[0]["backup"])
assert r.get("warning") and "name" in r["warning"]
assert m.flp_restore(song, os.path.join(m.BACKUPS, "20261001-120000-123-song.flp"))["restored"] == song
assert os.path.isfile(os.path.join(m.LEGACY_BACKUPS, "20261001-120000-125-gone.flp"))
assert not [f for f in os.listdir(m.BACKUPS) if f.endswith(".flp")]
m.flp_diff(song)
print("old backups ok")

# ---- F9: verify says what it checked; edits it can't see are not 'verified: true'
p = copy("v.flp"); pre = m._fingerprint(p); old = open(p, "rb").read()
m.flp_set_tempo(p, 97); m._CALL_BACKUPS.clear()
written = open(p, "rb").read(); want = m._fingerprint(p)
assert want["tempo"] != pre["tempo"]
r = m._verify_loaded(p, written, dict(pre), False, pre, None)
assert r["verified"] is None and "can't be checked" in r["verify_note"], r
real_sleep = time.sleep
m.time.sleep = lambda s: real_sleep(min(s, 0.01))
m.BRIDGE.call = lambda *a, **k: {}
m._save_in_fl_and_wait = lambda project, wait_s=8.0, stop=None: True        # FL saved what it holds = the edit
r = m._verify_loaded(p, written, want, False, pre, None)
assert r["verified"] is True and r["checked"][0] == "tempo" and r["not_checked"], r   # (+ tempo_lane: the
# fixture's pattern holds a recorded tempo event at bar 1, which flp_set_tempo moves too)
m.time.sleep = real_sleep
print("verify ok")

# ---- F10 / F2 / F11 / F12: the FL lock with a job holding FL
m._windows_only = lambda n: False
m._save_in_fl_and_wait = vars(m)["_save_in_fl_and_wait"]
m._start_job("batch", lambda: time.sleep(9) or {}, fl=True)
time.sleep(0.5)
t = time.time(); assert m._fl_acquire() is False and time.time() - t < 3
assert not m._VERIFY_STOP.is_set()                                       # F10: a refusal leaves no flag behind
res = {}
th = threading.Thread(target=lambda: res.setdefault("r", m.flh_run([{"tool": "fl_play", "args": {"channel": 0}}])), daemon=True)
t = time.time(); th.start(); th.join(8)
assert not th.is_alive() and time.time() - t < 6, "flh_run blocked on the FL lock"           # F2
assert res["r"]["steps"][0].get("busy") and "busy" in res["r"]["steps"][0]["error"], res
m.fl_dialogs = lambda: [{"hwnd": 5, "title": "Missing", "kind": "missing_file", "text": [], "buttons": ["OK"]}]
t = time.time(); err, txt = call("fl_dialog", action="list")
assert not err and "Missing" in txt and time.time() - t < 1, txt                          # F12
j = m._start_job("masking", lambda **k: {"ok": 1}, live=False)["job"]
assert m.JOBS[j]["drives_fl"] is False                                                     # F11
j = m._start_job("anything", lambda: {"ok": 1}, fl=False)["job"]
assert m.JOBS[j]["drives_fl"] is False
assert m._job_drives_fl("mixeng", None, {"stems": "x"}) is False and m._job_drives_fl("mixeng", None, {}) is True
assert m._job_drives_fl("render", True) is True and m._job_drives_fl("collab") is False
m.fl_job(wait_s=12)
print("lock ok")

# ---- F13 / E2E-3: links, user roots
other = os.path.join(TMP, "D_drive", "FL Projects"); os.makedirs(other)
os.symlink(other, os.path.join(R, "FL Projects"))
lp = os.path.join(R, "FL Projects", "song.flp"); shutil.copy(FIX, lp)
try:
    m.writable(lp); raise SystemExit("link outside written")
except PermissionError as e:
    assert "is a link to" in str(e) and "pending" not in str(e), e
assert m.roots() == [os.path.normcase(os.path.abspath(R))] and m.PENDING_DIR not in json.dumps(m.fl_status())
m.writable(os.path.join(m.PENDING_DIR, "x", "y.flp"))                    # still writable for fl-headless itself
saved = os.environ.pop("FLH_ROOTS")
try:
    m.writable(song); raise SystemExit("no roots: written")
except PermissionError as e:
    assert "FLH_ROOTS isn't set" in str(e), e
os.environ["FLH_ROOTS"] = saved
print("roots ok")

# ---- E2E-11 / E2E-12: unknown tools, argument checks; $prev aliases
err, txt = call("flp_inf")
assert err and "flp_info" in txt and "flh_call" in txt and "flh_tools" in txt, txt
err, txt = call("flp_info", path=12345)
assert err and "'path'" in txt and "path" in txt, txt
err, txt = call("flp_info", pth=song)
assert err and "did you mean path" in txt, txt
assert m._check_args("flh_tools", {"limit": "3"}, m.TOOLS["flh_tools"])["limit"] == 3
assert m._check_args("flh_run", {"stop_on_error": "false", "steps": []}, m.TOOLS["flh_run"])["stop_on_error"] is False
r = m.flh_run([{"tool": "flp_set_tempo", "args": {"path": song, "bpm": 100}}, {"tool": "flp_info",
                                                                                 "args": {"path": "$prev.output"}}])
assert r["ok"], r
r = m.flh_run([{"tool": "flp_info", "args": {"path": song}}, {"tool": "flp_info", "args": {"path": "$prev.nope"}}])
assert not r["ok"] and "tempo_bpm" in r["steps"][-1]["error"], r
print("dispatcher ok")

# ---- E2E-8: a writer that changes nothing doesn't reload FL
hp = copy("noop.flp"); calls = []


def bridge(cmd, timeout=15.0, **a):
    calls.append((cmd, a.get("action")))
    if cmd == "transport" and a.get("action") == "save":
        os.utime(hp, (time.time() + 3, time.time() + 3))
    return {"playing": False}


m.BRIDGE.call = bridge
m._reload_in_fl = lambda p: calls.append(("reload", p)) or {}
m._open_project_guess = lambda: (hp, ["noop - FL Studio 2026"])
m.fl_running = lambda: True
m._fl_main_window = lambda: (1, "noop - FL Studio 2026")
r = m._with_open_project(hp, hp, lambda: {"output": hp, "removed": []})
assert r["reloaded_in_fl"] is False and "nothing changed" in r["note"] and ("reload", hp) not in calls, (r, calls)
print("ok")
