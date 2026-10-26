"""If FL shows no sign of loading after a direct reopen, reload falls back to blank template -> project."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fl_headless_mcp as m
tmp = tempfile.mkdtemp(); proj = os.path.join(tmp, "song.flp"); blank = os.path.join(tmp, "blank.flp")
for f in (proj, blank):
    open(f, "wb").write(b"x")
opened = []
m._open_state = lambda p: "open"
m._open_running_or_fail = lambda p, on_save="no", **k: opened.append((p, on_save)) or True
m._blank_project = lambda: blank
m._mark_changed = lambda: True
m._tag_track = lambda: (3, 'Bass')
m._tag_gone = lambda t, w, **k: False
m._state = lambda *a, **k: {}
m.BRIDGE.call = lambda *a, **k: {"playing": False}               # never shows a load
m._fl_load_seen = lambda wait_s, ignore_after_s=2.5: False
m._reload_in_fl(proj)
assert opened == [(proj, "no"), (blank, "no"), (proj, "no")], opened
opened.clear(); m._tag_gone = lambda t, w, **k: True
m._reload_in_fl(proj)
assert opened == [(proj, "no")], opened
print("ok")
