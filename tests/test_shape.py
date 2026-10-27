"""flh_shape: flp_extend_ending holds the last chord longer (layers let go earlier, clip grows, optional ritard) and
flp_smooth_entries ramps in parts that enter out of nowhere. Run: python3 tests/test_shape.py (no FL needed)."""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_shape_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_shape  # noqa: E402
ns = vars(m)
flh_store.register(ns); flh_shape.register(ns)
m.fl_running = lambda: False
try:
    src = os.path.join(HERE, "corpus", "base.flp")
    p = os.path.join(tmp, "song.flp"); shutil.copy(src, p)
    flh_store.journal_update(p, set={"channels": {"0": {"role": "melody"}, "1": {"role": "accompaniment"},
                                                  "2": {"role": "bass"}, "4": {"role": "strings"}}})
    d = m._load(p); notes, tempos = m._song_x(d, m._events(d)); P = m._ppq(d)
    last_on = max(n[0] for n in notes); last_end = max(n[0] + n[1] for n in notes)
    dry = flh_shape.flp_extend_ending(p, seconds=20, dry_run=True)
    assert dry["dry_run"] and dry["held_notes"] > 0 and m._load(p) == d, dry          # nothing written
    r = ns["TOOLS"]["flp_extend_ending"][0](project=p, output=p, seconds=20, ritard=0.1)
    d2 = m._load(p); n2, t2 = m._song_x(d2, m._events(d2))
    end2 = max(n[0] + n[1] for n in n2)
    bpm = 60.0 * 0.9
    want = last_end + int(round(20 * bpm / 60 * P))
    assert abs(end2 - want) <= 2, (end2, want, r)
    lay = [n for n in n2 if n[2] == 4 and n[0] + n[1] > last_on]                   # strings: let go earlier
    assert all(n[0] + n[1] < end2 for n in lay), lay[:3]
    assert r["clips_grown"] >= 1 and r["end_bar_after"] > r["end_bar_before"], r
    assert "ritard" in r and len({round(b, 2) for _, b in t2}) >= 2, (r, t2[:4])
    inf = m.flp_info(p)
    assert inf["song_length_bars"] >= inf["last_note_end_bars"] - 0.01, inf            # the clip covers the ring
    # rules mode: a slow 3/4 piece with an expressive tempo map; the final chord must sound long enough IN SECONDS
    sl = os.path.join(tmp, "slow.flp")
    m.flp_write_notes(src, sl, [{"channel": 0, "notes": [[b, 1, 60 + b % 5, 70] for b in range(1, 9)]
                                 + [[9, 1, k, 60] for k in (48, 55, 64)]},
                                {"channel": 4, "notes": [[8, 2, 52, 50], [8, 2, 59, 50]]}], tempo="keep")
    m.flp_project_info(sl, output=sl, time_sig_num=3, time_sig_beat=4)
    m.flp_tempo_write(sl, sl, points=[[1, 31], [5, 24], [8, 27]])
    flh_store.journal_update(sl, set={"channels": {"0": {"role": "melody"}, "4": {"role": "strings"}}})
    flh_store.profile_set("Slow pieces", {"rules": {"min_ending_s": 30, "min_tail_s": 6}})
    d = m._load(sl); n0, t0 = m._song_x(d, m._events(d)); P = m._ppq(d)
    assert len({round(b) for _, b in t0}) >= 3, t0[:6]                              # a real tempo map
    want, marg = flh_shape.hold_for_rules({"min_ending_s": 30, "min_tail_s": 6})
    r = ns["TOOLS"]["flp_extend_ending"][0](project=sl, output=sl)
    assert r["mode"] == "rules" and r["rules"]["min_ending_s"] == 30 and abs(r["hold"]["main_s"] - want) < 0.2, r
    assert want >= 30 / flh_shape.HOLD_FADE_EFF and marg >= 2, (want, marg)
    d = m._load(sl); n1, t1 = m._song_x(d, m._events(d))
    on = max(n[0] for n in n1)
    pend = max(n[0] + n[1] for n in n1 if n[2] == 0); lend = max(n[0] + n[1] for n in n1 if n[2] == 4)
    ps, ls = flh_shape._secs(t1, on, pend, P), flh_shape._secs(t1, on, lend, P)
    assert abs(ps - r["hold"]["main_s"]) < 0.2 and 1.4 < ps - ls < 5.1, (ps, ls, r)   # seconds via the tempo map
    assert all(n[0] + n[1] == pend for n in n1 if n[2] == 0 and n[0] == on), "every chord note held"
    bpm_end = flh_shape._bpm_at(t1, on, 0)
    assert abs((pend - on) / P - ps * bpm_end / 60) < 0.6, (pend - on, ps, bpm_end)  # beats != seconds at ~27 BPM
    inf = m.flp_info(sl)
    assert inf["time_signature"] == "3/4" and inf["song_length_bars"] > inf["last_note_end_bars"] + 0.3, inf
    before = open(sl, "rb").read()
    r2 = flh_shape.flp_extend_ending(sl)                                            # already long enough: no change
    assert r2["held_notes"] == 0 and open(sl, "rb").read() == before, r2
    rs = flh_shape.flp_extend_ending(sl, hold_s=60, dry_run=True)
    assert rs["mode"] == "hold_s" and abs(rs["hold"]["main_s"] - 60) < 0.2 and rs["held_notes"] > 0, rs
    flh_store.profile_set("Slow pieces", {"rules": {"exempt": ["slow"]}})
    assert flh_shape._profile_rules(sl) == (None, None)
    # entrances: a channel that enters after silence with a jump gets ramped
    q = os.path.join(tmp, "entries.flp")
    m.flp_write_notes(src, q, [{"channel": 0, "notes": [[b, 0.25, 60, 40] for b in range(1, 9)]},
                               {"channel": 2, "notes": [[b + x / 4, 0.25, 72, 110] for b in range(5, 9) for x in range(4)]}],
                      tempo="keep")
    dr = flh_shape.flp_smooth_entries(q, dry_run=True)
    assert dr["entries"] and dr["entries"][0]["channel"] == 2 and dr["entries"][0]["bar"] == 5, dr
    rr = flh_shape.flp_smooth_entries(q)
    dq = m._load(q); nq, _ = m._song_x(dq, m._events(dq))
    first = sorted([n for n in nq if n[2] == 2])[:4]
    assert first[0][4] < first[-1][4] < 110 + 1 and first[0][4] <= 0.6 * 110, first
    assert rr["changed_notes"] == 4, rr
finally:
    shutil.rmtree(tmp, True)
print("shape ok")
