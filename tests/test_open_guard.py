"""Writers aimed at the project open in FL go through _with_open_project; others run directly."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fl_headless_mcp as m
seen = []
m._with_open_project = lambda project, output, fn: (seen.append(output), fn())[1]
for name in m._OPEN_PROJECT_GUARD:
    assert getattr(m.TOOLS[name][0], "__wrapped__", None), name
f = m._guard_open_project(lambda **kw: "ran", "project", "output")
assert f(project="a.flp", output="b.flp") == "ran" and seen == ["b.flp"]
g = m._guard_open_project(lambda **kw: "ran", "path", None)
assert g(path="c.flp", bpm=120) == "ran" and seen[-1] == "c.flp"
info = m._guard_open_project(m.flp_project_info, "path", "output")
seen.clear()
try:
    info(path="nope.flp")
except Exception:
    pass
assert seen == [], "reading project info must not reload FL"
print("ok")
