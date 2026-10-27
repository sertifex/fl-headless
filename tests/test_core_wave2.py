"""Core wave 2: tool tiers + discovery, parse cache, flh_run parallel / dry-run / estimates, slide notes, channel
glide + insert, empty-project fallbacks, markers / meters / MIDI export, tempo lane + tempo automation, fl_mix_check
fields, CROSS_PLATFORM / lazy sets, and the new bridge-script commands (fake FL modules).
Run: python3 tests/test_core_wave2.py"""
import json
import os
import shutil
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
TMP = tempfile.mkdtemp(prefix="flh_w2_")
os.environ["FLH_ROOTS"] = TMP
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
os.environ.pop("FLH_TOOLSET", None)
import fl_headless_mcp as m  # noqa: E402
import fl_music  # noqa: E402

ns = vars(m)
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
m.fl_running = lambda: False
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)


def copy(name):
    p = os.path.join(TMP, name)
    shutil.copy(FIX, p)
    return p


# ============================================================================= tool surface
lst = m.handle({"method": "tools/list"})["tools"]
names = [t["name"] for t in lst]
assert 30 <= len(names) <= 52, len(names)               # the essentials stay small: every name costs tokens
for must in ("flh_tools", "flh_call", "flh_run", "fl_status", "flh_setup", "fl_watchdog", "fl_open", "flh_sync",
             "flp_compose", "flp_edit", "fl_review", "fl_finalize", "fl_listen", "fl_mix_engineer", "audio_to_midi"):
    assert must in names, must
assert "fl_settings_set" not in names and "fl_settings_set" in m.TOOLS
for t in lst:                                                   # short descriptions and parameter help
    assert len(t["description"]) <= 290, (t["name"], len(t["description"]))
    for p, spec in t["inputSchema"]["properties"].items():
        assert len(spec.get("description", "")) <= 80, (t["name"], p)
size = len(json.dumps({"tools": lst}))
ins = m.handle({"method": "initialize", "params": {}})["instructions"]
assert "## HOW TO WORK WITH PEOPLE" in ins and len(ins) < 4800 and "flh_tools" in ins, len(ins)
assert size + len(ins) < 36000, size                            # ~9k tokens incl. instructions
assert any("options: flh_tools" in t["description"] for t in lst)  # hidden optional params are announced
os.environ["FLH_TOOLSET"] = "full"
full = m.handle({"method": "tools/list"})["tools"]
assert len(full) == len(m.TOOLS)
os.environ["FLH_TOOLSET"] = "vocal,flp_tempo_write"
grp = [t["name"] for t in m.handle({"method": "tools/list"})["tools"]]
assert "fl_vocal_comp" in grp and "flp_tempo_write" in grp and "flh_tools" in grp and "flp_compose" not in grp, grp
os.environ.pop("FLH_TOOLSET")
g = m.flh_tools()
assert g["total"] == len(m.TOOLS) and "vocal" in g["groups"] and "fl_vocal_comp" in g["groups"]["vocal"]
assert all(m.tool_group(n) != "other" for n in m.TOOLS), [n for n in m.TOOLS if m.tool_group(n) == "other"]
q = m.flh_tools(query="sidechain duck")
assert q["tools"] and q["tools"][0][0] == "flp_duck", q
one = m.flh_tools(name="flp_tempo_write")
assert one["listed"] is False and "flh_call" in one["call"] and "properties" in one["arguments"]
assert "did_you_mean" in m.flh_tools(name="tempo_write")
two = m.flh_tools(name=["flp_info", "nope"])
assert "flp_info" in two and "error" in two["nope"]
src = copy("surface.flp")
r = m.handle({"method": "tools/call", "params": {"name": "flh_call", "arguments": {"tool": "flp_tempo_map",
                                                                                  "args": {"path": src}}}})
assert not r.get("isError") and "map" in json.loads(r["content"][0]["text"]), r
r = m.handle({"method": "tools/call", "params": {"name": "flp_list", "arguments": {"path": src}}})  # unlisted, direct
assert not r.get("isError")
st = m.fl_status()
assert st["toolset"]["listed"] == len(names) and st["toolset"]["set"] == "essential"
print("tool surface ok", len(names), "tools,", size + len(ins), "chars")

# ============================================================================= parse cache
p = copy("cache.flp")
os.environ["FLH_PARSE_CACHE"] = "0"
ref = (m.flp_info(p), m.flp_list(p), m.flp_patterns(p), m.flp_playlist(p), m.flp_tempo_map(p))
os.environ["FLH_PARSE_CACHE"] = "1"
got = (m.flp_info(p), m.flp_list(p), m.flp_patterns(p), m.flp_playlist(p), m.flp_tempo_map(p))
assert json.dumps(ref, sort_keys=True, default=str) == json.dumps(got, sort_keys=True, default=str)
before = dict(m._PARSE_STATS)
m.flp_info(p); m.flp_info(p)
assert m._PARSE_STATS["hits"] > before["hits"]
d = m._load(p); ev = m._events(d)
pats = m._patterns(d, ev); pats[1]["notes"].clear(); pats[1]["name"] = "X"; ev.clear()  # callers may edit theirs
assert m._patterns(d, m._events(d))[1]["notes"] and m._events(d)
_, chans, _ = m._structure(d); chans[0]["name"] = "hacked"
assert m._structure(d)[1][0].get("name") != "hacked"
n0 = m.flp_info(p)["notes_per_channel"]
m.flp_add_notes(p, [{"note": "C4", "bar": 1, "beat": 0, "beats": 1}], pattern=1, channel=0)
assert m.flp_info(p)["notes_per_channel"][0] == n0[0] + 1           # our own write invalidates
d2 = bytearray(m._load(p)); open(p, "wb").write(bytes(d2))           # an outside rewrite (same content) still reads
assert m.flp_info(p)["notes_per_channel"][0] == n0[0] + 1
print("parse cache ok", m.parse_cache_stats())

# ============================================================================= chords (bucketed = original)
import random  # noqa: E402
rnd = random.Random(5)
notes = [{"pos": rnd.randrange(0, 4000), "len": rnd.randrange(0, 700), "key": rnd.randrange(30, 90)} for _ in range(200)]
assert m._chords_by_segment(notes, 384) == fl_music.chords_by_segment(notes, 384)
print("chords ok")

# ============================================================================= flh_run: parallel, dry run, estimates
a = copy("run_a.flp"); b = copy("run_b.flp")
steps = [{"tool": "flp_info", "args": {"path": a}}, {"tool": "flp_patterns", "args": {"path": b}},
         {"tool": "flp_tempo_map", "args": {"path": a}}, {"tool": "flp_set_tempo", "args": {"path": b, "bpm": 99}},
         {"tool": "flp_info", "args": {"path": "$prev.path"}}]
groups = m._run_groups(steps, "auto")
assert groups == [[0, 1, 2], [3], [4]], groups
dry = m.flh_run(steps, dry_run=True)
assert dry["dry_run"] and dry["parallel_groups"] == 1 and all("estimate_s" in s for s in dry["plan"]), dry
assert any(s.get("writes") for s in dry["plan"]) and dry["plan"][4].get("uses_prev")
res = m.flh_run(steps)
assert res["ok"] and res["ran"] == 5 and res["parallel"]["groups"] == 1, res
assert [s["step"] for s in res["steps"]] == [1, 2, 3, 4, 5]
assert res["steps"][4]["result"]["tempo_bpm"] == 99.0
seq = m.flh_run(steps[:3], parallel=False)
assert "parallel" not in seq and [s["result"] for s in seq["steps"]] == [s["result"] for s in res["steps"][:3]]
bad = m.flh_run([{"tool": "flp_info", "args": {"path": a}}, {"tool": "nope"}, {"tool": "flp_info", "args": {"path": a}}])
assert not bad["ok"] and bad["ran"] == 2 and "stopped" in bad
assert m._run_groups([{"tool": "fl_ping", "parallel": True}, {"tool": "flp_info", "parallel": True},
                      {"tool": "flp_set_tempo", "parallel": True}], True) == [[0, 1], [2]]
e = m.estimate("flp_info", {"path": a})
assert e["estimate_s"] > 0 and "measured" in e["basis"]
e = m.estimate("fl_listen", {"start_bar": 1, "end_bar": 9, "project": a})
assert e["estimate_s"] > 4 and "real time" in e["basis"], e
e = m.estimate("fl_finalize", {"project": a})
assert "render speed" in e["basis"], e
secs, bars, bpb = m._song_seconds(a)
assert secs > 0 and bars > 0 and bpb == 4
print("flh_run ok", res["parallel"])

# ============================================================================= slide notes
p = copy("slide.flp")
r = m.flp_replace_part(p, p, channel=1, notes=[[1, 0.5, "C3", 100], [1.5, 0.25, "G3", 100, {"slide": True}],
                                               {"bar": 2, "len": 0.5, "key": 50, "vel": 90, "slide": True}])
assert r["slides"] == 2 and r["added"] == 3, r
d = m._load(p); ev = m._events(d)
recs = [x for v in m._patterns(d, ev).values() for x in v["notes"] if x[2] == 1]
assert sum(1 for x in recs if x[1] & m.NOTE_SLIDE) == 2 and all(x[1] & 0x4000 for x in recs)
rows = m.flp_notes(p, channel=1)["notes"]
assert sum(1 for x in rows if x.get("slide")) == 2
m.flp_edit_notes(p, [{"op": "slide", "on": False}], channel=1)
d = m._load(p); assert not any(x[1] & m.NOTE_SLIDE for v in m._patterns(d, m._events(d)).values() for x in v["notes"])
m.flp_add_notes(p, [{"note": "D3", "bar": 1, "beat": 1, "beats": 1, "slide": True}, {"note": "E3", "bar": 1, "beat": 2}],
                pattern=1, channel=1)
d = m._load(p); fl = [x[1] for v in m._patterns(d, m._events(d)).values() for x in v["notes"] if x[2] == 1 and x[4] in (50, 52)]
assert sorted(bool(f & m.NOTE_SLIDE) for f in fl) == [False, False, True], fl   # + the earlier D3
out = os.path.join(TMP, "written.flp")
m.flp_write_notes(FIX, out, [{"channel": 0, "notes": [[1, 1, 40, 100], [1.5, 0.5, 47, 100, True]]}], song_length=None)
d = m._load(out); n_ = m._notes_event(d, m._events(d))[1]
assert len(n_) == 2 and [bool(x[1] & m.NOTE_SLIDE) for x in sorted(n_)] == [False, True]
print("slide notes ok")

# ============================================================================= channel insert + glide
d = m._load(FIX)
assert m._channel_inserts(d) == {0: 3, 1: 4, 2: 5, 3: 0, 4: 6, 5: 0}, m._channel_inserts(d)
assert m._channel_insert(FIX, 2) == 5 and ns["_channel_insert"] is m._channel_insert
p = copy("glide.flp")
r = m.flp_channel_glide(p, p, channel=0, slide=300)
assert r["before"] == {"mono": False, "porta": False, "slide": 500, "max_voices": 0} and r["after"]["mono"] and \
    r["after"]["porta"] and r["after"]["slide"] == 300, r
r = m.flp_channel_glide(p, p, channel=0, mono=False, porta=None)
assert r["after"] == {"mono": False, "porta": True, "slide": 300, "max_voices": 0}, r
assert "flp_channel_glide" in m.LAZY_TOOLS and "flp_channel_glide" in m._OPEN_PROJECT_GUARD
print("insert + glide ok")

# ============================================================================= empty projects
d = m._load(FIX); ev = m._events(d)
k_meta = next(k for k, e in enumerate(ev) if e["id"] == m.EV_PATTERN_NEW and ev[k + 1]["id"] == m.EV_PATTERN_NAME)
j = k_meta + 1
while ev[j]["id"] in m.PATTERN_META:
    j += 1
kp = next(k for k, e in enumerate(ev) if e["id"] == m.EV_PLAYLIST)
pl = m._payload(d, ev[kp])
no_pat_clips = b"".join(pl[i:i + 88] for i in range(0, len(pl), 88) if struct.unpack("<H", pl[i + 6:i + 8])[0] < m.PATTERN_BASE)
bare = os.path.join(TMP, "bare.flp")
m._save(m._splice(d, ev, [(k_meta, j, b""), (kp, kp + 1, m._data_event(m.EV_PLAYLIST, no_pat_clips))]), bare)
assert not any(v["name"] for v in m._patterns(m._load(bare), m._events(m._load(bare))).values())
r = m.flp_new_pattern(bare, "Verse", place_at_bar=5, length_bars=4)
d = m._load(bare); ev = m._events(d)
assert m._patterns(d, ev)[r["pattern"]]["name"] == "Verse"
cl = [c for c in m.flp_playlist(bare)["clips"] if c["kind"] == "pattern"]
assert len(cl) == 1 and cl[0]["bar"] == 5.0 and cl[0]["length_bars"] == 4.0, cl
noplay = os.path.join(TMP, "noplay.flp")                          # no playlist event at all
d = m._load(FIX); ev = m._events(d); kp = next(k for k, e in enumerate(ev) if e["id"] == m.EV_PLAYLIST)
m._save(m._splice(d, ev, [(kp, kp + 1, b"")]), noplay)
r = m.flp_new_pattern(noplay, "Hook", place_at_bar=1)
assert [c["name"] for c in m.flp_playlist(noplay)["clips"]] == ["Hook"]
print("empty projects ok")

# ============================================================================= markers, meters, MIDI export
p = copy("markers.flp")
d = m._load(p); ev = m._events(d); P = m._ppq(d)
k99 = next(k for k, e in enumerate(ev) if e["id"] == 99)
mk = b""
for tick, name, sig in ((0, "Intro", None), (8 * 4 * P, "Waltz", (3, 4)), (8 * 4 * P + 4 * 3 * P, "Chorus", None)):
    mk += bytes([148]) + struct.pack("<I", tick | (m.MARKER_SIGNATURE if sig else 0))
    if sig:
        mk += bytes([33, sig[0], 34, sig[1]])
    mk += m._data_event(205, name.encode("utf-16-le") + b"\x00\x00")
m._save(m._splice(d, ev, [(k99 + 1, k99 + 1, mk)]), p)
info = m.flp_info(p)
assert info["time_signature"] == "4/4" and info["time_signature_changes"] == [{"bar": 9.0, "time_signature": "3/4"}]
assert [(x["bar"], x["name"]) for x in info["markers"]] == [(1.0, "Intro"), (9.0, "Waltz"), (13.0, "Chorus")], info["markers"]
assert m.flp_playlist(p)["markers"][1]["time_signature"] == "3/4"
mid = os.path.join(TMP, "out.mid")
r = m.flp_export_midi(p, mid)
assert r["time_signatures"] == ["4/4", "3/4"], r
raw = open(mid, "rb").read()
assert raw.count(b"\xff\x58\x04") == 2 and b"\xff\x58\x04\x03\x02\x18\x08" in raw
assert ns["_markers"] is m._markers
print("markers + meters ok")

# ============================================================================= tempo lane + tempo automation
p = copy("tempo.flp")
assert "Tempo" not in [x["name"] for x in m.flp_patterns(p)]
r = m.flp_tempo_write(p, p, points=[[1, 100], [5, 110], [9, 120]])
assert r["created"]["clip_track"] >= 1 and r["tempo_points"] == 3, r
tm = m.flp_tempo_map(p)
assert [x["bpm"] for x in tm["map"]] == [100.0, 110.0, 120.0] and tm["map"][1]["bar"] == 5.0, tm
r2 = m.flp_tempo_write(p, p, points=[[1, 90], [3, 95]])           # second time: in place, nothing new
assert "created" not in r2 and [x["bpm"] for x in m.flp_tempo_map(p)["map"]] == [90.0, 95.0]
p = copy("tempo_auto.flp")
r = m.flp_add_automation(p, p, points=[{"bar": 3, "value": 0.0}, {"bar": 5, "value": 1.0}], track=0, param="volume",
                         name="Tempo ramp")
d = m._load(p); ev = m._events(d)
k = next(k for k, e in enumerate(ev) if e["id"] == m.EV_REMOTE_LINK and struct.unpack("<H", m._payload(d, e)[2:4])[0] == r["channel"])
lk = bytearray(m._payload(d, ev[k])); lk[8:12] = struct.pack("<HH", 5, 0x4000)
m._save(m._splice(d, ev, [(k, k + 1, m._data_event(m.EV_REMOTE_LINK, bytes(lk)))]), p)
d = m._load(p); ev = m._events(d)
_, tmp_ = m._song(d, ev)
start = 2 * 4 * P
assert any(t == start and abs(b - 10.0) < 0.01 for t, b in tmp_), tmp_[:5]
assert abs(max(b for _, b in tmp_) - 522.0) < 0.01
tm = m.flp_tempo_map(p)
assert tm["tempo_automation_clips"] == 1 and tm["max_bpm"] > 500
print("tempo lane + automation ok")

# ============================================================================= fl_mix_check fields
st = {"channels": [{"plugin": "x", "insert": 3}, {"plugin": "y", "insert": 4}],
      "mixer": [{"index": 0, "name": "Master", "effects": []}, {"index": 3, "name": "Keys", "effects": []},
                {"index": 4, "name": "Keys", "effects": []}]}
m.fl_state = lambda: st
m._sync_before = lambda *a, **k: None
m.BRIDGE.call = lambda *a, **k: {}
meas = {"lufs": -20.0, "bands_db": {"sub": -30, "bass": -10, "low_mid": -8, "mid": -6, "presence": -12, "air": -20},
        "crest_db": 12.5, "width_db": -9.0, "lr_correlation": 0.8, "centroid_hz": 900, "peak_dbfs": -3.0}
m._listen_measure = lambda *a: dict(meas)
m._open_project_guess = lambda: (None, "")
r = m.fl_mix_check(1, 5, window_s=0)
tr = r["tracks"]
assert set(tr) == {"Keys (3)", "Keys (4)"}, tr
assert tr["Keys (3)"]["index"] == 3 and tr["Keys (3)"]["crest_db"] == 12.5 and tr["Keys (4)"]["centroid_hz"] == 900
assert {"width_db", "lr_correlation"} <= set(tr["Keys (3)"])
print("mix check fields ok")

# ============================================================================= sets
assert {"flp_compose", "flp_arrange_song", "flp_adjust"} <= m.NOT_READERS and "flp_compose_write" in m.LAZY_TOOLS
for t in ("fl_vocal_comp", "fl_vocal_clean", "fl_vocal_double", "fl_vocal_chain", "fl_vocal_balance", "audio_to_midi",
          "capture_from_midi_file", "flp_score", "flp_clashes", "fl_reference_match", "fl_verify_intent",
          "fl_platform_preview", "fl_masking"):
    assert t in m.CROSS_PLATFORM, t
assert not m._windows_only("fl_vocal_comp") and m._windows_only("fl_render") == (os.name != "nt")
for e_ in ("flh_newsong", "flh_handbook", "flh_extras", "flh_access"):
    assert e_ in m.EXTENSIONS
print("sets ok")


# ============================================================================= bridge script (fake FL modules)
def fake_fl():
    S = {"param": {(1, -1, 2): 0.3123456789}, "routes": set(), "levels": {}, "armed": set(), "fpt": [], "pos": [0],
         "precount": [False], "looprec": [True], "pats": {1: "Pattern 1"}, "cur": [1]}

    def mod(name, **kw):
        mm = types.ModuleType(name); mm.__dict__.update(kw); sys.modules[name] = mm
    mod("device", getPortNumber=lambda: 3, dispatchReceiverCount=lambda: 0, dispatch=lambda *a: None,
        midiOutSysex=lambda b: None)
    mod("midi", SONGLENGTH_S=1, SONGLENGTH_ABSTICKS=2, FPT_Save=92, FPT_Metronome=110, FPT_Loop=15, FPT_Record=12,
        FPT_CountDown=115, FPT_LoopRecord=113, FPT_PunchIn=31, FPT_PunchOut=32, FFNEP_DontPromptName=2, FPN_Preset=6)
    mod("general", getRecPPQ=lambda: 96, saveUndo=lambda *a: None, getVersion=lambda: 36)

    def gt(cmd, v):
        S["fpt"].append((cmd, S["pos"][0]))
        if cmd == 115:
            S["precount"][0] = not S["precount"][0]
        if cmd == 113:
            S["looprec"][0] = not S["looprec"][0]
    mod("transport", isPlaying=lambda: False, getSongPos=lambda mode=-1: S["pos"][0],
        setSongPos=lambda p, mode=-1: S["pos"].__setitem__(0, p), globalTransport=gt, isRecording=lambda: False,
        getLoopMode=lambda: 1)
    mod("ui", isMetronomeEnabled=lambda: True, isPrecountEnabled=lambda: S["precount"][0],
        isLoopRecEnabled=lambda: S["looprec"][0], showWindow=lambda w: None, setFocused=lambda w: None)
    mod("mixer", trackCount=lambda: 8, getTrackName=lambda t: f"Insert {t}", getTrackVolume=lambda t, m_=0: 0.0,
        getTrackPan=lambda t: 0.0, isTrackMuted=lambda t: False, getTrackColor=lambda t: 0x222222,
        getCurrentTempo=lambda: 120000, setRouteTo=lambda a, b_, v, u=False: (S["routes"].add((a, b_)) if v else
                                                                              S["routes"].discard((a, b_))),
        getRouteSendActive=lambda a, b_: (a, b_) in S["routes"], afterRoutingChanged=lambda: None,
        setRouteToLevel=lambda a, b_, lv: S["levels"].__setitem__((a, b_), lv),
        getRouteToLevel=lambda a, b_: S["levels"].get((a, b_), 0.8),
        isTrackArmed=lambda t: t in S["armed"], armTrack=lambda t: S["armed"].symmetric_difference_update({t}),
        getTrackRecordingFileName=lambda t: f"rec{t}.wav" if t in S["armed"] else "",
        getTrackPeaks=lambda t, mode: [0.5, 0.25, 0.5][mode])
    mod("channels", channelCount=lambda: 2, getTargetFxTrack=lambda i, g=False: [2, 3][i],
        getChannelName=lambda i, g=False: f"Ch {i}", getChannelVolume=lambda i, m_=0: 0.78, getChannelPan=lambda i: 0.0,
        isChannelMuted=lambda i: False, getChannelColor=lambda i: 0x111111)
    mod("patterns", patternCount=lambda: len(S["pats"]), getPatternName=lambda p_: S["pats"].get(p_, ""),
        setPatternName=lambda p_, n: S["pats"].__setitem__(p_, n), getPatternColor=lambda p_: 0,
        patternNumber=lambda: S["cur"][0],
        findFirstNextEmptyPat=lambda f, x=-1, y=-1: (S["cur"].__setitem__(0, max(S["pats"]) + 1),
                                                     S["pats"].setdefault(S["cur"][0], "")))
    mod("playlist", getTrackName=lambda t: f"Track {t}", getTrackColor=lambda t: 0)
    mod("arrangement")

    def gpv(prm, i, sl=-1, g=False):
        return S["param"].get((i, sl, prm), 0.0)

    def spv(v, prm, i, sl=-1, g=False):
        S["param"][(i, sl, prm)] = v
    mod("plugins", isValid=lambda i, s=-1, g=False: s in (-1, 0), getPluginName=lambda i, s=-1, *a: "Fruity EQ",
        getParamCount=lambda i, s=-1, g=False: 3, getParamName=lambda p_, i, s=-1, g=False: f"Band {p_}",
        getParamValue=gpv, setParamValue=spv,
        getParamValueString=lambda p_, i, s=-1, g=False: "%.1f dB" % (gpv(p_, i, s) * 36 - 18))
    return S


S = fake_fl()
sys.path.insert(0, os.path.join(ROOT, "src", "fl_script"))
import device_flheadless as B  # noqa: E402

assert B.SCRIPT_VERSION == m._script_version_shipped() and B.cmd_ping()["version"] == B.SCRIPT_VERSION
pp = B.cmd_plugin_params(1, -1)
assert pp["params"][2]["value"] == 0.3123456789                     # full precision
pr = B.cmd_plugin_probe(1, 2, points=5)
assert len(pr["texts"]) == 5 and pr["texts"][0] == "-18.0 dB" and pr["texts"][-1] == "18.0 dB"
assert pr["restored"] == 0.3123456789 == S["param"][(1, -1, 2)]
ms = B.cmd_mixer_set(2, send_levels="4:0.5,5:0.8")
assert S["levels"] == {(2, 4): 0.5, (2, 5): 0.8} and {"to": 4, "level": 0.5} in ms["sends"], ms
assert m._send_levels_arg({4: 0.5}, {5: 0.0}) == "4:0.500000,5:0.800000"
sg = B.cmd_song("where")
assert sg["countdown"] is False and sg["loop_record"] is True
sg = B.cmd_song("countdown")
assert sg["countdown"] is True and S["fpt"][-1][0] == 115
sg = B.cmd_song("loop_record")
assert sg["loop_record"] is False and S["precount"][0] is True and S["looprec"][0] is False
mi = B.cmd_mixer_info("all")
assert [t["used"] for t in mi["tracks"]][:4] == [False, False, True, True] and mi["tracks"][0]["sends"] is None
ar = B.cmd_mixer_arm(3, True)
assert ar == {"track": 3, "armed": True, "recording_file": "rec3.wav"}
assert B.cmd_mixer_arm(3, True)["armed"] and 3 in S["armed"]                  # no double toggle
assert B.cmd_mixer_peaks("0,3") == {"peaks": {0: [0.5, 0.25], 3: [0.5, 0.25]}}
S["fpt"].clear(); S["pos"][0] = 7
rs = B.cmd_rec_setup(punch_in_bar=9, punch_out_bar=17, loop_record=False, bpb=4)
assert S["fpt"] == [(31, 8 * 4 * 96), (32, 16 * 4 * 96)] and S["pos"][0] == 7, S["fpt"]   # position put back
assert rs == {"punch": [9.0, 17.0], "loop_record": False} and S["fpt"] == [(31, 3072), (32, 6144)]
pn = B.cmd_pattern_new("Idea 2")
assert pn["pattern"] == 2 and S["pats"][2] == "Idea 2" and pn["pattern_count"] == 2
st_ = B.cmd_state()
assert st_["pattern_count"] == 2 and not st_["patterns_capped"]
assert B._run("mixer_peaks;tracks=s:" + __import__("base64").b64encode(b"0").decode()).startswith("OK")
for c in ("plugin_probe", "mixer_info", "mixer_arm", "mixer_peaks", "rec_setup", "pattern_new"):
    assert c in B.COMMANDS
assert all(ord(ch) < 128 for ch in open(os.path.join(ROOT, "src", "fl_script", "device_flheadless.py"), encoding="utf-8").read())
note = m._script_note({"bridge": "fl-headless", "version": 2})
assert "script_outdated" in note and "script_outdated" not in m._script_note({"version": B.SCRIPT_VERSION})
print("bridge script ok")
shutil.rmtree(TMP, ignore_errors=True)
print("ok")
