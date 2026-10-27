"""Offline WAV editing for fl-headless: trim/cut/fade/normalize/pitch/stretch/denoise/join/slice.

Pure numpy + stdlib. Audio is float64 arrays shaped (samples, channels) in -1..1.
"""
import os
import re
import struct

import numpy as np

OPS = ["trim", "cut", "silence", "fade_in", "fade_out", "gain", "normalize", "reverse",
       "repeat", "pitch", "stretch", "mono", "stereo", "width", "denoise", "dehum"]

_PARAMS = {
    "trim": {"start", "end"}, "cut": {"start", "end", "crossfade"}, "silence": {"at", "length"},
    "fade_in": {"length", "curve"}, "fade_out": {"length", "curve"}, "gain": {"db"},
    "normalize": {"peak_db", "lufs"}, "reverse": {"start", "end"}, "repeat": {"times", "crossfade"},
    "pitch": {"semitones", "keep_length"}, "stretch": {"ratio", "to_length", "from_bpm", "to_bpm"},
    "mono": set(), "stereo": set(), "width": {"amount"},
    "denoise": {"amount_db", "profile_start", "profile_end"},
    "dehum": {"freq", "harmonics", "amount_db", "width_hz"},
}


# ----------------------------------------------------------------------------- WAV I/O

def wav_header(path):
    """-> dict(tag, ch, rate, bits, align, offset, frames) of a RIFF/WAVE file, reading only the headers."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        h = f.read(12)
        if len(h) < 12 or h[:4] != b"RIFF" or h[8:12] != b"WAVE":
            raise ValueError(f"not a RIFF/WAVE file: {path}")
        fmt = None; pos = 12
        while pos + 8 <= size:
            f.seek(pos); hd = f.read(8)
            if len(hd) < 8:
                break
            cid, ln = hd[:4], struct.unpack("<I", hd[4:8])[0]
            if cid == b"fmt ":
                fmt = f.read(min(ln, 64))
            elif cid == b"data":
                if fmt is None:
                    break
                avail = size - (pos + 8)
                ln = avail if ln in (0, 0xFFFFFFFF) or ln > avail else ln    # streaming / truncated: what's there
                tag, ch, rate, _, align, bits = struct.unpack("<HHIIHH", fmt[:16])
                if tag == 0xFFFE and len(fmt) >= 26:
                    tag = struct.unpack("<H", fmt[24:26])[0]
                if ch < 1 or align < 1 or rate < 1:
                    raise ValueError(f"bad WAV header in {path}")
                return {"tag": tag, "ch": ch, "rate": rate, "bits": bits, "align": align, "offset": pos + 8,
                        "frames": ln // align}
            pos += 8 + ln + (ln & 1)
    raise ValueError(f"WAV missing fmt or data chunk: {path}")


def _decode(raw, tag, width):
    if tag == 1 and width == 1:
        return (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    if tag == 1 and width == 2:
        return np.frombuffer(raw, "<i2").astype(np.float32) * np.float32(1 / 32768)
    if tag == 1 and width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        w = np.zeros((len(b), 4), np.uint8); w[:, 1:] = b
        return (w.view("<i4")[:, 0] >> 8).astype(np.float64) * (1 / 8388608.0)
    if tag == 1 and width == 4:
        return np.frombuffer(raw, "<i4") * (1 / 2147483648.0)
    if tag == 3 and width == 4:
        return np.frombuffer(raw, "<f4")
    if tag == 3 and width == 8:
        return np.frombuffer(raw, "<f8")
    return None


def _ext80(b):
    """IEEE 754 80-bit extended (AIFF's sample rate) -> float."""
    exp = ((b[0] & 0x7F) << 8) | b[1]
    mant = int.from_bytes(b[2:10], "big")
    if exp == 0 and mant == 0:
        return 0.0
    v = mant * 2.0 ** (exp - 16383 - 63)
    return -v if b[0] & 0x80 else v


def aiff_load(path):
    """AIFF / AIFF-C (uncompressed: NONE, sowt, fl32, fl64) -> (float64 (frames, ch), rate, bits)."""
    d = open(path, "rb").read()
    if d[:4] != b"FORM" or d[8:12] not in (b"AIFF", b"AIFC"):
        raise ValueError(f"{path}: not an AIFF file")
    aifc = d[8:12] == b"AIFC"
    i, comm, ssnd = 12, None, None
    while i + 8 <= len(d):
        cid, n = d[i:i + 4], struct.unpack(">I", d[i + 4:i + 8])[0]
        if cid == b"COMM":
            comm = d[i + 8:i + 8 + n]
        elif cid == b"SSND":
            off = struct.unpack(">I", d[i + 8:i + 12])[0]
            ssnd = memoryview(d)[i + 16 + off:i + 8 + n]
        i += 8 + n + (n & 1)
    if comm is None or ssnd is None:
        raise ValueError(f"{path}: AIFF without COMM or SSND chunk")
    ch, frames, bits = struct.unpack(">hIh", comm[:8])
    rate = int(round(_ext80(comm[8:18])))
    comp = comm[18:22] if aifc and len(comm) >= 22 else b"NONE"
    width = (bits + 7) // 8
    raw = bytes(ssnd[:frames * ch * width])
    if comp in (b"fl32", b"FL32"):
        x = np.frombuffer(raw, ">f4").astype(np.float64); bits = 32
    elif comp in (b"fl64", b"FL64"):
        x = np.frombuffer(raw[:len(raw) // 8 * 8], ">f8").astype(np.float64); bits = 64
    elif comp in (b"NONE", b"sowt", b"twos"):
        little = comp == b"sowt"
        a = np.frombuffer(raw[:len(raw) // width * width], np.uint8).reshape(-1, width)
        if not little:
            a = a[:, ::-1]                                        # big-endian -> little
        v = np.zeros(len(a), np.int64)
        for k in range(width):
            v |= a[:, k].astype(np.int64) << (8 * k)
        sign = 1 << (8 * width - 1)
        v = (v ^ sign) - sign                                     # two's complement
        x = v / float(sign)
    else:
        raise ValueError(f"{path}: compressed AIFF-C ({comp.decode('latin-1')}) isn't supported: convert it to WAV")
    x = x[:len(x) // ch * ch].reshape(-1, ch)
    return x, rate, bits


def _media_load(path, dtype=np.float64):
    """MP3 / M4A / WMA ... with Windows Media Foundation -> (dtype array, rate, 16) or None when not such a file."""
    try:
        import mf_audio
    except ImportError:
        return None
    if not mf_audio.is_media(path):
        return None
    x, rate = mf_audio.decode(path)
    return x.astype(dtype, copy=False), int(rate), 16                 # float32 stays float32 (no 2x copy)


def load_ex(path, dtype=np.float64, nonfinite="raise", block_frames=1 << 18):
    """Read a WAV block by block into one preallocated array -> (x (n, ch) dtype, rate, bits, non-finite count).
    PCM 8/16/24/32, float 32/64, EXTENSIBLE. Memory: just the output (no whole-file bytes or int32 copies).
    nonfinite: "raise" (NaN/inf samples -> a clear error) or "zero" (set to 0, counted)."""
    try:
        import ogg_vorbis_lite as OV
        ogg = OV.is_ogg_wav(path)
    except ImportError:
        ogg = False
    if ogg:                                                   # FL's compressed factory WAVs ('Og') and .ogg files
        y, rate = OV.decode(path)
        return y.astype(dtype), int(rate), 16, 0
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:4] == b"fLaC":                                   # FLAC (finals, stems, FL's export): numpy decoder
        import flh_review
        y, rate, bits = flh_review.flac_load(path, dtype)
        return y.astype(dtype, copy=False), int(rate), int(bits), 0
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):  # AIFF samples (Mac-made packs)
        y, rate, bits = aiff_load(path)
        return y.astype(dtype), int(rate), int(bits), 0
    if head[:4] != b"RIFF":                                   # MP3 / M4A ... (Windows' own decoders)
        got = _media_load(path, dtype)
        if got is not None:
            return got[0], got[1], got[2], 0
    hd = wav_header(path)
    tag, ch, align = hd["tag"], hd["ch"], hd["align"]
    width = align // ch
    if _decode(b"\0" * align, tag, width) is None:
        raise ValueError(f"unsupported WAV format tag {tag:#x} / {hd['bits']} bit in {path}")
    n = hd["frames"]
    x = np.empty((n, ch), dtype=dtype)
    bad = 0
    with open(path, "rb") as f:
        f.seek(hd["offset"])
        for a in range(0, n, block_frames):
            k = min(block_frames, n - a)
            raw = f.read(k * align)
            k = len(raw) // align
            if not k:
                x = x[:a]; break
            v = _decode(raw[:k * align], tag, width).reshape(k, ch)
            if tag == 3:
                fin = np.isfinite(v)
                if not fin.all():
                    nb = int(v.size - np.count_nonzero(fin)); bad += nb
                    if nonfinite == "raise":
                        raise ValueError(f"{os.path.basename(str(path))} has NaN/inf samples (from {a / hd['rate']:.2f} "
                                         f"s; a plugin glitch?): nothing can be measured. Fix it with audio_edit "
                                         f"(any op turns them into silence) or re-render")
                    v = np.where(fin, v, 0.0)
            x[a:a + k] = v
    return x, int(hd["rate"]), int(hd["bits"]), bad


def load(path, nonfinite="raise"):
    """Read a WAV -> (x float64 (n, ch), rate, bits). PCM 8/16/24/32, float 32/64, EXTENSIBLE (block-wise read)."""
    x, rate, bits, _ = load_ex(path, np.float64, nonfinite)
    return x, rate, bits


def save(path, x, rate, bits=24, chunk=1 << 18, clip=False):
    """Write WAV: bits 16/24 PCM (clipped) or 32 float. Written chunk by chunk to a .tmp file, then os.replace."""
    x = np.asarray(x)
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    n, ch = x.shape
    if bits in (16, 24):
        tag, width = 1, bits // 8
        fmt = struct.pack("<HHIIHH", tag, ch, int(rate), int(rate) * ch * width, ch * width, bits)
        extra = b""
    elif bits == 32:
        tag, width = 3, 4
        fmt = struct.pack("<HHIIHHH", tag, ch, int(rate), int(rate) * ch * 4, ch * 4, 32, 0)
        extra = b"fact" + struct.pack("<II", 4, n)
    else:
        raise ValueError("bits must be 16, 24 or 32 (float)")
    data_len = n * ch * width
    head = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra + b"data" + struct.pack("<I", data_len)
    riff = len(head) + data_len + (data_len & 1)
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", riff) + head)
            for a in range(0, n, chunk):
                c = np.asarray(x[a:a + chunk], dtype=np.float64)
                if not np.isfinite(c).all():                      # NaN / inf: silence, never a full-scale click
                    c = np.nan_to_num(c, nan=0.0, posinf=0.0, neginf=0.0)
                if clip:
                    c = np.clip(c, -1.0, 1.0)
                if bits == 32:
                    f.write(c.astype("<f4").tobytes())
                    continue
                scale = float(2 ** (bits - 1))
                v = np.clip(np.round(c * scale), -scale, scale - 1).astype(np.int32)
                if bits == 16:
                    f.write(v.astype("<i2").tobytes())
                else:
                    f.write(v.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes())
            if data_len & 1:
                f.write(b"\0")
        os.replace(tmp, path)
    except BaseException:                                         # a full disk / a bad sample: no half .tmp left
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def _peak(x, chunk=1 << 20):
    """max |x| without a full-size temporary."""
    pk = 0.0
    for a in range(0, len(x), chunk):
        c = x[a:a + chunk]
        if c.size:
            pk = max(pk, float(np.max(c)), -float(np.min(c)))
    return pk


def _2d(x):
    x = np.asarray(x, dtype=np.float64)
    return x.reshape(-1, 1) if x.ndim == 1 else x


# ----------------------------------------------------------------------------- time values

_TIME_RE = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)\s*([a-z]*)\s*$", re.I)
_UNITS = {"": 1, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "ms": 1e-3,
          "b": "beat", "beat": "beat", "beats": "beat", "bar": "bar", "bars": "bar"}


def to_seconds(value, bpm=None):
    """Number = seconds; strings '1.5s', '250ms', '3b'/'3 beats', '2bar'/'2 bars' (4/4, needs bpm)."""
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
        sec = float(value)
    elif isinstance(value, str) and _TIME_RE.match(value):
        num, unit = _TIME_RE.match(value).groups()
        unit = unit.lower()
        if unit not in _UNITS:
            raise ValueError(f"bad time value {value!r}: unknown unit {unit!r} (use s, ms, b/beats, bar/bars)")
        u = _UNITS[unit]
        if isinstance(u, str):
            if not bpm or float(bpm) <= 0:
                raise ValueError(f"time value {value!r} is in {u}s and needs a bpm")
            u = (60.0 / float(bpm)) * (4 if u == "bar" else 1)
        sec = float(num) * u
    else:
        raise ValueError(f"bad time value {value!r}: use seconds (number) or '1.5s', '250ms', '3b', '2bar'")
    if not np.isfinite(sec) or sec < 0:
        raise ValueError(f"time value {value!r} must be a finite non-negative duration")
    return sec


def to_samples(value, rate, bpm=None):
    """Time value -> integer sample count at `rate`."""
    return int(round(to_seconds(value, bpm) * rate))


# ----------------------------------------------------------------------------- DSP helpers

def _hann(n):
    return 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)  # periodic


def _xfade(a, b):
    """Equal-power crossfade: a fades out, b fades in (same shape)."""
    t = (np.arange(len(a)) + 0.5) / max(len(a), 1)
    return a * np.cos(t * np.pi / 2)[:, None] + b * np.sin(t * np.pi / 2)[:, None]


def _concat(a, b, cf):
    cf = min(int(cf), len(a), len(b))
    if cf <= 0:
        return np.concatenate([a, b])
    return np.concatenate([a[:-cf], _xfade(a[-cf:], b[:cf]), b[cf:]])


def _curve(n, kind):
    t = (np.arange(n) + 0.5) / max(n, 1)
    if kind == "linear":
        return t
    if kind == "exp":
        return (np.exp(4 * t) - 1) / (np.exp(4) - 1)
    if kind == "s":
        return 0.5 - 0.5 * np.cos(np.pi * t)
    raise ValueError("curve must be 'linear', 'exp' or 's'")


def _match_channels(x, ch):
    if x.shape[1] == ch:
        return x
    return x[:, np.arange(ch) % x.shape[1]]


_PHASES = 2048  # polyphase kernel table resolution (linearly interpolated)


def resample_to(x, n_out, step=None, quality=16):
    """Windowed-sinc (Blackman) resampling of x to n_out samples, reading x at k*step."""
    x = _2d(x)
    n, ch = x.shape
    if n_out <= 0 or n == 0:
        return np.zeros((max(n_out, 0), ch))
    step = n / n_out if step is None else step
    fc = min(1.0, 1.0 / step)
    half = min(int(np.ceil(quality / fc)), 512)
    pos = np.arange(n_out) * step
    base = np.floor(pos).astype(np.int64)
    fi = (pos - base) * _PHASES
    k = fi.astype(np.int64)
    a = fi - k
    xp = np.concatenate([np.zeros((half + 1, ch)), x, np.zeros((half + 2, ch))])
    xs = [xp[:, c] for c in range(ch)]
    taps = np.arange(-half + 1, half + 1)
    d = np.arange(_PHASES + 1)[:, None] / _PHASES - taps[None, :]  # kernel table: phase x tap
    u = d / half
    table = fc * np.sinc(fc * d) * (0.42 + 0.5 * np.cos(np.pi * u) + 0.08 * np.cos(2 * np.pi * u))
    out = np.zeros((n_out, ch))
    T = len(taps); B = max(256, (1 << 21) // max(T, 1))             # blocks: (B, taps) weights + gathers, one einsum
    off = taps + half + 1
    for b0 in range(0, n_out, B):
        b1 = min(n_out, b0 + B)
        kk = k[b0:b1]; aa = a[b0:b1, None]
        W = table[kk] * (1 - aa) + table[kk + 1] * aa
        idx = base[b0:b1, None] + off[None, :]
        for c in range(ch):
            out[b0:b1, c] = np.einsum("ij,ij->i", xs[c][idx], W)
    return out


def resample(x, rate_in, rate_out):
    """Change sample rate (duration preserved)."""
    if rate_in == rate_out:
        return _2d(x)
    x = _2d(x)
    return resample_to(x, int(round(len(x) * rate_out / rate_in)), rate_in / rate_out)


def _peaks(mag):
    p = np.flatnonzero((mag[1:-1] > mag[:-2]) & (mag[1:-1] >= mag[2:])) + 1
    return p if len(p) else np.array([int(np.argmax(mag))])


def _pv_1d(x, ratio, N=4096, Hs=1024):
    """Phase-vocoder time stretch with identity phase locking (Laroche & Dolson)."""
    n = len(x)
    n_out = int(round(n * ratio))
    win = _hann(N)
    ha_f = Hs / ratio
    M = int(np.ceil((n_out + N / 2) / Hs)) + 1
    ta = np.round(np.arange(M) * ha_f).astype(np.int64)
    pad = N // 2
    xp = np.zeros(pad + max(n, int(ta[-1])) + N)
    xp[pad:pad + n] = x
    out = np.zeros(M * Hs + N)
    wsum = np.zeros(M * Hs + N)
    bins = N // 2 + 1
    omega = 2 * np.pi * np.arange(bins) / N
    idx = np.arange(bins)
    prev_phi = ps = None
    R = N // Hs if N % Hs == 0 else 0
    w2 = win * win
    for m0 in range(0, M, 256):                                  # batches: FFTs and overlap-add vectorised, only
        m1 = min(M, m0 + 256)                                    # the phase propagation runs frame by frame
        X = np.fft.rfft(xp[ta[m0:m1, None] + np.arange(N)] * win, axis=1)
        MAG, PHI = np.abs(X), np.angle(X)
        PS = np.empty_like(PHI)
        for i, m in enumerate(range(m0, m1)):
            mag, phi = MAG[i], PHI[i]
            if m == 0:
                ps = phi.copy()
            else:
                ha = max(int(ta[m] - ta[m - 1]), 1)
                pk = _peaks(mag)
                dphi = phi[pk] - prev_phi[pk] - omega[pk] * ha
                dphi -= 2 * np.pi * np.round(dphi / (2 * np.pi))
                ps_pk = ps[pk] + (omega[pk] + dphi / ha) * Hs
                owner = np.searchsorted((pk[:-1] + pk[1:]) / 2.0, idx)
                ps = ps_pk[owner] + (phi - phi[pk][owner])
                ps = np.mod(ps + np.pi, 2 * np.pi) - np.pi
            prev_phi = phi
            PS[i] = ps
        Y = np.fft.irfft(MAG * np.exp(1j * PS), N, axis=1) * win
        K = m1 - m0
        if R:
            Yr = Y.reshape(K, R, Hs)
            for j in range(R):
                out[(m0 + j) * Hs:(m0 + j + K) * Hs] += Yr[:, j, :].reshape(-1)
                wsum[(m0 + j) * Hs:(m0 + j + K) * Hs] += np.tile(w2[j * Hs:(j + 1) * Hs], K)
        else:
            for i in range(K):
                s_ = (m0 + i) * Hs
                out[s_:s_ + N] += Y[i]; wsum[s_:s_ + N] += w2
    out /= np.maximum(wsum, 1e-3 * wsum.max())
    res = out[pad:pad + n_out]
    return np.pad(res, (0, n_out - len(res))) if len(res) < n_out else res


def time_stretch(x, ratio):
    """Change duration by `ratio` without changing pitch; exact length round(n*ratio)."""
    x = _2d(x)
    if not 0.05 <= ratio <= 20:
        raise ValueError("stretch ratio must be between 0.05 and 20")
    if abs(ratio - 1) < 1e-9:
        return x.copy()
    return np.stack([_pv_1d(x[:, c], ratio) for c in range(x.shape[1])], axis=1)


def pitch_shift(x, semitones, keep_length=True):
    """Pitch shift. keep_length=False = tape-style resample (speed changes too)."""
    x = _2d(x)
    r = 2.0 ** (float(semitones) / 12.0)
    n = len(x)
    if keep_length:
        return resample_to(time_stretch(x, r), n)
    return resample_to(x, max(int(round(n / r)), 1))


# ----------------------------------------------------------------------------- loudness

def _biquad_response(b, a, nfft):
    z = np.exp(-1j * np.pi * np.arange(nfft // 2 + 1) / (nfft // 2))
    return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)


def _k_coeffs(rate):
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = np.tan(np.pi * f0 / rate)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = np.tan(np.pi * f0 / rate)
    a0 = 1 + K / Q + K * K
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    return b1, a1, b2, a2


def _k_weight_blocks(x, rate):
    """BS.1770 K-weighting (shelf + RLB high-pass) via FFT of the exact biquad response, overlap-save in blocks with
    1 s of history (the filters' memory dies out long before): yields (start, y block float64). No full-length FFT."""
    b1, a1, b2, a2 = _k_coeffs(rate)
    n = len(x); hist = int(rate)
    nfft = 1 << int(np.ceil(np.log2(max(8 * hist, 1 << 18))))
    H = _biquad_response(b1, a1, nfft) * _biquad_response(b2, a2, nfft)
    core = nfft - hist
    for a in range(0, n, core):
        b = min(n, a + core); lo = max(0, a - hist)
        seg = np.zeros((nfft, x.shape[1]))
        seg[hist - (a - lo):hist + (b - a)] = x[lo:b]
        y = np.fft.irfft(np.fft.rfft(seg, nfft, axis=0) * H[:, None], nfft, axis=0)
        yield a, y[hist:hist + (b - a)]


def _k_weight(x, rate):
    """K-weighted copy of x (block-wise filtering; the output is the only full-length array)."""
    x = _2d(x); out = np.empty_like(x)
    for a, y in _k_weight_blocks(x, rate):
        out[a:a + len(y)] = y
    return out


def integrated_loudness(x, rate):
    """ITU-R BS.1770-4 integrated loudness in LUFS (-inf for silence). Streams: only block energies are kept."""
    x = _2d(x)
    n = len(x)
    blk, step = int(round(0.4 * rate)), int(round(0.1 * rate))
    if n < blk:
        pos = np.array([0, n])
    else:
        st = np.arange(0, n - blk + 1, step)
        pos = np.unique(np.concatenate([st, st + blk]))
    cs_at = np.zeros(len(pos)); acc = 0.0; j = 0
    while j < len(pos) and pos[j] == 0:
        j += 1
    for a, y in _k_weight_blocks(x, rate):
        e = np.einsum("ij,ij->i", y, y)
        c = np.cumsum(e) + acc
        b = a + len(y)
        k = j
        while k < len(pos) and pos[k] <= b:
            k += 1
        if k > j:
            cs_at[j:k] = c[pos[j:k] - a - 1]
            j = k
        acc = float(c[-1]) if len(c) else acc
    cs = dict(zip(pos.tolist(), cs_at.tolist()))
    if n < blk:
        zs = np.array([cs[n] / max(n, 1)])
    else:
        zs = np.array([(cs[int(s0) + blk] - cs[int(s0)]) / blk for s0 in st])
    with np.errstate(divide="ignore"):
        lj = -0.691 + 10 * np.log10(zs)
    g = lj > -70
    if not g.any():
        return float("-inf")
    rel = -0.691 + 10 * np.log10(zs[g].mean()) - 10
    g &= lj > rel
    return float(-0.691 + 10 * np.log10(zs[g].mean()))


def _dbfs(v):
    return round(float(20 * np.log10(v)), 2) if v > 0 else None


# ----------------------------------------------------------------------------- denoise

def _box(a, w, axis):
    h = w // 2
    pad = [(0, 0)] * a.ndim
    pad[axis] = (h, h)
    ap = np.pad(a, pad, mode="edge")
    L = a.shape[axis]
    return sum(np.take(ap, np.arange(k, k + L), axis=axis) for k in range(w)) / w


def _medfilt(v, w):
    h = w // 2
    return np.median(np.lib.stride_tricks.sliding_window_view(np.pad(v, h, mode="edge"), w), axis=1)


def _quietest(x, rate, length=0.5):
    m = x.mean(axis=1)
    L = int(round(length * rate))
    if len(m) <= L:
        return 0, len(m)
    cs = np.concatenate([[0.0], np.cumsum(m * m)])
    st = np.arange(0, len(m) - L + 1, max(int(0.05 * rate), 1))
    i = st[int(np.argmin(cs[st + L] - cs[st]))]
    return int(i), int(i + L)


def stft_frames(col, N, H, win, e0, e1):
    """Windowed spectra of frames e0..e1 of col on stft_stream's grid (frame m centred on sample m*H)."""
    n = len(col); idx = (np.arange(e0, e1) * H - N // 2)[:, None] + np.arange(N)
    ok = (idx >= 0) & (idx < n)
    return np.fft.rfft(np.where(ok, np.asarray(col)[np.clip(idx, 0, max(n - 1, 0))], 0.0) * win, axis=1)


def stft_stream(col, N, H, win_a, win_s, process, out, block=512, ctx=0, M=None):
    """Streaming STFT -> process -> overlap-add of one channel, without padded / accumulator copies of the whole
    signal: frame m covers col[m*H - N//2 : m*H + N//2] (zeros outside). process(X_ctx, m0, m1, e0) gets the spectra
    of frames e0..e1 (= m0..m1 plus up to ctx frames of context each side) and returns the processed spectra of
    frames m0..m1. Output = sum(irfft * win_s) / max(sum(win_s * win_a), 1e-3 * its max), written into out (n,)."""
    n = len(col)
    M = int(np.ceil((n + N / 2) / H)) + 1 if M is None else int(M)
    off = np.arange(N); half = N // 2
    ww = win_a * win_s
    R = N // H if N % H == 0 else 0
    if R:                                                         # steady-state overlap of the windows
        wmax = float(np.max(ww.reshape(R, H).sum(0)))
    else:
        wmax = float(ww.max())

    def frames(e0, e1):
        idx = (np.arange(e0, e1) * H - half)[:, None] + off
        ok = (idx >= 0) & (idx < n)
        fr = np.where(ok, np.asarray(col)[np.clip(idx, 0, max(n - 1, 0))], 0.0)
        return fr * win_a
    carry = np.zeros(N); carry_w = np.zeros(N)
    for m0 in range(0, M, block):
        m1 = min(M, m0 + block); K = m1 - m0
        e0, e1 = max(0, m0 - ctx), min(M, m1 + ctx)
        Y = process(np.fft.rfft(frames(e0, e1), axis=1), m0, m1, e0)
        fr = np.fft.irfft(Y, N, axis=1) * win_s
        span = np.zeros(K * H + N); wsp = np.zeros(K * H + N)
        span[:N] += carry; wsp[:N] += carry_w
        if R:
            fr_ = fr.reshape(K, R, H)
            for j in range(R):
                span[j * H:j * H + K * H] += fr_[:, j, :].reshape(-1)
                wsp[j * H:j * H + K * H] += np.tile(ww[j * H:(j + 1) * H], K)
        else:
            for k in range(K):
                span[k * H:k * H + N] += fr[k]; wsp[k * H:k * H + N] += ww
        done = K * H if m1 < M else K * H + N                     # the last batch flushes everything
        seg = span[:done] / np.maximum(wsp[:done], 1e-3 * wmax)
        p0 = m0 * H - half                                        # output index of span[0]
        a, b = max(0, p0), min(n, p0 + done)
        if b > a:
            out[a:b] = seg[a - p0:b - p0]
        carry = np.zeros(N); carry_w = np.zeros(N)
        rest = span[K * H:]; carry[:len(rest)] = rest; carry_w[:len(rest)] = wsp[K * H:]
    return out


def denoise(x, rate, amount_db=12.0, profile=None, N=2048, H=512, block=1024):
    """Spectral gating. profile=(start, end) samples of noise-only audio, else quietest 0.5 s. float32 input stays
    float32 (streamed STFT: no full-length float64 copies)."""
    x = np.asarray(x)
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    if x.dtype not in (np.float32, np.float64):
        x = x.astype(np.float64)
    n, ch = x.shape
    a, b = profile if profile else _quietest(x, rate)
    floor = 10 ** (-abs(float(amount_db)) / 20)
    win = _hann(N)
    M = int(np.ceil((n + N / 2) / H)) + 1
    starts = np.arange(M) * H  # frame m is centred on input sample m*H
    off = np.arange(N)
    out = np.zeros((n, ch), dtype=x.dtype)
    centers = starts
    pm = (centers >= a + N // 2) & (centers <= b - N // 2)
    if not pm.any():
        pm = (centers >= a) & (centers < b)
    if not pm.any():
        pm[min(int(a // H), M - 1)] = True
    pidx = np.flatnonzero(pm)
    for c in range(ch):
        col = x[:, c]
        half = N // 2

        def spec_frames(i0, i1):
            idx = (starts[i0:i1, None] - half) + off
            ok = (idx >= 0) & (idx < n)
            X = np.fft.rfft(np.where(ok, col[np.clip(idx, 0, n - 1)], 0.0) * win, axis=1)
            return X

        def smooth_p(X):
            P = X.real ** 2 + X.imag ** 2
            return _box(_box(P, 3, 0), 5, 1)

        Pn = smooth_p(spec_frames(max(pidx[0] - 1, 0), min(pidx[-1] + 2, M)))
        noise = _medfilt(Pn.mean(axis=0), 31) + 1e-20

        def process(X, m0, m1, e0):
            Ps = smooth_p(X)
            r_db = 10 * np.log10(Ps / noise + 1e-30)
            mask = _box(_box(np.clip((r_db - 3.0) / 6.0, 0, 1), 3, 0), 3, 1)
            g = floor + (1 - floor) * mask
            return (X * g)[m0 - e0:m0 - e0 + (m1 - m0)]
        stft_stream(col, N, H, win, win, process, out[:, c], block=block, ctx=2)
    return out


def _welch(m, rate, N=16384):
    w = _hann(N); acc = None; k = 0
    for s in range(0, max(1, len(m) - N), N // 2):
        seg = m[s:s + N]
        if len(seg) < N:
            seg = np.pad(seg, (0, N - len(seg)))
        P = np.abs(np.fft.rfft(seg * w)) ** 2
        acc = P if acc is None else acc + P; k += 1
    return np.fft.rfftfreq(N, 1 / rate), acc / max(k, 1)


def find_hum(x, rate, harmonics=6):
    """Mains hum: (fundamental Hz or None, how far its harmonics stand above their neighbourhood in dB)."""
    m = _2d(x).mean(axis=1)
    N = 65536 if len(m) >= 65536 * 2 else 1 << max(12, int(np.log2(max(len(m) // 2, 4096))))
    f, P = _welch(m, rate, N)
    db = 10 * np.log10(P + 1e-30); df = f[1] - f[0]; best = (None, 0.0)
    for nominal in (50.0, 60.0):
        lo, hi = int((nominal - 1.0) / df), int(np.ceil((nominal + 1.0) / df)) + 1
        i = lo + int(np.argmax(db[lo:hi]))                       # mains drifts a little
        a, b, c = db[i - 1], db[i], db[i + 1]
        den = a - 2 * b + c
        f0 = f[i] + (float(np.clip(0.5 * (a - c) / den, -0.5, 0.5)) * df if den < 0 else 0.0)   # parabolic peak
        stand = []; top = db.max()
        for k in range(1, harmonics + 1):
            c = int(round(k * f0 / df))
            if c + int(12 / df) >= len(db):
                break
            peak = db[max(c - 2, 0):c + 3].max()
            if peak < top - 60:                                  # far below the music: not audible hum
                continue
            ring = np.r_[db[max(c - int(10 / df), 0):max(c - int(3 / df), 1)], db[c + int(3 / df):c + int(10 / df)]]
            stand.append(peak - np.median(ring))
        score = float(np.mean(sorted(stand, reverse=True)[:3])) if stand else 0.0
        if score > best[1]:
            best = (float(f0), score)
    if best[0] is not None and not _steady(m, rate, best[0]):
        return (None, best[1])                                  # comes and goes: it's music, not mains hum
    return best


def _steady(m, rate, f0, seg_s=2.0, need=0.8):
    """Hum is constant: it must stand out in nearly every 2-second stretch, not just on average."""
    L = int(seg_s * rate)
    if len(m) < 2 * L:
        return True
    N = 1 << int(np.log2(L)); w = _hann(N); fr = np.fft.rfftfreq(N, 1 / rate); df = fr[1] - fr[0]; hits = total = 0
    for s in range(0, len(m) - N, N):
        db = 20 * np.log10(np.abs(np.fft.rfft(m[s:s + N] * w)) + 1e-12)
        if db.max() < -100:
            continue
        total += 1; ok = False
        for k in (1, 2, 3):
            c = int(round(k * f0 / df))
            ring = np.r_[db[max(c - int(10 / df), 0):max(c - int(3 / df), 1)], db[c + int(3 / df):c + int(10 / df)]]
            if len(ring) and db[max(c - 1, 0):c + 2].max() - np.median(ring) > 8:
                ok = True; break
        hits += ok
    return total == 0 or hits / total >= need


def dehum(x, rate, freq="auto", harmonics=10, amount_db=30.0, width_hz=2.0):
    """Notch out mains hum (50/60 Hz and its harmonics), zero-phase. -> (audio, info dict)."""
    x = _2d(x)
    if freq in (None, "auto"):
        f0, score = find_hum(x, rate)
        if f0 is None or score < 6.0:
            return x, {"hum": None, "note": "no mains hum found; left unchanged"}
    else:
        f0, score = float(freq), None
    n = len(x); pad = int(rate)                                  # 1 s each side so the notch ringing doesn't wrap
    N = 1 << int(np.ceil(np.log2(n + 2 * pad)))
    fr = np.fft.rfftfreq(N, 1 / rate); g = np.ones(len(fr))
    floor = 10 ** (-abs(float(amount_db)) / 20); half = float(width_hz) / 2
    for k in range(1, int(harmonics) + 1):
        fk = k * f0
        if fk > rate / 2 - 50:
            break
        d = np.abs(fr - fk); hk = half + 0.02 * k               # allow for a little error in f0
        near = d < 3 * hk
        shape = np.where(d[near] <= hk, 0.0, 0.5 - 0.5 * np.cos(np.pi * (d[near] - hk) / (2 * hk)))
        g[near] = np.minimum(g[near], floor + (1 - floor) * shape)
    out = np.empty_like(x)
    for c in range(x.shape[1]):
        buf = np.zeros(N); buf[pad:pad + n] = x[:, c]
        out[:, c] = np.fft.irfft(np.fft.rfft(buf) * g, N)[pad:pad + n]
    return out, {"hum": round(f0, 2), "stood_out_db": None if score is None else round(score, 1)}


# ----------------------------------------------------------------------------- ops

def _span(p, n, rate, bpm, start="start", end="end"):
    s = to_samples(p[start], rate, bpm) if p.get(start) is not None else 0
    e = to_samples(p[end], rate, bpm) if p.get(end) is not None else n
    s, e = min(s, n), min(e, n)
    if e <= s:
        raise ValueError(f"empty region: {start}={p.get(start)!r} {end}={p.get(end)!r} (file is {n / rate:.3f}s)")
    return s, e


def _op_trim(x, rate, p, bpm, notes):
    s, e = _span(p, len(x), rate, bpm)
    return x[s:e]


def _op_cut(x, rate, p, bpm, notes):
    n = len(x)
    s, e = _span(p, n, rate, bpm)
    cf = to_samples(p.get("crossfade", "5ms"), rate, bpm)
    h1 = min(cf // 2, s)
    h2 = min(cf - cf // 2, n - e)
    if h1 + h2 == 0:
        return np.concatenate([x[:s], x[e:]])
    mid = _xfade(x[s - h1:s + h2], x[e - h1:e + h2])
    return np.concatenate([x[:s - h1], mid, x[e + h2:]])


def _op_silence(x, rate, p, bpm, notes):
    at = min(to_samples(p.get("at", 0), rate, bpm), len(x))
    ln = to_samples(p["length"], rate, bpm)
    return np.concatenate([x[:at], np.zeros((ln, x.shape[1])), x[at:]])


def _op_fade_in(x, rate, p, bpm, notes):
    ln = min(to_samples(p["length"], rate, bpm), len(x))
    x = x.copy()
    x[:ln] *= _curve(ln, p.get("curve", "s"))[:, None]
    return x


def _op_fade_out(x, rate, p, bpm, notes):
    ln = min(to_samples(p["length"], rate, bpm), len(x))
    x = x.copy()
    if ln:
        x[len(x) - ln:] *= _curve(ln, p.get("curve", "s"))[::-1, None]
    return x


def _op_gain(x, rate, p, bpm, notes):
    return x * 10 ** (float(p["db"]) / 20)


def _op_normalize(x, rate, p, bpm, notes):
    peak = _peak(x)
    if peak == 0:
        notes.append("normalize: silent input, unchanged")
        return x
    if p.get("lufs") is None:
        return x * (10 ** (float(p.get("peak_db", -1.0)) / 20) / peak)
    L = integrated_loudness(x, rate)
    if not np.isfinite(L):
        notes.append("normalize: loudness below gate, unchanged")
        return x
    g = 10 ** ((float(p["lufs"]) - L) / 20)
    lim = 10 ** (-1 / 20)
    if peak * g > lim:
        g2 = lim / peak
        notes.append(f"normalize: peak would hit {20 * np.log10(peak * g):.2f} dBFS; gain limited so peak = "
                     f"-1 dBFS (loudness {L + 20 * np.log10(g2):.2f} LUFS instead of {float(p['lufs']):.2f})")
        g = g2
    x *= g                                                       # in place: x is this edit's own working copy
    return x


def _op_reverse(x, rate, p, bpm, notes):
    s, e = _span(p, len(x), rate, bpm)
    x = x.copy()
    x[s:e] = x[s:e][::-1]
    return x


def _op_repeat(x, rate, p, bpm, notes):
    times = int(p["times"])
    if times < 1:
        raise ValueError("repeat times must be >= 1")
    cf = to_samples(p.get("crossfade", 0), rate, bpm)
    out = x
    for _ in range(times - 1):
        out = _concat(out, x, cf)
    return out


def _op_pitch(x, rate, p, bpm, notes):
    return pitch_shift(x, float(p["semitones"]), bool(p.get("keep_length", True)))


def _op_stretch(x, rate, p, bpm, notes):
    if p.get("ratio") is not None:
        r = float(p["ratio"])
    elif p.get("to_length") is not None:
        r = to_samples(p["to_length"], rate, bpm) / max(len(x), 1)
    elif p.get("from_bpm") and p.get("to_bpm"):
        r = float(p["from_bpm"]) / float(p["to_bpm"])
    else:
        raise ValueError("stretch needs ratio, to_length, or from_bpm + to_bpm")
    return time_stretch(x, r)


def _op_mono(x, rate, p, bpm, notes):
    return x.mean(axis=1, keepdims=True)


def _op_stereo(x, rate, p, bpm, notes):
    return np.repeat(x, 2, axis=1) if x.shape[1] == 1 else x[:, :2]


def _op_width(x, rate, p, bpm, notes):
    if x.shape[1] != 2:
        raise ValueError("width needs a stereo file (use 'stereo' first)")
    amt = float(p["amount"])
    if not 0 <= amt <= 2:
        raise ValueError("width amount must be 0..2 (0 = mono, 1 = unchanged, 2 = extra wide)")
    mid, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2 * amt
    return np.stack([mid + side, mid - side], axis=1)


def _op_dehum(x, rate, p, bpm, notes):
    y, info = dehum(x, rate, freq=p.get("freq", "auto"), harmonics=int(p.get("harmonics", 10)),
                    amount_db=float(p.get("amount_db", 30)), width_hz=float(p.get("width_hz", 2.0)))
    notes.append(info.get("note") or f"removed {info['hum']} Hz hum and harmonics")
    return y


def _op_denoise(x, rate, p, bpm, notes):
    prof = None
    if p.get("profile_start") is not None or p.get("profile_end") is not None:
        prof = _span(p, len(x), rate, bpm, "profile_start", "profile_end")
    return denoise(x, rate, float(p.get("amount_db", 12)), prof)


def apply_op(x, rate, op, bpm=None, notes=None):
    """Apply one op dict to x; returns new x."""
    if not isinstance(op, dict) or "op" not in op:
        raise ValueError(f"each op must be a dict with an 'op' key, got {op!r}")
    name = op["op"]
    if name not in _PARAMS:
        raise ValueError(f"unknown op {name!r}; available: {', '.join(OPS)}")
    extra = set(op) - _PARAMS[name] - {"op"}
    if extra:
        raise ValueError(f"op {name!r}: unknown param(s) {sorted(extra)}; allowed {sorted(_PARAMS[name])}")
    if len(x) == 0 and name not in ("silence",):
        raise ValueError(f"op {name!r}: audio is empty")
    return globals()["_op_" + name](x, rate, op, bpm, notes if notes is not None else [])


def _out_bits(bits, src_bits):
    if bits is None:                                             # float sources stay float: their overs survive
        return src_bits if src_bits in (16, 24) else (32 if src_bits in (32, 64) else 24)
    if bits not in (16, 24, 32):
        raise ValueError("bits must be 16, 24 or 32 (float)")
    return bits


# ----------------------------------------------------------------------------- public API

def _same(a, b):
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def not_in_place(output, inputs):
    """Refuse writing over a source file: the original audio is never replaced in place (no undo, no Recycle Bin)."""
    for i in inputs:
        if _same(output, i):
            raise ValueError(f"output is the input file ({os.path.basename(str(i))}): write to a new file name; "
                             "fl-headless never overwrites source audio in place")


def edit(input, output, ops, bpm=None, bits=None):
    """Load one WAV, apply ops in order, save. Returns a summary dict."""
    not_in_place(output, [input])
    x, rate, src_bits, bad = load_ex(input, nonfinite="zero")
    n0, notes = len(x), []
    if bad:
        notes.append(f"{bad} NaN/inf sample(s) in the source were set to silence")
    for op in ops or []:
        x = apply_op(x, rate, op, bpm, notes)
    ob = _out_bits(bits, src_bits)
    pk = _peak(x)
    if ob != 32 and pk > 1.0:
        notes.append(f"peak {_dbfs(pk):+.2f} dBFS clipped to 0 dBFS in the {ob}-bit file (lower the gain, or bits=32 "
                     "keeps the overs)")
    save(output, x, rate, ob)
    res = {"output": output, "seconds_before": round(n0 / rate, 4), "seconds_after": round(len(x) / rate, 4),
           "rate": rate, "channels": int(x.shape[1]), "ops": [o["op"] for o in ops or []],
           "peak_dbfs": _dbfs(pk)}
    if notes:
        res["notes"] = notes
    return res


def join(inputs, output, crossfade="10ms", gap=0, bpm=None, bits=None):
    """Concatenate WAVs (resampled to the first file's rate, channels = max), crossfade or gap."""
    if not inputs:
        raise ValueError("join needs at least one input")
    not_in_place(output, inputs)
    clips, rate, src_bits = [], None, None
    for p in inputs:
        x, r, b = load(p)
        if rate is None:
            rate, src_bits = r, b
        clips.append(resample(x, r, rate))
    ch = max(c.shape[1] for c in clips)
    clips = [_match_channels(c, ch) for c in clips]
    g = to_samples(gap, rate, bpm)
    cf = 0 if g > 0 else to_samples(crossfade, rate, bpm)
    out = clips[0]
    for c in clips[1:]:
        out = np.concatenate([out, np.zeros((g, ch)), c]) if g > 0 else _concat(out, c, cf)
    save(output, out, rate, _out_bits(bits, src_bits))
    return {"output": output, "inputs": len(inputs), "seconds": round(len(out) / rate, 4), "rate": rate,
            "channels": ch, "peak_dbfs": _dbfs(float(np.max(np.abs(out))) if out.size else 0.0)}


def detect_onsets(mono, rate, threshold=None, min_length=0.06, N=1024, H=256):
    """Spectral-flux onsets -> sorted sample positions (snapped to a preceding quiet point)."""
    n = len(mono)
    delta = 0.1 if threshold is None else float(threshold)
    win = _hann(N)
    M = int(np.ceil(n / H)) + 1
    xp = np.zeros(M * H + N)
    xp[N // 2:N // 2 + n] = mono
    off = np.arange(N)
    flux = np.zeros(M)
    prev = np.zeros(N // 2 + 1)
    for b0 in range(0, M, 2048):
        st = np.arange(b0, min(b0 + 2048, M)) * H
        L = np.log1p(1000 * np.abs(np.fft.rfft(xp[st[:, None] + off] * win, axis=1)))
        D = np.diff(np.vstack([prev, L]), axis=0)
        flux[b0:b0 + len(st)] = np.maximum(D, 0).sum(axis=1)
        prev = L[-1]
    if flux.max() <= 0:
        return []
    flux /= flux.max()
    thr = _medfilt(flux, 2 * int(0.1 * rate / H) + 1) + delta
    fp = np.concatenate([[0.0], flux, [0.0]])
    cand = np.flatnonzero((flux >= fp[:-2]) & (flux > fp[2:]) & (flux > thr))
    min_len = max(int(round(min_length * rate)), 1)
    picked = []
    for t in cand:  # min distance: keep the stronger of close peaks
        if picked and (t - picked[-1]) * H < min_len:
            if flux[t] > flux[picked[-1]]:
                picked[-1] = t
            continue
        picked.append(t)
    env = np.convolve(mono * mono, np.ones(48) / 48, mode="same")
    w5 = int(0.005 * rate)
    w1 = max(int(0.001 * rate), 1)
    out = []
    for t in picked:
        c = t * H
        lo, hi = max(c - N // 2, 0), min(c + N // 2 + H, n - 1)
        if hi - lo < 2:
            continue
        i0 = lo + int(np.argmax(np.diff(env[lo:hi + 1])))
        a = max(i0 - w5, 0)
        seg = env[a:i0 + 1]
        lvl = seg.min() + 0.2 * (env[i0] - seg.min())
        j = a + int(np.flatnonzero(seg <= lvl)[-1])
        z0 = max(j - w1, 1)
        zc = np.flatnonzero(mono[z0 - 1:j] * mono[z0:j + 1] <= 0)
        cut = z0 + int(zc[-1]) if len(zc) else j
        if out and cut - out[-1] < min_len:
            continue
        out.append(cut)
    return out


def slice(input, output_dir, mode="transients", bpm=None, every="1b", count=None, threshold=None,
          min_length="60ms", fade="3ms", prefix=None, bits=None, times=None):
    """Split a WAV into numbered files. mode: 'transients' | 'beats' | 'equal' | 'times' (start seconds in `times`)."""
    x, rate, src_bits = load(input)
    n = len(x)
    min_len = to_samples(min_length, rate, bpm)
    if mode == "beats":
        if not bpm:
            raise ValueError("slice mode 'beats' needs bpm")
        step = to_samples(every, rate, bpm)
        if step <= 0:
            raise ValueError("'every' must be > 0")
        starts = list(range(0, n, step))
    elif mode == "times":
        if not times:
            raise ValueError("slice mode 'times' needs a list of start times")
        starts = sorted({max(0, int(round(float(t) * rate))) for t in times})
        if starts[0] > 0:
            starts = [0] + starts
    elif mode == "equal":
        if not count or int(count) < 1:
            raise ValueError("slice mode 'equal' needs count >= 1")
        starts = sorted({int(round(i * n / int(count))) for i in range(int(count))})
    elif mode == "transients":
        starts = detect_onsets(x.mean(axis=1), rate, threshold, min_len / rate)
        lead_loud = starts and np.max(np.abs(x[:starts[0]]), initial=0) > 10 ** (-50 / 20)
        if not starts or (starts[0] >= min_len and lead_loud):
            starts = [0] + starts
        elif starts[0] < min_len:
            starts[0] = 0
    else:
        raise ValueError("slice mode must be 'transients', 'beats', 'equal' or 'times'")
    bounds = [s for s in starts if s < n]
    ends = bounds[1:] + [n]
    while len(bounds) > 1 and ends[-1] - bounds[-1] < min_len:  # merge a tiny tail
        bounds.pop()
        ends.pop()
        ends[-1] = n
    os.makedirs(output_dir, exist_ok=True)
    stem = prefix or os.path.splitext(os.path.basename(input))[0]
    digits = max(2, len(str(len(bounds))))
    fl = to_samples(fade, rate, bpm)
    ob = _out_bits(bits, src_bits)
    files = []
    for i, (s, e) in enumerate(zip(bounds, ends), 1):
        seg = x[s:e].copy()
        f = min(fl, len(seg) // 4)
        if f > 0:
            ramp = ((np.arange(f) + 0.5) / f)[:, None]
            seg[:f] *= ramp
            seg[-f:] *= ramp[::-1]
        path = os.path.join(output_dir, f"{stem}_{i:0{digits}d}.wav")
        save(path, seg, rate, ob)
        files.append(path)
    return {"files": files, "starts_s": [round(s / rate, 4) for s in bounds], "count": len(files)}
