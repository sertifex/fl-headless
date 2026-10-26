"""fl_bridge_setup(action='use_input'): the bridge script on an existing MIDI input (a keyboard) - no virtual port.
FL's MIDI settings are mocked (registry dump), as are FL's restart and the mailbox. Run: python3 tests/test_bridge_input.py"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_bin_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402

DEV = r"FL Studio 2026\Settings\Devices"
REG = {DEV + r"\MIDI input\MPK mini 3": {"Enabled": [4, 1], "Type": [4, 0], "ScriptFolder": [1, ""], "Port": [4, -1]},
       DEV + r"\MIDI input\Akai Pad": {"Enabled": [4, 1], "Type": [4, -3], "ScriptFolder": [1, "Akai MPD"],
                                        "Port": [4, 3]},
       DEV + r"\MIDI output\MPK mini 3": {"Port": [4, -1]}}
calls = {"settings": [], "ping": 0, "installed": []}
m.IS_WINDOWS = True
m.fl_running = lambda: True
m.BRIDGE.ports = lambda: {"midi_in": ["MPK mini 3", "Akai Pad"], "midi_out": ["MPK mini 3"]}
m._loopback_ports = lambda p: []
m._reg_dump_keys = lambda: list(REG)
m._reg_dump = lambda sub="": {k: v for k, v in REG.items() if k == sub or k.startswith(sub + "\\")}
m._hardware_dir = lambda: os.path.join(tmp, "Hardware")
m._install_script = lambda dest, name_line=None: calls["installed"].append(dest)


def settings_set(changes, restart_fl=False, reopen=True, close_fl=None):
    calls["settings"].append(changes)
    for c in changes:
        typ = 1 if c.get("type") == "text" else 4
        REG[c["key"]][c["name"]] = [typ, int(c["value"]) if typ == 4 and str(c["value"]).lstrip("-").isdigit() else c["value"]]
    return {"changed": changes}


m.fl_settings_set = settings_set
m._mailbox_alive = lambda max_age=5.0: True
_real_call = m.BRIDGE.call


def call(cmd, timeout=15.0, **a):
    if cmd == "ping":
        calls["ping"] += 1
        return {"pong": True}
    raise AssertionError(cmd)


m.BRIDGE.call = call
key = DEV + r"\MIDI input\MPK mini 3"
# no port given: the inputs are listed, nothing changes
r = m.fl_bridge_setup(action="use_input")
assert r["done"] is False and r["inputs"] == ["MPK mini 3", "Akai Pad"] and not calls["settings"], r
# FL open and no restart allowed: refused before anything is written
try:
    m.fl_bridge_setup(action="use_input", port="MPK mini 3"); raise SystemExit("must ask for restart_fl")
except RuntimeError as e:
    assert "restart_fl=true" in str(e) and not calls["settings"], e
# an input that runs another FL script: refused unless replace=true
r = m.fl_bridge_setup(action="use_input", port="Akai Pad", restart_fl=True)
assert r["done"] is False and r["its_script"] == "Akai MPD" and not calls["settings"], r
# the keyboard: the script goes on its input (enabled, script type, our folder, a free port), FL answers by mailbox
r = m.fl_bridge_setup(action="use_input", port="MPK mini 3", restart_fl=True)
assert r["done"] is True and calls["ping"] >= 1 and calls["installed"], r
assert REG[key]["ScriptFolder"][1] == "fl-headless" and REG[key]["Type"][1] == -3 and REG[key]["Enabled"][1] == 1, REG[key]
assert REG[key]["Port"][1] not in (-1, 3), REG[key]                       # not the Akai's port number
assert m._state()["bridge_input"]["port"] == "MPK mini 3"
# setup no longer asks for a virtual port
# undo: the input's old values come back, the mode is forgotten
r = m.fl_bridge_setup(action="undo_input", restart_fl=True)
assert r["done"] is True and REG[key]["ScriptFolder"][1] == "" and REG[key]["Type"][1] == 0 and REG[key]["Port"][1] == -1, REG[key]
assert not m._state().get("bridge_input")
r = m.fl_bridge_setup(action="undo_input", restart_fl=True)
assert r["done"] is False, r
# the mailbox never answers: reported with the way back, nothing claimed
m._mailbox_alive = lambda max_age=5.0: False
m.time_sleep = None
import time as _t
_orig_sleep = m.time.sleep
m.time.sleep = lambda s: None
_orig_time = m.time.time
clock = [1000.0]
m.time.time = lambda: clock.__setitem__(0, clock[0] + 5) or clock[0]
try:
    r = m.fl_bridge_setup(action="use_input", port="MPK mini 3", restart_fl=True)
    assert r["done"] is False and "undo_input" in r["why"], r
finally:
    m.time.sleep = _orig_sleep; m.time.time = _orig_time
import shutil
shutil.rmtree(tmp, True)
print("bridge input ok")
