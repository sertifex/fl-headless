"""fl_musicality + the hook-first composer: the scorer tells a hook from noise, a locked bass from a loose one and a
song that builds from one texture; melodies are motif-built (a repeated rhythm cell, phrases that land on chord
tones with space after them), deterministic per seed, in any meter; beats and arrangements carry their score;
flp_musicality scores a project; a whole beat composes well under a second."""
import os, random, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402
import fl_musicality as FM  # noqa: E402
ns = vars(m)
import flh_compose as C  # noqa: E402
if "flp_compose" not in ns["TOOLS"]:
    import flh_store; flh_store.register(ns)
    import flh_score as SC; SC.register(ns)
    C.register(ns)

# ----------------------------------------------------------------------------- the scorer tells craft from noise
chs = [{"start": 0, "len": 32, "root": 0, "iv": [0, 4, 7]}, {"start": 32, "len": 32, "root": 5, "iv": [0, 4, 7]}]
cell = [(0, 2, 0), (2, 2, 2), (4, 4, 4), (8, 2, 2), (10, 4, 0)]          # a motif in scale steps (C major)
SC_ = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79]
hook = []                                                                 # the motif twice, then a step higher
for ph, base in ((0, 2), (16, 2), (32, 3), (48, 3)):
    for s, l, d in cell:
        hook.append({"s": ph + s, "l": l, "k": SC_[base + d // 2], "v": 90})
rnd = random.Random(3)
noise = [{"s": i * 1.0, "l": 1.0, "k": rnd.randint(55, 90), "v": 90} for i in range(64)]
hm, nm = FM.score_beat({"melody": hook}, chs, bars=4), FM.score_beat({"melody": noise}, chs, bars=4)
assert hm["score"] > nm["score"] + 15 and hm["hook"] > nm["hook"], (hm, nm)
kick = [{"s": float(s), "part": "kick"} for s in range(0, 64, 8)]
on = [{"s": float(s), "l": 6.0, "k": 36, "v": 100} for s in range(0, 64, 8)]
off = [{"s": float(s) + 0.5, "l": 6.0, "k": 36, "v": 100} for s in range(0, 64, 8)]
g_on = FM.groove_metrics({"drums": kick, "bass": on}, chs, 16, 4)
g_off = FM.groove_metrics({"drums": kick, "bass": off}, chs, 16, 4)
assert g_on["lock"] > g_off["lock"] and g_on["flams"] > g_off["flams"], (g_on, g_off)
same = [{"name": n, "bars": 8, "parts": {"drums": kick * 2, "melody": hook}} for n in ("Intro", "Verse", "Hook", "Outro")]
built = [{"name": "Intro", "bars": 8, "parts": {"melody": hook[:5]}}, {"name": "Verse", "bars": 8, "parts": {"drums": kick}},
         {"name": "Hook", "bars": 8, "parts": {"drums": kick * 2, "melody": hook, "bass": on}},
         {"name": "Outro", "bars": 8, "parts": {"melody": hook[:3]}}]
assert FM.section_metric(built) > FM.section_metric(same), (FM.section_metric(built), FM.section_metric(same))
assert FM.score_beat({}, chs)["score"] is None                              # nothing to score is said, not 0
assert set(FM.compact(hm)) <= {"score", "hook", "fit", "groove", "form", "weak"}

# ----------------------------------------------------------------------------- hook-first melodies
G = {k: v for k, v in C.genres().items() if not k.startswith("_")}
for name in ("trap", "lofi", "boombap", "house", "pop", "gospel", "jazzhop", "rnb"):
    g = G[name]; key = g["keys"]["default"]
    chs_, _ = C.harmony(key, g, 8, None, 2)
    mn, info = C.melody(key, chs_, g, 8, 2)
    assert mn == C.melody(key, chs_, g, 8, 2)[0], name                       # deterministic per seed
    assert info["form"].split()[-1] == "C" and info["candidates"] >= 2, info  # the last phrase is a cadence
    last = mn[-1]                                                            # it ends on a stable chord tone
    c = C.chord_at(chs_, last["s"])
    assert last["k"] % 12 in {(c["root"] + i) % 12 for i in c["iv"] if i in (0, 3, 4, 7)}, (name, last, c["name"])
    sigs = [frozenset(round(n["s"] - b * 16, 1) for n in mn if b * 16 <= n["s"] < b * 16 + 16) for b in range(8)]
    assert sum(1 for b in range(1, 8) if sigs[b] in sigs[:b]) >= 3, (name, sigs)   # the motif's rhythm comes back
    if (g["melody"].get("hook") or {}).get("breath", 3):                    # phrases breathe
        gaps = [mn[i + 1]["s"] - (mn[i]["s"] + mn[i]["l"]) for i in range(len(mn) - 1)]
        assert sum(1 for x in gaps if x >= 1.5) >= 2, (name, gaps)
    sc = FM.score_beat({"melody": mn}, chs_, bars=8)
    assert sc["hook"] >= 0.7 and sc["fit"] >= 0.7, (name, sc)
# a contrasting plan letter (B) and a user motif still work; any meter
gos = G["gospel"]
assert "B" in C.melody("Ab major", C.harmony("Ab major", gos, 8)[0], gos, 8, 1)[1]["form"]
for ts in ("3/4", "6/8", "7/8"):
    with C.in_meter(ts) as mt:
        g = G["pop"]; chs_, _ = C.harmony("C major", g, 8, None, 1)
        mn, info = C.melody("C major", chs_, g, 8, 1)
        assert mn and all(0 <= n["s"] < 8 * mt["spb"] for n in mn), (ts, info)
        assert all(mn[i]["s"] + mn[i]["l"] <= mn[i + 1]["s"] + 1e-6 for i in range(len(mn) - 1)), ts
# variants for sections: the call keeps each phrase's first bar + its landing; sparse fewer still
var_c = C.melody_variant(mn_ := C.melody("C major", C.harmony("C major", G["pop"], 8)[0], G["pop"], 8, 1)[0], "call")
var_s = C.melody_variant(mn_, "sparse")
assert 0 < len(var_s) <= len(var_c) < len(mn_), (len(var_s), len(var_c), len(mn_))
assert all((n["s"] % 32) < 16 or n is max((x for x in mn_ if x["s"] // 32 == n["s"] // 32), key=lambda x: x["s"])
           for n in var_c)
# a bridge on other chords
bc, about = C.bridge_harmony("C major", "pop", 8, [[0, 16, "Am"], [16, 16, "F"], [32, 16, "C"], [48, 16, "G"]], 1)
assert bc and bc[0]["root"] != 9 and "bridge on" in about, about

# ----------------------------------------------------------------------------- grooves: bass with the kick, drums settle
for name in ("pop", "gospel", "lofi"):
    g = G[name]; key = g["keys"]["default"]
    hits = C.drums(g, 8, 1.0, 1)
    ks = sorted({h["s"] for h in hits if h["part"] == "kick"})
    chs_, _ = C.harmony(key, g, 8, None, 1)
    bn, _ = C.bassline(chs_, g, ks, 1, 0, 8, key)
    on_k = sum(1 for k in ks if any(abs(n["s"] - k) < 0.5 or n["s"] <= k < n["s"] + n["l"] for n in bn)) / float(len(ks))
    assert on_k >= 0.8, (name, on_k)                                       # every kick has bass under it
    assert FM.groove_metrics({"drums": [h for h in hits if h["part"] == "kick"], "bass": bn}, chs_, 16, 8)["flams"] >= 0.8
assert C.bass_lock_of("house") == "offbeat" and C.bass_lock_of("jazzhop") == "walk" and C.bass_lock_of("trap") == "kick"
tr = C.drums(G["trap"], 8, 1.0, 4)
oh4 = [h for h in tr if h["part"] == "openhat" and 48 <= h["s"] < 64]       # bar 4: the phrase marker
assert any(abs(h["s"] - 62) < 0.01 for h in oh4), oh4
lt = C.drums(G["lofi"], 8, 1.0, 1, "light")                                  # a light verse fills too
assert {round(h["s"] % 16) for h in lt if h["part"] == "snare" and 48 <= h["s"] < 64} != \
       {round(h["s"] % 16) for h in lt if h["part"] == "snare" and 16 <= h["s"] < 32}

# ----------------------------------------------------------------------------- whole beats: scored, fast, deterministic
worst = 0.0
for name in ("trap", "lofi", "boombap", "house", "pop", "gospel", "jazzhop"):
    g = G[name]; key = g["keys"]["default"]
    t = time.time(); bt = C.compose_beat(g, key, 8, 5); dt = time.time() - t
    worst = max(worst, dt)
    assert bt["musicality"]["score"] >= 80, (name, bt["musicality"])
    assert bt == C.compose_beat(g, key, 8, 5) or bt["parts"] == C.compose_beat(g, key, 8, 5)["parts"], name
print(f"slowest beat: {worst * 1000:.0f} ms")
assert worst < 1.0, worst

# ----------------------------------------------------------------------------- in a project: compose, arrange, score
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):
    FIX = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIX):
    print("skipped: project part (no fixture)"); print("ok"); sys.exit(0)
m._with_open_project = lambda project, output, fn: fn()
src = os.path.join(tmp, "song.flp"); shutil.copy(FIX, src)
m.flp_edit_notes(src, [{"op": "delete"}], pattern=1)
m.flp_playlist(src, "remove", clips=[c["id"] for c in m.flp_playlist(src)["clips"]])
names = ["Kick", "Snare", "Hat", "Open Hat", "Bass", "Keys", "Lead"]
ops = [{"tool": "flp_add_channel", "clone_from": 0} for _ in names]
ops.append({"tool": "flp_style_channels", "channels": [{"channel": 6 + i, "name": n} for i, n in enumerate(names)]})
m.flp_edit(src, ops)
T = ns["TOOLS"]
r = T["flp_compose"][0](project=src, what="beat", genre="pop", section="Hook", seed=3)
assert r["verified"] and r["musicality"]["score"] >= 80 and "musicality" in r["variations"][0]["about"], r["musicality"]
assert r["steps_s"]["generate"] < 1.0, r["steps_s"]
a = T["flp_arrange_song"][0](project=src, genre="pop", mode="replace", seed=3)
assert a["verified"], a.get("verify_problems")
gen = set(a["generated"])
assert {"Melody Sparse", "Bridge Chords", "Bridge Bass", "Bridge Melody"} <= gen, gen
assert "bridge on" in a.get("bridge", ""), a.get("bridge")
assert a["musicality"]["score"] >= 75 and a["musicality"]["song_form"] >= 0.8, a["musicality"]
mu = T["flp_musicality"][0](project=src)
assert mu["musicality"]["score"] >= 75 and len(mu["sections"]) == len(a["sections"]), mu
hooks = [x for x in mu["sections"] if x["section"] == "Hook"]
assert hooks and all("hook" in x for x in hooks), hooks
intro = next(x for x in mu["sections"] if x["section"] == "Intro")
assert "hook" not in intro, intro                                            # a teaser isn't judged as the hook
one = T["flp_musicality"][0](project=src, section="Hook", full=True)
assert len(one["sections"]) == 3 and "song_form" not in one["musicality"], one          # every Hook, no song form
assert "detail" in one["sections"][0] and one["musicality"]["score"] >= 70, one
assert "flp_musicality" in T and "flp_musicality" not in m.listed_tools(), "unlisted: the tool list stays small"
r3 = T["flp_compose"][0](project=src, what="melody", section="Hook", variations=3, seed=4, dry_run=True)
assert r3["musicality"].get("best") in ("A", "B", "C") and all("musicality" in x for x in r3["variations"]), r3["musicality"]
print("ok")
