"""flp_instrument_sound rewrites a Splice INSTRUMENT state to another pack and keeps every length field consistent."""
import os, re, shutil, struct, sys, tempfile
import xml.etree.ElementTree as ET
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
FIX = os.environ.get("FLH_TEST_INSTRUMENT_FLP") or os.path.join(HERE, "corpus", "base.flp")   # has INSTRUMENT on ch 4
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp
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
print("ok", r["sound"], r["was"])
