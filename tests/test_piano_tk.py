"""fl_piano_tk (Transkun): the semi-CRF decode against the reference fixtures (always); front end, model and
a full transcription when the add-on is installed (numpy result == onnxruntime result == PyTorch fixtures)."""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
import fl_piano_tk as K  # noqa: E402
FX = os.path.join(HERE, "fixtures", "transkun")


def ref_intervals(path, key=None):
    j = json.load(open(path))
    j = j.get("intervals", j)
    return [[tuple(x) for x in j[str(k)]] for k in range(len(j))]


S = np.fromfile(os.path.join(FX, "ref3c_syn_S.f32"), "<f4").reshape(6, 6, 90)
z = np.zeros((5, 90), np.float32)
assert K.viterbi_backward(S, z) == ref_intervals(os.path.join(FX, "ref3c_syn_intervals.json"))
fj = json.load(open(os.path.join(FX, "ref3c_forced_intervals.json")))
assert K.viterbi_backward(S, z, fj["forcedStartPos"]) == ref_intervals(os.path.join(FX, "ref3c_forced_intervals.json"))
# continuous-Bernoulli mean: symmetric, 0.5 at 0, tends to 0 / 1
m = K.cb_mean(np.array([-50.0, -1.0, 0.0, 1e-5, 1.0, 50.0]))
assert abs(m[2] - 0.5) < 1e-9 and abs(m[3] - 0.5) < 1e-5 and m[0] < 0.03 and m[5] > 0.97 and abs(m[1] + m[4] - 1) < 1e-9
# resolve_overlapping: a re-strike cuts the earlier note of the same key
r = K.resolve_overlapping([{"start": 0, "end": 2, "pitch": 60}, {"start": 1, "end": 3, "pitch": 60}])
assert [(e["start"], e["end"]) for e in r] == [(0, 1), (1, 3)]

if not K.available():
    print("Transkun add-on not installed: model checks skipped"); print("ok"); sys.exit(0)
tk = K.Transkun()
d = K.model_dir()
ra = os.path.join(d, "ref3b_audio.f32")
if os.path.isfile(ra):                                       # developer fixtures present
    audio = np.fromfile(ra, "<f4")
    feats = np.fromfile(os.path.join(d, "ref3b_features.f32"), "<f4").reshape(66, 229, 6)
    F = tk.features(tk.frames(audio))
    assert np.abs(F - feats).max() < 5e-5, np.abs(F - feats).max()
    S_ref = np.fromfile(os.path.join(d, "ref3c_S.f32"), "<f4").reshape(66, 66, 90)
    for be in ("numpy", "auto"):
        o = tk.main.run({"featuresBatch": feats[None]}, backend=be)
        assert np.abs(o["S"] - S_ref).max() / np.abs(S_ref).max() < 1e-4, be
    assert K.viterbi_backward(S_ref, np.zeros((65, 90), np.float32)) == ref_intervals(os.path.join(d, "ref3c_intervals.json"))
    print("front end + model + decode match the PyTorch fixtures")
# end to end on a synthetic phrase: C4, then E4+G4 with the sustain pedal down
fs = 44100
def tone(f, dur, v):
    t = np.arange(int(fs * dur)) / fs
    return v * np.exp(-t * 1.5) * np.minimum(1, t / 0.003) * sum(np.sin(2 * np.pi * f * h * t) / h ** 1.4 for h in (1, 2, 3, 4, 5))
x = np.zeros(int(fs * 6.0))
for st, k, v in ((0.5, 60, 0.5), (2.0, 64, 0.4), (2.0, 67, 0.4), (3.5, 72, 0.45)):
    seg = tone(440 * 2 ** ((k - 69) / 12), 2.0, v)
    i = int(st * fs); x[i:i + len(seg)] += seg[:len(x) - i]
res = tk.transcribe((x / np.abs(x).max() * 0.6).astype(np.float32), fs)
got = sorted((round(n["t"], 1), n["key"]) for n in res["notes"])
print("synthetic:", got[:8], res["info"])
# a synthetic tone isn't a real piano (Transkun is trained on real ones): 3 of 4 found, nothing at a wrong pitch
found = [any(abs(t - st) <= 0.06 and kk == k for t, kk in got) for st, k in ((0.5, 60), (2.0, 64), (2.0, 67), (3.5, 72))]
assert sum(found) >= 3 and {k for _, k in got} <= {60, 64, 67, 72}, (found, got)

# through audio_to_midi: model auto picks Transkun, the pedal reaches the MIDI, a re-run reuses the listening
import tempfile
TMP = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = TMP
os.environ.setdefault("LOCALAPPDATA", TMP)
os.environ["FLH_UPDATE_CHECK"] = "0"
import fl_headless_mcp  # noqa: E402,F401
import flh_transcribe as T  # noqa: E402
wav = os.path.join(TMP, "phrase.wav")
import fl_audio_edit as AE  # noqa: E402
AE.save(wav, (x / np.abs(x).max() * 0.6).astype(np.float32), fs, bits=16)
mid = os.path.join(TMP, "phrase.mid")
r1 = T.audio_to_midi(wav, mode="piano", output_mid=mid, meter="4/4")
assert r1["model"]["name"].startswith("Transkun") and r1["notes_summary"]["kept"] >= 3, r1["model"]
r2 = T.audio_to_midi(wav, mode="piano", meter="3/4")
assert r2["steps_s"].get("reused_listening")
rb = T.audio_to_midi(wav, mode="piano", model="basic-pitch")
assert rb["model"]["name"].startswith("basic-pitch")
try:
    T.audio_to_midi(wav, mode="piano", model="nope")
    raise AssertionError("bad model accepted")
except ValueError:
    pass
print("ok")
