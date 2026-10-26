"""flh_rise (flp_build_rise): the orchestral build into a climax, FL simulated, projects built with the server's own
writers from tests/corpus/base.flp. Ladder chords spelled right in minor / major keys; segments on bars in 4/4 and
3/4 (beats when the build is shorter than the ladder); a B minor 4/4 piano + strings + timpani + choir + celli song at
60 BPM: dry_run writes nothing, then ONE batch - the accompaniment on the ladder (pitch classes of each chord, no
semitone rub against the melody), timpani hits tuned, an accelerando ~55 -> ~65 that returns to 60 on the
downbeat, a crescendo (first beat avg + 25 <= last beat avg, variation kept), the melody untouched, everything
outside the build untouched, a timpani roll peaking ON the downbeat, celli held from it, the journal note, a second
build refused, undo restores the exact file; a 3/4 D minor song with no timpani / celli: the reversed swell ends on
the downbeat, a low-strings channel is made (a copy of the strings when no instrument is owned), 3-beat bars; a 6/8
Eb major song rolls on its toms channel (the drum fallback) into the downbeat; a piano-only song says clearly what
it skipped and keeps its top voice (the melody) as written."""
import json, os, random, shutil, statistics, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702  (leave no temp files behind)
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
import flh_compose as C  # noqa: E402
if "flp_compose" not in ns["TOOLS"]:                     # the server loads it (EXTENSIONS); else register here
    import flh_store; flh_store.register(ns)
    import flh_score as SC; SC.register(ns)
    C.register(ns)
import flh_rise as R  # noqa: E402
if "flp_build_rise" not in ns["TOOLS"]:
    R.register(ns)
import flh_store  # noqa: E402

# ----------------------------------------------------------------------------- pure music
names = lambda k: [c["name"] for c in R.ladder_chords(k)]
assert names("B minor") == ["A#dim7", "Edim7", "C#dim7", "F#sus4"], names("B minor")
assert names("D minor") == ["C#dim7", "Gdim7", "Edim7", "Asus4"], names("D minor")
assert names("F# minor")[0] == "E#dim7" and names("F# minor")[-1] == "C#sus4"
assert names("C major") == ["Bdim7", "Ddim7", "F#dim7", "Gsus4"], names("C major")
lb = R.ladder_chords("B minor")
assert lb[0]["pcs"] == [1, 4, 7, 10] and lb[-1]["pcs"] == [1, 6, 11]          # A# C# E G ; F# B C#
assert [c["root"] for c in lb] == [10, 4, 1, 6]
P = 96
assert R.segments([0, 384, 768, 1152], list(range(0, 1536, 96)), 1536, 4) == [(0, 384), (384, 768), (768, 1152),
                                                                              (1152, 1536)]
assert R.segments([0, 288, 576, 864], list(range(0, 1152, 96)), 1152, 4)[-1] == (864, 1152)   # 3/4 bars
s2 = R.segments([0, 288], list(range(0, 576, 96)), 576, 4)                    # 2 bars of 3/4: on beats
assert len(s2) == 4 and all(a % 96 == 0 for a, _ in s2) and s2[-1][1] == 576, s2
ramp = R.tempo_ramp(list(range(0, 1536, 96)), 60.0, 0.083, 1.0)
bp = [b for _, b in ramp]
assert all(x < y for x, y in zip(bp, bp[1:])) and 54.5 < bp[0] < 55.5 and 64.5 < bp[-1] < 65.5, bp
rn = R.roll_notes(1536, P, 2.0, 60.0, {"strokes_per_s": 12, "from_vel": 34, "strike_beats": 1}, 107, 47)
assert rn[-1][0] == 1536 and rn[-1][3] == 107 and max(x[3] for x in rn[:-1]) < 107 and rn[0][0] == 1536 - 192
assert all(a[3] <= b[3] for a, b in zip(rn[:-1], rn[1:])), rn            # rising into the strike

# ----------------------------------------------------------------------------- projects (mock FL)
FIX = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIX):
    print("skipped: project tests (no tests/corpus/base.flp)"); print("ok"); sys.exit(0)
m._with_open_project = lambda project, output, fn: fn()
build = ns["TOOLS"]["flp_build_rise"][0]


def blank(name, chans, num=4, den=4, bpm=60):
    p = os.path.join(tmp, name); shutil.copy(FIX, p)
    m.flp_edit_notes(p, [{"op": "delete"}], pattern=1)
    m.flp_playlist(p, "remove", clips=[c["id"] for c in m.flp_playlist(p)["clips"]])
    if (num, den) != (4, 4):
        m.flp_project_info(p, p, time_sig_num=num, time_sig_beat=den)
    m.flp_set_tempo(p, bpm)
    ops = [{"tool": "flp_add_channel", "clone_from": 0} for _ in chans]
    ops.append({"tool": "flp_style_channels", "channels": [{"channel": 6 + i, "name": n} for i, n in enumerate(chans)]})
    m.flp_edit(p, ops)
    return p


def heard(p):
    d = m._load(p); ev = m._events(d)
    return m._song(d, ev)


def semi(a, b):
    return (a - b) % 12 in (1, 11)


# B minor, 4/4, 60 BPM: Bm G D A | Bm G Em F# | Bm G D F# - Verse bars 1-8, Climax 9-12
PIANO, MEL, STR, TIM, CHO, CEL = 6, 7, 8, 9, 10, 11
song = blank("bminor.flp", ["Piano", "Melody", "Strings", "Timpani", "Choir", "Celli"])
PROG = [(35, [59, 62, 66]), (43, [59, 62, 67]), (38, [57, 62, 66]), (45, [57, 61, 64]),
        (35, [59, 62, 66]), (43, [59, 62, 67]), (40, [59, 64, 67]), (42, [58, 61, 66]),
        (35, [59, 62, 66]), (43, [59, 62, 67]), (38, [57, 62, 66]), (42, [58, 61, 66])]
MELN = {0: [74, 73, 71, 66], 1: [71, 74, 79, 78], 2: [78, 76, 74, 73], 3: [73, 76, 73, 69], 4: [74, 76, 78, 79],
        5: [83, 81, 79, 78], 6: [76, 79, 78, 76], 7: [73, 70, 73, 78], 8: [78, 81, 83, 86], 9: [83, 79, 74, 79],
        10: [78, 81, 78, 74], 11: [73, 78, 82, 78]}
rnd = random.Random(5)
rows, mrows, srows, crows, trows = [], [], [], [], []
for b, (bass, tri) in enumerate(PROG):
    t0 = b * 4
    rows.append([PIANO, t0, 3.9, bass, 66 + rnd.randint(-6, 6)])
    rows += [[PIANO, t0 + beat, 0.9, q, 64 + rnd.randint(-8, 8)] for beat in range(4) for q in tri]
    mrows += [[MEL, t0 + i, 0.95, q, 80 + rnd.randint(-5, 5)] for i, q in enumerate(MELN[b])]
    srows += [[STR, t0, 3.95, q - 12, 58 + rnd.randint(-4, 4)] for q in tri]
    if b >= 8:
        crows += [[CHO, t0, 3.95, q, 70] for q in tri]
    if b in (0, 4):
        trows.append([TIM, t0, 1.0, 47, 90])
m.flp_edit(song, [{"tool": "flp_compose_write", "pattern": "Piano", "notes": rows, "place": [[1, 12]], "track": 1},
                  {"tool": "flp_compose_write", "pattern": "Melody", "notes": mrows, "place": [[1, 12]], "track": 2},
                  {"tool": "flp_compose_write", "pattern": "Strings", "notes": srows, "place": [[1, 12]], "track": 3},
                  {"tool": "flp_compose_write", "pattern": "Choir", "notes": crows, "place": [[1, 12]], "track": 4},
                  {"tool": "flp_compose_write", "pattern": "Timpani", "notes": trows, "place": [[1, 12]], "track": 5}])
flh_store.journal_update(song, set={"key": "B minor", "sections": [{"name": "Verse", "bar": 1, "end_bar": 9},
                                                                    {"name": "Climax", "bar": 9, "end_bar": 13}]})
jpath = os.path.splitext(song)[0] + ".journal.json"
before, jbefore = open(song, "rb").read(), open(jpath, "rb").read()
nv0 = len(m.flp_versions(song))
notes0, tempos0 = heard(song)
A, Z = 4 * 4 * P, 8 * 4 * P                               # bar 5 .. bar 9 in ticks

dr = build(project=song, into="Climax", dry_run=True)
assert dr["dry_run"] and open(song, "rb").read() == before and open(jpath, "rb").read() == jbefore, "dry run wrote"
assert not os.path.isdir(os.path.join(tmp, "_fx")) and len(m.flp_versions(song)) == nv0, "dry run made files"
assert dr["into"] == {"section": "Climax", "bar": 9} and dr["build_bars"] == "5-8" and dr["key"] == "B minor", dr
assert dr["harmony"]["ladder"].startswith("A#dim7 (bar 5) -> Edim7 (bar 6) -> C#dim7 (bar 7) -> F#sus4 (bar 8)"), dr
assert dr["roll"]["kind"] == "timpani roll" and dr["low_strings"]["channel"] == "Celli", dr
assert dr["listen"] == "fl_listen(start_bar=4, end_bar=11)" and "would_write" in dr

m._CALL_BACKUPS.clear()                                  # a new tool call (the server clears it)
r = build(project=song, into="Climax")
assert r["did"].startswith("built a rise into Climax (bar 9)") and "undo" in r and "next" in r, r
assert r["clashes"]["counts"]["high"] == 0, r["clashes"]
notes1, tempos1 = heard(song)
# -- harmony: the accompaniment sits on the ladder, never a semitone from a melody note sounding with it
lad = R.ladder_chords("B minor")
mel1 = [n for n in notes1 if n[2] == MEL]
for n in notes1:
    if n[2] in (PIANO, STR) and A <= n[0] < Z:
        seg = (n[0] - A) // (4 * P)
        assert n[3] % 12 in lad[seg]["pcs"], ("off the ladder", n, lad[seg]["name"])
        for k in mel1:
            if n[0] < k[0] + k[1] and k[0] < n[0] + n[1] and n[2] == PIANO and n[3] > 50:
                assert not semi(n[3], k[3]), ("rubs against the melody", n, k)
low = sorted((n for n in notes1 if n[2] == PIANO and A <= n[0] < Z and n[3] < 50), key=lambda n: n[0])
assert [n[3] % 12 for n in low] == [c["root"] for c in lad] or len(low) == 4, low      # bass notes on the ladder
tim_in = [n for n in notes1 if n[2] == TIM and A <= n[0] < Z - 2 * P]
assert tim_in and all(n[3] % 12 in lad[0]["pcs"] for n in tim_in), tim_in      # the bar-5 hit tuned to A#dim7
# -- the melody is untouched (pitch, timing, velocity)
assert sorted(n for n in notes0 if n[2] == MEL) == sorted(mel1)
# -- everything outside the build plays as before (the new roll / celli aside)
out0 = sorted(n for n in notes0 if (n[0] < A or n[0] >= Z) and n[2] != TIM)
out1 = sorted(n for n in notes1 if (n[0] < A or n[0] >= Z) and n[2] not in (TIM, CEL))
assert out0 == out1, "notes outside the build changed"
# -- tempo: up monotonically across the build, back to 60 on the downbeat
tm = sorted(tempos1)
inb = [b for t, b in tm if A <= t < Z]
assert len(inb) == 16 and all(x < y for x, y in zip(inb, inb[1:])), inb
assert 54 <= inb[0] <= 56 and 64 <= inb[-1] <= 66, inb
at = lambda t: [b for x, b in tm if x <= t][-1]
assert at(Z) == 60.0 and at(Z + 3 * 4 * P) == 60.0 and at(A - 1) == 60.0, tm
assert r["tempo"]["from"] == inb[0] and r["tempo"]["back_to"] == 60.0
# -- crescendo on the accompaniment, human variation kept
acc = [n for n in notes1 if n[2] in (PIANO, STR) and A <= n[0] < Z]
fb = [n[4] for n in acc if n[0] < A + P]
lb_ = [n[4] for n in acc if Z - P <= n[0] < Z]
assert statistics.mean(fb) + 25 <= statistics.mean(lb_), (statistics.mean(fb), statistics.mean(lb_))
last_bar = [n[4] for n in acc if n[2] == PIANO and Z - 4 * P <= n[0] < Z and n[3] > 50]
assert statistics.pstdev(last_bar) >= 2 and len(set(last_bar)) >= 4, last_bar            # not flattened
assert "measured" in r["dynamics"] and r["dynamics"]["from"] == 50 and r["dynamics"]["to"] == 95
# -- the roll peaks ON the downbeat
roll = sorted(n for n in notes1 if n[2] == TIM and Z - 2 * P <= n[0] <= Z)
assert len(roll) >= 8 and roll[-1][0] == Z and roll[-1][4] == max(n[4] for n in roll), roll
assert roll[0][4] < roll[len(roll) // 2][4] < roll[-1][4] and all(n[0] < Z for n in roll[:-1])
assert r["roll"]["peak"].startswith("v") and "downbeat of bar 9" in r["roll"]["peak"]
# -- the celli enter sustained on the downbeat
cel = [n for n in notes1 if n[2] == CEL]
assert cel and all(n[0] == Z and n[1] >= 4 * P for n in cel) and {n[3] % 12 for n in cel} == {11}, cel
# -- the melody's arch is a suggestion, not a change
assert r["melody"]["peak_in_last_bar"] is False and "by hand" in r["melody"]["suggest"], r["melody"]
# -- journal
jr = json.load(open(jpath))
assert jr["rises"]["9"]["ladder"] == ["A#dim7", "Edim7", "C#dim7", "F#sus4"], jr["rises"]
assert any("rise into Climax" in x for x in jr["notes"]), jr["notes"]
# -- a second build into the same downbeat is refused; undo restores the exact file
try:
    build(project=song, into="Climax"); raise SystemExit("a second rise must be refused")
except ValueError as e:
    assert "already" in str(e)
vs = m.flp_versions(song)
assert vs, "no backup"
m._CALL_BACKUPS.clear()
m.flp_restore(song, vs[-1]["backup"])
assert open(song, "rb").read() == before, "undo didn't restore the project"
# harmony / tempo off: only the crescendo, roll and celli
r_ = build(project=song, into=9, harmony=False, tempo=False, dry_run=True)
assert r_["harmony"] == {"skipped": "harmony=False"} and r_["tempo"] == {"skipped": "tempo=False"}
assert not any(o["tool"] == "flp_tempo_write" for o in r_["would_write"])
r_ = build(project=song, into="Climax", intensity=0.5, dry_run=True)    # gentler: less push, a shorter roll
assert r_["tempo"]["from"] > 57 and r_["tempo"]["to"] < 63 and r_["roll"]["beats"] == 1.0 and r_["dynamics"]["to"] < 95, r_
try:
    build(project=song, into="Bridge", dry_run=True); raise SystemExit("an unknown section must be refused")
except ValueError as e:
    assert "Climax" in str(e) and "Verse" in str(e), e

# ----------------------------------------------------------------------------- 3/4, D minor, no timpani / celli
PIANO, MEL, STR = 6, 7, 8
w = blank("waltz.flp", ["Piano", "Melody", "Strings"], 3, 4, bpm=72)
PR = [(38, [57, 62, 65]), (46, [58, 62, 65]), (43, [58, 62, 67]), (45, [57, 61, 64])] * 3
MW = [[74, 72, 69], [70, 74, 77], [79, 77, 74], [73, 76, 81]] * 3
rows = []
for b, (bass, tri) in enumerate(PR):
    t0 = b * 3
    rows.append([PIANO, t0, 0.95, bass, 70 + rnd.randint(-6, 6)])
    rows += [[PIANO, t0 + beat, 0.9, q, 60 + rnd.randint(-8, 8)] for beat in (1, 2) for q in tri]
    rows += [[MEL, t0 + i, 0.95, q, 82 + rnd.randint(-4, 4)] for i, q in enumerate(MW[b])]
    rows += [[STR, t0, 2.95, q - 12, 56 + rnd.randint(-4, 4)] for q in tri]
m.flp_edit(w, [{"tool": "flp_compose_write", "pattern": "Waltz", "notes": rows, "place": [[1, 12]], "track": 1}])
flh_store.journal_update(w, set={"key": "D minor"})
assert m.flp_info(w)["time_signature"] == "3/4"
wb = open(w, "rb").read()
n0, _ = heard(w)
A, Z = 4 * 3 * P, 8 * 3 * P
dw = build(project=w, into=9, dry_run=True)
assert dw["meter"] == "3/4" and dw["key"] == "D minor" and open(w, "rb").read() == wb, dw
assert dw["harmony"]["ladder"].startswith("C#dim7 (bar 5) -> Gdim7 (bar 6) -> Edim7 (bar 7) -> Asus4 (bar 8)"), dw
m._CALL_BACKUPS.clear()
r3 = build(project=w, into=9)
n1, t1 = heard(w)
lad = R.ladder_chords("D minor")
for n in n1:
    if n[2] in (PIANO, STR) and A <= n[0] < Z:
        assert n[3] % 12 in lad[(n[0] - A) // (3 * P)]["pcs"], ("off the ladder (3/4)", n)
assert sorted(n for n in n0 if n[2] == MEL) == sorted(n for n in n1 if n[2] == MEL)
inb = [b for t, b in sorted(t1) if A <= t < Z]
assert len(inb) == 12 and all(x < y for x, y in zip(inb, inb[1:])) and inb[0] < 72 < inb[-1], inb
assert [b for t, b in sorted(t1) if t <= Z][-1] == 72.0
acc = [n for n in n1 if n[2] in (PIANO, STR) and A <= n[0] < Z]
assert statistics.mean(n[4] for n in acc if n[0] < A + P) + 25 <= statistics.mean(n[4] for n in acc if n[0] >= Z - P)
assert "new_channel" in r3["low_strings"] and r3["low_strings"]["why"], r3["low_strings"]
lsc = r3["low_strings"]["new_channel"]["channel"]
assert "copy of Strings" in r3["low_strings"]["new_channel"]["sound"], r3["low_strings"]
ls = [n for n in n1 if n[2] == lsc]
assert ls and all(n[0] == Z for n in ls) and {n[3] % 12 for n in ls} == {2}, ls      # D on bar 9's downbeat
chans = C.project_view(w)["channels"]
assert chans[lsc]["name"] == "Low Strings"
jw = json.load(open(os.path.splitext(w)[0] + ".journal.json"))
assert jw["channels"][str(lsc)]["role"] == "bass" and jw["compose"]["channels"]["low strings"] == lsc
if C._fx_mod() is not None:                                # the reversed swell ends ON the downbeat
    assert r3["roll"]["kind"].startswith("reversed-cymbal swell") and "no timpani" in r3["roll"]["why"], r3["roll"]
    sw = [n for n in n1 if chans.get(n[2], {}).get("name", "").startswith("Reverse Swell")]
    assert len(sw) == 1 and sw[0][0] + sw[0][1] == Z, sw
    assert os.path.isfile(os.path.join(tmp, "_fx", r3["roll"]["file"]))
else:
    assert "skipped" in r3["roll"]
vs = m.flp_versions(w)
m._CALL_BACKUPS.clear()
m.flp_restore(w, vs[-1]["backup"])
assert open(w, "rb").read() == wb
# a short build (2 bars of 3/4): the ladder on beats
d2 = build(project=w, into=9, bars=2, dry_run=True)
assert d2["build_bars"] == "7-8" and "beat" in d2["harmony"]["ladder"], d2["harmony"]

# ----------------------------------------------------------------------------- 6/8, Eb major, a toms channel (the drum fallback)
cm = blank("compound.flp", ["Piano", "Lead", "Toms"], 6, 8, bpm=90)
rows = []
for b in range(12):
    t0 = b * 3.0
    rows.append([6, t0, 2.9, 39 + (b % 3) * 2, 70])
    rows += [[6, t0 + i * 0.5, 0.45, 63 + (i % 3) * 4, 60 + rnd.randint(-8, 8)] for i in range(6)]
    rows += [[7, t0 + i * 1.5, 1.4, 79 + (b + i) % 3, 80] for i in range(2)]
m.flp_edit(cm, [{"tool": "flp_compose_write", "pattern": "Six", "notes": rows, "place": [[1, 12]], "track": 1}])
flh_store.journal_update(cm, set={"key": "Eb major"})
c0, _ = heard(cm)
A, Z = 4 * 3 * P, 8 * 3 * P
m._CALL_BACKUPS.clear()
rc = build(project=cm, into=9, low_strings=False)
assert rc["meter"] == "6/8" and rc["harmony"]["ladder"].startswith("Ddim7 (bar 5) -> Fdim7 (bar 6) -> Adim7 (bar 7) "
                                                                    "-> Bbsus4 (bar 8)"), rc["harmony"]
assert rc["did"].startswith("built a rise into bar 9 over bars 5-8 in Eb major"), rc["did"]
c1, tc = heard(cm)
lad = R.ladder_chords("Eb major")
assert all(n[3] % 12 in lad[(n[0] - A) // (3 * P)]["pcs"] for n in c1 if n[2] == 6 and A <= n[0] < Z)
assert sorted(n for n in c0 if n[2] == 7) == sorted(n for n in c1 if n[2] == 7)               # the melody kept
inb = [b for t, b in sorted(tc) if A <= t < Z]
assert len(inb) == 8 and all(x < y for x, y in zip(inb, inb[1:])) and [b for t, b in sorted(tc) if t <= Z][-1] == 90.0
assert rc["roll"]["kind"] == "soft low-drum roll" and rc["roll"]["channel"] == "Toms", rc["roll"]
toms = sorted(n for n in c1 if n[2] == 8)
assert toms[-1][0] == Z and toms[-1][4] == max(n[4] for n in toms) and toms[0][0] == Z - 2 * 3 * P // 2, toms
assert rc["low_strings"] == {"skipped": "low_strings=False"}

# ----------------------------------------------------------------------------- piano only: said clearly
solo = blank("solo.flp", ["Piano"])
rows = []
for b, (bass, tri) in enumerate(PROG):
    rows.append([6, b * 4, 3.9, bass, 64])
    rows += [[6, b * 4 + beat, 0.9, q, 60 + rnd.randint(-6, 6)] for beat in range(4) for q in tri]
    rows += [[6, b * 4 + i, 0.95, q, 78] for i, q in enumerate(MELN[b])]
m.flp_edit(solo, [{"tool": "flp_compose_write", "pattern": "Piano", "notes": rows, "place": [[1, 12]], "track": 1}])
flh_store.journal_update(solo, set={"key": "B minor"})
real_fx = C._fx_mod
C._fx_mod = lambda: None
try:
    ds = build(project=solo, into=9, dry_run=True)
finally:
    C._fx_mod = real_fx
assert "no timpani" in ds["roll"]["skipped"], ds["roll"]
assert "no celli" in ds["low_strings"]["skipped"] and "Celli" in ds["low_strings"]["do"], ds["low_strings"]
assert ds["harmony"].get("melody_kept") or ds["harmony"].get("revoiced"), ds["harmony"]
m._CALL_BACKUPS.clear()
rs = build(project=solo, into=9)
ns1, _ = heard(solo)
assert rs["did"].startswith("built a rise"), rs
have = {(n[0], n[3], n[4]) for n in ns1 if n[2] == 6}
for b in range(4, 8):                                    # the top voice (the melody) as written, velocity too
    for i, q in enumerate(MELN[b]):
        assert ((b * 4 + i) * P, q, 78) in have, ("piano melody changed", b, i, q)
print("ok")
