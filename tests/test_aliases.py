"""Common other names for arguments are accepted (path -> project, from_bar -> start_bar ...); ambiguous ones aren't."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import fl_headless_mcp as m  # noqa: E402
T = m.TOOLS
a = m._check_args("fl_open", {"path": "C:/x.flp"}, T["fl_open"])
assert a == {"project": "C:/x.flp"}, a
a = m._check_args("fl_render", {"project": "x.flp", "from_bar": 3, "to_bar": 9}, T["fl_render"])
assert a.get("start_bar") == 3 and a.get("end_bar") == 9 and "from_bar" not in a, a
try:
    m._check_args("fl_open", {"projetc": "x"}, T["fl_open"]); raise SystemExit("typo must be refused")
except ValueError as e:
    assert "did you mean project" in str(e), e
try:                                                                  # both given: refused, never silently swapped
    m._check_args("fl_open", {"project": "a.flp", "path": "b.flp"}, T["fl_open"]); raise SystemExit("refuse")
except ValueError as e:
    assert "path" in str(e), e
print("aliases ok")
# flp_edit batches fl_load_preset as its file-level twin
import tempfile, shutil
tmp = tempfile.mkdtemp(); os_ = os
try:
    os.environ["FLH_ROOTS"] = tmp
    plan = m._plan_ops(os.path.join(tmp, "x.flp"), [{"tool": "fl_load_preset", "preset": os.path.join(tmp, "p.fst"),
                                                     "channel": 2}])
    assert plan[0][0] == "flp_load_plugin" and plan[0][2]["channel"] == 2 and plan[0][2]["preset"].endswith("p.fst"), plan
finally:
    shutil.rmtree(tmp, True)
print("plan ok")
# a project named loosely: a song name, a path relative to an allowed folder, no '.flp' -> the real file, said so
tmp = tempfile.mkdtemp()
try:
    os.environ["FLH_ROOTS"] = tmp
    base = os.path.join(HERE, "corpus", "base.flp")
    for rel in (("My Projects", "Late Rain", "Late Rain.flp"), ("My Projects", "Amber Light", "Amber Light.flp"),
                ("Old", "Amber Light", "Amber Light.flp"), ("My Projects", "Late Rain", "Backup", "Late Rain (overwritten).flp")):
        p = os.path.join(tmp, *rel); os.makedirs(os.path.dirname(p), exist_ok=True); shutil.copy(base, p)
    want = os.path.join(tmp, "My Projects", "Late Rain", "Late Rain.flp")
    for given in ("Late Rain", "Late Rain.flp", os.path.join("My Projects", "Late Rain", "Late Rain.flp"), want[:-4]):
        a = m._check_args("flp_info", {"path": given}, T["flp_info"])
        assert a["path"] == want and m._ARG_NOTES.items, (given, a)
    a = m._check_args("flp_info", {"path": want}, T["flp_info"])            # a real file: untouched, nothing noted
    assert a["path"] == want and not m._ARG_NOTES.items
    try:
        m._check_args("flp_info", {"path": "Amber Light"}, T["flp_info"]); raise SystemExit("two same-name projects: must ask")
    except ValueError as e:
        assert "several projects match" in str(e) and str(e).count("Amber Light.flp") == 2, e
    a = m._check_args("flp_info", {"path": "Nothing Like It"}, T["flp_info"])   # no match: left for the tool's error
    assert a["path"] == "Nothing Like It"
    a = m._check_args("flp_edit", {"project": "Late Rain", "output": "Late Rain v2.flp", "ops": []}, T["flp_edit"])
    assert a["output"] == os.path.join(os.path.dirname(want), "Late Rain v2.flp"), a          # next to its project
    a = m._check_args("flp_edit", {"project": want, "output": os.path.join(tmp, "x.flp"), "ops": []}, T["flp_edit"])
    assert a["output"] == os.path.join(tmp, "x.flp")                                         # absolute: untouched
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "flp_info",
                                                                                "arguments": {"path": "Late Rain"}}})
    assert "resolved" in r["content"][0]["text"] and not r.get("isError"), r
finally:
    shutil.rmtree(tmp, True)
print("project names ok")
