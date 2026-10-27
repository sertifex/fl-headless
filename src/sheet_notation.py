"""sheet_notation: exact notes from a sheet-music reading (ABC 2.1 text or MusicXML), validated for reading mistakes.

Pure Python, stdlib only, importable on its own (not an fl-headless extension; flh_sheet.py builds the tools on it).
It reads nothing but the MusicXML/.mxl path it is given and writes nothing but the path given to to_midi: callers
check permissions (ns["writable"]) first.

API
  parse_abc(text, ppq=960, tune=None) -> Score        first tune (or the one whose X: equals `tune`)
  parse_musicxml(src, ppq=960) -> Score                src: path (.musicxml/.xml/.mxl), XML text or bytes
  parse_score_data(data, ppq=960, parts=None) -> Score  per-bar JSON reading (format flh-score/1, docs/sheet_music.md):
      {"meter", "key", "tempo", "bars": {"9": {"chord", "section", "mel": [[beat, len, "Bb4"], ...], "parts": {...}}}}
      beats 0-based quarters; overlapping notes in a part become extra ABC voices of the same name (one track / one
      inline voice); graces (length 0) never overlap the same key; checks with the data's bar numbers;
      .score_info = layers, sections, chords, per-line beat sums, the generated ABC
  diff_score_data(a, b) / merge_score_data(readings, prefer=None)  two readings bar by bar per part / page-range
      readings joined with overlap + conflict checks
  validate(score, strict=False, max_issues=40) -> [issue, ...] ranked worst first. issue = {"severity": "high" |
      "med" | "low", "kind", "bar" (printed measure number), "voice", "line" (source line), "message", "fix"}.
      strict=False leaves out "low" (info) issues.
  to_midi(score, path=None) -> {"path", "tracks", "notes", "ppq"}  format-1 file (conductor track with tempo, meter
      and key-signature events, then one track per voice with name + program). path=None: returns {"bytes": ...}.
  to_inline(score, pickup="pad", fl_meter=None) -> {"voices": {name: [[bar, len_bars, pitch, vel], ...]},
      "summary": {...}}  fl-headless inline notes (bar 1 = song start, bars of the FL project's single meter =
      the score's main meter unless fl_meter=(n, d)). pickup="pad": a pickup bar ends where FL bar 2 starts so
      score bar 2 lands on FL bar 2; pickup="start": the pickup starts at bar 1.
  diff(a, b, max_bars=30) -> {"same", "bars_a", "bars_b", "differing", "bars": [...], "meta": [...]}  written
      bar-by-bar comparison of two readings (or a reading vs a MusicXML export); bars are aligned with difflib so an
      inserted or dropped bar shows as one insert/delete, not as every later bar differing.
  summary(score) / score.summary() -> bars, meters, keys, tempos, per-voice ranges and note counts.

Score
  ppq, title, composer, source ("abc" | "musicxml")
  meters [(tick, num, den)], keys [(tick, "G major")], tempos [(tick, bpm)]   written timeline (no repeats)
  tempo_map [(tick, bpm)], meter_map, key_map                                playback timeline (repeats expanded)
  voices [Voice]: name, id, part, staff, clef, program (GM 0-127 hint or None), piano (bool)
  measures [dict]: num (printed number: a pickup is 0 in ABC; MusicXML keeps its own), index, start_tick (written),
      len_ticks, meter (n, d) or None, rep_start, rep_end, rep_times, volta (sorted list) or None, nav (list of
      segno / coda / tocoda / fine / dc / ds), implicit (short bar that is allowed: pickup, last bar, bar split
      by a repeat), line
  order [measure index, ...] playback order (repeats, 1st/2nd endings, D.C./D.S. al Fine/al Coda when marked)
  notes [dict] playback notes sorted by start: start_tick, len_ticks, pitch (MIDI, C4 = 60, sounding), vel, voice,
      staff, tied (tie-merged), grace, measure (printed number), source_line
  parse_issues [issue]   problems met while parsing (also returned by validate)
  bar_notes() -> [{"bar", "meter", "notes": [(start_q, len_q, pitch), ...]}] per written bar, all voices, quarter
      notes from the bar start (for chroma / audio comparison).

Conventions
  ABC: note letters are absolute pitches (C = middle C = MIDI 60) whatever the clef; the clef informs validation.
  K:/V: clef=treble-8 (or +8), octave=n and transpose=n shift the sounding pitch (ABC 2.1 player rules).
  Bare "Q:120" follows ABC 2.1 (unit-note beats); write Q:1/4=120. A key or meter change written in one voice only
  applies to that voice's key (validate warns); meter changes apply to the bar for all voices.
  Dynamics apply to the voice they are written in; a voice without any dynamics follows the first voice that has
  them. Velocities: pppp 16 ppp 28 pp 40 p 54 mp 66 mf 80 (default) f 94 ff 108 fff 118 ffff 126, sfz/sf/fz 112,
  accent +20, marcato +28; staccato plays half length, staccatissimo a quarter.
  Grace notes take a 32nd each (at most half the main note) from the start of the note they decorate (the main
  note starts later and is shorter; bar durations stay exact).
  MusicXML: pitch is the written pitch, <transpose> (chromatic + octave-change) makes it sounding; clef-octave-change
  is display only (as MusicXML defines). Directions' dynamics apply to the whole part. Cue notes are silent.

Known gaps: ABC voice overlay (&), P: part order, U: user symbols, microtones, ornaments (trill/mordent play as
written), hairpins, MusicXML score-timewise, percussion maps (unpitched notes play their display pitch), lyrics.
"""
import bisect
import difflib
import io
import math
import os
import re
import struct
import zipfile
from fractions import Fraction as Fr
from xml.parsers import expat

__all__ = ["parse_abc", "parse_musicxml", "parse_score_data", "load_score_data", "diff_score_data",
           "merge_score_data", "parse_pitch", "validate", "to_midi", "to_inline", "diff", "summary", "Score", "Voice",
           "pitch_name", "PPQ"]

PPQ = 960
STEPS = "CDEFGAB"
STEP_SEMI = (0, 2, 4, 5, 7, 9, 11)
DEFAULT_VEL = 80
DYN = {"pppp": 16, "ppp": 28, "pp": 40, "p": 54, "mp": 66, "mf": 80, "f": 94, "ff": 108, "fff": 118, "ffff": 126}
SFZ = {"sfz", "sf", "fz", "sffz", "sfp", "rfz", "rf", "sfzp"}
SEV = {"error": "high", "warning": "med", "info": "low"}
_RANK = {"high": 0, "med": 1, "low": 2}
_MODES = {"maj": 0, "ion": 0, "mix": -1, "dor": -2, "min": -3, "aeo": -3, "m": -3, "phr": -4, "loc": -5, "lyd": 1}
_MODE_NAME = {0: "major", -1: "mixolydian", -2: "dorian", -3: "minor", -4: "phrygian", -5: "locrian", 1: "lydian"}
_LETTER_FIFTHS = {"F": -1, "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5}
# far outside a clef's staff (about 5+ ledger lines), on written pitch
CLEF_RANGE = {"treble": (43, 100), "bass": (24, 76), "alto": (38, 86), "tenor": (36, 82), "baritone": (28, 78)}
CLEF_MID = {"treble": 71, "bass": 50, "alto": 60, "tenor": 57, "baritone": 53}
PIANO_RANGE = (21, 108)


def pitch_name(p, alter=None):
    """MIDI number -> 'C4' / 'F#3' / 'Bb2' (C4 = 60). alter (from a spelled note) picks # or b."""
    if p is None:
        return "?"
    pc, octv = p % 12, p // 12 - 1
    if alter is not None and alter < 0 and pc in (1, 3, 6, 8, 10):
        return "C D EF G A B"[(pc + 1) % 12].strip() + "b" + str(octv if pc != 11 else octv)
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return names[pc] + str(octv)


def _spell(n):
    """A spelled written note -> 'F#4'."""
    acc = {2: "##", 1: "#", 0: "", -1: "b", -2: "bb"}.get(n.get("alter", 0), "")
    if "step" not in n:
        return pitch_name(n["pitch"])
    return STEPS[n["step"]] + acc + str(n["octave"])


def _q_name(q):
    """Duration in quarters -> 'an eighth', 'a dotted quarter', ..."""
    names = {Fr(4): "a whole", Fr(2): "a half", Fr(1): "a quarter", Fr(1, 2): "an eighth", Fr(1, 4): "a 16th",
             Fr(1, 8): "a 32nd", Fr(1, 16): "a 64th"}
    q = Fr(q)
    if q in names:
        return names[q] + " note"
    if q * 2 / 3 in names:
        return "a dotted " + names[q * 2 / 3].split(" ", 1)[1] + " note"
    if q * 3 / 2 in names:
        return names[q * 3 / 2] + "-note triplet"
    return f"{q} quarter{'s' if q != 1 else ''}"


def _nice(q):
    """A duration a tuplet group may span: a power-of-two note value, plain or dotted."""
    q = Fr(q)
    for k in range(-6, 5):
        b = Fr(2) ** k
        if q == b or q == b * Fr(3, 2):
            return True
    return False


# ============================================================================= keys / meters
def key_alters(fifths):
    """fifths (-7..7, more allowed) -> {letter: alter}."""
    alt = {}
    for k, L in enumerate("FCGDAEB"):
        if fifths > 0:
            alt[L] = (fifths - k + 6) // 7 if fifths > k else 0
        elif fifths < 0:
            alt[L] = -((-fifths - (6 - k) + 6) // 7) if -fifths > 6 - k else 0
        else:
            alt[L] = 0
    return alt


def key_name(fifths, mode=0):
    tf = fifths - mode
    L = "FCGDAEB"[(tf + 1) % 7]
    acc = (tf + 1) // 7
    return L + ("#" * acc if acc > 0 else "b" * -acc) + " " + _MODE_NAME.get(mode, "major")


def _key(fifths=0, mode=0, extra=None, name=None):
    alters = key_alters(fifths)
    if extra:
        alters.update(extra)
    return {"fifths": fifths, "mode": mode, "alters": alters, "name": name or key_name(fifths, mode)}


C_MAJOR = _key(0, 0)


def _meter_q(m):
    return Fr(4 * m[0], m[1]) if m else None


# ============================================================================= model
class Voice:
    def __init__(self, vid, name=None):
        self.id = vid
        self.name = name or str(vid)
        self.part = None
        self.staff = None
        self.clef = "treble"
        self.program = None
        self.piano = False
        self.primary = True
        self.measures = []          # per global measure: {"events", "len", "present", "line"}
        self.dyn = []               # [((midx, pos), vel)]
        self.key_changes = []       # [(midx, name)]

    def __repr__(self):
        return f"Voice({self.name!r}, staff={self.staff}, clef={self.clef})"


class Score:
    def __init__(self, ppq=PPQ, source=""):
        self.ppq = ppq
        self.source = source
        self.title = ""
        self.composer = ""
        self.voices = []
        self.measures = []
        self.order = []
        self.notes = []
        self.meters, self.keys, self.tempos = [], [], []
        self.tempo_map, self.meter_map, self.key_map = [], [], []
        self.tempo_assumed = False
        self.parse_issues = []
        self.divisions = None
        self.part_names = {}

    # compatibility helpers for tool layers
    @property
    def meta(self):
        return {"title": self.title, "composer": self.composer, "meters": self.meters, "keys": self.keys,
                "tempos": self.tempos, "divisions": self.divisions, "source": self.source, "ppq": self.ppq}

    @property
    def bars(self):
        return self.measures

    @property
    def meter(self):
        return main_meter(self)

    @property
    def key(self):
        return self.keys[0][1] if self.keys else None

    @property
    def tempo(self):
        return self.tempos[0][1] if self.tempos else None

    def tick(self, q):
        return int(round(q * self.ppq))

    def summary(self):
        return summary(self)

    def bar_notes(self):
        out = []
        for i, gm in enumerate(self.measures):
            ns = []
            for v in self.voices:
                for sn in v.sound[i]:
                    ns.append((float(sn["pos"]), float(sn["dur"]), sn["pitch"]))
            out.append({"bar": gm["num"], "meter": gm["meter"], "notes": sorted(ns)})
        return out

    def __repr__(self):
        return f"Score({self.title!r}, {len(self.measures)} bars, {len(self.voices)} voices, {len(self.notes)} notes)"


def _issue(sev, kind, bar, voice, line, message, fix=""):
    return {"severity": SEV.get(sev, sev), "kind": kind, "bar": bar, "voice": voice, "line": line,
            "message": message, "fix": fix}


def _vm(line=0):
    return {"events": [], "len": Fr(0), "present": True, "line": line}


# ============================================================================= ABC
_NOTE_RE = re.compile(r"(\^\^|\^|__|_|=)?([A-Ga-g])([',]*)(\d*)(/*)(\d*)")
_REST_RE = re.compile(r"([zx])(\d*)(/*)(\d*)")
_MREST_RE = re.compile(r"([ZX])(\d*)")
_BAR_RE = re.compile(r"(:*)(\[\|\]?|\|[|\]]*|(?=::))(:*)")
_VOLTA_RE = re.compile(r"\[?(\d+(?:[,\-]\d+)*)")
_TUP_RE = re.compile(r"\((\d+)(?::(\d*))?(?::(\d*))?")
_FIELD_RE = re.compile(r"^([A-Za-z]):(.*)$")
_INLINE_RE = re.compile(r"\[([A-Za-z]):([^\]]*)\]")
_NAV_TEXT = [(re.compile(r"d\.?\s*c\.?\s*al\s*fine", re.I), ("dc", "al_fine")),
             (re.compile(r"d\.?\s*c\.?\s*al\s*coda", re.I), ("dc", "al_coda")),
             (re.compile(r"d\.?\s*s\.?\s*al\s*fine", re.I), ("ds", "al_fine")),
             (re.compile(r"d\.?\s*s\.?\s*al\s*coda", re.I), ("ds", "al_coda")),
             (re.compile(r"^\s*(d\.?\s*c\.?|da\s*capo)\s*$", re.I), ("dc", None)),
             (re.compile(r"^\s*(d\.?\s*s\.?|dal\s*segno)\s*$", re.I), ("ds", None)),
             (re.compile(r"^\s*to\s*coda", re.I), ("tocoda", None)),
             (re.compile(r"^\s*fine\s*$", re.I), ("fine", None)),
             (re.compile(r"^\s*coda\s*$", re.I), ("coda", None)),
             (re.compile(r"^\s*segno\s*$", re.I), ("segno", None))]
_DECO_NAV = {"segno": ("segno",), "coda": ("coda",), "fine": ("fine",), "dacoda": ("tocoda",), "tocoda": ("tocoda",),
             "d.c.": ("dc",), "dacapo": ("dc",), "d.s.": ("ds",), "dalsegno": ("ds",), "d.c.alfine": ("dc", "al_fine"),
             "d.c.alcoda": ("dc", "al_coda"), "d.s.alfine": ("ds", "al_fine"), "d.s.alcoda": ("ds", "al_coda")}
_SHORT_DECO = {".": "staccato", "~": "roll", "H": "fermata", "L": "accent", "M": "lowermordent", "O": "coda",
               "P": "uppermordent", "S": "segno", "T": "trill", "u": "upbow", "v": "downbow"}


def _nav_from_text(t):
    for rx, marks in _NAV_TEXT:
        if rx.search(t):
            return marks
    return ()


def _parse_meter(s):
    s = s.strip()
    if not s or s.lower() == "none":
        return None
    if s == "C":
        return (4, 4)
    if s == "C|":
        return (2, 2)
    m = re.match(r"\(?([\d+ ]+)\)?\s*/\s*(\d+)", s)
    if not m:
        raise ValueError(f"meter '{s}': use M:4/4, M:6/8, M:C, M:C| or M:none")
    num = sum(int(x) for x in re.findall(r"\d+", m.group(1)))
    if num <= 0 or int(m.group(2)) <= 0:                          # M:0/4 has no bar length (crashed to_inline)
        raise ValueError(f"meter '{s}': use M:4/4, M:6/8, M:C, M:C| or M:none")
    return (num, int(m.group(2)))


def _parse_frac(s):
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", s)
    if m:
        return Fr(int(m.group(1)), int(m.group(2)))
    m = re.match(r"\s*(\d+)", s)
    if m:
        return Fr(int(m.group(1)))
    raise ValueError(f"note length '{s}': use L:1/8")


def _parse_tempo(s, unit):
    """Q: field -> quarter-note bpm (or None)."""
    t = re.sub(r'"[^"]*"', " ", s)
    m = re.search(r"((?:\d+/\d+\s*)+)=\s*(\d+(?:\.\d+)?)", t)
    if m:
        tot = sum(Fr(int(a), int(b)) for a, b in re.findall(r"(\d+)/(\d+)", m.group(1)))
        return float(m.group(2)) * float(tot) * 4
    m = re.search(r"C\s*=\s*(\d+)", t)
    if m:
        return float(m.group(1))
    m = re.search(r"^\s*(\d+(?:\.\d+)?)\s*$", t)
    if m:
        return float(m.group(1)) * float(unit) * 4
    return None


def _parse_clef(tokens):
    """Pull clef settings out of K:/V: tokens -> (dict, rest_tokens)."""
    out, rest = {}, []
    for tok in tokens:
        kv = re.match(r"(\w+)=(.*)$", tok)
        if kv:
            k, v = kv.group(1).lower(), kv.group(2).strip('"')
            if k == "clef":
                out.update(_clef_word(v) or {"clef": "treble"})
            elif k in ("transpose", "t"):
                out["transpose"] = int(v)
            elif k == "octave":
                out["octave"] = int(v)
            elif k in ("name", "nm"):
                out["name"] = v
            elif k in ("subname", "snm", "sname"):
                out["sname"] = v
            elif k == "program":
                out["program"] = int(v)
            continue
        cw = _clef_word(tok)
        if cw:
            out.update(cw)
        else:
            rest.append(tok)
    return out, rest


def _clef_word(w):
    m = re.fullmatch(r"(treble|bass|alto|tenor|baritone|perc|none)(\d)?([+-]8)?", w.lower())
    if not m:
        return None
    name = m.group(1)
    if name == "bass" and m.group(2) == "3":
        name = "baritone"
    if name in ("perc", "none"):
        name = "treble"
    return {"clef": name, "clef_oct": {"+8": 1, "-8": -1}.get(m.group(3), 0)}


def _parse_key(s):
    """K: field -> (key dict or None (unchanged), clef dict)."""
    toks = re.findall(r'\w+="[^"]*"|\S+', s)
    clef, toks = _parse_clef(toks)
    if not toks:
        return None, clef
    first = toks.pop(0)
    if first.lower() == "none":
        return _key(0, 0, name="none"), clef
    if first in ("HP", "Hp"):
        return _key(2, -1, {"C": 1, "F": 1, "G": 0} if first == "Hp" else None,
                    name="highland pipes"), clef
    m = re.fullmatch(r"([A-G])([#b]?)(\w*)", first)
    if not m:
        if re.fullmatch(r"(\^\^|\^|__|_|=)[a-gA-G]", first):
            toks.insert(0, first)
            tonic_f, mode = 0, 0
        else:
            raise ValueError(f"key '{s}': use K:G, K:Am, K:D dorian, K:Bb mix, K:F# minor")
    else:
        tonic_f = _LETTER_FIFTHS[m.group(1)] + {"#": 7, "b": -7, "": 0}[m.group(2)]
        ms = m.group(3).lower()
        if not ms and toks and re.fullmatch(r"[A-Za-z]+", toks[0]) and toks[0].lower()[:3] in _MODES:
            ms = toks.pop(0).lower()
        if ms in ("", "maj", "major", "ion", "ionian"):
            mode = 0
        elif ms == "m":
            mode = -3
        elif ms[:3] in _MODES:
            mode = _MODES[ms[:3]]
        else:
            raise ValueError(f"key '{s}': unknown mode '{ms}'")
    fifths = tonic_f + mode
    extra, explicit_only = {}, False
    for t in toks:
        if t.lower() == "exp":
            explicit_only = True
            continue
        am = re.fullmatch(r"(\^\^|\^|__|_|=)([a-gA-G])", t)
        if am:
            extra[am.group(2).upper()] = {"^": 1, "^^": 2, "_": -1, "__": -2, "=": 0}[am.group(1)]
    k = _key(fifths, mode)
    if explicit_only:
        k["alters"] = {L: 0 for L in STEPS}
    k["alters"].update(extra)
    if extra:
        k["name"] += " (" + " ".join(f"{L}{'#' if a > 0 else 'b' if a < 0 else 'n'}" for L, a in extra.items()) + ")"
    return k, clef


class _VS:
    """ABC voice parse state."""

    def __init__(self, vid, d):
        self.v = Voice(vid)
        self.unit = d["unit"]
        self.meter = d["meter"]
        self.key = d["key"]
        self.clef = "treble"
        self.clef_oct = 0
        self.octave = 0
        self.transpose = 0
        self.ms = []                 # finished measures
        self.minfo = []              # per measure: dict(start/end attrs)
        self.cur = None
        self.bar_acc = {}
        self.tie_carry = {}
        self.deco = []
        self.grace = None            # list while inside {...}
        self.pend_grace = []
        self.tup = None              # [remaining, factor, record]
        self.tie_src = None          # last event: its tied notes carry their accidental over the bar line
        self.last_bar_attrs = None
        self.broken = None
        self.last = None
        self.last_bar_line = None
        self.volta = None
        self.pend_meter = None
        self.started = False


class _AbcParser:
    def __init__(self, text, ppq, tune):
        self.ppq = ppq
        self.issues = []
        self.text = text
        self.tune = tune
        self.score = Score(ppq, "abc")
        self.vs = {}
        self.vorder = []
        self.cur = None
        self.defaults = {"unit": None, "meter": (4, 4), "key": C_MAJOR}
        self.header_voice = {}
        self.programs = {}
        self.tempos = []             # (vid, midx, pos, bpm)
        self.score_dir = None
        self.line = 0

    # ------------------------------------------------------------------ helpers
    def issue(self, sev, kind, msg, fix="", bar=None, voice=None):
        self.issues.append(_issue(sev, kind, bar, voice, self.line, msg, fix))

    def voice(self, vid, props=None):
        if vid not in self.vs:
            d = dict(self.defaults)
            if d["unit"] is None:
                d["unit"] = self._default_unit()
            vs = _VS(vid, d)
            hp = dict(self.header_voice.get(vid, {}))
            if props:
                hp.update(props)
            self._apply_vprops(vs, hp)
            self.vs[vid] = vs
            self.vorder.append(vid)
            self._new_measure(vs)
        elif props:
            self._apply_vprops(self.vs[vid], props)
        return self.vs[vid]

    def _apply_vprops(self, vs, p):
        if "name" in p:
            vs.v.name = p["name"]
        elif "sname" in p and vs.v.name == str(vs.v.id):
            vs.v.name = p["sname"]
        if "clef" in p:
            vs.clef = p["clef"]
            vs.clef_oct = p.get("clef_oct", 0)
        for k in ("octave", "transpose"):
            if k in p:
                setattr(vs, k, p[k])
        if "program" in p:
            vs.v.program = p["program"]

    def _default_unit(self):
        m = self.defaults["meter"]
        if m and Fr(m[0], m[1]) < Fr(3, 4):
            return Fr(1, 16)
        return Fr(1, 8)

    def _new_measure(self, vs):
        vm = _vm(self.line)
        vm.update({"pos": Fr(0), "meter": vs.meter, "meter_set": False, "key": vs.key, "nav": [], "mm": 0,
                   "content": False, "start": set(), "end": set(), "volta": vs.volta, "tuplets": [], "coda0": False,
                   "same_line_bar": False, "rep_times": 2})
        if vs.pend_meter is not None:
            vm["meter"] = vs.pend_meter[0]
            vm["meter_set"] = True
            vs.pend_meter = None
        vs.cur = vm
        return vm

    # ------------------------------------------------------------------ top level
    def run(self):
        lines = self.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        in_tune = False
        in_body = False
        found = False
        seen_x = False
        for ln, raw in enumerate(lines, 1):
            self.line = ln
            line = raw
            if line.startswith("%%"):
                if in_tune or not found:
                    self._directive(line[2:].strip(), in_body)
                continue
            line = re.sub(r"(?<!\\)%.*$", "", line).rstrip()
            fm = _FIELD_RE.match(line) if not line.startswith("|:") else None
            if fm and fm.group(1) == "X":
                seen_x = True
                if found and in_tune:
                    break                          # next tune: stop
                if self.tune is None or fm.group(2).strip() == str(self.tune):
                    in_tune, found = True, True
                continue
            if not in_tune:
                if not found and not seen_x and fm and fm.group(1) in "TCMLQKV":
                    in_tune, found = True, True    # no X: line at all
                else:
                    continue
            if not line.strip():
                if in_body:
                    in_tune = False                # blank line ends the tune
                continue
            if fm and in_body and fm.group(2)[:1] in "|:]":
                fm = None                          # a music line like "e:|" that only looks like a field
            if fm:
                f, val = fm.group(1), fm.group(2).strip()
                if not in_body:
                    self._header_field(f, val)
                    if f == "K":
                        in_body = True
                    continue
                self._body_field(f, val)
                continue
            in_body = True
            if self.cur is None:
                self.cur = self._first_voice()
            self.voice(self.cur)
            self._music(line, ln)
        if not found:
            raise ValueError("no ABC tune found (needs at least K: and some notes)")
        if not self.vs:
            raise ValueError("ABC tune has no notes (needs K: and a body)")
        for vs in self.vs.values():
            self._close(vs, set(), final=True)
        return self._build()

    def _first_voice(self):
        if self.cur:
            return self.cur
        return next(iter(self.header_voice), "1")

    def _directive(self, d, in_body):
        m = re.match(r"MIDI\s+program\s+(?:(\d+)\s+)?(\d+)", d, re.I)
        if m:
            prog = int(m.group(2))
            if in_body and self.cur:
                self.voice(self.cur).v.program = prog
            else:
                self.programs["*"] = prog
            return
        m = re.match(r"(score|staves)\s+(.*)", d, re.I)
        if m:
            self.score_dir = m.group(2)

    def _header_field(self, f, val):
        sc = self.score
        if f == "T":
            sc.title = sc.title or val
        elif f == "C":
            sc.composer = sc.composer or val
        elif f == "M":
            self.defaults["meter"] = self._safe(_parse_meter, val, (4, 4))
        elif f == "L":
            self.defaults["unit"] = self._safe(_parse_frac, val, Fr(1, 8))
        elif f == "Q":
            self._pending_q = val
        elif f == "K":
            k, clef = self._safe(_parse_key, val, (None, {}))
            if k:
                self.defaults["key"] = k
            if clef:
                self.header_voice.setdefault("__k", {}).update(clef)
            if self.defaults["unit"] is None:
                self.defaults["unit"] = self._default_unit()
            kc = self.header_voice.pop("__k", None)
            vid = self._first_voice()
            if kc:
                self.header_voice.setdefault(vid, {}).update(kc)
            q = getattr(self, "_pending_q", None)
            if q:
                bpm = _parse_tempo(q, self.defaults["unit"])
                if bpm:
                    self.tempos.append((None, 0, Fr(0), bpm))
        elif f == "V":
            vid, props = self._vfield(val)
            self.header_voice.setdefault(vid, {}).update(props)

    def _safe(self, fn, val, default):
        try:
            return fn(val)
        except ValueError as e:
            self.issue("error", "syntax", str(e))
            return default

    def _vfield(self, val):
        toks = re.findall(r'\w+="[^"]*"|\S+', val)
        if not toks:
            return "1", {}
        vid = toks[0]
        props, _ = _parse_clef(toks[1:])
        return vid, props

    def _body_field(self, f, val):
        if f == "V":
            vid, props = self._vfield(val)
            self.cur = vid
            self.voice(vid, props)
            return
        vs = self.voice(self.cur or self._first_voice())
        if self.cur is None:
            self.cur = vs.v.id
        self._field(vs, f, val)

    def _field(self, vs, f, val):
        if f == "M":
            m = self._safe(_parse_meter, val, vs.meter)
            vs.meter = m
            if vs.cur["content"]:
                vs.pend_meter = (m,)
            else:
                vs.cur["meter"], vs.cur["meter_set"] = m, True
        elif f == "L":
            vs.unit = self._safe(_parse_frac, val, vs.unit)
        elif f == "K":
            k, clef = self._safe(_parse_key, val, (None, {}))
            if k:
                vs.key = k
                vs.bar_acc = {}
                if not vs.cur["content"]:
                    vs.cur["key"] = k
                vs.v.key_changes.append((len(vs.ms), k["name"]))
            if clef:
                self._apply_vprops(vs, clef)
        elif f == "Q":
            bpm = _parse_tempo(val, vs.unit)
            if bpm:
                self.tempos.append((vs.v.id, len(vs.ms), vs.cur["pos"], bpm))
        elif f == "V":
            vid, props = self._vfield(val)
            self.cur = vid
            self.voice(vid, props)
        # T: P: w: W: N: R: I: etc. are ignored

    # ------------------------------------------------------------------ music lines
    def _music(self, line, ln):
        i, n = 0, len(line)
        vs = self.vs[self.cur]
        while i < n:
            vs = self.vs[self.cur]
            c = line[i]
            if c in " \t`$y\\":
                i += 1
                continue
            if c == '"':
                j = line.find('"', i + 1)
                if j < 0:
                    self.issue("warning", "syntax", 'unclosed "..." (chord symbol or text)', 'close it with "')
                    return
                txt = line[i + 1:j]
                if txt[:1] in "^_<>@":
                    txt = txt[1:]
                for mk in _nav_from_text(txt):
                    self._nav(vs, mk)
                i = j + 1
                continue
            if c in "!+":
                j = line.find(c, i + 1)
                if j < 0:
                    i += 1
                    continue
                self._deco(vs, line[i + 1:j])
                i = j + 1
                continue
            if c in _SHORT_DECO and not (c == "." and line[i + 1:i + 2] == "|"):
                if c == "." and line[i + 1:i + 2] == "(":
                    i += 2
                    continue
                self._deco(vs, _SHORT_DECO[c])
                i += 1
                continue
            if c == "[":
                m = _INLINE_RE.match(line, i)
                if m:
                    self._inline_field(m.group(1), m.group(2).strip())
                    i = m.end()
                    continue
                if line[i + 1:i + 2] == "|":
                    m = _BAR_RE.match(line, i)
                    i = self._bar(vs, line, m, ln)
                    continue
                m = re.match(r"\[(\d+(?:[,\-]\d+)*)", line[i:])
                if m:
                    self._volta(vs, m.group(1))
                    i += m.end()
                    continue
                if line[i + 1:i + 2] == '"':
                    j = line.find('"', i + 2)
                    i = j + 1 if j > 0 else n
                    continue
                i = self._chord(vs, line, i, ln)
                continue
            if c in "|:":
                m = _BAR_RE.match(line, i)
                if m and m.end() > i and ("|" in m.group(0) or "::" in m.group(0)):
                    i = self._bar(vs, line, m, ln)
                    continue
                self.issue("warning", "syntax", f"stray '{c}'", "remove it or make it a bar line")
                i += 1
                continue
            if c == "]":
                self.issue("warning", "syntax", "stray ']'")
                i += 1
                continue
            if c == "(":
                m = _TUP_RE.match(line, i)
                if m:
                    self._tuplet(vs, m)
                    i = m.end()
                else:
                    i += 1                         # slur
                continue
            if c == ")":
                i += 1
                continue
            if c == "{":
                vs.grace = []
                i += 1
                if line[i:i + 1] == "/":
                    i += 1
                continue
            if c == "}":
                if vs.grace is not None:
                    vs.pend_grace.extend(vs.grace)
                vs.grace = None
                i += 1
                continue
            if c == "-":
                if vs.last is not None:
                    for nt in vs.last["notes"]:
                        nt["tie"] = True
                else:
                    self.issue("warning", "tie", "tie '-' with no note before it")
                i += 1
                continue
            if c in "<>":
                j = i
                while j < n and line[j] == c:
                    j += 1
                self._broken(vs, c, j - i)
                i = j
                continue
            if c == "&":
                self.issue("warning", "unsupported", "voice overlay '&' is not supported; notes after it were "
                           "skipped", "write the second line as its own V: voice")
                j = line.find("|", i)
                i = j if j >= 0 else n
                continue
            m = _NOTE_RE.match(line, i)
            if m:
                self._note(vs, m, ln)
                i = m.end()
                continue
            m = _REST_RE.match(line, i)
            if m:
                self._rest(vs, m, ln)
                i = m.end()
                continue
            m = _MREST_RE.match(line, i)
            if m:
                self._mrest(vs, m, ln)
                i = m.end()
                continue
            self.issue("warning", "syntax", f"unknown symbol '{c}' skipped")
            i += 1

    def _inline_field(self, f, val):
        if f == "V":
            vid, props = self._vfield(val)
            self.cur = vid
            self.voice(vid, props)
            return
        self._field(self.vs[self.cur], f, val)

    def _deco(self, vs, d):
        d0 = d.strip()
        dl = d0.lower().replace(" ", "")
        if dl in DYN:
            vs.deco.append(("dyn", DYN[dl]))
        elif dl in SFZ:
            vs.deco.append(("sfz", 112))
        elif dl == "fp":
            vs.deco.append(("sfz", 94))
            vs.deco.append(("dyn", 54))
        elif dl in (">", "accent", "emphasis"):
            vs.deco.append(("art", "accent"))
        elif dl in ("^", "marcato"):
            vs.deco.append(("art", "marcato"))
        elif dl in ("staccato", "."):
            vs.deco.append(("art", "staccato"))
        elif dl in ("staccatissimo", "wedge"):
            vs.deco.append(("art", "staccatissimo"))
        elif dl in _DECO_NAV:
            for mk in _DECO_NAV[dl]:
                self._nav(vs, mk)
        # everything else (trill, fermata, slurs, hairpins, fingering...) has no effect on pitch/time here

    def _nav(self, vs, mk):
        cur = vs.cur
        if mk == "coda" and not cur["content"]:
            cur["coda0"] = True
        cur["nav"].append(mk)

    def _tuplet(self, vs, m):
        p = int(m.group(1))
        q = int(m.group(2)) if m.group(2) else None
        r = int(m.group(3)) if m.group(3) else p
        if q is None:
            compound = vs.meter and vs.meter[0] % 3 == 0 and vs.meter[0] > 3
            q = {2: 3, 3: 2, 4: 3, 6: 2, 8: 3}.get(p, 3 if compound else 2)
        if p <= 0 or r <= 0:
            self.issue("warning", "tuplet", f"tuplet ({m.group(0)[1:]} makes no sense")
            return
        if vs.tup and vs.tup[0] > 0:
            self._tup_done(vs, cut=True)
        rec = {"p": p, "q": q, "r": r, "durs": [], "line": self.line, "midx": len(vs.ms)}
        vs.tup = [r, Fr(q, p), rec]

    def _tup_done(self, vs, cut=False):
        rec = vs.tup[2]
        rec["cut"] = cut
        vs.cur["tuplets"].append(rec)
        vs.tup = None

    def _broken(self, vs, c, k):
        prev = vs.last
        if prev is None or prev.get("grace") or not vs.cur["events"] or vs.cur["events"][-1] is not prev:
            self.issue("warning", "syntax", f"broken rhythm '{c * k}' without a note before it in the bar")
            return
        big, small = Fr(2) - Fr(1, 2 ** k), Fr(1, 2 ** k)
        fp, fn = (big, small) if c == ">" else (small, big)
        old = prev["dur"]
        prev["dur"] = old * fp
        for nt in prev["notes"]:
            if "dur" in nt:
                nt["dur"] *= fp
        vs.cur["pos"] += prev["dur"] - old
        vs.broken = fn

    def _length(self, vs, a, sl, b):
        num = int(a) if a else 1
        if sl:
            den = int(b) if b else 2 ** len(sl)
            if b and len(sl) > 1:
                den = int(b) * 2 ** (len(sl) - 1)
        else:
            den = 1
        return vs.unit * 4 * Fr(num, den)              # quarters

    def _pitch(self, vs, acc, letter, octs):
        step = STEPS.index(letter.upper())
        octave = (4 if letter.isupper() else 5) + octs.count("'") - octs.count(",") + vs.octave
        kalter = vs.key["alters"].get(letter.upper(), 0)
        explicit = bool(acc)
        if acc:
            alter = {"^": 1, "^^": 2, "_": -1, "__": -2, "=": 0}[acc]
            vs.bar_acc[(step, octave)] = alter
        elif (step, octave) in vs.tie_carry:
            alter = vs.tie_carry[(step, octave)]
        elif (step, octave) in vs.bar_acc:
            alter = vs.bar_acc[(step, octave)]
        else:
            alter = kalter
        wp = (octave + 1) * 12 + STEP_SEMI[step] + alter
        return {"pitch": wp + vs.transpose + 12 * vs.clef_oct, "wpitch": wp, "step": step, "alter": alter,
                "octave": octave, "kalter": kalter, "explicit": explicit, "tie": False, "clef": vs.clef,
                "sstep": None if vs.transpose else step, "salter": None if vs.transpose else alter}

    def _place(self, vs, notes, dur, ln, rest=False):
        """Place a note/chord/rest event at the voice's position."""
        if vs.grace is not None:                         # inside {...}: grace notes
            if not rest:
                vs.grace.append({"pos": None, "dur": Fr(0), "notes": notes, "rest": False, "grace": True,
                                 "art": [], "line": ln, "vel": None})
            return None
        cur = vs.cur
        f = vs.tup[1] if vs.tup else Fr(1)
        if vs.broken is not None:
            f *= vs.broken
            vs.broken = None
        dur = dur * f
        for nt in notes:
            if "dur" in nt:
                nt["dur"] = nt["dur"] * f
        for g in vs.pend_grace:
            g["pos"] = cur["pos"]
            cur["events"].append(g)
        vs.pend_grace = []
        ev = {"pos": cur["pos"], "dur": dur, "notes": notes, "rest": rest, "grace": False, "art": [], "line": ln,
              "vel": None}
        midx = len(vs.ms)
        for kind, val in vs.deco:
            if kind == "dyn":
                vs.v.dyn.append(((midx, cur["pos"]), val))
            elif kind == "sfz":
                ev["vel"] = val
            else:
                ev["art"].append(val)
        vs.deco = []
        cur["events"].append(ev)
        cur["content"] = True
        cur["pos"] += dur
        if vs.tup:
            vs.tup[2]["durs"].append(dur)
            vs.tup[0] -= 1
            if vs.tup[0] <= 0:
                self._tup_done(vs)
        vs.last = ev
        vs.tie_src = ev
        vs.tie_carry = {}
        return ev

    def _note(self, vs, m, ln):
        acc, letter, octs, a, sl, b = m.groups()
        nt = self._pitch(vs, acc, letter, octs)
        self._place(vs, [nt], self._length(vs, a, sl, b), ln)

    def _rest(self, vs, m, ln):
        dur = self._length(vs, m.group(2), m.group(3), m.group(4))
        if vs.grace is not None:
            return
        self._place(vs, [], dur, ln, rest=True)

    def _mrest(self, vs, m, ln):
        k = int(m.group(2)) if m.group(2) else 1
        mq = _meter_q(vs.cur["meter"]) or Fr(4)
        ev = self._place(vs, [], mq, ln, rest=True)
        if ev is not None:
            ev["mrest"] = True
        vs.cur["mm"] = k

    def _chord(self, vs, line, i, ln):
        j = line.find("]", i)
        if j < 0:
            self.issue("warning", "syntax", "unclosed chord '['", "close it with ]")
            return len(line)
        body = line[i + 1:j]
        notes, first = [], None
        k = 0
        while k < len(body):
            ch = body[k]
            if ch in "!+":
                e = body.find(ch, k + 1)
                if e < 0:
                    break
                self._deco(vs, body[k + 1:e])
                k = e + 1
                continue
            if ch in _SHORT_DECO and ch not in "uvHLMOPST":
                self._deco(vs, _SHORT_DECO[ch])
                k += 1
                continue
            if ch == "-":
                if notes:
                    notes[-1]["tie"] = True
                k += 1
                continue
            m = _NOTE_RE.match(body, k)
            if m:
                acc, letter, octs, a, sl, b = m.groups()
                nt = self._pitch(vs, acc, letter, octs)
                d = self._length(vs, a, sl, b)
                nt["dur"] = d
                if first is None:
                    first = d
                notes.append(nt)
                k = m.end()
                continue
            if ch in " \t":
                k += 1
                continue
            self.issue("warning", "syntax", f"unknown symbol '{ch}' in chord [{body}]")
            k += 1
        e = j + 1
        m = re.match(r"(\d*)(/*)(\d*)", line[e:])
        mult = self._length(vs, m.group(1), m.group(2), m.group(3)) / (vs.unit * 4)
        e += m.end()
        if not notes:
            self.issue("warning", "syntax", "empty chord []")
            return e
        for nt in notes:
            nt["dur"] = nt["dur"] * mult
        dur = first * mult
        ev = self._place(vs, notes, dur, ln)
        if ev is not None:
            for nt in notes:
                if nt["dur"] == dur:
                    del nt["dur"]
        return e

    def _volta(self, vs, s):
        nums = set()
        for part in s.split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                nums.update(range(int(a), int(b) + 1))
            elif part:
                nums.add(int(part))
        vs.volta = sorted(nums)
        if not vs.cur["content"]:
            vs.cur["volta"] = vs.volta
            vs.cur["volta_start"] = True

    def _bar(self, vs, line, m, ln):
        lead, pipes, trail = m.group(1), m.group(2), m.group(3)
        tok = m.group(0)
        end = m.end()
        attrs = set()
        if lead:
            attrs.add("rep_end")
        if trail:
            attrs.add("rep_start")
        if not pipes:                              # '::'
            attrs = {"rep_end", "rep_start"}
        if "||" in tok or "|]" in tok or "[|" in tok:
            attrs.add("double")
        if "|]" in tok and "[|]" not in tok:
            attrs.add("final")
        vm = _VOLTA_RE.match(line, end)
        volta = None
        if vm and (vm.group(0).startswith("[") or end == m.end()):
            volta = vm.group(1)
            end = vm.end()
        src = vs.tie_src
        cur = vs.cur
        same_line = vs.last_bar_line == ln
        if not cur["content"] and not cur["mm"]:
            plain = not attrs and vs.last_bar_attrs == set() and not cur["nav"]
            if vs.ms and same_line and plain:
                cur["empty"] = True                # '| |': an empty bar
                self._close(vs, attrs)
            else:
                if "rep_end" in attrs and vs.ms:
                    vs.minfo[-1]["end"].add("rep_end")
                    if vs.volta is not None:
                        vs.volta = None
                if "rep_start" in attrs:
                    cur["start"].add("rep_start")
                if "double" in attrs and vs.ms:
                    vs.minfo[-1]["end"].add("double")
                if "final" in attrs and vs.ms:
                    vs.minfo[-1]["end"].add("final")
        else:
            self._close(vs, attrs)
        vs.last_bar_line = ln
        vs.last_bar_attrs = attrs
        vs.tie_carry = {}
        if src is not None:
            vs.tie_carry = {(nt["step"], nt["octave"]): nt["alter"] for nt in src["notes"] if nt.get("tie")}
            vs.tie_src = None
        if volta:
            self._volta(vs, volta)
        return end

    def _close(self, vs, attrs, final=False):
        cur = vs.cur
        if final and not cur["content"] and not cur["mm"]:
            return
        if vs.tup and vs.tup[0] > 0:
            self._tup_done(vs, cut=True)
        if vs.grace is not None or vs.pend_grace:
            if vs.pend_grace:
                self.issue("warning", "grace", "grace notes {..} at the end of a bar with no note after them "
                           "(dropped)", bar=None, voice=vs.v.name)
            vs.pend_grace = []
            vs.grace = None
        cur["len"] = cur["pos"]
        cur["end"] = set(attrs) - {"rep_start"}
        info = {"start": set(cur["start"]), "end": cur["end"], "volta": cur["volta"], "nav": cur["nav"],
                "meter": cur["meter"], "meter_set": cur["meter_set"], "key": cur["key"], "line": cur["line"],
                "tuplets": cur["tuplets"], "coda0": cur["coda0"], "empty": cur.get("empty", False)}
        vs.ms.append(cur)
        vs.minfo.append(info)
        end_attrs = info["end"]
        for k in range(max(0, cur["mm"] - 1)):     # Z4: the same full-bar rest 4 times
            info["end"] = set()
            c2 = dict(cur)
            c2["events"] = [dict(e) for e in cur["events"]]
            vs.ms.append(c2)
            i2 = dict(info)
            i2["start"], i2["nav"], i2["tuplets"] = set(), [], []
            i2["end"] = set(end_attrs) if k == cur["mm"] - 2 else set()
            i2["meter_set"] = False
            vs.minfo.append(i2)
        vs.bar_acc = {}
        # a volta ends at a repeat end, a double/final bar or a repeat start
        if attrs & {"rep_end", "double", "final", "rep_start"}:
            vs.volta = None
        self._new_measure(vs)
        if "rep_start" in attrs:
            vs.cur["start"].add("rep_start")

    # ------------------------------------------------------------------ build the Score
    def _build(self):
        sc = self.score
        sc.parse_issues = self.issues
        vss = [self.vs[v] for v in self.vorder]
        for vs in vss:
            if vs.v.program is None and "*" in self.programs:
                vs.v.program = self.programs["*"]
            vs.v.clef = vs.clef
        nmax = max(len(vs.ms) for vs in vss)
        lens = {vs.v.name: len(vs.ms) for vs in vss}
        if len(set(lens.values())) > 1:
            self.issues.append(_issue("error", "bar_count", None, None, None,
                                      "voices have different numbers of bars: " +
                                      ", ".join(f"{k} {v}" for k, v in lens.items()),
                                      "a bar line is missing or doubled in the shorter/longer voice; "
                                      "diff the voices bar by bar"))
        gms = []
        meter = vss[0].minfo[0]["meter"] if vss[0].minfo else self.defaults["meter"]
        for i in range(nmax):
            infos = [(vs, vs.minfo[i]) for vs in vss if i < len(vs.minfo)]
            gm = {"index": i, "nav": [], "rep_start": False, "rep_end": False, "rep_times": 2, "volta": None,
                  "double": False, "final": False, "implicit": False, "tempos": [], "coda0": False,
                  "line": infos[0][1]["line"] if infos else 0}
            set_m = [inf["meter"] for _, inf in infos if inf["meter_set"]]
            if set_m:
                if len(set(set_m)) > 1:
                    self.issues.append(_issue("warning", "meter", None, None, gm["line"],
                                              "voices change to different meters in the same bar",
                                              "use one [M:] for every voice"))
                meter = set_m[0]
            gm["meter"] = meter
            first = infos[0][1]
            gm["rep_start"] = "rep_start" in first["start"]
            gm["rep_end"] = "rep_end" in first["end"]
            gm["double"] = bool(first["end"] & {"double", "final"})
            gm["final"] = "final" in first["end"]
            gm["volta"] = first["volta"]
            gm["key"] = first["key"]
            for vs, inf in infos[1:]:
                if ("rep_start" in inf["start"]) != gm["rep_start"] or ("rep_end" in inf["end"]) != gm["rep_end"] \
                        or inf["volta"] != gm["volta"]:
                    self.issues.append(_issue("warning", "repeat", None, vs.v.name, inf["line"],
                                              f"repeat/ending signs differ from voice {vss[0].v.name}",
                                              "write the same |: :| [1 [2 in every voice"))
                    break
            for _, inf in infos:
                for mk in inf["nav"]:
                    if mk not in gm["nav"]:
                        gm["nav"].append(mk)
                gm["coda0"] = gm["coda0"] or inf["coda0"]
            gms.append(gm)
        for vs in vss:
            v = vs.v
            v.measures = []
            for i in range(nmax):
                if i < len(vs.ms):
                    cm = vs.ms[i]
                    vm = {"events": cm["events"], "len": cm["len"], "present": True, "line": cm["line"],
                          "empty": vs.minfo[i]["empty"] or (not cm["events"]), "tuplets": vs.minfo[i]["tuplets"],
                          "key": vs.minfo[i]["key"], "mrest": bool(cm["mm"])}
                else:
                    vm = {"events": [], "len": Fr(0), "present": False, "line": 0, "empty": True, "tuplets": [],
                          "key": vs.key, "mrest": False}
                v.measures.append(vm)
        for _, midx, pos, bpm in self.tempos:
            if midx < len(gms):
                if not any(t[0] == pos for t in gms[midx]["tempos"]):
                    gms[midx]["tempos"].append((pos, bpm))
        sc.voices = [vs.v for vs in vss]
        _staffing_abc(sc, self.score_dir)
        sc.keys_by_voice = True
        _finish(sc, gms)
        return sc


def _staffing_abc(sc, sdir):
    """Staff/part numbers from %%score / %%staves: (a b) share a staff, {a b} is one (piano) part."""
    byid = {str(v.id): v for v in sc.voices}
    staff = 0
    if sdir:
        depth_brace, in_paren, part = 0, False, 0
        toks = re.findall(r"[(){}\[\]|]|[^\s(){}\[\]|]+", sdir)
        brace_staffs = []
        for t in toks:
            if t == "{":
                depth_brace += 1
                part += 1
                brace_staffs = []
            elif t == "}":
                depth_brace -= 1
                if len(brace_staffs) == 2:
                    for v in sc.voices:
                        if v.staff in brace_staffs:
                            v.piano = True
            elif t == "(":
                in_paren = True
                staff += 1
                if depth_brace:
                    brace_staffs.append(staff)
            elif t == ")":
                in_paren = False
            elif t in "[]|":
                continue
            elif t in byid:
                v = byid[t]
                if not in_paren:
                    staff += 1
                    if depth_brace:
                        brace_staffs.append(staff)
                v.staff = staff
                if not depth_brace:
                    part += 1
                v.part = part
    for v in sc.voices:
        if v.staff is None:
            staff += 1
            v.staff = staff
            v.part = staff
    for v in sc.voices:
        if re.search(r"piano|pno|keys|keyboard|\brh\b|\blh\b|right|left|organ|harpsi|cembalo|celest", v.name, re.I) \
                or (v.program is not None and (0 <= v.program <= 7 or 16 <= v.program <= 23)):
            v.piano = True
    if not any(v.piano for v in sc.voices) and all(v.program is None for v in sc.voices):
        staffs = sorted({v.staff for v in sc.voices})
        clefs = {v.staff: v.clef for v in sc.voices}
        if len(staffs) == 2 and clefs[staffs[0]] == "treble" and clefs[staffs[1]] == "bass":
            for v in sc.voices:
                v.piano = True


def parse_abc(text, ppq=PPQ, tune=None):
    """ABC 2.1 text -> Score (first tune, or the one with X:tune)."""
    if not isinstance(text, str):
        raise ValueError("parse_abc wants ABC text")
    try:
        return _AbcParser(text, ppq, tune).run()
    except (ZeroDivisionError, IndexError, KeyError, TypeError, AttributeError, OverflowError) as e:
        raise ValueError(f"ABC not understood ({type(e).__name__}: {e})") from None


# ============================================================================= MusicXML
class _X:
    __slots__ = ("tag", "a", "kids", "text", "ln")

    def __init__(self, tag, a, ln):
        self.tag, self.a, self.kids, self.text, self.ln = tag, a, [], "", ln

    def get(self, k, d=None):
        return self.a.get(k, d)

    def find(self, path):
        e = self
        for t in path.split("/"):
            e = next((k for k in e.kids if k.tag == t), None)
            if e is None:
                return None
        return e

    def findall(self, tag):
        return [k for k in self.kids if k.tag == tag]

    def ft(self, path, d=None):
        e = self.find(path)
        return e.text.strip() if e is not None and e.text.strip() else d

    def iter(self, tag):
        for k in self.kids:
            if k.tag == tag:
                yield k
            yield from k.iter(tag)


def _xml_tree(data):
    if b"<!ENTITY" in data[:20000]:
        raise ValueError("MusicXML with entity declarations is refused")
    p = expat.ParserCreate()
    p.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack, root = [], []

    def start(tag, attrs):
        e = _X(tag.split("}")[-1], attrs, p.CurrentLineNumber)
        if stack:
            stack[-1].kids.append(e)
        else:
            root.append(e)
        stack.append(e)

    def end(tag):
        stack.pop()

    def chars(t):
        if stack:
            stack[-1].text += t

    def refuse(*a):
        raise ValueError("MusicXML with entity declarations is refused")

    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, chars
    p.EntityDeclHandler = refuse
    try:
        p.Parse(data, True)
    except expat.ExpatError as e:
        raise ValueError(f"MusicXML is not well-formed XML: {e}") from None
    if not root:
        raise ValueError("empty MusicXML")
    return root[0]


MAX_XML = 64 << 20


def _mxl(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("not a valid .mxl (zip) file") from None
    names = z.namelist()
    target = None
    if "META-INF/container.xml" in names:
        c = _xml_tree(z.read("META-INF/container.xml"))
        rf = next(c.iter("rootfile"), None)
        if rf is not None and rf.get("full-path") in names:
            target = rf.get("full-path")
    if target is None:
        target = next((n for n in names if not n.startswith("META-INF") and n.lower().endswith((".xml", ".musicxml"))),
                      None)
    if target is None:
        raise ValueError(".mxl has no MusicXML score inside")
    if z.getinfo(target).file_size > MAX_XML:
        raise ValueError(".mxl score is too large")
    return z.read(target)


def _xml_bytes(src):
    if isinstance(src, (bytes, bytearray)):
        data = bytes(src)
    elif isinstance(src, str) and src.lstrip().startswith("<"):
        data = src.encode("utf-8")
        data = re.sub(rb"^\s*<\?xml[^>]*\?>", b'<?xml version="1.0" encoding="UTF-8"?>', data)
    else:
        if not os.path.isfile(src):
            raise ValueError(f"MusicXML file not found: {src}")
        if os.path.getsize(src) > MAX_XML:
            raise ValueError("MusicXML file is too large")
        with open(src, "rb") as f:
            data = f.read()
    if data[:2] == b"PK":
        data = _mxl(data)
    return data


_TYPE_Q = {"maxima": Fr(32), "long": Fr(16), "breve": Fr(8), "whole": Fr(4), "half": Fr(2), "quarter": Fr(1),
           "eighth": Fr(1, 2), "16th": Fr(1, 4), "32nd": Fr(1, 8), "64th": Fr(1, 16), "128th": Fr(1, 32)}
_CLEF_SIGN = {("G", 2): "treble", ("F", 4): "bass", ("C", 3): "alto", ("C", 4): "tenor", ("F", 3): "baritone"}


def parse_musicxml(src, ppq=PPQ):
    """MusicXML (partwise; .musicxml/.xml text or file, or a compressed .mxl) -> Score."""
    try:
        return _parse_musicxml(src, ppq)
    except (ZeroDivisionError, IndexError, KeyError, TypeError, AttributeError, OverflowError) as e:
        raise ValueError(f"MusicXML not understood ({type(e).__name__}: {e})") from None


def _parse_musicxml(src, ppq):
    root = _xml_tree(_xml_bytes(src))
    if root.tag == "score-timewise":
        raise ValueError("score-timewise MusicXML is not supported: export partwise (the default in MuseScore etc.)")
    if root.tag != "score-partwise":
        raise ValueError(f"not a MusicXML score (root <{root.tag}>)")
    sc = Score(ppq, "musicxml")
    issues = sc.parse_issues
    sc.title = root.ft("work/work-title") or root.ft("movement-title") or ""
    ident = root.find("identification")
    if ident is not None:
        for c in ident.findall("creator"):
            if c.get("type") == "composer" and c.text.strip():
                sc.composer = c.text.strip()
    pinfo = {}
    pl = root.find("part-list")
    for sp in (pl.findall("score-part") if pl is not None else []):
        prog = sp.ft("midi-instrument/midi-program")
        pinfo[sp.get("id")] = {"name": sp.ft("part-name") or sp.get("id"),
                               "program": int(prog) - 1 if prog and prog.isdigit() else None,
                               "inst": sp.ft("score-instrument/instrument-name") or ""}
    gms = []
    voices = []
    for pidx, part in enumerate(root.findall("part")):
        pid = part.get("id")
        info = pinfo.get(pid, {"name": pid, "program": None, "inst": ""})
        sc.part_names[pid] = info["name"]
        div = 1
        staves = 1
        clefs = {}
        keys = {}
        transpose = 0
        diatonic = 0
        pvoices = {}
        dyn = []
        cur_volta = None
        measures = part.findall("measure")
        piano_like = bool(re.search(r"piano|pno|keyboard|organ|harpsi|celest", info["name"] + " " + info["inst"],
                                    re.I)) or (info["program"] is not None and (0 <= info["program"] <= 7 or
                                                                                16 <= info["program"] <= 23))

        def get_voice(vnum, staff):
            if vnum not in pvoices:
                v = Voice(f"{pid}:{vnum}")
                v.part = pid
                v.program = info["program"]
                v.vnum = vnum
                v.staff_counts = {}
                v.measures = [{"events": [], "len": Fr(0), "present": False, "line": 0, "empty": True,
                               "tuplets": [], "key": C_MAJOR, "mrest": False} for _ in range(len(measures))]
                pvoices[vnum] = v
            return pvoices[vnum]

        for midx, meas in enumerate(measures):
            while len(gms) <= midx:
                gms.append({"index": len(gms), "nav": [], "rep_start": False, "rep_end": False, "rep_times": 2,
                            "volta": None, "double": False, "final": False, "implicit": False, "tempos": [],
                            "coda0": False, "line": meas.ln, "meter": None, "num": None, "_meter_set": False})
            gm = gms[midx]
            if pidx == 0:
                gm["num"] = meas.get("number")
                gm["implicit"] = meas.get("implicit") == "yes"
                if midx > 0 and not gm["_meter_set"]:
                    gm["meter"] = gms[midx - 1]["meter"]
            pos = Fr(0)
            prev_start = Fr(0)
            last_ev = {}
            tup_open = {}
            for el in meas.kids:
                t = el.tag
                if t == "attributes":
                    d = el.ft("divisions")
                    if d:
                        div = int(d)
                        if div <= 0:
                            raise ValueError(f"MusicXML divisions {div} (line {el.ln}) must be positive")
                        sc.divisions = sc.divisions or div
                    st = el.ft("staves")
                    if st:
                        staves = int(st)
                    for k in el.findall("key"):
                        fifths = int(k.ft("fifths") or 0)
                        mode = {"minor": -3, "dorian": -2, "mixolydian": -1, "phrygian": -4, "locrian": -5,
                                "lydian": 1, "aeolian": -3}.get((k.ft("mode") or "major").lower(), 0)
                        kk = _key(fifths, mode)
                        num = k.get("number")
                        if num:
                            keys[int(num)] = kk
                        else:
                            keys = {0: kk}
                        if pidx == 0 and (not sc.keys or sc.keys[-1][2] != kk["name"]):
                            sc.keys.append((midx, pos, kk["name"]))
                    for tm in el.findall("time"):
                        if tm.find("senza-misura") is not None:
                            m = None
                        else:
                            beats = tm.ft("beats") or "4"
                            m = (sum(int(x) for x in re.findall(r"\d+", beats)), int(tm.ft("beat-type") or 4))
                            if m[0] <= 0 or m[1] <= 0:                 # 0/4 or 4/0: no bar length (crashed later)
                                raise ValueError(f"MusicXML time {m[0]}/{m[1]} (line {tm.ln}) isn't a time signature")
                        if pidx == 0 or not gm["_meter_set"]:
                            gm["meter"], gm["_meter_set"] = m, True
                    for c in el.findall("clef"):
                        sign, line = c.ft("sign") or "G", int(c.ft("line") or {"G": 2, "F": 4, "C": 3}.get(
                            c.ft("sign") or "G", 2))
                        clefs[int(c.get("number") or 1)] = _CLEF_SIGN.get((sign, line), "treble")
                    tr = el.find("transpose")
                    if tr is not None:
                        transpose = int(tr.ft("chromatic") or 0) + 12 * int(tr.ft("octave-change") or 0)
                        diatonic = int(tr.ft("diatonic") or 0)
                elif t == "note":
                    grace = el.find("grace") is not None
                    chord = el.find("chord") is not None
                    cue = el.find("cue") is not None
                    rest = el.find("rest") is not None
                    vnum = el.ft("voice") or "1"
                    staff = int(el.ft("staff") or 1)
                    d = Fr(int(el.ft("duration") or 0), div) if not grace else Fr(0)
                    if d < 0:                                     # a note ending before it starts (hung to_midi)
                        raise ValueError(f"MusicXML note duration {el.ft('duration')} (line {el.ln}) is negative")
                    npos = prev_start if chord else pos
                    v = get_voice(vnum, staff)
                    v.staff_counts[staff] = v.staff_counts.get(staff, 0) + 1
                    vm = v.measures[midx]
                    if not vm["present"]:
                        vm["present"] = True
                        vm["line"] = el.ln
                        vm["key"] = keys.get(staff, keys.get(0, C_MAJOR))
                    art = []
                    nots = el.find("notations")
                    ties = [x.get("type") for x in el.findall("tie")]
                    tm = el.find("time-modification")
                    if nots is not None:
                        ties += [x.get("type") for x in nots.findall("tied")]
                        arts = nots.find("articulations")
                        if arts is not None:
                            for a in arts.kids:
                                art.append({"strong-accent": "marcato", "spiccato": "staccato",
                                            "detached-legato": "tenuto"}.get(a.tag, a.tag))
                        for dn in nots.findall("dynamics"):
                            for dk in dn.kids:
                                if dk.tag in SFZ or dk.tag in DYN:
                                    art.append(("vel", 112 if dk.tag in SFZ else DYN[dk.tag]))
                        if not chord and not grace:
                            for tp in nots.findall("tuplet"):
                                if tp.get("type") == "start":
                                    tup_open[vnum] = {"durs": [], "line": el.ln, "p": None, "q": None,
                                                      "midx": midx}
                    nt = None
                    if not rest and not cue:
                        ptag = el.find("pitch")
                        if ptag is None:
                            ptag = el.find("unpitched")
                            step_s = ptag.ft("display-step") if ptag is not None else None
                            oct_s = ptag.ft("display-octave") if ptag is not None else None
                            alter = 0
                        else:
                            step_s, oct_s = ptag.ft("step"), ptag.ft("octave")
                            alter = int(round(float(ptag.ft("alter") or 0)))
                        if step_s and oct_s:
                            step = STEPS.index(step_s.upper())
                            octave = int(oct_s)
                            wp = (octave + 1) * 12 + STEP_SEMI[step] + alter
                            kk = keys.get(staff, keys.get(0, C_MAJOR))
                            nt = {"pitch": wp + transpose, "wpitch": wp, "step": step, "alter": alter,
                                  "octave": octave, "kalter": kk["alters"][STEPS[step]],
                                  "explicit": el.find("accidental") is not None, "tie": "start" in ties,
                                  "clef": clefs.get(staff, "treble"), "staff": staff, "line": el.ln}
                            ss = (step + diatonic) % 7
                            so = octave + (step + diatonic) // 7 + (transpose - (transpose % 12 if transpose >= 0
                                                                                 else -((-transpose) % 12))) // 12
                            nt["sstep"] = ss
                            sa = nt["pitch"] - ((so + 1) * 12 + STEP_SEMI[ss])
                            sa = ((sa + 6) % 12) - 6
                            nt["salter"] = sa if -2 <= sa <= 2 else None
                    if chord and vnum in last_ev and last_ev[vnum]["pos"] == npos and nt is not None:
                        ev = last_ev[vnum]
                        if d != ev["dur"] and not grace:
                            nt["dur"] = d
                        ev["notes"].append(nt)
                    else:
                        nvel = next((a[1] for a in art if isinstance(a, tuple)), None)
                        art = [a for a in art if not isinstance(a, tuple)]
                        ev = {"pos": npos, "dur": d, "notes": [nt] if nt else [], "rest": nt is None, "grace": grace,
                              "art": art, "line": el.ln, "vel": nvel, "staff": staff, "cue": cue}
                        if el.find("rest") is not None and el.find("rest").get("measure") == "yes":
                            ev["mrest"] = True
                        vm["events"].append(ev)
                        last_ev[vnum] = ev
                        if tm is not None and not grace and vnum in tup_open:
                            to = tup_open[vnum]
                            to["p"], to["q"] = int(tm.ft("actual-notes") or 3), int(tm.ft("normal-notes") or 2)
                            to["durs"].append(d)
                    if nots is not None and not chord and not grace:
                        for tp in nots.findall("tuplet"):
                            if tp.get("type") == "stop" and vnum in tup_open:
                                vm["tuplets"].append(tup_open.pop(vnum))
                    if not chord:
                        prev_start = npos
                        if not grace:
                            pos += d
                    vm["len"] = max(vm["len"], npos + d)
                elif t == "backup":
                    pos -= Fr(int(el.ft("duration") or 0), div)
                    if pos < 0:
                        issues.append(_issue("warning", "xml", gm["num"], info["name"], el.ln,
                                             "<backup> goes before the bar start"))
                        pos = Fr(0)
                elif t == "forward":
                    d = Fr(int(el.ft("duration") or 0), div)
                    if d < 0:
                        raise ValueError(f"MusicXML forward duration {el.ft('duration')} (line {el.ln}) is negative")
                    vnum = el.ft("voice")
                    if vnum:
                        v = get_voice(vnum, int(el.ft("staff") or 1))
                        vm = v.measures[midx]
                        vm["present"] = True
                        vm["len"] = max(vm["len"], pos + d)
                    pos += d
                elif t == "direction":
                    _xml_direction(el, gm, pos, dyn, midx, pidx)
                elif t == "sound":
                    _xml_sound(el, gm, pos, pidx)
                elif t == "barline":
                    loc = el.get("location") or "right"
                    rep = el.find("repeat")
                    style = el.ft("bar-style") or ""
                    end = el.find("ending")
                    if rep is not None:
                        if rep.get("direction") == "forward":
                            gm["rep_start"] = True
                        else:
                            gm["rep_end"] = True
                            if rep.get("times"):
                                gm["rep_times"] = int(rep.get("times"))
                    if style in ("light-light", "light-heavy", "heavy-light", "heavy-heavy"):
                        gm["double"] = True
                        if style == "light-heavy" and loc == "right":
                            gm["final"] = True
                    if end is not None:
                        nums = sorted({int(x) for x in re.findall(r"\d+", end.get("number") or "")})
                        if end.get("type") == "start":
                            cur_volta = nums or [1]
                            gm["volta"] = cur_volta
                            gm["volta_start"] = True
                        elif end.get("type") in ("stop", "discontinue"):
                            gm["volta"] = gm["volta"] or cur_volta or nums
                            cur_volta = None
                            gm["_volta_closed"] = True
            if pidx == 0:
                gm["key"] = keys.get(1, keys.get(0, C_MAJOR))
            if cur_volta and not gm.get("_volta_closed") and gm["volta"] is None:
                gm["volta"] = cur_volta
        for vnum, v in sorted(pvoices.items(), key=lambda kv: kv[0]):
            sidx = max(v.staff_counts, key=v.staff_counts.get) if v.staff_counts else 1
            v.staff = f"{pid}.{sidx}" if staves > 1 else pid
            v.clef = clefs.get(sidx, "treble")
            v.piano = piano_like or staves == 2
            v.dyn = list(dyn)
            v.sidx = sidx
            many = len(pvoices) > 1
            if many and staves == 2 and v.piano:
                same = [w for w in pvoices.values() if (max(w.staff_counts, key=w.staff_counts.get)
                                                       if w.staff_counts else 1) == sidx]
                hand = "RH" if sidx == 1 else "LH"
                v.name = f"{info['name']} {hand}" + (f" v{vnum}" if len(same) > 1 else "")
            else:
                v.name = info["name"] + (f" v{vnum}" if many else "")
            voices.append(v)
        # primary voice per staff: the first voice number on it
        seen = set()
        for v in sorted(pvoices.values(), key=lambda w: (str(w.staff), int(w.vnum) if w.vnum.isdigit() else 99)):
            v.primary = v.staff not in seen
            seen.add(v.staff)
    if not voices:
        raise ValueError("MusicXML score has no notes")
    names = {}
    for v in voices:
        if v.name in names:
            names[v.name] += 1
            v.name = f"{_vn(v.name)} ({names[v.name]})"
        else:
            names[v.name] = 1
    for gm in gms:
        gm.pop("_meter_set", None)
        gm.pop("_volta_closed", None)
    sc.voices = voices
    key_marks = sc.keys
    sc.keys = []
    _finish(sc, gms, xml_keys=key_marks)
    return sc


def _xml_direction(el, gm, pos, dyn, midx, pidx):
    snd = el.find("sound")
    marked = False
    for dt in el.findall("direction-type"):
        for k in dt.kids:
            if k.tag == "dynamics":
                for dk in k.kids:
                    if dk.tag in DYN:
                        dyn.append(((midx, pos), DYN[dk.tag]))
                        marked = True
            elif k.tag == "words" and pidx == 0:
                for mk in _nav_from_text(k.text or ""):
                    if mk not in gm["nav"]:
                        gm["nav"].append(mk)
                    if mk == "coda" and pos == 0:
                        gm["coda0"] = True
            elif k.tag == "segno" and pidx == 0:
                if "segno" not in gm["nav"]:
                    gm["nav"].append("segno")
            elif k.tag == "coda" and pidx == 0:
                gm["nav"].append("coda_sym")
                if pos == 0:
                    gm["coda0"] = True
            elif k.tag == "metronome" and (snd is None or not snd.get("tempo")):
                unit = _TYPE_Q.get(k.ft("beat-unit") or "quarter", Fr(1))
                if k.find("beat-unit-dot") is not None:
                    unit *= Fr(3, 2)
                pm = k.ft("per-minute")
                try:
                    bpm = float(pm) * float(unit)
                    if pidx == 0 and not any(t[0] == pos for t in gm["tempos"]):
                        gm["tempos"].append((pos, bpm))
                except (TypeError, ValueError):
                    pass
    if snd is not None:
        if snd.get("dynamics") and not marked:
            try:
                dyn.append(((midx, pos), max(1, min(127, int(round(float(snd.get("dynamics")) * 0.9))))))
            except ValueError:
                pass
        _xml_sound(snd, gm, pos, pidx)


def _xml_sound(snd, gm, pos, pidx):
    if pidx != 0:
        return
    tempo = snd.get("tempo")
    if tempo:
        try:
            bpm = float(tempo)
            gm["tempos"] = [t for t in gm["tempos"] if t[0] != pos] + [(pos, bpm)]
        except ValueError:
            pass
    for attr, mk in (("segno", "segno"), ("coda", "coda_dest"), ("tocoda", "tocoda"), ("dacapo", "dc"),
                     ("dalsegno", "ds"), ("fine", "fine")):
        val = snd.get(attr)
        if val and not (attr == "dacapo" and val != "yes"):
            if mk not in gm["nav"]:
                gm["nav"].append(mk)
            if mk == "coda_dest":
                gm["coda0"] = True


# ============================================================================= shared: measures, sounding, playback
def _finish(sc, gms, xml_keys=None):
    """Measure lengths/numbers, written timeline, sounding notes and the expanded playback."""
    voices = sc.voices
    n = len(gms)
    # implicit (short-allowed) bars and lengths
    for i, gm in enumerate(gms):
        mq = _meter_q(gm["meter"])
        lens = [v.measures[i]["len"] for v in voices if v.measures[i]["present"] and v.primary]
        lens = lens or [v.measures[i]["len"] for v in voices if v.measures[i]["present"]] or [Fr(0)]
        mx = max(lens)
        gm["content_len"] = mx
        if mq is None:
            gm["len"] = mx
            gm["implicit"] = True
            continue
        nxt = gms[i + 1] if i + 1 < n else None
        boundary = (i == 0 or i == n - 1 or gm["rep_end"] or gm["double"] or gm["rep_start"]
                    or gm.get("volta_start") or (nxt is not None and (nxt["rep_start"] or nxt.get("volta_start")))
                    or gm.get("implicit"))
        if mx < mq and boundary and mx > 0:
            gm["implicit"] = True
            gm["len"] = mx
        else:
            gm["implicit"] = bool(gm.get("implicit")) and mx < mq
            gm["len"] = mx if gm["implicit"] else max(mq, mx)
    # printed numbers
    if sc.source == "abc":
        num = 0 if gms and gms[0]["implicit"] and n > 1 else 1
        for gm in gms:
            gm["num"] = num
            num += 1
    else:
        for i, gm in enumerate(gms):
            raw = gm.get("num")
            gm["num"] = int(raw) if raw and str(raw).lstrip("-").isdigit() else (raw or i + 1)
    q = Fr(0)
    for gm in gms:
        gm["start_q"] = q
        gm["start_tick"] = sc.tick(q)
        gm["len_ticks"] = sc.tick(q + gm["len"]) - gm["start_tick"]
        q += gm["len"]
    sc.measures = gms
    # meta (written timeline)
    sc.meters = []
    for gm in gms:
        m = gm["meter"]
        if m and (not sc.meters or (sc.meters[-1][1], sc.meters[-1][2]) != tuple(m)):
            sc.meters.append((gm["start_tick"], m[0], m[1]))
    if xml_keys is not None:
        for midx, pos, name in xml_keys:
            sc.keys.append((sc.tick(gms[midx]["start_q"] + pos), name))
    elif voices:
        v0 = voices[0]
        prev = None
        for i, vm in enumerate(v0.measures):
            k = vm["key"]["name"]
            if k != prev:
                sc.keys.append((gms[i]["start_tick"], k))
                prev = k
    sc.tempos = []
    for gm in gms:
        for pos, bpm in sorted(gm["tempos"]):
            sc.tempos.append((sc.tick(gm["start_q"] + pos), round(bpm, 3)))
    # dynamics: a voice without any follows the first voice that has some
    src = next((v.dyn for v in voices if v.dyn), [])
    for v in voices:
        v.dyn_eff = sorted(v.dyn or src, key=lambda x: x[0])
    # sounding notes per voice per measure
    for v in voices:
        v.sound = [_sound(v, i, vm) for i, vm in enumerate(v.measures)]
    _expand(sc)


def _vel_at(v, midx, pos):
    keys = [k for k, _ in v.dyn_eff]
    j = bisect.bisect_right(keys, (midx, pos)) - 1
    return v.dyn_eff[j][1] if j >= 0 else DEFAULT_VEL


def _sound(v, midx, vm):
    out = []
    pend = []
    for ev in vm["events"]:
        if ev["grace"]:
            pend.append(ev)
            continue
        if ev.get("cue"):
            pend = []
            continue
        base = _vel_at(v, midx, ev["pos"])
        if ev.get("vel"):
            base = max(base, ev["vel"])
        if "accent" in ev["art"]:
            base += 20
        if "marcato" in ev["art"]:
            base += 28
        vel = max(1, min(127, base))
        shift = Fr(0)
        if pend:
            g = len(pend)
            d = min(Fr(1, 8), ev["dur"] / (2 * g)) if ev["dur"] > 0 else Fr(1, 8)
            for k, gev in enumerate(pend):
                for nt in gev["notes"]:
                    out.append({"pos": ev["pos"] + k * d, "dur": d, "pitch": nt["pitch"], "vel": vel, "grace": True,
                                "tie": False, "line": gev["line"], "staff": v.staff, "n": nt})
            shift = g * d
            pend = []
        if ev["rest"]:
            continue
        fac = Fr(1)
        if "staccato" in ev["art"]:
            fac = Fr(1, 2)
        if "staccatissimo" in ev["art"]:
            fac = Fr(1, 4)
        for nt in ev["notes"]:
            dur = nt.get("dur", ev["dur"])
            sd = dur * fac if not nt.get("tie") else dur
            sd = max(sd - shift, Fr(1, 64))
            out.append({"pos": ev["pos"] + shift, "dur": sd, "pitch": nt["pitch"], "vel": vel, "grace": False,
                        "tie": nt.get("tie", False), "line": ev["line"], "staff": v.staff, "n": nt,
                        "wdur": dur})
    return out


def _expand(sc):
    """Playback order through repeats, endings and D.C./D.S.; notes, tempo/meter/key maps on the playback timeline."""
    gms = sc.measures
    n = len(gms)
    order = []
    issues = sc.parse_issues
    segno = next((i for i, g in enumerate(gms) if "segno" in g["nav"]), None)
    dest = next((i for i, g in enumerate(gms) if "coda_dest" in g["nav"]), None)
    tocoda = [i for i, g in enumerate(gms) if "tocoda" in g["nav"]]
    codas = [i for i, g in enumerate(gms) if "coda" in g["nav"] or "coda_sym" in g["nav"]]
    if dest is None:
        if tocoda:
            dest = next((i for i in codas if i > tocoda[0]), None)
        elif len(codas) >= 2:
            tocoda, dest = [codas[0]], codas[1]
        elif len(codas) == 1 and gms[codas[0]]["coda0"]:
            dest = codas[0]
    elif not tocoda:
        tocoda = [i for i in codas if i < dest][:1]
    sc.nav = {"segno": segno, "coda": dest, "tocoda": tocoda[0] if tocoda else None}
    # volta groups: the largest ending number of the group each volta bar belongs to
    gmax = {}
    i = 0
    while i < n:
        if gms[i]["volta"]:
            j = i
            while j < n and gms[j]["volta"]:
                j += 1
            mx = max(max(gms[k]["volta"]) for k in range(i, j))
            for k in range(i, j):
                gmax[k] = (i, j, mx)
            i = j
        else:
            i += 1
    i, sec, pas, back, jumped, jump_used = 0, 0, 1, False, False, False
    prev_volta = False
    steps = 0
    while i < n and steps < 20 * n + 100:
        steps += 1
        g = gms[i]
        if g["rep_start"] and not back and not jumped:
            sec, pas = i, 1
        if not g["volta"] and prev_volta and not back:
            if not jumped:
                pas = 1
                sec = i
        back = False
        if g["volta"]:
            if jumped:
                if gmax[i][2] not in g["volta"]:
                    i += 1
                    continue
            elif pas not in g["volta"]:
                i += 1
                prev_volta = True
                continue
        prev_volta = bool(g["volta"])
        order.append(i)
        if jumped and "fine" in g["nav"]:
            break
        if jumped and i in tocoda and dest is not None and dest > i:
            i = dest
            continue
        if g["rep_end"] and not jumped:
            if g["volta"]:
                again = pas < gmax[i][2]
            else:
                again = pas < g["rep_times"]
            if again:
                pas += 1
                i, back = sec, True
                continue
            pas, sec = 1, i + 1
        if ("dc" in g["nav"] or "ds" in g["nav"]) and not jump_used:
            jump_used, jumped = True, True
            if "ds" in g["nav"]:
                if segno is None:
                    issues.append(_issue("error", "nav", g["num"], None, g["line"], "D.S. but no segno in the score",
                                         "mark the segno bar with !segno! (ABC) / a segno sign"))
                    i += 1
                    continue
                i = segno
            else:
                i = 0
            continue
        i += 1
    sc.order = order
    # playback notes
    raw = []
    tmap, mmap, kmap = [], [], []
    q = Fr(0)
    v0 = sc.voices[0] if sc.voices else None
    for k, i in enumerate(order):
        g = gms[i]
        for pos, bpm in sorted(g["tempos"]):
            tmap.append((sc.tick(q + pos), round(bpm, 3)))
        if g["meter"] and (not mmap or mmap[-1][1:] != g["meter"]):
            mmap.append((sc.tick(q), g["meter"][0], g["meter"][1]))
        if v0 is not None:
            kk = g.get("key") or v0.measures[i]["key"]
            if not kmap or kmap[-1][1] != kk["fifths"] or kmap[-1][2] != kk["mode"]:
                kmap.append((sc.tick(q), kk["fifths"], kk["mode"]))
        for vi, v in enumerate(sc.voices):
            for sn in v.sound[i]:
                s = sc.tick(q + sn["pos"])
                e = sc.tick(q + sn["pos"] + sn["dur"])
                raw.append({"start_tick": s, "len_ticks": max(1, e - s), "pitch": sn["pitch"], "vel": sn["vel"],
                            "voice": v.name, "staff": v.staff, "tied": False, "grace": sn["grace"],
                            "measure": g["num"], "source_line": sn["line"], "_tie": sn["tie"], "_vi": vi,
                            "_midx": i, "_n": sn["n"], "_wdur_end": sc.tick(q + sn["pos"] + sn.get("wdur", sn["dur"]))})
        q += g["len"]
    sc.total_ticks = sc.tick(q)
    # tie merging per voice
    out = []
    sc.unresolved_ties = []
    byv = {}
    for nt in raw:
        byv.setdefault(nt["_vi"], []).append(nt)
    for vi, lst in byv.items():
        lst.sort(key=lambda x: (x["start_tick"], x["pitch"]))
        open_ = {}
        starts = {}
        for nt in lst:
            starts.setdefault(nt["start_tick"], []).append(nt)
        for nt in lst:
            if nt.get("_merged"):
                continue
            p = nt["pitch"]
            o = open_.get(p)
            if o is not None and o["_wdur_end"] == nt["start_tick"] and not nt["grace"]:
                o["len_ticks"] = nt["start_tick"] + nt["len_ticks"] - o["start_tick"]
                o["_wdur_end"] = nt["_wdur_end"]
                o["tied"] = True
                nt["_merged"] = True
                if not nt["_tie"]:
                    del open_[p]
                continue
            if o is not None:
                sc.unresolved_ties.append((o, starts.get(o["_wdur_end"], [])))
                del open_[p]
            out.append(nt)
            if nt["_tie"] and not nt["grace"]:
                open_[p] = nt
        for p, o in open_.items():
            sc.unresolved_ties.append((o, starts.get(o["_wdur_end"], [])))
    # unresolved ties found later in the sweep (a different-pitch target) are checked from the leftovers
    for o, _ in sc.unresolved_ties:
        o["len_ticks"] = max(o["len_ticks"], 1)
    out.sort(key=lambda x: (x["start_tick"], x["_vi"], x["pitch"]))
    sc._raw_notes = out
    sc.notes = [{k: v for k, v in nt.items() if not k.startswith("_")} for nt in out]
    if not tmap or tmap[0][0] != 0:
        sc.tempo_assumed = True                     # no tempo at the start: 120 assumed until the first mark
        tmap.insert(0, (0, 120.0))
    sc.tempo_map = _dedupe(tmap)
    sc.meter_map = mmap
    sc.key_map = kmap


def _dedupe(m):
    out = []
    for t, v in m:
        if out and out[-1][0] == t:
            out[-1] = (t, v)
        elif not out or out[-1][1] != v:
            out.append((t, v))
    return out


# ============================================================================= validate
def validate(score, strict=False, max_issues=40):
    """Reading mistakes and notation problems, ranked worst first (see module docstring)."""
    sc = score
    iss = [dict(x) for x in sc.parse_issues]
    gms = sc.measures
    num = lambda i: gms[i]["num"] if i is not None and 0 <= i < len(gms) else None  # noqa: E731
    for x in iss:
        if x["bar"] is None and x.get("line"):
            x["bar"] = _bar_of_line(sc, x["line"])
    add = lambda sev, kind, i, v, line, msg, fix="": iss.append(  # noqa: E731
        _issue(sev, kind, num(i), v, line, msg, fix))
    _v_structure(sc, add)
    _v_durations(sc, add)
    _v_ties(sc, add)
    _v_tuplets(sc, add)
    _v_dups_empty(sc, add)
    shifted = _v_octaves(sc, add)
    _v_ranges(sc, add, shifted)
    _v_span(sc, add)
    _v_accidentals(sc, add, strict)
    if not strict:
        iss = [x for x in iss if x["severity"] != "low"]
    seen, out = set(), []
    for x in iss:
        k = (x["kind"], x["bar"], x["voice"], x["message"])
        if k not in seen:
            seen.add(k)
            out.append(x)
    out.sort(key=lambda x: (_RANK[x["severity"]], _bar_sort(x["bar"])))
    if max_issues and len(out) > max_issues:
        extra = len(out) - max_issues
        out = out[:max_issues]
        out.append(_issue("info", "more", None, None, None, f"{extra} more issues not shown (max_issues)"))
    return out


def _bar_sort(b):
    try:
        return float(b)
    except (TypeError, ValueError):
        return 1e9


def _bar_of_line(sc, line):
    best = None
    for gm in sc.measures:
        if gm["line"] and gm["line"] <= line:
            best = gm["num"]
    return best


def _vn(name):
    """A voice name for messages: '1' -> 'voice 1'."""
    return f"voice {name}" if str(name).isdigit() else str(name)


def _v_structure(sc, add):
    gms = sc.measures
    n = len(gms)
    open_start = None
    had_end = False
    for i, g in enumerate(gms):
        if g["rep_start"]:
            if open_start is not None:
                add("warning", "repeat", open_start, None, gms[open_start]["line"],
                    f"repeat start |: at bar {gms[open_start]['num']} has no :| before the next |:",
                    "add the missing :| (or remove the extra |:)")
            open_start = i
        if g["rep_end"]:
            if open_start is None and had_end:
                add("warning", "repeat", i, None, g["line"],
                    "repeat end :| with no |: after the previous repeat: plays back to the bar after it",
                    "add the |: where the repeat starts")
            elif open_start is None and not had_end and i > 0 and any(gm["double"] for gm in gms[:i]):
                add("info", "repeat", i, None, g["line"], ":| with no |: repeats from the start of the piece",
                    "add |: if the repeat starts at a double bar")
            open_start = None
            had_end = True
    if open_start is not None:
        add("warning", "repeat", open_start, None, gms[open_start]["line"],
            f"repeat start |: at bar {gms[open_start]['num']} never closed with :|", "add the :| (or remove |:)")
    # voltas
    i = 0
    while i < n:
        if gms[i]["volta"]:
            j = i
            groups = []
            while j < n and gms[j]["volta"]:
                if not groups or gms[j]["volta"] != groups[-1][0] or gms[j].get("volta_start"):
                    groups.append([gms[j]["volta"], j, j])
                else:
                    groups[-1][2] = j
                j += 1
            nums = [x for g in groups for x in g[0]]
            if 1 not in nums:
                add("warning", "volta", i, None, gms[i]["line"],
                    f"ending [{','.join(map(str, groups[0][0]))}] with no 1st ending before it",
                    "the first ending is probably [1")
            if len(groups) == 1 and not any(gms[k]["rep_end"] for k in range(i, j)):
                add("warning", "volta", i, None, gms[i]["line"],
                    "a 1st ending with no :| and no 2nd ending", "close it with :| and add the [2 ending")
            for vols, a, b in groups[:-1]:
                if not any(gms[k]["rep_end"] for k in range(a, b + 1)):
                    add("warning", "volta", a, None, gms[a]["line"],
                        f"ending [{','.join(map(str, vols))}] does not end with :|",
                        "a non-final ending must end with a repeat sign :|")
            if len(nums) != len(set(nums)):
                add("warning", "volta", i, None, gms[i]["line"], "an ending number appears twice in one group",
                    "number the endings [1 [2 ...")
            last = groups[-1]
            if last[2] - last[1] >= 4 and len(groups) > 1:
                add("warning", "volta", last[1], None, gms[last[1]]["line"],
                    f"the last ending runs {last[2] - last[1] + 1} bars (no || or |] to close it?)",
                    "end the ending with || after its bar(s)")
            i = j
        else:
            i += 1
    nav = getattr(sc, "nav", {})
    alln = [mk for g in gms for mk in g["nav"]]
    if any("tocoda" == m for m in alln) and nav.get("coda") is None:
        add("error", "nav", None, None, None, "'To Coda' but no coda bar found", "mark the coda bar (!coda!)")
    if ("fine" in alln) and not ("dc" in alln or "ds" in alln):
        add("info", "nav", None, None, None, "Fine marked but no D.C./D.S.: playback ignores it")
    # key changes in some voices only (ABC)
    if sc.source == "abc" and len(sc.voices) > 1:
        changes = {}
        for v in sc.voices:
            for midx, name in v.key_changes:
                if midx > 0:
                    changes.setdefault(midx, {})[v.name] = name
        for midx, d in sorted(changes.items()):
            missing = [v.name for v in sc.voices if v.name not in d]
            if missing:
                add("warning", "key", midx, ", ".join(missing), sc.measures[min(midx, len(gms) - 1)]["line"],
                    f"key change to {next(iter(d.values()))} in {', '.join(d)} but not in {', '.join(missing)}",
                    "write the [K:] in every voice")


def _v_durations(sc, add):
    gms = sc.measures
    for i, g in enumerate(gms):
        mq = _meter_q(g["meter"])
        if mq is None:
            continue
        present = [v for v in sc.voices if v.measures[i]["present"]]
        lens = {v.name: v.measures[i]["len"] for v in present}
        for v in present:
            vm = v.measures[i]
            if vm.get("mrest") or (vm["empty"] and not vm["events"]):
                continue
            L = vm["len"]
            exp = g["len"] if g["implicit"] else mq
            if L == exp:
                continue
            if not v.primary and L < exp:
                if L > 0:
                    add("info", "duration", i, v.name, vm["line"],
                        f"bar {g['num']} {_vn(v.name)}: secondary voice fills {_beats(L, g)} of {_beats(exp, g)} beats")
                continue
            if g["implicit"] and L < g["len"]:
                add("warning", "duration", i, v.name, vm["line"],
                    f"bar {g['num']} {_vn(v.name)}: {_beats(L, g)} beats while other voices have {_beats(g['len'], g)}",
                    _dur_fix(vm, g["len"] - L))
                continue
            d = exp - L
            what = "missing" if d > 0 else "extra"
            others = {k: x for k, x in lens.items() if k != v.name and x != L}
            oth = ("; " + ", ".join(f"{k} has {_beats(x, g)}" for k, x in list(others.items())[:2])) if others else ""
            add("error", "duration", i, v.name, vm["line"],
                f"bar {g['num']} {_vn(v.name)}: {_beats(L, g)} beats in {g['meter'][0]}/{g['meter'][1]} "
                f"({what} {_q_name(abs(d))}){oth}", _dur_fix(vm, d))


def _beats(q, g):
    m = g["meter"]
    b = Fr(q) * (m[1] if m else 4) / 4
    return str(b.numerator) if b.denominator == 1 else f"{float(b):g}"


def _dur_fix(vm, d):
    """Suggest a fix for a bar that is d quarters short (d > 0) or long (d < 0)."""
    evs = [e for e in vm["events"] if not e["grace"]]
    if d > 0:
        for e in evs:
            if e["dur"] == 2 * d:
                nm = _spell(e["notes"][0]) if e["notes"] else "rest"
                return f"a dot lost on {nm} ({_q_name(e['dur'])})? or another note's length misread"
        return f"{_q_name(d)} missing (a note, rest or a wrong length)"
    d = -d
    for e in evs:
        if e["dur"] / 3 == d:
            nm = _spell(e["notes"][0]) if e["notes"] else "rest"
            return f"an extra dot on the {nm}?"
    return f"{_q_name(d)} too much (a wrong length or a note from another voice)"


def _v_ties(sc, add):
    for o, cands in getattr(sc, "unresolved_ties", []):
        n0 = o["_n"]
        same_voice = [c for c in cands if c["_vi"] == o["_vi"] and not c["grace"]]
        v = sc.voices[o["_vi"]]
        if same_voice:
            tgt = min(same_voice, key=lambda c: abs(c["pitch"] - o["pitch"]))
            near = abs(tgt["pitch"] - o["pitch"]) <= 2
            add("warning", "tie", o["_midx"], v.name, o["source_line"],
                f"bar {o['measure']} {_vn(v.name)}: tie from {_spell(n0)} to {_spell(tgt['_n'])} (a tie joins equal pitches)",
                "accidental lost on the tied note?" if near and tgt["_n"].get("step") == n0.get("step")
                else "a slur written as a tie, or a misread note")
        else:
            add("warning", "tie", o["_midx"], v.name, o["source_line"],
                f"bar {o['measure']} {_vn(v.name)}: tie from {_spell(n0)} leads to no note", "remove the tie or check the "
                "next note")


def _v_tuplets(sc, add):
    for v in sc.voices:
        for i, vm in enumerate(v.measures):
            for t in vm.get("tuplets", []):
                p, q = t.get("p"), t.get("q")
                if not t["durs"]:
                    continue
                tot = sum(t["durs"], Fr(0))
                if t.get("cut"):
                    add("warning", "tuplet", t.get("midx", i), v.name, t["line"],
                        f"bar {sc.measures[i]['num']} {_vn(v.name)}: tuplet ({p} has only {len(t['durs'])} of its notes "
                        f"before the bar line", "check the tuplet number / notes")
                elif not _nice(tot):
                    add("warning", "tuplet", i, v.name, t["line"],
                        f"bar {sc.measures[i]['num']} {_vn(v.name)}: tuplet ({p}:{q} spans {tot} quarters, not a note value",
                        f"a ({p} over notes of mixed length needs (p:q:r, e.g. (3:2:2 for a quarter+eighth triplet")


def _v_dups_empty(sc, add):
    for v in sc.voices:
        for i, vm in enumerate(v.measures):
            g = sc.measures[i]
            if vm["present"] and not vm["events"] and v.primary:
                add("warning", "empty", i, v.name, vm["line"] or g["line"],
                    f"bar {g['num']} {_vn(v.name)}: empty bar", "add the rest (z / Z) or the notes that belong there")
            seen = {}
            for e in vm["events"]:
                for nt in e["notes"]:
                    k = (e["pos"], nt["pitch"], e["grace"])
                    if k in seen:
                        add("warning", "duplicate", i, v.name, e["line"],
                            f"bar {g['num']} {_vn(v.name)}: {_spell(nt)} written twice at the same time",
                            "remove one (or move it to another voice)")
                    seen[k] = True


def _notes_by_bar(sc, v):
    """[(midx, [sounding non-grace notes])] for bars that have notes."""
    out = []
    for i, snd in enumerate(v.sound):
        ns = [s for s in snd if not s["grace"]]
        if ns:
            out.append((i, ns))
    return out


def _median(xs):
    xs = sorted(xs)
    k = len(xs)
    return (xs[k // 2] + xs[(k - 1) // 2]) / 2 if k else None


def _v_octaves(sc, add):
    """Runs of bars shifted ~an octave or ~a clef (octave + 6th) from their neighbours."""
    flagged = set()
    for v in sc.voices:
        bars = _notes_by_bar(sc, v)
        if len(bars) < 4:
            continue
        means = [sum(n["pitch"] for n in ns) / len(ns) for _, ns in bars]
        cand = []
        for k in range(len(bars)):
            ref = means[max(0, k - 6):k] + means[k + 1:k + 7]
            r = _median(ref)
            cand.append(0 if r is None or abs(means[k] - r) < 9 else (1 if means[k] > r else -1))
        k = 0
        while k < len(bars):
            if cand[k] == 0:
                k += 1
                continue
            s = cand[k]
            j = k
            while j < len(bars) and cand[j] == s:
                j += 1
            run = range(k, j)
            if len(run) <= 6:
                ref = [means[x] for x in range(max(0, k - 8), k) if cand[x] == 0] + \
                      [means[x] for x in range(j, min(len(bars), j + 8)) if cand[x] == 0]
                if len(ref) >= 3:
                    r = _median(ref)
                    rm = sum(means[x] for x in run) / len(run)
                    delta = rm - r
                    refb = [x for x in list(range(max(0, k - 8), k)) + list(range(j, min(len(bars), j + 8)))
                            if cand[x] == 0]
                    rp = [n["pitch"] for x in run for n in bars[x][1]]
                    if delta > 0:
                        apart = min(rp) >= _median([max(n["pitch"] for n in bars[x][1]) for x in refb])
                    else:
                        apart = max(rp) <= _median([min(n["pitch"] for n in bars[x][1]) for x in refb])
                    if abs(delta) >= 10 and apart:
                        strong = _shift_evidence(sc, v, bars, k, j, refb, delta)
                        a, b = sc.measures[bars[k][0]]["num"], sc.measures[bars[j - 1][0]]["num"]
                        where = f"bar {a}" if a == b else f"bars {a}-{b}"
                        dirw = "above" if delta > 0 else "below"
                        clef = v.clef
                        if 16 <= abs(delta) <= 24:
                            what = "an octave and a 6th"
                            if delta > 0:
                                fix = f"{'bass' if clef != 'treble' else 'a bass-clef passage'} clef read as treble? " \
                                      f"move these notes down 12 staff steps (a 6th + an octave)"
                            else:
                                fix = "treble clef read as bass? move these notes up 12 staff steps"
                        elif 10 <= abs(delta) <= 15:
                            what = "an octave"
                            fix = f"octave misread (8va sign / {',' if delta > 0 else chr(39)} marks)? move them " \
                                  f"{'down' if delta > 0 else 'up'} an octave"
                        else:
                            what = f"{abs(round(delta))} semitones"
                            fix = "check the clef and octave marks of this passage"
                        add("warning" if strong else "info", "octave", bars[k][0], v.name, bars[k][1][0]["line"],
                            f"{where} {_vn(v.name)}: notes ~{what} {dirw} the bars around them", fix)
                        for x in run:
                            flagged.add((v.name, bars[x][0]))
            k = j
    return flagged


# comfortable range of a clef: the staff plus 3 ledger lines (written pitch)
CLEF_COMFORT = {"treble": (53, 88), "bass": (33, 67), "alto": (43, 77), "tenor": (40, 74), "baritone": (36, 71)}


def _shift_evidence(sc, v, bars, k, j, refb, delta):
    """Is a register jump of bars[k:j] likely a misreading (not a written octave change)? Evidence: leaps of 9+
    semitones into and out of it, most notes beyond 3 ledger lines, or a bar repeating a neighbour an octave away."""
    jin = bars[k][1][0]["pitch"] - bars[k - 1][1][-1]["pitch"] if k > 0 else None
    jout = bars[j][1][0]["pitch"] - bars[j - 1][1][-1]["pitch"] if j < len(bars) else None
    jumps = [x for x in (jin, jout) if x is not None]
    if jumps and all(abs(x) >= 9 for x in jumps):
        return True
    run = [n for x in range(k, j) for n in bars[x][1]]
    out = 0
    for n in run:
        lo, hi = CLEF_COMFORT.get(n["n"].get("clef", v.clef), (0, 127))
        wp = n["n"].get("wpitch", n["pitch"])
        out += wp < lo or wp > hi
    if out * 2 >= len(run):
        return True
    near = [x for x in refb if k - 2 <= x < k or j <= x < j + 2]
    for x in range(k, j):
        a = [n["pitch"] for n in bars[x][1]]
        for y in near:
            b = [n["pitch"] for n in bars[y][1]]
            if len(a) == len(b) and len(a) >= 2:
                d = {p - q for p, q in zip(a, b)}
                if len(d) == 1 and next(iter(d)) % 12 == 0 and next(iter(d)) != 0:
                    return True
    return False


def _v_ranges(sc, add, shifted):
    for v in sc.voices:
        bad = []
        for i, snd in enumerate(v.sound):
            if (v.name, i) in shifted:
                continue
            for s in snd:
                n = s["n"]
                lo, hi = CLEF_RANGE.get(n.get("clef", v.clef), (0, 127))
                wp = n.get("wpitch", s["pitch"])
                if v.piano and not (PIANO_RANGE[0] <= s["pitch"] <= PIANO_RANGE[1]):
                    add("error", "range", i, v.name, s["line"],
                        f"bar {sc.measures[i]['num']} {_vn(v.name)}: {_spell(n)} is outside the piano (A0-C8)",
                        "octave marks (' ,) misread?")
                elif wp < lo or wp > hi:
                    bad.append((i, s, n, "high" if wp > hi else "low"))
        # group into runs of bars
        runs = []
        for i, s, n, hl in bad:
            if runs and i - runs[-1][1] <= 1 and runs[-1][3] == hl:
                runs[-1][1] = i
                runs[-1][2].append(n)
            else:
                runs.append([i, i, [n], hl, s["line"]])
        for a, b, ns, hl, line in runs:
            na, nb = sc.measures[a]["num"], sc.measures[b]["num"]
            where = f"bar {na}" if a == b else f"bars {na}-{nb}"
            clef = ns[0].get("clef", v.clef)
            ex = ", ".join(sorted({_spell(x) for x in ns})[:3])
            if clef == "bass" and hl == "high":
                fix = "a treble-clef passage in this staff (clef change missed), or notes read an octave high"
            elif clef == "treble" and hl == "low":
                fix = "a bass-clef passage in this staff (clef change missed), or notes read an octave low"
            else:
                fix = "octave marks misread, or an 8va/8vb sign?"
            add("warning", "range", a, v.name, line,
                f"{where} {_vn(v.name)}: {len(ns)} note{'s' if len(ns) > 1 else ''} ({ex}) far {'above' if hl == 'high' else 'below'} "
                f"a {clef}-clef staff", fix)


def _v_span(sc, add):
    by_staff = {}
    for v in sc.voices:
        if v.piano:
            by_staff.setdefault(v.staff, []).append(v)
    for vs in by_staff.values():
        for i in range(len(sc.measures)):
            groups = {}
            for v in vs:
                for s in v.sound[i]:
                    if not s["grace"]:
                        groups.setdefault(s["pos"], []).append((s, v))
            for _, lst in sorted(groups.items()):
                ps = [s["pitch"] for s, _ in lst]
                if max(ps) - min(ps) > 16:
                    lo = min(lst, key=lambda x: x[0]["pitch"])
                    hi = max(lst, key=lambda x: x[0]["pitch"])
                    g = sc.measures[i]
                    add("warning", "span", i, lo[1].name, hi[0]["line"],
                        f"bar {g['num']} {_vn(lo[1].name)}: chord {_spell(lo[0]['n'])}-{_spell(hi[0]['n'])} spans "
                        f"{max(ps) - min(ps)} semitones in one hand (more than a 10th)",
                        "one note an octave off, or it belongs to the other hand?")
                    break


def _v_accidentals(sc, add, strict):
    gms = sc.measures
    # per-voice written note lists
    allw = []
    for vi, v in enumerate(sc.voices):
        seq = []
        for i, snd in enumerate(v.sound):
            for s in snd:
                if s["grace"] or "step" not in s["n"]:
                    continue
                seq.append((i, s))
        allw.append(seq)
        # whole-voice: a letter almost always altered against the key -> key signature misread
        byl = {}
        for i, s in seq:
            byl.setdefault(s["n"]["step"], []).append((i, s))
        for step, lst in byl.items():
            alts = {}
            for i, s in lst:
                alts[s["n"]["alter"]] = alts.get(s["n"]["alter"], 0) + 1
            ka = lst[0][1]["n"]["kalter"]
            key0 = v.measures[lst[0][0]]["key"]
            if key0["mode"] == -3 and key0["name"][0] in STEPS and (step - STEPS.index(key0["name"][0])) % 7 in (5, 6):
                continue
            for a, c in alts.items():
                if a != ka and c >= 8 and c >= 0.9 * len(lst) and all(s["n"]["kalter"] == ka for _, s in lst):
                    acc = {1: "#", -1: "b", 0: " natural", 2: "##", -2: "bb"}[a]
                    add("warning", "key", lst[0][0], v.name, lst[0][1]["line"],
                        f"{_vn(v.name)}: {STEPS[step]} is {STEPS[step]}{acc} in {c} of {len(lst)} notes, against the key "
                        f"signature", "key signature misread (a sharp/flat missing or extra)?")
        # singleton against a consistent passage
        for step, lst in byl.items():
            for i, s in lst:
                n = s["n"]
                win = [(j, x) for j, x in lst if abs(j - i) <= 4 and x is not s]
                same = [x for j, x in win if x["n"]["alter"] == n["alter"]]
                if same:
                    continue
                cnt = {}
                for j, x in win:
                    cnt[x["n"]["alter"]] = cnt.get(x["n"]["alter"], 0) + 1
                if not cnt:
                    continue
                maj, c = max(cnt.items(), key=lambda kv: kv[1])
                if c < 3:
                    continue
                before = any(j <= i and x["n"]["alter"] == maj and x["pos"] + (gms[j]["start_q"]) <
                             s["pos"] + gms[i]["start_q"] for j, x in win)
                after = any(x["n"]["alter"] == maj and x["pos"] + gms[j]["start_q"] > s["pos"] + gms[i]["start_q"]
                            for j, x in win)
                if not (before and after):
                    continue
                key = v.measures[i]["key"]
                deg = (step - STEPS.index(key["name"][0])) % 7 if key["name"][0] in STEPS else None
                if key["mode"] == -3 and deg in (5, 6):
                    continue                        # raised/lowered 6th-7th in minor
                if _chromatic_step(seq, s):
                    continue
                if _supported(sc, vi, i, step, n["alter"]):
                    continue
                acc = lambda a: {1: "#", -1: "b", 0: "", 2: "##", -2: "bb"}[a]  # noqa: E731
                if n["alter"] == n["kalter"]:
                    fix = f"accidental missing: {STEPS[step]}{acc(maj)}{n['octave']}?"
                else:
                    fix = f"misread accidental: {STEPS[step]}{acc(maj) or ' natural'}{n['octave']}?"
                add("warning", "accidental", i, v.name, s["line"],
                    f"bar {gms[i]['num']} {_vn(v.name)}: {_spell(n)} where the bars around it have "
                    f"{STEPS[step]}{acc(maj) or ' natural'} x{c}", fix)
    # cross-voice: same letter, different accidental, sounding together
    ev = []
    for vi, seq in enumerate(allw):
        for i, s in seq:
            st = gms[i]["start_q"] + s["pos"]
            ev.append((st, st + s["dur"], vi, i, s))
    ev.sort(key=lambda x: x[0])
    for a in range(len(ev)):
        s0, e0, v0, _, n0 = ev[a]
        for b in range(a + 1, len(ev)):
            s1, _, v1, i1, n1 = ev[b]
            if s1 >= e0:
                break
            if v1 == v0:
                continue
            x, y = n0["n"], n1["n"]
            if x.get("salter") is None or y.get("salter") is None:
                continue
            if x["sstep"] == y["sstep"] and x["salter"] != y["salter"]:
                simul = s1 == s0
                if not simul and not strict:
                    continue
                odd = n0 if x["alter"] != x["kalter"] else n1
                ov = sc.voices[v0 if odd is n0 else v1]
                add("warning" if simul else "info", "accidental", i1, ov.name, odd["line"],
                    f"bar {gms[i1]['num']}: {_spell(x)} ({_vn(sc.voices[v0].name)}) against {_spell(y)} "
                    f"({_vn(sc.voices[v1].name)}) at the same time", "one of them lost or gained an accidental")
            elif strict and s1 == s0:
                iv = abs(n0["pitch"] - n1["pitch"]) % 12
                for odd, other in ((n0, n1), (n1, n0)):
                    o = odd["n"]
                    if o["alter"] != o["kalter"] and iv in (1, 11):
                        alt = abs(odd["pitch"] - (o["alter"] - o["kalter"]) - other["pitch"]) % 12
                        if alt not in (1, 11):
                            add("info", "accidental", i1, sc.voices[v0 if odd is n0 else v1].name, odd["line"],
                                f"bar {gms[i1]['num']}: {_spell(o)} (against the key) makes a semitone clash with "
                                f"{_spell(other['n'])}; the key's version would not", "accidental misread?")


def _chromatic_step(seq, s):
    """True when s moves by a semitone to/from a different letter next to it (a chromatic passing/neighbour note)."""
    idx = next((k for k, (_, x) in enumerate(seq) if x is s), None)
    if idx is None:
        return False
    for k in (idx - 1, idx + 1):
        if 0 <= k < len(seq):
            o = seq[k][1]
            if abs(o["pitch"] - s["pitch"]) == 1 and o["n"]["step"] != s["n"]["step"]:
                return True
    return False


def _supported(sc, vi, i, step, alter):
    """Another voice uses the same spelled note in this bar (the accidental is harmonic, not a slip)."""
    for wj, w in enumerate(sc.voices):
        if wj == vi:
            continue
        for s in w.sound[i]:
            n = s["n"]
            if n.get("step") == step and n.get("alter") == alter:
                return True
    return False


# ============================================================================= score data (per-bar JSON readings)
SCORE_FORMAT = "flh-score/1"
GRID = 48                                       # positions/lengths snap to 1/48 quarter (16ths, 32nds, triplets)
_SD_RESERVED = {"section", "chord", "chords", "roman", "text", "marks", "ties", "note", "notes", "comment", "perc",
                "dyn", "meter", "key", "tempo", "beats", "repeat_start", "repeat_end", "ending", "parts", "rest",
                "page", "system", "src", "lyrics", "fingering", "bar"}
_SD_PITCH = re.compile(r"^\s*([A-Ga-g])(##|bb|x|#|b|n|♯♯|♭♭|♯|♭|♮)?(-?\d)\s*$")
_SD_ACC = {None: 0, "": 0, "#": 1, "♯": 1, "##": 2, "x": 2, "♯♯": 2, "b": -1, "♭": -1, "bb": -2,
           "♭♭": -2, "n": 0, "♮": 0}
_SD_FLAGS = {"tie": "tie", "~": "tie", "-": "tie", "accent": "accent", ">": "accent", "staccato": "staccato",
             ".": "staccato", "stacc": "staccato", "staccatissimo": "staccatissimo", "wedge": "staccatissimo",
             "marcato": "marcato", "^": "marcato", "grace": "grace", "tenuto": None, "fermata": None, "slur": None,
             "arp": None, "arpeggio": None, "trill": None}


def parse_pitch(s):
    """'Bb4' / 'C#5' / 'F##3' / 'Ebb2' / 'Bn4' -> (step 0-6, alter, octave, MIDI) (C4 = 60). ValueError if not."""
    m = _SD_PITCH.match(str(s))
    if not m:
        raise ValueError(f"pitch {s!r}: use a letter, an optional # / b / ## / bb and the octave, like 'Bb4' or "
                         "'C#5' (C4 = middle C)")
    step = STEPS.index(m.group(1).upper())
    alter = _SD_ACC[m.group(2)]
    octave = int(m.group(3))
    midi = (octave + 1) * 12 + STEP_SEMI[step] + alter
    if not 0 <= midi <= 127:
        raise ValueError(f"pitch {s!r} is outside MIDI 0-127")
    return step, alter, octave, midi


def _sd_num(x):
    """A beat / length: number, '1/3', '1 1/2' -> Fraction (exact), or raise ValueError."""
    if isinstance(x, bool):
        raise ValueError("a number")
    if isinstance(x, (int, Fr)):
        return Fr(x)
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError("a finite number")
        return Fr(x)
    s = str(x).strip()
    m = re.fullmatch(r"(\d+)\s+(\d+)\s*/\s*(\d+)", s)
    if m:
        return Fr(int(m.group(1))) + Fr(int(m.group(2)), int(m.group(3)))
    return Fr(s)


def _snap(q):
    """-> (snapped Fraction on the 1/GRID grid, error in quarters)."""
    s = Fr(round(q * GRID), GRID)
    return s, abs(float(q - s))


def load_score_data(src):
    """dict | JSON text | path to a .json file -> (dict, path or None)."""
    import json
    if isinstance(src, dict):
        return src, None
    if isinstance(src, (bytes, bytearray)):
        src = src.decode("utf-8", "replace")
    s = str(src).strip()
    if s.startswith("{"):
        try:
            return json.loads(s), None
        except ValueError as e:
            raise ValueError(f"score data isn't valid JSON: {e}") from None
    p = s.strip('"')
    if "\n" in p or not os.path.isfile(p):
        raise ValueError("score: give the score data as a dict, JSON text or the path to a .json file")
    with open(p, encoding="utf-8-sig") as f:
        try:
            return json.load(f), p
        except ValueError as e:
            raise ValueError(f"{os.path.basename(p)} isn't valid JSON: {e}") from None


def _sd_meter(x, where):
    if isinstance(x, (list, tuple)) and len(x) == 2:
        x = f"{int(x[0])}/{int(x[1])}"
    m = _parse_meter(str(x))
    if m is None:
        raise ValueError(f"{where}: meter 'none' isn't supported in score data; give e.g. '4/4'")
    return m


def _sd_key(x):
    """'Dm' / 'D minor' / 'F' / 'Bb major' / -1 (fifths) / '1b' / '2#' -> ABC key text."""
    if x is None or x == "":
        return "C"
    if isinstance(x, int) and not isinstance(x, bool):
        return key_name(int(x), 0).split()[0]
    s = str(x).strip()
    m = re.fullmatch(r"(\d)\s*([#b])", s)
    if m:
        return key_name(int(m.group(1)) * (1 if m.group(2) == "#" else -1), 0).split()[0]
    if s.lower() in ("0", "none", "open"):
        return "C"
    s = re.sub(r"\s+major$", "", s, flags=re.I)
    return re.sub(r"\s+minor$", "m", s, flags=re.I)


def _sd_tempo(x):
    if isinstance(x, dict):
        x = x.get("bpm")
    v = float(x)
    if not 5 <= v <= 1000:
        raise ValueError(f"tempo {x}: give quarter notes per minute (5-1000)")
    return v


def _sd_bar_parts(bar):
    """A bar dict -> {part: entries}: the 'parts' dict plus top-level keys whose value is a list of notes."""
    out = {}
    for k, v in bar.items():
        if k in _SD_RESERVED or str(k).startswith("_") or not isinstance(v, (list, tuple)):
            continue
        if v and all(isinstance(e, (list, tuple)) for e in v):
            out[str(k)] = list(v)
        elif not v:
            out[str(k)] = []
    pp = bar.get("parts")
    if isinstance(pp, dict):
        for k, v in pp.items():
            if isinstance(v, (list, tuple)):
                out[str(k)] = out.get(str(k), []) + list(v)
    return out


def _sd_chords(c, bar):
    if c is None or c == "":
        return []
    if isinstance(c, str):
        return [[bar, 0, c]]
    out = []
    for e in c if isinstance(c, (list, tuple)) else []:
        if isinstance(e, (list, tuple)) and len(e) >= 2:
            try:
                out.append([bar, float(_sd_num(e[0])), str(e[1])])
            except (ValueError, ZeroDivisionError):
                pass
    return out


def _sd_abc_note(step, octave):
    letter = STEPS[step]
    if octave >= 5:
        return letter.lower() + "'" * (octave - 5)
    return letter + "," * (4 - octave)


def _sd_len(q):
    u = q * GRID
    if u.denominator != 1:
        raise ValueError(f"length {q} off the 1/{GRID} grid")
    return str(int(u))


def _sd_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return 1


def _sd_show(e):
    s = repr(list(e) if isinstance(e, tuple) else e)
    return s if len(s) <= 40 else s[:37] + "..."


def _sd_runs(nums):
    out = []
    for n in nums:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return [tuple(r) for r in out]


def _sd_range_txt(nums):
    return ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in _sd_runs(sorted(nums)))


def _sd_cover(ivs):
    tot, end = Fr(0), None
    for a, b in sorted(ivs):
        if end is None or a >= end:
            tot += b - a
            end = b
        elif b > end:
            tot += b - end
            end = b
    return tot


def _sd_flags(extra, add, where, num, p):
    flags = set()
    for fl in extra:
        words = re.split(r"[\s,+]+", fl.strip().lower()) if isinstance(fl, str) else [repr(fl)]
        for w in words:
            if not w:
                continue
            if w in _SD_FLAGS:
                if _SD_FLAGS[w]:
                    flags.add(_SD_FLAGS[w])
            elif add is not None:
                add("info", "flag", num, p, f"{where}: flag {fl!r} ignored (known: tie, accent, staccato, "
                    "staccatissimo, marcato, grace)")
    return flags


def parse_score_data(data, ppq=PPQ, parts=None):
    """Per-bar score data (docs/sheet_music.md) -> Score, built through generated ABC so every ABC check applies.
    data: dict / JSON text / .json path. parts: only these part names (None = all).
    The Score also gets .score_info = {first_bar, last_bar, bars, parts, layers, sections, chords, beat_check, fixed,
    abc, pages, file, pickup}; problems in the data itself are parse_issues with the data's bar numbers."""
    data, path = load_score_data(data)
    if not isinstance(data, dict):
        raise ValueError("score data must be a JSON object with 'bars'")
    pre = []

    def add(sev, kind, bar, part, msg, fix=""):
        pre.append(_issue(sev, kind, bar, part, None, msg, fix))

    bars = {}
    raw_bars = data.get("bars")
    if isinstance(raw_bars, list):
        raw_bars = {b.get("bar", k + 1): b for k, b in enumerate(raw_bars) if isinstance(b, dict)}
    if not isinstance(raw_bars, dict) or not raw_bars:
        raise ValueError('score data has no "bars": give {"bars": {"1": {"mel": [[0, 1, "C5"], ...]}, ...}}')
    for k, b in raw_bars.items():
        try:
            n = int(str(k).strip())
        except ValueError:
            add("error", "bar_number", None, None, f"bar key {k!r} isn't a whole number (skipped)",
                'key bars by their printed number: "12"')
            continue
        if n < 0:
            add("error", "bar_number", None, None, f"bar {n}: negative bar numbers aren't allowed (skipped)")
            continue
        bars[n] = b if isinstance(b, dict) else {}
        if not isinstance(b, dict):
            add("error", "bar_data", n, None, f"bar {n} isn't an object (treated as empty)",
                'write {"mel": [[beat, length, "Pitch"], ...]}')
    if not bars:
        raise ValueError("score data: no usable bars")
    try:
        meter = _sd_meter(data.get("meter") or "4/4", "meter")
    except ValueError as e:
        raise ValueError(f"score data: {e}") from None
    key_txt = _sd_key(data.get("key"))
    try:
        kdef, _ = _parse_key(key_txt)
    except ValueError as e:
        raise ValueError(f"score data key {data.get('key')!r}: {e}") from None
    kdef = kdef or C_MAJOR
    tempo = None
    if data.get("tempo") not in (None, ""):
        try:
            tempo = _sd_tempo(data["tempo"])
        except (TypeError, ValueError) as e:
            add("warning", "tempo", None, None, f"tempo {data.get('tempo')!r} not understood ({e}): ignored")
    pinfo = data.get("parts") if isinstance(data.get("parts"), dict) else {}
    want = None if parts is None else [str(p) for p in parts]
    first, last = min(bars), max(bars)
    nums = list(range(first, last + 1))
    for a, b in _sd_runs([n for n in nums if n not in bars]):
        add("warning", "missing_bar", a, None, (f"bar {a}" if a == b else f"bars {a}-{b}") + " not in the data (filled "
            "with rests)", "missing from the reading? an empty bar {} is a bar of rests")
    # ------------------------------------------------------------------ per bar: lengths, notes, beat sums
    rows = []                   # (num, length q, meter set here, key set here, tempo set here, bar dict)
    cur_m, cur_k = meter, key_txt
    head_m = None
    sections, chords, pages = [], [], []
    part_order = list(pinfo)
    notes, graces = {}, {}      # part -> [(abs_q, bar_idx, beat, dur, step, alter, octave, midi, flags)]
    check = {"bars": 0, "lines": 0, "full": 0, "gaps": [], "over": []}
    abs_q = Fr(0)
    for bi, num in enumerate(nums):
        b = bars.get(num, {})
        mset = kset = tset = None
        if b.get("meter") not in (None, ""):
            try:
                m2 = _sd_meter(b["meter"], f"bar {num}")
                if m2 != cur_m:
                    mset = m2
                cur_m = m2
            except ValueError as e:
                add("error", "meter", num, None, f"bar {num}: {e}")
        if bi == 0:
            head_m, mset = cur_m, None
        if b.get("key") not in (None, ""):
            try:
                k2 = _sd_key(b["key"])
                _parse_key(k2)
                if k2 != cur_k:
                    kset = k2
                cur_k = k2
            except ValueError as e:
                add("error", "key", num, None, f"bar {num}: key {b.get('key')!r} not understood ({e})")
        if b.get("tempo") not in (None, ""):
            try:
                tset = _sd_tempo(b["tempo"])
            except (TypeError, ValueError) as e:
                add("warning", "tempo", num, None, f"bar {num}: tempo {b.get('tempo')!r} not understood ({e})")
        L = _meter_q(cur_m)
        if b.get("beats") not in (None, ""):
            try:
                L2, _ = _snap(_sd_num(b["beats"]))
                if L2 <= 0:
                    raise ValueError("must be > 0")
                L = L2
            except (ValueError, ZeroDivisionError, TypeError) as e:
                add("error", "bar_length", num, None, f"bar {num}: beats {b.get('beats')!r} not understood ({e})")
        if b.get("section") and (not sections or sections[-1][1] != str(b["section"])):
            sections.append([num, str(b["section"])])
        chords += _sd_chords(b.get("chord", b.get("chords")), num)
        if b.get("page") is not None:
            try:
                pages.append({"page": int(b["page"]), "system": int(b.get("system") or 1), "first_bar": num})
            except (TypeError, ValueError):
                pass
        bparts = _sd_bar_parts(b)
        for p in bparts:
            if p not in part_order:
                part_order.append(p)
        if want is not None:
            bparts = {p: v for p, v in bparts.items() if p in want}
        check["bars"] += 1
        for p, ents in bparts.items():
            ivs, seen = [], {}
            for k, e in enumerate(ents):
                where = f"bar {num} {p}: entry {k + 1} {_sd_show(e)}"
                if not isinstance(e, (list, tuple)) or len(e) < 3:
                    add("error", "entry", num, p, f"{where} isn't [beat, length, 'Pitch']")
                    continue
                try:
                    beat0, dur0 = _sd_num(e[0]), _sd_num(e[1])
                except (ValueError, ZeroDivisionError, TypeError):
                    add("error", "entry", num, p, f"{where}: beat and length must be numbers (quarter notes)")
                    continue
                (beat, eb), (dur, ed) = _snap(beat0), _snap(dur0)
                if max(eb, ed) > 0.002:
                    add("warning", "grid", num, p, f"{where}: {float(beat0):g}/{float(dur0):g} isn't on a 1/{GRID} "
                        f"quarter grid (moved to {float(beat):g}/{float(dur):g})", "write triplets as '1/3', '2/3'")
                flags = _sd_flags(e[3:], add, where, num, p)
                try:
                    step, alter, octave, midi = parse_pitch(e[2])
                except ValueError as ex:
                    add("error", "pitch", num, p, f"{where}: {ex}",
                        "move words (percussion, text) to 'text', or leave the part out")
                    continue
                if "grace" in flags:
                    dur = Fr(0)
                if dur < 0:
                    add("error", "length", num, p, f"{where}: negative length")
                    continue
                if beat < 0 or beat >= L:
                    add("error", "position", num, p, f"{where}: starts at beat {float(beat):g}, outside the bar "
                        f"(0 to {float(L):g}; beats count from 0 in quarter notes)", "wrong bar or wrong beat?")
                    continue
                if dur == 0:
                    graces.setdefault(p, []).append((abs_q + beat, bi, beat, step, alter, octave, midi))
                    continue
                if beat + dur > L:
                    add("error", "overflow", num, p, f"{where}: ends at beat {float(beat + dur):g} but the bar has "
                        f"{float(L):g} (cut at the bar line)", "a wrong length - or a tie: end the note at the bar "
                        "line with 'tie' and start the next bar with the same pitch")
                    check["over"].append(f"{num} {p}")
                    dur = L - beat
                lst = notes.setdefault(p, [])
                if (beat, midi) in seen:
                    j = seen[(beat, midi)]
                    old = lst[j]
                    if dur > old[3]:
                        lst[j] = old[:3] + (dur,) + old[4:8] + (old[8] | flags,)
                    add("info", "shared_head", num, p, f"bar {num} {p}: {e[2]} at beat {float(beat):g} listed twice "
                        "(kept once, the longer)")
                    continue
                seen[(beat, midi)] = len(lst)
                lst.append((abs_q + beat, bi, beat, dur, step, alter, octave, midi, frozenset(flags)))
                ivs.append((beat, beat + dur))
            if ivs:
                check["lines"] += 1
                cov = _sd_cover(ivs)
                if cov >= L:
                    check["full"] += 1
                else:
                    check["gaps"].append(f"{num} {p} -{float(L - cov):g}")
                    add("info", "gap", num, p, f"bar {num} {p}: notes cover {float(cov):g} of {float(L):g} beats "
                        "(rests - or a note missed?)")
        rows.append((num, L, mset, kset, tset, b))
        abs_q += L
    if want is not None:
        miss = [p for p in want if p not in part_order]
        if miss:
            add("warning", "parts", None, None, f"parts {miss} aren't in the data (it has {part_order})")
        part_order = [p for p in part_order if p in want]
    part_order = [p for p in part_order if notes.get(p) or graces.get(p)]
    if not part_order:
        raise ValueError("score data has no notes" + (f" in the parts {want}" if want else "") +
                         ": write [beat, length, 'Pitch'] entries")
    # ------------------------------------------------------------------ layers (ABC voices) per part
    abc_voices = []
    for p in part_order:
        evs = {}
        for t in notes.get(p, []):
            evs.setdefault((t[0], t[3]), []).append(t)
        layers = []
        for g in sorted(evs.values(), key=lambda g: (g[0][0], -max(x[7] for x in g))):
            st, du = g[0][0], g[0][3]
            pick = next((li for li, ly in enumerate(layers)
                         if ly["end"] == st and ly["ties"] & {x[7] for x in g}), None)
            if pick is None:
                pick = next((li for li, ly in enumerate(layers) if ly["end"] <= st and not ly["ties"]), None)
            if pick is None:
                pick = next((li for li, ly in enumerate(layers) if ly["end"] <= st), None)
            if pick is None:
                layers.append({"end": Fr(0), "ties": set(), "evs": [], "graces": []})
                pick = len(layers) - 1
            ly = layers[pick]
            ly["evs"].append(g)
            ly["end"] = st + du
            ly["ties"] = {x[7] for x in g if "tie" in x[8]}
        if not layers:
            layers.append({"end": Fr(0), "ties": set(), "evs": [], "graces": []})
        for gr in sorted(graces.get(p, [])):
            t = gr[0]
            pick = next((li for li, ly in enumerate(layers) if any(g[0][0] == t for g in ly["evs"])), None)
            if pick is None:
                pick = next((li for li, ly in enumerate(layers)
                             if not any(g[0][0] < t < g[0][0] + g[0][3] for g in ly["evs"])), None)
            if pick is None:
                layers.append({"end": Fr(0), "ties": set(), "evs": [], "graces": []})
                pick = len(layers) - 1
            layers[pick]["graces"].append(gr)
        abc_voices += [(p, ly) for ly in layers]
    # ------------------------------------------------------------------ ABC text (one line per voice and bar)
    title = str(data.get("title") or "").replace("\n", " ").strip()
    out = ["X:1", f"T:{title or 'Score'}", f"M:{head_m[0]}/{head_m[1]}", "L:1/192"]
    if tempo:
        out.append(f"Q:1/4={tempo:g}")
    for vi, (p, ly) in enumerate(abc_voices):
        info = pinfo.get(p) if isinstance(pinfo.get(p), dict) else {}
        clef = str(info.get("clef") or "").lower()
        if clef not in CLEF_RANGE:
            ps = sorted(t[7] for t in notes.get(p, [])) or [60]
            clef = "bass" if ps[len(ps) // 2] < 57 else "treble"
        prog = info.get("program")
        nm = re.sub(r'["\n]', "'", p)
        out.append(f'V:v{vi + 1} name="{nm}" clef={clef}' +
                   (f" program={int(prog)}" if isinstance(prog, int) and not isinstance(prog, bool) else ""))
    out.append(f"K:{key_txt}")
    line_map = {}
    for vi, (p, ly) in enumerate(abc_voices):
        out.append(f"V:v{vi + 1}")
        kalt = dict(kdef["alters"])
        by_bar, gr_bar = {}, {}
        for g in ly["evs"]:
            by_bar.setdefault(g[0][1], []).append(g)
        for gr in ly["graces"]:
            gr_bar.setdefault(gr[1], {}).setdefault(gr[2], []).append(gr)
        prev_tied = set()
        for bi, (num, L, mset, kset, tset, b) in enumerate(rows):
            s = []
            if b.get("repeat_start"):
                s.append("|:")
            if b.get("ending") not in (None, ""):
                s.append(f"|[{_sd_int(b['ending'])} ")
            if mset:
                s.append(f"[M:{mset[0]}/{mset[1]}]")
            if kset:
                s.append(f"[K:{kset}]")
                kalt = dict((_parse_key(kset)[0] or C_MAJOR)["alters"])
            if tset and vi == 0:
                s.append(f"[Q:1/4={tset:g}]")
            dy = b.get("dyn")
            dy = dy.get(p) if isinstance(dy, dict) else dy
            if isinstance(dy, str) and dy.strip().lower() in DYN:
                s.append(f"!{dy.strip().lower()}!")
            state, gmap, tied_now = {}, dict(gr_bar.get(bi, {})), set()
            cuts = sorted(gmap)
            pos = Fr(0)
            at_start = [True]

            def acc_for(step, alter, octave):
                cur = state.get((step, octave), kalt.get(STEPS[step], 0))
                if alter != cur or (at_start[0] and (step, octave) in prev_tied):
                    state[(step, octave)] = alter
                    return {2: "^^", 1: "^", 0: "=", -1: "_", -2: "__"}[alter]
                return ""

            def graces_txt(t):
                gs = gmap.pop(t, [])
                return "{" + "".join(acc_for(g[3], g[4], g[5]) + _sd_abc_note(g[3], g[5]) for g in gs) + "}" \
                    if gs else ""

            def rest_to(t, pos):
                while pos < t:
                    nxt = min([c for c in cuts if pos < c < t] + [t])
                    s.append(graces_txt(pos) + f"z{_sd_len(nxt - pos)} ")
                    at_start[0] = False
                    pos = nxt
                return pos

            for g in sorted(by_bar.get(bi, []), key=lambda g: g[0][2]):
                beat, du = g[0][2], g[0][3]
                pos = rest_to(beat, pos)
                fl = set().union(*[x[8] for x in g])
                deco = ("!accent!" if "accent" in fl else "") + ("!marcato!" if "marcato" in fl else "") + \
                    ("!wedge!" if "staccatissimo" in fl else "." if "staccato" in fl else "")
                pre_g = graces_txt(beat)
                ns_ = []
                for x in sorted(g, key=lambda x: x[7]):
                    tie = "-" if "tie" in x[8] and len(g) > 1 else ""
                    ns_.append(acc_for(x[4], x[5], x[6]) + _sd_abc_note(x[4], x[6]) + tie)
                    if "tie" in x[8]:
                        tied_now.add((x[4], x[6]))
                one = len(ns_) == 1
                s.append(pre_g + deco + (ns_[0] if one else "[" + "".join(ns_) + "]") + _sd_len(du) +
                         ("-" if one and "tie" in g[0][8] else "") + " ")
                at_start[0] = False
                pos = beat + du
            pos = rest_to(L, pos)
            for t in sorted(gmap):              # cannot happen (graces sit on a free spot); keep them visible
                s.append(graces_txt(t))
            prev_tied = tied_now
            end = ":|" if b.get("repeat_end") else ("|]" if bi == len(rows) - 1 else "|")
            out.append("".join(s).rstrip() + " " + end)
            line_map[len(out)] = (num, p)
    abc = "\n".join(out) + "\n"
    try:
        sc = parse_abc(abc, ppq=ppq)
    except ValueError as e:
        raise ValueError(f"score data couldn't be built ({e})") from None
    # ------------------------------------------------------------------ back to the data's bar numbers
    renum = {}
    for i, gm in enumerate(sc.measures):
        new = rows[i][0] if i < len(rows) else gm["num"]
        renum[gm["num"]] = new
        gm["num"] = new
    for lst in (sc.notes, getattr(sc, "_raw_notes", [])):
        for n_ in lst:
            n_["measure"] = renum.get(n_["measure"], n_["measure"])
    for x in sc.parse_issues:
        if x.get("line") in line_map:
            x["bar"], x["voice"] = line_map[x["line"]]
    sc.source = "score"
    sc.title = title
    if data.get("composer"):
        sc.composer = str(data["composer"])
    fixed = _sd_overlaps(sc)
    sc.parse_issues = pre + sc.parse_issues
    gaps = check.pop("gaps")
    check["gaps"] = len(gaps)
    if gaps:
        check["gap_list"] = gaps[:12]
    if not check["over"]:
        check.pop("over")
    sc.score_info = {"first_bar": first, "last_bar": last, "bars": len(rows), "parts": part_order,
                     "layers": {p: sum(1 for q_, _ in abc_voices if q_ == p) for p in part_order},
                     "sections": sections, "chords": chords, "beat_check": check, "fixed": fixed, "abc": abc,
                     "pages": pages, "file": path, "part_info": pinfo,
                     "pickup": bool(len(rows) > 1 and rows[0][1] < _meter_q(head_m))}
    return sc


def _sd_overlaps(sc):
    """The same key twice at once in one part (layers, shared heads, a grace under a held note): the later note
    cuts the earlier one where it starts; equal starts keep the longer. -> {cut, dropped} counts."""
    out = {"cut": 0, "dropped": 0}
    by = {}
    for n in sc.notes:
        by.setdefault((n["voice"], n["pitch"]), []).append(n)
    drop = set()
    for lst in by.values():
        lst.sort(key=lambda n: (n["start_tick"], -n["len_ticks"]))
        prev = None
        for n in lst:
            if prev is not None and n["start_tick"] < prev["start_tick"] + prev["len_ticks"]:
                if n["start_tick"] == prev["start_tick"]:
                    drop.add(id(n))
                    out["dropped"] += 1
                    continue
                prev["len_ticks"] = n["start_tick"] - prev["start_tick"]
                out["cut"] += 1
            prev = n
    if drop:
        sc.notes = [n for n in sc.notes if id(n) not in drop]
    return {k: v for k, v in out.items() if v}


# ------------------------------------------------------------------ comparing / merging readings
def _sd_bars_by_num(d):
    raw = d.get("bars") if isinstance(d, dict) else None
    if isinstance(raw, list):
        raw = {b.get("bar", k + 1): b for k, b in enumerate(raw) if isinstance(b, dict)}
    out = {}
    for k, b in (raw or {}).items() if isinstance(raw, dict) else ():
        try:
            out[int(str(k).strip())] = b if isinstance(b, dict) else {}
        except ValueError:
            continue
    return out


def _sd_norm_bar(b, parts=None):
    """A bar dict -> {part: sorted ((beat, dur, midi, tie), ...)} for comparing readings (bad entries skipped)."""
    out = {}
    for p, ents in _sd_bar_parts(b if isinstance(b, dict) else {}).items():
        if parts is not None and p not in parts:
            continue
        lst = set()
        for e in ents:
            try:
                bt, du, mi = _snap(_sd_num(e[0]))[0], _snap(_sd_num(e[1]))[0], parse_pitch(e[2])[3]
            except (ValueError, ZeroDivisionError, TypeError, IndexError):
                continue
            fl = _sd_flags(e[3:], None, "", 0, p)
            lst.add((bt, Fr(0) if "grace" in fl else du, mi, "tie" in fl))
        out[p] = tuple(sorted(lst))
    return out


def _sd_q(q):
    f = float(q)
    return str(int(f)) if f == int(f) else f"{f:.3f}".rstrip("0").rstrip(".")


def _sd_note_txt(n):
    return f"{pitch_name(n[2])}@{_sd_q(n[0])}" + (f"/{_sd_q(n[1])}" if n[1] else " (grace)")


def diff_bar_parts(a, b):
    """Two normalized bars ({part: notes}) -> [{part, changed, only_a, only_b}] (empty list = the same)."""
    out = []
    for p in sorted(set(a) | set(b), key=lambda p: (p not in a, p)):
        A, B = list(a.get(p, ())), list(b.get(p, ()))
        if A == B:
            continue
        if not A or not B:
            out.append({"part": p, ("only_b" if not A else "only_a"): f"the whole part ({len(A or B)} notes)"})
            continue
        ca, cb = [x for x in A if x not in B], [x for x in B if x not in A]
        changed = []
        for x in list(ca):
            m = next((y for y in cb if y[0] == x[0] and y[1] == x[1] and y[2] != x[2]), None)
            if m is not None:
                d = m[2] - x[2]
                changed.append(f"beat {_sd_q(x[0])}: {pitch_name(x[2])} vs {pitch_name(m[2])}" +
                               (" (octave)" if d and d % 12 == 0 else " (accidental?)" if abs(d) == 1 else ""))
            else:
                m = next((y for y in cb if y[0] == x[0] and y[2] == x[2]), None)
                if m is None:
                    continue
                if m[1] == x[1]:
                    changed.append(f"beat {_sd_q(x[0])}: {pitch_name(x[2])} tie {'yes' if x[3] else 'no'} vs "
                                   f"{'yes' if m[3] else 'no'}")
                else:
                    changed.append(f"beat {_sd_q(x[0])}: {pitch_name(x[2])} length {_sd_q(x[1])} vs {_sd_q(m[1])}")
            ca.remove(x)
            cb.remove(m)
        e = {"part": p}
        if changed:
            e["changed"] = changed[:8]
        if ca:
            e["only_a"] = [_sd_note_txt(x) for x in ca][:8]
        if cb:
            e["only_b"] = [_sd_note_txt(x) for x in cb][:8]
        out.append(e)
    return out


def diff_score_data(a, b, parts=None, max_bars=40):
    """Two per-bar readings (dict / JSON / path) of the same music -> the bars where they disagree, part by part
    (beats as in the data: 0-based quarters), header differences, and bars only one reading has."""
    A, B = load_score_data(a)[0], load_score_data(b)[0]
    ba, bb = _sd_bars_by_num(A), _sd_bars_by_num(B)
    meta = [f"{k}: {A.get(k)!r} vs {B.get(k)!r}" for k in ("meter", "key", "tempo")
            if A.get(k) is not None and B.get(k) is not None and str(A.get(k)) != str(B.get(k))]
    both = sorted(set(ba) & set(bb))
    bars, same = [], 0
    for n in both:
        hd = [f"{k} {ba[n][k]!r} vs {bb[n][k]!r}" for k in ("meter", "beats", "key")
              if ba[n].get(k) is not None and bb[n].get(k) is not None and str(ba[n][k]) != str(bb[n][k])]
        d = diff_bar_parts(_sd_norm_bar(ba[n], parts), _sd_norm_bar(bb[n], parts))
        if d or hd:
            bars.append({"bar": n, **({"header": hd} if hd else {}), **({"parts": d} if d else {})})
        else:
            same += 1
    only_a, only_b = sorted(set(ba) - set(bb)), sorted(set(bb) - set(ba))
    out = {"same": not bars and not meta and not only_a and not only_b, "bars_compared": len(both),
           "bars_same": same, "bars_differ": len(bars), "bars": bars[:max_bars]}
    if meta:
        out["meta"] = meta
    if only_a:
        out["only_a"] = _sd_range_txt(only_a)
    if only_b:
        out["only_b"] = _sd_range_txt(only_b)
    if len(bars) > max_bars:
        out["more"] = len(bars) - max_bars
    return out


def _sd_copy(x):
    if isinstance(x, dict):
        return {k: _sd_copy(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_sd_copy(v) for v in x]
    return x


def _sd_put_part(bar, p, ents):
    """Set part p of a bar where it lives (top level or under 'parts')."""
    if isinstance(bar.get(p), list) and p not in _SD_RESERVED:
        bar[p] = _sd_copy(ents)
        return
    if not isinstance(bar.get("parts"), dict):
        bar["parts"] = {}
    bar["parts"][p] = _sd_copy(ents)


def merge_score_data(readings, prefer=None):
    """Several per-bar readings (one per page range, or per instrument group) -> (merged dict, report).
    A bar only one reading has is taken as it is. A bar several readings have: parts only one of them has are joined;
    a part two readings both have must agree, else it's a conflict - `prefer` (the 0-based index of the reading to
    trust, 'first' or 'last') settles conflicts; without it the first reading's version is kept and the conflict is
    reported. Header fields (meter, key, tempo) must agree too."""
    docs = []
    for r in readings or []:
        d, path = load_score_data(r)
        if not isinstance(d, dict):
            raise ValueError("each reading must be a score-data object")
        docs.append(d)
    if not docs:
        raise ValueError("give at least one reading")
    if prefer is None:
        pref = None
    elif prefer == "first":
        pref = 0
    elif prefer == "last":
        pref = len(docs) - 1
    else:
        pref = int(prefer)
        if not 0 <= pref < len(docs):
            raise ValueError(f"prefer={prefer}: there are {len(docs)} readings (0-based)")
    head, meta = {"format": SCORE_FORMAT}, []
    for k in ("title", "composer", "meter", "key", "tempo"):
        vals = [(i, d.get(k)) for i, d in enumerate(docs) if d.get(k) not in (None, "")]
        if not vals:
            continue
        head[k] = vals[0][1]
        other = [(i, v) for i, v in vals if str(v) != str(vals[0][1])]
        if other and k in ("meter", "key", "tempo"):
            meta.append(f"{k}: reading {vals[0][0]} says {vals[0][1]!r}, " +
                        ", ".join(f"reading {i} says {v!r}" for i, v in other))
            if pref is not None and docs[pref].get(k) not in (None, ""):
                head[k] = docs[pref][k]
    pinfo = {}
    for d in docs:
        if isinstance(d.get("parts"), dict):
            for p, v in d["parts"].items():
                pinfo.setdefault(p, v)
    if pinfo:
        head["parts"] = pinfo
    per = [_sd_bars_by_num(d) for d in docs]
    merged, src, conflicts, overlaps = {}, {}, [], []
    for i, bars in enumerate(per):
        for n, b in sorted(bars.items()):
            if n not in merged:
                merged[n], src[n] = _sd_copy(b), [i]
                continue
            overlaps.append(n)
            cur = merged[n]
            na, nb = _sd_norm_bar(cur), _sd_norm_bar(b)
            clash = [p for p in sorted(set(na) & set(nb)) if na[p] != nb[p]]
            hdr = [k for k in ("meter", "beats", "key") if cur.get(k) is not None and b.get(k) is not None and
                   str(cur[k]) != str(b[k])]
            if clash or hdr:
                c = {"bar": n, "readings": [src[n][0], i]}
                if clash:
                    c["parts"] = diff_bar_parts({p: na[p] for p in clash}, {p: nb[p] for p in clash})
                if hdr:
                    c["header"] = [f"{k} {cur[k]!r} vs {b[k]!r}" for k in hdr]
                conflicts.append(c)
                if pref == i:
                    bp = _sd_bar_parts(b)
                    for p in clash:
                        _sd_put_part(cur, p, bp[p])
                    for k in hdr:
                        cur[k] = b[k]
            src[n].append(i)
            for p, ents in _sd_bar_parts(b).items():          # parts only this reading has: joined
                if p not in na:
                    _sd_put_part(cur, p, ents)
            for k, v in b.items():                           # metadata the earlier reading lacks
                if k in _SD_RESERVED and k != "parts" and cur.get(k) in (None, "", []):
                    cur[k] = _sd_copy(v)
    nums = sorted(merged)
    missing = [n for n in range(nums[0], nums[-1] + 1) if n not in merged] if nums else []
    parts_by = [sorted({p for b in bars.values() for p in _sd_bar_parts(b)}) for bars in per]
    out = dict(head)
    out["bars"] = {str(n): merged[n] for n in nums}
    rep = {"readings": len(docs), "bars": len(nums), "range": f"{nums[0]}-{nums[-1]}" if nums else None,
           "bars_by_reading": [_sd_range_txt(b) if b else None for b in per], "conflicts": conflicts,
           "parts": sorted({p for ps in parts_by for p in ps})}
    if overlaps:
        rep["overlap_bars"] = _sd_range_txt(sorted(set(overlaps)))
    if missing:
        rep["missing"] = _sd_range_txt(missing)
    if meta:
        rep["meta"] = meta
    if len(per) > 1:
        odd = {str(i): [p for p in ps if sum(p in x for x in parts_by) == 1] for i, ps in enumerate(parts_by)}
        odd = {k: v for k, v in odd.items() if v}
        if odd:
            rep["parts_in_one_reading_only"] = odd
    if conflicts:
        rep["resolved_by"] = f"reading {pref}" if pref is not None else "none: the first reading's version was kept"
    return out, rep


# ============================================================================= summary / inline / MIDI
def main_meter(sc):
    """The meter covering the most written time (FL projects have one)."""
    tot = {}
    for g in sc.measures:
        if g["meter"]:
            tot[g["meter"]] = tot.get(g["meter"], 0) + g["len"]
    return max(tot, key=tot.get) if tot else (4, 4)


def summary(sc):
    vs = {}
    for v in sc.voices:
        ps = [n["pitch"] for n in sc.notes if n["voice"] == v.name]
        vs[v.name] = {"notes": len(ps), "range": f"{pitch_name(min(ps))}-{pitch_name(max(ps))}" if ps else None,
                      "clef": v.clef, "staff": v.staff}
        if v.program is not None:
            vs[v.name]["program"] = v.program
    meters = []
    for g in sc.measures:
        m = f"{g['meter'][0]}/{g['meter'][1]}" if g["meter"] else "free"
        if not meters or meters[-1][1] != m:
            meters.append([g["num"], m])
    keys = []
    for tick, name in sc.keys:
        b = _bar_at_tick(sc, tick)
        if not keys or keys[-1][1] != name:
            keys.append([b, name])
    tempos = [[_bar_at_tick(sc, t), b] for t, b in sc.tempos]
    out = {"title": sc.title, "bars": len(sc.measures), "bars_played": len(sc.order), "meters": meters, "keys": keys,
           "tempos": tempos or None, "pickup": bool(sc.measures and sc.measures[0]["implicit"] and len(sc.measures) > 1),
           "voices": vs, "notes": len(sc.notes), "seconds": round(_seconds(sc, sc.total_ticks), 2)}
    if sc.tempo_assumed:
        out["tempo_assumed"] = 120
    if len(sc.order) != len(sc.measures) or sc.order != list(range(len(sc.measures))):
        out["play_order"] = _compress_order([sc.measures[i]["num"] for i in sc.order])
    return out


def _compress_order(nums):
    out, k = [], 0
    while k < len(nums):
        j = k
        while j + 1 < len(nums) and isinstance(nums[j], int) and isinstance(nums[j + 1], int) and \
                nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(f"{nums[k]}" if j == k else f"{nums[k]}-{nums[j]}")
        k = j + 1
    return " ".join(out)


def _bar_at_tick(sc, tick):
    b = None
    for g in sc.measures:
        if g["start_tick"] <= tick:
            b = g["num"]
    return b


def _seconds(sc, tick):
    s, prev_t, bpm = 0.0, 0, (sc.tempo_map[0][1] if sc.tempo_map else 120.0)
    for tt, b in sc.tempo_map[1:]:
        if tt >= tick:
            break
        s += (tt - prev_t) / sc.ppq * 60 / bpm
        prev_t, bpm = tt, b
    return s + (tick - prev_t) / sc.ppq * 60 / bpm


def to_inline(sc, pickup="pad", fl_meter=None):
    """Per-voice fl-headless inline notes [[bar, len_bars, pitch, vel], ...] + a summary."""
    m = tuple(fl_meter) if fl_meter else main_meter(sc)
    bar_t = Fr(4 * m[0], m[1]) * sc.ppq
    off = 0
    if pickup == "pad" and sc.measures and sc.measures[0]["implicit"] and len(sc.measures) > 1 and sc.order and \
            sc.order[0] == 0:
        off = int(bar_t - sc.measures[0]["len_ticks"]) % int(bar_t)
    voices = {v.name: [] for v in sc.voices}
    for n in sc.notes:
        bar = 1 + Fr(n["start_tick"] + off) / bar_t
        ln = Fr(n["len_ticks"]) / bar_t
        voices[n["voice"]].append([_num(bar), _num(ln), n["pitch"], n["vel"]])
    s = summary(sc)
    s["fl_meter"] = f"{m[0]}/{m[1]}"
    s["fl_bars"] = math.ceil((sc.total_ticks + off) / bar_t) if sc.total_ticks else 0
    if off:
        s["pickup_offset_bars"] = _num(Fr(off) / bar_t)
    if any(g["meter"] and g["meter"] != m for g in sc.measures):
        s["note"] = f"the score changes meter; FL's grid is {m[0]}/{m[1]}, so later bar numbers differ from the score's"
    s["tempo_map"] = [[_num(1 + Fr(t + off) / bar_t), b] for t, b in sc.tempo_map]
    return {"voices": voices, "summary": s}


def _num(x):
    f = float(x)
    r = round(f, 6)
    return int(r) if r == int(r) else r


def _wvlq(v):
    if v < 0:                                                     # a negative delta never ends the loop below
        raise ValueError(f"MIDI delta time {v} is negative (a note before the start of the song)")
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    return bytes(reversed(out))


def _track(evs, name):
    body = b"\x00\xff\x03" + _wvlq(len(name.encode())) + name.encode()
    last = 0
    for tick, _, ev in sorted(evs, key=lambda e: (e[0], e[1])):
        body += _wvlq(tick - last) + ev
        last = tick
    body += b"\x00\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + body


def to_midi(sc, path=None):
    """Format-1 MIDI: conductor track (tempo, meter, key) + one track per voice."""
    cond = []
    for t, bpm in sc.tempo_map:
        us = max(1, min(0xFFFFFF, int(round(60_000_000 / bpm))))  # MIDI's 3-byte tempo (Q:1/4=1 overflowed)
        cond.append((t, 0, b"\xff\x51\x03" + us.to_bytes(3, "big")))
    for t, nn, dd in sc.meter_map:
        cond.append((t, 1, b"\xff\x58\x04" + bytes([nn, max(0, int(math.log2(dd))), 24, 8])))
    for t, fifths, mode in sc.key_map:
        sf = max(-7, min(7, fifths))
        cond.append((t, 2, b"\xff\x59\x02" + struct.pack("b", sf) + bytes([1 if mode == -3 else 0])))
    tracks = [_track(cond, sc.title or "score")]
    ch = 0
    count = 0
    names = set()
    for v in sc.voices:
        if v.name in names:                       # voices sharing a name (score data's layers) = one track
            continue
        names.add(v.name)
        if ch == 9:
            ch += 1
        c = ch % 16
        ch += 1
        evs = [(0, 0, bytes([0xC0 | c, (v.program or 0) & 0x7F]))]
        for n in sc.notes:
            if n["voice"] != v.name:
                continue
            evs.append((n["start_tick"], 2, bytes([0x90 | c, n["pitch"] & 0x7F, max(1, min(127, n["vel"]))])))
            evs.append((n["start_tick"] + n["len_ticks"], 1, bytes([0x80 | c, n["pitch"] & 0x7F, 0])))
            count += 1
        tracks.append(_track(evs, v.name))
    data = struct.pack(">4sIHHH", b"MThd", 6, 1, len(tracks), sc.ppq) + b"".join(tracks)
    if path is None:
        return {"bytes": data, "tracks": len(tracks), "notes": count, "ppq": sc.ppq}
    with open(path, "wb") as f:
        f.write(data)
    return {"path": path, "tracks": len(tracks), "notes": count, "ppq": sc.ppq}


# ============================================================================= diff
def _bar_prints(sc):
    out = []
    for i, g in enumerate(sc.measures):
        ns = []
        for v in sc.voices:
            for s in v.sound[i]:
                ns.append((s["pos"], s["pitch"], s.get("wdur", s["dur"]), s["grace"]))
        out.append((g["num"], g["meter"], tuple(sorted(ns))))
    return out


def diff(a, b, max_bars=30):
    """Bar-by-bar differences between two Scores (written bars, all voices; durations as written)."""
    A, B = _bar_prints(a), _bar_prints(b)
    fa = [x[2] for x in A]
    fb = [x[2] for x in B]
    sm = difflib.SequenceMatcher(None, fa, fb, autojunk=False)
    bars = []
    differing = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        if op == "replace" and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                differing += 1
                bars.append(_bar_diff(A[i1 + k], B[j1 + k]))
        else:
            if i2 > i1:
                differing += i2 - i1
                bars.append({"bar": A[i1][0] if i2 - i1 == 1 else f"{A[i1][0]}-{A[i2 - 1][0]}",
                             "only_in": "a", "bars": i2 - i1,
                             "notes": _ntxt(A[i1][2], A[i1][1])[:8]})
            if j2 > j1:
                differing += j2 - j1
                bars.append({"bar_b": B[j1][0] if j2 - j1 == 1 else f"{B[j1][0]}-{B[j2 - 1][0]}",
                             "only_in": "b", "bars": j2 - j1,
                             "notes": _ntxt(B[j1][2], B[j1][1])[:8]})
    meta = []
    ma = [(m[1], m[2]) for m in a.meters]
    mb = [(m[1], m[2]) for m in b.meters]
    if ma != mb:
        meta.append(f"meters differ: {ma} vs {mb}")
    ka = [k for _, k in a.keys]
    kb = [k for _, k in b.keys]
    if ka != kb:
        meta.append(f"keys differ: {ka} vs {kb}")
    if len(a.voices) != len(b.voices):
        meta.append(f"voices: {len(a.voices)} vs {len(b.voices)}")
    out = {"same": not bars and not meta, "bars_a": len(A), "bars_b": len(B), "differing": differing,
           "bars": bars[:max_bars], "meta": meta}
    if len(bars) > max_bars:
        out["more"] = len(bars) - max_bars
    return out


def _beat(pos, meter):
    den = meter[1] if meter else 4
    b = 1 + Fr(pos) * den / 4
    return str(b.numerator) if b.denominator == 1 else f"{float(b):g}"


def _ntxt(ns, meter):
    return [f"{_beat(p, meter)}:{pitch_name(k)}{'g' if g else ''}/{_durtxt(d)}" for p, k, d, g in ns]


def _durtxt(d):
    d = Fr(d)
    return str(d.numerator) if d.denominator == 1 else f"{d.numerator}/{d.denominator}"


def _bar_diff(x, y):
    na, ma, A = x
    nb, mb, B = y
    ca = list(A)
    cb = list(B)
    for t in A:
        if t in cb:
            cb.remove(t)
            ca.remove(t)
    changed = []
    for t in list(ca):
        m = next((u for u in cb if u[0] == t[0] and u[3] == t[3]), None)
        if m is not None:
            if m[1] != t[1]:
                changed.append(f"{_beat(t[0], ma)}: {pitch_name(t[1])}->{pitch_name(m[1])}" +
                               (" (octave)" if abs(m[1] - t[1]) == 12 else ""))
            else:
                changed.append(f"{_beat(t[0], ma)}: {pitch_name(t[1])} length {_durtxt(t[2])}->{_durtxt(m[2])}")
            ca.remove(t)
            cb.remove(m)
    d = {"bar": na}
    if nb != na:
        d["bar_b"] = nb
    if ma != mb:
        d["meter"] = f"{ma} vs {mb}"
    if changed:
        d["changed"] = changed[:8]
    if ca:
        d["only_a"] = _ntxt(ca, ma)[:8]
    if cb:
        d["only_b"] = _ntxt(cb, ma)[:8]
    return d
