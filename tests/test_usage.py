"""Local tool usage log (usage.json in the state folder): counts per tool from the dispatcher (handle, flh_run steps,
flh_call's inner tool), errors (raised or an 'error' result), seconds, last day; writes throttled (none per call),
merged with what's on disk, bounded; never arguments / paths; FLH_NO_USAGE=1 opt-out; flh_usage output; bug report.
Run: python3 tests/test_usage.py"""
import json, os, sys, tempfile, time, zipfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ.pop("FLH_NO_USAGE", None)
import fl_headless_mcp as m  # noqa: E402

F = m._usage_file()
assert F == os.path.join(os.path.dirname(m.STATE), "usage.json")
SECRET = os.path.join(tmp, "My Secret Song", "Secret Song.flp")


def boom(path=None):
    raise FileNotFoundError(path)


m.TOOLS["zz_ok"] = (lambda path=None: {"ok": True, "path": path}, "", {})
m.TOOLS["zz_err"] = (boom, "", {})
m.TOOLS["zz_soft"] = (lambda path=None: {"error": f"can't read {path}"}, "", {})


def call(name, **a):
    return m.handle({"method": "tools/call", "params": {"name": name, "arguments": a}})


m.USAGE_WRITE_S = 30.0
for _ in range(3):
    assert not call("zz_ok", path=SECRET).get("isError")
for _ in range(2):
    assert call("zz_err", path=SECRET)["isError"]
call("zz_soft", path=SECRET)
call("zz_nope")                                                   # unknown tool: not counted
m.flh_run([{"tool": "zz_ok", "args": {"path": SECRET}}, {"tool": "zz_err", "args": {}}], stop_on_error=False)
m.flh_run([{"tool": "no_such_tool_xyz", "args": {}}])             # unknown inside flh_run: not counted
r = call("flh_call", tool="zz_ok", args={"path": SECRET})         # flh_call counts itself AND the inner tool
assert not r.get("isError"), r
assert not os.path.exists(F), "no disk write per call (throttled)"

t = m._usage_totals()["tools"]
assert t["zz_ok"]["calls"] == 5 and t["zz_ok"]["errors"] == 0, t["zz_ok"]
assert t["zz_err"]["calls"] == 3 and t["zz_err"]["errors"] == 3, t["zz_err"]
assert t["zz_soft"]["calls"] == 1 and t["zz_soft"]["errors"] == 1
assert t["flh_call"]["calls"] == 1 and "zz_nope" not in t and "no_such_tool_xyz" not in t
assert t["zz_ok"]["last"] == time.strftime("%Y-%m-%d") and t["zz_ok"]["secs"] >= 0

# ---- throttled write: not before USAGE_WRITE_S, then on the next call
m._USAGE["last_write"] = time.time()
call("zz_ok"); assert not os.path.exists(F)
m._USAGE["last_write"] = time.time() - 31
call("zz_ok"); assert os.path.isfile(F), "due -> written on this call"
d = json.load(open(F, encoding="utf-8"))
assert d["v"] == 1 and d["since"] == time.strftime("%Y-%m-%d") and d["tools"]["zz_ok"]["calls"] == 7, d
assert not m._USAGE["pending"]
raw = open(F, encoding="utf-8").read()
assert "Secret" not in raw and tmp not in raw and "path" not in raw, "never arguments or paths"
assert len(raw) < 4000
mt = os.stat(F).st_mtime_ns
call("zz_ok"); assert os.stat(F).st_mtime_ns == mt, "just written: the next call doesn't write again"

# ---- merge with what's on disk (another fl-headless server on this PC) + at-exit flush
d["tools"]["zz_ok"]["calls"] += 100; d["tools"]["fl_render"] = {"calls": 2, "errors": 1, "secs": 9.5, "last": "2026-01-02"}
json.dump(d, open(F, "w", encoding="utf-8"))
m._usage_at_exit()                                                # flushes the pending zz_ok call
d2 = json.load(open(F, encoding="utf-8"))
assert d2["tools"]["zz_ok"]["calls"] == 108 and d2["tools"]["fl_render"]["calls"] == 2, d2["tools"]
assert m._usage_flush() is False                                  # nothing pending: no write

# ---- flh_usage
u = m.flh_usage(top=3)
assert u["enabled"] and u["calls"] >= 112 and u["tools_total"] == len(m.TOOLS), u
assert [x["tool"] for x in u["most_used"]][:1] == ["zz_ok"] and len(u["most_used"]) == 3, u["most_used"]
ep = [x["tool"] for x in u["error_prone"]]
assert ep[0] in ("zz_err", "zz_soft") and "fl_render" in ep and "zz_ok" not in ep, u["error_prone"]
assert u["never_used"] == len(m.TOOLS) - u["tools_used"] and "never_used_tools" not in u
uu = m.flh_usage(unused=True)
never = uu["never_used_tools"].split(", "); rare = uu["rarely_used_tools"].split(", ")
assert "flp_compose" in never and "zz_ok" not in never and "fl_render" in rare and "zz_soft" in rare, (rare,)
assert len(never) == uu["never_used"]
via = json.loads(call("flh_usage", top="2", unused="true")["content"][0]["text"])
assert len(via["most_used"]) == 2 and "never_used_tools" in via
assert m.tool_group("flh_usage") != "other"

# ---- robustness: corrupt / hostile file, bounded size
open(F, "w").write("{not json")
assert "zz_ok" not in m._usage_totals()["tools"] and "zz_ok" not in str(m.flh_usage())
json.dump({"tools": {"../../evil": {"calls": 5}, "ok_name": {"calls": "x"}, "zz_ok": {"calls": 2}}, "since": 5},
          open(F, "w"))
t = m._usage_totals()["tools"]
assert "../../evil" not in t and "ok_name" not in t and t["zz_ok"]["calls"] == 2, t
big = {"tools": {f"t{i}": {"calls": 1, "errors": 0, "secs": 0, "last": f"2026-01-{1 + i % 28:02d}"} for i in range(40)}}
old_max = m.USAGE_MAX_TOOLS; m.USAGE_MAX_TOOLS = 10
assert len(m._usage_merge(big, {})["tools"]) == 10
m.USAGE_MAX_TOOLS = old_max

# ---- bug report: counts only
call("zz_err")
r = m.flh_bug_report(errors=2)
z = zipfile.ZipFile(r["zip"]); ub = json.loads(z.read("usage.json"))
assert "usage.json" in r["contains"] and ub["top"]["zz_ok"] >= 2 and ub["errors"]["zz_err"] == 1, ub
assert "Secret" not in z.read("usage.json").decode()

# ---- opt-out
os.environ["FLH_NO_USAGE"] = "1"
before = m._usage_totals()["tools"].get("zz_ok", {}).get("calls")
m._USAGE["last_write"] = time.time() - 100
mt = os.stat(F).st_mtime_ns
call("zz_ok"); m.flh_run([{"tool": "zz_ok", "args": {}}])
assert m._usage_totals()["tools"].get("zz_ok", {}).get("calls") == before and os.stat(F).st_mtime_ns == mt
u = m.flh_usage(); assert u["enabled"] is False and "note" in u
assert m._usage_summary() == {"enabled": False}
del os.environ["FLH_NO_USAGE"]

# ---- cost: the per-call hook is in-memory only
m._USAGE["last_write"] = time.time()
t0 = time.perf_counter()
for _ in range(20000):
    m._usage_note("zz_ok", 0.01)
per = (time.perf_counter() - t0) / 20000
assert per < 50e-6, f"{per * 1e6:.1f} us per call"
print(f"usage hook: {per * 1e6:.2f} us per call")
print("ok")
