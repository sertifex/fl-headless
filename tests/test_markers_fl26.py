"""Playlist markers and playlist track names in FL 2026's layout (PC sweep 2026-10-23, files.markers_meter and
newsong.playlist_names failed). FL's own save showed:
  - markers written between the arrangement header (99 241 36) and its playlist event (233) came back as markers of
    the LAST PATTERN (inside its 65 .. 164 group): the playlist had none;
  - FL writes a marker as 148 33 34 46 45 168 101 205 and a playlist track as 238 43 [239 name] (the name last).
`fl_model_save` below models that (only what the evidence showed). Checks: the old placement reproduces the failure;
every writer now puts markers where FL keeps them; the readers read FL's own groups; group-aware rewrites leave no
orphan events. Run: python3 tests/test_markers_fl26.py (no FL needed)."""
import os, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import flh_newsong as N, flh_pccheck as PC, flh_realism as R  # noqa: E402
ns = vars(m)
for mod in (N, PC, R):
    mod.H = ns
BASE = os.path.join(HERE, "data", "fl2026_empty.flp")            # FL 2026's empty template (FL-saved)
if not os.path.exists(BASE):              # FL's own file can't ship: this test needs its exact layout (optional)
    print("skipped: tests/data/fl2026_empty.flp (FL 2026's empty template, private fixture) is not here")
    print("ok"); sys.exit(0)
d0 = m._load(BASE)
P = m._ppq(d0)


def ids(d, a=None, b=None):
    ev = m._events(d)
    return [e["id"] for e in ev[a:b]]


def fl_model_save(d):
    """What FL 2026's load + save did to the evidence files: marker groups of an arrangement BEFORE its 233 move to
    the last pattern's group; markers after it stay, rewritten as FL's full group; tracks become 238 43 [239];
    marker property events without a 148 of their own (orphans an old rewrite left) become one more marker each,
    named by their 205, at the position of the LAST 148 FL read (PC 2026-10-23: every marker at bar 56.75 with an
    older version's names)."""
    ev = m._events(d); a = m._arrangement(d, ev)
    edits, moved = [], b""
    last_pos = None
    for k in range(a["first"] + 1, a["end"]):
        if ev[k]["id"] == 148:
            last_pos = m._payload(d, ev[k])
    k = a["first"] + 1
    while k < a["end"]:
        e = ev[k]
        if e["id"] in (46, 45, 168, 101, 205, 33, 34):              # an orphan: FL makes it a marker at last_pos
            j = k
            while j < a["end"] and ev[j]["id"] in (46, 45, 168, 101, 33, 34):
                j += 1
            if j < a["end"] and ev[j]["id"] == 205 and last_pos is not None:
                full = (bytes([148]) + last_pos + bytes([33, 4, 34, 4, 46, 0, 45, 0, 168]) + b"\0" * 4
                        + bytes([101]) + b"\0\0" + m._raw(d, ev[j]))
                edits.append((k, j + 1, full)); k = j + 1
                continue
        if e["id"] == 148:
            j = m._marker_group_end(ev, k, a["end"])
            grp = {ev[i]["id"]: m._payload(d, ev[i]) for i in range(k, j)}
            full = (bytes([148]) + grp[148] + bytes([33, (grp.get(33) or b"\x04")[0], 34, (grp.get(34) or b"\x04")[0],
                                                  46, 0, 45, 0]) + bytes([168]) + b"\0" * 4 + bytes([101]) + b"\0\0"
                    + m._data_event(205, grp.get(205) or b"\0\0"))
            if k < a["playlist"]:
                moved += full; edits.append((k, j, b""))
            else:
                edits.append((k, j, full))
            k = j
            continue
        if e["id"] == 238:
            j = k + 1; name = None
            while j < a["end"] and ev[j]["id"] in (43, 239):
                if ev[j]["id"] == 239:
                    name = m._raw(d, ev[j])
                j += 1
            edits.append((k, j, m._raw(d, e) + bytes([43, 0]) + (name or b"")))
            k = j
            continue
        k += 1
    if moved:
        last_pat = max(i for i in range(a["first"]) if ev[i]["id"] == m.EV_PATTERN_NEW)
        edits.append((last_pat + 1, last_pat + 1, moved))
    return m._splice(d, ev, edits)


def with_pattern(d):
    """A pattern meta group before the arrangement (FL's 'last pattern' that took the markers)."""
    ev = m._events(d); a = m._arrangement(d, ev)
    grp = bytes([m.EV_PATTERN_NEW]) + struct.pack("<H", 1) + m._data_event(193, "Verse".encode("utf-16-le") + b"\0\0")
    return m._splice(d, ev, [(a["first"], a["first"], grp)])


def old_writer(d, marks):
    """The writers before the fix: 148 + 205 right before the arrangement's playlist event."""
    ev = m._events(d); kp = m._arrangement(d, ev)["playlist"]
    new = b"".join(bytes([148]) + struct.pack("<I", int((bar - 1) * 4 * P)) +
                   m._data_event(205, n.encode("utf-16-le") + b"\0\0") for bar, n in marks)
    return m._splice(d, ev, [(kp, kp, new)])


def read(d):
    ev = m._events(d)
    return [(mk["tick"], mk["name"]) for mk in m._markers(d, ev)], m._stray_markers(d, ev)


base = with_pattern(d0)
marks = [(1, "Intro"), (5, "A"), (13, "B")]
want = [(int((b - 1) * 4 * P), n) for b, n in marks]

# 1) the failure, reproduced: the old placement leaves the playlist without markers after FL's save
after = fl_model_save(old_writer(base, marks))
got, stray = read(after)
assert got == [] and [s["name"] for s in stray] == ["Intro", "A", "B"] and stray[0]["pattern"] == 1, (got, stray)
p_old = os.path.join(roots, "old.flp"); open(p_old, "wb").write(after)
info = m.flp_info(p_old)
assert "markers" not in info and "pattern(s) [1]" in info.get("markers_note", ""), info

# 2) every writer now puts them where FL keeps them
d1, n = m._set_markers(base, marks, P)
assert n == 3
got, stray = read(fl_model_save(d1))
assert got == want and not stray, (got, stray)

d2 = N._markers(base, marks)[0]
got, stray = read(fl_model_save(d2))
assert got == want and not stray, (got, stray)

d3 = PC._add_markers(base, [(1, "FLH Intro", None), (3, "FLH Drop", None), (5, "FLH Waltz", (3, 4))])
saved = fl_model_save(d3)
p3 = os.path.join(roots, "check.flp"); open(p3, "wb").write(saved)
info = m.flp_info(p3)
assert [x["name"] for x in info["markers"]] == ["FLH Intro", "FLH Drop", "FLH Waltz"], info.get("markers")
assert any(abs(t["bar"] - 5) < 0.01 and t["time_signature"] == "3/4" for t in info["time_signature_changes"]), info

# the slot: right after the playlist event, before the track groups - FL's own save's layout (99 241 36 233 |
# markers | 238 43 239), PC 2026-10-23 evidence
ev = m._events(d1); a = m._arrangement(d1, ev)
first_mk = min(k for k in range(a["first"], a["end"]) if ev[k]["id"] == 148)
assert first_mk == a["playlist"] + 1 and first_mk < min(g["k"] for g in m._track_groups(d1, ev).values())

# 3) markers an older version put before the playlist event move to FL's place on the next rewrite
d4, _ = m._set_markers(old_writer(base, [(9, "Old")]), marks, P)
got, stray = read(fl_model_save(d4))
assert got == want and not stray, (got, stray)

# 4) FL's full marker groups (148 33 34 46 45 168 101 205): rewrites take whole groups - no orphans left
fl_saved = fl_model_save(PC._add_markers(base, [(1, "Keep me?", None), (3, "Waltz", (3, 4))]))
d5, _ = m._set_markers(fl_saved, [(2, "New")], P)
ev5 = m._events(d5); a5 = m._arrangement(d5, ev5)
body = [e["id"] for e in ev5[a5["first"]:a5["end"]]]
assert body.count(148) == 2 and body.count(205) == 2 and body.count(168) == 1 and body.count(101) == 1, body
got = [(mk["name"], mk["signature"], mk["num"], mk["den"]) for mk in m._markers(d5, ev5)]
assert got == [("Waltz", True, 3, 4), ("New", False, None, None)], got
# shifting moves FL-shaped groups too (only the position)
d6 = m._shift_markers(d5, 0, 4 * P)
assert [mk["tick"] for mk in m._markers(d6, m._events(d6))] == [mk["tick"] + 4 * P for mk in m._markers(d5, ev5)]

# 4b) PC 2026-10-23: a song an OLDER version wrote (its markers before the 233, and FL's earlier groups stripped of
# 148 33 34 only: 46 45 168 101 205 orphans with an older version's names) -> FL's load + save put every marker at
# the last marker's bar with the old names. Any fl-headless write now repairs the layout first (_save)
older = fl_model_save(PC._add_markers(base, [(1, "Old intro", None), (5, "Old verse", None), (9, "Old end", None)]))
ev_o = m._events(older); a_o = m._arrangement(older, ev_o)
strip = [(k, k + 3, b"") for k in range(a_o["first"], a_o["end"]) if ev_o[k]["id"] == 148]   # 148 33 34 only
damaged = m._splice(older, ev_o, strip)
damaged = old_writer(damaged, marks)                               # the old writer's markers before the 233
got, _ = read(fl_model_save(damaged))
assert len(got) >= 2 and len({t for t, _ in got}) == 1 and want not in (got,), got   # the failure, reproduced
p_dmg = os.path.join(roots, "damaged.flp")
m._save(damaged, p_dmg)                                            # any write (here: the low-level one) repairs it
fixed = m._load(p_dmg)
assert m._marker_layout_fix(fixed)[1] == 0, "repaired once, stable"
got, stray = read(fixed)
assert got == want and not stray, (got, stray)
got, stray = read(fl_model_save(fixed))                            # ... and FL's load + save keeps them
assert got == want and not stray, (got, stray)
# the reader no longer lets an orphan's name overwrite the last marker's (it showed 'Old end' for 'B' before)
assert [n for _, n in read(damaged)[0]] == ["Intro", "A", "B"], read(damaged)[0]
# a file FL already scrambled that way (all markers at one bar, the real ones on the last pattern): rescued too
scrambled = fl_model_save(damaged)
p_scr = os.path.join(roots, "scr.flp")
with open(p_scr, "wb") as f:
    f.write(scrambled)
info = m.flp_info(p_scr)
assert "scrambles" in info.get("markers_note", ""), info
res, n = m._marker_layout_fix(scrambled)
got, stray = read(res)
assert n == 3 and got == want and not stray, (n, got, stray)
# markers after the track groups (this morning's slot) move to FL's place; a song in FL's own layout is untouched
ev1 = m._events(d1); a1 = m._arrangement(d1, ev1); grp1 = m._track_groups(d1, ev1)
mk1 = [(k, m._marker_group_end(ev1, k)) for k in range(a1["first"], a1["end"]) if ev1[k]["id"] == 148]
blob = b"".join(m._raw(d1, ev1[i]) for k, j in mk1 for i in range(k, j))
late = m._splice(d1, ev1, [(k, j, b"") for k, j in mk1] + [(max(g["end"] for g in grp1.values()),) * 2 + (blob,)])
res, n = m._marker_layout_fix(late)
assert n == 3 and read(res)[0] == want and m._events(res)[m._arrangement(res, m._events(res))["playlist"] + 1]["id"] == 148
assert m._marker_layout_fix(fl_model_save(d1)) == (fl_model_save(d1), 0)

# 4c) PC 2026-10-23, the merged edit on that song: flp_edit [flp_remove_channels, flp_mixer] of a file in the older
# layout. The layout repair inside _save made flp_remove_channels' own read-back check fail ('verification failed at
# item N (ev 148 ...)': nothing written); now it compares alike, the edit lands, and FL's load + save keeps markers
song = m._load(os.path.join(HERE, "corpus", "base.flp"))
Ps = m._ppq(song)
song_old = m._splice(song, m._events(song), [(m._arrangement(song, m._events(song))["playlist"],) * 2 + (b"".join(
    bytes([148]) + struct.pack("<I", int((b - 1) * 4 * Ps)) + m._data_event(205, n.encode("utf-16-le") + b"\0\0")
    for b, n in marks),)])                                         # markers before the 233 (the older writer)
want_s = [(int((b - 1) * 4 * Ps), n) for b, n in marks]
p_m = os.path.join(roots, "merged.flp")
open(p_m, "wb").write(song_old)
last_ch = max(m._structure(song_old)[1])
m.fl_running = lambda: False
r = m.TOOLS["flp_edit"][0](project=p_m, ops=[
    {"tool": "flp_remove_channels", "args": {"channels": [last_ch], "force": True}},
    {"tool": "flp_mixer", "args": {"tracks": [{"track": 1, "volume_db": -3}]}}])
assert r["results"][0]["removed"][0]["channel"] == last_ch, r
done = m._load(p_m)
assert m._marker_layout_fix(done)[1] == 0 and read(done)[0] == want_s, read(done)
got, stray = read(fl_model_save(done))
assert got == want_s and not [x for x in stray if not x["signature"]], (got, stray)   # (a pattern's own 4/4 stays)

# 5) playlist track names in FL's order 238 43 239: read; named in place; new names after the 43
plain = fl_model_save(base)                                        # FL's tracks: 238 43 (no names)
ev = m._events(plain)
assert all(g["name"] is None for g in m._track_groups(plain, ev).values())
named, nst = N._style_tracks(plain, [{"track": 1, "name": "Drums", "color": "#5C2626"}, {"track": 2, "name": "Bass"}])
assert nst == 2
evn = m._events(named); g = m._track_groups(named, evn)
assert g[1]["name"] == "Drums" and g[2]["name"] == "Bass" and g[3]["name"] is None, g
assert [evn[k]["id"] for k in range(g[1]["k"], g[1]["end"])] == [238, 43, 239], "FL's order"
assert m._payload(named, evn[g[1]["k"]])[4:7] == bytes([0x5C, 0x26, 0x26])
after = fl_model_save(named)                                       # FL's save keeps them
ga = m._track_groups(after, m._events(after))
assert ga[1]["name"] == "Drums" and ga[2]["name"] == "Bass", ga
renamed, _ = N._style_tracks(after, [{"track": 1, "name": "Kit"}])
gr = m._track_groups(renamed, m._events(renamed))
assert gr[1]["name"] == "Kit" and len([1 for e in m._events(renamed) if e["id"] == 239]) == 2, gr
# the old writer's order (238 239 43) is read too, and the old adjacency reader missed FL's order (the failure)
old_order = m._splice(plain, ev, [(m._track_groups(plain, ev)[1]["k"] + 1, m._track_groups(plain, ev)[1]["k"] + 1,
                                   m._data_event(239, "Drums".encode("utf-16-le") + b"\0\0"))])
assert m._track_groups(old_order, m._events(old_order))[1]["name"] == "Drums"
evA = m._events(after)
adjacent = {k for k, e in enumerate(evA) if e["id"] == 238 and k + 1 < len(evA) and evA[k + 1]["id"] == 239}
assert not adjacent, "FL puts 43 between 238 and 239"
# flh_realism names a track the same way
rn, ok = R._name_track(after, 3, "Strings")
assert ok and R._track_events(rn, m._events(rn))[3][1] == "Strings"
print("ok")
