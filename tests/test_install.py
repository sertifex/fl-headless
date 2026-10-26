"""install.py: --help and unknown flags change nothing; only full folder paths are accepted; a re-run keeps settings
added by hand to the fl-headless entry (FLH_FL_EXE, FLH_TOOLSET...) and FLH_ROOTS unless new folders are given.
Runs the installer in a subprocess against a temp config (FLH_CLAUDE_CONFIG) and temp HOME / APPDATA / LOCALAPPDATA.
Run: python3 tests/test_install.py"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL = os.path.join(os.path.dirname(HERE), "install.py")
TMP = tempfile.mkdtemp(prefix="flh_install_")
CFG = os.path.join(TMP, "Claude", "claude_desktop_config.json")
HOME = os.path.join(TMP, "home"); os.makedirs(os.path.join(HOME, "Music"))
A, B = os.path.join(TMP, "songs a"), os.path.join(TMP, "songs b")
os.makedirs(A); os.makedirs(B)
ENV = dict(os.environ, HOME=HOME, USERPROFILE=HOME, APPDATA=os.path.join(TMP, "roaming"),
           LOCALAPPDATA=os.path.join(TMP, "local"), FLH_CLAUDE_CONFIG=CFG)
ENV.pop("FLH_ROOTS", None)


def run(*args):
    r = subprocess.run([sys.executable, INSTALL, *args], env=ENV, capture_output=True, text=True, timeout=300,
                       stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def write_cfg(c):
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(c, f)


def cfg():
    with open(CFG, encoding="utf-8") as f:
        return json.load(f)


def entry():
    m = cfg()["mcpServers"]
    assert "fl-headless" not in m, "the extension's name must never be in Claude's config (they clash)"
    return m["fl-headless-manual"]


hand = {"mcpServers": {"other": {"command": "x"},
                       "fl-headless": {"command": "old-python", "args": ["old.py"], "disabled": False,
                                       "env": {"FLH_ROOTS": A, "FLH_FL_EXE": "C:\\FL\\FL64.exe",
                                               "FLH_TOOLSET": "full"}}}}
write_cfg(hand); before = open(CFG, "rb").read()
# --help (any spelling) prints the usage and changes nothing
for h in ("--help", "-h", "/?"):
    code, out = run(h)
    assert code == 0 and "install.py" in out and "--yes" in out, (h, code, out)
    assert open(CFG, "rb").read() == before and not os.path.exists(CFG + ".bak"), h
# also when it comes after a folder: help wins, nothing written
code, out = run(B, "--help")
assert code == 0 and open(CFG, "rb").read() == before, out
# an unknown flag is refused, nothing written
code, out = run("--roots", B)
assert code == 2 and "Unknown option" in out and open(CFG, "rb").read() == before, (code, out)
# relative folders are refused (they'd resolve against Claude's working folder)
for bad in ("Music", "songs;" + B, "./x"):
    code, out = run(bad)
    assert code == 2 and "full folder path" in out and open(CFG, "rb").read() == before, (bad, code, out)
print("help / flags / relative refused ok")

# --yes on an existing entry: every hand-added setting kept, FLH_ROOTS kept, command/args refreshed
code, out = run("--yes")
assert code == 0, out
e = entry()
assert e["env"] == hand["mcpServers"]["fl-headless"]["env"], e
assert e["disabled"] is False and e["command"] == sys.executable and e["args"][0].endswith(".py"), e
assert cfg()["mcpServers"]["other"] == {"command": "x"}
assert os.path.isfile(CFG + ".bak") and os.path.isfile(CFG + ".original.bak")
# new folders given (quotes and spaces stripped): only FLH_ROOTS changes
code, out = run(f' "{B}" ; {A} ;')
assert code == 0, out
e = entry()
assert e["env"]["FLH_ROOTS"] == f"{B};{A}" and e["env"]["FLH_FL_EXE"] == "C:\\FL\\FL64.exe" and \
    e["env"]["FLH_TOOLSET"] == "full", e
# two separate folder arguments are joined
code, out = run(A, B)
assert code == 0 and entry()["env"]["FLH_ROOTS"] == f"{A};{B}", out
print("merge ok")

# an earlier broken install left a relative FLH_ROOTS ('--help'): replaced by the default, other keys kept
write_cfg({"mcpServers": {"fl-headless": {"command": "p", "args": [], "env": {"FLH_ROOTS": "--help",
                                                                              "FLH_BLANK": "C:\\b.flp"}}}})
code, out = run("-y")
assert code == 0, out
e = entry()
assert e["env"]["FLH_ROOTS"] == os.path.join(HOME, "Music") and e["env"]["FLH_BLANK"] == "C:\\b.flp", e
# first install (no entry, no config file yet): the default folders
os.remove(CFG)
code, out = run("--yes")
assert code == 0 and entry()["env"] == {"FLH_ROOTS": os.path.join(HOME, "Music")}, (out, cfg())
# the installer starts the server the way Claude will and reports it answering; the summary is kept as a log
assert "[server] starts and answers (" in out, out
log = os.path.join(TMP, "local", "fl-headless", "install_log.txt")
assert os.path.isfile(log) and "server" in open(log, encoding="utf-8").read()
# a server that can't start is reported as a problem, not hidden
# --uninstall: the entry goes (the old and the new name), the others and a backup stay
write_cfg({"mcpServers": {"other": {"command": "x"}, "fl-headless": {"command": "p", "args": [], "env": {}},
                          "fl-headless-manual": {"command": "p", "args": [], "env": {}}}})
code, out = run("--uninstall")
assert code == 0 and "removed fl-headless" in out, out
assert cfg() == {"mcpServers": {"other": {"command": "x"}}} and "fl-headless" in open(CFG + ".bak").read(), cfg()
code, out = run("--uninstall")
assert code == 0 and "no fl-headless entry" in out, out

# the Claude Desktop extension is installed: the manual install says so and (with no 'yes') changes nothing
ext = os.path.join(TMP, "roaming", "Claude", "Claude Extensions", "local.mcpb.author.fl-headless")
os.makedirs(ext); json.dump({"name": "fl-headless"}, open(os.path.join(ext, "manifest.json"), "w"))
write_cfg({"mcpServers": {"other": {"command": "x"}}}); before = open(CFG, "rb").read()
r = subprocess.run([sys.executable, INSTALL], env=ENV, capture_output=True, text=True, timeout=300, input="\nn\n")
assert "extension is already installed" in r.stdout and open(CFG, "rb").read() == before, r.stdout[-800:]
print("ok")
