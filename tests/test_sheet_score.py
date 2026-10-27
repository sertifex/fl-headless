"""Per-bar score data (flh-score/1) -> exact notes: sheet_notation.parse_score_data (pitch names, the key signature
written back as ABC accidentals, overlapping lines as same-name ABC voices, chords, ties across bar lines, graces
that never overlap the same key, pickups, meter/key/tempo changes, repeats), the checks with the data's own bar
numbers (overflow, outside the bar, bad pitches, missing bars, grid, per-line beat sums), diff/merge of readings,
and sheet_import(score=...) into MIDI and a project (ONE edit, own bar numbers, channel={part: index}, journal),
sheet_merge / sheet_diff / sheet_explain through the MCP dispatcher. Only small synthetic examples.
Run: python3 tests/test_sheet_score.py   (prints 'ok')"""
import json, os, random, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402
import flh_sheet as SH  # noqa: E402
import flh_store  # noqa: E402
import sheet_notation as SN  # noqa: E402
from fractions import Fraction as Fr  # noqa: E402
ns = vars(m)
flh_store.register(ns); SH.register(ns)
P = SN.PPQ


def call(name, **args):
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    raw = next(c["text"] for c in r["content"] if c["type"] == "text")
    return {"_error": raw} if r.get("isError") else json.loads(raw)


def played(sc, voice=None):
    return sorted((n["start_tick"], n["len_ticks"], n["pitch"]) for n in sc.notes if voice in (None, n["voice"]))


def kinds(iss):
    return [(i["kind"], i["bar"], i["severity"]) for i in iss]


# ============================================================================= 1. pitch names
assert SN.parse_pitch("C4")[3] == 60 and SN.parse_pitch("Bb4")[3] == 70 and SN.parse_pitch("C#5")[3] == 73
assert SN.parse_pitch("Cb4")[3] == 59 and SN.parse_pitch("B#3")[3] == 60 and SN.parse_pitch("F##3")[3] == 55
assert SN.parse_pitch("Ebb2")[3] == 38 and SN.parse_pitch("Bn4")[3] == 71 and SN.parse_pitch("b♭4")[3] == 70
assert SN.parse_pitch("A-1")[3] == 9
for bad in ("H4", "C", "Bb", "C#10", "x4", ""):
    try:
        SN.parse_pitch(bad)
        raise SystemExit(f"{bad!r} must be refused")
    except ValueError:
        pass

# ============================================================================= 2. a small two-part piece
TUNE = {"format": "flh-score/1", "title": "Little Song", "meter": "4/4", "key": "F", "tempo": 90,
        "parts": {"piano": {"clef": "bass"}},
        "bars": {"1": {"section": "A", "chord": "F", "mel": [[0, 1, "A4"], [1, 1, "Bb4"], [2, 2, "C5", "tie"]],
                       "parts": {"piano": [[0, 2, "F3"], [0, 2, "A3"], [0, 4, "C3"], [2, 1, "Bb3"], [3, 1, "B3"]]}},
                 "2": {"chord": [[0, "Dm"], [2, "C"]],
                       "mel": [[0, 1, "C5"], [1, 0, "D5"], [1, 1, "C5"], [2, 1.5, "A4"], [3.5, 0.5, "G4"]],
                       "parts": {"piano": [[0, 4, "D3"], [2, 0, "E3"], [2, 2, "F3"]]}},
                 "3": {"section": "B", "mel": [[0, "1/3", "F4"], ["1/3", "1/3", "G4"], ["2/3", "1/3", "A4"],
                                               [1, 3, "F4", "accent"]], "piano": [[0, 4, "F2"]]}}}
sc = SN.parse_score_data(TUNE)
si = sc.score_info
assert SN.validate(sc, strict=True) == [] or all(i["severity"] == "low" for i in SN.validate(sc, strict=True)), \
    SN.validate(sc, strict=True)
assert si["parts"] == ["piano", "mel"] or si["parts"] == ["mel", "piano"], si["parts"]
assert si["layers"]["piano"] == 2 and si["layers"]["mel"] == 1, si["layers"]   # C3 whole note under the chords
assert si["sections"] == [[1, "A"], [3, "B"]] and si["chords"][1] == [2, 0.0, "Dm"], (si["sections"], si["chords"])
assert si["beat_check"] == {"bars": 3, "lines": 6, "full": 6, "gaps": 0}, si["beat_check"]
mel = played(sc, "mel")
assert (2 * P, 3 * P, 72) in mel, mel                         # C5 half tied into bar 2's quarter: one 3-beat note
assert not any(s == 4 * P and p == 72 and ln == P for s, ln, p in mel)
g = [n for n in sc.notes if n["voice"] == "mel" and n["grace"]]
assert len(g) == 1 and g[0]["pitch"] == 74 and g[0]["start_tick"] == 5 * P and g[0]["len_ticks"] <= P // 8, g
assert (8 * P, P // 3, 65) in mel and (8 * P + P // 3, P // 3, 67) in mel     # triplets exact ('1/3')
acc = [n for n in sc.notes if n["voice"] == "mel" and n["start_tick"] == 9 * P][0]
assert acc["vel"] > 80, acc                                   # accent
pno = played(sc, "piano")
assert (3 * P, P, 59) in pno and (2 * P, P, 58) in pno, pno       # B natural against the key's Bb, Bb3 from the key
inl = SN.to_inline(sc)["voices"]
assert set(inl) == {"mel", "piano"}, inl.keys()                # the layers are ONE inline voice per part
assert len(inl["piano"]) == sum(1 for n in sc.notes if n["voice"] == "piano")
mid = SN.to_midi(sc)
assert mid["tracks"] == 3, mid                                 # conductor + one track per part (layers merged)
assert [g["num"] for g in sc.measures] == [1, 2, 3]
assert "V:v1" in si["abc"] and "L:1/192" in si["abc"]

# a grace on the key a held note in another line is sounding: the held note ends where the grace starts
G = {"meter": "4/4", "bars": {"1": {"rh": [[0, 4, "G4"], [2, 0, "G4"], [2, 2, "B4"]]}}}
sc = SN.parse_score_data(G)
rh = played(sc, "rh")
held = [x for x in rh if x[2] == 67 and x[0] == 0][0]
gr = [n for n in sc.notes if n["grace"]][0]
assert held[0] + held[1] <= gr["start_tick"], (rh, gr)          # never two G4s at once
assert sc.score_info["fixed"] == {"cut": 1}, sc.score_info["fixed"]
for n1 in sc.notes:                                            # and nothing else overlaps on one key
    for n2 in sc.notes:
        if n1 is not n2 and n1["pitch"] == n2["pitch"]:
            assert n1["start_tick"] + n1["len_ticks"] <= n2["start_tick"] or \
                n2["start_tick"] + n2["len_ticks"] <= n1["start_tick"]
# a shared notehead (both voices list it) is kept once, the longer
sc = SN.parse_score_data({"bars": {"1": {"rh": [[0, 1, "E5"], [0, 4, "E5"], [1, 3, "C5"]]}}})
assert [x for x in played(sc) if x[2] == 76] == [(0, 4 * P, 76)], played(sc)

# ============================================================================= 3. the key signature round trip
rnd = random.Random(5)
LET = "CDEFGAB"
for key in ("C", "G", "D", "Bb", "Eb", "F#", "Ab", "Dm", "Ddor", "C#m", -3, "2#", "1b"):
    data = {"meter": "3/4", "key": key, "bars": {}}
    want = []
    for bar in range(1, 7):
        ents, t = [], Fr(0)
        while t < 3:
            d = rnd.choice([Fr(1, 2), Fr(1), Fr(1, 4)])
            d = min(d, 3 - t)
            nm = rnd.choice(LET) + rnd.choice(["", "", "#", "b", "n", "##", "bb"]) + str(rnd.choice([3, 4, 5]))
            ents.append([str(t), str(d), nm])
            want.append(((bar - 1) * 3 * P + int(t * P), SN.parse_pitch(nm)[3]))
            t += d
        data["bars"][str(bar)] = {"line": ents}
    sc = SN.parse_score_data(data)
    got = sorted((n["start_tick"], n["pitch"]) for n in sc.notes)
    assert got == sorted(want), (key, [x for x in got if x not in want][:4], [x for x in want if x not in got][:4])

# ============================================================================= 4. reading mistakes, with the data's bar numbers
BAD = {"meter": "4/4", "key": "Dm",
       "bars": {"29": {"mel": [[0, 2, "D5"], [2, 3, "F5"]]},                     # 5 beats: overflow
                "30": {"mel": [[0, 4, "A4"]], "bass": [[0, 4, "roll / l.v."]]},   # words in a note list
                "31": {"mel": [[4, 1, "A4"], [0, 1, "G4"]]},                     # starts outside the bar
                "33": {"mel": [[0, 4, "D5"]]},                                   # bar 32 missing
                "34": {"mel": [[0, 0.3, "D5"], [0.3, 3.7, "E5"]]},               # off the grid
                "35": {"mel": [[0, 2, "D5"]]}}}                                  # a gap (rest or missed note)
sc = SN.parse_score_data(BAD)
iss = SN.validate(sc, strict=True)
k = kinds(iss)
assert ("overflow", 29, "high") in k and ("pitch", 30, "high") in k and ("position", 31, "high") in k, k
assert ("missing_bar", 32, "med") in k and ("grid", 34, "med") in k and ("gap", 35, "low") in k, k
assert sc.score_info["beat_check"]["over"] == ["29 mel"] and sc.score_info["beat_check"]["gaps"] >= 1
assert [g["num"] for g in sc.measures] == [29, 30, 31, 32, 33, 34, 35]
assert all(n["measure"] in range(29, 36) for n in sc.notes)
assert "ends at beat 5" in next(i for i in iss if i["kind"] == "overflow")["message"]
assert ("gap", 35, "low") not in kinds(SN.validate(sc))           # info-level gaps only in strict mode
# ABC's own checks report in the data's numbers too (a far octave slip in a quiet line)
OCT = {"meter": "4/4", "bars": {str(b): {"mel": [[0, 2, "E4"], [2, 2, "G4"]]} for b in range(40, 52)}}
OCT["bars"]["46"] = {"mel": [[0, 2, "E6"], [2, 2, "G6"]]}
OCT["bars"]["47"] = {"mel": [[0, 2, "E6"], [2, 2, "G6"]]}
k = kinds(SN.validate(SN.parse_score_data(OCT)))
assert any(kd == "octave" and b in (46, 47) for kd, b, _ in k), k
try:
    SN.parse_score_data({"bars": {"1": {"text": "x"}}})
    raise SystemExit("no notes must be refused")
except ValueError as e:
    assert "no notes" in str(e)

# ============================================================================= 5. pickup, meter / key / tempo changes, repeats
PU = {"meter": "3/4", "key": "G", "tempo": 100,
      "bars": {"0": {"beats": 1, "mel": [[0, 1, "D5"]]},
               "1": {"mel": [[0, 3, "G5"]]},
               "2": {"meter": "2/4", "tempo": 80, "mel": [[0, 2, "F#5"]]},
               "3": {"key": "C", "mel": [[0, 2, "F5"]], "repeat_start": True},
               "4": {"mel": [[0, 2, "E5"]], "repeat_end": True}}}
sc = SN.parse_score_data(PU)
assert not [i for i in SN.validate(sc) if i["severity"] in ("high", "med")], SN.validate(sc)
assert sc.score_info["pickup"] and sc.measures[0]["implicit"] and sc.measures[0]["num"] == 0
assert [(n["measure"], n["pitch"]) for n in sorted(sc.notes, key=lambda n: n["start_tick"])] == \
    [(0, 74), (1, 79), (2, 78), (3, 77), (4, 76), (3, 77), (4, 76)], [(n["measure"], n["pitch"]) for n in sc.notes]
assert [t[1] for t in sc.tempo_map] == [100.0, 80.0], sc.tempo_map
assert [(n, d) for _, n, d in sc.meter_map][:2] == [(3, 4), (2, 4)], sc.meter_map
inl = SN.to_inline(sc)
assert inl["summary"].get("pickup_offset_bars"), inl["summary"]    # pickup padded: score bar 1 = FL bar 2

# ============================================================================= 6. diff and merge of readings
A = {"meter": "4/4", "key": "C", "bars": {"1": {"mel": [[0, 2, "C5"], [2, 2, "E5"]]},
                                         "2": {"mel": [[0, 4, "G5"]], "bass": [[0, 4, "C3"]]},
                                         "3": {"mel": [[0, 4, "C5"]]}}}
B = {"meter": "4/4", "key": "C", "bars": {"2": {"mel": [[0, 4, "G5"]], "bass": [[0, 4, "C2"]]},
                                         "3": {"mel": [[0, 2, "C5"], [2, 2, "D5"]]},
                                         "4": {"mel": [[0, 4, "C5"]]}}}
d = SN.diff_score_data(A, B)
assert not d["same"] and d["bars_compared"] == 2 and d["bars_differ"] == 2 and d["only_a"] == "1" \
    and d["only_b"] == "4", d
b2 = next(x for x in d["bars"] if x["bar"] == 2)["parts"][0]
assert b2["part"] == "bass" and "(octave)" in b2["changed"][0], b2
b3 = next(x for x in d["bars"] if x["bar"] == 3)["parts"][0]
assert any("length 4 vs 2" in c for c in b3.get("changed", [])) and b3["only_b"] == ["D5@2/2"], b3
assert SN.diff_score_data(A, json.dumps(A))["same"]
merged, rep = SN.merge_score_data([A, B])
assert rep["bars"] == 4 and rep["range"] == "1-4" and rep["overlap_bars"] == "2-3" and len(rep["conflicts"]) == 2, rep
assert merged["bars"]["2"]["bass"] == [[0, 4, "C3"]] and "first" in rep["resolved_by"]
merged, rep = SN.merge_score_data([A, B], prefer=1)
assert merged["bars"]["3"]["mel"] == [[0, 2, "C5"], [2, 2, "D5"]] and rep["resolved_by"] == "reading 1"
# complementary readers (one per instrument) join without conflicts; gaps and header mismatches are reported
C1 = {"meter": "4/4", "key": "C", "bars": {"1": {"mel": [[0, 4, "C5"]]}, "2": {"mel": [[0, 4, "D5"]]}}}
C2 = {"meter": "3/4", "key": "C", "bars": {"1": {"parts": {"cello": [[0, 4, "C3"]]}}, "4": {"mel": [[0, 4, "E5"]]}}}
merged, rep = SN.merge_score_data([C1, C2])
assert not rep["conflicts"] and merged["bars"]["1"]["parts"]["cello"] == [[0, 4, "C3"]], (rep, merged)
assert rep["missing"] == "3" and any("meter" in x for x in rep["meta"]), rep
assert rep["parts_in_one_reading_only"] == {"1": ["cello"]}, rep

# ============================================================================= 7. sheet_import(score=...) + tools
r = SH.sheet_import(score=TUNE, dry_run=True)
assert r["written"] is False and r["source"] == "score data (inline text)" and "inline" not in r, r
assert r["score_data"]["bars"] == "1-3" and r["score_data"]["beat_check"]["full"] == 6, r["score_data"]
assert r["summary"]["voices"]["piano"]["notes"] == 9 and r["summary"]["voices"]["mel"]["notes"] == 11, r["summary"]
assert "overlapping_lines" in r["score_data"] and "abc" not in r["score_data"]
assert "abc" in SH.sheet_import(score=TUNE, dry_run=True, full=True)["score_data"]
r = SH.sheet_import(score=BAD, dry_run=True)
assert r["blocked"] and [x["bar"] for x in r["reread"]][:3] == [29, 30, 31], r["reread"]
assert "score data" in r["next"]
# bar -> page/system from the data itself
PG = json.loads(json.dumps(BAD)); PG["bars"]["29"]["page"] = 4; PG["bars"]["29"]["system"] = 2
r = SH.sheet_import(score=PG, dry_run=True)
assert r["reread"][0]["where"] == "page 4 system 2", r["reread"][0]
# instrument-named parts are range checked (a hint: flute below its range = octave misread?)
FL_ = {"bars": {"1": {"flute": [[0, 4, "C3"]]}, "2": {"flute": [[0, 4, "C5"]]}}}
r = SH.sheet_import(score=FL_, dry_run=True)
assert any("flute" in x and "C3" in x for x in r["score_data"].get("outside_instrument_range", [])), r["score_data"]
# a .json file, MIDI
jp = os.path.join(tmp, "reading.json")
with open(jp, "w") as f:
    json.dump(TUNE, f)
mp = os.path.join(tmp, "out", "tune.mid")
r = SH.sheet_import(score=jp, output_mid=mp)
assert r["written"] and os.path.isfile(mp) and r["source"] == "score data reading.json", r
raw = open(mp, "rb").read()
assert raw[:4] == b"MThd" and struct.unpack(">H", raw[10:12])[0] == 3     # conductor + mel + piano
# into a project: ONE edit, own bar numbers, unmapped parts left out
FIX = os.path.join(HERE, "corpus", "base.flp")
reloads = []


def fake_wop(project, output, fn, _pending_ok=True):
    if m._EDIT_DEPTH:
        return fn()
    reloads.append(output); return fn()


m._with_open_project = fake_wop
proj = os.path.join(tmp, "song.flp"); shutil.copy(FIX, proj)
LATE = {"title": "Late Part", "meter": "4/4", "key": "C", "tempo": 72,
        "bars": {str(b): {"section": "B" if b < 31 else "C", "chord": "C",
                          "mel": [[0, 2, "E5"], [2, 2, "G5"]], "bass": [[0, 4, "C3"]],
                          "perc": [[0, 4, "x-note"]]} for b in range(29, 33)}}
LATE["bars"]["29"]["parts"] = {"strings": [[0, 4, "G3"], [0, 4, "C4"]]}
r = SH.sheet_import(score=LATE, project=proj, channel={"mel": 0, "bass": 1})
assert r["written"] and len(reloads) == 1, (r, reloads)
assert "left_out" in r["score_data"] and "strings" in r["score_data"]["left_out"], r["score_data"]
assert r["score_data"]["placement"].startswith("bar 29 starts at FL bar 29"), r["score_data"]
pats = {p["name"]: p for p in m.flp_patterns(proj)}
assert pats["Late Part - mel"]["placed_at_bars"] == [29.0], pats["Late Part - mel"]
assert pats["Late Part - mel"]["notes_per_channel"] == {0: 8} and pats["Late Part - bass"]["notes_per_channel"] == \
    {1: 4}, pats
j = flh_store.journal_get(proj)["sheet"]
assert j["source"] == "score data" and j["score_bars"] == "29-32" and j["sections"] == [[29, "B"], [31, "C"]], j
assert j["chords"].startswith("29:C 30:C"), j["chords"]
r = SH.sheet_import(score=LATE, project=proj, channel={"mel": 2}, parts=["mel", "strings"])
assert [p["voice"] for p in r["patterns"]] == ["mel"] and r["notes"], r     # strings: no channel -> not written
# through the dispatcher
r = call("sheet_import", score=json.dumps(TUNE), dry_run=True)
assert "_error" not in r and r["score_data"]["parts"], r
r = call("sheet_explain", score=TUNE)
assert any("4/4" in s for s in r["explanation"]), r
r = call("sheet_diff", a=A, b=B)
assert r["bars_differ"] == 2 and r["a"] == "score data", r
r = call("sheet_diff", a=jp, b=TUNE)
assert r["same"], r
# sheet_merge: a folder of page-range readings -> one file, one bar per line; conflicts block the write
rd = os.path.join(tmp, "readers"); os.makedirs(rd)
P1 = {"format": "flh-score/1", "meter": "4/4", "key": "C", "tempo": 72,
      "bars": {"1": {"mel": [[0, 4, "C5"]]}, "2": {"mel": [[0, 4, "D5"]]}}}
P2 = {"format": "flh-score/1", "meter": "4/4", "key": "C",
      "bars": {"3": {"mel": [[0, 4, "E5"]]}, "4": {"mel": [[0, 2, "F5"], [2, 3, "G5"]]}}}
for nm, dd in (("score_p1-2.json", P1), ("score_p3.json", P2)):
    with open(os.path.join(rd, nm), "w") as f:
        json.dump(dd, f)
out = os.path.join(rd, "score_merged.json")
r = call("sheet_merge", readings=rd, output=out)
assert r["written"] and r["bars"] == 4 and r["bars_by_reading"] == ["1-2", "3-4"], r
assert r["check"]["blocking"] >= 1 and r["check"]["reread"][0]["bar"] == 4, r["check"]   # bar 4 overflows
txt = open(out).read()
assert txt.count('\n    "') == 4 and json.loads(txt)["tempo"] == 72, txt
assert "sheet_import" in r["next"]
r = call("sheet_merge", readings=[A, B], output=os.path.join(tmp, "ab.json"))
assert r["written"] is False and r["conflicts"] and "prefer" in r["why"] and not os.path.exists(
    os.path.join(tmp, "ab.json")), r
r = call("sheet_merge", readings=[A, B], output=os.path.join(tmp, "ab.json"), prefer=1)
assert r["written"], r
r = call("sheet_merge", readings=[A, B], output=os.path.join(tempfile.gettempdir(), "outside_roots_merge.json"),
         prefer=0)
assert "_error" in r, r                                       # writes only inside FLH_ROOTS
r = call("sheet_merge", readings=[P1])
assert r["merged"]["bars"]["2"] == {"mel": [[0, 4, "D5"]]}, r
assert m.tool_group("sheet_merge") == "sheet"
# the example in docs/sheet_music.md is valid score data
doc = open(os.path.join(os.path.dirname(HERE), "docs", "sheet_music.md"), encoding="utf-8").read()
ex = json.loads(doc.split("```json", 1)[1].split("```", 1)[0])
r = SH.sheet_import(score=ex, dry_run=True, strict=True)
assert not r.get("blocked") and r["score_data"]["beat_check"]["full"] == r["score_data"]["beat_check"]["lines"], r
sc = SN.parse_score_data(ex)
assert [n["vel"] for n in sc.notes if n["measure"] == 2][0] < 80                # dyn p
assert sc.voices[[v.name for v in sc.voices].index("cello")].program == 42
sch = ns["TOOLS"]["sheet_import"][2]["properties"]
assert "score" in sch and "parts" in sch
assert "score" in m.ESSENTIAL_PARAMS["sheet_import"] and len(m.SHORT_DESC["sheet_import"]) <= 250
print("ok")
