"""flp_write_notes on a project whose one pattern isn't on the playlist (FL's empty template + flp_new_pattern, PC
2026-10-26: 'the playlist has no pattern clip to set the song length with'): the pattern is placed at bar 1 and the
notes play. No FL needed. Run: python3 tests/test_write_notes_place.py"""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402

p = os.path.join(tmp, "Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), p)
ids = [c["id"] for c in m.flp_playlist(p)["clips"]]
m.flp_playlist(p, "remove", clips=ids)
d = m._load(p)
assert not m._clips(d, m._events(d)) and len(m._patterns(d, m._events(d))) == 1
r = m.flp_write_notes(p, p, [{"channel": 0, "notes": [[1, 1, 60, 100], [3, 1, 64, 100]]}], tempo="keep")
assert "placed" in r and r["notes_written"] == 2, r
info = m.flp_info(p)
assert info["clips"] >= 1 and info["song_length_bars"] >= 4 and info["notes_per_channel"].get(0) == 2, info
print("ok")
