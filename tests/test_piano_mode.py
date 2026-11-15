"""audio_to_midi mode='piano' end to end: synthesized piano audio -> the bundled model -> clean-up -> MIDI with a
tempo map / time signature / key signature, and the project write (flp_edit + song settings mocked)."""
import json, os, struct, sys, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
TMP = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = TMP
os.environ.setdefault("LOCALAPPDATA", TMP)
os.environ["FLH_UPDATE_CHECK"] = "0"
import fl_headless_mcp as m  # noqa: E402
import fl_piano_tx as PT  # noqa: E402
import flh_transcribe as T  # noqa: E402

try:
    PT.model_path()
except PT.ModelMissing:
    print("model not bundled: skipped"); print("ok"); sys.exit(0)

fs = 22050


def tone(f, d, vel):
    t = np.arange(int(fs * d)) / fs
    env = np.exp(-t * 1.8) * np.minimum(1, t / 0.004) * (vel / 127)
    return sum(env * np.sin(2 * np.pi * f * h * t) / h ** 1.3 for h in (1, 2, 3, 4, 5))


def hz(k):
    return 440.0 * 2 ** ((k - 69) / 12)


# a 3/4 waltz in G major, 12 bars, played with a tempo swell 88 -> 104 -> 84
prog = [0, 5, 7, 0]
score = []
for i in range(12):
    r = 43 + prog[i % 4]
    b0 = 3 * i
    score.append((b0, 2.8, r, 96))
    for bb in (1, 2):
        score += [(b0 + bb, 0.8, r + 12 + 4, 62), (b0 + bb, 0.8, r + 19, 60)]
    score.append((b0, 1.0, r + 24 + [0, 4, 7, 4][i % 4], 84))
nbeat = 3 * 12 + 2
bpm = np.interp(np.arange(nbeat), [0, nbeat / 2, nbeat - 1], [88, 104, 84])
bt = np.concatenate([[0.4], 0.4 + np.cumsum(60 / bpm)])
x = np.zeros(int((bt[-1] + 2) * fs))
for b, l, k, v in score:
    i = int(bt[int(b)] * fs)
    seg = tone(hz(k), min(l * 60 / 90 + 0.4, 3.0), v)
    x[i:i + len(seg)] += seg[:len(x) - i]
x = (x / np.abs(x).max() * 0.6).astype(np.float32)

mid = os.path.join(TMP, "waltz.mid")
r = T.audio_to_midi((x, fs), model="basic-pitch", output_mid=mid, mode="piano", full=True)
print({k: r[k] for k in ("meter", "key", "notes_summary", "hands")}, r["tempo"]["range_bpm"])
assert r["mode"] == "piano" and r["meter"]["sig"] == "3/4", r["meter"]
assert r["key"]["estimated"] in ("G major", "E minor"), r["key"]
got = {(round((b - 1) * 3), k) for p in r["notes"].values() for b, _, k, _ in p}
want = {(int(b), k) for b, _, k, _ in score}
off = min(b for b, _ in got) - min(b for b, _ in want)
hit = len({(b - off, k) for b, k in got} & want) / len(want)
assert hit > 0.85, hit
assert r["tempo"]["range_bpm"][1] - r["tempo"]["range_bpm"][0] > 8          # follows the swell
print("notes on the right beat:", round(hit, 3))

# the MIDI: conductor track with several tempo events, 3/4, G major; two hand tracks
data = open(mid, "rb").read()
assert data[:4] == b"MThd" and struct.unpack(">H", data[10:12])[0] in (3, 4)
assert data.count(b"\xff\x51\x03") > 5
assert b"\xff\x58\x04\x03\x02" in data and b"\xff\x59\x02\x01\x00" in data
assert r["output_mid"]["tracks"] in (["bass", "left", "right"], ["right", "left"]), r["output_mid"]["tracks"]

# project write: one flp_edit with a new pattern + notes; song settings only when asked
calls = []
m.flp_edit = lambda project, ops: calls.append(("edit", ops)) or {"ops": len(ops)}
T.H["flp_edit"] = m.flp_edit
T.H["flp_tempo_write"] = lambda p, o, midi=None, **k: calls.append(("tempo", open(midi, "rb").read().count(b"\xff\x51\x03"))) or {"ok": True}
T.H["flp_project_info"] = lambda p, o=None, **k: calls.append(("info", k)) or {}
T._unique_pattern_name = lambda project, base: base
proj = os.path.join(TMP, "song.flp")
r2 = T.audio_to_midi((x, fs), model="basic-pitch", mode="piano", project=proj, channel={"right": 0, "left": 1})
assert [c[0] for c in calls] == ["edit"], calls
ops = calls[0][1]
assert ops[0]["tool"] == "flp_new_pattern" and {o["channel"] for o in ops[1:]} == {0, 1}   # bass rides with left
assert "not changed" in r2["project"]["song_settings"]
calls.clear()
r3 = T.audio_to_midi((x, fs), model="basic-pitch", mode="piano", project=proj, channel=0, song_settings=True, hands=False)
kinds = [c[0] for c in calls]
assert kinds == ["edit", "info", "tempo"], kinds
assert calls[1][1] == {"time_sig_num": 3, "time_sig_beat": 4} and calls[2][1] > 5
assert r3["project"]["song_settings"]["time_signature"] == "3/4"

# cross-check: the same waltz played again (slower, other voicing, a fourth higher, a bar of noodling first)
def render(sc, bpms, transpose=0, lead_in=0.4):
    nb_ = int(max(b + l for b, l, _, _ in sc)) + 3
    bp = np.interp(np.arange(nb_), np.linspace(0, nb_ - 1, len(bpms)), bpms)
    bt_ = np.concatenate([[lead_in], lead_in + np.cumsum(60 / bp)])
    y = np.zeros(int((bt_[-1] + 2) * fs))
    for b, l, k, v in sc:
        i = int(np.interp(b, np.arange(len(bt_)), bt_) * fs)
        seg = tone(hz(k + transpose), min(l * 60 / 80 + 0.4, 3.0), v)
        y[i:i + len(seg)] += seg[:len(y) - i]
    return (y / np.abs(y).max() * 0.6).astype(np.float32)


score2 = [(b, l, k + (12 if 55 <= k < 60 else 0), max(40, v - 10)) for b, l, k, v in score]   # re-voiced
noodle = [(i * 0.5, 0.45, 72 + (i * 5) % 9, 60) for i in range(6)]
y2 = render(noodle + [(b + 3, l, k, v) for b, l, k, v in score2], [76, 90, 80], transpose=5)
ab = os.path.join(TMP, "ab.flac")
rx = T.audio_to_midi((x, fs), model="basic-pitch", mode="piano", compare_with=(y2, fs), meter="3/4", ab_wav=ab, full=True)
xc = rx["cross_check"]
print("cross-check:", {k: xc.get(k) for k in ("lines_up", "alignment_cost", "other_is_higher_by_semitones", "summary")})
assert xc["lines_up"] and xc["other_is_higher_by_semitones"] == 5, xc
assert xc["summary"]["agree"] + xc["summary"]["partial"] >= 0.8 * xc["bars_this"] and xc["summary"]["disagree"] <= 1
assert rx["confidence"]["per_bar"]["low"] <= 2 and rx["confidence"]["per_bar"]["chroma_match_median"] > 0.8
assert all(b["cross"] is not None for b in rx["confidence"]["bars"] if b["notes"])
fl = open(ab, "rb").read(4)
assert fl == b"fLaC" and rx["ab_wav"]["left"] == "the recording"
# a different piece (a march in D, 4/4) doesn't line up: no verdicts, said plainly
march = []
for i in range(10):
    r_ = 38 + [0, 7, 9, 5][i % 4]
    march += [(4 * i, 1.9, r_, 95), (4 * i + 2, 1.9, r_ + 7, 75)]
    march += [(4 * i + bb, 0.45, r_ + 24 + [0, 4, 7, 4][bb], 70) for bb in range(4)]
rn = T.audio_to_midi((x, fs), model="basic-pitch", mode="piano", compare_with=(render(march, [110]), fs), meter="3/4")
assert rn["cross_check"]["lines_up"] is False and "don't line up" in " ".join(rn["warnings"]), rn["cross_check"]
assert "other_is_higher_by_semitones" not in rn["cross_check"]

# a given meter is used as is; bad mode names are refused
r4 = T.audio_to_midi((x, fs), model="basic-pitch", mode="piano", meter="6/8")
assert r4["meter"]["sig"] == "6/8"
try:
    T.audio_to_midi((x, fs), model="basic-pitch", mode="pianoo")
    raise AssertionError("bad mode accepted")
except ValueError as e:
    assert "piano" in str(e)
print("ok")
