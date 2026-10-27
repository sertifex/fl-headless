"""fl_musicality: does a written part / beat "rhyme"? Plain helper (stdlib only, no server state) that scores note
lists the way an arranger listens - is there a hook, do the parts fit the harmony, do they lock into a groove, does
the music go somewhere. flh_compose uses it to keep the best of a few seeded candidates and to score parts that are
already in a project (flp_musicality).

Notes are dicts {s (16th steps from the block start), l (steps), k (MIDI pitch), v}; drum hits {s, part}; chords
[{start, len, root, iv | pcs, bass?}] (flh_compose's chord dicts work as they are). Everything is a measurement of
the notes, never of the sound: a high score means "follows the craft rules" (motif, chord tones on strong beats,
steps, cadences, bass on the kick, sections that build), not "sounds good" - a human still has to listen.

score_beat(parts, chords, ...) -> {score 0-100, hook, fit, groove, form (0..1 each), weak [what pulled it down]}
"""
import math

WEIGHTS = {"hook": 0.30, "fit": 0.25, "groove": 0.25, "form": 0.20}
WORDS = {                                     # what a low sub-score means, in words (the result's 'weak' list)
    "rhythm_repeat": "the melody's rhythm doesn't repeat (no motif to remember)",
    "variation": "the melody repeats exactly with no development",
    "contour": "repeated rhythms don't keep the motif's shape",
    "hook_return": "the opening idea never comes back",
    "steps": "the melody leaps more than it steps",
    "range": "the melody's range is too narrow or too wide to sing",
    "shape": "phrases drone on one note or hammer the same top note",
    "space": "the melody leaves no room (no rests) or is nearly empty",
    "chord_tones": "strong beats miss the chord tones",
    "passing": "non-chord tones aren't approached / left by step",
    "collisions": "the melody rubs a semitone against the chords on strong beats",
    "lock": "the bass doesn't lock to the kick",
    "flams": "bass and kick land just apart (flams)",
    "changes": "the bass misses the roots on chord changes",
    "drum_repeat": "the drum groove doesn't settle into a repeating pattern",
    "register": "melody and bass crowd the same register",
    "answer": "the counter-line talks over the melody instead of answering it",
    "cadence": "phrases don't land on a stable chord tone",
    "structure": "no phrase structure (AABA / ABAB) in the melody",
    "fills": "no drum fill / change at phrase ends",
    "sections": "the sections don't build or contrast (same density everywhere)",
}


# ----------------------------------------------------------------------------- small helpers
def _peak(x, lo, hi, soft):
    """1 inside [lo, hi], falling linearly to 0 over `soft` outside."""
    if x < lo:
        return max(0.0, 1.0 - (lo - x) / soft)
    if x > hi:
        return max(0.0, 1.0 - (x - hi) / soft)
    return 1.0


def _mean(xs, default=None):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else default


def _pcs(c):
    if c.get("pcs") is not None:
        return set(int(p) % 12 for p in c["pcs"])
    return {(int(c["root"]) + int(i)) % 12 for i in c.get("iv") or [0, 4, 7]}


def _stable(c):
    """Root, 3rd and 5th (the notes a phrase can end on)."""
    r = int(c.get("root", 0))
    iv = [int(i) for i in (c.get("iv") or [])] or sorted((p - r) % 12 for p in _pcs(c))
    th = next((i for i in iv if i in (3, 4)), None)
    fi = next((i for i in iv if i in (6, 7, 8)), None)
    return {"root": r % 12, "third": None if th is None else (r + th) % 12, "fifth": None if fi is None else (r + fi) % 12}


def chord_at(chords, s):
    cur = chords[0] if chords else None
    for c in chords:
        if c["start"] <= s + 1e-6:
            cur = c
        else:
            break
    return cur


def _sorted(notes):
    return sorted((n for n in notes or [] if n is not None), key=lambda n: (n["s"], n.get("k", 0)))


def _mono(notes):
    """The top line of a part (one note per onset: the highest)."""
    out = {}
    for n in notes:
        key = round(n["s"], 2)
        if key not in out or n["k"] > out[key]["k"]:
            out[key] = n
    return [out[k] for k in sorted(out)]


def _q(x):
    """A position on the nearest 16th: swing (up to a triplet feel) and played timing don't make a rhythm differ."""
    return int(math.floor(x + 0.5))


def _sig(notes, b, spb):
    """Onset positions inside bar b (half-step resolution)."""
    a = b * spb
    return frozenset(_q(n["s"] - a) for n in notes if a - 1e-6 <= n["s"] < a + spb - 1e-6)


def _jacc(a, b):
    if not a and not b:
        return None
    return len(a & b) / float(len(a | b))


def _bar_notes(notes, b, spb):
    a = b * spb
    return [n for n in notes if a - 1e-6 <= n["s"] < a + spb - 1e-6]


def _contour(ns):
    return [y["k"] - x["k"] for x, y in zip(ns, ns[1:])]


def _contour_sim(a, b):
    """Same motif shape: the same direction, about the same size (a sequence on scale steps may differ by a
    semitone or two)."""
    if not a or not b:
        return 1.0 if not a and not b else 0.0
    n = min(len(a), len(b))
    sc = 0.0
    for x, y in zip(a[:n], b[:n]):
        sx, sy = (x > 0) - (x < 0), (y > 0) - (y < 0)
        if sx == sy and abs(x - y) <= 2:
            sc += 1.0
        elif sx == sy:
            sc += 0.5
    return sc / float(max(len(a), len(b)))


def _sounding(notes, s):
    return [n for n in notes if n["s"] <= s + 1e-6 < n["s"] + n["l"] - 1e-6]


# ----------------------------------------------------------------------------- melody
def melody_metrics(mel, chords, spb=16, strong=(0, 8), bars=None, chord_notes=None, unit_bars=None):
    """Sub-scores (0..1) of a lead line: motif repetition vs variation, singability, space, harmonic fit."""
    mel = _mono(_sorted(mel))
    out = {}
    if len(mel) < 3:
        return {"empty": True}
    nb = int(bars or max(1, math.ceil((mel[-1]["s"] + mel[-1]["l"]) / spb - 1e-6)))
    per = [[] for _ in range(nb)]                         # the notes of each bar, once
    for n in mel:
        b = int((n["s"] + 1e-6) // spb)
        if 0 <= b < nb:
            per[b].append(n)
    sigs = [frozenset(_q(n["s"] - b * spb) for n in per[b]) for b in range(nb)]
    conts = [_contour(per[b]) for b in range(nb)]
    busy = [b for b in range(nb) if sigs[b]]
    # motif: each bar's rhythm recurs elsewhere, but not every bar is an exact copy
    rr = []
    for b in busy:
        js = [_jacc(sigs[b], sigs[o]) for o in busy if o != b]
        js = [j for j in js if j is not None]
        if js:
            rr.append(max(js))
    rep = _mean(rr, 0.0)
    out["rhythm_repeat"] = _peak(rep, 0.6, 1.0, 0.45)
    exact = {}
    for b in busy:
        exact.setdefault(tuple((_q(n["s"] - b * spb), n["k"]) for n in per[b]), []).append(b)
    copies = sum(len(v) for v in exact.values() if len(v) > 1) / float(max(1, len(busy)))
    distinct = len({n["k"] for n in mel})
    out["variation"] = min(_peak(copies, 0.0, 0.5, 0.45), _peak(distinct, 4, 12, 3))
    cs = []
    for i, b in enumerate(busy):
        for o in busy[i + 1:]:
            j = _jacc(sigs[b], sigs[o])
            if j is not None and j >= 0.7:
                cs.append(_contour_sim(conts[b], conts[o]))
    out["contour"] = _mean(cs, 0.0) if cs else 0.0
    ub = int(unit_bars or (2 if nb >= 4 else 1))
    first = [sigs[b] for b in range(min(ub, nb))]
    ret = 0.0
    for st in range(ub, nb - ub + 1, ub):
        js = [_jacc(first[i], sigs[st + i]) for i in range(len(first))]
        js = [j for j in js if j is not None]
        if js and _mean(js) >= 0.7:
            ret = 1.0
            break
        if js:
            ret = max(ret, _mean(js) * 0.8)
    out["hook_return"] = ret
    # singable: steps, leaps that recover, range
    ivs = [y["k"] - x["k"] for x, y in zip(mel, mel[1:])]
    moving = [i for i in ivs if i != 0]
    steps = sum(1 for i in moving if abs(i) <= 3) / float(max(1, len(moving)))   # a minor 3rd is a pentatonic step
    big = sum(1 for i in moving if abs(i) > 7) / float(max(1, len(moving)))
    rec = []
    for a, b_ in zip(ivs, ivs[1:]):
        if abs(a) > 4:
            rec.append(1.0 if (b_ * a < 0 and abs(b_) <= 4) or b_ == 0 else 0.0)
    out["steps"] = min(_peak(steps, 0.7, 1.0, 0.5), _peak(big, 0.0, 0.06, 0.2), _mean(rec, 1.0) * 0.5 + 0.5)
    ks = sorted(n["k"] for n in mel)
    span = ks[-1] - ks[0]
    out["range"] = _peak(span, 7, 17, 7)
    # shape: each 2-bar phrase moves (no droning on one note) and has a peak it doesn't hammer
    sh = []
    for u in range(0, nb, 2):
        ns = [n for n in mel if u * spb <= n["s"] < min(nb, u + 2) * spb]
        if len(ns) < 2:
            continue
        ks_ = [n["k"] for n in ns]
        same = sum(1 for x, y in zip(ks_, ks_[1:]) if x == y) / float(len(ks_) - 1)
        top = ks_.count(max(ks_)) / float(len(ks_))
        sh.append(min(_peak(len(set(ks_)), 3, 99, 2), _peak(same, 0.0, 0.3, 0.4), _peak(top, 0.0, 0.34, 0.4)))
    out["shape"] = _mean(sh, 0.0)
    # space: not every 16th filled, some breathing gaps per 2 bars
    dens = len(mel) / float(nb * spb)
    gaps = []
    for u in range(0, nb, 2):
        a, b_ = u * spb, min(nb, u + 2) * spb
        ns = [n for n in mel if a <= n["s"] < b_]
        if not ns:
            gaps.append(0.6)
            continue
        ends = [n["s"] + n["l"] for n in ns]
        free = [ns[i + 1]["s"] - ends[i] for i in range(len(ns) - 1)] + [b_ - ends[-1], ns[0]["s"] - a]
        tail = (b_ - ends[-1]) + (0 if u + 2 >= nb else 0)    # the phrase breathes at its end (call / answer)
        nxt = [n for n in mel if n["s"] >= b_]
        if nxt:
            tail = max(tail, 0) + max(0.0, nxt[0]["s"] - b_)
        gaps.append(1.0 if max(free) >= 2 and tail >= 1.5 else 0.6 if max(free) >= 2 else 0.3 if max(free) >= 1 else 0.0)
    out["space"] = min(_peak(dens, 0.08, 0.42, 0.2), _mean(gaps, 0.0) * 0.6 + 0.4)
    # fit: chord tones on the strong beats, passing / neighbour tones by step, no semitone rubs
    if chords:
        st = []
        coll, coll_n = 0, 0
        cby = [[] for _ in range(nb)]                     # chord notes overlapping each bar
        for x in chord_notes or []:
            for b in range(max(0, int(x["s"] // spb)), min(nb, int((x["s"] + x["l"]) // spb) + 1)):
                cby[b].append(x)
        mby = [[] for _ in range(nb)]
        for x in mel:
            for b in range(max(0, int(x["s"] // spb)), min(nb, int((x["s"] + x["l"]) // spb) + 1)):
                mby[b].append(x)
        for b in range(nb):
            for p in strong:
                s = b * spb + p + 0.3                         # just after the beat: played notes land a hair late
                snd = _sounding(mby[b], s)
                if not snd:
                    continue
                n = snd[-1]
                c = chord_at(chords, s)
                pcs = _pcs(c)
                pc = n["k"] % 12
                st.append(1.0 if pc in pcs else 0.6 if (pc - c["root"]) % 12 in (2, 9) else 0.0)
                if chord_notes:
                    coll_n += 1
                    if any(abs(n["k"] - x["k"]) in (1, 13) or n["k"] - x["k"] == -11
                           for x in _sounding(cby[b], s)):    # a semitone / b9 rub, or a maj7 over the lead
                        coll += 1
        out["chord_tones"] = _peak(_mean(st, 0.0), 0.8, 1.0, 0.5)
        if chord_notes and coll_n:
            out["collisions"] = _peak(coll / float(coll_n), 0.0, 0.03, 0.25)
        nct = []
        for i, n in enumerate(mel):
            c = chord_at(chords, n["s"])
            if n["k"] % 12 in _pcs(c):
                continue
            a = abs(n["k"] - mel[i - 1]["k"]) if i else 0
            b_ = abs(mel[i + 1]["k"] - n["k"]) if i + 1 < len(mel) else 0
            nct.append(1.0 if a <= 2 and b_ <= 2 else 0.5 if min(a, b_) <= 2 else 0.0)
        out["passing"] = _mean(nct, 1.0)
        # phrase ends: the last note of each unit lands on a stable chord tone
        cad = []
        for u in range(ub, nb + 1, ub):
            ns = [n for n in mel if (u - ub) * spb <= n["s"] < u * spb]
            if not ns:
                continue
            n = ns[-1]
            c = chord_at(chords, n["s"])
            sb = _stable(c)
            pc = n["k"] % 12
            sc = 1.0 if pc in (sb["root"], sb["third"]) else 0.7 if pc == sb["fifth"] else 0.3 if pc in _pcs(c) else 0.0
            if n["l"] < 1.5:
                sc *= 0.8
            cad.append(sc)
        out["cadence"] = _mean(cad, 0.0)
    out["_phrase_bars"] = ub
    return out


def structure_metric(mel, spb=16, bars=None, unit_bars=2):
    """AABA / ABAB / AAAB...: same-letter units similar, the hook recognisable when it returns, not one unit 4x."""
    mel = _mono(_sorted(mel))
    if len(mel) < 4:
        return None
    nb = int(bars or max(1, math.ceil((mel[-1]["s"] + mel[-1]["l"]) / spb - 1e-6)))
    ub = unit_bars if nb >= 4 * unit_bars else 1
    nu = nb // ub
    if nu < 3:
        return None
    units = []
    for u in range(nu):
        ns = [n for n in mel if u * ub * spb <= n["s"] < (u + 1) * ub * spb]
        units.append(([frozenset(_q(n["s"] - u * ub * spb) for n in ns)], _contour(ns),
                      [n["k"] for n in ns]))

    memo = {}

    def sim(i, j):
        if (i, j) in memo:
            return memo[(i, j)]
        memo[(i, j)] = r = _sim(i, j)
        return r

    def _sim(i, j):
        a, b = units[i], units[j]
        jr = _jacc(a[0][0], b[0][0])
        if jr is None:
            return 1.0
        return 0.65 * jr + 0.35 * _contour_sim(a[1], b[1])
    best = 0.0
    for tpl in ("AABA", "ABAB", "AAAB", "ABAC", "AABB", "ABAA"):
        t = (tpl * (nu // 4 + 1))[:nu]
        same = [sim(i, j) for i in range(nu) for j in range(i + 1, nu) if t[i] == t[j]]
        diff = [sim(i, j) for i in range(nu) for j in range(i + 1, nu) if t[i] != t[j]]
        if not same:
            continue
        s = _mean(same) * 0.75 + (0.25 if not diff or min(diff) < 0.9 else 0.05)
        best = max(best, s)
    exact = len({tuple(u[2]) for u in units}) == 1
    return best * (0.75 if exact else 1.0)


# ----------------------------------------------------------------------------- groove
def groove_metrics(parts, chords, spb=16, bars=None, bass_lock="kick", pulse=4):
    out = {}
    drums = parts.get("drums") or []
    kick = sorted(h["s"] for h in drums if h.get("part") == "kick")
    bass = [n for n in _sorted(parts.get("bass")) if not n.get("slide") and not n.get("glide")]
    nb = int(bars or 1)
    if bass:
        ons = sorted({round(n["s"], 3) for n in bass})
        if kick:
            d = [min(abs(o - k) for k in kick) for o in ons]
            out["flams"] = _peak(sum(1 for x in d if 0.2 < x < 1.0) / float(len(d)), 0.0, 0.05, 0.3)
            on_k = sum(1 for x in d if x <= 0.2) / float(len(d))
            if bass_lock == "offbeat":
                out["lock"] = _peak(sum(1 for x in d if x >= 1.0) / float(len(d)), 0.7, 1.0, 0.5)
            elif bass_lock == "free":
                pass
            elif bass_lock == "walk":
                out["lock"] = _peak(sum(1 for o in ons if abs(o / pulse - round(o / pulse)) < 0.05) / float(len(ons)),
                                    0.8, 1.0, 0.5)
            else:
                covered = sum(1 for k in kick if any(abs(k - o) <= 0.2 for o in ons) or
                              any(n["s"] <= k < n["s"] + n["l"] for n in bass)) / float(len(kick))
                out["lock"] = _peak(0.5 * on_k + 0.5 * covered, 0.7, 1.0, 0.5)
        if chords:
            ch = []
            for c in chords:
                near = [n for n in bass if abs(n["s"] - c["start"]) <= 1.0]
                want = c.get("bass") if c.get("bass") is not None else c["root"]
                ch.append(1.0 if any(n["k"] % 12 == want % 12 for n in near) else 0.3 if near else 0.0)
            out["changes"] = _mean(ch, 0.0)
    if drums:
        reps = []
        for part in ("kick", "snare", "clap"):
            hs = [h for h in drums if h.get("part") == part]
            if not hs:
                continue
            sg = [_sig(hs, b, spb) for b in range(nb)]
            for b in range(2, nb):
                if b % 4 == 3 or (b - 2) % 4 == 3:            # phrase-end bars may fill: not a groove change
                    continue
                j = _jacc(sg[b], sg[b - 2])
                if j is not None:
                    reps.append(1.0 if j >= 0.75 else j)
        if reps:
            out["drum_repeat"] = _peak(_mean(reps), 0.7, 1.0, 0.4)
    mel = parts.get("melody") or []
    if mel and bass:
        mk = sorted(n["k"] for n in mel)
        bk = sorted(n["k"] for n in bass)
        lo_m = mk[len(mk) // 10]
        hi_b = bk[min(len(bk) - 1, (9 * len(bk)) // 10)]
        out["register"] = _peak(lo_m - hi_b, 12, 200, 12)
    cm = parts.get("counter") or []
    if cm and mel:
        ans = sum(1 for n in cm if not any(m["s"] <= n["s"] + 0.25 < m["s"] + m["l"] for m in mel)) / float(len(cm))
        out["answer"] = _peak(ans, 0.6, 1.0, 0.5)
    return out


def fill_metric(drums, spb=16, bars=None, every=(4, 8)):
    """Phrase ends (every 4 / 8 bars) change the drums (a fill, a drop, an open hat)."""
    if not drums:
        return None
    nb = int(bars or 1)
    if nb < 4:
        return None
    half = spb / 2.0                                      # a fill lives in the bar's second half
    skins = [h for h in drums if h.get("part") not in ("hat", "ride", "shaker")]   # cymbals may keep time
    if not skins:                                         # hats / ride / shaker only: no fill to measure (not 0)
        return None
    allsig = [frozenset((h.get("part"), _q(h["s"] - b * spb)) for h in skins
                        if b * spb + half <= h["s"] < (b + 1) * spb) for b in range(nb)]
    ends = [b for b in range(nb) if (b + 1) % min(every) == 0 or (b + 1) % max(every) == 0]
    got = []
    for b in ends:
        ref = allsig[b - 2] if b >= 2 else allsig[0]
        j = _jacc(allsig[b], ref)
        got.append(1.0 if j is not None and j < 0.75 else 0.0)
    # at least one fill per 8 bars is the bar
    per8 = []
    for a in range(0, nb, 8):
        per8.append(max([g for b, g in zip(ends, got) if a <= b < a + 8] or [0.0]))
    return _mean(per8, 0.0)


# ----------------------------------------------------------------------------- sections (an arranged song)
def _hist(notes):
    w = [0.0] * 12
    for n in notes or []:
        w[n["k"] % 12] += n.get("l", 1)
    t = math.sqrt(sum(x * x for x in w)) or 1.0
    return [x / t for x in w]


def section_metric(sections, spb=16):
    """sections [{name, bars, parts {role: notes|hits}}] in song order: the hook is the fullest, verses sparser
    with their own (or no) melody, intro / outro thinner, a bridge contrasts (harmony or texture), the bar before a
    hook changes (fill, drop-out, build), more than one texture."""
    if not sections or len(sections) < 2:
        return None

    def dens(sec):
        n = sum(len(v or []) for v in (sec.get("parts") or {}).values())
        return n / float(max(1, sec.get("bars") or 1)), sum(1 for v in (sec.get("parts") or {}).values() if v)
    kind = lambda sec: str(sec.get("kind") or sec["name"]).lower().rstrip("0123456789 ")
    HOOK = ("hook", "chorus", "drop")
    ds = [(kind(s),) + dens(s) for s in sections]
    hooks = [i for i, d in enumerate(ds) if d[0] in HOOK]
    if not hooks:
        return None
    hd = max(ds[i][1] for i in hooks)
    hl = max(ds[i][2] for i in hooks)
    hs = sections[hooks[0]]["parts"]
    checks = []
    for i, (k, d, l) in enumerate(ds):
        p = sections[i].get("parts") or {}
        if k in ("verse",):
            checks.append(1.0 if d < hd * 0.92 or l < hl else 0.3)
            if p.get("melody") and hs.get("melody"):          # the verse sings something of its own
                a = {(round(n["s"] % (4 * spb), 1), n["k"]) for n in p["melody"] if n["s"] < 4 * spb}
                b = {(round(n["s"] % (4 * spb), 1), n["k"]) for n in hs["melody"] if n["s"] < 4 * spb}
                checks.append(1.0 if (_jacc(a, b) or 0) < 0.8 else 0.4)
        elif k in ("intro", "outro"):
            checks.append(1.0 if d < hd * 0.75 or l < hl else 0.2)
        elif k in ("bridge", "breakdown"):
            hh, bh = _hist(hs.get("chords") or hs.get("bass")), _hist(p.get("chords") or p.get("bass"))
            cos = sum(x * y for x, y in zip(hh, bh))
            hr, br = sections[hooks[0]].get("roots") or [], sections[i].get("roots") or []
            n_ = min(len(hr), len(br))
            moved = n_ and sum(1 for a, b in zip(hr[:n_], br[:n_]) if a != b) / float(n_) >= 0.5
            checks.append(1.0 if cos < 0.9 or moved else 0.7 if abs(d - hd) > hd * 0.25 or l < hl - 1 else 0.2)
        elif k in ("pre-hook", "prehook", "pre", "pre-chorus", "build", "buildup"):
            checks.append(1.0 if d != hd else 0.5)
    for i in hooks:                                           # the way into a hook: its last bar changes
        if i == 0:
            continue
        prev = sections[i - 1]
        dr = [h for h in (prev.get("parts") or {}).get("drums") or [] if h.get("part") not in ("hat", "ride", "shaker")]
        nb = int(prev.get("bars") or 1)
        if nb < 2:
            continue
        last = frozenset((h.get("part"), _q(h["s"] - (nb - 1) * spb)) for h in dr if h["s"] >= (nb - 1) * spb)
        ref = frozenset((h.get("part"), _q(h["s"] - (nb - 3 if nb >= 3 else 0) * spb)) for h in dr
                        if (nb - 3 if nb >= 3 else 0) * spb <= h["s"] < (nb - 2 if nb >= 3 else 1) * spb)
        j = _jacc(last, ref)
        checks.append(1.0 if j is None or j < 0.8 else 0.3)
    lay = len({(round(d, 1), l) for _, d, l in ds})            # not one texture all song long
    checks.append(1.0 if lay >= 3 else 0.6 if lay == 2 else 0.0)
    return _mean(checks, None)


# ----------------------------------------------------------------------------- the whole beat
def score_beat(parts, chords, spb=16, strong=(0, 8), bars=None, bass_lock="kick", pulse=4, sections=None,
               unit_bars=None, full=False, hook=True):
    """{score, hook, fit, groove, form, weak} for a beat / section: parts {drums, bass, chords, melody, counter}.
    hook=False: a section that isn't meant to carry the hook (intro, verse, outro: a teaser or no melody) is
    scored on fit / groove / form only."""
    mel = parts.get("melody") or []
    nb = bars
    if nb is None:
        ends = [n["s"] + n.get("l", 1) for v in parts.values() for n in (v or [])]
        nb = max(1, int(math.ceil(max(ends or [spb]) / spb - 1e-6)))
    mm = melody_metrics(mel, chords, spb, strong, nb, parts.get("chords"), unit_bars) if mel else {"empty": True}
    gm = groove_metrics(parts, chords, spb, nb, bass_lock, pulse)
    subs = {}
    if not mm.get("empty") and hook:
        subs["hook"] = {k: mm[k] for k in ("rhythm_repeat", "variation", "contour", "hook_return", "steps", "range",
                                           "shape", "space") if k in mm}
    if not mm.get("empty"):
        subs["fit"] = {k: mm[k] for k in ("chord_tones", "passing", "collisions") if k in mm}
    subs["groove"] = {k: gm[k] for k in ("lock", "flams", "changes", "drum_repeat", "register", "answer") if k in gm}
    form = {}
    if not mm.get("empty") and "cadence" in mm:
        form["cadence"] = mm["cadence"]
    st = structure_metric(mel, spb, nb) if mel and hook else None
    if st is not None:
        form["structure"] = st
    fm = fill_metric(parts.get("drums"), spb, nb)
    if fm is not None:
        form["fills"] = fm
    sm = section_metric(sections) if sections else None
    if sm is not None:
        form["sections"] = sm
    subs["form"] = form
    hw = {"rhythm_repeat": 1.4, "variation": 1.0, "contour": 0.8, "hook_return": 1.0, "steps": 1.2, "range": 0.6,
          "shape": 1.2, "space": 1.0}
    res = {}
    for k, d in subs.items():
        if not d:
            continue
        avg = sum(d[x] * hw.get(x, 1.0) for x in d) / sum(hw.get(x, 1.0) for x in d)
        low = sorted(d.values())[:2]                       # a part is only as good as its weakest spots
        res[k] = 0.6 * avg + 0.4 * _mean(low)
    if not res:
        return {"score": None, "note": "nothing to score (no melody, bass or drums)"}
    tot_w = sum(WEIGHTS[k] for k in res) or 1.0
    score = sum(res[k] * WEIGHTS[k] for k in res) / tot_w
    out = {"score": int(round(100 * score))}
    for k in ("hook", "fit", "groove", "form"):
        if k in res:
            out[k] = round(res[k], 2)
    flat = [(v, k) for d in subs.values() for k, v in d.items()]
    weak = [WORDS[k] for v, k in sorted(flat) if v < 0.6 and k in WORDS][:2]
    if weak:
        out["weak"] = weak
    if full:
        out["detail"] = {k: {x: round(y, 2) for x, y in d.items()} for k, d in subs.items() if d}
    return out


def melody_score(mel, chords, spb=16, strong=(0, 8), bars=None, chord_notes=None):
    """One number (0..1) for a lead line alone: hook, fit and form as score_beat weighs them (no groove)."""
    r = score_beat({"melody": mel, "chords": chord_notes or []}, chords, spb, strong, bars)
    w = {"hook": 0.45, "fit": 0.3, "form": 0.25}
    have = [k for k in w if k in r]
    return sum(r[k] * w[k] for k in have) / (sum(w[k] for k in have) or 1.0)


def compact(m):
    """The short form a result carries: {score, hook, fit, groove, form}."""
    return {k: m[k] for k in ("score", "hook", "fit", "groove", "form", "weak") if k in m}
