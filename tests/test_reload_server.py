"""flh_reload swaps in code from disk, keeps BRIDGE/JOBS, and the dispatcher serves the new code."""
import os, sys, shutil, tempfile, json, io
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); src = os.path.join(os.path.dirname(HERE), "src", "fl_headless_mcp.py")
copy = os.path.join(tmp, "fl_headless_mcp.py"); shutil.copy(src, copy)
import importlib.util
spec = importlib.util.spec_from_file_location("flh_main", copy); m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m); sys.modules["flh_main"] = m          # as the launcher imports it
sent = []; m._send = lambda msg: sent.append(msg)
bridge = m.BRIDGE; m.JOBS["x"] = {"status": "running"}
open(copy, "a", encoding="utf-8").write('\nSERVER["version"] = "9.9.9"\nTOOLS["zz_new"] = (lambda: {"hi": 1}, "new", _schema())\n')
buf = io.StringIO(); real_out = sys.stdout; sys.stdout = buf     # the routed send writes the real stdout
try:
    r = m.flh_reload()
finally:
    sys.stdout = real_out
sent += [json.loads(x) for x in buf.getvalue().splitlines() if x.strip().startswith("{")]
assert r["version"] == "9.9.9" and "zz_new" in r["tools_added"], r
live = sys._flh_live
assert live["BRIDGE"] is bridge and live["JOBS"]["x"]["status"] == "running"
out = m.handle({"method": "tools/call", "params": {"name": "zz_new", "arguments": {}}})
got = json.loads(out["content"][0]["text"]); assert got["hi"] == 1 and "took_s" in got, out
assert any(x.get("method") == "notifications/tools/list_changed" for x in sent)
open(copy, "a", encoding="utf-8").write("\ndef broken(:\n")
try:
    m.flh_reload(); raise SystemExit("syntax error must not swap")
except SyntaxError:
    pass
assert sys._flh_live is live, "old code keeps serving after a failed reload"
# the running loop's own handle now goes through the live code (new dispatcher, not only new tools)
assert getattr(m, "_FLH_ROUTED", False) and m.handle.__name__ == "routed_handle"
live["handle"] = lambda req: {"via": "live"}
assert m.handle({"method": "ping"}) == {"via": "live"}
del sys._flh_live
print("ok")
