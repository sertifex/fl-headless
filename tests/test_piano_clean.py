"""fl_piano_clean: on synthetic performances with known answers - rubato tempo maps, 3/4 vs 4/4 vs 6/8, key used
only for semitone ties, pedal pieces merged, overtone ghosts dropped, hands split. No model needed."""
import os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
import fl_piano_clean as C  # noqa: E402

rng = np.random.default_rng(7)


def perform(score, bpm_curve, jitter=0.012):
    """score: [(beat, len_beats, key, vel)] -> raw notes in seconds played with a tempo curve (bpm per beat)."""
    nb = int(max(b + l for b, l, _, _ in score)) + 2
    bpm = np.interp(np.arange(nb), np.linspace(0, nb - 1, len(bpm_curve)), bpm_curve)
    bt = np.concatenate([[0.5], 0.5 + np.cumsum(60.0 / bpm)])

    def at(b):
        i = int(np.floor(b)); f = b - i
        return bt[i] + f * (bt[i + 1] - bt[i])
    out = []
    for b, l, k, v in score:
        t = at(b) + rng.normal(0, jitter)
        out.append({"t": t, "end": at(b + l) - 0.02, "key": k, "vel": v, "amp": v / 127, "onset": True, "op": 0.9})
    return sorted(out, key=lambda n: n["t"]), bt


def waltz(bars, key_root=43):                 # 3/4 in G major: bass on 1, chord on 2 and 3, melody on top
    prog = [0, 5, 7, 0, 9, 5, 7, 0]           # I IV V I vi IV V I
    sc = []
    for i in range(bars):
        r = key_root + prog[i % len(prog)]
        third = 4 if prog[i % len(prog)] in (0, 5, 7) else 3
        b0 = 3 * i
        sc.append((b0, 2.9, r, 92))
        for bb in (1, 2):
            sc += [(b0 + bb, 0.9, r + 12 + third, 60), (b0 + bb, 0.9, r + 19, 58)]
        mel = r + 24 + [0, 4, 7, 12][i % 4] - (12 if r + 24 > 79 else 0)
        sc += [(b0, 1.0, mel, 80), (b0 + 1.5, 0.5, mel + 2, 70), (b0 + 2, 1.0, mel - 1 if False else mel + 4, 72)]
    return sc


def march(bars, key_root=38):                 # 4/4 in D major
    prog = [0, 7, 9, 5]
    sc = []
    for i in range(bars):
        r = key_root + prog[i % 4]
        b0 = 4 * i
        sc.append((b0, 1.9, r, 95)); sc.append((b0 + 2, 1.9, r + 7, 75))
        for bb in range(4):
            sc.append((b0 + bb, 0.45, r + 24 + [0, 4, 7, 4][bb], 70))
            sc.append((b0 + bb + 0.5, 0.45, r + 24 + [2, 5, 9, 2][bb], 55))
    return sc


def check(score, bpm_curve, want_sig, want_key, **kw):
    raw, bt = perform(score, bpm_curve)
    c = C.clean(raw, **kw)
    rep = c["report"]
    assert rep["meter"]["sig"] == want_sig, (want_sig, rep["meter"])
    if want_key:
        assert rep["key"]["estimated"] == want_key, rep["key"]
    # every note on the right beat (relative to the first note, in quarter notes)
    q = 1.5 if c["meter"]["compound"] else 1.0
    got = sorted((round(b, 3), k) for p in c["parts"].values() for b, _, k, _ in p)
    want = sorted((round(b * q, 3), k) for b, _, k, _ in score)
    off_g, off_w = got[0][0], want[0][0]
    gs = {(round(b - off_g, 3), k) for b, k in got}
    ws = {(round(b - off_w, 3), k) for b, k in want}
    hit = len(gs & ws) / len(ws)
    # bars: the first downbeat note must sit on a bar line
    bar_q = c["beats_per_bar_q"]
    assert abs(got[0][0] % bar_q) < 1e-6 or rep["meter"]["pickup_quarters"] > 0, got[:3]
    # tempo map follows the curve (within 8 %)
    tm = np.array([b for _, b in c["tempo_map"]])
    lo, hi = min(bpm_curve) / q, max(bpm_curve) / q
    assert np.percentile(tm, 10) > lo * 0.92 and np.percentile(tm, 90) < hi * 1.08, (lo, hi, np.percentile(tm, [10, 90]))
    return hit, c


h, c = check(waltz(24), [84, 100, 112, 96, 78], "3/4", "G major")
assert h > 0.95, h
assert c["report"]["tempo"]["range_bpm"][1] - c["report"]["tempo"]["range_bpm"][0] > 15   # it follows, not one bpm
assert min(k for _, _, k, _ in c["parts"]["right"]) > max(k for _, _, k, _ in c["parts"]["left"]) - 12
print("waltz 3/4 rubato:", round(h, 3), c["report"]["tempo"]["range_bpm"], c["report"]["meter"])

h, c = check(march(16), [100, 118, 92], "4/4", "D major")
assert h > 0.95, h
print("march 4/4 rubato:", round(h, 3), c["report"]["tempo"]["range_bpm"])

# 6/8: two dotted-quarter beats per bar, eighths in threes
sc = []
for i in range(16):
    r = 45 + [0, 5, 7, 0][i % 4]
    sc.append((2 * i, 1.9, r, 95))
    for j in range(6):
        sc.append((2 * i + j / 3, 0.3, r + 12 + [0, 7, 12, 16, 12, 7][j], 60 if j % 3 else 78))
h, c = check(sc, [60, 70, 56], "6/8", None)
assert h > 0.9, h
print("6/8:", round(h, 3), c["report"]["grid"]["subdivision"])

# given meter wins over detection
raw, _ = perform(waltz(12), [96])
assert C.clean(raw, meter="3/4")["report"]["meter"]["sig"] == "3/4"

# merge: a held note split by the pedal (no fresh attack) is one note; a re-strike stays two
n1 = [{"t": 1.0, "end": 1.6, "key": 60, "vel": 80, "amp": 0.6, "op": 0.9},
      {"t": 1.65, "end": 2.4, "key": 60, "vel": 60, "amp": 0.5, "op": 0.1},
      {"t": 3.0, "end": 3.5, "key": 60, "vel": 80, "amp": 0.6, "op": 0.9},
      {"t": 3.55, "end": 4.0, "key": 60, "vel": 80, "amp": 0.6, "op": 0.9}]
m, k = C.merge_pieces(n1)
assert k == 1 and len(m) == 3 and abs(m[0]["end"] - 2.4) < 1e-9

# ghosts: a weak octave struck with a strong note goes; a real octave doubling (own attack) stays
g = [{"t": 1.0, "end": 2.0, "key": 48, "vel": 100, "amp": 0.8, "op": 0.9},
     {"t": 1.01, "end": 1.8, "key": 60, "vel": 30, "amp": 0.25, "op": 0.2},      # ghost
     {"t": 3.0, "end": 4.0, "key": 48, "vel": 100, "amp": 0.8, "op": 0.9},
     {"t": 3.0, "end": 4.0, "key": 60, "vel": 90, "amp": 0.7, "op": 0.8},        # doubling
     {"t": 5.0, "end": 5.05, "key": 70, "vel": 20, "amp": 0.15, "op": 0.1}]      # blip
kept, cnt = C.drop_ghosts(g)
assert cnt == {"overtone": 1, "blip": 1, "low": 0} and len(kept) == 3, cnt

# key only settles ties: a near-equal semitone pair keeps the in-key note; a clear winner wins even out of key
key = C._key_from_name("C major")
tie = [{"t": 1.0, "end": 2.0, "key": 64, "vel": 70, "amp": 0.50, "op": 0.8},    # E (in C major)
       {"t": 1.0, "end": 2.0, "key": 63, "vel": 70, "amp": 0.52, "op": 0.8}]    # Eb
kept, rep = C.settle_semitones(tie, key)
assert [n["key"] for n in kept] == [64] and rep["settled_by_key"] == 1
clear = [{"t": 1.0, "end": 2.0, "key": 64, "vel": 40, "amp": 0.25, "op": 0.8},
         {"t": 1.0, "end": 2.0, "key": 63, "vel": 90, "amp": 0.70, "op": 0.8}]   # Eb clearly stronger: kept
kept, rep = C.settle_semitones(clear, key)
assert [n["key"] for n in kept] == [63] and rep["settled_by_evidence"] == 1
# an out-of-key note on its own is never touched
solo = [{"t": 1.0, "end": 2.0, "key": 63, "vel": 70, "amp": 0.5, "op": 0.8}]
assert C.settle_semitones(solo, key)[0] == solo

# key estimate
assert C.estimate_key([{"t": 0, "end": 1, "key": k, "amp": 0.5} for k in (60, 64, 67, 72, 65, 69, 62, 71, 60, 67)])["name"] == "C major"

# hands: a wide chord splits low/high; a single melody line stays one hand
hs = C.split_hands([(0, 1, 36, 80), (0, 1, 43, 70), (0, 1, 64, 70), (0, 1, 72, 80), (1, 1, 74, 80), (2, 1, 76, 80)])
assert sorted(k for *_, k, _ in hs["left"]) == [36, 43] and sorted(k for *_, k, _ in hs["right"]) == [64, 72, 74, 76]

# fix: a weak low note with no attack under a stronger octave / 12th is a ghost; a struck bass note is not
lo = [{"t": 2.0, "end": 4.0, "key": 43, "vel": 90, "amp": 0.7, "op": 0.9, "onset": True},
      {"t": 2.3, "end": 2.6, "key": 31, "vel": 42, "amp": 0.33, "op": 0.15, "onset": False},     # G1 ghost
      {"t": 5.0, "end": 6.0, "key": 31, "vel": 80, "amp": 0.6, "op": 0.8, "onset": True},      # real low G
      {"t": 5.0, "end": 6.0, "key": 43, "vel": 80, "amp": 0.7, "op": 0.8, "onset": True}]
kept, cnt = C.drop_ghosts(lo)
assert cnt["low"] == 1 and [(n["key"], n["t"]) for n in kept if n["key"] == 31] == [(31, 5.0)], cnt

# fix: a key re-struck while still in one hand stays in that hand (no hopping between hands)
seq = [(0, 3, 58, 70), (0, 3, 46, 80), (0, 3, 39, 80),                 # Bb3 over a low Bb/Eb
       (3, 3, 58, 70), (3, 1, 50, 70), (3, 1, 43, 70),                 # Bb3 again, left hand moves up
       (6, 3, 58, 70), (6, 1, 51, 70), (6, 1, 46, 70), (6.5, 0.5, 70, 70)]
hs = C.split_hands(seq)
assert {h for h, ns in hs.items() for b, l, k, v in ns if k == 58} == {"right"} or \
    {h for h, ns in hs.items() for b, l, k, v in ns if k == 58} == {"left"}, hs

# fix: note ends follow how long the note rings in the audio (never shorter)
sr = 22050
t = np.arange(int(sr * 3.0)) / sr
y = np.zeros(int(sr * 4.0), np.float32)
y[int(0.5 * sr):int(0.5 * sr) + len(t)] += (np.exp(-t * 1.2) * sum(np.sin(2 * np.pi * 392.0 * h * t) / h for h in (1, 2, 3)) * 0.3).astype(np.float32)
nn = [{"t": 0.5, "end": 0.7, "key": 67, "vel": 80, "amp": 0.6, "op": 0.9}]       # the model ended it at 0.2 s
assert C.ring_ends(nn, y, sr) == 1 and 2.0 < nn[0]["end"] < 3.6, nn
nn2 = [{"t": 0.5, "end": 3.0, "key": 67, "vel": 80, "amp": 0.6, "op": 0.9}]
C.ring_ends(nn2, y, sr)
assert nn2[0]["end"] >= 3.0                                             # never shortened

# chunk 2 rules -------------------------------------------------------------------------------
def N(t, end, key, vel=80, **k):
    d = {"t": t, "end": end, "key": key, "vel": vel, "amp": vel / 127, "op": 1.0, "onset": True}
    d.update(k)
    return d

# a key let go under the sustain pedal sounds until the pedal comes up; no pedal at the release -> unchanged
ns = [N(1.0, 1.5, 60), N(3.0, 3.4, 64)]
assert C.apply_pedal(ns, [(0.9, 2.6)]) == 1 and ns[0]["end"] == 2.6 and ns[1]["end"] == 3.4
# the same key struck again while it still sounds: the first note ends there
ns = [N(1.0, 3.0, 60), N(2.0, 2.5, 60), N(2.2, 4.0, 64)]
assert C.cut_restrikes(ns) == 1 and abs(ns[0]["end"] - 1.998) < 1e-9
# bass: lowest note of a chord when low or well below the rest; not while a lower key is still held
ns = [N(0.0, 3.0, 39), N(0.0, 3.0, 46), N(0.0, 3.0, 55),        # Eb2 bass under a chord
      N(1.0, 2.0, 58), N(1.0, 2.0, 70),                          # Bb3 under a melody note while Eb2 held: no
      N(4.0, 6.0, 43), N(4.0, 6.0, 50),                          # G2: new bass
      N(5.0, 5.5, 72)]                                           # a lone high note: no
assert C.mark_bass(ns) == 2 and [n["key"] for n in ns if n["bass"]] == [39, 43]
# a bass note held by the pedal stops when the next bass is struck
ns[0]["end"] = 5.0
assert C.cut_bass(ns) == 1 and ns[0]["end"] < 4.0
# Transkun notes (velocity, no attack probability): a much quieter octave struck with a note is a ghost
tk = [N(1.0, 2.0, 48, 90, src="transkun"), N(1.01, 1.9, 60, 40, src="transkun"),
      N(3.0, 4.0, 48, 90, src="transkun"), N(3.0, 4.0, 60, 85, src="transkun")]
kept, cnt = C.drop_ghosts(tk)
assert cnt["overtone"] == 1 and len(kept) == 3
# whole pass: three parts (bass / left / right), the pedal and bass rules are reported
song = []
for i in range(8):                                              # 3/4: bass on 1, chord 2-3, melody
    b0 = i * 1.8
    song += [N(b0, b0 + 0.5, 36 + [0, 5, 7, 0][i % 4], 90),
             N(b0 + 0.6, b0 + 1.0, 52, 60), N(b0 + 0.6, b0 + 1.0, 55, 60),
             N(b0 + 1.2, b0 + 1.6, 52, 60), N(b0 + 1.2, b0 + 1.6, 55, 60),
             N(b0, b0 + 1.7, 72 + [0, 2, 4, 2][i % 4], 85)]
c = C.clean(song, pedal={"sustain": [(i * 1.8 - 0.05, i * 1.8 + 1.75) for i in range(8)]}, meter="3/4")
assert set(c["parts"]) == {"bass", "left", "right"}, set(c["parts"])
r = c["report"]["notes"]
assert r["held_by_pedal"] > 0 and r["bass_notes"] == 8 and all(k < 48 for _, _, k, _ in c["parts"]["bass"]), r
assert C.clean(song, meter="3/4", bass=False)["parts"].keys() == {"left", "right"}

# downbeat repair: a missing beat is inserted so the bar lines stay on the bass entries
F = np.zeros(30); F[[0, 3, 6, 8, 11, 14, 17]] = 3.0        # beat 8 should be 9: the tracker missed one
pos, ops = C.downbeat_align(F, 3)
assert ops and ops[0][1] == "insert", ops
print("ok")
