"""The Claude Desktop extension (fl-headless.mcpb): the manifest is a valid MCPB v0.4 uv bundle whose version is the
server's, Claude's folder arguments become the FLH_ROOTS the server uses, tools/make_mcpb.py builds a zip with the
manifest, pyproject.toml (numpy) and the server - and nothing dev-only or private - and 'set me up' does the FL side
on a simulated Windows PC: the bridge script into FL's Hardware folder, FL's MIDI settings over Windows' loopback
(FL closed: written at once, no waiting; FL open: only after the user's OK), or the one step in plain words.
Run: python3 tests/test_mcpb.py (no FL, no network)."""
import json, os, re, subprocess, sys, tempfile, time, zipfile
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, os.path.join(REPO, "tools")); sys.path.insert(0, os.path.join(REPO, "mcpb"))
tmp = tempfile.mkdtemp(prefix="flh_mcpb_t_")
HOME = os.path.join(tmp, "home", "user")
os.environ.update(LOCALAPPDATA=os.path.join(tmp, "local"), APPDATA=os.path.join(tmp, "roaming"), HOME=HOME,
                  USERPROFILE=HOME, FLH_NO_WARM="1")
for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial", "FLH_FL_USERDATA", "FLH_FL_EDITION", "FLH_FL_EXE",
          "FL_EXE", "FLH_ROOTS", "FLH_INSTALLED_BY"):
    os.environ.pop(k, None)
import launch as L  # noqa: E402  (mcpb/launch.py)
import make_mcpb as MB  # noqa: E402

# ---- 1. the committed manifest: JSON, the fields Claude needs, uv + numpy, folders as arguments (not one env var)
with open(os.path.join(REPO, "mcpb", "manifest.json"), encoding="utf-8") as f:
    man = json.load(f)
for k in ("manifest_version", "name", "version", "description", "author", "server"):
    assert man.get(k), k
assert man["manifest_version"] == "0.4" and man["name"] == "fl-headless" and man["license"] == "GPL-3.0-or-later"
assert man["server"]["type"] == "uv" and man["server"]["entry_point"] == "mcpb/launch.py", man["server"]
mc = man["server"]["mcp_config"]
assert mc["command"] == "uv" and mc["args"][:4] == ["run", "--directory", "${__dirname}", "mcpb/launch.py"], mc
assert mc["args"][-1] == "${user_config.song_folders}" and mc["env"]["FLH_INSTALLED_BY"] == "mcpb", mc
sf = man["user_config"]["song_folders"]
assert sf["type"] == "directory" and sf["multiple"] is True and sf["required"] is True and sf["default"], sf
assert man["compatibility"]["platforms"] == ["win32"] and "Windows" in man["long_description"]
assert "understand what's happening" in man["description"] and "stay in charge" in man["description"], man["description"]
with open(os.path.join(REPO, "mcpb", "pyproject.toml"), encoding="utf-8") as f:
    pp = f.read()
assert re.search(r'"numpy>=1\.\d+,<3"', pp) and 'requires-python = ">=3.10"' in pp and not re.search(r"(?m)^\[build-system\]", pp), pp
assert MB.check_manifest(dict(man, version="2.1.0"), {"mcpb/launch.py", "data/theme/logo.png", "pyproject.toml"}) == []
bad = json.loads(json.dumps(man)); bad["server"]["mcp_config"]["env"]["FLH_ROOTS"] = "${user_config.song_folders}"
bad["user_config"]["song_folders"]["enum"] = ["x"]; bad["nope"] = 1
errs = " | ".join(MB.check_manifest(bad, {"mcpb/launch.py", "data/theme/logo.png", "pyproject.toml"}))
assert "only args expand lists" in errs and "enum" in errs and "nope" in errs, errs
print("manifest ok")

# ---- 2. Claude's arguments -> FLH_ROOTS (';'-joined, what the server's roots() reads)
A, B = os.path.join(tmp, "songs a"), os.path.join(tmp, "FL; odd")
os.makedirs(A); os.makedirs(os.path.join(HOME, "Music"))
env = {"FLH_FL_EXE": "${user_config.fl_exe}", "FLH_INSTALLED_BY": "mcpb"}
refused = L.prepare_env([A, A + os.sep, "${HOME}/Music", "relative/songs", B, "${user_config.song_folders}", ""], env)
assert "FLH_FL_EXE" not in env, env                               # an optional setting left empty is dropped
assert env["FLH_ROOTS"] == ";".join([A, os.path.join(HOME, "Music")]), env["FLH_ROOTS"]
assert refused == ["relative/songs", B], refused                   # never guessed: relative / ';' in the name
assert env["FLH_INSTALLED_BY"] == "mcpb" and L.expand("${DOCUMENTS}/Image-Line").endswith(os.path.join("Documents", "Image-Line"))
# Claude Desktop saves the default '${DOCUMENTS}/Image-Line' as 'Documents\Image-Line' (PC 2026-10-25): mapped to the
# real Documents folder, not refused; other relative paths still are
env3 = {}; ref3 = L.prepare_env(["Documents\\Image-Line", "music/Piano", "Songs\\x"], env3)
assert env3["FLH_ROOTS"].split(";") == [os.path.join(HOME, "Documents", "Image-Line"),
                                        os.path.join(HOME, "Music", "Piano")] and ref3 == ["Songs\\x"], (env3, ref3)
env2 = {}; L.prepare_env([], env2)
assert env2["FLH_ROOTS"] == "" and env2["FLH_INSTALLED_BY"] == "mcpb", env2
os.environ["FLH_ROOTS"] = env["FLH_ROOTS"]
import fl_headless_mcp as m  # noqa: E402
assert m.roots() == [os.path.normcase(os.path.abspath(p)) for p in (A, os.path.join(HOME, "Music"))], m.roots()
m.writable(os.path.join(A, "x.flp"))                                # inside a picked folder: allowed
try:
    m.writable(os.path.join(REPO, "x.flp")); raise SystemExit("the extension folder must not be writable")
except PermissionError:
    pass
print("folders -> FLH_ROOTS ok")

# ---- 3. make_mcpb: a zip Claude can install, nothing dev-only or private in it
try:
    in_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=REPO, capture_output=True,
                            text=True).stdout.strip() == "true"
except OSError:
    in_git = False
if in_git:
    out = os.path.join(tmp, "dist")
    t0 = time.time()
    rep = MB.build(out_dir=out, worktree=True)
    assert not rep["errors"] and rep.get("out"), rep
    assert rep["out"] == os.path.join(out, f"fl-headless-{m.SERVER['version']}.mcpb"), rep["out"]
    assert "folder argument -> FLH_ROOTS" in rep["selftest"], rep["selftest"]
    with zipfile.ZipFile(rep["out"]) as z:
        names = z.namelist()
        bm = json.loads(z.read("manifest.json")); bp = z.read("pyproject.toml").decode()
        assert z.testzip() is None
    assert bm["version"] == m.SERVER["version"] and f'version = "{m.SERVER["version"]}"' in bp, (bm["version"], bp)
    for need in ("manifest.json", "pyproject.toml", "mcpb/launch.py", "fl_headless_mcp.py", "flh_setup.py",
                 "fl_script/device_flheadless.py", "data/theme/logo.png", "LICENSE", "NOTICE.md", bm["icon"]):
        assert need in names, need
    assert MB.check_manifest(bm, set(names)) == [], MB.check_manifest(bm, set(names))
    assert bm["tools"] and {t["name"] for t in bm["tools"]} <= set(m.TOOLS) and "flh_setup" in {t["name"] for t in bm["tools"]}
    for n in names:
        assert not n.startswith(("tests/", "review/", "tools/", ".claude/", ".venv/", "venv/", "server/lib/", "dist/")), n
        assert n not in ("install.bat", "install.py", "mcpb/manifest.json", "mcpb/pyproject.toml", ".gitignore"), n
        assert "__pycache__" not in n and not n.endswith((".pyc", ".mcpb", ".bak", ".log")), n
        assert os.path.basename(n) not in ("RELEASE" ".md", "fl_headless_launch.py", "uv.lock"), n
        if MB.MR is not None:                                       # the release's own exclusions hold too
            assert not MB.MR.excluded(n), (n, MB.MR.excluded(n))
    assert len(set(names)) == len(names) and names == sorted(names), "deterministic, sorted, no duplicates"
    print(f"make_mcpb ok ({len(names)} files, {rep['bytes'] // 1024} KB, {time.time() - t0:.1f} s)")
else:
    print("make_mcpb skipped (not a git checkout)")

# ---- 4. 'set me up' inside Claude on a simulated Windows PC (extension context: nobody ran install.py)
os.environ["FLH_INSTALLED_BY"] = "mcpb"
import flh_setup as S  # noqa: E402
ns = vars(m); T = m.TOOLS
UD = os.path.join(HOME, "Documents", "Image-Line", "FL Studio")
HW = os.path.join(UD, "Settings", "Hardware")
os.makedirs(os.path.join(UD, "Settings", "Themes"))
LOOP = "Default Basic App Loopback"
KEY = "Software\\Image-Line\\FL Studio\\Devices\\MIDI input\\" + LOOP
PC = {"running": False, "answers": False, "out": [], "in": [], "inputs": {}, "settings": [], "ping": 0}


class Bridge:
    ok = False
    ports_used = ()

    def ports(self):
        return {"midi_out": list(PC["out"]), "midi_in": list(PC["in"])}

    def choose(self, p):
        if "Flapi Request" in p["midi_out"]:
            return ("Flapi Request", "Flapi Response")
        return (LOOP, LOOP) if LOOP in p["midi_out"] else None

    def close(self):
        pass

    def call(self, cmd, **k):
        PC["ping"] += 1
        if not PC["answers"]:
            raise TimeoutError("no answer")
        return {"version": 4}


def settings_set(changes, restart_fl=False, reopen=True, close_fl=None):
    PC["settings"].append(list(changes))
    for c in changes:
        PC["inputs"].setdefault(c["key"], {})[c["name"]] = (4, c["value"])
    if PC["running"]:                                               # FL restarts and loads the script
        PC["answers"] = True
    return {"changed": [c["name"] for c in changes]}


ns.update({"IS_WINDOWS": True, "BRIDGE": Bridge(), "fl_exe": lambda: r"C:\Program Files\Image-Line\FL Studio 2026\FL64.exe",
           "fl_installs": lambda: [], "_fl_file_version": lambda: "26.1.4.5589", "fl_running": lambda: PC["running"],
           "_blank_project": lambda: None, "_hardware_dir": lambda: HW,
           "_userdata_pick": lambda refresh=False: {"path": UD, "layer": "registry", "why": "FL's setting",
                                                     "candidates": [{"path": UD, "exists": True}]},
           "_fl_userdata": lambda: UD, "_known_documents": lambda: [], "_reg_values": lambda *a, **k: {},
           "_fl_key": lambda: r"Software\Image-Line\FL Studio",
           "_reg_dump": lambda rel=None: dict(PC["inputs"]) if rel and rel.endswith("Devices") else {},
           "_fl_midi_key": lambda kind, port: KEY if kind == "input" else KEY.replace("MIDI input", "MIDI output"),
           "_loopback_test": lambda port=None: ({"port": LOOP, "loopback": True} if LOOP in PC["out"] else
                                                {"port": LOOP, "loopback": False, "why": "port not present"}),
           "fl_settings_set": settings_set, "_note_script_location": lambda: False, "_mailbox_alive": lambda: False})
S._uninstall_names = lambda: []

# no MIDI port at all: the script goes in, the one step is said plainly (Windows 11 loopback / loopMIDI / keyboard)
r = T["flh_setup"][0]()
assert r["installed_as"] == "claude_extension", r.get("installed_as")
a = T["flh_setup"][0](action="apply", fl_edition="producer")
script = os.path.join(HW, "fl-headless", "device_flheadless.py")
assert os.path.isfile(script) and any("script into FL" in d for d in a["done"]), a["done"]
with open(script, encoding="utf-8") as f:
    assert "fl-headless" in f.read(400)
c = a["connection"]
assert c["status"] == "needs you" and "MIDI Settings" in c["say"] and "loopMIDI" in c["say"], c
assert not PC["settings"], "nothing may change in FL's settings without a port"
st = {s["n"]: s for s in a["steps"]}
assert st[3]["status"] == "needs you" and st[4]["status"] == "done", a["steps"]

# an extension update ships a newer script: 'set me up' refreshes the one in FL
with open(script, "w", encoding="utf-8") as f:
    f.write("# name=fl-headless\n# fl-headless bridge (old version)\n")
a = T["flh_setup"][0](action="apply")
assert any("updated fl-headless's script" in d for d in a["done"]), a["done"]
with open(script, encoding="utf-8", newline="") as f:
    assert f.read() == m._script_body("# name=fl-headless")

# Windows' loopback appears, FL is open: nothing restarts FL without the user's OK
PC["out"], PC["in"], PC["running"] = [LOOP], [LOOP], True
r = T["flh_setup"][0]()
s3 = {s["n"]: s for s in r["steps"]}[3]
assert s3["status"] == "needs you" and "closes and reopens once" in s3["next"], s3
assert s3["do"] == "flh_setup(action='apply', restart_fl=true)" and not s3.get("auto"), s3
a = T["flh_setup"][0](action="apply")
assert a["connection"]["status"] == "needs your ok" and a["connection"]["do"].endswith("restart_fl=true)"), a
assert not PC["settings"], PC["settings"]
# ... the user says OK: FL's MIDI settings are set (the script on the loopback input) and FL answers
a = T["flh_setup"][0](action="apply", restart_fl=True)
assert a["connection"]["status"] == "connected", a["connection"]
names = {(ch["name"], ch["value"]) for ch in PC["settings"][-1]}
assert {("Enabled", "1"), ("Type", "-3"), ("ScriptFolder", "fl-headless")} <= names, names
assert {s["n"]: s for s in a["steps"]}[3]["status"] == "done", a["steps"]

# FL closed on a fresh PC with the loopback: written at once, no 45-second wait for an FL that isn't running
PC.update(running=False, answers=False, inputs={}, settings=[])
r = T["flh_setup"][0]()
s3 = {s["n"]: s for s in r["steps"]}[3]
assert s3["status"] == "needs you" and s3.get("auto") and s3["do"] == "flh_setup(action='apply')", s3
t0 = time.time()
a = T["flh_setup"][0](action="apply")
assert time.time() - t0 < 20, time.time() - t0
assert a["connection"]["status"] == "set" and "next time FL starts" in a["connection"]["say"], a["connection"]
assert any("loopback" in d for d in a["done"]) and len(PC["settings"]) == 1, (a["done"], PC["settings"])
s3 = {s["n"]: s for s in a["steps"]}[3]
assert s3["status"] == "done" and s3["say"] == "Set up: I connect when FL is open.", s3
assert m._state().get("bridge_ports") == [LOOP, LOOP]

# loopMIDI's two ports instead: the one FL click left (Controller type), in plain words
PC.update(out=["Flapi Request"], **{"in": ["Flapi Response"]}, inputs={}, settings=[])
a = T["flh_setup"][0](action="apply")
assert a["connection"]["status"] == "needs you" and "Controller type" in a["connection"]["say"], a["connection"]
assert "Controller type" in {s["n"]: s for s in a["steps"]}[3]["next"] and not PC["settings"]

# extension wording: song folders are picked in Claude's settings, not with an installer
os.environ["FLH_ROOTS"] = ""
s2 = {s["n"]: s for s in T["flh_setup"][0]()["steps"]}[2]
assert s2["status"] == "needs you" and "Settings > Extensions" in s2["next"] and "install" not in s2["next"].lower(), s2
os.environ["FLH_ROOTS"] = A
# the old manual install still in Claude's config next to the extension: said once, plainly
cfg = os.path.join(os.environ["APPDATA"], "Claude", "claude_desktop_config.json"); os.makedirs(os.path.dirname(cfg))
with open(cfg, "w", encoding="utf-8") as f:
    json.dump({"mcpServers": {"fl-headless": {"command": "python", "args": ["x.py"]}}}, f)
r = T["flh_setup"][0]()
s1 = {s["n"]: s for s in r["steps"]}[1]
assert s1["status"] == "needs you" and "installed twice" in s1["next"] and r["also_installed_manually"] == [cfg], s1
os.remove(cfg)
# without the extension variable the old wording stays (install.bat users)
os.environ.pop("FLH_INSTALLED_BY")
os.environ["FLH_ROOTS"] = ""
s2 = {s["n"]: s for s in T["flh_setup"][0]()["steps"]}[2]
assert "installer" in s2["next"] and T["flh_setup"][0]()["installed_as"] == "manual", s2
print("first run in Claude ok")

# the manual install under its new name next to the extension is reported as a double install
cfgp = os.path.join(os.environ["APPDATA"], "Claude", "claude_desktop_config.json")
os.makedirs(os.path.dirname(cfgp), exist_ok=True)
json.dump({"mcpServers": {"fl-headless-manual": {"command": "py", "args": []}}}, open(cfgp, "w"))
assert S._double_install() == [cfgp], S._double_install()
json.dump({"mcpServers": {"fl-headless-manual": {"command": "py", "disabled": True}}}, open(cfgp, "w"))
assert S._double_install() == []
os.remove(cfgp)
print("ok")
