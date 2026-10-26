"""2.0.0 release-review fixes that need no FL: relative FLH_ROOTS entries ignored and reported; flh_tools(name=[...])
through the dispatcher; required arguments named with a short usage (no Python TypeError); fl_review live mode off
Windows says so; flh_run step shapes ({name, arguments}, args as a JSON string); 'auto' for 'default: auto' booleans;
the save wait follows a slow save (up to a cap) and a twin file proves the open project only when it alone changed;
the roots scan skips FL's backup copies and stops at its limit. Run: python3 tests/test_release_fixes.py"""
import json, os, shutil, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="flh_relfix_")
ROOT = os.path.join(TMP, "root"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = f"{ROOT}; Music ;./rel"
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "local")
import fl_headless_mcp as m  # noqa: E402
FIX = os.path.join(HERE, "corpus", "base.flp")


def call(name, args):
    r = m.handle({"method": "tools/call", "params": {"name": name, "arguments": args}})
    return r.get("isError", False), r["content"][0]["text"]


# ---- L1: relative entries are ignored (never resolved against the client's working folder), and reported
assert m.roots() == [os.path.normcase(os.path.abspath(ROOT))], m.roots()
assert m.relative_roots() == ["Music", "./rel"], m.relative_roots()
s = m.TOOLS["flh_setup"][0]()
assert s["writable_roots"]["ignored_not_absolute"] == ["Music", "./rel"], s["writable_roots"]
assert any("not absolute" in n for n in s["next_steps"]), s["next_steps"]
try:
    m.writable(os.path.join(os.getcwd(), "Music", "x.flp")); raise SystemExit("a relative root must not be writable")
except PermissionError:
    pass
print("relative roots ok")

# ---- M1: flh_tools(name=[...]) through the dispatcher (string or array)
err, txt = call("flh_tools", {"name": ["fl_render", "flp_info"]})
assert not err and "fl_render" in txt and "flp_info" in txt, txt[:300]
err, txt = call("flh_tools", {"name": "flp_info"})
assert not err and "flp_info" in txt
assert m.TOOLS["flh_tools"][2]["properties"]["name"]["type"] == ["string", "array"]

# ---- M5: required arguments are named, with the tool's short usage, before the tool runs
for tool, arg in (("flp_info", "path"), ("flp_remove_channels", "project"), ("flp_score", "project"),
                  ("flh_call", "tool"), ("flh_run", "steps"), ("flp_clashes", "project")):
    err, txt = call(tool, {})
    assert err and f"{tool} needs {arg}" in txt and f"Usage: {tool}({arg}" in txt, (tool, txt)
    assert "TypeError" not in txt and "KeyError" not in txt and "NoneType" not in txt, txt
# not required after all: a guarded writer's output (defaults to its input), a parameter with a default (fl_close)
assert m._check_args("flp_replace_part", {"project": FIX, "channel": 0}, m.TOOLS["flp_replace_part"])
err, txt = call("fl_close", {})
assert err and "needs confirm" not in txt, txt
err, txt = call("flp_info", {"path": "  "})
assert err and "flp_info needs path" in txt, txt
# through flh_call too
err, txt = call("flh_call", {"tool": "flp_info", "args": {}})
assert err and "flp_info needs path" in txt, txt
if not m.IS_WINDOWS:                                              # fl_review live mode: the Windows-only note
    err, txt = call("fl_review", {})
    assert err and "Windows only" in txt and "audio=" in txt and "windll" not in txt, txt
print("arguments ok")

# ---- L4: 'auto' where a boolean's description says 'default: auto' -> the tool's own default (None)
assert m._check_args("fl_review", {"final": "auto"}, m.TOOLS["fl_review"])["final"] is None
assert m._check_args("flp_perform_setup", {"project": FIX, "quantize": "auto"},
                     m.TOOLS["flp_perform_setup"])["quantize"] is None

# ---- M6: flh_run step shapes
song = os.path.join(ROOT, "song.flp"); shutil.copy(FIX, song)
r = m.TOOLS["flh_run"][0](steps=[{"name": "flp_info", "arguments": {"path": song}},
                                 {"tool": "flp_info", "args": json.dumps({"path": song})},
                                 json.dumps({"tool": "flp_info", "args": {"path": song}})])
assert r["ok"] and r["ran"] == 3 and all("result" in x for x in r["steps"]), r
err, txt = call("flh_run", {"steps": [{"name": "flp_info", "arguments": json.dumps({"path": song})}]})
assert not err and json.loads(txt)["ok"] is True, txt[:300]
for bad, why in (([{"args": {}}], "no tool named"), ([{"tool": "flp_info", "args": "{nope"}], "isn't JSON"),
                 ([{"tool": "flp_info", "args": [1]}], "must be an object"), ([5], "each step is")):
    try:
        m.TOOLS["flh_run"][0](steps=bad); raise SystemExit(f"must refuse {bad}")
    except ValueError as e:
        assert why in str(e) and "each step is" in str(e), e
print("flh_run shapes ok")

# ---- M2: a save that keeps writing past wait_s is waited for (the deadline moves on), up to the cap
proj = os.path.join(ROOT, "slow.flp"); open(proj, "wb").write(b"x" * 10)
before = m._file_sig(proj)


def slow_writer(total_s, step_s=0.12):
    t0 = time.time(); n = 10
    while time.time() - t0 < total_s:
        time.sleep(step_s); n += 1000
        with open(proj, "ab") as f:
            f.write(b"y" * 1000)


th = threading.Thread(target=slow_writer, args=(2.5,)); th.start()
t0 = time.time(); got = m._wait_saved_any({proj: before}, 1.0); took = time.time() - t0; th.join()
assert got == proj and took > 2.4, (got, took)
before = m._file_sig(proj)
th = threading.Thread(target=slow_writer, args=(3.0,)); th.start()
t0 = time.time(); got = m._wait_saved_any({proj: before}, 0.5, cap_s=1.2); took = time.time() - t0; th.join()
assert got is None and took < 2.0, (got, took)                   # still writing at the cap: no proof
print(f"slow save ok ({took:.1f} s cap)")

# ---- L6: a twin proves the open file only when it alone changed; the project itself wins when it changed
a = os.path.join(ROOT, "a", "Beat.flp"); b = os.path.join(ROOT, "b", "Beat.flp"); c = os.path.join(ROOT, "c", "Beat.flp")
for p in (a, b, c):
    os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(b"0")


def touch_later(*paths, delay=0.2):
    def run():
        time.sleep(delay)
        for p in paths:
            with open(p, "wb") as f:
                f.write(b"saved " + p.encode())
    th_ = threading.Thread(target=run); th_.start(); return th_


def sigs():
    return {p: m._file_sig(p) for p in (a, b, c)}


time.sleep(0.05)
th = touch_later(b); got = m._wait_saved_any(sigs(), 1.5); th.join()
assert got == b, got                                              # only the twin changed: FL has the twin
time.sleep(0.05)
th = touch_later(b, a); got = m._wait_saved_any(sigs(), 1.5); th.join()
assert got == a, got                                              # the project changed too: it is the proof
time.sleep(0.05)
th = touch_later(b, c); got = m._wait_saved_any(sigs(), 1.0); th.join()
assert got is None, got                                           # two twins at once: proves nothing
print("twin rule ok")

# ---- L5: the roots scan skips FL's backup copies / own folders, is cached, and stops at its limit
for rel in ("x/Beat.flp", "x/Backup/Beat.flp", "x/Beat (overwritten at 2251h).flp", "x/Beat (autosave).flp",
            "y/Other.flp"):
    p = os.path.join(ROOT, rel); os.makedirs(os.path.dirname(p), exist_ok=True); shutil.copy(FIX, p)
m._GUESS_CACHE.clear()
files, complete = m._scan_flps()
names = sorted(os.path.relpath(f, ROOT) for f in files)
assert complete and not any("Backup" in n or "overwritten" in n or "autosave" in n for n in names), names
assert os.path.join("x", "Beat.flp") in names and os.path.join("y", "Other.flp") in names, names
assert m._is_backup_copy(os.path.join(ROOT, "x", "Backup", "Beat.flp"))
assert m._is_backup_copy(os.path.join(ROOT, "Beat (overwritten at 2251h).flp"))
assert not m._is_backup_copy(os.path.join(ROOT, "x", "Beat.flp"))
assert sorted(m._find_by_stem("Beat")) == sorted([a, b, c, os.path.join(ROOT, "x", "Beat.flp")]), m._find_by_stem("Beat")
new = os.path.join(ROOT, "y", "Fresh.flp"); shutil.copy(FIX, new)
m._GUESS_CACHE["\0scan"] = (time.time() - 5, *m._GUESS_CACHE["\0scan"][1:])   # a scan from 5 s ago
assert m._find_by_stem("Fresh") == [new]                          # a miss looks again
m._GUESS_CACHE.clear(); m.FLP_SCAN_MAX = 3
files, complete = m._scan_flps()
assert len(files) <= 3 and not complete, (len(files), complete)
m.FLP_SCAN_MAX = 20000; m._GUESS_CACHE.clear()
print("ok")

# ["array","string"] arguments: a JSON list sent as text is the list (fl_pc_check(areas='["a", "b"]') ran nothing)
_a = m._check_args("fl_pc_check", {"areas": '["lazy.big_project", "sound.shape"]'}, m.TOOLS["fl_pc_check"])["areas"]
assert _a == ["lazy.big_project", "sound.shape"], _a
assert m._check_args("fl_pc_check", {"areas": "core"}, m.TOOLS["fl_pc_check"])["areas"] == "core"
print("array-or-string args ok")
