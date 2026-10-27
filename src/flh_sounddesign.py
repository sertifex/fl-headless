"""flh_sounddesign: make a channel's sound better (or insane) from words, live in the open FL.

fl_sound_shape(channel, words, amount)  'warm', 'dark', 'bright', 'airy', 'wide', 'punchy', 'soft', 'gritty', 'lofi',
    'dreamy', 'huge', 'clean', 'tight', 'insane' (and their everyday aliases) become (1) nudges of the channel's own
    instrument controls, found by their FL parameter names (cutoff, resonance, attack, release, detune, drive, tone,
    width, chorus...: works on any plugin FL exposes, stock or third party), and (2) a chain of FL's stock effects on
    the channel's mixer track (EQ, compressor, Soundgoodizer or the Limiter's saturation, Stereo Enhancer, Reeverb 2,
    Delay 3, Filter), set in real units through the plugin maps. Effects missing from the track are added in ONE
    project edit; every knob goes in live. check=true measures the channel alone before and after with FL's own
    export (~9 s each), keeps the level matched (the channel's mixer fader takes the difference, so 'better' isn't
    just 'louder') and says whether each wish was achieved. undo=true puts every knob and fader back and removes the
    effects it added. Data: data/sound/shape.json (a user copy in the state folder's sound/ wins).
knobs={knob: move} sets any knob (grammar: parse_move): '-36 dB' / '2000 Hz' / '30%' set that value; '+3 dB',
    'by -3 dB', 'up 2', 'down 20 ms', '-20 ms', '-3%' (of the shown percent), '-1 oct' move from where it is; 'x0.5'
    multiplies; '=0.5' is the raw knob position. A value FL's display search can't reach is searched here
    (few round trips); outside the knob's range it stops at the nearest end and says so.
"""
import json
import os
import re
import time

H = None
UNDO_KEY = "sound_shape"
LEVEL_MATCH_MAX_DB = 12.0          # never move a fader further than this to match levels
NUDGE_MAX = 0.45                   # one call moves an instrument knob by at most this share of its range
DARK_EQ_DEEPER = 1.4               # darker with no instrument cutoff to lower: the EQ's high shelves cut this much more
FILTER_ENV = re.compile(r"(?i)\b(filter|filt|flt|vcf|cutoff)\b|\bf\.?\s*env")


def _data():
    for base in ([os.path.join(os.path.dirname(H["STATE"]), "sound")] if H and H.get("STATE") else []) + \
            [os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sound")]:
        try:
            with open(os.path.join(base, "shape.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    raise RuntimeError("data/sound/shape.json is missing")


def _mix():
    import sys
    m = sys.modules.get("flh_mixeng")
    if m is None:
        import importlib
        m = importlib.import_module("flh_mixeng")
    return m


def parse_words(text, data=None):
    """'make the piano warm and huge' -> (['warm', 'huge'], boost). Longest alias first, each word once."""
    data = data or _data()
    t = " " + re.sub(r"[^a-z0-9\- ]+", " ", str(text or "").lower()) + " "
    found, boost = [], 0
    pairs = sorted(((a, k) for k, w in data["words"].items() for a in [k] + list(w.get("aliases") or [])),
                   key=lambda p: -len(p[0]))
    for alias, key in pairs:
        if f" {alias} " in t and key not in found:
            found.append(key); t = t.replace(f" {alias} ", " ")
            boost += int(data["words"][key].get("boost") or 0)
    return found, boost


def _amount(amount, boost, data):
    names = list(data["amounts"])
    a = str(amount or "medium").lower()
    if a not in data["amounts"]:
        raise ValueError(f"amount: {' | '.join(names)}")
    i = min(len(names) - 1, names.index(a) + boost)
    return names[i], float(data["amounts"][names[i]])


def _owned(plugin):
    try:
        own = H["plugin_ownership"](plugin)
        return own.get("status") is True
    except Exception:
        return True                                         # can't tell: the effect add itself reports a refusal


def plan(words, amount="medium", data=None):
    """The moves for these words at this amount: {words, amount, inst: {role: share}, eq: [...], fx: [...], intents}."""
    data = data or _data()
    keys, boost = parse_words(words, data) if isinstance(words, str) else (list(words), 0)
    if not keys:
        raise ValueError("no sound words understood: try " + ", ".join(data["words"]))
    aname, k = _amount(amount, boost, data)
    inst, eqs, fx, intents, notes = {}, [], {}, [], []
    for w in keys:
        spec = data["words"][w]
        for role, share in (spec.get("inst") or {}).items():
            inst[role] = max(-NUDGE_MAX, min(NUDGE_MAX, inst.get(role, 0.0) + share * k))
        for e in spec.get("eq") or []:
            e2 = dict(e)
            if e2.get("type") not in ("High pass", "Low pass"):
                e2["gain_db"] = round(float(e2["gain_db"]) * k, 2)
            eqs.append(e2)
        for f in spec.get("fx") or []:
            f2 = f
            if not _owned(f["plugin"]) and f.get("alt"):
                notes.append(f"{f['plugin']} isn't in your FL edition: used {f['alt']['plugin']} instead")
                f2 = f["alt"]
            ctl = {}
            for c, v in f2["controls"].items():
                ctl[c] = round(v * k, 2) if c in (f2.get("scale") or []) and isinstance(v, (int, float)) else v
            cur = fx.setdefault(f2["plugin"], {})
            for c, v in ctl.items():                        # two words on one effect: the stronger setting wins
                if isinstance(v, (int, float)) and isinstance(cur.get(c), (int, float)):
                    cur[c] = v if abs(v) > abs(cur[c]) else cur[c]
                else:
                    cur[c] = v
        intents += [i for i in spec.get("intents") or [] if i not in intents]
    for w in keys:                                          # a word's settings for effects other words add
        for f in data["words"][w].get("fx_tune") or []:
            cur = fx.get(f["plugin"])
            if cur is None:
                continue
            for c, v in f["controls"].items():
                cur[c] = min(v, cur[c]) if isinstance(v, (int, float)) and isinstance(cur.get(c), (int, float)) else v
    dark = DARK_INTENT in intents
    if dark:
        eqs, inst, intents = _dark_wins(eqs, inst, intents, notes)
    for p, ctl in fx.items():                               # keep effects inside their safe ranges
        for c in list(ctl):
            if c.endswith("_pct") and isinstance(ctl[c], (int, float)):
                ctl[c] = max(0.0, min(100.0, ctl[c]))
            if c == "stereo_separation":
                ctl[c] = max(-100.0, min(100.0, ctl[c]))
    eqs = _merge_eq(eqs)
    return {"words": keys, "amount": aname, "inst": {r: round(v, 3) for r, v in inst.items() if abs(v) > 0.005},
            "eq": eqs, "fx": [{"plugin": p, "controls": c} for p, c in fx.items()], "intents": intents, "notes": notes,
            "eq_last": dark}


DARK_INTENT = "darker"
TOP_END_HZ = 2000.0                 # 'darker' is measured on 2.5-16 kHz: no other word's boost from here up survives
BRIGHT_INTENTS = ("brighter", "airier")


def _dark_wins(eqs, inst, intents, notes):
    """'darker' together with a word that lifts the top end ('dreamy', 'airy', 'bright', 'insane'): the dark one wins
    there - no EQ boost at TOP_END_HZ and up, no brighter tone/cutoff nudge, no 'brighter'/'airier' check that can
    only fail. The other word keeps everything else (its space, width, envelope)."""
    drop = [e for e in eqs if e.get("type") in ("High shelf", "Peaking") and float(e.get("gain_db") or 0) > 0
            and float(e["hz"]) >= TOP_END_HZ]
    if drop:
        eqs = [e for e in eqs if not any(e is d for d in drop)]
        notes.append("darker wins on the top end: left out " +
                     ", ".join(f"{e['type'].lower()} +{e['gain_db']:g} dB at {e['hz']:g} Hz" for e in drop))
    for role in ("cutoff", "tone"):
        if inst.get(role, 0.0) > 0:
            inst[role] = 0.0
    gone = [i for i in intents if i in BRIGHT_INTENTS]
    if gone:
        notes.append(f"asked darker and {'/'.join(gone)} together: darker wins on the top end")
    return eqs, inst, [i for i in intents if i not in BRIGHT_INTENTS]


def _merge_eq(eqs):
    """Same kind near the same frequency (within ~1/2 octave): one band with the summed gain (max 7 bands)."""
    out = []
    for e in eqs:
        hit = next((o for o in out if o["type"] == e["type"] and abs(_oct(o["hz"], e["hz"])) < 0.5), None)
        if hit and e["type"] not in ("High pass", "Low pass"):
            hit["gain_db"] = round(max(-12.0, min(12.0, hit["gain_db"] + e["gain_db"])), 2)
        elif hit:
            hit["hz"] = max(hit["hz"], e["hz"]) if e["type"] == "High pass" else min(hit["hz"], e["hz"])
        else:
            out.append(dict(e))
    return [e for e in out if e["type"] in ("High pass", "Low pass") or abs(e["gain_db"]) >= 0.3][:7]


def _oct(a, b):
    import math
    return math.log2(float(a) / float(b))


def _role_of(name, data):
    """What a knob does, from its name - for any plugin, stock or third party: 'Fil Cutoff', 'VCF Freq',
    'filter_1_cutoff' -> cutoff; 'Dry/Wet', 'Mix' -> mix; 'Amp Env A' -> attack. None when unsure."""
    n = " " + re.sub(r"[^a-z0-9]+", " ", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name)).lower()) + " "
    keep = data.get("keep_with") or []
    if any(f" {s} " in n for s in data.get("skip_names") or []) and not any(f" {w} " in n for w in keep):
        return None
    env = any(f" {w} " in n for w in data.get("single_letter_need") or [])
    best = None
    for role, keys in data["roles"].items():
        for k in keys:
            if len(k) == 1 and not env:
                continue                                    # 'A' alone is an attack only on an envelope
            if f" {k} " in n and (best is None or len(k) > best[1]):
                best = (role, len(k))
    return best[0] if best else None


def _num(text):
    try:
        return _mix().parse_display(text)
    except Exception:
        return None, None


def _remember_plugin(plugin, roles, count):
    """What fl-headless learned about a plugin's knobs, kept in the state folder (any plugin, also future ones)."""
    if not plugin:
        return
    try:
        p = os.path.join(os.path.dirname(H["STATE"]), "plugin_knobs.json")
        try:
            with open(p, encoding="utf-8") as f:
                mem = json.load(f)
        except (OSError, ValueError):
            mem = {}
        row = {"seen": time.strftime("%Y-%m-%d"), "params": count,
               "roles": {r: [x["name"] for x in v][:6] for r, v in roles.items()}}
        if mem.get(plugin, {}).get("roles") == row["roles"]:
            return
        mem[plugin] = row
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mem, f, indent=0)
        os.replace(tmp, p)
    except Exception:
        pass


UNITS = {"%": "%", "pct": "%", "percent": "%", "ms": "ms", "s": "s", "sec": "s", "secs": "s", "seconds": "s",
         "hz": "Hz", "khz": "kHz", "k": "kHz", "db": "dB", "st": "st", "semi": "st", "semis": "st",
         "semitone": "st", "semitones": "st", "cent": "cents", "cents": "cents", "ct": "cents", "oct": "oct",
         "octave": "oct", "octaves": "oct", "q": "Q"}
SIGNED_ABS_UNITS = ("dB", "st", "cents")         # '-36 dB' is a value to set: these units have real negative values
SEARCH_MAX_SETS = 8                              # own knob search: at most this many FL round trips


def parse_move(move):
    """The knob-move grammar -> {mode: norm | mul | rel | abs, num, unit} (unit in base form: ms, Hz, dB, %, st,
    cents, oct, Q or None). Raises ValueError for text it can't read.
      '=0.5' raw knob position 0..1;  'x0.5' / '*2' / '/2' multiply the shown value;
      '-36 dB' / '-12 st' / '-50 cents' SET that value (units with real negative values);  '2000 Hz', '30%', '0.8 Q' set;
      '+3 dB', 'by -3 dB', 'up 2 st', 'down 20 ms', '-20 ms', '-3%', '-1 oct' (octaves always move) move from now;
      'to -30%' forces a set."""
    t = " ".join(str(move).strip().lower().replace("−", "-").split())
    m = re.match(r"^=\s*(\d*\.?\d+)$", t)
    if m:
        v = float(m.group(1))
        if v > 1.0:
            raise ValueError(f"'{move}': '=' sets the raw knob position, 0..1")
        return {"mode": "norm", "num": v, "unit": None}
    m = re.match(r"^([x*×/])\s*(\d*\.?\d+)$", t)
    if m:
        f = float(m.group(2))
        if f <= 0:
            raise ValueError(f"'{move}': multiply by a number above 0")
        return {"mode": "mul", "num": 1.0 / f if m.group(1) == "/" else f, "unit": None}
    word = None
    m = re.match(r"^(to|by|up|down)\s+(.*)$", t)
    if m:
        word, t = m.group(1), m.group(2)
    m = re.match(r"^([-+])?\s*(\d*\.?\d+)\s*([a-z%]+)?$", t)
    if not m:
        raise ValueError(f"can't read the move '{move}'")
    sign, num, u = m.group(1), float(m.group(2)), m.group(3)
    if u is not None and u not in UNITS:
        raise ValueError(f"'{move}': unknown unit '{u}' (dB, %, ms, s, Hz, kHz, st, cents, oct, Q)")
    unit = UNITS.get(u) if u else None
    if unit == "s":
        num, unit = num * 1000.0, "ms"
    elif unit == "kHz":
        num, unit = num * 1000.0, "Hz"
    if sign == "-":
        num = -num
    if word in ("up", "down"):
        if sign:
            raise ValueError(f"'{move}': 'up'/'down' take a plain number ('down 3 dB')")
        return {"mode": "rel", "num": -abs(num) if word == "down" else abs(num), "unit": unit}
    if word == "by":
        return {"mode": "rel", "num": num, "unit": unit}
    if word == "to":
        return {"mode": "abs", "num": num, "unit": unit}
    if unit == "oct":                            # octaves move a frequency; on an octave display: signed moves
        return {"mode": "rel" if sign else "octabs", "num": num, "unit": unit}
    if sign == "+" or (sign == "-" and unit not in SIGNED_ABS_UNITS):
        return {"mode": "rel", "num": num, "unit": unit}
    return {"mode": "abs", "num": num, "unit": unit}


def _clamp01(x):
    return max(0.0, min(1.0, float(x)))


def knob_target(p, role, share=None, move=None):
    """Where a knob should go. share: a word's nudge (-1..1); move: the grammar of parse_move, or a number (a share).
    -> ('value', number in the knob's display unit) | ('norm', 0..1) | (None, why)."""
    v0 = float(p.get("value") or 0.0)
    cur, unit = _num(p.get("text"))
    if move is not None and not isinstance(move, (int, float)):
        try:
            mv = parse_move(move)
        except ValueError as e:
            return None, str(e)
        return _apply_move(p, mv, v0, cur, unit)
    s = float(move if move is not None else share or 0.0)
    if str(p.get("text", "")).strip().lower() in ("on", "off"):     # a switch (FLEX's Reverb / Delay): on for more
        return ("norm", 1.0) if s > 0 else ("norm", 0.0)
    kind, t = _share_target(role, s, v0, cur, unit)
    if s and (abs(t - cur) <= _tol(p.get("text"), t) if kind == "value" else abs(t - v0) < 1e-3):
        # the display can't go further that way. A knob that still has travel shows an offset / a bipolar amount
        # (FLEX's 'Filter cutoff' at '0%'): move the knob itself. At its real end: no fake '0% -> 0%' move.
        # PC-VERIFY: FLEX 'Filter cutoff' / 'Post filter cutoff' showing '0%': read its 0..1 value - 0.0 (the floor:
        # skipped, the EQ cuts deeper) or mid-travel (moved by knob position); listen that 'darker' gets darker
        if (s < 0 and v0 > 0.005) or (s > 0 and v0 < 0.995):
            return "norm", round(_clamp01(v0 + s), 4)
        return None, f"already at its {'lowest' if s < 0 else 'highest'} ('{p.get('text')}')"
    return kind, t


def _share_target(role, s, v0, cur, unit):
    if cur is not None and unit == "Hz" and role in ("cutoff",) and cur > 0:
        return "value", max(20.0, min(20000.0, cur * 2 ** (s * 6)))        # musical: a share of ~0.17 = 1 octave
    if cur is not None and unit == "ms" and role in ("attack", "decay", "release"):
        return "value", (cur * 2 ** (s * 6)) if cur >= 1.0 else max(0.0, s * 300.0)
    if cur is not None and unit == "%":
        return "value", max(-100.0 if cur < 0 else 0.0, min(100.0, cur + s * 100.0))   # '-20%': a bipolar display
    return "norm", round(max(0.0, min(1.0, v0 + s)), 4)


def _apply_move(p, mv, v0, cur, unit):
    mode, x, u = mv["mode"], float(mv["num"]), mv["unit"]
    rel = mode == "rel"
    shown = f"'{p.get('name')}' shows '{p.get('text')}'"
    du = unit if cur is not None else None                      # the display's unit (None: no number shown)
    if mode == "norm":
        return "norm", x
    if mode == "mul":
        return ("norm", _clamp01(v0 * x)) if cur is None else ("value", cur * x)
    if u == "oct":
        if du == "Hz" and cur and cur > 0:                      # '-1 oct' / '2 oct' move a frequency
            return "value", cur * 2 ** x
        if du == "oct":                                         # an octave width: '+0.5 oct' moves, '2 oct' sets
            return "value", (cur + x) if rel else x
        return None, f"{shown}: octaves only move a frequency (Hz) or an octave width"
    if u == "%":
        if du == "%":                                           # the shown percent: '-3%' from 34% is 31%
            return "value", (cur + x) if rel else x
        return "norm", _clamp01(v0 + x / 100.0 if rel else x / 100.0)     # a share of the knob's travel
    if u is None:
        if cur is None:
            return "norm", _clamp01(v0 + x / 100.0 if rel else x / 100.0)
        return "value", (cur + x) if rel else x
    if u == "Q":
        if du == "Q":
            return "value", (cur + x) if rel else x
        if du == "oct" and not rel:
            try:
                return "value", _mix().q_to_oct(x)
            except Exception:
                pass
        return None, f"{shown}: Q doesn't apply here (move it with '+10%', 'x0.5' or '=0.5')"
    if cur is None:
        return None, f"{shown}: no number to set in {u}"
    if du not in (u, ""):
        return None, f"{shown}, not {u}"
    return "value", (cur + x) if rel else x


def _tol(text, target):
    """How close the display must come: half its last shown digit (or 0.5 % of the target)."""
    t = str(text or "").replace(",", "")
    m = re.search(r"\d+(?:\.(\d+))?", t)
    res = 10.0 ** -len(m.group(1)) if m and m.group(1) else 1.0
    low = t.lower()
    if re.search(r"\d\s*k(hz)?\b", low) or (re.search(r"\d\s*(s|sec)\b", low) and "ms" not in low):
        res *= 1000.0                                           # kHz / seconds displays, compared in Hz / ms
    return max(0.51 * res, 0.005 * abs(float(target)), 1e-9)


def _bridge_target(target, text):
    try:
        return _mix()._bridge_target(target, text)
    except Exception:
        return float(target)


def set_knob(index, slot, p, kind, target):
    """Set one knob live -> (text FL shows, before value, note or None). 'value' targets: FL's own display search
    first, checked by reading the display back; when it can't get there, our own search (_search_knob). Never a
    jump to the far end: a target outside the knob's range stops at the nearest end and the note says so."""
    before = float(p.get("value") or 0.0)
    if kind != "value":
        r = H["fl_plugin_set"](int(index), int(p["param"]), float(target), int(slot))
        return r.get("text"), before, None
    target = float(target)
    cur, _u = _num(p.get("text"))
    if cur is None:
        raise ValueError(f"'{p.get('name')}' shows no number ('{p.get('text')}')")
    if abs(cur - target) <= _tol(p.get("text"), target):
        return p.get("text"), before, None
    extra = []
    try:
        r = H["fl_plugin_set_value"](int(index), int(p["param"]), _bridge_target(target, p.get("text")), int(slot))
        got, _ = _num(r.get("text"))
        if got is not None and abs(got - target) <= _tol(r.get("text"), target):
            return r.get("text"), before, None
        if got is not None and r.get("value") is not None:
            extra.append((float(r["value"]), got, r.get("text")))
    except Exception:
        pass                                                    # FL restores the knob on a failed search
    return _search_knob(index, slot, p, target, before, cur, extra)


def _search_knob(index, slot, p, target, x0, v0, extra=()):
    """Find the 0..1 knob position whose display shows `target` (any monotonic knob, rising or falling; linear,
    log or dB curves): secant steps from where the knob is, then bracketed secant / bisection, at most
    SEARCH_MAX_SETS round trips. Out of range: the nearest end, said in the note. Any error: the knob goes back."""
    # PC-VERIFY: Fruity Compressor threshold '-36 dB', Fruity Limiter ceiling '-1 dB', an EQ gain '-6 dB' and Delay 3
    # feedback '-3%' land within the display's last digit in <= 8 round trips; out of range stops at the near end
    param, idx, sl = int(p["param"]), int(index), int(slot)
    pts = [(float(x0), float(v0), p.get("text"))] + [q for q in extra if abs(q[0] - float(x0)) > 1e-9]
    state = {"sets": 0, "at": pts[-1][0]}

    def probe(x):
        x = _clamp01(x)
        r = H["fl_plugin_set"](idx, param, x, sl)
        state["sets"] += 1; state["at"] = x
        v, _ = _num(r.get("text"))
        if v is None:
            raise ValueError(f"FL shows no number for '{p.get('name')}' ('{r.get('text')}')")
        pts.append((x, v, r.get("text")))

    def close(q):
        return abs(q[1] - target) <= _tol(q[2], target)
    clamped, bisect, width = False, False, None
    try:
        while state["sets"] < SEARCH_MAX_SETS:
            if any(close(q) for q in pts):
                break
            lo = [q for q in pts if q[1] < target]; hi = [q for q in pts if q[1] > target]
            if lo and hi:                                       # bracketed: secant inside, bisection when slow
                a = max(lo, key=lambda q: q[1]); b = min(hi, key=lambda q: q[1])
                w = abs(a[0] - b[0])
                if w < 1e-6:
                    break
                if width is not None and w > 0.5 * width:
                    bisect = True
                width = w
                if bisect:
                    x = (a[0] + b[0]) / 2.0; bisect = False
                else:
                    x = a[0] + (target - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                    m_lo, m_hi = min(a[0], b[0]) + 0.05 * w, max(a[0], b[0]) - 0.05 * w
                    x = max(m_lo, min(m_hi, x))
                probe(x); continue
            near = sorted(pts, key=lambda q: abs(q[1] - target))
            moved = [q for q in pts if q[1] != v0]
            if not moved:                                       # nothing seen move yet (first step, a flat stretch):
                toward = 1.0 if x0 <= 0.5 else -1.0             # small steps around where it is, middle side first
                steps = [toward * d for d in (0.05, 0.15)] + [-toward * d for d in (0.05, 0.15)] + [toward * 0.35]
                x = next((_clamp01(x0 + d) for d in steps
                          if not any(abs(q[0] - _clamp01(x0 + d)) < 1e-9 for q in pts)), None)
                if x is None:
                    break
            else:
                (xa, va, _ta) = near[0]
                xb, vb, _tb = next((q for q in near[1:] if q[1] != va and q[0] != xa), (xa, va, None))
                if vb == va:
                    break
                x = xa + (target - va) * (xb - xa) / (vb - va)
            x = _clamp01(x)
            if any(abs(q[0] - x) < 1e-9 for q in pts):          # already there: the target is past this end
                clamped = x in (0.0, 1.0)
                break
            probe(x)
        best = min(pts, key=lambda q: abs(q[1] - target))
        if abs(state["at"] - best[0]) > 1e-9:
            probe(best[0])
            best = pts[-1]
    except Exception:
        try:
            H["fl_plugin_set"](idx, param, float(x0), sl)
        except Exception:
            pass
        raise
    note = None
    if not close(best) and all(q[1] == v0 for q in pts):        # the display never moved: put it back
        if abs(state["at"] - float(x0)) > 1e-9:
            H["fl_plugin_set"](idx, param, float(x0), sl)
        raise ValueError(f"FL's display of '{p.get('name')}' didn't change when the knob moved: left unchanged")
    if not close(best):
        lo = [q for q in pts if q[1] < target]; hi = [q for q in pts if q[1] > target]
        if clamped or (not (lo and hi) and best[0] in (0.0, 1.0)):
            note = f"clamped to the knob's range: it shows '{best[2]}' (asked {target:g})"
        else:
            note = f"closest FL shows: '{best[2]}' (asked {target:g})"
    return best[2], float(x0), note


def _find_knobs(params, roles, key):
    """The knobs a knobs= key means: an exact name, then a role (up to 3), then the shortest name holding the key as
    a whole word ('feedback' -> 'Feedback level'). -> ([param...], note or None)."""
    k = str(key).lower().strip()
    ps = [p for p in params if str(p.get("name", "")).lower().strip() == k]
    if ps:
        return ps, None
    if roles.get(k):
        return roles[k][:3], None
    pat = re.compile(r"(^|[^a-z0-9])" + re.escape(k) + r"($|[^a-z0-9])")
    hits = [p for p in params if pat.search(str(p.get("name", "")).lower())]
    if not hits:
        return [], None
    pick = min(hits, key=lambda p: len(str(p.get("name", ""))))
    others = [p["name"] for p in hits if p is not pick][:3]
    return [pick], (f"'{key}' = '{pick['name']}'" + (f" (also: {', '.join(others)})" if others else ""))


def plugin_knobs(index, slot=-1, data=None):
    """Any plugin's knobs by role, read live: ({role: [param...]}, plugin name, params [...]). slot -1 = the
    channel's instrument, 0..9 = an effect on mixer track `index`. New plugins are remembered in the state folder."""
    data = data or _data()
    try:
        live = H["fl_plugin_params"](int(index), int(slot))
    except Exception:
        return {}, None, []
    params = live.get("params") or []
    out = {}
    for p in params:
        r = _role_of(p.get("name", ""), data)
        if r:
            out.setdefault(r, []).append(p)
    _remember_plugin(live.get("plugin"), out, len(params))
    return out, live.get("plugin"), params


def _unnamed(params):
    """A plugin that names its knobs only 'Param 1', 'Param 2' ... (some VSTs): nothing to understand by name."""
    return bool(params) and sum(1 for p in params if re.match(r"(?i)^\s*(param(eter)?|p)\s*\d+\s*$",
                                                          str(p.get("name", "")))) > 0.6 * len(params)


def instrument_controls(channel, data=None):
    """The channel's instrument controls by role ({role: [{param, name, value, text}]}), read live. {} for a sampler
    channel or a plugin FL exposes no named controls of."""
    roles, plugin, _params = plugin_knobs(channel, -1, data)
    return roles, plugin


def _channel_row(channel):
    st = H["fl_state"]()
    row = next((c for c in st.get("channels") or [] if int(c.get("index", -1)) == int(channel)), None)
    if row is None:
        raise ValueError(f"no channel {channel} in the open FL project")
    shared = [c["name"] for c in st.get("channels") or [] if c.get("insert") == row.get("insert")
              and int(c["index"]) != int(channel)]
    return row, shared, st


def _measure(channel):
    """The channel alone through FL's own export: preview numbers + a measurement for intent_verify (taken now: the
    next preview overwrites the WAV)."""
    r = H["fl_preview"](channels=[int(channel)], keep=True)
    wav = r.pop("wav", None)
    if wav:
        try:
            if r.get("lufs") is not None:
                r["m"] = _mix()._measure_any(wav)
        except Exception:
            r["m"] = None
        try:
            os.remove(wav)
        except OSError:
            pass
    return r


def _project():
    p, _ = H["_open_project_guess"]()
    return p


def _undo_store(project, channel, entry=None, pop=False):
    st = H["_state"]().get(UNDO_KEY) or {}
    key = f"{os.path.normcase(os.path.abspath(project or '?'))}|{int(channel)}"
    if pop:
        got = st.pop(key, None); H["_state"]({UNDO_KEY: st}); return got
    if entry is None:
        return st.get(key)
    prev = st.get(key) or {}
    for k in ("params", "mix", "faders", "added", "sampler"):
        prev[k] = (prev.get(k) or []) + (entry.get(k) or [])
    st[key] = prev
    H["_state"]({UNDO_KEY: st})
    return prev


def _shape_words(channel, words=None, amount="medium", check=True, dry_run=False):
    """Make one channel sound <words> (see the module doc). -> what moved, the measured result, how to undo."""
    T0 = time.time(); steps = {}

    def mark(s, t=[T0]):
        steps[s] = round(time.time() - t[0], 2); t[0] = time.time()
    channel = int(channel)
    project = _project()
    data = _data()
    pl = plan(words, amount, data)
    row, shared, st = _channel_row(channel)
    insert = row.get("insert")
    ctl, plugin = instrument_controls(channel, data)
    mark("plan")
    inst_moves, held = [], []
    dark = DARK_INTENT in pl["intents"]
    for role, share in pl["inst"].items():
        for p in (ctl.get(role) or [])[:3]:                  # several oscillators: up to 3 of each role
            if dark and share > 0 and role in ("decay", "sustain", "release") and FILTER_ENV.search(str(p["name"])):
                held.append(f"{p['name']} (a longer/higher filter envelope opens the top end)")
                continue
            kind, tgt = knob_target(p, role, share=share)
            if kind is None:
                held.append(f"{p['name']} {tgt}")
                continue
            inst_moves.append({"param": p["param"], "name": p["name"], "role": role, "from": float(p.get("value") or 0),
                               "kind": kind, "to": round(tgt, 4), "text_before": p.get("text"), "_p": p})
    if held:
        pl["notes"].append("not moved: " + "; ".join(held[:4]))
    if dark and plugin and pl["inst"].get("cutoff", 0.0) < 0 and not any(m["role"] == "cutoff" for m in inst_moves):
        deeper = False                                     # no instrument cutoff to lower: the EQ does it alone
        for e in pl["eq"]:
            if e["type"] == "High shelf" and e["gain_db"] < 0:
                e["gain_db"] = round(max(-12.0, e["gain_db"] * DARK_EQ_DEEPER), 2); deeper = True
        if deeper:
            pl["notes"].append("the instrument's cutoff couldn't go lower: the EQ's high cut is deeper instead")
    smp_moves = _sampler_moves(pl["inst"]) if not plugin and H.get("sampler_shape") else {}
    fx_ops = []
    if insert is None or int(insert) <= 0:
        pl["notes"].append("this channel plays straight into the Master, so effects would change the whole song: "
                           "only its own instrument controls were shaped (give it a mixer track to shape more)")
    else:
        eq_ops = [dict(op="eq", track=int(insert), hz=e["hz"], gain_db=e.get("gain_db", 0), type=e["type"],
                       **({"q": e["q"]} if e.get("q") else {}), why=f"{'+'.join(pl['words'])}") for e in pl["eq"]]
        fx_only = [dict(op="plugin", track=int(insert), plugin=f["plugin"], controls=f["controls"],
                        why=f"{'+'.join(pl['words'])}") for f in pl["fx"]]
        # darker: the EQ is added after the reverb/delay (effects are added in op order) so their tails are cut too
        fx_ops = fx_only + eq_ops if pl.get("eq_last") else eq_ops + fx_only
    out = {"channel": channel, "name": row.get("name"), "instrument": plugin or "sampler", "words": pl["words"],
           "amount": pl["amount"], "mixer_track": insert}
    if shared and fx_ops:
        out["shared_track"] = f"mixer track {insert} also carries {', '.join(shared[:4])}: they get the effects too"
    if not inst_moves and plugin and plugin.lower() not in ("sampler", ""):
        pl["notes"].append(f"{plugin} shows no controls by name (cutoff, attack, detune...): shaped with effects only")
    if smp_moves and dry_run:
        out["sampler_moves"] = smp_moves
    if dry_run:
        out.update(plan={"instrument": [{k: m[k] for k in ("name", "role", "text_before", "kind", "to")}
                                        for m in inst_moves],
                         "effects": fx_ops}, notes=pl["notes"] or None)
        return out
    before = _measure(channel) if check else None
    mark("measure_before")
    undo_params, done_inst = [], []
    for m in inst_moves:
        try:
            txt, before_v, note = set_knob(channel, -1, m["_p"], m["kind"], m["to"])
            undo_params.append({"index": channel, "slot": -1, "param": m["param"], "before": before_v})
            done_inst.append(f"{m['name']}: {m['text_before']} -> {txt}" + (f" ({note})" if note else ""))
        except Exception as e:
            pl["notes"].append(f"{m['name']}: {str(e)[:60]}")
    smp_undo = None
    if smp_moves and project:
        try:
            sr = H["sampler_shape"](project, channel, smp_moves)
            smp_undo = sr.get("undo")
            done_inst += [f"sampler {d['field']}: {d.get('before')} -> {d.get('after')}" for d in sr.get("done") or []]
            if sr.get("notes"):
                pl["notes"] += list(sr["notes"])[:3]
        except Exception as e:
            pl["notes"].append(f"sampler settings: {str(e)[:80]}")
    mark("instrument")
    M = _mix()
    mix_res, mix_undo = None, []
    if fx_ops:
        n0 = len((H["_state"]().get("mix_engineer") or {}).get("applied") or [])
        mix_res = M.apply_ops(fx_ops, label=f"sound shape {pl['words']}", project=project)
        me = dict(H["_state"]().get("mix_engineer") or {})
        mix_undo = (me.get("applied") or [])[n0:]
        if mix_undo:                                       # ours to undo (fl_sound_shape undo=true), not the mix
            me["applied"] = (me.get("applied") or [])[:n0]  # engineer's: its revert would put these knobs back
            H["_state"]({"mix_engineer": me})               # again later, over whatever was set since
        # an EQ band width FL shows in a unit the map can't convert to (a % instead of octaves / Q) is set by the
        # plugin map's curve inside apply_ops (plugin_set_named; its 'effects' line says unverified, listen), and
        # undone with the other mix moves
    mark("effects")
    added = [a for a in (mix_res or {}).get("added_effects") or []]
    out["instrument_moves"] = done_inst or None
    if mix_res:
        out["effects"] = [f"{d.get('plugin', d['op'])}: " + ", ".join(d.get("set") or []) for d in mix_res["done"]] or None
        miss = [f"{d.get('plugin')}: {x.get('control')} ({x.get('why')})" for d in mix_res["done"]
                for x in (d.get("skipped") or [])]
        if miss:
            out["controls_not_set"] = miss[:8]
        if mix_res.get("skipped"):
            out["effects_skipped"] = [f"{s['op'].get('plugin', s['op'].get('op'))}: {s['why']}" for s in mix_res["skipped"]][:6]
        if added:
            out["added_effects"] = added
    faders = []
    if check and before and before.get("lufs") is not None:
        after = _measure(channel)
        mark("measure_after")
        out["measured"] = {"before_lufs": before.get("lufs"), "after_lufs": after.get("lufs"),
                           "before_centroid_hz": before.get("centroid_hz"), "after_centroid_hz": after.get("centroid_hz")}
        if after.get("lufs") is not None and insert and int(insert) > 0:
            diff = float(after["lufs"]) - float(before["lufs"])
            if abs(diff) >= 0.5:
                try:
                    cur = H["BRIDGE"].call("mixer_set", track=int(insert)).get("volume_db")
                    if cur is not None and float(cur) > -90:
                        mv = max(-LEVEL_MATCH_MAX_DB, min(LEVEL_MATCH_MAX_DB, -diff))
                        H["BRIDGE"].call("mixer_set", track=int(insert), volume_db=min(5.6, float(cur) + mv))
                        faders.append({"track": int(insert), "before": float(cur)})
                        out["level_matched"] = f"mixer track {insert} {mv:+.1f} dB (the change made it {diff:+.1f} LU)"
                except Exception as e:
                    out["level_matched"] = f"not done ({str(e)[:60]})"
        if pl["intents"] and before.get("m") and after.get("m"):
            try:
                v = M.intent_verify(before["m"], after["m"], pl["intents"])
                out["result"] = [r["intent"].replace("_", " ") + ": " + r["say"] for r in v["results"]]
            except Exception as e:
                out["result"] = [f"couldn't measure ({str(e)[:60]})"]
        mark("verify")
    elif check:
        out["measured"] = "the channel was silent alone before the change (no notes playing?): nothing to compare"
    _undo_store(project, channel, {"params": undo_params, "mix": mix_undo, "faders": faders,
                                   "added": [[a] for a in added], "sampler": [smp_undo] if smp_undo else []})
    if pl["notes"]:
        out["notes"] = pl["notes"]
    out["undo"] = f"fl_sound_shape(channel={channel}, undo=true) puts it all back"
    out["listen"] = "fl_preview(channels=[%d]) or play it in FL" % channel
    out["steps_s"] = steps
    return out


SAMPLER_FROM_ROLES = {"attack": ("attack_ms", 6.0), "decay": ("decay_ms", 6.0), "release": ("release_ms", 6.0)}


def _sampler_moves(inst):
    """A word plan's instrument nudges -> Sampler channel moves (envelope times as ratios, the filter in octaves)."""
    mv = {}
    for role, share in (inst or {}).items():
        if role in SAMPLER_FROM_ROLES:
            f, k = SAMPLER_FROM_ROLES[role]
            mv[f] = f"x{round(2 ** (share * k), 3)}"
        elif role == "cutoff":
            mv["cutoff"] = round(share * 6, 2)               # octaves
        elif role == "resonance":
            mv["resonance_pct"] = round(share * 100, 1)
    return mv


def fl_sound_shape(channel, words=None, amount="medium", check=None, undo=False, dry_run=False, knobs=None,
                   plugin=None, variations=0, pick=None, layer=None, sound=None):
    """See the module doc; knobs / variations / layer are the other modes."""
    channel = int(channel)
    if check is None:                                     # the before/after renders: only when asked or turned on
        check = bool(H["auto_renders"]()) if H.get("auto_renders") else False
    if undo:
        return _undo(channel, _project())
    if layer:
        return _layer(channel, layer, sound)
    if knobs is not None:
        return _knobs(channel, knobs, plugin)
    if variations and not pick:
        return _variations(channel, words, int(variations))
    return _shape_words(channel, words, pick or amount, check, dry_run)


def _targets(channel, plugin=None):
    """[(label, index, slot)] the knob mode may touch: the instrument (slot -1) and the effects on its mixer track."""
    row, _shared, st = _channel_row(channel)
    out = [("instrument", int(channel), -1)]
    ins = row.get("insert")
    mx = next((t for t in st.get("mixer") or [] if ins is not None and int(t.get("index", -2)) == int(ins)), None)
    for e in (mx or {}).get("effects") or []:
        if ins is not None and int(ins) > 0:
            out.append((e.get("name") or f"slot {e.get('slot')}", int(ins), int(e.get("slot"))))
    if plugin is None or str(plugin).lower() in ("instrument", "inst"):
        return out[:1] if plugin else out
    w = str(plugin).lower()
    m = re.match(r"^slot\s*(\d+)$", w)
    hit = [t for t in out if (m and t[2] == int(m.group(1))) or (not m and w in t[0].lower())]
    if not hit:
        raise ValueError(f"no '{plugin}' on this channel or its mixer track (there: {', '.join(t[0] for t in out)})")
    return hit


def _knobs(channel, knobs, plugin=None):
    """knobs='list': every understood knob (by role) of the instrument and its track's effects, any plugin.
    knobs={role, knob name or a word of it: move}: set them (the grammar of parse_move)."""
    data = _data()
    tg = _targets(channel, plugin if knobs != "list" or plugin else None)
    if knobs == "list" or knobs == {}:
        rows = []
        for label, idx, slot in tg:
            roles, name, params = plugin_knobs(idx, slot, data)
            rows.append({"plugin": name or label, "where": "instrument" if slot < 0 else f"track {idx} slot {slot}",
                         "knobs": {r: [f"{p['name']} = {p.get('text')}" for p in v][:4] for r, v in roles.items()},
                         "unnamed": _unnamed(params) or None, "all": len(params)})
        return {"channel": channel, "plugins": rows,
                "set_with": "fl_sound_shape(channel, knobs={'cutoff': '-1 oct', 'attack': '+20 ms', 'mix': '30%'}, "
                            "plugin='instrument' | an effect's name | 'slot N')"}
    if not isinstance(knobs, dict):
        raise ValueError("knobs: 'list' or {role or knob name: move}")
    if plugin is None:
        tg = tg[:1]                                           # the instrument unless an effect is named
    project = _project()
    done, skipped, undo = [], [], []
    for label, idx, slot in tg:
        roles, name, params = plugin_knobs(idx, slot, data)
        if _unnamed(params):
            skipped.append({"plugin": name, "why": "this plugin names its knobs only 'Param N' to FL: open its window"})
            continue
        for key, mv in knobs.items():
            k = str(key).lower().strip()
            ps, matched = _find_knobs(params, roles, key)
            if not ps:
                skipped.append({"plugin": name, "knob": key, "why": "not found (fl_sound_shape(knobs='list') shows them)"})
                continue
            for p in ps:
                kind, tgt = knob_target(p, k if k in roles else _role_of(p["name"], data), move=mv)
                if kind is None:
                    skipped.append({"plugin": name, "knob": p["name"], "why": tgt}); continue
                try:
                    txt, before, note = set_knob(idx, slot, p, kind, tgt)
                    undo.append({"index": idx, "slot": slot, "param": p["param"], "before": before})
                    extra = "; ".join(x for x in (matched, note) if x)
                    done.append(f"{name}: {p['name']} {p.get('text')} -> {txt}" + (f" ({extra})" if extra else ""))
                except Exception as e:
                    skipped.append({"plugin": name, "knob": p["name"], "why": str(e)[:80]})
    if undo:
        _undo_store(project, channel, {"params": undo})
    return {"channel": channel, "set": done or None, "skipped": skipped or None,
            "undo": f"fl_sound_shape(channel={channel}, undo=true)" if undo else None}


def _variations(channel, words, n=3):
    """Hear-and-compare versions of the same words at rising amounts: each is applied, the channel measured alone,
    then put back; pick='medium' (or the version's amount) applies the one you like."""
    amounts = ["subtle", "medium", "heavy", "insane"][:max(2, min(4, n))]
    rows, T0 = [], time.time()
    project = _project()
    prior = _undo_store(project, channel, pop=True)            # the user's earlier shaping: kept, not undone with
    try:                                                       # the versions (and still undoable afterwards)
        return _variations_run(channel, words, amounts, rows, T0)
    finally:
        cur = _undo_store(project, channel, pop=True)
        for e in (prior, cur):
            if e:
                _undo_store(project, channel, e)


def _variations_run(channel, words, amounts, rows, T0):
    base = _measure(channel)
    for i, a in enumerate(amounts):
        r = _shape_words(channel, words, a, check=False)
        m = _measure(channel)
        row = {"version": "ABCD"[i], "amount": a, "lufs": m.get("lufs"), "centroid_hz": m.get("centroid_hz")}
        if base.get("m") and m.get("m") and r.get("words"):
            try:
                pl = plan(words, a)
                if pl["intents"]:
                    v = _mix().intent_verify(base["m"], m["m"], pl["intents"])
                    row["result"] = [x["say"] for x in v["results"]]
            except Exception:
                pass
        rows.append(row)
        last = i == len(amounts) - 1                           # back to the original before the next version;
        _undo(channel, _project(), keep_effects=not last)      # after the last, the added effects go too
    return {"channel": channel, "words": words, "original": {"lufs": base.get("lufs"), "centroid_hz": base.get("centroid_hz")},
            "versions": rows, "pick": f"fl_sound_shape(channel={channel}, words='{words}', pick='<amount>')",
            "note": "every version was measured alone and put back (the effects it added are removed again)",
            "took_s": round(time.time() - T0, 1)}


LAYERS = {"octave": 12, "up": 12, "sub": -12, "down": -12, "double": 0, "unison": 0, "fifth": 7, "third": 4}


def _layer(channel, layer, sound=None):
    """A layer for a bigger sound: a copy of the channel (or `sound`, a preset/.fst, on a copy) playing the same notes
    an octave up / a sub octave down / a fifth up / doubled, a little quieter, in ONE project edit."""
    key = str(layer).lower().strip()
    if key not in LAYERS:
        raise ValueError(f"layer: {' | '.join(LAYERS)}")
    shift = LAYERS[key]
    project = _project()
    if not project:
        raise RuntimeError("save the song in FL once (inside the allowed folders) so it can be edited")
    res = {}

    def run():
        add = H["flp_add_channel"](project, project, int(channel))
        new = int(add["new_channel"])
        if sound:
            pre = sound
            if not os.path.isfile(str(pre)):
                hits = H["fl_find_presets"](str(sound), limit=5).get("presets") or []
                if not hits:
                    raise ValueError(f"no preset or sound named '{sound}' on this PC (fl_sound_catalog lists them)")
                pre = hits[0]
            H["flp_load_plugin"](project, project, pre, channel=new)
            res["sound"] = os.path.basename(str(pre))
        d = H["_load"](project); ev = H["_events"](d)
        pats = H["_patterns"](d, ev); n = 0
        for pid, pt in pats.items():
            notes = [H["_rec_to_note"](r) for r in pt["notes"]]
            src = [x for x in notes if int(x["ch"]) == int(channel)]
            if not src:
                continue
            extra = [{"pos": x["pos"], "len": x["len"], "key": max(0, min(131, int(x["key"]) + shift)),
                      "vel": max(1, int(int(x["vel"]) * 0.8)), "ch": new} for x in src]
            d = H["_write_pattern_notes"](d, H["_events"](d), pid, notes + extra); n += len(extra)
        H["_save"](d, project)
        res.update(new_channel=new, notes=n)
        return {"output": project}
    H["_with_open_project"](project, project, run)
    try:
        nm = (_channel_row(channel)[0].get("name") or "Layer")
        H["BRIDGE"].call("batch", ops=json.dumps([{"op": "channel", "index": res["new_channel"],
                                                     "name": f"{nm} {key}"}]), label="layer")
    except Exception:
        pass
    res.update(channel=channel, layer=key, semitones=shift,
               note=("it shares the original's mixer track; fl_sound_shape the new channel to shape it on its own"),
               undo="flp_versions / flp_restore puts the song back (or flp_remove_channels the new channel)")
    return res


def _undo(channel, project, keep_effects=False):
    u = _undo_store(project, channel, pop=True)
    if not u:
        return {"channel": channel, "undone": False, "why": "nothing shaped on this channel in this project yet"}
    done, errs = 0, []
    for p in reversed(u.get("params") or []):
        try:
            H["fl_plugin_set"](p["index"], p["param"], p["before"], p.get("slot", -1)); done += 1
        except Exception as e:
            errs.append(str(e)[:60])
    for m in reversed(u.get("mix") or []):
        try:
            if m.get("kind") == "param":
                H["fl_plugin_set"](m["track"], m["param"], m["before"], m["slot"]); done += 1
        except Exception as e:
            errs.append(str(e)[:60])
    for f in reversed(u.get("faders") or []):
        try:
            H["BRIDGE"].call("mixer_set", track=f["track"], volume_db=f["before"]); done += 1
        except Exception as e:
            errs.append(str(e)[:60])
    for su in reversed(u.get("sampler") or []):
        try:
            H["sampler_undo"](project, channel, su); done += 1
        except Exception as e:
            errs.append(f"sampler: {str(e)[:60]}")
    removed = []
    adds = []
    for a in u.get("added") or []:
        s = a[0] if isinstance(a, list) else a
        mt = re.match(r"^(.+) on track (\d+) slot (\d+)$", str(s))
        if mt:
            adds.append((int(mt.group(2)), int(mt.group(3)), mt.group(1)))
    if adds and keep_effects:
        _undo_store(project, channel, {"added": [[f"{p} on track {t} slot {s_}"] for t, s_, p in adds]})
        adds = []
    if adds and project:
        rm = H["TOOLS"]["flp_remove_effect"][0]
        for t, s, plug in sorted(adds, key=lambda x: -x[1]):      # highest slot first
            try:
                rm(project=project, output=project, track=t, slot=s); removed.append(f"{plug} (track {t} slot {s})")
            except Exception as e:
                errs.append(f"{plug}: {str(e)[:60]}")
    r = {"channel": channel, "undone": True, "restored": done, "removed_effects": removed or None}
    if errs:
        r["problems"] = errs[:5]
    return r


def register(ns):
    global H
    H = ns
    S = ns["_schema"]
    STR, BOOL, INT = ns["_STR"], ns["_BOOL"], ns["_INT"]
    ns["TOOLS"]["fl_sound_shape"] = (
        fl_sound_shape, "Make a channel sound better or insane, live in the open FL: words='warm huge', 'dark lofi', "
        "'bright punchy', 'dreamy wide', 'gritty', 'clean tight', 'insane'. Nudges the instrument's own controls "
        "(cutoff, attack, detune...) and builds a stock-effect chain on its mixer track; check=true measures before/"
        "after, keeps the level matched and says what was achieved. undo=true puts it all back. knobs: '-36 dB', "
        "'2000 Hz', '30%' set; '+3 dB', 'by -3 dB', 'up 2', 'down 20 ms', '-3%', '-1 oct' move; 'x0.5' multiplies; "
        "'=0.5' raw knob; out of range stops at the nearest end (said).",
        S(channel=INT("channel number"), words=STR("what it should sound like", True),
          amount=STR("subtle | medium | heavy | insane (default medium)", True),
          check=BOOL("measure before/after with two renders + level match (~18 s; default: only when flh_auto_renders is "
                     "on)", True),
          undo=BOOL("put this channel back as it was", True), dry_run=BOOL("only the plan", True),
          knobs=ns["_p"](["string", "object"], "'list', or {knob: '-36 dB' sets, '+3 dB' '-1 oct' 'x0.5' move}", True),
          plugin=STR("knobs: 'instrument' (default), an effect's name or 'slot N'", True),
          variations=INT("compare 2-4 versions at rising amounts (measured, put back)", True),
          pick=STR("apply this version's amount after variations", True),
          layer=STR("octave | sub | fifth | double: a layer playing the same notes", True),
          sound=STR("layer: a preset / .fst for the layer instead of a copy", True)))
    try:
        ns["ESSENTIAL_TOOLS"].add("fl_sound_shape")
        ns["ESSENTIAL_PARAMS"]["fl_sound_shape"] = ["channel", "words", "amount", "knobs", "undo"]
        ns["SHORT_DESC"]["fl_sound_shape"] = ("Make a channel sound better/insane from words ('warm huge', 'dark lofi', "
                                              "'insane'): instrument knobs + effect chain, live; measured; undo=true.")
    except Exception:
        pass
