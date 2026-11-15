"""fl_piano_check.self_check: render the transcription, compare with the recording bar by bar, re-check bad bars
with a second opinion. It must (1) repair a bar where the main notes are missing, (2) never trade correct notes
for a worse second opinion, (3) flag what it can't fix. No model needed: the "recording" is synthesized."""
import os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
import fl_piano_check as K  # noqa: E402
import fl_piano_clean as C  # noqa: E402

sr = K.SC_SR
def N(t, d, k, v=80):
    return {"t": t, "end": t + d, "key": k, "vel": v, "amp": v / 127, "op": 1.0, "onset": True}

# the truth: 8 bars of 2 s, a chord per bar plus a melody note in the second half
prog = [(48, 52, 55), (53, 57, 60), (55, 59, 62), (48, 52, 55)] * 2
truth = []
for i, ch in enumerate(prog):
    truth += [N(i * 2.0, 1.8, k, 75) for k in ch] + [N(i * 2.0 + 1.0, 0.9, ch[-1] + 12 + (i % 3) * 2, 85)]
n = int(17.0 * sr)
rec = K.synth_notes(truth, n) + np.random.default_rng(0).normal(0, 1e-4, n).astype(np.float32)
bands = C.key_bands(rec, sr)
bounds = np.arange(9) * 2.0

# (1) main transcription missed bar 3's chord and bar 6's melody; the second opinion has them
main = [q for q in truth if not (4.0 <= q["t"] < 6.0 and q["key"] < 70) and not (10.0 <= q["t"] < 12.0 and q["key"] >= 70)]
before = K.render_scores(bands, main, None, bounds, n)
assert before[2] < 0.8, before
fixed, rep = K.self_check(bands, main, None, bounds, n, {"second": truth})
bars = {r["bar"] for r in rep["repaired"]}
assert 3 in bars and rep["match_median"][1] >= rep["match_median"][0], rep
after = K.render_scores(bands, fixed, None, bounds, n)
assert after[2] > before[2] + 0.1 and after[2] > 0.9, (before, after)
assert all(after[j] >= before[j] - 0.02 for j in range(8)), (before, after)        # nothing got worse
print("repair:", rep["repaired"], "median", rep["match_median"])

# (2) the main notes are right and the second opinion is wrong (a semitone off): nothing changes
wrong = [dict(q, key=q["key"] + 1) for q in truth]
same, rep2 = K.self_check(bands, truth, None, bounds, n, {"second": wrong})
assert rep2["repaired"] == [] and len(same) == len(truth), rep2["repaired"]

# (3) a bar both get wrong stays flagged as uncertain
bad = [q for q in truth if not (12.0 <= q["t"] < 14.0)] + [N(12.0, 1.8, 61, 75), N(12.0, 1.8, 66, 75)]
out, rep3 = K.self_check(bands, bad, None, bounds, n, {"second": bad})
assert any(u["bar"] == 7 for u in rep3["still_uncertain"]), rep3["still_uncertain"]
assert rep3["uncertain"][1] >= 1
print("ok")
