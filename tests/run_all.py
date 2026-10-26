"""Run the test suite (or only what a change affects).

  python3 tests/run_all.py                    every test, 2 at a time (--jobs N)
  python3 tests/run_all.py compose newsong    tests whose file name contains one of the words
  python3 tests/run_all.py --changed          only the tests affected by what changed vs git HEAD (+ untracked files)
  python3 tests/run_all.py --changed HEAD~3   ... vs another revision
  python3 tests/run_all.py --files flh_compose.py data/genres/trap.json    ... by these files
  python3 tests/run_all.py --changed --list   show the selection and why, run nothing
Options: --jobs N / -j N (default 2), --slowest [N] (every test's time, slowest first; default 15 rows), --deep (with
--changed/--files: follow callers in the server file without a depth limit), --no-smoke, --why (reason per test).

Each test runs in its own process with its own TMPDIR, HOME and LOCALAPPDATA (removed afterwards: the suite never fills
the disk and parallel tests never share fl-headless's state folder). Tests in SERIAL run alone after the others.
Longest tests start first (times remembered in the dev cache). Prints PASS/FAIL per test and a summary line; exits 1
when anything failed.

Selection (--changed / --files) uses an import/usage map built from the sources and cached in the dev cache
(review/.devcache/, or FLH_DEV_CACHE; rebuilt for files whose mtime/size changed):
  - a test is affected by a module it names (import, register, sys.modules["..."], __import__) and by the modules
    those name, by the tools a changed module registers (TOOLS[...] = ...) and the ns names it exports (ns["x"] = ...)
  - fl_headless_mcp.py (every test imports it) is mapped per top-level def / assignment: the changed lines' symbols,
    their callers (2 levels; --deep: all), the tools wrapping them -> the tests and modules that use those names
  - data/<sub>/... -> the modules and server symbols that name "<sub>" (data/genres -> flh_compose -> compose,
    newsong, ... tests); other files -> the tests / modules that name the file
  - always: the smoke set (SMOKE) and an in-runner registry check (the server imports and every extension registers)
  - more than MAX_SHARE of all tests selected -> the whole suite (said so)."""
import ast, bisect, json, os, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MAIN = "fl_headless_mcp"
SMOKE = ("test_help.py", "test_core_wave2.py")       # manual search + tool tiers / discovery / registry sets
# Tests that must not run beside others (they change files outside their own temp folder). Everything else is
# isolated by the per-test TMPDIR / HOME / LOCALAPPDATA this runner sets.
SERIAL = {
    "test_install.py": "install.py writes fl_headless_launch.py next to the server (the repo); the e2e / pc-check "
                       "tests watch the repo for new files",
    "test_bughunt_setup.py": "runs install.py, which rewrites fl_headless_launch.py in the repo (same reason)",
}
# first-run order before the dev cache knows the times (longest first keeps both workers busy to the end)
DEFAULT_TIMES = {"test_finalize.py": 76, "test_pccheck.py": 57, "test_corpus.py": 53, "test_pccheck_mod.py": 40,
                 "test_vocal.py": 41, "test_fuzz.py": 40, "test_e2e.py": 30}
MAX_SHARE = 0.8
DISPATCH = {"handle", "run_call", "flh_run", "flh_call", "flp_edit", "_plan_ops", "_soft_deadline_call", "estimate",
            "_estimate"}                             # call everything by name: reached as callers they select nothing
LISTS = {"EXTENSIONS", "HELPER_MODULES"}             # server names that list modules without using them
DEPTH = 2
HUB_REFS = 40                                        # a server name used by more symbols than this isn't followed
CACHE_VER = 7
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# how an extension reaches server / other-extension names at call time: H["x"], ns.get("x"), ns["TOOLS"]["x"], T["x"]
NS_USE = re.compile(r"""(?:\b(?:H|ns|NS|_H|_NS|HOST|host|core|self\.ns|ctx\.ns|self\.H)\s*(?:\[\s*|\.get\(\s*)"""
                    r"""|\[\s*["']TOOLS["']\s*\]\s*\[\s*|\bT\[\s*|\b_tool\(\s*)["']([A-Za-z_]\w*)["']""")


# ============================================================================ dev cache
def cache_dir():
    d = os.environ.get("FLH_DEV_CACHE") or os.path.join(REPO, "review", ".devcache")
    os.makedirs(d, exist_ok=True)
    return d


def _load_json(name, default):
    try:
        with open(os.path.join(cache_dir(), name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(name, obj):
    p = os.path.join(cache_dir(), name); tmp = p + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, p)


# ============================================================================ sources
def _sig(p):
    st = os.stat(p)
    return [st.st_mtime_ns, st.st_size]


def py_files():
    """{module name: path} of the repo's Python: top-level modules, tests/*.py, tools/*.py, fl_script/*.py."""
    out = {}
    for sub in ("", "tests", "tools", "fl_script"):
        d = os.path.join(REPO, sub)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.endswith(".py") and f not in ("fl_headless_launch.py", "run_all.py"):
                out.setdefault(f[:-3], os.path.join(d, f))
    return out


def _read(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def _main_symbols(text):
    """Top-level statements of the server file: [[names defined, first line, last line, names used, tool keys, kind,
    identifier-like strings used]]
    (kind: def / assign / import / expr / other; if/try blocks at the top level count as their statements)."""
    tree = ast.parse(text)
    out = []

    def used(node):
        u, strs = set(), set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                u.add(n.id)
            elif isinstance(n, ast.Attribute):
                u.add(n.attr)
            elif isinstance(n, ast.alias):                        # lazy imports inside functions
                u.add(n.name.split(".")[0])
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) >= 4 and \
                    WORD.fullmatch(n.value):
                strs.add(n.value)                                 # sys.modules["x"], globals()["x"], tool names
        return u, strs

    def visit(stmts):
        for st in stmts:
            names, tools, kind = set(), set(), "other"
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(st.name); kind = "def"
            elif isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                kind = "assign"
                for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            names.add(n.id)
                    if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and \
                            isinstance(t.slice.value, str):          # TOOLS["fl_x"] = (fn, ...) / LONG_HELP["x"]
                        names.discard(getattr(t.value, "id", None)); tools.add(t.slice.value)
            elif isinstance(st, (ast.If, ast.Try, ast.With)):
                inner = list(getattr(st, "body", [])) + list(getattr(st, "orelse", [])) + \
                    [x for h in getattr(st, "handlers", []) for x in h.body] + list(getattr(st, "finalbody", []))
                visit(inner); continue
            elif isinstance(st, (ast.Import, ast.ImportFrom)):
                names.update((a.asname or a.name).split(".")[0] for a in st.names); kind = "import"
            elif isinstance(st, ast.Expr):
                kind = "doc" if isinstance(st.value, ast.Constant) else "expr"
            u, strs = used(st)
            entries = {}
            val = getattr(st, "value", None)
            if kind == "assign" and isinstance(val, ast.Dict):        # TOOLS = {"fl_x": (fl_x, ...), ...}: per key
                for kk, vv in zip(val.keys, val.values):
                    if isinstance(kk, ast.Constant) and isinstance(kk.value, str):
                        entries[kk.value] = sorted(used(vv)[0])
                if len(val.keys) > 8:
                    kind = "table"
            elif kind == "assign" and isinstance(val, (ast.List, ast.Tuple, ast.Set)) and len(val.elts) > 8:
                kind = "table"
            out.append([sorted(names), st.lineno, getattr(st, "end_lineno", st.lineno), sorted(u - names),
                        sorted(tools), kind, sorted(strs - u - names), entries])
    visit(tree.body)
    return out


def _refs(text):
    """Module names a file loads: import / from lines (any indentation, comma lists) and quoted module-like names
    (sys.modules["x"], __import__("x"), ("flh_store", "flh_compose") loops, EXTENSIONS)."""
    out = set()
    for mm in re.finditer(r"^[ \t]*import[ \t]+([\w., \t]+)", text, re.M):
        out |= {x.strip().split(" ")[0].split(".")[0] for x in mm.group(1).split(",") if x.strip()}
    out |= set(re.findall(r"^[ \t]*from[ \t]+(\w+)", text, re.M))
    out |= set(re.findall(r"""["']([a-z][a-z0-9]*_[a-z0-9_]+)["']""", text))
    return sorted(out)


def build_map():
    """The usage map, from the cache where files are unchanged."""
    files = py_files()
    old = _load_json("testmap.json", {})
    if old.get("ver") != CACHE_VER:
        old = {}
    ent, changed = {}, False
    for name, p in files.items():
        sig = _sig(p); o = (old.get("files") or {}).get(name)
        if o and o["sig"] == sig and o["path"] == p:
            ent[name] = o; continue
        changed = True
        text = _read(p)
        e = {"sig": sig, "path": p, "tokens": sorted(set(WORD.findall(text)))}
        e["refs"] = _refs(text)
        e["nsuses"] = sorted(set(NS_USE.findall(text)))
        if name != MAIN:
            e["exports"] = sorted(set(re.findall(r"""ns\[\s*["']([A-Za-z_]\w*)["']\s*\]\s*=(?!=)""", text)))
            e["registers"] = sorted(set(re.findall(r"""\[\s*["']([a-z][a-z0-9]*_[a-z0-9_]+)["']\s*\]\s*=(?!=)""",
                                                   text)))
        else:
            try:
                e["symbols"] = _main_symbols(text)
            except SyntaxError as ex:
                e["symbols"] = None; e["syntax"] = str(ex)
            e["top_imports"] = sorted(set(re.findall(r"^(?:import|from)\s+(\w+)", text, re.M)))
        ent[name] = e
    if changed or set(ent) != set((old.get("files") or {})):
        tools = _tool_modules() if changed else old.get("tools", {})
        mp = {"ver": CACHE_VER, "files": ent, "tools": tools}
        _save_json("testmap.json", mp)
        return mp
    return old


def _tool_modules():
    """{tool: registering module} from importing the server in a throwaway sandbox (extensions included)."""
    tmp = tempfile.mkdtemp(prefix="flh_map_")
    env = dict(os.environ, TMPDIR=tmp, HOME=tmp, LOCALAPPDATA=tmp, FLH_ROOTS=tmp, FLH_NO_USAGE="1", FLH_NO_WARM="1",
               FLH_UPDATE_CHECK="0")
    code = ("import json, fl_headless_mcp as m\n"
            "print(json.dumps({'tools': {t: m._TOOL_MODULE.get(t, 'fl_headless_mcp') for t in m.TOOLS},"
            " 'errors': m._EXT_STATUS.get('errors')}))")
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True,
                           timeout=120, stdin=subprocess.DEVNULL)
        return json.loads(r.stdout.strip().splitlines()[-1])["tools"]
    except Exception:                                             # noqa: BLE001 - selection still works by name
        return {}
    finally:
        shutil.rmtree(tmp, True)


# ============================================================================ what changed
def git(*a):
    r = subprocess.run(["git", "-C", REPO] + list(a), capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {r.stderr.strip()}")
    return r.stdout


def changed_files(rev="HEAD"):
    out = set(x for x in git("diff", "--name-only", rev, "--").splitlines() if x)
    out |= set(x for x in git("ls-files", "--others", "--exclude-standard").splitlines() if x)
    return sorted(out)


def _hunks(rel, rev):
    """(new-file line ranges, old-file line ranges) changed in rel vs rev; None = the whole file is new."""
    try:
        diff = git("diff", "-U0", rev, "--", rel)
    except RuntimeError:
        return None
    if not diff.strip():
        try:
            git("cat-file", "-e", f"{rev}:{rel}")
        except RuntimeError:
            return None                                           # untracked / new: everything
        return [], []
    new, old = [], []
    for mm in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        a, b, c, d = int(mm.group(1)), int(mm.group(2) or 1), int(mm.group(3)), int(mm.group(4) or 1)
        new.append((c, c + max(d, 1) - 1)); old.append((a, a + max(b, 1) - 1)) if b else None
    return new, old


def _syms_at(symbols, ranges):
    starts = [s[1] for s in symbols]; hit, loose = set(), False
    for lo, hi in ranges:
        k = max(0, bisect.bisect_right(starts, hi) - 1)
        found = False
        while k >= 0 and k < len(symbols) and symbols[k][1] <= hi:
            s = symbols[k]
            if s[2] >= lo:
                hit.add(k); found = True
            if s[1] < lo:
                break
            k -= 1
        loose |= not found
    return hit, loose


# ============================================================================ selection
def select(paths, mp, rev="HEAD", deep=False):
    """({test file: reason}, [reasons to run everything]) for the changed repo-relative paths."""
    F = mp["files"]
    tests = {n for n, e in F.items() if n.startswith("test_") and os.path.dirname(e["path"]) == HERE}
    mods = {n for n in F if n not in tests and n != MAIN}
    toks = {n: set(e["tokens"]) for n, e in F.items()}
    refs = {n: set(e.get("refs", ())) for n, e in F.items()}
    tool_mod = mp.get("tools") or {}
    main = F.get(MAIN) or {}
    syms = main.get("symbols")
    words, reasons, full, seeds, texts = {}, {}, [], {}, {}
    main_names = {nm for sy in (syms or []) for nm in sy[0]}

    def text_of(n):
        if n not in texts:
            texts[n] = _read(F[n]["path"])
        return texts[n]

    def add_word(w, r):
        if w and w not in words:
            words[w] = r

    def module_hit(name, r):
        """A module is affected: its name, the tools it registers and the ns names it exports become affected."""
        if name in tests:
            reasons.setdefault(name + ".py", r); return
        if name == MAIN or name in words:
            return
        add_word(name, r)
        e = F.get(name) or {}
        for t in sorted(set(t for t, mo in tool_mod.items() if mo == name) |
                        set(t for t in e.get("registers", []) if t in tool_mod)):
            add_word(t, f"{r}: tool {t}")
        for x in e.get("exports", []):
            if x not in main_names:
                add_word(x, f"{r}: ns['{x}']")

    used = set()

    def module_used(name, r):
        e = F.get(name) or {}
        for t in sorted(set(t for t, mo in tool_mod.items() if mo == name) |
                        set(t for t in e.get("registers", []) if t in tool_mod)):
            add_word(t, f"{r}: tool {t}")
        for x in e.get("exports", []):
            if x not in main_names:                               # ns["ESSENTIAL"] |= ...: a registry, not a function
                add_word(x, f"{r}: ns['{x}']")

    def named_by(rx, r, modules=True, need=None):
        """What names a data folder / file (a regex on the source): tests always, modules and server symbols too.
        need: a plain string every hit contains (a cheap pre-check)."""
        for n in F:
            if n == MAIN or (not modules and n not in tests):
                continue
            if (need is None or need in text_of(n)) and rx.search(text_of(n)):
                module_hit(n, r)
        if modules and syms and (need is None or need in text_of(MAIN)):
            t = text_of(MAIN); starts = [sy[1] for sy in syms]
            for mm in rx.finditer(t):                             # the symbols around each hit
                ln = t.count("\n", 0, mm.start()) + 1
                k = bisect.bisect_right(starts, ln) - 1
                if k >= 0 and syms[k][1] <= ln <= syms[k][2]:
                    seeds.setdefault(k, r)

    for rel in paths:
        rel = rel.replace("\\", "/")
        base = os.path.basename(rel); stem, ext = os.path.splitext(base); top = rel.split("/")[0]
        if top == "review" or top == ".claude" or "__pycache__" in rel or ext in (".pyc", ".tmp", ".log"):
            continue
        if rel.startswith("tests/") and base.startswith("test_") and ext == ".py" and rel.count("/") == 1:
            reasons.setdefault(base, f"{rel} changed"); continue
        if ext == ".py" and stem == MAIN and "/" not in rel:
            if syms is None:
                full.append(f"{rel} doesn't parse ({main.get('syntax', '')[:80]})"); continue
            h = _hunks(rel, rev)
            if h is None:
                full.append(f"{rel}: no diff against {rev}"); continue
            hit, loose = _syms_at(syms, h[0])
            if loose:
                hit |= _syms_at(syms, [(lo - 1, hi + 1) for lo, hi in h[0]])[0]
            if h[1]:                                              # lines removed: the symbols they were in
                try:
                    old = _main_symbols(git("show", f"{rev}:{rel}"))
                    for k in _syms_at(old, h[1])[0]:
                        for nm in old[k][0] + old[k][4]:
                            add_word(nm, f"{MAIN}.py: {nm} changed")
                        if old[k][5] in ("import", "expr"):
                            full.append(f"{MAIN}.py: a top-level {old[k][5]} changed (line {old[k][1]})")
                except Exception:                                 # noqa: BLE001
                    pass
            for k in hit:
                if syms[k][5] in ("import", "expr"):
                    full.append(f"{MAIN}.py: a top-level {syms[k][5]} changed (line {syms[k][1]})")
                seeds.setdefault(k, f"{MAIN}.py: " + ((syms[k][0] or syms[k][4] or [f"line {syms[k][1]}"])[0])
                                 + " changed")
            continue
        if ext == ".py" and stem in F and stem != MAIN:
            module_hit(stem, f"{rel} changed"); continue
        if top == "data":
            parts = rel.split("/")
            key = re.escape(parts[1] if len(parts) > 2 else base)
            # data/x named as a data path: "data", "x" / DATA, "x" / _data_dirs("x") / data/x
            rx = re.compile(r"""(?:["']data["']|(?:DATA|[dD]ata)\w*)\s*[,(]\s*["']%s["']|data/%s\b""" % (key, key))
            named_by(rx, f"{rel} changed", need=parts[1] if len(parts) > 2 else base)
            continue
        # docs, checklists, fixtures, launch scripts ...: what names the file (tests always, code when it's code)
        rx = re.compile(re.escape(base) + (r"|\b" + re.escape(top) + "/" if "/" in rel and top != "tests" else ""))
        named_by(rx, f"{rel} changed", modules=ext not in (".md", ".txt"), need=None if "/" in rel else base)
        if top == "tests" and "/" in rel:                         # tests/corpus/x.flp, tests/fixtures/...
            sub = rel.split("/")[1]
            named_by(re.compile(r"""["']%s["']|tests/%s\b""" % (re.escape(sub), re.escape(sub))),
                     f"{rel} changed", modules=False)

    # fixed point: server symbols -> callers -> names/tools -> modules using them -> server symbols naming those
    users = {}
    for k, sy in enumerate(syms or []):
        if sy[5] == "table":
            continue                                              # a table lists functions, it doesn't call them
        for u in sy[3]:
            users.setdefault(u, []).append(k)
    done, caller = set(), {}                                     # caller: symbol -> the affected name it uses
    for _round in range(6):
        n_words = len(words)
        if syms:
            aff = {k for k in seeds if k not in done}
            frontier, depth = set(aff), 0
            while frontier and (deep or depth < DEPTH):
                nxt = {}
                for k in frontier:
                    for nm in syms[k][0]:
                        if nm in DISPATCH:
                            continue
                        us = users.get(nm, [])
                        if len(us) > HUB_REFS and k not in seeds:
                            continue                              # a hub reached through a caller: not followed
                        for u in us:
                            nxt.setdefault(u, nm)
                frontier = set(nxt) - aff - done - set(seeds)
                for k in frontier:
                    caller[k] = nxt[k]
                aff |= frontier; depth += 1
            affn = set()
            for k in aff:
                sy = syms[k]
                if k in seeds:
                    lbl = seeds[k]
                else:
                    lbl = f"{MAIN}.py: {(sy[0] or sy[4] or ['?'])[0]} calls {caller.get(k)}"
                for nm in sy[0] + sy[4]:
                    if k not in seeds and (len(users.get(nm, ())) > HUB_REFS or nm in DISPATCH):
                        continue                                  # TOOLS, _state, handle ...: reached via a caller
                    add_word(nm, lbl); affn.add(nm)
            for sy in syms:                                       # TOOLS["x"] = (an affected function, ...)
                if sy[4] and affn & set(sy[3]):
                    for t in sy[4]:
                        add_word(t, f"{MAIN}.py: tool {t} ({sorted(affn & set(sy[3]))[0]})")
                for key, nms in (sy[7] or {}).items():            # TOOLS = {"x": (an affected function, ...)}
                    if affn & set(nms):
                        add_word(key, f"{MAIN}.py: {(sy[0] or ['?'])[0]}['{key}'] ({sorted(affn & set(nms))[0]})")
            done |= aff
        mod_words = {w for w in words if w in mods}
        use_words = {w for w in words if w not in mods and w not in tool_mod}    # functions / constants / ns names
        for n in sorted(mods):
            if n in words or n in used:
                continue
            hit = (refs[n] & mod_words) - {n}
            if hit:                                               # it imports an affected module: its tools
                w = sorted(hit)[0]; used.add(n)
                module_used(n, f"{n} imports {w}"); continue
            hit = set(F[n].get("nsuses", ())) & use_words
            if hit:                                               # it calls an affected function: its tools
                w = sorted(hit)[0]; used.add(n)
                module_used(n, f"{n} uses {w}")
        if syms:                                                  # server symbols naming an affected module/export
            wset = set(words)
            for k, sy in enumerate(syms):
                if k not in done and k not in seeds and sy[5] != "table" and not set(sy[0]) & (LISTS | DISPATCH) and \
                        ((set(sy[3]) | set(sy[6])) & wset & (mods | {x for n in mods for x in F[n].get("exports", [])})):
                    seeds[k] = f"{MAIN}.py: {(sy[0] or sy[4] or ['?'])[0]} uses " \
                               f"{sorted((set(sy[3]) | set(sy[6])) & wset)[0]}"
        if len(words) == n_words and all(k in done for k in seeds):
            break
    wset = set(words)
    for n in sorted(tests):
        if n + ".py" in reasons:
            continue
        hit = (toks[n] & (wset - mods)) | (refs[n] & wset & mods)
        if hit:
            w = min(hit, key=lambda x: (x not in mods, len(words[x]), x))
            reasons[n + ".py"] = w if words[w].startswith(w) else f"{w} <- {words[w]}"
    return reasons, full


# ============================================================================ running
def run(path, root, timeout=900):
    name = os.path.basename(path)[:-3]
    tmp = tempfile.mkdtemp(prefix=name + "_", dir=root)
    home = os.path.join(tmp, "home"); local = os.path.join(tmp, "localappdata")
    os.makedirs(home); os.makedirs(local)
    env = dict(os.environ, TMPDIR=tmp, TEMP=tmp, TMP=tmp, HOME=home, USERPROFILE=home, LOCALAPPDATA=local,
               APPDATA=os.path.join(tmp, "appdata"), FLH_NO_WARM="1", FLH_UPDATE_CHECK="0")
    env.pop("FLH_ROOTS", None)
    t = time.time()
    try:
        r = subprocess.run([sys.executable, path], cwd=REPO, env=env, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        out, code, so = (r.stdout + r.stderr).strip(), r.returncode, r.stdout.strip()
    except subprocess.TimeoutExpired:
        out, code, so = f"timed out after {timeout} s", -1, ""
    finally:
        shutil.rmtree(tmp, True)
    last = so.splitlines()[-1] if so else ""           # stdout's last line: a warning on stderr can't hide 'ok'
    ok = code == 0 and any(w in last for w in ("ok", "OK", "skipped"))
    return path, ok, round(time.time() - t, 1), out


def registry_check():
    """The server imports in a sandbox and every extension registers (the server tolerates a broken one silently)."""
    tmp = tempfile.mkdtemp(prefix="flh_reg_")
    env = dict(os.environ, TMPDIR=tmp, HOME=tmp, LOCALAPPDATA=tmp, FLH_ROOTS=tmp, FLH_NO_USAGE="1", FLH_NO_WARM="1",
               FLH_UPDATE_CHECK="0")
    code = ("import json, fl_headless_mcp as m\n"
            "bad = {t: type(e).__name__ for t, e in m.TOOLS.items() if not (isinstance(e, tuple) and callable(e[0]) "
            "and isinstance(e[1], str) and isinstance(e[2], dict))}\n"
            "print(json.dumps({'errors': m._EXT_STATUS.get('errors'), 'bad': bad, 'tools': len(m.TOOLS)}))")
    t = time.time()
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True,
                           timeout=120, stdin=subprocess.DEVNULL)
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
            ok = not res["errors"] and not res["bad"] and res["tools"] > 50
            out = json.dumps(res)[:1500]
        except (ValueError, IndexError):
            ok, out = False, (r.stdout + r.stderr)[-1500:]
    finally:
        shutil.rmtree(tmp, True)
    return "registry", ok, round(time.time() - t, 1), out


def main(argv):
    a = list(argv)

    def flag(name):
        if name in a:
            a.remove(name); return True
        return False

    def opt(names, default=None, const=None, ok=lambda v: True):
        """--name VALUE, or --name alone (= const) when the next word isn't a value for it."""
        for n in names:
            if n in a:
                i = a.index(n)
                if i + 1 < len(a) and not a[i + 1].startswith("-") and ok(a[i + 1]):
                    v = a[i + 1]; del a[i:i + 2]; return v
                del a[i]; return const
        return default

    def is_rev(v):
        try:
            git("rev-parse", "--verify", "--quiet", v + "^{commit}"); return True
        except RuntimeError:
            return False
    jobs = int(opt(["--jobs", "-j"], "2", "2", str.isdigit))
    slowest = opt(["--slowest"], None, "15", str.isdigit)
    changed_rev = opt(["--changed"], None, "HEAD", is_rev)
    deep, listing, no_smoke, show_why = flag("--deep"), flag("--list"), flag("--no-smoke"), flag("--why")
    files = None
    if "--files" in a:
        i = a.index("--files"); j = i + 1
        while j < len(a) and not a[j].startswith("-"):
            j += 1
        files = a[i + 1:j]; del a[i:j]
    names = [x for x in a if not x.startswith("-")]
    unknown = [x for x in a if x.startswith("-")]
    if unknown:
        print(f"unknown option(s): {unknown}\n" + __doc__); return 2
    all_tests = sorted(f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    tests, why, note = list(all_tests), {}, ""
    t_sel = time.time()
    if changed_rev or files is not None:
        mp = build_map()
        if files is not None:
            paths = [os.path.relpath(os.path.abspath(f), REPO) if os.path.exists(f) else f for f in files]
        else:
            paths = changed_files(changed_rev)
        why, full = select(paths, mp, rev=changed_rev or "HEAD", deep=deep)
        why = {t: r for t, r in why.items() if t in all_tests}
        if not no_smoke:
            for s in SMOKE:
                why.setdefault(s, "smoke")
        src = f"--changed {changed_rev}" if changed_rev else "--files"
        if full:
            tests = list(all_tests); note = f"whole suite: {'; '.join(full[:3])}"
        elif len(why) > MAX_SHARE * len(all_tests):
            tests = list(all_tests); note = f"whole suite: the change reaches {len(why)} of {len(all_tests)} tests"
        else:
            tests = sorted(why)
            note = f"{len(tests)} of {len(all_tests)} tests ({src}: {len(paths)} changed file(s))"
        note += f"; selected in {time.time() - t_sel:.1f} s"
    if names:
        tests = [t for t in tests if any(n in t for n in names)]
    if listing or show_why:
        print(note or f"{len(tests)} tests")
        for t in tests:
            print(f"  {t:34s} {why.get(t, '')[:110]}")
        if listing:
            return 0
    timings = _load_json("timings.json", {})
    par = sorted((t for t in tests if t not in SERIAL), key=lambda t: -float(timings.get(t, DEFAULT_TIMES.get(t, 10.0))))
    ser = [t for t in tests if t in SERIAL]
    root = tempfile.mkdtemp(prefix="flh_tests_")
    t0 = time.time(); results = []

    def report(path, ok, took, out):
        results.append((os.path.basename(path), ok, took))
        name = os.path.basename(path)
        if ok:
            print(f"PASS {name} ({took} s)", flush=True)
        else:
            tail = "\n    ".join(out.splitlines()[-12:])
            print(f"FAIL {name} ({took} s)\n    {tail}", flush=True)
    try:
        with ThreadPoolExecutor(max(1, jobs)) as ex:
            futs = [ex.submit(run, os.path.join(HERE, t), root) for t in par]
            if changed_rev or files is not None:
                futs.append(ex.submit(registry_check))
            for f in as_completed(futs):
                report(*f.result())
        for t in ser:                                             # alone: nothing else runs meanwhile
            report(*run(os.path.join(HERE, t), root))
    finally:
        shutil.rmtree(root, True)
    wall = time.time() - t0
    for n, ok, took in results:
        if n.endswith(".py"):
            timings[n] = took
    try:
        _save_json("timings.json", timings)
    except OSError:
        pass
    bad = [n for n, ok, _ in results if not ok]
    slow = sorted(results, key=lambda r: -r[2])
    if slowest:
        print(f"\ntimes (slowest first, {len(slow)} tests):")
        for n, ok, took in slow[:int(slowest)]:
            print(f"  {took:7.1f} s  {n}{'' if ok else '  FAIL'}")
    cpu = sum(t for _, _, t in results)
    print(f"\n{len(results) - len(bad)}/{len(results)} passed in {wall:.0f} s wall ({cpu:.0f} s of test time, "
          f"jobs {jobs}{', serial: ' + ', '.join(ser) if ser else ''}){'; ' + note if note else ''}; slowest: "
          + ", ".join(f"{n} {t} s" for n, _, t in slow[:5]))
    if bad:
        print("failed: " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BrokenPipeError:                                       # `run_all.py --list | head`
        sys.exit(0)
