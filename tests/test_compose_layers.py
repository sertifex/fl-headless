"""flp_compose(what='layer') role specs: where as a list of bar ranges writes notes for EVERY role (an arp's short
16ths were all dropped by the span clip), vel in any scale (0..1, 1..127, '70%', soft / normal / loud; never near
silent by a scale mix-up), register in any form, and a new layer channel (a clone of another channel) gets its OWN
free mixer insert named after its role, dark colored and routed to the Music bus - never the insert of the channel
it was cloned from. Run: python3 tests/test_compose_layers.py"""
import os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
os.environ.setdefault("FLH_NO_WARM", "1")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
import flh_compose as C  # noqa: E402
if "flp_compose" not in ns["TOOLS"]:
    import flh_store; flh_store.register(ns)
    import flh_score as SC; SC.register(ns)
    C.register(ns)
if "flh_new_song" not in ns["TOOLS"]:
    import flh_newsong as N0; N0.register(ns)
import flh_newsong as N  # noqa: E402

# ----------------------------------------------------------------------------- spec parsing
V = C._spec_vel
assert V(0.7) == [83, 95], V(0.7)                         # a fraction of full, not MIDI 0
assert V(90) == [84, 96] and V("90") == [84, 96] and V("70%") == V(0.7)
assert V("soft")[0] >= 40 and V("loud")[0] > V("normal")[0] > V("soft")[0]
assert V(0.01)[0] >= C.VEL_FLOOR and V(3)[0] >= C.VEL_FLOOR   # never near-silent by accident
assert V([0.5, 0.8]) == [64, 102] and V([100, 60]) == [60, 100] and V(1) == [121, 127] and V(127) == [121, 127]
assert V(None) is None and V("") is None
for bad in (0, -5, "nope", True, float("nan")):
    try:
        V(bad); raise SystemExit(f"vel {bad!r} must be refused")
    except ValueError:
        pass
R = C._spec_register
assert R(["C3", "C5"]) == [48, 72] and R(["C5", "C3"]) == [48, 72] and R("C3-C5") == [48, 72] and R("C3..C5") == [48, 72]
assert R([48, 72]) == [48, 72] and R("C4") == [54, 66] and R([60, 61]) == [54, 66] and R(None) is None
for bad in (["C3", 200], "H9"):
    try:
        R(bad); raise SystemExit(f"register {bad!r} must be refused")
    except ValueError:
        pass
W = C._spec_where
assert W([[9, 57], [57, 97]]) == [[9, 97]] and W([[9, 97]]) == [[9, 97]] and W([9, 97]) == [[9, 97]]
assert W("9-57, 60-72") == [[9, 57], [60, 72]] and W([[41, 25]]) == [[25, 41]] and W("all") == "all"
assert W("climax") == "climax" and W(None) is None
for bad in ("loud", [[0, 4]], [[1, 2, 3]], [["a", 2]]):
    try:
        W(bad); raise SystemExit(f"where {bad!r} must be refused")
    except ValueError:
        pass
sp = C._layer_specs(None, [{"role": "pad", "where": [[2, 6]], "vel": 0.7, "register": "C3-C5"}])
assert sp[0]["vel"] == [83, 95] and sp[0]["register"] == [48, 72] and sp[0]["where"] == [[2, 6]], sp

# ----------------------------------------------------------------------------- every role, list-of-ranges where
g = C.genre_data("synthwave")
key = "G major"
chs, _ = C.harmony(key, g, 16, None, 1)
S = C._spb()
spans = [((9 - 1) * S, (12 - 1 + 1) * S), ((14 - 1) * S, (16 - 1 + 1) * S)]     # bars 9-12 and 14-16
for tempo in (100, 118, 130):
    for r in list(C.LAYER_ROLES) + ["counter"]:
        mel = C.melody(key, chs, g, 16, 1)[0] if r == "counter" else None
        ln, li = C.layer(r, chs, key, g, 1, 0, None, 16, tempo, mel, where=spans)
        if r == "counter":
            ln = C._clip_spans(ln, spans)
        assert ln, (r, tempo, "0 notes with a list-of-ranges where")
        assert all(any(a - 1e-6 <= n["s"] < b for a, b in spans) and n["l"] > 0 for n in ln), (r, tempo)
        assert all(n["s"] + n["l"] <= max(b for a, b in spans if a <= n["s"] < b) + 1e-6 for n in ln), r
        full, _ = C.layer(r, chs, key, g, 1, 0, None, 16, tempo, mel)
        if r != "counter":                                # the spans keep every note that starts in them
            inside = [n for n in full if any(a - 1e-6 <= n["s"] < b for a, b in spans)]
            assert len(ln) >= len(inside), (r, tempo, len(ln), len(inside))
arp = [{"s": float(i), "l": 0.9, "k": 60, "v": 80} for i in range(64)]
assert len(C._clip_spans(arp, [(16.0, 32.0), (48.0, 64.0)])) == 32       # 0.9-step notes are kept

# ----------------------------------------------------------------------------- the 'electro' genre (motorik / 70s)
for alias in ("kraftwerk", "motorik", "krautrock", "70s electro", "synth-pop classic", "electro", "Kraftwerk beat"):
    assert C.genre_data(alias)["name"] == "electro", alias
assert C.genre_data("electro house")["name"] == "house" and C.genre_data("synthpop")["name"] == "pop"
E = C.genre_data("electro")
assert 100 <= E["tempo"]["min"] and E["tempo"]["max"] <= 130 and E["humanize"]["timing_ms"] == 0
for key_ in ("G major", "D mixolydian", "A minor"):
    chs_e, info_e = C.harmony(key_, E, 8, None, 3)
    assert len({c["name"] for c in chs_e}) <= 4 and chs_e[0]["start"] == 0, (key_, info_e)
hits = C.drums(E, 4, 1.0, 2)
hat8 = sorted({h["s"] % 16 for h in hits if h["part"] == "hat"})
assert set(hat8) <= set(range(0, 16, 2)) and len(hat8) >= 6, hat8                # straight 8ths
assert {h["s"] % 16 for h in hits if h["part"] == "snare"} == {4, 12}
bt = C.compose_beat(E, "C major", 4, 1, 0)
assert bt["parts"]["bass"] and bt["parts"]["melody"] and bt["parts"]["chords"], bt["summary"]

# ----------------------------------------------------------------------------- a 4/4 song: arp + pad layers, inserts
FIX = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIX):
    print("skipped: project tests (no tests/corpus/base.flp)"); print("ok"); sys.exit(0)
m._with_open_project = lambda project, output, fn: fn()
compose = ns["TOOLS"]["flp_compose"][0]
src = os.path.join(tmp, "song.flp"); shutil.copy(FIX, src)
m.flp_edit_notes(src, [{"op": "delete"}], pattern=1)
m.flp_playlist(src, "remove", clips=[c["id"] for c in m.flp_playlist(src)["clips"]])
m.flp_style_channels(src, src, [{"channel": 0, "name": "Bass"}, {"channel": 1, "name": "Polysynth"},
                                {"channel": 2, "name": "Lead"}, {"channel": 4, "name": "Pad"}])
BUS = 12
m.flp_mixer(src, src, [{"track": BUS, "name": "Music Bus", "color": "#20304A"}] +
            [{"track": t, "route_to": [BUS], "unroute": [0]} for t in (3, 4, 5, 6)])
rows = []
prog = [(55, [67, 71, 74]), (62, [66, 69, 74]), (52, [67, 71, 76]), (48, [67, 72, 76])]   # G D Em C
for b in range(16):
    bass, tri = prog[b % 4]
    rows.append([0, b * 4, 4.0, bass - 12, 96])
    rows += [[1, b * 4, 3.9, p, 84] for p in tri]
    rows += [[2, b * 4 + i, 0.9, 79 + (i % 3) * 2, 100] for i in range(4)]
m.flp_edit(src, [{"tool": "flp_compose_write", "pattern": "Song", "notes": rows, "place": [[1, 16]], "track": 1}])
bass_ins = N._chan_props(m._load(src), m._events(m._load(src)), 0)["insert"]
r = compose(project=src, what="layer", roles=[{"role": "arp", "where": [[3, 9], [9, 14]]},
                                              {"role": "counter", "where": [[2, 4], [10, 12]]}],
            bars="all", genre="synthwave", key="G major", seed=2)
assert r["verified"], r
d = m._load(src); ev = m._events(d); P = m._ppq(d)
pats = {p["name"]: p["notes"] for p in m._patterns(d, ev).values()}
arp_n = pats.get("Loop Arp") or []
assert len(arp_n) > 50, (len(arp_n), r.get("layers"), r.get("notes"))
assert all(2 * 4 - 0.01 <= x[0] / P < 14 * 4 for x in arp_n), sorted(x[0] / P for x in arp_n)[:4]   # bars 3-14
assert pats.get("Loop Counter"), r
nc = {c["role"]: c for c in r["new_channels"]}
assert set(nc) == {"arp", "counter"}, r["new_channels"]
trk = [k for k, e in enumerate(ev) if e["id"] == m.EV_MIXER_TRACK]


def mix_name(t):
    a = trk[t]
    while a > 0 and ev[a - 1]["id"] in (m.EV_MIXER_COLOR, m.EV_MIXER_HASPROPS, m.EV_MIXER_NAME):
        a -= 1
    return next((m._text16(m._payload(d, ev[k])) for k in range(a, trk[t]) if ev[k]["id"] == m.EV_MIXER_NAME), None)


def routes(t):
    end = trk[t + 1] if t + 1 < len(trk) else len(ev)
    rr = next(m._payload(d, ev[k]) for k in range(trk[t], end) if ev[k]["id"] == m.EV_MIXER_ROUTE)
    return [i for i, b in enumerate(rr) if b]


ins = {}
for role, c in nc.items():
    pr = N._chan_props(d, ev, c["channel"])
    ins[role] = pr["insert"]
    assert pr["insert"] and pr["insert"] != bass_ins and pr["insert"] == c["mixer_insert"], (role, pr, c, bass_ins)
    assert mix_name(pr["insert"]) == c["name"] and routes(pr["insert"]) == [BUS], (role, mix_name(pr["insert"]),
                                                                                  routes(pr["insert"]))
    col = pr["color"]
    assert col and max(int(col[1:3], 16), int(col[3:5], 16), int(col[5:7], 16)) < 0x80, (role, col)   # dark
assert ins["arp"] != ins["counter"], ins
assert N._chan_props(d, ev, 0)["insert"] == bass_ins                                     # the bass keeps its own
import flh_store as ST  # noqa: E402
jr = ST.journal_get(src)
assert jr["channels"][str(nc["arp"]["channel"])].get("mixer") == ins["arp"], jr["channels"]
# a pad at vel 0.7 (a fraction): audible velocities, not 1-6
r2 = compose(project=src, what="layer", roles=[{"role": "pad", "where": [[5, 8]], "vel": 0.7}], bars="all", seed=3,
             realism=False)
d = m._load(src); ev = m._events(d)
pad = [x for p in m._patterns(d, ev).values() if p["name"] == "Loop Pad" for x in p["notes"]]
assert pad and min(x[11] for x in pad) >= 70, sorted({x[11] for x in pad})
assert r2["layers"]["pad"]["vel"] == [83, 95], r2["layers"]
# a where outside the block says why it wrote nothing (never a silent '0 notes')
r3 = compose(project=src, what="layer", roles=[{"role": "bell", "where": [[40, 44]]}], bars="all", seed=3,
             dry_run=True)
assert any("bell: 0 notes" in n for n in r3.get("notes") or []), r3.get("notes")
print("ok")
