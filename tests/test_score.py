"""flh_score: score view (key, chords, roman numerals, parts, repeats, size) and the clash detector (rubs, melody vs
chord, bass vs chord, mud, ranges, crossing, jazz tolerance), entrances; on note lists, a MIDI file and .flp files
written with the server's own writers."""
import json, os, re, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702  (leave no temp files behind)
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_score as SC  # noqa: E402
ns = vars(m)
flh_store.register(ns); SC.register(ns)
assert "flp_score" in m.TOOLS and "flp_clashes" in m.TOOLS and callable(ns["score_view"]) and callable(ns["clashes"])
P = 96; BAR = 4 * P
N = lambda k: SC.FM.parse_note(k)


def prog(bars=8, horn=None, extra=()):
    """C Am F G loop: bass roots, pad triads, a chord-tone melody in quarters. horn: (bar, note) held a whole bar."""
    chords = [("C2", ["C4", "E4", "G4"], ["E5", "G5", "E5", "C5"]), ("A1", ["A3", "C4", "E4"], ["A4", "C5", "E5", "C5"]),
              ("F1", ["F3", "A3", "C4"], ["F5", "A5", "F5", "C5"]), ("G1", ["G3", "B3", "D4"], ["G5", "D5", "B4", "D5"])]
    out = []
    for b in range(bars):
        bass, pad, mel = chords[b % 4]; t = b * BAR
        out.append((t, BAR, 0, N(bass), 90))
        out += [(t, BAR, 1, N(k), 80) for k in pad]
        out += [(t + i * P, P, 2, N(k), 100) for i, k in enumerate(mel)]
    if horn:
        out.append(((horn[0] - 1) * BAR, BAR, 3, N(horn[1]), 95))
    return out + list(extra)


PARTS = {0: {"name": "Bass"}, 1: {"name": "Pad"}, 2: {"name": "Lead"}, 3: {"name": "Horn"}}
kinds = lambda r: [(i["severity"], i["kind"]) for i in r["issues"]]

# ---------------------------------------------------------------- pure theory helpers
assert SC.roman((7, "7", frozenset({7, 11, 2, 5})), "C major") == "V7"
assert SC.roman((9, "m", frozenset({9, 0, 4})), "C major") == "vi"
assert SC.roman((0, "", frozenset({0, 4, 7})), "C major", bass=4) == "I6"
assert SC.roman((7, "7", frozenset({7, 11, 2, 5})), "C major", bass=11) == "V65"
assert SC.roman((8, "", frozenset({8, 0, 3})), "C minor") == "VI" and SC.roman((11, "dim", frozenset()), "C minor") == "#vii°"
w = [0.0] * 12
for pc in (2, 5, 9, 0, 4):
    w[pc] = 1
assert SC.best_chord(w, 2, 0.0)[1] == "m9" and SC.best_chord(w, 2)[1] in ("m7", "m9"), SC.best_chord(w, 2)
assert SC._compress(["I", "vi", "IV", "V"] * 2) == "[I vi IV V]x2" and SC._compress(["i", "i", "i"]) == "i*3"
ins = SC.instrument_for(["Timpani"]); assert ins["id"] == "timpani" and ins["_range"] == (36, 60) and ins["uncertain"]
assert SC.instrument_for(["Horns a4"])["id"] == "french horn" and SC.instrument_for(["Bass Clarinet"])["id"] == "bass clarinet"
assert SC.instrument_for(["Celli Long"])["id"] == "cello" and SC.instrument_for(["Kick 808"])["id"] == "drums"
assert SC.instrument_for(["Tuned Percussion"]) is None or SC.instrument_for(["Tuned Percussion"])["id"] != "drums"
assert SC.instrument_for(["English Horn"])["id"] == "english horn" and SC.instrument_for(["Synth Bass 1"])["id"] == "synth bass"

# ---------------------------------------------------------------- score view of a known progression
mod = SC.build_model(prog(8), P, parts=PARTS)
sv = SC.score_view(mod)
txt = sv["score"]
print(txt)
assert "key C major" in txt and "[I vi IV V]x2" in txt, txt
assert "I=C" in txt and "vi=Am" in txt and "IV=F" in txt and "V=G" in txt
roles = {ch: p["role"] for ch, p in mod["parts"].items()}
assert roles == {0: "bass", 1: "pad", 2: "melody"}, roles
assert "5-8=1-4" in txt, txt                                     # repeats
js = SC.score_view(mod, format="json")
assert js["key"][0][0] == "C major" and js["progression"]["1-8"] == ["I", "vi", "IV", "V"] * 2, js["progression"]
assert js["chords"][:2] == [{"bar": 1, "chord": "C", "roman": "I"}, {"bar": 2, "chord": "Am", "roman": "vi"}], js["chords"]
# the ns API other modules use: structured by default, and it takes plain notes
api = ns["score_view"](notes=[{"pos": n[0], "len": n[1], "ch": n[2], "key": n[3], "vel": n[4]} for n in prog(8)], ppq=P,
                       bpm=100)
assert api["key"][0][0] == "C major" and api["tempo"] == 100 and api["chords"][3]["chord"] == "G", api["chords"][:4]
assert ns["clashes"](notes=[{"pos": n[0], "len": n[1], "ch": n[2], "key": n[3], "vel": n[4]} for n in prog(8, horn=(1, "F#4"))],
                     ppq=P)["issues"][0]["kind"] == "rub"
assert "score" in ns["score_view"](project=None, notes=[{"pos": 0, "len": P, "key": 60}], format="text")
assert js["parts"][2]["role"] == "melody" and js["melody"]["range"] == "A4-A5", js["melody"]
# detail views: section / bars, part, piano roll
d1 = SC.score_view(mod, bars="1-2", part=2, piano_roll=True)["score"]
assert "1:I 2:vi" in d1 and "PART 2 Lead" in d1 and "ROLL ch2 bars 1-2" in d1 and "E5  |#===" in d1, d1
# half-bar chords: a chord change in the middle of a bar shows as "a,b"
half = [(0, 2 * P, 1, N(k), 80) for k in ("C4", "E4", "G4")] + [(2 * P, 2 * P, 1, N(k), 80) for k in ("G3", "B3", "D4")] \
    + [(BAR, BAR, 1, N(k), 80) for k in ("C4", "E4", "G4")] + [(0, 2 * P, 0, N("C2"), 90), (2 * P, 2 * P, 0, N("G1"), 90),
                                                              (BAR, BAR, 0, N("C2"), 90)]
hs = SC.score_view(SC.build_model(half, P), format="json")
assert hs["progression"]["1-2"] == ["I,V", "I"], hs["progression"]

# size bound: a 3-minute song (96 bars at 128 bpm), 7 parts, 8 named sections -> small default output
big = []
for b in range(96):
    t = b * BAR; root = [36, 33, 29, 31][b % 4]
    big += [(t + i * P, P // 2, 6, 36, 100) for i in range(4)] + [(t + i * P + P // 2, P // 4, 6, 42, 70) for i in range(4)]
    big += [(t, BAR, 0, root, 90)] + [(t, BAR, 1, root + 24 + d, 70) for d in (0, 4, 7)]
    big += [(t + i * P // 2, P // 2, 2, root + 48 + (i * 2) % 12, 95) for i in range(8)]
    big += [(t + i * P, P, 3, root + 36 + (4 if i % 2 else 7), 80) for i in range(4)]
    big += [(t + 2 * P, 2 * P, 4, root + 31, 75)]
    big += [(t + i * P // 4, P // 4, 5, root + 60 + (i % 4) * 3, 60) for i in range(16)]
secs = [{"name": n, "bar": 1 + 12 * i} for i, n in enumerate(["Intro", "Verse", "Pre", "Chorus", "Verse 2", "Chorus 2",
                                                               "Bridge", "Outro"])]
bm = SC.build_model(big, P, parts={6: {"name": "Kick"}, 0: {"name": "Bass"}, 1: {"name": "Pad"}, 2: {"name": "Lead"},
                                   3: {"name": "Keys"}, 4: {"name": "Strings"}, 5: {"name": "Arp"}}, sections=secs, tempo=128)
t0 = time.time(); bv = SC.score_view(bm); took = time.time() - t0
print(f"3-min song view: {len(bv['score'])} chars, {took:.3f}s")
assert len(bv["score"]) < 4500 and took < 0.5, (len(bv["score"]), took)
assert bm["parts"][6]["role"] == "drums" and "Chorus 2 61-72: = Intro" in bv["score"], bv["score"]

# ---------------------------------------------------------------- clash detector
clean = SC.clashes(SC.build_model(prog(8), P, parts=PARTS))
print("clean:", json.dumps(kinds(clean)), clean.get("info"))
assert clean["issues"] == [] and clean["verdict"].startswith("no clashes"), clean["issues"]

horn = SC.clashes(SC.build_model(prog(8, horn=(1, "F#4")), P, parts=PARTS))
print("horn:", json.dumps(horn["issues"][:2], indent=0))
top = horn["issues"][0]
assert top["kind"] == "rub" and top["severity"] == "high" and "Horn(ch3)" in top["parts"], top
assert "F#4 vs" in top["notes"] and "G4" in top["notes"] and "m2" in top["notes"], top
assert "F#4 -> G4" in top["fix"] and "flp_edit_notes" in top["fix"] and top["where"] == "bar 1:1", top
assert len([i for i in horn["issues"] if i["severity"] in ("high", "med")]) == 1, horn["issues"]

# a passing tone on a weak 8th is fine; the same note held on a strong beat in the melody is flagged
passing = prog(8, extra=[(BAR * 4 + P + P // 2, P // 2, 3, N("F#4"), 90)])
assert SC.clashes(SC.build_model(passing, P, parts=PARTS))["issues"] == []
mel_bad = [n for n in prog(8) if not (n[2] == 2 and n[0] == 0)] + [(0, 2 * P, 2, N("F5"), 100)]
mb = SC.clashes(SC.build_model(mel_bad, P, parts=PARTS))
assert any(i["kind"] in ("melody_nct", "rub") and "F5" in i["notes"] for i in mb["issues"]), mb["issues"]
# appoggiatura that resolves by step (D5 -> C5 over C) is fine
appo = [n for n in prog(8) if not (n[2] == 2 and n[0] < 2 * P)] + [(0, P, 2, N("D5"), 100), (P, P, 2, N("C5"), 100)]
assert not [i for i in SC.clashes(SC.build_model(appo, P, parts=PARTS))["issues"] if i["kind"] == "melody_nct"]

# bass against the chord: Eb under a C major triad
bb = [n for n in prog(8) if not (n[2] == 0 and n[0] == 0)] + [(0, BAR, 0, N("Eb2"), 90)]
bbr = SC.clashes(SC.build_model(bb, P, parts=PARTS))
assert any(i["kind"] == "bass_root" and "Eb" in i["notes"] for i in bbr["issues"]), bbr["issues"]
# inversion (E under C) is fine
inv = [n for n in prog(8) if not (n[2] == 0 and n[0] == 0)] + [(0, BAR, 0, N("E2"), 90)]
assert not [i for i in SC.clashes(SC.build_model(inv, P, parts=PARTS))["issues"] if i["kind"] == "bass_root"]

# muddy low voicing: a pad playing C2-E2-G2 (3rds far below C3)
mud = prog(8) + [(0, BAR, 4, N(k), 90) for k in ("C2", "E2", "G2")]
md = SC.clashes(SC.build_model(mud, P, parts={**PARTS, 4: {"name": "Low Pad"}}))
assert any(i["kind"] == "mud" for i in md["issues"]), md["issues"]

# support layer above the melody and louder
cross = prog(8) + [(b * BAR, BAR, 4, N("C6"), 120) for b in range(4)]
cr = SC.clashes(SC.build_model(cross, P, parts={**PARTS, 4: {"name": "Strings", "role": "pad"}}))
assert any(i["kind"] == "crossing" and i["severity"] == "med" for i in cr["issues"]), cr["issues"]

# jazz ii-V-I with 9ths / 13ths: nothing under jazz tolerance, the strict reading flags the close 7ths
jazz = []
for rep_ in range(2):
    o = rep_ * 3 * BAR
    for b, (bass, pad, mel) in enumerate([("D2", ["F3", "A3", "C4", "E4"], [("E5", 2), ("F5", 2)]),
                                          ("G2", ["F3", "B3", "E4"], [("E5", 2), ("D5", 2)]),
                                          ("C2", ["E3", "B3", "D4", "G4"], [("D5", 4)])]):
        t = o + b * BAR
        jazz += [(t, BAR, 0, N(bass), 85)] + [(t, BAR, 1, N(k), 75) for k in pad]
        at = 0
        for k, beats in mel:
            jazz.append((t + at * P, beats * P, 2, N(k), 95)); at += beats
jm = SC.build_model(jazz, P, parts=PARTS, genre="jazz")
jr = SC.clashes(jm)
print("jazz:", jr["tolerance"], json.dumps(kinds(jr)))
assert jr["tolerance"] == "jazz" and jr["issues"] == [], jr["issues"]
js_ = SC.clashes(jm, tolerance="strict")
print("jazz strict:", json.dumps(kinds(js_)))
assert js_["tolerance"] == "strict" and js_["issues"], js_
assert "ii9" in SC.score_view(jm)["score"] and "V13" in SC.score_view(jm)["score"]

# ranges: a timpani note at E1 -> out of range (med), fix up an octave
timp = prog(8) + [(0, P, 5, N("E1"), 100), (BAR, P, 5, N("D2"), 100), (2 * BAR, P, 5, N("A2"), 100)]
tr = SC.clashes(SC.build_model(timp, P, parts={**PARTS, 5: {"name": "Timpani"}}))
oor = [i for i in tr["issues"] if i["kind"] == "out_of_range"]
print("timpani:", json.dumps(oor))
assert oor and oor[0]["severity"] == "med" and "E1" in oor[0]["notes"] and "E1 -> E2" in oor[0]["fix"], oor
assert "check the library" in oor[0]["why"]
# crowded register: 5 parts all around C4
crowd = prog(8, extra=[(b * BAR, BAR, c, N(k), 70) for b in range(8) for c, k in ((5, "E4"), (6, "G3"), (7, "C4"))])
cw = SC.clashes(SC.build_model(crowd, P, parts={**PARTS, 5: {"name": "Keys"}, 6: {"name": "Organ"}, 7: {"name": "Guitar"}}),
                kinds=["crowd"])
assert any(i["kind"] == "crowd" for i in cw["issues"]), cw["issues"]
# bass doubling the melody in octaves -> info only
octs = [(i * P, P, 2, 72 + i, 100) for i in range(8)] + [(i * P, P, 0, 36 + i, 90) for i in range(8)] + \
    [(0, 8 * P, 1, k, 60) for k in (60, 64, 67)]
oc = SC.clashes(SC.build_model(octs, P, parts=PARTS), full=True)
assert any(i["severity"] == "info" and i["kind"] == "octaves" for i in oc["issues"]), oc["issues"]
assert all(i["severity"] == "info" for i in oc["issues"] if i["kind"] == "octaves")

# ---------------------------------------------------------------- entrances (abrupt layers)
ent = prog(8) + [(4 * BAR, 2 * P, 4, N(k), 122) for k in ("C4", "E4")] + \
    [(4 * BAR + i * P, P, 4, N("E4"), 70) for i in range(2, 16)]
e1 = SC.entrances(SC.build_model(ent, P, parts={**PARTS, 4: {"name": "Strings"}}))
print("entrances:", json.dumps(e1)[:400])
assert e1 and e1[0]["ch"] == 4 and e1[0]["bar"] == 5 and e1[0]["first_vel"] == 122 and "velocity" in e1[0]["fix"], e1
ramp = prog(8) + [(4 * BAR + i * P, P, 4, N("E4"), 40 + 6 * i) for i in range(16)]
assert SC.entrances(SC.build_model(ramp, P, parts={**PARTS, 4: {"name": "Strings"}})) == []

# ---------------------------------------------------------------- genre bass idioms (groove / jazz) vs strict
# root on 1, the (unvoiced) 5th on 3, a chromatic approach note on 4 into the next root; keys voice 7th chords
# without their 5th. Idiomatic in house / funk / afrobeats...: nothing to flag there; strict still flags the approaches.
grv = []
for rep_ in range(2):
    for b, (pad, bass) in enumerate([(["C4", "E4", "B4"], ["C2", "G2", "G#1"]), (["A3", "C4", "G4"], ["A1", "E2", "F#1"]),
                                     (["F3", "A3", "E4"], ["F1", "C2", "F#1"]), (["G3", "B3", "F4"], ["G1", "D2", "C#2"])]):
        t = (rep_ * 4 + b) * BAR
        grv += [(t, BAR, 1, N(k), 72) for k in pad]
        grv += [(t, 2 * P, 0, N(bass[0]), 96), (t + 2 * P, P, 0, N(bass[1]), 90), (t + 3 * P, P, 0, N(bass[2]), 88)]
grv += [(8 * BAR, BAR, 1, N(k), 72) for k in ("C4", "E4", "B4")] + [(8 * BAR, BAR, 0, N("C2"), 96)]
GP = {0: {"name": "Bass"}, 1: {"name": "Keys", "role": "chords"}}
gh = SC.clashes(SC.build_model(grv, P, parts=GP, genre="deep house"))
print("groove:", gh["tolerance"], json.dumps(kinds(gh)))
assert gh["tolerance"] == "groove" and not [i for i in gh["issues"] if i["severity"] in ("high", "med")], gh["issues"]
assert not [i for i in gh["issues"] if i["kind"] == "bass_root"], gh["issues"]
gs = SC.clashes(SC.build_model(grv, P, parts=GP), tolerance="strict")
print("groove strict:", json.dumps(kinds(gs)))
assert any(i["kind"] in ("bass_root", "rub") and "Bass" in i["parts"] for i in gs["issues"]), gs["issues"]
assert SC.tolerance_for({"genre": "afrobeats"})[0] == "groove" and SC.tolerance_for({"genre": "lo-fi"})[0] == "jazz"
assert SC.tolerance_for({"genre": "baroque chorale"})[0] == "strict" and SC.tolerance_for({"genre": "polka"})[0] == "normal"
# walking bass passing tones (jazz): a note per beat, steps between chord tones
walk = []
for rep_ in range(2):
    for b, (pad, line) in enumerate([(["F3", "A3", "C4", "E4"], ["D2", "E2", "F2", "F#2"]), (["F3", "B3", "E4"], ["G2", "A2", "B2", "B1"]),
                                     (["E3", "B3", "D4", "G4"], ["C2", "D2", "E2", "C#2"])]):
        t = (rep_ * 3 + b) * BAR
        walk += [(t, BAR, 1, N(k), 70) for k in pad] + [(t + i * P, P - 8, 0, N(k), 90) for i, k in enumerate(line)]
wk = SC.clashes(SC.build_model(walk, P, parts=GP, genre="jazz"))
assert not [i for i in wk["issues"] if i["severity"] in ("high", "med")], wk["issues"]
# a consonant slash chord (D under F#-A-E: Dmaj9 without its 3rd... it has the 3rd F#): not a wrong bass outside strict
sl = [(b * BAR, BAR, 1, N(k), 70) for b in range(4) for k in ("F#3", "A3", "E4")] + [(b * BAR, BAR, 0, N("D2"), 90) for b in range(4)]
assert not [i for i in SC.clashes(SC.build_model(sl, P, parts=GP, genre="pop"))["issues"] if i["kind"] == "bass_root"]
assert SC.is_chord_set({5, 9, 0, 3, 7}) and SC.is_chord_set({0, 4, 7, 11}) and not SC.is_chord_set({0, 4, 5, 7})
# FL slide notes bend the held note (they don't sound on their own): an 808 C2 sliding up a semitone is one moving
# note, not a C2+C#2 cluster
sld = [{"pos": b * BAR, "len": BAR, "ch": 0, "key": N("C2"), "vel": 110} for b in range(4)] + \
      [{"pos": b * BAR + 3 * P, "len": P // 2, "ch": 0, "key": N("C#2"), "vel": 110, "slide": True} for b in range(4)] + \
      [{"pos": b * BAR, "len": BAR, "ch": 1, "key": N(k), "vel": 70} for b in range(4) for k in ("C4", "Eb4", "G4")]
smod = SC.build_model(sld, P, parts={0: {"name": "808", "role": "bass"}, 1: {"name": "Keys"}}, genre="trap")
b0 = [n for n in smod["notes"] if n[2] == 0]
assert len(b0) == 8 and all(a[0] + a[1] <= b[0] for a, b in zip(b0, b0[1:])), b0          # split, no overlap
assert [n[3] for n in b0[:2]] == [N("C2"), N("C#2")] and b0[1][1] == P, b0[:2]             # bent for the rest of the bar
assert not [i for i in SC.clashes(smod)["issues"] if i["kind"] == "mud"]

# ---------------------------------------------------------------- speed + identical results on a big song
# The indexed clash detector must give exactly what checking every pair gives (reference: a plain all-pairs loop over
# the same rules), on dense and random material, and stay fast on big songs.
import random  # noqa: E402
rnd = random.Random(5)
dense = []
for b in range(64):
    t = b * BAR
    for ch in range(12):
        root = [36, 33, 29, 31][b % 4] + 12 * (ch % 4)
        dense += [(t + i * P, P * rnd.choice((1, 2)), ch, root + rnd.choice((0, 1, 4, 6, 7, 11, 13, 14)), rnd.randint(60, 110))
                  for i in range(0, 4, rnd.choice((1, 2)))]
secs_d = [{"name": f"S{i}", "bar": 1 + 4 * i} for i in range(16)]
md = SC.build_model(dense, P, sections=secs_d)


t0 = time.time(); fast = SC.find_clashes(md); took = time.time() - t0
# reference: the same detector with the old full active-list scan, rebuilt from the source with the index removed
import inspect, textwrap  # noqa: E402
code = inspect.getsource(SC._find_clashes)
a_ = code.index("    dists = sorted("); b_ = code.index("        for _, x in cand:\n")
old_loop = """    active = []
    for n in ns:
        active = [x for x in active if x[POS] + x[LEN] > n[POS]]
        k = n[KEY]
        for x in list(active):
"""
code = code[:a_] + old_loop + code[b_ + len("        for _, x in cand:\n"):]
code = code.replace("""        by_key.setdefault(k, {})[idx] = n
        heapq.heappush(heap, (n[POS] + n[LEN], idx, k))
        idx += 1
""", """        active.append(n)
""").replace("def _find_clashes(", "def _find_clashes_ref(")
g_ = dict(vars(SC)); exec(code, g_)
SC._CACHE.clear(); md2 = SC.build_model(dense, P, sections=secs_d)
t1 = time.time(); ref = g_["_find_clashes_ref"](md2); took_ref = time.time() - t1
assert json.dumps(fast, sort_keys=True, default=str) == json.dumps(ref, sort_keys=True, default=str), "indexed != all-pairs"
print(f"clashes dense: {len(fast)} issues, indexed {took:.3f}s vs all-pairs {took_ref:.3f}s")
assert SC.find_clashes(md) == fast and SC.find_clashes(md) is not SC.find_clashes(md)      # cached, copies handed out
# FL slide notes: the bisect version equals the plain one
for trial in range(200):
    r_ = random.Random(trial)
    nts = [(r_.randint(0, 40) * 12, r_.randint(1, 8) * 12, r_.randint(0, 2), r_.randint(30, 60), 100, None, 0)
           for _ in range(r_.randint(0, 40))]
    sls = [(r_.randint(0, 40) * 12, r_.randint(1, 4) * 12, r_.randint(0, 2), r_.randint(30, 60), 90, None, 0)
           for _ in range(r_.randint(1, 12))]
    assert SC.apply_slides(nts, sls) == SC._apply_slides_ref(nts, sls), trial
# FZ-06: a note millions of bars out (corrupt clip) is left out with a warning; nothing hangs
far = prog(8) + [(2 ** 31, P, 2, 72, 100), (0, 2 ** 31, 1, 40, 80)]
t0 = time.time()
fv_ = SC.score_view(SC.build_model(far, P, parts=PARTS))
fc_ = SC.clashes(SC.build_model(far, P, parts=PARTS))
fd_ = SC.doctor_check(SC.build_model(far, P, parts=PARTS))
assert time.time() - t0 < 3, time.time() - t0
assert "| 8 bars" in fv_["score"], fv_["score"][:200]                       # the song is its 8 real bars
assert "WARNING 1 note(s) from bar 5,592,406 on were left out" in fv_["score"] and "cut at the song's end" in fc_["warning"], fc_
assert fd_["clashes"][0]["kind"] == "far_notes" and fd_["clashes"][0]["severity"] == "high", fd_
# big songs: the default view groups parts by role and stays small; part lists per section become counts
many = []
for b in range(32):
    for ch in range(40):
        many.append((b * BAR, BAR, ch, 48 + (ch * 7) % 24, 80))
mv = SC.score_view(SC.build_model(many, P))["score"]
assert "grouped by role" in mv and len(mv) < 4000 and "parts:40 of 40 (all)" in mv, (len(mv), mv[:600])
assert "grouped" not in SC.score_view(SC.build_model(many, P), full=True)["score"]
# sounding pitch: a pitched sampler written +30 (F#1 808 on FL's C5 root) is analysed where it sounds
jr_ = {"channels": {"0": {"transpose": 30}}, "compose": {"transpose": {"0": 30}}}
assert SC.sounding_offsets(jr_) == {0: 30} and SC.sounding_offsets({"channels": {"1": {"transpose": 0}}}) == {}
mo = SC.build_model([(n[0], n[1], n[2], n[3] + (30 if n[2] == 0 else 0), n[4]) for n in prog(8)], P, parts=PARTS)
mo["journal"] = jr_; mo["offsets_apply"] = True; mo["notes"] = sorted(mo["notes"]); SC._finish(mo)
base_ = SC.build_model(prog(8), P, parts=PARTS)
assert [n[:5] for n in mo["notes"]] == [n[:5] for n in base_["notes"]] and mo["offsets"] == {0: 30}
assert SC.score_view(mo, format="json")["chords"] == SC.score_view(base_, format="json")["chords"]
assert kinds(SC.clashes(mo)) == kinds(SC.clashes(base_))
_h = SC._edit_hint(mo, 0, [n for n in mo["notes"] if n[2] == 0][:1], [])
assert re.search(r'low"?[=:] ?' + str(N("C2") + 30) + r"\b", _h), _h
# plain words (fl_access): '[i VI]x2' -> 'i, VI (all twice)', the roll as words, colour names
assert SC._compress(["i", "VI", "i", "VI"]) == "[i VI]x2" and SC._compress(["i", "VI", "i", "VI"], True) == "i, VI (all twice)"
assert SC._compress(["i", "i", "i"], True) == "i three times"
assert SC.grid_words("X...x...o..g....") == "step 1 accent, step 5 hit, step 9 soft, step 12 ghost"
assert SC.color_name("#5A2A2A") == "dark red" and SC.color_name("#303030") == "dark grey" and "blue" in SC.color_name("#2A3A6A")
ns["access_prefs"] = lambda: {"vision": "blind"}
pw = SC.score_view(SC.build_model(prog(8), P, parts=PARTS), piano_roll=True)["score"]
assert "KEY TO THE SHORT FORMS" in pw and "NOTES of channel" in pw and "on beat 1 for 1" in pw and "#" not in pw.split("NOTES")[1], pw
ns.pop("access_prefs")
assert "ROLL ch" in SC.score_view(SC.build_model(prog(8), P, parts=PARTS), piano_roll=True)["score"]

# ---------------------------------------------------------------- MIDI file input (one part per track)
mid = os.path.join(tmp, "prog.mid")
tracks = [("tempo", [(0, b"\xff\x51\x03" + (500000).to_bytes(3, "big"))])]
for ch, name in ((0, "Bass"), (1, "Pad"), (2, "Lead")):
    evs = []
    for pos, ln, c, k, v in prog(8):
        if c == ch:
            evs += [(pos, bytes([0x90 | ch, k, v])), (pos + ln, bytes([0x80 | ch, k, 0]))]
    tracks.append((name, evs))
m.midi_write(mid, P, tracks)
mv = SC.score_view(mid, format="json")
assert mv["key"][0][0] == "C major" and [p["name"] for p in mv["parts"]] == ["Bass", "Pad", "Lead"], mv["parts"]
assert mv["tempo"] == 120 and mv["progression"]["1-8"][:4] == ["I", "vi", "IV", "V"], mv

# ---------------------------------------------------------------- .flp written by the server (fixture template)
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
if os.path.isfile(FIX):
    t0 = time.time(); fv = SC.score_view(FIX); took = time.time() - t0
    print(f"fixture view {len(fv['score'])} chars {took:.3f}s; clashes", json.dumps(kinds(SC.clashes(FIX))))
    assert took < 0.5 and len(fv["score"]) < 4500, (took, len(fv["score"]))
    SC._CACHE.clear()

    def write(name, notes, chan_map):
        parts = []
        for ch, fl_ch in chan_map.items():
            p = os.path.join(tmp, f"{name}_{ch}.mid")
            evs = []
            for pos, ln, c, k, v in notes:
                if c == ch:
                    evs += [(pos, bytes([0x90, k, v])), (pos + ln, bytes([0x80, k, 0]))]
            m.midi_write(p, P, [("t", evs)]); parts.append({"channel": fl_ch, "midi": p})
        out = os.path.join(tmp, name + ".flp")
        m.flp_write_notes(FIX, out, parts, song_length=None, tempo=120)
        return out
    fl = write("horn", prog(8, horn=(1, "F#4")), {0: 0, 1: 1, 2: 2, 3: 4})
    flh_store.journal_update(fl, set={"channels": {"0": {"name": "Bass"}, "1": {"name": "Pad"}, "2": {"name": "Lead"},
                                                   "4": {"name": "Horn", "instrument": "french horn"}}})
    fc = m.TOOLS["flp_clashes"][0](fl)
    top = fc["issues"][0]
    print("flp horn:", json.dumps(top))
    assert top["kind"] == "rub" and "Horn(ch4)" in top["parts"] and "F#4 -> G4" in top["fix"], top
    _a = {k: int(v) for k, v in re.findall(r'"?(\w+)"?\s*[=:]\s*(-?\d+)', top["fix"])}
    assert [_a.get(k) for k in ("pattern", "channel", "start_bar", "end_bar", "low", "high")] == [1, 4, 1, 2, 66, 66], top["fix"]
    # flp_edit_notes isn't a listed tool: the hint is a flh_call a model can make as written
    assert 'flh_call("flp_edit_notes", {"path": project' in top["fix"], top["fix"]
    _hj = json.loads(top["fix"][top["fix"].index("{"):top["fix"].rindex(")")].replace('"path": project', '"path": null'))
    assert _hj["ops"] == [{"op": "transpose", "semitones": 1}], _hj
    fs = m.TOOLS["flp_score"][0](fl)["score"]
    assert "key C major" in fs and "4 Horn [french horn]" in fs, fs
    # apply the suggested fix with the server's own writer -> the rub is gone
    m.flp_edit_notes(fl, [{"op": "transpose", "semitones": 1}], pattern=1, channel=4, start_bar=1, end_bar=2, low=66, high=66)
    after = SC.clashes(fl)
    assert not [i for i in after["issues"] if i["kind"] == "rub"], after["issues"]
    # doctor helper: timpani written below its range on channel 0
    tp = write("timp", prog(8, extra=[(0, P, 5, N("E1"), 100), (BAR, P, 5, N("D2"), 100)]), {0: 0, 1: 1, 2: 2, 5: 4})
    flh_store.journal_update(tp, set={"channels": {"4": {"name": "Timpani"}}})
    dc = SC.doctor_check(tp)
    assert dc["range"] and dc["range"][0]["kind"] == "out_of_range" and "E1" in dc["range"][0]["notes"], dc
    # lazy mode: reading a shadow copy (no journal next to it) still uses the real project's journal
    flh_store.journal_update(fl, set={"sections": [{"name": "Head", "bar": 1}, {"name": "Tail", "bar": 5}]})
    shadow_dir = os.path.join(tmp, "pending"); os.makedirs(shadow_dir, exist_ok=True)
    sh = os.path.join(shadow_dir, "shadow.flp"); shutil.copy(fl, sh)
    assert "Head" not in json.dumps(SC.score_view(sh, format="json")["sections"])
    jv = SC.score_view(sh, format="json", journal_from=fl)
    assert [x["name"] for x in jv["sections"]] == ["Head", "Tail"], jv["sections"]
    print("lazy melody part:", SC.clashes(sh)["melody_part"], SC.clashes(sh, journal_from=fl)["melody_part"])
    assert SC.clashes(sh, journal_from=fl)["melody_part"] == "Lead(ch2)" != SC.clashes(sh)["melody_part"]
    # queued (lazy) edits: flp_score / flp_clashes read the queued state (shadow) with the project's journal, say so
    sh2 = os.path.join(shadow_dir, os.path.basename(fl)); shutil.copy(fl, sh2)
    m.flp_edit_notes(sh2, [{"op": "transpose", "semitones": 2}], channel=0)          # the queued edit
    real = {k: m.__dict__.get(k) for k in ("_pending_get", "_shadow")}
    m._pending_get = lambda p: [{"tool": "flp_edit_notes"}] if os.path.abspath(p) == os.path.abspath(fl) else []
    m._shadow = lambda p: (sh2, None)
    try:
        assert getattr(m.TOOLS["flp_score"][0], "__lazy__", False) and getattr(m.TOOLS["flp_clashes"][0], "__lazy__", False)
        qs = m.TOOLS["flp_score"][0](project=fl, format="json")
        assert qs["pending_ops"] == 1 and "included" in qs["pending_note"] and "queued" in qs["read"], qs
        assert [x["name"] for x in qs["sections"]] == ["Head", "Tail"], qs["sections"]       # the real journal
        plain = SC.score_view(fl, format="json")
        assert qs["chords"] != plain["chords"], "the queued transposition is in the read"
        assert sh2 not in json.dumps(qs) and shadow_dir not in json.dumps(qs)
        qc = m.TOOLS["flp_clashes"][0](project=fl)
        assert qc["pending_ops"] == 1 and qc["melody_part"] == "Lead(ch2)" and "read" in qc, qc
    finally:
        m.__dict__.update(real)
    assert "pending_ops" not in m.TOOLS["flp_score"][0](project=fl, format="json")      # nothing queued: the file

# fix hints: a listed tool is named directly, an unlisted one through flh_call (callable in every client)
ns = vars(m)
SC._LISTED["t"] = 0
assert SC.call_hint("flp_compose", {"project": SC.Raw("project"), "what": "beat"}, ns) == "flp_compose(project, what='beat')"
h = SC.call_hint("flp_edit_notes", {"path": SC.Raw("project"), "channel": 3, "ops": [{"op": "delete"}]}, ns)
assert h == 'flh_call("flp_edit_notes", {"path": project, "channel": 3, "ops": [{"op":"delete"}]})', h
os.environ["FLH_TOOLSET"] = "full"; SC._LISTED["t"] = 0
try:
    assert SC.call_hint("flp_edit_notes", {"channel": 3}, ns) == "flp_edit_notes(channel=3)"
finally:
    os.environ.pop("FLH_TOOLSET"); SC._LISTED["t"] = 0
assert SC.call_hint("flp_edit_notes", {"channel": 3}, {}).startswith('flh_call(')          # unknown -> flh_call
# ---------------------------------------------------------------- a time-signature change mid-song (meter map)
# bars 1-4 in 4/4 (C Am F G), then 3/4 from bar 5 (tick 16 beats): Dm G C Am, 3 beats each; a horn F#4 rub at bar 6
def map_song(horn=True):
    out = []
    for b, (bass, pad) in enumerate((("C2", ["C4", "E4", "G4"]), ("A1", ["A3", "C4", "E4"]), ("F1", ["F3", "A3", "C4"]),
                                     ("G1", ["G3", "B3", "D4"]))):
        out += [(b * BAR, BAR, 0, N(bass), 90)] + [(b * BAR, BAR, 1, N(k), 80) for k in pad]
    for b, (bass, pad) in enumerate((("D2", ["D4", "F4", "A4"]), ("G1", ["G3", "B3", "D4"]), ("C2", ["C4", "E4", "G4"]),
                                     ("A1", ["A3", "C4", "E4"]))):
        t = 4 * BAR + b * 3 * P
        out += [(t, 3 * P, 0, N(bass), 90)] + [(t, 3 * P, 1, N(k), 80) for k in pad]
    if horn:
        out.append((4 * BAR + 3 * P, 3 * P, 3, N("G#4"), 95))     # bar 6 (G chord): G#4 rubs the G4 / G3
    return out


MM = [(0, 4, 4), (4 * BAR, 3, 4)]
mm = SC.build_model(map_song(), P, parts=PARTS, meters=MM)
assert mm["n_bars"] == 8 and SC._bar(mm, 4 * BAR + 3 * P) == 6 and SC._bb(mm, 4 * BAR + 4 * P) == "6:2", (mm["n_bars"], SC._bar(mm, 4 * BAR + 3 * P), SC._bb(mm, 4 * BAR + 4 * P))
assert SC._bar_tick(mm, 7) == 4 * BAR + 6 * P and abs(SC._tick_barf(mm, 4 * BAR + 6 * P + P) - (7 + 1 / 3)) < 1e-9
jm = SC.score_view(mm, format="json")
assert jm["meter"] == "4/4 > 3/4 (bar 5)" and jm["bars"] == 8, (jm["meter"], jm["bars"])
assert [c["chord"] for c in jm["chords"]] == ["C", "Am", "F", "G", "Dm", "G", "C", "Am"], jm["chords"]
assert SC._range_arg(mm, bars="5-6") == (4 * BAR, 4 * BAR + 6 * P, "bars 5-6")
flat = SC.build_model(map_song(), P, parts=PARTS)                 # the same notes read as 4/4 throughout: wrong bars
assert [c["chord"] for c in SC.score_view(flat, format="json")["chords"]][4:] != ["Dm", "G", "C", "Am"]
cm = SC.clashes(mm)
rub = next(i for i in cm["issues"] if i["kind"] == "rub")
assert rub["where"] == "bar 6:1" and "Ab4" in rub["notes"], rub
assert "ROLL ch1 bars 5-6 (4/4 > 3/4 (bar 5)" in SC.score_view(mm, bars="5-6", part=1, piano_roll=True)["score"]
rw = SC.roll_words(mm, 1, 4 * BAR, 4 * BAR + 6 * P)
assert rw[1].startswith(" bar 5: D4 on beat 1") and rw[2].startswith(" bar 6: G3 on beat 1"), rw
# a .flp with a time-signature marker: the model reads the map (FL's marker events)
src = os.path.join(tmp, "map.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), src) if \
    os.path.isfile(os.path.join(HERE, "corpus", "base.flp")) else None
if os.path.isfile(src):
    import struct
    d = m._load(src); ev = m._events(d); Pf = m._ppq(d)
    k99 = next(k for k, e in enumerate(ev) if e["id"] == 99)
    mk = bytes([148]) + struct.pack("<I", 4 * 4 * Pf | m.MARKER_SIGNATURE) + bytes([33, 3, 34, 4])
    m._save(m._splice(d, ev, [(k99 + 1, k99 + 1, mk)]), src)
    SC._CACHE.clear()
    lm = SC.load_model(src)
    assert lm["meters"] == [(0, 4, 4), (16 * Pf, 3, 4)] and SC._bar_tick(lm, 6) == 16 * Pf + 3 * Pf, lm["meters"]
print("ok")
