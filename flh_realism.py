"""fl-headless realism: make programmed support layers (strings, choir, brass, winds, timpani, pads...) sound like
people played them and someone recorded it. Programmed parts sound "computer made" because their dynamics are flat,
every note starts on the grid, lengths switch on and off, the articulation doesn't fit the passage and nobody
breathes. This module fixes those as DATA-DRIVEN rules per instrument family (data/realism/families.json) and per
library (data/realism/libraries.json), for any user's song, genre-aware (subtle for pop/EDM pads, rich for
orchestral, cinematic and piano-ballad songs).

1. Expression: per-phrase controller curves - a swell into long notes (attack ramp), a bow-like arc inside very long
   ones, a crescendo/diminuendo that follows the melody contour and the section's energy, a gentle decay at phrase
   ends, deterministic micro-variation. Written as automation clips (one per section and controller, named
   "<channel> dynamics - <section>" / "<channel> expression - <section>") on playlist tracks named "<channel>
   dynamics" / "<channel> expression". Targets: a sampled library's dynamics (CC1) / expression (CC11) through FL's
   VST wrapper (plugin parameter 4096 + CC; PC-VERIFY), or a plugin's own host parameter when its index is known.
   Channels with no controller to drive (FL synths, samplers) get the honest, weaker fallback: velocities carry the
   phrase shape and the channel's volume knob follows the curve, never above the level the user set.
2. Timing and lengths per family: late speaking attacks scaled by note length and register plus a section spread
   (strings), breaths that shorten the note before them (choir, winds, brass), tongued tight attacks and shorter
   phrase ends (brass), no drift but realistic rolls (timpani, mallets), legato overlaps where the library plays
   legato, a release before a repeated note, breath/bow limits that re-articulate impossible held notes, almost
   nothing for pads and synths. Deterministic (seeded by the song, the part and each note's own position).
3. Articulations: per section, longs / shorts / pizz / tremolo / swells from note length, density, repeats and
   energy. A plugin holds ONE articulation per channel (no per-note switching without keyswitches), so the tool sets
   it only when the whole part wants one that differs from the loaded one (Spitfire BBC / LABS / Splice INSTRUMENT,
   via flp_plugin_settings) and otherwise says which sections want what. Libraries with keyswitch data (user data;
   none shipped) get keyswitch notes (PC-VERIFY).
4. flp_realism(project, ...) picks the non-piano layers (roles from the journal / score view), applies 1-3 in ONE
   flp_edit batch (lazy-aware), stores what it did in the song journal (undo=True puts every note back and removes
   its clips; a re-run after note edits starts from the original notes and replaces its own clips by name, never
   anyone else's automation). ns["realism_apply"](notes, family, context) is the pure engine other modules call on
   the notes they write; ns["realism_expression_ops"](project, channel, expression, context) turns its expression
   spec into flp_edit ops; ns["realism_families"]() is the data.
5. Honest verification proxies per channel: controller movement inside notes, timing spread, note-length
   naturalness against the family's ranges - and the reminder that the listener decides (A/B with fl_ab).

Follows the extension contract in flh_store.py.
"""
import json
import math
import os
import re
import struct
import time
import zlib

H = {}
HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = {}
JKEY = "realism"                                   # song journal key owned by this module
SKIP_FAMILIES = ("keys", "drums", "vocal")
CONTROLS = ("dynamics", "expression")
NOTE_SLIDE = 0x08


# ============================================================================= data
def _merge(a, b):
    """Deep merge b into a. Lists of entries with an "id" (libraries) merge by id: the user's entry updates the
    shipped one with that id, new ones go first (so they match first)."""
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        elif isinstance(v, list) and isinstance(a.get(k), list) and v and all(isinstance(x, dict) and "id" in x
                                                                              for x in v):
            have = {x.get("id"): x for x in a[k] if isinstance(x, dict)}
            new = []
            for x in v:
                if x["id"] in have:
                    _merge(have[x["id"]], x)
                else:
                    new.append(x)
            a[k] = new + a[k]
        else:
            a[k] = v
    return a


def _user_dir():
    st = H.get("STATE")
    return os.path.join(os.path.dirname(st), "realism") if st else None


def data(name):
    """data/realism/<name>.json merged with the user's <state folder>/realism/<name>.json (cached on mtimes)."""
    base = os.path.join(HERE, "data", "realism", name + ".json")
    ud = _user_dir(); user = os.path.join(ud, name + ".json") if ud else None
    key = tuple(os.path.getmtime(p) if p and os.path.isfile(p) else 0 for p in (base, user))
    hit = _DATA.get(name)
    if hit and hit[0] == key:
        return hit[1]
    with open(base, encoding="utf-8") as f:
        d = json.load(f)
    if user and os.path.isfile(user):
        try:
            with open(user, encoding="utf-8") as f:
                _merge(d, json.load(f))
        except (OSError, ValueError) as e:                     # a broken user file never breaks the tool
            d["_user_error"] = f"{user}: {e}"
    _DATA[name] = (key, d)
    return d


def realism_families():
    """The family rules (data/realism/families.json merged with the user's override)."""
    return data("families")


def canon_family(name):
    """A family word ('woodwind', 'Strings', 'bells') -> this module's family name, or None for nothing."""
    f = str(name or "").strip().lower()
    if not f:
        return None
    F = data("families")
    if f in F["families"] or f in SKIP_FAMILIES:
        return f
    a = F.get("aliases", {}).get(f)
    if a:
        return a
    if f.endswith("s") and f[:-1] in F["families"]:
        return f[:-1]
    return "generic"


def family_cfg(fam):
    F = data("families")["families"]
    c = dict(F.get(fam) or F["generic"])
    for k in ("expression", "timing", "lengths", "velocity", "natural"):
        c[k] = dict(c.get(k) or {})
    c["name"] = fam if fam in F else "generic"
    return c


def amount_for(genre, family=None):
    """Default amount for a genre/style text (data rules) times the family's amount_scale."""
    A = data("families")["amount_by_genre"]; g = str(genre or "")
    val = next((float(v) for rx, v in A.get("rules", []) if g and re.search(rx, g, re.I)),
               float(A.get("default", 0.65)))
    if family:
        val *= float(family_cfg(canon_family(family) or "generic").get("amount_scale", 1.0))
    return max(float(A.get("min", 0.0)), min(float(A.get("max", 1.0)), val))


# ============================================================================= deterministic noise
def _u(*keys):
    """A stable value in [0, 1) from any keys (same keys -> same value, on every machine and run)."""
    return (zlib.crc32(("|".join(str(k) for k in keys)).encode("utf-8")) & 0xFFFFFFFF) / 4294967296.0


def _noise(seed, x_beats):
    """Smooth deterministic noise in about [-1, 1] over beats (three slow sines with seeded phases)."""
    out = 0.0
    for k, (T, a) in enumerate(((7.3, 0.5), (3.1, 0.3), (1.7, 0.2))):
        out += a * math.sin(2 * math.pi * (x_beats / T + _u(seed, "phase", k)))
    return out


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _lerp(a, b, t):
    return a + (b - a) * t


# ============================================================================= the pure engine
def _norm_notes(notes):
    items = []
    for i, n in enumerate(notes or []):
        if isinstance(n, dict):
            s = n.get("start", n.get("s")); e = n.get("end", n.get("e")); p = n.get("pitch", n.get("p", n.get("key")))
            v = n.get("vel", n.get("v", n.get("velocity", 100))); fixed = bool(n.get("slide") or n.get("fixed"))
        else:
            s, e, p, v = list(n)[:4]; fixed = False
        s = int(round(float(s))); e = int(round(float(e)))
        items.append({"i": i, "s": s, "e": max(e, s + 1), "p": int(p), "v": int(round(float(v))), "fixed": fixed,
                      "s0": s, "e0": max(e, s + 1), "v0": int(round(float(v)))})
    return items


def _sections_of(ctx, items):
    secs = []
    for k, s in enumerate(ctx.get("sections") or []):
        try:
            a, b = int(s["start"]), int(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a:
            secs.append({"name": str(s.get("name") or f"Section {k + 1}"), "start": a, "end": b,
                         **({"energy": float(s["energy"])} if s.get("energy") is not None else {})})
    secs.sort(key=lambda s: s["start"])
    if not secs:
        end = max((it["e"] for it in items), default=1)
        secs = [{"name": "Song", "start": 0, "end": end + 1}]
    return secs


def _sec_index(secs, t):
    k = 0
    for j, s in enumerate(secs):
        if s["start"] <= t:
            k = j
        else:
            break
    return k


def _energy(secs, items, others, P):
    """Section energy 0..1: density (all parts) and loudness, relative to the song's other sections."""
    have = [s.get("energy") for s in secs]
    if all(h is not None for h in have):
        return [_clamp(float(h), 0.0, 1.0) for h in have]
    dens, vel = [], []
    allnotes = [(it["s"], it["v"]) for it in items] + [(int(o[0]), int(o[3])) for o in (others or []) if len(o) >= 4]
    for s in secs:
        xs = [v for t, v in allnotes if s["start"] <= t < s["end"]]
        beats = max(1.0, (s["end"] - s["start"]) / P)
        dens.append(len(xs) / beats); vel.append(sum(xs) / len(xs) if xs else 0.0)

    def nz(xs):
        lo, hi = min(xs), max(xs)
        return [0.5 if hi - lo < 1e-9 else (x - lo) / (hi - lo) for x in xs]
    dn, vn = nz(dens), nz(vel)
    return [h if h is not None else 0.5 * a + 0.5 * b for h, a, b in zip(have, dn, vn)]


def _phrases(items, order, secs, P, ms_t, cfg_ph):
    """[[item index, ...]] in time order; a phrase ends at a rest >= gap or at a section boundary; very long phrases
    are split at their largest inner gap (or the onset nearest their middle)."""
    gap = max(float(cfg_ph.get("gap_beats", 0.75)) * P, float(cfg_ph.get("gap_ms", 220)) / ms_t)
    maxlen = float(cfg_ph.get("max_beats", 32)) * P
    out, cur, cur_end, cur_sec = [], [], None, None
    for i in order:
        it = items[i]; sec = _sec_index(secs, it["s"])
        if cur and (it["s"] - cur_end >= gap or sec != cur_sec):
            out.append(cur); cur = []
        if not cur:
            cur_end = it["e"]
        cur.append(i); cur_end = max(cur_end, it["e"]); cur_sec = sec
    if cur:
        out.append(cur)

    def split(ph):
        a = items[ph[0]]["s"]; b = max(items[i]["e"] for i in ph)
        if b - a <= maxlen or len(ph) < 4:
            return [ph]
        best, bk, run_end = None, None, items[ph[0]]["e"]
        mid = (a + b) / 2
        for k in range(1, len(ph)):
            s = items[ph[k]]["s"]
            if s > items[ph[k - 1]]["s"]:
                score = (s - run_end, -abs(s - mid))
                if best is None or score > best:
                    best, bk = score, k
            run_end = max(run_end, items[ph[k]]["e"])
        if bk is None:
            return [ph]
        return split(ph[:bk]) + split(ph[bk:])
    res = []
    for ph in out:
        res += split(ph)
    return res


def _groups(items, ph, tol):
    """Onset groups (chords) of a phrase: [[item index, ...]] by start."""
    gs = []
    for i in ph:
        if gs and items[i]["s"] - items[gs[-1][0]]["s"] <= tol:
            gs[-1].append(i)
        else:
            gs.append([i])
    return gs


def _arc(x, pk):
    """0 at the phrase start, 1 at its peak (pk), 0 at its end; smooth."""
    if x <= pk:
        return math.sin(math.pi / 2 * x / max(pk, 1e-6))
    return math.cos(math.pi / 2 * (x - pk) / max(1 - pk, 1e-6))


def _simplify(pts, tol=0.008):
    """Drop points that lie on the line between their neighbours (and duplicate ticks); keeps the shape."""
    pts = sorted(pts)
    ded = []
    for t, v in pts:
        if ded and t == ded[-1][0]:
            ded[-1] = (t, v)
        else:
            ded.append((t, v))
    if len(ded) <= 2:
        return ded
    out = [ded[0]]
    for k in range(1, len(ded) - 1):
        (t0, v0), (t1, v1), (t2, v2) = out[-1], ded[k], ded[k + 1]
        lin = v0 + (v2 - v0) * (t1 - t0) / max(1, t2 - t0)
        if abs(v1 - lin) >= tol:
            out.append(ded[k])
    out.append(ded[-1])
    return out


def curve_at(pts, t):
    """Value of a piecewise-linear curve [(tick, v)] at tick t (held before the first / after the last point)."""
    if not pts:
        return None
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def _mono(items, ph, P):
    """True when the phrase is a single line (notes barely overlap)."""
    xs = sorted(ph, key=lambda i: items[i]["s"])
    ov = sum(1 for a, b in zip(xs, xs[1:]) if items[a]["e0"] - items[b]["s0"] > P // 16)
    return ov <= max(0, len(xs) // 10)


def realism_apply(notes, family, context=None):
    """Make a programmed part sound performed. Pure: same input -> same output.
    notes: [{start, end, pitch, vel, ...}] (ticks at context ppq; extra keys pass through unchanged) or
    [[start, end, pitch, vel], ...]. family: strings | choir | brass | winds (woodwind) | timpani | mallets (bells) |
    pluck (guitar, harp) | pad | synth | lead | bass | generic; keys / drums / vocal come back untouched.
    context: {ppq, bpm, seed, genre, amount (0..1, default by genre), sections [{name, start, end(, energy)}] in
    ticks, part, others [[s, e, p, v]] (other parts, for section energy), legato (True when the library plays
    overlapping notes as legato, False/None: no overlaps), mode ("controller" default | "fallback": velocities carry
    the phrase shape | "notes": no curves)}.
    -> {"notes": [same shape as given, with start/end/vel changed; re-articulated long notes add a note carrying
    "realism_split_of": index], "expression": {"family", "mode", "clips": [{"section", "control", "points":
    [[tick, value 0..1], ...]}], "phrases": [[start, end], ...]} or None, "report": {...}}"""
    t0 = time.time()
    ctx = dict(context or {})
    fam = canon_family(family) or "generic"
    as_list = bool(notes) and not isinstance(notes[0], dict)
    if fam in SKIP_FAMILIES:
        return {"notes": [dict(n) if isinstance(n, dict) else list(n) for n in notes or []], "expression": None,
                "report": {"family": fam, "skipped": f"{fam}: left exactly as played"}}
    cfg = family_cfg(fam)
    P = int(ctx.get("ppq") or 96)
    try:
        bpm = float(ctx.get("bpm") or 120.0)
    except (TypeError, ValueError):
        bpm = 120.0
    if not 10.0 <= bpm <= 999.0:
        bpm = 120.0
    ms_t = 60000.0 / (bpm * P)                                      # ms per tick
    try:
        _bpb = float(ctx.get("beats_per_bar") or 4) if isinstance(ctx, dict) else 4.0   # quarter beats per bar
    except (TypeError, ValueError):
        _bpb = 4.0
    _bpb = _bpb if 0.5 <= _bpb <= 32 else 4.0
    T = lambda ms: ms / ms_t                                        # noqa: E731  ms -> ticks
    amount = ctx.get("amount")
    amount = amount_for(ctx.get("genre"), fam) if amount is None else _clamp(float(amount), 0.0, 1.0) * \
        float(cfg.get("amount_scale", 1.0))
    mode = ctx.get("mode") or "controller"
    seed = f"{ctx.get('seed', 0)}:{ctx.get('part') or fam}"
    items = _norm_notes(notes)
    rep = {"family": fam, "amount": round(amount, 3), "notes": len(items)}
    if not items:
        return {"notes": [], "expression": None, "report": rep}
    secs = _sections_of(ctx, items)
    order = sorted(range(len(items)), key=lambda i: (items[i]["s"], items[i]["p"], i))
    energy = _energy(secs, items, ctx.get("others"), P)
    phrases = _phrases(items, order, secs, P, ms_t, data("families").get("phrase", {}))
    X, Tm, L, V = cfg["expression"], cfg["timing"], cfg["lengths"], cfg["velocity"]
    F = data("families"); fb = F.get("fallback", {})
    vscale = float(fb.get("velocity_scale", 2.0)) if mode == "fallback" else 1.0
    tol = max(1, P // 16)
    clips = {}                                                       # (section, control) -> points
    swells = 0
    # ---- 1. velocities and expression curves, per phrase
    prev_end = None
    for ph in phrases:
        gs = _groups(items, ph, tol)
        ps = items[ph[0]]["s"]; pe = max(items[i]["e"] for i in ph); span = max(1, pe - ps)
        sec = _sec_index(secs, ps); E = energy[sec]
        pitches = [max(items[i]["p"] for i in g) for g in gs]
        mean_p = sum(pitches) / len(pitches)
        pk = 0.62
        if len(gs) > 2:
            top = max(range(len(gs)), key=lambda k: (pitches[k], -k))
            x = (items[gs[top][0]]["s"] - ps) / span
            if 0.2 <= x <= 0.85:
                pk = x
        for k, g in enumerate(gs):                                   # velocity phrasing
            x = (items[g[0]]["s"] - ps) / span
            for i in g:
                it = items[i]
                if it["fixed"]:
                    continue
                dv = (float(V.get("arc", 0)) * (_arc(x, pk) - 0.5) + (float(V.get("accent_first", 0)) if k == 0 else 0)
                      + float(V.get("contour", 0)) * _clamp((it["p"] - mean_p) / 12.0, -1, 1)
                      + float(V.get("spread", 0)) * (2 * _u(seed, "v", it["s0"], it["p"]) - 1))
                hi = 128 if it["v0"] >= 128 else 127
                it["v"] = int(round(_clamp(it["v0"] + amount * vscale * dv, 1, hi)))
        if not X.get("use") or mode == "notes":
            prev_end = pe
            continue
        vmean = sum(items[i]["v0"] for i in ph) / len(ph) / 127.0
        lo, hi = X.get("level", [0.4, 0.8])
        lvl = _lerp(float(lo), float(hi), _clamp(0.65 * E + 0.35 * vmean, 0, 1))
        fl, ce = float(X.get("floor", 0.1)), float(X.get("ceiling", 0.95))
        k_ = amount
        pts = []
        pre = ps - float(X.get("pre_beats", 0.25)) * P
        if prev_end is not None:
            pre = max(pre, prev_end + 1)
        pre = max(0, int(round(pre)))
        swd = float(X.get("swell_depth", 0.15))
        if pre < ps:
            pts.append((pre, lvl - k_ * swd))
        for k, g in enumerate(gs):
            gst = items[g[0]]["s"]; gen = max(items[i]["e"] for i in g)
            nxt = items[gs[k + 1][0]]["s"] if k + 1 < len(gs) else pe
            x = (gst - ps) / span
            base = (lvl + k_ * float(X.get("arc", 0.15)) * (_arc(x, pk) - 0.5)
                    + k_ * float(X.get("contour", 0.0)) * _clamp((pitches[k] - mean_p) / 12.0, -1, 1)
                    + k_ * float(X.get("micro", 0.0)) * _noise(seed, gst / P))
            glen = (gen - gst) / P
            room = nxt - gst                                         # until the next onset
            if glen >= float(X.get("swell_min_beats", 1.0)) and room > P // 4:
                swells += 1
                sb = X.get("swell_beats", [0.3, 1.5])
                sw = min(_clamp(glen * 0.3, float(sb[0]), float(sb[1])) * P, 0.5 * room)
                pts.append((gst, base - k_ * swd))
                pts.append((gst + sw, base))
                if glen * P >= 2.5 * sw and room >= 0.6 * glen * P and k + 1 < len(gs):
                    mid = gst + 0.55 * (gen - gst)
                    pts.append((mid, base + k_ * float(X.get("bow_arc", 0.05))))
                    pts.append((gen - min(0.25 * (gen - gst), P), base))
            else:
                pts.append((gst, base))
        lg = gs[-1]; lst = items[lg[0]]["s"]; ln_last = max(1, pe - lst)
        v_last = curve_at(_simplify(pts, 0.0), lst) if pts else lvl
        pts.append((pe - 0.5 * ln_last, v_last))
        pts.append((pe, v_last - k_ * float(X.get("end_decay", 0.2))))
        pts = [(int(round(t)), round(_clamp(v, fl, ce), 4)) for t, v in pts]
        ctl = "dynamics"
        clips.setdefault((sec, ctl), []).extend(_simplify(pts))
        el = X.get("expression_level", [0.82, 0.97])
        elv = _lerp(float(el[0]), float(el[1]), E)
        epts = [(pre, elv * (1 - 0.12 * k_)), (min(pe - 1, ps + min(span * 0.15, P)), elv),
                (max(ps + 1, pe - min(0.3 * ln_last, P)), elv),
                (pe, elv - k_ * float(X.get("expression_end_fade", 0.1)))]
        epts = [(int(round(t)), round(_clamp(v, 0.0, 1.0), 4)) for t, v in sorted(set(epts))]
        clips.setdefault((sec, "expression"), []).extend(_simplify(epts))
        prev_end = pe
    # ---- 2. timing
    by_pitch = {}
    for i in order:
        by_pitch.setdefault(items[i]["p"], []).append(i)
    rolls = set()
    if Tm.get("roll"):
        rio = T(float(Tm.get("roll_ioi_ms", 160)))
        for p, xs in by_pitch.items():
            run = [xs[0]] if xs else []
            for a, b in zip(xs, xs[1:]):
                if 0 < items[b]["s"] - items[a]["s"] <= rio:
                    run.append(b)
                else:
                    if len(run) >= 3:
                        rolls.update(run)
                        _roll_shape(items, run, seed, Tm, amount)
                    run = [b]
            if len(run) >= 3:
                rolls.update(run)
                _roll_shape(items, run, seed, Tm, amount)
    late_lo, late_hi = (float(x) for x in Tm.get("late_ms", [0, 0]))
    reg = float(Tm.get("register_ms_per_octave", 0)); spr = float(Tm.get("spread_ms", 0))
    mx = float(Tm.get("max_shift_ms", 20))
    late_applied = []
    nxt_same = {}
    for p, xs in by_pitch.items():
        for a, b in zip(xs, xs[1:]):
            nxt_same[a] = b
    prv_same = {b: a for a, b in nxt_same.items()}
    for i in order:
        it = items[i]
        if it["fixed"]:
            continue
        lb = (it["e0"] - it["s0"]) / P
        if i in rolls:
            off = float(Tm.get("roll_spread_ms", 2.0)) * (2 * _u(seed, "t", it["s0"], it["p"]) - 1); late = 0.0
        else:
            late = _lerp(late_lo, late_hi, _clamp(lb / 4.0, 0, 1)) + reg * max(0.0, (72 - it["p"]) / 12.0)
            off = late + spr * (2 * _u(seed, "t", it["s0"], it["p"]) - 1)
        off_ms = _clamp(amount * off, -mx, mx)
        dt = int(round(off_ms / ms_t))
        if dt > 0:
            dt = min(dt, int(0.3 * (it["e0"] - it["s0"])))
            if i in nxt_same:
                dt = min(dt, items[nxt_same[i]]["s0"] - it["s0"] - 1)
        elif dt < 0:
            floor_ = 0 if i not in prv_same else items[prv_same[i]]["s0"] + 1
            dt = max(dt, floor_ - it["s0"])
        dt = max(dt, -it["s0"])
        it["s"] = it["s0"] + dt
        if late > 0 and dt:
            late_applied.append(dt * ms_t)
    # ---- 3. lengths
    rr = T(float(L.get("release_restart_ms", 0)))
    legato_lib = ctx.get("legato") is True and bool(L.get("legato"))
    leg_used = touch_used = 0
    for ph in phrases:
        if len(ph) < 2 or not _mono(items, ph, P):
            continue
        xs = sorted(ph, key=lambda i: items[i]["s0"])
        for a, b in zip(xs, xs[1:]):
            A, B = items[a], items[b]
            if A["fixed"] or B["s0"] <= A["s0"]:
                continue
            gap = B["s0"] - A["e0"]
            if A["p"] == B["p"]:
                continue                                             # repeated note: the release rule below
            if legato_lib and gap <= float(L.get("legato_max_gap_beats", 0.25)) * P and amount >= 0.25:
                A["e"] = max(A["e"], B["s"] + int(round(T(float(L.get("legato_overlap_ms", 40))))))
                leg_used += 1
            elif 0 < gap <= float(L.get("touch_gap_beats", 0)) * P:
                A["e"] = max(A["e"], B["s"] - max(1, int(round(T(5)))))
                touch_used += 1
    released = 0
    if rr > 0:
        for p, xs in by_pitch.items():
            for a, b in zip(xs, xs[1:]):
                A, B = items[a], items[b]
                if A["fixed"] or B["s"] <= A["s"]:
                    continue
                if B["s"] - A["e"] < rr:
                    new_e = max(A["s"] + max(1, int(0.4 * (A["e0"] - A["s0"]))), int(B["s"] - rr))
                    if new_e < A["e"]:
                        A["e"] = new_e; released += 1
    breaths = []
    if L.get("breath"):
        need = T(float(L.get("breath_ms", 220)) * (0.6 + 0.4 * amount))

        def breathe(ph_items, before, why):
            last_end = max(items[i]["e"] for i in ph_items)
            if before - last_end >= need:
                return False
            hit = False
            for i in ph_items:
                it = items[i]
                if it["fixed"] or it["e"] <= before - need:
                    continue
                ne = max(it["s"] + max(1, int(0.5 * (it["e0"] - it["s0"]))), int(round(before - need)))
                if ne < it["e"]:
                    it["e"] = ne; hit = True
            if hit:
                breaths.append({"at": int(before), "why": why,
                                "notes": sorted(items[i]["i"] for i in ph_items if items[i]["e"] < items[i]["e0"])})
            return hit
        for a, b in zip(phrases, phrases[1:]):                     # phrase ends that run straight into the next
            breathe(a, items[b[0]]["s"], "phrase end")
        mb = L.get("max_breath_s")
        if mb:
            lim = T(float(mb) * 1000.0)
            for ph in phrases:
                gs = _groups(items, sorted(ph, key=lambda i: (items[i]["s"], i)), tol)
                start = items[gs[0][0]]["s"]
                k = 1
                while k < len(gs):
                    gstart = items[gs[k][0]]["s"]
                    if gstart - start <= lim:
                        k += 1
                        continue
                    lo_t = start + 0.45 * lim
                    cands = [j for j in range(1, k + 1) if lo_t <= items[gs[j][0]]["s"] <= start + lim] or [k]
                    bar = max(1, int(round(_bpb * P)))      # the context's bar (4/4 when not given)

                    def score(j):
                        prevg = gs[j - 1]; s_j = items[gs[j][0]]["s"]
                        plen = max(items[i]["e0"] - items[i]["s0"] for i in prevg)
                        leap = abs(max(items[i]["p"] for i in gs[j]) - max(items[i]["p"] for i in prevg))
                        on_bar = min(s_j % bar, bar - s_j % bar) <= tol
                        return (on_bar, plen, leap, -s_j)
                    j = max(cands, key=score)
                    breathe(gs[j - 1], items[gs[j][0]]["s"], "long phrase")
                    start = items[gs[j][0]]["s"]; k = j + 1
    shortened_ends = 0
    pes = float(L.get("phrase_end_shorten", 0.0)) * amount
    if pes > 0:
        for ph in phrases:
            pe = max(items[i]["e"] for i in ph)
            for i in ph:
                it = items[i]
                if not it["fixed"] and it["e"] >= pe - tol and it["e"] == it["e0"]:
                    it["e"] = max(it["s"] + 1, int(round(it["e"] - pes * (it["e0"] - it["s0"]))))
                    shortened_ends += 1
    splits = []
    ms_ = L.get("max_sustain_s")
    if ms_:
        lim = T(float(ms_) * 1000.0); rearti = T(float(L.get("rearticulate_ms", 150)))
        for i in order:
            it = items[i]
            if it["fixed"] or it["e"] - it["s"] <= lim:
                continue
            cur_s, parts = it["s"], []
            while it["e"] - cur_s > lim:
                cut = cur_s + max(P, int(lim // P) * P)               # re-articulate on a beat
                if it["e"] - cut < P:
                    break
                parts.append(cut); cur_s = cut
            if not parts:
                continue
            end0 = it["e"]
            it["e"] = max(it["s"] + 1, int(parts[0] - rearti))
            for k, c in enumerate(parts):
                e_ = int(parts[k + 1] - rearti) if k + 1 < len(parts) else end0
                splits.append({"src": i, "s": int(c), "e": max(int(c) + 1, e_),
                               "v": int(_clamp(it["v"] - 6 * (k + 1), 1, 127))})
    # ---- output
    out = []
    for it in items:
        n = notes[it["i"]]
        if as_list:
            o = list(n); o[0], o[1], o[3] = it["s"], max(it["e"], it["s"] + 1), it["v"]
        else:
            o = dict(n)
            sk = "start" if "start" in n or "s" not in n else "s"
            ek = "end" if "end" in n or "e" not in n else "e"
            vk = "vel" if "vel" in n or "v" not in n else "v"
            o[sk], o[ek], o[vk] = it["s"], max(it["e"], it["s"] + 1), it["v"]
        out.append(o)
    for sp in splits:
        n = notes[items[sp["src"]]["i"]]
        if as_list:
            o = list(n); o[0], o[1], o[3] = sp["s"], sp["e"], sp["v"]
        else:
            o = dict(n)
            o["start" if "start" in n or "s" not in n else "s"] = sp["s"]
            o["end" if "end" in n or "e" not in n else "e"] = sp["e"]
            o["vel" if "vel" in n or "v" not in n else "v"] = sp["v"]
            o["realism_split_of"] = items[sp["src"]]["i"]
        out.append(o)
    expr = None
    if clips:
        cl = []
        for (sec, ctl), pts in sorted(clips.items(), key=lambda kv: (kv[0][0], CONTROLS.index(kv[0][1]))):
            pts = _simplify(pts, 0.004)
            if len(pts) >= 2:
                cl.append({"section": secs[sec]["name"], "section_index": sec, "control": ctl,
                           "points": [[int(t), float(v)] for t, v in pts]})
        expr = {"family": fam, "mode": mode, "clips": cl,
                "phrases": [[items[ph[0]]["s"], max(items[i]["e"] for i in ph)] for ph in phrases],
                "sections": [{"name": s["name"], "start": s["start"], "end": s["end"]} for s in secs]}
    rep.update({"tick_ms": round(ms_t, 2), "phrases": len(phrases), "swells": swells, "breaths": len(breaths),
                "breath_notes": [n for b in breaths for n in b["notes"]][:200],
                "late_ms": [round(min(late_applied), 1), round(max(late_applied), 1)] if late_applied else None,
                "spread_ms": spr * amount, "legato": "overlap" if leg_used else ("touch" if touch_used else "off"),
                "legato_links": leg_used, "touch_links": touch_used, "released_repeats": released,
                "shortened_phrase_ends": shortened_ends, "rearticulated": len(splits), "roll_notes": len(rolls),
                "compute_ms": int((time.time() - t0) * 1000)})
    return {"notes": out, "expression": expr, "report": rep}


def _roll_shape(items, run, seed, Tm, amount):
    """A roll: hand-to-hand velocity, a gentle swell through it."""
    n = len(run)
    for k, i in enumerate(run):
        it = items[i]
        if it["fixed"]:
            continue
        hand = float(Tm.get("roll_hand_vel", 4)) * (1 if k % 2 == 0 else -1)
        swell = float(Tm.get("roll_swell_vel", 10)) * (k / max(1, n - 1) - 0.5)
        jit = 2.0 * (2 * _u(seed, "roll", it["s0"], it["p"]) - 1)
        hi = 128 if it["v0"] >= 128 else 127
        it["v"] = int(round(_clamp(it["v"] + amount * (hand + swell + jit), 1, hi)))


# ============================================================================= articulations
def suggest_articulations(notes, family, context=None):
    """Per section: which articulation the passage asks for (data rules) -> [{section, articulation, why}]."""
    ctx = dict(context or {})
    fam = canon_family(family) or "generic"
    items = _norm_notes(notes)
    if not items:
        return []
    P = int(ctx.get("ppq") or 96); bpm = float(ctx.get("bpm") or 120.0)
    if not 10.0 <= bpm <= 999.0:
        bpm = 120.0
    ms_t = 60000.0 / (bpm * P)
    secs = _sections_of(ctx, items); energy = _energy(secs, items, ctx.get("others"), P)
    A = data("families").get("articulations", {}); rep_ms = float(A.get("repeat_ms", 140))
    out = []
    for k, s in enumerate(secs):
        xs = sorted((it for it in items if s["start"] <= it["s0"] < s["end"]), key=lambda it: (it["s0"], it["p"]))
        if not xs:
            continue
        lens = sorted((it["e0"] - it["s0"]) * ms_t / 1000.0 for it in xs)
        med = lens[len(lens) // 2]
        dur = max(0.001, (max(it["e0"] for it in xs) - xs[0]["s0"]) * ms_t / 1000.0)
        dens = len({it["s0"] for it in xs}) / dur
        bp = {}
        for it in xs:
            bp.setdefault(it["p"], []).append(it["s0"])
        fast = sum(1 for v in bp.values() for a, b in zip(v, v[1:]) if 0 < (b - a) * ms_t <= rep_ms)
        rf = fast / max(1, len(xs))
        vel = sorted(it["v0"] for it in xs)[len(xs) // 2]
        facts = {"median_len_s": med, "density": dens, "repeat_fast": rf, "vel": vel, "energy": energy[k]}
        for rule in A.get("rules", []):
            if rule.get("families") and fam not in rule["families"]:
                continue
            w = rule.get("when", {}); ok = True
            for key, lim in w.items():
                name, _, side = key.rpartition("_")
                if name not in facts:
                    continue
                ok = ok and (facts[name] >= lim if side == "min" else facts[name] <= lim)
            if ok:
                out.append({"section": s["name"], "articulation": rule["name"], "notes": len(xs),
                            "why": f"median note {med:.2f} s, {dens:.1f} notes/s, repeats {rf:.0%}, vel {vel}, "
                                   f"energy {energy[k]:.2f}"})
                break
    return out


# ============================================================================= verification proxies
def measure(before, after, curves, family, P, bpm):
    """Honest proxies for one part: controller movement inside notes, timing spread, note-length naturalness.
    before/after: [(s, e, p, v)]; curves: {control: [(tick, v)]} (song ticks)."""
    cfg = family_cfg(canon_family(family) or "generic"); nat = cfg.get("natural", {})
    ms_t = 60000.0 / (bpm * P); out = {}
    grid = max(1, P // 4)

    def dev(xs):
        ds = [min(s % grid, grid - s % grid) * ms_t for s, *_ in xs]
        return round(sum(ds) / len(ds), 1) if ds else 0.0
    out["timing_ms_off_grid"] = {"before": dev(before), "after": dev(after)}
    lo, hi = nat.get("timing_spread_ms", [0, 999])
    out["timing_natural"] = lo <= out["timing_ms_off_grid"]["after"] <= hi or not before
    nl = nat.get("note_len_s", [0, 1e9])
    ok = sum(1 for s, e, *_ in after if nl[0] <= (e - s) * ms_t / 1000.0 <= nl[1])
    out["lengths_in_range"] = f"{ok}/{len(after)}" if after else "0/0"
    vb = [v for *_, v in before]; va = [v for *_, v in after]
    out["velocity_range"] = {"before": [min(vb), max(vb)] if vb else None, "after": [min(va), max(va)] if va else None}
    if curves:
        dyn = curves.get("dynamics") or curves.get("volume") or next(iter(curves.values()))
        longs = [(s, e) for s, e, *_ in after if e - s >= P]
        moves = []
        for s, e in longs:
            vals = [curve_at(dyn, s + (e - s) * k / 8.0) for k in range(9)]
            vals = [v for v in vals if v is not None]
            if vals:
                moves.append(max(vals) - min(vals))
        if moves:
            moves.sort()
            em = nat.get("expression_move", [0.03, 1.0])
            out["expression_inside_long_notes"] = {
                "long_notes": len(moves), "moving": f"{sum(1 for x in moves if x >= em[0]) * 100 // len(moves)}%",
                "median_move": round(moves[len(moves) // 2], 3)}
    return out


# ============================================================================= project side
def _store():
    import flh_store
    if not flh_store.H:
        flh_store.H = H
    return flh_store


def _journal(project):
    try:
        return _store().journal_get(project)
    except Exception:
        return {}


def _sync(project):
    """Edits queued for this project (lazy mode) go into the file first, so realism reads the song as it is."""
    try:
        if H.get("_pending_get") and H["_pending_get"](project):
            H["_sync_pending"](project)
    except Exception as e:
        raise RuntimeError(f"couldn't apply the edits queued for this project first ({e}); run flh_sync, then retry")


def _state_xml(st):
    k = st.find(b"VC2!")
    if k < 0:
        return None
    n = struct.unpack("<I", st[k + 4:k + 8])[0]
    return st[k + 8:k + 8 + n].decode("utf-8", "replace")


def _channels(d, ev):
    """{channel: {name, type, plugin, vst, volume (0..1 of FL's raw 0-12800), state xml or None}}."""
    pay = H["_payload"]; out = {}; cur = None
    for e in ev:
        i = e["id"]
        if i == H["EV_CHANNEL"]:
            cur = struct.unpack("<H", pay(d, e)[:2])[0]; out[cur] = {"name": None, "type": None, "volume": None}
        elif cur is None:
            continue
        elif i == H["EV_CHANNEL_END"]:
            cur = None
        elif i == 21 and e["len"] == 1:
            out[cur]["type"] = pay(d, e)[0]
        elif i == H["EV_DISPLAY_NAME"] and out[cur]["name"] is None:
            out[cur]["name"] = H["_text16"](pay(d, e))
        elif i == 219 and e["len"] >= 8 and out[cur]["volume"] is None:
            # channel levels: pan, volume (0-12800, FL's default 10000 = 78 %), ... PC-VERIFY: automation value =
            # raw / 12800 (checklists/realism.md)
            out[cur]["volume"] = struct.unpack("<i", pay(d, e)[4:8])[0] / 12800.0
    for ch in out:
        try:
            b, ks = H["_channel_block"](d, ev, ch)
            st = pay(d, ev[ks]) if ks is not None else b""
        except Exception:
            st = b""
        info = out[ch]
        try:
            info["vst"] = H["_wrapper_name"](st) if st else None
        except Exception:
            info["vst"] = None
        info["xml"] = _state_xml(st) if st else None
        if info["xml"]:
            mm = re.search(r"<META [^>]*>", info["xml"])
            if mm:
                for f in ("name", "family", "tags"):
                    v = re.search(f'{f}="([^"]*)"', mm.group(0))
                    if v:
                        info["preset_" + f] = v.group(1)
    return out


def _artics(xml):
    """(articulation names, index of the active one or None) from a Spitfire/INSTRUMENT state."""
    if not xml:
        return [], None
    arts = re.findall(r"<ARTIC>.*?</ARTIC>", xml, flags=re.S)
    names = [(re.search(r'a_name" value="([^"]*)"', a) or [None, ""])[1] for a in arts]
    act = next((k for k, a in enumerate(arts) if re.search(r'a_active" value="2"', a)), None)
    return names, act


def _defaultish(name, info):
    n = re.sub(r"\s*#\d+$", "", str(name or "")).strip().lower()
    return not n or n in {str(info.get("vst") or "").lower(), str(info.get("plugin") or "").lower(), "sampler",
                          "3x osc"} or re.match(r"^(channel|insert|generator)\s*\d*$", n)


def detect_family(texts, ins=None, role=None):
    """(family, why) from text (journal role, preset name / family / tags, channel name), then the score's instrument
    guess and role; (None, why) when nothing says."""
    D = data("families")["detect"]
    for t in texts:
        if not t:
            continue
        for rx, fam in D.get("rules", []):
            if re.search(rx, str(t), re.I):
                return fam, f"'{str(t)[:40]}'"
    if ins:
        f = D.get("instrument_id", {}).get(ins.get("id")) or D.get("instrument_family", {}).get(ins.get("family"))
        if f:
            return f, f"instrument {ins.get('id')}"
    if role and D.get("role", {}).get(role):
        return D["role"][role], f"role {role}"
    return None, "no family words in its name, sound or role"


def _library(info, fam):
    """The library entry that drives this channel (data/realism/libraries.json) or None."""
    Lb = data("libraries")
    text = " ".join(str(info.get(k) or "") for k in ("vst", "plugin", "preset_family", "preset_name"))
    for lib in Lb.get("libraries", []):
        if any(re.search(rx, text, re.I) for rx in lib.get("match", [])):
            if lib.get("families") and fam not in lib["families"]:
                continue
            return lib
    return None


def _targets(ch, info, fam, lib, params, cfg):
    """How this channel's expression is written: (mode, {control: (link param id, label)}, notes list)."""
    notes = []
    if not cfg["expression"].get("use"):
        return "notes", {}, notes
    Lb = data("libraries"); base = int(Lb.get("wrapper", {}).get("cc_param_base", 4096))
    is_vst = bool(info.get("vst")) or info.get("plugin") == "Fruity Wrapper"
    over = (params or {})
    if is_vst:
        got = {}
        for ctl in CONTROLS:
            idx = over.get(ctl)
            hp = ((lib or {}).get("host_params") or {}).get(ctl) or {}
            if idx is None and hp.get("index") is not None and hp.get("verified"):
                idx = hp["index"]
            if idx is not None:
                got[ctl] = (0x8000 | int(idx), f"host parameter {int(idx)}" +
                            (f" ({hp.get('name')})" if hp.get("name") else ""))
        if got:
            return "controller", got, notes
        ccs = (lib or {}).get("controllers")
        gv = Lb.get("generic_vst", {})
        if not ccs and fam in gv.get("families", []):
            ccs = gv.get("controllers"); notes.append("assumed the CC1 dynamics / CC11 expression convention "
                                                      "(unknown library)")
        if ccs:
            # PC-VERIFY: an automation clip linked to the channel's plugin parameter 4096 + n reaches the VST as MIDI
            # CC n (BBC SO / LABS dynamics + expression knobs move while the song plays; checklists/realism.md)
            return "controller", {ctl: (0x8000 | (base + int(cc)), f"CC{int(cc)}") for ctl, cc in ccs.items()
                                  if ctl in CONTROLS and cc is not None}, notes
    return "fallback", {"volume": (0, "channel volume")}, notes


def _song_notes(d, ev, pats, chans):
    """{channel: [note dicts]} as the playlist plays them, each with its source record (pattern, record index) and
    whether this is the record's first placement."""
    PB = H["PATTERN_BASE"]; CM = H.get("CLIP_MUTE", 0x2000)
    clips = sorted(H["_clips"](d, ev), key=lambda c: (struct.unpack("<I", c[2][0:4])[0], c[1]))
    seen = set(); out = {}
    for _, _, r in clips:
        item = struct.unpack("<H", r[6:8])[0]
        if item < PB or struct.unpack("<H", r[18:20])[0] & CM:
            continue
        pid = item - PB; pat = pats.get(pid)
        if not pat:
            continue
        pos = struct.unpack("<I", r[0:4])[0]; ln = struct.unpack("<I", r[8:12])[0]; s0 = H["_clip_offsets"](r)[0]
        for idx, n in enumerate(pat["notes"]):
            if chans is not None and n[2] not in chans:
                continue
            if s0 <= n[0] < s0 + ln:
                st = pos + n[0] - s0; L = max(1, min(n[3], s0 + ln - n[0]))
                src = (pid, idx)
                out.setdefault(n[2], []).append({"start": st, "end": st + L, "pitch": n[4], "vel": n[11],
                                                 "src": src, "first": src not in seen, "clip": (pos, pos + ln),
                                                 "slide": bool(n[1] & NOTE_SLIDE)})
                seen.add(src)
    return out


def _restore(pats, applied, chans):
    """Patterns with this module's earlier changes undone (value matching, so user edits survive) ->
    (patterns copy, stats {channel: {restored, kept_edited, missing}}, cur {(pid, ch): [the record now in the file
    for each restored channel record, in order]}, dropped {(pid, ch): [(record now in the file, song tick)]} = the
    re-articulation notes it had added)."""
    stats, cur_of, dropped = {}, {}, {}
    pats = {pid: {"notes": list(v["notes"]), "ctrl": v["ctrl"], "name": v["name"]} for pid, v in pats.items()}
    for ch in chans:
        a = (applied or {}).get(str(ch)) or {}
        st = {"restored": 0, "kept_edited": 0, "missing": 0}
        for pid, v in pats.items():
            rows = (a.get("notes") or {}).get(str(pid)) or []
            addl = (a.get("added") or {}).get(str(pid)) or []
            back = {}
            for row in rows:                                         # [opos, olen, key, ovel, npos, nlen, nvel]
                back.setdefault((row[4], row[5], row[2], row[6]), []).append((row[0], row[1], row[3]))
            added = {}
            for row in addl:                                         # [pos, len, key, vel(, song tick)]
                added.setdefault((row[0], row[1], row[2], row[3]), []).append(row[4] if len(row) > 4 else None)
            new, cur = [], []
            for r in v["notes"]:
                if r[2] != ch:
                    new.append(r); continue
                k = (r[0], r[3], r[4], r[11])
                if added.get(k):
                    dropped.setdefault((pid, ch), []).append((r, added[k].pop(0))); continue
                if back.get(k):
                    o = back[k].pop(0); rr = list(r); rr[0], rr[3], rr[11] = o[0], o[1], o[2]
                    new.append(tuple(rr)); cur.append(r); st["restored"] += 1
                else:
                    new.append(r); cur.append(r)
                    if rows:
                        st["kept_edited"] += 1
            st["missing"] += sum(len(x) for x in back.values())
            v["notes"] = new
            if cur:
                cur_of[(pid, ch)] = cur
        if not a:
            st["kept_edited"] = 0
        stats[ch] = st
    return pats, stats, cur_of, dropped


def _recs_digest(recs):
    return zlib.crc32(repr(sorted(tuple(r) for r in recs)).encode()) & 0xFFFFFFFF


def _bpm_of(model):
    tm = model.get("tempos") or [(0, 120.0)]
    vals = [b for _, b in tm if 10 <= b <= 999]
    return sorted(vals)[len(vals) // 2] if vals else 120.0


def _model(project):
    f = H.get("score_model")
    if not callable(f):
        raise RuntimeError("flp_realism needs flh_score (roles, sections, genre) - it isn't loaded")
    return f(project)


def _pick_channels(model, chinfo, journal, channels, families):
    """[(channel, family, why)] to perform + [skipped {channel, name, why}]."""
    parts = model.get("parts") or {}
    jch = journal.get("channels") or {}
    fams = {str(k): v for k, v in (families or {}).items()}
    want = None
    if channels not in (None, "auto", "all"):
        want = set()
        for c in (channels if isinstance(channels, (list, tuple)) else [channels]):
            if isinstance(c, int) or str(c).strip().isdigit():
                want.add(int(c))
            else:
                hit = [k for k, v in chinfo.items() if (v.get("name") or "").lower() == str(c).lower()] or \
                      [k for k, v in chinfo.items() if str(c).lower() in (v.get("name") or "").lower()]
                if not hit:
                    raise ValueError(f"no channel named '{c}'")
                want.update(hit)
    pick, skip = [], []
    sk = data("families")["skip"]
    for ch in sorted(parts):
        p = parts[ch]; info = chinfo.get(ch, {})
        name = info.get("name") or p.get("name") or f"Channel {ch}"
        if want is not None and ch not in want:
            continue
        jr = jch.get(str(ch)) or {}
        fam = canon_family(fams.get(str(ch)) or fams.get(name)) if (fams.get(str(ch)) or fams.get(name)) else None
        why = "you said so" if fam else None
        if not fam and (jr.get("family") or jr.get("role")):
            fam, why = detect_family([jr.get("family"), jr.get("role")])
            fam = canon_family(fam) if fam else None
            why = f"journal {why}" if fam else None
        if not fam:
            texts = [info.get("preset_name"), info.get("preset_family"), info.get("preset_tags")]
            if not _defaultish(name, {"vst": info.get("vst"), "plugin": p.get("plugin")}):
                texts.append(name)
            fam, why = detect_family(texts, p.get("_ins"), p.get("role"))
            fam = canon_family(fam) if fam else None
        if p.get("role") == "drums" and fam not in ("timpani", "mallets"):
            fam, why = "drums", "drum part"
        if not fam:
            fam = "generic"
        if fam in sk.get("families", []):
            skip.append({"channel": ch, "name": name, "why": f"{fam} ({why}): left exactly as played"})
            continue
        if want is None and (fam in sk.get("auto_skip", []) or fam == "generic"):
            skip.append({"channel": ch, "name": name, "why": f"{fam} ({why}): not a support layer - name it in "
                                                             "channels=[...] to perform it anyway"})
            continue
        pick.append((ch, fam, why))
    return pick, skip


def _section_filter(model, sections):
    """[(start tick, end tick)] for section names / 'a-b' bar ranges, or None for the whole song."""
    if not sections:
        return None
    secs = model.get("sections") or []; bar_t = model.get("bar_t") or 4 * model["P"]
    out = []
    for w in (sections if isinstance(sections, (list, tuple)) else [sections]):
        m_ = re.match(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$", str(w))
        if m_:
            out.append((int((float(m_.group(1)) - 1) * bar_t), int((float(m_.group(2)) - 1) * bar_t))); continue
        hit = [s for s in secs if s["name"].lower() == str(w).lower()]
        if not hit:
            raise ValueError(f"no section '{w}' (have: {[s['name'] for s in secs]})")
        out += [(int(s["a"]), int(s["b"])) for s in hit]
    return out


def _volume_points(info, cfg, pts):
    """Fallback: the dynamics curve as the channel's volume knob, never above the level the user set."""
    fb = data("families").get("fallback", {}); depth = float(fb.get("volume_depth", 0.35))
    basev = info.get("volume") if info.get("volume") is not None else 0.78125   # FL's default channel volume
    X = cfg["expression"]; lo, hi = float(X.get("floor", 0.1)), float(X.get("ceiling", 0.95))
    return [[t, round(basev * (1 - depth + depth * _clamp((v - lo) / max(1e-6, hi - lo), 0, 1)), 4)] for t, v in pts]


def _labels(chname, ctl, secname, used):
    word = {"dynamics": "dynamics", "expression": "expression", "volume": "expression"}[ctl]
    lab = f"{chname} {word} - {secname}"
    k = 2; base = lab
    while lab in used:
        lab = f"{base} ({k})"; k += 1
    used.add(lab)
    return lab


def _track_name(chname, ctl):
    return f"{chname} {'dynamics' if ctl == 'dynamics' else 'expression'}"


def _existing_links(d, ev):
    """{automation channel: (param id, destination)}."""
    out = {}
    for e in ev:
        if e["id"] == H["EV_REMOTE_LINK"] and e["len"] >= 12:
            p = H["_payload"](d, e)
            out[struct.unpack("<H", p[2:4])[0]] = struct.unpack("<HH", p[8:12])
    return out


def _plan(project, channels="auto", sections=None, amount=None, articulation="auto", families=None, params=None,
          genre=None, seed=0, undo=False):
    """Read the project and work out everything flp_realism would write (nothing is written)."""
    T0 = time.time(); steps = {}
    d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
    model = _model(project); steps["model"] = round(time.time() - T0, 3)
    journal = _journal(project); jr = journal.get(JKEY) or {}
    applied = jr.get("applied") or {}
    chinfo = _channels(d, ev)
    for ch, p in (model.get("parts") or {}).items():
        chinfo.setdefault(ch, {}).setdefault("plugin", p.get("plugin"))
    gtext = genre or model.get("genre") or ""
    bpm = _bpm_of(model)
    if undo:
        pick = [(int(c), a.get("family", "generic"), "undo") for c, a in applied.items()]
        skip = []
        have = {c for c, _, _ in pick}                             # + channels whose expression clips came from
        lk0 = _existing_links(d, ev)                              # realism_expression_ops (flh_compose layers,
        for ac, (kind, nm) in H["_clip_names"](d, ev).items():    # flh_perform): named like ours, linked to them
            dest = lk0.get(ac, (None, None))[1]
            if kind != "automation" or dest is None or int(dest) in have:
                continue
            cname = (chinfo.get(int(dest)) or {}).get("name") or f"Channel {dest}"
            if re.match(r"^" + re.escape(cname) + r" (dynamics|expression) - ", nm or ""):
                pick.append((int(dest), "generic", "undo")); have.add(int(dest))
    else:
        pick, skip = _pick_channels(model, chinfo, journal, channels, families)
    chans = {c for c, _, _ in pick}                                # channels not picked keep what they have
    pats0 = H["_patterns"](d, ev)
    pats, restore_stats, cur_of, dropped = _restore(pats0, applied, chans)
    steps["read"] = round(time.time() - T0, 3)
    song = _song_notes(d, ev, pats, None)
    secs_m = model.get("sections") or []
    sections_t = [{"name": s["name"], "start": int(s["a"]), "end": int(s["b"])} for s in secs_m]
    only = _section_filter(model, sections)

    def in_only(t):
        return only is None or any(a <= t < b for a, b in only)
    names = {c: (chinfo.get(c, {}).get("name") or f"Channel {c}") for c in chans}
    clipnames = H["_clip_names"](d, ev); links = _existing_links(d, ev)
    plan = {"notes": [], "automation": [], "own": [], "remove": [], "artic": [], "journal": {}, "channels": [],
            "skipped": skip, "bpm": bpm, "ppq": P}
    profile_params = {}
    try:
        profile_params = (_store().profile_get() or {}).get("realism_params") or {}
    except Exception:
        pass
    new_applied = {} if undo else {k: v for k, v in applied.items() if int(k) not in chans}
    all_own_labels = {lab for a in applied.values() for lab in (a.get("clips") or [])}

    def own_of(ch):
        """This module's clips for channel ch: the journal's, plus clips named like ours that drive this channel
        (written by realism_expression_ops for flh_perform / flh_compose)."""
        rx = re.compile(r"^" + re.escape(names[ch]) + r" (dynamics|expression) - ")
        got = set((applied.get(str(ch)) or {}).get("clips") or [])
        for ac, (kind, nm) in clipnames.items():
            if kind == "automation" and links.get(ac, (None, None))[1] == ch and rx.match(nm or ""):
                got.add(nm)
        return got
    for ch in chans:
        all_own_labels |= own_of(ch)
    for ch in sorted(chans):
        fam = next((f for c, f, _ in pick if c == ch), None)
        why = next((w for c, _, w in pick if c == ch), None)
        old = applied.get(str(ch)) or {}
        mine = song.get(ch, [])
        cfg = family_cfg(fam or "generic"); info = chinfo.get(ch, {})
        lib = _library(info, fam) if fam else None
        others = [[n["start"], n["end"], n["pitch"], n["vel"]] for c2, ns in song.items() if c2 != ch for n in ns]
        res = None
        if fam and not undo:
            pr = dict(profile_params.get((lib or {}).get("id") or "", {}))
            pr.update((params or {}).get(str(ch)) or (params or {}).get(names[ch]) or {})
            mode, targets, tnotes = _targets(ch, info, fam, lib, pr, cfg)
            conflict = []
            for ctl, (pid_, lab_) in list(targets.items()):          # someone else already automates it: hands off
                for ac, (lp, ld) in links.items():
                    nm = clipnames.get(ac, (None, ""))[1]
                    if ld == ch and lp == pid_ and nm not in all_own_labels:
                        conflict.append(f"{lab_} is already automated by '{nm}' (left alone)")
                        targets.pop(ctl, None); break
            if mode != "notes" and not targets:
                mode = "fallback" if not conflict else "notes"
            if mode == "fallback" and not data("families").get("fallback", {}).get("volume", True):
                targets = {}
            ctx = {"ppq": P, "bpm": bpm, "seed": seed, "genre": gtext, "amount": amount, "sections": sections_t,
                   "beats_per_bar": model.get("bpb") or 4,
                   "part": names[ch], "others": others, "mode": mode,
                   "legato": bool(lib and lib.get("legato") and _legato_loaded(info, lib))}
            res = realism_apply([dict(n, fixed=n["slide"]) for n in mine], fam, ctx)
        # ---- notes: new records per (pattern, channel)
        by_pid = {}
        for pid, v in pats.items():
            rs = [r for r in v["notes"] if r[2] == ch]
            if rs or any(r[2] == ch for r in pats0.get(pid, {"notes": []})["notes"]):
                by_pid[pid] = rs
        if not mine and not by_pid and not undo:
            continue
        if only is None or undo:
            new_recs = {pid: [list(r) for r in rs] for pid, rs in by_pid.items()}
        else:                                                      # outside the chosen sections: as it is now
            new_recs = {pid: [list(r) for r in cur_of.get((pid, ch), rs)] for pid, rs in by_pid.items()}
        jnotes, jadded = {}, {}
        if only is not None and not undo:
            for pid in by_pid:
                for r, tick in dropped.get((pid, ch), []):
                    if tick is not None and not in_only(tick):
                        new_recs[pid].append(list(r))
                        jadded.setdefault(str(pid), []).append([r[0], r[3], r[4], r[11], tick])
        changed = 0
        if res:
            outn = res["notes"]
            for k, n in enumerate(outn):
                src_i = n.get("realism_split_of")
                base = mine[src_i] if src_i is not None else (mine[k] if k < len(mine) else None)
                if base is None or not base["first"] or not in_only(base["start"]):
                    continue
                pid, idx = base["src"]
                rec = pats[pid]["notes"][idx]
                ds, de = n["start"] - base["start"], n["end"] - base["end"]
                npos = rec[0] + ds
                cs, ce_ = base["clip"]
                if not (cs <= n["start"] < ce_) or npos < 0:
                    ds = 0; npos = rec[0]
                if src_i is None:
                    nlen = max(1, rec[3] + de - ds)
                    j = _rec_index(by_pid[pid], rec, idx, pats[pid]["notes"])
                    if j is None:
                        continue
                    new_recs[pid][j][0] = npos; new_recs[pid][j][3] = nlen; new_recs[pid][j][11] = int(n["vel"])
                    if (npos, nlen, int(n["vel"])) != (rec[0], rec[3], rec[11]):
                        changed += 1
                else:
                    r2 = list(rec); r2[0] = rec[0] + (n["start"] - base["start"]); r2[3] = max(1, n["end"] - n["start"])
                    r2[11] = int(n["vel"]); new_recs[pid].append(r2)
                    jadded.setdefault(str(pid), []).append([r2[0], r2[3], r2[4], r2[11], int(n["start"])])
                    changed += 1
        # journal rows: original -> written (for every record of this channel we may have changed)
        for pid, rs in by_pid.items():
            rows = []
            for j, r in enumerate(rs):
                w = new_recs[pid][j]
                if (w[0], w[3], w[11]) != (r[0], r[3], r[11]):
                    rows.append([r[0], r[3], r[4], r[11], w[0], w[3], w[11]])
            if rows:
                jnotes[str(pid)] = rows
        for pid, rs in new_recs.items():
            cur = [r for r in pats0.get(pid, {"notes": []})["notes"] if r[2] == ch]
            if sorted(tuple(r) for r in rs) != sorted(tuple(r) for r in cur):
                plan["notes"].append({"pattern": pid, "channel": ch, "records": rs, "expect": _recs_digest(cur)})
        # ---- automation clips
        labels = []
        if res and res.get("expression"):
            used = set()
            ex = res["expression"]
            for cl in ex["clips"]:
                if not any(a <= cl["points"][0][0] < b for a, b in (only or [(0, 1 << 40)])):
                    continue
                ctl = cl["control"]
                tg = targets.get(ctl)
                if tg is None and mode == "fallback" and ctl == "dynamics" and "volume" in targets:
                    ctl, tg = "volume", targets["volume"]
                if tg is None:
                    continue
                pts = _volume_points(info, cfg, cl["points"]) if ctl == "volume" else cl["points"]
                lab = _labels(names[ch], ctl, cl["section"], used)
                plan["automation"].append({"label": lab, "pid": tg[0], "dest": ch,
                                           "track_name": _track_name(names[ch], ctl),
                                           "points": [[round(t / P, 6), v] for t, v in pts]})
                labels.append(lab)
        own_ch = sorted(own_of(ch))
        keep_old = [lab for lab in own_ch if only is not None and lab not in labels and
                    not _label_in_sections(lab, names[ch], sections_t, only)]
        plan["own"] += own_ch
        for lab in own_ch:
            if lab not in labels and lab not in keep_old:
                plan["remove"].append(lab)
        # ---- articulation
        art = None
        if res and fam and not undo:
            art = _articulation_plan(ch, info, fam, lib, mine, {"ppq": P, "bpm": bpm, "sections": sections_t,
                                                                 "others": others}, articulation)
            if art.get("set"):
                plan["artic"].append({"tool": "flp_plugin_settings", "channel": ch, "articulation": art["set"]})
            for ksw in art.get("keyswitches") or []:              # PC-VERIFY: the library switches on these notes
                first = min((n for n in mine if n["first"] and ksw["start"] <= n["start"] < ksw["end"]),
                            key=lambda n: n["start"], default=None)
                if first is None:
                    continue
                pid, idx = first["src"]; rec = pats[pid]["notes"][idx]
                lead = max(1, P // 8)
                lo_pos = rec[0] - (first["start"] - first["clip"][0])      # the clip's first pattern tick
                r2 = list(rec); r2[0] = max(lo_pos, rec[0] - lead, 0); r2[3] = max(1, P // 16)
                r2[4] = int(ksw["note"]); r2[11] = 100
                r2[1] = int(r2[1]) & ~NOTE_SLIDE
                if pid in new_recs:
                    new_recs[pid].append(r2)
                    jadded.setdefault(str(pid), []).append([r2[0], r2[3], r2[4], r2[11], int(first["start"])])
                    cur = [r for r in pats0.get(pid, {"notes": []})["notes"] if r[2] == ch]
                    for it in plan["notes"]:
                        if it["pattern"] == pid and it["channel"] == ch:
                            it["records"] = new_recs[pid]; break
                    else:
                        plan["notes"].append({"pattern": pid, "channel": ch, "records": new_recs[pid],
                                              "expect": _recs_digest(cur)})
        elif undo and (old.get("artic") or {}).get("before"):
            if (old["artic"].get("after") or "") != old["artic"]["before"]:
                plan["artic"].append({"tool": "flp_plugin_settings", "channel": ch,
                                      "articulation": old["artic"]["before"]})
        if res and not undo:
            ent = {"family": fam, "name": names[ch], "mode": mode, "notes": jnotes, "added": jadded,
                   "clips": labels + keep_old, "amount": res["report"]["amount"],
                   **({"artic": {"before": (old.get("artic") or {}).get("before") or art.get("before"),
                                 "after": art.get("set") or art.get("before")}}
                      if art and art.get("before") is not None else {})}
            new_applied[str(ch)] = ent
            before = [(n["start"], n["end"], n["pitch"], n["vel"]) for n in mine]
            after = [(n["start"], n["end"], n["pitch"], n["vel"]) for n in res["notes"]]
            curves = {}
            for a in plan["automation"]:
                if a["dest"] == ch:
                    key = "volume" if a["pid"] == 0 else ("dynamics" if " dynamics - " in a["label"] else "expression")
                    curves.setdefault(key, []).extend((int(round(b * P)), v) for b, v in a["points"])
            curves = {k: sorted(v) for k, v in curves.items()}
            plan["channels"].append({"channel": ch, "name": names[ch], "family": fam, "why": why, "mode": mode,
                                     "targets": {k: v[1] for k, v in targets.items()},
                                     "target_notes": tnotes + conflict,
                                     "report": res["report"], "changed_notes": changed, "clips": labels,
                                     "articulation": art, "library": (lib or {}).get("name"),
                                     "restored": restore_stats.get(ch),
                                     "verify": measure(before, after, curves, fam, P, bpm)})
        elif undo:
            plan["channels"].append({"channel": ch, "name": names[ch], "undo": True, "restored": restore_stats.get(ch),
                                     "clips_removed": own_ch})
    plan["journal"] = new_applied
    plan["automation_same"] = _clips_same(d, ev, plan["automation"], set(plan["own"]))
    plan["steps_s"] = {**steps, "plan": round(time.time() - T0, 3)}
    plan["sections"] = sections_t
    plan["genre"] = gtext
    return plan


def _clips_same(d, ev, autos, own):
    """True when every planned clip already exists (own label) with the same target, position and points."""
    if not autos:
        return True
    P = H["_ppq"](d); names = H["_clip_names"](d, ev); links = _existing_links(d, ev)
    autos_ev = H["_automation_events"](d, ev)
    pos = {}
    for _, _, r in H["_clips"](d, ev):
        pos.setdefault(struct.unpack("<H", r[6:8])[0], []).append((struct.unpack("<I", r[0:4])[0],
                                                                   struct.unpack("<I", r[8:12])[0]))
    for a in autos:
        cs = [c for c, (kind, nm) in names.items() if kind == "automation" and nm == a["label"] and nm in own]
        if len(cs) != 1 or links.get(cs[0]) != (a["pid"], a["dest"]) or cs[0] not in autos_ev:
            return False
        pts = H["_auto_points"](H["_payload"](d, ev[autos_ev[cs[0]]]))[0]
        want = sorted((float(b), float(v)) for b, v in a["points"])
        if len(pts) != len(want):
            return False
        b0 = want[0][0]
        if any(abs(p_[0] - (b - b0)) > 1e-6 or abs(p_[1] - v) > 1e-6 for p_, (b, v) in zip(pts, want)):
            return False
        if pos.get(cs[0]) != [(int(round(b0 * P)), int(round((want[-1][0] - b0) * P)))]:
            return False
    return True


def _label_in_sections(lab, chname, sections_t, only):
    for s in sections_t:
        if lab.startswith(f"{chname} ") and (lab.endswith(f" - {s['name']}") or f" - {s['name']} (" in lab):
            if any(a <= s["start"] < b for a, b in only):
                return True
    return False


def _rec_index(rs, rec, idx, allrecs):
    """Index of record `rec` (pattern record index idx) within this channel's record list rs."""
    k = 0
    for j, r in enumerate(allrecs):
        if r[2] == rec[2]:
            if j == idx:
                return k
            k += 1
    return None


def _legato_loaded(info, lib):
    """True when the library plays legato AND the loaded articulation is a legato one (or unknown: library default)."""
    names, act = _artics(info.get("xml"))
    if not names or act is None:
        return bool(lib.get("legato"))
    return lib.get("legato_word", "legato").lower() in names[act].lower()


def _art_category(name, lib):
    for cat, words in ((lib or {}).get("articulations") or {}).items():
        if any(w.lower() in str(name or "").lower() for w in words):
            return cat
    return None


def _articulation_plan(ch, info, fam, lib, mine, ctx, mode):
    """Suggestions per section; 'set' when the whole part wants one articulation the loaded one isn't."""
    sug = suggest_articulations([{"start": n["start"], "end": n["end"], "pitch": n["pitch"], "vel": n["vel"]}
                                 for n in mine], fam, ctx)
    names, act = _artics(info.get("xml"))
    out = {"sections": [{"section": s["section"], "wants": s["articulation"]} for s in sug][:16],
           "before": names[act] if names and act is not None else None}
    if not sug:
        return out
    weights = {}
    for s in sug:
        weights[s["articulation"]] = weights.get(s["articulation"], 0) + s["notes"]
    top = max(weights, key=weights.get); share = weights[top] / max(1, sum(weights.values()))
    out["wants"] = top
    if mode in ("off", False, None) or not names or not lib or not lib.get("articulations"):
        if names and not lib:
            out["note"] = "unknown library: articulation left as loaded"
        return out
    cur_cat = _art_category(out["before"], lib)
    words = (lib.get("articulations") or {}).get(top) or []
    avail = next((n for w in words for n in names if w.lower() in n.lower()), None)
    if len(weights) > 1:
        secs = ", ".join(f"{s['section']}: {s['articulation']}" for s in sug[:6])
        out["note"] = (f"sections want different articulations ({secs}); a channel holds one, so: clone the channel "
                       f"(flp_add_channel clone_from={ch}), set the other articulation on the copy "
                       "(flp_plugin_settings "
                       "articulation=...) and move those sections' notes to it (flp_replace_part), or use a "
                       "keyswitch patch (libraries.json keyswitches)")
    if mode == "suggest":
        return out
    ks = lib.get("keyswitches") or {}
    if ks and any(s["articulation"] in ks for s in sug):          # the library switches by note: one per section
        secs = {x["name"]: x for x in ctx.get("sections") or []}
        out["keyswitches"] = [{"section": s["section"], "articulation": s["articulation"],
                               "note": int(ks[s["articulation"]]), "start": secs[s["section"]]["start"],
                               "end": secs[s["section"]]["end"]}
                              for s in sug if s["articulation"] in ks and s["section"] in secs]
        out.pop("note", None)
        return out
    if share >= 0.8 and cur_cat != top and avail and avail != out["before"]:
        out["set"] = avail
    elif cur_cat != top and not avail:
        out.setdefault("note", f"the part wants '{top}' but this sound has no such articulation ({names[:6]})")
    return out


# ============================================================================= the writer (an flp_edit op)
def _track_events(d, ev):
    """{playlist track number: (event index of 238, name or None)} in the current arrangement (FL's 238 43 239
    groups: H['_track_groups'])."""
    return {t: (g["k"], g["name"]) for t, g in H["_track_groups"](d, ev).items()}


def _name_track(d, track, name):
    """Give playlist track `track` (current arrangement) a name: its 239 replaced, else one at the end of its group
    (after FL's 43, as FL writes it)."""
    # PC-VERIFY: FL shows the name on that playlist track (same layout as flh_newsong._style_tracks)
    ev = H["_events"](d); g = H["_track_groups"](d, ev).get(track)
    if not g:
        return d, False
    new = H["_data_event"](239, str(name).encode("utf-16-le") + b"\x00\x00")
    if g["name_k"] is not None:
        return H["_splice"](d, ev, [(g["name_k"], g["name_k"] + 1, new)]), True
    return H["_splice"](d, ev, [(g["end"], g["end"], new)]), True


def _pick_track(d, name, taken):
    """A playlist track for `name`: one already named so, else the first unnamed track with no clips (above the
    highest used one when possible)."""
    ev = H["_events"](d); tracks = _track_events(d, ev)
    for t, (_, nm) in sorted(tracks.items()):
        if nm == name:
            return t, False
    used = {499 - struct.unpack("<H", r[12:14])[0] + 1 for _, _, r in H["_clips"](d, ev)} | taken
    named = {t for t, (_, nm) in tracks.items() if nm}
    top = max(used | named, default=0)
    for rng in (range(top + 1, H["MAX_PLAYLIST_TRACK"] + 1), range(1, H["MAX_PLAYLIST_TRACK"] + 1)):
        for t in rng:
            if t not in used and t not in named:
                return t, True
    return None, False


def flp_realism_write(project, plan, output=None):
    """flp_realism's write (an flp_edit op): note records per (pattern, channel), automation clips by label (its own
    earlier clips are updated in place, never anyone else's), named playlist tracks."""
    output = output or project
    H["writable"](output)
    d = H["_load"](project); ev = H["_events"](d)
    res = {"patterns": 0, "clips_added": 0, "clips_updated": 0, "tracks_named": []}
    pats = H["_patterns"](d, ev); nev = H["_pattern_note_events"](d, ev)
    batch, later = [], []
    for it in plan.get("notes") or []:
        pid, ch = int(it["pattern"]), int(it["channel"])
        cur = [r for r in pats.get(pid, {"notes": []})["notes"] if r[2] == ch]
        if it.get("expect") is not None and _recs_digest(cur) != int(it["expect"]):
            raise ValueError(f"pattern {pid} channel {ch} changed since the plan was made; run flp_realism again")
        new = []
        for r in it["records"]:
            r = [int(x) for x in r]
            if len(r) != 14:
                raise ValueError("a note record has 14 fields")
            new.append({"pos": r[0], "len": r[3], "key": r[4], "vel": r[11], "ch": ch, "_r": tuple(r)})
        (batch if nev.get(pid) and pid not in {b[0] for b in batch} else later).append((pid, ch, new))
    if batch:                                                 # one splice for every pattern that has a notes event
        edits = []
        for pid, ch, new in batch:
            keep = [H["_rec_to_note"](r) for r in pats[pid]["notes"] if r[2] != ch]
            body = b"".join(H["_note_to_rec"](n)
                            for n in sorted(keep + new, key=lambda n: (n["pos"], n["ch"], n["key"])))
            edits += H["_notes_event_edits"](ev, nev[pid], body)
        d = H["_splice"](d, ev, edits)
    for pid, ch, new in later:                                # rare: a pattern with no notes event yet / twice
        ev = H["_events"](d)
        keep = [H["_rec_to_note"](r) for r in H["_patterns"](d, ev).get(pid, {"notes": []})["notes"] if r[2] != ch]
        d = H["_write_pattern_notes"](d, ev, pid, keep + new)
    res["patterns"] = len(batch) + len(later)
    autos_ = list(plan.get("automation") or []); own_ = list(plan.get("own") or [])
    # PC-VERIFY: flp_compose(what='layer', role='strings') onto a channel it creates: the expression clips appear in
    # the same reload, linked to the new channel (fallback: its volume) and named like flp_realism's
    ci_ = _channels(d, H["_events"](d)) if plan.get("expression") else {}
    for x in plan.get("expression") or []:                    # channels this batch created: resolved now
        ch = int(x["channel"])
        try:
            a_, o_ = _expression_autos(project, ci_, ch, x["spec"], int(x.get("ppq") or H["_ppq"](d)))
        except ValueError as e:
            res.setdefault("expression", {})[str(ch)] = f"not written: {e}"
            continue
        autos_ += a_; own_ += o_ + [a["label"] for a in a_]
        res.setdefault("expression", {})[str(ch)] = (len(a_) if a_ else "nothing to drive on this channel (its "
                                                     "family plays expression with the notes)")
    own = set(own_) | {a["label"] for a in autos_}
    taken = set(); track_for = {}
    for a in autos_:
        ev = H["_events"](d)
        names = H["_clip_names"](d, ev)
        old = next((c for c, (kind, nm) in sorted(names.items()) if kind == "automation" and nm == a["label"]
                    and nm in own), None)
        tn = a.get("track_name")
        trk = None
        if old is None and tn:
            if tn not in track_for:
                t, fresh = _pick_track(d, tn, taken)
                if t is not None:
                    taken.add(t)
                    if fresh or not any(nm == tn for _, nm in _track_events(d, H["_events"](d)).values()):
                        d, ok = _name_track(d, t, tn)
                        if ok:
                            res["tracks_named"].append(f"{t}: {tn}")
                track_for[tn] = t
            trk = track_for[tn]
        pts = [(float(b), float(v)) for b, v in a["points"]]
        d, info = H["_automation_write"](d, int(a["pid"]), int(a["dest"]), a["label"], pts, trk, old)
        res["clips_updated" if old is not None else "clips_added"] += 1
    H["_save"](d, output)
    return {"output": output, **res}


# ============================================================================= tool
def _summary(c):
    """One plain sentence per channel."""
    if c.get("undo"):
        r = c.get("restored") or {}
        return (f"{c['name']}: {r.get('restored', 0)} notes back as they were, {len(c.get('clips_removed') or [])} "
                "realism clips removed")
    r = c["report"]; bits = []
    nm = c["name"]; fam = c["family"]
    if c["mode"] == "controller" and c["clips"]:
        ctl = " + ".join(f"{v}" for v in c["targets"].values())
        bits.append(f"{r.get('phrases', 0)} phrase shapes with {r.get('swells', 0)} swells ({ctl}, "
                    f"{len(c['clips'])} clips)")
    elif c["mode"] == "fallback":
        bits.append(f"{r.get('phrases', 0)} phrase shapes in velocities" +
                    (f" + channel volume ({len(c['clips'])} clips)" if c["clips"] else "") +
                    " - weaker than a dynamics controller (no controller found for this plugin)")
    else:
        bits.append(f"{r.get('phrases', 0)} phrases shaped in velocities")
    if r.get("breaths"):
        bits.append(f"breaths at {r['breaths']} places")
    if r.get("late_ms"):
        bits.append(f"attacks {r['late_ms'][0]:.0f}-{r['late_ms'][1]:.0f} ms late")
    elif r.get("spread_ms"):
        bits.append(f"timing spread +-{r['spread_ms']:.0f} ms")
    if r.get("roll_notes"):
        bits.append(f"{r['roll_notes']} roll notes hand-to-hand")
    if r.get("legato") == "overlap":
        bits.append(f"legato on ({r['legato_links']} overlaps)")
    elif r.get("legato") == "touch":
        bits.append(f"lines connected ({r['touch_links']} gaps closed; this sound has no legato)")
    if r.get("shortened_phrase_ends"):
        bits.append(f"{r['shortened_phrase_ends']} phrase ends shorter")
    if r.get("rearticulated"):
        bits.append(f"{r['rearticulated']} held notes re-articulated (breath/bow limit)")
    if r.get("released_repeats"):
        bits.append(f"{r['released_repeats']} repeated notes released before they restart")
    a = c.get("articulation") or {}
    if a.get("set"):
        bits.append(f"articulation {a.get('before')} -> {a['set']}")
    if a.get("keyswitches"):
        bits.append("keyswitches: " + ", ".join(f"{k['section']} {k['articulation']}" for k in a["keyswitches"][:4]))
    return f"{nm} ({fam}): " + ", ".join(bits)


def flp_realism(project, channels="auto", sections=None, amount=None, dry_run=False, output=None, undo=False,
                articulation="auto", families=None, params=None, genre=None, seed=0, full=False):
    """Make the support layers sound played and recorded: expression automation, human timing and lengths, breaths,
    legato, articulation - in ONE batch; undo=True puts it all back."""
    T0 = time.time()
    project = os.path.abspath(project)
    if not os.path.isfile(project):
        raise FileNotFoundError(project)
    _sync(project)
    out = output or project
    if params:                                                  # verified host parameter indexes: remember per library
        _remember_params(project, params)
    if amount is not None and not 0.0 <= float(amount) <= 1.0:
        raise ValueError("amount is 0..1 (0.5 subtle, 1 full)")
    pl = _plan(project, channels, sections, amount, articulation, families, params, genre, seed, undo)
    res = {"project": out, "genre": pl["genre"] or None, "bpm": round(pl["bpm"], 2),
           "channels": [_summary(c) for c in pl["channels"]]}
    if pl["skipped"]:
        res["left_alone"] = [f"{s['name']}: {s['why']}" for s in pl["skipped"]][:12]
    if not undo:
        res["checks"] = {c["name"]: c["verify"] for c in pl["channels"] if c.get("verify")}
        notes = [f"{c['name']}: {n}" for c in pl["channels"] for n in (c.get("target_notes") or [])]
        notes += [f"{c['name']}: {(c.get('articulation') or {}).get('note')}" for c in pl["channels"]
                  if (c.get("articulation") or {}).get("note")]
        tick = max((c["report"].get("tick_ms") or 0) for c in pl["channels"] if c.get("report")) \
            if any(c.get("report") for c in pl["channels"]) else 0
        if tick > 4:
            notes.append(f"timing moves in steps of {tick:.1f} ms (the project's PPQ {pl['ppq']} at {pl['bpm']:.0f} "
                         "BPM): finer human timing needs a higher PPQ (FL: Options > Project general settings)")
        rs = [c for c in pl["channels"] if (c.get("restored") or {}).get("kept_edited")]
        notes += [f"{c['name']}: {c['restored']['kept_edited']} notes you edited since the last run were taken as "
                  "they are now" for c in rs]
        if notes:
            res["notes"] = notes[:10]
    ops = []
    if pl["notes"] or (pl["automation"] and not pl["automation_same"]):
        ops.append({"tool": "flp_realism_write", "plan": {k: pl[k] for k in ("notes", "automation", "own")}})
    ops += pl["artic"]
    rem = _removal_op(project, pl["remove"], pl["own"])
    if rem:
        ops.append(rem)
    if full:
        res["detail"] = [{k: v for k, v in c.items() if k not in ("verify",)} for c in pl["channels"]]
    if dry_run:
        res["dry_run"] = True
        res["would_write"] = {"note_patterns": len(pl["notes"]), "clips": len(pl["automation"]),
                              "clips_removed": len(rem["channels"]) if rem else 0,
                              "articulation": [f"channel {a['channel']}: {a['articulation']}" for a in pl["artic"]]}
        res["steps_s"] = pl["steps_s"]
        return res
    T1 = time.time()
    if ops:
        r = H["TOOLS"]["flp_edit"][0](project=project, ops=ops, output=output)
        res["edit"] = {"ops": len(ops)}
        if isinstance(r, dict) and r.get("queued"):
            res["queued"] = True                              # lazy mode: checked on the shadow copy by flp_edit
            res["verify_file"] = "queued: FL gets it at the next sync (flh_sync), checked then"
        else:
            res["verify_file"] = _verify_written(out, pl, undo)
    else:
        res["edit"] = "nothing to write: the project already sounds this way"
    st = _store()
    jr = (_journal(project).get(JKEY) or {})
    hist = list(jr.get("history") or [])
    if jr.get("applied"):
        hist = (hist + [{"when": jr.get("when"), "channels": sorted(jr["applied"])}])[-5:]
    try:
        st.journal_update(out, set={JKEY: None})
        st.journal_update(out, set={JKEY: {"applied": pl["journal"], "when": time.strftime("%Y-%m-%d %H:%M"),
                                           "history": hist, "amount": amount, "seed": seed}})
    except Exception as e:                                   # the edit is in; say what couldn't be remembered
        res["journal_error"] = f"{type(e).__name__}: {e} (undo/re-run won't know about this run)"
    res["listen"] = ("These are proxies; whether it sounds played is your ear's call. A/B it: fl_ab(project) "
                     "toggles the version before this run (flp_versions keeps it) against this one; undo: "
                     "flp_realism(project, undo=true); re-run after editing notes (its own clips are replaced).")
    res["pc_verify"] = "first time on this PC: checklists/realism.md (does the plugin follow the clips?)"
    res["steps_s"] = {**pl["steps_s"], "write": round(time.time() - T1, 3), "total": round(time.time() - T0, 3)}
    return res


def _removal_op(project, labels, own):
    """flp_remove_channels op for this module's clips that are no longer needed (by label, own clips only)."""
    if not labels:
        return None
    d = H["_load"](project); ev = H["_events"](d)
    names = H["_clip_names"](d, ev)
    chs = sorted(c for c, (kind, nm) in names.items() if kind == "automation" and nm in set(labels) and nm in set(own))
    if not chs:
        return None
    return {"tool": "flp_remove_channels", "channels": chs, "force": True}


def _verify_written(path, pl, undo):
    """Read the written file back: the planned note records are there and the clips point at their channels."""
    try:
        d = H["_load"](path); ev = H["_events"](d)
    except Exception as e:
        return {"ok": False, "problems": [f"couldn't read the file back ({e})"]}
    probs = []
    pats = H["_patterns"](d, ev)
    for it in pl["notes"]:
        cur = sorted(tuple(r) for r in pats.get(it["pattern"], {"notes": []})["notes"] if r[2] == it["channel"])
        if cur != sorted(tuple(int(x) for x in r) for r in it["records"]):
            probs.append(f"pattern {it['pattern']} channel {it['channel']}: notes differ from the plan")
    names = H["_clip_names"](d, ev); links = _existing_links(d, ev)
    byname = {}
    for c, (kind, nm) in names.items():
        if kind == "automation":
            byname.setdefault(nm, []).append(c)
    placed = {struct.unpack("<H", r[6:8])[0] for _, _, r in H["_clips"](d, ev)}
    for a in pl["automation"]:
        cs = byname.get(a["label"]) or []
        if len(cs) != 1:
            probs.append(f"clip '{a['label']}': found {len(cs)}"); continue
        if links.get(cs[0]) != (a["pid"], a["dest"]):
            probs.append(f"clip '{a['label']}' targets {links.get(cs[0])}, not {(a['pid'], a['dest'])}")
        if cs[0] not in placed:
            probs.append(f"clip '{a['label']}' isn't in the playlist")
    for lab in pl["remove"]:
        if byname.get(lab):
            probs.append(f"old clip '{lab}' is still there")
    return {"ok": not probs, **({"problems": probs[:8]} if probs else {})}


def _remember_params(project, params):
    """Store verified host parameter indexes per library in the collection profile (realism_params)."""
    try:
        d = H["_load"](project); ev = H["_events"](d); ci = _channels(d, ev)
        prof = _store().profile_get() or {}
        rp = dict(prof.get("realism_params") or {})
        for k, v in (params or {}).items():
            ch = int(k) if str(k).isdigit() else next((c for c, i in ci.items() if (i.get("name") or "") == k), None)
            if ch is None or not isinstance(v, dict):
                continue
            lib = _library(ci.get(ch, {}), None)
            if lib:
                rp[lib["id"]] = {c: int(x) for c, x in v.items() if c in CONTROLS and x is not None}
        if rp and _store().profile_name():
            _store().profile_set(_store().profile_name(), {"realism_params": rp})
    except Exception:
        pass


def realism_expression_ops(project, channel, expression, context=None):
    """flp_edit ops that write an expression spec from realism_apply (for flh_perform / flh_compose): the channel's
    targets are resolved from the project like flp_realism does; clips get flp_realism's names, so a later
    flp_realism run or undo replaces / removes them. [] when the channel has nothing to drive or no spec.
    A channel the same batch creates first (context new_channel=true: flp_add_channel ops before this one) isn't in
    the file yet: its targets are resolved when the batch runs (flp_realism_write plan 'expression')."""
    if not expression or not expression.get("clips"):
        return []
    d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
    ctx = dict(context or {}); ppq = int(ctx.get("ppq") or P)
    ch = int(channel); ci = _channels(d, ev)
    if ch not in ci:
        if not ctx.get("new_channel"):
            raise ValueError(f"channel {ch} isn't in the project")
        return [{"tool": "flp_realism_write", "plan": {"notes": [], "automation": [], "own": [], "expression": [
            {"channel": ch, "spec": expression, "ppq": ppq}]}}]
    autos, own = _expression_autos(project, ci, ch, expression, ppq)
    if not autos:
        return []
    return [{"tool": "flp_realism_write",
             "plan": {"notes": [], "automation": autos, "own": own + [a["label"] for a in autos]}}]


def _expression_autos(project, ci, ch, expression, ppq):
    """([automation plan rows], [labels flp_realism wrote on this channel before]) for an expression spec on a
    channel of ci (_channels); [] rows when the channel has nothing to drive (piano / drums family, no target)."""
    info = ci.get(int(ch))
    if info is None:
        raise ValueError(f"channel {ch} isn't in the project")
    fam = canon_family(expression.get("family")) or "generic"
    cfg = family_cfg(fam); lib = _library(info, fam)
    mode, targets, _ = _targets(ch, info, fam, lib, None, cfg)
    jr = (_journal(project).get(JKEY) or {}).get("applied") or {}
    own = list((jr.get(str(ch)) or {}).get("clips") or [])
    if mode == "notes" or not targets:
        return [], own
    name = info.get("name") or f"Channel {ch}"
    used, autos = set(), []
    for cl in expression["clips"]:
        ctl = cl["control"]; tg = targets.get(ctl)
        if tg is None and mode == "fallback" and ctl == "dynamics":
            ctl, tg = "volume", targets.get("volume")
        if tg is None:
            continue
        pts = _volume_points(info, cfg, cl["points"]) if ctl == "volume" else cl["points"]
        lab = _labels(name, ctl, cl["section"], used)
        autos.append({"label": lab, "pid": tg[0], "dest": int(ch), "track_name": _track_name(name, ctl),
                      "points": [[round(t / ppq, 6), round(float(v), 4)] for t, v in pts]})
    return autos, own


# ============================================================================= register
def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    ns["realism_apply"] = realism_apply
    ns["realism_families"] = realism_families
    ns["realism_expression_ops"] = realism_expression_ops
    ns["realism_suggest_articulations"] = suggest_articulations
    ns["TOOLS"]["flp_realism"] = (
        flp_realism,
        "Make programmed support layers (strings, choir, brass, winds, timpani, pads...) sound played and recorded, "
        "for any song and genre: per-phrase dynamics/expression automation the plugin follows (swells into long "
        "notes, bow arcs, crescendos with the melody and section energy, phrase-end decays), human timing (late "
        "speaking attacks, section spread, no drift for timpani), natural lengths (breaths that shorten the note "
        "before them, legato overlaps where the library plays legato, releases before repeated notes, breath/bow "
        "limits), articulation per passage - in ONE batch. Piano/keys, drums and bass are left as played. amount "
        "0..1 (default by genre: subtle for pop/EDM, rich for orchestral/cinematic/ballad). Returns one plain line "
        "per channel plus honest checks (movement inside notes, timing spread, lengths vs the family's ranges); "
        "your ear decides (fl_ab). undo=true restores every note and removes its clips; re-run after edits "
        "replaces its own clips by name, never your automation. dry_run: report only.",
        S(project=ns["_FLP"],
          channels=P(["string", "array"], "auto (default: the non-piano layers) or channels (index or name)", True,
                     items={"type": ["integer", "string"]}),
          sections=P(["string", "array"], "only these sections (names or bar ranges '9-16')", True,
                     items={"type": "string"}),
          amount=ns["_NUM"]("0..1 (default by genre/style)", True),
          dry_run=ns["_BOOL"]("report what it would do, write nothing", True),
          output=ns["_STR"]("save as (default: the project itself)", True),
          undo=ns["_BOOL"]("put the notes back and remove its clips", True),
          articulation=ns["_STR"]("auto (default: set it when the whole part wants another one) | suggest | off",
                                  True),
          families=P("object", "{channel or name: family} when the guess is wrong (strings, choir, brass, winds, "
                     "timpani, mallets, pluck, pad, synth, lead, bass)", True),
          params=P("object", "{channel: {dynamics: index, expression: index}} plugin parameter indexes you checked "
                   "with fl_plugin_params (remembered per library)", True),
          genre=ns["_STR"]("genre/style words (default: the song's journal/project)", True),
          seed=ns["_INT"]("variation seed (default 0; same seed = same result)", True),
          full=ns["_BOOL"]("per-channel detail", True)))
    ns["TOOLS"]["flp_realism_write"] = (
        ns["_guard_open_project"](flp_realism_write, "project", "output"),
        "flp_realism's write (flp_edit op; flp_realism / realism_expression_ops build it): plan {notes: [{pattern, "
        "channel, records, expect}], automation: [{label, pid, dest, track_name, points [[beat, value]]}], own: "
        "[labels it may update], expression: [{channel, spec, ppq}] (an expression spec for a channel an earlier op "
        "of the same batch created: its targets are resolved when this op runs)}.",
        S(project=ns["_FLP"], plan=P("object", "the write plan"), output=ns["_STR"]("save as", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_realism_write"] = ("project", "output")
    if "ESSENTIAL_TOOLS" in ns:
        ns["ESSENTIAL_TOOLS"].add("flp_realism")
    if "TOOL_GROUPS" in ns:
        ns["TOOL_GROUPS"]["flp_realism"] = "perform"
        ns["TOOL_GROUPS"]["flp_realism_write"] = "perform"
