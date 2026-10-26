"""flh_sampler: Sampler channel envelope / filter / pitch / reverse / declick / cut-itself in the .flp - read, shape,
write, round trips, unknown fields, exact-byte undo; the live bridge command against fake FL modules.
Run: python3 tests/test_sampler.py"""
import base64
import os
import shutil
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="flh_sampler_")
os.environ["FLH_ROOTS"] = TMP
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
import fl_headless_mcp as m  # noqa: E402
import flh_sampler as SM  # noqa: E402

ns = vars(m)
m.fl_running = lambda: False
SM.register(ns)
CORPUS = os.path.join(HERE, "corpus")


def copy(name):
    p = os.path.join(TMP, name)
    shutil.copy(os.path.join(CORPUS, name), p)
    return p


def blocks(path):
    """{channel: bytes of its events} + everything outside the channel blocks."""
    d = open(path, "rb").read(); ev = m._events(d); out = {}
    for ch, (a, b) in m._channel_ranges(d, ev).items():
        out[ch] = d[ev[a]["off"]:ev[b - 1]["end"]]
    return d, out


# ============================================================================= read (sampler type 0, audio clip type 4)
P = copy("missing_samples.flp")
r = SM.sampler_read(P, 6)
assert r["kind"] == "sampler" and r["envelope"] is False and r["filter_type"] == "fast_lp", r
assert r["sustain_pct"] == 39.1 and r["cutoff_pct"] == 100.0 and r["resonance_pct"] == 0.0, r   # FL defaults
assert r["pitch_st"] == 1 and r["fine_cents"] == 6 and r["reverse"] is True and r["cut_itself"] is False, r
assert r["release_tension"] == -78.9 and r["attack_ms"] > 0 and r["cutoff_hz"] == 20000, r
B = copy("base.flp")
assert SM.sampler_read(B, 5)["kind"] == "audio clip"
for bad in (0, 3):                                               # plugin / automation channels are refused
    try:
        SM.sampler_read(B, bad); raise AssertionError("plugin channel accepted")
    except ValueError as e:
        assert "not a Sampler" in str(e), e
try:
    SM.sampler_read(B, 99); raise AssertionError
except ValueError as e:
    assert "no channel" in str(e)

# ============================================================================= unit helpers
for ms in (0.5, 5, 50, 300, 2000):
    assert abs(SM._ms(SM._raw_ms(ms)) - ms) / ms < 0.05, ms
for hz in (40, 200, 2000, 15000):
    assert abs(SM._hz(SM._raw_hz(hz)) - hz) / hz < 0.03, hz
assert SM._parse("x2", False) == ("ratio", 2.0) and SM._parse("/2", False) == ("ratio", 0.5)
assert SM._parse("2k", False) == ("abs_hz", 2000.0) and SM._parse("2000 Hz", False) == ("abs_hz", 2000.0)
assert SM._parse("150 ms", False) == ("abs_ms", 150.0) and SM._parse("1.2s", False) == ("abs_ms", 1200.0)
assert SM._parse("40%", False) == ("abs_pct", 40.0) and SM._parse(-1.0, False) == ("delta", -1.0)
assert SM._parse(5, True) == ("abs", 5.0) and SM._parse("+5", True) == ("delta", 5.0)

# ============================================================================= shape: every field, others untouched
orig = open(P, "rb").read()
_, ob = blocks(P)
moves = {"attack_ms": 20, "release_ms": "x2", "decay_ms": "=300", "sustain_pct": "40%", "cutoff": -1.0,
         "resonance_pct": 20, "filter_type": "lp", "pitch_st": -2, "fine_cents": 4, "reverse": False,
         "declick": "transient", "cut_itself": True, "decay_tension": -50, "wobble": 3}
res = SM.sampler_shape(P, 6, moves)
fields = {x["field"] for x in res["done"]}
assert fields >= {"attack_ms", "release_ms", "decay_ms", "sustain_pct", "cutoff", "resonance_pct", "filter_type",
                  "pitch_st+fine_cents", "reverse", "declick", "cut_itself", "envelope", "decay_tension"}, fields
assert [s["field"] for s in res["skipped"]] == ["wobble"] and "unknown field" in res["skipped"][0]["why"], res
assert res["notes"] and "envelope was off" in res["notes"][0] and res["estimate"], res
new = open(P, "rb").read()
assert len(new) == len(orig)                                     # payloads replaced in place
_, nb = blocks(P)
assert all(nb[c] == ob[c] for c in ob if c != 6) and nb[6] != ob[6]   # only channel 6 changed
d_o, d_n = bytearray(orig), bytearray(new)
diff = [i for i in range(len(orig)) if orig[i] != new[i]]
ev = m._events(orig); a, b = m._channel_ranges(orig, ev)[6]
assert all(ev[a]["off"] <= i < ev[b - 1]["end"] for i in diff)
s = SM.sampler_read(P, 6)                                        # reparse: the values stuck
assert s["envelope"] is True and s["sustain_pct"] == 39.8 and abs(s["decay_ms"] - 300) < 6, s   # 40% -> 51/128
assert s["predelay_ms"] == 0.0 and s["hold_ms"] == 0.0, s       # neutral stages when the envelope came on
assert abs(s["attack_ms"] - 20) < 1.5, s                         # 0 ms (neutral) + 20
assert abs(s["release_ms"] - 2 * r["release_ms"]) / (2 * r["release_ms"]) < 0.02, (s, r)
assert 9000 < s["cutoff_hz"] < 11000 and s["filter_type"] == "lp" and s["resonance_pct"] == 19.9, s
assert s["pitch_st"] == -1 and s["fine_cents"] == 10 and s["reverse"] is False, s    # 106 c - 2 st + 4 c
assert s["declick"] == "transient" and s["cut_itself"] is True and s["decay_tension"] == -50.0, s
# the cut group is one no other channel uses
d = open(P, "rb").read(); ev = m._events(d)
groups = {ch: struct.unpack("<HH", m._payload(d, ev[k])[:4]) for ch, (a, b) in m._channel_ranges(d, ev).items()
          for k in range(a, b) if ev[k]["id"] == 132}
g = groups[6][0]
assert g and groups[6] == (g, g) and all(g not in v for c, v in groups.items() if c != 6), groups
# the same block shaped in another project reads back the same (round trip elsewhere)
Q = copy("missing_samples.flp")
SM.sampler_shape(Q, 6, dict(moves, wobble=None))
assert SM.sampler_read(Q, 6) == s
assert open(Q, "rb").read() == new

# ============================================================================= undo restores the exact bytes
u = SM.sampler_undo(P, 6, res["undo"])
assert sorted(u["restored"]) == sorted({e["id"] for e in res["undo"]["events"]}), u
assert open(P, "rb").read() == orig
assert SM.sampler_undo(P, 6, res["undo"])["restored"] == []     # twice: nothing left to do
# changed again since: kept, reported
res2 = SM.sampler_shape(P, 6, {"cutoff": "1000 Hz"})
SM.sampler_shape(P, 6, {"cutoff": "x0.5"})
u2 = SM.sampler_undo(P, 6, res2["undo"])
assert u2["restored"] == [] and u2["kept"] and "changed since" in u2["kept"][0]["why"], u2
assert 400 < SM.sampler_read(P, 6)["cutoff_hz"] < 600

# ============================================================================= details
shutil.copy(os.path.join(CORPUS, "missing_samples.flp"), P)
r3 = SM.sampler_shape(P, 7, {"pitch_st": 24})
assert SM.sampler_read(P, 7)["pitch_st"] == 12 and "limited" in r3["notes"][0], r3
r4 = SM.sampler_shape(P, 7, {"envelope": True})                 # on, nothing else: stages untouched
assert SM.sampler_read(P, 7)["sustain_pct"] == 39.1 and "notes" not in r4
r5 = SM.sampler_shape(P, 7, {"attack_ms": "fast", "filter_type": "nope", "reverse": "maybe"})
assert {x["field"] for x in r5["skipped"]} == {"attack_ms", "filter_type", "reverse"} and not r5["undo"]["events"], r5
assert SM.sampler_shape(P, 7, {"bogus": 1})["skipped"][0]["field"] == "bogus"
dry = SM.sampler_shape(P, 7, {"reverse": True}, dry_run=True)
assert dry["dry_run"] and dry["done"] and SM.sampler_read(P, 7)["reverse"] is True    # 7 was already reversed
dry = SM.sampler_shape(P, 7, {"reverse": False}, dry_run=True)
assert SM.sampler_read(P, 7)["reverse"] is True                  # dry run wrote nothing
r6 = SM.sampler_shape(P, 7, {"cut_itself": True}); r7 = SM.sampler_shape(P, 8, {"cut_itself": True})
assert r6["done"][0]["group"] != r7["done"][0]["group"]          # two channels never share (no cross-choking)
SM.sampler_shape(P, 7, {"cut_itself": False})
assert SM.sampler_read(P, 7)["cut_group"] == [0, 0]
SM.sampler_shape(B, 5, {"release_ms": "200 ms", "reverse": False})          # audio clip channel works too
assert abs(SM.sampler_read(B, 5)["release_ms"] - 200) < 5
names = SM._names(*(lambda d: (d, m._events(d)))(open(P, "rb").read()))
assert SM._resolve(open(P, "rb").read(), m._events(open(P, "rb").read()), names[9]) in (9,) or \
    list(names.values()).count(names[9]) > 1

# ============================================================================= the tool (flh_call)
T = ns["TOOLS"]["flp_sampler_settings"]
assert len(T[1]) <= 290 and all(len(p["description"]) <= 80 for p in T[2]["properties"].values()), T
assert T[2]["required"] == ["project", "channel"], T[2]
shutil.copy(os.path.join(CORPUS, "missing_samples.flp"), P)
before = open(P, "rb").read()
rd = m._flh_call("flp_sampler_settings", {"project": P, "channel": 6})
assert rd.get("kind", (rd.get("result") or {}).get("kind")) == "sampler", rd
OUT = os.path.join(TMP, "out.flp")
w = T[0](project=P, output=OUT, channel="6", set={"attack_ms": 5, "cutoff": 2000, "resonance": 30,
                                                     "sustain_pct": 100, "pitch_st": -7, "nonsense": 1})
assert open(P, "rb").read() == before                            # the input is untouched
now = SM.sampler_read(OUT, 6)
assert abs(now["attack_ms"] - 5) < 0.5 and 1900 < now["cutoff_hz"] < 2100 and now["resonance_pct"] == 30.1, now
assert now["sustain_pct"] == 100.0 and now["pitch_st"] == -7 and w["skipped"][0]["field"] == "nonsense", w
assert w["now"] == {k: v for k, v in now.items() if k not in ("channel", "kind", "estimated")}

# ============================================================================= live bridge command (fake FL)
calls = []


def mod(name, **kw):
    mm = types.ModuleType(name); mm.__dict__.update(kw); sys.modules[name] = mm


REC = {}
mod("device", getPortNumber=lambda: 3, dispatchReceiverCount=lambda: 0, dispatch=lambda *a: None,
    midiOutSysex=lambda b: None)
mod("midi", REC_Chan_FCut=2, REC_Chan_FRes=3, FromMIDI_Max=1 << 30, REC_MIDIController=1, REC_UpdateControl=2,
    REC_UpdateValue=4, REC_GetValue=8)


def rec(eid, v, flags):
    if flags & 8:
        return REC.get(eid, 0)
    REC[eid] = v
    calls.append(("rec", eid, v))


mod("general", processRECEvent=rec, saveUndo=lambda *a: None)
mod("transport"); mod("mixer")
PITCH = {}
mod("channels", channelCount=lambda g=False: 2, getRecEventId=lambda i, g=False: 1000 * (i + 1),
    setChannelPitch=lambda i, v, mode=0, g=False: PITCH.__setitem__(i, v),
    getChannelPitch=lambda i, mode=0, g=False: PITCH.get(i, 0.0) if mode == 1 else 2)
sys.path.insert(0, os.path.join(ROOT, "fl_script"))
import device_flheadless as BR  # noqa: E402

assert "sampler_live" in BR.COMMANDS
lv = BR.cmd_sampler_live(1, pitch_cents=-30, cutoff=0.5, resonance=0.25)
assert lv == {"index": 1, "pitch_cents": -30.0, "pitch_range_st": 2, "cutoff": 0.5, "resonance": 0.25}, lv
assert REC[2002] == 1 << 29 and REC[2003] == 1 << 28
assert BR.cmd_sampler_live(0)["cutoff"] == 0.0
seen = {}
ns["BRIDGE"] = types.SimpleNamespace(call=lambda cmd, **kw: seen.update(cmd=cmd, **kw) or {"ok": 1})
SM.sampler_live(3, cutoff=0.4)
assert seen == {"cmd": "sampler_live", "index": 3, "pitch_cents": None, "cutoff": 0.4, "resonance": None}, seen
assert all(ord(c) < 128 for c in open(os.path.join(ROOT, "fl_script", "device_flheadless.py"), encoding="utf-8").read())

shutil.rmtree(TMP, ignore_errors=True)
print("ok")
