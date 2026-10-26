"""flh_newsong: flp_load_sample (new / replace, sample path event, channel count), flh_new_song for three genres on a
temp folder with a mocked FL (file valid, channels/names/colors/routing/patterns/markers/tempo as the recipe, journal),
owned-only substitutions (Fruity vs Producer edition), save_template round trip, welcome, icon probe, recipes data.
Run: python3 tests/test_newsong.py (no FL needed)."""
import json, math, os, re, shutil, struct, sys, tempfile, time, wave
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
docs = os.path.join(tmp, "Documents", "Image-Line", "FL Studio"); os.environ["FLH_FL_USERDATA"] = docs
packs = os.path.join(tmp, "FL", "Data", "Patches", "Packs"); os.environ["FLH_FACTORY_PACKS"] = packs
os.environ["FLH_SPLICE_LIB"] = os.path.join(tmp, "Splice", "INSTRUMENT")
os.environ["FLH_FLEX_DIR"] = os.path.join(tmp, "FLEX")
for k in ("FLH_PLUGIN_DB", "FLH_FL_EDITION"):
    os.environ.pop(k, None)
import numpy as np  # noqa: E402

import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_catalog as C, flh_compose, flh_newsong as N  # noqa: E402
ns = vars(m)
for mod in (flh_store, C, flh_compose, N):
    mod.register(ns)
for t in ("flh_new_song", "flp_load_sample", "flh_save_template", "flh_welcome", "flh_icon_probe"):
    assert t in ns["TOOLS"], t
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")


# ---------------------------------------------------------------- fixtures: FL-like blank project, .fst presets, WAVs
def raw_events(d, ev, a, b):
    return b"".join(m._raw(d, e) for e in ev[a:b])


def make_flp(events_bytes, nch=0, ppq=96):
    return b"FLhd" + struct.pack("<I", 6) + struct.pack("<hHH", 0, nch, ppq) + b"FLdt" + \
        struct.pack("<I", len(events_bytes)) + events_bytes


def make_blank(src, dst):
    """FL's empty template stand-in: the fixture without channels, notes, clips or remote links."""
    d = m._load(src); ev = m._events(d)
    keep = []; inside = False
    for k, e in enumerate(ev):
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


if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
blank = os.path.join(tmp, "Empty.flp"); make_blank(FIX, blank); os.environ["FLH_BLANK"] = blank
bd = m._load(blank); bev = m._events(bd)
assert not [e for e in bev if e["id"] == 64] and m.flp_list(blank)["channels"] == []

fd = m._load(FIX); fev = m._events(fd)
k201 = next(k for k, e in enumerate(fev) if e["id"] == 201)                         # channel 0's plugin block
k213 = next(k for k in range(k201, len(fev)) if fev[k]["id"] == 213)
GEN_STATE = m._raw(fd, fev[k213]); GEN_HEAD = m._raw(fd, fev[k201 + 1])
fx_k = [k for k, e in enumerate(fev) if e["id"] == 201 and m._text16(m._payload(fd, e)) == "Fruity Parametric EQ"][0]
fx_end = next(k for k in range(fx_k, len(fev)) if fev[k]["id"] == 213)
FX_BODY = raw_events(fd, fev, fx_k + 1, fx_end + 1)


def fst(path, plugin, effect=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    name = m._data_event(201, plugin.encode("utf-16-le") + b"\x00\x00")
    body = name + (FX_BODY if effect else GEN_HEAD + GEN_STATE)
    with open(path, "wb") as f:
        f.write(make_flp(body))


pdb = os.path.join(docs, "Presets", "Plugin database", "Installed")
for p in ("FL Keys", "BooBass", "3x Osc", "Sytrus", "FLEX", "Fruity Kick", "Plucked!", "Fruity DX10"):
    fst(os.path.join(pdb, "Generators", "Fruity", p + ".fst"), p)
fst(os.path.join(pdb, "Generators", "VST3", "Splice INSTRUMENT.fst"), "Fruity Wrapper")
for p in ("Fruity Parametric EQ 2", "Fruity Limiter", "Fruity Reeverb 2", "Fruity Delay 3"):
    fst(os.path.join(pdb, "Effects", "Fruity", p + ".fst"), p, effect=True)
pp = os.path.join(docs, "Presets", "Plugin presets", "Generators")
fst(os.path.join(pp, "Sytrus", "Keys", "Rhodes Classic.fst"), "Sytrus")
fst(os.path.join(pp, "Sytrus", "Pads", "Warm Pad.fst"), "Sytrus")
fst(os.path.join(pp, "Sytrus", "Leads", "Bell Lead.fst"), "Sytrus")


def wav(path, x, fs=44100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    y = (np.clip(x, -1, 1) * 32000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs); w.writeframes(y.tobytes())


fs = 44100; rng = np.random.default_rng(1)
t = lambda s: np.arange(int(fs * s)) / fs
def kick(dur=0.35, f0=55.0, click=1.0, lp=None):
    tt = t(dur); f = f0 + 120 * np.exp(-tt * 40)
    x = np.sin(2 * np.pi * np.cumsum(f) / fs) * np.exp(-tt * 9) + click * 0.3 * rng.standard_normal(len(tt)) * np.exp(-tt * 300)
    if lp:
        k = np.ones(lp) / lp; x = np.convolve(x, k, "same")
    return x
def noise(dur, decay, hp=True):
    tt = t(dur); x = rng.standard_normal(len(tt))
    if hp:
        x = np.diff(np.concatenate([[0], x]))
    return x * np.exp(-tt * decay) * 0.5
def tone(dur, hz, decay=1.5):
    tt = t(dur); return 0.8 * np.sin(2 * np.pi * hz * tt) * np.exp(-tt * decay)


wav(os.path.join(packs, "Drums", "Kicks", "Clean Kick 01.wav"), kick())
wav(os.path.join(packs, "Drums", "Kicks", "Dusty Kick SP.wav"), kick(lp=12) + 0.01 * rng.standard_normal(int(fs * .35)))
wav(os.path.join(packs, "Drums", "Snares", "Snare Crack.wav"), noise(0.25, 18, False) + tone(0.25, 200, 30) * 0.4)
wav(os.path.join(packs, "Drums", "Claps", "Clap Big.wav"), noise(0.3, 14, False))
wav(os.path.join(packs, "Drums", "Hats", "Hat Tight.wav"), noise(0.08, 60))
wav(os.path.join(packs, "Drums", "Hats", "Open Hat 1.wav"), noise(0.6, 6))
wav(os.path.join(packs, "Drums", "Percussion", "Shaker 2.wav"), noise(0.15, 30))
wav(os.path.join(packs, "Drums", "Percussion", "Rim Click.wav"), tone(0.08, 1200, 60) + noise(0.08, 80) * 0.3)
wav(os.path.join(packs, "Drums", "808", "808 Long.wav"), tone(2.0, 46.25, 0.8))          # F#1, pitch from audio
wav(os.path.join(packs, "FX", "Risers", "Riser White.wav"), noise(2.5, 0.2))
wav(os.path.join(packs, "Textures", "Vinyl Crackle.wav"), noise(3.0, 0.01) * 0.2)
wav(os.path.join(packs, "Loops", "Drums", "Break 90 BPM.wav"), noise(2.67, 0.1))
wav(os.path.join(packs, "Drums", "Misc", "Mystery 3.wav"), kick(0.4, 50.0))           # name says nothing: audio does
with open(os.path.join(packs, "Drums", "Kicks", "Kick Legacy.ogg"), "wb") as f:     # not a WAV: name only
    f.write(b"OggS")

# ---------------------------------------------------------------- sample catalog
s = C.fl_sound_catalog(type="sample")
assert s["count"] == 14 and s["by_role"]["kick"] == 3 and s["roots"][0]["source"] == "FL factory", s
r = C.fl_sound_catalog(kind="kick", query="dusty punchy", genre="lofi")
assert r["type"] == "sample" and r["results"][0]["name"] == "Dusty Kick SP", r["results"]
assert "dusty" in r["results"][0]["tags"] and "flp_load_sample" in r["load_with"] and "load" not in r["results"][0], r
assert C.fl_sound_catalog(kind="kick", query="dusty punchy", genre="lofi", full=True)["results"][0]["load"]["tool"] == "flp_load_sample"
r = C.fl_sound_catalog(type="sample", query="clean kick")                    # the role named in the query filters
assert r["role"] == "kick" and all(x["role"] == "kick" for x in r["results"]), r
r = C.fl_sound_catalog(kind="kick", query="clean")
assert r["results"][0]["name"] == "Clean Kick 01", r["results"]
r = C.fl_sound_catalog(kind="808")
e808 = r["results"][0]
assert e808["name"] == "808 Long" and e808.get("pitch") in (29, 30) and e808["note"].startswith(("F#1", "F1", "G1")), e808
r = C.fl_sound_catalog(kind="loop", genre="boombap")
assert r["results"][0]["bpm"] == 90 and r["results"][0]["sub"] == "drum", r["results"]
r = C.fl_sound_catalog(kind="ride")                                   # none: honest fallback to crash -> none -> empty
assert r["results"] == [] and "note" in r, r
r = C.fl_sound_catalog(kind="perc")                                   # shared kind: instruments + samples
assert any(x["name"] == "Shaker 2" for x in r.get("samples", [])) or r.get("found") is not None, r
assert C.classify_sample(os.path.join(packs, "Drums", "Misc", "Mystery 3.wav"), packs)["role"] == "other"
assert C._feat_role(C.sample_features(os.path.join(packs, "Drums", "Misc", "Mystery 3.wav"))) == "kick"
feats = json.load(open(os.path.join(os.path.dirname(m.STATE), "sample_features.json")))
assert len(feats) >= 3 and all("f" in v for v in feats.values()), len(feats)
# M9: the ranking doesn't depend on a time budget or a warm cache (same kit on any PC)
def _cold():
    C._SAMPLE_MEMO.pop("feat", None)
    if os.path.isfile(C._feat_path()):
        os.remove(C._feat_path())
_cold()
cold = [x["path"] for x in C.sample_search("kick", "punchy dark", "trap", limit=6)["results"]]
warm = [x["path"] for x in C.sample_search("kick", "punchy dark", "trap", limit=6)["results"]]
assert cold == warm and cold, (cold, warm)
_cold()
late = C.sample_search("kick", "punchy dark", "trap", limit=6, budget_s=0)          # out of time: no features at all
plain_ = C.sample_search("kick", "punchy dark", "trap", limit=6, features=False)
assert "features skipped" in late.get("note", "") and [x["path"] for x in late["results"]] == [x["path"] for x in plain_["results"]]
# L9: the walk goes alphabetically and says which packs a capped walk left out
wd_ = os.path.join(tmp, "walk")
for pk in ("Alpha", "Beta", "Gamma"):
    for i in range(2):
        os.makedirs(os.path.join(wd_, pk), exist_ok=True); open(os.path.join(wd_, pk, f"s{i}.wav"), "wb").write(b"x")
missed_ = []
got_ = C._walk_samples(wd_, 3, missed_)
assert [os.path.basename(os.path.dirname(p_)) for p_, _, _ in got_] == ["Alpha", "Alpha", "Beta"], got_
assert {os.path.basename(x) for x in missed_} == {"Beta", "Gamma"}, missed_
t0 = time.time(); C.fl_sound_catalog(kind="kick", query="dusty"); assert time.time() - t0 < 0.5   # cached

# ---------------------------------------------------------------- flp_load_sample
song = os.path.join(roots, "LS", "ls.flp"); os.makedirs(os.path.dirname(song)); shutil.copy(FIX, song)
kickp = os.path.join(packs, "Drums", "Kicks", "Clean Kick 01.wav")
n0 = len(m.flp_list(song)["channels"]); hdr0 = struct.unpack("<hHH", m._load(song)[8:14])[1]
r = ns["TOOLS"]["flp_load_sample"][0](project=song, output=song, sample=kickp)
assert r["created"] and r["channel"] == 6 and r["from"] == "FL's built-in channel block", r
d = m._load(song); ev = m._events(d)
ch = flh_compose.channels_of(d, ev)
assert len(ch) == n0 + 1 and struct.unpack("<hHH", d[8:14])[1] == hdr0 + 1
assert ch[6]["type"] == 0 and ch[6]["name"] == "Clean Kick 01" and ch[6]["sample"] == "Clean Kick 01.wav", ch[6]
paths = [m._text16(m._payload(d, e)) for e in ev if e["id"] == m.EV_SAMPLE_PATH]
assert os.path.abspath(kickp) in paths, paths
r2 = N.flp_load_sample(song, song, os.path.join(packs, "Drums", "Snares", "Snare Crack.wav"))   # copies sampler 6
assert r2["channel"] == 7 and r2["from"].startswith("a copy of sampler channel 6"), r2
k0_, kend_ = N._chan_range(m._load(song), m._events(m._load(song)), 7)        # its own routing, not channel 6's insert
ins_ = [struct.unpack("<h", m._payload(m._load(song), e)[:2])[0] for e in m._events(m._load(song))[k0_:kend_ + 1] if e["id"] == 104]
other = {struct.unpack("<h", m._payload(m._load(song), e)[:2])[0] for c in range(7)
         for e in (lambda d_, ev_: ev_[N._chan_range(d_, ev_, c)[0]:N._chan_range(d_, ev_, c)[1] + 1])(m._load(song), m._events(m._load(song)))
         if e["id"] == 104}
assert len(ins_) == 1 and r2["insert"].startswith(str(ins_[0])), (ins_, r2)               # a free insert, not the Master
assert ins_[0] == 0 or ins_[0] not in other, (ins_, other)
r3 = N.flp_load_sample(song, song, os.path.join(packs, "Drums", "Hats", "Hat Tight.wav"), channel=6, name="Hat")
d = m._load(song); ch = flh_compose.channels_of(d, m._events(d))
assert not r3["created"] and ch[6]["sample"] == "Hat Tight.wav" and ch[6]["name"] == "Hat" and len(ch) == n0 + 2
for bad in (0, 3, 5):                                                  # plugin / automation / audio clip channels
    try:
        N.flp_load_sample(song, song, kickp, channel=bad); raise SystemExit("must refuse a non-sampler channel")
    except ValueError as e:
        assert "sampler" in str(e)
assert flh_compose.channels_of(m._load(song), None)[5]["type"] == 4          # the audio clip was left alone
try:
    N.flp_load_sample(song, song, kickp, channel=0); raise SystemExit("refuse")
except ValueError as e:
    assert "replace_plugin=true" in str(e), e
d0 = m._load(song); ch0 = flh_compose.channels_of(d0, m._events(d0))[0]
n_notes0 = sum(1 for p in m._patterns(d0, m._events(d0)).values() for r_ in p["notes"] if r_[2] == 0)
rp = N.flp_load_sample(song, song, kickp, channel=0, replace_plugin=True)
d = m._load(song); ch = flh_compose.channels_of(d, m._events(d))
assert rp["replaced_plugin"] and ch[0]["type"] == 0 and ch[0]["name"] == ch0["name"] and len(ch) == n0 + 2, (rp, ch[0])
assert sum(1 for p in m._patterns(d, m._events(d)).values() for r_ in p["notes"] if r_[2] == 0) == n_notes0
k0, kend = N._chan_range(d, m._events(d), 7)
assert m._events(d)[kend]["id"] == 51
m.flp_info(song); m.flp_playlist(song)                                 # still parses everywhere
# into the blank project (no channels at all)
b2 = os.path.join(roots, "LS", "blank.flp"); shutil.copy(blank, b2)
r = N.flp_load_sample(b2, b2, kickp)
d = m._load(b2); ch = flh_compose.channels_of(d, m._events(d))
assert r["channel"] == 0 and list(ch) == [0] and ch[0]["type"] == 0 and struct.unpack("<hHH", d[8:14])[1] == 1, ch

# ---------------------------------------------------------------- flh_new_song with a mocked FL
opened = []
m.fl_open = lambda p: opened.append(p) or {"opened": p}
ns["IS_WINDOWS"] = True                                                   # pretend: fl_open is called (mocked)
m._state({"fl_edition": "producer"})


def check_song(res, genre, want_bpm):
    p = res["project"]
    assert os.path.isfile(p) and p.startswith(roots) and opened[-1] == p, res
    d = m._load(p); ev = m._events(d)
    info = m.flp_info(p)
    assert abs(info["tempo_bpm"] - want_bpm) < 1e-6, info
    rec, g = N.recipe(genre)
    chans = flh_compose.channels_of(d, ev)
    names = [c["name"] for c in chans.values()]
    for spec in rec["channels"]:
        assert spec["name"] in names, (spec["name"], names)
    # colors (dark palette) and mixer inserts as planned
    j = flh_store.journal_get(p)
    assert j["genre"] == g["name"] and j["key"] and j["bpm"] == want_bpm and j["sections"], j
    starts = N._chan_starts(d, ev)
    for k0, idx in starts:
        _, kend = N._chan_range(d, ev, idx)
        blk = {e["id"]: m._payload(d, e) for e in ev[k0:kend + 1]}
        if (j["channels"].get(str(idx)) or {}).get("fx_kind"):          # ear candy from the starter arrangement
            assert max(blk[m.EV_CHANNEL_COLOR][:3]) < 0x80 and j["channels"][str(idx)]["role"] == "fx"
            assert struct.unpack("<h", blk[N.EV_INSERT])[0] == j["channels"][str(idx)]["mixer"]
            continue
        if chans[idx]["type"] == 5 and str(chans[idx]["name"]).startswith(("Delay throws", "Filter ")):
            continue                                                    # ear candy automation (delay throws, filter)
        spec = next((s for s in rec["channels"] if s["name"] == chans[idx]["name"]), None); assert spec, (chans[idx]["name"], [c["name"] for c in chans.values()])
        col = N._palette_color(rec, spec["role"], spec.get("family"))
        r_, g_, b_ = N._rgb(col)
        assert blk[m.EV_CHANNEL_COLOR][:3] == bytes([r_, g_, b_]), (spec["name"], blk[m.EV_CHANNEL_COLOR].hex(), col)
        assert max(r_, g_, b_) < 0x80, col                                 # dark shades by default
        ins = struct.unpack("<h", blk[N.EV_INSERT])[0]
        assert ins == j["channels"][str(idx)]["mixer"], (spec["name"], ins, j["channels"][str(idx)])
    # mixer names + routing to the buses
    trk = [k for k, e in enumerate(ev) if e["id"] == m.EV_MIXER_TRACK]
    def mix_name(t):
        a = trk[t]
        while a > 0 and ev[a - 1]["id"] in (m.EV_MIXER_COLOR, m.EV_MIXER_HASPROPS, m.EV_MIXER_NAME):
            a -= 1
        return next((m._text16(m._payload(d, ev[k])) for k in range(a, trk[t]) if ev[k]["id"] == m.EV_MIXER_NAME), None)
    def routes(t):
        end = trk[t + 1] if t + 1 < len(trk) else len(ev)
        r = next(m._payload(d, ev[k]) for k in range(trk[t], end) if ev[k]["id"] == m.EV_MIXER_ROUTE)
        return [i for i, b in enumerate(r) if b]
    mixer = res["mixer"]
    bus = {x.split(" ", 1)[1]: int(x.split()[0]) for x in mixer["buses"]}
    assert "Drums Bus" in bus and "Music Bus" in bus, mixer
    kick_ins = next(v["mixer"] for v in j["channels"].values() if v["role"] == "kick")
    assert mix_name(kick_ins) == "Kick" and routes(kick_ins) == [bus["Drums Bus"]], (mix_name(kick_ins), routes(kick_ins))
    assert mix_name(bus["Drums Bus"]) == "Drums Bus" and routes(bus["Drums Bus"]) == [0]
    bass_ins = next(v["mixer"] for v in j["channels"].values() if v["role"] == "bass")
    assert routes(bass_ins) == [bus["Music Bus"]]
    sends = {x.split(" ", 1)[1]: int(x.split()[0]) for x in mixer["sends"]}
    fx = N._effects(d)
    assert any(t == sends["Reverb"] and f["plugin"] == "Fruity Reeverb 2" for (t, _), f in fx.items()), fx
    lim = max(s_ for (t, s_), f in fx.items() if t == 0)
    assert N._is_limiter(fx[(0, lim)]["plugin"]), "a limiter is the last effect on the master"
    assert sum(1 for (t, _), f in fx.items() if t == 0 and N._is_limiter(f["plugin"])) == 1, "never two limiters"
    assert any(t == 0 and f["plugin"] == "Fruity Parametric EQ 2" for (t, _), f in fx.items()), fx
    # playlist tracks named, markers at the sections
    tnames = {t: g["name"] for t, g in m._track_groups(d, ev).items() if g["name"] is not None}
    for g in m._track_groups(d, ev).values():                     # FL 2026's order: 238 43 239 (name last)
        if g["name_k"] is not None:
            assert g["name_k"] == g["end"] - 1, ("the name ends the track's group", g)
    marks = [mk["name"] for mk in m._markers(d, ev)]
    assert [s["name"] for s in j["sections"]] == marks[-len(j["sections"]):], (marks, j["sections"])
    arr = m._arrangement(d, ev)                                   # where FL reads playlist markers (PC 2026-10-23)
    pos = [k for k in range(arr["first"], arr["end"]) if ev[k]["id"] == N.EV_MARKER_POS]
    first_track = min(g["k"] for g in m._track_groups(d, ev).values())   # FL's own save: 233 | markers | tracks
    assert pos and min(pos) == arr["playlist"] + 1 and max(pos) < first_track, (pos, arr, first_track)
    secs = {s_["name"] for s_ in j["sections"]}
    assert not [x for x in m._stray_markers(d, ev) if x["name"] in secs], m._stray_markers(d, ev)
    return d, ev, j, tnames, chans


# 1) lofi with a starter beat laid out in the form
r = N.flh_new_song("lofi", name="Rainy Desk", vibe="dusty")
assert r["genre"] == "lofi" and r["bpm"] == 82 and r["key"] == "F major" and r["opened"], r
d, ev, j, tnames, chans = check_song(r, "lofi", 82)
assert r["file_s"] < 5.0, r["steps_s"]
if os.environ.get("FLH_TEST_VERBOSE"):
    print(json.dumps(r, indent=1))
assert tnames[1] == "Drums" and tnames[2] == "Bass" and tnames[3] == "Chords" and tnames[4] == "Melody", tnames
kick_ch = j["compose"]["drum_map"]["kick"][0]
assert "Dusty Kick SP" in chans[kick_ch]["sample"], chans[kick_ch]
pats = m._patterns(d, ev)
notes_by = {}
for p in pats.values():
    for n in p["notes"]:
        notes_by[n[2]] = notes_by.get(n[2], 0) + 1
for role in ("kick", "snare", "hat"):
    assert notes_by.get(j["compose"]["drum_map"][role][0]), (role, notes_by)
for role in ("bass", "chords", "melody"):
    assert notes_by.get(j["compose"]["channels"][role]), (role, notes_by, j["compose"]["channels"])
assert r.get("starter", {}).get("arranged") or any("form" in n for n in r.get("notes", [])), r
assert len(j["sections"]) >= 4 and j["sections"][0]["bar"] == 1, j["sections"]
assert any("Sytrus 'Rhodes Classic'" in c for c in r["channels"]), r["channels"]      # producer: Sytrus preset
vinyl = next(c for c in chans.values() if c["name"] == "Vinyl")
assert vinyl["sample"] == "Vinyl Crackle.wav", vinyl
assert os.path.isfile(flh_store.journal_path(r["project"]))
work = os.path.join(os.path.dirname(m.STATE), "work")
assert not os.path.isdir(work) or not os.listdir(work), "scratch copies are cleaned up"
assert not any(os.path.normcase(x).startswith(os.path.normcase(work)) for x in m._TEMP_ROOTS)
assert not [f for f in os.listdir(m.BACKUPS) if "Rainy Desk" in f] if os.path.isdir(m.BACKUPS) else True

# 2) trap without a starter: empty section blocks + markers, the 808 sample, Fruity edition substitutions
m._state({"fl_edition": "fruity"})
r = N.flh_new_song("trap", bpm=140, key="F minor", starter=False, name="Night")
d, ev, j, tnames, chans = check_song(r, "trap", 140)
assert tnames[1] == "Sections" and "starter" not in r, (tnames, r)
plan = N._section_plan(flh_compose.genre_data("trap"), N.recipe("trap")[0])
assert [s["name"] for s in j["sections"]] == [s["name"] for s in plan]
pl = m.flp_playlist(r["project"])["clips"]
assert len(pl) == len(plan) and all(c["track"] == 1 for c in pl) and pl[0]["name"] == "Intro", pl
assert not any("Sytrus" in c.split(" -> ")[0] for c in r["channels"]), r["channels"]   # Fruity: Sytrus is a demo
assert "Clean Kick 01" in r["channels"][0] or "Dusty" not in r["channels"][0], r["channels"][0]
assert any("Sytrus: trial" in c for c in r["channels"]) and any("trial" in n for n in r["notes"]), r
c808 = next(c for c in chans.values() if c["name"] == "808")
assert c808["type"] == 0 and c808["sample"] == "808 Long.wav", c808
c8 = next(i for i, c in chans.items() if c["name"] == "808")          # starter=False still records the 808 offset
assert j["channels"][str(c8)]["transpose"] == 30 and j["compose"]["transpose"][str(c8)] == 30, j["channels"][str(c8)]
assert any("808 sample is pitched F#1" in n for n in r["notes"]), r["notes"]
night = r["project"]
assert any("FL Keys" in c for c in r["channels"] if c.split(" ", 1)[1].startswith("Keys")), r["channels"]

# 2a) a fresh new song takes written notes: flp_write_arrangement / flp_write_notes replace the empty section blocks
def _mid(path, notes, bpm=140, tpb=480):
    evs = []
    for st, ln, k, v in notes:
        evs.append((int(st * tpb), b"\x90" + bytes([k, v]))); evs.append((int((st + ln) * tpb), b"\x80" + bytes([k, 0])))
    evs.sort(key=lambda e: (e[0], e[1][0] == 0x90))
    trk = b"\x00\xff\x51\x03" + struct.pack(">I", int(60e6 / bpm))[1:]; last = 0
    for t, msg in evs:
        dt = t - last; last = t; vl = [dt & 0x7F]; dt >>= 7
        while dt: vl.append(0x80 | (dt & 0x7F)); dt >>= 7
        trk += bytes(reversed(vl)) + msg
    trk += b"\x00\xff\x2f\x00"
    open(path, "wb").write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, tpb) + b"MTrk" + struct.pack(">I", len(trk)) + trk)
    return path
wa = os.path.join(os.path.dirname(night), "wa.flp")
shutil.copyfile(night, wa)
kick = _mid(os.path.join(tmp, "k.mid"), [(b * 4.0, 0.5, 60, 100) for b in range(12)])           # bars 1..12
keys = _mid(os.path.join(tmp, "p.mid"), [(28.0, 8.0, 64, 80), (44.0, 2.0, 67, 70)])             # rings past bar 9
r = m.flp_write_arrangement(wa, wa, [{"channel": 0, "midi": kick}, {"channel": 7, "midi": keys}],
                            [{"name": "Intro", "bar": 1}, {"name": "Verse", "bar": 5}, {"name": "Hook", "bar": 9}],
                            tempo=96)
assert r["notes_written"] == 14 and [L["name"] for L in r["patterns"]] == ["Intro", "Verse", "Hook"], r
assert r["patterns"][1]["length_bars"] > 4 and r["markers"] == 3, r                       # Verse clip covers the ring
pats = {p["name"]: p for p in m.flp_patterns(wa)}
assert {"Intro", "Verse", "Hook"} <= set(pats) and not any(                               # old empty blocks gone
    p["placed_at_bars"] for n, p in pats.items() if n not in ("Intro", "Verse", "Hook")), pats
assert len(pats) <= 4, pats                                                                # (FL's pattern 1 may stay)
assert pats["Intro"]["notes"] == 4 and pats["Verse"]["notes"] == 5 and pats["Hook"]["notes"] == 5, pats
inf = m.flp_info(wa)
assert [mk["name"] for mk in inf["markers"]] == ["Intro", "Verse", "Hook"] and abs(inf["tempo_bpm"] - 96) < .01, inf
assert [s["name"] for s in flh_store.journal_get(wa)["sections"]] == ["Intro", "Verse", "Hook"]
wn = os.path.join(os.path.dirname(night), "wn.flp")
r = m.flp_write_notes(night, wn, [{"channel": 0, "notes": [[1, 0.25, 60, 100], [2, 0.25, 60, 90]]}], tempo="keep")
assert r["notes_written"] == 2 and [L["name"] for L in r["patterns"]] == ["Song"], r
try:                                                                                       # a song with notes: refused
    m.flp_write_arrangement(wa, wa, [{"channel": 0, "midi": kick}], [{"name": "A", "bar": 1}])
    raise AssertionError("should refuse")
except ValueError as e:
    assert "flp_replace_part" in str(e) and "Intro" in str(e), e

# 2b) drill-uk with a starter: the sampled 808 (F#1) is written 30 semitones up so FL's sampler (C5 root) sounds F#1
r = N.flh_new_song("drill-uk", name="Tune", open=False)
d = m._load(r["project"]); ev = m._events(d); j = flh_store.journal_get(r["project"])
assert isinstance(j.get("balance_pending"), dict) and "pending" in r.get("levels", ""), (j.get("balance_pending"), r)
c808 = j["compose"]["channels"]["bass"]
assert j["channels"][str(c808)]["transpose"] == 30 and j["compose"]["transpose"][str(c808)] == 30, j["channels"]
keys = [n[4] for p in m._patterns(d, ev).values() for n in p["notes"] if n[2] == c808]
g = flh_compose.genre_data("drill-uk")
assert keys and min(keys) >= g["bass"]["register"][0] + 30 - 12 and any("808 sample is pitched F#1" in n for n in r["notes"]), \
    (sorted(set(keys))[:5], r.get("notes"))
tune = r["project"]
# later writers honour the offset too: a new bassline (and one on the starter-less song) lands where the 808 sounds
import flh_score as SC  # noqa: E402
SC.register(ns)
rb = ns["TOOLS"]["flp_compose"][0](tune, "bass", section="Verse", variations=1)
_hn = next((n for n in rb.get("notes", []) if "plays together with Hook 808" in n), "")
assert "flp_compose_clear" in _hn and "flp_playlist(" not in _hn, rb.get("notes")   # by content, never clip ids (F4)
# the hint works as written: it removes exactly those Hook 808 clips (found by pattern, bar, track), nothing else
_clips = json.loads(_hn[_hn.index('"clips": ') + 9:_hn.rindex("}")])
_cp = os.path.join(os.path.dirname(tune), "hint copy.flp"); shutil.copyfile(tune, _cp)
_before = m.flp_playlist(_cp)["clips"]
_rr = ns["TOOLS"]["flp_compose_clear"][0](project=_cp, clips=_clips)
_after = m.flp_playlist(_cp)["clips"]
assert _rr["removed"] == len(_clips) and len(_after) == len(_before) - len(_clips), (_rr, len(_before), len(_after))
assert not [c for c in _after if [c.get("pattern"), c["bar"], c["track"]] in _clips]
_rr = ns["TOOLS"]["flp_compose_clear"][0](project=_cp, clips=_clips)          # already gone: left alone, said so
assert _rr["removed"] == 0 and _rr["not_found"] == len(_clips), _rr
d = m._load(tune); ev = m._events(d)
vb = [n[4] for p in m._patterns(d, ev).values() if p["name"].lower().startswith("verse") for n in p["notes"] if n[2] == c808]
lo_, hi_ = g["bass"]["register"][:2]
assert vb and min(vb) >= lo_ + 30 - 12 and max(vb) <= hi_ + 30 + 12, (sorted(set(vb)), rb.get("variations"))
assert "clashes" not in rb or "bars" in rb["clashes"], rb.get("clashes")     # checked on the written bars only
# flp_score / flp_clashes judge the 808 at the pitch it sounds (written - 30), never "move it an octave"
sv = SC.score_api(tune)
p808 = next(x for x in sv["parts"] if x["ch"] == c808)
low_k = min(n[3] for n in SC.load_model(tune)["notes"] if n[2] == c808)
assert lo_ - 12 <= low_k <= hi_ + 12, (low_k, p808)
assert sv["written_offsets"] == {str(c808): 30}, sv.get("written_offsets")
cl = SC.clashes(tune, kinds=["weak_register", "out_of_range"], limit=20, full=True)
i808 = [i for i in cl["issues"] if f"(ch{c808})" in i["parts"]]
assert not any(i["kind"] == "out_of_range" or "4" in i["notes"] or "5" in i["notes"] for i in i808), i808  # no C4+ "808"
for i in i808:                                                             # fix hints select the written pitch
    lo_sel = int(re.search(r'"?low"?\s*[=:]\s*(-?\d+)', i["fix"]).group(1))
    assert lo_sel - 30 in {n[3] for n in SC.load_model(tune)["notes"] if n[2] == c808}, i
# the same works on the song made without a starter
rn = ns["TOOLS"]["flp_compose"][0](night, "bass", genre="trap", key="F minor", bars=4)
d = m._load(night); ev = m._events(d)
nb_ = [n[4] for p in m._patterns(d, ev).values() for n in p["notes"] if n[2] == c8]
gt = flh_compose.genre_data("trap")
assert nb_ and min(nb_) >= gt["bass"]["register"][0] + 30 - 12, sorted(set(nb_))
# swapping the 808 sample: the new pitch is measured, the offset updated and the old notes moved to sound the same
wav(os.path.join(packs, "Drums", "808", "808 Deep A.wav"), tone(2.0, 55.0, 0.8))   # A1 = 33 -> offset +27
before = sorted(n[4] for p in m._patterns(m._load(tune), m._events(m._load(tune))).values() for n in p["notes"] if n[2] == c808)
rs = N.flp_load_sample(tune, tune, os.path.join(packs, "Drums", "808", "808 Deep A.wav"), channel=c808)
after = sorted(n[4] for p in m._patterns(m._load(tune), m._events(m._load(tune))).values() for n in p["notes"] if n[2] == c808)
jt = flh_store.journal_get(tune)
assert rs["tuning"]["written_offset"] == 27 and rs["tuning"]["notes_moved"] == len(before), rs
assert after == [k - 3 for k in before] and jt["channels"][str(c808)]["transpose"] == 27, (before[:4], after[:4])
assert jt["compose"]["transpose"][str(c808)] == 27

# 3) house at producer edition with 2 variations to audition, no opening
m._state({"fl_edition": "producer"})
n_open = len(opened)
r = N.flh_new_song("house", variations=2, open=False, name="Club")
assert len(opened) == n_open and "opened" not in r
d, ev, j, tnames, chans = (lambda p: (m._load(p), m._events(m._load(p)), flh_store.journal_get(p), None, None))(r["project"])
names = [p["name"] for p in m._patterns(d, ev).values()]
assert sum(1 for n in names if n.endswith(" Drums") or " Drums" in n) >= 3, names
assert "flp_compose(what='pick'" in r["try_next"][0], r["try_next"]
r2 = N.flh_new_song("house", variations=0, open=False, name="Club")             # never overwrites
assert r2["project"] != r["project"] and r2["project"].endswith(os.path.join("Club 2", "Club 2.flp")), r2["project"]

# the composer fails: the project is still made, empty, with section blocks on their own track (said honestly)
real_comp = ns["TOOLS"]["flp_compose"]
def boom(*a, **k):
    raise ValueError("composer offline")
ns["TOOLS"]["flp_compose"] = (boom,) + tuple(real_comp[1:])
r = N.flh_new_song("pop", open=False, name="NoBeat")
ns["TOOLS"]["flp_compose"] = real_comp
assert "starter" not in r and r.get("ok") is False and list(r)[:2] == ["ok", "warning"], r   # at the top (E2E-9)
assert any("no starter beat" in n and "composer offline" in n and "flp_compose(project, what='beat')" in n
           for n in r["warning"]), r
clips = m.flp_playlist(r["project"])["clips"]
order_len = len(N.recipe("pop")[0]["playlist"])
assert clips and all(c["track"] > 4 for c in clips), clips

# a small mixer: groups merge, then sends/buses go - all reported
d = m._load(blank)
assert N._insert_capacity(d) == 16
specs = [{"role": "kick", "name": f"K{i}", "group": "G" if i else None} for i in range(20)]
ins, buses, sends, notes, _ = N._plan_inserts(specs, N.recipe("lofi")[0], 6)
assert len(ins) + len(buses) + len(sends) <= 6 and notes, (len(ins), buses, sends, notes)

# ---------------------------------------------------------------- save template + new song from it
lofi = N.flh_new_song("lofi", name="Base For Template", open=False)["project"]
st = N.flh_save_template(lofi, "My Lofi", fl_templates=True)
assert os.path.isfile(st["stored"]) and st["cleared"]["notes"] > 0 and st["roles"], st
assert st["fl_template"] and os.path.isfile(st["fl_template"]), st     # FL's user Templates folder: allowed (.flp only)
tj = json.load(open(os.path.join(os.path.dirname(st["stored"]), "template.json")))
assert tj["genre"] == "lofi" and tj["format"] == "flh-user-template/1" and any(c["role"] == "kick" for c in tj["channels"])
assert not any(p["notes"] for p in m._patterns(m._load(st["stored"]), m._events(m._load(st["stored"]))).values())
r = N.flh_new_song(template="My Lofi", name="From Template", bpm=78, open=False)
d, ev, j, _, _ = (lambda p: (m._load(p), m._events(m._load(p)), flh_store.journal_get(p), None, None))(r["project"])
assert m.flp_info(r["project"])["tempo_bpm"] == 78 and j["genre"] == "lofi" and j["template"] == "My Lofi", j
assert len(flh_compose.channels_of(d, ev)) == len(tj["channels"]) and r.get("starter"), r
assert sum(len(p["notes"]) for p in m._patterns(d, ev).values()) > 0
os.environ["FLH_ROOTS"] = roots + ";" + os.path.join(docs, "Projects")             # FL's Templates folder allowed
os.makedirs(os.path.join(docs, "Projects"), exist_ok=True)
st = N.flh_save_template(lofi, "My Lofi 2", fl_templates=True)
assert st["fl_template"] and os.path.isfile(st["fl_template"]), st
# H5: a template the user made in FL with the same name is never replaced
user_t = os.path.join(docs, "Projects", "Templates", "Mine", "Mine.flp")
os.makedirs(os.path.dirname(user_t)); open(user_t, "wb").write(b"user's own FL template")
st = N.flh_save_template(lofi, "Mine", fl_templates=True)
assert open(user_t, "rb").read() == b"user's own FL template", "user template overwritten"
assert st["fl_template"].endswith(os.path.join("Mine 2", "Mine 2.flp")) and "nothing was replaced" in st["fl_template_note"], st
try:                                                                       # same fl-headless name again: refused
    N.flh_save_template(lofi, "Mine", fl_templates=True); raise SystemExit("must refuse to replace a template")
except ValueError as e:
    assert "overwrite=true" in str(e)
st2 = N.flh_save_template(lofi, "Mine", fl_templates=True, overwrite=True)
assert os.path.isfile(st2["replaced"]["backup"]) and open(user_t, "rb").read() == b"user's own FL template", st2
assert os.path.isfile(st2["fl_template"]) and st2["fl_template"] != user_t, st2
os.environ["FLH_ROOTS"] = roots
# a pitched sampler's offset survives the template round trip
st = N.flh_save_template(tune, "Drill Base")
assert st["sounding_offsets"] == {str(c808): 27}, st
tjd = json.load(open(os.path.join(os.path.dirname(st["stored"]), "template.json")))
assert tjd["compose"]["transpose"] == {str(c808): 27} and any(c.get("transpose") == 27 for c in tjd["channels"]), tjd
r = N.flh_new_song(template="Drill Base", name="From Drill", open=False)
jd = flh_store.journal_get(r["project"])
assert jd["channels"][str(c808)]["transpose"] == 27 and jd["compose"]["transpose"][str(c808)] == 27, jd["channels"]
d = m._load(r["project"]); ev = m._events(d)
kd = [n[4] for p in m._patterns(d, ev).values() for n in p["notes"] if n[2] == c808]
assert kd and min(kd) >= g["bass"]["register"][0] + 27 - 12, sorted(set(kd))

# ---------------------------------------------------------------- welcome, icon probe, errors
w = N.flh_welcome()
assert w["first_time"] is True and 6 <= len(w["try"]) <= 8 and len(w["promises"]) >= 4, w
assert any("lofi" in x for x in w["try"]) and m._state().get("welcomed")
assert N.flh_welcome()["first_time"] is False
assert len(json.dumps(w)) < 2500, len(json.dumps(w))
pr = N.flh_icon_probe(os.path.join(roots, "probe.flp"), start=0, count=5)
d = m._load(pr["output"]); ev = m._events(d)
icons = [struct.unpack("<I", m._payload(d, e))[0] for e in ev if e["id"] == m.EV_CHANNEL_ICON]
assert icons[:5] == [0, 1, 2, 3, 4] and len(m.flp_list(pr["output"])["channels"]) == 5, icons
for bad, kw in (("genre", {"genre": "polka-x"}), ("key", {"genre": "lofi", "key": "H dorian x"}),
                ("kit", {"genre": "lofi", "kit": "nope"}), ("roots", {"genre": "lofi", "folder": tmp})):
    try:
        N.flh_new_song(open=False, **kw); raise SystemExit(f"{bad} must fail")
    except (ValueError, PermissionError):
        pass

# ---------------------------------------------------------------- E2E-9: FL runs and its title names no project
# (FL starting / loading): the open-project guard can't vouch for any file, but the starter beat is composed into a
# brand-new scratch file FL can't have open, so it's still written; a project FL might have open is still refused
real = {k: ns[k] for k in ("fl_running", "_open_state", "IS_WINDOWS")}
ns.update(fl_running=lambda: True, _open_state=lambda p: "unknown", IS_WINDOWS=False)
try:
    r9 = N.flh_new_song("trap", name="Fresh FL", open=False)
    n9 = sum((m.flp_info(r9["project"]).get("notes_per_channel") or {}).values())
    assert n9 > 0 and r9.get("starter") and "warning" not in r9 and r9.get("ok", True), r9
    try:
        ns["TOOLS"]["flp_compose"][0](r9["project"], "melody", section="Hook")
        raise SystemExit("an edit of a project FL may have open must still be refused")
    except RuntimeError as e:
        assert "can't tell" in str(e), e
finally:
    ns.update(real)


# ---------------------------------------------------------------- FL's real empty template: one sampler, no end event
# (PC bug 2026-10-20: FL said "error reading the FLP" - the sampler's block was copied through the arrangement and
# mixer for every new sampler, so the song had 6 arrangements and 108 mixer tracks)
import fl_blocks  # noqa: E402
smp = [[e, bytes(p)] for e, p, _ in m._raw_events(fl_blocks.AUDIO_BLOCK)]
smp = [it for it in smp if it[0] not in (196, 170, 51)]
smp[0] = [21, b"\x00"]
blk = bytes([64]) + struct.pack("<H", 0) + b"".join(m._data_event(e, p) if e >= 192 else bytes([e]) + p for e, p in smp)
b_ev = m._events(bd); at = next(k for k, e in enumerate(b_ev) if e["id"] == m.EV_ARRANGEMENT)
fl_blank = bytearray(m._splice(bd, b_ev, [(at, at, blk)]))
fmt, _, ppq = struct.unpack("<hHH", fl_blank[8:14]); fl_blank[8:14] = struct.pack("<hHH", fmt, 1, ppq)
blank2 = os.path.join(tmp, "Empty FL.flp"); open(blank2, "wb").write(bytes(fl_blank))
assert [k for k, i, a, b in m._blocks(*(lambda x: (x, m._events(x)))(m._load(blank2))) if k == "channel"] == ["channel"]
os.environ["FLH_BLANK"] = blank2
try:
    r = N.flh_new_song("lofi", name="Template Shape", open=False, seed=7)
finally:
    os.environ["FLH_BLANK"] = blank
assert not any("no starter beat" in w for w in (r.get("warning") or [])), r.get("warning")
d = m._load(r["project"]); ev = m._events(d)
want = N._structure(m._load(blank2)); got = N._structure(d)
assert got["arrangements"] == want["arrangements"] and got["mixer tracks"] == want["mixer tracks"], (want, got)
spans = [(i, a, b) for k, i, a, b in m._blocks(d, ev) if k == "channel"]
assert struct.unpack("<hHH", d[8:14])[1] == len(spans), (d[8:14], len(spans))
for i, a, b in spans:
    ids = [ev[x]["id"] for x in range(a, b)]
    assert ids[-1] == 51 and not set(ids) & {99, 236, 233, 238}, (i, ids[-6:])
    if ids[1:2] == [21] and m._payload(d, ev[a + 1]) == b"\x00" and r["channels"]:
        pass
smp_paths = [k for k, e in enumerate(ev) if e["id"] == 196]
assert smp_paths and all(any(a < k < b for _, a, b in spans) for k in smp_paths), "a sample path outside its channel"


# ---------------------------------------------------------------- Plugin database entries are name-only references
# (PC 2026-10-20: FL 2026's 'Plugin database\\...\\Fruity Parametric EQ 2.fst' has no settings -> use its Default.fst)
ref_body = bytes([199, 9]) + b"11.5.5.0\x00" + bytes([201, 15]) + b"Fruity Tester\x00\x00" + bytes([203, 15]) + b"Fruity Tester\x00\x00"
ref = os.path.join(docs, "Presets", "Plugin database", "Installed", "Effects", "Fruity", "Fruity Tester.fst")
os.makedirs(os.path.dirname(ref), exist_ok=True); open(ref, "wb").write(make_flp(ref_body))
try:
    m._fst_block(ref); raise AssertionError("a bare reference must not load without its Default.fst")
except ValueError as e:
    assert "Plugin database" in str(e), e
dflt = os.path.join(docs, "Presets", "Plugin presets", "Effects", "Fruity Tester", "Default.fst")
fst(dflt, "Fruity Tester", effect=True)
blk_ev, plug = m._fst_block(ref)
assert plug == "Fruity Tester" and any(e == m.EV_PLUGIN_STATE for e, _, _ in blk_ev), (plug, [e for e, _, _ in blk_ev])
assert m._fst_default_for("fruity tester") == dflt


# ---------------------------------------------------------------- FL 2026's own Empty.flp (private test data)
REAL_EMPTY = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "fl2026_empty.flp")
if os.path.isfile(REAL_EMPTY):
    os.environ["FLH_BLANK"] = REAL_EMPTY
    try:
        r = N.flh_new_song("lofi", name="Real Empty", open=False, seed=7)
    finally:
        os.environ["FLH_BLANK"] = blank
    assert not r.get("warning"), r.get("warning")
    d = m._load(r["project"]); ev = m._events(d)
    want = N._structure(m._load(REAL_EMPTY)); got = N._structure(d)
    assert (got["arrangements"], got["mixer tracks"]) == (want["arrangements"], want["mixer tracks"]), (want, got)
    blocks = m._blocks(d, ev)
    chans = [b for b in blocks if b[0] == "channel"]
    assert all(ev[b - 1]["id"] == 51 for _, _, a, b in chans), "every channel closes with 51"
    first_meta = next(k for k, e in enumerate(ev) if e["id"] == 65 and ev[k + 1]["id"] in m.PATTERN_META)
    assert first_meta >= chans[0][3], "pattern names after the first channel, as FL keeps them"
    assert m._patterns(d, ev) and any(p["notes"] for p in m._patterns(d, ev).values()), "starter beat written"
    assert next(m._payload(d, e) for e in ev if e["id"] == 9) == b"\x01", "new songs open in song mode"
    f169 = next(struct.unpack("<I", m._payload(d, e))[0] for e in ev if e["id"] == 169)
    assert f169 & 0x08, f"FL 2026 reads 88-byte clips only with 169 bit 0x08 (got {f169:#x})"
    e0 = m._events(m._load(REAL_EMPTY))
    assert next(struct.unpack("<I", m._payload(m._load(REAL_EMPTY), e))[0] for e in e0 if e["id"] == 169) == 7
    clipless = os.path.join(roots, "clipless.flp"); m._save(m._load(REAL_EMPTY), clipless)
    assert m._load(clipless) == m._load(REAL_EMPTY), "no clips: the flag is left as FL wrote it"


# ---------------------------------------------------------------- presets load with their plugin window closed
hdr = bytearray(52); hdr[8] = 2; hdr[16:20] = struct.pack("<I", 0x145)
win = os.path.join(docs, "Presets", "Plugin presets", "Effects", "Open Window", "Default.fst")
os.makedirs(os.path.dirname(win), exist_ok=True)
open(win, "wb").write(make_flp(m._data_event(201, "Open Window".encode("utf-16-le") + b"\x00\x00") +
                               m._data_event(212, bytes(hdr)) + m._data_event(213, b"\x01\x02")))
hb = next(p for e, p, _ in m._fst_block(win)[0] if e == 212)
assert struct.unpack("<I", hb[16:20])[0] == 0x140, hb[16:20].hex()

# ---------------------------------------------------------------- every genre has a recipe that loads
for gname in sorted(k for k in flh_compose.genres() if not k.startswith("_")):
    rec, g = N.recipe(gname)
    assert rec["format"] == "flh-template/1" and rec["genre"] == gname and rec["channels"], gname
    roles = {c["role"] for c in rec["channels"]}
    assert {"bass", "chords", "melody"} <= roles and roles & set(N.DRUM_ROLES), (gname, roles)
    for c in rec["channels"]:
        fam = c.get("family") or c["role"]
        assert c.get("choices") or fam in rec["choices"] or c["role"] in N.SAMPLER_ROLES, (gname, c)
# ---------------------------------------------------------------- stems: grouped, FLAC, measured, silent packs noted
rainy = os.path.join(os.path.dirname(os.path.dirname(night)), "Rainy Desk", "Rainy Desk.flp")
jr_ = flh_store.journal_get(rainy)
keys_ch = next(int(c) for c, v in jr_["channels"].items() if v.get("role") in ("chords", "keys"))
flh_store.journal_update(rainy, set={"channels": {str(keys_ch): {"plugin": "INSTRUMENT",
                                                                  "sound": "Old Pack - Test / Preset"}}})
_real_render = m._render
def _fake_render(project, out_dir, formats, timeout_s, data=None, out_stem=None):
    dd = data; evv = m._events(dd)
    chs = {r_[2] for v in m._patterns(dd, evv).values() for r_ in v["notes"]}
    f = os.path.join(out_dir, out_stem + ".wav")
    quiet = chs == {keys_ch}
    wav(f, np.zeros(fs) if quiet else 0.3 * np.sin(2 * np.pi * 110 * t(1.0)))
    return {"rendered": [f]}
m._render = _fake_render
running_ = m.fl_running; m.fl_running = lambda: False
try:
    st = m._render_stems(rainy, None, None, "flac", True, True)
    assert all(f.endswith(".flac") for f in st["stems"]) and all(os.path.isfile(f) for f in st["stems"]), st
    assert not any(f.endswith(".wav") for f in os.listdir(st["folder"])), os.listdir(st["folder"])   # WAVs replaced
    assert any(lv.get("silent") for lv in st["levels"]) and "Old Pack - Test" in " ".join(st["silent"]), st
    assert "Old Pack - Test" in (m._state().get("silent_packs") or {}), m._state().get("silent_packs")
    g = m._render_stems(rainy, os.path.join(roots, "gst"), None, "wav", True, True, groups="roles", measure=True)
    names = [lv["stem"] for lv in g["levels"]]
    assert "Drums" in names and "Music" in names and all(f.endswith(".wav") for f in g["stems"]), g
    inst = m._render_stems(rainy, os.path.join(roots, "ist"), None, "wav", True, True, groups="instrumental")
    assert [lv["stem"] for lv in inst["levels"]] == ["Instrumental"], inst
    # in the open FL: each stem through FL's own export with the other channels muted live; mutes put back, FL stays
    d_ = m._load(rainy); n_ch = len(m._structure(d_)[1])
    live = {i: (i == 0) for i in range(n_ch)}                       # the user had channel 0 muted
    heard, calls = [], []
    saved = {k: getattr(m, k) for k in ("fl_state", "fl_channel_set", "_render_in_fl", "_open_project_guess",
                                         "fl_close", "_sync_before")}
    m.fl_running = lambda: True
    m.fl_state = lambda: {"channels": [{"index": i, "muted": v} for i, v in live.items()]}
    def _set(i, muted=None, **k):
        import threading as _th
        if _th.current_thread() is not _th.main_thread():          # a new song's background balance: not this test
            return
        calls.append((i, muted)); live[i] = bool(muted)
    m.fl_channel_set = _set
    def _in_fl(project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None):
        import threading as _th
        if _th.current_thread() is _th.main_thread():              # not a new song's background balance
            heard.append({i for i, v in live.items() if not v})
        f = os.path.join(out_dir, out_stem + ".wav"); wav(f, 0.3 * np.sin(2 * np.pi * 110 * t(1.0)))
        return {"rendered": [f]}
    m._render_in_fl = _in_fl
    m._open_project_guess = lambda: (rainy, "test")
    m._sync_before = lambda *a, **k: None
    def _no_close(**k):
        raise AssertionError("FL must stay open")
    m.fl_close = _no_close
    try:
        lv = m._render_stems(rainy, os.path.join(roots, "lst"), None, "flac", True, True, groups="roles", method="in_fl")
        assert lv["method"] == "in_fl" and len(heard) == len(lv["levels"]) >= 2, lv
        grp = {x["stem"]: set(x["channels"]) for x in lv["levels"]}
        autos = m._automation_channels(m._load(rainy))           # automation clips play in every stem
        for h, (nm, chs) in zip(heard, grp.items()):
            assert h - autos == chs - {0} and autos - {0} <= h, (nm, h, chs)   # that stem's channels (user mutes kept)
        assert live == {i: (i == 0) for i in range(n_ch)}, live      # the user's mutes are back
        assert all(f.endswith(".flac") for f in lv["stems"]), lv
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
finally:
    m._render = _real_render; m.fl_running = running_

# ---------------------------------------------------------------- FL's own multisampled instruments (Packs/Instruments)
fst(os.path.join(packs, "Instruments", "Bass", "Acoustic.fst"), "DirectWave")
cat_i = C.fl_sound_catalog(query="acoustic bass", refresh=True, type="instrument")
hit = next(x for x in cat_i["results"] if x["source"] == "FL instruments")
assert hit["kind"] == "bass" and hit["owned"] is True and hit["load"]["tool"] == "fl_load_preset", hit
r = N.flh_new_song("lofi", name="Upright", open=False)
bass_line = next(c for c in r["channels"] if c.split(" ", 2)[1].startswith("Bass"))
assert "Acoustic" in bass_line, r["channels"]                        # a recorded bass for an acoustic genre
r = N.flh_new_song("techno", name="Synthy", open=False)
assert not any("Acoustic" in c for c in r["channels"]), r["channels"]  # electronic genres keep synths
# ---------------------------------------------------------------- FLEX sound quality: Core / Essential packs beat the
# General MIDI Library for every genre (prefer_flex or not); GM only when nothing else of the kind exists
import flh_flex as F  # noqa: E402
F.register(ns)
FLEXD = os.environ["FLH_FLEX_DIR"]; os.makedirs(os.path.join(FLEXD, "Packs"), exist_ok=True)


def flexpack(name, presets):
    ents = [("presetsIndex.presetsIndex", b"\x13\x37" * 8)] + [(x + ".flexpreset", b"P" * 32) for x in presets]
    base = 8 + sum(4 + len(n) + 1 + 16 for n, _ in ents); tab = body = b""
    for n, data in ents:
        raw = n.encode() + b"\0"
        tab += struct.pack(">I", len(raw)) + raw + struct.pack(">QQ", base + len(body), len(data)); body += data
    with open(os.path.join(FLEXD, "Packs", name + ".flexpack"), "wb") as f:
        f.write(struct.pack(">II", 2, len(ents)) + tab + body)


GM = ["Electric Grand Piano", "Electric Piano 1", "Organ 1", "Choir Aahs", "Fretless Bass Mellow", "Muted Trumpet",
      "Church Bell", "Tubular Bells", "Warm Pad", "String Ensemble 1", "Acoustic Guitar Nylon", "Distortion Guitar",
      "Square Lead", "Kalimba", "Flute", "Poly Synth", "808 Bass"]
GOOD = {"Core Pianos": ["KEY AG Fused Piano", "KEY Felt Upright", "KEY Rhodes Suitcase", "KEY Dark Grand"],
        "Core Guitars": ["GTR Electric Mellow", "GTR Distorted Lead", "GTR Nylon Soft", "GTR Heavy Rhythm",
                         "GTR Overdrive Crunch"],
        "Core 808s": ["808 Heavy Atom", "808 Long Deep"],
        "Essential Bass Guitars": ["Upright Bass", "Fingered Electric Bass", "Picked Bass"],
        "Core Organs": ["ORG B3 Gospel Drawbar", "ORG Soul Combo"],
        "Core Strings": ["STR Lush Ensemble", "STR Cello Section", "STR Legato Warm"],
        "Core Choirs": ["VOX Gospel Choir", "VOX Epic Choir Lush"],
        "Core Brass": ["BRS Horns Sustain", "BRS Soul Section"],
        "Core Woodwinds": ["WND Flute Soft", "WND Sax Breathy"],
        "Core Mallets": ["BELL Dark Music Box", "BELL Glock Soft", "PLK Kalimba Soft"],
        "Melodic Techno": ["PAD Ala Talis", "PAD Warm Tape Haze", "PAD Dark Drone Atmos", "PAD Lush Evolving",
                           "SYN Dark Stab", "SYN Juno Poly", "SYN Supersaw Wide", "PLK Analog Pluck", "PLK House Chord",
                           "LD Analog Saw", "LD Soft Smooth", "LD Bright Square"]}
flexpack("General Midi Library", GM)
for pk, pre in GOOD.items():
    flexpack(pk, pre)
with open(os.path.join(FLEXD, "cache.json"), "w", encoding="utf-8-sig") as f:
    json.dump([{"productName": "Melodic Techno", "filename": "/FLEX/Melodic Techno.flexpack", "genre": "Techno",
                "instrument": "Synth"}], f)
assert F.pack_tier("General Midi Library")[0] == 0 and F.pack_tier("Core Pianos")[0] == 3
GMSET = {x.lower() for x in GM}


def melodic(rec):
    return [s for s in rec["channels"] if s["role"] not in N.SAMPLER_ROLES]


def resolve_all(g):
    N._FLEX_USED.clear()
    rec, _ = N.recipe(g)
    out = {}
    for spec in melodic(rec):
        out[spec["name"]] = N._resolve(copy.deepcopy(spec), rec, g, [], None, [], set())
    return out


import copy  # noqa: E402
m._state({"fl_edition": "producer"})
for pf in (True, False):
    flh_store.profile_set("Songs", {"prefer_flex": pf})
    assert N._prefer_flex() is pf
    picked = {}
    for g in sorted(N.recipes()):
        for nm, r in resolve_all(g).items():
            lab = r.get("label") or ""
            assert "General Midi" not in lab, (pf, g, nm, r)        # GM never beats a decent sound of the kind
            spec = next(x for x in N.recipe(g)[0]["channels"] if x["name"] == nm)
            if pf and ("sample" not in (spec.get("choices") or [{}])[0] or spec.get("family") == "808"):   # (a vocal chop / cowbell keeps its sample)
                assert r.get("plugin") == "FLEX", (g, nm, r)        # the profile's FLEX preference is respected
            picked[(g, nm)] = lab
    if pf:
        PICKED = picked
    if os.environ.get("FLH_SHOW"):
        for k in sorted(picked):
            print("  prefer_flex" if pf else "  neutral", k, "->", picked[k])
assert "Rhodes" in PICKED[("jazzhop", "Rhodes")] and "Upright Bass" in PICKED[("jazzhop", "Upright Bass")], PICKED
assert "B3" in PICKED[("gospel", "Organ")] and "Gospel Choir" in PICKED[("gospel", "Choir")], PICKED
assert "Felt" in PICKED[("lofi", "Keys")] or "Rhodes" in PICKED[("lofi", "Keys")], PICKED
assert "Upright" in PICKED[("lofi", "Bass")] and "Distorted" in PICKED[("metal", "Lead Guitar")], PICKED
assert "Dark" in PICKED[("trap", "Bells")] and "Stab" in PICKED[("house", "Stabs")], PICKED

# the starter beat's channel list names the chosen presets
flh_store.profile_set("Songs", {"prefer_flex": True})
N._FLEX_USED.clear()
r = N.flh_new_song("lofi", name="Core Keys", open=False)
keys = next(c for c in r["channels"] if c.split(" ", 2)[1] == "Keys:")
assert "FLEX" in keys and "(Core Pianos)" in keys and "General Midi" not in " ".join(r["channels"]), r["channels"]

# only the General MIDI Library left: a real stock preset still wins; GM only where nothing else fits
for pk in GOOD:
    os.remove(os.path.join(FLEXD, "Packs", pk + ".flexpack"))
assert {x["pack"] for x in F.index()} == {"General Midi Library"}
for pf in (True, False):
    flh_store.profile_set("Songs", {"prefer_flex": pf})
    lofi_r = resolve_all("lofi"); gospel_r = resolve_all("gospel")
    assert "Sytrus 'Rhodes Classic'" in lofi_r["Keys"]["label"], lofi_r["Keys"]      # stock rhodes > GM piano
    org = gospel_r["Organ"]                                                            # no organ anywhere else:
    assert org["label"] == "FLEX Organ 1 (General Midi Library)" and "General MIDI" in org["hint"], org   # GM, said
m._state({"fl_edition": "fruity"})                                   # no Sytrus: FL Keys (a real piano) beats GM
flh_store.profile_set("Songs", {"prefer_flex": True})
assert resolve_all("lofi")["Keys"]["label"].startswith("FL Keys"), resolve_all("lofi")["Keys"]
flh_store.profile_set("Songs", {"prefer_flex": False})
m._state({"fl_edition": "producer"})

# a gospel organ is a B3 / drawbar / tonewheel (PC 2026-10-23: 'H Electronic Organ Buzz' was picked): with only an
# unrated pack of mixed organs, the tonewheel wins; without it a church organ beats the buzzy / reed / Farfisa ones;
# a part that asks for a Farfisa still gets one (asked-for words are never 'bad')
ORGANS = ["H Electronic Organ Buzz", "H Tonewheel Organ", "H Church Organ", "H Reed Organ", "H Farfisa Organ"]
flexpack("Organ Mix", ORGANS)
assert F.name_quality("H Electronic Organ Buzz", "organ") < 0 < F.name_quality("H Tonewheel Organ", "organ")
assert F.name_quality("H Farfisa Organ", "organ", asked=["farfisa"]) == 0 > F.name_quality("H Farfisa Organ", "organ")
for pf in (True, False):
    flh_store.profile_set("Songs", {"prefer_flex": pf})
    assert resolve_all("gospel")["Organ"]["label"] == "FLEX H Tonewheel Organ (Organ Mix)", resolve_all("gospel")["Organ"]
    assert "Tonewheel" in resolve_all("boombap")["Keys"]["label"] or not pf, resolve_all("boombap")["Keys"]
plain = [h["preset"] for h in F.flex_search(None, kind="organ", limit=8)]             # no character: quality words only
assert plain[0] == "H Tonewheel Organ" and plain.index("H Electronic Organ Buzz") > plain.index("H Church Organ"), plain
assert F.flex_search(None, kind="organ", prefer="farfisa", limit=3)[0]["preset"] == "H Farfisa Organ"
os.remove(os.path.join(FLEXD, "Packs", "Organ Mix.flexpack"))
flexpack("Organ Mix 2", [o for o in ORGANS if "Tonewheel" not in o])
flh_store.profile_set("Songs", {"prefer_flex": True})
assert resolve_all("gospel")["Organ"]["label"] == "FLEX H Church Organ (Organ Mix 2)", resolve_all("gospel")["Organ"]
os.remove(os.path.join(FLEXD, "Packs", "Organ Mix 2.flexpack"))
flh_store.profile_set("Songs", {"prefer_flex": False})

print("newsong ok")
