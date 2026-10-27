"""Bare FL of every edition: no third-party plugins (no INSTRUMENT / LABS, no FLEX packs, no Kontakt ...), every FL
native installed (FL installs the higher-tier ones as demos), and each edition in turn (fruity, producer, signature,
all_plugins, and an unknown edition): every genre template builds a full song, every part gets a real sound that
edition owns (never a trial plugin, never an empty channel), higher editions get their higher-tier choices, skipped
trial plugins are said out loud, guessed preset names fall back to the plugin's default patch and say so. Plus the
template data rules (stock every-edition tail on every melodic chain) and flh_setup's first-run edition flow.
Run: python3 tests/test_editions.py (no FL needed)."""
import json, os, struct, sys, tempfile, time, wave
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
docs = os.path.join(tmp, "Documents", "Image-Line", "FL Studio"); os.environ["FLH_FL_USERDATA"] = docs
packs = os.path.join(tmp, "FL", "Data", "Patches", "Packs"); os.environ["FLH_FACTORY_PACKS"] = packs
os.environ["FLH_SPLICE_LIB"] = os.path.join(tmp, "Splice", "INSTRUMENT")      # not there: no LABS
os.environ["FLH_FLEX_DIR"] = os.path.join(tmp, "FLEX")                          # not there: no FLEX packs
for k in ("FLH_PLUGIN_DB", "FLH_FL_EDITION"):
    os.environ.pop(k, None)
import numpy as np  # noqa: E402

import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_catalog as C, flh_compose, flh_newsong as N, flh_setup as S  # noqa: E402
ns = vars(m)
for mod in (flh_store, C, flh_compose, N, S):
    mod.register(ns)
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):
    FIX = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
T0 = time.time()

# ---------------------------------------------------------------- template data rules (no FL, no fixtures)
EVERY = {k for k, v in C.NATIVE_MIN.items() if v == "fruity"}
HIGHER = {k for k, v in C.NATIVE_MIN.items() if v in ("signature", "all_plugins")}
MELODIC = lambda spec: spec["role"] not in N.SAMPLER_ROLES or (spec.get("family") == "808")


def chain(rec, spec):
    fam = spec.get("family") or spec["role"]
    return spec.get("choices") or (rec.get("choices") or {}).get(fam) or \
        (rec.get("choices") or {}).get("sample" if spec["role"] in N.SAMPLER_ROLES else "keys") or []


genres = sorted(N.recipes())
assert len(genres) >= 26, genres
for g in genres:
    rec, _ = N.recipe(g)
    for spec in rec["channels"]:
        ch = chain(rec, spec)
        assert ch, (g, spec["name"])
        for c in ch:
            assert set(c) & {"sample", "labs", "flex", "plugin", "fl_instrument"}, (g, c)
            if "plugin" in c:                              # only plugins the edition table knows (or free ones)
                assert C._native_min(c["plugin"]) or C._matches(c["plugin"], C.FREE_PLUGINS), (g, c)
            for p in c.get("presets") or []:               # name patterns only: no shipped preset files / paths
                assert "/" not in p and "\\" not in p and not p.lower().endswith((".fst", ".nmsv")), (g, p)
        if not MELODIC(spec) or any("sample" in c for c in ch[:1]) and spec["role"] in N.SAMPLER_ROLES:
            continue
        stock = [c for c in ch if c.get("plugin") in EVERY]
        assert stock, f"{g} {spec['name']}: no stock plugin every FL edition has ({ch})"
        third = [i for i, c in enumerate(ch) if "labs" in c or "flex" in c or (
            "plugin" in c and not C._native_min(c["plugin"]))]
        assert not third or any(c.get("plugin") in EVERY for c in ch[third[-1] + 1:]), \
            f"{g} {spec['name']}: no every-edition stock choice after the last third-party one ({ch})"
        names = [c.get("plugin") for c in ch]
        if "Sytrus" in names:                              # higher-tier picks come before the Producer stock synth
            for h in HIGHER & set(names):
                assert names.index(h) < names.index("Sytrus"), (g, spec["name"], names)
for g in ("boombap", "cinematic", "jazzhop"):              # the free option most people have first, FLEX right after
    rec, _ = N.recipe(g)
    mel = chain(rec, next(s for s in rec["channels"] if s["role"] == "melody"))
    assert "labs" in mel[0] and "flex" in mel[1], (g, mel[:2])


# ---------------------------------------------------------------- a bare FL install
def raw_events(d, ev, a, b):
    return b"".join(m._raw(d, e) for e in ev[a:b])


def make_flp(events_bytes, nch=0, ppq=96):
    return b"FLhd" + struct.pack("<I", 6) + struct.pack("<hHH", 0, nch, ppq) + b"FLdt" + \
        struct.pack("<I", len(events_bytes)) + events_bytes


def make_blank(src, dst):
    d = m._load(src); ev = m._events(d)
    keep = []; inside = False
    for e in ev:
        if e["id"] == 64:
            inside = True
        if not inside and e["id"] not in (227,):
            if e["id"] == m.EV_NOTES:
                keep.append(m._data_event(m.EV_NOTES, b""))
            elif e["id"] == m.EV_PLAYLIST:
                keep.append(m._data_event(m.EV_PLAYLIST, b""))
            else:
                keep.append(m._raw(d, e))
        if inside and e["id"] == 51:
            inside = False
    hl = struct.unpack("<I", d[4:8])[0]
    head = bytearray(d[:8 + hl]); fmt, _, ppq = struct.unpack("<hHH", head[8:14])
    head[8:14] = struct.pack("<hHH", fmt, 0, ppq)
    body = b"".join(keep)
    with open(dst, "wb") as f:
        f.write(bytes(head) + b"FLdt" + struct.pack("<I", len(body)) + body)


blank = os.path.join(tmp, "Empty.flp"); make_blank(FIX, blank); os.environ["FLH_BLANK"] = blank
fd = m._load(FIX); fev = m._events(fd)
k201 = next(k for k, e in enumerate(fev) if e["id"] == 201)
k213 = next(k for k in range(k201, len(fev)) if fev[k]["id"] == 213)
GEN_STATE = m._raw(fd, fev[k213]); GEN_HEAD = m._raw(fd, fev[k201 + 1])
fx_k = [k for k, e in enumerate(fev) if e["id"] == 201 and m._text16(m._payload(fd, e)) == "Fruity Parametric EQ"][0]
fx_end = next(k for k in range(fx_k, len(fev)) if fev[k]["id"] == 213)
FX_BODY = raw_events(fd, fev, fx_k + 1, fx_end + 1)


def fst(path, plugin, effect=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    name = m._data_event(201, plugin.encode("utf-16-le") + b"\x00\x00")
    with open(path, "wb") as f:
        f.write(make_flp(name + (FX_BODY if effect else GEN_HEAD + GEN_STATE)))


pdb = os.path.join(docs, "Presets", "Plugin database", "Installed")
for p in sorted(k for k in C.NATIVE_MIN if k in C.NATIVE_GENERATORS or C.NATIVE_MIN[k] == "fruity"):
    if p in ("Audio clip", "Automation clip", "Channel sampler", "Layer", "MIDI Out", "Patcher", "Dashboard",
             "Fruity Envelope Controller", "Fruity Keyboard Controller"):
        continue
    fst(os.path.join(pdb, "Generators", "Fruity", p + ".fst"), p)  # FL lists every native, demos included
for p in ("Fruity Parametric EQ 2", "Fruity Limiter", "Fruity Reeverb 2", "Fruity Delay 3"):
    fst(os.path.join(pdb, "Effects", "Fruity", p + ".fst"), p, effect=True)
pp = os.path.join(docs, "Presets", "Plugin presets", "Generators")
for plug, rel in (("Sytrus", "Keys/Rhodes Classic"), ("Sytrus", "Pads/Warm Pad"), ("Sytrus", "Leads/Saw Lead"),
                  ("Sytrus", "Bass/Deep Bass"), ("Harmor", "Pads/Glass Pad"), ("Harmless", "Pads/Soft Pad"),
                  ("Harmless", "Leads/Bright Lead"), ("Harmless", "Chords/Saw Chord"), ("Toxic Biohazard", "Leads/Big Lead"),
                  ("PoiZone", "Leads/Square Lead"), ("Sawer", "Poly/Juno Poly"), ("Sawer", "Bass/Saw Bass"),
                  ("Harmor", "Bass/Reese Bass"), ("Toxic Biohazard", "Chords/Saw Stack")):
    fst(os.path.join(pp, plug, *rel.split("/")) + ".fst", plug)     # Sakura / Transistor Bass: none here
fst(os.path.join(packs, "Instruments", "Bass", "Acoustic.fst"), "DirectWave")   # FL's own multisampled instruments


def wav(path, x, fs=44100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    y = (np.clip(x, -1, 1) * 32000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs); w.writeframes(y.tobytes())


fs = 44100; rng = np.random.default_rng(3)
tt = lambda s: np.arange(int(fs * s)) / fs
noise = lambda dur, dec: rng.standard_normal(int(fs * dur)) * np.exp(-tt(dur) * dec) * 0.4
tone = lambda dur, hz, dec=1.5: 0.8 * np.sin(2 * np.pi * hz * tt(dur)) * np.exp(-tt(dur) * dec)
kick = lambda: np.sin(2 * np.pi * np.cumsum(55 + 120 * np.exp(-tt(.35) * 40)) / fs) * np.exp(-tt(.35) * 9)
for rel, x in (("Drums/Kicks/Kick 01.wav", kick()), ("Drums/Snares/Snare 01.wav", noise(.25, 18) + tone(.25, 200, 30) * .4),
               ("Drums/Claps/Clap 01.wav", noise(.3, 14)), ("Drums/Hats/Hat 01.wav", noise(.08, 60)),
               ("Drums/Hats/Open Hat 01.wav", noise(.6, 6)), ("Drums/Percussion/Shaker 01.wav", noise(.15, 30)),
               ("Drums/Percussion/Rim 01.wav", tone(.08, 1200, 60)), ("Drums/Percussion/Conga 01.wav", tone(.3, 300, 12)),
               ("Drums/Cymbals/Ride 01.wav", noise(1.0, 3)), ("Drums/Cymbals/Crash 01.wav", noise(1.5, 2)),
               ("Drums/Toms/Tom 01.wav", tone(.4, 120, 8)), ("Drums/808/808 Long.wav", tone(2.0, 46.25, .8)),
               ("FX/Risers/Riser 01.wav", noise(2.0, .2)), ("Textures/Vinyl Crackle.wav", noise(3.0, .01) * .2),
               ("Vocals/Vocal Chop Hey.wav", tone(.4, 440, 4) + noise(.4, 8) * .2)):
    wav(os.path.join(packs, *rel.split("/")), x)

m.fl_open = lambda p: {"opened": p}
TIER = {e: i for i, e in enumerate(C.EDITIONS)}


def plugin_ok(plug, ed):
    """The edition owns it: stock / free, or a native at or below the edition's tier."""
    need = C._native_min(plug)
    if need is None:
        return C._matches(plug, C.FREE_PLUGINS)
    return need == "fruity" or (ed is not None and TIER[need] <= TIER[ed])


def build(g, ed, starter, name):
    m._state({"fl_edition": ed})
    N._FLEX_USED.clear()
    r = N.flh_new_song(g, name=name, open=False, starter=starter, seed=1)
    jr = flh_store.journal_get(r["project"])
    rec, _ = N.recipe(g)
    used = {}
    for chn, v in jr["channels"].items():
        if v.get("fx_kind"):                              # the starter arrangement's ear-candy FX samplers
            assert v["role"] == "fx" and os.path.isfile(v.get("sample") or ""), (g, ed, v)
            continue
        spec = next(s for s in rec["channels"] if s["name"] == v["name"])
        line = next(x for x in r["channels"] if x.startswith(f"{chn} {v['name']}:"))
        assert v.get("sound"), f"{g}/{ed}: {v['name']} is empty ({line})"
        plug = v.get("plugin")
        if plug:
            from_fl_instruments = "Instruments" in str(v.get("sound"))   # DirectWave player, every edition
            bought = C._matches(plug, (flh_store.profile_get() or {}).get("owned_plugins"))   # the user's own list
            assert from_fl_instruments or plugin_ok(plug, ed) or bought, f"{g}/{ed}: {v['name']} uses {plug} ({line})"
            assert C.sound_ok(plug) is True or from_fl_instruments, (g, ed, plug, C.ownership(plug))
        elif MELODIC(spec):
            assert v["sound"].lower().endswith((".wav", ".ogg", ".flac", ".mp3")), (g, ed, line)
        used[spec["role"]] = ("FL instruments" if plug and "Instruments" in str(v.get("sound")) else plug or "sample",
                              line)
    return r, used


# ---------------------------------------------------------------- every genre x every edition
want_high = {   # genre: {role: plugin} the All Plugins Edition gets (and lower editions don't)
    "lofi": {"melody": "Sakura"}, "ambient": {"melody": "Sakura", "pad": "Harmor"}, "synthwave": {
        "melody": "Toxic Biohazard", "chords": "Sawer", "bass": "Sawer", "pad": "Harmor"},
    "hyperpop": {"melody": "PoiZone", "chords": "Toxic Biohazard"}, "techno": {"bass": "Transistor Bass",
                                                                            "chords": "PoiZone"},
    "house": {"bass": "Transistor Bass", "melody": "Sakura"}, "dnb": {"bass": "Harmor"},
    "afrobeats": {"chords": "Sakura"}}
want_sig = {"synthwave": {"melody": "Harmless", "chords": "Harmless", "pad": "Harmless"},
            "hyperpop": {"melody": "Harmless", "chords": "Harmless"}, "ambient": {"pad": "Harmless"},
            "pop": {"pad": "Harmless", "melody": "Harmless"}}
seen = {e: set() for e in C.EDITIONS + (None,)}
n = 0
for ed in ("fruity", "producer", "signature", "all_plugins", None):
    for g in genres:
        r, used = build(g, ed, starter=(ed == "fruity"), name=f"{g} {ed}")
        n += 1
        plugs = {p for p, _ in used.values()}
        seen[ed] |= plugs
        if os.environ.get("FLH_TEST_VERBOSE"):
            print(ed, g, {k: v[0] for k, v in used.items() if v[0] != "sample"})
        if ed == "fruity":                                  # a full beat from the barest FL
            npc = m.flp_info(r["project"]).get("notes_per_channel") or {}
            assert sum(npc.values()) > 0 and r.get("starter"), (g, r.get("warning"))
            jr = flh_store.journal_get(r["project"])
            for role in ("bass", "chords", "melody"):
                chn = (jr.get("compose") or {}).get("channels", {}).get(role)
                assert chn is not None and npc.get(chn) or npc.get(str(chn)), (g, role, npc, chn)
        for role, plug in (want_high.get(g) or {}).items():
            got = used[role][0]
            if ed == "all_plugins":
                assert got == plug, f"{g} {role}: All Plugins Edition gets {got}, wanted {plug} ({used[role][1]})"
                if plug in ("Sakura", "Transistor Bass"):    # no factory preset in this fixture: said so
                    rec_ = N.recipe(g)[0]
                    pick = next(c for c in chain(rec_, next(s_ for s_ in rec_["channels"] if s_["role"] == role))
                                if c.get("plugin") == plug)
                    assert "default patch" in used[role][1], used[role][1]
                    assert (f"no {plug} factory preset" in used[role][1]) == bool(pick.get("presets")), used[role][1]
            else:
                assert got != plug, (g, ed, role, got)
                assert f"{plug}: trial in your FL edition" in used[role][1], (g, ed, used[role][1])
        for role, plug in (want_sig.get(g) or {}).items():
            if ed == "signature":
                assert used[role][0] == plug, (g, role, used[role])
        if ed in ("fruity", None):
            assert "Sytrus" not in plugs and not plugs & HIGHER, (g, ed, plugs)
        if ed == "producer":
            assert not plugs & HIGHER, (g, plugs)
        if ed in ("fruity", "producer", "signature", None) and any("trial" in x for x in r["channels"]):
            assert any("not used (trial" in x for x in r.get("notes") or []), (g, ed, r.get("notes"))
        if ed is None and any("ownership unknown" in x for x in r["channels"]):
            assert any("ownership is unknown" in x for x in r.get("notes") or []), (g, r.get("notes"))
assert "Sytrus" in seen["producer"] and "Sytrus" not in seen["fruity"], seen
assert {"Harmless"} <= seen["signature"] and not HIGHER - {"Harmless", "DirectWave"} & seen["signature"], seen
assert {"Harmor", "Sakura", "PoiZone", "Sawer", "Toxic Biohazard", "Transistor Bass"} <= seen["all_plugins"], seen
assert {"FL Keys", "BooBass"} <= seen["fruity"] and seen["fruity"] <= EVERY | {"sample", "FLEX", "FL instruments"}, seen
assert seen[None] <= EVERY | {"sample", "FLEX", "FL instruments"}, seen[None]

# ---------------------------------------------------------------- a synth's raw init patch (its guessed preset names
# missed) is passed over for a named preset later in the chain, and still beats a bare 3x Osc
m._state({"fl_edition": "all_plugins"}); N._FLEX_USED.clear()
spec = {"role": "chords", "name": "Keys", "family": "keys",
        "choices": [{"plugin": "Harmless", "presets": ["*nothing like this*"]}, {"plugin": "Sytrus", "presets": ["*rhodes*"]},
                    {"plugin": "3x Osc"}]}
rr = N._resolve(dict(spec), N.recipe("lofi")[0], "lofi", [], None, [], set())
assert rr["label"] == "Sytrus 'Rhodes Classic'" and any("Harmless" in x and "init patch" in x for x in rr["skipped"]), rr
spec["choices"][1] = {"plugin": "Sytrus", "presets": ["*nothing either*"]}
rr = N._resolve(dict(spec), N.recipe("lofi")[0], "lofi", [], None, [], set())
assert rr["plugin"] == "Harmless" and "default patch" in rr["label"], rr       # first init patch, not 3x Osc
spec["character"] = {"query": "warm"}                                           # character words pick among matches
fst(os.path.join(pp, "Sytrus", "Keys", "Rhodes Warm.fst"), "Sytrus"); N._PRESETS.clear()
spec["choices"][1] = {"plugin": "Sytrus", "presets": ["*rhodes*"]}
rr = N._resolve(dict(spec), N.recipe("lofi")[0], "lofi", [], None, [], set())
assert rr["label"] == "Sytrus 'Rhodes Warm'", rr
os.remove(os.path.join(pp, "Sytrus", "Keys", "Rhodes Warm.fst")); N._PRESETS.clear()
for g in genres:                                                                # template character data is well formed
    for s_ in N.recipe(g)[0]["channels"]:
        ch_ = s_.get("character")
        if ch_ is not None:
            assert isinstance(ch_.get("query"), str) and isinstance(ch_.get("avoid", []), list), (g, s_["name"], ch_)

# ---------------------------------------------------------------- no FLEX either (removed / an old FL): stock still
real_find = N._find_plugin
N._find_plugin = lambda name: None if C._norm(name) == "flex" else real_find(name)
try:
    for g in genres:
        r, used = build(g, "fruity", starter=False, name=f"{g} noflex")
        assert {p for p, _ in used.values()} <= EVERY | {"sample", "FL instruments"}, (g, used)
finally:
    N._find_plugin = real_find

# ---------------------------------------------------------------- the profile's own plugin lists
flh_store.profile_set("Songs", {"owned_plugins": ["Harmor"]}, activate=True)    # bought separately
m._state({"fl_edition": "producer"})
r, used = build("synthwave", "producer", starter=False, name="bought harmor")
assert used["pad"][0] == "Harmor" and used["melody"][0] != "Toxic Biohazard", used
flh_store.profile_set("Songs", {"owned_plugins": [], "trial_plugins": ["FL Keys"]})   # the user says it's a demo here
r, used = build("pop", "producer", starter=False, name="no fl keys")
assert used["chords"][0] != "FL Keys" and "FL Keys: trial in your FL edition (the collection profile" in used["chords"][1], used
flh_store.profile_set("Songs", {"owned_plugins": [], "trial_plugins": [], "prefer_flex": True})
r, used = build("synthwave", "all_plugins", starter=False, name="flex first")     # FLEX preference, no packs: on
assert any("FLEX" in x for x in r["channels"]), r["channels"]                    # (FLEX's default sound, once)
flh_store.profile_set("Songs", {"prefer_flex": False})

# ---------------------------------------------------------------- first-run edition flow (flh_setup)
m._state({"fl_edition": None, "fl_edition_unsure": None})
rep = S.check()
e = rep["fl_edition"]
assert e["edition"] is None and len(e["ask"]["options"]) == 5 and "unknown" in e["ask"]["options"][-1], e
assert sum("FL edition" in s for s in rep["next_steps"]) == 1, rep["next_steps"]       # one question
flh_store.profile_set("Songs", {"owned_plugins": ["Sytrus"], "trial_plugins": ["Harmor"]})
ev = C.edition_from_plugins()
assert ev["at_least"] == "producer" and ev["at_most"] == "signature" and "edition" not in ev, ev
e = S.check()["fl_edition"]
assert [o.split(" =")[0] for o in e["ask"]["options"]] == ["producer", "signature", "unknown"], e   # narrowed
flh_store.profile_set("Songs", {"trial_plugins": ["Harmor", "Gross Beat"]})
assert C.fl_edition() == ("producer", "plugins"), C.fl_edition()                  # pinned by the user's own lists
e = S.check()["fl_edition"]
assert e["edition"] == "producer" and e["source"] == "plugins" and "ask" not in e and "Sytrus" in e["unlocks"]["instruments"], e
assert not any("FL edition" in s for s in S.check()["next_steps"])
flh_store.profile_set("Songs", {"owned_plugins": ["Gross Beat"], "trial_plugins": ["Sytrus"]})
assert C.edition_from_plugins().get("conflict") and C.fl_edition() == (None, None)   # contradicting lists: ask
flh_store.profile_set("Songs", {"owned_plugins": ["*"], "trial_plugins": []})        # wildcards prove nothing
assert C.edition_from_plugins() is None
a = S.apply(fl_edition="not sure", theme=False)
assert any("not sure" in x for x in a["done"]) and m._state()["fl_edition_unsure"] is True, a
rep = S.check()
assert not any("FL edition" in s for s in rep["next_steps"]) and "wasn't sure" in rep["fl_edition"]["note"], rep
a = S.apply(fl_edition="Signature Bundle", theme=False)
assert m._state()["fl_edition"] == "signature" and not m._state()["fl_edition_unsure"], m._state()
assert a["fl_edition"]["edition"] == "signature" and "Harmless" in a["fl_edition"]["unlocks"]["instruments"], a
assert "Harmor" not in a["fl_edition"]["unlocks"]["instruments"] and "Gross Beat" in a["fl_edition"]["unlocks"]["effects"]
assert "demos" in a["fl_edition"]["trial_here"] and any("Harmless" in x for x in a["done"]), a
u = C.edition_unlocks("fruity")
assert u["instruments"] == [] and u["effects"] == [] and u["not_included"] == sum(
    1 for v in C.NATIVE_MIN.values() if v != "fruity")
assert set(C.edition_unlocks("all_plugins")["instruments"]) == {k for k, v in C.NATIVE_MIN.items()
                                                                  if v != "fruity" and k in C.NATIVE_GENERATORS}
try:
    S.apply(fl_edition="deluxe", theme=False); raise SystemExit("bad edition accepted")
except ValueError as ex:
    assert "unknown" in str(ex)
print(f"editions ok ({n} songs, {time.time() - T0:.0f} s)")
