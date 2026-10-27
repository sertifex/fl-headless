"""Regression tests for the release bug hunt of the music / audio tools (M1-M8). Each block names the bug it pins.
Run: python3 tests/test_bughunt_release_music.py (no FL needed)."""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_bhrm_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import fl_music  # noqa: E402
ns = vars(m)
m.fl_running = lambda: False
T = ns["TOOLS"]
import flh_compose as C  # noqa: E402
import flh_perform as PF  # noqa: E402
import flh_catalog as K  # noqa: E402
import fl_audio_edit as A  # noqa: E402
CORPUS = os.path.join(HERE, "corpus")
_n = [0]


def copy(name):
    _n[0] += 1
    d = os.path.join(tmp, f"p{_n[0]}"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".flp"); shutil.copy(os.path.join(CORPUS, name + ".flp"), p)
    return p


# ---------------------------------------------------------------------------------------------------------------
# M1 a major key with a genre that only has minor progressions (drill, metal, techno) read the minor numerals on
# the MAJOR scale ('Cm B A G' for i-VII-VI-V, metal 'Cm C# B') and kept the melody in major over them.
C_MINOR = {0, 2, 3, 5, 7, 8, 10}
for gname in ("drill-uk", "drill-ny", "metal", "techno"):
    for sd in (1, 2, 3):
        chs, info = C.harmony("C major", gname, 8, None, sd)
        assert info.get("key") == "C minor" and "used C minor" in info["note"], (gname, info)
        for c in chs:                                   # roots on the C minor scale (+ the phrygian bII, Db)
            assert c["root"] in C_MINOR | {1}, (gname, sd, c["name"], info)
        assert not {"B", "A", "C#"} & {c["name"] for c in chs}, (gname, info)
chs, info = C.harmony("C major", "drill-ny", 8, None, 3)
assert info["chords"] == ["Cm", "Bb", "Ab", "G"], info          # i-VII-VI-V read in C minor
chs, info = C.harmony("C minor", "drill-ny", 8, None, 3)
assert "key" not in info and "note" not in info, info            # a minor key: nothing switched
chs, info = C.harmony("C major", "pop", 8, None, 1)
assert "key" not in info, info                                   # a genre with major progressions: unchanged
assert C.parallel_key("Db major", "minor") == "Db minor" and C.parallel_key("F# major", "minor") == "F# minor"
assert C.genre_key("Cb major", "drill-uk") == ("Cb minor", "drill-uk has no major progressions: used Cb minor")
assert C.genre_key("A minor", "drill-uk") == ("A minor", None) and C.genre_key("C major", "pop") == ("C major", None)
bt = C.compose_beat("drill-uk", "C major", 8, 2)
assert bt["key"] == "C minor", bt["key"]
mel = bt["parts"]["melody"]
assert mel and sum(1 for n in mel if n["k"] % 12 == 4) == 0, "E natural in a C minor melody"
assert sum(1 for n in mel if n["k"] % 12 in C_MINOR | {1, 11}) >= 0.9 * len(mel), [n["k"] for n in mel]
bass = bt["parts"]["bass"]
assert all(n["k"] % 12 != 4 for n in bass), "E natural in a C minor bass"
assert bt["musicality"]["score"] >= 60, bt["musicality"]         # scored on the real (now consistent) parts
p = copy("base")
r = T["flp_compose"][0](project=p, what="chords", genre="metal", key="C major", dry_run=True, seed=2)
assert r["key"] == "C minor" and any("metal has no major progressions: used C minor" in x for x in r.get("notes", [])), r
r = T["flp_compose"][0](project=p, what="melody", genre="techno", key="C major", dry_run=True, seed=2)
assert r["key"] in ("C minor", "C major"), r                     # the song's own chords may be followed
print("M1 ok")

# ---------------------------------------------------------------------------------------------------------------
# M2 quantize ends=true guarded the end with an ABSOLUTE tick (max(g, ...)): every note after the first grid step
# got length 1; half-grid ties used banker's rounding (480 -> 0 but 1440 -> 1920 on a 960 grid).
nt = [{"pos": p_, "len": 110, "key": 60, "vel": 100, "ch": 0} for p_ in (0, 480, 965, 1440)]
q = fl_music.apply(nt, {"op": "quantize", "grid": "1/4", "ends": True}, 960)
assert [(n["pos"], n["len"]) for n in q] == [(0, 960), (960, 960), (960, 960), (1920, 960)], q
q = fl_music.apply(nt, {"op": "quantize", "grid": "1/8", "ends": True}, 960)
assert all(n["len"] >= 480 for n in q), q
q = fl_music.apply([{"pos": p_, "len": 10, "key": 60, "vel": 100, "ch": 0} for p_ in (240, 720, 1200)],
                   {"op": "quantize", "grid": "1/8"}, 960)
assert [n["pos"] for n in q] == [480, 960, 1440], q                # half a grid always rounds up
print("M2 ok")

# ---------------------------------------------------------------------------------------------------------------
# M3 audio_finish: TypeError on < 0.4 s input, IndexError on a quiet file (absolute -40 dB gate), and a silent
# miss of the target when the true peak capped the gain (no limiter: -14 asked -> -25.3).
fs = 44100


def wav(name, y):
    p_ = os.path.join(tmp, name); A.save(p_, y, fs, 24); return p_


t = np.arange(int(0.3 * fs)) / fs; s = 0.5 * np.sin(2 * np.pi * 440 * t)
try:
    m.audio_finish(wav("short.wav", np.stack([s, s], 1)), os.path.join(tmp, "short.flac"), target_lufs=-14)
    raise SystemExit("a 0.3 s file was finished")
except ValueError as e:
    assert "0.4 s" in str(e), e
qt = 1e-3 * np.sin(2 * np.pi * 440 * np.arange(fs * 5) / fs)
r = m.audio_finish(wav("quiet.wav", np.stack([qt, qt], 1)), os.path.join(tmp, "quiet.flac"), target_lufs=-14)
assert abs(r["lufs"] + 14) < 0.5 and r["end_s"] > 4.5, r
rng = np.random.default_rng(0); n = fs * 20; y = np.zeros(n)
for k in range(0, n, fs // 2):
    y[k:k + 2000] += 0.95 * rng.normal(0, 1, 2000) * np.exp(-np.arange(2000) / 200)
y += 0.01 * rng.normal(0, 1, n); y = np.clip(y, -1, 1)
dr = wav("drums.wav", np.stack([y, y], 1))
lim = ns.pop("_finalize_limit")
try:                                                            # no limiter loaded: say the target was missed
    r0 = m.audio_finish(dr, os.path.join(tmp, "d0.flac"), target_lufs=-14)
finally:
    ns["_finalize_limit"] = lim
assert r0["below_target_db"] > 5 and "true-peak ceiling" in r0["note"] and r0["limited"] is False, r0
r1 = m.audio_finish(dr, os.path.join(tmp, "d1.flac"), target_lufs=-14)
assert r1["limited"] is True and r1["lufs"] > r0["lufs"] + 3 and r1["true_peak_dbfs"] <= -0.9, r1
assert r1["lufs"] >= -14.5 or (r1["below_target_db"] > 0 and r1["note"]), r1
print("M3 ok")

# ---------------------------------------------------------------------------------------------------------------
# M4 _bpm_key: 'bpm\b' missed '90bpm_Am' ('_' is a word character).
for stem, want in (("Loop_90bpm_Am", (90, "A minor")), ("Loop_90BPM_Am", (90, "A minor")),
                   ("Piano_Loop_120bpm_C#min", (120, "C# minor")), ("bpm140_Gm_loop", (140, "G minor")),
                   ("Loop_bpm95_Dm", (95, "D minor")), ("Synth_Ebm_128bpm", (128, "Eb minor")),
                   ("Loop 128 BPM Gmaj", (128, "G major")), ("Trap_Loop_140_bpm_Am", (140, "A minor"))):
    assert K._bpm_key(stem) == want, (stem, K._bpm_key(stem))
assert K.classify_sample("/x/Packs/Loops/Piano_Loop_90bpm_Am.wav", "/x/Packs")["bpm"] == 90
print("M4 ok")

# ---------------------------------------------------------------------------------------------------------------
# M5 swing in compound / odd meters paired 8ths from the bar start (6/8: beat 2 at 720 moved) or absolute tick 0
# (fl_music in 7/8: bar 2's downbeat at 1680 moved).
div = 480; sw = {"ratio": 0.62, "grid": 8}
g = {"first_bar": 1, "sections": [{"name": "A", "bar": 1}]}
c = PF._Ctx(g, {"div": div, "notes": [{"s": 0, "e": div * 3 * 4}], "meters": [(0, 6, 8)]})
assert [round(c.swing(k * 240, sw)) for k in range(7)] == [k * 240 for k in range(7)]   # 6/8: straight 8ths
assert c.swing_skipped == {"6/8"}
assert round(c.swing(120, {"ratio": 0.62, "grid": 16})) > 120                         # 16ths still swing
c = PF._Ctx(g, {"div": div, "notes": [{"s": 0, "e": div * 14}], "meters": [(0, 7, 8)]})
assert round(c.swing(1680, sw)) == 1680 and round(c.swing(1440, sw)) == 1440 and round(c.swing(240, sw)) > 240
assert round(c.swing(1560, sw)) == 1560                                                # the odd last 8th: straight
c = PF._Ctx(g, {"div": div, "notes": [{"s": 0, "e": div * 8}], "meters": [(0, 4, 4)]})
assert round(c.swing(240, sw)) == round(0.62 * 480) and round(c.swing(720, sw)) == 480 + round(0.62 * 480)
nt = [{"pos": k * 240, "len": 200, "key": 60, "vel": 90, "ch": 0} for k in range(14)]
o = fl_music.apply(nt, {"op": "swing", "grid": "1/8", "amount": 0.62}, div, meters=[(0, 7, 8)])
assert o[7]["pos"] == 1680 and o[6]["pos"] == 1440 and o[1]["pos"] > 240 and o[8]["pos"] > 1920, [x["pos"] for x in o]
o = fl_music.apply(nt, {"op": "swing", "grid": "1/8", "amount": 0.62, "meter": "6/8"}, div)
assert [x["pos"] for x in o] == [x["pos"] for x in nt]
o = fl_music.apply(nt, {"op": "swing", "grid": "1/8", "amount": 0.62}, div)         # no meter: 4/4 as before
assert o[1]["pos"] == 240 + round(0.24 * 240) and o[2]["pos"] == 480
print("M5 ok")

# ---------------------------------------------------------------------------------------------------------------
# M7 flp_analyze_music on a .mid counted 4/4 bars whatever the file's time signature (FF 58) and read GM drums
# (channel 10) as pitches for the key.
mdiv = 480; bar = 3 * mdiv
tr = [(0, b"\xff\x58\x04\x03\x02\x18\x08")]
for i, ch in enumerate([[60, 64, 67], [65, 69, 72], [67, 71, 74], [60, 64, 67]] * 2):
    for k in ch:
        tr += [(i * bar, bytes([0x90, k, 90])), (i * bar + bar - 10, bytes([0x80, k, 0]))]
p = os.path.join(tmp, "waltz.mid"); m.midi_write(p, mdiv, [("piano", tr)])
r = T["flp_analyze_music"][0](path=p)
assert r["chords"][:4] == ["1: C", "2: F", "3: G", "4: C"] and r["meter"] == "3/4" and abs(r["bars"] - 8) < 0.1, r
lead = []
for i in range(16):
    k = [60, 62, 64, 65, 67, 69, 71, 72][i % 8]; lead += [(i * mdiv, bytes([0x90, k, 90])), (i * mdiv + 400, bytes([0x80, k, 0]))]
drm = []
for i in range(64):
    k = [36, 42, 38, 42][i % 4]; drm += [(i * mdiv // 4, bytes([0x99, k, 100])), (i * mdiv // 4 + 100, bytes([0x89, k, 0]))]
p = os.path.join(tmp, "cmaj_drums.mid"); m.midi_write(p, mdiv, [("lead", lead), ("drums", drm)])
r = T["flp_analyze_music"][0](path=p)
p2 = os.path.join(tmp, "cmaj.mid"); m.midi_write(p2, mdiv, [("lead", lead)])
r2 = T["flp_analyze_music"][0](path=p2)
assert r["key"] == r2["key"] and r["key"][0][0] == "C major" and r["drums_skipped"] == 64, (r["key"], r2["key"])
assert r2["meter"] == "4/4" and "drums_skipped" not in r2
print("M7 ok")

# ---------------------------------------------------------------------------------------------------------------
# M8 flp_compare_midi merged notes doubled across source tracks but not the song's copies: comparing a song with
# its own MIDI export reported a layer's 77 notes as "new / not in the source".
p = copy("base"); mid = os.path.join(os.path.dirname(p), "base.mid")
m.flp_export_midi(p, mid)
r = T["flp_compare_midi"][0](project=p, midi=mid)
c_ = r["counts"]
assert c_["missing"] == 0 and c_["new"] == 0 and c_["repeated_new_runs"] == 0, c_
assert c_.get("source_doubled_notes_merged") and c_["doubled_notes_matched"] == c_["source_doubled_notes_merged"], c_
print("M8 ok")

# ---------------------------------------------------------------------------------------------------------------
# M5b flp_edit_notes passed no meter to fl_music: swing 8ths in a 6/8 song moved the bar's second dotted-quarter
# beat (the 4th eighth) late.
p = copy("one_channel")
m.flp_project_info(p, p, time_sig_num=6, time_sig_beat=8)
d = m._load(p); ev = m._events(d); P = m._ppq(d); pid = min(m._patterns(d, ev))
eighth = P // 2
notes = [{"pos": i * eighth, "len": eighth // 2, "key": 60, "vel": 100, "ch": 0} for i in range(6)]
m._save(m._write_pattern_notes(d, ev, pid, notes), p)
m.flp_edit_notes(p, [{"op": "swing", "grid": "1/8", "amount": 0.66}], output=p, pattern=pid)
d = m._load(p); ev = m._events(d)
pos = sorted(m._rec_to_note(r)["pos"] for r in m._patterns(d, ev)[pid]["notes"])
assert 3 * eighth in pos, (pos, "the 6/8 bar's second beat moved")
print("M5b ok")
print("ok")
