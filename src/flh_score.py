"""fl-headless score: the "musical eyes". Claude can't hear, so this turns a project (or a MIDI file) into compact,
accurate musical facts instead of raw note lists.

score_view (tool flp_score, ns["score_view"]): key / mode with confidence and modulations per section, tempo and
meter, chords per bar (or half bar) with roman numerals, per part: role, register, density, rhythm, velocity;
melody contour, repeats, per-section energy and layers; ASCII piano roll of one part on request.
clashes (tool flp_clashes, ns["clashes"]): note-level problems between layers (minor 2nd / major 7th / tritone rubs,
melody against the chord, bass against the chord, muddy low voicings, crowded registers, support layers above the
melody, notes outside an instrument's range, kick/bass pitch fights, bass/melody octaves as info), ranked, each with a
concrete fix that names the tool. Tolerance per genre (data/score/rules.json): strict / normal / groove (normal harmony +
idiomatic bass motion: root-fifth, octaves, chromatic approach and walking passing notes) / jazz (extensions too).
FL slide notes are read as bends of the held note (apply_slides), not as extra notes.
entrances (ns["score_entrances"]): layers that come in much louder than they continue, with no ramp (fl_review).
compare (tool flp_compare_midi, ns["compare_midi"]): a song against the MIDI it came from - per-region bar map, missing
source notes, new notes, repeated stand-ins, near-duplicate strikes, timing / velocity / tempo; restore / remove write
back (thresholds: data/score/compare.json).

Data, not code: data/instrument_ranges.json (sounding ranges, C4 = 60) and data/score/rules.json (low interval
limits, chord vocabulary, tolerance per genre, thresholds). Users override both with files of the same name in
<state folder>/score/.

Follows the extension contract in flh_store.py: register(ns) keeps the server namespace in H; server functions are
looked up in H at call time.
"""
import bisect
import heapq
import json
import math
import os
import re
import struct
import time

import fl_music as FM
import flh_store as S

H = {}
HERE = os.path.dirname(os.path.abspath(__file__))
POS, LEN, CH, KEY, VEL, PID, PPOS = range(7)
NAMES = FM.NAMES
_DATA = {}
_CACHE = {}
SEV = {"high": 0, "med": 1, "low": 2, "info": 3}
MAX_BARS = 20000                                                 # longer than any real song (~11 h at 120 BPM)
PARTS_GROUP = 20                                                 # score_view text: more parts than this -> by role
ROLE_WORDS = [("drums", r"drum|kick|snare|hat|perc|beat|cymbal|clap|\btoms?\b|rhythm section"),
              ("bass", r"\bbass\b|\bsub\b|808|low ?end|bassline"),
              ("melody", r"melod|\blead\b|vocal|\bvox\b|topline|hook|\bsolo\b|theme|\btune\b|\bmain\b|\bstar\b"),
              ("counter", r"counter|descant|obbligato|second line|answer"),
              ("pad", r"\bpad|drone|sustain|\bbed\b|atmos|choir|wash"),
              ("arp", r"\barp|ostinato|sequence|pluck"),
              ("chords", r"chord|\bcomp|keys|rhythm guitar|stab|harmony|accomp|piano|strings|support|layer")]
NAME_ROLES = [("drums", r"\bkick|\bsnare|hi-?hat|\bhats?\b|\bclap|\bdrums?\b|\bperc\b|\bcymbal"),
              ("bass", r"^(sub ?)?bass\b|\bbass$|\bsub\b|\b808\b|bassline"),
              ("melody", r"\blead\b|\bmelody\b|\bvocals?\b|\bvox\b|\btopline\b|\bhook\b"),
              ("pad", r"\bpads?\b"), ("arp", r"\barp\b|arpeggi"), ("chords", r"\bchords?\b")]
LINE_ROLES = ("melody", "counter")
SUPPORT_ROLES = ("pad", "chords", "counter", "arp")
RHYTHM_VALUES = [(0.25, "16ths"), (1 / 3, "8th triplets"), (0.5, "8ths"), (2 / 3, "quarter triplets"),
                 (0.75, "dotted 8ths"), (1.0, "quarters"), (1.5, "dotted quarters"), (2.0, "halves"),
                 (3.0, "dotted halves"), (4.0, "whole notes")]


# ============================================================================= fix hints a model can actually call
class Raw(str):
    """A hint argument written as-is (a placeholder such as project), not quoted."""


_LISTED = {"t": 0.0, "ns": None, "names": frozenset()}


def is_listed(tool, ns=None):
    """Is `tool` in the client's tool listing right now (essential tier + extension entry points + the toolset)?
    Unknown -> False (the flh_call form always works)."""
    ns = ns if ns is not None else H
    if not ns:
        return False
    now = time.time()
    if _LISTED["ns"] is not ns or now - _LISTED["t"] > 2.0:
        names = set()
        try:
            if ns.get("listed_tools"):
                names = set(ns["listed_tools"]())
            else:
                names = set(ns.get("ESSENTIAL") or ()) | set(ns.get("ESSENTIAL_TOOLS") or ())
        except Exception:
            names = set(ns.get("ESSENTIAL") or ()) | set(ns.get("ESSENTIAL_TOOLS") or ())
        _LISTED.update(t=now, ns=ns, names=frozenset(names))
    return tool in _LISTED["names"]


def _hint_val(v, js):
    if isinstance(v, Raw):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v) if js else repr(v)
    return json.dumps(v, separators=(",", ":"))


def call_hint(tool, args=None, ns=None):
    """A call a model can make: tool(a=1, ...) when the tool is listed, else flh_call("tool", {"a": 1, ...}).
    Raw values are written bare; a Raw value equal to its key (project) is positional in the direct form."""
    args = args or {}
    if is_listed(tool, ns):
        bits = [str(v) if isinstance(v, Raw) and v == k else f"{k}={_hint_val(v, False)}" for k, v in args.items()]
        return f"{tool}({', '.join(bits)})"
    return f'flh_call("{tool}", {{' + ", ".join(f'"{k}": {_hint_val(v, True)}' for k, v in args.items()) + "})"


# ============================================================================= plain / screen-reader-friendly words
def wordy():
    """True when the user opted into plain language or uses a screen reader / low vision (fl_access): tools then
    give worded forms ('Intro at bar 1') instead of compact shorthand ('Intro@1', 'X.x.o', '[i VI]x2')."""
    f = H.get("access_prefs")
    if not callable(f):
        return False
    try:
        p = f() or {}
    except Exception:
        return False
    return p.get("vision") in ("low", "blind") or p.get("language") == "plain"


_TIMES = {1: "once", 2: "twice", 3: "three times", 4: "four times"}


def times_word(n):
    return _TIMES.get(int(n), f"{int(n)} times")


def grid_words(grid):
    """'X...x...o..g....' (16 steps of a bar) -> 'step 1 accent, step 5 hit, step 9 soft, step 12 ghost'."""
    kinds = {"X": "accent", "x": "hit", "o": "soft", "g": "ghost", "r": "roll"}
    hits = [f"step {i + 1} {kinds[c]}" for i, c in enumerate(str(grid)) if c in kinds]
    return ", ".join(hits) if hits else "silent"


GRID_KEY = "16 steps of bar 1 (a 16th note each): X accent, x hit, o soft, g ghost, r roll, . rest"


def color_name(hexcolor):
    """'#5A2A2A' -> 'dark red' (so colour is never the only way something is told)."""
    try:
        h = str(hexcolor).lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return None
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 0.08:
        return "black" if mx < 0.15 else ("dark grey" if mx < 0.4 else ("grey" if mx < 0.75 else "white"))
    d = mx - mn
    hue = (60 * ((g - b) / d % 6) if mx == r else 60 * ((b - r) / d + 2) if mx == g else 60 * ((r - g) / d + 4))
    names = [(15, "red"), (40, "orange"), (65, "yellow"), (160, "green"), (195, "teal"), (255, "blue"),
             (290, "purple"), (335, "pink"), (361, "red")]
    nm = next(n for lim, n in names if hue < lim)
    if nm == "orange" and mx < 0.6:
        nm = "brown"
    sat = d / mx
    pre = "dark " if mx < 0.45 else ("light " if mn > 0.7 else "")
    return pre + ("greyish " if sat < 0.3 else "") + nm


def color_text(hexcolor):
    """'#5A2A2A (dark red)'."""
    nm = color_name(hexcolor)
    return f"{hexcolor} ({nm})" if nm else str(hexcolor)


# ============================================================================= data
def _user_file(name):
    st = H.get("STATE")
    return os.path.join(os.path.dirname(st), "score", name) if st else None


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v
    return a


def data(name):
    """'instruments' (data/instrument_ranges.json) or 'rules' (data/score/rules.json), merged with the user's
    override file in <state folder>/score/ (its instrument entries come first, so they win)."""
    if name in _DATA:
        return _DATA[name]
    rel = "instrument_ranges.json" if name == "instruments" else os.path.join("score", "rules.json")
    with open(os.path.join(HERE, "data", rel), encoding="utf-8") as f:
        d = json.load(f)
    up = _user_file(os.path.basename(rel))
    if up and os.path.isfile(up):
        try:
            with open(up, encoding="utf-8") as f:
                u = json.load(f)
            if name == "instruments":
                d["instruments"] = list(u.get("instruments") or []) + d["instruments"]
                d["roles"].update(u.get("roles") or {})
            else:
                _merge(d, u)
            d["_user_file"] = up
        except Exception as e:                                   # a broken user file must not break the tools
            d["_user_error"] = f"{up}: {e}"
    if name == "instruments":
        for i in d["instruments"]:
            i["_re"] = [re.compile(p, re.I) for p in i.get("match", [])]
            for k in ("range", "good"):
                if k in i:
                    i["_" + k] = tuple(FM.parse_note(x) for x in i[k])
        for k, r in d["roles"].items():
            if isinstance(r, dict):
                for kk in ("range", "good"):
                    if kk in r:
                        r["_" + kk] = tuple(FM.parse_note(x) for x in r[kk])
    else:
        d["_limits"] = {int(k): FM.parse_note(v) for k, v in d["low_interval_limits"].items() if not k.startswith("_")}
    _DATA[name] = d
    return d


def instrument_for(texts):
    """First instrument entry whose patterns match one of the texts (tried in order) -> entry dict or None."""
    ins = data("instruments")["instruments"]
    for t in texts:
        if not t:
            continue
        t = str(t)
        for i in ins:
            if any(r.search(t) for r in i["_re"]):
                return i
    return None


# ============================================================================= chords (numpy-vectorised naming)
_CH = {}


def _chord_table():
    if "M" in _CH:
        return _CH
    import numpy as np
    rows, miss, meta = [], [], []
    for q, iv in data("rules")["chords"].items():
        if q.startswith("_"):
            continue
        q = q.split("|")[0]                                       # "13|5": a second voicing of the 13 chord
        for r in range(12):
            v = np.zeros(12); mw = np.zeros(12)
            for i in iv:
                v[(r + i) % 12] = 1; mw[(r + i) % 12] = 0.5 if i == 7 else 1.0      # an omitted 5th is fine
            rows.append(v); miss.append(mw); meta.append((r, q, frozenset((r + i) % 12 for i in iv), len(iv)))
    _CH.update(M=np.array(rows), MW=np.array(miss), n=np.array([m[3] for m in meta], float),
               root=np.array([m[0] for m in meta]), meta=meta,
               ext=np.array([1.0 if m[1] in EXT_Q else (0.6 if m[1] in ("aug", "dim") else
                                                         (0.5 if m[1] in ("sus2", "sus4") else 0.0)) for m in meta]))
    return _CH


EXT_Q = {"9", "maj9", "m9", "13", "m11", "6/9", "7b9", "add9", "madd9", "6", "m6", "7sus4", "mmaj7"}


def is_chord_set(pcs):
    """Is this pitch-class set exactly a chord of the vocabulary (any root)? (F A C Eb G = F9, C E G B = Cmaj7)"""
    key = frozenset(pcs)
    hit = _CH.get(("set", key))
    if hit is None:
        T = _chord_table()
        hit = _CH[("set", key)] = any(meta[2] == key for meta in T["meta"])
    return hit


def best_chord(w, bass=None, ext_pen=0.06):
    """12 pitch-class weights (+ bass pitch class) -> (root, quality, pcs frozenset, score) or None.
    Same scoring idea as fl_music.name_chord, with jazz extensions (penalised by ext_pen outside jazz, so plain
    triads / 7ths win unless the extension is really there) and a stronger bass-is-root preference."""
    import numpy as np
    w = np.asarray(w, float); tot = w.sum()
    if tot <= 0:
        return None
    T = _chord_table()
    inside = T["M"] @ w / tot
    missing = (T["MW"] @ (w <= tot * 0.02)) / T["n"]
    score = inside - 0.5 * missing - 0.025 * T["n"] - ext_pen * T["ext"]
    if bass is not None:
        score = score + 0.12 * (T["root"] == bass)
    k = int(np.argmax(score))
    if score[k] < 0.4:
        return None
    r, q, pcs, _ = T["meta"][k]
    return r, q, pcs, float(score[k])


def chord_name(c, bass=None):
    if not c:
        return "-"
    r, q, pcs = c[0], c[1], c[2]
    s = NAMES[r] + ("(5)" if q == "5" else q)
    if bass is not None and bass != r and bass in pcs:
        s += "/" + NAMES[bass]
    return s


MAJ_NUM = ["I", "bII", "II", "bIII", "III", "IV", "#IV", "V", "bVI", "VI", "bVII", "VII"]
MIN_NUM = ["I", "bII", "II", "III", "#III", "IV", "#IV", "V", "VI", "#VI", "VII", "#VII"]
ROMAN_SUFFIX = {"": "", "m": "", "dim": "°", "aug": "+", "sus2": "sus2", "sus4": "sus4", "7": "7", "maj7": "maj7",
                "m7": "7", "m7b5": "ø7", "dim7": "°7", "add9": "add9", "madd9": "add9", "6": "add6", "m6": "add6",
                "5": "(5)", "7sus4": "7sus4", "9": "9", "maj9": "maj9", "m9": "9", "13": "13", "m11": "11",
                "6/9": "6/9", "7b9": "7b9", "mmaj7": "maj7"}
SEVENTHS = ("7", "maj7", "m7", "m7b5", "dim7", "9", "maj9", "m9", "13", "m11", "7b9", "mmaj7", "7sus4")


def parse_key(name):
    """'C major' / 'A minor' -> (root pc, minor?)"""
    r, mode, _ = FM.parse_key(name)
    return r, mode in ("minor", "harmonic minor", "melodic minor", "dorian", "phrygian", "locrian")


def roman(c, key, bass=None):
    """Chord (root, quality, ...) in a key name -> roman numeral with figured-bass inversion (I6, V65)."""
    if not c:
        return "-"
    kr, minor = parse_key(key) if isinstance(key, str) else key
    r, q = c[0], c[1]
    num = (MIN_NUM if minor else MAJ_NUM)[(r - kr) % 12]
    if (q.startswith("m") and not q.startswith("maj")) or q in ("dim", "dim7", "m7b5"):
        num = num.lower()
    s = num + ROMAN_SUFFIX.get(q, q)
    if bass is not None and bass != r and bass in c[2] and q not in ("", "m", "dim", "aug", "7", "maj7", "m7",
                                                                       "m7b5", "dim7", "mmaj7"):
        return s + "/" + NAMES[bass]
    if bass is not None and bass != r and bass in c[2]:
        iv = (bass - r) % 12; sev = q in SEVENTHS
        fig = {3: "6", 4: "6", 6: "64", 7: "64", 8: "64"}.get(iv) if not sev else \
            {3: "65", 4: "65", 6: "43", 7: "43", 10: "42", 11: "42", 9: "42"}.get(iv)
        s = (num + (ROMAN_SUFFIX.get(q, "") if not sev else ROMAN_SUFFIX.get(q, "").replace("7", "")) + fig) if fig \
            else s + "/" + NAMES[bass]
    return s


# ============================================================================= the model
def _norm_note(n):
    if isinstance(n, dict):
        return (int(n["pos"]), max(1, int(n["len"])), int(n.get("ch", 0)), int(n["key"]), int(n.get("vel", 100)),
                n.get("pattern"), n.get("ppos", n["pos"]))
    n = tuple(n)
    return n[:5] + (n[5] if len(n) > 5 else None, n[6] if len(n) > 6 else n[0])


SLIDE_FLAG = 0x08                                                 # FL note flag: slide note (see flh_compose)


def apply_slides(notes, slides):
    """FL slide notes don't sound on their own: they bend the note already playing on that channel to their pitch,
    which then keeps sounding until that note ends. -> note list where each bent note is split at the slide (the
    rest of it at the new pitch); a slide note with nothing under it plays as a normal note (FL does the same).
    The bent note is the latest-starting note sounding under the slide (first in time order on ties); found by
    bisect per channel."""
    if not slides:
        return list(notes)
    out = sorted(notes)
    per, mx = {}, {}
    for i, n in enumerate(out):
        per.setdefault(n[CH], []).append((n[POS], i))
        mx[n[CH]] = max(mx.get(n[CH], 0), n[LEN])
    for sl in sorted(slides):
        lst = per.setdefault(sl[CH], [])
        k = bisect.bisect_left(lst, (sl[POS], -1)) - 1           # notes starting before the slide
        lo = sl[POS] - mx.get(sl[CH], 0)
        base = None
        while k >= 0 and lst[k][0] > lo:
            p_, i_ = lst[k]
            if base is not None and p_ < out[base][POS]:
                break
            n = out[i_]
            if n[POS] + n[LEN] > sl[POS] and (base is None or p_ > out[base][POS] or i_ < base):
                base = i_
            k -= 1
        if base is None:
            bisect.insort(lst, (sl[POS], len(out)))
            mx[sl[CH]] = max(mx.get(sl[CH], 0), sl[LEN])
            out.append(sl)
            continue
        n = out[base]
        end = n[POS] + n[LEN]
        out[base] = n[:LEN] + (sl[POS] - n[POS],) + n[LEN + 1:]
        bisect.insort(lst, (sl[POS], len(out)))
        out.append((sl[POS], end - sl[POS], n[CH], sl[KEY], n[VEL]) + tuple(sl[5:]))
    return sorted(out)


def _apply_slides_ref(notes, slides):
    """The plain O(slides x notes) version (tests compare apply_slides against it)."""
    if not slides:
        return list(notes)
    out = sorted(notes)
    for sl in sorted(slides):
        base = None
        for i, n in enumerate(out):
            if n[CH] == sl[CH] and n[POS] <= sl[POS] < n[POS] + n[LEN] and n[POS] < sl[POS]:
                if base is None or n[POS] > out[base][POS]:
                    base = i
        if base is None:
            out.append(sl)
            continue
        n = out[base]
        end = n[POS] + n[LEN]
        out[base] = n[:LEN] + (sl[POS] - n[POS],) + n[LEN + 1:]
        out.append((sl[POS], end - sl[POS], n[CH], sl[KEY], n[VEL]) + tuple(sl[5:]))
    return sorted(out)


def build_model(notes, ppq=96, beats_per_bar=4, time_sig=None, parts=None, sections=None, tempo=120.0, tempos=None,
                genre=None, name=None, automation=None, meters=None):
    """Model from note lists (tests, other modules). notes: (pos, len, ch, key, vel) tuples or {pos, len, ch, key,
    vel, slide} dicts in ticks (slide=true: an FL slide note, see apply_slides); parts: {ch: {name, role, instrument, melody, pitch, insert, preset, plugin}}; sections:
    [{name, bar, end_bar}]; meters: the time-signature map [(tick, num, den)] when the meter changes in the song."""
    plain, slides = [], []
    for n in notes:
        (slides if isinstance(n, dict) and n.get("slide") else plain).append(_norm_note(n))
    m = {"P": int(ppq), "bpb": float(beats_per_bar), "ts": tuple(time_sig or (int(beats_per_bar), 4)),
         "notes": sorted(apply_slides(plain, slides)), "tempos": tempos or [(0, float(tempo))],
         "parts_in": {int(k): dict(v) for k, v in (parts or {}).items()}, "sections_in": sections,
         "genre": genre, "name": name or "notes", "automation": automation or [], "source": None, "clip_sections": [],
         "meters": [tuple(int(x) for x in t) for t in meters] if meters else None}
    return _finish(m)


def load_model(project, journal_from=None):
    """Model of a .flp or .mid (cached on path + mtime + size + journal mtime). journal_from: the real project whose
    song journal applies (sections, roles, genre, sampler offsets) when `project` is a shadow / scratch copy."""
    if isinstance(project, dict):
        return project
    p = os.path.abspath(project); st = os.stat(p)
    jsrc = os.path.abspath(journal_from) if journal_from else p
    jp = S.journal_path(jsrc)
    key = (st.st_mtime_ns, st.st_size, jsrc, os.path.getmtime(jp) if os.path.isfile(jp) else 0)
    hit = _CACHE.get(p)
    if hit and hit[0] == key:
        return hit[1]
    m = _model_midi(p, jsrc) if p.lower().endswith((".mid", ".midi")) else _model_flp(p, jsrc)
    if len(_CACHE) > 6:
        _CACHE.clear()
    _CACHE[p] = (key, m)
    return m


def _journal(p):
    try:
        return S.journal_get(p)
    except Exception:
        return {}


def sounding_offsets(journal):
    """{channel: semitones} for pitched samplers whose notes are written away from the pitch they sound at
    (written = sounding + offset; an F#1 808 sample gets +30 because FL plays a sample's own pitch on C5).
    Read from the song journal: channels.N.transpose, mirrored in compose.transpose. The one helper every
    reader (score, compose) and writer (compose, arrange, adjust, newsong, load_sample) uses."""
    out = {}
    jr = journal if isinstance(journal, dict) else {}
    chans = {k: (v or {}).get("transpose") for k, v in (jr.get("channels") or {}).items() if isinstance(v, dict)}
    for src in (((jr.get("compose") or {}).get("transpose") or {}), chans):
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            try:
                if v is not None:
                    out[int(k)] = int(v)
            except (TypeError, ValueError):
                pass
    return {c: o for c, o in out.items() if o}


def _model_flp(p, jsrc=None):
    t0 = time.time()
    d = H["_load"](p); ev, blocks, _ = H["_structure"](d); P = H["_ppq"](d); pay = H["_payload"]
    meta, chans = {}, {}; cur = None
    for e in ev:                                                   # project info + channel scan (one pass)
        i = e["id"]
        if i == H["EV_CHANNEL"]:
            cur = struct.unpack("<H", pay(d, e)[:2])[0]; chans[cur] = {}
        elif cur is None:
            if i not in meta:
                meta[i] = e
        elif i == H["EV_CHANNEL_END"]:
            cur = None
        elif i == 0 and e["len"] == 1:
            chans[cur]["enabled"] = bool(pay(d, e)[0])
        elif i == 21 and e["len"] == 1:
            chans[cur]["type"] = pay(d, e)[0]
        elif i == 104 and e["len"] >= 2:                         # PC-VERIFY: event 104 = mixer insert on FL 2024-2026
            chans[cur]["insert"] = struct.unpack("<h", pay(d, e)[:2])[0]
        elif i == H["EV_DISPLAY_NAME"] and "name" not in chans[cur]:
            chans[cur]["name"] = H["_text16"](pay(d, e))
    PB_ = H.get("PROJECT_BYTE", {}); PT = H.get("PROJECT_TEXT", {})
    num = pay(d, meta[PB_["time_sig_num"]])[0] if PB_.get("time_sig_num") in meta else 4
    den = pay(d, meta[PB_["time_sig_beat"]])[0] if PB_.get("time_sig_beat") in meta else 4
    num, den = num or 4, den or 4
    genre = H["_text16"](pay(d, meta[PT["genre"]])) if PT.get("genre") in meta else ""
    main = next((struct.unpack("<I", pay(d, e))[0] / 1000 for e in ev if e["id"] == H["EV_TEMPO"]), 120.0)
    pats = H["_patterns"](d, ev); clips = H["_clips"](d, ev)
    PBASE = H["PATTERN_BASE"]; CM = H.get("CLIP_MUTE", 0x2000)
    notes, tempos, autos, csecs, slides = [], [], [], [], []; muted_clips = 0
    for _, _, r in clips:
        item = struct.unpack("<H", r[6:8])[0]; pos = struct.unpack("<I", r[0:4])[0]
        ln = struct.unpack("<I", r[8:12])[0]; muted = bool(struct.unpack("<H", r[18:20])[0] & CM)
        if item < PBASE:
            if chans.get(item, {}).get("type") == 5 and not muted:
                autos.append({"channel": item, "name": chans[item].get("name") or f"Automation {item}",
                              "pos": pos, "end": pos + ln})
            continue
        pid = item - PBASE; pat = pats.get(pid)
        if not pat:
            continue
        start = H["_clip_offsets"](r)[0]
        if muted:                                                  # a muted clip plays nothing (tempo lane too)
            muted_clips += 1
            continue
        for c in H["_ctrl_records"](pat["ctrl"]):
            t = struct.unpack("<I", c[:4])[0]
            if (c[4], c[7]) == H["TEMPO_CTRL"] and start <= t < start + ln:
                tempos.append((pos + t - start, struct.unpack("<I", c[8:])[0] / 1000))
        if pat["notes"] and (pat["name"] or "").strip().lower() != "tempo":
            csecs.append((pos, pat["name"] or f"Pattern {pid}", ln))
        for n in pat["notes"]:
            if start <= n[0] < start + ln:
                # PC-VERIFY: FL slide notes (flag 0x08) read as bends of the held note (checklists/score.md)
                (slides if n[1] & SLIDE_FLAG else notes).append(
                    (pos + n[0] - start, max(1, min(n[3], start + ln - n[0])), n[2], n[4], min(127, n[11]), pid, n[0]))
    notes = apply_slides(notes, slides)
    tempos.sort()
    if not tempos or tempos[0][0] > 0:
        tempos.insert(0, (0, main))
    jr = _journal(jsrc or p)
    parts = {}
    for ch in {n[CH] for n in notes}:
        c = chans.get(ch, {}); b = blocks.get(ch, {}); info = {"name": c.get("name") or b.get("name"),
                                                               "plugin": b.get("plugin"), "insert": c.get("insert"),
                                                               "enabled": c.get("enabled", True), "type": c.get("type")}
        if b.get("plugin") == "Fruity Wrapper" and "state" in b:
            st = pay(d, ev[b["state"]])
            try:
                info["vst"] = H["_wrapper_name"](st)
            except Exception:
                pass
            k = st.find(b"VC2!")                                   # JUCE plugins (Spitfire / Splice): loaded sound
            # PC-VERIFY: BBC SO Discover / LABS states carry <META name=...> with the instrument (checklists/score.md)
            if k >= 0:
                xml = st[k + 8:k + 8 + struct.unpack("<I", st[k + 4:k + 8])[0]].decode("utf-8", "replace")
                mm = re.search(r'<META [^>]*>', xml)
                if mm:
                    for f in ("name", "family", "tags"):
                        v = re.search(f'{f}="([^"]*)"', mm.group(0))
                        if v:
                            info["preset_" + f] = v.group(1)
        parts[ch] = info
    try:                                                           # time-signature markers: the meter map
        meters = [tuple(int(x) for x in t) for t in H["_meter_map"](d, ev)] if H.get("_meter_map") else None
    except Exception:
        meters = None
    m = {"P": P, "bpb": num * 4.0 / den, "ts": (num, den), "notes": sorted(notes), "tempos": tempos, "meters": meters,
         "parts_in": parts, "sections_in": None, "journal": jr, "genre": " ".join(x for x in (
             str(jr.get("genre") or ""), str(jr.get("style") or ""), genre) if x).strip(),
         "name": os.path.basename(p), "automation": autos, "source": p, "clip_sections": csecs,
         "muted_clips": muted_clips, "parse_s": round(time.time() - t0, 3), "offsets_apply": True}
    return _finish(m)


def _vlq(t, j):
    n = 0
    while True:
        b = t[j]; j += 1; n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            return n, j


def _model_midi(p, jsrc=None):
    """MIDI file: one part per (track, MIDI channel); track names become part names."""
    d = open(p, "rb").read()
    if d[:4] != b"MThd":
        raise ValueError(f"{p}: not a MIDI file")
    hl = struct.unpack(">I", d[4:8])[0]; _, ntr, div = struct.unpack(">HHH", d[8:14])
    if div & 0x8000:
        raise ValueError("SMPTE-timed MIDI files aren't supported")
    i = 8 + hl; notes, tempos, names, num, den = [], [], {}, 4, 4; part_of = {}; sigs = {}
    for tr in range(ntr):
        if d[i:i + 4] != b"MTrk":
            break
        L = struct.unpack(">I", d[i + 4:i + 8])[0]; t = d[i + 8:i + 8 + L]; i += 8 + L
        j = tick = 0; run = None; on = {}; tname = None
        while j < len(t):
            dt, j = _vlq(t, j); tick += dt; b = t[j]
            if b == 0xFF:
                typ = t[j + 1]; ln, j = _vlq(t, j + 2)
                if typ == 0x51 and ln == 3:
                    tempos.append((tick, round(60_000_000 / int.from_bytes(t[j:j + 3], "big"), 3)))
                elif typ == 0x58 and ln >= 2:
                    if tick == 0:
                        num, den = t[j], 2 ** t[j + 1]
                    if t[j] and t[j + 1] <= 6:
                        sigs[tick] = (t[j], 2 ** t[j + 1])
                elif typ == 0x03 and tname is None:
                    tname = t[j:j + ln].decode("latin-1", "replace")
                j += ln
                continue
            if b in (0xF0, 0xF7):
                ln, j = _vlq(t, j + 1); j += ln
                continue
            if b & 0x80:
                run = b; j += 1
            hi, mc = run & 0xF0, run & 0x0F
            if hi in (0x80, 0x90):
                key, vel = t[j], t[j + 1]
                ch = part_of.setdefault((tr, mc), len(part_of))
                if hi == 0x90 and vel:
                    on.setdefault((mc, key), []).append((tick, vel))
                elif on.get((mc, key)):
                    s, v = on[(mc, key)].pop(0); notes.append((s, max(1, tick - s), ch, key, v, None, s))
            j += 1 if hi in (0xC0, 0xD0) else 2
        for (tr_, mc), ch in part_of.items():
            if tr_ == tr:
                names[ch] = {"name": tname, "midi_channel": mc + 1}
    tempos = sorted(tempos) or [(0, 120.0)]
    if tempos[0][0] > 0:
        tempos.insert(0, (0, tempos[0][1]))
    for ch, info in names.items():
        if info.get("midi_channel") == 10:
            info["kind"] = "drums"                                  # GM percussion channel
    jr = _journal(jsrc or p)
    sigs[0] = (num, den)
    meters = [(t_, n_, d_) for t_, (n_, d_) in sorted(sigs.items())]
    m = {"P": div, "bpb": num * 4.0 / den, "ts": (num, den), "notes": sorted(notes), "tempos": tempos,
         "meters": meters if len(meters) > 1 else None, "parts_in": names, "sections_in": None, "journal": jr,
         "genre": " ".join(str(jr.get(k) or "") for k in ("genre", "style")).strip(), "name": os.path.basename(p),
         "automation": [], "source": p, "clip_sections": []}
    return _finish(m)


def _median(v):
    s = sorted(v); n = len(s)
    return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2) if n else 0


def _role_from_text(t):
    t = (t or "").lower()
    for role, rx in ROLE_WORDS:
        if re.search(rx, t):
            return role
    return None


def _finish(m):
    """Derived facts: bar grid, parts (instrument, role, stats), melody, sections."""
    P = m["P"]; m["bar_t"] = bar_t = max(1, int(round(m["bpb"] * P)))
    _meter_setup(m)
    notes = m["notes"]
    # pitched samplers whose notes are written away from their sounding pitch: analyse what is heard
    offs = sounding_offsets(m.get("journal")) if m.get("offsets_apply") else {}
    m["offsets"] = offs
    if offs:
        notes = sorted(n[:KEY] + (max(0, n[KEY] - offs[n[CH]]),) + n[KEY + 1:] if n[CH] in offs else n for n in notes)
    # a note or clip millions of bars out (corrupt clip position / length) must not make the analysis hang
    lim = MAX_BARS * bar_t
    far = [n for n in notes if n[POS] >= lim]
    cut = sum(1 for n in notes if n[POS] < lim < n[POS] + n[LEN])
    if far or cut:                                                 # an endless note ends with the rest of the song
        song_end = max((n[POS] + n[LEN] for n in notes if n[POS] + n[LEN] <= lim), default=0)
        notes = [n if n[POS] + n[LEN] <= lim else
                 n[:LEN] + (min(lim, max(song_end, n[POS] + bar_t)) - n[POS],) + n[LEN + 1:] for n in notes if n[POS] < lim]
        m["far"] = {"notes": len(far), "bar": (1 + far[0][POS] // bar_t) if far else MAX_BARS + 1, "cut": cut}
        m["clip_sections"] = [c for c in m.get("clip_sections") or [] if c[0] < lim]
        m["automation"] = [a for a in m.get("automation") or [] if a["pos"] < lim]
    m["notes"] = notes
    m["end"] = max((n[POS] + n[LEN] for n in notes), default=0)
    m["n_bars"] = max(1, -(-m["end"] // bar_t)) if not m.get("bar_starts") else max(1, _bar_idx(m, max(0, m["end"] - 1)) + 1)
    num, den = m["ts"]
    if den == 8 and num % 3 == 0 and num >= 6:                     # compound meter: strong = each dotted beat
        m["strong_t"] = int(round(1.5 * P)) if num > 6 else int(round(m["bpb"] * P / 2))
    elif m["bpb"] >= 4 and m["bpb"] == int(m["bpb"]) and int(m["bpb"]) % 2 == 0:
        m["strong_t"] = bar_t // 2
    else:
        m["strong_t"] = bar_t
    m["seg_t"] = m["strong_t"] if m["strong_t"] < bar_t else bar_t
    jr = m.get("journal") or {}
    jch = {str(k): v for k, v in (jr.get("channels") or {}).items() if isinstance(v, dict)}
    by = {}
    for n in notes:
        by.setdefault(n[CH], []).append(n)
    parts = {}
    for ch, ns in by.items():
        info = dict(m["parts_in"].get(ch) or {}); j = jch.get(str(ch), {})
        for k in ("name", "role", "instrument", "melody", "pitch", "insert"):
            if j.get(k) is not None and (k != "name" or not info.get("name") or _defaultish(info)):
                info[k] = j[k]
        info["jname"] = j.get("name")
        info.update(_stats(m, ns))
        info["ch"] = ch
        parts[ch] = info
    for ch, info in parts.items():
        _identify(m, info)
    _pick_melody(m, parts)
    m["parts"] = parts
    m["drums"] = frozenset(ch for ch, p in parts.items() if p["role"] == "drums")
    m["pitched"] = [n for n in notes if n[CH] not in m["drums"] and parts[n[CH]].get("enabled", True) is not False]
    m["sections"] = _sections(m)
    m["_c"] = {}
    m["ext_pen"] = 0.0 if tolerance_for(m)[0] == "jazz" else 0.06
    return m


def _defaultish(info):
    """Channel name is just the plugin's default name (e.g. 'Splice INSTRUMENT #3')."""
    nm = (info.get("name") or "").lower()
    if not nm:
        return True
    base = re.sub(r"\s*#\d+$", "", nm)
    return any(base == (info.get(k) or "").lower() for k in ("plugin", "vst")) or base in ("sampler", "3x osc")


def _stats(m, ns):
    P = m["P"]; bar_t = m["bar_t"]
    keys = [n[KEY] for n in ns]; vels = [n[VEL] for n in ns]
    groups = []                                                   # onset groups (chords)
    tol = max(1, P // 24)
    for n in ns:
        if groups and n[POS] - groups[-1][0] <= tol:
            groups[-1][1].append(n)
        else:
            groups.append((n[POS], [n]))
    bars = sorted({_bar_idx(m, n[POS]) for n in ns})
    ioi = [(b[0] - a[0]) / P for a, b in zip(groups, groups[1:]) if 0 < b[0] - a[0] <= 4 * P]
    avg_len = sum(n[LEN] for n in ns) / len(ns) / P
    on_beat = sum(1 for g in groups if g[0] % P <= tol or P - g[0] % P <= tol) / len(groups)
    rhythm = None
    if ioi:
        cnt = {}
        for x in ioi:
            v = min(RHYTHM_VALUES, key=lambda rv: abs(rv[0] - x))
            cnt[v[1]] = cnt.get(v[1], 0) + 1
        rhythm = max(cnt, key=cnt.get)
    return {"n": len(ns), "low": min(keys), "high": max(keys), "median": _median(keys), "vel_lo": min(vels),
            "vel_hi": max(vels), "vel": round(sum(vels) / len(vels)), "poly": round(len(ns) / len(groups), 2),
            "avg_len": round(avg_len, 2), "bars": bars, "density": round(len(ns) / max(1, len(bars)), 1),
            "rhythm": rhythm, "on_beat": round(on_beat, 2),
            "legato": round(avg_len / (sum(ioi) / len(ioi)), 2) if ioi and sum(ioi) else None}


def _identify(m, p):
    """Instrument (range entry) and role of a part."""
    texts = [p.get("instrument"), p.get("jname"), p.get("preset_name")]
    if not _defaultish(p):
        texts.append(p.get("name"))
    texts += [p.get("preset_family"), p.get("preset_tags"), p.get("role"), p.get("vst"), p.get("plugin")]
    if _defaultish(p):
        texts.append(p.get("name"))
    ins = instrument_for(texts)
    p["instrument_id"] = ins["id"] if ins else None
    p["_ins"] = ins
    role = _role_from_text(p.get("role")) if p.get("role") else None
    named = None
    if role is None:                                               # unambiguous channel names: Lead, Bass, Pad, Arp
        for t in (p.get("jname"), p.get("name") if not _defaultish(p) else None):
            if t:
                named = next((r for r, rx in NAME_ROLES if re.search(rx, t, re.I)), None)
                if named:
                    break
        role = named
    if p.get("melody") is True:
        role = "melody"
    if p.get("kind") == "drums" or (ins and ins.get("kind") == "drums"):
        role = "drums"
    if role is None and (p.get("plugin") in ("FPC", "Fruity DrumSynth Live", "Drumaxx", "Fruity Slicer")):
        role = "drums"
    if role is None and p.get("type") == 0 and p["low"] == p["high"] and p["avg_len"] <= 0.5:
        role = "drums"                                            # one-shot sample on one key
    if role is None and ins and ins["id"] == "timpani":
        role = "perc"
    if role is None:
        if p["poly"] >= 2.0:
            role = "pad" if p["avg_len"] >= 2.0 else "chords"
        elif p["median"] < 52 or (ins and ins.get("family") == "bass") or (ins and ins["id"] in ("sub bass", "808",
                                                                                                 "synth bass")):
            role = "bass"
        elif p["avg_len"] <= 0.55 and p["density"] >= 8 and (p.get("legato") or 0) < 1.3:
            role = "arp"
        else:
            role = "line"
    p["role"] = role
    p["role_guessed"] = not (p.get("melody") is True or named or (p.get("role") and _role_from_text(p.get("role"))))


def _pick_melody(m, parts):
    nb = m["n_bars"]
    mel = [c for c, p in parts.items() if p["role"] == "melody"]
    lines = [c for c, p in parts.items() if p["role"] == "line" and len(p["bars"]) >= max(1, 0.15 * nb)]
    skyline = False
    if not mel and lines:
        mel = [max(lines, key=lambda c: parts[c]["median"] + 0.08 * parts[c]["vel"] + 4 * math.log2(1 + parts[c]["n"]))]
    if not mel:
        cand = [c for c, p in parts.items() if p["role"] in ("chords", "pad", "line") and len(p["bars"]) >= 0.15 * nb]
        if cand:
            mel = [max(cand, key=lambda c: parts[c]["high"] + parts[c]["median"] * 0.5)]
            skyline = parts[mel[0]]["poly"] >= 1.4
    for c, p in parts.items():
        if p["role"] == "line":
            p["role"] = "melody" if mel and c == mel[0] else "counter"
    if mel:
        parts[mel[0]]["role"] = parts[mel[0]]["role"] if skyline else "melody"
    m["melody"] = {"ch": mel[0], "skyline": skyline} if mel else None


def melody_line(m):
    """Monophonic melody notes (the melody part, or the top voice of a polyphonic part)."""
    if "melody_line" in m["_c"]:
        return m["_c"]["melody_line"]
    out = []
    if m.get("melody"):
        ch = m["melody"]["ch"]; tol = max(1, m["P"] // 24); grp = []
        ns = [n for n in m["notes"] if n[CH] == ch]
        for n in ns:
            if grp and n[POS] - grp[0][POS] > tol:
                out.append(max(grp, key=lambda x: x[KEY])); grp = []
            grp.append(n)
        if grp:
            out.append(max(grp, key=lambda x: x[KEY]))
        if m["melody"]["skyline"]:                                # top voice: drop inner notes under a held top
            keep = []
            for n in out:
                if keep and keep[-1][POS] + keep[-1][LEN] > n[POS] + m["P"] // 4 and n[KEY] < keep[-1][KEY]:
                    continue
                keep.append(n)
            out = keep
    m["_c"]["melody_line"] = out
    return out


def _sections(m):
    bar_t = m["bar_t"]; last = m["n_bars"] + 1
    secs = m.get("sections_in") or (m.get("journal") or {}).get("sections") or []
    out = []
    for s in secs:
        if isinstance(s, dict) and s.get("bar") is not None:
            out.append({"name": str(s.get("name") or f"bar {s['bar']}"), "bar": float(s["bar"]),
                        "end_bar": float(s["end_bar"]) if s.get("end_bar") else None, "src": "journal"})
    if not out and m.get("clip_sections"):
        starts = {}
        for pos, name, ln in m["clip_sections"]:
            starts.setdefault(pos, set()).add(name)
        if len(starts) >= 2:
            for pos in sorted(starts):
                nm = starts[pos]
                name = next(iter(nm)) if len(nm) == 1 else None
                b = _tick_barf(m, pos)
                if out and name and out[-1]["name"] == name:
                    continue                                       # a loop placed again: same section
                out.append({"name": name or f"{b:g}", "bar": round(b, 3), "end_bar": None, "src": "playlist"})
            if len(out) < 2 or len(out) > 40:
                out = []
    if not out:
        blk = 8 if m["n_bars"] > 8 else max(1, m["n_bars"])
        if m["n_bars"] > 32 * blk:                                 # very long songs: at most ~32 blocks
            blk = 8 * -(-m["n_bars"] // (32 * 8))
        out = [{"name": f"{b}-{min(b + blk - 1, m['n_bars'])}", "bar": float(b), "end_bar": None, "src": "blocks"}
               for b in range(1, m["n_bars"] + 1, blk)]
    out.sort(key=lambda s: s["bar"])
    seen = {}
    for i, s in enumerate(out):
        if not s.get("end_bar"):
            s["end_bar"] = out[i + 1]["bar"] if i + 1 < len(out) else float(last)
        c = seen.get(s["name"], 0); seen[s["name"]] = c + 1
        if c:
            s["name"] = f"{s['name']}#{c + 1}"
        s["a"] = _bar_tick(m, s["bar"]); s["b"] = _bar_tick(m, s["end_bar"])
    return out


def section_of(m, tick):
    """The first section (in bar order) holding this tick, else the last one (bisect over the section starts)."""
    secs = m["sections"]
    if not secs:
        return None
    st = m["_c"].get("sec_starts")
    if st is None:
        st = m["_c"]["sec_starts"] = [s["a"] for s in secs]
        m["_c"]["sec_simple"] = all(s["b"] <= t["a"] for s, t in zip(secs, secs[1:]))
    if m["_c"]["sec_simple"]:                                     # sorted, no overlaps: one candidate
        j = bisect.bisect_right(st, tick) - 1
        if j >= 0 and secs[j]["a"] <= tick < secs[j]["b"]:
            return secs[j]
        return secs[-1]
    for s in secs[:bisect.bisect_right(st, tick)]:               # overlapping journal sections: first match
        if s["a"] <= tick < s["b"]:
            return s
    return secs[-1]


def _by_ch(m, pitched=False):
    """{channel: notes in time order} (cached)."""
    ck = ("by_ch", pitched)
    out = m["_c"].get(ck)
    if out is None:
        out = {}
        for n in (m["pitched"] if pitched else m["notes"]):
            out.setdefault(n[CH], []).append(n)
        m["_c"][ck] = out
    return out


def _slice(m, a, b, pitched=False):
    """Notes with a <= start < b, in time order (bisect over the sorted starts)."""
    src = m["pitched"] if pitched else m["notes"]
    ck = ("pos", pitched)
    pos = m["_c"].get(ck)
    if pos is None:
        pos = m["_c"][ck] = [n[POS] for n in src]
    return src[bisect.bisect_left(pos, a):bisect.bisect_left(pos, b)]


def _range_arg(m, section=None, bars=None):
    """(start tick, end tick, label) of a section name / 'a-b' bars selection, or the whole song."""
    if section:
        if re.fullmatch(r"\s*\d+(\.\d+)?\s*-\s*\d+(\.\d+)?\s*", str(section)):
            bars = section
        else:
            low = str(section).lower()
            hit = next((s for s in m["sections"] if s["name"].lower() == low), None) or \
                next((s for s in m["sections"] if s["name"].lower().startswith(low)), None)
            if not hit:
                raise ValueError(f"no section '{section}' (have: {[s['name'] for s in m['sections']]})")
            return hit["a"], hit["b"], hit["name"]
    if bars:
        a, b = [float(x) for x in str(bars).split("-")]
        return _bar_tick(m, a), _bar_tick(m, b + 1), f"bars {a:g}-{b:g}"
    return 0, _bar_start(m, m["n_bars"]), None


# ----------------------------------------------------------------------------- bars through the meter map
# One meter: bar i starts at i * bar_t (the plain formulas). Time-signature markers (m["meters"], more than one entry):
# bars have the length of the meter in force; m["bar_starts"] lists their start ticks to past the song's end.
def _meter_setup(m):
    # PC-VERIFY: flp_score on a song with a time-signature marker mid-song: bar numbers and chords per bar match FL's
    # playlist ruler after the marker (checklists/score.md)
    ms = sorted({int(t): (int(n_), int(d_)) for t, n_, d_ in (m.get("meters") or []) if n_ and d_}.items())
    m["bar_starts"] = None
    if len(ms) < 2 or all(x[1] == ms[0][1] for x in ms):
        return
    P = m["P"]; end = max([n[POS] + n[LEN] for n in m["notes"]] + [ms[-1][0]])
    st, ts = [], []
    for i, (t0, (n_, d_)) in enumerate(ms):
        blen = P * n_ * 4.0 / d_
        t1 = ms[i + 1][0] if i + 1 < len(ms) else end + 8 * blen
        t = float(t0)
        while t < t1 - 1e-6 and len(st) < MAX_BARS + 64:
            st.append(int(round(t))); ts.append((n_, d_)); t += blen
    m["bar_starts"], m["bar_ts"] = st, ts
    m["meter_text"] = " > ".join(f"{n_}/{d_}" + (f" (bar {_bar_idx(m, t) + 1})" if t else "") for t, (n_, d_) in ms)


def _bar_idx(m, tick):
    """0-based bar holding a tick."""
    st = m.get("bar_starts")
    if not st:
        return tick // m["bar_t"]
    if tick >= st[-1]:
        n_, d_ = m["bar_ts"][-1]
        return len(st) - 1 + int((tick - st[-1]) // (m["P"] * n_ * 4.0 / d_))
    return max(0, bisect.bisect_right(st, tick) - 1)


def _bar_start(m, bi):
    """Start tick of 0-based bar bi."""
    st = m.get("bar_starts")
    if not st:
        return bi * m["bar_t"]
    if bi < len(st):
        return st[max(0, bi)]
    n_, d_ = m["bar_ts"][-1]
    return int(round(st[-1] + (bi - len(st) + 1) * m["P"] * n_ * 4.0 / d_))


def _bar_len(m, bi):
    st = m.get("bar_starts")
    if not st:
        return m["bar_t"]
    return _bar_start(m, bi + 1) - _bar_start(m, bi)


def _bar_meter(m, bi):
    """(num, den) of 0-based bar bi."""
    st = m.get("bar_starts")
    if not st:
        return m["ts"]
    return m["bar_ts"][min(max(0, bi), len(st) - 1)]


def _bar_tick(m, bar):
    """1-based (fractional) song bar -> tick."""
    if not m.get("bar_starts"):
        return int(round((float(bar) - 1) * m["bar_t"]))
    b = float(bar) - 1; bi = int(math.floor(b + 1e-9))
    return int(round(_bar_start(m, bi) + (b - bi) * _bar_len(m, bi)))


def _tick_barf(m, tick):
    """Tick -> 1-based fractional bar."""
    if not m.get("bar_starts"):
        return 1 + tick / m["bar_t"]
    bi = _bar_idx(m, tick)
    return 1 + bi + (tick - _bar_start(m, bi)) / _bar_len(m, bi)


def _strong_len(m, bi=None, ts=None):
    """Ticks between strong beats in a bar of meter ts (the model's rule: compound = each dotted beat, even quarter
    meters = half bars, others = the bar)."""
    num, den = ts or _bar_meter(m, bi)
    P = m["P"]; q = num * 4.0 / den; blen = int(round(q * P))
    if den == 8 and num % 3 == 0 and num >= 6:
        return int(round(1.5 * P)) if num > 6 else int(round(q * P / 2))
    if q >= 4 and q == int(q) and int(q) % 2 == 0:
        return blen // 2
    return blen


def _off_strong(m, tick):
    """Ticks past the last strong beat (0 = on a strong beat)."""
    if not m.get("bar_starts"):
        return tick % m["strong_t"]
    bi = _bar_idx(m, tick)
    return (tick - _bar_start(m, bi)) % _strong_len(m, bi)


def _meter_label(m):
    num, den = m["ts"]
    return m.get("meter_text") or f"{num}/{den}"


def _bar(m, tick):
    return 1 + _bar_idx(m, tick)


def _bb(m, tick):
    """'bar:beat' (1-based; beats to 0.1)."""
    b = _bar_idx(m, tick); beat = round((tick - _bar_start(m, b)) / m["P"] + 1, 1)
    return f"{b + 1}:{beat:g}"


def nn(k):
    return FM.note_name(int(k))


# ============================================================================= harmony per segment
def _segments(m, seg_t):
    """Per segment: {ch: 12 weights} and {ch: lowest key} (pitched parts)."""
    ck = ("seg", seg_t)
    if ck in m["_c"]:
        return m["_c"][ck]
    nseg = max(1, -(-m["end"] // seg_t))
    W = [dict() for _ in range(nseg)]; LOW = [dict() for _ in range(nseg)]
    mch = m["melody"]["ch"] if m.get("melody") and not m["melody"]["skyline"] else None
    P = m["P"]
    for n in m["pitched"]:
        s0, e = n[POS] // seg_t, n[POS] + n[LEN]
        s1 = min(nseg - 1, (e - 1) // seg_t)
        # loud, long, accompaniment notes define the harmony; short melody notes are often passing tones
        vw = (0.5 + n[VEL] / 254) * (0.5 + 0.5 * min(1.0, n[LEN] / P)) * (0.6 if n[CH] == mch else 1.0)
        for s in range(s0, s1 + 1):
            a, b = max(n[POS], s * seg_t), min(e, (s + 1) * seg_t)
            ov = b - a
            if ov <= 0:
                continue
            w = W[s].get(n[CH])
            if w is None:
                w = W[s][n[CH]] = [0.0] * 12
            w[n[KEY] % 12] += ov * vw
            if ov >= seg_t / 4 or n[POS] >= s * seg_t:
                lo = LOW[s].get(n[CH])
                if lo is None or n[KEY] < lo:
                    LOW[s][n[CH]] = n[KEY]
    m["_c"][ck] = (W, LOW)
    return W, LOW


def chord_at(m, seg_t, s, exclude=frozenset(), pen=None):
    """(chord tuple or None, bass pc) for segment s of seg_t ticks, leaving out the excluded channels."""
    return chord_segs(m, seg_t, s, s + 1, exclude, pen)


def chord_segs(m, seg_t, s0, s1, exclude=frozenset(), pen=None):
    pen = m.get("ext_pen", 0.06) if pen is None else pen
    ck = ("chord", seg_t, s0, s1, exclude, pen)
    c = m["_c"].get(ck)
    if c is not None:
        return c
    W, LOW = _segments(m, seg_t)
    w = [0.0] * 12; lo = None
    for s in range(max(0, s0), min(len(W), s1)):
        for ch, v in W[s].items():
            if ch in exclude:
                continue
            for i in range(12):
                w[i] += v[i]
            k = LOW[s].get(ch)
            if k is not None and (lo is None or k < lo):
                lo = k
    bass = lo % 12 if lo is not None else None
    if bass is not None:
        w[bass] *= 1.5                                             # the bass defines the harmony more than its length
    c = (best_chord(w, bass, pen), bass)
    m["_c"][ck] = c
    return c


def pcs_span(m, a, b, exclude=frozenset()):
    """12 pitch-class weights sounding in ticks [a, b) (beat grid), leaving out the excluded channels."""
    P = m["P"]; s0 = a // P; s1 = max(s0 + 1, -(-b // P))
    ck = ("pcs", s0, s1, exclude)
    if ck in m["_c"]:
        return m["_c"][ck]
    W, _ = _segments(m, P)
    w = [0.0] * 12
    for s in range(max(0, s0), min(len(W), s1)):
        for ch, v in W[s].items():
            if ch not in exclude:
                for i in range(12):
                    w[i] += v[i]
    m["_c"][ck] = w
    return w


def chord_span(m, a, b, exclude=frozenset(), pen=None):
    """Harmony sounding in ticks [a, b) (beat grid; at least one beat)."""
    P = m["P"]
    s0 = a // P; s1 = max(s0 + 1, -(-b // P))
    return chord_segs(m, P, s0, s1, exclude, pen)


def key_estimate(m, a=0, b=None, top=3):
    """Krumhansl-Schmuckler (fl_music.detect_key) on duration-weighted notes, bass parts counted double; near-ties
    (within 0.05) go to the key whose tonic chord opens or closes the passage."""
    a = int(a)                                                     # ticks: a float bar's tick would break range()
    b = int(b) if b is not None else None
    ck = ("key", a, b, top)
    if ck in m["_c"]:
        return m["_c"][ck]
    e = b if b is not None else 1 << 40
    notes = []
    if "maxlen" not in m["_c"]:
        m["_c"]["maxlen"] = max((n[LEN] for n in m["pitched"]), default=0)
    for n in _slice(m, a - m["_c"]["maxlen"], e, pitched=True):
        if n[POS] < e and n[POS] + n[LEN] > a:
            ln = min(n[POS] + n[LEN], e) - max(n[POS], a)
            notes.append({"key": n[KEY], "len": ln * (2 if m["parts"][n[CH]]["role"] == "bass" else 1)})
    res = FM.detect_key(notes, max(top, 3)) if notes else []
    if len(res) > 1:
        end = min(e, m["end"]); bar_t = m["bar_t"]
        if m.get("bar_starts"):                                    # the first and last bars of the meter map
            ends = [_bar_chord(m, _bar_idx(m, a))[0], _bar_chord(m, _bar_idx(m, max(0, end - 1)))[0]]
        else:
            ends = [chord_segs(m, bar_t, a // bar_t, a // bar_t + 1)[0], chord_segs(m, bar_t, (end - 1) // bar_t, (end - 1) // bar_t + 1)[0]]
        def tonic_hit(k):
            r, minor = parse_key(k)
            return any(c and c[0] == r and (c[1].startswith("m") and not c[1].startswith("maj")) == minor for c in ends)
        near = [x for x in res if res[0][1] - x[1] <= 0.05]
        best = next((x for x in near if tonic_hit(x[0])), None)
        if best and best != res[0]:
            res = [best] + [x for x in res if x != best]
    res = res[:top]
    m["_c"][ck] = res
    return res


def _conf(r):
    return "high" if r >= 0.8 else ("medium" if r >= 0.6 else "low")


# ============================================================================= score view
def _tempo_at(m, a, b):
    tm = m["tempos"]; cur = tm[0][1]; seen = []
    for t, bpm in tm:
        if t <= a:
            cur = bpm
        elif t < b:
            seen.append(bpm)
    lo, hi = min([cur] + seen), max([cur] + seen)
    return cur, lo, hi


def _fmt_bpm(x):
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _family(q):
    return "m" if (q.startswith("m") and not q.startswith("maj")) else ("d" if q.startswith("dim") or q == "m7b5" else
                                                                       ("a" if q == "aug" else "M"))


def _map_grid(m):
    """A segment grid every bar line and strong beat of the meter map falls on (ticks; cached)."""
    q = m["_c"].get("map_grid")
    if q is None:
        q = 0
        for x in set(m["bar_ts"]):
            q = math.gcd(q, int(round(m["P"] * x[0] * 4.0 / x[1])))
            q = math.gcd(q, _strong_len(m, ts=x))
        q = m["_c"]["map_grid"] = max(1, q)
    return q


def _bar_chord(m, bi, part=None):
    """(chord, bass) of 0-based bar bi of a meter map; part 0 / 1: its first / second strong segment."""
    q = _map_grid(m); a = _bar_start(m, bi); L = _bar_len(m, bi)
    if part is not None:
        sl = _strong_len(m, bi)
        a, L = a + part * sl, sl
    return chord_segs(m, q, a // q, (a + L) // q)


def bar_chords(m, a, b, segment="auto"):
    """[(bar, chord, bass, half_chord or None)] for the bars in [a, b)."""
    bar_t, seg_t = m["bar_t"], m["seg_t"]
    out = []
    if m.get("bar_starts"):                                       # time-signature markers: each bar its own length
        for bi in range(_bar_idx(m, a), _bar_idx(m, max(a, b - 1)) + 1):
            split = _strong_len(m, bi) < _bar_len(m, bi)
            if segment == "half" and split:
                c1 = _bar_chord(m, bi, 0); c2 = _bar_chord(m, bi, 1)
                out.append((bi + 1, c1[0], c1[1], c2))
                continue
            c = _bar_chord(m, bi)
            half = None
            if segment == "auto" and split:
                c1 = _bar_chord(m, bi, 0); c2 = _bar_chord(m, bi, 1)
                if c1[0] and c2[0] and (c1[0][0], _family(c1[0][1])) != (c2[0][0], _family(c2[0][1])) and \
                        c1[0][3] >= 0.75 and c2[0][3] >= 0.75:
                    c, half = c1, c2
            out.append((bi + 1, c[0], c[1], half))
        return out
    for bi in range(a // bar_t, -(-b // bar_t)):
        if segment == "half" and seg_t < bar_t:
            c1 = chord_at(m, seg_t, bi * bar_t // seg_t); c2 = chord_at(m, seg_t, bi * bar_t // seg_t + 1)
            out.append((bi + 1, c1[0], c1[1], c2))
            continue
        c = chord_at(m, bar_t, bi)
        half = None
        if segment == "auto" and seg_t < bar_t:
            c1 = chord_at(m, seg_t, bi * bar_t // seg_t); c2 = chord_at(m, seg_t, bi * bar_t // seg_t + 1)
            if c1[0] and c2[0] and (c1[0][0], _family(c1[0][1])) != (c2[0][0], _family(c2[0][1])) and \
                    c1[0][3] >= 0.75 and c2[0][3] >= 0.75:
                c, half = c1, c2
        out.append((bi + 1, c[0], c[1], half))
    return out


def _compress(seq, plain=False):
    """['i','VI','i','VI'] -> '[i VI]x2'; repeats in a row -> 'i*3'. plain: 'i, VI (all twice)', 'i three times'."""
    n = len(seq)
    if not n:
        return ""
    sep = ", " if plain else " "
    for p in range(1, n // 2 + 1):
        reps = n // p
        if reps >= 2 and all(seq[i] == seq[i % p] for i in range(n)):
            body = sep.join(_runs(seq[:p], plain))
            tail = seq[reps * p:]
            if plain:
                head = f"{body} (all {times_word(reps)})" if p > 1 else f"{seq[0]} {times_word(reps)}"
                return head + (", then " + sep.join(_runs(tail, plain)) if tail else "")
            return (f"[{body}]x{reps}" if p > 1 else f"{seq[0]}*{reps}") + (" " + " ".join(_runs(tail)) if tail else "")
    return sep.join(_runs(seq, plain))


def _runs(seq, plain=False):
    out = []
    for x in seq:
        if out and out[-1][0] == x:
            out[-1][1] += 1
        else:
            out.append([x, 1])
    return [x if c == 1 else (f"{x} {times_word(c)}" if plain else f"{x}*{c}") for x, c in out]


def _section_facts(m, s):
    a, b = s["a"], s["b"]
    ns = _slice(m, a, b)
    pitched = [n for n in ns if n[CH] not in m["drums"]]
    bars = max(1e-9, (b - a) / m["bar_t"] if not m.get("bar_starts") else _tick_barf(m, b) - _tick_barf(m, a))
    k = key_estimate(m, a, b, 2)
    tempo, lo, hi = _tempo_at(m, a, b)
    return {"notes": len(ns), "npb": round(len(ns) / bars, 1), "vel": round(sum(n[VEL] for n in ns) / len(ns)) if ns else 0,
            "layers": sorted({n[CH] for n in ns}), "low": min((n[KEY] for n in pitched), default=None),
            "high": max((n[KEY] for n in pitched), default=None), "key": k, "tempo": (tempo, lo, hi)}


def _energy(facts):
    mx = {k: max([f[k] for f in facts] + [1e-9]) for k in ("npb", "vel")}
    ml = max([len(f["layers"]) for f in facts] + [1]); ms = max([(f["high"] - f["low"]) if f["low"] is not None else 0
                                                                 for f in facts] + [1])
    out = []
    for f in facts:
        sp = (f["high"] - f["low"]) if f["low"] is not None else 0
        e = 0.35 * f["npb"] / mx["npb"] + 0.25 * f["vel"] / mx["vel"] + 0.25 * len(f["layers"]) / ml + 0.15 * sp / ms
        out.append(int(round(9 * e)) if f["notes"] else 0)
    return out


def repeats(m, a=0, b=None, ch=None, transpose=False):
    """Bar-level repetition: [(dst_a, dst_b, src_a, src_b, sim, shift)] (1-based bars), runs >= 2 bars
    (single bars too when ch is given). transpose: also find transposed repeats (melodic motifs)."""
    bar_t = m["bar_t"]; b = m["end"] if b is None else b
    q = max(1, m["P"] // 4)
    nb0, nb1 = a // bar_t, -(-b // bar_t)
    if m.get("bar_starts"):
        nb0, nb1 = _bar_idx(m, a), _bar_idx(m, max(a, b - 1)) + 1
    sigs = {}
    for n in m["notes"]:
        if ch is not None and n[CH] != ch:
            continue
        bi = _bar_idx(m, n[POS])
        if nb0 <= bi < nb1:                                       # (bars of another meter can't match: length kept)
            sigs.setdefault(bi, []).append(((n[POS] - _bar_start(m, bi)) // q, n[KEY], n[CH]) +
                                           ((_bar_meter(m, bi),) if m.get("bar_starts") else ()))
    fs = {}
    for bi, v in sigs.items():
        if transpose:
            k0 = min(x[1] for x in v)
            fs[bi] = (frozenset((x[0], x[1] - k0, x[2]) for x in v), k0)
        else:
            fs[bi] = (frozenset(v), 0)
    first, match = {}, {}
    order = sorted(fs)
    for bi in order:
        s, k0 = fs[bi]
        if s in first:
            src = first[s]; match[bi] = (src, 1.0, k0 - fs[src][1]); continue
        first[s] = bi
        best = None
        for ai in order:
            if ai >= bi:
                break
            t, _ = fs[ai]
            if not 0.7 <= len(t) / len(s) <= 1.43:
                continue
            j = len(s & t) / len(s | t)
            if j >= 0.7 and (best is None or j > best[1]):
                best = (ai, j, k0 - fs[ai][1])
        if best:
            match[bi] = best
    runs = []
    for bi in order:
        if bi not in match:
            continue
        src, sim, sh = match[bi]
        r = runs[-1] if runs else None
        if r and r["db"] == bi - 1 and r["sb"] + 1 < bi and r["sb"] + 1 in fs and fs[r["sb"] + 1][0] == fs[bi][0]:
            src = r["sb"] + 1; sim = 1.0; sh = fs[bi][1] - fs[src][1]  # keep the run going (a loop repeated)
        if r and r["db"] == bi - 1 and r["sb"] == src - 1 and (r["shift"] == sh or not transpose):
            r["db"], r["sb"] = bi, src; r["sims"].append(sim)
        else:
            runs.append({"da": bi, "db": bi, "sa": src, "sb": src, "sims": [sim], "shift": sh})
    minlen = 1 if ch is not None else 2
    return [(r["da"] + 1, r["db"] + 1, r["sa"] + 1, r["sb"] + 1, round(sum(r["sims"]) / len(r["sims"]), 2),
             r["shift"] if transpose else 0) for r in runs if r["db"] - r["da"] + 1 >= minlen]


def _fmt_repeats(rs, limit=10):
    out = []
    for da, db, sa, sb, sim, sh in sorted(rs, key=lambda r: -(r[1] - r[0]))[:limit]:
        dst = f"{da}" if da == db else f"{da}-{db}"; src = f"{sa}" if sa == sb else f"{sa}-{sb}"
        out.append((da, f"{dst}{'=' if sim >= 0.999 else '~'}{src}" + (f"({sh:+d})" if sh else "")))
    return [x for _, x in sorted(out)]


def contour(m, notes=None, a=0, b=None):
    """Melody contour: range, climax, steps / leaps share, phrase shapes."""
    line = notes if notes is not None else [n for n in melody_line(m) if a <= n[POS] < (b if b is not None else 1 << 40)]
    if len(line) < 2:
        return None
    P = m["P"]
    iv = [abs(y[KEY] - x[KEY]) for x, y in zip(line, line[1:])]
    steps = sum(1 for v in iv if 0 < v <= 2) / len(iv); leaps = sum(1 for v in iv if v >= 5) / len(iv)
    phrases, cur = [], [line[0]]
    for x, y in zip(line, line[1:]):
        if y[POS] - (x[POS] + x[LEN]) >= P or y[POS] - x[POS] >= 4 * P:
            phrases.append(cur); cur = []
        cur.append(y)
    phrases.append(cur)
    shapes = {}
    for ph in phrases:
        if len(ph) < 3:
            continue
        ks = [n[KEY] for n in ph]; hi, lo = max(ks), min(ks); f, l_ = ks[0], ks[-1]
        if hi - lo <= 2:
            sh = "flat"
        elif hi - max(f, l_) >= 3 and 0 < ks.index(hi) < len(ks) - 1:
            sh = "arch"
        elif min(f, l_) - lo >= 3 and 0 < ks.index(lo) < len(ks) - 1:
            sh = "valley"
        elif l_ - f >= 3:
            sh = "rise"
        elif f - l_ >= 3:
            sh = "fall"
        else:
            sh = "wave"
        shapes[sh] = shapes.get(sh, 0) + 1
    top = max(line, key=lambda n: (n[KEY], -n[POS]))
    return {"range": f"{nn(min(n[KEY] for n in line))}-{nn(top[KEY])}", "climax": f"{nn(top[KEY])}@{_bb(m, top[POS])}",
            "steps": round(steps, 2), "leaps": round(leaps, 2), "phrases": len(phrases),
            "shapes": dict(sorted(shapes.items(), key=lambda kv: -kv[1]))}


def _rhythm_txt(p):
    if p["avg_len"] >= 2.5 and p["poly"] >= 1.5:
        r = f"sustained {p['avg_len']:g}b"
    else:
        r = p.get("rhythm") or "-"
        if p.get("on_beat") is not None and p["on_beat"] < 0.6:
            r += f" {int(round(100 * (1 - p['on_beat'])))}%off"
        if p.get("legato") is not None and p["legato"] < 0.5:
            r += " stacc"
    if p["poly"] >= 1.5:
        r += f" poly{p['poly']:g}"
    return r


def _part_label(p):
    nm = p.get("name") or ""
    if _defaultish(p) and p.get("preset_name"):
        nm = p["preset_name"]
    nm = nm[:22]
    ins = p.get("instrument_id")
    return f"{p['ch']} {nm or 'ch' + str(p['ch'])}" + (f" [{ins}]" if ins and ins.lower() not in nm.lower() else "")


def _active_ranges(bars):
    out, run = [], None
    for b in bars:
        if run and b == run[1] + 1:
            run[1] = b
        else:
            if run:
                out.append(run)
            run = [b, b]
    if run:
        out.append(run)
    return ",".join(f"{a + 1}" if a == b else f"{a + 1}-{b + 1}" for a, b in out)


def piano_roll(m, ch, a, b, max_bars=8):
    """ASCII piano roll of one part: '#' note start, '=' held, '.' rest; '|' bar lines. 16ths up to 4 bars, else
    8ths. Rows: highest pitch first."""
    bar_t, P = m["bar_t"], m["P"]
    if m.get("bar_starts"):
        return _piano_roll_map(m, ch, a, b, max_bars)
    b = min(b, a + max_bars * bar_t)
    nbars = -(-(b - a) // bar_t)
    res = 4 if nbars <= 4 else 2
    cell = P // res; cols = int(round(m["bpb"] * res))
    ns = [n for n in m["notes"] if n[CH] == ch and n[POS] < b and n[POS] + n[LEN] > a]
    if not ns:
        return [f"(no notes on channel {ch} in bars {1 + a // bar_t}-{b // bar_t})"]
    keys = sorted({n[KEY] for n in ns}, reverse=True)
    rows = list(range(keys[0], keys[-1] - 1, -1)) if keys[0] - keys[-1] <= 24 else keys
    grid = {k: ["."] * (cols * nbars) for k in rows}
    for n in ns:
        first = (max(n[POS], a) - a) // cell; last = min(cols * nbars - 1, (min(n[POS] + n[LEN], b) - 1 - a) // cell)
        for ci in range(first, last + 1):
            if grid[n[KEY]][ci] != "#":
                grid[n[KEY]][ci] = "#" if ci == first and n[POS] >= a else "="
    head = "    |" + "|".join("".join(str(1 + c // res) if c % res == 0 else " " for c in range(cols))
                              for _ in range(nbars)) + "|"
    out = [f"ROLL ch{ch} bars {1 + a // bar_t}-{a // bar_t + nbars} (1 cell = 1/{4 * res} note; # start = held)", head]
    for k in rows:
        g = grid[k]
        out.append(f"{nn(k):<4}|" + "|".join("".join(g[i * cols:(i + 1) * cols]) for i in range(nbars)) + "|")
    return out


def _piano_roll_map(m, ch, a, b, max_bars=8):
    """piano_roll for a song with time-signature markers: each bar as long as its meter."""
    P = m["P"]; bi0 = _bar_idx(m, a); a = _bar_start(m, bi0)
    bis = [bi for bi in range(bi0, bi0 + max_bars) if _bar_start(m, bi) < b]
    b = _bar_start(m, bis[-1] + 1)
    res = 4 if len(bis) <= 4 else 2
    cell = max(1, P // res)
    cols = [max(1, int(round(_bar_len(m, bi) / cell))) for bi in bis]
    tot = sum(cols)
    ns = [n for n in m["notes"] if n[CH] == ch and n[POS] < b and n[POS] + n[LEN] > a]
    if not ns:
        return [f"(no notes on channel {ch} in bars {bi0 + 1}-{bis[-1] + 1})"]
    keys = sorted({n[KEY] for n in ns}, reverse=True)
    rows = list(range(keys[0], keys[-1] - 1, -1)) if keys[0] - keys[-1] <= 24 else keys
    grid = {k: ["."] * tot for k in rows}
    for n in ns:
        first = (max(n[POS], a) - a) // cell; last = min(tot - 1, (min(n[POS] + n[LEN], b) - 1 - a) // cell)
        for ci in range(first, last + 1):
            if grid[n[KEY]][ci] != "#":
                grid[n[KEY]][ci] = "#" if ci == first and n[POS] >= a else "="
    cuts = [sum(cols[:i]) for i in range(len(cols) + 1)]
    head = "    |" + "|".join("".join(str(1 + c // res) if c % res == 0 else " " for c in range(k)) for k in cols) + "|"
    out = [f"ROLL ch{ch} bars {bi0 + 1}-{bis[-1] + 1} ({_meter_label(m)}; 1 cell = 1/{4 * res} note; # start = held)",
           head]
    for k in rows:
        g = grid[k]
        out.append(f"{nn(k):<4}|" + "|".join("".join(g[cuts[i]:cuts[i + 1]]) for i in range(len(cols))) + "|")
    return out


def score_view(project, section=None, part=None, bars=None, piano_roll=False, segment="auto", format="text",
               full=False, journal_from=None):
    """Compact musical facts about a song or a selection. -> {"score": text, ...} (format=json: structured).
    journal_from: the real project when `project` is a shadow copy (lazy mode), so its journal applies."""
    t0 = time.time()
    m = load_model(project, journal_from); t1 = time.time()
    if not m["notes"]:
        raise ValueError("no notes in the song (nothing placed in the playlist plays notes)")
    a, b, label = _range_arg(m, section, bars)
    secs = [s for s in m["sections"] if s["b"] > a and s["a"] < b]
    gk = key_estimate(m, 0, None, 3)                               # the song's key (numerals stay in it)
    key = gk[0][0] if gk else "C major"
    lk = key_estimate(m, a, b, 1) if label else []
    facts = [_section_facts(m, s) for s in secs]
    energy = _energy(facts)
    num, den = m["ts"]
    tempo0, tlo, thi = _tempo_at(m, a, b)
    L = []
    title = f"SCORE {m['name']}" + (f" | {label}" if label else "")
    tempo_txt = f"{_fmt_bpm(tempo0)} bpm" + (f" ({_fmt_bpm(tlo)}-{_fmt_bpm(thi)})" if thi - tlo >= 1 else "")
    alt = f"; alt {gk[1][0]} {gk[1][1]:.2f}" if len(gk) > 1 else ""
    local = f" | here {lk[0][0]} (r={lk[0][1]:.2f})" if lk and lk[0][0] != key else ""
    nbars_ = round((b - a) / m["bar_t"]) if not m.get("bar_starts") else round(_tick_barf(m, b) - _tick_barf(m, a))
    L.append(f"{title} | {nbars_} bars {_meter_label(m)} | {tempo_txt} | key {key} "
             f"(r={gk[0][1]:.2f} {_conf(gk[0][1])}{alt}){local} | C4=60 (FL's piano roll calls it C5)" if gk else
             f"{title} | no pitched notes")
    plain = wordy()
    if plain:
        L.append("KEY TO THE SHORT FORMS: E0-9 = energy (0 quiet, 9 full); n/b = notes per bar; v = velocity (how hard, "
                 "1-127); r = how sure the key is (0-1); roman numerals = chords counted from the key (capital = major, "
                 "small = minor); 'a,b' = two chords in one bar; '= Verse' = the same chords as the Verse; ? = guessed")
    if m.get("muted_clips"):
        L.append(f"(skipped {m['muted_clips']} muted clips)")
    if m.get("far"):
        L.append("WARNING " + _far_text(m))
    if m.get("offsets"):
        L.append("(pitched samplers shown at sounding pitch; written = sounding " + ", ".join(
            f"{o:+d} on ch{c}" for c, o in sorted(m["offsets"].items())) + ")")
    # ---- sections
    L.append(f"SECTIONS ({secs[0]['src'] if secs else '-'}) bar name: E(0-9) notes/bar vel parts register [key/tempo if changed]")
    jsecs = []
    for s, f, e in zip(secs, facts, energy):
        extra = []
        if f["key"] and f["key"][0][0] != key and f["key"][0][1] >= 0.7:
            here = dict(f["key"]).get(key)
            extra.append(f"key {f['key'][0][0]}" + (" (mod)" if here is None or f["key"][0][1] - here >= 0.1 else "?"))
        tp = f["tempo"]
        if abs(tp[0] - tempo0) >= 0.5 or tp[2] - tp[1] >= 1:
            extra.append(f"{_fmt_bpm(tp[0])}bpm" + (f"({_fmt_bpm(tp[1])}-{_fmt_bpm(tp[2])})" if tp[2] - tp[1] >= 1 else ""))
        reg = f"{nn(f['low'])}-{nn(f['high'])}" if f["low"] is not None else "-"
        L.append(f" {s['name'] if s['src'] == 'blocks' else str(s['bar']).rstrip('0').rstrip('.') + ' ' + s['name']}: E{e} {f['npb']:g}n/b v{f['vel']} parts:{_layers_txt(f['layers'], m['parts'])} "
                 f"{reg}" + (" " + " ".join(extra) if extra else ""))
        jsecs.append({"name": s["name"], "bar": s["bar"], "end_bar": s["end_bar"], "energy": e, "notes_per_bar": f["npb"],
                      "vel": f["vel"], "parts": f["layers"], "register": reg,
                      "key": f["key"][0][0] if f["key"] else None, "key_r": f["key"][0][1] if f["key"] else None,
                      "tempo": tp[0]})
    # ---- parts
    sel_parts, here = {}, {}
    for n in _slice(m, a, b):
        here.setdefault(n[CH], []).append(n)
    for ch, p in sorted(m["parts"].items()):
        ns = here.get(ch)
        if ns:
            q = dict(p); q.update(_stats(m, ns)); q["role"] = p["role"]; sel_parts[ch] = q
    grouped = format != "json" and not full and part is None and len(sel_parts) > PARTS_GROUP
    L.append("PARTS ch name [instrument] role(?=guessed) register notes/bar vel rhythm (bars active)" if not grouped else
             f"PARTS {len(sel_parts)} parts, grouped by role (part=<channel> or full=true lists each): role count "
             "channels register notes/bar")
    jparts, groups = [], {}
    mel_ch = m["melody"]["ch"] if m.get("melody") else None
    for ch, p in sel_parts.items():
        role = p["role"] + ("?" if p.get("role_guessed") else "")
        if p["role"] == "drums":
            reg = f"{len({n[KEY] for n in _by_ch(m).get(ch, [])})} keys"
        else:
            reg = f"{nn(p['low'])}-{nn(p['high'])}"
        act = _active_ranges(p["bars"])
        if len(act) > 30:
            act = f"{len(p['bars'])} bars"
        line = f" {_part_label(p)} {role} {reg} {p['density']:g} v{p['vel_lo']}-{p['vel_hi']} {_rhythm_txt(p)} ({act})"
        if not grouped or ch == mel_ch:
            L.append(line + (" <- melody" if grouped else ""))
        else:
            gr = groups.setdefault(p["role"], {"chs": [], "lo": 127, "hi": 0, "npb": 0.0, "drum": p["role"] == "drums"})
            gr["chs"].append(ch); gr["npb"] += p["density"]
            gr["lo"] = min(gr["lo"], p["low"]); gr["hi"] = max(gr["hi"], p["high"])
        jparts.append({"ch": ch, "name": p.get("name"), "instrument": p.get("instrument_id"), "role": p["role"],
                       "role_guessed": p.get("role_guessed"), "low": nn(p["low"]), "high": nn(p["high"]),
                       "notes_per_bar": p["density"], "vel": [p["vel_lo"], p["vel_hi"]], "rhythm": _rhythm_txt(p),
                       "bars": act})
    for role, gr in sorted(groups.items(), key=lambda kv: -len(kv[1]["chs"])):
        rg = _active_ranges([c - 1 for c in gr["chs"]]).split(",")
        L.append(f" {role} x{len(gr['chs'])}: ch {' '.join(rg[:10])}{f' +{len(rg) - 10} more' if len(rg) > 10 else ''} "
                 + (f"{nn(gr['lo'])}-{nn(gr['hi'])}" if not gr["drum"] else "drums") + f" {gr['npb']:.0f} n/b total")
    # ---- melody
    mel = None
    if m.get("melody") and m["melody"]["ch"] in sel_parts:
        c = contour(m, a=a, b=b)
        if c:
            mel = dict(c, ch=m["melody"]["ch"], skyline=m["melody"]["skyline"])
            sh = " ".join(f"{k}{v}" for k, v in c["shapes"].items())
            L.append(f"MELODY ch{mel['ch']}{' (top voice)' if mel['skyline'] else ''}: {c['range']}, climax {c['climax']}, "
                     f"{int(100 * c['steps'])}% steps {int(100 * c['leaps'])}% leaps, {c['phrases']} phrases: {sh or '-'}")
    # ---- chords
    seg = "half" if segment == "half" else ("bar" if segment == "bar" else "auto")
    legend, jchords, rows = {}, {}, []
    L.append(f"CHORDS in {key} (roman per bar; a,b = two halves; - = none)")
    seqs = {}
    detail = bool(section or bars)
    for s in secs:
        sa, sb = max(s["a"], a), min(s["b"], b)
        bc = bar_chords(m, sa, sb, seg)
        seq = []
        for bar_no, c, bass, half in bc:
            r = roman(c, key, bass)
            if c:
                legend.setdefault(r, chord_name(c, bass))
            rows.append({"bar": bar_no, "chord": _sym(c, bass), "roman": r})
            if half:
                r2 = roman(half[0], key, half[1])
                if half[0]:
                    legend.setdefault(r2, chord_name(half[0], half[1]))
                rows.append({"bar": bar_no + 0.5, "chord": _sym(half[0], half[1]), "roman": r2})
                r = f"{r},{r2}"
            seq.append(r)
        jchords[s["name"]] = seq
        if detail:
            L.append(f" {s['name']}: " + " ".join(f"{bc[i][0]}:{x}" for i, x in enumerate(seq)))
            continue
        key_seq = tuple(seq)
        same = seqs.get(key_seq)
        span = f"{int(s['bar'])}-{int(s['end_bar']) - 1}" if s["end_bar"] - s["bar"] > 1 else f"{int(s['bar'])}"
        L.append(f" {s['name'] if s['src'] == 'blocks' else s['name'] + ' ' + span}: " + (f"= {same}" if same else _compress(seq, plain)))
        seqs.setdefault(key_seq, s["name"])
    L.insert(len(L) - len(secs), " legend " + " ".join(f"{k}={v}" for k, v in list(legend.items())[:24]))
    # ---- repeats
    rp = _fmt_repeats(repeats(m, a, b), 12)
    if rp:
        L.append("REPEATS (bars; = exact, ~ similar) " + " ".join(rp))
    # ---- detail views
    sel = None
    if part is not None:
        sel = _find_part(m, part)
        L += _part_detail(m, sel, a, b, secs)
    if piano_roll:
        ch = sel if sel is not None else (m["melody"]["ch"] if m.get("melody") else next(iter(sel_parts)))
        ra = a if (section or bars) else (secs[0]["a"] if secs else 0)
        L += (roll_words if plain else piano_roll_fn)(m, ch, ra, min(b, ra + (8 if (section or bars) else 4) * m["bar_t"]))
    if full:
        L.append("ACTIVITY per section (parts playing): " + "; ".join(
            f"{s['name']}:{','.join(map(str, f['layers']))}" for s, f in zip(secs, facts)))
    text = "\n".join(L)
    res = {"score": text} if format != "json" else {
        "song": m["name"], "bars": nbars_, "meter": _meter_label(m), "tempo": tempo0,
        "key": gk, "sections": jsecs, "parts": jparts, "chords": rows, "progression": jchords, "legend": legend,
        "repeats": rp, "melody": mel}
    if m.get("far") and format == "json":
        res["warning"] = _far_text(m)
    if m.get("offsets") and format == "json":
        res["written_offsets"] = {str(c): o for c, o in sorted(m["offsets"].items())}
    res["steps_s"] = {"model": round(t1 - t0, 3), "view": round(time.time() - t1, 3)}
    return res


def _layers_txt(layers, parts):
    """Which parts play: the list, or for big songs a count (+ the few that rest)."""
    if len(layers) <= 12:
        return ",".join(map(str, layers)) or "-"
    rest = sorted(set(parts) - set(layers))
    return f"{len(layers)} of {len(parts)}" + (f" (all but {','.join(map(str, rest))})" if 0 < len(rest) <= 6 else
                                               (" (all)" if not rest else ""))


def _far_text(m):
    f = m["far"]
    bits = []
    if f["notes"]:
        bits.append(f"{f['notes']} note(s) from bar {f['bar']:,} on were left out")
    if f.get("cut"):
        bits.append(f"{f['cut']} note(s) held past bar {MAX_BARS:,} were cut at the song's end")
    return "; ".join(bits) + (f" (a clip or note that far out - over {MAX_BARS:,} bars - looks corrupt: check the "
                              "playlist's last clips and the note / clip lengths)")


piano_roll_fn = piano_roll


def roll_words(m, ch, a, b, max_bars=8):
    """The piano roll as words, for screen readers: per bar, each note with its beat and length."""
    bar_t, P = m["bar_t"], m["P"]
    mp = bool(m.get("bar_starts"))
    b = min(b, a + max_bars * bar_t) if not mp else min(b, _bar_start(m, _bar_idx(m, a) + max_bars))
    ns = [n for n in _by_ch(m).get(ch, []) if a <= n[POS] < b]
    b0, b1 = (a // bar_t, -(-b // bar_t)) if not mp else (_bar_idx(m, a), _bar_idx(m, max(a, b - 1)) + 1)
    if not ns:
        return [f"(no notes on channel {ch} in bars {1 + b0}-{b // bar_t if not mp else b1})"]
    out = [f"NOTES of channel {ch}, bars {1 + b0}-{b1} (beat = quarter note; lengths in beats)"]
    for bi in range(b0, b1):
        row = [n for n in ns if _bar_idx(m, n[POS]) == bi]
        if not row:
            out.append(f" bar {bi + 1}: rest")
            continue
        out.append(f" bar {bi + 1}: " + "; ".join(
            f"{nn(n[KEY])} on beat {round((n[POS] - _bar_start(m, bi)) / P + 1, 2):g} for {round(n[LEN] / P, 2):g}"
            for n in row[:24]) + (f"; +{len(row) - 24} more" if len(row) > 24 else ""))
    return out


def _sym(c, bass=None):
    """Plain chord symbol for other modules: 'Am7', 'G5', 'C/E' ('-' = none)."""
    if not c:
        return "-"
    s_ = NAMES[c[0]] + c[1]
    return s_ + ("/" + NAMES[bass] if bass is not None and bass != c[0] and bass in c[2] else "")


def _model_arg(project=None, notes=None, ppq=96, bpm=None, beats_per_bar=4, path=None, parts=None, sections=None,
               time_sig=None, genre=None):
    if notes is not None:
        return build_model(notes, ppq, beats_per_bar, time_sig=time_sig, parts=parts, sections=sections,
                           tempo=bpm or 120.0, genre=genre)
    src = project or path
    if src is None:
        raise ValueError("give project= (.flp / .mid) or notes=")
    return src


def score_api(project=None, notes=None, ppq=96, bpm=None, beats_per_bar=4, path=None, parts=None, sections=None,
              time_sig=None, genre=None, **kw):
    """ns["score_view"] for other modules: like the flp_score tool but structured (format=json by default) and it
    also takes notes=[{pos, len, key, vel, ch}] (ticks at ppq) instead of a project. Result keys: key [(name, r)],
    sections, parts, chords [{bar, chord, roman}] (bar 1-based, x.5 = second half), progression {section: [roman]},
    legend, repeats, melody, steps_s; format="text" -> {"score": text}."""
    kw.setdefault("format", "json")
    return score_view(_model_arg(project, notes, ppq, bpm, beats_per_bar, path, parts, sections, time_sig, genre), **kw)


def clashes_api(project=None, notes=None, ppq=96, bpm=None, beats_per_bar=4, path=None, parts=None, sections=None,
                time_sig=None, genre=None, **kw):
    """ns["clashes"]: the flp_clashes tool, also on notes=[...] (see score_api)."""
    return clashes(_model_arg(project, notes, ppq, bpm, beats_per_bar, path, parts, sections, time_sig, genre),
                   genre=genre, **kw)


def _find_part(m, part):
    if isinstance(part, int) or str(part).strip().isdigit():
        ch = int(part)
        if ch not in m["parts"]:
            raise ValueError(f"channel {ch} plays no notes (parts: {sorted(m['parts'])})")
        return ch
    low = str(part).lower()
    for ch, p in m["parts"].items():
        if low in " ".join(str(p.get(k) or "") for k in ("name", "instrument_id", "role", "preset_name")).lower():
            return ch
    raise ValueError(f"no part matching '{part}' (parts: {[_part_label(p) for p in m['parts'].values()]})")


def _part_detail(m, ch, a, b, secs):
    p = m["parts"][ch]
    ns = [n for n in m["notes"] if n[CH] == ch and a <= n[POS] < b]
    L = [f"PART {_part_label(p)} role {p['role']}{'?' if p.get('role_guessed') else ''}" +
         (f" | range {p['_ins']['range'][0]}-{p['_ins']['range'][1]} good {p['_ins']['good'][0]}-{p['_ins']['good'][1]}"
          if p.get("_ins") and p["_ins"].get("range") else "")]
    for s in secs:
        sn = [n for n in ns if s["a"] <= n[POS] < s["b"]]
        if not sn:
            L.append(f" {s['name']}: -"); continue
        st = _stats(m, sn)
        L.append(f" {s['name']}: {nn(st['low'])}-{nn(st['high'])} {st['density']:g}n/b v{st['vel_lo']}-{st['vel_hi']} "
                 f"{_rhythm_txt(dict(st, role=p['role']))} first {nn(sn[0][KEY])}@{_bb(m, sn[0][POS])} v{sn[0][VEL]}")
    if p["role"] != "drums":
        line = ns if p["poly"] < 1.4 else None
        c = contour(m, notes=line) if line else None
        if c:
            L.append(f" contour {c['range']} climax {c['climax']} {int(100 * c['steps'])}% steps "
                     f"{int(100 * c['leaps'])}% leaps " + " ".join(f"{k}{v}" for k, v in c["shapes"].items()))
        mot = _fmt_repeats(repeats(m, a, b, ch=ch, transpose=True), 12)
        if mot:
            L.append(" motifs (bars; (+n) = transposed) " + " ".join(mot))
    return L


# ============================================================================= clash detector
def tolerance_for(m, tolerance="auto", genre=None):
    """-> (name, settings)."""
    rules = data("rules"); tols = rules["tolerance"]
    if isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool):
        x = float(tolerance); name = "strict" if x < 0.34 else ("normal" if x < 0.67 else "jazz")
        t = dict(tols[name]); t["scale"] = round(1.4 - 0.8 * x, 2)
        return name, t
    tolerance = (tolerance or "auto").lower()
    if tolerance in tols:
        return tolerance, tols[tolerance]
    text = (genre or m.get("genre") or "").lower()
    if not text:
        try:
            prof = S.profile_get() if S.H else {}
            text = " ".join(str((prof or {}).get(k) or "") for k in ("genre", "style")).lower()
        except Exception:
            text = ""
    for name in ("jazz", "strict", "groove"):
        if any(w in text for w in rules["genre_tolerance"].get(name) or []):
            return name, tols[name]
    return "normal", tols["normal"]


def _allowed(c, tol):
    """Chord tones + the extensions the tolerance accepts, as a pitch-class set."""
    if not c:
        return frozenset()
    r, q, pcs = c[0], c[1], c[2]
    ext = set(tol.get("ext") or [])
    if q in ("7", "9", "13", "7b9", "7sus4"):
        ext |= set(tol.get("dominant_ext") or [])
    if q in ("", "maj7", "maj9", "6", "6/9", "add9"):
        ext |= set(tol.get("major_ext") or [])
    if q == "5":
        ext |= {3, 4}                                              # a power chord leaves the 3rd open
    if q not in ("dim", "dim7", "m7b5", "aug"):
        ext |= {7}                                                 # an omitted 5th (13 chords, shells) still fits
    return frozenset(pcs | {(r + e) % 12 for e in ext})


def _sev(score, rules):
    s = rules["severity"]
    return "high" if score >= s["high"] else ("med" if score >= s["med"] else ("low" if score >= s["low"] else None))


def _pname(m, ch):
    p = m["parts"].get(ch, {})
    nm = p.get("name") if p.get("name") and not _defaultish(p) else (p.get("preset_name") or p.get("instrument_id"))
    return f"{(nm or 'ch' + str(ch))[:16].strip()}(ch{ch})"


def _edit_hint(m, ch, notes_, ops):
    """flp_edit_notes call that selects these notes (pattern-relative bars of the project's time signature, as the
    tool counts them)."""
    P = m["P"]; BT = m["bar_t"]                                    # 4/4: 4 beats
    bars_ = sorted({n[POS] // BT for n in notes_})
    if len(bars_) > 1 and len({n[KEY] for n in notes_}) == 1:     # only the flagged notes: one call per bar
        first = [n for n in notes_ if n[POS] // BT == bars_[0]]
        shift = [b - bars_[0] for b in bars_[1:4]]
        return _edit_hint(m, ch, first, ops) + f"; repeat with start_bar/end_bar shifted by {', '.join(map(str, shift))}" + \
            (f" (+{len(bars_) - 4} more)" if len(bars_) > 4 else "") + " bars"
    pids = {n[PID] for n in notes_}
    off = (m.get("offsets") or {}).get(ch, 0)                       # select by the pitch written in the pattern
    keys = [n[KEY] + off for n in notes_]
    if len(pids) == 1 and None not in pids:
        pp = [n[PPOS] for n in notes_]
        a, b = 1 + min(pp) // BT, 2 + max(pp) // BT
        return call_hint("flp_edit_notes", {"path": Raw("project"), "pattern": pids.pop(), "channel": ch, "start_bar": a,
                                            "end_bar": b, "low": min(keys), "high": max(keys), "ops": ops})
    if None not in pids:                                          # notes from several patterns: one call each,
        calls = []                                                # with bars counted inside each pattern
        for pid in sorted(pids, key=lambda x: (x is None, x))[:3]:
            calls.append(_edit_hint(m, ch, [n for n in notes_ if n[PID] == pid], ops))
        return "; ".join(calls) + (f" (+{len(pids) - 3} more patterns)" if len(pids) > 3 else "")
    a, b = 1 + min(n[POS] for n in notes_) // BT, 2 + max(n[POS] for n in notes_) // BT
    return call_hint("flp_edit_notes", {"path": Raw("project"), "channel": ch, "start_bar": a, "end_bar": b,
                                        "low": min(keys), "high": max(keys), "ops": ops})


def _where(m, ticks):
    ticks = sorted(set(ticks))
    if len({_bar(m, t) for t in ticks}) == 1:
        return f"bar {_bb(m, ticks[0])}" + (f" (+{len(ticks) - 1} in the bar)" if len(ticks) > 1 else "")
    bars = sorted({_bar(m, t) for t in ticks})
    sec = {section_of(m, t)["name"] for t in ticks if section_of(m, t)}
    rng = f"{bars[0]}-{bars[-1]}" if len(bars) > 1 else f"{bars[0]}"
    return f"bars {rng} ({len(ticks)}x" + (f", {'/'.join(sorted(sec))[:40]}" if sec else "") + ")"


def _nearest_tone(k, pcs, avoid=()):
    best = None
    for d in (1, -1, 2, -2, 3, -3, 4, -4):
        if (k + d) % 12 in pcs:
            pen = abs(d) + (1.5 if any(abs(k + d - x) % 12 in (1, 11) for x in avoid) else 0)
            if best is None or pen < best[0]:
                best = (pen, d)
    return best[1] if best else None


def find_clashes(m, tolerance="auto", genre=None, start=0, end=None, kinds=None):
    """All issues (unranked, unlimited): [{severity, kind, where, parts, notes, why, fix, score, ...}].
    Cached on the model per (tolerance, genre, range, kinds); callers get their own copies of the issue dicts."""
    ck = ("clashes", str(tolerance), genre, start, m["end"] if end is None else end, tuple(sorted(kinds or ())))
    hit = m["_c"].get(ck)
    if hit is None:
        hit = m["_c"][ck] = _find_clashes(m, tolerance, genre, start, end, kinds)
    return [dict(i) for i in hit]


def _find_clashes(m, tolerance="auto", genre=None, start=0, end=None, kinds=None):
    rules = data("rules"); tname, tol = tolerance_for(m, tolerance, genre)
    P, seg_t, strong_t = m["P"], m["seg_t"], m["strong_t"]
    pen = 0.0 if tname == "jazz" else 0.06
    span = lambda a_, b_, ex=frozenset(): chord_span(m, a_, min(b_, a_ + 2 * P), ex, pen)
    end = m["end"] if end is None else end
    if "maxlen" not in m["_c"]:
        m["_c"]["maxlen"] = max((n[LEN] for n in m["pitched"]), default=0)
    ns = [n for n in _slice(m, start - m["_c"]["maxlen"], end, pitched=True) if n[POS] < end and n[POS] + n[LEN] > start]
    nsc = {}                                                      # the same notes per channel (time order)
    for n in ns:
        nsc.setdefault(n[CH], []).append(n)
    want = set(kinds) if kinds else None
    groups = {}                                                   # key -> {score, ticks, notes, ...}

    def add(key, score, tick, n_list, **info):
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"score": 0.0, "ticks": [], "notes": [], **info}
        g["score"] += score; g["ticks"].append(tick); g["notes"] += n_list
        for k, v in info.items():
            if k.endswith("_set"):
                g[k] |= v
    lims = rules["_limits"]
    culprits = set()
    bass_chs = {ch for ch, p in m["parts"].items() if p["role"] == "bass"}
    nxt_bass = {}
    for ch in bass_chs:
        bn_ = _by_ch(m, True).get(ch, [])
        for a_, b_ in zip(bn_, bn_[1:]):
            nxt_bass[a_] = b_
    pass_lim = float(tol.get("bass_passing_beats", 0) or 0)

    def passing(n):
        """A short bass note that moves by step (1-2 semitones) straight into a chord tone: chromatic approach,
        walking-bass passing tone, neighbour. Idiomatic in groove / jazz styles (tolerance bass_passing_beats)."""
        if not pass_lim or n[CH] not in bass_chs or n[LEN] > pass_lim * P + P // 24:
            return False
        nx = nxt_bass.get(n)
        if nx is None or nx[POS] - (n[POS] + n[LEN]) > P // 2 or not 0 < abs(nx[KEY] - n[KEY]) <= 2:
            return False
        c2, _ = span(nx[POS], nx[POS] + max(nx[LEN], P // 2), frozenset({n[CH]}))
        return c2 is None or nx[KEY] % 12 in _allowed(c2, tol)
    # ---- simultaneous pairs: rubs, voicings, mud
    # Sounding notes indexed by key: only intervals that can matter (the mud limits' intervals and the rubs 1/6/11
    # semitones, +1 octave) are looked at, in the order the notes started (same result as checking every pair).
    dists = sorted({d for d in lims if d > 0} | {1, 6, 11, 13, 18, 23})
    by_key, heap, idx = {}, [], 0
    for n in ns:
        while heap and heap[0][0] <= n[POS]:
            _, i_, k_ = heapq.heappop(heap)
            del by_key[k_][i_]
        k = n[KEY]
        cand = []
        for d in dists:
            for kk in (k - d, k + d):
                bk = by_key.get(kk)
                if bk:
                    cand.extend(bk.items())
        if len(cand) > 1:
            cand.sort(key=lambda c_: c_[0])
        for _, x in cand:
            ov = min(x[POS] + x[LEN], n[POS] + n[LEN]) - n[POS]
            if ov <= 0:
                continue
            ovb = ov / P; lo, hi = (x, n) if x[KEY] <= n[KEY] else (n, x)
            iv = hi[KEY] - lo[KEY]; ic = iv % 12; velf = (x[VEL] + n[VEL]) / 200
            # mud: close intervals low down (same part too)
            if 0 < iv in lims and lo[KEY] < lims[iv] and ovb >= 0.5:
                # the limits are a soft guide: a semitone under one is borderline, compound intervals (9ths) blur less
                below = min(1.5, (lims[iv] - lo[KEY]) / 4)
                sec = section_of(m, n[POS])
                add(("mud", sec["name"] if sec else ""), min(ovb, 4) * velf * (0.15 + 0.55 * below)
                    * (0.6 if iv > 12 else 1.0) * (0.3 if passing(lo) or passing(hi) else 1.0) * tol["scale"],
                    n[POS], [lo, hi], pairs=[], sec=sec["name"] if sec else "")
                groups[("mud", sec["name"] if sec else "")]["pairs"].append((lo, hi))
            if x[CH] == n[CH] or iv == 0 or iv > 24 or ic not in (1, 6, 11):
                continue
            if ovb < 0.25:
                continue
            t0 = n[POS]; onbeat = t0 % P < max(1, P // 24)
            if ovb < 0.75 and not onbeat:
                continue                                           # passing tone
            strong = _off_strong(m, t0) < max(1, P // 24)
            base = {1: 1.0, 11: 0.6, 6: 0.45}[ic] * (1.0 if iv < 12 else (0.6 if iv < 24 else 0.35))
            reg = 1.0 if iv >= 12 else (2.0 if lo[KEY] < 48 else (1.3 if lo[KEY] < 60 else (0.8 if lo[KEY] > 84 else 1.0)))
            c, bass = span(t0, t0 + ov)
            pcs = _allowed(c, tol)
            a_in, b_in = lo[KEY] % 12 in pcs, hi[KEY] % 12 in pcs
            f = 1.0
            if a_in and b_in:
                f = tol["explained_low"] if lo[KEY] < 48 and iv < 12 else tol["explained"]
                if ic == 6 and c and c[1] in ("7", "9", "13", "7b9", "dim", "dim7", "m7b5"):
                    f *= 0.2                                       # the dominant / diminished tritone
            score = base * reg * velf * min(ovb, 4) * (1.0 if strong else 0.8) * f * tol["scale"]
            if score < 0.05:
                continue
            if a_in and b_in:
                if iv < 12 and (ic == 1 or lo[KEY] < 60) or tname == "strict":
                    add(("voicing", lo[CH], hi[CH], lo[KEY] % 12, hi[KEY] % 12), score, t0, [lo, hi], chord=c, ic=ic,
                        a=lo, b=hi)
                continue
            if a_in != b_in:
                cul, oth = (hi, lo) if a_in else (lo, hi)
            else:
                cul, oth = sorted((lo, hi), key=lambda z: z[VEL] * z[LEN])[0], None
                oth = hi if cul is lo else lo
            if passing(cul):
                continue                                           # a bass approach / passing note
            culprits.add(cul)
            add(("rub", cul[CH], cul[KEY]), 0.0, t0, [cul], others_set=set(), ivs_set=set(), per={})
            g = groups[("rub", cul[CH], cul[KEY])]
            g["per"].setdefault(cul, []).append(score)
            g["others_set"].add((oth[CH], oth[KEY])); g["ivs_set"].add(ic if iv < 12 else ic + 12)
            g.setdefault("chord", c)
        by_key.setdefault(k, {})[idx] = n
        heapq.heappush(heap, (n[POS] + n[LEN], idx, k))
        idx += 1
    # ---- melody against the chord
    mel = melody_line(m) if m.get("melody") else []
    mch = m["melody"]["ch"] if m.get("melody") else None
    for i, n in enumerate(mel):
        if not (start <= n[POS] < end) or n in culprits:
            continue
        lb = n[LEN] / P
        if lb < tol["nct_beats"] or _off_strong(m, n[POS]) >= max(1, P // 24):
            continue
        excl = frozenset({mch})
        c, bass = span(n[POS], n[POS] + n[LEN], excl)
        if not c or c[3] < 0.8:
            continue                                               # no clear harmony under it: can't judge
        pcs = _allowed(c, tol)
        if n[KEY] % 12 in pcs:
            continue
        ca, _ = span(n[POS], n[POS] + n[LEN])
        if ca and n[KEY] % 12 in ca[2] and len(c[2] - ca[2]) <= 1 and ca[3] >= 0.75:
            continue                                               # melody + accompaniment = one clear chord
        if tname != "strict" and is_chord_set(c[2] | {n[KEY] % 12}):
            continue                                               # it extends the chord into another one (C + B = Cmaj7)
        nxt = mel[i + 1] if i + 1 < len(mel) else None
        if nxt and nxt[POS] - (n[POS] + n[LEN]) <= P and 0 < abs(nxt[KEY] - n[KEY]) <= 2:
            c2, _ = span(nxt[POS], nxt[POS] + nxt[LEN], excl)
            if c2 and nxt[KEY] % 12 in c2[2] and (tname != "strict" or lb < 2):
                continue                                           # appoggiatura / suspension that resolves by step
        chromatic = gk_scale(m, n[POS]) and n[KEY] % 12 not in gk_scale(m, n[POS])
        score = min(lb, 4) * (n[VEL] / 100) * (1.3 if n[POS] - _bar_start(m, _bar_idx(m, n[POS])) == 0 else 1.0) * (1.3 if chromatic else 1.0) \
            * tol["scale"]
        add(("melody_nct", n[CH], n[KEY], chord_name(c)), score, n[POS], [n], chord=c)
    # ---- bass against the chord
    for ch, p in m["parts"].items():
        if p["role"] != "bass":
            continue
        excl = frozenset({ch})
        bn = nsc.get(ch, [])
        prev = None
        for n in bn:
            lb = n[LEN] / P
            if lb < 1 and not (_off_strong(m, n[POS]) < max(1, P // 24) and lb >= 0.5):
                prev = n; continue
            c, _ = span(n[POS], n[POS] + n[LEN], excl)
            fifth = (c[0] + 7) % 12 if c and c[1] not in ("dim", "dim7", "m7b5", "aug", "aug7") else None
            if not c or c[3] < 0.8 or n[KEY] % 12 in c[2] or n[KEY] % 12 == fifth or \
                    (c[1] == "5" and (n[KEY] - c[0]) % 12 in (3, 4)) or passing(n):
                prev = n; continue                                 # chord tone (an omitted 5th still is), or passing
            r = c[0]; pc = n[KEY] % 12
            if tol.get("slash9_ok") and pc in ((r + 2) % 12, (r + 10) % 12, (r + 11) % 12):
                prev = n; continue                                 # F/G, C/Bb: slash chords with a 9th / 7th bass
            cw, _ = span(n[POS], n[POS] + n[LEN])
            if cw and cw[0] == pc and len(c[2] - cw[2]) <= 1:
                prev = n; continue                                 # the bass re-roots the chord (E-G-B over C = Cmaj7)
            if tname != "strict":
                w = pcs_span(m, n[POS], n[POS] + n[LEN], excl); tot = sum(w) or 1.0
                if w[(pc + 1) % 12] + 0.5 * w[(pc + 6) % 12] < 0.08 * tot and w[pc] + w[(pc + 7) % 12] > 0:
                    prev = n; continue                             # no b9 / #11 above it: a consonant slash chord
            held = n[LEN] >= 2 * seg_t or (prev is not None and prev[KEY] % 12 == pc and n[POS] - prev[POS] <= 2 * seg_t)
            ce, _ = span(max(n[POS], n[POS] + n[LEN] - P), n[POS] + n[LEN], excl)
            sk = key_estimate(m, 0, None, 1)
            tonic = parse_key(sk[0][0])[0] if sk else None
            moving = ce is not None and ce[:2] != c[:2]
            if held and tol.get("pedal_ok") and (moving and pc in ce[2] or tonic is not None and pc in
                                                 (tonic, (tonic + 7) % 12)):
                prev = n; continue                                 # pedal point: tonic / dominant, or held under a change
            score = min(lb, 4) * (n[VEL] / 100) * 1.4 * tol["scale"]
            add(("bass_root", ch, pc, chord_name(c)), score, n[POS], [n], chord=c)
            prev = n
    # ---- support layers above the melody (voice crossing), bass/melody octaves
    if mel and (not want or want & {"crossing", "octaves"}):
        mel_in = [n for n in mel if start <= n[POS] < end]
        for ch, p in m["parts"].items():
            if ch == mch or p["role"] not in SUPPORT_ROLES + ("perc",) or p["role"] == "perc":
                continue
            sn = nsc.get(ch, [])
            j = 0
            for x in mel_in:
                while j < len(sn) and sn[j][POS] + sn[j][LEN] <= x[POS]:
                    j += 1
                k = j
                while k < len(sn) and sn[k][POS] < x[POS] + x[LEN]:
                    s = sn[k]; k += 1
                    ov = (min(s[POS] + s[LEN], x[POS] + x[LEN]) - max(s[POS], x[POS])) / P
                    if s[KEY] > x[KEY] and ov >= 1:
                        sec = section_of(m, x[POS])
                        louder = s[VEL] >= x[VEL]
                        add(("crossing", ch, sec["name"] if sec else ""), min(ov, 4) * 0.25 * (2.0 if louder else 0.6)
                            * tol["scale"], x[POS], [s], mel=[], louder_n=0)
                        g = groups[("crossing", ch, sec["name"] if sec else "")]
                        g["louder_n"] += 1 if louder else 0; g["mel"].append(x)
        for ch, p in m["parts"].items():
            if p["role"] != "bass":
                continue
            bon = {n[POS]: n for n in nsc.get(ch, [])}
            run = []
            for x1, x2 in zip(mel_in, mel_in[1:]):                 # consecutive melody notes, both doubled
                b1, b2 = bon.get(x1[POS]), bon.get(x2[POS])
                if b1 and b2 and (x1[KEY] - b1[KEY]) % 12 == 0 and (x2[KEY] - b2[KEY]) % 12 == 0 and x2[KEY] != x1[KEY] and \
                        (x2[KEY] - x1[KEY]) * (b2[KEY] - b1[KEY]) > 0:
                    run.append(x2[POS])
            if len(run) >= 3:
                add(("octaves", ch), 0.0, run[0], [], ticks_all=run)
                groups[("octaves", ch)]["ticks"] = run
    # ---- kick vs bass (kick pitch from the journal: channels.N.pitch)
    bass = [n for n in ns if m["parts"][n[CH]]["role"] == "bass"]
    bpos = [n[POS] for n in bass]; blen = max((n[LEN] for n in bass), default=0)
    for kc, kp in m["parts"].items():
        if kp["role"] != "drums" or kp.get("pitch") is None:
            continue
        try:
            kpc = FM.parse_note(kp["pitch"]) % 12
        except Exception:
            continue
        kicks = [n[POS] for n in _by_ch(m).get(kc, []) if start <= n[POS] < end]
        for t in kicks:                                            # bass notes sounding at t (start order)
            for n in bass[bisect.bisect_left(bpos, t - blen + 1):bisect.bisect_right(bpos, t)]:
                if n[POS] <= t < n[POS] + n[LEN] and n[KEY] < 55 and (n[KEY] - kpc) % 12 in (1, 6, 11):
                    add(("kick_bass", n[CH], n[KEY] % 12), 0.5 * n[VEL] / 100 * tol["scale"], t, [n], kick=kc)
    # ---- ranges
    for ch, p in m["parts"].items():
        if p["role"] == "drums":
            continue
        ins = p.get("_ins") if (p.get("_ins") or {}).get("_range") else None
        rr = ins or data("instruments")["roles"].get(p["role"])
        if not rr or not rr.get("_range"):
            continue
        lo_, hi_ = rr["_range"]; glo, ghi = rr.get("_good", rr["_range"])
        pn = nsc.get(ch, [])
        for n in pn:
            if n[KEY] < lo_ or n[KEY] > hi_:
                add(("out_of_range", ch, "low" if n[KEY] < lo_ else "high"), 1.5, n[POS], [n], rng=rr, ins=ins)
            elif ins and (n[KEY] < glo or n[KEY] > ghi):
                add(("weak_register", ch, "low" if n[KEY] < glo else "high"), 0.12 * min(4, n[LEN] / P) + 0.08,
                    n[POS], [n], rng=rr, ins=ins)
    # ---- crowded registers per section
    crowd_n = int(rules.get("crowd_parts", 4))
    npos = [n[POS] for n in ns]
    for s in m["sections"]:
        if s["b"] <= start or s["a"] >= end:
            continue
        meds = []
        per = {}                                                   # this section's notes, bucketed once
        for n in ns[bisect.bisect_left(npos, s["a"]):bisect.bisect_left(npos, s["b"])]:
            per.setdefault(n[CH], []).append(n[KEY])
        for ch, p in m["parts"].items():
            if p["role"] in ("drums", "perc"):
                continue
            sn = per.get(ch, [])
            if len(sn) >= max(2, (s["b"] - s["a"]) / m["bar_t"]):
                meds.append((_median(sn), ch))
        meds.sort()
        best = []
        for i in range(len(meds)):
            win = [x for x in meds if meds[i][0] <= x[0] < meds[i][0] + 12]
            if len(win) > len(best):
                best = win
        if len(best) >= crowd_n:
            add(("crowd", s["name"]), 0.5 + 0.3 * (len(best) - crowd_n), s["a"], [], parts_l=[c for _, c in best],
                lo=best[0][0], hi=best[-1][0])
    return _issues_from(m, groups, rules, tname, want)


def gk_scale(m, tick):
    """Scale pitch classes of the song key (cached)."""
    if "scale" not in m["_c"]:
        k = key_estimate(m, 0, None, 1)
        m["_c"]["scale"] = FM.scale_set(k[0][0]) if k else None
    return m["_c"]["scale"]


def _issues_from(m, groups, rules, tname, want):
    out = []
    for key, g in groups.items():
        kind = key[0]
        if want and kind not in want:
            continue
        if g.get("per"):                                           # one wrong note against several: worst + a little
            g["score"] = sum(max(v) + 0.25 * (sum(v) - max(v)) for v in g["per"].values())
        sc = g["score"]; sev = _sev(sc, rules); iss = None
        if kind == "rub":
            if not sev:
                continue
            ch, k = key[1], key[2]
            cul = g["notes"]; c = g.get("chord")
            excl_c, _ = chord_span(m, cul[0][POS], cul[0][POS] + m["P"], frozenset({ch}))
            tones = excl_c[2] if excl_c else (c[2] if c else frozenset())
            d = _nearest_tone(k, tones, [o[1] for o in g["others_set"]])
            ivn = {1: "m2", 11: "M7", 6: "tritone", 13: "m9", 23: "M7+8va", 18: "tritone+8va"}
            others = sorted(g["others_set"])[:3]
            fix = (f"{nn(k)} -> {nn(k + d)} (chord tone of {chord_name(excl_c)}): "
                   + _edit_hint(m, ch, cul, [{"op": "transpose", "semitones": d}])) if d else \
                f"leave {nn(k)} out of {_pname(m, ch)} there or re-voice the chord"
            iss = {"kind": "rub", "parts": f"{_pname(m, ch)} vs " + ", ".join(sorted({_pname(m, o[0]) for o in others})),
                   "notes": f"{nn(k)} vs " + ",".join(nn(o[1]) for o in others) + " (" +
                   "/".join(ivn.get(i, str(i)) for i in sorted(g["ivs_set"])) + ")",
                   "why": f"{nn(k)} is not in the {chord_name(excl_c or c)} chord; it grinds against held notes"
                          + (" low down" if k < 60 else ""),
                   "fix": fix}
        elif kind == "voicing":
            if not sev:
                continue
            a, b = g["a"], g["b"]; ic = g["ic"]
            mover = b if m["parts"][b[CH]]["role"] not in ("melody", "bass") else a
            how = (f"move {_pname(m, mover[CH])} {nn(mover[KEY])} {'up' if mover is b else 'down'} an octave "
                   f"(a {'M7/9th' if ic == 1 else 'wider interval'} instead of a {'2nd' if ic == 1 else 'close rub'})")
            iss = {"kind": "voicing", "parts": f"{_pname(m, a[CH])} vs {_pname(m, b[CH])}",
                   "notes": f"{nn(a[KEY])} vs {nn(b[KEY])} ({ {1: 'm2', 11: 'M7', 6: 'tritone'}[ic]})",
                   "why": f"both are chord tones of {chord_name(g.get('chord'))}, but voiced this close"
                          + (" in a low register" if a[KEY] < 60 else "") + f" they rub ({tname} tolerance)",
                   "fix": how + ": " + _edit_hint(m, mover[CH], [mover], [{"op": "transpose", "octaves": 1 if mover is b else -1}])}
        elif kind == "melody_nct":
            if not sev:
                continue
            ch, k, cn = key[1], key[2], key[3]; c = g.get("chord")
            d = _nearest_tone(k, c[2]) if c else None
            iss = {"kind": "melody_nct", "parts": f"{_pname(m, ch)} melody",
                   "notes": f"{nn(k)} over {cn}",
                   "why": f"held on a strong beat, not a chord tone of {cn}, no step resolution",
                   "fix": (f"{nn(k)} -> {nn(k + d)} (chord tone): " + _edit_hint(m, ch, g["notes"], [{"op": "transpose", "semitones": d}])
                           if d else "change the chord under it") + "; or shorten it into a passing note"}
        elif kind == "bass_root":
            if not sev:
                continue
            ch, pc, cn = key[1], key[2], key[3]; c = g.get("chord")
            k = g["notes"][0][KEY]
            root_d = min(((c[0] - pc) % 12, -((pc - c[0]) % 12)), key=abs) if c else 0
            iss = {"kind": "bass_root", "parts": f"{_pname(m, ch)} bass",
                   "notes": f"{NAMES[pc]} under {cn}",
                   "why": f"bass {NAMES[pc]} is not in {cn} (not root, inversion or pedal)",
                   "fix": f"play the root {NAMES[c[0]]}: "
                          + _edit_hint(m, ch, g["notes"], [{"op": "transpose", "semitones": root_d}])
                          + f", or change the chord above to one with {NAMES[pc]}"}
        elif kind == "mud":
            if not sev:
                continue
            pairs = g["pairs"]
            cnt = {}
            for lo, hi in pairs:
                if m["parts"][hi[CH]]["role"] != "bass":
                    cnt[(hi[CH], hi[KEY])] = cnt.get((hi[CH], hi[KEY]), 0) + 1
                else:
                    cnt[(lo[CH], lo[KEY])] = cnt.get((lo[CH], lo[KEY]), 0) + 1
            (wc, wk), _ = max(cnt.items(), key=lambda kv: kv[1]) if cnt else ((pairs[0][1][CH], pairs[0][1][KEY]), 0)
            ex = pairs[0]
            iv = ex[1][KEY] - ex[0][KEY]
            iss = {"kind": "mud", "parts": ", ".join(sorted({_pname(m, x[CH]) for p_ in pairs[:20] for x in p_}))[:80],
                   "notes": f"e.g. {nn(ex[0][KEY])}+{nn(ex[1][KEY])} ({iv} st); {len(pairs)} close low pairs",
                   "why": "intervals this close below their low limit (3rds above C3, 5ths above Bb1) blur into mud",
                   "fix": f"drop {_pname(m, wc)}'s {nn(wk)} or move it up an octave: "
                          + _edit_hint(m, wc, [x for p_ in pairs for x in p_ if x[CH] == wc and x[KEY] == wk] or [ex[1]],
                                       [{"op": "transpose", "octaves": 1}])}
        elif kind == "crossing":
            louder = g["louder_n"] / max(1, len(g["notes"]))
            sev = "med" if louder >= 0.5 and sc >= 1.0 else ("low" if sc >= 0.5 else None)
            if not sev:
                continue
            ch = key[1]; mk = min(x[KEY] for x in g["mel"])
            iss = {"kind": "crossing", "parts": f"{_pname(m, ch)} above the melody {_pname(m, m['melody']['ch'])}",
                   "notes": f"{len(g['notes'])} notes above it (top {nn(max(n[KEY] for n in g['notes']))})",
                   "why": "a support layer above the melody" + (" and as loud or louder" if louder >= 0.5 else "") +
                          " masks it / steals the top line",
                   "fix": f"move {_pname(m, ch)}'s notes above {nn(mk)} down an octave: "
                          + _edit_hint(m, ch, g["notes"], [{"op": "transpose", "octaves": -1}])
                          + ", or lower its velocity (ops=[{\"op\": \"velocity\", \"scale\": 0.75}])"}
        elif kind == "octaves":
            iss = {"kind": "octaves", "parts": f"{_pname(m, key[1])} (bass) and the melody", "notes": f"{len(g['ticks'])} moves in octaves",
                   "why": "the bass doubles the melody in parallel octaves (thins the harmony; fine if intended, e.g. unison riffs)",
                   "fix": "if unintended: give the bass chord roots / contrary motion there"}
            sev = "info"
        elif kind == "kick_bass":
            if not sev:
                continue
            kp = m["parts"][g["kick"]].get("pitch")
            iss = {"kind": "kick_bass", "parts": f"{_pname(m, g['kick'])} (kick, tuned {kp}) vs {_pname(m, key[1])}",
                   "notes": f"bass {NAMES[key[2]]} on kick hits",
                   "why": "the kick's pitch rubs against the bass note under it (low-end beating)",
                   "fix": "tune the kick to the song's root/5th (channel pitch), or duck the bass on the kick (flp_duck)"}
        elif kind in ("out_of_range", "weak_register"):
            ch, side = key[1], key[2]; rr = g["rng"]; ins = g.get("ins")
            if kind == "weak_register" and not sev:
                continue
            sev = "med" if kind == "out_of_range" else "low"
            ks = sorted({n[KEY] for n in g["notes"]})
            lo_, hi_ = rr["_range"] if kind == "out_of_range" else rr.get("_good", rr["_range"])
            far = (lo_ - ks[0]) if side == "low" else (ks[-1] - hi_)
            octs = -(-far // 12) * (1 if side == "low" else -1)
            label = ins["id"] if ins else f"{m['parts'][ch]['role']} role"
            iss = {"kind": kind, "parts": _pname(m, ch),
                   "notes": ",".join(nn(k) for k in ks[:6]) + (f" (+{len(ks) - 6})" if len(ks) > 6 else ""),
                   "why": (f"{'below' if side == 'low' else 'above'} the {label}'s "
                           f"{'playable range' if kind == 'out_of_range' else 'good register'} "
                           f"{nn(lo_)}-{nn(hi_)}") + (" (range from general knowledge; check the library)"
                                                       if ins and ins.get("uncertain") else "")
                          + (" - the sample may be silent or the note sounds unnatural" if kind == "out_of_range" else ""),
                   "fix": f"move them {abs(octs)} octave{'s' if abs(octs) > 1 else ''} {'up' if octs > 0 else 'down'} "
                          f"({nn(ks[0])} -> {nn(ks[0] + 12 * octs)}): "
                          + _edit_hint(m, ch, g["notes"], [{"op": "transpose", "octaves": octs}])}
        elif kind == "crowd":
            if not sev:
                continue
            iss = {"kind": "crowd", "parts": ", ".join(_pname(m, c) for c in g["parts_l"]),
                   "notes": f"all centred {nn(int(g['lo']))}-{nn(int(g['hi']))}",
                   "why": f"{len(g['parts_l'])} layers in one octave in {key[1]}: they mask each other",
                   "fix": "move one an octave (pad up / chords down), thin the voicings, or drop a layer there "
                          "(flp_edit_notes op transpose octaves=±1)"}
        if iss and kind in ("rub", "melody_nct", "bass_root", "voicing") and sev in ("high", "med"):
            bars_ = {_bar(m, n[POS]) for n in g["notes"]}
            secs_ = {(section_of(m, n[POS]) or {}).get("name") for n in g["notes"]}
            if len(bars_) >= 3 and len(secs_) >= 2:                # the same dissonance all over the song: a colour
                sev = "low"
                iss["why"] += f" - but it recurs in {len(bars_)} bars across sections, so it is probably an intended " \
                              "colour: listen before changing it"
        if iss:
            iss["severity"] = sev
            iss["where"] = _where(m, [n[POS] for n in g["notes"]] if kind in ("rub", "melody_nct", "bass_root",
                                                                               "out_of_range", "weak_register")
                                  else g["ticks"])
            iss["score"] = round(sc, 2)
            iss["_t"] = min(g["ticks"])
            out.append(iss)
    out.sort(key=lambda i: (SEV[i["severity"]], -i["score"], i["_t"]))
    for i in out:
        i.pop("_t", None)
    return out


def clashes(project, section=None, bars=None, tolerance="auto", genre=None, limit=10, min_severity="low", kinds=None,
            full=False, journal_from=None):
    """Ranked note-level problems between layers, each with a concrete fix."""
    t0 = time.time()
    m = load_model(project, journal_from); t1 = time.time()
    a, b, label = _range_arg(m, section, bars)
    tname, _ = tolerance_for(m, tolerance, genre)
    allx = find_clashes(m, tolerance, genre, a, b, kinds)
    cut = SEV.get(min_severity, 2)
    keep = [i for i in allx if SEV[i["severity"]] <= cut or (i["severity"] == "info" and full)]
    info = [i for i in allx if i["severity"] == "info"]
    if not full:
        for i in keep:
            i.pop("score", None)
    lim = int(limit or 10)
    res = {"issues": keep[:lim], "more": max(0, len(keep) - lim),
           "counts": {s: sum(1 for i in allx if i["severity"] == s) for s in ("high", "med", "low")},
           "tolerance": tname, "melody_part": _pname(m, m["melody"]["ch"]) if m.get("melody") else None,
           "range": label or "whole song",
           "verdict": "fix the high ones first" if any(i["severity"] == "high" for i in allx) else
           ("minor issues only" if keep else "no clashes found (a human still needs to listen)")}
    if info and not full:
        res["info"] = [f"{i['parts']}: {i['notes']} ({i['where']})" for i in info[:3]]
    res["roles"] = {ch: p["role"] + ("?" if p.get("role_guessed") else "") for ch, p in sorted(m["parts"].items())}
    if len(res["roles"]) > PARTS_GROUP and not full:              # big songs: only the parts named in the issues
        named = {int(x) for i in res["issues"] for x in re.findall(r"\(ch(\d+)\)", str(i.get("parts", "")))}
        res["roles"] = {ch: r for ch, r in res["roles"].items() if ch in named or ch == (m.get("melody") or {}).get("ch")}
        res["roles_note"] = f"{len(m['parts'])} parts: roles shown for the ones in these issues (full=true: all)"
    if m.get("far"):
        res["warning"] = _far_text(m)
    if m.get("offsets"):
        res["sounding_pitch"] = ("pitched samplers are judged at the pitch they sound (written = sounding "
                                 + ", ".join(f"{o:+d} on ch{c}" for c, o in sorted(m["offsets"].items())) + ")")
    res["steps_s"] = {"model": round(t1 - t0, 3), "detect": round(time.time() - t1, 3)}
    return res


# ============================================================================= entrances (abrupt layers)
def entrances(project, all=False):
    """Layers that come in (after >= silence_bars of rest) with first notes much louder than what follows and no
    velocity ramp. -> [{ch, part, bar, tick, first_vel, next_vel, ramp, automation, section_start, flagged, why,
    fix}] (flagged ones only unless all=true)."""
    m = load_model(project); rules = data("rules")["abrupt"]; P, bar_t = m["P"], m["bar_t"]
    gap = rules["silence_bars"] * bar_t; out = []
    for ch, p in m["parts"].items():
        if p["role"] in ("drums",):
            continue
        ns = [n for n in m["notes"] if n[CH] == ch]
        last_end = None
        for i, n in enumerate(ns):
            if last_end is not None and n[POS] - last_end < gap:
                last_end = max(last_end, n[POS] + n[LEN]); continue
            last_end = max(last_end or 0, n[POS] + n[LEN])
            first = [x for x in ns[i:] if x[POS] < n[POS] + P // 2]
            nxt = [x for x in ns[i:] if n[POS] + P // 2 <= x[POS] < n[POS] + 4 * bar_t]
            if len(nxt) < 2:
                continue
            fv = sum(x[VEL] for x in first) / len(first); av = sum(x[VEL] for x in nxt) / len(nxt)
            onsets = sorted({x[POS] for x in ns[i:i + 12]})[:4]
            ovel = [max(x[VEL] for x in ns if x[POS] == t) for t in onsets]
            ramp = len(ovel) >= 3 and ovel[0] + 10 <= max(ovel[1:])
            sec = section_of(m, n[POS]); at_start = sec is not None and abs(n[POS] - sec["a"]) <= P
            auto = [a_["name"] for a_ in m.get("automation") or [] if a_["pos"] - bar_t <= n[POS] < a_["end"]]
            sustained = p["avg_len"] >= 1.5 or (p.get("_ins") or {}).get("family") in ("strings", "brass", "voice") \
                or p["role"] == "pad"
            loud = fv >= av + rules["vel_jump"]
            hard = sustained and not at_start and fv >= rules["full_vel"] and first[0][LEN] >= P
            flagged = (loud or hard) and not ramp
            why = (f"enters at v{fv:.0f}, then plays v{av:.0f}" if loud else f"a sustained layer enters mid-section at v{fv:.0f}") \
                + (" (an automation clip is active there - fine if it fades this layer in: " + ", ".join(auto[:2]) + ")" if auto else "")
            ins = p.get("insert")
            e = {"ch": ch, "part": _pname(m, ch), "bar": round(_tick_barf(m, n[POS]), 2), "tick": n[POS],
                 "first_vel": round(fv), "next_vel": round(av), "ramp": ramp, "automation": auto[:2],
                 "section_start": at_start, "section": sec["name"] if sec else None,
                 "flagged": flagged, "severity": ("med" if (loud and sustained and not auto) else "low") if flagged else None,
                 "why": why,
                 "fix": f"velocity ramp into it: " + _edit_hint(m, ch, first + nxt[:3], [{"op": "velocity", "ramp": [max(20, round(av) - 25), round(av)]}])
                        + ("; or a volume fade-in: " + call_hint("flp_add_automation", {
                            "project": Raw("project"), "output": Raw("project"), "track": ins, "param": "volume",
                            "points": [{"bar": max(1, round(_tick_barf(m, n[POS]) - 1, 2)), "value": 0.3},
                                       {"bar": round(_tick_barf(m, n[POS]) + 1, 2), "value": 0.8}]})
                           if ins not in (None, 0, -1)
                           else "; or a volume fade-in with flp_add_automation on its mixer track")}
            if flagged or all:
                out.append(e)
    out.sort(key=lambda e: (0 if e["severity"] == "med" else 1, e["tick"]))
    return out


# ============================================================================= compare a song to its source MIDI
# flp_compare_midi: a song (a rework, cover or arrangement) against the MIDI it came from. Each MIDI bar is mapped to
# the song by a shift in whole beats: its notes count where a song note of the same key sits within one 16th after the
# shift (humanized songs, unquantized sources, meter changes only one side has) - a Viterbi path with a switch penalty,
# so a new intro or a cut moves the map per region while the rest keeps one constant shift; a bar counts as found
# relative to its neighbours' match level. Notes then match on the same key within tolerance_beats. Out: missing source notes per song bar / section, new notes per
# channel, repeated "one finger" stand-ins, near-duplicate strikes, timing (onset offsets from the source in ms, split
# chords), velocity (spread, melody above the accompaniment) and the tempo map. restore=true writes the missing notes
# back into the pattern that plays there; remove= deletes stand-ins / duplicates / note ids. A pattern placed more
# than once is only edited where every placement wants the same edit (else reported, or split with shared='split').
# Thresholds: data/score/compare.json (user override <state folder>/score/compare.json).
CMP_DEFAULT = {"grid_beats": 0.25, "shift_bars": [-6, 14], "max_shift_bars": 64, "switch_penalty": 0.35,
               "min_bar_floor": 0.1, "min_bar_match": 0.35, "min_bar_relative": 0.25, "near_dup_beats": 0.12,
               "channel_fit": 0.75,
               "stand_in": {"min_strikes": 3, "within_bars": 2, "gap_bars": 1, "missing_near_beats": 1.0},
               "timing_ms": {"tight": 10, "natural": 25, "loose": 45}, "chord_beats": 0.03, "chord_split_ms": 30,
               "velocity": {"flat_sd": 2.5, "melody_margin": 4}, "tempo": {"steady_bpm": 0.5, "jump_bpm": 3},
               "limit": 12}
# a song note: tick, length, channel, sounding key, raw FL velocity, pattern, position in the pattern, clip, FL record
SN_T, SN_L, SN_C, SN_K, SN_V, SN_P, SN_PP, SN_CL, SN_R = range(9)
CMP_REMOVE_KINDS = {"stand_ins": "stand_ins", "stand-ins": "stand_ins", "standins": "stand_ins",
                    "near_duplicates": "near_duplicates", "near_dupes": "near_duplicates",
                    "duplicates": "near_duplicates", "repeated_new": "repeated_new"}
CMP_ID = re.compile(r"n(\d+)\.(\d+)\.(\d+)$")


def _cmp_limits():
    if "compare" in _DATA:
        return _DATA["compare"]
    d = json.loads(json.dumps(CMP_DEFAULT))
    for p in (os.path.join(HERE, "data", "score", "compare.json"), _user_file("compare.json")):
        if p and os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    _merge(d, json.load(f))
            except Exception as e:                                   # a broken file never breaks the tool
                d["_error"] = f"{p}: {e}"
    _DATA["compare"] = d
    return d


def _u16(r, i):
    return struct.unpack_from("<H", r, i)[0]


def _u32(r, i):
    return struct.unpack_from("<I", r, i)[0]


def _cmp_bar_starts(meters, P, end):
    """Bar start ticks through the meter map, from bar 1 to the first bar starting after `end`."""
    out = [0]; b = 1
    while out[-1] <= end and len(out) < MAX_BARS:
        b += 1; out.append(int(round(H["_bar_to_tick"](b, meters, P))))
    return out


def _cmp_read_song(project, journal_from=None):
    """One parse: notes as the playlist plays them (with clip, pattern, record), clips, placements in other
    arrangements, tempo map, meter map, channel names, sounding offsets."""
    d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
    pats = H["_patterns"](d, ev)
    PB = H["PATTERN_BASE"]; CM = H.get("CLIP_MUTE", 0x2000); SL = H.get("NOTE_SLIDE", SLIDE_FLAG)
    recs = H["_playlist_at"](d, ev)[2]
    clips = []
    for i, r in enumerate(recs):
        item = _u16(r, 6)
        if _u16(r, 4) != PB or item < PB or item - PB not in pats:
            continue
        clips.append({"i": i, "pid": item - PB, "pos": _u32(r, 0), "len": _u32(r, 8), "s0": H["_clip_offsets"](r)[0],
                      "muted": bool(_u16(r, 18) & CM)})
    other = {}
    try:
        cur = H["_arrangement"](d, ev)
        for a in H["_arrangements"](d, ev):
            if cur is not None and a["index"] == cur["index"]:
                continue
            for r in H["_playlist_at"](d, ev, a["index"])[2]:
                if _u16(r, 6) >= PB:
                    other[_u16(r, 6) - PB] = other.get(_u16(r, 6) - PB, 0) + 1
    except Exception:                                              # noqa: BLE001 - other arrangements are a bonus
        pass
    jr = _journal(journal_from or project); offs = sounding_offsets(jr)
    notes = []
    for c in clips:
        if c["muted"]:
            continue
        s0, ln = c["s0"], c["len"]
        for r in pats[c["pid"]]["notes"]:
            if s0 <= r[0] < s0 + ln and not r[1] & SL:              # slide notes are bends of a held note
                notes.append((c["pos"] + r[0] - s0, max(1, min(r[3], s0 + ln - r[0])), r[2],
                              max(0, r[4] - offs.get(r[2], 0)), r[11], c["pid"], r[0], c["i"], r))
    notes.sort(key=lambda n: (n[SN_T], n[SN_C], n[SN_K]))
    tempos = sorted(H["_song_x"](d, ev)[1]) or [(0, 120.0)]
    return {"d": d, "ev": ev, "P": P, "pats": pats, "clips": clips, "other_arr": other, "notes": notes,
            "tempos": tempos, "meters": H["_meter_map"](d, ev), "names": H["_channel_display_names"](d),
            "offs": offs, "journal": jr}


def _cmp_channels(channels, S_, present):
    names = S_["names"]; jch = (S_["journal"] or {}).get("channels") or {}
    if channels in (None, "", []):
        rx = dict(NAME_ROLES)["drums"]
        out = [c for c in present if not re.search(rx, names.get(c) or "", re.I)
               and ((jch.get(str(c)) or {}).get("role") if isinstance(jch.get(str(c)), dict) else None) != "drums"]
        return out or list(present)
    out = []
    for x in (channels if isinstance(channels, (list, tuple)) else [channels]):
        if isinstance(x, int) or (isinstance(x, str) and x.strip().isdigit()):
            out.append(int(x)); continue
        low = str(x).strip().lower()
        hit = [c for c, nm in names.items() if (nm or "").lower() == low] or \
              [c for c, nm in names.items() if low in (nm or "").lower()]
        if not hit:
            raise ValueError(f"no channel named '{x}' (channels: {[f'{c}: {n}' for c, n in sorted(names.items())][:24]})")
        out += hit
    return sorted(set(out))


def _cmp_sections(project, S_, journal_from=None):
    """[(start tick, name)] from the journal's sections, else named playlist markers, else the score model's
    sections (pattern names / 8-bar blocks); -> (sections, source)."""
    P, meters = S_["P"], S_["meters"]; out = []; src = "journal"
    for s in (S_["journal"] or {}).get("sections") or []:
        if isinstance(s, dict) and s.get("bar") is not None:
            try:
                out.append((int(round(H["_bar_to_tick"](float(s["bar"]), meters, P))),
                            str(s.get("name") or f"bar {s['bar']}")))
            except (TypeError, ValueError):
                pass
    if not out:
        src = "markers"
        try:
            out = [(mk["tick"], mk["name"]) for mk in H["_markers"](S_["d"], S_["ev"])
                   if not mk["signature"] and (mk["name"] or "").strip()]
        except Exception:                                          # noqa: BLE001
            out = []
    if not out:
        try:
            m = load_model(project, journal_from)
            out = [(s["a"], s["name"]) for s in m["sections"]]; src = (m["sections"] or [{}])[0].get("src") or "model"
        except Exception:                                          # noqa: BLE001
            out = []
    if not out:
        out, src = [(0, "song")], "none"
    out.sort(); seen = {}; named = []
    for t, nm in out:
        c = seen.get(nm, 0); seen[nm] = c + 1
        named.append((t, f"{nm}#{c + 1}" if c else nm))
    return named, src


def _cmp_tempo_fn(tempos):
    ts = [t for t, _ in tempos]; bs = [b for _, b in tempos]

    def bpm_at(t):
        j = bisect.bisect_right(ts, t) - 1
        b = bs[max(0, j)]
        return b if b and b > 0 else 120.0
    return bpm_at


def _cmp_align(src, src_bar, bs_src, n_src_bar, song_ticks, by_key, s_end, P, step, bpb, L):
    """Viterbi over shifts in whole beats. A MIDI bar's score at a shift: its notes that find a song note of the same
    key within one grid step after the shift (tolerant: humanized songs, unquantized sources, meters that differ),
    2*m / (MIDI notes + song notes in the shifted window). Staying on one shift is preferred (switch penalty), so a
    constant shift wins over scattered best guesses. -> (shift in ticks or None per MIDI bar, its score)."""
    nsrc = len(bs_src) - 1
    nt = max(1, int(math.ceil(s_end / (bpb * P))))
    lo, hi = float(L["shift_bars"][0]), float(L["shift_bars"][1]); cap = int(L.get("max_shift_bars") or 64)
    hi = max(hi, min(cap, nt - nsrc + 4)); lo = min(lo, -min(cap, max(0, nsrc - nt + 4)))
    lo_b, hi_b = int(math.floor(lo * bpb)), int(math.ceil(hi * bpb))
    pen = float(L["switch_penalty"]); eps = 1e-4
    hits = [dict() for _ in range(nsrc)]                           # MIDI bar -> {shift in beats: matched notes}
    for si, s in enumerate(src):
        k = src_bar[si]; tk = by_key.get(s[2])
        if not 0 <= k < nsrc or not tk:
            continue
        t = s[0]; got = set()
        for x in range(bisect.bisect_left(tk, t + lo_b * P - step), bisect.bisect_right(tk, t + hi_b * P + step)):
            d = tk[x] - t
            for b in range(max(lo_b, -(-(d - step) // P)), min(hi_b, (d + step) // P) + 1):
                got.add(b)
        for b in got:
            hits[k][b] = hits[k].get(b, 0) + 1

    def score(k, b):
        c = hits[k].get(b)
        if not c:
            return 0.0
        a = bs_src[k] + b * P - step // 2; z = bs_src[k + 1] + b * P - step // 2
        T = bisect.bisect_left(song_ticks, z) - bisect.bisect_left(song_ticks, a)
        return 2.0 * c / (n_src_bar[k] + max(T, c))

    def valid(k, b):
        return 0 <= bs_src[k] + b * P < s_end
    states = range(lo_b, hi_b + 1)
    prev = {b: score(0, b) - eps * abs(b) for b in states if valid(0, b)}
    if not prev:
        return [None] * nsrc, [0.0] * nsrc
    backs = []; last = 0
    for k in range(1, nsrc):
        bo = max(prev, key=lambda b: (prev[b], -abs(b))); best = prev[bo]
        cur, bk = {}, {}
        for b in states:
            if not valid(k, b):
                continue
            stay = prev.get(b)
            if stay is not None and stay >= best - pen:
                cur[b], bk[b] = stay, b
            else:
                cur[b], bk[b] = best - pen, bo
            cur[b] += score(k, b) - eps * abs(b)
        if not cur:                                                # the source runs past the song's end
            break
        backs.append(bk); prev = cur; last = k
    b = max(prev, key=lambda x: (prev[x], -abs(x)))
    path = [b]
    for bk in reversed(backs):
        b = bk[b]; path.append(b)
    path.reverse()
    path += [None] * (nsrc - last - 1)
    return ([b * P if b is not None else None for b in path],
            [score(k, b) if b is not None else 0.0 for k, b in enumerate(path)])


def _cmp_bar_map(xs, shifts, roles, nonempty, to_bar):
    """Compact ranges: 'MIDI 1-4 -> song 5 (loop)', 'MIDI 5-50 -> song 9-54', 'MIDI 60-61 -> not found'. xs: the song
    tick each MIDI bar starts at; a run is one constant shift (a song bar like 17.75: the bars don't line up there)."""
    out = []; k = 0; n = len(xs)
    last_note = max((i for i in range(n) if nonempty[i]), default=-1)

    def sb(x):
        return f"{round(to_bar(x), 2):g}"
    while k <= last_note:
        if roles[k] == "notfound" or xs[k] is None:
            e = k
            while e + 1 <= last_note and (roles[e + 1] == "notfound" or xs[e + 1] is None):
                e += 1
            out.append(f"MIDI {k + 1}" + (f"-{e + 1}" if e > k else "") + " -> not found"); k = e + 1
            continue
        e = k
        if k + 1 <= last_note and xs[k + 1] == xs[k] and roles[k + 1] != "notfound":
            while e + 1 <= last_note and xs[e + 1] == xs[k] and roles[e + 1] != "notfound":
                e += 1
            out.append(f"MIDI {k + 1}-{e + 1} -> song {sb(xs[k])} (loop)"); k = e + 1
            continue
        while e + 1 <= last_note and xs[e + 1] is not None and shifts[e + 1] == shifts[e] and \
                roles[e + 1] != "notfound" and not (e + 2 <= last_note and xs[e + 2] == xs[e + 1]):
            e += 1
        out.append(f"MIDI {k + 1}" + (f"-{e + 1}" if e > k else "") + f" -> song {sb(xs[k])}" +
                   (f"-{sb(xs[e])}" if e > k else ""))
        k = e + 1
    return out


def _bars_txt(bars):
    """[3, 4, 5, 9] -> '3-5, 9'."""
    bars = sorted(set(bars)); out = []; i = 0
    while i < len(bars):
        j = i
        while j + 1 < len(bars) and bars[j + 1] == bars[j] + 1:
            j += 1
        out.append(f"{bars[i]}" + (f"-{bars[j]}" if j > i else "")); i = j + 1
    return ", ".join(out)


def _sd(v):
    if len(v) < 2:
        return 0.0
    mu = sum(v) / len(v)
    return math.sqrt(sum((x - mu) ** 2 for x in v) / (len(v) - 1))


def _top_voice(notes, P):
    """notes: [(tick, len, key)] sorted by tick -> [bool]: the note is the highest one sounding at its onset."""
    eps = max(1, int(P * 0.06)); back = 8 * P
    ts = [n[0] for n in notes]; out = []
    for i, (t, ln, k) in enumerate(notes):
        lo = bisect.bisect_left(ts, t - back); hi = bisect.bisect_right(ts, t + eps)
        top = True
        for j in range(lo, hi):
            if j == i:
                continue
            t2, l2, k2 = notes[j]
            if k2 > k and (t2 + l2 > t + eps or abs(t2 - t) <= eps):
                top = False; break
        out.append(top)
    return out


def _cmp_tempo_summary(tempos, to_bar, bar_len, L):
    pts = []
    for t, b in sorted(tempos):
        b = round(float(b), 2)
        if pts and pts[-1][0] == t:
            pts[-1] = (t, b)
        elif not pts or abs(pts[-1][1] - b) >= 0.01:
            pts.append((t, b))
    pts = [p for i, p in enumerate(pts) if i == 0 or abs(p[1] - pts[i - 1][1]) >= 0.01]
    vals = [b for _, b in pts]; lo, hi = min(vals), max(vals)
    steady = hi - lo <= float(L["tempo"]["steady_bpm"])
    moves = []; i = 1
    while i < len(pts):
        j = i
        while j + 1 < len(pts) and pts[j + 1][0] - pts[j][0] <= bar_len + 1:
            j += 1
        seg = [b for _, b in pts[i - 1:j + 1]]; dif = [b - a for a, b in zip(seg, seg[1:])]
        kind = "jump" if j == i else ("ramp" if all(x > 0 for x in dif) or all(x < 0 for x in dif) else "wobble")
        a, b = to_bar(pts[i][0]), to_bar(pts[j][0])
        txt = (f"bar {a:g}: {_fmt_bpm(seg[0])} -> {_fmt_bpm(seg[-1])} (jump)" if kind == "jump" else
               f"bars {a:g}-{b:g}: {_fmt_bpm(seg[0])} -> {_fmt_bpm(seg[-1])} ({kind}" +
               (f", {_fmt_bpm(min(seg[1:]))}-{_fmt_bpm(max(seg[1:]))})" if kind == "wobble" else ")"))
        moves.append({"bar": a, "to_bar": b, "from": seg[0], "to": seg[-1], "kind": kind, "text": txt})
        i = j + 1
    return {"steady": steady, "bpm": _fmt_bpm(vals[0]) if steady else None, "min_bpm": lo, "max_bpm": hi,
            "moves": moves, "pts": pts}


def _cmp_tempo_shape(pts, a, b, L):
    """How the tempo moves between ticks a and b: range, time-weighted average, gradual slow-downs / speed-ups (2+
    small steps one way) and jumps (one step of jump_bpm or more). None when no time falls there."""
    if b <= a or not pts:
        return None
    ts = [t for t, _ in pts]
    j = max(0, bisect.bisect_right(ts, a) - 1)
    seg = [(a, pts[j][1])] + [p for p in pts[j + 1:] if a < p[0] < b]
    vals = [v for _, v in seg]; lo, hi = min(vals), max(vals)
    avg = sum(v * ((seg[i + 1][0] if i + 1 < len(seg) else b) - t) for i, (t, v) in enumerate(seg)) / (b - a)
    J = float(L["tempo"].get("jump_bpm", 3)); steady = hi - lo <= float(L["tempo"]["steady_bpm"])
    slow = fast = jup = jdn = 0; run = 0
    for d in (y - x for x, y in zip(vals, vals[1:])):
        if abs(d) >= J:
            jup += d > 0; jdn += d < 0; run = 0; continue
        sgn = 1 if d > 0 else -1
        if run and (run > 0) == (sgn > 0):
            run += sgn
            if abs(run) == 2:
                slow += sgn < 0; fast += sgn > 0
        else:
            run = sgn
    parts = [f"{n} {w}" for n, w in ((slow, "gradual slow-down(s)"), (fast, "gradual speed-up(s)"),
                                     (jup, "jump(s) up"), (jdn, "jump(s) down")) if n]
    moves = "steady" if steady else (", ".join(parts) or f"{len(vals) - 1} small change(s)")
    return {"lo": lo, "hi": hi, "avg": round(avg, 1), "moves": moves,
            "text": (f"{_fmt_bpm(lo)} BPM, steady" if steady else
                     f"{_fmt_bpm(lo)}-{_fmt_bpm(hi)} BPM (avg {_fmt_bpm(round(avg, 1))}): {moves}")}


def _cmp_analyse(project, midi, channels, tol_beats, L, journal_from=None):
    t0 = time.time(); steps = {}
    S_ = _cmp_read_song(project, journal_from); P = S_["P"]
    mm = load_model(midi); div = mm["P"]; f = P / float(div)
    drums = {c for c, info in (mm.get("parts_in") or {}).items() if (info or {}).get("kind") == "drums"}
    src = sorted((int(round(n[POS] * f)), max(1, int(round(n[LEN] * f))), n[KEY], n[VEL])
                 for n in mm["notes"] if n[CH] not in drums)
    if not src:
        raise ValueError(f"{os.path.basename(midi)} has no notes to compare (drum-channel notes are left out)")
    keep, last_on, src_dups = [], {}, 0                            # one key struck twice at once (two tracks with the
    dup_n = {}                                                     # same note): one note on a keyboard, not two
    for s in src:                                                  # dup_n: kept index -> copies merged into it
        if s[2] in last_on and s[0] - last_on[s[2]][0] <= max(1, P // 16):
            src_dups += 1; ki = last_on[s[2]][1]; dup_n[ki] = dup_n.get(ki, 0) + 1; continue
        last_on[s[2]] = (s[0], len(keep)); keep.append(s)
    src = keep
    present = sorted({n[SN_C] for n in S_["notes"]})
    want = set(_cmp_channels(channels, S_, present))
    song = [n for n in S_["notes"] if n[SN_C] in want]
    if not song:
        raise ValueError(f"no notes on the compared channels {sorted(want)} in the playlist (muted clips don't count)")
    steps["read"] = round(time.time() - t0, 3); t1 = time.time()
    s_end = max([n[SN_T] + n[SN_L] for n in song] + [n[SN_T] for n in S_["notes"]] + [0])
    bs_song = _cmp_bar_starts(S_["meters"], P, s_end)
    mmet = [tuple(int(x) for x in t) for t in (mm.get("meters") or [(0,) + tuple(mm["ts"])])]
    meter_note = None
    if len({x[1:] for x in mmet}) > 1 and len({x[1:] for x in S_["meters"]}) == 1:
        # the song keeps one meter (no time-signature markers): its bars are what the user sees, so the MIDI's bars
        # are counted the same way (a source with meter changes would otherwise drift against the song's bars)
        num, den = S_["meters"][0][1], S_["meters"][0][2]
        chg = ", ".join(f"{n_}/{d_} at its beat {t_ / div + 1:g}" for t_, n_, d_ in mmet[1:5])
        meter_note = (f"the MIDI changes meter ({chg}{', ...' if len(mmet) > 5 else ''}) and the song doesn't: "
                      f"MIDI bars are counted in the song's {num}/{den}")
        mmet = [(0, num, den)]
    m_end = max(n[POS] + n[LEN] for n in mm["notes"])
    bs_src = [int(round(t * f)) for t in _cmp_bar_starts(mmet, div, m_end)]
    step = max(1, int(round(P * float(L["grid_beats"]))))

    def bar_of(bs, t):
        return bisect.bisect_right(bs, t) - 1

    def q(t):
        return int(round(t / step)) * step
    src_bar = [bar_of(bs_src, q(s[0])) for s in src]
    song_bar = [bar_of(bs_song, q(n[SN_T])) for n in song]
    song_sets = [set() for _ in range(len(bs_song) - 1)]          # exact (step, key) per song bar: repeats only
    for n, j in zip(song, song_bar):
        if 0 <= j < len(song_sets):
            song_sets[j].add(((q(n[SN_T]) - bs_song[j]) // step, n[SN_K]))
    nsrc = len(bs_src) - 1
    n_src_bar = [0] * nsrc
    for k in src_bar:
        if 0 <= k < nsrc:
            n_src_bar[k] += 1
    by_key = {}
    for i, n in enumerate(song):
        by_key.setdefault(n[SN_K], []).append(i)
    ticks_of = {k: [song[i][SN_T] for i in v] for k, v in by_key.items()}
    song_ticks = sorted(n[SN_T] for n in song)
    bpb = S_["meters"][0][1] * 4.0 / S_["meters"][0][2]
    shifts, sc = _cmp_align(src, src_bar, bs_src, n_src_bar, song_ticks, ticks_of, s_end, P, step, bpb, L)
    xs = [bs_src[k] + shifts[k] if shifts[k] is not None else None for k in range(nsrc)]
    # a bar is found when it matches at all and either well on its own or not far below its neighbours (a humanized,
    # heavily edited bar inside a well-matched region still belongs to that region)
    floor_, absm, rel = float(L["min_bar_floor"]), float(L["min_bar_match"]), float(L["min_bar_relative"])
    roles = []
    for k in range(nsrc):
        if not n_src_bar[k]:
            roles.append("empty"); continue
        nb = [sc[i] for i in range(max(0, k - 4), min(nsrc, k + 5))
              if i != k and n_src_bar[i] and xs[i] is not None]
        ok = xs[k] is not None and sc[k] >= floor_ and (sc[k] >= absm or sc[k] >= rel * (_median(nb) if nb else 1))
        roles.append("primary" if ok else "notfound")
    by_x = {}
    for k in range(nsrc):
        if roles[k] == "primary":
            by_x.setdefault(xs[k], []).append(k)
    for x, ks in by_x.items():                                     # several MIDI bars on one song spot: a loop the
        keep = max(ks, key=lambda k: (sc[k], k))                    # song plays once; only the best-matching bar counts
        for k in ks:
            if k != keep:
                roles[k] = "loop"
    spans = sorted((xs[k], xs[k] + bs_src[k + 1] - bs_src[k]) for k in range(nsrc) if roles[k] == "primary")
    sp_a = [a for a, _ in spans]

    def is_cov(t):
        j = bisect.bisect_right(sp_a, t) - 1
        return any(spans[i][0] <= t < spans[i][1] for i in range(max(0, j - 1), j + 1))
    covered = {bar_of(bs_song, a) for a, _ in spans}
    steps["align"] = round(time.time() - t1, 3); t1 = time.time()
    # ---- note matching: same key, nearest within the tolerance, one to one
    tol = max(1, int(round(P * float(tol_beats))))
    exp = {}; cands = []
    for si, s in enumerate(src):
        k = src_bar[si]
        if not 0 <= k < nsrc or roles[k] != "primary":
            continue
        e = s[0] + shifts[k]; exp[si] = (e, max(0, bar_of(bs_song, e)))
        lst = by_key.get(s[2])
        if not lst:
            continue
        tk = ticks_of[s[2]]
        for x in range(bisect.bisect_left(tk, e - tol), bisect.bisect_right(tk, e + tol)):
            ni = lst[x]; cands.append((abs(song[ni][SN_T] - e), si, ni))
    cands.sort(); s_used, n_used, pairs = {}, {}, []
    for dist, si, ni in cands:
        if si in s_used or ni in n_used:
            continue
        s_used[si] = ni; n_used[ni] = si; pairs.append((si, ni, song[ni][SN_T] - exp[si][0]))
    dbl = 0                                                        # the song plays a doubled source note on both
    if dup_n:                                                      # layers: the second copy matches the merged one,
        left = dict(dup_n)                                         # it isn't "new"
        for dist, si, ni in cands:
            if ni not in n_used and left.get(si, 0) > 0 and si in s_used:
                left[si] -= 1; n_used[ni] = si; dbl += 1
    missing = sorted((si for si in exp if si not in s_used), key=lambda si: exp[si][0])
    cov = [is_cov(q(n[SN_T])) for n in song]
    new = [i for i in range(len(song)) if i not in n_used and cov[i]]
    unmapped = [i for i in range(len(song)) if i not in n_used and not cov[i]]
    steps["match"] = round(time.time() - t1, 3); t1 = time.time()
    # ---- stand-ins: runs of one channel + key struck >= 3 times within 2 bars, unmatched, source notes missing near
    bar_len = (bs_song[-1] - bs_song[0]) / max(1, len(bs_song) - 1)
    SI = L["stand_in"]; miss_t = sorted(exp[si][0] for si in missing)
    near_t = int(round(P * float(SI["missing_near_beats"])))
    runs = {}
    for i in new:
        runs.setdefault((song[i][SN_C], song[i][SN_K]), []).append(i)
    stand, rep_new = [], []
    for (ch, key), idx in runs.items():
        idx.sort(key=lambda i: song[i][SN_T]); chain = [idx[0]]
        for i in idx[1:] + [None]:
            if i is not None and song[i][SN_T] - song[chain[-1]][SN_T] <= float(SI["gap_bars"]) * bar_len:
                chain.append(i); continue
            if len(chain) >= int(SI["min_strikes"]):
                ts = [song[c][SN_T] for c in chain]; w = float(SI["within_bars"]) * bar_len
                dense = any(bisect.bisect_right(ts, ts[a] + w) - a >= int(SI["min_strikes"]) for a in range(len(ts)))
                near = bisect.bisect_right(miss_t, ts[-1] + near_t) - bisect.bisect_left(miss_t, ts[0] - near_t)
                if dense:                                          # next to missing source notes: a stand-in;
                    (stand if near else rep_new).append(          # else a repeated figure the source doesn't have
                        {"ch": ch, "key": key, "idx": list(chain), "missing_near": near,
                         "bars": (bar_of(bs_song, ts[0]) + 1, bar_of(bs_song, ts[-1]) + 1)})
            chain = [i] if i is not None else []
    stand.sort(key=lambda r: (-len(r["idx"]), r["bars"])); rep_new.sort(key=lambda r: (-len(r["idx"]), r["bars"]))
    # ---- near-duplicate strikes: same channel + key within near_dup_beats
    dup_t = max(1, int(round(P * float(L["near_dup_beats"]))))
    ck = {}
    for i, n in enumerate(song):
        ck.setdefault((n[SN_C], n[SN_K]), []).append(i)
    dups = []
    for (ch, key), idx in ck.items():
        for a, b in zip(idx, idx[1:]):
            if song[b][SN_T] - song[a][SN_T] <= dup_t:
                ma, mb = a in n_used, b in n_used
                drop = b if (ma and not mb) else a if (mb and not ma) else \
                    (b if song[b][SN_V] <= song[a][SN_V] else a)
                dups.append({"ch": ch, "key": key, "keep": a if drop == b else b, "drop": drop,
                             "gap": song[b][SN_T] - song[a][SN_T]})
    steps["find"] = round(time.time() - t1, 3)
    secs, sec_src = _cmp_sections(project, S_, journal_from)
    return {"S": S_, "P": P, "mm": mm, "div": div, "src": src, "song": song, "want": sorted(want), "bs_song": bs_song,
            "bs_src": bs_src, "xs": xs, "shifts": shifts, "sc": sc, "roles": roles, "covered": covered,
            "src_bar": src_bar, "n_src_bar": n_src_bar, "meter_note": meter_note, "src_dups": src_dups, "dbl": dbl, "cov": cov,
            "song_bar": song_bar, "song_sets": song_sets, "exp": exp, "pairs": pairs,
            "n_used": n_used, "s_used": s_used, "missing": missing, "new": new, "unmapped": unmapped,
            "stand": stand, "rep_new": rep_new, "dups": dups, "dup_t": dup_t, "tol": tol, "bar_len": bar_len,
            "sections": secs, "sec_src": sec_src, "steps": steps}


def _cmp_channel_fit(R):
    """{channel: share of its notes (where the source maps) that a source note of the same key sits near}."""
    by_k = {}
    for si, (e, _j) in R["exp"].items():
        by_k.setdefault(R["src"][si][2], []).append(e)
    for v in by_k.values():
        v.sort()
    tot, hit = {}, {}
    for n, c in zip(R["song"], R["cov"]):
        if not c:
            continue
        ch = n[SN_C]; tot[ch] = tot.get(ch, 0) + 1
        es = by_k.get(n[SN_K]) or []
        x = bisect.bisect_left(es, n[SN_T] - R["tol"])
        if x < len(es) and es[x] <= n[SN_T] + R["tol"]:
            hit[ch] = hit.get(ch, 0) + 1
    return {ch: hit.get(ch, 0) / t for ch, t in tot.items() if t}


def _cmp_id(n):
    return f"n{n[SN_T]}.{n[SN_C]}.{n[SN_R][4]}"


def _cmp_report(R, L, full=False):
    S_ = R["S"]; P = R["P"]; song = R["song"]; src = R["src"]; bs = R["bs_song"]; names = S_["names"]
    lim = None if full else int(L.get("limit") or 12)
    bpm_at = _cmp_tempo_fn(S_["tempos"])
    sec_t = [t for t, _ in R["sections"]]

    def sec(t):
        return R["sections"][max(0, bisect.bisect_right(sec_t, t) - 1)][1]

    def bar_of(t):
        return bisect.bisect_right(bs, t) - 1

    def cname(ch):
        return names.get(ch) or f"ch {ch}"

    def ms(dt, t):
        return dt / P * 60000.0 / bpm_at(t)

    def beat(t, j):
        return round(1 + (t - bs[j]) / P, 2)
    out = {}
    # ---- bar map
    bar_map = _cmp_bar_map(R["xs"], R["shifts"], R["roles"], R["n_src_bar"],
                           lambda t: H["_tick_to_bar"](t, S_["meters"], P, None))
    song_bars_with = sorted({R["song_bar"][i] for i in R["unmapped"] if R["song_bar"][i] >= 0})
    sig = {}
    for j in sorted(R["covered"]):
        sig.setdefault(frozenset(R["song_sets"][j]), j)
    new_mat = []
    if song_bars_with:
        rep = {j + 1: sig.get(frozenset(R["song_sets"][j])) for j in song_bars_with if R["song_sets"][j]}
        new_mat = [{"bars": _bars_txt([j + 1 for j in song_bars_with]), "notes": len(R["unmapped"])}]
        reps = sorted(b for b, v in rep.items() if v is not None)
        if reps:
            new_mat[0]["repeats_of_song_bars"] = {str(b): rep[b] + 1 for b in reps[:lim]}
    # ---- missing per song bar / section
    per_bar = {}
    for si in R["missing"]:
        e, j = R["exp"][si]; per_bar.setdefault(j, []).append((e, src[si][2]))
    miss_rows = []
    for j, items in per_bar.items():
        items.sort()
        miss_rows.append({"bar": j + 1, "section": sec(bs[j]), "n": len(items),
                          "notes": " ".join(f"{nn(k)}@{beat(e, j):g}" for e, k in items)})
    miss_rows.sort(key=lambda r: (-r["n"], r["bar"]))
    by_sec = {}
    for r in miss_rows:
        by_sec[r["section"]] = by_sec.get(r["section"], 0) + r["n"]
    # ---- new notes per channel
    new_by = {}
    for i in R["new"]:
        new_by.setdefault(song[i][SN_C], []).append(i)
    new_rows = sorted(({"channel": cname(ch), "ch": ch, "n": len(v),
                        "bars": _bars_txt([R["song_bar"][i] + 1 for i in v]),
                        **({"ids": [_cmp_id(song[i]) for i in v]} if full else {})} for ch, v in new_by.items()),
                      key=lambda r: -r["n"])

    def run_rows(runs):
        return [{"channel": cname(r["ch"]), "ch": r["ch"], "key": nn(r["key"]),
                 "bars": f"{r['bars'][0]}" + (f"-{r['bars'][1]}" if r["bars"][1] != r["bars"][0] else ""),
                 "strikes": len(r["idx"]), "missing_near": r["missing_near"], "section": sec(song[r["idx"][0]][SN_T]),
                 **({"ids": [_cmp_id(song[i]) for i in r["idx"]]} if full else {})} for r in runs]
    stand_rows = run_rows(R["stand"]); rep_rows = run_rows(R["rep_new"])
    dup_rows = []
    for r in R["dups"]:
        a = song[r["keep"]]; b = song[r["drop"]]; t = min(a[SN_T], b[SN_T]); j = bar_of(t)
        dup_rows.append({"channel": cname(r["ch"]), "key": nn(r["key"]), "bar": j + 1, "beat": beat(t, j),
                         "gap_ms": round(ms(r["gap"], t), 1), "keep": _cmp_id(a), "drop": _cmp_id(b)})
    # ---- timing per section
    TL = L["timing_ms"]

    grid = max(1, P // 8)
    on_grid = sum(1 for s in src if min(s[0] % grid, grid - s[0] % grid) <= max(1, P // 96)) / len(src)

    def feel(sd):                                                  # offsets from a played (unquantized) source: tight
        return (("tight (quantized-sounding)" if on_grid >= 0.8 else "tight (close to the source's own timing)")
                if sd <= TL["tight"] else "natural" if sd <= TL["natural"] else
                "loose" if sd <= TL["loose"] else "sloppy")
    tsec = {}
    for si, ni, dt in R["pairs"]:
        t = song[ni][SN_T]; tsec.setdefault(sec(t), []).append(ms(dt, t))
    ch_t = max(1, int(round(P * float(L["chord_beats"])))); chords = {}
    for si, ni, dt in R["pairs"]:
        s = src[si]; k = R["src_bar"][si]
        chords.setdefault((k, s[0] // ch_t), []).append((song[ni][SN_T], dt))
    csec = {}
    for (k, _), tds in chords.items():                             # a source chord's notes: how far apart the song
        if len(tds) >= 2:                                          # puts them, beyond the source's own spread
            t = min(x[0] for x in tds); dts = [x[1] for x in tds]
            csec.setdefault(sec(t), []).append((ms(max(dts) - min(dts), t), bar_of(t) + 1))
    timing_rows = []; all_ms = []
    for name in [n for _, n in R["sections"]]:
        v = tsec.get(name)
        if not v:
            continue
        all_ms += v; med = _median(v); sd = _sd(v)
        row = {"section": name, "notes": len(v), "median_ms": round(med, 1), "spread_ms": round(sd, 1),
               "p90_ms": round(sorted(abs(x - med) for x in v)[int(0.9 * (len(v) - 1))], 1), "feel": feel(sd)}
        cs = csec.get(name) or []
        if cs:
            split = [c for c in cs if c[0] > float(L["chord_split_ms"])]
            worst = max(cs)
            row.update(chords=len(cs), chords_split=len(split),
                       chord_spread_median_ms=round(_median([c[0] for c in cs]), 1))
            if split:
                row["worst_chord"] = f"bar {worst[1]}: {worst[0]:.0f} ms"
        timing_rows.append(row)
    # ---- velocity per section and channel; melody (top voice) over the accompaniment
    VL = L["velocity"]
    sv = {}
    for n in song:
        sv.setdefault(sec(n[SN_T]), {}).setdefault(n[SN_C], []).append(min(127, n[SN_V]))
    top = _top_voice([(n[SN_T], n[SN_L], n[SN_K]) for n in song], P)
    mel = {}
    for n, tp in zip(song, top):
        mel.setdefault(sec(n[SN_T]), ([], []))[0 if tp else 1].append(min(127, n[SN_V]))
    src_c = sorted((R["exp"][si][0], s[1], s[2], s[3]) for si, s in enumerate(src) if si in R["exp"])
    stop = _top_voice([(e, l_, k) for e, l_, k, _v in src_c], P)
    smel, ssd = {}, {}
    for (e, _l, _k, v), tp in zip(src_c, stop):
        smel.setdefault(sec(e), ([], []))[0 if tp else 1].append(v); ssd.setdefault(sec(e), []).append(v)
    flat_secs = []; pvel = {}
    for si, ni, _dt in R["pairs"]:
        pvel.setdefault((sec(song[ni][SN_T]), song[ni][SN_C]), []).append(src[si][3])
    vel_rows = []
    for name in [n for _, n in R["sections"]]:
        if name not in sv:
            continue
        chs = {cname(ch): f"{_median(v):.0f}±{_sd(v):.0f} ({min(v)}-{max(v)})" +
               (" flat" if len(v) > 3 and _sd(v) < VL["flat_sd"] else "") for ch, v in sorted(sv[name].items())}
        row = {"section": name, "channels": chs}
        if ssd.get(name):
            row["source_spread"] = round(_sd(ssd[name]), 1)
        for ch, v in sorted(sv[name].items()):                     # flat where the same notes vary in the source
            sv_src = pvel.get((name, ch)) or []
            if len(v) > 7 and len(sv_src) > 7 and _sd(v) < VL["flat_sd"] <= _sd(sv_src):
                flat_secs.append(f"{name} ({cname(ch)})")
        tv, av = mel.get(name, ([], []))
        if tv and av:
            diff = _median(tv) - _median(av); row["melody_over_accomp"] = round(diff, 1)
            row["melody_above"] = diff >= VL["melody_margin"]
        st, sa = smel.get(name, ([], []))
        if st and sa:
            row["source_melody_over_accomp"] = round(_median(st) - _median(sa), 1)
        vel_rows.append(row)
    # ---- tempo
    meters = S_["meters"]

    def to_bar(t):
        return round(H["_tick_to_bar"](t, meters, P), 1)
    tsum = _cmp_tempo_summary(S_["tempos"], to_bar, R["bar_len"], L)
    mm = R["mm"]; f = P / float(R["div"])
    ssum = _cmp_tempo_summary([(int(round(t * f)), b) for t, b in (mm.get("tempos") or [(0, 120.0)])],
                              lambda t: float(bisect.bisect_right(R["bs_src"], t)), R["bar_len"], L)
    tempo = {"steady": tsum["steady"], "min_bpm": tsum["min_bpm"], "max_bpm": tsum["max_bpm"]}
    simple = all(m_["kind"] != "wobble" for m_ in tsum["moves"])
    if simple:                                                     # a few clear moves: list them
        tempo["changes"] = [m_["text"] for m_ in tsum["moves"][:lim]]
        if len(tsum["moves"]) > len(tempo["changes"]):
            tempo["more_changes"] = len(tsum["moves"]) - len(tempo["changes"])
    if not tsum["steady"]:                                         # per section: range, average, how it moves
        ends = [t for t, _ in R["sections"][1:]] + [max(R["bs_song"][-1], 1)]
        rows = []
        for (a, name), b in zip(R["sections"], ends):
            sh = _cmp_tempo_shape(tsum["pts"], a, b, L)
            if sh:
                rows.append({"section": name, "tempo": sh["text"]})
        if not simple or lim is None or len(rows) <= lim:
            tempo["sections"] = rows
    tempo["source"] = (f"steady {ssum['bpm']} BPM" if ssum["steady"] else
                       f"{_fmt_bpm(ssum['min_bpm'])}-{_fmt_bpm(ssum['max_bpm'])} BPM, {len(ssum['moves'])} change(s)")
    if tsum["steady"]:
        tempo["verdict"] = f"steady {tsum['bpm']} BPM" + (" (the source moves; the song doesn't)"
                                                          if not ssum["steady"] else "")
    else:
        kinds = {}
        for m_ in tsum["moves"]:
            kinds[m_["kind"]] = kinds.get(m_["kind"], 0) + 1
        allsh = _cmp_tempo_shape(tsum["pts"], 0, max(R["bs_song"][-1], 1), L)
        how = (", ".join(f"{v} {k}{'s' if v > 1 else ''}" for k, v in sorted(kinds.items())) if simple else
               allsh["moves"] + " (rubato all through)")
        tempo["verdict"] = (f"moves {_fmt_bpm(tsum['min_bpm'])}-{_fmt_bpm(tsum['max_bpm'])} BPM: " + how +
                            ("; the source is steady, so these moves are the song's own - unplanned tempo moves "
                             "sound amateur, planned rubato needs a listen" if ssum["steady"] else
                             "; the source moves too (compare where)"))
    # ---- counts, summary
    nsrc_cmp = len(R["exp"])
    sd_all = _sd(all_ms) if all_ms else None
    cnt = {"source_notes": len(src) + R.get("src_dups", 0), "compared": nsrc_cmp, "matched": len(R["pairs"]), "missing": len(R["missing"]),
           "new": len(R["new"]), "stand_in_runs": len(R["stand"]), "repeated_new_runs": len(R["rep_new"]),
           "near_duplicates": len(R["dups"]),
           "song_notes_without_source_bars": len(R["unmapped"])}
    if R.get("src_dups"):
        cnt["source_doubled_notes_merged"] = R["src_dups"]
    if R.get("dbl"):
        cnt["doubled_notes_matched"] = R["dbl"]                   # song notes playing a merged source copy
    loops = sum(1 for r in R["roles"] if r == "loop"); nf = [k + 1 for k, r in enumerate(R["roles"]) if r == "notfound"]
    summ = [f"{len(R['missing'])} of {nsrc_cmp} source notes missing"
            + (f" ({100.0 * len(R['missing']) / nsrc_cmp:.0f}%)" if nsrc_cmp else "")
            + (f", most in {', '.join(s for s, _ in sorted(by_sec.items(), key=lambda x: -x[1])[:3])}" if by_sec else ""),
            f"{len(R['new'])} new note(s) not in the source" + (f", {len(R['stand'])} repeated stand-in run(s) "
                                                               f"(one key struck again where source notes went)"
                                                               if R["stand"] else "")
            + (f", {len(R['rep_new'])} repeated new-note run(s)" if R["rep_new"] else ""),
            "timing: " + (f"onsets {feel(sd_all)} ({sd_all:.0f} ms spread from the source" +
                          (f"; widest {max(timing_rows, key=lambda r: r['spread_ms'])['section']} "
                           f"{max(r['spread_ms'] for r in timing_rows):.0f} ms)" if len(timing_rows) > 1 and
                           max(r['spread_ms'] for r in timing_rows) > TL["tight"] else ")")
                          if sd_all is not None else "no matched notes"),
            "tempo: " + tempo["verdict"]]
    if R["dups"]:
        summ.insert(2, f"{len(R['dups'])} near-duplicate strike(s) (same key twice within "
                       f"{float(L['near_dup_beats']):g} beat)")
    split_secs = [f"{r['section']} ({r['chords_split']} of {r['chords']}, median {r['chord_spread_median_ms']:.0f} ms)"
                  for r in timing_rows if r.get("chords") and r["chords_split"] >= max(2, 0.25 * r["chords"])]
    loose = [f"{r['section']} {r['spread_ms']:.0f} ms" for r in sorted(timing_rows, key=lambda r: -r["spread_ms"])
             if r["spread_ms"] > TL["natural"]]
    if loose:
        summ.insert(-1, "timing loose / sloppy in " + ", ".join(loose[:4]))
    if split_secs:
        summ.insert(-1, f"chords split apart (notes > {float(L['chord_split_ms']):g} ms apart) in " +
                    ", ".join(split_secs[:4]))
    if flat_secs:
        summ.append(f"velocity flat (machine-like) where the source varies: {', '.join(flat_secs[:4])}")
    low_mel = [r["section"] for r in vel_rows if r.get("melody_above") is False     # the source has it above there
               and r.get("source_melody_over_accomp", VL["melody_margin"]) >= VL["melody_margin"]]
    if low_mel:
        summ.append(f"velocity: the melody isn't above the accompaniment in {', '.join(low_mel[:4])}")
    out.update({"summary": summ, "counts": cnt, "bar_map": bar_map[:lim * 2] if lim else bar_map,
                "sections_from": R["sec_src"]})
    if R.get("meter_note"):
        out["midi_bars"] = R["meter_note"]
    if loops:
        out["loop_bars_skipped"] = loops
    if nf:
        out["source_bars_not_found"] = _bars_txt(nf)
    if new_mat:
        out["song_bars_without_source"] = new_mat
    out["missing"] = miss_rows[:lim] if lim else miss_rows
    if lim and len(miss_rows) > lim:
        out["missing_more_bars"] = len(miss_rows) - lim
    if by_sec:
        out["missing_by_section"] = dict(sorted(by_sec.items(), key=lambda x: -x[1]))
    out["new"] = new_rows
    out["stand_ins"] = stand_rows[:lim] if lim else stand_rows
    if rep_rows:
        out["repeated_new"] = rep_rows[:lim] if lim else rep_rows
    out["near_duplicates"] = dup_rows[:lim] if lim else dup_rows
    out["timing"] = {"verdict": (f"{feel(sd_all)}: onsets spread {sd_all:.0f} ms from the source" if sd_all is not None
                                 else "no matched notes"),
                     "offsets": "ms; + = the song plays later than the source", "source_on_grid_pct": round(100 * on_grid),
                     "sections": timing_rows}
    if on_grid < 0.8:
        out["timing"]["note"] = ("the source itself isn't quantized: offsets are measured against its own timing, "
                                 "not a grid")
    out["velocity"] = {"sections": vel_rows}
    out["tempo"] = tempo
    nxt = []
    if R["missing"]:
        nxt.append("restore=true writes the missing notes back (channel by nearest pitch, velocity and timing from "
                   "matched neighbours)")
    if R["stand"]:
        nxt.append("remove=['stand_ins'] deletes the repeated stand-ins (with restore=true: one call)")
    if R["rep_new"]:
        nxt.append("remove=['repeated_new'] deletes repeated new-note runs that sit next to no missing source note "
                   "(e.g. stand-ins left after a restore); keep them if they're a figure you added on purpose")
    if R["dups"]:
        nxt.append("remove=['near_duplicates'] deletes the softer / unmatched strike of each pair")
    nxt.append("listen after any change: these are note facts, not how it sounds")
    out["next"] = nxt
    return out


def _cmp_plan(R, L, restore, rem):
    """-> (ops, skipped, unknown ids). An op: {kind add|del, clip, pid, ppos, ch, key (written), ...}."""
    S_ = R["S"]; song = R["song"]; src = R["src"]; dup_t = R["dup_t"]
    clips = [c for c in S_["clips"] if not c["muted"]]; pats = S_["pats"]; offs = S_["offs"]
    ops, skipped, unknown = [], {}, []
    targets = []
    if rem:
        allx = {}
        for n in S_["notes"]:
            allx.setdefault(_cmp_id(n), []).append(n)
        for x in rem:
            if x == "stand_ins":
                targets += [song[i] for r in R["stand"] for i in r["idx"]]
            elif x == "repeated_new":
                targets += [song[i] for r in R["rep_new"] for i in r["idx"]]
            elif x == "near_duplicates":
                targets += [song[r["drop"]] for r in R["dups"]]
            elif allx.get(x):
                targets += allx[x][:1]
            else:
                unknown.append(x)
    seen = set()
    for n in targets:
        key = (n[SN_CL], n[SN_PP], n[SN_C], n[SN_R])
        if key in seen:
            continue
        seen.add(key)
        ops.append({"kind": "del", "clip": n[SN_CL], "pid": n[SN_P], "ppos": n[SN_PP], "ch": n[SN_C],
                    "key": n[SN_R][4], "rec": n[SN_R], "tick": n[SN_T]})
    if not restore:
        return ops, skipped, unknown
    gone = {(n[SN_T], n[SN_C], n[SN_K]) for n in targets}
    # neighbours: timing shift per (bar, ch) / bar, velocity ratio per (bar, ch) / bar / ch / all
    dt_bc, dt_b, vr_bc, vr_b, vr_c, vr = {}, {}, {}, {}, {}, []
    for si, ni, dt in R["pairs"]:
        j = R["exp"][si][1]; ch = song[ni][SN_C]; ratio = song[ni][SN_V] / max(1, src[si][3])
        dt_bc.setdefault((j, ch), []).append(dt); dt_b.setdefault(j, []).append(dt)
        vr_bc.setdefault((j, ch), []).append(ratio); vr_b.setdefault(j, []).append(ratio)
        vr_c.setdefault(ch, []).append(ratio); vr.append(ratio)
    ticks = [n[SN_T] for n in song]
    med_key = {}
    for n in song:
        med_key.setdefault(n[SN_C], []).append(n[SN_K])
    med_key = {c: _median(v) for c, v in med_key.items()}
    have = {}
    for n in S_["notes"]:
        if (n[SN_T], n[SN_C], n[SN_K]) not in gone:
            have.setdefault((n[SN_C], n[SN_K]), []).append(n[SN_T])
    for v in have.values():
        v.sort()
    bar_len = R["bar_len"]

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1
    for si in R["missing"]:
        e, j = R["exp"][si]; s = src[si]
        lo, hi = bisect.bisect_left(ticks, e - bar_len), bisect.bisect_right(ticks, e + bar_len)
        near = [song[i] for i in range(lo, hi) if (song[i][SN_T], song[i][SN_C], song[i][SN_K]) not in gone]
        if near:
            cnt = {}
            for n in near:
                cnt[n[SN_C]] = cnt.get(n[SN_C], 0) + 1
            ch = min(near, key=lambda n: (abs(n[SN_K] - s[2]), -cnt[n[SN_C]], n[SN_C]))[SN_C]
        elif med_key:
            ch = min(med_key, key=lambda c: (abs(med_key[c] - s[2]), c))
        else:
            skip("no_channel"); continue
        sh = dt_bc.get((j, ch)) if len(dt_bc.get((j, ch)) or []) >= 2 else dt_b.get(j)
        t = max(0, e + int(round(_median(sh))) if sh else e)
        rs = next((x for x in (vr_bc.get((j, ch)), vr_b.get(j), vr_c.get(ch), vr) if x), None)
        vel = max(1, min(128, int(round(s[3] * (_median(rs) if rs else 1.0)))))
        hv = have.get((ch, s[2]), [])
        x = bisect.bisect_left(hv, t - dup_t)
        if x < len(hv) and hv[x] <= t + dup_t:
            skip("duplicate"); continue
        cover = [c for c in clips if c["pos"] <= t < c["pos"] + c["len"]]
        if not cover:
            skip("no_clip_plays_there"); continue

        def rank(c):
            pp = t - c["pos"] + c["s0"]
            mine = [r[0] for r in pats[c["pid"]]["notes"] if r[2] == ch and c["s0"] <= r[0] < c["s0"] + c["len"]]
            return (0, min(abs(p - pp) for p in mine)) if mine else (1 if pats[c["pid"]]["notes"] else 2, 0)
        c = min(cover, key=lambda c: (rank(c), -c["pos"], c["pid"]))
        pp = t - c["pos"] + c["s0"]
        tm = [r for r in pats[c["pid"]]["notes"] if r[2] == ch] or \
             [r for p in pats.values() for r in p["notes"] if r[2] == ch]
        tmpl = min(tm, key=lambda r: abs(r[0] - pp)) if tm else None
        key_w = max(0, min(131, s[2] + offs.get(ch, 0)))
        ops.append({"kind": "add", "clip": c["i"], "pid": c["pid"], "ppos": pp, "ch": ch, "key": key_w, "len": s[1],
                    "vel": vel, "tmpl": tmpl, "tick": t, "bar": j + 1})
        hv.insert(bisect.bisect_left(hv, t), t); have[(ch, s[2])] = hv
    return ops, skipped, unknown


def _cmp_write(R, L, restore, rem, shared, output):
    S_ = R["S"]; P = R["P"]; pats = S_["pats"]; dup_t = R["dup_t"]
    ops, skipped, unknown = _cmp_plan(R, L, restore, rem)
    plays = {}
    for c in S_["clips"]:
        if not c["muted"]:
            plays.setdefault(c["pid"], []).append(c)
    by_clip = {}                                                   # (clip, kind, ch, key) -> pattern positions
    for o in ops:
        by_clip.setdefault((o["clip"], o["kind"], o["ch"], o["key"]), []).append(o["ppos"])

    def equiv(o, ci):
        tol = 0 if o["kind"] == "del" else dup_t
        return any(abs(p - o["ppos"]) <= tol for p in by_clip.get((ci, o["kind"], o["ch"], o["key"]), ()))
    for o in ops:
        others = [c for c in plays.get(o["pid"], [])
                  if c["i"] != o["clip"] and c["s0"] <= o["ppos"] < c["s0"] + c["len"]]
        o["uniform"] = not S_["other_arr"].get(o["pid"]) and all(equiv(o, c["i"]) for c in others)
    conflict = sorted({o["pid"] for o in ops if not o["uniform"]})
    split, held = [], []
    for pid in conflict:
        (split if shared == "split" and not pats[pid]["ctrl"] else held).append(pid)
    d = S_["d"]; PB = H["PATTERN_BASE"]; clone_of = {}; made = []
    # PC-VERIFY: shared='split' - each placement of the pattern is its own pattern in FL's playlist (named
    # '<name> (bar N)'), plays the same notes as before plus its own edit; the pattern colour isn't copied
    for pid in split:                                              # one pattern per placement (the first keeps the
        cs = sorted(plays.get(pid, []), key=lambda c: c["pos"])    # original unless another arrangement places it)
        for c in (cs if S_["other_arr"].get(pid) else cs[1:]):
            nm = (pats[pid]["name"] or f"Pattern {pid}") + f" (bar {H['_tick_to_bar'](c['pos'], S_['meters'], P):g})"
            d, np_ = H["_add_pattern"](d, nm)
            d = H["_write_pattern_notes"](d, H["_events"](d), np_, [H["_rec_to_note"](r) for r in pats[pid]["notes"]])
            clone_of[c["i"]] = np_
            made.append({"pattern": np_, "name": nm, "from": pid})
    if clone_of:
        ev = H["_events"](d); recs = H["_playlist_at"](d, ev)[2]
        for ci, np_ in clone_of.items():
            recs[ci][6:8] = struct.pack("<H", PB + np_)
        d = H["_put_clips"](d, ev, recs)
    target, held_rows, done = {}, {}, {}
    for o in ops:
        if o["pid"] in held and not o["uniform"]:
            held_rows.setdefault(o["pid"], []).append(o); continue
        tp = clone_of.get(o["clip"], o["pid"]) if o["pid"] in split else o["pid"]
        ps = done.setdefault((tp, o["kind"], o["ch"], o["key"]), [])
        if any(abs(p - o["ppos"]) <= (0 if o["kind"] == "del" else dup_t) for p in ps):
            continue                                               # the same edit from another placement: once
        ps.append(o["ppos"]); target.setdefault(tp, []).append(o)
    ev = H["_events"](d); now = H["_patterns"](d, ev); added = removed = 0
    for tp, lst in sorted(target.items()):
        cur = [H["_rec_to_note"](r) for r in now[tp]["notes"]]
        for o in lst:
            if o["kind"] == "del":
                k = next((i for i, n in enumerate(cur) if tuple(n["_r"]) == tuple(o["rec"])), None)
                if k is not None:
                    cur.pop(k); removed += 1
            else:
                tm = o["tmpl"]
                rec = H["_note_record"](o["ppos"], o["len"], o["ch"], o["key"], o["vel"], False,
                                        H["_fl_fields"](tm) if tm else None, tm[9] if tm else 0)
                cur.append({"pos": o["ppos"], "len": o["len"], "key": o["key"], "vel": o["vel"], "ch": o["ch"],
                            "_r": rec}); added += 1
        d = H["_write_pattern_notes"](d, ev, tp, cur); ev = H["_events"](d)
    res = {"output": output, "restored": sum(1 for o in ops if o["kind"] == "add" and not
                                             (o["pid"] in held and not o["uniform"])),
           "removed": sum(1 for o in ops if o["kind"] == "del" and not (o["pid"] in held and not o["uniform"])),
           "records_written": {"added": added, "removed": removed}}
    if skipped:
        res["skipped"] = skipped
    if unknown:
        res["unknown_ids"] = unknown[:20]
    if made:
        res["split_patterns"] = made
    if held_rows:
        res["shared_patterns_not_edited"] = [
            {"pattern": pid, "name": pats[pid]["name"], "placed_at_bars": [H["_tick_to_bar"](c["pos"], S_["meters"], P)
                                                                           for c in plays.get(pid, [])][:12],
             "other_arrangements": S_["other_arr"].get(pid, 0), "edits_skipped": len(v),
             "why": ("it holds recorded automation / tempo, so it isn't copied" if pats[pid]["ctrl"] and
                     shared == "split" else "it plays more than once and the edit differs between its placements"),
             "fix": "shared='split' makes each placement its own pattern, then edits each" if not pats[pid]["ctrl"]
             else "split it by hand (FL: make unique) and run again"}
            for pid, v in sorted(held_rows.items())]
    if ops and (added or removed or made):
        try:
            had = set(H["_versions"](output)) if os.path.isfile(output) else set()
        except Exception:                                          # noqa: BLE001
            had = None
        H["_save"](d, output); res["written"] = True
        try:                                                       # the backup this write made (none: a new file,
            new_b = sorted(set(H["_versions"](output)) - had) if had is not None else []   # or one already taken
            if new_b:                                              # earlier in this same tool call)
                res["backup"] = new_b[-1]
        except Exception:                                          # noqa: BLE001
            pass
        res["undo"] = "flp_versions(project) lists the backup taken before this write; flp_restore(project, backup) " \
                      "puts it back"
    else:
        res["note"] = "nothing to write"
    return res


def flp_compare_midi(project, midi, channels=None, tolerance_beats=0.3, restore=False, remove=None, output=None,
                     shared="report", full=False, journal_from=None):
    """Compare a song with the MIDI it came from (see the section comment above). Read-only unless restore / remove."""
    t0 = time.time(); L = _cmp_limits()
    if not midi or not os.path.isfile(str(midi)):
        raise FileNotFoundError(f"MIDI file not found: {midi}")
    if not str(midi).lower().endswith((".mid", ".midi")):
        raise ValueError("midi: a .mid / .midi file")
    if shared not in ("report", "split"):
        raise ValueError("shared: 'report' (leave patterns placed more than once alone where their placements "
                         "differ) or 'split' (make those placements their own patterns)")
    tol = float(tolerance_beats)
    if not 0 < tol <= 2:
        raise ValueError("tolerance_beats: more than 0, at most 2")
    rem = []
    for x in ([remove] if isinstance(remove, str) else list(remove or [])):
        x = str(x).strip()
        k = CMP_REMOVE_KINDS.get(x.lower())
        if k:
            rem.append(k)
        elif CMP_ID.match(x):
            rem.append(x)
        else:
            raise ValueError(f"remove: 'stand_ins', 'repeated_new', 'near_duplicates' or note ids like 'n7680.1.60' "
                             f"from the report (got {x!r})")
    R = _cmp_analyse(project, midi, channels, tol, L, journal_from)
    left_out = {}
    if channels in (None, "", []):                                 # default: the channels that play the source, not
        fit = _cmp_channel_fit(R)                                  # the layers an arrangement added (choir, pads...)
        keep = [c for c, v in fit.items() if v >= float(L.get("channel_fit", 0.75))] or \
            [max(fit, key=lambda c: (fit[c], -c))] if fit else []
        if keep and sorted(keep) != R["want"]:
            left_out = {R["S"]["names"].get(c) or f"ch {c}": f"{round(100 * v)}% of its notes in the source"
                        for c, v in sorted(fit.items()) if c not in keep}
            channels = sorted(keep)
            R = _cmp_analyse(project, midi, channels, tol, L, journal_from)
    res = {"project": os.path.basename(project), "midi": os.path.basename(midi), **_cmp_report(R, L, full)}
    res["channels"] = {str(c): R["S"]["names"].get(c) or f"ch {c}" for c in R["want"]}
    if left_out:
        res["channels_left_out"] = left_out                        # pass channels= to compare them too
    steps = dict(R["steps"])
    if restore or rem:
        output = output or project
        H["writable"](output)
        t1 = time.time()
        w = _cmp_write(R, L, bool(restore), rem, shared, output)
        steps["write"] = round(time.time() - t1, 3)
        if w.get("written"):
            t1 = time.time()
            A = _cmp_analyse(output, midi, channels, tol, L, journal_from or project)
            w["after"] = {"missing": len(A["missing"]), "new": len(A["new"]), "stand_in_runs": len(A["stand"]),
                          "repeated_new_runs": len(A["rep_new"]), "near_duplicates": len(A["dups"])}
            if R["stand"] and "stand_ins" not in rem and A["rep_new"]:
                w["hint"] = (f"{len(R['stand'])} stand-in run(s) are still in the song; now that the source notes are "
                             "back they show as repeated_new: remove=['repeated_new'] deletes them")
            steps["verify"] = round(time.time() - t1, 3)
        res["write"] = w
    c = res["counts"]
    if c["compared"] and c["matched"] < 0.5 * c["compared"]:
        res["warning"] = (f"only {c['matched']} of {c['compared']} source notes matched: the song may be transposed, "
                          "shifted by part of a bar, or not made from this MIDI - check bar_map before restoring")
    if L.get("_error"):
        res["settings_error"] = L["_error"]
    steps["total"] = round(time.time() - t0, 3)
    res["steps_s"] = steps
    return res


def _register_compare(ns):
    """flp_compare_midi: a reader by default; restore / remove write through the open-project guard (flp_edit can
    batch it; the project open in FL is saved first and reloaded after)."""
    P, Sch = ns["_p"], ns["_schema"]
    guarded = ns["_guard_open_project"](flp_compare_midi, "project", "output")

    def tool(**kw):
        if not kw.get("restore") and not kw.get("remove"):
            kw.pop("output", None)                                # nothing is written: no FL save / reload
            return flp_compare_midi(**kw)
        kw["output"] = kw.get("output") or kw["project"]
        return guarded(**kw)
    tool.__wrapped__ = flp_compare_midi
    ns["compare_midi"] = flp_compare_midi
    ns["TOOLS"]["flp_compare_midi"] = (
        tool, "Compare a song with the MIDI it came from (a rework, cover, arrangement): maps each MIDI bar to a "
        "song bar (a new intro or cut moves the map per region; shown as ranges), then lists source notes the song "
        "lost (per bar / section, note@beat), new notes per channel, repeated 'one finger' stand-ins where source "
        "notes went, near-duplicate strikes; timing (onset spread vs the source in ms, chords split apart), velocity "
        "(spread, melody above the accompaniment) and the tempo map (steady or where it moves) - the parts that make "
        "playing sound amateur. restore=true writes the missing notes back (channel by nearest pitch, velocity and "
        "timing from matched neighbours, duplicates skipped); remove=['stand_ins'|'near_duplicates'|note ids] deletes. "
        "A pattern placed more than once is only edited where all its placements want the same edit (shared='split' "
        "makes them separate patterns). Backed up first (flp_restore undoes). full=true: every row + note ids.",
        Sch(project=ns["_FLP"], midi=ns["_STR"]("the source .mid file"),
            channels=P("array", "song channels to compare, index or name (default: every non-drum channel)", True,
                       items={"type": ["integer", "string"]}),
            tolerance_beats=ns["_NUM"]("a song note matches a source note on the same key within this many beats "
                                       "(default 0.3)", True),
            restore=ns["_BOOL"]("write the missing source notes back into the patterns that play there", True),
            remove=P("array", "'stand_ins', 'repeated_new', 'near_duplicates' and/or note ids from the report "
                     "(n<tick>.<ch>.<key>)",
                     True, items={"type": "string"}),
            shared=ns["_STR"]("report (default: leave a pattern placed several times alone where its placements "
                              "differ) | split (make each placement its own pattern, then edit)", True),
            output=ns["_STR"]("where to save a restore / remove (default: the project itself)", True),
            full=ns["_BOOL"]("every row, plus note ids", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_compare_midi"] = ("project", "output")


# ============================================================================= queued (lazy) edits
def _queued_reader(fn):
    """TOOLS wrapper: with edits queued for the project (lazy mode) read the queued state (the shadow copy: file +
    queued edits, the project's own journal) and say so; otherwise the file. Marked __lazy__ so the server doesn't
    wrap it again with its file-only reader."""
    def run(project=None, **kw):
        q = []
        try:
            if isinstance(project, str) and os.path.isfile(project) and H.get("_pending_get"):
                q = H["_pending_get"](project)
        except Exception:
            q = []
        if not q:
            return fn(project, **kw)
        sh = H["_shadow"](project)[0]
        kw.setdefault("journal_from", project)
        r = fn(sh, **kw)
        if H.get("_unshadow"):
            r = H["_unshadow"](r, sh, project, len(q), read=True)
        if isinstance(r, dict):
            r["read"] = f"the queued state: the file + {len(q)} queued edit(s) (FL gets them at flh_sync)"
        return r
    run.__wrapped__ = fn
    run.__lazy__ = True
    return run


# ============================================================================= fl_doctor helper
def doctor_check(project, clash_top=3):
    """{range: [issues], clashes: [top issues]} for fl_doctor (one model load)."""
    m = load_model(project)
    ck = ("doctor", clash_top)
    if ck in m["_c"]:
        return json.loads(m["_c"][ck])
    allx = find_clashes(m)
    rng = [i for i in allx if i["kind"] == "out_of_range"]
    rest = [i for i in allx if i["kind"] not in ("out_of_range", "weak_register") and i["severity"] != "info"]
    top = rest[:clash_top]
    if m.get("far"):                                              # a corrupt far-out clip: say so first
        top = [{"severity": "high", "kind": "far_notes", "where": f"bar {m['far']['bar']:,}", "parts": "playlist",
                "notes": f"{m['far']['notes']} note(s)", "why": _far_text(m),
                "fix": "find the clip with flp_playlist and move / shorten it (or delete it)"}] + top
    res = {"range": rng, "clashes": top, "clash_counts": {s: sum(1 for i in rest if i["severity"] == s)
                                                          for s in ("high", "med", "low")}}
    m["_c"][ck] = json.dumps(res)
    return res


# ============================================================================= registration
def register(ns):
    global H
    H = ns
    if not S.H:
        S.H = ns
    _CACHE.clear(); _DATA.clear(); _CH.clear()
    P, Sch = ns["_p"], ns["_schema"]
    ns["score_view"] = score_api; ns["clashes"] = clashes_api; ns["score_model"] = load_model
    ns["score_build_model"] = build_model; ns["score_entrances"] = entrances; ns["score_doctor"] = doctor_check
    ns["score_find_clashes"] = find_clashes; ns["instrument_for"] = instrument_for
    ns["score_rules"] = lambda: data("rules")
    ns["sounding_offsets"] = sounding_offsets            # {channel: semitones written above the sounding pitch}
    ns["access_words"] = wordy
    _register_compare(ns)                                # flp_compare_midi (song vs its source MIDI)
    ns["TOOLS"]["flp_score"] = (
        _queued_reader(score_view), "Read a song like a score instead of raw notes (compact, ~1k tokens for a 3-minute song): key/mode "
        "with confidence and modulations per section, tempo/meter, chords per bar with roman numerals, per part: role, "
        "instrument, register, notes/bar, velocity, rhythm, active bars; melody contour, repeated bars, per-section "
        "energy (E0-9) and which parts play. Works on a .flp or .mid. Detail: section= (name or '9-16'), part= "
        "(channel or name), bars=, piano_roll=true (ASCII roll of one part), segment=half, format=json.",
        Sch(project=P("string", "the .flp or .mid"), section=ns["_STR"]("section name or bar range '9-16'", True),
            part=P(["integer", "string"], "channel index or part name: adds a per-section detail of that part", True),
            bars=ns["_STR"]("bar range 'a-b' (like section)", True),
            piano_roll=ns["_BOOL"]("ASCII piano roll of the part (or the melody), up to 8 bars", True),
            segment=ns["_STR"]("auto (bar, split when the halves differ) | bar | half", True),
            format=ns["_STR"]("text (default) | json", True),
            full=ns["_BOOL"]("also the part activity per section", True)))
    ns["TOOLS"]["flp_clashes"] = (
        _queued_reader(clashes), "Find wrong / clashing notes between layers (no FL needed): sustained minor-2nd / major-7th / tritone "
        "rubs between parts (weighted by length, velocity, register; passing tones skipped), melody notes that aren't "
        "chord tones on strong beats and don't resolve, bass notes against the chord, muddy low voicings, too many "
        "layers in one octave, support layers above the melody, notes outside an instrument's range (orchestral, GM, "
        "synth roles), kick/bass pitch fights (journal channels.N.pitch), bass/melody octaves as info. Ranked; each "
        "issue names the bar, parts, notes, why, and the fix (flp_edit_notes call). Tolerance follows the genre "
        "(jazz / lo-fi / soul allow extensions; house / rock / afrobeats / funk / reggae... 'groove' allow chromatic "
        "approach and walking passing notes in the bass) or tolerance=strict|normal|groove|jazz|0..1.",
        Sch(project=P("string", "the .flp or .mid"), section=ns["_STR"]("section name or bar range", True),
            bars=ns["_STR"]("bar range 'a-b'", True),
            tolerance=P(["string", "number"], "auto (genre) | strict | normal | groove | jazz | 0..1 (0 strict, 1 jazz)", True),
            genre=ns["_STR"]("override the genre used for tolerance", True),
            limit=ns["_INT"]("max issues (default 10)", True),
            min_severity=ns["_STR"]("high | med | low (default low)", True),
            kinds=P("array", "only these kinds: rub voicing melody_nct bass_root mud crowd crossing out_of_range "
                    "weak_register kick_bass octaves", True, items={"type": "string"}),
            full=ns["_BOOL"]("include scores and info items", True)))
