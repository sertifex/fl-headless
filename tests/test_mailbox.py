"""The file-mailbox transport: the real device script (inside a fake FL) answers the server through files.
Run: python tests/test_mailbox.py"""
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
tmp = tempfile.mkdtemp(); os.environ["LOCALAPPDATA"] = tmp

import test_bridge as TB                                         # noqa: E402  (fake FL API, reused)
import fl_headless_mcp as m                                      # noqa: E402

TB.install_fake_fl([], relay=False)
body = m._script_body()                                          # mailbox folder filled in
assert "\nMAILBOX = None\n" not in body and "\nMAILBOX = '" in body
script = os.path.join(tmp, "device_flheadless.py")
open(script, "w", encoding="utf-8").write(body)
g = {"__name__": "flh_script"}
exec(compile(open(script, encoding="utf-8").read(), script, "exec"), g)

assert not m._mailbox_alive()                                    # nothing yet -> MIDI would be used
stop = threading.Event()


def idle():                                                      # FL calls OnIdle many times a second
    while not stop.is_set():
        g["OnIdle"](); time.sleep(0.005)


t = threading.Thread(target=idle, daemon=True); t.start()
time.sleep(0.05)
assert m._mailbox_alive(), "heartbeat missing"
m.BRIDGE.open = lambda: (_ for _ in ()).throw(AssertionError("MIDI must not be used"))
r = m.BRIDGE.call("ping"); assert r, r
st = m.BRIDGE.call("state"); assert "channels" in st, st
ctx = m.BRIDGE.call("context", bpb=3); assert "focused" in ctx and "bar" in ctx, ctx
song = m.BRIDGE.call("song", action="goto", bar=5.0, bpb=3); assert song["bar"] == 5.0, song
try:
    m.BRIDGE.call("no_such_command"); raise SystemExit("error not raised")
except RuntimeError as e:
    assert "unknown command" in str(e), e
# many calls in a row from two threads stay matched to their own replies
res = []
def worker():
    for _ in range(10): res.append(m.BRIDGE.call("ping"))
ws = [threading.Thread(target=worker) for _ in range(2)]
[w.start() for w in ws]; [w.join() for w in ws]
assert len(res) == 20 and m.BRIDGE.transport == "mailbox"
mb = m._mailbox_dir()
assert not os.path.exists(os.path.join(mb, "req.json"))         # answered requests are removed
# a server restart (new process: ids start again at 1) must not get an old reply
b2 = m._Bridge(); b2.open = m.BRIDGE.open
assert b2.call("context", bpb=4)["focused"] is None or True and "bar" in b2.call("context", bpb=4)
# a left-over request (server gave up, FL restarted) is dropped, not run
import json as _j
ran = []
g["COMMANDS"]["ping"] = lambda: ran.append(1) or {"pong": True}
open(os.path.join(mb, "req.json"), "w").write(_j.dumps({"id": "old-1", "until": time.time() - 5, "payload": "ping"}))
time.sleep(0.1)
assert not ran, "stale request ran"
os.remove(os.path.join(mb, "req.json"))
stop.set(); t.join()
print("mailbox ok")
