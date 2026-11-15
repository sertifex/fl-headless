"""fl_piano_tx: resampling, basic-pitch's note building on known grids, frame times; the real model when bundled."""
import os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
import fl_piano_tx as P  # noqa: E402

# resampling is band-limited and keeps the exact rate (no drift over minutes)
for fs, dur in ((48000, 70), (44100, 70), (96000, 6), (22050, 3)):
    t = np.arange(int(fs * dur)) / fs
    x = np.sin(2 * np.pi * 440 * t) + 0.3 * np.sin(2 * np.pi * 3000 * t)
    y = P.resample(x, fs)
    assert len(y) == round(len(x) * 22050 / fs)
    tt = np.arange(len(y)) / 22050
    e = np.abs(y - (np.sin(2 * np.pi * 440 * tt) + 0.3 * np.sin(2 * np.pi * 3000 * tt)))[3000:-3000]
    assert e.max() < 1e-3, (fs, e.max())

# note building: onset-led notes, a note found only by the melodia trick, too-short blips dropped
F = np.zeros((400, 88)); O = np.zeros((400, 88))
F[50:120, 39] = 0.8; O[50, 39] = 0.9                    # C4 with an onset
F[200:260, 43] = 0.6                                     # E4, no onset peak -> inferred onset
F[300:305, 50] = 0.9                                     # 5 frames: shorter than the minimum
F[320:390, 30] = 0.35; F[340:345, 30] = 0.1              # Eb3 with a short dip: one note (energy tolerance)
ev = P.notes_from_grids(F, O, infer_onsets=True)
keys = sorted((k, s, e) for s, e, k, a, o in ev)
assert [k for k, _, _ in keys] == [51, 60, 64], keys
k60 = [x for x in keys if x[0] == 60][0]
assert k60[1] == 50 and abs(k60[2] - 120) <= 1
assert P.notes_from_grids(F, O, min_key=62) and all(k >= 62 for _, _, k, _, _ in P.notes_from_grids(F, O, min_key=62))

# frame times: ~86 fps with basic-pitch's per-window correction
tt = P.frame_times(400)
assert abs(tt[86] - 86 * 256 / 22050) < 1e-9 and tt[200] < 200 * 256 / 22050

# the bundled model, end to end on a synthetic piano-like tone (C4 then an E4+G4 dyad)
try:
    mp = P.model_path()
except P.ModelMissing:
    mp = None
if mp:
    fs = 22050
    def tone(f, d):
        t = np.arange(int(fs * d)) / fs
        env = np.exp(-t * 2.5) * np.minimum(1, t / 0.005)
        return sum(env * np.sin(2 * np.pi * f * h * t) / h for h in (1, 2, 3, 4))
    x = np.concatenate([np.zeros(fs // 4), tone(261.63, 1.0), tone(329.63, 1.2) + tone(392.0, 1.2), np.zeros(fs // 2)])
    for be in ("numpy", "auto"):                         # numpy = what users without onnxruntime get
        r = P.transcribe(x * 0.3, fs, backend=be)
        got = {n["key"] for n in r["notes"] if n["end"] - n["t"] > 0.2}
        assert {60, 64, 67} <= got, (be, got, r["notes"][:10])
        if be == "numpy":
            ref = r["notes"]
        else:                                            # same notes either way
            assert [(n["key"], round(n["t"], 2)) for n in r["notes"]] == [(n["key"], round(n["t"], 2)) for n in ref]
        print("model:", r["info"])
else:
    print("model not bundled: end-to-end part skipped")
print("ok")
