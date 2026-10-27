"""flh_mixeng fl_room ("one room"): families + classical seating from names, the pure plan (tempo-derived decay and
pre-delay, sends by stage distance, distance darkening, piano dry and front, genre-aware styles, user pans kept),
and apply with a fake FL: buses on free inserts (named, reverb added in one project edit, 100% wet), sends, PEQ2
high shelves, pans, journal record, before/after verification, revert (sends unrouted, pans/names back).
Run: python3 tests/test_mixeng_room.py"""
import math, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_mixeng as X  # noqa: E402
ns = vars(m)
flh_store.register(ns); X.register(ns)
assert "fl_room" in ns["TOOLS"] and callable(ns["room_plan"])

# ---------------------------------------------------------------- families, seating
fam = lambda *t: X.room_family(*t)[0]
assert fam("Grand Piano") == "piano" and fam("Violins 1") == "strings" and fam("Cellos") == "strings"
assert fam("French Horns") == "brass" and fam("Choir Aah") == "choir" and fam("Timpani") == "timpani"
assert fam("Flute") == "woodwind" and fam("Warm Pad") == "pad" and fam("Lead Vox") == "vocal"
assert fam("Kick") is None and fam("808") is None and fam("Riser FX") is None and fam("Taiko Drums") == "timpani"
assert "kept out" in X.room_family("Kick")[1] and "unknown" in X.room_family("Insert 4")[1]
assert X.seat_pan("Violins 1") == -0.5 and X.seat_pan("Cellos") == 0.35 and X.seat_pan("Low Strings") == 0.5
assert X.seat_pan("Horns") == -0.35 and X.seat_pan("Strings") is None and X.seat_pan("Piano") is None

# ---------------------------------------------------------------- the plan (pure)
rows = [{"track": 1, "name": "Piano", "family": "piano", "pan": 0.0},
        {"track": 2, "name": "Violins 1", "family": "strings", "pan": 0.0},
        {"track": 3, "name": "Cellos", "family": "strings", "pan": 0.6},          # the user panned it: kept
        {"track": 4, "name": "Horns", "family": "brass", "pan": 0.0},
        {"track": 5, "name": "Kick", "family": None, "why": "kick"}]
pl = X.room_plan(rows, "hall", "cinematic", 100)
tail = pl["bus"]["tail"]["controls"]
assert tail["decay_s"] == 2.7 and tail["predelay_ms"] == 38 and tail["wet_pct"] == 100 and tail["dry_pct"] == 0, tail
assert pl["bus"]["er"]["controls"]["early_pct"] == 100 and pl["orchestral"]
t = {x["name"]: x for x in pl["tracks"]}
assert t["Piano"]["distance"] == "front" and t["Piano"]["send_db"] == -16 and "shelf" not in t["Piano"]
assert "pan_to" not in t["Piano"]                                     # the piano stays where it is (front)
assert t["Violins 1"]["send_db"] == -9 and t["Violins 1"]["pan_to"] == -0.5 and t["Violins 1"]["shelf"]["gain_db"] == -1.5
assert t["Horns"]["distance"] == "back" and t["Horns"]["send_db"] == -6 and t["Horns"]["shelf"] == {"hz": 7000, "gain_db": -3.0}
assert t["Horns"]["send_db"] > t["Violins 1"]["send_db"] > t["Piano"]["send_db"]       # farther = wetter
assert "pan_to" not in t["Cellos"] and "yours" in t["Cellos"]["pan_kept"]
assert pl["skipped"][0]["name"] == "Kick"
# tempo: slow songs clamp the decay, fast ones too
assert X.room_plan(rows, "hall", "cinematic", 40)["bus"]["tail"]["controls"]["decay_s"] == 3.2
assert X.room_plan(rows, "hall", "cinematic", 200)["bus"]["tail"]["controls"]["decay_s"] == 1.8
# studio: one bus, no darkening, quieter sends; pop offsets them further; no seating in a non-orchestral pop layer
ps = X.room_plan([rows[0], {"track": 6, "name": "Pad", "family": "pad", "pan": 0.0}], "studio", "pop", 120)
assert "er" not in ps["bus"] and not ps["orchestral"] and all("shelf" not in x for x in ps["tracks"])
assert {x["name"]: x["send_db"] for x in ps["tracks"]} == {"Piano": -24.0, "Pad": -17.0}, ps["tracks"]
ops = X.room_plan(rows, "hall", "cinematic", 100, buses={"tail": 8, "er": 9, "tail_add": True, "er_add": True})["ops"]
kinds = [o["op"] for o in ops]
assert kinds[:4] == ["name", "plugin", "name", "plugin"] and kinds.count("send") == 8 and kinds.count("pan") == 2
assert kinds.count("eq") == 3 and all(o["type"] == "High shelf" for o in ops if o["op"] == "eq")
print("plan ok")

# ---------------------------------------------------------------- fake FL
MAPS = {p: X.plugin_map(p) for p in ("Fruity Reeverb 2", "Fruity Parametric EQ 2")}
FX, MIX, presets_loaded = {}, {}, []
def disp(e, v):
    if e.get("curve") == "enum":
        o = e["options"]; return o[int(round(v * (len(o) - 1)))]
    val = X.from_norm(e, v); u = e.get("unit")
    return {"Hz": f"{val:.0f} Hz", "ms": f"{val:.1f} ms", "s": f"{val:.2f} s", "%": f"{val:.1f}%", "x": f"{val:.2f}x",
            "dB": f"{val:+.1f} dB", "oct": f"{val:.2f} oct"}.get(u, f"{val:.2f}")
def new_fx(plugin):
    mp = MAPS[plugin]; vals = {}
    for k, e in mp["controls"].items():
        vals[e["index"]] = 0.5
        if k == "main_level_db":
            vals[e["index"]] = X.to_norm(e, 0)
        elif plugin.startswith("Fruity Parametric"):
            b = int(k[4]) - 1
            if k.endswith("_gain_db"): vals[e["index"]] = X.to_norm(e, 0)
            if k.endswith("_freq_hz"): vals[e["index"]] = X.to_norm(e, mp["band_defaults_hz"][b])
            if k.endswith("_type"): vals[e["index"]] = X.to_norm(e, mp["band_default_type"][b])
    return {"plugin": plugin, "vals": vals}
for t, n in ((0, "Master"), (1, "Piano"), (2, "Violins 1"), (3, "Cellos"), (4, "Horns"), (5, "Kick"), (6, "Choir")):
    MIX[t] = {"name": n, "pan": 0.6 if n == "Cellos" else 0.0, "sends": {}}
for t in range(7, 16):
    MIX[t] = {"name": f"Insert {t}", "pan": 0.0, "sends": {}}
FX[(7, 0)] = new_fx("Fruity Parametric EQ 2")                       # a user's EQ on a free-looking insert: not free
def params_of(t, s):
    f = FX[(t, s)]; mp = MAPS[f["plugin"]]
    return [{"param": e["index"], "name": e["fl_name"], "value": f["vals"][e["index"]], "text": disp(e, f["vals"][e["index"]])}
            for e in mp["controls"].values()]
def entry(t, s, p):
    return next(e for e in MAPS[FX[(t, s)]["plugin"]]["controls"].values() if e["index"] == p)
def mix_reply(t):
    x = MIX[t]
    return {"track": t, "name": x["name"], "pan": x["pan"], "volume_db": 0.0,
            "routes_to": sorted(x["sends"]), "sends": [{"to": d, "level": lv} for d, lv in sorted(x["sends"].items())]}
def fake_call(cmd, timeout=15.0, **a):
    if cmd == "state":
        return {"playing": False}
    if cmd == "plugin_params":
        if (a["index"], a["slot"]) not in FX:
            raise RuntimeError("no plugin")
        return {"plugin": FX[(a["index"], a["slot"])]["plugin"], "params": params_of(a["index"], a["slot"])}
    if cmd == "plugin_set":
        t, s = a["index"], a["slot"]; FX[(t, s)]["vals"][a["param"]] = max(0.0, min(1.0, a["value"]))
        return {"param": a["param"], "text": disp(entry(t, s, a["param"]), FX[(t, s)]["vals"][a["param"]])}
    if cmd == "mixer_set":
        t = a["track"]; x = MIX[t]
        if a.get("name") is not None: x["name"] = a["name"]
        if a.get("pan") is not None: x["pan"] = a["pan"]
        if a.get("unroute"): [x["sends"].pop(int(d), None) for d in str(a["unroute"]).split(",")]
        if a.get("send_levels"):
            for part in a["send_levels"].split(","):
                d, lv = part.split(":"); x["sends"][int(d)] = float(lv)
        return mix_reply(t)
    if cmd == "mixer_info":
        want = range(0, 16) if a["tracks"] == "all" else [int(a["tracks"])]
        return {"tracks": [{"index": t, "name": MIX[t]["name"], "used": 1 <= t <= 6, "muted": False,
                            "effects": [FX[k]["plugin"] for k in sorted(FX) if k[0] == t]} for t in want]}
    raise AssertionError(cmd)
m.BRIDGE.call = fake_call
CH = [("Grand Piano", 1), ("Violins 1", 2), ("Cellos", 3), ("Horns", 4), ("Kick", 5), ("Choir", 6)]
def state():
    return {"tempo": 100.0, "channels": [{"index": i, "name": n, "plugin": "FLEX", "insert": t} for i, (n, t) in enumerate(CH)],
            "mixer": [{"index": t, "name": MIX[t]["name"], "pan": MIX[t]["pan"],
                       "effects": [{"slot": k[1], "name": FX[k]["plugin"]} for k in sorted(FX) if k[0] == t]}
                      for t in range(0, 7)]}
ns["fl_state"] = state; m.fl_state = state
proj = os.path.join(tmp, "Score.flp"); open(proj, "wb").write(b"")
flh_store.journal_update(proj, set={"genre": "cinematic"})
ns["_open_project_guess"] = lambda: (proj, "Score")
ns["fl_running"] = lambda: True
ns["fl_find_presets"] = lambda plug, limit=20: {"presets": [f"C:\\Plugin database\\Effects\\Fruity\\{plug}.fst"]}
def load_plugin(path, out, pre, track=None, slot=None, channel=None):
    plug = os.path.splitext(pre.split("\\")[-1])[0]; presets_loaded.append((track, slot, plug))
    FX[(track, slot)] = new_fx(plug); return {"ok": True}
ns["flp_load_plugin"] = load_plugin
edits = []
ns["_with_open_project"] = lambda p, o, fn: (edits.append(p), fn())[1]
ns["plugin_ownership"] = lambda name: {"status": "trial", "why": "fruity edition"}
listened = []
def listen(a, b, secs):
    listened.append(secs)
    return {"lufs": -16.0, "width_db": -14.0 if len(listened) == 1 else -10.5, "bands_db": {"sub": -20, "bass": -9,
            "low_mid": -6, "mid": -6, "presence": -11, "air": -16}}
ns["_listen_measure"] = listen

# ---------------------------------------------------------------- proposal (nothing changes) -> apply -> revert
pr = X.room()
assert pr["style"] == "hall" and pr["genre"] == "cinematic" and pr["bpm"] == 100.0 and "applied" not in pr, pr
assert pr["proposal"]["bus"]["tail"]["track"] == 8 and pr["proposal"]["bus"]["er"]["track"] == 9, pr["proposal"]["bus"]
assert pr["reverb"]["plugin"] == "Fruity Reeverb 2" and not presets_loaded and MIX[2]["sends"] == {}
assert [s["name"] for s in pr["proposal"]["skipped"]] == ["Kick"]
ap = X.room(apply=True)
assert len(edits) == 1, "buses' reverbs + far tracks' EQs added in ONE project edit"
assert sorted(presets_loaded) == [(2, 0, "Fruity Parametric EQ 2"), (3, 0, "Fruity Parametric EQ 2"),
                                  (4, 0, "Fruity Parametric EQ 2"), (6, 0, "Fruity Parametric EQ 2"),
                                  (8, 0, "Fruity Reeverb 2"), (9, 0, "Fruity Reeverb 2")], presets_loaded
assert MIX[8]["name"] == "Room" and MIX[9]["name"] == "Room ER"
got = X.plugin_get_named(8, 0)["controls"]
assert abs(got["wet_pct"]["value"] - 100) < 1 and abs(got["dry_pct"]["value"]) < 1, got
assert abs(got["decay_s"]["value"] - 2700) < 100 and abs(got["predelay_ms"]["value"] - 38) < 2, got
lv = {t: MIX[t]["sends"] for t in (1, 2, 3, 4, 6)}
assert all(set(v) == {8, 9} for v in lv.values()) and 5 not in [t for t in MIX if MIX[t]["sends"]], lv
assert lv[1][8] < lv[2][8] < lv[4][8], lv                              # piano driest, horns wettest
assert MIX[2]["pan"] == -0.5 and MIX[4]["pan"] == -0.35 and MIX[3]["pan"] == 0.6 and MIX[1]["pan"] == 0.0
shelf = X.plugin_get_named(4, 0)["controls"]
assert shelf["band7_type"]["value"] == "High shelf" and abs(shelf["band7_gain_db"]["value"] + 3.0) < 0.2, shelf
assert ap["verify"]["settings"].split("/")[0] == ap["verify"]["settings"].split("/")[1].split()[0], ap["verify"]
assert ap["verify"]["sound"]["results"][0]["status"] == "achieved" and listened == [8.0, 8.0], ap["verify"]
jr = flh_store.journal_get(proj)
assert jr["room"]["buses"] == {"tail": 8, "er": 9} and jr["room"]["style"] == "hall" and jr["mix_log"][-1]["by"] == "room: hall"
assert jr["room"]["tracks"]["4"]["distance"] == "back"
# a second run reuses the buses (named Room / Room ER, reverb already there): no project edit
ap2 = X.room(apply=True, style="chamber", verify=False)
assert len(edits) == 1 and ap2["proposal"]["bus"]["tail"]["reused"] and MIX[8]["name"] == "Room", ap2["proposal"]["bus"]
assert abs(X.plugin_get_named(8, 0)["controls"]["decay_s"]["value"] - 1500) < 100
rv = X.room(revert_last=True)
assert rv["reverted"] > 0 and all(not MIX[t]["sends"] for t in range(1, 7)), {t: MIX[t]["sends"] for t in MIX}
assert MIX[2]["pan"] == 0.0 and MIX[4]["pan"] == 0.0 and MIX[8]["name"] == "Insert 8" and MIX[3]["pan"] == 0.6
# genre-aware: EDM gets no room unless asked; tracks={...} picks and names families
ns["fl_running"] = lambda: False
assert X.room(project=proj, genre="techno")["proposal"] is None
ns["flp_list"] = lambda p: {"channels": [{"index": 0, "name": "Synth A", "plugin": "Sytrus", "insert": 3}]}
pt = X.room(project=proj, genre="techno", style="studio", tracks={3: "strings"})
assert pt["proposal"]["tracks"][0]["family"] == "strings" and "open the project in FL" in pt["next"], pt
try:
    X.room(project=proj, tracks={3: "kazoo"}); raise SystemExit("bad family must fail")
except ValueError:
    pass
# Convolver: only where owned AND its map can set pre-delay / decay
ns["plugin_ownership"] = lambda name: {"status": True, "why": "producer"}
assert X._reverb_choice("auto", "hall")[0] == "Fruity Reeverb 2" and "convolver" in X._reverb_choice("auto", "hall")[1]
print("ok")
