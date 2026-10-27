"""Mix proposals from measured tracks. Run: python tests/test_mix.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import fl_mix as M

def tr(name, lufs, **b):
    bands = {"sub": -30, "bass": -12, "low_mid": -8, "mid": -6, "presence": -10, "air": -16}; bands.update(b)
    return {"name": name, "lufs": lufs, "bands_db": bands}

tracks = {5: tr("Melody", -20, mid=-4), 7: tr("Strings", -21, low_mid=-3, bass=-5), 3: tr("Bass", -24, bass=-2, sub=-10),
          4: tr("Accompaniment", -26)}
p = M.propose(tracks, mix={"true_peak_dbfs": 0.4, "bands_db": {"low_mid": -2.5}})
acts = [(x["name"], x["action"]) for x in p]
assert ("Strings", "fader") in acts, acts                       # pad too close to the lead
assert ("Master", "master") == acts[0], acts                    # clipping first
assert ("Strings", "highpass") in acts, acts                    # strings' low end vs the bass part
assert all("id" in x and "why" in x for x in p)
assert M.importance("Soft layer") == 1.0 and M.importance("Melody") == 3.0
assert M.eq_band_for(400, "eq_cut") == 2 and M.eq_band_for(3500, "eq_cut") == 5 and M.eq_band_for(100, "highpass") == 0
assert M.eq_band_for(8000, "eq_cut") == 6 and M.eq_band_for(4500, "eq_cut") == 5   # band 7 (shelf) for 5 kHz+
assert M.propose({1: {"name": "x", "lufs": None, "bands_db": {}}}) == []
print("mix ok")

# ---- orchestration with a fake FL: check (solo each track), apply, revert
import tempfile
tmp = tempfile.mkdtemp(); os.environ["LOCALAPPDATA"] = tmp; os.environ["FLH_ROOTS"] = tmp
import fl_headless_mcp as m
vol = {0: 0.0, 5: 0.0, 7: -12.0}; solo = {"t": None}; params = {}
def fake_call(cmd, timeout=15.0, **a):
    if cmd == "mixer_set":
        if a.get("solo") is not None: solo["t"] = a["track"] if a["solo"] else None
        if a.get("volume_db") is not None: vol[a["track"]] = a["volume_db"]
        if a.get("volume") is not None: vol[a["track"]] = round(m._fader_db(a["volume"]), 2)   # script v7: 0..1
        return {"track": a["track"], "volume_db": vol.get(a["track"], 0.0), "volume": m._fader(vol.get(a["track"], 0.0))}
    if cmd == "plugin_set":
        params[(a["index"], a["param"])] = a["value"]; return {}
    raise AssertionError(cmd)
m.BRIDGE.call = fake_call
m.SET_VERIFY_S = 0.0
m.fl_state = lambda: {"channels": [{"plugin": "x", "insert": 5}, {"plugin": "y", "insert": 7}],
                      "mixer": [{"index": 0, "name": "Master", "volume_db": vol[0], "effects": []},
                                {"index": 5, "name": "Melody", "volume_db": vol[5], "effects": []},
                                {"index": 7, "name": "Strings", "volume_db": vol[7],
                                 "effects": [{"slot": 0, "name": "Fruity parametric EQ 2"}]}]}
meas = {None: tr("mix", -18), 5: tr("Melody", -20, mid=-4), 7: tr("Strings", -21, low_mid=-3, bass=-5)}
m._listen_measure = lambda a, b, c: dict(meas[solo["t"]], true_peak_dbfs=-3.0)
m._open_project_guess = lambda: (None, "")
m.fl_plugin_params = lambda t, s: {"params": [{"param": 2, "value": 0.5}, {"param": 9, "value": 0.39}]}
set_values = []
m.fl_plugin_set_value = lambda t, prm, v, s: set_values.append((t, prm, v))
r = m.fl_mix_check(1, 9)
fid = [p["id"] for p in r["proposals"] if p["action"] == "fader"]
eid = [p["id"] for p in r["proposals"] if p["action"] == "eq_cut" and p["track"] == 7]
assert fid and eid and solo["t"] is None, r
a = m.fl_mix_apply(ids=fid + eid[:1])
assert vol[7] < -12.0 and set_values, (vol, set_values)
m.fl_mix_apply(revert=True)
assert vol[7] == -12.0 and params and all(k[0] == 7 for k in params), (vol, params)
print("mix apply ok")

# a part already far under the lead gets no EQ dip (it would only bury it further)
quiet = {5: tr("Melody", -24, presence=-30), 7: tr("Strings", -37, presence=-17)}
assert not [p for p in M.propose(quiet) if p["action"] == "eq_cut"], M.propose(quiet)
print("quiet ok")
