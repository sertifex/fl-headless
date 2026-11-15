"""fl_piano_check: how far to trust a piano transcription, bar by bar, and a cross-check against a second recording.

Per bar (bars of the detected / given meter):
  chroma  - A/B: the pitch classes the original audio has in that bar vs the ones the transcribed notes produce
            (same harmonic weighting on both sides), cosine 0..1
  grid    - share of the bar's notes that were played close to the beat grid (before snapping)
  model   - the model's own certainty for the bar's notes (level and attack)
  cross   - (with a second recording) do the two agree on the bar's harmony and bass?
score = weighted mean -> high / medium / low, with the weakest part named as the reason.

Cross-check: the two transcriptions are reduced to per-bar harmony (pitch-class profile) and bass (lowest pitch
class at the downbeat); a transposition between them is found; bars are aligned with DTW (open start/end, so an extra
intro or ending on either side is fine; repeats map many-to-one). Arrangements may differ, so notes are compared by
harmony, never note-for-note. Notes whose pitch class the other recording doesn't have in that bar are flagged as
doubtful (dropped only on request, and only when the model was unsure of them too).
"""
import numpy as np

W_SCORE = {"chroma": 0.35, "grid": 0.25, "model": 0.25, "cross": 0.15}
FLAG_BELOW = 0.7         # rendered transcription vs recording, per bar: below this a bar is flagged uncertain


def _q_to_s(x, beat_q, beats_s):
    """Quarter-note position -> seconds through the tempo map (straight-line beyond its ends)."""
    q = np.asarray(beat_q, float)
    t = np.asarray(beats_s, float)
    x = np.asarray(x, float)
    y = np.interp(x, q, t)
    if len(q) > 1:
        lo, hi = x < q[0], x > q[-1]
        y = np.where(lo, t[0] + (x - q[0]) * (t[1] - t[0]) / max(q[1] - q[0], 1e-9), y)
        y = np.where(hi, t[-1] + (x - q[-1]) * (t[-1] - t[-2]) / max(q[-1] - q[-2], 1e-9), y)
    return y


def _s_to_q(x, beats_s, beat_q):
    """Seconds -> quarter-note position (inverse of _q_to_s)."""
    return _q_to_s(x, beats_s, beat_q)


def _bar_bounds_s(beat_q, beats_s, bar_q, n_bars):
    return _q_to_s(np.arange(n_bars + 1) * bar_q, beat_q, beats_s)


DECAY_Q = 3.0            # a held piano note fades: weight halves about every 2 quarter notes (~1.3 s at 90 bpm)


def _decayed(a, b, start, tau=DECAY_Q):
    """Integral of exp(-(t - start) / tau) over [a, b] (how much a fading note contributes there)."""
    return tau * (np.exp(-(a - start) / tau) - np.exp(-(b - start) / tau))


def notes_bar_chroma(parts, bar_q, n_bars, harm=True):
    """[(b, ln, k, v)] parts -> (n_bars x 12) duration x velocity weighted chroma (+ the 12th's pc, like the audio
    bands that sum harmonics 1-3), and the lowest pitch class sounding at each downbeat (bass)."""
    C = np.zeros((n_bars, 12))
    low = np.full(n_bars, 200)
    for ev in parts.values():
        for b, ln, k, v in ev:
            a, e = b, b + ln
            j0, j1 = int(a // bar_q), int(min(n_bars - 1, (e - 1e-9) // bar_q))
            for j in range(max(0, j0), j1 + 1):
                lo_, hi_ = max(a, j * bar_q), min(e, (j + 1) * bar_q)
                if hi_ <= lo_:
                    continue
                w = _decayed(lo_, hi_, a) * v / 127.0
                C[j, k % 12] += w
                if harm:
                    C[j, (k + 7) % 12] += 0.35 * w
                if a <= j * bar_q + 0.5 and k < low[j]:
                    low[j] = k
    bass = np.where(low < 200, low % 12, -1)
    return C, bass


def audio_bar_chroma(bands, bounds_s, fps=22050 / 256):
    """Key-band levels (dB, 88 keys) -> per-bar chroma from the audio itself (energy above each key's floor)."""
    L, keys = bands
    lin = 10 ** (L / 20.0)
    floor = np.percentile(lin, 20, axis=1, keepdims=True)
    lin = np.maximum(lin - floor * 1.5, 0)
    pc = np.array([k % 12 for k in keys])
    n = len(bounds_s) - 1
    C = np.zeros((n, 12))
    level = np.zeros(n)
    for j in range(n):
        a, b = int(max(0, bounds_s[j]) * fps), int(max(0, bounds_s[j + 1]) * fps)
        if b <= a:
            continue
        seg = lin[:, a:b].sum(1)
        level[j] = seg.sum()
        for p in range(12):
            C[j, p] = seg[pc == p].sum()
    return C, level


def _cos(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na > 0 and nb > 0 else None


def bar_report(c, bar_q, cross=None, render=None):
    """c: fl_piano_clean.clean() result. -> {'bars': [...], 'summary': {...}, 'low': [...]}"""
    parts = c["parts"]
    end_q = max((b + ln for ev in parts.values() for b, ln, _, _ in ev), default=0.0)
    n_bars = max(1, int(np.ceil(end_q / bar_q - 1e-9)))
    NC, _ = notes_bar_chroma(parts, bar_q, n_bars)
    bounds = _bar_bounds_s(c["beat_q"], c["beats_s"], bar_q, n_bars)
    AC, lvl = audio_bar_chroma(c["bands"], bounds) if c.get("bands") is not None else (None, None)
    quiet = (lvl < 0.02 * np.median(lvl[lvl > 0])) if lvl is not None and (lvl > 0).any() else np.zeros(n_bars, bool)
    per = {j: [] for j in range(n_bars)}
    for m in c["note_meta"]:
        j = int(m["q"] // bar_q)
        if 0 <= j < n_bars:
            per[j].append(m)
    rows = []
    for j in range(n_bars):
        ns = per[j]
        grid = float(np.mean([m["err"] < 0.06 for m in ns])) if ns else None
        model = float(np.mean([0.5 * min(1.0, m["amp"] / 0.6) + 0.5 * m["op"] for m in ns])) if ns else None
        chroma = _cos(AC[j], NC[j]) if AC is not None else None
        if chroma is None and AC is not None and quiet[j] and NC[j].sum() == 0:
            chroma = 1.0                                         # silence written as silence
        x = cross.get(j) if cross else None
        rnd = float(render[j]) if render is not None and j < len(render) else None
        # the rendered comparison, when there is one, is the better "does it sound like the recording" measure
        parts_ = {"chroma": rnd if rnd is not None else chroma, "grid": grid, "model": model, "cross": x}
        have = {k: v for k, v in parts_.items() if v is not None}
        if have:
            wsum = sum(W_SCORE[k] for k in have)
            score = sum(W_SCORE[k] * v for k, v in have.items()) / wsum
        else:
            score = None
        weakest = min(have, key=lambda k: have[k]) if have else None
        rows.append({"bar": j + 1, "score": None if score is None else round(score, 2),
                     "chroma": None if chroma is None else round(chroma, 2),
                     "grid": None if grid is None else round(grid, 2),
                     "model": None if model is None else round(model, 2),
                     "cross": None if x is None else round(x, 2), "notes": len(ns),
                     "render": None if rnd is None else round(rnd, 2),
                     "level": "empty" if score is None else ("high" if score >= 0.75 else "medium" if score >= 0.55 else "low"),
                     "weakest": weakest})
    summ = {lv: sum(r["level"] == lv for r in rows) for lv in ("high", "medium", "low", "empty")}
    vals = [r["chroma"] for r in rows if r["chroma"] is not None]
    summ["chroma_match_median"] = round(float(np.median(vals)), 2) if vals else None
    low = [{"bar": r["bar"], "score": r["score"], "why": _why(r)} for r in rows if r["level"] == "low"]
    return {"bars": rows, "summary": summ, "low": low, "n_bars": n_bars}


_REASON = {"chroma": "the transcription doesn't sound like the recording here (rendered and compared)",
           "grid": "the playing is far off the beat grid (free time / rubato): bar lines are a guess",
           "model": "the model was unsure of these notes (quiet or blurred by the pedal)",
           "cross": "the other recording has different harmony / bass here"}


def _why(r):
    return _REASON.get(r["weakest"], "")


# ---------------------------------------------------------------- cross-check
def _norm_rows(C):
    n = np.linalg.norm(C, axis=1, keepdims=True)
    return np.where(n > 0, C / np.maximum(n, 1e-12), 0.0)


def beat_chroma(c, fps=22050 / 256):
    """Per-beat chroma of the recording itself (key bands, energy above floor, log-compressed, unit length)."""
    L, keys = c["bands"]
    lin = 10 ** (L / 20.0)
    lin = np.maximum(lin - np.percentile(lin, 20, axis=1, keepdims=True) * 1.5, 0)
    pc = np.array([k % 12 for k in keys])
    P = np.stack([lin[pc == p].sum(0) for p in range(12)])          # 12 x frames
    bt = np.asarray(c["beats_s"])
    out = np.zeros((len(bt) - 1, 12))
    for i in range(len(bt) - 1):
        a, b = int(max(0, bt[i]) * fps), int(max(0, bt[i + 1]) * fps)
        if b > a:
            out[i] = np.log1p(P[:, a:b].mean(1) * 100)
    return _norm_rows(out)


def dtw_sub(D, pen=0.25):
    """Subsequence DTW: every row (start to end) is aligned, the columns may start / end anywhere. Steps (1,1),
    (1,2), (2,1) cover a 2:1 tempo difference; (1,0) / (0,1) cost `pen` extra (a held or repeated stretch).
    -> (path [(i, j)], mean cost along the path)."""
    n, m = D.shape
    A = np.full((n + 1, m + 2), np.inf)
    bk = np.zeros((n + 1, m + 2), np.int8)
    A[0, :] = 0.0
    steps = ((1, 1, 0.0), (1, 2, 0.1), (2, 1, 0.1), (1, 0, pen), (0, 1, pen))
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, arg = np.inf, 0
            d = D[i - 1, j - 1]
            for s_, (di, dj, extra) in enumerate(steps):
                pi, pj = i - di, j - dj
                if pi < 0 or pj < 0 or (di == 0 and dj == 0):
                    continue
                if pi == 0 and di != 0 and dj == 0:
                    continue
                v = A[pi, pj] + d + extra
                if v < best:
                    best, arg = v, s_
            A[i, j] = best
            bk[i, j] = arg
    j = int(np.argmin(A[n, 1:m + 1])) + 1
    cost = float(A[n, j])
    i = n
    path = []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        di, dj, _ = steps[bk[i, j]]
        i, j = i - di, j - dj
    path = path[::-1]
    return path, cost / max(len(path), 1)


def _notes_chroma_span(meta, t0, t1):
    v = np.zeros(12)
    for m in meta:
        ov = min(m["end"], t1) - max(m["t"], t0)
        if ov > 0:
            v[m["key"] % 12] += ov * max(m["amp"], 0.05)
    return v


def _low_pcs(meta, t, win):
    sound = [m for m in meta if m["t"] <= t + win and m["end"] >= t - 0.05]
    if not sound:
        return set()
    lo = min(m["key"] for m in sound)
    return {m["key"] % 12 for m in sound if m["key"] <= lo + 2}


HOP_S = 0.5
MAX_COST = 0.45          # mean DTW cost above this: the recordings don't line up -> no verdicts


def time_chroma(meta, hop_s=HOP_S, shift=0):
    """Notes -> mean-removed, unit-length chroma every hop_s seconds (what changes, not the key's colour)."""
    T = max((m["end"] for m in meta), default=0.0)
    n = int(T / hop_s) + 1
    X = np.zeros((n, 12))
    for m in meta:
        a, b = int(m["t"] / hop_s), int(m["end"] / hop_s) + 1
        X[a:b, (m["key"] + shift) % 12] += max(m["amp"], 0.05)
    X = X - X.mean(0)
    return _norm_rows(X)


def cross_check(ca, cb, bar_qa, bar_qb=None):
    """Two clean() results of the same piece (arrangements may differ). Aligned in time on what changes in their
    harmony (DTW, 2:1 tempo steps, either recording may have extra material, any transposition). If they don't
    line up, says so and gives no verdicts. Otherwise each bar of the first is compared with the matched stretch
    of the second: harmony (pitch classes) and bass (lowest notes at the downbeat).
    -> ({bar idx: 0..1} or None, report, {bar idx: pitch classes the other lacks})."""
    meta, metb0 = ca["note_meta"], cb["note_meta"]
    A = time_chroma(meta)
    best = None
    # transpositions: the 4 whose overall pitch profile fits best get the (slower) full alignment
    pa = np.zeros(12)
    pb = np.zeros(12)
    for m in meta:
        pa[m["key"] % 12] += (m["end"] - m["t"]) * max(m["amp"], 0.05)
    for m in metb0:
        pb[m["key"] % 12] += (m["end"] - m["t"]) * max(m["amp"], 0.05)
    shifts = sorted(range(12), key=lambda k: -(_cos(pa, np.roll(pb, k)) or 0.0))[:4]
    for sh in shifts:
        B = time_chroma(metb0, shift=sh)
        D = 1.0 - A @ B.T
        p1, c1 = dtw_sub(D)
        p2, c2 = dtw_sub(D.T)
        cand = (c1, sh, p1) if c1 <= c2 else (c2, sh, [(i, j) for j, i in p2])
        if best is None or cand[0] < best[0]:
            best = cand
    cost, sh, path = best
    rep = {"other_is_higher_by_semitones": int((-sh + 6) % 12 - 6), "alignment_cost": round(cost, 3),
           "alignment": "in time, on harmony changes (DTW)"}
    if cost > MAX_COST:
        rep.pop("other_is_higher_by_semitones")
        rep["lines_up"] = False
        rep["verdict"] = ("the two recordings don't line up (alignment cost "
                          f"{cost:.2f}, needs < {MAX_COST}): a different arrangement or section order, another "
                          "piece, or a transcription too rough to follow. No per-bar verdicts from the cross-check; "
                          "the confidence report uses this recording alone")
        return None, rep, {}
    rep["lines_up"] = True
    a_of = {}
    for i, j in path:
        a_of.setdefault(i, []).append(j)
    bta = np.asarray(ca["beats_s"])
    qa = np.asarray(ca["beat_q"])
    ea = max((b + ln for ev in ca["parts"].values() for b, ln, _, _ in ev), default=0.0)
    na = max(1, int(np.ceil(ea / bar_qa - 1e-9)))
    metb = [dict(m, key=m["key"] + sh) for m in metb0]
    names = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    agree, rows, doubtful = {}, [], {}
    for jb in range(na):
        ta0 = float(_q_to_s(jb * bar_qa, qa, bta))
        ta1 = float(_q_to_s((jb + 1) * bar_qa, qa, bta))
        fr = [f for f in range(int(ta0 / HOP_S), int(ta1 / HOP_S) + 1) if f in a_of]
        if not fr:
            continue
        js = [j for f in fr for j in a_of[f]]
        tb0, tb1 = min(js) * HOP_S, (max(js) + 1) * HOP_S
        va = _notes_chroma_span(meta, ta0, ta1)
        vb = _notes_chroma_span(metb, tb0, tb1)
        if not va.any():
            continue
        h = _cos(va, vb) or 0.0
        la = _low_pcs(meta, ta0, 0.15)
        lb = _low_pcs(metb, tb0, 0.3)
        bass_ok = None if not la or not lb else bool(la & lb)
        agree[jb] = 0.7 * h + 0.3 * (1.0 if bass_ok in (True, None) else 0.0)
        status = "agree" if h >= 0.8 and bass_ok is not False else ("partial" if h >= 0.6 or bass_ok else "disagree")
        rows.append({"bar": jb + 1, "other_s": [round(tb0, 1), round(tb1, 1)], "harmony": round(h, 2),
                     "bass_same": bass_ok, "status": status})
        va_n, vb_n = va / max(va.max(), 1e-9), vb / max(vb.max(), 1e-9)
        miss = {p for p in range(12) if va_n[p] > 0.25 and vb_n[p] < 0.05}
        if miss:
            doubtful[jb] = miss
    summ = {s_: sum(r["status"] == s_ for r in rows) for s_ in ("agree", "partial", "disagree")}
    summ["not_aligned"] = na - len(rows)
    js_all = [j for _, j in path]
    rep.update(other_span_s=[round(min(js_all) * HOP_S, 1), round((max(js_all) + 1) * HOP_S, 1)],
               bars_this=na, summary=summ, disagree=[r for r in rows if r["status"] == "disagree"],
               doubtful_pitch_classes={str(i + 1): sorted(names[p] for p in v) for i, v in sorted(doubtful.items())})
    return agree, rep, doubtful


def drop_doubtful(parts, doubtful, bar_q, meta=None, max_conf=0.45):
    """Remove notes whose pitch class the other recording lacks in that bar AND that the model was unsure of."""
    conf = {}
    for m in meta or []:
        conf[(round(m["q"], 4), m["key"])] = 0.5 * min(1.0, m["amp"] / 0.6) + 0.5 * m["op"]
    out, n = {}, 0
    for p, ev in parts.items():
        keep = []
        for b, ln, k, v in ev:
            j = int(b // bar_q)
            if k % 12 in doubtful.get(j, ()) and conf.get((round(b, 4), k), 0.0) < max_conf:
                n += 1
                continue
            keep.append((b, ln, k, v))
        out[p] = keep
    return out, n


# ---------------------------------------------------------------- A/B audio
def synth(parts, beat_q, beats_s, sr, n_samples):
    """Notes (quarter-note time) -> a plain piano-like tone at the recording's timing (through the tempo map)."""
    y = np.zeros(n_samples, np.float32)

    def sec(x):
        return float(_q_to_s(x, beat_q, beats_s))
    for ev in parts.values():
        for b, ln, k, v in ev:
            a = int(sec(b) * sr)
            e = int(sec(b + ln) * sr)
            if a >= n_samples or e <= a:
                continue
            d = min(n_samples - a, e - a + int(0.25 * sr))
            tt = np.arange(d) / sr
            f0 = 440.0 * 2 ** ((k - 69) / 12)
            env = np.exp(-tt * (1.2 + f0 / 900)) * np.minimum(1.0, tt / 0.003)
            rel = np.clip(1 - (tt - (e - a) / sr) / 0.25, 0, 1)
            sig = sum(np.sin(2 * np.pi * f0 * h * tt) / h ** 1.5 for h in (1, 2, 3, 4) if f0 * h < sr / 2)
            y[a:a + d] += (sig * env * rel * (v / 127.0) * 0.25).astype(np.float32)
    return y


# ---------------------------------------------------------------- self-check: render, compare, re-check, flag
SC_SR = 22050
SC_FPS = SC_SR / 256


def synth_notes(notes, n, sr=SC_SR, t0=0.0):
    """Notes in seconds -> a plain piano-like tone (same voice for every candidate, so comparisons are fair)."""
    y = np.zeros(n, np.float32)
    for q in notes:
        a = int((q["t"] - t0) * sr)
        e = int((q["end"] - t0) * sr)
        if e <= 0 or a >= n or e <= a:
            continue
        a0 = max(a, 0)
        d = min(n - a0, e - a0 + int(0.25 * sr))
        tt = (np.arange(d) + (a0 - a)) / sr
        f0 = 440.0 * 2 ** ((q["key"] - 69) / 12)
        env = np.exp(-tt * (1.2 + f0 / 900)) * np.minimum(1.0, tt / 0.003)
        rel = np.clip(1 - (tt - (e - a) / sr) / 0.25, 0, 1)
        sig = sum(np.sin(2 * np.pi * f0 * h * tt) / h ** 1.5 for h in (1, 2, 3, 4) if f0 * h < sr / 2)
        y[a0:a0 + d] += (sig * env * rel * (q["vel"] / 127.0) * 0.25).astype(np.float32)
    return y


def _prep(L):
    lin = 10 ** (L / 20.0)
    lin = np.maximum(lin - np.percentile(lin, 20, axis=1, keepdims=True) * 1.5, 0)
    return np.log1p(lin * 100)


def _match(R, S, f0, f1, sub=4):
    """How alike two key-band pictures are over frames f0..f1: cosine in 4 slices of the span, averaged
    (so a note in the wrong place within the bar counts)."""
    cs = []
    for a, b in zip(*(lambda e: (e[:-1], e[1:]))(np.linspace(f0, f1, sub + 1).astype(int))):
        if b <= a:
            continue
        r, s_ = R[:, a:b].mean(1), S[:, a:b].mean(1)
        nr, ns = np.linalg.norm(r), np.linalg.norm(s_)
        if nr < 1e-6 and ns < 1e-6:
            cs.append(1.0)
        elif nr < 1e-6 or ns < 1e-6:
            cs.append(0.0)
        else:
            cs.append(float(r @ s_ / (nr * ns)))
    return float(np.mean(cs)) if cs else 1.0


def _sounding(notes, pedal):
    import fl_piano_clean as PC
    ns = [dict(q) for q in notes]
    if pedal:
        PC.apply_pedal(ns, pedal.get("sustain"))
    PC.cut_restrikes(ns)
    return ns


def render_scores(rec_bands, notes, pedal, bounds, n):
    """Render the notes, analyse the render like the recording, score every bar 0..1."""
    import fl_piano_clean as PC
    R = _prep(rec_bands[0])
    S = _prep(PC.key_bands(synth_notes(_sounding(notes, pedal), n))[0])
    nb = len(bounds) - 1
    return np.array([_match(R, S, int(max(0, bounds[j]) * SC_FPS), int(max(0, bounds[j + 1]) * SC_FPS))
                     for j in range(nb)])


def _local_scores(R, notes, pedal, bounds, j, n_total, span=3):
    """Scores of bars j .. j+span-1 from a render of only the notes around them (includes ringing from before)."""
    import fl_piano_clean as PC
    nb = len(bounds) - 1
    ks = list(range(j, min(nb, j + span)))
    t0 = max(0.0, bounds[j] - 3.0)
    t1 = bounds[ks[-1] + 1]
    near = [q for q in notes if q["end"] > t0 and q["t"] < t1]
    m = int((t1 - t0) * SC_SR) + 256
    S = _prep(PC.key_bands(synth_notes(_sounding(near, pedal), m, t0=t0))[0])
    off = int(t0 * SC_FPS)
    out = []
    for k in ks:
        a, b = int(bounds[k] * SC_FPS), int(bounds[k + 1] * SC_FPS)
        Rk = R[:, max(0, a):b]
        Sk = S[:, max(0, a - off):max(0, b - off)]
        w = min(Rk.shape[1], Sk.shape[1])
        out.append(_match(Rk[:, :w], Sk[:, :w], 0, w))
    return np.array(out)


def _bar_of(t, bounds):
    return int(np.clip(np.searchsorted(bounds, t, "right") - 1, 0, len(bounds) - 2))


def self_check(rec_bands, notes, pedal, bounds, n, candidates, thr=0.8, flag=0.7, margin=0.03, max_bars=120):
    """Render the transcription, compare it with the recording bar by bar; for each bar under `thr`, try every
    candidate source for the notes struck in that bar (a candidate replaces them, or '+name' adds its notes
    the current ones lack) and keep a change only when that bar and the next together match better by `margin`.
    candidates: {name: notes in seconds}. -> (notes, report)."""
    R = _prep(rec_bands[0])
    before = render_scores(rec_bands, notes, pedal, bounds, n)
    cur = [dict(q) for q in notes]
    nb = len(bounds) - 1
    bad = [j for j in range(nb) if before[j] < thr][:max_bars]
    repaired = []

    def same(a, b):
        return a["key"] == b["key"] and abs(a["t"] - b["t"]) < 0.08
    for j in bad:
        base_v = _local_scores(R, cur, pedal, bounds, j, n)
        base = float(base_v.mean())
        inbar = [q for q in cur if _bar_of(q["t"], bounds) == j]
        rest = [q for q in cur if _bar_of(q["t"], bounds) != j]
        best = (base + margin, None, None)
        for name, cand in candidates.items():
            cb = [dict(q) for q in cand if _bar_of(q["t"], bounds) == j]
            if not cb and not inbar:
                continue
            for mode in ("replace", "add"):
                if mode == "replace":
                    trial_bar = cb
                else:
                    extra = [q for q in cb if not any(same(q, p) for p in inbar)]
                    if not extra:
                        continue
                    trial_bar = inbar + extra
                v = _local_scores(R, rest + trial_bar, pedal, bounds, j, n)
                sc = float(v.mean())
                # better overall AND no bar it touches gets worse (pedal-held notes ring into later bars)
                if sc > best[0] and float((v - base_v).min()) >= -0.02:
                    best = (sc, f"{'+' if mode == 'add' else ''}{name}", trial_bar)
        if best[1]:
            cur = rest + best[2]
            repaired.append({"bar": j + 1, "used": best[1], "notes_before": len(inbar), "notes_after": len(best[2]),
                             "local_match": [round(base, 2), round(best[0], 2)]})
    cur.sort(key=lambda q: (q["t"], q["key"]))
    after = render_scores(rec_bands, cur, pedal, bounds, n) if repaired else before
    still = [{"bar": j + 1, "match": round(float(after[j]), 2)} for j in range(nb) if after[j] < flag]
    rep = {"bars": nb, "checked": len(bad), "recheck_below": thr, "uncertain_below": flag,
           "match_median": [round(float(np.median(before)), 2), round(float(np.median(after)), 2)],
           "bars_below_recheck": [int((before < thr).sum()), int((after < thr).sum())],
           "uncertain": [int((before < flag).sum()), int((after < flag).sum())],
           "repaired": repaired, "still_uncertain": still, "per_bar_after": [round(float(v), 2) for v in after]}
    return cur, rep
