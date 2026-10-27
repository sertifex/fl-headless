"""flh_compose in any meter: meter facts (data/meters.json), every generator in 3/4 6/8 2/4 5/4 7/8 12/8 (bar
lengths, strong beats, chord tones), 4/4 output unchanged (a signature of the pre-meter code), and a 3/4 solo-piano
project built with the server's own writers: layers follow its chords and bass in 3-beat bars, roles=[...] writes
several layers on their own channels in ONE batch, support level is faint with the choir on the climax only, the
layers end with the music, beat / bass / melody / counter / voicing work in 3/4, generic drum feel said honestly,
compose_around takes a 3/4 idea, tempo maps don't move anything off the bars; flp_arrange_song / extend /
flp_adjust in 3/4 (transitions a beat of the meter, 3-beat fills and generated drums, the bar insert) and on a meter
map (4/4 then 3/4: material mismatch said, the time-signature marker moves with an extend); a faint pad layer joins a
lofi beat block and the arrangement; _fresh on a stale project says why FL wasn't asked to save."""
import hashlib, json, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702  (leave no temp files behind)
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
import flh_compose as C  # noqa: E402
if "flp_compose" not in ns["TOOLS"]:                     # the server loads it (EXTENSIONS); else register here
    import flh_store; flh_store.register(ns)
    import flh_score as SC; SC.register(ns)
    C.register(ns)
import flh_score as SC  # noqa: E402

# ----------------------------------------------------------------------------- meter facts
mi = C.meter_info
assert mi("3/4")["spb"] == 12 and mi("3/4")["strong"] == [0] and mi("3/4")["beats"] == [0, 4, 8]
assert mi("6/8")["spb"] == 12 and mi("6/8")["beats"] == [0, 6] and mi("6/8")["pulse"] == 6
assert mi("2/4")["spb"] == 8 and mi("5/4")["spb"] == 20 and mi("5/4")["strong"] == [0, 12]
assert mi("7/8")["spb"] == 14 and mi("7/8")["beats"] == [0, 4, 8] and mi("12/8")["spb"] == 24
assert mi("4/4")["legacy"] and mi("2/2")["legacy"] and not mi("3/4")["legacy"]
assert mi("5/8")["beats"] == [0, 4] and mi("11/8")["spb"] == 22                 # computed when not in the data
assert C._parse_ts(3) == (3, 4) and C._parse_ts(3.5) == (7, 8) and C._parse_ts("6/8") == (6, 8)
for bad in ("3/3", "0/4", "7/12"):
    try:
        C._parse_ts(bad); raise SystemExit(f"{bad} must be refused")
    except ValueError:
        pass
MD = C.meters_data()["meters"]
for ts, d in MD.items():                                  # shipped generic grooves: one bar = the meter's steps
    S = mi(ts)["spb"]
    for part, ps in d["drums"]["parts"].items():
        for p in ps["patterns"]:
            assert len(p["steps"]) % S == 0 and set(p["steps"]) <= set("Xxog."), (ts, part, p)
        if ps.get("maybe"):
            assert len(ps["maybe"]) % S == 0, (ts, part)
    for part, mk in (d["drums"].get("fill") or {}).get("parts", {}).items():
        assert len(mk) == S and set(mk) <= set("Xxog._"), (ts, part, mk)
    assert all(0 <= b < S for b in d["beats"] + d["strong"] + d["chord_segments"]), ts
G = {k: v for k, v in C.genres().items() if not k.startswith("_")}
for name, g in G.items():                                 # genre files' own meter patterns
    for ts, d in ((g.get("drums") or {}).get("meters") or {}).items():
        S = mi(ts)["spb"]
        for part, ps in d["parts"].items():
            assert all(len(p["steps"]) % S == 0 for p in ps["patterns"]), (name, ts, part)
        for part, mk in (d.get("fill") or {}).get("parts", {}).items():
            assert len(mk) == S, (name, ts, part)
with C.in_meter("6/8"):                                   # 4/4 data bars laid onto the meter's beats
    assert C._fit_bar("x---x---x---x---", 12) == "x-----x-----" and C._fit_bar("x-x-x-x-x-x-x-x-", 12) == "x--x--x--x--"
with C.in_meter("3/4"):
    assert C._fit_bar("x---x-x-x---x---", 12) == "x---x-x-x---" and C._spb() == 12
with C.in_meter("5/4"):
    assert C._fit_bar("x-------x---x---", 20) == "x-------x---x---x---"
assert C._spb() == 16 and C._meter()["legacy"]           # outside: 4/4 again

# ----------------------------------------------------------------------------- 4/4 output pinned (changed on purpose 2026-10-22: hook-first melodies, kick-locked bass, settled drums)
def sig44():
    out = []
    for name in ("trap", "lofi", "house", "cinematic", "rock", "jazzhop"):
        g = C.genre_data(name); key = g["keys"]["default"]
        hits = C.drums(g, 8, 1.0, 3)
        chs, _ = C.harmony(key, g, 8, None, 3)
        cn, _ = C.chord_part(chs, g, key, 3)
        bn, _ = C.bassline(chs, g, [h["s"] for h in hits if h["part"] == "kick"], 3, 0, 8, key)
        mn, _ = C.melody(key, chs, g, 8, 3)
        cm, _ = C.counter_melody(mn, chs, key, g, 3, bars=8)
        lay = [C.layer(r, chs, key, g, 3, 0, {"melody": [64, 80]}, 8, 120, mn)[0]
               for r in ("pad", "strings", "choir", "arp", "bell", "brass")]
        out.append([hits, chs, cn, bn, mn, cm, lay])
    return hashlib.sha1(json.dumps(out, sort_keys=True).encode()).hexdigest()
assert sig44() == "756b9315da0b4896833a3ad40c2a5e80bb21c89d", "4/4 generator output changed"
with C.in_meter("4/4"):
    assert sig44() == "756b9315da0b4896833a3ad40c2a5e80bb21c89d"


# ----------------------------------------------------------------------------- generators in every meter
def chord_ok(n, chs, extra=(2, 10, 11, 9, 5)):
    ch = C.chord_at(chs, n["s"])
    return n["k"] % 12 in set(C.chord_pcs(ch)) | {(ch["root"] + x) % 12 for x in extra}


for ts in ("3/4", "6/8", "2/4", "5/4", "7/8", "12/8"):
    with C.in_meter(ts) as mt:
        S, total = mt["spb"], 8 * mt["spb"]
        st_n = [0, 0]                                     # held notes on the meter's strong beats: chord tones
        for name, g in sorted(G.items()):
            key = g["keys"]["default"]
            for lvl in ("full", "light", "sparse", "half", "build"):
                hits = C.drums(g, 8, 1.0, 1, lvl)
                assert hits and all(0 <= h["s"] < total and 1 <= h["v"] <= 127 for h in hits), (ts, name, lvl)
            full = C.drums(g, 8, 1.0, 1)
            assert all(any(h["part"] == "kick" and abs(h["s"] - b * S) < 1e-6 for h in full) for b in (0, 1)), (ts, name)
            chs, info = C.harmony(key, g, 8, None, 1)
            assert sum(c["len"] for c in chs) == total and chs[0]["start"] == 0, (ts, name, info)
            cn, _ = C.chord_part(chs, g, key, 1)
            assert cn and all(0 <= n["s"] < total for n in cn) and all(chord_ok(n, chs) for n in cn), (ts, name)
            bn, bi = C.bassline(chs, g, [h["s"] for h in full if h["part"] == "kick"], 1, 0, 8, key)
            assert bn and all(0 <= n["s"] < total for n in bn), (ts, name)
            for c in chs:                                 # the bass plays the root on every chord change
                at = [n for n in bn if abs(n["s"] - c["start"]) < 1 and not n.get("glide")]
                if at and g["bass"].get("rhythm") != "walk":
                    assert any(n["k"] % 12 == c["root"] for n in at), (ts, name, c["name"])
            mn, _ = C.melody(key, chs, g, 8, 1)
            sc = set(C.scale_pcs(key, "key"))
            assert mn and all(0 <= n["s"] < total for n in mn), (ts, name)
            assert sum(1 for n in mn if n["k"] % 12 in sc | set(C.chord_pcs(C.chord_at(chs, n["s"])))) >= 0.9 * len(mn), (ts, name)
            strong = [n for n in mn if C._strong(n["s"]) and n["l"] >= 3]
            st_n[0] += len(strong); st_n[1] += sum(1 for n in strong if chord_ok(n, chs, (2,)))
            cm, _ = C.counter_melody(mn, chs, key, g, 1, bars=8)
            assert all(0 <= n["s"] < total for n in cm), (ts, name)
            for r in C.LAYER_ROLES:
                ln, li = C.layer(r, chs, key, g, 1, 0, {"melody": [64, 80]}, 8, 110, mn)
                assert ln and all(0 <= n["s"] < total and n["s"] + n["l"] <= total for n in ln), (ts, name, r)
                if C.LAYER_ROLES[r]["kind"] in ("sustain", "low"):
                    assert all(chord_ok(n, chs, (2,)) or n["k"] % 12 == (C.chord_at(chs, n["s"]).get("bass") or -1)
                               for n in ln), (ts, name, r)
                    assert all(c["start"] % S == 0 or c["start"] % (S // 2) == 0 for c in chs), (ts, name)
        assert st_n[1] >= 0.93 * st_n[0] and st_n[0] > 50, (ts, st_n)   # (an appoggiatura may resolve by step)
        # the drum source is honest: genre patterns when the genre has them, else a generic feel
        hits, info = C.drums_meter("cinematic", 4)
        assert info["source"] == ("genre" if ts in ("3/4", "6/8") else "generic"), (ts, info)
        assert "generic" in C.drum_feel_note("pop") and "own" in C.drum_feel_note("cinematic") or ts not in ("3/4", "6/8")
with C.in_meter("3/4"):                                   # 3/4 strong beat = beat 1 only; 6/8: 1 and 4
    assert C._strong(0) and C._strong(12) and not C._strong(8) and C._on_beat(8) and not C._on_beat(6)
with C.in_meter("6/8"):
    assert C._strong(6) and C._on_beat(6) and not C._on_beat(4)
assert C._strong(8) and C._on_beat(4)                    # 4/4
# layer options: register / vel / where spans; low strings follow a given bass line
with C.in_meter("3/4"):
    chs, _ = C.harmony("C major", "pop", 4, "C Am F G", 1)
    low, li = C.layer("low strings", chs, "C major", "pop", 1, 0, None, 4, 100, follow=[(0, 40), (12, 45), (24, 41), (36, 43)])
    assert [n["k"] % 12 for n in low] == [4, 9, 5, 7] and all(36 <= n["k"] <= 57 for n in low), low   # E under C: C/E
    ch_, _ = C.layer("choir", chs, "C major", "pop", 1, 0, None, 4, 100, vel=[30, 40], where=[(12, 36)])
    assert ch_ and all(12 <= n["s"] and n["s"] + n["l"] <= 36 and 30 <= n["v"] <= 40 for n in ch_), ch_
    st_, si = C.layer("strings", chs, "C major", "pop", 1, 0, None, 4, 100, register=[48, 60])
    assert si["register"] == [48, 60] and all(48 <= n["k"] <= 72 for n in st_), st_
assert C.layer_role("Cello") == "low strings" and C.layer_role("voices") == "choir"
try:
    C.layer_role("kazoo"); raise SystemExit("unknown role must be refused")
except ValueError as e:
    assert "low strings" in str(e)

# ----------------------------------------------------------------------------- a 3/4 solo-piano project
FIX = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIX):
    print("skipped: project tests (no tests/corpus/base.flp)"); sys.exit(0)
reloads = []
def fake(project, output, fn):
    if m._EDIT_DEPTH:
        return fn()
    reloads.append(output); return fn()
m._with_open_project = fake
TOOLS = ns["TOOLS"]
compose = TOOLS["flp_compose"][0]


def blank(name, num=3, den=4, chans=()):
    p = os.path.join(tmp, name); shutil.copy(FIX, p)
    m.flp_edit_notes(p, [{"op": "delete"}], pattern=1)
    m.flp_playlist(p, "remove", clips=[c["id"] for c in m.flp_playlist(p)["clips"]])
    if (num, den) != (4, 4):
        m.flp_project_info(p, p, time_sig_num=num, time_sig_beat=den)
    if chans:
        ops = [{"tool": "flp_add_channel", "clone_from": 0} for _ in chans]
        ops.append({"tool": "flp_style_channels", "channels": [{"channel": 6 + i, "name": n} for i, n in enumerate(chans)]})
        m.flp_edit(p, ops)
    return p


src = blank("waltz.flp", 3, 4, ["Piano"])
assert m.flp_info(src)["time_signature"] == "3/4"
PIANO = 6
PROG = [("C", 48, [60, 64, 67]), ("C/E", 52, [60, 64, 67]), ("F", 41, [60, 65, 69]), ("G", 43, [59, 62, 67]),
        ("Am", 45, [60, 64, 69]), ("Em", 40, [59, 64, 67]), ("F", 41, [60, 65, 69]), ("G", 43, [59, 62, 67]),
        ("Am", 45, [60, 64, 69]), ("F", 41, [60, 65, 69]), ("C", 48, [60, 64, 67]), ("G", 43, [59, 62, 67]),
        ("F", 41, [60, 65, 69]), ("G", 43, [59, 62, 67]), ("C", 48, [60, 64, 67]), ("C", 36, [60, 64, 67])]
MEL = {"C": [72, 76, 79], "C/E": [76, 74, 72], "F": [77, 76, 74], "G": [74, 71, 67], "Am": [72, 76, 81], "Em": [71, 67, 64]}
rows = []
for b, (nm_, bass, tri) in enumerate(PROG):            # LH: bass on 1, chord on 2 and 3; RH: quarters; bars 9-12 loud
    lv, rv = (92, 104) if 8 <= b < 12 else (56, 64)
    t0 = b * 3
    if b == 15:                                          # the last chord rings 2 beats, then the piece is over
        rows += [[PIANO, t0, 2.0, bass, lv]] + [[PIANO, t0, 2.0, p, lv] for p in tri] + [[PIANO, t0, 2.0, 72, rv]]
        continue
    rows.append([PIANO, t0, 1.0, bass, lv])
    rows += [[PIANO, t0 + beat, 0.9, p, lv - 6] for beat in (1, 2) for p in tri]
    rows += [[PIANO, t0 + i, 0.95, p, rv] for i, p in enumerate(MEL[nm_])]
m.flp_edit(src, [{"tool": "flp_compose_write", "pattern": "Piano", "notes": rows, "place": [[1, 16]], "track": 1}])
cl = m.flp_playlist(src)["clips"]                       # the batch writer placed 16 bars of 3 beats (server's reader)
assert [(c["name"], c["bar"], c["length_bars"]) for c in cl] == [("Piano", 1.0, 16.0)], cl
v = C.project_view(src)
assert v["meters"] == [(0, 3, 4)] and v["bpb"] == 3
with C.in_meter("3/4"):                                  # the song's chords, read per 3-beat bar
    ctx = C.song_context(v, 1.0, 16, "C major")
    assert [c["start"] for c in ctx["chords"]][:8] == [0, 12, 24, 36, 48, 60, 72, 84], ctx["chords"][:8]
    assert ctx["chords"][1].get("bass") == 4, ctx["chords"][1]      # C/E: the slash bass is kept
    sp, bars_ = C._loudest(ctx, v["roles"], 16, 12)
    assert bars_ == [(9, 12)], bars_
n0 = len(reloads)
before = open(src, "rb").read()
dr = compose(project=src, what="layer", roles=["low strings", "strings", "choir"], bars="all", genre="cinematic",
             seed=3, dry_run=True)
assert dr["dry_run"] and open(src, "rb").read() == before and len(dr["would_write"]) == 3, dr
r = compose(project=src, what="layer", roles=["cello", "strings", "choir"], bars="all", genre="cinematic", seed=3)
assert len(reloads) == n0 + 1, "one FL reload for all the layers"
assert r["verified"] and r["meter"] == "3/4" and r["bars"] == 16, r
assert set(r["channels"]) == {"low strings", "strings", "choir"} and len(set(r["channels"].values())) == 3, r["channels"]
assert sorted(c["name"] for c in r["new_channels"]) == ["Choir", "Low Strings", "Strings"], r["new_channels"]
assert r["level"].startswith("support") and r["layers"]["choir"]["where"].startswith("the climax, bars 9-12"), r["layers"]
assert r["layers"]["low strings"]["follows"] == "the song's bass" and "bar 16 beat 3" in r["layers"]["strings"]["ends"]
assert r["clashes"]["counts"]["high"] == 0 and r["clashes"]["counts"]["med"] == 0, r["clashes"]
cl = {c["name"]: c for c in m.flp_playlist(src)["clips"]}
for nm_ in ("Loop Low Strings", "Loop Strings", "Loop Choir"):
    assert cl[nm_]["bar"] == 1.0 and cl[nm_]["length_bars"] == 16.0, cl[nm_]
d = m._load(src); ev = m._events(d); P = m._ppq(d)
pats = {p["name"]: p["notes"] for p in m._patterns(d, ev).values()}
piano_vel = sorted(x[11] for x in pats["Piano"])[len(pats["Piano"]) // 2]
end_beats = 15 * 3 + 2                                   # the piano's last note ends at bar 16 beat 3
for nm_, ch in (("Loop Low Strings", r["channels"]["low strings"]), ("Loop Strings", r["channels"]["strings"]),
                ("Loop Choir", r["channels"]["choir"])):
    ns_ = pats[nm_]
    assert ns_ and all(x[2] == ch for x in ns_), nm_
    for x in ns_:
        beat = x[0] / P
        bar = int(round(beat / 3 - 0.02)) if abs(beat / 3 - round(beat / 3)) < 0.1 else None
        assert bar is not None, (nm_, beat)               # every layer chord starts on a 3-beat bar line
        nm_c, bass, tri = PROG[min(15, bar)]
        root = tri[0] if nm_c not in ("Am", "Em", "G") else {"Am": 57, "Em": 52, "G": 55}[nm_c]
        pcs = {p % 12 for p in tri} | {(root + 2) % 12}
        if nm_ == "Loop Low Strings":
            assert x[4] % 12 == bass % 12 and 36 <= x[4] <= 57, (beat, x[4], bass)     # the song's bass, cello range
        else:
            assert x[4] % 12 in pcs, (nm_, beat, x[4], nm_c)
        assert x[11] < piano_vel, (nm_, x[11], piano_vel)           # faint: under the piano
    assert max((x[0] + x[3]) / P for x in ns_) <= end_beats + 0.05, nm_   # nothing rings past the music
    if nm_ != "Loop Choir":                               # (the choir sings the climax only)
        assert abs(max((x[0] + x[3]) / P for x in ns_) - end_beats) < 0.1, nm_  # the final chord holds to it
choir_beats = [x[0] / P for x in pats["Loop Choir"]]
assert min(choir_beats) >= 24 - 0.1 and max(choir_beats) < 36, choir_beats  # bars 9-12 only
assert max(x[11] for x in pats["Loop Choir"]) < max(x[11] for x in pats["Loop Strings"])
jr = json.load(open(os.path.splitext(src)[0] + ".journal.json"))
assert jr["channels"][str(r["channels"]["low strings"])]["role"] == "bass"         # score / mixer read it as a bass
assert jr["compose"]["channels"]["low strings"] == r["channels"]["low strings"]
# again: the same channels are found (no new clones), one more batch
r2 = compose(project=src, what="layer", roles=[{"role": "choir", "where": [[5, 8]], "vel": 40}, "low strings"],
             bars="all", seed=4)
assert not r2.get("new_channels") and r2["channels"]["choir"] == r["channels"]["choir"], r2
assert r2["layers"]["choir"]["where"] == "bars 5-8" and r2["layers"]["choir"]["vel"] == [34, 46], r2["layers"]
d = m._load(src); ev = m._events(d)
ch2 = [x for p in m._patterns(d, ev).values() if p["name"] == "Loop Choir" for x in p["notes"]]
assert ch2 and all(12 - 0.1 <= x[0] / P < 24 and 34 - 8 <= x[11] <= 46 + 8 for x in ch2), [(x[0] / P, x[11]) for x in ch2]
for bad in ({"roles": ["strings", "choir"], "channel": 3}, {"roles": ["kazoo"]}, {"roles": [{"role": "choir", "where": "loud"}]}):
    try:
        compose(project=src, what="layer", **bad); raise SystemExit(f"must refuse {bad}")
    except ValueError:
        pass
# one role, normal level: the old behaviour (not faint), still in 3-beat bars
r3 = compose(project=src, what="layer", role="pad", bars=8, seed=5, realism=False)
pad = [x for p in m._patterns(m._load(src), m._events(m._load(src))).values() if p["name"] == "Loop Pad" for x in p["notes"]]
assert r3["verified"] and "level" not in r3 and min(x[11] for x in pad) >= 50, [x[11] for x in pad]
assert all(abs(x[0] / P / 3 - round(x[0] / P / 3)) < 0.05 for x in pad)
# the other composer paths in 3/4 follow the song too
rb = compose(project=src, what="bass", bars=8, seed=2, genre="pop")
assert rb["verified"] and rb["meter"] == "3/4", rb
bn = [x for p in m._patterns(m._load(src), m._events(m._load(src))).values() if p["name"] == "Loop Bass" for x in p["notes"]]
for b in range(8):                                       # a bass note on every bar's downbeat, on the song's chord
    at = [x for x in bn if abs(x[0] / P - 3 * b) < 0.1]
    assert at and any(x[4] % 12 in ({PROG[b][1] % 12} | {p % 12 for p in PROG[b][2]}) for x in at), (b, at)
rm = compose(project=src, what="melody", bars=8, seed=2, genre="pop")
assert rm["verified"] and rm["meter"] == "3/4"
rc = compose(project=src, what="counter", source=PIANO, bars=8, seed=2)
assert rc["verified"], rc
rl = compose(project=src, what="layer", roles=["counter", {"role": "pad", "where": "climax"}], source=PIANO, bars="all",
             level="support", seed=2, dry_run=True)
assert rl["layers"]["pad"]["where"].startswith("the climax") and rl["layers"]["counter"]["vel"][1] < piano_vel, rl["layers"]
rv = compose(project=src, what="voicing", channel=PIANO, genre="pop", dry_run=True)
assert rv["patterns"], rv
SC._CACHE.clear()
assert SC.clashes(src, bars="1-8")["counts"]["high"] == 0
# a beat on an empty 3/4 project: generic waltz drums, said honestly; clips are 3-beat bars
b34 = blank("beat34.flp", 3, 4, ["Kick", "Snare", "Hat", "Bass", "Keys", "Lead"])
rb = compose(project=b34, what="beat", genre="trap", key="A minor", bars=4, seed=1)
assert rb["verified"] and rb["meter"] == "3/4" and any("generic 3/4 feel" in n for n in rb["notes"]), rb.get("notes")
cl = m.flp_playlist(b34)["clips"]
assert {c["length_bars"] for c in cl} == {4.0} and {c["bar"] for c in cl} == {1.0}, cl
d = m._load(b34); ev = m._events(d); P2 = m._ppq(d)
allp = {p["name"]: p["notes"] for p in m._patterns(d, ev).values()}
kicks = sorted({round(x[0] / P2, 2) for x in allp["Beat Drums"] if x[2] == 6})
assert all(any(abs(k - 3 * b) < 0.05 for k in kicks) for b in range(4)) and max(kicks) < 12, kicks
assert all(x[0] / P2 < 12 for p in allp.values() for x in p), "nothing past 4 bars of 3/4"
rc6 = compose(project=blank("cine68.flp", 6, 8, ["Kick", "Snare", "Taiko", "Strings", "Horns"]), what="drums",
              genre="cinematic", bars=4)
assert rc6["meter"] == "6/8" and any("cinematic's own 6/8 patterns" in n for n in rc6["notes"]), rc6.get("notes")
# compose_around: a 3/4 idea (time_signature or beats_per_bar) -> parts in 3-beat bars
idea = [[1 + b + i / 3, 1 / 3, k, 90] for b in range(4) for i, k in enumerate((69, 72, 76) if b % 2 == 0 else (67, 71, 74))]
for meta in ({"time_signature": "3/4"}, {"beats_per_bar": 3}):
    ca = C.compose_around(dict({"notes": idea, "bpm": 100, "bars": 4}, **meta), ["chords", "bass", "drums"], genre="pop", seed=1)
    assert any("3/4" in n for n in ca["notes"]) and len(ca["chords"]) >= 2, ca
    for part, rows_ in ca["parts_notes"].items():
        assert all(1 <= r_[0] < 5 for r_ in rows_), (part, rows_[:4])
    assert any(r_[0] == 2.0 for r_ in ca["parts_notes"]["bass"]), ca["parts_notes"]["bass"]   # bar 2 starts after 3 beats
try:
    C.compose_around({"notes": idea, "bpm": 100, "bars": 4, "time_signature": "3/4"}, ["chords"], genre="pop",
                     project=blank("four.flp", 4, 4, ["Keys"]))
    raise SystemExit("a 3/4 idea into a 4/4 project must be refused")
except ValueError as e:
    assert "3/4" in str(e) and "4/4" in str(e), e
# ----------------------------------------------------------------------------- arrange / extend / adjust in 3/4
import struct  # noqa: E402
A = TOOLS["flp_arrange_song"][0]
ADJ = TOOLS["flp_adjust"][0]


def copy_song(srcp, name):
    p_ = os.path.join(tmp, name); shutil.copy(srcp, p_)
    shutil.copy(os.path.splitext(srcp)[0] + ".journal.json", os.path.splitext(p_)[0] + ".journal.json")
    return p_


def clips_of(p_):
    out = {}
    for c in m.flp_playlist(p_)["clips"]:
        out.setdefault(c["name"], []).append((c["bar"], c["length_bars"]))
    return {k: sorted(x) for k, x in out.items()}


def pat_notes(p_):
    d_ = m._load(p_); ev_ = m._events(d_)
    return {p["name"]: p["notes"] for p in m._patterns(d_, ev_).values() if p["notes"]}


ar = copy_song(b34, "arr34.flp")
ra = A(project=ar, form="Intro:4, Verse:8, Hook:8, Outro:4", genre="trap", mode="replace", block="Beat", seed=1)
assert ra["verified"] and [x["meter"] for x in ra["sections"]] == ["3/4"] * 4, ra
assert [x["bar"] for x in ra["sections"]] == [1, 5, 13, 21] and \
    [n for n in ra["generated"] if n not in C.FX_NAMES.values() and not n.startswith("Snare Build")] == \
        ["Drums Light", "Melody Sparse"], ra
rr_ = next(t for t in ra["transitions"] if t["kind"] == "riser")      # ear candy in 3/4: 4 beats = 1 1/3 bars
assert rr_["bar"] == 11.667 and rr_["at"] == "ends on bar 13", rr_
sb_ = next(t for t in ra["transitions"] if t["kind"] == "snare_build")  # 2 bars of 3/4, stopped by the 2-beat drop-out
assert sb_["bar"] == 11.0 and sb_["beats"] == 4.0, sb_
assert ra["musicality"]["score"] > 0 and "song_form" in ra["musicality"], ra["musicality"]
cl = clips_of(ar)
assert (9.0, 3.333) in cl["Drums Light"] and (9.0, 3.333) in cl["Beat 808"], cl   # dropout: 2 of 3 beats out
assert (17.0, 3.667) in cl["Beat Chords"] and (17.0, 3.667) in cl["Beat Drums"], cl  # stop: the last beat silent
assert max(b + L for b, L in cl["Beat Chords"]) == 25.0, cl
pn = pat_notes(ar)
assert max((x[0] + x[3]) / P2 for x in pn["Drums Light"]) <= 12 + 1e-6, "4 bars of 3 beats"
assert 0 < len(pn["Melody Sparse"]) < len(pn["Beat Melody"])                     # the intro / outro teaser
assert (1.0, 4.0) in clips_of(ar)["Melody Sparse"] and "Beat Melody" in clips_of(ar)   # (the hook keeps the full one)
assert all(any(abs(x[0] / P2 - 3 * b) < 0.05 for x in pn["Drums Light"] if x[2] == 6) for b in range(4))  # kick on 1
jr = json.load(open(os.path.splitext(ar)[0] + ".journal.json"))
assert [(x["name"], x["bar"]) for x in jr["sections"]][:4] == [("Intro", 1.0), ("Verse", 5.0), ("Hook", 13.0), ("Outro", 21.0)]
# a fill in 3/4: one 3-beat bar, ending on the bar line
af = copy_song(b34, "fill34.flp")
rf = A(project=af, form="Hook:4, Verse:4", genre="trap", mode="replace", block="Beat", seed=1)
assert rf["verified"] and any(t["kind"] == "drum fill" and t["bar"] == 4 for t in rf["transitions"]), rf["transitions"]
cl = clips_of(af)
assert cl["Fill"] == [(4.0, 1.0)] and (1.0, 3.0) in cl["Beat Drums"], cl
fn_ = pat_notes(af)["Fill"]
assert fn_ and all(x[0] / P2 < 3 for x in fn_) and max(x[0] for x in fn_) / P2 > 2, [x[0] / P2 for x in fn_]
# extend in 3/4: the bars are 3-beat bars; everything after moves
re_ = A(project=ar, extend={"section": "Verse", "bars": 4}, seed=2)
assert re_["at_bar"] == 13.0 and re_["meter"] == "3/4" and re_["clips_placed"] > 0, re_
cl = clips_of(ar)
assert cl["Melody Sparse"][-1] == (25.0, 4.0) and (17.0, 4.0) in cl["Beat Melody"], cl   # (intro / outro: the teaser)
d = m._load(ar); ev = m._events(d)
assert any(struct.unpack("<I", r[0:4])[0] == 16 * 3 * P2 for _, _, r in m._clips(d, ev)), "bar 17 of 3/4 = tick 16 * 3 beats"
jr = json.load(open(os.path.splitext(ar)[0] + ".journal.json"))
assert [(x["name"], x["bar"]) for x in jr["sections"]][:4] == [("Intro", 1.0), ("Verse", 5.0), ("Hook", 17.0), ("Outro", 25.0)]
rh = A(project=af, extend={"section": "Hook", "bars": 4}, seed=2)          # the varied drums: 3/4 bars too
vn = pat_notes(af)[rh["drum_variation"]]
assert rh["drum_variation"] == "Hook Drums Var" and max(x[0] / P2 for x in vn) < 12, rh
assert all(any(abs(x[0] / P2 - 3 * b) < 0.05 for x in vn if x[2] == 6) for b in range(3)), sorted({x[0] / P2 for x in vn})
# adjust in 3/4: accents / added hits follow the meter's beats (no longer skipped)
a34 = ADJ(project=ar, words="make it bounce more", dry_run=True)
assert "3/4" in a34.get("meter", "") and any(k.split(":")[1].startswith("accent") for k in a34["changes"]), a34
m34 = C.meter_info("3/4"); m68 = C.meter_info("6/8")
assert [C._metric({"pos": t}, 96, False, m34) for t in (0, 96, 192, 48, 24)] == ["downbeat", "backbeat", "backbeat",
                                                                                 "eighth", "offbeat"]
assert [C._metric({"pos": t}, 96, False) for t in (0, 96, 192)] == ["downbeat", "backbeat", "downbeat"]   # 4/4
assert C._metric({"pos": 144}, 96, False, m68) == "downbeat" and C._metric({"pos": 96}, 96, False, m68) == "eighth"
sn_ = [{"pos": b * 288, "len": 24, "key": 38, "vel": 100, "ch": 1, "_r": None} for b in range(4)]
out_, _ = C.apply_transform(sn_, {"ops": [{"op": "density", "factor": 2.0, "roles": ["snare"]}],
                                  "channels": {"1": {"role": "snare", "drum": True}}, "meter": "3/4"}, 96)
assert {(n["pos"] // 24) % 12 for n in out_ if n["pos"] % 288} <= {6, 11}, sorted(n["pos"] for n in out_)

# ----------------------------------------------------------------------------- a meter map: 4/4, then 3/4 from bar 5
def with_marker(p_, bar44, ts):
    d_ = m._load(p_); ev_ = m._events(d_); P_ = m._ppq(d_)
    k99 = next(k for k, e in enumerate(ev_) if e["id"] == 99)
    mk = bytes([148]) + struct.pack("<I", int((bar44 - 1) * 4 * P_) | m.MARKER_SIGNATURE) + bytes([33, ts[0], 34, ts[1]])
    mk += m._data_event(205, "Waltz".encode("utf-16-le") + b"\x00\x00")
    m._save(m._splice(d_, ev_, [(k99 + 1, k99 + 1, mk)]), p_)


mp = blank("map.flp", 4, 4, ["Kick", "Snare", "Hat", "Bass", "Keys", "Lead"])
with_marker(mp, 5, (3, 4))
assert m.flp_info(mp)["time_signature_changes"] == [{"bar": 5.0, "time_signature": "3/4"}]
rb = compose(project=mp, what="beat", genre="trap", key="A minor", bars=4, seed=1)
assert rb["verified"] and "meter" not in rb, rb                    # bars 1-4: 4/4
rm_ = A(project=mp, form="Intro:4, Hook:4", genre="trap", mode="replace", block="Beat", seed=1)
assert [x.get("meter") for x in rm_["sections"]] == ["4/4", "3/4"] and rm_["verified"], rm_
assert any("in 3/4 but its material (Beat) was written in 4/4" in n for n in rm_.get("notes", [])), rm_.get("notes")
cl = clips_of(mp)
assert (5.0, 4.0) in cl["Beat Drums"] or (5.0, 3.667) in cl["Beat Drums"] or any(b == 5.0 for b, _ in cl["Beat Chords"]), cl
# extend the 4/4 Intro by 2 bars: 4/4 bars go in, the 3/4 marker moves with the song (bar 7 now)
rx = A(project=mp, extend={"section": "Intro", "bars": 2}, seed=1)
assert rx["meter"] == "4/4" and rx["at_bar"] == 5.0, rx
assert m.flp_info(mp)["time_signature_changes"] == [{"bar": 7.0, "time_signature": "3/4"}], m.flp_info(mp)
assert any(b == 7.0 for b, _ in clips_of(mp)["Beat Chords"]), clips_of(mp)["Beat Chords"]
# the writer itself: bars inside the 3/4 part are 3-beat bars
ins = TOOLS["flp_compose_insert"][0](project=mp, output=mp, at=9, bars=2)
d = m._load(mp); P_ = m._ppq(d)
assert ins["meter"] == "3/4" and ins["ticks"] == 2 * 3 * P_ and ins["at_tick"] == 6 * 4 * P_ + 2 * 3 * P_, ins
assert ins["markers_moved"] == 0 and m.flp_info(mp)["time_signature_changes"] == [{"bar": 7.0, "time_signature": "3/4"}]
# ----------------------------------------------------------------------------- a faint pad in a lofi arrangement
lo = blank("lofi.flp", 4, 4, ["Kick", "Snare", "Hat", "Bass", "Keys", "Guitar", "Pad"])
rb = compose(project=lo, what="beat", genre="lofi", key="F major", bars=4, seed=2, section="A")
rp = compose(project=lo, what="layer", role="pad", section="A", level="support", seed=2, realism=False)
assert rp["verified"] and rp["channels"]["pad"] == 12 and not rp.get("new_channels"), rp
jr = json.load(open(os.path.splitext(lo)[0] + ".journal.json"))
blk = jr["compose"]["blocks"]["A"]
assert blk["what"] == "beat" and blk["parts"]["pad"] == "A Pad" and blk["parts"]["drums"] == "A Drums", blk
ral = A(project=lo, genre="lofi", mode="replace", seed=2)
lay = {x["section"]: x["layers"] for x in ral["sections"]}
assert ral["verified"] and "pad" in lay["Intro"] and "pad" in lay["B"] and "pad" not in lay["A"], lay
pv = [x[11] for x in pat_notes(lo)["A Pad"]]; kv = [x[11] for x in pat_notes(lo)["A Chords"]]
assert pv and sorted(pv)[len(pv) // 2] < sorted(kv)[len(kv) // 2], (pv[:6], kv[:6])    # faint: under the keys
# ----------------------------------------------------------------------------- _fresh on a stale project (reload failed)
saved = {k: ns.get(k) for k in ("IS_WINDOWS", "fl_running", "_open_project_guess", "_stale_block", "_save_in_fl_and_wait")}
asked = []
ns.update(IS_WINDOWS=True, fl_running=lambda: True, _open_project_guess=lambda: (lo, None),
          _stale_block=lambda p_=None: "not saved: FL holds an older copy", _save_in_fl_and_wait=lambda *a, **k: asked.append(1))
try:
    note = C._fresh(lo)
    assert not asked and "newer than the copy FL has open" in note and "didn't save in time" not in note, note
    ns["_stale_block"] = lambda p_=None: None; ns["_save_in_fl_and_wait"] = lambda *a, **k: False
    assert "didn't save in time" in C._fresh(lo)
finally:
    for k, x in saved.items():
        if x is None:
            ns.pop(k, None)
        else:
            ns[k] = x
# a tempo map: the layers stay on the bars (beats), the result says the tempo moves
tm = os.path.join(tmp, "tempo.flp"); shutil.copy(src, tm)
shutil.copy(os.path.splitext(src)[0] + ".journal.json", os.path.splitext(tm)[0] + ".journal.json")
m.flp_tempo_write(tm, tm, points=[[1, 60], [9, 76], [13, 64]])
rt = compose(project=tm, what="layer", role="strings", bars="all", seed=3, realism=False, section=None)
assert any("tempo changes" in n for n in rt.get("notes", [])), rt.get("notes")
d = m._load(tm); ev = m._events(d)
st = [x for p in m._patterns(d, ev).values() if p["name"] == "Loop Strings" for x in p["notes"]]
assert st and all(abs(x[0] / P / 3 - round(x[0] / P / 3)) < 0.05 for x in st)
# meter maps: a change inside the song is found (markers), bars <-> ticks follow it
vv = {"P": 96, "meters": [(0, 4, 4), (4 * 4 * 96, 3, 4)], "tempo": 120.0, "tempos": [(0, 120.0)]}
assert C._meter_at(vv, 1.0) == ((4, 4), [(5.0, "3/4")]) and C._meter_at(vv, 6.0) == ((3, 4), [])
assert C._bar_tick(vv, 6.0) == 16 * 96 + 3 * 96 and abs(C._tick_bar(vv, 16 * 96 + 3 * 96) - 6.0) < 1e-6
print("ok")
