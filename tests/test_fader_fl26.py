"""Live volumes on FL 2026 (PC 2026-10-23), the REAL bridge script inside a simulated FL 2026 whose mixer applies
setTrackVolume only after the script's callback (a read-back inside the callback shows the old value), with FL's
exact fader curve, an 18-track mixer (master + 16 inserts + the 'Current' track 17), a track driven by automation,
and channel volumes on FL's channel scale. The server talks to it through its real bridge client.
  - root cause: the old binary search over that read-back ran faders to an end (silent master, +5.59 dB tracks)
  - fl_mixer_set: one set at FL's curve position, checked in a later read; never an extreme; honest failures
  - fl_mix_engineer moves: undone when > 1.5 dB off, master never below -12 dB; fl_mix_apply the same
  - fl_channel_set: FL's channel dB scale (-6 dB = 0.748), routing to the Current track refused, routing checked
  - Parametric EQ 2: a Q width set where FL shows the width as a %
Run: python3 tests/test_fader_fl26.py"""
import math
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="flh_fader26_")
os.environ["FLH_ROOTS"] = TMP
os.environ.setdefault("LOCALAPPDATA", os.path.join(TMP, "appdata"))
os.environ.setdefault("FLH_NO_WARM", "1")
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


# ---------------------------------------------------------------- FL 2026, simulated
N_TRACKS = 18                                                     # 0 master, 1..16 inserts, 17 = Current
fl_db = lambda v: float("-inf") if v <= 0 else 20 * math.log10((20 ** v - 1) / (20 ** 0.8 - 1))
ch_db = lambda v: float("-inf") if v <= 0 else 20 * math.log10((11 ** v - 1) / 10)
FL = {"vol": {t: 0.8 for t in range(N_TRACKS)}, "pending": {}, "visits": [], "auto": {}, "offset": {},
      "names": {}, "target": {i: i + 1 for i in range(12)}, "chvol": {i: 0.78125 for i in range(12)}, "lag": True}
FL["vol"][2] = 1.0                                                # a bass track at the fader's top (+5.59 dB)
FL["target"][11] = 16


def set_vol(t, v, pickup=0):
    FL["visits"].append((t, round(v, 6)))
    if FL["lag"]:
        FL["pending"][t] = v                                      # FL 2026: applied after the callback
    else:
        FL["vol"][t] = v


def idle():
    """FL after the script's callback: pending fader sets land, automation / controllers have their say."""
    for t, v in FL["pending"].items():
        off = FL["offset"].get(t)                                 # (amount, sets it still applies to)
        if off and off[1] > 0:
            FL["offset"][t] = (off[0], off[1] - 1); v += off[0]
        FL["vol"][t] = max(0.0, min(1.0, v))
    FL["pending"].clear()
    for t, v in FL["auto"].items():
        FL["vol"][t] = v


def get_vol(t, mode=0):
    v = FL["vol"][t]
    return fl_db(v) if mode else v


def mod(name, **kw):
    m_ = types.ModuleType(name); m_.__dict__.update(kw); sys.modules[name] = m_


sent = []
mod("device", getPortNumber=lambda: 10, dispatchReceiverCount=lambda: 0, midiOutSysex=lambda b: sent.append(bytes(b)))
mod("midi", SONGLENGTH_S=1, TN_LastIns=2, FPN_Preset=6, PIM_None=0)


def track_info(mode):
    return {0: 0, 1: 1, 2: N_TRACKS - 2, 3: 0}[mode]


def set_name(t, n):
    if t < N_TRACKS - 1:                                          # FL ignores a name on its Current track
        FL["names"][t] = n


mod("mixer", trackCount=lambda: N_TRACKS, getTrackInfo=track_info, getCurrentTempo=lambda: 120000,
    getTrackName=lambda t: FL["names"].get(t, "" if t == N_TRACKS - 1 else f"Insert {t}"), setTrackName=set_name,
    getTrackVolume=get_vol, setTrackVolume=set_vol, getTrackPan=lambda t: 0.0, setTrackPan=lambda t, p: None,
    isTrackMuted=lambda t: False, muteTrack=lambda t: None, getTrackColor=lambda t: 0x333333,
    setTrackColor=lambda t, c: None, getRouteSendActive=lambda a, b: False, afterRoutingChanged=lambda: None)


def set_target(i, t, g=False):
    FL["target"][i] = min(int(t), N_TRACKS - 2)                   # FL keeps a channel on a real insert


mod("channels", channelCount=lambda g=False: 12, getChannelName=lambda i, g=False: f"Ch {i}",
    getChannelVolume=lambda i, mode=False, g=False: ch_db(FL["chvol"][i]) if mode else FL["chvol"][i],
    setChannelVolume=lambda i, v, pick=0, g=False: FL["chvol"].__setitem__(i, v),
    getChannelPan=lambda i, g=False: 0.0, isChannelMuted=lambda i, g=False: False,
    getChannelColor=lambda i, g=False: 0x444444, getTargetFxTrack=lambda i, g=False: FL["target"][i],
    setTargetFxTrack=set_target)
mod("plugins", isValid=lambda i, s=-1, g=False: False)
mod("transport", isPlaying=lambda: False, getSongPos=lambda m=0: 0.0)
mod("general", getRecPPQ=lambda: 96)
for name in ("arrangement", "patterns", "playlist", "ui"):
    sys.modules.pop(name, None)

sys.path[:0] = [os.path.join(ROOT, "fl_script"), ROOT]
import device_flheadless as B  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
import flh_mixeng as X  # noqa: E402
if getattr(X, "H", None) is not ns:
    X.register(ns)


class Event:
    def __init__(self, b):
        self.sysex, self.handled = b, False


inbox = []


def send(data):
    B.OnSysEx(Event(data)); inbox.extend(sent); sent.clear()
    idle()                                                        # FL's own work after the callback


m.BRIDGE.open = lambda: None
m.BRIDGE._send = send
m.BRIDGE._drain = lambda: [inbox.pop(0) for _ in range(len(inbox))]
m._mailbox_alive = lambda *a, **k: False
m.SET_VERIFY_S = 0.0
m._open_project_guess = lambda *a, **k: (None, None)
db_now = lambda t: round(fl_db(FL["vol"][t]), 2)
extreme = lambda t: [v for tt, v in FL["visits"] if tt == t and v in (0.0, 1.0)]

# ---------------------------------------------------------------- 0. FL's curve: exact, both ways, both sides agree
for v, d in m.FADER_DB[1:]:
    check(abs(m._fader_db(v) - d) < (0.2 if d <= -60 else 0.02), f"curve meets FL's measured point {v} -> {d}")
check(all(abs(m._fader_db(m._fader(d)) - d) < 1e-9 for d in (-60, -24, -17.5, -1.8, 0, 3, 5.5)), "dB round trip")
check(m._fader(0) == 0.8 and m._fader(99) == 1.0 and m._fader(-999) == 0.0, "0 dB = 0.8, ends clamp")
check(round(m._fader_db(1.0), 2) == 5.59, "the fader's top reads +5.59 dB (what the PC showed)")
check(all(abs(B.fader_pos(d) - m._fader(d)) < 1e-12 for d in (-40, -6, 0, 4)), "script and server curves agree")
check(abs(m._chan_vol(-6) - 0.748) < 5e-4 and round(m._chan_db(0.78125), 2) == -5.18, "channel scale")

# ---------------------------------------------------------------- 1. root cause: the old search on FL 2026
FL["vol"][0] = 0.8; FL["visits"].clear()
lo, hi = 0.0, 1.0                                                 # the v5 bridge's _fit, in one callback
for _ in range(30):
    mid = (lo + hi) / 2; set_vol(0, mid)
    if get_vol(0, 1) < -1.8:
        lo = mid
    else:
        hi = mid
set_vol(0, (lo + hi) / 2); idle()
check(FL["vol"][0] < 1e-6, f"reproduced: the old search put the master at the bottom ({FL['vol'][0]})")
FL["vol"][5] = m._fader(-20.0)
lo, hi = 0.0, 1.0
for _ in range(30):
    mid = (lo + hi) / 2; set_vol(5, mid)
    if get_vol(5, 1) < -17.5:
        lo = mid
    else:
        hi = mid
set_vol(5, (lo + hi) / 2); idle()
check(db_now(5) == 5.59, f"reproduced: -17.5 asked read +5.59 ({db_now(5)})")
FL["vol"][0] = 0.8; FL["vol"][5] = m._fader(-20.0)

# ---------------------------------------------------------------- 2. fl_mixer_set on FL 2026
FL["visits"].clear()
r = m.fl_mixer_set(0, volume_db=-1.8)
check(r.get("volume_held") and r["volume_db"] == -1.8 and db_now(0) == -1.8, f"master -1.8: {r}")
check(not extreme(0) and len([1 for t, _ in FL["visits"] if t == 0]) == 1, f"one set, no search: {FL['visits']}")
r = m.fl_mixer_set(5, volume_db=-17.5)
check(r["volume_db"] == -17.5 and db_now(5) == -17.5 and not extreme(5), f"-17.5: {r}")
r = m.fl_mixer_set(2, volume_db=-6)
check(r["volume_db"] == -6.0 and db_now(2) == -6.0, f"bass from the top to -6: {r}")
r = m.fl_mixer_set(2, volume_db=-4)
check(r["volume_db"] == -4.0 and db_now(2) == -4.0, f"bass -4: {r}")
r = m.fl_mixer_set(0, reset=True)
check(r.get("volume_held") and r["volume_db"] == 0.0 and FL["vol"][0] == 0.8, f"reset reads 0 dB, not 5.59: {r}")
r = m.fl_mixer_set(3)
check(r.get("volume") == 0.8 and r.get("volume_db") == 0.0 and r.get("fl_db") == 0.0, f"plain read: {r}")
# a track automation holds (FL side; the saved file doesn't know): never an extreme, honest failure
FL["auto"][9] = m._fader(-13.96); idle(); FL["visits"].clear()
ok, msg = raises(lambda: m.fl_mixer_set(9, volume_db=-17), "NOT set", "-13.96", "insert_gain")
check(ok and not extreme(9) and all(abs(v - m._fader(-17)) < 1e-6 for t, v in FL["visits"] if t == 9),
      f"automated: {msg} {FL['visits']}")
FL["auto"].clear()
# FL's Current track and inserts
ok, msg = raises(lambda: m.fl_mixer_set(17, name="Strings"), "Current", "inserts 1-16")
check(ok and 17 not in FL["names"], f"Current track refused: {msg}")
check(m.fl_mixer_set(16, name="Strings")["name"] == "Strings", "the last insert takes a name")
ok, msg = raises(lambda: m.fl_mixer_set(4, route_to=[17]), "Current")
check(ok, f"routing to the Current track refused: {msg}")
r = m.fl_mixer_set(17)
check(r.get("current_track") is True, f"reading the Current track is fine: {r}")
# the reload path's 'mark changed' nudge: by position and back exactly; never with an older script's search
FL["vol"][1] = m._fader(-7.3); FL["visits"].clear()
check(m._mark_changed() is True and abs(FL["vol"][1] - m._fader(-7.3)) < 1e-6 and not extreme(1)
      and len([1 for t, _ in FL["visits"] if t == 1]) == 2, f"mark-changed nudge: {FL['visits']} {db_now(1)}")
real_call = m.BRIDGE.call
m.BRIDGE.call = lambda cmd, **a: {"track": a.get("track"), "volume_db": 0.0} if not set(a) - {"track", "timeout"} else \
    (_ for _ in ()).throw(AssertionError(f"older script: no volume set may go out: {a}"))
check(m._mark_changed() is False, "older script: no nudge")
m.BRIDGE.call = real_call
# live batches and the flp_mixer live route use the same one-set path
b = m.fl_batch([{"op": "mixer", "track": 6, "volume_db": -9}])
check(b["done"] == 1 and db_now(6) == -9.0, f"batch volume: {b} {db_now(6)}")

# ---------------------------------------------------------------- 3. mix engineer moves
X._fl_gone_check = lambda what: None
X._state_for = lambda tracks: {"mixer": [{"index": t, "effects": []} for t in tracks], "light": True}
ns["_driven_by"] = lambda target, project=None: []
FL["vol"][0] = 0.8
r = X.apply_ops([{"op": "fader", "track": 0, "db": -1.8}], project=os.path.join(TMP, "x.flp"))
check(r["done"] and r["done"][0]["to_db"] == -1.8 and db_now(0) == -1.8, f"engineer master -1.8: {r}")
r = X.apply_ops([{"op": "fader", "track": 0, "db": -20}], project=os.path.join(TMP, "x.flp"))
check(db_now(0) == -12.0 and r["done"] and "never goes lower" in r["done"][0].get("note", ""),
      f"master floor -12: {r} {db_now(0)}")
FL["vol"][7] = m._fader(-6.0); FL["offset"][7] = (m._fader(-7.0) - m._fader(-9.0), 2)   # +2 dB twice
r = X.apply_ops([{"op": "fader", "track": 7, "db": -3}], project=os.path.join(TMP, "x.flp"))
check(not r["done"] and "put back" in r["skipped"][0]["why"] and abs(db_now(7) + 6.0) < 0.05,
      f"> 1.5 dB off: undone and said: {r} {db_now(7)}")
FL["offset"].clear()
st = m._state().get("mix_engineer") or {}
r = X.revert()
check(FL["vol"][0] == 0.8, f"revert puts the master back exactly: {r} {db_now(0)}")
# fl_mix_apply (fl_mix_check proposals): the same floor and checks
FL["vol"][0] = 0.8
m._state({"mix_check": {"proposals": [{"id": 1, "track": 0, "action": "master", "db": -30}], "applied": []}})
r = m.fl_mix_apply()
check(r["applied"] == [1] and db_now(0) == -12.0, f"fl_mix_apply master floor: {r} {db_now(0)}")
m.fl_mix_apply(revert=True)
check(FL["vol"][0] == 0.8, f"fl_mix_apply revert exact: {FL['vol'][0]}")

# ---------------------------------------------------------------- 4. channels
r = m.fl_channel_set(6, volume_db=-6)
check(abs(r["volume"] - 0.748) < 5e-4 and r["volume_db"] == -6.0 and r["default_db"] == -5.18 and r["volume_held"],
      f"channel -6 dB on FL's channel scale: {r}")
ok, msg = raises(lambda: m.fl_channel_set(11, mixer_track=17), "Current", "inserts 1-16", "nothing was changed")
check(ok and FL["target"][11] == 16, f"routing to the Current track refused: {msg}")
check(m.fl_channel_set(11, mixer_track=13)["insert"] == 13, "routing to 13")
N_TRACKS = 22                                                     # FL with more inserts: 17 is a real insert
FL["vol"].update({t: 0.8 for t in range(18, 22)})
mixer = sys.modules["mixer"]; mixer.trackCount = lambda: N_TRACKS
check(m.fl_channel_set(11, mixer_track=17)["insert"] == 17, "17 is fine once the mixer has it")

# ---------------------------------------------------------------- 5. Parametric EQ 2 width as a %
mp = X.plugin_map(X.PEQ)
got = X.bridge_unit(mp, "band3_width", 1.4, "Q", "%")
check(got is not None and 20 < got[0] < 30 and "unverified" in got[1], f"Q 1.4 -> width %: {got}")
learned = dict(mp, controls=dict(mp["controls"], band3_width=dict(mp["controls"]["band3_width"], unit="%",
                                                                  min=0, max=100)))
X_pm, X_br = X.plugin_map, X._bridge
knob = {"v": 0.5}
X.plugin_map = lambda name: learned
ns["fl_plugin_params"] = lambda t, s, f="": {"plugin": X.PEQ, "params": [
    {"param": 16, "name": "Band 3 width", "value": knob["v"], "text": f"{knob['v'] * 100:.1f}%"}]}


def peq_bridge(cmd, **a):
    if cmd == "plugin_set":
        knob["v"] = float(a["value"])
        return {"param": a["param"], "value": knob["v"], "text": f"{knob['v'] * 100:.1f}%"}
    if cmd == "state":
        return {"playing": False}
    raise AssertionError(cmd)


X._bridge = peq_bridge
r = X.plugin_set_named(3, 1, X.PEQ, {"band3_width": "1.4 Q"})
d = (r.get("set") or [{}])[0]
check(not r["skipped"] and d.get("unverified") and abs(knob["v"] * 100 - got[0]) < 1.0, f"PEQ2 Q on a % width: {r}")
X.plugin_map, X._bridge = X_pm, X_br

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
if FAILS:
    print(f"{len(FAILS)} failure(s)"); sys.exit(1)
print("ok")
