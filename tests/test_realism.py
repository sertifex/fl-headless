"""flh_realism: the pure engine (deterministic, bounded timing, breaths shorten the right notes, rolls, legato only
where the library plays it, splits of impossible held notes, pads subtle, keys untouched) and flp_realism on
synthetic projects built with the server's own writers: automation clips that target the right plugin parameters and
sit under the right notes, named playlist tracks, piano and the user's own automation untouched, articulation,
dry run, one batch, re-run idempotent, re-run after edits, undo, channel/section subsets, fallback, conflicts,
realism_expression_ops, the main file's channel automation targets, and robustness on the corpus."""
import glob, json, os, re, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_score, flh_cleanup, flh_realism as R  # noqa: E402
ns = vars(m)
for mod in (flh_store, flh_score, flh_cleanup, R):
    mod.register(ns)
m._with_open_project = lambda project, output, fn, **k: fn()
for t in ("flp_realism", "flp_realism_write"):
    assert t in m.TOOLS, t
assert callable(ns["realism_apply"]) and callable(ns["realism_expression_ops"]) and ns["realism_families"]()["families"]
assert "flh_realism" in m.EXTENSIONS and "flp_realism" in m.ESSENTIAL and "flp_realism" in m.SHORT_DESC
assert m._OPEN_PROJECT_GUARD["flp_realism_write"] == ("project", "output")

P = 96; BAR = 4 * P
FIX = os.path.join(HERE, "corpus", "base.flp")
CC1, CC11 = 0x8000 | 4097, 0x8000 | 4107


# ============================================================================= 1. the pure engine
def ms_ticks(ms, bpm=90):
    return ms / (60000.0 / (bpm * P))


def chords(bars, keys=(55, 60, 64), vel=80):
    return [{"start": b * BAR, "end": (b + 1) * BAR, "pitch": k, "vel": vel} for b in range(bars) for k in keys]


ctx = {"ppq": P, "bpm": 90, "seed": 1, "genre": "cinematic", "part": "Strings",
       "sections": [{"name": "A", "start": 0, "end": 8 * BAR}, {"name": "B", "start": 8 * BAR, "end": 16 * BAR}]}
src = chords(16)
r1 = R.realism_apply(src, "strings", ctx); r2 = R.realism_apply(src, "strings", ctx)
assert (r1["notes"], r1["expression"]) == (r2["notes"], r2["expression"]), "same input -> same output"
r3 = R.realism_apply(src, "strings", dict(ctx, seed=2))
assert r3["notes"] != r1["notes"], "another seed varies"
assert src == chords(16), "input not mutated"
cfg = R.family_cfg("strings")
offs = [n["start"] - o["start"] for n, o in zip(r1["notes"], src)]
assert all(0 <= x <= ms_ticks(cfg["timing"]["max_shift_ms"]) + 1 for x in offs), offs
assert len(set(offs)) >= 2, "section spread: chord notes don't all move together"
assert any(x > 0 for x in offs), "strings speak late"
assert all(n["pitch"] == o["pitch"] for n, o in zip(r1["notes"], src))
ex = r1["expression"]
assert ex and {c["control"] for c in ex["clips"]} == {"dynamics", "expression"} and \
    {c["section"] for c in ex["clips"]} == {"A", "B"}
for c in ex["clips"]:
    ts = [t for t, _ in c["points"]]
    assert ts == sorted(ts) and len(set(ts)) == len(ts) and all(0 <= v <= 1 for _, v in c["points"]), c
    sec = next(s for s in ctx["sections"] if s["name"] == c["section"])
    assert sec["start"] - BAR <= ts[0] and ts[-1] <= sec["end"], (c["section"], ts[0], ts[-1])
dyn = sorted(p for c in ex["clips"] if c["control"] == "dynamics" for p in c["points"])
moves = [max(R.curve_at(dyn, s + k * BAR / 8) for k in range(9)) - min(R.curve_at(dyn, s + k * BAR / 8) for k in range(9))
         for s in range(0, 16 * BAR, BAR)]
assert min(moves) > 0.03, f"the controller moves inside every long note: {moves}"
assert r1["report"]["swells"] >= 16
# legato only where the library plays it (a mono line)
line = [{"start": i * P, "end": (i + 1) * P, "pitch": 60 + (i % 5), "vel": 90} for i in range(16)]
lg = R.realism_apply(line, "strings", dict(ctx, legato=True))["notes"]
nl = R.realism_apply(line, "strings", dict(ctx, legato=False))["notes"]
assert sum(1 for a, b in zip(lg, lg[1:]) if a["end"] > b["start"]) >= 12, "legato library: overlaps"
assert all(a["end"] <= b["start"] for a, b in zip(nl, nl[1:])), "no legato: no overlaps"
# choir: the note before a breath gets shorter (continuous line across the section boundary)
choir = [{"start": i * 2 * P, "end": (i + 1) * 2 * P, "pitch": 64 + (i % 3), "vel": 75, "i": i} for i in range(64)]
rc = R.realism_apply(choir, "choir", ctx)
bn = rc["report"]["breath_notes"]
before_boundary = 8 * BAR // (2 * P) - 1                        # the half note ending at the section boundary
assert before_boundary in bn, bn
nb = rc["notes"][before_boundary]
assert 8 * BAR - nb["end"] >= ms_ticks(0.6 * 260) - 1, nb
assert rc["report"]["breaths"] >= 3, "catch breaths inside the long phrases too"
for k in bn:
    assert rc["notes"][k]["end"] < choir[k]["end"] and rc["notes"][k]["end"] - rc["notes"][k]["start"] >= P // 2
assert all(n["i"] == k for k, n in enumerate(rc["notes"])), "unknown keys pass through"
not_breath = [k for k in range(64) if k not in bn and choir[k]["pitch"] != choir[k + 1 if k < 63 else k]["pitch"]]
assert all(rc["notes"][k]["end"] == choir[k]["end"] for k in not_breath[:-1]), "only breath notes get shorter"
# impossible held notes re-articulate (breath limit)
held = [{"start": 0, "end": 16 * BAR, "pitch": 60, "vel": 80}]
rh = R.realism_apply(held, "choir", ctx)["notes"]
assert len(rh) >= 2 and rh[1].get("realism_split_of") == 0, rh
assert all((n["end"] - n["start"]) * 60000.0 / (90 * P) <= R.family_cfg("choir")["lengths"]["max_sustain_s"] * 1000 + 1
           for n in rh)
# timpani: no drift; a roll goes hand to hand
timp = [{"start": k * BAR, "end": k * BAR + P, "pitch": 43, "vel": 100} for k in range(4)] + \
       [{"start": 4 * BAR + k * P // 8, "end": 4 * BAR + (k + 1) * P // 8, "pitch": 43, "vel": 80} for k in range(16)]
rt = R.realism_apply(timp, "timpani", ctx)
assert rt["expression"] is None
assert all(n["start"] == o["start"] for n, o in zip(rt["notes"][:4], timp[:4])), "single hits stay on the beat"
assert all(abs(n["start"] - o["start"]) <= ms_ticks(2.5) + 1 for n, o in zip(rt["notes"], timp))
rv = [n["vel"] for n in rt["notes"][4:]]
assert sum(1 for a, b, c in zip(rv, rv[1:], rv[2:]) if (b - a) * (c - b) < 0) >= 8, f"hand to hand: {rv}"
assert rt["report"]["roll_notes"] == 16
# keys untouched, lists in -> lists out, pads subtle, amount by genre
rk = R.realism_apply([[0, P, 60, 90]], "keys", ctx)
assert rk["notes"] == [[0, P, 60, 90]] and rk["expression"] is None
rl = R.realism_apply([[s["start"], s["end"], s["pitch"], s["vel"]] for s in src], "strings", ctx)
assert isinstance(rl["notes"][0], list) and [n[:4] for n in rl["notes"]] == \
    [[n["start"], n["end"], n["pitch"], n["vel"]] for n in r1["notes"]]
rp = R.realism_apply(chords(8, (48, 55)), "pad", dict(ctx, genre="edm"))
assert all(abs(n["vel"] - 80) <= 3 for n in rp["notes"]), "pads: subtle"
assert all(abs(n["start"] - o["start"]) <= ms_ticks(8) for n, o in zip(rp["notes"], chords(8, (48, 55))))
assert R.amount_for("edm") < R.amount_for("pop") < R.amount_for("piano ballad") < R.amount_for("orchestral")
assert R.canon_family("woodwind") == "winds" and R.canon_family("bells") == "mallets" and R.canon_family("guitar") == "pluck"
rw = R.realism_apply(line, "woodwind", ctx)
assert rw["report"]["family"] == "winds"
assert R.realism_apply([], "strings", ctx)["notes"] == []
# articulation suggestions
sh = [{"start": k * P // 2, "end": k * P // 2 + P // 5, "pitch": 60 + k % 4, "vel": 100} for k in range(32)]
tr = [{"start": k * P // 4, "end": (k + 1) * P // 4, "pitch": 62, "vel": 90} for k in range(32)]
assert R.suggest_articulations(sh, "strings", {"ppq": P, "bpm": 120})[0]["articulation"] == "short"
assert R.suggest_articulations(tr, "strings", {"ppq": P, "bpm": 120})[0]["articulation"] == "tremolo"
assert R.suggest_articulations(chords(4), "strings", {"ppq": P, "bpm": 90})[0]["articulation"] in ("long", "swell")
pz = [dict(n, vel=50) for n in sh]
assert R.suggest_articulations(pz, "strings", {"ppq": P, "bpm": 120})[0]["articulation"] == "pizz"

# ============================================================================= 2. projects
TPL = os.path.join(tmp, "template.flp"); shutil.copy(FIX, TPL)
m.flp_playlist(TPL, "remove", clips=[c["id"] for c in m.flp_playlist(TPL)["clips"] if c["kind"] == "audio"])
STRS = m.flp_add_channel(TPL, TPL, clone_from=1)["new_channel"]
TIMP = m.flp_add_channel(TPL, TPL, clone_from=2)["new_channel"]


def set_sound(path, ch, family, name, artics=None, active=0):
    """The channel's INSTRUMENT state: META family/name/tags and (optionally) articulation names + the active one."""
    d = m._load(path); ev = m._events(d); _, ks = m._channel_block(d, ev, ch); st = m._payload(d, ev[ks])

    def edit(x):
        x = re.sub(r'(<META [^>]*?)family="[^"]*"', lambda g: g.group(1) + f'family="{family}"', x, count=1)
        x = re.sub(r'(<META [^>]*?)name="[^"]*"', lambda g: g.group(1) + f'name="{name}"', x, count=1)
        x = re.sub(r'(<META [^>]*?)tags="[^"]*"', lambda g: g.group(1) + 'tags="Orchestral"', x, count=1)
        if artics:
            arts = re.findall(r"<ARTIC>.*?</ARTIC>", x, flags=re.S)
            for k, a in enumerate(arts):
                b = re.sub(r'(a_name" value=")[^"]*', lambda g: g.group(1) + artics[k % len(artics)], a, count=1)
                b = re.sub(r'(a_active" value=")[^"]*', lambda g: g.group(1) + ("2" if k == active else "0"), b, count=1)
                x = x.replace(a, b, 1)
        return x
    new = m._juce_xml_rewrite(st, edit)
    m._save(m._splice(d, ev, [(ks, ks + 1, m._data_event(m.EV_PLUGIN_STATE, new))]), path)


set_sound(TPL, 1, "LABS Strings", "Strings Ensemble")
set_sound(TPL, 2, "LABS Choir", "Choir Ahh")
set_sound(TPL, 4, "LABS Pads", "Warm Pad")
set_sound(TPL, STRS, "LABS Strings", "Strings Ensemble", ["Strings Long", "Strings Short"], 0)
set_sound(TPL, TIMP, "LABS Timpani", "Timpani")


def mid(path, nn):
    evs = [(s, bytes([0x90, k, v])) for s, e, k, v in nn] + [(e, bytes([0x80, k, 0])) for s, e, k, v in nn]
    m.midi_write(path, P, [("p", evs)])


def song_parts():
    parts = {0: [(b * BAR + i * P, b * BAR + i * P + P - 4, 60 + (i * 2 + b) % 7, 90) for b in range(16) for i in range(4)],
             1: [(b * BAR, (b + 1) * BAR, k, 80) for b in range(16) for k in (55, 60, 64)],
             2: [(b * BAR + i * 2 * P, b * BAR + (i + 1) * 2 * P, 67 + (b + i) % 3, 75) for b in range(16) for i in range(2)],
             4: [(b * 2 * BAR, (b + 1) * 2 * BAR, k, 70) for b in range(8) for k in (48, 55)],
             STRS: [(b * BAR + k * P // 2, b * BAR + k * P // 2 + P // 5, 67 + k % 3, 100) for b in range(16) for k in range(8)],
             TIMP: [(b * BAR, b * BAR + P, 43, 100) for b in range(0, 16, 2)] +
                   [(7 * BAR + 2 * P + k * P // 8, 7 * BAR + 2 * P + (k + 1) * P // 8, 43, 70 + k) for k in range(16)]}
    return parts


def make(name):
    path = os.path.join(tmp, name + ".flp"); mids = []
    for ch, nn in sorted(song_parts().items()):
        mp = os.path.join(tmp, f"{name}_{ch}.mid"); mid(mp, nn); mids.append({"channel": ch, "midi": mp})
    m.flp_write_arrangement(TPL, path, mids, [{"name": "Verse", "bar": 1}, {"name": "Chorus", "bar": 9}], tempo=90)
    m.flp_style_channels(path, path, [{"channel": 0, "name": "Piano"}, {"channel": 1, "name": "Strings"},
                                      {"channel": 2, "name": "Choir"}, {"channel": 4, "name": "Warm Pad"},
                                      {"channel": STRS, "name": "Strings Short"}, {"channel": TIMP, "name": "Timpani"}])
    m.flp_add_automation(path, path, [{"bar": 1, "db": 0}, {"bar": 5, "db": -6}], track=3, name="My fade")
    return path


def view(path):
    d = m._load(path); nn, _ = m._song(d, m._events(d)); out = {}
    for pos, ln, ch, key, vel in nn:
        out.setdefault(ch, []).append((pos, ln, key, vel))
    return {k: sorted(v) for k, v in out.items()}


def autos(path):
    """{name: {channel, link, points, clips [(pos, len, track)]}} of automation clips."""
    d = m._load(path); ev = m._events(d); names = m._clip_names(d, ev); links = R._existing_links(d, ev)
    ae = m._automation_events(d, ev); out = {}
    clips = {}
    for _, _, r in m._clips(d, ev):
        it = struct.unpack("<H", r[6:8])[0]
        clips.setdefault(it, []).append((struct.unpack("<I", r[0:4])[0], struct.unpack("<I", r[8:12])[0],
                                         499 - struct.unpack("<H", r[12:14])[0] + 1))
    for c, (kind, nm) in names.items():
        if kind == "automation":
            pts = m._auto_points(m._payload(d, ev[ae[c]]))[0] if c in ae else []
            out.setdefault(nm, []).append({"channel": c, "link": links.get(c), "points": [(p[0], p[1]) for p in pts],
                                           "clips": clips.get(c, [])})
    return out


def track_names(path):
    d = m._load(path); return {nm: t for t, (_, nm) in R._track_events(d, m._events(d)).items() if nm}


def active_artic(path, ch):
    d = m._load(path); ev = m._events(d); _, ks = m._channel_block(d, ev, ch)
    names, act = R._artics(R._state_xml(m._payload(d, ev[ks])))
    return names[act]


song = make("song")
v0 = view(song); a0 = autos(song); raw0 = open(song, "rb").read()
assert a0["My fade"][0]["link"] == (0x1FC0, 0x7000 + (3 << 6))
# dry run writes nothing
dr = R.flp_realism(song, dry_run=True)
assert open(song, "rb").read() == raw0 and dr["dry_run"] and dr["would_write"]["clips"] >= 8, dr
names_done = " | ".join(dr["channels"])
assert "Strings (strings)" in names_done and "Choir (choir)" in names_done and "Timpani (timpani)" in names_done, names_done
assert any("Piano" in x for x in dr["left_alone"]), dr["left_alone"]
# the real run: ONE flp_edit batch
calls = []
real_edit = m.TOOLS["flp_edit"]
m.TOOLS["flp_edit"] = (lambda **kw: calls.append(kw) or real_edit[0](**kw),) + tuple(real_edit[1:])
res = R.flp_realism(song)
m.TOOLS["flp_edit"] = real_edit
assert len(calls) == 1, calls
assert res["verify_file"]["ok"], res
print("  " + "\n  ".join(res["channels"]))
assert "late" in res["channels"][0] or any("late" in x for x in res["channels"])
assert any("breaths at" in x for x in res["channels"] if x.startswith("Choir"))
v1 = view(song); a1 = autos(song)
assert v1[0] == v0[0], "piano untouched"
assert v1[1] != v0[1] and v1[2] != v0[2], "strings and choir performed"
assert a1["My fade"] == a0["My fade"], "the user's automation untouched"
assert "Piano dynamics - Verse" not in a1
# automation clips target the right plugin parameters and sit under the right notes
for ch, nm in ((1, "Strings"), (2, "Choir"), (4, "Warm Pad"), (STRS, "Strings Short")):
    for sec, (a, b) in (("Verse", (0, 8 * BAR)), ("Chorus", (8 * BAR, 16 * BAR))):
        dyn = a1[f"{nm} dynamics - {sec}"]; exn = a1[f"{nm} expression - {sec}"]
        assert len(dyn) == 1 and len(exn) == 1, (nm, sec)
        assert dyn[0]["link"] == (CC1, ch) and exn[0]["link"] == (CC11, ch), (nm, dyn[0]["link"], exn[0]["link"])
        notes = [n for n in v0[ch] if a <= n[0] < b]
        (pos, ln, trk), = dyn[0]["clips"]
        assert pos <= min(n[0] for n in notes) and pos >= a - BAR, (nm, sec, pos)
        assert pos + ln >= max(n[0] + n[1] for n in notes) - 1 and pos + ln <= b + 1, (nm, sec, pos, ln)
        assert all(0 <= v <= 1 for _, v in dyn[0]["points"])
        assert track_names(song).get(f"{nm} dynamics") == trk, (nm, track_names(song))
assert not any(k.startswith("Timpani ") for k in a1), "timpani: no controller clips"
tv0 = sorted(n for n in v0[TIMP] if n[1] >= P); tv1 = sorted(n for n in v1[TIMP] if n[1] >= P)
assert [n[0] for n in tv0] == [n[0] for n in tv1], "timpani single hits stay on the beat"
assert active_artic(song, STRS) == "Strings Short", "short passages -> the Short articulation"
j = flh_store.journal_get(song)["realism"]
assert set(j["applied"]) == {"1", "2", "4", str(STRS), str(TIMP)}, j["applied"].keys()
assert m.flp_health(song) is not None and m.flp_playlist(song)["clips"]
# re-run: nothing changes
raw1 = open(song, "rb").read()
again = R.flp_realism(song)
assert again["edit"].startswith("nothing to write") and open(song, "rb").read() == raw1, again["edit"]
# a note edit, then a re-run: starts from the originals, keeps the edit, replaces its own clips (no duplicates)
m.flp_edit_notes(song, [{"op": "transpose", "semitones": 1}], pattern="Verse", channel=2, start_bar=1, end_bar=2)
re_ = R.flp_realism(song)
a2 = autos(song)
assert all(len(v) == 1 for k, v in a2.items()), {k: len(v) for k, v in a2.items() if len(v) > 1}
assert set(a2) == set(a1)
v2 = view(song)
assert v2[1] == v1[1] and v2[4] == v1[4] and v2[0] == v0[0], "unedited parts: same as the first run"
c1 = [n for n in v1[2] if n[0] >= BAR]; c2 = [n for n in v2[2] if n[0] >= BAR]
assert c1 == c2, "choir outside the edit: as before"
assert any("you edited" in x for x in re_.get("notes", [])), re_.get("notes")
# undo: every note back, its clips gone, the user's clip and the articulation as they were
un = R.flp_realism(song, undo=True)
v3 = view(song); a3 = autos(song)
for ch in (0, 1, 4, STRS, TIMP):
    assert v3[ch] == v0[ch], f"channel {ch} back as it was"
assert [n for n in v3[2] if n[0] >= BAR] == [n for n in v0[2] if n[0] >= BAR]
assert a3 == a0, set(a3) ^ set(a0)
assert active_artic(song, STRS) == "Strings Long"
assert not flh_store.journal_get(song)["realism"]["applied"]
# and again after undo
R.flp_realism(song)
assert view(song)[1] == v1[1]

# ---- channel subset and section subset
s2 = make("subset")
R.flp_realism(s2)
vs = view(s2)
R.flp_realism(s2, channels=["Choir"], amount=1.0, seed=5)
vt = view(s2)
assert vt[1] == vs[1] and vt[4] == vs[4], "a channel subset leaves the other channels' realism alone"
assert vt[2] != vs[2]
assert set(flh_store.journal_get(s2)["realism"]["applied"]) == {"1", "2", "4", str(STRS), str(TIMP)}
s3 = make("sections")
v30 = view(s3)
R.flp_realism(s3, sections=["Chorus"], channels=["Strings"])
v31 = view(s3); a31 = autos(s3)
assert [n for n in v31[1] if n[0] < 8 * BAR] == [n for n in v30[1] if n[0] < 8 * BAR], "Verse untouched"
assert [n for n in v31[1] if n[0] >= 8 * BAR] != [n for n in v30[1] if n[0] >= 8 * BAR]
assert "Strings dynamics - Chorus" in a31 and "Strings dynamics - Verse" not in a31
R.flp_realism(s3, sections=["Verse"], channels=["Strings"])
v32 = view(s3); a32 = autos(s3)
assert [n for n in v32[1] if n[0] >= 8 * BAR] == [n for n in v31[1] if n[0] >= 8 * BAR], "Chorus kept"
assert "Strings dynamics - Chorus" in a32 and "Strings dynamics - Verse" in a32
R.flp_realism(s3, undo=True)
assert view(s3)[1] == v30[1]

# ---- fallback (no controller: an FL synth), conflicts, verified parameter indexes
s4 = make("fallback")
real_channels = R._channels


def fake_channels(d, ev):
    out = real_channels(d, ev)
    out[2].update(vst=None, xml=None, preset_name=None, preset_family=None, preset_tags=None, plugin="FLEX")
    out[2]["name"] = "Choir"
    return out


R._channels = fake_channels
rf = R.flp_realism(s4, families={"2": "choir"})
R._channels = real_channels
line_ = next(x for x in rf["channels"] if x.startswith("Choir"))
assert "weaker" in line_ and "channel volume" in line_, line_
af = autos(s4)
vol = af["Choir expression - Verse"][0]
assert vol["link"] == (0, 2), vol["link"]                         # REC_Chan_Vol on channel 2
d4 = m._load(s4); base = R._channels(d4, m._events(d4))[2]["volume"]
assert all(v <= base + 1e-6 for _, v in vol["points"]) and min(v for _, v in vol["points"]) < base
s5 = make("conflict")
m.flp_add_automation(s5, s5, [{"bar": 1, "value": 0.3}, {"bar": 9, "value": 0.9}], channel=1, plugin_param=4097,
                     name="My strings swell")
assert autos(s5)["My strings swell"][0]["link"] == (CC1, 1)
rc5 = R.flp_realism(s5)
a5 = autos(s5)
assert "Strings dynamics - Verse" not in a5 and "Strings expression - Verse" in a5, sorted(a5)
assert any("already automated" in x for x in rc5.get("notes", [])), rc5.get("notes")
s6 = make("params")
R.flp_realism(s6, channels=["Strings"], params={"1": {"dynamics": 12, "expression": 13}})
a6 = autos(s6)
assert a6["Strings dynamics - Verse"][0]["link"] == (0x8000 | 12, 1) and \
    a6["Strings expression - Verse"][0]["link"] == (0x8000 | 13, 1)

# ---- realism_expression_ops (flh_perform / flh_compose write expression in their own batch)
s7 = make("ops")
d7 = m._load(s7); nn = [n for n in R._song_notes(d7, m._events(d7), m._patterns(d7, m._events(d7)), {1})[1]]
ra = R.realism_apply(nn, "strings", {"ppq": m._ppq(d7), "bpm": 90, "seed": 0, "sections": [
    {"name": "Verse", "start": 0, "end": 8 * BAR}, {"name": "Chorus", "start": 8 * BAR, "end": 16 * BAR}]})
ops = R.realism_expression_ops(s7, 1, ra["expression"], {"ppq": m._ppq(d7)})
assert ops and ops[0]["tool"] == "flp_realism_write"
m.flp_edit(s7, ops)
a7 = autos(s7)
assert a7["Strings dynamics - Verse"][0]["link"] == (CC1, 1)
R.flp_realism(s7, channels=["Strings"])
a7b = autos(s7)
assert all(len(v) == 1 for v in a7b.values()), "flp_realism takes over the clips it named (no duplicates, no conflict)"
assert R.realism_expression_ops(s7, 0, None) == []

# ---- keyswitch libraries (user data): one keyswitch note per section, removed again by undo
ud = os.path.join(os.path.dirname(m.STATE), "realism"); os.makedirs(ud, exist_ok=True)
with open(os.path.join(ud, "libraries.json"), "w") as f:
    json.dump({"libraries": [{"id": "spitfire_labs", "keyswitches": {"long": 24, "short": 25}}]}, f)
assert R.data("libraries")["libraries"][0]["id"] == "spitfire_bbc", "user entries merge by id"
assert next(x for x in R.data("libraries")["libraries"] if x["id"] == "spitfire_labs")["keyswitches"]["short"] == 25
s9 = make("keyswitch")
v90 = view(s9)
k9 = R.flp_realism(s9, channels=["Strings Short"])
v91 = view(s9)
ksn = [n for n in v91[STRS] if n[2] in (24, 25)]
assert [n[2] for n in sorted(ksn)] == [25, 25], (ksn, k9["channels"])       # Verse and Chorus both short
assert all(n[0] < 8 * BAR for n in ksn[:1]) and sorted(ksn)[1][0] < 8 * BAR + P, sorted(ksn)
assert active_artic(s9, STRS) == "Strings Long", "keyswitch libraries keep the loaded patch"
R.flp_realism(s9, undo=True)
assert view(s9)[STRS] == v90[STRS]
os.remove(os.path.join(ud, "libraries.json"))

# ---- the main file: channel automation targets
s8 = os.path.join(tmp, "auto.flp"); shutil.copy(FIX, s8)
r8 = m.flp_add_automation(s8, s8, [{"bar": 1, "value": 0.2}, {"bar": 2, "value": 0.8}], channel=1, plugin_param=4107)
r9 = m.flp_add_automation(s8, s8, [{"bar": 1, "value": 0.5}, {"bar": 2, "value": 0.7}], channel=2, param="volume",
                          name="ch2 vol")
a8 = autos(s8)
assert a8[r8["name"]][0]["link"] == (CC11, 1) and a8["ch2 vol"][0]["link"] == (0, 2), (r8, a8.get("ch2 vol"))
r10 = m.flp_add_automation(s8, s8, [{"bar": 1, "value": 0.1}, {"bar": 3, "value": 0.9}], channel=2, param="volume",
                           name="ch2 vol", replace=True)
assert r10.get("replaced") and len(autos(s8)["ch2 vol"]) == 1
for bad in ({"channel": 99, "param": "volume"}, {"channel": 1, "param": "cutoff"}):
    try:
        m.flp_add_automation(s8, s8, [{"bar": 1, "value": 0}, {"bar": 2, "value": 1}], **bad)
        raise AssertionError(bad)
    except ValueError:
        pass

# ---- robustness on the corpus: never crashes, file still parses, undo leaves no clip of ours
bad = []
for f in sorted(glob.glob(os.path.join(HERE, "corpus", "*.flp"))):
    c = os.path.join(tmp, "c_" + os.path.basename(f)); shutil.copy(f, c)
    try:
        R.flp_realism(c, dry_run=True)
        R.flp_realism(c, channels="auto", amount=1.0)
        R.flp_realism(c, channels=[0], families={"0": "strings"})
        m._events(m._load(c)); m.flp_playlist(c)
        R.flp_realism(c, undo=True)
        left = [k for k in autos(c) if re.search(r" (dynamics|expression) - ", k)]
        assert not left, left
    except Exception as e:                                       # a clear refusal is fine; a crash is not
        if not isinstance(e, (ValueError, FileNotFoundError)) or "Traceback" in str(e):
            bad.append(f"{os.path.basename(f)}: {type(e).__name__}: {e}")
assert not bad, "\n".join(bad)
print("ok")
