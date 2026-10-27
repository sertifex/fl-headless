"""FLEX packs whose sounds aren't on this PC: a pack file shorter than its own file table says (a download that
didn't finish) or one FLEX's store list says isn't installed is marked installed=false in the index, left out of
flex_search (and so of flh_new_song's picks and the silent-sound swap), shown last and flagged by fl_sound_catalog;
a pack with a preset that made no sound here goes last, with two it's left out. One kind per preset everywhere: the
catalog and flp_flex_preset agree (a 'Core Synths' preset with store tags 'Bass;Synth' is a synth, not a bass).
Synthetic packs; no FL needed. Run: python3 tests/test_flex_install.py"""
import json, os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
flex = os.path.join(tmp, "FLEX"); os.makedirs(os.path.join(flex, "Packs"))
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ["FLH_FLEX_DIR"] = flex; os.environ["FLH_NO_WARM"] = "1"; os.environ.setdefault("FLH_FL_EDITION", "producer")


def pack(name, names, cut=0, sample_kb=4):
    ents = [("presetsIndex.presetsIndex", b"\x13\x37" * 50)] + [(n + ".flexpreset", b"P" * 64) for n in names]
    ents.append(("Samples/content.bin", b"S" * (sample_kb * 1024)))
    base = 8 + sum(4 + len(n) + 1 + 16 for n, _ in ents); tab = body = b""
    for n, data in ents:
        raw = n.encode() + b"\0"
        tab += struct.pack(">I", len(raw)) + raw + struct.pack(">QQ", base + len(body), len(data)); body += data
    blob = struct.pack(">II", 2, len(ents)) + tab + body
    with open(os.path.join(flex, "Packs", name + ".flexpack"), "wb") as f:
        f.write(blob[:len(blob) - cut] if cut else blob)


pack("Core Synths", ["LED Analogia", "LD Bright Square"])
pack("Saif Sameer Synthwave", ["FLEX Juno Flexity Warm", "FLEX DX7 Lead"], cut=3000)    # a download that stopped
pack("Store Leads", ["LD Store Saw"])
pack("Other Leads", ["LD Smooth Analog", "LD Soft Mono"])
with open(os.path.join(flex, "cache.json"), "w", encoding="utf-8-sig") as f:
    json.dump([{"productName": "Core Synths", "filename": "/FLEX/Core Synths.flexpack", "genre": "Electronic",
                "instrument": "Bass;Synth;Pads"},
               {"productName": "Store Leads", "filename": "/FLEX/Store Leads.flexpack", "instrument": "Synth",
                "installed": False}], f)

import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
for mod in ("flh_store", "flh_catalog", "flh_newsong", "flh_flex"):
    __import__(mod).register(ns)
import flh_flex as F  # noqa: E402
import flh_catalog as CAT  # noqa: E402
import flh_newsong as N  # noqa: E402

idx = {(r["pack"], r["preset"]): r for r in F.index()}
assert idx[("Saif Sameer Synthwave", "FLEX DX7 Lead")].get("installed") is False, idx
assert "downloaded" in idx[("Saif Sameer Synthwave", "FLEX DX7 Lead")]["why"]
assert idx[("Store Leads", "LD Store Saw")].get("installed") is False                 # the store list says so
assert idx[("Core Synths", "LED Analogia")].get("installed") is None
assert idx[("Core Synths", "LED Analogia")]["kind"] == "synth", idx[("Core Synths", "LED Analogia")]   # not 'bass'
assert F.kind_of("Thing", "", "Bass") == "bass" and F.kind_of("Thing", "", "Bass;Synth") == "synth"
fl = F.pack_flags()
assert set(fl) == {"Saif Sameer Synthwave", "Store Leads"}, fl
leads = F.flex_search(kind="lead", limit=20)
packs = {x["pack"] for x in leads}
assert "Saif Sameer Synthwave" not in packs and "Store Leads" not in packs and leads, leads
allx = F.flex_search(kind="lead", limit=20, include_unavailable=True)
gone = [x for x in allx if x["pack"] == "Saif Sameer Synthwave"]
assert gone and gone[0].get("unavailable") and allx[-1]["pack"] in fl, allx
assert F._flex_list(kind="lead")["unavailable_packs"]

# the catalog: one kind, the unavailable ones flagged and last
cat = CAT.fl_sound_catalog(query="sequencer arp analogia", refresh=True)
led = next(x for x in cat["results"] if x["name"] == "LED Analogia")
assert led["kind"] == "synth", led
cl = CAT.fl_sound_catalog(query="lead", kind="lead", limit=30)
names = [x["name"] for x in cl["results"]]
dx = next((x for x in cl["results"] if x["name"] == "FLEX DX7 Lead"), None)
assert dx and dx.get("installed") is False and dx.get("may_load_silent"), dx
assert names.index("FLEX DX7 Lead") > max(names.index(n_) for n_ in ("LD Smooth Analog", "LD Bright Square")), names
assert all(x.get("installed") is False for x in cl["results"][names.index("FLEX DX7 Lead"):]), cl["results"]
# flp_flex_preset reports the same kind (and warns when asked for an unavailable one by name)
song = os.path.join(tmp, "Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song)
m._with_open_project = lambda a, b_, fn: fn()
r = F.flp_flex_preset(song, song, "LED Analogia")
assert r["kind"] == led["kind"] == "synth" and not r.get("warning"), r
r2 = F.flp_flex_preset(song, song, "FLEX DX7 Lead", channel=r["new_channel"])
assert "silent" in r2.get("warning", ""), r2

# flh_new_song's pick for a lead: never the unavailable packs, and it says it left them out
rec, _g = N.recipe("synthwave")
spec = next(s for s in rec["channels"] if s["role"] == "melody")
N._FLEX_USED.clear()
skipped = []
got = N._flex_choice("lead", spec, rec, "synthwave", [], skipped)
assert got and got["pack"] not in fl, got
assert any("left out" in s and "Saif Sameer Synthwave" in s for s in skipped), skipped
# a preset of a pack made no sound here: that pack goes last; two silent presets: left out
N.silent_mark("FLEX", "Other Leads", "LD Soft Mono")
assert "Other Leads" in F.pack_flags()
lead2 = F.flex_search(kind="lead", limit=20)
assert lead2[-1]["pack"] == "Other Leads" and lead2[0]["pack"] == "Core Synths", lead2
N._FLEX_USED.clear(); skipped = []
got = N._flex_choice("lead", spec, rec, "synthwave", [], skipped)
assert got and got["pack"] == "Core Synths", got                    # the swap / next song go to another pack
N.silent_mark("FLEX", "Other Leads", "LD Smooth Analog")
assert all(x["pack"] != "Other Leads" for x in F.flex_search(kind="lead", limit=20))
N.silent_sounds("clear")
assert "Other Leads" not in F.pack_flags()
print("ok")
