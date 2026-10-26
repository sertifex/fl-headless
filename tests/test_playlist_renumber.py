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

# FL saved BETWEEN the list and the edit, handing the old ids to other clips (PC 2026-10-24: a batch remove by
# ids hit the wrong clips / failed): the ids from the last list still mean the clips that list showed
def fl_rotates_ids(project):
    d = m._load(project); ev = m._events(d); k, recs = m._playlist_event(d, ev)
    recs = [bytearray(r) for r in recs]
    ids = [struct.unpack("<I", r[32:36])[0] for r in recs]
    for r, new in zip(recs, ids[1:] + ids[:1]):
        r[32:36] = struct.pack("<I", new)
    m._save(m._splice(d, ev, [(k, k + 1, m._data_event(m.EV_PLAYLIST, b"".join(bytes(r) for r in recs)))]), project)
m._with_open_project = lambda project, output, fn: fn()
shutil.copy(FIX, src)
listed = m.flp_playlist(src)["clips"]
assert len(listed) >= 2 or "corpus" in FIX, listed
if len(listed) >= 2:
    target = listed[0]
    fl_rotates_ids(src)
    m.flp_playlist(src, "remove", clips=[target["id"]])
    left = m.flp_playlist(src)["clips"]
    assert key(target) not in [key(c) for c in left] and len(left) == len(listed) - 1, "the renumbered id hit another clip"
    # the listed clip changed in FL since the list: refused, nothing removed
    listed = m.flp_playlist(src)["clips"]
    d = m._load(src); ev = m._events(d); k, recs = m._playlist_event(d, ev); recs = [bytearray(r) for r in recs]
    i0 = next(i for i, r in enumerate(recs) if struct.unpack("<I", r[32:36])[0] == listed[0]["id"])
    recs[i0][0:4] = struct.pack("<I", struct.unpack("<I", recs[i0][0:4])[0] + 96)       # moved in FL
    m._save(m._splice(d, ev, [(k, k + 1, m._data_event(m.EV_PLAYLIST, b"".join(bytes(r) for r in recs)))]), src)
    try:
        m.flp_playlist(src, "remove", clips=[listed[0]["id"]]); raise SystemExit("acted on a clip that changed")
    except ValueError as e:
        assert "list the clips again" in str(e), e
    assert len(m.flp_playlist(src)["clips"]) == len(listed)
print("ok")
