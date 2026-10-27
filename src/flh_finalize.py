"""fl-headless finals: one call renders, limits, finishes and files a song's FINAL.flac (extension, see flh_store).

fl_finalize   FL closes once (saved), each project renders to WAV (live progress: pct / eta_s from the song length,
              the tempo map and this PC's learned render speed, or the growing WAV when FL writes it as it goes),
              then one finishing pass: a transparent true-peak limiter lets quiet, peaky mixes reach the loudness
              target, the lead silence and dead tail are trimmed, the ending fades, TPDF dither (16-bit), FLAC. The
              file lands at <final_root>/<song>/<final_name> (old final recycled only after the new one is written),
              FL reopens once, the song's journal records the final.
              With no collection profile: next to the project as "<song> - FINAL.flac", -14 LUFS, -1 dBTP, 24-bit.
audio_limit   the limiter alone (WAV in -> WAV out).

Limiter: 4x oversampled true-peak detection (frame FFT fractional delays), linked channels, 5 ms lookahead with a
box-smoothed attack that reaches the needed gain before the peak, program-dependent release (50 ms for transients,
150 ms once reduction is sustained). The loudness solve runs on a K-weighted energy computed once (from the same
FFT frames as the true-peak detector) and calibrated to the server's own meter; the result is verified on the final
integer samples with the server's meter and the true-peak detector (corrected and re-checked if it misses):
loudness within 0.05 LU of the target, true peak <= the ceiling.
"""
import json
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

class _LazyNumpy:
    """numpy loads on first use (keeps server start fast on weak PCs); then the global `np` is numpy itself."""
    def __getattr__(self, name):
        import numpy
        globals()["np"] = numpy
        return getattr(numpy, name)


np = _LazyNumpy()

import flh_common as C
import flh_store

H = {}                                                   # server namespace, set by register()

LOOKAHEAD_S = 0.005
REL_FAST_S, REL_SLOW_S, SLOW_ATT_S = 0.05, 0.15, 0.10
MARGIN_DB = 0.1
LUFS_TOL = 0.05                                          # verified on the integer samples
_BLOCK = 32                                              # control-rate block (samples)
_FR, _FM = 32768, 2048                                   # FFT frame and overlap margin (K-weighting needs ~40 ms)
_GROUP = 8                                               # frames per FFT batch
NEUTRAL = {"lufs": -14.0, "true_peak": -1.0, "bits": 24}
FIRST_RATIO = 0.4                                        # render seconds per song second before any is measured
_FFT = {}                                                # lazy: scipy.fft costs ~0.15 s to import (server startup)
FFT_BACKEND = "lazy"


def _fft():
    """(rfft, irfft, float32 ok) - scipy.fft when present (optional, ~1.5x per FFT), else numpy. Imported on first use."""
    global FFT_BACKEND
    if not _FFT:
        try:
            if os.environ.get("FLH_NO_SCIPY"):
                raise ImportError
            import scipy.fft as _sfft
            _FFT.update(rfft=lambda a: _sfft.rfft(a, axis=1, workers=1),
                        irfft=lambda a, n: _sfft.irfft(a, n, axis=1, workers=1), f32=True)
            FFT_BACKEND = "scipy"
        except ImportError:
            _FFT.update(rfft=lambda a: np.fft.rfft(a, axis=1), irfft=lambda a, n: np.fft.irfft(a, n, axis=1),
                        f32=int(np.__version__.split(".")[0]) >= 2)   # numpy 2 FFTs keep float32 (1.x upcasts)
            FFT_BACKEND = "numpy"
    return _FFT["rfft"], _FFT["irfft"], _FFT["f32"]


def _progress(**k):
    H.get("_job_progress", lambda **kw: None)(**k)


def _threads():
    return max(1, min(8, os.cpu_count() or 1))


def _pmap(fn, items):
    items = list(items)
    if len(items) <= 1 or _threads() == 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(min(_threads(), len(items))) as ex:
        return list(ex.map(fn, items))


class _Bg:
    """Run fn(*a) in a thread; .get() returns its result or raises its exception."""
    def __init__(self, fn, *a):
        self.r = self.e = None
        self.t = threading.Thread(target=self._run, args=(fn, a), daemon=True); self.t.start()

    def _run(self, fn, a):
        try:
            self.r = fn(*a)
        except BaseException as e:                      # noqa: BLE001 - handed to the caller
            self.e = e

    def get(self):
        self.t.join()
        if self.e is not None:
            raise self.e
        return self.r


# ----------------------------------------------------------------------------- analysis (one FFT pass)
def analyse(x, fs, tp=True, k=True, scale=1.0, blocks=False):
    """One overlap-discard FFT pass (threads over frame groups): -> {"env": linked 4x true-peak envelope per sample
    (max |x| over channels at t, t+1/4, t+1/2, t+3/4), "e": K-weighted power per sample summed over up to 2 channels
    (blocks=True: "eB", its sums per _BLOCK-sample control block instead - a 32x smaller array)}.
    x can be any numeric dtype (int samples with scale=1/full): each group of frames is converted on the fly, so
    memory stays at the outputs (no padded / float64 copies of the whole signal)."""
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    n, ch = x.shape
    FR, FM = (_FR, _FM) if k else (16384, 512)                   # true peak alone needs a short margin
    core = FR - 2 * FM; nf = max(1, -(-n // core))
    _rfft, _irfft, _FFT32 = _fft()
    dt = np.float32 if _FFT32 else np.float64
    m = np.arange(FR // 2 + 1)
    resp = [np.exp(2j * np.pi * m * (q / 4.0) / FR).astype(np.complex64 if _FFT32 else np.complex128)
            for q in (1, 2, 3)] if tp else []
    kr = None
    if k:
        kr = np.sqrt(H["_k_weight"](np.fft.rfftfreq(FR, 1.0 / fs), fs)).astype(dt)
    kch = min(2, ch)
    env = np.empty(nf * core, np.float32) if tp else None
    if k and blocks:
        e = None; eB = np.zeros(-(-(nf * core) // _BLOCK))
    else:
        e = np.empty(nf * core, np.float64) if k else None; eB = None
    grp = _GROUP * (_FR // FR)
    sc = dt(scale) if scale != 1.0 else None

    def job(g0):
        cnt = min(grp, nf - g0)
        a, b = g0 * core, (g0 + cnt) * core
        lo, hi = a - FM, b + FM                                  # padded span this group reads
        pk = None; ek = None
        for c in range(ch):
            seg = np.zeros(hi - lo, dt)
            s0, s1 = max(0, lo), min(n, hi)
            if s1 > s0:
                seg[s0 - lo:s1 - lo] = x[s0:s1, c]
                if sc is not None:
                    seg *= sc
            win = np.lib.stride_tricks.sliding_window_view(seg, FR)[::core][:cnt]
            X = _rfft(win)
            if tp:
                p_ = np.abs(win[:, FM:FM + core])
                for R in resp:
                    np.maximum(p_, np.abs(_irfft(X * R, FR)[:, FM:FM + core]), out=p_)
                pk = p_ if pk is None else np.maximum(pk, p_, out=pk)
            if k and c < kch:
                y = _irfft(X * kr, FR)[:, FM:FM + core]
                y2 = np.square(y, dtype=np.float64).ravel()
                ek = y2 if ek is None else ek + y2
        if tp:
            env[a:b] = pk.ravel()
        if k:
            if b > n:                                            # filter ringing past the end doesn't count
                ek[max(0, n - a):] = 0.0
            if blocks:
                eB[a // _BLOCK:b // _BLOCK] = ek.reshape(-1, _BLOCK).sum(1)
            else:
                e[a:b] = ek
    _pmap(job, range(0, nf, grp))
    out = {"env": env[:n] if tp else None, "e": e[:n] if (k and not blocks) else None}
    if k and blocks:
        out["eB"] = eB[:-(-n // _BLOCK)]
    return out


def tp_envelope(x):
    """Per-sample linked true peak (max |x| over channels at t, t+1/4, t+1/2, t+3/4), 4x oversampled."""
    return analyse(x, 48000, tp=True, k=False)["env"]


def true_peak_db(x, chunk=1 << 21, scale=1.0):
    """4x oversampled true peak (dBTP) of x (n,) or (n, ch) (times scale: integer samples with 1/full): THE shared
    implementation (server _true_peak, fl_review, finals). Long audio is measured in overlapping chunks (only each
    chunk's core counts), so memory stays bounded."""
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    n = len(x)
    if not n:
        return -120.0
    M = 2048; best = 0.0
    for a in range(0, n, chunk):
        lo, hi = max(0, a - M), min(n, a + chunk + M)
        env = analyse(x[lo:hi], 48000, tp=True, k=False, scale=scale)["env"]
        best = max(best, float(env[a - lo:a - lo + min(chunk, n - a)].max()))
    if not np.isfinite(best):
        return float("nan")
    return float(20 * np.log10(max(best, 1e-12)))


def _k_energy(x, fs):
    return analyse(x, fs, tp=False, k=True)["e"]


def _gated(z):
    """BS.1770 gating of 400 ms block powers z (same as the server's _lufs)."""
    L = -0.691 + 10 * np.log10(np.maximum(z, 1e-12)); L = L[L > -70]
    if not len(L):
        return None
    rel = -0.691 + 10 * np.log10(np.mean(10 ** ((L + 0.691) / 10))) - 10
    L2 = L[L > rel]
    return float(-0.691 + 10 * np.log10(np.mean(10 ** ((L2 + 0.691) / 10))))


class _Meter:
    """Fast integrated loudness of the K-energy with per-control-block weights (gain^2 x fade^2) over a sample
    window [lo, hi): the server's 400 ms / 100 ms blocks at their exact sample positions, block sums read from a
    control-rate running sum (linear inside a 32-sample block). Agrees with the server's meter within ~0.02 LU."""
    def __init__(self, e, fs, eB=None, n=None):
        B = _BLOCK
        if eB is None:
            nb = -(-len(e) // B)
            pe = np.zeros(nb * B); pe[:len(e)] = e
            eB = pe.reshape(nb, B).sum(1); n = len(e)
        self.eB = np.asarray(eB, np.float64); self.fs = fs; self.n = int(n)
        self.blk, self.hop = int(0.4 * fs), int(0.1 * fs)

    def lufs(self, wB=None, lo=0, hi=None):
        hi = self.n if hi is None else hi
        w = self.eB if wB is None else self.eB * wB
        c = np.concatenate(([0.0], np.cumsum(w))); wp = np.concatenate((w, [0.0]))
        st = lo + np.arange(0, (hi - lo) - self.blk + 1, self.hop)
        if not len(st):
            return None

        def C(sm):
            b = sm // _BLOCK
            return c[b] + (sm - b * _BLOCK) / _BLOCK * wp[b]
        return _gated((C(st + self.blk) - C(st)) / self.blk)


def _check_audible(x):
    """Refuse silence, DC-only and non-finite audio with a clear message (the limiter can't measure them)."""
    step = max(1, len(x) // 400000)
    xs = np.asarray(x[::step], np.float64)
    if not np.isfinite(xs).all():
        bad = int(np.count_nonzero(~np.isfinite(np.asarray(x))))
        raise ValueError(f"the audio has {bad} NaN/inf samples (a plugin glitch?): fix the source before finishing")
    if not len(xs) or float(np.max(np.std(xs, axis=0))) < 10 ** (-70 / 20):
        raise ValueError("the audio is silent or DC only (nothing over -70 dB to measure)")


# ----------------------------------------------------------------------------- limiter
def _peak_ctrl(env, fs):
    """dB peak per control block, looking ahead: max over [block start, block end + lookahead]."""
    B = _BLOCK; L = max(1, int(LOOKAHEAD_S * fs))
    nb = -(-len(env) // B)
    pe = np.zeros(nb * B, np.float32); pe[:len(env)] = env
    bm = pe.reshape(nb, B).max(1).astype(np.float64)
    k = (B + L - 2) // B
    pk = bm.copy()
    for s in range(1, k + 1):
        np.maximum(pk[:-s], bm[s:], out=pk[:-s])
    return 20 * np.log10(np.maximum(pk, 1e-12))


def _ctrl_curve(pk_db, gain_db, ceil_db, fs):
    """Gain reduction (dB, >= 0) per control block for pre-gain gain_db and ceiling ceil_db, or None."""
    req = np.maximum(0.0, pk_db + gain_db - ceil_db)
    nz = np.flatnonzero(req)
    if not len(nz):
        return None
    B = _BLOCK
    af = float(np.exp(-B / (REL_FAST_S * fs))); ar = float(np.exp(-B / (REL_SLOW_S * fs)))
    ka = 1 - float(np.exp(-B / (SLOW_ATT_S * fs)))
    i0 = int(nz[0])
    out = [0.0] * (len(req) - i0); f = s = 0.0; j = 0
    for r in req[i0:].tolist():
        if r > f:
            f = r                                                    # fast: instant attack, 50 ms release
        else:
            f = r + (f - r) * af
        if r > s:
            s += (r - s) * ka                                        # slow: builds only on sustained reduction
        else:
            s = r + (s - r) * ar
        out[j] = f if f > s else s; j += 1
    full = np.zeros(len(req)); full[i0:] = out
    return full


def _block_gain(red_db, fs):
    """Per-block linear gain as the meter sees it (block-rate version of the lookahead ramp)."""
    lin = 10 ** (-red_db / 20)
    Lb = max(1, int(round(LOOKAHEAD_S * fs / _BLOCK)))
    c = np.concatenate(([0.0], np.cumsum(np.concatenate((lin[:1].repeat(Lb), lin)))))
    return np.minimum((c[Lb + 1:] - c[1:len(lin) + 1]) / Lb, 1.0)


def _sample_gain(red_db, n, fs, chunk=1 << 20):
    """Linear per-sample limiter gain (<= 1, float32): control blocks repeated, trailing mean over the lookahead so
    the ramp reaches each peak's gain by the peak. Computed in chunks (float64 inside), so no full-length float64
    temporaries."""
    linB = 10 ** (-np.asarray(red_db, np.float64) / 20)
    L = max(1, int(LOOKAHEAD_S * fs))
    out = np.empty(n, np.float32)
    for a in range(0, n, chunk):
        b = min(n, a + chunk)
        lo = a - L                                               # the trailing window reaches L samples back
        idx = np.arange(max(lo, 0), b)
        lin = linB[np.minimum(idx // _BLOCK, len(linB) - 1)]
        if lo < 0:                                               # before the start: the first block's gain
            lin = np.concatenate((np.full(-lo, linB[0]), lin))
        c = np.concatenate(([0.0], np.cumsum(lin)))
        k = b - a
        out[a:b] = np.minimum((c[L + 1:L + 1 + k] - c[1:1 + k]) / L, 1.0)
    return out


def _lufs_int(q, full, fs, chunk_blocks=512):
    """The server's _lufs maths (400 ms blocks at 100 ms, K-weighting by FFT, both gates) on integer samples / full,
    a chunk of blocks at a time: the same number without a float64 copy of the whole file."""
    blk, hop = int(0.4 * fs), int(0.1 * fs)
    f = np.fft.rfftfreq(blk, 1 / fs); kw = H["_k_weight"](f, fs)
    n = len(q); nb = max(0, (n - blk) // hop + 1); z = np.zeros(nb)
    for c in range(min(q.shape[1], 2)):
        for a in range(0, nb, chunk_blocks):
            b = min(nb, a + chunk_blocks)
            col = np.ascontiguousarray(q[a * hop:(b - 1) * hop + blk, c], dtype=np.float64) / float(full)
            for a2 in range(0, b - a, 256):
                b2 = min(b - a, a2 + 256)
                fr = np.lib.stride_tricks.as_strided(col[a2 * hop:], shape=(b2 - a2, blk), strides=(hop * 8, 8))
                z[a + a2:a + b2] += (np.abs(np.fft.rfft(fr, axis=1)) ** 2 @ kw) * 2 / (blk * blk)
    if not nb:
        return None
    Lb = -0.691 + 10 * np.log10(np.maximum(z, 1e-12)); Lb = Lb[Lb > -70]
    if not len(Lb):
        return None
    rel = -0.691 + 10 * np.log10(np.mean(10 ** ((Lb + 0.691) / 10))) - 10
    L2 = Lb[Lb > rel]
    return float(-0.691 + 10 * np.log10(np.mean(10 ** ((L2 + 0.691) / 10))))


MAX_RED_DB = 20.0                                         # never more peak reduction than this (gain cap)
KNEE_LU = 0.3                                             # unreachable target: stop this far under the plateau


def _solve(meter, pk, ceil_db, target, off, wB, lo, hi, gain, iterations=10):
    """Pre-gain so that loudness (fast meter + off, window lo:hi, weights wB) hits target with the limiter.
    -> (gain, red, predicted LUFS, iterations, reach). reach is None when the target is reachable, else
    {"max_lufs": the loudest this mix gets at the ceiling, "why": plateau | max_reduction | gate}: past that point the
    limiter only takes away what extra gain adds (dense mixes plateau around -10 LUFS), so the solve stops KNEE_LU
    under the plateau instead of piling on gain (it used to run to +50 dB and fail with a TypeError)."""
    gmax = float(ceil_db) - float(np.max(pk)) + MAX_RED_DB

    def at(g):
        red = _ctrl_curve(pk, g, ceil_db, meter.fs)
        g2 = wB if red is None else wB * _block_gain(red, meter.fs) ** 2
        m = meter.lufs(g2, lo, hi)
        return red, (None if m is None else m + g + off)
    hist = []; slope = 0.7; why = None; n = 0
    gain = min(float(gain), gmax)
    for _ in range(iterations):
        red, Lg = at(gain); n += 1
        if Lg is None:                                           # everything limited under the -70 gate
            why = "gate"; break
        hist.append((gain, Lg, red))
        err = float(target) - Lg
        if abs(err) < 0.005:
            break
        if len(hist) > 1:
            dg = hist[-1][0] - hist[-2][0]
            if abs(dg) > 1e-6:
                s = (hist[-1][1] - hist[-2][1]) / dg
                if dg > 0.25 and s < 0.15 and err > 0:
                    why = "plateau"; break
                slope = min(1.0, max(0.1, s))
        ng = min(gain + max(-6.0, min(6.0, err / slope)), gmax)
        if err > 0 and ng <= gain + 1e-6:                        # already at the reduction cap
            why = "max_reduction"; break
        gain = ng
    if not hist:                                                 # the first guess was already past the gate
        gain = min(gain, float(ceil_db) - float(np.max(pk)))
        red, Lg = at(gain); n += 1
        if Lg is None:
            raise ValueError("the audio is silent or DC only (nothing to measure)")
        hist.append((gain, Lg, red)); why = why or "gate"
    if why is None:
        return hist[-1][0], hist[-1][2], hist[-1][1], n, None
    Lmax = max(h[1] for h in hist)
    goal = Lmax - (KNEE_LU if why != "max_reduction" else 0.0)
    if why != "max_reduction" and goal < float(target):
        lo_h = max((h for h in hist if h[1] <= goal), key=lambda h: h[0], default=None)
        hi_h = min((h for h in hist if h[1] >= goal), key=lambda h: h[0])
        if lo_h is None:                                         # bracket from below: plain gain to the ceiling
            g0 = min(hist[0][0], float(ceil_db) - float(np.max(pk)))
            r0, L0 = at(g0); n += 1
            lo_h = (g0, L0, r0) if L0 is not None and L0 <= goal else None
        if lo_h is not None and hi_h[0] > lo_h[0]:
            a, b = lo_h, hi_h
            for _ in range(10):                                  # secant/bisection on the rising part
                if b[1] - a[1] > 1e-6:
                    g = a[0] + (goal - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                    g = min(max(g, a[0] + 0.1 * (b[0] - a[0])), b[0] - 0.1 * (b[0] - a[0]))
                else:
                    g = 0.5 * (a[0] + b[0])
                red, Lg = at(g); n += 1
                if Lg is None:
                    b = (g, Lmax, red); continue
                if abs(Lg - goal) < 0.03:
                    a = b = (g, Lg, red); break
                if Lg < goal:
                    a = (g, Lg, red)
                else:
                    b = (g, Lg, red)
            best = b if abs(b[1] - goal) <= abs(a[1] - goal) else a
        else:
            best = hi_h
    else:
        best = max(hist, key=lambda h: (h[1], -h[0]))
    if best[1] >= float(target) - 0.005:                         # the knee search landed on the target after all
        return best[0], best[2], best[1], n, None
    return best[0], best[2], best[1], n, {"max_lufs": round(Lmax, 1), "why": why}


def _reach_note(reach, target, tp_max, got):
    """Plain words for an unreachable loudness target."""
    return (f"the most this mix reaches at {tp_max:g} dBTP is about {reach['max_lufs']:.1f} LUFS; past that the "
            f"limiter only squashes (the final is {got:.1f} LUFS, {target - got:.1f} LU under the {target:g} target, "
            f"rendered audio kept). Louder needs mix changes (tame the loudest peaks and low end, bus compression, "
            f"denser arrangement); or finish at target_lufs={int(np.floor(got))} for less limiting")


# ----------------------------------------------------------------------------- trim / fade (as audio_finish)
def _trim(x, glin, fs, floor_db=-58.0, chunk_hops=2000):
    """Lead silence and dead tail of the signal x * glin: -> (start, end, last_music) samples.
    Start 50 ms before the first sample above -60 dBFS; end 0.3 s after the smoothed level, once past the last
    point above -40 dBFS, drops under floor_db (late pedal clicks / stray noise ignored).
    Streams over x in chunks (per-sample peak and mean square are never stored for the whole song)."""
    n, ch = x.shape; scalar = np.ndim(glin) == 0
    hop = int(fs) // 20; nh = n // hop
    thr = 10 ** (-60 / 20); i0 = None
    msq = np.zeros(nh)
    C = hop * chunk_hops
    for a in range(0, n, C):
        b = min(n, a + C)
        xc = np.asarray(x[a:b], dtype=np.float64)
        g = glin if scalar else np.asarray(glin[a:b], dtype=np.float64)
        if i0 is None:
            am = np.abs(xc).max(1) * g
            k = int(np.argmax(am > thr))
            if am[k] > thr:
                i0 = a + k
        hb = min(nh, b // hop) - a // hop                        # whole hops inside this chunk (a is hop-aligned)
        if hb > 0:
            p = np.square(xc[:hb * hop]).mean(1) * (g * g if scalar else g[:hb * hop] ** 2)
            msq[a // hop:a // hop + hb] = p.reshape(hb, hop).mean(1)
    if i0 is None:
        raise ValueError("the audio is silent")
    start = max(0, i0 - int(0.05 * fs))
    rms = np.sqrt(msq)
    sm = np.convolve(rms, np.ones(30) / 30, mode="same"); db = 20 * np.log10(sm + 1e-12)
    loud = np.flatnonzero(db > -40)
    last_music = int(loud[-1]) if len(loud) else len(db) - 1
    below = np.flatnonzero(db[last_music:] < floor_db)
    end_i = last_music + (int(below[0]) if len(below) else len(db) - 1 - last_music)
    end = min(n, end_i * hop + int(0.3 * fs))
    if end <= start:
        end = n
    return start, end, min(end, last_music * hop)


def _dead_tail_end(x, start, end, fade, glin, fs, keep_s=0.8, look_s=60.0):
    """Where the faded, gained audio last rises above -60 dBFS (rule_check's silence threshold) + keep_s: a
    diminuendo over a chord whose notes already died leaves seconds of inaudible tail (trail_silence) - cut there.
    -> the new end (never later than end)."""
    a = max(start, end - int(look_s * fs))
    seg = np.abs(np.asarray(x[a:end], dtype=np.float32)).max(axis=1) * fade[a - start:end - start]
    seg = seg * (np.float32(glin) if np.ndim(glin) == 0 else np.asarray(glin[a:end], dtype=np.float32))
    hit = np.flatnonzero(seg > 10 ** (-60 / 20))
    if not len(hit):
        return end
    e2 = a + int(hit[-1]) + int(keep_s * fs)
    return e2 if e2 < end - int(0.4 * fs) else end


def _fade(start, end, last_music, fs, fade_s):
    """Fade-out length in samples: fade_s seconds, or "auto": from where the tail falls under -40 dBFS to the end
    (only the decay is faded, so hard endings keep their last hit), at least 0.3 s and at most 4 s."""
    n = end - start
    if fade_s in (None, "auto"):
        fo = end - max(start, last_music)
        fo = int(min(max(fo, 0.3 * fs), 4.0 * fs))
    else:
        fo = int(float(fade_s) * fs)
    return min(n, max(0, fo))


def _fade_curve(n, fs, fo, shape="linear"):
    w = np.ones(n)
    fi = min(n, int(0.005 * fs)); w[:fi] = np.linspace(0, 1, fi)
    if fo:
        w[n - fo:] *= _fade_shape(fo, shape, fs)
    return w


def _fade_shape(fo, shape="linear", fs=44100):
    """The fade-out gain over fo samples. linear: 1 -> 0 (the classic, kept exact for explicit fade_s numbers).
    ("diminuendo", depth_db): an even fall of depth_db over the fade (exponential, like a chord dying away) that
    reaches the silence floor right at the end, so the tail stays audible to the end and never stops; a 50 ms taper
    closes it. A held chord becomes a natural decrescendo instead of a flat hold that stops."""
    w = _shape_at(np.linspace(0.0, 1.0, fo), shape)
    if isinstance(shape, (tuple, list)) and fo > 1:
        k = max(2, min(int(fo), int(0.05 * fs)))                 # the closing 50 ms taper
        w[-k:] *= np.linspace(1.0, 0.0, k)
    return w


def _shape_at(u, shape):
    if isinstance(shape, (tuple, list)) and shape and shape[0] == "diminuendo":
        depth = float(shape[1])
        if len(shape) >= 4 and float(shape[3]) > 0:             # a short drop under full level, then the even fall
            pre, fr = float(shape[2]), float(shape[3])
            u = np.asarray(u, dtype=np.float64)
            db = np.where(u < fr, pre * u / fr, pre + (depth - pre) * (u - fr) / max(1e-9, 1.0 - fr))
            return 10 ** (-db / 20.0)
        return 10 ** (-depth * u / 20.0)
    return 1.0 - u
HOLD_FADE_EFF = 0.8                        # rule_check's ending_length / the faded span, for the diminuendo (tests)


def _window_weights(n, start, end, fs, fo, nb, shape="linear"):
    """Per-block fade^2 weights over the whole signal (zeros outside [start, end))."""
    w = np.zeros(nb * _BLOCK); w[start:end] = _fade_curve(end - start, fs, fo, shape) ** 2
    return w.reshape(nb, _BLOCK).mean(1)


def ending_hold_for_rules(R, margin_s=None):
    """Seconds a final chord must sound from its attack so the diminuendo over the hold meets the rules (used by
    flh_shape.flp_extend_ending): min_ending_s / HOLD_FADE_EFF + 1 s (the fade starts just after the attack), at
    least min_tail_s, plus a margin (10 %, at least 2 s). -> (hold_s, margin_s)."""
    R = dict(DEFAULT_RULES, **{k: v for k, v in (R or {}).items() if k in DEFAULT_RULES and v is not None})
    need = max(float(R["min_ending_s"]) / HOLD_FADE_EFF + 1.0, float(R["min_tail_s"]))
    m = max(2.0, 0.1 * need) if margin_s is None else float(margin_s)
    return need + m, m


# ----------------------------------------------------------------------------- the ending's shape (render, numpy)
def _level_curve(x, fs, hop_s=0.1, win_hops=4, chunk=1 << 20):
    """Short-term level of the mid signal as rule_check measures it (0.4 s windows every 0.1 s), streamed in chunks:
    -> (t seconds, dB, sample peak)."""
    n = len(x); hop = max(1, int(hop_s * fs)); nh = n // hop
    ms = np.zeros(nh); pk = 0.0
    step = hop * max(1, chunk // hop)
    for a in range(0, nh * hop, step):
        b = min(nh * hop, a + step)
        xc = np.asarray(x[a:b], dtype=np.float32)
        mono = xc.mean(axis=1, dtype=np.float32) if xc.ndim > 1 else xc
        ms[a // hop:b // hop] = np.square(mono).reshape(-1, hop).mean(1, dtype=np.float64)
        if len(xc):
            pk = max(pk, float(xc.max()), -float(xc.min()))
    if nh < win_hops:
        return np.zeros(0), np.zeros(0), pk
    msw = np.convolve(ms, np.ones(win_hops) / win_hops, mode="valid")
    t = (np.arange(len(msw)) * hop + win_hops * hop / 2.0) / fs
    return t, 10 * np.log10(msw + 1e-20), pk


def _last_attack(x, fs, a, b, pk, chunk=1 << 20):
    """Seconds of the last note attack in x[a:b] (rule_check's onset rule: the 10 ms peak envelope more than doubles
    and is within 30 dB of the peak), or None."""
    h = max(1, int(0.01 * fs)); a = a - a % h
    stop = a + ((b - a) // h) * h; S = h * max(1, chunk // h)
    e10 = []
    for c0 in range(a, stop, S):
        c1 = min(stop, c0 + S)
        xc = np.ascontiguousarray(x[c0:c1]).reshape((c1 - c0) // h, -1)   # one row per 10 ms (all channels)
        e10.append(np.maximum(xc.max(1), -xc.min(1)))
    if not e10:
        return None
    e = np.concatenate(e10).astype(np.float64)
    i = _last_onset(e, pk)
    return (a + i * h) / fs if i is not None else None


def _last_onset(e10, pk, look=5):
    """Index of the last attack in a 10 ms peak envelope: the level more than doubles over the loudest of the
    `look` blocks before it (50 ms - beating between the notes of a held chord is not an attack) and is within
    30 dB of the peak. None when there is none."""
    e = np.asarray(e10, dtype=np.float64)
    if len(e) < 2:
        return None
    prev = np.full(len(e), np.inf)
    for k in range(1, look + 1):
        sh = np.concatenate([np.full(k, e[0]), e[:-k]])
        prev = sh if k == 1 else np.maximum(prev, sh)
    on = np.nonzero((e > 2.0 * (prev + 1e-9)) & (e > pk * 10 ** (-30 / 20)))[0]
    on = on[on > 0]
    return int(on[-1]) if len(on) else None


TAIL_SCAN_S = 300.0                        # the final chord is looked for in the song's last 5 minutes


def ending_shape(x, fs, end=None, attack_s=None):
    """How the song ends, measured on the render: the last attack, how long the final chord holds (until its level
    falls 10 dB under the hold: the notes end), whether that hold is FLAT (a sustained chord - layers holding the
    level - that then stops, rather than a piano decaying by itself), and where a diminuendo over it starts.
    -> {"attack_s", "hold_s", "flat", "fade_from_s", "hold_vs_full_db", "t", "L", "loud"} (t/L: the level curve)."""
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    n = len(x) if end is None else int(end)
    t, L, pk = _level_curve(x[:n], fs)
    out = {"attack_s": None, "hold_s": 0.0, "flat": False, "fade_from_s": None, "t": t, "L": L, "loud": None}
    if not len(L) or pk <= 0:
        return out
    top = float(L.max())
    sel = L[L > top - 40]
    loud = float(np.percentile(sel, 90)) if len(sel) else top
    out["loud"] = loud
    ta = _last_attack(x, fs, max(0, n - int(TAIL_SCAN_S * fs)), n, pk)
    if attack_s is not None and 0 < float(attack_s) < n / fs - 1.0 and (ta is None or ta < float(attack_s) - 1.0):
        # sustained layers as loud as the final chord hide its attack in the audio (the peak doesn't double):
        # the notes say where it is (fl_finalize passes it; fl_ending_check's simulation uses the same moment)
        # PC-VERIFY: FL's render starts at song time 0 - the notes' attack lines up with the audio's (+-50 ms)
        ta = float(attack_s); out["attack_from"] = "notes"
    if ta is None:
        return out
    out["attack_s"] = round(ta, 2)
    out.update(_hold_on_curve(t, L, ta, loud))
    return out


def _hold_on_curve(t, L, ta, loud):
    """The final chord's hold on a level curve (ending_shape's rule, shared with the note simulation): from 0.5 s
    after the attack until the level falls 10 dB under its first 1.5 s; FLAT = spread <= 8 dB and a fall <= 1.5 dB/s.
    -> {} (too short) or {hold_s, flat, fade_from_s, hold_vs_full_db, hold_level_db, hold_spread_db?, hold_fall_db_s?}"""
    ia = int(np.searchsorted(t, ta + 0.5))
    if ia >= len(L) - 5:
        return {}
    out = {}
    ref = float(np.median(L[ia:ia + 15]))
    below = np.nonzero(L[ia:] < ref - 10)[0]
    j = ia + (int(below[0]) if len(below) else len(L) - 1 - ia)
    hold = float(t[j] - ta)
    seg = L[ia:j]
    flat = False
    if hold >= 2.5 and len(seg) >= 10:
        spread = float(np.percentile(seg, 95) - np.percentile(seg, 5))
        rate = (float(seg[:10].mean()) - float(seg[-10:].mean())) / max(0.5, float(t[j - 1] - t[ia]) - 1.0)
        flat = spread <= 8.0 and rate <= 1.5                   # dB over the hold / dB per second
        out["hold_spread_db"] = round(spread, 1); out["hold_fall_db_s"] = round(rate, 2)
    out.update(hold_s=round(hold, 2), flat=bool(flat), fade_from_s=round(ta + min(1.0, 0.1 * hold), 2),
               hold_vs_full_db=round(ref - loud, 1), hold_level_db=round(ref, 1))
    return out


PRE_DROP_MAX_S = 3.0                       # the rule-aware diminuendo leaves full level within this (or 12 % of it)


def _dim_shape(hold_level_db, gain_db, floor_db, fh_s, loud_db=None, R=None):
    """fl_finalize's diminuendo over a flat hold: ("diminuendo", depth_db[, pre_db, pre_frac]). depth: from the hold's
    level (after the gain) down to the trim floor. With rules (and the song's full level known) the fall starts with
    a short drop under rule_check's 'full level' (loud - 6 dB, +2.5 dB room) within PRE_DROP_MAX_S, so ending_length
    is nearly the whole faded span even when the final chord is as loud as the music (layers + piano at the end).
    PC-VERIFY: listen - the held chord settles a few dB just after its attack, then dies away evenly (no audible dip)."""
    depth = min(80.0, max(20.0, float(hold_level_db) + gain_db - floor_db))
    if R is None or loud_db is None or fh_s <= 4.0:
        return ("diminuendo", round(depth, 1))
    pre = max(0.0, float(hold_level_db) - (float(loud_db) - 6.0)) + 2.5
    pre = min(pre, 0.5 * depth)
    frac = min(PRE_DROP_MAX_S, 0.12 * fh_s) / fh_s
    return ("diminuendo", round(depth, 1), round(pre, 2), round(frac, 4))


def _predict_ending(shape, start, end, fo, fs, fade_shape, gain_db, R):
    """ending_length (rule_check's measure) the finished audio should have with this fade, from the level curve:
    last moment within 6 dB of full level -> last moment above the silence threshold (+10 dB crest allowance).
    A prediction from the level curve; the final itself is measured after."""
    t, L, loud = shape.get("t"), shape.get("L"), shape.get("loud")
    if t is None or not len(L) or loud is None:
        return None
    s0, s1, f0 = start / fs, end / fs, (end - fo) / fs
    w = np.ones(len(t))
    if fo > 0:
        u = np.clip((t - f0) / max(1e-9, fo / fs), 0.0, 1.0)
        w = _shape_at(u, fade_shape)
    Lf = np.where((t >= s0) & (t <= s1), L + 20 * np.log10(np.maximum(w, 1e-9)), -400.0)
    full = np.nonzero(Lf >= loud - 6)[0]
    aud = np.nonzero(Lf + gain_db + 10.0 > R["tail_silence_db"])[0]
    if not len(full) or not len(aud):
        return None
    return round(float(t[aud[-1]] - t[full[-1]]), 1)


JUMP_FLOOR_DB = 30.0       # a level jump is measured from at most this far under full level (silence = this floor):
                           # the windows flagged stay the same (a flagged window is > full - 20 dB), the dB is sane
                           # and a ride after a full stop starts here instead of at digital silence


def _jumps_on_curve(t, L, R, t0=2.0, loud=None):
    """rule_check's level_jumps on a level curve: [(t, rise dB)] (rises over max_jump_db within jump_window_s,
    near full level, from t0 on)."""
    if loud is None:
        sel = L[L > np.max(L) - 40]
        loud = float(np.percentile(sel, 90)) if len(sel) else float(np.max(L))
    k = max(1, int(R["jump_window_s"] / 0.1)); out = []
    for i in range(k, len(L)):
        if t[i] < t0:
            continue
        d = L[i] - max(L[i - k], loud - JUMP_FLOOR_DB)          # from silence: a rise from the floor, not 180 dB
        if d > R["max_jump_db"] and L[i] > loud - 20 and (not out or t[i] - out[-1][0] > 2.0):
            out.append((float(t[i]), float(d)))
    return out


def _ride_jumps(x, fs, R, rounds=4):
    """A sudden rise in level the rules forbid (level_jumps: more than max_jump_db within jump_window_s - a part
    entering at full level, a loud section starting on one beat) is ridden: the level just after it is lowered and
    rises back at most (max_jump_db - 2) dB per window, so the entrance swells in over a second or two instead of
    jumping. Only where rule_check would flag it (near full level, after the first 1.5 s); nothing else changes.
    -> (x or a ridden float32 copy, [{at_s, db, ride_db}] or None).
    PC-VERIFY: listen at the reported at_s - the entrance swells in, it doesn't pump or duck audibly."""
    t, L, _pk = _level_curve(x, fs)
    if len(L) < 8:
        return x, None
    sel = L[L > np.max(L) - 40]
    loud = float(np.percentile(sel, 90)) if len(sel) else float(np.max(L))
    on = np.nonzero(L > loud - 50)[0]
    t0 = (float(t[on[0]]) if len(on) else 0.0) + 1.5
    found = _jumps_on_curve(t, L, R, t0, loud)
    if not found:
        return x, None
    k = max(1, int(R["jump_window_s"] / 0.1)); thr = float(R["max_jump_db"]) - 2.0
    ride = np.zeros(len(L)); y = x; Lcur = L
    ts = np.arange(len(x)) / float(fs)
    for _ in range(rounds):
        Lc = Lcur.copy()
        for i in range(k, len(L)):                              # each window rises at most thr (measured, ridden)
            if t[i] >= t0 and Lcur[i] > loud - 22:
                Lc[i] = min(Lc[i], max(Lc[i - k], loud - JUMP_FLOOR_DB) + thr)
        ride = np.minimum(0.0, ride + (Lc - Lcur))
        g = (10 ** (np.interp(ts, t, ride, left=0.0, right=0.0) / 20.0)).astype(np.float32)
        y = x * g[:, None]
        _t2, L2, _ = _level_curve(y, fs)
        Lcur = L2 if len(L2) == len(L) else np.resize(L2, len(L))
        if not _jumps_on_curve(t, Lcur, R, t0, loud):
            break
    rides = [{"at_s": round(a, 1), "db": round(b, 1),
              "ride_db": round(float(ride[max(0, int(np.searchsorted(t, a)) - 1):int(np.searchsorted(t, a)) + 3].min()), 1)}
             for a, b in found[:8]]
    return y, rides


# ----------------------------------------------------------------------------- the ending predicted from the notes
SIM_HOP_S = 0.1
SIM_VEL_DB = 40.0                          # note level: 40*log10(vel/100) dB (velocity -> volume, squared amplitude)
SIM_ATTACK_S = 0.25                        # sustained parts (strings, pads, choir): attack, then flat to note-off
SIM_RELEASE_DB_S = 20.0                    # their release after note-off
SIM_DAMP_DB_S = 40.0                       # a decaying part (piano...) after note-off (damper / release)
SIM_DRUM_DB_S = 25.0
SIM_LOUD_OVER_LUFS = 3.0                   # the music's full short-term level over the final's integrated LUFS
SIM_CREST_DB = 10.0                        # sample peak over the 0.4 s RMS near the silence threshold


def _decay_db_s(key):
    """A struck/plucked note's fall (dB/s) after its first second: low notes ring longer (a piano's low C ~1.2 dB/s,
    middle C ~2.6, an octave up ~3.4). PC-VERIFY: compare with the PC's piano (fl_ending_check vs the render)."""
    return 1.2 + 0.06 * max(0.0, float(key) - 36.0)


def _sim_curve(notes, t):
    """Mean-square level per hop centre t from notes [(on_s, off_s, vel, key, kind)] (kind: 'sustain' | 'decay' |
    'drum'), then rule_check's 0.4 s window -> dB."""
    P = np.zeros(len(t)); A = np.zeros(len(t))
    for on, off, vel, key, kind in notes:
        if vel <= 0 or off <= on:
            continue
        a0 = SIM_VEL_DB * np.log10(max(1.0, float(vel)) / 100.0)
        i0 = int(np.searchsorted(t, on)); i1 = int(np.searchsorted(t, off + 4.0))
        if i1 <= i0:
            continue
        tt = t[i0:i1] - on
        if kind == "sustain":
            db = np.where(t[i0:i1] <= off, 0.0, -SIM_RELEASE_DB_S * (t[i0:i1] - off))
            pw = 10 ** ((a0 + db) / 10.0) * np.clip(tt / SIM_ATTACK_S, 0.0, 1.0)
        else:
            rate = SIM_DRUM_DB_S if kind == "drum" else _decay_db_s(key)
            db = -np.minimum(4.0, 8.0 * tt) - rate * tt
            db = db - SIM_DAMP_DB_S * np.maximum(0.0, t[i0:i1] - off)
            pw = 10 ** ((a0 + db) / 10.0)
        P[i0:i1] += pw
        A[i0:i1] += np.sqrt(pw)
    w = 4
    Pw = np.convolve(P, np.ones(w) / w, mode="same")
    return P, A, 10 * np.log10(Pw + 1e-20)


def final_attack_s(notes):
    """The final chord's attack from notes [(on_s, off_s, vel, ...)]: the earliest onset of the notes starting within
    0.15 s of the last onset (a humanized chord is spread over a few ms)."""
    if not notes:
        return None
    last = max(n[0] for n in notes)
    return float(min(n[0] for n in notes if n[0] >= last - 0.15))


def simulate_ending(notes, rules=None, target_lufs=-16.0, floor_db=-58.0):
    """What fl_finalize + rule_check will measure on the ending, simulated from the notes (no render): the song's
    level curve (struck notes decay, sustained layers hold, releases), the final chord's attack and hold as
    ending_shape finds them, the fade fl_finalize then chooses (the rule-aware diminuendo over a flat hold, else the
    short decay fade), and rule_check's own definitions on the result: ending_length = the last moment within 6 dB of
    the full level (90th percentile) -> the last audible moment; tail_ring = last attack -> silence; level_jumps.
    notes: [(on_s, off_s, vel, key, kind)]. A model (instrument levels and decays are guessed from the notes): the
    render stays the proof. -> {ending_s, tail_s, attack_s, hold_s, flat, fade, jumps, end_s, last_full_s}"""
    R = _rules(rules)
    notes = [n for n in notes if n[1] > n[0]]
    if not notes:
        return None
    end_n = max(n[1] for n in notes)
    t = np.arange(0.0, end_n + 6.0, SIM_HOP_S) + SIM_HOP_S / 2
    P, A, L = _sim_curve(notes, t)
    top = float(L.max()); sel = L[L > top - 40]
    loud = float(np.percentile(sel, 90)) if len(sel) else top
    gain = float(target_lufs) + SIM_LOUD_OVER_LUFS - loud      # relative curve -> dBFS after fl_finalize's gain
    # the last attack (rule_check / ending_shape: the peak envelope more than doubles over the 50 ms before, within
    # 30 dB of the peak): the notes starting together (30 ms) add at least as much amplitude as already sounds
    ta = final_attack_s(notes)           # fl_finalize finds the attack in the audio, else takes this one
    hold = _hold_on_curve(t, L, ta, loud)
    # fl_finalize's trim end: 0.3 s after the 1.5 s level drops under the floor (once past the last -40 dBFS point)
    sm = 10 * np.log10(np.convolve(P, np.ones(15) / 15, mode="same") + 1e-20) + gain
    lm_i = np.nonzero(sm > -40)[0]
    lm = float(t[lm_i[-1]]) if len(lm_i) else float(t[-1])
    bl = np.nonzero((t >= lm) & (sm < floor_db))[0]
    te = (float(t[bl[0]]) if len(bl) else float(t[-1])) + 0.3
    a_i = np.nonzero(L + gain + SIM_CREST_DB > -60.0)[0]
    t_start = max(0.0, (float(t[a_i[0]]) if len(a_i) else 0.0) - 0.05)
    fo = min(max(te - lm, 0.3), 4.0)
    fshape, kind = "linear", "auto_decay"
    if hold.get("flat") and hold.get("fade_from_s") is not None and te - max(t_start, hold["fade_from_s"]) > fo:
        fo = min(te - t_start, te - max(t_start, hold["fade_from_s"]))
        fshape, kind = _dim_shape(hold.get("hold_level_db", loud), gain, floor_db, fo, loud, R), "hold_diminuendo"
    u = np.clip((t - (te - fo)) / max(1e-9, fo), 0.0, 1.0)
    w = _shape_at(u, fshape)
    live = (t >= t_start) & (t <= te)
    Lf = np.where(live, L + 20 * np.log10(np.maximum(w, 1e-9)), -400.0)
    sel = Lf[Lf > Lf.max() - 40]
    loud_f = float(np.percentile(sel, 90)) if len(sel) else float(Lf.max())
    full = np.nonzero(Lf >= loud_f - 6)[0]
    aud = np.nonzero(Lf + gain + SIM_CREST_DB > R["tail_silence_db"])[0]
    if not len(full) or not len(aud):
        return None
    end_t = min(te, float(t[aud[-1]]) + SIM_HOP_S)
    last_full = float(t[full[-1]])
    jumps = _jumps_on_curve(t[live] - t_start, Lf[live], R, 2.0, loud_f)
    return {"ending_s": round(end_t - last_full, 1), "tail_s": round(end_t - ta, 1), "attack_s": round(ta, 2),
            "hold_s": hold.get("hold_s"), "flat": bool(hold.get("flat")), "fade": kind,
            "fade_s": round(fo, 1), "hold_vs_full_db": hold.get("hold_vs_full_db"), "end_s": round(end_t, 1),
            "last_full_s": round(last_full, 1),
            "jumps": [{"at_s": round(a + t_start, 1), "db": round(b, 1)} for a, b in jumps[:8]]}


def _note_kinds(project, d, ev):
    """channel -> 'sustain' | 'decay' | 'drum' (flh_shape's layer / drum rules: journal roles, then channel names)."""
    f = H.get("_ending_channel_kinds")
    if callable(f):
        try:
            return f(project, d, ev)
        except Exception:                                         # noqa: BLE001 - the plain guess below
            pass
    return lambda ch: "decay"


def ending_notes_s(project, d=None):
    """The song's notes in seconds through the tempo map, with the simulation's kind per note:
    [(on_s, off_s, vel, key, kind)] (muted / zero-velocity notes left out)."""
    d = d if d is not None else H["_read_flp_bytes"](project)
    ev = H["_events"](d)
    notes, _ = H["_song"](d, ev)
    if not notes:
        return []
    tick_s = H["_rc"]().timeline(d)[0]
    kind = _note_kinds(project, d, ev)
    memo = {}
    out = []
    for pos, ln, ch, key, vel in notes:
        if ch not in memo:
            memo[ch] = kind(ch)
        out.append((tick_s(pos), tick_s(pos + max(1, ln)), float(vel), int(key), memo[ch]))
    return out


def _notes_attack(pl):
    """The final chord's attack (song seconds) from the project's notes, for finish's ending_shape (cached on the plan;
    None when the notes can't be read)."""
    if "_attack_s" not in pl:
        try:
            pl["_attack_s"] = final_attack_s(ending_notes_s(pl["project"]))
        except Exception:                                         # noqa: BLE001 - the audio's own attack then
            pl["_attack_s"] = None
    return pl["_attack_s"]


def ending_predict(project, rules=None, target_lufs=None, d=None):
    """simulate_ending on the project's own notes (fl_ending_check's prediction; flp_extend_ending checks its hold
    with simulate_ending too). target_lufs: the profile's finish loudness (default -16)."""
    if target_lufs is None:
        try:
            target_lufs = float(((flh_store.profile_get() or {}).get("finish") or {}).get("lufs") or -16.0)
        except Exception:                                         # noqa: BLE001
            target_lufs = -16.0
    return simulate_ending(ending_notes_s(project, d), rules, target_lufs)


def load_wav(path):
    """WAV -> (float32 samples (n, ch) in -1..1, rate, bits): PCM 8/16/24/32-bit, float 32/64, extensible headers.
    Read block by block into one float32 array (float32 holds 24-bit PCM exactly and halves the memory of the
    finishing pass). NaN/inf samples -> a clear error."""
    import fl_audio_edit as AE
    x, rate, bits, _ = AE.load_ex(path, np.float32, "raise")
    return x, rate, bits


def _levels(x):
    """Per-sample max |x| over channels and mean square over channels (for the trim)."""
    a = np.abs(x[:, 0]); p = np.square(x[:, 0])
    for c in range(1, x.shape[1]):
        np.maximum(a, np.abs(x[:, c]), out=a); p += np.square(x[:, c])
    if x.shape[1] > 1:
        p /= x.shape[1]
    return a, p


def _quantize_gain(x, start, end, fade, glin, bits, chunk=1 << 20):
    """x[start:end] * fade * glin -> int32 samples at `bits` (as _quantize: TPDF dither for 16-bit, rounding for 24),
    chunk by chunk: the only full-length output is the int32 array."""
    full = (1 << (int(bits) - 1)) - 1
    n = end - start; ch = x.shape[1]
    q = np.empty((n, ch), np.int32)
    rng = np.random.default_rng(1) if int(bits) == 16 else None
    scalar = np.ndim(glin) == 0
    for a in range(0, n, chunk):
        b = min(n, a + chunk)
        w = fade[a:b] * (np.float32(glin) if scalar else glin[start + a:start + b])
        y = x[start + a:start + b].astype(np.float32) * w[:, None]
        y *= full
        if rng is not None:
            y += rng.random(y.shape, dtype=np.float32); y -= rng.random(y.shape, dtype=np.float32)
        np.round(y, out=y)
        np.clip(y, -full - 1, full, out=y)
        q[a:b] = y
    return q, full


def _quantize(y, bits):
    """float -> int at `bits` (y is scaled in place): TPDF dither (+-1 LSB triangular) for 16-bit, rounding for 24."""
    full = (1 << (int(bits) - 1)) - 1
    y *= full
    if int(bits) == 16:
        rng = np.random.default_rng(1)
        y += rng.random(y.shape, dtype=np.float32); y -= rng.random(y.shape, dtype=np.float32)
    np.round(y, out=y)
    np.clip(y, -full - 1, full, out=y)
    return y.astype(np.int32), full


# ----------------------------------------------------------------------------- the finishing pass
def finish(x, fs, output, target_lufs, max_true_peak=-1.0, fade_s="auto", bits=24, limiter=True, floor_db=-58.0,
           hold_fade=None, rules=None, ending_attack_s=None):
    """Render WAV samples -> verified FLAC at output: limit (if needed), trim, fade, dither, encode.
    hold_fade (default: on for fade_s "auto"; fl_finalize also turns it on for the profile's default number): a final
    chord that holds FLAT and then stops (sustained layers) is faded from just after its attack to silence as a
    diminuendo (ending_shape) - a number fade_s is then the shortest fade. An explicit number with hold_fade off is
    exact (linear). rules: the finished audio is checked against them (info["rules"], with fixes for what fails).
    -> info: lufs / true_peak_dbfs measured on the written integer samples, gain_db, limiter stats, steps_s."""
    import flac_lite
    t = time.time(); st = {}
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    n, ch = x.shape; target = float(target_lufs); tp_max = float(max_true_peak)
    _check_audible(x)
    rides = None
    if rules:                                                    # sudden rises the rules forbid: ridden in first
        x, rides = _ride_jumps(x, fs, _rules(rules))
        st["ride_jumps"] = round(time.time() - t, 2); t = time.time()
    A = analyse(x, fs, blocks=True)
    meter = _Meter(None, fs, eB=A["eB"], n=n); nb = len(meter.eB)
    L_srv = meter.lufs()                                         # verified with the server's meter maths at the end
    if L_srv is None:
        raise ValueError("the audio is silent (or shorter than 0.4 s)")
    off = 0.0
    pk = _peak_ctrl(A["env"], fs); tp_in = float(20 * np.log10(max(float(A["env"].max()), 1e-12)))
    del A                                                        # the envelope is in pk now
    st["analyse"] = round(time.time() - t, 2); t = time.time()
    ceil = tp_max - MARGIN_DB
    gain = target - L_srv
    start, te, lm = _trim(x, 10 ** (gain / 20), fs, floor_db)    # te: where the audio dies (the trim's end)
    end = te
    info = {"lufs_in": round(L_srv, 2), "true_peak_in": round(tp_in, 2)}
    if hold_fade is None:
        hold_fade = fade_s in (None, "auto")
    R = _rules(rules) if rules else None
    shape = None
    if hold_fade:                                                # how the song ends (level curve, last attack)
        try:
            shape = ending_shape(x, fs, te, ending_attack_s)
        except Exception as e:                                   # noqa: BLE001 - the plain fade still works
            info["ending_shape_error"] = f"{type(e).__name__}: {e}"
        st["ending_shape"] = round(time.time() - t, 2); t = time.time()
    fshape = "linear"

    def fade_len(start, te, lm, gain_db):
        """(fade samples, shape, end): a flat hold becomes a long diminuendo from just after its attack, falling
        evenly from the hold's level to the trim floor at the end.
        PC-VERIFY: listen - a held final chord fades smoothly to silence, no audible stop, rules pass first time."""
        fo = _fade(start, te, lm, fs, fade_s)
        if not (shape and shape.get("flat") and shape.get("fade_from_s") is not None):
            return fo, "linear", te
        fh = te - max(start, int(shape["fade_from_s"] * fs))
        if fh <= fo:
            return fo, "linear", te
        fh = min(te - start, fh)
        return fh, _dim_shape(shape.get("hold_level_db", -20.0), gain_db, floor_db, fh / fs, shape.get("loud"), R), te
    red = None; rounds = 0; fixes = []; reach = None
    for attempt in range(4):
        for _ in range(2):                                       # window from the actual gain (as audio_finish)
            fo, fshape, end = fade_len(start, te, lm, gain)
            wB = _window_weights(n, start, end, fs, fo, nb, fshape)
            if limiter:
                gain, red, L_pred, it, reach = _solve(meter, pk, ceil, target, off, wB, start, end, gain)
                rounds += it
            else:                                                # plain gain, capped by the peak
                gain = target - (meter.lufs(wB, start, end) + off)
                if tp_in + gain > ceil:
                    gain = ceil - tp_in
                red = None; L_pred = meter.lufs(wB, start, end) + off + gain
            gs = glin = None
            gs = None if red is None else _sample_gain(red, n, fs)
            if gs is None:
                glin = 10 ** (gain / 20)
            else:                                                # in place: one float32 array (gs = glin / g)
                gs *= np.float32(10 ** (gain / 20)); glin = gs
            s2, e2, lm2 = _trim(x, glin, fs, floor_db)
            if (s2, e2) == (start, te):
                break
            start, te, lm = s2, e2, lm2
        fcur = _fade_curve(end - start, fs, fo, fshape).astype(np.float32); f_from = end - fo
        if isinstance(fshape, tuple):                            # the diminuendo can bury a decayed tail early
            e2 = _dead_tail_end(x, start, end, fcur, glin, fs)
            if e2 < end:
                fcur = fcur[:e2 - start]; end = e2
        q, full = _quantize_gain(x, start, end, fcur, glin, bits)
        st[f"limit_{attempt}" if attempt else "limit"] = round(time.time() - t, 2); t = time.time()
        enc = _Bg(flac_lite.encode, output, q, fs, int(bits))     # speculative: kept when the checks pass
        lq = _Bg(_lufs_int, q, full, fs)
        tp_q = true_peak_db(q, scale=1.0 / float(full))
        L_q = lq.get()
        if L_q is None:
            enc.get()
            raise ValueError("the finished audio is shorter than one 400 ms loudness block")
        st["verify" if not attempt else f"verify_{attempt}"] = round(time.time() - t, 2); t = time.time()
        loud_ok = abs(L_q - target) <= LUFS_TOL or (red is None and gain >= ceil - tp_in - 1e-9 and L_q < target) \
            or (reach is not None and L_q < target and abs(L_q - L_pred) <= 0.25)
        if loud_ok and tp_q <= tp_max:
            break
        enc.get()
        fixes.append({"lufs": round(L_q, 3), "true_peak": round(tp_q, 3)})
        off += L_q - L_pred                                      # learn the meters' difference on this song
        if tp_q > ceil + 0.02:
            ceil -= tp_q - ceil + 0.01
    fl = enc.get()
    st["encode_wait"] = round(time.time() - t, 2)
    over = 0.0
    if gs is not None:                                           # gs holds glin = limiter gain x pre-gain
        over = float(np.count_nonzero(gs[start:end] < np.float32(10 ** (gain / 20) * 10 ** (-1 / 20)))) / fs * 1000
    gmax = 0.0 if red is None else float(red.max())
    info.update(output=output, bytes=fl["bytes"], length=f"{int((end - start) / fs // 60)}:{(end - start) / fs % 60:05.2f}",
                trim_start_s=round(start / fs, 3), end_s=round(end / fs, 3), fade_s=round(fo / fs, 2),
                gain_db=round(gain, 2), limited=red is not None, gain_reduction_max_db=round(gmax, 2),
                ms_over_1db=round(over, 1), lufs=round(L_q, 2), true_peak_dbfs=round(tp_q, 2), bits=int(bits),
                rate=int(fs), channels=int(ch), limiter_iterations=rounds, steps_s=st)
    if fixes:
        info["corrections"] = fixes
    if rides:
        info["jumps_smoothed"] = rides
    if shape and shape.get("attack_s") is not None:
        dim = isinstance(fshape, tuple)
        fd = {"kind": "hold_diminuendo" if dim else ("auto_decay" if fade_s in (None, "auto")
                                                                        else "fixed"),
              "last_attack_s": round(shape["attack_s"] - start / fs, 1), "hold_s": shape["hold_s"],
              "flat_hold": shape["flat"]}
        if dim:
            fd["from_s"] = round((f_from - start) / fs, 1); fd["fall_db"] = fshape[1]
            if len(fshape) >= 4:
                fd["settle_db"] = fshape[2]
        if R:
            fd["predicted_ending_s"] = _predict_ending(shape, start, f_from + fo, fo, fs, fshape, gain, R)
        info["fade"] = fd
    if R:                                                        # the finished audio itself, against the rules
        try:
            t = time.time()
            info["rules"] = rule_check(q, fs, R, scale=1.0 / float(full))
            st["rules"] = round(time.time() - t, 2)
        except Exception as e:                                   # noqa: BLE001 - a check never costs the final
            info["rules"] = {"pass": None, "error": f"{type(e).__name__}: {e}"}
    if abs(L_q - target) > LUFS_TOL:
        info["below_target_db" if L_q < target else "above_target_db"] = round(abs(L_q - target), 2)
        if not limiter:
            info["note"] = "limiter off: the true-peak ceiling capped the gain"
    if reach is not None and L_q < target - LUFS_TOL:
        info["max_lufs"] = reach["max_lufs"]; info["unreachable"] = reach["why"]
        info["note"] = _reach_note(reach, target, tp_max, L_q)
    if tp_q > tp_max:
        info["warning"] = f"true peak {tp_q:.2f} dBTP above the {tp_max} ceiling"
    elif gmax > 6:
        info["warning"] = f"{gmax:.1f} dB of limiting: audible on peaks; consider a lower target"
    return info


DEFAULT_RULES = {"min_ending_s": 30.0, "min_tail_s": 6.0, "tail_silence_db": -60.0, "max_lead_silence_s": 0.3,
                 "max_trail_silence_s": 2.0, "max_jump_db": 10.0, "jump_window_s": 0.5}


def _st_loudness(x, fs, win=0.4, hop=0.1):
    """Short-term level (dB RMS of the mid signal, K-weighting left out: it's for changes over time) -> (t, dB)."""
    mono = x.mean(axis=1) if x.ndim > 1 else x
    w = max(1, int(win * fs)); h = max(1, int(hop * fs))
    n = max(0, (len(mono) - w) // h + 1)
    if n == 0:
        return np.zeros(0), np.zeros(0)
    c = np.concatenate([[0.0], np.cumsum(mono.astype(np.float64) ** 2)])
    idx = np.arange(n) * h
    ms = (c[idx + w] - c[idx]) / w
    return (idx + w / 2) / fs, 10 * np.log10(ms + 1e-20)


def flac_load_any(path):
    """Our own FLAC back as samples (fixed predictors: the numpy decoder)."""
    import flh_review
    return flh_review.flac_load(path)


def _rules(rules):
    """The defaults with `rules` on top - unknown names and non-numbers are refused (checked before any render)."""
    if rules is not None and not isinstance(rules, dict):
        raise ValueError("rules: an object like {min_ending_s: 30, max_jump_db: 10}")
    R = dict(DEFAULT_RULES)
    for k, v in (rules or {}).items():
        if k == "exempt" or v is None:
            continue
        if k not in DEFAULT_RULES:
            raise ValueError(f"rules: unknown rule '{k}' (known: {', '.join(sorted(DEFAULT_RULES))}, exempt)")
        try:
            R[k] = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"rules: {k} must be a number (got {v!r})") from None
    return R


def rule_check(x, fs, rules=None, scale=None):
    """A render checked against a collection's rules (for songs a game or a playlist starts and fades at any moment):
    ending length, the last chord's ring, a tail that dies to true silence (no noise/hum left, not cut off), lead and
    trail silence, a click on the first note, sudden level jumps. scale: multiply the samples (integer finals).
    -> {"pass", "checks": [{rule, pass, value, limit, at?, why}]}"""
    R = _rules(rules)
    x = np.asarray(x, dtype=np.float64)
    if scale is not None:
        x = x * float(scale)
    if x.ndim == 1:
        x = x[:, None]
    mono = np.abs(x).max(axis=1)
    pk = float(mono.max()) + 1e-12
    out = []

    def add(rule, ok, value, limit, why, at=None):
        if isinstance(value, (np.floating, np.integer)):
            value = float(value)
        d = {"rule": rule, "pass": bool(ok), "value": value, "limit": limit, "why": why}
        if at is not None:
            d["at_s"] = float(at)
        out.append(d)
    thr_lin = 10 ** (R["tail_silence_db"] / 20)
    aud = np.nonzero(mono > thr_lin)[0]
    if not len(aud):
        return {"pass": False, "checks": [{"rule": "audible", "pass": False, "why": "the render is silent"}]}
    first, last = int(aud[0]), int(aud[-1])
    lead = first / fs; trail = (len(mono) - 1 - last) / fs
    add("lead_silence", lead <= R["max_lead_silence_s"], round(lead, 2), R["max_lead_silence_s"],
        "silence before the first note (a game starts the track at once; fl_finalize trims it)")
    add("trail_silence", trail <= R["max_trail_silence_s"], round(trail, 2), R["max_trail_silence_s"],
        "silence after the tail (fl_finalize trims it to the decay)")
    seg = x[first:first + max(1, int(0.005 * fs))].mean(axis=1)    # the first 5 ms
    jump = float(np.abs(np.diff(seg)).max()) if len(seg) > 1 else 0.0
    add("start_click", jump < 0.25 * pk, round(float(20 * np.log10(jump / pk + 1e-12)), 1),
        "first 5 ms: no step bigger than -12 dB of the peak", "a hard first note or a click when the game starts it")
    t, L = _st_loudness(x, fs)
    if len(L):
        loud = float(np.percentile(L[L > np.max(L) - 40], 90)) if np.any(L > np.max(L) - 40) else float(np.max(L))
        last_loud = t[np.nonzero(L >= loud - 6)[0][-1]]            # the last moment at (near) full level
        end_t = last / fs
        ending = end_t - last_loud
        add("ending_length", ending >= R["min_ending_s"], round(ending, 1), R["min_ending_s"],
            "time from the last full-level moment to silence: a long, gentle outro (the game may fade it anyway)",
            round(float(last_loud), 1))
        env = mono[:last + 1]
        hop = max(1, int(0.01 * fs)); nfr = len(env) // hop
        e10 = env[:nfr * hop].reshape(nfr, hop).max(axis=1) if nfr else env
        io = _last_onset(e10, pk)
        last_on = io * hop / fs if io is not None else 0.0
        tail = end_t - last_on
        add("tail_ring", tail >= R["min_tail_s"], round(tail, 1), R["min_tail_s"],
            "the last chord rings out (from its attack to silence)", round(float(last_on), 1))
        end_rms = 20 * np.log10(float(np.sqrt(np.mean(x[max(0, last - int(0.05 * fs)):last + 1] ** 2))) + 1e-12)
        pre = x[max(0, last - int(1.5 * fs)):max(1, last - int(0.5 * fs))]
        pre_rms = 20 * np.log10(float(np.sqrt(np.mean(pre ** 2))) + 1e-12) if len(pre) else end_rms
        cut = end_rms > R["tail_silence_db"] + 15 and end_rms > pre_rms - 10
        add("tail_not_cut", not cut, round(end_rms, 1), f"> {R['tail_silence_db'] + 15} dBFS at the very end = cut",
            "the reverb/ring dies away instead of stopping abruptly")
        floor = x[max(0, len(x) - int(1.0 * fs)):]
        fl_db = 20 * np.log10(float(np.sqrt(np.mean(floor ** 2))) + 1e-12)
        add("ends_in_silence", fl_db <= R["tail_silence_db"] or trail > 0.5, round(fl_db, 1), R["tail_silence_db"],
            "the last second is true silence (no noise floor, vinyl or hum left running)")
        k = max(1, int(R["jump_window_s"] / 0.1))
        jumps = []
        for i in range(k, len(L)):
            if t[i] < 2.0:
                continue
            d = L[i] - max(L[i - k], loud - JUMP_FLOOR_DB)
            if d > R["max_jump_db"] and L[i] > loud - 20:
                if not jumps or t[i] - jumps[-1][0] > 2.0:
                    jumps.append((float(t[i]), float(d)))
        add("level_jumps", not jumps, [{"at_s": round(a, 1), "db": round(b, 1)} for a, b in jumps[:8]],
            f"<= {R['max_jump_db']} dB within {R['jump_window_s']} s",
            "sudden jumps in loudness (they make game music jump out)")
    return {"pass": all(c["pass"] for c in out), "checks": out}


def rule_fixes(checked, project=None, fade=None, rules=None, explicit_fade=False):
    """Concrete next steps for the rules a final failed: [{rule, fix}] (the fix names the call to make)."""
    if not isinstance(checked, dict):
        return []
    R = _rules(rules) if rules else dict(DEFAULT_RULES)
    proj = project or C.Raw("project")
    fade = fade or {}
    out = []
    by = {c.get("rule"): c for c in checked.get("checks", []) if isinstance(c, dict)}

    def short(rule):
        c = by.get(rule) or {}
        try:
            return max(0.0, float(c["limit"]) - float(c["value"]))
        except (KeyError, TypeError, ValueError):
            return 0.0
    hold_now = fade.get("hold_s")
    need_more = 0.0
    if (by.get("ending_length") or {}).get("pass") is False:
        need_more = max(need_more, short("ending_length") / HOLD_FADE_EFF + 1.0)
    if (by.get("tail_ring") or {}).get("pass") is False:
        need_more = max(need_more, short("tail_ring") + 1.0)
    if need_more > 0:
        more = int(np.ceil(need_more))
        if hold_now:
            call = C.tool_call(H, "flp_extend_ending", {"project": proj, "hold_s": int(np.ceil(hold_now + need_more))})
            fix = (f"hold the last chord {more} s longer (it sounds {hold_now:g} s from its attack now): {call}; then "
                   "fl_finalize again")
        else:
            call = C.tool_call(H, "flp_extend_ending", {"project": proj, "seconds": more})
            fix = f"hold the last chord {more} s longer: {call}; then fl_finalize again"
        if explicit_fade or fade.get("kind") == "fixed":
            fix += " - with fade_s left to the profile/'auto', so the held chord fades as a diminuendo"
        elif fade.get("flat_hold") is False and hold_now:
            fix += " (the chord decays by itself; sustained layers under it make a longer, even ending)"
        for rule in ("ending_length", "tail_ring"):
            if (by.get(rule) or {}).get("pass") is False:
                out.append({"rule": rule, "fix": fix})
    if (by.get("tail_not_cut") or {}).get("pass") is False:
        out.append({"rule": "tail_not_cut", "fix": "the ending stops abruptly: let the last notes ring ("
                    + C.tool_call(H, "flp_extend_ending", {"project": proj}) + ") and finish with fade_s 'auto'"})
    if (by.get("ends_in_silence") or {}).get("pass") is False:
        out.append({"rule": "ends_in_silence", "fix": "noise or hum keeps running at the very end (a noise/vinyl "
                    "layer, an amp sim or a reverb that never dies): end or lower it, then fl_finalize again"})
    if (by.get("level_jumps") or {}).get("pass") is False:
        at = [j.get("at_s") for j in (by["level_jumps"].get("value") or []) if isinstance(j, dict)][:3]
        out.append({"rule": "level_jumps", "fix": f"sudden level jumps (at {at} s of the final): "
                    + C.tool_call(H, "flp_smooth_entries", {"project": proj}) + " ramps parts that enter out of nowhere"})
    if (by.get("start_click") or {}).get("pass") is False:
        out.append({"rule": "start_click", "fix": "the first note starts hard (a click when a game starts the track): "
                    "a softer first note or a slower attack on its channel"})
    for rule in ("lead_silence", "trail_silence"):
        if (by.get(rule) or {}).get("pass") is False:
            out.append({"rule": rule, "fix": f"{rule.replace('_', ' ')} after trimming: check where the song's "
                        "first/last notes sit (flp_info) - fl_finalize trims to the audio it finds"})
    return out


def audio_rule_check(input, rules=None):
    """Check a WAV (a render or kept_wav) against a collection's rules; rules default to the active profile's."""
    x, fs, _ = load_wav(input)
    try:
        prof = flh_store.profile_get() or {}
    except Exception:
        prof = {}
    rl = rules if rules is not None else prof.get("rules")
    r = rule_check(x, fs, rl)
    r["input"] = input
    if r.get("pass") is False:
        try:
            sh = ending_shape(x, fs)
            fx = rule_fixes(r, None, {"hold_s": sh.get("hold_s") or None, "flat_hold": sh.get("flat")}, rl)
        except Exception:                                         # noqa: BLE001 - the check itself stands
            fx = rule_fixes(r, None, None, rl)
        if fx:
            r["fixes"] = fx
    return r


COLLECTION_EXTS = (".flac", ".wav", ".aif", ".aiff", ".mp3", ".m4a", ".ogg")


def _collection_files(folder, pattern):
    """Audio files of a collection: in the folder and one level of song folders below it, matching `pattern`."""
    import fnmatch
    out = []
    for root in [folder] + sorted(os.path.join(folder, d) for d in os.listdir(folder)
                                  if os.path.isdir(os.path.join(folder, d)) and not d.startswith((".", "_"))):
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for n in names:
            p = os.path.join(root, n)
            if os.path.isfile(p) and n.lower().endswith(COLLECTION_EXTS) and fnmatch.fnmatch(n.lower(), pattern.lower()):
                out.append(p)
    return out


def audio_collection_report(folder=None, files=None, pattern="*FINAL*", rules=None, limit_files=40):
    """A collection's finals side by side: loudness, true peak, length, tone (centroid / low-high tilt), width and
    the rule checks (ending, ring, silence) per song, the collection's medians, and the songs that stand out."""
    if not files:
        if not folder or not os.path.isdir(folder):
            raise ValueError("give folder= (a collection folder: finals in it or in its song folders) or files=[...]")
        files = _collection_files(folder, pattern or "*")
        if not files and pattern and pattern != "*":
            files = _collection_files(folder, "*")
    files = [f for f in list(files) if f][:int(limit_files)]
    if not files:
        raise ValueError(f"no audio files found in {folder}")
    rows, errors = [], []
    for i, f in enumerate(files):
        try:
            _progress(step="measure", song=os.path.basename(f), n=f"{i + 1}/{len(files)}")
        except Exception:                                         # noqa: BLE001 - outside a job
            pass
        try:
            x, fs, bits = H["_wav_load"](f)
        except Exception as e:                                    # noqa: BLE001 - listed, the rest still runs
            errors.append(f"{os.path.basename(f)}: {str(e)[:100]}")
            continue
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        m = H["_measure"](x, fs)
        b = m.get("bands_db") or {}                               # high (presence, air) vs low (bass, low mid)
        hi = [float(b[k]) for k in ("presence", "air") if k in b]; lo = [float(b[k]) for k in ("bass", "low_mid") if k in b]
        tilt = round(float(np.mean(hi) - np.mean(lo)), 1) if hi and lo else None
        rc = rule_check(x, fs, rules) if rules is not False else None
        rule_vals = {c["rule"]: c.get("value") for c in (rc or {}).get("checks", [])}
        rows.append({"song": os.path.splitext(os.path.basename(f))[0], "file": f, "lufs": m["lufs"],
                     "true_peak": round(float(true_peak_db(x if x.shape[1] > 1 else np.repeat(x, 2, 1))), 2),
                     "seconds": round(len(x) / fs, 1), "rate": fs, "bits": bits,
                     "centroid_hz": m.get("centroid_hz"), "tilt_db": tilt,
                     "width_db": float(m.get("width_db") or 0.0), "crest_db": float(m.get("crest_db") or 0.0),
                     "ending_s": rule_vals.get("ending_length"), "ring_s": rule_vals.get("tail_ring"),
                     **({"rules_failed": [c["rule"] for c in rc["checks"] if not c["pass"]]} if rc else {})})
        del x
    if not rows:
        raise ValueError("none of the files could be read: " + "; ".join(errors[:5]))

    def med(k):
        v = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
        return round(float(np.median(v)), 2) if v else None
    medians = {k: med(k) for k in ("lufs", "true_peak", "seconds", "centroid_hz", "tilt_db", "width_db", "crest_db",
                                   "ending_s", "ring_s")}
    stands_out = []
    for r in rows:
        why = []
        if medians["lufs"] is not None and r["lufs"] is not None and abs(r["lufs"] - medians["lufs"]) > 1.5:
            why.append(f"{r['lufs'] - medians['lufs']:+.1f} LU from the collection's loudness")
        if r["true_peak"] > -1.0:
            why.append(f"true peak {r['true_peak']} dBTP (over -1)")
        if medians["tilt_db"] is not None and r["tilt_db"] is not None and abs(r["tilt_db"] - medians["tilt_db"]) > 4:
            why.append("brighter than the rest" if r["tilt_db"] > medians["tilt_db"] else "darker than the rest")
        if medians["ending_s"] and r.get("ending_s") is not None and r["ending_s"] < 0.5 * medians["ending_s"]:
            why.append(f"short ending ({r['ending_s']} s vs {medians['ending_s']} s typical)")
        if r.get("rules_failed"):
            why.append("rules: " + ", ".join(r["rules_failed"]))
        if why:
            stands_out.append({"song": r["song"], "why": why})
    for r in rows:
        r.pop("file", None)
    res = {"songs": rows, "medians": medians, "stands_out": stands_out}
    if errors:
        res["unreadable"] = errors
    if not stands_out:
        res["note"] = "the collection is consistent: no song stands out on loudness, peaks, tone or ending"
    return res


def limit(x, fs, target_lufs, max_true_peak=-1.0, iterations=10):
    """Gain to target_lufs with true peak <= max_true_peak - 0.1 dB using the limiter when needed (no trim/fade).
    -> (y, info). info: lufs, true_peak, gain_db, gain_reduction_max_db, ms_over_1db, limited, steps_s."""
    t = time.time(); st = {}
    x = np.asarray(x, np.float64)
    if x.ndim == 1:
        x = x[:, None]
    n = len(x); target = float(target_lufs); ceil = float(max_true_peak) - MARGIN_DB
    _check_audible(x)
    A = analyse(x, fs)
    meter = _Meter(A["e"], fs); L_in = meter.lufs(); off = 0.0
    if L_in is None:
        raise ValueError("the audio is silent or DC only (nothing over the -70 LUFS gate to measure)")
    tp_in = float(20 * np.log10(max(float(A["env"].max()), 1e-12)))
    pk = _peak_ctrl(A["env"], fs); wB = np.ones(len(meter.eB))
    st["analyse"] = round(time.time() - t, 2); t = time.time()
    gain = target - L_in
    info = {"lufs_in": round(L_in, 2), "true_peak_in": round(tp_in, 2)}
    if tp_in + gain <= ceil:                                       # plain gain is enough
        y = x * 10 ** (gain / 20)
        info.update(limited=False, gain_db=round(gain, 2), gain_reduction_max_db=0.0, ms_over_1db=0.0,
                    lufs=round(L_in + gain, 2), true_peak=round(tp_in + gain, 2), steps_s=st)
        return y, info
    reach = None
    for attempt in range(3):
        gain, red, L_pred, it, reach = _solve(meter, pk, ceil, target, off, wB, 0, n, gain, iterations)
        g = 10 ** (gain / 20) * (1.0 if red is None else _sample_gain(red, n, fs))
        y = x * (g if red is None else g[:, None])
        st["limit"] = round(time.time() - t, 2); t = time.time()
        lq = _Bg(H["_lufs"], y, fs)
        tp_out = true_peak_db(y)
        L_out = lq.get()
        st["verify"] = round(time.time() - t, 2); t = time.time()
        if L_out is None:
            raise ValueError("the limited audio measures silent (shorter than one 400 ms loudness block?)")
        loud_ok = abs(L_out - target) <= LUFS_TOL or (reach is not None and L_out < target
                                                       and abs(L_out - L_pred) <= 0.25)
        if loud_ok and tp_out <= ceil + 0.05:
            break
        off += L_out - L_pred
        if tp_out > ceil + 0.05:
            ceil -= tp_out - ceil
    gmax = 0.0 if red is None else float(red.max())
    over = 0.0 if red is None else float(np.count_nonzero(_sample_gain(red, n, fs) < 10 ** (-1 / 20))) / fs * 1000
    info.update(limited=red is not None, gain_db=round(gain, 2), gain_reduction_max_db=round(gmax, 2),
                ms_over_1db=round(over, 1), lufs=round(L_out, 2), true_peak=round(tp_out, 2),
                iterations=attempt + 1, steps_s=st)
    if gmax > 6:
        info["warning"] = f"{gmax:.1f} dB of limiting: audible on peaks; consider a lower target"
    if tp_out > float(max_true_peak):
        info["warning"] = f"true peak {tp_out:.2f} dBTP above the {float(max_true_peak):g} ceiling"
    if reach is not None and L_out < target - LUFS_TOL:
        info.update(max_lufs=reach["max_lufs"], unreachable=reach["why"],
                    below_target_db=round(target - L_out, 2),
                    note=_reach_note(reach, target, float(max_true_peak), L_out))
    return y, info


# ----------------------------------------------------------------------------- render progress
def _speed_path():
    return os.path.join(os.path.dirname(H["STATE"]), "render_speed.json")


def _speed():
    try:
        with open(_speed_path(), encoding="utf-8") as f:
            d = json.load(f)
        if float(d.get("ratio", 0)) > 0:
            return d
    except (OSError, ValueError, TypeError):
        pass
    return {"ratio": FIRST_RATIO, "renders": 0}


def _learn_speed(render_s, song_s):
    """Exponential average of render seconds per song second on this PC (fl-headless's own state folder)."""
    if not song_s or song_s < 5 or render_s <= 0:
        return None
    d = _speed(); obs = render_s / song_s; nr = int(d.get("renders", 0))
    ratio = obs if nr == 0 else d["ratio"] + (0.5 if nr < 3 else 0.3) * (obs - d["ratio"])
    d = {"ratio": round(ratio, 4), "renders": nr + 1, "last": round(obs, 4), "updated": time.strftime("%Y-%m-%d %H:%M")}
    try:
        p = _speed_path(); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(p + ".tmp", p)
    except OSError:
        return None
    return d


def song_seconds(project):
    """Playback length of the project's playlist in seconds (tempo map applied), or None."""
    try:
        d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
        notes, tempos = H["_song"](d, ev)
        end = 0
        for e in ev:
            if e["id"] == H["EV_PLAYLIST"] and e["len"] % H["CLIP_RECORD"] == 0:
                p = H["_payload"](d, e)
                for i in range(0, len(p), H["CLIP_RECORD"]):
                    end = max(end, int.from_bytes(p[i:i + 4], "little") + int.from_bytes(p[i + 8:i + 12], "little"))
        if not end and notes:
            end = max(nt[0] + nt[1] for nt in notes)
        if not end:
            return None
        secs = 0.0; tm = sorted(tempos) or [(0, 120.0)]
        for i, (tk, bpm) in enumerate(tm):
            if tk >= end:
                break
            nxt = min(end, tm[i + 1][0]) if i + 1 < len(tm) else end
            secs += (nxt - tk) / P * 60.0 / max(float(bpm), 1.0)
        return round(secs, 2) if secs > 0 else None
    except Exception:                                            # noqa: BLE001 - an estimate only
        return None


def _wav_seconds(path):
    """Seconds of audio in a WAV that is still being written (header rate/channels/bits, file size), or None."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            h = f.read(4096)
        if h[:4] != b"RIFF" or h[8:12] != b"WAVE":
            return None
        i = 12; bps = None
        while i + 8 <= len(h):
            cid = h[i:i + 4]; ln = int.from_bytes(h[i + 4:i + 8], "little")
            if cid == b"fmt ":
                chn = int.from_bytes(h[i + 10:i + 12], "little"); rate = int.from_bytes(h[i + 12:i + 16], "little")
                bits = int.from_bytes(h[i + 22:i + 24], "little")
                bps = rate * chn * max(1, bits // 8)
            elif cid == b"data":
                return (size - (i + 8)) / bps if bps else None
            i += 8 + ln + (ln & 1)
    except OSError:
        pass
    return None


class _RenderWatch:
    """Finds the render's temp folder (<temp>/flh_*/out/*.wav) and reads how far FL has written the WAV."""
    def __init__(self, exclude):
        self.root = tempfile.gettempdir(); self.exclude = {os.path.normcase(p) for p in exclude}
        self.before = self._dirs(); self.wav = None

    def _dirs(self):
        try:
            return {e.path for e in os.scandir(self.root) if e.name.startswith("flh_") and
                    not e.name.startswith("flh_final_") and e.is_dir()}
        except OSError:
            return set()

    def seconds(self):
        if self.wav is None:
            for d in sorted(self._dirs() - self.before):
                out = os.path.join(d, "out")
                if os.path.normcase(d) in self.exclude or not os.path.isdir(out):
                    continue
                wavs = [f for f in os.listdir(out) if f.lower().endswith(".wav")]
                if wavs:
                    self.wav = os.path.join(out, wavs[0]); break
        return _wav_seconds(self.wav) if self.wav else None


def _render_with_progress(pl, sub, timeout_s, report, method="cli"):
    """H['_render'] (method cli) or H['_render_any'] (auto / in_fl) in a worker thread; report(elapsed_s, rendered_s
    or None) every ~2 s from this (job) thread."""
    box = {}
    jl = H.get("_JOB_LOCAL"); job = (getattr(jl, "job", None), getattr(jl, "t0", None)) if jl is not None else None

    def work():
        if job and job[0]:
            jl.job, jl.t0 = job                                   # so progress from inside _render still shows
        try:
            if H.get("_render_cached"):                            # an unchanged song: the last render, no export
                box["r"] = H["_render_cached"](pl["project"], sub, "wav", timeout_s, out_stem="render", method=method,
                                               close_fl=method == "cli", store="exact", strict_file=True)
            elif method == "cli":
                box["r"] = H["_render"](pl["project"], sub, "wav", timeout_s, out_stem="render")
            else:
                box["r"] = H["_render_any"](pl["project"], sub, "wav", timeout_s, out_stem="render", method=method)
        except BaseException as e:                              # noqa: BLE001 - re-raised below
            box["e"] = e
    # the render runs in THIS thread (it may hold FL's lock: a worker thread would find FL 'busy' - PC 2026-10-20,
    # in-FL renders inside fl_finalize jobs); progress is reported from a small side thread instead
    watch = _RenderWatch([sub]); t0 = time.time(); done = threading.Event()

    def progress():
        if job and job[0]:
            jl.job, jl.t0 = job
        while not done.wait(2.0):
            try:
                report(time.time() - t0, watch.seconds())
            except Exception:                                    # noqa: BLE001 - progress must never break a render
                pass
    th = threading.Thread(target=progress, daemon=True); th.start()
    try:
        work()
    finally:
        done.set()
    if "e" in box:
        raise box["e"]
    return box["r"], time.time() - t0


# ----------------------------------------------------------------------------- tools
def audio_limit(input, output, target_lufs, max_true_peak=-1.0, bits=24):
    """True-peak limiter: WAV in -> WAV out at target_lufs, true peak <= max_true_peak - 0.1 dB."""
    t0 = time.time()
    H["writable"](output)
    import fl_audio_edit as AE
    AE.not_in_place(output, [input])
    x, fs, _ = H["_wav_load"](input)
    t = time.time()
    y, info = limit(x, fs, target_lufs, max_true_peak)
    info["steps_s"]["load"] = round(t - t0, 2)
    t = time.time()
    AE.save(output, y, fs, int(bits))
    info["steps_s"]["write"] = round(time.time() - t, 2)
    info.update(output=output, seconds=round(len(x) / fs, 2), total_s=round(time.time() - t0, 2))
    return info


_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def _clean(name):
    s = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in str(name)).strip().rstrip(". ") or "song"
    return s + "_" if s.split(".")[0].upper() in _RESERVED else s


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def _target(prof, role):
    """Loudness/format target: neutral defaults < profile finish < profile finish_by_role[role]."""
    t = dict(NEUTRAL); t["fade_s"] = "auto"
    t.update({k: v for k, v in (prof.get("finish") or {}).items() if v is not None})
    t.update({k: v for k, v in ((prof.get("finish_by_role") or {}).get(role or "") or {}).items() if v is not None})
    return t


def _plan(project, song, role, target_lufs, prof, true_peak=None, bits=None, fade_s=None, finals=None, rules=None):
    project = os.path.abspath(project)
    if not os.path.isfile(project):
        raise FileNotFoundError(project)
    stem = os.path.splitext(os.path.basename(project))[0]
    j = flh_store.journal_get(project)
    songs = prof.get("songs") or {}
    mapped = next((k for k, v in songs.items() if isinstance(v, dict) and v.get("project")
                   and _norm(v["project"]) == _norm(project)), None)
    song = song or (j.get("song") if j.get("song") != stem else None) or mapped or stem
    role = role or j.get("role") or (songs.get(song) or {}).get("role") or (songs.get(mapped) or {}).get("role")
    tgt = _target(prof, role)
    for k, v in (("lufs", target_lufs), ("true_peak", true_peak), ("bits", bits), ("fade_s", fade_s)):
        if v is not None:
            tgt[k] = v
    tgt["fade_explicit"] = fade_s is not None and fade_s != "auto"   # a number given in the call: exact, linear
    tgt["lufs"] = float(tgt["lufs"]); tgt["true_peak"] = float(tgt["true_peak"]); tgt["bits"] = int(tgt["bits"])
    if tgt["bits"] not in (16, 24):
        raise ValueError("bits must be 16 or 24")
    if tgt["fade_s"] not in (None, "auto"):
        tgt["fade_s"] = float(tgt["fade_s"])
    name = (prof.get("final_name") or "{song} - FINAL.flac").replace("{song}", _clean(song))
    if not name.lower().endswith(".flac"):
        name += ".flac"
    root = prof.get("final_root")
    sub = str(prof.get("final_subfolder", "{song}")).replace("{song}", _clean(song)) if root else ""
    folder = (os.path.join(root, _clean(sub)) if sub else root) if root else os.path.dirname(project)
    final = os.path.join(folder, _clean(name))
    H["writable"](final)
    vers = None
    vlist = finals if finals is not None else prof.get("finals")
    if vlist:                                                      # several finals from one render (e.g. mod + YouTube)
        vers = []; seen = set()
        for v in vlist:
            if not isinstance(v, dict) or not v.get("name"):
                raise ValueError("finals: [{name, lufs, true_peak?, bits?, fade_s?}, ...]")
            vt = dict(tgt)
            for k_in, k in (("lufs", "lufs"), ("target_lufs", "lufs"), ("true_peak", "true_peak"), ("bits", "bits"),
                            ("fade_s", "fade_s")):
                if v.get(k_in) is not None:
                    vt[k] = v[k_in]
            if v.get("fade_s") is not None:                        # a version's own fade: exact when the call gave it
                vt["fade_explicit"] = finals is not None and v["fade_s"] != "auto"
                if vt["fade_s"] != "auto":
                    vt["fade_s"] = float(vt["fade_s"])
            vt["lufs"] = float(vt["lufs"]); vt["true_peak"] = float(vt["true_peak"]); vt["bits"] = int(vt["bits"])
            fmt = str(v.get("format") or "flac").lower().lstrip(".")
            if fmt not in ("flac", "wav"):
                raise ValueError("finals: format is flac (default) or wav (e.g. for a game's audio converter)")
            vname = (prof.get("final_name") or "{song} - FINAL ({version}).flac")
            if "{version}" not in vname:
                vname = os.path.splitext(vname)[0] + " ({version}).flac"
            vname = vname.replace("{song}", _clean(song)).replace("{version}", _clean(str(v["name"])))
            if vname.lower().endswith((".flac", ".wav")):
                vname = os.path.splitext(vname)[0]
            vname += "." + fmt
            vf = os.path.join(folder, _clean(vname))
            if _norm(vf) in seen:
                raise ValueError("two finals map to the same file: give them different names")
            seen.add(_norm(vf)); H["writable"](vf)
            vers.append({"name": str(v["name"]), "target": vt, "final": vf, "format": fmt})
        final = vers[0]["final"]
    rl = rules if rules is not None else prof.get("rules")
    if rl:
        _rules(rl)                                                 # a typo in the rules stops here, before rendering
    if rl and (song in (rl.get("exempt") or []) or ((songs.get(song) or {}).get("rules_exempt"))):
        rl = None                                                  # e.g. a main-menu piece with no fade rules
    return {"project": project, "song": song, "role": role, "target": tgt, "fade_s": tgt["fade_s"], "final": final,
            "versions": vers, "rules": rl,
            "no_final_root": not root, "no_profile": not prof, "song_s": song_seconds(project),
            "reach_warning": _reach_warning(j.get("final"), tgt["lufs"]),
            # the journal's master tone (flp_master_tone) is a Parametric EQ 2 in the project: the render has it
            # when it's still there - said in the result when it isn't
            "tone": H["master_tone_check"](project) if H.get("master_tone_check") and j.get("master_tone") else None}


def _reach_warning(prev, target):
    """Up-front warning from the song's last final (journal): its measured loudness ceiling or heavy limiting."""
    if not isinstance(prev, dict):
        return None
    try:
        mx = prev.get("max_lufs"); lu = prev.get("lufs"); lim = prev.get("limited_db_max")
        if mx is not None and float(target) > float(mx) + 0.3:
            return (f"the last final of this song topped out at about {float(mx):.1f} LUFS; {float(target):g} is "
                    f"probably unreachable without mix changes (the finish will stop at what the mix allows)")
        if lu is not None and lim is not None and float(lim) >= 8 and float(target) > float(lu) + 0.5:
            return (f"the last final ({float(lu):.1f} LUFS) already needed {float(lim):.1f} dB of limiting; "
                    f"{float(target):g} LUFS may be unreachable or sound squashed")
    except (TypeError, ValueError):
        return None
    return None


def _drop_own(path):
    """Remove a temp file fl-headless itself made next to a final ('<final>.new.flac' / '.new.wav')."""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _keep_render(wav, pl):
    """Move the rendered WAV next to the final (old copy recycled) -> its path."""
    kw = os.path.splitext(pl["final"])[0] + ".wav"; H["writable"](kw)
    os.makedirs(os.path.dirname(kw), exist_ok=True)
    if os.path.exists(kw):
        H["files_recycle"]([kw])
    shutil.move(wav, kw)
    return kw


PROFILE_HINT = ("no collection profile is active, so the final went next to the project at neutral defaults "
                "(-14 LUFS, -1 dBTP, 24-bit, fade on the decay only). To set house rules once for a set of songs: "
                "flh_profile(name='My Album', set={'final_root': 'D:/Music/My Album', 'final_name': "
                "'{song} - FINAL.flac', 'finish': {'lufs': -14, 'true_peak': -1, 'bits': 24}})")


def _finish_est(song_s):
    return 2.0 + 0.012 * (song_s or 240)


def _ending_note(pl, fin):
    """A note when the file holds the final chord much longer (journal 'ending_hold', written by flp_extend_ending)
    than the FINAL's sound lasts after its last attack: struck / plucked notes (piano, harp, guitar...) ring out by
    themselves, however long their keys are held, and the FINAL ends where the sound ends (its trim). None otherwise."""
    try:
        eh = (flh_store.journal_get(pl["project"]).get("ending_hold") or {})
        want = float(eh.get("main_s") or 0)
        la = (fin.get("fade") or {}).get("last_attack_s")
        if not want or la is None:
            return None
        tail = float(fin["end_s"]) - float(fin["trim_start_s"]) - float(la)
    except Exception:                                             # noqa: BLE001 - a note only
        return None
    if tail >= want - max(3.0, 0.25 * want):
        return None
    return (f"the file holds the final chord {want:g} s from its attack, but the FINAL's sound ends {tail:.1f} s after "
            "it: the held notes died away by themselves (a struck / plucked sound such as piano fades however long "
            "its keys are held; the FINAL is trimmed where the audio falls silent). For a longer ending, let a "
            "sustained part hold (e.g. flp_finish_piece layers=['strings'] or a pad), or slow the last bars (ritard)")


LENGTH_NOTE_S = 5.0                # the FINAL ends this much (or 5 %) before the song's end: said (the sound died away)


def _length_check(pl, r):
    """The render's length against the project's song through its tempo map (H['_render_length']): {render_s,
    song_s, song_bars, short_s?, render_ends_bar?} or None (no WAV / no model)."""
    f = H.get("_render_length")
    wav = (r.get("rendered") or [None])[0] if isinstance(r, dict) else None
    if not callable(f) or not wav:
        return None
    try:
        return f(pl["project"], wav)
    except Exception:                                             # noqa: BLE001 - no check, no harm
        return None


def _drop_wav(r):
    for w in (r.get("rendered") or []) if isinstance(r, dict) else []:
        try:
            os.remove(w)                                          # our own temp folder's render
        except OSError:
            pass


def _mismatch_msg(pl, lc, twice, why_not=None):
    p = pl["project"]
    where = f" (it ends near bar {lc['render_ends_bar']:g} of {lc['song_bars'] + 1:g})" if lc.get("render_ends_bar") else ""
    msg = (f"RenderMismatch: the render is {lc['render_s']:g} s{where}, but {os.path.basename(p)} on disk runs "
           f"{lc['song_s']:g} s through its tempo map (its last clip / note), so no FINAL was made - a FINAL "
           "without the song's end is worse than none. ")
    if twice:
        msg += ("It was rendered twice (the second time after FL loaded the file itself), so FL's export stops before "
                "the song's end: look in FL for a playlist time selection or a loop / stop marker before the end, or "
                "clips muted at the end (then the length model is off); ")
    elif why_not:
        msg += f"Not rendered again: {why_not}; "
    return msg + ("after listening, fl_finalize(..., length_check=false) makes the FINAL anyway. A same-title copy "
                  "can't be told apart by FL's window: give each version its own title (flp_save_version / "
                  "flp_project_info title=...)")


def _length_gate(pl, r, sub, timeout_s, report, method):
    """Every render is measured against the file's song length BEFORE it becomes a FINAL (PC 2026-10-23: a FINAL
    without the 20 s just added to the ending). A render more than ~1.5 s shorter than the song is another state
    of the song (FL held an older copy or a same-title twin, or a stale cached render) or FL stopped early:
    rendered ONCE more - after FL loaded the file itself (only when FL has no unsaved changes: nothing of FL's is
    lost) and without the render cache - else refused with the reason. -> (render result, check, error or None)."""
    lc = _length_check(pl, r)
    pl["length_check"] = lc
    if not lc or not lc.get("short_s") or pl.get("length_check_off"):
        return r, lc, None
    first = dict(lc)
    route = r.get("method") or method
    _drop_wav(r)
    why_not = None
    if route == "cli" and not r.get("reused"):
        why_not = "FL's command-line render read the file itself (another render would be the same)"
    elif route == "in_fl" and H.get("fl_running") and H["fl_running"]():
        flag = H["_fl_changed_flag"]() if H.get("_fl_changed_flag") else None
        if flag != 0:
            why_not = ("FL has unsaved changes (or can't say), and loading the file would throw them away: save in FL "
                       "or fl_open the project, then fl_finalize again")
        else:                                                     # FL's copy == what it loaded: reloading loses nothing
            try:
                H["_fl_load_fresh"](pl["project"])
            except Exception as e:                                # noqa: BLE001
                why_not = f"FL couldn't be made to load the file ({type(e).__name__}: {e})"
    if why_not:
        return r, first, _mismatch_msg(pl, first, False, why_not)
    try:
        RC = H["_rc"]() if H.get("_rc") else None
        ctx = RC.off() if RC is not None and hasattr(RC, "off") else None
        if ctx is not None:
            with ctx:
                r2, _took = _render_with_progress(pl, sub, timeout_s, report, method)
        else:
            r2, _took = _render_with_progress(pl, sub, timeout_s, report, method)
    except Exception as e:                                        # noqa: BLE001
        return r, first, _mismatch_msg(pl, first, False, f"the second render failed ({type(e).__name__}: {e})")
    lc2 = _length_check(pl, r2)
    if lc2 and lc2.get("short_s"):
        _drop_wav(r2)
        return r2, lc2, _mismatch_msg(pl, lc2, True)
    pl["length_check"] = dict(lc2 or {}, first_render_s=first["render_s"], re_rendered=(
        f"the first render was {first['render_s']:g} s for a {first['song_s']:g} s song (FL held another state of "
        "it, or a stale render): rendered again after FL loaded the file"))
    return r2, pl["length_check"], None


def _length_view(pl, fin):
    """The final item's length check: render / song / FINAL seconds; a note when the FINAL's sound ends well before
    the song (the render covered the whole song, the audio itself fell silent - decaying notes, empty bars)."""
    lc = pl.get("length_check")
    if not lc:
        return None, None
    out = {k: lc[k] for k in ("render_s", "song_s", "first_render_s", "re_rendered") if lc.get(k) is not None}
    note = None
    try:
        fin_s = float(fin["end_s"]) - float(fin["trim_start_s"])
        out["final_s"] = round(fin_s, 2)
        gap = float(lc["song_s"]) - float(fin["end_s"])
        if gap > max(LENGTH_NOTE_S, 0.05 * float(lc["song_s"])):
            note = (f"the render covered the whole song ({lc['render_s']:g} s), but the sound fell silent {gap:.1f} s "
                    f"before its end ({lc['song_s']:g} s through the tempo map), so the FINAL is {fin_s:.1f} s: held "
                    "struck / plucked notes (piano, harp...) decay by themselves and empty bars are trimmed. For a "
                    "longer ending let a sustained part hold, or slow the last bars")
    except (KeyError, TypeError, ValueError):
        pass
    return out, note


def _finalize(plans, reopen, keep_wav, limiter, timeout_s, method="cli"):
    """Two phases. FL phase (holds the FL lock): close FL once (command-line route), render every song, reopen FL
    once. File phase (FL free again: other FL tools work meanwhile): limit + finish + verify + file each render."""
    t0 = time.time()
    n_exports0 = H["_renders_here"]() if H.get("_renders_here") else 0
    steps = {"close_fl": 0.0, "render": {}, "finish": {}, "file": 0.0, "reopen": 0.0}
    finals, notes = [], []
    rendered = []                                                  # (plan, wav or None, error or None) per song
    work = tempfile.mkdtemp(prefix="flh_final_")
    H["_TEMP_ROOTS"].append(os.path.normcase(os.path.abspath(work)))
    speed = _speed()
    n = len(plans)
    R_SHARE = 85.0                                                 # % of the progress bar for the FL phase
    try:
        with C.fl_lock(H):
            _progress(step="close_fl", pct=0)
            t = time.time(); closed = None
            if method != "cli" and "_render_method" in H and \
                    H["_render_method"](plans[0]["project"], "wav", method) != "in_fl":
                method = "cli"
            was_open = H["_open_project_guess"]()[0] if method != "cli" else None   # FL stays open: reopen this
            peek = H.get("_render_cache_peek")
            cached = bool(peek) and all(peek(p["project"], "wav", method, strict_file=True) for p in plans)
            if cached:
                notes.append("every song is unchanged since its last render: reused (FL wasn't closed)")
            try:
                if H["fl_running"]() and method == "cli" and not cached:
                    closed = H["fl_close"](save=True)
                steps["close_fl"] = round(time.time() - t, 2)
                for i, pl in enumerate(plans):
                    song = pl["song"]
                    base = R_SHARE * i / n; share = R_SHARE / n
                    later = sum(speed["ratio"] * (p["song_s"] or 240) for p in plans[i + 1:]) + \
                        sum(_finish_est(p["song_s"]) for p in plans)
                    try:
                        est = speed["ratio"] * pl["song_s"] if pl["song_s"] else None
                        rough = speed.get("renders", 0) == 0
                        _progress(step="render", song=song, pct=round(base, 1), render_pct=0, song_s=pl["song_s"],
                                  eta_s=round(est + later) if est else None,
                                  estimate="first render on this PC: rough (+-50%)" if rough else
                                  f"learned from {speed['renders']} render(s) on this PC")

                        def report(el, rendered_s, pl=pl, est=est, base=base, share=share, later=later, rough=rough,
                                   song=song):
                            if rendered_s is not None and pl["song_s"]:
                                frac = min(0.99, rendered_s / pl["song_s"])
                                left = el / max(frac, 1e-3) * (1 - frac) if frac > 0.02 else (est or 0) - el
                                src = "rendered audio"
                            elif est:
                                frac = min(0.97, el / est); left = max(est - el, 2.0); src = "estimate"
                            else:
                                frac = None; left = None; src = "unknown song length"
                            f = {"step": "render", "song": song, "elapsed_s": round(el)}
                            if frac is not None:
                                f.update(pct=round(base + share * frac, 1), render_pct=round(100 * frac),
                                         eta_s=round(left + later), render_eta_s=round(left))
                            if rendered_s is not None:
                                f["rendered_s"] = round(rendered_s, 1)
                            f["source"] = src
                            if rough and src == "estimate":
                                f["estimate"] = "first render on this PC: rough (+-50%)"
                            _progress(**f)
                        sub = os.path.join(work, f"{i:02d}"); os.makedirs(sub)
                        r, took = _render_with_progress(pl, sub, timeout_s, report, method)
                        if r.get("closed_fl") and not closed:          # FL's own export didn't start: FL was closed
                            closed = r["closed_fl"]; method = "cli"
                        steps["render"][song] = round(took, 2)
                        if isinstance(r.get("steps_s"), dict):              # inside the render (save, load, export)
                            steps.setdefault("render_steps", {})[song] = r["steps_s"]
                        if r.get("cache_note"):                         # e.g. the file changed since a kept render
                            notes.append(f"{song}: {r['cache_note']}")
                        if r.get("reused"):                             # no export: nothing to learn about speed
                            steps.setdefault("reused", []).append(song)
                            notes.append(f"{song}: {r['reused']}")
                        else:
                            learned = _learn_speed(took, pl["song_s"])
                            if learned:
                                speed = learned
                        r, _lc, bad = _length_gate(pl, r, sub, timeout_s, report, method)
                        if bad:                                         # not the file's whole song: no FINAL
                            rendered.append((pl, None, bad))
                            continue
                        rendered.append((pl, r["rendered"][0], None))
                    except Exception as e:
                        rendered.append((pl, None, f"{type(e).__name__}: {e}"))
            finally:
                _progress(step="reopen", pct=R_SHARE, eta_s=round(sum(_finish_est(p["song_s"]) for p in plans) + 1))
                t = time.time()
                if closed and reopen and closed.get("project") and os.path.isfile(closed["project"]):
                    try:
                        H["fl_open"](closed["project"])
                    except Exception as e:
                        notes.append(f"reopen failed: {e}")
                elif was_open and reopen and not closed:           # rendered in the open FL: back to the user's song
                    try:
                        cur = H["_open_project_guess"]()[0]
                        if not cur or _norm(cur) != _norm(was_open):
                            H["fl_open"](was_open)
                    except Exception as e:
                        notes.append(f"reopen failed: {e}")
                steps["reopen"] = round(time.time() - t, 2)
        # ---- file phase: FL is free again (no FL lock held from here on)
        for i, (pl, wav, err) in enumerate(rendered):
            song = pl["song"]
            if err:
                finals.append({"song": song, "error": err})
                continue
            base = R_SHARE + (99 - R_SHARE) * i / n; share = (99 - R_SHARE) / n
            later = sum(_finish_est(p["song_s"]) for p, w, e in rendered[i + 1:] if w)
            versions = pl.get("versions") or [{"name": None, "target": pl["target"], "final": pl["final"]}]
            x = None; fs = None; t_load = 0.0; checked = None; render_note = None
            for vi, ver in enumerate(versions):
                tgt = ver["target"]; final_path = ver["final"]; vlabel = ver["name"]
                last = vi == len(versions) - 1
                try:
                    _progress(step="finish", song=song, version=vlabel, pct=round(base, 1),
                              eta_s=round(_finish_est(pl["song_s"]) * (len(versions) - vi) + later))
                    t = time.time()
                    if x is None:
                        x, fs, rbits = load_wav(wav)
                        t_load = time.time() - t
                        render_note = None                          # the render itself limits the final
                        if rbits and int(rbits) < 24:
                            render_note = (f"the render was {rbits}-bit (FL's export settings): set 24-bit or 32-bit "
                                           "float in FL's export window once for cleaner finals")
                        if fs and int(fs) < 44100:
                            render_note = (render_note + "; " if render_note else "") + \
                                f"the render was {fs} Hz: 44.1 kHz or more is the norm for finals"
                    os.makedirs(os.path.dirname(final_path), exist_ok=True)
                    tmp_flac = os.path.splitext(final_path)[0] + ".new.flac"
                    H["writable"](tmp_flac)
                    try:
                        fin = finish(x, fs, tmp_flac, tgt["lufs"], tgt["true_peak"], fade_s=tgt.get("fade_s", pl["fade_s"]),
                                     bits=tgt["bits"], limiter=limiter, hold_fade=not tgt.get("fade_explicit"),
                                     rules=pl.get("rules") if vi == 0 else None,
                                     ending_attack_s=_notes_attack(pl))
                    except Exception:
                        _drop_own(tmp_flac)                         # our own half-made file: removed (never the bin)
                        raise
                    finally:
                        if last:
                            x = None
                    fin["steps_s"]["load"] = round(t_load, 2)
                    if vi == 0 and fin.get("rules") is not None:      # the FINAL against the collection's rules
                        checked = fin.pop("rules")
                        fx = rule_fixes(checked, pl["project"], fin.get("fade"), pl.get("rules"),
                                        explicit_fade=tgt.get("fade_explicit"))
                        if fx:
                            checked["failed"] = [c["rule"] for c in checked.get("checks", []) if c.get("pass") is False]
                            checked["fixes"] = fx
                    steps["finish"][song + (f" ({vlabel})" if vlabel else "")] = round(time.time() - t, 2)
                    _progress(step="file", song=song, pct=round(base + share * 0.9, 1), eta_s=round(later))
                    t = time.time()
                    if os.path.exists(final_path):                 # the new one is written: now swap
                        H["files_recycle"]([final_path])
                    if ver.get("format") == "wav":                  # a WAV final (a game's converter takes WAV)
                        import fl_audio_edit as AE
                        y_, r_, _b = flac_load_any(tmp_flac)
                        tmp_wav = os.path.splitext(final_path)[0] + ".new.wav"; H["writable"](tmp_wav)
                        AE.save(tmp_wav, y_, r_, bits=int(tgt["bits"]) if int(tgt["bits"]) in (16, 24) else 24)
                        del y_
                        os.remove(tmp_flac); tmp_flac = tmp_wav
                    os.replace(tmp_flac, final_path)
                    kept = None
                    if last and (keep_wav or fin.get("max_lufs") is not None):   # unreachable: keep it to re-finish
                        kept = _keep_render(wav, dict(pl, final=final_path)); wav = None
                    rec = {"path": final_path, "lufs": fin["lufs"], "true_peak": fin["true_peak_dbfs"],
                           "trim_start_s": fin.get("trim_start_s"), "length": fin["length"],
                           "limited_db_max": fin["gain_reduction_max_db"], "max_lufs": fin.get("max_lufs"),
                           "when": time.strftime("%Y-%m-%d %H:%M:%S")}
                    if vlabel:
                        rec["version"] = vlabel
                    try:
                        jset = {"song": song}
                        if vlabel:
                            jset["finals"] = {vlabel: rec}
                            if vi == 0:
                                jset["final"] = rec
                        else:
                            jset["final"] = rec
                        if checked is not None and vi == 0:
                            jset["rules_check"] = checked
                        flh_store.journal_update(pl["project"], set=jset, add={"versions": {
                            "label": "final" + (f" ({vlabel})" if vlabel else ""), "path": final_path,
                            "when": rec["when"], "note": f"{rec['lufs']} LUFS, TP {rec['true_peak']} dBTP"}})
                    except Exception as e:
                        notes.append(f"{song}: journal not updated ({type(e).__name__}: {e})")
                    steps["file"] = round(steps["file"] + time.time() - t, 2)
                    item = {"song": song, "path": final_path, "lufs": fin["lufs"], "true_peak": fin["true_peak_dbfs"],
                            "target": {"lufs": tgt["lufs"], "true_peak": tgt["true_peak"], "bits": tgt["bits"]},
                            "length": fin["length"], "fade_s": fin["fade_s"],
                            **({"fade": fin["fade"]} if fin.get("fade") else {}),
                            "limited_db_max": fin["gain_reduction_max_db"], "finish_steps_s": fin["steps_s"]}
                    if vlabel:
                        item["version"] = vlabel
                    if checked is not None and vi == 0:
                        item["rules"] = checked
                    if render_note and vi == 0:
                        item["render_note"] = render_note
                    lv, lnote = _length_view(pl, fin) if vi == 0 else (None, None)
                    if lv:
                        item["length_check"] = lv
                    en = _ending_note(pl, fin) if vi == 0 else None
                    if en or lnote:
                        item["ending_note"] = en or lnote
                    if fin["limited"]:
                        item["limited_ms_over_1db"] = fin["ms_over_1db"]
                    for k in ("warning", "below_target_db", "above_target_db", "note", "corrections", "max_lufs"):
                        if fin.get(k):
                            item[k] = fin[k]
                    if kept:
                        item["kept_wav"] = kept
                        if fin.get("max_lufs") is not None:
                            item["next"] = ("to finish quieter without re-rendering: " + C.tool_call(H, "audio_limit", {
                                "input": kept, "output": C.Raw("output"), "target_lufs": int(np.floor(fin["lufs"]))}))
                    if pl.get("reach_warning") and vi == 0:
                        item["reach_warning"] = pl["reach_warning"]
                    if pl.get("tone") and vi == 0:                  # master tone EQ: in the render, or why not
                        item["master_tone"] = ("in the render (master EQ, journal master_tone)" if pl["tone"].get("ok")
                                               else pl["tone"].get("note") or "not confirmed in the project")
                    hint = (PROFILE_HINT if pl["no_profile"] else
                            "profile has no final_root: filed next to the project" if pl["no_final_root"] else None)
                    if hint and vi == 0:
                        item["note"] = item["note"] + " | " + hint if item.get("note") else hint
                    finals.append(item)
                except Exception as e:
                    item = {"song": song, "error": f"{type(e).__name__}: {e}"}
                    for t_ in (os.path.splitext(final_path)[0] + ".new.flac", os.path.splitext(final_path)[0] + ".new.wav"):
                        _drop_own(t_)                               # a failure after finish: no half final left
                    if vlabel:
                        item["version"] = vlabel
                    if wav and os.path.isfile(wav):                # the render worked: never throw it away
                        try:
                            kept = _keep_render(wav, dict(pl, final=final_path)); wav = None
                            item["kept_wav"] = kept
                            item["next"] = ("the render is kept; finish it with " + C.tool_call(H, "audio_limit", {
                                "input": kept, "output": C.Raw("output"), "target_lufs": C.Raw("target_lufs")}) +
                                " or fix the cause and run fl_finalize again")
                        except Exception as e2:                    # noqa: BLE001 - reported
                            item["kept_wav_error"] = f"{type(e2).__name__}: {e2}"
                    finals.append(item)
                    x = None
                    break
    finally:
        # fl-headless's own temp folder (the renders left in it: a full-length WAV per song) is removed, as the
        # server removes its own temp folders: in the Recycle Bin it filled the disk with one WAV per final (and on
        # macOS / Linux, where there's no Recycle Bin to use, it stayed in the temp folder for good)
        shutil.rmtree(work, ignore_errors=True)
        if os.path.isdir(work):
            notes.append(f"temp folder not removed: {work}")
        try:
            H["_TEMP_ROOTS"].remove(os.path.normcase(os.path.abspath(work)))
        except ValueError:
            pass
    _progress(step="done", pct=100, eta_s=0)
    res = {"finals": finals, "steps_s": steps, "total_s": round(time.time() - t0, 1), "render_method": method,
           "renders": (H["_renders_here"]() - n_exports0) if H.get("_renders_here") else None,
           "render_speed": {"ratio": speed["ratio"], "renders_measured": speed.get("renders", 0)}}
    failed = [f for f in finals if f.get("error")]
    res["ok"] = not failed
    if failed:
        res["failed"] = len(failed)
        res["error"] = (f"{len(failed)} of {len(finals)} final(s) failed: " +
                        "; ".join(f"{f['song']}: {f['error']}" for f in failed[:3]) +
                        (f" (+{len(failed) - 3} more)" if len(failed) > 3 else ""))
        kept = [f for f in failed if f.get("kept_wav")]
        if kept:
            res["next"] = f"the render of {', '.join(f['song'] for f in kept)} is kept (kept_wav): " + kept[0]["next"]
        else:
            res["next"] = ("fix the cause in the error (fl_status / fl_doctor help), then run fl_finalize again for "
                           "the failed song(s): " + ", ".join(f["song"] for f in failed))
    if notes:
        res["notes"] = notes
    return res


def fl_finalize(project=None, projects=None, song=None, role=None, target_lufs=None, background=True, reopen=True,
                keep_wav=False, limiter=True, timeout_s=1800, true_peak=None, bits=None, fade_s=None, method="auto",
                finals=None, rules=None, length_check=True):
    """Render + limit + finish + file the FINAL.flac of one or several songs (FL closes once, reopens once). Every
    render is measured against the file's song length first (length_check): a render of another state of the song
    is rendered once more, then refused - never a FINAL."""
    items = [x for x in list(projects or []) + ([project] if project else []) if x]
    if not items:
        raise ValueError("give project or projects")
    if len(items) > 1 and (song or role):
        raise ValueError("song/role apply to one project; set them per song in the journal or profile")
    prof = flh_store.profile_get()
    plans = [_plan(p, song, role, target_lufs, prof, true_peak, bits, fade_s, finals, rules) for p in items]
    if isinstance(length_check, str):
        length_check = length_check.strip().lower() not in ("false", "no", "off", "0")
    for p_ in plans:
        p_["length_check_off"] = length_check is False
    allf = [_norm(v["final"]) for p in plans for v in (p.get("versions") or [{"final": p["final"]}])]
    if len(set(allf)) != len(allf):
        raise ValueError("two projects map to the same final file; give them different song names")
    if method not in ("auto", "in_fl", "cli"):
        raise ValueError("method: auto | in_fl | cli")
    args = (plans, reopen, keep_wav, limiter, timeout_s, method)
    warn = [f"{p['song']}: {p['reach_warning']}" for p in plans if p.get("reach_warning")]
    if background:
        r = C.start_job(H, "finalize", _finalize, *args, _fl=False, _own_kind=True)   # holds FL only to render
        r["note"] = ("FL is busy only while it renders; the finishing (limit, FLAC, checks) runs on the files with FL "
                     "free again. " + r.get("note", ""))
        r["songs"] = [p["song"] for p in plans]
        if warn:
            r["warnings"] = warn
        sp = _speed()
        known = [p["song_s"] for p in plans if p["song_s"]]
        if len(known) == len(plans):
            r["eta_s"] = round(sum(sp["ratio"] * s + _finish_est(s) for s in known) + 5)
        return r
    return _finalize(*args)


def register(ns):
    global H
    H = ns
    ns["render_speed"] = _speed                           # learned render seconds per song second (estimates)
    ns["ending_hold_for_rules"] = ending_hold_for_rules   # flp_extend_ending sizes the final chord with it
    ns["simulate_ending"] = simulate_ending               # the ending predicted from notes (extend + fl_ending_check)
    ns["ending_predict"] = ending_predict
    ns["_finalize_limit"] = limit                         # audio_finish limits to its target with it
    S, P = ns["_schema"], ns["_p"]
    STR, NUM, BOOL = ns["_STR"], ns["_NUM"], ns["_BOOL"]
    ns["TOOLS"]["fl_finalize"] = (
        fl_finalize, "Make the FINAL of one or several songs in one call: FL saves and closes ONCE, each project "
        "renders (live pct/eta_s in fl_job), a transparent true-peak limiter lets quiet peaky mixes reach the target, "
        "lead silence/dead tail are trimmed, the ending fades, FLAC is written and verified (loudness within 0.05 LU, "
        "true peak under the ceiling) into the collection profile's <final_root>/<song>/<final_name> (no profile: "
        "next to the project as '<song> - FINAL.flac' at -14 LUFS / -1 dBTP / 24-bit); the old final is recycled only "
        "after the new one is written, FL reopens once, the song journal records it. Targets come from the profile's "
        "finish (and finish_by_role for the song's role). A target the mix can't reach (dense mixes plateau around -10 "
        "LUFS) is not forced: the final stops at what the mix allows, reports max_lufs and keeps the rendered WAV "
        "(kept_wav + next step); a failed finish also keeps the render. Background job: check with fl_job.",
        S(project=P("string", "Path to the .flp", True),
          projects=P("array", "several .flp paths (FL closes and reopens once)", True, items={"type": "string"}),
          song=STR("song name (default: journal, profile songs, project name)", True),
          role=STR("song role for the profile's finish_by_role (default: journal or profile)", True),
          target_lufs=NUM("override the loudness target (default: profile, else -14)", True),
          true_peak=NUM("override the true-peak ceiling dBTP (default: profile, else -1)", True),
          bits=ns["_INT"]("16 (TPDF dither) or 24 (default: profile, else 24)", True),
          fade_s=P(["number", "string"], "fade-out seconds, exact (linear), or 'auto'. Default (profile number or auto): "
                   "the decay is faded, and a final chord that holds flat then stops fades from its attack as a long "
                   "diminuendo", True),
          background=BOOL("run as a job (default true)", True),
          reopen=BOOL("reopen what FL had open (default true)", True),
          keep_wav=BOOL("keep the rendered WAV next to the final (default false: recycled)", True),
          limiter=BOOL("true-peak limit when gain alone can't reach the target (default true)", True),
          timeout_s=ns["_INT"]("render timeout per song (default 1800)", True),
          method=STR("render route: auto (default: FL's own export in the open FL once it worked on this PC, else "
                     "the command line with FL closed once) | in_fl | cli", True),
          finals=P("array", "several finals from one render, e.g. [{name: 'mod', lufs: -20, format: 'wav'}, {name: 'youtube', lufs: "
                   "-14}] -> '<song> - FINAL (<name>).flac' each (default: the profile's finals)", True,
                   items={"type": "object"}),
          rules=P("object", "check the render against rules: min_ending_s, min_tail_s, tail_silence_db, "
                  "max_lead_silence_s, max_trail_silence_s, max_jump_db (default: the profile's rules; a profile "
                  "song with rules_exempt, or rules.exempt: [songs], skips them)", True),
          length_check=BOOL("measure every render against the file's song length (tempo map) and refuse a FINAL "
                            "more than ~1.5 s shorter, after one re-render (default true; false only after listening)",
                            True)))
    ns["TOOLS"]["audio_rule_check"] = (
        audio_rule_check, "Check a WAV/FLAC (a render, a kept_wav or a final) against a collection's rules: ending length, the last "
        "chord's ring, tail dies to true silence (no hum/noise, not cut off), lead/trail silence, a click on the first "
        "note, sudden level jumps. Pass/fail per rule with times.",
        S(input=STR("WAV or FLAC file"), rules=P("object", "thresholds (default: the profile's rules)", True)))
    ns["TOOLS"]["audio_collection_report"] = (
        audio_collection_report, "A collection's finals side by side (the folder and its song folders, '*FINAL*' "
        "files): loudness, true peak, length, tone, width and the rule checks per song, the medians, and which songs "
        "stand out (louder/quieter, brighter/darker, short ending, failed rules).",
        S(folder=STR("collection folder", True), files=P("array", "or these files", True, items={"type": "string"}),
          pattern=STR("file name pattern (default *FINAL*)", True),
          rules=P("object", "rule thresholds (default: the defaults)", True),
          limit_files=ns["_INT"]("at most this many files (default 40)", True)))
    ns.setdefault("TOOL_GROUPS", {})["audio_collection_report"] = "listen_render"
    ns["TOOLS"]["audio_limit"] = (
        audio_limit, "Transparent true-peak limiter (4x oversampled, 5 ms lookahead, linked stereo, 50-150 ms "
        "release): WAV in -> WAV out at target LUFS with true peak <= max_true_peak - 0.1 dB. Reports max gain "
        "reduction and ms limited by more than 1 dB; an unreachable target stops at the most the mix reaches "
        "(max_lufs, below_target_db, note). Output must be a new file (never the input).",
        S(input=STR("WAV file"), output=STR("WAV to write (inside FLH_ROOTS)"),
          target_lufs=NUM("integrated loudness target"),
          max_true_peak=NUM("true-peak ceiling dBTP (default -1.0)", True),
          bits=ns["_INT"]("16 / 24 / 32 (float); default 24", True)))
