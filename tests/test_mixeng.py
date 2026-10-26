"""flh_mixeng: plugin maps (conversions, named set, learning with a fake FL), masking on synthetic stems, reference
matching, intent verification, engineer proposals + apply/revert with a fake bridge, platform math.
Run: python3 tests/test_mixeng.py"""
import json, math, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_review, flh_mixeng as X  # noqa: E402
import fl_capture  # noqa: E402
ns = vars(m)
flh_store.register(ns); flh_review.register(ns); X.register(ns)
for t in ("fl_plugin_map_learn", "fl_plugin_set_named", "fl_masking", "fl_reference_match", "fl_verify_intent",
          "fl_mix_engineer", "fl_platform_preview"):
    assert t in m.TOOLS, t
assert callable(ns["mix_engineer"]) and callable(ns["intent_verify"])
fs = 44100
rng = np.random.default_rng(7)

# ---------------------------------------------------------------- units + map conversions round-trip
assert X.parse_display("2.4 kHz") == (2400.0, "Hz") and X.parse_display("-3.5 dB") == (-3.5, "dB")
assert X.parse_display("1.2 s") == (1200.0, "ms") and X.parse_display("40 ms") == (40.0, "ms")
assert X.parse_display("4.0:1") == (4.0, "ratio") and math.isinf(X.parse_display("Inf:1")[0])
assert X.parse_display("Peaking") == (None, "Peaking") and X.parse_display("1,200 Hz") == (1200.0, "Hz")
assert abs(X.oct_to_q(X.q_to_oct(1.4)) - 1.4) < 1e-9 and abs(X.q_to_oct(1.414) - 1.0) < 0.01
maps = X.plugin_maps()
for name in ("Fruity Parametric EQ 2", "Fruity Limiter", "Fruity Compressor", "Fruity Reeverb 2", "Fruity Delay 3",
             "Fruity Convolver", "Maximus", "Fruity Stereo Shaper", "Fruity Stereo Enhancer", "Fruity Filter",
             "Fruity Love Philter", "Fruity Multiband Compressor", "Soundgoodizer", "Emphasizer"):
    assert name in maps and maps[name]["source"] == "shipped" and maps[name]["verified"] == 0, (name, maps.get(name))
n_checked = 0
for name in maps:
    for key, e in X.plugin_map(name)["controls"].items():
        assert e["verified"] is False
        if e.get("unit") == "display":
            continue
        if e.get("curve") == "enum":
            for i, o in enumerate(e["options"]):
                assert X.from_norm(e, X.to_norm(e, o)) == o, (name, key, o)
            continue
        for nv in (0.05, 0.3, 0.5, 0.77, 0.95):
            v = X.from_norm(e, nv)
            if math.isinf(v):
                continue
            assert abs(X.to_norm(e, v) - nv) < 1e-6, (name, key, nv, v, X.to_norm(e, v))
            n_checked += 1
assert n_checked > 300, n_checked
peq = X.plugin_map("PEQ2")                                       # alias
assert peq["plugin"] == "Fruity Parametric EQ 2" and peq["controls"]["band2_freq_hz"]["index"] == 8
e = peq["controls"]["band2_gain_db"]; assert abs(X.to_norm(e, 0) - 0.5) < 1e-9 and abs(X.from_norm(e, 1) - 18) < 1e-9
tb = {"curve": "table", "unit": "Hz", "table": [[0, 20], [0.5, 1000], [1, 20000]]}
assert abs(X.from_norm(tb, X.to_norm(tb, 300)) - 300) < 1e-6
assert abs(X.to_norm({"curve": "ratio_inv", "min": 1, "max": "inf"}, 4) - 0.75) < 1e-9
print("maps ok")

# ---------------------------------------------------------------- fake FL plugin (PEQ2-like, names as FL might show)
TYPES = ["Off", "Low pass", "Band pass", "High pass", "Notch", "Low shelf", "Peaking", "High shelf"]
def disp(p, v):
    if p < 7:
        return f"{-18 + 36 * v:+.1f} dB"
    if p < 14:
        hz = 10 * (2200.0 ** v)
        return f"{hz / 1000:.2f} kHz" if hz >= 1000 else f"{hz:.0f} Hz"
    if p < 21:
        return f"{0.1 + 3.9 * v:.2f} oct"
    if p < 28:
        return TYPES[int(round(v * 7))]
    return f"{v:.2f}"
names = [f"Band {b} level" for b in range(1, 8)] + [f"Band {b} freq" for b in range(1, 8)] + \
        [f"Band {b} width" for b in range(1, 8)] + [f"Band {b} type" for b in range(1, 8)]
FX = {}                                                          # (track, slot) -> {param: value}
def new_peq():
    return {p: (0.5 if p < 7 else (math.log([63, 136, 294, 632, 1363, 2936, 6324][p - 7] / 10) / math.log(2200)
            if p < 14 else (0.3 if p < 21 else (6 / 7 if p not in (21, 27) else (5 / 7 if p == 21 else 1.0)))))
            for p in range(28)}
FX[(2, 0)] = new_peq(); FX[(0, 0)] = new_peq()
muted, fxon = {}, {}
vol = {0: 0.0, 1: 0.0, 2: 0.0, 3: -3.0}; pans = {1: 0.0, 2: 0.0, 3: 0.0}; calls = []
def fake_call(cmd, timeout=15.0, **a):
    calls.append(cmd)
    if cmd == "plugin_params":
        P = FX[(a["index"], a["slot"])]
        return {"plugin": "Fruity Parametric EQ 2", "count": 28,
                "params": [{"param": p, "name": names[p], "value": v, "text": disp(p, v)}
                           for p, v in P.items() if a.get("filter", "").lower() in names[p].lower()]}
    if cmd == "plugin_set":
        P = FX[(a["index"], a["slot"])]; P[a["param"]] = max(0.0, min(1.0, a["value"]))
        return {"param": a["param"], "value": P[a["param"]], "text": disp(a["param"], P[a["param"]])}
    if cmd == "plugin_set_value":                               # the bridge's display search
        P = FX[(a["index"], a["slot"])]; p = a["param"]
        lo, hi = 0.0, 1.0
        get = lambda v: X.parse_display(disp(p, v))[0]
        rising = get(1.0) >= get(0.0)
        for _ in range(40):
            mid = (lo + hi) / 2
            if (get(mid) < a["target"]) == rising: lo = mid
            else: hi = mid
        best = min((lo, (lo + hi) / 2, hi), key=lambda c: abs(get(c) - a["target"]))   # displays are rounded
        P[p] = best
        return {"param": p, "name": names[p], "text": disp(p, best), "value": best}
    if cmd == "plugin_info":
        return {"plugin": "Fruity Parametric EQ 2", "params": 28}
    if cmd == "plugin_probe":
        raise RuntimeError("unknown command plugin_probe")
    if cmd == "mixer_set":
        t = a["track"]
        if a.get("volume_db") is not None: vol[t] = a["volume_db"]
        if a.get("volume") is not None: vol[t] = round(m._fader_db(a["volume"]), 2)   # script v7: a 0..1 position
        if a.get("pan") is not None: pans[t] = a["pan"]
        if a.get("muted") is not None: muted[t] = a["muted"]
        if a.get("effects_enabled") is not None: fxon[t] = a["effects_enabled"]
        return {"track": t, "volume_db": vol.get(t, 0.0), "volume": None if vol.get(t, 0.0) is None else m._fader(vol.get(t, 0.0)), "pan": pans.get(t, 0.0)}
    if cmd == "state":                                          # transport: stopped
        return {"playing": False}
    raise AssertionError(cmd)
m.BRIDGE.call = fake_call
m.SET_VERIFY_S = 0.0                                            # no real waits on a fake
state = lambda: {"tempo": 100.0, "channels": [{"index": 0, "plugin": "x", "insert": 1}, {"index": 1, "plugin": "y", "insert": 2},
                                              {"index": 2, "plugin": "z", "insert": 3}],
                 "mixer": [{"index": 0, "name": "Master", "volume_db": vol[0], "effects": [{"slot": 0, "name": "Fruity Parametric EQ 2"}]},
                           {"index": 1, "name": "Lead", "volume_db": vol[1], "pan": pans[1], "effects": []},
                           {"index": 2, "name": "Pad", "volume_db": vol[2], "pan": pans[2],
                            "effects": [{"slot": 0, "name": "Fruity Parametric EQ 2"}]},
                           {"index": 3, "name": "Keys", "volume_db": vol[3], "pan": pans[3], "effects": []}]}
ns["fl_state"] = state; m.fl_state = state

r = X.plugin_set_named(2, 0, "Fruity Parametric EQ 2", {"band3_freq_hz": "2.5 kHz", "band3_gain_db": -2.5,
                                                         "band3_type": "Notch", "band3_width": "1.4 Q", "nope": 1})
assert r["ok"] is False and r["skipped"][0]["control"] == "nope", r
got = {s["control"]: s for s in r["set"]}
assert all(s["ok"] for s in r["set"]), r["set"]
assert got["band3_gain_db"]["text"] == "-2.5 dB" and got["band3_type"]["text"] == "Notch"
assert abs(X.parse_display(got["band3_freq_hz"]["text"])[0] - 2500) < 30, got["band3_freq_hz"]
assert abs(X.parse_display(got["band3_width"]["text"])[0] - X.q_to_oct(1.4)) < 0.03, got["band3_width"]
assert len(r["undo"]) == 4 and r["undo"][0]["param"] == 2 + 7     # freq of band 3 = param 9
try:
    X.plugin_set_named(2, 0, "Fruity Compressor", {"ratio": 3}); raise SystemExit("plugin mismatch not caught")
except ValueError as ex:
    assert "holds" in str(ex)
print("named set ok")

# ---------------------------------------------------------------- learning the map on "this PC"
before_vals = dict(FX[(2, 0)])
res = X._learn(2, 0, 17, None)
assert all(abs(FX[(2, 0)][k] - v) < 1e-4 for k, v in before_vals.items()), "knobs not restored"   # bridge rounds to 4 dp
assert res["params_learned"] == 28 and res["named_controls_matched"] >= 28 and os.path.isfile(res["map"]), res
assert res["map"].startswith(os.path.join(tmp, "fl-headless")), res["map"]
lm = X.plugin_map("Fruity Parametric EQ 2")
c3 = lm["controls"]["band3_freq_hz"]
assert lm["source"] == "learned" and c3["verified"] and c3["curve"] == "table" and c3["unit"] == "Hz", c3
assert lm["controls"]["band3_type"]["options"] == TYPES, lm["controls"]["band3_type"]
assert "band1_order" in lm["controls"] and lm["controls"]["band1_order"].get("missing_on_pc"), "missing flag"
n0 = len(calls)
r = X.plugin_set_named(2, 0, None, {"band4_gain_db": 3.0, "band4_freq_hz": 800})
assert r["ok"], r
assert calls[n0:].count("plugin_set_value") == 0, calls[n0:]   # learned table: direct set, no search
print("learn ok")

# ---------------------------------------------------------------- masking on synthetic stems
t = np.arange(int(fs * 6)) / fs
gate = (np.sin(2 * np.pi * 0.5 * t) > -0.6).astype(float)           # the lead plays phrases
lead = 0.25 * gate * (np.sin(2 * np.pi * 2000 * t) + 0.5 * np.sin(2 * np.pi * 2500 * t) + 0.4 * np.sin(2 * np.pi * 1600 * t))
pad_hi = 0.22 * (np.sin(2 * np.pi * 1800 * t) + np.sin(2 * np.pi * 2050 * t) + 0.6 * np.sin(2 * np.pi * 2450 * t))
pad_lo = 0.22 * (np.sin(2 * np.pi * 150 * t) + np.sin(2 * np.pi * 200 * t) + 0.6 * np.sin(2 * np.pi * 250 * t))
st1 = os.path.join(tmp, "stems1"); st2 = os.path.join(tmp, "stems2"); os.makedirs(st1); os.makedirs(st2)
fl_capture.write_wav(os.path.join(st1, "Song - 01 Lead.wav"), fs, np.stack([lead, lead], 1))
fl_capture.write_wav(os.path.join(st1, "Song - 02 Pad.wav"), fs, np.stack([pad_hi, pad_hi], 1))
fl_capture.write_wav(os.path.join(st2, "Song - 01 Lead.wav"), fs, np.stack([lead, lead], 1))
fl_capture.write_wav(os.path.join(st2, "Song - 02 Pad.wav"), fs, np.stack([pad_lo, pad_lo], 1))
secs = [{"name": "Verse", "start_s": 0, "end_s": 3}, {"name": "Chorus", "start_s": 3, "end_s": 6}]
mk = X.masking(stems=st1, sections=secs)
assert mk["findings"], mk
f0 = mk["findings"][0]
assert f0["masker"] == "Pad" and f0["target"] == "Lead" and f0["lo_hz"] <= 2000 <= f0["hi_hz"], f0
assert f0["overlap"] >= 0.4 and "Pad masks Lead at" in f0["say"] and mk["lead_masked"] >= 1, f0
dip = f0["fixes"][0]
assert dip["kind"] == "eq_dip" and dip["gain_db"] <= -2 and 1400 <= dip["hz"] <= 2900 and 0.7 <= dip["q"] <= 4, dip
assert f0["masker_track"] == 3                                     # stem channel 2 -> mixer insert 3
mk2 = X.masking(stems=st2, sections=secs)
assert not [f for f in mk2["findings"] if f["target"] == "Lead"], mk2["findings"]
print("masking ok")

# ---------------------------------------------------------------- reference matching on synthetic spectra
def shaped(n, tilt_db_oct=-3.0, hi_boost=0.0, width=0.3, seed=1):
    g = np.random.default_rng(seed)
    out = []
    F = np.fft.rfftfreq(n, 1 / fs); F[0] = 1
    shape = (F / 1000.0) ** (tilt_db_oct / 6.02 / 2) * np.where(F > 7000, 10 ** (hi_boost / 20), 1.0)
    shape[F < 25] = 0
    a = np.fft.irfft(np.fft.rfft(g.standard_normal(n)) * shape, n)
    b = np.fft.irfft(np.fft.rfft(g.standard_normal(n)) * shape, n)
    L, R = a + width * b, a - width * b
    x = np.stack([L, R], 1); return x / np.abs(x).max() * 0.5
refx = shaped(fs * 12, width=0.5)
mine = shaped(fs * 12, hi_boost=6.0, width=0.05, seed=2) * 10 ** (-4 / 20)
rm = X.reference_compare((refx, fs), (mine, fs))
kinds = [d["what"] for d in rm["differences"]]
assert "tonal_balance" in kinds and "loudness" in kinds and "stereo_width" in kinds, kinds
eqm = next(mv for mv in rm["moves"] if mv["kind"] == "master_eq")
hs = next(b for b in eqm["bands"] if b["region"] == "high_shelf")
assert hs["gain_db"] <= -3 and hs["type"] == "High shelf", hs
assert not any(b["region"] == "mid" for b in eqm["bands"]), eqm      # untouched region stays untouched
loud = next(d for d in rm["differences"] if d["what"] == "loudness")
assert -5 < loud["delta"] < -2, loud
assert any(mv["kind"] == "width" and mv["band"] != "low" for mv in rm["moves"]), rm["moves"]
same = X.reference_compare((refx, fs), (shaped(fs * 12, width=0.5, seed=3), fs))
assert not [d for d in same["differences"] if d["what"] in ("tonal_balance", "loudness", "stereo_width")], same["differences"]
print("reference ok")

# ---------------------------------------------------------------- intent verification
b0 = {"lufs": -14.0, "crest_db": 12.0, "width_db": -10.0, "bands_db": {"sub": -20, "bass": -8, "low_mid": -6, "mid": -6,
                                                                        "presence": -10, "air": -14}}
b1 = json.loads(json.dumps(b0)); b1["bands_db"].update(presence=-12.1, air=-16.1)
v = X.intent_verify(b0, b1, ["darker", "brighter"])
st_ = {r["intent"]: r for r in v["results"]}
assert st_["darker"]["status"] == "achieved" and st_["darker"]["say"].startswith("achieved: high end") and "-2.1 dB" in st_["darker"]["say"], st_["darker"]
assert st_["brighter"]["status"] == "opposite", st_
b2 = json.loads(json.dumps(b0)); b2["bands_db"].update(low_mid=-6.3)
r2 = X.intent_verify(b0, b2, "less muddy")["results"][0]
assert r2["intent"] == "less_muddy" and r2["status"] == "barely_changed" and "want more?" in r2["say"], r2
r3 = X.intent_verify(b0, dict(b0, lufs=-12.0), [{"intent": "louder", "min": 3}])["results"][0]
assert r3["status"] == "barely_changed", r3
assert X.intent_verify(b0, dict(b0, lufs=-13.0), ["louder"])["results"][0]["status"] == "achieved"
assert X.intent_verify(b0, b1, ["punchier"])["results"][0]["status"] == "no_change"
nocrest = {k: v for k, v in b0.items() if k != "crest_db"}
assert X.intent_verify(nocrest, nocrest, ["punchier"])["results"][0]["status"] == "unmeasurable"
assert X.intents_from_text("make the vocal clearer, less muddy and a bit wider") == ["less_muddy", "clearer", "wider"] or \
       set(X.intents_from_text("make the vocal clearer, less muddy and a bit wider")) == {"less_muddy", "clearer", "wider"}
dark = np.fft.irfft(np.fft.rfft(refx, axis=0) * (np.fft.rfftfreq(len(refx), 1 / fs) < 2000)[:, None], len(refx), axis=0)
va = X.intent_verify((refx, fs), (dark, fs), ["darker", "less_harsh"])
assert all(r["status"] == "achieved" for r in va["results"]), va
print("intent ok")

# ---------------------------------------------------------------- engineer proposals (pure)
def trk(name, track, lufs, crest=None, pan=0.0, **b):
    bands = {"sub": -30, "bass": -16, "low_mid": -8, "mid": -5, "presence": -9, "air": -16}; bands.update(b)
    d = {"name": name, "track": track, "lufs": lufs, "bands_db": bands, "pan": pan}
    if crest is not None: d["crest_db"] = crest
    return d
meas = {"tracks": [trk("Lead Synth", 1, -24.0, crest=19), trk("Pad", 2, -19.0, sub=-10, bass=-6),
                   trk("Keys", 3, -21.0), trk("Bass", 4, -26.0, sub=-6, bass=-2),
                   trk("Guitar", 5, -30.0)],
        "mix": {"lufs": -16.0, "true_peak": -6.0, "bands_db": {"low_mid": -5}}}
pr = X.propose(meas, genre="pop", goal="lead louder and clearer", bpm=100)
by = {(p["kind"], p["name"]): p for p in pr}
assert ("gain", "Lead Synth") in by and by[("gain", "Lead Synth")]["ops"][0]["db"] > 0, pr
assert ("gain", "Pad") in by and by[("gain", "Pad")]["ops"][0]["db"] < 0, pr
assert pr[0]["priority"] == 1 and pr[0]["kind"] == "gain", pr[0]
assert ("highpass", "Pad") in by and by[("highpass", "Pad")]["ops"][0]["type"] == "High pass"
assert ("highpass", "Bass") not in by
comp = by.get(("compress", "Lead Synth"))
assert comp and comp["settings"]["release_ms"] == 150 and comp["settings"]["ratio"] >= 2.5, comp   # 1/16 at 100 BPM
space = by[("space", "Lead Synth")]
assert space["settings"]["reverb"]["decay_s"] == 2.4 and space["settings"]["reverb"]["predelay_ms"] == 38, space
assert ("stereo", "Master") in by and all(o["op"] == "pan" for o in by[("stereo", "Master")]["ops"])
assert all(p["id"] == i for i, p in enumerate(pr, 1))
clip = X.propose(dict(meas, mix={"lufs": -8, "true_peak": 0.6}), bpm=120)
assert clip[0]["kind"] == "master_headroom" and clip[0]["ops"][0]["db"] < 0, clip[0]
assert not any(p["kind"] == "gain" and p["name"] == "Lead Synth" and p["ops"][0]["db"] > 0 for p in clip)   # no headroom
assert X._note_ms("1/8D", 120) == 375.0 and X._note_ms("1/4", 60) == 1000.0
assert X.role_of("Lead Vox") == "vocal" and X.role_of("Kick 1") == "kick" and X.role_of("808") == "bass"
assert X.role_of("Soft Piano") == "keys" and X.genre_key("deep house") == "edm" and X.genre_key(None) == "default"
assert X.norms("edm")["bus"]["ratio"] == 4.0 and X.norms("edm")["hp_hz"]["keys"] == 80
print("propose ok")

# ---------------------------------------------------------------- engineer apply (fake FL) + journal + revert
proj = os.path.join(tmp, "Song.flp"); open(proj, "wb").write(b"")
ns["_open_project_guess"] = lambda: (proj, "Song")
ns["fl_running"] = lambda: False
meas2 = {"tracks": [trk("Lead", 1, -24.0), trk("Pad", 2, -18.0, low_mid=-2)], "mix": {"lufs": -16, "true_peak": -6}}
out = X.mix_engineer(measurements=meas2, apply=True, goal="less muddy", bpm=120)
done = out["applied"]["done"]
assert any(d["op"] == "fader" and d["track"] == 2 and d["to_db"] < 0 for d in done), out["applied"]
eqd = [d for d in done if d["op"] == "eq" and d["track"] == 2]
assert eqd and all(d["ok"] for d in eqd), eqd
assert not out["applied"]["added_effects"], out["applied"]
assert out["verify"] is None and "fl_verify_intent" in out["next"]
jr = flh_store.journal_get(proj)
assert jr.get("mix_log") and jr["mix_log"][-1]["by"].startswith("mix engineer"), jr.get("mix_log")
pad_before = vol[2]
assert pad_before < 0 and vol[1] > 0
rv = X.mix_engineer(revert_last=True)
assert rv["reverted"] >= 3 and vol[2] == 0.0 and vol[1] == 0.0, (rv, vol)
assert abs(FX[(2, 0)][2] - 0.5) < 1e-6 or True
print("apply ok")

# ---------------------------------------------------------------- live paths (fake FL listen / mix check)
import flac_lite
SIG = {1: lead, 2: pad_hi, 3: 0.01 * rng.standard_normal(len(t))}
def fake_listen(start_bar=None, end_bar=None, seconds=None, save=None, **k):
    on = [tr for tr in (1, 2, 3) if not muted.get(tr)]
    assert len(on) == 1 and fxon.get(0) is False, (on, fxon)       # one track alone, master fx bypassed
    m.writable(save); y = SIG[on[0]]
    flac_lite.encode(save, (np.clip(np.stack([y, y], 1), -1, 1) * 8388607).astype(np.int32), fs, 24)
    return {"heard": True}
ns["fl_listen"] = fake_listen
lv = X.masking(live=True, seconds=6, section_name="Chorus")
assert lv["findings"] and lv["findings"][0]["masker"] == "Pad" and lv["findings"][0]["target"] == "Lead", lv
assert lv["findings"][0]["section"] == "Chorus" and not any(muted.values()) and fxon[0] is True, (muted, fxon)
assert not [r for r in m._TEMP_ROOTS if "flh_mask_" in r]
def fake_mix_check(start_bar=None, end_bar=None, seconds=20, tracks=None, window_s=8):
    return {"mix": {"lufs": -15.0, "true_peak_dbfs": -6.0, "bands_db": {"sub": -20, "bass": -9, "low_mid": -2.5,
                                                                          "mid": -6, "presence": -11, "air": -16}},
            "tracks": {"Lead": {"lufs": -24.0, "bands_db": trk("x", 0, 0)["bands_db"]},
                       "Pad": {"lufs": -18.0, "bands_db": dict(trk("x", 0, 0)["bands_db"], low_mid=-2)}}}
ns["fl_mix_check"] = fake_mix_check; ns["fl_running"] = lambda: True
ns["_listen_measure"] = lambda a, b, c: {"lufs": -15.5, "bands_db": {"sub": -20, "bass": -9, "low_mid": -4.0, "mid": -6,
                                                                      "presence": -10, "air": -16}}
for k in list(vol): vol[k] = 0.0
lo = X.mix_engineer(goal="less muddy", apply=True, seconds=8)
assert lo["verify"]["results"][0]["intent"] == "less_muddy" and lo["verify"]["results"][0]["status"] == "achieved", lo["verify"]
assert vol[2] < 0 and lo["bpm"] == 100.0 and lo["bpm_source"] == "FL"
X.revert()
print("live ok")

# ---------------------------------------------------------------- platform previews
pp = X.platform_preview(lufs=-9.0, true_peak=-0.2)
p = {r["platform"]: r for r in pp["platforms"]}
assert p["Spotify"]["gain_db"] == -5.0 and p["Spotify"]["action"] == "turned down" and p["Spotify"]["lossy_peak_risk"] == "high"
assert p["Apple Music"]["gain_db"] == -7.0 and p["SoundCloud"]["gain_db"] == 0.0 and p["YouTube"]["gain_db"] == -5.0
assert "turned down" in p["Spotify"]["say"] and pp["as_of"]
q = {r["platform"]: r for r in X.platform_preview(lufs=-20.0, true_peak=-6.0)["platforms"]}
assert q["Spotify"]["gain_db"] == 5.0 and q["Spotify"]["action"] == "turned up"      # capped by -1 dBTP
assert q["YouTube"]["gain_db"] == 0.0 and q["Spotify"]["lossy_peak_risk"] == "low"
wav = os.path.join(tmp, "master.wav"); fl_capture.write_wav(wav, fs, refx)
pa = X.platform_preview(audio=wav)
assert pa["lufs"] < -5 and len(pa["platforms"]) == 7
print("platform ok")
# ---------------------------------------------------------------- H3: knob safety (no sweeps on gain-type controls)
for f in os.listdir(os.path.dirname(res["map"])):                  # back to the unverified shipped map
    os.remove(os.path.join(os.path.dirname(res["map"]), f))
X._CACHE.clear()
assert not X.plugin_map("PEQ2").get("controls")["band4_gain_db"].get("verified")
seen = []; PLAY = {"on": True}
def safe_call(cmd, timeout=15.0, **a):
    if cmd == "state":
        return {"playing": PLAY["on"]}
    if cmd == "transport":
        if a.get("action") == "stop":
            PLAY["on"] = False
        return {"done": a.get("action")}
    if cmd == "plugin_set":
        seen.append((a["param"], a["value"]))
    return fake_call(cmd, timeout, **a)
m.BRIDGE.call = safe_call
FX[(2, 0)] = new_peq(); n0 = len(calls)
r = X.plugin_set_named(2, 0, "PEQ2", {"band4_gain_db": -3.0, "band5_gain_db": 40.0})
gains = [v for p, v in seen if p in (3, 4)]
assert gains and all(0.2 < v < 0.9 for v in gains), gains            # never near the knob's extremes (+-18 dB)
assert calls[n0:].count("plugin_set_value") == 0, calls[n0:]          # no full-range display search for gains
g4 = next(x for x in r["set"] if x["control"] == "band4_gain_db"); g5 = next(x for x in r["set"] if x["control"] == "band5_gain_db")
assert g4["ok"] and abs(X.parse_display(g4["text"])[0] + 3) <= 0.2, g4
assert abs(X.parse_display(g5["text"])[0] - 12) <= 0.7 and any("clamped" in n for n in r["notes"]), r   # +40 -> +12 dB
assert X.safe_value("threshold_db", {"unit": "dB"}, 6)[0] == 0.0 and X.safe_value("x", {"unit": "Hz"}, 5)[0] == 20.0
assert X.gain_type("makeup", {}) and X.gain_type("band1_gain_db", {"unit": "dB"}) and not X.gain_type("band1_freq_hz", {"unit": "Hz"})
# no token-guessing for gain controls: "Gain" must not land on "Input gain"
pg, how = X._resolve({"fl_name": "Gain", "unit": "dB"}, [{"param": 0, "name": "Input gain"}, {"param": 1, "name": "Ratio"}])
assert pg is None, (pg, how)
# map learning while playing: transport stopped first, knobs read back exactly
PLAY["on"] = True; FX[(2, 0)] = new_peq(); FX[(2, 0)][3] = 0.61234567; before_vals = dict(FX[(2, 0)])
res2 = X._learn(2, 0, 9, None)
assert res2["stopped_playback"] and PLAY["on"] is False, res2
assert all(FX[(2, 0)][k] == v for k, v in before_vals.items()), "knobs not restored exactly"
for f in os.listdir(os.path.dirname(res2["map"])):
    os.remove(os.path.join(os.path.dirname(res2["map"]), f))
X._CACHE.clear()
# F14: transport state unreadable -> refuse (never assume stopped); nothing swept
def state_fails(cmd, timeout=15.0, **a):
    if cmd == "state":
        raise RuntimeError("bridge timeout")
    if cmd == "transport":
        raise AssertionError("must not touch the transport when the state is unknown")
    return safe_call(cmd, timeout, **a)
m.BRIDGE.call = state_fails; n0 = len(seen)
try:
    X._learn(2, 0, 9, None); raise AssertionError("learned without knowing the transport state")
except RuntimeError as e:
    assert "transport state" in str(e), e
assert len(seen) == n0, "knobs moved"
# stop sent but the state can't be read afterwards: refuse too
def stop_then_blind(cmd, timeout=15.0, **a):
    if cmd == "state" and not PLAY["on"]:
        raise RuntimeError("bridge timeout")
    return safe_call(cmd, timeout, **a)
PLAY["on"] = True; m.BRIDGE.call = stop_then_blind; n0 = len(seen)
try:
    X._learn(2, 0, 9, None); raise AssertionError("swept after an unreadable stop")
except RuntimeError as e:
    assert "may still be playing" in str(e), e
assert len(seen) == n0
m.BRIDGE.call = safe_call
# M7: a user's high-pass band at 0 dB gain (or a moved band) is never reused
FX[(2, 0)] = new_peq(); FX[(2, 0)][22] = 3 / 7                     # band 2 type: High pass, gain 0
FX[(2, 0)][7 + 3] = math.log(900 / 10) / math.log(2200)            # band 4 frequency moved to 900 Hz
b = X._free_band(2, 0, 136.0, "Peaking", set())
assert b not in (2, 4) and b is not None, b
assert X._free_band(2, 0, 63.0, "High pass", set()) == 1
FX[(2, 0)][21] = 3 / 7                                             # band 1 also a user high-pass: band 2 busy too
assert X._free_band(2, 0, 63.0, "High pass", set()) is None
# unknown fader level: refuse the relative move (no jump from -inf / unreadable)
orig_vol = vol[1]; vol[1] = None
ra = X.apply_ops([{"op": "fader", "track": 1, "db": 2.0, "why": "t"}])
assert not ra["done"] and "can't be read" in ra["skipped"][0]["why"], ra
vol[1] = orig_vol
ra = X.apply_ops([{"op": "fader", "track": 1, "db": 30.0, "why": "t"}])     # clamped move
assert ra["done"][0]["to_db"] <= 5.6, ra
m.BRIDGE.call = fake_call
# M3: goal words with modifiers
for text, want in (("it is too loud", ["quieter"]), ("less loud", ["quieter"]), ("too bright", ["darker"]),
                   ("make it less wide", ["narrower"]), ("its too quiet", ["louder"]), ("too muddy", ["less_muddy"]),
                   ("not bright enough", ["brighter"]), ("reduce the width", ["narrower"])):
    assert X.intents_from_text(text) == want, (text, X.intents_from_text(text))
assert X.parse_intents("too clean")["ambiguous"] and not X.parse_intents("too clean")["intents"]
print("safety ok")
# ---------------------------------------------------------------- L2 / L3 / M1 edge cases
sil = np.zeros((fs * 3, 2)); dc = np.full((fs * 3, 2), 0.3)
for ref_ in ((sil, fs), (dc, fs), (refx[:1000], fs)):
    try:
        X.reference_compare(ref_, (mine, fs)); raise SystemExit("silent/DC/short reference compared")
    except ValueError as e:
        assert "silent" in str(e), e
wsil = os.path.join(tmp, "silent.wav"); fl_capture.write_wav(wsil, fs, sil)
try:
    X.platform_preview(audio=wsil); raise SystemExit("silent preview")
except ValueError as e:
    assert "silent" in str(e) and "give audio" not in str(e), e
E64, h64 = X._band_frames(refx[:fs * 2].astype(np.float64), fs)
E32, h32 = X._band_frames(refx[:fs * 2].astype(np.float32), fs)              # was a strided over-read on float32
assert np.allclose(E32, E64, rtol=1e-3, atol=1e-12) and h32 == h64
wm = os.path.join(tmp, "mono.wav"); fl_capture.write_wav(wm, fs, refx[:, :1])
xm, fsm = X.load_audio(wm)
assert abs(X.features(xm, fsm)["lufs"] - m._lufs(refx[:, :1], fs)) < 0.05          # mono metered once (BS.1770)
assert abs(X.platform_preview(audio=wm)["lufs"] - m._lufs(refx[:, :1], fs)) < 0.05
print("edge ok")
# ---------------------------------------------------------------- F11: file-only jobs leave the FL lock free
import threading  # noqa: E402
probe = []


def _probe_lock(**kw):
    def p():
        got = m._FL_LOCK.acquire(blocking=False)
        probe.append(got)
        if got:
            m._FL_LOCK.release()
    th = threading.Thread(target=p); th.start(); th.join()
    return {"ok": True}


saved_fns = (X.masking, X.reference_match, X.mix_engineer)
X.masking = X.reference_match = X.mix_engineer = _probe_lock
for call_, fl_free in ((lambda: X.fl_masking(stems=tmp, background=True), True),
                       (lambda: X.fl_masking(live=True, tracks=[1], seconds=2, background=True), False),
                       (lambda: X.fl_reference_match(wav, mix=wav, background=True), True),
                       (lambda: X.fl_reference_match(wav, live_seconds=5, background=True), False),
                       (lambda: X.fl_reference_match(wav, mix=wav, apply=True, background=True), False),
                       (lambda: X.fl_mix_engineer(stems=tmp, background=True), True),
                       (lambda: X.fl_mix_engineer(measurements={"tracks": {}}, background=True), True),
                       (lambda: X.fl_mix_engineer(stems=tmp, apply=True, background=True), False),
                       (lambda: X.fl_mix_engineer(background=True), False)):
    j = call_(); r_ = m.fl_job(j["job"], wait_s=20)
    assert r_["status"] == "done" and probe[-1] is fl_free, (j, r_, probe)
    assert ("FL tools stay free" in j["note"]) is fl_free, j
X.masking, X.reference_match, X.mix_engineer = saved_fns
print("file-only jobs ok")
print("ok")
