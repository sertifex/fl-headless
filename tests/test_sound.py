"""Sound description + wish scoring. Run: python tests/test_sound.py"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import fl_sound as S
sr = 48000; t = np.arange(int(3 * sr)) / sr
def tone(freqs, attack, decay):
    env = np.minimum(1, t / max(attack, 1e-3)) * np.exp(-t / decay)
    y = sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs) * env * 0.5
    return np.stack([y, y * 0.9], 1)
pad = S.describe(tone([220, 330, 440], 0.6, 8.0), sr)
pluck = S.describe(tone([880, 1760, 3520, 5000], 0.003, 0.15), sr)
assert pad["attack_ms"] > 300 and pad["sustain_db"] > -6, pad
assert pluck["attack_ms"] < 60 and pluck["sustain_db"] < -18 and pluck["centroid_hz"] > pad["centroid_hz"], pluck
assert S.score(pad, "soft string pad") > S.score(pluck, "soft string pad")
assert S.score(pluck, "bright pluck") > S.score(pad, "bright pluck")
assert S.describe(np.zeros((sr, 2)), sr) == {"silent": True}
print("sound ok")

# ---- fl_audition with a fake FL: query mode ranks by the wish, saves previews, reopens the song; apply
import tempfile, time
tmp = tempfile.mkdtemp(); os.environ["LOCALAPPDATA"] = tmp; os.environ["FLH_ROOTS"] = tmp
import fl_capture as C
import fl_headless_mcp as m
SOUNDS = {5: tone([220, 330, 440], 0.6, 8.0), 6: tone([880, 1760, 3520, 5000], 0.003, 0.15)}
cur = {"ch": None}
class Rec:
    def start(self): pass
    def stop(self): return sr, np.vstack([np.zeros((2400, 2)), SOUNDS[cur["ch"]]]).astype(np.float32)
C.available = lambda: (True, "ok"); C.LoopbackRecorder = Rec
def call(cmd, timeout=15.0, **a):
    if cmd == "play": cur["ch"] = a["channel"]
    return {}
m.BRIDGE.call = call; m.fl_running = lambda: True
m.time.sleep = lambda s: None
opened = []
m.fl_open = lambda p, save_current=True: opened.append(p)
m._open_project_guess = lambda: (os.path.join(tmp, "song.flp"), "song")
tpl = os.path.join(tmp, "tpl.flp"); open(tpl, "wb").write(b"x"); m._state({"selftest_template": tpl})
m.fl_find_presets = lambda q, limit=40: {"presets": [r"C:\\P\\Packs\\Instruments\\Soft Pad.fst", r"C:\\P\\Packs\\Instruments\\Glass Pluck.fst",
                                                     r"C:\\P\\Plugin presets\\Effects\\Reverb.fst"]}
nxt = iter([5, 6])
m.flp_add_channel = lambda a, b, c, preset=None: {"new_channel": next(nxt)}
r = m.fl_audition(want="soft string pad", query="pad", count=5)
assert [x["name"] for x in r["ranked"]] == ["Soft Pad", "Glass Pluck"], r["ranked"]
assert all(os.path.getsize(x["preview"]) > 100 for x in r["ranked"])
assert opened[-1].endswith("song.flp") and opened[0].endswith("audition.flp"), opened
loaded = []
m.flp_load_plugin = lambda a, b, pre, channel=None: loaded.append((pre, channel))
m._with_open_project = lambda p, o, fn, **k: fn()                 # FL saves + reloads around the write (mocked)
a = m.fl_audition(apply=1, apply_channel=5)
assert loaded == [(r"C:\\P\\Packs\\Instruments\\Soft Pad.fst", 5)] and a["applied"] == "Soft Pad", (loaded, a)
print("audition ok")
