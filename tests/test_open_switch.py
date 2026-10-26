"""fl_open to a different project: saves the current one first (titles come back as a list), answers 'yes'; the
switch is checked by FL's title (PC 2026-10-23: the Open dialog took the path, FL stayed on the old project): an Open
FL ignored is sent once more, then a clear error. A slow load with no sign in the title is not 'ignored' (the wait
follows the load cost; a silent bridge counts as loading), and a retry leaves no 'save changes?' / Open dialog behind."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp
import fl_headless_mcp as m
a = os.path.join(tmp, "A.flp"); b = os.path.join(tmp, "B.flp")
for f in (a, b):
    open(f, "wb").write(b"x")
calls = []
TITLE = {"t": "A - FL Studio 2026", "ignore": 0}


def fake_open(p, on_save="no", discard_ok=None):
    calls.append(("open", p, on_save))
    if TITLE["ignore"]:                                           # FL ignores this Open (title stays)
        TITLE["ignore"] -= 1
    else:
        TITLE["t"] = os.path.splitext(os.path.basename(p))[0] + " - FL Studio 2026"
    return True


m.fl_running = lambda: True
m._open_project_guess = lambda: (a, ["A - FL Studio 2026", "Plugin window"])
m._fl_main_window = lambda: (1, TITLE["t"])
m._fl_windows = lambda: [(1, TITLE["t"])]
m._fl_pids = lambda: [1]
m.BRIDGE.call = lambda *x, **k: calls.append(("bridge", x, k)) or {}
m._open_running_or_fail = fake_open
m._state = lambda *x, **k: {}
m._measure_load_later = lambda *a, **k: None
m._stop_autoplay_later = lambda *a, **k: None
m.OPEN_SWITCH_IDLE_S = 0.3
_real_sleep = m.time.sleep
m.time.sleep = lambda s: _real_sleep(min(s, 0.02))
r = m.fl_open(b)
assert ("open", b, "yes") in calls, calls
assert any(c[0] == "bridge" and c[1] == ("transport",) for c in calls), calls
assert [c for c in calls if c[0] == "open"] == [("open", b, "yes")] and "first_open_ignored_s" not in r["steps_s"], r
# FL ignores the first Open: sent once more, and that one lands
calls.clear(); TITLE.update(t="A - FL Studio 2026", ignore=1)
r = m.fl_open(b)
assert [c for c in calls if c[0] == "open"] == [("open", b, "yes")] * 2 and "first_open_ignored_s" in r["steps_s"], r
assert m._OPEN_PENDING["project"] is None
# FL ignores both: a clear error, nothing claimed
calls.clear(); TITLE.update(t="A - FL Studio 2026", ignore=2)
try:
    m.fl_open(b); raise SystemExit("must fail")
except RuntimeError as e:
    assert "didn't switch" in str(e) and "A - FL Studio 2026" in str(e), e
assert len([c for c in calls if c[0] == "open"]) == 2 and m._OPEN_PENDING["project"] is None
# FL busy loading (its script's heartbeat stopped; the title doesn't move yet): not 'ignored' - no second Open into a
# loading FL; the result says it's still loading, and fl_status never names the project FL is leaving as open
calls.clear(); TITLE.update(t="A - FL Studio 2026", ignore=1)
m._fl_ui_busy = lambda: True
m._load_cost_guess = lambda p: (None, None)
m.OPEN_SWITCH_CAP_S = 0.5
r = m.fl_open(b)
assert [c for c in calls if c[0] == "open"] == [("open", b, "yes")] and r.get("loading"), r
assert m._OPEN_PENDING["project"] == os.path.abspath(b)
m._open_project_guess = lambda: (a, ["A - FL Studio 2026"])
m.fl_dialogs = lambda: []
st = m.fl_status()
assert "open_project" not in st and st["switching"]["to"] == os.path.abspath(b) and st["still_shown"] == a, st
TITLE["t"] = "B - FL Studio 2026"                                  # FL got there: the pending switch clears
m._open_project_guess = lambda: (b, ["B - FL Studio 2026"])
m._project_names = lambda p: {os.path.splitext(os.path.basename(p))[0].lower()}
st = m.fl_status()
assert st["open_project"]["path"] == b and "switching" not in st and m._OPEN_PENDING["project"] is None, st
print("switch ok")

# ---- PC 2026-10-23: 'first_open_ignored_s 9.8 / 20.4' on songs that load in 10-26 s - FL was still loading the first
# Open when the retry fired; afterwards FL showed a 'Confirm' save-changes box and, after No, an Open dialog.
# A simulated FL whose loads take a while with NO sign in the title (no 'Opening: ...').
import time  # noqa: E402
FL = {"cur": a, "target": None, "ready": 0.0, "busy_bridge": False, "dialogs": [], "late": None, "said": []}


def ftitle():
    if FL["target"] and time.time() >= FL["ready"]:
        FL["cur"], FL["target"] = FL["target"], None
        if FL["late"]:                                            # a Ctrl+O FL queued while busy pops up now
            FL["dialogs"].append(FL["late"]); FL["late"] = None
    return os.path.splitext(os.path.basename(FL["cur"]))[0] + " - FL Studio 2026"


def slow_open(load_s, retry_leaves_popups=False):
    def op(p, on_save="no", discard_ok=None):
        calls.append(("open", p, on_save))
        n = len([c for c in calls if c[0] == "open"])
        if FL["target"] and retry_leaves_popups:                  # FL busy: this Ctrl+O waits in its queue
            FL["late"] = {"hwnd": 70, "title": "Confirm", "class": "TMsgForm", "kind": "save_changes",
                          "text": ["Save changes to B?"], "buttons": []}
            FL["ready"] = min(FL["ready"], time.time() + 0.1)    # (the first load ends soon after)
            return True
        if n == 1 or not FL["target"]:
            FL["target"] = p; FL["ready"] = time.time() + load_s
        return True
    return op


def fbridge(cmd, *x, **k):
    calls.append(("bridge", (cmd,), k))
    if FL["busy_bridge"] and FL["target"] and cmd != "transport":
        raise RuntimeError("FL busy (loading)")
    return {}


def answer(h, how):
    FL["said"].append((h, how))
    FL["dialogs"] = [d for d in FL["dialogs"] if d["hwnd"] != h]
    if h == 70 and how == "no":                                   # 'No' -> FL goes on with the queued Ctrl+O: Open
        FL["dialogs"].append({"hwnd": 71, "title": "Open", "class": "#32770", "kind": "open_dialog", "text": [],
                              "buttons": [], "file_dialog": True})
    return True


m._fl_main_window = lambda: (1, ftitle())
m._fl_windows = lambda: [(1, ftitle())]
m._main_title = ftitle
m._open_project_guess = lambda: (FL["cur"], [ftitle()])
m._project_names = lambda p: {os.path.splitext(os.path.basename(p))[0].lower()}
m.BRIDGE.call = fbridge
m.fl_dialogs = lambda: list(FL["dialogs"])
m._answer_one = answer
m._press_vk = lambda h, vk, wait_s=1.5: answer(h, "esc") and "posted"
m._fl_ui_busy = lambda: False
m.OPEN_SWITCH_CAP_S = 30.0; m.OPEN_SWITCH_IDLE_S = 0.3


def reset(cur=a):
    calls.clear(); FL.update(cur=cur, target=None, ready=0.0, busy_bridge=False, dialogs=[], late=None, said=[])
    m._OPEN_PENDING.update(project=None, t=0.0)


# 1) a load that shows nothing for 1.2 s, on a song measured to load in ~2 s: not 'ignored' (the wait follows the load
#    cost), one Open only
reset(); m._open_running_or_fail = slow_open(1.2)
m._load_cost_guess = lambda p: (2.0, "measured")
r = m.fl_open(b, save_current=False)
opens = [c for c in calls if c[0] == "open"]
assert opens == [("open", b, "no")] and "first_open_ignored_s" not in r["steps_s"] and FL["cur"] == b, (opens, r)

# 2) no load cost known, but FL's bridge (it answered before the Open) goes silent while FL loads: still loading
reset(); m._open_running_or_fail = slow_open(1.2); FL["busy_bridge"] = True
m._load_cost_guess = lambda p: (None, None)
r = m.fl_open(b, save_current=False)
opens = [c for c in calls if c[0] == "open"]
assert opens == [("open", b, "no")] and "first_open_ignored_s" not in r["steps_s"] and FL["cur"] == b, (opens, r)

# 3) a retry that still happens (no sign at all past the wait) while FL is in fact loading: its Ctrl+O waits in FL's
#    queue and pops up after the load as 'Confirm' (+ Open after No). Nothing is left behind, and the result says so
reset(); m._open_running_or_fail = slow_open(5.0, retry_leaves_popups=True)
r = m.fl_open(b, save_current=False)
assert "first_open_ignored_s" in r["steps_s"] and FL["cur"] == b, r
assert FL["dialogs"] == [], ("popups left behind", FL["dialogs"])
assert FL["said"] == [(70, "no"), (71, "esc")], FL["said"]
assert [(d["kind"], d["did"]) for d in r.get("popups_closed", [])] == [("save_changes", "no"), ("open_dialog", "cancel")], r
# ... save_current=true: the stray 'save changes?' gets Cancel (nothing asked to save the song just opened; no Open
#     dialog follows), never Yes
reset(); m._open_running_or_fail = slow_open(5.0, retry_leaves_popups=True)
r = m.fl_open(b)
assert FL["dialogs"] == [] and FL["said"] == [(70, "esc")], FL["said"]
assert [(d["kind"], d["did"]) for d in r.get("popups_closed", [])] == [("save_changes", "cancel")], r
# ... popups that were up before fl_open are never touched
reset(); m._open_running_or_fail = slow_open(0.1)
FL["dialogs"] = [{"hwnd": 9, "title": "Open", "class": "#32770", "kind": "open_dialog", "text": [], "buttons": []}]
m._fl_blocking_popup = lambda: None
r = m.fl_open(b, save_current=False)
assert FL["said"] == [] and len(FL["dialogs"]) == 1 and "popups_closed" not in r, (FL, r)
print("ok")
