"""Regression tests for the release bug hunt of the .flp FILE layer (arranging, writers, MIDI in/out, muted clips).
Each test names the bug it pins. Run: python3 tests/test_bughunt_release_file.py (no FL needed)."""
import os, shutil, struct, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_bhrel_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
m.fl_running = lambda: False
CORPUS = os.path.join(HERE, "corpus")
_n = [0]


def copy(name):
    _n[0] += 1
    d = os.path.join(tmp, f"p{_n[0]}"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".flp"); shutil.copy(os.path.join(CORPUS, name + ".flp"), p)
    return p


def path(name):
    _n[0] += 1
    d = os.path.join(tmp, f"o{_n[0]}"); os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def clips(p):
    return m.flp_playlist(p, "list")["clips"]


def heard(p):
    d = m._load(p); return m._song(d, m._events(d))


def recs(p):
    d = m._load(p); return m._playlist_at(d, m._events(d))[2]


def wav(p, seconds=16):
    import numpy as np
    f = wave.open(p, "wb"); f.setnchannels(1); f.setsampwidth(2); f.setframerate(44100)
    f.writeframes(np.zeros(44100 * seconds, dtype=np.int16).tobytes()); f.close()
    return p


def one_per_bar(bars=8, tempo=120):
    """one_channel with a note at the start of each of `bars` bars and a tempo record at tick 0 (as FL/MIDI imports
    have) in the same pattern."""
    p = copy("one_channel"); o = path("w.flp")
    m.flp_write_notes(p, o, [{"channel": 0, "notes": [[b, 1, 60, 100] for b in range(1, bars + 1)]}], tempo=tempo)
    d = m._load(o); pats = m._patterns(d, m._events(d))
    assert any(v["notes"] and v["ctrl"] for v in pats.values()), "fixture: no pattern with notes + a tempo row"
    return o


# ---------------------------------------------------------------------------------------------------------------
# BR-1 flp_arrange insert_bars/delete_bars/duplicate (and flp_compose_insert) treated any pattern with a controller
# row as a tempo lane and never split it: every FL/MIDI-imported pattern has a tempo row at tick 0, so its notes
# stayed where they were.
def test_arrange_moves_notes_of_patterns_with_a_tempo_row():
    o = one_per_bar(); P = m._ppq(m._load(o)); bar = 4 * P
    o2 = path("ins.flp"); m.flp_arrange(o, [{"op": "insert_bars", "at": 3, "bars": 2}], output=o2)
    assert [n[0] // bar + 1 for n in heard(o2)[0]] == [1, 2, 5, 6, 7, 8, 9, 10]
    assert heard(o2)[1][0] == (0, 120.0) and all(b == 120.0 for _, b in heard(o2)[1]), heard(o2)[1]
    o3 = path("del.flp"); m.flp_arrange(o, [{"op": "delete_bars", "from": 3, "to": 5}], output=o3)
    assert [n[0] // bar + 1 for n in heard(o3)[0]] == [1, 2, 3, 4, 5, 6]
    o4 = path("dup.flp"); m.flp_arrange(o, [{"op": "duplicate", "from": 3, "to": 5}], output=o4)
    assert [n[0] // bar + 1 for n in heard(o4)[0]] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    import flh_compose
    o5 = path("ci.flp"); r = flh_compose.flp_compose_insert(o, o5, at=3, bars=2)
    assert [n[0] // bar + 1 for n in heard(o5)[0]] == [1, 2, 5, 6, 7, 8, 9, 10], r


def test_note_pattern_tempo_change_held_into_split_clip():
    # a tempo change inside the pattern: the clip that starts after a delete starts at the tempo in force there
    o = one_per_bar(); d = m._load(o); ev = m._events(d); P = m._ppq(d)
    k = next(i for i, e in enumerate(ev) if e["id"] == m.EV_PATTERN_CTRL)
    row = bytes(m._payload(d, ev[k]))[:12]
    fast = struct.pack("<I", 4 * 4 * P) + row[4:8] + struct.pack("<I", 150000)        # 150 BPM from bar 5
    m._save(m._splice(d, ev, [(k, k + 1, m._data_event(m.EV_PATTERN_CTRL, row + fast))]), o)
    m.flp_arrange(o, [{"op": "delete_bars", "from": 3, "to": 6}])
    tm = heard(o)[1]
    assert (2 * 4 * P, 150.0) in tm and tm[0] == (0, 120.0), tm                   # bar 3 now plays at 150
    assert [n[0] // (4 * P) + 1 for n in heard(o)[0]] == [1, 2, 3, 4, 5]


def test_tempo_lane_insert_uses_the_clip_start_offset():
    # _stretch_tempo_patterns cut at (at - pos), ignoring the clip's start offset, and wrote its end offset as the
    # length alone
    p = copy("one_channel"); m.flp_tempo_write(p, p, points=[[1, 100], [3, 120], [5, 140]])
    d = m._load(p); ev = m._events(d); P = m._ppq(d); bar = 4 * P
    pid = next(i for i, v in m._patterns(d, ev).items() if v["name"] == "Tempo")
    k, size, rs = m._playlist_at(d, ev)
    for r in rs:
        if struct.unpack("<H", r[6:8])[0] == m.PATTERN_BASE + pid:          # window from pattern bar 3, at song bar 3
            ln, = struct.unpack("<I", r[8:12]); struct.pack_into("<I", r, 0, 2 * bar)
            struct.pack_into("<I", r, 8, ln - 2 * bar); struct.pack_into("<II", r, 24, 2 * bar, ln)
    m._save(m._splice(d, ev, [(k, k + 1, m._data_event(m.EV_PLAYLIST, b"".join(bytes(r) for r in rs)))]), p)
    m.flp_arrange(p, [{"op": "insert_bars", "at": 4, "bars": 1}])
    tm = [x for x in heard(p)[1] if x[0] > 0]
    assert tm == [(2 * bar, 120.0), (5 * bar, 140.0)], tm                      # bar 3 stays, bar 5 -> bar 6
    r = next(r for r in recs(p) if struct.unpack("<H", r[6:8])[0] == m.PATTERN_BASE + pid)
    s, e = m._clip_offsets(r); ln, = struct.unpack("<I", r[8:12])
    assert s == 2 * bar and e == s + ln, (s, e, ln)


# ---------------------------------------------------------------------------------------------------------------
# BR-2 delete_bars dropped a whole audio clip that started inside the cut, even when most of it ran on after it.
def test_delete_bars_keeps_audio_that_runs_past_the_cut():
    p = copy("one_channel"); m.flp_add_audio_clip(p, p, wav(path("vox.wav")), bar=4)
    r = m.flp_arrange(p, [{"op": "delete_bars", "from": 3, "to": 5}])
    a = [c for c in clips(p) if c["kind"] == "audio"]
    assert [(c["bar"], c["length_bars"]) for c in a] == [(3.0, 3.0)], a        # bars 4-8 -> 3-6 (bar 4 was cut)
    assert any("now starts at the cut" in n for n in r.get("notes", [])), r


# ---------------------------------------------------------------------------------------------------------------
# BR-3 insert/delete left automation and audio clips that start BEFORE the edit point and run past it untouched: a
# whole-song volume curve didn't get the inserted bars, nor lose the deleted ones.
def test_automation_crossing_the_edit_point_follows_inserts_and_deletes():
    p = copy("one_channel")
    ch = m.flp_add_automation(p, p, [{"bar": 1, "value": 0.2}, {"bar": 5, "value": 1.0}, {"bar": 9, "value": 0.2}])["channel"]

    def auto(f):
        d = m._load(f); ev = m._events(d)
        pts = m._auto_points(m._payload(d, ev[m._automation_events(d, ev)[ch]]))[0]
        c = [c for c in clips(f) if c["kind"] == "automation"]
        return [(round(b, 3), round(v, 3)) for b, v, _, _ in pts], [(c_["bar"], c_["length_bars"]) for c_ in c]
    o = path("ins.flp"); m.flp_arrange(p, [{"op": "insert_bars", "at": 3, "bars": 2}], output=o)
    pts, cl = auto(o)
    assert cl == [(1.0, 10.0)], cl
    assert pts == [(0.0, 0.2), (8.0, 0.6), (16.0, 0.6), (24.0, 1.0), (40.0, 0.2)], pts
    o = path("del.flp"); r = m.flp_arrange(p, [{"op": "delete_bars", "from": 3, "to": 5}], output=o)
    pts, cl = auto(o)
    assert cl == [(1.0, 6.0)], cl
    assert pts[:2] == [(0.0, 0.2), (8.0, 0.6)] and pts[-1] == (24.0, 0.2) and abs(pts[2][1] - 1.0) < 0.01, pts
    assert any("automation" in n for n in r["notes"]), r


def test_audio_crossing_the_edit_point_is_reported():
    p = copy("one_channel"); m.flp_add_audio_clip(p, p, wav(path("vox.wav")), bar=2)
    r = m.flp_arrange(p, [{"op": "insert_bars", "at": 3, "bars": 1}], output=path("i.flp"))
    assert any("audio clip" in n and "inserted bars" in n for n in r.get("notes", [])), r
    r = m.flp_arrange(p, [{"op": "delete_bars", "from": 3, "to": 4}], output=path("d.flp"))
    assert any("audio clip" in n and "runs across the cut" in n for n in r.get("notes", [])), r
    q = copy("one_channel"); m.flp_add_audio_clip(q, q, wav(path("short.wav"), 8), bar=2)      # bars 2-4 (60 BPM)
    r = m.flp_arrange(q, [{"op": "delete_bars", "from": 3, "to": 6}])
    assert [(c["bar"], c["length_bars"]) for c in clips(q) if c["kind"] == "audio"] == [(2.0, 1.0)], clips(q)
    assert any("ended inside the cut" in n for n in r["notes"]), r


# ---------------------------------------------------------------------------------------------------------------
# BR-4 writers turned bars into ticks as beats_per_bar * PPQ, ignoring time-signature markers (inline notes, tempo
# points, section markers, bar starts for audio slicing).
def test_writers_follow_time_signature_markers():
    p = copy("timesig_markers"); d = m._load(p); ev = m._events(d); P = m._ppq(d); mm = m._meter_map(d, ev)
    assert len(mm) > 1, mm
    o = path("rp.flp"); m.flp_replace_part(p, o, 0, notes=[[10, 0.25, 60, 100]])
    assert [n[0] for n in heard(o)[0] if n[2] == 0] == [m._bar_to_tick(10, mm, P)]
    o = path("tw.flp"); m.flp_tempo_write(p, o, points=[[1, 100], [10, 140]])
    assert (m._bar_to_tick(10, mm, P), 140.0) in heard(o)[1], heard(o)[1]
    d2, _ = m._set_markers(d, [(10, "Here")], P)
    mk = [x for x in m._markers(d2, m._events(d2)) if x["name"] == "Here"]
    assert [x["tick"] for x in mk] == [m._bar_to_tick(10, mm, P)], mk


def test_bar_starts_follow_the_meter():
    p = copy("one_channel"); m.flp_project_info(p, p, time_sig_num=3, time_sig_beat=4)
    _, tm = heard(p); bpm = tm[0][1]
    assert len(tm) == 1, tm
    st = m._bar_starts_s(p, start_bar=2, every="1bar", until_s=10)
    beat = 60.0 / bpm
    assert abs(st[1] - st[0] - 3 * beat) < 1e-6, (st[:3], bpm)
    st0 = m._bar_starts_s(p, start_bar=1, every="1bar", until_s=10)
    assert abs(st0[2] - 2 * 3 * beat) < 1e-6, st0[:3]


# ---------------------------------------------------------------------------------------------------------------
# BR-5 _place_pattern copied the first clip's flags: in a song whose first clip is muted, new clips came out muted.
def test_new_clip_not_muted_like_its_template():
    p = copy("one_channel"); m.flp_playlist(p, "mute", output=p, clips=[clips(p)[0]["id"]])
    m.flp_new_pattern(p, "Chorus", output=p, place_at_bar=9, length_bars=4)
    m.flp_tempo_write(p, p, points=[[1, 90], [5, 120]])
    got = {c["name"]: c["muted"] for c in clips(p)}
    assert got == {"Pattern 1": True, "Chorus": False, "Tempo": False}, got


# ---------------------------------------------------------------------------------------------------------------
# BR-6 _song_parse counted muted clips: export_midi, flp_info and every reader 'heard' notes FL doesn't play.
def test_muted_clips_play_nothing():
    p = copy("one_channel"); before = len(heard(p)[0])
    m.flp_playlist(p, "mute", output=p, clips=[clips(p)[0]["id"]])
    assert heard(p)[0] == [] and m.flp_info(p)["notes_per_channel"] == {}, m.flp_info(p)["notes_per_channel"]
    d = m._load(p); assert len(m._song_x_all(d, m._events(d))[0]) == before
    try:
        m.flp_export_midi(p, path("x.mid")); raise AssertionError("exported muted notes")
    except ValueError:
        pass


# ---------------------------------------------------------------------------------------------------------------
# BR-7 midi_read dropped notes never released, paired notes by key across tracks/channels, crashed with a bare
# IndexError on a truncated track and left a tempo map starting after tick 0 without the 120 BPM before it;
# midi_write sorted a 0-length note's off before its on (a stuck note).
def test_midi_read_hung_notes_closed_and_reported():
    mid = path("hang.mid")
    on = [(0, b"\x90\x3c\x64"), (96, b"\x80\x3c\x00"), (96, b"\x90\x40\x64"), (384, b"\x90\x43\x64"), (480, b"\x80\x43\x00")]
    m.midi_write(mid, 96, [("tempo", [(0, b"\xff\x51\x03" + (500000).to_bytes(3, "big"))]), ("notes", on)])
    w = []
    assert m.midi_read(mid, w)[1] == [(0, 96, 60, 100), (96, 480, 64, 100), (384, 480, 67, 100)] and w
    r = m.flp_write_notes(copy("one_channel"), path("h.flp"), [{"channel": 0, "midi": mid}])
    assert r["notes_written"] == 3 and "never released" in r["warnings"][0], r


def test_midi_read_pairs_per_track_and_channel():
    mid = path("two.mid")
    a = [(0, b"\x90\x3c\x64"), (400, b"\x80\x3c\x00")]                         # track 1: a long C4
    b = [(100, b"\x91\x3c\x50"), (150, b"\x81\x3c\x00")]                       # track 2: a short C4 inside it
    m.midi_write(mid, 96, [("t", []), ("a", a), ("b", b)])
    assert sorted(m.midi_read(mid)[1]) == [(0, 400, 60, 100), (100, 150, 60, 80)], m.midi_read(mid)[1]
    mid2 = path("ch.mid")                                                      # one track, two channels
    m.midi_write(mid2, 96, [("t", []), ("x", [(0, b"\x90\x3c\x64"), (100, b"\x91\x3c\x50"), (150, b"\x81\x3c\x00"),
                                               (400, b"\x80\x3c\x00")])])
    assert sorted(m.midi_read(mid2)[1]) == [(0, 400, 60, 100), (100, 150, 60, 80)], m.midi_read(mid2)[1]


def test_midi_read_truncated_and_late_tempo():
    mid = path("t.mid"); m.midi_write(mid, 96, [("t", [(96, b"\xff\x51\x03" + (400000).to_bytes(3, "big"))]),
                                              ("n", [(0, b"\x90\x3c\x64"), (96, b"\x80\x3c\x00")])])
    assert m.midi_read(mid)[2] == [(0, 120.0), (96, 150.0)], m.midi_read(mid)[2]
    raw = bytearray(open(mid, "rb").read())
    j = raw.rindex(b"\x80\x3c\x00")                                           # cut the last track inside an event
    cut = raw[:j + 1]; k = cut.rindex(b"MTrk"); struct.pack_into(">I", cut, k + 4, len(cut) - k - 8)
    bad = path("bad.mid"); open(bad, "wb").write(bytes(cut))
    try:
        m.midi_read(bad); raise AssertionError("no error")
    except ValueError as e:
        assert "truncated" in str(e), e


def test_midi_write_zero_length_note_not_stuck():
    mid = path("z.mid"); m.midi_write(mid, 96, [("t", []), ("n", [(48, b"\x80\x3c\x00"), (48, b"\x90\x3c\x64")])])
    raw = open(mid, "rb").read()
    assert raw.index(b"\x90\x3c\x64") < raw.index(b"\x80\x3c\x00"), raw
    assert m.midi_read(mid)[1] == [(48, 49, 60, 100)], m.midi_read(mid)[1]
    mid = path("y.mid")                                                       # a note ending where the next starts
    m.midi_write(mid, 96, [("t", []), ("n", [(0, b"\x90\x3c\x64"), (48, b"\x80\x3c\x00"), (48, b"\x90\x3c\x50"),
                                             (96, b"\x80\x3c\x00")])])
    assert m.midi_read(mid)[1] == [(0, 48, 60, 100), (48, 96, 60, 80)], m.midi_read(mid)[1]


# ---------------------------------------------------------------------------------------------------------------
# BR-8 flp_write_arrangement defaulted beats_per_bar to 4 on a 3/4 template: 4/4 sections in a 3/4 song.
def test_write_arrangement_takes_the_template_meter():
    p = copy("one_channel"); m.flp_project_info(p, p, time_sig_num=3, time_sig_beat=4)
    mid = path("n.mid"); P = 96; on = []
    for b in range(8):
        on += [(b * 3 * P, b"\x90\x3c\x64"), (b * 3 * P + P, b"\x80\x3c\x00")]
    m.midi_write(mid, P, [("t", []), ("n", on)])
    o = path("arr.flp")
    r = m.flp_write_arrangement(p, o, [{"channel": 0, "midi": mid}], [{"name": "A", "bar": 1}, {"name": "B", "bar": 5}])
    assert r["beats_per_bar"] == 3 and m.flp_info(o)["time_signature"] == "3/4", r
    assert [(c["name"], c["bar"], c["length_bars"]) for c in clips(o)][0] == ("A", 1.0, 4.0), clips(o)


# ---------------------------------------------------------------------------------------------------------------
# BR-9 flp_pattern_tools split re-placed each new pattern with window (0, length): a clip trimmed into its pattern
# played the pattern's start instead; mute and track weren't carried either.
def test_split_keeps_clip_window_mute_and_track():
    p = copy("base")
    c = [c for c in clips(p) if c["kind"] == "pattern"][0]
    m.flp_playlist(p, "resize", output=p, clips=[c["id"]], trim_start_bars=8)
    c = [c for c in clips(p) if c["kind"] == "pattern"][0]
    m.flp_playlist(p, "move", output=p, clips=[c["id"]], track=5)
    before = sorted((n[0], n[2], n[3]) for n in heard(p)[0])
    o = path("split.flp"); m.flp_pattern_tools(p, "split", pattern=c["pattern"], output=o)
    assert sorted((n[0], n[2], n[3]) for n in heard(o)[0]) == before
    new = [x for x in clips(o) if x["kind"] == "pattern"]
    assert new and all(x["from_bar_in_pattern"] == 9.0 and x["track"] == 5 and not x["muted"] for x in new), new
    q = copy("one_channel"); m.flp_playlist(q, "mute", output=q, clips=[clips(q)[0]["id"]])
    o = path("split2.flp"); m.flp_pattern_tools(q, "split", pattern=1, output=o)
    assert all(x["muted"] for x in clips(o) if x["kind"] == "pattern"), clips(o)


# ---------------------------------------------------------------------------------------------------------------
# BR-10 flp_write_notes into an empty-section template sized the Song clip as 4/4 bars: in a 3/4 project the notes
# past ~3/4 of the length were cut off. Overlapping same-key notes on one channel were written without a word.
def test_fresh_song_write_follows_the_meter_and_reports_overlaps():
    t = copy("one_channel_no_notes"); m.flp_new_pattern(t, "A"); m.flp_new_pattern(t, "B")
    m.flp_project_info(t, t, time_sig_num=3, time_sig_beat=4)
    o = path("fresh.flp")
    notes = [[b, 1, 60, 100] for b in range(1, 13)] + [[12.5, 1, 60, 90]]
    r = m.flp_write_notes(t, o, [{"channel": 0, "notes": notes}])
    P = m._ppq(m._load(o))
    assert len(heard(o)[0]) == 13, (r, len(heard(o)[0]))
    assert m.flp_info(o)["time_signature"] == "3/4" and r["beats_per_bar"] == 3, r
    song = [c for c in clips(o) if c["kind"] == "pattern"]
    assert song[0]["bar"] == 1.0 and song[0]["length_bars"] >= 12.5, song
    assert r.get("same_key_overlaps") == {"0": 1} and "overlapping same-key" in r["overlap_note"], r
    assert heard(o)[0][-1][0] == int(round(11.5 * 3 * P))


def test_write_notes_reports_overlaps():
    r = m.flp_write_notes(copy("one_channel"), path("ov.flp"),
                          [{"channel": 0, "notes": [[1, 1, 60, 100], [1.25, 1, 60, 90], [3, 0.5, 62, 100]]}])
    assert r["same_key_overlaps"] == {"0": 1}, r
    r = m.flp_write_notes(copy("one_channel"), path("no.flp"), [{"channel": 0, "notes": [[1, 1, 60, 100], [2, 1, 60, 90]]}])
    assert "same_key_overlaps" not in r, r


# ---------------------------------------------------------------------------------------------------------------
# BR-11 flh_compose's _new_pattern took max + 1 with no cap: pattern 1000+ in a project at FL's 999 limit.
def test_compose_new_pattern_respects_the_999_limit():
    import flh_compose
    d = m._load(copy("patterns_1000"))
    try:
        flh_compose._new_pattern(d, "X"); raise AssertionError("made a pattern past 999")
    except ValueError as e:
        assert "999" in str(e), e
    d = m._load(copy("one_channel")); d, pid = flh_compose._new_pattern(d, "X")
    assert pid == 2, pid


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
