"""flh_perform + flh_realism: layer parts (strings / choir / pads / brass ...) get a family at setup and are played by
ns["realism_apply"] instead of the piano rules; without it (or with style none) nothing changes; the piano and the other
parts render exactly as before; the clash guard gives back the written timing when realism's timing would rub against
another part; expression curves go into the batch through the realism writer, else 'run flp_realism after'.
Run: python3 tests/test_perform_realism.py"""
import copy, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ.setdefault("LOCALAPPDATA", tmp)
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_perform as P  # noqa: E402
ns = vars(m); flh_store.register(ns); P.register(ns)
ns.pop("realism_apply", None); ns.pop("realism_expression_ops", None)

# ---- families from names
assert P.layer_family("Strings") == "strings" and P.layer_family("Low Strings") == "strings"
assert P.layer_family("Choir Pad") == "choir" and P.layer_family("Warm Pad") == "pad" and P.layer_family("Horns") == "brass"
assert P.layer_family("Grand Piano") == "piano" and P.layer_family("Piano Pad") == "piano"
assert P.layer_family("808") == "bass" and P.layer_family("Kick") == "drums" and P.layer_family("Channel 3") is None
assert P.layer_family(None, "C:\\x\\Ensemble Strings.fst") == "strings" and P.layer_family("Flute") == "woodwind"
assert set(P.realism_families()) == {"strings", "choir", "pad", "brass", "woodwind", "bells"}

# ---- realism_run: index mapping, list replies, clash guard (revert / drop)
def shift(notes, fam, ctx):
    return {"notes": [dict(n, start=n["start"] + 4, end=n["end"] + 30, vel=n["vel"] - 5) for n in notes],
            "expression": {"cc": 11, "points": [[0, 70], [ctx["ppq"] * 4, 96]]}}
src = [[0, 96, 60, 80], [96, 192, 64, 80]]
got, ex, rep = P.realism_run(shift, src, "strings", {"ppq": 96})
assert [g[:4] for g in got] == [[4, 126, 60, 75], [100, 222, 64, 75]] and [g[4] for g in got] == [0, 1], got
assert ex["cc"] == 11 and rep == {"family": "strings", "notes": 2, "retimed": 2}, rep
# a semitone above the first note starting where it ended: the legato tail would rub -> written timing back
got, _, rep = P.realism_run(shift, src, "strings", {"ppq": 96}, others=[[100, 300, 73, 90]])
assert got[0][:2] == [0, 96] and got[1][:2] == [100, 222] and rep["clash_guard_reverted"] == 1, (got, rep)
# an existing rub stays (it was written that way), only NEW rubs are guarded
got, _, rep = P.realism_run(shift, src, "strings", {"ppq": 96}, others=[[0, 300, 61, 90]])
assert got[0][:2] == [4, 126] and "clash_guard_reverted" not in rep, (got, rep)
# list replies without indexes: matched back by pitch + nearest start; added notes that clash are left out
lst = lambda notes, fam, ctx: [[n["start"] + 2, n["end"], n["pitch"], n["vel"]] for n in reversed(notes)] + [[10, 50, 72, 40]]
got, _, rep = P.realism_run(lst, src, "choir", {"ppq": 96}, others=[[0, 96, 73, 80]])
assert sorted((g[0], g[4]) for g in got) == [(2, 0), (98, 1)] and rep["clash_guard_dropped"] == 1, (got, rep)
# a held note split in two (re-articulated): the split part keeps a rub the written note already had
spl = lambda notes, fam, ctx: {"notes": [dict(notes[0], end=48), dict(notes[0], start=48, realism_split_of=0)] + notes[1:]}
got, _, rep = P.realism_run(spl, src, "strings", {"ppq": 96}, others=[[0, 96, 61, 80]])
assert [g[:2] + [g[4]] for g in got] == [[0, 48, 0], [48, 96, None], [96, 192, 1]] and "clash_guard_dropped" not in rep, got
try:
    P.realism_run(lambda *a: {"nope": 1}, src, "pad", {"ppq": 96}); raise SystemExit("must refuse")
except ValueError:
    pass
ex2 = P.expression_at({"family": "strings", "clips": [{"control": "dynamics", "points": [[0, 0.5], [96, 0.7]]}]}, [384, 768])
assert [c["points"][0][0] for c in ex2["clips"]] == [384, 768] and ex2["family"] == "strings"
assert P.expression_at({"cc": 1}, [384]) == {"cc": 1}
print("realism_run ok")

# ---- a song: piano chords (ch 0), strings (ch 1), lead (ch 2), arranged in one project
TPL = os.path.join(tmp, "template.flp"); shutil.copy(FIX, TPL)
m.flp_playlist(TPL, "remove", clips=[c["id"] for c in m.flp_playlist(TPL)["clips"] if c["kind"] == "audio"])
m._with_open_project = lambda project, output, fn: fn()
PPQ = 96


def make_song(name, parts, names, sections):
    path = os.path.join(tmp, name + ".flp"); mids = []
    for i, (ch, nn) in enumerate(sorted(parts.items())):
        mp = os.path.join(tmp, f"{name}_{ch}.mid")
        evs = [(s_, bytes([0x90, k, v])) for s_, e_, k, v in nn] + [(e_, bytes([0x80, k, 0])) for s_, e_, k, v in nn]
        if i == 0:
            evs.append((0, b"\xff\x51\x03" + int(round(60e6 / 120.0)).to_bytes(3, "big")))
        m.midi_write(mp, PPQ, [("p", evs)]); mids.append({"channel": ch, "midi": mp})
    m.flp_write_arrangement(TPL, path, mids, sections, tempo="auto", beats_per_bar=4)
    m.flp_style_channels(path, path, [{"channel": c, "name": n} for c, n in names.items()])
    return path


bar = 4 * PPQ
piano, strings, lead = [], [], []
for b in range(8):
    t0 = b * bar
    for k in (0, 2):
        for p_ in (48, 52, 55):
            piano.append((t0 + k * PPQ, t0 + (k + 2) * PPQ - 8, p_, 72 + b % 3))
    for p_ in (60, 64, 67):                                          # strings: whole-bar chords
        strings.append((t0, t0 + bar - 12, p_, 70))
    for k in range(4):
        lead.append((t0 + k * PPQ, t0 + (k + 1) * PPQ - 10, (72, 76, 79)[(b + k) % 3], 88))
lead.append((7 * bar + bar - 12, 7 * bar + bar + PPQ, 73, 90))       # C#5 right where the last strings chord ends
SONG = make_song("layers", {0: piano, 1: strings, 2: lead}, {0: "Piano", 1: "Strings", 2: "Lead"},
                 [{"name": "Verse", "bar": 1}, {"name": "Chorus", "bar": 5}])
su = P.flp_perform_setup(SONG, style="natural")
spec = copy.deepcopy(flh_store.journal_get(SONG)["performance"])
fams = {k: p.get("family") for k, p in spec["parts"].items()}
assert fams == {"Piano": None, "Strings": "strings", "Lead": None}, fams
base = os.path.dirname(SONG)
r0 = P.perform(spec, base_dir=base)                                    # no realism module: as before
nofam = copy.deepcopy(spec)
for p in nofam["parts"].values():
    p.pop("family", None)
assert P.perform(nofam, base_dir=base)["parts"] == r0["parts"], "a family alone changes nothing"
assert "realism" not in r0 and "expression" not in r0

# ---- with flh_realism: strings by realism, the piano and the lead exactly as before
calls = []
def fake_realism(notes, family, ctx):
    calls.append((family, ctx["part"], ctx["source"], ctx["ppq"], len(notes), len(ctx["others"])))
    return shift(notes, family, ctx)
ns["realism_apply"] = fake_realism
P._RENDER_CACHE.clear()
r1 = P.perform(spec, base_dir=base)
assert calls and calls[0][:4] == ("strings", "Strings", "perform", r1["ppq"]) and calls[0][5] > 0, calls
assert r1["parts"]["Piano"] == r0["parts"]["Piano"] and r1["parts"]["Lead"] == r0["parts"]["Lead"], "other parts move"
assert r1["parts"]["Strings"] != r0["parts"]["Strings"]
src_str = sorted(P.load_source(os.path.join(base, spec["source"]))["notes"], key=lambda n: (n["s"], n["p"]))
src_str = [n for n in src_str if n["t"] == spec["parts"]["Strings"]["track"]]
div = r1["ppq"]; d4 = 4; tail = 30
want = sorted([n["s"] + d4, n["e"] + tail, n["p"], n["v"] - 5] for n in src_str)
got = sorted(r1["parts"]["Strings"])
rev = r1["realism"]["Strings"].get("clash_guard_reverted", 0)
assert rev >= 1, r1["realism"]                                         # the last chord's tail would rub the C#5
same = sum(1 for a, b in zip(got, want) if a == b)
assert same == len(want) - rev, (same, len(want), rev)
last = [n for n in got if n[0] >= 7 * 4 * div - 2]
assert rev == 1 and [n[:3] for n in last if n[2] == 60] == [[7 * 4 * div, 8 * 4 * div - 12, 60]], last   # written timing back
assert all(n[0] == 7 * 4 * div + d4 for n in last if n[2] != 60), last  # its neighbours (no rub) keep realism's
assert r1["expression"]["Strings"]["cc"] == 11
P._RENDER_CACHE.clear()
assert P.perform(spec, base_dir=base)["parts"] == r1["parts"], "deterministic"
# realism off per part / per spec, and style none: as without the module
off = copy.deepcopy(spec); off["parts"]["Strings"]["realism"] = False
assert P.perform(off, base_dir=base)["parts"] == r0["parts"]
off = copy.deepcopy(spec); off["realism"] = False
assert P.perform(off, base_dir=base)["parts"] == r0["parts"]
print("perform realism ok:", r1["realism"])

# ---- a write: realism reported, expression via the writer in the same batch (else: run flp_realism after)
real_edit = ns["flp_edit"]; batches = []
def edit(project, ops, output=None):
    batches.append([o.get("tool") for o in ops])
    return real_edit(project, [o for o in ops if o.get("tool") != "x_expression"], output)
ns["flp_edit"] = edit
w = P.flp_perform(SONG, changes={"Chorus": {"humanize": "-10%"}})
assert w.get("realism", {}).get("Strings", {}).get("family") == "strings", w
assert "flp_realism" in w.get("realism_next", ""), w
assert w["verify"]["ok"], w["verify"]
seen = []
ns["realism_expression_ops"] = lambda project, ch, ex, ctx: (seen.append((ch, ex["cc"], ctx["source"], ctx["part"]))
                                                              or [{"tool": "x_expression", "channel": ch}])
w2 = P.flp_perform(SONG, parts="all")
assert seen == [(1, 11, "perform", "Strings")] and "x_expression" in batches[-1] and "realism_next" not in w2, (seen, batches)
assert w2["verify"]["ok"], w2["verify"]
# style none stays an exact identity even with realism loaded
SONG2 = make_song("layers2", {0: piano, 1: strings, 2: lead}, {0: "Piano", 1: "Strings", 2: "Lead"},
                  [{"name": "Verse", "bar": 1}])
n_before = len(calls)
P.flp_perform_setup(SONG2, style="none")
r2 = P.perform(flh_store.journal_get(SONG2)["performance"], base_dir=os.path.dirname(SONG2))
assert len(calls) == n_before and "realism" not in r2
ns["flp_edit"] = real_edit

# ---- with the real flh_realism (when it's there): contract only - piano/lead untouched, strings performed, one batch
try:
    import flh_realism
except ImportError:
    flh_realism = None
if flh_realism is not None:
    for k in ("realism_apply", "realism_expression_ops"):
        ns.pop(k, None)
    flh_realism.register(ns); P._RENDER_CACHE.clear()
    rr = P.perform(spec, base_dir=base)
    assert rr["parts"]["Piano"] == r0["parts"]["Piano"] and rr["parts"]["Lead"] == r0["parts"]["Lead"]
    assert rr["realism"]["Strings"]["family"] == "strings" and "error" not in rr["realism"]["Strings"], rr["realism"]
    wr = P.flp_perform(SONG, parts="all")
    assert wr["verify"]["ok"] and "realism_next" not in wr, (wr["verify"], wr.get("realism_next"))
    print("with flh_realism:", rr["realism"]["Strings"], [x.get("tool") for x in (wr.get("edit") or {}).get("results", [])])
print("ok")
