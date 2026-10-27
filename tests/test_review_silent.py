"""fl_doctor / fl_review: practically silent channels (content not on this PC, a level knob at 0 - by the plugin's own
parameter names, any plugin) and abrupt_entrance without false positives on struck chords out of a decaying tail
(synthetic audio). Run: python3 tests/test_review_silent.py"""
import json, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
lib = os.path.join(tmp, "lib"); os.environ["FLH_SPLICE_LIB"] = lib
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_review as R, flh_flex as FX  # noqa: E402
import fl_audio_edit as AE  # noqa: E402
ns = vars(m)
flh_store.register(ns); R.register(ns); FX.register(ns)
kinds = lambda res, k: [i for i in res["issues"] if i["kind"] == k]

# ---------------------------------------------------------------- level-type names: the plugin's own, any plugin
lk = R.level_kind
assert lk("Volume") == "level" and lk("outputGain") == "level" and lk("Master Level") == "level"
assert lk("Dynamics") == "level" and lk("EXPRESSION") == "level" and lk("i_dynamics") == "level"
assert lk("Sustain Level") is None and lk("Reverb Level") is None and lk("LFO Amount") is None
assert lk("Release") is None and lk("Cutoff") is None and lk("Velocity Amount") == "weak"
assert R._shows_silent(0.0, "0%", R.level_rules()) and R._shows_silent(0.0, "-inf dB", R.level_rules())
assert not R._shows_silent(0.5, "0.0 dB", R.level_rules()) and R._shows_silent(0.0, "-72 dB", R.level_rules())
assert not R._shows_silent(0.7, "70%", R.level_rules()) and R._shows_silent(0.0, "", R.level_rules())
assert R._shows_silent(0.0, "Off", R.level_rules())
# a saved JUCE-style state: a 0..1 PARAM (ccFrom/ccTo) mapped to CC11 at 0 = silent; a dB gain stored as 0.0 (unity,
# no MIDI range) is never read as silent
st = (b"xxxxVC2!" + b"\0\0\0\0")
xml = ('<R><PARAMETERS><PARAM id="g_general_param_4" value="0.0" cc="11" ccFrom="0.0" ccTo="1.0"/>'
       '<PARAM id="outputGain" value="0.0"/><PARAM id="g_general_param_1" value="1.0" cc="7" ccFrom="0.0" ccTo="1.0"/>'
       '<PARAM id="dynamics" value="0.0" cc="-1" ccFrom="0.0" ccTo="1.0"/></PARAMETERS></R>').encode()
import struct  # noqa: E402
st = b"xxxx" + b"VC2!" + struct.pack("<I", len(xml)) + xml
q = R.quiet_knobs_static(st)
assert sorted(x["name"] for x in q) == ["dynamics", "expression (CC11)"], q

# ---------------------------------------------------------------- doctor: an INSTRUMENT channel with notes, CC11 at 0
src = os.path.join(tmp, "song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), src)
d = m._load(src); ev = m._events(d); _, ks = m._channel_block(d, ev, 4)
assert b'id="g_general_param_4" value="1.0"' in m._payload(d, ev[ks])
m.flp_plugin_settings(src, src, 4, params={"g_general_param_4": 0.0})      # expression (CC11) to 0
dr = R.fl_doctor(src)
sk = kinds(dr, "silent_knob")
print("silent_knob:", json.dumps(sk)[:300])
assert len(sk) == 1 and sk[0]["severity"] == "high" and "channel 4" in sk[0]["where"], sk
assert "expression (CC11)" in sk[0]["what"] and "flp_plugin_settings" in sk[0]["fix"] and \
    "g_general_param_4" in sk[0]["fix"], sk
assert not kinds(dr, "content_missing")                    # no INSTRUMENT library here yet: can't tell, not flagged

# ---------------------------------------------------------------- content: the pack the state names isn't on this PC
os.makedirs(os.path.join(lib, "LABS - Choir", "Presets", "v1.0.0"))
dr = R.fl_doctor(src)
cm = kinds(dr, "content_missing")
print("content_missing:", json.dumps(cm)[:300])
assert cm and all(i["severity"] == "high" for i in cm) and "Content Not Found" in cm[0]["what"], cm
c4 = [i for i in cm if "channel 4" in i["where"]]
assert c4 and "Soft Piano" in c4[0]["what"] and "flp_instrument_sound" in c4[0]["fix"], cm
os.makedirs(os.path.join(lib, "LABS - Soft Piano", "Presets", "v1.0.5"))   # presets only: not downloaded
os.makedirs(os.path.join(lib, "Intimate Grand Piano", "Presets", "v1.0.0"))
cm = [i for i in kinds(R.fl_doctor(src), "content_missing") if "channel 4" in i["where"]]
assert cm and "not downloaded" in cm[0]["what"], cm
for pk in ("LABS - Soft Piano", "Intimate Grand Piano"):                       # their sounds downloaded
    with open(os.path.join(lib, pk, "Samples.bin"), "wb") as f:
        f.write(b"\0" * 1_200_000)
assert not kinds(R.fl_doctor(src), "content_missing")
m._state({"silent_packs": {"LABS - Soft Piano": {"seen": "2026-10-20", "project": "x.flp"}}})
cm = kinds(R.fl_doctor(src), "content_missing")
assert cm and cm[0]["severity"] == "med" and "rendered silent" in cm[0]["what"], cm
m._state({"silent_packs": {}})

# ---------------------------------------------------------------- live: the plugin's own knob names, read from FL
seen = []


def fake_params(index, slot=-1, filter=""):
    seen.append(index)
    if index == 2:
        return {"plugin": "Some Synth", "params": [
            {"param": 0, "name": "Dynamics", "value": 0.0, "text": "0%"},
            {"param": 1, "name": "Expression", "value": 0.0, "text": "0"},
            {"param": 2, "name": "Release", "value": 0.0, "text": "0 ms"},
            {"param": 3, "name": "Output Gain", "value": 0.5, "text": "0.0 dB"}]}
    return {"plugin": "Other", "params": [{"param": 0, "name": "Volume", "value": 0.8, "text": "-1.9 dB"}]}


ns["fl_plugin_params"], real_params = fake_params, ns["fl_plugin_params"]
ns["_open_project_guess"], real_guess = (lambda: (src, "song")), ns["_open_project_guess"]
dl = R.fl_doctor(src, live=True)
lv = [i for i in kinds(dl, "silent_knob") if "shows" in i["what"]]
print("live:", json.dumps(lv)[:300])
assert sorted(seen) == [0, 1, 2, 4], seen                    # only channels that play notes
assert len(lv) == 2 and all("channel 2" in i["where"] for i in lv), lv
assert {"'Dynamics'", "'Expression'"} == {i["what"].split(": ")[1].split(" (")[0] for i in lv}, lv
assert "fl_plugin_set" in lv[0]["fix"]
ns["_open_project_guess"] = lambda: (os.path.join(tmp, "other.flp"), "other")
dl = R.fl_doctor(src, live=True)
assert any("FL doesn't have this project open" in n for n in dl.get("notes") or []), dl.get("notes")
ns["fl_plugin_params"], ns["_open_project_guess"] = real_params, real_guess

# ---------------------------------------------------------------- fl_review audio: the doctor's silent finds as a hint
fs = 44100
quiet = np.zeros((fs * 12, 2)); quiet[:, 0] = quiet[:, 1] = 1e-6 * np.random.default_rng(1).normal(size=fs * 12)
wq = os.path.join(tmp, "quiet.wav"); AE.save(wq, quiet, fs, 24)
rv = R.fl_review(project=src, audio=wq, sections=[{"name": "A", "start_s": 0, "end_s": 12}], offset_s=0)
assert kinds(rv, "silent_knob"), rv["issues"]
ss = kinds(rv, "silent_section")
assert ss and "practically silent" in ss[0]["fix"] and "channel 4" in ss[0]["fix"], ss

# ---------------------------------------------------------------- abrupt_entrance: struck chords vs a pad slam
rng = np.random.default_rng(7)


def chord(t0, dur, db, tau=1.5, freqs=(220.0, 277.2, 329.6), roll=0.08):
    """A struck (piano-like) chord at t0: each note an exponential decay, rolled by `roll` s."""
    n = int(dur * fs); out = np.zeros(n)
    for k, f in enumerate(freqs):
        a = int((t0 + k * roll) * fs)
        if a >= n:
            continue
        t = np.arange(n - a) / fs
        out[a:] += np.sin(2 * np.pi * f * t) * np.exp(-t / tau) * 10 ** (db / 20) / len(freqs)
    return out


def pad(t0, dur, db, f=196.0):
    n = int(dur * fs); t = np.arange(n) / fs
    return np.where(t >= t0, np.sin(2 * np.pi * f * t) * 10 ** (db / 20), 0.0)


dur = 24.0
y = sum(chord(t0, dur, -12) for t0 in (2.0, 5.0, 8.0, 14.0)) + rng.normal(0, 1e-5, int(dur * fs))
pr = y + pad(18.0, dur, -14)                                  # a sustained pad slams in mid-section at 18 s
secs = [{"name": "A", "start_s": 0, "end_s": 12}, {"name": "B", "start_s": 12, "end_s": 24}]
wp = os.path.join(tmp, "prelude.wav"); AE.save(wp, np.stack([y, y], 1), fs, 24)
ra = R.fl_review(audio=wp, sections=secs)
ab = kinds(ra, "abrupt_entrance")
print("prelude:", json.dumps(ab)[:200], ra.get("notes"))
assert not ab, ab                                             # struck chords out of a dying tail: none flagged
assert any("struck attacks" in n for n in ra.get("notes") or []), ra.get("notes")
wp2 = os.path.join(tmp, "prelude_pad.wav"); AE.save(wp2, np.stack([pr, pr], 1), fs, 24)
ab2 = kinds(R.fl_review(audio=wp2, sections=secs), "abrupt_entrance")
print("pad:", json.dumps(ab2)[:200])
assert len(ab2) == 1 and ab2[0]["where"].startswith("0:18"), ab2
# the same check on power blocks: a decaying attack out of a falling tail is struck; a flat step isn't
blocks = [1e-3 * 0.8 ** k for k in range(40)] + [1e-1 * 0.75 ** k for k in range(40)]
assert R.struck_entry(blocks, 4.0) and not R.abrupt_events(None, None, blocks, [], None)
assert not R.struck_entry([1e-3] * 60 + [1e-1] * 60, 6.0)
shutil.rmtree(tmp, True)
print("ok")
