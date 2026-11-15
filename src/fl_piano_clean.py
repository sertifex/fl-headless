"""fl_piano_clean: turn raw piano notes (seconds, from fl_piano_tx) into a playable score.

Steps, each measured and reported:
  1. merge  - a held note the pedal smeared into pieces (same key, tiny gap, no fresh attack) becomes one note
  2. ghosts - overtone ghosts (an octave / 12th / 2 octaves above a stronger note, struck with it, weak attack)
              and tiny blips are dropped; real octave doublings with their own attack stay
  3. key    - key from the duration-weighted pitch classes. Used ONLY to settle a semitone tie (two neighbour keys
              struck together with near-equal evidence); a note is never moved into the key
  4. tempo  - a beat tracker that follows the playing (rubato): the beat interval may drift beat to beat, gently
              pulled toward the song's main tempo. Output is a tempo MAP (one bpm per beat), not one number
  5. meter  - time signature from where bass notes, harmony changes and accents recur (2, 3 or 4 beats; compound
              6/8, 9/8, 12/8 when beats split in three) + the pickup
  6. grid   - notes snap to the beat subdivision that fits the playing (1/2, 1/3, 1/4 or 1/6 of a beat), in beat
              time, so the tempo map carries the expression and the notes sit on the right beats
  7. hands  - left / right split per chord by register and voice-leading (each hand's centre moves smoothly, a
              hand spans at most ~a 10th)

numpy only. Inputs: notes [{t, end, key, vel, amp, onset, op}] and optionally the onset grid for beat tracking.
"""
import math

import numpy as np

NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
FPS = 22050 / 256


# ---------------------------------------------------------------- 1. merge pedal-smeared pieces
def merge_pieces(notes, max_gap=0.09, restrike_op=0.4):
    """Same key, gap <= max_gap s, and the later piece has no attack of its own (onset prob < restrike_op and
    not much louder) -> one note. Returns (notes, merged count)."""
    by = {}
    for n in sorted(notes, key=lambda n: n["t"]):
        by.setdefault(n["key"], []).append(dict(n))
    out, merged = [], 0
    for k, lst in by.items():
        cur = lst[0]
        for n in lst[1:]:
            gap = n["t"] - cur["end"]
            fresh = n.get("op", 1.0 if n.get("onset") else 0.0) >= restrike_op or n["amp"] > cur["amp"] * 1.25
            if gap <= max_gap and not fresh:
                w1, w2 = cur["end"] - cur["t"], n["end"] - n["t"]
                cur["amp"] = (cur["amp"] * w1 + n["amp"] * w2) / max(w1 + w2, 1e-9)
                cur["end"] = max(cur["end"], n["end"])
                merged += 1
            else:
                out.append(cur)
                cur = n
        out.append(cur)
    out.sort(key=lambda n: (n["t"], n["key"]))
    return out, merged


# ---------------------------------------------------------------- 2. overtone ghosts and blips
GHOST_INTERVALS = (12, 19, 24, 28)


def drop_ghosts(notes, together=0.06, amp_ratio=0.55, weak_op=0.5, blip_s=0.09, blip_amp=0.35):
    """-> (kept, {'overtone': n, 'blip': n}). Conservative: a real octave doubling has its own attack and level."""
    keep = [True] * len(notes)
    starts = np.array([n["t"] for n in notes])
    cnt = {"overtone": 0, "blip": 0, "low": 0}
    for i, h in enumerate(notes):
        dur = h["end"] - h["t"]
        if h.get("src") == "transkun":                          # no attack probability: judge by velocity
            if dur < 0.06 and h["vel"] < 30:
                keep[i] = False
                cnt["blip"] += 1
                continue
            lo, hi = np.searchsorted(starts, h["t"] - 0.04), np.searchsorted(starts, h["t"] + 0.04, "right")
            for j in range(lo, hi):
                f = notes[j]
                if j != i and (h["key"] - f["key"]) in GHOST_INTERVALS and h["vel"] < 0.6 * f["vel"] \
                        and h["end"] <= f["end"] + 0.15:
                    keep[i] = False
                    cnt["overtone"] += 1
                    break
            continue
        hop = h.get("op", 1.0 if h.get("onset") else 0.0)
        if dur < blip_s and h["amp"] < blip_amp and hop < weak_op:
            keep[i] = False
            cnt["blip"] += 1
            continue
        if hop >= weak_op:
            continue
        if h["key"] < 48 and not h.get("onset", True) and hop < 0.3 and h["amp"] < 0.45 and dur < 0.6:
            # a weak low note with no attack under a stronger note an octave / 12th / 2 octaves up (sub-harmonic)
            if any(f is not h and (f["key"] - h["key"]) in GHOST_INTERVALS and f["t"] <= h["t"] + 0.05
                   and f["end"] >= h["end"] - 0.05 and f["amp"] > h["amp"] for f in notes):
                keep[i] = False
                cnt["low"] = cnt.get("low", 0) + 1
                continue
        lo, hi = np.searchsorted(starts, h["t"] - together), np.searchsorted(starts, h["t"] + together, "right")
        for j in range(lo, hi):
            f = notes[j]
            if j == i or (h["key"] - f["key"]) not in GHOST_INTERVALS:
                continue
            if h["amp"] < amp_ratio * f["amp"] and h["end"] <= f["end"] + 0.15:
                keep[i] = False
                cnt["overtone"] += 1
                break
    return [n for n, k in zip(notes, keep) if k], cnt


# ---------------------------------------------------------------- 2b. note ends from how long they ring
def key_bands(y, sr=22050, hop=256, n_fft=4096, keys=range(21, 109), block=2048):
    """dB level per piano key per frame (~86 fps): energy at the key's first 3 harmonics (+-1/4 tone)."""
    keys = list(keys)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    W = np.zeros((len(keys), len(freqs)), np.float32)
    for i, k in enumerate(keys):
        f0 = 440.0 * 2 ** ((k - 69) / 12)
        for h, w in ((1, 1.0), (2, 0.6), (3, 0.35)):
            lo, hi = f0 * h * 2 ** (-1 / 24), f0 * h * 2 ** (1 / 24)
            sel = (freqs >= lo) & (freqs <= hi)
            if not sel.any() and f0 * h < sr / 2:
                sel = np.abs(freqs - f0 * h) == np.abs(freqs - f0 * h).min()
            W[i, sel] = w
    y = np.asarray(y, np.float32)
    pad = np.concatenate([np.zeros(n_fft // 2, np.float32), y, np.zeros(n_fft // 2, np.float32)])
    nfr = 1 + (len(pad) - n_fft) // hop
    win = np.hanning(n_fft).astype(np.float32)
    out = np.empty((len(keys), nfr), np.float32)
    for a in range(0, nfr, block):
        b = min(nfr, a + block)
        idx = (np.arange(a, b) * hop)[:, None] + np.arange(n_fft)[None, :]
        S = np.abs(np.fft.rfft(pad[idx] * win, axis=1)).astype(np.float32)
        out[:, a:b] = (W @ S.T)
    return 20 * np.log10(out + 1e-6), keys


def ring_ends(notes, y, sr=22050, drop_db=30.0, masked_drop_db=18.0, floor_db=6.0, max_s=8.0, bands=None):
    """Extend each note's end to where the key stops ringing in the audio (never shortens). Stops at the next
    strike of the same key, when the level falls drop_db below the attack, or near that key's noise floor. Where a
    lower note could feed this key through its overtones, the stricter masked_drop_db applies. -> count extended."""
    if y is None or not notes:
        return 0
    L, keys = bands if bands is not None else key_bands(y, sr)
    fps = sr / 256
    k0 = keys[0]
    ker = np.ones(5) / 5
    floor = np.percentile(L, 10, axis=1)
    by_key = {}
    for n in notes:
        by_key.setdefault(n["key"], []).append(n)
    for lst in by_key.values():
        lst.sort(key=lambda n: n["t"])
    ext = 0
    for k, lst in by_key.items():
        if not 0 <= k - k0 < len(L):
            continue
        tr = np.convolve(L[k - k0], ker, mode="same")
        for i, n in enumerate(lst):
            a = int(n["t"] * fps)
            e = int(n["end"] * fps)
            nxt = int(lst[i + 1]["t"] * fps) - 1 if i + 1 < len(lst) else len(tr) - 1
            cap = min(nxt, a + int(max_s * fps), len(tr) - 1)
            if e >= cap:
                continue
            peak = float(tr[a:a + int(0.15 * fps) + 1].max()) if a < len(tr) else -120.0
            masked = any(m["key"] in (k - 12, k - 19, k - 24) and m["t"] <= n["end"] and m["end"] > n["end"]
                         for m in notes)
            thr = max(peak - (masked_drop_db if masked else drop_db), floor[k - k0] + floor_db)
            j = e
            while j < cap and tr[j] > thr:
                j += 1
            if j > e + 2:
                n["end"] = j / fps
                ext += 1
    return ext


# ---------------------------------------------------------------- 2c. pedal, bass line, re-strikes
def _pedal_release(t, spans):
    """If the sustain pedal is down at time t -> the time it comes up, else None."""
    for a, b in spans:
        if a - 1e-3 <= t < b:
            return b
    return None


def apply_pedal(notes, spans, max_s=12.0):
    """A key let go while the sustain pedal is down keeps sounding until the pedal comes up: the note is written
    that long (so it sounds right without the pedal track, e.g. in an FL pattern). -> count extended."""
    n_ext = 0
    for n in notes:
        r = _pedal_release(n["end"], spans or [])
        if r is not None:
            e = min(r, n["t"] + max_s)
            if e > n["end"] + 0.02:
                n["end"] = e
                n_ext += 1
    return n_ext


def mark_bass(notes, together=0.05, low=52, gap=7, top=60):
    """The bass line: in each chord (notes struck together), its lowest note when that note is low (<= E3) or
    stands clearly below the rest (a 5th or more under the next note, up to C4). Sets n['bass']. -> count."""
    order = sorted(range(len(notes)), key=lambda i: notes[i]["t"])
    groups, cur = [], []
    for i in order:
        if cur and notes[i]["t"] - notes[cur[0]]["t"] > together:
            groups.append(cur)
            cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    nb = 0
    for n in notes:
        n["bass"] = False
    starts = np.array([n["t"] for n in notes])
    last = None
    for g in groups:
        ks = sorted(g, key=lambda i: notes[i]["key"])
        k0 = notes[ks[0]]["key"]
        k1 = notes[ks[1]]["key"] if len(ks) > 1 else None
        t0 = notes[ks[0]]["t"]
        # a key still held down (its own key release, before the pedal) below this one: not a new bass
        held_below = any(n["key"] < k0 and n["t"] < t0 - together and n["end"] > t0 + 0.03
                         for n in notes[:np.searchsorted(starts, t0)])
        if held_below:
            continue
        # the "pah" of oom-pah-pah: soon after a bass, a note more than a 5th above it is accompaniment (or an
        # octave above within 2.5 s), not a new bass
        if last and ((t0 - last[1] < 1.6 and k0 > last[0] + 7) or (t0 - last[1] < 2.5 and k0 >= last[0] + 12)):
            continue
        if k0 <= low or (k1 is not None and k1 - k0 >= gap and k0 <= top):
            notes[ks[0]]["bass"] = True
            last = (k0, t0)
            nb += 1
    return nb


def cut_bass(notes, min_gap=0.05):
    """A bass note stops when the next bass note is struck (a new bass = a new chord), even with the pedal down:
    low strings don't ring through the chord change in the written part. -> count shortened."""
    bs = sorted((n for n in notes if n.get("bass")), key=lambda n: n["t"])
    cut = 0
    for a, b in zip(bs, bs[1:]):
        if b["t"] - a["t"] > min_gap and a["end"] > b["t"]:
            a["end"] = max(a["t"] + 0.02, b["t"] - 0.005)
            cut += 1
    return cut


def cut_restrikes(notes):
    """The same key struck again while it still sounds (either hand): the first note ends there. -> count."""
    by = {}
    for n in notes:
        by.setdefault(n["key"], []).append(n)
    cut = 0
    for lst in by.values():
        lst.sort(key=lambda n: n["t"])
        for a, b in zip(lst, lst[1:]):
            if a["end"] > b["t"]:
                a["end"] = max(a["t"] + 0.01, b["t"] - 0.002)
                cut += 1
    return cut


# ---------------------------------------------------------------- 3. key (only settles ties)
def estimate_key(notes):
    pc = np.zeros(12)
    for n in notes:
        pc[n["key"] % 12] += (n["end"] - n["t"]) * max(n["amp"], 0.05)
    if pc.sum() <= 0 or pc.std() < 1e-12:
        return None
    res = []
    for r in range(12):
        for nm, prof in (("major", KK_MAJOR), ("minor", KK_MINOR)):
            res.append((float(np.corrcoef(pc, np.roll(prof, r))[0, 1]), r, nm))
    res.sort(reverse=True)
    c, r, nm = res[0]
    scale = [(r + s) % 12 for s in ((0, 2, 4, 5, 7, 9, 11) if nm == "major" else (0, 2, 3, 5, 7, 8, 10, 11))]
    rel = next(((c2, r2, n2) for c2, r2, n2 in res[1:] if not (r2 == (r + 9) % 12 and n2 == "minor" and nm == "major")
                and not (r2 == (r + 3) % 12 and n2 == "major" and nm == "minor")), res[1])
    return {"name": f"{NAMES[r]} {nm}", "tonic": r, "mode": nm, "scale": sorted(set(scale)),
            "correlation": round(c, 3), "margin": round(c - rel[0], 3), "runner_up": f"{NAMES[rel[1]]} {rel[2]}"}


def settle_semitones(notes, key, together=0.05, tie=1.5):
    """Two keys a semitone apart, struck together, overlapping most of their length: the model is unsure which.
    Clear evidence (amplitude ratio beyond `tie`) -> keep the stronger. A tie -> keep the one in the key.
    Both or neither in key and tied -> keep both (honest: reported as unclear). Returns (notes, report)."""
    if not notes:
        return notes, {"settled_by_evidence": 0, "settled_by_key": 0, "unclear": 0}
    starts = np.array([n["t"] for n in notes])
    drop = set()
    rep = {"settled_by_evidence": 0, "settled_by_key": 0, "unclear": 0}
    sc = set(key["scale"]) if key else set()
    for i, a in enumerate(notes):
        if i in drop:
            continue
        hi = np.searchsorted(starts, a["t"] + together, "right")
        for j in range(i + 1, hi):
            b = notes[j]
            if j in drop or abs(a["key"] - b["key"]) != 1:
                continue
            ov = min(a["end"], b["end"]) - max(a["t"], b["t"])
            if ov < 0.6 * min(a["end"] - a["t"], b["end"] - b["t"]):
                continue
            r = (a["amp"] + 1e-6) / (b["amp"] + 1e-6)
            if r > tie or r < 1 / tie:
                drop.add(j if r > 1 else i)
                rep["settled_by_evidence"] += 1
            elif sc and ((a["key"] % 12 in sc) != (b["key"] % 12 in sc)):
                drop.add(j if a["key"] % 12 in sc else i)
                rep["settled_by_key"] += 1
            else:
                rep["unclear"] += 1
            if i in drop:
                break
    return [n for k, n in enumerate(notes) if k not in drop], rep


# ---------------------------------------------------------------- 4. tempo map (rubato-following beats)
def onset_envelope(notes, n_frames, onset_grid=None, fps=FPS):
    """Beat evidence per frame from the struck notes: velocity x attack strength, bass weighted. (The model's raw
    onset grid is noisier than the notes built from it, so it isn't used here.)"""
    env = np.zeros(n_frames)
    for n in notes:
        f = int(round(n["t"] * fps))
        if 0 <= f < n_frames:
            env[f] += (n["vel"] / 127.0) * (0.3 + n.get("op", 0.5)) * (1.6 if n["key"] < 55 else 1.0)
    k = np.exp(-0.5 * (np.arange(-3, 4) / 1.2) ** 2)
    return np.convolve(env, k / k.sum(), mode="same")


def local_tempo(env, fps, tau0, win_s=10.0, hop_s=2.0, dev=0.3):
    """Beat period per frame that follows slow tempo changes: autocorrelation in 10-s windows (peak within +-30 %
    of the main period, plus its double-period support), median-smoothed over ~10 s."""
    n = len(env)
    W, Hh = int(win_s * fps), max(1, int(hop_s * fps))
    lo, hi = max(2, int(tau0 * (1 - dev))), int(tau0 * (1 + dev)) + 1
    L = np.arange(lo, hi)
    cs, ts = [], []
    for a in range(0, max(1, n - W // 2), Hh):
        seg = env[max(0, a - W // 2):a + W // 2]
        seg = seg - seg.mean()
        ts.append(a)
        if len(seg) < 2 * hi + 2 or seg.std() < 1e-9:
            cs.append(np.nan)
            continue
        N = 1 << int(np.ceil(np.log2(2 * len(seg))))
        ac = np.fft.irfft(np.abs(np.fft.rfft(seg, N)) ** 2, N)[:len(seg)]
        ac /= max(ac[0], 1e-12)
        sc = (ac[L] + 0.5 * ac[np.minimum(2 * L, len(ac) - 1)]) * np.exp(-0.5 * (np.log(L / tau0) / 0.15) ** 2)
        cs.append(float(L[np.argmax(sc)]) if sc.max() > 0.05 else np.nan)
    cs = np.array(cs, float)
    ok = ~np.isnan(cs)
    if not ok.any():
        return np.full(n, float(tau0))
    cs = np.interp(np.arange(len(cs)), np.nonzero(ok)[0], cs[ok])
    k = 5
    pad = np.pad(cs, k // 2, mode="edge")
    cs = np.array([np.median(pad[i:i + k]) for i in range(len(cs))])
    return np.interp(np.arange(n), np.array(ts, float), cs)


def track_beats_rubato(env, fps, bpm0, tight=30.0):
    """Beats that follow the playing: Ellis' dynamic-programming tracker, but the expected beat period comes from
    the local tempo curve at each moment (rubato, ritardando), so beats bend with the music without jitter.
    -> (beat frame indexes, tempo-curve bpm range)."""
    n = len(env)
    tau = local_tempo(env, fps, fps * 60.0 / bpm0)
    o = env / max(env.std(), 1e-9)
    o = o - 0.5 * o.mean()                                       # silence between beats costs a little
    sc = np.zeros(n)
    bk = -np.ones(n, dtype=np.int64)
    for t in range(n):
        lo, hi = int(tau[t] * 0.6), int(tau[t] * 1.6) + 1
        p = t - np.arange(max(1, lo), hi)
        p = p[p >= 0]
        if len(p):
            cand = sc[p] - tight * np.log((t - p) / tau[t]) ** 2
            j = int(np.argmax(cand))
            if cand[j] > 0:
                sc[t] = o[t] + cand[j]
                bk[t] = p[j]
                continue
        sc[t] = o[t]
    tail = np.arange(max(0, n - int(tau[-1] * 1.6) - 1), n)
    t = int(tail[np.argmax(sc[tail])])
    beats = [t]
    while bk[t] >= 0:
        t = int(bk[t])
        beats.append(t)
    rng = (round(60 * fps / float(tau.max()), 1), round(60 * fps / float(tau.min()), 1))
    return np.array(beats[::-1]), rng


def extend_beats(bt, t_end, t_start=0.0):
    """Beat times extended to cover t_start..t_end with the edge intervals (pickups, last notes)."""
    bt = list(bt)
    if len(bt) < 2:
        return np.array(bt)
    while bt[0] > t_start:
        bt.insert(0, bt[0] - (bt[1] - bt[0]))
    while bt[-1] < t_end:
        bt.append(bt[-1] + (bt[-1] - bt[-2]))
    return np.array(bt)


def time_to_beat(t, bt):
    """Seconds -> beat position (piecewise linear between beats)."""
    t = np.asarray(t, float)
    i = np.clip(np.searchsorted(bt, t, "right") - 1, 0, len(bt) - 2)
    return i + (t - bt[i]) / (bt[i + 1] - bt[i])


def tempo_map(bt):
    """bpm per beat (median of 3 so one late chord doesn't spike it)."""
    iv = np.diff(bt)
    bpm = 60.0 / np.maximum(iv, 1e-3)
    if len(bpm) >= 3:
        sm = bpm.copy()
        sm[1:-1] = np.median(np.stack([bpm[:-2], bpm[1:-1], bpm[2:]]), axis=0)
        bpm = sm
    return bpm


# ---------------------------------------------------------------- 6. grid
SUBDIVS = (2, 3, 4, 6)


def pick_subdivision(pos, tol=0.05):
    """The subdivision whose grid the note starts hit most beyond chance (chance-corrected, so a finer grid isn't
    picked just for being fine). -> (s, {s: hit rate}, {s: corrected score})."""
    pos = np.asarray(pos, float)
    if not len(pos):
        return 2, {}, {}
    hit, kap = {}, {}
    for s in SUBDIVS:
        err = np.abs(pos * s - np.round(pos * s)) / s
        h = float((err < tol).mean())
        ch = min(0.95, 2 * tol * s)
        hit[s], kap[s] = h, (h - ch) / (1 - ch)
    best = max(SUBDIVS, key=lambda s: (round(kap[s], 3), -s))
    if best == 6 and kap[3] >= kap[6] - 0.03:
        best = 3
    if best == 4 and kap[2] >= kap[4] - 0.03:
        best = 2
    return best, hit, kap


# ---------------------------------------------------------------- 5. meter
def detect_meter(notes_b, n_beats, subdiv):
    """notes_b: [(start_beat, len_beats, key, vel)]. -> dict(beats_per_bar m, phase, sig, compound, confidence)."""
    if n_beats < 8:
        return {"m": 4, "phase": 0, "sig": "4/4", "num": 4, "den": 4, "compound": False, "confidence": 0.0,
                "scores": {}}
    F, Fc = beat_features(notes_b, n_beats, parts=True)
    return _meter_from_features(F, n_beats, subdiv, Fc)


def beat_features(notes_b, n_beats, parts=False):
    """Downbeat evidence per beat: bass entries, harmony changes, accents (z-scored, weighted)."""
    bass = np.zeros(n_beats)
    acc = np.zeros(n_beats)
    chroma = np.zeros((n_beats, 12))
    for b, ln, k, v in notes_b:
        i = int(round(b))
        if 0 <= i < n_beats and abs(b - i) < 0.2:
            acc[i] += v / 127.0
            if k < 55:
                bass[i] += v / 127.0 * (1.5 if k < 48 else 1.0)
        a, e = int(max(0, math.floor(b))), int(min(n_beats, math.ceil(b + ln)))
        for j in range(a, e):
            chroma[j, k % 12] += min(b + ln, j + 1) - max(b, j)
    nrm = np.linalg.norm(chroma, axis=1) + 1e-9
    cc = chroma / nrm[:, None]
    change = np.zeros(n_beats)
    change[1:] = 1 - (cc[1:] * cc[:-1]).sum(1)

    def z(x):
        return (x - x.mean()) / (x.std() + 1e-9)
    F = 0.45 * z(bass) + 0.35 * z(change) + 0.2 * z(acc)
    return (F, z(change)) if parts else F


def _phase_score(F, m):
    best = (-9.0, 0)
    n = len(F)
    for ph in range(m):
        on = np.zeros(n, bool)
        on[ph::m] = True
        best = max(best, (float(F[on].mean() - F[~on].mean()), ph))
    return best


def _meter_from_features(F, n_beats, subdiv, Fc=None):
    scores = {}
    for m in (2, 3, 4):
        best = (-9.0, 0)
        for ph in range(m):
            on = np.zeros(n_beats, bool)
            on[ph::m] = True
            s = float(F[on].mean() - F[~on].mean())
            best = max(best, (s, ph))
        scores[m] = best
    m = max((3, 4), key=lambda q: scores[q][0])
    if scores[2][0] > 1.25 * scores[4][0] and scores[2][0] > scores[3][0]:
        # 2 only when the harmony also turns every 2 beats (a bass that alternates inside a 4-beat bar isn't 2/4)
        if Fc is None or _phase_score(Fc, 2)[0] >= 0.9 * _phase_score(Fc, 4)[0]:
            m = 2
    s_sorted = sorted((v[0] for v in scores.values()), reverse=True)
    conf = float(np.clip((scores[m][0] - (s_sorted[1] if s_sorted[0] == scores[m][0] else s_sorted[0])) /
                         max(abs(scores[m][0]), 1e-6), 0, 1)) if len(s_sorted) > 1 else 0.0
    compound = subdiv in (3, 6)
    num, den = {(2, False): (2, 4), (3, False): (3, 4), (4, False): (4, 4),
                (2, True): (6, 8), (3, True): (9, 8), (4, True): (12, 8)}[(m, compound)]
    return {"m": m, "phase": scores[m][1], "sig": f"{num}/{den}", "num": num, "den": den, "compound": compound,
            "confidence": round(conf, 2), "scores": {f"{k}": round(v[0], 3) for k, v in scores.items()}}


def downbeat_align(F, m, jump=1.6):
    """Viterbi over bar positions: each beat normally advances one position; a beat the tracker missed (+2) or
    added (+0) costs `jump`. Rewards downbeats where F is high. -> (positions per beat, [(beat, 'insert'|'drop')])."""
    n = len(F)
    if n < 2 or m < 2:
        return list(range(n)), []
    NEG = -1e18
    sc = np.full((n, m), NEG)
    bk = np.zeros((n, m), dtype=np.int64)
    sc[0] = [F[0] if p == 0 else 0.0 for p in range(m)]
    for i in range(1, n):
        for p in range(m):
            best, arg = NEG, 0
            for q in range(m):
                d = (p - q) % m
                c = sc[i - 1, q] + (0.0 if d == 1 else (-jump if d in (0, 2) else NEG))
                if c > best:
                    best, arg = c, q
            sc[i, p] = best + (F[i] if p == 0 else 0.0)
            bk[i, p] = arg
    p = int(np.argmax(sc[-1]))
    pos = [p]
    for i in range(n - 1, 0, -1):
        p = int(bk[i, p])
        pos.append(p)
    pos = pos[::-1]
    ops = []
    for i in range(1, n):
        d = (pos[i] - pos[i - 1]) % m
        if d == 2:
            ops.append((i, "insert"))
        elif d == 0:
            ops.append((i, "drop"))
    return pos, ops


def apply_beat_ops(bt, ops):
    """insert: a beat midway before beat i; drop: beat i is removed (its neighbours' interval absorbs it)."""
    ins = {i for i, o in ops if o == "insert"}
    drp = {i for i, o in ops if o == "drop"}
    out = []
    for i, t in enumerate(bt):
        if i in ins and i > 0:
            out.append((bt[i - 1] + t) / 2)
        if i in drp and 0 < i < len(bt) - 1:
            continue
        out.append(t)
    return np.array(out)


# ---------------------------------------------------------------- 7. hands
def split_hands(notes_b, together=0.08, span=16, start_split=60, stick=6.0, stick_beats=1.5):
    """notes_b: [(b, ln, k, v)] -> {'right': [...], 'left': [...]}. Chords (notes within `together` beats) are cut at
    the split that keeps each hand near its moving centre and inside a hand span."""
    if not notes_b:
        return {"right": [], "left": []}
    ev = sorted(notes_b, key=lambda n: (n[0], n[2]))
    groups, cur = [], [ev[0]]
    for n in ev[1:]:
        if n[0] - cur[0][0] <= together:
            cur.append(n)
        else:
            groups.append(cur)
            cur = [n]
    groups.append(cur)
    lc, rc = start_split - 9.0, start_split + 7.0
    out = {"right": [], "left": []}
    last = {}                                                    # key -> (hand, end beat) of its last strike
    for g in groups:
        ks = sorted(g, key=lambda n: n[2])
        best = None
        for cut in range(len(ks) + 1):
            L, R = ks[:cut], ks[cut:]
            cost = 0.0
            for hand, c in ((L, lc), (R, rc)):
                if hand:
                    lo, hi = hand[0][2], hand[-1][2]
                    cost += sum(abs(n[2] - c) for n in hand) / len(hand)
                    if hi - lo > span:
                        cost += 6.0 * (hi - lo - span)
            if L and R and L[-1][2] >= R[0][2]:
                cost += 50
            for hand_name, hand in (("left", L), ("right", R)):   # a key just held stays in its hand
                for n in hand:
                    pr = last.get(n[2])
                    if pr and pr[0] != hand_name and pr[1] >= n[0] - stick_beats:
                        cost += stick
            if not L and len(R) > 1 and R[-1][2] - R[0][2] > span:
                cost += 20
            if best is None or cost < best[0]:
                best = (cost, cut)
        L, R = ks[:best[1]], ks[best[1]:]
        out["left"].extend(L)
        out["right"].extend(R)
        for n in L:
            last[n[2]] = ("left", n[0] + n[1])
        for n in R:
            last[n[2]] = ("right", n[0] + n[1])
        if L:
            lc = 0.7 * lc + 0.3 * float(np.mean([n[2] for n in L]))
        if R:
            rc = 0.7 * rc + 0.3 * float(np.mean([n[2] for n in R]))
        if rc - lc < 7:                                          # hands never collapse onto each other
            mid = (rc + lc) / 2
            lc, rc = mid - 3.5, mid + 3.5
    return out


# ---------------------------------------------------------------- the whole pass
def clean(notes, n_frames=None, onset_grid=None, tempo=None, meter=None, quantize=True, hands=True,
          key=None, bpm_range=(40, 200), audio=None, sr=22050, ring=True, pedal=None, bass=True):
    """raw notes (seconds) -> score. tempo: a known bpm (start of the beat tracker; the map still follows the
    playing). meter: e.g. '3/4' to skip detection. Returns parts in beats (quarter notes), tempo map, report."""
    rep = {}
    n0 = len(notes)
    ns, merged = merge_pieces(notes)
    ns, gh = drop_ghosts(ns)
    bands = key_bands(audio, sr) if audio is not None else None
    ring = ring_ends(ns, audio, sr, bands=bands) if audio is not None and ring else None
    ns.sort(key=lambda n: (n["t"], n["key"]))
    n_bass = mark_bass(ns) if bass else 0
    ped_ext = apply_pedal(ns, (pedal or {}).get("sustain")) if pedal else None
    bass_cut = cut_bass(ns) if bass else 0
    restruck = cut_restrikes(ns)
    k_est = estimate_key(ns)
    k_use = _key_from_name(key) if key else k_est
    ns, settle = settle_semitones(ns, k_use)
    rep["notes"] = {"raw": n0, "merged_pieces": merged, "overtone_ghosts": gh["overtone"], "blips": gh["blip"],
                    "low_ghosts": gh["low"], "ends_from_ring": ring, "held_by_pedal": ped_ext,
                    "bass_notes": n_bass, "bass_cut_at_next_bass": bass_cut, "restrikes_cut": restruck,
                    **settle, "kept": len(ns)}
    if not ns:
        raise ValueError("no notes left after clean-up (is this a piano recording?)")
    t_end = max(n["end"] for n in ns)
    nf = n_frames or int(t_end * FPS) + 10
    env = onset_envelope(ns, nf, onset_grid)
    import flh_transcribe as TX
    if tempo:
        bpm0, conf0 = float(tempo), 1.0
    else:
        bpm0, conf0, _ = TX.tempo_estimate(env, FPS, min_bpm=bpm_range[0], max_bpm=bpm_range[1], prior_bpm=90.0)
        bpm0 = bpm0 or 90.0
    first = min(n["t"] for n in ns)
    struck_t = np.array([n["t"] for n in ns if n.get("op", 0) >= 0.4] or [n["t"] for n in ns])
    level = None
    if not tempo:                                                # which metrical level is the beat?
        cands = []
        for f in (1.0, 2 / 3, 1.5, 0.5, 2.0):
            b = bpm0 * f
            if not 40 <= b <= 200:
                continue
            bf_, cv_ = track_beats_rubato(env, FPS, b)
            bt_ = bf_ / FPS
            if len(bt_) < 4:
                continue
            bt_ = extend_beats(bt_, t_end + 0.01, first - 1e-3)
            s_, _, k_ = pick_subdivision(time_to_beat(struck_t, bt_))
            med = float(np.median(60 / np.diff(bt_)))
            pen = 0.0 if 50 <= med <= 140 else 0.08                 # usual beat range
            pen += 0.02 * (SUBDIVS.index(s_))                       # simpler grids first
            cands.append((k_[s_] - pen, f, b, bf_, cv_, s_))
        if cands:
            best = max(cands, key=lambda c_: c_[0])
            level = {"chosen_x": round(best[1], 3), "tried": {f"{c_[2]:.0f}": round(c_[0], 2) for c_ in cands}}
            bpm0, bf, curve = best[2], best[3], best[4]
    if level is None:
        bf, curve = track_beats_rubato(env, FPS, bpm0)
    bt = bf / FPS
    bt = extend_beats(bt[(bt >= first - 1.0)], t_end + 0.01, first - 1e-3) if len(bt) >= 2 else \
        np.arange(first, t_end + 60 / bpm0, 60 / bpm0)
    bpm_map = tempo_map(bt)
    starts_b = time_to_beat([n["t"] for n in ns], bt)
    ends_b = time_to_beat([n["end"] for n in ns], bt)
    struck = np.array([n.get("op", 0) >= 0.4 for n in ns])
    s, hits, kaps = pick_subdivision(starts_b[struck] if struck.sum() >= 8 else starts_b)
    nb = [(float(b), float(max(e - b, 1e-3)), n["key"], n["vel"]) for b, e, n in zip(starts_b, ends_b, ns)]
    n_beats = len(bt) - 1
    if meter:
        num, den = (int(v) for v in str(meter).split("/"))
        comp = den == 8 and num % 3 == 0 and num > 3
        mtr = {"m": num // 3 if comp else num, "phase": None, "sig": f"{num}/{den}", "num": num, "den": den,
               "compound": comp, "confidence": 1.0, "given": True}
        if comp and s not in (3, 6):
            s = 3
    else:
        mtr = detect_meter([(round(b * s) / s, ln, k, v) for b, ln, k, v in nb], n_beats, s)
        # metrical level: a counted "beat" faster than ~150 bpm is really a subdivision -> group it
        med = float(np.median(bpm_map)) if len(bpm_map) else 0.0
        if med > 150 and n_beats >= 12:
            g = 3 if mtr["m"] == 3 else 2
            ph = mtr["phase"]
            F0 = beat_features([(round(b * s) / s, ln, k, v) for b, ln, k, v in nb], n_beats)
            ph = max(range(g), key=lambda p_: float(F0[p_::g].mean()))
            bt = extend_beats(bt[ph::g], t_end + 0.01, first - 1e-3)
            bpm_map = tempo_map(bt)
            starts_b = time_to_beat([n["t"] for n in ns], bt)
            ends_b = time_to_beat([n["end"] for n in ns], bt)
            s, hits, kaps = pick_subdivision(starts_b[struck] if struck.sum() >= 8 else starts_b)
            if g == 3 and s not in (3, 6):
                s = 3
            nb = [(float(b), float(max(e - b, 1e-3)), n["key"], n["vel"]) for b, e, n in zip(starts_b, ends_b, ns)]
            n_beats = len(bt) - 1
            mtr = detect_meter([(round(b * s) / s, ln, k, v) for b, ln, k, v in nb], n_beats, s)
            mtr["regrouped"] = f"{g} counted beats = 1 beat ({med:.0f} bpm was a subdivision)"
    if mtr.get("phase") is None:
        mtr["phase"] = _best_phase(nb, n_beats, mtr["m"], s)
    # downbeats: keep bass entries / harmony changes on bar lines; repair beats the tracker miscounted
    F = beat_features([(round(b * s) / s, ln, k, v) for b, ln, k, v in nb], n_beats)
    pos, ops = downbeat_align(F, mtr["m"])
    if ops:
        bt2 = apply_beat_ops(bt, ops)
        bt2 = bt2[np.concatenate([[True], np.diff(bt2) > 0.05])]
        if len(bt2) >= 3:
            bt = bt2
            bpm_map = tempo_map(bt)
            starts_b = time_to_beat([n["t"] for n in ns], bt)
            ends_b = time_to_beat([n["end"] for n in ns], bt)
            nb = [(float(b), float(max(e - b, 1e-3)), n["key"], n["vel"]) for b, e, n in zip(starts_b, ends_b, ns)]
            n_beats = len(bt) - 1
            F = beat_features([(round(b * s) / s, ln, k, v) for b, ln, k, v in nb], n_beats)
            pos, _ = downbeat_align(F, mtr["m"], jump=1e6)        # phase only, no more repairs
    mtr["phase"] = (mtr["m"] - pos[0]) % mtr["m"]
    mtr["beat_repairs"] = {"inserted": sum(o == "insert" for _, o in ops), "dropped": sum(o == "drop" for _, o in ops)}
    # beats -> quarter notes; compound beats are dotted quarters
    q = 1.5 if mtr["compound"] else 1.0
    shift = (mtr["m"] - mtr["phase"]) % mtr["m"]                # first downbeat lands on a bar line
    out = []
    for b, ln, k, v in nb:
        if quantize:
            b2 = round(b * s) / s
            if abs(b - b2) > 0.1:                                # an ornament / off-grid note: the finer grid
                bf = round(b * 2 * s) / (2 * s)
                if abs(b - bf) < 0.06:
                    b2 = bf
            e2 = max(b2 + 1.0 / s, round((b + ln) * s) / s)
        else:
            b2, e2 = b, b + ln
        out.append((round((b2 + shift) * q, 6), round((e2 - b2) * q, 6), int(k), int(v)))
    out_pre = list(out)
    out = _dedupe(out)
    bar_q = mtr["num"] * (4.0 / mtr["den"])
    tm = [(round((i + shift) * q, 6), round(float(b) / q, 3)) for i, b in enumerate(bpm_map)]
    first_q = min(b for b, _, _, _ in out)
    drop_bars = math.floor(first_q / bar_q + 1e-9) * bar_q         # no empty bars before the music
    if drop_bars:
        out = [(round(b - drop_bars, 6), ln, k, v) for b, ln, k, v in out]
        tm = [(round(p_ - drop_bars, 6), b) for p_, b in tm]
        first_q -= drop_bars
    keep = [x for x in tm if x[0] >= 0]
    tm = ([(0.0, [b for p_, b in tm if p_ < 0][-1])] if any(p_ < 0 for p_, _ in tm) and (not keep or keep[0][0] > 0)
          else []) + keep
    if hands:
        bass_set = {(round(o[0] - drop_bars, 6), o[2]) for o, n in zip(out_pre, ns) if n.get("bass")}
        parts = split_hands(out)                                 # hands first (the bass anchors the left hand)
        bpart = [e for e in parts["left"] if (e[0], e[2]) in bass_set]
        if bpart:
            parts = {"bass": bpart, "left": [e for e in parts["left"] if (e[0], e[2]) not in bass_set],
                     "right": parts["right"]}
    else:
        parts = {"piano": out}
    timing = _timing_error(ns, bt, s, quantize)
    rep["tempo"] = {"start_bpm": round(bpm0, 1), "start_confidence": round(float(conf0), 2),
                    "median_bpm": round(float(np.median(bpm_map)) / q, 1),
                    "range_bpm": [round(float(np.percentile(bpm_map, 5)) / q, 1),
                                  round(float(np.percentile(bpm_map, 95)) / q, 1)],
                    "rubato_pct": round(100 * float(np.std(np.diff(bt)) / max(np.mean(np.diff(bt)), 1e-9)), 1),
                    "local_curve_bpm": [round(v / q, 1) for v in curve], "level": level,
                    "beats": int(n_beats), "unit": "dotted quarter" if mtr["compound"] else "quarter"}
    rep["meter"] = {k: mtr[k] for k in ("sig", "confidence", "beat_repairs", "regrouped") if k in mtr}
    rep["meter"].update(pickup_quarters=round(first_q, 3) and round(bar_q - first_q, 3),
                        alternatives=mtr.get("scores", {}))
    rep["key"] = ({"estimated": k_est["name"], "correlation": k_est["correlation"], "margin": k_est["margin"],
                   "runner_up": k_est["runner_up"], "used_for": "semitone ties only (no note moved)"}
                  if k_est else None)
    if key:
        rep["key"] = dict(rep["key"] or {}, given=key)
    rep["grid"] = {"subdivision": f"1/{s} beat", "quantized": bool(quantize), "timing": timing,
                   "struck_notes_on_grid": {f"1/{k}": round(v, 2) for k, v in hits.items()},
                   "chance_corrected": {f"1/{k}": round(v, 2) for k, v in kaps.items()}}
    rep["hands"] = {p: len(v) for p, v in parts.items()}
    beat_q = [round((i + shift) * q - drop_bars, 6) for i in range(len(bt))]
    meta = []                                                   # per kept note: snapped start (q), off-grid, model conf
    for (b, ln, k, v), n, bq in zip(nb, ns, [o[0] for o in out_pre]):
        meta.append({"q": bq - drop_bars, "key": k, "err": abs(b - round(b * s) / s), "amp": n["amp"],
                     "op": n.get("op", 0.5), "t": n["t"], "end": n["end"]})
    return {"parts": parts, "tempo_map": tm, "beats_s": [round(float(t), 4) for t in bt], "beat_q": beat_q,
            "note_meta": meta, "bands": bands, "repairs": ops,
            "beats_per_bar_q": bar_q, "meter": mtr, "subdivision": s, "shift_beats": shift, "q": q,
            "notes_s": ns, "report": rep}


def _best_phase(nb, n_beats, m, s):
    r = detect_meter([(round(b * s) / s, ln, k, v) for b, ln, k, v in nb], n_beats, s)
    return r["phase"] if r["m"] == m else 0


def _key_from_name(name):
    try:
        root, mode = str(name).strip().split()[:2]
    except ValueError:
        root, mode = str(name).strip(), "major"
    alias = {"Db": "C#", "D#": "Eb", "Gb": "F#", "G#": "Ab", "A#": "Bb", "Cb": "B", "Fb": "E", "E#": "F", "B#": "C"}
    root = alias.get(root[:1].upper() + root[1:], root[:1].upper() + root[1:])
    if root not in NAMES:
        raise ValueError(f"key: unknown root {name!r}")
    r = NAMES.index(root)
    minor = mode.lower().startswith("min")
    sc = [(r + x) % 12 for x in ((0, 2, 3, 5, 7, 8, 10, 11) if minor else (0, 2, 4, 5, 7, 9, 11))]
    return {"name": f"{root} {'minor' if minor else 'major'}", "tonic": r, "mode": "minor" if minor else "major",
            "scale": sorted(set(sc))}


def _dedupe(ev):
    """After snapping, the same key struck twice on one grid point -> one note (the longer)."""
    best = {}
    for b, ln, k, v in ev:
        key = (b, k)
        if key not in best or ln > best[key][1]:
            best[key] = (b, ln, k, v)
    out = sorted(best.values())
    # a note never runs into the next strike of the same key
    last = {}
    res = []
    for b, ln, k, v in sorted(out, key=lambda e: (e[2], e[0])):
        if k in last and last[k][0] + last[k][1] > b:
            pb, pl, pk, pv = last[k]
            res[res.index(last[k])] = (pb, max(1e-3, b - pb), pk, pv)
        n = (b, ln, k, v)
        res.append(n)
        last[k] = n
    return sorted(res)


def _timing_error(ns, bt, s, quantize):
    """How far (ms) the snapped starts are from the played ones: median and 90th percentile."""
    if not ns:
        return {}
    b = time_to_beat([n["t"] for n in ns], bt)
    bq = np.round(b * s) / s
    if quantize:
        bf = np.round(b * 2 * s) / (2 * s)
        bq = np.where((np.abs(b - bq) > 0.1) & (np.abs(b - bf) < 0.06), bf, bq)
    else:
        bq = b
    i = np.clip(np.floor(bq).astype(int), 0, len(bt) - 2)
    tq = bt[i] + (bq - i) * (bt[i + 1] - bt[i])
    d = np.abs(tq - np.array([n["t"] for n in ns])) * 1000
    return {"median_ms": round(float(np.median(d)), 1), "p90_ms": round(float(np.percentile(d, 90)), 1)}
