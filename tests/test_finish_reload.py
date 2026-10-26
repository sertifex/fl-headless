"""flp_finish_piece makes FL hold the EDITED file before its final renders (PC 2026-10-23: the ending step wrote +20 s
while FL showed another file / a same-title copy, the batch didn't reload FL (save_reload 0.01 s), and the final
rendered by FL's own export missed the ending). Run: python3 tests/test_finish_reload.py (no FL needed)."""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_finrl_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local"); os.environ["FLH_NO_WARM"] = "1"
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_shape, flh_finish  # noqa: E402
ns = vars(m)
flh_store.register(ns); flh_shape.register(ns); flh_finish.register(ns)
TOOLS = ns["TOOLS"]
order = []
S = {"route": "in_fl", "reloaded": False, "load_fail": False, "fl": True}


def fl_running():
    return S["fl"]


def batch(project, output, fn, _pending_ok=True):
    r = fn()
    order.append("batch_done")
    if isinstance(r, dict) and S["reloaded"]:
        r = dict(r, reloaded_in_fl=True)
    return r


def extend(**kw):
    order.append("ending"); S["n"] = S.get("n", 0) + 1
    r0 = m.fl_running; m.fl_running = lambda: False
    try:
        m.flp_set_tempo(kw["project"], 70 + S["n"])          # the file really changes
    finally:
        m.fl_running = r0
    return {"mode": "seconds", "held_notes": 9, "clips_grown": 1, "added_s": 20.0}


def load_file(project, why="render"):
    order.append("fl_load")
    if S["load_fail"]:
        raise m.InFlUnavailable("FL didn't open Song Rework.flp (waited 400 s; FL last showed 'Opening: X')")
    return {"how": "opened", "steps_s": {"open": 61.0}}


def finalize(**kw):
    order.append("final")
    return {"ok": True, "finals": [{"song": "Song Rework", "path": os.path.join(tmp, "x.flac"), "lufs": -16.0,
                                    "true_peak": -1.1, "length": "4:03"}]}


try:
    p = os.path.join(tmp, "Song Rework.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), p)
    TOOLS["flp_extend_ending"] = (extend,) + tuple(TOOLS["flp_extend_ending"][1:])
    TOOLS["fl_finalize"] = (finalize,) + tuple(TOOLS["fl_finalize"][1:])
    ns["_with_open_project"] = batch
    ns["fl_running"] = fl_running
    ns["_render_method"] = lambda pr, f="wav", meth="auto", start_bar=None: S["route"]
    ns["_fl_load_file"] = load_file

    def run():
        order.clear()
        return flh_finish.flp_finish_piece(p, layers=[], perform=False, ending=True, final=True, song="Song Rework",
                                           background=False)

    # 1. the batch didn't reload FL (FL showed another file): FL loads the edited file BEFORE the final
    r = run()
    assert order == ["ending", "batch_done", "fl_load", "final"], order
    st = next(s for s in r["steps"] if s["step"] == "fl_load")
    assert st["ok"] and st["how"] == "opened" and "fl_load" in r["steps_s"] and r["ok"], r
    # 2. the batch's own save + reload put the edits into FL: no second load
    S["reloaded"] = True
    run()
    assert order == ["ending", "batch_done", "final"], order
    S["reloaded"] = False
    # 3. the command line renders the file itself (FL closes for it): no load needed
    S["route"] = "cli"
    run()
    assert order == ["ending", "batch_done", "final"], order
    S["route"] = "in_fl"
    # 4. FL closed: nothing to load
    S["fl"] = False
    run()
    assert order == ["ending", "batch_done", "final"], order
    S["fl"] = True
    # 5. FL couldn't load it: the final is skipped (never a FINAL of an older copy), the reason said
    S["load_fail"] = True
    r = run()
    assert order == ["ending", "batch_done", "fl_load"] and not r["ok"], (order, r)
    fin = next(s for s in r["steps"] if s["step"] == "final")
    assert fin.get("skipped") and any("fl_load" in e and "no final was rendered" in e for e in r["errors"]), r
    S["load_fail"] = False
    print("finish reloads FL after its edits before the final ok")
finally:
    shutil.rmtree(tmp, True)
print("ok")
