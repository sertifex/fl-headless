"""Audio clip (sampler) settings: read what FL itself saved, then set and read back every control.
Uses a project FL 2026 saved after its controls were changed by hand (tests/data, kept out of git: it holds
plugin states). Run: python tests/test_audio_clip.py"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp
import fl_headless_mcp as m  # noqa: E402

m._with_open_project = lambda p, o, f: f()
src = os.path.join(tmp, "a.flp"); shutil.copy(FIX, src)
r = m.flp_audio_clip_settings(src, 5)                       # what was set by hand in FL
assert r["stretch_mode"] == "stretch_pro" and r["time_beats"] == 4.0 and r["pitch_cents"] == 106, r
assert r["reverse"] and r["normalize"] and r["remove_dc"] and r["reverse_polarity"], r
assert r["fade_stereo"] and r["swap_stereo"] and abs(r["start"] - 0.0347) < 1e-3 and r["length"] == 1.0, r
out = os.path.join(tmp, "b.flp")
w = m.flp_audio_clip_settings(src, 5, output=out, stretch_mode="e3_generic", time="2bar", pitch_cents=-300,
                              multiplier=2, reverse=False, normalize=False, remove_dc=False, reverse_polarity=False,
                              fade_stereo=False, swap_stereo=False, fade_in=1, fade_out=0.5, start=0.25, length=0.5)
r = m.flp_audio_clip_settings(out, 5)
assert r["stretch_mode"] == "e3_generic" and r["time_beats"] == 8.0 and r["pitch_cents"] == -300, r
assert r["multiplier"] == 2.0 and not any(r[k] for k in ("reverse", "normalize", "remove_dc", "reverse_polarity",
                                                           "fade_stereo", "swap_stereo")), r
assert r["fade_in"] == 1.0 and r["fade_out"] == 0.5 and r["start"] == 0.25 and r["length"] == 0.5, r
assert m.flp_info(out)["notes_per_channel"] == m.flp_info(src)["notes_per_channel"]   # nothing else touched
for bad in (dict(stretch_mode="warp"), dict(start=2), dict(fade_in=9), dict(multiplier=0)):
    try:
        m.flp_audio_clip_settings(src, 5, output=out, **bad); raise AssertionError(bad)
    except ValueError:
        pass
print("audio clip ok")
