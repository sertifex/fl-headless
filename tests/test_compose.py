"""flh_compose: genre data, generators (in key, in range, genre-plausible, deterministic), a full beat written into a
synthetic project through ONE flp_edit batch (mock FL), variations + pick, counter/layer/voicing, arrange_song,
extend, and flp_adjust('bounce') moving swing/accents the right way."""
import json, os, shutil, statistics, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702  (leave no temp files behind)
import fl_headless_mcp as m  # noqa: E402
import fl_music  # noqa: E402
ns = vars(m)
import flh_compose as C  # noqa: E402
if "flp_compose" not in ns["TOOLS"]:                     # the server loads it (EXTENSIONS); else register here
    import flh_store; flh_store.register(ns)
    import flh_score as SC; SC.register(ns)
    C.register(ns)
import flh_score as SC  # noqa: E402
assert callable(ns["compose_around"])

# ----------------------------------------------------------------------------- data
G = {k: v for k, v in C.genres().items() if not k.startswith("_")}
assert not C.genres().get("_errors"), C.genres().get("_errors")
need = {"trap", "boombap", "drill-uk", "drill-ny", "lofi", "rnb", "pop", "house", "techno", "dnb", "afrobeats",
        "reggaeton", "ambient", "cinematic", "rock", "jazzhop", "jersey-club", "phonk", "hyperpop", "lofi-house",
        "synthwave", "dancehall", "amapiano", "gospel", "kpop", "metal"}
assert need <= set(G), need - set(G)
for name, g in G.items():
    for k in ("tempo", "swing", "keys", "drums", "bass", "harmony", "melody", "form", "sections", "roles", "mix"):
        assert k in g, (name, k)
    t = g["tempo"]
    assert t["min"] <= t["default"] <= t["max"], name
    for part, spec in g["drums"]["parts"].items():
        for p in spec.get("patterns", []):
            grid = p.get("grid", 16)
            assert len(p["steps"]) % grid == 0 and set(p["steps"]) <= set("Xxog."), (name, part, p["steps"])
        if spec.get("maybe"):
            assert len(spec["maybe"]) % 16 == 0, (name, part)
        for r in (spec.get("rolls") or {}).get("rates", {}):
            assert r in C.RATE_STEPS, (name, r)
    for part, mask in (g["drums"].get("fill") or {}).get("parts", {}).items():
        assert len(mask) == 16 and set(mask) <= set("Xxog._"), (name, part, mask)
    for p in g["bass"].get("patterns", []):
        assert set(p["steps"]) <= set("x-."), (name, p)
    for p in g["harmony"]["rhythm"]["patterns"] + g["melody"]["rhythms"]:
        assert set(p["steps"]) <= set("x-."), (name, p)
    key = g["keys"]["default"]
    fam = C.key_info(key)[3]
    for pr in g["harmony"]["progressions"]:
        k2 = key if pr.get("mode", fam) == fam else ("C major" if pr["mode"] == "major" else "A minor")
        for tok in pr["rn"]:
            C.chord_from_token(tok, k2)
    for sec, s in g["sections"].items():
        assert s.get("drums", "full") in ("full", "light", "sparse", "half", "build", "none"), (name, sec)
    assert name in [C.genre_data(a)["name"] for a in [name] + g.get("aliases", [])[:1]]
assert C.genre_data("drill")["name"] == "drill-uk" and C.genre_data("boom bap")["name"] == "boombap"
assert C.genre_data("dark trap beat")["name"] == "trap"
for words, want in (("neo soul beat", "rnb"), ("lo-fi house", "lofi-house"), ("jersey club beat", "jersey-club"),
                    ("drift phonk", "phonk"), ("k-pop", "kpop"), ("j-pop ballad", "kpop"), ("metalcore", "metal"),
                    ("amapiano", "amapiano"), ("outrun", "synthwave"), ("dancehall riddim", "dancehall"),
                    ("gospel", "gospel"), ("hyperpop", "hyperpop"), ("deep house", "house")):
    assert C.genre_data(words)["name"] == want, (words, C.genre_data(words)["name"])
ADJ = C.adjectives()
kinds = set(ADJ["op_kinds"])
for w, a in ADJ["words"].items():
    for op in a.get("ops", []):
        assert op["op"] in kinds, (w, op)
    if a.get("opposite"):
        assert a["opposite"] in ADJ["words"], (w, a["opposite"])
for w in ("bounce", "darker", "brighter", "harder", "emptier", "busier", "more human", "tighter", "wider", "warmer",
          "dustier", "epic", "chill", "sad", "happy", "aggressive"):
    assert w in ADJ["words"], w

# ----------------------------------------------------------------------------- theory
def nm(tok, key):
    c = C.chord_from_token(tok, key)
    return C._pc_name(c["root"], True) + c["q"]
assert nm("VI", "C minor") == "Ab" and nm("III", "C minor") == "Eb" and nm("VII", "C minor") == "Bb"
assert nm("bII", "C minor") == "Db" and nm("V7", "C minor") == "G7" and nm("iiø", "C minor") == "Dm7b5"
assert nm("bVII", "C major") == "Bb" and nm("iv", "C major") == "Fm" and nm("bVImaj7", "C major") == "Abmaj7"
assert nm("ii9", "F major") == "Gm9" and nm("#iv°", "C major") == "Gbdim"
assert C.chord_from_token("D/F#", "G major")["bass"] == 6
assert [c["name"] for c, _ in C.parse_progression("Am F C G", "C major")] == ["Am", "F", "C", "G"]


def in_key_or_chord(notes, key, chords):
    sc = set(C.scale_pcs(key, "key"))
    bad = []
    for n in notes:
        ch = C.chord_at(chords, n["s"])
        if n["k"] % 12 not in sc | set(C.chord_pcs(ch)):
            bad.append((n["s"], n["k"], ch["name"]))
    return bad


# ----------------------------------------------------------------------------- generators, every genre
for name, g in sorted(G.items()):
    key = g["keys"]["default"]
    for seed in (1, 2, 3):
        hits = C.drums(g, 8, 1.0, seed)
        assert all(0 <= h["s"] < 128 and 1 <= h["v"] <= 127 for h in hits), name
        chs, info = C.harmony(key, g, 8, None, seed)
        assert sum(c["len"] for c in chs) == 128 and chs[0]["start"] == 0, (name, info)
        cn, ci = C.chord_part(chs, g, key, seed)
        for n in cn:
            ch = C.chord_at(chs, n["s"])
            assert n["k"] % 12 in set(C.chord_pcs(ch)) | {(ch["root"] + 14) % 12, (ch["root"] + 10) % 12, (ch["root"] + 11) % 12}, \
                (name, ch["name"], n)
        lo, hi = g["harmony"]["voicing"]["register"]
        assert all(lo - 12 <= n["k"] <= hi + 12 for n in cn), (name, ci)
        bn, bi = C.bassline(chs, g, [h["s"] for h in hits if h["part"] == "kick"], seed, 0, 8, key)
        blo, bhi = g["bass"]["register"]
        assert all(blo - 12 <= n["k"] <= bhi + 24 for n in bn), (name, [n["k"] for n in bn])
        for c in chs:                                       # the bass plays the chord root on every chord change
            at = [n for n in bn if abs(n["s"] - c["start"]) < 1 and not n.get("glide")]
            if at and g["bass"].get("rhythm") != "walk":
                assert any(n["k"] % 12 == c["root"] for n in at), (name, c["name"], at)
        mn, mi = C.melody(key, chs, g, 8, seed)
        mlo, mhi = g["melody"]["range"]
        assert mn and all(mlo <= n["k"] <= mhi for n in mn), (name, mi)
        assert not in_key_or_chord(mn, key, chs), (name, in_key_or_chord(mn, key, chs)[:3])
        assert all(n["s"] + n["l"] <= 128 + 1e-6 for n in mn), name
        assert all(mn[i]["s"] + mn[i]["l"] <= mn[i + 1]["s"] + 1e-6 for i in range(len(mn) - 1)), name   # monophonic
        cm, cmi = C.counter_melody(mn, chs, key, g, seed, bars=8)
        assert cm, (name, cmi)
        assert not in_key_or_chord(cm, key, chs), (name, "counter")

# trap: 1/16 hats with 1/32 and triplet rolls; snare on beat 3 (half-time)
trap = G["trap"]
roll_steps = set()
for seed in range(1, 9):
    hs = C.drums(trap, 8, 1.0, seed)
    hats = [h for h in hs if h["part"] == "hat"]
    assert sum(1 for h in hats if abs(h["s"] - round(h["s"])) < 1e-6) >= 32
    for h in hats:
        if h.get("roll"):
            roll_steps.add(round(h["l"], 3))
    sn = [h["s"] % 16 for h in hs if h["part"] == "snare" and h["v"] > 90 and not h.get("roll")]
    assert sn and all(abs(s - 8) < 1e-6 for s in sn), sn
assert 0.5 in roll_steps and (0.667 in roll_steps or 0.333 in roll_steps), roll_steps
# boombap: snare on 2 and 4 + ghost snares; swing pushes the odd 16ths late when written
bb = G["boombap"]
hs = C.drums(bb, 8, 1.0, 4)
big = sorted({h["s"] % 16 for h in hs if h["part"] == "snare" and h["v"] > 90})
assert set(big) >= {4, 12}, big
assert any(h["v"] < 60 for h in hs if h["part"] == "snare"), "ghost snares"
rows = C._rows([{"s": 1, "l": 1, "k": 60, "v": 90}, {"s": 2, "l": 1, "k": 60, "v": 90}], 0, None, bb, 90, 1, humanize=False)
assert abs(rows[0][1] - (1 + 0.16) / 4) < 1e-6 and abs(rows[1][1] - 0.5) < 1e-6, rows
# lofi: 7ths and 9ths
lf = G["lofi"]
chs, _ = C.harmony("F major", lf, 8, None, 2)
cn, ci = C.chord_part(chs, lf, "F major", 2)
sizes = {}
for n in cn:
    sizes.setdefault(n["s"], []).append(n)
assert statistics.mean(len(v) for v in sizes.values()) >= 4, ci
ext = sum(1 for v in sizes.values() if any(((n["k"] - C.chord_at(chs, n["s"])["root"]) % 12) in (10, 11, 2) for n in v))
assert ext == len(sizes), (ext, len(sizes))
# rock: power chords
rk = G["rock"]
chs, _ = C.harmony("E minor", rk, 4, None, 1)
cn, _ = C.chord_part(chs, rk, "E minor", 1)
grp = {}
for n in cn:
    grp.setdefault(n["s"], []).append(n["k"])
assert all(sorted(v)[1] - sorted(v)[0] == 7 and len(v) == 3 for v in grp.values()), list(grp.values())[:3]
# 808 glides: trap / drill basses glide and the FL encoding marks slide notes and holds the base note
glides = 0
for name in ("trap", "drill-uk", "drill-ny"):
    for seed in range(1, 5):
        g = G[name]
        chs, _ = C.harmony(g["keys"]["default"], g, 8, None, seed)
        bn, bi = C.bassline(chs, g, None, seed, 0, 8, g["keys"]["default"])
        glides += bi["glides"]
        fl = C.encode_glides(bn, "slide")
        sl = [n for n in fl if n.get("slide")]
        assert len(sl) == bi["glides"], (name, seed)
        for s in sl:                                         # a held note sounds under every slide note
            assert any(n["s"] < s["s"] - 1e-6 and n["s"] + n["l"] >= s["s"] + s["l"] - 1e-6 and not n.get("slide") for n in fl), s
        ov = C.encode_glides(bn, "overlap")
        assert not any(n.get("slide") for n in ov) and len(ov) == len(bn)
assert glides >= 6, glides
bn, _ = C.bassline(*C.harmony("C minor", G["boombap"], 8, None, 1)[:1], G["boombap"], None, 1, 0, 8, "C minor")
assert not any(n.get("glide") for n in bn), "boombap bass doesn't glide"
# jazz-hop walking bass: a note per beat
chs, _ = C.harmony("Bb major", G["jazzhop"], 4, None, 1)
wb, wi = C.bassline(chs, G["jazzhop"], None, 1, 0, 4, "Bb major")
assert wi["rhythm"] == "walk" and len(wb) == 16 and all(n["s"] % 4 == 0 for n in wb), wb
# determinism and variations
a = C.drums(trap, 8, 1.0, 11); b = C.drums(trap, 8, 1.0, 11); c = C.drums(trap, 8, 1.0, 11, vary=1)
assert a == b and a != c
h1 = C.harmony("C minor", trap, 8, None, 3, 0)[1]["progression"]
h2 = C.harmony("C minor", trap, 8, None, 3, 1)[1]["progression"]
assert h1 != h2, (h1, h2)
m1 = C.melody("C minor", C.harmony("C minor", trap, 8)[0], trap, 8, 5)
assert m1 == C.melody("C minor", C.harmony("C minor", trap, 8)[0], trap, 8, 5)
assert m1[0] != C.melody("C minor", C.harmony("C minor", trap, 8)[0], trap, 8, 5, vary=1)[0]
# a user motif keeps its rhythm in the A sections
mot = [[0, 2, "C5"], [2, 2, "Eb5"], [4, 4, "G5"], [8, 4, "F5"]]
mm, _ = C.melody("C minor", C.harmony("C minor", trap, 4, "i iv")[0], trap, 4, 1, motif=mot)
assert [n["s"] for n in mm[:4]] == [0, 2, 4, 8] and mm[0]["k"] == 72, mm[:4]
# voicing: smoother than root-position blocks
blocks = []
for i, (r, q) in enumerate([(0, "maj7"), (9, "m7"), (2, "m7"), (7, "7")]):
    for iv in C.QUALITY[q]:
        blocks.append({"s": i * 16, "l": 16, "k": 48 + r + iv, "v": 80})
rv, ri = C.revoice(blocks, "C major", "extended", [50, 74], {"7": 1, "9": 0.5})
assert ri["movement_after"] < ri["movement_before"], ri
# layers sit in the free register and follow the chords
chs, _ = C.harmony("C minor", trap, 4, "i VI")
pad, pi = C.layer("pad", chs, "C minor", trap, 1, occupied={"melody": [72, 86], "bass": [30, 40]}, bars=4)
assert pad and max(n["k"] for n in pad) <= 71 and min(n["k"] for n in pad) >= 43, pi
arp, ai = C.layer("arp", chs, "C minor", trap, 1, occupied={"melody": [72, 86]}, bars=4, tempo=140)
assert arp and not in_key_or_chord(arp, "C minor", chs)

# ----------------------------------------------------------------------------- drum fingerprints (what makes the genre)
def frac(hits, part, steps, bars, grid_bars=None):
    """Share of bars where `part` hits every step in `steps` (fills excluded: the genre's fill bars are skipped)."""
    ok = n = 0
    for b in range(bars):
        if b in (grid_bars or ()):
            continue
        n += 1
        have = {round(h["s"] - b * 16, 2) for h in hits if h["part"] == part and b * 16 <= h["s"] < b * 16 + 16}
        ok += all(st in have for st in steps)
    return ok / max(1, n)
FP = {"house": [("kick", (0, 4, 8, 12)), ("clap", (4, 12)), ("openhat", (2, 6, 10, 14))],
      "techno": [("kick", (0, 4, 8, 12))], "lofi-house": [("kick", (0, 4, 8, 12)), ("clap", (4, 12))],
      "trap": [("snare", (8,))], "hyperpop": [("snare", (8,))], "boombap": [("snare", (4, 12)), ("kick", (0,))],
      "rock": [("snare", (4, 12)), ("kick", (0,))], "metal": [("snare", ((4, 12), (8,)))], "pop": [("snare", (4, 12))],
      "dnb": [("kick", (0,)), ("snare", (4, 12))], "reggaeton": [("kick", (0, 4, 8, 12)), ("snare", (3, 6, 11, 14))],
      "synthwave": [("kick", (0, 8)), ("snare", (4, 12))], "dancehall": [("kick", (0, 3, 6)), ("clap", (4, 12))],
      "jersey-club": [("kick", (0, 4, 8)), ("clap", (4, 12))], "phonk": [("clap", (4, 12))],
      "amapiano": [("kick", (0,)), ("shaker", (0, 2, 4, 6, 8, 10, 12, 14))], "gospel": [("snare", (4, 12))], "kpop": [("snare", (4, 12))]}
for name, checks in FP.items():
    g = G[name]
    every = int((g["drums"].get("fill") or {}).get("every") or 0)
    fills = {b for b in range(8) if every and (b + 1) % every == 0}
    for seed in (1, 2, 3):
        hs = C.drums(g, 8, 1.0, seed)
        for part, steps in checks:
            alts = steps if isinstance(steps[0], tuple) else (steps,)
            f = max(frac(hs, part, st, 8, fills) for st in alts)
            assert f >= 0.7, (name, seed, part, steps, f)
# jersey club: the bounce - a kick a beat for three beats, then the triplet (or its 16th version) over beats 3-4
jc = C.drums(G["jersey-club"], 8, 1.0, 1)
assert any(abs(h["s"] % 16 - x) < 0.01 for h in jc if h["part"] == "kick" for x in (8 + 8 / 3, 11.0)), "jersey bounce"
# synthwave: the octave pulse (root, octave, root, octave on the 8ths)
sw_chs, _ = C.harmony("A minor", G["synthwave"], 2, "i")
sb, _ = C.bassline(sw_chs, G["synthwave"], None, 1, 0, 2, "A minor")
assert len({n["k"] for n in sb}) == 2 and max(n["k"] for n in sb) - min(n["k"] for n in sb) == 12, sb[:4]

# ----------------------------------------------------------------------------- generated material through the clash
# detector: every genre, a few seeds, chords + bass + melody + counter (with the guards flp_compose uses) -> no
# high / med issue (genre tolerance), no avoid note held over a chord, 808 slides off the downbeat on chord tones
def beat_parts(g, seed, bars=8):
    key = g["keys"]["default"]
    hits = C.drums(g, bars, 1.0, seed)
    chs, _ = C.harmony(key, g, bars, None, seed)
    cn, _ = C.chord_part(chs, g, key, seed)
    bn, bi = C.bassline(chs, g, [h["s"] for h in hits if h["part"] == "kick"], seed, 0, bars, key)
    reg = g["harmony"]["voicing"]["register"]
    C.mud_guard(cn, bn, reg[1] + 7, chs)
    mn, _ = C.melody(key, chs, g, bars, seed)
    C.melody_guard(cn, mn, reg[0])
    cm, _ = C.counter_melody(mn, chs, key, g, seed, bars=bars, accomp=cn)
    return key, chs, cn, bn, mn, cm
def tick_notes(ns_, ch):
    return [{"pos": int(n["s"] * 24), "len": max(1, int(n["l"] * 24)), "ch": ch, "key": n["k"], "vel": n["v"],
             "slide": bool(n.get("slide"))} for n in ns_]
bad, held, avoid = [], 0, 0
for name, g in sorted(G.items()):
    for seed in (1, 2, 3):
        key, chs, cn, bn, mn, cm = beat_parts(g, seed)
        notes = tick_notes(cn, 1) + tick_notes(C.encode_glides(bn), 2) + tick_notes(mn, 3) + tick_notes(cm, 4)
        mod = SC.build_model(notes, 96, parts={1: {"name": "Chords", "role": "chords"}, 2: {"name": "Bass", "role": "bass"},
                                               3: {"name": "Lead", "role": "melody"}, 4: {"name": "Counter", "role": "counter"}},
                             genre=name)
        bad += [(name, seed, i["kind"], i["notes"]) for i in SC.find_clashes(mod) if i["severity"] in ("high", "med")]
        for n in mn:
            if n["l"] >= 2:
                held += 1
                ch = C.chord_at(chs, n["s"])
                nx = next((c for c in chs if n["s"] < c["start"] < n["s"] + n["l"]), None)
                if nx is not None and n["s"] + n["l"] - nx["start"] > nx["start"] - n["s"]:
                    ch = nx
                pcs, pc = set(C.chord_pcs(ch)), n["k"] % 12
                avoid += pc not in pcs and (pc - 1) % 12 in pcs
        for n in bn:
            if n.get("glide"):
                assert n["s"] % 16 != 0, (name, seed, n)
                c = C.chord_at(chs, n["s"])
                prev = max((p for p in bn if p["s"] < n["s"]), key=lambda p: p["s"])
                assert n["k"] % 12 in set(C.chord_pcs(c)) or abs(n["k"] - prev["k"]) == 12, (name, seed, n, c["name"])
                assert n["k"] <= g["bass"]["register"][1] + 12, (name, n)
print("generator clash matrix:", len(G), "genres x 3 seeds, med/high:", bad[:4], "held avoid notes:", avoid, "/", held)
assert not bad, bad
assert avoid == 0, avoid
# avoid notes: the 4th over a major chord is not held, the passing 4th is fine
cmaj = C.chord_from_token("C", "C major")
assert 5 not in C.chord_scale(C.scale_pcs("C major"), cmaj, None, True) and 5 in C.chord_scale(C.scale_pcs("C major"), cmaj)
# guards: a chord note a semitone under the lead moves away; a keys note a b9 over a low bass note moves up
cn_ = [{"s": 0, "l": 16, "k": k, "v": 80} for k in (60, 64, 67, 71)]
mg = C.melody_guard(cn_, [{"s": 0, "l": 8, "k": 72, "v": 100}], 52)
assert mg["moved"] + mg["dropped"] == 1 and not any(abs(72 - n["k"]) % 12 == 1 for n in cn_), cn_
cn_ = [{"s": 0, "l": 16, "k": k, "v": 80} for k in (48, 52, 55)]           # C3 E3 G3 over a B1 bass: C3 = a b9 low
assert C.mud_guard(cn_, [{"s": 0, "l": 16, "k": 35, "v": 100}], 80) == 1 and sorted(n["k"] for n in cn_) == [52, 55, 60], cn_

# ----------------------------------------------------------------------------- compose_around (an idea -> parts)
NN = fl_music.parse_note
ode = "E5 E5 F5 G5 G5 F5 E5 D5 C5 C5 D5 E5 E5 D5 D5".split()
idea = {"notes": [[1 + i * 0.25, 0.25 if i < 14 else 0.5, NN(p), 90] for i, p in enumerate(ode)], "beats_per_bar": 4,
        "bpm": 100, "key": None, "chords": None, "bars": 4, "source": "midi_file"}
ra = C.compose_around(idea, ["chords", "bass", "drums", "counter"], genre="pop", seed=1)
print("around ode:", ra["idea"]["key"], ra["chords"], ra["clashes"]["counts"], ra["steps_s"]["total"])
assert ra["idea"]["key"] == "C major" and ra["chords"] == ["C", "G", "C", "G"], (ra["idea"], ra["chords"])
assert ra["chords_from"].startswith("harmonized") and ra["idea_fit"]["strong_beat_chord_tones"] >= 0.5
assert set(ra["parts_notes"]) == {"chords", "bass", "drums", "counter"} and ra["clashes"]["counts"]["high"] == 0
assert ra["clashes"]["counts"]["med"] == 0 and "how" in ra and ra["steps_s"]["total"] < 0.5
assert all(1 <= row[0] < 5 for rows_ in ra["parts_notes"].values() for row in rows_)
ra2 = C.compose_around(idea, "beat")                     # no genre: an honest guess, with alternatives
assert ra2["guesses"][0].startswith("genre ") and "also fits" in ra2["guesses"][0], ra2["guesses"]
# a swung idea (8ths late): the swing is measured and matched; genre guess says it's swung
sw_rows = [[1 + (b * 16 + i * 2 + (0.6 if i % 2 else 0)) / 16, 1.2 / 16, NN(p), 90]
           for b in range(4) for i, p in enumerate(["C5", "D5", "Eb5", "G5", "F5", "Eb5", "D5", "C5"])]
assert C.idea_swing(C._idea_notes({"notes": sw_rows})[1]) == (0.65, 8)
rs = C.compose_around({"notes": sw_rows, "bpm": 88, "bars": 4}, ["drums", "bass"])
assert rs["swing"]["grid"] == "8ths" and "swung 8ths" in rs["guesses"][0], rs
row8 = C._rows([{"s": 2, "l": 1, "k": 60, "v": 90}, {"s": 4, "l": 1, "k": 60, "v": 90}], 0, None, G["pop"], 90, 1,
               humanize=False, swing8=0.65)
assert abs(row8[0][1] - 0.65) < 1e-6 and abs(row8[1][1] - 1.0) < 1e-6, row8
# played chords (Am F C G) -> read, not re-harmonized; the bass / melody fit them
pc_rows = [[1 + b, 1.0, NN(p), 80] for b, c in enumerate([["A3", "C4", "E4"], ["F3", "A3", "C4"], ["C4", "E4", "G4"],
                                                          ["G3", "B3", "D4"]]) for p in c]
rc_ = C.compose_around({"notes": pc_rows, "bpm": 96, "bars": 4}, ["bass", "melody", "chords"], genre="pop")
assert rc_["idea"]["part"] == "chords" and rc_["chords"] == ["Am", "F", "C", "G"] and "chords" not in rc_["parts"], rc_
assert any("the idea is the chords" in x for x in rc_["notes"]) and rc_["clashes"]["counts"]["med"] == 0
# a bassline -> chords on its roots
bl = [[1 + b + i / 4, 0.2, NN(r) + (7 if i == 2 else 0), 96] for b, r in enumerate(["A2", "F2", "C3", "G2"]) for i in range(4)]
rb = C.compose_around({"notes": bl, "bpm": 122, "bars": 4}, ["chords", "drums"], genre="house")
assert rb["idea"]["part"] == "bass" and [c[:2].rstrip("m7") for c in rb["chords"]] == ["A", "F", "C", "G"], rb["chords"]
# a drum idea: chords from the genre, the 808 locks to its kicks
dr_rows = [[1 + i / 4, 0.05, 36 if i % 2 == 0 else 38, 100] for i in range(8)]
rd = C.compose_around({"notes": dr_rows, "bpm": 140, "bars": 2, "part": "drums"}, ["chords", "bass"], genre="trap", limit=99)
assert rd["idea"]["part"] == "drums" and {round((r[0] - 1) * 16) for r in rd["parts_notes"]["bass"] if not r[4:]} <= {0, 8, 16, 24}
# output_mid: idea + parts in one .mid that flh_score reads back
mid_out = os.path.join(tmp, "around.mid")
ro = C.compose_around(idea, ["chords", "bass", "drums"], genre="pop", output_mid=mid_out)
assert os.path.isfile(mid_out) and ro["output_mid"]["tracks"] == ["Idea", "Drums", "Bass", "Chords"], ro["output_mid"]
assert SC.clashes(mid_out)["counts"]["high"] == 0
for bad_idea in ({"notes": []}, {"notes": [[1, 1, 60, 90]], "time_signature": "3/3"}):
    try:
        C.compose_around(bad_idea, ["chords"]); raise SystemExit("must refuse")
    except ValueError:
        pass
# many genres x ideas: no high/med clash between the idea and what was built around it
nb_ = 0
for name, g in sorted(G.items()):
    key = g["keys"]["default"]
    chs, _ = C.harmony(key, g, 4, None, 51)
    mn, _ = C.melody(key, chs, g, 4, 51)
    rows = [[1 + n["s"] / 16 + (0.004 if i % 3 == 1 else 0), n["l"] / 16, n["k"], n["v"]] for i, n in enumerate(mn)]
    r_ = C.compose_around({"notes": rows, "bpm": g["tempo"]["default"], "bars": 4}, ["all", "pad"], genre=name, seed=2)
    nb_ += sum(1 for i in r_["clashes"]["top"] if i["severity"] in ("high", "med"))
assert nb_ == 0, nb_

# ----------------------------------------------------------------------------- writing into a project (mock FL)
if not os.path.isfile(FIX):
    print("skipped: writer tests (no fixture in tests/data or tests/corpus)"); sys.exit(0)
reloads = []
def fake(project, output, fn):
    if m._EDIT_DEPTH:
        return fn()
    reloads.append(output); return fn()
m._with_open_project = fake
src = os.path.join(tmp, "song.flp"); shutil.copy(FIX, src)
# a synthetic beat project: empty the fixture's pattern and playlist, add named channels by cloning
m.flp_edit_notes(src, [{"op": "delete"}], pattern=1)
ids = [c["id"] for c in m.flp_playlist(src)["clips"]]
m.flp_playlist(src, "remove", clips=ids)
names = ["Kick", "Snare", "Hat", "Open Hat", "808", "Keys", "Lead"]
ops = [{"tool": "flp_add_channel", "clone_from": 0} for _ in names]
ops.append({"tool": "flp_style_channels", "channels": [{"channel": 6 + i, "name": n} for i, n in enumerate(names)]})
m.flp_edit(src, ops)
chan = {n: 6 + i for i, n in enumerate(names)}
v = C.project_view(src)
assert v["roles"][chan["Kick"]] == "kick" and v["roles"][chan["Open Hat"]] == "openhat" and v["roles"][chan["808"]] == "bass"
assert v["roles"][chan["Keys"]] == "chords" and v["roles"][chan["Lead"]] == "melody" and v["empty"]
TOOLS = ns["TOOLS"]
compose = TOOLS["flp_compose"][0]
# dry run writes nothing
before = open(src, "rb").read()
dr = compose(project=src, what="beat", genre="trap", key="C# minor", dry_run=True, seed=7)
assert dr["dry_run"] and open(src, "rb").read() == before and len(dr["would_write"]) == 4, dr
# the beat: one batch, four patterns on the right channels, tempo into the genre's range, slide notes on the 808
n0 = len(reloads)
r = compose(project=src, what="beat", genre="trap", key="C# minor", seed=7)
assert len(reloads) == n0 + 1, "one FL reload for the whole beat"
assert r["verified"], r.get("verify_problems")
print("beat steps_s:", r["steps_s"])
assert r["steps_s"]["total"] < 0.5, r["steps_s"]                      # compute only: FL is mocked here
assert r["channels"] == {"chords": chan["Keys"], "bass": chan["808"], "melody": chan["Lead"]}, r["channels"]
assert r["drum_channels"]["kick"] == chan["Kick"] and r["drum_channels"]["hat"] == chan["Hat"], r["drum_channels"]
assert r["drum_channels"]["snare"] == chan["Snare"] and "clap" not in r["drum_channels"]  # no doubled hits
assert abs(m.flp_info(src)["tempo_bpm"] - 140) < 0.01
pats = {p["name"]: p for p in m.flp_patterns(src)}
for pn, chs_ in (("Beat Drums", [chan["Kick"], chan["Snare"], chan["Hat"]]), ("Beat Chords", [chan["Keys"]]),
                 ("Beat 808", [chan["808"]]), ("Beat Melody", [chan["Lead"]])):
    assert pn in pats, (pn, list(pats))
    assert set(pats[pn]["notes_per_channel"]) <= set(chs_ + [chan["Open Hat"]]) and pats[pn]["notes"] > 0, (pn, pats[pn])
    assert pats[pn]["placed_at_bars"] == [1.0], (pn, pats[pn])
d = m._load(src); ev = m._events(d)
pid808 = next(p for p, x in m._patterns(d, ev).items() if x["name"] == "Beat 808")
recs = m._patterns(d, ev)[pid808]["notes"]
slides = [x for x in recs if x[1] & C.SLIDE_FLAG]
glides = sum(1 for _ in slides)
assert all(x[2] == chan["808"] for x in recs)
assert all(x[1] == C.NOTE_FLAGS for x in recs if not x[1] & C.SLIDE_FLAG)
assert glides >= 1, "seed 7 writes 808 slide notes"
jr = json.load(open(os.path.splitext(src)[0] + ".journal.json"))
assert jr["compose"]["genre"] == "trap" and jr["compose"]["blocks"]["Beat"]["parts"]["bass"] == "Beat 808", jr["compose"]
SC._CACHE.clear()                                          # the written beat, read back from the .flp (slide flags too)
bc = SC.clashes(src)
assert bc["counts"]["high"] == 0 and bc["counts"]["med"] == 0, bc["issues"]
# hats: 16ths on the grid at 140 straight; kicks within the 808's onsets (808 follows the kick)
notes = m.flp_notes(src, pattern="Beat Drums", channel=chan["Hat"], limit=2000)
assert notes["count"] >= 60
# variations side by side, then pick B
r3 = compose(project=src, what="melody", section="Hook", variations=3, seed=3)
lab = [x["label"] for x in r3["variations"]]
assert lab == ["A", "B", "C"], r3
starts = [x["listen"]["start_bar"] for x in r3["variations"]]
assert starts == [9.0, 17.0, 25.0], starts
assert len({x["about"] for x in r3["variations"]}) == 3 or len({tuple(x["patterns"]) for x in r3["variations"]}) == 3
pats = {p["name"]: p for p in m.flp_patterns(src)}
assert all(f"Hook {l} Melody" in pats and pats[f"Hook {l} Melody"]["placed_at_bars"] == [s] for l, s in zip(lab, starts))
mel = [m.flp_notes(src, pattern=f"Hook {l} Melody", limit=500)["notes"] for l in lab]
assert mel[0] != mel[1] and mel[1] != mel[2]
pk = compose(project=src, what="pick", section="Hook", variation="B")
assert pk["picked"] == "B" and pk["placed_at"] == [9.0], pk
cl = m.flp_playlist(src)["clips"]
assert [c["bar"] for c in cl if c["name"] == "Hook B Melody"] == [9.0]
assert not [c for c in cl if c["name"] in ("Hook A Melody", "Hook C Melody")]
jr = json.load(open(os.path.splitext(src)[0] + ".journal.json"))
assert any(s["name"] == "Hook" and s["bar"] == 9.0 for s in jr["sections"]), jr["sections"]
# counter-melody answering the Hook melody, a pad layer, re-voiced chords
rc = compose(project=src, what="counter", section="Hook", seed=2)
assert rc["verified"] and rc["channels"]["counter"] not in chan.values(), rc
assert rc["new_channels"][0]["name"] == "Counter" and rc["new_channels"][0]["sound_needed"]
rl = compose(project=src, what="layer", role="pad", section="Hook", seed=2)
assert rl["verified"], rl
padn = m.flp_notes(src, pattern="Hook Pad", limit=500)["notes"]
assert padn, rl
rv = compose(project=src, what="voicing", channel=chan["Keys"], genre="lofi")
assert rv["patterns"], rv
# arrange: verse/hook form with generated light drums, transitions; then extend the verse
ar = TOOLS["flp_arrange_song"][0](project=src, form="Intro:4, Verse:8, Hook:8, Outro:4", mode="replace", block="Beat")
secs = [(s["section"], s["bar"], s["bars"]) for s in ar["sections"]]
assert secs == [("Intro", 1.0, 4), ("Verse", 5.0, 8), ("Hook", 13.0, 8), ("Outro", 21.0, 4)], secs
assert "Drums Light" in ar["generated"], ar
assert any(t["kind"].startswith("dropout") for t in ar["transitions"]), ar["transitions"]
cl = m.flp_playlist(src)["clips"]
intro = [c for c in cl if c["bar"] < 5 and c["kind"] == "pattern"]
assert intro and not [c for c in intro if "Drums" in c["name"]], intro      # trap intro: no drums
assert [c for c in cl if c["name"] == "Drums Light" and c["bar"] == 5.0]
hook_drums = [c for c in cl if c["name"] == "Beat Drums" and 13 <= c["bar"] < 21]
assert hook_drums, cl
verse_end = [c for c in cl if c["name"] == "Drums Light" and abs(c["bar"] + c["length_bars"] - 12.5) < 1e-3]
assert verse_end, "dropout: verse drums stop 2 beats before the hook"
ex = TOOLS["flp_arrange_song"][0](project=src, extend={"section": "Verse", "bars": 8})
assert ex["at_bar"] == 13.0 and ex["clips_placed"] >= 2, ex
jr = json.load(open(os.path.splitext(src)[0] + ".journal.json"))
hook = next(s for s in jr["sections"] if s["name"] == "Hook")
assert hook["bar"] == 21.0, jr["sections"]
# adjust: 'bounce' swings the drums and bass later on the odd 16ths and strengthens the downbeat accent
adj = TOOLS["flp_adjust"][0]
pre = adj(project=src, words="make it bounce more", dry_run=True)
assert pre["dry_run"] and pre["after_predicted"]["drums"]["swing"] > (pre["before"]["drums"]["swing"] or 0.5), pre
ra = adj(project=src, words="make it bounce more")
b, a = ra["before"]["drums"], ra["after"]["drums"]
assert a["swing"] > (b["swing"] or 0.5) and a["accent"] > b["accent"], (b, a)
assert any("swing" in x for x in ra["as_expected"]), ra
ah = adj(project=src, words="make it bounce more", section="Hook", dry_run=True)   # M6: shared patterns are said
print("adjust Hook also changes:", ah.get("also_changes"))
assert ah["also_changes"]["bars"] and all(not 21 <= b < 29 for b in ah["also_changes"]["bars"]), ah.get("also_changes")
ra2 = adj(project=src, words="a bit emptier, darker")
assert ra2["after"]["drums"]["notes"] < ra2["before"]["drums"]["notes"], ra2
assert ra2["words"][0]["strength"] == 0.5 and "darker" in ra2["mix_intents"]
ra3 = adj(project=src, words="less busy")
assert ra3["words"][0]["word"] == "emptier"
try:
    adj(project=src, words="make it purple"); raise SystemExit("unknown words must be refused")
except ValueError:
    pass
# the writer alone: a pattern made from nothing gets placed even with no pattern clip to copy
src2 = os.path.join(tmp, "blank.flp"); shutil.copy(FIX, src2)
m.flp_playlist(src2, "remove", clips=[c["id"] for c in m.flp_playlist(src2)["clips"]])
w = m.flp_edit(src2, [{"tool": "flp_compose_write", "pattern": "Test", "notes": [[0, 0, 1, 60, 100, 0], [0, 1, 1, 62, 90, 1]],
                       "place": [[3, 2]], "track": 2}])
cl = m.flp_playlist(src2)["clips"]
assert [(c["name"], c["bar"], c["length_bars"], c["track"]) for c in cl] == [("Test", 3.0, 2.0, 2)], cl
d = m._load(src2); ev = m._events(d)
recs = next(x for x in m._patterns(d, ev).values() if x["name"] == "Test")["notes"]
assert sorted(x[1] & C.SLIDE_FLAG for x in recs) == [0, C.SLIDE_FLAG]
# compose_around into a project: the idea gets its own pattern/channel, the parts go next to it, ONE batch; a second
# run finds the idea already in the project and doesn't copy it again
src3 = os.path.join(tmp, "idea.flp"); shutil.copy(FIX, src3)
m.flp_edit_notes(src3, [{"op": "delete"}], pattern=1)
m.flp_playlist(src3, "remove", clips=[c["id"] for c in m.flp_playlist(src3)["clips"]])
m.flp_edit(src3, [{"tool": "flp_add_channel", "clone_from": 0} for _ in names] +
           [{"tool": "flp_style_channels", "channels": [{"channel": 6 + i, "name": n} for i, n in enumerate(names)]}])
n0 = len(reloads)
dr3 = C.compose_around(idea, ["chords", "bass", "drums", "counter"], genre="lofi", project=src3, dry_run=True)
assert dr3["dry_run"] and [w["pattern"] for w in dr3["would_write"]][0] == "Idea" and len(reloads) == n0
wa = C.compose_around(idea, ["chords", "bass", "drums", "counter"], genre="lofi", project=src3)
print("around project:", {k: wa[k] for k in ("written", "channels", "at_bar", "verified")}, wa["steps_s"])
assert wa["verified"] and len(reloads) == n0 + 1 and wa["at_bar"] == 1.0, wa
assert wa["channels"]["idea"] == chan["Lead"] and wa["channels"]["chords"] == chan["Keys"] and wa["channels"]["bass"] == chan["808"]
assert wa["idea"]["in_project"].startswith("written") and wa["steps_s"]["total"] < 0.5
pats3 = {p["name"]: p for p in m.flp_patterns(src3)}
assert {"Idea", "Idea Chords", "Idea Bass", "Idea Drums", "Idea Counter"} <= set(pats3), list(pats3)
assert all(pats3[n]["placed_at_bars"] == [1.0] for n in ("Idea", "Idea Chords", "Idea Bass", "Idea Drums"))
idea_back = m.flp_notes(src3, pattern="Idea", limit=100)["notes"]
assert [fl_music.parse_note(n["note"]) for n in idea_back] == [NN(x) for x in ode]       # untouched, every note
assert [n["at"] for n in idea_back][:3] == ["1.1", "1.2", "1.3"]
assert abs(m.flp_info(src3)["tempo_bpm"] - 100) < 0.01                       # empty project: the idea's tempo
wb = C.compose_around(idea, ["pad"], genre="lofi", project=src3)
assert wb["idea"]["in_project"].startswith("already in pattern 'Idea'") and "Idea Pad" in {p["name"] for p in m.flp_patterns(src3)}
assert sum(1 for p in m.flp_patterns(src3) if p["name"] == "Idea") == 1
jr3 = json.load(open(os.path.splitext(src3)[0] + ".journal.json"))
assert jr3["compose"]["blocks"]["Idea"]["what"] == "around" and jr3["compose"]["genre"] == "lofi", jr3["compose"]
SC._CACHE.clear()
assert SC.clashes(src3)["counts"]["high"] == 0
# the capture tools hand ideas over through ns["compose_around"]
import flh_transcribe as TR  # noqa: E402
TR.register(ns)
cab = TR.capture_and_build(idea, what=["chords", "bass"], genre="pop", options={"seed": 4})
assert cab["genre"] == "pop" and set(cab["parts"]) == {"chords", "bass"} and cab["chords"], cab

# ----------------------------------------------------------------------------- review fixes
# M4: a new note never inherits another note's group / fine pitch / pan / mod (only the MIDI channel)
d = m._load(src2); ev = m._events(d); pid_t = next(p for p, x in m._patterns(d, ev).items() if x["name"] == "Test")
odd = [dict(n, _r=tuple(list(n["_r"][:5]) + [7, 90, 0, 0, 3, 10, n["_r"][11], 5, 9])) for n in
       map(m._rec_to_note, m._patterns(d, ev)[pid_t]["notes"])]
m._save(m._write_pattern_notes(d, ev, pid_t, odd), src2)
m.flp_edit(src2, [{"tool": "flp_compose_write", "pattern": "Fresh", "notes": [[0, 0, 1, 64, 100, 0]]}])
d = m._load(src2); ev = m._events(d)
rec = next(x for x in m._patterns(d, ev).values() if x["name"] == "Fresh")["notes"][0]
first_midi = next(r[9] for _, x in sorted(m._patterns(d, ev).items()) for r in x["notes"] if r[2] == 0)
assert rec[5] == 0 and rec[6] == 120 and rec[10] == 64 and rec[12] == 128 and rec[13] == 128 and rec[9] == first_midi, rec
assert any(r[5] == 7 and r[6] == 90 for x in m._patterns(d, ev).values() for r in x["notes"])   # the odd notes exist
# M2: score's chord names hand over with their extensions and slash basses
for nm_, q_, b_ in (("C7b9", "7b9", None), ("C6/9", "6/9", None), ("Am/E", "m", 4), ("G(5)", "5", None),
                    ("C6/9/E", "6/9", 4), ("Fmaj7#11/A", "maj7", 9)):
    c_ = C._chord_or_none(nm_, "C major")
    assert c_ and c_["q"] == q_ and c_["bass"] == b_, (nm_, c_)
bl, _ = C.bassline([dict(C._chord_or_none("Am/E", "A minor"), start=0, len=32)], "pop", [0, 8, 16, 24], bars=2, key="A minor")
assert all(n["k"] % 12 == 4 for n in bl[:1]), bl[:2]                    # the inversion's bass, not the root
# M1: melodies move mostly by step (leaps over a major 3rd ~9% or less)
ivs = []
for gname in sorted(G):
    gd = C.genre_data(gname); key_ = (gd.get("keys") or {}).get("default", "C minor")
    for sd in range(1, 9):
        chs_, _ = C.harmony(key_, gd, 8, None, sd, 0); ml, _ = C.melody(key_, chs_, gd, 8, sd, 0)
        ivs += [abs(b["k"] - a["k"]) for a, b in zip(ml, ml[1:])]
leaps = sum(1 for x in ivs if x > 4) / len(ivs)
print(f"melody leaps > M3: {leaps:.3f}")
assert leaps < 0.11, leaps
# H1: the post-write clash check covers the bars just written, and only reports this part's channel first
assert rc["clashes"]["bars"] == "9-16" and "counts" in rc["clashes"], rc["clashes"]
# offsets: ops that read pitch (mode) see the sounding pitch of a pitched sampler, writes go back to written pitch
nts = [{"pos": 0, "len": 96, "key": 64 + 30, "vel": 100, "ch": 4, "_r": None}, {"pos": 0, "len": 96, "key": 64, "vel": 100, "ch": 5, "_r": None}]
out_, _ = C.apply_transform(nts, {"ops": [{"op": "mode", "to": "minor", "roles": ["all"]}], "channels": {"4": {"role": "bass"},
                            "5": {"role": "chords"}}, "key": "C major", "offsets": {"4": 30}}, 96)
assert sorted((n["ch"], n["key"]) for n in out_) == [(4, 63 + 30), (5, 63)], out_
# lazy mode: score reads the shadow copy but the real project's journal
seen = {}
real_sv = ns["score_view"]
def spy(*a, **k):
    seen.update(k); return real_sv(*a, **k)
ns["score_view"] = spy
v_ = C.project_view(src); v_["src"] = src + ".shadow.flp"; shutil.copy(src, v_["src"])
C.song_context(v_, 1.0, 4, "C# minor")
ns["score_view"] = real_sv
assert seen.get("journal_from") == src, seen
# arrange / extend / adjust in other meters (and meter maps): tests/test_compose_meter.py
# L7 + M12: replace re-lays only the bars it arranges (a composed clip far after them stays) and reads back
src4 = os.path.join(tmp, "song4.flp"); shutil.copy(src, src4); shutil.copy(os.path.splitext(src)[0] + ".journal.json", os.path.splitext(src4)[0] + ".journal.json")
m.flp_playlist(src4, "place", pattern="Beat Drums", bar=200, track=9)
keep4 = sorted(c["bar"] for c in m.flp_playlist(src4)["clips"] if c["name"] == "Beat Drums")
ar4 = TOOLS["flp_arrange_song"][0](project=src4, form="Intro:4, Hook:8", mode="replace", block="Beat", start_bar=300)
assert ar4["verified"] is True, ar4.get("verify_problems")
now4 = sorted(c["bar"] for c in m.flp_playlist(src4)["clips"] if c["name"] == "Beat Drums")
assert [b for b in now4 if b < 300] == keep4 and any(b >= 300 for b in now4), (keep4, now4)   # earlier clips stay
# FZ-14: a project without channels gets a clear message, not an internal error
emp = os.path.join(tmp, "empty.flp"); shutil.copy(os.path.join(HERE, "corpus", "empty.flp"), emp)
try:
    compose(project=emp, what="chords", genre="pop", bars=4); raise SystemExit("must refuse")
except ValueError as e:
    assert "no channels" in str(e) and "max()" not in str(e), e
# accessibility: the drum grid comes with its key, or as words for screen readers / plain language
rg = compose(project=src, what="drums", genre="trap", bars=1, dry_run=True, full=True)
assert rg["variations"][0]["grid_key"].startswith("16 steps") and "X" in "".join(rg["variations"][0]["grid_bar1"].values())
ns["access_prefs"] = lambda: {"language": "plain"}
rg = compose(project=src, what="drums", genre="trap", bars=1, dry_run=True, full=True)
ns.pop("access_prefs")
assert "step 1" in rg["variations"][0]["grid_bar1"]["kick"] and "grid_key" not in rg["variations"][0], rg["variations"][0]

# ----------------------------------------------------------------------------- review fixes (F4, F15, E2E-6, E2E-7)
import signal, threading  # noqa: E402,E401
signal.alarm(120)                                         # a hang (E2E-6) fails the test instead of blocking the suite


def _copy_song(name):
    p = os.path.join(tmp, name + ".flp"); shutil.copy(src, p)
    shutil.copy(os.path.splitext(src)[0] + ".journal.json", os.path.splitext(p)[0] + ".journal.json")
    return p


def _renumber(p):
    """What FL does on save: the same clips, other ids (here: reversed)."""
    d = m._load(p); ev = m._events(d); k, recs = m._playlist_event(d, ev)
    recs = [bytearray(r) for r in recs]
    ids = [struct.unpack("<I", r[32:36])[0] for r in recs]
    assert len(recs[0]) >= 88 and len(set(ids)) > 2, "fixture must store clip ids"
    for r, i in zip(recs, reversed(ids)):
        struct.pack_into("<I", r, 32, i)
    m._save(m._splice(d, ev, [(k, k + 1, m._data_event(m.EV_PLAYLIST, b"".join(bytes(r) for r in recs)))]), p)


sig = lambda p: sorted((c["name"], c["bar"], c["length_bars"], c["track"]) for c in m.flp_playlist(p)["clips"])
# F4: replace finds the old composed clips by (pattern, bar, track) AFTER FL's save renumbered every clip id; the
# user's own clip far away stays, and the result equals a run where nothing was renumbered
fa, fb = _copy_song("f4a"), _copy_song("f4b")
for p in (fa, fb):
    m.flp_edit(p, [{"tool": "flp_compose_write", "pattern": "My Riff", "notes": [[chan["Lead"], 0, 1, 72, 100, 0]],
                    "place": [[60, 1]], "track": 30}])
form = "Intro:4, Verse:8, Hook:8, Outro:4"
m._with_open_project = lambda project, output, fn: fn() if m._EDIT_DEPTH else (_renumber(output), fn())[1]
try:
    ra = TOOLS["flp_arrange_song"][0](project=fa, form=form, mode="replace", block="Beat")
finally:
    m._with_open_project = fake
rb_ = TOOLS["flp_arrange_song"][0](project=fb, form=form, mode="replace", block="Beat")
assert ra["verified"] and ra["removed_clips"] == rb_["removed_clips"] > 0, (ra, rb_)
assert not [n for n in ra.get("notes", []) if "weren't" in n], ra["notes"]
assert sig(fa) == sig(fb), [x for x in sig(fa) if x not in sig(fb)][:5]
assert ("My Riff", 60.0, 1.0, 30) in sig(fa)
cnt = {}
for x in sig(fa):
    cnt[x] = cnt.get(x, 0) + 1
assert all(v == 1 for v in cnt.values()), [x for x, v in cnt.items() if v > 1]      # nothing stacked twice
jr = json.load(open(os.path.splitext(fa)[0] + ".journal.json"))
assert "Drums Light" in jr["compose"]["arrangement"]["generated"], jr["compose"]["arrangement"]
# replace again: the generated parts from last time (Drums Light, Fill...) are re-laid too, not a clash
rr = TOOLS["flp_arrange_song"][0](project=fa, form=form, mode="replace", block="Beat")
assert rr["verified"] and sig(fa) == sig(fb), (rr, [x for x in sig(fa) if x not in sig(fb)][:5],
                                               [x for x in sig(fb) if x not in sig(fa)][:5])
# F4: the refusal names what's in the way and never tells a replace to use replace
m.flp_edit(fb, [{"tool": "flp_compose_write", "pattern": "My Riff", "place": [[3, 1]], "track": 30}])
try:
    TOOLS["flp_arrange_song"][0](project=fb, form=form, mode="replace", block="Beat"); raise SystemExit("must refuse")
except ValueError as e:
    assert "My Riff" in str(e) and "aren't composed parts" in str(e) and "mode='replace'" not in str(e), e
try:
    TOOLS["flp_arrange_song"][0](project=fb, form=form, mode="build", block="Beat"); raise SystemExit("must refuse")
except ValueError as e:
    assert "start_bar=" in str(e) and "mode='extend'" in str(e), e
# flp_compose_clear: by name within bars; missing clips are counted, not an error
fc = _copy_song("f4c")
n_hook = len([c for c in m.flp_playlist(fc)["clips"] if c["name"] == "Beat Drums"])
rc_ = m.flp_edit(fc, [{"tool": "flp_compose_clear", "names": ["Beat Drums"], "from_bar": 1, "to_bar": 25}])
left = [c for c in m.flp_playlist(fc)["clips"] if c["name"] == "Beat Drums"]
assert rc_["results"][0]["removed"] >= 1 and all(c["bar"] + c["length_bars"] <= 1 or c["bar"] >= 25 for c in left), \
    (rc_, [(c["bar"], c["length_bars"]) for c in left])
assert len(left) == n_hook - rc_["results"][0]["removed"], (n_hook, left)
rc_ = m.flp_edit(fc, [{"tool": "flp_compose_clear", "clips": [["Beat Drums", 999, None]]}])
assert rc_["results"][0]["removed"] == 0 and rc_["results"][0]["not_found"] == 1, rc_
# E2E-6: extend on a zero-length section refuses at once (was an endless loop); so do 0 / absurd bars
fz = _copy_song("f6")
flh_store_ = __import__("flh_store")
jz = flh_store_.journal_get(fz)
flh_store_.journal_update(fz, set={"sections": [{"name": "Zero", "bar": 5.0, "end_bar": 5.0}] + (jz.get("sections") or [])})
for ext, word in (({"section": "Zero", "bars": 4}, "no length"), ({"section": "Verse", "bars": -4}, "bars"),
                  ({"section": "Verse", "bars": 10 ** 9}, "bars"), ({"section": "Verse", "occurrence": 7}, "occurrence")):
    try:
        TOOLS["flp_arrange_song"][0](project=fz, extend=ext, dry_run=True); raise SystemExit(f"must refuse {ext}")
    except ValueError as e:
        assert word in str(e), (ext, e)
try:
    C._slots_for([{"name": "x", "bar": 1.0, "end_bar": 9.0}], 0); raise SystemExit("must refuse 0-bar slots")
except ValueError:
    pass
# E2E-7: two clips on the same start bar make ONE section (the section-like name), none has zero length
f7 = os.path.join(tmp, "f7.flp"); shutil.copy(FIX, f7)
m.flp_edit_notes(f7, [{"op": "delete"}], pattern=1)
m.flp_playlist(f7, "remove", clips=[c["id"] for c in m.flp_playlist(f7)["clips"]])
m.flp_edit(f7, [{"tool": "flp_compose_write", "pattern": nm, "notes": [[0, 0, 1, 60, 100, 0]], "place": pl, "track": tr,
                 "place_mode": "add"}                              # the 2nd Verse is a repeat (default: moved)
                for nm, pl, tr in (("Pattern 7", [[1, 4]], 1), ("Intro", [[1, 4]], 2), ("Verse", [[5, 8]], 1),
                                   ("Hook", [[13, 8]], 3), ("Hook Pad", [[13, 8]], 4), ("Verse", [[21, 8]], 1))])
secs7 = flh_store_.sections_from_project(f7)
assert [(s["name"], s["bar"], s["end_bar"]) for s in secs7] == [("Intro", 1.0, 5.0), ("Verse", 5.0, 13.0),
                                                                 ("Hook", 13.0, 21.0)], secs7
assert len({s["bar"] for s in secs7}) == len(secs7) and all(s["end_bar"] > s["bar"] for s in secs7)
# F15: the composer asks FL to save only while it holds the FL lock; FL busy -> no save, the result says so
saves = []
real = {k: ns.get(k) for k in ("IS_WINDOWS", "fl_running", "_open_project_guess", "_save_in_fl_and_wait")}
ns.update(IS_WINDOWS=True, fl_running=lambda: True, _open_project_guess=lambda: (src, "title"),
          _save_in_fl_and_wait=lambda p, wait_s=8.0, stop=None: saves.append((p, stop is not None and stop())) or True)
try:
    r15 = compose(project=src, what="drums", genre="trap", bars=1, dry_run=True)
    assert saves == [(src, False)] and "read_note" not in r15, (saves, r15.get("read_note"))
    held, go = threading.Event(), threading.Event()

    def holder():
        with ns["_FL_LOCK"]:
            held.set(); go.wait(10)
    th = threading.Thread(target=holder); th.start(); held.wait(5)
    try:
        r15 = compose(project=src, what="drums", genre="trap", bars=1, dry_run=True)
    finally:
        go.set(); th.join()
    assert len(saves) == 1 and "FL is busy" in r15.get("read_note", ""), (saves, r15.get("read_note"))
    other = os.path.join(tmp, "not open.flp"); shutil.copy(src, other)          # FL has another project: no save
    compose(project=other, what="drums", genre="trap", bars=1, dry_run=True)
    assert len(saves) == 1, saves
finally:
    ns.update(real)
# E2E-10: what the flp_edit batch says about FL is carried into the composer's result
r10 = {}
C._carry(r10, {"reloaded_in_fl": False, "fl_shows_old_version": True, "WARNING": "x", "verify_job": "verify-3"})
assert r10 == {"reloaded_in_fl": False, "fl_shows_old_version": True, "WARNING": "x", "verify_job": "verify-3",
               "verified_in_fl": False}, r10
signal.alarm(0)

# genres tool
gl = TOOLS["flh_genres"][0]()
assert gl["count"] >= 26
assert "i" in " ".join(TOOLS["flh_genres"][0](genre="trap")["progressions"])
print("ok")
