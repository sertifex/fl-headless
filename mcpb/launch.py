"""Starts fl-headless when it's installed as a Claude Desktop extension (fl-headless.mcpb).

Claude Desktop runs:  uv run --directory <extension folder> mcpb/launch.py <song folder> <song folder> ...
(see mcpb/manifest.json). uv brings Python and numpy (pyproject.toml); Claude's settings page gives the folders the
user picked, one argument each - an extension can't join several folders into one environment variable, so they
arrive here and become FLH_ROOTS (';'-separated, what the server reads). FLH_INSTALLED_BY=mcpb tells setup it's an
extension (messages say "Claude > Settings > Extensions", not "run the installer").

Nothing is written into the extension folder (Claude replaces it on every update): fl-headless keeps its notes,
backups and caches in %LOCALAPPDATA%\\fl-headless as always. Python's own bytecode cache and uv's .venv are the only
things that appear next to the code, and both are rebuilt by themselves after an update."""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the extension folder (the server's files)
_PLACEHOLDER = re.compile(r"^\$\{[^}]*\}$")                           # a setting the user left empty stays literal
KNOWN = {"HOME": None, "DOCUMENTS": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
         "MUSIC": "4BD8D571-6D19-48D3-BE97-422220080E43",
         "DESKTOP": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "DOWNLOADS": "374DE290-123F-4565-9164-39C4925E467B"}
LOCAL_APPDATA_ID = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"


def _known_folder(fid):
    """A Windows known folder (follows OneDrive / moved folders), or None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(fid).bytes_le)
        ptr = ctypes.c_wchar_p()
        f = ctypes.windll.shell32.SHGetKnownFolderPath
        f.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        ok = f(ctypes.byref(guid), 0, None, ctypes.byref(ptr)) == 0 and ptr.value
        val = ptr.value if ok else None
        ctypes.windll.ole32.CoTaskMemFree(ptr)
        return val
    except Exception:                                                 # noqa: BLE001
        return None


def _system_dir(name):
    home = os.path.expanduser("~")
    if name == "HOME":
        return home
    return _known_folder(KNOWN[name]) or os.path.join(home, name.capitalize())


def expand(p):
    """${HOME} / ${DOCUMENTS} / ${DESKTOP} / ${DOWNLOADS} / ${/} / ${pathSeparator} in a folder Claude passed on
    unexpanded (a default value that wasn't substituted), then a normal path."""
    def sub(m):
        k = m.group(1)
        if k in ("/", "pathSeparator"):
            return os.sep
        return _system_dir(k) if k in KNOWN else m.group(0)
    p = re.sub(r"\$\{([^}]+)\}", sub, str(p).strip().strip('"').strip("'").strip())
    return os.path.normpath(p) if p else ""


# PC-VERIFY: Claude Desktop runs "uv" from its own install (no uv on PATH needed) and passes each picked folder as
# one argument, defaults expanded (${HOME} / ${DOCUMENTS}); expand() covers a default passed on literally
# (checklists/install.md, "Claude Desktop extension" 2-3).
def _known_relative(p):
    """'Documents\\Image-Line' -> the real Documents folder + Image-Line. Claude Desktop shows and saves the default
    '${DOCUMENTS}/Image-Line' that way (PC 2026-10-25: the folder was then refused as relative). Only a relative path
    starting with one of these folder names is mapped; anything else relative is still refused."""
    if not p or os.path.isabs(p):
        return p
    parts = re.split(r"[\\/]", p, maxsplit=1)
    name = parts[0].strip().upper()
    if name in KNOWN and name != "HOME":
        return os.path.normpath(os.path.join(_system_dir(name), parts[1] if len(parts) > 1 else ""))
    return p


def folders_from_args(argv):
    """The song folders Claude passed (one per argument) -> (FLH_ROOTS string, [refused entries]). Empty settings,
    relative paths and paths containing ';' (FLH_ROOTS' separator) are refused, never guessed."""
    out, bad = [], []
    for a in argv:
        if not str(a).strip() or _PLACEHOLDER.match(str(a).strip()):
            continue
        p = _known_relative(expand(a))
        if not p or not os.path.isabs(p) or ";" in p or _PLACEHOLDER.search(p) or "${" in p:
            bad.append(str(a))
        elif p not in out:
            out.append(p)
    return ";".join(out), bad


def prepare_env(argv, env=None):
    """Set up the environment the server reads, from Claude's arguments. Returns what was refused (for stderr)."""
    env = os.environ if env is None else env
    for k in [k for k, v in env.items() if k.startswith("FLH_") and _PLACEHOLDER.match(str(v).strip())]:
        del env[k]                                                    # an optional setting left empty
    for k in [k for k in ("FLH_FL_EXE",) if k in env and not env[k].strip()]:
        del env[k]
    roots, bad = folders_from_args(argv)
    if roots or "FLH_ROOTS" not in env:
        env["FLH_ROOTS"] = roots
    env.setdefault("FLH_INSTALLED_BY", "mcpb")
    if os.name == "nt" and not env.get("LOCALAPPDATA"):               # the state folder must never be relative (it
        env["LOCALAPPDATA"] = (_known_folder(LOCAL_APPDATA_ID) or     # would land in the extension folder)
                               os.path.join(os.path.expanduser("~"), "AppData", "Local"))
    elif not env.get("LOCALAPPDATA") and not os.path.isabs(os.path.expanduser("~")):
        import tempfile
        env["LOCALAPPDATA"] = tempfile.gettempdir()
    return bad


def main(argv=None):
    bad = prepare_env(sys.argv[1:] if argv is None else argv)
    if bad:
        print(f"fl-headless: ignored song folder setting(s) {bad}: not a full folder path", file=sys.stderr)
    sys.argv = sys.argv[:1]                                           # the server takes no arguments
    code = os.path.join(HERE, "src")                                  # the server's code and data (2.2.1+)
    if code not in sys.path:
        sys.path.insert(0, code)
    import fl_headless_mcp                                            # as a module: one copy of its globals, also
    fl_headless_mcp.main()                                            # after flh_reload (like install.py's launcher)


if __name__ == "__main__":
    main()
