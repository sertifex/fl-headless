"""flh_setup (check / apply, neutral profile, edition storage) and flh_run (order, $prev, stop_on_error,
no recursion, compaction) and the Windows-only guard. Run: python3 tests/test_setup.py (no FL needed)."""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
for k in ("FLH_FL_EDITION", "FLH_FL_EXE", "FL_EXE"):
    os.environ.pop(k, None)
import fl_headless_mcp as m  # noqa: E402
T = m.TOOLS
assert "flh_setup" in T and "flh_run" in T, sorted(T)[:5]
assert m.SERVER["version"] == "2.1.2"

# ---- check: a fresh PC
r = T["flh_setup"][0]()
assert r["ready"] is False and r["profile"]["exists"] is False and r["fl_edition"]["edition"] is None, r
assert r["profile"]["finish"] == {"lufs": -14.0, "true_peak": -1.0, "bits": 24}, r["profile"]
assert any("FL edition" in s for s in r["next_steps"]) and any("profile" in s for s in r["next_steps"]), r["next_steps"]
if os.name != "nt":
    assert r["fl"]["supported"] is False
# the guided checklist: 7 steps in order, each done / needs you / optional, ONE next action when the user is needed
steps = r["steps"]
assert [s["step"] for s in steps] == ["FL Studio", "Song folders", "Connection to FL", "FL edition", "fl-headless look",
                                      "Free extras", "Ready"], steps
for s in steps:
    assert s["status"] in ("done", "needs you", "optional") and (s["status"] != "needs you" or
                                                                 isinstance(s.get("next"), str)), s
assert steps[3]["status"] == "needs you" and "Help > About" in steps[3]["next"], steps[3]          # the one question
assert steps[1]["status"] == "needs you" and steps[1].get("auto"), steps[1]                         # apply makes it
assert steps[5]["status"] == "optional" and steps[6]["status"] == "needs you", steps
assert steps[6]["next"].startswith("FL edition: ") and steps[6]["do"].startswith("flh_setup(action='apply'"), steps[6]
if os.name != "nt":
    assert [steps[i]["status"] for i in (0, 2, 4)] == ["optional"] * 3 and "Windows" in steps[0]["say"], steps

# ---- apply: neutral profile + edition; nothing about writable folders changes
before = m.roots()
a = T["flh_setup"][0](action="apply", fl_edition="Producer Edition", collection="Album", roots=[tmp, os.path.join(tmp, "nope")])
assert m.roots() == before, "a tool call must never widen FLH_ROOTS"
p = a["profile"]
assert p["finish"]["lufs"] == -14.0 and p["finish"]["bits"] == 24 and p["fl_edition"] == "producer", p
assert m._state()["fl_edition"] == "producer" and m._state()["collection"] == "Album"
assert any("don't exist" in n for n in a["notes"]), a["notes"]
r2 = T["flh_setup"][0]()
assert r2["fl_edition"]["edition"] == "producer" and r2["profile"]["exists"], r2
assert [s["status"] for s in r2["steps"]][1:4:2] == ["done", "done"] and r2["steps"][6]["status"] == "done", r2["steps"]
assert "Album" in r2["steps"][1]["say"] and r2["steps"][6]["say"].startswith("Ready for song files"), r2["steps"]
assert a["steps"] == r2["steps"] or len(a["steps"]) == 7
a2 = T["flh_setup"][0](action="apply", finish={"lufs": -16})            # fill in, keep the rest
assert a2["profile"]["finish"] == {"lufs": -16, "true_peak": -1.0, "bits": 24, "fade_s": 2.5}, a2["profile"]
try:
    T["flh_setup"][0](action="apply", fl_edition="deluxe"); raise SystemExit("bad edition accepted")
except ValueError:
    pass
import flh_catalog as C  # noqa: E402
assert C.sound_ok("Sytrus") is True and C.sound_ok("Harmor") == "trial"
# audio_finish takes the profile's target when none is given
assert m._finish_target()["lufs"] == -16

# ---- flh_run
calls = []
T["zz_a"] = (lambda x=1: (calls.append(("a", x)), {"output": f"file{x}.flp", "took_s": 1.0, "list": [7, 8]})[1], "", {})
T["zz_b"] = (lambda path=None, n=None: (calls.append(("b", path, n)), {"got": path, "n": n})[1], "", {})
T["zz_err"] = (lambda: 1 / 0, "", {})
run = T["flh_run"][0]
res = run([{"tool": "zz_a", "args": {"x": 3}}, {"tool": "zz_b", "args": {"path": "$prev.output", "n": "$prev.list[1]"}}])
assert res["ok"] and res["ran"] == 2, res
assert "took_s" not in res["steps"][0]["result"] and res["steps"][1]["result"] == {"got": "file3.flp", "n": 8}, res
res = run([{"tool": "zz_err"}, {"tool": "zz_a"}])
assert not res["ok"] and res["ran"] == 1 and "ZeroDivisionError" in res["steps"][0]["error"] and res["stopped"], res
res = run([{"tool": "zz_err"}, {"tool": "zz_a"}], stop_on_error=False)
assert not res["ok"] and res["ran"] == 2 and res["steps"][1]["result"]["output"] == "file1.flp", res
res = run([{"tool": "flh_run", "args": {"steps": []}}])
assert not res["ok"] and "can't run inside" in res["steps"][0]["error"], res
res = run([{"tool": "zz_b", "args": {"path": "$prev.output"}}])
assert "no previous result" in res["steps"][0]["error"], res
res = run([{"tool": "zz_a"}, {"tool": "zz_b", "args": {"path": "$prev.missing"}}])
assert "has no missing" in res["steps"][1]["error"], res
out = m.handle({"method": "tools/call", "params": {"name": "flh_run", "arguments": {"steps": [{"tool": "zz_a"}]}}})
assert json.loads(out["content"][0]["text"])["ok"] is True

# ---- Windows-only tools say so off Windows (and stay usable on Windows)
if os.name != "nt":
    out = m.handle({"method": "tools/call", "params": {"name": "fl_ping", "arguments": {}}})
    assert out.get("isError") and "Windows only" in out["content"][0]["text"], out
    res = run([{"tool": "fl_transport", "args": {"action": "play"}}])
    assert "Windows only" in res["steps"][0]["error"], res
    assert not m._windows_only("fl_help") and not m._windows_only("flp_info") and m._windows_only("files_recycle")
    # fl_status still answers
    assert m.fl_status()["fl_running"] is False

# ---- universality helpers
assert m._safe_stem(os.path.join(tmp, "Café Été — №5.flp")) == "Caft5"
assert m._is_untitled("Projekt 3 - FL Studio 2026") and m._is_untitled("Untitled - FL Studio 21")
assert not m._is_untitled("My Song - FL Studio 2026")
assert m._dialog_kind("Speichern unter", []) == "save_as" and m._dialog_kind("FL Studio", ["Änderungen speichern?"]) == "save_changes"
assert m._fl_version_rank(r"C:\Program Files\Image-Line\FL Studio 21\FL64.exe")[0] == 2021
assert m._fl_version_rank(r"C:\Program Files\Image-Line\FL Studio 2026\FL64.exe")[0] == 2026
body = m._script_body()
assert body.isascii()
print("setup ok")
