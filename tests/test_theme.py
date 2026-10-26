"""flh_setup's FL theme: user themes folder discovery (nested 'FL Studio\\FL Studio\\Settings\\Themes', OneDrive copies,
newest __current wins), install (idempotent, hash-checked), always made FL's theme once by setup (no opt-out) after
saving the user's theme as the visible preset 'My theme before fl-headless' + swatch thumbnail (written once, never
overwritten), FL running (files + preset now, the switch waits: one click or the next FL start), restore, the user's
later theme choice respected, the dev builds' hidden backup migrated. Run: python3 tests/test_theme.py (no FL needed)."""
import os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = os.path.join(tmp, "music"); os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
HOME = os.path.join(tmp, "home", "user")
os.environ["USERPROFILE"] = HOME; os.environ["HOME"] = HOME
for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial", "FLH_FL_USERDATA"):
    os.environ.pop(k, None)
os.makedirs(os.environ["FLH_ROOTS"], exist_ok=True)
import fl_headless_mcp as m  # noqa: E402
import flh_setup as S  # noqa: E402
ns = vars(m)
S.register(ns)
S._theme_supported = lambda: True
FL = {"running": False}
ns["fl_running"] = lambda: FL["running"]
REAL_DOCS = os.path.join(HOME, "Documents")
ONEDRIVE_DOCS = os.path.join(HOME, "OneDrive", "Documents")
REG = {"Personal": os.path.join("%USERPROFILE%", "OneDrive", "Documents")}     # OneDrive-redirected Documents
ns["_reg_values"] = lambda hive, path, wow=0: dict(REG) if path.endswith("User Shell Folders") else {}
ns["_known_documents"] = lambda: [ONEDRIVE_DOCS]
ns["_fl_userdata"] = lambda: os.path.join(ONEDRIVE_DOCS, "Image-Line", "FL Studio")    # the server's (stale) guess
SRC = {f: open(S._theme_src(f), "rb").read() for f in S.THEME_FILES}


def setup_tree():
    stale = os.path.join(ONEDRIVE_DOCS, "Image-Line", "FL Studio", "Settings", "Themes")
    real = os.path.join(REAL_DOCS, "Image-Line", "FL Studio", "FL Studio", "Settings", "Themes")
    for d in (stale, real):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(stale, "__current.flstheme"), "wb") as f:
        f.write(b"\xef\xbb\xbfHue=10\r\nLightness=0\r\n")
    os.utime(os.path.join(stale, "__current.flstheme"), (time.time() - 86400 * 90,) * 2)
    with open(os.path.join(real, "__current.flstheme"), "wb") as f:
        f.write(b"\xef\xbb\xbfHue=42\r\nLightness=-100\r\nBackPicFilename=\r\n")
    return stale, real


stale, real = setup_tree()

# ---- the theme files ship as FL writes them, with no install-specific paths
th = SRC[S.THEME_FILES[0]]
assert th.startswith(b"\xef\xbb\xbf") and b"\r\n" in th and b"\n" not in th.replace(b"\r\n", b""), "BOM + CRLF"
assert b"BackPicFilename=fl-headless background.jpg" in th and b"Program Files" not in th
assert SRC["thmfl-headless.png"][:8] == b"\x89PNG\r\n\x1a\n" and SRC["fl-headless background.jpg"][:2] == b"\xff\xd8"
assert os.path.isfile(os.path.join(os.path.dirname(S._theme_src("x")), "logo.png"))

# ---- discovery: the nested folder with the newest __current wins over the stale OneDrive copy the server picked
pick = S._theme_folder()
assert pick["path"] == real, pick
assert "__current.flstheme" in pick["why"] and stale in pick["others"], pick
st = S._theme_status()
assert st["installed"] is False and st["active"] is False and st["folder"] == real, st
# no theme files anywhere: the FL user data folder's Settings decides
alone = os.path.join(tmp, "alone"); os.makedirs(os.path.join(alone, "Image-Line", "FL Studio", "Settings"))
keep = (ns["_known_documents"], ns["_fl_userdata"], REG.copy())
ns["_known_documents"] = lambda: [alone]; ns["_fl_userdata"] = lambda: os.path.join(alone, "Image-Line", "FL Studio")
REG.clear(); os.environ["USERPROFILE"] = alone
p2 = S._theme_folder()
assert p2["path"] == os.path.join(alone, "Image-Line", "FL Studio", "Settings", "Themes") and "user data" in p2["why"], p2
ns["_known_documents"], ns["_fl_userdata"] = keep[0], keep[1]; REG.update(keep[2]); os.environ["USERPROFILE"] = HOME
assert S._theme_folder()["path"] == real

# ---- the write check: our names inside a discovered Settings\Themes only
for bad in (os.path.join(real, "evil.flstheme"), os.path.join(tmp, "Settings", "Themes", "fl-headless.flstheme"),
            os.path.join(os.path.dirname(real), "fl-headless.flstheme")):
    try:
        S._theme_target_ok(bad); raise SystemExit(f"write allowed: {bad}")
    except PermissionError:
        pass
assert S._theme_target_ok(os.path.join(real, "__current.flstheme"))

# ---- the write check: the visible backup preset and its thumbnail are allowed, the old hidden name no longer
for ok in ("My theme before fl-headless.flstheme", "thmMy theme before fl-headless.png",
           "My theme before fl-headless (2026-10-22 1530).flstheme", "thmMy theme before fl-headless (2026-10-22 1530).png"):
    assert S._theme_target_ok(os.path.join(real, ok)), ok
for bad in ("__before fl-headless.flstheme", "My theme before fl-headless.png", "thmMy theme.png"):
    try:
        S._theme_target_ok(os.path.join(real, bad)); raise SystemExit(f"write allowed: {bad}")
    except PermissionError:
        pass

# ---- the thumbnail: a valid PNG at FL's thumbnail size, drawn from the theme's own colors
import struct, zlib  # noqa: E401,E402
import numpy as np  # noqa: E402


def png_pixels(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, bits, ctype = struct.unpack(">IIBB", data[16:26])
    pos, idat = 8, b""
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]; t = data[pos + 4:pos + 8]
        assert zlib.crc32(data[pos + 4:pos + 8 + n]) & 0xFFFFFFFF == struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
        if t == b"IDAT":
            idat += data[pos + 8:pos + 8 + n]
        pos += 12 + n
    raw = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(h, 1 + w * 3)
    assert (raw[:, 0] == 0).all() and bits == 8 and ctype == 2
    return raw[:, 1:].reshape(h, w, 3)


px = png_pixels(S.theme_thumbnail(S._theme_src(S.THEME_FILES[0])))
assert px.shape == (180, 300, 3), px.shape
assert tuple(px[23, 150]) == (217, 119, 87), tuple(px[23, 150])        # Highlight=5732313 (COLORREF) = clay orange
assert tuple(px[175, 2]) == (21, 21, 21), tuple(px[175, 2])            # BackColor=-15395563 (ARGB) = #151515
assert S._theme_rgb({"x": "-3646906"}, "x", None) == (200, 90, 70) and S._theme_rgb({}, "x", (1, 2, 3)) == (1, 2, 3)
odd = os.path.join(tmp, "odd.flstheme")
with open(odd, "wb") as f:
    f.write(b"Hue=abc\r\nHighlight=\r\n")                              # junk values: defaults, still a picture
assert png_pixels(S.theme_thumbnail(odd)).shape == (180, 300, 3)

# ---- FL running: theme files + the visible backup preset now, __current untouched, the one click said
FL["running"] = True
before = open(os.path.join(real, "__current.flstheme"), "rb").read()
r = S.flh_setup(action="apply", fl_edition="producer")
cur = os.path.join(real, "__current.flstheme"); bak = os.path.join(real, S.THEME_BACKUP)
thumb = os.path.join(real, "thmMy theme before fl-headless.png")
assert open(cur, "rb").read() == before, "__current written while FL runs"
assert any("Options > Theme" in n and "one click" in n for n in r["notes"]), r["notes"]
assert open(bak, "rb").read() == before and png_pixels(open(thumb, "rb").read()).shape == (180, 300, 3)
assert any("My theme before fl-headless" in d for d in r["done"]), r["done"]
assert "My theme before fl-headless" in r["steps"][4]["say"] and r["steps"][4]["status"] == "needs you", r["steps"][4]
assert "Options > Theme" in r["steps"][4]["next"] and not r["steps"][4].get("auto"), r["steps"][4]
for f in S.THEME_FILES:
    assert open(os.path.join(real, f), "rb").read() == SRC[f], f
assert not any(os.path.exists(os.path.join(stale, f)) for f in S.THEME_FILES), "installed into the stale copy"
c = S.flh_setup("check")
assert c["theme"]["installed"] is True and c["theme"]["active"] is False and c["theme"]["pending"], c["theme"]
assert c["theme"]["backup_preset"] == "My theme before fl-headless", c["theme"]
assert any("Options > Theme" in s for s in c["next_steps"]), c["next_steps"]
assert "switch_back" in S.theme_setup(apply=True, auto=True) and open(cur, "rb").read() == before

# ---- install is idempotent; a changed installed file is updated (content hash)
r = S.theme_setup(apply=False)
assert "no change" in r["done"][0], r
with open(os.path.join(real, "thmfl-headless.png"), "wb") as f:
    f.write(b"old")
assert S._theme_status()["installed"] == "outdated"
r = S.theme_setup(apply=False)
assert "thmfl-headless.png" in r["done"][0] and "fl-headless.flstheme" not in r["done"][0], r
assert open(os.path.join(real, "thmfl-headless.png"), "rb").read() == SRC["thmfl-headless.png"]
S.flh_setup(action="theme", apply=True)                   # explicit: FL still running -> no switch
assert open(cur, "rb").read() == before and open(bak, "rb").read() == before
assert len([f for f in os.listdir(real) if f.startswith("My theme before")]) == 1, os.listdir(real)   # same theme: one

# ---- FL closed (FL wrote the same theme back): the waiting switch happens on the next setup
FL["running"] = False
os.utime(cur, None)
r = S.flh_setup(action="apply")
assert open(cur, "rb").read() == SRC["fl-headless.flstheme"], r
assert open(bak, "rb").read() == before, "backup is the user's old theme"
assert any("made fl-headless" in d for d in r["done"]), r["done"]
assert r["switch_back"] == "To switch back: FL > Options > Theme > pick 'My theme before fl-headless'.", r
st5 = r["steps"][4]
assert st5["status"] == "done" and "Options > Theme > pick 'My theme before fl-headless'" in st5["say"], st5
assert m._state()["theme"]["applied"] and not m._state()["theme"].get("pending")
c = S.flh_setup("check")["theme"]
assert c["active"] is True and c["backup"] is True and c["installed"] is True, c
assert S._theme_folder()["path"] == real                 # our own write doesn't count as FL's evidence (backup does)
r = S.flh_setup(action="apply")                           # again: nothing changes
assert open(bak, "rb").read() == before and any("is FL's theme" in d for d in r["done"]), r

# ---- the user picks another theme in FL: a later setup never switches it back
with open(cur, "wb") as f:
    f.write(b"\xef\xbb\xbfHue=200\r\nLightness=50\r\n")
users = open(cur, "rb").read()
r = S.flh_setup(action="apply")
assert open(cur, "rb").read() == users, "setup re-applied over the user's choice"
assert any("you picked another theme" in n for n in r["notes"]), r["notes"]
c = S.flh_setup("check")
assert c["theme"]["active"] is False and c["steps"][4]["status"] == "done" and "kept" in c["steps"][4]["say"], c["steps"][4]

# ---- explicit switch back: the older backup is never replaced (the user's newer theme gets its own preset)
r = S.flh_setup(action="theme")
assert open(cur, "rb").read() == SRC["fl-headless.flstheme"] and open(bak, "rb").read() == before
extra = [f for f in os.listdir(real) if f.startswith("My theme before fl-headless (") and f.endswith(".flstheme")]
assert len(extra) == 1 and open(os.path.join(real, extra[0]), "rb").read() == users, extra
assert os.path.isfile(os.path.join(real, "thm" + extra[0][:-9] + ".png")), os.listdir(real)

# ---- restore: FL running -> refused with the preset to pick; FL closed -> the backup comes back
FL["running"] = True
r = S.flh_setup(action="theme", restore=True)
assert not r["done"] and "Options > Theme" in r["note"] and extra[0][:-9] in r["note"], r
assert open(cur, "rb").read() == SRC["fl-headless.flstheme"]
FL["running"] = False
r = S.flh_setup(action="theme", restore=True)
assert open(cur, "rb").read() == users and r["done"], r          # the theme right before the latest switch
assert os.path.isfile(bak) and os.path.isfile(os.path.join(real, extra[0])), "backups are kept"
r = S.flh_setup(action="apply")                                 # restored: setup leaves it alone
assert open(cur, "rb").read() == users

# ---- no opt-out: theme=false still installs and switches (FL closed); the old 'declined' state is ignored
ns["_state"]({"theme": {"declined": True}})
for f in os.listdir(real):
    os.remove(os.path.join(real, f))
with open(cur, "wb") as f:
    f.write(before)
r = S.flh_setup(action="apply", theme=False)
assert open(cur, "rb").read() == SRC["fl-headless.flstheme"] and open(bak, "rb").read() == before, r
assert any("part of setup" in n for n in r["notes"]), r["notes"]
assert "declined" not in m._state()["theme"]

# ---- the user picks another theme while the switch waits (FL open): kept, never switched later
ns["_state"]({"theme": {}})
with open(cur, "wb") as f:
    f.write(before)
FL["running"] = True
r = S.flh_setup(action="apply")
assert m._state()["theme"]["pending"] and open(cur, "rb").read() == before
with open(cur, "wb") as f:
    f.write(b"\xef\xbb\xbfHue=300\r\nLightness=20\r\n")         # FL wrote the user's new pick
FL["running"] = False
r = S.flh_setup(action="apply")
assert b"Hue=300" in open(cur, "rb").read() and any("while the switch waited" in n for n in r["notes"]), r["notes"]
assert not m._state()["theme"].get("pending") and S.flh_setup(action="apply") and b"Hue=300" in open(cur, "rb").read()

# ---- the next FL start by fl-headless: the hook switches a waiting theme (FL closed), and never raises
ns["_state"]({"theme": {}})
with open(cur, "wb") as f:
    f.write(before)
ns["_BEFORE_FL_START"] = []
S.register(ns); S.register(ns)
assert ns["_BEFORE_FL_START"] == [S._theme_before_start]        # once, even when registered again
FL["running"] = True
r = S.flh_setup(action="apply")
assert any("next time fl-headless starts FL" in n for n in r["notes"]), r["notes"]
for h in ns["_BEFORE_FL_START"]:
    h()                                                         # FL still running: nothing happens
assert open(cur, "rb").read() == before
FL["running"] = False
for h in ns["_BEFORE_FL_START"]:
    h()
assert open(cur, "rb").read() == SRC["fl-headless.flstheme"] and m._state()["theme"]["applied"]
keep_run = ns["fl_running"]; ns["fl_running"] = lambda: 1 / 0
S._theme_before_start()                                         # errors are swallowed
ns["fl_running"] = keep_run
del ns["_BEFORE_FL_START"]

# ---- the dev builds' hidden backup becomes the visible preset (copied, the old file kept)
ns["_state"]({"theme": {"applied": "2026-10-20 10:00", "backup_file": "__before fl-headless.flstheme"}})
for f in os.listdir(real):
    os.remove(os.path.join(real, f))
with open(os.path.join(real, S.THEME_LEGACY), "wb") as f:
    f.write(before)
with open(cur, "wb") as f:
    f.write(SRC["fl-headless.flstheme"])
r = S.flh_setup(action="apply")
assert open(bak, "rb").read() == before and os.path.isfile(os.path.join(real, S.THEME_LEGACY)), os.listdir(real)
assert os.path.isfile(thumb) and m._state()["theme"]["backup_file"] == S.THEME_BACKUP
assert any("is now the preset" in d for d in r["done"]), r["done"]

# ---- FL's default theme (no __current yet): nothing to save, the way back is FL's default theme
ns["_state"]({"theme": {}})
for f in os.listdir(real):
    os.remove(os.path.join(real, f))
os.remove(os.path.join(stale, "__current.flstheme"))           # (else that old copy is the folder FL used last)
open(os.path.join(real, "Saved.flstheme"), "wb").write(b"Hue=1\r\n")   # FL's evidence that this folder is used
r = S.flh_setup(action="apply")
assert open(cur, "rb").read() == SRC["fl-headless.flstheme"] and not os.path.exists(bak), os.listdir(real)
assert "default theme" in r["steps"][4]["say"], r["steps"][4]
r = S.flh_setup(action="theme", restore=True)
assert not r["done"] and "default theme" in r["note"], r

# ---- not on Windows: nothing about themes
S._theme_supported = lambda: False
c = S.flh_setup("check")
assert "theme" not in c and "Windows" in S.theme_setup()["note"] and c["steps"][4]["status"] == "optional"

# ---- the tool: no theme=false opt-out any more; apply / restore stay
sch = m.TOOLS["flh_setup"][2]["properties"]
assert {"apply", "restore"} <= set(sch) and "theme" not in sch and "theme" in sch["action"]["description"]
assert "My theme before fl-headless" in m.TOOLS["flh_setup"][1] and "theme=false" not in m.TOOLS["flh_setup"][1]
print("theme ok")
