"""FL user data folder detection: evidence layers (env > flh_setup > FL's registry setting > where FL runs the
script > FL's own activity), a stale OneDrive copy next to the real one, fl-headless's own files never counting as
activity, flh_setup check/apply. Run: python3 tests/test_core_userdata.py (no FL needed)."""
import os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
for k in ("FLH_FL_USERDATA", "OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
    os.environ.pop(k, None)
import fl_headless_mcp as m  # noqa: E402
import flh_setup  # noqa: E402

home = os.path.join(tmp, "Users", "someone")
od_docs = os.path.join(home, "OneDrive", "Documents"); loc_docs = os.path.join(home, "Documents")
OD = os.path.join(od_docs, "Image-Line", "FL Studio"); LOC = os.path.join(loc_docs, "Image-Line", "FL Studio")


def touch(path, age_s):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("x")
    t = time.time() - age_s
    os.utime(path, (t, t))


# the author's PC: a stale OneDrive copy that fl-headless itself wrote to recently (its script + mailbox), and the
# local folder FL really uses (FL's own settings files are newer there)
touch(os.path.join(OD, "Settings", "Hardware", "fl-headless", "device_flheadless.py"), 60)
touch(os.path.join(OD, "Settings", "fl-headless mailbox", "req.json"), 30)
touch(os.path.join(OD, "Settings", "Browser", "old.ini"), 90 * 86400)
touch(os.path.join(LOC, "Settings", "Hardware", "Flapi Request", "device_Flapi Request.py"), 40 * 86400)
touch(os.path.join(LOC, "Settings", "Browser", "history.ini"), 3600)
m._known_documents = lambda: [od_docs, loc_docs]                 # Windows' Documents points at OneDrive
m.os.path.expanduser = (lambda real: (lambda p: p.replace("~", home, 1) if p.startswith("~") else real(p)))(os.path.expanduser)
assert os.path.expanduser("~") == home


def pick():
    m._DOCS_CACHE.clear()
    return m._userdata_pick(refresh=True)


# (e) activity: FL's own newest file decides; fl-headless's own script/mailbox in the OneDrive copy don't count
p = pick()
assert p["layer"] == "activity" and os.path.normcase(p["path"]) == os.path.normcase(LOC), p
assert "history.ini" in p["why"] and "guess" in p["why"], p["why"]
assert m._fl_userdata() == p["path"] and m._documents_dirs()[0] == p["path"], m._documents_dirs()
assert {os.path.normcase(c["path"]) for c in p["candidates"]} >= {os.path.normcase(OD), os.path.normcase(LOC)}
assert sum(c["chosen"] for c in p["candidates"]) == 1
# the old heuristic would have counted Settings\Hardware files (our own install) and picked OneDrive
assert m._userdata_activity(OD) < m._userdata_activity(LOC)
# _hardware_dir follows the winner (even before its Hardware folder holds our script)
assert os.path.normcase(m._hardware_dir()) == os.path.normcase(os.path.join(LOC, "Settings", "Hardware"))
print("activity layer ok")

# (d) where FL runs the script: FL reported loading it from the OneDrive copy -> that wins over activity
m._state({"fl_script_file": {"path": os.path.join(OD, "Settings", "Hardware", "fl-headless", "device_flheadless.py"),
                             "t": int(time.time())}})
p = pick()
assert p["layer"] == "script_loaded" and os.path.normcase(p["path"]) == os.path.normcase(OD), p
m._state({"fl_script_file": None})
# heartbeat (FL versions with file I/O): fresh alive.json in one copy's mailbox
touch(os.path.join(LOC, "Settings", "fl-headless mailbox", "alive.json"), 2)
p = pick()
assert p["layer"] == "script_heartbeat" and os.path.normcase(p["path"]) == os.path.normcase(LOC), p
os.remove(os.path.join(LOC, "Settings", "fl-headless mailbox", "alive.json"))
print("script layer ok")

# (c) FL's own setting in the registry (names the Image-Line level; normalized to ...\FL Studio); a weak hit (just a
# path) never decides alone
m.IS_WINDOWS = True
reg = {"FL Studio 2026\\General": {"User data folder": [1, os.path.join(loc_docs, "Image-Line")],
                                   "Last browse": [1, os.path.join(od_docs, "Image-Line")]},
       "FL Studio 2026\\Devices": {}}
m._reg_dump = lambda sub="": {k: v for k, v in reg.items() if k.startswith(sub.strip("\\"))} if sub else reg
m._fl_key = lambda: "FL Studio 2026"
m._state({"fl_script_file": {"path": os.path.join(OD, "Settings", "Hardware", "fl-headless", "device_flheadless.py"),
                             "t": int(time.time())}})
p = pick()
assert p["layer"] == "fl_setting" and os.path.normcase(p["path"]) == os.path.normcase(LOC), p
assert "User data folder" in p["why"], p["why"]
hits = m._fl_userdata_registry_hits()
assert {h["strength"] for h in hits} == {"strong", "weak"}, hits
# two strong settings that disagree (current FL key wins over an older version's key)
reg["FL Studio 21\\General"] = {"UserDataFolder": [1, os.path.join(od_docs, "Image-Line")]}
p = pick()
assert p["layer"] == "fl_setting" and os.path.normcase(p["path"]) == os.path.normcase(LOC), p
# same rank, different folders: ambiguous -> next layer (the script FL loaded: OneDrive)
reg["FL Studio 2026\\Paths"] = {"User data": [1, os.path.join(od_docs, "Image-Line")]}
p = pick()
assert p["layer"] == "script_loaded", p
del reg["FL Studio 2026\\Paths"]; del reg["FL Studio 21\\General"]
m._state({"fl_script_file": None})
# MIDI settings: the script folder FL loads exists in one copy only
reg["FL Studio 2026\\Devices\\MIDI input\\Flapi Request"] = {"ScriptFolder": [1, "fl-headless"]}
reg["FL Studio 2026\\General"] = {}
p = pick()
assert p["layer"] == "script_assigned" and os.path.normcase(p["path"]) == os.path.normcase(OD), p
m.IS_WINDOWS = False
print("registry layer ok")

# (b) stored with flh_setup apply(user_data=...), given at the Image-Line level; (a) the env var wins over it
flh_setup.register(vars(m)) if not flh_setup.H else None
flh_setup.H = vars(m)
try:
    flh_setup._set_user_data(os.path.join(tmp, "nope")); raise SystemExit("missing folder accepted")
except ValueError:
    pass
try:
    flh_setup._set_user_data(home); raise SystemExit("a folder without Settings accepted")
except ValueError:
    pass
assert os.path.normcase(flh_setup._set_user_data(os.path.join(od_docs, "Image-Line"))) == os.path.normcase(OD)
p = pick()
assert p["layer"] == "setup" and os.path.normcase(p["path"]) == os.path.normcase(OD), p
rep = flh_setup._user_data_report()
assert rep["layer"] == "setup" and rep["exists"] and any(os.path.normcase(c["path"]) == os.path.normcase(LOC)
                                                         for c in rep["others"]), rep
a = flh_setup.flh_setup(action="apply", user_data=os.path.join(loc_docs, "Image-Line", "FL Studio"))
assert any("user data folder" in d for d in a["done"]), a
assert os.path.normcase(m._state()["fl_userdata"]) == os.path.normcase(LOC)
os.environ["FLH_FL_USERDATA"] = os.path.join(od_docs, "Image-Line")
p = pick()
assert p["layer"] == "env" and os.path.normcase(p["path"]) == os.path.normcase(OD), p
del os.environ["FLH_FL_USERDATA"]
m._state({"fl_userdata": None})
print("setup / env layers ok")

# only one exists: no guessing needed
import shutil  # noqa: E402
shutil.rmtree(os.path.join(od_docs, "Image-Line"))
p = pick()
assert p["layer"] == "only" and os.path.normcase(p["path"]) == os.path.normcase(LOC), p
# a sample path with %FLStudioUserData% resolves to whichever level holds the file
touch(os.path.join(LOC, "Audio", "kick.wav"), 10)
q = m._expand_sample("%FLStudioUserData%/Audio/kick.wav", os.path.join(tmp, "song.flp"))
assert os.path.isfile(q), q
touch(os.path.join(loc_docs, "Image-Line", "Packs", "snare.wav"), 10)
q = m._expand_sample("%FLStudioUserData%/Packs/snare.wav", os.path.join(tmp, "song.flp"))
assert os.path.isfile(q), q
print("ok")
