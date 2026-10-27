"""Playlist clip editing on a project FL 2026 saved (tests/data). Run: python tests/test_playlist.py"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp
import fl_headless_mcp as m  # noqa: E402

for t, want in (("FL Studio 2026", True), ("Project 2 - FL Studio 2026", True), ("Untitled - FL Studio 2026", True),
                ("Amber Light (Piano) - FL Studio 2026", False), ("Project notes - FL Studio 2026", False)):
    assert m._is_untitled(t) is want, t
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
m._with_open_project = lambda p, o, f: f()
src = os.path.join(tmp, "a.flp"); shutil.copy(FIX, src)
before = m.flp_playlist(src)["clips"]
pat = next(c for c in before if c["kind"] == "pattern")
audio = next(c for c in before if c["kind"] == "audio")
out = os.path.join(tmp, "b.flp")
r = m.flp_playlist(src, "place", output=out, pattern=pat["pattern"], bar=9, track=7, repeat=3, every_bars=2)
new = [c for c in m.flp_playlist(out)["clips"] if c["id"] in r["clips"]]
assert [c["bar"] for c in new] == [9.0, 11.0, 13.0] and all(c["track"] == 7 for c in new), new
m.flp_playlist(out, "move", clips=r["clips"], bar=21, track=9)
moved = [c for c in m.flp_playlist(out)["clips"] if c["id"] in r["clips"]]
assert [c["bar"] for c in moved] == [21.0, 23.0, 25.0] and all(c["track"] == 9 for c in moved), moved
m.flp_playlist(out, "resize", clips=r["clips"][:1], length_bars=1, trim_start_bars=0.5)
c0 = next(c for c in m.flp_playlist(out)["clips"] if c["id"] == r["clips"][0])
assert c0["bar"] == 21.5 and c0["length_bars"] == 1.0 and c0["from_bar_in_pattern"] == 1.5, c0
m.flp_playlist(out, "mute", clips=[audio["id"]])
assert next(c for c in m.flp_playlist(out)["clips"] if c["id"] == audio["id"])["muted"]
m.flp_playlist(out, "unmute", clips=[audio["id"]])
assert not next(c for c in m.flp_playlist(out)["clips"] if c["id"] == audio["id"])["muted"]
r2 = m.flp_playlist(out, "place", channel=audio["channel"], bar=5)
assert next(c for c in m.flp_playlist(out)["clips"] if c["id"] == r2["clips"][0])["kind"] == "audio"
m.flp_playlist(out, "remove", clips=r["clips"] + r2["clips"])
assert len(m.flp_playlist(out)["clips"]) == len(before)
for bad in (dict(action="move", clips=[999999], bar=1), dict(action="place", bar=1), dict(action="bogus")):
    try:
        m.flp_playlist(out, **bad); raise AssertionError(bad)
    except ValueError:
        pass
print("playlist ok")
