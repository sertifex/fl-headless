"""Small FLAC encoder (numpy only): fixed predictors 0-4, partitioned Rice residuals, best of independent / left-side /
right-side / mid-side stereo per frame, constant and verbatim subframes. Lossless and readable by every FLAC decoder
(flac, ffmpeg, foobar, DAWs); compresses music to roughly half a WAV.

Everything is vectorized: a whole chunk of frames is analysed at once (cost of every predictor order, partition
order and Rice parameter from partition sums), then written as one array of bit fields (value, width, length) that
is packed into 64-bit words with add.reduceat; CRC-16 runs over all frames of a chunk in parallel (16-bit table and a
log2 tree). Chunks run in threads (numpy releases the GIL), so a 4-minute stereo song encodes in well under a second.
Any sample rate up to 655 kHz, 1-8 channels, 16 or 24 bits.
"""
import hashlib
import os
import struct
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

BLOCK = 4096
MAX_PORDER = 4                                   # Rice partitions down to 256 samples
CHUNK = 96                                       # frames per vectorized chunk
EXACT_STEREO = False                             # True: full analysis of L, R, side and mid for every frame
_CRC8 = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = ((_c << 1) ^ 0x07) & 0xFF if _c & 0x80 else (_c << 1) & 0xFF
    _CRC8.append(_c)
_CRC16 = []
for _i in range(256):
    _c = _i << 8
    for _ in range(8):
        _c = ((_c << 1) ^ 0x8005) & 0xFFFF if _c & 0x8000 else (_c << 1) & 0xFFFF
    _CRC16.append(_c)
_RATE_CODES = {88200: 0b0001, 176400: 0b0010, 192000: 0b0011, 8000: 0b0100, 16000: 0b0101, 22050: 0b0110,
               24000: 0b0111, 32000: 0b1000, 44100: 0b1001, 48000: 0b1010, 96000: 0b1011}


def _crc8(b):
    c = 0
    for x in b:
        c = _CRC8[c ^ x]
    return c


def _crc16(b):
    c = 0; t = _CRC16
    for x in b:
        c = ((c << 8) & 0xFFFF) ^ t[(c >> 8) ^ x]
    return c


_POW = []                                        # _POW[l][c] = register after shifting 2**l zero words through c
_POW_LOCK = threading.Lock()


def _crc16_pow(levels):
    with _POW_LOCK:                              # chunks encode in threads: build the tables once, in order
        if not _POW:
            t = np.array(_CRC16, np.uint16); v = np.arange(65536, dtype=np.uint16)
            c1 = t[v >> 8]                                         # first byte from register 0
            _POW.append(((c1 << 8) ^ t[(c1 >> 8) ^ (v & 0xFF)]).astype(np.uint16))
        while len(_POW) < levels + 1:
            _POW.append(_POW[-1][_POW[-1]])
        return list(_POW)


def _crc16_many(buf, starts, lens):
    """CRC-16 of buf[starts[i]:starts[i]+lens[i]] for every i: each message left-padded with zeros (which leaves a
    CRC that starts from 0 unchanged) into one row, 16-bit words, then a pairwise tree using shift tables."""
    lens = [int(v) for v in lens]; starts = [int(v) for v in starts]
    W = (max(lens) + 1) // 2
    rows = np.zeros((len(lens), 2 * W), np.uint8)
    for i, (s0, n) in enumerate(zip(starts, lens)):
        rows[i, 2 * W - n:] = buf[s0:s0 + n]
    w = (rows[:, 0::2].astype(np.uint16) << 8) | rows[:, 1::2]
    lv = max(1, int(W).bit_length())
    P = _crc16_pow(lv)
    c = P[0][w]; l = 0
    while c.shape[1] > 1:
        if c.shape[1] & 1:                                         # a leading all-zero segment keeps lengths even
            c = np.concatenate((np.zeros((len(c), 1), np.uint16), c), 1)
        c = P[l][c[:, 0::2]] ^ c[:, 1::2]; l += 1
    return c[:, 0]


def _utf8_num(n):
    """FLAC frame number, UTF-8 style."""
    if n < 0x80:
        return bytes([n])
    for nbytes, limit in ((2, 0x800), (3, 0x10000), (4, 0x200000), (5, 0x4000000), (6, 0x80000000)):
        if n < limit:
            tail = [0x80 | ((n >> (6 * i)) & 0x3F) for i in range(nbytes - 2, -1, -1)]
            lead = ((0xFF << (8 - nbytes)) & 0xFF) | (n >> (6 * (nbytes - 1)))
            return bytes([lead] + tail)
    raise ValueError("frame number too large")


# ----------------------------------------------------------------------------- analysis
_KS = np.arange(31, dtype=np.float64)
_P2K = 2.0 ** -_KS


_LN2 = float(np.log(2.0))


def _rice_best(S, c, kmax=30):
    """Best Rice parameter and its estimated bits per partition: bits(k) = c(k+1) + sum(u >> k), with
    sum(u >> k) ~ S/2^k - c(1-2^-k)/2; the continuous optimum is 2^k = ln2 (S/c - 1/2), so floor/ceil are tried."""
    S = S.astype(np.float64); c = np.asarray(c, np.float64)
    kc = np.log2(np.maximum(_LN2 * (S / c - 0.5), 1.0))
    best_b = best_k = None
    for k in (np.clip(np.floor(kc), 0, kmax), np.clip(np.floor(kc) + 1, 0, kmax)):
        p2 = 2.0 ** -k
        bits = c * (k + 1) + np.maximum(0.0, S * p2 - c * (1 - p2) * 0.5)
        if best_b is None:
            best_b, best_k = bits, k
        else:
            m = bits < best_b
            best_b = np.where(m, bits, best_b); best_k = np.where(m, k, best_k)
    return best_k.astype(np.int64), best_b


def _analyse(X, bps):
    """Best subframe for every row (frame) of X (F, bn): -> dict of arrays
    cost (bits), kind (0 constant, 1 verbatim, 2 fixed), order, porder, method, ks (F, 2**pmax), pmax."""
    F, bn = X.shape
    bps = np.broadcast_to(np.asarray(bps, np.float64), (F,))
    pmax = 0
    while pmax < MAX_PORDER and bn % (1 << (pmax + 1)) == 0 and (bn >> (pmax + 1)) >= 16:
        pmax += 1
    NP = 1 << pmax; ps = bn >> pmax
    best = {"cost": np.full(F, 8.0 + bn * bps), "kind": np.ones(F, np.int64), "order": np.zeros(F, np.int64),
            "porder": np.zeros(F, np.int64), "method": np.zeros(F, np.int64), "ks": np.zeros((F, NP), np.int64),
            "pmax": pmax}
    const = (X == X[:, :1]).all(1)
    E = X.astype(np.int32, copy=False)                             # <= 25-bit samples: order-4 residuals fit
    for o in range(0, min(4, bn - 1) + 1):
        if o:
            E = E[:, 1:] - E[:, :-1]
        A = np.abs(E)                                              # zigzag sum ~ 2 sum|e| - (number < 0)
        S = np.empty((F, NP), np.int64)
        S[:, 0] = A[:, :ps - o].sum(1, dtype=np.int64)
        if NP > 1:
            S[:, 1:] = A[:, ps - o:].reshape(F, NP - 1, ps).sum(2, dtype=np.int64)
        S = 2 * S - (np.arange(NP) == 0) * (ps - o) // 2 - (np.arange(NP) != 0) * ps // 2
        np.maximum(S, 0, out=S)
        cand = None
        for p in range(pmax, -1, -1):
            n_p = 1 << p
            Sp = S.reshape(F, n_p, NP // n_p).sum(2)
            cnt = np.full(n_p, bn >> p, np.int64); cnt[0] -= o
            ks, bits = _rice_best(Sp, cnt[None, :])
            meth = (ks > 14).any(1)
            cost = bits.sum(1) + np.where(meth, 5, 4) * n_p + 8 + o * bps + 6
            ksf = np.repeat(ks, NP // n_p, axis=1)
            if cand is None:
                cand = [cost, np.full(F, p), meth.astype(np.int64), ksf]
            else:
                b = cost < cand[0]
                cand[0] = np.where(b, cost, cand[0]); cand[1] = np.where(b, p, cand[1])
                cand[2] = np.where(b, meth, cand[2]); cand[3] = np.where(b[:, None], ksf, cand[3])
        b = cand[0] < best["cost"]
        best["cost"] = np.where(b, cand[0], best["cost"]); best["kind"] = np.where(b, 2, best["kind"])
        best["order"] = np.where(b, o, best["order"]); best["porder"] = np.where(b, cand[1], best["porder"])
        best["method"] = np.where(b, cand[2], best["method"]); best["ks"] = np.where(b[:, None], cand[3], best["ks"])
    best["cost"] = np.where(const, 8.0 + bps, best["cost"]); best["kind"] = np.where(const, 0, best["kind"])
    return best


# ----------------------------------------------------------------------------- bit fields
def _pack_into(words, vals, ends):
    """OR fields into big-endian 32-bit words: vals (uint32) right-aligned so their last bit is at ends-1. A value's
    leading zeros are free, so no widths are needed; fields never overlap, so per-word sums equal ORs."""
    if not len(vals):
        return
    last = ends - 1
    wi = last >> 5
    r = (last & 31).astype(np.uint32)
    nw = len(words)                                                # sums of disjoint bits < 2**32: exact in float64
    lo = np.bincount(wi, weights=(vals << (np.uint32(31) - r)).astype(np.float64), minlength=nw)
    hv = (vals >> r) >> np.uint32(1)                               # the part that spills into the previous word
    hi = np.bincount(wi, weights=hv.astype(np.float64), minlength=nw)
    words += lo[:nw].astype(np.uint32)
    words[:-1] += hi[1:nw].astype(np.uint32)


def _encode_chunk(sig, bps, frame0, rate_code, size_code):
    """Frames for blocks sig (F, bn, ch) int64 -> (bytes, list of frame sizes)."""
    F, bn, ch = sig.shape
    sig = sig.astype(np.int32, copy=False)
    if ch == 2:
        L, R = sig[:, :, 0], sig[:, :, 1]
        cands = [(L, bps), (R, bps), (L - R, bps + 1), ((L + R) >> 1, bps)]
        if EXACT_STEREO:                                           # full analysis of all four signals
            an = [_analyse(c_, b_) for c_, b_ in cands]
            est = [a["cost"] for a in an]
        else:                                                      # estimate from order-2 residuals (as libFLAC)
            an = None
            est = [bn * np.log2(1.0 + np.abs(np.diff(c_, 2, axis=1)).mean(1, dtype=np.float64)) if bn > 2
                   else np.zeros(F) for c_, _ in cands]
        modes = np.stack([est[0] + est[1], est[0] + est[2], est[2] + est[1], est[3] + est[2]])
        mode = modes.argmin(0)                                     # 0 indep, 1 left/side, 2 side/right, 3 mid/side
        ca = np.array([1, 8, 9, 10])[mode]
        slot_sig = [np.array([0, 0, 2, 3])[mode], np.array([1, 2, 1, 2])[mode]]
    else:
        cands = [(sig[:, :, c], bps) for c in range(ch)]
        an = None
        ca = np.full(F, ch - 1)
        slot_sig = [np.full(F, c) for c in range(ch)]
    V = np.zeros((F, ch, bn), np.uint32); NB = np.zeros((F, ch, bn), np.uint32)
    one = np.uint32(1)
    info = []
    for s in range(ch):
        pick = slot_sig[s]
        if an is None and len(np.unique(pick)) == 1:
            X = cands[int(pick[0])][0]; B = np.full(F, cands[int(pick[0])][1], np.int64)
        else:
            X = np.empty((F, bn), np.int32); B = np.empty(F, np.int64)
            for c in np.unique(pick).tolist():
                m = pick == c
                X[m] = cands[c][0][m]; B[m] = cands[c][1]
        if an is None:
            fi = _analyse(X, B)
        else:
            fi = {k: np.empty((F,) + an[0][k].shape[1:], np.int64) for k in ("kind", "order", "porder", "method", "ks")}
            for c in np.unique(pick).tolist():
                m = pick == c
                for k in fi:
                    fi[k][m] = an[c][k][m]
            fi["pmax"] = an[0]["pmax"]
        pmax = fi["pmax"]; NP = 1 << pmax; ps = bn >> pmax
        info.append((fi, B))
        kind, order = fi["kind"], fi["order"]
        fx = np.flatnonzero(kind == 2)
        if len(fx):                                                # fixed predictor: Rice codes, warm-up raw
            Xf = X[fx]; of = order[fx]; nf = len(fx)
            E = np.zeros((nf, bn), np.int32); D = Xf
            for o in range(0, 5):
                if o:
                    D = D[:, 1:] - D[:, :-1]
                m = of == o
                if m.any():
                    E[m, o:] = D[m]
            U = ((E << 1) ^ (E >> 31)).view(np.uint32).reshape(nf, NP, ps)
            K = fi["ks"][fx].astype(np.uint32)[:, :, None]
            v = ((U & ((one << K) - one)) | (one << K)).reshape(nf, bn)
            nb = ((U >> K) + (K + one)).reshape(nf, bn)
            Bf = B[fx]
            for jj in range(4):                                    # warm-up samples: raw, bps bits
                m = of > jj
                if m.any():
                    v[m, jj] = (Xf[m, jj].astype(np.int64) & ((np.int64(1) << Bf[m]) - 1)).astype(np.uint32)
                    nb[m, jj] = Bf[m]
            V[fx, s] = v; NB[fx, s] = nb
        for f in np.flatnonzero(kind != 2).tolist():               # verbatim (all samples) / constant (sample 0)
            b_ = int(B[f]); raw = (X[f].astype(np.int64) & ((1 << b_) - 1)).astype(np.uint32)
            if kind[f] == 1:
                V[f, s] = raw; NB[f, s] = b_
            else:
                V[f, s, 0] = raw[0]; NB[f, s, 0] = b_
    # inserted fields: frame headers, subframe headers, residual method/partition order, Rice parameters
    ins_at, ins_v, ins_w = [], [], []
    bs_code = 0b1100 if bn == BLOCK else (0b0110 if bn <= 256 else 0b0111)
    for f in range(F):
        h = bytearray(b"\xFF\xF8")
        h.append((bs_code << 4) | rate_code); h.append((int(ca[f]) << 4) | (size_code << 1))
        h += _utf8_num(frame0 + f)
        if bs_code == 0b0110:
            h.append(bn - 1)
        elif bs_code == 0b0111:
            h += (bn - 1).to_bytes(2, "big")
        h.append(_crc8(h))
        base = f * ch * bn
        ins_at += [base] * len(h); ins_v += list(h); ins_w += [8] * len(h)
        for s in range(ch):
            fi, B = info[s]
            kd = int(fi["kind"][f]); o = int(fi["order"][f]); sb = base + s * bn
            if kd != 2:
                ins_at.append(sb); ins_v.append(0 if kd == 0 else 0b10); ins_w.append(8)
            else:
                p = int(fi["porder"][f]); meth = int(fi["method"][f]); n_p = 1 << p
                ins_at += [sb, sb + o]; ins_v += [(8 | o) << 1, (meth << 4) | p]; ins_w += [8, 6]
                ks = fi["ks"][f, ::NP // n_p].tolist(); pw = 5 if meth else 4
                ins_at.append(sb + o); ins_v.append(ks[0]); ins_w.append(pw)
                for m in range(1, n_p):
                    ins_at.append(sb + m * (bn >> p)); ins_v.append(ks[m]); ins_w.append(pw)
    N = F * ch * bn
    at = np.asarray(ins_at, np.int64); iw = np.asarray(ins_w, np.int64); iv = np.asarray(ins_v, np.uint32)
    body = NB.reshape(F, -1).sum(1, dtype=np.int64) + np.bincount(at // (ch * bn), weights=iw, minlength=F).astype(np.int64)
    pad = (-body) & 7
    size = (body + pad) // 8 + 2
    # byte padding + CRC-16 slot close each frame: zero bits placed before the next frame's header
    at = np.concatenate((at, (np.arange(F) + 1) * ch * bn))
    iw = np.concatenate((iw, pad + 16)); iv = np.concatenate((iv, np.zeros(F, np.uint32)))
    key = at * 2 + np.concatenate((np.ones(len(ins_at), np.int64), np.zeros(F, np.int64)))
    o_ = np.argsort(key, kind="stable"); at, iw, iv = at[o_], iw[o_], iv[o_]
    # inserted bits that precede base field i become leading zeros of field i (its value stays right-aligned)
    nbits = NB.ravel()
    inner = at < N
    np.add.at(nbits, at[inner], iw[inner].astype(np.uint32))
    ends = np.cumsum(nbits, dtype=np.int64)
    total = int(ends[-1]) + int(iw[~inner].sum())
    # inserted fields' own ends: the end of the base field before their index, plus the group's running widths
    before = np.where(at > 0, ends[np.maximum(at - 1, 0)], 0)
    first = np.concatenate(([True], at[1:] != at[:-1]))
    cs = np.cumsum(iw); gstart = np.maximum.accumulate(np.where(first, np.arange(len(at)), 0))
    ins_end = before + cs - (cs[gstart] - iw[gstart])
    words = np.zeros(total // 32 + 2, np.uint32)
    _pack_into(words, V.ravel(), ends)
    _pack_into(words, iv, ins_end)
    buf = words.astype(">u4").view(np.uint8)[:total // 8]
    fstart = np.concatenate(([0], np.cumsum(size)[:-1]))
    crc = _crc16_many(buf, fstart, size - 2)
    buf[fstart + size - 2] = (crc >> 8).astype(np.uint8); buf[fstart + size - 1] = (crc & 0xFF).astype(np.uint8)
    return buf.tobytes(), size.tolist()


def _threads(threads):
    if threads:
        return max(1, int(threads))
    return max(1, min(8, os.cpu_count() or 1))


def _encode_parts(samples, rate, bps, threads=None):
    x = np.asarray(samples)
    if x.ndim == 1:
        x = x[:, None]
    n, ch = x.shape
    bps = int(bps); rate = int(rate)
    if not 1 <= ch <= 8:
        raise ValueError("1 to 8 channels")
    if bps not in (16, 24):
        raise ValueError("bps must be 16 or 24")
    if not 1 <= rate < (1 << 20):
        raise ValueError("bad sample rate")
    if n == 0:
        raise ValueError("no samples")
    lim = 1 << (bps - 1)
    if int(x.max()) >= lim or int(x.min()) < -lim:
        raise ValueError(f"samples out of {bps}-bit range")
    x = np.ascontiguousarray(x, dtype="<i4")

    def md5():                                                     # MD5 of the interleaved little-endian PCM,
        h = hashlib.md5()                                          # chunk by chunk (no full-size byte copy)
        for a in range(0, n, 1 << 18):
            c = x[a:a + (1 << 18)]
            raw = c.astype("<i2") if bps == 16 else c.view(np.uint8).reshape(-1, 4)[:, :3]
            h.update(np.ascontiguousarray(raw))
        return h.digest()
    rate_code = _RATE_CODES.get(rate, 0)                           # 0: "see STREAMINFO" (any other rate)
    size_code = {16: 0b100, 24: 0b110}[bps]
    nfull = n // BLOCK
    jobs = [(s, min(nfull, s + CHUNK)) for s in range(0, nfull, CHUNK)]
    if n % BLOCK:
        jobs.append((nfull, None))

    def run(job):
        a, b = job
        if b is None:
            return _encode_chunk(x[a * BLOCK:][None], bps, a, rate_code, size_code)
        return _encode_chunk(x[a * BLOCK:b * BLOCK].reshape(b - a, BLOCK, ch), bps, a, rate_code, size_code)
    T = _threads(threads)
    if T > 1 and len(jobs) > 1:
        with ThreadPoolExecutor(min(T, len(jobs) + 1)) as ex:
            h = ex.submit(md5)
            parts = list(ex.map(run, jobs))
            digest = h.result()
    else:
        digest = md5(); parts = [run(j) for j in jobs]
    sizes = [s for _, ss in parts for s in ss]
    si = struct.pack(">HH", BLOCK, BLOCK) + min(sizes).to_bytes(3, "big") + max(sizes).to_bytes(3, "big")
    v = (rate << 44) | ((ch - 1) << 41) | ((bps - 1) << 36) | n
    si += v.to_bytes(8, "big") + digest
    head = b"fLaC" + bytes([0x80]) + len(si).to_bytes(3, "big") + si   # last metadata block: STREAMINFO
    return [head] + [p for p, _ in parts], len(sizes)


def encode_bytes(samples, rate, bps, threads=None):
    """samples: int array (n, channels) at `bps` bits (16 or 24) -> (FLAC file bytes, frames)."""
    parts, frames = _encode_parts(samples, rate, bps, threads)
    return b"".join(parts), frames


def encode(path, samples, rate, bps, threads=None):
    """samples: int array (n, channels) at `bps` bits (16 or 24) -> FLAC file at path (written whole, then renamed)."""
    parts, frames = _encode_parts(samples, rate, bps, threads)
    tmp = path + ".part"
    try:
        with open(tmp, "wb") as f:
            for p in parts:
                f.write(p)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return {"frames": frames, "bytes": sum(len(p) for p in parts)}
