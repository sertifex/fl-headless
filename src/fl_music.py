"""Music theory and note operations for fl-headless (pure Python, no FL needed).

Notes are dicts: {"pos": ticks, "len": ticks, "key": MIDI note, "vel": 0-127, "ch": channel, ...}; other keys are
carried through untouched. All operations take a list and return a new list.
"""
import math
import random

NAMES = "C C# D Eb E F F# G Ab A Bb B".split()
LETTER = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10],
    "harmonic minor": [0, 2, 3, 5, 7, 8, 11], "melodic minor": [0, 2, 3, 5, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10], "phrygian": [0, 1, 3, 5, 7, 8, 10], "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10], "locrian": [0, 1, 3, 5, 6, 8, 10],
    "major pentatonic": [0, 2, 4, 7, 9], "minor pentatonic": [0, 3, 5, 7, 10], "blues": [0, 3, 5, 6, 7, 10],
}
ALIASES = {"maj": "major", "ionian": "major", "m": "minor", "min": "minor", "aeolian": "minor",
           "harmonic": "harmonic minor", "melodic": "melodic minor", "pentatonic": "major pentatonic"}

# Krumhansl-Kessler key profiles
KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

CHORDS = {"": [0, 4, 7], "m": [0, 3, 7], "dim": [0, 3, 6], "aug": [0, 4, 8], "sus2": [0, 2, 7], "sus4": [0, 5, 7],
          "7": [0, 4, 7, 10], "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "m7b5": [0, 3, 6, 10], "dim7": [0, 3, 6, 9],
          "add9": [0, 2, 4, 7], "madd9": [0, 2, 3, 7], "6": [0, 4, 7, 9], "m6": [0, 3, 7, 9], "5": [0, 7]}


def parse_note(s):
    """'C4', 'F#3', 'Bb5' or a MIDI number -> MIDI number (C4 = 60)."""
    if isinstance(s, int):
        return s
    if isinstance(s, float) and s.is_integer():                   # a JSON number sent as 67.0
        return int(s)
    s = str(s).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    letter = s[:1].upper(); rest = s[1:]; acc = 0
    while rest and rest[0] in "#b":
        acc += 1 if rest[0] == "#" else -1; rest = rest[1:]
    if letter not in LETTER or not rest.lstrip("-").isdigit():
        raise ValueError(f"note '{s}': use a MIDI number or a name with its octave like C4 (middle C), F#3, Eb5")
    return 12 * (int(rest) + 1) + LETTER[letter] + acc


def note_name(k):
    return NAMES[k % 12] + str(k // 12 - 1)


def parse_key(s):
    """'G minor', 'Gm', 'F# dorian', 'Bb' -> (root pitch class, scale name, intervals)."""
    s = str(s).strip()
    letter = s[0].upper(); rest = s[1:]; acc = 0
    while rest and rest[0] in "#b":
        acc += 1 if rest[0] == "#" else -1; rest = rest[1:]
    mode = rest.strip().lower() or "major"
    mode = ALIASES.get(mode, mode)
    if mode not in SCALES:
        raise ValueError(f"unknown scale '{mode}' (known: {', '.join(SCALES)})")
    return (LETTER[letter] + acc) % 12, mode, SCALES[mode]


def scale_set(key):
    root, _, iv = parse_key(key)
    return {(root + i) % 12 for i in iv}


def detect_key(notes, top=3):
    """Krumhansl-Schmuckler key finding, weighted by duration. -> [(key name, correlation)]"""
    w = [0.0] * 12
    for n in notes:
        w[n["key"] % 12] += max(1, n["len"])
    if not any(w):
        return []
    def corr(a, b):
        ma, mb = sum(a) / 12, sum(b) / 12
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
        return num / den if den else 0.0
    res = []
    for r in range(12):
        res.append((f"{NAMES[r]} major", corr(w, KK_MAJOR[-r:] + KK_MAJOR[:-r])))
        res.append((f"{NAMES[r]} minor", corr(w, KK_MINOR[-r:] + KK_MINOR[:-r])))
    res.sort(key=lambda x: -x[1])
    return [(k, round(c, 3)) for k, c in res[:top]]


def name_chord(weights, bass=None):
    """Best chord for 12 pitch-class weights -> name like 'Gm', 'Ebmaj7', 'D/F#' (or None)."""
    total = sum(weights)
    if total <= 0:
        return None
    best = None
    for r in range(12):
        for q, iv in CHORDS.items():
            pcs = [(r + i) % 12 for i in iv]
            inside = sum(weights[p] for p in pcs) / total
            missing = sum(1 for p in pcs if weights[p] == 0) / len(pcs)
            score = inside - 0.5 * missing - 0.02 * len(iv) + (0.05 if bass == r else 0)
            if best is None or score > best[0]:
                best = (score, r, q)
    if best[0] < 0.4:
        return None
    _, r, q = best
    name = NAMES[r] + q
    if bass is not None and bass != r and bass in [(r + i) % 12 for i in CHORDS[q]]:
        name += "/" + NAMES[bass]
    return name


def chords_by_segment(notes, seg):
    """[(segment start tick, chord name)] for segments of `seg` ticks."""
    if not notes:
        return []
    end = max(n["pos"] + n["len"] for n in notes); out = []
    for s in range(0, end, seg):
        w = [0.0] * 12; low = None
        for n in notes:
            a, b = max(n["pos"], s), min(n["pos"] + n["len"], s + seg)
            if b > a:
                w[n["key"] % 12] += b - a
                if low is None or n["key"] < low:
                    low = n["key"]
        out.append((s, name_chord(w, low % 12 if low is not None else None)))
    return out


def _groups(notes, tol=2):
    """Chords: notes starting together (within tol ticks), per channel."""
    out = []
    for n in sorted(notes, key=lambda n: (n["ch"], n["pos"], n["key"])):
        if out and out[-1][0]["ch"] == n["ch"] and abs(out[-1][0]["pos"] - n["pos"]) <= tol:
            out[-1].append(n)
        else:
            out.append([n])
    return out


def _meter_list(m):
    """'7/8' | [7, 8] | [[tick, 7, 8], ...] -> [(tick, num, den)] sorted (None -> None)."""
    if not m:
        return None
    if isinstance(m, str):
        a, b = m.split("/")
        return [(0, int(a), int(b))]
    if isinstance(m, (list, tuple)) and len(m) == 2 and all(isinstance(x, (int, float)) for x in m):
        return [(0, int(m[0]), int(m[1]))]
    return sorted((int(t), int(a), int(b)) for t, a, b in m)


def _pulse(num, den, ppq, bar):
    """The felt beat in ticks: dotted quarters in 6/8 9/8 12/8, the bar in 5/8 7/8, halves in x/2 (= flh_perform)."""
    if den == 8:
        return ppq * 3 // 2 if num % 3 == 0 else bar
    if den == 16:
        return ppq * 3 // 4 if num % 3 == 0 else ppq
    if den == 2:
        return ppq * 2
    return ppq


def _swing_slot(pos, g, meters, ppq):
    """(start of the g-pair holding pos, or None when pos is in no pair: a compound beat or an odd bar's last
    unit). Pairs count from each felt beat of each bar (meters [(tick, num, den)]; none = 4/4 from tick 0)."""
    t0, num, den = 0, 4, 4
    for mt in meters or ():
        if mt[0] <= pos:
            t0, num, den = mt
    bar = int(num * 4 * ppq // den)
    bs = t0 + ((pos - t0) // bar) * bar
    pl = _pulse(num, den, ppq, bar)
    if pl % (2 * g) and pl != bar:                         # 6/8: three 8ths a beat, no 8th pairs
        return None
    ps = bs + ((pos - bs) // pl) * pl
    pe = min(ps + pl, bs + bar)
    base = ps + ((pos - ps) // (2 * g)) * 2 * g
    return base if base + 2 * g <= pe else None


def apply(notes, op, ppq, tempo_bpm=120.0, meters=None):
    """Apply one operation dict to the notes. -> new list. meters [(tick, num, den)] (or op "meter": '7/8'):
    the time signature the notes are in, so swing pairs 8ths inside each beat of each bar."""
    kind = op.get("op")
    beat = ppq
    def grid(v, default="1/16"):
        v = v or default
        if isinstance(v, (int, float)):
            return int(v)
        num, den = str(v).split("/")
        trip = den.endswith("t")
        ticks = 4 * ppq * int(num) / int(den.rstrip("t"))
        return int(round(ticks * (2 / 3 if trip else 1)))
    def ms(v):
        return int(round(float(v) / 1000 * tempo_bpm / 60 * ppq))
    notes = [dict(n) for n in notes]
    if kind == "quantize":
        g = grid(op.get("grid")); st = float(op.get("strength", 1.0))
        def snap(x):                                      # half a grid rounds up (later), always
            return int(math.floor(x / g + 0.5)) * g
        for n in notes:
            target = snap(n["pos"]); n["pos"] = int(round(n["pos"] + (target - n["pos"]) * st))
            if op.get("ends"):                            # at least one grid step long after the start's snap
                end = n["pos"] + n["len"]; tend = max(n["pos"] + g, snap(end))
                n["len"] = max(1, int(round(end + (tend - end) * st)) - n["pos"])
    elif kind == "humanize":
        rnd = random.Random(op.get("seed", 1)); t = ms(op.get("timing_ms", 12)); v = int(op.get("velocity", 8))
        for n in notes:
            n["pos"] = max(0, n["pos"] + int(rnd.gauss(0, t / 2)))
            n["vel"] = max(1, min(127, n["vel"] + int(rnd.gauss(0, v / 2))))
    elif kind == "transpose":
        d = int(op.get("semitones", 0)) + 12 * int(op.get("octaves", 0))
        for n in notes:
            n["key"] = max(0, min(127, n["key"] + d))
    elif kind == "fix_scale":
        ok = scale_set(op["key"])
        for n in notes:
            if n["key"] % 12 not in ok:
                down, up = n["key"] - 1, n["key"] + 1
                while down % 12 not in ok:
                    down -= 1
                while up % 12 not in ok:
                    up += 1
                n["key"] = down if (n["key"] - down) <= (up - n["key"]) else up
    elif kind == "velocity":
        if not notes:
            return notes
        a, b = min(n["pos"] for n in notes), max(n["pos"] for n in notes)
        for n in notes:
            if "set" in op:
                v = op["set"]
            elif "ramp" in op:
                lo, hi = op["ramp"]; v = lo + (hi - lo) * ((n["pos"] - a) / (b - a) if b > a else 0)
            else:
                v = n["vel"] * float(op.get("scale", 1)) + float(op.get("add", 0))
            n["vel"] = max(1, min(127, int(round(v))))
    elif kind == "legato":
        gap = int(op.get("gap_ticks", 0))
        for ch in {n["ch"] for n in notes}:
            mine = sorted([n for n in notes if n["ch"] == ch], key=lambda n: n["pos"])
            onsets = sorted({n["pos"] for n in mine})
            for n in mine:
                nxt = next((o for o in onsets if o > n["pos"]), None)
                if nxt is not None:
                    n["len"] = max(1, nxt - n["pos"] - gap)
    elif kind == "staccato":
        L = grid(op.get("length"), "1/16")
        for n in notes:
            n["len"] = min(n["len"], L)
    elif kind == "strum":
        spread = ms(op.get("ms", 25)); down = op.get("direction", "down") == "down"
        for grp in _groups(notes):
            grp.sort(key=lambda n: n["key"], reverse=not down)
            for i, n in enumerate(grp):
                n["pos"] += i * spread; n["len"] = max(1, n["len"] - i * spread)
    elif kind == "arpeggiate":
        rate = grid(op.get("rate"), "1/16"); mode = op.get("mode", "up"); gate = float(op.get("gate", 0.9))
        rnd = random.Random(op.get("seed", 1)); out = []
        for grp in _groups(notes):
            keys = sorted(n["key"] for n in grp)
            if mode == "down":
                seq = keys[::-1]
            elif mode == "updown":
                seq = keys + keys[-2:0:-1]
            else:
                seq = keys
            start, dur = grp[0]["pos"], max(n["len"] for n in grp); t = 0; i = 0
            while t < dur:
                k = rnd.choice(keys) if mode == "random" else seq[i % len(seq)]
                src = next(n for n in grp if n["key"] == k)
                out.append(dict(src, pos=start + t, len=max(1, int(min(rate, dur - t) * gate))))
                t += rate; i += 1
        notes = out
    elif kind == "swing":
        g = grid(op.get("grid"), "1/8"); amt = float(op.get("amount", 0.6))
        shift = int(round((amt * 2 - 1) * g))
        mts = _meter_list(op.get("meter")) or _meter_list(meters)
        for n in notes:
            base = _swing_slot(n["pos"], g, mts, ppq)
            if base is not None and 0 <= n["pos"] - (base + g) < g / 4:    # the off-beat of its pair
                n["pos"] += shift
    elif kind == "chop":
        g = grid(op.get("grid"), "1/16"); out = []
        for n in notes:
            t = 0
            while t < n["len"]:
                out.append(dict(n, pos=n["pos"] + t, len=max(1, min(g, n["len"] - t) - 1))); t += g
        notes = out
    elif kind == "octave":
        d = 12 * int(op.get("octaves", 1))
        extra = [dict(n, key=n["key"] + d, vel=max(1, int(n["vel"] * float(op.get("velocity_scale", 0.85)))))
                 for n in notes if 0 <= n["key"] + d <= 127]
        notes = notes + extra if op.get("double", True) else extra
    elif kind == "harmonize":
        root, _, iv = parse_key(op["key"]); steps = int(op.get("steps", 2)) * (1 if op.get("above", True) else -1)
        sc = sorted({(root + i) % 12 for i in iv}); extra = []
        for n in notes:
            pc = n["key"] % 12
            if pc not in sc:
                continue
            idx = sc.index(pc); j = idx + steps
            new = n["key"] - pc + sc[j % len(sc)] + 12 * (j // len(sc))
            if 0 <= new <= 127:
                extra.append(dict(n, key=new, vel=max(1, int(n["vel"] * float(op.get("velocity_scale", 0.85))))))
        notes = notes + extra
    elif kind == "invert":
        times = int(op.get("times", 1))
        for grp in _groups(notes):
            if len(grp) < 2:
                continue
            for _ in range(abs(times)):
                grp.sort(key=lambda n: n["key"])
                if times > 0:
                    grp[0]["key"] += 12
                else:
                    grp[-1]["key"] -= 12
    elif kind == "dedupe":
        seen = set(); out = []
        for n in sorted(notes, key=lambda n: (n["pos"], n["key"], n["ch"], -n["len"])):
            k = (n["pos"], n["key"], n["ch"])
            if k not in seen:
                seen.add(k); out.append(n)
        notes = out
    elif kind == "delete":
        return []
    elif kind == "tighten":
        # notes that start together (within `window`, any channel) move toward their shared beat: the grid line
        # nearest the earliest of them. Runs/arpeggios (further apart than the window) are left alone.
        free = str(op.get("grid", "")).lower() in ("none", "off", "free")
        g = grid(None if free else op.get("grid")); st = float(op.get("strength", 0.75))
        win = ms(op["window_ms"]) if op.get("window_ms") is not None else \
            (grid(op["window"]) if op.get("window") is not None else max(1, g * 2 // 3))
        order = sorted(range(len(notes)), key=lambda i: notes[i]["pos"]); groups = []
        for i in order:
            if groups and notes[i]["pos"] - notes[groups[-1][0]]["pos"] <= win:
                groups[-1].append(i)
            else:
                groups.append([i])
        for grp in groups:
            if len(grp) < 2:
                continue
            if str(op.get("grid", "")).lower() in ("none", "off", "free"):
                anchor = notes[grp[0]]["pos"]                      # rubato baked into the notes: no grid
            else:
                anchor = round(notes[grp[0]]["pos"] / g) * g
            for i in grp:
                n = notes[i]; end = n["pos"] + n["len"]
                n["pos"] = max(0, int(round(n["pos"] + (anchor - n["pos"]) * st)))
                if not op.get("move_ends"):
                    n["len"] = max(1, end - n["pos"])
        last = {}                                                 # a note moved earlier must not overlap the
        for n in sorted(notes, key=lambda n: n["pos"]):           # previous one on the same key
            k = (n.get("ch"), n["key"]); p = last.get(k)
            if p is not None and p["pos"] + p["len"] > n["pos"] - 2 and p["pos"] < n["pos"]:
                p["len"] = max(1, n["pos"] - 2 - p["pos"])
            last[k] = n
    else:
        raise ValueError(f"unknown op '{kind}'")
    return notes


OPS = ["quantize", "tighten", "humanize", "transpose", "fix_scale", "velocity", "legato", "staccato", "strum", "arpeggiate",
       "swing", "chop", "octave", "harmonize", "invert", "dedupe", "delete"]


# ----------------------------------------------------------------------------- drum patterns
# 16 steps per bar. x = accent, o = hit, g = ghost, r = roll (two 1/32 hits), . = rest
DRUMS = {
    "house":   {"kick": "o...o...o...o...", "clap": "....o.......o...", "hat": "..o...o...o...o.",
                "openhat": "..o...o...o...o.", "perc": "...o......o..o.."},
    "techno":  {"kick": "x...o...o...o...", "clap": "....o.......o...", "hat": "oooooooooooooooo",
                "openhat": "..o...o...o...o.", "perc": "......o.......o."},
    "trap":    {"kick": "o.....o...o.....", "snare": "........x.......", "hat": "o.o.o.o.o.o.r.o.",
                "openhat": "..............o.", "perc": "...o.......o...."},
    "boombap": {"kick": "o......o..o.....", "snare": "....x.......x...", "hat": "o.o.o.o.o.o.o.o.",
                "openhat": "..............o.", "perc": "................"},
    "lofi":    {"kick": "o.....o...o..o..", "snare": "....x.......x...", "hat": "o.o.o.o.o.o.o.o.",
                "openhat": "................", "perc": "..........o....."},
    "dnb":     {"kick": "o.........o.....", "snare": "....x.......x...", "hat": "o.o.o.o.o.o.o.o.",
                "openhat": "..o.............", "perc": ".......o......o."},
    "rock":    {"kick": "o.......o.o.....", "snare": "....x.......x...", "hat": "o.o.o.o.o.o.o.o.",
                "openhat": "..............o.", "perc": "................"},
    "pop":     {"kick": "o.....o.o.......", "snare": "....x.......x...", "hat": "o.o.o.o.o.o.o.o.",
                "openhat": "......o.........", "clap": "....o.......o..."},
    "reggaeton": {"kick": "o...o...o...o...", "snare": "...o..o....o..o.", "hat": "o.o.o.o.o.o.o.o.",
                  "openhat": "................", "perc": "................"},
    "afrobeats": {"kick": "o.....o...o.....", "snare": "...o......o.....", "hat": "o.oo.oo.o.oo.oo.",
                  "openhat": "........o.......", "perc": "..o..o..o..o.o.."},
}
STYLE_DEFAULTS = {"trap": {"swing": 0.5}, "boombap": {"swing": 0.58}, "lofi": {"swing": 0.62},
                  "afrobeats": {"swing": 0.55}}
FILLS = {"snare": "............xoxx", "kick": "o.......o.o...oo", "hat": "o.o.o.o.o.o.rrrr"}


def drum_notes(style, bars, ppq, channels, ghost=True, variation=True, key=60, seed=1):
    """Notes for a drum groove. channels: {"kick": ch, "snare": ch, ...} (parts without a channel are skipped)."""
    if style not in DRUMS:
        raise ValueError(f"style must be one of {', '.join(DRUMS)}")
    rnd = random.Random(seed); step = ppq // 4; out = []
    base = DRUMS[style]
    for b in range(int(bars)):
        fill_bar = variation and int(bars) >= 4 and (b + 1) % 4 == 0
        for part, ch in channels.items():
            if ch is None or part not in base:
                continue
            pat = base[part]
            if fill_bar and part in FILLS:
                pat = pat[:12] + FILLS[part][12:]
            elif variation and b % 2 == 1 and part == "kick":
                pat = pat[:14] + ("o." if pat[14] == "." else "..")
            if ghost and part == "snare" and style in ("boombap", "lofi", "rock", "pop", "dnb"):
                pat = "".join("g" if c == "." and i in (7, 10, 15) and rnd.random() < 0.6 else c
                              for i, c in enumerate(pat))
            for i, c in enumerate(pat):
                if c == ".":
                    continue
                vel = {"x": 112, "o": 96, "g": 42, "r": 84}[c] + rnd.randint(-6, 6)
                if part == "hat":
                    vel -= 0 if i % 4 == 0 else 14
                pos = (b * 16 + i) * step
                hits = [(pos, step // 2), (pos + step // 2, step // 2)] if c == "r" else [(pos, step)]
                for p, L in hits:
                    out.append({"pos": p, "len": max(1, L - 1), "key": key, "vel": max(1, min(127, vel)), "ch": ch})
    return out
