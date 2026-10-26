"""FLP format correctness (fixes FZ-01..19, C1/C2, M3-M5 from internal write-ups) on the corpus
(tests/corpus, tools/make_corpus.py). Run: python3 tests/test_flp_format.py"""
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, os.path.join(REPO, "tools"))
TMP = tempfile.mkdtemp(prefix="flh_fmt_")
os.environ["FLH_ROOTS"] = TMP
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="flh_fmt_state_")
import fl_headless_mcp as m  # noqa: E402
import make_corpus as mc  # noqa: E402

m.fl_running = lambda: False
C = os.path.join(HERE, "corpus")
PB = 0x5000
n_ = [0]


def cp(name):
    n_[0] += 1
    dst = os.path.join(TMP, f"{n_[0]:02d}_{name}.flp"); shutil.copy(os.path.join(C, name + ".flp"), dst)
    return dst


def out(tag):
    n_[0] += 1
    return os.path.join(TMP, f"{n_[0]:02d}_{tag}_out.flp")


def ids(path):
    return [e[0] for e in mc.parse(open(path, "rb").read())[3]]


def raises(fn, *words):
    try:
        fn()
    except ValueError as e:
        assert all(w in str(e) for w in words), e
        return str(e)
    raise AssertionError("no ValueError")


# ---- FZ-01..04: malformed files give clean errors
bad = os.path.join(TMP, "bad.flp")
for blob, why in ((b"FLhd", "short"), (b"FLhd" + struct.pack("<I", 0xFFFFFFFF) + bytes(20), "header"),
                  (open(os.path.join(C, "base.flp"), "rb").read()[:-3], "truncated")):
    open(bad, "wb").write(blob); m._forget_file(bad)
    for tool in (m.flp_info, m.flp_list, m.flp_patterns):
        raises(lambda: tool(bad))
d = bytearray(open(os.path.join(C, "base.flp"), "rb").read()); d[12:14] = b"\x00\x00"
open(bad, "wb").write(bytes(d)); m._forget_file(bad)
raises(lambda: m.flp_info(bad), "PPQ 0")
print("malformed files ok")

# ---- FZ-07: a new pattern's name group goes after the previous pattern's whole settings group
p = cp("base"); o = out("newpat")
g0 = next(g for g in m._pattern_groups(m._load(p), m._events(m._load(p))) if g[0] == "pattern_meta")
ev0 = m._events(m._load(p)); meta_ids = [ev0[k]["id"] for k in range(g0[2], g0[3])]
assert len(meta_ids) > 10 and 205 in meta_ids, meta_ids               # name, color, ..., time signature, length
r = m.flp_new_pattern(p, "New ü", output=o, place_at_bar=3, length_bars=2)
d1 = m._load(o); ev1 = m._events(d1)
groups = {(g[0], g[1]): [ev1[k]["id"] for k in range(g[2], g[3])] for g in m._pattern_groups(d1, ev1)}
assert groups[("pattern_meta", 1)] == meta_ids, groups[("pattern_meta", 1)]
assert groups[("pattern_meta", r["pattern"])] == [65, 193]
clip = next(c for c in m.flp_playlist(o)["clips"] if c.get("pattern") == r["pattern"])
assert clip["bar"] == 3.0 and clip["length_bars"] == 2.0, clip
print("pattern settings groups ok")

# ---- FZ-08: velocity 128 (FL's 100 %) on notes nobody touched survives an edit
p = cp("notes_edges"); o = out("vel")
d0 = m._load(p); pat0 = m._patterns(d0, m._events(d0))[1]["notes"]
assert any(r[11] == 128 for r in pat0)
m.flp_add_notes(p, [{"note": "C4"}], pattern=1, channel=0, output=o)
pat1 = m._patterns(m._load(o), m._events(m._load(o)))[1]["notes"]
assert sorted(r for r in pat0) == sorted(r for r in pat1 if not (r[2] == 0 and r[4] == 60 and r[0] == 0 and r[11] == 100)), \
    "untouched notes changed"
o2 = out("vel2")
m.flp_edit_notes(p, [{"op": "transpose", "semitones": 1}], output=o2, pattern=1, channel=0)
kept = [r for r in m._patterns(m._load(o2), m._events(m._load(o2)))[1]["notes"] if r[2] != 0]
assert sorted(kept) == sorted(r for r in pat0 if r[2] != 0)
print("velocity 128 kept ok")

# ---- FZ-09: style a channel with no name event: the name goes into its own block, the mixer is untouched
p = cp("unnamed_last_channel"); o = out("style")
fx0 = m.flp_list(p)["effects"]
m.flp_style_channels(p, o, [{"channel": 5, "name": "Renamed", "color": "#203040"}])
assert m.flp_list(o)["effects"] == fx0
d1 = m._load(o); ev1 = m._events(d1); a, b = m._channel_span(d1, ev1, 5)
assert any(ev1[k]["id"] == m.EV_DISPLAY_NAME and m._text16(m._payload(d1, ev1[k])) == "Renamed" for k in range(a, b))
print("style channels ok")

# ---- FZ-11: several arrangements: readers and writers use the current one
p = cp("arrangements_3")
info = m.flp_info(p); pl = m.flp_playlist(p)
assert info["clips"] == pl["count"] == 2 and len(info["arrangements"]) == 3, info
assert m.flp_playlist(p, arrangement=1)["count"] == 3 and m.flp_playlist(p, arrangement=2)["count"] == 0
assert info["notes_per_channel"] == m.flp_info(os.path.join(C, "base.flp"))["notes_per_channel"]
o = out("arr")
m.flp_playlist(p, "place", output=o, pattern=1, bar=90, track=4)
assert m.flp_playlist(o)["count"] == 3 and m.flp_playlist(o, arrangement=1)["count"] == 3
raises(lambda: m.flp_playlist(p, "remove", output=o, clips=[1], arrangement=1), "current")
print("arrangements ok")

# ---- FZ-12: FL 20/21 playlist records (32 / 60 bytes)
for name, n, size in (("legacy_playlist32", 11, 32), ("legacy_playlist60", 22, 60)):
    p = cp(name); d = m._load(p); ev = m._events(d)
    assert m._playlist_at(d, ev)[1] == size
    pl = m.flp_playlist(p)
    assert pl["count"] == n and m.flp_info(p)["clips"] == n and sorted(c["id"] for c in pl["clips"]) == list(range(1, n + 1))
    o = out(name)
    m.flp_playlist(p, "mute", output=o, clips=[2])
    a, b = m._playlist_at(d, ev)[2], m._playlist_at(m._load(o), m._events(m._load(o)))[2]
    assert len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1
    assert [c["muted"] for c in m.flp_playlist(o)["clips"]].count(True) == 1
print("legacy playlist layouts ok")

# ---- FZ-13: a pattern with two notes events: an edit writes one, no duplicates
p = cp("split_notes"); o = out("split")
before = len(m._patterns(m._load(p), m._events(m._load(p)))[1]["notes"])
m.flp_add_notes(p, [{"note": "C4"}], pattern=1, channel=0, output=o)
d1 = m._load(o); assert len(m._patterns(d1, m._events(d1))[1]["notes"]) == before + 1
assert len(m._pattern_note_events(d1, m._events(d1))[1]) == 1
print("split notes ok")

# ---- FZ-16 / FZ-18 / FZ-19: track 500, clip id 0xFFFFFFFF, pattern id 0xFFFF
p = cp("track_500"); o = out("t500")
r = m.flp_new_pattern(p, "X", output=o, place_at_bar=2)
assert 1 <= r["playlist_track"] <= 500, r
raises(lambda: m.flp_new_pattern(p, "Y", output=o, place_at_bar=2, playlist_track=501), "track 501")
assert m._next_clip_id([5, 0xFFFFFFFF]) == 1 and m._next_clip_id([3, 7]) == 8
assert m._new_pattern_id({1: 0, 0xFFFF: 0}) == 2 and m._new_pattern_id({1: 0, 2: 0}) == 3
print("bounds ok")

# ---- FZ-05: MIDI export with FL's top keys and odd tempos
p = cp("notes_edges"); mid = os.path.join(TMP, "x.mid")
r = m.flp_export_midi(p, mid)
assert r.get("warnings") and all(k <= 127 for s, e, k, v in m.midi_notes(mid)[1])
print("midi export ok")

# ---- FZ-10 / M5: bars follow the time signature
p = cp("timesig_markers"); o = out("audio")
wav = mc.wav(os.path.join(TMP, "tone.wav"), 0.5)
r = m.flp_add_audio_clip(p, o, wav, bar=3, name="Clip")
c = next(c for c in m.flp_playlist(o)["clips"] if c.get("channel") == r["channel"])
assert c["bar"] == 3.0, c
assert m._bar_to_tick(m._tick_to_bar(12345, m._meter_map(m._load(p), m._events(m._load(p))), 96),
                      m._meter_map(m._load(p), m._events(m._load(p))), 96) == 12345
print("time signatures ok")

# ---- FZ-15: flp_edit batches flp_project_info
p = cp("base"); o = out("batch")
m.flp_edit(p, [{"tool": "flp_project_info", "title": "Batch é"}], output=o)
assert m.flp_project_info(o)["title"] == "Batch é"
raises(lambda: m.flp_edit(p, [{"tool": "flp_project_info", "nonsense": 1}], output=o), "nonsense")
print("edit batch ok")

# ---- flp_collect_samples: same-name samples stay apart, FL's factory samples stay referenced
p = cp("missing_samples")
s1 = os.path.join(TMP, "a", "kick.wav"); s2 = os.path.join(TMP, "b", "kick.wav")
os.makedirs(os.path.dirname(s1)); os.makedirs(os.path.dirname(s2))
mc.wav(s1, 0.2); mc.wav(s2, 0.3)
proj = mc.Proj(open(p, "rb").read())
paths = [s1, s2]
for c_ in proj.chans:
    for e in c_:
        if e[0] == 196 and paths and m._text16(e[1]).startswith("C:\\Nowhere"):
            e[1] = mc.t16(paths.pop(0))
victim = [c_ for c_ in proj.chans if any(e[0] == 196 and m._text16(e[1]) == "Samples\\relative.wav" for e in c_)]
for e in victim[0]:
    if e[0] == 196:
        e[1] = mc.t16(s2)
open(p, "wb").write(proj.bytes()); m._forget_file(p)
o = os.path.join(TMP, "collect", "song.flp"); os.makedirs(os.path.dirname(o))
r = m.flp_collect_samples(p, output=o)
assert sorted(r["collected"]) == ["kick (2).wav", "kick.wav"] and r["factory_samples_kept"] == 1, r
print("collect samples ok")

# ---- C1: flp_replace_part puts a channel's notes into the layer that holds it (stacked clips, repeats)
p = cp("base"); song = out("layers")
shutil.copy(p, song)
for i, (nm, ch, key) in enumerate((("Sec Drums", 0, 36), ("Sec Bass", 2, 40), ("Sec Keys", 3, 60))):
    r = m.flp_new_pattern(song, nm, output=song, place_at_bar=81, length_bars=4, playlist_track=10 + i)
    m.flp_playlist(song, "place", output=song, pattern=r["pattern"], bar=85, track=10 + i, length_bars=4)
    m.flp_add_notes(song, [{"note": key + j, "bar": 1 + j, "beat": 0, "beats": 1, "slide": ch == 2 and j == 1}
                           for j in range(4)], pattern=r["pattern"], channel=ch, output=song)
d0 = m._load(song); ev0 = m._events(d0)
pid = {v["name"]: k for k, v in m._patterns(d0, ev0).items()}
assert any(n[5] for n in m._song_x(d0, ev0)[0] if n[2] == 2), "slide flag read"
o = out("replace")
r = m.flp_replace_part(song, o, 2, notes=[[81, 1, 41, 90], [82, 1, 43, 91, {"slide": True}], [86, 1, 45, 92]])
d1 = m._load(o); ev1 = m._events(d1); pats = m._patterns(d1, ev1)
assert sorted(n[4] for n in pats[pid["Sec Bass"]]["notes"] if n[2] == 2) == [41, 43], pats[pid["Sec Bass"]]["notes"]
assert all(n[2] != 2 for n in pats[pid["Sec Drums"]]["notes"] + pats[pid["Sec Keys"]]["notes"])
assert r["verified"]["read_back"] and r["in_repeated_patterns_skipped"] == 1 and r["repeated_patterns"], r
assert sum(1 for n in pats[pid["Sec Bass"]]["notes"] if n[1] & m.NOTE_SLIDE) == 1
s0, s1_ = m._song(d0, ev0)[0], m._song(d1, ev1)[0]
assert [n for n in s0 if n[2] != 2] == [n for n in s1_ if n[2] != 2]
# an fl field carry-over: [flags, group, fine, x7, release, midi_ch, pan, mod_x, mod_y]
o = out("replace_fl")
m.flp_replace_part(song, o, 2, notes=[{"tick": 80 * 4 * 96, "ticks": 96, "key": 50, "vel": 128,
                                       "fl": [16384 | 8, 0, 100, 0, 5, 0, 20, 10, 200], "slide": True}])
rec = next(n for n in m._patterns(m._load(o), m._events(m._load(o)))[pid["Sec Bass"]]["notes"] if n[4] == 50)
assert rec[1] & 8 and rec[6] == 100 and rec[10] == 20 and rec[11] == 128 and rec[12] == 10 and rec[13] == 200, rec
print("replace part ok")
print("ok")
