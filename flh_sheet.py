"""flh_sheet: sheet music in - read scanned/engraved pages system by system, check a reading exactly, write it to FL.

Workflow (Claude reads the notes from pictures; this module makes the pictures sharp and the checking exact):
1. sheet_pages(source)  PNG / JPEG (baseline + progressive) pages or a PDF with scanned page images (DCTDecode /
                        FlateDecode / LZW / ASCII filters; JBIG2, CCITT fax and JPEG 2000 are not decoded - export
                        those pages as PNG). Staff lines are found by horizontal projection (after a small deskew),
                        five evenly spaced lines make a staff, staves joined by a left barline/brace make a system,
                        bar lines are vertical strokes spanning every staff of the system. Each call returns ONE image
                        (the dispatcher sends one image per result): a zoomed crop of one or more systems with small
                        red bar numbers above the detected bar lines (estimates), plus counts and a "next" call.
                        Decoded pages and their layout are cached, so stepping through systems is fast.
                        Frame tools (coordinates in the page scaled to `frame` px wide, default 1000): systems=true
                        (boxes, staff lines, bar-line x as text), rows=S + bars='a-b' / crop=[x0, y0, x1, y1]
                        (upscaled crops, save= writes the PNG), split=N (page ranges for N parallel readers balanced
                        by detected bars, each with the brief from data/sheet/reader_brief.md).
2. sheet_import(abc | musicxml | score)  score = per-bar score data (flh-score/1, sheet_notation.parse_score_data:
                        built through generated ABC, so every ABC check applies, with the data's bar numbers).
                        Parse (sheet_notation) + validate; blocking issues (severity >= med; strict: any)
                        come back as a "re-read these bars" list (with page/system when `pages` maps bars to systems)
                        and nothing is written unless force=true. Otherwise: MIDI (output_mid, inside FLH_ROOTS)
                        and/or one named pattern per voice in the project in ONE flp_edit (lazy-aware, guarded),
                        tempo/meter when the project is empty (or set_tempo=true), and the source in the song journal.
3. reference_audio      a recording of the piece: chroma per frame (flh_transcribe's chroma) aligned to the score's
                        chroma by DTW (tempo-agnostic), then bars whose pitch content disagrees are flagged with
                        missing/extra pitch classes and a confidence. Chroma can't hear octaves and dense polyphony
                        blurs it: a flag means "look again", no flag doesn't prove the reading right. A monophonic
                        score also gets an octave check from the pitch tracker.
4. sheet_diff(a, b)     two readings (ABC/MusicXML; score data: per bar and part) or a reading vs a project part.
   sheet_merge(readings) page-range readings (score data) -> one, with overlap / conflict / missing-bar checks.
5. sheet_explain        the score in plain words: key, meter, tempo, form, repeats/endings, range per voice.

Notation (parsing, validation, MIDI) lives in sheet_notation.py (pure Python), imported lazily.
Follows the extension contract in flh_store.py.
"""
import json
import math
import os
import re
import struct
import time
import zlib

class _LazyNumpy:
    """numpy loads on first use (keeps server start fast on weak PCs); then the global `np` is numpy itself."""
    def __getattr__(self, name):
        import numpy
        globals()["np"] = numpy
        return getattr(numpy, name)


np = _LazyNumpy()

import flh_common as C

H = {}
JKEY = "sheet"                                     # song journal key owned by this module
IMG_EXT = (".png", ".jpg", ".jpeg", ".jpe", ".jfif")
_CACHE = {}                                        # (path, mtime, size) -> source info; page layout cache inside
_CACHE_PAGES = []                                  # LRU of (key, page) holding decoded pixels (big)
_CACHE_MAX_PAGES = 3


class Unsupported(ValueError):
    """An image or PDF variant this module can't decode; the message says what to do instead."""


# ============================================================================= PNG
def _paeth_row(f, prior, bpp):
    out = bytearray(len(f))
    for i in range(len(f)):
        a = out[i - bpp] if i >= bpp else 0
        b = prior[i]
        c = prior[i - bpp] if i >= bpp else 0
        p = a + b - c
        pa = p - a if p >= a else a - p
        pb = p - b if p >= b else b - p
        pc = p - c if p >= c else c - p
        out[i] = (f[i] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
    return out


def _avg_row(f, prior, bpp):
    out = bytearray(len(f))
    for i in range(len(f)):
        a = out[i - bpp] if i >= bpp else 0
        out[i] = (f[i] + ((a + prior[i]) >> 1)) & 255
    return out


def _png_unfilter(raw, w, h, bpp, rowbytes):
    """Undo the PNG row filters -> (h, rowbytes) uint8."""
    out = np.zeros((h, rowbytes), np.uint8)
    prior = bytes(rowbytes)
    pos = 0
    for y in range(h):
        ft = raw[pos]; f = raw[pos + 1:pos + 1 + rowbytes]; pos += 1 + rowbytes
        if len(f) < rowbytes:
            raise ValueError("PNG data is truncated")
        if ft == 0:
            row = f
        elif ft == 1:                                  # Sub: running sum per channel (mod 256)
            a = np.frombuffer(f, np.uint8).astype(np.int64)
            pad = (-len(a)) % bpp
            a = np.concatenate([a, np.zeros(pad, np.int64)]).reshape(-1, bpp)
            row = (np.cumsum(a, axis=0) & 255).astype(np.uint8).ravel()[:rowbytes].tobytes()
        elif ft == 2:
            row = ((np.frombuffer(f, np.uint8).astype(np.uint16) + np.frombuffer(prior, np.uint8)) & 255) \
                .astype(np.uint8).tobytes()
        elif ft == 3:
            row = bytes(_avg_row(f, prior, bpp))
        elif ft == 4:
            if not any(prior):                         # Paeth over a zero row is Sub
                a = np.frombuffer(f, np.uint8).astype(np.int64)
                pad = (-len(a)) % bpp
                a = np.concatenate([a, np.zeros(pad, np.int64)]).reshape(-1, bpp)
                row = (np.cumsum(a, axis=0) & 255).astype(np.uint8).ravel()[:rowbytes].tobytes()
            else:
                row = bytes(_paeth_row(f, prior, bpp))
        else:
            raise ValueError(f"PNG row filter {ft} is invalid (damaged file?)")
        out[y] = np.frombuffer(row, np.uint8)
        prior = row
    return out


def _samples(rows, w, bitdepth, nch):
    """Unpack filtered-free rows -> (h, w, nch) integer samples."""
    h = rows.shape[0]
    if bitdepth == 8:
        return rows[:, :w * nch].reshape(h, w, nch).astype(np.int32)
    if bitdepth == 16:
        b = rows[:, :w * nch * 2].reshape(h, w * nch, 2).astype(np.int32)
        return (b[..., 0] * 256 + b[..., 1]).reshape(h, w, nch)
    bits = np.unpackbits(rows, axis=1)                 # 1/2/4-bit: MSB first
    per = bitdepth
    n = w * nch
    bits = bits[:, :n * per].reshape(h, n, per)
    vals = np.zeros((h, n), np.int32)
    for k in range(per):
        vals = vals * 2 + bits[:, :, k]
    return vals.reshape(h, w, nch)


def _to_gray(s, maxv, kind, alpha=None):
    """Samples -> gray uint8 (alpha composited on white)."""
    s = s.astype(np.float32) * (255.0 / maxv)
    if kind == "rgb":
        g = 0.299 * s[..., 0] + 0.587 * s[..., 1] + 0.114 * s[..., 2]
    else:
        g = s[..., 0]
    if alpha is not None:
        a = alpha.astype(np.float32) / maxv
        g = g * a + 255.0 * (1 - a)
    return np.clip(g + 0.5, 0, 255).astype(np.uint8)


def png_decode(data):
    """PNG bytes -> gray uint8 (h, w). Every color type and bit depth, Adam7 interlace, palettes, alpha on white."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos, idat, plte, trns = 8, [], None, None
    w = h = None
    while pos + 8 <= len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + ln]; pos += 12 + ln
        if typ == b"IHDR":
            if len(body) < 13:
                raise ValueError("PNG header is damaged")
            w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", body[:13])
            if not 0 < w * h <= 1 << 28:                          # a damaged size asked numpy for 30+ GB
                raise ValueError(f"PNG size {w}x{h} is damaged or too large to read")
        elif typ == b"PLTE":
            plte = np.frombuffer(body, np.uint8).reshape(-1, 3)
        elif typ == b"tRNS":
            trns = body
        elif typ == b"IDAT":
            idat.append(body)
        elif typ == b"IEND":
            break
    if w is None:
        raise ValueError("PNG has no header")
    raw = zlib.decompress(b"".join(idat))
    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ct)
    if nch is None:
        raise ValueError(f"PNG color type {ct} is invalid")
    bpp = max(1, nch * bd // 8)

    def decode_pass(raw_, pw, ph, off):
        if pw == 0 or ph == 0:
            return None, off
        rb = (pw * nch * bd + 7) // 8
        rows = _png_unfilter(raw_[off:], pw, ph, bpp, rb)
        return _samples(rows, pw, bd, nch), off + ph * (rb + 1)

    if il == 1:                                        # Adam7
        S = np.zeros((h, w, nch), np.int32); off = 0
        for x0, y0, dx, dy in ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
                               (0, 1, 1, 2)):
            pw = (w - x0 + dx - 1) // dx if w > x0 else 0; ph = (h - y0 + dy - 1) // dy if h > y0 else 0
            s, off = decode_pass(raw, pw, ph, off)
            if s is not None:
                S[y0::dy, x0::dx] = s
    else:
        S, _ = decode_pass(raw, w, h, 0)
    maxv = (1 << bd) - 1
    if ct == 3:
        if plte is None:
            raise ValueError("palette PNG without a palette")
        idx = np.clip(S[..., 0], 0, len(plte) - 1)
        rgb = plte[idx].astype(np.int32)
        alpha = None
        if trns:
            ta = np.full(len(plte), 255, np.int32); ta[:len(trns)] = np.frombuffer(trns, np.uint8)[:len(plte)]
            alpha = ta[idx]
        return _to_gray(rgb, 255, "rgb", alpha)
    if ct == 0:
        return _to_gray(S, maxv, "gray")
    if ct == 2:
        return _to_gray(S, maxv, "rgb")
    if ct == 4:
        return _to_gray(S[..., :1], maxv, "gray", S[..., 1])
    return _to_gray(S[..., :3], maxv, "rgb", S[..., 3])


# ============================================================================= JPEG (baseline + progressive)
ZZ = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6,
      7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31,
      39, 46, 53, 60, 61, 54, 47, 55, 62, 63]                   # list: indexes numpy arrays the same way
_IDCT_CACHE = []


def _idct():
    """8x8 inverse-DCT basis (built on first JPEG decode, so importing this module never loads numpy)."""
    if not _IDCT_CACHE:
        _IDCT_CACHE.append(np.array([[(math.sqrt(0.5) if u == 0 else 1.0) * math.cos((2 * x + 1) * u * math.pi / 16) / 2
                                      for x in range(8)] for u in range(8)]))
    return _IDCT_CACHE[0]


def __getattr__(name):                                                   # SH._IDCT keeps working for callers/tests
    if name == "_IDCT":
        return _idct()
    raise AttributeError(name)


def _huff_table(counts, symbols):
    """Canonical Huffman -> lookup list indexed by the next 16 bits: (symbol, length)."""
    look = [None] * 65536
    code, k = 0, 0
    for ln in range(1, 17):
        for _ in range(counts[ln - 1]):
            if k >= len(symbols):
                break
            sh = 16 - ln
            start = code << sh
            look[start:start + (1 << sh)] = [(symbols[k], ln)] * (1 << sh)
            code += 1; k += 1
        code <<= 1
    return look


class _Bits:
    """MSB-first bit reader over one entropy-coded segment (byte stuffing already removed)."""
    __slots__ = ("b", "p", "n")

    def __init__(self, data):
        self.b = data + b"\x00\x00\x00\x00"; self.p = 0; self.n = len(data) * 8

    def peek16(self):
        p = self.p; i = p >> 3; b = self.b
        return ((b[i] << 16 | b[i + 1] << 8 | b[i + 2]) >> (8 - (p & 7))) & 0xFFFF

    def get(self, n):
        if n == 0:
            return 0
        p = self.p; i = p >> 3; b = self.b
        v = (b[i] << 24 | b[i + 1] << 16 | b[i + 2] << 8 | b[i + 3]) >> (32 - (p & 7) - n)
        self.p = p + n
        return v & ((1 << n) - 1)

    def decode(self, look):
        e = look[self.peek16()]
        if e is None:
            raise ValueError("bad Huffman code in the JPEG data (damaged file?)")
        self.p += e[1]
        return e[0]


def _extend(v, s):
    return v - (1 << s) + 1 if s and v < (1 << (s - 1)) else v


def _scan_segments(data, pos):
    """The entropy-coded data after an SOS header: [segments split at RST markers], position of the next marker."""
    segs, cur, i, n = [], bytearray(), pos, len(data)
    while i < n:
        j = data.find(b"\xff", i)
        if j < 0:
            cur += data[i:]; i = n; break
        cur += data[i:j]
        nxt = data[j + 1] if j + 1 < n else 0
        if nxt == 0:
            cur.append(0xFF); i = j + 2
        elif 0xD0 <= nxt <= 0xD7:
            segs.append(bytes(cur)); cur = bytearray(); i = j + 2
        elif nxt == 0xFF:
            i = j + 1                                  # fill byte
        else:
            i = j; break
    segs.append(bytes(cur))
    return segs, i


def jpeg_decode(data):
    """JPEG bytes -> gray uint8 (h, w). Baseline and extended sequential (Huffman) and progressive; 8-bit (and
    12-bit, scaled) samples; 1 (gray), 3 (YCbCr or RGB) and 4 (CMYK/YCCK, approximated) components.
    Lossless, hierarchical and arithmetic-coded JPEGs raise Unsupported."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG file")
    pos, n = 2, len(data)
    qt, dc_t, ac_t = {}, {}, {}
    frame, comps, ri, adobe, progressive = None, [], 0, None, False
    coefs = {}
    while pos < n:
        if data[pos] != 0xFF:
            pos += 1; continue
        m = data[pos + 1]; pos += 2
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7 or m == 0xFF:
            if m == 0xFF:
                pos -= 1
            continue
        if m == 0xD9:
            break
        ln = struct.unpack(">H", data[pos:pos + 2])[0]; seg = data[pos + 2:pos + ln]; pos += ln
        if m == 0xDB:                                  # quantization tables
            k = 0
            while k < len(seg):
                pq, tq = seg[k] >> 4, seg[k] & 15; k += 1
                if pq:
                    q = np.frombuffer(seg[k:k + 128], ">u2").astype(np.float64); k += 128
                else:
                    q = np.frombuffer(seg[k:k + 64], np.uint8).astype(np.float64); k += 64
                t = np.zeros(64); t[ZZ] = q; qt[tq] = t
        elif m == 0xC4:                                # Huffman tables
            k = 0
            while k < len(seg):
                tc, th = seg[k] >> 4, seg[k] & 15
                counts = list(seg[k + 1:k + 17]); tot = sum(counts)
                syms = list(seg[k + 17:k + 17 + tot]); k += 17 + tot
                (ac_t if tc else dc_t)[th] = _huff_table(counts, syms)
        elif m == 0xDD:
            ri = struct.unpack(">H", seg[:2])[0]
        elif m == 0xEE and seg[:5] == b"Adobe":
            adobe = seg[11] if len(seg) > 11 else 0
        elif m in (0xC0, 0xC1, 0xC2):
            progressive = m == 0xC2
            prec, Y, X, nc = struct.unpack(">BHHB", seg[:6])
            comps = []
            for c in range(nc):
                cid, hv, tq = seg[6 + 3 * c], seg[7 + 3 * c], seg[8 + 3 * c]
                comps.append({"id": cid, "h": hv >> 4, "v": hv & 15, "tq": tq})
            hmax = max(c["h"] for c in comps); vmax = max(c["v"] for c in comps)
            mx = (X + 8 * hmax - 1) // (8 * hmax); my = (Y + 8 * vmax - 1) // (8 * vmax)
            for c in comps:
                c["bw"] = mx * c["h"]; c["bh"] = my * c["v"]                     # MCU-padded block grid
                c["cw"] = (int(math.ceil(X * c["h"] / hmax)) + 7) // 8           # the component's own blocks
                c["ch"] = (int(math.ceil(Y * c["v"] / vmax)) + 7) // 8
                coefs[c["id"]] = [0] * (c["bw"] * c["bh"] * 64)
            frame = {"prec": prec, "X": X, "Y": Y, "mx": mx, "my": my, "hmax": hmax, "vmax": vmax}
            if Y == 0:
                raise Unsupported("this JPEG gives its height later in the file (DNL marker): not supported - "
                                  "re-save it as PNG")
        elif m in (0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            kind = "lossless" if m in (0xC3, 0xC7, 0xCB, 0xCF) else ("arithmetic-coded" if m >= 0xC9 else
                                                                       "hierarchical")
            raise Unsupported(f"{kind} JPEG isn't supported: export/re-save the page as PNG (or a normal JPEG)")
        elif m == 0xDA:
            if frame is None:
                raise ValueError("JPEG scan before the frame header")
            ns_ = seg[0]
            sc = []
            for c in range(ns_):
                cid, t = seg[1 + 2 * c], seg[2 + 2 * c]
                comp = next(x for x in comps if x["id"] == cid)
                sc.append((comp, t >> 4, t & 15))
            Ss, Se, A = seg[1 + 2 * ns_], seg[2 + 2 * ns_], seg[3 + 2 * ns_]
            Ah, Al = A >> 4, A & 15
            segs, pos = _scan_segments(data, pos)
            _decode_scan(frame, sc, coefs, dc_t, ac_t, segs, ri, progressive, Ss, Se, Ah, Al)
    if frame is None:
        raise ValueError("JPEG without a frame header")
    planes = {}
    for c in comps:
        q = qt.get(c["tq"])
        if q is None:
            raise ValueError("JPEG references a missing quantization table")
        nat = np.array(coefs[c["id"]], np.float64).reshape(-1, 64)    # stored in natural order
        nat *= q
        F = nat.reshape(-1, 8, 8)
        D = _idct()
        px = np.matmul(np.matmul(D.T, F), D)                      # separable 8x8 inverse DCT
        shift = 2048 if frame["prec"] == 12 else 128
        px = px + shift
        if frame["prec"] == 12:
            px = px / 16.0
        img = px.reshape(c["bh"], c["bw"], 8, 8).transpose(0, 2, 1, 3).reshape(c["bh"] * 8, c["bw"] * 8)
        fy, fx = frame["vmax"] // c["v"], frame["hmax"] // c["h"]
        if fy > 1 or fx > 1:
            img = np.repeat(np.repeat(img, fy, 0), fx, 1)
        planes[c["id"]] = img[:frame["Y"], :frame["X"]]
    P = [planes[c["id"]] for c in comps]
    if len(P) == 1:
        g = P[0]
    elif len(P) == 3:
        rgb = adobe == 0 or [c["id"] for c in comps] == [82, 71, 66]
        g = (0.299 * P[0] + 0.587 * P[1] + 0.114 * P[2]) if rgb else P[0]
    elif len(P) == 4:                                  # CMYK / YCCK (Adobe: stored inverted) - approximate gray
        if adobe == 2:
            g = P[0] * (P[3] / 255.0)
        else:
            g = (255.0 - (0.3 * (255 - P[0]) + 0.59 * (255 - P[1]) + 0.11 * (255 - P[2]))) * (P[3] / 255.0)
    else:
        g = P[0]
    return np.clip(g + 0.5, 0, 255).astype(np.uint8)


def _decode_scan(frame, sc, coefs, dc_t, ac_t, segs, ri, progressive, Ss, Se, Ah, Al):
    zz = list(ZZ)
    single = len(sc) == 1
    if single:                                         # non-interleaved: the component's own block grid
        comp = sc[0][0]
        units = [(comp, by, bx) for by in range(comp["ch"]) for bx in range(comp["cw"])]
        mcus = [[u] for u in units]
    else:
        mcus = []
        for my in range(frame["my"]):
            for mx in range(frame["mx"]):
                mcu = []
                for comp, _, _ in sc:
                    for v in range(comp["v"]):
                        for h in range(comp["h"]):
                            mcu.append((comp, my * comp["v"] + v, mx * comp["h"] + h))
                mcus.append(mcu)
    tabs = {c["id"]: (dc_t.get(td), ac_t.get(ta)) for c, td, ta in sc}
    seg_i = 0
    bits = _Bits(segs[0])
    pred = {c["id"]: 0 for c, _, _ in sc}
    eobrun = [0]
    p1 = 1 << Al; m1 = -1 << Al
    for mi, mcu in enumerate(mcus):
        if ri and mi and mi % ri == 0:
            seg_i += 1
            bits = _Bits(segs[seg_i] if seg_i < len(segs) else b"")
            for k in pred:
                pred[k] = 0
            eobrun[0] = 0
        for comp, by, bx in mcu:
            co = coefs[comp["id"]]; base = (by * comp["bw"] + bx) * 64
            dct, act = tabs[comp["id"]]
            if not progressive:                        # hot loop: the bit reader inlined
                bb = bits.b; p = bits.p
                i = p >> 3
                e = dct[((bb[i] << 16 | bb[i + 1] << 8 | bb[i + 2]) >> (8 - (p & 7))) & 0xFFFF]
                if e is None:
                    raise ValueError("bad Huffman code in the JPEG data (damaged file?)")
                p += e[1]; s = e[0]
                if s:
                    i = p >> 3
                    v = ((bb[i] << 24 | bb[i + 1] << 16 | bb[i + 2] << 8 | bb[i + 3]) >> (32 - (p & 7) - s)) & \
                        ((1 << s) - 1)
                    p += s
                    pred[comp["id"]] += v - (1 << s) + 1 if v < (1 << (s - 1)) else v
                co[base] = pred[comp["id"]]
                k = 1
                while k < 64:
                    i = p >> 3
                    e = act[((bb[i] << 16 | bb[i + 1] << 8 | bb[i + 2]) >> (8 - (p & 7))) & 0xFFFF]
                    if e is None:
                        raise ValueError("bad Huffman code in the JPEG data (damaged file?)")
                    p += e[1]; rs = e[0]; s = rs & 15
                    if s == 0:
                        if rs != 0xF0:
                            break
                        k += 16; continue
                    k += rs >> 4
                    if k > 63:
                        break
                    i = p >> 3
                    v = ((bb[i] << 24 | bb[i + 1] << 16 | bb[i + 2] << 8 | bb[i + 3]) >> (32 - (p & 7) - s)) & \
                        ((1 << s) - 1)
                    p += s
                    co[base + zz[k]] = v - (1 << s) + 1 if v < (1 << (s - 1)) else v
                    k += 1
                bits.p = p
            elif Ss == 0:                              # DC scans
                if Ah == 0:
                    s = bits.decode(dct)
                    pred[comp["id"]] += _extend(bits.get(s), s)
                    co[base] = pred[comp["id"]] * p1
                elif bits.get(1):
                    co[base] |= p1
            elif Ah == 0:                              # AC first pass
                if eobrun[0] > 0:
                    eobrun[0] -= 1; continue
                k = Ss
                while k <= Se:
                    rs = bits.decode(act); r, s = rs >> 4, rs & 15
                    if s == 0:
                        if r < 15:
                            eobrun[0] = (1 << r) - 1 + (bits.get(r) if r else 0)
                            break
                        k += 16; continue
                    k += r
                    if k > 63:
                        break
                    co[base + zz[k]] = _extend(bits.get(s), s) * p1
                    k += 1
            else:                                      # AC refinement (libjpeg's decode_mcu_AC_refine)
                k = Ss
                if eobrun[0] == 0:
                    while k <= Se:
                        rs = bits.decode(act); r, s = rs >> 4, rs & 15
                        if s:
                            s = p1 if bits.get(1) else m1
                        elif r != 15:
                            eobrun[0] = (1 << r) + (bits.get(r) if r else 0)
                            break
                        while k <= Se:
                            z = base + zz[k]
                            if co[z] != 0:
                                if bits.get(1) and (co[z] & p1) == 0:
                                    co[z] += p1 if co[z] >= 0 else m1
                            else:
                                r -= 1
                                if r < 0:
                                    break
                            k += 1
                        if s and k <= 63:
                            co[base + zz[k]] = s
                        k += 1
                if eobrun[0] > 0:
                    while k <= Se:
                        z = base + zz[k]
                        if co[z] != 0 and bits.get(1) and (co[z] & p1) == 0:
                            co[z] += p1 if co[z] >= 0 else m1
                        k += 1
                    eobrun[0] -= 1


# ============================================================================= PDF (scanned pages: embedded images)
class _Ref(tuple):
    """An indirect reference (num, gen)."""


class _Name(str):
    """A PDF name (without the slash)."""


_WS = b" \t\r\n\x0c\x00"
_DELIM = b"()<>[]{}/%"


def _skip_ws(d, i):
    n = len(d)
    while i < n:
        c = d[i]
        if c in _WS:
            i += 1
        elif c == 0x25:                                # % comment
            while i < n and d[i] not in b"\r\n":
                i += 1
        else:
            break
    return i


def _token_end(d, i):
    n = len(d)
    while i < n and d[i] not in _WS and d[i] not in _DELIM:
        i += 1
    return i


def _pdf_value(d, i, depth=0):
    """Parse one PDF object at i -> (value, next index). Dicts -> dict (name keys as str), arrays -> list,
    names -> _Name, refs -> _Ref, strings -> bytes."""
    if depth > 64:
        raise ValueError("PDF objects nested too deep")
    i = _skip_ws(d, i)
    c = d[i:i + 1]
    if c == b"<" and d[i + 1:i + 2] == b"<":
        i += 2; out = {}
        while True:
            i = _skip_ws(d, i)
            if d[i:i + 2] == b">>":
                return out, i + 2
            if d[i:i + 1] != b"/":
                raise ValueError(f"PDF dictionary key expected at byte {i}")
            k, i = _pdf_value(d, i, depth + 1)
            v, i = _pdf_value(d, i, depth + 1)
            out[str(k)] = v
    if c == b"[":
        i += 1; out = []
        while True:
            i = _skip_ws(d, i)
            if d[i:i + 1] == b"]":
                return out, i + 1
            v, i = _pdf_value(d, i, depth + 1)
            out.append(v)
    if c == b"/":
        j = _token_end(d, i + 1)
        raw = d[i + 1:j]
        name = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)
        return _Name(name.decode("latin-1")), j
    if c == b"(":
        i += 1; depthp = 1; out = bytearray()
        while i < len(d):
            ch = d[i]
            if ch == 0x5C:                             # backslash escape
                nx = d[i + 1:i + 2]
                esc = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}.get(nx)
                if esc is not None:
                    out += esc; i += 2
                elif nx and nx in b"01234567":
                    m = re.match(rb"[0-7]{1,3}", d[i + 1:i + 4]); out.append(int(m.group(0), 8) & 255)
                    i += 1 + len(m.group(0))
                elif nx in (b"\r", b"\n"):
                    i += 2 + (1 if d[i + 1:i + 3] == b"\r\n" else 0)
                else:
                    out += nx; i += 2
                continue
            if ch == 0x28:
                depthp += 1
            elif ch == 0x29:
                depthp -= 1
                if depthp == 0:
                    return bytes(out), i + 1
            out.append(ch); i += 1
        raise ValueError("unterminated PDF string")
    if c == b"<":
        j = d.index(b">", i)
        hx = re.sub(rb"\s", b"", d[i + 1:j])
        if len(hx) % 2:
            hx += b"0"
        return bytes.fromhex(hx.decode("ascii")), j + 1
    j = _token_end(d, i)
    tok = d[i:j]
    if not tok:
        raise ValueError(f"unexpected PDF byte {d[i:i + 1]!r} at {i}")
    if tok == b"true":
        return True, j
    if tok == b"false":
        return False, j
    if tok == b"null":
        return None, j
    try:
        num = int(tok)
    except ValueError:
        try:
            return float(tok), j
        except ValueError:
            return _Name(tok.decode("latin-1")), j            # an operator / keyword
    m = re.match(rb"\s+(\d+)\s+R(?![^\s/\[\]<>()%])", d[j:j + 24])
    if m:
        return _Ref((num, int(m.group(1)))), j + m.end()
    return num, j


def _lzw_decode(data, early=1):
    out = bytearray()
    table = [bytes([i]) for i in range(256)] + [b"", b""]
    bits, nbits, code_len, prev = 0, 0, 9, None
    for byte in data:
        bits = (bits << 8) | byte; nbits += 8
        while nbits >= code_len:
            nbits -= code_len
            code = (bits >> nbits) & ((1 << code_len) - 1)
            if code == 256:
                table = table[:258]; code_len = 9; prev = None; continue
            if code == 257:
                return bytes(out)
            if code < len(table):
                entry = table[code]
                if prev is not None:
                    table.append(prev + entry[:1])
            elif prev is not None:
                entry = prev + prev[:1]; table.append(entry)
            else:
                raise ValueError("bad LZW data")
            out += entry; prev = entry
            if len(table) + early >= (1 << code_len) and code_len < 12:
                code_len += 1
    return bytes(out)


def _a85_decode(data):
    data = re.sub(rb"\s", b"", data)
    if data.startswith(b"<~"):
        data = data[2:]
    data = data.split(b"~>")[0]
    out = bytearray(); grp = []
    for ch in data:
        if ch == 0x7A and not grp:                     # 'z' = four zero bytes
            out += b"\0\0\0\0"; continue
        grp.append(ch - 33)
        if len(grp) == 5:
            v = 0
            for g in grp:
                v = v * 85 + g
            out += struct.pack(">I", v & 0xFFFFFFFF); grp = []
    if grp:
        n = len(grp)
        grp += [84] * (5 - n); v = 0
        for g in grp:
            v = v * 85 + g
        out += struct.pack(">I", v & 0xFFFFFFFF)[:n - 1]
    return bytes(out)


def _rl_decode(data):
    out = bytearray(); i = 0
    while i < len(data):
        n = data[i]
        if n == 128:
            break
        if n < 128:
            out += data[i + 1:i + 2 + n]; i += 2 + n
        else:
            out += data[i + 1:i + 2] * (257 - n); i += 2
    return bytes(out)


def _predictor(data, parms):
    """Undo a PNG (>= 10) or TIFF (2) predictor of a Flate/LZW stream."""
    pred = int((parms or {}).get("Predictor", 1) or 1)
    if pred < 2:
        return data
    cols = int(parms.get("Columns", 1) or 1); colors = int(parms.get("Colors", 1) or 1)
    bpc = int(parms.get("BitsPerComponent", 8) or 8)
    rowbytes = (cols * colors * bpc + 7) // 8; bpp = max(1, colors * bpc // 8)
    if pred == 2:
        if bpc != 8:
            raise Unsupported("TIFF predictor with non-8-bit samples isn't supported: export the page as PNG")
        a = np.frombuffer(data[:len(data) // rowbytes * rowbytes], np.uint8).reshape(-1, cols, colors).astype(np.int64)
        return (np.cumsum(a, axis=1) & 255).astype(np.uint8).tobytes()
    h = len(data) // (rowbytes + 1)
    return _png_unfilter(data, cols, h, bpp, rowbytes).tobytes()


class PDF:
    """Just enough of a PDF reader to find each page's images: objects (also inside object streams), the page tree
    (inherited Resources, Rotate), image XObjects (also inside Form XObjects)."""

    def __init__(self, data):
        self.d = data
        if not data.lstrip()[:5] == b"%PDF-" and b"%PDF-" not in data[:1024]:
            raise ValueError("not a PDF file")
        self.offsets = {}
        for m in re.finditer(rb"(?<![0-9])(\d{1,10})\s+(\d{1,5})\s+obj\b", data):
            self.offsets[int(m.group(1))] = m.end()            # later definitions (incremental updates) win
        self.cache = {}
        self.objstm = {}
        self._load_objstms()
        self.trailer = self._trailer()
        if self.trailer.get("Encrypt") is not None:
            raise Unsupported("this PDF is encrypted: open it in a PDF viewer and print/export the pages to PNG "
                              "(or save an unprotected copy)")

    def _trailer(self):
        tr = {}
        for m in re.finditer(rb"trailer\s*<<", self.d):
            try:
                tr.update(_pdf_value(self.d, m.end() - 2)[0])
            except (ValueError, IndexError):
                pass
        if "Root" not in tr:                           # cross-reference streams carry the trailer keys
            for num in self.offsets:
                o = self.get(_Ref((num, 0)))
                if isinstance(o, dict) and o.get("Type") == "XRef":
                    tr.update({k: v for k, v in o.items() if k in ("Root", "Encrypt", "Info")})
        if "Root" not in tr:
            for num in self.offsets:
                o = self.get(_Ref((num, 0)))
                if isinstance(o, dict) and o.get("Type") == "Catalog":
                    tr["Root"] = _Ref((num, 0)); break
        return tr

    def _load_objstms(self):
        for num in list(self.offsets):
            try:
                o = self._read(num)
            except Exception:
                continue
            if isinstance(o, dict) and o.get("Type") == "ObjStm" and "_stream" in o:
                try:
                    raw = self.stream_data(o)
                    n, first = int(o["N"]), int(o["First"])
                    hdr = raw[:first].split()
                    for k in range(n):
                        onum, off = int(hdr[2 * k]), int(hdr[2 * k + 1])
                        if onum not in self.offsets:
                            self.objstm[onum] = (raw, first + off)
                except Exception:
                    continue

    def _read(self, num):
        if num in self.cache:
            return self.cache[num]
        if num in self.offsets:
            v, j = _pdf_value(self.d, self.offsets[num])
            if isinstance(v, dict):
                k = _skip_ws(self.d, j)
                if self.d[k:k + 6] == b"stream":
                    k += 6
                    if self.d[k:k + 2] == b"\r\n":
                        k += 2
                    elif self.d[k:k + 1] in (b"\n", b"\r"):
                        k += 1
                    ln = v.get("Length")
                    if isinstance(ln, _Ref):
                        ln = self._read(ln[0]) if ln[0] != num else None
                    end = k + ln if isinstance(ln, int) and ln >= 0 else -1
                    if end < 0 or self.d[end:end + 30].lstrip()[:9] != b"endstream":
                        end = self.d.find(b"endstream", k)          # a wrong /Length: find the end
                        while end > k and self.d[end - 1:end] in (b"\n", b"\r"):
                            end -= 1
                    v = dict(v); v["_stream"] = (k, end)
        elif num in self.objstm:
            raw, off = self.objstm[num]
            v = _pdf_value(raw, off)[0]
        else:
            v = None
        self.cache[num] = v
        return v

    def get(self, v):
        seen = 0
        while isinstance(v, _Ref) and seen < 32:
            v = self._read(v[0]); seen += 1
        return v

    def stream_data(self, o, stop_at_image=False):
        """Decoded stream bytes. stop_at_image: leave a final DCT/JPX/JBIG2/CCITT filter undone -> (bytes, filter)."""
        a, b = o["_stream"]
        data = self.d[a:b]
        flt = self.get(o.get("Filter"))
        parms = self.get(o.get("DecodeParms"))
        flts = [str(self.get(f)) for f in (flt if isinstance(flt, list) else ([flt] if flt else []))]
        pl = parms if isinstance(parms, list) else [parms] * len(flts)
        for k, f in enumerate(flts):
            p = self.get(pl[k]) if k < len(pl) else None
            p = {kk: self.get(vv) for kk, vv in p.items()} if isinstance(p, dict) else {}
            if f in ("FlateDecode", "Fl"):
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    data = zlib.decompressobj().decompress(data)          # tolerate a damaged tail
                data = _predictor(data, p)
            elif f in ("LZWDecode", "LZW"):
                data = _predictor(_lzw_decode(data, int(p.get("EarlyChange", 1))), p)
            elif f in ("ASCIIHexDecode", "AHx"):
                hx = re.sub(rb"\s", b"", data).split(b">")[0]
                data = bytes.fromhex((hx + (b"0" if len(hx) % 2 else b"")).decode("ascii"))
            elif f in ("ASCII85Decode", "A85"):
                data = _a85_decode(data)
            elif f in ("RunLengthDecode", "RL"):
                data = _rl_decode(data)
            elif f in ("DCTDecode", "DCT", "JPXDecode", "JBIG2Decode", "CCITTFaxDecode", "CCF"):
                if stop_at_image and k == len(flts) - 1:
                    return data, f
                raise Unsupported(f"{f} inside a filter chain isn't supported")
            elif f == "Crypt":
                continue
            else:
                raise Unsupported(f"PDF filter {f} isn't supported")
        return (data, None) if stop_at_image else data

    def pages(self):
        """[(page dict, inherited resources, rotate)] in reading order."""
        out = []
        root = self.get(self.trailer.get("Root")) or {}

        def walk(node, res, rot, depth):
            node = self.get(node)
            if not isinstance(node, dict) or depth > 50:
                return
            res = self.get(node.get("Resources")) if node.get("Resources") is not None else res
            rot = self.get(node.get("Rotate")) if node.get("Rotate") is not None else rot
            kids = self.get(node.get("Kids"))
            if node.get("Type") == "Pages" or isinstance(kids, list):
                for k in kids or []:
                    walk(k, res, rot, depth + 1)
            else:
                out.append((node, res or {}, int(rot or 0)))
        walk(root.get("Pages"), {}, 0, 0)
        return out

    def page_images(self, res, depth=0):
        """Image XObjects reachable from a resources dict (Form XObjects searched too) -> [stream dicts]."""
        res = self.get(res) or {}
        xo = self.get(res.get("XObject")) or {}
        out = []
        for _, ref in sorted(xo.items()) if isinstance(xo, dict) else []:
            o = self.get(ref)
            if not isinstance(o, dict):
                continue
            if o.get("Subtype") == "Image":
                out.append(o)
            elif o.get("Subtype") == "Form" and depth < 8:
                out += self.page_images(o.get("Resources"), depth + 1)
        return out


def _pdf_colorspace(pdf, cs):
    """-> (kind, components, palette or None, note). kind: gray | rgb | cmyk | indexed | tint | lab."""
    cs = pdf.get(cs)
    if isinstance(cs, list) and cs:
        head = str(pdf.get(cs[0]))
        if head == "ICCBased":
            s = pdf.get(cs[1]); n = int((s or {}).get("N", 3))
            return {1: "gray", 3: "rgb", 4: "cmyk"}.get(n, "gray"), n, None, None
        if head == "Indexed":
            base = _pdf_colorspace(pdf, cs[1]); hival = int(pdf.get(cs[2]))
            lk = pdf.get(cs[3])
            if isinstance(lk, dict) and "_stream" in lk:
                lk = pdf.stream_data(lk)
            lk = bytes(lk or b"")
            nb = base[1]
            pal = np.frombuffer(lk[:(hival + 1) * nb].ljust((hival + 1) * nb, b"\0"), np.uint8) \
                .reshape(hival + 1, nb).astype(np.float32)
            if base[0] == "rgb":
                g = 0.299 * pal[:, 0] + 0.587 * pal[:, 1] + 0.114 * pal[:, 2]
            elif base[0] == "cmyk":
                g = 255 - np.clip(0.3 * pal[:, 0] + 0.59 * pal[:, 1] + 0.11 * pal[:, 2] + pal[:, 3], 0, 255)
            else:
                g = pal[:, 0]
            return "indexed", 1, g, None
        if head in ("CalGray",):
            return "gray", 1, None, None
        if head in ("CalRGB",):
            return "rgb", 3, None, None
        if head == "Lab":
            return "lab", 3, None, None
        if head in ("Separation", "DeviceN"):
            n = len(pdf.get(cs[1])) if head == "DeviceN" else 1
            return "tint", n, None, "spot-color image read as ink amount"
        if head == "Pattern":
            raise Unsupported("pattern color space image")
        return _pdf_colorspace(pdf, cs[0])
    name = str(cs or "DeviceGray")
    return {"DeviceGray": ("gray", 1, None, None), "G": ("gray", 1, None, None), "DeviceRGB": ("rgb", 3, None, None),
            "RGB": ("rgb", 3, None, None), "DeviceCMYK": ("cmyk", 4, None, None), "CMYK": ("cmyk", 4, None, None),
            "I": ("indexed", 1, None, None)}.get(name, ("gray", 1, None, f"color space {name} read as gray"))


def pdf_image_gray(pdf, o):
    """One image XObject -> gray uint8 (h, w)."""
    w = int(pdf.get(o.get("Width") or o.get("W") or 0)); h = int(pdf.get(o.get("Height") or o.get("H") or 0))
    data, last = pdf.stream_data(o, stop_at_image=True)
    if last in ("DCTDecode", "DCT"):
        g = jpeg_decode(data)
        dec = pdf.get(o.get("Decode"))
        if isinstance(dec, list) and dec[:2] in ([1, 0], [1.0, 0.0]):
            g = 255 - g
        return g
    if last in ("JBIG2Decode",):
        raise Unsupported("this PDF's page images are JBIG2 (a black-and-white scan format) - not decoded here. "
                          "Export the pages as PNG (most PDF viewers: Export/Print to image, or 'pdftoppm -png') "
                          "and pass the PNG files")
    if last in ("CCITTFaxDecode", "CCF"):
        raise Unsupported("this PDF's page images are CCITT fax (black-and-white scan) - not decoded here. Export the "
                          "pages as PNG and pass the PNG files")
    if last == "JPXDecode":
        raise Unsupported("this PDF's page images are JPEG 2000 - not decoded here. Export the pages as PNG")
    mask = bool(pdf.get(o.get("ImageMask")))
    bpc = 1 if mask else int(pdf.get(o.get("BitsPerComponent")) or 8)
    if mask:
        kind, ncomp, pal, _ = "gray", 1, None, None
    else:
        kind, ncomp, pal, _ = _pdf_colorspace(pdf, o.get("ColorSpace"))
    rowbytes = (w * ncomp * bpc + 7) // 8
    need = rowbytes * h
    if len(data) < need:
        data = data + b"\0" * (need - len(data))
    rows = np.frombuffer(data[:need], np.uint8).reshape(h, rowbytes)
    S = _samples(rows, w, bpc, ncomp).astype(np.float32)
    maxv = float((1 << bpc) - 1)
    dec = pdf.get(o.get("Decode"))
    if mask:                                           # sample 0 paints (black) unless Decode [1 0]
        inv = isinstance(dec, list) and dec[:2] in ([1, 0], [1.0, 0.0])
        g = np.where((S[..., 0] > 0) != inv, 255.0, 0.0)
        return g.astype(np.uint8)
    if isinstance(dec, list) and len(dec) >= 2 and kind != "indexed":
        lo, hi = float(dec[0]), float(dec[1])
        S = (lo + S / maxv * (hi - lo)) * maxv
    if kind == "indexed":
        idx = np.clip(S[..., 0].astype(np.int64), 0, len(pal) - 1)
        g = pal[idx]
    elif kind == "rgb":
        g = (0.299 * S[..., 0] + 0.587 * S[..., 1] + 0.114 * S[..., 2]) * (255.0 / maxv)
    elif kind == "cmyk":
        ink = (0.3 * S[..., 0] + 0.59 * S[..., 1] + 0.11 * S[..., 2] + S[..., 3]) * (255.0 / maxv)
        g = 255.0 - np.clip(ink, 0, 255)
    elif kind == "tint":
        g = 255.0 - S.mean(axis=2) * (255.0 / maxv)
    elif kind == "lab":
        g = S[..., 0] * (255.0 / maxv)
    else:
        g = S[..., 0] * (255.0 / maxv)
    return np.clip(g + 0.5, 0, 255).astype(np.uint8)


# ============================================================================= sources -> pages (cached)
def _natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _src_key(path):
    st = os.stat(path)
    return (os.path.normcase(os.path.abspath(path)), int(st.st_mtime), st.st_size)


def open_source(source):
    """source: an image path, a PDF path, a folder of page images, or a list of image paths ->
    {"pages": [page descriptors], "kind", "notes": [...]}. Cached by file identity."""
    if isinstance(source, (list, tuple)):
        paths = [str(p) for p in source]
    else:
        source = str(source or "").strip().strip('"')
        if not source:
            raise ValueError("give source: a PNG/JPEG page, a list of them, a folder of page images, or a PDF")
        if os.path.isdir(source):
            paths = sorted((os.path.join(source, f) for f in os.listdir(source) if f.lower().endswith(IMG_EXT)),
                           key=_natural_key)
            pdfs = [f for f in os.listdir(source) if f.lower().endswith(".pdf")]
            if not paths and pdfs:
                raise ValueError(f"the folder holds PDF(s) {sorted(pdfs)[:3]}: pass the PDF itself as source")
            if not paths:
                raise ValueError("no PNG/JPEG page images in that folder")
        else:
            paths = [source]
    pages, notes, kinds = [], [], set()
    for p in paths:
        if not os.path.isfile(p):
            raise ValueError(f"no such file: {p}")
        low = p.lower()
        if low.endswith(".pdf"):
            info = _pdf_info(p)
            kinds.add("pdf"); notes += info["notes"]
            pages += info["pages"]
        elif low.endswith(IMG_EXT):
            kinds.add("image"); pages.append({"kind": "image", "path": p, "label": os.path.basename(p)})
        elif low.endswith((".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic")):
            raise Unsupported(f"{os.path.basename(p)}: {os.path.splitext(p)[1]} isn't read here - save/export the "
                              "page as PNG (or JPEG)")
        else:
            with open(p, "rb") as f:
                head = f.read(8)
            if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8"):
                kinds.add("image"); pages.append({"kind": "image", "path": p, "label": os.path.basename(p)})
            elif head.startswith(b"%PDF"):
                info = _pdf_info(p); kinds.add("pdf"); notes += info["notes"]; pages += info["pages"]
            else:
                raise Unsupported(f"{os.path.basename(p)}: not a PNG, JPEG or PDF")
    return {"pages": pages, "kind": "+".join(sorted(kinds)), "notes": notes}


def _pdf_info(path):
    key = ("pdf",) + _src_key(path)
    if key in _CACHE:
        return _CACHE[key]
    with open(path, "rb") as f:
        data = f.read()
    pdf = PDF(data)
    plist = pdf.pages()
    if not plist:
        raise ValueError("couldn't find any pages in this PDF (damaged?)")
    pages, notes, vector = [], [], []
    for i, (pg, res, rot) in enumerate(plist):
        imgs = pdf.page_images(pg.get("Resources") if pg.get("Resources") is not None else res)
        imgs = [o for o in imgs if int(pdf.get(o.get("Width")) or 0) * int(pdf.get(o.get("Height")) or 0) >= 40000]
        if not imgs:
            vector.append(i + 1)
            pages.append({"kind": "pdf_vector", "path": path, "index": i, "label": f"page {i + 1}"})
            continue
        imgs.sort(key=lambda o: -int(pdf.get(o.get("Width"))) * int(pdf.get(o.get("Height"))))
        if len(imgs) > 1:
            notes.append(f"page {i + 1} has {len(imgs)} images; the largest is used")
        pages.append({"kind": "pdf_image", "path": path, "index": i, "obj": imgs[0], "rotate": rot % 360,
                      "label": f"page {i + 1}", "filter": str(pdf.get(imgs[0].get("Filter")))})
    if len(vector) == len(plist):
        raise Unsupported("this PDF has no page images - it's engraved (vector) music, not a scan. Best: get the "
                          "MusicXML from whoever made it (or the notation program: File > Export > MusicXML), or "
                          "open the PDF in MuseScore (its PDF import) and export MusicXML for sheet_import. To read "
                          "it by eye here: print/export the pages to PNG (e.g. 'pdftoppm -r 200 -png file.pdf page') "
                          "and pass the PNG files")
    if vector:
        notes.append(f"pages {vector} have no images (vector/text pages) and are skipped: export them as PNG to read "
                     "them")
    info = {"pages": pages, "notes": notes, "pdf": pdf}
    _CACHE[key] = info
    return info


def _page_key(page):
    return (page["path"], page.get("index")) + _src_key(page["path"])[1:]


def _lru_get(key):
    for k, g in _CACHE_PAGES:
        if k == key:
            return g
    return None


def _lru_put(key, g):
    _CACHE_PAGES[:] = [(k, x) for k, x in _CACHE_PAGES if k != key] + [(key, g)]
    del _CACHE_PAGES[:-_CACHE_MAX_PAGES]


def page_gray(page):
    """A page descriptor -> gray uint8 pixels as stored (not deskewed)."""
    if page["kind"] == "image":
        with open(page["path"], "rb") as f:
            data = f.read()
        if data[:8].startswith(b"\x89PNG"):
            g = png_decode(data)
        elif data[:2] == b"\xff\xd8":
            g = jpeg_decode(data)
        else:
            raise Unsupported(f"{os.path.basename(page['path'])} isn't a PNG or JPEG")
    elif page["kind"] == "pdf_image":
        pdf = _pdf_info(page["path"])["pdf"]
        g = pdf_image_gray(pdf, page["obj"])
        rot = page.get("rotate") or 0
        if rot:
            g = np.ascontiguousarray(np.rot90(g, -(rot // 90)))
    else:
        raise Unsupported(f"{page['label']} of this PDF has no page image (vector page): export it as PNG")
    return g


# ============================================================================= page layout: staves, systems, bar lines
def _otsu(g):
    hist = np.bincount(g.ravel(), minlength=256).astype(np.float64)
    p = hist / max(1.0, hist.sum()); w = np.cumsum(p); mu = np.cumsum(p * np.arange(256)); mt = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        sb = (mt * w - mu) ** 2 / (w * (1 - w))
    sb[~np.isfinite(sb)] = 0
    return int(np.argmax(sb))


def _skew(dark, max_deg=3.0):
    """Small page rotation (degrees) that makes staff lines most horizontal: projection sharpness over shears of
    column strips."""
    H, W = dark.shape
    nst = 32
    edges = np.linspace(0, W, nst + 1).astype(int)
    S = np.stack([dark[:, edges[k]:edges[k + 1]].sum(1) for k in range(nst)], 1).astype(np.float64)
    cx = (edges[:-1] + edges[1:]) / 2 - W / 2
    best, best_a = -1.0, 0.0
    for a in np.arange(-max_deg, max_deg + 1e-9, 0.05):
        off = np.round(cx * math.tan(math.radians(a))).astype(int)
        acc = np.zeros(H + 2 * int(abs(off).max()) + 2)
        base = int(abs(off).max()) + 1
        for k in range(nst):
            acc[base - off[k]:base - off[k] + H] += S[:, k]
        sc = float((acc ** 2).sum())
        if sc > best:
            best, best_a = sc, float(a)
    return best_a


def _deskew(g, deg):
    """Undo a small rotation by shifting column strips vertically (fine for the few degrees scans are off)."""
    H, W = g.shape
    out = np.full_like(g, 255)
    t = math.tan(math.radians(deg))
    step = max(4, W // 256)
    for x0 in range(0, W, step):
        off = int(round((x0 + step / 2 - W / 2) * t))
        src = g[:, x0:x0 + step]
        if off > 0:
            out[:H - off, x0:x0 + step] = src[off:]
        elif off < 0:
            out[-off:, x0:x0 + step] = src[:H + off]
        else:
            out[:, x0:x0 + step] = src
    return out


def _runs(mask):
    """[(start, end_exclusive)] of True runs."""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.nonzero(d == 1)[0].tolist(), np.nonzero(d == -1)[0].tolist()))


def _staff_lines(dark, x0, x1):
    """Candidate staff lines [(center, thickness)] from the row projection; tries a couple of thresholds."""
    frac = dark[:, x0:x1].mean(1)
    top = float(np.percentile(frac, 99.7)) if len(frac) else 0
    found = []
    for k in (0.45, 0.3, 0.2):
        T = max(0.12, k * top)
        lines = []
        for a, b in _runs(frac > T):
            w = frac[a:b]
            lines.append((float((np.arange(a, b) * w).sum() / max(w.sum(), 1e-9)), b - a))
        staves = _find_staves(lines)
        if len(staves) > len(found):
            found = staves
    return found


def _find_staves(lines):
    out, i = [], 0
    while i + 5 <= len(lines):
        grp = lines[i:i + 5]
        c = [x[0] for x in grp]; gaps = np.diff(c); med = float(np.median(gaps))
        thick = max(x[1] for x in grp)
        if med >= 3 and med > 1.8 * thick and np.all(np.abs(gaps - med) <= 0.22 * med + 1):
            out.append({"lines": c, "space": med, "thick": max(1, int(round(np.median([x[1] for x in grp]))))})
            i += 5
        else:
            i += 1
    return out


def _staff_extent(dark, st):
    """Left/right x of a staff: columns where at least 4 of the 5 lines are dark (gaps closed)."""
    H, W = dark.shape
    t = st["thick"]
    cnt = np.zeros(W, np.int32)
    for y in st["lines"]:
        a = max(0, int(round(y - t / 2 - 1))); b = min(H, int(round(y + t / 2 + 1)) + 1)
        cnt += dark[a:b].any(0)
    good = cnt >= 4
    gap = int(2 * st["space"])
    runs = _runs(good)
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    if not merged:
        return 0, W
    a, b = max(merged, key=lambda r: r[1] - r[0])
    return a, b


def _col_frac(dark, y0, y1, x0, x1):
    y0 = max(0, int(y0)); y1 = min(dark.shape[0], int(y1)); x0 = max(0, int(x0)); x1 = min(dark.shape[1], int(x1))
    if y1 <= y0 or x1 <= x0:
        return np.zeros(max(0, x1 - x0))
    return dark[y0:y1, x0:x1].mean(0)


def _barlines(dark, st, ext, above_ok, below_ok):
    """x centers of bar line candidates on one staff (stems with a head at the end are rejected)."""
    s = st["space"]; t = st["thick"]; top, bot = st["lines"][0], st["lines"][-1]
    x0, x1 = ext
    fr = _col_frac(dark, top - t / 2, bot + t / 2 + 1, x0, x1)
    cand = []
    H = dark.shape[0]
    line_rows = np.zeros(H, bool)
    for y in st["lines"]:
        line_rows[max(0, int(y - t)):int(y + t) + 1] = True
    for a, b in _runs(fr >= 0.85):
        a += x0; b += x0
        if b - a > max(3, 0.8 * s):
            continue
        xc = (a + b - 1) / 2
        colv = dark[int(top - t / 2):int(bot + t / 2) + 1, max(0, a - 1):b + 1].any(1)
        gaps = [e - st for st, e in _runs(~colv)]
        if gaps and max(gaps) > max(1.0, 0.25 * s):    # a stem stops short of the far line; a bar line doesn't
            continue
        bad = False                                    # a filled note head right beside it = a stem
        ya, yb = int(max(0, top - 1.2 * s)), int(min(H, bot + 1.2 * s + 1))
        rows = (~line_rows[ya:yb]).astype(np.float64)
        win = max(2, int(0.8 * s))
        for xa, xb in ((a - 0.55 * s - 1, a - 1), (b + 1, b + 0.55 * s + 1)):
            xa, xb = int(max(0, xa)), int(min(dark.shape[1], xb))
            if xb - xa < 2:
                continue
            prof = dark[ya:yb, xa:xb].mean(1) * rows
            if len(prof) < win:
                continue
            num_ = np.convolve(prof, np.ones(win), mode="valid")
            den_ = np.convolve(rows, np.ones(win), mode="valid")
            ok = den_ >= max(2, 0.25 * win)
            if ok.any() and (num_[ok] / den_[ok]).max() > 0.45:
                bad = True
        if bad:
            continue
        cx = int(round(xc))
        col = dark[:, max(0, cx - 1):cx + 2].any(1)
        up = 0
        y = int(top - t)
        while y > 0 and col[y]:
            up += 1; y -= 1
        dn = 0
        y = int(bot + t)
        while y < H - 1 and col[y]:
            dn += 1; y += 1
        if (up > 1.0 * s and not above_ok) or (dn > 1.0 * s and not below_ok):
            continue
        cand.append((xc, b - a))
    return cand


def analyze_page(g):
    """Gray page -> layout {systems: [{staves, box, bar_x, bars_est, space}], skew_deg, size}; may deskew g."""
    T0 = time.time()
    thr = int(np.clip(_otsu(g), 90, 200))
    dark = g < thr
    H, W = g.shape
    small = dark[::2, ::2] if H * W > 4e6 else dark
    deg = _skew(small)
    if abs(deg) >= 0.1:
        g = _deskew(g, deg)
        dark = g < thr
    colfrac = dark.mean(0)
    content = np.nonzero((colfrac > 0.002) & (colfrac < 0.6))[0]
    x0, x1 = (int(content[0]), int(content[-1]) + 1) if len(content) else (0, W)
    staves = _staff_lines(dark, x0, x1)
    for st in staves:
        st["ext"] = _staff_extent(dark, st)
    systems, cur = [], []
    for i, st in enumerate(staves):
        if cur:
            prev = cur[-1]
            a = max(prev["ext"][0], st["ext"][0]); b = min(prev["ext"][1], st["ext"][1])
            y0 = prev["lines"][-1] + prev["thick"] + 1; y1 = st["lines"][0] - st["thick"]
            joined = b > a and y1 > y0 and (_col_frac(dark, y0, y1, a, b) >= 0.92).any()
            if not joined:
                systems.append(cur); cur = []
        cur.append(st)
    if cur:
        systems.append(cur)
    out = []
    for sys_ in systems:
        sp = float(np.median([s["space"] for s in sys_]))
        ex0 = min(s["ext"][0] for s in sys_); ex1 = max(s["ext"][1] for s in sys_)
        per = []
        for k, st in enumerate(sys_):
            per.append(_barlines(dark, st, st["ext"], k > 0, k < len(sys_) - 1))
        tol = max(3.0, 0.4 * sp)
        xs = [x for x, _ in per[0]]
        for other in per[1:]:
            ox = [x for x, _ in other]
            xs = [x for x in xs if any(abs(x - o) <= tol for o in ox)]
        xs.sort()
        merged = []
        for x in xs:                                   # double / repeat bar lines count once
            if merged and x - merged[-1][-1] < 1.2 * sp:
                merged[-1].append(x)
            else:
                merged.append([x])
        bars = [float(np.mean(m)) for m in merged]
        kinds = ["double" if len(m) > 1 else "single" for m in merged]
        if bars and bars[0] - ex0 < 1.5 * sp:          # the system's opening line isn't a bar boundary
            bars, kinds = bars[1:], kinds[1:]
        n_bars = len(bars) + (0 if bars and ex1 - bars[-1] < 2.0 * sp else 1)
        out.append({"staves": len(sys_), "space": round(sp, 2), "top": sys_[0]["lines"][0],
                    "bottom": sys_[-1]["lines"][-1], "x0": ex0, "x1": ex1, "bar_x": [round(x, 1) for x in bars],
                    "bar_kinds": kinds, "bars_est": n_bars, "staff_lines": [s["lines"] for s in sys_]})
    for i, s in enumerate(out):                        # crop boxes: halfway to the neighbours
        sp = s["space"]
        y0 = (out[i - 1]["bottom"] + s["top"]) / 2 if i else max(0.0, s["top"] - 7 * sp)
        y1 = (s["bottom"] + out[i + 1]["top"]) / 2 if i + 1 < len(out) else min(float(H), s["bottom"] + 7 * sp)
        s["box"] = [int(max(0, s["x0"] - 2.5 * sp)), int(max(0, y0)), int(min(W, s["x1"] + 2 * sp)), int(min(H, y1))]
    lone = len(out) > 1 and all(s["staves"] == 1 for s in out)
    return {"systems": out, "skew_deg": round(deg, 2), "size": [W, H], "threshold": thr, "gray": g,
            "single_staves_only": lone, "took_s": round(time.time() - T0, 3)}


_LAYOUTS = {}                                      # page key -> layout without pixels (small; kept for the session)


def page_layout(page, decode=True):
    """Layout of a page (cached); with decode, its deskewed pixels too (LRU of a few pages)."""
    key = _page_key(page)
    lay = _LAYOUTS.get(key)
    if lay is not None and not decode:
        return lay
    g = _lru_get(key)
    if lay is None:
        full = analyze_page(page_gray(page))
        g = full.pop("gray")
        _LAYOUTS[key] = lay = full
        if len(_LAYOUTS) > 400:
            _LAYOUTS.pop(next(iter(_LAYOUTS)))
        _lru_put(key, g)
    elif g is None:
        g = page_gray(page)
        if abs(lay["skew_deg"]) >= 0.1:
            g = _deskew(g, lay["skew_deg"])
        _lru_put(key, g)
    return {**lay, "gray": g}


# ============================================================================= rendering crops (one image per result)
IMAGE_BUDGET_PX = 1_250_000                        # Claude's vision downsizes bigger images: stay sharp below it
_DIGITS = {"0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
           "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001010010010",
           "8": "111101111101111", "9": "111101111001111"}
MARK_RGB = (200, 30, 30)


def _resize(g, nw, nh):
    """Gray -> (nh, nw): area average when shrinking a lot, then bilinear."""
    h, w = g.shape
    f = g.astype(np.float32)
    k = int(min(w / max(nw, 1), h / max(nh, 1)))
    if k >= 2:
        hh, ww = h // k * k, w // k * k
        f = f[:hh, :ww].reshape(hh // k, k, ww // k, k).mean((1, 3)); h, w = f.shape
    ys = np.clip((np.arange(nh) + 0.5) * h / nh - 0.5, 0, h - 1)
    xs = np.clip((np.arange(nw) + 0.5) * w / nw - 0.5, 0, w - 1)
    y0 = np.floor(ys).astype(int); x0 = np.floor(xs).astype(int)
    y1 = np.minimum(y0 + 1, h - 1); x1 = np.minimum(x0 + 1, w - 1)
    wy = (ys - y0)[:, None]; wx = (xs - x0)[None, :]
    top = f[y0][:, x0] * (1 - wx) + f[y0][:, x1] * wx
    bot = f[y1][:, x0] * (1 - wx) + f[y1][:, x1] * wx
    return np.clip(top * (1 - wy) + bot * wy + 0.5, 0, 255).astype(np.uint8)


def _draw_text(rgb, x, y, text, scale, color=MARK_RGB):
    """Tiny 3x5 digits (bar numbers) at (x, y) top-left."""
    H, W, _ = rgb.shape
    for ch in str(text):
        bm = _DIGITS.get(ch)
        if bm:
            for r in range(5):
                for c in range(3):
                    if bm[r * 3 + c] == "1":
                        ya, xa = y + r * scale, x + c * scale
                        if 0 <= ya < H and 0 <= xa < W:
                            rgb[max(0, ya):min(H, ya + scale), max(0, xa):min(W, xa + scale)] = color
        x += 4 * scale


def _split_points(sysl, parts):
    """x split positions for `parts` strips, snapped to a detected bar line near each ideal cut."""
    x0, x1 = sysl["box"][0], sysl["box"][2]
    cuts, snapped = [], []
    for k in range(1, parts):
        ideal = x0 + (x1 - x0) * k / parts
        near = [b for b in sysl["bar_x"] if abs(b - ideal) < 0.25 * (x1 - x0) / parts]
        if near:
            c = min(near, key=lambda b: abs(b - ideal)); snapped.append(True)
        else:
            c = ideal; snapped.append(False)
        cuts.append(int(round(c)))
    return cuts, snapped


def render_systems(lay, idxs, zoom=2.0, max_width=1400, marks=True, budget=IMAGE_BUDGET_PX):
    """Systems (0-based indices) -> (rgb uint8 array, info). Each system is cut into strips (left part on top) when
    one strip at the wanted scale would be wider than max_width; everything fits the pixel budget."""
    g = lay["gray"]
    sy = [lay["systems"][i] for i in idxs]
    sp = float(np.median([s["space"] for s in sy]))
    want = max(0.2, 8.0 * float(zoom) / sp)            # zoom 2 = 16 px per staff space
    widest = max(s["box"][2] - s["box"][0] for s in sy)
    if max_width < widest * want <= max_width / 0.8:   # nearly fits: one strip, slightly smaller
        want = max_width / widest
    sep = 10
    for _ in range(12):
        plan, tot_h = [], 0
        for s in sy:
            x0, y0, x1, y1 = s["box"]
            parts = max(1, int(math.ceil((x1 - x0) * want / max_width - 1e-9)))
            cuts, snapped = _split_points(s, parts)
            edges = [x0] + cuts + [x1]
            strips = []
            for a, b in zip(edges[:-1], edges[1:]):
                a2 = a - (0 if a == x0 else int(0.4 * s["space"])); b2 = b + (0 if b == x1 else int(0.4 * s["space"]))
                strips.append((max(0, a2), b2))
            plan.append((s, strips, snapped)); tot_h += sum(int((y1 - y0) * want) for _ in strips) + sep * len(strips)
        width = min(max_width, int(max((b - a) for _, st, _ in plan for a, b in st) * want) + 1)
        if width * tot_h <= budget or want <= 0.2:
            break
        want *= max(0.5, math.sqrt(budget / max(1.0, width * tot_h)) * 0.98)
    rows, info = [], []
    bar_no = 0
    for s, strips, snapped in plan:
        x0, y0, x1, y1 = s["box"]
        ent = {"strips": len(strips), "cut_at_bar_lines": all(snapped) if snapped else None}
        for si, (a, b) in enumerate(strips):
            crop = g[y0:y1, a:b]
            nw, nh = max(1, int((b - a) * want)), max(1, int((y1 - y0) * want))
            im = _resize(crop, nw, nh)
            rgb = np.repeat(im[:, :, None], 3, 2)
            if marks:
                sc = max(1, int(round(s["space"] * want * 0.13)))
                ytop = int((s["top"] - y0) * want)
                yt = max(0, ytop - int(1.6 * s["space"] * want) - 5 * sc)
                prev = x0 if si == 0 else a
                xs = [bx for bx in s["bar_x"] if a <= bx < b + 1]
                for bx in xs:                          # tick above each detected bar line
                    px = int((bx - a) * want)
                    rgb[max(0, ytop - int(1.2 * s["space"] * want)):max(0, ytop - int(0.5 * s["space"] * want)),
                        max(0, px - sc // 2):px + sc // 2 + 1] = MARK_RGB
                last = si == len(strips) - 1
                ends = xs + ([s["x1"]] if last and (not xs or s["x1"] - xs[-1] > 2 * s["space"]) else [])
                for e in ends:                         # bar number (within this system) centred over the bar
                    if e <= prev + s["space"]:
                        continue
                    bar_no += 1
                    mid = int(((prev + e) / 2 - a) * want)
                    _draw_text(rgb, mid - 4 * sc * len(str(bar_no)) // 2, yt, bar_no, sc)
                    prev = e
            rows.append(rgb)
            rows.append(np.full((sep, rgb.shape[1], 3), (255, 170, 170) if si < len(strips) - 1 else (150, 150, 150),
                                np.uint8))
        info.append(ent)
        bar_no = 0
    rows = rows[:-1]
    W = max(r.shape[1] for r in rows)
    out = np.full((sum(r.shape[0] for r in rows), W, 3), 255, np.uint8)
    y = 0
    for r in rows:
        out[y:y + r.shape[0], :r.shape[1]] = r; y += r.shape[0]
    return out, {"px_per_staff_space": round(sp * want, 1), "systems": info, "size": [int(W), int(out.shape[0])]}


def page_overview(lay, max_width=1400, budget=IMAGE_BUDGET_PX):
    """The whole page scaled into the budget, with each system's number in the left margin."""
    g = lay["gray"]; H, W = g.shape
    sc = min(max_width / W, math.sqrt(budget / (W * H)), 1.0)
    nw, nh = max(1, int(W * sc)), max(1, int(H * sc))
    rgb = np.repeat(_resize(g, nw, nh)[:, :, None], 3, 2)
    s_ = max(2, int(nh / 300))
    for i, s in enumerate(lay["systems"]):
        y = int((s["top"] + s["bottom"]) / 2 * sc) - 2 * s_
        _draw_text(rgb, max(0, int(s["box"][0] * sc) - 4 * s_ * len(str(i + 1)) - s_), y, i + 1, s_)
    return rgb


# ============================================================================= notation glue (sheet_notation, lazy)
SEV = {"info": 0, "low": 1, "warning": 2, "med": 2, "medium": 2, "high": 3, "error": 3}
NAMES = "C C# D Eb E F F# G Ab A Bb B".split()


def note_name(k):
    return NAMES[int(k) % 12] + str(int(k) // 12 - 1)


def _sn():
    import importlib
    import sys
    if "sheet_notation" in sys.modules:
        return sys.modules["sheet_notation"]
    try:
        return importlib.import_module("sheet_notation")
    except ImportError:
        raise RuntimeError("sheet_notation.py (the notation parser) is missing next to the server: reinstall "
                           "fl-headless")


def _is_path(s, exts):
    s = str(s).strip().strip('"')
    return "\n" not in s and len(s) < 1024 and s.lower().endswith(exts) and os.path.isfile(s)


def _is_score_data(x):
    """A per-bar score-data reading: a dict, JSON object text or a .json path."""
    if isinstance(x, dict):
        return True
    s = str(x).strip()
    return s.startswith("{") or _is_path(s, (".json",))


def load_score(abc=None, musicxml=None, score=None, parts=None):
    """-> (score, {"type", "file"}). abc / musicxml: the text itself or a path to the file; score: per-bar score
    data (dict, JSON text or a .json path; parts = only these parts)."""
    SN = _sn()
    if sum(x is not None for x in (abc, musicxml, score)) != 1:
        raise ValueError("give one of abc= (ABC text or a .abc file), musicxml= (.musicxml/.xml/.mxl file or the XML "
                         "text) or score= (per-bar score data: a dict, JSON text or a .json file)")
    if score is not None:
        sc = SN.parse_score_data(score, parts=parts)
        return sc, {"type": "score data", "file": sc.score_info.get("file")}
    if abc is not None:
        f = None
        if _is_path(abc, (".abc", ".txt")):
            f = str(abc).strip().strip('"')
            with open(f, encoding="utf-8", errors="replace") as fh:
                abc = fh.read()
        return SN.parse_abc(str(abc)), {"type": "abc", "file": f}
    f = str(musicxml).strip().strip('"') if _is_path(musicxml, (".musicxml", ".xml", ".mxl")) else None
    return SN.parse_musicxml(f or musicxml), {"type": "musicxml" + (" (.mxl)" if f and f.lower().endswith(".mxl")
                                                                    else ""), "file": f}


def _validate(score, strict=False):
    SN = _sn()
    try:
        iss = SN.validate(score, strict=strict)
    except TypeError:
        iss = SN.validate(score)
    out = []
    for i in iss or []:
        if isinstance(i, dict):
            out.append(i)
        else:
            out.append({"severity": getattr(i, "severity", "med"), "bar": getattr(i, "bar", None),
                        "voice": getattr(i, "voice", None), "message": str(getattr(i, "message", i))})
    return sorted(out, key=lambda x: -SEV.get(str(x.get("severity", "med")).lower(), 2))


def _inline(score):
    SN = _sn()
    r = SN.to_inline(score)
    if isinstance(r, tuple):
        r = {"voices": r[0], "summary": r[1] if len(r) > 1 else {}}
    voices = r.get("voices") if isinstance(r, dict) else None
    if voices is None:
        voices = {k: v for k, v in r.items() if isinstance(v, list)}
    return {str(k): v for k, v in voices.items()}, (r.get("summary") or {}) if isinstance(r, dict) else {}


def _meters(score):
    m = getattr(score, "meters", None) or [(0, 4, 4)]
    return [(int(t), int(n), int(d)) for t, n, d in m]


def _main_meter(score):
    """The meter FL's grid uses (sheet_notation: the one covering the most written time)."""
    m = getattr(score, "meter", None)
    if isinstance(m, (tuple, list)) and len(m) == 2:
        return int(m[0]), int(m[1])
    return _meters(score)[0][1:]


def _qpb(num, den):
    return num * 4.0 / den


def _tempos(score):
    t = getattr(score, "tempos", None) or []
    return [(int(a), float(b)) for a, b in t]


def _ppq(score):
    return int(getattr(score, "ppq", 480) or 480)


def _score_summary(score, voices, isum, issues=None):
    """Compact facts from sheet_notation's summary: bars, meters, keys, tempos, play order, per-voice range/notes."""
    keep = ("title", "composer", "bars", "bars_played", "play_order", "pickup", "meters", "keys", "tempos",
            "tempo_assumed", "seconds", "fl_meter", "fl_bars", "pickup_offset_bars", "note")
    out = {k: isum[k] for k in keep if isum.get(k) not in (None, "", [], False)}
    if not out.get("title") and getattr(score, "title", None):
        out["title"] = score.title
    vs = {}
    for name, ns_ in voices.items():
        ps = [int(n[2]) for n in ns_]
        v = {"notes": len(ns_), "range": f"{note_name(min(ps))}-{note_name(max(ps))}" if ps else None}
        extra = (isum.get("voices") or {}).get(name) or {}
        if extra.get("clef"):
            v["clef"] = extra["clef"]
        vs[name] = v
    out["voices"] = vs
    if "bars" not in out:
        end = max([n[0] - 1 + n[1] for v in voices.values() for n in v] or [0])
        out["bars"] = int(math.ceil(end - 1e-6))
    if issues is not None:
        c = {}
        for i in issues:
            sv = str(i.get("severity", "med")).lower(); c[sv] = c.get(sv, 0) + 1
        out["issues"] = c
    return out


def _score_data_report(si, place, full=False):
    """Compact facts about a score-data reading: bars, parts, beat sums, where it lands in FL."""
    r = {"bars": f"{si['first_bar']}-{si['last_bar']}", "parts": si["parts"], "beat_check": si["beat_check"]}
    multi = {p: n for p, n in si["layers"].items() if n > 1}
    if multi:
        r["overlapping_lines"] = {p: f"{n} lines (chords / voices of different lengths)" for p, n in multi.items()}
    if si.get("fixed"):
        r["same_key_overlaps_fixed"] = si["fixed"]
    if si["sections"]:
        r["sections"] = si["sections"][:(60 if full else 12)]
    if si["chords"]:
        r["chord_symbols"] = len(si["chords"])
    if place:
        r["placement"] = (f"bar {si['first_bar']} starts at FL bar {int(place) if float(place).is_integer() else place}"
                          + (" (the pickup is padded: it ends where the next FL bar starts)" if si.get("pickup")
                             else ""))
    if full:
        r["abc"] = si["abc"]
    return r


def _range_check(score, pinfo=None):
    """Score-data parts named after an instrument (data/instrument_ranges.json, via flh_score): notes outside its
    range -> ['bar 12 flute: C3 below C4-C7', ...] (an octave misread, or the part name isn't the instrument)."""
    try:
        import importlib
        import sys
        FS = sys.modules.get("flh_score") or importlib.import_module("flh_score")
    except Exception:
        return []
    out, by = [], {}
    for n in getattr(score, "notes", []):
        by.setdefault(n["voice"], []).append(n)
    for part, ns_ in by.items():
        info = (pinfo or {}).get(part) if isinstance((pinfo or {}).get(part), dict) else {}
        try:
            ent = FS.instrument_for([info.get("instrument"), part.replace("_", " ")])
        except Exception:
            return out
        rg = (ent or {}).get("_range")
        if not rg:
            continue
        lo, hi = rg
        bad = [n for n in ns_ if not lo <= n["pitch"] <= hi]
        for n in bad[:3]:
            out.append(f"bar {n['measure']} {part}: {note_name(n['pitch'])} "
                       f"{'below' if n['pitch'] < lo else 'above'} the {ent.get('id')} range "
                       f"{note_name(lo)}-{note_name(hi)}" + (f" (+{len(bad) - 3} more)" if len(bad) > 3 and
                                                             n is bad[2] else ""))
    return out[:12]


def _where(bar, pages):
    """A bar -> 'page P system S' from the caller's mapping [{page, system, first_bar}] (first_bar ascending)."""
    if not pages or bar is None:
        return None
    try:
        rows = sorted(({"page": int(p.get("page", 1)), "system": int(p.get("system", 1)),
                        "first_bar": float(p["first_bar"])} for p in pages if "first_bar" in p),
                      key=lambda r: r["first_bar"])
        hit = None
        for r in rows:
            if float(bar) >= r["first_bar"]:
                hit = r
        return f"page {hit['page']} system {hit['system']}" if hit else None
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def _reread(issues, flags, pages, min_sev):
    """Bars to look at again, worst first: [{bar, where, why: [...]}]."""
    by = {}
    for i in issues:
        if SEV.get(str(i.get("severity", "med")).lower(), 2) < min_sev or i.get("bar") is None:
            continue
        e = by.setdefault(i["bar"], {"bar": i["bar"], "why": [], "sev": 0})
        e["why"].append((f"{i['voice']}: " if i.get("voice") else "") + str(i.get("message", ""))
                        + (f" (fix: {i['fix']})" if i.get("fix") else ""))
        e["sev"] = max(e["sev"], SEV.get(str(i.get("severity")).lower(), 2))
    for f in flags or []:
        e = by.setdefault(f["bar"], {"bar": f["bar"], "why": [], "sev": 0})
        e["why"].append("recording disagrees: " + f["detail"]); e["sev"] = max(e["sev"], 2)
    out = []
    for e in sorted(by.values(), key=lambda e: (-e["sev"], e["bar"] if isinstance(e["bar"], (int, float)) else 0)):
        w = _where(e["bar"], pages)
        out.append({"bar": e["bar"], **({"where": w} if w else {}), "why": e["why"][:4]})
    return out


# ============================================================================= reference audio: DTW on chroma
def _tr():
    import importlib
    import sys
    return sys.modules.get("flh_transcribe") or importlib.import_module("flh_transcribe")


def _playback_notes(score, voices):
    """[(start_q, len_q, pitch, measure)] in playback order (repeats played out when the score expands them)."""
    ppq = _ppq(score)
    notes = getattr(score, "notes", None)
    out = []
    if notes:
        for n in notes:
            g = n.get if isinstance(n, dict) else (lambda k, d=None, n=n: getattr(n, k, d))
            if g("grace") or (voices and g("voice") is not None and str(g("voice")) not in voices):
                continue
            out.append((g("start_tick") / ppq, max(g("len_ticks"), 1) / ppq, int(g("pitch")), g("measure")))
    if not out:                                        # fallback: inline song bars of the main meter
        m = _main_meter(score); qpb = _qpb(*m)
        for ns_ in voices.values():
            for b, ln, p, _ in ns_:
                out.append(((b - 1) * qpb, ln * qpb, int(p), int(math.floor(b + 1e-6))))
    out.sort()
    return out


def _score_chroma(pnotes, frame_q):
    """Score chroma per frame (harmonic-weighted) + the measure each frame belongs to."""
    end = max(s + ln for s, ln, _, _ in pnotes)
    n = int(math.ceil(end / frame_q - 1e-9))
    Cs = np.zeros((n, 12)); meas = [None] * n
    for s, ln, p, m in pnotes:
        a = s / frame_q; b = (s + ln) / frame_q
        for f in range(int(math.floor(a)), min(n, int(math.ceil(b)))):
            ov = min(b, f + 1) - max(a, f)
            if ov <= 0:
                continue
            Cs[f, p % 12] += ov; Cs[f, (p + 7) % 12] += 0.3 * ov; Cs[f, (p + 4) % 12] += 0.12 * ov
            if meas[f] is None or ov > 0.5:
                meas[f] = m
    last = None
    for f in range(n):                                 # rests belong to the bar before
        if meas[f] is None:
            meas[f] = last
        last = meas[f]
    return Cs, meas


def _norm_rows(X):
    nr = np.linalg.norm(X, axis=1, keepdims=True)
    return np.where(nr > 1e-9, X / np.maximum(nr, 1e-9), 0.0)


def _dtw(Cost):
    """DTW with steps (1,1), (2,1), (1,2) (local tempo 0.5x..2x), start and end pinned -> path [(i, j)]."""
    N, M = Cost.shape
    INF = 1e18
    D = np.full((N, M), INF); B = np.zeros((N, M), np.int8)
    D[0, 0] = Cost[0, 0]
    for i in range(1, N):
        c = Cost[i]
        cand = np.full((3, M), INF)
        cand[0, 1:] = D[i - 1, :-1]                                    # (1,1)
        if i >= 2:
            cand[1, 1:] = D[i - 2, :-1] + Cost[i - 1, 1:]              # (2,1)
        cand[2, 2:] = D[i - 1, :-2] + c[1:-1]                          # (1,2)
        k = np.argmin(cand, 0)
        D[i] = cand[k, np.arange(M)] + c; B[i] = k
    i, j = N - 1, M - 1
    if D[i, j] >= INF / 2:                             # the shapes don't allow a full path: take the best end
        j = int(np.argmin(D[i]))
    path = [(i, j)]
    while i > 0 and j > 0:
        k = B[i, j]
        if k == 0:
            i, j = i - 1, j - 1
        elif k == 1:
            path.append((i - 1, j)); i, j = i - 2, j - 1
        else:
            path.append((i, j - 1)); i, j = i - 1, j - 2
        path.append((i, j))
    return path[::-1]


def compare_audio(score, voices, audio, max_frames=2400):
    """Align the score to a recording and flag bars whose pitch content disagrees.
    -> {"flags": [{bar, confidence, missing, extra, detail}], "aligned_bpm", "bars_checked", "match", "notes"}"""
    T0 = time.time()
    TR = _tr()
    x, fs = TR.load_audio(audio)
    y, fsw = TR.work_rate(x, fs)
    fsw = int(fsw)
    pn = _playback_notes(score, voices)
    if not pn:
        return {"flags": [], "notes": ["the score has no notes to compare"]}
    hop = 1024
    Ca, _, crate = TR.chroma(y, fsw, n_fft=4096, hop=hop)
    rms = np.sqrt(np.convolve(y ** 2, np.ones(hop) / hop, mode="same")[::hop][:len(Ca)] + 1e-12)
    db = 20 * np.log10(rms / max(rms.max(), 1e-12))
    act = np.nonzero(db > -40)[0]
    if len(act) < 4:
        return {"flags": [], "notes": ["the recording is (nearly) silent"]}
    a0, a1 = int(act[0]), int(act[-1]) + 1
    total_q = max(s + ln for s, ln, _, _ in pn) - min(s for s, _, _, _ in pn)
    sec_q = (a1 - a0) / crate / max(total_q, 1e-6)     # tempo-agnostic: the recording's length sets the scale
    frame_q = 0.5
    while total_q / frame_q > max_frames:
        frame_q *= 2
    q0 = min(s for s, _, _, _ in pn)
    pn0 = [(s - q0, ln, p, m) for s, ln, p, m in pn]
    Cs, meas = _score_chroma(pn0, frame_q)
    fa = frame_q * sec_q * crate                       # chroma frames per score frame
    na = max(2, int(round((a1 - a0) / fa)))
    edges = np.linspace(a0, a1, na + 1)
    A = np.zeros((na, 12)); Adb = np.zeros(na)
    for k in range(na):
        lo, hi = int(edges[k]), max(int(edges[k]) + 1, int(edges[k + 1]))
        A[k] = Ca[lo:hi].mean(0); Adb[k] = db[lo:hi].mean()
    A[Adb < -45] = 0
    An, Sn = _norm_rows(A), _norm_rows(Cs)
    cost = 1.0 - Sn @ An.T
    both_silent = (np.linalg.norm(Sn, axis=1)[:, None] == 0) & (np.linalg.norm(An, axis=1)[None, :] == 0)
    cost[both_silent] = 0.0
    path = _dtw(cost)
    matched = {}
    for i, j in path:
        matched.setdefault(i, []).append(j)
    per = {}
    for i, js in matched.items():
        m = meas[i]
        if m is None:
            continue
        e = per.setdefault(m, {"S": np.zeros(12), "A": np.zeros(12), "n": 0})
        e["S"] += Cs[i]; e["A"] += An[js].mean(0); e["n"] += 1
    rows = []
    for m, e in per.items():
        if e["S"].sum() <= 0:
            continue
        s = e["S"] / np.linalg.norm(e["S"]); a = e["A"] / max(np.linalg.norm(e["A"]), 1e-9)
        rows.append((m, float(s @ a), e))
    if not rows:
        return {"flags": [], "notes": ["nothing to compare"]}
    sims = np.array([r[1] for r in rows])
    med = float(np.median(sims)); mad = float(np.median(np.abs(sims - med))) + 0.02
    floor = min(med - 4 * mad, med - 0.12)
    flags = []
    for m, sim, e in rows:
        sh = e["S"] / e["S"].sum(); ah = e["A"] / max(e["A"].sum(), 1e-9)
        missing = [NAMES[k] for k in np.argsort(-sh) if sh[k] >= 0.08 and ah[k] < 0.45 * sh[k]]
        extra = [NAMES[k] for k in np.argsort(-ah) if ah[k] >= 0.1 and sh[k] < 0.03]
        semi = any((NAMES.index(a_) - NAMES.index(b_)) % 12 in (1, 11) for a_ in missing for b_ in extra)
        if sim >= floor and not (semi and sim < med - 0.02):
            continue
        det = []
        if missing:
            det.append("score has " + ", ".join(missing[:4]) + " the recording lacks")
        if extra:
            det.append("recording has " + ", ".join(extra[:4]) + " the score lacks")
        semis = [(a_, b_) for a_ in missing for b_ in extra if (NAMES.index(a_) - NAMES.index(b_)) % 12 in (1, 11)]
        if semis:
            det.append(f"a wrong accidental? ({semis[0][0]} vs {semis[0][1]})")
        conf = float(np.clip((floor - sim) / max(med - floor, 0.05) + 0.5, 0.3, 0.95)) if sim < floor else 0.35
        flags.append({"bar": m, "match": round(sim, 2), "confidence": round(conf, 2), "missing": missing[:4],
                      "extra": extra[:4], "detail": "; ".join(det) or "pitch content differs"})
    flags.sort(key=lambda f: f["match"])
    notes = ["chroma can't hear octaves, and dense chords or loud drums blur it: a flag means look again; no flag "
             "doesn't prove a bar right"]
    poly = max(sum(1 for s2, l2, _, _ in pn if s2 < s + 1e-6 and s2 + l2 > s + 1e-6) for s, _, _, _ in pn[:400])
    oct_flags = []
    if poly <= 1:                                      # one line: the pitch tracker can check octaves
        try:
            tr = TR.pitch_track(y, fsw)
            t_a = tr["t"]; f0 = tr["f0"]
            midi = np.where(f0 > 0, 69 + 12 * np.log2(np.maximum(f0, 1e-9) / 440.0), np.nan)
            per_bar = {}
            for i, js in matched.items():
                m = meas[i]
                if m is None or not js:
                    continue
                per_bar.setdefault(m, []).append((edges[min(js)] / crate, edges[min(max(js) + 1, na)] / crate))
            sp = {}
            for s, ln, p, m in pn:
                sp.setdefault(m, []).append(p)
            for m, spans in per_bar.items():
                lo = min(a for a, _ in spans); hi = max(b for _, b in spans)
                sel = (t_a >= lo) & (t_a < hi) & np.isfinite(midi)
                if sel.sum() < 5 or m not in sp:
                    continue
                d = float(np.nanmedian(midi[sel]) - np.median(sp[m]))
                if abs(abs(d) - 12) < 2.5 or abs(abs(d) - 24) < 2.5:
                    oct_flags.append({"bar": m, "match": None, "confidence": 0.6, "missing": [], "extra": [],
                                      "detail": f"the recording plays this bar about {round(d / 12):+d} octave(s) "
                                                "from the score (wrong clef/octave sign?)"})
            notes.append("one-voice score: octaves checked with the pitch tracker")
        except Exception as e:                         # the octave check is a bonus
            notes.append(f"octave check skipped ({type(e).__name__})")
    else:
        notes.append(f"polyphonic score (up to {poly} notes at once): octaves aren't checked")
    have = {f["bar"] for f in flags}
    flags += [f for f in oct_flags if f["bar"] not in have]
    match = float(np.mean([1 - cost[i, j] for i, j in path]))
    return {"flags": flags, "bars_checked": len(rows), "median_match": round(med, 2), "path_match": round(match, 2),
            "aligned_bpm": round(60.0 / sec_q, 1), "notes": notes, "took_s": round(time.time() - T0, 2)}


# ============================================================================= tools
def _h(name, default=None):
    return (H or {}).get(name, default)


def _call_hint(name, args):
    try:
        return C.tool_call(H, name, args)
    except Exception:
        return f"{name}({json.dumps(args)})"


# ----------------------------------------------------------------------------- page frame: systems list, crops
FRAME_W = 1000                                     # crop / systems coordinates: the page scaled to this width
CROP_MAX_UP = 4.0                                  # crops are upscaled at most this much (more adds only blur)
SPLIT_BUDGET_S = 45.0                              # split=N analyses pages for this long, then splits by page count


def _bars_arg(bars, nb, si):
    """'3-5' | 3 | [3, 5] -> (a, b) 1-based bars within a system of nb bars."""
    if isinstance(bars, (list, tuple)) and len(bars) in (1, 2):
        a, b = int(bars[0]), int(bars[-1])
    elif isinstance(bars, (int, float)) and not isinstance(bars, bool):
        a = b = int(bars)
    else:
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", str(bars))
        if not m:
            raise ValueError("bars: one bar (3), a range ('3-5' or [3, 5]) within the system")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if not 1 <= a <= b <= nb:
        raise ValueError(f"system {si + 1} has {nb} bars (detected bar lines; an estimate): ask for bars 1-{nb}, or "
                         "use crop=[x0, y0, x1, y1]")
    return a, b


def _bar_edges(s):
    """x edges of a system's bars: the system start, every detected bar line, the end (if the last bar is open)."""
    edges = [float(s["x0"])] + [float(x) for x in s["bar_x"]]
    if not s["bar_x"] or s["x1"] - s["bar_x"][-1] > 2 * s["space"]:
        edges.append(float(s["x1"]))
    return edges


def crop_image(g, box, max_width=1400, budget=IMAGE_BUDGET_PX, up_max=CROP_MAX_UP):
    """Page pixels box [x0, y0, x1, y1] -> (gray crop scaled to fit max_width and the pixel budget, scale, box)."""
    Hh, W = g.shape
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, x1 = max(0, min(W, x0)), max(0, min(W, x1))
    y0, y1 = max(0, min(Hh, y0)), max(0, min(Hh, y1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError("the crop is empty or outside the page (coordinates are in the page frame: x 0..frame, "
                         "y 0..page height in that frame)")
    cw, ch = x1 - x0, y1 - y0
    sc = min(max_width / cw, math.sqrt(budget / (cw * ch)), up_max)
    nw, nh = max(1, int(cw * sc)), max(1, int(ch * sc))
    return _resize(g[y0:y1, x0:x1], nw, nh), sc, [x0, y0, x1, y1]


def _systems_in_frame(lay, k, plist):
    out = []
    for i, s in enumerate(lay["systems"]):
        e = {"system": i + 1, "staves": s["staves"], "box": [int(round(v / k)) for v in s["box"]],
             "staff_y": [[round(ln[0] / k, 1), round(ln[-1] / k, 1)] for ln in s["staff_lines"]],
             "bar_x": [round(x / k, 1) for x in s["bar_x"]], "bars_est": s["bars_est"],
             "staff_space": round(s["space"] / k, 2)}
        if "first_bar_est" in plist[i]:
            e["first_bar_est"] = plist[i]["first_bar_est"]
        out.append(e)
    return out


def _write_png(path, png):
    path = str(path).strip().strip('"')
    if not path.lower().endswith(".png"):
        raise ValueError("save must be a .png path")
    H["writable"](path)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)
    return path


def _pages_frame(res, lay, plist, source, p, npg, rows, crop, bars, frame, systems, save, max_width, marks, steps,
                 T0):
    """sheet_pages' frame modes: systems=true (boxes + bar lines as text), crop=[...] or rows=S + bars='a-b'."""
    frame = float(frame or FRAME_W)
    if not 100 <= frame <= 20000:
        raise ValueError("frame: the width of the coordinate frame in px (default 1000)")
    W, Hh = lay["size"]
    k = W / frame
    fw = int(frame) if float(frame).is_integer() else frame
    res["frame"] = {"width": fw, "height": int(round(Hh / k)),
                    "note": "every x/y here is in this frame (the page scaled to this width), after deskew"}
    sysl = lay["systems"]
    if systems and crop is None and bars is None:
        res["systems"] = _systems_in_frame(lay, k, plist)
        res.pop("page_systems", None)
        res["how"] = ("crop one system's bars with rows=S, bars='a-b' (bars within the system), or any region with "
                      "crop=[x0, y0, x1, y1] in this frame; bar_x = detected bar lines (estimates)")
        res["steps_s"] = {**steps, "total": round(time.time() - T0, 3)}
        return res
    T1 = time.time()
    s_idx = None
    if bars is not None:
        idx, mode = _parse_rows(rows if rows is not None else 1, len(sysl))
        if mode != "systems" or len(idx) != 1 or not 0 <= idx[0] < len(sysl):
            raise ValueError(f"bars= needs one system: rows=S (page {p} has {len(sysl)} systems)")
        s_idx = idx[0]
        s = sysl[s_idx]
        edges = _bar_edges(s)
        a, b = _bars_arg(bars, len(edges) - 1, s_idx)
        pad = 0.6 * s["space"]
        box = [edges[a - 1] - (2.5 * s["space"] if a == 1 else pad), s["box"][1], edges[b] + pad, s["box"][3]]
        shown = {"system": s_idx + 1, "bars_in_system": f"{a}-{b}" if a != b else str(a)}
        if plist[s_idx].get("first_bar_est"):
            fb = plist[s_idx]["first_bar_est"]
            shown["bar_numbers_est"] = f"{fb + a - 1}-{fb + b - 1}" if a != b else str(fb + a - 1)
    else:
        if not isinstance(crop, (list, tuple)) or len(crop) != 4:
            raise ValueError("crop: [x0, y0, x1, y1] in the page frame (default 1000 px wide)")
        box = [float(v) * k for v in crop]
        shown = {}
    g, sc, px = crop_image(lay["gray"], box, max_width=max_width)
    rgb = np.repeat(g[:, :, None], 3, 2)
    if marks:                                          # ticks above the detected bar lines inside the crop
        for s in sysl:
            if s["bottom"] < px[1] or s["top"] > px[3]:
                continue
            yt = int((s["top"] - px[1]) * sc)
            ya, yb = max(0, yt - int(1.3 * s["space"] * sc)), max(0, yt - int(0.6 * s["space"] * sc))
            w = max(1, int(round(s["space"] * sc * 0.12)))
            for bx in s["bar_x"]:
                if px[0] <= bx < px[2]:
                    cx = int((bx - px[0]) * sc)
                    rgb[ya:yb, max(0, cx - w // 2):cx + w // 2 + 1] = MARK_RGB
    shown.update(crop=[round(v / k, 1) for v in px], image=f"{rgb.shape[1]}x{rgb.shape[0]}",
                 upscale=round(sc, 2))
    sp = [s["space"] for s in sysl if not (s["bottom"] < px[1] or s["top"] > px[3])]
    if sp:
        shown["px_per_staff_space"] = round(min(sp) * sc, 1)
    if marks:
        shown["marks"] = "red ticks = detected bar lines (estimates)"
    res["showing"] = shown
    res.pop("page_systems", None)
    steps["render"] = round(time.time() - T1, 3)
    pe = _h("_png_encode")
    if pe is None:
        raise RuntimeError("the server's PNG encoder isn't available")
    import base64
    png = pe(int(rgb.shape[1]), int(rgb.shape[0]), np.ascontiguousarray(rgb).tobytes())
    res["_image_png"] = base64.b64encode(png).decode()   # PC-VERIFY: the client shows the crop as an image
    if save:
        res["saved"] = _write_png(save, png)
    if s_idx is not None:
        nb = len(_bar_edges(sysl[s_idx])) - 1
        a, b = _bars_arg(bars, nb, s_idx)
        w = b - a + 1
        if b < nb:
            nxt = {"source": source, "page": p, "rows": s_idx + 1, "bars": f"{b + 1}-{min(nb, b + w)}"}
        elif s_idx + 1 < len(sysl):
            nb2 = len(_bar_edges(sysl[s_idx + 1])) - 1
            nxt = {"source": source, "page": p, "rows": s_idx + 2, "bars": f"1-{min(w, nb2)}"}
        else:
            nxt = {"source": source, "page": p + 1, "systems": True} if p < npg else None
        res["next"] = _call_hint("sheet_pages", nxt) if nxt else "that was the last bar of the last page"
    steps["total"] = round(time.time() - T0, 3)
    res["steps_s"] = steps
    return res


# ----------------------------------------------------------------------------- split a score across readers
def _brief_template():
    """The helper-reader brief (data/sheet/reader_brief.md; a copy in <state folder>/sheet/ overrides it)."""
    cands = []
    try:
        if H.get("STATE"):
            cands.append(os.path.join(os.path.dirname(H["STATE"]), "sheet", "reader_brief.md"))
    except Exception:
        pass
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sheet", "reader_brief.md"))
    for c in cands:
        try:
            with open(c, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return ("Read pages <<pages>> (bars <<bars>>) of <<source>> into fl-headless score data (flh-score/1, see "
            "docs/sheet_music.md). Parts: <<parts>>. Check with sheet_import(score=..., dry_run=true); save as "
            "<<output>>.")


def _partition(weights, n):
    """Contiguous groups of pages (weights) into n parts with the smallest largest part -> [(start, end_excl)]."""
    P = len(weights)
    n = max(1, min(n, P))
    pre = [0.0]
    for w in weights:
        pre.append(pre[-1] + w)
    INF = float("inf")
    best = [[INF] * (P + 1) for _ in range(n + 1)]
    cut = [[0] * (P + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for g in range(1, n + 1):
        for j in range(g, P + 1):
            for i in range(g - 1, j):
                c = max(best[g - 1][i], pre[j] - pre[i])
                if c < best[g][j] - 1e-9:
                    best[g][j], cut[g][j] = c, i
    out, j = [], P
    for g in range(n, 0, -1):
        i = cut[g][j]
        out.append((i, j))
        j = i
    return out[::-1]


def split_plan(source, n, parts=None, first_bar=1, out_dir=None):
    """Page ranges for n parallel readers, balanced by detected bars, each with a copy-paste brief."""
    T0 = time.time()
    src = open_source(source)
    pages = src["pages"]
    n = int(n)
    if n < 1:
        raise ValueError("split: the number of readers (2-6 is typical)")
    info, weights, all_known = [], [], True
    for i, pg in enumerate(pages):
        e = {"page": i + 1}
        if time.time() - T0 < SPLIT_BUDGET_S:
            try:
                lay = page_layout(pg, decode=False)
                e["systems"] = len(lay["systems"])
                e["bars_est"] = sum(s["bars_est"] for s in lay["systems"])
            except Unsupported as ex:
                e["note"] = str(ex)[:120]
        if "bars_est" not in e or not e["bars_est"]:
            all_known = False
        info.append(e)
    known = [e["bars_est"] for e in info if e.get("bars_est")]
    med = sorted(known)[len(known) // 2] if known else 1
    weights = [float(e.get("bars_est") or med) for e in info]
    groups = _partition(weights, n)
    fb = 1 if first_bar is None else int(first_bar)
    starts, c = [], fb
    for e in info:
        starts.append(c)
        c += int(e.get("bars_est") or med)
    base = str(source[0] if isinstance(source, (list, tuple)) else source)
    od = str(out_dir).strip().strip('"') if out_dir else (os.path.dirname(os.path.abspath(base)) or ".")
    tpl = _brief_template()
    plist = ", ".join(str(x) for x in parts) if parts else \
        "one per staff/instrument, lowercase, the SAME names as the other readers (agree on them first)"
    readers = []
    for k, (a, b) in enumerate(groups):
        pr = f"{a + 1}-{b}" if b - a > 1 else str(a + 1)
        br = f"~{starts[a]}-{starts[b - 1] + int(info[b - 1].get('bars_est') or med) - 1}"
        out = os.path.join(od, f"score_p{a + 1}-{b}.json" if b - a > 1 else f"score_p{a + 1}.json")
        text = (tpl.replace("<<source>>", base).replace("<<pages>>", pr).replace("<<bars>>", br)
                .replace("<<parts>>", plist).replace("<<output>>", out).replace("<<first_page>>", str(a + 1)))
        readers.append({"reader": k + 1, "pages": pr, "bars_est": br, "output": out, "brief": text})
    res = {"source": os.path.basename(base), "pages": len(pages), "readers": readers,
           "page_info": info if len(info) <= 40 else info[:40],
           "bars_note": ("bar numbers are estimates from detected bar lines: the brief tells each reader to use "
                         "the numbers printed on the page") if all_known else
                        ("some pages couldn't be analysed: split by page count; bar ranges are rough"),
           "then": [_call_hint("sheet_merge", {"readings": [r["output"] for r in readers],
                                               "output": os.path.join(od, "score_merged.json")}),
                    _call_hint("sheet_import", {"score": os.path.join(od, "score_merged.json"), "dry_run": True})],
           "took_s": round(time.time() - T0, 3)}
    if len(readers) < n:
        res["note"] = f"only {len(readers)} reader(s): the source has {len(pages)} page(s)"
    return res


def _parse_rows(rows, n):
    """rows: None | int | [ints] | 'all' | 'page' | '2-4' -> (list of 0-based system indices, mode)."""
    if rows is None:
        return [0], "systems"
    if isinstance(rows, str):
        r = rows.strip().lower()
        if r in ("page", "overview"):
            return [], "page"
        if r == "all":
            return list(range(n)), "systems"
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", r)
        if m:
            return [i - 1 for i in range(int(m.group(1)), int(m.group(2)) + 1)], "systems"
        if r.isdigit():
            return [int(r) - 1], "systems"
        raise ValueError("rows: a system number, a list, '2-4', 'all' or 'page'")
    if isinstance(rows, (int, float)):
        return [int(rows) - 1], "systems"
    return [int(x) - 1 for x in rows], "systems"


def sheet_pages(source, page=None, rows=None, zoom=2.0, max_width=1400, marks=True, crop=None, bars=None,
                frame=FRAME_W, systems=False, save=None, split=None, parts=None, first_bar=1):
    """See the tool description."""
    T0 = time.time(); steps = {}
    if split:
        return split_plan(source, split, parts=parts, first_bar=first_bar, out_dir=save)
    list_systems = bool(systems)
    src = open_source(source)
    pages = src["pages"]
    npg = len(pages)
    p = int(page or 1)
    if not 1 <= p <= npg:
        raise ValueError(f"page {p} doesn't exist: the source has {npg} page(s)")
    pg = pages[p - 1]
    lay = page_layout(pg)
    steps["decode+layout"] = round(time.time() - T0, 3)
    systems = lay["systems"]
    n = len(systems)
    zoom = float(zoom or 2.0); max_width = int(max(400, min(int(max_width or 1400), 2400)))
    res = {"source": os.path.basename(str(source if not isinstance(source, (list, tuple)) else source[0])),
           "pages": npg, "page": p, "page_size": lay["size"], "systems_on_page": n}
    if lay["skew_deg"]:
        res["deskewed_deg"] = lay["skew_deg"]
    before, known = 0, True                            # bars on earlier pages (only when they were analysed)
    for q in pages[:p - 1]:
        l2 = _LAYOUTS.get(_page_key(q))
        if l2 is None:
            known = False; break
        before += sum(s["bars_est"] for s in l2["systems"])
    cum = before
    plist = []
    for i, s in enumerate(systems):
        plist.append({"system": i + 1, "staves": s["staves"], "bars_est": s["bars_est"],
                      **({"first_bar_est": cum + 1} if known else {})})
        cum += s["bars_est"]
    res["page_systems"] = plist
    if src["notes"]:
        res["notes"] = src["notes"][:4]
    if list_systems or crop is not None or bars is not None:
        return _pages_frame(res, lay, plist, source, p, npg, rows, crop, bars, frame, list_systems, save, max_width,
                            marks, steps, T0)
    idx, mode = _parse_rows(rows, n)
    T1 = time.time()
    if not n or mode == "page":
        rgb = page_overview(lay, max_width)
        res["showing"] = {"view": "whole page", "image": f"{rgb.shape[1]}x{rgb.shape[0]}",
                          "marks": "red numbers in the left margin = system numbers"}
        if not n:
            sp_hint = "the scan may be too small (staff lines need ~2 px and ~6+ px between them) or not sheet music"
            res["warning"] = ("no staves found on this page: " + sp_hint + "; read the overview, or export the page "
                              "at a higher resolution (200-300 dpi)")
        nxt = {"source": source, "page": p, "rows": 1} if n else ({"source": source, "page": p + 1}
                                                                  if p < npg else None)
    else:
        bad = [i + 1 for i in idx if not 0 <= i < n]
        if bad:
            raise ValueError(f"system(s) {bad} don't exist: page {p} has {n} system(s)")
        rgb, info = render_systems(lay, idx, zoom=zoom, max_width=max_width, marks=marks)
        if info["px_per_staff_space"] < 11:
            idx2 = idx[:max(1, len(idx) // 2)]
            if len(idx2) < len(idx):                   # too many at once to stay sharp: show fewer
                rgb, info = render_systems(lay, idx2, zoom=zoom, max_width=max_width, marks=marks)
                res["fewer_shown"] = f"showing systems {idx2[0] + 1}-{idx2[-1] + 1} so they stay sharp"
                idx = idx2
        shown = []
        for k, i in enumerate(idx):
            s = systems[i]
            w = max(1.0, s["x1"] - s["x0"])
            ent = {"system": i + 1, "staves": s["staves"], "bars_est": s["bars_est"],
                   "bar_lines_at_pct": [round(100 * (x - s["x0"]) / w) for x in s["bar_x"]]}
            if "double" in s["bar_kinds"]:
                ent["double_or_repeat_bar_lines"] = [j + 1 for j, kd in enumerate(s["bar_kinds"]) if kd == "double"]
            if info["systems"][k]["strips"] > 1:
                ent["strips"] = info["systems"][k]["strips"]
                ent["strip_cuts"] = "at bar lines" if info["systems"][k]["cut_at_bar_lines"] else \
                    "mid-bar (no bar line near): the strips overlap a little - don't read the overlap twice"
            if known:
                ent["first_bar_est"] = plist[i]["first_bar_est"]
            shown.append(ent)
        res["showing"] = {"systems": shown, "image": f"{info['size'][0]}x{info['size'][1]}",
                          "px_per_staff_space": info["px_per_staff_space"]}
        if marks:
            res["showing"]["marks"] = ("red = detected bar lines (ticks) and bar numbers within each system - "
                                       "estimates; trust the music over the marks")
        if any(x.get("strips", 1) > 1 for x in shown):
            res["showing"]["layout"] = "a long system is cut into strips: left part on top, pink line between"
        last = idx[-1] + 1
        nxt = ({"source": source, "page": p, "rows": last + 1} if last < n else
               ({"source": source, "page": p + 1} if p < npg else None))
    if n and min(s["space"] for s in systems) < 10:
        res["low_resolution"] = ("staff lines are under 10 px apart: bar-line estimates get unreliable and small "
                                 "accidentals blur - a 200-300 dpi scan reads better")
    if lay.get("single_staves_only"):
        res["note_systems"] = ("no staves are joined by a bar line here, so every staff counts as its own system "
                               "(if this is piano/ensemble music with braces only, read staves in pairs)")
    steps["render"] = round(time.time() - T1, 3)
    pe = _h("_png_encode")
    if pe is None:
        raise RuntimeError("the server's PNG encoder isn't available")
    import base64
    T2 = time.time()
    png = pe(int(rgb.shape[1]), int(rgb.shape[0]), np.ascontiguousarray(rgb).tobytes())
    steps["png"] = round(time.time() - T2, 3)
    res["_image_png"] = base64.b64encode(png).decode()   # PC-VERIFY: the client shows it as an image (checklist 1)
    res["next"] = _call_hint("sheet_pages", nxt) if nxt else "that was the last system: write the ABC/MusicXML " \
        "and check it with " + _call_hint("sheet_import", {"abc": "...", "dry_run": True})
    if p == 1 and (rows is None):
        res["how"] = ("read each system into ABC (one voice per staff/hand; bar lines as in the picture), then "
                      "sheet_import(dry_run=true) checks every bar's length, ties, ranges exactly")
    steps["total"] = round(time.time() - T0, 3)
    res["steps_s"] = steps
    return res


def _project_has_notes(project):
    try:
        return sum(sum((p.get("notes_per_channel") or {}).values()) for p in H["flp_patterns"](project)) > 0
    except Exception:
        return True


def _unique_name(project, base, taken):
    try:
        names = {str(p["name"]).lower() for p in H["flp_patterns"](project)}
    except Exception:
        names = set()
    names |= {t.lower() for t in taken}
    if base.lower() not in names:
        return base
    i = 2
    while f"{base} {i}".lower() in names:
        i += 1
    return f"{base} {i}"


def _channels(project):
    try:
        return [(c["index"], c.get("name")) for c in H["flp_list"](project)["channels"]]
    except Exception:
        return []


def _channel_map(channel, voices, project, clone_from):
    """-> ({voice: channel index}, extra ops for new channels, notes)."""
    names = list(voices)
    if isinstance(channel, str) and channel.strip().lower() == "new":
        chans = _channels(project)
        if not chans:
            raise ValueError("couldn't read the project's channels")
        src = int(clone_from) if clone_from is not None else chans[0][0]
        nxt = max(i for i, _ in chans) + 1
        ops, cmap, style = [], {}, []
        for k, v in enumerate(names):
            ops.append({"tool": "flp_add_channel", "clone_from": src})
            cmap[v] = nxt + k; style.append({"channel": nxt + k, "name": str(v)[:40]})
        ops.append({"tool": "flp_style_channels", "channels": style})
        return cmap, ops, [f"new channels {nxt}-{nxt + len(names) - 1} are copies of channel {src} (its sound): "
                           "give each the sound you want"]
    if isinstance(channel, bool):
        raise ValueError("channel: an index, a list (one per voice), {voice: index} or 'new'")
    if isinstance(channel, (int, float)) or (isinstance(channel, str) and channel.strip().isdigit()):
        return {v: int(channel) for v in names}, [], []
    if isinstance(channel, list):
        if len(channel) < len(names):
            raise ValueError(f"channel list has {len(channel)} entries for {len(names)} voices {names}")
        return {v: int(channel[i]) for i, v in enumerate(names)}, [], []
    if isinstance(channel, dict):
        cmap = {}
        for k, v in enumerate(names):
            hit = channel.get(v, channel.get(str(k + 1), channel.get(str(v).lower())))
            if hit is not None:
                cmap[v] = int(hit)
        miss = [v for v in names if v not in cmap]
        notes = [f"voices without a channel are not written: {miss}"] if miss else []
        if not cmap:
            raise ValueError(f"no channel given for the voices {names}")
        return cmap, [], notes
    raise ValueError("channel: an index, a list (one per voice), {voice: index} or 'new'")


def _journal_record(project, entry):
    try:
        import flh_store
        if not flh_store.H:
            flh_store.H = H
        j = flh_store.journal_get(project).get(JKEY) or {}
        hist = (list(j.get("history") or []) + ([{k: j[k] for k in ("when", "source", "file", "title") if k in j}]
                                                 if j.get("when") else []))[-5:]
        flh_store.journal_update(project, set={JKEY: None})
        flh_store.journal_update(project, set={JKEY: {**entry, "history": hist}})
        return True
    except Exception as e:
        return f"journal not updated: {type(e).__name__}: {e}"


def sheet_import(abc=None, musicxml=None, project=None, channel=None, output_mid=None, title=None, strict=False,
                 reference_audio=None, dry_run=False, force=False, pages=None, place_at_bar=None, set_tempo=None,
                 clone_from=None, full=False, score=None, parts=None):
    """See the tool description."""
    T0 = time.time(); steps = {}
    if isinstance(parts, str):
        parts = [p.strip() for p in parts.split(",") if p.strip()]
    score_, src = load_score(abc, musicxml, score, parts=parts)
    left_out = []
    sinfo = getattr(score_, "score_info", None)
    if sinfo and parts is None and isinstance(channel, dict):
        mapped = [p for p in sinfo["parts"] if p in channel or p.lower() in channel]
        left_out = [p for p in sinfo["parts"] if p not in mapped]
        if mapped and left_out:                        # parts without a channel are not read or checked
            score_, src = load_score(score=score, parts=mapped)
            sinfo = score_.score_info
        else:
            left_out = []
    score = score_
    steps["parse"] = round(time.time() - T0, 3)
    T1 = time.time()
    issues = _validate(score, strict)
    voices, isum = _inline(score)
    steps["validate"] = round(time.time() - T1, 3)
    summ = _score_summary(score, voices, isum, issues)
    if title:
        summ["title"] = title
    res = {"source": src["type"] + (f" {os.path.basename(src['file'])}" if src.get("file") else " (inline text)"),
           "summary": summ}
    if sinfo:
        if pages is None and sinfo.get("pages"):
            pages = sinfo["pages"]
        if place_at_bar is None:
            place_at_bar = max(1, sinfo["first_bar"])
        res["score_data"] = _score_data_report(sinfo, place_at_bar, full)
        rc = _range_check(score_, sinfo.get("part_info"))
        if rc:
            res["score_data"]["outside_instrument_range"] = rc
        if left_out:
            res["score_data"]["left_out"] = f"parts without a channel were not read: {left_out}"
    elif place_at_bar is None:
        place_at_bar = 1
    flags = None
    if reference_audio:
        T2 = time.time()
        try:
            cmp_ = compare_audio(score, voices, reference_audio)
            flags = cmp_["flags"]
            res["audio_check"] = {k: v for k, v in cmp_.items() if k != "flags"}
            res["audio_check"]["bars_flagged"] = len(flags)
        except Exception as e:
            res["audio_check"] = {"error": f"{type(e).__name__}: {e}", "note": "the score itself is unaffected"}
        steps["audio_check"] = round(time.time() - T2, 3)
    min_block = 1 if strict else 2
    blocking = [i for i in issues if SEV.get(str(i.get("severity", "med")).lower(), 2) >= min_block]
    reread = _reread(issues, flags, pages, min_block if blocking else 1)
    if issues:
        res["issues"] = [{k: i[k] for k in ("severity", "bar", "voice", "message", "fix") if i.get(k) is not None}
                         for i in issues[:(60 if full else 12)]]
        if len(issues) > len(res["issues"]):
            res["issues_more"] = len(issues) - len(res["issues"])
    if reread:
        res["reread"] = reread[:(60 if full else 15)]
    if blocking and not force:
        res.update(written=False, blocked=True,
                   why=f"{len(blocking)} issue(s) of severity {'low or worse (strict)' if strict else 'med or worse'}"
                       ": nothing was written",
                   next="look at those bars again (" + _call_hint("sheet_pages", {"source": "...", "page": 1,
                                                                                  "rows": 1}) +
                        "), fix the ABC/MusicXML and call again; force=true writes it anyway")
        if sinfo:
            res["next"] = ("look at those bars again (sheet_pages crop / rows+bars), fix them in the score data "
                           "(beats 0-based in quarter notes) and call again; force=true writes it anyway")
        res["steps_s"] = {**steps, "total": round(time.time() - T0, 3)}
        return res
    if blocking:
        res["forced"] = f"written despite {len(blocking)} blocking issue(s) (force=true)"
    mm = _main_meter(score); qpb = _qpb(*mm)
    ttl = (title or getattr(score, "title", None) or "Sheet").strip()[:40] or "Sheet"
    plan = {"midi": output_mid, "project": project}
    if dry_run:
        res.update(written=False, dry_run=True)
        res["would_write"] = {k: v for k, v in plan.items() if v} or "nothing (give output_mid and/or project)"
        if voices and (full or not sinfo):
            res["inline"] = {v: ns_[:(400 if full else 24)] for v, ns_ in voices.items()}
            res["inline_format"] = "[bar, length_bars, pitch, velocity] in song bars" + \
                ("" if full else " (first 24 per voice; full=true for all)")
        res["next"] = "fix anything in reread/issues, then call again without dry_run (with project/channel " \
                      "or output_mid)"
        res["steps_s"] = {**steps, "total": round(time.time() - T0, 3)}
        return res
    written, notes = {}, []
    if output_mid:
        T3 = time.time()
        if not str(output_mid).lower().endswith((".mid", ".midi")):
            raise ValueError("output_mid must end in .mid")
        H["writable"](output_mid)
        os.makedirs(os.path.dirname(os.path.abspath(output_mid)) or ".", exist_ok=True)
        _sn().to_midi(score, output_mid)
        written["midi"] = output_mid
        steps["midi"] = round(time.time() - T3, 3)
    if project:
        T4 = time.time()
        if not os.path.isfile(project):
            raise ValueError(f"no such project: {project}")
        if channel is None:
            res.update(written=bool(written), needs_channel=True,
                       channels=[f"{i}: {n}" for i, n in _channels(project)][:40],
                       voices=list(voices),
                       next="call again with channel= (one index for all voices, a list per voice, {voice: index}, "
                            "or 'new' = one new channel per voice, copied from clone_from)")
            if written:
                res["written_files"] = written
            res["steps_s"] = {**steps, "total": round(time.time() - T0, 3)}
            return res
        cmap, ops, cn = _channel_map(channel, voices, project, clone_from)
        notes += cn
        empty = not _project_has_notes(project)
        do_song = empty if set_tempo is None else bool(set_tempo)
        tmap = [(float(bb), float(bpm)) for bb, bpm in (isum.get("tempo_map") or [])] or \
            [(1.0, float(bpm)) for _, bpm in _tempos(score)[:1]]
        info = H["flp_project_info"](project)
        proj_q = _qpb(int(info.get("time_sig_num") or 4), int(info.get("time_sig_beat") or 4))
        mtxt = f"{mm[0]}/{mm[1]}"
        if do_song:
            # the meter first: flp_tempo_write reads its [bar, bpm] points in the project's meter at that step
            ops.append({"tool": "flp_project_info", "time_sig_num": mm[0], "time_sig_beat": mm[1]})
            if tmap:
                ops.append({"tool": "flp_set_tempo", "bpm": round(tmap[0][1], 3)})
                if len({round(b, 3) for _, b in tmap}) > 1 and "flp_tempo_write" in H["TOOLS"]:
                    off = float(place_at_bar or 1) - 1
                    ops.append({"tool": "flp_tempo_write",
                                "points": [[round(bb + off, 4), round(bpm, 3)] for bb, bpm in tmap]})
            proj_q = qpb
            song = {"tempo": tmap[0][1] if tmap else None, "meter": mtxt,
                    **({"tempo_changes": len(tmap) - 1} if len(tmap) > 1 else {})}
            if isum.get("tempo_assumed"):
                notes.append("the score gives no tempo: 120 bpm was assumed")
            if len({tuple(x[1:]) for x in _meters(score)}) > 1:
                notes.append(f"the score changes meter; the FL project uses its main meter ({mtxt}) - the notes "
                             "are placed in exact beats, and a MIDI file (output_mid) carries every change")
        else:
            song = None
            pt = info.get("tempo_bpm")
            if tmap and pt and abs(float(pt) - tmap[0][1]) > 0.5:
                notes.append(f"the project plays at {pt:g} bpm, the score says {tmap[0][1]:g}: kept the project's "
                             "tempo (set_tempo=true to use the score's)")
            if abs(proj_q - qpb) > 1e-6:
                notes.append(f"the project's meter differs from the score's {mtxt}: notes are placed in exact beats, "
                             "so FL's bar numbers won't match the score's")
        pats, taken = [], []
        total_q = max([(n[0] - 1 + n[1]) * qpb for v in voices.values() for n in v] or [qpb])
        for v, ns_ in voices.items():
            if v not in cmap or not ns_:
                continue
            nm = _unique_name(project, f"{ttl} - {v}" if len(voices) > 1 else ttl, taken)
            taken.append(nm)
            op = {"tool": "flp_new_pattern", "name": nm}
            if place_at_bar:
                op.update(place_at_bar=float(place_at_bar), length_bars=max(1, math.ceil(total_q / proj_q - 1e-9)))
            ops.append(op)
            items = [{"note": int(p), "bar": 1, "beat": round((b - 1) * qpb, 5),
                      "beats": round(max(ln * qpb, 1 / 96), 5),
                      "velocity": int(max(1, min(127, vel)))} for b, ln, p, vel in ns_]
            ops.append({"tool": "flp_add_notes", "pattern": nm, "channel": cmap[v], "notes": items})
            pats.append({"pattern": nm, "voice": v, "channel": cmap[v], "notes": len(items)})
        if not pats:
            raise ValueError("nothing to write: no voice has both notes and a channel")
        # PC-VERIFY: one save/reload in the open FL; patterns "<title> - <voice>" at place_at_bar on free tracks,
        # tempo + time signature only on an empty project (checklists/sheet.md 5-8)
        r = H["TOOLS"]["flp_edit"][0](project=project, ops=ops)
        written["project"] = project
        res["patterns"] = pats
        if song:
            res["song_settings"] = song
        if isinstance(r, dict) and r.get("queued"):
            res["queued"] = True
            res["queued_note"] = "lazy mode: FL gets it at the next sync (flh_sync) - checked on the shadow copy"
        steps["project"] = round(time.time() - T4, 3)
        entry = {"when": time.strftime("%Y-%m-%d %H:%M"), "source": src["type"], "file": src.get("file"),
                 "title": ttl, "validation": summ.get("issues") or {}, "forced": bool(blocking),
                 "patterns": [p["pattern"] for p in pats], "bars": summ["bars"],
                 **({"audio_flags": [f["bar"] for f in flags]} if flags is not None else {})}
        if sinfo:                                      # what a later composer / arranger call can build on
            entry.update(score_bars=f"{sinfo['first_bar']}-{sinfo['last_bar']}", placed_at_bar=place_at_bar,
                         sections=sinfo["sections"][:40],
                         chords=" ".join(f"{b}{'' if not bt else '.' + _fmt_q(bt)}:{c}"
                                         for b, bt, c in sinfo["chords"][:400]) or None)
        jr = _journal_record(project, entry)
        if jr is not True:
            notes.append(jr)
        else:
            res["journal"] = "source recorded in the song journal (sheet)"
    res["written"] = bool(written)
    if written:
        res["written_files"] = written
    else:
        res["note"] = "checked only: give output_mid and/or project + channel to write"
    if notes:
        res["notes"] = notes
    nxt = []
    if project:
        nxt.append("listen (fl_listen) and compare with the sheet")
        nxt.append("make it sound played: flp_perform (piano/keys) or flp_realism (strings, winds, choir)")
        nxt.append("review: fl_review / flp_score")
    elif written:
        nxt.append("import the MIDI into FL, or call again with project + channel")
    res["next"] = nxt
    res["steps_s"] = {**steps, "total": round(time.time() - T0, 3)}
    return res


def _as_score(x):
    """A reading: ABC text / .abc path, MusicXML text / path, score data (dict / JSON / .json) -> (score, label)."""
    if _is_score_data(x):
        return load_score(score=x)[0], "score data"
    s = str(x).strip().strip('"')
    if _is_path(s, (".musicxml", ".xml", ".mxl")) or s.lstrip().startswith("<"):
        return load_score(musicxml=x)[0], "musicxml"
    return load_score(abc=x)[0], "abc"


def _project_part(project, pattern=None, channel=None):
    """A project pattern's notes -> [(start_q, len_q, pitch)] (pattern time)."""
    d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
    pats = H["_patterns"](d, ev)
    pid = H["_pattern_id"](d, ev, pattern) if pattern is not None else None
    if pid is None:
        raise ValueError("give pattern= (name or id) for the project part to compare")
    out = []
    for n in map(H["_rec_to_note"], pats.get(pid, {"notes": []})["notes"]):
        if channel is not None and n["ch"] != int(channel):
            continue
        out.append((n["pos"] / P, n["len"] / P, int(n["key"])))
    return sorted(out)


def _fmt_q(q):
    return f"{q:.3f}".rstrip("0").rstrip(".")


def sheet_diff(a, b, pattern=None, channel=None, voice=None, full=False):
    """See the tool description."""
    T0 = time.time()
    isproj = [isinstance(x, str) and x.strip().lower().endswith(".flp") and os.path.isfile(x.strip()) for x in (a, b)]
    if all(isproj):
        raise ValueError("compare a reading (ABC/MusicXML) with a project part, or two readings; for two projects "
                         "use flp_diff")
    if not any(isproj) and _is_score_data(a) and _is_score_data(b):
        out = {"a": "score data", "b": "score data",
               **_plain(_sn().diff_score_data(a, b, parts=[voice] if voice else None, max_bars=200 if full else 40)),
               "format": "beat = 0-based quarter notes, as in the data; note@beat/length"}
        out["took_s"] = round(time.time() - T0, 3)
        return out
    if not any(isproj):
        sa, la = _as_score(a); sb, lb = _as_score(b)
        r = _sn().diff(sa, sb)
        out = {"a": la, "b": lb, "diff": _plain(r)}
        if isinstance(r, dict):
            out = {"a": la, "b": lb, **_plain(r)}
        out["took_s"] = round(time.time() - T0, 3)
        return out
    rd, proj = (b, a) if isproj[0] else (a, b)
    sc, lab = _as_score(rd)
    voices, _ = _inline(sc)
    qpb = _qpb(*_main_meter(sc))
    if voice is not None:
        if str(voice) not in voices:
            raise ValueError(f"no voice {voice!r}: the reading has {list(voices)}")
        voices = {str(voice): voices[str(voice)]}
    got = _project_part(proj, pattern, channel)
    key = lambda n: (round(n[0] * 48), n[2])           # onset on a 1/48-quarter grid + pitch
    chosen = None
    if voice is None and len(voices) > 1:              # the voice(s) the pattern holds: best note overlap
        gk = {key(n) for n in got}
        opts = {v: [v] for v in voices}; opts["(all voices)"] = list(voices)
        best = -1.0
        for lab_, vs_ in opts.items():
            rk = {key(((bb - 1) * qpb, 0, int(p))) for v in vs_ for bb, _, p, _ in voices[v]}
            jac = len(rk & gk) / max(1, len(rk | gk))
            if jac > best + 1e-9:
                best, chosen = jac, lab_
        voices = {v: voices[v] for v in opts[chosen]}
    ref = sorted(((bb - 1) * qpb, ln * qpb, int(p)) for v in voices.values() for bb, ln, p, _ in v)
    ra = {}; rb = {}
    for n in ref:
        ra.setdefault(key(n), []).append(n)
    for n in got:
        rb.setdefault(key(n), []).append(n)
    bars = {}
    for k in set(ra) | set(rb):
        na, nb = len(ra.get(k, [])), len(rb.get(k, []))
        q = k[0] / 48; bar = int(q // qpb) + 1
        e = bars.setdefault(bar, {"bar": bar, "only_reading": [], "only_project": [], "length_differs": []})
        lbl = f"{note_name(k[1])}@{_fmt_q(q - (bar - 1) * qpb + 1)}"
        if na > nb:
            e["only_reading"] += [lbl] * (na - nb)
        elif nb > na:
            e["only_project"] += [lbl] * (nb - na)
        else:
            la_ = sorted(x[1] for x in ra[k]); lb_ = sorted(x[1] for x in rb[k])
            if any(abs(x - y) > 1 / 24 for x, y in zip(la_, lb_)):
                e["length_differs"].append(f"{lbl} ({_fmt_q(la_[0])} vs {_fmt_q(lb_[0])} beats)")
    diff = [{k: v for k, v in e.items() if v} for e in sorted(bars.values(), key=lambda e: e["bar"])
            if e["only_reading"] or e["only_project"] or e["length_differs"]]
    return {"reading": lab, **({"voice": f"{chosen} (picked: best match)"} if chosen else
                               ({"voice": voice} if voice else {})),
            "project": os.path.basename(proj), "pattern": pattern, "notes_reading": len(ref),
            "notes_project": len(got), "same": not diff, "bars_differ": len(diff),
            "bars": diff[:(200 if full else 20)], "format": "note@beat (beat 1 = bar start, in quarter notes)",
            "took_s": round(time.time() - T0, 3)}


def _dump_score(d):
    """Score data as JSON with one bar per line (readable, diff-friendly)."""
    head = {k: v for k, v in d.items() if k != "bars"}
    lines = ["{"] + [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in head.items()]
    bars = d.get("bars") or {}
    lines.append('  "bars": {')
    items = list(bars.items())
    for i, (k, v) in enumerate(items):
        lines.append(f"    {json.dumps(str(k))}: {json.dumps(v, ensure_ascii=False)}" + ("," if i < len(items) - 1
                                                                                         else ""))
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def sheet_merge(readings, output=None, prefer=None, check=True, full=False):
    """See the tool description."""
    T0 = time.time()
    SN = _sn()
    if isinstance(readings, (str, dict)):
        r0 = readings
        if isinstance(r0, str) and os.path.isdir(r0.strip().strip('"')):
            d = r0.strip().strip('"')
            readings = sorted((os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".json")),
                              key=_natural_key)
            if output:
                ob = os.path.basename(str(output))
                readings = [x for x in readings if os.path.basename(x) != ob]
        else:
            readings = [r0]
    if not readings:
        raise ValueError("readings: a list of score-data readings (.json paths, JSON text or objects), or a folder "
                         "of .json files")
    merged, rep = SN.merge_score_data(readings, prefer=prefer)
    res = {k: v for k, v in rep.items() if v not in (None, [], {})}
    if not full and len(res.get("conflicts", [])) > 12:
        res["conflicts_more"] = len(res["conflicts"]) - 12
        res["conflicts"] = res["conflicts"][:12]
    blocked = bool(rep["conflicts"]) and prefer is None
    if check:
        T1 = time.time()
        try:
            sc = SN.parse_score_data(merged)
            iss = _validate(sc)
            blk = [i for i in iss if SEV.get(str(i.get("severity", "med")).lower(), 2) >= 2]
            res["check"] = {"issues": len(iss), "blocking": len(blk),
                            "beat_check": sc.score_info["beat_check"]}
            rr = _reread(iss, None, sc.score_info.get("pages"), 2)
            if rr:
                res["check"]["reread"] = rr[:(40 if full else 10)]
        except ValueError as e:
            res["check"] = {"error": str(e)}
        res.setdefault("steps_s", {})["check"] = round(time.time() - T1, 3)
    if output:
        out = str(output).strip().strip('"')
        if not out.lower().endswith(".json"):
            raise ValueError("output must be a .json path")
        if blocked:
            res.update(written=False, why="readings disagree in the bars listed under 'conflicts': re-read those "
                       "bars and fix one reading, or pass prefer=<index of the reading to trust> (0-based)")
        else:
            H["writable"](out)
            os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                f.write(_dump_score(merged))
            res.update(written=True, output=out)
            res["next"] = _call_hint("sheet_import", {"score": out, "dry_run": True})
    else:
        res["written"] = False
        if full or len(json.dumps(merged)) < 6000:
            res["merged"] = merged
        else:
            res["note"] = ("give output= (a .json path) to save the merged score (it's too big to show; full=true "
                           "shows it)")
    res.setdefault("steps_s", {})["total"] = round(time.time() - T0, 3)
    return res


def _plain(o, depth=0):
    if depth > 8:
        return str(o)
    if isinstance(o, dict):
        return {str(k): _plain(v, depth + 1) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [_plain(v, depth + 1) for v in o][:400]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    if hasattr(o, "__dict__"):
        return _plain(vars(o), depth + 1)
    return str(o)


def _bar_list(bs):
    """[1, 2, 3, 5] -> 'bars 1-3 and 5'."""
    bs = sorted(set(int(b) for b in bs if b is not None))
    if not bs:
        return ""
    parts, a = [], bs[0]; prev = a
    for b in bs[1:] + [None]:
        if b is not None and b == prev + 1:
            prev = b; continue
        parts.append(f"{a}" if a == prev else f"{a}-{prev}")
        if b is not None:
            a = prev = b
    return ("bar " if len(bs) == 1 else "bars ") + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1
                                                     else parts[0])


def explain_score(score, voices, isum):
    """The score in plain sentences (key, meter, tempo, pickup, repeats, endings, jumps, ranges)."""
    say = []
    t = isum.get("title") or getattr(score, "title", None)
    comp = getattr(score, "composer", None)
    if t:
        say.append(f'"{t}"' + (f" by {comp}" if comp else "") + ".")
    keys = isum.get("keys") or []
    if keys:
        say.append(f"Key: {keys[0][1]}" + ("" if len(keys) == 1 else " - then " + ", ".join(
            f"{k} from bar {b}" for b, k in keys[1:4])) + ".")
    meters = isum.get("meters") or []
    beat_word = {"2": "half notes", "4": "quarter notes", "8": "eighth notes", "16": "sixteenth notes"}
    for i, (b, m) in enumerate(meters[:4]):
        if m == "free":
            say.append(f"From bar {b} there's no meter (free time)."); continue
        num, den = m.split("/")
        feel = (" - compound: the beat is a dotted quarter, three eighths each"
                if den == "8" and num in ("6", "9", "12")
                else " - an uneven meter: the beats group unevenly" if num in ("5", "7", "11") else "")
        say.append(("Meter" if i == 0 else f"From bar {b} the meter is") + f" {m}: {num} "
                   f"{beat_word.get(den, f'1/{den} notes')} per bar{feel}.")
    tempos = isum.get("tempos") or []
    if tempos:
        bpm = float(tempos[0][1])
        word = ("very slow" if bpm < 60 else "slow" if bpm < 76 else "walking pace" if bpm < 108 else "lively"
                if bpm < 140 else "fast")
        say.append(f"Tempo {bpm:g} beats per minute ({word})" + ("" if len(tempos) == 1 else ", with tempo changes at "
                   + ", ".join(f"bar {b} ({x:g})" for b, x in tempos[1:4])) + ".")
    elif isum.get("tempo_assumed"):
        say.append("No tempo is written (120 bpm is assumed for playback).")
    meas = [x if isinstance(x, dict) else vars(x) for x in (getattr(score, "measures", None) or [])]
    if meas and isum.get("pickup"):
        q = meas[0].get("len_ticks", 0) / _ppq(score)
        say.append(f"It starts with a pickup (upbeat) of {q:g} beat(s) before the first full bar.")
    if meas:
        say.append(f"{len(meas)} written bars" + (f", {isum['bars_played']} played with the repeats" if
                                                  isum.get("bars_played") and isum["bars_played"] != len(meas) else "")
                   + ".")
    start = meas[0].get("num") if meas else None
    for x in meas:
        if x.get("rep_start"):
            start = x.get("num")
        if x.get("rep_end"):
            times = int(x.get("rep_times") or 2)
            say.append(f"Bars {start}-{x.get('num')} are played {'twice' if times == 2 else f'{times} times'} "
                       "(repeat signs).")
            start = None
    voltas = {}
    for x in meas:
        for n in (x.get("volta") or []):
            voltas.setdefault(int(n), []).append(x.get("num"))
    if voltas:
        say.append("Endings: " + "; ".join(f"ending {n} is {_bar_list(b)}" for n, b in sorted(voltas.items())) +
                   " - the first time through play ending 1; on the repeat skip it and play ending 2.")
    plain = {"dc": "D.C. - go back to the start", "ds": "D.S. - go back to the sign (segno)", "segno": "the sign "
             "(segno) a D.S. jumps back to", "fine": "Fine - the piece ends here after a jump back",
             "coda": "the coda starts here", "tocoda": "To Coda - after the jump back, skip from here to the coda"}
    for x in meas:
        for nv in (x.get("nav") or [])[:3]:
            say.append(f"Bar {x.get('num')}: {plain.get(str(nv).lower(), str(nv))}.")
    if isum.get("play_order") and len(say) < 40:
        say.append(f"Playing order of the bars: {isum['play_order']}.")
    vinfo = isum.get("voices") or {}
    for name, ns_ in voices.items():
        ps = [n[2] for n in ns_]
        if not ps:
            continue
        clef = (vinfo.get(name) or {}).get("clef")
        say.append(f"{name}: {len(ns_)} notes from {note_name(min(ps))} to {note_name(max(ps))}" +
                   (f" ({clef} clef)" if clef else "") + ".")
    return say


def sheet_explain(abc=None, musicxml=None, score=None):
    """See the tool description."""
    score, _ = load_score(abc, musicxml, score)
    voices, isum = _inline(score)
    return {"explanation": explain_score(score, voices, isum), "summary": _score_summary(score, voices, isum),
            "naming": "C4 = middle C"}


# ============================================================================= registration
def _tool(fn):
    def run(**kw):
        return _plain_top(fn(**kw))
    run.__wrapped__ = fn; run.__name__ = fn.__name__; run.__doc__ = fn.__doc__
    return run


def _plain_top(r):
    if isinstance(r, dict):
        img = r.pop("_image_png", None)
        r = _plain(r)
        if img:
            r["_image_png"] = img
        return r
    return _plain(r)


def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    STR, INT, NUM, BOOL = ns["_STR"], ns["_INT"], ns["_NUM"], ns["_BOOL"]
    T = ns["TOOLS"]
    T["sheet_pages"] = (
        _tool(sheet_pages), "Sheet music pictures, sharp, one system at a time: PNG/JPEG pages, a folder of them or a "
        "PDF of scanned pages. Finds staves, systems (staves joined by a bar line) and bar lines, and returns ONE "
        "zoomed image per call (long systems cut into strips, left part on top) with red bar numbers above the "
        "detected bar lines (estimates), counts per system and the next call - so you read every system, none "
        "skipped. rows: a system number, a list, '2-4', 'all' (sharpness permitting) or 'page' (overview). zoom 2 = "
        "16 px per staff space. Frame tools (coordinates in a page frame `frame` px wide, default 1000): "
        "systems=true lists every system's box, staff lines and bar-line x (text, no image); rows=S + bars='3-5' "
        "crops those bars of system S; crop=[x0, y0, x1, y1] crops any region (upscaled, sharp); save=<.png> also "
        "writes the image. split=N plans N parallel readers (page ranges balanced by bars, a copy-paste brief each; "
        "parts= the part names they must use). Engraved (vector) PDFs, JBIG2/CCITT/JPEG 2000 scans: says how to get "
        "PNG/MusicXML (pages rendered to PNG work).",
        S(source=P(["string", "array"], "image/PDF path, folder of page images, or a list of image paths",
                   items={"type": "string"}),
          page=INT("page (default 1)", True),
          rows=P(["integer", "string", "array"], "system(s) to show: 2, [2, 3], '2-4', 'all', 'page'", True,
                 items={"type": "integer"}),
          zoom=NUM("default 2 (= 16 px per staff space)", True), max_width=INT("image width cap (default 1400)", True),
          marks=BOOL("red bar-line ticks and numbers (default true)", True),
          crop=P("array", "[x0, y0, x1, y1] in the page frame", True, items={"type": "number"}),
          bars=P(["integer", "string", "array"], "with rows=S: bars of that system, 3, '3-5' or [3, 5]", True,
                 items={"type": "integer"}),
          frame=NUM("width of the coordinate frame in px (default 1000)", True),
          systems=BOOL("list systems/staves/bar lines in frame coordinates (no image)", True),
          save=STR("also write the image to this .png (split: folder for the readers' .json)", True),
          split=INT("plan this many parallel readers (page ranges + briefs)", True),
          parts=P("array", "split: part names every reader uses", True, items={"type": "string"}),
          first_bar=INT("split: number of the first bar on page 1 (default 1)", True)))
    T["sheet_import"] = (
        _tool(sheet_import), "Sheet music into FL: give the reading as abc (ABC text or .abc), musicxml (.musicxml/"
        ".xml/.mxl or the XML text) or score (per-bar score data, flh-score/1: {meter, key, tempo, bars: {\"9\": "
        "{chord, mel: [[beat, length, 'Bb4'], ...], parts: {name: [...]}}}} - beats 0-based in quarter notes, length "
        "0 = grace note, 'tie' as a 4th item; docs/sheet_music.md). Score data lands at its own bar numbers "
        "(place_at_bar default = its first bar); channel={part: index} maps parts (unmapped parts are not read); "
        "parts= reads only those. Parses and validates exactly (bar lengths, ties, tuplets, ranges, voices): "
        "issues of severity med+ (strict: any) block the write and come back as a re-read list per bar (with page/"
        "system when `pages` [{page, system, first_bar}] is given); force=true writes anyway. Writes output_mid "
        "and/or one named pattern per voice into project (channel: index, list, {voice: index} or 'new'; ONE "
        "flp_edit), tempo/meter when the project is empty (set_tempo), and records the source in the song journal. "
        "reference_audio (WAV/FLAC of the piece) aligns the score to it and flags bars that sound different (a "
        "hint, not proof). dry_run=true: check only. Read pages with sheet_pages first.",
        S(abc=STR("ABC text or a .abc file", True), musicxml=STR(".musicxml/.xml/.mxl path or MusicXML text", True),
          project=P("string", "write into this .flp", True),
          channel=P(["integer", "string", "array", "object"], "index | [per voice] | {voice: index} | 'new'", True,
                    items={"type": "integer"}),
          output_mid=STR("write a .mid (inside FLH_ROOTS)", True), title=STR("pattern name base", True),
          strict=BOOL("block on low-severity issues too", True),
          reference_audio=STR("a recording of the piece (WAV/FLAC) to check against", True),
          dry_run=BOOL("check only, write nothing", True), force=BOOL("write despite blocking issues", True),
          pages=P("array", "[{page, system, first_bar}] so re-read hints name the system", True,
                  items={"type": "object"}),
          place_at_bar=NUM("playlist bar for the new patterns (default 1, score data: its first bar; 0 = don't "
                           "place)", True),
          set_tempo=BOOL("set the project's tempo/meter from the score (default: only if it has no notes)", True),
          clone_from=INT("channel='new' copies this channel (default the first)", True),
          full=BOOL("all issues / inline notes (score data: + the generated ABC)", True),
          score=P(["object", "string"], "per-bar score data: object, JSON text or a .json file", True),
          parts=P(["array", "string"], "score data: only these parts", True, items={"type": "string"})))
    T["sheet_diff"] = (
        _tool(sheet_diff), "Compare two readings of the same music (ABC/MusicXML text or files, or per-bar score data: "
        "bar by bar and part by part, e.g. two readers' versions of the same page) - or a reading with a project part "
        "(a .flp + pattern [+ channel]): the bars that differ, note by note. For re-checks after re-reading a system.",
        S(a=P(["string", "object"], "reading (ABC/MusicXML/score data) or .flp"),
          b=P(["string", "object"], "reading (ABC/MusicXML/score data) or .flp"),
          pattern=P(["integer", "string"], "project pattern (name or id)", True), channel=INT("project channel", True),
          voice=STR("compare only this voice / part of the reading", True), full=BOOL("all differing bars", True)))
    T["sheet_merge"] = (
        _tool(sheet_merge), "Join per-bar score-data readings (one per page range from parallel readers, or one per "
        "instrument group) into one: bars only one reading has are taken, parts only one reading has are joined, a "
        "part two readings both have must agree (else a conflict with the differing notes; prefer=<0-based index> "
        "settles it). Reports overlaps, missing bars, header (meter/key/tempo) and part-name mismatches, then checks "
        "the merged score like sheet_import (blocking issues + re-read list). output=<.json> writes it (one bar per "
        "line) unless conflicts are unresolved.",
        S(readings=P(["array", "string"], "list of .json paths / JSON texts / objects, or a folder of .json files",
                     items={}),
          output=STR("write the merged score data here (.json, inside FLH_ROOTS)", True),
          prefer=P(["integer", "string"], "reading to trust on conflicts: index, 'first' or 'last'", True),
          check=BOOL("validate the merged score (default true)", True),
          full=BOOL("all conflicts / re-read bars; show the merged data", True)))
    T["sheet_explain"] = (
        _tool(sheet_explain), "A piece of sheet music in plain words, for learners and anyone who can't read notation: "
        "key, meter and what it means, tempo, pickup, repeats and endings, D.C./D.S./Coda, range of each part.",
        S(abc=STR("ABC text or a .abc file", True), musicxml=STR(".musicxml/.xml/.mxl or MusicXML text", True),
          score=P(["object", "string"], "per-bar score data (object, JSON or .json)", True)))
    if "TOOL_GROUPS" in ns:
        for t in ("sheet_pages", "sheet_import", "sheet_diff", "sheet_explain", "sheet_merge"):
            ns["TOOL_GROUPS"][t] = "sheet"
    # the essential listing of sheet_import names the score-data input too (tables live in the server)
    ep = ns.get("ESSENTIAL_PARAMS")
    if isinstance(ep, dict) and isinstance(ep.get("sheet_import"), list) and "score" not in ep["sheet_import"]:
        ep["sheet_import"] = ep["sheet_import"] + ["score"]
    sp = ns.get("SHORT_PARAMS")
    if isinstance(sp, dict) and isinstance(sp.get("sheet_import"), dict):
        sp["sheet_import"].setdefault("score", "per-bar JSON reading (docs/sheet_music.md)")
    sd = ns.get("SHORT_DESC")
    if isinstance(sd, dict) and "sheet_import" in sd:
        sd["sheet_import"] = ("Sheet music -> FL: read pages with sheet_pages, write ABC/MusicXML or per-bar score "
                              "data (score=); every bar is checked (re-read list), then MIDI / one pattern per voice.")
    ns["sheet_compare_audio"] = compare_audio
