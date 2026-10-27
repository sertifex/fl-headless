"""flh_pccheck: fl_pc_check - the automatic check of fl-headless on a real Windows PC with FL Studio open.

One call runs every PC-VERIFY item that can be verified without a person (riskiest first), on SANDBOX copies only,
as a background job (fl_job shows 'check 7/16: in-FL render probe'), and writes a report (JSON + short markdown) into
fl-headless's state folder: per check status / detail / evidence / seconds / checklist item, a bug bundle
(flh_bug_report) when anything failed, and the next steps. Checks that need the person (a hand step in FL, a mic, a
MIDI keyboard) run last, so the person can walk away for the automatic part.

Safety: the song FL has open is recorded and saved by FL's own save first (an unsaved NEW project stops the run:
'save it first'), every check works on files in <first FLH_ROOTS folder>\\_fl-headless-check\\<time>\\, FL's popups
are answered with Escape/Cancel only (never 'Save anyway'), every check has a deadline, FL closing mid-run stops the
run with a report, fl_pc_check(stop=true) stops between steps, and at the end the user's song is reopened and the
sandbox goes to the Recycle Bin (keep_sandbox=true keeps it). A run cut off by a server restart is recovered by the
next fl_pc_check call (the song reopened, the old sandbox recycled). The user's song is never a sandbox file (FL on
an earlier run's test song at the start -> FL gets an empty project at the end: File > New, else FL's empty
template); the sandbox is recycled only once FL has left it (one retry; else said with its path and retried by the
next run); the run works under a temporary profile '_fl-headless check' (user-wide settings only, final_root inside
the sandbox), so no final, preview, stem or _fx file of a test song lands in the user's collection folders.

Contract with flh_pccheck_mod.py (module checks: music/audio/vocal/midi/handbook/catalog/newsong/mix/finalize/review):
checks(ns) -> [Check]; Check = {id, title, area, risk 1..5, needs, minutes, run(ctx)}; run returns {status
pass|fail|warn|skip, detail, evidence, fix_hint, checklist} or raises. The ctx API is the Ctx class below.

PC-VERIFY: this whole module is the verification harness; its own unknowns are the load-wait heuristics
(_wait_loaded) and the Recycle Bin lookup (files.recycle evidence)."""
import contextlib
import glob
import json
import os
import re
import shutil
import struct
import sys
import threading
import time
import traceback

H = None                                                          # the live server namespace (register)
REPORT_KEEP = 3                                                   # newest report folders kept in the state folder
SANDBOX_DIR = "_fl-headless-check"
USER_WAIT_S = 900                                                 # how long a check waits for the person's answer
MIN_DEADLINE_S = 90.0                                             # a check's deadline: max(this, 4 x its minutes)
MOD_NAME = "flh_pccheck_mod"                                      # the module checks' file (another module's owner)
STATUSES = ("pass", "fail", "warn", "skip")
AREAS_CORE = ("core", "render", "live_notes", "lazy", "bridge", "files")
PERSON = {"user", "mic", "midi_keyboard"}                         # needs that mean the person is at the PC
_RUN = {"job": None, "stop": threading.Event(), "ask": None, "answer": None, "answer_ev": threading.Event(),
        "running": False, "last": None}
_RUN_LOCK = threading.Lock()


class Stopped(Exception):
    """fl_pc_check(stop=true): the run ends after cleaning up."""


class FLGone(Exception):
    """FL closed (or crashed) during the run."""


class UserMustAct(Exception):
    """Before starting: something only the person can do (start FL, save the song, answer a popup)."""


class FLLoadFailed(Exception):
    """FL showed its 'error while reading the FLP' popup for a sandbox song: every later check needs FL to open test
    songs, and FL now holds a half-loaded 'untitled' project, so the run stops (PC run 2026-10-20: 22 checks failed
    one after another behind this)."""


LOAD_ERROR_WORDS = ("error occurred while reading", "may be corrupted", "caused an error while opening")


def _load_error_popup():
    """FL's 'An error occurred while reading the FLP file' box (class TMsgForm, title 'Error'; its text is drawn,
    not a child control, so the title is all Windows shows) -> the dialog, or None."""
    for d in _dialogs():
        txt = " ".join(d.get("text") or []).lower()
        if any(w in txt for w in LOAD_ERROR_WORDS) or (d.get("class") == "TMsgForm" and
                                                       str(d.get("title") or "").strip().lower() == "error"):
            return d
    return None


# ============================================================================= small helpers
def _T():
    return H["TOOLS"]


def _now():
    return time.strftime("%Y%m%d-%H%M%S")


def _state_dir():
    return os.path.dirname(H["STATE"])


def _key(p):
    return os.path.normcase(os.path.abspath(p))


def _inside(path, folder):
    a, b = _key(path), _key(folder).rstrip("\\/")
    return a == b or a.startswith(b + os.sep) or a.startswith(b + "/")


def _tempo(path):
    return float(H["flp_info"](path).get("tempo_bpm") or 0)


def _title():
    try:
        return H["_main_title"]() or ""
    except Exception:
        return ""


def _stem_of(path):
    return os.path.splitext(os.path.basename(path))[0]


def _dialogs():
    try:
        return list(H["fl_dialogs"]())
    except Exception:
        return []


def _jobs():
    return H["JOBS"]


def _set_progress(**fields):
    """Live progress for fl_job (works from any thread: the job id is kept in _RUN)."""
    jid = _RUN.get("job")
    if jid and jid in _jobs():
        cur = dict(_jobs()[jid].get("progress") or {})
        cur.update({k: v for k, v in fields.items()})
        for k in [k for k, v in cur.items() if v is None]:
            cur.pop(k)
        cur["at"] = round(time.time() - (_RUN.get("t0") or time.time()), 1)
        _jobs()[jid]["progress"] = cur


def _fl_alive(pids0):
    """FL still the same process as at the start (not closed, not crashed and restarted)."""
    try:
        if not H["fl_running"]():
            return False
        now = set(H["_fl_pids"]() or [])
        return not pids0 or not now or bool(now & set(pids0))
    except Exception:
        return False


def _save_read(path, wait_s=15.0):
    """FL saves the open project through the bridge; True once the file landed (then read it).
    PC 2026-10-20: a save sent while the edit's own background check was saving was dropped by FL (every check that
    saved right after a live edit failed): that check is asked to step aside (as a new edit does) and this save runs
    holding its lock; one more save is tried after a pause.
    Edits still queued for a merged reload go into FL first (PC 2026-10-23 files.slide_flag: FL's save was read
    while the slide-off edit waited for its reload, so the check saw FL's older copy)."""
    if H.get("_sync_before") and H.get("_pending_get") and H["_pending_get"](path):
        H["_sync_before"]("the check's save", path)
    lock, stop = H.get("_VERIFY_LOCK"), H.get("_VERIFY_STOP")
    got = False
    if lock is not None:
        if stop is not None:
            stop.set()
        try:
            got = lock.acquire(timeout=30.0)
        finally:
            if stop is not None:
                stop.clear()
    why = None
    try:
        sig0 = _sig(path)
        for attempt in range(2):
            if H["_save_in_fl_and_wait"](path, wait_s if attempt == 0 else 25.0):
                return True
            why = H["_save_refused_why"]() if H.get("_save_refused_why") else None
            time.sleep(1.5)
            if not why and sig0 is not None and _sig(path) != sig0 and _settled(path):
                return True                                       # the first save landed late (a big project)
    finally:
        if got:
            lock.release()
    if why:                         # the save guard said no: say why (PC 2026-10-24 lazy.big_project hid it)
        raise RuntimeError(f"fl-headless refused the save of {os.path.basename(path)}: {why}")
    raise RuntimeError(f"FL didn't save {os.path.basename(path)} within {wait_s:.0f} + 25 s (busy, or a popup)")


def _sig(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _settled(path, quiet_s=1.0, cap_s=30.0):
    """True once the file stops changing for quiet_s (a late save finished writing), False after cap_s."""
    t0 = time.time(); last = _sig(path); since = time.time()
    while time.time() - t0 < cap_s:
        time.sleep(0.25)
        now = _sig(path)
        if now != last:
            last, since = now, time.time()
        elif time.time() - since >= quiet_s:
            return now is not None
    return False


def _notes_of(path, ch):
    """[(key, flags)] of channel ch in every pattern of a project file."""
    out = []
    for pid, recs in H["_channel_notes"](path, ch).items():
        out += [(int(r[4]), int(r[1]), pid, int(r[0])) for r in recs]
    return sorted(out, key=lambda x: (x[3], x[0]))


def _channels(path):
    try:
        return H["flp_list"](path).get("channels") or []
    except Exception:
        return []


def _pick_channel(path, words=("808", "bass", "sub"), with_notes=False):
    chans = _channels(path)
    if not chans:
        return None
    if with_notes:
        info = H["flp_info"](path).get("notes_per_channel") or {}
        noted = [c for c in chans if str(c.get("index")) in info]
        chans = noted or chans
    for w in words:
        for c in chans:
            if w in str(c.get("name") or "").lower():
                return int(c["index"])
    return int(chans[0]["index"])


def _clone_channels(d, total):
    """A project with its channel blocks cloned round-robin until it has `total` channels (a 'big project' made of
    the project's own stock channels: FL loads it without missing plugins)."""
    ev = H["_events"](d)
    spans = [(i, a, b) for kind, i, a, b in H["_blocks"](d, ev) if kind == "channel"]
    if not spans:
        return d, 0
    idx = {i: (a, b) for i, a, b in spans}
    srcs = [i for i, a, b in spans if b > a and ev[b - 1]["id"] == H["EV_CHANNEL_END"]]
    if not srcs:
        return d, 0
    nxt = max(idx) + 1; add = b""; n = 0
    while len(idx) + n < total:
        s = srcs[n % len(srcs)]; k0, k_end = idx[s]
        add += bytes([H["EV_CHANNEL"]]) + struct.pack("<H", nxt) + d[ev[k0]["end"]:ev[k_end - 1]["end"]]
        nxt += 1; n += 1
    at = max(b for _, _, b in spans)
    new = bytearray(H["_splice"](d, ev, [(at, at, add)]))
    fmt, nch, ppq = struct.unpack("<hHH", new[8:14]); new[8:14] = struct.pack("<hHH", fmt, nch + n, ppq)
    return bytes(new), n


def _add_markers(d, marks):
    """Playlist markers [(bar, name, (num, den) or None)] into the current arrangement where FL reads them
    (H['_marker_slot']). A (num, den) marker is a time-signature marker (position bit 0x08000000 + events 33/34)."""
    ev = H["_events"](d); P = H["_ppq"](d)
    at = H["_marker_slot"](d, ev)
    if at is None:
        raise ValueError("the project has no playlist event to put markers after")
    num0, den0 = H["_project_meter"](d, ev)
    new = b"".join(H["_marker_event"](int(round((float(bar) - 1) * P * num0 * 4 / den0)), name, sig)
                   for bar, name, sig in marks)
    return H["_splice"](d, ev, [(at, at, new)])


def _poly(path, ch):
    """Channel ch's polyphony event 221 as FL wrote it: {max_voices, slide, mono, porta} or None."""
    d = H["_load"](path); ev = H["_events"](d)
    rng = H["_channel_ranges"](d, ev).get(int(ch))
    if not rng:
        return None
    k = next((k for k in range(*rng) if ev[k]["id"] == H["EV_POLYPHONY"] and ev[k]["len"] >= 9), None)
    if k is None:
        return None
    p = H["_payload"](d, ev[k]); mx, sl = struct.unpack_from("<II", p, 0)
    return {"max_voices": mx, "slide": sl, "mono": bool(p[8] & H["POLY_MONO"]), "porta": bool(p[8] & H["POLY_PORTA"])}


def _res(status, detail, evidence=None, fix_hint=None, checklist=None, **extra):
    r = {"status": status, "detail": detail, "evidence": evidence or {}}
    if fix_hint:
        r["fix_hint"] = fix_hint
    if checklist:
        r["checklist"] = checklist
    r.update(extra)
    return r


# ============================================================================= the context checks get
class Ctx:
    """What every check gets (see the module docstring / pccheck contract)."""

    def __init__(self, ns, sandbox, report_dir, unattended=False, original=None, pids=None):
        self.ns = ns
        self.sandbox = sandbox
        self.report_dir = report_dir
        self.unattended = bool(unattended)
        self.original = original
        self.pids = list(pids or [])
        self.opened = []
        self.made = []
        self.notes = []
        self._cleanups = []
        self._deadline = None
        self._expired = False
        self._snap_n = 0
        self.check = None                                         # the check running now (id)
        self.info = {}                                            # facts shared between checks (bridge_ok, fl...)

    # ---- control
    def _guard(self):
        if _RUN["stop"].is_set():
            raise Stopped("stopped by fl_pc_check(stop=true)")
        if self._expired or (self._deadline is not None and time.time() > self._deadline):
            self._expired = True
            raise TimeoutError(f"{self.check}: over its deadline")

    def say(self, text):
        self._guard()
        _set_progress(detail=str(text)[:160])

    @contextlib.contextmanager
    def deadline(self, seconds):
        """TimeoutError when the block takes longer than `seconds` (checked at every ctx.call / ctx.say and at the
        end; tools also get their own timeouts)."""
        prev = self._deadline
        mine = time.time() + float(seconds)
        p0 = getattr(self, "_person_s", 0.0)
        self._deadline = mine if prev is None else min(prev, mine)
        try:
            yield
            spent_waiting = getattr(self, "_person_s", 0.0) - p0  # the person's time doesn't count
            if time.time() - spent_waiting > mine + 0.5:
                raise TimeoutError(f"took longer than its {seconds:.0f} s deadline")
        finally:
            self._deadline = prev if prev is None else prev + (getattr(self, "_person_s", 0.0) - p0)

    def remaining(self, default=900.0):
        return max(5.0, (self._deadline - time.time()) if self._deadline else float(default))

    def cleanup(self, fn):
        self._cleanups.append(fn)

    def run_cleanups(self):
        errs = []
        while self._cleanups:
            fn = self._cleanups.pop()
            try:
                fn()
            except Exception as e:                                # cleanups never stop the others
                errs.append(f"{type(e).__name__}: {e}"[:200])
        return errs

    # ---- tools
    def call(self, tool, **args):
        """A tool through the dispatcher path (argument checks, the same wrappers as a client call); raises on error
        (recorded for flh_bug_report)."""
        self._guard()
        try:
            H["_CALL_BACKUPS"].clear()                            # one backup per call, as a client call gets
            return H["_flh_call"](tool, args)
        except Exception as e:
            try:
                H["_note_tool_error"](tool, e, traceback.format_exc())
            except Exception:
                pass
            raise

    def fl_restarted(self):
        """A check closed and restarted FL on purpose (e.g. the command-line render): take the new FL process as
        the one to watch, so the runner doesn't mistake it for a crash."""
        try:
            self.pids = list(self.ns["_fl_pids"]() or [])
        except Exception:
            pass
        return self.pids

    def snapshot(self, label="fl"):
        """A picture of FL's window saved into the report folder -> its path, or None (and why in ctx.notes)."""
        try:
            r = H["_flh_call"]("fl_snapshot", {})
            png = r.get("_image_png") if isinstance(r, dict) else None
            if not png:
                raise RuntimeError("no picture came back")
            return self.save_image(png, label)
        except Exception as e:
            self.notes.append(f"snapshot '{label}' unavailable: {type(e).__name__}: {str(e)[:120]}")
            return None

    def save_image(self, png_b64, label):
        import base64
        self._snap_n += 1
        safe = re.sub(r"[^\w.-]+", "_", str(label))[:40] or "snap"
        path = os.path.join(self.report_dir, f"{self._snap_n:02d}_{safe}.png")
        os.makedirs(self.report_dir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(png_b64))
        return path

    def ask_user(self, prompt, timeout_s=USER_WAIT_S):
        """For checks that need the person: shows `prompt` in fl_job's progress (needs_you) and waits for
        fl_pc_check(answer=...). None when unattended, on 'skip', or after timeout_s."""
        if self.unattended:
            return None
        self._guard()
        _RUN["answer"] = None; _RUN["answer_ev"].clear(); _RUN["ask"] = prompt
        _set_progress(needs_you=f"{prompt} -> then fl_pc_check(answer='done') (or answer='skip')")
        t0 = time.time()
        try:
            while not _RUN["answer_ev"].wait(0.5):
                if _RUN["stop"].is_set():
                    raise Stopped("stopped by fl_pc_check(stop=true)")
                if time.time() - t0 > timeout_s:
                    break
        finally:
            waited = time.time() - t0
            self._person_s = getattr(self, "_person_s", 0.0) + waited
            if self._deadline is not None:                        # the person's time doesn't count
                self._deadline += waited
            _RUN["ask"] = None
            _set_progress(needs_you=None)
        ans = _RUN["answer"]
        if ans is None or str(ans).strip().lower() in ("skip", "no", "cancel", "n"):
            return None
        return str(ans)

    # ---- projects
    def _unique(self, name):
        base = re.sub(r'[<>:"/\\|?*]', "", str(name)).strip() or "FLH Check"
        for n in range(1, 200):
            nm = base if n == 1 else f"{base} {n}"
            if not os.path.exists(os.path.join(self.sandbox, nm)):
                return nm
        raise RuntimeError("no free sandbox name")

    def make_project(self, name, kind="small"):
        """A fresh test .flp in the sandbox -> its path. small: a short starter beat from FL's empty template with
        FL's own sounds (flh_new_song); layers: the same, arranged (stacked layer patterns); slides: small with an
        808/bass slide note; big: small with its channels cloned to 150 (load cost); blank: FL's empty template."""
        self._guard()
        nm = self._unique(name)
        if kind == "blank":
            src = H["_blank_project"]()
            if not src:
                raise FileNotFoundError("FL's empty template wasn't found (set FLH_BLANK to an empty .flp)")
            dst = os.path.join(self.sandbox, nm, nm + ".flp")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            H["writable"](dst)
            shutil.copyfile(src, dst)
            self.made.append(dst)
            return dst
        if "flh_new_song" not in _T():
            raise RuntimeError("flh_new_song (flh_newsong module) isn't loaded, so no test song can be made")
        r = H["_flh_call"]("flh_new_song", {"genre": "lofi", "name": nm, "folder": self.sandbox, "open": False,
                                             "arrange": kind == "layers", "seed": 7})
        path = r.get("project") or r.get("output") or r.get("path")
        if not path or not os.path.isfile(path):
            raise RuntimeError(f"flh_new_song made no project: {str(r)[:200]}")
        if not _inside(path, self.sandbox):
            raise RuntimeError(f"the test song landed outside the sandbox: {path}")
        if kind == "slides":
            ch = _pick_channel(path)
            if ch is not None:
                H["_flh_call"]("flp_replace_part", {"project": path, "output": path, "channel": ch, "notes": [
                    [1, 1, "C2", 100], [1.5, 0.25, "G2", 100, {"slide": True}]]})
        elif kind == "big":
            d, n = _clone_channels(H["_load"](path), 150)
            if n:
                H["_write_bytes"](path, d)
        self.made.append(path)
        return path

    def copy_user_song(self):
        """A COPY of the song FL had open when the run started (renamed '<name> (check copy)'), or None."""
        if not self.original or not os.path.isfile(self.original):
            return None
        stem = _stem_of(self.original) + " (check copy)"
        nm = self._unique(stem)
        dst = os.path.join(self.sandbox, nm, nm + ".flp")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        H["writable"](dst)
        shutil.copyfile(self.original, dst)
        self.made.append(dst)
        return dst

    def open_in_fl(self, path, timeout_s=240):
        """fl_open + wait until FL holds it (title + bridge), then stop FL's autoplay. -> fl_open's result with
        load_s (seconds until FL answered with the project loaded)."""
        self._guard()
        if not _inside(path, self.sandbox) and not (self.original and _key(path) == _key(self.original)):
            raise RuntimeError("checks only open sandbox projects in FL")
        t0 = time.time()
        same_name = (H["_title_stem"](_title()) or "").lower() in _names(path)
        try:
            r = self.call("fl_open", project=path)
        except RuntimeError as e:                                 # FL still on the person's new project: theirs to save
            if not self.opened and not self.original and ("unsaved new project" in str(e) or "never-saved" in str(e)):
                raise UserMustAct("FL has a new project with unsaved changes: save it (File > Save as...) or discard "
                                  "it (File > New, answer No), then run fl_pc_check() again") from e
            raise
        self.opened.append(path)                                  # tried: FL no longer holds the person's song
        if same_name:                                             # FL's title won't change: let the load begin first
            time.sleep(1.5)
        load_s = _wait_loaded(path, min(timeout_s, self.remaining(timeout_s)), t0)
        try:
            H["BRIDGE"].call("transport", action="stop", timeout=3.0)
        except Exception:
            pass
        r = dict(r) if isinstance(r, dict) else {"result": r}
        r["load_s"] = load_s
        return r


def _names(path):
    try:
        return H["_project_names"](path)
    except Exception:
        return {_stem_of(path).lower()}


def _wait_loaded(path, timeout_s, t0=None):
    """Seconds until FL's main title names the project and the bridge answers twice in a row (FL's UI thread, and so
    the bridge, is busy while loading). PC-VERIFY: FL's title may show the name before plugins finish loading."""
    t0 = t0 or time.time()
    names = _names(path); ok_at = None
    while time.time() - t0 < timeout_s:
        if _RUN["stop"].is_set():
            raise Stopped("stopped by fl_pc_check(stop=true)")
        bad = _load_error_popup()
        if bad:
            try:
                H["fl_dialog"]("click", "enter", bad["hwnd"])        # an OK-only notice: nothing to decide
            except Exception:
                pass
            raise FLLoadFailed(f"FL couldn't read {os.path.basename(path)} ('An error occurred while reading the FLP "
                               "file'). fl-headless made that test song, so this is a fl-headless bug; the file is "
                               "kept in the sandbox for the bug report")
        ts = (H["_title_stem"](_title()) or "").lower()
        if ts and ts in names:
            try:
                H["BRIDGE"].call("ping", timeout=2.0)
                if ok_at is not None:
                    return round(ok_at - t0, 1)
                ok_at = time.time()
            except Exception:
                ok_at = None
        else:
            ok_at = None
        time.sleep(0.3)
    raise TimeoutError(f"FL didn't show {os.path.basename(path)} loaded within {timeout_s:.0f} s")


# ============================================================================= core checks
CL = "checklists/core.md"


def chk_userdata(ctx):
    r = ctx.call("flh_setup")
    fl = r.get("fl") or {}
    ud = fl.get("user_data") or {}
    path, layer = ud.get("path"), ud.get("layer")
    ctx.info["fl"] = {k: fl.get(k) for k in ("version", "edition", "exe") if fl.get(k)}
    where = None
    try:
        where = H["_note_script_location"]()
    except Exception:
        where = None
    ev = {"picked": path, "layer": layer, "why": ud.get("why"), "script_file": where,
          "others": [o.get("path") for o in (ud.get("others") or [])][:4]}
    ctx.info["userdata_ok"] = False
    if not path:
        return _res("fail", "no FL user data folder was found", ev, "flh_setup user_data pick returned nothing",
                    f"{CL}#0-prepare")
    if where:
        if _inside(where, path):
            ctx.info["userdata_ok"] = True
            return _res("pass", f"FL runs the bridge script from inside the folder fl-headless picked ({layer})", ev,
                        checklist=f"{CL}#0-prepare")
        m = re.split(r"[\\/]Settings[\\/]", where, maxsplit=1, flags=re.I)
        real = m[0] if len(m) == 2 else os.path.dirname(where)
        return _res("fail", f"fl-headless picked {path} ({layer}) but FL runs its script from {real}", ev,
                    "_userdata_pick chose a folder FL doesn't use (core.md 0.2): the layer that won is wrong here",
                    f"{CL}#0-prepare",
                    next_step=f"flh_setup(action='apply', user_data='{real}') then fl_pc_check(areas=['core']) again")
    ctx.info["userdata_ok"] = True
    if layer in ("env", "setup", "fl_setting", "only") or str(layer).startswith("script_"):
        return _res("pass", f"user data folder from '{layer}' (FL's script didn't say where it runs: the 'where' "
                            "answer is missing - core.md 0.4)", ev, checklist=f"{CL}#0-prepare")
    return _res("warn", f"the user data folder is a guess (layer '{layer}') and FL's script didn't report where it "
                        "runs from", ev, "no strong evidence layer matched (registry value / script location)",
                f"{CL}#0-prepare", next_step="send fl_settings(query='user data') output with the report")


def _file_script_version(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            m = re.search(r"^SCRIPT_VERSION = (\d+)", f.read(), re.M)
        return int(m.group(1)) if m else None
    except OSError:
        return None


def chk_bridge(ctx):
    ship = H["_script_version_shipped"]()
    installed = {p: _file_script_version(p) for p, _ in H["_installed_scripts"]()}
    try:
        r = ctx.call("fl_ping")
    except Exception as e:
        ctx.info["bridge_ok"] = False
        return _res("fail", f"FL's bridge doesn't answer: {str(e)[:160]}", {"installed": installed, "shipped": ship},
                    "fl_ping failed", f"{CL}#5-wave-2", next_step="fl_bridge_setup(action='status') and "
                    "flh_setup() show what's missing (loopMIDI / the script assigned in FL's MIDI settings)")
    have = r.get("version")
    ev = {"fl_runs": have, "shipped": ship, "installed": {os.path.basename(os.path.dirname(p)): v
                                                         for p, v in installed.items()}}
    ctx.info["bridge_version"] = have
    if r.get("script_outdated") or (ship and (have is None or int(have) < int(ship))):
        updated = None
        if ctx.info.get("userdata_ok") is False:
            ctx.info["bridge_ok"] = True
            return _res("fail", f"FL runs bridge script v{have or '1-2'} (this fl-headless ships v{ship}), and the user "
                                "data folder is wrong (core.userdata), so the new script wasn't installed", ev,
                        checklist=f"{CL}#5-wave-2",
                        stop_run="Fix the user data folder first (core.userdata's next step), then "
                                 "fl_bridge_setup(action='update_script'), restart FL and run fl_pc_check() again")
        if not installed or any((v or 0) < (ship or 0) for v in installed.values()):
            try:
                updated = ctx.call("fl_bridge_setup", action="update_script").get("updated")
            except Exception as e:
                updated = f"failed: {e}"
        ev["updated_now"] = updated
        ctx.info["bridge_ok"] = True
        return _res("fail", f"FL runs bridge script v{have or '1-2'}, this fl-headless ships v{ship}. The new script is "
                            "installed; FL loads it only when it starts", ev,
                    checklist=f"{CL}#5-wave-2",
                    stop_run="Close FL yourself (save your song), start it again, then run fl_pc_check() again: the "
                             "other checks need the new bridge script")
    ctx.info["bridge_ok"] = True
    return _res("pass", f"bridge script v{have} answers (current)", ev, checklist=f"{CL}#5-wave-2")


def chk_identity(ctx):
    a = ctx.make_project("FLH Check Beat", "small")
    stem = os.path.basename(a)
    twin = os.path.join(ctx.sandbox, "old takes", stem)
    os.makedirs(os.path.dirname(twin), exist_ok=True)
    shutil.copyfile(a, twin)
    ev = {}; bad = []
    ctx.say("open the sandbox song and prove it by a save")
    ctx.open_in_fl(a)
    proven = H["_prove_open"](a)
    ev["proven"] = bool(proven and _key(proven) == _key(a))
    if not ev["proven"]:
        bad.append(f"the save proof named {proven!r}, not the opened file")
    ctx.say("same-name twin: FL has 'old takes' open with an unsaved change")
    ctx.open_in_fl(twin)
    ctx.call("fl_set_tempo", bpm=133)                             # an unsaved change FL holds in memory
    r = ctx.call("flp_set_tempo", path=a, bpm=101)
    ev["fl_has_open"] = r.get("fl_has_open")
    _save_read(twin)
    ta, tt = _tempo(a), _tempo(twin)
    ev.update(tempo_written=ta, twin_tempo_after_fl_save=tt)
    if abs(ta - 101) > 0.01:
        bad.append(f"the file asked for has tempo {ta}, not 101")
    if abs(tt - 133) > 0.01:
        bad.append(f"FL's unsaved change in the twin was lost (twin tempo {tt}, FL had 133)")
    if _key(H["_open_project_guess"]()[0] or twin) != _key(twin) and (H["_title_stem"](_title()) or "") != _stem_of(twin):
        bad.append(f"FL no longer shows the twin: {_title()}")
    ctx.say("edit the open twin: reload in place")
    r2 = ctx.call("flp_set_tempo", path=twin, bpm=102)
    _save_read(twin)
    ev["twin_reloaded_in_fl"] = r2.get("reloaded_in_fl")
    if abs(_tempo(twin) - 102) > 0.01:
        bad.append(f"FL doesn't hold the edit of the open twin (tempo {_tempo(twin)}, wanted 102)")
    ctx.say("FL's default name ('Project N')")
    pn = os.path.join(ctx.sandbox, "Project 9137", "Project 9137.flp")
    os.makedirs(os.path.dirname(pn), exist_ok=True); shutil.copyfile(a, pn)
    ctx.open_in_fl(pn)
    r3 = ctx.call("flp_set_tempo", path=pn, bpm=99)
    _save_read(pn)
    ev["default_name_reloaded"] = r3.get("reloaded_in_fl")
    if abs(_tempo(pn) - 99) > 0.01:
        bad.append(f"editing an open 'Project N' file didn't reach FL (tempo {_tempo(pn)}, wanted 99)")
    try:                                                          # leaving a 'Project N' is part of this check
        ctx.open_in_fl(a)
    except Exception as e:
        bad.append(f"switching away from the open 'Project N' file failed: {str(e)[:160]}")
    if bad:
        return _res("fail", "; ".join(bad), ev, "open-project identity (proof by save, _with_open_project)",
                    f"{CL}#18-which-file-fl-has-open",
                    next_step="stop live editing of open projects until fixed; send the bug report")
    return _res("pass", "the open file is proven by a save; a same-name twin keeps FL's unsaved change; open twin and "
                        "'Project N' edits reload in FL", ev, checklist=f"{CL}#18-which-file-fl-has-open")


def chk_untitled(ctx):
    target = ctx.make_project("FLH Check Untitled", "small")
    ans = ctx.ask_user("In FL: File > New (an empty project), add one note anywhere, and DON'T save it")
    if not ans:
        return _res("skip", "needs a hand step in FL (File > New + one note, unsaved)", {},
                    checklist=f"{CL}#18-which-file-fl-has-open",
                    next_step="fl_pc_check(areas=['core.untitled_refusal']) when you're at the PC")
    t0 = _title(); mt0 = os.path.getmtime(target); ev = {"title_before": t0}
    refused, why = False, None
    try:
        ctx.call("fl_open", project=target)
    except Exception as e:
        refused, why = True, str(e)[:240]
    t1 = _title(); pops = [d.get("title") for d in _dialogs()]
    ev.update(refused=refused, why=why, title_after=t1, popups_left=pops,
              target_unchanged=os.path.getmtime(target) == mt0)
    ok = refused and H["_is_untitled"](t1) and not pops and ev["target_unchanged"]
    if not H["_is_untitled"](t0):
        ok = False; ev["note"] = "FL's title didn't show a new project when the check started"
    if ctx.ask_user("Now discard that test project: in FL press Ctrl+N (File > New) and answer 'No' (or 'Skip') to "
                    "'save changes?'") is not None:
        # the person just made FL's project a fresh, empty one for this check: FL 2026 still calls it 'changed'
        # (PC 2026-10-20), so the next sandbox open and the final restore may answer its save question with No/Skip
        H["_TRUST_FRESH"]["until"] = time.time() + 600
    if ok:
        return _res("pass", "fl_open refused while FL held an unsaved new project; FL kept it, no Save As left open",
                    ev, checklist=f"{CL}#18-which-file-fl-has-open")
    return _res("fail", "an unsaved new project in FL wasn't protected: " + (why or "fl_open went ahead"), ev,
                "fl_open H2 path (_open_in_running_fl with an untitled modified project)",
                f"{CL}#18-which-file-fl-has-open")


def _song_seconds(path):
    info = H["flp_info"](path)
    bars = float(info.get("song_length_bars") or info.get("last_note_end_bars") or 0)
    num, den = 4, 4
    try:
        num, den = (int(x) for x in str(info.get("time_signature") or "4/4").split("/"))
    except ValueError:
        pass
    bpm = float(info.get("tempo_bpm") or 120)
    return bars * num * 4 / den * 60.0 / bpm


def chk_render(ctx):
    p = ctx.make_project("FLH Check Render", "small")
    ctx.open_in_fl(p)
    ev = {}
    ctx.say("render probe: FL's Save dialog")
    r1 = ctx.call("fl_render_probe")
    ev["save_dialog"] = {"ok": r1.get("ok"), "why": r1.get("why"), "seen": (r1.get("seen") or {}).get("save_dialog"),
                         "cancelled": (r1.get("seen") or {}).get("cancelled")}
    left = [d.get("title") for d in _dialogs()]
    if not r1.get("ok") or left:
        return _res("fail", f"FL's export Save dialog probe failed: {r1.get('why') or 'a dialog stayed open'}",
                    dict(ev, popups_left=left), "_render_in_fl probe=save_dialog", f"{CL}#1-render-from-the-open-fl",
                    next_step="close any FL dialog by hand; renders keep using the command line")
    ctx.say("render probe: FL's export settings window")
    r2 = ctx.call("fl_render_probe", stage="export_dialog")
    img = r2.pop("_image_png", None) if isinstance(r2, dict) else None
    seen2 = (r2.get("seen") or {})
    ev["export_dialog"] = {"ok": r2.get("ok"), "why": r2.get("why"),
                           "title": (seen2.get("export_dialog") or {}).get("title"),
                           "class": (seen2.get("export_dialog") or {}).get("class"),
                           "cancelled": seen2.get("cancelled"), "file_written": seen2.get("file_written")}
    if img:
        ev["export_dialog"]["picture"] = ctx.save_image(img, "export_dialog")
    left = [d.get("title") for d in _dialogs()]
    if not r2.get("ok") or left or seen2.get("file_written"):
        return _res("fail", f"FL's export settings probe failed: {r2.get('why') or 'dialog left / file written'}",
                    dict(ev, popups_left=left), "_render_in_fl probe=export_dialog", f"{CL}#1-render-from-the-open-fl")
    ctx.say("a real render inside FL")
    pids = set(H["_fl_pids"]() or [])
    out = os.path.join(ctx.sandbox, "renders")
    r3 = ctx.call("fl_render", project=p, method="in_fl", output_dir=out, timeout_s=int(min(600, ctx.remaining(600))))
    wavs = [w for w in (r3.get("rendered") or []) if os.path.isfile(w)]
    ev["render"] = {"method": r3.get("method"), "steps_s": r3.get("steps_s"), "seconds": r3.get("seconds")}
    same_fl = H["fl_running"]() and (not pids or bool(set(H["_fl_pids"]() or []) & pids))
    if not wavs:
        return _res("fail", "fl_render(method='in_fl') wrote no file", ev, "_render_in_fl", f"{CL}#1-render-from-the-open-fl")
    a = H["audio_info"](wavs[0]); want = _song_seconds(p)
    ev["audio"] = {k: a.get(k) for k in ("seconds", "lufs", "peak_dbfs")}; ev["song_seconds"] = round(want, 1)
    bad = []
    if not same_fl:
        bad.append("FL closed or restarted during the in-FL render")
    if r3.get("method") != "in_fl":
        bad.append(f"rendered by '{r3.get('method')}', not in FL")
    if not (0.5 * want - 1 <= float(a.get("seconds") or 0) <= want + 30):
        bad.append(f"render length {a.get('seconds')} s vs the song's {want:.1f} s")
    if a.get("lufs") is None or float(a["lufs"]) < -50:
        bad.append(f"the render is (nearly) silent: {a.get('lufs')} LUFS")
    if float(a.get("peak_dbfs") or 0) > 0.5:
        bad.append(f"the render clips: peak {a.get('peak_dbfs')} dBFS")
    if bad:
        return _res("fail", "; ".join(bad), ev, "in-FL render", f"{CL}#1-render-from-the-open-fl")
    return _res("pass", f"probes clean, FL exported {a.get('seconds')} s at {a.get('lufs')} LUFS without closing", ev,
                checklist=f"{CL}#1-render-from-the-open-fl")


def chk_cli_refusal(ctx):
    p = ctx.make_project("FLH Check CLI", "small")
    pids = set(H["_fl_pids"]() or [])
    ev = {}
    for label, args in (("cli", {"method": "cli"}), ("allow_while_running", {"allow_while_running": True, "method": "cli"})):
        try:
            ctx.call("fl_render", project=p, output_dir=os.path.join(ctx.sandbox, "renders"), **args)
            ev[label] = "rendered (should have been refused)"
        except Exception as e:
            ev[label] = f"refused: {str(e)[:140]}"
    alive = H["fl_running"]() and (not pids or bool(set(H["_fl_pids"]() or []) & pids))
    ok = all(str(v).startswith("refused") for v in ev.values()) and alive
    if ok:
        return _res("pass", "with FL open the command-line render is refused and FL stays open", ev,
                    checklist=f"{CL}#6-review-fixes")
    return _res("fail", "the command-line render didn't refuse with FL open" if alive else "FL closed",
                ev, "fl_render cli path with FL running", f"{CL}#6-review-fixes")


def chk_live_notes(ctx):
    """The probe writes the user's live_notes setting: only a PASS (or a pass with an undo warning) may leave it
    changed; a skip (needs the hand step, unattended) or a failure puts back what the user had."""
    prev = (H["_state"]() or {}).get("live_notes")
    res = None
    try:
        res = _chk_live_notes(ctx)
        return res
    finally:
        if isinstance(res, dict) and res.get("status") in ("pass", "warn"):
            _RUN["live_notes_proved"] = (H["_state"]() or {}).get("live_notes")
        elif (H["_state"]() or {}).get("live_notes") != prev:
            H["_state"]({"live_notes": prev})
            if isinstance(res, dict):
                res.setdefault("evidence", {})["live_notes_setting"] = (
                    f"put back as it was ({'on' if (prev or {}).get('ok') else 'off'}): a check that didn't pass "
                    "never switches live notes off")


def _chk_live_notes(ctx):
    p = ctx.make_project("FLH Check Live", "small")
    ctx.open_in_fl(p)
    ctx.say("live notes probe")
    r = ctx.call("fl_live_notes_probe", project=p)
    first = r
    if not r.get("live_notes_enabled") and "Tools > Scripting" in str(r.get("next")):
        ans = ctx.ask_user("In FL's piano roll (any channel) run Tools > Scripting > fl_headless_notes once (it does "
                           "nothing by itself, it only becomes FL's 'last script')")
        if not ans:
            return _res("skip", "live notes need one hand step in FL first (run the fl_headless_notes script once)",
                        {"probe": r.get("steps")}, checklist=f"{CL}#2-live-notes-without-reload",
                        next_step="fl_pc_check(areas=['live_notes']) when you're at the PC")
        r = ctx.call("fl_live_notes_probe", project=p)
    ev = {"probe_steps": r.get("steps"), "first_probe_next": first.get("next") if first is not r else None}
    if not r.get("live_notes_enabled"):
        return _res("fail", f"live notes stay off: {r.get('next')}", ev, "fl_live_notes_probe",
                    f"{CL}#2-live-notes-without-reload")
    ch = int(r.get("channel", 0))
    before = H["_channel_notes"](p, ch)
    ctx.say("a live edit (no reload)")
    r2 = ctx.call("flp_replace_part", project=p, output=p, channel=ch, notes=[[1, 1, "C4", 90], [2, 1, "E4", 90]])
    ev["edit"] = {"live": r2.get("live"), "verified": r2.get("verified"), "live_note": r2.get("live_note"),
                  "steps_s": r2.get("steps_s")}
    _save_read(p)
    keys = sorted({k for k, *_ in _notes_of(p, ch)})
    ev["keys_in_fl_save"] = keys
    bad = []
    if not (r2.get("live") and r2.get("verified")):
        bad.append(f"the edit didn't go in live ({r2.get('live_note') or 'no live: true'})")
    if keys != [60, 64]:
        bad.append(f"FL's save holds keys {keys}, not [60, 64]")
    ctx.say("one undo step?")
    after_edit = H["_channel_notes"](p, ch)
    undo = "not tried"
    try:
        ctx.call("fl_transport", action="undo")
        _save_read(p)
        now = H["_channel_notes"](p, ch)
        undo = ("yes: FL's undo took the whole live write back" if now == before else
                "no change: FL's undo didn't touch the live write" if now == after_edit else
                "partly: FL's undo changed some notes")
    except Exception as e:
        undo = f"couldn't try: {str(e)[:100]}"
    ev["undo_one_step"] = undo
    if bad:
        return _res("fail", "; ".join(bad), ev, "_live_replace_part", f"{CL}#2-live-notes-without-reload")
    if not undo.startswith("yes"):
        return _res("warn", f"live notes work and are on; undo: {undo}", ev, "live write undo granularity",
                    f"{CL}#2-live-notes-without-reload")
    return _res("pass", "live notes work and are ON: an edit landed without a reload (checked by FL's save), and "
                        "Ctrl+Z takes it back in one step", ev, checklist=f"{CL}#2-live-notes-without-reload")


def chk_lazy(ctx):
    big = ctx.make_project("FLH Check Big", "big")
    prev = H["_state"]().get("reload_mode")
    run = H["_state"]().get("pc_check_run")
    if isinstance(run, dict):                                     # a crash mid-check: the next run / start puts it back
        H["_state"]({"pc_check_run": dict(run, reload_mode_before=prev, reload_mode_saved=True)})

    def restore():
        H["_state"]({"reload_mode": prev})
        cur = H["_state"]().get("pc_check_run")
        if isinstance(cur, dict) and cur.get("reload_mode_saved"):
            H["_state"]({"pc_check_run": {k: v for k, v in cur.items() if not k.startswith("reload_mode_")}})
    ctx.cleanup(restore)                                          # (also right after the check, below)
    ctx.say("open the big project (measuring its load)")
    o = ctx.open_in_fl(big, timeout_s=300)
    ev = {"channels": len(_channels(big)), "load_s": o.get("load_s")}
    try:
        H["_note_load_cost"](big, max(0.1, float(o.get("load_s") or 0.1)), "pc_check")
    except Exception:
        pass
    try:
        return _lazy_body(ctx, big, ev)
    finally:
        restore()
        try:
            H["flh_sync"](project=big, discard=True)              # nothing of this check stays queued
        except Exception:
            pass


LAZY_ICON = 12                                                   # a channel icon only exists in the file: no live route


def _route(r):
    """How a writer's edit went: queued (lazy), live (in FL, no reload) or direct (file edit + reload)."""
    r = r if isinstance(r, dict) else {}
    return "queued" if r.get("queued") else "live" if r.get("live") else "direct"


def _lazy_body(ctx, big, ev):
    ctx.call("flh_sync", mode="lazy")
    try:
        H["_prove_open"](big)                                     # the proof is a save: before the 'untouched' baseline
    except Exception:
        pass
    orig = open(big, "rb").read()
    ch = _pick_channel(big, words=(), with_notes=False) or 0
    other = next((int(c["index"]) for c in _channels(big) if int(c["index"]) != ch), ch)
    ctx.say("three edits: the first has no live route (a channel icon), so it queues and the others queue behind it")
    T = time.time()
    rs = [ctx.call("flp_edit", project=big, ops=[{"tool": "flp_style_channels", "channels": [
              {"channel": other, "name": "FLH Lazy", "icon": LAZY_ICON}]}]),
          ctx.call("flp_set_tempo", path=big, bpm=127),
          ctx.call("flp_replace_part", project=big, output=big, channel=ch, notes=[[1, 1, "C4", 90]])]
    ev["queue_s"] = round(time.time() - T, 2)
    routes = [_route(r) for r in rs]
    untouched = open(big, "rb").read() == orig
    ev.update(routes=dict(zip(("rename+icon", "tempo", "notes"), routes)), file_untouched_while_queued=untouched)
    ctx.say("a hand edit FL holds in memory only (mixer track name)")
    ctx.call("fl_mixer_set", track=2, name="FLH Hand Edit")
    T = time.time()
    s = ctx.call("flh_sync")
    ev["sync_s"] = round(time.time() - T, 1)
    ent = next(iter((s.get("synced") or {}).values()), {}) if isinstance(s.get("synced"), dict) else {}
    ev["sync"] = {k: ent.get(k) for k in ("ops", "reloaded_in_fl", "steps_s") if k in ent}
    _save_read(big)
    names = {int(c["index"]): c.get("name") for c in _channels(big)}
    mix = ctx.call("fl_mixer_info", tracks="2")
    mname = next((t.get("name") for t in (mix.get("tracks") or []) if int(t.get("index", -1)) == 2), None)
    ev.update(tempo=_tempo(big), renamed=names.get(other), keys=sorted({k for k, *_ in _notes_of(big, ch)}),
              hand_edit_kept=mname)
    return _lazy_verdict(ev, routes, untouched, ent, names.get(other) == "FLH Lazy", mname)


def _lazy_verdict(ev, routes, untouched, ent, renamed_ok, mname):
    """Every edit either queued or went in live (both fine: live needs no reload at all); the queued ones went into
    FL in ONE flh_sync; FL's save holds all three; the hand edit survived. A live edit makes FL save the file in a
    moment, so 'the file is untouched while queued' only counts when every edit queued."""
    nq = routes.count("queued")
    bad = []
    direct = [i + 1 for i, r in enumerate(routes) if r == "direct"]
    if direct:
        bad.append(f"edit(s) {direct} were neither queued nor done live (routes {routes})")
    if not nq:
        bad.append(f"no edit queued (routes {routes}): the queue + one sync path wasn't exercised")
    if nq == len(routes) and not untouched:
        bad.append("the file changed while edits were queued")
    if nq and (not ent or ent.get("ops") != nq):
        bad.append(f"flh_sync didn't apply the {nq} queued edit(s) at once: {ev.get('sync')}")
    if abs(float(ev.get("tempo") or 0) - 127) > 0.01 or not renamed_ok or 60 not in (ev.get("keys") or []):
        bad.append("FL's save after the sync misses an edit")
    if mname != "FLH Hand Edit":
        bad.append(f"the hand edit in FL was lost (mixer track 2 is '{mname}')")
    if bad:
        return _res("fail", "; ".join(bad), ev, "lazy reloads (_sync_pending)", f"{CL}#3-lazy-reloads",
                    next_step="use flh_sync(mode='immediate') until fixed")
    how = f"{nq} edit(s) queued instantly, one sync applied them" + (
        f", {routes.count('live')} went in live (no reload)" if "live" in routes else "")
    return _res("pass", f"{how}; FL holds all three and kept the hand edit (load {ev.get('load_s')} s for "
                        f"{ev.get('channels')} channels)", ev, checklist=f"{CL}#3-lazy-reloads")


def chk_slides(ctx):
    p = ctx.make_project("FLH Check Slides", "small")
    ch = _pick_channel(p)
    ctx.open_in_fl(p)
    r = ctx.call("flp_replace_part", project=p, output=p, channel=ch,
                 notes=[[1, 1, "C2", 100], [1.5, 0.25, "G2", 100, {"slide": True}]])
    _save_read(p)
    got = _notes_of(p, ch); S = H["NOTE_SLIDE"]
    ev = {"channel": ch, "live": r.get("live"), "notes": [{"key": k, "slide": bool(f & S)} for k, f, *_ in got]}
    ok = any(k == 43 and f & S for k, f, *_ in got) and any(k == 36 and not f & S for k, f, *_ in got)
    pid = got[0][2] if got else 0
    try:
        H["BRIDGE"].call("piano_roll", channel=ch, pattern=pid, show=True, timeout=5.0)
    except Exception:
        pass
    snap = ctx.snapshot("slide_notes")
    ev["picture"] = snap
    routes = []
    for pat in sorted({x[2] for x in got if x[1] & S}):
        e = ctx.call("flp_edit_notes", path=p, output=p, channel=ch, pattern=pat, ops=[{"op": "slide", "on": False}])
        routes.append("live" if e.get("live") else "queued" if e.get("queued") else
                      "reload" if e.get("reloaded_in_fl") else "file")
    _save_read(p)                                                 # (queued edits go into FL first)
    off = not any(f & S for _, f, *_ in _notes_of(p, ch))
    ev.update(slide_off_again=off, slide_off_route=routes)
    if not ok or not off:
        return _res("fail", "the slide flag (0x08) didn't survive FL's load+save" if not ok else
                    f"turning slides off didn't reach FL (route {routes})", ev, "NOTE_SLIDE flag 0x08 "
                    "(live: _pr_note sends slide False; file: flp_edit_notes slide op)", f"{CL}#5-wave-2")
    if snap:
        return _res("warn", f"slide flag 0x08 survives FL's load+save; look at {snap}: the G2 note should show FL's "
                            "slide triangle", ev, checklist=f"{CL}#5-wave-2", look=snap)
    return _res("pass", "slide flag 0x08 survives FL's load+save (no picture: confirm the slide triangle by eye, "
                        "core.md 5.9)", ev, checklist=f"{CL}#5-wave-2")


def chk_glide(ctx):
    p = ctx.make_project("FLH Check Glide", "small")
    ch = _pick_channel(p)
    if _poly(p, ch) is None:
        return _res("skip", f"channel {ch} of FL's template has no polyphony event 221", {"channel": ch})
    ctx.open_in_fl(p)
    r = ctx.call("flp_channel_glide", project=p, output=p, channel=ch, max_voices=1)
    _save_read(p)
    got = _poly(p, ch)
    want = r.get("after") or {}
    ev = {"channel": ch, "written": want, "after_fl_save": got}
    if got and got["mono"] and got["porta"] and got["max_voices"] == want.get("max_voices") and \
            got["slide"] == want.get("slide"):
        return _res("pass", "event 221 (max voices, slide, mono|porta flags) is kept as written by FL's load+save",
                    ev, checklist=f"{CL}#5-wave-2")
    return _res("fail", "FL changed event 221 after loading it: the layout guess is off", ev,
                "event 221 layout (max u32, slide u32, flags u8)", f"{CL}#5-wave-2")


def chk_markers(ctx):
    p = ctx.make_project("FLH Check Markers", "small")
    d = _add_markers(H["_load"](p), [(1, "FLH Intro", None), (3, "FLH Drop", None), (5, "FLH Waltz", (3, 4))])
    H["_write_bytes"](p, d)
    ctx.open_in_fl(p)
    _save_read(p)
    info = H["flp_info"](p)
    names = [m.get("name") for m in info.get("markers") or []]
    ts = info.get("time_signature_changes") or []
    snap = ctx.snapshot("markers")
    ev = {"markers": info.get("markers"), "time_signature_changes": ts, "picture": snap}
    try:                                                          # where FL put them when not on the playlist
        d2 = H["_load"](p)
        stray = H["_stray_markers"](d2, H["_events"](d2))
        if stray:
            ev["moved_to_patterns"] = [(s["name"], s["pattern"]) for s in stray][:8]
    except Exception:                                             # noqa: BLE001
        pass
    ok = all(n in names for n in ("FLH Intro", "FLH Drop")) and any(
        abs(float(t.get("bar", 0)) - 5) < 0.01 and t.get("time_signature") == "3/4" for t in ts)
    if ok:
        return _res("pass", "markers and a 3/4 time-signature marker survive FL's load+save at the bars written", ev,
                    checklist=f"{CL}#5-wave-2")
    moved = ev.get("moved_to_patterns")
    return _res("fail", "FL dropped or moved the markers / time signature written by fl-headless"
                + (f" (FL kept {len(moved)} as pattern {moved[0][1]}'s own markers)" if moved else ""), ev,
                "fl_headless_mcp._marker_slot: markers 148 (+33/34) + 205 after the arrangement's playlist event "
                "and its track groups", f"{CL}#5-wave-2")


def chk_tempo_lane(ctx):
    p = ctx.make_project("FLH Check Tempo", "small")
    bars = float(H["flp_info"](p).get("song_length_bars") or 4)
    b2 = int(max(2, min(5, bars - 1)))
    ctx.open_in_fl(p)
    r = ctx.call("flp_tempo_write", project=p, output=p, points=[[1, 100], [b2, 120]])
    _save_read(p)
    tm = H["flp_tempo_map"](p)
    ev = {"created": r.get("created"), "to_bar": b2, "map_after_fl_save": (tm.get("map") or [])[:6]}
    heard = {}
    # FL applies a tempo lane's points as the playhead passes them (it doesn't chase a point it was dropped past:
    # PC 2026-10-20 read 100 after a jump straight to bar 3.25), so play from just before the change, through it
    spb = 60.0 / 100.0                                            # seconds per beat at 100 BPM
    try:                                                          # the lane is a playlist clip: song mode plays it
        w = ctx.call("fl_song", action="where")
        ev["song_mode_before"] = w.get("song_mode")
        if w.get("song_mode") is False:
            ctx.call("fl_song", action="song_mode")
    except Exception as e:
        ev["song_mode_error"] = str(e)[:120]
    for label, bar, wait in (("start", 1.0, 1.2), ("after", b2 - 0.25, 4 * 0.25 * spb + 1.0)):
        ctx.call("fl_song", action="goto", bar=bar)
        ctx.call("fl_transport", action="play")
        time.sleep(wait)
        try:
            heard[label] = round(float(H["BRIDGE"].call("state", light=True, timeout=5.0).get("tempo") or 0), 2)
        finally:
            ctx.call("fl_transport", action="stop")
    ev["fl_tempo_while_playing"] = heard
    if abs(heard.get("start", 0) - 100) < 1 and abs(heard.get("after", 0) - 120) < 1:
        return _res("pass", f"the tempo lane plays: FL shows 100 BPM at bar 1 and 120 after bar {b2}", ev,
                    checklist=f"{CL}#5-wave-2")
    return _res("fail", f"FL's tempo while playing was {heard} (wanted 100 then 120)", ev,
                "flp_tempo_write pattern/clip", f"{CL}#5-wave-2")


def chk_empty(ctx):
    b = ctx.make_project("FLH Check Empty", "blank")
    has_ch = bool(_channels(b))
    ctx.call("flp_new_pattern", path=b, name="FLH Verse", place_at_bar=1)
    if has_ch:
        ctx.call("flp_add_notes", path=b, pattern="FLH Verse", notes=[{"note": "C4", "bar": 1}])
    ctx.open_in_fl(b)
    _save_read(b)
    d = H["_load"](b); ev_ = H["_events"](d)
    pats = H["_patterns"](d, ev_)
    names = {pid: pt.get("name") for pid, pt in pats.items()}
    try:
        pnames = {int(x.get("id", x.get("pattern", -1))): x.get("name") for x in
                  (H["flp_patterns"](b).get("patterns") or [])}
        names.update({k: v for k, v in pnames.items() if v})
    except Exception:
        pass
    pid = next((k for k, v in names.items() if v == "FLH Verse"), None)
    clips = H["_clips"](d, ev_)
    on_bar1 = [r for _, _, r in clips if struct.unpack("<I", r[0:4])[0] == 0]
    notes = len(pats.get(pid, {}).get("notes") or []) if pid is not None else 0
    ev = {"template_channels": has_ch, "pattern": pid, "clips_at_bar_1": len(on_bar1), "notes": notes}
    ok = pid is not None and on_bar1 and (notes >= 1 or not has_ch)
    if ok:
        return _res("pass", "a pattern (+ note) and its clip made in FL's empty template survive FL's load+save" +
                    ("" if has_ch else " (the template has no channel, so no note was written)"), ev,
                    checklist=f"{CL}#5-wave-2")
    return _res("fail", "FL dropped the pattern / clip / note added to its empty template", ev,
                "empty-project inserts (_pattern_meta_anchor, AUTOMATION_CLIP template)", f"{CL}#5-wave-2")


def chk_arrangement(ctx):
    p = ctx.make_project("FLH Check Arrangement", "small")
    ctx.open_in_fl(p)
    _save_read(p)
    d = H["_load"](p); ev_ = H["_events"](d)
    arrs = H["_arrangements"](d, ev_)
    cur = next((e for e in ev_ if e["id"] == H["EV_CURRENT_ARRANGEMENT"] and e["len"] == 2), None)
    val = struct.unpack("<H", H["_payload"](d, cur))[0] if cur else None
    ev = {"event_100": val, "arrangements": [a["index"] for a in arrs]}
    if val is not None and val in ev["arrangements"]:
        return _res("pass", f"FL's save holds event 100 = {val}, an existing arrangement" +
                    (" (one arrangement only: the 2+ case is core.md 17)" if len(arrs) < 2 else ""), ev,
                    checklist=f"{CL}#17-project-file-structure")
    return _res("fail", "FL's save has no valid event 100 (current arrangement)", ev, "EV_CURRENT_ARRANGEMENT",
                f"{CL}#17-project-file-structure")


def chk_backups(ctx):
    bk = H["BACKUPS"]
    H["flp_versions"](os.path.join(ctx.sandbox, "none.flp"))       # runs the one-time migration
    flat = [f for f in glob.glob(os.path.join(bk, "*.flp")) if os.path.isfile(f)]
    legacy = len(glob.glob(os.path.join(H["LEGACY_BACKUPS"], "*.flp")))
    p = ctx.make_project("FLH Check Backups", "small")
    twin = os.path.join(ctx.sandbox, "other copy", os.path.basename(p))
    os.makedirs(os.path.dirname(twin), exist_ok=True); shutil.copyfile(p, twin)
    t0 = _tempo(p)
    ctx.call("flp_set_tempo", path=p, bpm=111)
    ctx.call("flp_set_tempo", path=p, bpm=112)
    v = ctx.call("flp_versions", path=p)
    vt = ctx.call("flp_versions", path=twin)
    v = v if isinstance(v, list) else v.get("versions", [])
    vt = vt if isinstance(vt, list) else vt.get("versions", [])
    mine = [x for x in v if not x.get("legacy")]
    ev = {"flat_left": len(flat), "legacy": legacy, "versions": len(mine), "twin_versions": len([x for x in vt
                                                                                                if not x.get("legacy")])}
    restored = None
    if mine:
        ctx.call("flp_restore", path=p, backup=mine[-1]["backup"])
        restored = _tempo(p)
    ev["restored_tempo"] = restored
    bad = []
    if flat:
        bad.append(f"{len(flat)} 1.16 backups weren't migrated out of the flat folder")
    if len(mine) < 2:
        bad.append(f"2 edits made {len(mine)} per-project backups")
    if ev["twin_versions"]:
        bad.append("a same-name project in another folder shares the backups")
    if restored is None or abs(restored - 111) > 0.01:
        bad.append(f"restoring the newest backup gave tempo {restored} (wanted 111; the original was {t0})")
    if bad:
        return _res("fail", "; ".join(bad), ev, "per-project backups / migration", f"{CL}#4-bug-report")
    return _res("pass", f"old backups migrated ({legacy} kept as name-match legacy); each project has its own "
                        "backups and restore works", ev, checklist="checklists/00_FIRST_HOUR.md#b-bridge-and-setup-10-min")


def _recycle_bin_has(name, size):
    """Best effort: a file of this size and extension in the drive's Recycle Bin ($R* entries). None = can't tell."""
    if os.name != "nt":
        return None
    root = os.path.splitdrive(os.path.abspath(H["roots"]()[0]))[0] + "\\"
    ext = os.path.splitext(name)[1]
    try:
        hits = glob.glob(os.path.join(root, "$Recycle.Bin", "*", "$R*" + ext))
        return any(os.path.getsize(h) == size for h in hits)
    except OSError:
        return None


def chk_recycle(ctx):
    junk = os.path.join(ctx.sandbox, "recycle me.txt")
    data = b"fl-headless recycle check " + str(time.time()).encode()
    with open(junk, "wb") as f:
        f.write(data)
    r = ctx.call("files_recycle", paths=[junk])
    gone = not os.path.exists(junk)
    in_bin = _recycle_bin_has(os.path.basename(junk), len(data))
    ev = {"result": {k: r.get(k) for k in ("recycled", "refused") if isinstance(r, dict) and k in r},
          "gone": gone, "found_in_recycle_bin": in_bin}
    if gone and in_bin is not False:
        return _res("pass", "files_recycle moved the sandbox file to the Recycle Bin" +
                    ("" if in_bin else " (the bin's contents couldn't be listed to double-check)"), ev,
                    checklist=f"{CL}#6-review-fixes")
    return _res("fail", "files_recycle didn't put the file in the Recycle Bin" if gone else "the file is still there",
                ev, "files_recycle / SHFileOperationW", f"{CL}#6-review-fixes")


def _c(id_, title, risk, minutes, needs, run, preflight=False):
    return {"id": id_, "title": title, "area": id_.split(".")[0], "risk": risk, "minutes": minutes,
            "needs": set(needs), "run": run, "preflight": preflight}


def core_checks():
    return [
        _c("core.userdata", "user data folder = the one FL runs its script from", 3, 0.2, {"fl", "bridge"},
           chk_userdata, preflight=True),
        _c("bridge.ping", "bridge script v4 answers (update needed?)", 3, 0.2, {"fl", "bridge"}, chk_bridge,
           preflight=True),
        _c("render.in_fl", "in-FL render probe + a real render of a tiny song", 5, 1.0, {"fl", "bridge"}, chk_render),
        _c("core.identity_save", "which file FL has open: proof by save, same-name twin, 'Project N'", 5, 1.0,
           {"fl", "bridge"}, chk_identity),
        _c("live_notes.probe", "live notes probe, a live edit checked by save, one undo step", 4, 1.0,
           {"fl", "bridge"}, chk_live_notes),
        _c("lazy.big_project", "lazy reload: big project load cost, 3 edits (queued or live), one sync, hand edit "
                               "kept", 4, 2.0, {"fl", "bridge"}, chk_lazy),
        _c("files.slide_flag", "slide notes (flag 0x08) after FL's load+save", 3, 0.7, {"fl", "bridge"}, chk_slides),
        _c("files.tempo_lane", "tempo lane made by fl-headless plays in FL", 3, 0.7, {"fl", "bridge"}, chk_tempo_lane),
        _c("files.empty_inserts", "pattern + clip + note in FL's empty template survive FL", 3, 0.5, {"fl", "bridge"},
           chk_empty),
        _c("files.recycle", "files_recycle goes to the Recycle Bin", 3, 0.1, set(), chk_recycle),
        _c("render.cli_refusal", "command-line render refused while FL is open (FL stays)", 2, 0.2, {"fl"},
           chk_cli_refusal),
        _c("files.channel_glide", "channel glide event 221 after FL's load+save", 2, 0.5, {"fl", "bridge"}, chk_glide),
        _c("files.markers_meter", "markers + time signature after FL's load+save", 2, 0.5, {"fl", "bridge"},
           chk_markers),
        _c("files.backups", "backups migrated, per-project backups, restore", 2, 0.2, set(), chk_backups),
        _c("files.current_arrangement", "current arrangement event 100 in FL's save", 1, 0.4, {"fl", "bridge"},
           chk_arrangement),
        _c("core.untitled_refusal", "an unsaved new project in FL is never lost (you do File > New)", 4, 1.0,
           {"fl", "bridge", "user"}, chk_untitled),
    ]


# ============================================================================= module checks (other file)
def module_checks():
    """flh_pccheck_mod.checks(ns) -> (checks, error or None). Works without the file."""
    import importlib
    here = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isfile(os.path.join(here, MOD_NAME + ".py")) and MOD_NAME not in sys.modules:
        return [], f"{MOD_NAME}.py isn't installed (core checks only)"
    try:
        mod = sys.modules.get(MOD_NAME) or importlib.import_module(MOD_NAME)
        raw = list(mod.checks(H) or [])
    except Exception as e:
        return [], f"flh_pccheck_mod failed to load: {type(e).__name__}: {e}"
    out, bad = [], []
    for c in raw:
        try:
            ok = isinstance(c, dict) and c.get("id") and callable(c.get("run"))
            if not ok:
                raise ValueError("needs id and run")
            c = dict(c); c["needs"] = set(c.get("needs") or ()); c["risk"] = int(c.get("risk", 2))
            c["minutes"] = float(c.get("minutes", 1)); c.setdefault("title", c["id"])
            c.setdefault("area", str(c["id"]).split(".")[0]); c["preflight"] = False
            out.append(c)
        except Exception as e:
            bad.append(f"{(c or {}).get('id') if isinstance(c, dict) else c}: {e}")
    return out, ("; ".join(bad)[:300] if bad else None)


def _as_list(x):
    if x is None:
        return []
    if isinstance(x, str):
        return [s.strip() for s in x.split(",") if s.strip()]
    return [str(s).strip() for s in x if str(s).strip()]


def _matches(c, sel):
    return any(s == c["id"] or s == c["area"] or c["id"].startswith(s + ".") for s in sel)


EXPECT_FACTOR_MODULE = 0.6        # module checks budget deadlines generously; their typical real time is ~60% of it
QUICK_MIN_RISK = 3                # quick run (default): the checks that matter most - risk 3+, plus setup


def _expect(c):
    return c["minutes"] * (EXPECT_FACTOR_MODULE if c.get("module") else 1.0)


def plan(areas=None, skip=None, max_risk=None, full=False):
    """The checks to run, in order: preflight (setup, bridge), then by risk (riskiest first); checks that need the
    person last (so the automatic part runs unattended). -> (plan, module_error)."""
    mods, err = module_checks()
    ids = set(); allc = []
    for c in mods:
        c["module"] = True
    for c in core_checks() + mods:
        if c["id"] in ids:
            continue
        ids.add(c["id"]); allc.append(c)
    areas, skip = _as_list(areas), _as_list(skip)
    sel = [c for c in allc if (not areas or _matches(c, areas) or c["preflight"]) and not _matches(c, skip)]
    if max_risk is not None:
        sel = [c for c in sel if c["preflight"] or c["risk"] <= int(max_risk)]
    if not full and not areas:                                  # quick: the important checks only
        sel = [c for c in sel if c["preflight"] or c["risk"] >= QUICK_MIN_RISK]
    order = {c["id"]: i for i, c in enumerate(allc)}
    sel.sort(key=lambda c: (not c["preflight"], bool(c["needs"] & PERSON), -c["risk"], order[c["id"]]))
    return sel, err


def _user_action(c):
    n = c["needs"]
    parts = []
    if "user" in n:
        parts.append("a hand step in FL")
    if "mic" in n:
        parts.append("a microphone (sing/speak a few seconds)")
    if "midi_keyboard" in n:
        parts.append("play a few notes on the MIDI keyboard")
    return ", ".join(parts) or None


def _dry_run(sel, err, unattended):
    rows = [{"id": c["id"], "title": c["title"], "risk": c["risk"], "minutes": c["minutes"],
             **({"you": _user_action(c)} if _user_action(c) else {}),
             **({"internet": True} if "network" in c["needs"] else {})} for c in sel]
    auto = round(sum(_expect(c) for c in sel if not _user_action(c)), 1)
    you = round(sum(_expect(c) for c in sel if _user_action(c)), 1)
    pre = {}
    try:
        pre["fl_running"] = bool(H["fl_running"]())
        if pre["fl_running"]:
            t = _title(); pre["fl_title"] = t
            pre["fl_has_new_unsaved_project"] = bool(H["_is_untitled"](t))
    except Exception as e:
        pre["fl"] = f"unknown: {e}"
    return {"dry_run": True, "checks": rows, "count": len(rows), "estimated_minutes": {"automatic": auto,
            "with_you": you if not unattended else 0, "total": auto + (0 if unattended else you)},
            "you_will": [
                "have FL Studio open with a SAVED song (or a fresh empty project); the check saves it with FL's own "
                "save, works only on copies in a sandbox folder and reopens your song at the end",
                "not use FL during the automatic part (about %d min); FL's window will switch between test songs" % auto,
                *([] if unattended else [f"{r['id']}: {r['you']}" for r in rows if r.get("you")]),
                "answer prompts with fl_pc_check(answer='done') (or 'skip'); stop any time with fl_pc_check(stop=true)"],
            **({"module_checks": err} if err else {}), "fl_now": pre,
            "start": "fl_pc_check()" + (" with unattended=true to skip the hand steps" if not unattended else "")}


# ============================================================================= the user's settings around a run
# A run (fl_pc_check, fl_sweep) changes settings of the PERSON's: the live-notes probe writes live_notes, the lazy
# check sets reload_mode. PC 2026-10-23: after an unattended sweep fl_status showed live_notes: false - the probe ran
# unattended, needed the hand step, its failure switched live notes off, and the check was then only 'skipped'.
# Every run now remembers both before it starts (in state.json: a crash or restart puts them back too) and puts them
# back afterwards; only a live-notes check that PASSED in the run may leave live notes on.
USER_SETTINGS = ("live_notes", "reload_mode")


def settings_save(by):
    """Remember the user's USER_SETTINGS before a run. Nested (fl_pc_check inside fl_sweep): the outer record stays.
    -> (snapshot, outer: this call owns the record)."""
    st = H["_state"]() or {}
    rec = st.get("user_settings_before")
    if isinstance(rec, dict) and by != "sweep" and rec.get("by") == "sweep" and _sweep_running():
        return {k: rec.get(k) for k in USER_SETTINGS}, False
    snap = {k: st.get(k) for k in USER_SETTINGS}
    H["_state"]({"user_settings_before": dict(snap, by=by, t=int(time.time()))})
    _RUN["live_notes_proved"] = None
    return snap, True


def settings_restore(snap=None, clear=True):
    """Put the user's USER_SETTINGS back (snap, else the record in state.json). A live-notes check that passed in this
    run keeps live notes on (that is what the person ran it for). -> {setting: value now} of what changed, or None."""
    st = H["_state"]() or {}
    rec = st.get("user_settings_before")
    if snap is None:
        snap = rec if isinstance(rec, dict) else None
    if snap is None:
        return None
    want = {k: snap.get(k) for k in USER_SETTINGS}
    if isinstance(_RUN.get("live_notes_proved"), dict):
        want["live_notes"] = _RUN["live_notes_proved"]
    changed = {k: want[k] for k in USER_SETTINGS if st.get(k) != want[k]}
    upd = dict(changed)
    if clear:
        upd["user_settings_before"] = None
    if upd:
        H["_state"](upd)
    return {k: (bool((v or {}).get("ok")) if k == "live_notes" else (v or "default")) for k, v in changed.items()} or None


def _settings_recover(in_run=False, force=False):
    """A run cut off (crash, server restart) left its record: the user's settings back. None while a run that owns
    the record is still going. in_run: called by a starting fl_pc_check (only a running sweep's record is live);
    force: by a starting sweep (any record left is stale)."""
    rec = (H["_state"]() or {}).get("user_settings_before")
    if not isinstance(rec, dict):
        return None
    if not force and rec.get("by") == "sweep" and _sweep_running():
        return None
    if not in_run and rec.get("by") == "pc_check" and _RUN.get("running"):
        return None
    if not in_run and any(str(j.get("job") or "").startswith(("pccheck-", "sweep-")) and j.get("status") == "running"
           for j in list((H.get("JOBS") or {}).values())):
        return None
    _RUN["live_notes_proved"] = None
    return settings_restore(rec) or {}


# ============================================================================= sandbox safety (pc_check, bench, sweep)
# PC 2026-10-23 (fl_sweep from FL's empty new project): pc_check couldn't put FL back on a new project, so FL stayed on
# a sandbox song; the bench after it recorded that sandbox song as "the user's song" and reopened it at the end, FL
# kept holding a file from the sandbox and the sandbox was never recycled (the failure only in a note). And a sandbox
# final was filed into the active collection's final folder (fl_finalize follows the active profile). So: the user's
# song is never a sandbox file, FL leaves the sandbox before it's recycled (said with its path when it can't be), and
# every run works under a temporary sandbox profile whose final folder is inside the sandbox.
CHECK_PROFILE = "_fl-headless check"                              # the temporary profile of pc_check / bench runs
TEMP_PROFILES = (CHECK_PROFILE, "_fl-headless bench")             # (flh_bench's BENCH_PROFILE too)
PROFILE_STATE = "sandbox_profile_user"                            # state: the user's own collection while one is active
LEFTOVERS_STATE = "sandbox_leftovers"                             # state: sandboxes that couldn't be recycled yet
RECYCLE_RETRY_S = 3.0                                             # before the one retry of a sandbox recycle (scaled)
EMPTY_WAIT_S = 30.0                                               # FL's title showing its empty template (scaled)


def _sandbox_bases():
    try:
        return [os.path.join(r, SANDBOX_DIR) for r in (H["roots"]() or [])]
    except Exception:
        return []


def in_sandbox(path):
    """True for a path inside any fl-headless sandbox (<an FLH_ROOTS folder>/_fl-headless-check/...: pc_check, bench
    and sweep runs all work there)."""
    if not path:
        return False
    return any(_inside(path, b) and _key(path) != _key(b) for b in _sandbox_bases())


def user_song(path):
    """The song to put back in FL at the end -> path, or None (FL gets an empty project). Never a sandbox file: FL
    showing one at the start means an earlier run left it there, not that it's the user's. Nor FL's empty template
    (an earlier run's end): that is an empty project."""
    return None if not path or in_sandbox(path) or _is_blank(path) else path


def _sandbox_root_of(path):
    """<base>/<run folder> of a path inside a sandbox, or None."""
    for b in _sandbox_bases():
        if _inside(path, b) and _key(path) != _key(b):
            rel = os.path.relpath(os.path.abspath(path), os.path.abspath(b))
            return os.path.join(b, rel.replace("\\", "/").split("/")[0])
    return None


def _wait_s(seconds):
    try:
        return float(H["_scaled"](seconds))
    except Exception:
        return float(seconds)


def _fl_sandbox_song(sandbox=None, exclude=None):
    """The sandbox song FL has open (in `sandbox`, else in any sandbox) -> its path, or None. FL's own answer first
    (_open_project_guess); with a `sandbox` given, else FL's title naming a song in it (and not the user's song
    `exclude`) - never by title alone across all sandboxes (a user's song may share a name with an old test song)."""
    try:
        if not H["fl_running"]():
            return None
    except Exception:
        return None
    try:
        cur = H["_open_project_guess"]()[0]
    except Exception:
        cur = None
    if cur:
        if (sandbox and _inside(cur, sandbox)) or (not sandbox and in_sandbox(cur)):
            return cur
        return None
    stem = (H["_title_stem"](_title()) or "").lower()
    if not sandbox or not stem or (exclude and stem in _names(exclude)) or not os.path.isdir(sandbox):
        return None
    for top in (sandbox,):
        for dp, _dn, fs in os.walk(top):
            for f in fs:
                if f.lower().endswith(".flp") and (_stem_of(f).lower() == stem or stem in _names(os.path.join(dp, f))):
                    return os.path.join(dp, f)
    return None


def _is_new_project_title(t):
    """FL's title shows a new (untitled) project: 'FL Studio 2026' or 'Project 7 - FL Studio 2026'. An empty title
    (FL busy, no main window) is NOT one."""
    t = str(t or "").strip()
    if not t or "fl studio" not in t.lower():
        return False
    return not H["_title_stem"](t) or H["_is_untitled"](t)


def _is_blank_title(t):
    """FL's title names its empty template (left by _new_empty_project's second route) and no song in FLH_ROOTS has
    that name: an empty project, not one of the user's songs."""
    stem = H["_title_stem"](t)
    if not stem:
        return False
    try:
        blank = H["_blank_project"]()
    except Exception:
        blank = None
    if not blank or stem.lower() not in _names(blank):
        return False
    try:                                                          # (the template itself can be FL's 'last opened')
        return not [c for c in (H["_name_candidates"](stem) or []) if _key(c) != _key(blank)]
    except Exception:
        return False


def _is_blank(path):
    try:
        blank = H["_blank_project"]()
    except Exception:
        return False
    return bool(path and blank and _key(path) == _key(blank))


def _new_empty_project(discard=()):
    """FL on an empty project at the end (the run started on one, or the user's song is unknown): FL's own File > New
    (Ctrl+N) first; when that doesn't take (FL not in front, the key didn't land), FL's empty template, opened the way
    the server refreshes a project (_open_running_or_fail; 'save changes?' answered No only for the sandbox songs in
    `discard`, which FL saved first). -> {ok, how, note | why}.
    PC-VERIFY: after the template route FL's title shows the template's name (or a new project), not a sandbox song"""
    if _ctrl_n_new_project():
        return {"ok": True, "how": "file_new", "note": "FL is back on a new empty project"}
    tries = ["File > New (Ctrl+N) didn't change FL's title to a new project"]
    try:
        blank = H["_blank_project"]()
    except Exception:
        blank = None
    if not blank:
        return {"ok": False, "how": "file_new", "tries": tries, "why": "File > New didn't take and FL's empty template "
                "wasn't found", "note": "use File > New in FL"}
    try:
        try:
            H["BRIDGE"].call("transport", action="save", timeout=5.0)   # the sandbox song saved: 'No' loses nothing
        except Exception:
            pass
        H["_open_running_or_fail"](blank, on_save="no", discard_ok=[p for p in discard if p and in_sandbox(p)] + [blank])
    except Exception as e:
        return {"ok": False, "how": "empty_template", "tries": tries, "why": f"FL's empty template didn't open: "
                f"{type(e).__name__}: {str(e)[:160]}", "note": "use File > New in FL"}
    names = _names(blank); t0 = time.time(); lim = _wait_s(EMPTY_WAIT_S); ok = False
    while time.time() - t0 < lim:
        t = _title()
        if (H["_title_stem"](t) or "").lower() in names or _is_new_project_title(t):
            ok = True
            break
        time.sleep(0.3)
    if ok:
        return {"ok": True, "how": "empty_template", "tries": tries,
                "note": f"FL is on its empty template ({_stem_of(blank)}): an empty project"}
    return {"ok": False, "how": "empty_template", "tries": tries, "title_now": _title(),
            "why": "FL's title didn't show its empty template", "note": "use File > New in FL"}


@contextlib.contextmanager
def sandbox_profile(sandbox, name=CHECK_PROFILE, rules=None):
    """A temporary collection profile active for the block, so nothing a run makes in the sandbox follows the user's
    collection (tools read the active profile): the user's user-wide settings only (edition, owned plugins, palette,
    access ... - flh_store.USER_WIDE_KEYS), no final_name / finals / songs / taste, and final_root INSIDE the sandbox
    (finals, their stems and previews stay there). `rules`: ending rules for the block (the bench). The user's own
    collection comes back afterwards (state only: their profile files are only read); a crash leaves a record
    (PROFILE_STATE) that collection_recover() puts back. Already active under this name: nothing to do."""
    S = sys.modules.get("flh_store")
    st = H["_state"]() or {}
    prev = st.get("collection")
    if S is None or getattr(S, "H", None) in (None, {}) or prev == name:
        yield {"name": prev}
        return
    rec = st.get(PROFILE_STATE)
    outer = not isinstance(rec, dict)
    user = prev if outer else rec.get("collection")
    if outer:
        H["_state"]({PROFILE_STATE: {"collection": prev}})
    try:
        base = S.profile_get(user) if user and user not in TEMP_PROFILES else {}
    except Exception:
        base = {}
    keys = getattr(S, "USER_WIDE_KEYS", ("fl_edition", "owned_plugins", "trial_plugins", "palette", "access"))
    prof = {k: v for k, v in (base or {}).items() if k in keys}
    finals = os.path.join(sandbox, "finals")
    H["writable"](os.path.join(finals, "x.flac"))
    prof.update(name=name, final_root=finals, folder=sandbox)
    if rules:
        prof["rules"] = dict(rules)
    path = S._profile_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:                  # our own temporary file in our state folder
        json.dump(prof, f, indent=1)
    H["_state"]({"collection": name})
    try:
        yield prof
    finally:
        H["_state"](dict({"collection": prev}, **({PROFILE_STATE: None} if outer else {})))
        try:
            os.remove(path)
        except OSError:
            pass


def collection_recover(force=False):
    """A run cut off inside sandbox_profile (crash, server restart) left a temporary profile active: the user's own
    collection back (state only) and the temporary files removed. force: called by the starting run itself; else
    only when no pc_check / bench / sweep job of this server runs. -> {collection} put back, or None."""
    st = H["_state"]() or {}
    rec = st.get(PROFILE_STATE)
    cur = st.get("collection")
    if not isinstance(rec, dict) and cur != CHECK_PROFILE:
        return None
    if not force:
        if _RUN.get("running") or _sweep_running() or getattr(sys.modules.get("flh_bench"), "_RUN", {}).get("running"):
            return None
        if any(str(j.get("job") or "").startswith(("pccheck-", "bench-", "sweep-")) and j.get("status") == "running"
               for j in list((H.get("JOBS") or {}).values())):
            return None
    back = (rec or {}).get("collection")
    back = None if back in TEMP_PROFILES else back
    upd = {PROFILE_STATE: None}
    if cur in TEMP_PROFILES or cur is None:
        upd["collection"] = back
    H["_state"](upd)
    S = sys.modules.get("flh_store")
    for n in TEMP_PROFILES:
        try:
            os.remove(S._profile_path(n))                         # our own temporary files
        except Exception:
            pass
    return {"collection": upd.get("collection", cur)}


def _note_leftover(sandbox):
    """A sandbox that couldn't be recycled: recycled by the next run (after FL has left it)."""
    try:
        cur = [p for p in (H["_state"]().get(LEFTOVERS_STATE) or []) if _key(p) != _key(sandbox)]
        H["_state"]({LEFTOVERS_STATE: (cur + [sandbox])[-20:]})
    except Exception:
        pass


def _recycle_sandbox(sandbox):
    """The sandbox to the Recycle Bin, once more after a short wait when it's still there -> (True, None) or
    (False, why). Called only after FL has left it."""
    why = None
    for attempt in (1, 2):
        try:
            _clean_sandbox(sandbox)
        except Exception as e:
            why = f"{type(e).__name__}: {str(e)[:200]}"
        if not os.path.exists(sandbox):
            return True, None
        if attempt == 1:
            _forget_sandbox(sandbox)
            time.sleep(_wait_s(RECYCLE_RETRY_S))
    return False, why or "it was still there after files_recycle"


def _recycle_leftovers():
    """Sandboxes earlier runs couldn't recycle (LEFTOVERS_STATE), now that FL has left them -> what happened, or
    None. Only folders inside a sandbox base are touched."""
    lo = [p for p in ((H["_state"]() or {}).get(LEFTOVERS_STATE) or []) if isinstance(p, str)]
    if not lo:
        return None
    done, still = [], []
    for p in lo:
        if not os.path.isdir(p) or not in_sandbox(p):
            continue
        if _fl_sandbox_song(p):
            still.append(p); continue
        _forget_sandbox(p)
        ok, _why = _recycle_sandbox(p)
        (done if ok else still).append(p)
    H["_state"]({LEFTOVERS_STATE: still or None})
    out = {}
    if done:
        out["recycled"] = done
    if still:
        out["still_there"] = still
    return out or None


def leave_sandbox(sandbox, original=None, exclude=None):
    """FL off the sandbox's songs: the user's song reopened, else an empty project -> what was done, or None when FL
    wasn't on one. (A cut-off run's recovery; the run's own end uses _restore.)"""
    held = _fl_sandbox_song(sandbox, exclude)
    if not held:
        return None
    orig = user_song(original)
    if orig and os.path.isfile(orig):
        H["_flh_call"]("fl_open", {"project": orig})
        return {"reopened": orig}
    r = _new_empty_project([held])
    return {"empty_project": bool(r.get("ok")), **({"why": r["why"]} if r.get("why") else {})}


def finish_sandbox(rep, sandbox, keep=False, exclude=None):
    """After the restore: the run's sandbox to the Recycle Bin only once FL has left it (one retry), said with its path
    when it couldn't be (rep['sandbox_not_recycled']; the next run tries again); then earlier leftovers."""
    held = _fl_sandbox_song(sandbox, exclude)
    r = rep.get("restored")
    if held and isinstance(r, dict) and r.get("ok"):
        r["ok"] = False
        r["why"] = f"FL still shows '{_stem_of(held)}' from the sandbox"
    if keep:
        rep["sandbox_kept"] = sandbox
    elif held:
        rep["sandbox_kept"] = sandbox
        rep["sandbox_not_recycled"] = {"path": sandbox, "why": f"FL still has '{_stem_of(held)}' from it open"}
        _note_leftover(sandbox)
    else:
        ok, why = _recycle_sandbox(sandbox)
        if ok:
            rep["sandbox_recycled"] = True
        else:
            rep["sandbox_kept"] = sandbox
            rep["sandbox_not_recycled"] = {"path": sandbox, "why": why}
            _note_leftover(sandbox)
    try:
        lo = _recycle_leftovers()
        if lo:
            rep["earlier_sandboxes"] = lo
    except Exception as e:
        rep.setdefault("notes", []).append(f"earlier sandboxes not recycled: {type(e).__name__}: {e}")


def _sandbox_note(rep):
    """The next step when the sandbox stayed behind, or None."""
    nr = rep.get("sandbox_not_recycled")
    if not nr:
        return None
    return (f"the sandbox wasn't moved to the Recycle Bin ({nr.get('why')}): {nr.get('path')} - the next "
            "fl_pc_check / fl_bench tries again once FL has left it, or move it to the Recycle Bin yourself")


# ============================================================================= the runner
def _sweep_running():
    """The running fl_sweep's job id (it runs fl_pc_check and fl_bench itself), else None."""
    sw = sys.modules.get("flh_sweep")
    r = getattr(sw, "_RUN", None) or {}
    return (r.get("job") or "yes") if r.get("running") else None


def _quick_precheck():
    """The cheap part of _preflight, answered at once (before a job starts): what the person must do first, or None."""
    if _RUN["running"]:
        return None
    try:
        if not H["fl_running"]():
            return "Start FL Studio and open a saved song (or leave its empty new project)"
        if len(H["_fl_pids"]() or []) > 1:
            return "Two copies of FL are running: close the extra one"
        t = _title()
        if H["_title_stem"](t) and H["_is_untitled"](t) and "*" in t:
            return "FL has a new project with unsaved changes: save it (File > Save as...) or discard it"
        pops = [d.get("title") for d in _dialogs() if d.get("blocks_fl", True)]
        if pops:
            return f"FL shows a popup ({pops[:3]}): answer it in FL first"
    except Exception:
        return None
    return None


def _preflight(ctx, user=None):
    """FL running, one copy, no popup, the open song identified and saved -> {original, title, pids}. Raises
    UserMustAct with what to do. The original is never a sandbox file: FL on a song of an earlier run's sandbox ->
    original None (an empty project at the end) and on_sandbox = that file. user={'original': ...} (a sweep's later
    run): the song the sweep found at its start, handed through - FL isn't asked again (it shows what the run before
    left)."""
    if not H["IS_WINDOWS"]:
        raise UserMustAct("fl_pc_check runs on the Windows PC with FL Studio (this machine isn't Windows)")
    if not H["fl_running"]():
        raise UserMustAct("Start FL Studio and open a saved song (or leave the empty new project), then run "
                          "fl_pc_check() again")
    pids = list(H["_fl_pids"]() or [])
    if len(pids) > 1:
        raise UserMustAct(f"{len(pids)} copies of FL are running: close the extra one, then run fl_pc_check() again")
    pops = [d for d in _dialogs() if d.get("blocks_fl", True)]
    if pops:
        raise UserMustAct(f"FL shows a popup ({[d.get('title') for d in pops][:3]}): answer it in FL first")
    try:                                                          # popups that were the user's before the run: the
        ctx.dialogs_at_start = {d.get("hwnd") for d in _dialogs()}   # restore never cancels those
    except Exception:
        pass
    title = _title()
    if isinstance(user, dict) and "original" in user:
        if H["_title_stem"](title) and H["_is_untitled"](title) and "*" in title:
            raise UserMustAct("FL has a new project with unsaved changes: save it (File > Save as...) or close it, "
                              "then run fl_pc_check() again")
        on_sb = _fl_sandbox_song()
        return dict({"original": user_song(user.get("original")), "title": title, "pids": pids, "handed": True},
                    **({"on_sandbox": on_sb} if on_sb else {}))
    if not H["_title_stem"](title):
        try:                                                      # FL 2026.1.7 titles a new project just
            H["BRIDGE"].call("ping", timeout=3.0)                 # 'FL Studio 2026': answering = a new project
        except Exception:
            raise UserMustAct("FL is still starting or loading a project: wait until its title shows the project, "
                              "then run fl_pc_check() again")
        return {"original": None, "title": title, "pids": pids}
    if H["_is_untitled"](title) or _is_blank_title(title):        # (FL's empty template: left by an earlier run)
        if "*" in title:
            raise UserMustAct("FL has a new project with unsaved changes: save it (File > Save as...) or close it, "
                              "then run fl_pc_check() again")
        return {"original": None, "title": title, "pids": pids}
    cur, _t = H["_open_project_guess"]()
    if cur and in_sandbox(cur):                                   # an earlier run left FL on its song: not the user's
        return {"original": None, "title": title, "pids": pids, "on_sandbox": cur}
    cands = [cur] if cur else list(H["_name_candidates"](H["_title_stem"](title)) or [])
    if not cands:
        raise UserMustAct(f"FL shows '{H['_title_stem'](title)}', which isn't a project inside your fl-headless "
                          "folders (FLH_ROOTS): save it inside them, or open a song from there, then run "
                          "fl_pc_check() again")
    proven = H["_prove_open"](cands[0])                            # FL's own save: proof + nothing unsaved
    if proven == "never_saved":
        raise UserMustAct("FL's project was never saved: save it (File > Save as...), then run fl_pc_check() again")
    if not proven:
        raise UserMustAct(f"FL didn't save '{H['_title_stem'](title)}' when asked (busy, a popup, or the file is "
                          "outside FLH_ROOTS): press Ctrl+S in FL, then run fl_pc_check() again")
    if in_sandbox(proven):
        return {"original": None, "title": title, "pids": pids, "on_sandbox": proven}
    return {"original": proven, "title": title, "pids": pids}


def _answer_popups(ctx, where):
    """Leftover FL popups after a check: a picture, then Escape (Cancel) - never 'Save anyway' or 'No'."""
    pops = _dialogs()
    if not pops:
        return []
    snap = ctx.snapshot(f"popup_after_{where}")
    out = []
    for d in pops:
        out.append({"title": d.get("title"), "text": (d.get("text") or [])[:2], "picture": snap})
        try:
            H["fl_dialog"]("click", "escape", d.get("hwnd"))
        except Exception:
            pass
    return out


def _ctrl_n_new_project():
    """FL back to a fresh empty project (it had one when the run started): File > New with the saved sandbox song
    open (no 'save changes?' then). True when FL's title shows a new project."""
    try:
        H["BRIDGE"].call("transport", action="save", timeout=5.0)
        time.sleep(1.0)
        if not H["_fl_foreground"]():
            return False
        H["_send_keys"](0x4E, (0x11,))
        t0 = time.time()
        while time.time() - t0 < 10:
            time.sleep(0.4)
            for d in _dialogs():                                 # a 'save changes?' anyway: Cancel, keep the song
                if d.get("kind") == "save_changes":
                    H["fl_dialog"]("click", "escape", d.get("hwnd")); return False
            if _is_new_project_title(_title()):                  # (an empty title - FL busy - isn't one: _is_untitled
                return True                                      # said yes to it, and the sandbox song stayed open)
    except Exception:
        return False
    return False


def _forget_sandbox(sandbox):
    """No background check or queued edit of a sandbox file outlives the run."""
    try:
        pv = H["_PENDING_VERIFY"]
        pv["__gen"] = pv.get("__gen", 0) + 1                     # verify jobs still waiting step aside
        for k in [k for k in list(pv) if k != "__gen" and _inside(k, sandbox)]:
            pv.pop(k, None)
    except Exception:
        pass
    try:
        for v in list(H["_pending_all"]().values()):
            if _inside(v.get("project", ""), sandbox):
                H["_pending_set"](v["project"], [])
    except Exception:
        pass
    try:                                                          # a background flush of a sandbox song still
        A, L = H["_AUTOSYNC"], H["_AUTOSYNC_LOCK"]                # waiting for FL (the run held it) gives up instead
        with L:                                                   # of taking FL in the middle of the next step
            for k in [k for k, v in list(A.items()) if (v or {}).get("project") and _inside(v["project"], sandbox)]:
                A.pop(k, None)
    except Exception:
        pass


def _recycle(paths):
    paths = [p for p in paths if p and os.path.exists(p)]
    if not paths:
        return {"recycled": []}
    return H["files_recycle"](paths)


def _clean_sandbox(sandbox):
    """The sandbox -> the Recycle Bin; the backup copies fl-headless made of its projects (in the state folder:
    our own copies of sandbox files only, like the server's own backup rotation) are removed."""
    flps = [os.path.join(dp, f) for dp, _, fs in os.walk(sandbox) for f in fs if f.lower().endswith(".flp")]
    bdirs = [H["_backup_dir"](p) for p in flps]
    try:
        r = _recycle([sandbox])
    except Exception:                                             # Windows refused the folder (shell code 120: a
        time.sleep(2.0)                                           # file still being written/held a moment after the
        try:                                                      # run - PC 2026-10-22); the same folder piece by
            r = _recycle([sandbox])                               # piece then worked: its songs first, then itself
        except Exception:
            for dp in sorted({os.path.dirname(f) for f in flps}, key=len, reverse=True):
                if os.path.isdir(dp) and os.path.normcase(dp) != os.path.normcase(sandbox):
                    try:
                        _recycle([dp])
                    except Exception:
                        pass
            r = _recycle([sandbox])
    for b in bdirs:
        if os.path.isdir(b) and _inside(b, H["BACKUPS"]):
            shutil.rmtree(b, ignore_errors=True)
    parent = os.path.dirname(sandbox)
    try:
        if os.path.isdir(parent) and not os.listdir(parent):
            _recycle([parent])
    except Exception:
        pass
    return r


def _prune_reports(base):
    """Our own earlier report folders (state folder) beyond the newest REPORT_KEEP are removed."""
    try:
        old = sorted(d for d in glob.glob(os.path.join(base, "2*")) if os.path.isdir(d))[:-REPORT_KEEP]
        for d in old:
            if os.path.isfile(os.path.join(d, "report.json")) and _inside(d, _state_dir()):
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def _recover_previous(keep_sandbox=False):
    """A run cut off (server restart, crash): reopen the user's song if FL still shows a sandbox project, recycle
    the old sandbox. -> what was done, or None."""
    back = _settings_recover(in_run=True)                         # the user's live notes / reload mode
    coll = None
    try:
        coll = collection_recover(force=True)                     # a cut-off run's temporary profile
    except Exception:
        pass
    st = H["_state"]().get("pc_check_run")
    if not st:
        out = {}
        if back:
            out["settings_restored"] = back
        if coll:
            out["collection_restored"] = coll
        return out or None
    out = {"previous_run": st.get("started"), "sandbox": st.get("sandbox")}
    if back:
        out["settings_restored"] = back
    if coll:
        out["collection_restored"] = coll
    sb = st.get("sandbox")
    if st.get("reload_mode_saved"):                               # cut off inside the lazy check: its mode back
        H["_state"]({"reload_mode": st.get("reload_mode_before")}); out["reload_mode_restored"] = st.get(
            "reload_mode_before") or "default"
    try:
        if sb:
            r = leave_sandbox(sb, st.get("original"))
            if r:
                out.update(r)
    except Exception as e:
        out["reopen_error"] = str(e)[:160]
    if sb and os.path.isdir(sb) and not keep_sandbox:
        if _fl_sandbox_song(sb):
            out["sandbox_error"] = "FL still has a song from it open"; _note_leftover(sb)
        else:
            _forget_sandbox(sb)
            ok, why = _recycle_sandbox(sb)
            if ok:
                out["sandbox_recycled"] = True
            else:
                out["sandbox_error"] = str(why)[:160]; _note_leftover(sb)
    H["_state"]({"pc_check_run": None})
    return out


def _run(sel, mod_err, unattended=False, keep_sandbox=False, user=None):
    """user: a dict a sweep hands through its runs - {} is filled with the song found at the start ({'original': path
    or None}); {'original': ...} is used as the song to put back (a later run never takes what an earlier one left in
    FL for the user's song)."""
    T0 = time.time(); _RUN["t0"] = T0
    stamp = _now()
    report_base = os.path.join(_state_dir(), "pc_check")
    report_dir = os.path.join(report_base, stamp)
    os.makedirs(report_dir, exist_ok=True)
    rep = {"fl_headless": H["SERVER"]["version"], "started": time.strftime("%Y-%m-%d %H:%M:%S"),
           "unattended": bool(unattended), "checks": [], "notes": []}
    if mod_err:
        rep["notes"].append(f"module checks: {mod_err}")
    recovered = None
    try:
        recovered = _recover_previous(keep_sandbox)
    except Exception as e:
        rep["notes"].append(f"recovering an interrupted run failed: {e}")
    if recovered:
        rep["recovered_previous_run"] = recovered
    roots = H["roots"]()
    if not roots:
        return _finish(rep, report_dir, T0, stop="FLH_ROOTS isn't set: run flh_setup() first")
    sandbox = os.path.join(roots[0], SANDBOX_DIR, stamp)
    ctx = Ctx(H, sandbox, report_dir, unattended)
    _set_progress(step="before starting: FL, the open song (saved by FL), popups", pct=0)
    try:
        pre = _preflight(ctx, user)
    except UserMustAct as e:
        return _finish(rep, report_dir, T0, stop=str(e))
    except Exception as e:
        return _finish(rep, report_dir, T0, stop=f"couldn't check FL before starting: {type(e).__name__}: {e}")
    pre["original"] = user_song(pre.get("original"))
    if isinstance(user, dict) and "original" not in user:
        user["original"] = pre["original"]
    ctx.original, ctx.pids = pre["original"], pre["pids"]
    rep["your_song"] = pre["original"] or ("a new empty project" + (
        f" ({H['_title_stem'](pre['title'])})" if H["_title_stem"](pre.get("title")) else ""))
    if pre.get("on_sandbox"):
        rep["notes"].append(f"FL showed '{_stem_of(pre['on_sandbox'])}' from an earlier run's sandbox at the start (not "
                            "a song of yours): FL is left on an empty project at the end")
        old = _sandbox_root_of(pre["on_sandbox"])
        if old and _key(old) != _key(sandbox):
            _note_leftover(old)                                   # recycled once FL has left it
    H["writable"](os.path.join(sandbox, "x.flp"))
    os.makedirs(sandbox, exist_ok=True)
    rep["sandbox"] = sandbox
    H["_state"]({"pc_check_run": {"original": pre["original"], "sandbox": sandbox, "started": rep["started"],
                                  "report": report_dir}})
    fl_gone = None; stop = None
    snap, own = settings_save("pc_check")
    prof = contextlib.ExitStack()
    try:                                                          # finals / previews of sandbox songs stay inside it
        prof.enter_context(sandbox_profile(sandbox))
    except Exception as e:
        stop = (f"couldn't switch to the sandbox profile ({type(e).__name__}: {e}), so test finals could land in your "
                "collection's final folder: nothing was run")
    try:
        n = len(sel)
        for i, c in enumerate(sel):
            row = {"id": c["id"], "title": c["title"], "area": c["area"], "risk": c["risk"]}
            if _RUN["stop"].is_set():
                rep["checks"].append(dict(row, status="skip", detail="stopped before it ran")); continue
            if fl_gone or stop:
                rep["checks"].append(dict(row, status="skip", detail="not run: " + (
                    "FL closed during the run" if fl_gone else "the run stopped early"))); continue
            if c["needs"] & {"fl", "bridge"} and not _fl_alive(ctx.pids):
                fl_gone = f"FL wasn't running any more before '{c['id']}'"
                rep["checks"].append(dict(row, status="skip", detail=fl_gone)); continue
            if "bridge" in c["needs"] and ctx.info.get("bridge_ok") is False:
                rep["checks"].append(dict(row, status="skip", detail="FL's bridge doesn't answer (bridge.ping)"))
                continue
            if c["needs"] & PERSON and unattended:
                rep["checks"].append(dict(row, status="skip", detail="needs you (unattended run): " +
                                          (_user_action(c) or "a hand step"),
                                          next_step=f"fl_pc_check(areas=['{c['id']}']) when you're at the PC"))
                continue
            _set_progress(step=f"check {i + 1}/{n}: {c['title']}", check=c["id"], pct=round(100 * i / max(1, n)),
                          detail=None)
            ctx.check = c["id"]; ctx._expired = False
            t = time.time(); limit = max(MIN_DEADLINE_S, float(c["minutes"]) * 60 * 4)
            timer = threading.Timer(limit + 30, lambda cid=c["id"]: _set_progress(
                detail=f"{cid} is over its deadline: waiting for the current FL step to return"))
            timer.daemon = True; timer.start()
            res = None
            try:
                with ctx.deadline(limit):
                    res = c["run"](ctx)
                if not isinstance(res, dict) or res.get("status") not in STATUSES:
                    raise ValueError(f"check returned {str(res)[:120]}")
            except Stopped:
                res = {"status": "skip", "detail": "stopped while it ran (fl_pc_check(stop=true))"}
            except UserMustAct as e:
                res = {"status": "skip", "detail": f"needs you first: {e}", "stop_run": str(e)}
            except FLLoadFailed as e:
                ctx.keep_sandbox = True
                res = {"status": "fail", "detail": str(e), "fix_hint": "the .flp fl-headless wrote doesn't load in "
                       "this FL: compare it with a file FL saved (flp_diff)", "stop_run": (
                           f"stopped after '{c['id']}': {e}. The other checks need FL to open test songs. FL now "
                           "holds a half-loaded untitled project: reopen your song (File > Open, answer No to "
                           "'save changes to untitled'). Send the bug report.")}
            except TimeoutError as e:
                res = {"status": "fail", "detail": f"timed out after {time.time() - t:.0f} s: {e}",
                       "fix_hint": "a step hung or FL was slow; see the traceback", "traceback":
                       traceback.format_exc()[-1500:]}
            except Exception as e:
                res = {"status": "fail", "detail": f"{type(e).__name__}: {str(e)[:300]}",
                       "traceback": traceback.format_exc()[-2000:]}
                try:
                    H["_note_tool_error"](f"fl_pc_check:{c['id']}", e, traceback.format_exc())
                except Exception:
                    pass
            finally:
                timer.cancel()
            row.update(res); row["seconds"] = round(time.time() - t, 1)
            if ctx.notes:
                row.setdefault("notes", []).extend(ctx.notes); ctx.notes = []
            if c["needs"] & {"fl", "bridge"}:
                if not _fl_alive(ctx.pids):
                    fl_gone = f"FL closed or crashed during '{c['id']}'"
                    row["status"] = "fail"; row["detail"] = f"{fl_gone}. " + str(row.get("detail") or "")
                else:
                    pops = _answer_popups(ctx, c["id"])
                    if pops:
                        row["popups_left_and_cancelled"] = pops
            rep["checks"].append(row)
            if res.get("stop_run"):
                stop = res["stop_run"]
            if _RUN["stop"].is_set() and not stop:
                stop = "stopped with fl_pc_check(stop=true)"
    finally:
        _set_progress(step="cleaning up: your song back in FL, sandbox to the Recycle Bin", pct=99, check=None,
                      detail=None)
        errs = ctx.run_cleanups()
        if errs:
            rep["notes"].append(f"cleanup errors: {errs}")
        _forget_sandbox(sandbox)
        rep["restored"] = _restore(ctx, pre, fl_gone)
        try:
            prof.close()                                          # the user's collection back
        except Exception as e:                                    # noqa: BLE001
            rep["notes"].append(f"couldn't put your collection back: {type(e).__name__}: {e}")
        finish_sandbox(rep, sandbox, keep_sandbox or getattr(ctx, "keep_sandbox", False), pre.get("original"))
        H["_state"]({"pc_check_run": None})
        try:                                                      # the user's live notes / reload mode as they were
            back = settings_restore(snap, clear=own)
            if back:
                rep["settings_restored"] = back
        except Exception as e:                                    # noqa: BLE001
            rep["notes"].append(f"couldn't put your live notes / reload mode back: {type(e).__name__}: {e}")
    if fl_gone:
        stop = (f"{fl_gone}. Start FL again; your song was saved by FL before the check began"
                + (f" ({pre['original']})" if pre["original"] else "") + ". Send the bug report.")
    return _finish(rep, report_dir, T0, stop=stop)


def _restore(ctx, pre, fl_gone, relaunch=False):
    if (fl_gone or not H["fl_running"]()) and not relaunch:       # the check: FL closing is the finding (a crash?)
        return {"ok": False, "why": "FL isn't running"}
    if fl_gone or not H["fl_running"]():                          # the bench: FL closed during the run (a command-
        if not pre.get("original"):                               # that didn't reopen, a crash): start it ONCE with
            return {"ok": False, "why": "FL isn't running"}       # the user's song - no FL runs, so no 2nd copy
        try:
            H["_flh_call"]("fl_open", {"project": pre["original"]})
            _wait_loaded(pre["original"], 240)
            return {"ok": True, "reopened": pre["original"], "note": "FL had closed during the run; started again "
                                                                     "with your song"}
        except Exception as e:
            return {"ok": False, "why": f"FL isn't running and couldn't be started again: {str(e)[:160]}"}
    if not ctx.opened and not pre.get("on_sandbox"):
        return {"ok": True, "note": "FL kept your song open the whole time"}
    cancelled, stuck = _clear_stuck_popup(ctx)                    # FL ignores File > Open behind a file dialog
    if stuck:
        return {"ok": False, "popup": stuck, "why": f"FL is showing the file dialog {stuck}",
                "note": f"close {stuck} in FL, then reopen your song (File > Open"
                        + (f": {pre['original']}" if pre.get("original") else "") + ")"}
    out = {"cancelled_popup": cancelled} if cancelled else {}
    r = _restore_open(ctx, pre)
    return {**r, **out}


TAKE_WORDS = ("edison",)                                          # a Save As naming these is a recorded take's


def _clear_stuck_popup(ctx):
    """Before FL gets the user's song back: the popup FL is stuck on, if any. A Save As for an audio take that came
    up during the run (Edison asking where to save the vocal check's recording, PC 2026-10-23) is cancelled: it's
    the sandbox's take, nothing of the user's. Another FILE dialog is named so the user knows what to close; other
    popups are left to fl_open (it answers leftovers). Returns (cancelled title or None, stuck words or None)."""
    try:
        d = H["_fl_blocking_popup"]()
    except Exception:
        return None, None
    if not d:
        return None, None
    words = H["_popup_words"](d)
    filedlg = bool(d.get("file_dialog")) or d.get("kind") in ("save_as", "open_dialog")
    texts = [str(d.get("title") or "")] + [str(t) for t in d.get("text") or []]
    take = (d.get("kind") == "save_as" or (d.get("file_dialog") and d.get("kind") != "open_dialog")) and \
        (any(H["AUDIO_FILE_RE"].search(t) for t in texts) or any(w in " ".join(texts).lower() for w in TAKE_WORDS))
    if take and d.get("hwnd") not in (getattr(ctx, "dialogs_at_start", None) or set()):
        try:
            ctx.snapshot("take_save_as_before_restore")
        except Exception:
            pass
        for how in ("cancel", "escape"):
            try:
                H["fl_dialog"]("click", how, d.get("hwnd"))
            except Exception:
                continue
            t0 = time.time()
            while time.time() - t0 < 3.0:
                try:
                    now = H["_fl_blocking_popup"]()
                except Exception:
                    now = None
                if not now or now.get("hwnd") != d.get("hwnd"):
                    return words, None
                time.sleep(0.2)
        return None, words
    return None, (words if filedlg else None)


RESTORE_WAIT_S = 120.0                                            # FL loading the person's song back (scaled per PC)
RESTORE_SETTLE_S = 3.0                                            # FL busy finishing the sandbox's save first
RESTORE_SWITCH_S = 20.0                                           # FL's title leaving the sandbox song (scaled)


def _restore_wait(path, timeout_s, title_before=None):
    """Wait until FL shows `path` loaded -> (loaded, why). When FL's title still shows the song it had before the
    open after RESTORE_SWITCH_S (scaled) and FL isn't busy (saving / loading), the open didn't take: give up early so
    the caller can retry. Never FLLoadFailed for the person's song: an FL error box then is a plugin's (e.g. Edison
    refusing a file), not this song's - it's dismissed and the title decides."""
    t0 = time.time(); names = _names(path)
    switch_s = min(float(timeout_s), H["_scaled"](RESTORE_SWITCH_S))
    if title_before is not None:
        while (H["_title_stem"](_title()) or "").lower() not in names and _title() == title_before:
            if _RUN["stop"].is_set():
                raise Stopped("stopped by fl_pc_check(stop=true)")
            if time.time() - t0 >= switch_s:
                try:
                    busy = H["_fl_still_working"]()
                except Exception:
                    busy = False
                if not busy:
                    return False, f"FL's title stayed '{title_before}' for {time.time() - t0:.0f} s (the open didn't take)"
                switch_s = min(float(timeout_s), switch_s + H["_scaled"](RESTORE_SWITCH_S))
            time.sleep(0.3)
    try:
        _wait_loaded(path, max(5.0, float(timeout_s) - (time.time() - t0)))
        return True, None
    except FLLoadFailed:
        return False, "FL showed an error box while switching (dismissed)"
    except Stopped:
        raise
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def _restore_open(ctx, pre):
    try:
        if user_song(pre.get("original")):                        # (never a sandbox file: see user_song)
            ctx._deadline = None; ctx._expired = False
            orig = pre["original"]; names = H["_project_names"](orig)
            tries = []
            # PC 2026-10-23: the first fl_open of the person's song right after the vocal check (Edison open in the
            # sandbox song) left FL on the sandbox, while the same fl_open seconds later worked: settle, try, and
            # once more after answering / closing what's in the way - the same fl_open path both times
            for attempt in (1, 2):
                row = {"try": attempt}
                if attempt == 2:
                    cancelled, stuck = _clear_stuck_popup(ctx)
                    if cancelled:
                        row["cancelled_popup"] = cancelled
                    if stuck:
                        row["popup"] = stuck
                    try:
                        row["popups_closed"] = [p.get("title") for p in _answer_popups(ctx, "restore")] or None
                    except Exception:
                        pass
                    time.sleep(H["_scaled"](RESTORE_SETTLE_S))
                t0 = time.time(); before = _title()
                try:
                    r = H["_flh_call"]("fl_open", {"project": orig})
                    if isinstance(r, dict) and r.get("note"):
                        row["note"] = str(r["note"])[:160]
                except Exception as e:
                    row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                if "error" not in row:
                    loaded, why = _restore_wait(orig, min(300.0, H["_scaled"](RESTORE_WAIT_S)), before)
                    if why:
                        row["wait"] = why
                else:
                    loaded = False
                row["s"] = round(time.time() - t0, 1)
                tries.append({k: v for k, v in row.items() if v is not None})
                if loaded or (H["_title_stem"](_title()) or "").lower() in names:
                    break
            try:
                H["BRIDGE"].call("transport", action="stop", timeout=3.0)
            except Exception:
                pass
            ok = (H["_title_stem"](_title()) or "").lower() in names
            if ok:                                                # FL's title can be stale after a failed load:
                try:                                              # FL's own save proves which file it holds
                    got = H["_prove_open"](orig)
                    ok = isinstance(got, str) and got != "never_saved" and _key(got) == _key(orig)
                except Exception:
                    pass
            out = {"ok": bool(ok), "reopened": orig}
            if len(tries) > 1 or not ok:
                out["tries"] = tries
            if not ok:
                try:
                    ctx.snapshot("restore_failed")
                except Exception:
                    pass
                out.update(title_now=_title(), note="reopen your song in FL (File > Open; answer No to 'save changes "
                                                    "to untitled' if FL asks)")
            return out
        discard = [p for p in list(ctx.opened) + list(getattr(ctx, "made", []) or []) + [pre.get("on_sandbox")]
                   if p and in_sandbox(p)]
        r = _new_empty_project(discard)
        if not r.get("ok"):
            r["note"] = "FL still shows a sandbox song: use File > New in FL (the sandbox is kept until then)"
        return r
    except Exception as e:
        return {"ok": False, "why": f"{type(e).__name__}: {str(e)[:200]}"}


def _next_steps(rep):
    out = []
    if rep.get("stopped"):
        out.append(rep["stopped"])
    for c in rep["checks"]:
        if c.get("next_step") and c.get("status") in ("fail", "warn", "skip"):
            out.append(f"{c['id']}: {c['next_step']}")
    looks = [c for c in rep["checks"] if c.get("look")]
    for c in looks:
        out.append(f"look: {c['id']} -> {c['look']}")
    if any(c.get("status") == "fail" for c in rep["checks"]):
        out.append("send the bug report zip" + (f" ({rep['bug_report']})" if rep.get("bug_report") else "") +
                   " and report.md with the failing check ids")
    if not rep.get("restored", {}).get("ok", True):
        out.append("FL wasn't put back on your song: " + str(rep["restored"].get("note") or rep["restored"].get("why")))
    if _sandbox_note(rep):
        out.append(_sandbox_note(rep))
    if not out:
        out.append("everything automatic passed: tell the developer 'fl_pc_check all pass' with report.md")
    return out


def _finish(rep, report_dir, T0, stop=None):
    rep["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rep["minutes"] = round((time.time() - T0) / 60, 1)
    if stop:
        rep["stopped"] = stop
    cnt = {s: 0 for s in STATUSES}
    for c in rep["checks"]:
        cnt[c.get("status", "fail")] = cnt.get(c.get("status", "fail"), 0) + 1
    rep["summary"] = cnt
    if cnt["fail"]:
        try:
            b = H["flh_bug_report"]()
            rep["bug_report"] = b.get("zip") if isinstance(b, dict) else None
        except Exception as e:
            rep["notes"].append(f"bug report failed: {e}")
    rep["next_steps"] = _next_steps(rep)
    jp = os.path.join(report_dir, "report.json"); mp = os.path.join(report_dir, "report.md")
    try:
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, default=str)
        with open(mp, "w", encoding="utf-8") as f:
            f.write(_markdown(rep))
    except OSError as e:
        rep["notes"].append(f"report not written: {e}")
    try:
        H["_state"]({"pc_check_last": jp})
    except Exception:
        pass
    _prune_reports(os.path.dirname(report_dir))
    _RUN["last"] = jp
    return _compact(rep, jp, mp)


def _compact(rep, jp, mp):
    keep = ("id", "status", "detail", "seconds", "look", "next_step", "checklist")
    shown = [{k: c[k] for k in keep if c.get(k) is not None} for c in rep["checks"] if c.get("status") != "pass"]
    out = {"summary": rep["summary"], "minutes": rep["minutes"], "not_passed": shown,
           "passed": [c["id"] for c in rep["checks"] if c.get("status") == "pass"],
           "next_steps": rep["next_steps"], "report": jp, "report_md": mp}
    for k in ("stopped", "bug_report", "restored", "sandbox_kept", "sandbox_not_recycled", "earlier_sandboxes",
              "recovered_previous_run"):
        if rep.get(k):
            out[k] = rep[k]
    if rep.get("notes"):
        out["notes"] = rep["notes"][:5]
    return out


def _markdown(rep):
    s = rep["summary"]
    lines = [f"# fl_pc_check {rep['started']} - fl-headless {rep['fl_headless']}", "",
             f"**{s['pass']} pass, {s['fail']} fail, {s['warn']} warn, {s['skip']} skip** in {rep['minutes']} min."
             + (f" Stopped: {rep['stopped']}" if rep.get("stopped") else ""), "",
             "| check | status | s | detail |", "|---|---|---|---|"]
    for c in rep["checks"]:
        det = str(c.get("detail") or "").replace("|", "/").replace("\n", " ")[:220]
        lines.append(f"| {c['id']} | {c.get('status')} | {c.get('seconds', '')} | {det} |")
    lines += ["", "## Next steps"] + [f"- {x}" for x in rep.get("next_steps") or []]
    if rep.get("bug_report"):
        lines += ["", f"Bug report: `{rep['bug_report']}`"]
    return "\n".join(lines) + "\n"


# ============================================================================= the tool
def fl_pc_check(areas=None, skip=None, unattended=False, max_risk=None, keep_sandbox=False, dry_run=False, stop=False,
                answer=None, status=False, full=False, report=None):
    """The automatic real-PC check (see the module docstring). Starts a background job; fl_job(<job>) shows
    'check i/n: ...' and needs_you when a check waits for the person."""
    if stop:
        if not _RUN["running"]:
            return {"stopped": False, "note": "no check is running"}
        _RUN["stop"].set(); _RUN["answer_ev"].set()
        return {"stopping": _RUN["job"], "note": "the running check finishes its current FL step, then FL gets your "
                                                 "song back and the sandbox is recycled: fl_job(<job>) for the report"}
    if answer is not None:
        if not _RUN["ask"]:
            return {"answered": False, "note": "no check is waiting for an answer"}
        _RUN["answer"] = str(answer); _RUN["answer_ev"].set()
        return {"answered": True, "to": _RUN["ask"], "job": _RUN["job"]}
    if status:
        if _RUN["running"]:
            j = _jobs().get(_RUN["job"], {})
            return {"running": True, "job": _RUN["job"], "progress": j.get("progress"), "waiting_for_you": _RUN["ask"]}
        last = report or _RUN.get("last") or H["_state"]().get("pc_check_last")
        if report and not areas:                                  # an earlier report: its not-passed rows
            try:
                with open(report, encoding="utf-8") as f:
                    rows = json.load(f).get("checks") or []
            except Exception as e:
                return {"running": False, "report": report, "rows_error": f"{type(e).__name__}: {e}"}
            return {"running": False, "report": report,
                    "checks": [{k: v for k, v in r.items() if k != "traceback"} for r in rows
                               if r.get("status") not in ("pass", "skip")]}
        if areas:                                                 # status + areas: those checks' full rows
            want = {areas} if isinstance(areas, str) else set(areas)
            path = last.get("report") if isinstance(last, dict) else last
            try:
                with open(path, encoding="utf-8") as f:
                    rows = json.load(f).get("checks") or []
            except Exception as e:
                return {"running": False, "last_report": last, "rows_error": f"{type(e).__name__}: {e}"}
            return {"running": False, "report": path,
                    "checks": [r for r in rows if r.get("id") in want or r.get("area") in want]}
        return {"running": False, "last_report": last}
    sel, err = plan(areas, skip, max_risk, full)
    if dry_run:
        return _dry_run(sel, err, unattended)
    if not H["IS_WINDOWS"]:
        raise RuntimeError("fl_pc_check runs on the Windows PC with FL Studio; dry_run=true shows the plan anywhere")
    sw = _sweep_running()
    if sw:
        return {"started": False, "note": f"fl_sweep is running ({sw}): it runs fl_pc_check itself; "
                                          "fl_sweep(status=true) shows where it is"}
    first = _quick_precheck()
    if first:
        return {"started": False, "you_first": first, "then": "fl_pc_check() again (dry_run=true shows the plan)"}
    with _RUN_LOCK:
        if _RUN["running"]:
            return {"already_running": _RUN["job"], "note": "fl_job(<job>) for progress, fl_pc_check(stop=true) to stop"}
        _RUN["running"] = True; _RUN["stop"].clear(); _RUN["ask"] = None; _RUN["answer_ev"].clear()

    def job():
        try:
            return _run(sel, err, unattended, keep_sandbox)
        finally:
            _RUN["running"] = False; _RUN["ask"] = None
    try:
        r = H["_start_job"]("pccheck", job, fl=True)
    except Exception:
        _RUN["running"] = False
        raise
    _RUN["job"] = r["job"]
    auto = round(sum(_expect(c) for c in sel if not _user_action(c)), 1)
    you = round(sum(_expect(c) for c in sel if _user_action(c)), 1)
    return dict(r, checks=len(sel), estimated_minutes={"automatic": auto, "with_you": 0 if unattended else you},
                note=f"don't use FL meanwhile; fl_job('{r['job']}') shows 'check i/n'. When its progress shows "
                     "needs_you, tell the user and answer with fl_pc_check(answer='done'). Stop: "
                     "fl_pc_check(stop=true).")


def _boot_restore_mode():
    """A pc_check cut off inside the lazy check (crash / server restart) left FL edits in its reload mode: the user's
    own comes back when the server starts (state only; FL and the sandbox wait for the next fl_pc_check)."""
    try:
        _settings_recover()                                       # live notes / reload mode from before a cut-off run
    except Exception:                                             # noqa: BLE001
        pass
    st = H["_state"]() or {}
    run = st.get("pc_check_run")
    if not isinstance(run, dict) or not run.get("reload_mode_saved") or _RUN.get("running"):
        return None
    if any(str(j.get("job") or "").startswith(("pccheck-", "sweep-")) and j.get("status") == "running"
           for j in list((H.get("JOBS") or {}).values())):
        return None
    H["_state"]({"reload_mode": run.get("reload_mode_before"),
                 "pc_check_run": {k: v for k, v in run.items() if not k.startswith("reload_mode_")}})
    return run.get("reload_mode_before") or "default"


def register(ns):
    global H
    H = ns
    try:
        _boot_restore_mode()
    except Exception:                                             # noqa: BLE001
        pass
    try:
        collection_recover()                                      # a crash inside a run: the user's collection back
    except Exception:                                             # noqa: BLE001
        pass
    S, P = ns["_schema"], ns["_p"]
    BOOL, INT = ns["_BOOL"], ns["_INT"]
    ns["TOOLS"]["fl_pc_check"] = (
        fl_pc_check,
        "Automatic check of fl-headless on this PC with FL Studio open (the developer's PC-VERIFY items): about 20 "
        "minutes in the background on SANDBOX copies, riskiest first - user data folder, bridge, which file FL has "
        "open, in-FL render, live notes, lazy reloads, file-format details FL must accept, backups, Recycle Bin, plus "
        "the module checks. FL's song is saved by FL first and reopened at the end; the sandbox goes to the Recycle "
        "Bin. Returns a job: fl_job(job) shows 'check i/n' and needs_you prompts (answer with answer='done' or "
        "'skip'); stop=true stops; status=true shows the run or the last report. dry_run=true: the plan with minutes "
        "and what the user must do. Result: summary, failed/warn checks, bug report zip, next steps, report files.",
        S(areas=P(["array", "string"], "only these areas / check ids (core, render, live_notes, lazy, bridge, files, "
                  "vocal, midi, mix, ...)", True, items={"type": "string"}),
          skip=P(["array", "string"], "areas / check ids to leave out", True, items={"type": "string"}),
          unattended=BOOL("skip every check that needs the person (default false)", True),
          max_risk=INT("only checks with risk <= this (1-5)", True),
          keep_sandbox=BOOL("keep the sandbox folder instead of recycling it", True),
          dry_run=BOOL("only the plan: checks, minutes, what the user must do", True),
          stop=BOOL("stop the running check (cleans up, reopens the song)", True),
          answer=ns["_STR"]("answer a needs_you prompt: 'done' or 'skip'", True),
          status=BOOL("the running check's progress, or the last report", True),
          full=BOOL("every check (default: the quick run - the important checks, risk 3+)", True),
          report=ns["_STR"]("status: read this report.json instead of the last one (its failed rows with evidence)",
                            True)))
    ns.setdefault("CROSS_PLATFORM", set()).add("fl_pc_check")     # dry_run anywhere; the run itself checks Windows
    try:
        ns["TOOL_GROUPS"]["fl_pc_check"] = "start"
    except Exception:
        pass
