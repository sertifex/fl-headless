"""fl_listen with a fake FL (bridge) and a fake speaker recording: plays bars, measures per section, saves FLAC.
Run: python tests/test_listen.py"""
import os
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import fl_capture as C                                           # noqa: E402
import fl_headless_mcp as m                                      # noqa: E402

SR = 48000
state = {"bar": 1.0, "playing": False, "song_mode": False, "t": None, "calls": []}
BAR_S = 1.0                                                      # one bar per second in this fake song


def fake_call(cmd, timeout=15.0, **a):
    state["calls"].append((cmd, a.get("action")))
    if cmd == "transport":
        if a["action"] == "play":
            state["playing"], state["t"], state["b0"] = True, time.time(), state["bar"]
        elif a["action"] == "stop" and state["playing"]:
            state["bar"] = state["b0"] + (time.time() - state["t"]) / BAR_S; state["playing"] = False
        return {"done": a["action"]}
    if cmd == "song":
        if a["action"] == "goto":
            state["bar"] = a["bar"]
        if a["action"] == "song_mode":
            state["song_mode"] = not state["song_mode"]
        b = state["b0"] + (time.time() - state["t"]) / BAR_S if state["playing"] else state["bar"]
        if state.get("wrap") and b >= state["wrap"]:
            b = 1.0                                              # the song ended: FL jumps back to the start
        return {"bar": round(b, 3), "song_mode": state["song_mode"]}
    raise AssertionError(cmd)


class FakeRec:
    hum = 0.0
    def start(self): self.t = time.time()
    def stop(self):
        n = int((time.time() - self.t) * SR); t = np.arange(n) / SR - m.PREROLL_S   # FL plays after the pre-roll
        quiet = 0.05 * np.sin(2 * np.pi * 220 * t); loud = 0.5 * np.sin(2 * np.pi * 220 * t)
        x = np.where(t < 2.1, quiet, loud).astype(np.float32)       # section B (bar 3+) is much louder
        x[t < 0.3] = 0                                               # pre-roll + latency before sound starts
        x = x + (self.hum * np.sin(2 * np.pi * 64 * (t + m.PREROLL_S))).astype(np.float32)
        return SR, np.stack([x, x], 1)


C.available = lambda: (True, "ok"); C.LoopbackRecorder = FakeRec
m.BRIDGE.call = fake_call; m.fl_running = lambda: True; m._open_bpb = lambda: 3
m._song_sections = lambda bpb: [("A", 1.0), ("B", 2.8)]
out = os.path.join(tmp, "take.flac")
r = m.fl_listen(start_bar=1, end_bar=4.8, save=out)
modes = [c for c in state["calls"] if c == ("song", "song_mode")]
assert r["heard"] and len(modes) == 2 and not state["song_mode"], (modes, state["song_mode"])  # song mode, then back
assert ("transport", "stop") in state["calls"][-3:], state["calls"][-5:]
names = [s["name"] for s in r["sections"]]
assert names and names[0].startswith("A") and names[-1].startswith("B"), names
la, lb = r["sections"][0]["lufs"], r["sections"][-1]["lufs"]
assert lb > la + 10, (la, lb)                                    # the loud part measures louder
assert os.path.getsize(out) > 1000 and open(out, "rb").read(4) == b"fLaC"
assert 3.0 < r["seconds"] < 7, r["seconds"]

class Silent(FakeRec):
    def stop(self):
        n = int((time.time() - self.t) * SR); return SR, np.zeros((n, 2), np.float32)
C.LoopbackRecorder = Silent
r2 = m.fl_listen(start_bar=1, seconds=1.0)
assert r2["heard"] is False and "device" in r2["why"], r2
C.LoopbackRecorder = FakeRec
state["wrap"] = 3.5
r3 = m.fl_listen(start_bar=1)                                    # no end given: stops at the song's end
assert r3["heard"] and r3["bars"][1] >= 3.0 and r3["seconds"] < 5.5, (r3["bars"], r3["seconds"])
assert "background" not in r3, r3.get("background")
state.pop("wrap", None); FakeRec.hum = 0.02                     # something else hums on the PC
r4 = m.fl_listen(start_bar=1, end_bar=4.8)
assert r4["background"]["hz"] in (63, 64, 65) and r4["background"]["db"] > -40, r4["background"]
assert 3.0 < r4["seconds"] < 7, r4["seconds"]                   # the hum doesn't stretch the take
FakeRec.hum = 0.0
# the reverb tail: once the speakers are quiet the take ends (no fixed 1.5 s wait); where the time went is reported
class Dry(FakeRec):
    def recent_peak(self, seconds=0.25):
        return 0.0 if not state["playing"] else 0.5
C.LoopbackRecorder = Dry
r5 = m.fl_listen(start_bar=1, seconds=1.0)
assert r5["steps_s"]["tail"] < 0.8 and {"fl_setup", "preroll", "play", "analyze"} <= set(r5["steps_s"]), r5["steps_s"]
assert r["steps_s"]["tail"] >= 1.4, r["steps_s"]                 # a recorder that can't tell: the full wait
print("listen ok")
