"""FL to the front before its Open / export shortcuts, with Windows' foreground lock simulated (PC 2026-10-23: fl_open
failed twice with "Windows kept 'textinputhost.exe' in front and refused to hand FL the focus"; one real mouse click
on FL fixed it). The fake user32 only hands over the foreground the way the real one can be talked into it; each
scenario checks the way that works is reached, in order, and that the last resort (a click on FL's own caption) never
clicks anything else and puts the mouse back. Run: python3 tests/test_focus.py (no Windows needed)."""
import ctypes
import os
import sys
import tempfile
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
TMP = tempfile.mkdtemp(prefix="flh_focus_")
os.environ["FLH_ROOTS"] = os.path.join(TMP, "root"); os.makedirs(os.environ["FLH_ROOTS"])
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
import fl_headless_mcp as m  # noqa: E402

FL, TIH, OTHER = 0x1001, 0x2002, 0x3003                           # FL's main window, TextInputHost, a plugin window
THREAD = {FL: 11, TIH: 22, OTHER: 33}
ME = 99


class U32:
    """user32 with a foreground lock. `unlock` names the one way that works: 'alt' (an ALT tap before
    SetForegroundWindow), 'attach' (our input attached to the foreground's thread), 'switch' (SwitchToThisWindow),
    'restore' (minimise + restore), 'click' (a real click on FL), 'esc' (Esc closes the overlay), 'minimize_overlay'
    (the overlay minimised), None (nothing)."""

    def __init__(self, unlock, fg=TIH):
        self.unlock = unlock; self.fg = fg; self.log = []; self.attached = set(); self.alt = False
        self.cursor = (500, 400); self.iconic = set(); self.overlay_visible = True
        self.caption = {(m_x, 4) for m_x in range(0, 3000)}       # FL answers HTCAPTION on its top edge ...
        self.covered = set()                                      # ... points another window lies over

    def _take(self, how):
        if self.unlock == how:
            self.fg = FL

    # --- the calls _fl_foreground makes
    def GetForegroundWindow(self):
        return self.fg

    def IsIconic(self, h):
        return h in self.iconic

    def IsWindowVisible(self, h):
        return h != TIH or self.overlay_visible

    def ShowWindow(self, h, cmd):
        self.log.append(("ShowWindow", h, cmd))
        if h == TIH and cmd == 6:
            self.overlay_visible = False
            if self.unlock == "minimize_overlay":
                self.fg = FL
        if h == FL and cmd == 6:
            self.iconic.add(FL)
        if h == FL and cmd == 9:
            if FL in self.iconic and self.unlock == "restore" and ("ShowWindow", FL, 6) in self.log:
                self.fg = FL
            self.iconic.discard(FL)
        return 1

    def keybd_event(self, vk, scan, flags, extra):
        self.log.append(("key", vk, flags))
        if vk == 0x12 and flags == 2:
            self.alt = True
        return None

    def AllowSetForegroundWindow(self, pid):
        self.log.append(("AllowSetForegroundWindow", pid)); return 1

    def LockSetForegroundWindow(self, code):
        self.log.append(("LockSetForegroundWindow", code)); return 1

    def SetForegroundWindow(self, h):
        self.log.append(("SetForegroundWindow", h, self.alt, frozenset(self.attached)))
        if h == FL and self.unlock == "alt" and self.alt:
            self.fg = FL
        if h == FL and self.unlock == "attach" and THREAD.get(self.fg) in self.attached and THREAD[FL] in self.attached:
            self.fg = FL
        self.alt = False
        return int(self.fg == FL)

    def GetWindowThreadProcessId(self, h, _):
        return THREAD.get(h, 0)

    def AttachThreadInput(self, a, b, on):
        self.log.append(("AttachThreadInput", a, b, on))
        (self.attached.add if on else self.attached.discard)(b)
        return 1

    def BringWindowToTop(self, h):
        return 1

    def SetActiveWindow(self, h):
        return 1

    def SetFocus(self, h):
        return 1

    def SwitchToThisWindow(self, h, alt):
        self.log.append(("SwitchToThisWindow", h)); self._take("switch")

    def GetWindowRect(self, h, pr):
        r = ctypes.cast(pr, ctypes.POINTER(wintypes.RECT)).contents
        r.left, r.top, r.right, r.bottom = 100, 50, 1700, 950
        return 1

    def WindowFromPoint(self, pt):
        return OTHER if (pt.x, pt.y - 50) in self.covered else FL

    def GetAncestor(self, h, flag):
        return h

    def SendMessageW(self, h, msg, wp, lp):
        if msg == 0x0084:                                         # WM_NCHITTEST
            x, y = lp & 0xFFFF, (lp >> 16) & 0xFFFF
            return 2 if (x - 100, y - 50) in self.caption else 1  # HTCAPTION / HTCLIENT
        return 0

    def GetCursorPos(self, pp):
        p = ctypes.cast(pp, ctypes.POINTER(wintypes.POINT)).contents
        p.x, p.y = self.cursor
        return 1

    def SetCursorPos(self, x, y):
        self.cursor = (x, y); self.log.append(("SetCursorPos", x, y)); return 1

    def mouse_event(self, flags, *a):
        self.log.append(("mouse", flags, self.cursor))
        if flags == 0x0004 and self.unlock == "click" and self.cursor[1] - 50 == 4:
            self.fg = FL


class K32:
    def GetCurrentThreadId(self):
        return ME


U = {"u": None}
m._fg_api = lambda: (U["u"], K32())
m._fl_main_window = lambda: (FL, "Song - FL Studio 2026")
m._window_exe = lambda h: {TIH: "textinputhost.exe", OTHER: "plugin.exe", FL: "fl64.exe"}.get(h, "")
m._desktop_locked = lambda: False
_real_sleep = m.time.sleep
m.time.sleep = lambda s: _real_sleep(min(s, 0.002))
m.FG_STEP_WAIT_S = 0.02


def esc(h, vk, wait_s=1.5):
    U["u"].log.append(("esc", h))
    if U["u"].unlock == "esc":
        U["u"].fg = FL                                            # the overlay closed: FL is in front again
    return None


m._press_vk = esc


def run(unlock, fg=TIH):
    U["u"] = U32(unlock, fg)
    ok = m._fl_foreground()
    return ok, U["u"], dict(m._FG_WHY)


def first(log, kind):
    return next(i for i, e in enumerate(log) if e[0] == kind)


# already in front: nothing is done
ok, u, why = run(None, fg=FL)
assert ok and why["how"] == "already" and not u.log, u.log

# the overlay: Esc first; still there -> minimised (ignored) and FL takes the front
ok, u, why = run("esc")
assert ok and u.log[0] == ("esc", TIH), u.log
ok, u, why = run("minimize_overlay")
assert ok and ("ShowWindow", TIH, 6) in u.log and u.log.index(("ShowWindow", TIH, 6)) > u.log.index(("esc", TIH)), u.log

# ALT tap BEFORE SetForegroundWindow; Allow/unlock asked before it
ok, u, why = run("alt", fg=OTHER)
sf = next(e for e in u.log if e[0] == "SetForegroundWindow")
assert ok and why["how"] == "alt_tap" and sf[2] is True, u.log
assert first(u.log, "AllowSetForegroundWindow") < first(u.log, "SetForegroundWindow")
assert ("AllowSetForegroundWindow", -1) in u.log and ("LockSetForegroundWindow", 2) in u.log
assert not any(e[0] == "esc" for e in u.log), "Esc only for Windows' overlays, never a plugin window"

# AttachThreadInput to the foreground's AND FL's thread, then SetForegroundWindow; detached again afterwards
ok, u, why = run("attach")
assert ok and why["how"] == "attach_input", (why, u.log)
assert ("AttachThreadInput", ME, THREAD[TIH], True) in u.log and ("AttachThreadInput", ME, THREAD[FL], True) in u.log
assert ("AttachThreadInput", ME, THREAD[TIH], False) in u.log and ("AttachThreadInput", ME, THREAD[FL], False) in u.log
assert not u.attached, "every attach undone"

# SwitchToThisWindow, then minimise + restore
ok, u, why = run("switch")
assert ok and why["how"] == "switch_to_this_window", why
ok, u, why = run("restore")
assert ok and why["how"] == "minimize_restore", why

# the last resort: one click on FL's own caption, the mouse put back where it was
ok, u, why = run("click")
clicks = [e for e in u.log if e[0] == "mouse"]
assert ok and why["how"] == "title_click" and [c[1] for c in clicks] == [2, 4], u.log
assert all(c[2][1] - 50 == 4 for c in clicks) and u.cursor == (500, 400), (clicks, u.cursor)
assert first(u.log, "SwitchToThisWindow") < first(u.log, "mouse"), "the click comes last"
# ... never where FL doesn't answer 'caption', nor where another window covers FL
U["u"] = U32("click"); U["u"].caption = set()
assert m._fl_foreground() is False and not [e for e in U["u"].log if e[0] == "mouse"]
U["u"] = U32("click"); U["u"].covered = {(x, 4) for x in range(0, 3000)}
assert m._fl_foreground() is False and not [e for e in U["u"].log if e[0] == "mouse"]
# ... nor when Windows is locked
m._desktop_locked = lambda: True
U["u"] = U32("click")
assert m._fl_foreground() is False and not [e for e in U["u"].log if e[0] == "mouse"]
m._desktop_locked = lambda: False

# nothing works: False, and the message names who kept the front
ok, u, why = run(None)
assert not ok and why["owner"] == "textinputhost.exe" and "textinputhost.exe" in m._fg_note(), why
print("ok")
