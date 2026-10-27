"""flh_setup's guided first-run checklist on a simulated Windows PC: the 7 steps in order (FL Studio, song folders,
connection to FL, FL edition, fl-headless look, free extras, ready), each done / needs you / optional with ONE plain
next action (no jargon), apply() doing everything safe in one call (profile, FL's script, theme), and the walkthrough
reaching 'ready'. Also flh_welcome's one-line-per-step view. Run: python3 tests/test_setup_walkthrough.py (no FL)."""
import os, re, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = os.path.join(tmp, "music"); os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
HOME = os.path.join(tmp, "home", "user")
os.environ["USERPROFILE"] = HOME; os.environ["HOME"] = HOME
for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial", "FLH_FL_USERDATA", "FLH_FL_EDITION", "FLH_FL_EXE",
          "FL_EXE"):
    os.environ.pop(k, None)
os.makedirs(os.environ["FLH_ROOTS"], exist_ok=True)
import fl_headless_mcp as m  # noqa: E402
import flh_setup as S  # noqa: E402
ns = vars(m)
T = m.TOOLS

# ---- a simulated Windows PC with FL Studio 2026 installed and closed, nothing else set up
UD = os.path.join(HOME, "Documents", "Image-Line", "FL Studio")
THEMES = os.path.join(UD, "Settings", "Themes")
os.makedirs(THEMES)
with open(os.path.join(THEMES, "__current.flstheme"), "wb") as f:
    f.write(b"\xef\xbb\xbfHue=42\r\nLightness=-100\r\n")
PC = {"running": False, "answers": False, "scripts": [], "inputs": {}, "out": [], "in": [], "exe": True}
calls = []


class Bridge:
    def ports(self):
        return {"midi_out": list(PC["out"]), "midi_in": list(PC["in"])}

    def choose(self, p):
        return ("Flapi Request", "Flapi Response") if "Flapi Request" in p["midi_out"] else None


def fl_exe():
    if not PC["exe"]:
        raise FileNotFoundError("FL64.exe isn't in the usual places")
    return r"C:\Program Files\Image-Line\FL Studio 2026\FL64.exe"


def bridge_setup(action="status", **k):
    calls.append(action)
    if action == "update_script":
        PC["scripts"] = [(os.path.join(UD, "Settings", "Hardware", "fl-headless", "device_flheadless.py"), "# name=")]
        return {"updated": [PC["scripts"][0][0]]}
    return {"transport": "midi", "ports": ["Flapi Request", "Flapi Response"],
            "fl_answers": True if PC["answers"] else "no answer: timeout"}


ns.update({"IS_WINDOWS": True, "BRIDGE": Bridge(), "fl_exe": fl_exe, "fl_installs": lambda: [(fl_exe(), "registry")],
           "_fl_file_version": lambda: "26.1.4.5589", "fl_running": lambda: PC["running"],
           "_blank_project": lambda: None, "_installed_scripts": lambda: list(PC["scripts"]),
           "_userdata_pick": lambda refresh=False: {"path": UD, "layer": "registry", "why": "FL's setting",
                                                     "candidates": [{"path": UD, "exists": True}]},
           "_fl_userdata": lambda: UD, "_known_documents": lambda: [], "_loopback_ports": lambda p: [],
           "_reg_values": lambda *a, **k: {}, "_fl_key": lambda: r"Software\Image-Line\FL Studio",
           "_reg_dump": lambda rel=None: dict(PC["inputs"]) if rel and rel.endswith("Devices") else {},
           "fl_bridge_setup": bridge_setup, "_note_script_location": lambda: False})
S._uninstall_names = lambda: []
NAMES = ["FL Studio", "Song folders", "Connection to FL", "FL edition", "fl-headless look", "Free extras", "Ready"]
JARGON = re.compile(r"bridge|FLH_ROOTS|flh_|fl_[a-z]|MCP|\bstate\b|registry|__current", re.I)


def shape(steps):
    assert [s["n"] for s in steps] == list(range(1, 8)) and [s["step"] for s in steps] == NAMES, steps
    for s in steps:
        assert s["status"] in ("done", "needs you", "optional"), s
        assert set(s) <= {"n", "step", "status", "say", "next", "do", "auto"}, s
        if s["status"] == "needs you":
            assert isinstance(s.get("next"), str) and s["next"] and "\n" not in s["next"], s     # ONE next action
        else:
            assert "next" not in s and s.get("say"), s
        for k in ("say", "next"):
            assert not JARGON.search(s.get(k, "").replace(tmp, "")), (k, s)                     # plain words
    return {s["n"]: s for s in steps}


# ---- 1. a fresh PC: what's done, what needs the user, what setup does by itself
r = T["flh_setup"][0]()
st = shape(r["steps"])
assert list(r)[:2] == ["steps", "ready"] and r["ready"] is False, list(r)[:3]
assert st[1]["status"] == "done" and st[1]["say"] == "FL Studio 2026.1.4 found", st[1]
assert st[2]["status"] == "needs you" and st[2]["auto"] and st[2]["do"] == "flh_setup(action='apply')", st[2]
assert st[2]["say"].startswith("Songs go in " + os.environ["FLH_ROOTS"]) and "-14 LUFS" in st[2]["say"], st[2]
assert st[3]["status"] == "needs you" and "loopMIDI" in st[3]["next"] and not st[3].get("auto"), st[3]
assert st[4]["status"] == "needs you" and "Help > About" in st[4]["next"] and "not sure" in st[4]["next"], st[4]
assert st[5]["status"] == "needs you" and st[5]["auto"] and "switch back" in st[5]["next"], st[5]
assert st[6]["status"] == "optional" and "required" in st[6]["say"], st[6]
assert st[7]["status"] == "needs you" and st[7]["next"].startswith("Connection to FL: "), st[7]
assert "make me a lofi beat" in st[7]["say"], st[7]
# the machine fields stay for compatibility
for k in ("fl", "midi", "fl_script", "writable_roots", "profile", "fl_edition", "theme", "next_steps", "extras"):
    assert k in r, k
assert any("FL edition" in s for s in r["next_steps"]) and any("profile" in s for s in r["next_steps"])

# ---- 2. apply: everything safe in one call (the edition the user named, profile, FL's script, the theme)
a = T["flh_setup"][0](action="apply", fl_edition="producer")
assert "update_script" in calls and any("script into FL" in d for d in a["done"]), a["done"]
assert any("made fl-headless FL's theme" in d for d in a["done"]), a["done"]
assert a["switch_back"] == "To switch back: FL > Options > Theme > pick 'My theme before fl-headless'.", a
assert os.path.isfile(os.path.join(THEMES, "My theme before fl-headless.flstheme"))
assert os.path.isfile(os.path.join(THEMES, "thmMy theme before fl-headless.png"))
st = shape(a["steps"])
assert [st[i]["status"] for i in (1, 2, 4, 5)] == ["done"] * 4, a["steps"]
assert st[1]["say"] == "FL Studio 2026.1.4 found (Producer Edition)" and "Producer" in st[4]["say"], st
assert st[5]["say"].startswith("On. To switch back"), st[5]
assert st[3]["status"] == "needs you" and st[7]["next"].startswith("Connection to FL: "), st[7]
n = len(calls)
T["flh_setup"][0](action="apply")                               # the script is there now: not written again
assert calls.count("update_script") == 1, calls

# ---- 3. the user adds the loopMIDI ports and picks the script in FL's MIDI settings: set up while FL is closed
PC["out"], PC["in"] = ["Flapi Request"], ["Flapi Response"]
st = shape(T["flh_setup"][0]()["steps"])
assert st[3]["status"] == "needs you" and "MIDI settings" in st[3]["next"], st[3]          # not picked in FL yet
PC["inputs"] = {r"Software\Image-Line\FL Studio\Devices\MIDI input\Flapi Request":
                {"ScriptFolder": (1, "fl-headless"), "Enabled": (1, "1"), "Port": (1, "12")}}
r = T["flh_setup"][0]()
st = shape(r["steps"])
assert st[3]["status"] == "done" and st[3]["say"] == "Set up: I connect when FL is open.", st[3]
assert st[7]["status"] == "done" and st[7]["say"].startswith("You're set. Try:") and r["ready"] is True, st[7]
assert "next" not in st[7]

# ---- 4. FL open: answers -> connected; doesn't answer -> one plain action (and the call that finds out why)
PC["running"], PC["answers"] = True, True
st = shape(T["flh_setup"][0]()["steps"])
assert st[3]["say"] == "Connected: I can work in FL live." and st[7]["status"] == "done", st
PC["answers"] = False
st = shape(T["flh_setup"][0]()["steps"])
assert st[3]["status"] == "needs you" and st[3]["do"] == "fl_watchdog()" and "popup" in st[3]["next"], st[3]
PC["running"] = False

# ---- 5. FL not found / numpy-free problems stay in their own step with one action
PC["exe"] = False
st = shape(T["flh_setup"][0]()["steps"])
assert st[1]["status"] == "needs you" and "FL64.exe" in st[1]["next"] and "FLH_FL_EXE" in st[1]["do"], st[1]
PC["exe"] = True

# ---- 6. song folders missing: the user's step (not something setup can do)
os.environ["FLH_ROOTS"] = os.path.join(tmp, "nope")
st = shape(T["flh_setup"][0]()["steps"])
assert st[2]["status"] == "needs you" and st[2]["next"].startswith("Create ") and not st[2].get("auto"), st[2]
del os.environ["FLH_ROOTS"]
st = shape(T["flh_setup"][0]()["steps"])
assert st[2]["status"] == "needs you" and "installer" in st[2]["next"], st[2]
os.environ["FLH_ROOTS"] = os.path.join(tmp, "music")

# ---- 7. the edition 'not sure' answer is a done step, not a question
T["flh_setup"][0](action="apply", fl_edition="not sure")
m._state({"fl_edition": None}); import flh_store; flh_store.profile_set(flh_store.profile_name(), {"fl_edition": None})
st = shape(T["flh_setup"][0]()["steps"])
assert st[4]["status"] == "done" and "every edition" in st[4]["say"], st[4]

# ---- flh_welcome: one line per step and the one next action
import flh_newsong  # noqa: E402,F401
w = T["flh_welcome"][0]()
assert len(w["setup"]) == 7 and w["setup"][0].startswith("1. FL Studio: done - FL Studio 2026.1.4 found"), w["setup"]
assert w["try"] and w["promises"] and isinstance(w["setup_ready"], bool), w
print("setup walkthrough ok")
