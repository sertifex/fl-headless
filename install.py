"""fl-headless installer (double-click install.bat, or run this directly).

    python install.py                          asks which folders the server may write to
    python install.py "D:\\Music;D:\\FL Projects"  uses these folders, no questions
    python install.py --yes                    default folders, no questions

Checks Python and numpy, finds FL Studio, adds fl-headless to Claude Desktop's config (keeps a .bak), installs the
bridge script into FL Studio, asks your FL edition (optional), and tells you the one FL step left for the live tools.
Then, in Claude: "run flh_setup" checks everything and lists anything still missing.

    python install.py --uninstall              removes fl-headless from Claude's config and FL (Recycle Bin)
    python install.py --help                   this text (nothing is changed)

Folders must be full paths (D:\\Music, not Music); put a path with spaces in quotes. Settings you added by hand to
the fl-headless entry in Claude's config (FLH_FL_EXE, FLH_TOOLSET...) are kept; FLH_ROOTS changes only when you give
new folders (or on the first install). FLH_CLAUDE_CONFIG=<path to claude_desktop_config.json> picks the config file."""
import glob, importlib, json, os, re, shutil, subprocess, sys

for _s in (sys.stdout, sys.stderr):                          # a console that can't show a character must not crash us
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
home = os.path.expanduser("~")
local, roaming = os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")
windows = sys.platform == "win32"
HELP_ARGS = ("-h", "--help", "/?", "-?", "/h", "/help", "help")


def is_abs_root(p):
    """A full folder path: 'D:\\Music', '\\\\server\\share' on Windows, '/home/me/Music' elsewhere. A relative entry would
    be resolved against Claude Desktop's working folder, which nobody means."""
    if windows:
        return bool(re.match(r"^[A-Za-z]:[\\/]", p) or re.match(r"^[\\/]{2}[^\\/]+[\\/]+[^\\/]+", p))
    return os.path.isabs(p)


def clean_roots(raw):
    """'D:\\Music; "D:\\FL Projects" ;' -> (['D:\\Music', 'D:\\FL Projects'], [relative entries refused])."""
    out, bad = [], []
    for r in str(raw or "").split(";"):
        r = r.strip().strip('"').strip("'").strip()
        if not r:
            continue
        if not is_abs_root(r):
            bad.append(r)
        elif r not in out:
            out.append(r)
    return out, bad


def parse_args(argv):
    """-> (roots given on the command line or None, yes). Help and unknown flags end the run before anything is
    written."""
    global uninstall
    given, yes = [], False
    for a in argv:
        if a.strip().lower() in HELP_ARGS:
            print(__doc__); sys.exit(0)
    for a in argv:
        if a in ("--yes", "-y", "/y"):
            yes = True
        elif a in ("--uninstall", "/uninstall"):
            uninstall = True
        elif a.startswith("-") or (windows and a.startswith("/")):
            print(f"Unknown option {a!r}. Nothing was changed.\n\n{__doc__}"); sys.exit(2)
        else:
            given.append(a)
    if not given:
        return None, yes
    roots_, bad = clean_roots(";".join(given))
    if bad or not roots_:
        print(f"Not a full folder path: {', '.join(repr(b) for b in bad) or repr(' '.join(given))}. Give full paths "
              "like \"D:\\FL Projects\" (in quotes when they have spaces), separated by ';'. Nothing was changed.")
        sys.exit(2)
    return ";".join(roots_), yes


uninstall = False
given_roots, _yes = parse_args(sys.argv[1:])
auto = given_roots is not None or _yes                       # folders given, or --yes: no questions
summary = []


def done(label, text):
    summary.append(f"  {label:<14}{text}"); print(f"[{label}] {text}")


def ask(q):
    try:
        return auto or input(q + " [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def finish(code=0):
    print("\nSummary\n" + "\n".join(summary))
    try:                                                     # kept for bug reports (flh_bug_report attaches it)
        d = os.path.join(local or home, "fl-headless"); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "install_log.txt"), "w", encoding="utf-8") as f:
            f.write(f"python {sys.version.split()[0]} {sys.executable}\nexit {code}\n" + "\n".join(summary) + "\n")
    except OSError:
        pass
    if not auto:
        try:
            input("\nPress Enter to close...")
        except EOFError:                                     # no console (run from a script)
            pass
    sys.exit(code)


# 1. Python and numpy
if sys.version_info < (3, 10):
    done("python", f"{sys.version.split()[0]} is too old: install Python 3.10+ from python.org and run again")
    finish(3)                                                # install.bat offers to install a current one
done("python", f"{sys.version.split()[0]} ({sys.executable})")
try:
    import numpy
    done("numpy", numpy.__version__)
except ImportError:
    if ask("numpy (needed for audio tools) is missing. Install it with pip now?"):
        pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "numpy"]
        if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode:
            subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"])     # Python without pip
        r = subprocess.run(pip)
        if r.returncode:                                     # no rights to the Python folder: into the user's site
            r = subprocess.run(pip + ["--user"])
        importlib.invalidate_caches()
        import site
        if site.getusersitepackages() not in sys.path:
            sys.path.append(site.getusersitepackages())
        try:
            import numpy
            done("numpy", f"installed {numpy.__version__}")
        except ImportError:
            done("numpy", f"pip failed (exit {r.returncode}): run '{sys.executable} -m pip install numpy' yourself")
    else:
        done("numpy", "missing: audio tools won't work until you run 'pip install numpy'")

# 2. Claude Desktop config
override = os.environ.get("FLH_CLAUDE_CONFIG", "").strip().strip('"')
if override:                                                 # one config file, named by the user (or a test)
    configs = [os.path.abspath(override)]
else:
    configs = [p for p in dict.fromkeys(
        [os.path.join(roaming, "Claude", "claude_desktop_config.json")]
        + glob.glob(os.path.join(local, "Claude-Data", "*", "claude_desktop_config.json"))
        + glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude",
                                 "claude_desktop_config.json")))
        if os.path.isfile(p)]
    if not configs:                       # Claude Desktop installed and started once, but no config file yet (Claude
        dirs = ([os.path.join(roaming, "Claude")] if roaming else []) + (   # writes it only on 'Edit Config'): a new
            glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude")) if local else [])
        configs = [os.path.join(d, "claude_desktop_config.json") for d in dict.fromkeys(dirs)   # one in its folder
                   if os.path.isabs(d) and os.path.isdir(d)]
cfgs = {}
for p in configs:
    if not os.path.exists(p):
        cfgs[p] = {}; continue                                 # a new config file at the named place
    try:
        with open(p, encoding="utf-8-sig") as f:
            cfgs[p] = json.load(f)
        if not isinstance(cfgs[p], dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as e:
        cfgs.pop(p, None)
        print(f"Skipping {p}: can't read it ({e})")


KEY = "fl-headless-manual"          # this install's name in Claude's config. Not 'fl-headless': that is the Claude
LEGACY_KEYS = ("fl-headless",)      # Desktop extension's name, and a config entry with the extension's name makes
                                    # Claude start the extension with this entry's arguments: neither runs (PC
                                    # 2026-10-25). Older installs used 'fl-headless': moved on install, removed on
                                    # uninstall.


def our_keys(cfg):
    m = cfg.get("mcpServers")
    return [k for k in (KEY,) + LEGACY_KEYS if isinstance(m, dict) and k in m]


def claude_running():
    """True when Claude Desktop is running (Windows). Claude rewrites its config from memory while it runs, so a
    change made now can be undone (PC 2026-10-25: an uninstall came back)."""
    if not windows:
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe", "/NH"], capture_output=True, text=True,
                           timeout=10)
        return "claude.exe" in (r.stdout or "").lower()
    except (OSError, subprocess.SubprocessError):
        return False


def wait_claude_closed():
    """Ask the user to quit Claude Desktop before Claude's config is changed. -> True when it's closed."""
    if not claude_running():
        return True
    print("\nClaude Desktop is open. Quit it first, or it may undo this change: right-click Claude's icon near the "
          "clock > Quit (closing the window isn't enough).")
    if auto:
        print("(Not waiting: --yes / folders given. If fl-headless doesn't change in Claude, quit Claude and run this "
              "again.)")
        return False
    for _ in range(20):
        try:
            ans = input("Press Enter when Claude is closed (or type 'skip'): ").strip().lower()
        except EOFError:
            return False
        if ans == "skip":
            return False
        if not claude_running():
            return True
        print("Claude is still running.")
    return False


def extension_installed():
    """Folders where Claude Desktop has the fl-headless extension installed ([] if none found)."""
    pats = [os.path.join(roaming, "Claude", "Claude Extensions", "*", "manifest.json")] if roaming else []
    if local:
        pats += [os.path.join(local, "Claude", "Claude Extensions", "*", "manifest.json"),
                 os.path.join(local, "Claude-Data", "*", "Claude Extensions", "*", "manifest.json"),
                 os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "Claude Extensions",
                              "*", "manifest.json")]
    out = []
    for pat in pats:
        for mf in glob.glob(pat):
            try:
                with open(mf, encoding="utf-8-sig") as f:
                    if (json.load(f) or {}).get("name") == "fl-headless":
                        out.append(os.path.dirname(mf))
            except (OSError, ValueError, AttributeError):
                continue
    return out


def write_cfg(p, cfg):
    with open(p + ".flh-tmp", "w", encoding="utf-8") as f:   # temp file + replace: never half a config
        json.dump(cfg, f, indent=2)
    os.replace(p + ".flh-tmp", p)


if uninstall:
    gone = 0
    closed = wait_claude_closed() if any(our_keys(c) for c in cfgs.values()) else True
    for p, cfg in cfgs.items():
        keys = our_keys(cfg)
        if keys:
            shutil.copy2(p, p + ".bak")
            for k in keys:
                del cfg["mcpServers"][k]
            write_cfg(p, cfg); gone += 1
            done("claude", f"removed fl-headless from {p} (previous version: {os.path.basename(p)}.bak)"
                 + ("" if closed else " - Claude was open: if it comes back, quit Claude and run this again"))
    if not gone:
        done("claude", "no fl-headless entry in Claude's config")
    try:
        import fl_headless_mcp as _m
        scripts = [p for p, _ in _m._installed_scripts()]
    except Exception:                                        # noqa: BLE001
        scripts = []
    for sp in scripts:
        folder = os.path.dirname(sp)
        if windows and ask(f"Move FL's bridge script folder {folder} to the Recycle Bin?"):
            code, aborted = _m._recycle_shell(folder)
            done("fl script", f"moved {folder} to the Recycle Bin" if not (code or aborted or os.path.exists(folder))
                 else f"couldn't recycle {folder}: delete it yourself")
        else:
            done("fl script", f"left {sp} (delete that folder yourself if you want)")
    done("kept", f"your songs, and fl-headless's notes/backups in {os.path.join(local or home, 'fl-headless')}")
    print("\nFully quit Claude Desktop (tray icon > Quit) and reopen it: fl-headless is gone from Claude.")
    finish(0)


def old_entry(cfg):
    m = cfg.get("mcpServers") or {}
    e = next((m[k] for k in (KEY,) + LEGACY_KEYS if isinstance(m.get(k), dict)), None)
    return e if isinstance(e, dict) else {}


def old_roots(cfg):
    env = old_entry(cfg).get("env")
    return clean_roots(env.get("FLH_ROOTS") if isinstance(env, dict) else "")[0]


old = next((";".join(old_roots(c)) for c in cfgs.values() if old_roots(c)), "")   # only full paths are reused
try:                                                         # the real Music / Documents (OneDrive, moved folders)
    import fl_headless_mcp as _m
    music, docs = _m._known_folder("Music")[0], _m._known_folder("Documents")[0]
except Exception:                                            # noqa: BLE001
    music, docs = os.path.join(home, "Music"), os.path.join(home, "Documents")
default = old or ";".join(p for p in (music, os.path.join(docs, "Image-Line")) if os.path.isdir(p))
roots_changed = given_roots is not None                      # FLH_ROOTS is rewritten only when folders were given
if given_roots is not None:
    flh_roots = given_roots
elif auto:
    flh_roots = default
else:
    while True:
        try:
            ans = input(f"Folders fl-headless may write to (full paths, semicolon-separated)\n[{default}]: ").strip()
        except EOFError:
            ans = ""
        if not ans:
            flh_roots = default; break
        ok_, bad_ = clean_roots(ans)
        if bad_:
            print(f"Not a full folder path: {', '.join(bad_)} (write it like D:\\FL Projects). Try again.")
            continue
        flh_roots, roots_changed = ";".join(ok_), True; break
flh_roots = ";".join(clean_roots(flh_roots)[0])
missing = [r for r in flh_roots.split(";") if r and not os.path.isdir(r)]
done("folders", (flh_roots or "none (file edits and renders will be refused)") + (f"  (not found: {';'.join(missing)})" if missing else ""))

LAUNCHER = os.path.join(here, "fl_headless_launch.py")         # the server runs as the module fl_headless_mcp
LAUNCH_SRC = ('"""Starts the fl-headless MCP server (written by install.py)."""\n'
              "import os\nimport sys\n\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
              "import fl_headless_mcp  # noqa: E402\n\nfl_headless_mcp.main()\n")
try:                                                            # (one copy of its globals, also after flh_reload)
    try:
        with open(LAUNCHER, encoding="utf-8", newline="") as f:
            same = f.read() == LAUNCH_SRC
    except OSError:
        same = False
    if not same:
        with open(LAUNCHER + ".tmp", "w", encoding="utf-8", newline="\n") as f:
            f.write(LAUNCH_SRC)
        os.replace(LAUNCHER + ".tmp", LAUNCHER)
    server = LAUNCHER
except OSError as e:
    print(f"couldn't write the launcher ({e}); the config starts fl_headless_mcp.py directly")
    server = os.path.join(here, "fl_headless_mcp.py")
entry = {"command": sys.executable, "args": [server], "env": {"FLH_ROOTS": flh_roots}}


def merged_entry(cfg):
    """The fl-headless entry for this config: command and args refreshed, every other key and env setting the user
    added by hand kept (FLH_FL_EXE, FLH_TOOLSET...). FLH_ROOTS is replaced only when folders were given now, or
    when the entry has no usable one yet (first install, or only relative entries)."""
    e = dict(old_entry(cfg))
    env = dict(e.get("env")) if isinstance(e.get("env"), dict) else {}
    e.update(command=sys.executable, args=[server])
    if roots_changed or not old_roots(cfg):
        env["FLH_ROOTS"] = flh_roots
    else:
        env["FLH_ROOTS"] = ";".join(old_roots(cfg))           # kept (relative entries dropped)
    e["env"] = env
    return e


ext = extension_installed()
if ext:
    print(f"\nThe fl-headless Claude Desktop extension is already installed ({ext[0]}). You don't need this manual "
          "install too: two copies would both drive FL.")
    if not ask("Install the manual copy anyway?"):
        done("claude", "not changed: the extension is installed (Claude > Settings > Extensions)")
        finish(0)
claude_closed = wait_claude_closed() if cfgs else True
for p, cfg in cfgs.items():
    existed = os.path.exists(p)
    try:
        if existed:
            if not os.path.exists(p + ".original.bak"):        # the config as it was before fl-headless, once
                shutil.copy2(p, p + ".original.bak")
            shutil.copy2(p, p + ".bak")                        # and as it was right before this run, every run
    except OSError as e:
        print(f"couldn't back up {p} ({e})")
    mcp = cfg.get("mcpServers")
    if not isinstance(mcp, dict):
        mcp = cfg["mcpServers"] = {}
    mcp[KEY] = merged_entry(cfg)
    for k in LEGACY_KEYS:                                      # an older install's name: the extension's
        mcp.pop(k, None)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    write_cfg(p, cfg)
    done("claude", (f"updated {p} (previous version: {os.path.basename(p)}.bak)" if existed else f"created {p}")
         + ("" if claude_closed else " - Claude was open: quit it fully and reopen it, then check fl-headless is there"))
if not cfgs:
    what = "can't be read (see above; fix or rename it, then run this again)" if configs else "not found"
    print(f"Claude Desktop config {what}. Add this under \"mcpServers\" in claude_desktop_config.json:")
    print(json.dumps({KEY: entry}, indent=2))
    done("claude", f"config {what.split(' (')[0]}: add the entry above by hand"
         + (" (Claude Desktop isn't installed or was never started)" if not configs else ""))



def self_test(timeout=30):
    """Start the server exactly as Claude Desktop will (same command, args and env) and ask it for its tools: a broken
    install shows up here, not as a silent 'server disconnected' in Claude."""
    import threading
    env = dict(os.environ, **{k: str(v) for k, v in merged_entry(next(iter(cfgs.values()), {})).get("env", {}).items()}) \
        if cfgs else dict(os.environ, FLH_ROOTS=flh_roots)
    try:
        pr = subprocess.Popen([sys.executable, server], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, cwd=os.path.dirname(server) or None)
    except OSError as e:
        return False, f"couldn't start it ({e})"
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "install"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}]
    out = {}

    def talk():
        try:
            o, e = pr.communicate("".join(json.dumps(x) + "\n" for x in msgs).encode("utf-8"), timeout=timeout)
            out["o"], out["e"] = o, e
        except Exception as ex:                              # noqa: BLE001
            out["x"] = ex
    t = threading.Thread(target=talk, daemon=True); t.start(); t.join(timeout + 5)
    if "o" not in out:
        try:
            pr.kill()
        except OSError:
            pass
        return False, f"no answer within {timeout} s"
    replies = {}
    for line in out["o"].decode("utf-8", "replace").splitlines():
        try:
            r = json.loads(line)
            replies[r.get("id")] = r
        except ValueError:
            continue
    tools = ((replies.get(2) or {}).get("result") or {}).get("tools")
    if tools:
        return True, f"starts and answers ({len(tools)} tools)"
    err = (out.get("e") or b"").decode("utf-8", "replace").strip().splitlines()
    return False, "it didn't answer: " + (err[-1][:200] if err else "no output")


ok_, why_ = self_test()
done("server", why_ if ok_ else f"PROBLEM - {why_}. Run '{sys.executable} {server}' in a terminal to see the error")

# 3. FL Studio itself, and the bridge script inside FL (mailbox path filled in)
try:
    import fl_headless_mcp as m
except Exception as e:                                       # noqa: BLE001
    done("fl script", f"couldn't load fl_headless_mcp.py ({type(e).__name__}: {e})")
    finish(1)
if windows:
    try:
        exe = m.fl_exe()
        try:                                         # its version is a nicety (no PowerShell, a locked file...)
            ver = m._fl_file_version() if exe else None
        except Exception:                            # noqa: BLE001
            ver = None
        done("fl studio", f"{exe}" + (f" (version {ver})" if ver else ""))
        try:
            fl_open_now = bool(exe and m.fl_running())   # PC-VERIFY: "Update MIDI scripts" button name in FL 2026
        except Exception:                            # noqa: BLE001
            fl_open_now = False
    except FileNotFoundError:
        done("fl studio", "not found: if it's installed somewhere unusual, add \"FLH_FL_EXE\": \"<path to FL64.exe>\" "
                          "to the fl-headless env in claude_desktop_config.json")
else:
    done("fl studio", "FL Studio can only be driven on Windows (macOS isn't supported); file and audio tools work here")
try:
    targets = [(p, first if first.startswith("# name=") else None) for p, first in m._installed_scripts()] \
        or [(os.path.join(m._hardware_dir(), "fl-headless", "device_flheadless.py"), None)]
    for p, name_line in targets:
        m._install_script(p, name_line)
    done("fl script", "installed " + ", ".join(p for p, _ in targets)
         + ("  (FL is open: it picks the script up after Options > MIDI settings > Update MIDI scripts, or its "
            "next start)" if windows and globals().get("fl_open_now") else ""))
except FileNotFoundError:
    done("fl script", "FL Studio's user data (Documents\\Image-Line\\FL Studio\\Settings\\Hardware) wasn't found: "
                      "start FL Studio once, then run this installer again")
except OSError as e:
    done("fl script", f"couldn't write it ({e})")

# 4. MIDI port for the live tools (FL loads the script on one MIDI input; replies go through a file mailbox)
if windows:
    try:
        ports = m.BRIDGE.ports()
        loop = m._loopback_ports(ports)
        virtual = [n for n in ports["midi_out"] if n in ports["midi_in"] and n not in loop and
                   re.search(r"loopmidi|flapi|virtual|loopbe|rtpmidi", n, re.I)]    # not a hardware keyboard
        if loop:
            done("live tools", f"'{loop[0]}' found: in Claude, ask \"run fl_bridge_setup use_loopback\"")
        elif virtual:
            done("live tools", f"ports found ({', '.join(virtual)}): in FL, Options > MIDI settings > Input, pick a "
                               "loopMIDI port, enable it and set Controller type to fl-headless")
        elif [n for n in ports["midi_in"] if n not in loop and n not in virtual]:
            kb = [n for n in ports["midi_in"] if n not in loop and n not in virtual][0]
            done("live tools", f"no virtual port needed: with '{kb}' connected, ask Claude \"run fl_bridge_setup "
                               f"use_input on {kb}\" (its notes still play). Or add Windows 11's 'Default Basic App "
                               "Loopback' in the MIDI Settings app and ask for use_loopback")
        else:
            done("live tools", "no loopback port: on Windows 11 add the 'Default Basic App Loopback' in the MIDI Settings "
                               "app and ask Claude \"run fl_bridge_setup use_loopback\", or create one port in loopMIDI "
                               "and pick it in FL with Controller type fl-headless - or connect a MIDI keyboard and ask "
                               "for use_input")
    except Exception as e:                                   # noqa: BLE001
        done("live tools", f"couldn't list MIDI ports ({type(e).__name__}: {e})")
else:
    done("live tools", "MIDI check skipped (not Windows)")

# 5. FL edition: which native plugins are owned (the rest run as demos). Never guessed: asked, or set later in Claude
if not auto:
    try:
        import flh_catalog
        ans = input("Your FL Studio edition (Help > About in FL): [f]ruity, [p]roducer, [s]ignature, [a]ll plugins, "
                    "or Enter to skip: ").strip().lower()
        key = {"f": "fruity", "p": "producer", "s": "signature", "a": "all_plugins"}.get(ans[:1]) if ans else None
        key = key or (flh_catalog.edition_key(ans) if ans else None)
        if key:
            m._state({"fl_edition": key}); done("edition", flh_catalog.EDITION_NAMES[key])
        else:
            done("edition", "skipped: Claude will ask (flh_setup)")
    except (EOFError, Exception):                            # noqa: BLE001
        done("edition", "skipped: Claude will ask (flh_setup)")

# 6. Speaker recording for fl_listen / fl_mix_check / fl_audition
try:
    import fl_capture
    ok, why = fl_capture.available()
    done("listening", "speaker recording works" if ok else f"not available ({why})")
except Exception as e:                                       # noqa: BLE001
    done("listening", f"not available ({type(e).__name__}: {e})")

print("\nNext: fully quit Claude Desktop (tray icon > Quit) and reopen it, do the 'live tools' step above, then ask "
      "Claude: \"run flh_setup\" - it checks everything and lists anything still missing.")
finish(0)
