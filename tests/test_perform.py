"""flh_perform: the performance engine reproduces a stored reference rendering from a spec; section edits stay in their section;
deterministic; flp_perform writes one batch of only what changed; dry_run writes nothing; undo restores.
Auto setup (any song): style none is an exact identity; deterministic; drums keep their timing under expressive;
3/4, 6/8 and 7/8 meters; audio-clip tempo guard; quantize + undo to the original; edit guard."""
import contextlib, copy, io, json, os, shutil, struct, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
DATA = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "perform")
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ.setdefault("LOCALAPPDATA", tmp)
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_perform as P  # noqa: E402
ns = vars(m); flh_store.register(ns); P.register(ns)
for t in ("flp_perform", "flp_perform_setup", "flp_perform_undo"):
    assert t in ns["TOOLS"], t
import glob  # noqa: E402
_specs = sorted(glob.glob(os.path.join(DATA, "*_perform.json")))
if not os.path.isfile(FIX):
    print("skipped: no project fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
HAVE_REF = bool(_specs) and os.path.isfile(os.path.join(DATA, "v8_expected.json"))
if HAVE_REF:
    SPEC_FILE = _specs[0]
    spec = json.load(open(SPEC_FILE))
    ref = json.load(open(os.path.join(DATA, "v8_expected.json")))
    # the reference song's sections, by position (the spec is private data; no names hard-coded here)
    SEC = [x_["name"] for x_ in spec["sections"]]
    S_HEART, S_THEME, S_RISE, S_STATE, S_CHANT, S_PEAK = SEC[1], SEC[2], SEC[3], SEC[4], SEC[5], SEC[6]
else:
    print("skipped: parts 1-5 (reference reproduction: needs the private spec in tests/data/perform, or "
          "FLH_TEST_DATA); the auto-setup parts 6-14 run on the committed fixture")

TPL = os.path.join(tmp, "template.flp"); shutil.copy(FIX, TPL)                # one pattern clip, no audio clip
m.flp_playlist(TPL, "remove", clips=[c["id"] for c in m.flp_playlist(TPL)["clips"] if c["kind"] == "audio"])
calls = []
real_edit = ns["flp_edit"]


def rng_of(render, name):
    s = next(x for x in render["sections"] if x["name"] == name)
    return s["start"], (s["end"] if s["end"] is not None else 10 ** 9)


def outside(render, lo, hi, pad=30):
    return sorted((n[0], n[2], n[3]) for v in render["parts"].values() for n in v if n[0] < lo - pad or n[0] >= hi + pad)


def inside(render, lo, hi):
    return sorted((n[0], n[2], n[3]) for v in render["parts"].values() for n in v if lo <= n[0] < hi)


if HAVE_REF:
    def match(want, got, tol_t=1, tol_v=1):
        """matched notes: same pitch, start within tol_t ticks, velocity within tol_v (in order)."""
        if len(want) != len(got):
            return 0
        return sum(1 for a, b in zip(want, got) if a[2] == b[2] and abs(a[0] - b[0]) <= tol_t and abs(a[3] - b[3]) <= tol_v)


    # ---- 1. the engine reproduces the stored reference result from the spec
    t0 = time.time(); r = P.perform(spec, base_dir=DATA); ms = (time.time() - t0) * 1000
    for part, want in ref["parts"].items():
        got = r["parts"][part]
        assert got == want, (part, len(want), len(got), match(want, got))
    assert r["tempo"] == ref["tempo"], "tempo map differs from the reference"
    print(f"reference reproduced exactly: {sum(len(v) for v in ref['parts'].values())} notes, {len(ref['tempo'])} tempo "
          f"points ({ms:.0f} ms)")

    # ---- 2. deterministic
    assert P.perform(copy.deepcopy(spec), base_dir=DATA)["parts"] == r["parts"]
    sec_spec = dict(copy.deepcopy(spec), rng="section")
    a1 = P.perform(sec_spec, base_dir=DATA); a2 = P.perform(copy.deepcopy(sec_spec), base_dir=DATA)
    assert a1["parts"] == a2["parts"] and a1["tempo"] == a2["tempo"]


    # ---- 3. a change to one section changes only that section
    for base in (spec, sec_spec):
        b = P.perform(base, base_dir=DATA)
        new, touched = P.apply_changes(base, {S_THEME: {"humanize": "-30%", "dyn": "+4"}})
        assert touched == [S_THEME], touched
        a = P.perform(new, base_dir=DATA); lo, hi = rng_of(a, S_THEME)
        assert outside(a, lo, hi) == outside(b, lo, hi), f"a one-section edit leaked into other sections ({base['rng']})"
        assert inside(a, lo, hi) != inside(b, lo, hi)
        assert a["tempo"] == b["tempo"]
        st_a, st_b = P.section_stats(a), P.section_stats(b)
        assert st_a[S_THEME]["vel"] > st_b[S_THEME]["vel"] + 3, (st_a[S_THEME], st_b[S_THEME])
        assert all(st_a[k] == st_b[k] for k in st_a if k not in (S_THEME, S_HEART, S_RISE)), "stats moved elsewhere"
    # tempo: notes untouched, bpm changes only in that section
    new, _ = P.apply_changes(spec, {S_CHANT: {"tempo": "-3%"}})
    assert new["sections"][5]["stretch"] == round(0.98 / 0.97, 6)
    a = P.perform(new, base_dir=DATA)
    assert a["parts"] == r["parts"], "a tempo change must not move notes (they are in ticks)"
    lo, hi = rng_of(a, S_CHANT)


    def bpm_at(tm, x):
        return [b for t, b in tm if t <= x][-1]


    for x in range(0, r["end_tick"], 240):
        if not (lo - 240 <= x < hi + 240):
            assert abs(bpm_at(a["tempo"], x) - bpm_at(r["tempo"], x)) < 0.4 + 1e-9, x
    sa, sb = P.section_stats(a), P.section_stats(r)
    assert abs(sa[S_CHANT]["bpm"] / sb[S_CHANT]["bpm"] - 0.97) < 0.005, (sa[S_CHANT], sb[S_CHANT])
    assert all(abs(sa[k]["bpm"] - sb[k]["bpm"]) <= 0.1 for k in sa if k not in (S_CHANT,)), "other sections' bpm moved"
    new, _ = P.apply_changes(spec, {S_PEAK: {"tempo": 1.02}, S_STATE: {"bpm": 66}},
                             render_fn=lambda s: P.section_stats(P.perform(s, base_dir=DATA)))
    s2 = P.section_stats(P.perform(new, base_dir=DATA))
    assert abs(s2[S_STATE]["bpm"] - 66) < 0.1, s2[S_STATE]
    assert new["sections"][6]["stretch"] == round(1 / 1.02, 6)
    try:
        P.apply_changes(spec, {"Nope": {"tempo": "-3%"}}); raise SystemExit("unknown section must fail")
    except ValueError as e:
        assert S_CHANT in str(e)

    # tempo events (rit / fermata) shape only the tempo map, inside their range
    ev = copy.deepcopy(spec); ev["events"] += [{"type": "fermata", "bar": 12, "beat": 3, "beats": 1, "stretch": 1.8},
                                               {"type": "rit", "from": 45, "to": 47, "amount": 1.3}]
    a = P.perform(ev, base_dir=DATA)
    assert a["parts"] == r["parts"]
    f0, f1 = P._Ctx(P.normalize(ev), P.load_source(os.path.join(DATA, spec["source"]))).tick(12, 3), None
    assert bpm_at(a["tempo"], f0 + 120) < bpm_at(r["tempo"], f0 + 120) / 1.5
    assert bpm_at(a["tempo"], f0 - 2000) == bpm_at(r["tempo"], f0 - 2000)
    # other source layouts: parts from tracks, [start, end] dynamics, plain defaults
    mini = {"source": spec["source"], "roles": {"method": "tracks"}, "parts": {"piano": {"track": 0, "role": "mel", "channel": 0}},
            "sections": [{"name": "A", "bar": 1, "dyn": [50, 70]}, {"name": "B", "bar": 20, "dyn": 80, "tight": True}]}
    mr = P.perform(mini, base_dir=DATA)
    assert len(mr["parts"]["piano"]) == len(P.load_source(os.path.join(DATA, spec["source"]))["notes"])
    assert P.perform(copy.deepcopy(mini), base_dir=DATA) == mr
    ms_ = P.section_stats(mr); assert ms_["A"]["vel"] < ms_["B"]["vel"], ms_

    # ---- 4. tools: setup, perform (mocked flp_edit), dry run, undo
    raw = os.path.join(tmp, "raw.flp"); shutil.copy(FIX, raw)
    try:
        P.flp_perform(raw); raise SystemExit("no spec must fail")
    except ValueError as e:
        assert "flp_perform_setup" in str(e)
    bad = copy.deepcopy(spec); bad["parts"]["bass"]["channel"] = 1
    try:
        P.flp_perform_setup(raw, spec=dict(bad, source=os.path.join(DATA, spec["source"]))); raise SystemExit("dup channel")
    except ValueError as e:
        assert "channel 1" in str(e)
    bad = copy.deepcopy(spec); bad["sections"][3]["bar"] = 2
    try:
        P.flp_perform_setup(raw, spec=dict(bad, source=os.path.join(DATA, spec["source"]))); raise SystemExit("order")
    except ValueError as e:
        assert "bar order" in str(e)
    su = P.flp_perform_setup(raw, spec_file=SPEC_FILE)
    assert su["stored"] and su["parts"]["melody"]["notes"] == len(ref["parts"]["melody"]), su
    assert any("Tempo" in w for w in su.get("warnings", [])), su            # the fixture has no Tempo lane
    # the rest runs on an arranged copy that has a Tempo lane and no audio clip (the guard is tested in section 7)
    filler = os.path.join(tmp, "filler.mid"); last = r["end_tick"] + 480 * 16
    m.midi_write(filler, 480, [("f", [(0, b"\xff\x51\x03" + (1000000).to_bytes(3, "big")), (0, bytes([0x90, 30, 1])),
                                      (last, bytes([0x80, 30, 0]))])])
    proj = os.path.join(tmp, "song.flp")
    m.flp_write_arrangement(TPL, proj, [{"channel": 4, "midi": filler}], [{"name": "Song", "bar": 1}], tempo="auto")
    assert not P._audio_clips(proj) and P._tempo_lane(proj, "Tempo")
    su = P.flp_perform_setup(proj, spec_file=SPEC_FILE)
    assert not su.get("warnings"), su
    spec0 = flh_store.journal_get(proj)["performance"]
    ns["flp_edit"] = lambda project, ops, output=None: (calls.append((project, ops, output)) or
                                                        {"ops": len(ops), "results": [{"tool": o["tool"]} for o in ops]})
    res = P.flp_perform(proj)
    assert len(calls) == 1, calls
    ops = calls[0][1]
    assert [o["tool"] for o in ops] == ["flp_replace_part"] * 3 + ["flp_tempo_write"], [o["tool"] for o in ops]
    assert {o["channel"] for o in ops[:3]} == {0, 1, 2}
    ppq = m._ppq(m._load(proj)); bpb = 4
    mel = next(o for o in ops if o.get("channel") == 2)["notes"]
    assert len(mel) == len(ref["parts"]["melody"])
    for n, (s, e, p0, v0) in zip(mel, ref["parts"]["melody"]):  # exact song ticks (the MIDI-import rounding)
        assert n["tick"] == s * ppq // 480 and n["key"] == p0 and n["vel"] == v0, n
        assert n["tick"] + n["ticks"] == max(s * ppq // 480 + 1, e * ppq // 480)
    assert len(ops[3]["points"]) == len(ref["tempo"]) and ops[3]["points"][0] == [1.0, ref["tempo"][0][1]]
    # nothing changed -> nothing written
    calls.clear(); res = P.flp_perform(proj)
    assert not calls and "nothing" in str(res["edit"]), res
    # tempo-only change -> only the tempo map
    t0 = time.time(); res = P.flp_perform(proj, changes={S_CHANT: {"tempo": "-3%"}}); ms_tempo = (time.time() - t0) * 1000
    assert len(calls) == 1 and [o["tool"] for o in calls[0][1]] == ["flp_tempo_write"], calls[0][1]
    assert res["sections_changed"] == [S_CHANT] or S_CHANT in res["sections_changed"], res
    print("\n".join(res["table"]))
    # a dynamics change -> only the parts with notes in the section, no tempo
    calls.clear(); res = P.flp_perform(proj, changes={S_THEME: {"humanize": "-30%", "dyn": "+4"}})
    tools = [o["tool"] for o in calls[0][1]]
    assert "flp_tempo_write" not in tools and tools.count("flp_replace_part") >= 1, tools
    j = flh_store.journal_get(proj)
    assert len(j["performance_history"]) == 2 and j["performance"]["sections"][2]["dyn"][0][1] == 52 + 4
    # dry run writes nothing
    jb = open(flh_store.journal_path(proj), "rb").read(); fb = open(proj, "rb").read(); calls.clear()
    t0 = time.time(); dr = P.flp_perform(proj, changes={S_PEAK: {"dyn": "+6", "tempo": 1.02}}, dry_run=True)
    ms_dry = (time.time() - t0) * 1000
    assert dr["dry_run"] and not calls and dr["write"]["tempo"] and dr["write"]["parts"], dr
    assert open(flh_store.journal_path(proj), "rb").read() == jb and open(proj, "rb").read() == fb
    assert any(row.startswith(S_PEAK + " *") and "->" in row for row in dr["table"]), dr["table"]
    # undo: the section edit goes away, the project gets the parts back
    calls.clear(); u = P.flp_perform_undo(proj)
    j = flh_store.journal_get(proj)
    assert j["performance"]["sections"][2]["dyn"][0][1] == 52 and len(j["performance_history"]) == 1
    assert [o["tool"] for o in calls[0][1]].count("flp_replace_part") >= 1
    und = P.perform(j["performance"], base_dir=os.path.dirname(proj))
    assert P._digests(und) == {k: v for k, v in j["performance_applied"].items() if k in ("parts", "tempo")}
    calls.clear(); P.flp_perform_undo(proj)                                 # back to the setup spec
    j = flh_store.journal_get(proj)
    assert P._digest(j["performance"]) == P._digest(spec0) and not j["performance_history"]
    assert P.perform(j["performance"], base_dir=os.path.dirname(proj))["parts"] == r["parts"]
    assert [o["tool"] for o in calls[0][1]] == ["flp_tempo_write"]          # notes were already the setup ones
    try:
        P.flp_perform_undo(proj); raise SystemExit("empty history must fail")
    except ValueError:
        pass

    # ---- 5. a real batch write (melody + the tempo map) lands on the right ticks
    ns["flp_edit"] = real_edit
    res = P.flp_perform(proj, parts=["melody", "tempo"])
    d = m._load(proj); notes, tmap_ = m._song(d, m._events(d))
    got = sorted((n[0], n[3], n[4]) for n in notes if n[2] == 2)
    want = sorted((s * ppq // 480, p, v) for s, e, p, v in ref["parts"]["melody"])
    assert got == want, (len(got), len(want), [x for x in want if x not in got][:3])
    assert [(t, b) for t, b in tmap_] == [(t * ppq // 480, b) for t, b in ref["tempo"]], "tempo map not written exactly"
    print(f"real flp_edit: melody ({len(got)} notes) and tempo map ({len(tmap_)} points) on the right ticks")
    print(f"timing: render {ms:.0f} ms, flp_perform tempo edit {ms_tempo:.0f} ms, dry run {ms_dry:.0f} ms")
    assert ms_dry < 1000 and ms_tempo < 1000

m._with_open_project = lambda project, output, fn: fn()

# ---- 6. auto setup from any project: helpers to build synthetic songs with the main file's writers
import random as _random  # noqa: E402
PPQ = 96


def view(project, channels=None):
    d_ = m._load(project); nn, tm = m._song(d_, m._events(d_)); out = {}
    for pos, ln, ch, key, vel in nn:
        if channels is None or ch in channels:
            out.setdefault(ch, []).append([pos, pos + max(1, ln), key, max(1, min(127, vel))])
    return {k: sorted(v) for k, v in out.items()}, [(int(t), float(b)) for t, b in tm]


def make_song(name, parts, bpb=3 if False else 4, sig=None, bpm=120.0, sections=None, tempo_lane=True, names=None):
    """parts {channel: [(s, e, key, vel)] at 96 PPQ} -> an arranged FL project (one pattern per section)."""
    path = os.path.join(tmp, name + ".flp"); mids = []
    for i, (ch, nn) in enumerate(sorted(parts.items())):
        mp = os.path.join(tmp, f"{name}_{ch}.mid")
        evs = [(s_, bytes([0x90, k, v])) for s_, e_, k, v in nn] + [(e_, bytes([0x80, k, 0])) for s_, e_, k, v in nn]
        if i == 0 and tempo_lane:
            evs.append((0, b"\xff\x51\x03" + int(round(60e6 / bpm)).to_bytes(3, "big")))
        m.midi_write(mp, PPQ, [("p", evs)]); mids.append({"channel": ch, "midi": mp})
    m.flp_write_arrangement(TPL, path, mids, sections or [{"name": "Song", "bar": 1}],
                            tempo="auto" if tempo_lane else bpm, beats_per_bar=bpb)
    if sig:
        m.flp_project_info(path, path, time_sig_num=sig[0], time_sig_beat=sig[1])
    if names:
        m.flp_style_channels(path, path, [{"channel": c, "name": n} for c, n in names.items()])
    return path


def band(bars, bar_len=4 * PPQ, human=0, seed=3, beats=4):
    """melody (ch 2), chords (ch 1), bass (ch 0), GM drums (ch 4); human = max random tick offset."""
    rnd = _random.Random(seed); out = {0: [], 1: [], 2: [], 4: []}

    def h():
        return rnd.randint(-human, human) if human else 0
    q = PPQ; e8 = PPQ // 2
    for b in range(bars):
        t0 = b * bar_len
        for k in range(beats):                                       # bass: quarters
            s_ = max(0, t0 + k * q + h()); out[0].append((s_, s_ + q - 10, 36 + (b + k) % 5, 90 + rnd.randint(-8, 8)))
        for k in range(0, beats, 2):                                 # chords: half notes, 3 voices
            s_ = max(0, t0 + k * q + h())
            for p_ in (55, 59, 62):
                out[1].append((s_, s_ + 2 * q - 6, p_ + b % 3, 70 + rnd.randint(-6, 6)))
        for k in range(beats * 2):                                   # melody: 8ths
            s_ = max(0, t0 + k * e8 + h()); out[2].append((s_, s_ + e8 - 4, 67 + (b * 3 + k) % 9, 85 + rnd.randint(-10, 10)))
        for k in range(beats * 2):                                   # drums: hats on 8ths, kick 1/3, snare 2/4
            s_ = max(0, t0 + k * e8 + h()); out[4].append((s_, s_ + 12, 42, 80 + rnd.randint(-10, 10)))
            if k % 2 == 0:
                out[4].append((s_, s_ + 12, 36 if (k // 2) % 2 == 0 else 38, 100))
    return out


def setup(project, **kw):
    return P.flp_perform_setup(project, **kw)


def spec_of(project):
    return flh_store.journal_get(project)["performance"]


def render_of(project):
    return P.perform(spec_of(project), base_dir=os.path.dirname(project))


def by_channel(project, rd):
    sp = spec_of(project)
    return {int(sp["parts"][k]["channel"]): v for k, v in rd["parts"].items()}


# 6a. identity: style none on a humanized 4/4 band -> nothing moves, nothing is written; a forced write round-trips
song = make_song("band", band(12, human=5), sections=[{"name": "Verse", "bar": 1}, {"name": "Chorus", "bar": 5},
                                                      {"name": "Outro", "bar": 9}])
before = open(song, "rb").read(); v0, t0_ = view(song)
su = setup(song, style="none")
roles = {x["channel"]: x["role"] for x in su["parts"].values() for _ in [0]} if False else \
    {v["channel"]: v["role"] for v in su["parts"].values()}
assert roles == {0: "bass", 1: "accompaniment", 2: "melody", 4: "drums"}, roles
assert su["sections_from"] == "section pattern clips" and "quantized" not in su and not su["writes_tempo_map"], su
rd = render_of(song)
assert by_channel(song, rd) == v0, "style none must play the project's notes exactly"
assert [tuple(x) for x in rd["tempo"]] == t0_
dr = P.flp_perform(song, dry_run=True)
assert dr["write"] == {"parts": [], "tempo": False}, dr
res = P.flp_perform(song)
assert "nothing" in str(res["edit"]) and open(song, "rb").read() == before, res
res = P.flp_perform(song, parts=list(spec_of(song)["parts"]))           # forced real write of every part
assert view(song) == (v0, t0_), "a forced identity write changed the notes"
print(f"identity (style none): {sum(len(v) for v in v0.values())} notes exactly where they were; forced write "
      "round-trips")
# the same on the fixture project (one long pattern, audio clip, 4 channels) and an extra project from
# FLH_TEST_PROJECT if set
for f in [FIX] + [p_ for p_ in [os.environ.get("FLH_TEST_PROJECT", "")] if p_ and os.path.isfile(p_)]:
    cp = os.path.join(tmp, "id_" + os.path.basename(f)); shutil.copy(f, cp); vv = view(cp)
    s_ = setup(cp, style="none"); rr = render_of(cp)
    assert by_channel(cp, rr) == {c: vv[0][c] for c in by_channel(cp, rr)}, f
    assert P.flp_perform(cp, dry_run=True)["write"] == {"parts": [], "tempo": False}
    print(f"identity on {os.path.basename(f)}: {sum(len(v) for v in by_channel(cp, rr).values())} notes, sections "
          f"from {s_['sections_from']}, roles {sorted(x['role'] for x in s_['parts'].values())}")

# 6b. determinism: the same project gives the same spec and render, every time
a1 = os.path.join(tmp, "det1.flp"); a2 = os.path.join(tmp, "det2.flp")
shutil.copy(song, a1); shutil.copy(song, a2)
for x in (a1, a2):
    setup(x, style="natural")
s1, s2 = spec_of(a1), spec_of(a2)
assert json.dumps(s1).replace("det1.", "X.") == json.dumps(s2).replace("det2.", "X.")
assert open(os.path.join(tmp, s1["source"]), "rb").read() == open(os.path.join(tmp, s2["source"]), "rb").read()
assert render_of(a1)["parts"] == render_of(a2)["parts"] and render_of(a1)["parts"] == render_of(a1)["parts"]

# 6c. styles: only expressive writes a tempo map; drums keep their exact timing under expressive
clean = make_song("clean", band(12, human=0), sections=[{"name": "A", "bar": 1}, {"name": "B", "bar": 7}])
cv, ct = view(clean)
for st in ("tight", "natural", "swing", "laid-back", "expressive"):
    su = setup(clean, style=st); rd = render_of(clean); bc = by_channel(clean, rd)
    assert su["writes_tempo_map"] == (st == "expressive"), (st, su["writes_tempo_map"])
    if st != "expressive":
        assert [tuple(x) for x in rd["tempo"]] == ct, st
        assert P.flp_perform(clean, dry_run=True)["write"]["tempo"] is False, st
    drums_t = [(n[0], n[1], n[2]) for n in bc[4]]; src_t = [(n[0], n[1], n[2]) for n in cv[4]]
    if st == "expressive":
        assert drums_t == src_t, f"{st} moved the drums"
    elif st == "swing":                                                  # the groove swings as one
        assert len({a[0] - b[0] for a, b in zip(drums_t, src_t) if b[0] % 96 == 48}) <= 3, \
            (st, sorted({a[0] - b[0] for a, b in zip(drums_t, src_t) if b[0] % 96 == 48}), drums_t[:6], src_t[:6])
    else:
        assert max(abs(a[0] - b[0]) for a, b in zip(drums_t, src_t)) <= 1, st      # drum_clip (3 ticks @480)
    assert sorted(n[2] for n in bc[4]) == sorted(n[2] for n in cv[4]), "drum pitches must never change"
    if st == "expressive":
        assert bc[2] != cv[2] and len(rd["tempo"]) > 10 and min(b for _, b in rd["tempo"]) < 120 * 0.9
        assert any(n[1] > o[1] for n, o in zip(bc[2], cv[2])), "melody legato"
        assert spec_of(clean)["events"][-1]["type"] == "rit"
    if st == "laid-back":
        assert sum(n[0] - o[0] for n, o in zip(bc[2], cv[2])) / len(cv[2]) > 1.5, "melody should sit behind the beat"
    if st == "swing":
        offs = [n[0] - o[0] for n, o in zip(bc[2], cv[2]) if o[0] % 96 == 48]
        ons = [n[0] - o[0] for n, o in zip(bc[2], cv[2]) if o[0] % 96 == 0]
        assert 8 <= sum(offs) / len(offs) <= 11 and abs(sum(ons) / len(ons)) < 1, (offs[:8], ons[:8])
print("styles: tight/natural/swing/laid-back write no tempo map; expressive rubato leaves drum timing exact")
# per-section edits stay relative and in their section; source-velocity dyn edits shift
setup(clean, style="natural"); base_r = render_of(clean)
new, touched = P.apply_changes(spec_of(clean), {"B": {"humanize": "+50%", "dyn": "+6"}},
                               render_fn=lambda s: P.section_stats(P.perform(s, base_dir=tmp)))
assert touched == ["B"] and new["sections"][1]["vel_offset"] == 6
ar = P.perform(new, base_dir=tmp); sa_, sb_ = P.section_stats(ar), P.section_stats(base_r)
lo, hi = rng_of(ar, "B")
assert outside(ar, lo, hi) == outside(base_r, lo, hi) and sa_["B"]["vel"] > sb_["B"]["vel"] + 4, (sa_, sb_)
new, _ = P.apply_changes(spec_of(clean), {"A": {"dyn": 50}}, render_fn=lambda s: P.section_stats(P.perform(s, base_dir=tmp)))
assert abs(P.section_stats(P.perform(new, base_dir=tmp))["A"]["vel"] - 50) < 1.5

# 6d. meters: 3/4 and 6/8 bars, meter-aware accents; a 7/8 change from a playlist signature marker
w34 = make_song("waltz", band(9, bar_len=3 * PPQ, beats=3, human=4), bpb=3)
su = setup(w34, style="none"); src = P.load_source(os.path.join(tmp, spec_of(w34)["source"]))
assert src["meters"] == [(0, 3, 4)] and su["meter"] == ["3/4"], (src["meters"], su["meter"])
assert by_channel(w34, render_of(w34)) == view(w34)[0]
assert P._Ctx(P.normalize(spec_of(w34)), src).bars[1] == (288, 288)
assert spec_of(w34)["sections"] == [{"name": "Bars 1-8", "bar": 1.0}, {"name": "Bars 9-10", "bar": 9.0}], spec_of(w34)["sections"]
j68 = make_song("jig", band(8, bar_len=3 * PPQ, beats=3), bpb=3, sig=(6, 8))
su = setup(j68, style="none"); sp = spec_of(j68); src = P.load_source(os.path.join(tmp, sp["source"]))
assert src["meters"] == [(0, 6, 8)] and su["meter"] == ["6/8"]
assert by_channel(j68, render_of(j68)) == view(j68)[0]
acc = dict(copy.deepcopy(sp), defaults={"accent": {"downbeat": 20, "beat": 10, "pulse": "meter"}})
ra_ = P.perform(acc, base_dir=tmp); bass = {n[0]: n[3] for n in by_channel(j68, ra_)[4] if n[2] == 42}  # hats on 8ths
orig = {n[0]: n[3] for n in view(j68)[0][4] if n[2] == 42}
assert bass[288] - orig[288] == 20 and bass[288 + 144] - orig[288 + 144] == 10 and bass[288 + 96] == orig[288 + 96], \
    "6/8 accents fall on the dotted-quarter pulse"
# 7/8 from bar 5: insert a playlist time-signature marker (FL: 148 position | 0x08000000, 33 num, 34 den, 205 name)
odd = make_song("odd", band(12, human=0), sections=[{"name": "Song", "bar": 1}])
d_ = m._load(odd); ev_ = m._events(d_); k99 = next(k for k, e in enumerate(ev_) if e["id"] == 99)
mark = (bytes([148]) + struct.pack("<I", (4 * 4 * PPQ) | 0x08000000) + bytes([33, 7, 34, 8])
        + m._data_event(205, "7/8".encode("utf-16-le") + b"\x00\x00"))
m._save(m._splice(d_, ev_, [(k99 + 1, k99 + 1, mark)]), odd)
pr = P._project_read(odd)
assert pr["meters"] == [(0, 4, 4), (4 * 4 * PPQ, 7, 8)] and pr["markers"][0]["signature"], pr["meters"]
su = setup(odd, style="natural"); sp = spec_of(odd); src = P.load_source(os.path.join(tmp, sp["source"]))
cx = P._Ctx(P.normalize(sp), src)
assert src["meters"] == [(0, 4, 4), (1536, 7, 8)] and cx.bars[4] == (1536, 336) and cx.bars[5] == (1872, 336)
assert sp["sections"][1]["bar"] == 9.0 and cx.tick(9) == 1536 + 4 * 336 and su["meter"] == ["4/4", "7/8 @bar 5.0"]
assert by_channel(odd, P.perform(dict(sp, defaults={}), base_dir=tmp)) == view(odd)[0]
print("meters: 3/4, 6/8 (dotted-quarter accents) and a 7/8 change from a playlist marker")

# 6e. quantize: humanized notes snap to their grid for the clean source; the original is the first history entry and
# flp_perform_undo brings it back exactly
hum = make_song("hum", band(8, human=4, seed=9), sections=[{"name": "A", "bar": 1}, {"name": "B", "bar": 5}])
hv = view(hum)
su = setup(hum, style="tight")
assert set(su["quantized"]) == {p_ for p_ in su["parts"]}, su.get("quantized")
assert all(q["grid"] == "1/16" for q in su["quantized"].values()), su["quantized"]
src = P.load_source(os.path.join(tmp, spec_of(hum)["source"]))
assert all(n["s"] % 24 == 0 for n in src["notes"]), "clean source on the 1/16 grid"
j = flh_store.journal_get(hum)
assert j["performance_history"][0]["changes"].startswith("original") and \
    j["performance_history"][0]["spec"]["source"].endswith("perform_original.mid")
res = P.flp_perform(hum)
assert set(res["write"]["parts"]) == set(su["parts"]) and not res["write"]["tempo"], res["write"]
assert view(hum)[0] != hv[0]
P.flp_perform_undo(hum)
assert view(hum) == hv, "undo after a quantized setup must restore the original notes exactly"
assert setup(hum, style="none").get("quantized") is None                  # style none never quantizes
print(f"quantize: {sum(q['moved'] for q in su['quantized'].values())} humanized onsets snapped to 1/16, "
      "original back exactly by undo")

# 6f. edits made in FL after setup are not overwritten silently; re-setup takes them (and reuses its own source)
setup(hum, style="natural"); P.flp_perform(hum)
try:
    P.flp_perform(hum); raise SystemExit("nothing changed: must pass")
except ValueError:
    raise SystemExit("an unchanged project must not trip the edit guard")
except SystemExit:
    pass
su2 = setup(hum, style="expressive")
assert su2.get("source_note") and spec_of(hum)["source"].endswith("perform_source.mid"), su2  # no stacked humanize
m.flp_replace_part(hum, hum, 2, notes=[[1, 0.25, 72, 100]])               # the user rewrites the melody in FL
try:
    P.flp_perform(hum, changes={"A": {"humanize": "+10%"}}); raise SystemExit("edit guard")
except ValueError as e:
    assert "changed in the project" in str(e), e
su3 = setup(hum)
assert su3["style"] == "expressive" and not su3.get("source_note") and su3["parts"][
    next(k for k, v in su3["parts"].items() if v["channel"] == 2)]["notes"] == 1

# ---- 7. audio clips: tempo maps are refused unless allowed; notes are still written
import wave  # noqa: E402
wav = os.path.join(tmp, "loop.wav")
with wave.open(wav, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\x00\x00" * 8000)
aud = make_song("aud", band(8), sections=[{"name": "A", "bar": 1}, {"name": "B", "bar": 5}])
m.flp_add_audio_clip(aud, aud, wav, bar=1)
su = setup(aud, style="expressive")
assert any("audio clips" in w for w in su["warnings"]), su
calls.clear(); ns["flp_edit"] = lambda project, ops, output=None: (calls.append(ops) or {"ops": len(ops), "results": []})
res = P.flp_perform(aud)
assert calls and "flp_tempo_write" not in [o["tool"] for o in calls[0]] and not res["write"]["tempo"]
assert any("audio clips" in w for w in res["warnings"]), res
calls.clear(); res = P.flp_perform(aud, allow_tempo_with_audio=True)
assert [o["tool"] for o in calls[0]] == ["flp_tempo_write"] and res["write"]["tempo"], calls
ns["flp_edit"] = real_edit
# a raw song without a Tempo lane: shaped tempo is skipped with a clear warning, notes still go in
nl = os.path.join(tmp, "nolane.flp"); shutil.copy(FIX, nl)
setup(nl, style="expressive", allow_tempo_with_audio=True)
dr = P.flp_perform(nl, dry_run=True)
assert dr["write"]["parts"] and not dr["write"]["tempo"] and any("tempo lane" in w for w in dr["warnings"]), dr
print("audio guard: expressive tempo map refused with audio clips, written with allow_tempo_with_audio")

# ---- 8. keys (one keyboard part playing tune + harmony + bass) split by skyline; name-based drums; parts filter
piano = []
for b in range(8):
    t0b = b * 4 * PPQ
    piano += [(t0b, t0b + 4 * PPQ - 10, 40 + b % 3, 70), (t0b, t0b + 2 * PPQ, 52, 60), (t0b, t0b + 2 * PPQ, 55, 60)]
    piano += [(t0b + k * 48, t0b + k * 48 + 44, 72 + (k * 2 + b) % 7, 90) for k in range(8)]
kick = [(b * PPQ, b * PPQ + 10, 60, 100) for b in range(32)]               # a one-pitch sample, not GM notes
pk = make_song("pk", {2: sorted(piano), 4: kick}, names={2: "Grand Piano", 4: "Kick"})
su = setup(pk, style="expressive")
assert su["parts"]["Grand Piano"]["role"] == "keys" and su["parts"]["Kick"]["role"] == "drums", su["parts"]
sp = spec_of(pk); assert sp["parts"]["Grand Piano"]["split"] == "skyline"
rd = render_of(pk); bc = by_channel(pk, rd)
assert [n[0] for n in bc[4]] == [n[0] for n in kick]
top = [n for n in bc[2] if n[2] >= 72]; low = [n for n in bc[2] if n[2] in (52, 55)]
assert sum(n[3] for n in top) / len(top) - 90 > sum(n[3] for n in low) / len(low) - 60, "melody voiced over harmony"
su = setup(pk, style="natural", parts=["Kick"])
assert list(su["parts"]) == ["Kick"]
su = setup(pk, style="natural", parts={"2": "melody"})
assert su["parts"]["Grand Piano"]["role"] == "melody"
su = setup(pk, style="natural", sections=[{"name": "A", "bar": 1}, {"name": "B", "bar": 5, "style": "tight"}])
sp = spec_of(pk); assert sp["sections"][1]["humanize"] == P.STYLES["tight"]["defaults"]["humanize"], sp["sections"]
setup(pk, style="natural", sections=4)
assert [s_["name"] for s_ in spec_of(pk)["sections"]] == ["Bars 1-4", "Bars 5-8"], spec_of(pk)["sections"]
print("roles: keyboard split by skyline, named drums, parts filter and role override; per-section styles")

# ---- 9. a layered song (one pattern per instrument, stacked; sections repeated) with 808 slides (review C1/C2)
lay = os.path.join(tmp, "layers.flp"); shutil.copy(FIX, lay)
m.flp_playlist(lay, "remove", output=lay, clips=[c["id"] for c in m.flp_playlist(lay)["clips"]])
layer_pid = {}
for i, (nm, ch, keys) in enumerate((("A Drums", 0, [36, 38]), ("A 808", 2, [40, 43]), ("A Keys", 1, [60, 64]))):
    r_ = m.flp_new_pattern(lay, nm, output=lay, place_at_bar=1, length_bars=4, playlist_track=1 + i)
    layer_pid[nm] = r_["pattern"]
    m.flp_playlist(lay, "place", output=lay, pattern=r_["pattern"], bar=5, track=1 + i, length_bars=4)
    m.flp_add_notes(lay, [{"note": keys[j % 2], "bar": 1 + j // 4, "beat": j % 4, "beats": 0.9,
                           "velocity": 90 + j % 7, "slide": ch == 2 and j % 5 == 3} for j in range(16)],
                    pattern=r_["pattern"], channel=ch, output=lay)


def song_x(path):
    d_ = m._load(path); e_ = m._events(d_)
    return sorted(n[:6] for n in m._song_x(d_, e_)[0]), m._patterns(d_, e_)


s0, pats0 = song_x(lay)
assert sum(n[5] for n in s0) == 6, "3 slide notes x 2 placements"
raw0 = open(lay, "rb").read()
setup(lay, style="none")
res = P.flp_perform(lay, parts="all")
assert open(lay, "rb").read() == raw0 and "nothing" in str(res["edit"]), "identity on stacked layers: a byte-exact no-op"
assert res["write"]["parts"] == [] and not res["sections_changed"] and "table" not in res, res
setup(lay, style="natural")
res = P.flp_perform(lay, parts="all")
s2, pats2 = song_x(lay)
for nm, pid in layer_pid.items():                                     # no part moved into another layer's pattern
    assert sorted((r[2], r[4], bool(r[1] & m.NOTE_SLIDE)) for r in pats2[pid]["notes"]) == \
        sorted((r[2], r[4], bool(r[1] & m.NOTE_SLIDE)) for r in pats0[pid]["notes"]), nm
assert sum(1 for r in pats2[layer_pid["A 808"]]["notes"] if r[1] & m.NOTE_SLIDE) == 3, "slides kept"
assert len(s2) == len(s0) and sum(n[5] for n in s2) == 6 and res["verify"]["ok"], res
print("layered song: identity byte-exact, natural keeps every part in its own layer and every 808 slide")


# ---- 10. overlapping patterns of one channel (a section 808 and a fill / verse 808 at the same bars on one track),
# repeats, the same pattern stacked twice, clip offsets (start in pattern), a short clip, a muted clip, slides
# (review F3 / music C1): every note stays in its own pattern; identity is a byte-exact no-op; natural keeps every
# note and slide; undo restores exactly; unique_repeats copies the repeats that play differently
def counts(path):
    s_, pats_ = song_x(path)
    by = {}
    for n in s_:
        by[n[2]] = by.get(n[2], 0) + 1
    return by, sum(n[5] for n in s_), {pid: sorted((r[2], r[4], bool(r[1] & m.NOTE_SLIDE)) for r in v["notes"])
                                       for pid, v in pats_.items()}


ov = os.path.join(tmp, "overlap.flp"); shutil.copy(FIX, ov)
m.flp_playlist(ov, "remove", output=ov, clips=[c["id"] for c in m.flp_playlist(ov)["clips"]])
pid_of = {}
for nm, ch, keys, slide in (("Hook 808", 2, [40, 43], True), ("Verse 808", 2, [40, 45], True), ("Drums", 0, [36, 38], False),
                            ("Keys", 1, [60, 64], False), ("Fill 808", 2, [40, 47], True)):
    r_ = m.flp_new_pattern(ov, nm, output=ov, **({"place_at_bar": 1, "length_bars": 4, "playlist_track": 2}
                                                if nm == "Hook 808" else {}))
    pid_of[nm] = r_["pattern"]
    m.flp_add_notes(ov, [{"note": keys[j % 2], "bar": 1 + j // 4, "beat": j % 4, "beats": 0.9 + (j % 3) * 0.5,
                          "velocity": 80 + (j * 7) % 40 + (48 if j == 5 else 0), "slide": slide and j % 5 == 3}
                         for j in range(16 if nm != "Fill 808" else 4)], pattern=r_["pattern"], channel=ch, output=ov)
place = [("Hook 808", 5, 2, 4), ("Verse 808", 5, 2, 4), ("Hook 808", 9, 2, 4),
         ("Verse 808", 9, 2, 4), ("Verse 808", 13, 2, 4), ("Hook 808", 17, 2, 4), ("Hook 808", 21, 2, 2),
         ("Fill 808", 8, 5, 1), ("Drums", 1, 1, 4), ("Drums", 5, 1, 4), ("Drums", 9, 1, 4), ("Keys", 1, 3, 4),
         ("Keys", 1, 4, 4), ("Keys", 5, 3, 4)]
for nm, bar_, tr, ln in place:
    m.flp_playlist(ov, "place", output=ov, pattern=pid_of[nm], bar=bar_, track=tr, length_bars=ln)
cl = m.flp_playlist(ov)["clips"]
v13 = next(c["id"] for c in cl if c["name"] == "Verse 808" and c["bar"] == 13)
m.flp_playlist(ov, "resize", output=ov, clips=[v13], trim_start_bars=1)        # starts 1 bar into its pattern
h17 = next(c["id"] for c in m.flp_playlist(ov)["clips"] if c["name"] == "Hook 808" and c["bar"] == 17)
m.flp_playlist(ov, "mute", output=ov, clips=[h17])
c0, sl0, p0 = counts(ov); v0, t0_ = view(ov); raw0 = open(ov, "rb").read(); sx0 = song_x(ov)[0]
assert sl0 > 0 and c0[2] > 50, (c0, sl0)
su = setup(ov, style="none")
res = P.flp_perform(ov, parts="all")
assert open(ov, "rb").read() == raw0 and "nothing" in str(res["edit"]), ("identity must be a byte-exact no-op", res)
for st in ("natural", "expressive", "swing"):
    open(ov, "wb").write(raw0)
    flh_store.journal_update(ov, set={"performance": None, "performance_history": None, "performance_applied": None,
                                      "performance_map": None})
    setup(ov, style=st)
    res = P.flp_perform(ov, parts="all")
    c1, sl1, p1 = counts(ov)
    assert res["verify"]["ok"] and not res["verify"]["problems"], (st, res["verify"])
    assert c1 == c0 and sl1 == sl0, (st, "notes or slides lost / gained", c0, c1, sl0, sl1)
    assert p1 == p0, (st, "a note moved to another pattern, or changed key / slide")
    assert any("repeated clips" in w for w in res.get("warnings", [])), (st, res.get("warnings"))
    u = P.flp_perform_undo(ov)
    assert song_x(ov)[0] == sx0, (st, "undo must bring every note back exactly")
print("overlapping patterns of one channel: identity byte-exact; natural/expressive/swing keep every note, slide and "
      "pattern; undo exact")
# unique_repeats: each repeat that plays differently gets its own pattern copy; nothing lost; undo exact in sound
open(ov, "wb").write(raw0)
flh_store.journal_update(ov, set={"performance": None, "performance_history": None, "performance_applied": None,
                                  "performance_map": None})
setup(ov, style="natural")
dr = P.flp_perform(ov, unique_repeats=True, dry_run=True)
assert dr["write"].get("unique_copies") and open(ov, "rb").read() == raw0, dr
res = P.flp_perform(ov, unique_repeats=True)
c2, sl2, _ = counts(ov)
assert res["verify"]["ok"] and c2 == c0 and sl2 == sl0, res
assert not any("repeated clips" in w for w in res.get("warnings", [])), res.get("warnings")
names2 = [p_["name"] for p_ in m.flp_patterns(ov)]
assert any(n_.startswith("Hook 808 @") for n_ in names2) and any(n_.startswith("Keys @") for n_ in names2), names2
mute_clip = next(c for c in m.flp_playlist(ov)["clips"] if c.get("muted"))
assert mute_clip["name"] == "Hook 808", "a muted clip is never copied"
res = P.flp_perform(ov, changes={"*": {"humanize": "+30%"}}, unique_repeats=True)    # the copies are performed too
assert res["verify"]["ok"] and counts(ov)[:2] == (c0, sl0), res
P.flp_perform_undo(ov); P.flp_perform_undo(ov)
assert sorted(n[:6] for n in song_x(ov)[0]) == sorted(n[:6] for n in sx0), "undo after unique copies: the same sound"
print(f"unique_repeats: {len(names2) - len(p0)} pattern copies, every note kept, undo exact")

# ---- 11. the read-back is honest: a write that loses a note says so (never ok: true)
open(ov, "wb").write(raw0)
flh_store.journal_update(ov, set={"performance": None, "performance_history": None, "performance_applied": None,
                                  "performance_map": None})
setup(ov, style="natural")


def lossy_edit(project, ops, output=None):
    r_ = real_edit(project, ops, output)
    d_ = m._load(output or project); e_ = m._events(d_); pid = pid_of["Verse 808"]
    recs = [r for r in m._patterns(d_, e_)[pid]["notes"]]
    drop = next(i for i, r in enumerate(recs) if r[1] & m.NOTE_SLIDE)
    recs = recs[:drop] + recs[drop + 1:]
    ks = m._pattern_note_events(d_, e_)[pid]
    m._save(m._splice(d_, e_, m._notes_event_edits(e_, ks, b"".join(struct.pack(m.NOTE_RECORD, *r) for r in recs))),
            output or project)
    return r_


ns["flp_edit"] = lossy_edit
res = P.flp_perform(ov)
ns["flp_edit"] = real_edit
assert not res["verify"]["ok"] and any("missing" in p_ and "slides" in p_ for p_ in res["verify"]["problems"]), res["verify"]
assert any("missing" in w for w in res["warnings"]), res["warnings"]
print("read-back: a lost slide note is reported, ok: false")

# ---- 12. undo with nothing to undo (E2E-4): says so, no table of changes, nothing written
open(ov, "wb").write(raw0)
flh_store.journal_update(ov, set={"performance": None, "performance_history": None, "performance_applied": None,
                                  "performance_map": None})
setup(ov, style="natural"); setup(ov, style="tight")
u = P.flp_perform_undo(ov)
assert u["undone"] is None and "nothing to undo" in u["note"] and "table" not in u and not u.get("sections_changed"), u
assert open(ov, "rb").read() == raw0
# a perform that writes nothing reports no section changes either (a tempo change in a song without a Tempo lane)
P.flp_perform(ov)
res = P.flp_perform(ov, changes={"*": {"tempo": "-3%"}})
assert "nothing" in str(res["edit"]) and res["sections_changed"] == [] and "table" not in res, res
print("undo with nothing to undo: says so")

# ---- 13. older journals (no note map): a clear re-setup message, nothing written
raw1 = open(ov, "rb").read()
flh_store.journal_update(ov, set={"performance_map": None})
try:
    P.flp_perform(ov, changes={"*": {"dyn": "+5"}}); raise SystemExit("an auto spec without a note map must refuse")
except ValueError as e:
    assert "flp_perform_setup" in str(e), e
assert open(ov, "rb").read() == raw1

# ---- 14. every corpus project (tests/corpus): style none is a byte-exact no-op; natural keeps every heard note and
# slide on its channel and reads back ok; undo brings the notes back exactly. A refusal is a clear ValueError.
CORPUS = os.path.join(HERE, "corpus")
done_c = 0
for f in sorted(glob.glob(os.path.join(CORPUS, "*.flp"))):
    cp = os.path.join(tmp, "corpus_" + os.path.basename(f)); shutil.copy(f, cp); raw = open(cp, "rb").read()
    try:
        setup(cp, style="none")
    except ValueError:
        continue                                                     # no notes that play / corrupt positions
    res = P.flp_perform(cp, parts="all")
    assert open(cp, "rb").read() == raw and "nothing" in str(res["edit"]), (f, "identity must be a byte-exact no-op")
    sx_b = sorted(n[:6] for n in song_x(cp)[0])
    setup(cp, style="natural")
    res = P.flp_perform(cp, parts="all")
    sx_a = sorted(n[:6] for n in song_x(cp)[0])
    chb, cha = {}, {}
    for src_, dst in ((sx_b, chb), (sx_a, cha)):
        for n in src_:
            dst[n[2]] = dst.get(n[2], 0) + 1
    assert cha == chb and sum(n[5] for n in sx_a) == sum(n[5] for n in sx_b), (f, "notes or slides lost")
    assert "nothing" in str(res["edit"]) or res["verify"]["ok"], (f, res.get("verify"))
    P.flp_perform_undo(cp)
    assert sorted(n[:6] for n in song_x(cp)[0]) == sx_b, (f, "undo must restore the notes exactly")
    done_c += 1
assert done_c >= 25, done_c
print(f"corpus: {done_c} projects - identity byte-exact, natural keeps every note and slide, undo exact")
print("ok")
