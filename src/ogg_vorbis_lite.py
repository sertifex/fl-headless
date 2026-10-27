"""ogg_vorbis_lite: a small Ogg Vorbis I decoder in Python + numpy (no other libraries, no ffmpeg).

Made for reading FL Studio's compressed factory samples - RIFF/WAVE files whose fmt tag is 0x674F ('Og') and whose
data chunk holds an Ogg Vorbis stream - and plain .ogg files, so their pitch, level, tone and length can be measured.
Floor 1 + residue types 0/1/2 + MDCT (what every libvorbis encoder writes). Floor 0 (never written by libvorbis since
2000) is reported as unsupported.

    x, rate = decode(path_or_bytes, max_seconds=None)    # x: float32 (frames, channels) in -1..1
    info(path_or_bytes) -> {"rate", "channels", "frames" (from the last granule, or None)}
    is_ogg_wav(path) -> True for FL's 'Og' WAV files
"""
import math
import struct

import numpy as np

__all__ = ["decode", "info", "is_ogg_wav", "ogg_stream", "VorbisError"]


class VorbisError(ValueError):
    pass


class _EOP(Exception):
    """End of packet while reading (the spec treats it as 'stop decoding this part')."""


# --------------------------------------------------------------------------------------------- containers
def ogg_stream(data):
    """The Ogg stream bytes inside data: a plain Ogg file, or the data chunk of an FL 'Og' WAV."""
    if data[:4] == b"OggS":
        return data
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        i = 12
        while i + 8 <= len(data):
            cid = data[i:i + 4]; n = struct.unpack("<I", data[i + 4:i + 8])[0]
            if cid == b"data":
                s = data[i + 8:i + 8 + n]
                k = s.find(b"OggS")
                if k >= 0:
                    return s[k:]
                break
            i += 8 + n + (n & 1)
        k = data.find(b"OggS")
        if k >= 0:
            return data[k:]
    raise VorbisError("no Ogg stream found (not an Ogg file or an FL 'Og' WAV)")


def is_ogg_wav(path):
    """True for an FL Studio compressed WAV (fmt tag 0x674F) or any WAV whose data starts with an Ogg stream."""
    try:
        with open(path, "rb") as f:
            head = f.read(256)
    except OSError:
        return False
    if head[:4] == b"OggS":
        return True
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return False
    i = 12
    while i + 8 <= len(head):
        cid = head[i:i + 4]; n = struct.unpack("<I", head[i + 4:i + 8])[0]
        if cid == b"fmt " and i + 10 <= len(head):
            if struct.unpack("<H", head[i + 8:i + 10])[0] in (0x674F, 0x6750, 0x6751, 0x676F, 0x6770, 0x6771):
                return True
        if cid == b"data":
            return head[i + 8:i + 12] == b"OggS"
        i += 8 + n + (n & 1)
    return False


def _packets(ogg):
    """Yield (packet bytes, granule of the page it ends on or -1). Only the first logical stream is read."""
    pos, serial, buf = 0, None, []
    n = len(ogg)
    while pos + 27 <= n:
        if ogg[pos:pos + 4] != b"OggS":
            k = ogg.find(b"OggS", pos + 1)
            if k < 0:
                break
            pos = k; continue
        granule = struct.unpack("<q", ogg[pos + 6:pos + 14])[0]
        ser = struct.unpack("<I", ogg[pos + 14:pos + 18])[0]
        nseg = ogg[pos + 26]
        lacing = ogg[pos + 27:pos + 27 + nseg]
        body = pos + 27 + nseg
        if serial is None:
            serial = ser
        if ser != serial:
            pos = body + sum(lacing); continue
        p = body
        done = []
        for lv in lacing:
            buf.append(ogg[p:p + lv]); p += lv
            if lv < 255:
                done.append(b"".join(buf)); buf = []
        for j, pk in enumerate(done):
            yield pk, (granule if j == len(done) - 1 else -1)
        pos = p


# --------------------------------------------------------------------------------------------- bit reader
class _Bits:
    __slots__ = ("b", "pos", "end")

    def __init__(self, data):
        self.b = bytes(data) + b"\0" * 9
        self.pos = 0
        self.end = len(data) * 8

    def read(self, n):
        if n == 0:
            return 0
        p = self.pos
        if p + n > self.end:
            self.pos = self.end
            raise _EOP()
        if n <= 56:
            w = int.from_bytes(self.b[p >> 3:(p >> 3) + 8], "little") >> (p & 7)
            self.pos = p + n
            return w & ((1 << n) - 1)
        lo = self.read(32)
        return lo | (self.read(n - 32) << 32)

    def peek(self, n):
        p = self.pos
        return (int.from_bytes(self.b[p >> 3:(p >> 3) + 8], "little") >> (p & 7)) & ((1 << n) - 1)


def _ilog(x):
    return x.bit_length() if x > 0 else 0


def _float32_unpack(x):
    mant = x & 0x1FFFFF
    exp = (x & 0x7FE00000) >> 21
    if x & 0x80000000:
        mant = -mant
    return math.ldexp(mant, exp - 788)


def _lookup1_values(entries, dims):
    r = int(math.floor(entries ** (1.0 / dims)))
    while (r + 1) ** dims <= entries:
        r += 1
    while r > 0 and r ** dims > entries:
        r -= 1
    return r


# --------------------------------------------------------------------------------------------- codebooks
_FAST = 10                                                     # bits of the fast lookup table


class _Codebook:
    __slots__ = ("dims", "entries", "lengths", "fast", "slow", "vq", "maxlen", "single")

    def __init__(self, br):
        if br.read(24) != 0x564342:
            raise VorbisError("bad codebook sync")
        self.dims = br.read(16)
        self.entries = entries = br.read(24)
        lengths = [0] * entries
        if br.read(1):                                         # ordered
            cur = 0; length = br.read(5) + 1
            while cur < entries:
                num = br.read(_ilog(entries - cur))
                for i in range(cur, min(entries, cur + num)):
                    lengths[i] = length
                cur += num; length += 1
        else:
            sparse = br.read(1)
            for i in range(entries):
                if sparse:
                    if br.read(1):
                        lengths[i] = br.read(5) + 1
                else:
                    lengths[i] = br.read(5) + 1
        self.lengths = lengths
        lt = br.read(4)
        self.vq = None
        if lt in (1, 2):
            mn = _float32_unpack(br.read(32)); dl = _float32_unpack(br.read(32))
            vbits = br.read(4) + 1; seq = br.read(1)
            nvals = _lookup1_values(entries, self.dims) if lt == 1 else entries * self.dims
            mult = np.array([br.read(vbits) for _ in range(nvals)], dtype=np.float64)
            vq = np.zeros((entries, self.dims), dtype=np.float64)
            if lt == 1 and nvals:
                idx = np.arange(entries)
                div = 1
                last = np.zeros(entries)
                for d in range(self.dims):
                    off = (idx // div) % nvals
                    val = mult[off] * dl + mn + last
                    vq[:, d] = val
                    if seq:
                        last = val
                    div *= nvals
            elif lt == 2:
                m = mult.reshape(entries, self.dims) * dl + mn
                vq = np.cumsum(m, axis=1) if seq else m
            self.vq = vq
        elif lt != 0:
            raise VorbisError(f"codebook lookup type {lt} unsupported")
        self._build()

    def _build(self):
        lengths = self.lengths
        used = [i for i, l in enumerate(lengths) if l]
        self.maxlen = max((lengths[i] for i in used), default=0)
        self.single = used[0] if len(used) == 1 else None
        marker = [0] * 33
        codes = {}
        for i in used:
            ln = lengths[i]
            entry = marker[ln]
            if ln < 32 and (entry >> ln):
                if self.single is not None:
                    break
                raise VorbisError("over-specified codebook")
            codes[i] = (entry, ln)
            for j in range(ln, 0, -1):
                if marker[j] & 1:
                    marker[j] = marker[j - 1] << 1 if j > 1 else marker[j] + 1
                    break
                marker[j] += 1
            for j in range(ln + 1, 33):
                if (marker[j] >> 1) == entry:
                    entry = marker[j]; marker[j] = marker[j - 1] << 1
                else:
                    break
        fast = [None] * (1 << _FAST)
        slow = {}
        for i, (c, ln) in codes.items():
            r = int(format(c, f"0{ln}b")[::-1], 2) if ln else 0      # first bit read = codeword MSB
            if ln <= _FAST:
                for s in range(1 << (_FAST - ln)):
                    fast[r | (s << ln)] = (i, ln)
            else:
                slow[(r, ln)] = i
        self.fast = fast
        self.slow = slow

    def scalar(self, br):
        if self.single is not None:                            # one used entry: its codeword is 1 bit (libvorbis)
            br.read(1)
            return self.single
        if br.pos >= br.end:
            raise _EOP()
        hit = self.fast[br.peek(_FAST)]
        if hit is not None and br.pos + hit[1] <= br.end:
            br.pos += hit[1]
            return hit[0]
        code = 0
        for ln in range(1, self.maxlen + 1):                    # slow path: long codewords (or the packet tail)
            code |= br.read(1) << (ln - 1)
            if ln > _FAST:
                e = self.slow.get((code, ln))
                if e is not None:
                    return e
            elif ln == self.maxlen:
                break
        if self.maxlen <= _FAST:
            raise _EOP()
        raise VorbisError("undecodable codeword")

    def vector(self, br):
        return self.vq[self.scalar(br)]

    def many(self, br, cnt):
        """cnt codewords -> list of entries (fewer if the packet ends: the spec's end-of-packet rule)."""
        out = []
        if self.single is not None:
            for _ in range(cnt):
                if br.pos >= br.end:
                    break
                br.pos += 1; out.append(self.single)
            return out
        fast, b, end = self.fast, br.b, br.end
        mask = (1 << _FAST) - 1
        pos = br.pos
        for _ in range(cnt):
            if pos >= end:
                break
            hit = fast[(int.from_bytes(b[pos >> 3:(pos >> 3) + 3], "little") >> (pos & 7)) & mask]
            if hit is not None and pos + hit[1] <= end:
                pos += hit[1]; out.append(hit[0])
                continue
            br.pos = pos
            try:
                out.append(self.scalar(br))
            except _EOP:
                pos = br.pos
                break
            pos = br.pos
        br.pos = pos
        return out


# --------------------------------------------------------------------------------------------- floors / residues
class _Floor1:
    def __init__(self, br):
        parts = br.read(5)
        self.pclass = [br.read(4) for _ in range(parts)]
        maxc = max(self.pclass, default=-1)
        self.cdim, self.csub, self.cmaster, self.subbooks = [], [], [], []
        for _ in range(maxc + 1):
            self.cdim.append(br.read(3) + 1)
            sub = br.read(2); self.csub.append(sub)
            self.cmaster.append(br.read(8) if sub else -1)
            self.subbooks.append([br.read(8) - 1 for _ in range(1 << sub)])
        self.mult = br.read(2) + 1
        rb = br.read(4)
        xs = [0, 1 << rb]
        for c in self.pclass:
            for _ in range(self.cdim[c]):
                xs.append(br.read(rb))
        self.xs = xs
        n = len(xs)
        self.low = [0, 0] + [0] * (n - 2)
        self.high = [0, 0] + [0] * (n - 2)
        for i in range(2, n):
            lo = hi = None
            for j in range(i):
                if xs[j] < xs[i] and (lo is None or xs[j] > xs[lo]):
                    lo = j
                if xs[j] > xs[i] and (hi is None or xs[j] < xs[hi]):
                    hi = j
            self.low[i] = lo; self.high[i] = hi
        self.order = sorted(range(n), key=lambda i: xs[i])
        self.range = (256, 128, 86, 64)[self.mult - 1]

    def decode(self, br, books):
        """-> the Y list, or None if the channel is unused in this packet."""
        if not br.read(1):
            return None
        rbits = _ilog(self.range - 1)
        ys = [br.read(rbits), br.read(rbits)]
        for c in self.pclass:
            cdim, cbits = self.cdim[c], self.csub[c]
            csub = (1 << cbits) - 1
            cval = books[self.cmaster[c]].scalar(br) if cbits else 0
            for _ in range(cdim):
                book = self.subbooks[c][cval & csub]
                cval >>= cbits
                ys.append(books[book].scalar(br) if book >= 0 else 0)
        return ys

    def curve(self, ys, n2):
        xs, rng = self.xs, self.range
        n = len(xs)
        fy = [0] * n
        step2 = [False] * n
        fy[0], fy[1] = ys[0], ys[1]
        step2[0] = step2[1] = True
        for i in range(2, n):
            lo, hi = self.low[i], self.high[i]
            pred = _render_point(xs[lo], fy[lo], xs[hi], fy[hi], xs[i])
            val = ys[i]
            highroom = rng - pred; lowroom = pred
            room = (highroom if highroom < lowroom else lowroom) * 2
            if val:
                step2[lo] = step2[hi] = step2[i] = True
                if val >= room:
                    fy[i] = val - lowroom + pred if highroom > lowroom else pred - val + highroom - 1
                else:
                    fy[i] = pred - ((val + 1) >> 1) if val & 1 else pred + (val >> 1)
            else:
                fy[i] = pred
        out = np.empty(n2, dtype=np.int64)
        m = self.mult
        lx, ly = 0, fy[self.order[0]] * m
        hx = 0; hy = ly
        for i in self.order[1:]:
            if step2[i]:
                hy = fy[i] * m; hx = xs[i]
                _render_line(lx, ly, hx, hy, out, n2)
                lx, ly = hx, hy
        if hx < n2:
            _render_line(hx, hy, n2, hy, out, n2)
        np.clip(out, 0, 255, out=out)
        return _INV_DB[out]


def _render_point(x0, y0, x1, y1, x):
    dy = y1 - y0; adx = x1 - x0
    off = (abs(dy) * (x - x0)) // adx
    return y0 - off if dy < 0 else y0 + off


def _render_line(x0, y0, x1, y1, out, n2):
    if x0 >= n2:
        return
    adx = x1 - x0
    if adx <= 0:
        return
    dy = y1 - y0
    base = int(dy / adx)                                       # C division: truncates toward zero
    ady = abs(dy) - abs(base) * adx
    sy = base - 1 if dy < 0 else base + 1
    end = min(x1, n2)
    if ady == 0:
        k = np.arange(end - x0)
        out[x0:end] = y0 + base * k
        return
    y, err = y0, 0
    out[x0] = y
    for x in range(x0 + 1, end):
        err += ady
        if err >= adx:
            err -= adx; y += sy
        else:
            y += base
        out[x] = y


# floor1 inverse dB table: 256 steps, geometric from 1.0649863e-07 to 1.0 (the spec's table to float precision)
_INV_DB = (1.0649863e-07 * (1.0 / 1.0649863e-07) ** (np.arange(256) / 255.0)).astype(np.float64)


class _Residue:
    def __init__(self, br, kind):
        self.kind = kind
        self.begin = br.read(24); self.end = br.read(24)
        self.psize = br.read(24) + 1
        self.nclass = br.read(6) + 1
        self.classbook = br.read(8)
        casc = []
        for _ in range(self.nclass):
            low = br.read(3)
            high = br.read(5) if br.read(1) else 0
            casc.append(high * 8 + low)
        self.books = [[br.read(8) if (c >> j) & 1 else -1 for j in range(8)] for c in casc]

    def decode(self, br, books, vecs, dnd, n2):
        """Add the residue into vecs (list of float arrays of n2), channels with dnd[i] skipped."""
        if self.kind == 2:
            if all(dnd):
                return
            ch = len(vecs)
            big = np.zeros(n2 * ch)
            self._decode(br, books, [big], [False], n2 * ch, 1)
            for c in range(ch):
                vecs[c] += big[c::ch]
            return
        self._decode(br, books, vecs, dnd, n2, 0 if self.kind == 0 else 1)

    def _decode(self, br, books, vecs, dnd, size, fmt):
        begin = min(self.begin, size); end = min(self.end, size)
        ps = self.psize
        nparts = (end - begin) // ps
        if nparts <= 0:
            return
        cb = books[self.classbook]
        cpw = cb.dims
        nclass = self.nclass
        chs = [j for j in range(len(vecs)) if not dnd[j]]
        cls = {j: [0] * (nparts + cpw) for j in chs}
        try:
            for pss in range(8):
                pc = 0
                while pc < nparts:
                    if pss == 0:
                        for j in chs:
                            t = cb.scalar(br)
                            arr = cls[j]
                            for i in range(cpw - 1, -1, -1):
                                arr[i + pc] = t % nclass; t //= nclass
                    for _ in range(cpw):
                        if pc >= nparts:
                            break
                        for j in chs:
                            book = self.books[cls[j][pc]][pss]
                            if book < 0:
                                continue
                            bk = books[book]
                            off = begin + pc * ps
                            v = vecs[j]
                            d = bk.dims
                            if fmt == 0:
                                step = ps // d
                                idx = bk.many(br, step)
                                if idx:
                                    vals = bk.vq[idx]                      # (n, d): v[off + i + j*step] += e[j]
                                    blk = np.zeros((d, step)); blk[:, :len(idx)] = vals.T
                                    v[off:off + d * step] += blk.reshape(-1)
                                if len(idx) < step:
                                    raise _EOP()
                            else:
                                cnt = -(-ps // d)
                                idx = bk.many(br, cnt)
                                if idx:
                                    vals = bk.vq[idx].reshape(-1)[:ps]
                                    v[off:off + len(vals)] += vals
                                if len(idx) < cnt:
                                    raise _EOP()
                        pc += 1
        except _EOP:
            return


# --------------------------------------------------------------------------------------------- MDCT / windows
_TW = {}


def _imdct(X):
    """Vorbis inverse MDCT of n/2 coefficients -> n samples (FFT based)."""
    n2 = len(X); n = n2 * 2
    tw = _TW.get(n)
    if tw is None:
        n0 = 0.5 + n / 4.0
        k = np.arange(n2); i = np.arange(n)
        tw = (np.exp(2j * np.pi * k * n0 / n), np.exp(1j * np.pi * (i + n0) / n))
        _TW[n] = tw
    z = np.zeros(n, dtype=np.complex128)
    z[:n2] = X * tw[0]
    return (tw[1] * np.fft.ifft(z) * n).real


_WIN = {}


def _slope(m):
    w = _WIN.get(m)
    if w is None:
        x = (np.arange(m) + 0.5) / m * (np.pi / 2)
        w = np.sin(np.pi / 2 * np.sin(x) ** 2)
        _WIN[m] = w
    return w


def _window(n, long_, prev, nxt, bs0):
    w = np.zeros(n)
    if long_ and not prev:
        ls, le = n // 4 - bs0 // 4, n // 4 + bs0 // 4
    else:
        ls, le = 0, n // 2
    if long_ and not nxt:
        rs, re = 3 * n // 4 - bs0 // 4, 3 * n // 4 + bs0 // 4
    else:
        rs, re = n // 2, n
    w[ls:le] = _slope(le - ls)
    w[le:rs] = 1.0
    w[rs:re] = _slope(re - rs)[::-1]
    return w


# --------------------------------------------------------------------------------------------- decoder
class _Decoder:
    def __init__(self, ident, setup):
        br = _Bits(ident)
        if br.read(8) != 1 or ident[1:7] != b"vorbis":
            raise VorbisError("not a Vorbis identification header")
        br.pos = 56
        if br.read(32) != 0:
            raise VorbisError("unsupported Vorbis version")
        self.channels = br.read(8)
        self.rate = br.read(32)
        br.read(32); br.read(32); br.read(32)
        b0 = br.read(4); b1 = br.read(4)
        self.bs = (1 << b0, 1 << b1)
        if not self.channels or not self.rate:
            raise VorbisError("bad Vorbis identification header")
        br = _Bits(setup)
        if br.read(8) != 5 or setup[1:7] != b"vorbis":
            raise VorbisError("not a Vorbis setup header")
        br.pos = 56
        self.books = [_Codebook(br) for _ in range(br.read(8) + 1)]
        for _ in range(br.read(6) + 1):
            if br.read(16) != 0:
                raise VorbisError("bad time-domain transform")
        self.floors = []
        for _ in range(br.read(6) + 1):
            t = br.read(16)
            if t != 1:
                raise VorbisError("Vorbis floor type 0 isn't supported (pre-2000 encoders only)")
            self.floors.append(_Floor1(br))
        self.residues = []
        for _ in range(br.read(6) + 1):
            t = br.read(16)
            if t > 2:
                raise VorbisError(f"residue type {t} unsupported")
            self.residues.append(_Residue(br, t))
        self.mappings = []
        cb = _ilog(self.channels - 1)
        for _ in range(br.read(6) + 1):
            if br.read(16) != 0:
                raise VorbisError("bad mapping type")
            subs = br.read(4) + 1 if br.read(1) else 1
            coup = []
            if br.read(1):
                for _ in range(br.read(8) + 1):
                    coup.append((br.read(cb), br.read(cb)))
            if br.read(2) != 0:
                raise VorbisError("bad mapping reserved bits")
            mux = [br.read(4) for _ in range(self.channels)] if subs > 1 else [0] * self.channels
            sub = []
            for _ in range(subs):
                br.read(8); sub.append((br.read(8), br.read(8)))
            self.mappings.append((coup, mux, sub))
        self.modes = []
        for _ in range(br.read(6) + 1):
            flag = br.read(1); br.read(16); br.read(16)
            self.modes.append((flag, br.read(8)))
        self.mode_bits = _ilog(len(self.modes) - 1)

    def packet(self, data):
        """-> (n, list of windowed n-sample channel blocks, long flag) or None for a non-audio/empty packet."""
        br = _Bits(data)
        try:
            if br.read(1) != 0:
                return None
            flag, mi = self.modes[br.read(self.mode_bits)]
            n = self.bs[flag]
            prev = nxt = 1
            if flag:
                prev = br.read(1); nxt = br.read(1)
        except (_EOP, IndexError):
            return None
        n2 = n // 2
        coup, mux, sub = self.mappings[mi]
        ch = self.channels
        ys = [None] * ch
        for c in range(ch):
            fl = self.floors[sub[mux[c]][0]]
            try:
                ys[c] = fl.decode(br, self.books)
            except _EOP:
                ys[c] = None
        nonzero = [y is not None for y in ys]
        for m, a in coup:
            if nonzero[m] or nonzero[a]:
                nonzero[m] = nonzero[a] = True
        vecs = [np.zeros(n2) for _ in range(ch)]
        for si, (_, res) in enumerate(sub):
            chans = [c for c in range(ch) if mux[c] == si]
            sv = [vecs[c] for c in chans]
            self.residues[res].decode(br, self.books, sv, [not nonzero[c] for c in chans], n2)
        for m, a in reversed(coup):
            M, A = vecs[m], vecs[a]
            pm, pa = M > 0, A > 0
            newm = np.where(pm, np.where(pa, M, M + A), np.where(pa, M, M - A))
            newa = np.where(pm, np.where(pa, M - A, M), np.where(pa, M + A, M))
            vecs[m], vecs[a] = newm, newa
        win = _window(n, flag, prev, nxt, self.bs[0])
        out = []
        for c in range(ch):
            if ys[c] is None:
                out.append(np.zeros(n))
                continue
            fl = self.floors[sub[mux[c]][0]]
            spec = vecs[c] * fl.curve(ys[c], n2)
            out.append(_imdct(spec) * win)
        return n, out


def _open(src):
    if isinstance(src, (bytes, bytearray, memoryview)):
        return bytes(src)
    with open(src, "rb") as f:
        return f.read()


def info(src):
    ogg = ogg_stream(_open(src))
    it = _packets(ogg)
    ident, _ = next(it)
    d = _Bits(ident); d.pos = 88
    ch = d.read(8); rate = d.read(32)
    last = -1
    for _, g in _packets(ogg):
        if g >= 0:
            last = g
    return {"rate": rate, "channels": ch, "frames": last if last >= 0 else None}


def decode(src, max_seconds=None):
    """Decode an Ogg Vorbis stream (plain .ogg, or an FL 'Og' WAV) -> (float32 array (frames, channels), rate)."""
    ogg = ogg_stream(_open(src))
    it = _packets(ogg)
    try:
        ident, _ = next(it); _comment, _ = next(it); setup, _ = next(it)
    except StopIteration:
        raise VorbisError("the Ogg stream ends before its Vorbis headers")
    try:
        dec = _Decoder(ident, setup)
    except _EOP:
        raise VorbisError("the Vorbis headers are cut short (truncated file?)")
    ch = dec.channels
    limit = int(max_seconds * dec.rate) if max_seconds else None
    chunks = [[] for _ in range(ch)]
    prev_n = None; prev_tail = None
    produced = 0
    final_granule = -1
    for pk, g in it:
        if g >= 0:
            final_granule = g
        r = dec.packet(pk)
        if r is None:
            continue
        n, blocks = r
        if prev_n is not None:
            # overlap: previous block's right half meets this block's left half at their quarter points
            left = n // 4 + prev_n // 4                           # samples returned: prev centre -> this centre
            for c in range(ch):
                seg = np.zeros(left)
                tail = prev_tail[c]                               # prev block from its centre: prev_n/2 samples
                cur = blocks[c]
                # place: prev centre at 0; this block starts at prev_n/4 - n/4 relative to prev centre
                start = prev_n // 4 - n // 4
                k = min(len(tail), left)
                seg[:k] += tail[:k]
                lo = max(start, 0); hi = min(start + n // 2 + n // 2, left)
                if hi > lo:
                    seg[lo:hi] += cur[lo - start:hi - start]
                chunks[c].append(seg)
            produced += left
        prev_n = n
        prev_tail = [b[n // 2:] for b in blocks]
        if limit and produced >= limit:
            break
    if not produced:
        return np.zeros((0, ch), dtype=np.float32), dec.rate
    x = np.stack([np.concatenate(c) for c in chunks], axis=1)
    if final_granule >= 0 and not limit and final_granule < len(x):
        x = x[:final_granule]
    if limit:
        x = x[:limit]
    if x.shape[1] in WAV_ORDER:                                    # Vorbis' surround order -> WAV's (FL FR FC LFE ...)
        x = x[:, WAV_ORDER[x.shape[1]]]
    return x.astype(np.float32), dec.rate


# Vorbis I channel order for 3-8 channels (spec 4.3.9) mapped to the WAV / WAVE_FORMAT_EXTENSIBLE order
WAV_ORDER = {3: [0, 2, 1], 5: [0, 2, 1, 3, 4], 6: [0, 2, 1, 5, 3, 4], 7: [0, 2, 1, 6, 5, 3, 4],
             8: [0, 2, 1, 7, 5, 6, 3, 4]}
