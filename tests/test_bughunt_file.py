"""Regression tests for bugs found in the bug hunt of the .flp FILE layer (parse/write, flp_* tools, arguments).
Each test names the bug it pins. Run: python3 tests/test_bughunt_file.py (no FL needed)."""
import glob, hashlib, os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_bhfile_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402
import fl_music  # noqa: E402
ns = vars(m)
m.fl_running = lambda: False
CORPUS = os.path.join(HERE, "corpus")
_n = [0]


def copy(name):
    _n[0] += 1
    d = os.path.join(tmp, f"p{_n[0]}"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".flp"); shutil.copy(os.path.join(CORPUS, name + ".flp"), p)
    return p


def sha(p):
    return hashlib.sha1(open(p, "rb").read()).hexdigest()


def clips(p):
    return m.flp_playlist(p, "list")["clips"]


def three_four(name="one_channel"):
    p = copy(name)
    m.flp_project_info(p, p, time_sig_num=3, time_sig_beat=4)
    return p


# ---------------------------------------------------------------------------------------------------------------
# BF-1 flp_write_notes / flp_write_arrangement into a template whose patterns are all empty (flh_new_song's section
# blocks) shutil.copy'd the template over `output` before anything else: an existing project at `output` was
# overwritten with NO backup (the one backup per call then saved the template copy, not the user's song).
def test_fresh_song_write_backs_up_existing_output():
    t = copy("one_channel_no_notes")
    m.flp_new_pattern(t, "A"); m.flp_new_pattern(t, "B")
    out = copy("base"); before = sha(out)
    m._CALL_BACKUPS.clear()
    r = m.flp_write_notes(t, out, [{"channel": 0, "notes": [[1, 1, 60, 100]]}])
    assert r["notes_written"] == 1, r
    backs = [b["backup"] for b in m.flp_versions(out)]
    assert any(sha(b) == before for b in backs), "the song that was at output has no backup"


# ---------------------------------------------------------------------------------------------------------------
# BF-2 flp_arrange counted 4/4 bars (bar = 4 * PPQ) in every song: in 3/4 'insert 1 bar at bar 3' cut at the
# 4/4 bar 3 (tick 8 beats, inside FL's bar 3) and moved later clips by 4 beats; playlist markers (sections, time
# signature changes) never moved with the clips.
def test_arrange_follows_time_signature_and_moves_markers():
    p = three_four()
    P = m._ppq(m._load(p))
    m.flp_playlist(p, "place", pattern=1, bar=3, track=2, length_bars=1)
    d = m._load(p); d, _ = m._set_markers(d, [(3, "Verse")], P, 3); m._save(d, p)
    m.flp_arrange(p, [{"op": "insert_bars", "at": 3, "bars": 1}])
    c = [x for x in clips(p) if x["track"] == 2]
    assert [x["bar"] for x in c] == [4.0], c                       # FL's bar 3 -> bar 4 (3 beats later)
    d = m._load(p); mk = m._markers(d, m._events(d))
    assert [(x["name"], x["tick"]) for x in mk] == [("Verse", 9 * P)], mk
    m.flp_arrange(p, [{"op": "delete_bars", "from": 2, "to": 3}])
    c = [x for x in clips(p) if x["track"] == 2]
    assert [x["bar"] for x in c] == [3.0], c
    d = m._load(p); mk = m._markers(d, m._events(d))
    assert [x["tick"] for x in mk] == [6 * P], mk


# ---------------------------------------------------------------------------------------------------------------
# BF-3 flp_set_song_length took 4/4 bars (bars * 4 * PPQ): 8 bars in a 3/4 song became 10.67 of FL's bars.
def test_set_song_length_follows_time_signature():
    p = three_four()
    m.flp_set_song_length(p, 8)
    assert m.flp_info(p)["song_length_bars"] == 8, m.flp_info(p)["song_length_bars"]


# ---------------------------------------------------------------------------------------------------------------
# BF-4 flp_duck turned hit ticks into bars with 4 * PPQ and flp_add_automation read them back as bars of the
# project's meter: in 3/4 every dip landed at 3/4 of its tick (the pump drifted off the kick). Its start/end_bar
# were 4/4 bars too.
def test_duck_points_on_the_hits_in_three_four():
    p = three_four()
    d = m._load(p); P = m._ppq(d)
    hits = sorted({n[0] for n in m._song(d, m._events(d))[0] if n[2] == 0 and n[0] >= 3 * P})   # from FL's bar 2
    assert hits and hits[0] % (4 * P), hits                        # (a hit that isn't on a 4/4 bar line)
    r = m.flp_duck(p, 0, 0, start_bar=2)
    d = m._load(p); ev = m._events(d)
    k, size, recs = m._playlist_at(d, ev)
    auto = [x for x in recs if struct.unpack("<H", x[6:8])[0] == r["channel"]]
    pos = struct.unpack("<I", auto[0][0:4])[0]
    assert abs(pos - max(0, hits[0] - 1)) <= 1, (pos, hits[0], P)


# ---------------------------------------------------------------------------------------------------------------
# BF-5 flp_add_automation placed points with the project's beats per bar only, ignoring time signature markers
# (flp_add_audio_clip and flp_playlist place follow them): after a 7/8 marker the clip started at the wrong bar.
def test_add_automation_follows_meter_markers():
    p = copy("timesig_markers")
    d = m._load(p); ev = m._events(d); P = m._ppq(d); meters = m._meter_map(d, ev)
    assert len(meters) > 1, meters
    bar = 12
    r = m.flp_add_automation(p, p, [{"bar": bar, "value": 0.2}, {"bar": bar + 1, "value": 0.8}])
    assert r["start_bar"] == bar, r
    d = m._load(p); k, size, recs = m._playlist_at(d, m._events(d))
    auto = [x for x in recs if struct.unpack("<H", x[6:8])[0] == r["channel"]]
    assert struct.unpack("<I", auto[0][0:4])[0] == m._bar_to_tick(bar, meters, P)



# ---------------------------------------------------------------------------------------------------------------
# BF-6 flp_playlist: 'place' refused a project whose playlist had no pattern clip yet ("no pattern clip to copy
# the clip format from") or no clip event at all ("no playlist clips yet") - while flp_new_pattern(place_at_bar)
# places into the same project fine (FL's own clip record); 'list' raised on an empty playlist instead of listing
# nothing.
def test_playlist_place_into_empty_playlist():
    for strip in (False, True):
        p = copy("one_channel_no_notes")
        if strip:                                                  # no playlist clip event (233) at all
            d = m._load(p); ev = m._events(d)
            ks = [k for k, e in enumerate(ev) if e["id"] == m.EV_PLAYLIST]
            d = m._splice(d, ev, [(k, k + 1, b"") for k in ks]); m._save(d, p)
            assert m.flp_playlist(p, "list")["clips"] == []
        pid = m.flp_patterns(p)[0]["pattern"]
        r = m.flp_playlist(p, "place", pattern=pid, bar=2, track=3, length_bars=2)
        assert len(r["clips"]) == 1, r
        c = clips(p)
        assert [(x["pattern"], x["bar"], x["track"], x["length_bars"]) for x in c] == [(pid, 2.0, 3, 2.0)], c


# ---------------------------------------------------------------------------------------------------------------
# BF-7 a project with one (still empty) pattern: flp_edit_notes / flp_notes / flp_add_notes(pattern=None paths)
# asked to "say which pattern (patterns with notes: [])" although there is only one to say.
def test_single_empty_pattern_is_the_pattern():
    p = copy("one_channel_no_notes")
    r = m.flp_notes(p)
    assert r["count"] == 0, r
    m.flp_edit_notes(p, [{"op": "transpose", "semitones": 1}])
    try:
        m.flp_notes(copy("empty"))
        raise AssertionError("no patterns: must refuse")
    except ValueError as e:
        assert "no patterns" in str(e), e


# ---------------------------------------------------------------------------------------------------------------
# BF-8 fl_music.parse_note: a JSON number sent as 67.0 crashed with KeyError '6' (read as a note letter); '' and
# 'H4' crashed with IndexError / KeyError instead of saying what a note looks like.
def test_parse_note_numbers_and_errors():
    assert fl_music.parse_note(67.0) == 67 and fl_music.parse_note("Eb5") == 75 and fl_music.parse_note("c#-1") == 1
    for bad in ("", "H4", "C", "C#x"):
        try:
            fl_music.parse_note(bad)
            raise AssertionError(bad)
        except ValueError as e:
            assert "C4" in str(e), e
    p = copy("one_channel")
    pid = m.flp_patterns(p)[0]["pattern"]
    r = m.flp_add_notes(p, [{"note": 67.0, "bar": 1, "beat": 0, "beats": 1}], pattern=pid, channel=0)
    assert r["added"] == 1


# ---------------------------------------------------------------------------------------------------------------
# BF-9 flp_analyze_music / flp_diff labelled bars as 4/4 bars (4 * PPQ) in every song: in 3/4 the chords were
# measured over 4 beats and named at the wrong bar numbers.
def test_analyze_music_bars_in_three_four():
    p = three_four()
    d = m._load(p); P = m._ppq(d)
    notes = m._song(d, m._events(d))[0]
    r = m.flp_analyze_music(p)
    end = max(n[0] + n[1] for n in notes)
    assert abs(r["bars"] - round(end / (3 * P), 2)) < 0.01, (r["bars"], end / (3 * P))
    assert all(c.split(":")[0].split(".")[0].isdigit() for c in r["chords"])



def set_current_arrangement(p, idx):
    d = m._load(p); ev = m._events(d)
    k = next(k for k, e in enumerate(ev) if e["id"] == m.EV_CURRENT_ARRANGEMENT and e["len"] == 2)
    m._save(m._splice(d, ev, [(k, k + 1, bytes([m.EV_CURRENT_ARRANGEMENT]) + struct.pack("<H", idx))]), p)


# ---------------------------------------------------------------------------------------------------------------
# BF-10 flp_pattern_tools remove_empty (also run by flp_write_notes into a fresh song) removed empty patterns that
# weren't placed in the CURRENT arrangement - a pattern placed only in another arrangement lost its pattern and that
# arrangement was left with clips of a pattern that no longer exists.
def test_remove_empty_keeps_patterns_of_other_arrangements():
    p = copy("arrangements_3")
    pid = m.flp_new_pattern(p, "Fill")["pattern"]
    set_current_arrangement(p, 1)
    m.flp_playlist(p, "place", pattern=pid, bar=5, track=4, length_bars=1)
    set_current_arrangement(p, 0)
    r = m.flp_pattern_tools(p, "remove_empty")
    assert pid not in r["removed"], r
    assert pid in {x["pattern"] for x in m.flp_patterns(p)}



# ---------------------------------------------------------------------------------------------------------------
# BF-11 midi_read dropped zero-length notes (note-on and note-off on the same tick, as some drum exporters write
# hits): offs sort before ons, so the off closed nothing and the on was never closed - the hit vanished from
# flp_write_notes / flp_replace_part / sheet imports.
def test_midi_zero_length_notes_kept():
    mid = os.path.join(tmp, "zero.mid")
    # written by hand, offs first at each tick, as those exporters do (midi_write now puts a 0-length note's off a
    # tick after its on, so it can't make such a file any more)
    body = bytes([0, 0x80, 36, 0, 0, 0x90, 36, 100, 96, 0x90, 36, 90, 96, 0x80, 36, 0, 0, 0x80, 36, 0,
                  0, 0x90, 36, 80, 0, 0xFF, 0x2F, 0])
    open(mid, "wb").write(struct.pack(">4sIHHH", b"MThd", 6, 1, 1, 96) + b"MTrk" + struct.pack(">I", len(body)) + body)
    div, notes, _ = m.midi_read(mid)
    assert sorted(notes) == [(0, 0, 36, 100), (96, 192, 36, 90), (192, 192, 36, 80)], notes



# ---------------------------------------------------------------------------------------------------------------
# BF-12 sheet_import into an empty project: the tempo map ([bar, bpm] in the score's meter) was written BEFORE the
# project's time signature was set, so flp_tempo_write read the bars as 4/4 bars - a 3/4 waltz's tempo change at
# bar 3 landed at FL's bar 3.67.
def test_sheet_import_tempo_changes_in_score_meter():
    import flh_sheet as SH, flh_store, flh_transcribe
    flh_store.register(ns); flh_transcribe.register(ns); SH.register(ns)
    p = copy("one_channel_no_notes")
    abc = "X:1\nT:Waltz\nM:3/4\nL:1/4\nQ:1/4=100\nK:C\nCDE|FGA|[Q:1/4=140]Bcd|cBA|]\n"
    r = SH.sheet_import(abc=abc, project=p, channel=0)
    assert r["written"], r
    assert [x["bar"] for x in m.flp_tempo_map(p)["map"]] == [1.0, 3.0], m.flp_tempo_map(p)



# ---------------------------------------------------------------------------------------------------------------
# BF-13 flp_write_arrangement crashed on valid section lists: none ([] -> IndexError), a last section that starts
# after the last note (an outro marker -> negative clip length, struct.error), and two sections on one bar wrote a
# zero-length clip.
def test_write_arrangement_section_edges():
    mid = os.path.join(tmp, "four_bars.mid")
    m.midi_write(mid, 96, [("x", [(0, bytes([0x90, 60, 100])), (16 * 96, bytes([0x80, 60, 0]))])])
    for secs, want in (([], [(1.0, 4.0)]), ([{"name": "A", "bar": 1}, {"name": "Outro", "bar": 9}], [(1.0, 8.0), (9.0, 1.0)]),
                       ([{"name": "A", "bar": 1}, {"name": "B", "bar": 1}], [(1.0, 4.0)])):
        t = copy("one_channel"); out = os.path.join(os.path.dirname(t), "out.flp")
        r = m.flp_write_arrangement(t, out, [{"channel": 0, "midi": mid}], secs)
        assert [(x["bar"], x["length_bars"]) for x in r["patterns"]] == want, (secs, r["patterns"])
        assert all(c["length_bars"] > 0 for c in clips(out)), clips(out)



# ---------------------------------------------------------------------------------------------------------------
# BF-14 one target listed twice wrote its event twice: flp_style_channels([{channel 0, name}, {channel 0, name}])
# left two name events in the channel (flp_list showed the first, FL the last); flp_copy_state with two copies into
# one target channel wrote two plugin states into it (a corrupt channel).
def test_duplicate_targets_not_written_twice():
    p = copy("base")
    m.flp_style_channels(p, p, [{"channel": 0, "name": "First"}, {"channel": 0, "name": "Second", "color": "#203040"}])
    d = m._load(p); ev = m._events(d); a, b = m._channel_span(d, ev, 0)
    names = [m._text16(m._payload(d, ev[k])) for k in range(a, b) if ev[k]["id"] == m.EV_DISPLAY_NAME]
    assert names.count("Second") == 1 and "First" not in names, names
    assert next(c for c in m.flp_list(p)["channels"] if c["index"] == 0)["name"] == "Second"
    src = copy("base"); before = sha(p)
    try:
        m.flp_copy_state(src, p, p, channels=[{"from": 0, "to": 1}, {"from": 1, "to": 1}], force=True)
        raise AssertionError("two copies into one channel must be refused")
    except ValueError:
        pass
    assert sha(p) == before



# ---------------------------------------------------------------------------------------------------------------
# BF-15 flp_mixer volume_db / pan on a track whose value isn't in the file's mixer parameters: nothing was written
# and the result still listed the track as done.
def test_mixer_volume_not_written_is_an_error():
    p = copy("base")
    d = m._load(p); ev = m._events(d)
    kp = next(k for k, e in enumerate(ev) if e["id"] == m.EV_MIXER_PARAMS); q = m._payload(d, ev[kp])
    code = 0x7000 + (1 << 6)
    keep = b"".join(q[i:i + 12] for i in range(0, len(q) - 11, 12)
                    if not (struct.unpack("<H", q[i + 6:i + 8])[0] == code and q[i + 4] == m.MIX_VOLUME))
    m._save(m._splice(d, ev, [(kp, kp + 1, m._data_event(m.EV_MIXER_PARAMS, keep))]), p)
    before = sha(p)
    try:
        m.flp_mixer(p, p, [{"track": 1, "volume_db": -6}])
        raise AssertionError("a volume that can't be written must not be reported as done")
    except ValueError as e:
        assert "volume" in str(e), e
    assert sha(p) == before
    m.flp_mixer(p, p, [{"track": 2, "volume_db": -6}])            # a track that has it still works
    assert abs(m._track_volume_db(m._load(p), 2) + 6) < 0.05



# ---------------------------------------------------------------------------------------------------------------
# BF-16 flp_tempo_write took any BPM: [[1, 0]] wrote a 0 BPM project tempo (flp_set_tempo refuses outside FL's
# 10-522), and a point before bar 1 crashed with a struct error instead of saying so.
def test_tempo_write_checks_values():
    p = copy("base"); before = sha(p)
    for pts in ([[1, 0]], [[1, 120], [3, 2000]], [[0, 120]]):
        try:
            m.flp_tempo_write(p, p, points=pts)
            raise AssertionError(pts)
        except ValueError as e:
            assert "10-522" in str(e), e
    assert sha(p) == before



# ---------------------------------------------------------------------------------------------------------------
# BF-17 sheet_notation.to_midi (sheet_import output_mid): a tempo under 3.58 BPM (Q:1/4=1, a misread digit) crashed
# with OverflowError packing MIDI's 3-byte tempo.
def test_sheet_to_midi_slow_tempo():
    import sheet_notation as S
    sc = S.parse_abc("X:1\nM:4/4\nL:1/4\nQ:1/4=1\nK:C\nCDEF|]\n")
    r = S.to_midi(sc)
    assert r["notes"] == 4 and r["bytes"][:4] == b"MThd", r



# ---------------------------------------------------------------------------------------------------------------
# BF-18 sheet_notation: a MusicXML note with a negative <duration> (a misread digit) put a note before the song's
# start and to_midi (sheet_import output_mid) looped forever in _wvlq (-1 >> 7 == -1) - a hung tool call burning a
# CPU core; <beats>0</beats> or M:0/4 crashed to_inline with ZeroDivisionError. All are now clear ValueErrors.
def test_sheet_bad_numbers_dont_hang():
    import signal, sheet_notation as S
    xml = ('<?xml version="1.0"?><score-partwise version="4.0"><part-list><score-part id="P1"><part-name>A'
           '</part-name></score-part></part-list><part id="P1"><measure number="1"><attributes><divisions>2'
           '</divisions><time><beats>{beats}</beats><beat-type>4</beat-type></time></attributes>'
           '<note><pitch><step>C</step><octave>4</octave></pitch><duration>{dur}</duration></note>'
           '<note><pitch><step>D</step><octave>4</octave></pitch><duration>2</duration></note></measure></part>'
           '</score-partwise>')
    def boom(*a):
        raise AssertionError("hung")
    alarm = hasattr(signal, "SIGALRM")                             # (no SIGALRM on Windows: no watchdog there)
    old = signal.signal(signal.SIGALRM, boom) if alarm else None
    if alarm:
        signal.alarm(10)
    try:
        for beats, dur in (("4", "-4"), ("0", "2")):
            try:
                sc = S.parse_musicxml(xml.format(beats=beats, dur=dur)); S.to_inline(sc); S.to_midi(sc)
                raise AssertionError((beats, dur))
            except ValueError:
                pass
        sc = S.parse_abc("X:1\nM:0/4\nL:1/4\nK:C\nCDEF|]\n"); S.to_inline(sc); S.to_midi(sc)
        try:
            m.midi_write(os.path.join(tmp, "neg.mid"), 96, [("x", [(-5, bytes([0x90, 60, 100]))])])
            raise AssertionError("negative tick")
        except ValueError:
            pass
    finally:
        if alarm:
            signal.alarm(0); signal.signal(signal.SIGALRM, old)



# ---------------------------------------------------------------------------------------------------------------
# BF-19 flp_export_midi: the 25th exported channel got MIDI channel 10 (GM drums), so a melodic part played as
# percussion in any GM player / DAW import.
def test_export_midi_never_uses_drum_channel():
    p = copy("one_channel"); out = os.path.join(os.path.dirname(p), "many.mid")
    real = m._song
    m._song = lambda d, ev: ([(i * 96, 96, ch, 60, 100) for i, ch in enumerate(range(30))], [(0, 120.0)])
    try:
        m.flp_export_midi(p, out)
    finally:
        m._song = real
    data = open(out, "rb").read(); used = set(); i = 14
    while i < len(data):
        L = struct.unpack(">I", data[i + 4:i + 8])[0]; t = data[i + 8:i + 8 + L]; i += 8 + L; j = 0; run = None
        while j < len(t):
            _, j = m._vlq(t, j); b = t[j]
            if b == 0xFF:
                ln, j = m._vlq(t, j + 2); j += ln
            else:
                if b & 0x80:
                    run = b; j += 1
                if run & 0xF0 == 0x90:
                    used.add(run & 0x0F)
                j += 2
    assert 9 not in used and len(used) == 15, used



# ---------------------------------------------------------------------------------------------------------------
# BF-20 flp_arrange took any numbers: delete_bars from 5 to 3 (swapped) moved every later clip 2 bars LATER and
# reported done; from 0 crashed with a struct error. Now refused with what's expected, the file untouched.
def test_arrange_refuses_backwards_ranges():
    p = copy("one_channel"); before = sha(p)
    for op in ({"op": "delete_bars", "from": 5, "to": 3}, {"op": "delete_bars", "from": 0, "to": 2},
               {"op": "insert_bars", "at": 2, "bars": 0}, {"op": "duplicate", "from": 3, "to": 3}):
        try:
            m.flp_arrange(p, [op])
            raise AssertionError(op)
        except ValueError as e:
            assert "bars start at 1" in str(e), e
    assert sha(p) == before



# ---------------------------------------------------------------------------------------------------------------
# BF-21 flp_set_song_length on a clip that starts later into its pattern (start offset s > 0) wrote its end offset
# as the new length alone (end < start + length; flp_playlist resize writes s + length).
def test_set_song_length_keeps_clip_window():
    p = copy("one_channel"); d = m._load(p); ev = m._events(d); P = m._ppq(d)
    k = m._clip_event(d, ev); r = bytearray(m._payload(d, ev[k])); ln, = struct.unpack("<I", r[8:12])
    r[24:32] = struct.pack("<II", P, P + ln)
    m._save(m._splice(d, ev, [(k, k + 1, m._data_event(m.EV_PLAYLIST, bytes(r)))]), p)
    m.flp_set_song_length(p, 8)
    d = m._load(p); ev = m._events(d); r = m._payload(d, ev[m._clip_event(d, ev)])
    assert m._clip_offsets(r) == (P, P + 8 * 4 * P), m._clip_offsets(r)



# ---------------------------------------------------------------------------------------------------------------
# BF-2b insert_bars at a time-signature change: every later marker (and clip) must sit exactly `bars` song bars later.
def test_arrange_insert_at_meter_change_keeps_bar_numbers():
    p = copy("timesig_markers")
    d = m._load(p); ev = m._events(d); P = m._ppq(d); meters = m._meter_map(d, ev)
    sig = [x for x in meters if x[0] > 0][0]
    at_bar = m._tick_to_bar(sig[0], meters, P)
    before = [(x["name"], m._tick_to_bar(x["tick"], meters, P)) for x in m._markers(d, ev)]
    m.flp_arrange(p, [{"op": "insert_bars", "at": at_bar, "bars": 2}])
    d = m._load(p); ev = m._events(d); meters2 = m._meter_map(d, ev)
    after = [(x["name"], m._tick_to_bar(x["tick"], meters2, P)) for x in m._markers(d, ev)]
    want = [(nm, b + 2 if b >= at_bar else b) for nm, b in before]
    assert after == want, (before, after)


# ---------------------------------------------------------------------------------------------------------------
# BF-23 flp_arrange delete_bars removed every automation clip that started inside the cut, even a whole-song volume
# curve that runs on long after it (deleting a silent bar 1 lost the song's dynamics). It is now kept: it starts at
# the cut and its curve picks up where the cut ends.
def test_delete_bars_keeps_automation_that_runs_past_the_cut():
    p = copy("one_channel")
    r = m.flp_add_automation(p, p, [{"bar": 1, "value": 0.2}, {"bar": 3, "value": 0.6}, {"bar": 5, "value": 1.0}])
    ch = r["channel"]
    m.flp_arrange(p, [{"op": "delete_bars", "from": 1, "to": 3}])
    d = m._load(p); ev = m._events(d); P = m._ppq(d)
    k, size, recs = m._playlist_at(d, ev)
    auto = [x for x in recs if struct.unpack("<H", x[6:8])[0] == ch]
    assert len(auto) == 1, "the automation clip was removed"
    assert struct.unpack("<I", auto[0][0:4])[0] == 0
    pts, _, _ = m._auto_points(m._payload(d, ev[m._automation_events(d, ev)[ch]]))
    assert abs(pts[0][1] - 0.6) < 1e-6 and abs(pts[-1][1] - 1.0) < 1e-6, pts
    assert abs((pts[-1][0] - pts[0][0]) - 8) < 1e-6, pts           # bar 3 -> 5 is still 2 bars


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
