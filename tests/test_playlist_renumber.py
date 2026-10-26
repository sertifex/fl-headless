"""FL renumbers clip ids when it saves the open project before an edit: remove/move still hit the clips asked for."""
import os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp
import fl_headless_mcp as m  # noqa: E402
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
src = os.path.join(tmp, "a.flp"); shutil.copy(FIX, src)

def fl_saves_and_renumbers(project, output, fn):          # what FL does before the edit
    d = m._load(project); ev = m._events(d); k, recs = m._playlist_event(d, ev)
    recs = [bytearray(r) for r in recs]
    for r in recs:
        r[32:36] = struct.pack("<I", struct.unpack("<I", r[32:36])[0] + 1000)
    m._save(m._splice(d, ev, [(k, k + 1, m._data_event(m.EV_PLAYLIST, b"".join(bytes(r) for r in recs)))]), project)
    return fn()
m._with_open_project = fl_saves_and_renumbers
before = m.flp_playlist(src)["clips"]
gone = before[1]
m.flp_playlist(src, "remove", clips=[gone["id"]])
after = m.flp_playlist(src)["clips"]
assert len(after) == len(before) - 1, (len(before), len(after))
key = lambda c: (c["kind"], c["bar"], c["track"], c.get("name"))
assert key(gone) not in [key(c) for c in after], "the wrong clip was removed"
print("ok")
