"""sheet_notation: ABC + MusicXML parsing to exact notes, repeats/endings/D.C./D.S. playback, validation of reading
mistakes, MIDI round trip through the server's own reader, inline export and diff."""
import io, os, shutil, sys, tempfile, zipfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import sheet_notation as S  # noqa: E402

P = S.PPQ
Q, E8 = P, P // 2


def notes(sc, voice=None, full=False):
    out = []
    for n in sc.notes:
        if voice is None or n["voice"] == voice:
            out.append((n["start_tick"], n["len_ticks"], n["pitch"], n["vel"]) if full else
                       (n["start_tick"], n["len_ticks"], n["pitch"]))
    return out


def kinds(iss):
    return sorted({x["kind"] for x in iss})


def quiet(sc, label):
    iss = S.validate(sc)
    assert iss == [], (label, iss)


# ---------------------------------------------------------------- 1. C major scale, header fields
sc = S.parse_abc("""X:1
T:Scale
C:Trad.
M:4/4
L:1/4
Q:1/4=100
K:C
CDEF|GABc|]
""")
assert sc.title == "Scale" and sc.composer == "Trad."
assert notes(sc) == [(i * Q, Q, p) for i, p in enumerate([60, 62, 64, 65, 67, 69, 71, 72])], notes(sc)
assert sc.tempo_map == [(0, 100.0)] and sc.meter_map == [(0, 4, 4)] and sc.key_map == [(0, 0, 0)]
assert [m["num"] for m in sc.measures] == [1, 2] and sc.measures[1]["start_tick"] == 4 * Q
assert all(n["vel"] == 80 and n["voice"] == "1" and n["source_line"] == 8 for n in sc.notes)
quiet(sc, "scale")
s = sc.summary()
assert s["bars"] == 2 and s["voices"]["1"]["range"] == "C4-C5" and s["notes"] == 8 and s["seconds"] == 4.8, s

# default L: from the meter (3/4 -> 1/8, 2/4 -> 1/16), Q:120 in unit notes, no X: line
sc = S.parse_abc("M:2/4\nK:C\nCDEF GABc|]\n")
assert notes(sc)[1] == (240, 240, 62)
sc = S.parse_abc("X:1\nM:3/4\nL:1/8\nQ:120\nK:C\nC6|]")
assert notes(sc) == [(0, 6 * E8, 60)] and sc.tempo_map == [(0, 60.0)]
sc = S.parse_abc("X:1\nM:6/8\nQ:3/8=60\nK:C\nC3 D3|]")
assert sc.tempo_map == [(0, 90.0)] and notes(sc) == [(0, 3 * E8, 60), (3 * E8, 3 * E8, 62)]

# ---------------------------------------------------------------- 2. 6/8 jig: pickup, repeats, 1st/2nd endings
JIG = """X:2
T:Jig
M:6/8
L:1/8
K:G
|:D|GAB c2A|BGE D2D|1 GAB AGF|G3 G2:|2 GAB AGF|G3 G3|]
"""
sc = S.parse_abc(JIG)
gm = sc.measures
assert len(gm) == 7 and [m["num"] for m in gm] == [0, 1, 2, 3, 4, 5, 6]
assert gm[0]["implicit"] and gm[0]["len_ticks"] == E8 and gm[4]["implicit"] and gm[4]["len_ticks"] == 5 * E8
assert gm[0]["rep_start"] and gm[4]["rep_end"] and gm[3]["volta"] == [1] and gm[4]["volta"] == [1]
assert gm[5]["volta"] == [2] and gm[6]["volta"] == [2] and not gm[1]["volta"]
assert sc.order == [0, 1, 2, 3, 4, 0, 1, 2, 5, 6], sc.order
ps = [n["pitch"] for n in sc.notes]
first = [62, 67, 69, 71, 72, 69, 71, 67, 64, 62, 62]
assert ps == first + [67, 69, 71, 69, 67, 66, 67, 67] + first + [67, 69, 71, 69, 67, 66, 67, 67], ps
# second time round the pickup starts right after the 5-eighth bar 4
t_rep = (1 + 6 + 6 + 6 + 5) * E8
assert sc.notes[19]["start_tick"] == t_rep and sc.notes[19]["pitch"] == 62 and sc.notes[19]["measure"] == 0
assert sc.notes[-1]["start_tick"] == t_rep + (1 + 6 + 6 + 6 + 3) * E8 and sc.notes[-1]["len_ticks"] == 3 * E8
quiet(sc, "jig")
s = sc.summary()
assert s["pickup"] and s["play_order"] == "0-4 0-2 5-6" and s["keys"] == [[0, "G major"]], s
inl = S.to_inline(sc)
v = inl["voices"]["1"]
assert abs(v[0][0] - (1 + 5 / 6)) < 1e-6 and abs(v[0][1] - 1 / 6) < 1e-6 and v[0][2:] == [62, 80], v[0]
assert v[1][0] == 2 and abs(v[1][1] - 1 / 6) < 1e-6 and v[1][2] == 67, v[1]
assert inl["summary"]["fl_meter"] == "6/8" and inl["summary"]["pickup_offset_bars"] == round(5 / 6, 6)
v2 = S.to_inline(sc, pickup="start")["voices"]["1"]
assert v2[0][0] == 1 and abs(v2[1][0] - (1 + 1 / 6)) < 1e-6

# ---------------------------------------------------------------- 3. two-voice piano: bass clef, %%score, triplets
PIANO = """X:3
T:Two hands
M:3/4
L:1/4
Q:1/4=90
%%score {RH LH}
V:RH clef=treble name="Right Hand"
V:LH clef=bass name="Left Hand"
K:F
V:RH
!p! A B c | (3A/B/c/ d2 | c3 |]
V:LH
F,, [C,F,A,] [C,F,A,] | B,, [D,F,B,]2 | F,,3 |]
"""
sc = S.parse_abc(PIANO)
assert [v.name for v in sc.voices] == ["Right Hand", "Left Hand"]
assert sc.voices[0].clef == "treble" and sc.voices[1].clef == "bass" and all(v.piano for v in sc.voices)
assert sc.voices[0].staff != sc.voices[1].staff
rh, lh = notes(sc, "Right Hand", True), notes(sc, "Left Hand", True)
T3 = Q // 3 * 2 // 2  # a triplet eighth: 320
assert rh == [(0, Q, 69, 54), (Q, Q, 70, 54), (2 * Q, Q, 72, 54),
              (3 * Q, 320, 69, 54), (3 * Q + 320, 320, 70, 54), (3 * Q + 640, 320, 72, 54), (4 * Q, 2 * Q, 74, 54),
              (6 * Q, 3 * Q, 72, 54)], rh
# LH has no dynamics of its own: it follows the right hand's !p!
assert lh == [(0, Q, 41, 54), (Q, Q, 48, 54), (Q, Q, 53, 54), (Q, Q, 57, 54), (2 * Q, Q, 48, 54), (2 * Q, Q, 53, 54),
              (2 * Q, Q, 57, 54), (3 * Q, Q, 46, 54), (4 * Q, 2 * Q, 50, 54), (4 * Q, 2 * Q, 53, 54),
              (4 * Q, 2 * Q, 58, 54), (6 * Q, 3 * Q, 41, 54)], lh
assert sc.tempo_map == [(0, 90.0)] and sc.key_map == [(0, -1, 0)] and sc.keys == [(0, "F major")]
quiet(sc, "piano")

# ---------------------------------------------------------------- 4. accidentals carry/cancel, ties across bars
sc = S.parse_abc("X:4\nM:4/4\nL:1/4\nK:C\n^F G F =F | ^F f F z | F2- F2- | F4 |]")
assert [p for _, _, p in notes(sc)] == [66, 67, 66, 65, 66, 77, 66, 65], notes(sc)
assert notes(sc)[-1] == (8 * Q, 8 * Q, 65) and sc.notes[-1]["tied"]
# a tie carries the accidental over the bar line (only for the tied note)
sc = S.parse_abc("X:4\nM:4/4\nL:1/4\nK:C\nz2 ^c2- | c2 c2 |]")
assert notes(sc) == [(2 * Q, 4 * Q, 73), (6 * Q, 2 * Q, 72)], notes(sc)
quiet(sc, "tie carry")
# double sharps / flats, key with explicit accidentals and modes
sc = S.parse_abc("X:4\nM:4/4\nL:1/4\nK:C\n^^C __B _E =E|]")
assert [p for _, _, p in notes(sc)] == [62, 69, 63, 64]
for k, fifths, name in (("D dorian", 0, "D dorian"), ("Am", 0, "A minor"), ("E phr", 0, "E phrygian"),
                        ("G mix", 0, "G mixolydian"), ("F lyd", 0, "F lydian"), ("B loc", 0, "B locrian"),
                        ("F#m", 3, "F# minor"), ("Bb", -2, "Bb major"), ("C#", 7, "C# major"), ("Eb minor", -6,
                                                                                            "Eb minor")):
    kk, _ = S._parse_key(k)
    assert kk["fifths"] == fifths and kk["name"] == name, (k, kk)
kk, _ = S._parse_key("D phr ^f")
assert kk["alters"]["F"] == 1 and kk["alters"]["E"] == -1 and kk["alters"]["B"] == -1
kk, clef = S._parse_key("Bb clef=bass")
assert kk["fifths"] == -2 and clef["clef"] == "bass"
sc = S.parse_abc("X:4\nM:4/4\nL:1/4\nK:D exp _b\nF B c d|]")
assert [p for _, _, p in notes(sc)] == [65, 70, 72, 74]

# ---------------------------------------------------------------- 5. key and meter changes mid-tune, inline fields
sc = S.parse_abc("X:5\nM:2/4\nL:1/8\nK:D\nFCFC | [K:Bb] BEBE | [M:3/4] [L:1/4] B c d |]")
assert [p for _, _, p in notes(sc)] == [66, 61, 66, 61, 70, 63, 70, 63, 70, 72, 74]
assert sc.meter_map == [(0, 2, 4), (4 * Q, 3, 4)] and sc.key_map == [(0, 2, 0), (2 * Q, -2, 0)], sc.key_map
assert notes(sc)[-1] == (4 * Q + 2 * Q, Q, 74) and sc.measures[2]["meter"] == (3, 4)
assert sc.summary()["meters"] == [[1, "2/4"], [3, "3/4"]]
quiet(sc, "changes")
sc = S.parse_abc("X:5\nM:4/4\nL:1/4\nK:C\nC D [Q:1/4=60] E F|]\nQ:1/4=90\nG4|]")
assert sc.tempo_map == [(0, 120.0), (2 * Q, 60.0), (4 * Q, 90.0)] and sc.tempos == [(2 * Q, 60.0), (4 * Q, 90.0)]
assert S.summary(sc)["tempo_assumed"] == 120

# ---------------------------------------------------------------- 6. grace notes, chords with lengths, decorations
sc = S.parse_abc("X:6\nM:4/4\nL:1/8\nK:C\n{g}A2 [CEG]2 !f! .c2 !>!c2 | [C2E2G2] [CE]2 z2 x2 |]")
n6 = notes(sc, full=True)
assert n6[0] == (0, 120, 79, 80) and sc.notes[0]["grace"] and n6[1] == (120, 840, 69, 80), n6[:2]
assert n6[2:5] == [(Q, Q, 60, 80), (Q, Q, 64, 80), (Q, Q, 67, 80)]
assert n6[5] == (2 * Q, E8, 72, 94) and n6[6] == (3 * Q, Q, 72, 114), n6[5:7]
assert n6[7:] == [(4 * Q, Q, 60, 94), (4 * Q, Q, 64, 94), (4 * Q, Q, 67, 94), (5 * Q, Q, 60, 94), (5 * Q, Q, 64, 94)]
quiet(sc, "grace/chords")
# two grace notes share at most half the main note; acciaccatura slash
sc = S.parse_abc("X:6\nM:4/4\nL:1/8\nK:C\n{/ga}B/ z/ z3|]")
assert notes(sc) == [(0, 60, 79), (60, 60, 81), (120, 120, 71)], notes(sc)
# chord with different inner lengths: chord advances by its first note
sc = S.parse_abc("X:6\nM:4/4\nL:1/4\nK:C\n[C2E] D z|]")
assert notes(sc) == [(0, 2 * Q, 60), (0, Q, 64), (2 * Q, Q, 62)], notes(sc)
sc = S.parse_abc("X:6\nM:4/4\nL:1/8\nK:C\n[CE]>[DF] [C2E2]>D z4|]")
assert notes(sc) == [(0, 720, 60), (0, 720, 64), (720, 240, 62), (720, 240, 65), (Q, 1440, 60), (Q, 1440, 64),
                     (Q + 1440, 240, 62)], notes(sc)
# !sfz! !pp! !mf! dynamics, staccatissimo
sc = S.parse_abc("X:6\nM:4/4\nL:1/4\nK:C\n!pp!C !sfz!D !mf!E !wedge!F|]")
assert notes(sc, full=True) == [(0, Q, 60, 40), (Q, Q, 62, 112), (2 * Q, Q, 64, 80), (3 * Q, Q // 4, 65, 80)]

# ---------------------------------------------------------------- 7. lengths, broken rhythm, tuplets, rests
sc = S.parse_abc("X:7\nM:4/4\nL:1/8\nK:C\nA>B C<D A3/2B/ c// d// e/ f | (3:2:2A2B (3cde (5:4f/g/a/b/c/ z2|]")
n7 = notes(sc)
assert n7[:4] == [(0, 720, 69), (720, 240, 71), (Q, 240, 60), (Q + 240, 720, 62)], n7[:4]
assert n7[4:8] == [(2 * Q, 720, 69), (2 * Q + 720, 240, 71), (3 * Q, 120, 72), (3 * Q + 120, 120, 74)], n7[4:8]
assert n7[8] == (3 * Q + 240, 240, 76) and n7[9] == (3 * Q + 480, E8, 77) and len(n7) == 10 + 2 + 3 + 5
b2 = 4 * Q + 0
assert n7[10:12] == [(b2, 640, 69), (b2 + 640, 320, 71)], n7[10:12]
assert n7[12:15] == [(b2 + Q, 320, 72), (b2 + Q + 320, 320, 74), (b2 + Q + 640, 320, 76)]
assert n7[15:] == [(b2 + 2 * Q + k * 192, 192, p) for k, p in enumerate([77, 79, 81, 83, 72])], n7[15:]
quiet(sc, "rhythms")
# multi-bar rest Z, invisible rest x, line continuation, comments, lyrics
sc = S.parse_abc("""X:7
M:4/4
L:1/4
K:C % key
Z2 | C D \\
E F | % continued
w: la la la la
x4 |]
""")
assert notes(sc) == [(8 * Q + i * Q, Q, p) for i, p in enumerate([60, 62, 64, 65])], notes(sc)
assert len(sc.measures) == 4 and sc.measures[3]["start_tick"] == 12 * Q
quiet(sc, "rests")

# ---------------------------------------------------------------- 8. D.C. al Fine, D.S. al Coda
sc = S.parse_abc("X:8\nM:4/4\nL:1\nK:C\nC | D !fine! || E | F !D.C.alfine! |]")
assert [p for _, _, p in notes(sc)] == [60, 62, 64, 65, 60, 62] and sc.order == [0, 1, 2, 3, 0, 1]
assert notes(sc)[-1][0] == 5 * 4 * Q
quiet(sc, "dc")
sc = S.parse_abc("X:8\nM:4/4\nL:1\nK:C\nC | !segno! D | E !dacoda! | F | G !D.S.alcoda! || !coda! A |]")
assert [p for _, _, p in notes(sc)] == [60, 62, 64, 65, 67, 62, 64, 69], notes(sc)
quiet(sc, "ds al coda")
# text annotations work too, and a D.C. pass skips repeats and takes the last ending
sc = S.parse_abc('X:8\nM:4/4\nL:1\nK:C\n|: C |1 D :|2 E | "^Fine" F || G "_D.C. al Fine" |]')
assert [p for _, _, p in notes(sc)] == [60, 62, 60, 64, 65, 67, 60, 64, 65], notes(sc)
# three endings: [1,2 ... :| [3
sc = S.parse_abc("X:8\nM:4/4\nL:1\nK:C\n|: C |1,2 D :|3 E |]")
assert [p for _, _, p in notes(sc)] == [60, 62, 60, 62, 60, 64], notes(sc)
# :: = end + start, |: missing at the start repeats from the top
sc = S.parse_abc("X:8\nM:4/4\nL:1\nK:C\nC | D :: E | F :|")
assert [p for _, _, p in notes(sc)] == [60, 62, 60, 62, 64, 65, 64, 65]
quiet(sc, "double repeat")

# ---------------------------------------------------------------- 9. clefs that transpose, transpose=, octave=, programs
sc = S.parse_abc("X:9\nM:4/4\nL:1/4\nV:1 clef=treble-8 name=Guitar\nV:2 transpose=-2 octave=-1\nK:C\n"
                 "V:1\n%%MIDI program 24\nE G c e|]\nV:2\nc d e f|]")
assert [p for _, _, p in notes(sc, "Guitar")] == [52, 55, 60, 64]
assert [p for _, _, p in notes(sc, "2")] == [58, 60, 62, 63]
assert sc.voices[0].program == 24 and sc.voices[1].program is None
sc = S.parse_abc("X:9\nM:none\nL:1/4\nK:C\nC D E | F G |]")
assert sc.measures[0]["meter"] is None and sc.measures[1]["start_tick"] == 3 * Q
quiet(sc, "free meter")
# second tune by X:
sc = S.parse_abc("X:1\nK:C\nC|]\n\nX:2\nT:Two\nK:G\nF|]\n", tune=2)
assert sc.title == "Two" and notes(sc) == [(0, E8, 66)]
try:
    S.parse_abc("hello world")
    raise AssertionError("no tune")
except ValueError:
    pass

# ---------------------------------------------------------------- 10. validation catches injected reading mistakes
def piano(rh_bars, lh_bars, key="C"):
    return ("X:10\nT:Test\nM:4/4\nL:1/4\n%%score {RH LH}\nV:RH clef=treble\nV:LH clef=bass\nK:" + key + "\n"
            "V:RH\n" + " | ".join(rh_bars) + " |]\nV:LH\n" + " | ".join(lh_bars) + " |]\n")


RH = ["e d c d", "e e e2", "d d d2", "e g g2", "e d c d", "e e e e", "d d e d", "c4"] * 1
LH = ["C, G, E, G,", "C, G, E, G,", "G,, D, B,, D,", "C, G, E, G,", "C, G, E, G,", "C, G, E, G,",
      "G,, D, B,, D,", "C,4"]
good = S.parse_abc(piano(RH, LH))
quiet(good, "good piano")
assert all(v.piano for v in good.voices)

# a missing beat
bad = list(RH); bad[2] = "d d d"
iss = S.validate(S.parse_abc(piano(bad, LH)))
assert iss[0]["kind"] == "duration" and iss[0]["bar"] == 3 and iss[0]["voice"] == "RH" and iss[0]["severity"] == "high"
assert "missing a quarter" in iss[0]["message"] and "a quarter note missing" in iss[0]["fix"], iss[0]
bad = list(RH); bad[1] = "e2 e"
iss = S.validate(S.parse_abc(piano(bad, LH)))
assert len(iss) == 1 and iss[0]["fix"].startswith("a dot lost on E5 (a half note)?"), iss
sc = S.parse_abc(piano(bad, LH))
assert sc.measures[3]["start_tick"] == 12 * Q            # later bars stay on the grid
# an extra dot
bad = list(RH); bad[1] = "e e e3/2 z/ z"
iss = S.validate(S.parse_abc(piano(bad, LH)))
assert iss and iss[0]["kind"] == "duration" and "extra" in iss[0]["message"], iss

# octave-shifted bar in the left hand
bad = list(LH); bad[4] = "C G E G"
iss = S.validate(S.parse_abc(piano(RH, bad)))
assert [x["kind"] for x in iss] == ["octave"], iss
assert iss[0]["bar"] == 5 and iss[0]["voice"] == "LH" and "octave" in iss[0]["message"] and "down" in iss[0]["fix"]

# bass clef passage read as treble (notes 12 staff steps = ~an octave + 6th too high)
bad = list(LH); bad[4] = "A e c e"; bad[5] = "A e c e"
iss = S.validate(S.parse_abc(piano(RH, bad)))
assert iss and iss[0]["kind"] == "octave" and "bars 5-6" in iss[0]["message"] and "treble" in iss[0]["fix"], iss

# hand span over a 10th
bad = list(LH); bad[7] = "[C,G,F]4"
iss = S.validate(S.parse_abc(piano(RH, bad)))
assert kinds(iss) == ["span"] and iss[0]["bar"] == 8 and "17 semitones" in iss[0]["message"], iss
# a 10th is fine
assert S.validate(S.parse_abc(piano(RH, LH[:7] + ["[C,G,E]4"]))) == []

# forgotten accidental in a passage that otherwise has F#
MEL = ["^F G A ^F", "G ^F E D", "^F G A B", "c B A G", "^F G A G", "^F A D C", "D ^F G A", "G4"]
assert S.validate(S.parse_abc(piano(MEL, LH))) == []          # (C major with F#s: fine)
bad = list(MEL); bad[4] = "F G A G"
iss = S.validate(S.parse_abc(piano(bad, LH)))
assert [x["kind"] for x in iss] == ["accidental"] and iss[0]["bar"] == 5 and "F#4" in iss[0]["fix"], iss
# ... but a chromatic passing note (F natural -> E) is left alone
bad = list(MEL); bad[5] = "F E D C"
assert S.validate(S.parse_abc(piano(bad, LH))) == []
# the same F against an F# in the other hand at the same moment
bad_lh = list(LH); bad_lh[5] = "^F, G, E, G,"
iss = S.validate(S.parse_abc(piano(["c d e f", "e d c2"] + RH[2:], ["C, G, E, F,"] + LH[1:])))
assert iss == [], iss
iss = S.validate(S.parse_abc(piano(["c d e ^f", "e d c2"] + RH[2:], ["C, G, E, F,"] + LH[1:])))
assert [x["kind"] for x in iss] == ["accidental"] and "same time" in iss[0]["message"], iss

# broken repeats and endings
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1\nK:C\n|: C | D |: E | F :|"))
assert kinds(iss) == ["repeat"] and iss[0]["bar"] == 1, iss
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1\nK:C\n|: C | D | E | F |]"))
assert kinds(iss) == ["repeat"] and "never closed" in iss[0]["message"], iss
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1\nK:C\n|: C |2 D | E |]"))
assert "volta" in kinds(iss), iss
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1\nK:C\nC | D !D.S.! |]"))
assert kinds(iss) == ["nav"] and iss[0]["severity"] == "high", iss

# tie to a different pitch, implausible tuplet, duplicate note, empty bar, bar count, key change in one voice
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1/4\nK:C\nC D E ^F- | G4 |]"))
assert kinds(iss) == ["tie"] and "F#4 to G4" in iss[0]["message"], iss
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1/8\nK:C\n(3A2BC z2 z4 z/|]"))
assert "tuplet" in kinds(iss), iss
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1/4\nK:C\n[CEC] D E F|]"))
assert kinds(iss) == ["duplicate"], iss
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1/4\nK:C\nC D E F | | G A B c|]"))
assert kinds(iss) == ["empty"] and iss[0]["bar"] == 2, iss
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1\nK:C\nV:1\nC | D | E |]\nV:2\nC | D |]"))
assert "bar_count" in kinds(iss), iss
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1\nK:C\nV:1\nC | [K:G] D | E |]\nV:2\nC | D | E |]"))
assert kinds(iss) == ["key"] and "not in 2" in iss[0]["message"], iss
# notes far outside a bass clef staff -> treble passage? ; piano range
iss = S.validate(S.parse_abc(piano(RH, ["C, G, E, G,"] * 4 + ["e' g' e' g'"] * 4)))
assert "range" in kinds(iss), iss
# unpitched syntax errors are reported, not fatal
sc = S.parse_abc("X:1\nM:4/4\nL:1/4\nK:C\nC D ? E F|]")
assert notes(sc)[-1] == (3 * Q, Q, 65) and S.validate(sc)[0]["kind"] == "syntax"
# strict adds low-severity hints
assert all(x["severity"] in ("high", "med", "low") for x in S.validate(good, strict=True))

# ---------------------------------------------------------------- 11. MusicXML: two staves, backup/forward, chords,
# ties, tuplets, repeats/endings, divisions change, transposing instrument, grace, dynamics, tempo
XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="4.0">
  <work><work-title>XML Test</work-title></work>
  <identification><creator type="composer">Nobody</creator></identification>
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name>
      <midi-instrument id="P1-I1"><midi-program>1</midi-program></midi-instrument></score-part>
    <score-part id="P2"><part-name>Clarinet in Bb</part-name>
      <midi-instrument id="P2-I1"><midi-program>72</midi-program></midi-instrument></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>6</divisions><key><fifths>-1</fifths><mode>major</mode></key>
        <time><beats>4</beats><beat-type>4</beat-type></time><staves>2</staves>
        <clef number="1"><sign>G</sign><line>2</line></clef><clef number="2"><sign>F</sign><line>4</line></clef>
      </attributes>
      <direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit>
        <per-minute>100</per-minute></metronome></direction-type><sound tempo="100"/></direction>
      <direction placement="below"><direction-type><dynamics><f/></dynamics></direction-type>
        <staff>1</staff><sound dynamics="106.67"/></direction>
      <note><pitch><step>C</step><octave>5</octave></pitch><duration>6</duration><voice>1</voice><type>quarter</type><staff>1</staff></note>
      <note><grace slash="yes"/><pitch><step>E</step><octave>5</octave></pitch><voice>1</voice><type>eighth</type><staff>1</staff></note>
      <note><pitch><step>D</step><octave>5</octave></pitch><duration>6</duration><voice>1</voice><type>quarter</type><staff>1</staff></note>
      <note><pitch><step>E</step><octave>5</octave></pitch><duration>12</duration><voice>1</voice><type>half</type><staff>1</staff></note>
      <note><chord/><pitch><step>B</step><alter>-1</alter><octave>5</octave></pitch><duration>12</duration><voice>1</voice><type>half</type><staff>1</staff></note>
      <backup><duration>24</duration></backup>
      <note><pitch><step>C</step><octave>3</octave></pitch><duration>24</duration><tie type="start"/><voice>5</voice><type>whole</type><staff>2</staff>
        <notations><tied type="start"/></notations></note>
    </measure>
    <measure number="2">
      <barline location="left"><bar-style>heavy-light</bar-style><repeat direction="forward"/></barline>
      <note><pitch><step>C</step><octave>5</octave></pitch><duration>2</duration><voice>1</voice><type>eighth</type>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><staff>1</staff>
        <notations><tuplet type="start"/></notations></note>
      <note><pitch><step>D</step><octave>5</octave></pitch><duration>2</duration><voice>1</voice><type>eighth</type>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><staff>1</staff></note>
      <note><pitch><step>E</step><octave>5</octave></pitch><duration>2</duration><voice>1</voice><type>eighth</type>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><staff>1</staff>
        <notations><tuplet type="stop"/></notations></note>
      <note><rest/><duration>18</duration><voice>1</voice><staff>1</staff></note>
      <backup><duration>24</duration></backup>
      <note><pitch><step>C</step><octave>3</octave></pitch><duration>24</duration><tie type="stop"/><voice>5</voice><type>whole</type><staff>2</staff></note>
    </measure>
    <measure number="3">
      <attributes><divisions>2</divisions></attributes>
      <barline location="left"><ending number="1" type="start"/></barline>
      <note><pitch><step>F</step><octave>5</octave></pitch><duration>8</duration><voice>1</voice><type>whole</type><staff>1</staff>
        <notations><articulations><staccato/></articulations></notations></note>
      <backup><duration>8</duration></backup>
      <forward><duration>4</duration><voice>5</voice><staff>2</staff></forward>
      <note><pitch><step>F</step><octave>2</octave></pitch><duration>4</duration><voice>5</voice><type>half</type><staff>2</staff></note>
      <barline location="right"><bar-style>light-heavy</bar-style><ending number="1" type="stop"/><repeat direction="backward"/></barline>
    </measure>
    <measure number="4">
      <barline location="left"><ending number="2" type="start"/></barline>
      <note><pitch><step>C</step><octave>5</octave></pitch><duration>8</duration><voice>1</voice><type>whole</type><staff>1</staff></note>
      <backup><duration>8</duration></backup>
      <note><rest/><duration>8</duration><voice>5</voice><staff>2</staff></note>
      <barline location="right"><bar-style>light-heavy</bar-style><ending number="2" type="discontinue"/></barline>
    </measure>
  </part>
  <part id="P2">
    <measure number="1">
      <attributes><divisions>1</divisions><key><fifths>1</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef><transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose></attributes>
      <note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration><voice>1</voice><type>whole</type></note>
    </measure>
    <measure number="2"><note><rest measure="yes"/><duration>4</duration><voice>1</voice></note></measure>
    <measure number="3"><note><pitch><step>F</step><alter>1</alter><octave>4</octave></pitch><duration>4</duration><voice>1</voice><type>whole</type></note></measure>
    <measure number="4"><note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration><voice>1</voice><type>whole</type></note></measure>
  </part>
</score-partwise>
"""
xp = os.path.join(tmp, "t.musicxml")
open(xp, "w", encoding="utf-8").write(XML)
for src in (xp, XML, XML.encode()):
    sc = S.parse_musicxml(src)
    assert sc.title == "XML Test" and sc.composer == "Nobody" and sc.divisions == 6
names = [v.name for v in sc.voices]
assert names == ["Piano RH", "Piano LH", "Clarinet in Bb"], names
assert sc.voices[0].program == 0 and sc.voices[2].program == 71 and sc.voices[1].clef == "bass"
assert sc.voices[0].staff == "P1.1" and sc.voices[1].staff == "P1.2" and sc.voices[0].piano
assert sc.order == [0, 1, 2, 1, 3], sc.order
assert sc.measures[1]["rep_start"] and sc.measures[2]["volta"] == [1] and sc.measures[3]["volta"] == [2]
assert sc.measures[2]["rep_end"] and not sc.measures[3]["rep_end"]
B = 4 * Q
rh = notes(sc, "Piano RH", True)
trip = lambda o: [(o, 320, 72, 94), (o + 320, 320, 74, 94), (o + 640, 320, 76, 94)]  # noqa: E731
exp_rh = [(0, Q, 72, 94), (Q, 120, 76, 94), (Q + 120, Q - 120, 74, 94), (2 * Q, 2 * Q, 76, 94),
          (2 * Q, 2 * Q, 82, 94)] + trip(B) + [(2 * B, 2 * Q, 77, 94)] + trip(3 * B) + [(4 * B, B, 72, 94)]
assert sorted(rh) == sorted(exp_rh), sorted(set(rh) ^ set(exp_rh))
assert [n["grace"] for n in sc.notes if n["voice"] == "Piano RH"][1]
lh = notes(sc, "Piano LH", True)
assert lh == [(0, 2 * B, 48, 94), (2 * B + 2 * Q, 2 * Q, 41, 94), (3 * B, B, 48, 94)], lh
assert [n["tied"] for n in sc.notes if n["voice"] == "Piano LH"] == [True, False, False]
cl = notes(sc, "Clarinet in Bb")
assert cl == [(0, B, 72), (2 * B, B, 64), (4 * B, B, 72)], cl     # written D5 / F#4 sound C5 / E4
assert sc.tempo_map == [(0, 100.0)] and sc.keys == [(0, "F major")] and sc.meter_map == [(0, 4, 4)]
assert [m["num"] for m in sc.measures] == [1, 2, 3, 4]
quiet(sc, "musicxml")

# .mxl (compressed) with a container
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container><rootfiles>'
               '<rootfile full-path="score/t.musicxml"/></rootfiles></container>')
    z.writestr("score/t.musicxml", XML)
mp = os.path.join(tmp, "t.mxl")
open(mp, "wb").write(buf.getvalue())
assert notes(S.parse_musicxml(mp)) == notes(sc)

# MusicXML pickup (implicit), meter change, a bar with a missing beat and an XML-level tuplet mistake
def xml_bars(bars, implicit_first=False):
    out = ['<?xml version="1.0"?><score-partwise><part-list><score-part id="P1"><part-name>Flute</part-name>'
           '</score-part></part-list><part id="P1">']
    for k, (attrs, ns) in enumerate(bars):
        out.append(f'<measure number="{k if implicit_first else k + 1}"' +
                   (' implicit="yes"' if implicit_first and k == 0 else "") + ">")
        if attrs:
            out.append(f"<attributes>{attrs}</attributes>")
        for step, octv, dur, extra in ns:
            p = "<rest/>" if step == "R" else f"<pitch><step>{step}</step><octave>{octv}</octave></pitch>"
            out.append(f"<note>{p}<duration>{dur}</duration><voice>1</voice>{extra}</note>")
        out.append("</measure>")
    out.append("</part></score-partwise>")
    return "".join(out)


ATT = "<divisions>2</divisions><time><beats>3</beats><beat-type>4</beat-type></time>"
x = xml_bars([(ATT, [("G", 4, 2, "")]), ("", [("C", 5, 2, ""), ("D", 5, 2, ""), ("E", 5, 2, "")]),
              ("<time><beats>2</beats><beat-type>4</beat-type></time>", [("F", 5, 4, "")]),
              ("", [("E", 5, 2, ""), ("D", 5, 2, "")])], implicit_first=True)
sc = S.parse_musicxml(x)
assert sc.measures[0]["implicit"] and sc.measures[0]["num"] == 0 and sc.meter_map == [(0, 3, 4), (4 * Q, 2, 4)]
assert notes(sc) == [(0, Q, 67), (Q, Q, 72), (2 * Q, Q, 74), (3 * Q, Q, 76), (4 * Q, 2 * Q, 77), (6 * Q, Q, 76),
                     (7 * Q, Q, 74)], notes(sc)
quiet(sc, "xml pickup")
assert S.to_inline(sc)["voices"]["Flute"][1][0] == 2       # bar 1 of the score = FL bar 2 (3/4 grid)
x = xml_bars([(ATT, [("C", 5, 2, ""), ("D", 5, 2, ""), ("E", 5, 2, "")]), ("", [("C", 5, 2, ""), ("D", 5, 2, "")]),
              ("", [("C", 5, 2, ""), ("D", 5, 2, ""), ("E", 5, 2, "")])])
iss = S.validate(S.parse_musicxml(x))
assert kinds(iss) == ["duration"] and iss[0]["bar"] == 2, iss
x = xml_bars([(ATT, [("C", 5, 2, "<notations><dynamics><sfz/></dynamics><articulations><accent/></articulations>"
                                   "</notations>"), ("D", 5, 2, "<notations><articulations><staccato/></articulations>"
                                                                "</notations>"), ("E", 5, 2, "")])])
assert notes(S.parse_musicxml(x), full=True) == [(0, Q, 72, 127), (Q, E8, 74, 80), (2 * Q, Q, 76, 80)]
TM = "<time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>"
x = xml_bars([("<divisions>6</divisions><time><beats>2</beats><beat-type>4</beat-type></time>",
               [("C", 5, 2, TM + '<notations><tuplet type="start"/></notations>'), ("D", 5, 2, TM),
                ("E", 5, 2, TM), ("F", 5, 2, TM + '<notations><tuplet type="stop"/></notations>'),
                ("R", 0, 4, "")])])
iss = S.validate(S.parse_musicxml(x))
assert "tuplet" in kinds(iss), iss
for badsrc in ("<html></html>", b"PK\x03\x04garbage", "<score-timewise/>",
               '<!DOCTYPE x [<!ENTITY a "aaaa">]><score-partwise/>', os.path.join(tmp, "missing.xml")):
    try:
        S.parse_musicxml(badsrc)
        raise AssertionError(badsrc)
    except ValueError:
        pass

# ---------------------------------------------------------------- 12. MIDI round trip through the server's reader
import fl_headless_mcp as m  # noqa: E402
for src in (S.parse_abc(PIANO), S.parse_abc(JIG), S.parse_musicxml(XML)):
    mp = os.path.join(tmp, "o.mid")
    r = S.to_midi(src, mp)
    assert r["tracks"] == len(src.voices) + 1 and r["notes"] == len(src.notes)
    div, mn, tmap = m.midi_read(mp)
    assert div == P
    mine = sorted((n["start_tick"], n["start_tick"] + n["len_ticks"], n["pitch"], n["vel"]) for n in src.notes)
    assert sorted(mn) == mine, (len(mn), len(mine))
    assert tmap == [(t, b) for t, b in src.tempo_map], (tmap, src.tempo_map)
    raw = open(mp, "rb").read()
    assert b"\xff\x58\x04" in raw and b"\xff\x59\x02" in raw and b"\xff\x03" in raw
    assert S.to_midi(src)["bytes"] == raw
# program changes land on the voice tracks
raw = S.to_midi(S.parse_musicxml(XML))["bytes"]
assert bytes([0xC2, 71]) in raw and bytes([0xC0, 0]) in raw

# ---------------------------------------------------------------- 13. diff: two readings, and ABC vs MusicXML
a = S.parse_abc(piano(RH, LH))
d = S.diff(a, S.parse_abc(piano(RH, LH)))
assert d["same"] and d["differing"] == 0
bad = list(RH); bad[3] = "e g ^g2"
d = S.diff(a, S.parse_abc(piano(bad, LH)))
assert not d["same"] and d["differing"] == 1 and d["bars"][0]["bar"] == 4 and d["bars"][0]["changed"] == ["3: G5->G#5"], d
bad = list(LH); bad[4] = "C G E G"
d = S.diff(a, S.parse_abc(piano(RH, bad)))
assert d["bars"][0]["bar"] == 5 and all("(octave)" in c for c in d["bars"][0]["changed"]), d
# an inserted bar shows as one insert, the rest still matches
ins = S.parse_abc("X:1\nM:4/4\nL:1/4\nK:C\nCDEF|GABc|cBAG|]")
ins2 = S.parse_abc("X:1\nM:4/4\nL:1/4\nK:C\nCDEF|EEEE|GABc|cBAG|]")
d = S.diff(ins, ins2)
assert d["differing"] == 1 and d["bars"][0]["only_in"] == "b" and d["bars"][0]["bar_b"] == 2, d
# the same music as ABC and as MusicXML compares equal
x = xml_bars([("<divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time>",
               [(s_, o_, 1, "") for s_, o_ in (("C", 4), ("D", 4), ("E", 4), ("F", 4))]),
              ("", [(s_, o_, 1, "") for s_, o_ in (("G", 4), ("A", 4), ("B", 4), ("C", 5))]),
              ("", [(s_, o_, 1, "") for s_, o_ in (("C", 5), ("B", 4), ("A", 4), ("G", 4))])])
d = S.diff(ins, S.parse_musicxml(x))
assert d["differing"] == 0, d

# ---------------------------------------------------------------- 14. tool-layer helpers
sc = S.parse_abc(PIANO)
bn = sc.bar_notes()
assert len(bn) == 3 and bn[0]["bar"] == 1 and bn[0]["meter"] == (3, 4) and (0.0, 1.0, 69) in bn[0]["notes"]
assert sc.meter == (3, 4) and sc.key == "F major" and sc.tempo == 90.0 and sc.meta["title"] == "Two hands"
inl = S.to_inline(sc)
r0 = inl["voices"]["Right Hand"][0]
assert set(inl["voices"]) == {"Right Hand", "Left Hand"} and r0[0] == 1 and r0[2:] == [69, 54]
assert abs(r0[1] - 1 / 3) < 1e-6 and inl["summary"]["fl_bars"] == 3

# ---------------------------------------------------------------- 15. more ABC syntax
# inline voice switches, alto clef, a clef change by K:, slurs, L accent, [2 ending with a space, [| and ||
sc = S.parse_abc("X:1\nM:4/4\nL:1/4\nV:A clef=alto\nK:C\n[V:A] (C D) LE F | [K:clef=bass] C, D, E, F, |]\n"
                 "[V:B] c d e f | g a b c' |]")
assert [v.clef for v in sc.voices] == ["bass", "treble"] and notes(sc, "A", True)[2] == (2 * Q, Q, 64, 100)
assert sc.voices[0].measures[0]["events"][0]["notes"][0]["clef"] == "alto"
assert [p for _, _, p in notes(sc, "B")] == [72, 74, 76, 77, 79, 81, 83, 84]
sc = S.parse_abc("X:1\nM:4/4\nL:1\nK:C\n[| C |: D | [1 E :| [2 F || G |]")
assert [p for _, _, p in notes(sc)] == [60, 62, 64, 62, 65, 67], notes(sc)
quiet(sc, "spaced endings")
# voices with different pickup lengths
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1/4\nK:C\nV:1\nG | c4 | c3 |]\nV:2\nE2 | C4 | C3 |]"))
assert kinds(iss) == ["duration"] and iss[0]["bar"] == 0 and "other voices have 2" in iss[0]["message"], iss
# strict mode adds low-severity hints (a 2nd/9th that would be consonant with the key's accidental)
iss = S.validate(S.parse_abc("X:1\nM:4/4\nL:1\nK:G\nV:1\n=F | G |]\nV:2\nE, | G, |]"), strict=True)
assert iss and all(x["severity"] == "low" for x in iss) and S.validate(S.parse_abc(
    "X:1\nM:4/4\nL:1\nK:G\nV:1\n=F | G |]\nV:2\nE, | G, |]")) == [], iss

# a realistic correct piece (a minuet with a modulating middle part and both halves repeated) stays quiet
MINUET = """X:1
T:Minuet
M:3/4
L:1/8
Q:1/4=110
%%score {RH LH}
V:RH clef=treble
V:LH clef=bass
K:G
V:RH
d2 GA Bc | d2 G2 G2 | e2 cd ef | g2 G2 G2 | c2 dc BA | B2 cB AG | F2 GA BG | A6 |
d2 GA Bc | d2 G2 G2 | e2 cd ef | g2 G2 G2 | c2 dc BA | B2 cB AG | A2 BA GF | G6 :|
|: b2 ga bg | a2 de fd | g2 ef gd | ^c2 Bc A2 | AB cd ef | g2 f2 e2 | f2 A2 ^c2 | d6 |
d2 GF G2 | e2 GF G2 | d2 c2 B2 | AG FG A2 | DE FG AB | c2 B2 A2 | Bd G2 F2 | G6 :|
V:LH
[G,B,D]4 A,2 | B,6 | C6 | B,6 | A,6 | G,6 | D2 B,2 G,2 | D2 D,2 C,2 |
B,,4 A,,2 | B,,2 G,,2 G,,2 | C6 | B,6 | A,6 | G,6 | D2 B,2 G,2 | G,6 :|
|: G,6 | F,6 | E,4 G,2 | A,4 A,,2 | A,6 | B,2 A,2 G,2 | A,2 D,2 A,,2 | D,6 |
B,6 | C6 | B,4 A,2 | D,4 F,2 | A,6 | G,2 F,2 E,2 | D,2 G,,2 D,2 | G,,6 :|
"""
sc = S.parse_abc(MINUET)
quiet(sc, "minuet")
assert len(sc.order) == 64 and sc.summary()["play_order"] == "1-16 1-32 17-32"
# ... and a left-hand bar read in the treble clef is caught
iss = S.validate(S.parse_abc(MINUET.replace("A,6 | G,6 | D2 B,2 G,2 | D2 D,2", "A,6 | e6 | D2 B,2 G,2 | D2 D,2", 1)))
assert iss and iss[0]["bar"] == 6 and iss[0]["voice"] == "LH" and iss[0]["kind"] in ("octave", "range"), iss

# ---------------------------------------------------------------- 16. garbage in -> ValueError or issues, never a crash
import random  # noqa: E402
rnd = random.Random(7)
alpha = list("ABCDEFGabcdefgzZx^_=,'/2345|:[]()<>{}-!\"% .~\n") + ["!p!", "[K:G]", "[M:3/4]", "|1", ":|2", "(3", "!D.C.!",
                                                                   "V:2\n", "V:1\n", "Z2", "[CEG]"]
for _ in range(300):
    body = "".join(rnd.choice(alpha) for _ in range(rnd.randint(1, 80)))
    try:
        sc = S.parse_abc("X:1\nM:%s\nL:1/8\nK:%s\n" % (rnd.choice(["4/4", "6/8", "none"]), rnd.choice(["C", "Bbm"]))
                         + body)
        S.validate(sc, strict=True), S.to_inline(sc), S.to_midi(sc), S.diff(sc, sc)
    except ValueError:
        pass
toks = __import__("re").findall(r"<[^>]+>|[^<]+", XML)
for _ in range(150):
    t = list(toks)
    for _ in range(3):
        i = rnd.randrange(len(t))
        if rnd.random() < 0.5:
            del t[i]
        elif not t[i].startswith("<"):
            t[i] = rnd.choice(["0", "-3", "x", "", "2.5"])
    try:
        sc = S.parse_musicxml("".join(t))
        S.validate(sc, strict=True), S.to_inline(sc), S.to_midi(sc)
    except ValueError:
        pass
print("ok")
