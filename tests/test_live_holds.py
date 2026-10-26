"""Live sets that must hold (PC 2026-10-23): a fader driven by an automation clip (fl_mixer_set fails and names the
clip, scale_clip / insert_gain alternatives, a null read-back, the bridge's _fit puts the fader back), a plugin knob
whose value didn't stick (fl_plugin_set_value reads back later, retries once, never reports a wrong value), the mix
engineer's effect-adding path on a slow-loading project (no bridge calls while FL's title says 'Opening: ...', the
wait follows the measured load cost, FL closing stops the job), and live notes / reload mode put back after an
unattended pc_check / sweep. Simulated FL (tests/test_e2e.py FakeFL + VClock). Run: python3 tests/test_live_holds.py"""
import importlib.util, os, shutil, struct, sys, types
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_e2e as E  # noqa: E402  (env, the server, FakeFL, VClock)

m, ns = E.m, E.ns
import flh_store, flh_mixeng as X, flh_pccheck as PC, flh_sweep as SW  # noqa: E402
for mod in (flh_store, X, PC, SW):
    if getattr(mod, "H", None) is not ns:
        mod.register(ns)
E.VCLOCK.install()
time = E.VCLOCK
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg); print("FAIL", msg)
    return cond


def raises(fn, *words):
    try:
        fn()
    except Exception as e:                                        # noqa: BLE001
        s = str(e)
        return all(w.lower() in s.lower() for w in words), s
    return False, "no error"


def auto_points(path, ch):
    d = m._load(path); ev = m._events(d); k = m._automation_events(d, ev)[ch]
    return [v for _b, v, *_ in m._auto_points(m._payload(d, ev[k]))[0]]


# ---------------------------------------------------------------- a project whose mixer track 3 volume is automated
proj = os.path.join(E.ROOTS, "Orchestra.flp")
shutil.copy(os.path.join(E.CORPUS, "base.flp"), proj)
r = m.flp_add_automation(proj, proj, [{"bar": 1, "db": -12}, {"bar": 3, "db": -3}, {"bar": 5, "db": 0}], track=3,
                         name="Timpani level")
CH = r["channel"]
clips = ns["_driven_by"](("mixer_volume", 3), proj)
check(clips and clips[0]["name"] == "Timpani level" and clips[0]["placed"], f"automation found: {clips}")
check(ns["_driven_by"](("mixer_volume", 4), proj) == [], "track 4 isn't automated")

FL = E.FakeFL().install(); FL.fl_open(proj)
m.SET_VERIFY_S = 0.05
calls = []
fader = {"auto": {3: -13.96}, "pos": {}, "script": 7, "null": set(), "drift": {}}
orig_bridge = FL.bridge
P = m._fader


def bridge(cmd, timeout=15.0, **a):
    """FL as the v7 bridge script reports it: 'volume' = the fader's 0..1 position, 'volume_fit' on a set."""
    calls.append((cmd, dict(a)))
    if cmd == "mixer_set":
        t = a.get("track"); v = a.get("volume")
        if v is not None and fader["script"] < 7:
            raise RuntimeError("FL: TypeError: cmd_mixer_set() got an unexpected keyword argument 'volume'")
        cur = lambda: (P(fader["auto"][t]) if t in fader["auto"] else
                       None if t in fader["null"] else fader["drift"].get(t, fader["pos"].get(t, 0.8)))
        rep = {"track": t, "name": f"Insert {t}", "pan": 0.0, "muted": False, "sends": []}
        if v is not None:
            before = cur()
            if t not in fader["auto"] and t not in fader["null"]:
                fader["pos"][t] = v                               # automation holds the fader; else it moves
            now = v if t in fader["drift"] else cur()             # drift: moves back only after the set
            rep["volume_fit"] = {"set_to": v, "before": before, "read_now": now}
            rep["volume"] = now
        else:
            rep["volume"] = cur()
        rep["volume_db"] = None if rep["volume"] is None else round(m._fader_db(rep["volume"]), 2)
        return rep
    return orig_bridge(cmd, timeout=timeout, **a)


m.BRIDGE.call = bridge

# ---- 1. an automated fader: no fader set, the clip named, never success
calls.clear()
ok, msg = raises(lambda: m.fl_mixer_set(3, volume_db=-17.5), "NOT set", "Timpani level", "scale_clip", "insert_gain")
check(ok, f"automated fader reported plainly: {msg}")
check(all(c[1].get("volume") is None for c in calls if c[0] == "mixer_set"), f"no fader set: {calls}")
# the same track without the file knowing (a clip added since the last save): the later read-back catches it
os.rename(proj, proj + ".bak")
try:
    ok, msg = raises(lambda: m.fl_mixer_set(3, volume_db=-17), "NOT set", "didn't move", "-13.96")
    check(ok, f"read-back catches it without the file: {msg}")
    fader["script"] = 6                                           # older bridge script: refused, nothing sent
    calls.clear()
    ok, msg = raises(lambda: m.fl_mixer_set(4, volume_db=-17.5), "NOT set", "update_script")
    check(ok and 4 not in fader["pos"], f"old script: refused + update hint: {msg}")
    fader["script"] = 7
finally:
    os.rename(proj + ".bak", proj)
# ---- a null read-back (getTrackVolume gave no number)
fader["null"].add(5)
ok, msg = raises(lambda: m.fl_mixer_set(5, volume_db=-6), "NOT set", "isn't a number")
check(ok, f"null read-back refused: {msg}")
# ---- a plain fader: held, checked in a later read, exact on FL's curve
calls.clear()
r = m.fl_mixer_set(4, volume_db=-6, name="Pad")
check(r.get("volume_held") is True and r.get("volume_db") == -6.0, f"plain set held: {r}")
check(abs(fader["pos"][4] - m._fader(-6)) < 1e-9, "set at FL's curve position")
check(sum(1 for c in calls if c[0] == "mixer_set" and c[1].get("track") == 4 and c[1].get("volume") is None) >= 1,
      "a later read-back after the set")
# ---- something moves it after the set (automation while playing)
fader["drift"][6] = m._fader(-2.0); fader["pos"][6] = m._fader(-4.0)
ok, msg = raises(lambda: m.fl_mixer_set(6, volume_db=-9), "NOT set", "something moves it")
check(ok, f"a set that doesn't stay is refused: {msg}")
fader["drift"].pop(6)

# ---- 1b. scale_clip: every point of the clip moved by the difference (asked - what the fader reads), FL reloaded
before = auto_points(proj, CH)
n_reload = len(FL.reloads)
r = m.fl_mixer_set(3, volume_db=-17, if_automated="scale_clip")
after = auto_points(proj, CH)
delta = r.get("delta_db")
check(r.get("volume_via") == "automation clip scaled" and abs(delta - (-17 + 13.96)) < 1e-6, f"scale_clip: {r}")
check(len(after) == len(before) and all(abs(m._fader_db(a) - (m._fader_db(b) + delta)) < 0.05
                                         for a, b in zip(after, before) if b > 0), f"points moved: {before} {after}")
check(len(FL.reloads) == n_reload + 1, "the open project was reloaded once")
ok, msg = raises(lambda: X.flp_scale_automation(proj, proj, ["Timpani level"], db=99), "between")
check(ok, f"silly db refused: {msg}")
d = m._load(proj); names = {nm: c for c, (k, nm) in m._clip_names(d, m._events(d)).items() if k == "automation"}
if names:
    other = m.flp_add_automation(proj, proj, [{"bar": 1, "pan": -1}, {"bar": 2, "pan": 1}], track=3, param="pan",
                                 name="Pan sweep")["channel"]
    ok, msg = raises(lambda: X.flp_scale_automation(proj, proj, [other], db=-3), "volume")
    check(ok, f"a pan clip isn't scaled in dB: {msg}")

# ---- 1c. insert_gain: Parametric EQ 2 'Main level' moved by the difference
got = {}
X_state_for, X_get, X_set = X._state_for, X.plugin_get_named, X.plugin_set_named
X._state_for = lambda tracks: {"mixer": [{"index": 3, "name": "Timpani", "effects": [
    {"slot": 2, "name": "Fruity Parametric EQ 2"}]}], "light": True}
X.plugin_get_named = lambda t, s, p=None: {"controls": {"main_level_db": {"value": 0.0}}}


def peq_set(t, s, p, vals):
    got.update(track=t, slot=s, vals=vals)
    return {"ok": True, "set": [{"control": "main_level_db", "text": f"{vals['main_level_db']:+.1f} dB", "ok": True}],
            "undo": [{"track": t, "slot": s, "param": 35, "before": 0.5}], "skipped": []}


X.plugin_set_named = peq_set
r = m.fl_mixer_set(3, volume_db=-17, if_automated="insert_gain")
check(r.get("volume_via") == "insert gain" and got.get("slot") == 2 and
      abs(got["vals"]["main_level_db"] - (-17 + 13.96)) < 1e-6, f"insert_gain: {r} {got}")
X.plugin_get_named = lambda t, s, p=None: {"controls": {"main_level_db": {"value": -16.0}}}
ok, msg = raises(lambda: m.fl_mixer_set(3, volume_db=-17, if_automated="insert_gain"), "range")
check(ok, f"insert gain out of range refused: {msg}")
X._state_for, X.plugin_get_named, X.plugin_set_named = X_state_for, X_get, X_set

# ---- 1d. the bridge script's curves (the full FL 2026 round trip is in tests/test_fader_fl26.py)
for name in ("channels", "device", "general", "midi", "mixer", "transport"):
    sys.modules.setdefault(name, types.ModuleType(name))
spec = importlib.util.spec_from_file_location("flh_bridge_script", os.path.join(E.REPO, "fl_script",
                                                                                 "device_flheadless.py"))
B = importlib.util.module_from_spec(spec); spec.loader.exec_module(B)
check(B.SCRIPT_VERSION >= 7 and m._script_version_shipped() == B.SCRIPT_VERSION, "bridge script bumped")
check(all(abs(B.fader_pos(d) - m._fader(d)) < 1e-9 for d in (-60, -17.5, -6, 0, 3, 5.5)), "script and server agree")

# ---------------------------------------------------------------- 2. a plugin knob that doesn't keep its value
knob = {"value": 0.5, "pending": None, "holds_after": 1, "sets": 0, "script": 6}


def text_of(v):
    return f"{-60 + 60 * v:.1f}dB"


def pbridge(cmd, timeout=15.0, **a):
    calls.append((cmd, dict(a)))
    if cmd == "plugin_set_value":
        pos_ = (a["target"] + 60) / 60.0; knob["sets"] += 1
        if knob["sets"] > knob["holds_after"]:
            knob["value"] = pos_
        r = {"param": a["param"], "name": "Wet level", "text": text_of(pos_), "value": 0.5, "before": 0.5}
        if knob["script"] >= 6:
            r["set_to"] = round(pos_, 5)
        return r
    if cmd == "plugin_set":
        knob["sets"] += 1
        if knob["sets"] > knob["holds_after"]:
            knob["value"] = a["value"]
        return {"param": a["param"], "value": knob["value"], "text": text_of(a["value"])}
    if cmd == "plugin_get":
        if knob["script"] < 6:
            raise RuntimeError("unknown command plugin_get")
        return {"param": a["param"], "name": "Wet level", "value": knob["value"], "text": "-38.0dB"}
    if cmd == "plugin_params":
        return {"plugin": "Fruity Convolver", "params": [{"param": 1, "name": "Wet level", "value": knob["value"],
                                                          "text": text_of(knob["value"])}]}
    return bridge(cmd, timeout=timeout, **a)


m.BRIDGE.call = pbridge
r = m.fl_plugin_set_value(0, 1, -38, slot=0)                      # first set doesn't stick, the retry does
check(r.get("held") is True and abs(r["value"] - 22 / 60) < 1e-3 and "second" in str(r.get("note")), f"retry: {r}")
knob.update(value=0.5, sets=0, holds_after=99)                    # never sticks: an honest failure
ok, msg = raises(lambda: m.fl_plugin_set_value(0, 1, -38, slot=0), "NOT set", "value")
check(ok, f"a knob that never keeps the value: {msg}")
knob.update(value=0.5, sets=0, holds_after=0, script=5)           # older script: plugin_params fallback
r = m.fl_plugin_set_value(0, 1, -40, slot=0)
check(r.get("held") is True and "older bridge script" in str(r.get("held_note")), f"old script fallback: {r}")
# a placed automation clip on that knob is named
m.flp_add_automation(proj, proj, [{"bar": 1, "value": 0.2}, {"bar": 4, "value": 0.6}], track=0, slot=0,
                     plugin_param=1, name="Verb wet ride")
knob.update(value=0.5, sets=0, holds_after=99, script=6)
ok, msg = raises(lambda: m.fl_plugin_set_value(0, 1, -38, slot=0), "NOT set", "Verb wet ride")
check(ok, f"automation clip on the knob named: {msg}")
m.BRIDGE.call = bridge

# ---------------------------------------------------------------- 3. mix engineer on a slow-loading project
load = {"until": 0.0, "dead_at": None, "fx": set(), "during_load": 0}
ns["_note_load_cost"](proj, 120.0, "test")                       # a heavy song: 2 minutes to load
X.FL_POLL_S = (0.5, 1.0, 2.0, 4.0)


def title():
    return f"Opening: Kontakt - FL Studio 2026" if time.time() < load["until"] else "Orchestra - FL Studio 2026"


def is_running():
    if load["dead_at"] is not None and time.time() >= load["dead_at"]:
        FL.running = False
    return FL.running


ns["_fl_loading_title"] = lambda: title() if E.ns["LOADING_TITLE_RE"].match(title()) else None
ns["fl_running"] = is_running
ns["fl_find_presets"] = lambda plug, limit=20: {"presets": [f"C:/FL/Plugin database/Effects/{plug}.fst"]}


def fake_load_plugin(project, output, preset, track=None, slot=None):
    load["fx"].add((track, slot, os.path.basename(preset)[:-4]))
    return {"output": output}


ns["flp_load_plugin"] = fake_load_plugin
ns["_with_open_project"] = lambda p, o, fn: (fn(), load.__setitem__("until", time.time() + 100.0))[0]


def mbridge(cmd, timeout=15.0, **a):
    if not is_running():
        raise TimeoutError("no reply from FL")
    if time.time() < load["until"]:
        load["during_load"] += 1
        raise TimeoutError(f"no reply from FL within {timeout}s")
    if cmd == "mixer_info":
        ts = [int(x) for x in str(a.get("tracks")).split(",")]
        rows = []
        for t in ts:
            fx = sorted((s, n) for tt, s, n in load["fx"] if tt == t)
            rows.append({"index": t, "name": f"Insert {t}", "effects": [n for _s, n in fx], "slots": [s for s, _n in fx]})
        return {"tracks": rows}
    if cmd == "ping":
        return {"pong": True}
    return bridge(cmd, timeout=timeout, **a)


m.BRIDGE.call = mbridge
t0 = time.time()
st = X._add_effects([(3, 1, "Fruity Parametric EQ 2"), (4, 0, "Fruity Compressor")])
took = time.time() - t0
check(load["during_load"] == 0, f"no bridge call while FL's title says it's loading ({load['during_load']})")
check(took >= 100 and X._has_effect(st, 3, "Fruity Parametric EQ 2"), f"waited for the load ({took:.0f} s)")
# FL already loading when the moves start: wait first, nothing sent meanwhile
load.update(until=time.time() + 30.0, during_load=0)
r = X.apply_ops([{"op": "pan", "track": 3, "pan": -0.3}])
check(load["during_load"] == 0 and r["done"], f"apply_ops waits for a loading FL: {r} {load['during_load']}")
# FL dies while the effects load: the job says so and stops
load.update(fx=set(), during_load=0)
ns["_with_open_project"] = lambda p, o, fn: (fn(), load.__setitem__("until", time.time() + 100.0),
                                             load.__setitem__("dead_at", time.time() + 20.0))[0]
ok, msg = raises(lambda: X._add_effects([(5, 0, "Fruity Parametric EQ 2")]), "stopped running")
check(ok, f"FL closing stops the job: {msg}")
check(isinstance(X.FLGone("x"), RuntimeError), "FLGone is an error")
# moves stop when FL goes away mid-batch
FL.running = True; load.update(dead_at=None, until=0.0)
n = [0]


def dying(cmd, timeout=15.0, **a):
    if cmd == "mixer_set":
        n[0] += 1
        if n[0] >= 2:
            FL.running = False
            raise TimeoutError("no reply from FL within 19.8s")
    return mbridge(cmd, timeout=timeout, **a)


m.BRIDGE.call = dying
r = X.apply_ops([{"op": "pan", "track": 3, "pan": -0.3}, {"op": "pan", "track": 4, "pan": 0.3},
                 {"op": "pan", "track": 5, "pan": 0.1}])
check(r.get("fl_gone") and any("not tried" in s["why"] for s in r["skipped"]), f"stops when FL is gone: {r}")
# an automated fader is never moved by the engineer
FL.running = True; m.BRIDGE.call = mbridge
r = X.apply_ops([{"op": "fader", "track": 3, "db": -2}], project=proj)
check(not r["done"] and "Timpani level" in r["skipped"][0]["why"], f"engineer skips an automated fader: {r}")
# FL dies while the engineer measures the tracks: the pass says FL stopped, not a bare timeout
X_meas = X._live_meas


def meas_dies(*a, **k):
    FL.running = False
    raise TimeoutError("no reply from FL within 19.8s")


X._live_meas = meas_dies
ok, msg = raises(lambda: X.mix_engineer(goal="cleaner", project=proj), "stopped running", "measured")
check(ok, f"FL gone while measuring: {msg}")
X._live_meas = X_meas; FL.running = True
m.BRIDGE.call = bridge

# ---------------------------------------------------------------- 4. live notes / reload mode after an unattended run
ns["_state"]({"live_notes": {"ok": True, "t": 1}, "reload_mode": "immediate", "user_settings_before": None})


class Ctx:
    unattended = True
    notes = []

    def make_project(self, *a):
        return proj

    def open_in_fl(self, *a, **k):
        return {}

    def say(self, *a):
        pass

    def call(self, tool, **kw):
        assert tool == "fl_live_notes_probe"
        ns["_state"]({"live_notes": {"ok": False, "why": "probe failed"}})   # what the probe does on a failure
        return {"live_notes_enabled": False, "next": "run Tools > Scripting > fl_headless_notes once"}

    def ask_user(self, prompt, timeout_s=None):
        return None                                               # unattended


res = PC.chk_live_notes(Ctx())
check(res["status"] == "skip" and ns["_state"]()["live_notes"].get("ok") is True,
      f"a skipped live-notes check leaves live notes on: {res} {ns['_state']().get('live_notes')}")
# a run changes both (the probe, the lazy check), the end puts them back; a passed probe keeps live notes on
snap, own = PC.settings_save("pc_check")
ns["_state"]({"live_notes": {"ok": False}, "reload_mode": "lazy"})
back = PC.settings_restore(snap, clear=own)
st = ns["_state"]()
check(st["live_notes"].get("ok") is True and st["reload_mode"] == "immediate" and not st.get("user_settings_before"),
      f"run restores: {back} {st.get('live_notes')} {st.get('reload_mode')}")
snap, own = PC.settings_save("pc_check")
ns["_state"]({"live_notes": {"ok": True, "t": 99}})
PC._RUN["live_notes_proved"] = {"ok": True, "t": 99}
PC.settings_restore(snap, clear=own)
check(ns["_state"]()["live_notes"].get("t") == 99, "a passed probe keeps live notes on")
# a sweep cut off (server restart): the boot puts them back
ns["_state"]({"live_notes": {"ok": False}, "reload_mode": None, "user_settings_before": {
    "live_notes": {"ok": True, "t": 5}, "reload_mode": "auto", "by": "sweep", "t": 1}})
PC._RUN["live_notes_proved"] = None
PC._boot_restore_mode()
st = ns["_state"]()
check(st["live_notes"].get("t") == 5 and st["reload_mode"] == "auto" and not st.get("user_settings_before"),
      f"boot restores a cut-off run's settings: {st.get('live_notes')} {st.get('reload_mode')}")
# nested: pc_check inside a running sweep keeps the sweep's record
SW._RUN["running"] = True
try:
    snap_s, own_s = PC.settings_save("sweep")
    ns["_state"]({"live_notes": {"ok": False}})
    snap_p, own_p = PC.settings_save("pc_check")
    check(own_s and not own_p and snap_p["live_notes"].get("t") == 5, f"nested record kept: {snap_p}")
    PC.settings_restore(snap_p, clear=own_p)
    check(ns["_state"]().get("user_settings_before", {}).get("by") == "sweep", "the sweep's record stays")
    PC.settings_restore(snap_s, clear=own_s)
finally:
    SW._RUN["running"] = False
check(ns["_state"]()["live_notes"].get("t") == 5 and not ns["_state"]().get("user_settings_before"),
      "sweep restores and clears")

FL.uninstall()
if FAILS:
    print(f"{len(FAILS)} failure(s)"); sys.exit(1)
print("ok")
