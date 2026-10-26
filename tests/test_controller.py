"""The generated controller script must react to CC numbers as FL sends them (ints). Run: python tests/test_controller.py"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fl_headless_mcp as m

calls = []
for n in ("channels", "mixer", "plugins", "transport"):
    sys.modules[n] = types.ModuleType(n)
sys.modules["mixer"].setTrackVolume = lambda t, v: calls.append(("vol", t, v))
sys.modules["channels"].setChannelVolume = lambda c, v: calls.append(("ch", c, v))
sys.modules["transport"].start = lambda: calls.append("play")
sys.modules["transport"].stop = sys.modules["transport"].record = lambda: None

table = {1: {"do": "mixer_volume", "track": 1}, 2: {"do": "channel_volume", "channel": 0}, 64: {"do": "play"}}
g = {}
exec(m.CONTROLLER_TEMPLATE.format(name="t", device="t", mapping=json.dumps(table, indent=1)), g)


class E:
    def __init__(self, d1, d2):
        self.data1, self.data2, self.handled = d1, d2, False


for d1, d2 in ((1, 127), (2, 0), (64, 127), (5, 10)):
    g["OnControlChange"](E(d1, d2))
assert calls == [("vol", 1, 1.0), ("ch", 0, 0.0), "play"], calls
print("controller ok")
