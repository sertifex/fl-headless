"""flh_mixeng map name check: plugin map controls matched to the live FL parameter names of a simulated FL
(exact, renamed, unmatched, ambiguous), corrected user maps written and used by plugin_set_named, shipped data
untouched unless the dev script is asked to write.
Run: python3 tests/test_map_names.py"""
import hashlib, io, json, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE); sys.path.insert(0, os.path.join(REPO, "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import fl_headless_mcp as m  # noqa: E402
import flh_mixeng as X  # noqa: E402
ns = vars(m)
X.register(ns)
sys.path.insert(0, os.path.join(REPO, "tools"))
import plugin_maps_names as T  # noqa: E402

assert "fl_plugin_maps_check" in m.TOOLS
desc, schema = m.TOOLS["fl_plugin_maps_check"][1], m.TOOLS["fl_plugin_maps_check"][2]
assert len(desc) <= 290, len(desc)
assert all(len(p.get("description", "")) <= 80 for p in schema["properties"].values())


def digest():
    h = hashlib.sha256()
    for f in sorted(os.listdir(X.MAP_DIR)):
        with open(os.path.join(X.MAP_DIR, f), "rb") as fh:
            h.update(f.encode() + fh.read())
    return h.hexdigest()


SHIPPED = digest()

# ---------------------------------------------------------------- a simulated FL: live names as FL 2026 shows them
REEVERB = ["Low cut", "High cut", "Predelay", "Room size", "Diffusion", "Decay time", "High damping",
           "Bass multiplier", "Crossover", "Stereo separation", "Dry level", "Early reflection level", "Wet level",
           "Mod Speed", "Mod Depth"]
DELAY = ["Input wet", "Delay type", "Tempo sync", "Keep pitch", "Time", "Delay time R/L offset", "Time smooth",
         "Stereo spread", "Modulation rate", "Modulation time", "Modulation cutoff", "Diffusion level",
         "Diffusion spread", "Distortion type", "Feedback level", "Filter mode", "Feedback cutoff",
         "Feedback resonance", "Distortion level", "Distortion knee", "Distortion symmetry", "Rate reduction",
         "Bits reduction", "Output wet", "Output tone", "Output dry"]
DELAY_IDX = {n: i for i, n in enumerate(DELAY)}
for n, want in (("Time", 4), ("Delay time R/L offset", 5), ("Stereo spread", 7), ("Feedback level", 14),
                ("Feedback cutoff", 16), ("Output wet", 23), ("Output dry", 25)):
    assert DELAY_IDX[n] == want, (n, DELAY_IDX[n])


def text(name, v):
    nl = name.lower()
    if "cut" in nl or "damping" in nl or "crossover" in nl:
        return f"{20 * 1000 ** v:.0f} Hz"
    if "decay" in nl:
        return f"{0.1 + 9.9 * v:.1f} s"
    if "delay" in nl or nl in ("time", "predelay"):
        return f"{500 * v:.0f} ms"
    if "type" in nl or "mode" in nl or "sync" in nl or "pitch" in nl:
        return "On" if v > 0.5 else "Off"
    return f"{100 * v:.0f}%"


FX = {(5, 0): ("Fruity Reeverb 2", {i: (n, 0.5) for i, n in enumerate(REEVERB)}),
      (5, 1): ("Fruity Delay 3", {DELAY_IDX[n]: (n, 0.5) for n in DELAY})}


def fake_params(index, slot=-1, filter=""):
    plug, P = FX.get((int(index), int(slot)), (None, {}))
    if plug is None:
        return {"plugin": "", "params": []}
    return {"plugin": plug, "params": [{"param": i, "name": n, "value": v, "text": text(n, v)}
                                       for i, (n, v) in sorted(P.items())]}


sets = []


class B:
    def call(self, cmd, timeout=15.0, **a):
        if cmd == "plugin_set":
            plug, P = FX[(a["index"], a["slot"])]
            n, _ = P[a["param"]]; P[a["param"]] = (n, max(0.0, min(1.0, a["value"])))
            sets.append((a["index"], a["slot"], a["param"]))
            return {"param": a["param"], "value": P[a["param"]][1], "text": text(n, P[a["param"]][1])}
        if cmd == "state":
            return {"playing": False}
        raise AssertionError(cmd)


m.BRIDGE = B(); ns["BRIDGE"] = m.BRIDGE
ns["fl_plugin_params"] = fake_params; m.fl_plugin_params = fake_params
ns["fl_state"] = lambda: {"mixer": [{"index": 5, "name": "FX", "effects": [{"slot": 0, "name": "Fruity Reeverb 2"},
                                                                          {"slot": 1, "name": "Fruity Delay 3"}]}]}

# ---------------------------------------------------------------- the matcher on its own
mp = {"controls": {"a": {"fl_name": "Band 2 level", "unit": "dB"}, "b": {"fl_name": "Gain", "unit": "dB"},
                   "c": {"fl_name": "Thres", "unit": "dB"}, "d": {"fl_name": "Cutoff", "unit": "Hz"}}}
live = [{"param": 0, "name": "Band 3 level", "text": "0.0 dB"}, {"param": 1, "name": "Low gain", "text": "0.0 dB"},
        {"param": 2, "name": "High gain", "text": "0.0 dB"}, {"param": 3, "name": "Threshold", "text": "-6 dB"},
        {"param": 4, "name": "Cutoff freq", "text": "-3 dB"}]
got, rest = X.match_live_names(mp, live)
assert "a" in rest, got                                           # band numbers never cross
assert "b" in rest and set(rest["b"]) <= {"Low gain", "High gain"}, rest   # generic + ambiguous: not guessed
assert got["c"][0]["name"] == "Threshold" and got["c"][1] == "synonym", got
assert "d" in rest, got                                           # a Hz control never lands on a dB knob
print("matcher ok")

# ---------------------------------------------------------------- the check: report, user maps written
before_rv = X.plugin_map("Fruity Reeverb 2")
r = m.TOOLS["fl_plugin_maps_check"][0](tracks=[5], apply=True)
rows = {x["plugin"]: x for x in r["plugins"]}
assert set(rows) == {"Fruity Reeverb 2", "Fruity Delay 3"}, rows
assert "Fruity Parametric EQ 2" in r["not_loaded"] and "mix.map_names" in r["note"]
rv, dl = rows["Fruity Reeverb 2"], rows["Fruity Delay 3"]
ren = {x["control"]: x for x in rv["renamed"]}
assert rv["matched"] >= 8, rv                                     # exact names (Low cut, Room size, ...)
if before_rv["controls"]["wet_pct"]["fl_name"] != "Wet level":    # the shipped map may already be fixed
    assert ren["wet_pct"]["new"] == "Wet level" and ren["wet_pct"]["index"] == 12, ren
    assert ren["dry_pct"]["new"] == "Dry level" and ren["dry_pct"]["index"] == 10, ren
    assert ren["decay_s"]["new"] == "Decay time" and ren["early_pct"]["new"] == "Early reflection level", ren
assert not rv["unmatched"], rv["unmatched"]
dren = {x["control"]: x for x in dl["renamed"]}
dmap = X.plugin_map("Fruity Delay 3")["controls"]
if dmap["wet_pct"]["fl_name"] == "Wet":
    assert dren["wet_pct"]["new"] == "Output wet" and dren["wet_pct"]["index"] == 23, dren   # not Input wet
    assert dren["dry_pct"]["new"] == "Output dry" and dren["dry_pct"]["index"] == 25, dren
un = {x["control"]: x for x in dl["unmatched"]}
if dmap.get("stereo_offset", {}).get("fl_name") == "Stereo offset":
    assert "stereo_offset" in un and "Delay time R/L offset" in un["stereo_offset"]["candidates"], un
    assert "Delay time R/L offset" in dl["live_names_unused"]
assert any("Wet level" in d for d in r["diff"]) or before_rv["controls"]["wet_pct"]["fl_name"] == "Wet level"
upath = os.path.join(X._user_map_dir(), "fruity_reeverb_2.json")
assert rv["user_map"] == upath and os.path.isfile(upath)
with open(upath, encoding="utf-8") as f:
    U = json.load(f)
w = U["controls"]["wet_pct"]
assert w["fl_name"] == "Wet level" and w["index"] == 12 and w["names_verified"] and U["names_verified"]
assert w["unit"] == before_rv["controls"]["wet_pct"]["unit"] and w["curve"] == before_rv["controls"]["wet_pct"]["curve"]
assert w["verified"] is False                                     # names only: curves are still unverified
assert X.plugin_map("Fruity Reeverb 2")["controls"]["wet_pct"]["fl_name"] == "Wet level"
assert digest() == SHIPPED, "the check wrote into data/plugin_maps"
print("check ok")

# ---------------------------------------------------------------- the corrected map is what plugin_set_named uses
sets.clear()
s = X.plugin_set_named(5, 0, None, {"wet_pct": 20})
assert s["set"] and s["set"][0]["matched_by"] == "name+index", s
assert sets and all(p == 12 for _, _, p in sets), sets
assert s["set"][0]["ok"], s

# ---------------------------------------------------------------- a second run: everything exact now
r2 = X.map_names_check(tracks=[5], apply=False, full=True)
rows2 = {x["plugin"]: x for x in r2["plugins"]}
assert not rows2["Fruity Reeverb 2"]["renamed"] and not rows2["Fruity Reeverb 2"]["reindexed"], rows2
assert r2["written"] is False
assert digest() == SHIPPED
# apply=False writes nothing
shutil.rmtree(X._user_map_dir())
X.map_names_check(tracks=[5], apply=False)
assert not os.path.isdir(X._user_map_dir()) or not os.listdir(X._user_map_dir())
X.map_names_check(apply=True)                                    # default scan: the tracks fl_state lists
assert os.path.isfile(upath)
print("rerun ok")

# ---------------------------------------------------------------- dev script: diff by default, --write on request
ship = os.path.join(tmp, "ship"); shutil.copytree(X.MAP_DIR, ship)
out = io.StringIO()
ch = T.run(X._user_map_dir(), ship, write=False, out=out)
with open(os.path.join(ship, "fruity_reeverb_2.json"), encoding="utf-8") as f:
    assert json.load(f) == json.load(open(os.path.join(X.MAP_DIR, "fruity_reeverb_2.json"), encoding="utf-8"))
if before_rv["controls"]["wet_pct"]["fl_name"] != "Wet level":
    k = os.path.join(ship, "fruity_reeverb_2.json")
    assert k in ch and '"fl_name": "Wet level"' in ch[k] and "+" in ch[k], out.getvalue()
T.run(X._user_map_dir(), ship, write=True, out=io.StringIO())
with open(os.path.join(ship, "fruity_reeverb_2.json"), encoding="utf-8") as f:
    F = json.load(f)
fw = F["controls"]["wet_pct"]
assert fw["fl_name"] == "Wet level" and fw["index"] == 12 and fw["names_verified"] and F["names_verified"]
assert fw["verified"] is False and F["source"] == "shipped"
assert T.run(X._user_map_dir(), ship, write=False, out=io.StringIO()) == {}   # applied: no diff left
assert digest() == SHIPPED

# (PC check 2026-10-23) the shipped maps no longer claim controls FL doesn't expose; PC-corrected indexes kept
def shipped(name):
    with open(os.path.join(X.MAP_DIR, name), encoding="utf-8") as f:
        return json.load(f)
gone = {"fruity_limiter.json": ("mode", "comp_gain_db", "gate_attack_ms"), "fruity_love_philter.json": ("mix_pct",),
        "fruity_stereo_shaper.json": ("mix_pct",)}
for f, keys in gone.items():
    S_ = shipped(f)
    assert not set(keys) & set(S_["controls"]) and set(keys) <= set(S_["not_exposed"]), (f, list(S_["controls"]))
L_ = shipped("fruity_limiter.json")["controls"]
assert L_["gate_gain_db"]["index"] == 15 and L_["gate_release_ms"]["index"] == 17, L_
MB = shipped("fruity_multiband_compressor.json")["controls"]
assert MB["high_attack_ms"]["fl_name"] == "H Attack" and MB["high_release_ms"]["fl_name"] == "H Release"
assert all(MB[f"{b}_{k}"]["fl_name"].startswith(b[0].upper() + " ") and "PC-VERIFY" in MB[f"{b}_{k}"]["pc_verify"]
           for b in ("low", "mid") for k in ("threshold_db", "attack_ms", "release_ms", "gain_db")), MB
SH = shipped("fruity_stereo_shaper.json")["controls"]
live = [{"param": i, "name": n, "value": 0.5, "text": "100%"} for i, n in enumerate(
    ["Left / right delay", "Left / right dephasing", "Right into left channel volume", "Left channel volume",
     "Right channel volume", "Left into right channel volume"])]
got, rest = X.match_live_names({"controls": SH}, live)
assert not rest and len(got) == len(SH), (rest, list(got))
print("ok")
