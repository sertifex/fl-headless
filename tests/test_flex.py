"""flh_flex: preset names read from FLEX pack file tables (.flexpack and .flex2pack layouts), kinds from names and
the store list, ranked search (kind, words, genre), a FLEX .fst made for any preset (pack/preset fields patched,
same length, knob values kept), and flp_flex_preset putting it on a new or an existing channel of a song.
Synthetic packs; no FL needed. Run: python3 tests/test_flex.py"""
import json, os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
flex = os.path.join(tmp, "FLEX"); os.makedirs(os.path.join(flex, "Packs"))
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ["FLH_FLEX_DIR"] = flex; os.environ["FLH_NO_WARM"] = "1"


def pack(path, names, flags=0x00000002):
    ents = [("presetsIndex.presetsIndex", b"\x13\x37" * 50), ("pcf.pcf", b"x")]
    for n in names:
        ents.append((n, b"P" * 64))
        if path.endswith(".flex2pack"):
            ents.append((n.rsplit(".", 1)[0] + ".usedsamples", b"u"))
    head = struct.pack(">II", flags, len(ents)); body = b""
    tab = b""
    base = 8 + sum(4 + len(n) + 1 + 16 for n, _ in ents)
    for n, data in ents:
        raw = n.encode() + b"\0"
        tab += struct.pack(">I", len(raw)) + raw + struct.pack(">QQ", base + len(body), len(data))
        body += data
    with open(path, "wb") as f:
        f.write(head + tab + body)


pack(os.path.join(flex, "Packs", "Core Brass.flex2pack"),
     ["BRS Horns Sustain.flex2preset", "BRS Trumpets Staccato.flex2preset", "BRS Epic Brass.flex2preset"], 0x01000002)
pack(os.path.join(flex, "Packs", "Dream Pads.flexpack"), ["Warm Haze.flexpreset", "Glass Choir.flexpreset",
                                                           "Night Drive.flexpreset"])
with open(os.path.join(flex, "cache.json"), "w", encoding="utf-8-sig") as f:
    json.dump([{"productName": "Dream Pads", "filename": "/FLEX/Dream Pads.flexpack", "genre": "Lo-Fi;Ambient",
                "instrument": "Pads"}], f)

import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
for mod in ("flh_store", "flh_catalog", "flh_newsong", "flh_flex"):
    __import__(mod).register(ns)
import flh_flex as F  # noqa: E402

idx = F.index()
names = {(r["pack"], r["preset"]) for r in idx}
assert ("Core Brass", "BRS Horns Sustain") in names and ("Dream Pads", "Warm Haze") in names, names
assert len(idx) == 6, idx                                                  # .usedsamples / index entries left out
k = {r["preset"]: r["kind"] for r in idx}
assert k["BRS Horns Sustain"] == "brass" and k["Glass Choir"] == "choir" and k["Warm Haze"] == "pad", k
assert k["Night Drive"] == "pad", k                                       # from the pack's store tags
s = F.flex_search(kind="brass")
assert [x["preset"] for x in s][:3] and all(x["kind"] == "brass" for x in s), s
assert F.flex_search(kind="horns") == s and F.kind_name("piano") == "keys"   # recipe kind names ({"flex": "piano"})
s = F.flex_search("epic", kind="brass")
assert s[0]["preset"] == "BRS Epic Brass", s
s = F.flex_search(kind="pad", genre="lofi")
assert s and s[0]["pack"] == "Dream Pads", s
assert F.flex_search("nothing like this") == []
assert os.path.isfile(F._cache_path())
idx2 = F.index()
assert idx2 == idx                                                         # cached

fst, hit = F.flex_fst("BRS Horns Sustain")
b = open(fst, "rb").read()
st = m._payload(b, F._state_span(b))
assert st[16:26] == b"Core Brass" and st[16 + 1025:16 + 1025 + 17] == b"BRS Horns Sustain", st[:40]
tpl = open(os.path.join(os.path.dirname(HERE), "data", "flex", "flex_template.fst"), "rb").read()
st0 = m._payload(tpl, F._state_span(tpl))
assert len(st) == len(st0) and st[F.NAMES_END:] == st0[F.NAMES_END:]       # knob values untouched
assert "FLEX" in (m._fst_label(fst) or "")
m._fst_block(fst)                                                          # loadable by flp_load_plugin
try:
    F.flex_fst("No Such Preset"); raise SystemExit("unknown preset accepted")
except ValueError:
    pass

song = os.path.join(tmp, "Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song)
m._with_open_project = lambda a, b_, fn: fn()
d0 = m._load(song); n0 = len(m.flh_newsong._chan_types(d0, m._events(d0))) if hasattr(m, "flh_newsong") else None
r = F.flp_flex_preset(song, song, "Warm Haze")
d = m._load(song); ev = m._events(d)
a, z = m._channel_block(d, ev, r["new_channel"])
blob = b"".join(m._payload(d, e) for e in ev[a:z + 1] if e["id"] == m.EV_PLUGIN_STATE)
assert b"Dream Pads" in blob and b"Warm Haze" in blob, r
r2 = F.flp_flex_preset(song, song, "BRS Trumpets Staccato", channel=r["new_channel"])
d = m._load(song); ev = m._events(d)
a, z = m._channel_block(d, ev, r["new_channel"])
blob = b"".join(m._payload(d, e) for e in ev[a:z + 1] if e["id"] == m.EV_PLUGIN_STATE)
assert b"Core Brass" in blob and b"BRS Trumpets Staccato" in blob and b"Warm Haze" not in blob, r2
assert "flp_flex_preset" in m.TOOLS and "flex_list" in m.TOOLS
# a new FLEX channel gets its own free mixer insert, named after it (PC 2026-10-27: it landed on the Bass insert)
d = m._load(song); ev = m._events(d); ins = m._channel_inserts(d, ev)
assert r.get("insert") and ins[r["new_channel"]] == r["insert"], (r, ins)
assert list(ins.values()).count(r["insert"]) == 1 and r["insert"] not in (3, 4, 5, 6), (r, ins)   # base.flp: 3-6 used
assert m._mixer_names(d, ev).get(r["insert"]) == "Warm Haze", m._mixer_names(d, ev)
rs = F.flp_flex_preset(song, song, "Glass Choir", insert="same", name="Choir Pad")
d = m._load(song); ev = m._events(d)
assert m._channel_inserts(d, ev)[rs["new_channel"]] == 3 and rs.get("insert") == 3, rs   # copied channel 0's insert
assert any(c.get("name") == "Choir Pad" for c in m.flp_list(song)["channels"] if c["index"] == rs["new_channel"]), \
    m.flp_list(song)["channels"]
# names as shown vs as stored: spelling / case / separators match; unclear names are refused with the closest
assert F.find("warm_haze")["preset"] == "Warm Haze" and F.find("BRS horns-sustain")["preset"] == "BRS Horns Sustain"
assert F.find("Horns Sustain")["preset"] == "BRS Horns Sustain"           # every word asked, one preset
try:
    F.flex_fst("BRS", "Core Brass"); raise SystemExit("an unclear name must be refused")
except ValueError as e:
    assert "several match" in str(e) and "BRS Epic Brass" in str(e) and "nothing was changed" in str(e), e
# fl_doctor's content check for FLEX: the pack a channel's state names isn't installed
st_ok = m._payload(open(F.flex_fst("Warm Haze")[0], "rb").read(), F._state_span(open(F.flex_fst("Warm Haze")[0], "rb").read()))
assert F.content_check("FLEX", st_ok)["problem"] is None
st_gone = F.patch_state(st_ok, "Vanished Pack", "Gone Preset")
cc = m.plugin_content_status("FLEX", st_gone)
assert cc and cc["family"] == "FLEX" and "isn't installed" in cc["problem"] and "flp_flex_preset" in cc["fix"], cc
assert F.content_check("Fruity Wrapper", st_gone) is None

# FLEX 808 presets sound two octaves below the written note (PC 2026-10-26: Heavy Atom, Sub Below, 808 Clean all
# played a written C2 at C0): the offset goes in the journal like a pitched sampler's, and a swap moves the notes
import flh_store  # noqa: E402
pack(os.path.join(flex, "Packs", "Core 808s.flex2pack"), ["808 Heavy Atom.flex2preset"], 0x01000002)
pack(os.path.join(flex, "Packs", "Core Bass.flexpack"), ["BAS Warm Amp Finger.flexpreset"])
F.index(refresh=True)
assert F.sounding_offset("808 Heavy Atom") == 24 and F.sounding_offset("BAS Warm Amp Finger") == 0
assert F.sounding_offset("Warm Haze") == 0 and F.sounding_offset("No Such Preset") == 0
r8 = F.flp_flex_preset(song, song, "808 Heavy Atom")
c8 = r8["new_channel"]
j8 = flh_store.journal_get(song)
assert j8["channels"][str(c8)]["transpose"] == 24 and j8["compose"]["transpose"][str(c8)] == 24, j8
assert "2 octaves below" in r8["tuning"], r8
m.flp_add_notes(song, [{"note": 51, "bar": 1, "beat": 0, "beats": 2}], channel=c8)   # Eb1 heard, written +24


def keys_of(ch):
    d_ = m._load(song); e_ = m._events(d_)
    return sorted(n[4] for p in m._patterns(d_, e_).values() for n in p["notes"] if n[2] == ch)


k0 = keys_of(c8)
assert k0 == [51], k0
rb = F.flp_flex_preset(song, song, "BAS Warm Amp Finger", channel=c8)        # a bass plays at pitch: notes -24
assert keys_of(c8) == [27] and rb["notes_moved"] == 1 and "removed" in rb["tuning"], (keys_of(c8), rb)
assert str(c8) not in {k for k, v in flh_store.journal_get(song)["channels"].items() if (v or {}).get("transpose")}
r8b = F.flp_flex_preset(song, song, "808 Heavy Atom", channel=c8)           # and back: +24 again
assert keys_of(c8) == [51] and flh_store.journal_get(song)["channels"][str(c8)]["transpose"] == 24, r8b
rp = F.flp_flex_preset(song, song, "Warm Haze", channel=r["new_channel"])   # no offset either side: nothing moves
assert "tuning" not in rp and "notes_moved" not in rp, rp

# ---------------------------------------------------------------- quality: data/flex/quality.json tiers + name words
assert F.pack_tier("General Midi Library")[0] == 0 and F.pack_tier("Core Pianos")[0] == 3
assert F.pack_tier("Essential Bass Guitars")[0] == 3 and F.pack_tier("Melodic Techno")[0] == 2
assert F.pack_tier("Check Pads", "Soft Guitar")[0] == 2 and F.pack_tier("Some Pack", "Choir Aahs")[0] == 1
assert F.gm_style("Fretless Bass Mellow") and F.gm_style("Organ 1") and not F.gm_style("Violin Ensemble Lush")
assert F.name_quality("KEY Felt Rhodes", "keys") > 0 > F.name_quality("Honky Piano Init", "keys")
pack(os.path.join(flex, "Packs", "General Midi Library.flexpack"),
     ["Electric Grand Piano.flexpreset", "Choir Aahs.flexpreset", "Muted Trumpet.flexpreset", "Warm Pad.flexpreset"])
pack(os.path.join(flex, "Packs", "Core Pianos.flexpack"), ["KEY AG Fused Piano.flexpreset", "KEY Felt Upright.flexpreset"])
pack(os.path.join(flex, "Packs", "Arksun Cityscape.flexpack"), ["KEY Night Rhodes.flexpreset"])
with open(os.path.join(flex, "cache.json"), "w", encoding="utf-8-sig") as f:
    json.dump([{"productName": "Dream Pads", "filename": "/FLEX/Dream Pads.flexpack", "genre": "Lo-Fi;Ambient",
                "instrument": "Pads"}, {"productName": "Arksun Cityscape", "filename": "/FLEX/Arksun Cityscape.flexpack",
                                        "genre": "Lo-Fi;Hip Hop", "instrument": "Keys"}], f)
F._MEMO.clear()
s = F.flex_search(kind="keys", prefer="electric grand piano")            # GM last, even when its name is asked for
assert s[-1]["preset"] == "Electric Grand Piano" and s[-1]["last_resort"] and s[-1]["tier"] == 0, s
assert s[0]["pack"] == "Core Pianos", s
s = F.flex_search(kind="keys", genre="lofi", prefer="rhodes")          # store-tagged genre pack + character word
assert s[0]["preset"] == "KEY Night Rhodes", s
s = F.flex_search(kind="keys", prefer="felt soft")
assert s[0]["preset"] == "KEY Felt Upright", s
s = F.flex_search(kind="keys", avoid=["fused"])
assert s[0]["preset"] != "KEY AG Fused Piano", s
s = F.flex_search("electric grand piano")                               # named outright: the user gets it
assert s[0]["preset"] == "Electric Grand Piano" and not s[0].get("last_resort"), s
s = F.flex_search(kind="pad")                                           # Dream Pads (unrated) over GM 'Warm Pad'
assert s[-1]["preset"] == "Warm Pad" and s[0]["pack"] == "Dream Pads", s
assert [x["preset"] for x in F.flex_search(kind="choir")] == ["Glass Choir", "Choir Aahs"]
shutil.rmtree(tmp, True)
print("flex ok")
