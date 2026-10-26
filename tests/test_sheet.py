"""flh_sheet: sheet music in. Image decoders (PNG filters/types, baseline + progressive JPEG from a tiny encoder
written here), PDFs with embedded page images (Flate + PNG predictor, DCT, 1-bit, /Rotate, object streams, vector
pages, encrypted / JBIG2 refusals), staff/system/bar-line detection on synthetic pages, the one-image-per-call
system crops through the MCP dispatcher, sheet_import (dry run, blocking, force, MIDI, project write in ONE edit,
journal), the reference-audio check (a deliberately wrong bar and a wrong accidental are flagged, a clean rendering
isn't, an octave slip in a one-voice line is), sheet_diff and sheet_explain.
Run: python3 tests/test_sheet.py   (prints 'ok')"""
import io, json, math, os, shutil, struct, sys, tempfile, wave, zlib
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import fl_headless_mcp as m  # noqa: E402
import flh_sheet as SH  # noqa: E402
import flh_store, flh_transcribe  # noqa: E402,E401
ns = vars(m)
flh_store.register(ns); flh_transcribe.register(ns); SH.register(ns)
import inspect  # noqa: E402
for t in ("sheet_pages", "sheet_import", "sheet_diff", "sheet_explain"):
    assert t in ns["TOOLS"], t
    sig = inspect.signature(ns["TOOLS"][t][0].__wrapped__).parameters
    sch = ns["TOOLS"][t][2]
    assert set(sch["properties"]) <= set(sig), (t, set(sch["properties"]) - set(sig))
    assert m.tool_group(t) == "sheet", t
assert "flh_sheet" in m.EXTENSIONS and "sheet_import" in m.ESSENTIAL and "sheet_import" in m.SHORT_DESC
assert len(m.SHORT_DESC["sheet_import"]) <= 250
listed = [t["name"] for t in m.handle({"method": "tools/list"})["tools"]]
assert "sheet_import" in listed and "sheet_pages" not in listed, listed
RNG = np.random.default_rng(11)


# ============================================================================= 1. PNG
def png_gray(img, filters=(0, 1, 2, 3, 4)):
    """8-bit gray PNG with the row filters cycled (exercises every unfilter path)."""
    h, w = img.shape
    raw = bytearray(); prev = np.zeros(w, np.int32)
    for y in range(h):
        ft = filters[y % len(filters)]; row = img[y].astype(np.int32); out = np.zeros(w, np.int32)
        for x in range(w):
            a = row[x - 1] if x else 0; b = prev[x]; c = prev[x - 1] if x else 0
            if ft == 0:
                pr = 0
            elif ft == 1:
                pr = a
            elif ft == 2:
                pr = b
            elif ft == 3:
                pr = (a + b) // 2
            else:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            out[x] = (row[x] - pr) & 255
        raw.append(ft); raw += bytes(out.astype(np.uint8)); prev = row
    ch = lambda t, b: struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) +
            ch(b"IDAT", zlib.compress(bytes(raw))) + ch(b"IEND", b""))


img = RNG.integers(0, 256, (37, 53)).astype(np.uint8)
assert np.array_equal(SH.png_decode(png_gray(img)), img)
rgb = RNG.integers(0, 256, (20, 30, 3)).astype(np.uint8)
g = SH.png_decode(m._png_encode(30, 20, rgb.tobytes()))
want = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
assert np.abs(g - want).max() <= 1.0

# ============================================================================= 2. JPEG (a tiny encoder)
ZZ = SH.ZZ
QT = np.array([16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56, 14, 17,
               22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92, 49, 64, 78,
               87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]) // 2 + 1        # natural order
AC_SYMS = [0x00, 0xF0] + [(r << 4) | s for r in range(16) for s in range(1, 11)]
DC_SYMS = list(range(12))


class BW:
    def __init__(self):
        self.out = bytearray(); self.acc = 0; self.n = 0

    def put(self, v, n):
        for k in range(n - 1, -1, -1):
            self.acc = (self.acc << 1) | ((v >> k) & 1); self.n += 1
            if self.n == 8:
                self.out.append(self.acc)
                if self.acc == 0xFF:
                    self.out.append(0)
                self.acc = 0; self.n = 0

    def flush(self):
        if self.n:
            self.put((1 << (8 - self.n)) - 1, 8 - self.n)


def mag(v):
    s = int(abs(v)).bit_length()
    return s, (v if v >= 0 else v + (1 << s) - 1)


def dht(cls, tid, syms, ln):
    counts = [0] * 16; counts[ln - 1] = len(syms)
    body = bytes([cls << 4 | tid]) + bytes(counts) + bytes(syms)
    return b"\xff\xc4" + struct.pack(">H", len(body) + 2) + body


def jpeg_gray(img, progressive=False, restart=0):
    h, w = img.shape
    H8, W8 = -(-h // 8) * 8, -(-w // 8) * 8
    p = np.pad(img.astype(np.float64), ((0, H8 - h), (0, W8 - w)), mode="edge") - 128
    D = SH._IDCT
    blocks = p.reshape(H8 // 8, 8, W8 // 8, 8).transpose(0, 2, 1, 3).reshape(-1, 8, 8)
    F = np.matmul(np.matmul(D, blocks), D.T).reshape(-1, 64)            # forward DCT (D orthonormal)
    q = np.round(F / QT).astype(int)
    zz = q[:, ZZ]                                                       # zigzag order
    dcode = {s: i for i, s in enumerate(DC_SYMS)}; acode = {s: i for i, s in enumerate(AC_SYMS)}
    hdr = b"\xff\xd8" + b"\xff\xdb" + struct.pack(">H", 67) + b"\x00" + bytes(QT[ZZ].astype(np.uint8))
    sof = b"\xff\xc2" if progressive else b"\xff\xc0"
    hdr += sof + struct.pack(">HBHHB", 11, 8, h, w, 1) + bytes([1, 0x11, 0])
    hdr += dht(0, 0, DC_SYMS, 4) + dht(1, 0, AC_SYMS, 8)
    if restart:
        hdr += b"\xff\xdd" + struct.pack(">HH", 4, restart)

    def sos(ss, se, ah, al):
        return b"\xff\xda" + struct.pack(">HB", 8, 1) + bytes([1, 0x00, ss, se, ah << 4 | al])

    def ac_run(bw, coefs):
        r = 0
        for v in coefs:
            if v == 0:
                r += 1; continue
            while r > 15:
                bw.put(acode[0xF0], 8); r -= 16
            s, bits = mag(v); bw.put(acode[(r << 4) | s], 8); bw.put(bits, s); r = 0
        if r:
            bw.put(acode[0x00], 8)

    def scan(kind, ss=0, se=63, ah=0, al=0):
        bw = BW(); segs = []; pred = 0
        for i, b in enumerate(zz):
            if restart and i and i % restart == 0:
                bw.flush(); segs.append(bytes(bw.out)); bw = BW(); pred = 0
            if kind == "dc":
                v = b[0] >> al; s, bits = mag(v - pred); pred = v
                bw.put(dcode[s], 4); bw.put(bits, s)
                if not progressive:
                    ac_run(bw, b[1:])
            elif kind == "dcref":
                bw.put(b[0] & 1, 1)
            else:
                ac_run(bw, b[ss:se + 1])
        bw.flush(); segs.append(bytes(bw.out))
        return b"".join(seg + (bytes([0xFF, 0xD0 + k % 8]) if k < len(segs) - 1 else b"") for k, seg in enumerate(segs))

    if not progressive:
        return hdr + sos(0, 63, 0, 0) + scan("dc") + b"\xff\xd9"
    return (hdr + sos(0, 0, 0, 1) + scan("dc", al=1) + sos(0, 0, 1, 0) + scan("dcref") + sos(1, 5, 0, 0) +
            scan("ac", 1, 5) + sos(6, 63, 0, 0) + scan("ac", 6, 63) + b"\xff\xd9")


ramp = (np.add.outer(np.arange(61) * 3, np.arange(83) * 2) % 256).astype(np.uint8)
ramp[20:24, :] = 10; ramp[:, 40:43] = 240
for prog in (False, True):
    for rst in (0, 5):
        dec = SH.jpeg_decode(jpeg_gray(ramp, progressive=prog, restart=rst))
        assert dec.shape == ramp.shape, dec.shape
        err = np.abs(dec.astype(float) - ramp).mean()
        assert err < 4.0, (prog, rst, err)
try:
    SH.jpeg_decode(b"\xff\xd8\xff\xc3\x00\x0b\x08\x00\x10\x00\x10\x01\x01\x11\x00")
    raise SystemExit("lossless JPEG must be refused")
except SH.Unsupported as e:
    assert "PNG" in str(e)
try:                                                             # PIL (if present) checks AC refinement scans too
    from PIL import Image
    b = io.BytesIO(); Image.fromarray(ramp).save(b, "JPEG", quality=90, progressive=True)
    ref = np.asarray(Image.open(io.BytesIO(b.getvalue())).convert("L")).astype(float)
    assert np.abs(SH.jpeg_decode(b.getvalue()) - ref).mean() < 1.0
    b = io.BytesIO(); Image.fromarray(np.repeat(ramp[:, :, None], 3, 2)).save(b, "JPEG", quality=85, subsampling=2)
    ref = np.asarray(Image.open(io.BytesIO(b.getvalue())).convert("L")).astype(float)
    assert np.abs(SH.jpeg_decode(b.getvalue()) - ref).mean() < 1.5
except ImportError:
    print("  (PIL not installed: progressive AC-refinement + color JPEG cross-check skipped)")


# ============================================================================= 3. synthetic sheet pages
def page(W=1700, H=2200, systems=((2, 4), (2, 5), (1, 3), (2, 4)), sp=16, seed=0, skew=0.0, noise=0):
    """Staff systems (staves, bars) with lines, an opening line, bar lines through the system and stemmed heads."""
    rng = np.random.default_rng(seed)
    g = np.full((H, W), 250, np.float64)
    y = 220; x0, x1 = 120, W - 100
    for nst, nb in systems:
        tops = [y + k * 10 * sp for k in range(nst)]
        for top in tops:
            for ln in range(5):
                g[top + ln * sp: top + ln * sp + 2, x0:x1] = 15
        bot = tops[-1] + 4 * sp
        g[tops[0]:bot + 2, x0:x0 + 3] = 15
        bx = [int(b) for b in np.linspace(x0 + 4 * sp, x1 - 1, nb + 1)[1:]]
        for b in bx:
            g[tops[0]:bot + 2, b - 2:b + 1] = 15
        edges = [x0 + 4 * sp] + bx
        for top in tops:
            for a, b in zip(edges[:-1], edges[1:]):
                for xn in np.linspace(a + sp * 1.5, b - sp * 1.5, 4):
                    xn = int(xn); pos = rng.integers(-2, 10); yc = top + pos * sp // 2
                    yy, xx = np.ogrid[-sp:sp + 1, -sp:sp + 1]
                    msk = (xx / (0.65 * sp)) ** 2 + (yy / (0.45 * sp)) ** 2 <= 1
                    g[yc - sp:yc + sp + 1, xn - sp:xn + sp + 1][msk] = 10
                    if pos < 4:
                        g[yc:yc + int(3.5 * sp), xn - int(0.6 * sp):xn - int(0.6 * sp) + 2] = 10
                    else:
                        g[yc - int(3.5 * sp):yc, xn + int(0.6 * sp) - 2:xn + int(0.6 * sp)] = 10
        y = bot + 9 * sp
    if noise:
        g = g + rng.normal(0, noise, g.shape)
    g = np.clip(g, 0, 255).astype(np.uint8)
    if skew:
        t = math.tan(math.radians(skew)); out = np.full_like(g, 250)
        for x in range(W):
            off = int(round((x - W / 2) * t))
            out[max(0, off):H + min(0, off), x] = g[max(0, -off):H - max(0, off), x]
        g = out
    return g, [(s, b) for s, b in systems]


bad = []
for seed in range(6):
    for sp in (10, 14, 20):
        gp, truth = page(sp=sp, seed=seed, noise=(seed % 3) * 8, skew=(seed % 5 - 2) * 0.7, H=max(2200, sp * 140))
        lay = SH.analyze_page(gp)
        got = [(s["staves"], s["bars_est"]) for s in lay["systems"]]
        if got != truth:
            bad.append((seed, sp, got))
assert len(bad) <= 1, bad                                        # bar counts are estimates; staves/systems exact
gp, truth = page(skew=1.5, noise=10)
lay = SH.analyze_page(gp)
assert abs(lay["skew_deg"] - 1.5) <= 0.15, lay["skew_deg"]
assert [s["staves"] for s in lay["systems"]] == [2, 2, 1, 2]

pdir = os.path.join(tmp, "pages"); os.makedirs(pdir)
g1, _ = page(seed=1)
g2, _ = page(seed=2, systems=((1, 4), (1, 4), (1, 3)), sp=18)
with open(os.path.join(pdir, "p1.png"), "wb") as f:
    f.write(png_gray(g1, filters=(1, 2, 4)))
with open(os.path.join(pdir, "p2.jpg"), "wb") as f:
    f.write(jpeg_gray(g2))


def call(name, **args):
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    content = r["content"]
    raw = next(c["text"] for c in content if c["type"] == "text")
    if r.get("isError"):
        return {"_error": raw}, []
    text = json.loads(raw)
    imgs = [c for c in content if c["type"] == "image"]
    return text, imgs


r, imgs = call("sheet_pages", source=pdir)
assert "_error" not in r, r
assert r["pages"] == 2 and r["systems_on_page"] == 4 and len(imgs) == 1, r
assert [s["staves"] for s in r["page_systems"]] == [2, 2, 1, 2]
assert r["showing"]["systems"][0]["system"] == 1 and r["showing"]["systems"][0]["bars_est"] == 4, r["showing"]
assert r["showing"]["px_per_staff_space"] >= 12, r["showing"]
assert "sheet_pages" in r["next"] and '"rows": 2' in r["next"], r["next"]
import base64  # noqa: E402
pix = SH.png_decode(base64.b64decode(imgs[0]["data"]))
assert pix.shape[0] * pix.shape[1] <= SH.IMAGE_BUDGET_PX + 1000 and pix.shape[1] <= 1400
lay1 = SH.page_layout(SH.open_source(pdir)["pages"][0])
ys = lay1["systems"][0]
assert ys["bars_est"] == 4 and len(ys["bar_x"]) == 4                # last bar line at the system end
r, imgs = call("sheet_pages", source=pdir, rows="all")
assert len(imgs) == 1 and len(r["showing"]["systems"]) >= 2
r, imgs = call("sheet_pages", source=pdir, rows="page")
assert r["showing"]["view"] == "whole page" and len(imgs) == 1
r, imgs = call("sheet_pages", source=pdir, page=1, rows=4)
assert '"page": 2' in r["next"], r["next"]
r, _ = call("sheet_pages", source=pdir, page=2)
assert r["systems_on_page"] == 3 and r["page_systems"][0].get("first_bar_est") == 17, r["page_systems"]
assert [s["bars_est"] for s in r["page_systems"]] == [4, 4, 3]
r, _ = call("sheet_pages", source=[os.path.join(pdir, "p2.jpg")], rows=[1, 2], zoom=1.5)
assert [s["system"] for s in r["showing"]["systems"]] == [1, 2]
wide, _ = page(W=3200, H=2400, sp=22, systems=((2, 6),))           # a long system comes back in strips
with open(os.path.join(tmp, "wide.png"), "wb") as f:
    f.write(png_gray(wide, filters=(0,)))
r, imgs = call("sheet_pages", source=os.path.join(tmp, "wide.png"))
s0 = r["showing"]["systems"][0]
assert s0.get("strips", 1) >= 2 and s0["strip_cuts"] == "at bar lines" and "strips" in r["showing"]["layout"], r
blank = np.full((400, 600), 250, np.uint8)
with open(os.path.join(tmp, "blank.png"), "wb") as f:
    f.write(png_gray(blank, filters=(0,)))
r, imgs = call("sheet_pages", source=os.path.join(tmp, "blank.png"))
assert r["systems_on_page"] == 0 and "no staves" in r["warning"] and len(imgs) == 1


# ============================================================================= 4. PDFs with page images
def make_pdf(path, pages, objstm=False, encrypt=False):
    """pages: [("flate", gray) | ("dct", gray) | ("bits", gray) | ("jbig2", None) | ("vector", None), rotate]."""
    objs = {}
    n = [2]

    def new(body):
        n[0] += 1; objs[n[0]] = body; return n[0]
    kids = []
    loose = []                                                   # page dicts that may go into an object stream
    for kind, gimg, rot in pages:
        res = "<< >>"
        if kind in ("flate", "dct", "bits", "jbig2"):
            if kind == "flate":
                h, w = gimg.shape
                raw = b"".join(b"\x02" + (gimg[y].astype(np.int16) - (gimg[y - 1] if y else 0)).astype(np.uint8)
                               .tobytes() for y in range(h))   # PNG 'Up' rows
                data = zlib.compress(raw)
                d = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray "
                     f"/BitsPerComponent 8 /Filter /FlateDecode /DecodeParms << /Predictor 15 /Colors 1 "
                     f"/BitsPerComponent 8 /Columns {w} >> /Length {len(data)} >>")
            elif kind == "dct":
                h, w = gimg.shape; data = jpeg_gray(gimg)
                d = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray "
                     f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>")
            elif kind == "bits":
                h, w = gimg.shape; data = zlib.compress(np.packbits(gimg > 128, axis=1).tobytes())
                d = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace [/ICCBased 999 0 R] "
                     f"/BitsPerComponent 1 /Filter [/FlateDecode] /Length {len(data)} >>")
            else:
                w = h = 400; data = b"\x00" * 50
                d = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray "
                     f"/BitsPerComponent 1 /Filter /JBIG2Decode /Length {len(data)} >>")
            im = new(d.encode() + b"\nstream\n" + data + b"\nendstream")
            res = f"<< /XObject << /Im0 {im} 0 R >> >>"
        cs = b"q 1 0 0 1 0 0 cm 0 0 m 100 100 l S Q" if kind == "vector" else b"q 600 0 0 800 0 0 cm /Im0 Do Q"
        c = new(f"<< /Length {len(cs)} >>".encode() + b"\nstream\n" + cs + b"\nendstream")
        pg = new(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources {res} /Contents {c} 0 R"
                 f"{f' /Rotate {rot}' if rot else ''} >>".encode())
        kids.append(pg); loose.append(pg)
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    objs[999] = b"<< /N 1 /Length 0 >>\nstream\n\nendstream"
    if objstm:                                                   # page dicts + catalog live in an object stream
        inner = [1, 2] + loose
        hdr = b""; body = b""
        for k in inner:
            hdr += f"{k} {len(body)} ".encode(); body += objs.pop(k) + b"\n"
        data = zlib.compress(hdr + body)
        objs[1000] = (f"<< /Type /ObjStm /N {len(inner)} /First {len(hdr)} /Filter /FlateDecode /Length {len(data)} >>"
                      .encode() + b"\nstream\n" + data + b"\nendstream")
        objs[1001] = b"<< /Type /XRef /Root 1 0 R /Size 1002 /W [1 2 1] /Length 0 >>\nstream\n\nendstream"
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n"); offs = {}
    for k in sorted(objs):
        offs[k] = len(out); out += f"{k} 0 obj\n".encode() + objs[k] + b"\nendobj\n"
    if not objstm:
        x = len(out)
        out += f"xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Root 1 0 R /Size {max(objs) + 1}".encode()
        out += (b" /Encrypt << /Filter /Standard >>" if encrypt else b"") + f" >>\nstartxref\n{x}\n%%EOF\n".encode()
    with open(path, "wb") as f:
        f.write(bytes(out))
    return path


gs = g1[::2, ::2].copy()
pdf1 = make_pdf(os.path.join(tmp, "scan.pdf"), [("flate", gs, 0), ("vector", None, 0),
                                                 ("dct", np.ascontiguousarray(np.rot90(g2[::2, ::2], 1)), 90),
                                                 ("bits", gs, 0)])
src = SH.open_source(pdf1)
assert len(src["pages"]) == 4 and any("no images" in n for n in src["notes"]), src["notes"]
assert np.array_equal(SH.page_gray(src["pages"][0]), gs)
p3 = SH.page_gray(src["pages"][2])
assert p3.shape == g2[::2, ::2].shape and np.abs(p3.astype(float) - g2[::2, ::2]).mean() < 6   # rotated back
p4 = SH.page_gray(src["pages"][3])
assert np.array_equal(p4 > 128, gs > 128)
r, imgs = call("sheet_pages", source=pdf1, page=3)
assert r["pages"] == 4 and r["systems_on_page"] == 3 and len(imgs) == 1, r
r, _ = call("sheet_pages", source=pdf1, page=2)
assert "vector" in r.get("_error", ""), r
pdf2 = make_pdf(os.path.join(tmp, "objstm.pdf"), [("flate", gs, 0)], objstm=True)
assert np.array_equal(SH.page_gray(SH.open_source(pdf2)["pages"][0]), gs)
for kind, want in (("vector", "MuseScore"), ("jbig2", "PNG")):
    pth = make_pdf(os.path.join(tmp, f"{kind}.pdf"), [(kind, None, 0)])
    try:
        sp_ = SH.open_source(pth)
        SH.page_gray(sp_["pages"][0])
        raise SystemExit(f"{kind} PDF must be refused")
    except SH.Unsupported as e:
        assert want in str(e), e
try:
    SH.open_source(make_pdf(os.path.join(tmp, "enc.pdf"), [("flate", gs, 0)], encrypt=True))
    raise SystemExit("encrypted PDF must be refused")
except SH.Unsupported as e:
    assert "encrypted" in str(e)

# ============================================================================= 5. sheet_import
ABC = """X:1
T:Little Tune
M:3/4
L:1/4
Q:1/4=96
K:G
V:1 name=Right
|: G A B | c2 B | A G F | G3 :|
V:2 name=Left clef=bass
|: G,,2 D, | C,2 G,, | D,2 D,, | G,,3 :|
"""
BAD = ABC.replace("| c2 B |", "| c2 B A |")                       # bar 2 of the right hand too long
r = SH.sheet_import(abc=ABC, dry_run=True)
assert r["written"] is False and r["summary"]["voices"]["Right"]["notes"] == 18 and r["summary"]["bars"] == 4, r
assert r["summary"]["fl_meter"] == "3/4" and "inline" in r
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):
    FIX = os.path.join(HERE, "corpus", "base.flp")
reloads = []
real_wop = m._with_open_project


def fake_wop(project, output, fn, _pending_ok=True):
    if m._EDIT_DEPTH:
        return fn()
    reloads.append(output); return fn()


m._with_open_project = fake_wop
proj = os.path.join(tmp, "song.flp"); shutil.copy(FIX, proj)
before = {p["pattern"] for p in m.flp_patterns(proj)}
r = SH.sheet_import(abc=BAD, project=proj, channel=0, pages=[{"page": 1, "system": 1, "first_bar": 1},
                                                              {"page": 1, "system": 2, "first_bar": 3}])
assert r["written"] is False and r["blocked"] and r["reread"][0]["bar"] == 2, r
assert r["reread"][0]["where"] == "page 1 system 1" and "Right" in r["reread"][0]["why"][0], r["reread"]
assert {p["pattern"] for p in m.flp_patterns(proj)} == before and not reloads
mid = os.path.join(tmp, "out", "tune.mid")
r = SH.sheet_import(abc=BAD, output_mid=mid, force=True)
assert r["written"] and r["forced"] and os.path.isfile(mid) and open(mid, "rb").read(4) == b"MThd"
try:
    SH.sheet_import(abc=ABC, output_mid=os.path.join(tempfile.gettempdir(), "outside_roots.mid"))
    raise SystemExit("MIDI outside FLH_ROOTS must be refused")
except (PermissionError, ValueError, RuntimeError):
    pass
r = SH.sheet_import(abc=ABC, project=proj)
assert r.get("needs_channel") and r["voices"] == ["Right", "Left"] and r["channels"], r
r = SH.sheet_import(abc=ABC, project=proj, channel={"Right": 0, "Left": 1}, set_tempo=True)
assert r["written"] and len(reloads) == 1, (r, reloads)            # ONE save/reload for every pattern + tempo
pats = {p["name"]: p for p in m.flp_patterns(proj)}
assert pats["Little Tune - Right"]["notes_per_channel"] == {0: 18}
assert pats["Little Tune - Left"]["notes_per_channel"] == {1: 14}
assert pats["Little Tune - Right"]["placed_at_bars"] == [1.0]
info = m.flp_project_info(proj)
assert info["time_sig_num"] == 3 and info["time_sig_beat"] == 4 and abs(info["tempo_bpm"] - 96) < 0.01, info
nn = m.flp_notes(proj, pattern="Little Tune - Right")
assert nn["notes"][0]["note"] == "G4" and nn["notes"][0]["at"] == "1.1", nn["notes"][:4]
assert nn["notes"][3]["at"] == "2.1", nn["notes"][:4]                  # 3/4 project: bar 2 = the 4th quarter
j = flh_store.journal_get(proj)["sheet"]
assert j["source"] == "abc" and j["patterns"] == ["Little Tune - Right", "Little Tune - Left"] and j["bars"] == 4
r = SH.sheet_import(abc=ABC, project=proj, channel="new")            # new channels, names unique
assert r["written"] and [p["pattern"] for p in r["patterns"]] == ["Little Tune - Right 2", "Little Tune - Left 2"]
newch = [p["channel"] for p in r["patterns"]]
chans = {c["index"]: c.get("name") for c in m.flp_list(proj)["channels"]}
assert chans[newch[0]] == "Right" and chans[newch[1]] == "Left", (newch, chans)
assert flh_store.journal_get(proj)["sheet"]["history"], "earlier import kept in history"
assert any("copies of channel" in n for n in r["notes"])
r = SH.sheet_import(abc=ABC, project=proj, channel=2)              # tempo kept: the project has notes now
assert r["written"] and "song_settings" not in r
# MusicXML (also as an .mxl zip) through the same path
XML = """<?xml version="1.0"?><score-partwise version="3.1"><part-list><score-part id="P1"><part-name>Flute
</part-name></score-part></part-list><part id="P1"><measure number="1"><attributes><divisions>1</divisions><key>
<fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef>
</attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>2</duration></note><note><pitch><step>E
</step><octave>5</octave></pitch><duration>2</duration></note></measure><measure number="2"><note><pitch><step>G
</step><octave>5</octave></pitch><duration>4</duration></note></measure></part></score-partwise>"""
xp = os.path.join(tmp, "flute.musicxml")
with open(xp, "w") as f:
    f.write(XML)
r = SH.sheet_import(musicxml=xp, dry_run=True)
assert r["source"].startswith("musicxml") and sum(v["notes"] for v in r["summary"]["voices"].values()) == 3, r
import zipfile  # noqa: E402
mxl = os.path.join(tmp, "flute.mxl")
with zipfile.ZipFile(mxl, "w") as z:
    z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container><rootfiles><rootfile full-path="s.xml"/>'
               '</rootfiles></container>')
    z.writestr("s.xml", XML)
r = SH.sheet_import(musicxml=mxl, dry_run=True)
assert sum(v["notes"] for v in r["summary"]["voices"].values()) == 3, r

# ============================================================================= 6. sheet_diff / sheet_explain
r = SH.sheet_diff(ABC, proj, pattern="Little Tune - Right")
assert r["same"] and r["voice"].startswith("Right"), r
r = SH.sheet_diff(ABC.replace("| A G F |", "| A G E |"), proj, pattern="Little Tune - Right")
assert not r["same"] and r["bars"][0]["bar"] == 3 and "E4@3" in r["bars"][0]["only_reading"], r
r = SH.sheet_diff(ABC, ABC.replace("| A G F |", "| A G E |"))
assert r["same"] is False, r
r, _ = call("sheet_diff", a=ABC, b=BAD)
assert r["same"] is False
ABC2 = """X:2
T:Endings
M:6/8
L:1/8
Q:3/8=60
K:Ddor
|: A | d2f a2f |1 e3 e2 :|2 d3 d2 |]
"""
ex = SH.sheet_explain(abc=ABC2)["explanation"]
txt = " ".join(ex)
assert "pickup" in txt and "compound" in txt and "ending 1 is bar 2" in txt and "ending 2 is bar 3" in txt, ex
assert "played twice" in txt and "D dorian" in txt, ex
r, _ = call("sheet_explain", abc=ABC)
assert any("Left" in s and "bass clef" in s for s in r["explanation"]), r

# ============================================================================= 7. reference audio
ABC3 = """X:1
T:Check
M:4/4
L:1/8
Q:1/4=100
K:C
V:1
C2E2 G2E2 | F2A2 c2A2 | G2B2 d2B2 | c4 G4 | E2G2 c2G2 | A2c2 e2c2 | F2A2 d2A2 | G4 B4 | c2G2 E2G2 | F2D2 B,2D2 | C8 |
V:2 clef=bass
C,8 | F,8 | G,8 | C,8 | C,8 | A,,8 | D,8 | G,,8 | C,8 | G,,8 | C,8 |
"""
sc3, _ = SH.load_score(abc=ABC3)
v3, _ = SH._inline(sc3)
FS = 22050


def render(voices, bpm, change=None, only=None):
    qs = 60 / bpm; out = np.zeros(int((11 * 4 + 2) * qs * FS))
    for v, ns_ in voices.items():
        if only and v != only:
            continue
        for b, ln, p, vel in ns_:
            p = change(int(b), p) if change else p
            t0 = (b - 1) * 4 * qs + 0.2; d = ln * 4 * qs
            n_ = int(d * FS); t = np.arange(n_) / FS; f = 440 * 2 ** ((p - 69) / 12)
            y = sum(a * np.sin(2 * np.pi * f * (h + 1) * t) for h, a in enumerate((1, .5, .3, .15)))
            env = np.minimum(1, t / 0.01) * np.exp(-t / 1.2) * np.minimum(1, (d - t) / 0.03)
            i = int(t0 * FS); out[i:i + n_] += 0.2 * y * env
    return out + 0.001 * RNG.standard_normal(len(out))


for bpm, wb in ((100, 6), (84, 3)):                              # tempo-agnostic: the score says 100
    rr = SH.compare_audio(sc3, v3, (render(v3, bpm, lambda b, p: p + 1 if b == wb else p), FS))
    assert [f["bar"] for f in rr["flags"]] == [wb], (bpm, rr["flags"])
    assert abs(rr["aligned_bpm"] - bpm) < 3, rr
rr = SH.compare_audio(sc3, v3, (render(v3, 112), FS))
assert rr["flags"] == [], rr["flags"]
rr = SH.compare_audio(sc3, v3, (render(v3, 100, lambda b, p: 63 if (b == 5 and p == 64) else p), FS))
assert [f["bar"] for f in rr["flags"]] == [5] and "accidental" in rr["flags"][0]["detail"], rr["flags"]
mono = {"1": v3["1"]}
rr = SH.compare_audio(sc3, mono, (render(v3, 100, lambda b, p: p + 12 if b == 4 else p, only="1"), FS))
assert any(f["bar"] == 4 and "octave" in f["detail"] for f in rr["flags"]), rr["flags"]
wav = os.path.join(tmp, "take.wav")
y = render(v3, 100, lambda b, p: p + 1 if b == 7 else p)
with wave.open(wav, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(FS)
    w.writeframes((np.clip(y / max(1e-9, np.abs(y).max()) * 0.8, -1, 1) * 32767).astype("<i2").tobytes())
r = SH.sheet_import(abc=ABC3, reference_audio=wav, dry_run=True)
assert r["audio_check"]["bars_flagged"] == 1 and r["reread"][0]["bar"] == 7, r.get("audio_check")
assert "recording disagrees" in r["reread"][0]["why"][0]
m._with_open_project = real_wop
print("ok")
