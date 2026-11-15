"""fl_piano_tk: piano transcription with Transkun V2 (Yujia Yan, Frank Cwitkowitz, Zhiyao Duan; MIT), run by
onnx_lite (numpy, or onnxruntime when installed).

Transkun scores every possible [start, end] interval on 90 tracks (sustain pedal, soft pedal, the 88 keys) and a
semi-CRF Viterbi decode picks the best set of non-overlapping intervals per track; a second small net gives each
note its velocity and sub-frame onset / offset. Everything here follows the reference implementation
(transkun 2.0.1: Util.makeFrame, MelSpectrum, NeuralSemiCRFInterval.viterbiBackward, TransKun.transcribeFrames /
transcribe, Data.resolveOverlapping) and is checked against its fixtures (tests/test_piano_tk.py).

The model files are an optional add-on (~57 MB, not shipped in the extension): data/transcribe/transkun/ holds
transkun.onnx, transkun-heads.onnx, freq2mels.f32, windows.f32, symbols.i32, params.json, LICENSE.transkun.
Source of the ONNX export: huggingface.co/TuesdayCrowd/transkun-onnx (v2.0.0, MIT).
"""
import json
import math
import os
import time

import numpy as np

import onnx_lite as OL

FILES = ("transkun.onnx", "transkun-heads.onnx", "freq2mels.f32", "windows.f32", "symbols.i32", "params.json")
SUSTAIN, SOFT = -64, -67

# the one-click install: these exact files (size, SHA-256) from this exact published revision, nothing else
REPO = "TuesdayCrowd/transkun-onnx"
REVISION = "b44598166715a50d863973a8ecbbdbbb392f08d6"          # v2.0.0
MANIFEST = {
    "transkun.onnx": (53391226, "6d6960ecbce5c3d51051ebc1ebdeec4d3018ea6cfd360fb1ae96359aa7a075a3"),
    "transkun-heads.onnx": (3422740, "f609f53fbb4df0a2165f941aff1d5a1c1063ba40aaa699365448dca5d9f3544d"),
    "freq2mels.f32": (1876884, "449f2a5269cde41f6705cfde7157240ce7c8930f20b50909d57e633395bbf0f4"),
    "windows.f32": (98304, "b059ebf1e5550a8186b120ddf3f068dd9075e72338051bcd0349c1f436ddb14f"),
    "symbols.i32": (360, "cd2920adac71c7495a8fbf9586d04c2737a9440d280fdc4a619f636837b98fee"),
    "params.json": (237, "4d52da9e78782329be6119a68fe4f0a2f98561d2874782e2bb17426620aed065"),
    "LICENSE.transkun": (1066, "7662b6b4acfb7090612b946c1f427d8c7b591cda7d7a16139aed4eb66d20b066"),
}
SOURCE_NAME = {"LICENSE.transkun": "LICENSE"}                  # file name in the Hugging Face repo
TOTAL_MB = round(sum(v[0] for v in MANIFEST.values()) / 1e6)
ALLOWED_HOSTS = ("huggingface.co", "hf.co")                     # + their subdomains (the download CDN)


class ModelMissing(Exception):
    pass


def user_dir():
    """Where the one-click install puts the model: fl-headless's own folder, kept across extension updates."""
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "fl-headless", "models", "transkun")


def _candidates(data_dir=None):
    if data_dir:
        return [data_dir]
    env = os.environ.get("FLH_TRANSKUN_DIR")
    return ([env] if env else []) + [user_dir(), os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                                                              "transcribe", "transkun")]


def model_dir(data_dir=None):
    for d in _candidates(data_dir):
        if all(os.path.isfile(os.path.join(d, f)) for f in FILES):
            return d
    raise ModelMissing(f"the Transkun piano model isn't installed. It's a free optional add-on ({TOTAL_MB} MB, MIT): "
                       "audio_to_midi with install_model=true downloads and checks it (or put the files from "
                       f"huggingface.co/{REPO} in {user_dir()})")


def available(data_dir=None):
    try:
        model_dir(data_dir)
        return True
    except ModelMissing:
        return False


class Transkun:
    def __init__(self, data_dir=None):
        d = model_dir(data_dir)
        p = json.load(open(os.path.join(d, "params.json")))
        self.fs, self.win_size, self.hop = int(p["fs"]), int(p["windowSize"]), int(p["hopSize"])
        self.eps = float(p["eps"])
        self.seg_s, self.step_s = float(p["segmentSizeSeconds"]), float(p["segmentHopSeconds"])
        self.f2m = np.fromfile(os.path.join(d, "freq2mels.f32"), "<f4").reshape(self.win_size // 2 + 1, -1)
        self.wins = np.fromfile(os.path.join(d, "windows.f32"), "<f4").reshape(-1, self.win_size)
        self.symbols = np.fromfile(os.path.join(d, "symbols.i32"), "<i4").tolist()
        self.main = OL.load(os.path.join(d, "transkun.onnx"))
        self.heads = OL.load(os.path.join(d, "transkun-heads.onnx"))

    # ------------------------------------------------------------ front end (Util.makeFrame + MelSpectrum)
    def frames(self, x):
        n = int(math.ceil(len(x) / self.hop)) + 1
        lp = self.win_size // 2
        rp = (n - 1) * self.hop + self.win_size // 2 - len(x)
        xp = np.pad(np.asarray(x, np.float64), (lp, rp))
        return np.lib.stride_tricks.sliding_window_view(xp, self.win_size)[::self.hop][:n]

    def features(self, fr):
        fr = (fr - fr.mean()) / (fr.std(ddof=1) + 1e-8)            # gain normalisation per segment
        n = len(fr)
        out = np.empty((n, self.f2m.shape[1], len(self.wins)), np.float32)
        for a in range(0, n, 128):                                 # in blocks: rfft of 6 windows is large
            sp = np.abs(np.fft.rfft(fr[a:a + 128, None, :] * self.wins[None], norm="ortho")) ** 2
            mel = sp @ self.f2m
            mel = (np.log(mel + self.eps) - math.log(self.eps)) / (-math.log(self.eps))
            out[a:a + 128] = mel.transpose(0, 2, 1)
        return out

    # ------------------------------------------------------------ one segment (TransKun.transcribeFrames)
    def segment(self, fr, forced, last_frame, backend="auto"):
        feats = self.features(fr)
        o = self.main.run({self.main.inputs[0][0]: feats[None]}, backend=backend)
        S, ctx = o["S"], o["ctx"]                                  # [T, T, 90], [90, T, 256]
        T = S.shape[0]
        path = viterbi_backward(S, np.zeros((T - 1, S.shape[2]), np.float32), forced)
        flat = [(j, a, b) for j, iv in enumerate(path) for a, b in iv]
        if not flat:
            return [], [0] * len(self.symbols), S
        sym = np.array([j for j, _, _ in flat])
        ca = ctx[sym, np.array([a for _, a, _ in flat])]
        cb = ctx[sym, np.array([b for _, _, b in flat])]
        h = self.heads.run({"attr": np.concatenate([ca, cb, ca * cb], axis=-1).astype(np.float32)}, backend=backend)
        vel = np.argmax(h["velLogits"], axis=-1)
        ofv, ofp = h["ofRaw"][:, :2], h["ofRaw"][:, 2:] > 0
        ofv = np.clip((cb_mean(ofv) - 0.5) / 0.99, -0.5, 0.5)
        dur = self.hop / self.fs
        notes, lastP, n = [], [], 0
        for j, ev in enumerate(self.symbols):
            last_end, cur_last = 0.0, 0
            for a, b in path[j]:
                st = (a + ofv[n, 0]) * dur
                en = (b + ofv[n, 1]) * dur
                has_on = bool(a > 0 or ofp[n, 0])
                has_off = bool(b < last_frame or ofp[n, 1])
                st = max(st, last_end)
                en = max(en, st + 1e-8)
                last_end = en
                notes.append({"start": st, "end": en, "pitch": ev, "velocity": int(vel[n]), "hasOnset": has_on,
                              "hasOffset": has_off})
                if has_off:
                    cur_last = b
                n += 1
            lastP.append(cur_last)
        notes.sort(key=lambda e: (e["start"], e["end"], e["pitch"]))
        return notes, lastP, S

    # ------------------------------------------------------------ whole recording (TransKun.transcribe)
    def transcribe(self, x, fs, progress=None, backend=None):
        """mono audio -> {'notes': [{t, end, key, vel}], 'pedal': {'sustain': [(t0, t1)], 'soft': [...]}, info}"""
        t0 = time.time()
        backend = backend or os.environ.get("FLH_ONNX_BACKEND", "auto")
        import fl_piano_tx as PT
        y = PT.resample(x, fs, self.fs) if fs != self.fs else np.asarray(x, np.float32)
        dur_s = len(y) / self.fs
        t_rs = time.time() - t0
        pad_t = self.seg_s - self.step_s
        npad = math.ceil(pad_t * self.fs)
        y = np.pad(y.astype(np.float64), (npad, npad))
        nsamp = len(y)
        start_frame = math.floor(pad_t * self.fs / self.hop)
        forced = [start_frame] * len(self.symbols)
        step = math.ceil(self.step_s * self.fs / self.hop) * self.hop
        seg = math.ceil(self.seg_s * self.fs)
        last_frame = round(seg / self.hop)
        by = {}
        starts = list(range(0, nsamp, step))
        t_model = time.time()
        for si, i in enumerate(starts):
            j = min(i + seg, nsamp)
            begin = i / self.fs - pad_t
            sl = y[i:j]
            if len(sl) < seg:
                sl = np.pad(sl, (0, seg - len(sl)))
            evs, lastP, _ = self.segment(self.frames(sl), forced, last_frame, backend)
            forced = [max(k - int(step / self.hop), 0) for k in lastP]
            for e in evs:
                e["start"] = max(e["start"] + begin, 0.0)
                e["end"] = max(e["end"] + begin, e["start"])
            for e in evs:                                          # mergeIncompleteEvent
                lst = by.setdefault(e["pitch"], [])
                if lst:
                    le = lst[-1]
                    if e["start"] < le["end"]:
                        if e["hasOnset"]:
                            lst[-1] = e
                        else:
                            le["hasOffset"] = e["hasOffset"]
                            le["end"] = max(e["end"], le["end"])
                        continue
                if e["hasOnset"]:
                    lst.append(e)
            if progress:
                progress((si + 1) / len(starts))
        for lst in by.values():
            if lst:
                lst[-1]["hasOffset"] = True
        evs = resolve_overlapping([e for lst in by.values() for e in lst if e["hasOffset"]])
        notes, ped = [], {"sustain": [], "soft": []}
        for e in evs:
            if e["pitch"] == SUSTAIN:
                ped["sustain"].append((round(e["start"], 4), round(e["end"], 4)))
            elif e["pitch"] == SOFT:
                ped["soft"].append((round(e["start"], 4), round(e["end"], 4)))
            else:
                v = int(e["velocity"])
                notes.append({"t": float(e["start"]), "end": float(e["end"]), "key": int(e["pitch"]),
                              "vel": max(1, v), "amp": round(v / 127.0, 3), "onset": True, "op": 1.0,
                              "src": "transkun"})
        info = {"model": "transkun-2.0 (Yan et al., MIT)",
                "backend": "onnxruntime" if backend != "numpy" and self.main._ort not in (None, False) else "numpy",
                "segments": len(starts), "seconds": round(dur_s, 3), "resample_s": round(t_rs, 2),
                "model_s": round(time.time() - t_model, 2)}
        return {"notes": notes, "pedal": ped, "info": info}


def window_notes(tk, y44, t0, t1, backend=None):
    """Re-listen to one stretch: a single 16-s window centred on [t0, t1] of the recording itself (no silence
    padding, so quiet passages get their own loudness normalisation). -> notes starting in [t0, t1] (seconds)."""
    seg_n = int(round(tk.seg_s * tk.fs))
    mid = 0.5 * (t0 + t1)
    a = int(max(0, min(len(y44) - seg_n, (mid - tk.seg_s / 2) * tk.fs)))
    sl = np.asarray(y44[a:a + seg_n], np.float64)
    if len(sl) < seg_n:
        sl = np.pad(sl, (0, seg_n - len(sl)))
    evs, _, _ = tk.segment(tk.frames(sl), [0] * len(tk.symbols), round(seg_n / tk.hop),
                           backend or os.environ.get("FLH_ONNX_BACKEND", "auto"))
    off = a / tk.fs
    out = []
    for e in evs:
        if e["pitch"] < 0:
            continue
        st, en = e["start"] + off, e["end"] + off
        if t0 <= st < t1 and e["hasOnset"]:
            v = max(1, int(e["velocity"]))
            out.append({"t": st, "end": max(en, st + 0.03), "key": int(e["pitch"]), "vel": v, "amp": round(v / 127, 3),
                        "onset": True, "op": 1.0, "src": "transkun"})
    return out


def cb_mean(logits):
    """Mean of a continuous Bernoulli with these logits: 1/(1-exp(-l)) - 1/l (-> 0.5 + l/12 near 0)."""
    l = np.asarray(logits, np.float64)
    small = np.abs(l) < 1e-3
    ls = np.where(small, 1.0, l)
    m = 1.0 / (-np.expm1(-ls)) - 1.0 / ls
    return np.where(small, 0.5 + l / 12.0, m)


def viterbi_backward(score, noise, forced=None):
    """transkun.CRF.NeuralSemiCRFInterval.viterbiBackward. score [T, T, K] (score[end, begin, k]), noise [T-1, K].
    -> per track a list of (begin, end) frame intervals."""
    T, _, K = score.shape
    q = np.zeros((T, K), np.float64)
    sT = np.ascontiguousarray(score.transpose(1, 0, 2))
    diag = np.ascontiguousarray(np.diagonal(score, axis1=0, axis2=1).T)       # [T, K]
    q[T - 1] = diag[T - 1] * (diag[T - 1] > 0)
    ptr = np.empty((T - 1, K), np.int64)
    for i in range(1, T):
        b = T - i - 1
        sub = sT[b, T - i:, :]                                                  # intervals starting at b
        cand = np.concatenate([q[T - i:T - i + 1] + noise[b], q[T - i:] + sub], axis=0)
        sel = np.argmax(cand, axis=0)
        q[b] = cand[sel, np.arange(K)] + diag[b] * (diag[b] > 0)
        ptr[i - 1] = sel - 1
    forced = forced if forced is not None else [0] * K
    res = []
    for k in range(K):
        j = int(forced[k])
        cur = []
        while j < T - 1:
            s = int(ptr[T - j - 2, k])
            if diag[j, k] > 0:
                cur.append((j, j))
            if s < 0:
                j += 1
            else:
                e = s + j + 1
                cur.append((j, e))
                j = e
        if score[T - 1, T - 1, k] > 0:
            cur.append((T - 1, T - 1))
        res.append(cur)
    return res


def resolve_overlapping(evs):
    """Data.resolveOverlapping: a key struck again cuts the previous note of that key; empty notes dropped."""
    evs = sorted(evs, key=lambda e: (e["start"], e["end"], e["pitch"]))
    last = {}
    out = []
    for e in evs:
        if e["pitch"] in last:
            p = out[last[e["pitch"]]]
            if p["end"] > e["start"]:
                p["end"] = e["start"]
        last[e["pitch"]] = len(out)
        out.append(e)
    out = [e for e in out if e["start"] < e["end"]]
    out.sort(key=lambda e: (e["start"], e["end"], e["pitch"]))
    return out


# ---------------------------------------------------------------- one-click install (only after the user said yes)
def _host_ok(url):
    import urllib.parse
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    h = (u.hostname or "").lower().rstrip(".")
    return (u.scheme == "https" and not u.username and not u.password and u.port in (None, 443)
            and any(h == a or h.endswith("." + a) for a in ALLOWED_HOSTS))


def _opener():
    import urllib.error
    import urllib.request

    class Safe(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            import urllib.parse
            newurl = urllib.parse.urljoin(req.full_url, newurl)
            if not _host_ok(newurl):
                raise urllib.error.URLError(f"refused a redirect off Hugging Face: {newurl[:100]}")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    return urllib.request.build_opener(Safe)


def file_url(name):
    return f"https://huggingface.co/{REPO}/resolve/{REVISION}/{SOURCE_NAME.get(name, name)}"


def install(dest=None, progress=None, opener=None, timeout=60):
    """Download the 7 model files, check each one's size and SHA-256, then move them into place. Nothing is kept
    from a failed or tampered download. -> {'dir', 'files', 'mb', 'seconds'}."""
    import hashlib
    dest = dest or user_dir()
    os.makedirs(dest, exist_ok=True)
    op = opener or _opener()
    t0 = time.time()
    done = 0
    total = sum(v[0] for v in MANIFEST.values())
    for name, (size, sha) in MANIFEST.items():
        final = os.path.join(dest, name)
        if os.path.isfile(final) and os.path.getsize(final) == size and _sha256(final) == sha:
            done += size
            continue                                             # already here and intact
        url = file_url(name)
        if not _host_ok(url):
            raise ValueError("refused a non-Hugging Face URL")
        part = final + ".part"
        h = hashlib.sha256()
        got = 0
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "fl-headless (piano model install)"})
            with op.open(req, timeout=timeout) as r:
                if not _host_ok(r.geturl() or url):
                    raise ValueError("refused a download from off Hugging Face")
                with open(part, "wb") as fh:
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        got += len(b)
                        if got > size:
                            raise ValueError(f"{name}: bigger than expected - refused")
                        h.update(b)
                        fh.write(b)
                        if progress:
                            progress((done + got) / total)
            if got != size or h.hexdigest() != sha:
                raise ValueError(f"{name} didn't match its fingerprint (size {got} of {size}) - refused, nothing "
                                 "installed. Try again later")
            os.replace(part, final)
            done += size
        except Exception:
            try:
                os.remove(part)
            except OSError:
                pass
            raise
    return {"dir": dest, "files": len(MANIFEST), "mb": round(total / 1e6, 1), "seconds": round(time.time() - t0, 1)}


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _choice_path():
    return os.path.join(os.path.dirname(user_dir()), "piano_model_choice.json")


def declined():
    try:
        return bool(json.load(open(_choice_path())).get("declined"))
    except Exception:
        return False


def remember_choice(decline):
    p = _choice_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        json.dump({"declined": bool(decline), "when": time.strftime("%Y-%m-%d")}, fh)
