"""Bug hunt (setup / install / catalog / store / tidy): a brand-new user's PC and damaged state files.
  - fl_sound_catalog doesn't list BBC SO Discover's sections as owned when BBC isn't installed
  - install.py creates Claude Desktop's config when Claude's folder exists but the file doesn't yet
  - install.bat's Python probes print UTF-8 (a user name with accents survives `set /p` under chcp 65001)
  - flh_tidy keeps the backups of a project on an unplugged drive, and never recycles where Windows has no bin
  - a damaged / Notepad-saved (BOM) journal or profile never breaks tools and is never silently overwritten
Run: python3 tests/test_bughunt_setup.py (no FL needed)."""
import json, os, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp(prefix="flh_bh_setup_")
roots = os.path.join(tmp, "music"); os.makedirs(roots)
for k in [k for k in os.environ if k.startswith("FLH_")]:
    os.environ.pop(k)
os.environ.update(FLH_ROOTS=roots, LOCALAPPDATA=os.path.join(tmp, "local"), FLH_NO_WARM="1",
                  HOME=os.path.join(tmp, "home"), USERPROFILE=os.path.join(tmp, "home"))
os.environ["FLH_SPLICE_LIB"] = os.path.join(tmp, "none", "Splice")
os.environ["FLH_FL_USERDATA"] = os.path.join(tmp, "none", "Image-Line", "FL Studio")
os.environ["FLH_FLEX_DIR"] = os.path.join(tmp, "none", "FLEX")
os.environ["FLH_FACTORY_PACKS"] = os.path.join(tmp, "none", "Packs")


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_catalog as C, flh_tidy  # noqa: E402
ns = vars(m)
for mod in (flh_store, C, flh_tidy):
    mod.register(ns)
m.flp_sounds = lambda: []

# ---- 1. a fresh PC with nothing installed: no BBC sections offered as owned
s = C.fl_sound_catalog(refresh=True)
assert not s["by_source"].get("BBC") and s["sources"]["BBC"]["found"] is False, s["by_source"]
r = C.fl_sound_catalog(kind="strings")
assert not [x for x in r["results"] if x["source"] == "BBC"], r["results"]
# a saved BBC sound proves it's installed (even when FL's plugin database doesn't list it)
m.flp_sounds = lambda: [{"name": "BBC Violins 1", "plugin": "BBC Symphony Orchestra Discover", "path": "x.fst"}]
s = C.fl_sound_catalog(refresh=True)
assert s["by_source"].get("BBC") and s["sources"]["BBC"]["found"] is True, s
m.flp_sounds = lambda: []
# owned_plugins given as one string ("Fruity *") is a list of one pattern, never its characters ('*' = everything)
flh_store.profile_set("Strings", {"owned_plugins": "Fruity *", "trial_plugins": "Sytrus"})
assert C.ownership("Harmor")["status"] == "trial" and C.ownership("Fruity Limiter")["status"] is True
assert C.ownership("Sytrus")["status"] == "trial"
m._state({"collection": None})
print("catalog: BBC only when installed, string plugin lists ok")

# ---- 2. install.py: Claude Desktop started once (its folder exists), no config file yet -> the config is created
roam = os.path.join(tmp, "roaming"); os.makedirs(os.path.join(roam, "Claude"))
env = {k: v for k, v in os.environ.items() if not k.startswith("FLH_")}
env.update(APPDATA=roam, LOCALAPPDATA=os.path.join(tmp, "local2"), HOME=os.path.join(tmp, "home2"),
           USERPROFILE=os.path.join(tmp, "home2"), FLH_NO_WARM="1")
os.makedirs(os.path.join(tmp, "home2"))
pr = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), roots], env=env, capture_output=True,
                    text=True, timeout=300, stdin=subprocess.DEVNULL)
cfgp = os.path.join(roam, "Claude", "claude_desktop_config.json")
assert pr.returncode == 0, pr.stdout[-2000:] + pr.stderr[-2000:]
assert os.path.isfile(cfgp), pr.stdout[-1500:]
with open(cfgp, encoding="utf-8") as f:
    e = json.load(f)["mcpServers"]["fl-headless-manual"]
assert e["env"]["FLH_ROOTS"] == roots and e["command"] == sys.executable, e
assert "created" in pr.stdout, pr.stdout[-800:]
# a config saved by Notepad with a UTF-8 BOM is read (and merged), not skipped as unreadable
with open(cfgp, "w", encoding="utf-8-sig") as f:
    json.dump({"mcpServers": {"other": {"command": "x"}}}, f)
pr = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), roots], env=env, capture_output=True,
                    text=True, timeout=300, stdin=subprocess.DEVNULL)
assert pr.returncode == 0 and "Skipping" not in pr.stdout, pr.stdout[-1500:]
with open(cfgp, encoding="utf-8") as f:
    c = json.load(f)
assert c["mcpServers"]["other"] == {"command": "x"} and "fl-headless-manual" in c["mcpServers"], c
print("install: config created in Claude's folder, BOM config merged ok")

# ---- 3. install.bat: the Python probes print UTF-8 (chcp 65001 reads the temp file as UTF-8)
with open(os.path.join(ROOT, "install.bat"), encoding="utf-8") as f:
    bat = f.read()
probes = [ln for ln in bat.splitlines() if "sys.executable" in ln and "-c" in ln]
assert probes and all("-X utf8" in ln for ln in probes), probes
print("install.bat: UTF-8 probes ok")

# ---- 4. flh_tidy: an unplugged drive's project keeps its backups; no Recycle Bin -> not removed on Windows
base = os.path.dirname(m.STATE); old = time.time() - 30 * 86400
d = os.path.join(base, "backups", "usb")
os.makedirs(d)
with open(os.path.join(d, "project.txt"), "w", encoding="utf-8") as f:
    f.write("Q:\\Songs\\Tune.flp")                       # a drive that isn't there now
for i in range(3):
    p = os.path.join(d, f"2026{i:04d}-x.flp"); touch(p, b"0" * 100); os.utime(p, (old, old))
r = flh_tidy.flh_tidy()
assert "backup" not in r["by_kind"], r
gone = os.path.join(base, "backups", "gone")
os.makedirs(gone)
with open(os.path.join(gone, "project.txt"), "w", encoding="utf-8") as f:
    f.write(os.path.join(roots, "deleted.flp"))           # the drive is there, the project isn't: removable
p = os.path.join(gone, "20260101-x.flp"); touch(p, b"0" * 100); os.utime(p, (old, old))
assert flh_tidy.flh_tidy()["by_kind"]["backup"]["items"] == 1
calls = []
ns.update(IS_WINDOWS=True, _has_recycle_bin=lambda path: (False, "removable drive"),
          _recycle_shell=lambda path: (calls.append(path), os.remove(path) if isinstance(path, str) else None,
                                       (0, False))[-1])
try:
    r = flh_tidy.flh_tidy(apply=True)
finally:
    ns["IS_WINDOWS"] = False
assert not calls and os.path.isfile(p) and r["removed"] == 0 and r.get("not_removed"), (calls, r)
print("tidy: offline drive kept, no bin -> not removed ok")

# ---- 5. a damaged journal / profile: tools keep working, the damaged file is kept aside, BOM files read fine
song = os.path.join(roots, "Tune.flp"); touch(song, b"x")
jp = flh_store.journal_path(song)
with open(jp, "w", encoding="utf-8") as f:
    f.write('{"likes": ["the pad", ')                       # cut off
j = flh_store.journal_get(song)
assert j["song"] == "Tune" and j.get("journal_damaged"), j
j = flh_store.journal_update(song, add={"likes": "the bass"})
assert j["likes"] == ["the bass"] and "journal_damaged" not in j, j
kept = [f for f in os.listdir(roots) if ".damaged-" in f]
assert kept, os.listdir(roots)
with open(jp, "w", encoding="utf-8-sig") as f:              # Notepad's UTF-8 with BOM
    json.dump({"likes": ["warm keys"]}, f)
assert flh_store.journal_get(song)["likes"] == ["warm keys"]
pp = flh_store._profile_path("Album")
os.makedirs(os.path.dirname(pp), exist_ok=True)
with open(pp, "w", encoding="utf-8-sig") as f:
    json.dump({"name": "Album", "style": "my house rules", "finish": {"lufs": -11.0}}, f)
assert flh_store.profile_get("Album")["style"] == "my house rules"
prof = flh_store.profile_set("Album", {"teaching": "beginner"})
assert prof["style"] == "my house rules" and prof["finish"]["lufs"] == -11.0, prof
with open(pp, "w", encoding="utf-8") as f:
    f.write("{broken")
r = flh_store.flh_profile("Album")
assert r["profile"] is None and "damaged" in r["note"], r                # said, not 'no profile yet'
flh_store.profile_set("Album", {"teaching": "off"})
assert [f for f in os.listdir(os.path.dirname(pp)) if f.startswith("Album.damaged-")], os.listdir(os.path.dirname(pp))
# a journal OneDrive / an antivirus holds for a moment (Windows: PermissionError on replace) is retried
real_replace, hits = os.replace, {"n": 0}


def busy_replace(a, b):
    if b == jp and hits["n"] < 3:
        hits["n"] += 1
        raise PermissionError(13, "in use by another process")
    return real_replace(a, b)


os.replace = busy_replace
try:
    j = flh_store.journal_update(song, add={"likes": "the drums"})
finally:
    os.replace = real_replace
assert hits["n"] == 3 and "the drums" in flh_store.journal_get(song)["likes"], hits
assert not [f for f in os.listdir(roots) if f.endswith(".tmp")], os.listdir(roots)
import flh_cleanup  # noqa: E402
flh_cleanup.register(ns)
song2 = os.path.join(roots, "Other.flp"); touch(song2, b"x")
jp2 = flh_store.journal_path(song2)
with open(jp2, "w", encoding="utf-8") as f:
    f.write('{"channels": {"3": {"role": "bass"}')            # cut off
before2 = open(jp2, "rb").read()
note = flh_cleanup._renumber_journal(song2, {1})
assert "damaged" in note and open(jp2, "rb").read() == before2, note   # left as it is, never overwritten
print("store: damaged / BOM journal and profile, busy file, cleanup leaves a damaged journal ok")

# ---- 6. a hand-made recipe file with an odd name (or a BOM) doesn't break every recipe tool
import flh_extras  # noqa: E402
flh_extras.register(ns)
rd = os.path.join(base, "recipes"); os.makedirs(rd, exist_ok=True)
with open(os.path.join(rd, "Mix Pass!.json"), "w", encoding="utf-8") as f:
    json.dump({"description": "mine", "steps": [{"tool": "fl_status", "args": {}}]}, f)
with open(os.path.join(rd, "bom_one.json"), "w", encoding="utf-8-sig") as f:
    json.dump({"description": "notepad", "steps": [{"tool": "fl_status", "args": {}}]}, f)
allr = flh_extras._all_recipes()
assert "mix_pass" in allr and "bom_one" in allr, sorted(allr)
assert flh_extras.flh_recipe_list("mix pass"), "the odd-named recipe is found by its plain name"
print("recipes: odd names / BOM ok")

# ---- 7. FLEX: a half-downloaded pack and an odd row in FLEX's own cache.json don't break the index
import flh_flex  # noqa: E402
flh_flex.register(ns)
fx = os.path.join(tmp, "FLEX2"); os.environ["FLH_FLEX_DIR"] = fx
touch(os.path.join(fx, "cache.json"), json.dumps([7, {"filename": "Good.flexpack", "genre": "Pop"}]).encode())
touch(os.path.join(fx, "Packs", "Half.flexpack"), b"\x00\x00\x00\x01\x00\x00\x00\x05\x00\x00\xff\xff")
idx = flh_flex.index(refresh=True)
assert idx == [], idx
print("flex: odd cache row / half pack ok")

# ---- 8. flh_setup's checklist doesn't wait for the first sound scan (start-up warm-up) to finish
import threading  # noqa: E402
import flh_setup  # noqa: E402
flh_setup.register(ns)
C._BUILDS["catalog"] = {"name": "catalog", "label": "sound index", "t0": time.time(), "pct": 40.0, "partial": None,
                        "done": threading.Event(), "result": None, "error": None}
real_cat = C._catalog
C._catalog = lambda refresh=False: (_ for _ in ()).throw(AssertionError("waited for the running scan"))
try:
    x = flh_setup._extras()
    assert x.get("indexing") and "scanning" in flh_setup._extras_say(x), x
    rep = flh_setup.check()
    assert "scanning" in rep["steps"][5]["say"], rep["steps"][5]
finally:
    C._catalog = real_cat; C._BUILDS.pop("catalog", None)
print("setup: no wait for the first scan ok")

# ---- 8b. FL 20.x has no themes: setup installs nothing there and doesn't claim 'made fl-headless FL's theme'
ud20 = os.path.join(tmp, "home", "Documents", "Image-Line", "FL Studio")
os.makedirs(os.path.join(ud20, "Settings"))


class _NoMidi:
    def ports(self):
        return {"midi_out": [], "midi_in": []}

    def choose(self, p):
        return None


saved = {k: ns.get(k) for k in ("IS_WINDOWS", "_fl_file_version", "fl_exe", "_fl_userdata", "BRIDGE",
                                "_installed_scripts", "_reg_dump", "_reg_values", "_reg_subkeys", "fl_running")}
ns.update(IS_WINDOWS=True, _fl_file_version=lambda: "20.8.4.2576", fl_exe=lambda: "C:\\FL 20\\FL64.exe",
          _fl_userdata=lambda: ud20, BRIDGE=_NoMidi(), _installed_scripts=lambda: [], _reg_dump=lambda rel=None: {},
          _reg_values=lambda *a, **k: {}, _reg_subkeys=lambda *a, **k: [], fl_running=lambda: False)
sys.modules.setdefault("winreg", type(sys)("winreg"))
sys.modules["winreg"].KEY_WOW64_64KEY, sys.modules["winreg"].KEY_WOW64_32KEY = 1, 2
try:
    r = flh_setup.theme_setup(apply=True, auto=True)
    assert not r["done"] and "21" in r["note"], r
    assert not os.path.exists(os.path.join(ud20, "Settings", "Themes")), "nothing written for FL 20"
    st5 = flh_setup.check()["steps"][4]
    assert st5["status"] == "optional" and "21" in st5["say"], st5
finally:
    ns.update(saved)
print("setup: no theme on FL 20 ok")

# ---- 9. a bench cut off by a crash left its temporary collection active: the user's comes back at the next start
import flh_bench  # noqa: E402
flh_store.profile_set("Album", {"teaching": "off"})
bp = flh_store._profile_path(flh_bench.BENCH_PROFILE)
with open(bp, "w", encoding="utf-8") as f:
    json.dump({"name": flh_bench.BENCH_PROFILE}, f)
m._state({"collection": flh_bench.BENCH_PROFILE, "bench_run": {"collection_before": "Album", "sandbox": None}})
flh_bench.register(ns)
assert m._state().get("collection") == "Album" and not os.path.exists(bp), m._state().get("collection")
assert m._state().get("bench_run"), "the sandbox / FL part is still recovered by the next bench run"
print("bench: collection back after a crash ok")

# ---- 10. a pc_check cut off inside the lazy check: the user's reload mode comes back (server start / next run)
import flh_pccheck  # noqa: E402
m._state({"reload_mode": "lazy", "pc_check_run": {"sandbox": None, "started": "x", "reload_mode_before": "immediate",
                                                  "reload_mode_saved": True}})
flh_pccheck.register(ns)
st = m._state()
assert st.get("reload_mode") == "immediate" and st["pc_check_run"] == {"sandbox": None, "started": "x"}, st
m._state({"reload_mode": "lazy", "pc_check_run": {"sandbox": None, "reload_mode_before": None,
                                                  "reload_mode_saved": True}})
ns["fl_running"] = lambda: False
r = flh_pccheck._recover_previous()
assert m._state().get("reload_mode") is None and r.get("reload_mode_restored") == "default", r
m._state({"pc_check_run": None, "reload_mode": None})
print("pc_check: reload mode back after a crash ok")
print("ok")
