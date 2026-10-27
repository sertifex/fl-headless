"""flp_compare_midi (flh_score): a song built from a source MIDI with the server's own writers (a new 4-bar intro, a
source loop played once, notes dropped and replaced by a repeated 'one finger' stand-in, a near-duplicate strike,
humanized timing in one section, a quiet melody in another, a tempo ramp, a 2-bar pattern placed twice whose
placements need different edits). The report must find all of it; restore must bring missing to 0 without editing a
shared pattern blindly; remove must delete stand-ins / duplicates; writes are backed up and go through the
open-project guard."""
import os, random, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_score as SC  # noqa: E402
ns = vars(m)
flh_store.register(ns); SC.register(ns)
m.fl_running = lambda: False
assert "flp_compare_midi" in m.TOOLS and m._OPEN_PROJECT_GUARD["flp_compare_midi"] == ("project", "output")
BASE = os.path.join(HERE, "corpus", "base.flp")
P = 96; BAR = 4 * P; DIV = 480; F = DIV // P                      # song PPQ 96, source MIDI PPQ 480
rnd = random.Random(7)

# ---------------------------------------------------------------- the source: 28 bars, bars 1-4 one looped bar
ROOTS = [48, 45, 41, 43, 40, 47, 45, 41, 43, 48, 40, 45, 47, 43, 41, 48, 45, 40, 43, 47, 41, 45, 48, 43, 40, 47, 40, 47]
mel_of = {}


def bar_notes(k):
    """[(tick in bar, len, key, vel, part)] of source bar k (0-based); part 'acc' or 'mel'."""
    kk = 0 if k < 4 else k                                        # bars 1-4: the same bar (a loop)
    if kk in (26, 27):
        kk -= 2                                                   # bars 27-28 repeat 25-26 (+1 extra note below)
    r = ROOTS[kk]; out = [(0, 2 * P, r, 70, "acc"), (2 * P, 2 * P, r + 7, 70, "acc")]
    for b in range(4):
        out += [(b * P, P, r + 16, 70, "acc"), (b * P, P, r + 19, 70, "acc")]
    if kk not in mel_of:
        g = random.Random(1000 + kk); mel_of[kk] = [g.choice([72, 74, 76, 77, 79, 81, 83, 84]) for _ in range(8)]
    out += [(i * P // 2, P // 2, mel_of[kk][i], 100, "mel") for i in range(8)]
    if k == 27:
        out.append((3 * P + P // 2, P // 2, 90, 100, "mel"))     # only in the 2nd repeat: placements differ
    return out


NB = 28
src = []                                                          # (tick in song PPQ, len, key, vel, part, bar)
for k in range(NB):
    src += [(k * BAR + t, ln, key, v, part, k) for t, ln, key, v, part in bar_notes(k)]
midi = os.path.join(tmp, "source.mid")
tempo = [(0, b"\xff\x51\x03" + (60_000_000 // 90).to_bytes(3, "big")), (0, b"\xff\x58\x04\x04\x02\x18\x08")]
acc_ev, mel_ev = [], []
for t, ln, key, v, part, _ in src:
    ch = 0 if part == "acc" else 1; evs = acc_ev if part == "acc" else mel_ev
    evs += [(t * F, bytes([0x90 | ch, key, v])), ((t + ln) * F, bytes([0x80 | ch, key, 0]))]
m.midi_write(midi, DIV, [("tempo", tempo), ("accomp", acc_ev), ("melody", mel_ev)])

# ---------------------------------------------------------------- the song (channels 1 = accompaniment, 2 = melody)
ACC, MEL = 1, 2
SHIFT = 1                                                         # source bar k >= 4 plays at song bar k + 1 (0-based)
dropped = []                                                      # (song tick, key) the song lost
stand_in_key = 51                                                 # in no source chord


def song_bar_notes(k):
    """Song notes (pattern-relative to the bar start) for source bar k, with the edits."""
    out = []
    for t, ln, key, v, part in bar_notes(k):
        if k == 27 and key == 90:
            continue                                              # the shared pattern can't hold it (2nd placement)
        sb = (k + SHIFT) if k >= 4 else 4
        if 7 <= k <= 9 and part == "acc" and key in (ROOTS[k] + 16, ROOTS[k] + 19) and t > 0:
            dropped.append((sb * BAR + t, key)); continue         # inner voices gone on beats 2-4
        if k == 21 and part == "mel" and t in (P // 2, 3 * P // 2, 5 * P // 2):
            dropped.append((sb * BAR + t, key)); continue         # three melody notes gone
        if k in (24, 26) and part == "mel" and t == 3 * P:
            if k == 24:
                dropped.append((sb * BAR + t, key))
            continue                                              # gone from the shared pattern (both placements)
        ch = ACC if part == "acc" else MEL
        if 19 <= k <= 23 and part == "mel":
            v = 55                                                # C: the melody under the accompaniment
        out.append((t, ln, ch, key, v))
    if k in (8, 9):
        out += [(i * P // 2, P // 2 - 4, ACC, stand_in_key, 64) for i in range(8)]   # one finger, every 8th
    return out


def pattern(ks, humanize=False):
    notes = []
    for i, k in enumerate(ks):
        for t, ln, ch, key, v in song_bar_notes(k):
            j = rnd.randint(-6, 6) if humanize else 0
            notes.append({"pos": max(0, i * BAR + t + j), "len": ln, "ch": ch, "key": key, "vel": v})
    return notes


pats = [("Intro", [{"pos": b * BAR, "len": BAR, "ch": ACC, "key": 55, "vel": 60} for b in range(4)], [(1, 4)]),
        ("A", pattern([3] + list(range(4, 11))), [(5, 8)]),       # song bars 5-12: the loop bar, source 5-11
        ("B", pattern(range(11, 19), humanize=True), [(13, 8)]),  # song 13-20 = source 12-19, human timing
        ("C", pattern(range(19, 24)), [(21, 5)]),                 # song 21-25 = source 20-24
        ("R", pattern([24, 25]), [(26, 2), (28, 2)])]             # song 26-27 and 28-29 = source 25-26 and 27-28
dup = next(n for n in pats[3][1] if n["ch"] == MEL and n["pos"] == 2 * BAR + P)
pats[3][1].append(dict(dup, pos=dup["pos"] + 4))                  # a near-duplicate strike (0.04 beat later)
song = os.path.join(tmp, "song.flp")
shutil.copy(BASE, song)
d = m._load(song); d = m._write_pattern_notes(d, m._events(d), 1, [])   # the fixture's own pattern: emptied
for name, notes, places in pats:
    d, pid = m._add_pattern(d, name)
    d = m._write_pattern_notes(d, m._events(d), pid, notes)
    track = m._free_track(d)
    for bar, n in places:
        d = m._place_pattern(d, pid, (bar - 1) * BAR, n * BAR, track)
m._save(d, song)
m.flp_tempo_write(song, song, points=[[1, 90], [13, 94], [14, 98], [15, 102], [16, 106], [20, 90]])
miss_expected = len(dropped) + 2                                  # the shared drop counts in both placements + key 90
assert miss_expected == 18 + 3 + 2 + 1, (len(dropped), miss_expected)

# ---------------------------------------------------------------- report
cmp_ = SC.flp_compare_midi
before = open(song, "rb").read()
r = m.TOOLS["flp_compare_midi"][0](project=song, midi=midi)      # through the tool entry: read-only
assert open(song, "rb").read() == before and "write" not in r
print("summary:", r["summary"])
print("bar map:", r["bar_map"])
assert r["bar_map"] == ["MIDI 1-4 -> song 5 (loop)", "MIDI 5-28 -> song 6-29"], r["bar_map"]
assert r["song_bars_without_source"][0]["bars"] == "1-4", r.get("song_bars_without_source")
c = r["counts"]
assert c["missing"] == miss_expected, (c, r["missing"])
assert c["new"] == 16 + 1, (c, r["new"])                          # 16 stand-in strikes + 1 duplicate
assert {x["bar"] for x in r["missing"]} >= {10, 11, 23, 26, 28, 29}, r["missing"]
top = r["missing"][0]; assert top["n"] == 6 and top["section"] == "A", top
assert "@2" in top["notes"] and "@3" in top["notes"] and "@4" in top["notes"], top
assert r["missing_by_section"]["A"] == 18, r["missing_by_section"]
assert len(r["stand_ins"]) == 1, r["stand_ins"]
si = r["stand_ins"][0]
assert si["ch"] == ACC and si["key"] == SC.nn(stand_in_key) and si["strikes"] == 16 and si["bars"] == "10-11", si
assert si["missing_near"] >= 6, si
assert len(r["near_duplicates"]) == 1 and r["near_duplicates"][0]["bar"] == 23, r["near_duplicates"]
tm = r["tempo"]
assert not tm["steady"] and tm["min_bpm"] == 90 and tm["max_bpm"] == 106, tm
assert any("ramp" in x and x.startswith("bars 13-16") for x in tm["changes"]), tm
assert any("jump" in x and "bar 20" in x for x in tm["changes"]), tm
assert tm["source"] == "steady 90 BPM" and "source is steady" in tm["verdict"], tm
tsec = {x["section"]: x for x in r["timing"]["sections"]}
assert tsec["A"]["spread_ms"] < 1 and tsec["A"]["chords_split"] == 0, tsec["A"]
assert tsec["B"]["spread_ms"] > 15 and tsec["B"]["chords_split"] > 0 and tsec["B"]["feel"] != "tight (quantized-sounding)", \
    tsec["B"]
vsec = {x["section"]: x for x in r["velocity"]["sections"]}
assert vsec["A"]["melody_above"] is True and vsec["C"]["melody_above"] is False, vsec
assert vsec["C"]["source_melody_over_accomp"] > 0, vsec["C"]
assert any("isn't above" in x for x in r["summary"]), r["summary"]
assert any(x.startswith("chords split apart") and "B (" in x for x in r["summary"]), r["summary"]
full = cmp_(song, midi, full=True)
assert len(full["stand_ins"][0]["ids"]) == 16 and all(SC.CMP_ID.match(x) for x in full["stand_ins"][0]["ids"])
assert len(str(r)) < 9000, len(str(r))                            # compact by default

# ---------------------------------------------------------------- restore, shared='report': the shared pattern's
# differing edit is reported, not written into both placements
out1 = os.path.join(tmp, "restored_report.flp")
w = cmp_(song, midi, restore=True, output=out1)["write"]
print("restore (report):", {k: w[k] for k in ("restored", "after", "shared_patterns_not_edited")})
assert w["after"]["missing"] == 1 and w["restored"] == miss_expected - 1, w
assert w["after"]["stand_in_runs"] == 0 and w["after"]["repeated_new_runs"] == 1 and "repeated_new" in w["hint"], w
sh = w["shared_patterns_not_edited"]
assert len(sh) == 1 and sh[0]["name"] == "R" and sh[0]["edits_skipped"] == 1 and "split" in sh[0]["fix"], sh
d1 = m._load(out1); p1 = {v["name"]: v for v in m._patterns(d1, m._events(d1)).values()}
assert not any(r_[4] == 90 for r_ in p1["R"]["notes"]), "the 2nd placement's note must not go into both"
mel_restored = [r_ for r_ in p1["R"]["notes"] if r_[0] == 3 * P and r_[2] == MEL]
assert len(mel_restored) == 1, mel_restored                       # the note both placements lack: written once
inner = [r_ for i in (4, 5, 6) for r_ in p1["A"]["notes"] if r_[2] == ACC and i * BAR + P <= r_[0] < (i + 1) * BAR
         and r_[0] % P == 0 and r_[4] in (ROOTS[3 + i] + 16, ROOTS[3 + i] + 19)]
assert len(inner) == 3 * 6, len(inner)                            # beats 2-4 of A's bars 5-7 (source 8-10): both voices
assert all(r_[11] == 70 for r_ in inner), {r_[11] for r_ in inner}  # velocity from matched neighbours
cmel = [r_ for r_ in p1["C"]["notes"] if r_[2] == MEL and 2 * BAR <= r_[0] < 3 * BAR]
assert len(cmel) == 8 + 1 and all(r_[11] == 55 for r_ in cmel), cmel   # quiet like its neighbours (+ the dup)
assert open(song, "rb").read() == before, "output elsewhere: the project itself is untouched"

# ---------------------------------------------------------------- restore + remove stand-ins, shared='split'
out2 = os.path.join(tmp, "restored_split.flp")
res2 = cmp_(song, midi, restore=True, remove=["stand_ins"], shared="split", output=out2)
w2 = res2["write"]
print("restore (split):", {k: w2[k] for k in ("restored", "removed", "split_patterns", "after")})
assert w2["after"]["missing"] == 0 and w2["after"]["stand_in_runs"] == 0 and w2["after"]["new"] == 1, w2
assert w2["removed"] == 16 and len(w2["split_patterns"]) == 1 and w2["split_patterns"][0]["name"].startswith("R"), w2
d2 = m._load(out2); ev2 = m._events(d2)
placed = [struct.unpack("<H", rr[6:8])[0] - m.PATTERN_BASE for _, _, rr in m._clips(d2, ev2)]
pn = {pid: v["name"] for pid, v in m._patterns(d2, ev2).items()}
r_pids = [pid for pid in placed if pn.get(pid, "").startswith("R")]
assert len(r_pids) == 2 and len(set(r_pids)) == 2, (r_pids, pn)  # each placement its own pattern now
p2 = m._patterns(d2, ev2)
assert sum(1 for pid in r_pids for r_ in p2[pid]["notes"] if r_[4] == 90) == 1  # only in the 2nd placement

# ---------------------------------------------------------------- remove one near-duplicate by id
dup_id = r["near_duplicates"][0]["drop"]
w3 = cmp_(out2, midi, remove=[dup_id])["write"]
assert w3["removed"] == 1 and w3["after"]["new"] == 0 and w3["after"]["near_duplicates"] == 0, w3
# deleting a note of a pattern placed twice, asked for at one placement only: reported, not deleted from both
ds = m._load(song)
r_note = next(n for n in m._song_x(ds, m._events(ds))[0] if n[2] == MEL and 25 * BAR <= n[0] < 26 * BAR)
w6 =cmp_(song, midi, remove=[f"n{r_note[0]}.{MEL}.{r_note[3]}"], output=os.path.join(tmp, "del_shared.flp"))["write"]
assert w6["removed"] == 0 and w6["shared_patterns_not_edited"][0]["name"] == "R" and not w6.get("written"), w6
try:
    cmp_(song, midi, remove=["everything"]); raise SystemExit("bad remove arg accepted")
except ValueError:
    pass

# ---------------------------------------------------------------- in place through the tool: guarded, backed up
m._CALL_BACKUPS.clear()                                           # a new tool call
n_before = len(m._versions(song))
w4 = m.TOOLS["flp_compare_midi"][0](project=song, midi=midi, restore=True, remove=["stand_ins", "near_duplicates"],
                                    shared="split")["write"]
assert w4["output"] == song and w4["written"] and w4["after"] == {
    "missing": 0, "new": 0, "stand_in_runs": 0, "repeated_new_runs": 0, "near_duplicates": 0}, w4
assert len(m._versions(song)) == n_before + 1 and w4.get("backup") and "flp_restore" in w4["undo"], w4
assert open(w4["backup"], "rb").read() == before, "the backup is the project as it was"
again = cmp_(song, midi)
assert again["counts"]["missing"] == 0 and not again["stand_ins"] and not again["near_duplicates"], again["counts"]
w5 = cmp_(song, midi, restore=True)["write"]
assert w5.get("note") == "nothing to write" and not w5.get("written") and w5["restored"] == 0, w5   # idempotent: nothing duplicated

# ---------------------------------------------------------------- a played source and a humanized song (regression:
# a real rework came out as '189 of 591 missing' with many 'not found' bars). The source: unquantized (about half the
# notes off the grid), meter changes (3/4, 2/4 bars) the song doesn't have, 4 identical opening bars, notes doubled on
# two tracks. The song: the same notes 4 bars later in plain 4/4, humanized onsets (up to +-0.15 beat) and velocities,
# rubato tempo (a slow-down every 2 bars, then a jump back), half the chord notes dropped in two bars.
g = random.Random(11)
METERS = [(4, 4)] * 12 + [(3, 4)] + [(4, 4)] * 3 + [(3, 4)] + [(4, 4)] * 13 + [(2, 4)] + [(4, 4)] * 5
src2, ts_ev, beat = [], [], 0                                     # (beat, len beats, key, vel, track)
for i, (num, den) in enumerate(METERS):
    if i == 0 or METERS[i - 1] != (num, den):
        ts_ev.append((beat * DIV, bytes([0xFF, 0x58, 4, num, 2, 24, 8])))
    if i < 4:                                                     # the heartbeat: one bar four times
        src2 += [(beat, 1, 40, 70, 0), (beat + 0.75, 0.25, 40, 60, 0), (beat + 2, 1, 40, 70, 0)]
    else:
        root = g.choice([38, 40, 41, 43, 45, 47])
        src2.append((beat, num, root, 70, 0))
        for b_ in range(num):
            src2 += [(beat + b_, 1, root + 16, 64, 0), (beat + b_, 1, root + 19, 64, 0)]
        src2 += [(beat + j_ / 2, 0.5, g.choice([64, 67, 69, 71, 72, 74, 76]), 90, 1) for j_ in range(num * 2)]
    beat += num
TOTAL_BEATS = beat
for bt in (24, 80):                                               # two tracks strike the same note together
    src2.append(next(s for s in src2 if s[0] == bt and s[4] == 0 and s[2] > 50)[:4] + (1,))
jit = {}
for i, s in enumerate(src2):                                      # about half the onsets off the grid
    jit[i] = g.randint(-12, 12) if g.random() < 0.5 else 0
ev0, ev1 = [], []
for i, (bt, ln, key, v, trk) in enumerate(src2):
    t0 = max(0, int(round(bt * DIV)) + jit[i]); (ev1 if trk else ev0).extend(
        [(t0, bytes([0x90 | trk, key, v])), (t0 + int(ln * DIV) - 10, bytes([0x80 | trk, key, 0]))])
midi2 = os.path.join(tmp, "played.mid")
m.midi_write(midi2, DIV, [("tempo", [(0, b"\xff\x51\x03" + (1_000_000).to_bytes(3, "big"))] + ts_ev),
                          ("left", ev0), ("right", ev1)])
seen2, notes2, drop2 = set(), [], 0
for i, (bt, ln, key, v, trk) in enumerate(src2):
    if (bt, key) in seen2:
        continue                                                  # the doubled note: once in the song
    seen2.add((bt, key))
    if (32 <= bt < 36 or 84 <= bt < 88) and trk == 0 and key > 50 and int(bt) % 2:
        drop2 += 1; continue                                      # half the chord notes of two bars gone
    tick = int(round((bt + 16) * P + jit[i] / F)) + g.randint(-14, 14)
    notes2.append({"pos": max(0, tick), "len": int(ln * P) - 2, "ch": MEL if trk else ACC, "key": key,
                   "vel": max(1, min(127, v + g.randint(-8, 8)))})
notes2 += [{"pos": (84 + 16) * P + 48 * k_, "len": 40, "ch": ACC, "key": 49 + k_, "vel": 60} for k_ in range(3)]
song2 = os.path.join(tmp, "played_song.flp")
shutil.copy(BASE, song2)
d = m._load(song2); d = m._write_pattern_notes(d, m._events(d), 1, [])
d, pid = m._add_pattern(d, "Intro")
d = m._write_pattern_notes(d, m._events(d), pid, [{"pos": b_ * BAR, "len": BAR, "ch": ACC, "key": 52, "vel": 50}
                                                   for b_ in range(4)])
d = m._place_pattern(d, pid, 0, 4 * BAR, m._free_track(d))
d, pid = m._add_pattern(d, "Piece")
d = m._write_pattern_notes(d, m._events(d), pid, [dict(n, pos=n["pos"] - 4 * BAR) for n in notes2])
d = m._place_pattern(d, pid, 4 * BAR, (TOTAL_BEATS // 4 + 1) * BAR, m._free_track(d))
m._save(d, song2)
rubato = [pt for b_ in range(1, TOTAL_BEATS // 4 + 6, 2) for pt in ([b_, 62], [b_ + 0.5, 60], [b_ + 1, 58],
                                                                     [b_ + 1.5, 56])]
m.flp_tempo_write(song2, song2, points=rubato)
r2 = cmp_(song2, midi2)
print("played:", r2["summary"][:2], r2["bar_map"], r2["counts"])
nb = -(-TOTAL_BEATS // 4)
assert r2["bar_map"] == [f"MIDI 1-{nb} -> song 5-{nb + 4}"], r2["bar_map"]
assert "midi_bars" in r2 and "3/4" in r2["midi_bars"], r2.get("midi_bars")
doubled = len(src2) - len({(s[0], s[2]) for s in src2})          # the 2 above + a hand doubling the other's key
assert r2["counts"]["missing"] == drop2 and r2["counts"]["source_doubled_notes_merged"] == doubled >= 2, \
    (r2["counts"], drop2, doubled)
assert not r2.get("source_bars_not_found") and r2["counts"]["new"] == 3, r2
assert {x["bar"] for x in r2["missing"]} == {13, 26}, r2["missing"]    # MIDI bars 9 and 22, 4 bars later
assert r2["timing"]["source_on_grid_pct"] < 80 and "note" in r2["timing"], r2["timing"]
t2 = r2["timing"]["sections"][0]                                 # +-0.15 beat at ~60 BPM: up to +-150 ms
assert t2["feel"] == "sloppy" and 60 < t2["spread_ms"] < 120 and t2["chords_split"] > t2["chords"] // 2, t2
assert any(x.startswith("timing loose / sloppy in Piece") for x in r2["summary"]), r2["summary"]
assert "rubato" in r2["tempo"]["verdict"] and "gradual slow-down" in r2["tempo"]["sections"][-1]["tempo"], r2["tempo"]
assert all(v["melody_above"] for v in r2["velocity"]["sections"] if "melody_above" in v), r2["velocity"]
w7 = cmp_(song2, midi2, restore=True, output=os.path.join(tmp, "played_restored.flp"))["write"]
assert w7["restored"] == drop2 and w7["after"]["missing"] == 0, w7
shutil.rmtree(tmp, True)
print("ok")
