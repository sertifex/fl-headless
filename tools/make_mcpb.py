"""Build the one-click Claude Desktop extension: dist/fl-headless-<version>.mcpb (an MCP Bundle, manifest v0.4).

Usage:
  python3 tools/make_mcpb.py                     build from the committed tree (HEAD) into dist/
  python3 tools/make_mcpb.py --out DIR           write the .mcpb into DIR
Options:
  --worktree        take the files as they are on disk (tracked files only), not as committed
  --name FILE       the bundle's file name (default fl-headless-<version>.mcpb; fl-headless.mcpb gives a stable
                    'releases/latest/download/fl-headless.mcpb' link)
  --no-selftest     skip starting the staged server (the self-test also fills the manifest's tool list)
  --cli             also run the official validator: npx @anthropic-ai/mcpb validate (needs Node / network)
  --schema FILE     also check the manifest against the official JSON schema file (schemas/mcpb-manifest-v0.4
                    .schema.json from github.com/modelcontextprotocol/mcpb; needs the jsonschema package)
  --check           build and check everything, write no .mcpb
  --json            the report as JSON
Exit codes: 0 ok, 1 a check failed (nothing written), 2 usage.

What goes in: what the public release ships (the release tool's EXCLUDE list and media rules when that tool is
present; in the public repo every tracked file already is the release) minus the folders an extension doesn't need
(tests/, tools/, review/) and the manual installer (install.py / install.bat: Claude installs the extension).
mcpb/manifest.json and mcpb/pyproject.toml go to the bundle's root with the version from SERVER in
src/fl_headless_mcp.py; mcpb/launch.py stays where it is (the entry point). The server type is "uv": Claude Desktop's uv
installs Python and numpy from pyproject.toml, so the bundle carries no Python packages (no lib/, venv/, .venv/).
The spec used: github.com/modelcontextprotocol/mcpb MANIFEST.md (manifest_version 0.4, uv runtime; schema
mcpb-manifest-v0.4, CLI @anthropic-ai/mcpb 2.1.2)."""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
try:                                                    # the release's own rules (dev repo); the public repo has no
    import make_release as MR                           # such tool: what git tracks there IS the release
except ImportError:                                     # noqa: PERF203
    MR = None

MANIFEST_SRC, PYPROJECT_SRC, ENTRY = "mcpb/manifest.json", "mcpb/pyproject.toml", "mcpb/launch.py"
SKIP_PREFIX = ("tests/", "tools/", "review/", ".claude/", "dist/", ".venv/", "venv/", "server/lib/", "server/venv/")
SKIP_FILES = {"install.py": "the manual installer (Claude installs the extension)",
              "install.bat": "the manual installer (Claude installs the extension)",
              ".gitignore": "git only", "AGENTS.md": "contributor notes", "DEVMAP.md": "contributor notes",
              "DEMO.md": "demo script for the README video", MANIFEST_SRC: "goes to the bundle root as manifest.json",
              PYPROJECT_SRC: "goes to the bundle root as pyproject.toml"}
SKIP_SUFFIX = (".mcpb", ".pyc", ".bak", ".tmp", ".log")
CLI_VERSION = "2.1.2"                                   # @anthropic-ai/mcpb used to check this builder
SPEC = "0.4"

# ---------------------------------------------------------------- the manifest rules (schema v0.4, strict objects)
_TOP = {"$schema", "dxt_version", "manifest_version", "name", "display_name", "version", "description",
        "long_description", "author", "repository", "homepage", "documentation", "support", "icon", "icons",
        "screenshots", "localization", "server", "tools", "tools_generated", "prompts", "prompts_generated",
        "keywords", "license", "privacy_policies", "compatibility", "user_config", "_meta"}
_USER_OPT = {"type", "title", "description", "required", "default", "multiple", "sensitive", "min", "max"}
_VAR_OK = re.compile(r"^\$\{(__dirname|pathSeparator|/|user_config\..+)\}$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_URL = re.compile(r"^https?://[^\s/$.?#].[^\s]*$")


def check_manifest(man, names):
    """Errors (list of str) of a manifest against MCPB manifest v0.4 + what Claude needs to run a uv bundle.
    `names` = the bundle's file paths. Mirrors the official zod schema (strict objects) and the CLI's checks
    (entry point exists, icon is a PNG in the bundle, only known ${...} variables in mcp_config)."""
    e = []
    if not isinstance(man, dict):
        return ["manifest.json isn't a JSON object"]
    e += [f"unknown top-level field '{k}'" for k in man if k not in _TOP]
    if man.get("manifest_version") != SPEC:
        e.append(f"manifest_version must be '{SPEC}' (the uv server type needs it), got {man.get('manifest_version')!r}")
    for k in ("name", "version", "description"):
        if not isinstance(man.get(k), str) or not man.get(k).strip():
            e.append(f"'{k}' is required (a non-empty string)")
    if isinstance(man.get("version"), str) and not _SEMVER.match(man["version"]):
        e.append(f"version {man['version']!r} isn't semver")
    a = man.get("author")
    if not isinstance(a, dict) or not isinstance(a.get("name"), str) or set(a) - {"name", "email", "url"}:
        e.append("author must be {name, email?, url?}")
    for k in ("homepage", "documentation", "support"):
        if k in man and not (isinstance(man[k], str) and _URL.match(man[k])):
            e.append(f"{k} must be a URL")
    r = man.get("repository")
    if r is not None and (not isinstance(r, dict) or set(r) != {"type", "url"} or not _URL.match(str(r.get("url")))):
        e.append("repository must be {type, url}")
    s = man.get("server")
    if not isinstance(s, dict) or set(s) - {"type", "entry_point", "mcp_config"}:
        e.append("server must be {type, entry_point, mcp_config}")
        s = {}
    if s.get("type") not in ("python", "node", "binary", "uv"):
        e.append(f"server.type {s.get('type')!r} isn't python | node | binary | uv")
    ep = s.get("entry_point")
    if not isinstance(ep, str) or ep not in names:
        e.append(f"entry point {ep!r} isn't in the bundle")
    elif s.get("type") in ("python", "uv") and not ep.endswith(".py"):
        e.append(f"entry point {ep} of a Python server should be a .py file")
    mc = s.get("mcp_config")
    if not isinstance(mc, dict) or not isinstance(mc.get("command"), str):
        e.append("server.mcp_config with a command is required (v0.4)")
        mc = {}
    if set(mc) - {"command", "args", "env", "platform_overrides"}:
        e.append(f"unknown mcp_config field(s) {sorted(set(mc) - {'command', 'args', 'env', 'platform_overrides'})}")
    cfgs = [("mcp_config.", mc)] + [(f"mcp_config.platform_overrides.{p}.", o)
                                    for p, o in (mc.get("platform_overrides") or {}).items()]
    for pre, c in cfgs:
        vals = [("command", c.get("command"))] if "command" in c else []
        args = c.get("args", [])
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            e.append(f"{pre}args must be a list of strings")
            args = []
        env = c.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            e.append(f"{pre}env must map strings to strings")
            env = {}
        vals += [(f"args[{i}]", x) for i, x in enumerate(args)] + [(f"env.{k}", v) for k, v in env.items()]
        for where, v in vals:
            for m in re.finditer(r"\$\{[^}]+\}", v or ""):
                if not _VAR_OK.match(m.group(0)):
                    e.append(f"invalid variable {m.group(0)} in {pre}{where} (allowed: __dirname, pathSeparator, /, "
                             "user_config.<key>)")
        for k, v in env.items():                       # a list setting can't become one string: Claude refuses it
            for m in re.finditer(r"\$\{user_config\.([^}]+)\}", v):
                opt = (man.get("user_config") or {}).get(m.group(1)) or {}
                if opt.get("multiple"):
                    e.append(f"{pre}env.{k} uses the multiple-value setting {m.group(1)}: only args expand lists")
        for i, x in enumerate(args):
            for m in re.finditer(r"\$\{user_config\.([^}]+)\}", x):
                if m.group(1) not in (man.get("user_config") or {}):
                    e.append(f"{pre}args[{i}] names an unknown setting {m.group(1)}")
                elif (man["user_config"][m.group(1)].get("multiple") and x != m.group(0)):
                    e.append(f"{pre}args[{i}]: a multiple-value setting must be a whole argument to expand")
    uc = man.get("user_config") or {}
    if not isinstance(uc, dict):
        e.append("user_config must be an object")
        uc = {}
    for k, o in uc.items():
        if not isinstance(o, dict):
            e.append(f"user_config.{k} must be an object"); continue
        if set(o) - _USER_OPT:
            e.append(f"user_config.{k}: unknown field(s) {sorted(set(o) - _USER_OPT)}")
        if o.get("type") not in ("string", "number", "boolean", "directory", "file"):
            e.append(f"user_config.{k}.type {o.get('type')!r} isn't string | number | boolean | directory | file")
        for f in ("title", "description"):
            if not isinstance(o.get(f), str):
                e.append(f"user_config.{k}.{f} is required")
        d = o.get("default")
        if d is not None and not (isinstance(d, (str, int, float, bool)) or
                                  (isinstance(d, list) and all(isinstance(x, str) for x in d))):
            e.append(f"user_config.{k}.default must be a string, number, boolean or list of strings")
    comp = man.get("compatibility") or {}
    if set(comp) - {"claude_desktop", "platforms", "runtimes"}:
        e.append("compatibility: only claude_desktop, platforms, runtimes")
    if set(comp.get("platforms") or []) - {"darwin", "win32", "linux"}:
        e.append("compatibility.platforms: darwin | win32 | linux")
    if set(comp.get("runtimes") or {}) - {"python", "node"}:
        e.append("compatibility.runtimes: python | node")
    for i, t in enumerate(man.get("tools") or []):
        if not isinstance(t, dict) or not isinstance(t.get("name"), str) or set(t) - {"name", "description"}:
            e.append(f"tools[{i}] must be {{name, description?}}")
    icon = man.get("icon")
    if icon is not None and (not isinstance(icon, str) or icon.startswith(("http://", "https://", "/"))
                             or "${" in icon or icon not in names):
        e.append(f"icon {icon!r} must be a relative path to a file in the bundle")
    if s.get("type") == "uv":
        if "pyproject.toml" not in names:
            e.append("a uv bundle needs pyproject.toml at its root")
        bad = sorted({n.split("/")[0] + "/" for n in names if n.startswith(("server/lib/", "server/venv/", ".venv/",
                                                                              "venv/"))})
        if bad:
            e.append(f"a uv bundle must not carry Python packages: {bad}")
    return e


# ---------------------------------------------------------------- the files
def _git(*a, inp=None):
    r = subprocess.run(["git", *a], cwd=REPO, input=inp, capture_output=True)
    if r.returncode:
        raise SystemExit(f"git {' '.join(a)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def source_files(worktree=False):
    """[(path, bytes, mode)] of the tracked files (HEAD, or the working tree)."""
    if MR is not None:
        return MR.source_files(worktree)
    if worktree:
        out = []
        for p in _git("ls-files", "-z").decode().split("\0"):
            fp = os.path.join(REPO, p)
            if p and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    out.append((p, f.read(), 0o755 if os.access(fp, os.X_OK) else 0o644))
        return out
    out = []
    with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", "HEAD"))) as t:
        for m in t.getmembers():
            if m.isfile():
                out.append((m.name, t.extractfile(m).read(), m.mode & 0o777))
            elif m.issym():
                raise SystemExit(f"symlink in the tree: {m.name}")
    return out


def skip_why(path):
    """Why a tracked file stays out of the bundle, or None."""
    if MR is not None:
        why = MR.excluded(path)
        if why:
            return "not in the public release: " + why
    if path.startswith(SKIP_PREFIX):
        return "dev / test folder"
    if path in SKIP_FILES:
        return SKIP_FILES[path]
    if path.endswith(SKIP_SUFFIX) or "/__pycache__/" in "/" + path:
        return "local / generated file"
    if os.path.basename(path) in ("fl_headless_launch.py", "install_log.txt", "undo_log.txt", "uv.lock") or \
            re.fullmatch(r"RELEASE\.md", path):            # (the release checklist is dev-only)
        return "dev-only / written on the user's PC"
    return None


def server_version(files):
    src = dict((p, b) for p, b, _ in files).get("src/fl_headless_mcp.py", b"").decode("utf-8", "replace")
    m = re.search(r'SERVER\s*=\s*\{[^}]*"version"\s*:\s*"([^"]+)"', src)
    if not m:
        raise SystemExit("no SERVER version in src/fl_headless_mcp.py")
    return m.group(1)


def stamp_pyproject(text, version):
    """pyproject.toml with [project] version = the server's."""
    new, n = re.subn(r'(?m)^version\s*=\s*"[^"]*"', f'version = "{version}"', text, count=1)
    if not n:
        raise SystemExit(f"{PYPROJECT_SRC} has no version line")
    return new


def plan(files):
    """(bundle {path: (bytes, mode)}, manifest dict, skipped [(path, why)], version)."""
    by = {p: (b, mode) for p, b, mode in files}
    for need in (MANIFEST_SRC, PYPROJECT_SRC, ENTRY, "src/fl_headless_mcp.py"):
        if need not in by:
            raise SystemExit(f"{need} isn't in the tree (commit it, or use --worktree)")
    version = server_version(files)
    man = json.loads(by[MANIFEST_SRC][0].decode("utf-8"))
    man["version"] = version
    bundle, skipped = {}, []
    for p, (b, mode) in sorted(by.items()):
        why = skip_why(p)
        if why:
            skipped.append((p, why))
        else:
            bundle[p] = (b, mode)
    pyproj = stamp_pyproject(by[PYPROJECT_SRC][0].decode("utf-8"), version)
    bundle["pyproject.toml"] = (pyproj.encode("utf-8"), 0o644)
    return bundle, man, skipped, version


def stage(bundle, man, root):
    for p, (b, mode) in bundle.items():
        fp = os.path.join(root, *p.split("/"))
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as f:
            f.write(b)
        os.chmod(fp, mode)
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, indent=2, ensure_ascii=False); f.write("\n")


def self_test(root, timeout=120):
    """Start the staged server the way Claude does (launch.py with folder arguments, FLH_INSTALLED_BY=mcpb), ask it
    for its tools over stdio. -> (ok, why, tools [{name, description}]). Uses this Python (it needs numpy)."""
    tmp = tempfile.mkdtemp(prefix="flh_mcpb_st_")
    songs = os.path.join(tmp, "songs"); os.makedirs(songs)
    env = dict(os.environ, LOCALAPPDATA=os.path.join(tmp, "local"), HOME=tmp, USERPROFILE=tmp, TMPDIR=tmp,
               FLH_INSTALLED_BY="mcpb", FLH_FL_EXE="${user_config.fl_exe}", FLH_NO_WARM="1", FLH_NO_USAGE="1",
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("FLH_ROOTS", None)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "make_mcpb"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "flh_setup", "arguments": {"action": "check"}}}]
    try:
        pr = subprocess.Popen([sys.executable, os.path.join(root, *ENTRY.split("/")), songs],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=root)
        out = {}

        def talk():
            try:
                out["o"], out["e"] = pr.communicate("".join(json.dumps(x) + "\n" for x in msgs).encode(), timeout)
            except Exception as ex:                       # noqa: BLE001
                out["x"] = ex
        t = threading.Thread(target=talk, daemon=True); t.start(); t.join(timeout + 5)
        if "o" not in out:
            pr.kill()
            return False, f"no answer within {timeout} s ({out.get('x')})", []
        rep = {}
        for line in out["o"].decode("utf-8", "replace").splitlines():
            try:
                r = json.loads(line); rep[r.get("id")] = r
            except ValueError:
                continue
        tools = ((rep.get(2) or {}).get("result") or {}).get("tools") or []
        if not tools:
            err = out["e"].decode("utf-8", "replace").strip().splitlines()
            return False, "it didn't list its tools: " + (err[-1][:300] if err else "no output"), []
        txt = "".join(c.get("text", "") for c in ((rep.get(3) or {}).get("result") or {}).get("content", []))
        try:
            chk = json.loads(txt)
        except ValueError:
            chk = {}
        roots = (chk.get("writable_roots") or {}).get("FLH_ROOTS")
        if os.path.normcase(os.path.abspath(songs)) not in (roots or []):
            return False, f"the folder argument didn't become FLH_ROOTS (setup saw {roots!r})", tools
        if chk.get("installed_as") != "claude_extension":
            return False, f"setup doesn't see the extension context (installed_as={chk.get('installed_as')!r})", tools
        return True, f"starts and answers ({len(tools)} tools listed), folder argument -> FLH_ROOTS", tools
    finally:
        shutil.rmtree(tmp, True)


def tool_list(tools, n=60):
    """The manifest's 'tools' (what Claude's extension page shows): the listed tools, first sentence each."""
    out = []
    for t in tools[:n]:
        d = re.split(r"(?<=[.!?])\s", str(t.get("description") or "").strip(), maxsplit=1)[0]
        out.append({"name": t["name"], "description": (d[:157] + "...") if len(d) > 160 else d})
    return out


def write_zip(root, dest, date_tuple):
    """A deterministic zip of the staged folder (sorted, fixed timestamps, deflate 9)."""
    names = []
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for f in sorted(fn):
            names.append(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/"))
    tmp = dest + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for n in sorted(names):
            fp = os.path.join(root, *n.split("/"))
            zi = zipfile.ZipInfo(n, date_time=date_tuple)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3
            zi.external_attr = ((os.stat(fp).st_mode & 0o777) | 0o100000) << 16
            with open(fp, "rb") as f:
                z.writestr(zi, f.read(), compresslevel=9)
    os.replace(tmp, dest)
    return names


def cli_validate(root):
    """The official validator on the staged manifest: (ok or None when it can't run, output)."""
    npx = shutil.which("npx")
    if not npx:
        return None, "npx not found"
    try:
        r = subprocess.run([npx, "-y", f"@anthropic-ai/mcpb@{CLI_VERSION}", "validate",
                            os.path.join(root, "manifest.json")], capture_output=True, text=True, timeout=300,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as ex:
        return None, str(ex)
    txt = (r.stdout + r.stderr).strip()
    return r.returncode == 0 and "ERROR" not in txt, txt[-2000:]


def schema_validate(man, path):
    """Errors of `man` against an official MCPB JSON schema file, or None when jsonschema isn't installed."""
    try:
        import jsonschema
    except ImportError:
        return None
    with open(path, encoding="utf-8") as f:
        schema = json.load(f)
    return [f"{'/'.join(str(x) for x in er.path) or '(top)'}: {er.message}"
            for er in jsonschema.Draft7Validator(schema).iter_errors(man)]


def build(out_dir=None, worktree=False, name=None, selftest=True, cli=False, check_only=False, schema=None):
    t0 = time.time()
    files = source_files(worktree)
    bundle, man, skipped, version = plan(files)
    rep = {"version": version, "source": "working tree" if worktree else "HEAD", "spec": f"MCPB manifest {SPEC}",
           "files": len(bundle) + 1, "skipped": len(skipped), "errors": [], "steps_s": {}}
    root = tempfile.mkdtemp(prefix="flh_mcpb_")
    try:
        stage(bundle, man, root)
        rep["steps_s"]["stage"] = round(time.time() - t0, 2)
        if selftest:
            t = time.time()
            ok, why, tools = self_test(root)
            rep["selftest"] = why; rep["steps_s"]["selftest"] = round(time.time() - t, 2)
            if not ok:
                rep["errors"].append("self-test: " + why)
            if tools:
                man["tools"] = tool_list(tools)
                stage({}, man, root)                       # rewrite manifest.json with the tool list
        names = sorted(list(bundle) + ["manifest.json"])
        rep["errors"] += check_manifest(man, set(names))
        if schema:
            se = schema_validate(man, schema)
            rep["schema"] = {"file": os.path.basename(schema), "ok": None if se is None else not se, "errors": se}
            rep["errors"] += [f"official schema: {x}" for x in (se or [])]
        if cli:
            t = time.time()
            ok, txt = cli_validate(root)
            rep["cli"] = {"ok": ok, "output": txt}; rep["steps_s"]["cli"] = round(time.time() - t, 2)
            if ok is False:
                rep["errors"].append("official validator: " + txt[-400:])
        if rep["errors"] or check_only:
            return rep
        out_dir = os.path.abspath(out_dir or os.path.join(REPO, "dist"))
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, name or f"fl-headless-{version}.mcpb")
        when = _git("log", "-1", "--format=%cd", "--date=format:%Y %m %d %H %M %S").decode().split() \
            if not worktree else time.strftime("%Y %m %d %H %M %S").split()
        write_zip(root, dest, tuple(max(1980, int(x)) if i == 0 else int(x) for i, x in enumerate(when)))
        rep["out"] = dest; rep["bytes"] = os.path.getsize(dest)
    finally:
        shutil.rmtree(root, True)
        rep["steps_s"]["total"] = round(time.time() - t0, 2)
    return rep


def main(argv):
    opts = {"out": None, "worktree": False, "name": None, "selftest": True, "cli": False, "check": False,
            "json": False, "schema": None}
    it = iter(argv)
    for a in it:
        if a in ("-h", "--help"):
            print(__doc__); return 0
        if a == "--out":
            opts["out"] = next(it, None)
        elif a == "--schema":
            opts["schema"] = next(it, None)
        elif a == "--name":
            opts["name"] = next(it, None)
        elif a in ("--worktree", "--cli", "--check", "--json"):
            opts[a[2:]] = True
        elif a == "--no-selftest":
            opts["selftest"] = False
        else:
            print(f"unknown option {a}\n\n{__doc__}"); return 2
    if opts["name"] and (not opts["name"].endswith(".mcpb") or os.sep in opts["name"] or "/" in opts["name"]):
        print("--name must be a file name ending in .mcpb"); return 2
    rep = build(opts["out"], opts["worktree"], opts["name"], opts["selftest"], opts["cli"], opts["check"],
                opts["schema"])
    if opts["json"]:
        print(json.dumps(rep, indent=1))
    else:
        print(f"fl-headless {rep['version']} from {rep['source']} ({rep['spec']}): {rep['files']} files, "
              f"{rep['skipped']} left out")
        if rep.get("selftest"):
            print("self-test: " + rep["selftest"])
        if rep.get("schema"):
            sc = rep["schema"]
            print(f"official schema {sc['file']}: " + ("ok" if sc["ok"] else "not checked (no jsonschema)"
                                                         if sc["ok"] is None else "FAILED"))
        if rep.get("cli"):
            print(f"official validator: {'ok' if rep['cli']['ok'] else 'FAILED' if rep['cli']['ok'] is False else 'not run'}"
                  f" - {rep['cli']['output'].splitlines()[-1] if rep['cli']['output'] else ''}")
        for e in rep["errors"]:
            print("ERROR: " + e)
        if rep.get("out"):
            print(f"built {rep['out']} ({rep['bytes'] / 1e6:.2f} MB) in {rep['steps_s']['total']} s")
        elif not rep["errors"]:
            print("checked, nothing written (--check)")
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
