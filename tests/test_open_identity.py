"""Which file FL has open, proven by a save through the bridge (FL's title shows only a name): same-name projects,
FL's default 'Project 3' names, Project-info titles, never-saved projects. Never 'No' / never a write behind FL's
back without that proof. Run: python3 tests/test_open_identity.py"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="flh_ident_")
ROOT = os.path.join(TMP, "root"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = ROOT
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
import fl_headless_mcp as m  # noqa: E402

_real_sleep = m.time.sleep
m.time.sleep = lambda s: _real_sleep(min(s, 0.05))                # FL waits shortened (polls keep their timing)


def copy(name, sub=""):
    d = os.path.join(ROOT, sub); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name); shutil.copy(FIX, p); return p


class FL:
    open = None; held = None; title = None; dialogs = []; opened = []; answers = []; bridge = True; saves = 0
    save_as_on_save = False; trial_on_save = False


def load(p):
    FL.open = p; FL.held = open(p, "rb").read() if p else None


def bridge(cmd, timeout=15.0, **a):
    if not FL.bridge:
        raise RuntimeError("bridge not answering")
    if cmd == "transport" and a.get("action") == "save":
        FL.saves += 1
        if FL.trial_on_save:                                      # a demo plugin: FL shows its warning, saves nothing
            FL.dialogs = [{"hwnd": 77, "title": "Warning", "kind": "other", "class": "TMsgForm",
                           "text": ["This project uses a demo plugin"], "buttons": ["Save anyway", "Cancel"]}]
            return {"done": "save"}
        if FL.open and FL.held is not None:
            with open(FL.open, "wb") as f:
                f.write(FL.held)
        elif FL.save_as_on_save:
            FL.dialogs = [{"hwnd": 99, "title": "Save As", "kind": "save_as", "class": "#32770", "text": [],
                           "buttons": []}]
        return {"done": "save"}
    if cmd == "state":
        return {"playing": False}
    if cmd == "mixer_set":
        return {"name": "Insert 1"}
    return {}


def open_dialog(p, on_save="no", discard_ok=None):
    FL.answers.append(on_save); FL.opened.append(p); load(p); FL.title = os.path.splitext(os.path.basename(p))[0]
    return True


m.BRIDGE.call = bridge
m.fl_running = lambda: True
m._fl_pids = lambda: [1]
m._fl_windows = lambda: [(1, f"{FL.title} - FL Studio 2026")]
m._fl_main_window = lambda: (1, f"{FL.title} - FL Studio 2026")
m.fl_dialogs = lambda: list(FL.dialogs)
m._press_vk = lambda hwnd, vk, wait_s=1.5: FL.dialogs.clear() or "key"
m._open_running_or_fail = open_dialog
m._tag_track = lambda: (3, "Insert 3")
m._tag_gone = lambda t, w, **k: True
m._blank_project = lambda: None
m._fl_load_seen = lambda *a, **k: True
m._verify_in_background = lambda *a, **k: "verify-x"
m._stop_autoplay_later = lambda *a, **k: None
m._measure_load_later = lambda *a, **k: None


def set_tempo(p, bpm):
    return m.TOOLS["flp_set_tempo"][0](path=p, bpm=bpm)          # as a client calls it (guarded writer)


def tempo(p):
    return m.flp_info(p)["tempo_bpm"]


# ---- two projects named Beat: A opened through Claude earlier, B opened by hand in FL (unsaved work in FL)
A = copy("Beat.flp", "album"); B = copy("Beat.flp", "album/old takes")
m._state({"last_opened": A})
load(B); FL.title = "Beat"
m._GUESS_CACHE.clear()
assert m._open_project_guess()[0] is None and "2 project files" in m._GUESS_WHY["why"], m._GUESS_WHY
assert m._open_state(A) == "unknown" and m._open_state(B) == "unknown"
# writing A: FL's save lands in B -> A isn't open -> A is written directly, B (FL's copy) untouched
b_before = open(B, "rb").read()
r = set_tempo(A, 101)
assert tempo(A) == 101 and "fl_has_open" in r and "old takes" in r["fl_has_open"], r
assert open(B, "rb").read() == b_before and FL.held == b_before and FL.answers == [], FL.answers
# writing B: FL's save lands in B -> proven -> edited and reloaded in place
r = set_tempo(B, 102)
assert r.get("reloaded_in_fl") is True and tempo(B) == 102 and FL.opened == [B], (r, FL.opened)
assert FL.answers == ["no"]                                   # 'No' only after the save proved FL's work is on disk
# fl_open(A) while FL shows 'Beat' (= B): the identity save shows B, so A is opened with Yes (never No)
FL.answers.clear(); FL.opened.clear(); m._OPEN_PROOF.update(path=None)
r = m.fl_open(A)
assert FL.opened == [A] and FL.answers == ["yes"], (FL.opened, FL.answers, r)
# fl_open(A) again: FL now has A, proven by the save -> reloaded in place (No is safe)
FL.answers.clear(); FL.opened.clear()
r = m.fl_open(A)
assert FL.opened == [A] and FL.answers == ["no"] and "identity_save" in r["steps_s"], (FL.opened, FL.answers, r)
# no bridge: nothing can be proven -> writes refused with the real reason, nothing changed
FL.bridge = False; m._OPEN_PROOF.update(path=None); a0 = open(A, "rb").read()
try:
    set_tempo(B, 90); raise SystemExit("must refuse")
except RuntimeError as e:
    assert "2 project files" in str(e) and "nothing was changed" in str(e), e
assert open(B, "rb").read() != b"" and open(A, "rb").read() == a0
# fl_open of the open name without a bridge: never 'No'
FL.answers.clear()
m.fl_open(B)
assert FL.answers == ["yes"], FL.answers
FL.bridge = True
print("same-name ok")

# ---- FL's default name: 'Project 3.flp' saved by a beginner and opened through fl_open
P3 = copy("Project 3.flp", "songs")
m._OPEN_PROOF.update(path=None); load(P3); FL.title = "Project 3"; m._state({"last_opened": P3}); m._GUESS_CACHE.clear()
assert m._open_state(P3) == "unknown" and "default name" in m._GUESS_WHY["why"]
FL.answers.clear()
r = set_tempo(P3, 111)
assert r.get("reloaded_in_fl") is True and tempo(P3) == 111 and FL.answers == ["no"], (r, FL.answers)
# a never-saved 'Project 3' in FL (no file): the save opens Save As (closed again) -> the file is written directly
Q = copy("Project 3.flp", "other")
m._OPEN_PROOF.update(path=None); FL.open = None; FL.held = None; FL.save_as_on_save = True; m._GUESS_CACHE.clear()
r = set_tempo(Q, 112)
assert tempo(Q) == 112 and "never-saved" in r.get("fl_has_open", "") and not FL.dialogs, r
FL.save_as_on_save = False
print("default names ok")

# ---- a project whose Project-info title differs from its file name, opened by hand
T = copy("mysong_v3.flp", "titled")
m.flp_project_info(T, output=T, title="My Song")
m._state({"last_opened": None}); m._GUESS_CACHE.clear(); m._OPEN_PROOF.update(path=None)
load(T); FL.title = "My Song"
assert m._open_state(T) == "open", m._GUESS_WHY
FL.answers.clear()
r = set_tempo(T, 98)
assert r.get("reloaded_in_fl") is True and tempo(T) == 98, r
print("titles ok")

# ---- the identity save brings up FL's trial 'Save anyway' warning (a demo plugin): Cancel, refuse, name it
FL.trial_on_save = True; FL.answers.clear(); FL.opened.clear(); m._OPEN_PROOF.update(path=None)
t_before = open(T, "rb").read(); t0 = m.time.time()
try:
    set_tempo(T, 77); raise SystemExit("an edit must refuse while FL can't save")
except RuntimeError as e:
    assert "demo" in str(e) and "Save anyway" in str(e) and "changed nothing" in str(e), e
assert open(T, "rb").read() == t_before and not FL.dialogs and FL.opened == [], FL.dialogs   # Esc'd, untouched
assert m.time.time() - t0 < 4, "the warning is seen without waiting out the save deadline"
# fl_open of another project while FL shows this one: never Ctrl+O into the blocked FL
other = copy("Other.flp", "titled")
try:
    m.fl_open(T); raise SystemExit("fl_open must refuse while FL shows the trial warning")
except RuntimeError as e:
    assert "demo" in str(e), e
assert FL.opened == [] and FL.answers == [] and not FL.dialogs, (FL.opened, FL.answers)
m._GUESS_CACHE.clear()
try:                                                              # switching to another file: the pre-save sees it
    m.fl_open(other); raise SystemExit("fl_open(other) must refuse while FL shows the trial warning")
except RuntimeError as e:
    assert "demo" in str(e), e
assert FL.opened == [] and not FL.dialogs, FL.opened
FL.trial_on_save = False
print("ok")
