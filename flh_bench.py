"""flh_bench: fl_bench - fl-headless's speed on THIS PC, measured with a fixed set of jobs, so before/after numbers
(a new version, a setting, another PC) are real instead of guessed.

Jobs (always run in this order; jobs=[...] picks some; each works on SANDBOX songs only):
  beat       flh_new_song(genre='trap', balance=True) until its background level balance has finished
  rework_34  a 16-bar 3/4 piano piece (chords + melody, ~60 BPM, a tempo change; built with the server's own
             writers on FL's empty template) -> flp_compose layers (low strings, strings, choir; bars='all') ->
             flp_perform (when the module is there) -> flp_extend_ending -> fl_finalize with ending rules; did the
             rules pass on the first render?
  ending     fl_ending_check's prediction (from the notes, no render) against the measured result of the render
             (rework_34's final when it ran, else its own extended copy + fl_finalize)
  mix_tweak  a fader move + fl_preview twice (the second must be reused / mixed offline: 0 renders)
  knob       fl_sound_shape(knobs={'threshold': '-36 dB'}) on a compressor-like effect of the beat (one is added to a
             sandbox track when the song has none); read back from FL: did the value land?
Each job: status pass | fail | skip with the reason, seconds (the measured steps; setup such as building or opening
a song is reported apart as setup_s), renders (FL exports, from the server's render counters - also those of the
background balance), reused / offline previews, steps_s, notes.

Safety (as fl_pc_check, whose helpers it reuses): the song FL has open is saved by FL first and reopened at the end
(never a sandbox song an earlier run left in FL: then FL gets an empty project), everything happens in <first
FLH_ROOTS folder>/_fl-headless-check/bench-<time>/ (recycled afterwards unless keep=true, only once FL has left it;
said with its path when it can't be), never a second FL, FL is closed only by fl_finalize's render path. The whole
run works under the temporary profile '_fl-headless check' and the rule jobs under '_fl-headless bench' (the user's
user-wide settings, the bench's rules, final_root inside the sandbox, no final_name / finals / songs / taste), so
finals and previews stay in the sandbox; the active collection is put back afterwards and the user's own profile
files are never written.

Results: <state folder>/bench/<time>.json (the newest KEEP_RESULTS kept) and a compact reply; compare='last' or a
file adds a before/after table (delta seconds and renders per job); compare=[a, b] compares two saved runs without
running anything. dry_run=true: the plan with the estimated minutes (any OS).

Slow mode: slow=true (or cores=N) limits FL's processes to 2 (N) CPU cores for the run (Windows CPU affinity, nothing
else changed) to approximate a budget PC; FL processes started during the run (the command-line render) get the same
mask; FL's own masks are put back at the end, on errors / stop, at server exit, and after a crash at the next server
start. Results say speed='slow (2 cores)'; compare='last_normal' / 'last_slow' picks the newest run of that kind and
a slow-vs-normal compare adds x (times slower) per job.

PC-VERIFY: the whole run is PC-only; see checklists/bench.md (the balance wait while the bench holds FL, the 3/4
fixture on FL's empty template, the compressor readback)."""
import contextlib
import glob
import json
import os
import platform
import re
import shutil
import sys
import threading
import time
import traceback

H = None                                                          # the live server namespace (register)
SANDBOX_DIR = "_fl-headless-check"                                # shared with fl_pc_check (one place to look)
BENCH_PROFILE = "_fl-headless bench"                              # the temporary collection profile
BENCH_RULES = {"min_ending_s": 20.0, "min_tail_s": 5.0}           # short enough for a quick run, real rules
KEEP_RESULTS = 30
BALANCE_WAIT_S = 300.0
FIX_SEED = 7                                                      # fixed seeds: the same songs every run
FIX_BPM = 60.0
FIX_TEMPO = [[1, 60], [9, 66], [13, 58]]                          # the tempo change of the 3/4 fixture
LAYERS = ["low strings", "strings", "choir"]
KNOB_DB = -36.0
KNOB_TOL_DB = 1.0
COMP_RE = re.compile(r"compress|limiter|maximus|\bcomp\b|dynamics", re.I)
PRED_RULES = ("ending_length", "tail_ring")                       # what fl_ending_check predicts
STATUSES = ("pass", "fail", "skip")
_RUN = {"running": False, "job": None}
_RUN_LOCK = threading.Lock()

# a 16-bar 3/4 piano piece: (chord, bass note, triad); right hand three quarter notes per bar; bars 9-12 louder
PROG = [("C", 48, [60, 64, 67]), ("C/E", 52, [60, 64, 67]), ("F", 41, [60, 65, 69]), ("G", 43, [59, 62, 67]),
        ("Am", 45, [60, 64, 69]), ("Em", 40, [59, 64, 67]), ("F", 41, [60, 65, 69]), ("G", 43, [59, 62, 67]),
        ("Am", 45, [60, 64, 69]), ("F", 41, [60, 65, 69]), ("C", 48, [60, 64, 67]), ("G", 43, [59, 62, 67]),
        ("F", 41, [60, 65, 69]), ("G", 43, [59, 62, 67]), ("C", 48, [60, 64, 67]), ("C", 36, [60, 64, 67])]
MEL = {"C": [72, 76, 79], "C/E": [76, 74, 72], "F": [77, 76, 74], "G": [74, 71, 67], "Am": [72, 76, 81],
       "Em": [71, 67, 64]}


class Skip(Exception):
    """A job can't run here (a missing module, no plugin preset): skipped with the reason."""


def _pc():
    """flh_pccheck (its Ctx, preflight, restore and sandbox cleanup are reused) -> the module, or None."""
    m = sys.modules.get("flh_pccheck")
    return m if m is not None and getattr(m, "H", None) is not None else None


def _T():
    return H["TOOLS"]


def _now():
    return time.strftime("%Y%m%d-%H%M%S")


def _state_dir():
    return os.path.dirname(H["STATE"])


def _bench_dir():
    return os.path.join(_state_dir(), "bench")


def _key(p):
    return os.path.normcase(os.path.abspath(p))


def _short(x, n=160):
    s = str(x)
    return s if len(s) <= n else s[:n - 3] + "..."


# ============================================================================= render counters
def _counts():
    """The server's session render counters (every thread: the background balance renders too)."""
    s = H.get("_RENDER_STATS") or {}
    return {"exports": int(s.get("exports") or 0), "reused": int(s.get("reused") or 0),
            "offline": int(s.get("offline") or 0)}


def _delta(c0):
    c1 = _counts()
    return {k: c1[k] - c0[k] for k in c1}


class Meter:
    """steps_s and render counts of one job: measured steps and setup steps (building / opening a song) apart."""

    def __init__(self):
        self.steps, self.setup = {}, {}
        self.r = {"exports": 0, "reused": 0, "offline": 0}
        self.setup_renders = 0
        self.last = {"exports": 0, "reused": 0, "offline": 0}
        self.notes = []

    @contextlib.contextmanager
    def step(self, name, setup=False):
        c0 = _counts(); t = time.time()
        try:
            yield
        finally:
            dt = time.time() - t; d = _delta(c0); self.last = d
            tgt = self.setup if setup else self.steps
            tgt[name] = round(tgt.get(name, 0.0) + dt, 2)
            if setup:
                self.setup_renders += d["exports"]
            else:
                for k in d:
                    self.r[k] += d[k]

    def seconds(self):
        return round(sum(self.steps.values()), 1)


# ============================================================================= FL helpers (patched in tests)
def _preflight(ctx, user=None):
    return _pc()._preflight(ctx, user)


def _restore(ctx, pre, fl_gone):
    return _pc()._restore(ctx, pre, fl_gone, relaunch=True)    # FL closed (a command-line render): start it with
                                                               # the user's song (no FL runs: no second copy)


def _open(ctx, path):
    """Open a sandbox song in FL and wait until it's loaded (fl_pc_check's open_in_fl)."""
    return ctx.open_in_fl(path)


def _fl_up():
    try:
        return bool(H["fl_running"]())
    except Exception:
        return False


def _progress(**f):
    try:
        H["_job_progress"](**{k: v for k, v in f.items() if v is not None})
    except Exception:
        pass


def _yield_fl(wait_fn):
    """Let go of the FL lock this job holds while wait_fn runs (the background balance needs it), then take it back.
    PC-VERIFY: a client tool call in that window can use FL (the bench says so in its notes)."""
    L = H.get("_FL_LOCK"); n = 0
    hold = H.get("_FL_HOLDER") if isinstance(H.get("_FL_HOLDER"), dict) else {}
    what = hold.get("what")
    if L is not None:
        while n < 64:
            try:
                L.release(); n += 1
            except RuntimeError:                                  # not (or no longer) held by this thread
                break
    try:
        return wait_fn()
    finally:
        for _ in range(n):
            L.acquire()
        if n and hold is not None:
            hold["what"] = what


@contextlib.contextmanager
def _bench_profile(ctx):
    """The bench's collection profile active for the block (flh_pccheck.sandbox_profile): the user's user-wide
    settings (edition, owned plugins ...) with the bench's ending rules, final_root inside the sandbox, no final_name /
    finals / songs / taste (so finals and their previews stay in the sandbox). The previous active collection comes
    back afterwards; the user's own profile files are only read."""
    with _pc().sandbox_profile(ctx.sandbox, BENCH_PROFILE, BENCH_RULES):
        yield dict(BENCH_RULES)


# ============================================================================= songs the jobs share
def _sbdir(ctx, *parts):
    p = os.path.join(ctx.sandbox, *parts)
    H["writable"](os.path.join(p, "x.flp"))
    os.makedirs(p, exist_ok=True)
    return p


def _new_song(ctx, label, genre, **kw):
    args = dict(genre=genre, name=label, folder=_sbdir(ctx, "songs"), seed=FIX_SEED, open=False, balance=False)
    args.update(kw)
    r = ctx.call("flh_new_song", **args)
    p = (r or {}).get("project") if isinstance(r, dict) else None
    if not p or not os.path.isfile(p):
        raise RuntimeError(f"flh_new_song made no project: {_short(r, 200)}")
    if not _pc()._inside(p, ctx.sandbox):
        raise RuntimeError(f"the bench song landed outside the sandbox: {p}")
    ctx.made.append(p)
    return p, r


def _ensure_open(ctx, st, M, path):
    if st.get("fl_holds") == _key(path):
        return
    with M.step("open_in_fl", setup=True):
        _open(ctx, path)
    if path not in ctx.opened:
        ctx.opened.append(path)                                   # FL holds a sandbox song: restore at the end
    st["fl_holds"] = _key(path)


def _ensure_beat(ctx, st, M):
    """The beat song (the beat job's, else a trap song without the balance) open in FL -> its path."""
    p = st.get("beat")
    if not p or not os.path.isfile(p):
        with M.step("make_song", setup=True):
            p, _ = _new_song(ctx, "Bench Trap", "trap")
        st["beat"] = p
        M.notes.append("the beat job didn't run first: a trap song without the level balance was made for this job")
    _ensure_open(ctx, st, M, p)
    return p


def _journal(path):
    try:
        return sys.modules["flh_store"].journal_get(path)
    except Exception:
        return {}


def _keys_channel(path, res):
    """The fixture's piano: the journal's keys / chords / lead / pad channel, else the first channel."""
    j = _journal(path)
    chans = j.get("channels") or {}
    for role in ("keys", "piano", "chords", "lead", "pad", "melody"):
        for k, v in chans.items():
            if str(k).isdigit() and str((v or {}).get("role") or "").lower() == role:
                return int(k)
    for line in (res or {}).get("channels") or []:
        mm = re.match(r"^(\d+) ", str(line))
        if mm and any(w in str(line).lower() for w in ("piano", "keys", "rhodes", "e.piano")):
            return int(mm.group(1))
    try:
        lst = H["flp_list"](path).get("channels") or []
        if lst:
            return int(lst[0]["index"])
    except Exception:
        pass
    return None


def _fixture_rows(ch):
    rows = []
    for b, (nm, bass, tri) in enumerate(PROG):
        lv, rv = (92, 104) if 8 <= b < 12 else (56, 64)
        t0 = b * 3
        if b == len(PROG) - 1:                                    # the last chord rings two beats
            rows += [[ch, t0, 2.0, bass, lv]] + [[ch, t0, 2.0, p, lv] for p in tri] + [[ch, t0, 2.0, 72, rv]]
            continue
        rows.append([ch, t0, 1.0, bass, lv])
        rows += [[ch, t0 + beat, 0.9, p, lv - 6] for beat in (1, 2) for p in tri]
        rows += [[ch, t0 + i, 0.95, p, rv] for i, p in enumerate(MEL[nm])]
    return rows


def _fixture(ctx, st, M):
    """The clean 3/4 piano piece (built once per run, never opened or changed afterwards) -> its path.
    PC-VERIFY: the cinematic template's keys channel plays the notes (an audible piano-ish sound)."""
    if st.get("fixture") and os.path.isfile(st["fixture"]):
        return st["fixture"]
    with M.step("build_fixture", setup=True):
        p, res = _new_song(ctx, "Bench Waltz", "cinematic", starter=False, arrange=False, bpm=FIX_BPM)
        ch = _keys_channel(p, res)
        if ch is None:
            raise Skip("the fixture song has no instrument channel to play the piano part")
        try:
            clips = [c["id"] for c in (ctx.call("flp_playlist", project=p).get("clips") or []) if "id" in c]
            if clips:
                ctx.call("flp_playlist", project=p, action="remove", clips=clips)
        except Exception as e:
            M.notes.append(f"fixture: the template's section clips stayed ({_short(e, 80)})")
        ctx.call("flp_project_info", path=p, output=p, time_sig_num=3, time_sig_beat=4)
        ctx.call("flp_edit", project=p, ops=[{"tool": "flp_compose_write", "pattern": "Piano", "track": 1,
                                              "notes": _fixture_rows(ch), "place": [[1, len(PROG)]]}])
        ctx.call("flp_tempo_write", project=p, output=p, points=FIX_TEMPO)
    st["fixture"] = p
    st["fixture_channel"] = ch
    return p


def _copy_song(ctx, src, label):
    """A copy of a sandbox song (its .flp and its journal) under a new name in its own folder -> the new path."""
    d = _sbdir(ctx, "songs", label)
    dst = os.path.join(d, label + ".flp")
    H["writable"](dst)
    shutil.copyfile(src, dst)
    js = os.path.splitext(src)[0] + ".journal.json"
    if os.path.isfile(js):
        jd = os.path.splitext(dst)[0] + ".journal.json"
        H["writable"](jd)
        with open(js, encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict):
            j["song"] = label
        with open(jd, "w", encoding="utf-8") as f:
            json.dump(j, f, indent=1)
    ctx.made.append(dst)
    return dst


def _final_rules(fin):
    """fl_finalize's result -> (the final's rule check or None, the final item)."""
    items = (fin or {}).get("finals") or []
    it = items[0] if items else {}
    return (it.get("rules") if isinstance(it, dict) else None), it


def _failed_rules(rules):
    return [c.get("rule") for c in (rules or {}).get("checks") or [] if c.get("pass") is False]


# ============================================================================= the jobs
def job_beat(ctx, st, M):
    name = "Bench Trap"
    news = H.get("_AUTOSYNC_NEWS")
    news0 = list(news) if isinstance(news, list) else []
    before = {t for t in threading.enumerate() if t.name == "flh-balance"}
    with M.step("new_song"):
        p, r = _new_song(ctx, name, "trap", open=True, balance=True)
    ctx.opened.append(p)                                          # FL holds a sandbox song now
    st["beat"] = p; st["fl_holds"] = _key(p)
    if isinstance(r.get("steps_s"), dict):
        M.notes.append("flh_new_song steps_s: " + json.dumps(r["steps_s"])[:200])
    # PC-VERIFY: the balance thread starts (flh_newsong names it 'flh-balance') and gets FL while the bench waits
    new = [t for t in threading.enumerate() if t.name == "flh-balance" and t not in before]
    if not new:
        return {"status": "fail", "detail": "the background level balance didn't start: " +
                _short(r.get("levels") or "no balance thread and no 'levels' line", 200)}

    def wait():
        t_end = time.time() + BALANCE_WAIT_S
        for t in new:
            while t.is_alive() and time.time() < t_end:
                t.join(1.0)
                ctx.say(f"waiting for the level balance ({time.time() - (t_end - BALANCE_WAIT_S):.0f} s)")
        return not any(t.is_alive() for t in new)
    with M.step("balance"):
        done = _yield_fl(wait)
    M.notes.append("the bench let go of FL while the balance ran (a tool call meanwhile would have been timed too)")
    base = os.path.basename(p)
    lines = [x for x in (list(news) if isinstance(news, list) else []) if x not in news0 and base in str(x)]
    line = next((x for x in lines if "balanc" in str(x)), None)
    out = {"song": p, "balance_news": _short(line, 200) if line else None}
    if not done:
        return dict(out, status="fail", detail=f"the level balance was still running after {BALANCE_WAIT_S:.0f} s")
    if line and "stopped" in str(line):
        return dict(out, status="fail", detail="the level balance stopped: " + _short(line, 160))
    return dict(out, status="pass", detail=f"new trap beat built, opened and balanced in {M.seconds():g} s"
                + ("" if line else " (no news line seen: another call may have taken it)"))


def job_rework(ctx, st, M):
    src = _fixture(ctx, st, M)
    p = _copy_song(ctx, src, "Bench Waltz rework")
    out = {"song": p}
    perr = None
    with _bench_profile(ctx) as R:
        _ensure_open(ctx, st, M, p)
        with M.step("compose_layers"):
            rc = ctx.call("flp_compose", project=p, what="layer", roles=list(LAYERS), bars="all", genre="cinematic",
                          seed=FIX_SEED)
        out["layers"] = sorted((rc or {}).get("channels") or {}) if isinstance(rc, dict) else None
        if "flp_perform" in _T() and "flp_perform_setup" in _T():
            try:
                with M.step("perform"):
                    ctx.call("flp_perform_setup", project=p, style="expressive")
                    ctx.call("flp_perform", project=p)
            except Exception as e:
                perr = f"{type(e).__name__}: {_short(e, 160)}"
                M.notes.append("flp_perform failed: " + perr)
        else:
            M.notes.append("flp_perform isn't loaded here: the performance step was left out")
        with M.step("extend_ending"):
            re_ = ctx.call("flp_extend_ending", project=p)
        out["ending"] = {k: (re_ or {}).get(k) for k in ("mode", "hold", "held_notes")}
        with M.step("finalize"):
            fin = ctx.call("fl_finalize", project=p, background=False, rules=dict(R))
    ctx.fl_restarted()                                            # the render path may have restarted FL
    st["fl_holds"] = _key(p)                                      # PC-VERIFY: fl_finalize reopens this song
    rules, item = _final_rules(fin)
    st["rework"] = {"project": p, "rules": rules}
    out.update(final=item.get("path"), render_method=(fin or {}).get("render_method"),
               final_steps_s=(fin or {}).get("steps_s"),                # where fl_finalize's time went
               rules_first_time=None if rules is None else bool(rules.get("pass")))
    if not (fin or {}).get("ok", bool(item.get("path"))):
        return dict(out, status="fail", detail="fl_finalize failed: " + _short((fin or {}).get("error") or
                                                                            item.get("error"), 200))
    if rules is None:
        return dict(out, status="fail", detail="fl_finalize didn't check the rules on the final")
    if not rules.get("pass"):
        return dict(out, status="fail", detail="the rules failed on the first render: " +
                    ", ".join(map(str, _failed_rules(rules))))
    if perr:
        return dict(out, status="fail", detail="the final passed the rules, but flp_perform failed: " + perr)
    return dict(out, status="pass", detail=f"layers + perform + ending + final in {M.seconds():g} s; the rules "
                                           "passed on the first render")


def job_ending(ctx, st, M):
    rw = st.get("rework") or {}
    own = rw.get("rules") is None or not os.path.isfile(rw.get("project") or "")
    out = {"mode": "own copy + fl_finalize" if own else "rework_34's song and final"}
    fin = None
    with _bench_profile(ctx) as R:
        if own:
            p = _copy_song(ctx, _fixture(ctx, st, M), "Bench Waltz ending")
            with M.step("extend_ending"):
                ctx.call("flp_extend_ending", project=p)
        else:
            p = rw["project"]
        with M.step("predict"):
            pr = ctx.call("fl_ending_check", project=p, rules=dict(R), render=False)
        pred_renders = M.last["exports"]
        predicted = (pr or {}).get("predicted")
        out.update(predicted=predicted, why=_short((pr or {}).get("why"), 160), need_hold_s=(pr or {}).get(
            "need_hold_s"), hold_s=(pr or {}).get("hold_s"), predict_renders=pred_renders)
        measured = None
        if predicted in (None, "borderline"):
            with M.step("check_render"):
                pm = ctx.call("fl_ending_check", project=p, rules=dict(R), render=True)
            measured = ((pm or {}).get("measured") or {}).get("pass")
            out["borderline_measured_pass"] = measured
        if own:
            _ensure_open(ctx, st, M, p)
            with M.step("finalize"):
                fin = ctx.call("fl_finalize", project=p, background=False, rules=dict(R))
            ctx.fl_restarted()
            st["fl_holds"] = _key(p)
    truth_rules = _final_rules(fin)[0] if own else rw["rules"]
    if truth_rules is None:
        return dict(out, status="fail", detail="no rule check on the final to compare with")
    rows = [c for c in truth_rules.get("checks") or [] if c.get("rule") in PRED_RULES]
    truth = all(c.get("pass") for c in rows) if rows else bool(truth_rules.get("pass"))
    out["render_pass"] = truth
    out["render_checks"] = {c.get("rule"): [c.get("value"), c.get("limit")] for c in rows}
    guess = measured if predicted in (None, "borderline") else predicted == "pass"
    out["agree"] = guess == truth
    if pred_renders:
        M.notes.append(f"the prediction rendered {pred_renders} time(s) (it should read the notes only)")
    if guess is None:
        return dict(out, status="fail", detail=f"fl_ending_check couldn't tell ({predicted}); the render "
                                               f"{'passed' if truth else 'failed'}")
    if guess != truth:
        return dict(out, status="fail", detail=f"predicted {predicted}, but the render "
                                               f"{'passed' if truth else 'failed'} the ending rules")
    return dict(out, status="pass", detail=f"predicted {predicted}" + (" (measured)" if measured is not None else "")
                + f", the render {'passed' if truth else 'failed'}: they agree")


def _state_now():
    try:
        return H["fl_state"]() or {}
    except Exception:
        return {}


def _fader_track(stt):
    for c in stt.get("channels") or []:
        try:
            if int(c.get("insert") or 0) > 0:
                return int(c["insert"])
        except (TypeError, ValueError):
            continue
    return 1


def _how(r):
    if not isinstance(r, dict):
        return None
    for k in ("reused", "source", "how", "method", "route", "mixed"):
        if r.get(k):
            return _short(f"{k}: {r[k]}", 100)
    return None


def job_mix_tweak(ctx, st, M):
    _ensure_beat(ctx, st, M)
    stt = _state_now()
    track = _fader_track(stt)
    row = next((t for t in stt.get("mixer") or [] if int(t.get("index", -1)) == track), {})
    before = row.get("volume_db")
    new = round(float(before) - 2.0, 1) if isinstance(before, (int, float)) and before > -60 else -3.0
    with M.step("fader"):
        ctx.call("fl_mixer_set", track=track, volume_db=new)
    with M.step("preview_1"):
        r1 = ctx.call("fl_preview")
    n1 = M.last
    with M.step("preview_2"):                                     # PC-VERIFY: 0 exports (reused / offline)
        r2 = ctx.call("fl_preview")
    n2 = M.last
    out = {"track": track, "volume_db": [before, new], "preview_1": {"renders": n1["exports"], "how": _how(r1)},
           "preview_2": {"renders": n2["exports"], "reused": n2["reused"], "offline": n2["offline"], "how": _how(r2)}}
    if n2["exports"]:
        return dict(out, status="fail", detail=f"the second preview of the unchanged song rendered again "
                                               f"({n2['exports']} export(s))")
    return dict(out, status="pass", detail=f"fader + 2 previews in {M.seconds():g} s, {M.r['exports']} render(s); "
                                           "the second preview was reused")


def _find_comp(stt):
    """(channel, track, slot, effect name) of a compressor-like effect on a channel's own mixer track, or None."""
    ins = {}
    for c in stt.get("channels") or []:
        try:
            t = int(c.get("insert") or 0)
        except (TypeError, ValueError):
            continue
        if t > 0:
            ins.setdefault(t, int(c["index"]))
    for t in stt.get("mixer") or []:
        ti = int(t.get("index", -1))
        if ti not in ins:
            continue
        for e in t.get("effects") or []:
            if COMP_RE.search(str(e.get("name") or "")):
                return ins[ti], ti, int(e.get("slot", 0)), str(e.get("name"))
    return None


def _comp_preset():
    N = sys.modules.get("flh_newsong")
    try:
        pre = ((N._find_plugin("Fruity Compressor") or {}).get("default")) if N else None
        if pre:
            return pre
    except Exception:
        pass
    try:
        hits = H["fl_find_presets"]("Fruity Compressor", limit=20).get("presets") or []
        return next((p for p in hits if p.lower().endswith("fruity compressor.fst")), None)
    except Exception:
        return None


def _num(text):
    mm = re.search(r"-?\d+(?:[.,]\d+)?", str(text or ""))
    return float(mm.group(0).replace(",", ".")) if mm else None


def job_knob(ctx, st, M):
    p = _ensure_beat(ctx, st, M)
    stt = _state_now()
    hit = _find_comp(stt)
    out = {}
    if not hit:
        pre = _comp_preset()
        if not pre:
            raise Skip("the beat has no compressor and Fruity Compressor's default preset wasn't found (refresh FL's "
                       "plugin database)")
        track = _fader_track(stt)
        row = next((t for t in stt.get("mixer") or [] if int(t.get("index", -1)) == track), {})
        used = {int(e.get("slot", -1)) for e in row.get("effects") or []}
        slot = next((s for s in range(10) if s not in used), None)
        if slot is None:
            raise Skip(f"mixer track {track} has no free effect slot")
        with M.step("add_compressor", setup=True):
            T = time.time()
            lr = ctx.call("flp_load_plugin", project=p, output=p, preset=pre, track=track, slot=slot) or {}
            out["load"] = {k: _short(lr.get(k), 80) for k in ("loaded", "queued", "reload", "reloaded_in_fl")
                           if k in lr}
            out["load_s"] = round(time.time() - T, 1)
            T = time.time()
            try:                                              # apply the queued (lazy) edit now: this job holds FL,
                sr = ctx.call("flh_sync") or {}               # so a merged reload would wait for it (PC 2026-10-22:
                out["sync"] = _short(sr.get("synced") or sr.get("note"), 200)   # 'one reload, 121.7 s')
            except Exception as e:
                out["sync"] = f"failed: {_short(e, 200)}"
            out["sync_s"] = round(time.time() - T, 1)
            t_end = time.time() + 60
            last = None
            while time.time() < t_end and not hit:
                last = _state_now()
                hit = _find_comp(last)
                if not hit:
                    time.sleep(0.3)
        if not hit:
            row = next((t for t in (last or {}).get("mixer") or [] if int(t.get("index", -1)) == track), None)
            out["fl_track_now"] = {"effects": [e.get("name") for e in (row or {}).get("effects") or []],
                                   "title": (last or {}).get("title")} if row else "track not in FL's state"
            try:                                                  # the song file itself: is the plugin there?
                d = H["_load"](p); ev = H["_events"](d)
                fs = H["_mixer_slots"](d, ev)
                out["file_slots"] = sorted(f"{t}.{sl}" for (t, sl), (blk, _) in fs.items() if t == track and blk is not None)
            except Exception as e:
                out["file_slots"] = f"unreadable: {_short(e, 100)}"
            diag = {k: out.get(k) for k in ("load", "load_s", "sync", "sync_s", "fl_track_now", "file_slots")}
            diag["fl_running"] = H["fl_running"]()
            return dict(out, status="fail", detail="the Fruity Compressor added to the bench beat didn't show up in "
                                                   f"FL (track {track} slot {slot}): {_short(json.dumps(diag), 600)}")
        out["added"] = f"Fruity Compressor on track {track} slot {slot}"
    ch, track, slot, name = hit
    out.update(channel=ch, plugin=name, where=f"track {track} slot {slot}")
    with M.step("set_knob"):
        r = ctx.call("fl_sound_shape", channel=ch, knobs={"threshold": f"{KNOB_DB:g} dB"}, plugin=name)
    done = (r or {}).get("set") or []
    out["set"] = [_short(x, 120) for x in done][:3] or None
    if (r or {}).get("skipped"):
        out["skipped"] = [_short(x, 120) for x in r["skipped"]][:3]
    got = None                                                    # PC-VERIFY: FL's text for the threshold ('-36.0 dB')
    with M.step("readback"):
        try:
            rp = ctx.call("fl_plugin_params", index=track, slot=slot, filter="thres")
            prm = next((x for x in (rp or {}).get("params") or [] if "thres" in str(x.get("name", "")).lower()), None)
            got = _num(prm.get("text")) if prm else None
            out["readback"] = prm.get("text") if prm else None
        except Exception as e:
            M.notes.append(f"readback failed: {_short(e, 100)}")
    if got is None:                                               # no readback: what fl_sound_shape said it set
        got = next((_num(x.split("->")[-1]) for x in done if "->" in str(x)), None)
        out["landed_from"] = "fl_sound_shape's own report (no readback)"
    landed = got is not None and abs(got - KNOB_DB) <= KNOB_TOL_DB
    out["landed"] = landed; out["value_db"] = got
    if not done:
        return dict(out, status="fail", detail=f"fl_sound_shape set nothing on {name}")
    if not landed:
        return dict(out, status="fail", detail=f"{name} threshold reads {got} dB, not {KNOB_DB:g} dB")
    return dict(out, status="pass", detail=f"{name} threshold {got:g} dB in {M.seconds():g} s")


JOBS = [
    {"id": "beat", "title": "new trap beat + background balance", "minutes": 1.5, "deadline_s": 600, "run": job_beat,
     "renders": "1-2 (the balance)"},
    {"id": "rework_34", "title": "3/4 piano rework: layers, perform, ending, final with rules", "minutes": 4.0,
     "deadline_s": 1800, "run": job_rework, "renders": "1 (the final)"},
    {"id": "ending", "title": "ending prediction vs the render", "minutes": 0.3, "minutes_alone": 3.5,
     "deadline_s": 1500, "run": job_ending, "renders": "0 with rework_34, else 1"},
    {"id": "mix_tweak", "title": "fader move + 2 previews", "minutes": 1.5, "deadline_s": 900, "run": job_mix_tweak,
     "renders": "0-1, the second preview 0"},
    {"id": "knob", "title": "compressor threshold -36 dB", "minutes": 0.5, "deadline_s": 300, "run": job_knob,
     "renders": "0"},
]
SETUP_MIN = {"start": 0.3, "fixture": 0.4, "finish": 0.5}


def _select(jobs):
    ids = [j["id"] for j in JOBS]
    if jobs in (None, "", "all", ["all"]):
        return list(JOBS)
    want = [jobs] if isinstance(jobs, str) else list(jobs)
    want = [str(w).strip().lower() for w in want if str(w).strip()]
    alias = {"rework": "rework_34", "34": "rework_34", "mix": "mix_tweak", "preview": "mix_tweak", "new_song": "beat"}
    want = [alias.get(w, w) for w in want]
    bad = [w for w in want if w not in ids]
    if bad:
        raise ValueError(f"unknown bench job(s) {bad}: {', '.join(ids)}")
    return [j for j in JOBS if j["id"] in want]                   # always the fixed order


def _estimate(sel):
    ids = {j["id"] for j in sel}
    m = SETUP_MIN["start"] + SETUP_MIN["finish"]
    for j in sel:
        m += j.get("minutes_alone", j["minutes"]) if j["id"] == "ending" and "rework_34" not in ids else j["minutes"]
    if ids & {"rework_34", "ending"}:
        m += SETUP_MIN["fixture"]
    if ids & {"mix_tweak", "knob"} and "beat" not in ids:
        m += 0.5                                                  # a beat song made + opened for them
    return round(m, 1)


def _plan(sel):
    return {"dry_run": True, "jobs": [{"id": j["id"], "title": j["title"], "minutes": j.get(
        "minutes_alone", j["minutes"]) if j["id"] == "ending" and not any(x["id"] == "rework_34" for x in sel)
        else j["minutes"], "renders": j["renders"]} for j in sel],
        "estimated_minutes": _estimate(sel),
        "needs": "Windows with FL Studio open on a saved song (or its empty new project) and no popup; don't use FL "
                 "while it runs",
        "safety": "sandbox songs only (<first FLH_ROOTS folder>/_fl-headless-check/bench-<time>, recycled after "
                  "unless keep=true); your song is saved by FL first and reopened at the end; the rules come from a "
                  "temporary profile and finals stay in the sandbox",
        "results": os.path.join(_bench_dir(), "<time>.json")}


# ============================================================================= results / compare
def _results():
    try:
        return sorted(glob.glob(os.path.join(_bench_dir(), "2*.json")))
    except Exception:
        return []


def _load_result(ref, before=None):
    """'last' (the newest saved run, older than `before`) or a path -> the saved run."""
    if ref in (None, ""):
        return None
    kind = str(ref).strip().lower()
    if kind in ("last", "last_normal", "last_slow"):
        files = [f for f in _results() if not before or os.path.basename(f) < os.path.basename(before)]
        base, rows, srcs = None, {}, []                           # per job: its newest PASSING row of that kind (a
        for f in reversed(files):                                 # knob-only re-run mustn't hide the full run - PC
            try:                                                  # 2026-10-22), else its newest row
                with open(f, encoding="utf-8") as fh:
                    d = json.load(fh) or {}
            except (OSError, ValueError):
                continue
            if not d.get("jobs") or not (kind == "last" or _is_slow(d) == (kind == "last_slow")):
                continue
            if base is None:
                base, base_f = d, f
            used = False
            for j in d["jobs"]:
                jid = j.get("id"); have = rows.get(jid)
                if have is None or (have.get("status") != "pass" and j.get("status") == "pass"):
                    rows[jid] = j; used = True
            if used and f not in srcs:
                srcs.append(f)
            if len(rows) >= len(JOBS) and all(r.get("status") == "pass" for r in rows.values()):
                break
        if base is None:
            raise ValueError(f"compare='{kind}': no earlier fl_bench run "
                             f"{'' if kind == 'last' else '(' + kind[5:] + ') '}with results is saved yet")
        order = [j["id"] for j in JOBS]
        out = dict(base, jobs=sorted(rows.values(), key=lambda r: order.index(r["id"]) if r.get("id") in order else 99))
        out["_file"] = srcs[0] if srcs else base_f
        if len(srcs) > 1:
            out["merged_from"] = srcs                             # per job, the newest run where it passed
        return out
    ref = str(ref)
    if not os.path.isabs(ref) and not os.path.isfile(ref):
        cand = os.path.join(_bench_dir(), ref if ref.endswith(".json") else ref + ".json")
        ref = cand if os.path.isfile(cand) else ref
    with open(ref, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or not isinstance(d.get("jobs"), list):
        raise ValueError(f"{ref} isn't an fl_bench result")
    d["_file"] = ref
    return d


def _is_slow(d):
    return str(d.get("speed") or "normal").startswith("slow")


def _step_deltas(a, b, min_s=1.0, top=5):
    """{step: [before_s, after_s]} for the steps whose time changed by min_s or more (new or gone steps too), the
    biggest change first - which step a slower run spent its time in."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    num = lambda v: float(v) if isinstance(v, (int, float)) else 0.0   # noqa: E731
    ch = [(k, a.get(k), b.get(k)) for k in list(a) + [k for k in b if k not in a]]
    ch = [(k, x, y) for k, x, y in ch if abs(num(y) - num(x)) >= min_s]
    ch.sort(key=lambda t: -abs(num(t[2]) - num(t[1])))
    return {k: [x, y] for k, x, y in ch[:top]} or None


def compare_runs(before, after):
    """Before/after per job: seconds, renders and their deltas (only jobs that ran in both are compared)."""
    b = {j["id"]: j for j in before.get("jobs") or []}
    rows, tot = [], {"before_s": 0.0, "after_s": 0.0, "before_renders": 0, "after_renders": 0}
    speeds_differ = _is_slow(before) != _is_slow(after)
    for j in after.get("jobs") or []:
        o = b.get(j["id"])
        if not o:
            rows.append({"job": j["id"], "after_s": j.get("seconds"), "note": "not in the earlier run"}); continue
        row = {"job": j["id"], "status": f"{o.get('status')} -> {j.get('status')}",
               "before_s": o.get("seconds"), "after_s": j.get("seconds"),
               "before_renders": o.get("renders"), "after_renders": j.get("renders")}
        if isinstance(o.get("seconds"), (int, float)) and isinstance(j.get("seconds"), (int, float)):
            row["delta_s"] = round(j["seconds"] - o["seconds"], 1)
            if o["seconds"] > 0:
                row["delta_pct"] = round(100.0 * (j["seconds"] - o["seconds"]) / o["seconds"])
        both = o.get("status") == "pass" and j.get("status") == "pass"   # a failed / skipped job stopped early
        if both and "delta_s" in row:
            tot["before_s"] += o["seconds"]; tot["after_s"] += j["seconds"]
        if isinstance(o.get("renders"), int) and isinstance(j.get("renders"), int):
            row["delta_renders"] = j["renders"] - o["renders"]
            if both:
                tot["before_renders"] += o["renders"]; tot["after_renders"] += j["renders"]
        if not both:
            row["note"] = "not in the totals: it didn't pass in both runs (a failed job stops early)"
        if speeds_differ and isinstance(o.get("seconds"), (int, float)) and o["seconds"] > 0 and \
                isinstance(j.get("seconds"), (int, float)):
            row["x"] = round(j["seconds"] / o["seconds"], 2)     # how many times slower (or faster) it got
        sd = _step_deltas(o.get("steps_s"), j.get("steps_s"))
        if sd:
            row["steps_changed"] = sd                             # where the time went (biggest change first)
        if o.get("mode") != j.get("mode"):
            row["note"] = (row.get("note", "") + "; " if row.get("note") else "") + \
                f"different setup ({o.get('mode')} vs {j.get('mode')}): not like for like"
        rows.append(row)
    tot = {k: round(v, 1) if isinstance(v, float) else v for k, v in tot.items()}
    tot["delta_s"] = round(tot["after_s"] - tot["before_s"], 1)
    tot["delta_renders"] = tot["after_renders"] - tot["before_renders"]
    tot["jobs"] = "jobs that passed in both runs"
    out = {"before": before.get("merged_from") or before.get("_file") or before.get("started"), "after": after.get("_file") or after.get(
        "started"), "rows": rows, "total": tot,
        "versions": [before.get("fl_headless"), after.get("fl_headless")]}
    sb, sa = before.get("speed") or "normal", after.get("speed") or "normal"
    if sb != sa:
        out["speed"] = [sb, sa]
        if tot["before_s"] > 0:
            tot["x"] = round(tot["after_s"] / tot["before_s"], 2)
        out["speed_note"] = ("a slow (fewer CPU cores) run against a normal one: the delta is roughly what a "
                             "budget PC costs, not a change in fl-headless; same-speed runs compare versions")
    return out


def _prune():
    for f in _results()[:-KEEP_RESULTS]:
        try:
            os.remove(f)                                          # our own old results in our state folder
        except OSError:
            pass


def _compact(rep, path):
    keep = ("id", "status", "seconds", "renders", "detail", "setup_s")
    rows = []
    for j in rep["jobs"]:
        r = {k: j[k] for k in keep if j.get(k) not in (None, {}, [])}
        for k in ("rules_first_time", "agree", "predicted", "landed", "mode", "render_method", "final_steps_s"):
            if k in j:
                r[k] = j[k]
        rows.append(r)
    out = {"summary": rep["summary"], "minutes": rep["minutes"], "jobs": rows, "file": path}
    if rep.get("speed", "normal") != "normal":
        out["speed"] = rep["speed"]
    for k in ("slow", "stopped", "restored", "sandbox_kept", "sandbox_not_recycled", "earlier_sandboxes", "compare",
              "recovered_previous_run"):
        if rep.get(k):
            out[k] = rep[k]
    if rep.get("notes"):
        out["notes"] = rep["notes"][:5]
    out["details"] = "steps_s and notes per job are in the file"
    return out


def _finish(rep, T0, stop=None, compare=None):
    rep["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rep["minutes"] = round((time.time() - T0) / 60, 1)
    if stop:
        rep["stopped"] = stop
    cnt = {s: 0 for s in STATUSES}
    for j in rep["jobs"]:
        cnt[j.get("status", "fail")] = cnt.get(j.get("status", "fail"), 0) + 1
    rep["summary"] = cnt
    rep["total"] = {"seconds": round(sum(j.get("seconds") or 0 for j in rep["jobs"]), 1),
                    "renders": sum(j.get("renders") or 0 for j in rep["jobs"])}
    os.makedirs(_bench_dir(), exist_ok=True)
    path = os.path.join(_bench_dir(), rep["stamp"] + ".json")
    if compare is not None:
        try:
            rep["compare"] = compare_runs(compare, dict(rep, _file=path))
        except Exception as e:
            rep["notes"].append(f"compare failed: {type(e).__name__}: {e}")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in rep.items() if k != "compare"}, f, indent=1, default=str)
    except OSError as e:
        rep["notes"].append(f"result not written: {e}")
    _prune()
    try:
        H["_state"]({"bench_last": path})
    except Exception:
        pass
    return _compact(rep, path)


def _recover_previous():
    """A bench run cut off (server restart): FL back on the user's song if it shows a bench song, the old sandbox
    recycled. -> what was done, or None."""
    aff = _restore_saved_affinity()                               # a cut-off slow run: FL's cores back first
    stt = H["_state"]().get("bench_run")
    if not stt:
        return {"affinity_restored": aff} if aff else None
    PC = _pc(); sb = stt.get("sandbox"); out = {"previous_run": stt.get("started")}
    try:
        if _fl_up() and sb:
            r = PC.leave_sandbox(sb, stt.get("original"))         # the user's song (never a sandbox file), else an
            if r:                                                 # empty project
                out.update(r)
    except Exception as e:
        out["reopen_error"] = _short(e)
    if H["_state"]().get("collection") in PC.TEMP_PROFILES:       # cut off inside a job: the user's collection back
        before = stt.get("collection_before")
        rec = H["_state"]().get(PC.PROFILE_STATE)
        if isinstance(rec, dict):
            before = rec.get("collection")
        H["_state"]({"collection": None if before in PC.TEMP_PROFILES else before, PC.PROFILE_STATE: None})
        for n in PC.TEMP_PROFILES:
            try:
                os.remove(sys.modules["flh_store"]._profile_path(n))
            except Exception:
                pass
    if sb and os.path.isdir(sb) and not stt.get("keep"):
        if PC._fl_sandbox_song(sb):
            out["sandbox_error"] = "FL still has a song from it open"; PC._note_leftover(sb)
        else:
            PC._forget_sandbox(sb)
            ok, why = PC._recycle_sandbox(sb)
            if ok:
                out["sandbox_recycled"] = True
            else:
                out["sandbox_error"] = _short(why); PC._note_leftover(sb)
    H["_state"]({"bench_run": None})
    if aff:
        out["affinity_restored"] = aff
    return out


# ============================================================================= slow mode (a budget PC, roughly)
SLOW_CORES = 2                                                    # slow=True: FL on 2 CPU cores during the run
PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_INFORMATION = 0x0400
ADOPT_EVERY_S = 1.0                                               # new FL processes (the render path) get the mask
_K32 = []


def _k32():
    """kernel32 with OpenProcess / Get+SetProcessAffinityMask / CloseHandle typed, or None (not Windows, no ctypes).
    Its own WinDLL instance, so the argtypes never clash with other modules' kernel32 use."""
    if _K32:
        return _K32[0]
    k = None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.OpenProcess.restype = wintypes.HANDLE
            k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k.GetProcessAffinityMask.restype = wintypes.BOOL
            k.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                                 ctypes.POINTER(ctypes.c_size_t)]
            k.SetProcessAffinityMask.restype = wintypes.BOOL
            k.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
            k.CloseHandle.restype = wintypes.BOOL
            k.CloseHandle.argtypes = [wintypes.HANDLE]
        except Exception:
            k = None
    _K32.append(k)
    return k


def _last_error():
    try:
        import ctypes
        return ctypes.get_last_error()
    except Exception:
        return None


def _proc_mask(pid, new=None):
    """FL process `pid`: its affinity mask (and the system's) -> (process_mask, system_mask); with `new`, sets it
    first and returns the masks read back. Raises OSError (process gone, access denied, not Windows).
    PC-VERIFY: OpenProcess(SET|QUERY_INFORMATION) on FL64.exe works without admin (same user, same integrity)."""
    import ctypes
    k = _k32()
    if k is None:
        raise OSError("CPU affinity needs Windows (kernel32)")
    h = k.OpenProcess(PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION, False, int(pid))
    if not h:
        raise OSError(f"can't open FL's process {pid} (Windows error {_last_error()})")
    try:
        if new is not None and not k.SetProcessAffinityMask(h, int(new)):
            raise OSError(f"SetProcessAffinityMask on {pid} failed (Windows error {_last_error()})")
        pm, sm = ctypes.c_size_t(0), ctypes.c_size_t(0)
        if not k.GetProcessAffinityMask(h, ctypes.byref(pm), ctypes.byref(sm)):
            raise OSError(f"GetProcessAffinityMask on {pid} failed (Windows error {_last_error()})")
        return int(pm.value), int(sm.value)
    finally:
        k.CloseHandle(h)


def _pick_mask(mask, cores):
    """`cores` of the CPUs in `mask` -> the new mask, or None when `mask` has no more than that. With twice as many
    logical CPUs as asked, every other one is taken: Windows numbers the two threads of a hyperthreaded core next
    to each other, so 0, 2 are two real cores, not one core's two threads. PC-VERIFY: on an SMT CPU, Task Manager >
    Details > Set affinity shows CPU 0 and CPU 2 ticked during a slow run."""
    bits = [i for i in range(max(1, int(mask).bit_length())) if int(mask) >> i & 1]
    if len(bits) <= cores:
        return None
    if len(bits) >= 2 * cores:
        bits = bits[::2]
    return sum(1 << i for i in bits[:cores])


def _bits(mask):
    return bin(int(mask)).count("1")


class Affinity:
    """FL limited to `cores` CPU cores while a slow run lasts; every FL process seen (the one open at the start and
    any started during the run, e.g. the command-line render) is limited, and put back exactly as it was by
    restore() - also on errors, a stop, the server exiting (atexit) or, after a crash, the next server start / bench.
    Nothing but the affinity mask is ever changed, and a mask someone else changed meanwhile is left alone."""

    def __init__(self, cores):
        self.cores = int(cores)
        self.orig, self.mine, self.failed, self.notes = {}, {}, {}, []
        self.limited = {}                                         # every process limited during the run (kept)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def _save(self):
        try:
            H["_state"]({"bench_affinity": {str(p): [self.orig[p], self.mine[p]] for p in self.mine} or None})
        except Exception:
            pass

    def apply(self, pids):
        """Limit the FL processes in `pids` not seen yet -> the pids limited now."""
        done = []
        with self._lock:
            for pid in pids or []:
                pid = int(pid)
                if pid in self.orig or pid in self.failed:
                    continue
                try:
                    pm, sm = _proc_mask(pid)
                    new = _pick_mask(pm, self.cores)
                    if new is None:
                        self.orig[pid] = pm
                        self.notes.append(f"FL (pid {pid}) already runs on {_bits(pm)} core(s): nothing to limit")
                        continue
                    self.orig[pid] = pm
                    got, _ = _proc_mask(pid, new)
                    self.mine[pid] = got; self.limited[pid] = got
                    done.append(pid)
                except OSError as e:
                    self.failed[pid] = _short(e, 160)
            if done:
                self._save()
        return done

    def adopt(self):
        try:
            return self.apply(H["_fl_pids"]() or [])
        except Exception:
            return []

    def start(self, pids):
        self.apply(pids)
        self.adopt()

        def watch():
            while not self._stop.wait(ADOPT_EVERY_S):
                self.adopt()
        self._thread = threading.Thread(target=watch, name="fl_bench-affinity", daemon=True)
        self._thread.start()
        return self

    def restore(self):
        """Every FL process limited by this run back to its own mask -> {restored, gone, left, errors}."""
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(ADOPT_EVERY_S + 1.0)
        out = _restore_masks({p: (self.orig[p], self.mine[p]) for p in self.mine})
        with self._lock:
            self.mine.clear()
        try:
            H["_state"]({"bench_affinity": None})
        except Exception:
            pass
        return out

    def report(self, restored=None):
        cpus = os.cpu_count() or 0
        r = {"cores": self.cores, "of_logical_cpus": cpus,
             "limited": {str(p): hex(m) for p, m in self.limited.items()} or None}
        if self.failed:
            r["couldnt_limit"] = dict(list(self.failed.items())[:4])
        if self.notes:
            r["notes"] = self.notes[:4]
        if restored is not None:
            r["restored"] = restored
        return {k: v for k, v in r.items() if v is not None}


def _restore_masks(saved):
    """{pid: (original, ours)} -> each still-running FL process whose mask is still ours gets its original back."""
    out = {"restored": [], "gone": [], "left": [], "errors": []}
    if not saved:
        return {k: v for k, v in out.items() if v}
    try:
        alive = {int(p) for p in (H["_fl_pids"]() or [])}
    except Exception:
        alive = None
    for pid, (orig, mine) in saved.items():
        pid = int(pid)
        if alive is not None and pid not in alive:                # gone (or the pid is someone else's now)
            out["gone"].append(pid); continue
        try:
            cur, _ = _proc_mask(pid)
            if cur != int(mine):
                out["left"].append(pid); continue                 # changed by someone else since: not ours to undo
            _proc_mask(pid, int(orig))
            out["restored"].append(pid)
        except OSError as e:
            out["errors"].append(f"{pid}: {_short(e, 120)}")
    return {k: v for k, v in out.items() if v}


def _restore_saved_affinity():
    """After a crash / kill mid-run: FL's processes the saved state says we limited, back. -> result or None."""
    try:
        saved = H["_state"]().get("bench_affinity")
    except Exception:
        return None
    if not saved or _RUN.get("running"):
        return None
    try:
        out = _restore_masks({int(p): tuple(v) for p, v in saved.items()})
    except Exception as e:
        out = {"errors": [_short(e)]}
    if not out.get("errors"):
        try:
            H["_state"]({"bench_affinity": None})
        except Exception:
            pass
    return out


_ACTIVE_AFFINITY = []
_ATEXIT = []


def _atexit_restore():
    for a in list(_ACTIVE_AFFINITY):
        try:
            a.restore()
        except Exception:
            pass


# ============================================================================= the runner
def _ctx_class():
    PC = _pc()

    class BenchCtx(PC.Ctx):
        def say(self, text):
            self._guard()
            _progress(**dict(_RUN.get("progress") or {}, detail=_short(text, 160)))
    return BenchCtx


def _run(sel, keep=False, compare=None, cores=None, user=None):
    """user: a sweep's hand-through of the user's song ({} is filled from this run's start; {'original': ...} is the
    song put back at the end - never what an earlier run left in FL)."""
    T0 = time.time()
    stamp = _now()
    PC = _pc()
    rep = {"fl_headless": H["SERVER"]["version"], "stamp": stamp, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
           "machine": {"os": platform.platform(terse=True), "cpus": os.cpu_count(), "python": platform.python_version()},
           "jobs": [], "notes": [], "rules": dict(BENCH_RULES)}
    try:
        rec = _recover_previous()
        if rec:
            rep["recovered_previous_run"] = rec
    except Exception as e:
        rep["notes"].append(f"recovering an interrupted run failed: {e}")
    roots = H["roots"]()
    if not roots:
        return _finish(rep, T0, stop="FLH_ROOTS isn't set: run flh_setup() first", compare=compare)
    sandbox = os.path.join(roots[0], SANDBOX_DIR, "bench-" + stamp)
    ctx = _ctx_class()(H, sandbox, os.path.join(_bench_dir(), "pictures"))
    _RUN["progress"] = {"step": "before starting: FL, the open song (saved by FL), popups", "pct": 0}
    _progress(**_RUN["progress"])
    try:
        pre = dict(_preflight(ctx, user))
    except PC.UserMustAct as e:
        return _finish(rep, T0, stop=str(e).replace("fl_pc_check()", "fl_bench()"), compare=compare)
    except Exception as e:
        return _finish(rep, T0, stop=f"couldn't check FL before starting: {type(e).__name__}: {e}", compare=compare)
    if isinstance(user, dict) and "original" in user:             # the sweep's song, found before its first run
        pre["original"] = user["original"]
    pre["original"] = PC.user_song(pre.get("original"))           # never a sandbox file
    if isinstance(user, dict) and "original" not in user:
        user["original"] = pre["original"]
    ctx.original, ctx.pids = pre.get("original"), list(pre.get("pids") or [])
    rep["your_song"] = pre.get("original") or "a new empty project"
    if pre.get("on_sandbox"):
        rep["notes"].append(f"FL showed '{os.path.splitext(os.path.basename(pre['on_sandbox']))[0]}' from an earlier "
                            "run's sandbox at the start (not a song of yours): FL is left on an empty project at the end"
                            if not pre["original"] else "FL showed a song from an earlier run's sandbox at the start: "
                            "your song is reopened at the end")
        old = PC._sandbox_root_of(pre["on_sandbox"])
        if old and _key(old) != _key(sandbox):
            PC._note_leftover(old)
    rep["speed"] = "normal"
    aff = None
    if cores:
        _RUN["progress"] = {"step": f"slow mode: FL limited to {cores} CPU cores for the run", "pct": 1}
        _progress(**_RUN["progress"])
        aff = Affinity(cores)
        _ACTIVE_AFFINITY.append(aff)
        try:
            aff.start(ctx.pids)
        except Exception as e:                                    # never a reason to touch anything else
            aff.failed[-1] = f"{type(e).__name__}: {_short(e, 160)}"
        if aff.mine:
            rep["speed"] = f"slow ({cores} cores)"
        elif aff.orig and not aff.failed:
            rep["speed"] = f"normal (FL already had {cores} cores or fewer)"
        else:
            rep["notes"].append("slow mode couldn't limit FL's CPU cores, so this is a normal-speed run: "
                                + "; ".join(str(v) for v in list(aff.failed.values())[:2]))
    H["writable"](os.path.join(sandbox, "x.flp"))
    os.makedirs(sandbox, exist_ok=True)
    rep["sandbox"] = sandbox
    H["_state"]({"bench_run": {"original": pre.get("original"), "sandbox": sandbox, "started": rep["started"],
                               "keep": bool(keep), "collection_before": H["_state"]().get("collection")}})
    st = {}; fl_gone = None; n = len(sel); blocked = None
    prof = contextlib.ExitStack()
    try:
        try:                                                      # every job's finals / previews stay in the sandbox
            prof.enter_context(PC.sandbox_profile(sandbox))
        except Exception as e:
            blocked = (f"couldn't switch to the sandbox profile ({type(e).__name__}: {_short(e)}), so finals could "
                       "land in your collection's final folder")
            rep["notes"].append(blocked)
        for i, j in enumerate(sel):
            row = {"id": j["id"], "title": j["title"]}
            if fl_gone or blocked:
                rep["jobs"].append(dict(row, status="skip", detail="not run: " + (fl_gone or blocked), seconds=0.0,
                                        renders=0))
                continue
            _RUN["progress"] = {"step": f"job {i + 1}/{n}: {j['title']}", "job": j["id"],
                                "pct": round(100 * i / max(1, n))}
            _progress(**_RUN["progress"])
            M = Meter(); ctx.check = j["id"]; ctx._expired = False
            t = time.time(); res = {}
            try:
                with ctx.deadline(j["deadline_s"]):
                    res = j["run"](ctx, st, M) or {}
                if res.get("status") not in STATUSES:
                    raise ValueError(f"job returned {_short(res, 120)}")
            except Skip as e:
                res = {"status": "skip", "detail": str(e)}
            except PC.UserMustAct as e:
                res = {"status": "skip", "detail": f"needs you first: {e}"}
            except TimeoutError as e:
                res = {"status": "fail", "detail": f"over its {j['deadline_s']} s deadline: {e}",
                       "traceback": traceback.format_exc()[-1200:]}
            except Exception as e:
                res = {"status": "fail", "detail": f"{type(e).__name__}: {_short(e, 300)}",
                       "traceback": traceback.format_exc()[-1500:]}
                try:
                    H["_note_tool_error"](f"fl_bench:{j['id']}", e, traceback.format_exc())
                except Exception:
                    pass
            row.update(res)
            row.update(seconds=M.seconds(), renders=M.r["exports"], steps_s=M.steps,
                       wall_s=round(time.time() - t, 1))
            if M.r["reused"] or M.r["offline"]:
                row["previews_reused"] = M.r["reused"]; row["previews_offline"] = M.r["offline"]
            if M.setup:
                row["setup_s"] = round(sum(M.setup.values()), 1); row["setup_steps_s"] = M.setup
                if M.setup_renders:
                    row["setup_renders"] = M.setup_renders
            if M.notes or ctx.notes:
                row["notes"] = M.notes + ctx.notes; ctx.notes = []
            rep["jobs"].append(row)
            if not _fl_up():
                fl_gone = f"FL wasn't running any more after '{j['id']}'"
                rep["notes"].append(fl_gone)
            else:
                ctx.fl_restarted()                                # fl_finalize's render path may restart FL
                try:
                    pops = PC._answer_popups(ctx, j["id"])
                    if pops:
                        row["popups_left_and_cancelled"] = pops
                except Exception:
                    pass
    finally:
        if aff is not None:                                       # first: FL gets all its cores back
            try:
                rep["slow"] = aff.report(aff.restore())
            except Exception as e:
                rep["slow"] = dict(aff.report(), restore_error=_short(e))
            if aff in _ACTIVE_AFFINITY:
                _ACTIVE_AFFINITY.remove(aff)
        _RUN["progress"] = {"step": "cleaning up: your song back in FL, sandbox to the Recycle Bin", "pct": 99}
        _progress(**_RUN["progress"])
        errs = ctx.run_cleanups()
        if errs:
            rep["notes"].append(f"cleanup errors: {errs}")
        try:
            PC._forget_sandbox(sandbox)
        except Exception:
            pass
        try:
            rep["restored"] = _restore(ctx, pre, fl_gone)
        except Exception as e:
            rep["restored"] = {"ok": False, "why": _short(e)}
        try:
            prof.close()                                          # the user's collection back
        except Exception as e:                                    # noqa: BLE001
            rep["notes"].append(f"couldn't put your collection back: {type(e).__name__}: {_short(e)}")
        try:                                                      # recycled only once FL has left it (one retry);
            PC.finish_sandbox(rep, sandbox, keep, pre.get("original"))   # else said with its path
        except Exception as e:                                    # noqa: BLE001
            rep["sandbox_kept"] = sandbox
            rep["sandbox_not_recycled"] = {"path": sandbox, "why": f"{type(e).__name__}: {_short(e)}"}
        if rep.get("sandbox_not_recycled"):
            rep["notes"].append(PC._sandbox_note(rep))
        H["_state"]({"bench_run": None})
    return _finish(rep, T0, compare=compare, stop=blocked)


# ============================================================================= the tool
def _cores_arg(slow, cores):
    """slow=True -> SLOW_CORES; cores=N (1..64) alone also means a slow run -> int or None."""
    if cores in (None, "", 0) and not slow:
        return None
    if cores in (None, "", 0):
        return SLOW_CORES
    try:
        n = int(cores)
    except (TypeError, ValueError):
        raise ValueError("cores: a whole number of CPU cores (1-64), e.g. 2")
    if not 1 <= n <= 64:
        raise ValueError("cores: 1-64")
    return n


def fl_bench(jobs=None, keep=False, compare=None, dry_run=False, slow=False, cores=None):
    """See the module docstring. A background job: fl_job(<job>) shows 'job i/n: ...' and returns the result."""
    sel = _select(jobs)
    ncores = _cores_arg(slow, cores)
    if isinstance(compare, (list, tuple)):                         # two saved runs: no FL, any OS
        if len(compare) != 2:
            raise ValueError("compare: 'last', a saved result file, or [before, after] (two files)")
        return {"compare": compare_runs(_load_result(compare[0]), _load_result(compare[1]))}
    if dry_run:
        plan = _plan(sel)
        if ncores:
            plan["slow"] = {"cores": ncores, "of_logical_cpus": os.cpu_count(),
                            "note": f"FL limited to {ncores} CPU cores during the run (put back after); "
                                    "expect it to take longer than the estimate, which is for a normal run"}
        if compare:
            try:
                plan["compare_with"] = _load_result(compare)["_file"]
            except Exception as e:
                plan["compare_error"] = f"{type(e).__name__}: {e}"
        return plan
    if not H["IS_WINDOWS"]:
        raise RuntimeError("fl_bench runs on the Windows PC with FL Studio; dry_run=true shows the plan anywhere, "
                           "compare=[a, b] compares two saved runs anywhere")
    if _pc() is None:
        raise RuntimeError("fl_bench needs the flh_pccheck module (its sandbox and FL safety helpers)")
    if getattr(_pc(), "_RUN", {}).get("running"):
        return {"started": False, "note": "fl_pc_check is running: wait for it (fl_job) and run fl_bench after"}
    sw = getattr(_pc(), "_sweep_running", lambda: None)()
    if sw:
        return {"started": False, "note": f"fl_sweep is running ({sw}): it runs fl_bench itself; "
                                          "fl_sweep(status=true) shows where it is"}
    earlier = _load_result(compare) if compare else None          # a bad compare stops here, before FL is used
    first = _pc()._quick_precheck()
    if first:
        return {"started": False, "you_first": first, "then": "fl_bench() again (dry_run=true shows the plan)"}
    with _RUN_LOCK:
        if _RUN["running"]:
            return {"already_running": _RUN["job"], "note": "fl_job(<job>) shows its progress"}
        _RUN["running"] = True

    def job():
        try:
            return _run(sel, keep, earlier, ncores)
        finally:
            _RUN["running"] = False
    try:
        r = H["_start_job"]("bench", job, fl=True)
    except Exception:
        _RUN["running"] = False
        raise
    _RUN["job"] = r.get("job") if isinstance(r, dict) else None
    out = dict(r) if isinstance(r, dict) else {"result": r}
    if ncores:
        out["slow"] = f"FL limited to {ncores} CPU cores during the run, then put back"
    out.update(jobs=[j["id"] for j in sel], estimated_minutes=_estimate(sel),
               note=f"don't use FL meanwhile; fl_job('{_RUN['job']}') shows 'job i/n' and returns the result")
    return out


def _boot_restore_collection():
    """A bench cut off by a crash / server restart left its temporary collection profile active (so new songs would
    use it): the user's own comes back when the server starts (state only; FL and the sandbox wait for the next
    fl_bench / fl_pc_check, which recover them). Never while a bench / sweep job of this server runs."""
    st = H["_state"]() or {}
    if st.get("collection") != BENCH_PROFILE or _RUN.get("running"):
        return None
    if any(str(j.get("job") or "").startswith(("bench-", "sweep-")) and j.get("status") == "running"
           for j in list((H.get("JOBS") or {}).values())):
        return None
    before = (st.get("bench_run") or {}).get("collection_before")
    rec = st.get("sandbox_profile_user")                          # (flh_pccheck.PROFILE_STATE: the user's own)
    if isinstance(rec, dict):
        before = rec.get("collection")
    temp = (BENCH_PROFILE, "_fl-headless check")
    H["_state"]({"collection": before if before not in temp else None, "sandbox_profile_user": None})
    for n in temp:
        try:
            os.remove(sys.modules["flh_store"]._profile_path(n))   # our own temporary files
        except Exception:
            pass
    return before


def register(ns):
    global H
    H = ns
    S, P = ns["_schema"], ns["_p"]
    BOOL = ns["_BOOL"]
    ns["TOOLS"]["fl_bench"] = (
        fl_bench,
        "Measure fl-headless's speed on this PC with a fixed set of jobs on SANDBOX songs (your song is saved by FL "
        "first and reopened after; the sandbox is recycled): beat (new trap song + its background level balance), "
        "rework_34 (a 3/4 piano piece -> string/choir layers, perform, rule-aware ending, final with ending rules: "
        "did they pass on the first render?), ending (fl_ending_check's prediction vs the render), mix_tweak (fader "
        "+ two previews, the second must not render), knob (compressor threshold -36 dB, read back). Per job: "
        "pass/fail/skip with the reason, seconds, renders, steps_s. A background job (fl_job shows 'job i/n'); the "
        "result is saved in the state folder (bench/<time>.json). compare='last' or a file adds a before/after table; "
        "compare=[a, b] compares two saved runs without running. dry_run=true: the plan and minutes (~10 for all). "
        "slow=true (or cores=N) limits FL to 2 (N) CPU cores during the run to approximate a budget PC and puts FL's "
        "own setting back after (also on errors / stop); the result says speed='slow (2 cores)', and "
        "compare='last_normal' / 'last_slow' picks the newest run of that kind.",
        S(jobs=P(["array", "string"], "only these jobs: beat, rework_34, ending, mix_tweak, knob (default all; they "
                 "always run in that order)", True, items={"type": "string"}),
          keep=BOOL("keep the sandbox folder (default false: Recycle Bin)", True),
          compare=P(["string", "array"], "'last' | 'last_normal' | 'last_slow', an earlier result file, or "
                    "[before, after] files", True, items={"type": "string"}),
          slow=BOOL("limit FL to 2 CPU cores during the run (a budget PC, roughly; default false)", True),
          cores=ns["_INT"]("slow mode with this many CPU cores (1-64; implies slow)", True),
          dry_run=BOOL("only the plan with the estimated minutes (any OS)", True)))
    if not _ATEXIT:
        import atexit
        atexit.register(_atexit_restore); _ATEXIT.append(1)
    try:
        _boot_restore_collection()
    except Exception:
        pass
    try:
        if ns.get("IS_WINDOWS") and (ns["_state"]() or {}).get("bench_affinity"):   # a slow run cut off by a crash
            threading.Thread(target=_restore_saved_affinity, name="fl_bench-affinity-restore", daemon=True).start()
    except Exception:
        pass
    ns.setdefault("CROSS_PLATFORM", set()).add("fl_bench")        # dry_run / compare anywhere; the run checks Windows
    try:
        ns["TOOL_GROUPS"]["fl_bench"] = "start"
    except Exception:
        pass
