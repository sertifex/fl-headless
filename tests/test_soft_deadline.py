"""A tool call that runs longer than the soft deadline (FLH_CALL_SOFT_S) is handed over as a job: the client gets
{"job": ...} at once, fl_job returns the result (or the error) when it's done, progress shows meanwhile, and calls
that can't safely run beside it are refused until it's done. Run: python3 tests/test_soft_deadline.py"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_soft_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ["FLH_CALL_SOFT_S"] = "0.4"
import fl_headless_mcp as m  # noqa: E402

S = m._schema


def slow(seconds=1.2, fail=False):
    for i in range(4):
        m._job_progress(step="working", n=i)
        time.sleep(float(seconds) / 4)
    if fail:
        raise ValueError("it broke")
    return {"done": True, "slept": seconds}


m.TOOLS["zz_slow"] = (slow, "test", S(seconds=m._NUM("", True), fail=m._BOOL("", True)))


def call(name, **args):
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    text = " ".join(c.get("text", "") for c in r["content"] if c.get("type") == "text")
    return r, text


# quick calls: answered directly, as before
r, t = call("zz_slow", seconds=0.05)
assert not r.get("isError") and json.loads(t)["done"] is True, t
# a long call: handed over as a job
t0 = time.time()
r, t = call("zz_slow", seconds=1.6)
j = json.loads(t)
assert time.time() - t0 < 1.0 and j["status"] == "running" and j["job"].startswith("zz_slow-"), (t, time.time() - t0)
jid = j["job"]
time.sleep(0.7)
st = m.fl_job(jid, wait_s=0)
assert st["status"] == "running" and st["progress"]["step"] == "working", st          # progress shows
# a call that isn't safe beside it is refused (nothing run), a read-only one runs
r2, t2 = call("zz_slow", seconds=0.01)
assert r2.get("isError") and "still running" in t2 and jid in t2, t2
r3, t3 = call("flh_tools", query="render")
assert not r3.get("isError"), t3
# fl_job returns the result the moment it's done
st = m.fl_job(jid, wait_s=10)
assert st["status"] == "done" and st["result"]["done"] is True and st["result"]["slept"] == 1.6, st
r4, t4 = call("zz_slow", seconds=0.01)                                                  # free again
assert not r4.get("isError"), t4
# a long call that fails: the job says so
r, t = call("zz_slow", seconds=1.0, fail=True)
jid2 = json.loads(t)["job"]
st = m.fl_job(jid2, wait_s=10)
assert st["status"] == "failed" and "it broke" in st["error"], st
# FLH_CALL_SOFT_S=0 turns it off: the call simply takes as long as it takes
os.environ["FLH_CALL_SOFT_S"] = "0"
r, t = call("zz_slow", seconds=0.8)
assert json.loads(t)["done"] is True, t
import shutil
shutil.rmtree(tmp, True)
print("soft deadline ok")
