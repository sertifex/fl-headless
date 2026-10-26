"""flh_finish: finish a piece in ONE call - support layers, a performance, a rule-aware ending, the FINAL.

flp_finish_piece   what took separate steps (and several FL reloads and renders) on a real PC, run through the existing
                   tools in order, every file edit inside ONE save + reload of the open project:
                     1 layers   flp_compose(what='layer', roles=[...], bars='all', level='support')   (any meter)
                     2 perform  flp_perform_setup(style) + flp_perform (the original parts only: the new layers keep
                                their own realism and follow the shared tempo map)
                     3 ending   flp_extend_ending (hold computed from the collection's rules through the tempo map)
                     4 predict  fl_ending_check(render=False): a predicted fail is fixed (a longer hold, in the same
                                reload) BEFORE anything renders
                     5 final    fl_finalize (fade auto fades a held final chord; it checks the rules itself). A failed
                                rule whose fix is mechanical (hold longer) is applied and the final made once more
                                (at most one retry, said so).
                   Defaults come from the song journal / collection profile ("finish_piece": {"layers": [...],
                   "perform_style": "..."}), else neutral (no layers, flp_perform_setup's own style choice).
                   dry_run: the plan with an estimate (nothing written).
"""
import math
import os
import re
import sys
import time

H = None
MECHANICAL = ("ending_length", "tail_ring")      # rules a longer hold fixes (rule_fixes names hold_s / seconds)
_CARRY = ("reloaded_in_fl", "verify_job", "fl_shows_old_version", "WARNING", "previous_edit_held",
          "queued_ops_applied_first", "fl_has_open", "verified")
_HOLD_RE = re.compile(r'hold_s["\']?\s*[:=]\s*(\d+(?:\.\d+)?)')
_SECS_RE = re.compile(r'seconds["\']?\s*[:=]\s*(\d+(?:\.\d+)?)')


# ----------------------------------------------------------------------------- helpers
def _mod(name):
    m = sys.modules.get(name)
    if m is None:
        try:
            m = __import__(name)
        except Exception:                                         # noqa: BLE001 - optional helper module
            m = None
    return m


def _tool(name):
    t = (H.get("TOOLS") or {}).get(name)
    if not t:
        raise RuntimeError(f"{name} isn't available in this server (update fl-headless)")
    return t[0]


def _store():
    import flh_store
    return flh_store


def _profile():
    try:
        return _store().profile_get() or {}
    except Exception:                                             # noqa: BLE001
        return {}


def _journal(project):
    try:
        return _store().journal_get(project) or {}
    except Exception:                                             # noqa: BLE001
        return {}


def _fp(d):
    v = d.get("finish_piece") if isinstance(d, dict) else None
    return v if isinstance(v, dict) else {}


def _rules_for(project):
    """(rules with defaults, the profile's own rules, where from) - (None, None, why) without rules or when the song
    is exempt. flh_shape's reading (exemptions included) when it's loaded."""
    sh = _mod("flh_shape")
    f = getattr(sh, "_profile_rules", None) if sh else None
    if callable(f):
        try:
            R, rl = f(project)
            return (R, rl, "collection profile") if R is not None else (None, None, "no rules (or the song is exempt)")
        except Exception:                                         # noqa: BLE001
            pass
    rl = _profile().get("rules")
    if isinstance(rl, dict) and rl:
        return dict(rl), rl, "collection profile"
    return None, None, "no rules in the collection profile"


def _flag(v, keep=()):
    """'false' / 'no' / 'off' / '0' (the dispatcher leaves strings alone for ["boolean", "string"] arguments) ->
    False, 'true' / 'yes' / 'on' / '1' -> True; other strings (a style, 'force', 'auto', a word in keep) as given."""
    if isinstance(v, str):
        t = v.strip().lower()
        if t in keep:
            return v
        if t in ("false", "no", "off", "0", "none", ""):
            return False
        if t in ("true", "yes", "on", "1"):
            return True
    return v


def _norm_layers(layers):
    if layers is None:
        return None
    if isinstance(layers, str):
        return [x.strip() for x in re.split(r"[,;+]", layers) if x.strip()]
    if isinstance(layers, (list, tuple)):
        return [x for x in layers if x not in (None, "")]
    raise ValueError("layers: a list of roles, e.g. ['low strings', 'strings', 'choir'] (or [] for none)")


def _role_name(x):
    return str(x.get("role") or x.get("name") or "") if isinstance(x, dict) else str(x)


def _note_channels(project):
    """Channels that play notes in the song now (before any layer is added)."""
    try:
        d = H["_load"](project)
        notes, _tm = H["_song_x"](d, H["_events"](d))
        return sorted({int(n[2]) for n in notes})
    except Exception:                                             # noqa: BLE001
        return None


def _hint(name, args):
    try:
        import flh_common as C
        return C.tool_call(H, name, args)
    except Exception:                                             # noqa: BLE001
        return f"{name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"


def _pick(r, keys):
    return {k: r[k] for k in keys if isinstance(r, dict) and r.get(k) not in (None, [], {}, "")}


def _num_fix(fixes):
    """The hold a failed ending rule's fix asks for: ('hold_s', N) / ('seconds', N) / None."""
    for f in fixes or []:
        if not isinstance(f, dict) or f.get("rule") not in MECHANICAL:
            continue
        txt = str(f.get("fix") or "")
        m = _HOLD_RE.search(txt)
        if m:
            return "hold_s", float(m.group(1))
        m = _SECS_RE.search(txt)
        if m:
            return "seconds", float(m.group(1))
    return None


# ----------------------------------------------------------------------------- the plan
def _plan(project, layers, perform, ending, final, song):
    prof, j = _profile(), _journal(project)
    perform, final = _flag(perform, keep=("none",)), _flag(final)     # 'none' is a performance style
    guessed = []
    lay = _norm_layers(layers)
    if lay is None:
        if _fp(j).get("layers") is not None:
            lay, lsrc = _norm_layers(_fp(j)["layers"]), "song journal (finish_piece.layers)"
        elif _fp(prof).get("layers") is not None:
            lay, lsrc = _norm_layers(_fp(prof)["layers"]), "collection profile (finish_piece.layers)"
        else:
            lay, lsrc = [], "none (no layers given, none in the journal or profile)"
        guessed.append(f"layers: {lsrc}")
    else:
        lsrc = "given"
    style, ssrc = None, None
    if perform is not False and perform is not None:
        if isinstance(perform, str) and perform.strip().lower() not in ("true", "yes", "auto"):
            style, ssrc = perform.strip().lower(), "given"
        else:
            auto = (j.get("performance") or {}).get("auto") if isinstance(j.get("performance"), dict) else None
            if _fp(j).get("perform_style"):
                style, ssrc = str(_fp(j)["perform_style"]).lower(), "song journal (finish_piece.perform_style)"
            elif isinstance(auto, dict) and auto.get("style"):
                style, ssrc = str(auto["style"]).lower(), "the song's earlier performance setup"
            elif _fp(prof).get("perform_style"):
                style, ssrc = str(_fp(prof)["perform_style"]).lower(), "collection profile (finish_piece.perform_style)"
            else:
                ssrc = ("flp_perform_setup's default (natural) - a guess: perform='expressive' gives classical / solo "
                        "piano rubato")
            guessed.append(f"performance style: {style or 'natural'} from {ssrc}")
        P = _mod("flh_perform")
        styles = getattr(P, "STYLES", None) if P else None
        if style and isinstance(styles, dict) and style not in styles:
            raise ValueError(f"perform: a style ({' | '.join(styles)}) or true/false (got '{style}')")
    hold = None
    if ending is not False and ending is not None and not isinstance(ending, bool):
        try:
            hold = float(ending)
        except (TypeError, ValueError):
            raise ValueError("ending: true (rules decide the hold), false, or seconds the final chord sounds")
        if not 0 < hold <= 240:
            raise ValueError("ending: 0..240 seconds (the final chord from its attack)")
    R, rl, rsrc = _rules_for(project)
    fmode = "force" if isinstance(final, str) and final.strip().lower() == "force" else bool(final)
    return {"project": project, "song": song, "layers": lay, "layers_from": lsrc, "perform": perform not in (False, None),
            "style": style, "style_from": ssrc, "ending": ending not in (False, None), "hold_s": hold,
            "rules": R, "rules_raw": rl, "rules_from": rsrc, "final": fmode, "guessed": guessed}


def _estimate(name, args):
    try:
        e = H["estimate"](name, args)
        return float(e.get("estimate_s") or 0), str(e.get("basis") or "")
    except Exception as ex:                                       # noqa: BLE001
        return 1.0, f"no estimate ({type(ex).__name__})"


def _reload_s(project):
    try:
        c = (H["project_load_cost"](project) or {}).get("seconds") or 6.0
    except Exception:                                             # noqa: BLE001
        c = 6.0
    return float(H.get("SAVE_RELOAD_S", 2.5)) + float(c)


def _dry(plan):
    p = plan["project"]
    rows, total, reloads = [], 0.0, 0
    rl_s = _reload_s(p)

    def add(step, tool, args, extra=None):
        nonlocal total, reloads
        est, basis = _estimate(tool, dict(args, project=p))
        if "save + reload" in basis:                              # the batch pays ONE save + reload for every edit
            if reloads:
                est = max(0.5, est - rl_s); basis += " (shared: one reload for all edits)"
            reloads += 1
        row = {"step": step, "tool": tool, "estimate_s": round(est, 1)}
        if args:
            row["args"] = args
        if extra:
            row.update(extra)
        rows.append(row); total += est
    if plan["layers"]:
        args = {"what": "layer", "roles": plan["layers"], "bars": "all", "level": "support"}
        extra = {}
        try:
            r = _tool("flp_compose")(project=p, dry_run=True, **args)
            extra = _pick(r, ("layers", "meter", "guesses"))
            if r.get("new_channels"):
                extra["new_channels"] = [_pick(c, ("channel", "role", "name", "sound_needed")) for c in r["new_channels"]]
        except Exception as e:                                    # noqa: BLE001 - the plan says it
            extra = {"error": f"{type(e).__name__}: {e}"}
        add("layers", "flp_compose", args, extra)
    if plan["perform"]:
        add("perform", "flp_perform_setup", {"style": plan["style"]} if plan["style"] else {})
        add("perform", "flp_perform", {})
    if plan["ending"]:
        args = {"hold_s": plan["hold_s"]} if plan["hold_s"] else {}
        extra = {}
        try:
            r = _tool("flp_extend_ending")(project=p, dry_run=True, **args)
            extra = _pick(r, ("mode", "hold", "rules", "note"))
            if plan["layers"] or plan["perform"]:
                extra["note"] = "computed on the song as it is now; it runs again after the layers / performance"
        except Exception as e:                                    # noqa: BLE001
            extra = {"error": f"{type(e).__name__}: {e}"}
        add("ending", "flp_extend_ending", args, extra)
    if plan["rules"] is not None and plan["final"]:
        extra = {}
        try:
            c = H["fl_ending_check"](p, rules=plan["rules_raw"], render=False)
            extra = {"now": _pick(c, ("predicted", "hold_s", "need_hold_s")),
                     "note": "the song as it is now (before the ending step); a fail after it is fixed before rendering"}
        except Exception as e:                                    # noqa: BLE001
            extra = {"error": f"{type(e).__name__}: {e}"}
        rows.append(dict({"step": "predict", "tool": "fl_ending_check", "args": {"render": False},
                          "estimate_s": 0.5}, **extra)); total += 0.5
    if plan["final"]:
        add("final", "fl_finalize", {"song": plan["song"]} if plan["song"] else {})
    out = {"dry_run": True, "project": p, "plan": rows, "estimate_s": round(total, 1),
           "fl_reloads": 1 if reloads else 0, "renders": 1 if plan["final"] else 0,
           "rules": plan["rules"], "rules_from": plan["rules_from"],
           "note": ("estimates are guesses from this session's timings, the FL load cost and the learned render "
                    "speed; a failed rule after the render may cost one more render (one retry at most)")}
    if plan["guessed"]:
        out["guessed"] = plan["guessed"]
    if any("new_channels" in r for r in rows) and plan["final"] != "force":
        out["final_note"] = ("the layers need new channels (cloned from an existing sound): the final is skipped until "
                             "they have their own sounds (or final='force')")
    return out


# ----------------------------------------------------------------------------- the run
def _call(rec, step, fn, **kw):
    """Run one sub-tool, time it, record ok/error. -> (ok, result)."""
    t = time.time()
    try:
        H["_job_progress"](step=step)
    except Exception:                                             # noqa: BLE001
        pass
    try:
        r = fn(**kw)
        ok = True
    except Exception as e:                                        # noqa: BLE001 - reported with the partial results
        r = f"{type(e).__name__}: {e}"
        ok = False
    rec["steps_s"][step] = round(rec["steps_s"].get(step, 0.0) + time.time() - t, 2)
    return ok, r


def _fail(rec, step, err):
    rec["steps"].append({"step": step, "ok": False, "error": str(err)[:400]})
    rec["errors"].append(f"{step}: {str(err)[:200]}")


def _extend(project, **kw):
    return _tool("flp_extend_ending")(project=project, ritard=0.0, **kw)


def _edits(plan, rec):
    """Every file edit of the run, inside ONE save + reload (the caller's _with_open_project). Stops at the first
    failing step; what already ran stays (the reload still happens, so FL never holds an older copy)."""
    p = plan["project"]
    before_ch = _note_channels(p)
    layer_ch = set()
    # 1 layers
    if plan["layers"]:
        cm = _mod("flh_compose")
        sc = getattr(cm, "scratch", None) if cm else None

        def compose():
            args = dict(project=p, what="layer", roles=plan["layers"], bars="all", level="support")
            if sc is None:
                return _tool("flp_compose")(**args)
            # PC-VERIFY: inside the batch flp_compose doesn't ask FL to save (no second save over the edits)
            with sc(p):                                           # inside this save+reload: write the file only
                return _tool("flp_compose")(**args)               # (no FL save of its own, no lazy queue)
        ok, r = _call(rec, "layers", compose)
        if not ok:
            _fail(rec, "layers", r)
            return
        row = {"step": "layers", "ok": True, **_pick(r, ("layers", "level", "meter", "verified", "guesses"))}
        if r.get("new_channels"):
            row["new_channels"] = [_pick(c, ("channel", "role", "name", "sound_needed")) for c in r["new_channels"]]
            rec["new_channels"] = row["new_channels"]
        if isinstance(r.get("clashes"), dict) and r["clashes"].get("verdict"):
            row["clashes"] = r["clashes"]["verdict"]
        if r.get("verified") is False:                            # written, but it didn't read back as planned
            row["ok"] = False
            row["why"] = "the layers didn't read back as planned: " + str(r.get("verify_problems") or "")[:200]
        rec["steps"].append(row)
        for x in (r.get("layers") or {}).values():
            if isinstance(x, dict) and x.get("channel") is not None:
                layer_ch.add(int(x["channel"]))
        for c in r.get("new_channels") or []:
            layer_ch.add(int(c["channel"]))
        rec["layer_channels"] = sorted(layer_ch)
        rec["human"].append("the layers' balance under the music (level 'support' is a guess)")
    # 2 performance: the original parts (layers keep their realism and follow the tempo map)
    if plan["perform"]:
        prev = set(int(c) for c in (_fp(_journal(p)).get("layer_channels") or []))
        parts = [c for c in (before_ch or []) if c not in layer_ch and c not in prev] or None
        kw = {"project": p}
        if plan["style"]:
            kw["style"] = plan["style"]
        if parts and (layer_ch or prev):
            kw["parts"] = parts
        ok, s = _call(rec, "perform", _tool("flp_perform_setup"), **kw)
        if not ok:
            _fail(rec, "perform", f"flp_perform_setup: {s}")
            return
        ok, r = _call(rec, "perform", _tool("flp_perform"), project=p)
        if not ok:
            _fail(rec, "perform", f"flp_perform: {r}")
            return
        row = {"step": "perform", "ok": True, "style": s.get("style") or plan["style"],
               "parts": sorted((s.get("parts") or {}).keys())[:8], **_pick(r, ("write", "sections_changed"))}
        if isinstance(r.get("edit"), str):
            row["edit"] = r["edit"]
        w = list(s.get("warnings") or []) + list(r.get("warnings") or [])
        if w:
            row["warnings"] = w[:4]
        if isinstance(r.get("verify"), dict) and r["verify"].get("ok") is False:
            row["verify"] = False; row["ok"] = False              # the performance's own read-back check failed
            row["why"] = "flp_perform's check failed: " + str(r["verify"].get("problems") or "")[:200]
        rec["steps"].append(row)
        rec["human"].append(f"the performance ({row['style'] or 'natural'}): rubato, dynamics, phrasing")
    # 3 ending
    if plan["ending"]:
        ok, r = _call(rec, "ending", _extend, project=p, **({"hold_s": plan["hold_s"]} if plan["hold_s"] else {}))
        if not ok:
            _fail(rec, "ending", r)
            return
        rec["steps"].append({"step": "ending", "ok": True, **_pick(r, ("mode", "hold", "held_notes", "clips_grown",
                                                                        "added_s", "note"))})
        rec["human"].append("the last chord's ring and diminuendo")
    _predict(plan, rec, fix=plan["ending"])


def _predict(plan, rec, fix=True):
    """4: will the ending pass the rules? From the notes, no render. A fail / borderline is fixed now (longer hold)."""
    if plan["rules"] is None or not plan["final"]:
        return
    p = plan["project"]
    chk = lambda: H["fl_ending_check"](p, rules=plan["rules_raw"], render=False)
    ok, c = _call(rec, "predict", chk)
    if not ok:
        rec["steps"].append({"step": "predict", "ok": False, "error": str(c)[:300]})
        return
    row = {"step": "predict", "ok": True, **_pick(c, ("predicted", "hold_s", "need_hold_s"))}
    rec["predicted"] = c.get("predicted")
    if c.get("predicted") in ("fail", "borderline") and c.get("need_hold_s"):
        want = int(math.ceil(float(c["need_hold_s"])))
        if not fix:
            row["fix"] = c.get("fix") or _hint("flp_extend_ending", {"project": p, "hold_s": want})
            row["note"] = "ending=false: not changed - the final will likely fail the ending rules"
        else:
            ok, r = _call(rec, "predict_fix", _extend, project=p, hold_s=want)
            if not ok:
                row["fix_error"] = str(r)[:300]
            else:
                row["fixed_before_render"] = {"hold_s": want, "was_s": c.get("hold_s"),
                                              "held_notes": r.get("held_notes")}
                ok2, c2 = _call(rec, "predict", chk)
                if ok2:
                    row["predicted_after_fix"] = c2.get("predicted")
                    rec["predicted"] = c2.get("predicted")
                    if c2.get("predicted") == "fail":
                        row["note"] = ("still predicted to fail after the longer hold: the check measures the ending "
                                       "differently from the hold that was written - a human should look")
    rec["steps"].append(row)


def _file_crc(p):
    try:
        import zlib
        with open(p, "rb") as f:
            return zlib.crc32(f.read())
    except OSError:
        return None


def _fl_reload_for_final(p, br, rec, step="fl_load"):
    """The FINAL is rendered by FL's own export when FL is open - and that renders what FL has LOADED, not the file.
    After the file edits FL must hold the edited file: when the batch didn't reload FL (FL showed another project,
    a same-title twin, or its reload failed), FL loads it now - proven by a save for a same-title twin, waited for
    through the whole load - BEFORE anything renders (PC 2026-10-23: the ending's +20 s missing from the FINAL).
    A failure is an error of this step (the final is then skipped: never a FINAL of an older copy)."""
    if not H.get("_fl_load_file"):
        return True
    try:
        if not H["fl_running"]():
            return True                                           # the command line renders the file itself
    except Exception:                                             # noqa: BLE001
        return True
    if isinstance(br, dict) and br.get("reloaded_in_fl") is True and not br.get("fl_shows_old_version"):
        return True                                               # the batch's own save + reload: FL has the edits
    try:
        route = H["_render_method"](p, "wav", "auto") if H.get("_render_method") else "cli"
    except Exception:                                             # noqa: BLE001
        route = "cli"
    if route != "in_fl":
        return True                                               # FL closes for the command line: it reads the file
    ok, r = _call(rec, step, H["_fl_load_file"], project=p, why="final")
    if not ok:
        _fail(rec, step, f"FL didn't load the edited file before the final ({r}); no final was rendered, so none can "
                         "be of an older copy. fl_open the project (it waits for the whole load), then fl_finalize")
        return False
    rec["steps"].append({"step": step, "ok": True, "how": (r or {}).get("how"),
                         "why": "the final renders what FL has loaded: FL now holds the edited file"})
    return True


def _final_row(fr):
    """(row, the final item, rules dict or None) from fl_finalize's result."""
    item = ((fr or {}).get("finals") or [{}])[0] if isinstance(fr, dict) else {}
    row = {"step": "final", "ok": bool(isinstance(fr, dict) and fr.get("ok", True) and not item.get("error"))}
    if item.get("error"):
        row["error"] = str(item["error"])[:300]
        for k in ("kept_wav", "next"):
            if item.get(k):
                row[k] = item[k]
    else:
        row.update(_pick(item, ("path", "lufs", "true_peak", "length", "fade_s", "limited_db_max", "max_lufs",
                                "below_target_db", "warning", "render_note")))
    rules = item.get("rules") if isinstance(item.get("rules"), dict) else None
    return row, item, rules


def _run(plan):
    T0 = time.time()
    p = plan["project"]
    n0 = H["_renders_here"]() if H.get("_renders_here") else None
    rec = {"steps": [], "steps_s": {}, "errors": [], "human": [], "new_channels": None, "layer_channels": [],
           "predicted": None}
    res = {"project": p}
    # ---- the edits: ONE save + reload of the open project for all of them
    if plan["layers"] or plan["perform"] or plan["ending"]:
        t = time.time(); crc0 = _file_crc(p); br = None
        try:
            # PC-VERIFY: layers + perform + ending on the song open in FL: ONE FL save, ONE reload, FL shows all three
            br = H["_with_open_project"](p, p, lambda: (_edits(plan, rec) or {"output": p}))
            fl = {k: br[k] for k in _CARRY if isinstance(br, dict) and k in br}
            if fl:
                res["fl"] = fl
        except Exception as e:                                    # noqa: BLE001 - FL busy / can't tell / ...
            _fail(rec, "edits", f"{type(e).__name__}: {e}")
        inner = sum(v for k, v in rec["steps_s"].items())
        rec["steps_s"]["save_reload"] = round(max(0.0, time.time() - t - inner), 2)
        if plan["final"] and not rec["errors"] and _file_crc(p) != crc0:
            _fl_reload_for_final(p, br, rec)                      # FL must hold the edits before the final renders
    else:
        _predict(plan, rec, fix=False)
    # ---- the final
    retry = None
    if plan["final"]:
        if rec["errors"]:
            rec["steps"].append({"step": "final", "ok": False, "skipped": "an earlier step failed: fix it, then "
                                 "flp_finish_piece again (steps that worked are in the project)"})
        elif rec["new_channels"] and plan["final"] != "force":
            chs = ", ".join(f"{c.get('channel')} {c.get('name') or c.get('role')}" for c in rec["new_channels"])
            rec["steps"].append({"step": "final", "ok": False, "skipped": (
                f"new layer channel(s) {chs} are clones of an existing sound: load their own sounds first "
                "(fl_audition / flp_instrument_sound), then " + _hint("flp_finish_piece", {
                    "project": p, "layers": [], "perform": False, "ending": False}) +
                " - or final='force' to render as they are")})
            rec["human"].append("pick sounds for the new layer channels")
        else:
            kw = {"project": p, "background": False}
            if plan["song"]:
                kw["song"] = plan["song"]
            # PC-VERIFY: fl_finalize inside this FL-holding job renders (no 'FL is busy' wait on its own job)
            ok, fr = _call(rec, "final", _tool("fl_finalize"), **kw)
            if not ok:
                _fail(rec, "final", fr)
            else:
                row, item, rules = _final_row(fr)
                failed = [c for c in (rules or {}).get("failed") or []]
                fx = _num_fix((rules or {}).get("fixes"))
                if row["ok"] and failed and fx and plan["ending"]:
                    others = [f for f in failed if f not in MECHANICAL]
                    kind, val = fx
                    t = time.time()
                    try:
                        er = H["_with_open_project"](p, p, lambda: _extend(p, **{kind: val}))
                        rec["steps_s"]["retry_fix"] = round(time.time() - t, 2)
                        retry = {"why": failed, "fix": {kind: val}, "held_notes": (er or {}).get("held_notes")}
                    except Exception as e:                        # noqa: BLE001
                        retry = {"why": failed, "fix_error": f"{type(e).__name__}: {e}"}
                    if "fix_error" not in retry and retry.get("held_notes") == 0:
                        retry["note"] = ("flp_extend_ending found nothing to hold longer (the chord already sounds that "
                                         "long by its measure), so no second render: a human should look at the ending")
                    elif "fix_error" not in retry and not others and \
                            not _fl_reload_for_final(p, er if isinstance(er, dict) else None, rec, "fl_load_retry"):
                        retry["error"] = "FL didn't load the longer hold: no second render"
                    elif "fix_error" not in retry and not others:
                        ok2, fr2 = _call(rec, "final_retry", _tool("fl_finalize"), **kw)
                        if ok2:
                            row2, item2, rules2 = _final_row(fr2)
                            row2["retry"] = True
                            row, item, rules = row2, item2, rules2
                            retry["finalized_again"] = True
                        else:
                            retry["error"] = str(fr2)[:300]
                    elif others:
                        retry["note"] = (f"the hold is longer now, but {', '.join(others)} also failed (not mechanical): "
                                         "fix that, then fl_finalize once - no second render was spent")
                rec["steps"].append(row)
                if rules is not None:
                    res["rules"] = {"pass": rules.get("pass"),
                                    "failed": rules.get("failed") or [c.get("rule") for c in rules.get("checks") or []
                                                                      if c.get("pass") is False]}
                    if rules.get("fixes") and not rules.get("pass"):
                        res["rules"]["fixes"] = rules["fixes"][:4]
                elif plan["rules"] is None:
                    res["rules"] = {"pass": None, "note": plan["rules_from"]}
                if row.get("path"):
                    res["final"] = _pick(row, ("path", "lufs", "true_peak", "length"))
                rec["human"].append("the FINAL as a whole: rules passing doesn't mean it sounds right")
    if retry:
        res["retry"] = retry
    res["ok"] = (not rec["errors"] and all(s.get("ok") for s in rec["steps"] if s["step"] != "predict")
                 and (res.get("rules") or {}).get("pass") is not False)     # a final that fails the rules isn't done
    res["steps"] = rec["steps"]
    res["steps_s"] = dict(rec["steps_s"], total=round(time.time() - T0, 1))
    if n0 is not None:
        res["renders"] = H["_renders_here"]() - n0
    if rec["predicted"]:
        res["predicted_ending"] = rec["predicted"]
    if rec["errors"]:
        res["errors"] = rec["errors"]
    if plan["guessed"]:
        res["guessed"] = plan["guessed"]
    if rec["human"]:
        res["listen"] = list(dict.fromkeys(rec["human"]))
    try:                                                          # the song remembers what finished it
        done = {"when": time.strftime("%Y-%m-%d %H:%M"), "layers": [_role_name(x) for x in plan["layers"]],
                "perform_style": next((s.get("style") for s in rec["steps"] if s["step"] == "perform"), None),
                "ok": res["ok"]}
        lc = sorted(set(rec["layer_channels"]) | set(int(c) for c in (_fp(_journal(p)).get("layer_channels") or [])))
        st = {"last": done}
        if lc:
            st["layer_channels"] = lc
        _store().journal_update(p, set={"finish_piece": st})
    except Exception as e:                                        # noqa: BLE001
        res["journal_note"] = f"journal not updated ({type(e).__name__})"
    return res


def flp_finish_piece(project, layers=None, perform=True, ending=True, final=True, song=None, dry_run=False,
                     background=True):
    """Finish a piece in one call: support layers, performance, rule-aware ending, prediction, FINAL (see module)."""
    if not project or not str(project).lower().endswith(".flp"):
        raise ValueError("project: the .flp to finish")
    project = os.path.abspath(project)
    if not os.path.isfile(project):
        raise FileNotFoundError(project)
    H["writable"](project)
    plan = _plan(project, layers, perform, ending, final, song)
    if not (plan["layers"] or plan["perform"] or plan["ending"] or plan["final"]):
        raise ValueError("nothing to do: layers=[], perform=false, ending=false and final=false")
    if dry_run:
        return _dry(plan)
    if background:
        import flh_common as C
        r = C.start_job(H, "finish", _run, plan, _fl=True)
        if isinstance(r, dict) and r.get("job"):
            try:
                est = _dry_estimate(plan)
                if est:
                    r["eta_s"] = est
            except Exception:                                     # noqa: BLE001
                pass
            r["note"] = (f"finishing in the background: fl_job('{r['job']}') for the result. FL is held for the run "
                         "(its popups can still be answered with fl_dialog)")
        return r
    return _run(plan)


def _dry_estimate(plan):
    """A quick eta (no sub-tool dry runs): the per-step estimates, one reload."""
    p = plan["project"]; total = 0.0
    for on, tool in ((plan["layers"], "flp_compose"), (plan["perform"], "flp_perform"),
                     (plan["ending"], "flp_extend_ending"), (plan["final"], "fl_finalize")):
        if on:
            total += _estimate(tool, {"project": p})[0]
    return int(round(total)) or None


DESC = ("Finish a piece in ONE call: support layers (flp_compose layer roles, bars all, level support), the "
        "performance (flp_perform_setup style + flp_perform; the original parts), a rule-aware ending "
        "(flp_extend_ending), the ending predicted against the collection's rules BEFORE rendering (a predicted fail "
        "gets a longer hold first), then fl_finalize - all file edits in ONE FL save+reload. A failed rule with a "
        "mechanical fix (hold longer) is fixed and finalized once more (max 1 retry, said). Defaults: song journal / "
        "profile 'finish_piece' {layers, perform_style}, else no layers. Returns steps, steps_s, renders, rules, the "
        "final (path, LUFS, TP, length) and what a human should listen to. New layer channels (cloned sounds) skip "
        "the final until they have sounds (final='force' renders anyway). dry_run: the plan with an estimate. "
        "Background job (fl_job).")


def register(ns):
    global H
    H = ns
    S, P = ns["_schema"], ns["_p"]
    STR, BOOL = ns["_STR"], ns["_BOOL"]
    ns["TOOLS"]["flp_finish_piece"] = (
        flp_finish_piece, DESC,
        S(project=ns["_FLP"],
          layers=P(["array", "string"], "roles e.g. ['low strings','strings','choir']; [] none; default journal/profile", True, items={}),
          perform=P(["boolean", "string"], "true (journal/profile style, else natural), a style e.g. 'expressive', or false", True),
          ending=P(["boolean", "number"], "true (hold from the rules), false, or the final chord's seconds", True),
          final=P(["boolean", "string"], "true (default), false, or 'force' (even with new unsounded layer channels)", True),
          song=STR("song name for the final (default: journal / profile / project name)", True),
          dry_run=BOOL("the plan with an estimate, nothing written", True),
          background=BOOL("run as a job (default true; fl_job for the result)", True)))
    ns.setdefault("TOOL_GROUPS", {})["flp_finish_piece"] = "compose"
