"""Render cache, stems-once offline remix, one-pass level balance, partial range renders, the pending balance of
songs made with open=false, and the session render counter. FL is simulated: a linear 'mixer' turns per-track tones
into the mix, at the faders stored in the .flp (FL saving = live fader moves written to the file).
Run: python3 tests/test_render_cache.py"""
import os
import shutil
import struct
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_rc_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local"); os.environ["FLH_NO_WARM"] = "1"
os.environ.pop("FLH_RENDER_CACHE_GB", None)
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
for mod in ("flh_store", "flh_catalog", "flh_compose", "flh_mixeng", "flh_finalize", "flh_newsong"):
    __import__(mod).register(ns)
import flh_newsong as NS, flh_store  # noqa: E402
import fl_render_cache as RC  # noqa: E402
import fl_audio_edit as AE  # noqa: E402
RC.bind(ns)

try:
    FS = 44100; LEN = {"s": 3.0}
    song = os.path.join(tmp, "Beat.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song)
    out = os.path.join(tmp, "renders"); os.makedirs(out)
    FREQ = {3: 110.0, 4: 220.0, 5: 55.0, 6: 440.0}                 # mixer track -> its tone (channels 0, 1, 2, 4)
    CH_TRACK = {0: 3, 1: 4, 2: 5, 4: 6}
    fp0 = RC.fingerprint(m._read_flp_bytes(song))
    assert {c: fp0["chan_insert"][c] for c in CH_TRACK} == CH_TRACK, fp0["chan_insert"]
    LIVE_MUTED = set()

    def stem(t, levels):
        n = int(FS * LEN["s"]); ti = np.arange(n) / FS
        y = 0.2 * np.sin(2 * np.pi * FREQ[t] * ti) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * ti))
        g = RC.fader_lin((levels.get(t) or [None])[0])
        return np.stack([y, 0.8 * y], 1) * g

    def model(levels, tracks=None):
        acc = sum(stem(t, levels) for t in (tracks or FREQ))
        return acc * RC.fader_lin((levels.get(0) or [None])[0])

    def file_edit(fn, *a, **k):                                       # an edit FL then has (its reload is simulated)
        run = m.fl_running; m.fl_running = lambda: False
        try:
            return fn(*a, **k)
        finally:
            m.fl_running = run

    def levels_now():
        return RC.fingerprint(m._read_flp_bytes(song))["levels"]

    # ============================================================ A. fl_render (FL closed): hit / miss / invalidation
    m.fl_running = lambda: False
    REAL_SHORT = m._render_short                                      # the fake renders here are seconds long, the
    m._render_short = lambda project, path: None                      # corpus song minutes: length checks in I.
    runs = []

    def fake_run_fl(target, rend, formats, expected, timeout_s):
        runs.append(target)
        f = os.path.join(rend, "x.wav"); AE.save(f, model(levels_now()), FS, 24)
        return {"x.wav": os.path.getsize(f)}, True, 1.0, ["FL64"]
    m._run_fl = fake_run_fl
    e0 = m._RENDER_STATS["exports"]
    r1 = m.fl_render(song, output_dir=out)
    assert r1["renders"] == 1 and not r1.get("reused") and len(runs) == 1, r1
    r2 = m.fl_render(song, output_dir=out)
    assert r2["renders"] == 0 and "reused render" in r2["reused"] and len(runs) == 1, r2
    assert r2["rendered"] == r1["rendered"] and os.path.isfile(r2["rendered"][0]), (r1, r2)
    o2 = os.path.join(tmp, "elsewhere")
    os.makedirs(o2); r2b = m.fl_render(song, output_dir=o2)            # another folder: a copy, still no export
    assert r2b["renders"] == 0 and os.path.isfile(r2b["rendered"][0]) and len(runs) == 1, r2b
    # a volatile event (FL's work-time stamp) changes on every save: still the same song
    d = m._load(song); ev = m._events(d)
    k237 = next((k for k, e in enumerate(ev) if e["id"] == 237), None)
    if k237 is not None:
        p = bytearray(m._payload(d, ev[k237])); p[-1] ^= 0x5A
        m._save(m._splice(d, ev, [(k237, k237 + 1, m._data_event(237, bytes(p)))]), song)
        assert m.fl_render(song, output_dir=out)["renders"] == 0 and len(runs) == 1
    m.flp_add_notes(song, [{"note": 60, "bar": 1, "beat": 0, "beats": 1}], channel=0)   # a note edit: a new render
    r3 = m.fl_render(song, output_dir=out)
    assert r3["renders"] == 1 and len(runs) == 2, r3
    m.flp_mixer(song, song, [{"track": 4, "volume_db": -6}])          # a fader saved in the file: a new mix
    assert m.fl_render(song, output_dir=out)["renders"] == 1 and len(runs) == 3
    assert m.fl_render(song, output_dir=out)["renders"] == 0 and len(runs) == 3
    os.remove(r3["rendered"][0])                                      # the user deleted the render: rendered again
    assert m.fl_render(song, output_dir=out)["renders"] == 1 and len(runs) == 4
    assert m._RENDER_STATS["exports"] - e0 == 4 and m._RENDER_STATS["reused"] >= 3, m._RENDER_STATS
    # fl_finalize's route: its WAV is consumed, so the cache keeps a copy; FL needn't close for an unchanged song
    sub1, sub2 = os.path.join(tmp, "fin1"), os.path.join(tmp, "fin2"); os.makedirs(sub1); os.makedirs(sub2)
    m.flp_add_notes(song, [{"note": 72, "bar": 2, "beat": 0, "beats": 1}], channel=1)
    assert not m._render_cache_peek(song, "wav", "cli")
    f1 = m._render_cached(song, sub1, "wav", 60, out_stem="render", method="cli", close_fl=True, store="exact")
    assert f1["renders"] == 1 and f1["rendered"][0].endswith("render.wav") and m._render_cache_peek(song, "wav", "cli")
    os.remove(f1["rendered"][0])                                      # fl_finalize moved / dropped it
    f2 = m._render_cached(song, sub2, "wav", 60, out_stem="render", method="cli", close_fl=True, store="exact")
    assert f2["renders"] == 0 and f2.get("reused") and os.path.isfile(f2["rendered"][0]) and len(runs) == 5, f2
    assert m._wav_load(f2["rendered"][0])[2] == 24                    # FL's own file, not a 16-bit measuring copy
    print("A fl_render cache ok")

    # ============================================================ B. FL open: previews, live fader moves, stems once
    FL = {"flag": 0, "split": False, "live": {}, "saves": 0}
    m.fl_running = lambda: True
    m.IS_WINDOWS = True
    m._open_project_guess = lambda: (song, [])
    m._open_in_fl = lambda p: True
    m._stale_block = lambda p=None: None
    m._fl_changed_flag = lambda: FL["flag"]
    m._sync_before = lambda *a, **k: None

    def fake_save(project, wait_s=8.0):                               # FL writes its live state into the file
        if FL["live"]:
            m.flp_mixer(song, song, [{"track": t, "volume_db": db} for t, db in FL["live"].items()])
            FL["live"].clear()
        FL["saves"] += 1; FL["flag"] = 0
        return True
    m._save_in_fl_and_wait = fake_save
    m.fl_save = lambda *a, **k: (fake_save(song), {"saved": song})[1]
    m.fl_state = lambda: {"channels": [{"index": c, "insert": t, "muted": c in LIVE_MUTED} for c, t in CH_TRACK.items()],
                          "mixer": [{"index": 0, "effects": [{"slot": 9, "name": "Fruity Limiter"}]}]}

    def chset(i, muted=None, **k):
        (LIVE_MUTED.add if muted else LIVE_MUTED.discard)(i)
    m.fl_channel_set = chset
    exports = []

    def fake_in_fl(project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None, split=None):
        exports.append(split); m._note_export("in_fl", 1.0)
        lv = levels_now(); tracks = [CH_TRACK[c] for c in CH_TRACK if c not in LIVE_MUTED]
        folder = tempfile.mkdtemp(prefix="flh_fake_"); target = os.path.join(folder, "Beat.wav")
        AE.save(target, model(lv, tracks), FS, 24)
        sp = None
        if FL["split"]:                                               # 'Split mixer tracks' ticked in FL
            for t in FREQ:
                y = stem(t, lv) if t in tracks else np.zeros((int(FS * LEN["s"]), 2))
                AE.save(os.path.join(folder, f"Beat_Insert {t}.wav"), y, FS, 24)
            sp = m._split_collect(project, folder, target, "Beat", None)
        os.makedirs(out_dir, exist_ok=True)
        dst = os.path.join(out_dir, (out_stem or "Beat") + ".wav"); shutil.move(target, dst)
        shutil.rmtree(folder, ignore_errors=True)
        r = {"rendered": [dst], "seconds": 1.0, "method": "in_fl"}
        if sp:
            r["split"] = sp
        return r
    m._render_in_fl = fake_in_fl
    p0 = m.fl_preview()                                               # the same version fl_render just rendered
    assert p0["renders"] == 0 and "reused render" in p0["reused"] and not exports, p0
    file_edit(m.flp_add_notes, song, [{"note": 62, "bar": 1, "beat": 2, "beats": 1}], channel=1)
    p1 = m.fl_preview()
    assert p1["renders"] == 1 and p1["lufs"] is not None and not p1.get("reused"), p1
    assert not os.path.exists(os.path.join(tmp, "_preview")), "no preview WAV left in the song folder"
    p2 = m.fl_preview()
    assert p2["renders"] == 0 and "reused render" in p2["reused"] and p2["lufs"] == p1["lufs"], p2
    s1 = m.fl_preview(channels=[2])
    assert s1["renders"] == 1 and s1["lufs"] < p1["lufs"] and not LIVE_MUTED, (s1, LIVE_MUTED)
    assert m.fl_preview(channels=[2])["renders"] == 0
    file_edit(m.flp_add_notes, song, [{"note": 64, "bar": 2, "beat": 0, "beats": 1}], channel=4)   # elsewhere
    assert m.fl_preview(channels=[2])["renders"] == 0                 # its channels / tracks didn't change
    assert m.fl_preview()["renders"] == 1                             # the whole mix did
    # a live fader move (through the bridge): FL reports unsaved changes -> saved first -> a new key
    FL["live"][6] = -8.0; FL["flag"] = 1
    p3 = m.fl_preview()
    assert FL["saves"] == 1 and p3["renders"] == 1 and "saved FL first" in p3.get("cache_note", ""), p3
    assert p3["lufs"] < p1["lufs"] + 0.5, (p1["lufs"], p3["lufs"])
    print("B previews ok")

    # ---- one split export -> the stems set; fader / mute changes mixed offline
    m._state({"in_fl_render": {"ok": True}, "in_fl_split": {"on": True}})
    FL["split"] = True
    n0 = len(exports)
    ent, fp, info = m._stems_set(song)
    assert ent and info["renders"] == 1 and len(exports) == n0 + 1 and exports[-1] is True, info
    assert sorted(int(t) for t in ent["meta"]["tracks"]) == [0, 3, 4, 5, 6], ent["meta"]["tracks"]
    ent2, _fp, info2 = m._stems_set(song)
    assert ent2 and info2["renders"] == 0 and "unchanged" in info2.get("reused", ""), info2
    FL["live"][4] = -3.0; FL["flag"] = 1                             # move a fader live: no export needed
    p5 = m.fl_preview()
    assert p5["renders"] == 0 and "offline" in p5 and "approximate" in p5["offline"], p5    # master has a limiter
    assert p5["changed_tracks"] == [4], p5
    truth = model(levels_now())
    lt = m._lufs(truth, FS)
    assert abs(p5["lufs"] - lt) < 0.05, (p5["lufs"], lt)
    p6 = m.fl_preview(exact=True)
    assert p6["renders"] == 1 and abs(p6["lufs"] - p5["lufs"]) < 0.05 and abs(p6["true_peak_dbfs"] - p5["true_peak_dbfs"]) < 0.1
    # remix exactness (fader changes are linear): the offline mix = the stems at the new faders
    ent = RC.get(RC.stems_key(RC.fingerprint(m._read_flp_bytes(song))))
    lv = {int(t): list(v) for t, v in levels_now().items()}
    lv[5] = [int(m._fader(-4.5) * 16000), lv[5][1]]; lv[0] = [int(m._fader(-1.0) * 16000), lv[0][1]]
    nofx = dict(ent, meta=dict(ent["meta"], master_fx=False))
    y, fs_, inf = RC.remix(nofx, lv)
    assert inf["exact"] and fs_ == FS and sorted(inf["changed"]) == [0, 5], inf
    want = None
    for t in FREQ:                                                    # what the cached stems sum to at those faders
        old = ent["meta"]["tracks"][str(t)]["level"]
        x_t = RC.load(RC.file_named(ent, f"t{t}"))[0] * RC.fader_lin(lv[t][0]) / RC.fader_lin(old[0])
        want = x_t if want is None else want + x_t
    want = want * RC.fader_lin(lv[0][0])
    assert np.allclose(y, want, atol=2e-4), float(np.abs(y - want).max())
    assert np.allclose(y, model({**levels_now(), 5: lv[5], 0: lv[0]}), atol=2e-3)
    y2, _, inf2 = RC.remix(ent, lv)
    assert not inf2["exact"] and any("master chain not re-run" in w for w in inf2["why"]), inf2
    ym, _, infm = RC.remix(nofx, levels_now(), mutes={3: True})       # a mute = that track out of the sum
    assert np.allclose(ym, model(levels_now(), [4, 5, 6]), atol=2e-3) and infm["changed"] == [3], infm
    # user-facing stems from the same kind of single export (one file per mixer track, cut per stem)
    n0 = len(exports)
    st_dir = os.path.join(tmp, "Stems")
    sr = m._render_stems(song, st_dir, [0, 1, 2, 4], "flac", True, True, groups=None, method="split")
    assert sr["method"] == "split" and sr["renders"] == 1 and len(exports) == n0 + 1, sr
    assert len(sr["stems"]) == 4 and all(f.endswith(".flac") and os.path.isfile(f) for f in sr["stems"]), sr
    assert [x["mixer_tracks"] for x in sr["levels"]] == [[3], [4], [5], [6]], sr["levels"]
    try:                                                              # a channel on the master has no track file
        m._render_stems(song, st_dir, None, "flac", True, True, method="split"); raise SystemExit("no error")
    except RuntimeError as e:
        assert "shares a mixer track" in str(e), e
    print("B stems-once remix ok")

    # ============================================================ C. level balance from ONE export
    flh_store.journal_update(song, {"channels": {"0": {"role": "kick", "name": "Kick", "mixer": 3},
                                                 "1": {"role": "snare", "name": "Snare", "mixer": 4},
                                                 "2": {"role": "bass", "name": "Bass", "mixer": 5},
                                                 "4": {"role": "keys", "name": "Keys", "mixer": 6}}})

    class B:
        def call(self, cmd, timeout=15.0, **a):
            if cmd == "mixer_set":
                t = a["track"]
                if a.get("volume_db") is not None:
                    FL["live"][t] = float(a["volume_db"]); FL["flag"] = 1
                lvl = levels_now().get(t) or [None]
                return {"volume_db": FL["live"].get(t, m._fader_db((lvl[0] if lvl[0] is not None else 12800) / 16000))}
            return {}
    m.BRIDGE = B()
    gains = []
    m.fl_plugin_params = lambda index, slot=-1, filter="": {"params": [{"param": 0, "name": "Gain"},
                                                                       {"param": 2, "name": "Limiter ceiling"}]}
    m.fl_plugin_set_value = lambda index, param, target, slot=-1: gains.append(target) or {"text": f"{target:.1f}dB"}
    file_edit(m.flp_add_notes, song, [{"note": 67, "bar": 3, "beat": 0, "beats": 1}], channel=2)   # no stems yet
    m.fl_preview = lambda channels=None, **k: (_ for _ in ()).throw(AssertionError("no per-part export"))
    n0 = len(exports)
    rb = NS.balance_song(song, "lofi")
    assert set(rb["measured"]) == {"drums", "Bass", "Keys"} and rb["renders"] == 1 and len(exports) == n0 + 1, rb
    assert "Split" in (rb["how"] or ""), rb
    rb2 = NS.balance_song(song, "lofi")                               # faders moved, sound unchanged: no export
    assert rb2["renders"] == 0 and len(exports) == n0 + 1 and set(rb2["measured"]) == {"drums", "Bass", "Keys"}, rb2
    lv2 = levels_now()                                                # measured at the faders as they are now
    for label, ts in (("drums", [3, 4]), ("Bass", [5]), ("Keys", [6])):
        ref = m._lufs(sum(stem(t, lv2) for t in ts), FS)
        assert abs(rb2["measured"][label] - ref) < 0.15, (label, rb2["measured"][label], ref)
    # no one-pass stems on this PC: each part alone, as before (and said)
    m._state({"in_fl_split": {"on": False}})
    previews = []
    m.fl_preview = lambda channels=None, **k: previews.append(list(channels)) or {"lufs": -20.0, "silent": False}
    rb3 = NS.balance_song(song, "lofi")
    assert len(previews) == 3 and any("one-pass stems unavailable" in n for n in rb3["notes"] or []), rb3
    print("C one-pass balance ok")

    # ============================================================ D. partial renders (bar range, pre-roll, tempo map)
    m.fl_running = lambda: False
    m._open_project_guess = lambda: (None, [])
    m._render_in_fl = lambda *a, **k: (_ for _ in ()).throw(AssertionError("FL is closed"))
    m.fl_render(song, output_dir=out)                                 # a full render of this version (cached)
    nr = len(runs)
    rr = m.render_range(song, 5, 7)
    _ts, bar_s, _P, _mm = RC.timeline(m._read_flp_bytes(song))
    assert rr["renders"] == 0 and "cached full render" in rr["source"] and len(runs) == nr, rr
    assert abs(rr["offset_s"] - bar_s(3)) < 1e-3 and abs(rr["start_s"] - bar_s(5)) < 1e-3, rr  # 2 bars of pre-roll
    full_x = m._wav_load(m.fl_render(song, output_dir=out)["rendered"][0])[0]
    i0 = int(round(bar_s(3) * FS))
    assert np.allclose(rr["x"][:1000], full_x[i0:i0 + 1000], atol=1e-6)
    m.flp_add_notes(song, [{"note": 62, "bar": 6, "beat": 0, "beats": 1}], channel=4)   # no full render of this one
    datas = []
    real_section = m._section_bytes

    def spy_section(d, a, b=None):
        datas.append((a, b)); return real_section(d, a, b)
    m._section_bytes = spy_section
    rr2 = m.render_range(song, 5, 7)
    assert rr2["renders"] == 1 and "range render" in rr2["source"] and datas == [(3.0, 7)], (rr2, datas)
    rr3 = m.render_range(song, 5, 7)
    assert rr3["renders"] == 0 and "cached range" in rr3["source"], rr3
    m._section_bytes = real_section
    print("D partial renders ok")

    # ============================================================ E. ending rules predicted from the notes
    hold, end_s, last_on = RC.ending_from_notes(m._read_flp_bytes(song))
    LEN["s"] = 60.0                                                   # the (fake) range render holds the ending
    ok = m.fl_ending_check(song, rules={"min_ending_s": 0.1, "min_tail_s": 0.1, "max_lead_silence_s": 99})
    assert ok["predicted"] == "pass" and ok["renders"] == 0 and "measured" not in ok, ok
    bad = m.fl_ending_check(song, rules={"min_ending_s": 120, "min_tail_s": 1})
    assert bad["predicted"] == "fail" and "flp_extend_ending" in bad["fix"] and bad["renders"] == 0, bad
    edge = m.fl_ending_check(song, rules={"min_ending_s": 0.1, "min_tail_s": max(0.2, hold - 0.5)})
    assert edge["predicted"] == "borderline" and "measured" in edge, edge
    assert {c["rule"] for c in edge["measured"]["checks"]} <= set(m.ENDING_RULES), edge["measured"]
    print("E ending prediction ok")

    # ============================================================ F. open=false: the balance runs at fl_open
    song2 = os.path.join(tmp, "Later.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song2)
    NS._balance_pending(song2, "lofi")
    assert flh_store.journal_get(song2)["balance_pending"]["genre"] == "lofi"
    started = []
    real_start = NS.start_balance
    NS.start_balance = lambda p, g=None, wait_s=90, pending=False: started.append((p, g, wait_s, pending))
    m._fl_open_raw = lambda p, save_current=True: {"opened": p}
    m.fl_open(song2)
    m.fl_open(song2)                                                  # one start per open burst
    assert started == [(song2, "lofi", 300, True)], started
    NS.start_balance = real_start
    real_bal = NS.balance_song
    NS.balance_song = lambda p, g=None: {"moved": ["Bass +2.0 dB"], "notes": None, "took_s": 0.1, "renders": 1}
    m._wait_open_in_fl = lambda p, t=240: True
    NS.start_balance(song2, "lofi", wait_s=5, pending=True)
    t0 = time.time()
    while flh_store.journal_get(song2).get("balance_pending") and time.time() - t0 < 10:
        time.sleep(0.05)
    assert not flh_store.journal_get(song2).get("balance_pending"), flh_store.journal_get(song2)
    assert any("levels balanced" in n and "1 export" in n for n in m._AUTOSYNC_NEWS), m._AUTOSYNC_NEWS
    NS.balance_song = real_bal
    print("F pending balance ok")

    # ============================================================ G. counters, status, LRU size cap
    tm = m.fl_timings()["renders"]
    assert tm["exports"] >= 10 and tm["reused"] >= 5 and tm["offline"] >= 1 and tm["by_route"].get("in_fl"), tm
    st = m.fl_render_cache()
    assert st["used_mb"] > 0 and "render" in st["by_kind"] and "stems" in st["by_kind"], st
    recycled = []                                 # evicted folders are deleted, never recycled: on the PC the
                                                  # recycler refused the state folder and nothing was ever evicted
    def fake_recycle(paths):                      # (the cache stood at 5.3 GB against a 2 GB cap)
        recycled.append(paths)
        raise PermissionError("outside FLH_ROOTS")
    m.files_recycle = fake_recycle
    was_win = m.IS_WINDOWS; m.IS_WINDOWS = True
    os.environ["FLH_RENDER_CACHE_GB"] = str(4e5 / (1 << 30))         # ~400 KB: the oldest entries go
    small = []
    for i in range(3):
        f = os.path.join(tmp, f"blob{i}.wav"); AE.save(f, np.zeros((40000, 2)), FS, 16)   # ~160 KB each
        small.append(RC.put(f"blob{i}", "render", [{"src": f, "name": "b.wav", "mode": "copy"}]))
        time.sleep(0.01)
    assert RC.get("blob2") and RC.get("blob1") and not RC.get("blob0"), RC.status()
    assert not os.path.exists(small[0]["files"][0]["path"]) and not recycled, recycled   # deleted, bin untouched
    orphan = os.path.join(RC.cache_dir(), "orphan0123"); os.makedirs(orphan)                # a put that lost the
    open(os.path.join(orphan, "x.wav"), "wb").write(b"0" * 1000)                           # index lock long ago
    os.utime(orphan, (time.time() - 7200, time.time() - 7200))
    young = os.path.join(RC.cache_dir(), "young0123"); os.makedirs(young)
    tr = RC.trim()
    assert tr["orphans_gone"] == 1 and not os.path.exists(orphan) and os.path.isdir(young), tr
    shutil.rmtree(young)
    m.IS_WINDOWS = was_win
    os.environ["FLH_RENDER_CACHE_GB"] = "0"                           # off: nothing reused
    assert RC.get("blob2") is None and not RC.enabled()
    os.environ.pop("FLH_RENDER_CACHE_GB")
    with RC.off():
        assert not RC.enabled()
    cl = m.fl_render_cache(action="clear")
    assert cl["used_mb"] == 0 and cl["cleared"] >= 3, cl
    try:
        m.fl_render_cache(split_click=[2, 0.5]); raise SystemExit("bad click point accepted")
    except ValueError:
        pass
    assert m.fl_render_cache(split_click=[0.31, 0.62])["split"]["click"] == [0.31, 0.62]
    clicks = []                                                       # 'Split mixer tracks' switched for a pass
    m._click_frac = lambda h, x, y: clicks.append((x, y)) or True
    m._state({"in_fl_split": {"on": False, "click": [0.3, 0.6]}})
    assert m._split_prepare(7, None) is None and not clicks           # a plain export leaves the user's setting
    assert m._split_prepare(7, True) == "on" and m._state()["in_fl_split"]["restore_off"] is True
    assert m._split_prepare(7, True) is None and len(clicks) == 1     # already on
    assert m._split_prepare(7, None) == "off" and m._state()["in_fl_split"]["on"] is False and len(clicks) == 2
    assert m._split_prepare(7, None) is None and len(clicks) == 2
    print("G counters / cap ok")

    # ============================================================ H. FL's split file names
    names = {3: "Kick", 4: "Kick", 5: "Bass"}
    got, left = RC.split_names(["/x/My Song_Master.wav", "/x/My Song_Kick.wav", "/x/My Song_Kick 2.wav",
                                "/x/My Song_Bass.wav", "/x/My Song_Insert 9.wav", "/x/weird.wav"], "My Song", names)
    assert got == {0: "/x/My Song_Master.wav", 3: "/x/My Song_Kick.wav", 4: "/x/My Song_Kick 2.wav",
                   5: "/x/My Song_Bass.wav", 9: "/x/My Song_Insert 9.wav"} and left == ["/x/weird.wav"], (got, left)
    print("H split names ok")

    # ============================================================ I. the key follows the song's CONTENT (PC 2026-10-23)
    # a version copy with the same title and an extended ending must never get the original's render: the key moves
    # with notes and song length, never with the Project-info title or the song name; a render shorter than the song
    # is never reused / kept
    m._render_short = REAL_SHORT
    m.fl_running = lambda: False
    fpk = lambda p: RC.mix_key(RC.fingerprint(m._read_flp_bytes(p)), formats="wav")
    orig = os.path.join(tmp, "Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), orig)
    m.flp_project_info(orig, output=orig, title="Song (Piano)")
    twin = os.path.join(tmp, "v2", "Song Rework.flp"); os.makedirs(os.path.dirname(twin))
    shutil.copy(orig, twin)                                           # a byte copy: same title, same content
    assert fpk(orig) == fpk(twin)                                     # same sound: one render serves both (correct)
    k0 = fpk(twin)
    m.flp_project_info(twin, output=twin, title="Something else")     # title: not part of the sound
    assert fpk(twin) == k0
    s0 = m._song_seconds(twin)[0]
    lst = m.flp_playlist(twin, "list")
    last = max((c for c in lst["clips"] if c.get("kind") == "pattern"), key=lambda c: c["bar"] + c["length_bars"])
    m.flp_playlist(twin, "resize", output=twin, clips=[last["id"]], length_bars=round(last["length_bars"] + 5, 3))
    assert m._song_seconds(twin)[0] > s0 + 1 and fpk(twin) != k0      # a longer song: another key
    k1 = fpk(twin)
    m.flp_add_notes(twin, [{"note": 64, "bar": 1, "beat": 1, "beats": 2}], channel=0)
    assert fpk(twin) != k1                                            # a note: another key
    # length check: FL's export runs at least to the song's end; > 1.5 s shorter = another state of the song
    song_s = m._song_seconds(twin)[0]
    wav_ok = os.path.join(tmp, "full.wav"); AE.save(wav_ok, np.zeros((int(FS * (song_s + 2.0)), 2)), FS, 16)
    wav_short = os.path.join(tmp, "short.wav"); AE.save(wav_short, np.zeros((int(FS * (song_s - 20.0)), 2)), FS, 16)
    wav_edge = os.path.join(tmp, "edge.wav"); AE.save(wav_edge, np.zeros((int(FS * (song_s - 1.0)), 2)), FS, 16)
    assert m._render_short(twin, wav_ok) is None and m._render_short(twin, wav_edge) is None
    sh = m._render_short(twin, wav_short)
    assert sh and abs(sh[0] - (song_s - 20.0)) < 0.05 and abs(sh[1] - song_s) < 0.05, sh
    rl = m._render_length(twin, wav_short)
    assert rl["short_s"] >= 19.9 and rl["render_ends_bar"] < rl["song_bars"] + 1, rl
    assert "short_s" not in m._render_length(twin, wav_ok)
    # a short render (FL held another state) is never cached under this song's key; a short cached one is dropped
    LEN["s"] = song_s - 20.0
    runs.clear()
    sub3 = os.path.join(tmp, "fin3"); os.makedirs(sub3)
    m._run_fl = fake_run_fl
    r_s = m._render_cached(twin, sub3, "wav", 60, out_stem="render", method="cli", close_fl=True, store="exact")
    assert r_s.get("render_short") and not m._render_cache_peek(twin, "wav", "cli"), r_s
    key = fpk(twin)
    RC.put(key, "render", [{"src": wav_short, "name": "mix.wav", "mode": "copy"}], route="cli", project=twin)
    assert not m._render_cache_peek(twin, "wav", "cli")               # kept but short: never handed back
    sub4 = os.path.join(tmp, "fin4"); os.makedirs(sub4)
    r_d = m._render_cached(twin, sub4, "wav", 60, out_stem="render", method="cli", close_fl=True, store="exact")
    assert not r_d.get("reused") and r_d["renders"] == 1 and "shorter than the song" in (r_d.get("cache_note") or ""), r_d
    print("I key follows content + length check ok")
finally:
    shutil.rmtree(tmp, True)
print("ok")
