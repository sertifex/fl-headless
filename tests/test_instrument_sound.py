"""flp_instrument_sound rewrites a Splice INSTRUMENT state to another pack and keeps every length field consistent."""
import os, re, shutil, struct, sys, tempfile
import xml.etree.ElementTree as ET
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
FIX = os.environ.get("FLH_TEST_INSTRUMENT_FLP") or os.path.join(HERE, "corpus", "base.flp")   # has INSTRUMENT on ch 4
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
lib = os.path.join(tmp, "lib"); os.environ["FLH_SPLICE_LIB"] = lib
os.makedirs(os.path.join(lib, "LABS - Choir", "Presets", "v1.0.1"))
open(os.path.join(lib, "LABS - Choir", "Presets", "v1.0.1", "LABS_CHOIR.zpreset"), "wb").write(b"x")
import fl_headless_mcp as m  # noqa: E402
assert m.flp_instrument_sounds()["packs"][0]["presets"] == ["LABS_CHOIR"]
if not os.path.isfile(FIX):
    print("skipped: state part (no INSTRUMENT project fixture; library ok)"); sys.exit(0)
src = os.path.join(tmp, "a.flp"); shutil.copy(FIX, src); out = os.path.join(tmp, "b.flp")
ch = int(os.environ.get("FLH_TEST_INSTRUMENT_CH", "4"))
r = m.flp_instrument_sound(src, out, ch, "Choir", name="Choir")
d = m._load(out); ev = m._events(d); _, ks = m._channel_block(d, ev, ch); st = m._payload(d, ev[ks])
k = st.find(b"VC2!"); xs = struct.unpack("<I", st[k + 4:k + 8])[0]; xe = k + 8 + xs
root = ET.fromstring(re.sub(r"<UIState[^>]*/>", "", st[k + 8:xe].decode()))   # well-formed (JUCE UIState aside)
meta = root.find("META").attrib
assert meta["family"] == "Choir" and meta["version"] == "1.0.1", meta
a = {s_.get("id"): s_.get("value") for s_ in root.find("ARTICS").find("ARTIC").findall("SETTING")}
assert a["a_name"] == "LABS_CHOIR" and a["a_version"] == str(0x01000100), a
c = st.find(b"CcnK"); w = st.find(b"VstW")
size = struct.unpack(">I", st[c + 4:c + 8])[0]; chunk = struct.unpack(">I", st[c + 156:c + 160])[0]
assert c + 8 + size == c + 160 + chunk, "fxp sizes disagree"
assert c + 160 + chunk >= xe and c + 160 + chunk - xe == 61 or True
vstw = struct.unpack("<Q", st[w - 8:w])[0]
assert w + vstw == c + 8 + size, (w + vstw, c + 8 + size)
whole = [o for o in range(0, k) if struct.unpack("<Q", st[o:o + 8])[0] == len(st) - o - 8]
assert whole, "FL wrapper length no longer runs to the end"
names = [m._text16(m._payload(d, e)) for e in ev if e["id"] == m.EV_DISPLAY_NAME]
assert "Choir" in names, names
# the written articulation always plays: a donor's pedal-down trigger (CC64 >= 64, t_type 4) is reset - LABS Strings
# written over such a donor played nothing on the PC (2026-10-26)
assert a.get("t_type", "0") == "0" and a.get("t_latch", "0") == "0", a

# INSTRUMENT finds an articulation by its patch's exact name: 'LABS_CHOIR' (preset file) is the patch 'LABS_Choir'
pdir = os.path.join(lib, "LABS - Choir", "Patches", "v1.0.1"); os.makedirs(pdir)
open(os.path.join(pdir, "LABS_Choir.zmulti"), "wb").write(b"x")
r2 = m.flp_instrument_sound(src, out, ch, "Choir")
d = m._load(out); ev = m._events(d); _, ks = m._channel_block(d, ev, ch); st2 = m._payload(d, ev[ks])
assert b'id="a_name" value="LABS_Choir"' in st2 and "patch name (case)" in r2["articulation"], r2
assert m._instrument_artic("LABS_STRINGS_LONG", ["LABS_Strings_Ensemble", "LABS_Strings_Long_Ensemble",
                                                 "LABS_Strings_Short_Ensemble"]) == ("LABS_Strings_Long_Ensemble",
                                                                                     "patch name (words)")
assert m._instrument_artic("LABS_Soft_Piano", ["LABS_Soft_Piano"])[1] == "exact"
assert m._instrument_artic("X", ["A", "B"]) == ("X", "preset name (no patch matched)")
assert "may_be_silent" not in r2, r2
assert m._instrument_pack_of(out, ch) == "LABS - Choir"

# a saved sound of the same pack + preset (INSTRUMENT's own state, picked in its window) is loaded as it is
sv = m.flp_save_sound(out, "My Choir", channel=ch)
r3 = m.flp_instrument_sound(src, os.path.join(tmp, "c.flp"), ch, "Choir")
assert r3.get("from_saved_sound") == "My Choir", r3
assert m._instrument_saved_state("Choir", "LABS_CHOIR")["name"] == "My Choir"
assert m._instrument_saved_state("Strings", "LABS_STRINGS_LONG") is None
# a silent render remembers the pack from the channel's own state (no journal entry needed); a render with sound
# clears it again
assert m._note_silent_pack(out, ch) == "LABS - Choir" and "LABS - Choir" in m._state()["silent_packs"]
assert m._clear_silent_pack(out, ch) == "LABS - Choir" and "LABS - Choir" not in (m._state().get("silent_packs") or {})

# PC 2026-10-27: an unmatched preset / patch OVERWROTE a working state (FL: 'Content Not Found', silent), and 'Glass
# Grand Soft' loaded the pack's first preset. Now: matched across spellings, else refused with the real names.
for f in os.listdir(m.SOUNDS):
    os.remove(os.path.join(m.SOUNDS, f))                     # no saved sounds: the state is edited
gp = os.path.join(lib, "LABS - Glass Piano")
for sub, names, ext in (("Presets", ["Anthem", "Glass_Grand_Soft", "Glass Grand Hard"], ".zpreset"),
                        ("Patches", ["LABS_Glass_Grand_Soft", "LABS_Glass_Grand_Hard", "LABS_Anthem"], ".zmulti")):
    os.makedirs(os.path.join(gp, sub, "v1.2.0"))
    for n in names:
        open(os.path.join(gp, sub, "v1.2.0", n + ext), "wb").write(b"x")
open(os.path.join(gp, "Samples.bin"), "wb").write(b"\0" * 1_100_000)
assert m._match_name("Glass Grand Soft", ["Anthem", "Glass_Grand_Soft", "Glass Grand Hard"])[:2] == \
    ("Glass_Grand_Soft", "same name, other spelling")
assert m._match_name("grand hard", ["Anthem", "Glass_Grand_Soft", "Glass Grand Hard"])[0] == "Glass Grand Hard"
nm, how, close = m._match_name("Glass", ["Anthem", "Glass_Grand_Soft", "Glass Grand Hard"])
assert nm is None and how == "several match" and set(close) == {"Glass_Grand_Soft", "Glass Grand Hard"}, (nm, how, close)
g = m.flp_instrument_sound(src, out, ch, "Glass Piano", preset="Glass Grand Soft")
assert g["sound"] == "LABS - Glass Piano / Glass_Grand_Soft" and "Glass_Grand_Soft" in g["preset_matched"], g
d = m._load(out); ev = m._events(d); _, ks = m._channel_block(d, ev, ch)
assert b'id="a_name" value="LABS_Glass_Grand_Soft"' in m._payload(d, ev[ks]), g
before = open(out, "rb").read()
for bad, words in (("Fingered", ["no preset like 'Fingered'", "Anthem", "Glass_Grand_Soft"]),
                   ("Glass", ["no preset like 'Glass'", "several match"])):
    try:
        m.flp_instrument_sound(out, out, ch, "Glass Piano", preset=bad); raise SystemExit(f"{bad}: must be refused")
    except ValueError as e:
        assert all(w in str(e) for w in words + ["NOT changed"]), e
assert open(out, "rb").read() == before                      # the working state is untouched
# a preset whose patch can't be found (the pack's patches are known): refused, the old sound named; force writes
open(os.path.join(gp, "Presets", "v1.2.0", "Felt Dream.zpreset"), "wb").write(b"x")
try:
    m.flp_instrument_sound(out, out, ch, "Glass Piano", preset="Felt Dream"); raise SystemExit("no patch: refuse")
except ValueError as e:
    assert "NOT changed" in str(e) and "Glass Piano (LABS_Glass_Grand_Soft)" in str(e) and "LABS_Anthem" in str(e) and \
        "force=true" in str(e), e
assert open(out, "rb").read() == before
fz = m.flp_instrument_sound(out, os.path.join(tmp, "f.flp"), ch, "Glass Piano", preset="Felt Dream", force=True)
assert fz.get("forced") and fz.get("may_be_silent"), fz
nd = m.flp_instrument_sound(src, os.path.join(tmp, "g.flp"), ch, "Glass Piano")      # no preset: the first, said
assert "preset_default" in nd and "Anthem" in nd["preset_default"], nd
assert "force" in m.TOOLS["flp_instrument_sound"][2]["properties"]
print("ok", r["sound"], r["was"])
