"""Open-project sync with live changes (2.2.0): a live change made in FL (fl_plugin_set on a mixer effect) survives a
queued edit's flh_sync - also after a reload that couldn't be proven (FL marked as holding an older copy: the save is
a probe, kept only when FL is proven to hold the file's edits, else a clear refusal with the file untouched and the
edit still queued); the post-sync check never puts an edit back over live changes; a no-op lazy write while FL holds
an older copy is queued (so the sync loads the file into FL); a tempo map batched with replace_part ops sticks; a
tempo lane re-saved by FL (master tempo at the playhead, re-rounded points) raises no 'didn't stick' alert while a
real stale lane still does; flp_remove_channels / flp_write_arrangement queue in lazy mode. Simulated FL
(tests/test_e2e.py FakeFL + VClock). Run: python3 tests/test_sync_live.py"""
import os, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_e2e as E  # noqa: E402  (env, the server, FakeFL, VClock)

m, ns = E.m, E.ns
E.VCLOCK.install()
FAILS = []
T = m.TOOLS


def check(cond, msg):
    if not cond:
        FAILS.append(msg); print("FAIL", msg)
    return cond


def call(name, **kw):
    return T[name][0](**kw)


def track_name(data, t):
    d = data if isinstance(data, bytes) else open(data, "rb").read()
    return m._mixer_names(d, m._events(d)).get(t)


WORK = os.path.join(E.ROOTS, "_fake_fl_ram"); os.makedirs(WORK, exist_ok=True)


def held_edit(FL, fn):
    """A change inside FL's memory only (what a live call does): FL's held bytes edited through a temp file."""
    tmp = os.path.join(WORK, "held.flp")
    with open(tmp, "wb") as f:
        f.write(FL.held)
    m._edit_enter()
    try:
        fn(tmp)
    finally:
        m._edit_exit()
    FL.held = open(tmp, "rb").read()


song = os.path.join(E.ROOTS, "Live Song.flp")
shutil.copy(os.path.join(E.CORPUS, "base.flp"), song)
FL = E.FakeFL().install()
FL.fl_open(song)
m._note_fl_holds(song)
base_bridge = FL.bridge
LIVE_NAME = {"n": 0}


def bridge(cmd, timeout=15.0, **a):
    if cmd == "plugin_set" and a.get("slot", -1) >= 0:            # a mixer effect's knob: FL's memory only
        LIVE_NAME["n"] += 1
        held_edit(FL, lambda p: m.flp_mixer(p, p, [{"track": int(a["index"]), "name": f"LIVE{LIVE_NAME['n']}"}]))
        return {"index": a["index"], "param": a["param"], "value": a["value"]}
    return base_bridge(cmd, timeout=timeout, **a)


FL.bridge = bridge
m.BRIDGE.call = bridge
m._note_load_cost(song, 45, "reload")
call("flh_sync", mode="lazy")
NOTES = [[1, 1, "C4", 90], [2, 0.5, 64, 80]]

# ---- 1. normal path: live knob, then a queued edit + sync -> both in FL (FL saved first, the edit on top)
r = call("fl_plugin_set", index=3, param=0, value=0.54, slot=0)
check(m._live_unsaved() and m._live_unsaved()[0][0] == "fl_plugin_set", f"live change logged: {m._LIVE_LOG[-1:]}")
r = call("flp_edit", project=song, ops=[{"tool": "flp_replace_part", "channel": 0, "notes": NOTES}])
check(r.get("queued") is True, f"queued: {r}")
s = call("flh_sync")
check(s["synced"].get(song, {}).get("reloaded_in_fl"), f"synced: {s}")
check(track_name(song, 3) == "LIVE1" and track_name(FL.held, 3) == "LIVE1", "live change kept (normal path)")
check(len(m.flp_notes(song, channel=0)["notes"]) >= 2, "queued notes in")
check(not m._live_unsaved(), "nothing unsaved after the sync")
print("normal path ok")

# ---- 2. a reload that couldn't be proven (FL really loaded it): FL is marked stale; a live change after it is
# kept by the next queued edit's sync (FL's save is a probe: it holds the file's edit, so it is the base)
fake_reload = FL.reload
unproven = {"n": 0}


def orig_reload(project, **kw):                                  # a proven reload clears the stale mark (as the real one)
    out = fake_reload(project, **kw)
    m._stale_clear(project)
    return out


def reload_unproven(project, **kw):
    if unproven["n"]:
        unproven["n"] -= 1
        return dict(fake_reload(project, **kw), unproven="test: the marker wasn't read back")
    return orig_reload(project, **kw)


ns["_reload_in_fl"] = reload_unproven
unproven["n"] = 1
call("flp_edit", project=song, ops=[{"tool": "flp_style_channels", "channels": [{"channel": 0, "name": "Edit A"}]}])
s = call("flh_sync")
check(m._stale_get(song) and s["synced"][song].get("WARNING"), f"unproven reload -> stale: {s}")
check(m._STALE_FL[m._ud_key(song)].get("prev") is not None, "the version before the edit kept with the stale mark")
call("fl_plugin_set", index=4, param=0, value=0.3, slot=0)             # live, after the unproven reload
call("flp_edit", project=song, ops=[{"tool": "flp_replace_part", "channel": 0, "notes": NOTES[:1]}])
s = call("flh_sync")
syn = s["synced"].get(song, {})
check(syn.get("reloaded_in_fl") and "fl_plugin_set" in str(syn.get("live_kept")), f"synced through the probe: {s}")
check(track_name(song, 4) == "LIVE2" and track_name(FL.held, 4) == "LIVE2", "live change kept after a stale mark")
check("Edit A" in str(m.flp_list(song)), "the earlier (unproven) edit is still there")
check(not m._stale_get(song), "the probe proved FL held the file: stale mark cleared")
print("stale + live kept ok")

# ---- 3. FL really did NOT load the edit (old copy) and has a live change since: refused, file untouched, queued
def reload_ignored(project, **kw):
    FL.reloads.append(project)
    return {"load": 0.05, "unproven": "test: FL kept its copy"}


ns["_reload_in_fl"] = reload_ignored
call("flp_edit", project=song, ops=[{"tool": "flp_style_channels", "channels": [{"channel": 0, "name": "Edit B"}]}])
s = call("flh_sync")
check(m._stale_get(song), "FL ignored the reload -> stale")
ns["_reload_in_fl"] = orig_reload
call("fl_plugin_set", index=5, param=0, value=0.7, slot=0)              # FL: old copy + this live change
call("flp_edit", project=song, ops=[{"tool": "flp_replace_part", "channel": 0, "notes": NOTES}])
file_before = open(song, "rb").read(); held_before = FL.held
try:
    call("flh_sync"); check(False, "a sync that would lose either side was refused")
except RuntimeError as e:
    msg = str(e)
    check("nothing was changed" in msg and "fl_plugin_set" in msg and "save_current=false" in msg, msg[:400])
check(open(song, "rb").read() == file_before, "the file is exactly as it was")
check(FL.held == held_before and track_name(FL.held, 5) == "LIVE3", "FL's state untouched (live change still there)")
check(len(m._pending_get(song)) == 1, "the edit is still queued")
check(m._stale_get(song), "still marked as holding an older copy")
bks = [v.get("backup") for v in m.flp_versions(song) if isinstance(v, dict) and v.get("backup")]
check(any(os.path.isfile(b) and track_name(b, 5) == "LIVE3" for b in bks),
      f"FL's state (with the live change) backed up in flp_versions: {bks[:3]}")
call("flh_sync", discard=True)
print("stale + lost edit refused ok")

# recover: fl_open(save_current=false) loads the file (FL's live change dropped, as the message says)
m._stale_clear(song); FL.fl_open(song); m._note_fl_holds(song)

# ---- 4. the post-sync check never puts the edit back over live changes made after the reload
state = {"file": b"edited"}
saved = []
fake_fp = {"notes": "edited"}
orig = {k: ns[k] for k in ("_save_in_fl_and_wait", "_fingerprint", "_write_bytes", "_backup", "_reload_in_fl")}
ns["_save_in_fl_and_wait"] = lambda p, wait_s=8.0, **k: True
ns["_fingerprint"] = lambda p: dict(fake_fp)
writes = []
ns["_write_bytes"] = lambda p, data: writes.append(data)
ns["_backup"] = lambda *a, **k: "bk"
ns["_reload_in_fl"] = lambda p, **k: writes.append("reload")
t0 = m.time.time()
fake_fp["notes"] = "old"                                                  # FL's save == the version before the edit
m._note_live_change("fl_channel_set", {"index": 2})                      # ... but a live change went in meanwhile
v = m._verify_loaded(song, b"edited", {"notes": "edited"}, False, {"notes": "old"}, None, t0 - 1)
check(v["verified"] is False and "fl_channel_set" in v.get("live_kept", []) and not writes, f"no restore: {v} {writes}")
v = m._verify_loaded(song, b"edited", {"notes": "edited"}, False, {"notes": "old"}, None, m.time.time() + 1)
check(writes and writes[0] == b"edited", f"without live changes FL's old copy is still corrected: {writes[:2]}")
ns.update(orig)
m._note_fl_synced()                                                       # (that live change was only pretend)
print("verify keeps live changes ok")

# ---- 5. a writer that changes nothing (the file holds it) while FL holds an older copy: queued, the sync loads it
m.flp_tempo_write(song, song, points=[[1, 71.8], [2, 80.5], [3, 69.2]]); m._CALL_BACKUPS.clear()
m._stale_mark(song, "test: a reload that wasn't proven", open(song, "rb").read())
r = call("flp_tempo_write", project=song, output=song, points=[[1, 71.8], [2, 80.5], [3, 69.2]])
check(r.get("queued") is True and r.get("fl_behind"), f"no-op write queued because FL is behind: {r}")
n0 = len(FL.reloads)
s = call("flh_sync")
check(len(FL.reloads) == n0 + 1 and s["synced"][song].get("reloaded_in_fl"), f"the sync loaded the file: {s}")
check([round(x["bpm"], 1) for x in m.flp_tempo_map(FL.open)["map"]][:3] == [71.8, 80.5, 69.2] and
      m._fingerprint(song)["tempo_lane"] == m._tempo_lane_fp(FL.held, m._events(FL.held))[1], "FL holds the map")
r = call("flp_tempo_write", project=song, output=song, points=[[1, 71.8], [2, 80.5], [3, 69.2]])
check(not r.get("queued") and "nothing changed" in str(r.get("note")), f"a real no-op says so: {r}")
print("no-op write while FL behind ok")

# ---- 6. tempo map batched with replace_part ops sticks (file, FL, verify)
r = call("flp_edit", project=song, ops=[{"tool": "flp_replace_part", "channel": 0, "notes": NOTES},
                                        {"tool": "flp_tempo_write", "points": [[1, 90], [3, 100], [5, 84.5]]},
                                        {"tool": "flp_replace_part", "channel": 0, "notes": NOTES[:1]}])
check(r.get("queued"), f"batch queued: {r}")
s = call("flh_sync")
want = [90.0, 100.0, 84.5]
got_file = [round(x["bpm"], 1) for x in m.flp_tempo_map(song)["map"]][:3]
held_tmp = os.path.join(WORK, "held_check.flp"); open(held_tmp, "wb").write(FL.held)
got_fl = [round(x["bpm"], 1) for x in m.flp_tempo_map(held_tmp)["map"]][:3]
check(got_file == want and got_fl == want, f"batched tempo map in the file {got_file} and in FL {got_fl}")
print("batched tempo ok")

# ---- 7. FL re-saves the lane (master tempo at the playhead, points re-rounded): no false alert; a stale lane: alert
d = open(song, "rb").read(); want_fp = m._fingerprint(song)
resaved = os.path.join(WORK, "resaved.flp"); open(resaved, "wb").write(d)
m.flp_tempo_write(resaved, resaved, points=[[1, 90.004], [3 + 1 / 384, 100.003], [5, 84.498]])   # re-rounded
d2 = open(resaved, "rb").read(); ev2 = m._events(d2)                          # FL stopped inside bars 3-4:
d2 = m._splice(d2, ev2, [(k, k + 1, bytes([m.EV_TEMPO]) + m.struct.pack("<I", 100000))
                         for k, e in enumerate(ev2) if e["id"] == m.EV_TEMPO][:1])   # master only, lane kept
open(resaved, "wb").write(d2)
got_fp = m._fingerprint(resaved)
check(got_fp["tempo"] != want_fp["tempo"], "the master tempo moved (FL's playhead value)")
check(not m._fp_diff(want_fp, got_fp), f"no difference after FL's re-save: {m._fp_diff(want_fp, got_fp)}")
m._PENDING_VERIFY[os.path.normcase(os.path.abspath(resaved))] = {"want": want_fp, "t": m.time.time(), "job": None}
m._state({"verify_alert": None})
check(m._check_previous_edit(resaved) is True and not m._state_get("verify_alert"), "no 'didn't stick' alert")
stale = os.path.join(WORK, "stale.flp"); open(stale, "wb").write(d)
m.flp_tempo_write(stale, stale, points=[[1, 80.5], [3, 80.5], [5, 80.5]])        # FL's OLD tempo map
m._PENDING_VERIFY[os.path.normcase(os.path.abspath(stale))] = {"want": want_fp, "t": m.time.time(), "job": None}
check(m._check_previous_edit(stale) is False and "tempo" in str(m._state_get("verify_alert")),
      f"a real stale lane is still caught: {m._state_get('verify_alert')}")
m._state({"verify_alert": None})
check(m._lanes_close((96, ((0, 90.0), (384, 100.0))), (96, ((0, 90.2), (385, 100.0)))), "lane tolerance")
check(not m._lanes_close((96, ((0, 90.0), (384, 100.0))), (96, ((0, 90.0), (768, 100.0)))), "a moved point counts")
print("tempo lane compare ok")

# ---- 8. flp_remove_channels / flp_write_arrangement queue in lazy mode (no reload until the sync)
FL.fl_open(song); m._note_fl_holds(song); m._stale_clear(song)
n0 = len(FL.reloads); before = open(song, "rb").read()
nch = m._fingerprint(song)["channels"]
r = call("flp_remove_channels", project=song, channels=[nch - 1], force=True)
check(r.get("queued") is True and len(FL.reloads) == n0 and open(song, "rb").read() == before,
      f"remove_channels queued, FL and file untouched: {str(r)[:300]}")
r = call("flp_remove_channels", project=song, channels=[nch - 2], dry_run=True)
check(not r.get("queued") and len(m._pending_get(song)) == 1, f"a dry run isn't queued: {str(r)[:200]}")
tmpl = os.path.join(E.ROOTS, "Template One.flp"); shutil.copy(os.path.join(E.CORPUS, "one_channel.flp"), tmpl)
mid = os.path.join(E.ROOTS, "part.mid")
m.midi_write(mid, 96, [("p", [(t0_, bytes([0x90, k_, 90])) for t0_, k_ in ((0, 60), (384, 64), (768, 67))]
                        + [(t0_ + 96, bytes([0x80, k_, 0])) for t0_, k_ in ((0, 60), (384, 64), (768, 67))])])
if os.path.isfile(mid):
    r = call("flp_write_arrangement", template=tmpl, output=song, parts=[{"channel": 0, "midi": mid}],
             sections=[{"name": "A", "bar": 1}, {"name": "B", "bar": 2}])
    check(r.get("queued") is True and len(FL.reloads) == n0 and r.get("output") == song,
          f"write_arrangement into the open song queued: {str(r)[:300]}")
    ops = m._pending_get(song)
    check(ops[-1].get("_keep_input") and ops[-1].get("template") == os.path.abspath(tmpl), f"template kept: {ops[-1]}")
s = call("flh_sync")
check(len(FL.reloads) == n0 + 1 and not m._pending_get(song), f"ONE reload for the queued writers: {s}")
if os.path.isfile(mid):
    names = {p.get("name") for p in m.flp_patterns(song)}
    check({"A", "B"} <= names, f"the arrangement is in the file: {names}")
    check(open(tmpl, "rb").read() == open(os.path.join(E.CORPUS, "one_channel.flp"), "rb").read(),
          "the template wasn't touched")
print("lazy remove_channels / write_arrangement ok")

FL.uninstall()
if FAILS:
    print(f"{len(FAILS)} failure(s)"); sys.exit(1)
print("ok")
