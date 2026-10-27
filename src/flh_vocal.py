"""fl-headless vocal tools: set up and record vocals in FL, comp takes, clean them, build a vocal chain, balance the
vocal against the beat, and make doubles / harmonies.

Offline (pure numpy, testable anywhere): fl_vocal_comp, fl_vocal_clean, fl_vocal_double, the measuring half of
fl_vocal_chain and fl_vocal_balance (vocal_wav + beat_wav). Live (FL on Windows, through the bridge): fl_vocal_setup,
fl_vocal_level_check, fl_vocal_record, and the apply=true halves of fl_vocal_chain / fl_vocal_balance.

FL's MIDI scripting API can arm mixer tracks (mixer.armTrack / isTrackArmed), read peak meters (mixer.getTrackPeaks)
and the file a track records into (mixer.getTrackRecordingFileName), but it can NOT pick a mixer track's audio input,
set the playlist time selection (loop / punch range) or load plugins. The FL-side commands this module uses for the
first three ("mixer_info", "mixer_arm", "mixer_peaks", "rec_setup") are in the bridge script since 2.0.0; with an
older script every tool falls back to what the existing commands can do and says so (clear instructions for the
rest). Look-ups go through ns at call time; nothing here imports the main file.

Data: data/vocal/chains.json (vocal chains per style), comp.json (comp scoring), balance.json (genre norms). A file of
the same name in <fl-headless state folder>/vocal/ is merged over the shipped one (user / community overrides).
"""
import glob
import inspect
import json
import math
import os
import re
import time

class _LazyNumpy:
    """numpy loads on first use (keeps server start fast on weak PCs); then the global `np` is numpy itself."""
    def __getattr__(self, name):
        import numpy
        globals()["np"] = numpy
        return getattr(numpy, name)


np = _LazyNumpy()

import flh_common as C

H = {}                                                    # the server namespace (set by register)
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "vocal")
HOP_S = 0.01                                              # analysis grid: 10 ms frames (envelope, f0, activity)
AUDIO_EXT = (".wav", ".flac", ".mp3", ".ogg", ".aif", ".aiff")
VOCAL_COLOR = "#6E2F50"                                   # deep plum: dark shades read better in FL than bright ones

# detection thresholds (fl_vocal_clean / comp); every one can be overridden with amounts={...}
CLEAN_DEFAULTS = {
    "click_k": 4.0,             # click block peak > k x local robust level (median |LPC residual|, max over +-15 ms)
    "click_max_ms": 5.0,        # a click is shorter than this
    "click_ratio": 1.7,         # ... and beats every residual peak 4-30 ms either side by this factor (glottal pulses repeat)
    "plosive_hz": 150.0,        # plosive band: below this
    "plosive_ratio": 0.5,       # low band / total energy in the burst
    "plosive_jump_db": 12.0,    # low band rise vs the 45-145 ms before
    "plosive_max_ms": 150.0,
    "plosive_drop_db": 10.0,    # low band must fall this much within 100 ms after (a sustained low voice isn't a pop)
    "plosive_db": 15.0,         # how much the low band is cut in a pop
    "sib_band_hz": [5000.0, 9000.0],
    "sib_ratio": 0.4,           # sibilance band / total energy
    "sib_target_ratio": 0.3,    # the de-esser pulls the band down until it is at most this share
    "sib_min_ms": 20.0, "sib_max_ms": 500.0,
    "deess_max_db": 8.0,
    "breath_flatness": 0.2,     # spectral flatness 500-5000 Hz (noise-like)
    "breath_below_db": 12.0,    # at least this far under the vocal level
    "breath_min_ms": 100.0, "breath_max_ms": 1000.0,
    "breath_reduce_db": 10.0, "breath_remove_db": 40.0,
    "breath_join_ms": 60.0,     # noise that runs into a vowel within this gap is a consonant onset (/h/, /f/, /sh/)
    "onset_guard_ms": 100.0,    # never cut breaths/noise by more than onset_guard_db this close before a voiced onset
    "onset_guard_db": 3.0,
    "noise_db": 8.0,            # gentle spectral gating depth
    "noise_skip_below_db": -75.0,   # floor already this low: leave it alone
}


# ============================================================================= small helpers
def _h(name, default=None):
    return H.get(name, default)


def _deep_merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _deep_merge(a[k], v)
        else:
            a[k] = v
    return a


_DATA_CACHE = {}


def _data(name):
    """Shipped data/vocal/<name>, with <state folder>/vocal/<name> merged over it (cached by mtime)."""
    paths = [os.path.join(DATA, name)]
    if H.get("STATE"):
        paths.append(os.path.join(os.path.dirname(H["STATE"]), "vocal", name))
    sig = tuple(os.path.getmtime(p) if os.path.isfile(p) else 0 for p in paths)
    hit = _DATA_CACHE.get(name)
    if hit and hit[0] == sig:
        return json.loads(json.dumps(hit[1]))
    out = {}
    for p in paths:
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                _deep_merge(out, json.load(f))
    _DATA_CACHE[name] = (sig, out)
    return json.loads(json.dumps(out))


def _db(p):
    return 10.0 * np.log10(np.maximum(p, 1e-20))


def _r(v, n=1):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), n)


def _mono(x):
    """float64 mono (mean of the channels), built block by block."""
    x = np.asarray(x)
    if x.ndim == 1:
        return np.asarray(x, dtype=np.float64)
    m = np.empty(len(x))
    for a in range(0, len(x), 1 << 20):
        m[a:a + (1 << 20)] = np.asarray(x[a:a + (1 << 20)], dtype=np.float64).mean(axis=1)
    return m


def _2d(x):
    x = np.asarray(x)
    if x.dtype not in (np.float32, np.float64):
        x = x.astype(np.float64)
    return x[:, None] if x.ndim == 1 else x


def _load_wav(path):
    """-> (x float32 (n, ch), sr, bits): float32 holds 24-bit PCM exactly and halves the memory of long takes."""
    import fl_audio_edit as AE
    if not os.path.isfile(str(path)):
        raise FileNotFoundError(str(path))
    x, sr, bits, _ = AE.load_ex(path, np.float32, "raise")
    return x, int(sr), bits


def _save_wav(path, x, sr, bits=24):
    import fl_audio_edit as AE
    H["writable"](path)
    return AE.save(path, _2d(x), sr, bits=bits if bits in (16, 24, 32) else 24, clip=True)


def _out_path(src, suffix, output):
    if output:
        return output
    stem, _ = os.path.splitext(os.path.abspath(src))
    return f"{stem} - {suffix}.wav"


def _ensure_new(src, output):
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(output)):
        raise ValueError("output must be a new file (the original take is never overwritten)")


def _call_flex(fn, **kw):
    """Call another module's function passing only the arguments it accepts (their signatures may evolve)."""
    try:
        sig = inspect.signature(fn)
        if not any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
            kw = {k: v for k, v in kw.items() if k in sig.parameters}
    except (TypeError, ValueError):
        pass
    return fn(**kw)


def _steps():
    t = [time.time()]; st = {}

    def mark(name):
        now = time.time(); st[name] = round(st.get(name, 0) + now - t[0], 3); t[0] = now
    return st, mark


# ============================================================================= DSP core
def _fast_len(n):
    """Next length with only 2/3/5 factors (numpy's FFT is slow on large prime-ish sizes)."""
    best = 1 << int(math.ceil(math.log2(max(n, 2))))
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            q = p35
            while q < n:
                q *= 2
            best = min(best, q)
            p35 *= 3
        p5 *= 5
    return best


def _lowpass_fft(y, sr, hz):
    n = len(y)
    if n == 0:
        return y
    N = _fast_len(n)
    Y = np.fft.rfft(y, N)
    f = np.fft.rfftfreq(N, 1.0 / sr)
    Y[f > hz] = 0
    return np.fft.irfft(Y, N)[:n]


def _band_fft(y, sr, lo, hi):
    """Brick-wall band-pass (for measuring, never for output audio)."""
    n = len(y)
    N = _fast_len(n)
    Y = np.fft.rfft(y, N)
    f = np.fft.rfftfreq(N, 1.0 / sr)
    Y[(f < lo) | (f >= hi)] = 0
    return np.fft.irfft(Y, N)[:n]


def _env_db(m, sr, hop_s=HOP_S):
    """RMS level (dBFS) of 20 ms windows centred every 10 ms."""
    hop = sr * hop_s
    F = int(len(m) / sr / hop_s) + 1
    w = int(round(hop))
    centers = np.round(np.arange(F) * hop).astype(np.int64)
    a = np.clip(centers - w, 0, len(m)); b = np.clip(centers + w, 0, len(m))
    c = _cumsum_at(m, np.concatenate([a, b]))                    # running sum of m^2 only where it is read
    e = (c[F:] - c[:F]) / np.maximum(b - a, 1)
    return _db(e)


def _cumsum_at(m, pos, block=1 << 20):
    """cumsum([0] + m*m) evaluated at the indices pos (0..len(m)), block by block."""
    pos = np.asarray(pos, np.int64)
    order = np.argsort(pos, kind="stable"); ps = pos[order]
    vals = np.zeros(len(pos)); acc = 0.0; j = 0
    while j < len(ps) and ps[j] <= 0:
        j += 1
    for a in range(0, len(m), block):
        seg = np.asarray(m[a:a + block], dtype=np.float64)
        cs = np.cumsum(seg * seg) + acc
        hi = a + len(seg); k = int(np.searchsorted(ps, hi, side="right"))
        if k > j:
            vals[j:k] = cs[ps[j:k] - a - 1]; j = k
        acc = float(cs[-1]) if len(cs) else acc
    vals[j:] = acc
    out = np.empty(len(pos)); out[order] = vals
    return out


def yin(m, sr, fmin=60.0, fmax=1100.0, hop_s=HOP_S, thresh=0.15):
    """YIN f0 tracker (de Cheveigne & Kawahara 2002) on a 10 ms grid: cumulative-mean-normalised difference
    function, absolute threshold, local-minimum descent, parabolic interpolation on the raw difference.
    -> (f0 Hz with NaN where unvoiced, aperiodicity 0..1). Works at ~20-24 kHz internally (anti-aliased)."""
    m = np.asarray(m, dtype=np.float64)
    if len(m):                                                   # DC / slow drift is not a pitch
        N = max(3, int(2 * sr / fmin)) | 1
        c = np.concatenate([[0.0], np.cumsum(m)]); h = N // 2; ii = np.arange(len(m))
        lo, hi = np.clip(ii - h, 0, len(m)), np.clip(ii + h + 1, 0, len(m))
        m = m - (c[hi] - c[lo]) / (hi - lo)
    q = max(1, int(sr // 20000))
    y = _lowpass_fft(m, sr, 0.45 * sr / q)[::q] if q > 1 else m
    fs = sr / q
    W = int(round(fs * 0.025))
    tmin = max(2, int(fs / fmax)); tmax = int(math.ceil(fs / fmin)) + 1
    L = W + tmax + 1
    F = int(len(m) / sr / hop_s) + 1
    pad = L
    yp = np.concatenate([np.zeros(pad), y, np.zeros(2 * pad)])
    starts = np.round(np.arange(F) * hop_s * fs).astype(np.int64) - W // 2 + pad
    nfft = 1 << int(math.ceil(math.log2(L + W)))
    f0 = np.full(F, np.nan); ap = np.ones(F)
    lv = _env_db(m, sr, hop_s)
    if F:                                                        # a take with no silence (one sustained note, a
        p10, p995 = np.percentile(lv, 10), np.percentile(lv, 99.5)   # trimmed phrase) still has a pitch
        gate = max(min(p10 + 10.0, p995 - 20.0), p995 - 50.0, -75.0)
    else:
        gate = 0
    taus = np.arange(tmax + 1)
    for b0 in range(0, F, 1024):
        b1 = min(F, b0 + 1024)
        idx = starts[b0:b1, None] + np.arange(L)
        Fr = yp[idx]
        A = np.fft.rfft(Fr, nfft); Bh = np.fft.rfft(Fr[:, :W], nfft)
        r = np.fft.irfft(A * np.conj(Bh), nfft)[:, :tmax + 1]
        cs = np.concatenate([np.zeros((b1 - b0, 1)), np.cumsum(Fr * Fr, axis=1)], axis=1)
        E0 = cs[:, W][:, None]
        Et = cs[:, taus + W] - cs[:, taus]
        d = np.maximum(E0 + Et - 2 * r, 0.0); d[:, 0] = 0
        cum = np.cumsum(d[:, 1:], axis=1)
        cm = np.ones_like(d)
        cm[:, 1:] = d[:, 1:] * np.arange(1, tmax + 1) / np.maximum(cum, 1e-20)
        seg = cm[:, tmin:tmax]
        below = seg < thresh
        has = below.any(axis=1)
        t = np.where(has, np.argmax(below, axis=1) + tmin, np.argmin(seg, axis=1) + tmin)
        rows = np.arange(b1 - b0)
        for _ in range(tmax):                                  # walk down to the local minimum of the dip
            nxt = np.minimum(t + 1, tmax - 1)
            move = (t + 1 < tmax) & (cm[rows, nxt] < cm[rows, t])
            if not move.any():
                break
            t = t + move
        a_ = d[rows, np.maximum(t - 1, 1)]; b_ = d[rows, t]; c_ = d[rows, np.minimum(t + 1, tmax)]
        den = a_ - 2 * b_ + c_
        off = np.where(den > 1e-20, 0.5 * (a_ - c_) / np.where(den > 1e-20, den, 1), 0.0)
        off = np.clip(off, -0.5, 0.5)
        apb = cm[rows, t]
        ok = has & (apb < thresh * 1.5) & (lv[b0:b1] > gate)
        f0[b0:b1] = np.where(ok, fs / (t + off), np.nan)
        ap[b0:b1] = np.clip(apb, 0, 1)
    return f0, ap


def pitch_track(m, sr):
    """f0 on the 10 ms grid: flh_transcribe's tracker when it is loaded and answers in a usable shape, else YIN.
    -> (f0, aperiodicity or None, source)."""
    F = int(len(m) / sr / HOP_S) + 1
    shared = H.get("pitch_track")
    if shared and shared is not pitch_track:
        try:
            r = _call_flex(shared, x=m, audio=m, y=m, fs=sr, sr=sr, rate=sr, hop_s=HOP_S)
            f, t = None, None
            if isinstance(r, dict):
                f = r.get("f0") if r.get("f0") is not None else r.get("hz")
                t = r.get("times") if r.get("times") is not None else r.get("t")
            elif isinstance(r, (tuple, list)) and len(r) >= 2:
                t, f = r[0], r[1]
            f = np.asarray(f, dtype=np.float64)
            if t is not None:
                t = np.asarray(t, dtype=np.float64)
                k = min(len(t), len(f)); t, f = t[:k], f[:k]
                grid = np.arange(F) * HOP_S
                vo = np.isfinite(f) & (f > 0)
                fi = np.interp(grid, t, np.where(vo, f, 0.0))
                vi = np.interp(grid, t, vo.astype(float)) > 0.5
                f = np.where(vi, fi, np.nan)
            if f.ndim == 1 and abs(len(f) - F) <= 3 and np.isfinite(f).any():
                f = np.pad(f[:F], (0, max(0, F - len(f))), constant_values=np.nan)
                f[f <= 0] = np.nan
                return f, None, "flh_transcribe"
        except Exception:
            pass
    f0, ap = yin(m, sr)
    return f0, ap, "yin"


def _regions(mask, min_gap, min_len):
    """[(a, b)] runs of True frames; gaps shorter than min_gap closed, runs shorter than min_len dropped."""
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return []
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    a = list(np.flatnonzero(d == 1)); b = list(np.flatnonzero(d == -1))
    runs = []
    for s, e in zip(a, b):
        if runs and s - runs[-1][1] < min_gap:
            runs[-1][1] = e
        else:
            runs.append([s, e])
    return [(int(s), int(e)) for s, e in runs if e - s >= min_len]


def _stft_frames(n, sr):
    N = 1 << int(round(math.log2(max(256, sr * 0.023))))
    return N, N // 4


def _features(m, sr):
    """Per STFT frame (~23 ms window, 75 % overlap): band energies, flatness, sibilance centroid."""
    N, Hh = _stft_frames(len(m), sr)
    win = np.sqrt(np.hanning(N + 1)[:N])
    M = int(math.ceil(len(m) / Hh)) + 1
    xp = np.concatenate([np.zeros(N // 2), m, np.zeros(N + Hh * 2)])
    f = np.fft.rfftfreq(N, 1.0 / sr)
    nyq = sr / 2.0
    s_lo, s_hi = CLEAN_DEFAULTS["sib_band_hz"]
    s_hi = min(s_hi, nyq * 0.96)
    bands = {"tot": (f >= 60) & (f < min(16000, nyq)), "low": (f >= 20) & (f < CLEAN_DEFAULTS["plosive_hz"]),
             "sib": (f >= s_lo) & (f < s_hi), "body": (f >= 300) & (f < 3000), "mid14": (f >= 1000) & (f < 4000)}
    flat_b = (f >= 500) & (f < min(5000, nyq))
    out = {k: np.zeros(M) for k in bands}
    out["flat"] = np.zeros(M); out["sibcen"] = np.zeros(M)
    sib_f = f[bands["sib"]]
    off = np.arange(N)
    for b0 in range(0, M, 1024):
        b1 = min(M, b0 + 1024)
        fr = xp[(np.arange(b0, b1) * Hh)[:, None] + off] * win
        S = np.abs(np.fft.rfft(fr, axis=1)) ** 2
        for k, bm in bands.items():
            out[k][b0:b1] = S[:, bm].sum(axis=1)
        Sf = S[:, flat_b] + 1e-20
        out["flat"][b0:b1] = np.exp(np.mean(np.log(Sf), axis=1)) / np.mean(Sf, axis=1)
        Ss = S[:, bands["sib"]]
        out["sibcen"][b0:b1] = (Ss * sib_f).sum(axis=1) / np.maximum(Ss.sum(axis=1), 1e-20) if len(sib_f) else 0
    out["N"], out["H"], out["t"] = N, Hh, np.arange(M) * Hh / sr
    out["ms_scale"] = 4.0 / (N * N)                             # band power sum -> mean square of the band (dBFS RMS)
    return out


def _to_grid(t, n_grid):
    return np.clip(np.round(np.asarray(t) / HOP_S).astype(np.int64), 0, max(0, n_grid - 1))


def _events_from(mask, t, min_s, max_s, gap=2):
    out = []
    for a, b in _regions(mask, gap, 1):
        dur = t[min(b, len(t) - 1)] - t[a] if b > a else 0
        if min_s <= dur + (t[1] - t[0] if len(t) > 1 else 0) <= max_s:
            out.append((a, b))
    return out


# ============================================================================= analysis of one take
def analyze(x, sr, cfg=None, detect=True):
    """Envelope, f0, phrases and (optionally) plosive / sibilance / breath events of one take."""
    cfg = dict(CLEAN_DEFAULTS, **(cfg or {}))
    comp = _data("comp.json")
    m = _mono(x)
    env = _env_db(m, sr)
    f0, ap, src = pitch_track(m, sr)
    F = len(env)
    f0 = np.pad(f0[:F], (0, max(0, F - len(f0))), constant_values=np.nan)
    voiced = np.isfinite(f0)
    floor = float(np.percentile(env, 10)); peak = float(np.percentile(env, 99.5))
    thr = max(floor + 12.0, peak - 30.0)
    active = env > thr
    regs = _regions(active, int(comp.get("min_gap_s", 0.25) / HOP_S), int(0.1 / HOP_S))
    phrases = [(a, b) for a, b in regs if voiced[a:b].mean() >= 0.1 or (b - a) * HOP_S >= 0.6]
    pmask = np.zeros(F, bool)
    for a, b in phrases:
        pmask[a:b] = True
    gap_env = env[~pmask] if (~pmask).any() else env
    A = {"sr": sr, "n": len(m), "m": m, "env": env, "f0": f0, "voiced": voiced, "floor_db": float(np.percentile(gap_env, 20)),
         "peak_db": peak, "thr_db": thr, "phrases": phrases, "pmask": pmask, "pitch_source": src,
         "speech_db": float(np.percentile(env[pmask], 90)) if pmask.any() else peak}
    if detect:
        A["feat"] = _features(m, sr)
        A.update(_detect_events(A, cfg))
    return A


def _detect_events(A, cfg):
    ft = A["feat"]; t = ft["t"]; F = len(A["env"])
    g = _to_grid(t, F)
    tot = ft["tot"] + 1e-20
    sc = ft["ms_scale"]
    totdb = _db(tot * sc); lowdb = _db(ft["low"] * sc); sibdb = _db(ft["sib"] * sc)
    peak_tot = float(np.percentile(totdb, 99.5))
    dt = t[1] - t[0] if len(t) > 1 else 0.005
    # ---- plosives: low-band bursts that jump up, dominate the frame and fall away quickly
    k45, k145, k100 = max(1, int(0.045 / dt)), max(2, int(0.145 / dt)), max(1, int(0.1 / dt))
    pre = np.full(len(t), -200.0)
    for k in range(k45, k145 + 1):
        pre[k:] = np.maximum(pre[k:], lowdb[:-k] if k < len(lowdb) else -200)
    cand = (ft["low"] / tot > cfg["plosive_ratio"]) & (lowdb - pre > cfg["plosive_jump_db"]) & (lowdb > peak_tot - 35)
    plos = []
    f0g = A["f0"][g]
    for a, b in _events_from(cand, t, 0.0, cfg["plosive_max_ms"] / 1000.0, gap=3):
        fz = f0g[a:min(len(t), b + max(1, int(0.03 / dt)))]       # a low sung / staccato note has a steady pitch
        fv = fz[np.isfinite(fz)]                                   # through the "burst"; a pop is a noise thump
        if len(fz) and len(fv) >= 0.6 * len(fz) and len(fv) >= 3 and \
                float(np.std(1200 * np.log2(fv / np.median(fv)))) < 60:
            continue
        k_pk = a + int(np.argmax(lowdb[a:b]))
        z = min(len(t) - 1, b + k100)
        if z > b and lowdb[k_pk] - lowdb[b:z + 1].min() >= cfg["plosive_drop_db"]:
            e = min(len(t), b + int(0.03 / dt))                  # include the burst's decay
            plos.append({"a": a, "b": e, "t": float(t[a]), "dur_ms": (t[min(e, len(t) - 1)] - t[a]) * 1000,
                         "db": float(lowdb[k_pk])})
    # ---- sibilance: the 5-9 kHz band dominating a loud enough frame
    sr_ = ft["sib"] / tot
    cand = (sr_ > cfg["sib_ratio"]) & (sibdb > peak_tot - 30)
    sibs = []
    for a, b in _events_from(cand, t, cfg["sib_min_ms"] / 1000.0, cfg["sib_max_ms"] / 1000.0, gap=2):
        cen = float(np.median(ft["sibcen"][a:b]))
        sibs.append({"a": a, "b": b, "t": float(t[a]), "dur_ms": (t[min(b, len(t) - 1)] - t[a]) * 1000,
                     "hz": cen, "ratio": float(np.max(sr_[a:b])), "db": float(np.max(sibdb[a:b]))})
    sib_frames = np.zeros(len(t), bool)
    for s in sibs:
        sib_frames[s["a"]:s["b"]] = True
    plo_frames = np.zeros(len(t), bool)
    for p in plos:
        plo_frames[p["a"]:p["b"]] = True
    # ---- breaths: noise-like, unvoiced, well under the vocal and above the floor
    voiced_f = A["voiced"][g]
    level_ok = (A["env"][g] < A["speech_db"] - cfg["breath_below_db"]) & (A["env"][g] > A["floor_db"] + 6)
    # near voicing (within 30 ms) doesn't count: vowel tails are not breaths
    vz = np.convolve(voiced_f.astype(float), np.ones(max(1, int(0.06 / dt))), mode="same") > 0
    cand = (ft["flat"] > cfg["breath_flatness"]) & ~vz & level_ok & ~sib_frames & ~plo_frames
    breaths, consonants = [], []
    on = _voiced_onsets(voiced_f)
    guard = float(cfg.get("onset_guard_ms", 100.0)) / 1000.0; join = float(cfg.get("breath_join_ms", 60.0)) / 1000.0
    for a, b in _events_from(cand, t, cfg["breath_min_ms"] / 1000.0, cfg["breath_max_ms"] / 1000.0, gap=6):
        # a breath is followed by a short pause; noise that runs straight into a vowel is the word's first
        # consonant (/h/ in "hello", /f/, /sh/): keep it, or keep its last onset_guard_ms when it is long
        k = int(np.searchsorted(on, b - 1))
        nxt = t[on[k]] if k < len(on) else None
        end_t = t[min(b, len(t) - 1)]
        if nxt is not None and nxt - end_t < join:
            b2 = int(np.searchsorted(t, nxt - guard - join))
            if t[min(b2, len(t) - 1)] - t[a] < cfg["breath_min_ms"] / 1000.0:
                consonants.append(float(t[a])); continue
            b = b2
        breaths.append({"a": a, "b": b, "t": float(t[a]), "dur_ms": (t[min(b, len(t) - 1)] - t[a]) * 1000,
                        "db": float(np.max(A["env"][g[a:b]]))})
    return {"plosives": plos, "sibilance": sibs, "breaths": breaths, "consonant_onsets": consonants,
            "voiced_onsets": on}


def _voiced_onsets(voiced_f):
    """Frame indices where voicing starts (sorted)."""
    v = np.asarray(voiced_f, bool)
    if not len(v):
        return np.zeros(0, np.int64)
    return np.flatnonzero(v & ~np.concatenate(([False], v[:-1])))


# ----------------------------------------------------------------------------- clicks (time domain)
def _lpc_blocks(m, sr, order=16):
    """LPC coefficients (autocorrelation + Levinson, vectorised over 20 ms blocks of a 40 ms Hann window)."""
    B = max(64, int(sr * 0.02)); n = len(m)
    nb = int(math.ceil(n / B))
    W = 2 * B
    xp = np.concatenate([np.zeros(B // 2), m, np.zeros(W + B)])
    nfft = 1 << int(math.ceil(math.log2(2 * W)))
    hw = np.hanning(W); R = np.empty((nb, order + 1))
    for c0 in range(0, nb, 1024):                                # batches: never the whole take's spectra at once
        c1 = min(nb, c0 + 1024)
        fr = xp[(np.arange(c0, c1) * B)[:, None] + np.arange(W)] * hw
        R[c0:c1] = np.fft.irfft(np.abs(np.fft.rfft(fr, nfft, axis=1)) ** 2, nfft, axis=1)[:, :order + 1]
    R[:, 0] = R[:, 0] * 1.0001 + 1e-12
    a = np.zeros((nb, order + 1)); a[:, 0] = 1.0
    E = R[:, 0].copy()
    for i in range(1, order + 1):
        acc = R[:, i] + (a[:, 1:i] * R[:, i - 1:0:-1]).sum(axis=1) if i > 1 else R[:, i].copy()
        k = -acc / np.maximum(E, 1e-20)
        prev = a.copy()
        for j in range(1, i):
            a[:, j] = prev[:, j] + k * prev[:, i - j]
        a[:, i] = k
        E = E * (1 - k * k)
    return a, B


def _lpc_residual(m, sr, order=16):
    a, B = _lpc_blocks(m, sr, order)
    n = len(m)
    mp = np.concatenate([np.zeros(order), m])
    e = np.zeros(n)
    from numpy.lib.stride_tricks import sliding_window_view
    sw = sliding_window_view(mp, order + 1)                      # row i = mp[i .. i+order] = m[i-order .. i]
    for k in range(a.shape[0]):
        s, z = k * B, min(n, (k + 1) * B)
        if s >= n:
            break
        e[s:z] = sw[s:z] @ a[k][::-1]
    return e


def detect_clicks(m, sr, k=4.0, max_ms=5.0, ratio=1.7):
    """Mouth clicks / ticks: isolated transients in the LPC residual (what the voice model can't predict).
    Per 1 ms block: the block's peak must beat k x the local robust level (median |residual| over +-15 ms) AND
    ratio x the largest peak 4-30 ms either side - so the periodic glottal pulses of a sung note (and vocal fry,
    period up to ~30 ms: their neighbours are as big) never count, only isolated ticks. Grouped, shorter than
    max_ms. -> [(start sample, end sample)]."""
    e = _lpc_residual(m, sr); np.abs(e, out=e)
    b = max(8, int(sr * 0.001)); nb = int(math.ceil(len(e) / b))
    bmax = np.empty(nb); bmed = np.empty(nb)
    for r0 in range(0, nb, 65536):                               # per 1 ms block, rows at a time (no padded copy)
        r1 = min(nb, r0 + 65536)
        seg = e[r0 * b:r1 * b]
        if len(seg) < (r1 - r0) * b:
            seg = np.pad(seg, (0, (r1 - r0) * b - len(seg)))
        ep = seg.reshape(r1 - r0, b)
        bmax[r0:r1] = ep.max(axis=1); bmed[r0:r1] = np.median(ep, axis=1)
    near, far = 4, 30
    nmax = np.zeros(nb); lmed = bmed.copy()
    for s in range(near, far + 1):
        nmax[s:] = np.maximum(nmax[s:], bmax[:-s]); nmax[:-s] = np.maximum(nmax[:-s], bmax[s:])
    for s in range(1, far + 1):
        lmed[s:] = np.maximum(lmed[s:], bmed[:-s]); lmed[:-s] = np.maximum(lmed[:-s], bmed[s:])
    floor = max(float(np.median(bmed)) * 0.05, 1e-7)
    cand = (bmax > ratio * nmax) & (bmax > k * np.maximum(lmed, floor))
    out = []
    for a, z in _regions(cand, 2, 1):
        seg = e[a * b:min(len(e), z * b)]
        if not len(seg):
            continue
        idx = np.flatnonzero(seg > 0.25 * seg.max())
        s0, s1 = a * b + int(idx[0]), a * b + int(idx[-1]) + 1
        if s1 - s0 <= sr * max_ms / 1000.0:
            out.append((s0, s1))
    return out


def _ar_fill(col, a, b, order=16):
    """Re-draw col[a:b] from the signal on both sides (forward + backward linear prediction, cross-faded)."""
    ctx = max(order * 8, b - a + order * 4, 512)
    L = b - a
    def coefs(seg):
        seg = seg * np.hanning(len(seg))
        R = np.array([np.dot(seg[:len(seg) - i], seg[i:]) for i in range(order + 1)])
        R[0] = R[0] * 1.0001 + 1e-12
        c = np.zeros(order + 1); c[0] = 1; E = R[0]
        for i in range(1, order + 1):
            acc = R[i] + np.dot(c[1:i], R[i - 1:0:-1])
            kk = -acc / max(E, 1e-20); p = c.copy()
            for j in range(1, i):
                c[j] = p[j] + kk * p[i - j]
            c[i] = kk; E *= (1 - kk * kk)
        return c
    def predict(hist, c, n):
        h = list(hist[-order:]); out = []
        for _ in range(n):
            v = -sum(c[j] * h[-j] for j in range(1, order + 1)); h.append(v); out.append(v)
        return np.array(out)
    pre = col[max(0, a - ctx):a]; post = col[b:b + ctx]
    if len(pre) < order * 2 or len(post) < order * 2:
        return np.linspace(col[max(a - 1, 0)], col[min(b, len(col) - 1)], L)
    fwd = predict(pre, coefs(pre), L)
    bwd = predict(post[::-1], coefs(post), L)[::-1]
    w = 0.5 - 0.5 * np.cos(np.pi * (np.arange(L) + 0.5) / L)
    y = fwd * (1 - w) + bwd * w
    lim = 2.0 * max(np.abs(pre[-order * 4:]).max(), np.abs(post[:order * 4]).max(), 1e-6)
    return np.clip(y, -lim, lim)


# ----------------------------------------------------------------------------- STFT gain pass
def _stft_gain(x, sr, g_all, g_low, g_sib, N, Hh, low_hz, sib_lo, sib_hi):
    """Apply per-frame gains (linear) to the whole spectrum, the band below low_hz and the sibilance band, with
    soft band edges; sqrt-Hann analysis/synthesis, 75 % overlap (identity when every gain is 1). Streams over
    frames (no padded / accumulator copies of the take); the output keeps x's float type."""
    import fl_audio_edit as AE
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    n, ch = x.shape
    win = np.sqrt(np.hanning(N + 1)[:N])
    M = len(g_all)
    f = np.fft.rfftfreq(N, 1.0 / sr)
    w_low = np.clip((low_hz * 1.35 - f) / (low_hz * 0.35), 0, 1)
    w_sib = np.clip(np.minimum((f - sib_lo * 0.85) / (sib_lo * 0.15), (sib_hi * 1.15 - f) / (sib_hi * 0.15)), 0, 1)
    out = np.zeros(x.shape, dtype=x.dtype if x.dtype in (np.float32, np.float64) else np.float64)

    def process(X, m0, m1, e0):
        G = g_all[m0:m1, None] * (1 + (g_low[m0:m1, None] - 1) * w_low) * (1 + (g_sib[m0:m1, None] - 1) * w_sib)
        return X * G
    for c in range(ch):
        AE.stft_stream(x[:, c], N, Hh, win, win, process, out[:, c], block=512, M=M)
    return out


def _ola(y, ws, fr, b0, Hh, win):
    """Overlap-add frames fr (K, N) that start at (b0 + k) * Hh into y, and win^2 into ws - vectorised when the
    hop divides the frame (R slice-adds instead of K per-frame adds)."""
    K, N = fr.shape
    if N % Hh == 0:
        R = N // Hh; fr_ = fr.reshape(K, R, Hh); w2 = win * win
        for j in range(R):
            a = (b0 + j) * Hh
            y[a:a + K * Hh] += fr_[:, j, :].reshape(-1)
            ws[a:a + K * Hh] += np.tile(w2[j * Hh:(j + 1) * Hh], K)
    else:
        for k in range(K):
            a = (b0 + k) * Hh
            y[a:a + N] += fr[k]; ws[a:a + N] += win * win


def _ramp_gain(M, events, gain_db, t, ramp_s=0.015):
    """Per-frame dB curve: gain_db inside the events, ramped in/out over ramp_s."""
    g = np.zeros(M)
    dt = t[1] - t[0] if len(t) > 1 else 0.005
    r = max(1, int(ramp_s / dt))
    for ev in events:
        a, b = ev["a"], ev["b"]
        gd = ev.get("gain_db", gain_db)
        g[a:b] = np.minimum(g[a:b], gd)
        for i in range(1, r + 1):
            v = gd * (1 - i / (r + 1))
            if a - i >= 0:
                g[a - i] = min(g[a - i], v)
            if b - 1 + i < M:
                g[b - 1 + i] = min(g[b - 1 + i], v)
    return g


# ============================================================================= keys, scales, melody targets
def _fl_music():
    import fl_music
    return fl_music


def _key_pcs(key):
    return sorted(_fl_music().scale_set(key))


def _estimate_key(f0s):
    """Krumhansl key from the sung pitches (voiced 10 ms frames weighted equally)."""
    notes = []
    for f0 in f0s:
        v = f0[np.isfinite(f0)]
        if len(v):
            k = np.round(69 + 12 * np.log2(v / 440.0)).astype(int)
            pcs, cnt = np.unique(k % 12, return_counts=True)
            notes += [{"key": int(p) + 60, "len": int(c)} for p, c in zip(pcs, cnt)]
    res = _fl_music().detect_key(notes) if notes else []
    return (res[0][0], res[0][1]) if res else (None, 0.0)


def _project_key(project):
    """Key of the song: flh_score's view, then the journal, then the notes in the project (flp_analyze_music)."""
    if not project:
        return None, None
    sv = H.get("score_view")
    if sv:
        try:
            r = _call_flex(sv, project=project, path=project)
            k = r.get("key") if isinstance(r, dict) else None
            if isinstance(k, (list, tuple)) and k:
                k = k[0][0] if isinstance(k[0], (list, tuple)) else k[0]
            if isinstance(k, dict):
                k = k.get("name") or k.get("key")
            if k:
                _fl_music().parse_key(str(k)); return str(k), "score_view"
        except Exception:
            pass
    try:
        import flh_store
        k = flh_store.journal_get(project).get("key")
        if k:
            _fl_music().parse_key(str(k)); return str(k), "journal"
    except Exception:
        pass
    try:
        r = H["flp_analyze_music"](project)
        k = r.get("key")
        if k:
            return (k[0][0] if isinstance(k[0], (list, tuple)) else str(k[0])), "project notes"
    except Exception:
        pass
    return None, None


def _project_tempo_bpb(project):
    bpm, bpb = None, 4
    if project and os.path.isfile(project):
        try:
            bpm = float(H["_project_tempo"](H["_load"](project)))
        except Exception:
            pass
        try:
            bpb = H["_qbar"](H["flp_project_info"](project))
        except Exception:
            pass
    return bpm, bpb


def _melody_targets(ref, F, bpm=None, start_bar=1.0, bpb=4):
    """Reference melody -> per-frame target MIDI pitch (NaN where no note). ref: .mid path, or
    [{note: 'A4'|69, start_s, end_s}] (or bar/beats: {note, bar, beats} with the song tempo)."""
    tgt = np.full(F, np.nan)
    fm = _fl_music()
    if isinstance(ref, str):
        div, notes, tempos = H["midi_read"](ref)
        tempo = tempos[0][1] if tempos else (bpm or 120.0)
        spq = 60.0 / float(bpm or tempo)
        origin = (float(start_bar) - 1) * bpb * spq
        items = [(s * spq / div - origin, e * spq / div - origin, k) for s, e, k, _ in notes]
    else:
        items = []
        for n in ref or []:
            k = fm.parse_note(n.get("note", n.get("key")))
            if "start_s" in n:
                items.append((float(n["start_s"]), float(n["end_s"]), k))
            else:
                if not bpm:
                    raise ValueError("reference_melody in bars needs the song tempo (bpm or project)")
                spq = 60.0 / float(bpm)
                s = ((float(n["bar"]) - float(start_bar)) * bpb + float(n.get("beat", 0))) * spq
                items.append((s, s + float(n.get("beats", 1)) * spq, k))
    for s, e, k in items:
        a, b = max(0, int(round(s / HOP_S))), min(F, int(round(e / HOP_S)))
        if b > a:
            tgt[a:b] = k
    return tgt


def _cents(f0):
    return 1200.0 * np.log2(np.where(np.isfinite(f0), f0, np.nan) / 440.0) + 6900.0


def _smooth_voiced(c, win=15):
    """Moving average over voiced frames only (removes vibrato so the note centre is judged)."""
    v = np.isfinite(c)
    z = np.where(v, c, 0.0)
    k = np.ones(win)
    num = np.convolve(z, k, mode="same"); den = np.convolve(v.astype(float), k, mode="same")
    out = np.where(v & (den > 0), num / np.maximum(den, 1e-9), np.nan)
    return out


def _pitch_dev(c_s, pcs=None, target=None):
    """Cents from the intended pitch: the reference note (octave-folded) or the nearest scale note."""
    if target is not None:
        d = c_s - target * 100.0
        return (d + 600.0) % 1200.0 - 600.0
    pc = (c_s % 1200.0)
    best = np.full(len(c_s), np.inf)
    for p in pcs:
        d = np.abs(((pc - p * 100.0) + 600.0) % 1200.0 - 600.0)
        best = np.minimum(best, d)
    return best


# ============================================================================= comping
def _consensus_phrases(As, F):
    masks = np.zeros((len(As), F))
    for i, A in enumerate(As):
        pm = A["pmask"][:F]
        masks[i, :len(pm)] = pm
    frac = masks.mean(axis=0)
    comp = _data("comp.json")
    regs = _regions(frac >= 0.5, int(comp.get("min_gap_s", 0.25) / HOP_S), int(comp.get("min_phrase_s", 0.25) / HOP_S))
    return regs, masks


def _cut_points(phrases, As, F):
    """A cut in every gap between phrases, at the frame where all takes together are quietest."""
    cuts = []
    E = np.zeros(F)
    for A in As:
        env = A["env"][:F]
        E[:len(env)] += 10 ** (env / 10)
    for (a0, b0), (a1, b1) in zip(phrases, phrases[1:]):
        lo, hi = b0, max(b0 + 1, a1)
        k = lo + int(np.argmin(E[lo:hi])) if hi > lo else lo
        cuts.append((k, hi - lo))
    return cuts


def _phrase_metrics(A, x, sr, a, b, ctx):
    """Raw metric amounts of one take in frames [a, b)."""
    met = {}
    c = ctx["cents_s"][ctx["i"]][a:b]
    craw = ctx["cents"][ctx["i"]][a:b]
    tg = ctx["target"][a:b] if ctx.get("target") is not None else None
    ok = np.isfinite(c) & (np.isfinite(tg) if tg is not None else True)
    if ok.sum() >= 10:
        dev = _pitch_dev(c[ok], ctx.get("pcs"), tg[ok] if tg is not None else None)
        met["pitch_cents"] = float(np.median(np.abs(dev)))
    else:
        met["pitch_cents"] = None
    v = np.isfinite(craw)
    d2 = craw[2:] - 2 * craw[1:-1] + craw[:-2] if len(craw) > 2 else np.array([])
    good = v[2:] & v[1:-1] & v[:-2] if len(craw) > 2 else np.array([], bool)
    if len(d2):
        jumps = np.abs(np.diff(craw))
        good &= np.nan_to_num(jumps[1:], nan=999) < 100
        good &= np.nan_to_num(jumps[:-1], nan=999) < 100
    met["instability"] = float(np.median(np.abs(d2[good]))) if good.sum() >= 10 else None
    if ctx.get("bpm"):
        grid = 60.0 / ctx["bpm"] / 4.0
        on = [o / sr for o in ctx["onsets"][ctx["i"]] if a * HOP_S <= o / sr < b * HOP_S]
        if on:
            ph = (np.array(on) + ctx["origin_s"]) / grid
            off = np.abs(ph - np.round(ph)) * grid * 1000.0
            met["timing_ms"] = float(np.median(off))
        else:
            met["timing_ms"] = None
    else:
        met["timing_ms"] = None
    env = A["env"]
    near = np.zeros(len(env), bool)
    near[max(0, a - 50):a] = True; near[b:min(len(env), b + 50)] = True
    near &= ~ctx["consensus"][:len(env)]
    met["noise_db"] = float(np.percentile(env[near], 20)) if near.sum() >= 5 else A["floor_db"]
    s0, s1 = int(a * HOP_S * sr), min(len(x), int(b * HOP_S * sr))
    met["clipped"] = int((np.abs(x[s0:s1]) >= 0.995).any(axis=1).sum()) if s1 > s0 else 0
    t0, t1 = a * HOP_S, b * HOP_S
    met["plosives"] = sum(1 for e in A.get("plosives", []) if t0 <= e["t"] < t1)
    met["sibilance"] = sum(1 for e in A.get("sibilance", []) if t0 <= e["t"] < t1 and e["ratio"] > 0.6)
    act = A["pmask"][a:b]
    lvl = env[a:b][act] if act.any() else env[a:b]
    met["level_db"] = float(_db(np.mean(10 ** (lvl / 10))))
    met["present"] = float(act.mean()) if len(act) else 0.0
    return met


def _penalties(mets, style):
    cfg = _data("comp.json")
    W = dict(cfg["weights"].get("default", {}), **cfg["weights"].get(style or "", {}))
    M = cfg["metrics"]
    levels = [m["level_db"] for m in mets if m["present"] >= 0.5]
    med = float(np.median(levels)) if levels else 0.0
    out = []
    for m in mets:
        m["energy_dev_db"] = abs(m["level_db"] - med) if m["present"] >= 0.5 else None
        m["missing"] = max(0.0, 100.0 * (1.0 - m["present"] / 0.8)) if m["present"] < 0.8 else 0.0
        parts = {}
        for k, spec in M.items():
            v = m.get(k)
            if v is None:
                continue
            v = max(0.0, v - spec.get("free_below", 0.0)) if k not in ("missing", "clipped", "plosives", "sibilance") else v
            pts = min(spec.get("cap", 99), v / spec["per"]) * W.get(k, 1.0)
            if pts > 0:
                parts[k] = pts
        out.append(parts)
    return out


def _viterbi(costs, switch):
    """Best take per phrase: sum of penalties plus `switch` per take change."""
    P, T = costs.shape
    acc = costs[0].copy(); back = np.zeros((P, T), int)
    for p in range(1, P):
        trans = acc[:, None] + switch * (1 - np.eye(T))
        back[p] = np.argmin(trans, axis=0)
        acc = trans[back[p], np.arange(T)] + costs[p]
    path = [int(np.argmin(acc))]
    for p in range(P - 1, 0, -1):
        path.append(int(back[p][path[-1]]))
    return path[::-1]


_LABEL = {"pitch_cents": ("pitch", "c"), "instability": ("wobble", "c"), "timing_ms": ("timing", "ms"),
          "noise_db": ("noise", "dB"), "clipped": ("clipping", " smp"), "plosives": ("pops", ""),
          "sibilance": ("harsh s", ""), "energy_dev_db": ("level", "dB"), "missing": ("missing", "%")}


def _why(mets, parts, pick, other):
    if other is None:
        return "only take"
    diff = {k: parts[other].get(k, 0) - parts[pick].get(k, 0) for k in set(parts[other]) | set(parts[pick])}
    if not diff:
        return "tie"
    k = max(diff, key=diff.get)
    if diff[k] <= 0:
        return "kept (switching not worth it)"
    nm, unit = _LABEL.get(k, (k, ""))
    a, b = mets[pick].get(k), mets[other].get(k)
    if k == "missing":
        return f"take {other + 1} missing part of the phrase"
    if a is None or b is None:
        return nm
    return f"{nm} {a:.0f}{unit} vs {b:.0f}{unit}"


def vocal_comp(takes, project=None, phrases="auto", key=None, reference_melody=None, output=None, style=None,
               bpm=None, start_bar=None, crossfade_ms=None, full=False):
    st, mark = _steps()
    if not takes or len(takes) < 1:
        raise ValueError("give at least one take (WAV paths)")
    takes = [str(t) for t in takes]
    cfg = _data("comp.json")
    X, sr0 = [], None
    for p in takes:
        x, sr, _ = _load_wav(p)
        if sr0 is None:
            sr0 = sr
        elif sr != sr0:
            raise ValueError(f"all takes need the same sample rate ({os.path.basename(p)} is {sr}, not {sr0})")
        X.append(x)
    sr = sr0
    n = max(len(x) for x in X); ch = max(x.shape[1] for x in X)
    X = [np.pad(np.repeat(x, ch, axis=1) if x.shape[1] < ch else x, ((0, n - len(x)), (0, 0))) for x in X]
    mark("load")
    As = [analyze(x, sr) for x in X]
    F = max(len(A["env"]) for A in As)
    mark("analyze")
    # key / melody / tempo
    key_src = "given" if key else None
    if reference_melody is None and not key:
        key, key_src = _project_key(project)
    pbpm, bpb = _project_tempo_bpb(project)
    bpm = float(bpm) if bpm else pbpm
    if start_bar is None and project:
        try:
            import flh_store
            vt = (flh_store.journal_get(project).get("vocal") or {}).get("takes") or []
            byp = {os.path.normcase(os.path.abspath(t.get("path", ""))): t for t in vt}
            hit = byp.get(os.path.normcase(os.path.abspath(takes[0])))
            start_bar = hit.get("starts_at_bar") if hit else None
        except Exception:
            pass
    sb = float(start_bar) if start_bar is not None else 1.0
    if not key and reference_melody is None:
        k, conf = _estimate_key([A["f0"] for A in As])
        key, key_src = k, f"estimated from the takes (confidence {conf:.2f})" if k else None
    pcs = _key_pcs(key) if key else None
    target = _melody_targets(reference_melody, F, bpm, sb, bpb) if reference_melody is not None else None
    import fl_audio_edit as AE
    ctx = {"pcs": pcs, "target": target, "bpm": bpm, "origin_s": (sb - 1) * bpb * 60.0 / bpm if bpm else 0.0,
           "cents": [], "cents_s": [], "onsets": [], "consensus": None}
    for A in As:
        c = _cents(np.pad(A["f0"], (0, F - len(A["f0"])), constant_values=np.nan))
        ctx["cents"].append(c); ctx["cents_s"].append(_smooth_voiced(c))
        ctx["onsets"].append(AE.detect_onsets(A["m"], sr) if bpm else [])
    mark("pitch_timing")
    # phrases
    if phrases in (None, "auto"):
        P, _ = _consensus_phrases(As, F)
        ph_src = "auto (energy + voicing, aligned across takes)"
    else:
        P = [(max(0, int(round(float(a) / HOP_S))), min(F, int(round(float(b) / HOP_S)))) for a, b in phrases]
        P = [(a, b) for a, b in sorted(P) if b > a]
        ph_src = "given"
    if not P:
        raise ValueError("no sung phrases found in the takes (silent or very quiet?)")
    cons = np.zeros(F, bool)
    for a, b in P:
        cons[a:b] = True
    ctx["consensus"] = cons
    # score
    T = len(As)
    mets = [[None] * T for _ in P]; parts = [[None] * T for _ in P]
    costs = np.zeros((len(P), T))
    for pi, (a, b) in enumerate(P):
        row = []
        for i, A in enumerate(As):
            ctx["i"] = i
            row.append(_phrase_metrics(A, X[i], sr, a, b, ctx))
        pr = _penalties(row, style)
        for i in range(T):
            mets[pi][i] = row[i]; parts[pi][i] = pr[i]; costs[pi, i] = sum(pr[i].values())
    path = _viterbi(costs, float(cfg.get("switch_cost", 4.0)))
    mark("score")
    # build
    cuts = _cut_points(P, As, F)
    xf = float(crossfade_ms if crossfade_ms is not None else cfg.get("crossfade_ms", 20)) / 1000.0
    bounds = [0] + [int(k * HOP_S * sr) for k, _ in cuts] + [n]
    out = np.zeros((n, ch))
    switches = []
    for si in range(len(P)):
        a, b = bounds[si], bounds[si + 1]
        take = path[si]
        ga = np.ones(b - a)
        if si > 0 and path[si - 1] != take:
            L = int(min(xf, max(cuts[si - 1][1] * HOP_S, 0.002)) * sr)
            a = max(0, a - L // 2)
            ga = np.ones(b - a); r = np.arange(L) / max(L - 1, 1)
            ga[:L] = np.sin(r * np.pi / 2)
            switches.append(round(bounds[si] / sr, 3))
        if si + 1 < len(P) and path[si + 1] != take:
            L = int(min(xf, max(cuts[si][1] * HOP_S, 0.002)) * sr)
            z = min(n, b + (L - L // 2))
            extra = z - b
            ga = np.concatenate([ga, np.ones(extra)])
            r = np.arange(L) / max(L - 1, 1)
            ga[len(ga) - L:] *= np.cos(r * np.pi / 2)
            b = z
        out[a:b] += X[take][a:b] * ga[:b - a, None]
    out_path = _out_path(takes[0], "comp", output)
    _ensure_new(takes[0], out_path)
    _save_wav(out_path, out, sr)
    mark("write")
    table = []
    for pi, (a, b) in enumerate(P):
        sc = [int(round(100 - costs[pi, i])) for i in range(T)]
        order = sorted(range(T), key=lambda i: costs[pi, i])
        other = next((i for i in order if i != path[pi]), None)
        if order[0] != path[pi] and costs[pi, order[0]] < costs[pi, path[pi]] - 0.5:
            why = f"kept take {path[pi] + 1} to avoid a switch (take {order[0] + 1} scores {sc[order[0]]})"
        else:
            why = _why(mets[pi], parts[pi], path[pi], other)
        row = {"p": pi + 1, "s": [round(a * HOP_S, 2), round(b * HOP_S, 2)], "pick": path[pi] + 1, "scores": sc,
               "why": why}
        if full:
            row["metrics"] = [{k: _r(v, 1) for k, v in m.items() if v is not None} for m in mets[pi]]
        table.append(row)
    use = {i + 1: sum(1 for p in path if p == i) for i in range(T)}
    res = {"output": out_path, "takes": len(takes), "phrases": len(P), "phrase_source": ph_src,
           "key": key, "key_source": ("reference melody" if reference_melody is not None else key_src),
           "tempo_bpm": bpm, "timing_grid": "1/16 straight" if bpm else "not judged (no tempo)",
           "pitch_from": As[0]["pitch_source"], "phrases_per_take": use, "switch_points_s": switches,
           "decisions": table if full or len(table) <= 24 else table[:24] + [{"more": len(table) - 24}],
           "steps_s": st,
           "note": "picked by measurement; listen at the switch points (crossfades sit in the gaps between phrases)"}
    if project:
        try:
            import flh_store
            flh_store.journal_update(project, set={"vocal": {"comp": {"path": out_path, "takes": takes, "key": key,
                                     "picks": [p + 1 for p in path], "when": time.strftime("%Y-%m-%d %H:%M"),
                                     **({"starts_at_bar": sb} if start_bar is not None else {})}}})
            res["journal"] = "vocal.comp"
        except Exception as e:
            res["journal_error"] = f"{type(e).__name__}: {e}"
    return res


# ============================================================================= cleanup
def _fmt_events(evs, full, key="t"):
    ts = [round(e[key], 3) if isinstance(e, dict) else round(e, 3) for e in evs]
    return ts if full or len(ts) <= 20 else ts[:20] + [f"+{len(ts) - 20} more"]


def vocal_clean(wav, output=None, breaths="reduce", clicks=True, plosives=True, sibilance=True, noise=True,
                amounts=None, full=False):
    if breaths not in ("reduce", "keep", "remove"):
        raise ValueError("breaths: reduce, keep or remove")
    st, mark = _steps()
    cfg = dict(CLEAN_DEFAULTS, **(amounts or {}))
    x, sr, bits = _load_wav(wav)
    out_path = _out_path(wav, "clean", output)
    _ensure_new(wav, out_path)
    H["writable"](out_path)
    mark("load")
    A = analyze(x, sr, cfg)
    ft = A["feat"]; t = ft["t"]; M = len(t)
    mark("analyze")
    y = x.copy()
    rep = {}
    # ---- clicks (time domain, before anything smears them)
    found = detect_clicks(A["m"], sr, cfg["click_k"], cfg["click_max_ms"], cfg.get("click_ratio", 1.7))
    near_plo = [(p["t"] - 0.01, p["t"] + p["dur_ms"] / 1000.0 + 0.01) for p in A["plosives"]]
    found = [(a, b) for a, b in found if not any(lo <= a / sr <= hi for lo, hi in near_plo)]
    if clicks:
        pad = max(1, int(sr * 0.0005))
        for a, b in found:
            a0, b0 = max(0, a - pad), min(len(y), b + pad)
            for c in range(y.shape[1]):
                y[a0:b0, c] = _ar_fill(y[:, c], a0, b0)
    rep["clicks"] = {"count": len(found), "fixed": bool(clicks), "at_s": _fmt_events([a / sr for a, _ in found], full)}
    mark("clicks")
    # ---- spectral gains: plosives (low band), sibilance (5-9 kHz band), breaths (everything)
    g_low = np.zeros(M); g_sib = np.zeros(M); g_all = np.zeros(M)
    if plosives and A["plosives"]:
        g_low = _ramp_gain(M, A["plosives"], -abs(cfg["plosive_db"]), t, 0.008)
    rep["plosives"] = {"count": len(A["plosives"]), "fixed": bool(plosives),
                       "cut_db": abs(cfg["plosive_db"]) if plosives else 0,
                       "below_hz": cfg["plosive_hz"], "at_s": _fmt_events(A["plosives"], full)}
    if sibilance and A["sibilance"]:
        tot = ft["tot"] + 1e-20; S = ft["sib"]; r = cfg["sib_target_ratio"]
        g2 = r * np.maximum(tot - S, 1e-20) / np.maximum(S * (1 - r), 1e-20)
        want = np.clip(10 * np.log10(np.maximum(g2, 1e-20)), -abs(cfg["deess_max_db"]), 0.0)
        mask = np.zeros(M, bool)
        for e in A["sibilance"]:
            mask[e["a"]:e["b"]] = True
        want = np.where(mask, want, 0.0)
        rel = max(1, int(0.03 / (t[1] - t[0])))                   # instant attack, ~30 ms release
        sm = want.copy()
        for k in range(1, M):
            sm[k] = min(want[k], sm[k - 1] + abs(cfg["deess_max_db"]) / rel)
        g_sib = sm
    sib_hz = [e["hz"] for e in A["sibilance"]]
    rep["sibilance"] = {"count": len(A["sibilance"]), "fixed": bool(sibilance),
                        "center_hz": int(np.median(sib_hz)) if sib_hz else None,
                        "max_cut_db": _r(-g_sib.min(), 1) if sibilance and len(g_sib) else 0,
                        "at_s": _fmt_events(A["sibilance"], full)}
    if breaths != "keep" and A["breaths"]:
        g_all = _ramp_gain(M, A["breaths"], -abs(cfg["breath_reduce_db"] if breaths == "reduce" else cfg["breath_remove_db"]),
                           t, 0.02)
        dt = t[1] - t[0] if len(t) > 1 else 0.005                 # word onsets are protected: never more than a
        kg = max(1, int(float(cfg.get("onset_guard_ms", 100.0)) / 1000.0 / dt))   # few dB right before a vowel
        for k in A.get("voiced_onsets", []):
            seg = g_all[max(0, k - kg):k + 1]
            np.maximum(seg, -abs(float(cfg.get("onset_guard_db", 3.0))), out=seg)
    rep["breaths"] = {"count": len(A["breaths"]), "action": breaths,
                      "by_db": 0 if breaths == "keep" else abs(cfg["breath_reduce_db"] if breaths == "reduce" else cfg["breath_remove_db"]),
                      "at_s": _fmt_events(A["breaths"], full),
                      "spans_s": [[round(float(e["t"]), 3), round(float(e["t"] + e["dur_ms"] / 1000.0), 3)] for e in A["breaths"]][:40]}
    if A.get("consonant_onsets"):
        rep["breaths"]["kept_as_consonants_s"] = _fmt_events(A["consonant_onsets"], full)
    if (g_low < 0).any() or (g_sib < 0).any() or (g_all < 0).any():
        lin = lambda g: 10 ** (g / 20)
        y = _stft_gain(y, sr, lin(g_all), lin(g_low), lin(g_sib), ft["N"], ft["H"], cfg["plosive_hz"],
                       cfg["sib_band_hz"][0], min(cfg["sib_band_hz"][1], sr / 2 * 0.96))
    mark("spectral")
    # ---- room noise: gentle spectral gating from a noise-only stretch (fl_audio_edit.denoise)
    floor = A["floor_db"]
    nz = {"floor_db": _r(floor, 1)}
    if noise:
        quiet = ~A["pmask"].copy()
        for e in A["breaths"] + A["plosives"]:
            a, b = int(e["t"] / HOP_S), int((e["t"] + e["dur_ms"] / 1000.0) / HOP_S) + 1
            quiet[max(0, a - 5):b + 5] = False
        regs = sorted(_regions(quiet, 3, int(0.25 / HOP_S)), key=lambda r: r[0] - r[1])
        if floor <= cfg["noise_skip_below_db"]:
            nz.update(reduced_db=0, skipped=f"floor already {floor:.0f} dBFS")
        elif not regs:
            nz.update(reduced_db=0, skipped="no noise-only stretch of 0.25 s to learn the noise from")
        else:
            import fl_audio_edit as AE
            a, b = regs[0]
            y = AE.denoise(y, sr, amount_db=abs(cfg["noise_db"]), profile=(int(a * HOP_S * sr), int(b * HOP_S * sr)))
            nz.update(reduced_db=abs(cfg["noise_db"]), profile_s=[round(a * HOP_S, 2), round(b * HOP_S, 2)])
    else:
        nz["reduced_db"] = 0
    rep["noise"] = nz
    mark("noise")
    _save_wav(out_path, y, sr, bits if bits in (16, 24) else 24)
    mark("write")
    return {"output": out_path, "seconds": round(len(x) / sr, 2), "found": rep, "steps_s": st,
            "thresholds": {k: cfg[k] for k in ("click_k", "click_ratio", "plosive_ratio", "plosive_jump_db", "sib_ratio",
                                                "breath_flatness") if k in cfg},
            "note": "new file; the take is untouched. Detection is by measurement - listen to the listed times "
                    "(amounts={...} tunes any threshold or depth)"}


# ============================================================================= vocal chain
def measure_take(x, sr):
    """What the chain is sized from: low end, mud, harshness, sibilance, dynamics, level, range of the voice."""
    A = analyze(x, sr)
    m = A["m"]
    act = A["pmask"]
    N = 8192
    f = np.fft.rfftfreq(N, 1.0 / sr); acc = np.zeros(len(f)); k = 0
    w = np.hanning(N)
    for s in range(0, len(m) - N, N // 2):
        c = int((s + N // 2) / sr / HOP_S)
        if c < len(act) and act[c]:
            acc += np.abs(np.fft.rfft(m[s:s + N] * w)) ** 2; k += 1
    if not k:
        raise ValueError("no sung/spoken phrases found in the take")
    S = acc / k
    # envelope through the harmonic tops (running max over one f0 spacing), then 1/3-octave smoothing: a sung note's
    # sparse harmonics would otherwise look like EQ bumps. Approximate by nature.
    f0v = A["f0"][np.isfinite(A["f0"])]
    df = f[1] - f[0]
    wb = max(3, int((float(np.median(f0v)) if len(f0v) else 150.0) / df))
    from numpy.lib.stride_tricks import sliding_window_view
    Sd = _db(S)
    env_db = sliding_window_view(np.pad(Sd, (wb // 2, wb - 1 - wb // 2), mode="edge"), wb).max(axis=1)
    Se = 10 ** (env_db / 10)
    grid = 2 ** np.arange(np.log2(50), np.log2(min(16000, sr / 2 * 0.95)), 1 / 24.0)
    lvl = []
    for g in grid:
        bm = (f >= g * 2 ** (-1 / 6)) & (f < g * 2 ** (1 / 6))
        lvl.append(_db(Se[bm].mean()) if bm.any() else np.nan)
    lvl = np.array(lvl)
    fit_m = (grid >= 150) & (grid <= 6000) & np.isfinite(lvl)
    coef = np.polyfit(np.log2(grid[fit_m]), lvl[fit_m], 1)
    res = lvl - np.polyval(coef, np.log2(grid))
    def bump(lo, hi):
        bm = (grid >= lo) & (grid <= hi) & np.isfinite(res)
        if not bm.any():
            return None, 0.0
        i = int(np.argmax(np.where(bm, res, -np.inf)))
        return float(grid[i]), float(res[i])
    mud_hz, mud_db = bump(180, 600)
    harsh_hz, harsh_db = bump(2000, 5000)
    lowm = (grid >= 60) & (grid <= 160) & np.isfinite(res)
    prox = float(np.mean(res[lowm])) if lowm.any() else 0.0
    f0 = A["f0"][np.isfinite(A["f0"])]
    loud = act & (A["env"] > A["thr_db"])                       # sounding frames only (not the gaps inside phrases)
    env = A["env"][loud]
    sm = np.convolve(A["env"], np.ones(5) / 5, mode="same")[loud]
    sib = A["sibilance"]
    dur_min = max(act.sum() * HOP_S / 60.0, 1e-3)
    sib_ex = [10 * np.log10(e["ratio"] / CLEAN_DEFAULTS["sib_target_ratio"]) for e in sib]
    return {"seconds": round(len(m) / sr, 2), "active_s": round(act.sum() * HOP_S, 1),
            "level_db": _r(np.percentile(env, 50), 1) if len(env) else None,
            "peak_dbfs": _r(20 * np.log10(max(np.abs(x).max(), 1e-9)), 1),
            "p95_db": _r(np.percentile(sm, 95), 1) if len(sm) else None,
            "range_db": _r(np.percentile(sm, 95) - np.percentile(sm, 10), 1) if len(sm) else None,
            "f0_low_hz": _r(np.percentile(f0, 5), 0) if len(f0) else None,
            "f0_median_hz": _r(np.median(f0), 0) if len(f0) else None,
            "mud": {"hz": _r(mud_hz, 0), "excess_db": _r(mud_db, 1), "note": "approximate (envelope through the harmonics)"},
            "harsh": {"hz": _r(harsh_hz, 0), "excess_db": _r(harsh_db, 1)},
            "low_end_excess_db": _r(prox, 1),
            "sibilance": {"per_min": _r(len(sib) / dur_min, 1), "center_hz": _r(np.median([e["hz"] for e in sib]), 0) if sib else None,
                          "excess_db": _r(np.median(sib_ex), 1) if sib else 0.0,
                          "level_db": _r(np.median([e["db"] for e in sib]), 1) if sib else None},
            "floor_db": _r(A["floor_db"], 1)}


def _own(name):
    fn = H.get("plugin_ownership")
    if not fn:
        if str(name).lower().startswith("fruity "):
            return {"status": True, "why": "stock Fruity plugin (every edition)"}
        return {"status": "unknown", "why": "edition unknown (flh_catalog not loaded)"}
    try:
        return fn(name)
    except Exception as e:
        return {"status": "unknown", "why": f"{type(e).__name__}: {e}"}


def derive_chain(meas, style, bpm=None, bpm_from=None):
    data = _data("chains.json")
    styles = data["styles"]
    if style not in styles:
        raise ValueError(f"style: {', '.join(styles)}")
    S = styles[style]; PL = data["plugins"]
    bpm_src = bpm_from or ("given" if bpm else "assumed 120 (no tempo given)")
    bpm = float(bpm or 120.0)
    beat_ms = 60000.0 / bpm
    chain = []
    # 1. clean-up EQ: high-pass under the voice, low-end / mud / harsh cuts sized from the take
    hp = float(S["hpf_hz"]); why = [f"{style} starting point {hp:.0f} Hz"]
    if meas.get("f0_low_hz"):
        lim = 0.75 * meas["f0_low_hz"]
        if hp > lim:
            hp = lim; why.append(f"kept under the voice's lowest notes ({meas['f0_low_hz']:.0f} Hz)")
    bands = [{"band": "high-pass", "hz": int(round(hp / 5) * 5), "slope": "24 dB/oct", "why": "; ".join(why)}]
    le = meas.get("low_end_excess_db") or 0.0
    if le > 2.0:
        bands.append({"band": "low shelf", "hz": 200, "gain_db": -round(min(6.0, le), 1),
                      "why": f"proximity effect: +{le:.1f} dB extra low end (mic close)"})
    mud = meas.get("mud") or {}
    if (mud.get("excess_db") or 0) > 1.5:
        bands.append({"band": "bell", "hz": int(mud["hz"]), "q": 1.4, "gain_db": -round(min(6.0, mud["excess_db"] * 0.8), 1),
                      "why": f"mud bump +{mud['excess_db']:.1f} dB at {mud['hz']:.0f} Hz"})
    harsh = meas.get("harsh") or {}
    pres = dict(S["presence"])
    if (harsh.get("excess_db") or 0) > 2.0:
        bands.append({"band": "bell", "hz": int(harsh["hz"]), "q": 2.0, "gain_db": -round(min(4.0, harsh["excess_db"] * 0.7), 1),
                      "why": f"harsh bump +{harsh['excess_db']:.1f} dB at {harsh['hz']:.0f} Hz"})
        if abs(math.log2(max(harsh["hz"], 1) / pres["hz"])) < 0.6:
            pres["db"] = 0.0
    chain.append({"stage": "corrective EQ", "plugin": PL["eq"], "bands": bands})
    # 2. compression sized from the measured range
    rng = meas.get("range_db") or 10.0; p95 = meas.get("p95_db") if meas.get("p95_db") is not None else -12.0
    scale = 0.7 if rng < 6 else (1.25 if rng > 15 else 1.0)
    comps = S["comp"] if rng >= 6 or len(S["comp"]) == 1 else S["comp"][:1]
    for i, c in enumerate(comps):
        gr = c["target_gr_db"] * scale
        over = gr / (1 - 1 / c["ratio"])
        chain.append({"stage": f"compressor {i + 1}", "plugin": PL["compressor"] if i == 0 else PL["leveler"],
                      "settings": {"threshold_db": round(p95 - over, 1), "ratio": c["ratio"], "attack_ms": c["attack_ms"],
                                   "release_ms": c["release_ms"], "gain_db": round(gr * 0.6, 1)},
                      "why": f"take moves {rng:.0f} dB (P10-P95): about {gr:.0f} dB gain reduction on the loud words; "
                             "threshold is relative to the take at unity gain"})
    # 3. de-essing from the measured sibilance
    sib = meas.get("sibilance") or {}
    if (sib.get("per_min") or 0) > 0:
        cen = sib.get("center_hz") or 7000
        depth = round(min(float(S["deess_max_db"]), max(2.0, float(sib.get("excess_db") or 0) + 2.0)), 1)
        de = {"stage": "de-esser", "plugin": PL["deesser"],
              "settings": {"crossover_hz": int(max(4000, min(8000, cen * 0.7))), "high_threshold_db":
                           round((sib.get("level_db") or -30) - depth, 1), "high_ratio": 4.0, "target_reduction_db": depth},
              "why": f"{sib['per_min']:.0f} sibilants/min centred near {cen:.0f} Hz, about {sib.get('excess_db') or 0:.1f} dB "
                     "over a natural share: compress only the top band"}
        alt = _own(PL["deesser_alt"])
        if alt.get("status") is True:
            de["alternative"] = f"{PL['deesser_alt']} high band (you own it)"
        de["offline_option"] = (C.tool_call(H, "fl_vocal_clean", {"wav": C.Raw("vocal wav"), "sibilance": True})
                                + " de-esses the file itself")
        chain.append(de)
    else:
        chain.append({"stage": "de-esser", "plugin": None, "why": "no harsh sibilance measured: skip it"})
    # 4. tone + colour
    tone = []
    if pres.get("db"):
        tone.append({"band": "bell", "hz": pres["hz"], "q": 1.0, "gain_db": pres["db"], "why": "presence / intelligibility"})
    air = dict(S["air"])
    if air.get("db") and (sib.get("excess_db") or 0) > 3:
        air["db"] = max(0.0, air["db"] - 1.5)
    if air.get("db"):
        tone.append({"band": "high shelf", "hz": air["hz"], "gain_db": air["db"],
                     "why": "air" + (" (reduced: the take is already sibilant)" if air["db"] < S["air"]["db"] else "")})
    if tone:
        chain.append({"stage": "tone EQ", "plugin": PL["eq"], "bands": tone})
    if S.get("saturation") and S["saturation"] != "none":
        chain.append({"stage": "saturation", "plugin": PL["saturation"],
                      "settings": {"amount": S["saturation"], "mix": 0.25 if S["saturation"] == "light" else 0.4},
                      "why": "harmonic density so the vocal reads on small speakers (parallel: keep the mix low)"})
    # sends sized to tempo
    sends = []
    rv = S.get("reverb")
    if rv:
        decay = max(0.6, min(4.5, rv["decay_beats"] * beat_ms / 1000.0))
        pre = max(10.0, min(80.0, rv["predelay_note"] * 4 * beat_ms))
        sends.append({"bus": "Vocal Reverb", "plugin": PL["reverb"],
                      "settings": {"decay_s": round(decay, 2), "predelay_ms": round(pre), "low_cut_hz": 250,
                                   "high_cut_hz": 9000, "wet": 1.0, "dry": 0.0}, "send_level": rv["wet"],
                      "why": f"{rv['decay_beats']} beats at {bpm:.0f} BPM; pre-delay keeps the words dry"})
    dl = S.get("delay")
    if dl:
        sends.append({"bus": "Vocal Delay", "plugin": PL["delay"],
                      "settings": {"time_ms": round(dl["beats"] * beat_ms), "time_note": _note_name(dl["beats"]),
                                   "feedback": dl["feedback"], "wet": 1.0, "dry": 0.0}, "send_level": dl["wet"],
                      "why": f"{_note_name(dl['beats'])} at {bpm:.0f} BPM"})
    # tuning
    tun = dict(S.get("tuner") or {})
    if tun.get("use") != "none":
        owned = [p for p in data["tuners"]["native"] if _own(p).get("status") is True]
        if owned:
            tun.update(plugin=owned[0], where="first slot (before the EQ)")
        else:
            tun.update(plugin=None, native_status={p: _own(p).get("why") for p in data["tuners"]["native"]},
                       free_options=data["tuners"]["free_third_party"], note=data["tuners"]["note"])
    for c in chain + sends:
        if c.get("plugin"):
            c["owned"] = _own(c["plugin"]).get("status")
    return {"style": style, "about": S.get("about"), "tempo_bpm": bpm, "tempo_from": bpm_src, "chain": chain,
            "sends": sends, "tuning": tun, "balance_target_lu": S.get("balance_lu")}


def _note_name(beats):
    names = {4: "1/1", 3: "1/2 dotted", 2: "1/2", 1.5: "1/4 dotted", 1: "1/4", 0.75: "1/8 dotted", 0.5: "1/8",
             0.375: "1/16 dotted", 0.25: "1/16"}
    return names.get(float(beats), f"{beats} beats")


# ============================================================================= doubles and harmonies
def _smooth_noise(n, sr, rate_hz, rng):
    """Smooth random curve in -1..1 (a few slow sines with random phases + low-passed noise)."""
    t = np.arange(n) / sr
    c = np.zeros(n)
    for k, mult in enumerate((1.0, 1.7, 2.9)):
        c += np.sin(2 * np.pi * rate_hz * mult * t + rng.uniform(0, 2 * np.pi)) / (k + 1)
    return c / np.abs(c).max() if np.abs(c).max() > 0 else c


def _vardelay(x, sr, delay_ms, drift_cents, rate_hz, rng):
    """y(t) = x(t - d(t)): a fixed timing offset plus a slowly wandering delay; the delay's slope is the pitch drift
    (cents = -1200 log2(1 + d'(t))), so drift_cents sets how far it wanders."""
    n = len(x)
    c = _smooth_noise(n, sr, rate_hz, rng)
    slope = drift_cents / 1200.0 * math.log(2)                 # max |d'| for that many cents
    dc = np.gradient(c) * sr
    k = slope / max(np.abs(dc).max(), 1e-12)
    d = np.maximum(delay_ms / 1000.0 + k * c, 0.0005) * sr     # samples
    pos = np.arange(n) - d
    i0 = np.floor(pos).astype(np.int64); fr = pos - i0
    ok = (i0 >= 0) & (i0 + 1 < n)
    y = np.zeros_like(x)
    i0c = np.clip(i0, 0, n - 2)
    for ch in range(x.shape[1]):
        col = x[:, ch]
        y[:, ch] = np.where(ok, col[i0c] * (1 - fr) + col[i0c + 1] * fr, 0.0)
    return y, d / sr * 1000.0


def _formant_fix(orig, shifted, sr, max_db=12.0):
    """Put the original's spectral envelope (cepstral, ~1.5 ms lifter) back onto the shifted voice."""
    import fl_audio_edit as AE
    N, Hh = 2048, 512
    win = np.sqrt(np.hanning(N + 1)[:N])
    nc = max(8, int(sr * 0.0015))
    out = np.zeros(shifted.shape)

    def env(X):
        L = np.log(np.abs(X) + 1e-9)
        cep = np.fft.irfft(L, N, axis=1); cep[:, nc:N - nc] = 0
        return np.fft.rfft(cep, N, axis=1).real
    for c in range(shifted.shape[1]):
        a = orig[:, min(c, orig.shape[1] - 1)]; b = shifted[:, c]
        n = len(b); M = int(math.ceil(n / Hh)) + 1

        def process(XB, m0, m1, e0, a=a):
            XA = AE.stft_frames(a, N, Hh, win, m0, m1)
            G = np.clip(env(XA) - env(XB), -max_db / 8.686, max_db / 8.686)
            return XB * np.exp(G)
        AE.stft_stream(b, N, Hh, win, win, process, out[:, c], block=256, M=M)
    return out


def _shift(x, sr, semis, formant):
    import fl_audio_edit as AE
    y = AE.pitch_shift(x, float(semis), keep_length=True)
    y = y[:len(x)] if len(y) >= len(x) else np.pad(y, ((0, len(x) - len(y)), (0, 0)))
    return _formant_fix(x, y, sr) if formant and abs(semis) > 0.3 else y


def _spans(mask, margin, n):
    """[(a, b)] runs of True in mask, widened by margin samples each side and merged (context for the shifter)."""
    m = np.asarray(mask, bool)
    if not m.any():
        return []
    d = np.diff(np.concatenate(([0], m.astype(np.int8), [0])))
    st, en = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    out = []
    for a, b in zip(np.maximum(0, st - margin), np.minimum(n, en + margin)):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], int(b))
        else:
            out.append([int(a), int(b)])
    return [tuple(o) for o in out]


def _harmony_intervals(f0, pcs, steps):
    """Semitones per 10 ms frame that move each sung note `steps` scale degrees (2 = a third up), held through
    unvoiced frames, median-smoothed."""
    F = len(f0)
    iv = np.full(F, np.nan)
    v = np.isfinite(f0)
    midi = 69 + 12 * np.log2(np.where(v, f0, 440.0) / 440.0)
    scale = sorted(pcs)
    for i in np.flatnonzero(v):
        k = midi[i]; base = int(np.floor(k / 12.0)) * 12
        cands = [base + o + p for o in (-12, 0, 12) for p in scale]
        j = int(np.argmin([abs(c - k) for c in cands]))
        ext = [base + o + p for o in (-24, -12, 0, 12, 24) for p in scale]
        jj = ext.index(cands[j]) + int(steps)
        tgt = ext[max(0, min(len(ext) - 1, jj))]
        iv[i] = tgt - cands[j]
    last = None
    for i in range(F):
        if np.isfinite(iv[i]):
            last = iv[i]
        elif last is not None:
            iv[i] = last
    first = next((v_ for v_ in iv if np.isfinite(v_)), None)
    iv = np.where(np.isfinite(iv), iv, first if first is not None else 0)
    w = 7
    pad = np.pad(iv, (w // 2, w // 2), mode="edge")
    from numpy.lib.stride_tricks import sliding_window_view
    return np.median(sliding_window_view(pad, w), axis=1)[:F]


def vocal_double(wav, output=None, kind="double", key=None, width=0.8, delay_ms=None, voices=None, steps=None,
                 drift_cents=8.0, formant=True, project=None, seed=1):
    kinds = ("double", "octave_up", "octave_down", "third_up", "harmony")
    if kind not in kinds:
        raise ValueError(f"kind: {', '.join(kinds)}")
    st, mark = _steps()
    x, sr, bits = _load_wav(wav)
    n = len(x)
    out_path = _out_path(wav, kind.replace("_", " "), output)
    _ensure_new(wav, out_path)
    H["writable"](out_path)
    rng = np.random.default_rng(int(seed))
    mono = _mono(x)[:, None]
    width = max(0.0, min(1.0, float(width)))
    info = {}
    layers = []                                                 # (signal (n,1), pan -1..1, label)
    if kind == "double":
        nv = int(voices or 2)
        base = [float(delay_ms)] if delay_ms is not None else [14.0, 23.0, 30.0, 11.0]
        for v_ in range(nv):
            dm = base[v_ % len(base)] if delay_ms is None else float(delay_ms) + 6.0 * v_
            y, d = _vardelay(mono, sr, dm, float(drift_cents), 0.35 + 0.15 * v_, rng)
            pan = 0.0 if nv == 1 else (-width if v_ % 2 == 0 else width) * (1 - 0.2 * (v_ // 2))
            layers.append((y, pan, f"double {v_ + 1}"))
            info[f"double {v_ + 1}"] = {"delay_ms": round(float(np.mean(d)), 1), "delay_range_ms": [round(float(d.min()), 1), round(float(d.max()), 1)],
                                       "drift_cents_max": round(float(drift_cents), 1), "pan": round(pan, 2)}
        quality = "natural: the take itself, delayed 10-30 ms with a slow pitch/timing wander (no pitch-shifter artefacts)"
    else:
        if kind in ("octave_up", "octave_down"):
            semis = 12 if kind == "octave_up" else -12
            y = _shift(mono, sr, semis, formant)
            layers.append((y, 0.0, kind)); info[kind] = {"semitones": semis}
            if voices and int(voices) >= 2:
                y2, _ = _vardelay(y, sr, 12.0, 6.0, 0.4, rng)
                layers = [(y, -width, kind + " L"), (y2, width, kind + " R")]
        else:
            if not key:
                key, ks = _project_key(project)
                if not key:
                    f0, _, _ = pitch_track(_mono(x), sr)
                    key, conf = _estimate_key([f0]); ks = f"estimated from the take ({conf:.2f})"
                info["key_source"] = ks
            if not key:
                raise ValueError("no key: give key='A minor' (or a project with notes)")
            pcs = _key_pcs(key)
            f0, _, _ = pitch_track(_mono(x), sr)
            st_list = [2] if kind == "third_up" else ([int(s) for s in steps] if isinstance(steps, (list, tuple)) else
                                                      [int(steps)] if steps is not None else [2, 4])
            for si, stp in enumerate(st_list):
                iv = _harmony_intervals(f0, pcs, stp)
                vals = sorted(set(int(round(v)) for v in iv))
                fs = np.clip((np.arange(n) / sr / HOP_S).astype(int), 0, len(iv) - 1)
                ivs = np.round(iv[fs]).astype(int)
                y = np.zeros((n, 1))
                L = max(1, int(0.02 * sr))
                kern = np.ones(L) / L
                for v in vals:                                    # each interval shifted only where it is used
                    wgt = np.convolve((ivs == v).astype(float), kern, mode="same")
                    for a_, b_ in _spans(wgt > 0, int(0.1 * sr), n):
                        y[a_:b_, 0] += _shift(mono[a_:b_], sr, v, formant)[:, 0] * wgt[a_:b_]
                pan = 0.0 if len(st_list) == 1 else (-width if si % 2 == 0 else width)
                lab = {2: "third up", 4: "fifth up", -2: "third down", -3: "fourth down", 5: "sixth up", 7: "octave up"}.get(stp, f"{stp:+d} degrees")
                layers.append((y, pan, lab))
                uniq, cnt = np.unique(np.round(iv[np.isfinite(f0)]).astype(int), return_counts=True)
                info[lab] = {"steps": stp, "semitones_used": {int(u): int(c) for u, c in zip(uniq, cnt)}}
            info["key"] = key
        quality = ("phase-vocoder pitch shift" + (" with formant correction (cepstral envelope)" if formant else "") +
                   ": fine as a background layer; an octave shift still sounds processed - keep it low in the mix")
    mark("render")
    stereo = any(p != 0 for _, p, _ in layers) or x.shape[1] > 1
    out = np.zeros((n, 2 if stereo else 1))
    for y, pan, _ in layers:
        if stereo:
            th = (pan + 1) * np.pi / 4
            out[:, 0] += y[:, 0] * np.cos(th); out[:, 1] += y[:, 0] * np.sin(th)
        else:
            out[:, 0] += y[:, 0]
    pk = np.abs(out).max()
    src_pk = np.abs(x).max()
    if pk > 0:
        out *= min(1.0, src_pk / pk) * 0.98 if pk > src_pk else 1.0
    _save_wav(out_path, out, sr, bits if bits in (16, 24) else 24)
    mark("write")
    return {"output": out_path, "kind": kind, "layers": info, "stereo": stereo, "quality": quality, "steps_s": st,
            "note": "sit doubles 6-10 dB under the lead and harmonies lower still; listen before keeping it"}


# ============================================================================= balance (measuring core)
def balance_measure(vocal, beat, sr, sections, style):
    """Per section: vocal minus beat loudness in the vocal band, only while the vocal sings; beat vs vocal in the
    1-4 kHz duck band. vocal / beat: (n, ch) arrays at the same rate and alignment."""
    cfg = _data("balance.json")
    lo, hi = cfg["vocal_band_hz"]; dlo, dhi = cfg["duck_band_hz"]
    norm = cfg["norms_lu"].get(style or "", cfg["norms_lu"]["default"])
    n = min(len(vocal), len(beat))
    vocal, beat = _2d(vocal)[:n], _2d(beat)[:n]
    A = analyze(vocal, sr, detect=False)
    act = A["pmask"]
    lufs = H["_lufs"]
    rows = []
    for sec in sections:
        a, b = int(sec["start_s"] * sr), min(n, int(sec["end_s"] * sr))
        fa, fb = int(sec["start_s"] / HOP_S), min(len(act), int(sec["end_s"] / HOP_S))
        on = np.flatnonzero(act[fa:fb]) + fa
        if b - a < sr or len(on) * HOP_S < 1.0:
            rows.append({"name": sec.get("name"), "vocal": False}); continue
        idx = np.concatenate([np.arange(int(f * HOP_S * sr), int((f + 1) * HOP_S * sr)) for f in on])
        idx = idx[idx < n]
        def band(y, l, h):
            return np.stack([_band_fft(y[a:b, c], sr, l, h) for c in range(y.shape[1])], axis=1)[idx - a]
        vb, bb = band(vocal, lo, hi), band(beat, lo, hi)
        if len(vb) < int(0.45 * sr):
            rows.append({"name": sec.get("name"), "vocal": False}); continue
        Lv, Lb = lufs(vb, sr), lufs(bb, sr)
        vd, bd = band(vocal, dlo, dhi), band(beat, dlo, dhi)
        dv = _db(np.mean(vd ** 2)) - _db(np.mean(bd ** 2))
        diff = (Lv - Lb) if (Lv is not None and Lb is not None) else None
        r = {"name": sec.get("name"), "vocal": True, "vocal_over_beat_lu": _r(diff, 1), "duck_band_diff_db": _r(dv, 1)}
        if diff is None:
            r["status"] = "unmeasurable"
        elif diff < norm[0]:
            r["status"] = "buried"; r["move_db"] = round(min(cfg["max_move_db"], (norm[0] + norm[1]) / 2 - diff) * 2) / 2
        elif diff > norm[1]:
            r["status"] = "too loud"; r["move_db"] = -round(min(cfg["max_move_db"], diff - (norm[0] + norm[1]) / 2) * 2) / 2
        else:
            r["status"] = "on top"; r["move_db"] = 0.0
        rows.append(r)
    moves = [r["move_db"] for r in rows if r.get("vocal") and "move_db" in r]
    overall = float(np.median(moves)) if moves else 0.0
    spread = (max(moves) - min(moves)) if moves else 0.0
    dd = [r["duck_band_diff_db"] for r in rows if r.get("duck_band_diff_db") is not None]
    duck = 0.0
    if dd and np.median(dd) < norm[0]:
        duck = round(min(cfg["duck"]["max_db"], max(cfg["duck"]["depth_db"], (norm[0] - float(np.median(dd))) / 2)) * 2) / 2
    return {"norm_lu": norm, "sections": rows, "vocal_move_db": round(overall * 2) / 2, "spread_db": round(spread, 1),
            "duck_db": duck, "phrases_s": [(round(a_ * HOP_S, 3), round(b_ * HOP_S, 3)) for a_, b_ in A["phrases"]]}


# ============================================================================= live FL helpers
FL_SIDE_REQUEST = ("the bridge script lacks '{cmd}' (requested from the fl_script owner: see checklists/vocal.md); "
                   "until it ships: {fallback}")


def _bridge(cmd, timeout=8.0, **a):
    return H["BRIDGE"].call(cmd, timeout=timeout, **a)


def _close_fl_menus(warn=None):
    """Close FL's small leftover menus/pickers (e.g. the mixer's 'External input' list) that block transport calls.
    Never touches save questions or file dialogs."""
    try:
        for d in H["fl_dialogs"]():
            t = str(d.get("title") or "").lower()
            if d.get("kind") in ("save_changes", "save_as") or d.get("file_dialog"):
                continue
            if t in ("external input", "external output") or d.get("class") in ("TQuickMenu", "TPopupMenu"):
                H["fl_dialog"]("click", "escape", d["hwnd"])
                if warn is not None:
                    warn.append(f"closed FL's '{d.get('title')}' popup first")
    except Exception:
        pass


def _bridge_try(cmd, timeout=8.0, **a):
    """(True, reply) or (False, None) when this FL-side script doesn't know the command yet."""
    try:
        return True, _bridge(cmd, timeout=timeout, **a)
    except RuntimeError as e:
        if "unknown command" in str(e) or "unknown op" in str(e):
            return False, None
        raise


def _need_fl():
    if not H["fl_running"]():
        raise RuntimeError("FL isn't open: open the song in FL first (fl_open)")


def _project_or_open(project=None):
    if project:
        return project
    try:
        return H["_open_project_guess"]()[0]
    except Exception:
        return None


def _journal(project):
    if not project:
        return {}
    try:
        import flh_store
        return flh_store.journal_get(project)
    except Exception:
        return {}


def _journal_set(project, set=None, add=None):
    if not project:
        return None
    try:
        import flh_store
        flh_store.journal_update(project, set=set, add=add)
        return "saved"
    except Exception as e:
        return f"not saved ({type(e).__name__}: {e})"


# PC-VERIFY: an unnamed insert reads back as "Insert N" (mixer.getTrackName) so free inserts are recognised
_DEFAULT_NAME = re.compile(r"^\s*(insert\s*\d*|)\s*$", re.I)


def _mixer_overview():
    """[{index, name, armed?, effects?}] for every insert, by the best means this bridge has."""
    ok, r = _bridge_try("mixer_info", tracks="all")
    if ok and isinstance(r, dict) and r.get("tracks"):
        return r["tracks"], "mixer_info"
    st = H["fl_state"]()
    known = {m["index"]: dict(m) for m in st.get("mixer", [])}
    used = {c.get("insert") for c in st.get("channels", [])}
    out = []
    for t in range(1, 126):
        if t in known:
            out.append(dict(known[t], used=True)); continue
        try:
            m = H["fl_mixer_set"](t)                              # no changes: just reads the track back
        except RuntimeError:
            break                                                 # past the last track
        out.append({"index": t, "name": m.get("name"), "used": t in used, "routes_to": m.get("routes_to")})
        if _DEFAULT_NAME.match(m.get("name") or "") and t not in used:
            break                                                 # first free one is all we need
    return out, "state + probing"


def _find_vocal_track(project=None, track=None):
    if track is not None:
        return int(track), "given"
    v = (_journal(project).get("vocal") or {}).get("track")
    if v is not None:
        return int(v), "journal"
    try:
        st = H["fl_state"]()
        for m in st.get("mixer", []):
            if re.search(r"vocal|vox|voice|lead v", m.get("name") or "", re.I):
                return int(m["index"]), f"mixer track named '{m['name']}'"
    except Exception:
        pass
    return None, None


def audio_device_report():
    # PC-VERIFY: registry names under Devices\Audio output (Device name / Sample rate / Buffer length) for ASIO and
    # non-ASIO drivers, and whether the buffer value is in samples
    """FL's audio device, sample rate and buffer (read-only from FL's registry settings) with tracking advice."""
    try:
        s = H["fl_settings_summary"]()
    except Exception as e:
        return {"readable": False, "why": f"{type(e).__name__}: {e}",
                "advice": ["Options > Audio settings: pick your interface's own ASIO driver (or FL Studio ASIO)",
                           "buffer 256 samples or less while recording (about 5.8 ms at 44.1 kHz)"]}
    a = s.get("audio", {})
    dev = (a.get("device") or {}).get("value"); rate = (a.get("sample_rate") or {}).get("value")
    buf = (a.get("buffer_length") or {}).get("value")
    extra = {}
    try:
        fk = H["_fl_key"](); d = H["_reg_dump"](fk + "\\Devices")
        for k, vals in d.items():
            for name, (typ, val) in vals.items():
                if re.search(r"buffer|asio|latency|sample rate|rate", name, re.I) and len(extra) < 8:
                    extra[f"{k.split(chr(92))[-1]}: {name}"] = val
    except Exception:
        pass
    def num(v):
        try:
            return float(str(v).split()[0])
        except (TypeError, ValueError, IndexError):
            return None
    r, b = num(rate), num(buf)
    kind = "unknown"
    if dev:
        dl = str(dev).lower()
        kind = "FL Studio ASIO" if "fl studio asio" in dl else ("ASIO" if "asio" in dl else "DirectSound/WASAPI")
    out = {"readable": True, "device": dev, "driver": kind, "sample_rate": rate, "buffer": buf, "related_settings": extra}
    adv = []
    if r and b and 16 <= b <= 16384:
        ms = b / r * 1000.0
        out["buffer_ms"] = round(ms, 1); out["round_trip_ms_approx"] = round(2 * ms + 1.5, 1)
        out["buffer_note"] = "computed assuming the buffer value is in samples"
        if b > 256:
            adv.append(f"buffer {int(b)} samples = {ms:.1f} ms each way: lower it to 256 or less while recording "
                       "(raise it again for mixing)")
    elif b is not None:
        out["buffer_note"] = "buffer unit unclear: check Options > Audio settings"
    if kind == "DirectSound/WASAPI":
        adv.append("switch Options > Audio settings > Device to your interface's ASIO driver (or FL Studio ASIO) "
                   "for low-latency monitoring")
    elif kind == "FL Studio ASIO":
        adv.append("FL Studio ASIO works; your interface's own ASIO driver (if it has one) usually has lower latency")
    adv.append("monitor without heavy effects on the vocal track while recording (or use the interface's direct monitoring)")
    out["advice"] = adv
    return out


def _recorded_dirs(project):
    # PC-VERIFY: where FL 2024/2025 writes recorded audio (default and with a project data folder set)
    """Where FL may write recorded audio: FLH_RECORDED_DIR, <user data>/Audio/Recorded, <user data>/Recorded, the
    project's data folder / folder 'Recorded'."""
    out = []
    if os.environ.get("FLH_RECORDED_DIR"):
        out.append(os.environ["FLH_RECORDED_DIR"])
    try:
        ud = H["_fl_userdata"]()
        if ud:
            out += [os.path.join(ud, "Audio", "Recorded"), os.path.join(ud, "Recorded"),
                    # FL 2026 keeps its user data one level down: <Documents>\Image-Line\FL Studio\FL Studio\Audio\
                    # Recorded (PC 2026-10-20: the take was there and fl-headless never looked)
                    os.path.join(ud, "FL Studio", "Audio", "Recorded"), os.path.join(ud, "FL Studio", "Recorded")]
    except Exception:
        pass
    if project:
        pd = os.path.dirname(os.path.abspath(project))
        stem = os.path.splitext(os.path.basename(project))[0]
        # PC-VERIFY: FL's per-project folder for recordings (a project saved with its own data folder / as a folder)
        out += [os.path.join(pd, "Recorded"), os.path.join(pd, "Audio", "Recorded"), os.path.join(pd, stem, "Recorded")]
        try:
            df = H["flp_project_info"](project).get("data_folder")
            if df:
                out.append(os.path.join(df, "Recorded")); out.append(df)
        except Exception:
            pass
    out += _edison_dirs()                                         # last: a take FL routed into Edison and saved
    return [d for d in dict.fromkeys(out) if d and os.path.isdir(d)]


def _edison_dirs():
    # PC-VERIFY: where Edison saves (PC 2026-10-23: its Save As opened in <Documents>\Image-Line\FL Studio\Edison\
    # Data\Samples while FL's own Recorded folder was one level down, ...\FL Studio\FL Studio\Audio\Recorded)
    """Edison's own sample folders in FL's user data (<user data>\\Edison\\Data\\Samples, also one level down)."""
    roots = []
    try:
        ud = H["_fl_userdata"]()
        if ud:
            roots += [ud, os.path.join(ud, "FL Studio"), os.path.dirname(ud)]
    except Exception:
        pass
    try:
        for d in H["_documents_dirs"]():
            roots += [os.path.join(d, "Image-Line", "FL Studio"), os.path.join(d, "Image-Line", "FL Studio", "FL Studio")]
    except Exception:
        pass
    out = []
    for r in roots:
        out += [os.path.join(r, "Edison", "Data", "Samples"), os.path.join(r, "Audio", "Edison")]
    return [d for d in dict.fromkeys(out) if os.path.isdir(d)]


def _is_edison_dir(p):
    return any(os.path.normcase(os.path.abspath(p)).startswith(os.path.normcase(os.path.abspath(d)) + os.sep)
               for d in _edison_dirs())


def _edison_on(track, info=None):
    """The Edison plugins in a mixer insert's effect slots (mixer_info), or [] / None when unknown."""
    if track is None:
        return None
    if info is None:
        ok, info = _bridge_try("mixer_info", tracks=str(track))
        if not ok:
            return None
    tr = ((info or {}).get("tracks") or [{}])[0] if isinstance(info, dict) else {}
    fx = tr.get("effects")
    if fx is None:
        return None
    return [f for f in fx if "edison" in str(f or "").lower()]


EDISON_FIX = ("FL sent the take to Edison instead of the playlist. Check, in this order: (1) right-click FL's Record "
              "button (top toolbar): 'Audio, into the playlist as an audio clip' must be ticked - not only 'into "
              "Edison'; (2) mixer insert {t}: no Edison in its effect slots (it captures the input itself): remove it "
              "(slot menu > Delete) or switch the slot off; (3) close Edison's window, then record again")
EDISON_INTO = ("FL's recording went into Edison (FL's record button menu is set to record into Edison), so no take file "
               "reached the playlist or FL's Recorded folder - the take is only in Edison's window (press Save there to "
               "keep it)")
EDISON_NEXT = ("Right-click FL's Record button and pick 'Audio, into the playlist as an audio clip', remove Edison from "
               "the Vocal insert ({t}: slot menu > Delete), close Edison's window, and record again")
EDISON_SAVE_WAIT_S = 3.0                                          # Edison's Save As coming up after the pass (scaled)


def _scaled_s(seconds):
    try:
        return float(H["_scaled"](seconds))
    except Exception:
        return float(seconds)


def _save_dialogs(before):
    """New Windows Save As dialogs (opened after `before` = hwnds) that save audio (Edison's 'Save sample as'),
    never FL's project Save As."""
    out = []
    try:
        ds = H["fl_dialogs"]()
    except Exception:
        return out
    for d in ds:
        if d.get("hwnd") in before or not (d.get("file_dialog") or d.get("kind") == "save_as"):
            continue
        try:
            info = H["_file_dialog_info"](d["hwnd"]) or {}
        except Exception:
            info = {}
        txt = " ".join([str(x) for x in info.get("combos") or []] +
                       [str(e[1] if isinstance(e, (list, tuple)) else e) for e in info.get("edits") or []] +
                       [str(d.get("title") or "")] + [str(x) for x in d.get("text") or []]).lower()
        if ".flp" in txt or "project" in txt:
            continue                                              # FL's own project Save As: not ours to answer
        if re.search(r"\bwav|wave|\.flac|\.mp3|untitled|edison|sample", txt):
            d = dict(d, _info=info)
            out.append(d)
    return out


def _handle_edison_save(before, project, take_no, warn):
    # PC-VERIFY: after a take recorded into Edison, which action opens Edison's Save As (PC 2026-10-23: a modal
    # 'Save As' in Edison\Data\Samples, file name 'untitled_2', type 'Microsoft wave file') - and that typing a full
    # path into it saves the take there
    """Answer an Edison 'Save As' that recording left open: save the take into the song's own folder (Recorded\\
    '<song> Vocal take N.wav'); with no song (a test take) cancel it. Never leaves the modal dialog open.
    -> [saved wav paths]."""
    saved = []
    for d in _save_dialogs(before):
        target = None
        if project:
            song = (_journal(project).get("song") or os.path.splitext(os.path.basename(project))[0])
            folder = os.path.join(os.path.dirname(os.path.abspath(project)), "Recorded")
            n = take_no
            while True:
                target = os.path.join(folder, f"{song} Vocal take {n}.wav")
                if not os.path.exists(target):
                    break
                n += 1
            try:
                H["writable"](target)
                os.makedirs(folder, exist_ok=True)
            except Exception as e:
                warn.append(f"can't save Edison's take into the song folder ({type(e).__name__}: {e})")
                target = None
        ok = False
        if target:
            try:
                ok = bool(H["_file_dialog_submit"](d["hwnd"], target))
            except Exception as e:
                warn.append(f"Edison's Save As didn't take the file name ({type(e).__name__})")
            if ok and _wait_stable([target]) and os.path.isfile(target):
                saved.append(os.path.normcase(os.path.abspath(target)))
                warn.append(f"FL recorded into Edison: its Save As was answered - the take is saved as {target}")
                take_no += 1
                continue
            if ok:
                warn.append(f"answered Edison's Save As with {target}, but no file appeared there: in Edison press "
                            "Save (or drag the take into the playlist)")
                continue
        still = any(x.get("hwnd") == d["hwnd"] for x in (H["fl_dialogs"]() or []))
        if still:
            try:
                H["_file_dialog_cancel"](d["hwnd"])
            except Exception:
                pass
            still = any(x.get("hwnd") == d["hwnd"] for x in (H["fl_dialogs"]() or []))
        warn.append(("Edison's Save As is still open - press Cancel in it" if still else
                     "cancelled Edison's Save As (a test take with no song to keep it in)" if not project else
                     "cancelled Edison's Save As: the take is still in Edison - press Save in Edison "
                     "(or drag it into the playlist)"))
    return saved


def _audio_snapshot(dirs):
    snap = {}
    for d in dirs:
        for p in glob.glob(os.path.join(glob.escape(d), "**", "*"), recursive=True):
            if p.lower().endswith(AUDIO_EXT):
                try:
                    snap[os.path.normcase(os.path.abspath(p))] = (os.path.getmtime(p), os.path.getsize(p))
                except OSError:
                    pass
    return snap


def _wait_stable(paths, wait_s=6.0):
    """Wait until FL has finished writing the files (size unchanged for 0.5 s)."""
    t0 = time.time(); last = None
    while time.time() - t0 < wait_s:
        cur = tuple(os.path.getsize(p) if os.path.isfile(p) else -1 for p in paths)
        if cur == last:
            return True
        last = cur; time.sleep(0.5)
    return False


# ============================================================================= tools: setup, level, record
def fl_vocal_setup(track=None, input=None, name="Vocal", color=None, arm=True, project=None):
    """Pick / prepare the vocal mixer track: name + dark color, arm it, report the audio device and latency."""
    _need_fl()
    st, mark = _steps()
    project = _project_or_open(project)
    res = {"steps_s": st}
    tracks, how = _mixer_overview()
    by = {int(m["index"]): m for m in tracks}
    chosen, why = None, None
    if track is not None:
        chosen, why = int(track), "given"
    else:
        t, src = _find_vocal_track(project)
        if t is not None:
            chosen, why = t, f"already set up ({src})"
        else:
            for m in sorted(tracks, key=lambda m: m["index"]):
                if m["index"] >= 1 and not m.get("used") and _DEFAULT_NAME.match(m.get("name") or "") \
                        and not m.get("effects"):
                    chosen, why = int(m["index"]), "first free insert (no channel, default name, no effects)"
                    break
    if chosen is None:
        raise RuntimeError("no free mixer insert found: give track=<n>")
    res.update(track=chosen, picked=why, scanned_with=how)
    mark("pick")
    prof = {}
    try:
        import flh_store
        prof = flh_store.profile_get()
    except Exception:
        pass
    col = color or ((prof.get("palette") or {}).get("vocal")) or VOCAL_COLOR
    m = H["fl_mixer_set"](chosen, name=name, color=col)
    res["mixer"] = {"name": m.get("name"), "color": m.get("color")}
    mark("name_color")
    # PC-VERIFY: mixer.armTrack toggles arming; check the track's record button lights up and isTrackArmed reads back
    if arm:
        ok, r = _bridge_try("mixer_arm", track=chosen, armed=True)
        if ok:
            res["armed"] = bool(r.get("armed")); res["recording_file"] = r.get("recording_file")
        else:
            res["armed"] = None
            res["arm_how"] = FL_SIDE_REQUEST.format(cmd="mixer_arm", fallback="click the round record-arm button at "
                                                    f"the bottom of mixer insert {chosen}")
    mark("arm")
    # FL's scripting API has no call for a mixer track's audio input
    # PC-VERIFY: the wording/location of the insert input selector in FL 2024+ (top right of the mixer)
    res["input"] = {"wanted": input, "set": False,
                    "how": f"Mixer > select insert {chosen} > the audio input selector at the top right of the mixer "
                           "(the 'IN' / input dropdown) > pick your mic's input (e.g. 'In 1' for a mono mic). "
                           "FL's scripting API can't choose inputs, so this one click is yours."}
    # fl_vocal_setup never loads Edison. FL itself puts Edison on the selected insert when its record filter is set to
    # 'into Edison' - then Edison captures the take and nothing reaches the playlist / Recorded folder
    # PC-VERIFY: mixer_info's effects list names an Edison loaded in a slot of the vocal insert as 'Edison'
    ed = _edison_on(chosen) if by.get(chosen) is None or "effects" not in by.get(chosen, {}) else \
        [f for f in by[chosen].get("effects") or [] if "edison" in str(f or "").lower()]
    if ed:
        res["edison_on_insert"] = ed
        res.setdefault("warnings", []).append(
            f"insert {chosen} has Edison in an effect slot: it takes the recording instead of the playlist - remove "
            "it (slot menu > Delete) unless you want takes in Edison (then fl_vocal_record saves Edison's take into "
            "the song folder when Edison asks)")
    # FL's API can't read the record filter (right-click menu of the Record button), so it is a check for the user
    res["record_mode"] = ("right-click FL's Record button: 'Audio, into the playlist as an audio clip' should be ticked "
                          "(takes then land in the playlist + FL's Recorded folder); 'into Edison' sends them to Edison")
    res["audio"] = audio_device_report()
    mark("device")
    if project:
        res["journal"] = _journal_set(project, set={"vocal": {"track": chosen, "name": name,
                                                              **({"input": input} if input else {})}})
    res["next"] = C.tool_call(H, "fl_vocal_level_check", {"seconds": 5}) + " while you sing the loudest line"
    return res


def _poll_peaks(track, seconds):
    vals = []; t0 = time.time()
    while time.time() - t0 < seconds:
        r = _bridge("mixer_peaks", tracks=str(track), timeout=3.0)
        pk = r.get("peaks", {}) if isinstance(r, dict) else {}
        v = pk.get(track, pk.get(str(track)))
        if isinstance(v, (list, tuple)):
            v = max(float(z) for z in v)
        if v is not None:
            vals.append(float(v))
        time.sleep(0.03)
    return np.array(vals)


def fl_vocal_level_check(seconds=5, track=None, play=False, start_bar=None, project=None):
    """Measure the mic level while the user sings, and say how to set the input gain (peaks -10..-6 dBFS)."""
    _need_fl()
    project = _project_or_open(project)
    t, src = _find_vocal_track(project, track)
    if t is None:
        raise ValueError("which mixer track is the vocal? run fl_vocal_setup first or give track=")
    seconds = max(2.0, min(30.0, float(seconds)))
    started = False
    if play:
        bpb = H["_open_bpb"]()
        if start_bar is not None:
            _bridge("song", action="goto", bar=float(start_bar), bpb=bpb)
        H["fl_transport"]("play"); started = True
    method = "mixer peak meter"
    try:
        # PC-VERIFY: mixer.getTrackPeaks(t, 2) follows the insert's meter (input monitoring, also while stopped)
        ok, _ = _bridge_try("mixer_peaks", tracks=str(t), timeout=3.0)
        if ok:
            lin = _poll_peaks(t, seconds)
            peak = float(lin.max()) if len(lin) else 0.0
            active = lin[lin > 10 ** (-50 / 20)]
            typical = float(np.percentile(active, 75)) if len(active) else 0.0
            clipped = int((lin >= 1.0).sum())
        else:
            import fl_capture as C
            okc, why = C.available()
            if not okc:
                return {"measured": False, "track": t, "why": FL_SIDE_REQUEST.format(
                    cmd="mixer_peaks", fallback=f"and speaker capture is unavailable ({why}): watch insert {t}'s meter - "
                    "loudest words should peak around -10..-6 dB, never red")}
            method = "FL's output (speaker loopback): includes the fader and master, so it is only a rough guide"
            sr, x = C.record(seconds)
            a = np.abs(np.asarray(x, dtype=np.float64))
            peak = float(a.max()) if a.size else 0.0
            env = _env_db(_mono(np.asarray(x, dtype=np.float64)), sr)
            act = env[env > -50]
            typical = 10 ** (float(np.percentile(act, 90)) / 20) * 1.41 if len(act) else 0.0
            clipped = int((a >= 0.999).any(axis=1).sum()) if a.ndim == 2 else int((a >= 0.999).sum())
    finally:
        if started:
            H["fl_transport"]("stop")
    pk_db = 20 * math.log10(max(peak, 1e-9)); ty_db = 20 * math.log10(max(typical, 1e-9))
    res = {"measured": True, "track": t, "track_from": src, "method": method, "seconds": seconds,
           "peak_dbfs": round(pk_db, 1), "typical_peak_dbfs": round(ty_db, 1), "clipped_reads": clipped}
    target = -8.0
    if pk_db < -45:
        res["verdict"] = "no signal"
        res["advice"] = (f"nothing reached insert {t}: check the input selector on that insert, that the mic/interface "
                         "is on (phantom power for condenser mics), and that FL's audio device is the interface")
    elif clipped or pk_db > -3:
        res["verdict"] = "too hot"
        res["change_db"] = round(target - pk_db - (3 if clipped else 0), 1)
        res["advice"] = f"turn the interface's input gain DOWN about {abs(res['change_db']):.0f} dB" + \
                        (" - it clipped: that take would be distorted" if clipped else "") + \
                        " (FL can't change the analog preamp; the knob is on the interface)"
    elif pk_db < -14:
        res["verdict"] = "too quiet"
        res["change_db"] = round(target - pk_db, 1)
        res["advice"] = f"turn the interface's input gain UP about {res['change_db']:.0f} dB (aim: loudest words -10..-6 dBFS)"
    else:
        res["verdict"] = "good"; res["change_db"] = 0.0
        res["advice"] = "level is right: loudest words land between -10 and -6 dBFS with headroom for louder takes"
    res["note"] = ("meter-based: sing the LOUDEST part of the song; mixer meters read after the fader, so keep insert "
                   f"{t}'s fader at 0 dB while checking")
    return res


def _song(action, **a):
    return _bridge("song", action=action, bpb=a.pop("bpb", 4), timeout=4.0, **a)


_TAKE_NO_RE = re.compile(r"vocal take (\d+)(?:\.wav)?$", re.I)


def _next_take_no(project, song):
    """The next take number for this song: one past the highest 'Vocal take N' in the journal's takes and in the
    song's Recorded folder (a second fl_vocal_record call goes on at take 3, it doesn't name a new take 'take 1')."""
    hi = 0
    if not project:
        return 1
    try:
        for r in (_journal(project).get("vocal") or {}).get("takes") or []:
            m = _TAKE_NO_RE.search(str((r or {}).get("name") or "").strip()) if isinstance(r, dict) else None
            if m:
                hi = max(hi, int(m.group(1)))
    except Exception:                                             # noqa: BLE001 - a damaged journal: the files decide
        pass
    folder = os.path.join(os.path.dirname(os.path.abspath(project)), "Recorded")
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    pre = f"{song} ".lower()
    for f in names:
        m = _TAKE_NO_RE.search(f)
        if m and f.lower().startswith(pre):
            hi = max(hi, int(m.group(1)))
    return hi + 1


def _record(start_bar, end_bar, takes=1, loop=True, punch=True, pre_roll_bars=1, track=None, project=None):
    _need_fl()
    st, mark = _steps()
    project = _project_or_open(project)
    t, _ = _find_vocal_track(project, track)
    bpb = H["_open_bpb"]()
    start_bar, end_bar = float(start_bar), float(end_bar)
    if end_bar <= start_bar:
        raise ValueError("end_bar must be after start_bar")
    takes = max(1, min(20, int(takes)))
    from_bar = max(1.0, start_bar - max(0.0, float(pre_roll_bars or 0)))
    res = {"track": t, "range_bars": [start_bar, end_bar], "pre_roll_from_bar": from_bar, "steps_s": st}
    warn = []
    edison_fx = None
    if t is not None:
        ok, info = _bridge_try("mixer_info", tracks=str(t))
        if ok and isinstance(info, dict):
            tr = (info.get("tracks") or [{}])[0]
            if tr.get("armed") is False:
                okA, _ = _bridge_try("mixer_arm", track=t, armed=True)
                warn.append("armed the vocal track" if okA else "vocal track wasn't armed")
            edison_fx = _edison_on(t, info)                       # what's on the insert BEFORE recording: Edison
            if tr.get("effects"):                                 # appearing after the pass = FL put it there
                res["effects_before"] = [f for f in tr["effects"] if f][:10]
            if edison_fx:                                         # Edison on the insert captures the take itself
                res["edison_on_insert"] = edison_fx
                warn.append(f"insert {t} has Edison in an effect slot: the take may go into Edison, not the playlist "
                            "(remove it for playlist takes)")
    else:
        warn.append("no vocal track known: FL records every armed insert")
    try:                                                          # popups already open aren't this recording's
        dialogs_before = {d.get("hwnd") for d in H["fl_dialogs"]() if d.get("hwnd") is not None}
    except Exception:
        dialogs_before = set()
    # punch / loop range: only through the FL-side rec_setup command (the API can't set the time selection)
    _close_fl_menus(warn)                                         # an open FL menu makes transport calls 'unsafe'
    try:
        okR, rr = _bridge_try("rec_setup", punch_in_bar=start_bar if punch else None,
                              punch_out_bar=end_bar if punch else None, loop_record=False, bpb=bpb)
    except RuntimeError as e:
        if "unsafe" not in str(e).lower():
            raise
        # PC 2026-10-20: 'Operation unsafe at current time' (FL busy with a menu/popup): record without punch points
        okR, rr = False, None
        warn.append("FL wouldn't set punch points right now (busy); recording from the pre-roll bar instead")
    res["punch"] = ("set in FL" if okR and punch else
                    "off" if not punch else
                    FL_SIDE_REQUEST.format(cmd="rec_setup", fallback="each take records from the pre-roll bar to "
                                           "end_bar; fl_vocal_comp lines takes up by their start bar"))
    tempo = None
    try:
        tempo = float(H["fl_state"]().get("tempo") or 0) or None
    except Exception:
        pass
    dirs = _recorded_dirs(project)
    before = _audio_snapshot(dirs)
    try:
        chans_before = {c["index"]: c.get("name") for c in H["fl_state"]().get("channels", [])}
    except Exception:
        chans_before = {}
    mark("prepare")
    H["fl_transport"]("stop")
    was_song = bool(_song("where", bpb=bpb).get("song_mode"))
    if not was_song:
        _song("song_mode", bpb=bpb)
    pass_s = (end_bar - from_bar) * bpb * 60.0 / (tempo or 120.0)
    t_start = time.time(); passes = []; popup = None
    turned_on = False
    try:
        for k in range(takes):
            _song("goto", bar=from_bar, bpb=bpb); time.sleep(0.15)
            if not _song("where", bpb=bpb).get("recording"):
                _song("record", bpb=bpb); turned_on = True; time.sleep(0.15)
            # PC-VERIFY: with record armed, play starts recording on armed inserts; FL may first show its
            # "what do you want to record" popup - it is reported, never answered blindly
            H["fl_transport"]("play")
            # FL's recording countdown (on by default) holds the playhead still for about a bar first: a stall
            # before the first move isn't the end of the take (PC 2026-10-20: stopped after 2.5 s, nothing recorded)
            p0 = time.time(); prev = from_bar; last_move = time.time() + 2 * bpb * 60.0 / (tempo or 120.0)
            hard = p0 + _scaled_s(pass_s + 15)                    # wall clock, however slowly FL answers
            while True:
                if time.time() > hard:
                    break
                time.sleep(0.25)
                if k == 0 and popup is None and time.time() - p0 < 3.0:
                    try:                                          # FL's 'what to record' question; a file
                        dl = [d for d in H["fl_dialogs"]()        # dialog (Edison's Save As) is handled after
                              if d.get("hwnd") not in dialogs_before or d.get("hwnd") is None]
                        dl = [d for d in dl if not (d.get("file_dialog") or d.get("kind") == "save_as")]
                        if dl:
                            popup = dl; break
                    except Exception:
                        pass
                try:
                    b = _song("where", bpb=bpb)["bar"]
                except (TimeoutError, OSError):                   # one slow reply isn't the end of the take
                    continue
                if b > prev + 1e-3:
                    prev, last_move = b, time.time()
                if b >= end_bar or b < prev - 0.5 or time.time() - last_move > 2.5 or time.time() - p0 > pass_s + 15:
                    break
            H["fl_transport"]("stop"); time.sleep(0.8)
            passes.append({"take": k + 1, "seconds": round(time.time() - p0, 1), "reached_bar": round(prev, 2)})
            if popup:
                break
            _job_progress = H.get("_job_progress")
            if _job_progress:
                _job_progress(step=f"take {k + 1}/{takes}", pct=int(100 * (k + 1) / takes))
    finally:
        try:
            H["fl_transport"]("stop")
            if turned_on and _song("where", bpb=bpb).get("recording"):
                _song("record", bpb=bpb)
            if not was_song:
                _song("song_mode", bpb=bpb)
        except Exception:
            pass
    mark("record")
    res["passes"] = passes
    res["loop"] = ("takes recorded as back-to-back passes from the pre-roll (one file per pass, no hands needed)"
                   if loop else "single pass per call")
    if popup:
        res["popup"] = popup
        res["stopped"] = ("FL asked what to record. In FL's record button menu choose audio only (uncheck "
                          "automation/score) - or answer the popup - then run fl_vocal_record again")
        return res
    # collect what FL wrote
    after = _audio_snapshot(_recorded_dirs(project) or dirs)
    new = sorted([p for p, (mt, sz) in after.items() if (p not in before or before[p] != (mt, sz)) and mt >= t_start - 2],
                 key=lambda p: after[p][0])
    mark("scan")
    try:
        okI, info = _bridge_try("mixer_info", tracks=str(t)) if t is not None else (False, None)
    except Exception:                                             # FL slow to answer: the files decide
        okI, info = False, None
    edison_after = None
    if okI and isinstance(info, dict):
        rf = ((info.get("tracks") or [{}])[0]).get("recording_file")
        if rf and os.path.isfile(rf) and os.path.normcase(os.path.abspath(rf)) not in new:
            new.append(os.path.normcase(os.path.abspath(rf)))
        edison_after = _edison_on(t, info)                        # FL loads Edison on the insert when its record
        if edison_after and not edison_fx:                        # setting is 'into Edison' (PC 2026-10-23)
            res["edison_on_insert"] = edison_after; res["edison_loaded_by_fl"] = True
    song = (_journal(project).get("song") if project else None) or \
           (os.path.splitext(os.path.basename(project))[0] if project else "Song")
    first_take = _next_take_no(project, song)                   # 'Vocal take N' goes on from earlier recordings
    edison_win, from_edison = [], set()
    if len(new) < len(passes):
        # the take may sit in Edison (FL's record filter 'into Edison', or Edison on the insert): an Edison Save As
        # left open blocks FL - answer it (the song's folder) or cancel it, never leave it
        try:
            edison_win = [d.get("title") for d in H["fl_dialogs"]() if "edison" in str(d.get("title") or "").lower()]
        except Exception:
            edison_win = []
        saved = _handle_edison_save(dialogs_before, project, first_take + len(new), warn)
        if not saved and (edison_fx or edison_after or edison_win):   # Edison involved: its Save As may come a
            t_w = time.time(); cap = _scaled_s(EDISON_SAVE_WAIT_S)    # moment later - a few seconds, never minutes
            while time.time() - t_w < cap and not _save_dialogs(dialogs_before):
                time.sleep(0.25)
            saved = _handle_edison_save(dialogs_before, project, first_take + len(new), warn)
        for p in saved:
            if p not in new:
                new.append(p); from_edison.add(p)
        for p in new:
            if _is_edison_dir(p):
                from_edison.add(p)
        mark("edison_check")
        if not new and (edison_fx or edison_after or edison_win):
            # the take exists only in Edison's memory: no file will come - say so now, don't wait for one
            tn = t if t is not None else "N"
            res["edison"] = (edison_win or edison_after or edison_fx or ["Edison"])[:2]
            res["takes"] = []
            res["stopped"] = EDISON_INTO.format(t=tn)
            res["next_step"] = EDISON_NEXT.format(t=tn)
            res["warnings"] = warn + [EDISON_FIX.format(t=tn)]
            return res
    _wait_stable(new)
    take_rows = []
    # PC-VERIFY: with FL's punch range set, a take's file starts at the punch-in bar (not at the pre-roll)
    starts = start_bar if (okR and punch) else from_bar
    for i, p in enumerate(new[:takes]):
        take_rows.append({"name": f"{song} Vocal take {first_take + i}", "path": p, "starts_at_bar": starts,
                          "bars": [start_bar, end_bar], "when": time.strftime("%Y-%m-%d %H:%M"),
                          **({"source": "edison"} if p in from_edison else {})})
        if p in from_edison:                                      # Edison's take isn't on the playlist
            # PC-VERIFY: Edison's take starts where FL started recording (the pre-roll bar, not the punch-in)
            take_rows[-1]["starts_at_bar"] = from_bar
    # PC-VERIFY: each recorded pass shows up as a new audio clip channel in fl_state, in pass order
    # name the new audio clip channels in FL (the files keep FL's names: renaming them would break the clips)
    renamed = []
    try:
        chans = H["fl_state"]().get("channels", [])
        fresh = [c for c in chans if c["index"] not in chans_before]
        for i, c in enumerate(fresh[:len(take_rows)]):
            H["fl_channel_set"](c["index"], name=take_rows[i]["name"], color=VOCAL_COLOR)
            take_rows[i]["channel"] = c["index"]; renamed.append(c["index"])
    except Exception as e:
        warn.append(f"couldn't name the take channels ({type(e).__name__})")
    mark("collect")
    res["takes"] = [{k: v for k, v in r.items() if k != "when"} for r in take_rows]
    if from_edison or edison_win or ((edison_fx or edison_after) and len(new) < len(passes)):
        res["edison"] = (edison_win or edison_fx or ["Edison"])[:2]   # PC 2026-10-20/23: FL recorded into Edison
        warn.append(EDISON_FIX.format(t=t if t is not None else "N"))
    if len(new) < len(passes):
        tn = t if t is not None else "N"
        warn.append(f"found {len(new)} new audio file(s) for {len(passes)} pass(es). Check: (1) mixer insert {tn}'s "
                    "record-arm button (round, bottom of the insert) is lit; (2) its audio input (top right of the "
                    f"mixer with insert {tn} selected) shows your mic's input, e.g. 'In 1', and its meter moves when "
                    "you sing (fl_vocal_level_check); (3) right-click FL's Record button: 'Audio, into the playlist "
                    "as an audio clip' is ticked (not only 'into Edison'), and no Edison sits on the insert; "
                    f"(4) Options > Audio settings: the input device is your interface. Searched: "
                    f"{dirs or 'no recorded-audio folder found (set FLH_RECORDED_DIR)'}")
    if project and take_rows:
        old = (_journal(project).get("vocal") or {}).get("takes") or []
        res["journal"] = _journal_set(project, set={"vocal": dict({"takes": old + take_rows},
                                                                  **({"track": t} if t is not None else {}))})
    if warn:
        res["warnings"] = warn
    res["next"] = (C.tool_call(H, "fl_vocal_comp", {"takes": C.Raw("the take wavs")}) +
                   " picks the best take per phrase" if len(take_rows) > 1 else
                   C.tool_call(H, "fl_vocal_clean", {"wav": C.Raw("the take wav")}) +
                   " for breaths/clicks/pops/sibilance")
    return res


def fl_vocal_record(start_bar, end_bar, takes=1, loop=True, punch=True, pre_roll_bars=1, track=None, project=None,
                    background=None):
    bpm = 120.0
    try:
        bpm = float(H["fl_state"]().get("tempo") or 120.0)
    except Exception:
        pass
    est = (float(end_bar) - max(1.0, float(start_bar) - float(pre_roll_bars or 0))) * H["_open_bpb"]() * 60.0 / bpm
    est = est * max(1, int(takes)) + 3 * int(takes) + 5
    kw = dict(start_bar=start_bar, end_bar=end_bar, takes=takes, loop=loop, punch=punch, pre_roll_bars=pre_roll_bars,
              track=track, project=project)
    auto = H.get("_auto_job")
    if auto:
        return auto("vocal_record", est, _record, kw, background)
    return _record(**kw)


# ============================================================================= tools: chain
def _take_from_journal(project):
    v = _journal(project).get("vocal") or {}
    if (v.get("comp") or {}).get("path") and os.path.isfile(v["comp"]["path"]):
        return v["comp"]["path"], "journal comp"
    for t in reversed(v.get("takes") or []):
        if t.get("path") and os.path.isfile(t["path"]):
            return t["path"], "journal last take"
    return None, None


def _plugin_db_roots():
    if os.environ.get("FLH_PLUGIN_DB"):
        return [os.environ["FLH_PLUGIN_DB"]]
    out = []
    try:
        for d in H["_documents_dirs"]():
            out += [os.path.join(d, "Presets", "Plugin database"), os.path.join(d, "FL Studio", "Presets", "Plugin database")]
    except Exception:
        pass
    return [r for r in dict.fromkeys(out) if os.path.isdir(r)]


_FST_CACHE = {}


def _find_fst(plugin):
    """FL's own plugin-database entry (<Name>.fst) for a plugin, built by FL on this PC (never shipped by us)."""
    key = plugin.lower()
    if key in _FST_CACHE and os.path.isfile(_FST_CACHE[key]):
        return _FST_CACHE[key]
    for r in _plugin_db_roots():
        hits = glob.glob(os.path.join(glob.escape(r), "**", glob.escape(plugin) + ".fst"), recursive=True)
        hits.sort(key=lambda p: (0 if os.sep + "Installed" + os.sep in p else 1, len(p)))
        if hits:
            _FST_CACHE[key] = hits[0]
            return hits[0]
    return None


def _slot_plugins(project, track):
    """{slot: plugin name or None} of a mixer track, from the project file."""
    d = H["_load"](project); ev = H["_events"](d)
    out = {}
    for (t, s), (first, _) in H["_mixer_slots"](d, ev).items():
        if t == int(track):
            out[s] = H["_text16"](H["_payload"](d, ev[first])) if first is not None else None
    return out


_EQ_TYPES = {"high-pass": "High pass", "low shelf": "Low shelf", "high shelf": "High shelf", "bell": "Peaking"}


def _q_to_oct(q):
    return round(2.0 / math.log(2) * math.asinh(1.0 / (2.0 * float(q))), 2)


def _controls_for(stage):
    """A chain stage as plugin controls in real units, named like flh_mixeng's plugin maps (band2_freq_hz, ...)."""
    pl = stage.get("plugin") or ""
    out, by_hand = {}, []
    if stage.get("bands"):
        for i, b in enumerate(stage["bands"], start=1):
            out[f"band{i}_type"] = _EQ_TYPES.get(b["band"], "Peaking")
            out[f"band{i}_freq_hz"] = b["hz"]
            if b.get("gain_db") is not None:
                out[f"band{i}_gain_db"] = b["gain_db"]
            if b.get("q"):
                out[f"band{i}_width"] = _q_to_oct(b["q"])
            if b.get("slope"):
                by_hand.append(f"band {i} slope {b['slope']}")
        return out, by_hand
    st = stage.get("settings") or {}
    if pl == "Fruity Compressor":
        out = {k: st[k] for k in ("threshold_db", "ratio", "attack_ms", "release_ms", "gain_db") if k in st}
    elif pl == "Fruity Limiter":
        out = {"comp_" + k: st[k] for k in ("threshold_db", "ratio", "attack_ms", "release_ms") if k in st}
        if "gain_db" in st:                                       # FL exposes ONE gain (before the compressor) and
            out["limit_gain_db"] = st["gain_db"]                  # no 'mode' control: the comp knobs act on their own
    elif pl == "Fruity Multiband Compressor":
        out = {"high_freq_hz": st.get("crossover_hz"), "high_threshold_db": st.get("high_threshold_db")}
        by_hand.append(f"high band ratio about {st.get('high_ratio', 4)}:1, aiming at ~{st.get('target_reduction_db')} dB on the s sounds")
    elif pl == "Fruity Reeverb 2":
        out = {"decay_s": st.get("decay_s"), "predelay_ms": st.get("predelay_ms"), "low_cut_hz": st.get("low_cut_hz"),
               "high_cut_hz": st.get("high_cut_hz"), "wet_pct": 100.0 * st.get("wet", 1.0), "dry_pct": 100.0 * st.get("dry", 0.0)}
    elif pl == "Fruity Delay 3":
        out = {"feedback_pct": 100.0 * st.get("feedback", 0.25), "wet_pct": 100.0, "dry_pct": 0.0}
        by_hand.append(f"time {st.get('time_note')} (tempo-synced) = {st.get('time_ms')} ms")
    else:
        by_hand.append(", ".join(f"{k} {v}" for k, v in st.items()) or "settings by ear")
    return {k: v for k, v in out.items() if v is not None}, by_hand


def _set_params_own(track, slot, plugin, values):
    """Fallback without flh_mixeng: match parameter names (param_hints) and set numbers by FL's display text."""
    hints = _data("chains.json").get("param_hints", {}).get(plugin, {})
    params = H["fl_plugin_params"](track, slot).get("params", [])
    done, manual = [], []
    for key, val in values.items():
        mm = re.match(r"band(\d+)_(\w+)", key)
        hint = (hints.get("band{n}_" + mm.group(2)) or "").replace("{n}", mm.group(1)) if mm else hints.get(key)
        p = next((q for q in params if hint and hint.lower() in (q.get("name") or "").lower()), None)
        if p is None or isinstance(val, str):
            manual.append(f"{key} = {val}"); continue
        if re.search(r"(?i)gain|thresh|level|output|ceil|makeup|volume|wet|mix|drive", f"{key} {p.get('name')}"):
            manual.append(f"{key} = {val} (gain-type knob: set by hand; the display search would sweep it)")
            continue
        try:
            r = H["fl_plugin_set_value"](track, p["param"], float(val), slot)
            done.append({"param": p["name"], "want": val, "shows": r.get("text")})
        except Exception as e:
            manual.append(f"{key} = {val} ({type(e).__name__})")
    return done, manual


_INTENT_OF = {"corrective EQ": "less_muddy", "de-esser": "less_harsh", "compressor 1": "more_compressed",
              "tone EQ": "clearer"}


def _listen_solo(track, seconds, start_bar):
    """fl_listen of one mixer track alone (others muted for the pass, then restored) -> measurement dict."""
    st = H["fl_state"]()
    used = sorted({m["index"] for m in st.get("mixer", []) if m["index"] > 0} | {int(track)})
    was = {m["index"]: bool(m.get("muted")) for m in st.get("mixer", [])}
    try:
        for u in used:
            H["fl_mixer_set"](u, muted=(u != int(track)) or was.get(u, False))
        r = H["fl_listen"](start_bar=start_bar, seconds=seconds)
    finally:
        for u in used:
            try:
                H["fl_mixer_set"](u, muted=was.get(u, False))
            except Exception:
                pass
    return r.get("whole") if r.get("heard") else None


def _truthy(v):
    """True for True / 1 / 'true' / 'yes' / 'on' (tool arguments sometimes arrive as strings)."""
    return v is True or (isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0) or \
        str(v).strip().lower() in ("true", "1", "yes", "y", "on")


def _chain_state():
    try:
        return H["_state"]().get("vocal_chain") or {}
    except Exception:
        return {}


def _apply_chain(plan, track, project, verify_listen=None, start_bar=None, replace=False):
    """Load missing plugins into EMPTY slots and set them. A slot that already holds the same plugin is reused only
    when fl-headless loaded it there itself (a re-run) or replace=true; a plugin the user dialled in is never
    overwritten. Every knob's previous value is stored (fl_vocal_chain revert_last=true puts them back)."""
    _need_fl()
    if track is None:
        raise ValueError("apply needs the vocal mixer track (track=, or run fl_vocal_setup)")
    if not project:
        raise ValueError("apply needs the open project (project=)")
    stages = [c for c in plan["chain"] if c.get("plugin")]
    have = _slot_plugins(project, track)
    order = sorted(have)
    cs = _chain_state()
    ours = {int(k) for k in (cs.get("loaded") or {}).get(f"{os.path.normcase(os.path.abspath(project))}|{int(track)}", [])}
    used, assign, ops, missing, kept = set(), [], [], [], []
    for c in stages:
        s = next((k for k in order if k not in used and (have.get(k) or "").lower() == c["plugin"].lower()
                  and (replace or k in ours)), None)
        theirs = next((k for k in order if k not in used and (have.get(k) or "").lower() == c["plugin"].lower()
                       and not (replace or k in ours)), None)
        if s is None and theirs is not None:
            used.add(theirs)
            kept.append(f"{c['stage']}: slot {theirs} already has {c['plugin']} (left as you set it; replace=true "
                        f"reconfigures it)")
            continue
        if s is None:
            s = next((k for k in order if k not in used and not have.get(k)), None)
            if s is None:
                missing.append(f"{c['stage']}: no free slot on insert {track}"); continue
            if c.get("owned") is not True and c.get("owned") != "unknown":
                missing.append(f"{c['stage']}: {c['plugin']} not owned ({c.get('owned')})"); continue
            fst = _find_fst(c["plugin"])
            if not fst:
                missing.append(f"{c['stage']}: {c['plugin']} not found in FL's plugin database (open it once in FL)")
                continue
            ops.append({"tool": "flp_load_plugin", "preset": fst, "track": int(track), "slot": int(s)})
        used.add(s); assign.append((c, s))
    rep = {"track": int(track), "loaded": [], "set": [], "by_hand": [], "skipped": missing}
    if kept:
        rep["kept_yours"] = kept
    undo_all = []
    setter, verify = H.get("plugin_set_named"), H.get("intent_verify")
    before = None
    if verify_listen and verify:
        try:
            before = _listen_solo(track, verify_listen, start_bar)
        except Exception as e:
            rep["verify_note"] = f"no before-listen ({type(e).__name__}: {e})"
    if ops:
        # PC-VERIFY: flp_load_plugin with FL's plugin-database .fst loads the stock plugin in its default state
        H["flp_edit"](project=project, ops=ops)
        rep["loaded"] = [f"slot {o['slot']}: {os.path.splitext(os.path.basename(o['preset']))[0]}" for o in ops]
        key = f"{os.path.normcase(os.path.abspath(project))}|{int(track)}"
        lo = dict(cs.get("loaded") or {}); lo[key] = sorted(ours | {int(o["slot"]) for o in ops})
        try:
            H["_state"]({"vocal_chain": dict(_chain_state(), loaded=lo)})
        except Exception:
            pass
    readback_ok = []
    for c, s in assign:
        vals, manual = _controls_for(c)
        rep["by_hand"] += [f"slot {s} {c['plugin']}: {m_}" for m_ in manual]
        if not vals:
            continue
        # PC-VERIFY: control names / units / enum labels (flh_mixeng maps, or chains.json param_hints) on this FL
        r = None
        if setter:
            try:
                r = _call_flex(setter, track=int(track), slot=int(s), plugin=c["plugin"], values=vals)
                undo_all += [dict(u, kind="param") for u in (r.get("undo") or [])]
                ok = [d_ for d_ in (r.get("set") or []) if d_.get("ok")]
                bad = [d_["control"] for d_ in (r.get("set") or []) if not d_.get("ok")] + \
                      [k["control"] for k in (r.get("skipped") or [])]
                rep["set"].append({"slot": s, "plugin": c["plugin"], "via": "plugin_set_named", "set": len(ok),
                                   "unconfirmed": bad})
                readback_ok.append(bool(r.get("ok")))
                if bad:
                    rep["by_hand"].append(f"slot {s} {c['plugin']}: check " + ", ".join(f"{k} = {vals.get(k)}" for k in bad))
            except Exception as e:
                r = None
                if "no plugin map" not in str(e):                 # e.g. the slot holds another plugin: never guess
                    rep["set"].append({"slot": s, "plugin": c["plugin"], "via": "plugin_set_named",
                                       "error": f"{type(e).__name__}: {e}"})
                    rep["by_hand"].append(f"slot {s} {c['plugin']}: {vals}")
                    continue
        if r is None:
            done, man = _set_params_own(int(track), int(s), c["plugin"], vals)
            rep["set"].append({"slot": s, "plugin": c["plugin"], "via": "fl_plugin_set_value", "params": done})
            if man:
                rep["by_hand"].append(f"slot {s} {c['plugin']}: " + ", ".join(man))
    if undo_all:
        try:
            H["_state"]({"vocal_chain": dict(_chain_state(), undo=undo_all, undo_project=project)})
            rep["undo"] = (f"{len(undo_all)} knob(s) stored: " +
                           C.tool_call(H, "fl_vocal_chain", {"revert_last": True}) + " puts them back")
        except Exception as e:
            rep["undo"] = f"not stored ({type(e).__name__})"
    rep["read_back"] = ("every control confirmed by FL's display" if readback_ok and all(readback_ok) else
                        "some controls unconfirmed (see by_hand)" if readback_ok else "display text per parameter")
    intents = [i for st_, i in _INTENT_OF.items() if any(c["stage"] == st_ for c, _ in assign)]
    if verify and before is not None:
        try:
            after = _listen_solo(track, verify_listen, start_bar)
            rep["intent_check"] = _call_flex(verify, before=before, after=after, intents=intents)
        except Exception as e:
            rep["intent_check"] = f"failed ({type(e).__name__}: {e})"
    elif verify:
        rep["intent_check"] = f"not run: verify_listen=<seconds> listens to the vocal alone before/after ({intents})"
    return rep


def _revert_chain():
    cs = _chain_state(); n = 0; failed = 0
    for u in reversed(cs.get("undo") or []):
        try:
            H["BRIDGE"].call("plugin_set", index=int(u["track"]), slot=int(u["slot"]), param=int(u["param"]),
                             value=float(u["before"]))
            n += 1
        except Exception:
            failed += 1
    H["_state"]({"vocal_chain": dict(cs, undo=[])})
    out = {"reverted": n, "note": "knobs put back; plugins the chain loaded stay in their slots (remove by hand)"}
    if failed:
        out["failed"] = failed
    return out


def fl_vocal_chain(track=None, style="pop", apply=False, take=None, project=None, bpm=None, verify_listen=None,
                   start_bar=None, replace=False, revert_last=False):
    if _truthy(revert_last):
        _need_fl()
        return _revert_chain()
    st, mark = _steps()
    project = _project_or_open(project) if (apply or not take) else project
    src = "given"
    if not take and project:
        take, src = _take_from_journal(project)
    meas = None
    if take:
        x, sr, _ = _load_wav(take)
        meas = measure_take(x, sr)
    mark("measure")
    bpm_from = "given" if bpm else None
    if bpm is None and project:
        bpm, _ = _project_tempo_bpb(project)
        bpm_from = "project" if bpm else None
    plan = derive_chain(meas or {}, style, bpm, bpm_from)
    if meas is None:
        plan["measured"] = False
        plan["note"] = "no take measured (take= or a take in the journal): style defaults only"
        for c in plan["chain"]:
            if c["stage"] == "de-esser" and not c.get("plugin"):
                c.update(plugin=_data("chains.json")["plugins"]["deesser"], why="not measured: start gentle (3-4 dB)")
    else:
        plan["measured"] = {"take": take, "from": src, **meas}
    mark("derive")
    if apply:
        t, _ = _find_vocal_track(project, track)
        plan["applied"] = _apply_chain(plan, t, project, verify_listen, start_bar, replace=_truthy(replace))
        mark("apply")
    plan["steps_s"] = st
    return plan


# ============================================================================= tools: balance
def _sections_seconds(project, n_s, bpm, start_bar, bpb):
    secs = []
    j = _journal(project)
    if j.get("sections") and bpm:
        spb = 60.0 / bpm * bpb
        ss = sorted(j["sections"], key=lambda s: s["bar"])
        for i, s in enumerate(ss):
            end = s.get("end_bar") or (ss[i + 1]["bar"] if i + 1 < len(ss) else None)
            a = (float(s["bar"]) - start_bar) * spb
            b = (float(end) - start_bar) * spb if end else n_s
            if b > 0 and a < n_s:
                secs.append({"name": s["name"], "start_s": max(0.0, a), "end_s": min(n_s, b), "bar": s["bar"]})
    if not secs:
        w = 8 * bpb * 60.0 / bpm if bpm else 20.0
        k = 0
        while k * w < n_s - 1:
            secs.append({"name": f"bars {int(start_bar + k * 8)}-{int(start_bar + (k + 1) * 8)}" if bpm else
                         f"{int(k * w // 60)}:{int(k * w % 60):02d}", "start_s": k * w, "end_s": min(n_s, (k + 1) * w)})
            k += 1
    return secs


# PC-VERIFY: two live passes (vocal alone, beat alone) give section names/levels that match the song
def _live_balance(vocal_track, beat_tracks, style, project):
    """Two live passes (vocal alone, beat alone) through fl_listen; band loudness estimated from its 6-band tone."""
    st = H["fl_state"]()
    used = sorted({m["index"] for m in st.get("mixer", []) if m["index"] > 0})
    beat = [int(b) for b in beat_tracks] if beat_tracks else [u for u in used if u != int(vocal_track)]
    was = {m["index"]: bool(m.get("muted")) for m in st.get("mixer", [])}
    def mutes(keep):
        for u in used:
            H["fl_mixer_set"](u, muted=(u not in keep) or was.get(u, False))
    try:
        mutes({int(vocal_track)}); rv = H["fl_listen"]()
        mutes(set(beat)); rb = H["fl_listen"]()
    finally:
        for u in used:
            try:
                H["fl_mixer_set"](u, muted=was.get(u, False))
            except Exception:
                pass
    def band_l(sec):
        if sec.get("lufs") is None:
            return None, None
        bd = sec["bands_db"]
        vb = sec["lufs"] + 10 * math.log10(sum(10 ** (bd[k] / 10) for k in ("low_mid", "mid", "presence")))
        db = sec["lufs"] + 10 * math.log10(sum(10 ** (bd[k] / 10) for k in ("mid", "presence")))
        return vb, db
    cfg = _data("balance.json"); norm = cfg["norms_lu"].get(style or "", cfg["norms_lu"]["default"])
    rows = []
    pairs = list(zip(rv.get("sections") or [rv.get("whole")], rb.get("sections") or [rb.get("whole")]))
    for sv, sb in pairs:
        (v1, v2), (b1, b2) = band_l(sv or {}), band_l(sb or {})
        if v1 is None or b1 is None:
            rows.append({"name": (sv or {}).get("name"), "vocal": False}); continue
        diff = v1 - b1
        r = {"name": sv.get("name"), "vocal": True, "vocal_over_beat_lu": round(diff, 1), "duck_band_diff_db": round(v2 - b2, 1)}
        mid = (norm[0] + norm[1]) / 2
        r["status"] = "buried" if diff < norm[0] else ("too loud" if diff > norm[1] else "on top")
        r["move_db"] = 0.0 if r["status"] == "on top" else round(max(-cfg["max_move_db"], min(cfg["max_move_db"], mid - diff)) * 2) / 2
        rows.append(r)
    moves = [r["move_db"] for r in rows if r.get("vocal")]
    dd = [r["duck_band_diff_db"] for r in rows if r.get("vocal")]
    duck = 0.0
    if dd and np.median(dd) < norm[0]:
        duck = round(min(cfg["duck"]["max_db"], max(cfg["duck"]["depth_db"], (norm[0] - float(np.median(dd))) / 2)) * 2) / 2
    return {"norm_lu": norm, "sections": rows, "vocal_move_db": round(float(np.median(moves)) * 2) / 2 if moves else 0.0,
            "spread_db": round(max(moves) - min(moves), 1) if moves else 0.0, "duck_db": duck, "beat_tracks": beat,
            "method": "two live passes (vocal solo, beat solo) measured from the speakers; band levels estimated "
                      "from 6-band tone, gaps between phrases partly included - a rough guide"}


def _duck_points(phrases, start_bar, bpm, bpb, depth_db, base_db, attack_s, release_s):
    spbar = bpb * 60.0 / bpm
    merged = []
    for a, b in phrases:
        a, b = a - attack_s, b + release_s
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    pts = []
    to_bar = lambda s: start_bar + max(0.0, s) / spbar
    for a, b in merged:
        pts += [{"bar": round(to_bar(a), 4), "db": base_db}, {"bar": round(to_bar(a + attack_s), 4), "db": base_db - depth_db},
                {"bar": round(to_bar(b - release_s), 4), "db": base_db - depth_db}, {"bar": round(to_bar(b), 4), "db": base_db}]
    out = []
    for p in pts:
        if out and p["bar"] <= out[-1]["bar"]:
            continue
        out.append(p)
    return out


def _balance(project=None, vocal_track=None, beat_tracks=None, vocal_wav=None, beat_wav=None, style=None, apply=False,
             duck=True, vocal_start_bar=None):
    st, mark = _steps()
    project = _project_or_open(project)
    j = _journal(project)
    style = style or (j.get("vocal") or {}).get("style") or "default"
    bpm, bpb = _project_tempo_bpb(project)
    vt = (j.get("vocal") or {})
    sb = float(vocal_start_bar) if vocal_start_bar is not None else float(
        (vt.get("comp") or {}).get("starts_at_bar") or ((vt.get("takes") or [{}])[-1]).get("starts_at_bar") or 1.0)
    if vocal_track is None:
        vocal_track, _ = _find_vocal_track(project)
    if vocal_wav and beat_wav:
        v, sr, _ = _load_wav(vocal_wav); b, sr2, _ = _load_wav(beat_wav)
        if sr != sr2:
            raise ValueError("vocal and beat files need the same sample rate")
        secs = _sections_seconds(project, len(v) / sr, bpm, sb, bpb)
        res = balance_measure(v, b, sr, secs, style)
        res["method"] = "measured from the files (vocal band, only while the vocal sings)"
    else:
        _need_fl()
        if vocal_track is None:
            raise ValueError("which mixer track is the vocal? (vocal_track=)")
        res = _live_balance(vocal_track, beat_tracks, style, project)
    mark("measure")
    res.update(style=style, vocal_track=vocal_track)
    if res["spread_db"] > 2.0:
        res["ride"] = "sections differ by more than 2 dB: ride the vocal per section (volume automation) rather than one fader move"
    if res.get("duck_db"):
        res["duck_suggestion"] = (f"the beat's 1-4 kHz competes with the vocal: dip the beat ~{res['duck_db']} dB "
                                  "while the vocal sings (apply=true writes level-duck automation on beat_tracks), "
                                  "or a static 1-4 kHz EQ dip on the beat bus")
    if H.get("masking"):
        res["masking_tool"] = "flh_mixeng masking is loaded: ns masking can pinpoint which beat track masks the vocal"
    if apply:
        done = []
        mv = res.get("vocal_move_db") or 0.0
        if mv and vocal_track is not None:
            if _safe_running():
                cur = H["fl_mixer_set"](int(vocal_track)).get("volume_db")
                if cur is None or not math.isfinite(float(cur)) or float(cur) <= -90:
                    done.append(f"vocal insert {vocal_track}: fader level unreadable (or -inf): not moved; set it "
                                f"{mv:+.1f} dB by hand")
                else:
                    mv = max(-12.0, min(6.0, float(mv)))
                    r = H["fl_mixer_set"](int(vocal_track), volume_db=min(5.6, float(cur) + mv))
                    done.append(f"vocal insert {vocal_track}: {float(cur):+.1f} -> {r.get('volume_db')} dB")
            elif project:
                d = H["_load"](project); cur = H["_track_volume_db"](d, int(vocal_track))
                H["TOOLS"]["flp_mixer"][0](project=project, output=project, tracks=[{"track": int(vocal_track), "volume_db": cur + mv}])
                done.append(f"vocal insert {vocal_track}: {cur:+.1f} -> {cur + mv:+.1f} dB (in the file)")
        if duck and res.get("duck_db") and project:
            beats = [int(t) for t in (beat_tracks or res.get("beat_tracks") or [])]
            ph = res.get("phrases_s")
            if not ph:
                wav = vocal_wav or _take_from_journal(project)[0]
                if wav:
                    x, sr, _ = _load_wav(wav); ph = [(a * HOP_S, b * HOP_S) for a, b in analyze(x, sr, detect=False)["phrases"]]
            if not beats:
                done.append("duck skipped: give beat_tracks=[...] (mixer inserts of the beat)")
            elif not ph or not bpm:
                done.append("duck skipped: need the vocal file (vocal_wav=) and the song tempo")
            else:
                cfg = _data("balance.json")["duck"]
                try:
                    tm = H["flp_tempo_map"](project)
                    if tm.get("min_bpm") != tm.get("max_bpm"):
                        done.append("note: the song changes tempo; duck points assume the first tempo")
                except Exception:
                    pass
                d = H["_load"](project)
                ops = []
                for t in beats:
                    base = H["_track_volume_db"](d, t)
                    pts = _duck_points(ph, sb, bpm, bpb, float(res["duck_db"]), base, cfg["attack_s"], cfg["release_s"])
                    ops.append({"tool": "flp_add_automation", "points": pts, "track": t, "param": "volume",
                                "name": f"Vocal duck {t}", "replace": True})
                # PC-VERIFY: the duck clips play back as gentle dips on the beat inserts under each phrase
                H["flp_edit"](project=project, ops=ops)
                done.append(f"ducked inserts {beats} by {res['duck_db']} dB under {len(ph)} vocal phrases (automation clips)")
        res["applied"] = done
        mark("apply")
    res["steps_s"] = st
    res["note"] = "genre norms are starting points: listen in context (and on small speakers) before keeping moves"
    return res


def _safe_running():
    try:
        return bool(H["fl_running"]())
    except Exception:
        return False


def fl_vocal_balance(project=None, vocal_track=None, beat_tracks=None, vocal_wav=None, beat_wav=None, style=None,
                     apply=False, duck=True, vocal_start_bar=None, background=None):
    kw = dict(project=project, vocal_track=vocal_track, beat_tracks=beat_tracks, vocal_wav=vocal_wav, beat_wav=beat_wav,
              style=style, apply=apply, duck=duck, vocal_start_bar=vocal_start_bar)
    if vocal_wav and beat_wav:
        return _balance(**kw)
    auto = H.get("_auto_job")
    return auto("vocal_balance", None, _balance, kw, background) if auto else _balance(**kw)


# ============================================================================= tool wrappers (offline ones can run as jobs)
def _audio_seconds(paths):
    tot = 0.0
    for p in paths if isinstance(paths, (list, tuple)) else [paths]:
        try:
            tot += float(H["_audio_seconds"](p))
        except Exception:
            try:
                tot += os.path.getsize(p) / (44100 * 2 * 3)
            except OSError:
                pass
    return tot


def _maybe_job(kind, est, fn, kw, background):
    """Offline (file-only) tools: a long run becomes a background job that does NOT hold the FL lock."""
    if H.get("_start_job") and (background or (background is None and est > 40)):
        return C.auto_job(H, kind, est, fn, kw, True, fl=False)
    return fn(**kw)


def fl_vocal_comp(takes, project=None, phrases="auto", key=None, reference_melody=None, output=None, style=None,
                  bpm=None, start_bar=None, crossfade_ms=None, full=False, background=None):
    kw = dict(takes=takes, project=project, phrases=phrases, key=key, reference_melody=reference_melody, output=output,
              style=style, bpm=bpm, start_bar=start_bar, crossfade_ms=crossfade_ms, full=full)
    return _maybe_job("vocal_comp", 2 + 0.12 * _audio_seconds(takes), vocal_comp, kw, background)


def fl_vocal_clean(wav, output=None, breaths="reduce", clicks=True, plosives=True, sibilance=True, noise=True,
                   amounts=None, full=False, background=None):
    kw = dict(wav=wav, output=output, breaths=breaths, clicks=clicks, plosives=plosives, sibilance=sibilance,
              noise=noise, amounts=amounts, full=full)
    return _maybe_job("vocal_clean", 2 + 0.25 * _audio_seconds(wav), vocal_clean, kw, background)


def fl_vocal_double(wav, output=None, kind="double", key=None, width=0.8, delay_ms=None, voices=None, steps=None,
                    drift_cents=8.0, formant=True, project=None, background=None):
    kw = dict(wav=wav, output=output, kind=kind, key=key, width=width, delay_ms=delay_ms, voices=voices, steps=steps,
              drift_cents=drift_cents, formant=formant, project=project)
    per = 0.05 if kind == "double" else 0.6
    return _maybe_job("vocal_double", 2 + per * _audio_seconds(wav), vocal_double, kw, background)


OFFLINE_TOOLS = {"fl_vocal_comp", "fl_vocal_clean", "fl_vocal_double", "fl_vocal_chain", "fl_vocal_balance"}


def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    STR, INT, NUM, BOOL = ns["_STR"], ns["_INT"], ns["_NUM"], ns["_BOOL"]
    T = ns["TOOLS"]
    bg = BOOL("run as a background job (default: automatically when long); fl_job for the result", True)
    styles = "rap|pop|rnb|podcast|rock|airy"
    T["fl_vocal_setup"] = (
        fl_vocal_setup, "Get ready to record a voice: picks a free mixer insert (or the one already set up), names it "
        "and gives it a dark color, arms it for recording, and reads FL's audio device / sample rate / buffer with "
        "latency advice (ASIO, buffer <= 256 for tracking). FL's scripting API can't choose the insert's audio input: "
        "the result says exactly where to click. Saved in the song journal (vocal.track).",
        S(track=INT("mixer insert to use (default: the journal's vocal track or the first free one)", True),
          input=STR("the mic input you want (e.g. 'In 1'); recorded in the journal, set by hand in FL", True),
          name=STR("insert name (default Vocal)", True), color=STR("'#RRGGBB' (default: a dark shade)", True),
          arm=BOOL("arm the insert for recording (default true)", True), project=ns["_STR"]("the open .flp", True)))
    T["fl_vocal_level_check"] = (
        fl_vocal_level_check, "Mic level check: while the user sings the loudest line for `seconds`, reads the vocal "
        "insert's peak meter and says how far to turn the interface's input gain (target: loudest words -10..-6 dBFS, "
        "never clipping). play=true plays the song from start_bar so they can sing along.",
        S(seconds=NUM("how long to listen (default 5, max 30)", True), track=INT("vocal mixer insert", True),
          play=BOOL("play the song meanwhile", True), start_bar=NUM("where to play from", True),
          project=STR("the open .flp", True)))
    T["fl_vocal_record"] = (
        fl_vocal_record, "Record vocal takes in the open FL project: plays from pre_roll_bars before start_bar to "
        "end_bar in record mode, `takes` times back to back (one audio file per pass), then finds the files FL wrote, "
        "names the new clip channels '<song> Vocal take N' and lists them in the song journal (vocal.takes) for "
        "fl_vocal_comp. Stops and reports if FL shows its record popup. Long recordings run as a background job.",
        S(start_bar=NUM("first bar of the part to record"), end_bar=NUM("bar where recording stops"),
          takes=INT("how many passes (default 1)", True), loop=BOOL("record the passes back to back (default true)", True),
          punch=BOOL("punch in/out at start/end bar when FL's bridge supports it (default true)", True),
          pre_roll_bars=NUM("bars of count-in played before start_bar (default 1)", True),
          track=INT("vocal mixer insert", True), project=STR("the open .flp", True), background=bg))
    T["fl_vocal_comp"] = (
        fl_vocal_comp, "Comp vocal takes (WAV files on the same timeline, e.g. from fl_vocal_record): splits them into "
        "phrases at the silences, scores every phrase of every take (pitch vs the song key or a reference melody, "
        "pitch wobble, timing vs the beat grid, noise, clipping, pops, harsh s, level) and picks the best take per "
        "phrase with hysteresis (no hopping every phrase), then writes one comp WAV with short equal-power crossfades "
        "inside the gaps. Returns a compact decision table (why each pick). Offline: works on any OS.",
        S(takes=P("array", "take WAV paths (same sample rate, same start)", items={"type": "string"}),
          project=STR("the song .flp (key, tempo, journal)", True),
          phrases=P(["string", "array"], "'auto' (default) or [[start_s, end_s], ...]", True, items={}),
          key=STR("song key, e.g. 'A minor' (default: project/journal, else estimated from the takes)", True),
          reference_melody=P(["string", "array"], ".mid path or [{note, start_s, end_s}] / [{note, bar, beats}]", True,
                             items={"type": "object"}),
          output=STR("comp WAV path (default '<first take> - comp.wav')", True),
          style=STR(f"weights: {styles} (rap: timing over pitch; podcast: no pitch)", True),
          bpm=NUM("tempo for the timing grid (default: the project's)", True),
          start_bar=NUM("song bar where the takes start (default: journal / 1)", True),
          crossfade_ms=NUM("crossfade length (default 20)", True), full=BOOL("per-take metrics in every row", True),
          background=bg))
    T["fl_vocal_clean"] = (
        fl_vocal_clean, "Clean a vocal take into a NEW file: finds and reports (counts + times) mouth clicks, plosive "
        "pops, harsh sibilance, breaths and room noise, then repairs clicks (re-drawn from both sides), cuts the low "
        "band of pops, de-esses with a dynamic 5-9 kHz band gain, reduces/removes breaths (or keeps them), and gently "
        "gates the noise using a noise profile from the silences. amounts={...} tunes thresholds/depths.",
        S(wav=STR("take WAV"), output=STR("new WAV (default '<take> - clean.wav')", True),
          breaths=STR("reduce (default, -10 dB) | keep | remove", True), clicks=BOOL("fix clicks (default true)", True),
          plosives=BOOL("fix pops (default true)", True), sibilance=BOOL("de-ess (default true)", True),
          noise=BOOL("reduce room noise (default true)", True),
          amounts=P("object", "overrides, e.g. {deess_max_db: 6, breath_reduce_db: 8, noise_db: 6, click_k: 5}", True),
          full=BOOL("list every event time", True), background=bg))
    T["fl_vocal_chain"] = (
        fl_vocal_chain, "A vocal chain for a style (rap, pop, rnb, podcast, rock, airy) with stock FL plugins, sized "
        "from measuring the take: high-pass under the voice, cuts for the measured mud / proximity / harshness, "
        "compressor thresholds from the take's dynamic range, de-esser band at the measured sibilance, tone, "
        "saturation, and reverb/delay sends timed to the tempo; edition-aware tuning (Pitcher/NewTone if owned, "
        "else free third-party options, never downloaded). apply=true loads missing plugins into free slots of the "
        "vocal insert and sets what it can, listing what to set by hand.",
        S(track=INT("vocal mixer insert (for apply)", True), style=STR(styles + " (default pop)", True),
          apply=BOOL("load and set it in the open project (default false: just the plan)", True),
          take=STR("take or comp WAV to measure (default: the journal's comp / last take)", True),
          project=STR("the song .flp", True), bpm=NUM("tempo (default: the project's)", True),
          verify_listen=NUM("with apply: listen to the vocal alone this many seconds before and after and check the "
                            "intents (less muddy, less harsh, more compressed) with flh_mixeng intent_verify", True),
          start_bar=NUM("where verify_listen plays from", True),
          replace=BOOL("with apply: also reconfigure same-named plugins already on the insert (default false: "
                       "plugins you set up are left alone; new ones go into empty slots)", True),
          revert_last=BOOL("put back every knob the last apply changed", True)))
    T["fl_vocal_balance"] = (
        fl_vocal_balance, "Is the vocal on top? Per section, vocal minus beat loudness in the vocal band while the "
        "vocal sings, against genre norms (e.g. rap +2..+5 LU); suggests a vocal fader move (or per-section riding) "
        "and a gentle duck of the beat under the vocal phrases. Measures from vocal_wav + beat_wav (offline), else "
        "with two live passes in FL. apply=true moves the fader and writes duck automation on beat_tracks.",
        S(project=STR("the song .flp", True), vocal_track=INT("vocal mixer insert", True),
          beat_tracks=P("array", "mixer inserts of the beat (default: every other used insert)", True, items={"type": "integer"}),
          vocal_wav=STR("vocal stem/comp WAV (aligned with beat_wav)", True), beat_wav=STR("beat/instrumental WAV", True),
          style=STR(styles, True), apply=BOOL("apply the moves (default false)", True),
          duck=BOOL("also write the duck when applying (default true)", True),
          vocal_start_bar=NUM("song bar where vocal_wav starts (default: journal / 1)", True), background=bg))
    T["fl_vocal_double"] = (
        fl_vocal_double, "Make a double or harmony from a vocal take (new file): double = natural double-tracks "
        "(10-30 ms late with a slow pitch/timing wander, L/R by width); octave_up / octave_down; third_up and harmony "
        "= scale-aware intervals in the song key from the take's pitch track (steps=[2,4] -> thirds + fifths). "
        "Pitch-shifted parts use a phase vocoder with formant correction: good as background layers, not as a lead.",
        S(wav=STR("take WAV"), output=STR("new WAV (default '<take> - <kind>.wav')", True),
          kind=STR("double | octave_up | octave_down | third_up | harmony", True),
          key=STR("song key for harmonies (default: project / estimated)", True),
          width=NUM("stereo spread 0..1 (default 0.8)", True), delay_ms=NUM("timing offset of a double (default 14/23 ms)", True),
          voices=INT("double voices (default 2 = L/R)", True),
          steps=P("array", "harmony scale steps, e.g. [2, 4] (2 = third, 4 = fifth, -2 = third below)", True,
                  items={"type": "integer"}),
          drift_cents=NUM("double pitch wander (default 8 cents)", True), formant=BOOL("formant correction (default true)", True),
          project=STR("the song .flp (key)", True), background=bg))
    cp = ns.get("CROSS_PLATFORM")
    if isinstance(cp, set):                                   # offline tools: no FL needed, run on any OS
        cp.update(OFFLINE_TOOLS)
