"""flh_mixeng units: a plugin map's unit vs the unit FL shows on the knob. Known relations convert (s/ms, Q/oct,
x/%, st/cents); a width FL shows as '%' (no known relation to octaves) is set by the map's curve and said to be
unverified; a level knob in a unit that can't be converted is never set by a guess; the name check accepts a width
shown as %. Fake FL plugins, no FL. Run: python3 tests/test_mixeng_units.py"""
import math, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_mixeng as X  # noqa: E402
ns = vars(m)
flh_store.register(ns); X.register(ns)

# ---------------------------------------------------------------- known relations
cu = X.convert_unit
assert cu(1.2, "s", "ms") == 1200 and cu(950, "ms", "s") == 0.95 and cu(2.5, "kHz", "Hz") == 2500
assert cu(1.5, "x", "%") == 150 and cu(50, "%", "x") == 0.5
assert cu(0.25, "st", "cents") == 25 and cu(-50, "cents", "st") == -0.5
assert abs(cu(cu(1.4, "Q", "oct"), "oct", "Q") - 1.4) < 1e-9
for a, b in (("oct", "%"), ("dB", "%"), ("Hz", "ms"), ("dB", "Hz")):
    assert not X.units_convertible(a, b)
    try:
        cu(1, a, b); raise SystemExit(f"{a}->{b} converted")
    except ValueError as ex:
        assert "can't convert" in str(ex)
assert X.units_convertible("s", "ms") and X.units_convertible("oct", "Q") and X.units_convertible("Hz", "Hz")

# ---------------------------------------------------------------- fake FL plugins with displays in other units
TYPES = ["Off", "Low pass", "Band pass", "High pass", "Notch", "Low shelf", "Peaking", "High shelf"]


def peq_text(p, v, width):
    if p < 7:
        return f"{-18 + 36 * v:+.1f} dB"
    if p < 14:
        hz = 10 * (2200.0 ** v)
        return f"{hz / 1000:.2f} kHz" if hz >= 1000 else f"{hz:.0f} Hz"
    if p < 21:
        return width(v)
    return TYPES[int(round(v * 7))]


def decay_text(v):
    sec = 0.1 + 19.9 * v * v
    return f"{sec * 1000:.0f} ms" if sec < 1 else f"{sec:.2f} s"


PEQ_NAMES = [f"Band {b} level" for b in range(1, 8)] + [f"Band {b} freq" for b in range(1, 8)] + \
            [f"Band {b} width" for b in range(1, 8)] + [f"Band {b} type" for b in range(1, 8)]
PCT = lambda v: f"{round(100 * v)}%"                                    # a width FL shows as a percentage
QD = lambda v: f"Q {X.oct_to_q(0.1 + 3.9 * v):.2f}"                     # a width FL shows as Q (falls as it rises)
PLUG = {(1, 0): ("Fruity Parametric EQ 2", {p: (PEQ_NAMES[p], (lambda v, p=p: peq_text(p, v, PCT))) for p in range(28)}),
        (2, 0): ("Fruity Parametric EQ 2", {p: (PEQ_NAMES[p], (lambda v, p=p: peq_text(p, v, QD))) for p in range(28)}),
        (3, 0): ("Fruity Reeverb 2", {5: ("Decay time", decay_text)}),
        (4, 0): ("Fruity Limiter", {0: ("Gain", lambda v: f"{round(100 * v)}%")})}   # map: dB; FL: %
VAL = {k: {p: 0.3 for p in ps} for k, (_, ps) in PLUG.items()}
sets = []


def fake_call(cmd, timeout=15.0, **a):
    k = (a.get("index"), a.get("slot"))
    if cmd == "plugin_params":
        name, ps = PLUG[k]
        return {"plugin": name, "params": [{"param": p, "name": nm, "value": VAL[k][p], "text": f(VAL[k][p])}
                                           for p, (nm, f) in ps.items()]}
    if cmd == "plugin_set":
        v = max(0.0, min(1.0, float(a["value"]))); VAL[k][a["param"]] = v; sets.append((k, a["param"], v))
        return {"param": a["param"], "value": v, "text": PLUG[k][1][a["param"]][1](v)}
    if cmd == "plugin_set_value":
        f = PLUG[k][1][a["param"]][1]
        get = lambda v: X.parse_display(f(v))[0]
        tgt = float(a["target"]) * (1000.0 if X.parse_display(f(0.5))[1] == "ms" and "s" in f(0.5)
                                    and "ms" not in f(0.5) else 1.0)
        lo, hi = 0.0, 1.0; up = get(1.0) >= get(0.0)
        for _ in range(40):
            mid = (lo + hi) / 2
            if (get(mid) < tgt) == up:
                lo = mid
            else:
                hi = mid
        VAL[k][a["param"]] = lo
        return {"param": a["param"], "value": lo, "text": f(lo)}
    if cmd == "state":
        return {"playing": False}
    raise AssertionError(cmd)


m.BRIDGE.call = fake_call
ns["fl_plugin_params"] = lambda track, slot=-1, filter="": fake_call("plugin_params", index=track, slot=slot)
ns["fl_plugin_set_value"] = lambda track, param, target, slot=-1: fake_call(
    "plugin_set_value", index=track, slot=slot, param=param, target=target)
peq = X.plugin_map(X.PEQ)["controls"]

# a width FL shows as %: set by the map's curve (octaves from the Q asked), unverified, undoable - never skipped
r = X.plugin_set_named(1, 0, X.PEQ, {"band2_width": "0.8 Q", "band2_gain_db": -2.0})
got = {s["control"]: s for s in r["set"]}
w = got["band2_width"]
assert not r["skipped"] and w["unverified"] and w["ok"] is False, r
assert "set by the plugin map's curve, display shows %: unverified, listen" in w["note"], w
assert abs(VAL[(1, 0)][15] - X.to_norm(peq["band2_width"], X.q_to_oct(0.8))) < 1e-9, VAL[(1, 0)][15]
assert {"track": 1, "slot": 0, "param": 15, "before": 0.3} in r["undo"], r["undo"]
assert got["band2_gain_db"]["ok"] and got["band2_gain_db"]["text"] == "-2.0 dB", got
assert any("unverified" in n for n in r.get("notes") or []), r
assert r["ok"] is False                                                 # honest: not verified

# the same through the engineer's apply: the 'set' line carries the note (sound shaping shows it)
st = {"mixer": [{"index": 1, "effects": [{"slot": 0, "name": X.PEQ}]}], "channels": []}
ns["fl_state"] = lambda *a, **k: st; m.fl_state = ns["fl_state"]
X._free_band = lambda *a, **k: 3
ns["_open_project_guess"] = lambda: (None, [])
res = X.apply_ops([{"op": "eq", "track": 1, "slot": 0, "hz": 300, "gain_db": 2.0, "type": "Peaking", "q": 0.8}])
line = " ".join(res["done"][0]["set"])
assert "band3_width=" in line and "unverified, listen" in line and not res["done"][0]["skipped"], res

# a width FL shows as Q (a known relation): converted, read back, verified
r = X.plugin_set_named(2, 0, X.PEQ, {"band4_width": "1 oct"})
s0 = r["set"][0]
assert s0["ok"] and not s0.get("unverified") and abs(X.parse_display(s0["text"])[0] - X.oct_to_q(1.0)) < 0.1, r

# seconds on the map, FL shows ms below 1 s and s above: both reach the asked time
for want, sec in (("2.5 s", 2.5), ("600 ms", 0.6), (1.5, 1.5)):
    r = X.plugin_set_named(3, 0, "Fruity Reeverb 2", {"decay_s": want})
    num = X.parse_display(r["set"][0]["text"])[0] / 1000.0
    assert r["set"][0]["ok"] and abs(num - sec) <= 0.06 * sec + 0.01, (want, r)

# a level knob (map dB) that FL shows as %: never set by a guess, left unchanged, said why
sets.clear()
r = X.plugin_set_named(4, 0, "Fruity Limiter", {"limit_gain_db": 3.0})
assert not r["set"] and r["skipped"] and "never set by a guess" in r["skipped"][0]["why"] and not r["undo"], r
assert not sets and VAL[(4, 0)][0] == 0.3, sets

# name check: a width shown as % is the same family; Hz on a width is not
assert X._unit_ok({"unit": "oct"}, {"text": "34%"}) and X._unit_ok({"unit": "oct"}, {"text": "Q 1.40"})
assert not X._unit_ok({"unit": "oct"}, {"text": "800 Hz"}) and X._unit_ok({"unit": "s"}, {"text": "950 ms"})

# the first local step follows the map's curve in FL's unit (Q falls while octaves rise)
sl = X._display_slope(peq["band2_width"], "Q", 0.3)
assert sl is not None and sl < 0, sl
assert X._display_slope(peq["band2_width"], "oct", 0.3) > 0 and X._display_slope(peq["band2_width"], "%", 0.3) is None
# learning a map keeps the control's own unit: decay_s stays seconds (FL's '1.20 s' reads as 1200 ms), so the room
# tail's plain number (decay_s: 2.5) still means 2.5 s after fl_plugin_map_learn
ns["fl_plugin_info"] = lambda track, slot=-1: {"params": 1}
_orig = m.BRIDGE.call
m.BRIDGE.call = lambda cmd, timeout=15.0, **a: ({"plugin": "Fruity Reeverb 2", "params": 1} if cmd == "plugin_info"
                                                else (_ for _ in ()).throw(RuntimeError("no probe")) if cmd == "plugin_probe"
                                                else _orig(cmd, **a))
res = X._learn(3, 0, 17, None)
d = X.plugin_map("Fruity Reeverb 2")["controls"]["decay_s"]
assert d["verified"] and d["unit"] == "s" and d["fl_unit"] == "ms" and abs(d["max"] - 20.0) < 0.05, d
r = X.plugin_set_named(3, 0, "Fruity Reeverb 2", {"decay_s": 2.5})
assert r["set"][0]["ok"] and abs(X.parse_display(r["set"][0]["text"])[0] - 2500) <= 0.06 * 2500, r
print("mixeng units ok")
