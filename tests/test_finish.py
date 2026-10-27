"""flh_finish: flp_finish_piece runs layers -> performance -> ending -> prediction -> final through the existing tools
(mocked through ns): the order, ONE save+reload for every file edit, a predicted fail fixed BEFORE the render, at most
one retry after a failed rule, dry_run's plan, errors reported with the partial results, the new-channel gate, the
background job. Run: python3 tests/test_finish.py (no FL needed)."""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_finish_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_shape, flh_compose, flh_finish  # noqa: E402
ns = vars(m)
flh_finish.register(ns)
m.fl_running = lambda: False
TOOLS = ns["TOOLS"]
REAL = {k: TOOLS[k] for k in ("flp_compose", "flp_perform_setup", "flp_perform", "flp_extend_ending", "fl_finalize")}
REAL_NS = {k: ns[k] for k in ("_with_open_project", "fl_ending_check", "_renders_here", "estimate")}

calls = []                     # (name, args, inside the edit batch?)
S = {"depth": 0, "batches": 0, "renders": 0}


def batch(project, output, fn, _pending_ok=True):
    S["batches"] += 1; S["depth"] += 1
    try:
        r = fn()
    finally:
        S["depth"] -= 1
    if isinstance(r, dict):
        r = dict(r, reloaded_in_fl=True)
    return r


def rec(name, kw):
    calls.append((name, {k: v for k, v in kw.items() if k != "project"}, S["depth"] > 0))


def setup(cfg):
    """cfg: compose / perform_fail / pred (list of predictions) / finals (list of rule results) ..."""
    calls.clear(); S.update(batches=0, renders=0, depth=0)

    def compose(**kw):
        rec("flp_compose", kw)
        if not kw.get("dry_run"):
            assert flh_compose._TL.scratch == kw["project"], "compose must write the file only inside the batch"
        r = {"layers": {"strings": {"notes": 40, "channel": 5}, "choir": {"notes": 12, "channel": 6}},
             "level": "support: ...", "verified": True}
        if cfg.get("new_channels"):
            r["new_channels"] = [{"channel": 6, "role": "choir", "name": "Choir", "sound_needed": "choir aahs"}]
        return r

    def psetup(**kw):
        rec("flp_perform_setup", kw)
        return {"style": kw.get("style") or "natural", "parts": {"melody": {"channel": 0}}}

    def perform(**kw):
        rec("flp_perform", kw)
        if cfg.get("perform_fail"):
            raise ValueError("this song's journal has no performance spec yet")
        return {"write": {"parts": ["melody"], "tempo": True}, "sections_changed": ["A"]}

    def extend(**kw):
        rec("flp_extend_ending", kw)
        return {"mode": "hold_s" if kw.get("hold_s") else "rules", "hold": {"main_s": kw.get("hold_s") or 41.0},
                "held_notes": 0 if cfg.get("held_zero") and kw.get("hold_s") == 50 else 4, "clips_grown": 1}

    preds = list(cfg.get("pred") or ["pass"])

    def check(project, rules=None, render=True):
        assert render is False, "the prediction never renders"
        calls.append(("fl_ending_check", {"rules": rules}, S["depth"] > 0))
        p = preds.pop(0) if len(preds) > 1 else preds[0]
        return {"predicted": p, "hold_s": 30.0 if p != "pass" else 42.0, "need_hold_s": 40.4,
                **({"fix": 'flp_extend_ending(project, hold_s=41)'} if p == "fail" else {})}

    finals = list(cfg.get("finals") or [{"pass": True, "checks": []}])

    def finalize(**kw):
        rec("fl_finalize", kw)
        assert kw.get("background") is False, "finalize runs inside this job"
        S["renders"] += 1
        rules = finals.pop(0) if len(finals) > 1 else finals[0]
        return {"ok": True, "renders": 1, "finals": [{"song": "Piece", "path": os.path.join(tmp, "Piece - FINAL.flac"),
                                                       "lufs": -18.0, "true_peak": -1.2, "length": "2:41",
                                                       "rules": rules}]}
    for k, f in (("flp_compose", compose), ("flp_perform_setup", psetup), ("flp_perform", perform),
                 ("flp_extend_ending", extend), ("fl_finalize", finalize)):
        TOOLS[k] = (f,) + tuple(REAL[k][1:])
    ns["_with_open_project"] = batch
    ns["fl_ending_check"] = check
    ns["_renders_here"] = lambda: S["renders"]
    ns["estimate"] = lambda tool, args=None: {"estimate_s": 60.0 if tool == "fl_finalize" else 8.0,
                                              "basis": "open in FL: save + reload (~6 s load)"}


def names():
    return [c[0] for c in calls]


try:
    src = os.path.join(HERE, "corpus", "base.flp")
    p = os.path.join(tmp, "Piece.flp"); shutil.copy(src, p)
    d = m._load(p); orig_ch = sorted({n[2] for n in m._song_x(d, m._events(d))[0]})
    RULES = {"min_ending_s": 30, "min_tail_s": 6}
    flh_store.profile_set("Coll", {"rules": RULES, "finish_piece": {"layers": ["low strings", "strings", "choir"],
                                                                    "perform_style": "expressive"}})

    # ---- 1. the whole run: profile defaults, order, ONE batch, a predicted fail fixed before the render
    setup({"pred": ["fail", "pass"]})
    r = flh_finish.flp_finish_piece(p, background=False)
    assert names() == ["flp_compose", "flp_perform_setup", "flp_perform", "flp_extend_ending", "fl_ending_check",
                       "flp_extend_ending", "fl_ending_check", "fl_finalize"], names()
    assert all(c[2] for c in calls[:-1]) and not calls[-1][2], calls          # every edit inside, the render outside
    assert S["batches"] == 1, S                                               # one FL save + reload for all edits
    comp = calls[0][1]
    assert comp["roles"] == ["low strings", "strings", "choir"] and comp["bars"] == "all" and comp["level"] == "support"
    ps = calls[1][1]
    assert ps["style"] == "expressive", ps                                    # the profile's style
    assert ps.get("parts") and 5 not in ps["parts"] and 6 not in ps["parts"] and set(ps["parts"]) <= set(orig_ch), ps
    assert calls[5][1]["hold_s"] == 41, calls[5]                              # ceil(need_hold_s), before rendering
    pr = next(s for s in r["steps"] if s["step"] == "predict")
    assert pr["predicted"] == "fail" and pr["fixed_before_render"]["hold_s"] == 41 and pr["predicted_after_fix"] == "pass"
    assert r["ok"] and r["renders"] == 1 and r["rules"]["pass"] is True and "retry" not in r, r
    assert r["final"]["lufs"] == -18.0 and r["final"]["path"].endswith("FINAL.flac") and r["final"]["length"], r
    assert {"layers", "perform", "ending", "predict", "final", "save_reload", "total"} <= set(r["steps_s"]), r["steps_s"]
    assert r["fl"]["reloaded_in_fl"] and r["listen"] and any("layers" in g for g in r["guessed"]), r
    j = flh_store.journal_get(p)
    assert j["finish_piece"]["layer_channels"] == [5, 6] and j["finish_piece"]["last"]["perform_style"] == "expressive"
    print("1 full run:", names(), "batches", S["batches"], "renders", r["renders"])

    # ---- 2. a failed rule with a mechanical fix: fixed (own reload) + ONE retry, never a third render
    fail = {"pass": False, "checks": [{"rule": "ending_length", "pass": False}], "failed": ["ending_length"],
            "fixes": [{"rule": "ending_length", "fix": 'hold the last chord 9 s longer: flh_call("flp_extend_ending", '
                                                       '{"project": "<project>", "hold_s": 50}); then fl_finalize again'}]}
    setup({"finals": [fail, fail]})
    r = flh_finish.flp_finish_piece(p, layers=[], perform=False, background=False)
    assert names() == ["flp_extend_ending", "fl_ending_check", "fl_finalize", "flp_extend_ending", "fl_finalize"], names()
    assert calls[3][1]["hold_s"] == 50.0 and calls[3][2] and S["batches"] == 2, (calls, S)
    assert r["retry"]["finalized_again"] and r["retry"]["fix"] == {"hold_s": 50.0} and r["renders"] == 2, r
    assert r["rules"]["pass"] is False and r["rules"]["failed"] == ["ending_length"] and not r["ok"], r
    fin = [s for s in r["steps"] if s["step"] == "final"]
    assert len(fin) == 1 and fin[0]["retry"], fin
    print("2 retry once:", r["retry"])

    # ---- 2b. the fix finds nothing to hold longer: no second render
    setup({"finals": [fail], "held_zero": True})
    r = flh_finish.flp_finish_piece(p, layers=[], perform=False, background=False)
    assert names().count("fl_finalize") == 1 and "no second render" in r["retry"]["note"] and r["renders"] == 1, r

    # ---- 2c. a non-mechanical failure too: the hold is fixed, no second render (it would fail anyway)
    both = dict(fail, failed=["ending_length", "ends_in_silence"],
                fixes=fail["fixes"] + [{"rule": "ends_in_silence", "fix": "noise keeps running at the end"}])
    setup({"finals": [both]})
    r = flh_finish.flp_finish_piece(p, layers=[], perform=False, background=False)
    assert names().count("fl_finalize") == 1 and names().count("flp_extend_ending") == 2, names()
    assert "ends_in_silence" in r["retry"]["note"] and r["renders"] == 1, r
    print("2b/2c no wasted render ok")

    # ---- 3. dry_run: the plan with an estimate, nothing written
    setup({})
    before = open(p, "rb").read()
    r = flh_finish.flp_finish_piece(p, dry_run=True)
    assert r["dry_run"] and [x["step"] for x in r["plan"]] == ["layers", "perform", "perform", "ending", "predict",
                                                                 "final"], r["plan"]
    assert all(c[1].get("dry_run") for c in calls if c[0] in ("flp_compose", "flp_extend_ending")), calls
    assert not any(c[0] in ("flp_perform", "flp_perform_setup", "fl_finalize") for c in calls), calls
    assert S["batches"] == 0 and open(p, "rb").read() == before
    # 4 edits each "estimated" with a reload, the batch pays it once: 8 + 3 x (8 - 8.5 -> 0.5) + 0.5 + 60
    assert r["fl_reloads"] == 1 and r["renders"] == 1 and 60 < r["estimate_s"] < 75, r
    assert r["rules"] == {"min_ending_s": 30.0, "min_tail_s": 6.0} and r["guessed"], r
    print("3 dry_run:", r["estimate_s"], "s")

    # ---- 4. an error in a step: reported with the partial results; later steps and the render don't run
    setup({"perform_fail": True})
    r = flh_finish.flp_finish_piece(p, background=False)
    assert names() == ["flp_compose", "flp_perform_setup", "flp_perform"], names()
    assert S["batches"] == 1 and not r["ok"] and r["renders"] == 0, r              # the reload still happened
    st = {s["step"]: s for s in r["steps"]}
    assert st["layers"]["ok"] and not st["perform"]["ok"] and "performance spec" in st["perform"]["error"], st
    assert "skipped" in st["final"] and r["errors"][0].startswith("perform"), r
    print("4 error partial:", r["errors"])

    # ---- 5. new layer channels (cloned sounds): the final waits for sounds; final='force' renders
    setup({"new_channels": True})
    r = flh_finish.flp_finish_piece(p, background=False)
    st = {s["step"]: s for s in r["steps"]}
    assert "fl_finalize" not in names() and "sounds" in st["final"]["skipped"] and not r["ok"], r
    assert st["layers"]["new_channels"][0]["sound_needed"] == "choir aahs", st
    setup({"new_channels": True})
    r = flh_finish.flp_finish_piece(p, final="force", background=False)
    assert "fl_finalize" in names() and r["ok"], r
    print("5 new-channel gate ok")

    # ---- 6. given arguments beat the profile; the journal beats the profile; no rules: no prediction
    flh_store.journal_update(p, set={"finish_piece": {"layers": ["pad"]}})
    setup({})
    r = flh_finish.flp_finish_piece(p, perform="natural", ending=45, background=False)
    assert calls[0][1]["roles"] == ["pad"] and calls[1][1]["style"] == "natural", calls
    assert next(c for c in calls if c[0] == "flp_extend_ending")[1]["hold_s"] == 45.0
    flh_store.profile_set("Coll", {"rules": None})
    setup({"finals": [None]})                                                 # no rules: finalize checks none
    r = flh_finish.flp_finish_piece(p, layers=[], perform=False, background=False)
    assert names() == ["flp_extend_ending", "fl_finalize"] and r["rules"]["pass"] is None, (names(), r)
    for bad in ({"perform": "operatic"}, {"ending": 999}, {"layers": [], "perform": False, "ending": False,
                                                           "final": False}):
        try:
            flh_finish.flp_finish_piece(p, background=False, **bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    print("6 defaults / validation ok")

    # ---- 7. background: a job holding FL; fl_job returns the result
    setup({})
    j = flh_finish.flp_finish_piece(p, layers=[], perform=False)
    assert j.get("job", "").startswith("finish") and j.get("eta_s"), j
    got = m.fl_job(j["job"], wait_s=20)
    assert got["status"] == "done" and got["result"]["ok"] and got.get("drives_fl"), got
    assert "flp_finish_piece" in m.TOOLS and m.tool_group("flp_finish_piece") == "compose"
    print("7 background job ok")
finally:
    for k, v in REAL.items():
        TOOLS[k] = v
    ns.update(REAL_NS)
    shutil.rmtree(tmp, ignore_errors=True)
print("ok")
