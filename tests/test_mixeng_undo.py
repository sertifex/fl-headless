"""Mix engineer undo + ids (PC 2026-10-26): revert_last only undoes the open project's own moves (another song's 62
knob/fader moves once landed in the open project), and apply=true with ids applies the moves of the list the user
saw - never a fresh measurement's renumbered list. No FL needed. Run: python3 tests/test_mixeng_undo.py"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_mixeng as X  # noqa: E402
ns = vars(m)
flh_store.register(ns); X.register(ns)

A = os.path.join(tmp, "A.flp"); B = os.path.join(tmp, "B.flp")
OPEN = {"p": A}
X.H["_open_project_guess"] = lambda: (OPEN["p"], os.path.basename(OPEN["p"]))
sent = []
X._bridge = lambda cmd, **a: sent.append((cmd, a)) or {}

X._undo_push(A, "a1", [{"kind": "pan", "track": 3, "before": 0.0}])
X._undo_push(B, "b1", [{"kind": "pan", "track": 9, "before": 0.5}])
OPEN["p"] = A
try:
    X.revert(); raise SystemExit("must refuse: the last moves were B's")
except ValueError as e:
    assert "B.flp" in str(e) and "nothing was changed" in str(e), e
assert not sent
OPEN["p"] = B
r = X.revert()
assert r["reverted"] == 1 and [a["track"] for _, a in sent] == [9], (r, sent)
sent.clear(); OPEN["p"] = A
r = X.revert()                                                    # now A's batch is the last one
assert r["reverted"] == 1 and [a["track"] for _, a in sent] == [3], (r, sent)
assert X.revert()["reverted"] == 0                                # nothing left
# an older install's shared list is never replayed
m._state({"mix_engineer": {"project": B, "applied": [{"kind": "pan", "track": 1, "before": 0}]}})
sent.clear(); r = X.revert()
assert r["reverted"] == 0 and not sent and "older" in r["note"], r

# ids: the moves of the shown list, no new measurement
calls = []
X._live_meas = lambda *a, **k: calls.append("measure") or (_ for _ in ()).throw(AssertionError("re-measured"))
X._one_export_meas = lambda *a, **k: calls.append("measure") or (None, "x")
got = []
X.apply_ops = lambda ops, label="", project=None: got.extend(ops) or {"done": ops}
props = [{"id": 1, "kind": "gain", "name": "Melody", "ops": [{"op": "fader", "track": 5, "db": 3}]},
         {"id": 2, "kind": "highpass", "name": "Choir", "ops": [{"op": "eq", "track": 7, "hz": 150}]}]
m._state({"mix_engineer_last": {"project": A, "proposals": props, "genre": "default"}})
r = X.mix_engineer(apply=True, ids=[2], project=A)
assert [o["track"] for o in got] == [7] and not calls and r["applied_ids"] == [2], (got, calls, r)
try:
    X.mix_engineer(apply=True, ids=[2], project=A); raise SystemExit("a used list can't be applied twice")
except ValueError as e:
    assert "nothing was changed" in str(e)
m._state({"mix_engineer_last": {"project": B, "proposals": props}})
try:
    X.mix_engineer(apply=True, ids=[1], project=A); raise SystemExit("another song's list must not apply")
except ValueError:
    pass
print("ok")
