"""fl_sound_shape: words -> plan (aliases, amounts, 'insane' boost, edition fallback, EQ merge), instrument controls
found by name (volume/pan never touched), the effect chain on the channel's mixer track, before/after measured with
the level matched on the fader, undo puts knobs and faders back and removes the effects it added; a channel on the
Master gets no effects. FL is simulated. Run: python3 tests/test_sound_shape.py"""
import os, sys, tempfile, wave
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local"); os.environ["FLH_NO_WARM"] = "1"
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
for mod in ("flh_store", "flh_catalog", "flh_mixeng", "flh_sounddesign"):
    __import__(mod).register(ns)
import flh_sounddesign as SD, flh_mixeng as M  # noqa: E402

# ---- plan
p = SD.plan("make the piano warmer and BIG")
assert p["words"] == ["warm", "huge"] and p["amount"] == "medium", p
assert p["inst"]["cutoff"] < 0 and p["inst"]["detune"] > 0, p["inst"]
assert any(f["plugin"] == "Fruity Reeverb 2" for f in p["fx"]) and "warmer" in p["intents"], p
p2 = SD.plan("insane", amount="heavy")
assert p2["amount"] == "insane", p2["amount"]                           # 'insane' pushes the amount one step
sub, ins = SD.plan("dreamy", "subtle"), SD.plan("dreamy", "insane")
w = lambda pl: next(f["controls"]["wet_pct"] for f in pl["fx"] if f["plugin"] == "Fruity Reeverb 2")
assert w(sub) < w(ins) <= 100, (w(sub), w(ins))
try:
    SD.plan("asdf qwer"); raise SystemExit("nonsense accepted")
except ValueError:
    pass
m.flh_edition = None
ns["plugin_ownership"] = lambda name: {"status": "trial" if name == "Soundgoodizer" else True}
pg = SD.plan("gritty")
assert pg["fx"][0]["plugin"] == "Fruity Limiter" and pg["notes"], pg          # not in the edition: the alternative
ns["plugin_ownership"] = lambda name: {"status": True}
assert SD.plan("gritty")["fx"][0]["plugin"] == "Soundgoodizer"
pe = SD.plan("warm clean bright")
assert len(pe["eq"]) <= 7 and len({(e["type"], e["hz"]) for e in pe["eq"]}) == len(pe["eq"]), pe["eq"]

# ---- simulated FL
params = [{"param": 0, "name": "Volume", "value": 0.8, "text": "0 dB"},
          {"param": 1, "name": "Filter cutoff", "value": 0.7, "text": "8000 Hz"},
          {"param": 2, "name": "Attack", "value": 0.1, "text": "5 ms"},
          {"param": 3, "name": "Unison detune", "value": 0.2, "text": "10 cents"},
          {"param": 4, "name": "Pan", "value": 0.5, "text": "C"}]
vals = {q["param"]: q["value"] for q in params}
faders = {7: -6.0}
calls = {"apply": [], "removed": []}
m.fl_state = lambda: {"channels": [{"index": 3, "name": "Keys", "insert": 7}, {"index": 4, "name": "Pad", "insert": 0}],
                      "mixer": []}
SCALE = {1: (11428.0, "Hz"), 2: (50.0, "ms"), 3: (50.0, "cents")}            # display = knob * scale
def text(param):
    if param in SCALE:
        return f"{vals[param] * SCALE[param][0]:.1f} {SCALE[param][1]}"
    return next(q["text"] for q in params if q["param"] == param)
m.fl_plugin_params = lambda index, slot=-1, filter="": {"plugin": "3x Osc", "params": [
    dict(q, value=vals[q["param"]], text=text(q["param"])) for q in params]}
def pset(index, param, value, slot=-1):
    vals[param] = value; return {"text": text(param)}
def pset_value(index, param, target, slot=-1):
    vals[param] = max(0.0, min(1.0, target / SCALE[param][0])); return {"text": text(param)}
m.fl_plugin_set = pset
m.fl_plugin_set_value = pset_value
class B:
    def call(self, cmd, **a):
        if cmd == "mixer_set":
            if "volume_db" in a:
                faders[a["track"]] = a["volume_db"]
            return {"volume_db": faders.get(a["track"])}
        return {}
m.BRIDGE = B()
m._open_project_guess = lambda: (os.path.join(tmp, "Song.flp"), [])
n = {"i": 0}
def wav_of(bright, gain):
    t = np.arange(44100) / 44100.0
    x = gain * (np.sin(2 * np.pi * 200 * t) + bright * np.sin(2 * np.pi * 6000 * t))
    f = os.path.join(tmp, f"p{n['i']}.wav"); n["i"] += 1
    with wave.open(f, "wb") as w_:
        w_.setnchannels(1); w_.setsampwidth(2); w_.setframerate(44100)
        w_.writeframes((x * 12000).astype("<i2").tobytes())
    return f
def preview(channels=None, keep=False, **k):
    after = bool(calls["apply"])
    f = wav_of(0.05 if after else 0.4, 0.5 if after else 0.3)
    xx, fs, _ = m._wav_load(f)
    me = m._measure(xx, fs)
    return {"lufs": me["lufs"], "centroid_hz": me["centroid_hz"], "wav": f}
m.fl_preview = preview
M.apply_ops = lambda ops, label="", project=None: (calls["apply"].append(ops), m._state({"mix_engineer": {"applied": (
    (m._state().get("mix_engineer") or {}).get("applied") or []) + [{"kind": "param", "track": 7, "slot": 2, "param": 9,
                                                                       "before": 0.5}]}}),
    {"done": [{"op": "plugin", "plugin": "Fruity Reeverb 2", "set": ["wet_pct=20 %"]}], "skipped": [],
     "added_effects": ["Fruity Reeverb 2 on track 7 slot 2"]})[-1]
m.TOOLS["flp_remove_effect"] = (lambda **a: calls["removed"].append(a), "", {})

r = SD.fl_sound_shape(3, "dark dreamy")
assert r["instrument"] == "3x Osc" and r["mixer_track"] == 7, r
# (bug hunt BH-11) the effect moves are sound shape's to undo: not left in the mix engineer's revert list too
assert not (m._state().get("mix_engineer") or {}).get("applied"), m._state().get("mix_engineer")
assert vals[0] == 0.8 and vals[4] == 0.5, vals                            # volume and pan never touched
assert vals[1] < 0.7 and vals[2] > 0.1, vals                              # darker cutoff, softer attack
ops = calls["apply"][0]
assert all(o["track"] == 7 for o in ops) and any(o["op"] == "eq" for o in ops), ops
assert r["measured"]["after_centroid_hz"] < r["measured"]["before_centroid_hz"], r["measured"]
assert "level_matched" in r and faders[7] < -6.0, (r.get("level_matched"), faders)     # louder after: fader down
assert r["result"] and "darker" in r["result"][0] and "achieved" in r["result"][0], r["result"]
assert not [f for f in os.listdir(tmp) if f.endswith(".wav")], "preview WAVs left behind"
d = SD.fl_sound_shape(3, "bright", dry_run=True)
assert d["plan"]["instrument"] and d["plan"]["effects"] and len(calls["apply"]) == 1, d
u = SD.fl_sound_shape(3, undo=True)
assert u["undone"] and abs(vals[1] - 0.7) < 1e-9 and abs(vals[2] - 0.1) < 1e-9 and faders[7] == -6.0, (u, vals, faders)
assert calls["removed"] and calls["removed"][0]["track"] == 7 and calls["removed"][0]["slot"] == 2, calls["removed"]
assert SD.fl_sound_shape(3, undo=True)["undone"] is False
# a channel straight on the Master: its own controls only, never effects on the whole song
calls["apply"].clear()
r = SD.fl_sound_shape(4, "huge", check=False)
assert not calls["apply"] and "Master" in r["notes"][0], r
SD.fl_sound_shape(4, undo=True)
assert vals[3] == 0.2, vals
assert "fl_sound_shape" in m.TOOLS

# ---- any plugin's knobs by what they do (names from any vendor)
D = SD._data()
for name, role in [("Fil Cutoff", "cutoff"), ("VCF Freq", "cutoff"), ("filter_1_cutoff", "cutoff"), ("Dry/Wet", "mix"),
                   ("Amp Env A", "attack"), ("Env1 Release", "release"), ("Uni Detune", "detune"), ("Drive", "drive"),
                   ("A", None), ("Volume", None), ("LFO Rate", None), ("Band 3 Freq", None), ("Macro Brightness", "tone")]:
    assert SD._role_of(name, D) == role, (name, SD._role_of(name, D), role)
assert SD._unnamed([{"name": f"Param {i}"} for i in range(10)])
L = SD.fl_sound_shape(3, knobs="list")
assert L["plugins"][0]["knobs"]["cutoff"] and "attack" in L["plugins"][0]["knobs"], L
k = SD.fl_sound_shape(3, knobs={"cutoff": "-1 oct", "attack": "+20 ms", "Unison detune": "=0.5"})
assert abs(vals[1] * 11428 - 8000 / 2) < 30 and abs(vals[2] * 50 - 25) < 0.6 and vals[3] == 0.5, (k, vals)
bad = SD.fl_sound_shape(3, knobs={"wobble": "+3"})
assert bad["skipped"] and not bad["set"], bad
SD.fl_sound_shape(3, undo=True)
assert abs(vals[1] - 0.7) < 1e-9 and abs(vals[2] - 0.1) < 1e-9 and vals[3] == 0.2, vals

# ---- variations: each version measured and put back; the original comes back at the end
calls["apply"].clear(); calls["removed"].clear()
v = SD.fl_sound_shape(3, "dark", variations=3)
assert [x["amount"] for x in v["versions"]] == ["subtle", "medium", "heavy"], v
assert abs(vals[1] - 0.7) < 1e-9 and calls["removed"], (vals, calls["removed"])
assert SD._undo_store(m._open_project_guess()[0], 3) is None

# ---- a Sampler channel: its envelope / filter through the sampler module
got = {}
ns["sampler_shape"] = lambda project, channel, moves, dry_run=False: (got.update(moves), {"done": [
    {"field": f, "before": 1, "after": 2} for f in moves], "undo": {"channel": channel, "events": []}})[1]
ns["sampler_undo"] = lambda project, channel, undo: got.setdefault("undone", True)
m.fl_plugin_params = lambda index, slot=-1, filter="": {"plugin": None, "params": []}
m.fl_state = lambda: {"channels": [{"index": 3, "name": "Kick", "insert": 7}], "mixer": []}
r = SD.fl_sound_shape(3, "tight punchy", check=False)
assert got.get("release_ms", "").startswith("x0") and any("sampler" in x for x in r["instrument_moves"]), (got, r)
SD.fl_sound_shape(3, undo=True)
assert got.get("undone"), got

# ---- layer: the same notes an octave up on a copy, in one edit
import shutil  # noqa: E402
song = os.path.join(tmp, "Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song)
m._with_open_project = lambda a, b, fn: fn()
d0 = m._load(song); ev0 = m._events(d0); pats0 = m._patterns(d0, ev0)
src_ch = next((int(m._rec_to_note(r)["ch"]) for pt in pats0.values() for r in pt["notes"]), None)
if src_ch is not None:
    n_src = sum(1 for pt in pats0.values() for r in pt["notes"] if int(m._rec_to_note(r)["ch"]) == src_ch)
    lr = SD.fl_sound_shape(src_ch, layer="octave")
    d1 = m._load(song); pats1 = m._patterns(d1, m._events(d1))
    lay = [m._rec_to_note(r) for pt in pats1.values() for r in pt["notes"] if int(m._rec_to_note(r)["ch"]) == lr["new_channel"]]
    assert lr["notes"] == n_src == len(lay), (lr, n_src, len(lay))
# ---- knob values: '-36 dB' sets, '+3 dB' / 'by -3 dB' / 'up' / 'down' / '-3%' / '-1 oct' move, 'x0.5', '=0.5'
G = SD.parse_move
assert G("-36 dB") == {"mode": "abs", "num": -36.0, "unit": "dB"}, G("-36 dB")
assert G("-12 st")["mode"] == G("-50 cents")["mode"] == G("-1dB")["mode"] == "abs"
assert G("-1 oct") == {"mode": "rel", "num": -1.0, "unit": "oct"} and G("+20 ms") == {"mode": "rel", "num": 20.0, "unit": "ms"}
assert G("by -3 dB") == {"mode": "rel", "num": -3.0, "unit": "dB"} and G("down 20 ms")["num"] == -20.0
assert G("up 2 st") == {"mode": "rel", "num": 2.0, "unit": "st"} and G("-3%")["mode"] == "rel" and G("-20 ms")["mode"] == "rel"
assert G("=0.5") == {"mode": "norm", "num": 0.5, "unit": None} and G("=0")["num"] == 0.0 and G("x0.5")["num"] == 0.5
assert G("to -30%") == {"mode": "abs", "num": -30.0, "unit": "%"} and G("1.2 s") == {"mode": "abs", "num": 1200.0, "unit": "ms"}
assert G("/2")["num"] == 0.5 and G("2 kHz")["num"] == 2000.0
for bad_mv in ("loud", "=2", "-3 parsecs", "up -3 dB"):
    try:
        G(bad_mv); raise SystemExit(f"bad move accepted: {bad_mv}")
    except ValueError:
        pass
K = lambda text, mv, value=0.5: SD.knob_target({"param": 0, "name": "K", "value": value, "text": text}, None, move=mv)
assert K("-10.0 dB", "-36 dB") == ("value", -36.0) and K("-10.0 dB", "+3 dB") == ("value", -7.0)
assert K("-10.0 dB", "by -3 dB") == ("value", -13.0) and K("0.0 dB", "down 6 dB") == ("value", -6.0)
for t in ("34%", "34 %", "34.0%"):
    assert K(t, "-3%") == ("value", 31.0) and K(t, "x0.5") == ("value", 17.0) and K(t, "=0") == ("norm", 0.0), t
assert K("-10.0 dB", "-3%") == ("norm", 0.47)                         # % on a dB knob: a share of its travel
assert K("8000 Hz", "-36 dB")[0] is None and K("34%", "0.8 Q")[0] is None   # wrong unit: refused, said why
assert K("8000 Hz", "-1 oct") == ("value", 4000.0) and K("8000 Hz", "1 oct") == ("value", 16000.0)
assert K("1.50 oct", "+0.5 oct") == ("value", 2.0) and K("1.50 oct", "2 oct") == ("value", 2.0)
assert abs(K("1.50 oct", "0.8 Q")[1] - M.q_to_oct(0.8)) < 1e-9
assert K("-12 st", "-24 st") == ("value", -24.0) and K("950 ms", "1.2 s") == ("value", 1200.0)

# a simulated mixer track: compressor, delay, EQ, limiter (curves rising and falling, linear and not)
FX = {1: ("Fruity Compressor", {0: ("Threshold", lambda x: f"{-60 * (1 - x) ** 2:.1f} dB", 0.9),
                                1: ("Gain", lambda x: f"{30 * x - 18:+.1f} dB", 0.6)}),
      2: ("Fruity Delay 3", {0: ("Wet", lambda x: f"{round(100 * x)}%", 0.34),
                             1: ("Feedback level", lambda x: f"{round(100 * x * x)} %", 0.5831),
                             2: ("Feedback cutoff", lambda x: f"{round(20 + 19980 * x * x)} Hz", 0.5)}),
      3: ("Fruity Parametric EQ 2", {15: ("Band 2 width", lambda x: f"{round(100 * x)}%", 0.34)}),
      4: ("Fruity Limiter", {0: ("Ceiling", lambda x: f"{-24 * x:.1f} dB", 0.0)})}
fxv = {(s, q): v for s, (_, ks) in FX.items() for q, (_, _, v) in ks.items()}
fx0 = dict(fxv)
visits, sv = [], {"mode": "fail", "calls": 0}
m.fl_state = lambda: {"channels": [{"index": 3, "name": "Keys", "insert": 7}],
                      "mixer": [{"index": 7, "effects": [{"name": FX[s][0], "slot": s} for s in FX]}]}
m.fl_plugin_params = lambda index, slot=-1, filter="": {"plugin": FX[slot][0], "params": [
    {"param": q, "name": nm, "value": fxv[(slot, q)], "text": f(fxv[(slot, q)])} for q, (nm, f, _) in FX[slot][1].items()]}
def fset(index, param, value, slot=-1):
    value = max(0.0, min(1.0, float(value))); fxv[(slot, param)] = value; visits.append((slot, param, value))
    return {"text": FX[slot][1][param][1](value), "value": value}
def fset_value(index, param, target, slot=-1):
    sv["calls"] += 1
    if sv["mode"] == "fail":                                   # FL's display search gave up (knob put back)
        raise ValueError("FL's display never reached it; left unchanged")
    if sv["mode"] == "stuck":                                  # 'done', but the display didn't move
        x = fxv[(slot, param)]; return {"text": FX[slot][1][param][1](x), "value": x}
    num = lambda x: SD._num(FX[slot][1][param][1](x))[0]       # a working search: bisection either direction
    a, b = 0.0, 1.0; up = num(1.0) > num(0.0)
    for _ in range(40):
        c = (a + b) / 2
        if (num(c) < target) == up:
            a = c
        else:
            b = c
    fxv[(slot, param)] = (a + b) / 2; return {"text": FX[slot][1][param][1]((a + b) / 2), "value": (a + b) / 2}
m.fl_plugin_set = fset
m.fl_plugin_set_value = fset_value
shown = lambda s, q: SD._num(FX[s][1][q][1](fxv[(s, q)]))[0]

# bug: '-36 dB' was relative and the fallback slammed the threshold to 0 dB
for mode in ("ok", "fail", "stuck"):
    sv["mode"] = mode; visits.clear(); fxv.update(fx0)
    r = SD.fl_sound_shape(3, knobs={"Threshold": "-36 dB"}, plugin="Compressor")
    assert r["set"] and abs(shown(1, 0) - -36.0) <= 0.2, (mode, r, shown(1, 0))
    assert all(v <= 0.9 + 1e-9 for s, q, v in visits), (mode, visits)  # never toward 0 dB (the far end)
    assert len(visits) <= SD.SEARCH_MAX_SETS + 1, (mode, len(visits))
    assert "clamped" not in r["set"][0], r
    if mode == "ok":
        assert not visits, visits                              # FL's own search did it: no extra round trips
sv["mode"] = "fail"
for plug, knob, want, s, q in (("Compressor", "Gain", "-6 dB", 1, 1), ("Limiter", "Ceiling", "-1 dB", 4, 0)):
    visits.clear()
    r = SD.fl_sound_shape(3, knobs={knob: want}, plugin=plug)
    assert abs(shown(s, q) - float(want.split()[0])) <= 0.2 and len(visits) <= SD.SEARCH_MAX_SETS + 1, (r, shown(s, q), visits)
# out of the knob's range: the nearest end, said so; never the far end
fxv.update(fx0); visits.clear()
r = SD.fl_sound_shape(3, knobs={"Threshold": "-80 dB"}, plugin="Compressor")
assert fxv[(1, 0)] == 0.0 and "clamped to the knob's range" in r["set"][0], r
assert all(v <= 0.9 + 1e-9 for _s, _q, v in visits) and len(visits) <= SD.SEARCH_MAX_SETS + 1, visits
r = SD.fl_sound_shape(3, knobs={"Ceiling": "+3 dB"}, plugin="Limiter")
assert fxv[(4, 0)] == 0.0 and "clamped" in r["set"][0], r
# bug: '-3%' on a delay's mix / feedback ('34%' and '34 %'), 'x0.5' multiplies, '=0' is the raw knob
for mode in ("ok", "fail"):
    sv["mode"] = mode; fxv.update(fx0)
    r = SD.fl_sound_shape(3, knobs={"mix": "-3%", "feedback": "-3%"}, plugin="Delay")
    assert shown(2, 0) == 31 and shown(2, 1) == 31, (mode, r, shown(2, 0), shown(2, 1))
    assert fxv[(2, 2)] == 0.5 and any("'feedback' = 'Feedback level'" in x for x in r["set"]), r   # one knob, named
    fxv.update(fx0)
    SD.fl_sound_shape(3, knobs={"Wet": "x0.5", "Feedback level": "=0"}, plugin="Delay")
    assert shown(2, 0) == 17 and fxv[(2, 1)] == 0.0, (shown(2, 0), fxv[(2, 1)])
# a knob whose display never moves: put back, said so
FX[5] = ("Stuck FX", {0: ("Drive", lambda x: "3.0 dB", 0.4)}); fxv[(5, 0)] = fx0[(5, 0)] = 0.4
m.fl_state = lambda: {"channels": [{"index": 3, "name": "Keys", "insert": 7}],
                      "mixer": [{"index": 7, "effects": [{"name": FX[s][0], "slot": s} for s in FX]}]}
r = SD.fl_sound_shape(3, knobs={"Drive": "-6 dB"}, plugin="Stuck")
assert not r["set"] and "didn't change" in r["skipped"][0]["why"] and fxv[(5, 0)] == 0.4, (r, fxv[(5, 0)])
# EQ band widths shown as a percentage: '+10%' / 'x0.5' move them; a Q onto a % display is refused, said why
fxv.update(fx0)
r = SD.fl_sound_shape(3, knobs={"Band 2 width": "+10%"}, plugin="Parametric")
assert shown(3, 15) == 44, (r, shown(3, 15))
r = SD.fl_sound_shape(3, knobs={"Band 2 width": "x0.5"}, plugin="Parametric")
assert shown(3, 15) == 22, (r, shown(3, 15))
r = SD.fl_sound_shape(3, knobs={"Band 2 width": "0.8 Q"}, plugin="Parametric")
assert not r["set"] and "Q doesn't apply" in r["skipped"][0]["why"], r
# a word's EQ band whose width the plugin map couldn't convert (FL shows %): flh_mixeng sets it by the map's curve
# (the old _eq_width_fallback here is gone), said as unverified, with an undo entry
fxv.update(fx0)
class _PB:
    def call(self, cmd, **a):
        if cmd == "plugin_set":
            return fset(a["index"], a["param"], a["value"], a.get("slot", -1))
        return B().call(cmd, **a)
_old_bridge = m.BRIDGE; m.BRIDGE = _PB()
r = M.plugin_set_named(7, 3, M.PEQ, {"band2_width": "0.8 Q"})
m.BRIDGE = _old_bridge
e = M.plugin_map(M.PEQ)["controls"]["band2_width"]
assert r["undo"] == [{"track": 7, "slot": 3, "param": 15, "before": 0.34}], r
assert abs(fxv[(3, 15)] - M.to_norm(e, M.q_to_oct(0.8))) < 1e-9, (fxv[(3, 15)], r)
s0 = r["set"][0]
assert s0["unverified"] and not s0["ok"] and "set by the plugin map's curve, display shows %: unverified, listen" in \
    s0["note"] and not r["skipped"], r
assert not hasattr(SD, "_eq_width_fallback")
# undo puts every knob the knob mode touched back
SD._undo_store(m._open_project_guess()[0], 3, pop=True)
fxv.update(fx0)
SD.fl_sound_shape(3, knobs={"Threshold": "-36 dB", "Gain": "-6 dB"}, plugin="Compressor")
SD.fl_sound_shape(3, knobs={"mix": "-3%"}, plugin="Delay")
u = SD.fl_sound_shape(3, undo=True)
assert u["undone"] and all(abs(fxv[k] - fx0[k]) < 1e-9 for k in fx0), (u, fxv)

# ---- (PC check 2026-10-23) 'darker + dreamy': the dark word wins on the top end, tails are darkened, EQ goes last
for amt in ("subtle", "medium", "heavy", "insane"):
    for w_ in ("darker dreamy", "dreamy dark", "dark airy", "dark insane", "dark bright"):
        pd = SD.plan(w_, amt)
        cuts = [e for e in pd["eq"] if e["type"] == "High shelf" and e["gain_db"] < 0]
        assert cuts and pd["eq_last"], (w_, amt, pd["eq"])
        assert not [e for e in pd["eq"] if e["type"] in ("High shelf", "Peaking") and e["gain_db"] > 0
                    and e["hz"] >= min(c["hz"] for c in cuts) / 2], (w_, amt, pd["eq"])      # no boost above the cut
        assert pd["inst"].get("cutoff", 0) <= 0 and pd["inst"].get("tone", 0) <= 0, pd["inst"]
        assert "darker" in pd["intents"] and not {"brighter", "airier"} & set(pd["intents"]), pd["intents"]
pd = SD.plan("darker dreamy", "heavy")
fxd = {f["plugin"]: f["controls"] for f in pd["fx"]}
assert fxd["Fruity Reeverb 2"]["high_cut_hz"] <= 5000 and fxd["Fruity Reeverb 2"]["high_damping_hz"] <= 3500, fxd
assert fxd["Fruity Delay 3"]["feedback_cutoff_hz"] <= 3000 and any("darker wins" in x for x in pd["notes"]), pd
assert min(e["hz"] for e in pd["eq"] if e["type"] == "High shelf") <= 3000, pd["eq"]   # reaches the 2.5k+ band
pdr = SD.plan("dreamy")                                         # alone: its air boost stays, no tail darkening
assert any(e["gain_db"] > 0 and e["hz"] >= 8000 for e in pdr["eq"]) and not pdr["eq_last"], pdr["eq"]
assert "high_cut_hz" not in {f["plugin"]: f["controls"] for f in pdr["fx"]}["Fruity Reeverb 2"], pdr["fx"]
assert not SD.plan("dark")["fx"], SD.plan("dark")["fx"]         # fx_tune never adds an effect by itself
# a cutoff showing '0%': mid-travel (an offset / bipolar display) gets a real knob move; at its floor no fake 0->0
KT = lambda text, value, s, name="Filter cutoff": SD.knob_target({"param": 1, "name": name, "value": value,
                                                                  "text": text}, "cutoff", share=s)
assert KT("0%", 0.5, -0.27) == ("norm", 0.23), KT("0%", 0.5, -0.27)
k0 = KT("0%", 0.0, -0.27)
assert k0[0] is None and "lowest" in k0[1], k0
assert KT("-20%", 0.4, -0.18) == ("value", -38.0), KT("-20%", 0.4, -0.18)             # bipolar display keeps its sign
assert KT("100%", 1.0, 0.2)[0] is None and KT("40%", 0.4, -0.1) == ("value", 30.0)
_fpp = m.fl_plugin_params
for cut_v, expect_move in ((0.0, False), (0.5, True)):
    flex = [{"param": 10, "name": "Filter cutoff", "value": cut_v, "text": "0%"},
            {"param": 11, "name": "Post filter cutoff", "value": cut_v, "text": "0%"},
            {"param": 12, "name": "Volume env release", "value": 0.0, "text": "0%"},
            {"param": 13, "name": "Filter env release", "value": 0.0, "text": "0%"},
            {"param": 14, "name": "Reverb mix", "value": 0.5, "text": "50%"}]
    m.fl_plugin_params = lambda index, slot=-1, filter="", flex=flex: {"plugin": "FLEX", "params": flex}
    dr = SD.fl_sound_shape(3, "darker dreamy", dry_run=True)
    moved = {x["name"]: x for x in dr["plan"]["instrument"]}
    assert "Filter env release" not in moved and "Volume env release" in moved, moved     # no brighter filter tail
    assert ("Filter cutoff" in moved) == expect_move, (cut_v, moved)
    if expect_move:
        assert moved["Filter cutoff"]["kind"] == "norm" and moved["Filter cutoff"]["to"] < 0.5, moved
    effs = dr["plan"]["effects"]
    assert [o["op"] for o in effs] == sorted((o["op"] for o in effs), key=lambda op: op == "eq"), effs  # EQ last
    sh = [o["gain_db"] for o in effs if o["op"] == "eq" and o["type"] == "High shelf"]
    assert sh and all(g < 0 for g in sh), effs
    if not expect_move:                                         # no cutoff to lower: the EQ cuts deeper, said
        assert min(sh) <= -6 * SD.DARK_EQ_DEEPER + 0.01 and any("cutoff couldn't go lower" in x for x in dr["notes"]), dr
m.fl_plugin_params = _fpp
print("sound shape ok")
