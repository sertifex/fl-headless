"""2.2.1 layout: code in src/, the shipped launcher at the top starts it, setup spots files an unzip-over-an-old-
install left behind, and setup names the exact loopMIDI mistake in FL's MIDI settings (swapped ports, port numbers
that don't match). Run: python3 tests/test_setup_layout.py (no FL needed)."""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "src"); sys.path.insert(0, SRC)
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import flh_setup as S  # noqa: E402

# ---- layout: nothing importable left at the top; the launcher points at src/
top_py = sorted(f for f in os.listdir(REPO) if f.endswith(".py"))
assert top_py == ["fl_headless_launch.py", "install.py"], top_py
assert os.path.isfile(os.path.join(SRC, "fl_headless_mcp.py")) and os.path.isdir(os.path.join(SRC, "data"))
assert os.path.isfile(os.path.join(SRC, "fl_script", "device_flheadless.py"))
src = open(os.path.join(REPO, "fl_headless_launch.py"), encoding="utf-8").read()
assert '"src"' in src and "fl_headless_mcp.main()" in src, src
inst = open(os.path.join(REPO, "install.py"), encoding="utf-8").read()
i = inst.index("LAUNCH_SRC = "); ns = {}
exec(inst[i:inst.index("try:", i)], ns)                       # install.py writes exactly the shipped launcher
assert ns["LAUNCH_SRC"] == src, "install.py's launcher differs from the shipped fl_headless_launch.py"
# the shipped launcher finds the server from any working directory
code =("import sys, os; sys.path.insert(0, os.path.join(%r, 'src')); import fl_headless_mcp as m; "
        "print(m.SERVER['version'])" % REPO)
r = subprocess.run([sys.executable, "-c", code], cwd=tmp, capture_output=True, text=True,
                   env=dict(os.environ, FLH_UPDATE_CHECK="0", FLH_NO_WARM="1"))
assert r.returncode == 0 and r.stdout.strip() == m.SERVER["version"], (r.stdout, r.stderr[-400:])
# the mcpb launcher puts src/ on the path too
assert 'os.path.join(HERE, "src")' in open(os.path.join(REPO, "mcpb", "launch.py"), encoding="utf-8").read()

# ---- leftovers of an unzip over a pre-2.2.1 folder
fake = os.path.join(tmp, "fl-headless"); code_dir = os.path.join(fake, "src")
os.makedirs(os.path.join(code_dir, "data")); os.makedirs(os.path.join(fake, "data"))
for n in ("fl_headless_mcp.py", "flh_setup.py"):
    open(os.path.join(code_dir, n), "w").close(); open(os.path.join(fake, n), "w").close()
open(os.path.join(fake, "fl_headless_launch.py"), "w").close(); open(os.path.join(fake, "install.py"), "w").close()
open(os.path.join(fake, "my_notes.py"), "w").close()       # not ours: never listed
left = S._old_layout_leftovers(code_dir)
assert left == ["data/", "fl_headless_mcp.py", "flh_setup.py"], left
assert S._old_layout_leftovers(fake) == []                  # an old-layout install itself: nothing to say

# ---- loopMIDI: the exact mistake in FL's MIDI settings
P = S._loopmidi_fl_problem
ok = {"inputs": [{"port": "Flapi Request", "enabled": True, "port_number": 10}], "outputs": {"Flapi Response": 10}}
assert P(ok) is None
assert P(None) is None and P({"error": "no registry"}) is None
sw = P({"inputs": [{"port": "Flapi Response", "enabled": True, "port_number": 10}], "outputs": {"Flapi Request": 10}})
assert sw and "swapped" in sw[0] and "Request is the input" in sw[1], sw
mis = P({"inputs": [{"port": "Flapi Request", "enabled": True, "port_number": 10}], "outputs": {"Flapi Response": 3}})
assert mis and "don't match" in mis[0] and "(10)" in mis[1], mis
no_out = P({"inputs": [{"port": "Flapi Request", "enabled": True, "port_number": 10}], "outputs": {"Flapi Response": None}})
assert no_out and "no port" in no_out[0], no_out
assert P({"inputs": [{"port": "Flapi Request", "enabled": True, "port_number": 10}], "outputs": {}}) is None
off = P({"inputs": [{"port": "Flapi Request", "enabled": False, "port_number": 10}], "outputs": {"Flapi Response": 10}})
assert off and "Output list (top)" in off[1] and "Input list (bottom)" in off[1], off
none = P({"inputs": [], "outputs": {}})
assert none and "Controller type" in none[1], none
other = P({"inputs": [{"port": "My Keyboard", "enabled": True, "port_number": 1}], "outputs": {}})
assert other is None                                         # the script on another enabled input works too
assert "Request is the input, Response is the output" in S.LOOPMIDI_CLICK

# ---- the docs carry the same steps
doc = open(os.path.join(REPO, "docs", "install.md"), encoding="utf-8").read()
assert "#### loopMIDI, step by step" in doc and "Request goes in Input, Response goes in Output" in doc
assert "docs/install.md#loopmidi-step-by-step" in open(os.path.join(REPO, "README.md"), encoding="utf-8").read()
print("ok")
