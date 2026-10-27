"""flp_edit: many edits in one step, all or nothing; inline notes; automation replace in place; clips by name."""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp
import fl_headless_mcp as m  # noqa: E402
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
reloads = []
real = m._with_open_project
def fake(project, output, fn):                       # count the would-be FL reloads
    if m._EDIT_DEPTH:
        return fn()
    reloads.append(output); return fn()
m._with_open_project = fake
src = os.path.join(tmp, "a.flp"); shutil.copy(FIX, src)
ch0 = m._fingerprint(src)["channels"]
notes = [[1, 1, "C4", 80], [2, 0.5, 64, 70], {"bar": 3, "len": 2, "key": "G3", "vel": 60}]
pts = [{"bar": 1, "db": -30}, {"bar": 5, "db": -12}]
r = m.flp_edit(src, [
    {"tool": "flp_replace_part", "channel": 0, "notes": notes},
    {"tool": "flp_add_automation", "name": "Fades", "track": 1, "points": pts, "playlist_track": 4},
    {"tool": "flp_add_automation", "name": "Fades", "track": 1, "replace": True,
     "points": [{"bar": 2, "db": -40}, {"bar": 9, "db": -6}]},
])
assert len(reloads) == 1, reloads                       # one save/reload for the whole batch
assert r["results"][0]["added"] == 3, r
assert r["results"][2].get("replaced") is True, r
fp = m._fingerprint(src)
assert fp["channels"] == ch0 + 1, (fp, ch0)            # replace didn't add a second channel
fades = [c for c in m.flp_playlist(src)["clips"] if c["name"] == "Fades"]
assert len(fades) == 1 and fades[0]["bar"] == 2.0 and fades[0]["track"] == 4, fades
# by name
m.flp_edit(src, [{"tool": "flp_playlist", "action": "remove", "names": ["Fades"]}])
assert not [c for c in m.flp_playlist(src)["clips"] if c["name"] == "Fades"]
# all or nothing
before = open(src, "rb").read()
try:
    m.flp_edit(src, [{"tool": "flp_add_automation", "name": "X", "track": 1, "points": pts},
                     {"tool": "flp_replace_part", "channel": 0, "notes": [[1, 1, "H9", 80]]}])
    raise SystemExit("bad op should fail")
except RuntimeError as e:
    assert "op 2" in str(e), e
assert open(src, "rb").read() == before, "failed batch must leave the file as it was"
try:
    m.flp_edit(src, [{"tool": "flp_render"}]); raise SystemExit("non-file tool must be refused")
except ValueError:
    pass
print("ok")
