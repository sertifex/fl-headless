"""Note / pattern / playlist writers (2.2): compose_write place_mode (an already placed pattern moves instead of playing
twice), replace_part into='covering_clip' / tempo lanes / jitter / grace notes / hidden notes, write_notes on
arranged songs (clear_song), MIDI time signatures and whole tempo maps, one playlist track for sections, flp_meter,
flp_new_from, flp_patterns_set, compose_drop unplace, and batching through flp_edit.
Run: python3 tests/test_note_writers.py (no FL needed)."""
import hashlib, os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_nw_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402
import flh_compose as C  # noqa: E402
m.fl_running = lambda: False
m._with_open_project = lambda project, output, fn, _pending_ok=True: fn()
CORPUS = os.path.join(HERE, "corpus")
_n = [0]


def copy(name):
    _n[0] += 1
    d = os.path.join(tmp, f"p{_n[0]}"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".flp"); shutil.copy(os.path.join(CORPUS, name + ".flp"), p)
    return p


def sha(p):
    return hashlib.sha1(open(p, "rb").read()).hexdigest()


def clips(p, name=None):
    return [c for c in m.flp_playlist(p)["clips"] if name is None or c["name"] == name]


def pats(p):
    d = m._load(p); return m._patterns(d, m._events(d))


def pid_of(p, name):
    return next(k for k, v in pats(p).items() if v["name"] == name)


P = m._ppq(m._load(os.path.join(CORPUS, "base.flp")))
BAR = 4 * P

# ---- registration: the new writers batch through flp_edit and queue in lazy mode
for t in ("flp_meter", "flp_patterns_set"):
    assert t in m.TOOLS and m._OPEN_PROJECT_GUARD[t] == ("project", "output") and t in m.LAZY_TOOLS, t
    assert m.TOOLS[t][0].__wrapped__ is getattr(m, t)
assert "flp_new_from" in m.TOOLS and "flp_new_from" in m.NOT_READERS
for t, args in (("flp_replace_part", ("into", "hidden")), ("flp_write_notes", ("meter", "clear_song")),
                ("flp_compose_write", ("place_mode",)), ("flp_compose_drop", ("unplace",))):
    for a in args:
        assert a in m.TOOLS[t][2]["properties"], (t, a)
print("registration ok")

# ---- 1. compose_write place=[..] on a pattern already placed: its clip moves / resizes (no second copy)
p = copy("base")
r = C.flp_compose_write(p, p, "B Theme", notes=[[0, 0, 4, 60, 100, 0]], place=[[3, 4]], track=6)
assert r["created"] and r["placed_at"] == [3.0], r
r = C.flp_compose_write(p, p, "B Theme", place=[[3, 2]])
c = clips(p, "B Theme")
assert len(c) == 1 and c[0]["bar"] == 3.0 and c[0]["length_bars"] == 2.0 and c[0]["track"] == 6, c
assert r["clips_moved"] == 1 and r["place_mode"] == "move" and r["track"] == 6, r
C.flp_compose_write(p, p, "B Theme", place=[[9, 8]])            # elsewhere: moved, not doubled
c = clips(p, "B Theme")
assert len(c) == 1 and c[0]["bar"] == 9.0 and c[0]["length_bars"] == 8.0, c
r = C.flp_compose_write(p, p, "B Theme", place=[[17, 4]], place_mode="add")
c = clips(p, "B Theme")
assert [x["bar"] for x in c] == [9.0, 17.0] and r["clips_added"] == 1, (c, r)
r = C.flp_compose_write(p, p, "B Theme", place=[[9, 4], [13, 4], [21, 2]])   # 2 reused, 1 added
c = clips(p, "B Theme")
assert [(x["bar"], x["length_bars"]) for x in c] == [(9.0, 4.0), (13.0, 4.0), (21.0, 2.0)], c
assert r["clips_moved"] == 2 and r["clips_added"] == 1, r
r = C.flp_compose_write(p, p, "B Theme", place=[[5, 4]], place_mode="replace")
c = clips(p, "B Theme")
assert [(x["bar"], x["track"]) for x in c] == [(5.0, 6)] and r["clips_removed"] == 3, (c, r)
try:
    C.flp_compose_write(p, p, "B Theme", place=[[5, 4]], place_mode="twice")
    raise AssertionError("bad place_mode accepted")
except ValueError as e:
    assert "place_mode" in str(e)
# only the playlist changed: the pattern's notes are the same
assert [n[4] for n in pats(p)[pid_of(p, "B Theme")]["notes"]] == [60]
print("compose_write place_mode ok")

# ---- compose_drop unplace: placed patterns go with their clips when asked
r = C.flp_compose_drop(p, p, patterns=["B Theme"])
assert r["removed"] == [] and r["kept"], r
r = C.flp_compose_drop(p, p, patterns=["B Theme"], unplace=True)
assert r["removed"] == ["B Theme"] and r["clips_removed"] == 1 and not clips(p, "B Theme"), r
print("compose_drop unplace ok")


# ---- 2. replace_part: sections without the channel, tempo lane, stacked clips, jitter, grace notes, hidden notes
def song():
    """Sections A (bars 1-4) and B (5-8) on track 1 holding channel 0; a tempo lane over the whole song."""
    q = copy("one_channel_no_notes")
    C.flp_compose_write(q, q, "A", notes=[[0, 0, 1, 60, 100, 0], [0, 8, 1, 62, 100, 0]], place=[[1, 4]], track=1)
    C.flp_compose_write(q, q, "B", notes=[[0, 0, 1, 64, 100, 0], [0, 4, 1, 65, 100, 0]], place=[[5, 4]], track=1)
    m.flp_tempo_write(q, q, points=[[1, 90], [5, 100]])
    return q


q = song()
assert "Tempo" in {v["name"] for v in pats(q).values()} and len(clips(q)) == 3
r = m.flp_replace_part(q, q, 1, notes=[[1.5, 0.25, 70, 90], [6, 0.5, 72, 90]])   # channel 1 is nowhere yet
ps = pats(q)
assert [n[4] for n in ps[pid_of(q, "A")]["notes"] if n[2] == 1] == [70], ps
assert [n[4] for n in ps[pid_of(q, "B")]["notes"] if n[2] == 1] == [72], ps
assert not ps[pid_of(q, "Tempo")]["notes"], "a part's notes went into the tempo lane"
assert r["verified"]["asked_not_heard"] == 0 and r["verified"]["heard_not_asked"] == 0, r
# humanize jitter a few ticks before B's start: B gets it, silently (no mismatch, no noise key)
early = 1 + (4 * BAR - 3) / BAR
r = m.flp_replace_part(q, q, 0, notes=[[1, 1, 60, 100], [early, 1, 64, 100], [6, 1, 65, 100]])
assert "moved_to_clip_start" not in r and "grace_notes_moved" not in r, r
assert r["verified"]["asked_not_heard"] == 0 and r["verified"]["heard_not_asked"] == 0, r
assert sorted(n[0] for n in pats(q)[pid_of(q, "B")]["notes"] if n[2] == 0 and n[4] == 64) == [0]
# a grace note (over before B starts) stays in A, which plays it before the downbeat as written
grace = 1 + (4 * BAR - 6) / BAR
r = m.flp_replace_part(q, q, 0, notes=[[1, 1, 60, 100], [grace, 4 / BAR, 63, 100], [5, 1, 64, 100]])
assert "grace_notes_moved" not in r and r["verified"]["asked_not_heard"] == 0, r
assert [n[0] for n in pats(q)[pid_of(q, "A")]["notes"] if n[4] == 63] == [4 * BAR - 6]
# ... in a gap before B (no clip plays it) it can only start with B: moved, and said
qg = song()
m.flp_playlist(qg, "resize", clips=[c["id"] for c in clips(qg, "A")], length_bars=3)
r = m.flp_replace_part(qg, qg, 0, notes=[[1, 1, 60, 100], [grace, 4 / BAR, 63, 100], [5, 1, 64, 100]])
assert r.get("grace_notes_moved") == 1 and r["grace_note_bars"] == [round(grace, 2)], r
# stacked clips that don't hold the channel: auto asks, covering_clip settles it (the latest-starting clip)
C.flp_compose_write(q, q, "Fill", notes=[[0, 0, 1, 40, 100, 0]], place=[[7, 2]], track=2)
try:
    m.flp_replace_part(q, q, 2, notes=[[7.5, 0.25, 50, 90]])
    raise AssertionError("stacked clips without the channel: auto should ask")
except ValueError as e:
    assert "covering_clip" in str(e), e
r = m.flp_replace_part(q, q, 2, notes=[[7.5, 0.25, 50, 90], [2, 0.25, 52, 90]], into="covering_clip")
ps = pats(q)
assert [n[4] for n in ps[pid_of(q, "Fill")]["notes"] if n[2] == 2] == [50], ps[pid_of(q, "Fill")]
assert [n[4] for n in ps[pid_of(q, "A")]["notes"] if n[2] == 2] == [52]
try:
    m.flp_replace_part(q, q, 2, notes=[[1, 1, 50, 90]], into="nearest")
    raise AssertionError("bad into accepted")
except ValueError as e:
    assert "into" in str(e)
# a shortened clip: the channel's notes past its end are junk -> removed (hidden='keep' leaves them)
q2 = song()
ida = next(c["id"] for c in clips(q2, "A"))
m.flp_playlist(q2, "resize", clips=[ida], length_bars=2)            # A's note at beat 8 (bar 3) is hidden now
q3 = q2.replace(".flp", "_keep.flp")
r = m.flp_replace_part(q2, q3, 0, notes=[[1, 1, 67, 100]], hidden="keep")
assert "hidden_removed" not in r and 62 in [n[4] for n in pats(q3)[pid_of(q3, "A")]["notes"]], r
r = m.flp_replace_part(q2, q2, 0, notes=[[1, 1, 67, 100]])
assert r["hidden_removed"] == 1 and [n[4] for n in pats(q2)[pid_of(q2, "A")]["notes"] if n[2] == 0] == [67], r
print("replace_part into / jitter / grace / hidden ok")

# ---- 3. write_notes: an arranged-song template, MIDI time signatures, whole tempo maps
q = song(); before = sha(q)
o = os.path.join(os.path.dirname(q), "new.flp")
try:
    m.flp_write_notes(q, o, [{"channel": 0, "notes": [[1, 1, 60, 100]]}])
    raise AssertionError("an arranged song template was overwritten silently")
except ValueError as e:
    assert "clear_song" in str(e) and "flp_new_from" in str(e), e
r = m.flp_write_notes(q, o, [{"channel": 0, "notes": [[1, 1, 60, 100], [3, 1, 62, 100]]}], clear_song=True)
assert r["notes_written"] == 2 and r["before"], r
assert sha(q) == before, "the template changed"
ps = pats(o)
assert len(ps) == 1 and sorted(n[4] for v in ps.values() for n in v["notes"]) == [60, 62], ps
assert [c["kind"] for c in clips(o)] == ["pattern"] and clips(o)[0]["bar"] == 1.0
assert len(m.flp_list(o)["channels"]) == len(m.flp_list(q)["channels"])


def midi_file(path, meters, tempos, notes, div=96):
    """meters [(tick, num, den)], tempos [(tick, bpm)], notes [(tick, ticks, key)]."""
    tr0 = [(t, b"\xff\x58\x04" + bytes([n, d.bit_length() - 1, 24, 8])) for t, n, d in meters]
    tr0 += [(t, b"\xff\x51\x03" + int(round(60_000_000 / b)).to_bytes(3, "big")) for t, b in tempos]
    tr1 = []
    for t, ln, k in notes:
        tr1 += [(t, bytes([0x90, k, 100])), (t + ln, bytes([0x80, k, 0]))]
    m.midi_write(path, div, [("tempo", tr0), ("piano", tr1)])
    return path


mid = midi_file(os.path.join(tmp, "waltz.mid"), [(0, 3, 4), (2 * 3 * 96, 6, 8)], [(0, 80), (3 * 96, 96)],
                [(0, 96, 60), (3 * 96, 96, 62), (6 * 96, 96, 64), (9 * 96, 96, 65)])
t1 = copy("one_channel")
o = os.path.join(os.path.dirname(t1), "waltz.flp")
r = m.flp_write_notes(t1, o, [{"channel": 0, "midi": mid}])
info = m.flp_info(o)
assert info["time_signature"] == "3/4", info
assert info["time_signature_changes"] == [{"bar": 3.0, "time_signature": "6/8"}], info
assert r["tempo_events"] == 2 and m.flp_tempo_map(o)["events"] == 2, r
assert any("time signature 3/4" in x for x in r["before"]), r
dd = m._load(o)
assert m._marker_layout_fix(dd)[1] == 0, "a marker sits where FL scrambles it"
r = m.flp_write_notes(t1, o, [{"channel": 0, "midi": mid}], meter="keep")
assert m.flp_info(o)["time_signature"] == "4/4" and "time_signature_changes" not in m.flp_info(o)
r = m.flp_write_notes(t1, o, [{"channel": 0, "notes": [[1, 1, 60, 100]]}], meter="7/8")
assert m.flp_info(o)["time_signature"] == "7/8"
# a fresh song (empty section patterns): the whole tempo map as a Tempo lane, sections on ONE track
t2 = copy("one_channel_no_notes")
m.flp_new_pattern(t2, "A"); m.flp_new_pattern(t2, "B")
mid2 = midi_file(os.path.join(tmp, "map.mid"), [], [(0, 70), (4 * 96, 75), (8 * 96, 82)],
                 [(i * 96, 96, 60 + i) for i in range(16)])
o2 = os.path.join(os.path.dirname(t2), "fresh.flp")
r = m.flp_write_notes(t2, o2, [{"channel": 0, "midi": mid2}])
tm = m.flp_tempo_map(o2)
assert tm["events"] == 3 and [x["bpm"] for x in tm["map"]] == [70.0, 75.0, 82.0], tm
assert "Tempo lane" in r.get("tempo_note", ""), r
o3 = os.path.join(os.path.dirname(t2), "arr.flp")
r = m.flp_write_arrangement(t2, o3, [{"channel": 0, "midi": mid2}], [{"name": "Verse", "bar": 1}, {"name": "Chorus", "bar": 3}])
secs = [c for c in clips(o3) if c["name"] in ("Verse", "Chorus")]
assert len(secs) == 2 and {c["track"] for c in secs} == {1}, secs
assert all(L["track"] == 1 for L in r["patterns"]), r
t3 = copy("one_channel")
o4 = os.path.join(os.path.dirname(t3), "arr1.flp")
r = m.flp_write_arrangement(t3, o4, [{"channel": 0, "midi": mid2}], [{"name": "Verse", "bar": 1}, {"name": "Chorus", "bar": 3}])
assert {c["track"] for c in clips(o4) if c["name"] in ("Verse", "Chorus")} == {1}, clips(o4)
print("write_notes clear_song / meter / tempo map / one track ok")

# ---- 4. flp_meter: the project's time signature, markers at later bars, changing and removing them
p = copy("base"); src_clips = clips(p)
r = m.flp_meter(p, p, "3/4")
assert r["written_as"] == "the project's time signature" and m.flp_info(p)["time_signature"] == "3/4", r
assert m.flp_project_info(p)["time_sig_num"] == 3
r = m.flp_meter(p, p, 6, 8, at_bar=5)
assert m.flp_info(p)["time_signature_changes"] == [{"bar": 5.0, "time_signature": "6/8"}], m.flp_info(p)
assert r["meter_map"][-1] == {"bar": 5.0, "time_signature": "6/8"}, r
r = m.flp_meter(p, p, 7, 8, at_bar=5)                             # the same bar: that marker changes, no second
d = m._load(p); mk = [x for x in m._markers(d, m._events(d)) if x["signature"]]
assert len(mk) == 1 and (mk[0]["num"], mk[0]["den"]) == (7, 8) and r["written_as"].startswith("the time-sig"), mk
assert m._marker_layout_fix(d)[1] == 0
r = m.flp_meter(p, p, at_bar=5, remove=True)
assert "time_signature_changes" not in m.flp_info(p) and r["written_as"] == "marker removed", r
for bad in ({"num": 3, "den": 5}, {"num": 17}, {"num": 3, "at_bar": 2.5}, {"num": 4, "at_bar": 1, "remove": True}):
    try:
        m.flp_meter(p, p, **bad)
        raise AssertionError(f"accepted {bad}")
    except ValueError:
        pass
assert len(clips(p)) == len(src_clips)                            # clips untouched
guarded = []                                                      # in place (no output): the open-project guard runs
real_wop = m._with_open_project
m._with_open_project = lambda project, output, fn, _pending_ok=True: guarded.append(output) or fn()
m.flp_meter(p, num=2, den=4); m.flp_patterns_set(p, patterns=[{"pattern": 1, "name": "P"}])
C.flp_compose_drop(p, patterns=["nothing here"])
m._with_open_project = real_wop
assert guarded == [p, p, p] and m.flp_info(p)["time_signature"] == "2/4", guarded
# a change that leaves later clips inside bars: said
p = copy("base")
m.flp_playlist(p, "place", pattern=1, bar=9, track=8, length_bars=1)
r = m.flp_meter(p, p, 3, 4, at_bar=5)
assert r.get("clips_off_bar_lines", 0) >= 1 and r["note"], r
# a project whose file has no time signature events: added after the project settings
p = copy("base"); d = m._load(p); ev = m._events(d)
k17 = [k for k, e in enumerate(ev) if e["id"] in (17, 18)][:2]
d = m._splice(d, ev, [(k, k + 1, b"") for k in k17]); m._save(d, p)
assert m.flp_project_info(p)["time_sig_num"] is None
m.flp_meter(p, p, 5, 4)
assert m.flp_info(p)["time_signature"] == "5/4" and m.flp_project_info(p)["time_sig_num"] == 5
# through flp_edit: one batch with the other writers
p = copy("base")
r = m.flp_edit(p, [{"tool": "flp_meter", "num": "3/4"}, {"tool": "flp_patterns_set", "patterns": [{"pattern": 1, "name": "Verse"}]},
                   {"tool": "flp_compose_write", "pattern": "Verse", "place": [[1, 2]]}])
assert r["ops"] == 3 and m.flp_info(p)["time_signature"] == "3/4" and pats(p)[1]["name"] == "Verse", r
assert len(clips(p, "Verse")) == 1 and clips(p, "Verse")[0]["length_bars"] == 2.0, clips(p, "Verse")
print("flp_meter ok")

# ---- 5. flp_new_from: the sounds and mixer of a song, an empty playlist, its own title; the source untouched
src = copy("timesig_markers"); before = sha(src)
r = m.flp_new_from(src, "Second Song", meter="4/4", tempo=96)
o = r["path"]
assert os.path.basename(o) == "Second Song.flp" and sha(src) == before, r
assert clips(o) == [] and m.flp_info(o)["clips"] == 0 and "markers" not in m.flp_info(o), m.flp_info(o)
ps = pats(o)
assert len(ps) == 1 and not any(v["notes"] for v in ps.values()), ps
assert m.flp_info(o)["time_signature"] == "4/4" and "time_signature_changes" not in m.flp_info(o)
assert r["tempo_bpm"] == 96 and r["title"] == "Second Song" and m.flp_project_info(o)["title"] == "Second Song"
assert r["kept"]["channels"] == len(m.flp_list(src)["channels"]), r
assert len(m.flp_list(o)["effects"]) == len(m.flp_list(src)["effects"])
assert r["left_out"]["clips"] > 0 and r["left_out"]["markers"] > 0, r
import flh_store  # noqa: E402
assert flh_store.journal_get(o).get("made_from") == os.path.abspath(src)
try:
    m.flp_new_from(src, "Second Song")
    raise AssertionError("an existing song was overwritten")
except FileExistsError:
    pass
try:
    m.flp_new_from(src, "x", keep="everything")
    raise AssertionError("bad keep accepted")
except ValueError:
    pass
# the new song takes notes, places its pattern, gets meter markers (its arrangement had no playlist event)
r = m.flp_meter(o, o, 3, 4, at_bar=3)
assert m.flp_info(o)["time_signature_changes"] == [{"bar": 3.0, "time_signature": "3/4"}]
r = m.flp_write_notes(o, o, [{"channel": 0, "notes": [[1, 1, 60, 100], [4, 1, 64, 100]]}], meter="keep")
assert r["notes_written"] == 2 and len(clips(o)) == 1 and clips(o)[0]["bar"] == 1.0, (r, clips(o))
assert m.flp_info(o)["time_signature_changes"] == [{"bar": 3.0, "time_signature": "3/4"}]
# from a song with audio clips, automation and several arrangements
for name in ("base", "arrangements_3", "automation_clips", "audio_clips"):
    s_ = copy(name); before = sha(s_)
    r = m.flp_new_from(s_, name + " new", folder=os.path.join(tmp, "new"))
    assert sha(s_) == before and m.flp_info(r["path"])["clips"] == 0, (name, r)
    d = m._load(r["path"]); ev = m._events(d)
    assert all(m._playlist_at(d, ev, a["index"])[2] == [] for a in m._arrangements(d, ev)), name
    assert len(m.flp_list(r["path"])["channels"]) == len(m.flp_list(s_)["channels"]), name
    m.flp_playlist(r["path"], "place", pattern=1, bar=2, track=1)  # the empty playlist takes clips again
    assert len(clips(r["path"])) == 1
print("flp_new_from ok")

# ---- 6. flp_patterns_set: rename / recolor offline
p = copy("base")
r = m.flp_patterns_set(p, p, [{"pattern": 1, "name": "Intro", "color": "#33445A"}])
assert pats(p)[1]["name"] == "Intro" and r["patterns"][0]["name"] == "Intro", r
r = m.flp_patterns_set(p, p, [{"pattern": "Intro", "name": "Verse"}])
assert pats(p)[1]["name"] == "Verse" and [c["name"] for c in clips(p) if c["kind"] == "pattern"] == ["Verse"]
d = m._load(p); ev = m._events(d)
g = next(g for g in m._pattern_groups(d, ev) if g[1] == 1 and g[0] == "pattern_meta")
cols = [m._payload(d, ev[k]) for k in range(g[2], g[3]) if ev[k]["id"] == 150]
assert cols == [bytes([0x33, 0x44, 0x5A, 0])], cols
# a pattern with notes but no name group gets one
q = copy("one_channel"); d = m._load(q); ev = m._events(d)
metas = [(a, b) for kind, _pid, a, b in m._pattern_groups(d, ev) if kind == "pattern_meta"]
if metas:
    d = m._splice(d, ev, [(a, b, b"") for a, b in metas]); m._save(d, q)
pid = next(iter(pats(q)))
m.flp_patterns_set(q, q, [{"pattern": pid, "name": "Hook"}])
assert pats(q)[pid]["name"] == "Hook" and pats(q)[pid]["notes"]
for bad in ([{"pattern": 99, "name": "x"}], [{"pattern": "Nope", "name": "x"}], [{"pattern": 1}], []):
    try:
        m.flp_patterns_set(p, p, bad)
        raise AssertionError(f"accepted {bad}")
    except ValueError:
        pass
print("flp_patterns_set ok")
print("ok")
