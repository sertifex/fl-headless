"""fl_piano_tx: polyphonic piano transcription with Spotify's basic-pitch model (Apache-2.0), run by onnx_lite.

The model (data/transcribe/nmp.onnx, shipped with fl-headless; see NOTICE.md) turns 2-second windows of 22.05 kHz
mono audio into three frame grids at ~86 fps: note (88 keys, is this key sounding), onset (88 keys, did it start
here) and contour (264 bins, 1/3 semitone pitch salience). The note-building below follows basic-pitch's own
post-processing (onset peaks -> forward energy walk, then the "melodia trick" for notes without a clear onset), so
results match the reference implementation; fl-headless's piano clean-up runs after it (flh_transcribe).

numpy only. onnxruntime, when installed, runs the model faster with the same result.
"""
import os
import time

import numpy as np

import onnx_lite as OL

SR = 22050
HOP = 256
FPS = SR / HOP                                   # model frames per second (86.13)
N_SAMPLES = SR * 2 - HOP                         # 43844 samples per window
ANNOT_FRAMES = 172                               # frames per window
OVERLAP_FRAMES = 30
OVERLAP = OVERLAP_FRAMES * HOP                   # 7680
WIN_HOP = N_SAMPLES - OVERLAP                    # 36164
MIDI_OFFSET = 21                                 # key 0 = A0
N_KEYS = 88
MODEL_NAME = "nmp.onnx"
# basic-pitch's defaults
ONSET_THRESH = 0.5
FRAME_THRESH = 0.3
MIN_NOTE_S = 0.1277
ENERGY_TOL = 11


class ModelMissing(Exception):
    pass


def model_path(data_dir=None):
    d = data_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "transcribe")
    p = os.path.join(d, MODEL_NAME)
    if not os.path.isfile(p):
        raise ModelMissing(f"the piano model {MODEL_NAME} is missing from {d} (it ships with fl-headless: reinstall "
                           "or copy it back)")
    return p


# ---------------------------------------------------------------- audio to 22.05 kHz
def resample(x, fs, target=SR, block_s=60.0):
    """Band-limited resampling by FFT, in overlapping blocks (long songs stay light on memory). float32 out."""
    x = np.asarray(x, np.float64)
    if fs == target:
        return x.astype(np.float32)
    n_out = int(round(len(x) * target / fs))
    blk = int(block_s * fs)
    if len(x) <= blk * 1.5:
        return _fft_resample(x, n_out, fs, target).astype(np.float32)
    pad = int(1.0 * fs)                                          # 1 s of context each side of a block
    out = np.empty(n_out, np.float32)
    a = 0
    while a < len(x):
        b = min(len(x), a + blk)
        lo, hi = max(0, a - pad), min(len(x), b + pad)
        seg = x[lo:hi]
        m = int(round(len(seg) * target / fs))
        y = _fft_resample(seg, m, fs, target)
        oa, ob = int(round(a * target / fs)), min(n_out, int(round(b * target / fs)))
        off = int(round((a - lo) * target / fs))
        out[oa:ob] = y[off:off + (ob - oa)]
        a = b
    return out


def _fft_resample(x, m, fs=None, target=None):
    n = len(x)
    if n == 0 or m == 0:
        return np.zeros(m)
    # mirror-pad to soften the circular wrap at the edges
    from math import gcd
    num, den = (target, fs) if fs and target else (m, n)
    g = gcd(int(num), int(den))
    num, den = int(num) // g, int(den) // g
    p = (min(n - 1, 2048) // den) * den                         # a whole number of output samples, no shift
    if p == 0 and den < n:
        p = den
    xx = np.concatenate([x[p:0:-1], x, x[-2:-p - 2:-1]]) if p > 0 else x
    N = -(-len(xx) // den) * den                               # length the exact ratio maps to an integer
    if N > len(xx):
        xx = np.concatenate([xx, np.zeros(N - len(xx))])
    M = N * num // den
    X = np.fft.rfft(xx)
    k = min(len(X), M // 2 + 1)
    Y = np.zeros(M // 2 + 1, complex)
    Y[:k] = X[:k]
    if M < N and M % 2 == 0:
        Y[-1] = Y[-1].real
    y = np.fft.irfft(Y, M) * (M / N)
    s = int(round(p * num / den))
    y = y[s:s + m]
    return np.pad(y, (0, m - len(y))) if len(y) < m else y


# ---------------------------------------------------------------- model
_OUT_NAMES = ("note", "onset", "contour")


def _io(model):
    inp = model.inputs[0][0]
    outs = model.outputs
    # basic-pitch's ONNX export: StatefulPartitionedCall:0 contour, :1 note, :2 onset; verified by shape below
    by_name = {}
    for o in outs:
        if o.endswith(":0"):
            by_name["contour"] = o
        elif o.endswith(":1"):
            by_name["note"] = o
        elif o.endswith(":2"):
            by_name["onset"] = o
    if len(by_name) != 3:
        by_name = dict(zip(_OUT_NAMES, outs))
    return inp, by_name


def run_model(y, model=None, progress=None, batch=8, backend=None):
    """y: float32 mono at 22.05 kHz -> dict note/onset (frames x 88), contour (frames x 264), plus info."""
    t0 = time.time()
    model = model or OL.load(model_path())
    inp, names = _io(model)
    n_orig = len(y)
    a = np.concatenate([np.zeros(OVERLAP // 2, np.float32), np.asarray(y, np.float32)])
    starts = list(range(0, len(a), WIN_HOP))
    W = np.zeros((len(starts), N_SAMPLES, 1), np.float32)
    for i, s in enumerate(starts):
        seg = a[s:s + N_SAMPLES]
        W[i, :len(seg), 0] = seg
    want = [names["note"], names["onset"], names["contour"]]
    res = {k: [] for k in _OUT_NAMES}
    i = 0
    bs = batch
    backend = backend or os.environ.get("FLH_ONNX_BACKEND", "auto")
    while i < len(W):
        chunk = W[i:i + bs]
        try:
            o = model.run({inp: chunk}, outputs=want, backend=backend)
        except Exception:
            if bs > 1:                                           # a session fixed to batch 1: go window by window
                bs = 1
                continue
            if backend != "numpy":
                backend = "numpy"
                continue
            raise
        for k, n in zip(_OUT_NAMES, want):
            res[k].append(np.asarray(o[n], np.float32).reshape(len(chunk), ANNOT_FRAMES, -1))
        i += len(chunk)
        if progress:
            progress(i / len(W))
    h = OVERLAP_FRAMES // 2
    n_frames = int(np.floor(n_orig * (FPS / SR)))
    out = {}
    for k in _OUT_NAMES:
        g = np.concatenate(res[k], axis=0)[:, h:-h, :]
        out[k] = g.reshape(-1, g.shape[-1])[:n_frames]
    _check_outputs(out)
    out["info"] = {"windows": len(W), "frames": n_frames, "backend": _backend_used(model, backend),
                   "model_s": round(time.time() - t0, 2)}
    return out


def _backend_used(model, backend):
    return "onnxruntime" if backend != "numpy" and model._ort not in (None, False) else "numpy"


def _check_outputs(out):
    if out["contour"].shape[1] != 264 or out["note"].shape[1] != N_KEYS or out["onset"].shape[1] != N_KEYS:
        raise OL.OnnxLiteError("this isn't the basic-pitch model (unexpected output sizes)")
    # onset activations are short spikes; note activations last. If the two look swapped, swap them.
    n, o = out["note"], out["onset"]
    if n.size and o.size and n.max() > 0.3 and o.max() > 0.3:
        dn, do = _persistence(n), _persistence(o)
        if do > dn * 1.5:
            out["note"], out["onset"] = o, n


def _persistence(g, thr=0.3):
    on = g > thr
    if not on.any():
        return 0.0
    starts = (on[1:] & ~on[:-1]).sum() + on[0].sum()
    return float(on.sum()) / max(1, int(starts))


def frame_times(n_frames):
    """basic-pitch's frame -> seconds (corrects the small drift of each 2-s window)."""
    t = np.arange(n_frames) * HOP / SR
    win = np.floor(np.arange(n_frames) / ANNOT_FRAMES)
    off = (HOP / SR) * (ANNOT_FRAMES - (N_SAMPLES / HOP)) + 0.0018
    return t - off * win


# ---------------------------------------------------------------- notes (basic-pitch's note creation)
def _argrelmax0(a):
    """Strict local maxima along axis 0 (first and last frames never count, like scipy's argrelmax)."""
    m = np.zeros(a.shape, bool)
    m[1:-1] = (a[1:-1] > a[:-2]) & (a[1:-1] > a[2:])
    return np.nonzero(m)


def _inferred_onsets(onsets, frames, n_diff=2):
    diffs = []
    for n in range(1, n_diff + 1):
        fa = np.concatenate([np.zeros((n, frames.shape[1]), frames.dtype), frames])
        diffs.append(fa[n:] - fa[:-n])
    fd = np.min(diffs, axis=0)
    fd[fd < 0] = 0
    fd[:n_diff] = 0
    mx = fd.max()
    if mx > 0:
        fd = onsets.max() * fd / mx
    return np.maximum(onsets, fd)


def notes_from_grids(frames, onsets, onset_thresh=ONSET_THRESH, frame_thresh=FRAME_THRESH, min_note_s=MIN_NOTE_S,
                     min_key=None, max_key=None, infer_onsets=True, melodia_trick=True, energy_tol=ENERGY_TOL):
    """-> list of (start_frame, end_frame, midi_key, amplitude 0..1), basic-pitch's algorithm."""
    frames = np.asarray(frames, np.float64).copy()
    onsets = np.asarray(onsets, np.float64).copy()
    n_frames = frames.shape[0]
    if n_frames < 3:
        return []
    min_len = int(round(min_note_s * FPS))
    if min_key is not None:
        lo = max(0, int(min_key) - MIDI_OFFSET)
        frames[:, :lo] = 0
        onsets[:, :lo] = 0
    if max_key is not None:
        hi = min(N_KEYS, int(max_key) - MIDI_OFFSET + 1)
        frames[:, hi:] = 0
        onsets[:, hi:] = 0
    if infer_onsets:
        onsets = _inferred_onsets(onsets, frames)
    pk = np.zeros(onsets.shape)
    idx = _argrelmax0(onsets)
    pk[idx] = onsets[idx]
    ot, of = np.nonzero(pk >= onset_thresh)
    order = np.lexsort((of, ot))[::-1]                          # latest onsets first, like the reference
    ot, of = ot[order], of[order]
    rem = frames.copy()
    top = N_KEYS - 1
    events = []
    for s, f in zip(ot, of):
        if s >= n_frames - 1:
            continue
        i, k = s + 1, 0
        while i < n_frames - 1 and k < energy_tol:
            k = k + 1 if rem[i, f] < frame_thresh else 0
            i += 1
        i -= k
        if i - s <= min_len:
            continue
        rem[s:i, f] = 0
        if f < top:
            rem[s:i, f + 1] = 0
        if f > 0:
            rem[s:i, f - 1] = 0
        events.append((int(s), int(i), int(f) + MIDI_OFFSET, float(frames[s:i, f].mean()), True))
    if melodia_trick:
        while rem.max() > frame_thresh:
            im, f = np.unravel_index(int(np.argmax(rem)), rem.shape)
            rem[im, f] = 0
            i, k = im + 1, 0
            while i < n_frames - 1 and k < energy_tol:
                k = k + 1 if rem[i, f] < frame_thresh else 0
                rem[i, f] = 0
                if f < top:
                    rem[i, f + 1] = 0
                if f > 0:
                    rem[i, f - 1] = 0
                i += 1
            ie = i - 1 - k
            i, k = im - 1, 0
            while i > 0 and k < energy_tol:
                k = k + 1 if rem[i, f] < frame_thresh else 0
                rem[i, f] = 0
                if f < top:
                    rem[i, f + 1] = 0
                if f > 0:
                    rem[i, f - 1] = 0
                i -= 1
            st = i + 1 + k
            if ie - st <= min_len:
                continue
            events.append((int(st), int(ie), int(f) + MIDI_OFFSET, float(frames[st:ie, f].mean()), False))
    return events


def transcribe(x, fs, progress=None, backend=None, **kw):
    """(mono audio, rate) -> {'notes': [{t, end, key, vel, amp, onset}], 'grids': ..., 'info': ...}."""
    t0 = time.time()
    y = resample(x, fs)
    t1 = time.time()
    g = run_model(y, progress=progress, backend=backend)
    ev = notes_from_grids(g["note"], g["onset"], **kw)
    tt = frame_times(len(g["note"]) + 1)
    on = g["onset"]
    notes = [{"t": float(tt[s]), "end": float(tt[min(e, len(tt) - 1)]), "key": k,
              "vel": int(max(1, min(127, round(127 * a)))), "amp": round(a, 3), "onset": o,
              "op": round(float(on[max(0, s - 2):s + 3, k - MIDI_OFFSET].max()), 3)}   # attack strength at the start
             for s, e, k, a, o in ev]
    notes.sort(key=lambda n: (n["t"], n["key"]))
    info = dict(g["info"])
    info["seconds"] = round(len(y) / SR, 3)
    info.update(resample_s=round(t1 - t0, 2), notes_s=round(time.time() - t1 - info["model_s"], 2))
    return {"notes": notes, "grids": g, "info": info, "audio": y, "sr": SR}
