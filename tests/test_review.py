"""fl_doctor (static .flp check) and fl_review (audio + live) with synthetic audio; arc_check unit cases."""
import glob, json, os, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_review as R, flac_lite  # noqa: E402
ns = vars(m)
flh_store.register(ns); R.register(ns)
assert "fl_doctor" in m.TOOLS and "fl_review" in m.TOOLS
kinds = lambda res: [(i["severity"], i["kind"]) for i in res["issues"]]

# ---------------------------------------------------------------- arc_check (pure)
good = [{"name": "Intro", "lufs": -30}, {"name": "Bridge", "lufs": -24}, {"name": "Build", "lufs": -21},
        {"name": "Peak", "lufs": -17}, {"name": "Ending", "lufs": -27}]
assert R.arc_check(good, "opener") == [], R.arc_check(good, "opener")
bad = [{"name": "Intro", "lufs": -30}, {"name": "Verse", "lufs": -16}, {"name": "Bridge", "lufs": -24},
       {"name": "Peak", "lufs": -19}, {"name": "Ending", "lufs": -28}]
a = R.arc_check(bad, "track")
assert a[0]["severity"] == "high" and a[0]["kind"] == "arc_peak_not_loudest" and "Verse" in a[0]["what"], a
flat = [{"name": n, "lufs": v} for n, v in [("A", -20), ("B", -19), ("C", -18), ("D", -21)]]
assert any(i["kind"] == "arc_flat" for i in R.arc_check(flat)), R.arc_check(flat)
wide = [{"name": n, "lufs": v} for n, v in [("A", -40), ("B", -19), ("C", -18)]]
assert any(i["kind"] == "arc_wide" for i in R.arc_check(wide))
nobuild = [{"name": "Intro", "lufs": -18}, {"name": "Verse", "lufs": -22}, {"name": "Peak", "lufs": -17},
           {"name": "Ending", "lufs": -28}]
assert any(i["kind"] == "arc_no_build" for i in R.arc_check(nobuild, "opener"))
assert not any(i["kind"] == "arc_no_build" for i in R.arc_check(nobuild, "track"))
quiet = [{"name": "Bridge", "lufs": -24}, {"name": "Interlude", "lufs": -19}, {"name": "Theme 2", "lufs": -24},
         {"name": "Climax", "lufs": -15}]
assert [i["kind"] for i in R.arc_check(quiet)][:1] == ["arc_quiet_section_loud"], R.arc_check(quiet)

# genre-aware arc: a pop-form song (verse / pre-chorus / chorus / bridge), the choruses loudest -> no issue
pop = [{"name": n, "lufs": v} for n, v in [("Intro", -16), ("Verse 1", -12), ("Pre-Chorus", -10.5), ("Chorus", -8),
                                          ("Verse 2", -12), ("Chorus 2", -7.5), ("Bridge", -11),
                                          ("Final Chorus", -7), ("Outro", -14)]]
for st in ("pop", None, R.arc_style({"genre": "Pop"})):
    assert R.arc_check(pop, "opener", st) == [], (st, R.arc_check(pop, "opener", st))
assert R.arc_style({"genre": "Synthwave pop"})["class"] == "dense"
assert R.arc_style({}, {"style": "piano is the star; layers faint and sparse"})["class"] == "dynamic"
# a verse louder than every chorus is still caught
pop_bad = [dict(s, lufs=-5.5) if s["name"] == "Verse 2" else s for s in pop]
pb = R.arc_check(pop_bad, "track", "pop")
assert pb and pb[0]["kind"] == "arc_peak_not_loudest" and "Verse 2" in pb[0]["what"], pb
# EDM: 5 LU between sections, the drops loudest -> no arc_flat (a dynamic genre would call it flat)
edm = [{"name": n, "lufs": v} for n, v in [("Intro", -12), ("Build", -9), ("Drop", -7), ("Breakdown", -11),
                                          ("Build 2", -8.5), ("Drop 2", -7)]]
assert R.arc_check(edm, "opener", R.arc_style({"genre": "EDM"})) == [], R.arc_check(edm, "opener", "dense")
assert R.arc_check(edm, "track") == []
assert any(i["kind"] == "arc_flat" for i in R.arc_check(edm, "track", "dynamic"))
assert any(i["kind"] == "arc_flat" for i in R.arc_check(edm, "track", R.arc_style({"arc": {"flat_lu": 6}})))
# names: song-form words (some non-English), build before peak, mixed names neutral; explicit flags first
kinds_of = {n: R.section_kind({"name": n}) for n in ("Pre-Chorus", "Build-up", "Drop", "Breakdown", "Outro Chorus",
                                                       "Estribillo", "Припев", "サビ", "Verse", "Bridge", "Einleitung",
                                                       "Dropout")}
assert kinds_of == {"Pre-Chorus": "build", "Build-up": "build", "Drop": "peak", "Breakdown": "quiet",
                    "Outro Chorus": None, "Estribillo": "peak", "Припев": "peak", "サビ": "peak", "Verse": None,
                    "Bridge": None, "Einleitung": "quiet", "Dropout": None}, kinds_of
flagged = [{"name": "Chorus", "lufs": -8}, {"name": "Bridge", "lufs": -6, "peak": True}, {"name": "Verse", "lufs": -10},
           {"name": "Intro", "lufs": -14}]
assert R.arc_check(flagged, "track", "pop") == [], R.arc_check(flagged, "track", "pop")
assert R.section_kind({"name": "Intro", "quiet": False}) is None
# correlation: below 0 always high; 0..0.05 only a low hint
row = lambda c: {"name": "A", "lufs": -12, "corr": c, "tp": -3, "clipped": 0, "bands": None}
sev = lambda c: [i["severity"] for i in R._issues([row(c)], {}, None, {}, False, None, {}, True)
                 if i["kind"] == "correlation"]
assert sev(-0.1) == ["high"] and sev(0.03) == ["low"] and sev(0.3) == [], (sev(-0.1), sev(0.03))

# ---------------------------------------------------------------- FLAC reader (flac_lite output, both stereo modes)
fs = 44100
rng = np.random.default_rng(3)
for sig in (np.stack([np.sin(np.arange(20000) / 9.0), np.sin(np.arange(20000) / 9.0) * 0.5], 1),
            rng.normal(0, 0.2, (15000, 2)).clip(-1, 1),
            np.concatenate([np.zeros((5000, 2)), rng.uniform(-1, 1, (9000, 2))])):   # silence + verbatim frames
    q = np.round(sig * 32767).astype(np.int64)
    fp = os.path.join(tmp, "t.flac"); flac_lite.encode(fp, q, fs, 16)
    x, r_, b_ = R.flac_load(fp)
    assert r_ == fs and b_ == 16 and x.shape == q.shape and np.array_equal(np.round(x * 32768).astype(np.int64), q)

# ---------------------------------------------------------------- fl_doctor on the fixtures
fixtures = (sorted(glob.glob(os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "*.flp")))
            or [os.path.join(HERE, "corpus", "base.flp")])
for fx in fixtures:
    t0 = time.time(); d = R.fl_doctor(fx); took = time.time() - t0
    assert isinstance(d["issues"], list) and "summary" in d and took < 2.0, (fx, took)
    for i in d["issues"]:
        assert set(i) >= {"severity", "kind", "where", "what", "fix"} and i["severity"] in ("high", "med", "low"), i
    sev = [R.SEV[i["severity"]] for i in d["issues"]]
    assert sev == sorted(sev), "issues ranked"
if fixtures:
    src = os.path.join(tmp, "song.flp"); shutil.copy(fixtures[0], src)
    d0 = R.fl_doctor(src)
    assert d0["summary"]["channels"] == 6 and "Splice INSTRUMENT" in d0["summary"]["plugins"], d0["summary"]
    assert d0["summary"]["automation"][0]["target"].startswith("Master slot 0 Fruity Convolver"), d0["summary"]
    assert not any(i["kind"] == "silent_instrument" for i in d0["issues"])
    # a Spitfire/Splice instance listening on MIDI channel 7 -> silent, high
    bad_p = os.path.join(tmp, "midi7.flp")
    m.flp_plugin_settings(src, bad_p, 1, settings={"p_midiChannel": 7})
    d1 = R.fl_doctor(bad_p)
    top = d1["issues"][0]
    assert top["kind"] == "silent_instrument" and top["severity"] == "high" and "channel 1" in top["where"], top
    assert "p_midiChannel" in top["fix"]
    # a trial plugin (from the profile) -> high
    flh_store.profile_set("Piano", {"trial_plugins": ["Splice INSTRUMENT"], "owned_plugins": ["Fruity *"]})
    d2 = R.fl_doctor(src)
    assert ("high", "trial_plugin") in kinds(d2), kinds(d2)
    flh_store.profile_set("Piano", {"trial_plugins": None, "owned_plugins": ["Fruity *", "Splice INSTRUMENT",
                                                                              "Emphasizer"]})
    assert ("high", "trial_plugin") not in kinds(R.fl_doctor(src))
    # flh_catalog's sound_ok wins over the built-in lists when the profile doesn't say
    flh_store.profile_set("Piano", {"owned_plugins": None})
    ns["sound_ok"] = lambda name: "trial" if "INSTRUMENT" in (name or "") else True
    assert ("high", "trial_plugin") in kinds(R.fl_doctor(src))
    ns["sound_ok"] = lambda name: True
    assert not [k_ for k_ in kinds(R.fl_doctor(src)) if k_[1] in ("trial_plugin", "not_owned", "unknown_plugin")]
    del ns["sound_ok"]
    flh_store.profile_set("Piano", {"owned_plugins": ["Fruity *", "Splice INSTRUMENT", "Emphasizer"]})
    print("doctor:", json.dumps(kinds(d1)))
    # byte-level edits of a copy: mute channel 2 (has notes), mute mixer track 4 (channel 1 plays into it),
    # turn the master's Emphasizer (slot 9) into a Stereo Enhancer, drop channel 4's notes (leftover channel)
    import struct
    dd = m._load(src); ev = m._events(dd); edits = []; cur = None; tracks = 0
    for k_, e in enumerate(ev):
        if e["id"] == m.EV_CHANNEL:
            cur = struct.unpack("<H", m._payload(dd, e))[0]
        elif e["id"] == 0 and cur == 2:
            edits.append((k_, k_ + 1, bytes([0, 0]))); cur = None
        elif e["id"] == m.EV_MIXER_TRACK:
            if tracks == 4:
                pl = bytearray(m._payload(dd, e)); pl[4] &= ~8; edits.append((k_, k_ + 1, m._data_event(236, bytes(pl))))
            tracks += 1
        elif e["id"] == m.EV_PLUGIN_NAME and m._text16(m._payload(dd, e)) == "Emphasizer":
            edits.append((k_, k_ + 1, m._data_event(m.EV_PLUGIN_NAME, "Fruity Stereo Enhancer".encode("utf-16-le") + b"\0\0")))
    bad2 = os.path.join(tmp, "bad2.flp"); m._save(m._splice(dd, ev, edits), bad2)
    m.flp_edit_notes(bad2, [{"op": "delete"}], channel=4)
    d3 = R.fl_doctor(bad2); k3 = kinds(d3)
    print("doctor2:", json.dumps(k3))
    assert ("high", "muted_track") in k3 and ("med", "muted_with_notes") in k3, k3
    assert ("med", "master_widener") in k3 and ("low", "unused_channel") in k3, k3
    assert k3.index(("high", "muted_track")) < k3.index(("low", "unused_channel"))
    # fix hints: unlisted tools via flh_call (callable in every client), listed ones directly
    fx_ = {i["kind"]: i["fix"] for i in d3["issues"]}
    assert (fx_["unused_channel"].startswith('flh_call("flp_remove_channels", {"project": "<project>", "channels": [')
            or fx_["unused_channel"].startswith("flp_remove_channels(")), fx_   # essential now: named directly
    assert 'flh_call("fl_mixer_set", {"track": ' in fx_["muted_track"], fx_
    m.ESSENTIAL_TOOLS.add("flp_remove_channels")                       # (the listing cache notices at once)
    fx_ = {i["kind"]: i["fix"] for i in R.fl_doctor(bad2)["issues"]}
    assert fx_["unused_channel"].startswith("flp_remove_channels(project=project, channels=["), fx_
    m.ESSENTIAL_TOOLS.discard("flp_remove_channels")

# ---------------------------------------------------------------- fl_review audio mode (synthetic song)
def tone(sec, db, inv=False, f=220.0):
    t = np.arange(int(sec * fs)) / fs
    sig = sum(np.sin(2 * np.pi * f * h * t) / h for h in (1, 2, 3, 5)) * 10 ** (db / 20)
    sig = sig + rng.normal(0, 10 ** ((db - 30) / 20), len(t))
    R_ = -sig if inv else sig * 0.9
    return np.stack([sig, R_], 1)

# 6 sections of 4 bars at 120 bpm (8 s each); section 2 "Verse" louder than "Peak"; "Storm" polarity-inverted
plan = [("Intro", -34, False), ("Verse", -14, False), ("Bridge", -26, False), ("Storm", -24, True),
        ("Peak", -18, False), ("Ending", -32, False)]
x = np.concatenate([tone(8, db, inv) for _, db, inv in plan])
import fl_audio_edit as AE  # noqa: E402
wav = os.path.join(tmp, "render.wav"); AE.save(wav, x, fs, 24)
journal_sections = [{"name": n, "bar": 1 + 4 * i, "end_bar": 5 + 4 * i} for i, (n, _, _) in enumerate(plan)]
proj = None
if fixtures:
    proj = os.path.join(tmp, "song.flp")
    flh_store.journal_update(proj, set={"sections": journal_sections, "role": "track",
                                        "bar_times": [[1, 0.0], [25, 48.0]]})
    res = R.fl_review(project=proj, audio=wav, offset_s=0)
else:
    res = R.fl_review(audio=wav, sections=[{"name": n, "start_s": 8 * i, "end_s": 8 * i + 8}
                                           for i, (n, _, _) in enumerate(plan)])
k = kinds(res)
print("review:", json.dumps(k))
assert ("high", "arc_peak_not_loudest") in k and "Verse" in res["issues"][0]["what"], res["issues"][:2]
cor = [i for i in res["issues"] if i["kind"] == "correlation"]
assert cor and cor[0]["severity"] == "high" and "Storm" in cor[0]["where"], cor
assert all(R.SEV[i["severity"]] <= R.SEV["high"] for i in res["issues"][:2])
assert [r["section"] for r in res["sections"]] == [n for n, _, _ in plan]
st = {r["section"]: r for r in res["sections"]}
assert st["Verse"]["lufs"] > st["Peak"]["lufs"] and st["Storm"]["corr"] < -0.9, st
if proj:
    jr = flh_store.journal_get(proj)
    assert jr["review"]["sections"][1]["section"] == "Verse" and jr["review"]["when"], jr["review"]

# well-shaped arc, as a FLAC "final": no arc issue
plan2 = [("Intro", -34), ("Bridge", -27), ("Build", -24), ("Peak", -19), ("Ending", -31), ("Coda", -36)]
x2 = np.concatenate([tone(8, db) for _, db in plan2])
q = np.round(np.clip(x2, -1, 1) * 32767).astype(np.int64)
fl = os.path.join(tmp, "Song (Piano) - FINAL.flac"); flac_lite.encode(fl, q, fs, 16)
secs2 = [{"name": n, "start_s": 8 * i, "end_s": 8 * i + 8} for i, (n, _) in enumerate(plan2)]
res2 = R.fl_review(audio=fl, sections=secs2)
k2 = kinds(res2)
print("good arc:", json.dumps(k2), res2["song"])
assert not [x_ for x_ in k2 if x_[1].startswith("arc_")], k2
assert not [x_ for x_ in k2 if x_[1] == "correlation"], k2
assert any(k == "loudness_target" for _, k in k2)   # final far from the target (low without a finish profile)

# an EDM render, genre from the journal: 5 LU between sections, drops loudest -> no arc issue
plan3 = [("Intro", -16), ("Build", -13), ("Drop", -11), ("Breakdown", -15), ("Build 2", -12.5), ("Drop 2", -11)]
x3 = np.concatenate([tone(8, db) for _, db in plan3])
wav3 = os.path.join(tmp, "edm.wav"); AE.save(wav3, x3, fs, 24)
if fixtures:
    edm_p = os.path.join(tmp, "edm.flp"); shutil.copy(fixtures[0], edm_p)
    flh_store.journal_update(edm_p, set={"genre": "EDM", "role": "opener", "bar_times": [[1, 0.0], [25, 48.0]],
                                         "sections": [{"name": n, "bar": 1 + 4 * i, "end_bar": 5 + 4 * i}
                                                      for i, (n, _) in enumerate(plan3)]})
    res3 = R.fl_review(project=edm_p, audio=wav3, offset_s=0)
else:
    res3 = R.fl_review(audio=wav3, sections=[{"name": n, "start_s": 8 * i, "end_s": 8 * i + 8}
                                             for i, (n, _) in enumerate(plan3)])
k3_ = kinds(res3)
print("edm:", json.dumps(k3_), res3["song"].get("range_lu"))
assert not [x_ for x_ in k3_ if x_[1].startswith("arc_")], k3_

# ---------------------------------------------------------------- live mode with a mocked fl_listen
if proj:
    calls = []
    def fake_listen(start_bar=None, end_bar=None, seconds=None, save=None, **k):
        calls.append((start_bar, seconds))
        sec = int((float(start_bar) - 1) // 4)                    # which synthetic section this bar is in
        y = x[int(sec * 8 * fs):int(sec * 8 * fs + float(seconds) * fs)]
        if save:
            flac_lite.encode(save, np.round(np.clip(y, -1, 1) * 8388607).astype(np.int64), fs, 24)
        return {"heard": True, "whole": {}, "saved": save}
    m.fl_listen = fake_listen; m.fl_running = lambda: True
    if not m.IS_WINDOWS:                                          # off Windows live mode is refused, said so
        try:
            R.fl_review(project=proj, mode="live"); raise AssertionError("live mode must be Windows-only")
        except RuntimeError as e:
            assert "Windows only" in str(e) and "audio=" in str(e), e
    _was_win = m.IS_WINDOWS; m.IS_WINDOWS = True                  # the live path itself, with FL mocked
    job = R.fl_review(project=proj, mode="live")
    assert job.get("job", "").startswith("review"), job
    m._job_wait(job["job"], 60)
    jr_ = m.JOBS[job["job"]]
    assert jr_["status"] == "done", jr_
    lv = jr_["result"]
    assert len(calls) == 6 and calls[0][0] == 2, calls                  # one bar after each section start, in order
    kl = kinds(lv)
    print("live:", json.dumps(kl))
    assert ("high", "arc_peak_not_loudest") in kl and any(i["kind"] == "correlation" for i in lv["issues"]), kl
    assert not os.path.exists(os.path.join(tmp, "_flh_review_listen.flac"))
    # melody-vs-layers balance (opt-in, mocked fl_mix_check): the choir louder than the piano -> high
    flh_store.journal_update(proj, set={"channels": {"0": {"role": "piano", "mixer": 3},
                                                     "5": {"role": "choir", "mixer": 7}}})
    seen = {}
    def fake_mix(**k):
        seen.update(k); return {"tracks": {"Piano": {"lufs": -20.0}, "Choir": {"lufs": -18.5}}}
    m.fl_mix_check = fake_mix
    m.fl_state = lambda: {"mixer": [{"index": 3, "name": "Piano"}, {"index": 7, "name": "Choir"}]}
    bl = R.fl_review(project=proj, mode="live", balance=True, background=False)
    assert seen["tracks"] == [3, 7] and seen["start_bar"] == 6, seen          # loudest section: Verse (bar 5)
    assert any(i["kind"] == "balance" and i["severity"] == "high" for i in bl["issues"]), kinds(bl)

m.IS_WINDOWS = _was_win if "_was_win" in dir() else m.IS_WINDOWS
# ---------------------------------------------------------------- abrupt entrances (audio) + notes checks (flh_score)
import flh_score as SC  # noqa: E402
SC.register(ns)
def two(sec, db1, db2=None, at=None, ramp_s=0.0):
    """tone at db1; from `at` s a second tone (330 Hz) at db2, faded in over ramp_s."""
    y = tone(sec, db1)
    if db2 is not None:
        z = tone(sec, db2, f=330.0); t = np.arange(len(z)) / fs
        g = np.clip((t - at) / ramp_s, 0, 1) if ramp_s else (t >= at).astype(float)
        y = y + z * g[:, None]
    return y
secs_ab = [{"name": "A", "start_s": 0, "end_s": 12}, {"name": "B", "start_s": 12, "end_s": 24}]
xa = np.concatenate([two(12, -30, -16, at=6.0), tone(12, -14)])          # a layer slams in at 6.0 s, mid-section
wa = os.path.join(tmp, "abrupt.wav"); AE.save(wa, xa, fs, 24)
ra = R.fl_review(audio=wa, sections=secs_ab)
ab = [i for i in ra["issues"] if i["kind"] == "abrupt_entrance"]
print("abrupt:", json.dumps(ab)[:300])
assert len(ab) == 1 and ab[0]["where"].startswith("0:06") and "flp_add_automation" in ab[0]["fix"], ab   # 12 s: section start
xs = np.concatenate([two(12, -30, -16, at=5.0, ramp_s=3.0), tone(12, -14)])  # the same layer faded in over 3 s
ws = os.path.join(tmp, "swell.wav"); AE.save(ws, xs, fs, 24)
assert not [i for i in R.fl_review(audio=ws, sections=secs_ab)["issues"] if i["kind"] == "abrupt_entrance"]
ev = R.abrupt_events(None, None, [1e-3] * 60 + [1e-1] * 60, [], None)      # power blocks: +20 dB at 6.0 s
assert len(ev) == 1 and abs(ev[0][0] - 6.0) < 0.15 and ev[0][2] == "loudness", ev
assert abs(R._bar_at(lambda b: (b - 1) * 2.0, 7.0, 99) - 4.5) < 1e-6

if fixtures:
    P_ = 96; BAR_ = 4 * P_; Nn = SC.FM.parse_note
    def prog_notes(extra=()):
        out = []
        for b_ in range(8):
            bass, pad, mel = [("C2", ["C4", "E4", "G4"], ["E5", "G5", "E5", "C5"]), ("A1", ["A3", "C4", "E4"], ["A4", "C5", "E5", "C5"]),
                              ("F1", ["F3", "A3", "C4"], ["F5", "A5", "F5", "C5"]), ("G1", ["G3", "B3", "D4"], ["G5", "D5", "B4", "D5"])][b_ % 4]
            t_ = b_ * BAR_
            out += [(t_, BAR_, 0, Nn(bass), 90)] + [(t_, BAR_, 1, Nn(k_), 80) for k_ in pad] + \
                [(t_ + i_ * P_, P_, 2, Nn(k_), 100) for i_, k_ in enumerate(mel)]
        return out + list(extra)
    def write_flp(name, notes_, names):
        parts = []
        for ch_, fl_ch in ((0, 0), (1, 1), (2, 2), (4, 4)):
            evs = []
            for pos, ln, c_, k_, v_ in notes_:
                if c_ == ch_:
                    evs += [(pos, bytes([0x90, k_, v_])), (pos + ln, bytes([0x80, k_, 0]))]
            if evs:
                mp = os.path.join(tmp, f"{name}{ch_}.mid"); m.midi_write(mp, P_, [("t", evs)])
                parts.append({"channel": fl_ch, "midi": mp})
        out = os.path.join(tmp, name + ".flp"); m.flp_write_notes(fixtures[0], out, parts, song_length=None, tempo=120)
        flh_store.journal_update(out, set={"channels": {str(k_): {"name": v_} for k_, v_ in names.items()}})
        return out
    # strings slam in at bar 5 at full velocity, then play softly: symbolic abrupt entrance (live mode: no audio)
    st_p = write_flp("strings", prog_notes([(4 * BAR_, 2 * P_, 4, Nn(k_), 122) for k_ in ("C4", "E4")] +
                                          [(4 * BAR_ + i_ * P_, P_, 4, Nn("E4"), 70) for i_ in range(2, 16)]),
                     {0: "Bass", 1: "Pad", 2: "Lead", 4: "Strings"})
    sy = R.abrupt_issues(None, None, None, [], None, st_p, None, 0.0, [])
    print("symbolic:", json.dumps(sy)[:300])
    assert sy and sy[0]["severity"] == "med" and "bar 5" in sy[0]["where"] and "Strings" in sy[0]["where"], sy
    assert "flp_edit_notes" in sy[0]["fix"] and "ramp" in sy[0]["fix"]
    # a render of it (120 bpm: bar 5 = 8.0 s) where a layer jumps in at 8.0 s: the audio issue names the strings
    xr = np.concatenate([two(8, -30), two(8, -30, -15, at=0.0)])
    wr = os.path.join(tmp, "strings.wav"); AE.save(wr, xr, fs, 24)
    rs = R.fl_review(project=st_p, audio=wr, sections=[{"name": "A", "bar": 1, "end_bar": 9}], offset_s=0)
    ab2 = [i for i in rs["issues"] if i["kind"] == "abrupt_entrance"]
    print("abrupt+symbolic:", json.dumps(ab2)[:300])
    assert len(ab2) == 1 and ab2[0]["severity"] == "med" and "Strings(ch4) enters there" in ab2[0]["what"], ab2
    assert "bar 5" in ab2[0]["where"] and "velocity" in ab2[0]["fix"], ab2
    # doctor: timpani below its range (med) and the clash summary (an F#4 rub over C)
    tp_p = write_flp("timp", prog_notes([(0, P_, 4, Nn("E1"), 100), (BAR_, P_, 4, Nn("D2"), 100),
                                         (2 * BAR_, BAR_, 2, Nn("F#4"), 100)]),
                     {0: "Bass", 1: "Pad", 2: "Lead", 4: "Timpani"})
    t0 = time.time(); dd = R.fl_doctor(tp_p); took = time.time() - t0
    kd = kinds(dd)
    print("doctor notes:", json.dumps(kd), dd["summary"].get("notes_check"), f"{took:.2f}s")
    assert ("med", "out_of_range") in kd and any(k_ == "clash" for _, k_ in kd) and took < 2.0, kd
    rr = next(i for i in dd["issues"] if i["kind"] == "out_of_range")
    assert "E1" in rr["what"] and "Timpani" in rr["where"] and "octaves" in rr["fix"], rr
# ---- H4: true peak = the shared oversampled detector (no periodic-FFT edge over-reads on correct finals)
import flh_finalize as _F, fl_audio_edit as _AE, struct as _st, tracemalloc  # noqa: E402
_F.register(ns)
_fs = 48000; _t = np.arange(_fs * 3) / _fs
_fw = np.minimum(1, np.minimum(_t, _t[-1] - _t) / 0.2) ** 2                   # faded ends (no click at the edges)
_s20k = np.repeat((10 ** (-3 / 20) * np.sin(2 * np.pi * 20000 * _t) * _fw)[:, None], 2, 1)
assert abs(R._tp(_s20k) - -3.0) < 0.1, R._tp(_s20k)                   # was -1.46 dBTP
_rng = np.random.default_rng(4); _pn = _rng.normal(0, 0.1, (_fs * 20, 2))
_fin = os.path.join(tmp, "final_tp.flac")
_info = _F.finish(_pn, _fs, _fin, -14.0, -1.0, bits=24)
_y, _r, _b = R.flac_load(_fin)
assert R.measure(_y, _r)["tp"] <= -1.0 + 0.05 and abs(R._tp(_y) - _info["true_peak_dbfs"]) < 0.02, (_info, R._tp(_y))
# ---- M1: a mono file meters like BS.1770 (one channel once), the same as the server/finalize meter
_mono = os.path.join(tmp, "mono.wav"); _AE.save(_mono, _pn[:, :1] * 0.5, _fs, 24)
_xm, _fsm, _ = R.load_audio(_mono)
_srv = m._lufs(_xm, _fsm)
_rv = R.fl_review(audio=_mono)
assert abs(_rv["song"]["lufs"] - _srv) < 0.1, (_rv["song"], _srv)
assert abs(R.integrated_lufs(R._st2(_xm), _fsm) - _srv) < 0.05
# ---- L1 / L6: NaN samples -> a clear error; 8-bit and 64-bit float WAVs read
def _wav_raw(path, tag, bits, ch, rate, raw):
    fmt = _st.pack("<HHIIHH", tag, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits)
    body = b"WAVEfmt " + _st.pack("<I", len(fmt)) + fmt + b"data" + _st.pack("<I", len(raw)) + raw
    open(path, "wb").write(b"RIFF" + _st.pack("<I", len(body)) + body)
_nanw = os.path.join(tmp, "nan.wav"); _v = np.full((_fs, 2), 0.1, "<f4"); _v[100, 0] = np.nan
_wav_raw(_nanw, 3, 32, 2, _fs, _v.tobytes())
try:
    R.fl_review(audio=_nanw); raise SystemExit("NaN audio measured")
except ValueError as e:
    assert "NaN" in str(e)
_w8 = os.path.join(tmp, "u8.wav"); _wav_raw(_w8, 1, 8, 1, 8000, bytes([128, 192, 64, 255]))
assert np.allclose(R.load_audio(_w8)[0][:, 0], [0, 0.5, -0.5, 127 / 128])
_w64 = os.path.join(tmp, "f64.wav"); _wav_raw(_w64, 3, 64, 2, 8000, np.array([[0.25, -0.5]] * 3, "<f8").tobytes())
assert np.allclose(R.load_audio(_w64)[0], [[0.25, -0.5]] * 3)
# ---- M2: bounded memory - the review of a minute of 48 kHz stereo never holds float64 copies of the song
_long = os.path.join(tmp, "long.wav"); _AE.save(_long, _rng.normal(0, 0.1, (_fs * 60, 2)), _fs, 24)
tracemalloc.start(); R.fl_review(audio=_long); _cur, _peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
_mb = _fs * 60 * 2 * 4 / 1e6                                            # the float32 samples themselves
print(f"review 60 s 48 kHz stereo: peak traced {_peak / 1e6:.0f} MB (samples {_mb:.0f} MB)")
assert _peak < 3.5 * _mb * 1e6, _peak
# ---- queued (lazy mode) edits: audio mode says the render doesn't have them (no FL sync); live mode syncs first
assert "fl_review" not in m.SYNC_FIRST_TOOLS                            # fl_review decides itself (per mode)
qp = os.path.join(tmp, "Queued Review.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), qp)
m._pending_set(qp, [{"tool": "flp_set_tempo", "bpm": 150}])
synced = []
orig_sync, orig_live, orig_run = ns.get("_sync_before"), R._review_live, ns["fl_running"]
ns["_sync_before"] = lambda reason, *a, **k: synced.append(reason)
rq = R.fl_review(project=qp, audio=_long)
assert rq["pending_ops"] == 1 and "aren't in this render" in rq["pending_note"] and not synced, rq
R._review_live = lambda *a, **k: {"mode": "live"}; ns["fl_running"] = lambda: True; _was_win = m.IS_WINDOWS
m.IS_WINDOWS = True
assert R.fl_review(project=qp, mode="live", background=False) == {"mode": "live"} and synced == ["fl_review"], synced
ns["_sync_before"], R._review_live, ns["fl_running"] = orig_sync, orig_live, orig_run
m.IS_WINDOWS = _was_win
m._pending_set(qp, [])
print("queued edits ok")
print("ok")
