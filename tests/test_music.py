"""Unit tests for fl_music (theory + note operations). Run: python tests/test_music.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import fl_music as M  # noqa: E402

P = 96
def n(pos, key, length=P, vel=100, ch=0):
    return {"pos": pos, "len": length, "key": key, "vel": vel, "ch": ch}

scale = [n(i * P, k) for i, k in enumerate([60, 62, 64, 65, 67, 69, 71, 72, 67, 64, 60])]
assert M.detect_key(scale)[0][0] == "C major", M.detect_key(scale)
minor = [n(i * P, k) for i, k in enumerate([57, 59, 60, 62, 64, 65, 67, 69, 64, 60, 57])]
assert M.detect_key(minor)[0][0] == "A minor", M.detect_key(minor)
w = [0.0] * 12
for pc in (7, 10, 2):
    w[pc] = 1
assert M.name_chord(w) == "Gm"
assert M.parse_key("F# dorian")[:2] == (6, "dorian") and M.parse_note("Bb3") == 58 and M.note_name(61) == "C#4"

q = M.apply([n(5, 60), n(50, 62)], {"op": "quantize", "grid": "1/16"}, P)
assert [x["pos"] for x in q] == [0, 48]
assert [x["key"] for x in M.apply([n(0, 61)], {"op": "fix_scale", "key": "C major"}, P)] == [60]
h = M.apply([n(0, 60)], {"op": "harmonize", "key": "C major", "steps": 2}, P)
assert sorted(x["key"] for x in h) == [60, 64]
chord = [n(0, 60, 4 * P), n(0, 64, 4 * P), n(0, 67, 4 * P)]
arp = M.apply(chord, {"op": "arpeggiate", "rate": "1/16", "mode": "up"}, P)
assert [x["key"] for x in arp[:4]] == [60, 64, 67, 60] and len(arp) == 16
st = M.apply(chord, {"op": "strum", "ms": 50, "direction": "down"}, P, 120)
assert sorted(x["pos"] for x in st) == [0, 10, 20]   # 50 ms at 120 bpm = 9.6 ticks
inv = M.apply(chord, {"op": "invert", "times": 1}, P)
assert sorted(x["key"] for x in inv) == [64, 67, 72]
lg = M.apply([n(0, 60, 10), n(P, 62, 10)], {"op": "legato"}, P)
assert lg[0]["len"] == P
sw = M.apply([n(48, 60)], {"op": "swing", "grid": "1/8", "amount": 0.75}, P)
assert sw[0]["pos"] == 72
d = M.drum_notes("house", 4, P, {"kick": 0, "clap": 1, "hat": 2})
assert sum(1 for x in d if x["ch"] == 0 and x["pos"] < 4 * P) == 4   # four on the floor in bar 1
# tighten: a flammed chord lands together, a 16th run next to it is left alone, ends stay put
ch = [dict(pos=96 + 1, len=200, key=48, vel=80, ch=0), dict(pos=96 + 9, len=192, key=64, vel=80, ch=2),
      dict(pos=96 + 14, len=150, key=64, vel=60, ch=4), dict(pos=96 + 24, len=20, key=67, vel=80, ch=2),
      dict(pos=96 + 48, len=20, key=69, vel=80, ch=2)]
t = M.apply(ch, {"op": "tighten", "strength": 0.8}, 96)
t = sorted(t, key=lambda x: (x["pos"], x["ch"]))
assert max(x["pos"] for x in t[:3]) - min(x["pos"] for x in t[:3]) <= 3, t
assert [x["pos"] for x in t[3:]] == [120, 144], t
assert all(x["pos"] + x["len"] == y["pos"] + y["len"] for x, y in zip(sorted(t, key=lambda x: x["key"] * 10 + x["ch"]),
                                                                        sorted(ch, key=lambda x: x["key"] * 10 + x["ch"]))
           if x["pos"] < 120)
# tighten without a grid (rubato in the notes): the group meets at its earliest note, not a grid line
fr = [dict(pos=101, len=200, key=48, vel=80, ch=0), dict(pos=105, len=150, key=64, vel=60, ch=4)]
t2 = sorted(M.apply(fr, {"op": "tighten", "grid": "none", "strength": 1.0, "window": 8}, 96), key=lambda x: x["ch"])
assert [x["pos"] for x in t2] == [101, 101], t2
print("music ok")
