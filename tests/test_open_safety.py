"""fl_open never saves over a file that changed on disk behind FL, and save_current=false never saves (PC 2026-10-23):
1. fl_open(B, save_current=false) with FL showing B ran an 'identity save' that wrote FL's older copy over a file the
   user had just restored on disk (twice) - now: no save at all, the file is loaded;
   with save_current left at true: no save over the newer file either (FL clean -> reloaded; FL with unsaved changes
   -> refused with the way out); _prove_open / edits / automatic saves refuse the same way; fl_save (explicit) saves.
2. fl_open(C, save_current=false) stopped on FL's 'save changes?' for the project FL had ('answered Cancel') - now:
   answered No, as the caller asked.
3. fl_open(B, save_current=false) after a new B (note lengths only) was written over it 'reloaded through an empty
   project first' while FL kept its old notes; fl_finalize's save then wrote them over the file - now a marker the
   bridge must read back proves the reload, else an error and no save of FL lands in the file.
   Run: python3 tests/test_open_safety.py"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="flh_osafe_")
ROOT = os.path.join(TMP, "root"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = ROOT
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
FIX = os.path.join(HERE, "corpus", "base.flp")
import fl_headless_mcp as m  # noqa: E402
REAL_OPEN = m._open_running_or_fail
REAL_RELOAD = m._reload_in_fl

_real_sleep = m.time.sleep
m.time.sleep = lambda s: _real_sleep(min(s, 0.02))
m.OPEN_SWITCH_IDLE_S = 0.3


def copy(name):
    p = os.path.join(ROOT, name); shutil.copy(FIX, p); return p


def read(p):
    with open(p, "rb") as f:
        return f.read()


class FL:
    open = None; held = None; changed = 1; saves = 0; opened = []; answers = []; discard = []; reloads = []


def load(p):
    FL.open = p; FL.held = read(p); FL.changed = 0


def bridge(cmd, timeout=15.0, **a):
    if cmd == "transport" and a.get("action") == "save":
        FL.saves += 1
        with open(FL.open, "wb") as f:
            f.write(FL.held)
        FL.changed = 0
        return {"done": "save"}
    if cmd == "state":
        return {"playing": False, "changed": FL.changed}
    if cmd == "mixer_set":
        return {"name": "Insert 1"}
    return {}


def title():
    return f"{os.path.splitext(os.path.basename(FL.open))[0]} - FL Studio 2026"


def open_dialog(p, on_save="no", discard_ok=None):
    FL.answers.append(on_save); FL.discard.append(discard_ok); FL.opened.append(p)
    if on_save == "yes":                                          # 'Yes' to 'save changes?' writes FL's copy
        with open(FL.open, "wb") as f:
            f.write(FL.held)
    load(p)
    return True


def reload_(p, discard=False, force_blank=False):
    FL.reloads.append((p, discard)); load(p); m._note_fl_holds(p)
    return {"open_dialog": 0.1, "load": 0.2}


m.BRIDGE.call = bridge
m.fl_running = lambda: True
m._fl_pids = lambda: [1]
m._fl_windows = lambda: [(1, title())]
m._fl_main_window = lambda: (1, title())
m.fl_dialogs = lambda: []
m._open_running_or_fail = open_dialog
m._reload_in_fl = reload_
m._stop_autoplay_later = lambda *a, **k: None
m._measure_load_later = lambda *a, **k: None
m._verify_in_background = lambda *a, **k: "verify-x"


def reset():
    FL.saves = 0; FL.opened = []; FL.answers = []; FL.discard = []; FL.reloads = []; m._GUESS_CACHE.clear()


B = copy("Song B.flp"); C = copy("Song C.flp")
fixed = os.path.join(ROOT, "restore", "corrected.flp"); os.makedirs(os.path.dirname(fixed)); shutil.copy(FIX, fixed)
m.TOOLS["flp_set_tempo"][0](path=fixed, bpm=133)                 # (not open in FL)
FIXED = read(fixed)

# ---- FL opened B (fl-headless saw it load), the user changed it in FL (unsaved), then copied a corrected file over B
load(B); m._note_fl_holds(B); m._state({"last_opened": B})
FL.held = FL.held + b""                                           # FL's copy: the old bytes
FL.changed = 1
shutil.copy(fixed, B)
assert m._fl_copy_older(B), "the restore is seen: B isn't what FL last loaded"

# 1a) save_current=false: no save of any kind; the file is loaded as it is on disk
reset()
r = m.fl_open(B, save_current=False)
assert FL.saves == 0 and read(B) == FIXED, ("saved over the restored file", FL.saves)
assert FL.reloads == [(B, True)] and FL.held == FIXED and "save_current=false" in r["via"], (FL.reloads, r)
assert "identity_save" not in r["steps_s"], r

# 1b) save_current left at true, FL has unsaved changes, the file changed behind FL: refused, nothing written
shutil.copy(FIX, B); load(B); m._note_fl_holds(B); FL.changed = 1
shutil.copy(fixed, B); reset()
try:
    m.fl_open(B); raise SystemExit("must refuse: saving would overwrite the restored file")
except RuntimeError as e:
    assert "save_current=false" in str(e) and "unsaved changes" in str(e), e
assert FL.saves == 0 and read(B) == FIXED and not FL.reloads
# ... the identity proof itself never saves over it either
try:
    m._prove_open(B); raise SystemExit("_prove_open must refuse")
except RuntimeError as e:
    assert "isn't what FL last loaded" in str(e), e
assert FL.saves == 0 and read(B) == FIXED
# ... nor an automatic save (render cache, autosync); fl_save is the user's own request and saves
assert m._save_in_fl_and_wait(B, wait_s=1.0) is False and FL.saves == 0 and read(B) == FIXED
# ... nor an edit's save: refused, file untouched
try:
    m.TOOLS["flp_set_tempo"][0](path=B, bpm=90); raise SystemExit("an edit must refuse")
except RuntimeError as e:
    assert "nothing was changed" in str(e) and "save_current=false" in str(e), e
assert FL.saves == 0 and read(B) == FIXED

# 1c) FL has no unsaved changes: nothing to lose -> the file is loaded, no save
FL.changed = 0; reset()
r = m.fl_open(B)
assert FL.saves == 0 and read(B) == FIXED and FL.held == FIXED and FL.reloads == [(B, True)], (FL.saves, r)
assert "no unsaved changes" in r["via"] and "changed on disk" in r.get("note", ""), r
# ... and once FL holds the file unchanged: nothing to do at all (no save, no reload)
reset()
r = m.fl_open(B)
assert FL.saves == 0 and not FL.reloads and "already open" in r["via"], r

# 1d) an edit while the file changed behind a CLEAN FL: no save first, the file is edited and FL loads it
shutil.copy(FIX, B); FL.changed = 0; reset()
r = m.TOOLS["flp_set_tempo"][0](path=B, bpm=91)
assert FL.saves == 0 and m.flp_info(B)["tempo_bpm"] == 91 and r.get("reloaded_in_fl") is True, (FL.saves, r)
assert FL.held == read(B)

# 1e) the normal case still proves identity by a save (FL holds the file; nothing newer on disk)
FL.changed = 1; reset()
r = m.fl_open(B)
assert FL.saves == 1 and "identity_save" in r["steps_s"] and FL.reloads == [(B, False)], (FL.saves, r)

# 1f) fl_save is the user's own save: it saves (and FL's copy is the file's again)
shutil.copy(fixed, B); FL.changed = 1; reset()
assert m.fl_save()["saved"] == B and FL.saves == 1 and not m._fl_copy_older(B)

# 1g) switching away from a project whose file changed behind an FL with unsaved changes: 'Yes' would overwrite it
load(B); m._note_fl_holds(B); FL.changed = 1; shutil.copy(fixed, B); reset()
try:
    m.fl_open(C); raise SystemExit("must refuse")
except RuntimeError as e:
    assert "save_current=false" in str(e), e
assert FL.saves == 0 and read(B) == FIXED and not FL.opened
print("restored file never overwritten ok")

# ---- 2) fl_open(C, save_current=false) while FL shows another project: 'No' to 'save changes?', whatever FL shows
load(B); m._note_fl_holds(B); FL.changed = 1; reset()
b_before = read(B)
r = m.fl_open(C, save_current=False)
assert FL.answers == ["no"] and FL.discard == [m._DISCARD_ANY] and FL.saves == 0, (FL.answers, FL.discard)
assert read(B) == b_before and r.get("not_saved") == B, r
# FL's own dialogs: with _DISCARD_ANY the question about ANOTHER project is answered No (was: Cancel and stop)
DLG = {"stage": 0, "said": []}


def dialogs():
    if DLG["stage"] == 0:
        return [{"hwnd": 5, "title": "Confirm", "class": "TMsgForm", "kind": "save_changes", "text": ["Save changes?"],
                 "buttons": ["Yes", "No", "Cancel"]}]
    return [{"hwnd": 6, "title": "Open", "class": "#32770", "kind": "open_dialog", "text": [], "buttons": []}]


m.fl_dialogs = dialogs
m._answer_one = lambda h, a: DLG["said"].append(a) or DLG.update(stage=1) or True
m._press_vk = lambda h, vk, wait_s=1.5: DLG["said"].append("esc") or "key"
got = m._await_open_dialog(set(), C, timeout_s=2.0, on_save="no", discard_ok=m._DISCARD_ANY)
assert got and got["kind"] == "open_dialog" and DLG["said"] == ["no"], (got, DLG)
DLG.update(stage=0, said=[]); FL.open = B                        # FL shows B; without the caller's word: Cancel
got = m._await_open_dialog(set(), C, timeout_s=2.0, on_save="no", discard_ok=None)
assert got is None and DLG["said"] == ["esc"] and "Cancel" in m._OPEN_REFUSAL["why"], DLG

# ---- 2b) queued (lazy) edits of the song + fl_open(B, save_current=false): they go into the FILE, FL isn't saved
shutil.copy(FIX, B); load(B); m._note_fl_holds(B); FL.changed = 1; reset()
m._pending_set(B, [{"tool": "flp_set_tempo", "bpm": 97}])
r = m.fl_open(B, save_current=False)
assert FL.saves == 0 and m.flp_info(B)["tempo_bpm"] == 97 and FL.held == read(B) and not m._pending_get(B), (FL.saves, r)

# ---- 3) the record survives a restart of the server (FL stays open): a copy put over B is still seen
shutil.copy(FIX, B); load(B); m._note_fl_holds(B); FL.changed = 1
m._FL_HOLDS.clear(); m._HOLDS_DISK["loaded"] = False               # a fresh server process, same FL (pid 1)
shutil.copy(fixed, B)
assert m._fl_copy_older(B), "restarted server: the restore over B must still be seen"
assert m._save_in_fl_and_wait(B, wait_s=1.0) is False and read(B) == FIXED
m._FL_HOLDS.clear(); m._HOLDS_DISK["loaded"] = False
m._fl_pids = lambda: [2]                                           # another FL process: the old record means nothing
assert not m._fl_copy_older(B)
m._fl_pids = lambda: [1]

# ---- 4) fl_close(save=true) (the command-line render closes FL that way) never saves over a file put over the song
import ctypes  # noqa: E402
m._FH_WROTE.clear(); m._STALE_FL.clear()                          # only the copy put over B (no edit of ours)
shutil.copy(FIX, B); load(B); m._note_fl_holds(B); FL.changed = 0; shutil.copy(fixed, B); reset()
assert not m._stale_block(B)
RUN = {"on": True}


class _U32:
    def PostMessageW(self, *a):
        RUN["on"] = False; return 1


ctypes.windll = type("W", (), {"user32": _U32()})()
m.fl_running = lambda: RUN["on"]
r = m.fl_close(save=True, timeout_s=1)
assert FL.saves == 0 and read(B) == FIXED and "isn't what FL last loaded" in str(r["saved"]), (FL.saves, r)
m.fl_running = lambda: True

# ---- 5) FL's own export (render in FL, finals) of a song whose file changed behind FL: never FL's older copy
m._ping_ok = lambda: True
shutil.copy(FIX, B); load(B); m._note_fl_holds(B); FL.changed = 1; shutil.copy(fixed, B); reset()
try:
    m._fl_load_file(B); raise SystemExit("must refuse: FL's export would render FL's copy, not the file")
except m.InFlUnavailable as e:
    assert "isn't what FL last loaded" in str(e) and "nothing was rendered" in str(e), e
assert FL.saves == 0 and not FL.reloads and read(B) == FIXED
FL.changed = 0; reset()                                            # nothing unsaved in FL: the file is loaded first
r = m._fl_load_file(B)
assert r["how"] == "reloaded" and FL.reloads == [(B, True)] and FL.held == FIXED and FL.saves == 0, (r, FL.reloads)
reset()
assert m._fl_load_file(B)["how"] == "already" and not FL.reloads    # FL holds the file now: nothing to do

# ---- 6) PC 2026-10-23: FL busy loading swallowed a Ctrl+O (no Open dialog in time); the next Ctrl+O came on top and an
# Open dialog was left open in FL. Now the load is waited out first, the Open the first Ctrl+O brought late is
# cancelled, and only then Ctrl+O again
EV = []; BUSY = {"n": 4}; LATE = {"up": False}


def oirf(p, on_save="no", discard_ok=None):
    EV.append("ctrl+o")
    return len([e for e in EV if e == "ctrl+o"]) > 1               # the first: no dialog within the wait (FL busy)


def busy(bridge_up=False, **k):
    BUSY["n"] -= 1
    if BUSY["n"] < 0 and not LATE["up"] and "esc 44" not in EV:
        LATE["up"] = True                                         # the load is over: the queued Ctrl+O's Open shows
    return BUSY["n"] >= 0


def esc(action="list", button=None, hwnd=None):
    EV.append(f"esc {hwnd}"); LATE["up"] = False
    return {"sent": "escape"}


m._open_in_running_fl = oirf
m._fl_load_busy = busy
m.fl_dialogs = lambda: ([{"hwnd": 44, "title": "Open", "class": "#32770", "kind": "open_dialog", "text": [],
                          "buttons": [], "file_dialog": True}] if LATE["up"] else [])
m.fl_dialog = esc
assert REAL_OPEN(C, on_save="no") is True
assert EV == ["ctrl+o", "esc 44", "ctrl+o"] and BUSY["n"] < 0, EV
print("busy FL: no second Ctrl+O on top ok")

# ---- 7) PC 2026-10-23 (data loss): a new .flp (note lengths only) written over the open song, fl_open(<it>,
# save_current=false) 'reloaded ... through an empty project first, seen in FL's title' - FL still held its OLD notes,
# the hold record was moved to the new file anyway, and fl_finalize's 'saved FL first' wrote the old notes over the
# file. Now: a marker only the file has must be read back through the bridge; else an error, FL marked as holding an
# older copy, the record NOT moved -> no automatic save (finalize, render cache), no fl_save over the file
import struct  # noqa: E402
BLANK7 = copy("Empty Template.flp")
m._reload_in_fl = REAL_RELOAD
m._fl_load_busy = lambda bridge_up=False, **k: False
m.fl_dialogs = lambda: []
m._blank_project = lambda: BLANK7
m.MARKER_GRACE_S = 0.1; m.BLANK_LEAVE_S = 0.5; m.BLANK_LOAD_S = 0.5; m.RELOAD_SETTLE_S = 2.0
F7 = {"restore_old": False, "song": None}


def nchan(d):
    return sum(1 for e in m._events(d) if e["id"] == m.EV_CHANNEL)


def bridge7(cmd, timeout=15.0, **a):                              # FL with a real mixer: names come from what it holds
    if cmd == "mixer_set":
        t = int(a["track"]); ev = m._events(FL.held)
        if a.get("name") is not None:                             # a live rename: FL's copy changes (unsaved)
            FL.held = m._mixer_name_bytes(FL.held, ev, t, a["name"]); ev = m._events(FL.held); FL.changed = 1
        return {"name": m._mixer_names(FL.held, ev).get(t) or f"Insert {t}"}
    if cmd == "state":
        return {"playing": False, "changed": FL.changed, "channel_count": nchan(FL.held), "title": title()}
    if cmd == "transport" and a.get("action") == "save":
        FL.saves += 1
        with open(FL.open, "wb") as f:
            f.write(FL.held)
        t = os.stat(FL.open).st_mtime + 2; os.utime(FL.open, (t, t)); FL.changed = 0
        return {"done": "save"}
    return {}


def open7(p, on_save="no", discard_ok=None):
    FL.answers.append(on_save); FL.opened.append(os.path.basename(p))
    if os.path.abspath(p) == os.path.abspath(BLANK7):
        F7["song"] = (FL.open, FL.held)                           # FL lets go of the song (keeps a copy in memory)
    elif F7["restore_old"] and F7["song"] and os.path.abspath(p) == os.path.abspath(F7["song"][0]):
        FL.open = p; FL.held = F7["song"][1]; FL.changed = 0      # FL brings its OLD copy back (title as the file's)
        return True
    load(p)
    return True


def with_lengths(p, k=2):
    """p with every note's length times k (nothing the bridge can read changes)."""
    d = read(p); ev = m._events(d); edits = []
    for i, e in enumerate(ev):
        if e["id"] == m.EV_NOTES and e["len"] % 24 == 0:
            q = bytearray(m._payload(d, e))
            for j in range(0, len(q), 24):
                struct.pack_into("<I", q, j + 8, max(1, struct.unpack_from("<I", q, j + 8)[0] * k))
            edits.append((i, i + 1, m._data_event(m.EV_NOTES, bytes(q))))
    assert edits
    with open(p, "wb") as f:
        f.write(m._splice(d, ev, edits))


m.BRIDGE.call = bridge7; m._open_running_or_fail = open7
m._FH_WROTE.clear(); m._STALE_FL.clear()
shutil.copy(FIX, B); load(B); m._note_fl_holds(B); m._state({"last_opened": B}); OLD = read(B)
with_lengths(B); NEW = read(B)
assert NEW != OLD and m._fl_copy_older(B), "only note lengths differ: nothing FL's title / channel count tells"
# 7a) the template route 'works' by FL's title, but FL keeps its old notes: must FAIL and block every save
F7["restore_old"] = True; FL.changed = 0; reset()
try:
    m.fl_open(B, save_current=False); raise SystemExit("must fail: FL kept its old notes")
except RuntimeError as e:
    assert "didn't load" in str(e) and "marker" in str(e) and "OLDER copy" in str(e), e
assert FL.opened == ["Empty Template.flp", "Song B.flp"], FL.opened   # template first, then the song (by title fine)
assert FL.held == OLD and read(B) == NEW, "the file keeps the edit exactly (the marker taken out again)"
assert m._fl_held(B) == m._crc(OLD), "what FL holds is NOT moved to the new file without the marker's proof"
assert m._stale_block(B) and m._fl_copy_older(B)
FL.changed = 1                                                    # FL 'has unsaved changes' (as on the PC)
fp, why = m._rc_project_fp(B, live=True)                          # fl_finalize / fl_render: 'saved FL first' spot
assert fp is None and "older" in why and FL.saves == 0 and read(B) == NEW, (fp, why, FL.saves)
assert m._save_in_fl_and_wait(B, wait_s=0.5) is False and FL.saves == 0
s = m.fl_save()
assert s["saved"] is None and "older" in s["why"] and FL.saves == 0 and read(B) == NEW, s
try:
    m._fl_load_file(B); raise SystemExit("must refuse: FL's export would render the old notes")
except m.InFlUnavailable:
    pass
assert FL.saves == 0 and read(B) == NEW and FL.held == OLD
# 7b) the marker route: FL really loads the file -> proven, FL and the file put back as they were, saves allowed again
F7["restore_old"] = False; reset()
r = m.TOOLS["fl_reload_from_disk"][0](project=B)
assert "marker" in r["proof"] and FL.opened == ["Empty Template.flp", "Song B.flp"], (r, FL.opened)
assert FL.held == NEW and read(B) == NEW and FL.saves == 0 and not r.get("reload_unproven"), r
assert not m._stale_block(B) and m._fl_held(B) == m._crc(NEW) and not m._fl_copy_older(B)
assert m._save_in_fl_and_wait(B, wait_s=1.0) and read(B) == NEW   # FL's save now holds the edit
# 7c) fl_open(<the open song>, save_current=false) is the same route, proven the same way
with_lengths(B, 3); NEW3 = read(B); reset()
r = m.fl_open(B, save_current=False)
assert FL.held == NEW3 and read(B) == NEW3 and "marker" in r["proof"] and "marker" in r["note"], r
assert FL.opened == ["Empty Template.flp", "Song B.flp"] and not m._stale_block(B), FL.opened
shutil.rmtree(TMP, True)
print("ok")
