"""flp_remove_channels: delete channels from a .flp and renumber every reference (notes, clips, links, layers,
controller records); refusals; verification; unused / unplaced_automation; flp_edit batching."""
import glob, json, os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_review as R, flh_cleanup as C  # noqa: E402
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
ns = vars(m)
flh_store.register(ns); R.register(ns); C.register(ns)
assert "flp_remove_channels" in m.TOOLS and m._OPEN_PROJECT_GUARD["flp_remove_channels"] == ("project", "output")
rm = C.flp_remove_channels
u16 = lambda b, i: struct.unpack_from("<H", b, i)[0]


def fresh(name):
    p = os.path.join(tmp, name); shutil.copy(FIX, p); return p


def chan_notes(path):
    """{channel: sorted [(pattern, pos, len, key, vel)]} over every pattern."""
    d = m._load(path); out = {}
    for pid, pt in m._patterns(d, m._events(d)).items():
        for r in pt["notes"]:
            out.setdefault(r[2], []).append((pid, r[0], r[3], r[4], r[11]))
    return {c: sorted(v) for c, v in out.items()}


def names(path):
    d = m._load(path); return {i: v["name"] for i, v in C._model(d)["info"].items()}


def links(path):
    d = m._load(path); return [(src, dest) for _, eid, _, src, dest in C._model(d)["links"] if eid == 227]


def splice_in(path, out, where, data):
    """Insert raw event bytes before event index `where`."""
    d = m._load(path); ev = m._events(d); m._save(m._splice(d, ev, [(where, where, data)]), out)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except C.Refuse as e:
        return str(e)
    raise SystemExit(f"expected a refusal: {a} {k}")


# ---------------------------------------------------------------- the fixture: 6 channels, automation 3 unplaced
src = fresh("a.flp")
n0, nm0, notes0 = m._fingerprint(src)["channels"], names(src), chan_notes(src)
assert n0 == 6 and nm0[3].startswith("Fruity Convolver") and nm0[5] == "Loop", nm0
cand = R.cleanup_candidates(m._load(src))
assert cand == {"unused": [], "unplaced_automation": [3]}, cand
doc = R.fl_doctor(src)
au = [i for i in doc["issues"] if i["kind"] == "automation_unplaced"]
assert au and "flp_remove_channels" in au[0]["fix"], au

# dry run: lists, writes nothing
before = open(src, "rb").read()
dr = rm(src, unplaced_automation=True, dry_run=True)
assert dr["dry_run"] and [r["channel"] for r in dr["removed"]] == [3] and dr["verified"]["reparsed"], dr
assert dr["references"]["renumbered_channels"] == {"4": 3, "5": 4}, dr["references"]
assert dr["references"]["links_dropped"] and dr["references"]["clips_renumbered"] == 1, dr["references"]
assert open(src, "rb").read() == before

# real run: automation channel 3 goes, 4 -> 3, 5 -> 4 everywhere
backups0 = len(m._versions(src))
res = rm(src, unplaced_automation=True)
print("removed:", json.dumps(res["references"]))
assert len(m._versions(src)) == backups0 + 1, "the original was backed up"
assert m._fingerprint(src)["channels"] == 5 and struct.unpack("<H", open(src, "rb").read()[10:12])[0] == 5
nm1 = names(src)
assert nm1 == {0: nm0[0], 1: nm0[1], 2: nm0[2], 3: nm0[4], 4: nm0[5]}, nm1
n1 = chan_notes(src)
assert n1 == {({4: 3}.get(c, c)): v for c, v in notes0.items()}, "notes renumbered, nothing else touched"
assert links(src) == [], "the deleted automation's link went with it"
loop = [c for c in m.flp_playlist(src)["clips"] if c.get("name") == "Loop"]
assert loop and loop[0]["channel"] == 4 and loop[0]["kind"] == "audio", loop
assert not [i for i in R.fl_doctor(src)["issues"] if i["kind"] in ("automation_unplaced", "unused_channel")]
m._structure(m._load(src))

# ---------------------------------------------------------------- refusals
src = fresh("b.flp"); before = open(src, "rb").read()
msg = refused(rm, src, channels=[0])
assert "notes on the playlist" in msg and "force" in msg, msg
msg = refused(rm, src, channels=["Loop"])
assert "clip(s) placed" in msg, msg
assert open(src, "rb").read() == before, "a refusal changes nothing"
assert "every channel" in refused(rm, src, channels=[0, 1, 2, 3, 4, 5], force=True)
try:
    rm(src, channels=[9]); raise SystemExit("bad index must fail")
except ValueError as e:
    assert "no channel 9" in str(e)
assert rm(src)["removed"] == [] and open(src, "rb").read() == before

# force: channel 0 and its 51 notes go, every later channel moves down one
res = rm(src, channels=[0], force=True)
assert res["forced"] and res["removed"][0]["notes_on_playlist"] == 51, res
n2 = chan_notes(src)
assert n2 == {c - 1: v for c, v in notes0.items() if c != 0}, "remaining notes identical, renumbered"
assert links(src) == [(2, 0x7000)], links(src)                      # automation 3 -> 2, mixer target unchanged
assert [c["channel"] for c in m.flp_playlist(src)["clips"] if c.get("name") == "Loop"] == [4]
# force on the audio clip channel: its clip leaves the playlist too
res = rm(src, channels=["Loop"], force=True)
assert not [c for c in m.flp_playlist(src)["clips"] if c["kind"] == "audio"] and res["references"]["clips_dropped"]

# ---------------------------------------------------------------- built with the main file's own writers
src = fresh("c.flp")
m.flp_add_channel(src, src, 1)                                      # channel 6: clone of 1, no notes -> unused
m.flp_add_automation(src, src, [{"bar": 1, "db": -20}, {"bar": 3, "db": -6}], track=2, name="Ride")   # 7, placed
m.flp_add_channel(src, src, 2)                                      # 8: clone of 2, gets notes in pattern 1
m.flp_add_notes(src, [{"note": "C5", "bar": 1, "beat": 0, "beats": 1, "velocity": 90}], pattern=1, channel=8)
assert m._fingerprint(src)["channels"] == 9
cand = R.cleanup_candidates(m._load(src))
assert cand == {"unused": [6], "unplaced_automation": [3]}, cand
du = [i for i in R.fl_doctor(src)["issues"] if i["kind"] == "unused_channel"]
assert du and "channel 6" in du[0]["where"] and "flp_remove_channels" in du[0]["fix"], du
notes_c, nm_c, ride = chan_notes(src), names(src), links(src)
assert (7, 0x7000 + (2 << 6)) in ride, ride
res = rm(src, unused=True, unplaced_automation=True)
assert sorted(r["channel"] for r in res["removed"]) == [3, 6], res["removed"]
mp = {0: 0, 1: 1, 2: 2, 4: 3, 5: 4, 7: 5, 8: 6}
assert names(src) == {mp[c]: v for c, v in nm_c.items() if c in mp}
assert chan_notes(src) == {mp[c]: v for c, v in notes_c.items() if c in mp}
assert links(src) == [(5, 0x7000 + (2 << 6))], links(src)          # Ride: source renumbered 7 -> 5
ride_clip = [c for c in m.flp_playlist(src)["clips"] if c.get("name") == "Ride"]
assert ride_clip and ride_clip[0]["channel"] == 5 and ride_clip[0]["kind"] == "automation", ride_clip
assert [i for i in R.fl_doctor(src)["issues"] if i["kind"] == "automation_target"] == []

# ---------------------------------------------------------------- crafted references: layers, channel-target
# automation, controller records with channel destinations
src = fresh("d.flp"); M = C._model(m._load(src))
_, end1 = M["blocks"][1]                                              # channel 1 becomes a layer of 2 and 4
lay = os.path.join(tmp, "d1.flp")
splice_in(src, lay, end1, bytes([94]) + struct.pack("<H", 2) + bytes([94]) + struct.pack("<H", 4))
d = m._load(lay); ev = m._events(d)
k225 = next(k for k, e in enumerate(ev) if e["id"] == 225)
p = m._payload(d, ev[k225])
rec = lambda dest, v: b"\0\0\0\0" + struct.pack("<HHI", 0x0001, dest, v)
p2 = p + rec(2, 111) + rec(4, 444) + rec(0x0FFF, 7)
k227 = next(k for k, e in enumerate(ev) if e["id"] == 227)
lk = bytearray(m._payload(d, ev[k227])); lk[10:12] = struct.pack("<H", 4)    # automation 3 now drives channel 4
m._save(m._splice(d, ev, [(k225, k225 + 1, m._data_event(225, p2)), (k227, k227 + 1, m._data_event(227, bytes(lk)))]), lay)
assert R.cleanup_candidates(m._load(lay))["unused"] == []
# removing channel 4 while automation 3 targets it: refused (even though 4's notes aren't the issue with force)
msg = refused(rm, lay, channels=[4], force=False)
assert "automates channel 4" in msg, msg
# removing channel 2 (force: it has notes): child 2 dropped, child 4 -> 3, record dest 2 dropped, 4 -> 3,
# the automation's channel target 4 -> 3, source 3 -> 2
res = rm(lay, channels=[2], force=True)
r_ = res["references"]
assert r_["layer_refs_dropped"] == 1 and r_["layer_refs_renumbered"] == 1, r_
assert r_["controller_records_dropped"] == 1 and r_["controller_records_renumbered"] == 1, r_
M2 = C._model(m._load(lay))
assert [c for _, c in M2["info"][1]["children"]] == [3], M2["info"][1]["children"]
assert [(s, dst) for _, eid, _, s, dst in M2["links"] if eid == 227] == [(2, 3)]
dests = [u16(r, 6) for _, eid, rr in M2["recs"] if eid == 225 for r in rr if u16(r, 6) < 0x7000]
assert sorted(dests) == [3, 0x0FFF, 0x4000], [hex(x) for x in dests]
val = [struct.unpack_from("<I", r, 8)[0] for _, eid, rr in M2["recs"] if eid == 225 for r in rr if u16(r, 6) == 3]
assert val == [444], val
# with the automation in the deletion too: allowed, no force needed for the link
lay2 = os.path.join(tmp, "d2.flp"); m._save(m._load(os.path.join(tmp, "d1.flp")), lay2)
res = rm(lay2, channels=[2, 3], force=True)
assert links(lay2) == [] and res["references"]["links_dropped"]

# ---------------------------------------------------------------- a second arrangement: its clips renumber too
src = fresh("e.flp"); d = m._load(src); ev = m._events(d)
k233 = next(k for k, e in enumerate(ev) if e["id"] == 233)
arr2 = struct.pack("<BH", 99, 1) + m._raw(d, ev[k233])
m._save(m._splice(d, ev, [(k233 + 1, k233 + 1, arr2)]), src)
rm(src, channels=[3])
items = [u16(r, 6) for _, rr in C._model(m._load(src))["clips"] for r in rr if u16(r, 6) < 0x5000]
assert items == [4, 4], items                                         # Loop 5 -> 4 in both arrangements

# ---------------------------------------------------------------- things it refuses to touch
src = fresh("f.flp"); d = m._load(src); ev = m._events(d)
k225 = next(k for k, e in enumerate(ev) if e["id"] == 225)
odd = os.path.join(tmp, "f1.flp")
m._save(m._splice(d, ev, [(k225, k225 + 1, m._data_event(225, m._payload(d, ev[k225]) + rec(0x2005, 1)))]), odd)
assert "unknown destination" in refused(rm, odd, channels=[3])
k227 = next(k for k, e in enumerate(ev) if e["id"] == 227)
lk = bytearray(m._payload(d, ev[k227])); lk[0:2] = b"\x01\x00"
odd2 = os.path.join(tmp, "f2.flp"); m._save(m._splice(d, ev, [(k227, k227 + 1, m._data_event(227, bytes(lk)))]), odd2)
assert "controller link" in refused(rm, odd2, channels=[3])
odd3 = os.path.join(tmp, "f3.flp")
m._save(m._splice(d, ev, [(k225, k225, m._data_event(222, b"\x01" * 12))]), odd3)
assert "legacy" in refused(rm, odd3, channels=[3])

# ---------------------------------------------------------------- verification catches a wrong rewrite
src = fresh("g.flp"); before = open(src, "rb").read()
real = C._rewrite
def broken(d, M, dele, force):                                         # forgets to renumber one note
    new, rep, bl = real(d, M, dele, force)
    ev = m._events(new); k = next(k for k, e in enumerate(ev) if e["id"] == 224 and e["len"])
    p = bytearray(m._payload(new, ev[k])); p[6:8] = struct.pack("<H", u16(p, 6) + 1)
    return m._splice(new, ev, [(k, k + 1, m._data_event(224, bytes(p)))]), rep, bl
C._rewrite = broken
try:
    rm(src, channels=[3]); raise SystemExit("verification must catch it")
except RuntimeError as e:
    assert "verification failed" in str(e), e
finally:
    C._rewrite = real
assert open(src, "rb").read() == before, "nothing written when verification fails"

# ---------------------------------------------------------------- flp_edit batches it (guarded writer)
src = fresh("h.flp")
r = m.flp_edit(src, [{"tool": "flp_remove_channels", "unplaced_automation": True},
                     {"tool": "flp_add_notes", "pattern": 1, "channel": 3,
                      "notes": [{"note": "E5", "bar": 2, "beats": 1}]}])
assert r["ops"] == 2 and m._fingerprint(src)["channels"] == 5, r
assert any(n[3] == 76 for n in chan_notes(src)[3]), "E5 added to the renumbered channel 3"
# the MCP tool wrapper: dry_run never needs FL; output defaults to the project
tool = m.TOOLS["flp_remove_channels"][0]
assert tool(project=src, channels=[3], dry_run=True)["dry_run"]
assert tool.__wrapped__ is C.flp_remove_channels

# ---------------------------------------------------------------- journal registries follow the renumbering (C4)
src = fresh("j.flp")
flh_store.journal_update(src, set={
    "channels": {"0": {"role": "keys"}, "1": {"role": "bass"}, "2": {"role": "melody"}, "3": {"role": "auto"}},
    "compose": {"channels": {"keys": 0, "bass": 1, "melody": 2, "pads": [0, 2]},
                "drum_map": {"kick": [1, 36, "Kit"], "snare": [2, 38, "Kit"]}, "transpose": {"1": 30, "2": -12},
                "tracks": {"keys": 1, "bass": 2}},
    "vocal": {"track": 5},
    "performance": {"parts": {"Keys": {"channel": 0}, "Bass": {"channel": 1}, "Mel": {"channel": 2}}},
    "performance_history": [{"spec": {"parts": {"Keys": {"channel": 0}, "Mel": {"channel": 2}}}}]})
r = rm(src, channels=[0], force=True)
j = flh_store.journal_get(src)
assert j["channels"] == {"0": {"role": "bass"}, "1": {"role": "melody"}, "2": {"role": "auto"}}, j["channels"]
assert j["compose"]["channels"] == {"bass": 0, "melody": 1, "pads": [1]}, j["compose"]["channels"]
assert j["compose"]["drum_map"] == {"kick": [0, 36, "Kit"], "snare": [1, 38, "Kit"]}
assert j["compose"]["transpose"] == {"0": 30, "1": -12} and j["compose"]["tracks"] == {"keys": 1, "bass": 2}
assert j["vocal"] == {"track": 5}                                     # a mixer insert, not a channel
assert j["performance"]["parts"] == {"Bass": {"channel": 0}, "Mel": {"channel": 1}}
assert j["performance_history"][0]["spec"]["parts"] == {"Mel": {"channel": 1}}
assert "journal renumbered" in r["journal"], r["journal"]

# ---------------------------------------------------------------- notes in unplaced patterns block unused=true (H4)
src = fresh("k.flp")
ch_free = m.flp_add_channel(src, src, clone_from=0)["new_channel"]       # a channel with no notes in the song
sk = m.flp_new_pattern(src, "My Sketch", output=src)
m.flp_add_notes(src, [{"note": "C4", "bar": 1, "beats": 1}] * 3, pattern=sk["pattern"], channel=ch_free, output=src)
dr = rm(src, unused=True, dry_run=True)
assert any(x["channel"] == ch_free for x in dr["removed"]), dr
assert any("My Sketch" in b for b in dr.get("refused", [])), dr
try:
    rm(src, unused=True); raise SystemExit("must refuse")
except C.Refuse as e:
    assert "aren't in the playlist" in str(e), e
assert any(x["channel"] == ch_free for x in rm(src, unused=True, force=True)["removed"])

# ---------------------------------------------------------------- every fixture: removing each removable channel
for fx in (sorted(glob.glob(os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "*.flp")))
            or [os.path.join(HERE, "corpus", "base.flp")]):
    nm = names(fx)
    if len(nm) < 2:                                               # e.g. FL's Empty.flp: the last channel stays
        p_ = os.path.join(tmp, "fx.flp"); shutil.copy(fx, p_)
        try:
            rm(p_, channels=list(nm), force=True); raise SystemExit("must keep the last channel")
        except C.Refuse:
            pass
        continue
    for c in nm:
        p_ = os.path.join(tmp, "fx.flp"); shutil.copy(fx, p_)
        out = rm(p_, channels=[c], force=True)
        assert out["verified"]["channels"] == len(nm) - 1, out
print("ok")
