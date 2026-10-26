"""flh_sweep: fl_sweep - one command for a full health sweep of fl-headless on this PC, turned into a ranked fix list.

Steps (one background job; fl_job shows 'sweep 2/5: bench (normal) - job 1/5 ...'):
  1. pc_check   fl_pc_check's own runner (flh_pccheck._run: full + unattended by default) inside this job
  2. bench      fl_bench's runner (flh_bench._run) at normal speed, then slow (FL on 2 CPU cores)
  3. usage      the local usage log (error counts per tool) and the recent tool errors (flh_bug_report's ring buffer)
  4. compare    against the previous sweep: per check newly failing / still failing / fixed / newly passing /
                newly warning; per bench job slower / faster beyond noise (> 15 % AND > 2 s, both runs passed) and
                renders up / down; tools whose error count grew
  5. fix list   ranked: crash / FL closed > fail > regression > warn > slow; each item: id, what, evidence pointers
                (report.json rows, pictures the checks already saved, the bench file), the code area (the checks'
                fix_hint / checklist links, the tool's module) and the sweep it was first seen in
  6. saved      <state folder>/sweeps/<time>/sweep.json + sweep.md (short) + share.zip (privacy-safe: flh_bug_report's
                bundle and scrubber plus a stricter pass that removes every path, the song's name and the collection
                name; sweep.md/json, pc_check report.md, bench results; no audio, projects or pictures unless
                share_pictures=true). The newest KEEP sweeps are kept (only our own sweep folders are removed).
Off Windows (or without flh_pccheck / flh_bench) the FL steps are skipped with a note; usage, errors, compare and the
fix list still run. dry_run=true: the plan, estimated minutes and what the user must do (writes nothing).
status=true: the running sweep's progress, or the last sweep's summary; report='last' | a sweep folder / sweep.json /
<time>: that sweep's fix list and compare.

PC-VERIFY: see checklists/sweep.md (the whole FL part runs only on the Windows PC)."""
import glob
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import zipfile

H = None                                                          # the live server namespace (register)
KEEP = 10                                                         # newest sweep folders kept
NOISE_PCT = 15.0                                                  # a bench job is slower / faster beyond noise only
NOISE_S = 2.0                                                     # when BOTH are exceeded
SLOW_FACTOR = 1.6                                                 # rough: a 2-core bench run vs its normal estimate
SLOW_OVER_ESTIMATE = 1.5                                          # a normal bench job over 1.5 x its estimate: 'slow'
SLOW_X = 3.0                                                      # slow run > 3 x the normal one: 'slow' (budget PCs)
TOP = 10
ERRORS_KEPT = 15
KINDS = ("crash", "fail", "regression", "warn", "slow")           # severity order
STAMP_RE = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")
LABELS = {"pc_check": "pc_check", "bench_normal": "bench (normal)", "bench_slow": "bench (slow, 2 CPU cores)",
          "usage": "usage log + tool errors"}
CODE_AREA = {                                                     # pc_check areas -> where the code lives
    "core": "fl_headless_mcp.py: FL identity / user data folder / open-project guard",
    "render": "fl_headless_mcp.py: render path (fl_render, in-FL render, command-line render)",
    "live_notes": "fl_headless_mcp.py: live notes (bridge script live edits)",
    "lazy": "fl_headless_mcp.py: lazy reload / flh_sync",
    "bridge": "fl_script/ bridge script + fl_headless_mcp.py bridge (MIDI)",
    "files": "fl_headless_mcp.py: .flp writers / backups / files_recycle",
}
BENCH_TOOLS = {"beat": ("flh_new_song", "flp_balance"), "rework_34": ("flp_compose", "flp_perform", "fl_finalize"),
               "ending": ("fl_ending_check", "fl_finalize"), "mix_tweak": ("fl_preview",),
               "knob": ("fl_sound_shape",)}
SHARE_STATE_DIRS = ("pc_check", "bench", "sweeps", "bug_reports")  # state sub-folders whose paths are ours (shareable)
DROP_KEYS = {"your_song", "original", "sandbox", "sandbox_kept", "reopened", "traceback_locals"}
_RUN = {"running": False, "job": None, "stop": threading.Event(), "step": None, "last": None}
_RUN_LOCK = threading.Lock()


# ============================================================================= small helpers
def _state_dir():
    return os.path.dirname(H["STATE"])


def _base():
    return os.path.join(_state_dir(), "sweeps")


def _now():
    return time.strftime("%Y%m%d-%H%M%S")


def _short(x, n=200):
    s = str(x)
    return s if len(s) <= n else s[:n - 3] + "..."


def _key(p):
    return os.path.normcase(os.path.abspath(p))


def _inside(path, folder):
    a, b = _key(path), _key(folder).rstrip("\\/")
    return a == b or a.startswith(b + os.sep)


def _mod(name):
    """A registered extension module (its register() ran: H is set) -> the module, or None."""
    m = sys.modules.get(name)
    return m if m is not None and getattr(m, "H", None) is not None else None


def _pc():
    return _mod("flh_pccheck")


def _bench():
    return _mod("flh_bench")


def _windows():
    return bool(H.get("IS_WINDOWS"))


def _progress(**f):
    jid = _RUN.get("job")
    jobs = H.get("JOBS") or {}
    if jid in jobs:
        cur = dict(jobs[jid].get("progress") or {})
        cur.update(f)
        cur["at"] = round(time.time() - (_RUN.get("t0") or time.time()), 1)
        jobs[jid]["progress"] = {k: v for k, v in cur.items() if v is not None}


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ============================================================================= saved sweeps
def _order(name):
    """Sort key of a sweep folder name: <time> then its -N suffix (two sweeps in one second)."""
    head, _, n = str(name).partition("-")
    rest = n.split("-")
    return (head, rest[0] if rest else "", int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 1)


def _own_dirs():
    """Our own sweep folders (a <time> name AND a sweep.json we wrote), oldest first."""
    base = _base()
    try:
        names = sorted(os.listdir(base), key=_order)
    except OSError:
        return []
    out = []
    for n in names:
        d = os.path.join(base, n)
        if STAMP_RE.match(n) and os.path.isdir(d) and os.path.isfile(os.path.join(d, "sweep.json")):
            out.append(d)
    return out


def _prune():
    """Keep the newest KEEP of our own sweep folders; nothing else in (or outside) the sweeps folder is touched."""
    removed = []
    for d in _own_dirs()[:-KEEP]:
        if _inside(d, _base()) and _key(d) != _key(_base()):
            shutil.rmtree(d, ignore_errors=True)
            removed.append(os.path.basename(d))
    return removed


def _resolve(ref, before=None):
    """'last' (newest sweep, older than `before` stamp), a stamp, a folder or a sweep.json -> (path, dict) or
    (None, None)."""
    if ref in (None, "", False):
        return None, None
    s = str(ref).strip()
    if s.lower() in ("last", "latest", "true", "1"):
        dirs = [d for d in _own_dirs() if not before or _order(os.path.basename(d)) < _order(before)]
        for d in reversed(dirs):
            p = os.path.join(d, "sweep.json"); obj = _read_json(p)
            if isinstance(obj, dict):
                return p, obj
        return None, None
    cands = [s, os.path.join(s, "sweep.json"), os.path.join(_base(), s, "sweep.json")]
    for p in cands:
        if os.path.isfile(p):
            obj = _read_json(p)
            if isinstance(obj, dict) and "fixes" in obj:
                return p, obj
            raise ValueError(f"{p} isn't an fl_sweep result")
    raise ValueError(f"no sweep '{s}' (use 'last', a time like 20261023-101500, a sweep folder or its sweep.json)")


# ============================================================================= the steps (each returns a dict)
def _step_pc_check(full, unattended, user=None):
    PC = _pc()
    if PC is None:
        return {"ran": False, "note": "flh_pccheck isn't loaded: pc_check skipped"}
    if not _windows():
        return {"ran": False, "note": "not Windows: pc_check skipped (it needs FL Studio on the Windows PC)"}
    sel, err = PC.plan(None, None, None, bool(full))
    PC._RUN["job"] = _RUN["job"]; PC._RUN["ask"] = None; PC._RUN["answer_ev"].clear()
    # 'running' only while the checks run: it also switches the render cache off (checks measure real renders), and
    # left on through the bench it made every fl_preview render again (PC 2026-10-23: bench mix_tweak failed in the
    # sweep, passed alone). fl_pc_check / fl_bench refuse during the whole sweep through _sweep_running().
    PC._RUN["running"] = True
    try:
        res = PC._run(sel, err, bool(unattended), False, user=user)
    finally:
        PC._RUN["running"] = False
    rep = _read_json(res.get("report")) if isinstance(res, dict) and res.get("report") else None
    return {"ran": True, "result": res, "report": (res or {}).get("report"), "report_md": (res or {}).get("report_md"),
            "rows": (rep or {}).get("checks") or [], "your_song": (rep or {}).get("your_song"),
            "minutes_budget": {c["id"]: c.get("minutes") for c in sel}}


def _step_bench(slow, user=None):
    B = _bench()
    label = "slow" if slow else "normal"
    if B is None or _pc() is None:
        return {"ran": False, "note": f"flh_bench / flh_pccheck isn't loaded: bench ({label}) skipped"}
    if not _windows():
        return {"ran": False, "note": f"not Windows: bench ({label}) skipped (it needs FL Studio on the Windows PC)"}
    try:
        if not H["fl_running"]():
            return {"ran": False, "fl_gone": True, "note": f"FL isn't running: bench ({label}) skipped"}
    except Exception:
        pass
    sel = B._select(None)
    B._RUN["running"] = True; B._RUN["job"] = _RUN["job"]
    orig = H.get("_job_progress")
    retry = None

    def progress(**f):                                            # the bench's 'job i/n' + our 'sweep i/n'
        if orig:
            orig(**f)
        _progress(sweep=_RUN.get("step"))
    H["_job_progress"] = progress
    try:
        res = B._run(sel, False, None, B.SLOW_CORES if slow else None, user=user)
        # stopped before any job (its preflight: FL still busy / loading / a popup right after the step before it -
        # PC 2026-10-23: the slow run ran 0 jobs in 16 s): let FL settle and try once more, then say why
        if isinstance(res, dict) and res.get("stopped") and not res.get("jobs") and not _RUN["stop"].is_set():
            retry = {"first_try": _short(res.get("stopped"), 300), "settle": _settle_fl()}
            res = B._run(sel, False, None, B.SLOW_CORES if slow else None, user=user)
    finally:
        B._RUN["running"] = False
        if H.get("_job_progress") is progress:
            H["_job_progress"] = orig
    data = _read_json(res.get("file")) if isinstance(res, dict) and res.get("file") else None
    out = {"ran": True, "result": res, "file": (res or {}).get("file"), "speed": (data or res or {}).get("speed"),
           "jobs": (data or {}).get("jobs") or (res or {}).get("jobs") or [],
           "estimate_min": {j["id"]: j.get("minutes") for j in sel}}
    if retry:
        out["retry"] = retry
    if (res or {}).get("stopped") and not out["jobs"]:
        out["note"] = f"bench ({label}) ran no jobs: {_short(res['stopped'], 240)}"
    return out


SETTLE_S = 30.0                                                   # FL settling between two steps (scaled per PC)
SETTLE_POLL_S = 1.0


def _settle_fl():
    """Wait (up to SETTLE_S, scaled) until FL runs, has no popup and isn't busy (loading / saving) -> what it saw
    (title: FL's title names a project)."""
    t0 = time.time(); limit = SETTLE_S
    try:
        limit = H["_scaled"](SETTLE_S)
    except Exception:
        pass
    seen = {}
    while True:
        try:
            seen = {"running": bool(H["fl_running"]())}
            pop = H["_fl_blocking_popup"]() if seen["running"] else None
            seen["popup"] = H["_popup_words"](pop) if pop else None
            seen["title"] = bool(H["_title_stem"](H["_main_title"]())) if seen["running"] else False
            seen["busy"] = bool(H["_fl_still_working"]()) if seen["running"] else None
        except Exception as e:                                    # noqa: BLE001 - only a wait
            seen = {"error": f"{type(e).__name__}: {_short(e, 80)}"}
        if seen.get("running") and not seen.get("popup") and not seen.get("busy"):
            break
        if time.time() - t0 >= limit or _RUN["stop"].is_set():
            break
        time.sleep(SETTLE_POLL_S)
    seen["waited_s"] = round(time.time() - t0, 1)
    return seen


def _step_usage():
    try:
        tot = (H.get("_usage_totals"))()
        tools = {k: {"calls": int(v.get("calls") or 0), "errors": int(v.get("errors") or 0)}
                 for k, v in (tot.get("tools") or {}).items()}
        since = tot.get("since")
    except Exception as e:                                        # noqa: BLE001
        tools, since = {}, None
        note = f"usage log unreadable: {type(e).__name__}"
    else:
        note = None
    errs = []
    for e in list(H.get("_ERRORS") or [])[-ERRORS_KEPT:]:
        tb = str(e.get("traceback") or "").strip().splitlines()
        errs.append({"t": e.get("t"), "tool": e.get("tool"), "error": _short(e.get("error"), 300),
                     **({"where": _short(tb[-3].strip(), 200)} if len(tb) >= 3 else {})})
    out = {"tools": tools, "since": since, "errors": errs,
           "usage_off": bool(H.get("_usage_off") and H["_usage_off"]())}
    if note:
        out["note"] = note
    return out


# ============================================================================= compare
def _bench_rows(sw, speed):
    return {j.get("id"): j for j in ((sw.get("bench") or {}).get(speed) or {}).get("jobs") or [] if j.get("id")}


def compare(cur, prev):
    """cur / prev: sweep dicts -> the compare table (see the module docstring)."""
    out = {"against": prev.get("stamp") if prev else None}
    if not prev:
        out["note"] = "first sweep (or compare off): nothing to compare with"
        return out
    pc_cur = {r["id"]: r.get("status") for r in cur.get("checks") or [] if r.get("id")}
    pc_prev = {r["id"]: r.get("status") for r in prev.get("checks") or [] if r.get("id")}
    ch = {"newly_failing": [], "still_failing": [], "fixed": [], "newly_passing": [], "newly_warning": []}
    if pc_cur and pc_prev:
        for cid, s in pc_cur.items():
            p = pc_prev.get(cid)
            if s == "fail":
                ch["still_failing" if p == "fail" else "newly_failing"].append(cid)
            elif p == "fail" and s in ("pass", "warn"):
                ch["fixed"].append(cid)
            elif s == "pass" and p != "pass":
                ch["newly_passing"].append(cid)
            elif s == "warn" and p == "pass":
                ch["newly_warning"].append(cid)
    elif pc_cur or pc_prev:
        out["checks_note"] = "pc_check ran in only one of the two sweeps"
    out["checks"] = {k: v for k, v in ch.items() if v}
    rows = []
    for speed in ("normal", "slow"):
        a, b = _bench_rows(prev, speed), _bench_rows(cur, speed)
        for jid, j in b.items():
            o = a.get(jid)
            if not o:
                continue
            row = {"speed": speed, "job": jid, "status": f"{o.get('status')} -> {j.get('status')}"}
            bs, as_ = o.get("seconds"), j.get("seconds")
            both = o.get("status") == "pass" and j.get("status") == "pass"
            if both and isinstance(bs, (int, float)) and isinstance(as_, (int, float)):
                d = as_ - bs; pct = 100.0 * d / bs if bs > 0 else 0.0
                row.update(before_s=bs, after_s=as_, delta_s=round(d, 1), delta_pct=round(pct))
                row["verdict"] = ("slower" if d > NOISE_S and pct > NOISE_PCT else
                                  "faster" if -d > NOISE_S and -pct > NOISE_PCT else "same")
            elif not both:
                row["verdict"] = "not compared (didn't pass in both)"
            br, ar = o.get("renders"), j.get("renders")
            if both and isinstance(br, int) and isinstance(ar, int) and br != ar:
                row["renders"] = f"{br} -> {ar}"; row["renders_delta"] = ar - br
            rows.append(row)
    out["bench"] = rows
    pu = (prev.get("usage") or {}).get("tools") or {}
    cu = (cur.get("usage") or {}).get("tools") or {}
    grew = []
    for t, v in cu.items():
        before = int((pu.get(t) or {}).get("errors") or 0)
        if v.get("errors", 0) > before:
            grew.append({"tool": t, "before": before, "after": v["errors"], "delta": v["errors"] - before})
    out["tool_errors_grew"] = sorted(grew, key=lambda g: (-g["delta"], g["tool"]))
    return out


# ============================================================================= the fix list
def _pictures(row):
    """Pictures a check already saved (its 'look' and any .png in its evidence) that exist."""
    out = []

    def walk(v):
        if isinstance(v, str):
            if v.lower().endswith(".png") and os.path.isfile(v) and v not in out:
                out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(row.get("look")); walk(row.get("evidence"))
    return out[:4]


def _tool_area(tool):
    mod = (H.get("_TOOL_MODULE") or {}).get(tool)
    return f"{mod}.py: {tool}" if mod else f"fl_headless_mcp.py: {tool}"


def _check_area(row):
    area = row.get("area") or str(row.get("id") or "").split(".")[0]
    out = {"code": CODE_AREA.get(area) or f"flh_pccheck_mod.py check {row.get('id')} (the module it tests)"}
    if row.get("fix_hint"):
        out["fix_hint"] = _short(row["fix_hint"], 240)
    if row.get("checklist"):
        out["checklist"] = row["checklist"]
    return out


def _item(kind, id_, what, evidence, area, rank=0.0, new=True):
    return {"id": id_, "kind": kind, "what": _short(what, 300), "evidence": evidence, "code_area": area,
            "_rank": rank, "_new": new}


def build_fixes(sw, cmp, prev):
    """sw: this sweep -> ranked fix items (crash > fail > regression > warn > slow)."""
    items = []
    pc = sw.get("pc_check") or {}
    rep = pc.get("report")
    stopped = str(pc.get("stopped") or "")
    cmp_ch = (cmp or {}).get("checks") or {}
    new_fail = set(cmp_ch.get("newly_failing") or []); new_warn = set(cmp_ch.get("newly_warning") or [])
    for r in sw.get("checks") or []:
        st, cid = r.get("status"), r.get("id")
        ev = {"report": rep, "row": cid, **({"detail": _short(r.get("detail"), 240)} if r.get("detail") else {})}
        pics = _pictures(r)
        if pics:
            ev["pictures"] = pics
        crash = st == "fail" and re.search(r"FL closed or crashed|FL wasn't running", str(r.get("detail") or ""))
        if crash:
            items.append(_item("crash", f"check:{cid}", f"FL closed during check {cid}", ev, _check_area(r),
                               -float(r.get("risk") or 0)))
        elif st == "fail":
            items.append(_item("fail", f"check:{cid}", f"check {cid} fails: {r.get('title') or ''}", ev, _check_area(r),
                               -float(r.get("risk") or 0), new=not prev or cid in new_fail))
        elif st == "warn":
            kind = "regression" if cid in new_warn else "warn"
            items.append(_item(kind, f"check:{cid}", f"check {cid} warns" + (" (passed in the previous sweep)" if
                         kind == "regression" else "") + f": {r.get('title') or ''}", ev, _check_area(r),
                               -float(r.get("risk") or 0)))
    if re.search(r"FL closed|FL wasn't running", stopped) and not any(i["kind"] == "crash" for i in items):
        items.append(_item("crash", "crash:pc_check", f"pc_check stopped: {stopped}", {"report": rep},
                           {"code": "flh_pccheck.py run / the check that ran last"}))
    for step, info in (sw.get("steps") or {}).items():
        if info.get("crashed"):
            items.append(_item("crash", f"crash:{step}", f"the sweep's {step} step raised: {info['crashed']}",
                               {"sweep": "sweep.json steps." + step}, {"code": f"flh_sweep.py / the {step} runner"}))
        if info.get("fl_gone"):
            items.append(_item("crash", f"crash:fl_gone_before_{step}", f"FL wasn't running any more before {step}",
                               {"sweep": "sweep.json steps." + step}, {"code": "the step before it (see its report)"}))
    for speed in ("normal", "slow"):
        b = (sw.get("bench") or {}).get(speed) or {}
        est = b.get("estimate_min") or {}
        for j in b.get("jobs") or []:
            jid, st = j.get("id"), j.get("status")
            det = str(j.get("detail") or "")
            if det.startswith("Stopped:") or "stopped by fl_pc_check" in det:
                continue                                          # a stop, not a finding
            ev = {"file": b.get("file"), "job": jid, **({"detail": _short(det, 240)} if det else {}),
                  **({"steps_s": j["steps_s"]} if j.get("steps_s") else {})}
            area = {"code": ", ".join(_tool_area(t) for t in BENCH_TOOLS.get(jid, ())) or "flh_bench.py",
                    "checklist": "checklists/bench.md"}
            if st == "fail":
                kind = "crash" if re.search(r"FL wasn't running|FL closed", det) else "fail"
                items.append(_item(kind, f"bench:{speed}:{jid}", f"bench job {jid} ({speed}) fails", ev, area))
            elif st == "pass" and speed == "normal" and isinstance(j.get("seconds"), (int, float)) and est.get(jid):
                over = j["seconds"] / (60.0 * float(est[jid]))
                if over > SLOW_OVER_ESTIMATE:
                    items.append(_item("slow", f"bench:{speed}:{jid}", f"bench job {jid} took {j['seconds']:.0f} s, "
                                       f"{over:.1f} x its {est[jid]} min estimate", ev, area, -over))
        if b.get("stopped") and re.search(r"FL wasn't running|FL closed", str(b.get("stopped"))):
            items.append(_item("crash", f"crash:bench_{speed}", f"bench ({speed}) stopped: {b['stopped']}",
                               {"file": b.get("file")}, {"code": "flh_bench.py run"}))
        elif b.get("ran") and b.get("stopped") and not b.get("jobs"):   # stopped before any job: a finding too
            items.append(_item("fail", f"bench:{speed}:not_run", f"bench ({speed}) ran no jobs: {b['stopped']}",
                               {"file": b.get("file"), **({"retry": b["retry"]} if b.get("retry") else {})},
                               {"code": "flh_bench.py _run (preflight) / flh_sweep.py _step_bench",
                                "checklist": "checklists/bench.md"}))
    for step, info in [("pc_check", sw.get("pc_check") or {})] + [
            ("bench_" + k, v or {}) for k, v in sorted((sw.get("bench") or {}).items())]:
        nr = info.get("sandbox_not_recycled")
        if isinstance(nr, dict):                                  # a test sandbox left on the user's disk: said
            items.append(_item("warn", f"cleanup:{step}", f"{step}'s sandbox wasn't recycled: {nr.get('why')}",
                               {"path": nr.get("path")}, {"code": "flh_pccheck.py finish_sandbox / _new_empty_project",
                                                          "checklist": "checklists/sweep.md"}))
    nb = _bench_rows(sw, "normal")
    for jid, j in _bench_rows(sw, "slow").items():
        o = nb.get(jid)
        if o and o.get("status") == "pass" and j.get("status") == "pass" and (o.get("seconds") or 0) > 0 and \
                isinstance(j.get("seconds"), (int, float)):
            x = j["seconds"] / o["seconds"]
            if x > SLOW_X and j["seconds"] - o["seconds"] > NOISE_S:
                items.append(_item("slow", f"bench:slow_x:{jid}", f"{jid} is {x:.1f} x slower on 2 CPU cores than "
                                   "normal (budget PCs feel this)", {"file": ((sw.get("bench") or {}).get("slow") or
                                                                              {}).get("file"), "job": jid},
                                   {"code": ", ".join(_tool_area(t) for t in BENCH_TOOLS.get(jid, ()))}, -x))
    for row in (cmp or {}).get("bench") or []:
        if row.get("verdict") == "slower":
            items.append(_item("regression", f"regress:{row['speed']}:{row['job']}", f"bench job {row['job']} "
                               f"({row['speed']}) got slower: {row['before_s']} -> {row['after_s']} s "
                               f"(+{row['delta_pct']} %)", {"file": ((sw.get("bench") or {}).get(row["speed"]) or {})
                                                           .get("file"), "job": row["job"]},
                               {"code": ", ".join(_tool_area(t) for t in BENCH_TOOLS.get(row["job"], ()))},
                               -float(row.get("delta_pct") or 0)))
        if (row.get("renders_delta") or 0) > 0:
            items.append(_item("regression", f"renders:{row['speed']}:{row['job']}", f"bench job {row['job']} "
                               f"({row['speed']}) renders more: {row['renders']}", {"file": ((sw.get("bench") or {})
                                                                                   .get(row["speed"]) or {}).get("file"),
                                                                                  "job": row["job"]},
                               {"code": ", ".join(_tool_area(t) for t in BENCH_TOOLS.get(row["job"], ()))
                                + "; fl_render_cache.py (reuse)"}, -float(row["renders_delta"])))
    errs = (sw.get("usage") or {}).get("errors") or []
    last_err = {}
    for e in errs:
        last_err[str(e.get("tool"))] = e
    grown = (cmp or {}).get("tool_errors_grew") if prev else None
    if grown:
        for g in grown:
            e = last_err.get(g["tool"])
            items.append(_item("regression", f"tool:{g['tool']}", f"{g['tool']}: {g['delta']} new error(s) since the "
                               f"previous sweep ({g['before']} -> {g['after']})",
                               {"usage": "usage.json", **({"last_error": e.get("error")} if e else {})},
                               {"code": _tool_area(g["tool"])}, -float(g["delta"])))
    elif not prev:
        for t, v in sorted(((sw.get("usage") or {}).get("tools") or {}).items()):
            if v.get("errors", 0) >= 2 and v["errors"] / max(1, v.get("calls", 1)) >= 0.2:
                e = last_err.get(t)
                items.append(_item("warn", f"tool:{t}", f"{t}: {v['errors']} of {v['calls']} calls failed",
                                   {"usage": "usage.json", **({"last_error": e.get("error")} if e else {})},
                                   {"code": _tool_area(t)}, -float(v["errors"])))
    seen, out = set(), []
    for it in sorted(items, key=lambda i: (KINDS.index(i["kind"]), not i["_new"], i["_rank"], i["id"])):
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        out.append(it)
    prev_first = {f.get("id"): f.get("first_seen") for f in (prev or {}).get("fixes") or []}
    for it in out:
        it["first_seen"] = prev_first.get(it["id"]) or sw["stamp"]
        it.pop("_rank", None); it.pop("_new", None)
    return out


# ============================================================================= privacy-safe sharing
_SEG = (r"(?:(?![A-Za-z]:[\\/])(?:[^\\/\"'`\r\n\t|;<>*?:,]|,(?!\s)|:(?![\s\\/])))+")  # a path segment
_PATH_RES = (                                                     # spaces allowed; never runs into the next "X:\\"
    re.compile(r"(?<![\w])[A-Za-z]:[\\/]+(?:" + _SEG + r"(?:[\\/]+" + _SEG + r")*)?[\\/]?"),   # C:\\...
    re.compile(r"\\\\[^\\/\s\"'<>]+(?:[\\/]+" + _SEG + r")+"),                           # \\\\server\\share
    re.compile(r"(?<![\w:/.<>~\\-])/" + _SEG + r"(?:/" + _SEG + r")+/?"))                      # /home/...


def _share_paths(text, state):
    st = state.replace("\\", "/").rstrip("/").lower()

    def rep(m):
        p = m.group(0)
        norm = p.replace("\\", "/")
        low = norm.lower()
        if st and (low == st or low.startswith(st + "/")):
            rel = norm[len(st):].lstrip("/")
            if rel.split("/")[0] in SHARE_STATE_DIRS:
                return "<state>/" + rel
            return "<state>/<path>"
        tail = re.split(r"[\\/]", p.rstrip("\\/"))[-1]
        if tail.lower().endswith(".py") and re.match(r"^[\w.-]+\.py$", tail):
            return "<path>/" + tail                               # code file names help a traceback (not private)
        ext = os.path.splitext(tail)[1].lower()
        return "<path>" + (ext if re.match(r"^\.[a-z0-9]{1,5}$", ext) else "")
    for rx in _PATH_RES:                                          # Windows paths first
        text = rx.sub(rep, text)
    return text


def _private_words():
    """Names that must not leave the PC in a shared zip: the song FL had open (and its folder), the collection."""
    words = set()
    for sw in (_RUN.get("current"),):
        for p in ((sw or {}).get("_private") or []):
            if p:
                words.add(p)
    try:
        c = (H["_state"]() or {}).get("collection")
        if c:
            words.add(str(c))
    except Exception:
        pass
    return sorted((w for w in words if len(w.strip()) >= 3 and w.strip().lower() not in
                   ("untitled", "project", "song", "new", "default", "_fl-headless bench")), key=len, reverse=True)


def share_scrub(v, private=None, state=None):
    """Stricter than flh_bug_report's _scrub: every path -> <path> (our own state sub-folders -> <state>/...,
    code file names kept), the song's / collection's names -> <song>, keys that only carry the user's song dropped,
    then _scrub (user names, e-mails)."""
    private = list(private if private is not None else _private_words())
    state = state if state is not None else _state_dir()
    if isinstance(v, dict):
        return {share_scrub(k, private, state) if isinstance(k, str) else k: share_scrub(x, private, state)
                for k, x in v.items() if k not in DROP_KEYS}
    if isinstance(v, (list, tuple)):
        return [share_scrub(x, private, state) for x in v]
    if not isinstance(v, str):
        return v
    s = _share_paths(v, state)
    for w in private:
        s = re.sub(re.escape(w), "<song>", s, flags=re.I)
    try:
        s = H["_scrub"](s)
    except Exception:
        pass
    return s


def _bug_bundle():
    """flh_bug_report's files (versions, setup, errors, timings, extensions, usage), read back from its zip; the
    zip itself is removed again (one zip for sharing: ours)."""
    out = {}
    try:
        r = H["flh_bug_report"]()
        path = r.get("zip") if isinstance(r, dict) else None
        if not path:
            return out
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                try:
                    out["bug_report/" + n] = json.loads(z.read(n).decode("utf-8"))
                except ValueError:
                    pass
        folder = os.path.join(_state_dir(), "bug_reports")
        if _inside(path, folder) and os.path.basename(path).startswith("flh-bug-"):
            os.remove(path)                                       # our own file, just made
    except Exception as e:                                        # noqa: BLE001
        out["bug_report/error.json"] = {"error": f"{type(e).__name__}"}
    return out


def _make_zip(folder, sw, md, share_pictures):
    private = _private_words(); state = _state_dir()
    files = {"sweep.json": _public(sw), "sweep.md": md}
    pc = sw.get("pc_check") or {}
    if pc.get("report_md") and os.path.isfile(pc["report_md"]):
        with open(pc["report_md"], encoding="utf-8", errors="replace") as f:
            files["pc_check/report.md"] = f.read()
    for speed in ("normal", "slow"):
        p = ((sw.get("bench") or {}).get(speed) or {}).get("file")
        d = _read_json(p) if p else None
        if isinstance(d, dict):
            files[f"bench/{speed}.json"] = d
    files.update(_bug_bundle())
    path = os.path.join(folder, "share.zip")
    pics = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, obj in files.items():
            obj = share_scrub(obj, private, state)
            z.writestr(name, obj if isinstance(obj, str) else json.dumps(obj, indent=1, default=str))
        if share_pictures:
            n = 0
            for it in sw.get("fixes") or []:
                for p in (it.get("evidence") or {}).get("pictures") or []:
                    if os.path.isfile(p) and _inside(p, state) and p not in pics:
                        n += 1; pics.append(p)
                        z.write(p, f"pictures/{n:02d}_{re.sub(r'[^A-Za-z0-9_.-]', '_', os.path.basename(p))[-60:]}")
    return {"zip": path, "size_bytes": os.path.getsize(path), "contains": sorted(files) +
            (["pictures/ (%d)" % len(pics)] if pics else []),
            "privacy": "paths -> <path> (fl-headless's own report folders -> <state>/...), the song's and the "
                       "collection's names -> <song>, user names -> <user>, e-mails -> <email>; no audio, no projects"
                       + ("; pictures of FL's window are included (they can show file names): look before sharing"
                          if pics else ", no pictures (share_pictures=true adds the checks' pictures)")}


def _public(sw):
    return {k: v for k, v in sw.items() if not k.startswith("_")}


# ============================================================================= markdown
def _markdown(sw):
    s = sw["summary"]
    k = s.get("by_kind") or {}
    lines = [f"# fl_sweep {sw['started']} - fl-headless {sw['fl_headless']}", "",
             f"**{s['items']} to fix**: " + ", ".join(f"{k.get(x, 0)} {x}" for x in KINDS) +
             f" - in {sw.get('minutes')} min." + (f" Stopped: {sw['stopped']}" if sw.get("stopped") else ""), ""]
    pcs = s.get("pc_check")
    lines.append("- pc_check: " + (", ".join(f"{v} {n}" for n, v in pcs.items()) if isinstance(pcs, dict) else
                                   str(pcs)))
    for speed in ("normal", "slow"):
        b = (s.get("bench") or {}).get(speed)
        lines.append(f"- bench ({speed}): " + (", ".join(f"{v} {n}" for n, v in b.items()) if isinstance(b, dict)
                                                else str(b)))
    lines.append(f"- tool errors (usage log): {s.get('tool_errors', 0)}")
    lines += ["", "## Fix first", "", "| # | kind | id | what | first seen |", "|---|---|---|---|---|"]
    for i, it in enumerate(sw.get("fixes", [])[:TOP], 1):
        lines.append(f"| {i} | {it['kind']} | {it['id']} | {it['what'].replace('|', '/')[:160]} | {it['first_seen']} |")
    if not sw.get("fixes"):
        lines.append("| - | - | - | nothing to fix | - |")
    c = sw.get("compare") or {}
    lines += ["", "## Compared with " + str(c.get("against") or "nothing (first sweep)")]
    for key, v in (c.get("checks") or {}).items():
        lines.append(f"- {key.replace('_', ' ')}: {', '.join(v)}")
    for r in c.get("bench") or []:
        if r.get("verdict") in ("slower", "faster") or r.get("renders"):
            lines.append(f"- bench {r['job']} ({r['speed']}): {r.get('verdict')} {r.get('before_s')} -> "
                         f"{r.get('after_s')} s" + (f", renders {r['renders']}" if r.get("renders") else ""))
    for g in c.get("tool_errors_grew") or []:
        lines.append(f"- {g['tool']}: errors {g['before']} -> {g['after']}")
    lines += ["", "## Next"] + [f"- {x}" for x in sw.get("next") or []]
    return "\n".join(lines) + "\n"


# ============================================================================= the run
def _summary(sw):
    by = {k: 0 for k in KINDS}
    for it in sw.get("fixes") or []:
        by[it["kind"]] += 1
    pc = sw.get("pc_check") or {}
    out = {"items": len(sw.get("fixes") or []), "by_kind": by,
           "pc_check": (pc.get("summary") if pc.get("ran") else pc.get("note") or "not run"),
           "bench": {}, "tool_errors": sum(v.get("errors", 0) for v in ((sw.get("usage") or {}).get("tools") or {})
                                           .values())}
    for speed in ("normal", "slow"):
        b = (sw.get("bench") or {}).get(speed) or {}
        out["bench"][speed] = b.get("summary") if b.get("ran") else (b.get("note") or "not run")
        if b.get("ran") and b.get("stopped") and not b.get("jobs"):
            out["bench"][speed] = {"jobs": 0, "stopped": _short(b["stopped"], 160)}
    return out


def _next(sw):
    out = []
    if sw.get("stopped"):
        out.append(f"stopped: {sw['stopped']}")
    pc = sw.get("pc_check") or {}
    if pc.get("stopped") and not pc.get("rows"):
        out.append(f"pc_check didn't run: {pc['stopped']}")
    for speed in ("normal", "slow"):
        b = (sw.get("bench") or {}).get(speed) or {}
        if b.get("ran") and b.get("stopped") and not b.get("jobs"):
            out.append(f"bench ({speed}) ran no jobs: {_short(b['stopped'], 200)}")
    fixes = sw.get("fixes") or []
    if any(f["kind"] == "crash" for f in fixes):
        out.append("FL closed (or a step crashed) during the sweep: start FL again (your song was saved by FL first) "
                   "and share the zip")
    if fixes:
        f = fixes[0]
        out.append(f"fix first: {f['id']} ({f['code_area'].get('code')})"
                   + (f" - {f['code_area']['fix_hint']}" if f["code_area"].get("fix_hint") else ""))
        out.append("share the zip with the developer: " + str((sw.get("paths") or {}).get("zip") or "share.zip"))
    for n in sw.get("notes") or []:
        if "skipped" in n:
            out.append(n)
            break
    if not fixes and not sw.get("stopped"):
        out.append("nothing to fix: run fl_sweep() again after an update or a change to compare")
    elif fixes:
        out.append("after fixing: fl_sweep() again - the compare shows what got fixed")
    return out[:6]


def _new_folder(stamp):
    base = _base(); os.makedirs(base, exist_ok=True)
    same = [_order(x)[2] for x in os.listdir(base) if x == stamp or x.startswith(stamp + "-")]
    n = max(same) + 1 if same else 1                              # after every sweep of this second (sort order),
    name = stamp if n == 1 else f"{stamp}-{n}"                    # even when the first of them was pruned
    while os.path.exists(os.path.join(base, name)):
        n += 1; name = f"{stamp}-{n}"
    d = os.path.join(base, name); os.makedirs(d)
    return d, name


def _run(full=True, unattended=True, bench=True, slow=True, compare_ref="last", share_pictures=False):
    T0 = time.time(); _RUN["t0"] = T0
    folder, stamp = _new_folder(_now())
    sw = {"fl_headless": H["SERVER"]["version"], "stamp": stamp, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
          "options": {"full": bool(full), "unattended": bool(unattended), "bench": bool(bench), "slow": bool(slow),
                      "compare": compare_ref if isinstance(compare_ref, str) else bool(compare_ref)},
          "steps": {}, "notes": [], "_private": []}
    _RUN["current"] = sw
    steps_s = {}
    plan = [("pc_check", True), ("bench_normal", bool(bench)), ("bench_slow", bool(slow)), ("usage", True)]
    todo = [p for p, on in plan if on]
    PC = _pc()
    snap = None
    # the user's song, found by the first run that starts (FL's song then: saved by FL, or None = an empty project)
    # and handed to every later run: a later run never takes what an earlier one left in FL (a sandbox song - PC
    # 2026-10-23: the benches reopened pc_check's 'icon probe_2' as "your song") for the song to put back
    user = {}
    if PC is not None:
        PC._RUN["stop"].clear()                                   # fl_pc_check / fl_bench refuse while we run
        try:                                                      # the user's live notes / reload mode, put back
            PC._settings_recover(in_run=True, force=True)         # at the end (also after a crash: state.json)
            snap, _own = PC.settings_save("sweep")
        except Exception as e:                                    # noqa: BLE001
            sw["notes"].append(f"couldn't remember your live notes / reload mode: {type(e).__name__}: {e}")
    try:
        for i, name in enumerate(todo):
            if _RUN["stop"].is_set():
                sw["steps"][name] = {"ran": False, "note": "stopped before it ran"}
                continue
            _RUN["step"] = f"sweep {i + 1}/{len(todo) + 1}: {LABELS[name]}"
            _progress(sweep=_RUN["step"], step=_RUN["step"], pct=None, detail=None)
            t = time.time()
            try:
                if name == "pc_check":
                    r = _step_pc_check(full, unattended, user)
                elif name == "bench_normal":
                    r = _step_bench(False, user)
                elif name == "bench_slow":
                    r = _step_bench(True, user)
                else:
                    r = _step_usage()
            except Exception as e:                                # noqa: BLE001 - one step never sinks the sweep
                r = {"ran": False, "crashed": f"{type(e).__name__}: {_short(e, 240)}",
                     "traceback": traceback.format_exc()[-1500:]}
                try:
                    H["_note_tool_error"](f"fl_sweep:{name}", e, traceback.format_exc())
                except Exception:
                    pass
            steps_s[name] = round(time.time() - t, 1)
            if r.get("note"):
                sw["notes"].append(r["note"])
            sw["steps"][name] = {k: v for k, v in r.items() if k in ("ran", "note", "crashed", "traceback", "fl_gone")}
            if name == "pc_check":
                res = r.get("result") or {}
                sw["checks"] = [{k: c.get(k) for k in ("id", "title", "area", "risk", "status", "detail", "seconds",
                                                       "fix_hint", "checklist", "look", "evidence", "next_step")
                                 if c.get(k) not in (None, "", {}, [])} for c in r.get("rows") or []]
                sw["pc_check"] = {"ran": bool(r.get("ran")), "summary": res.get("summary"), "report": r.get("report"),
                                  "report_md": r.get("report_md"), "stopped": res.get("stopped"),
                                  "bug_report": res.get("bug_report"), "note": r.get("note"),
                                  **({"sandbox_not_recycled": res["sandbox_not_recycled"]}
                                     if res.get("sandbox_not_recycled") else {})}
                if r.get("your_song"):
                    sw["_private"] += [os.path.splitext(os.path.basename(str(r["your_song"])))[0],
                                       os.path.basename(os.path.dirname(str(r["your_song"])))]
            elif name.startswith("bench_"):
                res = r.get("result") or {}
                sw.setdefault("bench", {})[name[6:]] = {
                    "ran": bool(r.get("ran")), "file": r.get("file"), "speed": r.get("speed"),
                    "summary": res.get("summary"), "stopped": res.get("stopped"), "note": r.get("note"),
                    "estimate_min": r.get("estimate_min"), **({"retry": r["retry"]} if r.get("retry") else {}),
                    **({"sandbox_not_recycled": res["sandbox_not_recycled"]} if res.get("sandbox_not_recycled") else {}),
                    "jobs": [{k: j.get(k) for k in ("id", "status", "seconds", "renders", "detail", "steps_s")
                              if j.get(k) not in (None, "", {})} for j in r.get("jobs") or []]}
            else:
                sw["usage"] = {"tools": r.get("tools") or {}, "since": r.get("since"), "errors": r.get("errors") or [],
                               "usage_off": r.get("usage_off")}
        if _RUN["stop"].is_set():
            sw["stopped"] = "stopped with fl_sweep(stop=true)"
        if "usage" not in sw:
            sw["usage"] = _step_usage()
        _RUN["step"] = f"sweep {len(todo) + 1}/{len(todo) + 1}: compare, fix list, report, zip"
        _progress(sweep=_RUN["step"], step=_RUN["step"], pct=95, detail=None, check=None, job=None)
        t = time.time()
        prev_path, prev = _resolve("last", before=stamp)          # first_seen: always the sweep right before
        cmp_path, cmp_prev = (prev_path, prev) if compare_ref in ("last", True) else (
            _resolve(compare_ref) if compare_ref else (None, None))
        sw["compare"] = compare(sw, cmp_prev) if compare_ref else {"against": None, "note": "compare off"}
        if cmp_path:
            sw["compare"]["against_file"] = cmp_path
        sw["fixes"] = build_fixes(sw, sw["compare"] if cmp_prev else None, prev)
        sw["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        sw["minutes"] = round((time.time() - T0) / 60, 1)
        sw["summary"] = _summary(sw)
        jp, mp = os.path.join(folder, "sweep.json"), os.path.join(folder, "sweep.md")
        sw["paths"] = {"folder": folder, "sweep_json": jp, "sweep_md": mp, "zip": os.path.join(folder, "share.zip"),
                       "pc_check_report": (sw.get("pc_check") or {}).get("report_md"),
                       "bench": [b.get("file") for b in (sw.get("bench") or {}).values() if b.get("file")]}
        sw["next"] = _next(sw)
        md = _markdown(sw)
        sw["share"] = _make_zip(folder, sw, md, share_pictures)
        steps_s["report"] = round(time.time() - t, 1)
        sw["steps_s"] = steps_s
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(_public(sw), f, indent=1, default=str)
        with open(mp, "w", encoding="utf-8") as f:
            f.write(md)
        pruned = _prune()
        _RUN["last"] = jp
        _progress(sweep="done", step="done", pct=100, detail=None)
        return _result(sw, pruned)
    finally:
        if PC is not None:
            PC._RUN["running"] = False; PC._RUN["stop"].clear(); PC._RUN["ask"] = None
            if snap is not None:
                try:
                    back = PC.settings_restore(snap)
                    if back:
                        sw["settings_restored"] = back
                except Exception:                                 # noqa: BLE001 - the next start retries (state)
                    pass
        _RUN["current"] = None


def _top(fixes, n=TOP):
    out = []
    for it in fixes[:n]:
        r = {"id": it["id"], "kind": it["kind"], "what": it["what"], "first_seen": it["first_seen"],
             "code": it["code_area"].get("code")}
        for k in ("fix_hint", "checklist"):
            if it["code_area"].get(k):
                r[k] = it["code_area"][k]
        ev = it.get("evidence") or {}
        r["evidence"] = {k: ev[k] for k in ("report", "row", "file", "job", "pictures", "last_error") if ev.get(k)}
        out.append(r)
    return out


def _compare_compact(c):
    out = {"against": c.get("against")}
    for k in ("note", "checks_note"):
        if c.get(k):
            out[k] = c[k]
    if c.get("checks"):
        out["checks"] = c["checks"]
    b = [{k: r[k] for k in ("speed", "job", "verdict", "before_s", "after_s", "delta_pct", "renders") if k in r}
         for r in c.get("bench") or [] if r.get("verdict") != "same" or r.get("renders")]
    if b:
        out["bench"] = b
    elif c.get("bench"):
        out["bench"] = "every job within noise (15 % / 2 s)"
    if c.get("tool_errors_grew"):
        out["tool_errors_grew"] = c["tool_errors_grew"][:10]
    return out


def _result(sw, pruned=None):
    out = {"summary": sw["summary"], "minutes": sw.get("minutes"), "top": _top(sw.get("fixes") or []),
           "compare": _compare_compact(sw.get("compare") or {}), "paths": sw.get("paths"), "next": sw.get("next")}
    if sw.get("stopped"):
        out["stopped"] = sw["stopped"]
    if sw.get("notes"):
        out["notes"] = sw["notes"][:5]
    if sw.get("share"):
        out["share"] = {k: sw["share"][k] for k in ("zip", "size_bytes", "privacy") if k in sw["share"]}
    if pruned:
        out["pruned_old_sweeps"] = len(pruned)
    if sw.get("steps_s"):
        out["steps_s"] = sw["steps_s"]
    if len(sw.get("fixes") or []) > TOP:
        out["more"] = f"{len(sw['fixes']) - TOP} more items: fl_sweep(report='last')"
    return out


# ============================================================================= dry run
def _dry_run(full, unattended, bench, slow, compare_ref):
    plan, minutes, you = [], 0.0, []
    PC, B = _pc(), _bench()
    win = _windows()
    if PC is not None:
        sel, err = PC.plan(None, None, None, bool(full))
        d = PC._dry_run(sel, err, bool(unattended))
        m = float(d["estimated_minutes"]["total"])
        plan.append({"step": "pc_check", "checks": d["count"], "minutes": m, "full": bool(full),
                     "unattended": bool(unattended), **({} if win else {"skipped": "not Windows"})})
        if win:
            minutes += m
        if not unattended:
            you += [x for x in d["you_will"] if ":" in x and x.split(":")[0] in {r["id"] for r in d["checks"]}]
    else:
        plan.append({"step": "pc_check", "skipped": "flh_pccheck isn't loaded"})
    for on, speed in ((bench, "normal"), (slow, "slow")):
        if not on:
            continue
        if B is None or PC is None:
            plan.append({"step": f"bench_{speed}", "skipped": "flh_bench isn't loaded"}); continue
        est = B._estimate(B._select(None)) * (SLOW_FACTOR if speed == "slow" else 1.0)
        plan.append({"step": f"bench_{speed}", "jobs": [j["id"] for j in B.JOBS], "minutes": round(est, 1),
                     **({"cores": B.SLOW_CORES, "note": "rough: FL on 2 CPU cores takes longer"} if speed == "slow"
                        else {}), **({} if win else {"skipped": "not Windows"})})
        if win:
            minutes += est
    plan.append({"step": "usage", "what": "error counts per tool (local usage log) + the last tool errors",
                 "minutes": 0.0})
    plan.append({"step": "report", "what": "compare with the previous sweep, ranked fix list, sweep.json/.md, share.zip",
                 "minutes": 0.1})
    minutes += 0.1
    prev_path, _ = _resolve("last") if compare_ref in ("last", True) else (
        _resolve(compare_ref) if compare_ref else (None, None))
    out = {"dry_run": True, "plan": plan, "estimated_minutes": round(minutes, 1),
           "you_must_do": you if not unattended else [],
           "compare_with": prev_path or ("nothing yet (first sweep)" if compare_ref else "compare off"),
           "saves": os.path.join(_base(), "<time>", "sweep.json | sweep.md | share.zip") + f" (newest {KEEP} kept)",
           "start": "fl_sweep()"}
    if win:
        out["before_start"] = ("FL Studio open on a SAVED song (or its empty new project), no popup; don't use FL "
                               f"for about {round(minutes)} min (it switches between test songs; your song comes back)")
        if unattended:
            out["you_must_do_note"] = "nothing (unattended): checks that need a person are skipped"
    else:
        out["note"] = "not Windows: the FL steps (pc_check, bench) are skipped; usage, compare and the fix list still run"
    return out


# ============================================================================= the tool
def _truthy(v):
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def fl_sweep(full=True, unattended=True, bench=True, slow=True, compare="last", dry_run=False, stop=False,
             status=False, report=None, share_pictures=False, answer=None):
    """See the module docstring. A background job: fl_job(<job>) shows 'sweep i/n' and returns the result."""
    if _truthy(stop):
        if not _RUN["running"]:
            return {"stopped": False, "note": "no sweep is running"}
        _RUN["stop"].set()
        PC = _pc()
        if PC is not None:                                        # the running check / bench stops at its next step
            PC._RUN["stop"].set(); PC._RUN["answer_ev"].set()
        return {"stopping": _RUN["job"], "note": "the current step finishes its FL step, FL gets your song back; "
                                                 "the sweep still writes its report: fl_job(<job>)"}
    if answer is not None:
        PC = _pc()
        if PC is None or not PC._RUN.get("ask"):
            return {"answered": False, "note": "nothing is waiting for an answer"}
        PC._RUN["answer"] = str(answer); PC._RUN["answer_ev"].set()
        return {"answered": True, "to": PC._RUN["ask"], "job": _RUN["job"]}
    if report not in (None, "", False):
        path, sw = _resolve(report)
        if not sw:
            return {"report": None, "note": "no sweep saved yet: fl_sweep() runs one"}
        return {"report": path, "summary": sw.get("summary"), "fixes": _top(sw.get("fixes") or [], 50),
                "compare": _compare_compact(sw.get("compare") or {}), "paths": sw.get("paths"), "next": sw.get("next"),
                "steps_s": sw.get("steps_s")}
    if _truthy(status):
        if _RUN["running"]:
            j = (H.get("JOBS") or {}).get(_RUN["job"], {})
            PC = _pc()
            return {"running": True, "job": _RUN["job"], "step": _RUN.get("step"), "progress": j.get("progress"),
                    **({"waiting_for_you": PC._RUN["ask"]} if PC is not None and PC._RUN.get("ask") else {})}
        path, sw = _resolve("last")
        if not sw:
            return {"running": False, "last": None, "note": "no sweep yet: fl_sweep(dry_run=true) shows the plan"}
        return {"running": False, "last": path, "started": sw.get("started"), "summary": sw.get("summary"),
                "top": _top(sw.get("fixes") or [], 5), "next": sw.get("next")}
    full, unattended, bench, slow = (_truthy(x) for x in (full, unattended, bench, slow))
    share_pictures = _truthy(share_pictures)
    cref = compare if isinstance(compare, str) and compare.strip() and compare.strip().lower() not in (
        "false", "0", "no", "off", "none") else ("last" if compare is True else None)
    if _truthy(dry_run):
        return _dry_run(full, unattended, bench, slow, cref)
    if cref and cref != "last":
        _resolve(cref)                                            # a bad compare stops here, before FL is used
    drives_fl = _windows() and _pc() is not None
    PC, B = _pc(), _bench()
    if PC is not None and PC._RUN.get("running"):
        return {"started": False, "note": "fl_pc_check is running: wait for it (fl_job), then fl_sweep()"}
    if B is not None and B._RUN.get("running"):
        return {"started": False, "note": "fl_bench is running: wait for it (fl_job), then fl_sweep()"}
    if drives_fl:
        first = PC._quick_precheck()
        if first:
            return {"started": False, "you_first": first, "then": "fl_sweep() again (dry_run=true shows the plan)"}
    with _RUN_LOCK:
        if _RUN["running"]:
            return {"already_running": _RUN["job"], "note": "fl_job(<job>) for progress, fl_sweep(stop=true) to stop"}
        _RUN["running"] = True; _RUN["stop"].clear(); _RUN["step"] = None

    def job():
        _RUN["job"] = getattr(H.get("_JOB_LOCAL"), "job", None) or _RUN["job"]
        try:
            return _run(full, unattended, bench, slow, cref, share_pictures)
        finally:
            _RUN["running"] = False; _RUN["step"] = None
    try:
        r = H["_start_job"]("sweep", job, fl=drives_fl)
    except Exception:
        _RUN["running"] = False
        raise
    if isinstance(r, dict) and r.get("job"):
        _RUN["job"] = r["job"]
    else:                                                         # ran inline (inside a job that holds FL)
        return r
    est = _dry_run(full, unattended, bench, slow, None)["estimated_minutes"]
    return dict(r, estimated_minutes=est, steps=[s for s, on in (("pc_check", True), ("bench_normal", bench),
                                                                   ("bench_slow", slow), ("usage", True)) if on],
                note=f"don't use FL meanwhile; fl_job('{r['job']}') shows 'sweep i/n'. Stop: fl_sweep(stop=true)."
                + ("" if unattended else " When progress shows needs_you, answer with fl_sweep(answer='done')."))


def register(ns):
    global H
    H = ns
    S, P = ns["_schema"], ns["_p"]
    BOOL, STR = ns["_BOOL"], ns["_STR"]
    ns["TOOLS"]["fl_sweep"] = (
        fl_sweep,
        "Full health sweep of fl-headless on this PC as ONE background job, turned into a ranked fix list: fl_pc_check "
        "(full + unattended by default), fl_bench at normal speed then slow (2 CPU cores), the local usage log's "
        "error counts per tool and the last tool errors; compared with the previous sweep (checks newly failing / "
        "still failing / fixed / newly passing, bench jobs slower or faster beyond noise (>15 % and >2 s) and render "
        "counts, tools whose errors grew). Fix list ranked crash/FL closed > fail > regression > warn > slow, each "
        "with evidence (report rows, pictures), code area (fix_hint / checklist) and first-seen sweep. Saves "
        "sweeps/<time>/sweep.json + sweep.md + a privacy-safe share.zip (no paths, song or user names, audio or "
        "projects; share_pictures=true adds the checks' pictures); newest 10 kept. Off Windows the FL steps are "
        "skipped. dry_run=true: plan + minutes; status=true: the running sweep or the last summary; report='last' "
        "or a path: one sweep's full fix list; stop=true stops.",
        S(full=BOOL("pc_check: every check (default true; false = the quick run)", True),
          unattended=BOOL("skip checks that need a person (default true)", True),
          bench=BOOL("run fl_bench at normal speed (default true)", True),
          slow=BOOL("run fl_bench in slow mode, FL on 2 CPU cores (default true)", True),
          compare=STR("'last' (default: the previous sweep), a sweep folder / sweep.json / <time>, or 'off'", True),
          dry_run=BOOL("only the plan, estimated minutes and what the user must do (writes nothing)", True),
          stop=BOOL("stop the running sweep (cleans up, reopens the song, still writes the report)", True),
          status=BOOL("the running sweep's progress, or the last sweep's summary", True),
          report=STR("read one sweep: 'last' or a sweep folder / sweep.json / <time>", True),
          share_pictures=BOOL("put the checks' pictures of FL's window into share.zip (default false)", True),
          answer=STR("answer a needs_you prompt of the running pc_check: 'done' or 'skip'", True)))
    ns.setdefault("CROSS_PLATFORM", set()).add("fl_sweep")        # dry_run / status / report / the non-FL steps
    # anywhere; like fl_pc_check it isn't wrapped with the FL lock (its job takes it), so stop / status answer at once
    try:
        ns["TOOL_GROUPS"]["fl_sweep"] = "start"
    except Exception:
        pass
