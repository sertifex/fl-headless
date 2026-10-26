"""flh_compose + flh_realism: flp_compose layers (strings / choir / pad / brass / bells, counter) are played by
ns["realism_apply"] by default (realism=False skips; without the module: written as before, said so), beat parts too
with realism=True (flh_new_song passes it for orchestral / cinematic genres), the clash guard keeps realism's timing
from adding rubs, the song-level clash check still runs, expression goes into the batch through the realism writer.
Run: python3 tests/test_compose_realism.py"""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
import flh_store, flh_score as SC, flh_compose as C  # noqa: E402
flh_store.register(ns); SC.register(ns); C.register(ns)
ns.pop("realism_apply", None); ns.pop("realism_expression_ops", None)

assert C.realism_for_genre("cinematic") is True and C.realism_for_genre("epic orchestral") is True
assert C.realism_for_genre("trap") is None and C.realism_for_genre("pop") is None
assert C.realism_for_genre({"name": "x", "realism": True}) is True and C.realism_for_genre("no such genre") is None
src_ns = open(os.path.join(os.path.dirname(HERE), "flh_newsong.py"), encoding="utf-8").read()
assert "realism=cm.realism_for_genre(gname)" in src_ns, "flh_new_song passes realism for its genre's starter"

if not os.path.isfile(FIX):
    print("skipped: writer tests (no fixture)"); print("ok"); sys.exit(0)
m._with_open_project = lambda project, output, fn: fn()
base = os.path.join(tmp, "base.flp"); shutil.copy(FIX, base)
m.flp_edit_notes(base, [{"op": "delete"}], pattern=1)
m.flp_playlist(base, "remove", clips=[c["id"] for c in m.flp_playlist(base)["clips"]])
names = ["Kick", "Snare", "Hat", "808", "Keys", "Lead", "Strings", "Low Strings", "Horns"]
ops = [{"tool": "flp_add_channel", "clone_from": 0} for _ in names]
ops.append({"tool": "flp_style_channels", "channels": [{"channel": 6 + i, "name": n} for i, n in enumerate(names)]})
m.flp_edit(base, ops)
chan = {n: 6 + i for i, n in enumerate(names)}
compose = ns["TOOLS"]["flp_compose"][0]
r = compose(project=base, what="beat", genre="trap", key="C minor", seed=7,
            channel={"chords": chan["Keys"], "bass": chan["808"], "melody": chan["Lead"]})
assert r["verified"] and "realism" not in r, r.get("realism")            # beat: realism off by default


def copy_of(name):
    p = os.path.join(tmp, name + ".flp"); shutil.copy(base, p)
    j = os.path.splitext(base)[0] + ".journal.json"
    if os.path.isfile(j):
        shutil.copy(j, os.path.splitext(p)[0] + ".journal.json")
    return p


def layer_notes(p, pat="Beat Strings"):
    return sorted((n["at"], n["note"], n["vel"], n["beats"]) for n in
                  m.flp_notes(p, pattern=pat, limit=2000)["notes"])


# ---- no realism module: the layer is written as before and the result says so
A = copy_of("A")
ra = compose(project=A, what="layer", role="strings", channel=chan["Strings"], section="Beat", seed=3)
assert ra["verified"] and ra["realism"]["strings"]["applied"] is False and "isn't loaded" in ra["realism"]["strings"]["why"]
B = copy_of("B")
rb = compose(project=B, what="layer", role="strings", channel=chan["Strings"], section="Beat", seed=3, realism=False)
assert "realism" not in rb and layer_notes(A) == layer_notes(B), "realism=False / no module: identical writes"

# ---- with flh_realism: played by it, clash-guarded, the clash check still runs, deterministic
calls = []
def fake_realism(notes, family, ctx):
    calls.append((family, ctx["part"], ctx["source"], ctx["ppq"], len(ctx["others"])))
    return {"notes": [dict(n, start=n["start"] + 24, end=n["end"] + ctx["ppq"], vel=max(1, n["vel"] - 6))
                      for n in notes], "expression": {"cc": 1, "points": [[0, 64], [ctx["ppq"] * 8, 100]]}}
ns["realism_apply"] = fake_realism
Cp = copy_of("C")
rc = compose(project=Cp, what="layer", role="strings", channel=chan["Strings"], section="Beat", seed=3)
info = rc["realism"]["strings"]
print("layer realism:", info, rc["clashes"].get("counts"), ra["clashes"].get("counts"))
assert rc["verified"] and info["applied"] and info["family"] == "strings" and info["retimed"] > 0, info
assert calls[-1][:4] == ("strings", "strings", "compose", 960) and calls[-1][4] > 0, calls
assert "flp_realism" in info.get("expression", ""), info                 # no writer: say so
assert layer_notes(Cp) != layer_notes(A)
assert "clashes" in rc, "the song-level clash check runs on what realism wrote"
hm = lambda r_: (r_["clashes"].get("counts") or {}).get("high", 0) + (r_["clashes"].get("counts") or {}).get("med", 0)
assert hm(rc) <= hm(ra), (rc["clashes"], ra["clashes"])                 # realism's timing added no rubs
D = copy_of("D")
rd = compose(project=D, what="layer", role="strings", channel=chan["Strings"], section="Beat", seed=3)
assert layer_notes(D) == layer_notes(Cp), "deterministic"
# a piano channel gets no realism, even as a layer
E = copy_of("E")
re_ = compose(project=E, what="layer", role="pad", channel=chan["Keys"], section="Beat", seed=3)
assert "realism" not in re_, re_.get("realism")

# ---- expression through the realism writer, in the same batch
seen, batches = [], []
real_auto = ns.get("flp_edit_auto"); real_edit = ns["flp_edit"]
def wrap(fn):
    def w(project, ops, *a, **k):
        batches.append([o.get("tool") for o in ops])
        return fn(project, [o for o in ops if o.get("tool") != "x_expression"], *a, **k)
    return w
if real_auto:
    ns["flp_edit_auto"] = wrap(real_auto)
ns["flp_edit"] = wrap(real_edit)
ns["realism_expression_ops"] = lambda project, ch, ex, ctx: (seen.append((ch, ex["cc"], ctx["pattern"], ctx["source"]))
                                                              or [{"tool": "x_expression", "channel": ch}])
F = copy_of("F")
rf = compose(project=F, what="layer", role="strings", channel=chan["Strings"], section="Beat", seed=3)
assert seen == [(chan["Strings"], 1, "Beat Strings", "compose")] and "x_expression" in batches[-1], (seen, batches)
assert "expression" not in rf["realism"]["strings"] and rf["verified"]

# ---- beat with realism=True (orchestral starter): strings / brass parts by realism, the drums untouched
G = copy_of("G"); calls.clear()
rg = compose(project=G, what="beat", genre="cinematic", key="D minor", seed=5, realism=True,
             channel={"chords": chan["Strings"], "bass": chan["Low Strings"], "melody": chan["Horns"]})
assert rg["verified"], rg.get("verify_problems")
assert {k: v["family"] for k, v in rg["realism"].items()} == {"chords": "strings", "bass": "strings",
                                                               "melody": "brass"}, rg["realism"]
assert sorted(c[1] for c in calls) == ["bass", "chords", "melody"], calls
H_ = copy_of("H")
rh = compose(project=H_, what="beat", genre="cinematic", key="D minor", seed=5, realism=None,
             channel={"chords": chan["Strings"], "bass": chan["Low Strings"], "melody": chan["Horns"]})
assert "realism" not in rh
ns["flp_edit"] = real_edit
if real_auto:
    ns["flp_edit_auto"] = real_auto

# ---- with the real flh_realism (when it's there): contract only
try:
    import flh_realism
except ImportError:
    flh_realism = None
if flh_realism is not None:
    for k in ("realism_apply", "realism_expression_ops"):
        ns.pop(k, None)
    flh_realism.register(ns)
    I = copy_of("I")
    ri = compose(project=I, what="layer", role="strings", channel=chan["Strings"], section="Beat", seed=3)
    assert ri["verified"] and ri["realism"]["strings"]["applied"], ri["realism"]
    assert hm(ri) <= hm(ra), ri["clashes"]
    print("with flh_realism:", ri["realism"]["strings"])
    # a layer on a channel the same batch creates: its expression is written in that batch too
    J = copy_of("J")
    rj = compose(project=J, what="layer", role="choir", section="Beat", seed=3)
    newc = [c["channel"] for c in rj.get("new_channels") or []]
    assert newc and rj["realism"]["choir"]["applied"] and "expression" not in rj["realism"]["choir"], rj.get("realism")
    assert (C.channels_of(m._load(J)).get(newc[0]) or {}).get("name") == "Choir"
    dj = m._load(J); evj = m._events(dj)
    auto_names = sorted(nm for kind, nm in m._clip_names(dj, evj).values() if kind == "automation")
    assert any(nm.startswith("Choir ") and " - " in nm for nm in auto_names), auto_names
    ops_ = flh_realism.realism_expression_ops(J, 999, {"family": "choir", "clips": [{"control": "dynamics",
                                              "section": "Beat", "points": [[0, 0.5]]}]}, {"new_channel": True})
    assert ops_ and ops_[0]["plan"]["expression"][0]["channel"] == 999
    rr_ = m.flp_edit(J, ops_)["results"][0]                 # still not there when the batch runs: said, nothing else
    assert "not written" in rr_["expression"]["999"], rr_
    try:
        flh_realism.realism_expression_ops(J, 999, {"family": "choir", "clips": [{"control": "dynamics",
                                           "section": "Beat", "points": [[0, 0.5]]}]})
        raise SystemExit("an unknown channel without new_channel must be refused")
    except ValueError:
        pass
print("ok")
