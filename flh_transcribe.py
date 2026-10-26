"""flh_transcribe: listen and transcribe - audio to MIDI, and "play me your idea" MIDI capture from FL.

Audio to MIDI (offline, stdlib + numpy):
  pitch_track(x, fs)   YIN difference function (parabolic trough depths) + pYIN-style threshold-distribution
                       candidate probabilities, an octave check (a clearly deeper trough at twice the lag beats a
                       shallow first one) and Viterbi over candidates/unvoiced -> f0, voicing probability, clarity,
                       RMS on a 5.8 ms grid. ns["pitch_track"] for other modules.
  segment_notes(track) note HMM over semitones + silence (wide enough for vibrato), glides fold into the note they
                       reach (onset = where the slide starts), re-articulations split on energy dips, attacks found
                       on a one-period RMS envelope, minimum length -> notes with cents and velocity from level.
  chords               log-compressed FFT chroma with a tuning estimate, harmonic chord templates (maj/m/7/dim/sus4/
                       maj7/m7, data/transcribe), cosine match per 93 ms frame + Viterbi, one label per beat,
                       Krumhansl key. Tempo from chord changes when nothing else gives it.
  drums                spectral-flux onsets (loudest band leads) refined on the waveform; each hit's band-amplitude
                       rise + noisiness -> kick / snare / hat (top end beyond the snare's own) / perc (toms), GM notes;
                       tempo from onset autocorrelation (metrical level in the usual beat range), Ellis DP beats and a
                       least-squares fit for the exact bpm; downbeat = where kicks land and snares don't.
  auto                 harmonic/percussive split (median filters), noise bursts at onsets, periodicity, whether the
                       f0 is a played note (fundamental present) and register -> drums/melody/bass/chords/mix.
MIDI capture: fl_capture_idea arms FL's own recording in a fresh pattern (bridge: song/transport/pattern/channel),
plays N bars, stops, has FL save and reads the recorded notes back from the .flp. Fallback: the user records by hand
and action="read" picks up the newest notes. capture_from_midi_file for exported MIDI. capture_and_build hands the
idea to flh_compose (ns["compose_around"]) when it is installed.
"""
import json
import math
import os
import time

class _LazyNumpy:
    """numpy loads on first use (keeps server start fast on weak PCs); then the global `np` is numpy itself."""
    def __getattr__(self, name):
        import numpy
        globals()["np"] = numpy
        return getattr(numpy, name)


np = _LazyNumpy()

import flh_common as C

H = None                                                         # live server namespace (set by register)
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "data", "transcribe", "transcribe.json")
NAMES = "C C# D Eb E F F# G Ab A Bb B".split()

_DEFAULT_DATA = {
    "chord_qualities": {"": [0, 4, 7], "m": [0, 3, 7], "7": [0, 4, 7, 10], "dim": [0, 3, 6], "sus4": [0, 5, 7],
                        "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10]},
    "harmonic_decay": 0.6, "harmonics": 6,
    "drum_bands_hz": [20, 70, 130, 250, 500, 1000, 2000, 4000, 7000, 12000, 24000],
    "drums": {"kick": {"gm": 36}, "snare": {"gm": 38}, "hat": {"gm": 42, "open_gm": 46, "open_after_s": 0.15},
              "perc": {"gm": 39, "tom_gm": [45, 47, 50]}},
    "drum_rules": {"kick": 0.25, "snare": 0.25, "hat": 0.25, "perc": 0.3, "noise_peak_share": 0.55,
                   "snare_top_ratio": 0.35},
    "midi_channels": {"melody": 0, "chords": 1, "bass": 2, "drums": 9},
    "chord_voicing_range": [52, 76], "chord_bass_octave": 3,
}
_DATA = {}


def data():
    """Shipped tables (data/transcribe/transcribe.json) over the built-in defaults; cached by file mtime."""
    try:
        mt = os.path.getmtime(DATA_FILE)
    except OSError:
        mt = None
    if _DATA.get("mt") != mt or "d" not in _DATA:
        d = json.loads(json.dumps(_DEFAULT_DATA))
        if mt is not None:
            try:
                with open(DATA_FILE, encoding="utf-8") as f:
                    d.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
            except (OSError, ValueError):
                pass
        _DATA.update(mt=mt, d=d)
    return _DATA["d"]


def note_name(k):
    return NAMES[int(k) % 12] + str(int(k) // 12 - 1)


# ============================================================================= audio in
def _ns(name, default=None):
    return (H or {}).get(name, default)


def load_audio(src, fs=None):
    """-> (mono float64, fs). src: a WAV/FLAC path, an (x, fs) pair, or an array with fs given."""
    if isinstance(src, (tuple, list)) and len(src) == 2 and np.ndim(src[0]) >= 1:
        x, fs = src
    elif isinstance(src, np.ndarray):
        if not fs:
            raise ValueError("give fs with a raw array")
        x = src
    else:
        path = str(src)
        if not os.path.isfile(path):
            raise ValueError(f"no such audio file: {path}")
        if path.lower().endswith(".flac"):
            import sys
            rv = sys.modules.get("flh_review")
            if rv is None or not hasattr(rv, "load_audio"):
                raise ValueError("FLAC needs flh_review's decoder; convert to WAV or install fl-headless fully")
            x, fs, _ = rv.load_audio(path)
        else:
            import fl_audio_edit as AE                           # block-wise reader, float32 (long files fit)
            x, fs, _, _ = AE.load_ex(path, np.float32, "raise")
    x = np.asarray(x)
    if x.ndim == 2:
        if x.shape[1] > 8:
            x = x.T
        m = np.empty(len(x))                                     # mono mix, block by block (float64 result)
        for a in range(0, len(x), 1 << 20):
            m[a:a + (1 << 20)] = np.asarray(x[a:a + (1 << 20)], dtype=np.float64).mean(axis=1)
        x = m
    else:
        x = np.asarray(x, dtype=np.float64)
    if not np.isfinite(x).all():
        raise ValueError("the audio has NaN/inf samples (a plugin glitch?): fix the file (" +
                         C.tool_name(H, "audio_edit") + ") or re-render")
    return x, int(fs)


def _lowpass_decimate(x, q):
    """Decimate by an integer factor with a windowed-sinc low-pass (FFT convolution, zero phase)."""
    if q <= 1:
        return x
    taps = 16 * q + 1; n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(n / q * 0.9) * np.hanning(taps); h /= h.sum()
    half = (taps - 1) // 2; L = len(x)
    out = np.empty(-(-L // q))
    B = max(q, (1 << 20) // q * q)                               # blocks of whole output samples (no full FFT)
    for a in range(0, L, B):
        b = min(L, a + B); lo, hi = max(0, a - half), min(L, b + half)
        seg = np.asarray(x[lo:hi], dtype=np.float64)
        yc = np.convolve(seg, h)[half:half + len(seg)]          # zero-phase ('same') for this segment
        idx = np.arange(a, b, q)
        out[idx // q] = yc[idx - lo]
    return out


def work_rate(x, fs, target=22050):
    """Analysis copy at ~22-24 kHz (integer decimation; content above ~10 kHz isn't needed here)."""
    q = max(1, int(fs // (target * 0.9)))
    return (_lowpass_decimate(x, q), fs / q) if q > 1 else (x, float(fs))


def _frames(x, L, hop, pad=None):
    """Frames of length L every hop, frame i covering [i*hop - pad, ...) (default pad L//2: centered)."""
    pad = L // 2 if pad is None else int(pad)
    xp = np.concatenate([np.zeros(pad), x, np.zeros(L + hop)])
    n = max(1, 1 + (len(x) - 1) // hop)
    return np.lib.stride_tricks.as_strided(xp, shape=(n, L), strides=(hop * xp.strides[0], xp.strides[0]))


def _rms_db(x, fs, hop, win, block=1 << 20):
    """Frame RMS in dBFS (centered frames, same grid as _frames with that hop). Running sums are taken block by
    block (only their values at the frame edges are kept), so a long take never needs full-length copies."""
    n = max(1, 1 + (len(x) - 1) // hop); i = np.arange(n) * hop
    pre = win // 2                                               # padded signal: zeros, x*x, zeros
    pos = np.concatenate([i, i + win])                           # cumsum indices needed
    order = np.argsort(pos, kind="stable"); ps = pos[order]
    vals = np.zeros(len(pos)); acc = 0.0; j = 0
    while j < len(ps) and ps[j] <= pre:                          # inside the leading zeros: 0
        j += 1
    for a in range(0, len(x), block):
        seg = np.asarray(x[a:a + block], dtype=np.float64)
        cs = np.cumsum(seg * seg) + acc
        hi = pre + a + len(seg)                                  # padded index after this block
        k = j
        while k < len(ps) and ps[k] <= hi:
            k += 1
        if k > j:
            vals[j:k] = cs[ps[j:k] - pre - a - 1]
            j = k
        acc = float(cs[-1]) if len(cs) else acc
    vals[j:] = acc                                               # trailing zeros: the total
    c = np.empty(len(pos)); c[order] = vals
    e = (c[n:] - c[:n]) / win
    return 10 * np.log10(np.maximum(e, 1e-12))


# ============================================================================= pitch (YIN / pYIN-style)
_BETA = {}


def _beta_cdf(a=2.0, b=18.0, n=1001):
    """CDF of a Beta(a, b) on 0..1 (pYIN's prior over YIN thresholds; mean a/(a+b) = 0.1)."""
    k = (a, b, n)
    if k not in _BETA:
        s = np.linspace(0, 1, n); pdf = s ** (a - 1) * (1 - s) ** (b - 1)
        c = np.cumsum(pdf); _BETA[k] = c / c[-1]
    return _BETA[k]


def _cmnd(F, W, tmax, raw=False):
    """Cumulative mean normalized difference (YIN) for a block of frames F (n, W + tmax) -> (n, tmax + 1).
    raw=True also returns the energy-normalized plain difference d / (e0 + e_tau) (0 = identical, ~2 = inverted)."""
    N = 1 << int(np.ceil(np.log2(F.shape[1] + W)))
    r = np.fft.irfft(np.fft.rfft(F, N, axis=1) * np.conj(np.fft.rfft(F[:, :W], N, axis=1)), N, axis=1)[:, :tmax + 1]
    cs = np.concatenate([np.zeros((F.shape[0], 1)), np.cumsum(F * F, axis=1)], axis=1)
    tau = np.arange(tmax + 1)
    e = cs[:, tau + W] - cs[:, tau]
    d = np.maximum(e[:, :1] + e - 2 * r, 0.0); d[:, 0] = 0
    cm = np.cumsum(d[:, 1:], axis=1) / np.arange(1, tmax + 1)
    out = np.ones_like(d); out[:, 1:] = d[:, 1:] / np.maximum(cm, 1e-12)
    if raw:
        return out, d / np.maximum(e[:, :1] + e, 1e-12)
    return out


def _remove_dc(y, fs, fmin=55.0, inplace=False, block=1 << 20):
    """Subtract a centered moving average (window ~ 2 periods of fmin, shrinking at the ends; O(n) via running
    sums taken block by block): removes DC and slow drift. inplace=True reuses y's buffer (y must be float64)."""
    y = np.asarray(y, dtype=np.float64)
    n = len(y); N = max(3, int(2 * fs / max(fmin, 1.0))) | 1; h = N // 2
    if n <= N:
        return y - (y.mean() if n else 0.0)
    ma = np.empty(n)
    for a in range(0, n, block):                                 # moving average of the ORIGINAL samples
        b = min(n, a + block); lo, hi = max(0, a - h), min(n, b + h + 1)
        c = np.concatenate(([0.0], np.cumsum(y[lo:hi])))
        i = np.arange(a, b); l_, r_ = np.maximum(i - h, 0), np.minimum(i + h + 1, n)
        ma[a:b] = (c[r_ - lo] - c[l_ - lo]) / (r_ - l_)
    if inplace:
        y -= ma
        return y
    return y - ma


def pitch_track(x, fs, fmin=55.0, fmax=1600.0, hop_s=None, max_candidates=5, silence_db=-55.0, octave_check=True,
                smooth=True):
    """Monophonic f0 track. -> dict of numpy arrays on a hop grid: t (s, frame centers), f0 (Hz, 0 = unvoiced),
    voiced (bool), voiced_prob (0..1, pYIN-style), clarity (1 - YIN aperiodicity of the chosen lag), rms_db; plus
    hop_s, fs (analysis rate) and _y (the analysis signal). Any input rate: works at ~44 kHz when fmax > 700 Hz
    (fine lags for high notes), else ~22 kHz."""
    x, fs = load_audio(x, fs) if not isinstance(x, np.ndarray) or x.ndim != 1 else (np.asarray(x, float), fs)
    y, fsw = work_rate(x, fs, target=44100 if fmax > 700 else 22050)   # high notes need fine lags
    y = _remove_dc(y, fsw, fmin, inplace=y is not x)             # DC / sub-sonic drift is not a pitch
    hop = max(32, int(round((hop_s or 128 / 22050) * fsw)))
    # search a little above fmax so a note right at the top has a trough with neighbours (else its octave below
    # wins); candidates above fmax * 1.06 are dropped afterwards
    tmin = max(2, int(fsw / (fmax * 1.25))); tmax = int(math.ceil(fsw / fmin)) + 1
    W = max(tmax, int(0.03 * fsw)); L = W + tmax
    Fr = _frames(y, L, hop, pad=(W + tmax // 2) // 2)         # integration window centered on the frame time
    n = Fr.shape[0]
    rms = _rms_db(y, fsw, hop, W)
    # silence gate: absolute (silence_db) for normal levels, relative for a quiet take - a recording that peaks at
    # -40 dBFS (speaker capture at low system volume, a quiet DI) kept only its loudest attack frames (PC check
    # 2026-10-23: 7 voiced frames of a held 808, all in its pitch-swept attack -> 'an octave off'). Unchanged
    # whenever the loudest frame reaches -20 dBFS.
    top = float(np.max(rms)) if len(rms) else silence_db
    gate = rms > max(min(silence_db, top - 35.0), top - 60.0, -110.0)
    cdf = _beta_cdf()
    cand_f = np.zeros((n, max_candidates)); cand_p = np.zeros((n, max_candidates)); cand_c = np.zeros((n, max_candidates))
    vprob = np.zeros(n); clar = np.zeros(n)
    B = 256
    for a in range(0, n, B):
        b = min(n, a + B)
        idx = [i for i in range(a, b) if gate[i]]
        if not idx:
            continue
        D, ND = _cmnd(Fr[idx], W, tmax, raw=True)
        for row, i in enumerate(idx):
            dd = D[row]; nd = ND[row]
            seg = dd[tmin:tmax]
            tr = np.nonzero((seg[1:-1] < seg[:-2]) & (seg[1:-1] <= seg[2:]))[0] + 1 + tmin
            if not len(tr):
                continue
            y0, y1, y2 = dd[tr - 1], dd[tr], dd[tr + 1]             # trough depth between lags (parabola)
            den = y0 - 2 * y1 + y2
            v = np.where(den > 1e-12, y1 - 0.125 * (y0 - y2) ** 2 / np.where(den > 1e-12, den, 1), y1)
            v = np.clip(v, 0.0, None)
            prev_min = np.concatenate([[np.inf], np.minimum.accumulate(v)[:-1]])
            hi = np.minimum(prev_min, 1.0)
            pr = np.where(v < hi, cdf[np.clip((hi * 1000).astype(int), 0, 1000)] - cdf[np.clip((v * 1000).astype(int), 0, 1000)], 0.0)
            g = int(np.argmin(v))
            pr[g] += 0.01 * cdf[min(1000, int(min(v.min(), 1.0) * 1000))]           # pYIN: absolute-min fallback
            if octave_check:                                       # a shallow first trough at half the true lag
                for j in np.nonzero(pr > 0.02)[0]:                 # (strong even harmonics): the deeper one wins
                    t2 = 2 * tr[j]
                    k2 = np.nonzero(np.abs(tr - t2) <= max(2, 0.03 * t2))[0]
                    if len(k2):
                        k2 = k2[np.argmin(v[k2])]
                        # (a deep first trough is the period: near fmax, few samples per period make it read a
                        # little shallower than its double, which is not an octave error)
                        if v[k2] < 0.5 * v[j] and v[j] - v[k2] > 0.025 and nd[tr[j]] > 1.5 * nd[tr[k2]] + 0.02:
                            pr[k2] += 0.9 * pr[j]; pr[j] *= 0.1
                for j in np.nonzero(pr > 0.02)[0]:                 # the reverse: YIN's cumulative mean reads short
                    h2 = tr[j] / 2.0                               # lags (high notes) too shallow, so the period's
                    k1 = np.nonzero(np.abs(tr - h2) <= max(1.5, 0.03 * h2))[0]   # double wins; if the plain
                    if len(k1):                                    # difference at half the lag is as good, the
                        k1 = k1[np.argmin(nd[tr[k1]])]             # shorter lag is the period
                        if nd[tr[k1]] <= 1.5 * nd[tr[j]] + 0.02 and pr[j] > pr[k1]:
                            pr[k1] += 0.9 * pr[j]; pr[j] *= 0.1
            order = np.argsort(-pr)[:max_candidates]
            for c, j in enumerate(order):
                if pr[j] <= 1e-4:
                    break
                t0 = tr[j]
                if 1 <= t0 < tmax:                                 # parabolic interpolation of the trough
                    y0, y1, y2 = dd[t0 - 1], dd[t0], dd[t0 + 1]
                    den = y0 - 2 * y1 + y2
                    off = 0.5 * (y0 - y2) / den if den > 1e-12 else 0.0
                    off = max(-0.5, min(0.5, off))
                else:
                    off = 0.0
                if fsw / (t0 + off) > fmax * 1.06:
                    continue
                cand_f[i, c] = fsw / (t0 + off); cand_p[i, c] = pr[j]; cand_c[i, c] = 1 - v[j]
            vprob[i] = min(1.0, float(pr.sum()))
            clar[i] = 1 - float(v.min())
    # Viterbi over (candidates + unvoiced): continuity in semitones, a cost to switch voicing
    K = max_candidates
    with np.errstate(divide="ignore"):
        midi = np.where(cand_f > 0, 69 + 12 * np.log2(np.maximum(cand_f, 1e-9) / 440.0), np.nan)
    em = np.full((n, K + 1), -np.inf)
    em[:, :K] = np.where(cand_p > 0, np.log(cand_p + 1e-6), -np.inf)
    em[:, K] = np.log(np.maximum(1.0 - np.minimum(cand_p.sum(1), 1.0), 1e-3))
    if not smooth:
        best = np.argmax(em, axis=1)
    else:
        lam, cap, vu = 0.35, 6.0, 2.5
        score = em[0].copy(); back = np.zeros((n, K + 1), dtype=np.int16)
        for i in range(1, n):
            pm, cm = midi[i - 1], midi[i]
            dm = np.abs(cm[:, None] - pm[None, :])                 # (cur K, prev K)
            tr = -np.minimum(lam * dm, cap) - np.where(dm > 1.0, 1.0, 0.0)
            tr = np.where(np.isnan(tr), -np.inf, tr)
            full = np.empty((K + 1, K + 1))
            full[:K, :K] = tr + score[None, :K]
            full[:K, K] = score[K] - vu
            full[K, :K] = score[:K] - vu
            full[K, K] = score[K]
            bk = np.argmax(full, axis=1); back[i] = bk
            score = full[np.arange(K + 1), bk] + em[i]
            if not np.isfinite(score).any():
                score = em[i].copy()
        best = np.zeros(n, dtype=int); best[-1] = int(np.argmax(score))
        for i in range(n - 1, 0, -1):
            best[i - 1] = back[i, best[i]]
    voiced = best < K
    f0 = np.where(voiced, cand_f[np.arange(n), np.minimum(best, K - 1)], 0.0)
    voiced &= f0 > 0
    f0[~voiced] = 0.0
    cc = np.where(voiced, cand_c[np.arange(n), np.minimum(best, K - 1)], clar)
    return {"t": np.arange(n) * hop / fsw, "f0": f0, "voiced": voiced, "voiced_prob": vprob, "clarity": cc,
            "rms_db": rms, "hop_s": hop / fsw, "fs": fsw, "_y": y}


# ============================================================================= notes from a pitch track
def _attack(y, fs, t, f0, back=0.08, fwd=0.05, min_rise=9.0):
    """Where a note's attack starts near t: a causal RMS envelope one period of f0 long (smooth even for a 40 Hz
    bass), the strongest rise of at least min_rise dB in the window, walked back to where it leaves the floor
    (-15 dB from the peak). None when there's no attack (a legato pitch change)."""
    P = int(np.clip(fs / max(f0, 20.0), 0.002 * fs, 0.03 * fs))
    a = max(0, int((t - back) * fs) - P); b = min(len(y), int((t + fwd) * fs))
    if b - a < 3 * P:
        return None
    c = np.concatenate([[0.0], np.cumsum(y[a:b] ** 2)])
    e = 10 * np.log10(np.maximum((c[P:] - c[:-P]) / P, 1e-12))   # e[i]: mean power of samples a+i .. a+i+P-1
    step = max(1, int(0.001 * fs))
    ee = e[::step]
    if len(ee) < 6:
        return None
    lo = int(np.argmin(ee))                                      # the quietest point, then the peak after it
    pk = lo + int(np.argmax(ee[lo:]))
    floor = float(ee[lo])
    if ee[pk] - floor < min_rise:
        return None
    lim = max(ee[pk] - 15.0, floor + 0.5 * (ee[pk] - floor))
    j = pk
    while j > lo and ee[j - 1] >= lim:
        j -= 1
    return (a + j * step + P) / fs                               # window ends at the attack: its last sample


def segment_notes(track, min_note_s=0.06, sigma=0.55, change_cost=6.0, silence_cost=4.0, split_db=6.0,
                  lo=None, hi=None):
    """Notes from pitch_track output: [{start, end (s), pitch (MIDI), cents, vel, conf}]. Vibrato-aware (wide
    per-semitone likelihood + switching cost), glides fold into the note they land on (onset where the slide
    starts), repeated same-pitch notes split on energy dips, attacks refined on a one-period RMS envelope."""
    f0, vp, rms, hop = track["f0"], track["voiced_prob"], track["rms_db"], track["hop_s"]
    n = len(f0)
    if n == 0 or not track["voiced"].any():
        return []
    with np.errstate(divide="ignore"):
        mf = np.where(f0 > 0, 69 + 12 * np.log2(np.maximum(f0, 1e-9) / 440.0), np.nan)
    v = track["voiced"]
    lo = int(np.floor(np.nanmin(mf))) - 1 if lo is None else lo
    hi = int(np.ceil(np.nanmax(mf))) + 1 if hi is None else hi
    states = np.arange(lo, hi + 1); S = len(states)
    p = np.where(v, np.clip(np.maximum(vp, 0.6), 0, 1), np.minimum(vp, 0.3))
    dm = np.where(v[:, None], mf[:, None] - states[None, :], 0.0)
    g = np.exp(-0.5 * (dm / sigma) ** 2) + 0.01
    em = np.empty((n, S + 1))
    em[:, :S] = np.log(np.where(v[:, None], p[:, None] * g, 0.0) + (1 - p[:, None]) * 0.03)
    em[:, S] = np.log((1 - p) + 0.002)
    score = em[0] + np.where(np.arange(S + 1) == S, 0.0, -silence_cost)
    back = np.zeros((n, S + 1), dtype=np.int32)
    idx = np.arange(S)
    for i in range(1, n):                                        # O(S) per frame: stay, jump from the best note,
        bn = int(np.argmax(score[:S])); top = score[bn]          # or come out of silence
        cand = np.stack([score[:S], np.full(S, top - change_cost), np.full(S, score[S] - silence_cost)])
        w = np.argmax(cand, axis=0)
        new = cand[w, idx]
        back[i, :S] = np.where(w == 0, idx, np.where(w == 1, bn, S))
        if score[S] >= top - silence_cost:
            back[i, S] = S; sil = score[S]
        else:
            back[i, S] = bn; sil = top - silence_cost
        score = np.concatenate([new, [sil]]) + em[i]
    path = np.zeros(n, dtype=int); path[-1] = int(np.argmax(score))
    for i in range(n - 1, 0, -1):
        path[i - 1] = back[i, path[i]]
    runs = []                                                    # [start frame, end frame (excl), state]
    s0 = 0
    for i in range(1, n + 1):
        if i == n or path[i] != path[s0]:
            if path[s0] != S:
                runs.append([s0, i, int(states[path[s0]])])
            s0 = i
    # re-articulation: split a run at a clear energy dip (same pitch played again)
    out = []
    for a, b, k in runs:
        cuts = [a]
        seg = rms[a:b]
        if b - a > 8:
            m = 4
            for j in range(m, b - a - m):
                w = seg[max(0, j - 12):j + 13]
                if seg[j] == w.min() and seg[:j].max() - seg[j] >= split_db and seg[j:].max() - seg[j] >= split_db \
                        and j - (cuts[-1] - a) > m:
                    cuts.append(a + j)
        cuts.append(b)
        for c0, c1 in zip(cuts[:-1], cuts[1:]):
            out.append([c0, c1, k, c0 != a])
    # glides: a short note whose pitch keeps sliding is the attack of the next one
    keep = []
    for idx, (a, b, k, rearti) in enumerate(out):
        seg = mf[a:b]; ok = ~np.isnan(seg)
        dur = (b - a) * hop
        if dur < 0.15 and ok.sum() >= 4 and idx + 1 < len(out) and out[idx + 1][0] == b:
            tt = np.arange(b - a)[ok] * hop
            slope = np.polyfit(tt, seg[ok], 1)[0] if ok.sum() > 1 else 0.0
            if abs(slope) > 10 and np.ptp(seg[ok]) > 0.8 and np.sign(slope) == np.sign(out[idx + 1][2] - k):
                out[idx + 1][0] = a; out[idx + 1][3] = rearti
                continue
        keep.append([a, b, k, rearti])
    out = keep
    # neighbor flicker (wide vibrato): A B A with B one semitone away and short -> A
    i = 1
    while i < len(out) - 1:
        A, Bn, C = out[i - 1], out[i], out[i + 1]
        if A[2] == C[2] and abs(Bn[2] - A[2]) == 1 and (Bn[1] - Bn[0]) * hop < 0.12 and A[1] == Bn[0] and Bn[1] == C[0] \
                and not Bn[3] and not C[3]:
            A[1] = C[1]; del out[i:i + 2]
        else:
            i += 1
    y = track.get("_y"); fsw = track["fs"]
    notes = []
    prev_end = -1
    for a, b, k, rearti in out:
        start, end = a * hop, b * hop
        after_silence = a != prev_end
        with np.errstate(invalid="ignore"):
            f_here = float(np.nanmedian(np.where(f0[a:b] > 0, f0[a:b], np.nan))) if (f0[a:b] > 0).any() else 200.0
        att = _attack(y, fsw, start, f_here) if y is not None else None
        def glide_start(j0):                                     # where the slide into this pitch begins: steep
            sgn = np.sign(k - (notes[-1]["pitch"] if notes else k))   # frames heading its way, then their eased start
            j = j0; lim_j = max(0, j0 - int(0.08 / hop))
            while j - 1 > lim_j and not np.isnan(mf[j - 1]) and not np.isnan(mf[j]) and \
                    sgn * (mf[j] - mf[j - 1]) / hop > 15.0:
                j -= 1
            if j < j0:
                lim2 = max(lim_j, j - int(0.03 / hop))
                while j - 1 > lim2 and not np.isnan(mf[j - 1]) and sgn * (mf[j] - mf[j - 1]) / hop > 3.0:
                    j -= 1
            return j
        if after_silence or rearti:
            if att is not None:
                start = att
            elif notes and not rearti and start - notes[-1]["end"] < 0.08:
                j = glide_start(min(a, max(0, int(round(notes[-1]["end"] / hop)))))  # a fast glide the tracker lost
                start = min(notes[-1]["end"], j * hop)
                notes[-1]["end"] = min(notes[-1]["end"], round(start, 4))
        elif att is not None and att < start + 0.02:             # re-attacked new pitch (a gap the frames bridged)
            start = att
            if notes:
                notes[-1]["end"] = min(notes[-1]["end"], round(start, 4))
        else:                                                    # legato change
            j = glide_start(a)
            if j < a and notes:
                start = j * hop
                notes[-1]["end"] = min(notes[-1]["end"], round(start, 4))
        prev_end = b
        mid = mf[a + (b - a) // 5: b - (b - a) // 5 or b]
        mid = mid[~np.isnan(mid)]
        if not len(mid):
            mid = mf[a:b][~np.isnan(mf[a:b])]
        if not len(mid):
            continue
        med = float(np.median(mid))
        pitch = int(round(med)) if abs(med - k) > 0.6 else k
        pk_db = float(rms[a:b].max())
        conf = float(np.clip(np.mean(track["clarity"][a:b]) * np.mean(vp[a:b] if vp[a:b].any() else 0.5), 0, 1))
        notes.append({"start": round(start, 4), "end": round(max(end, start + hop), 4), "pitch": pitch,
                      "cents": int(round((med - pitch) * 100)), "db": pk_db, "conf": round(conf, 2)})
    # minimum length: very short notes merge into a touching neighbor at the same pitch, else go
    res = []
    for nt in notes:
        if nt["end"] - nt["start"] < min_note_s:
            if res and res[-1]["pitch"] == nt["pitch"] and nt["start"] - res[-1]["end"] < 0.02:
                res[-1]["end"] = nt["end"]
            continue
        res.append(nt)
    _velocities(res)
    return res


def _velocities(notes, top=112, db_per_step=2.0):
    """MIDI velocity from each note's peak level, relative to the loudest note (loudest = 112, -20 dB = 72)."""
    if not notes:
        return
    ref = max(n["db"] for n in notes)
    for n in notes:
        n["vel"] = int(np.clip(round(top + db_per_step * (n.pop("db") - ref)), 1, 127))


# ============================================================================= onsets, tempo, beats
def stft_mag(y, n_fft, hop, window=True):
    F = _frames(y, n_fft, hop)
    w = np.hanning(n_fft) if window else np.ones(n_fft)
    out = np.empty((F.shape[0], n_fft // 2 + 1))
    for a in range(0, F.shape[0], 1024):
        out[a:a + 1024] = np.abs(np.fft.rfft(F[a:a + 1024] * w, axis=1))
    return out


def onset_envelope(y, fs, hop=128, n_fft=1024, bands=None, block=4096):
    """Log spectral flux (half-wave rectified, vs the max of the 2 previous frames) -> (env, frame rate).
    bands: [(lo_hz, hi_hz)] to get one envelope per band instead (shape (n, len(bands))).
    Streams over blocks of frames (two passes: global max, then flux), so a long song never holds its full STFT."""
    lead = n_fft // hop + 1                                      # silence in front: a hit at t=0 still shows as a rise
    yy = np.concatenate([np.zeros(lead * hop), np.asarray(y, dtype=np.float64)])
    F = _frames(yy, n_fft, hop)
    nF = F.shape[0]
    w = np.hanning(n_fft)
    mag = lambda a, b: np.abs(np.fft.rfft(F[a:b] * w, axis=1))
    mx = 0.0
    for a in range(0, nF, block):
        mx = max(mx, float(mag(a, min(nF, a + block)).max()) if nF else 0.0)
    sc = 1000.0 / max(mx, 1e-9)
    f = np.fft.rfftfreq(n_fft, 1 / fs)
    sel = None if bands is None else [(f >= lo) & (f < hi) for lo, hi in bands]
    out = np.zeros(nF) if bands is None else np.zeros((nF, len(bands)))
    hist = None                                                  # the two frames before the block
    for a in range(0, nF, block):
        b = min(nF, a + block)
        L = np.log1p(sc * mag(a, b))
        if hist is None:
            hist = np.vstack([L[:1], L[:1]])                     # frame 0 compares with itself (as before)
        Lp = np.vstack([hist, L])
        d = np.maximum(L - np.maximum(Lp[1:-1], Lp[:-2]), 0)
        out[a:b] = d.sum(1) if sel is None else np.stack([d[:, m].sum(1) for m in sel], 1)
        hist = Lp[-2:]
    return out[lead:], fs / hop


def pick_peaks(env, rate, delta=None, wait_s=0.03, pre_s=0.1, post_s=0.03):
    """Local maxima above a moving average + delta (librosa-style), at least wait_s apart -> frame indexes."""
    n = len(env)
    if n < 3:
        return np.zeros(0, dtype=int)
    e = env / max(env.max(), 1e-12)
    pre, post, wait = max(1, int(pre_s * rate)), max(1, int(post_s * rate)), max(1, int(wait_s * rate))
    c = np.concatenate([[0.0], np.cumsum(e)])
    i = np.arange(n); a = np.maximum(0, i - pre); b = np.minimum(n, i + post + 1)
    avg = (c[b] - c[a]) / (b - a)
    delta = 0.04 if delta is None else delta
    mx = np.array([e[max(0, k - post):k + post + 1].max() for k in range(n)])
    cand = np.nonzero((e == mx) & (e >= avg + delta) & (e > 0.02))[0]
    out = []
    for k in cand:
        if not out or k - out[-1] >= wait:
            out.append(k)
        elif e[k] > e[out[-1]]:
            out[-1] = k
    return np.array(out, dtype=int)


def refine_onset(y, fs, t, before=0.035, after=0.025):
    """Sample-accurate onset near t: the steepest energy rise of the pre-emphasized signal (so a hat on top of a
    kick's tail still shows its own attack), walked back to where the attack starts (10% of its level)."""
    nb, na = int(before * fs), int(after * fs)
    c = int(round(t * fs)); a = c - nb - 1; b = c + na
    seg = y[max(0, a):max(0, min(len(y), b))]
    if a < 0:
        seg = np.concatenate([np.zeros(-a), seg])
    if len(seg) < 32:
        return t
    z = seg[1:] - 0.95 * seg[:-1]
    w = max(1, int(0.001 * fs))
    e = np.convolve(z * z, np.ones(w) / w, mode="same")
    d = max(1, int(0.0015 * fs))
    rise = np.full(len(e), -np.inf); rise[d:-d] = e[2 * d:] - e[:-2 * d]
    k = int(np.argmax(rise))
    top = e[min(len(e) - 1, k + d)]; base = e[max(0, k - 3 * d)]
    lim = base + 0.1 * (top - base); j = k; stop = max(0, k - int(0.01 * fs))
    while j > stop and e[j - 1] > lim:
        j -= 1
    return max(0.0, (a + 1 + j - w // 2) / fs)


def tempo_estimate(env, rate, min_bpm=50, max_bpm=220, prior_bpm=120.0, prior_oct=0.9):
    """Tempo from the onset envelope's autocorrelation (+ half/double-lag support, weak log-normal prior around
    prior_bpm) -> (bpm, confidence 0..1, [(bpm, score)] runners-up)."""
    n = len(env)
    if n < rate * 2:                                             # < 2 s: no tempo (and np.convolve 'same' would
        return None, 0.0, []                                     # return the 1 s kernel's length for < 1 s)
    o = env - np.convolve(env, np.ones(int(rate)) / int(rate), mode="same")
    o = np.maximum(o, 0)
    if not o.any():
        return None, 0.0, []
    N = 1 << int(np.ceil(np.log2(2 * n)))
    ac = np.fft.irfft(np.abs(np.fft.rfft(o, N)) ** 2, N)[:n]
    ac /= max(ac[0], 1e-12)
    lmin, lmax = int(rate * 60 / max_bpm), min(n - 1, int(rate * 60 / min_bpm) + 1)
    if lmax <= lmin + 2:
        return None, 0.0, []
    lags = np.arange(lmin, lmax)

    def at(l):
        l = np.asarray(l, float); i = np.clip(l.astype(int), 0, n - 2); f = l - i
        return ac[i] * (1 - f) + ac[i + 1] * f
    bpm = 60 * rate / lags
    prior = np.exp(-0.5 * (np.log2(bpm / prior_bpm) / prior_oct) ** 2)
    s = (at(lags) + 0.25 * at(lags / 2)) * prior
    k = int(np.argmax(s))
    # metrical level: a loop repeats at the bar too, so the slowest strong level scores highest; between octave
    # relatives that are both strong, take the one in the usual beat range (closest to prior_bpm)
    base = at(lags)
    rel = [k]
    for f in (2.0, 0.5, 3.0, 1 / 3.0):
        j = int(np.argmin(np.abs(lags - lags[k] / f)))
        if abs(lags[j] * f - lags[k]) <= max(2, 0.03 * lags[k]) and base[j] >= 0.45 * base[k]:
            rel.append(j)
    inrange = [j for j in rel if 76 <= bpm[j] <= 160]
    if inrange:
        k = min(inrange, key=lambda j: abs(np.log2(bpm[j] / prior_bpm)))
        lo_, hi_ = max(0, k - 2), min(len(s), k + 3)
        k = lo_ + int(np.argmax(base[lo_:hi_]))
    l0 = lags[k]
    if 0 < k < len(s) - 1:                                       # parabolic peak
        den = base[k - 1] - 2 * base[k] + base[k + 1]
        l0 = l0 + (0.5 * (base[k - 1] - base[k + 1]) / den if den < 0 else 0.0)
    # refine with the peaks at whole multiples of the lag (more precise for longer loops)
    ests = [l0]
    for m in range(2, 9):
        c = m * l0
        if c + 3 >= n:
            break
        lo, hi = int(c - 0.02 * c) - 1, int(c + 0.02 * c) + 2
        seg = ac[max(1, lo):hi]
        if not len(seg):
            break
        j = int(np.argmax(seg)) + max(1, lo)
        if 0 < j < n - 1:
            den = ac[j - 1] - 2 * ac[j] + ac[j + 1]
            jj = j + (0.5 * (ac[j - 1] - ac[j + 1]) / den if den < 0 else 0.0)
        else:
            jj = j
        ests.append(jj / m)
    lag = float(np.median(ests[-4:])) if len(ests) > 2 else float(ests[0])
    conf = float(np.clip((s[k] - np.median(s)) / max(s[k], 1e-9), 0, 1))
    try:                                                         # a pulse needs onsets: a steady tone or a pad
        dens = len(pick_peaks(env, rate)) / max(n / rate, 1e-9)  # autocorrelates "well" but has no beat
        if dens < 0.5:
            conf *= max(0.0, dens / 0.5)
    except Exception:                                            # noqa: BLE001 - confidence stays as measured
        pass
    alts = []
    for j in np.argsort(-s)[:40]:
        b = round(60 * rate / lags[j], 1)
        if all(abs(b - a) / a > 0.04 for a, _ in alts) and abs(b - 60 * rate / lag) / b > 0.04:
            alts.append((b, round(float(s[j] / s[k]), 2)))
        if len(alts) == 2:
            break
    return 60 * rate / lag, conf, alts


def track_beats(env, rate, bpm, tightness=100.0):
    """Ellis (2007) dynamic-programming beat tracker on an onset envelope -> beat frame indexes."""
    n = len(env)
    period = rate * 60.0 / bpm
    o = env / max(env.std(), 1e-9)
    sc = np.zeros(n); bk = -np.ones(n, dtype=int)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    span = np.arange(lo, hi + 1)
    pen = -tightness * np.log(span / period) ** 2
    for t in range(n):
        p = t - span
        ok = p >= 0
        if ok.any():
            cand = sc[p[ok]] + pen[ok]
            j = int(np.argmax(cand))
            if cand[j] > 0:
                sc[t] = o[t] + cand[j]; bk[t] = p[ok][j]; continue
        sc[t] = o[t]
    # end: the best score in the last period (local maxima only)
    tail = np.arange(max(0, n - int(period) - 1), n)
    t = int(tail[np.argmax(sc[tail])])
    beats = [t]
    while bk[t] >= 0:
        t = bk[t]; beats.append(t)
    beats = np.array(beats[::-1])
    strength = o[beats]                                          # trim weak beats at the edges (silence)
    thr = 0.1 * np.median(strength[strength > 0]) if (strength > 0).any() else 0
    good = np.nonzero(np.convolve(strength > thr, np.ones(3), mode="same") > 0)[0]
    return beats[good[0]:good[-1] + 1] if len(good) else beats


def beat_grid(y, fs, tempo=None, onsets_s=None, env_rate=None, beats_per_bar=4):
    """Tempo + beat times for any audio: given tempo -> a grid phased on the onsets; else autocorrelation tempo,
    DP beats, then a least-squares fit of beat times (snapped to nearby onsets) for a precise bpm.
    -> {"bpm", "confidence", "beats" (s), "first_beat_s", "detected", "alternatives"}"""
    if env_rate is None:
        env, rate = onset_envelope(y, fs)
    else:
        env, rate = env_rate
    dur = len(y) / fs
    if tempo:
        bpm, conf, alts, detected = float(tempo), 1.0, [], False
    else:
        bpm, conf, alts = tempo_estimate(env, rate)
        detected = True
        if bpm is None:
            return {"bpm": None, "confidence": 0.0, "beats": [], "first_beat_s": 0.0, "detected": False,
                    "alternatives": []}
    period = 60.0 / bpm
    if not tempo:
        bf = track_beats(env, rate, bpm)
        bt = bf / rate
        if onsets_s is not None and len(onsets_s) and len(bt) >= 4:
            on = np.asarray(onsets_s)
            near = np.array([on[np.argmin(np.abs(on - b))] for b in bt])
            use = np.abs(near - bt) < 0.07 * period * 4
            bt2 = np.where(use, near, bt)
            k = np.round((bt2 - bt2[0]) / period)
            if use.sum() >= 4 and np.ptp(k) > 0:
                A = np.vstack([k[use], np.ones(use.sum())]).T
                slope, icpt = np.linalg.lstsq(A, bt2[use], rcond=None)[0]
                if abs(slope - period) / period < 0.05:
                    period = float(slope); bpm = 60.0 / period
                    bt = icpt + np.arange(int(np.round((dur - icpt) / period)) + 1) * period
                    bt = bt[(bt >= -0.02) & (bt < dur)]
        phase = ((bt[0] + 0.1 * period) % period) - 0.1 * period if len(bt) else 0.0
    else:
        phase = 0.0
        if onsets_s is not None and len(onsets_s):            # phase that puts most onsets on beats
            on = np.asarray(onsets_s)
            ph = np.linspace(0, period, 48, endpoint=False)
            sc = [np.sum(np.cos(2 * np.pi * (on - p) / period)) for p in ph]
            phase = float(ph[int(np.argmax(sc))])
            frac = ((on - phase) / period) % 1
            phase = float((phase + np.angle(np.mean(np.exp(2j * np.pi * frac))) / (2 * np.pi) * period) % period)
            phase = ((phase + 0.5 * period) % period) - 0.5 * period    # a hair early is beat 0, not beat 1
    beats = phase + np.arange(int(max(0, dur - phase) / period) + 1) * period
    return {"bpm": round(bpm, 2), "confidence": round(conf, 2), "beats": beats, "first_beat_s": round(float(phase), 4),
            "detected": detected, "alternatives": alts}


# ============================================================================= chords
def chroma(y, fs, n_fft=8192, hop=2048, fmin=55.0, fmax=4200.0, tuning=None):
    """Log-compressed FFT chroma (12 x frames) with a tuning estimate (cents off A440) -> (C (n, 12), tuning, rate).
    Bins weigh in by closeness to their semitone's center (a cheap constant-Q view of the FFT)."""
    M = stft_mag(y, n_fft, hop)
    f = np.fft.rfftfreq(n_fft, 1 / fs)
    sel = (f >= fmin) & (f <= fmax)
    fm = 69 + 12 * np.log2(f[sel] / 440.0)
    Ms = M[:, sel]
    if tuning is None:                                           # magnitude-weighted circular mean of peak offsets
        pk = (Ms[:, 1:-1] > Ms[:, :-2]) & (Ms[:, 1:-1] >= Ms[:, 2:]) & (Ms[:, 1:-1] > 0.05 * Ms.max())
        rows, cols = np.nonzero(pk)
        if len(cols):
            cols = cols + 1
            # interpolate the peak frequency (parabolic on log magnitude)
            la, lb, lc = (np.log(Ms[rows, cols + d] + 1e-12) for d in (-1, 0, 1))
            den = la - 2 * lb + lc; off = np.where(den < 0, 0.5 * (la - lc) / np.where(den < 0, den, 1), 0)
            fk = f[sel][cols] + off * (f[1] - f[0])
            dev = 69 + 12 * np.log2(np.maximum(fk, 1) / 440.0)
            dev = dev - np.round(dev)
            wgt = Ms[rows, cols] * (fk > 150)                    # low bins are too coarse for tuning
            tuning = float(np.angle(np.sum(wgt * np.exp(2j * np.pi * dev))) / (2 * np.pi)) if wgt.sum() > 0 else 0.0
        else:
            tuning = 0.0
    q = fm - tuning
    pc = np.round(q).astype(int) % 12
    w = np.cos(np.pi * (q - np.round(q))) ** 2
    L = np.log1p(100 * Ms / max(Ms.max(), 1e-9)) * w
    C = np.zeros((M.shape[0], 12))
    for p in range(12):
        C[:, p] = L[:, pc == p].sum(1)
    return C, tuning, fs / hop


def chord_templates():
    """[(name, root, quality)], T (n, 12): harmonic chord templates (each chord tone with decaying harmonics)."""
    d = data(); dec = float(d.get("harmonic_decay", 0.6)); nh = int(d.get("harmonics", 6))
    names, T = [], []
    for root in range(12):
        for q, iv in d["chord_qualities"].items():
            t = np.zeros(12)
            for i in iv:
                for h in range(1, nh + 1):
                    t[(root + i + int(round(12 * np.log2(h)))) % 12] += dec ** (h - 1)
            names.append((NAMES[root] + q, root, q)); T.append(t / np.linalg.norm(t))
    return names, np.array(T)


def _seventh_check(S, Cn, names, T):
    """Prefer the plain triad: a 7th chord only scores as itself when its 7th's chroma clearly beats what the
    triad's own overtones put in that bin (the 3rd harmonic of E and the 5th of G are both B, so a bright C major
    looks like Cmaj7 in a log chroma). The overtone strengths are measured per frame from bins only overtones can
    reach (the fifth's 3rd harmonic; a chord tone's 5th harmonic), so the test adapts to the sound."""
    d = data(); need = float(d.get("seventh_min_excess", 0.08)); pen = float(d.get("seventh_penalty", 0.25))
    S = S.copy()
    base = np.median(Cn, axis=1)                                 # leakage floor per frame
    eps = 1e-9
    for j, (_, r, q) in enumerate(names):
        if q not in ("7", "maj7", "m7"):
            continue
        third = (r + (3 if q == "m7" else 4)) % 12; fifth = (r + 7) % 12
        s7 = (r + (11 if q == "maj7" else 10)) % 12
        rho3 = np.clip((Cn[:, (fifth + 7) % 12] - base) / (Cn[:, fifth] + eps), 0, 1)
        p5 = (r + 4) % 12 if q == "m7" else (r + 8) % 12         # a 5th-harmonic-only bin
        src5 = r if q == "m7" else third
        rho5 = np.clip((Cn[:, p5] - base) / (Cn[:, src5] + eps), 0, 1)
        pred = base.copy()
        for t_ in (r, third, fifth):
            if (t_ + 7) % 12 == s7:
                pred = pred + rho3 * Cn[:, t_]
            if (t_ + 4) % 12 == s7:
                pred = pred + rho5 * Cn[:, t_]
        S[:, j] -= np.where(Cn[:, s7] - pred < need, pen, 0.0)
    return S


def estimate_key(cvec):
    """Krumhansl-Kessler key from a 12-bin pitch-class profile -> (name, correlation, margin to the runner-up)."""
    import fl_music
    cvec = np.asarray(cvec, dtype=np.float64)
    if not len(cvec) or not np.isfinite(cvec).all() or float(np.std(cvec)) < 1e-12:
        return None, 0.0, 0.0                                     # silence / flat: no key (never NaN)
    res = []
    for r in range(12):
        for nm, prof in (("major", fl_music.KK_MAJOR), ("minor", fl_music.KK_MINOR)):
            p = np.roll(prof, r)
            res.append((f"{NAMES[r]} {nm}", float(np.corrcoef(cvec, p)[0, 1])))
    res.sort(key=lambda x: -x[1])
    return res[0][0], round(res[0][1], 3), round(res[0][1] - res[1][1], 3)


def chord_track(y, fs, beats=None, change_cost=0.2, kappa=30.0, key=None):
    """Chords over time: cosine match of chroma frames (93 ms) to the templates, Viterbi-smoothed (a change costs
    change_cost), so chord changes are found even when the beat grid is a guess; then one label per beat interval
    (the label covering most of it). key: in-key chords get a small bonus.
    -> {"segments": [(start_s, end_s, name|None)], "labels": per beat, "scores", "margins", "tuning_cents", "key"}"""
    C, tuning, rate = chroma(y, fs)
    names, T = chord_templates()
    n = C.shape[0]
    energy = C.sum(1)
    Cs = np.vstack([C[max(0, i - 1):i + 2].mean(0) for i in range(n)])          # light smoothing (3 frames)
    Cn = Cs / np.maximum(np.linalg.norm(Cs, axis=1, keepdims=True), 1e-12)
    S = Cn @ T.T
    S = _seventh_check(S, Cn, names, T)
    bonus = np.zeros(len(names))
    if key:
        try:
            import fl_music
            sc = fl_music.scale_set(key)
            q = data()["chord_qualities"]
            bonus = np.array([0.01 if all((r + i) % 12 in sc for i in q[qq]) else 0.0 for _, r, qq in names])
        except Exception:
            pass
    em = kappa * (S + bonus)
    K = len(names)
    score = em[0].copy(); back = np.zeros((n, K), dtype=np.int32)
    for i in range(1, n):
        best = int(np.argmax(score))
        move = score[best] - kappa * change_cost
        back[i] = np.where(score >= move, np.arange(K), best)
        score = np.maximum(score, move) + em[i]
    path = np.zeros(n, dtype=int); path[-1] = int(np.argmax(score))
    for i in range(n - 1, 0, -1):
        path[i - 1] = back[i, path[i]]
    silent = energy < 0.05 * max(energy.max(), 1e-12)
    fl = [None if silent[i] else names[path[i]][0] for i in range(n)]
    ft = np.arange(n) / rate
    segs = []
    for i, lab in enumerate(fl):
        if segs and segs[-1][2] == lab:
            segs[-1][1] = ft[i] + 0.5 / rate
        else:
            segs.append([max(0.0, ft[i] - 0.5 / rate), ft[i] + 0.5 / rate, lab])
    tot = C[~silent].sum(0) if (~silent).any() else C.sum(0)
    out = {"segments": [tuple(x) for x in segs], "tuning_cents": int(round(tuning * 100)), "templates": names,
           "key": estimate_key(tot), "_frames": (S, path, silent, rate, len(y) / fs)}
    if beats is not None and len(beats):
        out.update(chord_beats(out, beats))
    return out


def chord_beats(track, beats):
    """One chord per beat interval from chord_track's frames: the label covering most of the interval."""
    S, path, silent, rate, dur = track["_frames"]; names = track["templates"]
    n = len(path); ft = np.arange(n) / rate
    out = {}
    if len(beats):
        edges = list(beats) + [min(dur, beats[-1] + (beats[-1] - beats[-2] if len(beats) > 1 else 0.5))]
        labels, scores, margins = [], [], []
        for a_, b_ in zip(edges[:-1], edges[1:]):
            m = (ft >= a_) & (ft < b_)
            if not m.any():
                m = np.zeros(n, dtype=bool); m[min(n - 1, max(0, int(a_ * rate)))] = True
            idx = np.nonzero(m)[0]
            cnt = {}
            for i in idx:
                cnt[path[i]] = cnt.get(path[i], 0) + 1
            p = max(cnt, key=lambda k_: (cnt[k_], -abs(np.mean([i for i in idx if path[i] == k_]) - idx.mean())))
            row = S[idx].mean(0)
            srt = np.sort(row)
            labels.append(None if silent[idx].all() else names[p][0])
            scores.append(round(float(row[p]), 3))
            margins.append(round(float(row[p] - (srt[-2] if p == int(np.argmax(row)) else srt[-1])), 3))
        out.update(labels=labels, scores=scores, margins=margins)
    return out


def tempo_from_changes(segments, prior_bpm=120.0):
    """When only chord changes give the time: bpm from the typical gap between changes, assuming a change every
    1, 2, 4 or 8 beats (the reading closest to prior_bpm in 76-160). -> (bpm, first change s) or (None, 0)."""
    st = [s for s, _, lab in segments if lab]
    if len(st) < 3:
        return None, 0.0
    gaps = np.diff(st)
    gaps = gaps[gaps > 0.25]
    if not len(gaps):
        return None, 0.0
    g = float(np.median(gaps))
    cands = [60.0 * k / g for k in (1, 2, 4, 8)]
    ok = [c for c in cands if 76 <= c <= 160] or cands
    return min(ok, key=lambda c: abs(np.log2(c / prior_bpm))), float(st[0])


def voice_chords(segments, lo=None, hi=None, bass_oct=None, sevenths=True):
    """[(start_beat, beats, chord name)] -> MIDI notes [(start_beat, beats, key, vel)]: a bass root plus the chord
    tones in the voicing range, each chord's inversion chosen for the smallest move from the previous one.
    sevenths=False voices 7th chords as their triads (used when the chart's confidence is low)."""
    d = data(); lo = lo or d["chord_voicing_range"][0]; hi = hi or d["chord_voicing_range"][1]
    bass_oct = d.get("chord_bass_octave", 3) if bass_oct is None else bass_oct
    quals = d["chord_qualities"]
    out, prev = [], None
    for st, ln, name in segments:
        if not name:
            continue
        root, q = _parse_chord(name)
        if not sevenths and q in ("7", "maj7", "m7"):
            q = "m" if q == "m7" else ""
        pcs = [(root + i) % 12 for i in quals.get(q, [0, 4, 7])]
        best = None
        for base in range(lo, lo + 12):
            v = sorted(base + ((p - base) % 12) for p in pcs)
            if v[-1] > hi:
                continue
            cost = sum(abs(a - b) for a, b in zip(v, prev)) if prev and len(prev) == len(v) else abs(np.mean(v) - (lo + hi) / 2)
            if best is None or cost < best[0]:
                best = (cost, v)
        v = best[1] if best else sorted(lo + ((p - lo) % 12) for p in pcs)
        prev = v
        out.append((st, ln, 12 * (bass_oct + 1) + root, 84))
        out += [(st, ln, k, 76) for k in v]
    return out


def _parse_chord(name):
    r = name[:2] if len(name) > 1 and name[1] in "#b" else name[:1]
    root = NAMES.index(r) if r in NAMES else {"Db": 1, "D#": 3, "Gb": 6, "G#": 8, "A#": 10}.get(r, 0)
    return root, name[len(r):]


# ============================================================================= drums
def _band_powers(seg, fs, edges):
    if len(seg) < 8:
        return np.zeros(len(edges) - 1)
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / fs)
    return np.array([S[(f >= a) & (f < b)].sum() for a, b in zip(edges[:-1], edges[1:])]) / len(seg)


def _new_noisiness(y, fs, i, end, pre_n, gap):
    """How noise-like is what an onset added (after minus 2x before), in 1-6 kHz and above 6 kHz: the energy share
    of the strongest 5% of bins - noise ~0.2-0.4 (band-limited noise a bit more), a pitched attack > 0.6."""
    a = y[i:max(i + 64, end)]; b = y[max(0, i - pre_n):max(0, i - gap)]
    n = 1 << int(np.ceil(np.log2(max(len(a), 64))))
    Pa = np.abs(np.fft.rfft(a * np.hanning(len(a)), n)) ** 2 / max(len(a), 1)
    Pb = np.abs(np.fft.rfft(b * np.hanning(len(b)), n)) ** 2 / max(len(b), 1) if len(b) > 8 else np.zeros_like(Pa)
    Dp = np.maximum(Pa - 2 * Pb, 0)
    f = np.fft.rfftfreq(n, 1 / fs); out = []
    for lo, hi in ((1000, 6000), (6000, fs / 2)):
        v = np.sort(Dp[(f >= lo) & (f < hi)])[::-1]
        if len(v) < 8 or v.sum() <= 0:
            out.append(1.0); continue
        k = max(1, int(round(0.05 * len(v))))
        out.append(float(v[:k].sum() / v.sum()))
    return out


def drum_hits(y, fs):
    """Onsets + classes from each onset's band-amplitude rise (after vs before): kick = sub/low rise, snare = noisy
    mid rise, hat = top end beyond what the snare brings, perc = low-mid (toms) without snare noise. Levels are
    relative to this recording (90th percentiles), thresholds in data/transcribe (drum_rules).
    -> (hits [{t, cls, act, share}], onsets_s, (env, rate), classes, stats)"""
    d = data(); classes = list(d["drums"])
    E = list(d["drum_bands_hz"])
    keepb = [i for i in range(len(E) - 1) if E[i] < fs / 2 * 0.95]
    edges = [E[i] for i in keepb] + [fs / 2]
    nb = len(edges) - 1
    envs, rate = onset_envelope(y, fs, bands=[(20, 150), (150, 2000), (2000, 6000), (6000, fs / 2)])
    nrm = envs / np.maximum(envs.max(0, keepdims=True), 1e-12)
    det = nrm.max(1) + 0.25 * nrm.sum(1)                         # the loudest band leads: a quiet hat still counts
    pk = pick_peaks(det, rate, delta=0.05, wait_s=0.03)
    on = np.array([refine_onset(y, fs, p / rate) for p in pk])
    if len(on) > 1:                                              # two picks refined onto the same hit
        on = on[np.concatenate([[True], np.diff(on) > 0.015])]
    pre_n, post_n, gap = int(0.03 * fs), int(0.05 * fs), int(0.003 * fs)
    D, FL = [], []
    for t in on:
        i = int(t * fs)
        nxt = on[on > t + 0.01]
        end = min(i + post_n, int(nxt[0] * fs) - gap if len(nxt) else len(y))
        post = _band_powers(y[i:max(i + 64, end)], fs, edges)
        pre = _band_powers(y[max(0, i - pre_n):max(0, i - gap)], fs, edges)
        D.append(np.sqrt(np.maximum(post - 2.0 * pre, 0)))         # a band counts only if it at least doubled
        FL.append(_new_noisiness(y, fs, i, end, pre_n, gap))
    D = np.array(D).reshape(-1, nb)
    FL = np.array(FL).reshape(-1, 2)
    if not len(D):
        return [], on, (det, rate), classes, {"onsets": 0}
    # per-band scale: the typical (median) rise among onsets that touch the band, so a quiet hat counts as much
    # as a loud snare's top end
    f_lo = np.array(edges[:-1]); f_hi = np.array(edges[1:])
    grp = {"low": f_hi <= 135, "lmid": (f_lo >= 125) & (f_hi <= 520), "mid": (f_lo >= 490) & (f_hi <= 4100),
           "upper": (f_lo >= 3900) & (f_hi <= 7100), "high": f_lo >= 6900}
    G = {g: np.sqrt((D[:, m] ** 2).sum(1)) if m.any() else np.zeros(len(D)) for g, m in grp.items()}

    def ref(v, q=90):
        nz = v[v > 0.02 * v.max()] if v.max() > 0 else v
        return max(float(np.percentile(nz, q)) if len(nz) else 1.0, 1e-9)
    N = {g: G[g] / ref(G[g]) for g in G}
    rule = d.get("drum_rules", {})
    k_min, s_min, h_min, p_min = (float(rule.get(k, v)) for k, v in
                                  (("kick", 0.25), ("snare", 0.25), ("hat", 0.25), ("perc", 0.3)))
    nz = float(rule.get("noise_peak_share", 0.55))               # snare / hat are noise: a pitched note's attack
    floor = 0.01 * max(float(max(v.max() for v in G.values())), 1e-12)   # isn't; and -40 dB re the loudest rise
    snare = (N["mid"] >= s_min) & (FL[:, 0] <= nz) & (G["mid"] >= floor)
    # how much top end the snare itself brings: the least among its hits, capped by the data's typical ratio (a
    # hat hidden under every snare is otherwise indistinguishable from a bright snare)
    hm = (G["high"] + 0.5 * G["upper"]) / np.maximum(G["mid"], 1e-12)
    rho = min(float(np.min(hm[snare])) if snare.sum() >= 3 else 1e9, float(rule.get("snare_top_ratio", 0.35)))
    top = G["high"] + 0.5 * G["upper"]
    quiet_mid = N["mid"] < 0.1
    hat_ref = ref(top[quiet_mid & (top > 0)], 75) if (quiet_mid & (top > 0)).sum() >= 2 else ref(top)
    excess = np.maximum(top - np.where(snare, rho * G["mid"], 0.0), 0) / hat_ref
    hits = []
    for i, t in enumerate(on):
        found = []
        if N["low"][i] >= k_min and G["low"][i] / ref(G["low"]) >= 0.5 * N["lmid"][i]:
            found.append(("kick", N["low"][i]))
        if snare[i]:
            found.append(("snare", N["mid"][i]))
        if excess[i] >= (h_min * 2 if snare[i] else h_min) and FL[i, 1] <= nz and top[i] >= floor:
            found.append(("hat", excess[i]))
        if not snare[i] and N["lmid"][i] >= p_min and N["lmid"][i] > 2.0 * N["low"][i]:
            found.append(("perc", N["lmid"][i]))
        tot = sum(v for _, v in found) or 1.0
        for c, v in found:
            hits.append({"t": float(t), "cls": c, "act": float(min(v, 2.0)), "share": round(v / tot, 2)})
    return hits, on, (det, rate), classes, {"onsets": len(on), "snare_top_ratio": round(rho, 3) if rho < 1e8 else None}


def _hat_open(y, fs, t, after_s):
    """Open hi-hat: high-band energy still within 20 dB of its peak after after_s."""
    i = int(t * fs); seg = y[i:i + int(0.4 * fs)]
    if len(seg) < int(after_s * fs) + 32:
        return False
    z = np.diff(seg)
    w = int(0.01 * fs)
    e = np.convolve(z * z, np.ones(w) / w, mode="valid")
    pk = e[:int(0.03 * fs)].max() if len(e) > int(0.03 * fs) else e.max()
    j = int(after_s * fs)
    return j < len(e) and e[j] > pk * 0.01


def _perc_note(y, fs, t, spec):
    """perc -> a tom (pitched, low/mid/high) or the clap/other note."""
    i = int(t * fs); seg = y[i:i + int(0.08 * fs)]
    if len(seg) < 64:
        return spec.get("gm", 39)
    P = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2; f = np.fft.rfftfreq(len(seg), 1 / fs)
    m = (f >= 100) & (f <= 4000)
    flat = np.exp(np.mean(np.log(P[m] + 1e-15))) / max(P[m].mean(), 1e-15)
    if flat < 0.05 and spec.get("tom_gm"):
        lo = (f >= 50) & (f <= 500)
        fp = f[lo][np.argmax(P[lo])]
        toms = spec["tom_gm"]
        return toms[0] if fp < 110 else toms[1] if fp < 180 or len(toms) < 3 else toms[-1]
    return spec.get("gm", 39)


def drums_transcribe(y, fs, tempo=None, beats_per_bar=4):
    """-> dict: hits [{t, cls, gm, vel, act}], grid (beat_grid), downbeat index, stats."""
    d = data()
    hits, on, envr, classes, st = drum_hits(y, fs)
    grid = beat_grid(y, fs, tempo=tempo, onsets_s=on, env_rate=envr)
    for h in hits:
        spec = d["drums"][h["cls"]]
        gm = spec["gm"]
        if h["cls"] == "hat" and spec.get("open_gm") and _hat_open(y, fs, h["t"], float(spec.get("open_after_s", 0.15))):
            gm = spec["open_gm"]
        elif h["cls"] == "perc":
            gm = _perc_note(y, fs, h["t"], spec)
        h["gm"] = int(gm)
        h["vel"] = int(np.clip(round(30 + 90 * min(1.0, h["act"]) ** 0.5), 1, 127))
    down = 0
    beats = grid["beats"]
    if grid["bpm"] and len(beats) >= beats_per_bar:              # downbeat: where kicks land and snares don't
        period = 60.0 / grid["bpm"]
        sc = np.zeros(beats_per_bar)
        for h in hits:
            j = int(round((h["t"] - beats[0]) / period))
            if abs(h["t"] - (beats[0] + j * period)) < 0.15 * period:
                sc[j % beats_per_bar] += h["act"] * (1.0 if h["cls"] == "kick" else -0.6 if h["cls"] == "snare" else 0)
        best = sc.max()                                          # near-ties (four on the floor): the earliest
        down = int(next(k for k in range(beats_per_bar) if sc[k] >= best - 0.1 * abs(best)))
    return {"hits": hits, "grid": grid, "downbeat_beat": down, "stats": st, "classes": classes}


# ============================================================================= what is it?
def _median_filter(M, k, axis):
    from numpy.lib.stride_tricks import sliding_window_view
    pad = [(0, 0)] * M.ndim; pad[axis] = (k // 2, k // 2)
    P = np.pad(M, pad, mode="edge")
    out = np.empty_like(M)
    if axis == 0:
        for a in range(0, M.shape[0], 256):
            out[a:a + 256] = np.median(sliding_window_view(P[a:a + 256 + k - 1], k, axis=0), axis=-1)
    else:
        for a in range(0, M.shape[0], 256):
            out[a:a + 256] = np.median(sliding_window_view(P[a:a + 256], k, axis=1), axis=-1)
    return out


def _f0_support(y, fs, tr, loud=None):
    """Is the tracked f0 a played note? dB of the f0 partial vs the strongest of its first 8 harmonics (median over
    loud voiced frames). A chord's partials can look periodic at a "virtual" low pitch with no energy of its own
    (<< -18 dB); a real note almost always has its fundamental among its strong partials."""
    if loud is None:
        loud = tr["rms_db"] > tr["rms_db"].max() - 35
    sup = []
    Mf = stft_mag(y, 4096, 1024); ff = np.fft.rfftfreq(4096, 1 / fs)
    for i in range(Mf.shape[0]):
        j = int(round(i * 1024 / fs / tr["hop_s"]))
        if j >= len(tr["f0"]) or not tr["voiced"][j] or not loud[j]:
            continue
        f0 = tr["f0"][j]; E = []
        for h in range(1, 9):
            band = (ff >= h * f0 * 0.97) & (ff <= h * f0 * 1.03)
            E.append(float((Mf[i, band] ** 2).max()) if band.any() else 0.0)
        if max(E) > 0:
            sup.append(10 * np.log10(max(E[0], 1e-20) / max(E)))
    return float(np.median(sup)) if sup else -99.0


def classify(y, fs, max_s=45.0):
    """Percussive vs harmonic (median-filter HPSS energy split), monophonic vs polyphonic (YIN periodicity of the
    non-silent frames) and register -> {"kind": drums|melody|bass|chords|mix, "confidence", evidence...}"""
    y = y[:int(max_s * fs)]
    M = stft_mag(y, 2048, 512)
    M = M[:, :int(6000 / (fs / 2048))]
    Hh = _median_filter(M, 17, 0); Pp = _median_filter(M, 17, 1)
    mask = Pp ** 2 / np.maximum(Hh ** 2 + Pp ** 2, 1e-20)
    E = M ** 2
    perc = float((mask * E).sum() / max(E.sum(), 1e-20))
    fb = np.arange(M.shape[1]) * fs / 2048
    harm_hi = float(((1 - mask) * E)[:, fb > 220].sum() / max(E.sum(), 1e-20))   # pitched content above the kick
    tr = pitch_track(y, fs, fmin=40.0, fmax=1600.0, smooth=False)
    loud = tr["rms_db"] > tr["rms_db"].max() - 35
    if loud.sum() < 5:
        return {"kind": "silence", "confidence": 1.0, "percussive": perc}
    clar = float(np.median(tr["clarity"][loud]))
    vfrac = float(np.mean(tr["voiced_prob"][loud] > 0.5))
    f0s = tr["f0"][loud & tr["voiced"]]
    med_f0 = float(np.median(f0s)) if len(f0s) else 0.0
    f0_support = _f0_support(y, fs, tr, loud)
    hits, on, _, _, _ = drum_hits(y, fs)                         # noise bursts (snare / hat) at onsets = a kit
    noisy = len({h["t"] for h in hits if h["cls"] in ("snare", "hat")}) / max(len(on), 1) if len(on) >= 6 else 0.0
    ev = {"percussive": round(perc, 2), "periodicity": round(clar, 2), "voiced": round(vfrac, 2),
          "median_f0_hz": round(med_f0, 1), "noisy_onsets": round(noisy, 2), "f0_support_db": round(f0_support, 1),
          "harmonic_above_220hz": round(harm_hi, 2)}
    if (noisy >= 0.3 and harm_hi < 0.15) or (perc >= 0.6 and harm_hi < 0.3):
        kind, conf = "drums", min(1.0, max((perc - 0.6) / 0.25 + 0.5, 0.4 + 0.5 * noisy))
    elif noisy >= 0.25 and harm_hi >= 0.15:                      # a kit plus pitched parts: a mix
        kind, conf = "mix", round(min(0.8, 0.4 + 0.5 * min(noisy, harm_hi * 2)), 2)
    elif clar >= 0.85 and vfrac >= 0.6 and f0_support > -18:
        kind = "bass" if med_f0 and med_f0 < 140 else "melody"
        conf = min(1.0, 0.5 + (clar - 0.85) * 3 + (vfrac - 0.6))
    else:
        kind, conf = "chords", min(1.0, 0.5 + (0.85 - clar) * 2)
    return dict(ev, kind=kind, confidence=round(float(conf), 2))


# ============================================================================= audio -> MIDI
def _grid_beats(q):
    """'1/16' -> 0.25 beats, '1/8t' -> 1/3, 0.5 -> 0.5; {"grid": .., "strength": ..} -> (beats, strength)."""
    strength = 1.0
    if isinstance(q, dict):
        strength = float(q.get("strength", 1.0)); q = q.get("grid", "1/16")
    if q in (None, False, "", "none", "off"):
        return None, 0.0
    if q is True:
        q = "1/16"
    if isinstance(q, (int, float)):
        return float(q), strength
    s = str(q).strip().lower(); trip = s.endswith("t")
    s = s.rstrip("t")
    num, _, den = s.partition("/")
    g = 4.0 * float(num) / float(den) if den else float(num)
    return (g * 2 / 3 if trip else g), strength


def _snap(b, g, strength):
    if not g:
        return b
    return b + strength * (round(b / g) * g - b)


def _melody_part(y, fs, mode, key=None):
    fmin, fmax, mn = (30.0, 420.0, 0.07) if mode == "bass" else (55.0, 1600.0, 0.06)
    tr = pitch_track(y, fs, fmin=fmin, fmax=fmax)
    notes = segment_notes(tr, min_note_s=mn)
    if key:                                                      # off-pitch notes between two semitones: the in-key one
        import fl_music
        sc = fl_music.scale_set(key)
        for n in notes:
            if abs(n["cents"]) >= 30 and n["pitch"] % 12 not in sc:
                alt = n["pitch"] + (1 if n["cents"] > 0 else -1)
                if alt % 12 in sc:
                    n["cents"] -= 100 * (alt - n["pitch"]); n["pitch"] = alt; n["snapped_to_key"] = True
    conf = float(np.mean([n["conf"] for n in notes])) if notes else 0.0
    sup = _f0_support(tr["_y"], tr["fs"], tr) if notes else 0.0
    if sup < -18:                                                # the "pitch" is a chord's virtual pitch
        conf = min(conf, 0.3)
    return {"events": notes, "confidence": round(conf, 2), "track": tr, "f0_support_db": round(sup, 1)}


def audio_to_midi(input, output_mid=None, mode="auto", key=None, tempo=None, project=None, channel=None,
                  pattern=None, quantize=None, beats_per_bar=4, align="auto", place_at_bar=1, limit=120, full=False):
    """Transcribe audio (WAV/FLAC path, or (x, fs)) to notes. mode melody|chords|bass|drums|auto. Writes a .mid
    (output_mid), returns notes inline as [bar, length_bars, pitch, vel] (flp_replace_part's format) and, with
    project + channel, writes them into a new pattern of that project in ONE flp_edit."""
    T0 = time.time(); steps = {}
    t = time.time()
    x, fs0 = load_audio(input)
    y, fs = work_rate(x, fs0)
    dur = len(y) / fs
    if dur < 0.2:
        raise ValueError("the audio is shorter than 0.2 s")
    steps["load"] = round(time.time() - t, 2); t = time.time()
    mode = (mode or "auto").lower()
    if mode not in ("auto", "melody", "chords", "bass", "drums"):
        raise ValueError("mode: melody, chords, bass, drums or auto")
    detected = None
    if mode == "auto":
        detected = classify(y, fs)
        k = detected["kind"]
        if k == "silence":
            raise ValueError("the audio is (nearly) silent")
        modes = ["drums", "chords"] if k == "mix" else [k]
        steps["classify"] = round(time.time() - t, 2); t = time.time()
    else:
        modes = [mode]
    parts, notes_beats, warn = {}, {}, []
    drum = None
    if "drums" in modes:
        drum = drums_transcribe(y, fs, tempo=tempo, beats_per_bar=beats_per_bar)
        steps["drums"] = round(time.time() - t, 2); t = time.time()
    mel = {}
    for m in ("melody", "bass"):
        if m in modes:
            mel[m] = _melody_part(y, fs, m, key)
            steps[m] = round(time.time() - t, 2); t = time.time()
    # one tempo / beat grid for every part
    if drum is not None:
        grid = drum["grid"]
    else:
        on = [n["start"] for p in mel.values() for n in p["events"]]
        if not on:
            env, rate = onset_envelope(y, fs)
            on = [p / rate for p in pick_peaks(env, rate)]
        grid = beat_grid(y, fs, tempo=tempo, onsets_s=np.array(on) if len(on) else None)
    if not grid["bpm"] or (grid["detected"] and grid["confidence"] < 0.25):
        warn.append(f"tempo unclear (best guess {grid['bpm']}); timing assumes 120 BPM - give tempo= for bars that line up")
        grid = beat_grid(y, fs, tempo=120.0)
    ch = None
    if "chords" in modes:
        ch = chord_track(y, fs, None, key=key)
        if not tempo and drum is None and not mel and (not grid["detected"] or grid["confidence"] < 0.7):
            tb, _ = tempo_from_changes(ch["segments"])
            if tb:
                grid = beat_grid(y, fs, tempo=tb, onsets_s=np.array([s_ for s_, _, l_ in ch["segments"] if l_]))
                grid.update(detected=True, confidence=0.4)
                warn.append(f"tempo guessed from the chord changes ({round(tb, 1)} BPM, a change every few beats); "
                            "give tempo= if you know it")
    bpm = float(grid["bpm"]); period = 60.0 / bpm
    beats = np.asarray(grid["beats"], float)
    if ch is not None:
        ch.update(chord_beats(ch, beats if len(beats) >= 2 else np.arange(0, dur, period)))
        steps["chords"] = round(time.time() - t, 2); t = time.time()
    g, strength = _grid_beats(quantize)
    al = align if align in ("audio", "grid") else ("grid" if g else "audio")
    origin = 0.0
    if al == "grid" and len(beats):
        down = drum["downbeat_beat"] if drum is not None else 0
        first = beats[min(down, len(beats) - 1)]
        bar_s = beats_per_bar * period
        events_t = [h["t"] for h in drum["hits"]] if drum else []
        events_t += [n["start"] for p in mel.values() for n in p["events"]]
        t_min = min(events_t) if events_t else 0.0
        origin = first - math.ceil(max(0.0, first - t_min - 0.25 * period) / bar_s - 1e-9) * bar_s   # pickups: a bar back
    def tob(s):                                                  # seconds -> beats; a hair before 0 is 0
        b = (s - origin) / period
        return 0.0 if -0.25 < b < 0 else b
    if drum is not None:
        ev = []
        for h in drum["hits"]:
            b = _snap(tob(h["t"]), g, strength)
            ev.append((b, 0.25, h["gm"], h["vel"], h["cls"]))
        ev.sort()
        counts = {}
        for e in ev:
            counts[e[4]] = counts.get(e[4], 0) + 1
        notes_beats["drums"] = [e[:4] for e in ev]
        n_on = drum["stats"].get("onsets", 0)
        expl = len({round(h["t"], 4) for h in drum["hits"]}) / max(n_on, 1)
        parts["drums"] = {"hits": len(ev), "counts": counts, "onsets": n_on,
                          "confidence": round(float(min(1.0, expl * (0.5 + 0.5 * grid["confidence"]))), 2),
                          "gm": {c: drum_spec["gm"] for c, drum_spec in data()["drums"].items()}}
    for m, p in mel.items():
        ev = []
        for n in p["events"]:
            b0 = _snap(tob(n["start"]), g, strength); b1 = _snap(tob(n["end"]), g, strength)
            if b1 - b0 < (g or 0.05) * 0.5:
                b1 = b0 + (g or 0.125)
            ev.append((b0, b1 - b0, n["pitch"], n["vel"]))
        notes_beats[m] = ev
        cents = [abs(n["cents"]) for n in p["events"]]
        parts[m] = {"notes": len(ev), "confidence": p["confidence"],
                    "range": f"{note_name(min(e[2] for e in ev))}-{note_name(max(e[2] for e in ev))}" if ev else None,
                    "avg_cents_off": round(float(np.mean(cents)), 1) if cents else None}
        if p["events"] and p.get("f0_support_db", 0) < -18:
            warn.append(f"{m}: this sounds polyphonic (the pitch found has no energy of its own) - try mode='chords'")
        elif p["events"] and p["confidence"] < 0.6:
            warn.append(f"{m}: low pitch confidence ({p['confidence']}) - noisy or not one note at a time?")
    if "chords" in modes:
        labs = ch["labels"]
        segs = []
        for i, lab in enumerate(labs):
            b = tob(beats[i]) if i < len(beats) else tob(beats[-1] + (i - len(beats) + 1) * period)
            if segs and segs[-1][2] == lab:
                segs[-1][1] += 1.0
            else:
                segs.append([round(b, 4), 1.0, lab])
        if g:
            for s_ in segs:
                s_[0] = _snap(s_[0], g, strength)
        bars = {}
        for b0, ln, lab in segs:
            bi = int(math.floor((b0 + 0.1) / beats_per_bar)) + 1           # a hair early still counts for its bar
            if lab:
                lst = bars.setdefault(bi, [])
                if not lst or lst[-1] != lab:
                    lst.append(lab)
        nb = max(bars) if bars else 0
        chart = " ".join(f"| {' '.join(bars.get(i, ['-']))}" for i in range(1, nb + 1)) + (" |" if nb else "")
        marg = float(np.mean(ch["margins"])) if ch["margins"] else 0.0
        sc_ = float(np.mean(ch["scores"])) if ch["scores"] else 0.0
        cconf = round(float(np.clip(sc_ * 0.6 + marg * 4, 0, 1)), 2)
        notes_beats["chords"] = [tuple(n) for n in voice_chords(                 # 7ths only from a confident chart
            [tuple(s_) for s_ in segs], sevenths=cconf >= float(data().get("seventh_voice_min_conf", 0.7)))]
        parts["chords"] = {"chart": chart[:2000], "changes": len([s_ for s_ in segs if s_[2]]), "confidence": cconf,
                           "key": ch["key"][0], "key_confidence": ch["key"][1], "tuning_cents": ch["tuning_cents"]}
        if full:
            parts["chords"]["per_beat"] = labs
        if cconf < 0.6:
            warn.append("chords: low confidence - polyphonic audio is hard to transcribe; check the chart by ear")
        else:
            warn.append("chords: polyphonic transcription is approximate (inversions/extensions may be off) - "
                        "listen before you build on it")
    # key over everything pitched
    key_est = None
    pitched = [n for m in ("melody", "bass") for n in notes_beats.get(m, [])]
    if pitched:
        import fl_music
        k_ = fl_music.detect_key([{"key": n[2], "len": int(n[1] * 480)} for n in pitched], 2)
        key_est = {"best": k_[0][0], "correlation": k_[0][1], "runner_up": k_[1][0] if len(k_) > 1 else None}
    elif "chords" in parts:
        key_est = {"best": parts["chords"]["key"], "correlation": parts["chords"]["key_confidence"]}
    steps["notes"] = round(time.time() - t, 2); t = time.time()
    res = {"input": input if isinstance(input, str) else "(array)", "seconds": round(dur, 2), "mode": modes if len(modes) > 1 else modes[0],
           "tempo": {"bpm": round(bpm, 2), "detected": grid["detected"], "confidence": grid["confidence"],
                     "alternatives": [a for a, _ in grid.get("alternatives", [])][:2]},
           "key": key_est if not key else {"given": key, "estimated": key_est and key_est["best"]},
           "align": al, "audio_starts_at_beat": round(-origin / period, 3), "parts": parts}
    if detected:
        res["detected"] = detected
    if g:
        res["quantized"] = {"grid_beats": g, "strength": strength}
    bpb = beats_per_bar
    inline = {}
    for m, ev in notes_beats.items():
        rows = [[round(float(1 + b / bpb), 4), round(float(ln / bpb), 4), int(k), int(v)] for b, ln, k, v in ev if b >= -1e-6]
        inline[m] = rows
    res["notes"] = {m: (r if full else r[:int(limit)]) for m, r in inline.items()}
    if any(len(r) > int(limit) for r in inline.values()) and not full:
        res["notes_truncated"] = {m: len(r) for m, r in inline.items() if len(r) > int(limit)}
    if output_mid:
        res["output_mid"] = write_midi(output_mid, notes_beats, bpm, beats_per_bar=beats_per_bar)
        steps["write_mid"] = round(time.time() - t, 2); t = time.time()
    if project:
        res["project"] = _write_to_project(project, notes_beats, channel, pattern, place_at_bar, bpb)
        steps["project"] = round(time.time() - t, 2)
    if warn:
        res["warnings"] = warn
    lead = next((m for m in ("melody", "bass", "chords", "drums") if notes_beats.get(m)), None)
    if lead:                                                     # the hand-off for flh_compose (capture_and_build)
        PPQ = 480
        tn = [{"pos": int(round(max(0.0, b) * PPQ)), "len": max(1, int(round(ln * PPQ))), "key": int(k), "vel": int(v)}
              for b, ln, k, v in notes_beats[lead]]
        an = analyze_notes(tn, PPQ, bpm, bpb) if lead != "drums" else {}
        if "chords" in parts and lead != "chords":
            an["chords_per_bar"] = [c.strip().split(" ")[0] for c in parts["chords"]["chart"].split("|")[1:-1]]
        idea = _idea(tn, PPQ, round(bpm, 2), bpb, "audio:" + lead, an)
        idea["part"] = lead
        _remember_idea(idea)
        res["idea"] = dict(idea, notes=idea["notes"][:int(limit)] if not full else idea["notes"])
    res["listen"] = "a transcription is a draft: play it back against the audio before relying on it"
    steps["total"] = round(time.time() - T0, 2)
    res["steps_s"] = steps
    return res


def _time_sig_meta(beats_per_bar):
    """Time-signature meta event for a bar of `beats_per_bar` quarter notes: 3 -> 3/4, 3.5 -> 7/8, 6 -> 6/4."""
    q = float(beats_per_bar or 4)
    num, den = (int(q), 4) if abs(q - round(q)) < 1e-6 else (int(round(q * 2)), 8)
    num = max(1, min(255, num)); dpow = {4: 2, 8: 3}[den]
    clocks = 24 if den == 4 else 12
    return b"\xff\x58\x04" + bytes([num, dpow, clocks, 8])


def write_midi(path, parts_beats, bpm, div=480, beats_per_bar=4):
    """{part: [(start_beat, len_beats, key, vel)]} -> type-1 .mid (tempo track + one track per part), with the
    real time signature (beats_per_bar quarter notes per bar)."""
    if not str(path).lower().endswith((".mid", ".midi")):
        raise ValueError("output_mid must end in .mid")
    w = _ns("writable")
    if w:
        w(path)
    chans = data()["midi_channels"]
    tracks = [("tempo", [(0, b"\xff\x51\x03" + int(round(60_000_000 / bpm)).to_bytes(3, "big")),
                         (0, _time_sig_meta(beats_per_bar))])]
    total = 0
    for i, (part, ev) in enumerate(parts_beats.items()):
        mc = int(chans.get(part, i)) & 15
        evs = []
        for b, ln, k, v in ev:
            s = int(round(max(0.0, b) * div)); e = max(s + 1, int(round(max(0.0, b + ln) * div)))
            evs.append((s, bytes([0x90 | mc, int(k) & 127, max(1, min(127, int(v)))])))
            evs.append((e, bytes([0x80 | mc, int(k) & 127, 0])))
        tracks.append((part, evs)); total += len(ev)
    mw = _ns("midi_write")
    if mw is None:
        raise RuntimeError("the server's MIDI writer isn't available")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    mw(path, div, tracks)
    return {"path": path, "tracks": list(parts_beats), "notes": total, "bpm": round(bpm, 2)}


def _unique_pattern_name(project, base):
    try:
        names = {p["name"].lower() for p in H["flp_patterns"](project)}
    except Exception:
        names = set()
    if base.lower() not in names:
        return base
    i = 2
    while f"{base} {i}".lower() in names:
        i += 1
    return f"{base} {i}"


def _write_to_project(project, parts_beats, channel, pattern, place_at_bar, bpb):
    """New named pattern (placed at place_at_bar) + the notes on the given channel(s), in ONE flp_edit.
    channel: an index (every part on it), or {part or drum class: index} ({"kick": 0, "snare": 1, ...})."""
    if channel is None:
        raise ValueError("give channel= (an index, or {part: index}) to write into the project")
    if not isinstance(channel, dict):
        chmap = {p: int(channel) for p in parts_beats}
    else:
        chmap = {str(k): int(v) for k, v in channel.items()}
    drum_cls = {v["gm"]: c for c, v in data()["drums"].items()}
    for c, v in data()["drums"].items():
        for k in [v.get("open_gm")] + list(v.get("tom_gm") or []):
            if k:
                drum_cls[k] = c
    per_ch = {}
    for part, ev in parts_beats.items():
        for b, ln, k, v in ev:
            ch = chmap.get(part)
            if ch is None and part == "drums":
                ch = chmap.get(drum_cls.get(int(k), "perc"), chmap.get("drums"))
            if ch is None:
                continue
            per_ch.setdefault(ch, []).append({"note": int(k), "bar": 1, "beat": round(max(0.0, b), 4),
                                              "beats": round(max(ln, 1 / 96), 4), "velocity": int(v)})
    if not per_ch:
        raise ValueError(f"no channel given for parts {sorted(parts_beats)}")
    ops = []
    if pattern is None:
        name = _unique_pattern_name(project, "Transcribed " + "+".join(parts_beats))
        end = max(n["beat"] + n["beats"] for ns_ in per_ch.values() for n in ns_)
        op = {"tool": "flp_new_pattern", "name": name}
        if place_at_bar:
            op.update(place_at_bar=float(place_at_bar), length_bars=max(1, math.ceil(end / 4 - 1e-9)))
        ops.append(op)
        pat = name
    else:
        pat = pattern
    for ch, ns_ in sorted(per_ch.items()):
        ops.append({"tool": "flp_add_notes", "pattern": pat, "channel": ch, "notes": ns_, "replace": pattern is not None})
    r = H["flp_edit"](project, ops)
    return {"pattern": pat, "channels": {str(c): len(v) for c, v in per_ch.items()}, "edit": {k: v for k, v in r.items()
                                                                                            if k in ("ops", "output", "reloaded_in_fl", "steps_s")}}


# ============================================================================= ideas: analysis shared by capture paths
def analyze_notes(notes, ppq, bpm=None, beats_per_bar=4):
    """Quick musical read of played notes [{pos, len, key, vel}] (ticks): key, implied chords per bar, timing feel
    (how far from the 1/16 grid, swing), range, density. Also ns["score_view"] when flh_score is installed."""
    import fl_music
    if not notes:
        return {}
    P = int(ppq); bar = int(round(beats_per_bar * P))
    notes = sorted(notes, key=lambda n: (n["pos"], n["key"]))
    k = fl_music.detect_key(notes, 2)
    ch = fl_music.chords_by_segment(notes, bar)
    chart = []
    for _, c in ch:
        chart.append(c or "-")
    g16 = P / 4.0
    dev = np.array([((n["pos"] / g16 + 0.5) % 1 - 0.5) * g16 for n in notes])
    ms_per_tick = 60000.0 / (bpm or 120.0) / P
    off_ms = float(np.mean(np.abs(dev)) * ms_per_tick)
    offs = [(n["pos"] % P) / P for n in notes if 0.3 < (n["pos"] % P) / P < 0.8]
    swing = round(float(np.median(offs)), 2) if len(offs) >= 4 else None
    feel = "on the grid" if off_ms < 4 else "tight" if off_ms < 12 else "loose / rubato" if off_ms > 30 else "human"
    if swing and swing >= 0.56:
        feel += f", swung (off-beats at {swing:.2f} of the beat)"
    end = max(n["pos"] + n["len"] for n in notes)
    bars = max(1, int(math.ceil(end / bar - 1e-9)))
    lens = sorted(n["len"] / P for n in notes)
    res = {"key": k[0][0] if k else None, "key_runner_up": k[1][0] if len(k) > 1 else None,
           "chords_per_bar": chart[:64], "feel": feel, "avg_off_grid_ms": round(off_ms, 1), "swing": swing,
           "range": f"{note_name(min(n['key'] for n in notes))}-{note_name(max(n['key'] for n in notes))}",
           "notes_per_bar": round(len(notes) / bars, 1), "bars": bars,
           "median_note_beats": round(lens[len(lens) // 2], 3),
           "polyphony": round(_polyphony(notes), 2)}
    sv = _ns("score_view")
    if sv:
        try:
            v = sv(notes=[{"pos": n["pos"], "len": n["len"], "key": n["key"], "vel": n.get("vel", 90), "ch": 0}
                          for n in notes], ppq=P, bpm=bpm, beats_per_bar=beats_per_bar)
            if isinstance(v, dict):                              # the musical read only, compact
                v = {"key": (v.get("key") or [])[:2], "chords": (v.get("chords") or [])[:32],
                     "progression": v.get("progression"),
                     "roles": [{k: p.get(k) for k in ("role", "rhythm", "low", "high")} for p in (v.get("parts") or [])][:4]}
            res["score_view"] = v
        except Exception as e:                                   # never let the optional view break a capture
            res["score_view_error"] = f"{type(e).__name__}: {e}"
    return res


def _polyphony(notes):
    """Average number of notes sounding while anything sounds."""
    ev = sorted([(n["pos"], 1) for n in notes] + [(n["pos"] + n["len"], -1) for n in notes])
    cur = tot = span = 0; last = None
    for t, d in ev:
        if last is not None and cur > 0:
            tot += cur * (t - last); span += t - last
        cur += d; last = t
    return tot / span if span else 1.0


def _idea(notes, ppq, bpm, beats_per_bar, source, analysis=None, origin_tick=0):
    """The hand-off format for flh_compose: inline notes [bar, length_bars, pitch, vel] in song bars."""
    bar = beats_per_bar * ppq
    rows = [[round(1 + (n["pos"] - origin_tick) / bar, 4), round(n["len"] / bar, 4), int(n["key"]), int(n.get("vel", 90))]
            for n in sorted(notes, key=lambda n: (n["pos"], n["key"]))]
    a = analysis or {}
    return {"notes": rows, "beats_per_bar": beats_per_bar, "bpm": bpm, "key": a.get("key"),
            "chords": a.get("chords_per_bar"), "bars": a.get("bars"), "source": source}


def capture_from_midi_file(path, beats_per_bar=4, analyze=True, limit=200, trim=True):
    """An idea from an exported .mid (the user played it in any DAW / app): notes, tempo, quick analysis."""
    if not os.path.isfile(path):
        raise ValueError(f"no such MIDI file: {path}")
    div, raw, tm = H["midi_read"](path)
    if not raw:
        raise ValueError("that MIDI file has no notes")
    bpm = tm[0][1] if tm else None
    notes = [{"pos": s, "len": max(1, e - s), "key": k, "vel": v} for s, e, k, v in raw]
    origin = 0
    if trim:                                                     # leading silence: start at the first played bar
        bar = beats_per_bar * div
        origin = (min(n["pos"] for n in notes) // bar) * bar
    for n in notes:
        n["pos"] -= origin
    an = analyze_notes(notes, div, bpm, beats_per_bar) if analyze else {}
    idea = _idea(notes, div, bpm, beats_per_bar, "midi_file", an)
    _remember_idea(idea)
    out = {"path": path, "notes": len(notes), "bpm": bpm, "tempo_changes": max(0, len(tm) - 1), "analysis": an,
           "idea": dict(idea, notes=idea["notes"][:int(limit)])}
    if trim and origin:
        out["trimmed_bars"] = origin // (beats_per_bar * div)
    return out


def _remember_idea(idea):
    try:
        st = H["_state"]
        st({"flh_last_idea": idea})
    except Exception:
        pass


# ============================================================================= capture from FL (record the user's playing)
_CAP = {}


def _call(cmd, **a):
    return H["BRIDGE"].call(cmd, **a)


def _bpb():
    try:
        return H["_open_bpb"]()
    except Exception:
        return 4


_SAVE_WHY = {"why": None}          # why the last _save_and_wait was refused by the server's save guards


def _save_and_wait(project, tries=2, timeout=8.0):
    """FL saves the open project - through the server's guarded save (never while FL holds an older copy than the
    file, a preview's live mutes are on, FL shows another project, or the file changed behind FL) - and waits until
    the file landed. -> seconds taken, or None (_SAVE_WHY['why'] says why when it was refused).
    PC-VERIFY: FPT_Save right after stopping a recording writes the recorded notes (no prompt for a saved project)."""
    _SAVE_WHY["why"] = None
    t0 = time.time()
    for _ in range(tries):
        if H["_save_in_fl_and_wait"](project, timeout):
            return round(time.time() - t0, 2)
        why = H["_save_refused_why"]() if H.get("_save_refused_why") else None
        if why:                                                  # refused: asking again changes nothing
            _SAVE_WHY["why"] = why
            return None
    return None


def _nkey(n):
    return [int(n["pos"]), int(n["len"]), int(n["key"]), int(n["ch"])]


def _new_notes(before_keys, notes):
    """Notes not in the before snapshot (as a multiset of position/length/pitch/channel), order kept."""
    from collections import Counter
    left = Counter(tuple(k) for k in before_keys); out = []
    for n in notes:
        k = tuple(_nkey(n))
        if left[k] > 0:
            left[k] -= 1
        else:
            out.append(n)
    return out


def _pattern_notes(project, pid):
    # PC-VERIFY: pattern numbers in the .flp equal FL's pattern numbers (patterns.patternNumber / jumpToPattern)
    d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
    pats = H["_patterns"](d, ev)
    tempo = None
    try:
        tempo = H["flp_info"](project).get("tempo_bpm")
    except Exception:
        pass
    p = pats.get(int(pid)) if pid is not None else None
    notes = [H["_rec_to_note"](r) for r in (p or {"notes": []})["notes"]]
    for n in notes:
        n.pop("_r", None)
    return notes, P, tempo, {k: v["name"] for k, v in pats.items()}


def _open_project():
    try:
        path, titles = H["_open_project_guess"]()
    except Exception:
        path, titles = None, []
    untitled = None
    try:
        main = H["_fl_main_window"]()
        untitled = H["_is_untitled"](main[1]) if main else None
    except Exception:
        pass
    return path, untitled


def _popups():
    # PC-VERIFY: if FL shows its "what would you like to record?" box on arming, it is listed here (by its title)
    try:
        return [{"title": d["title"], "kind": d["kind"], "buttons": d["buttons"][:4]} for d in H["fl_dialogs"]()]
    except Exception:
        return []


def _toggle_to(state, key, want, action, bpb, done):
    """FL's metronome / song mode / record / countdown are toggles: flip only when needed, remember to restore."""
    if want is None or state.get(key) is None or bool(state[key]) == bool(want):
        return
    _call("song", action=action, bpb=bpb)
    done.append(action)


def fl_capture_idea(bars=None, seconds=None, channel=None, count_in=True, metronome=True, action="record",
                    pattern=None, name=None, count_in_bars=1, analyze=True, limit=200):
    """Record what the user plays on their MIDI keyboard in the open FL, into a fresh pattern, and return the notes
    plus a quick analysis. action: record (bars/seconds: timed; neither: starts and returns - then action=stop),
    stop, read (fallback: the user recorded by hand; the newest notes of the selected/given pattern), cancel."""
    action = (action or "record").lower()
    if action not in ("record", "stop", "read", "cancel"):
        raise ValueError("action: record, stop, read or cancel")
    T0 = time.time(); steps = {}
    project, untitled = _open_project()
    if action in ("stop", "cancel"):
        sess = _CAP.get("session") or (H["_state"]().get("flh_capture") if "_state" in H else None)
        if not sess:
            raise ValueError("no capture is running (start one with fl_capture_idea(action='record'))")
        return _finish(sess, read=action == "stop", analyze=analyze, limit=limit, steps=steps, T0=T0)
    if not project or untitled:
        raise ValueError("save the project in FL once first (File > Save): fl-headless reads what FL recorded from "
                         "the saved .flp" + ("" if project else " (no saved project open in FL was found)"))
    H["writable"](project)                                       # FL saves it for us: same rule as our own writes
    bpb = _bpb()
    if action == "read":
        return _read_newest(project, pattern, channel, bpb, analyze, limit, steps, T0)
    try:
        st = _call("state")
    except Exception as e:
        raise RuntimeError(f"FL's bridge isn't answering ({type(e).__name__}: {e}); run "
                           f"{C.tool_call(H, 'fl_capture_probe')} / {C.tool_call(H, 'fl_bridge_setup')}, or record in "
                           f"FL yourself and use {C.tool_call(H, 'fl_capture_idea', {'action': 'read'})}")
    tempo = float(st.get("tempo") or 120.0)
    if st.get("playing"):
        _call("transport", action="stop")
    ctx = _call("context", bpb=bpb)
    ch = int(channel) if channel is not None else ((ctx.get("channel") or {}).get("index") or 0)
    # a fresh pattern: one past the highest FL or the file knows
    try:
        file_max = max((p["pattern"] for p in H["flp_patterns"](project)), default=0)
    except Exception:
        file_max = 0
    fl_pats = st.get("patterns") or []
    new = max(file_max, len(fl_pats) if len(fl_pats) < 64 else 0) + 1 if pattern is None else int(pattern)
    pname = name or ("Idea %d" % new)
    if pattern is not None:                                      # recording into an existing pattern: save first so
        if _save_and_wait(project) is None and _SAVE_WHY["why"]:  # the "before" snapshot is what FL really holds
            raise RuntimeError(f"nothing was recorded: {_SAVE_WHY['why']}")
    snapshot = _pattern_notes(project, new)[0] if pattern is not None else []
    # PC-VERIFY: jumpToPattern(new) on a pattern number past the last one creates an empty pattern and selects it;
    # naming it afterwards works (FL may refuse names on patterns that don't exist yet, hence select first)
    _call("pattern_set", index=new, select=True)
    if pattern is None:
        _call("pattern_set", index=new, name=pname)
    # PC-VERIFY: a keyboard on FL's generic controller plays (and records into) the selected channel
    _call("channel_set", index=ch, select=True)
    where = _call("song", action="where", bpb=bpb)
    flipped = []
    _toggle_to(where, "song_mode", False, "song_mode", bpb, flipped)        # pattern mode: record into the pattern
    _toggle_to(where, "metronome", bool(metronome), "metronome", bpb, flipped)
    cnt_ok = "countdown" in where
    if cnt_ok:
        _toggle_to(where, "countdown", bool(count_in), "countdown", bpb, flipped)
    # PC-VERIFY: with FL's loop recording on, a new (1-bar) pattern loops while recording and longer takes stack up
    # in bar 1; switch it off for the take when the bridge can (song action loop_record, state in 'where')
    if "loop_record" in where:
        _toggle_to(where, "loop_record", False, "loop_record", bpb, flipped)
    _call("song", action="goto", bar=1.0, bpb=bpb)                # PC-VERIFY: in pattern mode this is the pattern start
    if not where.get("recording"):
        _call("song", action="record", bpb=bpb); flipped.append("record")
    _call("transport", action="play")                            # PC-VERIFY: record armed + play = FL records notes
    t_play = time.time()
    steps["arm"] = round(t_play - T0, 2)
    bar_s = bpb * 60.0 / tempo
    # FL's count-in happens before recording starts; when the bridge can't switch it we don't know FL's setting,
    # so allow for it (a bar of extra recording is harmless, a cut-off ending isn't)
    pre = float(count_in_bars) * bar_s if (count_in or not cnt_ok) else 0.0   # PC-VERIFY: FL's count-in length (bars)
    sess = {"project": project, "pattern": new, "pattern_name": pname, "channel": ch, "tempo": tempo, "bpb": bpb,
            "flipped": [f for f in flipped if f != "record"], "armed_record": "record" in flipped, "t_play": t_play,
            "count_in_s": pre, "snapshot": [_nkey(n) for n in snapshot], "count_in": bool(count_in) and cnt_ok}
    _CAP["session"] = sess
    try:
        H["_state"]({"flh_capture": sess})
    except Exception:
        pass
    if bars is None and seconds is None:
        return {"recording": True, "pattern": new, "pattern_name": pname, "channel": ch, "tempo": tempo,
                "count_in": sess["count_in"], "metronome": bool(metronome),
                "next": "play your idea now; when done call fl_capture_idea(action='stop')",
                **({} if cnt_ok else {"note": "this bridge can't switch FL's count-in; FL's own countdown setting applies"})}
    dur = float(seconds) if seconds is not None else float(bars) * bar_s
    end = t_play + pre + dur + 0.25
    while time.time() < end:                                     # wait; stop early if the user stops FL
        time.sleep(min(1.0, max(0.0, end - time.time())))
        if time.time() < end - 0.5:
            try:
                if _call("context", bpb=bpb).get("playing") is False:   # lighter than state; the user hit stop
                    sess["stopped_by_user"] = True; break
            except Exception:
                pass
    steps["record"] = round(time.time() - t_play, 2)
    return _finish(sess, read=True, analyze=analyze, limit=limit, steps=steps, T0=T0)


def _finish(sess, read, analyze, limit, steps, T0):
    bpb = sess.get("bpb", 4)
    t = time.time()
    try:
        _call("transport", action="stop")
    except Exception:
        pass
    restored = []
    try:
        w = _call("song", action="where", bpb=bpb)
        if w.get("recording") and sess.get("armed_record", True):    # disarm only what we armed
            _call("song", action="record", bpb=bpb); restored.append("record off")
        for a in sess.get("flipped", []):                         # put FL's toggles back the way the user had them
            _call("song", action=a, bpb=bpb); restored.append(a)
    except Exception as e:
        restored.append(f"restore failed: {type(e).__name__}: {e}")
    _CAP.pop("session", None)
    try:
        H["_state"]({"flh_capture": None})
    except Exception:
        pass
    steps["stop"] = round(time.time() - t, 2)
    out = {"pattern": sess["pattern"], "pattern_name": sess.get("pattern_name"), "channel": sess["channel"],
           "restored": restored}
    if sess.get("stopped_by_user"):
        out["stopped_by_user"] = True
    if not read:
        out["cancelled"] = True; return out
    t = time.time()
    saved = _save_and_wait(sess["project"])
    steps["save"] = round(time.time() - t, 2)
    if saved is None:
        out["error"] = (("FL wasn't saved (%s), " % _SAVE_WHY["why"] if _SAVE_WHY["why"] else
                         "FL didn't save the project in time, ") +
                        "so the recorded notes couldn't be read yet; they are "
                        "in FL's pattern '%s' - try fl_capture_idea(action='read', pattern=%d)" % (sess.get("pattern_name"), sess["pattern"]))
        out["popups"] = _popups()
        return out
    notes, P, tempo, names = _pattern_notes(sess["project"], sess["pattern"])
    if sess.get("snapshot"):
        notes = _new_notes(sess["snapshot"], notes)
    return _report(out, notes, P, tempo or sess.get("tempo"), bpb, analyze, limit, steps, T0, "fl_capture")


def _report(out, notes, P, tempo, bpb, analyze, limit, steps, T0, source):
    if not notes:
        out["notes"] = 0
        out["nothing_recorded"] = ("no notes arrived: check that your keyboard is enabled in FL (Options > MIDI "
                                   "settings > Input: select it, Enable on), that FL's recording filter has 'Notes' "
                                   "on, and that you played after the count-in. Or record by hand in FL and call "
                                   "fl_capture_idea(action='read').")
        pops = _popups()
        if pops:
            out["popups"] = pops
        out["steps_s"] = dict(steps, total=round(time.time() - T0, 2))
        return out
    per = {}
    for n in notes:
        per[n["ch"]] = per.get(n["ch"], 0) + 1
    an = analyze_notes(notes, P, tempo, bpb) if analyze else {}
    origin = (min(n["pos"] for n in notes) // int(bpb * P)) * int(bpb * P)
    idea = _idea(notes, P, tempo, bpb, source, an, origin_tick=origin)
    _remember_idea(idea)
    out.update(notes=len(notes), notes_per_channel=per, bpm=tempo, analysis=an,
               idea=dict(idea, notes=idea["notes"][:int(limit)]))
    if len(idea["notes"]) > int(limit):
        out["idea_truncated"] = len(idea["notes"])
    out["steps_s"] = dict(steps, total=round(time.time() - T0, 2))
    return out


def _read_newest(project, pattern, channel, bpb, analyze, limit, steps, T0):
    """Fallback: the user recorded in FL themselves. Save, then take the newest notes: the pattern that changed
    since the last capture snapshot, else the selected (or given) pattern."""
    t = time.time()
    before = {}
    try:                                                         # what the file held at the last save
        d = H["_load"](project); ev = H["_events"](d)
        before = {k: [_nkey(H["_rec_to_note"](r)) for r in v["notes"]] for k, v in H["_patterns"](d, ev).items()}
    except Exception:
        pass
    pid = pattern
    if pid is None:
        try:
            pid = ((_call("context", bpb=bpb) or {}).get("pattern") or {}).get("index")
        except Exception:
            pid = None
    saved = _save_and_wait(project)
    steps["save"] = round(time.time() - t, 2)
    if saved is None:
        if _SAVE_WHY["why"]:
            return {"error": f"FL wasn't saved, so the recorded notes can't be read: {_SAVE_WHY['why']}",
                    "popups": _popups()}
        return {"error": "FL didn't save the project in time; retry in a few seconds", "popups": _popups()}
    d = H["_load"](project); ev = H["_events"](d)
    pats = H["_patterns"](d, ev)
    added = {k: len(_new_notes(before.get(k, []), [H["_rec_to_note"](r) for r in v["notes"]])) for k, v in pats.items()}
    changed = [k for k, v in added.items() if v]
    if pid is None and changed:
        pid = max(changed, key=lambda k: added[k])
    if pid is None:
        raise ValueError("couldn't tell which pattern you recorded into: select it in FL or give pattern=")
    if isinstance(pid, str) and not str(pid).isdigit():
        pid = H["_pattern_id"](d, ev, pid)
    notes, P, tempo, names = _pattern_notes(project, pid)
    new_only = int(pid) in changed and bool(before.get(int(pid)))
    if new_only:                                                 # only what arrived since the last save
        notes = _new_notes(before[int(pid)], notes)
    if channel is not None:
        notes = [n for n in notes if n["ch"] == int(channel)]
    out = {"pattern": int(pid), "pattern_name": names.get(int(pid)), "channel": channel, "only_new_notes": new_only}
    if new_only:
        out["note"] = "notes added since the project was last saved (other unsaved edits there would show too)"
    return _report(out, notes, P, tempo, bpb, analyze, limit, steps, T0, "fl_read")


def _winmm_inputs():
    """MIDI input devices Windows knows (names), or None off Windows."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class MIDIINCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("dwSupport", wintypes.DWORD)]
    w = ctypes.windll.winmm                                      # PC-VERIFY: names match FL's MIDI settings list
    out = []
    for i in range(w.midiInGetNumDevs()):
        c = MIDIINCAPSW()
        if w.midiInGetDevCapsW(i, ctypes.byref(c), ctypes.sizeof(c)) == 0:
            out.append(c.szPname)
    return out


def fl_capture_probe():
    """Read-only check of everything capture needs: bridge, transport toggles FL reports, the selected pattern/
    channel, a saved project, MIDI keyboards Windows sees, popups. Changes nothing in FL."""
    res = {"checks": {}, "next": []}
    c = res["checks"]
    try:
        _call("ping"); c["bridge"] = True
    except Exception as e:
        c["bridge"] = False
        res["next"].append(f"bridge not answering ({type(e).__name__}): run {C.tool_call(H, 'fl_bridge_setup')}")
        return res
    bpb = _bpb()
    st = _call("state")
    c["tempo"] = st.get("tempo"); c["playing"] = st.get("playing")
    c["patterns_in_fl"] = len(st.get("patterns") or [])
    w = _call("song", action="where", bpb=bpb)
    c["metronome"] = w.get("metronome"); c["record_armed"] = w.get("recording"); c["song_mode"] = w.get("song_mode")
    c["countdown_control"] = "countdown" in w
    c["countdown"] = w.get("countdown"); c["loop_record"] = w.get("loop_record")
    if not c["countdown_control"]:
        res["next"].append("bridge can't switch count-in yet (needs the 'countdown' song action); FL's own setting applies")
    if "loop_record" not in w:
        res["next"].append("bridge can't switch loop recording: if FL's Loop recording is on, takes longer than the "
                           "pattern stack up - turn it off in FL's recording menu, or update the bridge")
    elif w.get("loop_record"):
        res["next"].append("FL's loop recording is on: capture switches it off for the take and back after")
    ctx = _call("context", bpb=bpb)
    c["selected_channel"] = (ctx.get("channel") or {}).get("index"); c["selected_pattern"] = (ctx.get("pattern") or {}).get("index")
    project, untitled = _open_project()
    c["project"] = project; c["project_saved"] = bool(project) and not untitled
    if not c["project_saved"]:
        res["next"].append("save the project in FL once (capture reads the recorded notes from the saved .flp)")
    ins = _winmm_inputs()
    if ins is not None:
        virtual = [n for n in ins if "loopmidi" in n.lower() or "fl-headless" in n.lower()]
        c["midi_inputs"] = ins
        c["keyboards"] = [n for n in ins if n not in virtual]
        if not c["keyboards"]:
            res["next"].append("no MIDI keyboard found by Windows (only virtual ports) - plug it in / install its driver")
    c["popups"] = _popups()
    res["ready"] = bool(c["bridge"] and c["project_saved"] and (ins is None or c.get("keyboards")))
    res["try"] = "fl_capture_idea(bars=4) records 4 bars into a new pattern; or record yourself and call fl_capture_idea(action='read')"
    return res


# ============================================================================= build around an idea
def _load_idea(idea):
    if idea is None or idea == "last":
        got = H["_state"]().get("flh_last_idea") if "_state" in H else None
        if not got:
            raise ValueError("no idea captured yet: use fl_capture_idea, capture_from_midi_file or audio_to_midi first")
        return got
    if isinstance(idea, dict):
        if "idea" in idea:
            return idea["idea"]
        if "notes" in idea:
            return idea
    if isinstance(idea, str):
        if idea.lower().endswith((".mid", ".midi")):
            return capture_from_midi_file(idea, limit=10 ** 6)["idea"]
        if idea.lower().endswith((".wav", ".flac")):
            r = audio_to_midi(idea, mode="auto", full=True)
            if not r.get("idea"):
                raise ValueError("nothing could be transcribed from that audio")
            return r["idea"]
    raise ValueError("idea: 'last', a .mid/.wav path, or a result of fl_capture_idea / capture_from_midi_file")


def capture_and_build(idea="last", what=None, genre=None, project=None, options=None):
    """Build parts around an idea (chords, bass, drums, ...). Uses flh_compose's ns["compose_around"] when it is
    installed; otherwise returns the analysis plus concrete suggestions (implied chords, bass roots, drum style)."""
    what = list(what or ["chords", "bass", "drums"])
    idea_d = _load_idea(idea)
    fn = _ns("compose_around")
    if fn:
        return fn(idea=idea_d, what=what, genre=genre, project=project, **(options or {}))
    chords = [c for c in (idea_d.get("chords") or [])]
    sugg = {}
    if "chords" in what:
        sugg["chords"] = {"per_bar": chords or None,
                          "how": "voice these on a pad/keys channel (" + C.tool_name(H, "flp_add_notes") +
                                 ") or pick substitutes in the key"}
    if "bass" in what:
        roots = []
        for c in chords:
            if c and c != "-":
                r, _ = _parse_chord(c.split("/")[0]); roots.append(note_name(12 * 3 + r))
            else:
                roots.append(None)
        sugg["bass"] = {"roots_per_bar": roots, "how": "root on beat 1, fifth or octave on beat 3; follow the idea's rhythm"}
    if "drums" in what:
        sugg["drums"] = {"style": genre or "pop", "tool": "flp_drum_pattern",
                         "how": "pick the style from the genre; match the idea's feel (swing value in the analysis)"}
    return {"idea": {k: v for k, v in idea_d.items() if k != "notes"}, "notes": len(idea_d.get("notes") or []),
            "composer": None, "suggestions": sugg,
            "note": "flh_compose isn't installed, so nothing was written: these are suggestions"}


# ============================================================================= registration
def _plain(o):
    """numpy scalars/arrays -> plain JSON types (tool results go out as JSON)."""
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, np.ndarray):
        return [_plain(v) for v in o.tolist()]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _tool(fn):
    def run(**kw):
        return _plain(fn(**kw))
    run.__wrapped__ = fn; run.__name__ = fn.__name__; run.__doc__ = fn.__doc__
    return run


def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    STR, INT, NUM, BOOL = ns["_STR"], ns["_INT"], ns["_NUM"], ns["_BOOL"]
    ns["pitch_track"] = pitch_track                              # for flh_vocal and others: f0 track of (x, fs)
    ns["segment_notes"] = segment_notes
    ns["transcribe"] = audio_to_midi                             # for flh_compose: result["idea"] is the hand-off
    ns["beat_grid"] = beat_grid
    ns["analyze_notes"] = analyze_notes
    T = ns["TOOLS"]
    T["audio_to_midi"] = (
        _tool(audio_to_midi), "Listen to audio and write it down as MIDI: mode melody (one note at a time: voice, lead; "
        "vibrato/slides handled), bass, chords (chord chart per beat/bar + a voiced chord track), drums (kick/snare/"
        "hat/perc -> GM notes, tempo + beat tracking), or auto (decides and says how sure it is). Writes output_mid; "
        "returns notes inline [bar, length_bars, pitch, vel]; with project + channel writes them into a new pattern "
        "(one edit). quantize '1/16' (or {grid, strength}) snaps to the detected grid; default keeps the playing's "
        "timing. Confidence is reported honestly - polyphonic audio is hard; listen before relying on it.",
        S(input=STR("WAV or FLAC file"), output_mid=STR(".mid to write (inside FLH_ROOTS)", True),
          mode=STR("auto (default) | melody | chords | bass | drums", True),
          key=STR("known key, e.g. 'A minor' (borderline pitches snap into it)", True),
          tempo=NUM("known bpm (default: detected)", True), project=P("string", "write into this .flp", True),
          channel=P(["integer", "object"], "channel index, or {part/drum class: index} e.g. {kick: 0, snare: 1, hat: 2}", True),
          pattern=P(["integer", "string"], "existing pattern to fill (default: a new 'Transcribed ...' pattern)", True),
          quantize=P(["string", "number", "object"], "'1/16', '1/8', '1/8t', or {grid, strength 0..1}", True),
          beats_per_bar=INT("default 4", True), align=STR("audio (bar 1 = audio start) | grid (bars on the music's "
                                                         "downbeats) | auto", True),
          place_at_bar=NUM("where the new pattern goes in the playlist (default 1; 0 = don't place)", True),
          limit=INT("max inline notes per part (default 120)", True), full=BOOL("all notes and per-beat chords", True)))
    T["fl_capture_idea"] = (
        _tool(fl_capture_idea), "Play me your idea: records what you play on your MIDI keyboard in the open FL into a new "
        "pattern (count-in and metronome on by default) for bars/seconds, then returns the notes and a quick read "
        "(key, implied chords, feel). No bars/seconds: starts recording and returns - call again with action='stop'. "
        "action='read': you recorded in FL yourself - picks up the newest notes. Needs the project saved once.",
        S(bars=NUM("bars to record", True), seconds=NUM("or seconds", True),
          channel=INT("channel to play (default: the selected one)", True), count_in=BOOL("default true", True),
          metronome=BOOL("default true", True), action=STR("record (default) | stop | read | cancel", True),
          pattern=P(["integer", "string"], "record into / read from this pattern (default: a new one)", True),
          name=STR("name for the new pattern (default 'Idea N')", True),
          count_in_bars=NUM("length of FL's count-in in bars (default 1)", True),
          analyze=BOOL("default true", True), limit=INT("max notes inline (default 200)", True)))
    T["fl_capture_probe"] = (_tool(fl_capture_probe), "Check (read-only) that capture can work: bridge, FL's record/"
                             "metronome/count-in state, selected channel/pattern, saved project, MIDI keyboards "
                             "Windows sees, popups.", S())
    T["capture_from_midi_file"] = (
        _tool(capture_from_midi_file), "An idea from a .mid you exported or played elsewhere: notes inline, tempo, key, "
        "implied chords, feel - ready for capture_and_build.",
        S(path=STR(".mid file"), beats_per_bar=INT("default 4", True), analyze=BOOL("default true", True),
          limit=INT("max notes inline (default 200)", True), trim=BOOL("start at the first played bar (default true)", True)))
    T["capture_and_build"] = (
        _tool(capture_and_build), "Build parts around a captured idea: what ['chords', 'bass', 'drums'] (default), genre. "
        "idea: 'last' (default: the last capture/transcription), a .mid/.wav path, or a capture result. Uses the "
        "composer when installed, otherwise returns concrete suggestions.",
        S(idea=P(["string", "object"], "'last', a path, or a capture result", True),
          what=P("array", "parts to build", True, items={"type": "string"}), genre=STR("e.g. lofi, house, pop", True),
          project=P("string", "write into this .flp (composer)", True),
          options=P("object", "passed to the composer", True)))
