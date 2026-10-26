"""Port choice and the loopback probe used by fl_bridge_setup. Run: python tests/test_bridge_setup.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fl_headless_mcp as m

st = {}
m._state = lambda u=None: (st.update(u) if u else None) or dict(st)
B = m._Bridge
ports = {"midi_out": ["X", "Default Basic App Loopback"], "midi_in": ["X", "Default Basic App Loopback"]}
B.ports = lambda self: ports
b = B()
assert b.choose() == ("Default Basic App Loopback", "Default Basic App Loopback")                  # no loopMIDI: Windows loopback port
ports["midi_out"].append("Flapi Request"); ports["midi_in"].append("Flapi Response")
assert b.choose() == ("Flapi Request", "Flapi Response")                  # loopMIDI pair if present
st["bridge_ports"] = ["Default Basic App Loopback", "Default Basic App Loopback"]
assert b.choose() == ("Default Basic App Loopback", "Default Basic App Loopback")                  # saved choice wins
os.environ["FLH_PORT_OUT"] = os.environ["FLH_PORT_IN"] = "X"
assert b.choose() == ("X", "X")                                           # env wins over everything
del os.environ["FLH_PORT_OUT"], os.environ["FLH_PORT_IN"]
loop = []
B._open_pair = lambda self, p, o, i: setattr(self, "ok", True)
B._send = lambda self, d: loop.append(d)
B._drain = lambda self: [loop.pop(0) for _ in range(len(loop))]
B.close = lambda self: setattr(self, "ok", False)
assert m._loopback_test()["loopback"]
B._send = lambda self, d: None
assert not m._loopback_test()["loopback"]
# a dead port handle is reopened once instead of failing
calls = {"send": 0, "open": 0}
def bad_send(self, d):
    calls["send"] += 1
    if calls["send"] == 1:
        raise RuntimeError("MIDI send failed (error 64)")
    raise TimeoutError("sent")
B._send = bad_send
B.open = lambda self: calls.__setitem__("open", calls["open"] + 1)
try:
    B().call("ping", timeout=0.1)
except TimeoutError:
    pass
assert calls == {"send": 2, "open": 2}, calls
print("bridge setup ok")
