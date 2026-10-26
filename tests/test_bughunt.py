"""Regression tests for bugs found in the bug hunt of the fast-grown modules (each test names the bug it pins).
Run: python3 tests/test_bughunt.py (no FL needed)."""
import json, os, shutil, sys, tempfile, threading
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_bughunt_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import flh_store  # noqa: E402
ns = vars(m)
m.fl_running = lambda: False
CORPUS = os.path.join(HERE, "corpus")


def copy(name, sub="p"):
    d = os.path.join(tmp, sub); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".flp"); shutil.copy(os.path.join(CORPUS, name + ".flp"), p)
    return p


# ---------------------------------------------------------------------------------------------------------------
# BH-1 flh_store.journal_update: read-modify-write without a lock and one shared '<journal>.tmp' name. Background
# jobs (fl_finalize's file phase, the new-song balance, flp_finish_piece) write the journal while foreground tools
# do: updates were lost (or os.replace failed on the other thread's tmp file).
def test_journal_concurrent_updates():
    proj = copy("base", "journal_race")
    errors = []

    def writer(tag):
        for i in range(60):
            try:
                flh_store.journal_update(proj, set={f"{tag}_{i}": i}, add={"notes": f"{tag}-{i}"})
            except Exception as e:                                   # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
    ts = [threading.Thread(target=writer, args=(t,)) for t in "abcd"]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors, errors[:3]
    j = flh_store.journal_get(proj)
    lost = [f"{t}_{i}" for t in "abcd" for i in range(60) if f"{t}_{i}" not in j]
    assert not lost, f"{len(lost)} journal updates lost, e.g. {lost[:4]}"
    assert len(j["notes"]) == 240, len(j["notes"])
    left = [f for f in os.listdir(os.path.dirname(proj)) if f.endswith(".tmp")]
    assert not left, left


# ---------------------------------------------------------------------------------------------------------------
# BH-2 flp_finish_piece: perform / final take ["boolean", "string"], so the dispatcher leaves "false" a string;
# final="false" was bool("false") == True (a render that closes FL nobody asked for) and perform="false" / "no" was
# read as a style name and refused.
def test_finish_string_booleans():
    import flh_finish
    flh_finish.register(ns)
    p = copy("base", "finish_bool")
    for off in ("false", "no", "off", "False", "0"):
        pl = flh_finish._plan(p, [], off, False, off, None)
        assert pl["final"] is False and pl["perform"] is False, (off, pl["final"], pl["perform"])
    assert flh_finish._plan(p, [], "none", False, "none", None)["style"] == "none"     # a style, not 'off'
    assert flh_finish._plan(p, [], "none", False, "none", None)["final"] is False
    for on in ("true", "yes", "auto"):
        pl = flh_finish._plan(p, [], on, False, on, None)
        assert pl["final"] is True and pl["perform"] is True and pl["style"] is None, (on, pl)
    assert flh_finish._plan(p, [], False, False, "force", None)["final"] == "force"
    # through the MCP dispatcher: nothing to do with every step off as strings (no render, no FL)
    r = m.handle({"method": "tools/call", "params": {"name": "flp_finish_piece", "arguments": {
        "project": p, "layers": [], "perform": "false", "ending": "false", "final": "false", "background": False}}})
    assert r.get("isError") and "nothing to do" in r["content"][0]["text"], r


# ---------------------------------------------------------------------------------------------------------------
# BH-3 fl_finalize: (a) its own temp folder (the full-length render WAVs) went to the Recycle Bin after every final -
# disk space the user never gets back without emptying it - and on macOS / Linux files_recycle refuses, so the folder
# stayed in the temp folder; (b) a failing finish 'recycled' its own half-made '<final>.new.flac': off Windows that
# raised and hid the real error; a failure after finish (WAV conversion, the swap) left '.new.flac' / '.new.wav'
# next to the user's finals.
def test_finalize_temp_files():
    import numpy as np
    import flh_finalize, fl_audio_edit as AE
    flh_finalize.register(ns)
    systemp = os.path.join(tmp, "systemp"); os.makedirs(systemp, exist_ok=True)
    old_td = tempfile.tempdir; tempfile.tempdir = systemp
    proj = os.path.join(tmp, "fin", "Song.flp"); os.makedirs(os.path.dirname(proj), exist_ok=True)
    open(proj, "wb").write(b"FLhd fake")
    saved = {k: ns.get(k) for k in ("_render", "files_recycle", "fl_running", "_render_cached", "_render_cache_peek")}
    recycled = []
    real_finish = flh_finalize.finish

    def render(project, out_dir, formats, timeout_s, data=None, out_stem=None):
        out = os.path.join(out_dir, (out_stem or "x") + ".wav")
        t = np.arange(int(6 * 44100)) / 44100.0
        y = 0.3 * np.sin(2 * np.pi * 220 * t) * np.exp(-t / 3); y[:44100] = 0
        AE.save(out, np.stack([y, y], 1), 44100, 24)
        return {"rendered": [out], "seconds": 0.1}
    try:
        ns["_render"] = render; ns["fl_running"] = lambda: False
        ns["_render_cached"] = None; ns["_render_cache_peek"] = None
        ns["files_recycle"] = lambda paths: (recycled.extend(paths), {"recycled": paths})[1]
        r = flh_finalize.fl_finalize(project=proj, background=False, method="cli")
        assert r["ok"], r
        assert not any(os.path.basename(p).startswith("flh_final_") for p in recycled), recycled
        assert not [d for d in os.listdir(systemp) if d.startswith("flh_final_")], os.listdir(systemp)
        # (b) finish fails after writing part of its output: the real error is reported, no .new.* left
        def boom(x, fs, output, *a, **k):
            open(output, "wb").write(b"half")
            raise ValueError("boom in finish")
        flh_finalize.finish = boom

        def no_recycle(paths):
            raise RuntimeError("files_recycle uses the Windows Recycle Bin")
        ns["files_recycle"] = no_recycle
        r = flh_finalize.fl_finalize(project=proj, background=False, method="cli")
        err = r["finals"][0].get("error") or ""
        assert "boom in finish" in err, r["finals"][0]
        left = [f for dp, _, fs in os.walk(tmp) for f in fs if ".new." in f]
        assert not left, left
    finally:
        flh_finalize.finish = real_finish
        ns.update(saved); tempfile.tempdir = old_td


# ---------------------------------------------------------------------------------------------------------------
# BH-4 flp_smooth_entries counted 4/4 bars (bar = 4 * PPQ) in every song: in 3/4 the entry it reported was at the
# wrong bar (bar 7 for FL's bar 9) and the 'two empty bars before' / energy were measured in 4-beat bars; the ramp
# started at that 4/4 bar line instead of the entry, so a part entering mid-bar began loud.
def test_smooth_entries_meter():
    import flh_shape
    flh_shape.register(ns)
    p = os.path.join(tmp, "smooth34", "s.flp"); os.makedirs(os.path.dirname(p), exist_ok=True)
    m.flp_write_notes(os.path.join(CORPUS, "base.flp"), p, [
        {"channel": 0, "notes": [[b, 0.25, 60, 30] for b in range(1, 13)]},
        {"channel": 1, "notes": [[7.5, 1, 64, 120], [7.5, 1, 67, 120], [8.5, 1, 72, 120]]}], tempo="keep")
    m.flp_project_info(p, output=p, time_sig_num=3, time_sig_beat=4)
    d = m._load(p); ev = m._events(d); P = m._ppq(d)
    first = min(n[0] for n in m._song_x(d, ev)[0] if n[2] == 1)
    want_bar = int(m._tick_to_bar(first, m._meter_map(d, ev), P))
    assert want_bar == 9, want_bar                                    # (the fixture: FL's ruler, 3/4)
    r = flh_shape.flp_smooth_entries(p, dry_run=True)
    hit = [e for e in r["entries"] if e["channel"] == 1]
    assert hit and hit[0]["bar"] == want_bar, r
    flh_shape.flp_smooth_entries(p, output=p)
    d2 = m._load(p); n2 = sorted(n for n in m._song_x(d2, m._events(d2))[0] if n[2] == 1)
    assert n2[0][4] == round(120 * 0.55), [n[4] for n in n2]           # the entry itself starts soft


# ---------------------------------------------------------------------------------------------------------------
# BH-5 fl_sound_shape(variations=N): each version is put back with _undo, which pops the channel's WHOLE undo entry -
# including the user's own earlier fl_sound_shape on that channel: asking for variations silently reverted the shaping
# the user had kept (and later versions were compared on another sound than the 'original' measured first).
def test_sound_shape_variations_keep_earlier_shaping():
    import flh_sounddesign as SD
    SD.register(ns)
    proj = copy("base", "shape_var")
    sets = []
    saved = {k: getattr(SD, k) for k in ("_project", "_measure", "_shape_words")}
    saved_ns = {k: ns.get(k) for k in ("fl_plugin_set",)}
    try:
        SD._project = lambda: proj
        SD._measure = lambda ch: {"lufs": -20.0}
        ns["fl_plugin_set"] = lambda index, param, value, slot=-1: sets.append((index, param, value)) or {}
        SD._undo_store(proj, 3, {"params": [{"index": 3, "slot": -1, "param": 5, "before": 0.3}]})   # kept earlier

        def fake_shape(channel, words, amount, check=True, dry_run=False):
            SD._undo_store(proj, channel, {"params": [{"index": channel, "slot": -1, "param": 9, "before": 0.5}]})
            return {"words": ["brighter"]}
        SD._shape_words = fake_shape
        r = SD._variations(3, "brighter", 3)
        assert len(r["versions"]) == 3, r
        assert not [x for x in sets if x[1] == 5], f"the earlier shaping was undone: {sets}"
        assert [x for x in sets if x[1] == 9], sets                     # each version itself is put back
        left = SD._undo_store(proj, 3)
        assert left and [p_ for p_ in left["params"] if p_["param"] == 5], left    # still undoable later
        assert not [p_ for p_ in left["params"] if p_["param"] == 9], left
    finally:
        for k, v in saved.items():
            setattr(SD, k, v)
        ns.update(saved_ns)


# ---------------------------------------------------------------------------------------------------------------
# BH-6 flp_finish_piece said ok=true when flp_perform's own read-back check failed (verify.ok false) or the layers
# didn't read back as planned (verified false): the step row stayed ok and the whole run claimed success.
def test_finish_unverified_steps_not_ok():
    import flh_finish
    flh_finish.register(ns)
    p = copy("base", "finish_verify")
    T = ns["TOOLS"]
    saved = {k: T[k] for k in ("flp_compose", "flp_perform_setup", "flp_perform", "flp_extend_ending", "fl_finalize")}
    saved_ns = {k: ns.get(k) for k in ("_with_open_project", "fl_ending_check")}
    case = {}
    try:
        def put(name, fn):
            T[name] = (fn,) + tuple(saved[name][1:])
        put("flp_compose", lambda **k: {"layers": {"strings": {"channel": 7, "notes": 3}},
                                        "verified": case.get("layers_ok", True)})
        put("flp_perform_setup", lambda **k: {"style": "natural", "parts": {"melody": {"channel": 0}}})
        put("flp_perform", lambda **k: {"write": {"parts": ["melody"]},
                                        "verify": {"ok": case.get("perform_ok", True), "problems": ["x"]}})
        put("flp_extend_ending", lambda **k: {"mode": "hold_s", "held_notes": 2})
        put("fl_finalize", lambda **k: {"ok": True, "finals": [{"song": "s", "path": p[:-4] + ".flac", "lufs": -14,
                                                                 "true_peak": -1, "length": "0:10"}]})
        ns["_with_open_project"] = lambda project, output, fn, _pending_ok=True: fn()
        ns["fl_ending_check"] = lambda *a, **k: {"predicted": "pass"}
        base = dict(layers=["strings"], perform="natural", ending=False, final=False, background=False)
        r = flh_finish.flp_finish_piece(p, **base)
        assert r["ok"], r
        case.update(perform_ok=False)
        r = flh_finish.flp_finish_piece(p, **base)
        pr = next(x for x in r["steps"] if x["step"] == "perform")
        assert pr["ok"] is False and r["ok"] is False, r
        case.update(perform_ok=True, layers_ok=False)
        r = flh_finish.flp_finish_piece(p, **base)
        ly = next(x for x in r["steps"] if x["step"] == "layers")
        assert ly["ok"] is False and r["ok"] is False, r
    finally:
        T.update(saved); ns.update(saved_ns)


# ---------------------------------------------------------------------------------------------------------------
# BH-7 flp_realism(undo=true) only looked at the channels in its own journal entry, so the expression clips
# realism_expression_ops wrote for flp_compose layers / flp_perform ("named like flp_realism's, so a later run or undo
# removes them") stayed in the song after the undo.
def test_realism_undo_removes_layer_expression():
    fix = os.path.join(HERE, "data", "audio_clip_fl2026.flp")
    if not os.path.isfile(fix):
        print("  (skipped: no tests/data fixture)"); return
    import flh_compose, flh_realism, flh_score
    for mod in (flh_score, flh_compose, flh_realism):
        mod.register(ns)
    saved = ns["_with_open_project"]
    ns["_with_open_project"] = lambda project, output, fn, *a, **k: fn()
    try:
        base = os.path.join(tmp, "rz", "base.flp"); os.makedirs(os.path.dirname(base), exist_ok=True)
        shutil.copy(fix, base)
        m.flp_edit_notes(base, [{"op": "delete"}], pattern=1)
        m.flp_playlist(base, "remove", clips=[c["id"] for c in m.flp_playlist(base)["clips"]])
        names = ["Kick", "Snare", "Hat", "808", "Keys", "Lead"]
        ops = [{"tool": "flp_add_channel", "clone_from": 0} for _ in names]
        ops.append({"tool": "flp_style_channels", "channels": [{"channel": 6 + i, "name": n} for i, n in enumerate(names)]})
        m.flp_edit(base, ops)
        compose = ns["TOOLS"]["flp_compose"][0]
        compose(project=base, what="beat", genre="trap", key="C minor", seed=7,
                channel={"chords": 10, "bass": 9, "melody": 11})
        rj = compose(project=base, what="layer", role="choir", section="Beat", seed=3)
        assert rj.get("new_channels") and rj["realism"]["choir"]["applied"], rj.get("realism")

        def autos():
            d = m._load(base)
            return sorted(nm for kind, nm in m._clip_names(d, m._events(d)).values()
                          if kind == "automation" and nm.startswith("Choir "))
        assert autos(), "the fixture's layer wrote no expression clips"
        ns["TOOLS"]["flp_realism"][0](project=base, undo=True)
        assert not autos(), autos()
    finally:
        ns["_with_open_project"] = saved


# ---------------------------------------------------------------------------------------------------------------
# BH-8 the new-song level balance: when a quiet part needed more than its fader has, the drums were pulled down -
# a failed mixer_set there was swallowed, yet every other part's move was lowered by that amount and the result said
# 'drums -X dB': the song ended up with the parts too quiet against drums that never moved.
def test_balance_drums_move_verified():
    import flh_newsong as N
    N.register(ns)
    saved = {k: getattr(N, k) for k in ("_balance_units", "_measure_units")}
    saved_ns = {k: ns.get(k) for k in ("fl_state", "BRIDGE", "fl_save")}
    vol = {1: 0.0, 2: 0.0}

    class B:
        def call(self, cmd, track=None, volume_db=None, **k):
            if volume_db is not None:
                if track == 1:
                    raise RuntimeError("FL didn't answer")
                vol[track] = volume_db
            return {"volume_db": vol.get(track, 0.0)}
    try:
        N._balance_units = lambda project: [("drums", "drums", [0], 1), ("pad", "pad", [3], 2)]
        N._measure_units = lambda project, units, notes, steps: ({"drums": -10.0, "pad": -40.0}, "test")
        ns["fl_state"] = lambda: {"channels": [{"index": 0, "insert": 1}, {"index": 3, "insert": 2}], "mixer": []}
        ns["BRIDGE"] = B(); ns["fl_save"] = lambda: None
        r = N.balance_song(os.path.join(tmp, "x.flp"))
        assert not any(x.startswith("drums") for x in r["moved"]), r
        assert any("drums" in n for n in (r["notes"] or [])), r
        want = -10.0 + N.BALANCE_TARGETS.get("pad", -5.0) - -40.0            # the pad's own full move, capped
        assert abs(vol[2] - min(N.FADER_MAX_DB, max(-40.0, min(18.0, want)))) < 1e-6, (vol, want)
    finally:
        for k, v in saved.items():
            setattr(N, k, v)
        ns.update(saved_ns)


# ---------------------------------------------------------------------------------------------------------------
# BH-9 flh_new_song(open=true) started the balance with a 90 s wait for FL to show the song and nothing recorded: on a
# slow first FL start it gave up silently (no news, no pending mark) after telling the user it was being balanced.
def test_new_song_balance_survives_slow_open():
    import flh_newsong as N, flh_store as FS
    N.register(ns); FS.register(ns)
    proj = copy("base", "bal_slow")
    news = ns.setdefault("_AUTOSYNC_NEWS", [])
    saved = {k: ns.get(k) for k in ("_wait_open_in_fl",)}
    import threading as _th
    done = _th.Event()

    def wait(project, s):
        done.set(); return False                                      # FL never showed it in time
    try:
        ns["_wait_open_in_fl"] = wait
        n0 = len(news)
        N._start_new_song_balance(proj, "trap")
        assert done.wait(5)
        import time as _t
        for _ in range(50):
            if len(news) > n0:
                break
            _t.sleep(0.05)
        assert len(news) > n0 and "pending level balance" in news[-1], news[n0:]
        assert isinstance(FS.journal_get(proj).get("balance_pending"), dict), "not recorded for the next fl_open"
    finally:
        ns.update(saved)


# ---------------------------------------------------------------------------------------------------------------
# BH-10 fl_audio_edit.save (every WAV fl-headless writes): a failure mid-write (full disk - exactly when space
# matters - or a bad chunk) left '<file>.tmp' (up to the whole WAV) behind; NaN samples became full-scale clicks
# (NaN cast to int32 is INT_MIN).
def test_wav_save_no_tmp_left_and_nan_safe():
    import numpy as np
    import fl_audio_edit as AE
    d = os.path.join(tmp, "wavsave"); os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "x.wav")

    class Bad:                                                        # the second chunk fails
        shape = (1000, 2); ndim = 2
        def __getitem__(self, sl):
            if sl.start:
                raise OSError(28, "No space left on device")
            return np.zeros((sl.stop - sl.start, 2))
    import fl_audio_edit
    real = np.asarray
    try:
        fl_audio_edit.np.asarray = lambda a, dtype=None: a if isinstance(a, Bad) else real(a, dtype=dtype)
        try:
            AE.save(out, Bad(), 44100, 24, chunk=100)
            raise SystemExit("must raise")
        except OSError:
            pass
    finally:
        fl_audio_edit.np.asarray = real
    assert not os.listdir(d), os.listdir(d)
    y = np.zeros((100, 1)); y[10] = np.nan; y[20] = np.inf
    AE.save(out, y, 44100, 16)
    z, fs, _b = AE.load(out) if hasattr(AE, "load") else (None, None, None)
    if z is not None:
        # NaN and inf are written as silence (never a full-scale click), the same as test_bughunt_audio
        assert abs(float(np.asarray(z)[10, 0])) < 1e-6 and abs(float(np.asarray(z)[20, 0])) < 1e-6, np.asarray(z)[8:22, 0]


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
            print("passed", k)
    shutil.rmtree(tmp, ignore_errors=True)
    print("ok")
