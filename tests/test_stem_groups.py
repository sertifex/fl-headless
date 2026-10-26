"""Stem groups for any project (no journal roles): drums / bass / music / vocals from channel names in any style and
from drum plugins; 'instrumental' = everything but vocals + the vocals. Run: python3 tests/test_stem_groups.py"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
os.environ["FLH_ROOTS"] = tempfile.mkdtemp(); os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
import fl_headless_mcp as m  # noqa: E402

names = {0: "Kick", 1: "Vocals_01", 2: "Bass Drum", 3: "808 (glide)", 4: "HH-closed", 5: "Piano", 6: "FPC",
         7: "Lead Vox", 8: "Strings", 9: "Double Bass", 10: "Sub Bass", 11: "Guitar Loop", 12: "adlibs"}
plug = {6: "FPC", 5: "FL Keys"}
m._structure = lambda d: ([], {i: {"name": n, **({"plugin": plug[i]} if i in plug else {})} for i, n in names.items()}, {})
jr = {}
m._journal_get = lambda p: {"channels": jr}
g = dict(m._stem_groups("x", b"", [], set(names), "roles"))
assert g["Drums"] == {0, 2, 4, 6}, g
assert g["Bass"] == {3, 9, 10}, g
assert g["Vocals"] == {1, 7, 12}, g
assert g["Music"] == {5, 8, 11}, g
inst = dict(m._stem_groups("x", b"", [], set(names), "instrumental"))
assert inst["Vocals"] == {1, 7, 12} and inst["Instrumental"] == set(names) - {1, 7, 12}, inst
m_ci, m_mn = m._channel_inserts, m._mixer_names
# a recorded take named after its insert ('Insert 2_03') goes with the mixer track's name
names[13] = "Insert 2_03"
m._channel_inserts = lambda d, ev=None: {13: 2}
m._mixer_names = lambda d, ev: {2: "Lead Vocal"}
g = dict(m._stem_groups("x", b"", [], set(names), "roles"))
assert 13 in g["Vocals"], g
# the song journal's roles win over names
jr.update({"5": {"role": "vocal"}, "0": {"role": "pad"}})
g = dict(m._stem_groups("x", b"", [], set(names), "roles"))
assert 5 in g["Vocals"] and 0 in g["Music"], g
# a list of groups: only channels that play
g = m._stem_groups("x", b"", [], {0, 1}, [{"name": "Beat", "channels": [0, 6]}, {"name": "Voice", "channels": [1]}])
assert g == [("Beat", {0}), ("Voice", {1})], g
print("stem groups ok")
