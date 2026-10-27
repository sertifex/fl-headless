"""A collection profile's taste applies only to the collection's own songs: collection_of / profile_for (the song
journal's collection - '' = none - wins; else the profile's songs list, a listed song's own folder, the profile's
'folder'); everything else gets only the user-wide keys (palette, edition ...). flh_new_song makes a song outside the
active collection unless collection= says otherwise (journal + the profile's songs), so a collection's 'sound
real / instrument-led' style never turns a new synthwave song's ear candy organic; organic moves only use instruments the
song has (no 'Piano Pickup' on a synth chords channel). Run: python3 tests/test_collection_scope.py"""
import json, os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ["FLH_FL_USERDATA"] = os.path.join(tmp, "Documents", "Image-Line", "FL Studio")
os.environ["FLH_FACTORY_PACKS"] = os.path.join(tmp, "FL", "Data", "Patches", "Packs")
os.environ["FLH_SPLICE_LIB"] = os.path.join(tmp, "Splice", "INSTRUMENT")
os.environ["FLH_FLEX_DIR"] = os.path.join(tmp, "FLEX"); os.environ["FLH_NO_WARM"] = "1"
import fl_headless_mcp as m  # noqa: E402
import flh_store as ST, flh_catalog as CAT, flh_compose as C, flh_newsong as N  # noqa: E402
ns = vars(m)
for mod in (ST, CAT, C, N):
    mod.register(ns)
if "clashes" not in ns:
    import flh_score as SC; SC.register(ns)

# ----------------------------------------------------------------------------- membership
album = os.path.join(roots, "Album")
song_a = os.path.join(album, "Morning Light", "Morning Light.flp"); os.makedirs(os.path.dirname(song_a)); open(song_a, "wb").write(b"x")
ST.profile_set("Piano Reworks", {"style": "piano-led, sound real, support layers faint", "ear_candy": "organic",
                                 "palette": {"melody": "#203040"}, "fl_edition": "producer",
                                 "songs": {"Morning Light": {"project": song_a}}, "finish": {"lufs": -16}})
assert ST.profile_name() == "Piano Reworks"
assert ST.collection_of(song_a)[0] == "Piano Reworks"
alt = os.path.join(album, "Morning Light", "Morning Light v2.flp"); open(alt, "wb").write(b"x")
assert ST.collection_of(alt)[0] == "Piano Reworks"          # a version in the song's own folder
other = os.path.join(roots, "Beats", "Neon", "Neon.flp"); os.makedirs(os.path.dirname(other))
open(other, "wb").write(b"x")
assert ST.collection_of(other)[0] is None
p_in, p_out = ST.profile_for(song_a), ST.profile_for(other)
assert p_in.get("style") and p_in.get("ear_candy") == "organic" and p_in.get("finish"), p_in
assert "style" not in p_out and "ear_candy" not in p_out and "finish" not in p_out, p_out
assert p_out.get("palette") == {"melody": "#203040"} and p_out.get("fl_edition") == "producer", p_out   # user-wide
ST.journal_update(other, set={"collection": "Piano Reworks"})        # the journal says it belongs: it does
assert ST.profile_for(other).get("style")
ST.journal_update(other, set={"collection": ""})                     # '' = made outside any collection
assert ST.collection_of(other) == (None, "the song journal says it belongs to no collection")
ST.journal_update(song_a, set={"collection": ""})                    # the journal wins over the songs list
assert "style" not in ST.profile_for(song_a)
ST.journal_update(song_a, set={"collection": "Piano Reworks"})
ST.profile_set("Piano Reworks", {"folder": os.path.join(roots, "Album")})
loose = os.path.join(album, "Loose.flp"); open(loose, "wb").write(b"x")
assert ST.collection_of(loose)[0] == "Piano Reworks"                 # under the profile's folder
assert ST.profile_for(None).get("style") is None and ST.profile_for(None).get("palette")

# ----------------------------------------------------------------------------- organic moves: only real instruments
od = C._org_data(C._ec_data())
v = {"channels": {0: {"name": "Polysynth", "plugin": "FLEX", "type": 2}, 1: {"name": "Lead", "type": 2},
                  2: {"name": "Keys", "plugin": "FLEX", "type": 2}, 3: {"name": "Chords", "plugin": "FLEX", "type": 2}},
     "roles": {0: "chords", 1: "melody", 2: "chords", 3: "chords"},
     "journal": {"channels": {"3": {"role": "chords", "preset": "KEY Felt Upright", "sound": "x.fst"}}},
     "registry": {"channels": {"chords": 0}}}
ch = C._org_channels(v, od)
assert 0 not in (ch.get("piano") or []) and 2 in ch["piano"] and 3 in ch["piano"], ch   # name / preset says piano
v2 = dict(v, channels={0: {"name": "Polysynth", "plugin": "FLEX", "type": 2}}, roles={0: "chords"}, journal={})
assert not C._org_channels(v2, od).get("piano")

# ----------------------------------------------------------------------------- flh_new_song: collection= option
FIX = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIX):
    print("skipped: new-song part (no tests/corpus/base.flp)"); print("ok"); sys.exit(0)


def make_blank(src, dst):
    d = m._load(src); ev = m._events(d)
    keep = []; inside = False
    for e in ev:
        if e["id"] == 64:
            inside = True
        if not inside and e["id"] not in (227,):
            if e["id"] == m.EV_NOTES:
                keep.append(m._data_event(m.EV_NOTES, b""))
            elif e["id"] == m.EV_PLAYLIST:
                keep.append(m._data_event(m.EV_PLAYLIST, b""))
            else:
                keep.append(m._raw(d, e))
        if inside and e["id"] == 51:
            inside = False
    hl = struct.unpack("<I", d[4:8])[0]
    head = bytearray(d[:8 + hl]); fmt, _, ppq = struct.unpack("<hHH", head[8:14])
    head[8:14] = struct.pack("<hHH", fmt, 0, ppq)
    body = b"".join(keep)
    with open(dst, "wb") as f:
        f.write(bytes(head) + b"FLdt" + struct.pack("<I", len(body)) + body)


blank = os.path.join(tmp, "Empty.flp"); make_blank(FIX, blank); os.environ["FLH_BLANK"] = blank
ST.profile_set("Piano Reworks", {"new_song_folder": os.path.join(roots, "Album")})   # new songs land next to it
r = N.flh_new_song("synthwave", key="G major", name="Neon Drive", open=False, balance=False)
assert r.get("collection") is None, r
jr = ST.journal_get(r["project"])
assert jr.get("collection") == "", jr.get("collection")
assert ST.collection_of(r["project"])[0] is None and "style" not in ST.profile_for(r["project"])
pats = {p["name"] for p in m._patterns(m._load(r["project"]), m._events(m._load(r["project"]))).values()}
assert not [p for p in pats if p.lower().startswith("piano pickup")], sorted(pats)
ec = C._ec_settings(True, C.genre_data("synthwave"), {"journal": jr}, r["project"])
assert ec["mode"] == "synthetic" and "collection" not in ec["mode_from"], (ec["mode"], ec["mode_from"])
ecj = C._ec_settings(True, C.genre_data("synthwave"), {"journal": dict(jr, collection="Piano Reworks")}, r["project"])
assert ecj["mode"] == "organic" and ecj["mode_from"] == "collection profile", ecj["mode_from"]   # joined: it applies
assert "Neon Drive" not in (ST.profile_get("Piano Reworks").get("songs") or {})
r2 = N.flh_new_song("synthwave", key="G major", name="Joined", open=False, balance=False, collection=True,
                    starter=False)
assert r2["collection"] == "Piano Reworks" and ST.journal_get(r2["project"])["collection"] == "Piano Reworks"
assert ST.profile_get("Piano Reworks")["songs"]["Joined"]["project"] == r2["project"]
assert ST.profile_for(r2["project"]).get("style")
assert N._new_song_collection(None) == "" and N._new_song_collection("off") == ""
assert N._new_song_collection("Other Set") == "Other Set"
ns["_state"]({"collection": None})
try:
    N._new_song_collection(True); raise SystemExit("collection=true with none active must say so")
except ValueError:
    pass
print("ok")
