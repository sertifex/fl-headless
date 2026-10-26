"""flp_arrange_song ear candy: audio FX (flh_fx) placed on the section downbeats (riser ends on the Hook's first
tick, impact / sub drop start on it), one sampler channel per kind reused across boundaries and re-arrangements,
one FX insert, genre defaults (trap: riser + impact into Hook; lofi: none of them), ear_candy=False writes no FX,
dry runs write nothing, without flh_fx today's behaviour, filter moves (Fruity Filter automation) when FL's plugin
database has the filter, and ONE project save per arrangement. Ear candy with purpose (data/ear_candy.json): the
amounts (subtle / normal / lots / false: per-change counts, -6 / 0 / +2 dB), the rules (one riser, one big hit per
change, a kind only where it has a job, nothing inside a section), the snare build (8ths -> 16ths -> 32nds, rising
velocity, stops at a drop-out), delay throws (Fruity Delay 3 wet automation on the lead's insert, planned when the
lead plays into the Master), texture beds only under intro / outro / breakdown / bridge, the old synthetic cinematic
profile (only with ear_candy='synthetic'). Organic mode (cinematic / ambient / piano-led songs, collections that want
real / faint support): string swells, timpani / cymbal rolls, a choir breath, piano pickups written as notes on the
song's own channels (velocity ramps, a channel-volume ramp, nothing inside a section, no audio files), skipped moves
naming the missing instrument; the defaults per genre / journal / collection profile; flp_remove_fx removing exactly
what the arrangement added (channels, patterns, clips, plugins, the FX insert name) and its undo.
Run: python3 tests/test_compose_fx.py (no FL)."""
import json, os, shutil, struct, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
import flh_compose as C  # noqa: E402
import flh_store  # noqa: E402
import flh_newsong as N  # noqa: E402
import flh_sampler as SM  # noqa: E402
import flh_cleanup as CL  # noqa: E402
for mod in (flh_store, __import__("flh_score"), C, N, SM, CL):
    if getattr(mod, "H", None) is not ns:
        mod.register(ns)
TOOLS = ns["TOOLS"]
assert "flp_compose_fx" in TOOLS and ns["_OPEN_PROJECT_GUARD"]["flp_compose_fx"] == ("project", "output")

# ----------------------------------------------------------------------------- genre data
G = C.genres()
kinds_of = lambda g, sec: {C._trans_item(x)[0] for x in C._trans_for(G[g], sec)}
assert {"dropout", "riser", "impact", "sub_drop"} <= kinds_of("trap", "Hook"), kinds_of("trap", "Hook")
assert C._trans_item("riser:4") == ("riser", {"beats": 4.0})
assert C._trans_item({"kind": "Sub-Drop", "beats": 2}) == ("sub_drop", {"beats": 2})
for g in ("lofi", "jazzhop", "rnb", "gospel"):
    tr = G[g].get("transitions") or {}
    every = [C._trans_item(x)[0] for xs in list((tr.get("into") or {}).values()) + list((tr.get("after") or {}).values())
             for x in xs] + [C._trans_item(x)[0] for x in tr.get("end") or []]
    assert not {"riser", "impact", "sub_drop", "noise_sweep"} & set(every), (g, every)
for name, g in G.items():                                  # every kind in the data is one the arranger knows
    if name.startswith("_"):
        continue
    tr = g.get("transitions") or {}
    for xs in list((tr.get("into") or {}).values()) + list((tr.get("after") or {}).values()) + [tr.get("end") or []]:
        for x in xs:
            assert C._trans_item(x)[0] in C.TRANS_KINDS, (name, x)
    assert set(C._ear_candy_cfg(g)["filters"]) <= {"intro", "build", "outro"}, name
assert "reverse_crash" in kinds_of("lofi", "Hook") and "tape_stop" in {C._trans_item(x)[0] for x in G["lofi"]["transitions"]["end"]}

# ----------------------------------------------------------------------------- ear candy amounts + purpose rules (data)
ECD = C._ec_data()
assert set(ECD["amounts"]) == set(C.EC_AMOUNTS) and ECD["kinds"] and ECD["profiles"].get("synthetic_cinematic")
ORG = ECD["organic"]
assert set(ORG["moves"]) == set(C.ORGANIC_MOVES) and set(ORG["amounts"]) == set(C.EC_AMOUNTS)
assert ORG["amounts"]["subtle"]["per_boundary"] == 1 and ORG["amounts"]["normal"]["per_boundary"] == 2
for k, md in ORG["moves"].items():                          # every move: a role, a job, a reason, an instrument
    assert md.get("role") and md.get("purpose") and md.get("why") and md.get("needs") and md.get("catalog"), k
for _pl, its in ORG["places"]:
    assert set(its) <= set(C.ORGANIC_MOVES), its
for k, kd in ECD["kinds"].items():                         # every kind has a role, a job and a reason
    assert kd.get("role") and kd.get("purpose") and kd.get("why"), k
    assert k in C.TRANS_KINDS, k
for k in C.FX_BEDS:
    assert ECD["kinds"][k]["role"] == "bed" and set(ECD["kinds"][k]["purpose"]) <= set(ECD["rules"]["bed_sections"])
for name, g in G.items():                                   # every genre has its default amount in data
    if not name.startswith("_"):
        assert C._ear_candy_cfg(g)["amount"] in C.EC_AMOUNTS, name
amt = lambda gn, ec=True, j=None: C._ec_settings(ec, G[gn], {"journal": j or {}}, os.path.join(tmp, "x", "Song.flp"))
for gn in ("lofi", "jazzhop", "ambient", "cinematic"):
    assert amt(gn)["amount"] == "subtle", gn
for gn in ("trap", "drill-uk", "drill-ny", "house", "techno", "dnb", "phonk", "hyperpop"):
    assert amt(gn)["amount"] in ("normal", "lots"), gn
assert amt("hyperpop")["amount"] == "lots" and amt("trap")["from"] == "genre trap"
assert amt("trap", "lots")["amount"] == "lots" and amt("trap", "lots")["from"] == "given"
assert amt("trap", False) is None and amt("trap", "off") is None
try:
    amt("trap", "loads"); raise AssertionError("bad amount accepted")
except ValueError as e:
    assert "subtle" in str(e)
assert amt("trap", True, {"compose": {"ear_candy": "subtle"}})["from"] == "song journal"
# the mode: organic for cinematic / ambient / piano-led, synthetic for beats; the old cinematic set only when asked
for gn in ("cinematic", "ambient"):
    e_ = amt(gn)
    assert e_["mode"] == "organic" and e_["mode_from"] == f"genre {gn}" and e_["profile"] is None, e_
    assert e_["amount"] == "subtle" and e_["cfg"]["per_boundary"] == 1, e_
for gn in ("trap", "drill-uk", "house", "pop", "lofi"):
    assert amt(gn)["mode"] == "synthetic" and amt(gn)["profile"] is None, gn
assert amt("trap")["profile"] is None
pl = amt("pop", True, {"role": "piano-led closer"})              # the journal says piano-led: organic, subtle
assert pl["mode"] == "organic" and pl["amount"] == "subtle" and "piano-led" in pl["mode_from"], pl
assert amt("pop", "lots", {"role": "piano-led"})["amount"] == "lots"   # a given amount still wins (organic, 2/change)
assert amt("pop", "lots", {"role": "piano-led"})["mode"] == "organic"
assert amt("pop", True, {"role": "pianola"})["mode"] == "synthetic"    # whole words only
syn = amt("cinematic", "synthetic")                         # the old synthetic cinematic set: only when asked for
assert syn["mode"] == "synthetic" and syn["profile"][0] == "synthetic_cinematic" and syn["amount"] == "subtle", syn
assert amt("cinematic", "synthetic:lots")["amount"] == "lots"
og = amt("trap", "organic")                                 # organic on a beat when asked
assert og["mode"] == "organic" and og["mode_from"] == "given" and og["amount"] == "subtle" and og["given"] == "organic"
assert amt("trap", "organic:normal")["cfg"]["per_boundary"] == 2 and amt("trap", "organic:normal")["given"] == \
    "organic:normal"
assert amt("cinematic", "off") is None and amt("cinematic", False) is None
assert amt("cinematic", True, {"compose": {"ear_candy": "off"}}) is None          # the song said no
assert amt("cinematic", "subtle", {"compose": {"ear_candy": "off"}})["mode"] == "organic"   # asked again: back
assert amt("trap", True, {"compose": {"ear_candy": "organic"}})["mode_from"] == "song journal"
assert C._ec_parse("Organic:Normal") == (False, "organic", "normal") and C._ec_parse(None) == (False, None, None)
for bad in ("loads", "organic:loads", "subtle:organic", 7):
    try:
        C._ec_parse(bad); raise AssertionError(f"bad ear_candy {bad!r} accepted")
    except ValueError as e:
        assert "organic" in str(e) and "off" in str(e)
# the collection profile: its style text and its ear_candy key
flh_store.profile_set("Reworks", {"style": "Piano reworks of game themes: piano is the star, support layers faint, "
                                           "everything must sound real"})
RW = {"collection": "Reworks"}                              # a song of the collection (its journal says so)
cp = amt("pop", True, RW)
assert cp["mode"] == "organic" and "collection's style" in cp["mode_from"], cp
# ... only its own songs: a new song made outside it ('' = no collection) or one it doesn't list keeps the genre's
assert amt("pop", True, {"collection": ""})["mode"] == "synthetic"
assert amt("synthwave")["mode"] == "synthetic" and amt("pop")["mode_from"] == "default", amt("pop")["mode_from"]
flh_store.profile_set("Reworks", {"songs": {"Song": {"project": os.path.join(tmp, "x", "Song.flp")}}})
assert amt("pop")["mode"] == "organic"                      # listed in the profile's songs: a member
flh_store.profile_set("Reworks", {"songs": {"Song": None}})
assert amt("trap", "synthetic", RW)["mode"] == "synthetic"  # a given mode wins over the profile's words
flh_store.profile_set("Reworks", {"ear_candy": "off"})
assert amt("pop", True, RW) is None and amt("pop", True, dict(RW, compose={"ear_candy": "lots"}))["amount"] == "lots"
assert amt("pop", "organic", RW)["mode"] == "organic"       # (journal wins over the profile)
assert amt("pop")["mode"] == "synthetic"                    # not a member: the profile's 'off' doesn't apply
flh_store.profile_set("Beats", {"style": "hard trap", "ear_candy": "organic:normal"})
BT = {"collection": "Beats"}
cb = amt("trap", True, BT)
assert cb["mode"] == "organic" and cb["mode_from"] == "collection profile" and cb["amount"] == "normal", cb
flh_store.profile_set("Beats", {"ear_candy": "lots"})
assert amt("trap", True, BT)["mode"] == "synthetic" and amt("trap", True, BT)["amount"] == "lots"
for bad in ("loud", "organic:loud", 3):
    try:
        flh_store.flh_profile("Beats", set={"ear_candy": bad}); raise AssertionError(f"profile ear_candy {bad!r}")
    except ValueError as e:
        assert "organic" in str(e)
flh_store.flh_profile("Beats", set={"ear_candy": "organic"})
assert flh_store.profile_get("Beats")["ear_candy"] == "organic"
ns["_state"]({"collection": None})                          # the writer tests below: no active collection
assert amt("trap")["mode"] == "synthetic"
# the levels: subtle -6 dB, normal 0, lots +2 dB from the under-the-beat level (trap: kick -3 dB, 11 dB under)
lv = {a: C._ec_levels(amt("trap", a), G["trap"], {"levels_db": {"kick": -3, "snare": -5, "808": -4, "chords": -8}}, {})
      for a in C.EC_AMOUNTS}
assert lv["normal"] == (-14.0, -22.0) and lv["subtle"] == (-20.0, -28.0) and lv["lots"] == (-12.0, -20.0), lv
# where a change has a job: tags
TB = [{"section": n, "energy": e} for n, e in (("Intro", .2), ("Verse", .5), ("Build", .7), ("Drop", 1.0),
                                               ("Breakdown", .3), ("Drop", 1.0), ("Outro", .3))]
tags = [C._ec_tags(TB, i, ECD) for i in range(1, len(TB) + 1)]
assert "climax" in tags[4] and "climax" not in tags[2] and {"drop", "big"} <= tags[2]   # the LAST drop is the climax
assert "after_hook" in tags[3] and "breakdown" in tags[3] and "big" not in tags[3] and tags[6] == {"end"}
assert "build" in tags[1]
# the rules: one riser, one big hit, the amount's count, purpose
ecn, ecs, ecl = amt("trap"), amt("trap", "subtle"), amt("trap", "lots")
L = [C._trans_item(x) for x in ("riser:8", "noise_sweep", "cymbal_swell", "impact", "boom", "sub_drop", "snare_build",
                                "downlifter", "wind")]
ch_, sk_ = C._ec_pick(L, {"section", "hook", "big"}, ecn, "trap")
assert [k for k, _ in ch_] == ["riser", "impact", "snare_build"], ch_
why = dict(sk_)
assert "riser" in why["noise_sweep"] and "riser" in why["cymbal_swell"] and "big hit" in why["boom"] \
    and "big hit" in why["sub_drop"] and "no job" in why["downlifter"] and "wind" not in why, sk_
assert [k for k, _ in C._ec_pick(L, {"section", "hook"}, ecs, "trap")[0]] == ["riser"]
assert [k for k, _ in C._ec_pick(L, {"section", "hook", "climax"}, ecs, "trap", True)[0]] == ["riser", "impact"]
chl = [k for k, _ in C._ec_pick(L[:1], {"section", "hook", "drop"}, ecl, "trap")[0]]
assert chl[0] == "riser" and len(chl) <= ecl["cfg"]["per_boundary"] and "gap" in chl, chl   # lots spreads
assert sum(1 for k in chl if ECD["kinds"][k]["role"] == "riser") == 1 and \
    sum(1 for k in chl if ECD["kinds"][k]["role"] == "hit") <= 1, chl
assert not C._ec_pick([("downlifter", {})], {"section", "hook"}, ecn, "trap")[0]     # a fall has no job into a hook
# the snare build: 8ths -> 16ths -> 32nds, the velocity rising
rows = C.snare_build_rows(3, 38, 8)
st = [r[1] for r in rows]; dif = [round(b - a, 4) for a, b in zip(st, st[1:])]
assert st[0] == 0.0 and st[-1] < 8 and dif[:8] == [0.5] * 8 and dif[8:16] == [0.25] * 8 and set(dif[16:]) == {0.125}, dif
assert all(b[4] >= a[4] for a, b in zip(rows, rows[1:])) and rows[-1][4] - rows[0][4] > 60 and {r[0] for r in rows} == {3}
assert len(C.snare_build_rows(3, 38, 4)) == 16
print("ear candy rules ok")

if not os.path.isfile(FIX):
    print("skipped: writer tests (no fixture)"); print("ok"); sys.exit(0)

# ----------------------------------------------------------------------------- flh_fx: the real one, or a stand-in
calls = []
FX = C._fx_mod()
if FX is None:                                             # not written yet: a silent WAV of the exact length
    import types
    FX = types.ModuleType("flh_fx")
    FX.FX_KINDS = {k: {"ends_on": "downbeat" if C.FX_ANCHOR[k] == "end" else "after", "default_beats": C.FX_BEATS[k],
                       "about": k} for k in C.FX_AUDIO}

    def _make(kind, bpm, beats, out_dir, key=None, seed=1, intensity=1.0, sample=None):
        n = int(round(beats * 60.0 / bpm * 44100))
        p = os.path.join(out_dir, f"{C.FX_NAMES[kind]} {beats:g} beats {bpm:g} bpm.wav")
        with wave.open(p, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100); w.writeframes(b"\0" * 4 * n)
        return {"path": p, "kind": kind, "seconds": n / 44100.0, "beats": float(beats), "peak_at_s": 0.0,
                "ends_on": FX.FX_KINDS[kind]["ends_on"]}
    FX.make_fx = _make
    sys.modules["flh_fx"] = FX
real_make = FX.make_fx


def rec_make(kind, bpm, beats, out_dir, **kw):
    calls.append((kind, bpm, beats, kw.get("key"), kw.get("sample")))
    return real_make(kind, bpm, beats, out_dir, **kw)


FX.make_fx = rec_make
reloads = []


def fake(project, output, fn):
    if m._EDIT_DEPTH:
        return fn()
    reloads.append(output); return fn()


m._with_open_project = fake


def noise_wav(path, secs=1.5):
    x = (np.random.RandomState(3).randn(int(44100 * secs)) * 0.2 * np.linspace(1, 0, int(44100 * secs)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def song(name, genre, key):
    """A beat project: the fixture emptied, named channels (a Crash sampler with a real sample), a composed beat."""
    folder = os.path.join(tmp, name); os.makedirs(folder)
    p = os.path.join(folder, name + ".flp"); shutil.copy(FIX, p)
    m.flp_edit_notes(p, [{"op": "delete"}], pattern=1)
    m.flp_playlist(p, "remove", clips=[c["id"] for c in m.flp_playlist(p)["clips"]])
    names = ["Kick", "Snare", "Hat", "Open Hat", "808", "Keys", "Lead"]
    n0 = max(C.channels_of(m._load(p))) + 1
    m.flp_edit(p, [{"tool": "flp_add_channel", "clone_from": 0} for _ in names] +
               [{"tool": "flp_style_channels", "channels": [{"channel": n0 + i, "name": n} for i, n in enumerate(names)]}])
    crash = os.path.join(tmp, "kit", "Crash 01.wav"); noise_wav(crash)
    N.flp_load_sample(p, p, crash, name="Crash")
    r = TOOLS["flp_compose"][0](project=p, what="beat", genre=genre, key=key, seed=7)
    assert r["verified"], r.get("verify_problems")
    return p


def clips(p, name):
    return sorted((c["bar"], c["length_bars"], c["track"]) for c in m.flp_playlist(p)["clips"] if c["name"] == name)


arrange = TOOLS["flp_arrange_song"][0]
FORM = "Intro:4, Hook:8, Verse:8, Hook:8, Outro:4"          # Hooks at bars 5 and 21

# ----------------------------------------------------------------------------- trap: riser / impact / sub drop
src = song("trapsong", "trap", "C# minor")
d = m._load(src); P = m._ppq(d)
before = open(src, "rb").read()
dr = arrange(project=src, form=FORM, mode="replace", block="Beat", dry_run=True)
assert open(src, "rb").read() == before and not os.path.isdir(os.path.join(os.path.dirname(src), "_fx")), "dry run writes nothing"
assert not calls and any(t["kind"] == "riser" and t["file"] is None for t in dr["transitions"]), dr["transitions"]
n0 = len(reloads)
ar = arrange(project=src, form=FORM, mode="replace", block="Beat")
assert len(reloads) == n0 + 1, "one project save / FL reload per arrangement"
assert ar["verified"], ar.get("verify_problems")
tempo = m.flp_info(src)["tempo_bpm"]
tr = ar["transitions"]
fxs = [t for t in tr if t["kind"] in C.FX_AUDIO]
print("trap transitions:", [(t["kind"], t["bar"], t.get("beats"), t.get("file")) for t in fxs])
for k in ("riser", "impact", "downlifter"):
    assert any(t["kind"] == k for t in fxs), (k, tr)
assert not any(t["kind"] == "sub_drop" for t in fxs), fxs      # one big hit per change: the impact (listed first)
sk = ar["ear_candy"]["skipped"]
assert any(x["kind"] == "sub_drop" and "big hit" in x["why"] and x["bar"] == 21.0 for x in sk), sk
assert ar["ear_candy"]["amount"] == "normal" and ar["ear_candy"]["amount_from"] == "genre trap", ar["ear_candy"]
assert all(t.get("purpose") for t in tr), [t for t in tr if not t.get("purpose")]
assert next(t for t in fxs if t["kind"] == "riser" and t["bar"] == 20.0)["purpose"] == "into Hook at bar 21"
assert next(t for t in fxs if t["kind"] == "downlifter" and t["bar"] == 13.0)["purpose"] == "out of Hook into Verse at bar 13"
assert all(t["file"] and t["beats"] and t["channel"] is not None for t in fxs), fxs
mk = {c[0]: c for c in calls}
assert mk["riser"][2] == 4.0 and abs(mk["riser"][1] - tempo) < 1e-6 and mk["riser"][3] == "C# minor", mk["riser"]
assert mk["downlifter"][2] == 4.0
assert {c[0] for c in calls} == {"riser", "impact", "downlifter"}, calls   # one file per kind
assert len(calls) == 3
hook_ticks = [int(round((b - 1) * 4 * P)) for b in (5.0, 21.0)]
ris = clips(src, "Riser")
assert len(ris) == 2, ris                                   # into both hooks
for (b, L, _), ht in zip(ris, hook_ticks):
    assert int(round((b - 1) * 4 * P)) + int(round(L * 4 * P)) == ht, (b, L, ht)   # ends on the Hook's first tick
    assert abs(L - 1.0) < 1e-9                              # 4 beats
for nm_ in ("Impact",):
    cl = clips(src, nm_)
    assert [int(round((b - 1) * 4 * P)) for b, _, _ in cl] == hook_ticks, (nm_, cl)   # starts on the downbeat
dl = clips(src, "Downlifter")
assert [b for b, _, _ in dl] == [13.0, 29.0], dl             # out of each Hook (into the Verse / Outro)
assert len({c[2] for k in ("Riser", "Impact", "Downlifter") for c in clips(src, k)}) == 3   # own tracks
assert not clips(src, "Sub Drop")
# one sampler channel per kind, into one FX insert, playing the whole WAV
chs = C.channels_of(m._load(src))
by_name = {}
for c, x in chs.items():
    by_name.setdefault(x["name"], []).append(c)
fxch = {k: by_name[C.FX_NAMES[k]] for k in ("riser", "impact", "downlifter")}
assert all(len(v_) == 1 for v_ in fxch.values()), fxch
fxch = {k: v_[0] for k, v_ in fxch.items()}
assert all(chs[c]["type"] == 0 for c in fxch.values())
pats = {p_["name"]: p_ for p_ in m.flp_patterns(src)}
for k, c in fxch.items():
    assert set(pats[C.FX_NAMES[k]]["notes_per_channel"]) == {c}, (k, pats[C.FX_NAMES[k]])
dd = m._load(src); ev = m._events(dd)
props = {k: N._chan_props(dd, ev, c) for k, c in fxch.items()}
ins = {p_["insert"] for p_ in props.values()}
assert len(ins) == 1 and next(iter(ins)) > 0, props
assert m._mixer_names(dd, ev).get(next(iter(ins))) == "FX", m._mixer_names(dd, ev)
assert all(p_["color"] and max(int(p_["color"][i:i + 2], 16) for i in (1, 3, 5)) < 0x80 for p_ in props.values()), props
for k, c in fxch.items():
    st = SM.sampler_read(src, c)
    assert st.get("envelope") is not True and st.get("reverse") is not True and st.get("cut_itself") is not True, (k, st)
    assert st.get("envelope") is False and st.get("cutoff_pct") == 100.0, (k, st)   # the whole WAV plays
    sp = N._chan_sample_path(dd, ev, c)
    assert os.path.dirname(sp) == os.path.join(os.path.dirname(src), "_fx"), sp
    with wave.open(sp) as w:
        assert w.getnframes() == int(round(mk[k][2] * 60.0 / tempo * 44100)), (k, w.getnframes())
# the crash sample of the kit is what a reverse crash would reverse (lofi below); the journal knows the FX channels
jr = flh_store.journal_get(src)
assert jr["compose"]["fx_channels"] == fxch, jr["compose"].get("fx_channels")
assert all(jr["channels"][str(c)]["role"] == "fx" for c in fxch.values())
roles = C.project_view(src)["roles"]
assert all(roles[c] == "fx" for c in fxch.values()), {c: roles[c] for c in fxch.values()}   # 'Sub Drop' is no bass
assert any(n_.startswith("filter moves skipped") for n_ in ar.get("notes", [])), ar.get("notes")   # no FL plugin db here
# arrange again (replace): the same channels are reused, nothing doubles
n_ch = len(chs)
calls.clear()
ar2 = arrange(project=src, form=FORM, mode="replace", block="Beat")
assert ar2["verified"], ar2.get("verify_problems")
chs2 = C.channels_of(m._load(src))
assert len(chs2) == n_ch, (n_ch, len(chs2))
assert {k: c for k, c in flh_store.journal_get(src)["compose"]["fx_channels"].items()} == fxch
assert clips(src, "Riser") == ris and len(clips(src, "Impact")) == 2

# ----------------------------------------------------------------------------- filter moves (plugin db stand-in)
fd = m._load(FIX); fev = m._events(fd)
fx_k = [k for k, e in enumerate(fev) if e["id"] == 201 and "Fruity" in m._text16(m._payload(fd, e))
        and any(fev[j]["id"] == m.EV_PLUGIN_HEADER for j in range(k, min(k + 4, len(fev))))]
if fx_k:
    k0 = fx_k[-1]
    k1 = next(k for k in range(k0, len(fev)) if fev[k]["id"] == 213)
    body = m._data_event(201, "Fruity Filter".encode("utf-16-le") + b"\0\0") + b"".join(m._raw(fd, e) for e in fev[k0 + 1:k1 + 1])
    fst = os.path.join(tmp, "plugdb", "Fruity Filter.fst"); os.makedirs(os.path.dirname(fst))
    with open(fst, "wb") as f:
        f.write(b"FLhd" + struct.pack("<I", 6) + struct.pack("<hHH", 0, 0, 96) + b"FLdt" + struct.pack("<I", len(body)) + body)
    real_find = N._find_plugin
    N._find_plugin = lambda name: {"default": fst} if C._mod("flh_catalog")._norm(name) == C._mod("flh_catalog")._norm("Fruity Filter") else real_find(name)
    try:
        n0 = len(reloads)
        ar3 = arrange(project=src, form=FORM, mode="replace", block="Beat")
    finally:
        N._find_plugin = real_find
    assert len(reloads) == n0 + 1 and ar3["verified"], ar3.get("verify_problems")
    fl_ = [t for t in ar3["transitions"] if t["kind"].startswith("filter")]
    assert [t["kind"].split(" ")[1] for t in fl_] == ["intro", "high-pass", "outro"], fl_
    assert fl_[1]["bar"] == 19.0, fl_                      # 8 beats before the last Hook (bar 21)
    autos = [x for x in C.channels_of(m._load(src)).values() if x["type"] == 5 and (x["name"] or "").startswith("Filter")]
    assert len(autos) == 5, [x["name"] for x in autos]     # intro, build cutoff / hp / lp, outro
    fx_on = N._effects(m._load(src))
    assert any(C._mod("flh_catalog")._norm(x.get("plugin")) == C._mod("flh_catalog")._norm("Fruity Filter") for x in fx_on.values())
    ar4 = arrange(project=src, form=FORM, mode="replace", block="Beat")     # again: same filter, clips replaced
    N._find_plugin = real_find
    autos2 = [x for x in C.channels_of(m._load(src)).values() if x["type"] == 5 and (x["name"] or "").startswith("Filter")]
    assert len(autos2) == 5 and ar4["verified"], [x["name"] for x in autos2]
    print("filter moves:", [(t["kind"], t["bar"]) for t in fl_])
else:
    print("skipped: filter moves (the fixture has no effect plugin to stand in for Fruity Filter)")

# ----------------------------------------------------------------------------- lofi: soft (reverse crash, tape stop)
calls.clear()
lo = song("lofisong", "lofi", "A minor")
al = arrange(project=lo, form="Intro:4, A:8, B:8, Outro:4", mode="replace", block="Beat")
assert al["verified"], al.get("verify_problems")
ks = [t["kind"] for t in al["transitions"]]
assert "riser" not in ks and "impact" not in ks and "sub_drop" not in ks, ks
assert "reverse_crash" in ks and "tape_stop" in ks, ks
rc = next(c for c in calls if c[0] == "reverse_crash")
assert rc[4] and os.path.basename(rc[4]) == "Crash 01.wav", rc       # the kit's own crash, reversed
ts = clips(lo, "Tape Stop")
assert len(ts) == 1 and abs(ts[0][0] + ts[0][1] - 25.0) < 1e-9, ts  # ends on the song's last downbeat
rcl = clips(lo, "Reverse Crash")
assert rcl and abs(rcl[0][0] + rcl[0][1] - 13.0) < 1e-9, rcl         # ends on B's downbeat

# ----------------------------------------------------------------------------- nothing inside a section; snare build
def bounds_of(res):
    return {round(r["bar"], 3) for r in res["sections"]} | {round(res["start_bar"] + res["bars"], 3)}


def sec_at(res, bar):
    return next(r for r in res["sections"] if r["bar"] - 1e-6 <= bar < r["bar"] + r["bars"] - 1e-6)


def check_purpose(res, p):
    """Every placed effect says what it points at; every FX clip touches a section change (beds: whole quiet
    sections); never two risers sounding at once; at most one big hit per change."""
    B = bounds_of(res)
    tr = res["transitions"]
    assert all(t.get("purpose") for t in tr), [t for t in tr if not t.get("purpose")]
    for t in tr:
        k = t["kind"]
        if k in C.FX_BEDS:
            r = sec_at(res, t["bar"])
            assert C._sec_kind(r["section"]) in ECD["rules"]["bed_sections"] and t["bar"] == r["bar"] and \
                t["bars"] == r["bars"], (t, r)
            cl = clips(p, C.FX_NAMES[k])
            inside = [c for c in cl if r["bar"] - 1e-6 <= c[0] < r["bar"] + r["bars"] - 1e-6]
            assert inside and abs(sum(c[1] for c in inside) - r["bars"]) < 1e-6 and \
                all(c[0] + c[1] <= r["bar"] + r["bars"] + 1e-6 for c in inside), (k, inside, r)
        elif k in C.FX_AUDIO:
            cl = [c for c in clips(p, C.FX_NAMES[k]) if abs(c[0] - t["bar"]) < 1e-6]
            assert cl, (k, t)
            b0, L_ = cl[0][0], cl[0][1]
            assert round(b0, 3) in B or round(b0 + L_, 3) in B or round(b0 + L_ / 2, 3) in B, (k, b0, L_, B)
        elif k == "snare_build":
            pn = f"Snare Build {t['beats']:g}"
            cl = [c for c in clips(p, pn) if abs(c[0] - t["bar"]) < 1e-6]
            nxt = min(b for b in B if b > t["bar"])
            assert cl and nxt - (cl[0][0] + cl[0][1]) < 0.51 and nxt - cl[0][0] <= 2.0 + 1e-6, (t, cl, nxt)
            assert C._sec_kind(t["into"]) in ("hook", "drop", "build"), t      # its purpose: into a hook / drop
    all_fx = [(t["kind"], c) for t in tr if t["kind"] in C.FX_AUDIO and t["kind"] not in C.FX_BEDS
              for c in clips(p, C.FX_NAMES[t["kind"]]) if abs(c[0] - t["bar"]) < 1e-6]
    ris = [(k, c) for k, c in all_fx if ECD["kinds"][k]["role"] == "riser"]
    for i, (k1, c1) in enumerate(ris):
        for k2, c2 in ris[i + 1:]:
            assert c1[0] + c1[1] <= c2[0] + 1e-6 or c2[0] + c2[1] <= c1[0] + 1e-6, ("stacked risers", k1, c1, k2, c2)
    hits = {}
    for t in tr:
        if t["kind"] in C.FX_AUDIO and ECD["kinds"][t["kind"]]["role"] == "hit":
            hits.setdefault(t["purpose"], []).append(t["kind"])
    assert all(len(x) == 1 for x in hits.values()), hits


check_purpose(ar, src)
sb = sorted([t for t in ar["transitions"] if t["kind"] == "snare_build"], key=lambda t: t["bar"])
assert [(t["bar"], t["beats"]) for t in sb] == [(3.0, 8.0), (19.0, 6.0)], sb     # 2 bars; the 2nd stops at the dropout
dm_ = flh_store.journal_get(src)["compose"].get("drum_map") or {}
snare_ch = int((dm_.get("snare") or [None])[0]) if dm_.get("snare") else sb[0]["channel"]
assert sb[0]["channel"] == snare_ch
pv = C.project_view(src)
for t in sb:
    pat = next(x for x in pv["patterns"].values() if x.get("name") == f"Snare Build {t['beats']:g}")
    rec = sorted(pat["notes"], key=lambda r: r[0])
    assert {int(r[2]) for r in rec} == {snare_ch}
    pos = [r[0] / pv["P"] for r in rec]; dif = sorted({round(b - a, 4) for a, b in zip(pos, pos[1:])}, reverse=True)
    assert dif == [0.5, 0.25, 0.125], dif                   # 8ths -> 16ths -> 32nds
    vel = [r[11] for r in rec]
    assert all(b >= a for a, b in zip(vel, vel[1:])) and vel[-1] - vel[0] > 50, vel
    assert pos[-1] < t["beats"]
sbc = clips(src, "Snare Build 8")
assert sbc and sbc[0][:2] == (3.0, 2.0), sbc
sb_tracks = {c[2] for c in sbc + clips(src, "Snare Build 6")}
assert len(sb_tracks) == 1 and not [c for c in m.flp_playlist(src)["clips"] if c["track"] in sb_tracks
                                    and not str(c["name"]).startswith("Snare Build")]   # a track of its own
print("snare builds:", [(t["bar"], t["beats"], t["into"]) for t in sb])

# ----------------------------------------------------------------------------- amounts on the same song
src_a = song("amountsong", "trap", "C# minor")
res_a = {}
for a in C.EC_AMOUNTS:
    calls.clear()
    res_a[a] = arrange(project=src_a, form=FORM, mode="replace", block="Beat", ear_candy=a, dry_run=True)

    assert res_a[a]["ear_candy"]["amount"] == a and res_a[a]["ear_candy"]["amount_from"] == "given"
cnt = {a: {} for a in res_a}
for a, r in res_a.items():
    for t in r["transitions"]:
        if t["kind"] in C.FX_AUDIO + C.EC_MOVES and t["kind"] not in C.FX_BEDS + ("delay_throw",):
            cnt[a][t["purpose"]] = cnt[a].get(t["purpose"], 0) + 1
assert max(cnt["subtle"].values()) <= 2 and cnt["subtle"]["into Hook at bar 5"] == 1, cnt["subtle"]   # 2: the climax
assert cnt["subtle"]["into Hook at bar 21"] == 2 and cnt["normal"]["into Hook at bar 21"] == 3, cnt
assert sum(cnt["subtle"].values()) < sum(cnt["normal"].values()) <= sum(cnt["lots"].values()), cnt
assert max(cnt["lots"].values()) <= ECD["amounts"]["lots"]["climax_per_boundary"]
assert res_a["subtle"]["ear_candy"]["fx_level_db"] == res_a["normal"]["ear_candy"]["fx_level_db"] - 6
assert res_a["lots"]["ear_candy"]["fx_level_db"] == res_a["normal"]["ear_candy"]["fx_level_db"] + 2
ws = arrange(project=src_a, form=FORM, mode="replace", block="Beat", ear_candy="subtle")
assert ws["verified"], ws.get("verify_problems")
check_purpose(ws, src_a)
dd = m._load(src_a); ev = m._events(dd)
fx_ins = next(t for t, n in m._mixer_names(dd, ev).items() if n == "FX")
assert abs(m._track_volume_db(dd, fx_ins) - ws["ear_candy"]["fx_level_db"]) < 0.3, (m._track_volume_db(dd, fx_ins), ws["ear_candy"])
print("amounts:", {a: sum(c.values()) for a, c in cnt.items()}, "levels:",
      {a: r["ear_candy"]["fx_level_db"] for a, r in res_a.items()})

# ----------------------------------------------------------------------------- delay throws (a Delay 3 stand-in)
lead_ch = flh_store.journal_get(src_a)["compose"]["channels"]["melody"]
dd = m._load(src_a); ev = m._events(dd)
ins_lead = N._chan_props(dd, ev, lead_ch)["insert"]
set_ins = lambda i_: m._save(N._chan_edit(m._load(src_a), lead_ch, {N.EV_INSERT: struct.pack("<h", int(i_))}), src_a)
if not ins_lead:                                            # throws need the lead on an insert of its own
    ins_lead = N._free_insert(dd, ev)
set_ins(0)                                                  # the lead on the Master: the throws are planned, said
th0 = arrange(project=src_a, form=FORM, mode="replace", block="Beat", ear_candy="normal", dry_run=True)
assert th0["ear_candy"].get("planned") and any("Master" in n_ for n_ in th0.get("notes", [])), th0.get("ear_candy")
assert all(x["kind"] == "delay_throw" and "lead's last note before" in x["purpose"] for x in th0["ear_candy"]["planned"])
assert not [t for t in th0["transitions"] if t["kind"] == "delay_throw"]
set_ins(ins_lead)
if fx_k:
    body = m._data_event(201, "Fruity Delay 3".encode("utf-16-le") + b"\0\0") + b"".join(m._raw(fd, e) for e in fev[k0 + 1:k1 + 1])
    dst_ = os.path.join(tmp, "plugdb", "Fruity Delay 3.fst")
    with open(dst_, "wb") as f:
        f.write(b"FLhd" + struct.pack("<I", 6) + struct.pack("<hHH", 0, 0, 96) + b"FLdt" + struct.pack("<I", len(body)) + body)
    real_find = N._find_plugin
    nrm = C._mod("flh_catalog")._norm
    N._find_plugin = lambda name: {"default": dst_} if nrm(name) == nrm("Fruity Delay 3") else real_find(name)
    try:
        th = arrange(project=src_a, form=FORM, mode="replace", block="Beat", ear_candy="normal")
    finally:
        N._find_plugin = real_find
    assert th["verified"], th.get("verify_problems")
    thr = [t for t in th["transitions"] if t["kind"] == "delay_throw"]
    assert 1 <= len(thr) <= ECD["amounts"]["normal"]["throws_per_song"], thr
    for t in thr:                                           # on the lead's last note, within 2 bars of the change
        nxt = min(b for b in bounds_of(th) if b > t["bar"])
        assert 0 < nxt - t["bar"] <= 2.0 and t["purpose"] == f"the lead's last note before {t['into']} at bar {nxt:g}", t
        assert f"insert {ins_lead}" in t["on"] and "Fruity Delay 3" in t["on"]
    assert th["ear_candy"]["delay_throws"].startswith(f"Fruity Delay 3 wet on insert {ins_lead}")
    on_ins = [s_ for (t_, s_), x in N._effects(m._load(src_a)).items() if t_ == ins_lead and nrm(x.get("plugin")) == nrm("Fruity Delay 3")]
    assert len(on_ins) == 1, N._effects(m._load(src_a))
    dd = m._load(src_a); ev = m._events(dd)
    au = [c for c, x in C.channels_of(dd).items() if x["type"] == 5 and (x["name"] or "") == "Delay throws @1"]
    assert len(au) == 1, [x["name"] for x in C.channels_of(dd).values() if x["type"] == 5]
    pts, _, _ = m._auto_points(m._payload(dd, ev[m._automation_events(dd, ev)[au[0]]]))
    vals = [round(q[1], 4) for q in pts]
    assert vals[0] == 0.0 and vals[-1] == 0.0 and max(vals) > 0.1 and vals.count(max(vals)) == 2 * len(thr), vals
    assert flh_store.journal_get(src_a)["compose"]["delay_throw"]["track"] == ins_lead
    th2 = arrange(project=src_a, form=FORM, mode="replace", block="Beat", ear_candy="normal")   # again: same delay
    on_ins2 = [s_ for (t_, s_), x in N._effects(m._load(src_a)).items() if t_ == ins_lead and nrm(x.get("plugin")) == nrm("Fruity Delay 3")]
    au2 = [x for x in C.channels_of(m._load(src_a)).values() if x["type"] == 5 and (x["name"] or "") == "Delay throws @1"]
    assert on_ins2 == on_ins and len(au2) == 1 and th2["verified"]
    print("delay throws:", [(t["bar"], t["into"]) for t in thr])
else:
    print("skipped: delay throws (the fixture has no effect plugin to stand in for Fruity Delay 3)")

# ----------------------------------------------------------------------------- textures: only under quiet sections
calls.clear()
lb = song("lofibeds", "lofi", "A minor")
lbr = arrange(project=lb, form="Intro:4, A:8, B:8, Bridge:4, A:8, Outro:8", mode="replace", block="Beat", ear_candy="lots")
assert lbr["verified"], lbr.get("verify_problems")
check_purpose(lbr, lb)
beds = [t for t in lbr["transitions"] if t["kind"] in C.FX_BEDS]
assert {t["kind"] for t in beds} == {"vinyl"} and sorted(t["into"] for t in beds) == ["Bridge", "Intro", "Outro"], beds
vc = clips(lb, "Vinyl Bed")
assert all(c[1] == 4.0 for c in vc) and len(vc) == 1 + 1 + 2, vc    # 4-bar loops tile 4 + 4 + 8 bars exactly
assert lbr["ear_candy"]["texture_insert"].startswith("Texture") and \
    lbr["ear_candy"]["texture_level_db"] < lbr["ear_candy"]["fx_level_db"] - 4, lbr["ear_candy"]
vk = next(c for c in calls if c[0] == "vinyl")
assert vk[2] == 16.0                                        # one loop = 4 bars of 4/4
dd = m._load(lb); ev = m._events(dd)
tins = next(t for t, n in m._mixer_names(dd, ev).items() if n == "Texture")
assert abs(m._track_volume_db(dd, tins) - lbr["ear_candy"]["texture_level_db"]) < 0.3
vch = flh_store.journal_get(lb)["compose"]["fx_channels"]["vinyl"]
assert N._chan_props(dd, ev, vch)["insert"] == tins and flh_store.journal_get(lb)["channels"][str(vch)]["mixer"] == tins
sub = arrange(project=lb, form="Intro:4, A:8, B:8, Bridge:4, A:8, Outro:8", mode="replace", block="Beat", dry_run=True)
assert sub["ear_candy"]["amount"] == "lots" and sub["ear_candy"]["amount_from"] == "song journal"   # remembered
assert flh_store.journal_get(lb)["compose"]["ear_candy"] == "lots"
sub = arrange(project=lb, form="Intro:4, A:8, B:8, Bridge:4, A:8, Outro:8", mode="replace", block="Beat", dry_run=True,
              ear_candy="subtle")
assert sorted(t["into"] for t in sub["transitions"] if t["kind"] in C.FX_BEDS) == ["Intro", "Outro"]   # subtle: no bridge
print("beds:", [(t["kind"], t["into"], t["bar"], t["bars"]) for t in beds])

# ----------------------------------------------------------------------------- the old synthetic cinematic set (asked)
calls.clear()
cin = song("cinesong", "cinematic", "D minor")
cr = arrange(project=cin, form="Intro:4, A:8, Build:4, Climax:8, Outro:4", mode="replace", block="Beat",
             ear_candy="synthetic")
assert cr["verified"], cr.get("verify_problems")
check_purpose(cr, cin)
assert cr["ear_candy"]["mode"] == "synthetic" and cr["ear_candy"]["amount"] == "subtle" and \
    cr["ear_candy"]["profile"].startswith("synthetic_cinematic"), cr["ear_candy"]
ek = sorted((t["kind"], t["purpose"]) for t in cr["transitions"] if t["kind"] in C.FX_AUDIO)
assert ek == [("boom", "into Climax at bar 17"), ("cymbal_swell", "into Build at bar 13"),
              ("reverse_swell", "into A at bar 5"), ("reverse_swell", "into Climax at bar 17"),   # A: a big change
              ("wind", "under Intro at bar 1"), ("wind", "under Outro at bar 25")], ek
assert not {"riser", "impact", "sub_drop"} & {t["kind"] for t in cr["transitions"]}
rs = next(c for c in calls if c[0] == "reverse_swell")
assert rs[3] == "D minor"                                   # the key's chord swells in
assert flh_store.journal_get(cin)["compose"]["ear_candy"] == "synthetic"   # remembered: asked for
assert arrange(project=cin, form="Intro:4, A:8, Build:4, Climax:8, Outro:4", mode="replace", block="Beat",
               dry_run=True)["ear_candy"]["mode"] == "synthetic"
calls.clear()
pop_ = song("pianosong", "pop", "F major")
flh_store.journal_update(pop_, set={"role": "piano-led closer"})
pr = arrange(project=pop_, form="Intro:4, Verse:8, Hook:8, Outro:4", mode="replace", block="Beat", dry_run=True)
assert pr["ear_candy"]["mode"] == "organic" and pr["ear_candy"]["amount"] == "subtle", pr["ear_candy"]
assert "piano-led" in pr["ear_candy"]["mode_from"], pr["ear_candy"]
pk = {t["kind"] for t in pr["transitions"]}
assert not pk & set(C.FX_AUDIO + C.EC_MOVES) and not calls, pk          # no synthetic FX, no audio made
assert any(x["move"] == "string_swell" and "strings" in x["do"] for x in pr["ear_candy"].get("missing") or []), \
    pr["ear_candy"]                                         # the move it couldn't play says what would
print("synthetic cinematic (asked):", ek)

# ----------------------------------------------------------------------------- organic: real instruments as notes
def osong(name, names, genre="cinematic", key="D minor"):
    """A piece with the given instrument channels (cloned, named) and a composed beat on them."""
    folder = os.path.join(tmp, name); os.makedirs(folder)
    p = os.path.join(folder, name + ".flp"); shutil.copy(FIX, p)
    m.flp_edit_notes(p, [{"op": "delete"}], pattern=1)
    m.flp_playlist(p, "remove", clips=[c["id"] for c in m.flp_playlist(p)["clips"]])
    n0 = max(C.channels_of(m._load(p))) + 1
    m.flp_edit(p, [{"tool": "flp_add_channel", "clone_from": 0} for _ in names] +
               [{"tool": "flp_style_channels", "channels": [{"channel": n0 + i, "name": n} for i, n in enumerate(names)]}])
    r = TOOLS["flp_compose"][0](project=p, what="beat", genre=genre, key=key, seed=7)
    assert r["verified"], r.get("verify_problems")
    return p


def wavs_under(folder):
    return [f for _r, _d, fs in os.walk(folder) for f in fs if f.lower().endswith(".wav")]


def check_organic(res, p, pre):
    """Every organic move: on a channel the song had, in the bars just before a section change and ending on its
    downbeat (a timpani roll lands on it, one beat), velocities rising and under the piano's."""
    B = bounds_of(res)
    pv = C.project_view(p)
    pats_ = {x["name"]: x for x in pv["patterns"].values()}
    out = [t for t in res["transitions"] if t["kind"] in C.ORGANIC_MOVES]
    for t in out:
        assert t["channel"] in pre and pre[t["channel"]]["type"] != 5, t
        nxt = min(b for b in B if b > t["bar"] + 1e-9)
        cl = [c for c in clips(p, t["pattern"]) if abs(c[0] - t["bar"]) < 1e-6]
        assert len(cl) == 1, (t, clips(p, t["pattern"]))
        end = cl[0][0] + cl[0][1]
        sec = sec_at(res, t["bar"])
        assert abs(sec["bar"] + sec["bars"] - nxt) < 1e-6, (t, sec)          # in the section BEFORE the change
        if t["kind"] == "timpani_roll":
            assert 0 < end - nxt <= 0.25 + 1e-6, (t, cl)                     # lands on the downbeat (one beat)
        else:
            assert abs(end - nxt) < 1e-6, (t, cl)                            # ends on it
        assert t["purpose"] == f"into {t['into']} at bar {nxt:g}", t
        rec = sorted(pats_[t["pattern"]]["notes"], key=lambda r: (r[0], r[4]))
        assert rec and {int(r[2]) for r in rec} == {t["channel"]}, (t, rec)
        vel = [int(r[11]) for r in rec]
        assert all(b_ >= a_ for a_, b_ in zip(vel, vel[1:])) and vel[-1] > vel[0], (t["kind"], vel)   # a ramp
        assert max(vel) < res["ear_candy"]["piano_velocity"], (t["kind"], vel, res["ear_candy"])      # faint
    return out


calls.clear()
og = osong("organicsong", ["Piano", "Strings", "Timpani", "Choir"])
OFORM = "Intro:4, A:8, Build:4, Climax:8, Outro:4"           # changes at 5, 13, 17 (the climax), 25
pre_og = C.channels_of(m._load(og))
o1 = arrange(project=og, form=OFORM, mode="replace", block="Beat")
assert o1["verified"], o1.get("verify_problems")
e1 = o1["ear_candy"]
assert e1["mode"] == "organic" and e1["mode_from"] == "genre cinematic" and e1["amount"] == "subtle", e1
assert not calls and not wavs_under(os.path.dirname(og)), "organic: no audio made"
assert not [t for t in o1["transitions"] if t["kind"] in C.FX_AUDIO + C.EC_MOVES], o1["transitions"]
assert not [t for t in o1["transitions"] if str(t["kind"]).startswith("filter")]
mv1 = check_organic(o1, og, pre_og)
per = {}
for t in mv1:
    per[t["purpose"]] = per.get(t["purpose"], 0) + 1
assert mv1 and max(per.values()) == 1, per                  # subtle: one move per change
assert "into Climax at bar 17" in per, per                  # the climax always gets its move
kinds1 = {t["kind"]: t for t in mv1}
assert kinds1.get("string_swell") and kinds1["string_swell"]["on"] in ("Strings", "Low Strings"), mv1
assert next(t for t in mv1 if t["purpose"] == "into Climax at bar 17")["kind"] == "string_swell", mv1
sw = next(t for t in mv1 if t["kind"] == "string_swell")
pat_sw = next(x for x in C.project_view(og)["patterns"].values() if x["name"] == sw["pattern"])
st_ = sorted({r[0] for r in pat_sw["notes"]})
assert len(st_) >= 2, st_                                   # the voices enter one after another
assert any(x["move"] == "cymbal_roll" and "suspended cymbal" in x["needs"] for x in e1.get("missing") or []), e1
post_og = C.channels_of(m._load(og))
added = set(post_og) - set(pre_og)
assert added and all(post_og[c]["type"] == 5 for c in added), {c: post_og[c] for c in added}   # only the ramp clip
ramp = [post_og[c]["name"] for c in added]
assert all(n_.endswith(" swell @1") for n_ in ramp), ramp
dd = m._load(og); ev = m._events(dd)
au = next(c for c in added)
pts, _, _ = m._auto_points(m._payload(dd, ev[m._automation_events(dd, ev)[au]]))
vals = [round(q[1], 4) for q in pts]
assert vals[0] < vals[1] and vals[-1] == max(vals), vals   # dips at the swell's start, back on the downbeat
jo = flh_store.journal_get(og)["compose"]
assert set(jo["organic"]["patterns"]) == {t["pattern"] for t in mv1} and jo["organic"]["automation"] == ramp, jo
assert jo["arrangement"]["ear_candy_mode"] == "organic" and "ear_candy" not in jo     # not asked: not pinned
o2 = arrange(project=og, form=OFORM, mode="replace", block="Beat", ear_candy="normal")
assert o2["verified"] and o2["ear_candy"]["amount"] == "normal" and o2["ear_candy"]["mode"] == "organic", o2["ear_candy"]
mv2 = check_organic(o2, og, pre_og)
per2 = {}
for t in mv2:
    per2.setdefault(t["purpose"], []).append(t["kind"])
assert max(len(x) for x in per2.values()) == 2 and len(mv2) > len(mv1), per2   # normal: two per change
for ks_ in per2.values():                                   # one swell, one roll per change
    assert sum(k in ("string_swell", "choir_breath") for k in ks_) <= 1 and \
        sum(k in ("timpani_roll", "cymbal_roll") for k in ks_) <= 1, per2
assert "timpani_roll" in per2["into Climax at bar 17"], per2
tp = next(t for t in mv2 if t["kind"] == "timpani_roll")
tpat = next(x for x in C.project_view(og)["patterns"].values() if x["name"] == tp["pattern"])
assert len({r[4] for r in tpat["notes"]}) == 1 and 40 <= tpat["notes"][0][4] <= 55 and \
    tpat["notes"][0][4] % 12 in (2, 9), tpat["notes"][:2]   # one tuned drum: the key's tonic or dominant (D minor)
assert not wavs_under(os.path.dirname(og)) and flh_store.journal_get(og)["compose"]["ear_candy"] == "normal"
# the subtle level is lower than normal's
assert max(int(t["vel"].split("->")[1]) for t in mv1) < max(int(t["vel"].split("->")[1]) for t in mv2)
print("organic:", [(t["kind"], t["bar"], t["on"], t["vel"]) for t in mv2])

# a piano + choir piece (no strings, no timpani): the choir breathes into the climax, the piano picks up
pc_ = osong("pianochoir", ["Piano", "Choir"])
jpc = flh_store.journal_get(pc_)["compose"]
pch_ = int(jpc["channels"]["chords"]); bars_ = int(jpc["blocks"]["Beat"].get("bars") or 4)
held = [[pch_, b_ * 4.0, 4.0, k_, 84, 0] for b_ in range(bars_) for k_ in ((50, 53, 57), (46, 50, 53), (45, 48, 52),
                                                                           (48, 52, 55))[b_ % 4]]
TOOLS["flp_compose_write"][0](project=pc_, output=pc_, pattern="Beat Chords", notes=held, clear_channels=[pch_])
bch_ = int(jpc["channels"]["bass"])                          # the bass on the same roots, no melody: a clear harmony
pv_ = C.project_view(pc_)
bpat = next(x["name"] for x in pv_["patterns"].values() if x["name"].startswith("Beat ") and
            {int(r[2]) for r in x["notes"]} == {bch_})
TOOLS["flp_compose_write"][0](project=pc_, output=pc_, pattern=bpat, clear_channels=[bch_],
                              notes=[[bch_, b_ * 4.0, 4.0, (38, 34, 33, 36)[b_ % 4], 80, 0] for b_ in range(bars_)])
mch_ = int(jpc["channels"]["melody"])
mpat = next(x["name"] for x in pv_["patterns"].values() if {int(r[2]) for r in x["notes"]} == {mch_})
TOOLS["flp_compose_write"][0](project=pc_, output=pc_, pattern=mpat, notes=[], clear_channels=[mch_])
pre_pc = C.channels_of(m._load(pc_))
o3 = arrange(project=pc_, form=OFORM, mode="replace", block="Beat", ear_candy="organic:normal")
assert o3["verified"], o3.get("verify_problems")
mv3 = check_organic(o3, pc_, pre_pc)
k3 = [t["kind"] for t in mv3]
assert "choir_breath" in k3 and "timpani_roll" not in k3, mv3     # (the beat's bass is on a new 'Low Strings')
assert all(t["on"] == "Low Strings" for t in mv3 if t["kind"] == "string_swell"), mv3
assert next(t for t in mv3 if t["kind"] == "choir_breath")["purpose"] == "into Climax at bar 17"
pu = [t for t in mv3 if t["kind"] == "piano_pickup"]
assert pu and len(pu) <= ORG["moves"]["piano_pickup"]["max_per_song"], mv3
pv3 = C.project_view(pc_)
for t in pu:
    assert t["channel"] == pch_ and t["beats"] == 1.0, t
    rec = sorted(next(x for x in pv3["patterns"].values() if x["name"] == t["pattern"])["notes"], key=lambda r: r[0])
    ks = [r[4] for r in rec]
    assert ks == sorted(ks) and len(set(ks)) == len(ks) >= 2, ks          # a rising arpeggio
    bar_i = int(t["bar"] - sec_at(o3, t["bar"])["bar"]) % bars_          # the held chord of that bar
    chord = {k_ % 12 for k_ in ((50, 53, 57), (46, 50, 53), (45, 48, 52), (48, 52, 55))[bar_i % 4]}
    held_k = set(((50, 53, 57), (46, 50, 53), (45, 48, 52), (48, 52, 55))[bar_i % 4])
    stop_ = any(str(x["kind"]).startswith("stop") and abs(x["bar"] - (t["bar"] + 0.25)) < 1e-6 for x in o3["transitions"])
    assert {k_ % 12 for k_ in ks} <= chord and (stop_ or not set(ks) & held_k), (ks, chord)   # its tones, not its keys
    assert 45 <= min(ks) and max(ks) <= 57 + 12, ks         # the piano's own register (an octave over its chords)
mi = {x["move"]: x for x in o3["ear_candy"].get("missing") or []}
assert "timpani_roll" in mi and mi["timpani_roll"]["do"].startswith("add a timpani channel"), mi
assert not wavs_under(os.path.dirname(pc_))
print("piano + choir:", [(t["kind"], t["bar"], t["vel"]) for t in mv3])

# flh_new_song takes organic / off too (and refuses anything else before it builds)
def make_blank(src, dst):
    """FL's empty template stand-in (as tests/test_newsong.py): the fixture without channels, notes or clips."""
    d = m._load(src); ev = m._events(d); keep = []; inside = False
    for e in ev:
        if e["id"] == 64:
            inside = True
        if not inside and e["id"] != 227:
            keep.append(m._data_event(e["id"], b"") if e["id"] in (m.EV_NOTES, m.EV_PLAYLIST) else m._raw(d, e))
        if inside and e["id"] == 51:
            inside = False
    hl = struct.unpack("<I", d[4:8])[0]; head = bytearray(d[:8 + hl]); fmt, _, ppq = struct.unpack("<hHH", head[8:14])
    head[8:14] = struct.pack("<hHH", fmt, 0, ppq); body = b"".join(keep)
    with open(dst, "wb") as f:
        f.write(bytes(head) + b"FLdt" + struct.pack("<I", len(body)) + body)


os.environ["FLH_BLANK"] = os.path.join(tmp, "Empty.flp"); make_blank(FIX, os.environ["FLH_BLANK"])
for bad in ("loads", "organic:loud"):
    try:
        N.flh_new_song("cinematic", name="Bad EC", ear_candy=bad, balance=False, open=False)
        raise AssertionError(f"flh_new_song ear_candy={bad!r} accepted")
    except ValueError as e:
        assert "organic" in str(e)
calls.clear()
nso = N.flh_new_song("cinematic", name="Organic New", seed=3, ear_candy="organic", balance=False, open=False)
jn = flh_store.journal_get(nso["project"])["compose"]
assert jn["arrangement"]["ear_candy_mode"] == "organic" and jn["ear_candy"] == "organic", jn.get("arrangement")
assert not calls and not wavs_under(os.path.dirname(nso["project"]))
nsf = N.flh_new_song("trap", name="No EC New", seed=3, ear_candy="off", balance=False, open=False)
assert "ear_candy" not in flh_store.journal_get(nsf["project"])["compose"]["arrangement"]
assert not os.path.isdir(os.path.join(os.path.dirname(nsf["project"]), "_fx"))

# ----------------------------------------------------------------------------- flp_remove_fx: exactly what was added
remove = TOOLS["flp_remove_fx"][0]
ro = remove(project=og, kinds=["timpani_roll"])             # one kind: only the timpani rolls go
assert ro["removed"] and ro["patterns"] and all(n_.startswith("Timpani Roll") for n_ in ro["patterns"]), ro
assert not [c for c in m.flp_playlist(og)["clips"] if str(c["name"]).startswith("Timpani Roll")]
assert [c for c in m.flp_playlist(og)["clips"] if str(c["name"]).startswith("String Swell")]
assert flh_store.journal_get(og)["compose"]["ear_candy"] == "normal"   # a partial removal changes no default
ra_ = remove(project=og)
assert ra_["removed"] and not ra_.get("files") and "remembered" in ra_, ra_
names_og = {c: x["name"] for c, x in C.channels_of(m._load(og)).items()}
assert names_og == {c: x["name"] for c, x in pre_og.items()}, (names_og, pre_og)   # the channels the song had
left = [x["name"] for x in C.project_view(og)["patterns"].values() if C._ORG_RX.match(x["name"] or "")]
assert not left and not [c for c in m.flp_playlist(og)["clips"] if C._ORG_RX.match(str(c["name"]))], left
jo = flh_store.journal_get(og)
assert jo["compose"]["ear_candy"] == "off" and not jo["compose"].get("organic", {}).get("patterns"), jo["compose"]
assert "ear_candy" not in arrange(project=og, form=OFORM, mode="replace", block="Beat", dry_run=True)   # off now
un = remove(project=og, undo=True)                          # undo: the moves and the journal come back
assert un["journal"] == "restored"
assert [c for c in m.flp_playlist(og)["clips"] if str(c["name"]).startswith("String Swell")]
assert flh_store.journal_get(og)["compose"]["ear_candy"] == "normal"
assert {c: x["name"] for c, x in C.channels_of(m._load(og)).items()} == \
    {c: x["name"] for c, x in post_og.items() if c in pre_og or x["type"] == 5}
assert remove(project=pc_, kinds="filter")["removed"] is False      # nothing of that kind: nothing written
try:
    remove(project=og, kinds=["sparkles"]); raise AssertionError("unknown kind accepted")
except ValueError as e:
    assert "organic" in str(e)

# the synthetic FX of a trap beat: channels, patterns, clips, the FX insert, the plugins it loaded; files offered
calls.clear()
rms = song("removesong", "trap", "C# minor")
lead_r = flh_store.journal_get(rms)["compose"]["channels"]["melody"]
dd = m._load(rms); ev = m._events(dd)
m._save(N._chan_edit(dd, lead_r, {N.EV_INSERT: struct.pack("<h", int(N._free_insert(dd, ev)))}), rms)
snap = lambda p: ({c: x["name"] for c, x in C.channels_of(m._load(p)).items()},
                  sorted((k_, x.get("plugin")) for k_, x in N._effects(m._load(p)).items()),
                  m._mixer_names(m._load(p), m._events(m._load(p))))
ch0, fx0, mx0 = snap(rms)
real_find = N._find_plugin
if fx_k:
    nrm = C._mod("flh_catalog")._norm
    dbs = {nrm("Fruity Filter"): fst, nrm("Fruity Delay 3"): os.path.join(tmp, "plugdb", "Fruity Delay 3.fst")}
    N._find_plugin = lambda name: {"default": dbs[nrm(name)]} if nrm(name) in dbs else real_find(name)
try:
    rr = arrange(project=rms, form=FORM, mode="replace", block="Beat")
finally:
    N._find_plugin = real_find
assert rr["verified"] and rr["ear_candy"]["mode"] == "synthetic", rr.get("ear_candy")
ch1, fx1, mx1 = snap(rms)
made = {c: n_ for c, n_ in ch1.items() if c not in ch0}
pats1 = {x["name"] for x in C.project_view(rms)["patterns"].values()}
fx_pats = {C.FX_NAMES[k] for k in flh_store.journal_get(rms)["compose"]["fx_channels"]} | \
    {n_ for n_ in pats1 if n_.startswith("Snare Build")}
assert made and fx_pats and any(n_ == "FX" for n_ in mx1.values()), (made, fx_pats)
assert flh_store.journal_get(rms)["compose"]["fx_inserts"]["FX"] in mx1
if fx_k:
    assert len(fx1) == len(fx0) + 2, (fx0, fx1)              # the Filter and the Delay 3 it loaded
dr = remove(project=rms, dry_run=True)
assert dr["dry_run"] and {x["channel"] for x in dr["channels"]} == set(made), (dr, made)
assert set(dr["patterns"]) == fx_pats and dr["files"]["count"] == len(set(calls)), (dr, calls)
assert snap(rms)[0] == ch1                                  # a dry run writes nothing
rv = remove(project=rms)
assert rv["removed"] and rv["inserts"] and rv["files"]["count"] == len({c[0] for c in calls}), rv
ch2, fx2, mx2 = snap(rms)
assert ch2 == ch0, (ch0, ch2)                               # exactly the channels it had before
assert fx2 == fx0, (fx0, fx2)                               # the plugins it loaded are gone, nothing else
assert not [n_ for n_ in mx2.values() if n_ in ("FX", "Texture")], mx2
pats2 = {x["name"] for x in C.project_view(rms)["patterns"].values()}
assert pats2 == pats1 - fx_pats, (pats1 - pats2, fx_pats)   # its patterns gone, the arrangement's other parts kept
assert not [c for c in m.flp_playlist(rms)["clips"] if c["name"] in fx_pats or str(c["name"]).startswith(("Filter ", "Delay throws"))]
jr_ = flh_store.journal_get(rms)["compose"]
assert not jr_.get("fx_channels") and not jr_.get("fx_inserts") and not jr_.get("filter") and jr_["ear_candy"] == "off"
assert not [c for c, x in flh_store.journal_get(rms)["channels"].items() if x.get("role") == "fx"]
fxdir = os.path.join(os.path.dirname(rms), "_fx")
n_wav = len(wavs_under(fxdir))
assert n_wav == rv["files"]["count"]                        # offered, not touched
binned = []
ns["files_recycle"] = lambda paths: [binned.append(x) or os.remove(x) for x in paths]
rc_ = remove(project=rms, recycle_files=True)               # the offer taken: nothing else is removed twice
assert rc_["removed"] is False and rc_["files"]["recycled"] == n_wav and not wavs_under(fxdir), rc_
assert snap(rms)[0] == ch0
ub = remove(project=rms, undo=True)
assert snap(rms)[0] == ch1 and flh_store.journal_get(rms)["compose"]["fx_channels"], ub
assert "ear_candy" not in flh_store.journal_get(rms)["compose"] or \
    flh_store.journal_get(rms)["compose"]["ear_candy"] != "off"
print("remove fx:", {"channels": len(made), "patterns": sorted(fx_pats), "wavs": n_wav})

# ----------------------------------------------------------------------------- ear_candy=False / no flh_fx
nf = song("plainsong", "trap", "C# minor")
calls.clear()
before_ch = len(C.channels_of(m._load(nf)))
ap = arrange(project=nf, form=FORM, mode="replace", block="Beat", ear_candy=False)
assert ap["verified"] and not calls and not [t for t in ap["transitions"] if t["kind"] in C.FX_AUDIO], ap["transitions"]
assert any(t["kind"].startswith("dropout") for t in ap["transitions"])  # drum transitions still there
assert len(C.channels_of(m._load(nf))) == before_ch and "ear_candy" not in ap
assert not os.path.isdir(os.path.join(os.path.dirname(nf), "_fx"))
saved = sys.modules.get("flh_fx")
sys.modules["flh_fx"] = None                               # not installed: today's behaviour, said in notes
try:
    nf2 = song("oldsong", "trap", "C# minor")
    a0 = arrange(project=nf2, form=FORM, mode="replace", block="Beat")
finally:
    sys.modules["flh_fx"] = saved
assert a0["verified"] and any("flh_fx" in n_ for n_ in a0.get("notes", [])), a0.get("notes")
assert not [t for t in a0["transitions"] if t["kind"] in ("impact", "sub_drop")]
print("ok")
