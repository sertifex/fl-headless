"""Mix engineer for fl-headless: plugin maps (named controls with real units), masking detector, reference matching,
intent verification, an engineer that proposes (and optionally applies) prioritized mix moves, and platform
loudness previews.

Server functions are looked up through the live namespace H (set by register) at call time; nothing here imports
the main file. Data lives in data/plugin_maps/*.json (shipped, unverified), data/mixeng/*.json (genre norms,
intents) and data/platforms.json; maps learned on the user's PC go to <state folder>/plugin_maps and override
the shipped ones.

Plugin map format (one file per plugin):
  {"plugin": "Fruity Parametric EQ 2", "aliases": [...], "edition": "fruity", "roles": ["eq"], "source": "shipped"|
   "learned", "verified": bool, "controls": {"band2_gain_db": {"index": 1, "fl_name": "Band 2 level", "unit": "dB",
   "curve": "linear"|"log"|"pow"|"ratio_inv"|"enum"|"bool"|"table", "min": -18, "max": 18, "exp": 2,
   "options": [...], "table": [[norm, value], ...], "verified": false}}}
"""
import fnmatch
import glob
import json
import math
import os
import re
import struct
import sys
import time

import flh_common as C

H = {}                                                         # server namespace, set by register()
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
MAP_DIR = os.path.join(DATA, "plugin_maps")
_CACHE = {}

THIRD_OCT = [31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
             2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000]
BANDS6 = [("sub", 20, 60), ("bass", 60, 250), ("low_mid", 250, 800), ("mid", 800, 2500), ("presence", 2500, 6000),
          ("air", 6000, 16000)]
WIDTH_BANDS = [("low", 20, 150), ("low_mid", 150, 800), ("mid", 800, 4000), ("high", 4000, 16000)]


# ============================================================================= small helpers
def _json(path):
    st = os.stat(path)
    hit = _CACHE.get(path)
    if hit and hit[0] == st.st_mtime:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    _CACHE[path] = (st.st_mtime, d)
    return d


def _data(name):
    return _json(os.path.join(DATA, name))


def _state_dir():
    return os.path.dirname(H["STATE"]) if H.get("STATE") else os.path.join(os.path.expanduser("~"), "fl-headless")


def _user_map_dir():
    return os.path.join(_state_dir(), "plugin_maps")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def _norm_name(s):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split())


def _r(v, n=1):
    return None if v is None else round(float(v), n)


def _deep_merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _bridge(cmd, **a):
    return H["BRIDGE"].call(cmd, **a)


def _steps():
    t0 = [time.time()]; out = {}

    def mark(name):
        now = time.time(); out[name] = round(out.get(name, 0) + now - t0[0], 3); t0[0] = now
    return out, mark


def _progress(**k):
    try:
        H["_job_progress"](**k)
    except Exception:
        pass


# ============================================================================= units and display text
_UNIT_RE = re.compile(r"(?i)(khz|hz|ms|sec|s\b|db|%|:1|oct|st\b|cents?|x\b|q\b|bpm)")


def parse_display(text):
    """FL display text -> (value in base unit, base unit) or (None, label) for non-numeric text.
    '2.4 kHz' -> (2400, 'Hz'); '1.2 s' -> (1200, 'ms'); '-3.5 dB' -> (-3.5, 'dB'); '4.0:1' -> (4, 'ratio');
    'Inf:1' / '∞' -> (inf, 'ratio'); 'Off' -> (None, 'Off')."""
    t = str(text or "").replace(",", "").strip()
    low = t.lower()
    if re.search(r"(inf|∞)", low) and (":" in low or "ratio" in low or low.strip() in ("inf", "∞")):
        return float("inf"), "ratio"
    m = re.search(r"[-+]?\d*\.?\d+(?:e[-+]?\d+)?", t, re.I)
    if not m:
        return None, t
    v = float(m.group(0)); rest = t[m.end():].strip().lower()
    if re.match(r"(?i)^:\s*1", rest):
        return v, "ratio"
    if rest.startswith("khz") or (rest.startswith("k") and not rest.startswith("kb")):
        return v * 1000.0, "Hz"
    if rest.startswith("hz"):
        return v, "Hz"
    if rest.startswith("ms"):
        return v, "ms"
    if rest.startswith("sec") or re.match(r"^s\b", rest):
        return v * 1000.0, "ms"
    if rest.startswith("db"):
        return v, "dB"
    if rest.startswith("%"):
        return v, "%"
    if rest.startswith("oct"):
        return v, "oct"
    if rest.startswith("st") or rest.startswith("semi"):
        return v, "st"
    if rest.startswith("cent"):
        return v, "cents"
    if rest.startswith("x"):
        return v, "x"
    if rest.startswith("q") or low.startswith("q"):
        return v, "Q"
    return v, (rest.split()[0] if rest else "")


def q_to_oct(q):
    q = max(1e-3, float(q))
    return 2.0 / math.log(2) * math.asinh(1.0 / (2.0 * q))


def oct_to_q(n):
    n = max(1e-3, float(n))
    return math.sqrt(2 ** n) / (2 ** n - 1)


def convert_unit(value, from_unit, to_unit):
    """Number in from_unit -> to_unit (Hz/kHz, ms/s, Q/oct, %/fraction); raises when there is no conversion."""
    f, t = (from_unit or "").lower(), (to_unit or "").lower()
    if f == t or not f or not t or t in ("display",):
        return float(value)
    v = float(value)
    if (f, t) in _UNIT_PAIRS:
        return _UNIT_PAIRS[(f, t)](v)
    raise ValueError(f"can't convert {from_unit} to {to_unit}")


# relations that hold for any plugin (same quantity, other scale). Not here on purpose: dB <-> % (a % on a level knob
# may be amplitude or a share of the knob's travel) and oct/Q <-> % (a % width has no fixed meaning): those are set
# by the plugin map's curve and said to be unverified (plugin_set_named), or refused for level knobs.
_UNIT_PAIRS = {("khz", "hz"): lambda x: x * 1000, ("hz", "khz"): lambda x: x / 1000,
               ("s", "ms"): lambda x: x * 1000, ("ms", "s"): lambda x: x / 1000,
               ("q", "oct"): q_to_oct, ("oct", "q"): oct_to_q,
               ("fraction", "%"): lambda x: x * 100, ("%", "fraction"): lambda x: x / 100,
               ("x", "%"): lambda x: x * 100, ("%", "x"): lambda x: x / 100,           # 1.5x = 150%
               ("st", "cents"): lambda x: x * 100, ("cents", "st"): lambda x: x / 100,
               ("ratio", "x"): lambda x: x, ("x", "ratio"): lambda x: x}


def bridge_unit(mp, key, value, from_unit, to_unit):
    """A plugin's own unit table (map 'unit_bridges'): e.g. Parametric EQ 2 shows a band's width as a % of the knob;
    a Q goes there via octaves. -> (value in to_unit, note) or None when the map has no bridge for this control."""
    f, t = (from_unit or "").lower(), (to_unit or "").lower()
    for b in (mp or {}).get("unit_bridges") or []:
        if str(b.get("to", "")).lower() != t or not fnmatch.fnmatch(str(key), str(b.get("controls", ""))):
            continue
        via = str(b.get("via") or "").lower()
        pts = sorted((float(a), float(c)) for a, c in b.get("points") or [])
        if len(pts) < 2:
            continue
        try:
            x = float(value) if f == via else convert_unit(value, f, via)
        except ValueError:
            continue
        x = max(pts[0][0], min(pts[-1][0], x))
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= x <= x1:
                y = y0 + (y1 - y0) * (x - x0) / ((x1 - x0) or 1.0)
                break
        else:
            y = pts[-1][1]
        note = (f"{key}: {_r(float(value), 2)} {from_unit} = {_r(x, 2)} {via} = {_r(y, 1)} {to_unit} by the plugin "
                f"map's table" + ("" if b.get("verified") else " (unverified: look at the curve, listen)"))
        return y, note
    return None


def units_convertible(from_unit, to_unit):
    """Can a number in from_unit be shown in to_unit (same unit, or a known relation)?"""
    f, t = (from_unit or "").lower(), (to_unit or "").lower()
    return f == t or not f or not t or t == "display" or (f, t) in _UNIT_PAIRS


def _bridge_target(target, text):
    """The bridge's display search reads the first number (x1000 for kHz): seconds displays need seconds."""
    t = str(text or "").lower()
    if re.search(r"\d\s*(s|sec)\b", t) and "ms" not in t:
        return float(target) / 1000.0
    return float(target)


def _value_with_unit(v):
    """3000 -> (3000, None); '3 kHz' -> (3000, 'Hz'); '-2.5dB' -> (-2.5, 'dB'); 'Peaking' -> ('Peaking', None)."""
    if isinstance(v, (int, float, bool)) or v is None:
        return v, None
    num, unit = parse_display(v)
    if num is None:
        return str(v), None
    return num, unit or None


# ============================================================================= plugin maps
def _map_files():
    out = {}
    for d in (MAP_DIR, _user_map_dir()):                       # user-local (learned) maps override shipped ones
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            try:
                m = _json(f)
            except Exception:
                continue
            out[_slug(m.get("plugin") or os.path.splitext(os.path.basename(f))[0])] = (f, m)
    return out


def plugin_map(plugin):
    """The map for a plugin name (FL's name or an alias): learned map merged over the shipped one, or None."""
    want = _norm_name(plugin)
    shipped = user = None
    for d, kind in ((MAP_DIR, "shipped"), (_user_map_dir(), "user")):
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            try:
                m = _json(f)
            except Exception:
                continue
            names = [_norm_name(m.get("plugin"))] + [_norm_name(a) for a in m.get("aliases", [])]
            if want in names:
                if kind == "shipped":
                    shipped = (f, m)
                else:
                    user = (f, m)
    if not shipped and not user:
        return None
    if shipped and user:
        merged = _deep_merge(shipped[1], user[1])
        sc, uc = shipped[1].get("controls", {}), user[1].get("controls", {})
        # a user entry the name check couldn't match on the PC never hides a shipped entry with a newer name
        gone = set(shipped[1].get("not_exposed") or {})          # removed from the shipped map (FL has no such
        merged["controls"] = {**sc, **{k: v for k, v in uc.items()   # control): an old user entry doesn't bring it back
                                       if k not in gone and not (v.get("missing_on_pc") and k in sc
                                                                 and sc[k].get("fl_name") != v.get("fl_name"))}}
        merged["file"] = user[0]; merged["shipped_file"] = shipped[0]
        return merged
    f, m = user or shipped
    return dict(m, file=f)


def plugin_maps():
    """Compact list: plugin -> {source, verified controls, total controls, file}."""
    out = {}
    for _, (f, m) in _map_files().items():
        mp = plugin_map(m["plugin"]) or m
        ctl = mp.get("controls", {})
        out[mp["plugin"]] = {"source": mp.get("source"), "edition": mp.get("edition"),
                             "controls": len(ctl), "verified": sum(1 for e in ctl.values() if e.get("verified")),
                             "file": mp.get("file")}
    return out


def to_norm(entry, value):
    """Control value in its unit -> FL's 0..1 parameter value, from the map entry's curve."""
    curve = entry.get("curve", "linear")
    if curve == "enum" or entry.get("unit") == "enum":
        opts = entry.get("options") or []
        if isinstance(value, str):
            low = value.lower()
            idx = next((i for i, o in enumerate(opts) if str(o).lower() == low), None)
            if idx is None:
                idx = next((i for i, o in enumerate(opts) if low in str(o).lower()), None)
            if idx is None:
                raise ValueError(f"'{value}' isn't one of {opts}")
        else:
            idx = int(value)
        on = entry.get("option_norms")
        if on and idx < len(on):
            return float(on[idx][1])
        return idx / max(1, len(opts) - 1)
    if curve == "bool":
        return 1.0 if value else 0.0
    v = float(value)
    if curve == "table" and entry.get("table"):
        tb = sorted((float(n), float(x)) for n, x in entry["table"] if x is not None)
        return _interp_inv(tb, v, log=entry.get("unit") == "Hz")
    lo, hi = float(entry.get("min", 0.0)), entry.get("max", 1.0)
    hi = float("inf") if hi in ("inf", None) else float(hi)
    if curve == "log":
        v = min(max(v, lo), hi)
        return _clip01(math.log(v / lo) / math.log(hi / lo))
    if curve == "ratio_inv":
        v = max(1.0, v)
        top = 1.0 if math.isinf(hi) else 1.0 - 1.0 / hi
        return _clip01((1.0 - 1.0 / v) / top) if not math.isinf(v) else 1.0
    span = (hi - lo) or 1.0
    x = _clip01((v - lo) / span)
    if curve == "pow":
        return _clip01(x ** (1.0 / float(entry.get("exp", 2.0))))
    return x


def from_norm(entry, norm):
    """FL's 0..1 parameter value -> control value in its unit (inverse of to_norm)."""
    n = _clip01(float(norm)); curve = entry.get("curve", "linear")
    if curve == "enum" or entry.get("unit") == "enum":
        opts = entry.get("options") or []
        on = entry.get("option_norms")
        if on:
            return min(on, key=lambda o: abs(float(o[1]) - n))[0]
        return opts[int(round(n * max(1, len(opts) - 1)))] if opts else None
    if curve == "bool":
        return n >= 0.5
    if curve == "table" and entry.get("table"):
        tb = sorted((float(a), float(b)) for a, b in entry["table"] if b is not None)
        return _interp(tb, n, log=entry.get("unit") == "Hz")
    lo, hi = float(entry.get("min", 0.0)), entry.get("max", 1.0)
    hi = float("inf") if hi in ("inf", None) else float(hi)
    if curve == "log":
        return lo * (hi / lo) ** n
    if curve == "ratio_inv":
        top = 1.0 if math.isinf(hi) else 1.0 - 1.0 / hi
        k = n * top
        return float("inf") if k >= 1.0 else 1.0 / (1.0 - k)
    if curve == "pow":
        n = n ** float(entry.get("exp", 2.0))
    return lo + n * (hi - lo)


def _clip01(x):
    return 0.0 if x < 0 else (1.0 if x > 1 else float(x))


def _interp(tb, n, log=False):
    """norm -> value through a learned table [(norm, value)] (log-interpolated for Hz)."""
    if n <= tb[0][0]:
        return tb[0][1]
    for (n0, v0), (n1, v1) in zip(tb, tb[1:]):
        if n <= n1:
            t = (n - n0) / ((n1 - n0) or 1)
            if log and v0 > 0 and v1 > 0:
                return v0 * (v1 / v0) ** t
            return v0 + t * (v1 - v0)
    return tb[-1][1]


def _interp_inv(tb, v, log=False):
    """value -> norm through a monotone table (rising or falling); clamps at the ends."""
    vals = [b for _, b in tb]
    rising = vals[-1] >= vals[0]
    pts = tb if rising else list(reversed(tb))
    if v <= pts[0][1]:
        return pts[0][0]
    for (n0, v0), (n1, v1) in zip(pts, pts[1:]):
        if (v0 <= v <= v1) and v1 != v0:
            if log and v0 > 0 and v1 > 0 and v > 0:
                t = math.log(v / v0) / math.log(v1 / v0)
            else:
                t = (v - v0) / (v1 - v0)
            return n0 + t * (n1 - n0)
    return pts[-1][0]


def _resolve(entry, live_params):
    """Which live parameter a map entry is: by FL name (exact, then all name tokens), then by index when the map
    entry is verified. -> (param dict, how) or (None, why)."""
    if not live_params:
        return None, "the plugin reports no parameters"
    want = _norm_name(entry.get("fl_name"))
    by_name = [p for p in live_params if _norm_name(p.get("name")) == want] if want else []
    if by_name:
        if entry.get("index") is not None:
            same = [p for p in by_name if p["param"] == entry["index"]]
            if same:
                return same[0], "name+index"
        return by_name[0], "name"
    toks = [set(t.split()) for t in (entry.get("match") or ([want] if want else []))]
    if gain_type(None, entry):                                    # "Gain" is a subset of "Input gain": never guess
        toks = []
    best = None
    for p in live_params:
        nt = set(_norm_name(p.get("name")).split())
        for ts in toks:
            if ts and ts <= nt and (best is None or len(nt) < len(set(_norm_name(best.get("name")).split()))):
                best = p
    if best is not None:
        return best, "name tokens"
    if entry.get("verified") and entry.get("index") is not None:
        p = next((p for p in live_params if p["param"] == entry["index"]), None)
        if p:
            return p, "index (verified map)"
    return None, (f"no parameter named exactly '{entry.get('fl_name')}' on this plugin (map not verified on this PC: "
                  "run fl_plugin_map_learn)")


# ============================================================================= knob safety
_GAIN_RE = re.compile(r"(?i)(gain|level|volume|\bvol\b|thresh|ceiling|output|makeup|make-up|drive|boost|\bmix\b|wet|"
                      r"dry|amount|\bpre\b|post)")
# safe ranges for computed values (the engineer's own moves never need more; a typo or bad measurement can't
# slam a control): dB gains, thresholds, ceilings, frequencies, Q, percentages
SAFE = {"gain_db": (-24.0, 12.0), "threshold_db": (-60.0, 0.0), "ceiling_db": (-12.0, 0.0), "hz": (20.0, 20000.0),
        "q": (0.1, 18.0), "oct": (0.05, 4.0), "pct": (0.0, 100.0), "fader_db": (-60.0, 5.6), "fader_move_db": (-24.0, 6.0),
        "eq_gain_db": (-18.0, 9.0), "pan": (-1.0, 1.0)}


def gain_type(key, entry):
    """Is this a control whose extremes are loud (gain, level, threshold, output, makeup, wet...)?"""
    e = entry or {}
    if str(e.get("unit") or "").lower() == "db":
        return True
    return bool(_GAIN_RE.search(f"{key or ''} {e.get('fl_name') or ''}"))


def _safe(kind, v):
    lo, hi = SAFE[kind]
    return min(hi, max(lo, float(v)))


def safe_value(key, entry, v):
    """Clamp a numeric control value to a safe range for its kind -> (value, clamped?)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return v, False
    if math.isnan(x):
        raise ValueError(f"{key}: not a number")
    e = entry or {}; u = str(e.get("unit") or "").lower(); name = f"{key or ''} {e.get('fl_name') or ''}".lower()
    if u == "db":
        kind = "threshold_db" if "thresh" in name else "ceiling_db" if "ceil" in name else "gain_db"
    elif u == "hz":
        kind = "hz"
    elif u == "q":
        kind = "q"
    elif u == "oct":
        kind = "oct"
    elif u == "%":
        kind = "pct"
    else:
        kind = None
    y = _safe(kind, x) if kind else x
    lo, hi = e.get("min"), e.get("max")                           # and the map's own range
    if isinstance(lo, (int, float)) and y < lo:
        y = float(lo)
    if isinstance(hi, (int, float)) and not math.isinf(hi) and y > hi:
        y = float(hi)
    return y, abs(y - x) > 1e-9


def playing():
    """Is FL playing? None when the bridge can't say. Uses the transport-only state (a few bytes, any project size;
    the full state times out on big projects)."""
    try:
        light = H.get("_bridge_state_light")
        st = light(timeout=5.0) if light else _bridge("state")
        if not isinstance(st, dict) or "playing" not in st:
            return None
        return bool(st.get("playing"))
    except Exception:
        return None


def _display_slope(e, disp_unit, n):
    """d(display number)/d(knob) at knob position n from the map's curve, in FL's display unit (the map's unit
    converted: a Q display falls while an octave knob rises), or None when it can't be known."""
    try:
        mu = e.get("unit")
        conv = (lambda x: convert_unit(x, mu, disp_unit)) if disp_unit and mu not in (None, "display") \
            and disp_unit != mu else (lambda x: x)
        a, b = max(0.0, n - 0.02), min(1.0, n + 0.02)
        va, vb = conv(float(from_norm(e, a))), conv(float(from_norm(e, b)))
        if math.isinf(va) or math.isinf(vb) or b <= a or abs(vb - va) < 1e-12:
            return None
        return (vb - va) / (b - a)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def _set_local(track, slot, p, e, value, target, tol, step_max, disp_unit=None):
    """Set a numeric control by its displayed number WITHOUT sweeping the knob: start at the map curve's guess and
    refine with small secant steps (each <= step_max of the knob range) around it, so the control never visits its
    extremes (a compressor gain never jumps to +30 dB). -> (bridge reply, ok, best |error|)."""
    try:
        n = _clip01(to_norm(e, value))
    except (ValueError, TypeError, ZeroDivisionError):
        n = _clip01(float(p.get("value", 0.5)))
    pts = []; best = None; last = n
    for _ in range(8):
        r = _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=float(n)); last = n
        got, _ = parse_display(r.get("text"))
        if got is None or (isinstance(got, float) and math.isinf(got)):
            break
        err = float(got) - float(target)
        if best is None or abs(err) < best[0]:
            best = (abs(err), n, r)
        if abs(err) <= tol:
            break
        pts.append((n, float(got)))
        if len(pts) >= 2 and abs(pts[-1][1] - pts[-2][1]) > 1e-9 and abs(pts[-1][0] - pts[-2][0]) > 1e-9:
            slope = (pts[-1][1] - pts[-2][1]) / (pts[-1][0] - pts[-2][0])
            dn = -err / slope
        else:                                                     # first probe: a small step in the guessed direction
            slope = _display_slope(e, disp_unit, n)          # err is in FL's display unit: so is the slope
            if slope is not None:
                dn = -err / slope
            else:
                span = float(e.get("max", 1) or 1) - float(e.get("min", 0) or 0)
                dn = -err / span if span and not math.isinf(span) else (-0.02 if err > 0 else 0.02)
        dn = max(-step_max, min(step_max, dn))
        if abs(dn) < 1e-6:
            break
        n = _clip01(n + dn)
    if best is None:
        return None, False, None
    if abs(best[1] - last) > 1e-9:                                # put the closest position back
        best = (best[0], best[1], _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=float(best[1])))
    return best[2], best[0] <= tol, best[0]


def _plugin_name_ok(mp, live_name):
    names = [_norm_name(mp.get("plugin"))] + [_norm_name(a) for a in mp.get("aliases", [])]
    ln = _norm_name(live_name)
    return any(n and (n == ln or n in ln) for n in names)


def plugin_get_named(track, slot, plugin=None):
    """Read a plugin's mapped controls live: {control: {value, text, raw, param}}."""
    live = H["fl_plugin_params"](int(track), int(slot))
    mp = plugin_map(plugin or live.get("plugin"))
    if not mp:
        raise ValueError(f"no plugin map for '{plugin or live.get('plugin')}' (fl_plugin_map_learn makes one)")
    out = {}
    for key, e in mp["controls"].items():
        p, _ = _resolve(e, live.get("params"))
        if p is None:
            continue
        num, unit = parse_display(p.get("text"))
        out[key] = {"value": num if num is not None else p.get("text"), "unit": unit, "text": p.get("text"),
                    "raw": p.get("value"), "param": p["param"]}
    return {"plugin": live.get("plugin"), "controls": out}


def _enum_set(track, slot, p, entry, label):
    # PC-VERIFY: PEQ2 band type labels as FL shows them ('High pass', 'Peaking', ...) and that 3/7 = High pass
    """Set an enum parameter to the option whose display text matches `label` (scans steps when the guess is off)."""
    want = str(label).lower()
    guess = to_norm(entry, label)
    r = _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=float(guess))
    if want in str(r.get("text", "")).lower():
        return r, True
    n = max(8, len(entry.get("options") or []) * 2)
    for k in range(n + 1):
        r = _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=k / n)
        if str(r.get("text", "")).lower().strip() == want or want in str(r.get("text", "")).lower():
            return r, True
    _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=float(p.get("value", 0.0)))
    return {"text": p.get("text")}, False


def plugin_set_named(track, slot, plugin, values, tolerance=None):
    """Set named controls of a live plugin with real units: plugin_set_named(3, 1, 'Fruity Parametric EQ 2',
    {'band2_freq_hz': 3000, 'band2_gain_db': -2.5, 'band2_type': 'Peaking'}). Every control is matched to the live
    parameter by its FL name, set, and checked against the display text FL shows afterwards.
    -> {plugin, set: [{control, want, text, ok}], skipped: [...], undo: [{track, slot, param, before}]}"""
    track, slot = int(track), int(slot)
    live = H["fl_plugin_params"](track, slot)
    mp = plugin_map(plugin or live.get("plugin"))
    if not mp:
        raise ValueError(f"no plugin map for '{plugin}'")
    if not _plugin_name_ok(mp, live.get("plugin")):
        raise ValueError(f"mixer track {track} slot {slot} holds '{live.get('plugin')}', not {mp['plugin']}")
    params = live.get("params") or []
    done, skipped, undo, notes = [], [], [], []
    play = None
    for key, raw_v in values.items():
        e = mp["controls"].get(key)
        if e is None:
            skipped.append({"control": key, "why": f"not in the {mp['plugin']} map ({', '.join(list(mp['controls'])[:8])}...)"})
            continue
        p, how = _resolve(e, params)
        if p is None:
            skipped.append({"control": key, "why": how}); continue
        undo.append({"track": track, "slot": slot, "param": p["param"], "before": p.get("value")})
        v, unit = _value_with_unit(raw_v)
        if e.get("unit") == "enum" or e.get("curve") == "enum" or isinstance(v, str):
            opts = e.get("options") or []
            if not isinstance(v, str) and opts:                        # an option index (0 = the first option)
                i = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer() else -1
                if not 0 <= i < len(opts):
                    skipped.append({"control": key, "why": f"option index {v!r} isn't 0..{len(opts) - 1} "
                                                           f"({', '.join(map(str, opts))})"}); undo.pop(); continue
                v = opts[i]
            try:
                r, ok = _enum_set(track, slot, p, e, v)
            except ValueError as ex:                                   # unknown label: nothing was set
                skipped.append({"control": key, "why": str(ex)}); undo.pop(); continue
            done.append({"control": key, "want": v, "text": r.get("text"), "ok": ok, "matched_by": how})
            continue
        curve_only = None
        bridged = None
        try:
            if unit and e.get("unit") not in (None, "display") and unit != e.get("unit"):
                if units_convertible(unit, e["unit"]):
                    v = convert_unit(v, unit, e["unit"])
                else:                                               # e.g. a Q for a width FL shows as a %:
                    got = bridge_unit(mp, key, v, unit, e["unit"])   # the map's own table, else the plain error
                    if got is None:
                        v = convert_unit(v, unit, e["unit"])
                    v, bridged = got
            v, clamped = safe_value(key, e, v)
            if clamped:
                notes.append(f"{key}: clamped to {_r(v, 2)} {e.get('unit')} (safe range)")
            disp_num, disp_unit = parse_display(p.get("text"))
            target = v
            if disp_num is None:                                        # 'Off' at the knob's end: no unit to
                disp_unit = None                                        # read, the number after the move decides
            if disp_unit and e.get("unit") not in (None, "display") and disp_unit != e["unit"]:
                if units_convertible(e["unit"], disp_unit):
                    target = convert_unit(v, e["unit"], disp_unit)      # e.g. Q wanted, FL shows octaves
                else:
                    curve_only = disp_unit                              # e.g. octaves wanted, FL shows a %
        except ValueError as ex:
            skipped.append({"control": key, "why": str(ex)}); undo.pop(); continue
        if curve_only is not None:
            if gain_type(key, e):                                  # a level knob is never set by a guess
                skipped.append({"control": key, "why": f"FL shows '{p.get('text')}' for this {e.get('unit')} control "
                                f"and {e.get('unit')} can't be converted to {curve_only}: left unchanged (a level "
                                f"knob is never set by a guess; fl_plugin_map_learn({track}, {slot}) learns its "
                                "real scale)"})
                undo.pop(); continue
            # PC-VERIFY: Parametric EQ 2 band width shown as '%': the map curve's guess for octaves/Q is near the
            # planned width (look at the band's curve in the EQ window, listen)
            r = _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=float(to_norm(e, v)))
            note = (f"set by the plugin map's curve, display shows {curve_only}: unverified, listen"
                    + (" (fl_plugin_map_learn learns the real scale)" if not e.get("verified") else ""))
            done.append({"control": key, "want": f"{_r(v, 2)} {e.get('unit')}", "text": r.get("text"), "ok": False,
                         "unverified": True, "note": note, "matched_by": how})
            notes.append(f"{key}: {note} (FL shows '{r.get('text')}')")
            continue
        tol = tolerance if tolerance is not None else max(0.05 * abs(float(target)), 0.15 if disp_unit == "dB" else 0.01)
        r, ok = None, False
        if e.get("verified") and e.get("curve") == "table":       # learned on this PC: one round trip
            r = _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=to_norm(e, v))
            got, _ = parse_display(r.get("text"))
            ok = got is not None and abs(got - float(target)) <= tol
        gt = gain_type(key, e)
        if not ok:                                                 # local search: never visits the knob's extremes
            # PC-VERIFY: no audible spike while setting a compressor gain / EQ level during playback
            r2, ok, _err = _set_local(track, slot, p, e, v, float(target), max(tol, 0.06 * abs(float(target))),
                                      0.05 if gt else 0.15, disp_unit=disp_unit)
            r = r2 or r
        if not ok and not gt:                                      # full-range display search: never for gain-type
            if play is None:                                       # controls, never while the song plays
                play = playing()
            if play is False:
                try:
                    # PC-VERIFY: displays that switch unit inside the knob range (950 ms -> 1.2 s) defeat the search
                    r = H["fl_plugin_set_value"](track, p["param"], _bridge_target(target, p.get("text")), slot)
                    got, _ = parse_display(r.get("text"))
                    ok = got is not None and abs(got - float(target)) <= max(tol, 0.06 * abs(float(target)))
                except Exception as ex:                            # restore, then the map's curve
                    try:
                        _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=float(p.get("value", 0.0)))
                    except Exception:
                        pass
                    r = _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=to_norm(e, v))
                    r = dict(r, note=f"set by the map's curve (unverified): {ex}")
        if r is None:                                              # nothing readable: put it back, report
            _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=float(p.get("value", 0.0)))
            skipped.append({"control": key, "why": "FL shows no number for it; left unchanged"}); undo.pop(); continue
        d_ = {"control": key, "want": f"{_r(v, 2)} {e.get('unit')}", "text": r.get("text"), "ok": bool(ok),
              "matched_by": how}
        if bridged:
            d_["unverified"] = True; d_["note"] = bridged; notes.append(bridged)
        done.append(d_)
    out = {"plugin": live.get("plugin"), "track": track, "slot": slot, "set": done, "skipped": skipped,
           "undo": undo, "ok": all(d["ok"] for d in done) and not skipped}
    if notes:
        out["notes"] = notes
    return out


def fl_plugin_set_named(track, slot, values, plugin=None):
    return plugin_set_named(track, slot, plugin, values)


# ============================================================================= learning a map on the user's PC
def _learn(track, slot, points, params_filter):
    # PC-VERIFY: every stock effect's parameters list with names; probing + restore leaves the sound unchanged
    # (the bridge's plugin_params returns full-precision values, so restores are exact; checked by reading back)
    steps, mark = _steps()
    track, slot = int(track), int(slot)
    stopped = False
    pl = playing()
    if pl is None:                                                # can't tell: never assume it's stopped
        raise RuntimeError("couldn't read FL's transport state, so the sweep didn't start (it moves every knob to its "
                           "extremes and must never run while FL plays); check the bridge with fl_status, stop "
                           "playback, then run fl_plugin_map_learn again")
    if pl:                                                        # the sweep visits every knob's extremes: never
        _bridge("transport", action="stop"); stopped = True       # while the song plays (hearing / speakers)
        time.sleep(0.15)
        if playing() is not False:
            raise RuntimeError("FL may still be playing (stopped it, but the transport doesn't read 'stopped'); stop "
                               "playback, then run fl_plugin_map_learn again")
    live = H["fl_plugin_params"](track, slot, params_filter or "")
    name = live.get("plugin") or "plugin"
    plist = live.get("params") or []
    mark("read")
    pts = max(5, min(65, int(points)))
    grid = [k / (pts - 1) for k in range(pts)]
    probe = False
    learned = {}
    try:
        try:                                                      # optional bridge sweep (one round trip per param)
            if plist:
                _bridge("plugin_probe", index=track, slot=slot, param=plist[0]["param"], points=3)
                probe = True
        except Exception:
            probe = False
        for i, p in enumerate(plist):
            _progress(step=f"probing {p.get('name')}", pct=int(100 * i / max(1, len(plist))))
            if probe:
                texts = _bridge("plugin_probe", index=track, slot=slot, param=p["param"], points=pts)["texts"]
            else:
                texts = [_bridge("plugin_set", index=track, slot=slot, param=p["param"], value=g).get("text")
                         for g in grid]
            parsed = [parse_display(t) for t in texts]
            nums = [v for v, _ in parsed if v is not None]
            units = [u for v, u in parsed if v is not None and u]
            if len(nums) == len(parsed) and len(set(nums)) > max(2, pts // 4):
                unit = max(set(units), key=units.count) if units else ""
                learned[p["param"]] = {"fl_name": p.get("name"), "unit": unit or "display", "curve": "table",
                                       "table": [[round(g, 4), (None if math.isinf(v) else round(v, 4))]
                                                 for g, (v, _) in zip(grid, parsed)],
                                       "min": min(nums), "max": max(x for x in nums if not math.isinf(x))}
            else:                                                 # enum / switch: find the option order
                opts, norms = [], []
                for g, t in zip(grid, texts):
                    t = str(t or "").strip()
                    if not opts or opts[-1] != t:
                        opts.append(t); norms.append([g])
                    else:
                        norms[-1].append(g)
                learned[p["param"]] = {"fl_name": p.get("name"), "unit": "enum", "curve": "enum", "options": opts,
                                       "option_norms": [[o, round(sum(n) / len(n), 4)] for o, n in zip(opts, norms)]}
    finally:
        for p in plist:                                           # always put the knobs back
            try:
                _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=float(p.get("value", 0.0)))
            except Exception:
                pass
    restore_err, not_restored = 0.0, []
    try:                                                          # read back: every knob exactly where it was
        now = {q["param"]: q.get("value") for q in (H["fl_plugin_params"](track, slot, params_filter or "").get("params") or [])}
        for p in plist:
            v0, v1 = p.get("value"), now.get(p["param"])
            if isinstance(v0, (int, float)) and isinstance(v1, (int, float)) and abs(v1 - v0) > 1e-6:
                _bridge("plugin_set", index=track, slot=slot, param=p["param"], value=float(v0))
                restore_err = max(restore_err, abs(v1 - v0)); not_restored.append(p.get("name"))
    except Exception:
        not_restored.append("(read-back failed)")
    mark("probe")
    base = plugin_map(name) or {"plugin": name, "aliases": [], "roles": [], "controls": {}}
    controls = {k: dict(v) for k, v in base.get("controls", {}).items()}
    matched, used = [], set()
    plist_named = [{"param": k, "name": v["fl_name"]} for k, v in learned.items()]
    for key, e in controls.items():
        p, how = _resolve(dict(e, verified=False), plist_named)
        if p is None or p["param"] in used:
            e["verified"] = False; e["missing_on_pc"] = True
            continue
        used.add(p["param"]); L = learned[p["param"]]
        bu = e.get("unit")
        e.update(L, index=p["param"], verified=True, learned=time.strftime("%Y-%m-%d"))
        e.pop("missing_on_pc", None)
        lu = L.get("unit")
        if L.get("curve") == "table" and bu not in (None, "display", "enum") and lu not in (None, "display", "enum") \
                and bu != lu and units_convertible(lu, bu):
            # keep the control's own unit (decay_s stays seconds although FL's '1.2 s' reads as 1200 ms, a width
            # stays octaves when FL shows Q): callers pass plain numbers in the unit the control is named for
            tb = [[n, None if v is None else round(convert_unit(v, lu, bu), 6)] for n, v in L["table"]]
            vals = [v for _, v in tb if v is not None]
            e.update(unit=bu, table=tb, min=min(vals), max=max(vals), fl_unit=lu)
        if e.get("curve") == "table":                            # keep the semantic unit when FL's matches
            e.pop("exp", None)
        matched.append(key)
    for k, L in learned.items():                                  # parameters the shipped map didn't name
        if k in used:
            continue
        key = _slug(L["fl_name"]) or f"param_{k}"
        while key in controls:
            key += "_"
        controls[key] = dict(L, index=k, verified=True, learned=time.strftime("%Y-%m-%d"), auto=True)
    out = {"plugin": name, "aliases": base.get("aliases", []), "edition": base.get("edition"),
           "roles": base.get("roles", []), "source": "learned", "verified": True,
           "learned": time.strftime("%Y-%m-%d %H:%M"), "points": pts, "controls": controls}
    d = _user_map_dir(); os.makedirs(d, exist_ok=True)
    path = os.path.join(d, _slug(name) + ".json"); tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, path)
    _CACHE.pop(path, None)
    mark("write")
    missing = [k for k, e in controls.items() if e.get("missing_on_pc")]
    return {"plugin": name, "params_learned": len(learned), "named_controls_matched": len(matched),
            "named_controls_missing": missing[:20], "new_controls": len(learned) - len(used), "map": path,
            "probe": "bridge sweep" if probe else "one set per point", "steps_s": steps,
            "stopped_playback": stopped,
            "restored": "exact (read back)" if not not_restored else
                        f"re-set {len(not_restored)} knob(s) that read back differently: {not_restored[:5]}",
            "note": "knobs were restored to their values" + (" (playback was stopped for the sweep)" if stopped else "")}


def fl_plugin_map_learn(track, slot, points=17, params=None, background=None):
    """Learn a plugin's map on this PC: parameter names, display units and the knob curve, by probing."""
    n = H["fl_plugin_info"](int(track), int(slot)).get("params") or 40
    est = (n if not params else 10) * int(points) * 0.03 + 2
    return H["_auto_job"]("maplearn", est, _learn, dict(track=track, slot=slot, points=points,
                                                         params_filter=params), background)


# ============================================================================= map name check (live FL names)
# Shipped maps were written before anyone read FL's own parameter names; a wrong fl_name leaves a gain-type knob
# unset (the resolver never guesses those by tokens). The check reads the live names of every mapped effect,
# matches each map control to the parameter it means and records the right name + index in a user map.
_SYN = [("level", "amount", "amt", "lvl", "mix", "volume", "vol"), ("freq", "frequency", "cutoff"),
        ("resonance", "res", "reso"), ("width", "bandwidth", "bw"), ("threshold", "thresh", "thres"),
        ("separation", "sep"), ("reflection", "reflections", "er"),
        ("inversion", "invert", "inv"), ("modulation", "mod"), ("feedback", "fb"), ("saturation", "sat"),
        ("compressor", "comp", "compression"), ("attack", "att"), ("release", "rel")]
_SYN_OF = {w: i for i, g in enumerate(_SYN) for w in g}
_GENERIC = {"level", "amount", "amt", "lvl", "mix", "volume", "vol", "gain", "time", "value", "param"}
_UNIT_WORDS = {"hz", "khz", "db", "pct", "ms", "s", "x", "q", "oct", "st", "percent"}
_OUT_WORDS, _IN_WORDS = {"output", "out", "main", "master", "post"}, {"input", "in", "pre"}
_NAME_UNITS = {"dB": "level", "%": "level", "Hz": "Hz", "ms": "ms", "s": "ms", "ratio": "ratio", "oct": "oct",
               "Q": "oct"}                                        # unit families: a dB knob may show %, never Hz
_FAMILY_ALSO = {"oct": {"level"}}                                 # a band width may show a % (PEQ2), never Hz
MATCH_MIN = 0.6                                                   # fuzzy matches below this are reported, not written
TIE = 0.015                                                       # two live names this close: ambiguous, not written


def _words(s):
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            for w in _norm_name(s).split()]


def _word_eq(a, b):
    if a == b:
        return True
    if a in _SYN_OF and _SYN_OF.get(a) == _SYN_OF.get(b):
        return True
    if a.isdigit() or b.isdigit():
        return False
    s, l_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(s) >= 3 and l_.startswith(s)                       # thres/threshold, sep/separation, freq/frequency


def _covered(A, B):
    """How many words of A pair with distinct words of B."""
    free = list(B); n = 0
    for a in A:
        j = next((i for i, b in enumerate(free) if _word_eq(a, b)), None)
        if j is not None:
            free.pop(j); n += 1
    return n


def _name_score(M, L):
    """Map words M vs live words L -> (score, how) or (0, None)."""
    if not M or not L:
        return 0.0, None
    if sorted(x for x in M if x.isdigit()) != sorted(x for x in L if x.isdigit()):
        return 0.0, None                                          # 'Band 2 level' is never 'Band 3 level'
    if "".join(M) == "".join(L):
        return 0.9, "same words"
    cm, cl = _covered(M, L), _covered(L, M)
    generic = all(w in _GENERIC or w.isdigit() for w in M)
    if cm == len(M) and cl == len(L):
        return 0.9, ("synonym" if M != L else "same words")
    if cm == len(M):                                              # live name adds words: 'Wet' -> 'Wet level'
        extra = len(L) - len(M)
        return max(0.4, (0.6 if generic else 0.8) - 0.05 * extra), "live name adds words"
    if cl == len(L) and any(w not in _GENERIC and not w.isdigit() for w in L):
        return max(0.4, 0.7 - 0.05 * (len(M) - len(L))), "map name adds words"
    return 0.0, None


def _unit_ok(entry, p):
    u = _NAME_UNITS.get(str(entry.get("unit") or ""))
    if not u:
        return True
    num, lu = parse_display(p.get("text"))
    if num is None:
        return True                                               # 'Off' at one end of a numeric knob
    lu = _NAME_UNITS.get(lu or "")
    return lu is None or lu == u or lu in _FAMILY_ALSO.get(u, ())


def _candidate_score(key, entry, p):
    """How well a live parameter fits a map control -> (score, how)."""
    live = p.get("name") or ""
    want = entry.get("fl_name")
    if want and _norm_name(want) == _norm_name(live):
        return 1.0, "exact"
    if not _unit_ok(entry, p):
        return 0.0, None
    L = _words(live)
    best = (0.0, None)
    alts = [(_words(want), 0.0)] if want else []
    alts += [(_words(m), 0.0) for m in entry.get("match") or []]
    kw = [w for w in _words(key) if w not in _UNIT_WORDS]
    if kw:
        alts.append((kw, 0.05))                                   # the control's own key words, a bit less sure
    for M, pen in alts:
        s, how = _name_score(M, L)
        if s - pen > best[0]:
            best = (s - pen, how)
    if not best[1]:
        return best
    s = best[0]
    mine = set(_words(want or key))
    if gain_type(key, entry) and not (mine & (_OUT_WORDS | _IN_WORDS)):   # 'Wet' means the output wet
        if set(L) & _OUT_WORDS:
            s += 0.02
        elif set(L) & _IN_WORDS:
            s -= 0.02
    if entry.get("index") is not None and p.get("param") == entry.get("index"):
        s += 0.01
    return s, best[1]


def match_live_names(mp, params):
    """Match every control of a plugin map to the live parameter it means.
    -> {key: (param dict, how, score)} for matches, {key: [close candidates]} for the rest."""
    params = [p for p in params or [] if p.get("name")]
    ctl = mp.get("controls") or {}
    scored = {k: sorted(((*_candidate_score(k, e, p), p) for p in params), key=lambda x: -x[0]) for k, e in ctl.items()}
    pairs = sorted(((s, k, p["param"], how, p) for k, lst in scored.items() for s, how, p in lst if how),
                   key=lambda x: (-x[0], x[1]))
    got, used, ambiguous = {}, set(), {}
    for s, k, pi, how, p in pairs:
        if k in got or k in ambiguous or pi in used:
            continue
        if s < 1.0:
            if s < MATCH_MIN:
                continue
            rival = next((q for s2, h2, q in scored[k] if h2 and q["param"] != pi and q["param"] not in used
                          and s2 >= s - TIE), None)
            if rival is not None:
                ambiguous[k] = [p.get("name"), rival.get("name")]
                continue
        got[k] = (p, how, round(s, 3)); used.add(pi)
    rest = {}
    for k, e in ctl.items():
        if k in got:
            continue
        c = ambiguous.get(k) or [p.get("name") for s, how, p in scored[k] if how and p["param"] not in used][:3]
        if not c:                                                 # for a person: live names sharing a word
            mine = {w for w in _words(e.get("fl_name") or "") + _words(k) if w not in _GENERIC | _UNIT_WORDS}
            c = [p.get("name") for p in params if p["param"] not in used
                 and any(_word_eq(a, b) for a in mine for b in _words(p.get("name")))][:3]
        rest[k] = c
    return got, rest


def _effects_on(tracks):
    """[(track, slot, live dict)] of the mapped effects on these mixer tracks (all tracks fl_state lists when None)."""
    # PC-VERIFY: plugin_params on an empty slot raises or returns no plugin name (both are skipped)
    out = []
    if tracks is None:
        st = H["fl_state"]()
        locs = [(m["index"], fx["slot"]) for m in st.get("mixer", []) for fx in m.get("effects", [])
                if plugin_map(fx.get("name"))]
    else:
        locs = [(int(t), s) for t in tracks for s in range(10)]
    for t, s in locs:
        try:
            live = H["fl_plugin_params"](int(t), int(s))
        except Exception:
            continue
        if (live or {}).get("plugin") and plugin_map(live["plugin"]):
            out.append((t, s, live))
    return out


def _shipped_path(mp):
    for f in (mp.get("shipped_file"), mp.get("file")):
        if f and os.path.normcase(os.path.dirname(os.path.abspath(f))) == os.path.normcase(os.path.abspath(MAP_DIR)):
            return f
    return None


def _write_json(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    _CACHE.pop(path, None)


def shipped_fix(shipped, user):
    """A shipped map (dict) with the names/indexes a user map verified on a PC -> corrected copy (curves, units,
    ranges and `verified` untouched: only FL's names are confirmed)."""
    out = json.loads(json.dumps(shipped))
    uc = (user or {}).get("controls") or {}
    hit = False
    for k, e in (out.get("controls") or {}).items():
        u = uc.get(k) or {}
        if u.get("names_verified") and u.get("fl_name"):
            e["fl_name"] = u["fl_name"]; e["index"] = u.get("index", e.get("index"))
            e["names_verified"] = u["names_verified"]; hit = True
    if hit:
        out["names_verified"] = (user or {}).get("names_verified") or time.strftime("%Y-%m-%d")
    return out


def shipped_diff(shipped_path, user):
    """Unified diff of a shipped map file against its corrected copy ('' when nothing changes)."""
    import difflib
    with open(shipped_path, encoding="utf-8") as f:
        old = f.read()
    new = json.dumps(shipped_fix(json.loads(old), user), indent=1, ensure_ascii=False)
    if new == old:
        return ""
    rel = "data/plugin_maps/" + os.path.basename(shipped_path)
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + rel, "b/" + rel, n=1))


def map_names_check(tracks=None, apply=True, full=False):
    # PC-VERIFY: every mapped stock effect's live names match after the check (no 'unmatched' left for the shipped
    # maps once data/plugin_maps is corrected); fuzzy renames are the parameter the control means
    steps, mark = _steps()
    found = _effects_on(tracks)
    mark("read")
    today = time.strftime("%Y-%m-%d")
    report, seen, diffs, compact = [], set(), [], []
    for t, s, live in found:
        mp = plugin_map(live["plugin"])
        if mp["plugin"] in seen:
            continue
        seen.add(mp["plugin"])
        params = live.get("params") or []
        row = {"plugin": mp["plugin"], "track": t, "slot": s, "live_params": len(params)}
        if not params:
            row["note"] = "FL reports no parameters for it (not loaded / demo?): skipped"
            report.append(row); continue
        got, rest = match_live_names(mp, params)
        ctl = mp["controls"]
        matched, renamed, reindexed = [], [], []
        for k, (p, how, sc) in got.items():
            e = ctl[k]
            if _norm_name(e.get("fl_name")) != _norm_name(p.get("name")):
                renamed.append({"control": k, "old": e.get("fl_name"), "new": p.get("name"), "index": p["param"],
                                "how": how, "score": sc})
            elif e.get("index") != p["param"]:
                reindexed.append({"control": k, "name": p.get("name"), "old": e.get("index"), "new": p["param"]})
            else:
                matched.append(k)
        used = {p["param"] for p, _, _ in got.values()}
        units = []
        for k, (p, _, _) in got.items():
            lu = parse_display(p.get("text"))
            mu = ctl[k].get("unit")
            if lu[0] is not None and lu[1] and mu in _NAME_UNITS and _NAME_UNITS.get(lu[1]) and \
                    not units_convertible(mu, lu[1]):             # s vs ms, Q vs oct convert: not a difference
                units.append(f"{k}: map {mu}, FL shows {p.get('text')} (no conversion known)")
        if units:
            row["units_differ"] = units[:8]
        row.update(matched=len(matched), renamed=renamed, reindexed=reindexed,
                   unmatched=[{"control": k, "fl_name": ctl[k].get("fl_name"), "candidates": c} for k, c in rest.items()])
        if rest:
            row["live_names_unused"] = [p.get("name") for p in params if p["param"] not in used and p.get("name")][:30]
        for r in renamed:
            compact.append(f"{mp['plugin']}: {r['control']} '{r['old']}' -> '{r['new']}' (index {r['index']})")
        for r in reindexed:
            compact.append(f"{mp['plugin']}: {r['control']} index {r['old']} -> {r['new']}")
        if apply and got:
            path = os.path.join(_user_map_dir(), _slug(mp["plugin"]) + ".json")
            base = None
            if os.path.isfile(path):
                try:
                    base = json.loads(json.dumps(_json(path)))
                except Exception:
                    base = None
            if base is None:
                shipped = _shipped_path(mp)
                base = json.loads(json.dumps(_json(shipped))) if shipped else {k: v for k, v in mp.items()
                                                                               if k not in ("file", "shipped_file")}
                base["source"] = "names_checked"
            bc = base.setdefault("controls", {})
            for k, (p, how, sc) in got.items():
                e = bc.setdefault(k, json.loads(json.dumps(ctl[k])))
                e.update(fl_name=p.get("name"), index=p["param"], names_verified=today)
                e.pop("missing_on_pc", None)
                if how != "exact":
                    e["names_matched_by"] = how
            for k in rest:
                if k in bc:
                    bc[k]["missing_on_pc"] = True
            base["names_verified"] = today
            _write_json(path, base)
            row["user_map"] = path
            shp = _shipped_path(mp)
            if shp and full:
                d = shipped_diff(shp, base)
                if d:
                    diffs.append(d)
        report.append(row)
    mark("match+write")
    missing = [m for m in sorted(plugin_maps()) if m not in seen]
    out = {"checked": len(report), "plugins": report, "diff": compact,
           "written": bool(apply), "steps_s": steps}
    if missing:
        out["not_loaded"] = missing
        out["note"] = ("mapped effects not on the scanned tracks were not checked; fl_pc_check(areas=['mix.map_names'])"
                       " loads every one in a sandbox song (your song is reopened after)")
    if full:
        out["shipped_diff"] = "".join(diffs)
    if apply and any(r.get("renamed") or r.get("reindexed") for r in report):
        out["next"] = ("user maps corrected (they override the shipped ones); developers: python3 "
                       "tools/plugin_maps_names.py --write copies the names into data/plugin_maps")
    return out


def fl_plugin_maps_check(tracks=None, apply=True, full=False):
    return map_names_check(tracks, apply, full)


# ============================================================================= audio + measurements
def load_audio(path):
    """-> (x float32 (n, 2), fs). WAV read block-wise, FLAC via flh_review's decoder (float32: long / high-rate
    renders fit in memory; every measurement accumulates in float64)."""
    import numpy as np
    x, fs, _ = _R().load_audio(str(path))
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[1] == 1:
        x = _R().dup_mono(x)                                     # marked: loudness meters the one channel once
    return x[:, :2], int(fs)


def _R():
    R = sys.modules.get("flh_review")
    if R is None:
        import flh_review as R                                   # helpers only; R.H stays as the server set it
    return R


def _mlufs(x, fs):
    """Integrated loudness, BS.1770 channel layout: a mono source is metered once (not as dual mono, +3 dB).
    Block-wise meter (bounded memory on long renders)."""
    R_ = _R()
    return R_.integrated_lufs(x, fs) if getattr(R_, "H", None) else H["_lufs"](R_.mono_src(x), fs)


def _tpk(x):
    """True peak dBTP: the shared chunked detector (flh_finalize), else the server's."""
    try:
        import flh_finalize
        return flh_finalize.true_peak_db(x)
    except ImportError:
        return H["_true_peak"](x)


def _spectra_ms(x, fs, n=8192):
    """Average power spectra of mid and side (Hann frames, as the server's _spectrum), computed chunk by chunk."""
    import numpy as np
    w = np.hanning(n); f = np.fft.rfftfreq(n, 1 / fs)
    Sm = np.zeros(n // 2 + 1); Ss = np.zeros(n // 2 + 1); k = 0
    nf = len(range(0, max(1, len(x) - n), n)) if len(x) >= n else 0
    G = 64
    for g0 in range(0, nf, G):
        g1 = min(nf, g0 + G); a = g0 * n; b = g1 * n
        seg = np.asarray(x[a:b, :2], dtype=np.float64)
        if len(seg) < (g1 - g0) * n:
            g1 = g0 + len(seg) // n; seg = seg[:(g1 - g0) * n]
        if g1 <= g0:
            break
        L = seg[:, 0].reshape(g1 - g0, n); Rr = seg[:, 1].reshape(g1 - g0, n)
        Sm += (np.abs(np.fft.rfft((L + Rr) * 0.5 * w, axis=1)) ** 2).sum(0)
        Ss += (np.abs(np.fft.rfft((L - Rr) * 0.5 * w, axis=1)) ** 2).sum(0)
        k += g1 - g0
    if not k:
        return f, np.zeros(n // 2 + 1), np.zeros(n // 2 + 1)
    return f, Sm / k, Ss / k


def _ms_energy(x, block=1 << 20):
    """Mean square of mid and side, block-wise -> (em, es) (+1e-20 each)."""
    import numpy as np
    em = es = 0.0
    for a in range(0, len(x), block):
        l = np.asarray(x[a:a + block, 0], dtype=np.float64); r = np.asarray(x[a:a + block, 1], dtype=np.float64)
        m_ = (l + r) * 0.5; s_ = (l - r) * 0.5
        em += m_ @ m_; es += s_ @ s_
    n = max(1, len(x))
    return em / n + 1e-20, es / n + 1e-20


def _band_edges(c):
    return c / 2 ** (1 / 6), c * 2 ** (1 / 6)


def _bandsum(f, S, lo, hi):
    return float(S[(f >= lo) & (f < hi)].sum())


def _p100(x, fs):
    """K-weighted mean square per 100 ms (flh_review's when loaded, else unweighted)."""
    import numpy as np
    R = sys.modules.get("flh_review")
    if R is not None and getattr(R, "H", None):
        try:
            return R._p100(x, fs)
        except Exception:
            pass
    x = _R().mono_src(x); hop = int(0.1 * fs); nb = len(x) // hop; c = min(2, x.shape[1])
    return (x[:nb * hop, :c] ** 2).reshape(nb, hop, c).mean(1).sum(1) if nb else np.zeros(0)


def _transient_db(mid, fs):
    """Peak-to-body: how far 5 ms bursts rise over the preceding 100 ms (mean of the top 5%), in dB."""
    import numpy as np
    b = max(1, int(0.005 * fs)); nb = len(mid) // b
    if nb < 40:
        return None
    pw = (mid[:nb * b] ** 2).reshape(nb, b).mean(1) + 1e-12
    fast = 10 * np.log10(pw)
    cs = np.concatenate([[0.0], np.cumsum(pw)])
    k = 20
    slow = np.full(nb, np.nan)
    slow[k:] = 10 * np.log10((cs[k:nb] - cs[:nb - k]) / k)
    ok = ~np.isnan(slow)
    if not ok.any():
        return None
    gate = np.nanmax(slow) - 30
    d = (fast - slow)[ok & (slow > gate)]
    if len(d) < 10:
        return None
    top = np.sort(d)[-max(1, len(d) // 20):]
    return float(top.mean())


def features(x, fs, full=False):
    """Everything the mix tools compare, for one stereo signal: lufs, true_peak, crest_db, bands_db (6 bands,
    dB re total), tilt (dB/oct, 63 Hz-8 kHz from 1/3 octaves), width_db (side/mid), width_bands, corr,
    transient_db, low_ratio_db (<120 Hz share). full=True adds third_oct (28 bands, dB re total)."""
    import numpy as np
    R_ = _R()
    mono = R_.is_mono(x)
    x = np.asarray(x)
    if x.dtype not in (np.float32, np.float64):
        x = x.astype(np.float64)
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    elif x.shape[1] == 1:
        x = np.repeat(x, 2, 1)
    mid = np.empty(len(x), dtype=x.dtype)
    for a in range(0, len(x), 1 << 20):                          # no full-length float64 temporaries
        np.add(x[a:a + (1 << 20), 0], x[a:a + (1 << 20), 1], out=mid[a:a + (1 << 20)]); mid[a:a + (1 << 20)] *= 0.5
    f, S, Ss = _spectra_ms(x, fs)
    tot = _bandsum(f, S, 20, 16000) + 1e-20
    bands = {n: round(10 * math.log10(_bandsum(f, S, a, b) / tot + 1e-12), 2) for n, a, b in BANDS6}
    third = [10 * math.log10(_bandsum(f, S, *_band_edges(c)) / tot + 1e-12) for c in THIRD_OCT]
    sel = [i for i, c in enumerate(THIRD_OCT) if 63 <= c <= 8000]
    tilt = float(np.polyfit(np.log2([THIRD_OCT[i] for i in sel]), [third[i] for i in sel], 1)[0])
    wb = {}
    for n, a, b in WIDTH_BANDS:
        m_, s_ = _bandsum(f, S, a, b), _bandsum(f, Ss, a, b)
        wb[n] = round(10 * math.log10((s_ + 1e-20) / (m_ + 1e-20)), 1) if m_ > 1e-15 else None
    em, es = _ms_energy(x)
    corr, pk, rms = R_.stats2(x[:, 0], x[:, 1])
    pk += 1e-12; rms += 1e-12
    lufs = (R_.integrated_lufs(x[:, :1] if mono else x, fs) if getattr(R_, "H", None)
            else H["_lufs"](x[:, :1] if mono else x, fs))
    out = {"lufs": _r(lufs, 2), "true_peak": _r(_tpk(x), 2), "peak_dbfs": _r(20 * math.log10(pk), 2),
           "rms_db": _r(20 * math.log10(rms), 2), "crest_db": _r(20 * math.log10(pk / rms), 2), "bands_db": bands,
           "tilt": _r(tilt, 2), "width_db": _r(10 * math.log10(es / em), 2), "width_bands": wb, "corr": _r(corr, 3),
           "transient_db": _r(_transient_db(mid, fs), 2),
           "low_ratio_db": _r(10 * math.log10(_bandsum(f, S, 20, 120) / tot + 1e-12), 2),
           "seconds": round(len(x) / fs, 2)}
    if full:
        out["third_oct"] = [round(v, 2) for v in third]
    return out


def _db_sum(*vals):
    v = [10 ** (a / 10) for a in vals if a is not None]
    return 10 * math.log10(sum(v)) if v else None


def metrics(m):
    """Flat metric dict from any measurement shape: features() here, the server's _measure / fl_listen 'whole'
    (bands_db, width_db, crest_db, lr_correlation, true_peak_dbfs) or flh_review.measure (bands low/lowmid/mid/
    presence/air, crest, corr, tilt, tp)."""
    if m is None:
        return {}
    out = {}
    b6 = m.get("bands_db")
    br = m.get("bands") if isinstance(m.get("bands"), dict) else None
    if b6:
        out.update(sub_db=b6.get("sub"), bass_db=b6.get("bass"), low_mid_db=b6.get("low_mid"), mid_db=b6.get("mid"),
                   presence_db=b6.get("presence"), air_db=b6.get("air"))
        out["low_db"] = _db_sum(b6.get("sub"), b6.get("bass"))
    elif br:
        out.update(low_db=br.get("low"), low_mid_db=br.get("lowmid"), mid_db=br.get("mid"),
                   presence_db=br.get("presence"), air_db=br.get("air"))
    out["high_db"] = _db_sum(out.get("presence_db"), out.get("air_db"))
    lows = _db_sum(out.get("low_db"), out.get("low_mid_db"))
    out["warmth_db"] = None if lows is None or out["high_db"] is None else lows - out["high_db"]
    out["lufs"] = m.get("lufs")
    out["crest_db"] = m.get("crest_db", m.get("crest"))
    out["width_db"] = m.get("width_db")
    out["corr"] = m.get("corr", m.get("lr_correlation"))
    out["tilt"] = m.get("tilt")
    if out["tilt"] is None and b6:                                # density slope from the 6 bands
        cs, lv = [], []
        for n, a, b in BANDS6[1:]:
            if b6.get(n) is not None:
                cs.append(math.log2(math.sqrt(a * b))); lv.append(b6[n] - 10 * math.log10(math.log2(b / a) * 3))
        if len(cs) >= 3:
            n = len(cs); mx, my = sum(cs) / n, sum(lv) / n
            out["tilt"] = sum((a - mx) * (b - my) for a, b in zip(cs, lv)) / (sum((a - mx) ** 2 for a in cs) or 1)
    out["transient_db"] = m.get("transient_db")
    out["true_peak"] = m.get("true_peak", m.get("true_peak_dbfs", m.get("tp")))
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in out.items()}


def _measure_any(a):
    """Audio path -> features; dict -> as is (measurement); (x, fs) tuple -> features."""
    if isinstance(a, dict):
        return a
    if isinstance(a, (tuple, list)) and len(a) == 2 and not isinstance(a[0], str):
        return features(a[0], a[1])
    x, fs = load_audio(a)
    return features(x, fs)


# ============================================================================= intent verification
def _intents():
    return _data(os.path.join("mixeng", "intents.json"))


def parse_intents(text):
    """Goal text -> {"intents": [...], "ambiguous": [...]}. Modifiers flip a quality word to its opposite intent:
    'too loud' / 'less loud' -> quieter, 'too bright' -> darker, 'make it less wide' -> narrower, 'its too quiet' ->
    louder. Problem words keep their fix ('too muddy' -> less_muddy). A flipped word with no clear opposite
    ('too clean') goes to ambiguous instead of being guessed. Longest phrases first."""
    if not text:
        return {"intents": [], "ambiguous": []}
    D = _intents(); words = D["words"]; opp = D.get("opposites") or {}
    probs = {_norm_name(w) for w in D.get("problem_words") or []}
    flips = sorted({_norm_name(w) for w in D.get("flip_before") or []}, key=len, reverse=True)
    t = " " + _norm_name(text).replace("'", " ") + " "
    found = []                                                   # (position, intent or None, word)
    pairs = sorted(((w, i) for i, ws in words.items() for w in ws), key=lambda p: -len(p[0]))
    for w, intent in pairs:
        nw = _norm_name(w)
        if not nw:
            continue
        for mt in re.finditer(r"(?<= )" + re.escape(nw) + r"(?= )" if len(nw) <= 4 else re.escape(nw), t):
            a, b = mt.start(), mt.end()
            if t[a:b].strip() == "" or "\0" in t[a:b]:
                continue
            before = t[:a].rstrip()
            mod = next((f for f in flips if before.endswith(" " + f) or before == f), None)
            res = intent
            if mod and t[b:].lstrip().startswith("enough"):     # "not bright enough" = wants brighter
                mod = None
            if mod and not nw.split()[0] in ("less", "more") and nw not in probs:
                res = opp.get(intent, None)
                if res is None:
                    found.append((a, None, f"{mod} {nw}"))
                    t = t[:a] + "\0" * (b - a) + t[b:]
                    continue
            found.append((a, res, nw))
            t = t[:a] + "\0" * (b - a) + t[b:]
    found.sort()
    hits = [i for _, i, _ in found if i]
    amb = [w for _, i, w in found if i is None]
    return {"intents": list(dict.fromkeys(hits)), "ambiguous": list(dict.fromkeys(amb))}


def intents_from_text(text):
    """'make the vocal clearer and less muddy' -> ['clearer', 'less_muddy']; 'it is too loud' -> ['quieter']."""
    return parse_intents(text)["intents"]


def _judge(spec, mb, ma, min_override=None):
    k = spec["metric"]; a, b = mb.get(k), ma.get(k)
    if a is None or b is None:
        return None
    d = float(b) - float(a); want = float(min_override if min_override is not None else spec["min"])
    toward = d * spec["dir"]
    if toward >= want:
        status = "achieved"
    elif toward >= 0.25 * want:
        status = "barely_changed"
    elif toward <= -0.5 * want:
        status = "opposite"
    else:
        status = "no_change"
    return {"metric": k, "before": _r(a, 2), "after": _r(b, 2), "delta": _r(d, 2), "status": status, "need": want}


def intent_verify(before, after, intents, detail=False):
    """Did an edit do what was asked? before/after: audio paths, (x, fs) or measurement dicts. intents: names
    ('darker', 'less_muddy', ...), free text ('make it warmer'), or [{intent, min}] for a custom threshold.
    -> {results: [{intent, status: achieved|barely_changed|no_change|opposite|unmeasurable, delta, say}], all_ok}"""
    spec_all = _intents()["intents"]
    if isinstance(intents, str):
        found = [i.strip().replace(" ", "_") for i in re.split(r"[,;]", intents)]
        intents = [i for i in found if i in spec_all] or intents_from_text(intents)
    mb, ma = metrics(_measure_any(before)), metrics(_measure_any(after))
    res = []
    for it in intents or []:
        name, mn = (it.get("intent"), it.get("min")) if isinstance(it, dict) else (it, None)
        name = str(name).strip().lower().replace(" ", "_").replace("-", "_")
        spec = spec_all.get(name)
        if not spec:
            res.append({"intent": name, "status": "unknown_intent", "say": f"unknown intent; known: {', '.join(spec_all)}"})
            continue
        judged = [j for j in [_judge(spec, mb, ma, mn)] + [_judge(dict(a), mb, ma) for a in spec.get("alt", [])] if j]
        if not judged:
            res.append({"intent": name, "status": "unmeasurable",
                        "say": f"can't measure {spec['label']} from these inputs (need audio or {spec['metric']})"})
            continue
        order = ["achieved", "barely_changed", "no_change", "opposite"]
        best = min(judged, key=lambda j: order.index(j["status"]))
        prim = judged[0] if judged[0]["metric"] == spec["metric"] else best
        unit = "LU" if best["metric"] == "lufs" else ("dB/oct" if best["metric"] == "tilt" else
                                                       ("" if best["metric"] == "corr" else "dB"))
        lbl = spec["label"] if best["metric"] == spec["metric"] else best["metric"].replace("_db", "").replace("_", " ")
        if best["status"] == "achieved":
            say = f"achieved: {lbl} {best['delta']:+.1f} {unit}".strip()
        elif best["status"] == "barely_changed":
            say = f"barely changed ({lbl} {best['delta']:+.1f} {unit}, want {best['need']:g}+): want more?"
        elif best["status"] == "opposite":
            say = f"went the wrong way: {lbl} {best['delta']:+.1f} {unit}"
        else:
            say = f"no meaningful change ({lbl} {best['delta']:+.1f} {unit})"
        if spec.get("proxy"):
            say += " (proxy metric: listen to confirm)"
        r = {"intent": name, "status": best["status"], "metric": best["metric"], "delta": best["delta"], "say": say}
        if detail:
            r["checks"] = judged; r["primary"] = prim
        res.append(r)
    return {"results": res, "all_ok": bool(res) and all(r["status"] == "achieved" for r in res),
            "note": "band metrics are shares of the whole spectrum (dB), so a plain level change doesn't count"}


def fl_verify_intent(before, after, intents, detail=False):
    return intent_verify(before, after, intents, detail=detail)


# ============================================================================= platform previews
def platform_preview(audio=None, lufs=None, true_peak=None, platforms=None):
    """How a master plays on streaming services: per platform the playback gain, whether it's turned down/up,
    true-peak risk after lossy encoding, one-line advice. audio: WAV/FLAC (or give lufs + true_peak)."""
    import numpy as np
    P = _data("platforms.json")
    if audio is not None:
        x, fs = load_audio(audio)
        lufs = _mlufs(x, fs); true_peak = _tpk(x)
        xs_ = np.asarray(x[::max(1, len(x) // 200000)], dtype=np.float64)
        if lufs is None or not len(xs_) or float(np.max(np.std(xs_, axis=0))) < 10 ** (-70 / 20):
            raise ValueError(f"{os.path.basename(str(audio))} is silent or shorter than 0.4 s: there is no loudness "
                             "for a streaming service to normalize")
    if lufs is None or true_peak is None:
        raise ValueError("give audio, or lufs and true_peak")
    lufs, tp = float(lufs), float(true_peak)
    over = float(P.get("lossy_overshoot_db", 1.0))
    rows = []
    for key, p in P["platforms"].items():
        if platforms and key not in platforms and p["name"] not in platforms:
            continue
        tgt = p.get("target_lufs")
        if tgt is None or not p.get("normalized_by_default"):
            gain = 0.0; action = "plays as is (no normalization)"
        else:
            gain = tgt - lufs
            if gain > 0 and not p.get("turns_up"):
                gain = 0.0
            if gain > 0 and p.get("boost_peak_limit_dbtp") is not None:
                gain = max(0.0, min(gain, float(p["boost_peak_limit_dbtp"]) - tp))
            action = ("turned down" if gain < -0.5 else "turned up" if gain > 0.5 else "about unchanged")
        enc_peak = tp + over
        risk = "high" if enc_peak > 0.0 else ("some" if enc_peak > -0.5 else "low")
        advice_tp = p.get("loud_master_tp_advice_dbtp") if (tgt is not None and lufs > tgt and
                                                            p.get("loud_master_tp_advice_dbtp")) else p.get("max_tp_advice_dbtp")
        if tgt is not None and p.get("normalized_by_default") and lufs > tgt + 2:
            rec = (f"{lufs - tgt:.1f} dB louder than {p['name']} plays it: it will be turned down {-gain:.1f} dB, so "
                   f"extra limiting buys nothing here; keep peaks under {advice_tp:g} dBTP")
        elif tgt is not None and p.get("normalized_by_default") and lufs < tgt - 2:
            rec = (f"quieter than the {tgt:g} LUFS target: " + (f"turned up {gain:.1f} dB (limited by peak headroom)"
                   if gain > 0.5 else "not turned up (or no headroom): it will sound quieter than other songs"))
        else:
            rec = f"close to {p['name']}'s level" if tgt is not None else "loudness is up to you here"
        if risk != "low":
            rec += f"; true peak {tp:+.1f} dBTP may clip after lossy encoding (~{over:g} dB overshoot)"
        rows.append({"platform": p["name"], "target_lufs": tgt, "gain_db": round(gain, 1), "action": action,
                     "plays_at_lufs": round(lufs + gain, 1), "peak_after_gain_dbtp": round(tp + gain, 1),
                     "lossy_peak_risk": risk, "status": p.get("status"), "say": rec})
    return {"lufs": round(lufs, 2), "true_peak": round(tp, 2), "as_of": P.get("as_of"), "platforms": rows,
            "note": P.get("note")}


def fl_platform_preview(audio=None, lufs=None, true_peak=None, platforms=None):
    return platform_preview(audio, lufs, true_peak, platforms)


# ============================================================================= roles, stems, sections
def _genres():
    return _data(os.path.join("mixeng", "genres.json"))


ROLE_ORDER = ["vocal", "kick", "bass", "drums", "perc", "lead", "keys", "guitar", "strings", "pad", "fx"]


RHYTHM_ROLES = ("bass", "kick", "drums", "perc", "fx")      # never 'the lead' while a melodic part exists


def role_of(name):
    """Track role from its name (genres.json 'roles' keywords): vocal, lead, bass, kick, drums, perc, keys, guitar,
    strings, pad, fx, or support."""
    n = " " + _norm_name(name) + " "
    words = _genres()["roles"]
    for r in ROLE_ORDER:
        for w in words.get(r, []):
            w = _norm_name(w)
            if w and (" " + w + " " in n or (len(w) >= 4 and w in n)):
                return r
    return "support"


def importance(role):
    return float(_genres()["importance"].get(role, 1.4))


def genre_key(genre=None, project=None):
    """Canonical genre from text ('deep house' -> edm), else the journal/profile style, else 'default'."""
    G = _genres()
    text = genre
    if not text and project:
        try:
            S = sys.modules.get("flh_store")
            jr = S.journal_get(project) if S else {}
            text = jr.get("genre") or jr.get("style")
        except Exception:
            text = None
    if not text:
        try:
            S = sys.modules.get("flh_store")
            text = (S.profile_get() or {}).get("genre") if S and S.H else None
        except Exception:
            text = None
    if not text:
        return "default"
    t = _norm_name(text)
    if t in G["genres"]:
        return t
    for g, ws in G["genre_words"].items():
        if any(_norm_name(w) in t for w in ws):
            return g
    return "default"


def norms(genre="default"):
    """Genre norms merged over the defaults, then the collection profile's 'mix_norms' (user taste) on top."""
    G = _genres()["genres"]
    out = _deep_merge(G["default"], G.get(genre, {})) if genre != "default" else dict(G["default"])
    try:
        S = sys.modules.get("flh_store")
        if S and S.H:
            out = _deep_merge(out, (S.profile_get() or {}).get("mix_norms") or {})
    except Exception:
        pass
    return out


def _stem_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r" - (\d{2,}) (.+)$", stem)
    return (m.group(2).strip(), int(m.group(1))) if m else (stem, None)


def _stem_files(stems):
    if isinstance(stems, str):
        if os.path.isdir(stems):
            return sorted(glob.glob(os.path.join(stems, "*.wav")) + glob.glob(os.path.join(stems, "*.flac")))
        return [stems]
    return list(stems or [])


def _sections_s(sections, project, seconds):
    """[{name, start_s, end_s}] from explicit sections (seconds or bars), the project's journal/playlist, else
    one 'whole song' section."""
    secs = sections
    R = sys.modules.get("flh_review")
    jr = {}
    if project:
        try:
            S = sys.modules.get("flh_store"); jr = S.journal_get(project) if S else {}
        except Exception:
            jr = {}
        if not secs and R is not None and getattr(R, "H", None):
            try:
                secs = R._sections(project, jr, None)
            except Exception:
                secs = None
    if not secs:
        return [{"name": "whole song", "start_s": 0.0, "end_s": float(seconds)}]
    tf = None
    if any("start_s" not in s for s in secs):
        if R is None or not project:
            raise ValueError("sections in bars need the project (for its tempo); or give start_s/end_s")
        tf = R._time_fn(project, jr)
    out = []
    for i, s in enumerate(secs):
        if "start_s" in s:
            a, b = float(s["start_s"]), float(s.get("end_s") or seconds)
        else:
            nb = s.get("end_bar") or (secs[i + 1].get("bar") if i + 1 < len(secs) else None)
            a, b = tf(s["bar"]), (tf(nb) if nb else float(seconds))
        b = min(b, float(seconds))
        if b - a >= 1.0:
            out.append({"name": s.get("name") or f"part {i + 1}", "start_s": a, "end_s": b})
    return out or [{"name": "whole song", "start_s": 0.0, "end_s": float(seconds)}]


def _band_frames(x, fs):
    """Per-frame 1/3-octave band powers of the mid signal: (E frames x bands, hop seconds)."""
    import numpy as np
    mono = x[:, :2].mean(1)
    n = int(2 ** round(math.log2(fs * 0.093))); hop = n // 2
    w = np.hanning(n)
    f = np.fft.rfftfreq(n, 1 / fs)
    B = np.zeros((len(f), len(THIRD_OCT)))
    for j, c in enumerate(THIRD_OCT):
        lo, hi = _band_edges(c)
        B[(f >= lo) & (f < hi), j] = 1.0
    nfr = max(0, (len(mono) - n) // hop + 1)
    E = np.zeros((nfr, len(THIRD_OCT)))
    col = np.ascontiguousarray(mono, dtype=np.float64)          # the strides below assume 8-byte samples
    sv = np.lib.stride_tricks.sliding_window_view(col, n)[::hop] if nfr else None
    for a in range(0, nfr, 256):
        b = min(nfr, a + 256)
        fr = sv[a:b]
        E[a:b] = (np.abs(np.fft.rfft(fr * w, axis=1)) ** 2) @ B
    return E * (2.0 / (n * n * 0.375)), hop / fs


# ============================================================================= masking
def _fmt_hz(hz):
    return f"{hz / 1000:.1f} kHz" if hz >= 1000 else f"{int(round(hz))} Hz"


def _band_weight(c, target_role):
    if target_role in ("vocal", "lead"):
        return 1.0 if 800 <= c <= 5000 else (0.8 if 200 <= c <= 10000 else 0.5)
    return 0.8


def _pair(ET, EM, rT, weight, thr):
    """Ranges where masker M covers target T: [(lo_band, hi_band, overlap, coverage, severity)]."""
    import numpy as np
    if len(ET) < 3 or len(EM) < 3:
        return []
    k = min(len(ET), len(EM)); ET, EM = ET[:k], EM[:k]
    LT = 10 * np.log10(ET + 1e-14); LM = 10 * np.log10(EM + 1e-14)
    tot = 10 * np.log10(ET.sum(1) + 1e-14)
    act = tot > (np.percentile(tot, 95) - 30)
    if act.sum() < 3:
        return []
    sal = (LT > LT.max(1, keepdims=True) - 20) & act[:, None]
    d = np.clip((LM - LT + 12.0) / 18.0, 0.0, 1.0)
    ns = sal.sum(0)
    o = np.where(ns >= 3, (d * sal).sum(0) / np.maximum(ns, 1), 0.0)
    cov = ns / max(1, int(act.sum()))
    s = o * np.minimum(1.0, cov * 2)
    out, j = [], 0
    while j < len(s):
        if s[j] >= thr:
            a = j
            while j + 1 < len(s) and s[j + 1] >= thr:
                j += 1
            rng = range(a, j + 1); cw = sum(cov[i] for i in rng) or 1e-9
            ov = sum(o[i] * cov[i] for i in rng) / cw
            wb = sum(_band_weight(THIRD_OCT[i], rT) for i in rng) / len(rng)
            sev = ov * wb * weight * min(1.0, 2 * (cw / len(rng)))
            out.append((a, j, float(ov), float(cw / len(rng)), float(sev)))
        j += 1
    return out


def _register(E):
    """Bands where a part has most of its energy (within 6 dB of its strongest band, long-term)."""
    import numpy as np
    L = 10 * np.log10(E.mean(0) + 1e-14)
    return [i for i, v in enumerate(L) if v > L.max() - 6]


def _fixes(M, T, a, b, ov, crestM):
    lo, hi = _band_edges(THIRD_OCT[a])[0], _band_edges(THIRD_OCT[b])[1]
    fc = math.sqrt(lo * hi); q = min(4.0, max(0.7, fc / (hi - lo)))
    gain = -min(6.0, round((2.0 + 4.0 * ov) * 2) / 2)
    fx = [{"kind": "eq_dip", "track": M.get("track"), "name": M["name"], "plugin": "Fruity Parametric EQ 2",
           "hz": int(round(fc, -1)), "q": round(q, 2), "gain_db": gain,
           "say": f"dip {M['name']} {gain:+.1f} dB at {_fmt_hz(fc)} (Q {q:.1f})"}]
    if T["role"] in ("vocal", "lead") and (crestM is None or crestM < 14):
        fx.append({"kind": "sidechain", "track": M.get("track"),
                   "say": f"or duck {M['name']} from {T['name']} only while it plays: Fruity Limiter (COMP) on "
                          f"{M['name']} sidechained from {T['name']}'s track, or a Maximus band with sidechain "
                          f"for a dynamic dip at {_fmt_hz(fc)}"})
    regT, regM = T.get("_reg") or [], M.get("_reg") or []
    shared = set(range(a, b + 1)) & set(regT) & set(regM)
    if shared and THIRD_OCT[a] < 2000:
        down = (sum(regT) / len(regT)) >= (sum(regM) / len(regM))
        fx.append({"kind": "arrangement", "track": M.get("track"),
                   "say": f"or move {M['name']} an octave {'down' if down else 'up'} (or thin its voicing) so it "
                          f"leaves {T['name']}'s register"})
    return fx, lo, hi


def masking_from_stems(items, sections, lead=None, top=8, threshold=0.25):
    """items: [{name, track?, x, fs}] aligned from the same start. -> ranked findings with fixes."""
    import numpy as np
    parts = []
    for it in items:
        E, hop = _band_frames(it["x"], it["fs"])
        if not len(E) or float(E.sum()) <= 1e-12:
            continue
        rl = role_of(it["name"])
        if lead and _norm_name(lead) == _norm_name(it["name"]):
            rl = "lead"
        mid = it["x"].mean(1); pk = float(np.abs(mid).max()) + 1e-12; rms = float(np.sqrt(np.mean(mid ** 2))) + 1e-12
        parts.append(dict(name=it["name"], track=it.get("track"), role=rl, imp=importance(rl), E=E, hop=hop,
                          crest=20 * math.log10(pk / rms), _reg=_register(E)))
    if lead:
        for p in parts:
            if _norm_name(p["name"]) == _norm_name(lead):
                p["imp"] = 4.0
    finds = []
    for sec in sections:
        for T in parts:
            a0, b0 = int(sec["start_s"] / T["hop"]), int(sec["end_s"] / T["hop"])
            for M in parts:
                m0, m1 = int(sec["start_s"] / M["hop"]), int(sec["end_s"] / M["hop"])
                if M is T or M["imp"] > T["imp"] or (M["imp"] == T["imp"] and M["name"] < T["name"] and
                                                     T["role"] not in ("vocal", "lead")):
                    continue
                w = 1.0 if T["role"] in ("vocal", "lead") or T["imp"] >= 3 else (0.7 if T["role"] in ("bass", "kick") else 0.5)
                for a, b, ov, cov, sev in _pair(T["E"][a0:b0], M["E"][m0:m1], T["role"], w, 0.35):
                    if sev < threshold:
                        continue
                    fx, lo, hi = _fixes(M, T, a, b, ov, M["crest"])
                    finds.append({"masker": M["name"], "masker_track": M["track"], "target": T["name"],
                                  "target_track": T["track"], "section": sec["name"], "lo_hz": int(lo), "hi_hz": int(hi),
                                  "overlap": round(ov, 2), "severity": round(sev, 2),
                                  "say": f"{M['name']} masks {T['name']} at {_fmt_hz(lo)}-{_fmt_hz(hi)} in "
                                         f"{sec['name']} (overlap {ov:.2f})", "fixes": fx})
    finds.sort(key=lambda f: -f["severity"])
    # the same masker/target/range in several sections: keep the worst, list the others
    merged = []
    for f in finds:
        same = next((g for g in merged if g["masker"] == f["masker"] and g["target"] == f["target"] and
                     abs(math.log(g["lo_hz"] / f["lo_hz"])) < 0.5), None)
        if same:
            same.setdefault("also_in", []).append(f["section"])
        else:
            merged.append(f)
    return merged[:int(top)]


def _live_captures(tracks, start_bar, end_bar, seconds):
    # PC-VERIFY: mutes restored exactly as before, master FX re-enabled, passes start at the same bar
    """Each mixer track heard alone (others muted, master effects bypassed) through fl_listen; recordings are
    kept in a temp folder of fl-headless and removed after loading. -> [{name, track, x, fs}]"""
    import shutil
    import tempfile
    st = H["fl_state"]()
    names = {m["index"]: m["name"] for m in st["mixer"]}
    used = sorted({c["insert"] for c in st["channels"] if c.get("plugin") and c.get("insert", 0) > 0})
    todo = [int(t) for t in tracks] if tracks else used
    tracks_used = sorted({m["index"] for m in st["mixer"] if m["index"] > 0})
    was = {m["index"]: bool(m.get("muted")) for m in st["mixer"]}
    master_fx = bool(next((m for m in st["mixer"] if m["index"] == 0), {}).get("effects"))
    work = tempfile.mkdtemp(prefix="flh_mask_"); H["_TEMP_ROOTS"].append(os.path.normcase(os.path.abspath(work)))
    out = []
    try:
        if master_fx:
            _bridge("mixer_set", track=0, effects_enabled=False)
        for i, t in enumerate(todo):
            _progress(step=f"hearing {names.get(t, t)} alone", pct=int(100 * i / max(1, len(todo))))
            for u in tracks_used:
                _bridge("mixer_set", track=u, muted=(u != t) or was.get(u, False))
            path = os.path.join(work, f"t{t:03d}.flac")
            r = H["fl_listen"](start_bar=start_bar, end_bar=end_bar, seconds=seconds, save=path)
            if r.get("heard") and os.path.isfile(path):
                x, fs = load_audio(path)
                out.append({"name": names.get(t) or f"Insert {t}", "track": t, "x": x, "fs": fs})
    finally:
        for u in tracks_used:
            try:
                _bridge("mixer_set", track=u, muted=was.get(u, False))
            except Exception:
                pass
        if master_fx:
            try:
                _bridge("mixer_set", track=0, effects_enabled=True)
            except Exception:
                pass
        try:
            H["_TEMP_ROOTS"].remove(os.path.normcase(os.path.abspath(work)))
        except ValueError:
            pass
        shutil.rmtree(work, ignore_errors=True)
    return out


def _stem_items(stems):
    items = []
    for f in _stem_files(stems):
        x, fs = load_audio(f)
        name, ch = _stem_name(f)
        items.append({"name": name, "channel": ch, "track": None, "x": x, "fs": fs, "file": f})
    _map_tracks(items)
    return items


def _map_tracks(items):
    """Stem channel number -> mixer track (from the open FL, when the bridge answers)."""
    if not any(it.get("channel") is not None for it in items):
        return
    try:
        st = H["fl_state"]()
    except Exception:
        return
    ins = {c["index"]: c.get("insert") for c in st.get("channels", [])}
    for it in items:
        if it.get("channel") is not None and ins.get(it["channel"]):
            it["track"] = ins[it["channel"]]


def masking(stems=None, sections=None, project=None, lead=None, live=False, tracks=None, start_bar=None,
            end_bar=None, seconds=None, top=8, threshold=0.25, section_name=None):
    """Which tracks fight in the same band, per section; masking of the lead/vocal ranks first.
    stems: folder or list of WAV/FLAC stems (fl_render_stems output; same start). live=True: hear each mixer track
    alone in the open FL (one passage: start_bar..end_bar or seconds)."""
    steps, mark = _steps()
    if live:
        items = _live_captures(tracks, start_bar, end_bar, seconds)
        dur = min(len(it["x"]) / it["fs"] for it in items) if items else 0
        secs = [{"name": section_name or (f"bars {start_bar}-{end_bar}" if start_bar else "passage"), "start_s": 0.0,
                 "end_s": dur}]
    else:
        if not stems:
            raise ValueError("give stems (a folder or files from fl_render_stems) or live=true")
        items = _stem_items(stems)
        if tracks:
            want = {str(t).lower() for t in tracks}
            items = [it for it in items if it["name"].lower() in want or str(it.get("track")) in want
                     or str(it.get("channel")) in want]
        dur = max(len(it["x"]) / it["fs"] for it in items) if items else 0
        secs = _sections_s(sections, project, dur)
        if section_name:
            secs = [s for s in secs if _norm_name(s["name"]).startswith(_norm_name(section_name))] or secs
    mark("load")
    if len(items) < 2:
        return {"findings": [], "note": "need at least two audible tracks", "steps_s": steps}
    res = masking_from_stems(items, secs, lead=lead, top=top, threshold=threshold)
    mark("analyse")
    lead_hits = [f for f in res if role_of(f["target"]) in ("vocal", "lead") or (lead and f["target"] == lead)]
    return {"findings": res, "tracks": [it["name"] for it in items], "sections": [s["name"] for s in secs],
            "lead_masked": len(lead_hits), "steps_s": steps,
            "note": ("live captures are separate passes (aligned only roughly): stems are more precise"
                     if live else "from stems; fixes are starting points: listen after applying")}


def fl_masking(stems=None, sections=None, project=None, lead=None, live=False, tracks=None, start_bar=None,
               end_bar=None, seconds=None, section=None, top=8, background=None):
    kw = dict(stems=stems, sections=sections, project=project, lead=lead, live=live, tracks=tracks,
              start_bar=start_bar, end_bar=end_bar, seconds=seconds, top=top, section_name=section)
    est = (len(tracks) if tracks else 6) * (float(seconds or 20) + 4) if live else 8
    return C.auto_job(H, "masking", est, masking, kw, background, fl=bool(live))   # stems: files only, FL stays free


# ============================================================================= reference matching
def _st_curve(x, fs):
    """Short-term (3 s) loudness per 100 ms, LUFS (nan where silent)."""
    import numpy as np
    p = _p100(x, fs)
    if len(p) < 30:
        st = np.array([p.mean()]) if len(p) else np.zeros(1)
    else:
        st = np.convolve(p, np.ones(30) / 30, "valid")
    L = -0.691 + 10 * np.log10(np.maximum(st, 1e-12))
    return np.where(L > -70, L, np.nan)


def _norm_curve(L, points):
    import numpy as np
    if not len(L):
        return np.full(points, np.nan)
    t = np.linspace(0, 1, len(L)); g = np.linspace(0, 1, points)
    ok = ~np.isnan(L)
    if ok.sum() < 2:
        return np.full(points, np.nan)
    out = np.interp(g, t[ok], L[ok])
    # mark grid points that fall into long silent stretches
    idx = np.clip((g * (len(L) - 1)).astype(int), 0, len(L) - 1)
    out[~ok[idx]] = np.nan
    return out


EQ_REGIONS = [("low_shelf", 20, 110, 100, "Low shelf", None, "lows (<110 Hz)"),
              ("low_mid", 180, 560, 315, "Peaking", 1.0, "low-mids (180-560 Hz)"),
              ("mid", 560, 1800, 1000, "Peaking", 0.9, "mids (0.6-1.8 kHz)"),
              ("presence", 1800, 5600, 3200, "Peaking", 1.0, "presence (1.8-5.6 kHz)"),
              ("high_shelf", 7000, 17000, 8000, "High shelf", None, "highs (>7 kHz)")]


def reference_compare(ref, mine, ref_sections=None, sections=None, points=20):
    """ref/mine: (x, fs). The biggest differences with concrete moves. Pure (no FL)."""
    import numpy as np
    (rx, rfs), (mx, mfs) = ref, mine
    fr, fm = features(rx, rfs, full=True), features(mx, mfs, full=True)
    for nm, f_, x_ in (("reference", fr, rx), ("your mix", fm, mx)):
        xs_ = np.asarray(x_[::max(1, len(x_) // 200000)], dtype=np.float64)
        if f_.get("lufs") is None or not len(xs_) or float(np.max(np.std(xs_, axis=0))) < 10 ** (-70 / 20):
            raise ValueError(f"the {nm} is silent, DC only or shorter than 0.4 s: nothing to compare (pick a "
                             f"passage with music in it)")
    diffs, moves = [], []
    # --- loudness and dynamics
    dl = (fm["lufs"] or -70) - (fr["lufs"] or -70)
    plr_r = (fr["true_peak"] or 0) - (fr["lufs"] or -70); plr_m = (fm["true_peak"] or 0) - (fm["lufs"] or -70)
    Lr, Lm = _st_curve(rx, rfs), _st_curve(mx, mfs)
    def lra(L):
        v = L[~np.isnan(L)]
        v = v[v > (np.nanmax(L) - 25)] if len(v) else v
        return float(np.percentile(v, 95) - np.percentile(v, 10)) if len(v) > 5 else None
    lr_r, lr_m = lra(Lr), lra(Lm)
    if abs(dl) >= 1.0:
        diffs.append({"what": "loudness", "mine": fm["lufs"], "ref": fr["lufs"], "delta": round(dl, 1),
                      "say": f"{abs(dl):.1f} LU {'quieter' if dl < 0 else 'louder'} than the reference",
                      "weight": abs(dl) / 2})
        moves.append({"kind": "loudness", "target_lufs": fr["lufs"],
                      "say": f"finish to {fr['lufs']:.1f} LUFS (fl_finalize / audio_limit, true-peak safe) - level "
                             f"only matters off normalized platforms (see fl_platform_preview)"})
    dplr = plr_m - plr_r
    if abs(dplr) >= 2.0:
        diffs.append({"what": "peak_to_loudness", "mine": round(plr_m, 1), "ref": round(plr_r, 1), "delta": round(dplr, 1),
                      "say": f"peak-to-loudness {plr_m:.1f} vs {plr_r:.1f} dB: yours is "
                             f"{'more dynamic / less limited' if dplr > 0 else 'more squashed'}", "weight": abs(dplr) / 2})
        if dplr > 0:
            moves.append({"kind": "compression", "settings": {"ratio": 2.0, "attack_ms": 30, "gr_db": round(min(3, dplr / 3), 1)},
                          "say": f"about {dplr:.0f} dB more density: master bus comp 2:1, 30 ms attack, "
                                 f"~{min(3, dplr / 3):.1f} dB gain reduction, then the limiter for the rest"})
        else:
            moves.append({"kind": "compression", "say": f"back off master limiting/compression by ~{-dplr:.0f} dB "
                                                        "(lower the limiter input or the comp's ratio)"})
    if lr_r is not None and lr_m is not None and abs(lr_m - lr_r) >= 3:
        diffs.append({"what": "loudness_range", "mine": round(lr_m, 1), "ref": round(lr_r, 1),
                      "delta": round(lr_m - lr_r, 1), "weight": abs(lr_m - lr_r) / 3,
                      "say": f"loudness range {lr_m:.1f} vs {lr_r:.1f} LU: yours "
                             f"{'moves more between quiet and loud parts' if lr_m > lr_r else 'is flatter'}"})
    # --- loudness curve over normalized time
    cr = _norm_curve(Lr, points) - (fr["lufs"] or 0); cm = _norm_curve(Lm, points) - (fm["lufs"] or 0)
    dc = cm - cr; curve_notes = []
    j = 0
    while j < points:
        if not np.isnan(dc[j]) and abs(dc[j]) >= 3:
            a = j; sgn = np.sign(dc[j])
            while j + 1 < points and not np.isnan(dc[j + 1]) and abs(dc[j + 1]) >= 3 and np.sign(dc[j + 1]) == sgn:
                j += 1
            v = float(np.nanmean(dc[a:j + 1]))
            curve_notes.append({"from_pct": int(100 * a / (points - 1)), "to_pct": int(100 * j / (points - 1)),
                                "delta_lu": round(v, 1),
                                "say": f"at {int(100 * a / (points - 1))}-{int(100 * j / (points - 1))}% of the song "
                                       f"yours is {v:+.1f} LU vs its average compared to the reference"})
        j += 1
    curve_notes.sort(key=lambda c: -abs(c["delta_lu"]))
    if curve_notes:
        diffs.append({"what": "energy_curve", "say": curve_notes[0]["say"], "weight": abs(curve_notes[0]["delta_lu"]) / 3,
                      "regions": curve_notes[:3]})
    sec_rows = []
    if sections:
        dm, dr = len(mx) / mfs, len(rx) / rfs
        for i, s in enumerate(sections):
            a, b = float(s["start_s"]), float(s["end_s"])
            if ref_sections and i < len(ref_sections):
                ra, rb = float(ref_sections[i]["start_s"]), float(ref_sections[i]["end_s"])
            else:
                ra, rb = a / dm * dr, b / dm * dr
            Ls = _mlufs(mx[int(a * mfs):int(b * mfs)], mfs); Rs = _mlufs(rx[int(ra * rfs):int(rb * rfs)], rfs)
            if Ls is None or Rs is None:
                continue
            d = (Ls - fm["lufs"]) - (Rs - fr["lufs"])
            sec_rows.append({"section": s.get("name"), "mine_rel_lu": round(Ls - fm["lufs"], 1),
                             "ref_rel_lu": round(Rs - fr["lufs"], 1), "delta": round(d, 1)})
    # --- tonal balance
    third_d = [m - r for m, r in zip(fm["third_oct"], fr["third_oct"])]
    off = float(np.median(third_d))                               # level-align the curves: a boost in one region
    third_d = [d - off for d in third_d]                          # shouldn't read as a cut everywhere else
    tilt_d = (fm["tilt"] or 0) - (fr["tilt"] or 0)
    eq = []
    for key, lo, hi, hz, typ, q, label in EQ_REGIONS:
        vals = [d for c, d in zip(THIRD_OCT, third_d) if lo <= c < hi]
        if not vals:
            continue
        dv = sum(vals) / len(vals)
        if abs(dv) >= 1.0:
            g = -max(-4.0, min(4.0, round(0.75 * dv * 2) / 2))
            if g == 0:
                continue
            eq.append({"region": key, "hz": hz, "type": typ, "q": q, "gain_db": g, "delta_db": round(dv, 1),
                       "say": f"{label} {dv:+.1f} dB vs the reference: {typ.lower()} {g:+.1f} dB at {_fmt_hz(hz)}"
                              + (f" (Q {q:g})" if q else "")})
    if eq:
        top = max(eq, key=lambda e: abs(e["delta_db"]))
        diffs.append({"what": "tonal_balance", "say": f"tone: {top['say']}", "weight": abs(top["delta_db"]) / 1.5,
                      "tilt_delta_db_oct": round(tilt_d, 2)})
        moves.append({"kind": "master_eq", "bands": eq, "say": "; ".join(e["say"] for e in eq)})
    dlow = (fm["low_ratio_db"] or -60) - (fr["low_ratio_db"] or -60) - off   # same level alignment as the curves
    if abs(dlow) >= 2:
        diffs.append({"what": "low_end_ratio", "mine": fm["low_ratio_db"], "ref": fr["low_ratio_db"],
                      "delta": round(dlow, 1), "weight": abs(dlow) / 2.5,
                      "say": f"low end (<120 Hz) {dlow:+.1f} dB vs the reference's share"})
    # --- stereo width per band
    wnotes = []
    for n, _, _ in WIDTH_BANDS:
        a, b = fm["width_bands"].get(n), fr["width_bands"].get(n)
        if a is None or b is None:
            continue
        d = a - b
        if abs(d) >= 3 and not (n == "low" and d < 0):           # a narrower low end than the reference is fine
            wnotes.append({"band": n, "delta_db": round(d, 1)})
    if wnotes:
        top = max(wnotes, key=lambda w: abs(w["delta_db"]))
        diffs.append({"what": "stereo_width", "bands": wnotes, "weight": abs(top["delta_db"]) / 4,
                      "say": f"stereo width ({top['band'].replace('_', '-')}) {top['delta_db']:+.1f} dB side level vs "
                             f"the reference"})
        for w in wnotes:
            if w["band"] == "low" and w["delta_db"] > 0:
                moves.append({"kind": "width", "band": "low", "say": "low end is wider than the reference: keep bass "
                              "and kick centered / mono below ~120 Hz (Fruity Stereo Shaper or Stereo Enhancer on the "
                              "bass tracks, not the master)"})
            elif w["band"] != "low" and w["delta_db"] < 0:
                moves.append({"kind": "width", "band": w["band"], "say": f"narrower {w['band'].replace('_', '-')} "
                              "than the reference: pan support parts apart / double them, or widen pads with Fruity "
                              "Stereo Enhancer (check mono compatibility)"})
            elif w["band"] != "low":
                moves.append({"kind": "width", "band": w["band"], "say": f"wider {w['band'].replace('_', '-')} than the "
                              "reference: bring wide parts in (pan less, reduce stereo enhancers)"})
    if fm["corr"] is not None and fm["corr"] < 0.1 and (fr["corr"] or 1) > 0.3:
        diffs.append({"what": "phase", "say": f"L/R correlation {fm['corr']:.2f} vs {fr['corr']:.2f}: check for "
                      "phase problems / over-widening", "weight": 3.0})
    diffs.sort(key=lambda d: -d.pop("weight", 0))
    return {"mine": {k: fm[k] for k in ("lufs", "true_peak", "crest_db", "tilt", "width_db", "corr", "low_ratio_db")},
            "reference": {k: fr[k] for k in ("lufs", "true_peak", "crest_db", "tilt", "width_db", "corr", "low_ratio_db")},
            "differences": diffs, "moves": moves, "sections": sec_rows or None,
            "third_oct_delta_db": dict(zip([str(c) for c in THIRD_OCT], [round(v, 1) for v in third_d]))}


def reference_match(reference, mix=None, sections=None, ref_sections=None, project=None, apply=False,
                    full=False, live_seconds=None):
    """Compare a render (or a live take: mix=None + live_seconds) with the user's own reference song."""
    steps, mark = _steps()
    R = load_audio(reference); mark("load_reference")
    if mix is None:
        if not live_seconds:
            raise ValueError("give mix (a render) or live_seconds to record the open FL")
        import tempfile, shutil
        work = tempfile.mkdtemp(prefix="flh_ref_"); H["_TEMP_ROOTS"].append(os.path.normcase(os.path.abspath(work)))
        try:
            path = os.path.join(work, "take.flac")
            H["fl_listen"](seconds=float(live_seconds), save=path)
            M = load_audio(path)
        finally:
            try:
                H["_TEMP_ROOTS"].remove(os.path.normcase(os.path.abspath(work)))
            except ValueError:
                pass
            shutil.rmtree(work, ignore_errors=True)
    else:
        M = load_audio(mix)
    mark("load_mix")
    secs = _sections_s(sections, project, len(M[0]) / M[1]) if (sections or project) else None
    if secs and len(secs) == 1 and secs[0]["name"] == "whole song":
        secs = None
    res = reference_compare(R, M, ref_sections=ref_sections, sections=secs)
    mark("compare")
    if not full:
        res.pop("third_oct_delta_db", None)
    if apply:
        eq = next((m for m in res["moves"] if m["kind"] == "master_eq"), None)
        if eq:
            ops = [{"op": "eq", "track": 0, "hz": b["hz"], "gain_db": b["gain_db"], "q": b.get("q"), "type": b["type"],
                    "why": b["say"]} for b in eq["bands"]]
            res["applied"] = apply_ops(ops, label="reference match")
        else:
            res["applied"] = {"done": [], "note": "no master EQ move big enough to apply"}
        res["next"] = ("render or fl_listen again, then " + C.tool_name(H, "fl_reference_match") + " / " +
                       C.tool_name(H, "fl_verify_intent") + " to check")
    res["steps_s"] = steps
    return res


def fl_reference_match(reference, mix=None, sections=None, project=None, apply=False, full=False,
                       live_seconds=None, background=None):
    kw = dict(reference=reference, mix=mix, sections=sections, project=project, apply=apply, full=full,
              live_seconds=live_seconds)
    est = float(live_seconds) + 6 if live_seconds and mix is None else 10
    return C.auto_job(H, "refmatch", est, reference_match, kw, background, fl=(mix is None or bool(apply)))


# ============================================================================= applying moves (one batch, undoable)
PEQ = "Fruity Parametric EQ 2"


def _note_ms(note, bpm):
    m = re.fullmatch(r"\s*1/(\d+)\s*([dDtT]?)\s*", str(note))
    if not m:
        raise ValueError(f"note value like '1/8', '1/8D', '1/16T': {note}")
    ms = 60000.0 / float(bpm) * 4.0 / int(m.group(1))
    return ms * (1.5 if m.group(2).lower() == "d" else (2 / 3 if m.group(2).lower() == "t" else 1.0))


def _send_level(db):
    """Send knob value (0..1, 0.8 = 0 dB) for a level in dB: the server's fader curve when it has one."""
    try:
        return max(0.0, min(1.0, float(H["_fader"](float(db)))))
    except Exception:                                             # no measured curve: FL's documented-ish law
        return max(0.0, min(1.0, 0.8 * 10 ** (float(db) / 40.0)))


def _find_slot(st, track, plugin):
    mp = plugin_map(plugin) or {"plugin": plugin, "aliases": []}
    for m in st.get("mixer", []):
        if m["index"] == track:
            for fx in m.get("effects", []):
                if _plugin_name_ok(mp, fx.get("name")):
                    return fx["slot"], {fx["slot"] for fx in m.get("effects", [])}
            return None, {fx["slot"] for fx in m.get("effects", [])}
    return None, None


def _add_effects(adds):
    # PC-VERIFY: '<Plugin>.fst' default presets exist under Plugin database for each stock effect; one hot reload
    """Put stock effects on mixer slots in ONE project edit (the open project is saved, edited and reloaded in
    place; the server keeps a backup version). adds: [(track, slot, plugin)]."""
    path, _ = H["_open_project_guess"]()
    if not path and H.get("_open_project_identify"):              # same-title files: a save through FL proves which
        try:
            path = H["_open_project_identify"]()[0]
        except RuntimeError as e:
            raise RuntimeError(f"can't tell which project is open: {e}") from e
    if not path:
        raise RuntimeError("can't tell which project is open: save it inside FLH_ROOTS first")
    pres = []
    for t, s, plug in adds:
        want = (plug + ".fst").lower()
        pre = next((p for p in H["fl_find_presets"](plug, limit=20)["presets"] if p.lower().endswith(want)), None)
        if not pre:
            raise RuntimeError(f"no default preset file for {plug} on this PC (Plugin database)")
        pres.append((t, s, pre))
    def run():
        for t, s, pre in pres:
            H["flp_load_plugin"](path, path, pre, track=t, slot=s)
        return {"output": path}
    _fl_gone_check("before adding the effects")
    _wait_fl_ready(path, what="loading the project before the effects are added")
    H["_with_open_project"](path, path, run)
    tracks = sorted({t for t, _, _ in adds})
    try:
        return _wait_fl_ready(path, lambda st: all(_has_effect(st, t, p) for t, _, p in adds),
                              what="loading the project with the added effects", tracks=tracks)
    except TimeoutError as e:
        raise RuntimeError(f"the added effects aren't in FL yet: {e}. They ARE in the project file (saved, with a "
                           "backup version); once FL shows the project's name again, run the same call again - it "
                           "finds them and doesn't add them twice") from None


# ---- waiting for FL around a project edit. PC 2026-10-23 (a heavy orchestral song): the old loop asked FL for its
# FULL state every 0.3 s for 45 s while FL was still loading the reloaded project (each call waits its whole timeout),
# gave up after 149 s; the retry's first call timed out and FL then wasn't running any more. Now: the wait follows
# the project's measured load cost, no bridge call goes out while FL's title says 'Opening: ...', polls back off and
# read only the tracks concerned (mixer_info), and a job stops at once when FL stops running.
FL_WAIT_MIN_S = 45.0              # fast-PC seconds to wait for FL after a reload, at least ...
FL_WAIT_LOAD_X = 2.5              # ... or this many times the project's measured load (both scaled to this PC)
FL_POLL_S = (0.5, 1.0, 2.0, 4.0)  # back-off between polls
MOVE_TOL_DB = 1.5                 # a fader move landing further than this from its target is undone
MASTER_FLOOR_DB = -12.0           # a mix move never takes the master fader below this


class FLGone(RuntimeError):
    """FL stopped running during a job: nothing more is sent to it."""


def _fl_gone_check(what):
    """FLGone when FL's process is gone (checked by the process list; a bridge that still answers counts as alive).
    Off Windows there is no FL process to look at: nothing to check."""
    if not H.get("IS_WINDOWS"):
        return
    try:
        alive = H["fl_running"]()
    except Exception:                                             # noqa: BLE001 - can't tell: carry on
        return
    if not alive and not _fl_loading():
        try:
            _bridge("ping", timeout=2.0)
            alive = True
        except Exception:                                         # noqa: BLE001
            alive = False
    if not alive:
        raise FLGone(f"FL stopped running (closed or crashed) {what}; nothing more was done. Start FL and reopen the "
                     "song: the project file holds every change made so far (fl_mix_engineer revert_last=true undoes "
                     "the knob/fader moves once it's open)")


def _wait_budget(project):
    cost = None
    try:
        cost = H["_load_cost_guess"](project)[0] if project and "_load_cost_guess" in H else None
    except Exception:
        cost = None
    base = max(FL_WAIT_MIN_S, FL_WAIT_LOAD_X * float(cost or 0.0))
    try:
        return float(H["_scaled"](base)), cost
    except Exception:
        return base, cost


def _fl_loading():
    f = H.get("_fl_loading_title")
    try:
        return f() if f else None
    except Exception:
        return None


def _light_state(tracks):
    """{"mixer": [{index, name, effects: [{slot, name}]}]} for these mixer tracks from ONE mixer_info call (a few
    tracks: small on any project size; fl_state lists every channel and times out on big projects). None when the
    bridge script is older than v6 (no slot numbers)."""
    r = _bridge("mixer_info", tracks=",".join(str(int(t)) for t in tracks), timeout=10.0)
    rows = r.get("tracks") if isinstance(r, dict) else None
    if not rows or any("slots" not in x for x in rows):
        return None
    return {"mixer": [{"index": int(x["index"]), "name": x.get("name"),
                       "effects": [{"slot": int(s), "name": n} for s, n in zip(x["slots"], x.get("effects") or [])]}
                      for x in rows], "light": True}


def _state_for(tracks):
    """The mixer state apply_ops needs: light (only `tracks`) when the bridge can, else fl_state."""
    if tracks:
        try:
            st = _light_state(tracks)
            if st is not None:
                return st
        except Exception:                                         # noqa: BLE001 - the full state then
            pass
    return H["fl_state"]()


def _wait_fl_ready(project, pred=None, what="loading", tracks=None):
    """Wait until FL has loaded (title no longer 'Opening: ...') and pred(state) holds; -> the state (None without
    pred: then not a single bridge call). Budget: the project's load cost (FL_WAIT_LOAD_X x, at least FL_WAIT_MIN_S), scaled to this
    PC. No bridge call while FL shows it is loading; polls back off to FL_POLL_S[-1]. FLGone when FL stops running,
    TimeoutError after the budget. PC-VERIFY: fl_mix_engineer apply=true on a song that loads > 60 s adds its
    effects without a timeout and FL stays open."""
    budget, cost = _wait_budget(project)
    t0 = time.time(); k = 0; last = "not asked yet"
    while True:
        _fl_gone_check(f"while {what}")
        if _fl_loading():
            last = "FL's title still says it is loading"
        elif pred is None:
            return None
        else:
            try:
                st = _state_for(tracks)
                if pred(st):
                    return st
                last = "FL answers, the effects aren't on the tracks yet"
            except Exception as e:                                # noqa: BLE001
                last = f"{type(e).__name__}: {str(e)[:100]}"
        if time.time() - t0 >= budget:
            break
        _progress(detail=f"waiting for FL ({what}): {last}")
        time.sleep(FL_POLL_S[min(k, len(FL_POLL_S) - 1)]); k += 1
    raise TimeoutError(f"FL wasn't ready within {budget:.0f} s ({what}; this project's measured load: "
                       f"{f'{cost:.0f} s' if cost else 'not measured yet'}; last: {last})")


def _mixer_info(track):
    """One mixer track as the bridge's mixer_info sees it (effect names, name, sends) - also for bus tracks no
    channel feeds (fl_state lists only those). None when the bridge can't say."""
    try:
        r = _bridge("mixer_info", tracks=str(int(track)))
        rows = r.get("tracks") if isinstance(r, dict) else None
        return next((x for x in rows or [] if int(x.get("index", -1)) == int(track)), None)
    except Exception:
        return None


def _has_effect(st, track, plugin):
    """Is `plugin` on mixer `track`? fl_state for channel-fed tracks, mixer_info for buses fl_state doesn't list."""
    slot, used = _find_slot(st, track, plugin)
    if slot is not None:
        return True
    if used is not None:
        return False
    mi = _mixer_info(track)
    mp = plugin_map(plugin) or {"plugin": plugin, "aliases": []}
    return bool(mi) and any(_plugin_name_ok(mp, n) for n in mi.get("effects") or [])


def _free_band(track, slot, hz, kind, taken, fresh=False):
    # PC-VERIFY: an untouched PEQ2 reads gain ~0 dB, its default band types and default frequencies
    """Which PEQ2 band (1..7) to use: shelves/high-pass at the edges, bells at the nearest unused default band.
    A band counts as the user's (never reused) when ANY of its settings differs from PEQ2's defaults: gain not ~0,
    type not the default (a high-pass at 0 dB gain is in use), or frequency moved. When the bands can't be read, only
    an EQ this batch just added (fresh) is used."""
    mp = plugin_map(PEQ) or {}
    centers = mp.get("band_defaults_hz") or [63, 136, 294, 632, 1363, 2936, 6324]
    dtypes = mp.get("band_default_type") or []
    busy = set(taken)
    try:
        cur = plugin_get_named(track, slot, PEQ)["controls"]
        for b in range(1, 8):
            g = cur.get(f"band{b}_gain_db", {}).get("value")
            ty = cur.get(f"band{b}_type", {}).get("value")
            fq = cur.get(f"band{b}_freq_hz", {}).get("value")
            if g is None and ty is None and fq is None:
                if not fresh:
                    busy.add(b)                                   # unreadable: assume it's in use
                continue
            if isinstance(g, (int, float)) and abs(g) > 0.05:
                busy.add(b)
            if isinstance(ty, str) and ty.strip() and ty.strip().lower() != "off":
                t0 = dtypes[b - 1] if b - 1 < len(dtypes) else None
                if ty.strip().lower() in ("low pass", "high pass", "band pass", "notch") or \
                        (t0 and ty.strip().lower() != t0.lower() and not fresh):
                    busy.add(b)
            if isinstance(fq, (int, float)) and fq > 0 and abs(math.log(fq / centers[b - 1])) > 0.03 and not fresh:
                busy.add(b)
    except Exception:
        if not fresh:
            return None
    if kind in ("High pass", "Low shelf"):
        order = [1, 2]
    elif kind in ("High shelf", "Low pass"):
        order = [7, 6]
    else:
        order = sorted(range(2, 7), key=lambda b: abs(math.log(hz / centers[b - 1])))
    return next((b for b in order if b not in busy), None)


def apply_ops(ops, label="mix engineer", project=None):
    """Execute moves in one batch: fader/pan via the bridge, EQ/compressor settings via plugin maps (missing stock
    effects are added in one project edit). Records undo data in the state file and the song's journal.
    ops: [{op: fader, track, db} | {op: pan, track, pan} | {op: eq, track, hz, gain_db, q, type} |
          {op: plugin, track, plugin, controls, slot?, add?} (slot given: that slot, also on a bus track no channel
          feeds; add=true puts the plugin there first) | {op: send, track, to, db} (send level, routes if needed) |
          {op: name, track, name, color?}] -> {done, skipped, undo_count}"""
    steps, mark = _steps()
    _fl_gone_check("before the moves")
    path0 = project or H["_open_project_guess"]()[0]
    if _fl_loading():                                             # never ask a loading FL (a retry right after a
        _wait_fl_ready(path0, what="loading the project")         # timed-out run): wait for it, calmly
    st = _state_for(sorted({int(o["track"]) for o in ops if o.get("track") is not None}))
    plan, adds, planned_slots = [], [], {}
    for o in ops:
        if o["op"] == "plugin" and o.get("slot") is not None:     # an explicit slot (a bus the room made)
            if o.get("add") and (int(o["track"]), int(o["slot"]), o["plugin"]) not in adds:
                adds.append((int(o["track"]), int(o["slot"]), o["plugin"]))
            plan.append((o, "do", None)); continue
        if o["op"] in ("eq", "plugin"):
            plug = PEQ if o["op"] == "eq" else o["plugin"]
            slot, used = _find_slot(st, int(o["track"]), plug)
            key = (int(o["track"]), plug)
            if slot is None and key not in planned_slots:
                if used is None:
                    plan.append((o, "skip", f"mixer track {o['track']} isn't visible to the bridge (no channel feeds it)"))
                    continue
                used = set(used) | {s for (t, _), s in planned_slots.items() if t == int(o["track"])}
                free = next((i for i in range(10) if i not in used), None)
                if free is None:
                    plan.append((o, "skip", f"no free effect slot on mixer track {o['track']}")); continue
                planned_slots[key] = free; adds.append((int(o["track"]), free, plug))
        plan.append((o, "do", None))
    mark("plan")
    if adds:
        st = _add_effects(adds); mark("add_effects")
    done, skipped, undo = [], [], []
    taken = {}
    for i_op, (o, what, why) in enumerate(plan):
        if what == "skip":
            skipped.append({"op": o, "why": why}); continue
        t = int(o["track"])
        try:
            if o["op"] == "fader":
                # PC 2026-10-23: a master move of -1.8 dB left the master fader at its bottom (a silent export) - the
                # old bridge searched over FL 2026's lagging read-back. Now: FL's exact curve, one set, a later
                # read-back; a move landing more than MOVE_TOL_DB off is undone, the master never goes below
                # MASTER_FLOOR_DB, and an automated fader is never touched.
                pos, before, _r = H["_fader_read"](t)
                if pos is None or before is None or pos <= 0 or before <= -90:
                    skipped.append({"op": o, "why": "the fader's current level can't be read (or is at zero): a "
                                    "relative move would jump; set it by hand"}); continue
                mv = _safe("fader_move_db", o["db"])
                clips = [c for c in (H["_driven_by"](("mixer_volume", t), path0) if "_driven_by" in H else [])
                         if c["placed"]]
                if clips:                                          # FL's automation would override the fader
                    skipped.append({"op": o, "why": H["_automated_msg"](
                        f"mixer track {t}'s volume", clips, f"move it with fl_mixer_set(track={t}, volume_db="
                        f"{round(before + mv, 1)}, if_automated='scale_clip' or 'insert_gain')")}); continue
                want = _safe("fader_db", before + mv)
                floor_note = None
                if t == 0 and want < MASTER_FLOOR_DB:              # a mix move never buries the master
                    lim = min(before, MASTER_FLOOR_DB)
                    if want < lim:
                        floor_note = f"the master stops at {lim:g} dB (asked {want:.1f}): a mix move never goes lower"
                        want = lim
                    if abs(want - before) < 0.05:
                        skipped.append({"op": o, "why": floor_note or "the master is at its floor"}); continue
                f = H["_fader_apply"](t, want)
                got = f.get("db")
                if got is None or abs(got - want) > MOVE_TOL_DB:   # landed off target: undone, said so
                    try:
                        H["_fader_restore"](t, pos)
                        back = "put back where it was"
                    except Exception as ex:                        # noqa: BLE001
                        back = f"COULDN'T put it back ({type(ex).__name__}: {ex}): set it to {before:g} dB by hand"
                    skipped.append({"op": o, "why": f"the fader landed at {got if got is not None else 'nothing'} "
                                    f"dB, not {want:.2f} ({f.get('why') or 'off target'}): {back}"}); continue
                undo.append({"kind": "fader", "track": t, "before": before, "before_v": pos})
                d_ = {"op": "fader", "track": t, "from_db": round(before, 2), "to_db": got, "why": o.get("why")}
                if not f.get("ok"):                                # within MOVE_TOL_DB but not exact: say so
                    d_["off_by_db"] = round(got - want, 2); d_["note"] = f.get("why")
                if floor_note:
                    d_["note"] = floor_note
                done.append(d_)
            elif o["op"] == "pan":
                before = _bridge("mixer_set", track=t).get("pan", 0.0)
                _bridge("mixer_set", track=t, pan=_safe("pan", o["pan"]))
                undo.append({"kind": "pan", "track": t, "before": before})
                done.append({"op": "pan", "track": t, "from": before, "to": o["pan"], "why": o.get("why")})
            elif o["op"] == "send":
                # PC-VERIFY: setRouteTo keeps the track's route to the master (a send, not a re-route); the send knob
                # follows the fader curve (0.8 = 0 dB)
                dst = int(o["to"])
                cur = _bridge("mixer_set", track=t)
                was = next((x for x in cur.get("sends") or [] if int(x.get("to", -1)) == dst), None)
                before = (was.get("level") if was else None)
                lv = _send_level(_safe("fader_db", o["db"]))
                r = _bridge("mixer_set", track=t, send_levels=f"{dst}:{lv:.6f}")
                got = next((x for x in r.get("sends") or [] if int(x.get("to", -1)) == dst), None)
                undo.append({"kind": "send", "track": t, "to": dst, "before": before, "routed": was is not None})
                ok = got is not None and (got.get("level") is None or abs(float(got["level"]) - lv) < 0.01)
                done.append({"op": "send", "track": t, "to": dst, "db": round(float(o["db"]), 1), "ok": ok,
                             "why": o.get("why")})
            elif o["op"] == "name":
                cur = _bridge("mixer_set", track=t)
                kw = {"name": str(o["name"])}
                if o.get("color"):
                    kw["color"] = o["color"]
                _bridge("mixer_set", track=t, **kw)
                undo.append({"kind": "name", "track": t, "before": cur.get("name") or ""})
                done.append({"op": "name", "track": t, "name": o["name"], "why": o.get("why")})
            else:
                plug = PEQ if o["op"] == "eq" else o["plugin"]
                slot = int(o["slot"]) if o.get("slot") is not None else _find_slot(st, t, plug)[0]
                if slot is None:
                    skipped.append({"op": o, "why": f"{plug} not found on track {t} after adding"}); continue
                if o["op"] == "eq":
                    kind = o.get("type") or "Peaking"
                    fresh = (t, slot, PEQ) in {(a, b_, c) for a, b_, c in adds}
                    b = _free_band(t, slot, float(o["hz"]), kind, taken.setdefault((t, slot), set()), fresh=fresh)
                    if b is None:
                        skipped.append({"op": o, "why": "no unused EQ band left on that Parametric EQ 2 (bands you "
                                        "set are never overwritten)"}); continue
                    taken[(t, slot)].add(b)
                    vals = {f"band{b}_type": kind, f"band{b}_freq_hz": _safe("hz", o["hz"])}
                    if kind not in ("High pass", "Low pass"):
                        vals[f"band{b}_gain_db"] = _safe("eq_gain_db", o["gain_db"])
                    if o.get("q") and kind == "Peaking":
                        vals[f"band{b}_width"] = f"{_safe('q', o['q'])} Q"
                    r = plugin_set_named(t, slot, PEQ, vals)
                else:
                    r = plugin_set_named(t, slot, plug, o["controls"])
                undo += [dict(u, kind="param") for u in r["undo"]]
                done.append({"op": o["op"], "track": t, "slot": slot, "plugin": plug, "ok": r["ok"],
                             "set": [f"{s['control']}={s['text']}" + ("" if s["ok"] else f" ({s['note']})"
                                     if s.get("note") else " (check)") for s in r["set"]],
                             "skipped": r["skipped"] or None, "why": o.get("why")})
        except Exception as e:
            skipped.append({"op": o, "why": f"{type(e).__name__}: {e}"})
            if isinstance(e, (TimeoutError, FLGone)):              # FL gone or stuck: stop sending it calls
                try:
                    _fl_gone_check("during the moves")
                except FLGone as g:
                    rest = plan[i_op + 1:]
                    skipped += [{"op": x, "why": "not tried: FL stopped running"} for x, w, _ in rest if w == "do"]
                    fl_gone = str(g)
                    break
    else:
        fl_gone = None
    mark("apply")
    prev = (H["_state"]().get("mix_engineer") or {}).get("applied", [])
    path = project or H["_open_project_guess"]()[0]
    H["_state"]({"mix_engineer": {"project": path, "applied": prev + undo, "label": label}})
    if path:
        try:
            S = sys.modules.get("flh_store")
            if S and S.H:
                S.journal_update(path, add={"mix_log": {"when": time.strftime("%Y-%m-%d %H:%M"), "by": label,
                                                         "done": [d.get("why") or d["op"] for d in done][:20]}})
        except Exception:
            pass
    out = {"done": done, "skipped": skipped, "undo_count": len(undo), "added_effects": [f"{p} on track {t} slot {s}"
           for t, s, p in adds], "steps_s": steps}
    if fl_gone:
        out["fl_gone"] = out["STOPPED"] = fl_gone
    return out


def revert():
    """Undo everything the mix engineer applied since the last revert (faders, pans, plugin knobs; added effects
    stay in their slots but are put back to how they were set before the moves)."""
    me = H["_state"]().get("mix_engineer") or {}
    n = 0; failed = []
    for u in reversed(me.get("applied", [])):
        try:
            if u["kind"] == "fader":
                if u.get("before_v") is not None:                 # the exact position from before the move
                    H["_fader_restore"](u["track"], u["before_v"])
                else:
                    H["_fader_restore"](u["track"], H["_fader"](float(u["before"])))
            elif u["kind"] == "pan":
                _bridge("mixer_set", track=u["track"], pan=float(u["before"]))
            elif u["kind"] == "send":
                if not u.get("routed"):                            # the send didn't exist: take the route away
                    _bridge("mixer_set", track=u["track"], unroute=str(int(u["to"])))
                elif u.get("before") is not None:
                    _bridge("mixer_set", track=u["track"], send_levels=f"{int(u['to'])}:{float(u['before']):.6f}")
            elif u["kind"] == "name":
                _bridge("mixer_set", track=u["track"], name=str(u["before"]))
            else:
                _bridge("plugin_set", index=u["track"], slot=u["slot"], param=u["param"], value=float(u["before"]))
            n += 1
        except Exception as e:                                    # kept, so a later revert can retry it
            failed.append(dict(u, error=f"{type(e).__name__}: {str(e)[:80]}"))
    me["applied"] = [{k: v for k, v in u.items() if k != "error"} for u in reversed(failed)]
    H["_state"]({"mix_engineer": me})
    r = {"reverted": n}
    if failed:
        r["failed"] = [{"kind": u.get("kind"), "track": u.get("track"), "error": u["error"]} for u in failed]
    return r


# ============================================================================= level changes where automation holds the fader
def flp_scale_automation(project, output=None, clips=None, db=0.0):
    """Move every point of mixer-volume automation clips by `db` (FL's fader curve: 0 dB = 0.8): the level change
    for a track whose fader follows an automation clip (a fader set is overridden by it). clips: channel indexes or
    names of automation clips (default: none - name them). Only clips linked to a mixer track's volume; points
    that would pass the fader's top (+5.6 dB) or bottom are clipped there (counted). The file only; registered as a
    project writer (the open FL project is saved / reloaded around it)."""
    output = output or project
    H["writable"](output)
    if not clips:
        raise ValueError("clips: the automation clip channels (index or name) to scale")
    db = float(db)
    if not -48.0 <= db <= 24.0:
        raise ValueError("db must be between -48 and +24")
    d = H["_load"](project); ev = H["_events"](d)
    names = {c: nm for c, (kind, nm) in H["_clip_names"](d, ev).items() if kind == "automation"}
    want = []
    for c in clips if isinstance(clips, list) else [clips]:
        if isinstance(c, int) or (isinstance(c, str) and c.strip().isdigit()):
            ch = int(c)
        else:
            ch = next((i for i, nm in names.items() if _norm_name(nm) == _norm_name(c)), None)
            if ch is None:
                raise ValueError(f"no automation clip named '{c}' ({', '.join(repr(n) for n in list(names.values())[:8])})")
        if ch not in names:
            raise ValueError(f"channel {ch} isn't an automation clip")
        want.append(ch)
    links = {}
    for e in ev:
        if e["id"] == H["EV_REMOTE_LINK"] and e["len"] >= 12:
            p = H["_payload"](d, e)
            links[struct.unpack("<H", p[2:4])[0]] = struct.unpack("<HH", p[8:12])
    vol = H["MIX_LINK"]["volume"]
    auto_ev = H["_automation_events"](d, ev)
    edits, rep, clipped = [], [], 0
    for ch in want:
        pid, dest = links.get(ch, (None, None))
        if pid != vol or dest is None or dest < 0x7000 or (dest - 0x7000) & 63:
            raise ValueError(f"'{names[ch]}' (channel {ch}) doesn't automate a mixer track's volume; only volume clips "
                             "are scaled in dB")
        k = auto_ev.get(ch)
        if k is None:
            raise ValueError(f"'{names[ch]}' has no automation points")
        p = bytearray(H["_payload"](d, ev[k]))
        n = struct.unpack("<I", p[17:21])[0]
        if len(p) < 21 + 24 * n:
            raise ValueError(f"'{names[ch]}': automation points in an unknown layout; not changed")
        lo = hi = None
        for i in range(n):
            o = 21 + 24 * i + 8                                   # the value (double) of point i
            v = struct.unpack("<d", p[o:o + 8])[0]
            if not math.isfinite(v):
                continue
            nd = H["_fader_db"](max(0.0, min(1.0, v))) + db
            nv = H["_fader"](nd) if v > 0 else 0.0                # a point at -inf stays silent
            if nd > H["FADER_TOP_DB"] + 1e-9 or (v > 0 and nv <= 0.0):
                clipped += 1
            p[o:o + 8] = struct.pack("<d", max(0.0, min(1.0, nv)))
            lo = nv if lo is None else min(lo, nv); hi = nv if hi is None else max(hi, nv)
        edits.append((k, k + 1, H["_data_event"](H["EV_AUTOMATION"], bytes(p))))
        rep.append({"channel": ch, "name": names[ch], "points": n, "track": (dest - 0x7000) >> 6,
                    "now_db": [round(H["_fader_db"](lo), 1), round(H["_fader_db"](hi), 1)] if lo is not None else None})
    H["_save"](H["_splice"](d, ev, sorted(edits)), output)
    out = {"output": output, "scaled": rep, "db": db}
    if clipped:
        out["clipped_points"] = clipped
        out["note"] = f"{clipped} point(s) hit the fader's end and were clipped there"
    return out


def insert_gain(track, delta_db):
    """Change mixer `track`'s level by `delta_db` on an insert gain stage - Fruity Parametric EQ 2's 'Main level'
    (added to a free slot when the track has none) - for a track whose fader an automation clip drives. Set live
    through the plugin map and checked by FL's display. -> {plugin, slot, main_level_db: [before, after], added}"""
    t = int(track); delta = float(delta_db)
    st = _state_for([t])
    slot, used = _find_slot(st, t, PEQ)
    added = False
    if slot is None:
        free = next((i for i in range(10) if i not in (used or set())), None)
        if free is None:
            raise RuntimeError(f"mixer track {t} has no free effect slot for a Parametric EQ 2 gain stage")
        st = _add_effects([(t, free, PEQ)]); added = True
        slot = _find_slot(st, t, PEQ)[0]
        if slot is None:
            raise RuntimeError(f"Parametric EQ 2 isn't on mixer track {t} after adding it")
    cur = plugin_get_named(t, slot, PEQ)["controls"].get("main_level_db", {}).get("value")
    if not isinstance(cur, (int, float)):
        raise RuntimeError(f"can't read Parametric EQ 2's 'Main level' on track {t} slot {slot}")
    e = (plugin_map(PEQ) or {}).get("controls", {}).get("main_level_db", {})
    new = float(cur) + delta
    if not float(e.get("min", -18)) <= new <= float(e.get("max", 18)):
        raise RuntimeError(f"Parametric EQ 2's Main level would need {new:+.1f} dB (its range is "
                           f"{e.get('min', -18)}..{e.get('max', 18)} dB): scale the automation clip instead")
    r = plugin_set_named(t, slot, PEQ, {"main_level_db": new})
    s = (r.get("set") or [{}])[0]
    if not r.get("ok"):
        raise RuntimeError(f"Parametric EQ 2's Main level on track {t} didn't take {new:+.1f} dB (FL shows "
                           f"'{s.get('text')}'): {s.get('note') or r.get('skipped')}")
    me = H["_state"]().get("mix_engineer") or {}
    H["_state"]({"mix_engineer": dict(me, applied=(me.get("applied") or []) + [dict(u, kind="param")
                                                                             for u in r.get("undo") or []])})
    return {"plugin": PEQ, "slot": slot, "main_level_db": [round(float(cur), 2), round(new, 2)], "text": s.get("text"),
            "added": added, "undo": "fl_mix_engineer(revert_last=true)"}


# ============================================================================= the engineer
def _tracks_from(meas):
    """Normalize measurement inputs to [{name, track, role, lufs, bands_db, crest_db, width_db, pan}]."""
    rows = meas.get("tracks") if isinstance(meas, dict) else meas
    if isinstance(rows, dict):
        rows = [dict(v, name=v.get("name", k)) for k, v in rows.items()]
    out = []
    for r in rows or []:
        if r.get("lufs") is None or r["lufs"] <= -60:
            continue
        r = dict(r)
        r["role"] = r.get("role") or role_of(r.get("name"))
        out.append(r)
    return out


def _bpm(bpm, project, live):
    if bpm:
        return float(bpm), "given"
    if live:
        try:
            t = H["fl_state"]().get("tempo")
            if t:
                return float(t), "FL"
        except Exception:
            pass
    if project:
        try:
            t = H["flp_project_info"](project).get("tempo_bpm")
            if t:
                return float(t), "project"
        except Exception:
            pass
    return 120.0, "assumed (tempo unknown)"


def pick_lead(tr):
    """The lead among normalized tracks (each with imp, role, lufs): the most important, then loudest - melodic parts
    first, the rhythm section only when nothing else plays."""
    tune = [t for t in tr if t["role"] not in RHYTHM_ROLES]
    return max(tune or tr, key=lambda t: (t["imp"], t["lufs"])) if tr else None


def _journal_lead(project, meas):
    """The measured track that plays the song's melody, from the journal (channels.N.role melody / lead / vocal and
    its mixer insert), or None."""
    try:
        S = sys.modules.get("flh_store")
        jr = S.journal_get(project) if S else {}
    except Exception:
        return None
    tr = _tracks_from(meas) if meas else []
    by_track = {t.get("track"): t["name"] for t in tr if t.get("track") is not None}
    for want in (("vocal", "vox"), ("melody", "lead", "topline")):
        for c, v in sorted(((jr or {}).get("channels") or {}).items()):
            v = v if isinstance(v, dict) else {}
            if str(v.get("role") or "").lower() in want:
                ins = v.get("mixer")
                try:
                    if ins is not None and int(ins) in by_track:
                        return by_track[int(ins)]
                except (TypeError, ValueError):
                    pass
    return None


def propose(meas, genre="default", goal=None, bpm=120.0, masking_findings=None, lead=None, max_items=14):
    """The engineer's ranked moves from measurements. Pure: no FL access.
    meas: {"tracks": [{name, track, lufs, bands_db, crest_db?, pan?}], "mix": {lufs, true_peak, bands_db}}.
    -> [{id, priority 1-3, kind, track, name, why, settings, ops (None = suggestion only), tags, matches_goal}]"""
    n = norms(genre)
    tr = _tracks_from(meas)
    mix = (meas.get("mix") if isinstance(meas, dict) else None) or {}
    goal_int = set(intents_from_text(goal)) if goal else set()
    props = []

    def add(pri, kind, t, why, ops=None, settings=None, tags=()):
        props.append({"priority": pri, "kind": kind, "track": t.get("track") if t else 0,
                      "name": t.get("name") if t else "Master", "why": why, "settings": settings, "ops": ops,
                      "tags": list(tags)})
    if not tr:
        return []
    for t in tr:
        t["imp"] = importance(t["role"])
        if lead and _norm_name(lead) == _norm_name(t["name"]):
            t["imp"], t["role"] = 4.0, ("vocal" if t["role"] == "vocal" else "lead")
    # the lead: a named / vocal / lead track first; else the loudest melodic part - never the rhythm section (an 808
    # or a kick is loud by design: PC 2026-10-26 an RnB piano was turned down 6 dB 'below the lead (808)')
    L = pick_lead(tr)
    gaps, tol = n["gaps_db"], float(n.get("gap_tolerance_db", 1.5))
    tp = mix.get("true_peak", mix.get("true_peak_dbfs"))
    # ---- 0. master clipping / headroom first
    if tp is not None and tp > -1.0 and not mix.get("approximate"):   # stems' sum over-reads peaks: no master move
        cut = round(-1.0 - tp - 0.5, 1)
        add(1, "master_headroom", None, f"mix true peak {tp:+.1f} dBTP: {'clipping' if tp >= 0 else 'no headroom'}",
            ops=[{"op": "fader", "track": 0, "db": cut, "why": f"master {cut:+.1f} dB for headroom"}],
            tags=["quieter"])
    # ---- 1. gain staging: lead on top, layers below by genre norms
    overs = []
    for t in tr:
        if t is L or t["imp"] >= L["imp"]:
            continue
        want = L["lufs"] + float(gaps.get(t["role"], gaps.get("support", -5)))
        over = t["lufs"] - want
        if over > tol:
            overs.append((t, over, want))
        elif over < -(tol + 8) and t["role"] in ("bass", "kick", "drums", "keys", "guitar"):
            up = min(4.0, round((-over - tol - 4) * 2) / 2)
            if up >= 1:
                add(3, "gain", t, f"{t['name']} is {-over:.1f} dB under where a {t['role']} usually sits vs "
                    f"{L['name']}: may be buried", ops=[{"op": "fader", "track": t.get("track"), "db": up,
                                                         "why": f"{t['name']} +{up:g} dB (buried)"}], tags=["balance"])
    lead_up = 0.0
    if overs:
        worst = max(o for _, o, _ in overs)
        louder = [t for t, _, _ in overs if t["lufs"] > L["lufs"]]
        if louder and (tp is None or tp < -3.0) and worst >= 2.0:
            lead_up = min(3.0, round(worst / 2 * 2) / 2)
            add(1, "gain", L, f"the lead ({L['name']}) is buried: {', '.join(t['name'] for t in louder[:3])} "
                f"{'is' if len(louder) == 1 else 'are'} louder", ops=[{"op": "fader", "track": L.get("track"),
                "db": lead_up, "why": f"{L['name']} +{lead_up:g} dB (lead on top)"}], tags=["clearer", "balance"])
        for t, over, want in overs:
            cut = min(6.0, round((over - lead_up) * 2) / 2)
            if cut < 0.5:
                continue
            add(1 if over > 3 else 2, "gain", t, f"{t['name']} is {t['lufs'] - L['lufs']:+.1f} LU vs the lead "
                f"({L['name']}); a {t['role']} sits about {-float(gaps.get(t['role'], gaps.get('support', -5))):g} dB "
                f"under it in {genre}", ops=[{"op": "fader", "track": t.get("track"), "db": -cut,
                                              "why": f"{t['name']} -{cut:g} dB (below the lead)"}],
                tags=["clearer", "balance"])
    # ---- 2. high-pass non-bass tracks that carry lows
    for t in tr:
        hp = n["hp_hz"].get(t["role"], n["hp_hz"].get("support"))
        b = t.get("bands_db") or {}
        if not hp or not b:
            continue
        lows = _db_sum(b.get("sub"), b.get("bass") if hp >= 100 else None)
        if lows is not None and lows > -14:
            add(1 if (b.get("sub") or -99) > -12 else 2, "highpass", t,
                f"{t['name']} carries low end ({lows:+.0f} dB of its energy below ~250 Hz) that a {t['role']} doesn't "
                f"need: high-pass at {hp} Hz", settings={"hz": hp, "slope": "12-24 dB/oct"},
                ops=[{"op": "eq", "track": t.get("track"), "hz": hp, "type": "High pass", "gain_db": 0,
                      "why": f"{t['name']} high-pass {hp} Hz"}], tags=["cleaner", "less_muddy", "tighter"])
    # ---- 3. mud
    mud = n["mud"]
    for t in tr:
        b = t.get("bands_db") or {}
        lm = b.get("low_mid")
        if lm is None or t["role"] in ("bass", "kick") or t is L and lm < mud["share_db"] + 2:
            continue
        if lm > mud["share_db"]:
            g = -min(float(mud["max_cut_db"]), round((1.5 + (lm - mud["share_db"]) / 2) * 2) / 2)
            add(2, "mud_cut", t, f"{t['name']} is heavy at 250-800 Hz ({lm:+.1f} dB share): {g:+.1f} dB at "
                f"{mud['hz']} Hz (Q {mud['q']})", settings={"hz": mud["hz"], "q": mud["q"], "gain_db": g},
                ops=[{"op": "eq", "track": t.get("track"), "hz": mud["hz"], "q": mud["q"], "gain_db": g,
                      "type": "Peaking", "why": f"{t['name']} mud cut {g:+.1f} dB @ {mud['hz']} Hz"}],
                tags=["less_muddy", "cleaner", "clearer"])
    # ---- 4. masking (stems): dip the masker where it covers the lead
    lead_masked = False
    for f in masking_findings or []:
        fx = next((x for x in f.get("fixes", []) if x["kind"] == "eq_dip"), None)
        if not fx:
            continue
        is_lead = f["target"] == L["name"]
        lead_masked |= is_lead
        tm = next((t for t in tr if t["name"] == f["masker"]), {"name": f["masker"], "track": f.get("masker_track")})
        alts = [x["say"] for x in f.get("fixes", []) if x["kind"] != "eq_dip"]
        add(1 if is_lead else 2, "unmask", tm, f"{f['say']}: {fx['say']}" + (f" ({'; '.join(alts)})" if alts else ""),
            settings={"hz": fx["hz"], "q": fx["q"], "gain_db": fx["gain_db"]},
            ops=[{"op": "eq", "track": tm.get("track"), "hz": fx["hz"], "q": fx["q"], "gain_db": fx["gain_db"],
                  "type": "Peaking", "why": fx["say"]}], tags=["clearer", "cleaner"])
    # ---- 5. presence for the lead / de-harsh
    pr, hs = n["presence"], n["harsh"]
    lb = L.get("bands_db") or {}
    if lb.get("presence") is not None and lb["presence"] < pr["min_share_db"] and lb["presence"] < hs["share_db"] - 3:
        add(2 if not lead_masked else 1, "presence", L, f"{L['name']} has little 2.5-6 kHz ({lb['presence']:+.1f} dB "
            f"share): +{pr['boost_db']:g} dB at {_fmt_hz(pr['hz'])} (Q {pr['q']}) helps it read",
            settings={"hz": pr["hz"], "q": pr["q"], "gain_db": pr["boost_db"]},
            ops=[{"op": "eq", "track": L.get("track"), "hz": pr["hz"], "q": pr["q"], "gain_db": float(pr["boost_db"]),
                  "type": "Peaking", "why": f"{L['name']} presence +{pr['boost_db']:g} dB"}], tags=["clearer", "brighter"])
    for t in tr:
        p = (t.get("bands_db") or {}).get("presence")
        if p is not None and p > hs["share_db"]:
            g = -float(hs["cut_db"]) - (1.0 if p > hs["share_db"] + 3 else 0.0)
            add(2, "de_harsh", t, f"{t['name']} is strong at 2.5-6 kHz ({p:+.1f} dB share): {g:+.1f} dB at "
                f"{_fmt_hz(hs['hz'])} (Q {hs['q']}); if it's only on some notes, a dynamic cut (Maximus band) is gentler",
                settings={"hz": hs["hz"], "q": hs["q"], "gain_db": g},
                ops=[{"op": "eq", "track": t.get("track"), "hz": hs["hz"], "q": hs["q"], "gain_db": g, "type": "Peaking",
                      "why": f"{t['name']} de-harsh {g:+.1f} dB @ {_fmt_hz(hs['hz'])}"}], tags=["less_harsh", "darker"])
    # ---- 6. compression for consistency (from crest + genre)
    for t in tr:
        c = n["comp"].get(t["role"]); crest = t.get("crest_db")
        if not c or crest is None:
            continue
        ex = crest - float(c["crest_target_db"])
        if ex <= 3:
            continue
        ratio = float(c["ratio"]) + (1.0 if ex > 8 else 0.0)
        rel = int(round(_note_ms(c["release_note"], bpm)))
        thr = round((t["lufs"] + 4) * 2) / 2                      # ~4 dB above the body: only peaks are caught
        gr = (crest - 4) * (1 - 1 / ratio) * 0.5
        add(2 if t is L else 3, "compress", t,
            f"{t['name']} swings a lot (crest {crest:.0f} dB vs ~{c['crest_target_db']} dB typical): {ratio:g}:1, "
            f"{c['attack_ms']} ms attack, {rel} ms release ({c['release_note']} at {bpm:g} BPM), threshold "
            f"~{thr:g} dB, ~{gr:.0f} dB gain reduction on peaks", settings={"ratio": ratio, "attack_ms": c["attack_ms"],
            "release_ms": rel, "threshold_db": thr, "gain_db": round(gr / 2, 1), "note": "threshold estimated from "
            "loudness: set it so peaks show the stated reduction"},
            ops=[{"op": "plugin", "track": t.get("track"), "plugin": "Fruity Compressor",
                  "controls": {"threshold_db": thr, "ratio": ratio, "attack_ms": c["attack_ms"], "release_ms": rel,
                               "gain_db": round(gr / 2, 1)}, "why": f"{t['name']} compressor {ratio:g}:1"}],
            tags=["more_compressed", "punchier" if c["attack_ms"] >= 15 else "more_compressed"])
    # ---- 7. space: reverb / delay sized to tempo (suggestion: sends can't be levelled through the bridge)
    rv = n["reverb"]
    pre = int(round(_note_ms(rv["predelay_note"], bpm))); dec = round(float(rv["decay_beats"]) * 60.0 / bpm, 2)
    dly = int(round(_note_ms(n["delay_note"], bpm)))
    add(3, "space", L, f"send {L['name']} to a reverb bus: Fruity Reeverb 2 100% wet, pre-delay {pre} ms "
        f"({rv['predelay_note']}), decay {dec} s ({rv['decay_beats']} beats), low cut {rv['low_cut_hz']} Hz, high cut "
        f"{_fmt_hz(rv['high_cut_hz'])}; and/or Fruity Delay 3 at {n['delay_note']} ({dly} ms), low feedback",
        settings={"reverb": {"predelay_ms": pre, "decay_s": dec, "low_cut_hz": rv["low_cut_hz"],
                             "high_cut_hz": rv["high_cut_hz"], "wet_pct": 100, "dry_pct": 0},
                  "delay": {"time_ms": dly, "note": n["delay_note"], "feedback_pct": 25},
                  "how": "route the track to a free mixer track (fl_mixer_set route_to) and put the effects there; "
                         "set the send level by ear"}, tags=["wider"])
    # ---- 8. stereo placement
    pannable = [t for t in tr if t["role"] in n["pan"] and n["pan"][t["role"]] > 0 and t is not L]
    if len(pannable) >= 2 and all(abs(float(t.get("pan") or 0)) < 0.05 for t in pannable if "pan" in t) and \
            any("pan" in t for t in pannable):
        ops = []
        for i, t in enumerate(sorted(pannable, key=lambda t: -t["lufs"])):
            amt = float(n["pan"][t["role"]]) * (1 if i % 2 == 0 else -1)
            ops.append({"op": "pan", "track": t.get("track"), "pan": round(amt, 2), "why": f"{t['name']} pan {amt:+.2f}"})
        add(3, "stereo", None, "every support part sits in the center: spread them (lead, bass, kick stay centered)",
            ops=ops, settings={"pans": {o["why"]: o["pan"] for o in ops}}, tags=["wider", "cleaner"])
    for t in tr:
        if t["role"] in ("bass", "kick", "vocal", "lead") and abs(float(t.get("pan") or 0)) > 0.15:
            add(2, "stereo", t, f"{t['name']} is panned {float(t['pan']):+.2f}: keep a {t['role']} centered",
                ops=[{"op": "pan", "track": t.get("track"), "pan": 0.0, "why": f"{t['name']} centered"}], tags=["balance"])
    # ---- 9. bus glue + master chain (suggestions; the offline finisher is the honest way to hit a target)
    bus = n["bus"]
    add(3, "glue", None, f"glue: Fruity Compressor (or Fruity Limiter COMP) on the master or a music bus, "
        f"{bus['ratio']:g}:1, {bus['attack_ms']} ms attack, {int(round(_note_ms(bus['release_note'], bpm)))} ms release, "
        f"~{bus['gr_db']:g} dB gain reduction", settings=dict(bus), tags=["more_compressed"])
    try:
        tgt = H["_finish_target"]()
    except Exception:
        tgt = {"lufs": -14.0, "true_peak": -1.0}
    if mix.get("lufs") is not None:
        add(3, "master", None, f"master: finish to {tgt['lufs']:g} LUFS / {tgt['true_peak']:g} dBTP with fl_finalize "
            f"(offline true-peak limiter, verified) rather than a live maximizer; mix is now {mix['lufs']:.1f} LUFS",
            settings={"target_lufs": tgt["lufs"], "max_true_peak": tgt["true_peak"]}, tags=["louder"])
    b = mix.get("bands_db") or {}
    if b.get("low_mid") is not None and b["low_mid"] > -3.0 and not any(p["kind"] in ("mud_cut", "highpass") for p in props):
        add(2, "master_eq", None, f"whole mix heavy at 250-800 Hz ({b['low_mid']:+.1f} dB share): -1.5 dB at 350 Hz "
            "(Q 1) on the master, or find the track that causes it",
            ops=[{"op": "eq", "track": 0, "hz": 350, "q": 1.0, "gain_db": -1.5, "type": "Peaking",
                  "why": "master -1.5 dB @ 350 Hz"}], tags=["less_muddy"])
    # ---- rank: priority, goal match, then size
    for p in props:
        p["matches_goal"] = bool(goal_int & set(p["tags"]))
    props.sort(key=lambda p: (p["priority"] - (1 if p["matches_goal"] else 0), p["ops"] is None))
    seen, out = set(), []
    for p in props:
        k = (p["kind"], p["track"], p["name"])
        if k in seen:
            continue
        seen.add(k); out.append(p)
    out = out[:int(max_items)]
    for i, p in enumerate(out, 1):
        p["id"] = i
    return out


def _mix_listen(sa, sb, seconds):
    """The passage heard live once (at most fl_mix_check's 8 s window), master effects bypassed like fl_mix_check:
    the before / after of an applied mix pass."""
    m0 = _mixer_info(0)                                           # one track: small on any project size
    if m0 is None:
        st0 = H["fl_state"]()
        m0 = next((m for m in st0["mixer"] if m["index"] == 0), {})
    master_fx = bool(m0.get("effects"))
    if master_fx:
        _bridge("mixer_set", track=0, effects_enabled=False)
    try:
        secs = None if sb is not None else float(seconds or 20)
        est = H["_est_listen_s"](sa, sb, secs) or 20
        return H["_listen_measure"](sa, None, 8.0) if est > 8 else H["_listen_measure"](sa, sb, secs)
    finally:
        if master_fx:
            _bridge("mixer_set", track=0, effects_enabled=True)


def _live_meas(section, tracks, start_bar, end_bar, seconds, project):
    # PC-VERIFY: fl_mix_check's track names map back to mixer indices (duplicate names -> first match)
    if section and project and start_bar is None:
        S = sys.modules.get("flh_store")
        if S:
            start_bar, end_bar = S.section_range(S.journal_get(project), section)
    r = H["fl_mix_check"](start_bar=start_bar, end_bar=end_bar, seconds=seconds or 20, tracks=tracks)
    if r.get("heard") is False:
        raise RuntimeError(r.get("why") or "nothing heard")
    st = H["fl_state"]()
    by_name = {m["name"]: m for m in st["mixer"]}
    rows = []
    for name, m in (r.get("tracks") or {}).items():
        mm = by_name.get(name) or {}
        rows.append(dict(m, name=name, track=m.get("track", mm.get("index")), pan=mm.get("pan", 0.0)))
    mix = r.get("mix") or {}
    return {"tracks": rows, "mix": {"lufs": mix.get("lufs"), "true_peak": mix.get("true_peak_dbfs"),
                                    "bands_db": mix.get("bands_db")}}, (start_bar, end_bar)


def _one_export_meas(project, section=None, start_bar=None, end_bar=None, tracks=None):
    """Every used mixer track measured from ONE export with FL's 'Split mixer tracks' (cached: a later preview or
    fader change is mixed from it without exporting) instead of hearing the passage once per track in real time
    (PC 2026-10-26: 48 s of solo passes for a 10-track song). -> ((meas, (start_bar, end_bar)), None) or
    (None, why) when one-pass stems aren't available here (the caller listens instead)."""
    if not (project and H.get("_stems_set") and H.get("_rc")):
        return None, "no stems route"
    import numpy as np
    if section and start_bar is None:
        S = sys.modules.get("flh_store")
        if S:
            start_bar, end_bar = S.section_range(S.journal_get(project), section)
    ent, fp, info = H["_stems_set"](project)
    if ent is None:
        return None, info.get("why") or "no stems"
    RC = H["_rc"](); meta = ent.get("meta") or {}
    _tick_s, bar_s, _P, _m = RC.timeline(H["_read_flp_bytes"](project))
    a = bar_s(float(start_bar)) if start_bar else 0.0
    b = bar_s(float(end_bar)) if end_bar else None
    used = {int(t) for t in (meta.get("chan_insert") or {}).values() if int(t) > 0}
    want = {int(t) for t in tracks} if tracks else used
    try:
        live = {m_["index"]: m_ for m_ in (H["fl_state"]().get("mixer") or [])}
    except Exception:                                             # noqa: BLE001
        live = {}
    rows, total, fs0 = [], None, None
    for t_, tm in sorted((meta.get("tracks") or {}).items(), key=lambda kv: int(kv[0])):
        t = int(t_)
        f = RC.file_named(ent, f"t{t}")
        if t == 0 or f is None:
            continue
        x, fs = RC.load(f)
        old = (tm.get("level") or [None])[0]
        new = ((fp or {}).get("levels", {}).get(t) or [old])[0]
        x = x * (RC.fader_lin(new) / max(RC.fader_lin(old), 1e-12))
        seg = x[int(a * fs):(int(b * fs) if b else None)]
        if not len(seg):
            continue
        if 0 in (tm.get("routes") or [0]):                        # what reaches the master straight: the mix sum
            fs0 = fs0 or fs
            total = seg.copy() if total is None else (lambda k: total[:k] + seg[:k])(min(len(total), len(seg)))
        if t not in want or float(np.abs(seg).max()) < 10 ** (-70 / 20):
            continue
        lm = live.get(t) or {}
        rows.append(dict(features(seg, fs), name=lm.get("name") or tm.get("name") or f"Insert {t}", track=t,
                         pan=lm.get("pan", 0.0)))
    if not rows:
        return None, "the stems hold no sound in that range"
    mix = {}
    full = RC.get(RC.mix_key(fp, formats="wav")) if fp else None
    if full and full.get("files"):                                # the master render (its chain included)
        y, fsm = RC.load(full["files"][0])
        y = y[int(a * fsm):(int(b * fsm) if b else None)]
        if len(y):
            mf = features(y, fsm); mix = {"lufs": mf.get("lufs"), "true_peak": mf.get("true_peak"),
                                          "bands_db": mf.get("bands_db")}
    if not mix and total is not None and len(total):
        mf = features(total, fs0)
        mix = {"lufs": mf.get("lufs"), "true_peak": mf.get("true_peak"), "bands_db": mf.get("bands_db"),
               "approximate": True}
    how = info.get("reused") or "one export with FL's 'Split mixer tracks'"
    return ({"tracks": rows, "mix": mix, "how": how}, (start_bar, end_bar)), None


def _stems_meas(stems, section, project):
    items = _stem_items(stems)
    dur = max(len(it["x"]) / it["fs"] for it in items) if items else 0
    secs = _sections_s(None, project, dur) if project else [{"name": "whole song", "start_s": 0, "end_s": dur}]
    if section:
        secs = [s for s in secs if _norm_name(s["name"]).startswith(_norm_name(section))] or secs[:1]
    a, b = (secs[0]["start_s"], secs[-1]["end_s"]) if section else (0, dur)
    rows, total, fs0 = [], None, None
    import numpy as np
    for it in items:
        x = it["x"][int(a * it["fs"]):int(b * it["fs"])]
        f = features(x, it["fs"])
        rows.append(dict(f, name=it["name"], track=it.get("track")))
        if fs0 in (None, it["fs"]):
            fs0 = it["fs"]
            if total is None:
                total = x.copy()
            else:
                k = min(len(total), len(x)); total = total[:k] + x[:k]
    mix = features(total, fs0) if total is not None and len(total) else {}
    if mix:                                                      # stems each passed the master chain: their sum is
        mix["approximate"] = True                                # not the master (a limiter's peaks don't add up)
    return {"tracks": rows, "mix": mix}, items, secs


def mix_engineer(goal=None, section=None, tracks=None, apply=False, genre=None, stems=None, measurements=None,
                 project=None, bpm=None, lead=None, ids=None, start_bar=None, end_bar=None, seconds=None,
                 revert_last=False, max_items=14):
    """Engineer pass: measure (live fl_mix_check, rendered stems, or given measurements), propose prioritized
    moves, optionally apply them in one undoable batch and verify the result against the goal's intents."""
    if revert_last:
        return revert()
    steps, mark = _steps()
    live = measurements is None and not stems
    if live or apply:                                             # a retry right after a timed-out run finds FL
        _fl_gone_check("before the mix pass")                     # still loading: wait for it calmly (no bridge
        if _fl_loading():                                         # calls meanwhile) instead of piling calls on it
            _wait_fl_ready(project or H["_open_project_guess"]()[0], what="loading the project")   # its load cost
            mark("wait_fl")
    project = project or (H["_open_project_guess"]()[0] if live or apply else None)
    g = genre_key(genre, project)
    bpm_v, bpm_src = _bpm(bpm, project, live)
    findings, window = None, None
    if measurements is not None:
        meas = measurements
    elif stems:
        meas, items, secs = _stems_meas(stems, section, project)
        if len(items) >= 2:
            findings = masking_from_stems(items, secs, lead=lead, top=6)
    else:
        got, one_why = (_one_export_meas(project, section, start_bar, end_bar, tracks) if not seconds else
                        (None, "a seconds window is heard live"))
        if got:
            meas, window = got
        else:
            try:
                meas, window = _live_meas(section, tracks, start_bar, end_bar, seconds, project)
            except FLGone:
                raise
            except Exception:                                     # a timeout because FL went away: say THAT
                _fl_gone_check("while the tracks were measured")
                raise
            meas = dict(meas, how=f"each track heard alone ({one_why})")
    mark("measure")
    lead_src = "given" if lead else None
    if not lead and project:
        lead = _journal_lead(project, meas)
        lead_src = "the song journal (its melody channel)" if lead else None
    props = propose(meas, genre=g, goal=goal, bpm=bpm_v, masking_findings=findings, lead=lead, max_items=max_items)
    mark("propose")
    tr_ = _tracks_from(meas) if meas else []
    for t in tr_:
        t["imp"] = 4.0 if lead and _norm_name(lead) == _norm_name(t["name"]) else importance(t["role"])
    L_ = pick_lead(tr_)
    res = {"genre": g, "bpm": bpm_v, "bpm_source": bpm_src, "goal": goal,
           **({"measured": meas["how"]} if isinstance(meas, dict) and meas.get("how") else {}),
           "lead": ({"track": L_["name"], "from": lead_src or "the loudest melodic part (lead='<track>' to choose)"}
                    if L_ else None),
           "goal_intents": intents_from_text(goal) if goal else [],
           "goal_unclear": (parse_intents(goal)["ambiguous"] or None) if goal else None,
           "proposals": [{k: v for k, v in p.items() if k not in ("ops",) and v not in (None, [], False)}
                         | {"applies": p["ops"] is not None} for p in props],
           "masking": [f["say"] for f in findings] if findings else None}
    if isinstance(meas, dict) and (meas.get("mix") or {}).get("approximate"):
        res["mix_note"] = ("mix measured as the sum of the stems (each passed the master chain): approximate; master "
                           "headroom moves are skipped - check the real master with fl_review")
    H["_state"]({"mix_engineer_last": {"project": project, "proposals": props, "genre": g}})
    if not apply:
        res["next"] = "apply=true (optionally ids=[...]) runs the moves marked applies=true in one batch; revert_last=true undoes"
        res["steps_s"] = steps
        return res
    chosen = [p for p in props if p["ops"] and (ids is None or p["id"] in [int(i) for i in ids])]
    ops = [o for p in chosen for o in p["ops"] if o.get("track") is not None]
    unmapped = [o["why"] for p in chosen for o in p["ops"] if o.get("track") is None]
    before_mix = meas.get("mix") if isinstance(meas, dict) else None
    one_export = live and isinstance(meas, dict) and str(meas.get("how") or "").startswith(("one export", "stems from"))
    if one_export and H["fl_running"]():                          # the after-check is a short live listen: the
        try:                                                      # before must be the same kind of measurement
            before_mix = _mix_listen(*(window or (start_bar, end_bar)), seconds)
        except Exception:                                         # noqa: BLE001
            before_mix = None
        mark("measure_before")
    res["applied"] = apply_ops(ops, label="mix engineer" + (f": {goal}" if goal else ""), project=project)
    if unmapped:
        res["applied"]["not_applied"] = {"moves": unmapped[:10], "why": "no mixer track known for these stems (open "
                                         "the project in FL so stem channels map to mixer tracks)"}
    mark("apply")
    intents = res["goal_intents"] or list(dict.fromkeys(t for p in chosen for t in p["tags"]
                                                         if t in ("less_muddy", "less_harsh", "clearer")))[:3]
    if live and before_mix and H["fl_running"]():
        sa, sb = window or (start_bar, end_bar)
        after = _mix_listen(sa, sb, seconds)
        mark("measure_after")
        if after:
            res["verify"] = intent_verify(before_mix, after, intents) if intents else None
            res["after"] = {"lufs": after.get("lufs"), "bands_db": after.get("bands_db")}
    else:
        res["verify"] = None
        res["next"] = ("re-render (or fl_listen) and run " +
                       C.tool_call(H, "fl_verify_intent", {"before": C.Raw("old render"), "after": C.Raw("new"),
                                                           "intents": intents or ["clearer"]}) +
                       " to check; revert_last=true undoes")
    res["note"] = ("whole-mix verification only: per-track changes (e.g. a pad dip) may barely move the mix "
                   "metrics; listen too")
    res["steps_s"] = steps
    return res


def fl_mix_engineer(goal=None, section=None, tracks=None, apply=False, genre=None, stems=None, measurements=None,
                    project=None, bpm=None, lead=None, ids=None, start_bar=None, end_bar=None, seconds=None,
                    revert_last=False, background=None):
    kw = dict(goal=goal, section=section, tracks=tracks, apply=apply, genre=genre, stems=stems,
              measurements=measurements, project=project, bpm=bpm, lead=lead, ids=ids, start_bar=start_bar,
              end_bar=end_bar, seconds=seconds, revert_last=revert_last)
    live = measurements is None and not stems and not revert_last
    est = ((len(tracks) if tracks else 6) + 2) * 10 if live else 10
    return C.auto_job(H, "mixeng", est, mix_engineer, kw, background, fl=(live or bool(apply) or bool(revert_last)))


# ============================================================================= one room: a shared hall for acoustic layers
ROOM_STYLES = ("hall", "chamber", "studio", "auto", "none")
_DEFAULT_NAME = re.compile(r"^\s*(insert\s*\d*)?\s*$", re.I)


def _room_data():
    """data/mixeng/room.json with the collection profile's 'room' overrides (user taste) merged on top."""
    d = _data("mixeng/room.json")
    try:
        S = sys.modules.get("flh_store")
        if S and S.H:
            d = _deep_merge(d, (S.profile_get() or {}).get("room") or {})
    except Exception:
        pass
    return d


def _has_word(text, w):
    t = " " + _norm_name(text) + " "
    return (" " + _norm_name(w) + " ") in t


def room_family(*texts, data=None):
    """(family, why) for a track from its names (mixer track, channels, plugins, journal roles). family None:
    skipped (drums / synth bass / fx: they don't go in the room) or unknown."""
    D = data or _room_data()
    blob = " ".join(str(t or "") for t in texts)
    for k, ws in D["family_words"]:
        hit = next((w for w in ws if _has_word(blob, w)), None)
        if hit:
            return k, f"name: {hit}"
    hit = next((w for w in D["skip_words"] if _has_word(blob, w)), None)
    if hit:
        return None, f"'{hit}': kept out of the room (rhythm / low end / fx)"
    return None, "unknown instrument family (give tracks={track: family})"


def seat_pan(text, data=None):
    """Classical seating pan for a track name (violins left, cellos/basses right-center, ...) or None."""
    D = data or _room_data()
    for words, pan in D["seating"]:
        if any(_has_word(text, w) for w in words):
            return float(pan)
    return None


def _room_style(style, genre):
    D = _room_data()
    st = str(style or "auto").lower()
    if st not in ROOM_STYLES:
        raise ValueError(f"style must be one of {', '.join(ROOM_STYLES)}")
    if st == "auto":
        gs = D["genre_style"]
        return gs.get(genre, gs.get("default", "chamber")), f"auto from genre {genre}"
    return st, "given"


def _reverb_choice(reverb, style):
    """(plugin, why): Fruity Convolver where the user's FL edition owns it AND its map can set pre-delay/decay
    (a measured map from fl_plugin_map_learn), else Fruity Reeverb 2 (every edition, settable from tempo)."""
    rv = str(reverb or "auto").lower()
    own = H.get("plugin_ownership")
    status = None
    try:
        status = own("Fruity Convolver")["status"] if own else None
    except Exception:
        status = None
    # PC-VERIFY: Fruity Convolver's parameter names for pre-delay / decay once learned (fl_plugin_map_learn) map to
    # predelay_ms / decay_s; a default Convolver loads with an IR the user can swap for a hall
    cmap = plugin_map("Fruity Convolver") or {}
    settable = all(k in (cmap.get("controls") or {}) for k in ("predelay_ms", "decay_s"))
    if rv in ("convolver", "fruity convolver"):
        if status == "trial":
            raise ValueError("Fruity Convolver is a demo in your FL edition (a project saved with it warns): use "
                             "reverb='reeverb'")
        return "Fruity Convolver", ("asked for" + ("" if settable else "; its map can't set pre-delay/decay: they come "
                                                     "from the impulse response you load (pick a hall IR)"))
    if rv in ("reeverb", "reeverb 2", "fruity reeverb 2"):
        return "Fruity Reeverb 2", "asked for"
    if status is True and settable and style in ("hall", "chamber"):
        return "Fruity Convolver", "your FL edition owns it and its learned map sets pre-delay/decay"
    why = "stock in every edition; pre-delay/decay set from tempo"
    if status is True:
        why += ("; you own Fruity Convolver too (a hall impulse response sounds more real): reverb='convolver' uses "
                "it once fl_plugin_map_learn has mapped its pre-delay/decay")
    return "Fruity Reeverb 2", why


def room_plan(rows, style="hall", genre="default", bpm=120.0, reverb_plugin="Fruity Reeverb 2", buses=None):
    """The room as moves (pure: no FL). rows: [{track, name, family, pan?, texts?}] (family None = skipped).
    -> {style, bus: {tail: {plugin, controls}, er?: {...}}, tracks: [{track, name, family, distance, send_db,
        er_send_db, shelf, pan_to}], skipped, orchestral, ops (when buses = {"tail": t, "er": t, ...} is given)}"""
    D = _room_data()
    S = D["styles"][style]
    bpm = float(bpm or 120.0)
    dec_lo, dec_hi = S["decay_s"]; pre_lo, pre_hi = S["predelay_ms"]
    decay = round(min(dec_hi, max(dec_lo, float(S["decay_beats"]) * 60.0 / bpm)), 2)
    pre = round(min(pre_hi, max(pre_lo, _note_ms(S["predelay_note"], bpm))))
    er_on = bool(S.get("er"))
    tail = {"dry_pct": 0, "wet_pct": 100, "predelay_ms": pre, "decay_s": decay}
    if reverb_plugin == "Fruity Reeverb 2":
        tail.update(room_size=S["room_size"], diffusion=S["diffusion"], high_damping_hz=S["high_damping_hz"],
                    low_cut_hz=S["low_cut_hz"], high_cut_hz=S["high_cut_hz"], bass_multiplier=S["bass_multiplier"],
                    stereo_separation=S["stereo_separation"], early_pct=S.get("tail_early_pct", 20 if er_on else 60))
    else:
        mp = (plugin_map(reverb_plugin) or {}).get("controls") or {}
        tail = {k: v for k, v in tail.items() if k in mp}
    bus = {"tail": {"plugin": reverb_plugin, "name": D["bus_names"]["tail"], "controls": tail,
                    "about": f"{S['about']}: decay {decay} s ({S['decay_beats']} beats at {bpm:g} BPM, clamped "
                             f"{dec_lo}-{dec_hi} s), pre-delay {pre} ms ({S['predelay_note']})"}}
    if er_on:
        eb = dict(S["er_bus"]); eb.update(dry_pct=0)
        bus["er"] = {"plugin": "Fruity Reeverb 2", "name": D["bus_names"]["er"], "controls": eb,
                     "about": "early reflections only (short, dense): how far away each player sounds"}
    off = float(S.get("send_offset_db", 0)) + float((D.get("genre_send_offset_db") or {}).get(genre, 0))
    fams = [r.get("family") for r in rows if r.get("family")]
    orch = genre in ("orchestral", "cinematic") or sum(1 for f in fams if f in D["orchestral_families"]) >= 2
    shelf_k = {"hall": 1.0, "chamber": 0.5}.get(style, 0.0)
    out, skipped = [], []
    for r in rows:
        fam = r.get("family")
        if not fam:
            skipped.append({"track": r.get("track"), "name": r.get("name"), "why": r.get("why") or "no family"})
            continue
        dist = D["families"].get(fam, "mid")
        dd = D["distance"][dist]
        t = {"track": r["track"], "name": r.get("name"), "family": fam, "distance": dist,
             "send_db": round(float(dd["send_db"]) + off, 1)}
        if er_on:
            t["er_send_db"] = round(float(dd["er_send_db"]) + off, 1)
        sh = dd.get("shelf")
        if sh and shelf_k:
            t["shelf"] = {"hz": sh["hz"], "gain_db": round(float(sh["gain_db"]) * shelf_k, 1)}
        cur = r.get("pan")
        if orch and fam not in ("piano", "keys", "vocal", "lead"):
            sp = seat_pan(" ".join(str(x or "") for x in [r.get("name")] + list(r.get("texts") or [])), D)
            if sp is not None and (cur is None or abs(float(cur)) < 0.05):
                t["pan_to"] = sp
            elif sp is not None:
                t["pan_kept"] = f"already panned {float(cur):+.2f} (yours)"
        out.append(t)
    res = {"style": style, "bpm": bpm, "bus": bus, "tracks": out, "skipped": skipped, "orchestral": orch,
           "send_offset_db": off}
    if buses:
        res["ops"] = _room_ops(res, buses)
    return res


def _room_ops(plan, buses):
    """apply_ops moves for a room plan. buses: {"tail": track, "er": track, "tail_slot": n, "er_slot": n,
    "tail_add": bool, "er_add": bool}."""
    D = _room_data(); ops = []
    for k in ("tail", "er"):
        b = plan["bus"].get(k)
        if not b or buses.get(k) is None:
            continue
        t = int(buses[k])
        ops.append({"op": "name", "track": t, "name": b["name"], "color": D.get("bus_color"),
                    "why": f"bus '{b['name']}' on mixer track {t}"})
        ops.append({"op": "plugin", "track": t, "slot": int(buses.get(f"{k}_slot") or 0), "plugin": b["plugin"],
                    "add": bool(buses.get(f"{k}_add")), "controls": b["controls"],
                    "why": f"{b['name']}: {b['plugin']} {b['about']}"})
    for t in plan["tracks"]:
        if int(t["track"]) in {buses.get("tail"), buses.get("er")}:
            continue
        if buses.get("tail") is not None:
            ops.append({"op": "send", "track": t["track"], "to": int(buses["tail"]), "db": t["send_db"],
                        "why": f"{t['name']} -> room {t['send_db']:+.1f} dB ({t['distance']})"})
        if t.get("er_send_db") is not None and buses.get("er") is not None:
            ops.append({"op": "send", "track": t["track"], "to": int(buses["er"]), "db": t["er_send_db"],
                        "why": f"{t['name']} -> early reflections {t['er_send_db']:+.1f} dB"})
        if t.get("shelf"):
            ops.append({"op": "eq", "track": t["track"], "hz": t["shelf"]["hz"], "gain_db": t["shelf"]["gain_db"],
                        "type": "High shelf", "why": f"{t['name']} distance darkening {t['shelf']['gain_db']:+.1f} dB "
                                                     f"above {_fmt_hz(t['shelf']['hz'])}"})
        if t.get("pan_to") is not None:
            ops.append({"op": "pan", "track": t["track"], "pan": t["pan_to"],
                        "why": f"{t['name']} seated {t['pan_to']:+.2f}"})
    return ops


def _room_rows(project, tracks, live):
    """Tracks the room could take: [{track, name, family, why, pan, texts}] from FL (live) or the .flp, named by
    the mixer track, its channels/plugins and the journal's channel roles. tracks: [n] (only these) or
    {n: family} (these, with the family given)."""
    D = _room_data()
    given = {}
    if isinstance(tracks, dict):
        given = {int(k): (str(v).lower() if v else None) for k, v in tracks.items()}
        only = set(given)
    else:
        only = {int(x) for x in tracks} if tracks else None
    jch = {}
    try:
        S = sys.modules.get("flh_store")
        jch = ((S.journal_get(project) or {}).get("channels") or {}) if S and project else {}
    except Exception:
        jch = {}
    by = {}
    if live:
        st = H["fl_state"]()
        mx = {m["index"]: m for m in st.get("mixer", [])}
        for c in st.get("channels", []):
            t = c.get("insert")
            if t is None or int(t) <= 0:
                continue
            j = jch.get(str(c["index"])) or {}
            r = by.setdefault(int(t), {"track": int(t), "name": (mx.get(t) or {}).get("name") or c.get("name"),
                                       "pan": (mx.get(t) or {}).get("pan", 0.0), "texts": []})
            r["texts"] += [c.get("name"), c.get("plugin"), j.get("name"), j.get("role") if j.get("role") not in
                           ("chords", "melody", "counter") else None]
    else:
        fl = H["flp_list"](project)
        for c in fl.get("channels", []):
            t = c.get("insert")
            if t is None or int(t) <= 0:
                continue
            j = jch.get(str(c["index"])) or {}
            r = by.setdefault(int(t), {"track": int(t), "name": c.get("name") or j.get("name"), "pan": None, "texts": []})
            r["texts"] += [c.get("name"), c.get("plugin"), j.get("name")]
    for t, f in given.items():
        by.setdefault(t, {"track": t, "name": f"Insert {t}", "pan": None, "texts": []})
    rows = []
    for t, r in sorted(by.items()):
        if only is not None and t not in only:
            continue
        r["texts"] = [x for x in r["texts"] if x]
        if t in given and given[t]:
            fam, why = given[t], "given"
            if fam not in D["families"]:
                raise ValueError(f"track {t}: family must be one of {', '.join(sorted(D['families']))}")
        else:
            fam, why = room_family(r.get("name"), *r["texts"], data=D)
        r.update(family=fam, why=why)
        rows.append(r)
    return rows


def _find_room_buses(project, need_er):
    # PC-VERIFY: mixer_info lists every insert with its effects; the first free insert after the used ones is empty
    # in FL too (no effects, default name); a track the user named 'Room' is reused, never a second bus made
    """Mixer tracks for the room's buses: the journal's (still named so), tracks already named Room / Room ER, else
    the first free inserts (no channel, default name, no effects) after the used ones. Reads only."""
    D = _room_data(); names = D["bus_names"]
    jr = {}
    try:
        S = sys.modules.get("flh_store")
        jr = ((S.journal_get(project) or {}).get("room") or {}) if S and project else {}
    except Exception:
        jr = {}
    rows = None
    try:
        r = _bridge("mixer_info", tracks="all")
        rows = r.get("tracks") if isinstance(r, dict) else None
    except Exception:
        rows = None
    how = "mixer_info"
    if not rows:                                                  # older bridge: state + read-back probing
        how = "state + probing (effects on free tracks can't be seen: check them)"
        st = H["fl_state"]()
        used = {c.get("insert") for c in st.get("channels", [])}
        known = {m["index"]: m for m in st.get("mixer", [])}
        rows = []
        for t in range(1, 126):
            if t in known:
                rows.append(dict(known[t], used=True, effects=[e["name"] for e in known[t].get("effects", [])])); continue
            try:
                m = _bridge("mixer_set", track=t)
            except Exception:
                break
            rows.append({"index": t, "name": m.get("name"), "used": t in used, "effects": None})
            if len([x for x in rows if not x.get("used") and _DEFAULT_NAME.match(x.get("name") or "")]) >= 2:
                break
    byi = {int(x["index"]): x for x in rows}
    out, taken = {"how": how}, set()
    for k in ("tail",) + (("er",) if need_er else ()):
        want = names[k]
        t = (jr.get("buses") or {}).get(k)
        if t is not None and _norm_name((byi.get(int(t)) or {}).get("name")) == _norm_name(want):
            t = int(t)
        else:
            t = next((i for i, x in sorted(byi.items()) if i >= 1 and _norm_name(x.get("name")) == _norm_name(want)
                      and i not in taken), None)
        if t is not None:
            fx = byi[t].get("effects")
            out[k] = t; out[f"{k}_existing"] = True
            out[f"{k}_effects"] = fx
        else:
            top = max([i for i, x in byi.items() if x.get("used")] or [0])
            free = [i for i, x in sorted(byi.items()) if i >= 1 and not x.get("used") and i not in taken
                    and _DEFAULT_NAME.match(x.get("name") or "") and not x.get("effects") and not x.get("muted")]
            t = next((i for i in free if i > top), None) or (free[0] if free else None)
            if t is None:
                raise RuntimeError("no free mixer insert for the room bus (no channel, default name, no effects): "
                                   "free one or name a track 'Room'")
            out[k] = t; out[f"{k}_existing"] = False
        taken.add(t)
    return out


def _bus_slot(track, plugin, effects):
    """(slot, add?) for the room's reverb on a bus: its slot when it's there, else the next slot to add it in."""
    mp = plugin_map(plugin) or {"plugin": plugin, "aliases": []}
    if effects and any(_plugin_name_ok(mp, n) for n in effects):
        for s in range(10):
            try:
                live = H["fl_plugin_params"](int(track), s)
                if _plugin_name_ok(mp, live.get("plugin")):
                    return s, False
            except Exception:
                continue
    if effects:                                                    # something else is there: the first empty slot
        for s in range(10):
            try:
                live = H["fl_plugin_params"](int(track), s)
                if not (live or {}).get("plugin"):
                    return s, True
            except Exception:
                return s, True
        raise RuntimeError(f"no free effect slot on the room bus (mixer track {track})")
    return 0, True


def room(project=None, style="auto", apply=False, tracks=None, genre=None, bpm=None, reverb="auto", verify=True,
         seconds=8.0, revert_last=False):
    """One room for the acoustic layers: a shared reverb (tail + early-reflection buses), sends by stage distance,
    distance darkening, classical seating. Proposal by default; apply=True runs it in one undoable batch."""
    if revert_last:
        return revert()
    steps, mark = _steps()
    live = bool(H.get("fl_running") and H["fl_running"]())
    project = project or (H["_open_project_guess"]()[0] if live else None)
    if not live and not project:
        raise ValueError("give project= (the .flp) or open it in FL")
    g = genre_key(genre, project)
    st_name, st_why = _room_style(style, g)
    bpm_v, bpm_src = _bpm(bpm, project, live)
    res = {"genre": g, "style": st_name, "style_from": st_why, "bpm": bpm_v, "bpm_source": bpm_src}
    if st_name == "none":
        res.update(proposal=None, say=f"no shared room for {g}: these productions keep their space synthetic / dry "
                   "(per-sound reverbs and delays); style='studio' adds a subtle one if the layers sound pasted on",
                   steps_s=steps)
        return res
    rows = _room_rows(project, tracks, live)
    mark("read")
    plug, plug_why = _reverb_choice(reverb, st_name)
    plan = room_plan(rows, st_name, g, bpm_v, plug)
    res["reverb"] = {"plugin": plug, "why": plug_why}
    buses = None
    if live:
        try:
            buses = _find_room_buses(project, "er" in plan["bus"])
        except Exception as e:
            res["bus_problem"] = f"{type(e).__name__}: {e}"
    mark("plan")
    view = {"bus": {k: {"track": (buses or {}).get(k), "plugin": b["plugin"], "about": b["about"],
                        "settings": b["controls"], **({"reused": True} if (buses or {}).get(f"{k}_existing") else {})}
                    for k, b in plan["bus"].items()},
            "tracks": plan["tracks"], "skipped": plan["skipped"] or None, "orchestral_seating": plan["orchestral"]}
    res["proposal"] = {k: v for k, v in view.items() if v is not None}
    if not plan["tracks"]:
        res["say"] = "no acoustic layers found to put in a room (all skipped): give tracks={track: family}"
        res["steps_s"] = steps
        return res
    if not apply:
        res["next"] = ("apply=true builds it in one batch (buses + reverb + sends + darkening + seating); "
                       "revert_last=true undoes" + ("" if live else "; open the project in FL first (live moves)"))
        res["steps_s"] = steps
        return res
    if not live:
        raise RuntimeError("apply needs FL running with this project open (sends and knobs are set live)")
    if not buses:
        raise RuntimeError(res.get("bus_problem") or "no mixer track for the room bus")
    for k in ("tail", "er"):
        if k in plan["bus"] and buses.get(k) is not None:
            if buses.get(f"{k}_existing"):
                buses[f"{k}_slot"], buses[f"{k}_add"] = _bus_slot(buses[k], plan["bus"][k]["plugin"],
                                                                  buses.get(f"{k}_effects"))
            else:
                buses[f"{k}_slot"], buses[f"{k}_add"] = 0, True
    # PC-VERIFY: on a real orchestral song - Room / Room ER appear on free inserts with Fruity Reeverb 2 at 100% wet,
    # 0% dry, decay/pre-delay as proposed (Reeverb 2's shipped map is unverified: run fl_plugin_map_learn on it once);
    # each layer's send knob shows the proposed level; far tracks get a PEQ2 high shelf; violins pan left, cellos
    # right; the piano stays driest. Listen: one room, not each layer in its own space; nothing doubles in volume
    ops = _room_ops(plan, buses)
    try:
        S = sys.modules.get("flh_store")
        old_er = (((S.journal_get(project) or {}).get("room") or {}).get("buses") or {}).get("er") if S and S.H else None
    except Exception:
        old_er = None
    if old_er is not None and "er" not in plan["bus"]:
        res.setdefault("notes", []).append(f"the earlier '{_room_data()['bus_names']['er']}' bus (mixer track {old_er}) "
                                           "still gets sends from the last room: revert_last=true first for a clean switch")
    before = None
    measure = verify and H.get("_listen_measure")
    if measure:
        try:
            before = H["_listen_measure"](None, None, float(seconds))
        except Exception:
            before = None
        mark("measure_before")
    ap = apply_ops(ops, label=f"room: {st_name}", project=project)
    mark("apply")
    res["applied"] = ap
    bad = [d for d in ap["done"] if d.get("ok") is False]
    res["verify"] = {"settings": f"{len(ap['done']) - len(bad)}/{len(ap['done'])} moves read back as set",
                     "check": [d.get("why") for d in bad][:6] or None, "skipped": len(ap["skipped"]) or None}
    if before:
        try:
            after = H["_listen_measure"](None, None, float(seconds))
            mark("measure_after")
            if after:
                res["verify"]["sound"] = intent_verify(before, after, ["wider"])
                res["verify"]["sound"]["note"] = ("a room mostly adds side energy (width) and a tail; whether the "
                                                  "layers now sound like one recorded space is a listening call")
        except Exception as e:
            res["verify"]["sound"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        S = sys.modules.get("flh_store")
        if S and S.H and project:
            S.journal_update(project, set={"room": {
                "style": st_name, "genre": g, "bpm": bpm_v, "reverb": plug, "when": time.strftime("%Y-%m-%d %H:%M"),
                "buses": {k: buses.get(k) for k in ("tail", "er") if buses.get(k) is not None},
                "slots": {k: buses.get(f"{k}_slot") for k in ("tail", "er") if buses.get(k) is not None},
                "tracks": {str(t["track"]): {"family": t["family"], "distance": t["distance"], "send_db": t["send_db"]}
                           for t in plan["tracks"]}}})
    except Exception:
        pass
    res["note"] = ("a human should listen: send levels are starting points (front players drier, far ones wetter); "
                   "revert_last=true (or fl_mix_engineer revert_last=true) undoes the moves")
    res["steps_s"] = steps
    return res


def fl_room(project=None, style="auto", apply=False, tracks=None, genre=None, bpm=None, reverb="auto", verify=True,
            seconds=8.0, revert_last=False, background=None):
    kw = dict(project=project, style=style, apply=apply, tracks=tracks, genre=genre, bpm=bpm, reverb=reverb,
              verify=verify, seconds=seconds, revert_last=revert_last)
    est = (2 * float(seconds or 8) + 15) if apply and verify else (15 if apply else 3)
    return C.auto_job(H, "room", est, room, kw, background, fl=True)


# ============================================================================= master tone (flp_master_tone)
# A song's tone curve lives IN the project: a Fruity Parametric EQ 2 on the master, written into the .flp through
# the plugin map's 'file_state' layout (data/plugin_maps/fruity_parametric_eq_2.json), recorded in the song's journal
# ('master_tone'). Every render (fl_preview, fl_finalize, stems) then has it - no offline EQ step after rendering.
TONE_TYPES = {"peak": "Peaking", "low_shelf": "Low shelf", "high_shelf": "High shelf", "high_pass": "High pass",
              "low_pass": "Low pass"}
_TONE_ALIASES = {"bell": "peak", "peaking": "peak", "lowshelf": "low_shelf", "ls": "low_shelf",
                 "highshelf": "high_shelf", "hs": "high_shelf", "highpass": "high_pass", "hp": "high_pass",
                 "hpf": "high_pass", "lowpass": "low_pass", "lp": "low_pass", "lpf": "low_pass"}
_TONE_OUT = {"peaking": "peak", "low shelf": "low_shelf", "high shelf": "high_shelf", "high pass": "high_pass",
             "low pass": "low_pass", "band pass": "band_pass", "notch": "notch", "off": "off"}
TONE_MAX_BANDS = 7
TONE_BY = "flp_master_tone"
# dynamics on the master: a tone EQ goes BEFORE them (after a limiter its boosts would pass the ceiling)
_DYN_RE = re.compile(r"(?i)limiter|maximus|maximi[sz]er|compress|soundgoodizer|clipper|multiband|\bott\b|glue")
_TONE_PROOF = {}


class ToneLayoutUnknown(RuntimeError):
    """Parametric EQ 2's saved state can't be confirmed to follow the map's file_state layout on this PC."""


def tone_presets():
    """{name: {description, bands}}: data/mixeng/master_tone.json, then the user's <state folder>/master_tone.json."""
    out = {}
    for f in (os.path.join(DATA, "mixeng", "master_tone.json"), os.path.join(_state_dir(), "master_tone.json")):
        if os.path.isfile(f):
            try:
                out.update({str(k).lower(): v for k, v in (_json(f).get("presets") or {}).items() if isinstance(v, dict)})
            except Exception:                                     # noqa: BLE001 - a broken user file: shipped stay
                continue
    return out


def _tone_kind(t):
    k = re.sub(r"[\s\-]+", "_", str(t or "").strip().lower())
    k = _TONE_ALIASES.get(k.replace("_", ""), _TONE_ALIASES.get(k, k))
    if k not in TONE_TYPES:
        raise ValueError(f"band type '{t}': use one of {', '.join(TONE_TYPES)}")
    return k


def tone_bands(bands=None, preset=None):
    """Checked, canonical bands [{type, hz, gain_db, q}] from `bands` or a preset name. Refuses (never clamps)
    values outside safe master ranges: 20 Hz-20 kHz, -18..+9 dB, Q 0.1-18; at most 7 bands (PEQ2 has 7)."""
    if bands is not None and preset:
        raise ValueError("give bands or a preset, not both")
    src = bands
    if preset:
        pr = tone_presets()
        hit = pr.get(str(preset).strip().lower())
        if not hit:
            raise ValueError(f"no master tone preset '{preset}' (have: {', '.join(sorted(pr)) or 'none'})")
        src = hit.get("bands") or []
    if isinstance(src, dict):
        src = [src]
    if not isinstance(src, list):
        raise ValueError("bands: [{type: peak|low_shelf|high_shelf|high_pass|low_pass, hz, gain_db, q}]")
    if len(src) > TONE_MAX_BANDS:
        raise ValueError(f"{len(src)} bands: Parametric EQ 2 has {TONE_MAX_BANDS}")
    out = []
    for i, b in enumerate(src):
        if not isinstance(b, dict):
            raise ValueError(f"band {i + 1}: an object {{type, hz, gain_db, q}}")
        kind = _tone_kind(b.get("type"))
        try:
            hz = float(b.get("hz", b.get("freq_hz", b.get("freq"))))
        except (TypeError, ValueError):
            raise ValueError(f"band {i + 1}: hz (the band's frequency) is missing") from None
        lo, hi = SAFE["hz"]
        if not (lo <= hz <= hi) or math.isnan(hz):
            raise ValueError(f"band {i + 1}: {hz:g} Hz is outside {lo:g}-{hi:g} Hz")
        g = float(b.get("gain_db") or 0.0)
        glo, ghi = SAFE["eq_gain_db"]
        if not (glo <= g <= ghi) or math.isnan(g):
            raise ValueError(f"band {i + 1}: {g:+g} dB is outside {glo:g}..+{ghi:g} dB for a master tone band")
        if kind in ("high_pass", "low_pass"):
            g = 0.0
        q = b.get("q")
        if q is not None:
            q = float(q); qlo, qhi = SAFE["q"]
            if not (qlo <= q <= qhi) or math.isnan(q):
                raise ValueError(f"band {i + 1}: Q {q:g} is outside {qlo:g}-{qhi:g}")
        out.append({"type": kind, "hz": round(hz, 2), "gain_db": round(g, 2), "q": None if q is None else round(q, 3)})
    return out


def _tone_merge(old, new):
    """replace=false on our own EQ: a new band takes the place of an old one of the same type within 1/3 octave."""
    out = [dict(b) for b in old or []]
    for nb in new:
        hit = next((i for i, ob in enumerate(out) if ob.get("type") == nb["type"] and ob.get("hz") and
                    abs(math.log2(float(ob["hz"]) / nb["hz"])) <= 1 / 3), None)
        if hit is None:
            out.append(nb)
        else:
            out[hit] = nb
    if len(out) > TONE_MAX_BANDS:
        raise ValueError(f"merged with the tone already there that's {len(out)} bands (max {TONE_MAX_BANDS}): "
                         "use replace=true for a new curve")
    return out


def tone_assign(bands):
    """Which PEQ2 band (1..7) holds which tone band: high-pass / low shelf from band 1 up, high shelf / low pass from
    band 7 down, bells in between in frequency order. -> {band number: band}"""
    low = sorted([b for b in bands if b["type"] in ("high_pass", "low_shelf")], key=lambda b: (b["type"] != "high_pass", b["hz"]))
    high = sorted([b for b in bands if b["type"] in ("high_shelf", "low_pass")], key=lambda b: (b["type"] == "low_pass", b["hz"]))
    mid = sorted([b for b in bands if b["type"] == "peak"], key=lambda b: b["hz"])
    out = {}
    for i, b in enumerate(low):
        out[i + 1] = b
    for i, b in enumerate(reversed(high)):
        out[TONE_MAX_BANDS - i] = b
    inner = [n for n in range(2, TONE_MAX_BANDS) if n not in out]   # bells in bands 2..6 first, the edges if needed
    edges = [n for n in (1, TONE_MAX_BANDS) if n not in out]
    pool = sorted(inner[:len(mid)] + edges[:max(0, len(mid) - len(inner))])
    for n, b in zip(pool, mid):
        out[n] = b
    return out


def _tone_layout(mp):
    """{control: (int index, [lo, hi] | 'enum')} from the map's file_state, or None."""
    fs_ = (mp or {}).get("file_state") or {}
    if fs_.get("format") != "int32le" or not fs_.get("groups"):
        return None
    out = {}
    for g in fs_["groups"]:
        for i in range(int(g.get("count", 1))):
            out[str(g["controls"]).replace("{n}", str(i + 1))] = (int(g["first"]) + i, g.get("ints"))
    return out


def peq_decode(state, mp=None):
    """A Parametric EQ 2 state (bytes from the .flp) -> ({control: value in its unit, None = out of range}, {control:
    raw int}) through the map's file_state layout and the controls' curves."""
    mp = mp or plugin_map(PEQ) or {}
    lay = _tone_layout(mp)
    if not lay:
        raise ValueError("the Parametric EQ 2 map has no file_state layout")
    n = len(state or b"") // 4
    need = max(int(mp["file_state"].get("min_ints") or 0), max(i for i, _ in lay.values()) + 1)
    if n < need:
        raise ValueError(f"a Parametric EQ 2 state of {len(state or b'')} bytes is shorter than the map's layout "
                         f"({need} ints)")
    ints = struct.unpack_from(f"<{n}i", state)
    vals, raw = {}, {}
    for key, (i, ir) in lay.items():
        v = ints[i]; raw[key] = v
        e = (mp.get("controls") or {}).get(key) or {}
        if ir == "enum":
            opts = e.get("options") or []
            vals[key] = opts[v] if 0 <= v < len(opts) else None
        else:
            lo, hi = float(ir[0]), float(ir[1])
            vals[key] = from_norm(e, (v - lo) / (hi - lo)) if lo <= v <= hi else None
    return vals, raw


def _peq_encode(state, mp, norms):
    """Write {control: 0..1 value (enum: option index)} into a state through the layout; other bytes stay."""
    lay = _tone_layout(mp); n = len(state) // 4
    ints = list(struct.unpack_from(f"<{n}i", state))
    for key, v in norms.items():
        i, ir = lay[key]
        ints[i] = int(v) if ir == "enum" else int(round(float(ir[0]) + _clip01(v) * (float(ir[1]) - float(ir[0]))))
    return struct.pack(f"<{n}i", *ints) + bytes(state[n * 4:])


def _tone_default(mp):
    # PC-VERIFY: FL's 'Fruity Parametric EQ 2.fst' (Plugin database -> Default.fst) state reads as the map's
    # file_state: band types Low shelf / Peaking x5 / High shelf, levels 0 dB (proof ok on a real FL 2026)
    """FL's own default Parametric EQ 2 (its preset on this PC) and whether its saved state reads as the map's layout
    says: every band type the map's defaults, every level 0 dB, every value in range.
    -> {ok, why, preset, blk, state}. Cached per preset file version."""
    pre = _TONE_PROOF.get("preset")
    if not (pre and os.path.isfile(pre)):                         # one walk of FL's preset folders per session
        pres = []
        try:
            pres = H["fl_find_presets"](PEQ, limit=20)["presets"]
        except Exception:                                         # noqa: BLE001
            pass
        want = (PEQ + ".fst").lower()
        pre = next((p for p in pres if p.lower().endswith(want)), None)
    if not pre and H.get("_fst_default_for"):
        try:
            pre = H["_fst_default_for"](PEQ)
        except Exception:                                         # noqa: BLE001
            pre = None
    if not pre:
        return {"ok": False, "why": f"no {PEQ} preset on this PC (FL's Plugin database: FL builds it on its first "
                                    "start / plugin scan)"}
    _TONE_PROOF["preset"] = pre
    try:
        key = (os.path.normcase(os.path.abspath(pre)), os.path.getmtime(pre), json.dumps(mp.get("file_state"),
                                                                                       sort_keys=True))
    except OSError:
        key = None
    if key and key in _TONE_PROOF:
        return _TONE_PROOF[key]
    res = {"ok": False, "preset": pre}
    try:
        blk, _name = H["_fst_block"](pre)
        state = next((p for eid, p, _ in blk if eid == H["EV_PLUGIN_STATE"]), None)
        if state is None:
            raise ValueError("the preset holds no plugin state")
        vals, _raw = peq_decode(state, mp)
        bad = [k for k, v in vals.items() if v is None]
        for pat, exp in ((mp.get("file_state") or {}).get("default_check") or {}).items():
            for b in range(1, TONE_MAX_BANDS + 1):
                k = str(pat).replace("{n}", str(b))
                if k not in vals:
                    continue
                e = (mp.get(exp) or [])[b - 1] if isinstance(exp, str) and isinstance(mp.get(exp), list) else exp
                got = vals[k]
                same = (str(got).lower() == str(e).lower()) if isinstance(e, str) else \
                    (isinstance(got, (int, float)) and abs(float(got) - float(e)) < 0.01)
                if not same:
                    bad.append(f"{k} reads {got!r}, {e!r} expected")
        res.update(blk=blk, state=state, ok=not bad)
        if bad:
            res["why"] = (f"FL's default {PEQ} on this PC doesn't read as the plugin map's file layout ("
                          + "; ".join(bad[:3]) + (f"; +{len(bad) - 3} more" if len(bad) > 3 else "") + ")")
    except Exception as e:                                        # noqa: BLE001 - reported, never guessed around
        res["why"] = f"FL's {PEQ} preset can't be read as the map's file layout ({type(e).__name__}: {e})"
    if key:
        _TONE_PROOF[key] = res
    return res


def _tone_norms(mp, b, band, notes):
    """{control: 0..1 / option index} for PEQ2 band `b` set to `band` (None: back to FL's default flat band)."""
    ctl = mp.get("controls") or {}
    k = f"band{b}_"
    if band is None:
        return {}
    te = ctl[k + "type"]; opts = [str(o).lower() for o in te.get("options") or []]
    label = TONE_TYPES[band["type"]].lower()
    if label not in opts:
        raise ValueError(f"the map's band type options {te.get('options')} have no '{TONE_TYPES[band['type']]}'")
    out = {k + "type": opts.index(label), k + "freq_hz": to_norm(ctl[k + "freq_hz"], band["hz"]),
           k + "gain_db": to_norm(ctl[k + "gain_db"], band["gain_db"])}
    if band.get("q"):
        we = ctl.get(k + "width") or {}; wu = str(we.get("unit") or "")
        br = bridge_unit(mp, k + "width", band["q"], "Q", "%")
        if br is not None:                                        # the map's Q -> bandwidth table (unit_bridges)
            pct, note = br
            out[k + "width"] = to_norm(we, pct) if wu == "%" else _clip01(pct / 100.0)
            notes.add(note)
        elif units_convertible("Q", wu):
            out[k + "width"] = to_norm(we, convert_unit(band["q"], "Q", wu))
        else:
            notes.add(f"band {b}: no conversion from Q to the width's unit ({wu}): width left at FL's default")
    return out


def _tone_slots(d, ev):
    """Master (track 0) slots: {slot: (first event of its plugin block or None, slot marker, plugin name or None,
    state event index or None)}."""
    out = {}
    for (t, s), (first, marker) in H["_mixer_slots"](d, ev).items():
        if t != 0:
            continue
        name = k_state = None
        if first is not None:
            name = H["_text16"](H["_payload"](d, ev[first]))
            k_state = next((k for k in range(first, marker) if ev[k]["id"] == H["EV_PLUGIN_STATE"]), None)
        out[s] = (first, marker, name, k_state)
    return out


def _is_peq(name):
    return bool(name) and _plugin_name_ok(plugin_map(PEQ) or {"plugin": PEQ, "aliases": []}, name)


def _journal(project):
    S = sys.modules.get("flh_store")
    try:
        return (S.journal_get(project) or {}) if S and S.H else {}
    except Exception:                                             # noqa: BLE001
        return {}


def _tone_target(names, tone, slot, replace):
    """(slot, reuse, took_over, warning) on the master: {slot: plugin name or None} -> where the tone EQ goes."""
    ours = tone.get("slot") if isinstance(tone, dict) else None
    ours = int(ours) if ours is not None and _is_peq(names.get(int(ours))) else None
    if slot is not None:
        s = int(slot)
        if s not in names:
            raise ValueError(f"the master has no effect slot {s} (slots {min(names)}-{max(names)})")
        nm = names[s]
        if nm and not _is_peq(nm):
            raise ValueError(f"master slot {s} holds {nm}: pick an empty slot or one with a {PEQ}")
        if nm and s != ours and not replace:
            raise ValueError(f"master slot {s} holds a {PEQ} fl-headless didn't put there: replace=true takes it over")
        return s, bool(nm), bool(nm) and s != ours, None
    if ours is not None:
        return ours, True, False, None
    if replace:
        s = next((s for s in sorted(names) if _is_peq(names[s])), None)
        if s is not None:
            return s, True, True, None
    dyn = min((s for s, nm in names.items() if nm and _DYN_RE.search(nm)), default=None)
    free = [s for s in sorted(names) if not names[s]]
    if not free:
        raise ValueError("no free effect slot on the master: free one, or pass slot= of a Parametric EQ 2")
    before = [s for s in free if dyn is None or s < dyn]
    s = (before or free)[0]
    warn = None if before or dyn is None else (
        f"the tone EQ is in master slot {s}, after {names[dyn]} (slot {dyn}): move it above that in FL's mixer, or "
        "its boosts can pass the limiter's ceiling")
    return s, False, False, warn


def _tone_readback(output, s, mp, want_norms):
    """Decode the master slot's state from the written file: (bands as read, mismatches)."""
    d = H["_load"](output); ev = H["_events"](d)
    first, marker, name, k = _tone_slots(d, ev).get(s, (None, None, None, None))
    if k is None or not _is_peq(name):
        return [], [f"no {PEQ} in master slot {s} of the written file"]
    vals, raw = peq_decode(H["_payload"](d, ev[k]), mp)
    lay = _tone_layout(mp); bad = []
    for key, v in want_norms.items():
        ir = lay[key][1]
        exp = int(v) if ir == "enum" else int(round(float(ir[0]) + _clip01(v) * (float(ir[1]) - float(ir[0]))))
        if raw.get(key) != exp:
            bad.append(f"{key}: {raw.get(key)} in the file, {exp} written")
    return _bands_view(vals, mp), bad


def _bands_view(vals, mp, all_bands=False):
    """Decoded PEQ2 values -> compact bands [{band, type, hz, gain_db, q}] (flat bands left out unless all_bands)."""
    out = []
    wu = str(((mp.get("controls") or {}).get("band1_width") or {}).get("unit") or "")
    for b in range(1, TONE_MAX_BANDS + 1):
        ty = vals.get(f"band{b}_type"); g = vals.get(f"band{b}_gain_db"); hz = vals.get(f"band{b}_freq_hz")
        w = vals.get(f"band{b}_width")
        kind = _TONE_OUT.get(str(ty).lower(), str(ty).lower()) if ty is not None else None
        flat = kind == "off" or (kind in ("peak", "low_shelf", "high_shelf") and isinstance(g, (int, float))
                                 and abs(g) < 0.005)
        if flat and not all_bands:
            continue
        row = {"band": b, "type": kind, "hz": _r(hz, 1), "gain_db": _r(g, 2)}
        if isinstance(w, (int, float)):
            row["q"] = _r(oct_to_q(w), 2) if wu.lower() == "oct" else None
            row["width"] = f"{_r(w, 2)} {wu}".strip()
        out.append(row)
    return out


def _tone_match(jbands, read):
    """Does every journal band have a band on the EQ (same type, within 2 % in frequency, 0.05 dB)?"""
    miss = []
    for jb in jbands or []:
        ok = any(r.get("type") == jb.get("type") and r.get("hz") and jb.get("hz") and
                 abs(math.log(float(r["hz"]) / float(jb["hz"]))) < 0.02 and
                 (jb["type"] in ("high_pass", "low_pass") or abs(float(r.get("gain_db") or 0) - float(jb.get("gain_db") or 0)) <= 0.05)
                 for r in read)
        if not ok:
            miss.append(f"{jb.get('type')} {jb.get('hz')} Hz {jb.get('gain_db'):+g} dB" if isinstance(jb.get("gain_db"), (int, float))
                        else f"{jb.get('type')} {jb.get('hz')} Hz")
    return miss


def _tone_record(output, s, bands, preset, via, layout_ok):
    """The song journal's master_tone (finals and previews read it); None-valued keys are dropped."""
    S = sys.modules.get("flh_store")
    if not (S and S.H):
        return "journal not written (flh_store isn't loaded)"
    rec = {"plugin": PEQ, "track": 0, "slot": int(s), "bands": bands, "preset": preset, "via": via,
           "file_layout": "proven on this PC's default preset (unverified map)" if layout_ok else None,
           "when": time.strftime("%Y-%m-%d %H:%M"), "by": TONE_BY}
    try:
        S.journal_update(output, set={"master_tone": None})        # the whole record is replaced, not merged
        S.journal_update(output, set={"master_tone": {k: v for k, v in rec.items() if v is not None}},
                         add={"mix_log": {"when": rec["when"], "by": TONE_BY,
                                          "done": [f"master tone: {len(bands)} band(s)" + (f" ({preset})" if preset else "")]}})
    except Exception as e:                                        # noqa: BLE001
        return f"journal not written ({type(e).__name__}: {e})"
    return None


def flp_master_tone(project, bands=None, output=None, slot=None, replace=True, preset=None):
    """File-level: put (or reuse) a Fruity Parametric EQ 2 on the master and write the tone bands into the .flp.
    Registered as a project writer (the open FL project is saved / reloaded around it; flp_edit can batch it)."""
    steps, mark = _steps()
    output = output or project
    H["writable"](output)
    want = tone_bands(bands, preset)
    mp = plugin_map(PEQ)
    if not mp or not _tone_layout(mp):
        raise ToneLayoutUnknown(f"the {PEQ} plugin map has no file_state layout (data/plugin_maps)")
    proof = _tone_default(mp)
    if not proof.get("ok"):
        raise ToneLayoutUnknown(proof.get("why") or "the saved layout couldn't be confirmed")
    mark("layout")
    d = H["_load"](project); ev = H["_events"](d)
    sl = _tone_slots(d, ev)
    if not sl:
        raise ValueError("the project has no master mixer track with effect slots")
    names = {s: v[2] for s, v in sl.items()}
    tone = _journal(project).get("master_tone") or {}
    s, reuse, took_over, warn = _tone_target(names, tone, slot, replace)
    final = want
    if not replace and reuse and not took_over:                   # our own tone EQ, replace=false: merge
        final = _tone_merge(tone.get("bands") or [], want)
    assign = tone_assign(final)
    notes = set()
    norms = {}
    for b in range(1, TONE_MAX_BANDS + 1):
        norms.update(_tone_norms(mp, b, assign.get(b), notes))
    # replace and merge both rebuild the whole EQ from FL's default state: unused bands are flat, nothing left over
    # PC-VERIFY: after the reload FL's EQ window shows exactly these bands (type, Hz, dB, width for the Q); the
    # frequency int (0..65536 on the map's log 10 Hz-22 kHz curve) and width int (0..65536 = the % bridge) are guesses
    new_state = _peq_encode(proof["state"], mp, norms)
    first, marker, _nm, k_state = sl[s]
    if reuse:
        if k_state is None:
            raise ValueError(f"master slot {s}'s {PEQ} has no saved state in the file")
        edits = [(k_state, k_state + 1, H["_data_event"](H["EV_PLUGIN_STATE"], new_state))]
    else:
        blk = b""
        for eid, p, raw in proof["blk"]:
            if eid == H["EV_PLUGIN_HEADER"]:                       # point the header at the master slot
                h = bytearray(p); h[0:8] = struct.pack("<ii", 0, s)
                blk += H["_data_event"](eid, bytes(h))
            elif eid == H["EV_PLUGIN_STATE"]:
                blk += H["_data_event"](eid, new_state)
            else:
                blk += raw
        edits = [(marker, marker, blk)]
    before = set(H["_versions"](output)) if H.get("_versions") and os.path.isfile(output) else set()
    mark("plan")
    H["_save"](H["_splice"](d, ev, edits), output)
    mark("write")
    read, bad = _tone_readback(output, s, mp, norms)
    mark("read_back")
    jerr = _tone_record(output, s, final, preset, "file", True)
    after = H["_versions"](output) if H.get("_versions") else []
    backup = next((v for v in reversed(after) if v not in before), None) or (after[-1] if after else None)
    res = {"output": output, "slot": s, "added": not reuse, "bands": final,
           "assigned": {str(b): f"{x['type']} {x['hz']:g} Hz" + (f" {x['gain_db']:+g} dB" if x["type"] not in
                        ("high_pass", "low_pass") else "") for b, x in sorted(assign.items())},
           "read_back": {"ok": not bad, "eq": read} if not bad else {"ok": False, "mismatch": bad[:6], "eq": read},
           "in_renders": "the EQ is in the project: fl_preview / fl_finalize / stems renders all include it",
           "unverified": [f"{PEQ}'s file layout and its frequency / width curves are from the shipped map, not "
                          "measured (the layout is proven only by FL's default preset reading right). PC-VERIFY: open "
                          "the EQ in FL and compare the bands; listen."],
           "steps_s": steps}
    if took_over:
        res["took_over"] = f"master slot {s}'s existing {PEQ} (replace=true): all its bands were rewritten"
    if warn:
        res["warning"] = warn
    if notes:
        res["width_note"] = sorted(notes)[0] + (f" (+{len(notes) - 1} more)" if len(notes) > 1 else "")
    if jerr:
        res["journal"] = jerr
    if backup:
        res["backup"] = backup
        res["undo"] = C.tool_call(H, "flp_restore", {"path": output, "backup": backup})
    return res


# PC-VERIFY: the live fallback sets every PEQ2 band through FL (plugin_set_named, display read-back) and saves the
# project in FL; the file then matches what FL plays.
def _master_tone_live(project, bands, slot, replace, preset, why):
    """FL has the project open but the file layout can't be confirmed: put the EQ on (one save-edit-reload when it's
    missing) and set the bands live through the plugin map, then save in FL."""
    steps, mark = _steps()
    want = tone_bands(bands, preset)
    mp = plugin_map(PEQ) or {}
    _fl_gone_check("before the master tone")
    try:
        H["_backup"](project)                                     # reversible: flp_versions has the song as it was
    except Exception:                                             # noqa: BLE001
        pass
    st = _state_for([0])
    m0 = next((m for m in st.get("mixer", []) if m.get("index") == 0), None)
    if m0 is None:
        raise RuntimeError("FL's master track isn't visible to the bridge: nothing was changed")
    names = {i: None for i in range(10)}
    for fx in m0.get("effects", []):
        names[int(fx["slot"])] = fx.get("name")
    tone = _journal(project).get("master_tone") or {}
    s, reuse, took_over, warn = _tone_target(names, tone, slot, replace)
    final = _tone_merge(tone.get("bands") or [], want) if (not replace and reuse and not took_over) else want
    if not reuse:
        _add_effects([(0, s, PEQ)]); mark("add_effect")
    assign = tone_assign(final)
    dtypes = mp.get("band_default_type") or []
    dhz = mp.get("band_defaults_hz") or []
    vals = {}
    for b in range(1, TONE_MAX_BANDS + 1):
        x = assign.get(b)
        if x is None:                                             # unused: FL's default flat band
            vals[f"band{b}_gain_db"] = 0.0
            if b - 1 < len(dtypes):
                vals[f"band{b}_type"] = dtypes[b - 1]
            if b - 1 < len(dhz):
                vals[f"band{b}_freq_hz"] = float(dhz[b - 1])
            continue
        vals[f"band{b}_type"] = TONE_TYPES[x["type"]]
        vals[f"band{b}_freq_hz"] = x["hz"]
        vals[f"band{b}_gain_db"] = x["gain_db"]
        if x.get("q"):
            vals[f"band{b}_width"] = f"{x['q']} Q"
    r = plugin_set_named(0, s, PEQ, vals); mark("set")
    prev = (H["_state"]().get("mix_engineer") or {}).get("applied", [])
    H["_state"]({"mix_engineer": {"project": project, "applied": prev + [dict(u, kind="param") for u in r["undo"]],
                                  "label": "master tone"}})
    saved = False
    try:
        saved = bool(H["_save_in_fl_and_wait"](project, 8.0))
    except Exception:                                             # noqa: BLE001
        saved = False
    mark("save")
    jerr = _tone_record(project, s, final, preset, "live", False)
    res = {"output": project, "via": "live", "why_live": why, "slot": s, "added": not reuse, "bands": final,
           "ok": r["ok"], "set": [f"{x['control']}={x['text']}" + ("" if x["ok"] else " (check)") for x in r["set"]],
           "skipped": r["skipped"] or None, "saved_in_file": saved,
           "undo": C.tool_call(H, "fl_mix_engineer", {"revert_last": True}) + " puts the knobs back; flp_versions "
                   "has the project from before", "steps_s": steps}
    if not saved:
        res["note"] = "set in FL but not saved yet: save in FL (Ctrl+S) so renders from the file have it"
    if took_over:
        res["took_over"] = f"master slot {s}'s existing {PEQ} (replace=true)"
    if warn:
        res["warning"] = warn
    if jerr:
        res["journal"] = jerr
    return res


def master_tone_read(project, layout=True):
    """What tone is on the master (every PEQ2 there, decoded from the file) and what the journal says."""
    mp = plugin_map(PEQ) or {}
    d = H["_load"](project); ev = H["_events"](d)
    eqs = []
    for s, (first, marker, name, k) in sorted(_tone_slots(d, ev).items()):
        if not _is_peq(name):
            continue
        row = {"slot": s}
        try:
            vals, _raw = peq_decode(H["_payload"](d, ev[k]), mp) if k is not None else ({}, {})
            row["bands"] = _bands_view(vals, mp)
        except Exception as e:                                    # noqa: BLE001
            row["error"] = f"{type(e).__name__}: {e}"
        eqs.append(row)
    tone = _journal(project).get("master_tone")
    res = {"project": project, "master_eqs": eqs, "journal": tone}
    if tone:
        hit = next((e for e in eqs if e["slot"] == tone.get("slot")), None)
        miss = _tone_match(tone.get("bands"), hit.get("bands") or []) if hit else None
        res["matches_journal"] = bool(hit) and not miss
        if not hit:
            res["note"] = f"the journal's tone EQ (master slot {tone.get('slot')}) isn't in the project any more"
        elif miss:
            res["note"] = "the EQ in the project differs from the journal: " + "; ".join(miss[:4])
    if layout and not _tone_default(mp).get("ok"):
        res["layout"] = "unconfirmed on this PC: the bands shown are decoded with the shipped (unverified) layout"
    return res


def master_tone_check(project):
    """For fl_finalize / previews: None when the journal records no master tone, else {ok, note?}."""
    try:
        tone = _journal(project).get("master_tone")
        if not tone:
            return None
        r = master_tone_read(project, layout=False)
        out = {"ok": bool(r.get("matches_journal")), "slot": tone.get("slot"), "bands": len(tone.get("bands") or [])}
        if r.get("note"):
            out["note"] = r["note"] + " (flp_master_tone with the journal's master_tone bands puts it back)"
        return out
    except Exception as e:                                        # noqa: BLE001 - a check never breaks a final
        return {"ok": None, "note": f"master tone not checked ({type(e).__name__}: {e})"}


def master_tone(project=None, bands=None, output=None, slot=None, replace=True, preset=None):
    """flp_master_tone the tool: no bands / preset = read; else write into the file (guarded: FL saves / reloads
    around it) or, when the file layout can't be confirmed on this PC and FL has the project open, live."""
    if not project:
        project = H["_open_project_guess"]()[0]
        if not project:
            raise ValueError("give the project (.flp)")
    if bands is None and not preset:
        return master_tone_read(project)
    tone_bands(bands, preset)                                     # bad input fails before FL is touched
    mp = plugin_map(PEQ) or {}
    proof = _tone_default(mp) if _tone_layout(mp) else {"ok": False, "why": f"the {PEQ} map has no file_state layout"}
    same = not output or os.path.normcase(os.path.abspath(output)) == os.path.normcase(os.path.abspath(project))
    if not proof.get("ok"):
        if same and H["fl_running"]() and H["_project_is_open"](project):
            return _master_tone_live(project, bands, slot, replace, preset, proof.get("why"))
        raise ToneLayoutUnknown(f"{proof.get('why')}. Nothing was changed: open the project in FL and call again "
                                "(the bands are then set live through FL and saved)")
    guarded = H["_guard_open_project"](flp_master_tone, "project", "output")
    return guarded(project=project, bands=bands, output=output or project, slot=slot, replace=replace, preset=preset)


master_tone.__wrapped__ = flp_master_tone


# ============================================================================= registration
def register(ns):
    global H
    H = ns
    P, Sch, STR, BOOL, NUM, INT = ns["_p"], ns["_schema"], ns["_STR"], ns["_BOOL"], ns["_NUM"], ns["_INT"]
    ns.update({"plugin_map": plugin_map, "plugin_maps": plugin_maps, "plugin_set_named": plugin_set_named,
               "plugin_get_named": plugin_get_named, "masking": masking, "reference_match": reference_match,
               "intent_verify": intent_verify, "mix_engineer": mix_engineer, "mix_propose": propose,
               "mix_apply_ops": apply_ops, "platform_preview": platform_preview, "mix_features": features,
               "mix_metrics": metrics, "plugin_maps_check": map_names_check, "room_plan": room_plan, "room_family": room_family, "mix_room": room,
               "insert_gain": insert_gain, "mix_wait_fl_ready": _wait_fl_ready})
    guarded = ns["_guard_open_project"](flp_scale_automation, "project", "output")

    def scale_tool(project, output=None, clips=None, db=0.0):
        return guarded(project=project, output=output or project, clips=clips, db=db)
    scale_tool.__wrapped__ = flp_scale_automation
    ns["flp_scale_automation"] = scale_tool
    ns["_OPEN_PROJECT_GUARD"]["flp_scale_automation"] = ("project", "output")
    ns["TOOLS"]["flp_scale_automation"] = (
        scale_tool, "Make a track louder/quieter when an automation clip drives its mixer fader (a "
        "fader set is overridden by the clip): moves every point of the clip(s) by db on FL's fader curve. Only clips "
        "that automate a mixer track's volume; points past the fader's ends are clipped (counted). The open FL "
        "project is saved and reloaded around it. fl_mixer_set(..., if_automated='scale_clip') does this for you.",
        Sch(project=ns["_FLP"], output=STR("where to save (default: the project itself)", True),
            clips=P("array", "automation clip channels: index or name", items={"type": ["integer", "string"]}),
            db=NUM("dB to move every point by (+ louder, - quieter)")))
    ns.update({"master_tone": master_tone, "master_tone_read": master_tone_read, "master_tone_check": master_tone_check,
               "peq_decode": peq_decode, "tone_presets": tone_presets})
    ns["_OPEN_PROJECT_GUARD"]["flp_master_tone"] = ("project", "output")
    band_item = {"type": "object", "properties": {
        "type": {"type": "string", "enum": list(TONE_TYPES)}, "hz": {"type": "number"},
        "gain_db": {"type": "number"}, "q": {"type": "number"}}, "required": ["type", "hz"]}
    ns["TOOLS"]["flp_master_tone"] = (
        master_tone, "The song's tone curve IN the project: a Fruity Parametric EQ 2 on the master (added, or the one "
        "fl-headless put there reused; replace=true also takes over one already there) set to `bands`, written into "
        "the .flp through the plugin map (FL open: saved/reloaded around it; flp_edit can batch it). Every render "
        "(fl_preview, fl_finalize, stems) then has it - no offline EQ after rendering. Recorded in the song journal "
        "(master_tone); backup in flp_versions. preset='clean'|'warm'|'bright' (data/mixeng/master_tone.json). No "
        "bands/preset: shows the master's EQ and whether it matches the journal.",
        Sch(project=ns["_FLP"], bands=P("array", "[{type: peak|low_shelf|high_shelf|high_pass|low_pass, hz, gain_db, "
                                                  "q}] (max 7)", True, items=band_item),
            output=STR("where to save (default: the project itself)", True),
            slot=INT("master effect slot (default: the tone EQ already there, else the first free one before any "
                     "limiter)", True),
            replace=BOOL("true (default): the EQ holds exactly these bands, an existing Parametric EQ 2 on the master "
                         "is taken over; false: merged into fl-headless's own tone EQ, a user's EQ never touched", True),
            preset=STR("clean | warm | bright (or one from your state folder's master_tone.json)", True)))
    arr = lambda d, it=None: P("array", d, True, items=it or {})
    ns["TOOLS"]["fl_plugin_map_learn"] = (
        fl_plugin_map_learn, "Learn a stock plugin's control map on this PC (live, FL open): reads every parameter's "
        "name, probes the knob at `points` positions and records the displayed value/unit (dB, Hz, ms, ratio, "
        "options), restores the knobs, and saves the map to fl-headless's state folder where it overrides the shipped "
        "(unverified) map. Needed once per plugin before trusting named control setting fully.",
        Sch(track=INT("mixer track (or channel index for an instrument)"), slot=INT("effect slot (-1 = instrument)"),
            points=INT("knob positions per parameter (default 17)", True),
            params=STR("only parameters whose name contains this", True),
            background=BOOL("run as a background job (default: when long)", True)))
    if isinstance(ns.get("TOOL_GROUPS"), dict):
        ns["TOOL_GROUPS"]["fl_plugin_maps_check"] = "plugins_live"
    ns["TOOLS"]["fl_plugin_maps_check"] = (
        fl_plugin_maps_check, "Check the plugin maps' FL names against the live effects (read only): each named "
        "control is matched to the live parameter it means (exact, then same words/synonyms) and apply=true saves "
        "corrected user maps. Reports matched/renamed/unmatched; not loaded: run fl_pc_check mix.map_names.",
        Sch(tracks=arr("mixer tracks to scan (default: all fl_state lists)", {"type": "integer"}),
            apply=BOOL("save corrected user maps (default true)", True),
            full=BOOL("include a diff for data/plugin_maps", True)))
    ns["TOOLS"]["fl_plugin_set_named"] = (
        fl_plugin_set_named, "Set a live plugin's controls by name with real units, e.g. Fruity Parametric EQ 2 "
        "{band2_freq_hz: 3000, band2_gain_db: -2.5, band2_type: 'Peaking'} or Fruity Compressor {ratio: 3, attack_ms: "
        "10}. Matches each control to the FL parameter by name and checks FL's display afterwards (ok per control).",
        Sch(track=INT("mixer track"), slot=INT("effect slot"), values=P("object", "{control: value}"),
            plugin=STR("plugin name (default: what's in the slot)", True)))
    ns["TOOLS"]["fl_masking"] = (
        fl_masking, "Masking detector: which tracks fight in the same 1/3-octave bands per section, with masking of the "
        "lead/vocal ranked first ('Pad masks Lead at 1.0-3.2 kHz in Chorus (overlap 0.72)') and fixes (EQ dip with "
        "frequency/Q/gain on the masker, sidechain/dynamic dip, or an arrangement move). Input: rendered stems "
        "(fl_render_stems folder; precise) or live=true (each mixer track heard alone in the open FL; one passage).",
        Sch(stems=P(["string", "array"], "stems folder or files (WAV/FLAC)", True),
            sections=arr("[{name, start_s, end_s} | {name, bar, end_bar}] (default: the project's sections)",
                         {"type": "object"}),
            project=STR("the .flp (sections + tempo)", True), lead=STR("which track is the lead (default: by name)", True),
            live=BOOL("measure live instead of stems", True), tracks=arr("live: mixer tracks; stems: names"),
            start_bar=NUM("live: passage start bar", True), end_bar=NUM("live: passage end bar", True),
            seconds=NUM("live: passage length", True), section=STR("only this section (name)", True),
            top=INT("max findings (default 8)", True), background=BOOL("background job", True)))
    ns["TOOLS"]["fl_reference_match"] = (
        fl_reference_match, "Compare a render (or a live take) with the user's own reference song (WAV/FLAC): loudness "
        "and short-term curve over normalized time, section energy, tonal balance (1/3 octaves, tilt), stereo width "
        "per band, crest / peak-to-loudness, low-end share. Ranked differences + concrete moves (master EQ shelves/"
        "bells with gains, width, compression, finish target); apply=true puts the EQ moves on the master's "
        "Parametric EQ 2 (undo: fl_mix_engineer revert_last=true).",
        Sch(reference=STR("reference song: WAV/FLAC/AIFF, MP3/M4A on Windows"), mix=STR("your render (WAV/FLAC)", True),
            sections=arr("your song's sections (seconds or bars with project)", {"type": "object"}),
            project=STR("the .flp (for sections)", True), apply=BOOL("apply the master EQ moves live", True),
            full=BOOL("include the 1/3-octave difference table", True),
            live_seconds=NUM("no mix: record this many seconds from the open FL", True),
            background=BOOL("background job", True)))
    ns["TOOLS"]["fl_verify_intent"] = (
        fl_verify_intent, "Did an edit do what was asked? Before/after audio (or measurement dicts) against intents "
        "(darker, brighter, warmer, punchier, wider, narrower, louder, quieter, less_muddy, less_harsh, clearer, "
        "more_bass, less_bass, tighter, cleaner, more_compressed, ... or plain words): 'achieved: high end -2.1 dB' or "
        "'barely changed (+0.2 dB): want more?'.",
        Sch(before=P(["string", "object"], "audio path or measurement"), after=P(["string", "object"], "audio path or "
            "measurement"), intents=P(["array", "string"], "intent names, [{intent, min}] or text"),
            detail=BOOL("include every metric checked", True)))
    ns["TOOLS"]["fl_mix_engineer"] = (
        fl_mix_engineer, "Mix engineer: measures (live per-track solo pass in the open FL, rendered stems, or given "
        "measurements) and proposes prioritized moves like an engineer, genre-aware: gain staging (lead on top), "
        "high-passes, mud cuts, unmasking dips (stems), lead presence, de-harsh, compression from crest (release from "
        "tempo), reverb/delay sized to BPM, stereo placement, bus glue, master/finish target. apply=true runs the "
        "applicable moves in one undoable batch (faders/pans live, EQ/compressor via plugin maps; missing stock "
        "effects added in one project edit with a backup), logs them in the song's journal, re-measures and verifies "
        "the goal. revert_last=true undoes.",
        Sch(goal=STR("what to achieve, e.g. 'vocal clearer, less mud'", True), section=STR("section name to measure", True),
            tracks=arr("mixer tracks to measure (live)"), apply=BOOL("apply the moves", True),
            genre=STR("genre/style (default: journal/profile)", True), stems=P(["string", "array"], "stems folder/files", True),
            measurements=P("object", "{tracks: [{name, track, lufs, bands_db, crest_db, pan}], mix: {...}}", True),
            project=STR("the .flp", True), bpm=NUM("tempo (default: FL / project)", True),
            lead=STR("lead track name", True), ids=arr("apply only these proposal ids"),
            start_bar=NUM("live passage start", True), end_bar=NUM("live passage end", True),
            seconds=NUM("live passage seconds", True), revert_last=BOOL("undo the applied moves", True),
            background=BOOL("background job", True)))
    ns["TOOLS"]["fl_room"] = (
        fl_room, "One room: put the song's acoustic layers (piano, strings, choir, pads, brass, woodwinds...) in one "
        "shared recorded space, like a real recording. Builds a shared reverb (Fruity Reeverb 2, or Fruity Convolver "
        "where your edition owns it and its map is learned) on a free mixer track, plus an early-reflection bus in "
        "hall/chamber; pre-delay and decay from tempo and style; sends by stage distance per instrument family "
        "(front: piano/soloists drier, mid: strings/choir/pads, back: brass/timpani wetter), a gentle high-shelf cut on "
        "far tracks, classical seating pans for orchestral layers (only tracks still centered). Genre-aware (pop: "
        "subtle; EDM/hip-hop: none unless asked). Proposal by default; apply=true runs it in one undoable batch "
        "(journal record; revert_last=true undoes) and re-measures.",
        Sch(project=STR("the .flp (default: the open one)", True),
            style=P("string", "hall | chamber | studio | auto (from genre) | none", True,
                    enum=["hall", "chamber", "studio", "auto", "none"]),
            apply=BOOL("build it (FL open)", True),
            tracks=P(["array", "object"], "only these mixer tracks, or {track: family} (piano, strings, choir, brass, "
                     "woodwind, pad, bells, timpani, vocal, lead, guitar, pluck, organ)", True),
            genre=STR("genre (default: journal/profile)", True), bpm=NUM("tempo (default: FL / project)", True),
            reverb=STR("auto | reeverb | convolver", True), verify=BOOL("measure before/after (plays FL)", True),
            seconds=NUM("verify window seconds (default 8)", True),
            revert_last=BOOL("undo the applied moves", True), background=BOOL("background job", True)))
    ns["TOOLS"]["fl_platform_preview"] = (
        fl_platform_preview, "How a master will play on Spotify, Apple Music, YouTube, TIDAL, Amazon, Deezer, "
        "SoundCloud: playback gain from loudness normalization, whether it gets turned down/up, true-peak risk after "
        "lossy encoding, one-line advice. Values dated, marked documented vs approximate.",
        Sch(audio=STR("WAV/FLAC master", True), lufs=NUM("or: integrated LUFS", True),
            true_peak=NUM("and true peak dBTP", True), platforms=arr("only these (keys or names)", {"type": "string"})))
