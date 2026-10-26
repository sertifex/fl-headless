"""Small helpers shared by several extension modules (not an extension itself: no register, no tools).
Every function takes the live server namespace H (ns) and looks server functions up through it at call time.

- tool_call(H, name, args): how a result should name a tool for the model: the direct call when the tool is listed
  to the client (essential tier / chosen toolset), else flh_call("name", {...}) - unlisted tools can't be called
  directly in clients that only allow listed names.
- start_job(H, kind, fn, *a, _fl=..., **k): a background job that holds the FL lock only when it drives FL.
- fl_lock(H): hold the FL lock for the FL part of a job that was started without it.
- shadow_read(H, project): the project file to read, with queued (lazy mode) edits included.
"""
import contextlib
import inspect
import json
import os
import time

_LISTED = {"at": 0.0, "names": None, "key": None}


def listed_tools(H):
    """Names the client lists right now (cached ~2 s: results may name several tools)."""
    H = H or {}
    now = time.time()
    key = (id(H.get("TOOLS")), len(H.get("TOOLS") or ()), len(H.get("ESSENTIAL_TOOLS") or ()))
    if _LISTED["names"] is not None and _LISTED["key"] == key and now - _LISTED["at"] < 2.0:
        return _LISTED["names"]
    names = None
    try:
        fn = H.get("listed_tools")
        if callable(fn):
            names = set(fn())
    except Exception:
        names = None
    if names is None:
        names = set(H.get("ESSENTIAL") or ()) | set(H.get("ESSENTIAL_TOOLS") or ()) | set(H.get("CORE_ALWAYS") or ())
    _LISTED.update(at=now, names=names, key=key)
    return names


class Raw(str):
    """A placeholder in a hint: printed bare in a direct call (project), as "<project>" inside flh_call."""


def _direct(v):
    if isinstance(v, Raw):
        return str(v) if (str(v).isidentifier() or str(v) == "...") else f"<{v}>"
    return json.dumps(v, ensure_ascii=False)


def _jv(v):
    if isinstance(v, Raw):
        return json.dumps(f"<{v}>" if str(v) != "..." else "...", ensure_ascii=False)
    return json.dumps(v, ensure_ascii=False)


def tool_call(H, name, args=None):
    """How a result names a call for the model: 'name(a=1, b="x")' when the tool is listed to the client, else
    'flh_call("name", {"a": 1, "b": "x"})' (unlisted tools can't be called directly in most clients).
    args: dict; Raw("project") values are placeholders (project / "<project>"); a key "..." adds a trailing '...'
    (more arguments as needed)."""
    args = dict(args or {})
    more = args.pop("...", None) is not None
    if name in listed_tools(H):
        parts = [f"{k}={_direct(v)}" for k, v in args.items()] + (["..."] if more else [])
        return f"{name}(" + ", ".join(parts) + ")"
    parts = [f'"{k}": {_jv(v)}' for k, v in args.items()] + (["..."] if more else [])
    return f'flh_call("{name}", {{' + ", ".join(parts) + "})"


def tool_name(H, name):
    """A bare mention of a tool: 'name' when listed, else 'flh_call("name", ...)'."""
    return name if name in listed_tools(H) else f'flh_call("{name}", ...)'


def _job_flag(sj):
    try:
        ps = inspect.signature(sj).parameters
    except (TypeError, ValueError):
        return None
    for p in ("fl", "drives_fl", "uses_fl", "hold_fl"):
        if p in ps and ps[p].kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            return p
    return None


def start_job(H, kind, fn, /, *a, _fl=True, _own_kind=False, **k):
    """H['_start_job'] that holds the FL lock only when _fl is true (flag names start with '_': fn's own keyword
    arguments, e.g. kind=, pass through untouched). Uses the server's explicit flag when it has one;
    older servers: file-only jobs get a kind '<kind>_files' registered as a non-FL job (own_kind: the kind itself is
    registered - for a job kind that never holds the lock for its whole run, e.g. one using fl_lock for its FL part)."""
    sj = H["_start_job"]

    def run():                                  # fn's arguments never pass through _start_job's own signature
        return fn(*a, **k)                      # (a tool argument named kind= / fn= would clash there)
    flag = _job_flag(sj)
    if flag:
        return sj(kind, run, **{flag: bool(_fl)})
    if not _fl:
        nf = H.get("NON_FL_JOBS")
        if kind in (nf or ()):
            return sj(kind, run)
        try:
            kk = kind if _own_kind else kind + "_files"
            nf.add(kk)
            return sj(kk, run)
        except (AttributeError, TypeError):
            pass
    return sj(kind, run)


def auto_job(H, kind, est_s, fn, kw, background, fl=True):
    """H['_auto_job'] (long -> background job, short -> inline) with the FL lock only when fl is true."""
    if background is None:
        background = est_s is None or est_s > 40
    if background:
        r = start_job(H, kind, fn, _fl=fl, **kw)
        r["note"] = (f"running in the background (about {int(est_s) if est_s else '?'} s): fl_job('{r['job']}') "
                     "for the result" + ("" if fl else "; it works on files only, so FL tools stay free meanwhile"))
        if est_s:
            r["eta_s"] = int(round(est_s))
        return r
    return fn(**kw)


@contextlib.contextmanager
def fl_lock(H, what=None):
    """Hold the FL lock (re-entrant) for the FL part of a job started without it; waits for whoever has FL and shows
    that as the job's progress. Released on exit, so the file-only rest of the job leaves FL free."""
    lock = H.get("_FL_LOCK")
    if lock is None:
        yield
        return
    t0 = time.time()
    prog = H.get("_job_progress") or (lambda **_k: None)
    while not lock.acquire(timeout=1.0):
        try:
            busy = H["_fl_busy_with"]()
        except Exception:
            busy = "another FL step"
        prog(step=f"waiting for FL ({busy})", waited_s=round(time.time() - t0, 1))
    holder = H.get("_FL_HOLDER")
    prev = holder.get("what") if isinstance(holder, dict) else None
    jl = H.get("_JOB_LOCAL")
    jid = getattr(jl, "job", None) if jl is not None else None
    if isinstance(holder, dict) and jid and not prev:
        holder["what"] = what or f"job {jid} (fl_job)"
    try:
        yield
    finally:
        if isinstance(holder, dict) and jid and not prev:
            holder["what"] = None
        lock.release()


def shadow_read(H, project):
    """(path to read, n queued edits): the shadow copy (project + edits queued in lazy mode) when edits are queued,
    else the project itself."""
    try:
        if not project or not os.path.isfile(project):
            return project, 0
        q = H["_pending_get"](project) if "_pending_get" in H else []
        if not q:
            return project, 0
        sh, _sig = H["_shadow"](project)
        return sh, len(q)
    except KeyError:
        return project, 0


def shadow_note(n):
    return f"{n} queued edit(s) are included here (read from the queued state) but not in FL yet; flh_sync() sends them"


def unshadow(H, r, sh, project, n):
    """Put the project's path back where a result names the shadow copy, and say the queued edits are included."""
    if not sh or sh == project:
        return r
    if "_unshadow" in H:                                          # (n=0: only the paths are put back)
        return H["_unshadow"](r, sh, project, n, read=True)
    if isinstance(r, dict) and n:
        r["pending_ops"] = n
        r["pending_note"] = shadow_note(n)
    return r


_CALL_RE = None


def hint_text(H, text):
    """Rewrite 'tool(a=1, b=2)' mentions in a ready-made text (data files, shipped tips) for tools the client doesn't
    list into 'flh_call("tool", {"a": 1, "b": 2})'. Listed tools and unknown names stay as they are."""
    global _CALL_RE
    import re
    if _CALL_RE is None:
        _CALL_RE = re.compile(r"\b((?:fl|flp|flh|audio|files)_[a-z0-9_]+)\(([^()]*)\)")
    if not isinstance(text, str) or "(" not in text:
        return text
    T = (H or {}).get("TOOLS") or {}
    listed = listed_tools(H)

    def sub(m):
        name, args = m.group(1), m.group(2).strip()
        if name in listed or (T and name not in T):
            return m.group(0)
        body = re.sub(r"(?<![\w\"'])([A-Za-z_]\w*)=(?!=)", r'"\1": ', args)
        return f'flh_call("{name}", {{{body}}})'
    return _CALL_RE.sub(sub, text)
