"""fl-headless composing: genre knowledge AS DATA (data/genres/*.json, data/adjectives.json) plus deterministic
generators that write real FL content (drums, chords, bass/808 with glides, melody, counter-melody, re-voicing,
style layers, song form, plain-English adjustments). Every write is ONE flp_edit batch (FL saves once, reloads once,
all or nothing, automatic backup = reversible with flp_versions/flp_restore).
Extension contract: see flh_store.

Genre file (data/genres/<name>.json, format "flh-genre/1"; users can add/override files in
<fl-headless state folder>/genres/; the first file wins per name, user folder first):
  name, aliases[], about
  tempo {min, max, default, feel: half-time | straight | four-on-the-floor | two-step | dembow | free}
  meter (the genre's usual beats per bar; the composer writes the PROJECT's meter, see Meters below),
  swing (0.5 straight .. 0.67 triplet, 16th grid),
  humanize {timing_ms, vel}, loop_bars
  keys {modes {mode: weight}, roots [..], default "C# minor"}
  drums {vel {X, x, o, g}, parts {<part>: {patterns [{steps, w, grid?}], maybe, ghosts {at, chance},
         rolls {chance, max_per_bar, at, len, rates {"1/32"|"1/16t"|"1/32t"|"1/8t"|"1/64": w}, ramp [from, to],
         pitch_up_chance}, bar_alts [{chance, grid, steps, not_first}]}},
         fill {every, parts {<part>: 16-char mask, "_" = keep}, roll {part, at, len, rate, ramp},
               turn {<part>: mask} (the 4-bar phrase marker inside an 8-bar fill cycle; default an open hat on the
               last 8th)}, (extra kicks / ghosts are drawn once per 2-bar cell so the groove settles)
         variation {alt_pattern_chance, kick_maybe_scale}}
     Step strings: one char per step (16 per bar unless grid says 12 = 8th triplets, 24 = 16th triplets); a string
     may hold several bars. X accent, x strong, o normal, g ghost, . rest. "maybe": digit d = d*10 % chance of an
     extra hit there. Parts: kick snare clap hat openhat perc ride crash tom (crash plays at the block start and
     after fills only). An open hat chokes the closed hat on the same step.
  bass {type: 808 | bass | sub | upright | drone | log (amapiano log drum), register [lo, hi],
        lock: kick (default: a pattern bass moves onto nearby kicks and fills kick hits in its rests,
        lock_kick {snap: steps, fill: chance}) | offbeat (house pumps between the kicks) | walk | free,
        rhythm: kick | pattern | chord | walk, patterns [{steps, w}] (x onset, - hold, . rest),
        follow: root | root_fifth | octave (root / octave pulse) | walk,
        length: until_next | as_written | steps:N, gap_steps, vel [lo, hi], octave_jump, approach, octave_double,
        glide {chance, len_steps [..], where: late | any, targets {octave_up, octave_down, next_root, fifth,
        third (the chord's 3rd; minor_third = same): w}}, extra_hits {chance, at}}
     Glide targets are chord tones of the chord under the slide (fifth/third) and stay within an octave of the register.
     Glides are written as FL slide notes (the held 808 note keeps sounding and a slide note bends it; flag 0x08 in
     the note record) or, with glide="overlap", as overlapping notes for a mono/Porta channel.
  harmony {progressions [{rn [..], mode major|minor|any, bars (per chord), w}],
           voicing {style: triad | seventh | extended | rootless | shell | power | open, register [lo, hi],
                    extensions {"7": chance, "9": chance}}, rhythm {patterns [{steps, w}]}, vel, strum_ms}
     Roman numerals: case = quality (V major, v minor), suffixes 7 maj7 9 maj9 11 13 6 add9 sus2 sus4 5,
     ° / o / dim = diminished, ø = half-diminished, + = augmented; b/# before the numeral = borrowed/chromatic root.
     Roots count from the major scale in major-family keys and the natural minor scale in minor-family keys
     (so in a minor key VI, III, VII are the natural b6, b3, b7; in a major key bVII, bVI, iv are borrowed).
     Chord names (Am7, F, Cmaj7, D/F#) work everywhere a numeral does.
  melody {scale: key | pentatonic | <scale name>, range [lo, hi], rhythms [{steps, w, grid? (12 / 24 = triplet
          cells, 4/4 only)}] (1-bar or 2-bar rhythm cells), step_prob, max_leap, repeat_note, vel,
          length: until_next | as_written | short, max_len_steps, pickup (chance of a lead-in into the next phrase),
          hook {plan [letters per 2-bar phrase: A = the hook (motif + its answer), S = A sequenced to the chords,
                R = A's rhythm with new notes, B = a contrasting idea, C = A with a cadence (the last phrase is
                always one)], answer: same (the call's contour again) | other (call and response: it turns the
                other way), breath (16ths of rest after a phrase lands), land [16th positions in the answer bar the
                phrase may land on; [] = a riff that doesn't stop], shapes {arch | rise | fall | wave | hold: w},
                leap (chance of the motif's characteristic leap), span (semitones the hook stays in, 12),
                tessitura, phrase_bars (2), candidates (6: seeded melodies scored by fl_musicality, best kept)}
          (old files: form ["A", "A'", "B"] is read as a plan)}
  counter {mode: fills | guide, register: below | above, density}
  form {default [[section, bars], ..], <alt name>: [..]}
  sections {<name>: {energy 0..1, layers [drums, bass, chords, melody, counter, ...], drums: full | light |
            sparse | half | build | none, melody: full | call (each phrase's motif bar + its landing note) |
            sparse (its on-beat notes: intro / outro teaser) | none (default by the section's kind: hook full,
            verse / pre call, intro / outro / breakdown sparse), harmony: contrast (a bridge on another of the
            genre's progressions: its own chords, bass and a new melody), role: hook | verse (what an A / B
            section is, for the musicality check)}}
  transitions {into {<section>: [kind, ...]}, after {<section>: [...]} (where it ends into the next), end [...]
               (the song's last downbeat)}: kinds fill | dropout | stop (drums) and the audio FX riser | downlifter |
               impact | reverse_crash | sub_drop | tape_stop | noise_sweep ('riser:16' = 16 beats; made by flh_fx)
  ear_candy {filters [intro, build, outro] (Fruity Filter automation), build_beats, level_db, intensity}
  roles {<role or drum part>: {hint (for the sound catalog), family, name}}
  mix {loudness_lufs, notes [..], sidechain {trigger, target, depth_db}}   (hints for the mix engineer)

Meters: a bar holds num*16/den 16th steps (3/4 = 12, 6/8 = 12, 2/4 = 8, 5/4 = 20, 7/8 = 14); 4/4 and 2/2 use the
  genre data as written. In other meters: drums use the genre's drums.meters["3/4"] = {parts, fill} (same grammar,
  one bar = the meter's steps) or the meter's generic feel from data/meters.json (the result says which); comping,
  bass and melody rhythms use harmony.rhythm.meters["3/4"].patterns / bass.meters[..].patterns /
  melody.meters[..].rhythms when the genre has them, else their 4/4 bars cut / continued to the bar; strong beats,
  beats and the chord segments read from the song come from data/meters.json (3/4: strong 1; 6/8: 1 and 4 ...).
  The generators read the meter from in_meter(ts) (thread-local; flp_compose / compose_around set it).

Musicality (fl_musicality): every beat / part flp_compose writes is scored on its notes - hook (a motif that
  repeats and develops, singable steps, space), fit (chord tones on strong beats, no semitone rubs), groove (bass on
  the kick, roots on changes, drums that settle), form (cadences, AABA/ABAB phrases, fills at phrase ends, sections
  that build) - and results carry 'musicality' {score, hook, fit, groove, form, weak}; flp_musicality scores a
  project's own music (per section + the song). Craft rules, not taste: a human still listens.

Generators (pure, deterministic for a seed; positions in 16th steps from the block start):
  drums(genre, bars, variation, seed, level)   harmony(key, genre, bars, progression)   chord_part(chords, ...)
  bassline(chords, genre, kick_steps)          melody(key, chords, genre, bars, motif, rng, candidates, accomp)
  compose_beat(genre, key, bars, seed)         (the whole beat as flp_compose writes it, with its musicality)
  counter_melody(mel, chords, key, genre)      revoice(notes, key, style, register)    layer(role, chords, ...)

Guards (applied by flp_compose / compose_around, musically: what an arranger does): mud_guard (keys notes under their
low interval limit over the bass move up an octave), melody_guard (keys a semitone / maj7 against the lead, or above
it, move down or are left out), clear_rubs (held layers clear of the other parts), _fit_line (moving lines step to a
chord-scale note that doesn't grind against an accompaniment that must stay as is).

compose_around(idea, what, genre, project, **options) (ns["compose_around"], used by flh_transcribe's
capture_and_build): parts around a played / hummed / transcribed idea - key and chords inferred when missing
(harmonize: Viterbi over the key's and the genre's chords), genre guessed from tempo / swing / mode when not given
(guess_genre, says so), the idea's swing (16ths or 8ths) and phrase length kept, the idea itself never changed
(written to its own 'Idea' pattern, or found already in the project), all parts in ONE batch, an in-memory clash
check of idea + parts in the result. No project: output_mid=, or the notes inline.

Realism: parts on strings / choir / pad / brass / woodwind / bells channels (family from the channel's name, preset,
the layer role or the genre's sound hint; data/perform/layer_families.json) are played like a person by flh_realism
(ns["realism_apply"]: expression, per-family timing, breaths, legato) instead of the genre's humanize - by default for
what=layer/counter, for every part with realism=True (flh_new_song passes it for orchestral / cinematic genres), never
with realism=False. A clash guard (flh_perform.realism_run) gives a note its written timing back when the new timing
would rub against another part; the song-level clash check still runs on what was written.

Tools: flp_compose (drums | chords | bass | melody | counter | voicing | layer | beat | pick), flp_arrange_song
(song form, transitions, builds, extend a section), flp_adjust (plain-English words -> note ops + mix intents),
flp_compose_write (the batch writer the others use: notes into a named pattern, slide flags, placement, in-place
transforms read from the file at write time), flh_genres (list/inspect genre data), flp_musicality (unlisted: scores
the music a project already has, per section and for the song).
"""
import copy
import functools
import glob
import json
import math
import os
import random
import re
import statistics
import struct
import sys
import threading
import time
import zlib

import fl_music
import fl_musicality
import flh_perform
import flh_score

H = {}                                                   # the server namespace, set by register()
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SPB = 16                                                 # 16th steps per 4/4 bar
DEFAULT_VEL = {"X": 118, "x": 100, "o": 80, "g": 44}
RATE_STEPS = {"1/64": 0.25, "1/32t": 1 / 3, "1/32": 0.5, "1/16t": 2 / 3, "1/16": 1.0, "1/8t": 4 / 3, "1/8": 2.0}
DRUM_PARTS = ("kick", "snare", "clap", "hat", "openhat", "perc", "ride", "crash", "tom", "rim", "shaker")
GM_DRUMS = {"kick": 36, "snare": 38, "clap": 39, "hat": 42, "openhat": 46, "perc": 37, "rim": 37, "ride": 51,
            "crash": 49, "tom": 45, "shaker": 70}
PART_FALLBACK = {"snare": ["clap", "rim"], "clap": ["snare"], "rim": ["perc", "snare"], "perc": ["rim", "shaker"],
                 "ride": ["hat"], "hat": ["ride", "shaker"], "shaker": ["perc", "hat"], "tom": ["perc"],
                 "openhat": [], "crash": [], "kick": []}
SLIDE_FLAG = 0x08                                        # FL note flags bit 3 = slide note (PC-VERIFY in checklist)
NOTE_FLAGS = 16384                                       # what FL writes for a plain note


# ============================================================================= data
_CACHE = {}


def _data_dirs(sub):
    dirs = []
    try:
        st = H.get("STATE")
        if st:
            dirs.append(os.path.join(os.path.dirname(st), sub))   # user / community overrides first
    except Exception:
        pass
    dirs.append(os.path.join(DATA, sub))
    return dirs


def _norm(s):
    return re.sub(r"[^a-z0-9&]", "", str(s or "").lower())


def genres():
    """{name: genre dict} from data/genres (user folder first; cached by file mtimes)."""
    files = []
    for d in _data_dirs("genres"):
        files += sorted(glob.glob(os.path.join(d, "*.json")))
    sig = tuple((f, os.path.getmtime(f)) for f in files)
    if _CACHE.get("genres_sig") == sig:
        return _CACHE["genres"]
    out = {}
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                g = json.load(fh)
        except Exception as e:
            out.setdefault("_errors", {})[os.path.basename(f)] = str(e)
            continue
        name = g.get("name") or os.path.splitext(os.path.basename(f))[0]
        if name not in out:
            g["_file"] = f
            out[name] = g
    _CACHE["genres"], _CACHE["genres_sig"] = out, sig
    return out


def genre_data(name):
    """A genre by name or alias ('drill' -> drill-uk, 'boom bap' -> boombap)."""
    if isinstance(name, dict):
        return name
    gs = {k: v for k, v in genres().items() if not k.startswith("_")}
    n = _norm(name)
    for k, g in gs.items():
        if _norm(k) == n:
            return g
    for k, g in gs.items():
        if any(_norm(a) == n for a in g.get("aliases", [])):
            return g
    best = None                                          # 'dark trap beat' -> trap; the longest match wins
    for k, g in gs.items():                              # ('neo soul beat' -> rnb's 'neo soul', not 'soul')
        for a in [k] + list(g.get("aliases", [])):
            na = _norm(a)
            if n and na and na in n and (best is None or len(na) > best[0]):
                best = (len(na), g)
    if best:
        return best[1]
    raise ValueError(f"unknown genre '{name}' (known: {', '.join(sorted(gs))}; add one as data/genres/<name>.json)")


def adjectives():
    for d in _data_dirs(""):
        p = os.path.join(d, "adjectives.json")
        if os.path.isfile(p):
            m = os.path.getmtime(p)
            if _CACHE.get("adj_sig") != (p, m):
                with open(p, encoding="utf-8") as fh:
                    _CACHE["adj"], _CACHE["adj_sig"] = json.load(fh), (p, m)
            return _CACHE["adj"]
    raise ValueError("data/adjectives.json is missing")


# ============================================================================= meters
# Generators place notes in 16th steps from the block start. A bar holds _spb() of them: 16 in 4/4 (the genre data's
# own grid), num*16/den in any other meter (3/4 = 12, 6/8 = 12, 2/4 = 8, 5/4 = 20, 7/8 = 14). The meter of a call is
# thread-local (in_meter / _meter_scope); without one everything is 4/4 and the 4/4 code paths are the original ones.
_MT = threading.local()
LEGACY_METERS = ("4/4", "2/2")                           # 16 steps a bar: the genre data's grid as written


def meters_data():
    """data/meters.json (user folder first): beats, strong beats, chord segments, feel and generic drums per meter."""
    for d in _data_dirs(""):
        p = os.path.join(d, "meters.json")
        if os.path.isfile(p):
            m = os.path.getmtime(p)
            if _CACHE.get("meters_sig") != (p, m):
                try:
                    with open(p, encoding="utf-8") as fh:
                        _CACHE["meters"] = json.load(fh)
                except Exception:
                    _CACHE["meters"] = {}
                _CACHE["meters_sig"] = (p, m)
            return _CACHE["meters"]
    return {}


def _parse_ts(ts):
    """'3/4' | (3, 4) | {num, den} | quarter beats per bar (3 -> 3/4, 3.5 -> 7/8) -> (num, den)."""
    if isinstance(ts, dict):
        return _parse_ts((ts.get("num"), ts.get("den")) if ts.get("num") else ts.get("ts", "4/4"))
    if isinstance(ts, (list, tuple)):
        num, den = int(ts[0]), int(ts[1])
    elif isinstance(ts, str) and "/" in ts:
        a, b = ts.split("/", 1)
        num, den = int(a.strip()), int(b.strip())
    else:
        q = float(ts or 4)
        num, den = (int(q), 4) if q == int(q) else (int(round(q * 2)), 8)
    if not (1 <= num <= 32) or den not in (1, 2, 4, 8, 16) or num * 16 % den:
        raise ValueError(f"time signature {num}/{den} can't be composed in (denominators 2, 4, 8, 16)")
    return num, den


def meter_info(ts="4/4"):
    """Facts about a meter: ts, num, den, spb (16ths per bar), qbar (quarter beats), beats / strong / seg (16th
    positions in the bar), pulse (steps per beat), feel, drums (generic grammar or None), legacy (4/4-style)."""
    num, den = _parse_ts(ts)
    key = f"{num}/{den}"
    hit = _CACHE.setdefault("meter_info", {}).get(key)
    if hit and hit.get("_sig") == _CACHE.get("meters_sig"):
        return hit
    spb = num * 16 // den
    d = (meters_data().get("meters") or {}).get(key) or {}
    beats = [int(x) for x in d.get("beats") or []]
    if not beats:
        if den == 8 and num % 3 == 0 and num >= 6:         # compound: dotted-quarter beats
            beats = list(range(0, spb, 6))
        elif den == 16:
            beats = list(range(0, spb, 4)) or [0]
        elif den == 8:                                     # odd 8ths: groups of 2, the last group of 3
            n8 = num
            grp = [2] * (n8 // 2 - (n8 % 2)) + ([3] if n8 % 2 else []) if n8 >= 2 else [n8]
            beats, t = [], 0
            for gl in grp:
                beats.append(t)
                t += gl * 2
        else:
            beats = list(range(0, spb, 16 // den))
    strong = [int(x) for x in d.get("strong") or []] or (
        [0, beats[len(beats) // 2]] if len(beats) >= 4 and len(beats) % 2 == 0 else [0])
    seg = [int(x) for x in d.get("chord_segments") or []] or strong
    gaps = {b - a for a, b in zip(beats, beats[1:] + [spb])}
    m = {"ts": key, "num": num, "den": den, "spb": spb, "qbar": num * 4.0 / den, "beats": beats, "strong": strong,
         "seg": sorted(set(seg)), "pulse": gaps.pop() if len(gaps) == 1 else 4, "feel": d.get("feel"),
         "drums": d.get("drums"), "legacy": key in LEGACY_METERS, "_sig": _CACHE.get("meters_sig")}
    _CACHE["meter_info"][key] = m
    return m


M44 = {"ts": "4/4", "num": 4, "den": 4, "spb": 16, "qbar": 4.0, "beats": [0, 4, 8, 12], "strong": [0, 8], "seg": [0, 8],
       "pulse": 4, "feel": None, "drums": None, "legacy": True}


def _meter():
    return getattr(_MT, "m", None) or M44


class in_meter:
    """with in_meter('3/4'): the generators write 3/4 bars (thread-local; nested calls restore the outer meter)."""
    def __init__(self, ts):
        self.m = ts if isinstance(ts, dict) and "spb" in ts else meter_info(ts)

    def __enter__(self):
        self.prev = getattr(_MT, "m", None)
        _MT.m = self.m
        return self.m

    def __exit__(self, *exc):
        _MT.m = self.prev
        return False


def _meter_scope(fn):
    """Entry points that set the meter of their project / idea with _set_meter: restored when they return."""
    @functools.wraps(fn)
    def run(*a, **kw):
        prev = getattr(_MT, "m", None)
        try:
            return fn(*a, **kw)
        finally:
            _MT.m = prev
    return run


def _set_meter(ts):
    _MT.m = meter_info(ts)
    return _MT.m


def _spb():
    return _meter()["spb"]


def _at_pos(s, offs, spb, tol=1e-6):
    r = s % spb
    return any(abs(r - o) < tol for o in offs)


def _strong(s):
    """On a strong beat (4/4: beats 1 and 3)."""
    m = _meter()
    return s % 8 == 0 if m["legacy"] else _at_pos(s, m["strong"], m["spb"])


def _on_beat(s):
    """On a beat of the meter (4/4: every quarter; 6/8: every dotted quarter)."""
    m = _meter()
    return s % 4 == 0 if m["legacy"] else _at_pos(s, m["beats"], m["spb"])


def _near_strong(x, tol=0.45):
    """A (possibly humanized) position within tol of a strong beat."""
    m = _meter()
    if m["legacy"]:
        return _near(x, 8, tol)
    r = x % m["spb"]
    return any(min(abs(r - o), m["spb"] - abs(r - o)) <= tol for o in m["strong"])


def _fit_bar(bar, n):
    """A one-bar x/-/. step string (16 chars, 4/4) laid onto a bar of n steps: its quarter-beat cells go onto the
    meter's beats in order (cycling: 3/4 = beats 1-3, 5/4 = 1-4 + 1); a beat longer or shorter than a quarter
    (6/8's dotted quarters, 7/8's last 3 8ths) gets its cell stretched (an onset held, not repeated)."""
    m = _meter()
    beats = m["beats"] if m["spb"] == n else list(range(0, n, 4))
    bar = (bar + "." * 16)[:16]
    out = []
    for i, b0 in enumerate(beats):
        L = (beats[i + 1] if i + 1 < len(beats) else n) - b0
        cell = bar[(i % 4) * 4:(i % 4) * 4 + 4]
        if L == 4:
            out.append(cell)
            continue
        prev, seg = None, ""
        for j in range(L):
            src = min(3, int(j * 4 / L))
            c = cell[src]
            seg += "-" if (c == "x" and src == prev) else c
            prev = src
        out.append(seg)
    return "".join(out)[:n]


def _meter_alt(spec, field):
    """A genre section's own patterns for the current meter (spec['meters'][ts][field]) or None."""
    m = _meter()
    if m["legacy"] or not isinstance(spec, dict):
        return None
    alt = ((spec.get("meters") or {}).get(m["ts"]) or {}).get(field)
    return alt or None


def _pat_bar(steps, b, native=False):
    """Bar b of a looped step pattern in the current meter: 4/4 data bars are fitted (cut / continued) to the bar."""
    m = _meter()
    if m["legacy"]:
        return _bar_slice(steps, 16, b)
    if native:
        return _bar_slice(steps, m["spb"], b)
    return _fit_bar(_bar_slice(steps, 16, b), m["spb"])


def _pat_meter(steps, native=False):
    """A whole looped pattern (one string, several 4/4 bars) in the current meter."""
    m = _meter()
    if m["legacy"] or native:
        return steps
    nb = max(1, len(steps) // 16)
    return "".join(_fit_bar(steps[i * 16:(i + 1) * 16] if len(steps) >= 16 else _fit_bar(steps, 16), m["spb"])
                   for i in range(nb))


# ============================================================================= theory
QUALITY = {"": [0, 4, 7], "m": [0, 3, 7], "dim": [0, 3, 6], "aug": [0, 4, 8], "5": [0, 7], "sus2": [0, 2, 7],
           "sus4": [0, 5, 7], "7": [0, 4, 7, 10], "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "m7b5": [0, 3, 6, 10],
           "dim7": [0, 3, 6, 9], "mmaj7": [0, 3, 7, 11], "6": [0, 4, 7, 9], "m6": [0, 3, 7, 9],
           "add9": [0, 4, 7, 14], "madd9": [0, 3, 7, 14], "9": [0, 4, 7, 10, 14], "maj9": [0, 4, 7, 11, 14],
           "m9": [0, 3, 7, 10, 14], "11": [0, 4, 7, 10, 14, 17], "m11": [0, 3, 7, 10, 14, 17],
           "13": [0, 4, 7, 10, 14, 21], "m13": [0, 3, 7, 10, 14, 21], "maj13": [0, 4, 7, 11, 14, 21],
           "7sus4": [0, 5, 7, 10], "aug7": [0, 4, 8, 10], "7b9": [0, 4, 7, 10, 13], "6/9": [0, 4, 7, 9, 14],
           "m6/9": [0, 3, 7, 9, 14], "7#9": [0, 4, 7, 10, 15]}
_Q_ALIAS = {"maj": "", "M": "", "min": "m", "-": "m", "M7": "maj7", "Maj7": "maj7", "min7": "m7", "-7": "m7",
            "ø": "m7b5", "ø7": "m7b5", "°": "dim", "o": "dim", "°7": "dim7", "o7": "dim7", "+": "aug", "M9": "maj9",
            "min9": "m9", "sus": "sus4", "2": "sus2", "maj7#11": "maj7", "(5)": "5", "69": "6/9", "6add9": "6/9",
            "m69": "m6/9", "7(b9)": "7b9", "7(#9)": "7#9", "add2": "add9"}
IONIAN = [0, 2, 4, 5, 7, 9, 11]
AEOLIAN = [0, 2, 3, 5, 7, 8, 10]
_RN = re.compile(r"^(b|#)?(VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)(°|o|ø|\+|dim|aug)?"
                 r"(maj13|maj9|maj7|m7b5|add9|sus2|sus4|13|11|9|7|6|5)?$")
_CN = re.compile(r"^([A-Ga-g])([#b]?)(m?6/9|[^/]*)(?:/([A-Ga-g][#b]?))?$")
_ROMAN = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}


def key_info(key):
    """'C# minor' -> (root pc, mode, scale intervals, family 'major'|'minor')."""
    root, mode, iv = fl_music.parse_key(key)
    fam = "major" if 4 in iv and mode not in ("minor", "harmonic minor", "melodic minor", "dorian", "phrygian",
                                              "locrian", "minor pentatonic", "blues") else "minor"
    return root, mode, iv, fam


def _pc_name(pc, flats=False):
    sharp = "C C# D D# E F F# G G# A A# B".split()
    flat = "C Db D Eb E F Gb G Ab A Bb B".split()
    return (flat if flats else sharp)[pc % 12]


_MODE_FROM_MAJOR = {"dorian": 2, "phrygian": 4, "lydian": 5, "mixolydian": 7, "locrian": 11}


def _flats_for(key):
    root, mode, iv, fam = key_info(key)
    acc = str(key).strip()[1:2]
    if acc in ("#", "b"):                                 # spelled as asked: 'Db minor' in flats, 'C# minor' in sharps
        return acc == "b"
    if mode in _MODE_FROM_MAJOR:                          # a mode: its parent major's signature (C mixolydian = F)
        major_root = (root - _MODE_FROM_MAJOR[mode]) % 12
    else:
        major_root = root if fam == "major" else (root + 3) % 12   # relative major decides sharps/flats
    return major_root in (5, 10, 3, 8, 1, 6)


def chord_from_token(tok, key):
    """A roman numeral ('bVImaj7', 'iiø', 'V7') or chord name ('Am7', 'D/F#') in key -> chord dict."""
    tok = str(tok).strip()
    root, mode, iv, fam = key_info(key)
    m = _RN.match(tok)
    if m:
        acc, num, deco, suf = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
        deg = _ROMAN[num.lower()]
        ref = IONIAN if fam == "major" else AEOLIAN
        pc = (root + ref[deg] + (-1 if acc == "b" else 1 if acc == "#" else 0)) % 12
        upper = num.isupper()
        if deco in ("ø",):
            q = "m7b5"
        elif deco in ("°", "o", "dim"):
            q = "dim7" if suf == "7" else "dim"
        elif deco in ("+", "aug"):
            q = "aug7" if suf == "7" else "aug"
        elif upper:
            q = suf
        else:
            q = {"": "m", "7": "m7", "9": "m9", "11": "m11", "13": "m13", "6": "m6", "add9": "madd9",
                 "maj7": "mmaj7", "maj9": "m9", "5": "5", "sus2": "sus2", "sus4": "sus4", "m7b5": "m7b5"}.get(suf, "m")
        q = q if q in QUALITY else ""
        return {"rn": tok, "root": pc, "q": q, "iv": list(QUALITY[q]), "bass": None,
                "name": _pc_name(pc, _flats_for(key)) + q}
    m = _CN.match(tok)
    if not m:
        raise ValueError(f"chord '{tok}': use a roman numeral (i, VI, bVII7, iiø) or a chord name (Am7, F, D/F#)")
    pc = (fl_music.LETTER[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12
    q = m.group(3).strip()
    q = _Q_ALIAS.get(q, q)
    if q not in QUALITY:
        raise ValueError(f"chord '{tok}': unknown quality '{m.group(3)}' (known: {', '.join(k or 'maj' for k in QUALITY)})")
    bass = None
    if m.group(4):
        b = m.group(4)
        bass = (fl_music.LETTER[b[0].upper()] + {"#": 1, "b": -1, "": 0}[b[1:2]]) % 12
    return {"rn": tok, "root": pc, "q": q, "iv": list(QUALITY[q]), "bass": bass, "name": tok}


def parse_progression(prog, key):
    """'i VI III VII', 'Am F C G', ['i:2', 'VI:2'] -> [(chord, bars or None)]."""
    toks = prog if isinstance(prog, (list, tuple)) else re.split(r"[\s,|]+", str(prog).strip())
    out = []
    for t in toks:
        if not str(t).strip():
            continue
        t = str(t).strip()
        bars = None
        if ":" in t:
            t, b = t.rsplit(":", 1)
            bars = float(b)
        out.append((chord_from_token(t, key), bars))
    if not out:
        raise ValueError("empty progression")
    return out


def scale_pcs(key, which="key"):
    root, mode, iv, fam = key_info(key)
    which = which or "key"
    if which == "key":
        ivs = iv
    elif which == "pentatonic":
        ivs = fl_music.SCALES["major pentatonic" if fam == "major" else "minor pentatonic"]
    elif which in ("harmonic minor", "melodic minor") and fam == "major":
        ivs = iv
    else:
        ivs = fl_music.SCALES.get(which, iv)
    return sorted({(root + i) % 12 for i in ivs})


def chord_pcs(ch):
    return sorted({(ch["root"] + i) % 12 for i in ch["iv"]})


DOMINANT_Q = ("7", "9", "11", "13", "7sus4", "aug7", "dim", "dim7", "m7b5", "7b9", "7#9")


def chord_scale(scale, ch, key_pcs=None, avoid=False):
    """The melody scale bent to the chord: chord tones replace scale notes a semitone away; notes from outside
    the key (harmonic minor's raised 7th...) only where the chord has them (the raised 7th over V).
    avoid=True also drops the chord's avoid notes (for held notes): a scale note a semitone above a chord tone
    (the 4th over a major chord, the b6 over a minor one) and the tritone over the root of a non-dominant chord."""
    ct = chord_pcs(ch)
    pcs = set(scale)
    for t in ct:
        if t not in pcs:
            pcs -= {(t - 1) % 12, (t + 1) % 12}
            pcs.add(t)
    if key_pcs:
        kp = set(key_pcs)
        pcs = {p for p in pcs if p in kp or p in ct}
    if avoid:
        cts = set(ct)
        bad = {p for p in pcs if p not in cts and (p - 1) % 12 in cts}
        if ch.get("q") not in DOMINANT_Q:
            bad |= {(ch["root"] + 6) % 12} - cts
        if len(pcs - bad) >= 3:
            pcs -= bad
    return sorted(pcs)


def _pitches(pcs, lo=0, hi=127):
    s = set(p % 12 for p in pcs)
    return [p for p in range(max(0, lo), min(127, hi) + 1) if p % 12 in s]


def _place(pc, target, lo, hi):
    """The pitch of pitch class pc nearest target inside [lo, hi] (or nearest at all if none fits)."""
    cands = [p for p in range(lo, hi + 1) if p % 12 == pc % 12]
    if not cands:
        cands = [p for p in range(max(0, lo - 12), min(127, hi + 12) + 1) if p % 12 == pc % 12]
    return min(cands, key=lambda p: (abs(p - target), p))


def _fold(p, lo, hi):
    while p < lo:
        p += 12
    while p > hi:
        p -= 12
    return p if p >= lo else p + 12 if p + 12 <= hi else p


def _nearest(pitches, target, direction=0):
    if not pitches:
        return target
    if direction > 0:
        up = [p for p in pitches if p > target]
        if up:
            return up[0]
    elif direction < 0:
        dn = [p for p in pitches if p < target]
        if dn:
            return dn[-1]
    return min(pitches, key=lambda p: (abs(p - target), p))


def _step_in(pitches, cur, k):
    """Move k scale steps from cur inside a sorted pitch list."""
    if not pitches:
        return cur
    i = min(range(len(pitches)), key=lambda j: abs(pitches[j] - cur))
    return pitches[max(0, min(len(pitches) - 1, i + k))]


def _seed(seed, *tags):
    return zlib.crc32(("|".join(str(x) for x in (seed,) + tags)).encode())


def _rng(seed, *tags):
    return random.Random(_seed(seed, *tags))


def _pick(rnd, items, rot=0):
    """Weighted pick (items carry 'w'); rot>0 moves to the next live item (variations differ)."""
    if not items:
        return None, None
    ws = [max(0.0, float(it.get("w", 1))) if isinstance(it, dict) else 1.0 for it in items]
    tot = sum(ws)
    idx = len(items) - 1
    if tot > 0:
        r = rnd.random() * tot
        for i, w in enumerate(ws):
            r -= w
            if r < 0:
                idx = i
                break
    else:
        idx = 0
    if rot:
        live = [i for i, w in enumerate(ws) if w > 0] or list(range(len(items)))
        j = live.index(idx) if idx in live else 0
        idx = live[(j + rot) % len(live)]
    return idx, items[idx]


def _wpick(rnd, weights):
    items = [{"k": k, "w": w} for k, w in weights.items()]
    return _pick(rnd, items)[1]["k"]


def _clampv(v):
    return max(1, min(127, int(round(v))))


# ============================================================================= drums
def _hits(steps, grid=16):
    """Step string -> [(step in 16ths, level char)]."""
    u = 16.0 / grid
    return [(i * u, c) for i, c in enumerate(steps) if c in DEFAULT_VEL]


def _bar_slice(steps, grid, b):
    nb = max(1, len(steps) // grid)
    i = b % nb
    return steps[i * grid:(i + 1) * grid]


def _roll(out, part, bar, at, length, rate, ramp, base, pitch_up, rnd):
    u = RATE_STEPS.get(rate, 0.5)
    group = {"1/16t": 2, "1/8t": 4}.get(rate, 1)             # triplets fill whole groups (3 in two 16ths)
    length = min(SPB - at, int(math.ceil(length / group)) * group) if at + group <= SPB else length
    a, b = bar * SPB + at, bar * SPB + at + length
    out[:] = [h for h in out if not (h["part"] == part and a - 1e-6 <= h["s"] < b - 1e-6)]
    n = max(1, int(length / u + 1e-6))
    r0, r1 = (ramp or [0, 0])[:2]
    for k in range(n):
        f = k / max(1, n - 1)
        out.append({"part": part, "s": round(a + k * u, 4), "l": u, "v": _clampv(base + r0 + (r1 - r0) * f + rnd.randint(-2, 2)),
                    "dk": min(7, k) if pitch_up else 0, "nos": True, "roll": True})


def drums(genre, bars=4, variation=1.0, seed=1, level="full", vary=0, fill=True):
    """Drum hits [{part, s (16th step), l, v, dk (pitch offset), nos (no swing)}] from the genre's drum grammar.
    level: full | light (kick/snare/clap/hat, plain hats) | sparse (hats/perc, a kick per 2 bars) | half | build |
    none. variation scales every random part (maybes, ghosts, rolls, alternate patterns)."""
    g = genre_data(genre)
    if not _meter()["legacy"]:
        return drums_meter(g, bars, variation, seed, level, vary, fill)[0]
    dd = g.get("drums") or {}
    parts = dd.get("parts") or {}
    bars = int(bars)
    var = max(0.0, float(variation))
    lv = dict(DEFAULT_VEL, **(dd.get("vel") or {}))
    rnd = _rng(seed, "drums", g["name"], vary)
    if level == "none" or bars <= 0:
        return []
    if level == "build":
        return _build_drums(parts, bars, lv, rnd)
    if level == "half":
        out = []
        for b in range(bars):
            for st, c, p in ((0, "X", "kick"), (8, "X", "snare" if "snare" in parts else "clap")):
                out.append({"part": p, "s": b * SPB + st, "l": 1, "v": _clampv(lv[c] - 4), "dk": 0, "nos": False})
            for st in range(0, 16, 2):
                out.append({"part": "hat", "s": b * SPB + st, "l": 1, "v": _clampv(lv["o"] - 8), "dk": 0, "nos": False})
        return out
    choice = {}
    for part, spec in parts.items():
        pats = [p for p in spec.get("patterns") or [] if float(p.get("w", 1)) > 0]
        if not pats:
            continue
        ia, A = _pick(rnd, pats, vary)
        rest = [p for i, p in enumerate(pats) if i != ia]
        B = _pick(rnd, rest)[1] if rest else None
        simple = min(pats, key=lambda p: (len(_hits(p["steps"], p.get("grid", 16))) / max(1, len(p["steps"])), pats.index(p)))
        choice[part] = (A, B, simple)
    fs = dd.get("fill") or {}
    every = int(fs.get("every") or 0)
    vcfg = dd.get("variation") or {}
    alt_chance = float(vcfg.get("alt_pattern_chance", 0.25)) * var
    kick_scale = float(vcfg.get("kick_maybe_scale", 1.0))
    alt_block = {k: (k > 0 and rnd.random() < alt_chance) for k in range((bars + 1) // 2)}
    light_parts = {"kick", "snare", "clap", "hat", "ride"}
    sparse_parts = {"kick", "hat", "perc", "ride", "shaker"}
    out = []
    fill_bars = set()
    for b in range(bars):
        is_fill = bool(fill and every and (b + 1) % every == 0 and level in ("full", "light"))   # a verse fills too
        if is_fill:
            fill_bars.add(b)
        # a phrase marker at the 4-bar point of a longer fill cycle: the genre's drums.fill.turn masks, else an
        # open hat on the last 8th (when the kit has one)
        is_turn = bool(fill and level == "full" and every >= 8 and (b + 1) % 4 == 0 and not is_fill)
        turn = (fs.get("turn") or ({"openhat": "______________o_"} if "openhat" in parts else {})) if is_turn else {}
        names = list(choice) + [p for p in (fs.get("parts") or {}) if is_fill and p not in choice] + \
            [p for p in turn if p not in choice and p in parts]
        for part in names:
            if level == "light" and part not in light_parts or level == "sparse" and part not in sparse_parts:
                continue
            spec = parts.get(part, {})
            if part in choice:
                A, B, simple = choice[part]
                pat = simple if level in ("light", "sparse") else (B if alt_block.get(b // 2) and B else A)
                grid = int(pat.get("grid", 16))
                bar_str = _bar_slice(pat["steps"], grid, b)
            else:
                grid, bar_str = 16, "." * 16
            if level == "full" and not is_fill:
                for alt in spec.get("bar_alts") or []:
                    if alt.get("not_first") and b % 4 == 0:
                        continue
                    if rnd.random() < float(alt.get("chance", 0)) * var:
                        grid = int(alt.get("grid", 16))
                        bar_str = _bar_slice(alt["steps"], grid, 0)
                        break
            hits = _hits(bar_str, grid)
            if part == "crash" and not (b == 0 or (b - 1) in fill_bars):
                hits = []
            if level == "sparse" and part == "kick":
                hits = [h for h in hits if h[0] == 0][:1] if b % 2 == 0 else []
            # extra kicks and ghost notes: drawn once per 2-bar cell and repeated, so the groove settles (fills and
            # turnarounds bring the change)
            mrnd = _rng(seed, "drums-maybe", g["name"], vary, part, b % 2, b // 8)
            if level == "full" and spec.get("maybe"):
                mstr = _bar_slice(spec["maybe"], 16, b)
                sc = kick_scale if part == "kick" else 1.0
                for i, c in enumerate(mstr):
                    if c.isdigit() and c != "0" and not any(abs(h[0] - i) < 0.01 for h in hits) \
                            and mrnd.random() < int(c) / 10.0 * var * sc:
                        hits.append((float(i), "o"))
            if level == "full" and spec.get("ghosts") and part in choice:
                gh = spec["ghosts"]
                plen = max(1, len(choice[part][0]["steps"]) // 16)
                for at in gh.get("at") or []:
                    at = int(at)
                    if at // 16 != b % plen:
                        continue
                    st = at % 16
                    if not any(abs(h[0] - st) < 0.01 for h in hits) and mrnd.random() < float(gh.get("chance", 0.3)) * var:
                        hits.append((float(st), "g"))
            if is_fill and part in (fs.get("parts") or {}):
                mask = (fs["parts"][part] + "_" * 16)[:16]
                hits = [h for h in hits if mask[min(15, int(h[0]))] == "_"] + \
                       [(float(i), c) for i, c in enumerate(mask) if c in DEFAULT_VEL]
            elif part in turn:
                mask = (turn[part] + "_" * 16)[:16]
                hits = [h for h in hits if mask[min(15, int(h[0]))] == "_"] + \
                       [(float(i), c) for i, c in enumerate(mask) if c in DEFAULT_VEL]
            drop = {"light": -6, "sparse": -12}.get(level, 0)
            for st, c in hits:
                out.append({"part": part, "s": round(b * SPB + st, 4), "l": 16.0 / grid, "v": _clampv(lv[c] + drop + rnd.randint(-3, 3)),
                            "dk": 0, "nos": grid != 16 or abs(st - round(st)) > 1e-6})
        if level == "full" and not is_fill:
            for part, spec in parts.items():
                ro = spec.get("rolls")
                if not ro or part not in choice:
                    continue
                used = []
                for _ in range(int(ro.get("max_per_bar", 1))):
                    if rnd.random() >= float(ro.get("chance", 0)) * var:
                        continue
                    cands = [a for a in ro.get("at") or [] if not any(u0 <= a < u1 + 1 for u0, u1 in used)]
                    if not cands:
                        break
                    at = cands[rnd.randrange(len(cands))]
                    ln = min(rnd.choice(ro.get("len") or [1]), 16 - at)
                    rate = _wpick(rnd, ro.get("rates") or {"1/32": 1})
                    _roll(out, part, b, at, ln, rate, ro.get("ramp"), lv["o"], rnd.random() < float(ro.get("pitch_up_chance", 0)), rnd)
                    used.append((at, at + ln))
        if is_fill and fs.get("roll"):
            ro = fs["roll"]
            _roll(out, ro.get("part", "snare"), b, int(ro.get("at", 12)), int(ro.get("len", 4)), ro.get("rate", "1/32"),
                  ro.get("ramp"), lv["x"], False, rnd)
    oh = {round(h["s"], 3) for h in out if h["part"] == "openhat"}
    out = [h for h in out if not (h["part"] == "hat" and round(h["s"], 3) in oh)]
    out.sort(key=lambda h: (h["s"], DRUM_PARTS.index(h["part"]) if h["part"] in DRUM_PARTS else 99))
    return out


def _build_drums(parts, bars, lv, rnd):
    """A build-up: snare (or clap) quarters -> 8ths -> 16ths -> 32nds with a velocity ramp, kick on the beats until
    the last bar, steady 8th hats."""
    sn = "snare" if "snare" in parts else "clap" if "clap" in parts else next(iter(parts), "snare")
    out = []
    total = bars * SPB
    for b in range(bars):
        f = b / max(1, bars)
        if bars == 1 or f >= 0.75:
            rate = 1.0
        elif f >= 0.5:
            rate = 2.0
        else:
            rate = 4.0
        t = 0.0
        while t < SPB:
            s = b * SPB + t
            if b == bars - 1 and t >= 8:
                rate = 0.5
            out.append({"part": sn, "s": round(s, 4), "l": rate, "v": _clampv(58 + 60 * (s / total) + rnd.randint(-2, 2)),
                        "dk": 0, "nos": True, "roll": rate < 1})
            t += rate
        if "kick" in parts:
            for st in ((0, 4, 8, 12) if b < bars - 1 else (0,)):
                out.append({"part": "kick", "s": b * SPB + st, "l": 1, "v": _clampv(lv["x"]), "dk": 0, "nos": False})
        if "hat" in parts:
            for st in range(2, 16, 4):
                out.append({"part": "hat", "s": b * SPB + st, "l": 1, "v": _clampv(lv["o"] - 6), "dk": 0, "nos": False})
    out.sort(key=lambda h: h["s"])
    return out


def meter_drum_spec(g, m=None):
    """(drum grammar, source) for a meter that isn't 4/4: the genre's drums.meters[ts], else the generic feel of
    data/meters.json, else a plain one (kick on 1, a backbeat in the middle, the pulse on the hats)."""
    m = m or _meter()
    own = (((g.get("drums") or {}).get("meters") or {}).get(m["ts"]))
    if own and own.get("parts"):
        return own, "genre"
    if m.get("drums") and (m["drums"].get("parts")):
        return m["drums"], "generic"
    S, beats = m["spb"], m["beats"]
    k, sn, hat = ["."] * S, ["."] * S, ["."] * S
    k[0] = "X"
    if len(beats) > 1:
        sn[beats[len(beats) // 2]] = "x"
    for i, b in enumerate(beats):
        hat[b] = "x" if i == 0 else "o"
    return {"parts": {"kick": {"patterns": [{"steps": "".join(k)}]}, "snare": {"patterns": [{"steps": "".join(sn)}]},
                      "hat": {"patterns": [{"steps": "".join(hat)}]}}}, "plain"


def drum_feel_note(g, m=None):
    """What drums in a meter that isn't 4/4 are based on, in words for the result (honest about a generic feel)."""
    m = m or _meter()
    _, src = meter_drum_spec(genre_data(g), m)
    feel = f" ({m['feel']})" if m.get("feel") else ""      # e.g. " (waltz: strong 1, light 2 and 3)"
    if src == "genre":
        return f"drums: {genre_data(g)['name']}'s own {m['ts']} patterns"
    return (f"drums: a generic {m['ts']} feel{feel} - {genre_data(g)['name']} has no {m['ts']} drum patterns "
            f"(add drums.meters[\"{m['ts']}\"] to its genre file to change that); listen before keeping it")


def drums_meter(genre, bars=4, variation=1.0, seed=1, level="full", vary=0, fill=True):
    # PC-VERIFY: listen - the generic 3/4 (waltz) and 6/8 (compound) grooves feel like the meter, not a cut 4/4 beat
    """drums() in a meter that isn't 4/4 -> (hits, info {meter, source, feel}). Same levels (full | light | sparse |
    half | build | none), A/B patterns, maybes, fills and crash rule as the 4/4 grammar; the patterns come from the
    genre's drums.meters[ts] or the meter's generic feel (info source = genre | generic | plain)."""
    g = genre_data(genre)
    m = _meter()
    S, beats = m["spb"], m["beats"]
    spec, src = meter_drum_spec(g, m)
    parts = spec.get("parts") or {}
    bars = int(bars)
    var = max(0.0, float(variation))
    dd = g.get("drums") or {}
    lv = dict(DEFAULT_VEL, **(dd.get("vel") or {}), **(spec.get("vel") or {}))
    rnd = _rng(seed, "drums", g["name"], vary, m["ts"])
    info = {"meter": m["ts"], "source": src, "feel": m.get("feel")}
    if level == "none" or bars <= 0:
        return [], info
    sn = "snare" if "snare" in parts or "snare" in (dd.get("parts") or {}) else "clap"
    out = []
    if level in ("half", "build"):
        for b in range(bars):
            if level == "half":
                hs = [(0, "X", "kick")] + ([(beats[len(beats) // 2], "X", sn)] if len(beats) > 1 else [])
                hs += [(t, "o", "hat") for t in beats]
            else:                                          # build: snare on the beats -> 8ths -> 16ths, rising
                f = b / max(1, bars)
                rate = 1.0 if (bars == 1 or f >= 0.75) else 2.0 if f >= 0.5 else None
                ts_ = [float(t) for t in beats] if rate is None else [x * rate for x in range(int(S / rate))]
                hs = [(t, None, sn) for t in ts_] + [(0, "x", "kick")]
            for st, c, p in hs:
                s = b * S + st
                v = _clampv(58 + 60 * (s / max(1, bars * S))) if c is None else _clampv(lv[c] - (4 if p != "hat" else 8))
                out.append({"part": p, "s": round(s, 4), "l": 1, "v": v, "dk": 0, "nos": c is None, "roll": c is None and st % 1 != 0})
        out.sort(key=lambda h: (h["s"], DRUM_PARTS.index(h["part"]) if h["part"] in DRUM_PARTS else 99))
        return out, info
    choice = {}
    for part, ps in parts.items():
        pats = [p for p in ps.get("patterns") or [] if float(p.get("w", 1)) > 0 and p.get("steps")]
        if not pats:
            continue
        ia, A = _pick(rnd, pats, vary)
        rest = [p for i, p in enumerate(pats) if i != ia]
        B = _pick(rnd, rest)[1] if rest else None
        simple = min(pats, key=lambda p: (sum(1 for c in p["steps"] if c in DEFAULT_VEL) / max(1, len(p["steps"])), pats.index(p)))
        choice[part] = (A, B, simple)
    fs = spec.get("fill") or {}
    every = int(fs.get("every") or 0)
    alt_block = {k: (k > 0 and rnd.random() < 0.25 * var) for k in range((bars + 1) // 2)}
    light_parts = {"kick", "snare", "clap", "hat", "ride"}
    sparse_parts = {"kick", "hat", "perc", "ride", "shaker"}
    fill_bars = set()
    for b in range(bars):
        is_fill = bool(fill and every and (b + 1) % every == 0 and level in ("full", "light"))   # a verse fills too
        if is_fill:
            fill_bars.add(b)
        # a phrase marker at the 4-bar point of a longer fill cycle: the genre's drums.fill.turn masks, else an
        # open hat on the last 8th (when the kit has one)
        is_turn = bool(fill and level == "full" and every >= 8 and (b + 1) % 4 == 0 and not is_fill)
        turn = (fs.get("turn") or ({"openhat": "______________o_"} if "openhat" in parts else {})) if is_turn else {}
        names = list(choice) + [p for p in (fs.get("parts") or {}) if is_fill and p not in choice] + \
            [p for p in turn if p not in choice and p in parts]
        for part in names:
            if level == "light" and part not in light_parts or level == "sparse" and part not in sparse_parts:
                continue
            if part in choice:
                A, B, simple = choice[part]
                pat = simple if level in ("light", "sparse") else (B if alt_block.get(b // 2) and B else A)
                bar_str = _bar_slice(pat["steps"], S, b)
            else:
                bar_str = "." * S
            hits = [(float(i), c) for i, c in enumerate(bar_str[:S]) if c in DEFAULT_VEL]
            if part == "crash" and not (b == 0 or (b - 1) in fill_bars):
                hits = []
            if level == "sparse" and part == "kick":
                hits = [h for h in hits if h[0] == 0][:1] if b % 2 == 0 else []
            mb = (parts.get(part) or {}).get("maybe")
            if level == "full" and mb and not is_fill:
                for i, c in enumerate(_bar_slice(mb, S, b)[:S]):
                    if c.isdigit() and c != "0" and not any(abs(h[0] - i) < 0.01 for h in hits) and rnd.random() < int(c) / 10.0 * var:
                        hits.append((float(i), "o"))
            if is_fill and part in (fs.get("parts") or {}):
                mask = (fs["parts"][part] + "_" * S)[:S]
                hits = [h for h in hits if mask[min(S - 1, int(h[0]))] == "_"] + \
                       [(float(i), c) for i, c in enumerate(mask) if c in DEFAULT_VEL]
            drop = {"light": -6, "sparse": -12}.get(level, 0)
            for st, c in hits:
                accent = 0 if c in ("X", "g") or not _at_pos(st, m["strong"], S) else 3
                out.append({"part": part, "s": round(b * S + st, 4), "l": 1.0,
                            "v": _clampv(lv[c] + drop + accent + rnd.randint(-3, 3)), "dk": 0, "nos": False})
    oh = {round(h["s"], 3) for h in out if h["part"] == "openhat"}
    out = [h for h in out if not (h["part"] == "hat" and round(h["s"], 3) in oh)]
    out.sort(key=lambda h: (h["s"], DRUM_PARTS.index(h["part"]) if h["part"] in DRUM_PARTS else 99))
    return out, info


def fill_bar(genre, seed=1, kind="fill"):
    """One bar of transition drums: kind fill (the genre's fill) or dropout (kick on 1, silence after beat 2)."""
    g = genre_data(genre)
    if kind == "dropout":
        return [{"part": "kick", "s": 0, "l": 1, "v": 110, "dk": 0, "nos": False}]
    S = _spb()                                            # one bar of the current meter (4/4: 16 steps)
    leg = _meter()["legacy"]
    fs = ((g.get("drums") or {}).get("fill") or {}) if leg else (meter_drum_spec(g)[0].get("fill") or {})
    every = int(fs.get("every") or 0)
    if every and fs.get("parts"):
        hits = drums(g, every, 1.0, seed, "full")
        return [dict(h, s=round(h["s"] - (every - 1) * S, 4)) for h in hits if h["s"] >= (every - 1) * S]
    lv = dict(DEFAULT_VEL, **((g.get("drums") or {}).get("vel") or {}))
    parts = (g.get("drums") or {}).get("parts") or {}
    sn = "snare" if "snare" in parts else "clap" if "clap" in parts else "perc"
    out = [{"part": "kick", "s": 0, "l": 1, "v": lv["x"], "dk": 0, "nos": False}]
    roll = "........o.o.oxxX"                             # the climb ends on the bar line in any meter
    roll = roll[-S:] if S <= 16 else "." * (S - 16) + roll
    for i, c in enumerate(roll):
        if c in lv:
            out.append({"part": sn, "s": i, "l": 1, "v": lv[c], "dk": 0, "nos": False})
    return out


# ============================================================================= harmony
def harmony(key, genre, bars=4, progression=None, seed=1, vary=0):
    """Chord timeline [{rn, name, root, q, iv, bass, start (step), len (steps)}] and info {progression, chord_bars}."""
    g = genre_data(genre)
    h = g.get("harmony") or {}
    root, mode, iv, fam = key_info(key)
    rnd = _rng(seed, "harmony", g["name"], key, vary)
    note, switched = None, None
    if progression:
        seq = parse_progression(progression, key)
        default_bars = 1.0
        if all(b is None for _, b in seq) and len(seq) * 2 <= int(bars) and len(seq) <= 2:
            default_bars = 2.0
        seq = [(c, b if b is not None else default_bars) for c, b in seq]
        label = [c["rn"] for c, _ in seq]
    else:
        progs = [p for p in h.get("progressions") or [] if p.get("mode", "any") in (fam, "any")]
        if not progs:
            progs = h.get("progressions") or [{"rn": ["I" if fam == "major" else "i"], "bars": 1}]
            other = "minor" if fam == "major" else "major"
            if all(p.get("mode", "any") == other for p in progs):   # e.g. drill / metal: minor only
                key = parallel_key(key, other)               # the numerals are minor-key numerals: read them there
                switched = key
            note = f"{g['name']} has no {fam} progressions: used {key}" if switched else \
                f"no {fam}-key progressions in {g['name']}; used the others"
        _, pr = _pick(rnd, progs, vary)
        seq = [(chord_from_token(t, key), float(pr.get("bars", 1))) for t in pr["rn"]]
        label = list(pr["rn"])
    S = _spb()
    total = int(bars) * S
    out, t, i = [], 0, 0
    while t < total:
        c, cb = seq[i % len(seq)]
        ln = max(1, int(round(cb * S)))
        out.append(dict(copy.deepcopy(c), start=t, len=min(ln, total - t)))
        t += ln
        i += 1
    info = {"progression": "-".join(label), "chords": [c["name"] for c in out[:len(seq)]]}
    if note:
        info["note"] = note
    if switched:                                          # callers write the melody / bass in this key too
        info["key"] = switched
    return out, info


def genre_key(key, genre):
    """(key, note or None): a key whose family (major / minor) the genre has no progressions for, when all of them
    are of the other family (drill, metal, techno: minor only), becomes its parallel key - the one harmony() builds
    the chords in."""
    g = genre_data(genre) if isinstance(genre, str) else genre
    fam = key_info(key)[3]
    progs = (g.get("harmony") or {}).get("progressions") or []
    other = "minor" if fam == "major" else "major"
    if progs and all(p.get("mode", "any") == other for p in progs):
        k2 = parallel_key(key, other)
        return k2, f"{g['name']} has no {fam} progressions: used {k2}"
    return key, None


def parallel_key(key, fam):
    """'C major' -> 'C minor' (fam='minor'), spelled with the asked root's letter: the parallel key of the other
    family."""
    tok = str(key).strip().split()[0] if str(key).strip() else "C"
    root = key_info(key)[0]
    if not re.match(r"^[A-Ga-g][#b]?$", tok):
        tok = _pc_name(root, _flats_for(key))
    return f"{tok[0].upper()}{tok[1:]} {fam}"


def _chord_tones(ch, style, ext, key, rnd_cache):
    """Intervals to voice (relative to root) for a chord in a voicing style; extension choices are made once per
    chord name so repeats sound the same."""
    iv = list(ch["iv"])
    name = ch["name"]
    if name not in rnd_cache:
        r = random.Random(zlib.crc32(name.encode()) ^ rnd_cache.get("_seed", 0))
        rnd_cache[name] = (r.random(), r.random())
    r7, r9 = rnd_cache[name]
    scale = set(scale_pcs(key, "key"))
    q = ch["q"]
    if style == "power":
        return [0, 7, 12]
    has7 = any(i in (10, 11) for i in iv) or q in ("dim7", "6", "m6")
    if style in ("seventh", "extended", "rootless", "shell", "triad") and not has7 and q not in ("5", "sus2", "sus4", "aug") \
            and r7 < float(ext.get("7", 0)):
        if q in ("m", "madd9"):
            iv.append(10)
        elif q == "dim":
            iv.append(10)
        else:
            iv.append(11 if (ch["root"] + 11) % 12 in scale else 10)
        has7 = True
    if style in ("extended", "rootless", "seventh") and 14 not in iv and q not in ("dim", "dim7", "m7b5", "5", "aug") \
            and r9 < float(ext.get("9", 0)) and (ch["root"] + 2) % 12 in scale:
        iv.append(14)
    iv = sorted(set(iv))
    if style == "shell":
        third = [i for i in iv if i in (3, 4)] or [i for i in iv if i in (2, 5)]
        sev = [i for i in iv if i in (10, 11, 9)]
        return [0] + third[:1] + (sev[:1] or [7])
    if style == "rootless" and len(iv) >= 4:
        iv = [i for i in iv if i != 0]
        if len(iv) > 4 and 7 in iv:
            iv.remove(7)
    if style == "extended" and len(iv) > 4 and 7 in iv:
        iv.remove(7)
    if style == "triad" and len(iv) > 4:
        iv = [i for i in iv if i in (0, 3, 4, 7, 10, 11, 6, 8, 2, 5)][:4]
    return iv


def _vl_cost(a, b):
    if not a:
        return 0.0
    c = sum(min(abs(x - y) for y in a) for x in b) + sum(min(abs(x - y) for y in b) for x in a)
    return c / 2.0 + 0.5 * abs(max(a) - max(b))


LOW_LIMITS = {1: 52, 2: 51, 3: 48, 4: 46, 5: 45, 6: 47, 7: 34, 8: 43, 9: 41, 10: 41, 11: 41, 13: 40, 14: 39}


def _limits():
    """Low interval limits {semitones: lowest MIDI note for the lower voice} (flh_score's rules when loaded)."""
    try:
        return H["score_rules"]()["_limits"]
    except Exception:
        return LOW_LIMITS


def _mud(v, lim=None):
    """Penalty for intervals under their low interval limit inside one voicing (3rds below C3, 2nds below Eb3)."""
    lim = lim or LOW_LIMITS
    s = sorted(v)
    pen = 0.0
    for i, x in enumerate(s):
        if x >= 52:
            break
        for y in s[i + 1:]:
            iv = y - x
            if iv > 14:
                break
            if iv in lim and x < lim[iv]:
                pen += 2.0 + (lim[iv] - x) / 2.0
    return pen


def mud_guard(chord_notes, bass_notes, hi=None, chords=None):
    """Chord notes that sit under their low interval limit above a sounding bass note (a 2nd/3rd/tritone low
    down, a b9 over the bass) move up an octave when that stays in range: what an arranger does when the bass
    walks close under the keys; a 9th that can't move is left out (with chords given). Changes chord_notes in
    place. -> number of notes changed."""
    lim = _limits()
    hi = hi if hi is not None else 84
    bass = sorted(bass_notes, key=lambda n: n["s"])
    moved = 0
    drop = []
    for n in chord_notes:
        if n["k"] >= 60:
            continue
        bad = 0.0
        for b in bass:
            if b["s"] >= n["s"] + n["l"]:
                break
            ov = min(b["s"] + b["l"], n["s"] + n["l"]) - max(b["s"], n["s"])
            iv = n["k"] - b["k"]
            if ov >= 2 and 0 < iv in lim and b["k"] < lim[iv] and (iv % 12 in (1, 2, 6, 11) or iv < 12):
                bad += ov
        same = [m_["k"] for m_ in chord_notes if m_ is not n and abs(m_["s"] - n["s"]) < 0.5]
        if bad < 2:
            continue
        if n["k"] + 12 <= hi and not any(abs(k - n["k"] - 12) <= 1 for k in same):
            n["k"] += 12
            moved += 1
        elif chords and len(same) >= 3 and (n["k"] - chord_at(chords, n["s"])["root"]) % 12 == 2:
            drop.append(id(n))
            moved += 1
    if drop:
        chord_notes[:] = [n for n in chord_notes if id(n) not in drop]
    return moved


def melody_guard(chord_notes, mel, lo=48):
    """Keep the accompaniment out of the lead's way: a chord note a semitone (or a b9) against a held melody note,
    or above the melody while it sounds, moves down an octave (or is left out when the chord keeps 3+ notes).
    Changes chord_notes in place. -> {moved, dropped}."""
    out = {"moved": 0, "dropped": 0}
    if not mel:
        return out
    ms = sorted(mel, key=lambda n: n["s"])
    drop = set()
    for i, c in enumerate(chord_notes):
        worst = None
        for m_ in ms:
            if m_["s"] >= c["s"] + c["l"]:
                break
            ov = min(m_["s"] + m_["l"], c["s"] + c["l"]) - max(m_["s"], c["s"])
            if ov < 2:
                continue
            d = m_["k"] - c["k"]
            if (abs(d) % 12 == 1 and abs(d) < 24) or (d < 0 and ov >= 4) or (d in (11, 23) and ov >= 4):
                worst = m_
                break
        if worst is None:
            continue
        mates = [x for j, x in enumerate(chord_notes) if j != i and j not in drop and abs(x["s"] - c["s"]) < 0.5]
        if worst["k"] - c["k"] in (11, 23):                # the melody holds the maj7 of this note: leave it out
            if len(mates) >= 2:
                drop.add(i)
                out["dropped"] += 1
            continue
        k2 = c["k"] - 12
        while k2 > worst["k"] - 1 and k2 - 12 >= lo - 5:
            k2 -= 12
        mk = [x["k"] for x in mates]
        if k2 >= lo - 5 and k2 < worst["k"] and not any(abs(x - k2) <= 1 for x in mk) and \
                abs(worst["k"] - k2) % 12 != 1 and _mud(mk + [k2]) <= _mud(mk + [c["k"]]):
            c["k"] = k2
            out["moved"] += 1
        elif len(mates) >= 2:
            drop.add(i)
            out["dropped"] += 1
    if drop:
        chord_notes[:] = [c for j, c in enumerate(chord_notes) if j not in drop]
    return out


def voice(ch, tones, style, register, prev=None):
    """MIDI notes for a chord: smooth voice leading from prev inside [lo, hi] (power/open have fixed shapes)."""
    lo, hi = int(register[0]), int(register[1])
    root = ch["root"]
    if style == "power":
        r = _place(root, lo + 6 if prev is None else min(prev), lo, max(lo + 11, hi - 12))
        return [r, r + 7, r + 12]
    if style == "open":
        r = _place(root, lo + 4 if prev is None else min(prev), lo, lo + 14)
        rest = sorted({(i % 12) for i in tones if i % 12 != 0})
        out = [r]
        fifth = 7 if 7 in [i % 12 for i in tones] else None
        if fifth:
            out.append(r + 7)
        third = next((i for i in (3, 4, 2, 5) if i in rest), None)
        if third is not None:
            out.append(r + 12 + third)
        for i in rest:
            if i not in (3, 4, 2, 5, 7):
                out.append(r + 12 + i if r + 12 + i > out[-1] else r + 24 + i)
        out.append(r + 24 if r + 24 <= hi else r + 12)
        return sorted({p for p in out if p <= hi + 7})
    pcs = [(root + i) % 12 for i in tones]
    n = len(pcs)
    cands = []
    order = sorted(set(pcs), key=lambda p: tones[pcs.index(p)])
    for inv in range(len(order)):
        seq = order[inv:] + order[:inv]
        for start in range(lo, hi + 1):
            if start % 12 != seq[0]:
                continue
            v = [start]
            for pc in seq[1:]:
                p = v[-1] + 1
                while p % 12 != pc:
                    p += 1
                v.append(p)
            if v[-1] <= hi:
                cands.append(v)
                if len(v) >= 4 and style in ("extended", "rootless", "seventh"):
                    d2 = sorted(v[:-2] + [v[-2] - 12] + v[-1:])          # drop-2 spread
                    if d2[0] >= lo - 5:
                        cands.append(d2)
    if not cands:
        base = _place(root, (lo + hi) // 2, lo, hi)
        return sorted(base + i for i in tones)
    mid = (lo + hi) / 2.0
    lim = _limits()

    def cost(v):
        c = _vl_cost(prev, v) if prev else 0.0
        # a maj7 / b9 below C4 sits within about an octave of the bass root and rubs it; a minor 2nd inside the
        # voicing (maj7 next to the root) grinds unless it's up high
        low_rub = sum(6.0 for p in v if p < 60 and (p - root) % 12 in (11, 1))
        seconds = sum(2.5 for x, y in zip(v, v[1:]) if y - x == 1 and x < 76)
        return c + 0.15 * abs(sum(v) / len(v) - mid) + _mud(v, lim) + low_rub + seconds
    best = min(cands, key=lambda v: (cost(v), v))
    return best


def chord_part(chords, genre, key, seed=1, vary=0, style=None, register=None):
    """Voiced, rhythmic chord notes [{s, l, k, v, strum}] following the genre's voicing and comping rhythm."""
    g = genre_data(genre)
    h = g.get("harmony") or {}
    vo = h.get("voicing") or {}
    style = style or vo.get("style", "triad")
    reg = register or vo.get("register") or [52, 74]
    ext = vo.get("extensions") or {}
    rnd = _rng(seed, "chords", g["name"], vary)
    alt = _meter_alt(h.get("rhythm"), "patterns")       # the genre's own comping for this meter, else 4/4 bars fitted
    _, rh = _pick(rnd, alt or (h.get("rhythm") or {}).get("patterns") or [{"steps": "x" + "-" * 15}], vary)
    steps = rh["steps"]
    S = _spb()
    vlo, vhi = (h.get("vel") or [72, 90])[:2]
    cache = {"_seed": _seed(seed, "ext", vary)}
    prev, out, voicings = None, [], []
    for ch in chords:
        tones = _chord_tones(ch, style, ext, key, cache)
        v = voice(ch, tones, style, reg, prev)
        prev = v
        voicings.append(v)
        ons = []
        for off in range(0, ch["len"], S):
            bar = _pat_bar(steps, off // S, bool(alt))
            for i, c in enumerate(bar):
                if off + i >= ch["len"]:
                    break
                if c == "x":
                    ons.append(off + i)
        if not ons:
            ons = [0]
        for j, o in enumerate(ons):
            end = ons[j + 1] if j + 1 < len(ons) else ch["len"]
            ln = 1
            bar = _pat_bar(steps, o // S, bool(alt))
            k = o % S + 1
            while k < S and bar[k] == "-" and o - o % S + k < end:
                ln += 1
                k += 1
            if k >= S and o - o % S + k < end:        # a hold reaching the bar end continues into the next bar
                ln = end - o
            vel = rnd.randint(int(vlo), int(vhi)) + (6 if j == 0 else 0)
            for idx, p in enumerate(v):
                out.append({"s": ch["start"] + o, "l": max(0.5, ln - 0.1), "k": p, "v": _clampv(vel - (3 if idx < len(v) - 1 else 0)),
                            "strum": idx})
    return out, {"style": style, "register": reg, "rhythm": steps, "voicings": [[fl_music.note_name(p) for p in v] for v in voicings[:4]]}


def chord_at(chords, s):
    hit = chords[0] if chords else None
    for c in chords:
        if c["start"] <= s + 1e-6:
            hit = c
    return hit


# ============================================================================= bass
def _onsets_from(steps, total):
    """x/-/. string (looped) -> [(step, written length)]."""
    out = []
    n = len(steps)
    if not n:
        return out
    for t in range(0, total):
        c = steps[t % n]
        if c == "x":
            ln, k = 1, t + 1
            while k < total and steps[k % n] == "-":
                ln += 1
                k += 1
            out.append((t, ln))
    return out


def bassline(chords, genre, kick_steps=None, seed=1, vary=0, bars=None, key=None):
    """Bass / 808 notes [{s, l, k, v, glide?, glide_len?}] on the chord roots, locked to the kick or the genre's
    bass rhythm; 808 genres glide (slide) between roots and octaves."""
    g = genre_data(genre)
    b = g.get("bass") or {}
    S = _spb()
    leg = _meter()["legacy"]
    total = int(bars) * S if bars else max(c["start"] + c["len"] for c in chords)
    lo, hi = (b.get("register") or [28, 48])[:2]
    rnd = _rng(seed, "bass", g["name"], vary)
    rhythm = b.get("rhythm", "kick")
    typ = b.get("type", "bass")
    gap = float(b.get("gap_steps", 0.25))
    if rhythm == "walk":
        return _walk(chords, b, lo, hi, total, rnd, key), {"rhythm": "walk", "glides": 0}
    if rhythm == "kick":
        ks = sorted(set(round(s, 3) for s in (kick_steps if kick_steps is not None else
                    [h["s"] for h in drums(g, max(1, total // S), 1.0, seed, "full", vary) if h["part"] == "kick"])))
        ons = [(s, None) for s in ks if s < total]
        ex = b.get("extra_hits") or {}
        for at in ex.get("at") or []:
            at = int(at)
            if not leg:                                   # 2-bar 4/4 positions: the same bar and step, if it fits
                if at % 16 >= S:
                    continue
                at = at // 16 * S + at % 16
            for base in range(0, total, 2 * S):
                s = base + int(at)
                if s < total and not any(abs(s - o) < 1 for o, _ in ons) and rnd.random() < float(ex.get("chance", 0)):
                    ons.append((s, None))
    elif rhythm == "chord":
        ons = [(c["start"], c["len"]) for c in chords]
    else:
        alt = _meter_alt(b, "patterns")
        _, pat = _pick(rnd, alt or b.get("patterns") or [{"steps": "x-------x-------"}], vary)
        ons = [(s, l) for s, l in _onsets_from(_pat_meter(pat["steps"], bool(alt)), total)]
        if bass_lock_of(g) == "kick":
            ks = kick_steps if kick_steps is not None else [h["s"] for h in drums(g, max(1, total // S), 1.0, seed, "full", vary)
                                                            if h["part"] == "kick"]
            ons, locked = _lock_to_kick(ons, sorted(set(round(x, 3) for x in ks if x < total)), b, total, rnd)
    for c in chords:                                      # the bass re-attacks on every chord change
        if c["start"] < total and not any(abs(s - c["start"]) < 1 for s, _ in ons):
            ons.append((c["start"], None))
    ons = sorted({round(s, 3): (s, l) for s, l in ons}.values())
    lm = str(b.get("length", "until_next"))
    home = lo + (hi - lo) * 0.35
    notes = []
    count_in_chord = {}
    for i, (s, wl) in enumerate(ons):
        nxt = ons[i + 1][0] if i + 1 < len(ons) else total
        room = max(0.5, nxt - s - gap)
        if lm.startswith("steps:"):
            ln = min(float(lm.split(":")[1]), room)
        elif lm == "as_written" and wl:
            ln = min(float(wl) - gap if wl > 1 else wl, room) if wl else room
        else:
            ln = room
        c = chord_at(chords, s)
        rootp = _place(c["bass"] if c.get("bass") is not None else c["root"],
                       home if (not notes or b.get("follow") == "octave") else notes[-1]["k"], lo, hi)
        k = rootp
        n_in = count_in_chord.get(id(c), 0)
        count_in_chord[id(c)] = n_in + 1
        at_change = abs(s - c["start"]) < 1
        if b.get("follow") == "root_fifth" and not at_change and n_in % 2 == 1:
            k = rootp + 7 if rootp + 7 <= hi else rootp - 5
        elif b.get("follow") == "octave" and n_in % 2 == 1:      # the octave pulse (synthwave, dance pop)
            k = rootp + 12 if rootp + 12 <= hi + 12 else rootp - 12
        if not at_change and b.get("follow") != "octave" and rnd.random() < float(b.get("octave_jump", 0)):
            k = k + 12 if k + 12 <= hi + 12 else k
        nc = chord_at(chords, nxt) if nxt < total else None
        if typ not in ("808",) and nc is not None and nc is not c and not at_change and nxt - s <= 4 \
                and rnd.random() < float(b.get("approach", 0)):                # a short chromatic lead-in only
            tgt = _place(nc["root"], k, lo, hi)
            k = tgt - 1 if abs(tgt - 1 - k) <= abs(tgt + 1 - k) else tgt + 1
        notes.append({"s": s, "l": round(ln, 3), "k": max(0, k), "v": _clampv(rnd.randint(*[int(x) for x in (b.get("vel") or [96, 110])[:2]]) + (6 if s % S == 0 else 0))})
    gl = b.get("glide") or {}
    glides = 0
    if float(gl.get("chance", 0)) > 0:
        for i in range(1, len(notes)):
            n, p = notes[i], notes[i - 1]
            if gl.get("where", "late") == "late" and (n["s"] % S) < S / 2 or n["s"] % S == 0:
                continue                                           # the downbeat re-attacks with the kick
            if p.get("glide") or n["s"] - (p["s"] + p["l"]) > 2:
                continue
            if rnd.random() >= float(gl["chance"]):
                continue
            c = chord_at(chords, n["s"])
            at_change = abs(n["s"] - c["start"]) < 1
            tg = "next_root" if at_change else _wpick(rnd, gl.get("targets") or {"next_root": 1})
            top, bot = hi + 12, max(0, lo - 4)                     # an 808 slides an octave over its register at most
            if tg == "octave_up":
                t = p["k"] + 12 if p["k"] + 12 <= top else p["k"] - 12
            elif tg == "octave_down":
                t = p["k"] - 12 if p["k"] - 12 >= bot else p["k"] + 12
            elif tg in ("fifth", "third", "minor_third"):          # chord tones of the chord under it
                iv = c["iv"]
                want = (next((i for i in iv if i in (7, 6, 8)), 7) if tg == "fifth" else
                        next((i for i in iv if i in (3, 4)), next((i for i in iv if i in (2, 5)), 7)))
                pc = (c["root"] + want) % 12
                cands = [x for x in range(bot, top + 1) if x % 12 == pc and x != p["k"]]
                t = min(cands, key=lambda x: (abs(x - p["k"] - 4), x)) if cands else p["k"]
            else:
                t = _place(c["root"], p["k"], lo, hi)
            if t == p["k"]:
                t = p["k"] + 12 if p["k"] + 12 <= top else p["k"] - 12
            n["k"] = t
            n["glide"] = True
            n["glide_len"] = float(rnd.choice(gl.get("len_steps") or [1]))
            glides += 1
    if b.get("octave_double"):
        notes += [dict(n, k=n["k"] + 12, v=_clampv(n["v"] * 0.75), glide=False) for n in notes if n["k"] + 12 <= 127]
        notes.sort(key=lambda n: (n["s"], n["k"]))
    return notes, {"rhythm": rhythm, "glides": glides, "type": typ}


def _lock_to_kick(ons, kicks, b, total, rnd):
    """A pattern bass talks with the kick: a bass onset a step or two off a kick moves onto it (when that leaves
    room), and a kick that falls in a bass rest gets a short bass note (bass.lock_kick {snap, fill} in the genre
    data; defaults 2 steps / 0.5)."""
    lk = b.get("lock_kick") or {}
    snap, addp = float(lk.get("snap", 2)), float(lk.get("fill", 0.5))
    new = [list(o) for o in ons]
    n = 0
    for k in kicks:
        if any(abs(o[0] - k) < 0.5 for o in new):
            continue
        near = [(abs(o[0] - k), i) for i, o in enumerate(new) if abs(o[0] - k) <= snap
                and not any(abs(o[0] - k2) < 0.5 for k2 in kicks)]
        if near:
            _, i = min(near)
            if not any(abs(o[0] - k) < 1 for j, o in enumerate(new) if j != i):
                s0, l0 = new[i]
                new[i] = [k, None if l0 is None else max(0.5, l0 - (k - s0))]
                n += 1
                continue
        sounding = any(o[0] <= k < o[0] + (o[1] if o[1] is not None else 1e9) for o in new if o[0] < k)
        if not sounding and rnd.random() < addp:
            nxt = min([o[0] for o in new if o[0] > k] + [total])
            new.append([k, float(min(4.0, max(1.0, nxt - k - 0.5)))])
            n += 1
    new.sort(key=lambda o: o[0])
    return [tuple(o) for o in new], n


def _walk(chords, b, lo, hi, total, rnd, key):
    """Walking bass: root on the chord's first beat, chord/scale tones moving toward the next root, a chromatic
    approach on the last beat before a change."""
    out = []
    scale = _pitches(scale_pcs(key, "key") if key else list(range(12)), lo, hi + 5)
    cur = None
    pl = _meter()["pulse"]                                # steps per beat (4; 6 in compound meters)
    for ci, c in enumerate(chords):
        nxt = chords[ci + 1] if ci + 1 < len(chords) else chords[0]
        beats = max(1, int(c["len"] // pl))
        r = _place(c["root"], cur if cur is not None else lo + 7, lo, hi)
        tgt = _place(nxt["root"], r, lo, hi)
        tones = _pitches(chord_pcs(c), lo, hi + 5)
        for j in range(beats):
            s = c["start"] + j * pl
            if s >= total:
                break
            if j == 0:
                k = r
            elif j == beats - 1 and rnd.random() < float(b.get("approach", 0.5)):
                k = tgt - 1 if cur is not None and cur < tgt else tgt + 1
            else:
                direction = 1 if tgt > (cur or r) else -1          # beat 3 is a chord tone; 2 and 4 may pass by step
                k = _nearest(tones, cur or r, direction) if (j == 2 or rnd.random() < 0.6) else _step_in(scale, cur or r, direction)
            k = _fold(k, lo, hi + 5)
            out.append({"s": s, "l": pl - float(b.get("gap_steps", 0.4)), "k": k,
                        "v": _clampv(rnd.randint(*[int(x) for x in (b.get("vel") or [84, 100])[:2]]) + (5 if j == 0 else 0))})
            cur = k
    return out


def encode_glides(notes, mode="slide"):
    """Logical bass notes with glide marks -> FL notes. slide: the note before keeps sounding and a short slide
    note (flag) bends it to the new pitch (FL slide notes). overlap: plain notes that overlap (mono + Porta glides)."""
    out = []
    base = None
    for n in sorted(notes, key=lambda n: n["s"]):
        if n.get("glide") and base is not None and mode == "slide":
            base["l"] = round(n["s"] + n["l"] - base["s"], 3)
            out.append({"s": n["s"], "l": min(float(n.get("glide_len", 1)), n["l"]), "k": n["k"], "v": n["v"], "slide": True})
            continue
        if n.get("glide") and base is not None and mode == "overlap":
            base["l"] = round(n["s"] - base["s"] + min(1.0, n.get("glide_len", 1)), 3)
        m = {k: v for k, v in n.items() if k not in ("glide", "glide_len")}
        out.append(m)
        base = m
    return out


# ============================================================================= melody


def _motif_from_user(motif, unit, key):
    """User motif: [[step, len, pitch], ...] or [{s|step, l|len, k|pitch|note}] in 16ths -> motif with exact
    intervals (realized as scale steps between the given pitches)."""
    rows = []
    for x in motif:
        if isinstance(x, dict):
            s = x.get("s", x.get("step", 0))
            l = x.get("l", x.get("len", 1))
            k = x.get("k", x.get("pitch", x.get("note")))
        else:
            s, l, k = list(x)[:3]
        rows.append((float(s), float(l), fl_music.parse_note(k) if not isinstance(k, (int, float)) else int(k)))
    rows.sort()
    rows = [r for r in rows if r[0] < unit]
    if not rows:
        raise ValueError("motif has no notes inside the motif length")
    ons = [(s, l) for s, l, _ in rows]
    moves = [("abs", rows[0][2], False)] + [("int", rows[i][2] - rows[i - 1][2], False) for i in range(1, len(rows))]
    return {"ons": ons, "moves": moves, "steps": "user", "user": True}



HOOK_DEFAULT = {"plan": ["A", "S", "A", "C"], "answer": "same", "breath": 3, "land": [8, 0],
                "shapes": {"arch": 3, "fall": 2, "rise": 1, "wave": 1}, "leap": 0.5, "candidates": 6}
PLAN_OF_FORM = {"A": "A", "A'": "S", "B": "B", "B'": "R"}   # an old-style motif form read as a phrase plan


def _hook_spec(m):
    """The genre's hook grammar (melody.hook), defaults filled; an old genre file's melody.form becomes a plan."""
    h = dict(HOOK_DEFAULT, **(m.get("hook") or {}))
    if not (m.get("hook") or {}).get("plan") and m.get("form"):
        h["plan"] = [PLAN_OF_FORM.get(str(x), "A") for x in m["form"]]
    return h


def _cell_parse(steps, u, length_mode, max_len):
    """One rhythm cell -> [(t, len)] in 16th steps. x onset, - hold, . rest; u = 16ths per character (1, or 4/3
    for an 8th-triplet grid of 12 a bar)."""
    n = len(steps)
    ons = [i for i, c in enumerate(steps) if c in "xX"]
    out = []
    for j, i in enumerate(ons):
        nxt = ons[j + 1] if j + 1 < len(ons) else n
        holds = 0
        while i + 1 + holds < n and steps[i + 1 + holds] == "-":
            holds += 1
        if length_mode == "short":
            ln = min(1 + holds, nxt - i)
        elif length_mode == "as_written":
            ln = 1 + holds if holds else min(2.0 / u, nxt - i)
        else:
            ln = nxt - i
        out.append((round(i * u, 4), round(float(max(0.5, min(ln * u, max_len, (nxt - i) * u))), 4)))
    return out


def _mel_cells(m, S, length_mode, max_len):
    """The genre's rhythm cells for this meter: [{ons, len, steps, w}] (a cell is one or more bars). Cells with
    grid 12 / 24 (triplets) are used in 4/4 only; 4/4 cells are laid onto other meters' beats."""
    mt = _meter()
    alt = _meter_alt(m, "rhythms")
    src = [r for r in (alt or m.get("rhythms") or []) if r.get("steps")] or [{"steps": "x---x---x---x---"}]
    cells = []
    for r in src:
        grid = int(r.get("grid", 16))
        steps = r["steps"]
        if grid != 16:
            if not mt["legacy"] or alt:
                continue
            per, u = grid, 16.0 / grid
        elif alt or mt["legacy"]:
            per, u = (S if alt else 16), 1.0
        else:
            nb_ = max(1, len(steps) // 16)
            steps = "".join(_fit_bar(steps[j * 16:(j + 1) * 16], S) for j in range(nb_))
            per, u = S, 1.0
        bars_in = max(1, int(round(len(steps) / float(per))))
        steps = (steps + "." * (bars_in * per))[:bars_in * per]
        ons = _cell_parse(steps, u, length_mode, max_len)
        if ons:
            cells.append({"ons": ons, "len": bars_in * S, "steps": r["steps"], "w": float(r.get("w", 1))})
    return cells or [{"ons": [(0.0, 4.0), (4.0, 4.0), (8.0, 4.0), (12.0, 4.0)], "len": S, "steps": "x---x---x---x---",
                      "w": 1.0}]


def _shape_moves(rnd, n, shape, m, flip=False):
    """Scale-step moves (n-1 of them) that draw a contour: arch / rise / fall / wave / hold; mostly steps, a leap
    now and then that steps back the other way (gap fill), repeated notes as the genre likes."""
    rep = float(m.get("repeat_note", 0.2))
    stp = float(m.get("step_prob", 0.7))
    penta = len(scale_pcs("C major", m.get("scale", "key"))) <= 5 if m.get("scale") not in (None, "key") else False
    mx = 2 if penta or int(m.get("max_leap", 7)) < 7 or rnd.random() < 0.7 else 3   # a 3rd mostly, a 4th / 5th now and then
    lead = 1 if rnd.random() < float((m.get("hook") or {}).get("leap", HOOK_DEFAULT["leap"])) else -1
    moves = []
    for i in range(1, n):
        f = i / float(max(1, n - 1))
        if shape == "arch":
            d = 1 if f <= 0.55 else -1
        elif shape == "rise":
            d = 1 if f < 0.8 else -1
        elif shape == "fall":
            d = -1 if f > 0.2 else 1
        elif shape == "wave":                             # skip up, step back: a rising zigzag
            d = (1 if penta else 2) if i % 2 else -1
        else:                                             # hold: a repeated note with neighbours
            d = 0 if i % 2 else (1 if i % 4 == 2 else -1)
        if flip:
            d = -d
        r = rnd.random()
        if i == lead and d:                               # the motif's characteristic leap (a 3rd to a 5th)
            mv = (1 if d > 0 else -1) * rnd.randint(2, mx)
        elif moves and abs(moves[-1]) >= 2 and shape != "wave":   # after a leap: step back
            mv = -1 if moves[-1] > 0 else 1
        elif d == 0 or r < rep * 0.6:
            mv = 0
        elif r < 1.0 - min(0.08, max(0.0, 1.0 - stp - rep)):   # steps; another leap only now and then
            mv = d
        else:
            mv = d * 2
        moves.append(mv)
    return moves


class _MelCtx:
    """What the phrase builder needs: chords, scales, range window, key."""
    def __init__(self, key, chords, m, lo, hi, total):
        self.chords, self.m, self.lo, self.hi, self.total = chords, m, lo, hi, total
        self.scale = scale_pcs(key, m.get("scale", "key"))
        self.kpcs = scale_pcs(key, "key")
        self.root = key_info(key)[0]
        self.S = _spb()
        self._cs = {}

    def chord(self, at, ln=0.0):
        ch = chord_at(self.chords, at)
        nx = next((c for c in self.chords if at < c["start"] < at + ln), None)
        if nx is not None and at + ln - nx["start"] > nx["start"] - at:
            ch = nx                                       # an anticipation belongs to the chord it rings into
        return ch

    def cscale(self, ch, held):
        k = (id(ch), held)
        if k not in self._cs:
            pcs = chord_scale(self.scale, ch, self.kpcs, held)
            self._cs[k] = (_pitches(pcs, self.lo - 12, self.hi + 12), _pitches(chord_pcs(ch), self.lo - 12, self.hi + 12))
        return self._cs[k]


def _realize_line(ons, moves, start, mc, win, t0=0.0):
    """Onsets [(t, l)] (t from t0) + scale-step moves from a start pitch -> notes. Strong beats (and long notes on a
    beat) take the chord tone nearest the line's way; the rest stay on the chord's scale; the line stays in win."""
    lo, hi = win
    out, cur = [], None
    for i, (t, l) in enumerate(ons):
        at = t0 + t
        ch = mc.chord(at, l)
        cs, ct = mc.cscale(ch, l >= 2)
        if i == 0 or cur is None:
            p = _nearest(ct, start) if start is not None else _nearest(ct, (lo + hi) // 2)
            if not (lo <= p <= hi):
                p = _nearest([x for x in ct if lo <= x <= hi] or ct, p)
        else:
            mv = moves[i - 1] if i - 1 < len(moves) else 0
            p = _step_in(cs, cur, mv) if mv else (cur if cur % 12 in {x % 12 for x in cs} else _nearest(cs, cur))
            if p > hi or p < lo:                          # bounce off the window's edge
                p = _step_in(cs, cur, -mv if mv else (-1 if p > hi else 1))
            strong = _strong(at)                          # beats 1 and 3 (the meter's strong beats)
            if strong and p % 12 not in {x % 12 for x in ct}:
                d = 1 if mv > 0 else -1 if mv < 0 else 0
                q = _nearest(ct, p, 0)
                q2 = _nearest(ct, p, d) if d else q
                p = q if abs(q - p) <= abs(q2 - p) or not (lo <= q2 <= hi) else q2
                if p == cur and mv:                       # keep moving the way it went
                    p2 = _nearest(ct, cur, 1 if mv > 0 else -1)
                    if lo <= p2 <= hi and abs(p2 - cur) <= 7:
                        p = p2
            if not (lo <= p <= hi):
                inside = [x for x in (ct if strong else cs) if lo <= x <= hi]
                p = _nearest(inside or cs, p)
        out.append({"s": round(at, 3), "l": l, "k": p, "v": 0})
        cur = p
    return out


def _land(notes, mc, at, final, win, hold, breath, phrase_end):
    """End a phrase: the note on the landing beat `at` is a stable chord tone (final: the chord's root, else its 3rd
    or 5th, nearest the line), approached by step, held, then space until the phrase ends."""
    lo, hi = win
    keep = [n for n in notes if n["s"] < at - 1e-6]
    prev = keep[-1]["k"] if keep else (notes[0]["k"] if notes else (lo + hi) // 2)
    ch = mc.chord(at, 0)
    r = ch["root"]
    third = next(((r + i) % 12 for i in ch["iv"] if i in (3, 4)), None)
    fifth = next(((r + i) % 12 for i in ch["iv"] if i in (6, 7, 8)), None)
    want = [r] if final else [x for x in (third, fifth, r) if x is not None]
    tgt = [p for p in _pitches(want, lo, hi)] or _pitches(want, lo - 12, hi + 12)
    p = min(tgt, key=lambda x: (abs(x - prev) if abs(x - prev) <= 7 else 20 + abs(x - prev), x))
    if final and third is not None:                       # the 3rd is a fine resting place too when much nearer
        t3 = [x for x in _pitches([third], lo, hi) if abs(x - prev) + 3 < abs(p - prev)]
        if t3:
            p = min(t3, key=lambda x: abs(x - prev))
    end = max(at + 1.0, phrase_end - breath)
    cap = max(4.0, 2 * float(mc.m.get("max_len_steps", 8)))    # a pluck genre's landing stays a short note
    ln = max(1.0, min(float(hold or cap), cap, end - at))
    if keep and keep[-1]["s"] + 1 < at and abs(keep[-1]["k"] - p) > 2:   # approach by step
        pl = min(keep[-1]["l"], at - keep[-1]["s"])
        cs, _ = mc.cscale(mc.chord(keep[-1]["s"], pl), pl >= 2)            # (a held one: no avoid note)
        for d in ((1, -1) if keep[-1]["k"] > p else (-1, 1)):
            a = _step_in(cs, p, d)
            if a != p and abs(a - p) <= 2 and not _strong(keep[-1]["s"]) and lo <= a <= hi:
                keep[-1]["k"] = a
                break
    for n in keep:                                        # nothing rings into the landing note
        n["l"] = min(n["l"], max(0.5, at - n["s"]))
    keep.append({"s": round(at, 3), "l": round(ln, 3), "k": p, "v": 0, "land": True})
    return keep


def _phrase(mc, P, t0, call, answer, moves_a, moves_b, start, win, final, hs):
    """One phrase (P steps from t0): call cell + answer cell with their moves, the answer landing (antecedent:
    3rd / 5th; final: root) and breathing; a riff (no landing beats) still ends on a chord tone."""
    S = mc.S
    ons = [(t, l) for t, l in call["ons"]]
    if call["len"] < P and answer is not None:
        ons += [(t + call["len"], l) for t, l in answer["ons"] if t + call["len"] < P]
    ons = [(t, min(l, P - t)) for t, l in ons if t < P]
    n_call = sum(1 for t, _ in ons if t < min(call["len"], P) - 1e-6)
    moves = list(moves_a[:max(0, n_call - 1)])
    if len(ons) > n_call:                                 # into the answer: one move from the call's last note
        moves += list(moves_b[:len(ons) - n_call]) or [-1]
    notes = _realize_line(ons, moves, start, mc, win, t0)
    breath = float(hs.get("breath", 3))
    land = [x for x in hs.get("land") or [] if x is not None]
    if land or final:
        mt = _meter()
        if not mt["legacy"]:
            land = sorted(mt["strong"], reverse=True)
        lb = P - S if P >= 2 * S else 0                   # the answer bar (or the cell itself in 1-bar phrases)
        cands = [lb + x for x in (land or [0]) if lb + x < P - 1]
        cands.sort(reverse=True)
        at = None
        for c in cands:                                   # the latest landing beat that leaves room to breathe
            if c <= P - breath - 1 or c == min(cands):
                at = c
                break
        if at is not None:
            notes = _land(notes, mc, t0 + at, final, win, float(hs.get("hold", 0)) or None, breath, t0 + P)
    elif notes:                                           # a riff that doesn't stop: its last note still rests on
        n = notes[-1]                                     # the chord (root, 3rd or 5th)
        ch = mc.chord(n["s"], n["l"])
        st = [(ch["root"] + i) % 12 for i in ch["iv"] if i in (0, 3, 4, 6, 7, 8)]
        if n["k"] % 12 not in st:
            c_ = [x for x in _pitches(st, *win)]
            if c_:
                n["k"] = _nearest(c_, n["k"])
    return notes


def _sequence_shift(mc, a_t0, b_t0):
    """Scale steps between the chord roots where two phrases start (a sequence follows the chords), -3..3."""
    sc = sorted(set(mc.scale))
    ra, rb = chord_at(mc.chords, a_t0)["root"], chord_at(mc.chords, b_t0)["root"]
    if ra == rb or ra not in sc or rb not in sc:
        return 0
    d = (sc.index(rb) - sc.index(ra)) % len(sc)
    return d - len(sc) if d > len(sc) // 2 else d


def _same_harmony(mc, a_t0, b_t0, P):
    for c in mc.chords:
        if a_t0 <= c["start"] < a_t0 + P:
            o = chord_at(mc.chords, b_t0 + c["start"] - a_t0)
            if set(chord_pcs(o)) != set(chord_pcs(c)):
                return False
    return set(chord_pcs(chord_at(mc.chords, a_t0))) == set(chord_pcs(chord_at(mc.chords, b_t0)))


def _user_cell(motif, P, key, mc):
    """A user motif as a rhythm cell + its exact pitches + its moves in scale steps."""
    um = _motif_from_user(motif, P, key)
    ks, k = [], None
    for i, mv in enumerate(um["moves"]):
        k = int(mv[1]) if i == 0 else k + int(mv[1])
        ks.append(k)
    ons = [(float(s_), float(l_)) for s_, l_ in um["ons"]]
    end = max(s_ + l_ for s_, l_ in ons)
    ln = min(P, max(mc.S, int(math.ceil(end / float(mc.S) - 1e-6)) * mc.S))
    sc = _pitches(mc.scale, 0, 127)
    idx = lambda p: min(range(len(sc)), key=lambda j: abs(sc[j] - p))
    moves = [idx(b_) - idx(a_) for a_, b_ in zip(ks, ks[1:])]
    return {"ons": ons, "len": ln, "steps": "user", "w": 1.0}, ks, moves


def _answer_moves(rnd, call, ans, mv_a, m, hs, shape, shift=0):
    """The answer bar's moves: the call's own contour again (a repeat / sequence, `shift` scale steps over), or -
    call and response - a line turning the other way. The first move leads from the call's last note."""
    nc = len(call["ons"])
    na = len(ans["ons"]) if ans else 0
    if not na:
        return []
    if hs.get("answer") == "other":
        return _shape_moves(rnd, na + 1, shape, m, flip=True)
    back = -sum(mv_a[:nc - 1]) + shift                 # back to where the call began (a leap of a 4th at most)
    return [max(-3, min(3, back))] + list(mv_a[:na - 1])


def _hook_candidate(key, chords, g, m, bars, seed, vary, cand, lo, hi, motif=None):
    """One melody: a hook phrase built from a motif, developed through the genre's phrase plan."""
    S = _spb()
    total = int(bars) * S
    hs = _hook_spec(m)
    lm = m.get("length", "until_next")
    cells = _mel_cells(m, S, lm, float(m.get("max_len_steps", 4)))
    P = min(total, S * int(hs.get("phrase_bars", 2)))
    mc = _MelCtx(key, chords, m, lo, hi, total)
    rnd = _rng(seed, "hook", g["name"], vary, cand)
    span = int(hs.get("span", 12))
    center = (lo + hi) // 2 + int(hs.get("tessitura", 0))
    win = (max(lo, center - span // 2), min(hi, center + span - span // 2))
    user_ks = None
    if motif:
        call, user_ks, mv_user = _user_cell(motif, P, key, mc)
    else:
        call = _pick(rnd, cells, vary if cand == 0 else 0)[1]
    if call["len"] >= P:
        ans = None
    elif hs.get("answer") == "other" and len(cells) > 1 and not motif:
        ans = _pick(rnd, [c for c in cells if c is not call])[1]
    else:
        ans = call
    shapes = hs.get("shapes") or HOOK_DEFAULT["shapes"]
    shape = _wpick(rnd, shapes)
    nc = len(call["ons"])
    mv_a = mv_user if motif else _shape_moves(rnd, max(2, nc), shape, m)
    mv_b = _answer_moves(rnd, call, ans, mv_a, m, hs, shape)
    plan = [str(x).upper()[:1] or "A" for x in hs.get("plan") or ["A"]]
    nph = max(1, int(math.ceil(total / float(P))))
    letters = [plan[i % len(plan)] for i in range(nph)]
    if nph > 1 and letters[-1] != "C":
        letters[-1] = "C"
    if motif:
        win = (max(lo, min(win[0], min(user_ks))), min(hi, max(win[1], max(user_ks))))
        start = user_ks[0]
    else:
        start = _nearest(_pitches(chord_pcs(chord_at(chords, 0)), *win), center + int(hs.get("start_offset", 0)))

    def phrase(t0, c_, a_, ma, mb, st, w, final):
        Pp = min(P, total - t0)
        notes = _phrase(mc, Pp, t0, c_, a_, ma, mb, st, w, final, hs)
        if motif and c_ is call:                          # the user's motif keeps its own notes
            for j, n in enumerate(notes[:len(user_ks)]):
                if abs(n["s"] - t0 - call["ons"][j][0]) < 1e-6:
                    n.update(k=user_ks[j], user=True)
        return notes
    out, hook, used = [], None, []
    b_state = None
    for i, L in enumerate(letters):
        t0 = i * P
        final = i == nph - 1
        if hook is None:
            notes = phrase(t0, call, ans, mv_a, mv_b, start, win, final)
            hook = {"t0": t0, "notes": notes}
            L = "A"
        elif L == "A" and not final and _same_harmony(mc, hook["t0"], t0, P):
            notes = [dict(n, s=round(n["s"] - hook["t0"] + t0, 3)) for n in hook["notes"]]   # the hook, exactly
        elif L in ("A", "C", "S"):
            sh = _sequence_shift(mc, hook["t0"], t0) if L == "S" else 0
            st = start
            if sh:
                st = _step_in(mc.cscale(chord_at(chords, t0), False)[0], start, sh)
            notes = phrase(t0, call, ans, mv_a, mv_b, st, win, final)
        elif L == "R":                                    # the hook's rhythm, new notes
            mv = _shape_moves(rnd, max(2, nc), _wpick(rnd, shapes), m)
            notes = phrase(t0, dict(call, steps="r"), ans, mv, _answer_moves(rnd, call, ans, mv, m, hs, shape), start + 2,
                           win, final)
        else:                                             # B: a contrasting idea (new rhythm, the other way, a lift)
            if b_state is None:
                rest = [c for c in cells if c is not call] or cells
                bc = _pick(rnd, rest)[1]
                ba = bc if bc["len"] < P else None
                bsh = "fall" if shape in ("rise", "arch") else "arch"
                bm = _shape_moves(rnd, max(2, len(bc["ons"])), bsh, m)
                b_state = (bc, ba, bm, _answer_moves(rnd, bc, ba, bm, m, dict(hs, answer="other"), bsh))
            bc, ba, bm, bb = b_state
            lift = (min(hi - 4, win[0] + 2), min(hi, win[1] + 3))
            notes = phrase(t0, bc, ba, bm, bb, start + 3, lift, final)
        out += [n for n in notes if n["s"] < total - 1e-6]
        used.append(L)
    return out, {"plan": " ".join(used), "rhythm": call["steps"], "shape": shape,
                 "answer": (hs.get("answer") or "same") if ans is not None else "in the cell"}


def melody(key, chords, genre, bars=4, seed=1, vary=0, motif=None, rng=None, candidates=None, accomp=None):
    """A melody [{s, l, k, v}] built like a hook: a 1-2 bar motif (genre rhythm cell + a contour), answered and
    developed through the genre's phrase plan (melody.hook.plan: A = the hook, S = sequenced to the chords, R = its
    rhythm with new notes, B = a contrasting idea, C = the hook with a cadence), chord tones on strong beats,
    passing tones by step, phrases landing on stable chord tones with space after them. A few seeded candidates
    are scored (fl_musicality) and the best kept (accomp: the chord part's notes, for the semitone-rub check)."""
    g = genre_data(genre)
    m = g.get("melody") or {}
    lo, hi = [int(x) for x in (rng or m.get("range") or [60, 84])[:2]]
    S = _spb()
    total = int(bars) * S
    hs = _hook_spec(m)
    n = max(1, min(12, int(candidates or hs.get("candidates") or 6)))
    best, mt = None, _meter()
    for c in range(n):
        out, info = _hook_candidate(key, chords, g, m, bars, seed, vary, c, lo, hi, motif)
        _finish_melody(out, g, m, key, chords, total, lo, hi, seed, vary, c)
        if n == 1:
            best = (0.0, c, out, info)
            break
        sc = fl_musicality.melody_score(out, chords, S, tuple(mt["strong"]), int(bars), accomp)
        if best is None or sc > best[0] + 1e-9:
            best = (sc, c, out, info)
    sc, c, out, info = best
    for nn in out:
        nn.pop("user", None)
        nn.pop("land", None)
    info = dict(info, form=info.pop("plan"), candidates=n, picked=c + 1,
                range=f"{fl_music.note_name(min(x['k'] for x in out))}-{fl_music.note_name(max(x['k'] for x in out))}"
                if out else None)
    if n > 1:
        info["score"] = round(sc, 2)
    return out, info


def _finish_melody(out, g, m, key, chords, total, lo, hi, seed, vary, cand):
    """Pickups into the phrases, held strong-beat tones resolved, lengths made monophonic, phrase dynamics."""
    out.sort(key=lambda n: n["s"])
    S = _spb()
    hs = _hook_spec(m)
    P = min(total, S * int(hs.get("phrase_bars", 2)))
    scale = scale_pcs(key, m.get("scale", "key"))
    kp = scale_pcs(key, "key")
    ext9 = float(((g.get("harmony") or {}).get("voicing") or {}).get("extensions", {}).get("9", 0)) > 0
    _pickups(out, P, total, chords, scale, kp, lo, hi, _rng(seed, "melody-pickup", g["name"], vary, cand),
             float(m.get("pickup", 0.25)))
    _resolve_strong(out, chords, lo, hi, ext9)
    rnd = _rng(seed, "melody-vel", vary)
    vlo, vhi = [int(x) for x in (m.get("vel") or [84, 104])[:2]]
    mid = (vlo + vhi) / 2.0
    for i, n in enumerate(out):
        nxt = out[i + 1]["s"] if i + 1 < len(out) else total
        n["l"] = round(max(0.5, min(n["l"], nxt - n["s"], total - n["s"])), 3)
        pos = (n["s"] % P) / float(P)                     # a phrase leans in, then eases off at its end
        shape = 3 if pos < 0.5 else -3 if n.get("land") else 0
        n["v"] = _clampv(min(vhi, max(vlo, mid + rnd.randint(-(vhi - vlo) // 3, (vhi - vlo) // 3) + shape
                                      + (5 if _on_beat(n["s"]) else 0))))



def _pickups(out, unit, total, chords, scale, key_pcs, lo, hi, rnd, chance):
    """A short lead-in (one or two notes, by step) into the next phrase where the melody leaves room before it."""
    if chance <= 0 or len(out) < 2:
        return
    add = []
    for b in range(unit, total, unit):
        nxt = next((n for n in out if n["s"] >= b - 1e-6), None)
        prv = [n for n in out if n["s"] < b]
        if nxt is None or not prv or nxt["s"] - b > 1 or rnd.random() >= chance:
            continue
        end = prv[-1]["s"] + prv[-1]["l"]
        room = b - max(end, prv[-1]["s"] + 2)
        if room < 2:
            continue
        ch = chord_at(chords, b - 2)
        cs = _pitches(chord_scale(scale, ch, key_pcs, True), lo, hi)
        d = 1 if rnd.random() < 0.5 else -1
        p = _step_in(cs, nxt["k"], d)
        if p == nxt["k"]:
            continue
        if room >= 4 and rnd.random() < 0.5:
            p2 = _step_in(cs, p, d)
            if p2 != p:
                add.append({"s": b - 4, "l": 2.0, "k": p2, "v": 0})
        add.append({"s": b - 2, "l": 2.0, "k": p, "v": 0})
    out += add
    out.sort(key=lambda n: n["s"])
    for i in range(len(out) - 1):                          # monophonic: the note before a pickup ends there
        if out[i]["s"] + out[i]["l"] > out[i + 1]["s"]:
            out[i]["l"] = max(0.5, out[i + 1]["s"] - out[i]["s"])



def _resolve_strong(out, chords, lo, hi, ext9=False):
    """A held non-chord tone on beat 1 or 3 must resolve by step into a chord tone (appoggiatura / suspension);
    otherwise it becomes the nearest chord tone (the user's own motif notes are left alone)."""
    for i, n in enumerate(out):
        if n.get("user") or not _strong(n["s"]) or n["l"] < 3:
            continue
        ch = chord_at(chords, n["s"])
        ct = set(chord_pcs(ch)) | ({(ch["root"] + 2) % 12} if ext9 else set())
        if n["k"] % 12 in ct:
            continue
        nx = out[i + 1] if i + 1 < len(out) else None
        if nx and nx["s"] - (n["s"] + n["l"]) <= 2 and 0 < abs(nx["k"] - n["k"]) <= 2 and \
                nx["k"] % 12 in set(chord_pcs(chord_at(chords, nx["s"]))):
            continue
        cands = _pitches(chord_pcs(ch), lo, hi)
        if cands:
            n["k"] = _nearest(cands, n["k"])


# ============================================================================= counter-melody, voicings, layers
_CLASH = {1, 2, 6, 11}


def _sounding(notes, s):
    return [n for n in notes if n["s"] <= s + 1e-6 < n["s"] + n["l"]]


def counter_melody(mel, chords, key, genre, seed=1, vary=0, mode=None, register=None, bars=None, accomp=None):
    """A counter line answering `mel`: fills (short phrases in the melody's rests and long notes, contrary motion,
    chord tones on arrival) or guide (sustained 3rds/7ths moving by step under/over the melody). accomp: the
    chord part's notes ({s, l, k}) - the line keeps a semitone away from them too."""
    acc = sorted(accomp or [], key=lambda n: n["s"])

    def rub(p, s, ln):
        for a in acc:
            if a["s"] >= s + ln:
                break
            if a["s"] + a["l"] > s + 0.5 and abs(a["k"] - p) % 12 in (1, 11) and abs(a["k"] - p) < 12:
                return True
        return False
    g = genre_data(genre)
    c = g.get("counter") or {}
    mode = mode or c.get("mode", "fills")
    dens = float(c.get("density", 0.5))
    S = _spb()
    total = int(bars) * S if bars else max([ch["start"] + ch["len"] for ch in chords] + [n["s"] + n["l"] for n in mel] + [S])
    rnd = _rng(seed, "counter", g["name"], vary, mode)
    pitches = sorted(n["k"] for n in mel) or [72]
    center = pitches[len(pitches) // 2]
    mv = sorted(n["v"] for n in mel)[len(mel) // 2] if mel else 90     # the counter sits under the lead in level
    gv = (max(36, min(60, mv - 26)), max(44, min(74, mv - 14)))
    fv = (max(40, min(66, mv - 22)), max(48, min(84, mv - 8)))
    reg = register or c.get("register", "below")
    if isinstance(reg, (list, tuple)):
        lo, hi = int(reg[0]), int(reg[1])
    elif reg == "above":                                  # a descant, not a whistle: tops out around E6
        hi = min(88, center + 15)
        lo = min(center + 3, hi - 9)
    else:                                                 # under the lead, but clear of the bass's mud zone
        lo = max(52, center - 19)
        hi = max(lo + 9, center - 4)
    scale = scale_pcs(key, "key")
    out = []
    if mode == "guide":
        prev = (lo + hi) // 2
        for ch in chords:
            ivs = ch["iv"]
            third = next((i for i in ivs if i in (3, 4)), next((i for i in ivs if i in (2, 5)), 7))
            sev = next((i for i in ivs if i in (10, 11, 9)), 7)
            cands = _pitches([(ch["root"] + third) % 12, (ch["root"] + sev) % 12], lo, hi)
            if not cands:
                continue
            segs = [(ch["start"], ch["len"])]
            if ch["len"] >= 2 * S and dens > 0.3:
                segs = [(ch["start"], ch["len"] / 2), (ch["start"] + ch["len"] / 2, ch["len"] / 2)]
            for s, ln in segs:
                if s >= total:
                    continue
                snd = {n["k"] for n in _sounding(mel, s)}
                opts = sorted(cands, key=lambda p: (p in snd or any((abs(p - q) % 12) in _CLASH for q in snd),
                                                    rub(p, s, ln), abs(p - prev), p))
                ok = [q for q in opts if not (q in snd or any((abs(q - x) % 12) in _CLASH for x in snd) or rub(q, s, ln))]
                p = opts[0] if (s == ch["start"] or len(opts) < 2) else next((q for q in ok if q != prev), opts[0])
                out.append({"s": s, "l": round(min(ln, total - s) - 0.25, 3), "k": p, "v": _clampv(rnd.randint(*gv))})
                prev = p
        return out, {"mode": "guide", "register": [lo, hi], "notes": len(out)}
    cover = [False] * (total + 1)
    for n in mel:
        for t in range(int(n["s"]), min(total, int(math.ceil(n["s"] + n["l"])))):
            cover[t] = True
    gaps, t = [], 0
    while t < total:
        if not cover[t]:
            a = t
            while t < total and not cover[t]:
                t += 1
            if t - a >= 3:
                gaps.append((a, t))
        else:
            t += 1
    for n in mel:                                         # the later half of long held notes
        if n["l"] >= 6:
            a = int(n["s"] + n["l"] / 2)
            b = int(n["s"] + n["l"])
            if not any(x <= a < y for x, y in gaps):
                gaps.append((a, b))
    gaps.sort()
    cur = hi - 2 if reg != "above" else lo + 2
    for a, b in gaps:
        if rnd.random() > min(1.0, dens + 0.35):
            continue
        k = max(2 if b - a >= 4 else 1, min(4, int(round((b - a) / 2 * dens + 0.5))))
        before = [n for n in mel if n["s"] < a]
        d = -1 if (len(before) >= 2 and before[-1]["k"] > before[-2]["k"]) else 1   # contrary to the melody
        if cur >= hi - 2:
            d = -1
        elif cur <= lo + 2:
            d = 1
        rate = 2.0 if (b - a) >= 2 * k else 1.0
        prevp = None
        for i in range(k):
            s = a + i * rate
            if s >= b or s >= total:
                break
            ch = chord_at(chords, s)
            cs = _pitches(chord_scale(scale, ch, scale), lo, hi)
            ct = _pitches(chord_pcs(ch), lo, hi)
            if not cs:
                break
            if prevp is None:
                p = _nearest(ct, cur) if ct else _nearest(cs, cur)
            else:
                p = _step_in(cs, prevp, d * (2 if rnd.random() < 0.3 else 1))
                if i == k - 1 and ct:
                    p = _nearest(ct, p, 0)
            snd = [n["k"] for n in _sounding(mel, s)]
            tries = 0
            while (snd and any((abs(p - q) % 12) in _CLASH for q in snd) or rub(p, s, rate)) and tries < 4:
                p = _step_in(cs, p, d or 1)
                tries += 1
            ln = rate if i < k - 1 else min(b - s, rate * 2)
            out.append({"s": round(s, 3), "l": round(max(0.5, ln - 0.2), 3), "k": p, "v": _clampv(rnd.randint(*fv))})
            prevp = p
        if prevp is not None:
            cur = prevp
    if len(out) < 3 and chords:                           # a melody with no room to answer: a guide-tone line
        out, info = counter_melody(mel, chords, key, genre, seed, vary, "guide", [lo, hi], bars, accomp)
        info["mode"] = "guide (the melody leaves no gaps to answer)"
        return out, info
    return out, {"mode": "fills", "register": [lo, hi], "notes": len(out), "gaps": len(gaps)}


def identify_chord(pitches, key=None):
    """MIDI pitches sounding together -> chord dict (root, quality) or None."""
    w = [0.0] * 12
    for p in pitches:
        w[p % 12] += 1
    name = fl_music.name_chord(w, min(pitches) % 12 if pitches else None)
    if not name:
        return None
    base = name.split("/")[0]
    try:
        return chord_from_token(base, key or "C major")
    except ValueError:
        return None


def revoice(notes, key, style="triad", register=None, ext=None, tol=0.25):
    """Re-voice an existing chord part (notes in steps, {s, l, k, v}): same rhythm and chords, genre voicing with
    smooth voice leading. -> (notes, {groups, movement_before, movement_after})."""
    groups = []
    for n in sorted(notes, key=lambda n: (n["s"], n["k"])):
        if groups and abs(groups[-1][0]["s"] - n["s"]) <= tol:
            groups[-1].append(n)
        else:
            groups.append([n])
    allp = [n["k"] for n in notes] or [60]
    reg = register or [min(allp), max(max(allp), min(allp) + 14)]
    out, prev, mv_b, mv_a, prev_old = [], None, 0.0, 0.0, None
    cache = {"_seed": 7}
    kept = 0
    for grp in groups:
        old = sorted(n["k"] for n in grp)
        if len(old) < 2:
            out += [dict(n) for n in grp]
            kept += 1
            continue
        ch = identify_chord(old, key)
        if ch is None:
            out += [dict(n) for n in grp]
            kept += 1
            prev_old = old
            continue
        tones = _chord_tones(ch, style, ext or {}, key or "C major", cache) if style != "keep" else ch["iv"]
        if style in ("triad",) and len(old) >= 4:
            tones = sorted(set(list(ch["iv"]) + [i for i in ((p - ch["root"]) % 12 for p in old) if i not in ch["iv"]][:1]))
        v = voice(ch, tones, style, reg, prev)
        if prev_old:
            mv_b += _vl_cost(prev_old, old)
        if prev:
            mv_a += _vl_cost(prev, v)
        prev, prev_old = v, old
        L = max(n["l"] for n in grp)
        vel = max(n["v"] for n in grp)
        s = min(n["s"] for n in grp)
        for i, p in enumerate(v):
            src = grp[min(i, len(grp) - 1)]
            out.append(dict(src, s=s, l=L, k=p, v=vel))
    return out, {"groups": len(groups), "kept_as_is": kept, "movement_before": round(mv_b, 1), "movement_after": round(mv_a, 1)}


LAYER_ROLES = {
    "pad": {"kind": "sustain", "register": [52, 74], "voices": 4, "vel": [58, 72], "under": 30},
    "strings": {"kind": "sustain", "register": [55, 79], "voices": 3, "vel": [62, 80], "octave_top": True, "under": 25},
    "choir": {"kind": "sustain", "register": [55, 74], "voices": 3, "vel": [58, 74], "under": 35, "climax": True},
    "arp": {"kind": "arp", "register": [60, 84], "vel": [70, 92], "under": 25},
    "bell": {"kind": "hits", "register": [72, 96], "vel": [64, 86], "under": 25},
    "pluck": {"kind": "arp", "register": [64, 86], "vel": [72, 94], "rate": 2.0, "under": 25},
    "brass": {"kind": "stabs", "register": [50, 72], "vel": [84, 104], "under": 20},
    "organ": {"kind": "sustain", "register": [48, 72], "voices": 4, "vel": [64, 80], "under": 30},
    "low strings": {"kind": "low", "register": [36, 57], "vel": [60, 78], "under": 20},
}
# "under": in support level (layers under a solo part / level="support") the role's top velocity is this many percent
# under the song's median velocity; "climax": in support level the role plays only where the music is loudest.
LAYER_ALIASES = {"low": "low strings", "lowstrings": "low strings", "cello": "low strings", "cellos": "low strings",
                 "celli": "low strings", "cello bass": "low strings", "basses": "low strings", "double bass":
                 "low strings", "contrabass": "low strings", "bass strings": "low strings", "string bass":
                 "low strings", "low string": "low strings", "voices": "choir", "string": "strings",
                 "pads": "pad", "bells": "bell", "arpeggio": "arp", "horns": "brass", "counter melody": "counter",
                 "counter-melody": "counter"}


def layer_role(name):
    """A layer role name as the composer knows it ('cello' -> 'low strings'), or ValueError."""
    r = re.sub(r"\s+", " ", str(name or "").strip().lower().replace("_", " "))
    r = LAYER_ALIASES.get(r, r)
    if r != "counter" and r not in LAYER_ROLES:
        raise ValueError(f"layer role must be one of {', '.join(sorted(LAYER_ROLES) + ['counter'])} (got '{name}')")
    return r


def _follow_at(follow, s):
    """The song's bass pitch sounding at step s (follow: [(step, pitch)] sorted), or None."""
    hit = None
    for t, p in follow or []:
        if t <= s + 1e-6:
            hit = p
        else:
            break
    return hit


def _clip_spans(notes, spans):
    """Keep layer notes inside the step spans [(a, b)]: a note starting in a span is kept (cut at the span's end), a
    held note entering a span starts at its start when at least a step of it is left. Short notes (an arp's
    0.9-step 16ths) count by where they start, never by a minimum length (they were all dropped before)."""
    out = []
    for n in notes:
        s, e = float(n["s"]), float(n["s"]) + float(n["l"])
        for a, b in spans:
            if a - 1e-6 <= s < b - 1e-6:                  # starts inside: keep, cut at the span's end
                L = min(e, b) - s
                if e > b + 1e-6:
                    L = max(min(float(n["l"]), 0.25), L - 0.25)
                out.append(dict(n, l=round(L, 3)))
                break
            s2, e2 = max(s, a), min(e, b)
            if s < a and e2 - s2 >= 1.0:                  # a held note entering the span
                out.append(dict(n, s=round(s2, 3), l=round(e2 - s2 - (0.25 if e2 < e - 1e-6 else 0), 3)))
    return out


def layer(role, chords, key, genre=None, seed=1, vary=0, occupied=None, bars=None, tempo=None, mel=None, follow=None,
          register=None, vel=None, where=None):
    """A layer that follows the song's chords in a free register: pad/strings/choir/organ (sustained, voice-led),
    low strings (the harmony's bass note in the cello register: the song's own bass when `follow` gives it,
    [(step, pitch)], else the chord's bass / root), arp/pluck (chord tones as an arpeggio), bell (sparse high chord
    tones), brass (stabs on chord changes), counter (counter_melody). occupied: {role: [lo, hi]} of the existing parts
    (register gaps are used). register / vel: [lo, hi] overrides; where: [(a, b)] step spans to play in."""
    role = layer_role(role)
    if role == "counter":
        return counter_melody(mel or [], chords, key, genre, seed, vary, bars=bars)
    spec = LAYER_ROLES[role]
    lo, hi = spec["register"]
    occ = occupied or {}
    mel_r = occ.get("melody")
    if register:
        lo, hi = int(register[0]), int(register[1])
    elif mel_r and spec["kind"] in ("sustain", "stabs"):  # under the melody; never above it into a shrill register
        if mel_r[0] - 1 - lo >= 10:
            hi = min(hi, mel_r[0] - 1)
        else:
            hi = min(hi, mel_r[0] + 4)
            lo = max(40, min(lo, hi - 15))
    elif mel_r and spec["kind"] == "hits":                # bells sparkle above the melody when there's room
        if mel_r[1] + 13 <= 98:
            lo, hi = max(lo, mel_r[1] + 1), max(hi, mel_r[1] + 14)
    elif mel_r and spec["kind"] == "arp" and lo < mel_r[1] and hi > mel_r[0]:
        if mel_r[0] - 13 >= 50:
            lo, hi = mel_r[0] - 13, mel_r[0] - 1
        elif mel_r[1] + 13 <= 96:
            lo, hi = mel_r[1] + 1, mel_r[1] + 13
    bass_r = occ.get("bass")
    if bass_r and spec["kind"] != "low" and not register:
        lo = max(lo, bass_r[1] + 3)
    hi = min(max(hi, lo + 12), 98 if spec["kind"] == "hits" else 88)
    lo = min(lo, hi - 12)
    mt = _meter()
    S = mt["spb"]
    total = int(bars) * S if bars else max(c["start"] + c["len"] for c in chords)
    rnd = _rng(seed, "layer", role, vary)
    vlo, vhi = [int(x) for x in (vel or spec["vel"])[:2]]
    out, prev = [], None
    for ch in chords:
        if ch["start"] >= total:
            break
        L = min(ch["len"], total - ch["start"])
        if spec["kind"] == "sustain":
            n = spec.get("voices", 3)
            tones = sorted(set(ch["iv"]))
            if len(tones) > n and 7 in tones:
                tones.remove(7)
            tones = tones[:n]
            v = voice(ch, tones, "triad", [lo, hi], prev)
            if spec.get("octave_top") and v and v[-1] + 12 <= hi + 12:
                v = v + [v[0] + 12] if v[0] + 12 not in v else v
            prev = v
            for p in v:
                out.append({"s": ch["start"], "l": round(L - 0.25, 3), "k": p, "v": _clampv(rnd.randint(vlo, vhi))})
        elif spec["kind"] == "low":                       # the bass of the harmony, re-struck where the song's bass moves
            segs = [ch["start"]] + [t for t in range(int(math.ceil(ch["start"] / S)) * S, ch["start"] + L, S)
                                    if t > ch["start"] and follow]
            pts = []
            for t in segs:
                fp = _follow_at(follow, t)
                pc = fp % 12 if fp is not None else (ch.get("bass") if ch.get("bass") is not None else ch["root"])
                if pts and pts[-1][1] % 12 == pc:
                    continue
                tgt = pts[-1][1] if pts else (prev[0] if prev else (fp if fp is not None else (lo + hi) // 2))
                pts.append((t, _place(pc, tgt, lo, hi)))
            for j, (t, p) in enumerate(pts):
                e = pts[j + 1][0] if j + 1 < len(pts) else ch["start"] + L
                out.append({"s": t, "l": round(e - t - 0.25, 3), "k": p, "v": _clampv(rnd.randint(vlo, vhi))})
            prev = [pts[-1][1]] if pts else prev
        elif spec["kind"] == "arp":
            tones = _pitches(chord_pcs(ch), lo, hi)
            if not tones:
                continue
            rate = max(0.125, float(spec.get("rate") or (2.0 if (tempo or 100) >= 125 else 1.0)))
            seq = tones[:4] + tones[1:3][::-1] if len(tones) >= 4 else tones + tones[-2:0:-1]
            t, i = 0.0, 0
            while t < L:
                acc = (t % 4 == 0) if mt["legacy"] else _on_beat(ch["start"] + t)
                out.append({"s": round(ch["start"] + t, 3), "l": rate * 0.9, "k": seq[i % len(seq)],
                            "v": _clampv(rnd.randint(vlo, vhi) + (6 if acc else 0))})
                t += rate
                i += 1
        elif spec["kind"] == "hits":
            tones = _pitches(chord_pcs(ch), lo, hi)
            if not tones:
                continue
            offs = ((0, 10) if L >= 16 else (0,)) if mt["legacy"] else \
                ((0, mt["beats"][-1]) if L >= S and len(mt["beats"]) > 1 else (0,))
            for off in offs:
                if off < L:
                    out.append({"s": ch["start"] + off, "l": min(6.0, L - off), "k": tones[rnd.randrange(len(tones))],
                                "v": _clampv(rnd.randint(vlo, vhi))})
        else:
            v = voice(ch, sorted(set(ch["iv"]))[:3], "triad", [lo, hi], prev)
            prev = v
            sec = 6 if mt["legacy"] else (mt["strong"][1] if len(mt["strong"]) > 1 else mt["beats"][-1])
            for off in (0, sec) if L >= max(8, sec + 2) and sec > 0 else (0,):
                for p in v:
                    out.append({"s": ch["start"] + off, "l": 1.5, "k": p, "v": _clampv(rnd.randint(vlo, vhi))})
    info = {"role": role, "register": [lo, hi], "notes": len(out)}
    if where:
        out = _clip_spans(out, where)
        info["notes"] = len(out)
    return out, info


# ============================================================================= project reading
ROLE_WORDS = (
    ("openhat", ("open hat", "openhat", "open hh", "ohh", "open")),
    ("kick", ("kick", "kik", "bassdrum", "bass drum", "bd ")),
    ("snare", ("snare", "snr")),
    ("clap", ("clap", "clp", "snap")),
    ("rim", ("rim",)),
    ("hat", ("hat", "hh", "hihat", "hi-hat", "hi hat", "closed")),
    ("ride", ("ride",)),
    ("crash", ("crash", "cymbal")),
    ("tom", ("tom", "taiko")),
    ("shaker", ("shaker", "shkr", "tamb")),
    ("perc", ("perc", "conga", "bongo", "cowbell", "woodblock", "clave")),
    ("fx", ("riser", "sweep", "impact", "uplifter", "whoosh", "reverse", "fx")),
    ("low strings", ("low string", "cello", "celli", "contrabass", "double bass", "bass string", "string bass")),
    ("bass", ("808", "bass", "sub", "reese")),
    ("pad", ("pad", "atmos", "drone", "texture")),
    ("strings", ("string", "violin", "cello", "viola", "orchestra")),
    ("choir", ("choir", "voice", "vox")),
    ("counter", ("counter",)),
    ("arp", ("arp",)),
    ("bell", ("bell", "celest", "glock", "music box", "marimba", "kalimba", "vibes")),
    ("melody", ("lead", "melody", "pluck", "flute", "sax", "trumpet", "whistle", "horn", "solo", "vocal chop")),
    ("chords", ("chord", "keys", "piano", "rhodes", "epiano", "e-piano", "wurli", "organ", "guitar", "stab", "comp")),
)
KIT_PLUGINS = ("fpc", "drumaxx", "battery", "drumpad", "drum pad", "ezdrummer", "superior drummer", "addictive drums",
               "slicex", "fruity slicer", "drumgod", "drum kit")
MELODIC_FALLBACK = {"chords": ("chords", "pad", "organ", "strings"), "melody": ("melody", "bell", "arp", "lead"),
                    "counter": ("counter",), "bass": ("bass",), "pad": ("pad", "strings", "choir"),
                    "strings": ("strings", "pad"), "choir": ("choir", "pad"), "arp": ("arp",), "bell": ("bell",),
                    "pluck": ("pluck", "arp"), "brass": ("brass",), "organ": ("organ",),
                    "low strings": ("low strings",)}
CLONE_FAMILY = {"low strings": ("low strings", "strings", "pad")}   # what a clone for the role starts from
JOURNAL_ROLE = {"low strings": "bass"}                   # the song journal's role (what flh_score / the mixer read)
HARMONIC_ROLES = ("chords", "pad", "bass", "strings", "organ", "choir", "arp", "brass", "low strings")
PART_NAMES = {"drums": "Drums", "chords": "Chords", "bass": "Bass", "melody": "Melody", "counter": "Counter"}


def _text(d, e):
    return H["_text16"](H["_payload"](d, e))


def channels_of(d, ev=None):
    """{index: {name, plugin, type (0 sampler, 2 plugin, 4 audio clip, 5 automation), sample}} from the channel
    blocks themselves (sampler channels included)."""
    ev = ev if ev is not None else H["_events"](d)
    out, cur = {}, None
    EVC, EVP, EVN, EVS = H["EV_CHANNEL"], H["EV_PLUGIN_NAME"], H["EV_DISPLAY_NAME"], H.get("EV_SAMPLE_PATH", 196)
    for e in ev:
        eid = e["id"]
        if eid == EVC:
            cur = struct.unpack("<H", H["_payload"](d, e))[0]
            out[cur] = {"index": cur, "name": None, "plugin": None, "type": None, "sample": None}
        elif cur is None:
            continue
        elif eid == H.get("EV_CHANNEL_END", 51):
            cur = None
        elif eid == 21 and out[cur]["type"] is None:
            out[cur]["type"] = H["_payload"](d, e)[0]
        elif eid == EVP and out[cur]["plugin"] is None:
            out[cur]["plugin"] = _text(d, e) or None
        elif eid == EVN and out[cur]["name"] is None:
            out[cur]["name"] = _text(d, e) or None
        elif eid == EVS and out[cur]["sample"] is None:
            out[cur]["sample"] = os.path.basename((_text(d, e) or "").replace("\\", "/")) or None
    return out


def _role_from_text(text):
    t = " " + text.lower() + " "
    if any(k in t for k in KIT_PLUGINS):
        return "kit"
    for role, words in ROLE_WORDS:
        if any(w in t for w in words):
            return role
    return None


def detect_roles(chans, notes_by_ch, P, registry=None):
    """{channel: role} - compose registry first, then channel/plugin/sample names, then the notes."""
    roles = {}
    reg = {}
    for r, c in ((registry or {}).get("channels") or {}).items():
        if isinstance(c, list):
            c = c[0]
        try:
            reg[int(c)] = r
        except (TypeError, ValueError):
            pass
    fxc = (registry or {}).get("fx_channels")
    for c in (fxc.values() if isinstance(fxc, dict) else []):   # the arrangement's FX samplers ('Sub Drop' is no
        try:                                                     # bass, 'Reverse Crash' no crash)
            reg.setdefault(int(c), "fx")
        except (TypeError, ValueError):
            pass
    for ch, info in chans.items():
        if info.get("type") in (4, 5):                  # audio clip / automation channels hold no notes
            continue
        if ch in reg:
            roles[ch] = reg[ch]
            continue
        r = _role_from_text(" ".join(str(x or "") for x in (info.get("name"), info.get("plugin"), info.get("sample"))))
        ns = notes_by_ch.get(ch) or []
        if r is None and ns:
            ks = sorted(n[4] for n in ns)
            med = ks[len(ks) // 2]
            starts = {}
            for n in ns:
                starts[n[0]] = starts.get(n[0], 0) + 1
            poly = sum(starts.values()) / max(1, len(starts))
            if len(set(ks)) <= 2 and statistics.median(n[3] for n in ns) <= P // 2:
                r = "perc"
            elif med <= 47 and poly <= 1.3:
                r = "bass"
            elif poly >= 2:
                r = "chords"
            elif med >= 57:
                r = "melody"
            else:
                r = "chords"
        roles[ch] = r or "unknown"
    return roles


def _is_drum(role):
    return role in DRUM_PARTS or role in ("kit", "fx")


def _src(project):
    """The file to read: the project, or its shadow copy (project + edits queued for a lazy FL reload)."""
    try:
        if H.get("_pending_get") and H["_pending_get"](project):
            return H["_shadow"](project)[0]
    except Exception:
        pass
    return project


def project_view(project):
    """Everything the composer needs from a project in one read (queued edits included)."""
    src = _src(project)
    d = H["_load"](src)
    ev = H["_events"](d)
    P = H["_ppq"](d)
    chans = channels_of(d, ev)
    pats = H["_patterns"](d, ev)
    tempo = next((struct.unpack("<I", H["_payload"](d, e))[0] / 1000 for e in ev if e["id"] == H["EV_TEMPO"]), 120.0)
    try:
        bpb = H["_qbar"](H["flp_project_info"](src))
    except Exception:
        bpb = 4
    try:
        clips = H["flp_playlist"](src)["clips"]
    except Exception:
        clips = []
    notes_by_ch, tmpl = {}, {}
    for pid, p in pats.items():
        for r in p["notes"]:
            notes_by_ch.setdefault(r[2], []).append(r)
            tmpl.setdefault(r[2], r)
    jr = _journal(project)
    reg = jr.get("compose") or {}
    roles = detect_roles(chans, notes_by_ch, P, reg)
    used_tracks = {c["track"] for c in clips}
    end_bar = max([c["bar"] + c["length_bars"] for c in clips] + [1.0])
    offs = flh_score.sounding_offsets(jr)                 # pitched samplers: the song as it sounds
    try:
        song, tempos = H["_song"](d, ev)
    except Exception:
        song, tempos = [], []
    try:
        meters = [tuple(int(x) for x in m_) for m_ in H["_meter_map"](d, ev)]
    except Exception:
        meters = [(0, 4, 4)]
    if offs:
        song = [(n[0], n[1], n[2], max(0, n[3] - offs[n[2]]), n[4]) if n[2] in offs else n for n in song]
    sections = [s for s in (jr.get("sections") or []) if s.get("name") and s.get("bar") is not None]
    if not sections:
        sections = _sections_from_clips(clips, pats)
    return {"project": project, "src": src, "d": d, "ev": ev, "P": P, "bpb": bpb, "tempo": tempo, "channels": chans,
            "patterns": pats, "clips": clips, "notes_by_ch": notes_by_ch, "templates": tmpl, "roles": roles,
            "journal": jr, "registry": reg, "end_bar": int(math.ceil(end_bar - 1e-6)), "offsets": offs,
            "free_track": max(used_tracks, default=0) + 1, "song": song, "sections": sections,
            "meters": meters or [(0, 4, 4)], "tempos": tempos,
            "empty": not any(p["notes"] for p in pats.values()) and not clips}


def _sections_from_clips(clips, pats):
    """Sections from pattern clips named like '<Section> <Part>' (what flp_compose writes) or plain names."""
    out = []
    for c in sorted((c for c in clips if c.get("kind", "pattern") == "pattern"), key=lambda c: c["bar"]):
        nm = str(c.get("name") or "")
        m = re.match(r"^(.*?)\s+(?:[A-F]\s+)?(drums|bass|808|sub|chords|melody|counter|pad|strings|arp|bell|choir|"
                     r"pluck|brass|organ|low strings|fill|riser|drums \(\w+\))$", nm, re.I)
        sec = m.group(1) if m else None
        if not sec:
            continue
        if out and out[-1]["name"] == sec and c["bar"] < out[-1]["end_bar"] + 0.01:
            out[-1]["end_bar"] = max(out[-1]["end_bar"], c["bar"] + c["length_bars"])
            continue
        if any(o["name"] == sec and o["bar"] <= c["bar"] < o["end_bar"] for o in out):
            continue
        out.append({"name": sec, "bar": c["bar"], "end_bar": c["bar"] + c["length_bars"]})
    return [o for o in out if o["end_bar"] > o["bar"] + 1e-6]


def _store():
    import flh_store
    return flh_store


def _journal(project):
    try:
        return _store().journal_get(project)
    except Exception:
        return {}


def _journal_set(project, data, res=None):
    """Write the song memory; a failure is said in res["journal_warning"] (the next compose can't find these parts
    by role without it)."""
    try:
        _store().journal_update(project, set=data)
        return True
    except Exception as e:
        if res is not None:
            res["journal_warning"] = (f"the song journal wasn't updated ({type(e).__name__}: {str(e)[:100]}): the "
                                      "notes are written, but later tools won't know these parts' roles")
        return False


def occurrences(view, section):
    if not section:
        return []
    low = str(section).strip().lower()
    aud = [(a["start_bar"], a["end_bar"]) for a in ((view.get("registry") or {}).get("auditions") or {}).values() if a]
    secs = sorted((s for s in view["sections"] if not any(x <= s["bar"] < y for x, y in aud)), key=lambda s: s["bar"])
    out = [s for s in secs if s["name"].lower() == low] or [s for s in secs if s["name"].lower().startswith(low)]
    res = []
    for s in out:
        nxt = next((t["bar"] for t in secs if t["bar"] > s["bar"]), None)
        end = s.get("end_bar") or nxt or s["bar"] + 8
        res.append({"name": s["name"], "bar": float(s["bar"]), "end_bar": float(end)})
    return res


_TL = threading.local()                                 # per call: notes about what the read saw (_reads_fresh)


def _reads_fresh(fn):
    """Public composer entry points: a note from _fresh (FL busy / didn't save, so the file on disk was read) goes
    into the outermost result as read_note."""
    @functools.wraps(fn)
    def run(*a, **kw):
        depth = getattr(_TL, "depth", 0)
        if not depth:
            _TL.notes = []
        _TL.depth = depth + 1
        try:
            r = fn(*a, **kw)
        finally:
            _TL.depth = depth
        if not depth and isinstance(r, dict) and getattr(_TL, "notes", None):
            r["read_note"] = "; ".join(dict.fromkeys(_TL.notes))
        return r
    return run


def _fresh_note(text):
    if getattr(_TL, "depth", 0):
        _TL.notes.append(text)


def _fresh(project):
    """If FL has this project open, have FL save first so the composer reads what the user sees. FL is only asked
    when nothing else drives it (the global FL lock, taken without waiting): during a render / finalize / any FL job
    the file on disk is read and the result says so (read_note).
    PC-VERIFY: FL saves the open project and the file mtime changes before the composer reads it."""
    if getattr(_TL, "scratch", None) and _same_path(project, _TL.scratch):
        return None                                       # a scratch copy FL never had open (flh_new_song)
    try:
        if not (H.get("IS_WINDOWS", os.name == "nt") and H["fl_running"]()):
            return None
        guess, _ = H["_open_project_guess"]()
        if not guess or not _same_path(guess, project):
            return None
    except Exception:
        return None
    lock = H.get("_FL_LOCK")
    if lock is not None and not lock.acquire(blocking=False):
        busy = ""
        try:
            busy = f" ({H['_fl_busy_with']()})" if H.get("_fl_busy_with") else ""
        except Exception:
            pass
        note = (f"read the file on disk: FL is busy{busy}, so it wasn't asked to save first - changes made in FL "
                "since its last save aren't in this read")
        _fresh_note(note)
        return note
    try:
        if _stale_why(project):                           # FL holds an OLDER copy (a reload failed): never ask it
            note = ("read the file on disk without asking FL to save: the edited file is newer than the copy FL has "
                    "open (FL's last reload of it failed), so saving in FL would undo those edits - "
                    f"fl_open('{project}') loads the file into FL")
            _fresh_note(note)
            return note
        ok = H["_save_in_fl_and_wait"](project, stop=lambda: False)   # we hold FL: nothing else needs it meanwhile
        if ok is False:
            note = "FL didn't save in time, so the file on disk was read (changes made in FL since its last save aren't in it)"
            _fresh_note(note)
            return note
    except Exception as e:
        note = f"FL couldn't be asked to save first ({type(e).__name__}), so the file on disk was read"
        _fresh_note(note)
        return note
    finally:
        if lock is not None:
            lock.release()
    return None


def _stale_why(project):
    """The main file's message when FL holds an older copy of `project` than the file on disk, else None."""
    fn = H.get("_stale_block")
    try:
        return fn(project) if callable(fn) else None
    except Exception:
        return None


class scratch:
    """with scratch(path): composer writes to `path` (a brand-new file in a scratch folder that FL can't have open)
    go straight into the file - the open-project guard can't vouch for a file FL doesn't know (E2E-9)."""
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.prev = getattr(_TL, "scratch", None)
        _TL.scratch = self.path
        return self

    def __exit__(self, *exc):
        _TL.scratch = self.prev
        return False


def _same_path(a, b):
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except Exception:
        return False


def _inserts():
    """{channel: mixer insert} from the running FL (bridge), else {} (files don't say reliably)."""
    try:
        if not H["fl_running"]():                                # FL closed: no bridge to ask (PC 2026-10-22: the
            return {}                                            # arrangement waited 15 s on a reply that never came)
        st = H["fl_state"]()
        return {c["index"]: c.get("insert") for c in st.get("channels", []) if c.get("insert") is not None}
    except Exception:
        return {}


def _bar_tick(view, bar):
    """Song bar (1-based, fractional) -> song tick through the project's meter map (4/4 songs: the plain formula)."""
    P = view["P"]
    ms = view.get("meters") or [(0, 4, 4)]
    if len(ms) == 1 and tuple(ms[0][1:]) == (4, 4):
        return (bar - 1) * 4 * P
    return H["_bar_to_tick"](bar, ms, P)


def _tick_bar(view, t):
    ms = view.get("meters") or [(0, 4, 4)]
    if len(ms) == 1 and tuple(ms[0][1:]) == (4, 4):
        return 1.0 + t / (4.0 * view["P"])
    return H["_tick_to_bar"](t, ms, view["P"])


def _meter_at(view, bar):
    """(num, den) in force at a song bar, and [(bar, 'n/d')] of the meter changes after it."""
    ms = view.get("meters") or [(0, 4, 4)]
    cur, later = tuple(ms[0][1:]), []
    for t, n, d_ in ms[1:]:
        b = _tick_bar(view, t)
        if b <= bar + 1e-6:
            cur = (n, d_)
        else:
            later.append((round(b, 3), f"{n}/{d_}"))
    return cur, later


def _tempo_at(view, bar):
    """BPM at a song bar from the tempo map (automation / tempo events), else the project tempo."""
    t = _bar_tick(view, bar)
    bpm = None
    for x, b in view.get("tempos") or []:
        if x <= t + 1e-6:
            bpm = b
        else:
            break
    return float(bpm or view["tempo"])


def _tempo_span(view, a_bar, b_bar):
    """(lowest, highest) BPM the tempo map plays in song bars [a, b)."""
    a, b = _bar_tick(view, a_bar), _bar_tick(view, b_bar)
    bs = [_tempo_at(view, a_bar)] + [x[1] for x in view.get("tempos") or [] if a <= x[0] < b]
    return min(bs), max(bs)


def _steps_notes(view, a_bar, b_bar, channels=None):
    """Song notes between song bars [a, b) as steps from bar a: {ch: [{s, l, k, v}]}."""
    P = view["P"]
    st = P / 4.0
    a = _bar_tick(view, a_bar)
    b = _bar_tick(view, b_bar)
    out = {}
    for pos, ln, ch, key, vel in view["song"]:
        if a <= pos < b and (channels is None or ch in channels):
            out.setdefault(ch, []).append({"s": round((pos - a) / st, 3), "l": round(ln / st, 3), "k": key, "v": vel})
    return out


def _score_chords(project, a_bar, b_bar, key, journal_from=None):
    """Chords from flh_score's score_view (format=json: roman numerals per bar per section + legend), as chord
    dicts with start steps from a_bar. [] when unavailable. journal_from: the real project when `project` is its
    lazy-mode shadow copy (the journal's sections, roles, genre and sampler offsets live next to the real one)."""
    sv = H["score_view"](project, bars=f"{a_bar:g}-{b_bar - 1:g}", format="json", journal_from=journal_from)
    legend = sv.get("legend") or {}
    rows = []
    ch = sv.get("chords")
    if isinstance(ch, list):                              # [{bar, chord, roman}]
        for r in ch:
            if isinstance(r, dict) and r.get("chord") and r.get("bar") is not None:
                rows.append((float(r["bar"]), str(r["chord"])))
    elif isinstance(ch, dict):                            # {section: [roman per bar ("a,b" = halves)]} + legend
        for sec in sv.get("sections") or []:
            seq = ch.get(sec.get("name")) or []
            start = max(float(sec.get("bar", a_bar)), a_bar)
            for i, r in enumerate(seq):
                halves = str(r).split(",")
                for j, rr in enumerate(halves):
                    nm_ = legend.get(rr.strip())
                    if nm_:
                        rows.append((start + i + (0.5 * j if len(halves) > 1 else 0), str(nm_)))
    out = []
    for bar, name in sorted(rows):
        if not (a_bar <= bar < b_bar) or name in ("-", ""):
            continue
        c = _chord_or_none(name, key)
        if c is None:
            continue
        c["name"] = name
        c["start"] = int(round((bar - a_bar) * _spb()))
        if not out or out[-1]["name"] != c["name"]:
            out.append(c)
    out.sort(key=lambda c: c["start"])
    return out


def _chord_or_none(name, key):
    """A chord name from flh_score ('C7b9', 'C6/9', 'Am/E', 'G(5)') -> chord dict keeping its slash bass; the
    plain chord when the extension is unknown; None when nothing parses."""
    for tok in (name, str(name).rsplit("/", 1)[0] if re.search(r"/[A-G][#b]?$", str(name)) else None,
                str(name).split("/")[0]):
        if not tok:
            continue
        try:
            c = chord_from_token(tok, key)
        except ValueError:
            continue
        if c.get("bass") is None and tok != name:          # the extension was unknown but the bass is still real
            mm = re.search(r"/([A-G])([#b]?)$", str(name))
            if mm:
                c["bass"] = (fl_music.LETTER[mm.group(1)] + {"#": 1, "b": -1, "": 0}[mm.group(2)]) % 12
        return c
    return None


def song_context(view, a_bar, nb, key, source=None):
    """Chords (from the song's harmony), melody, kick hits and occupied registers in bars [a, a+nb)."""
    b_bar = a_bar + nb
    roles = view["roles"]
    by = _steps_notes(view, a_bar, b_bar)
    S = _spb()
    mel_ch = int(source) if source is not None else None
    if mel_ch is None:
        cands = [ch for ch, r in roles.items() if r in ("melody", "bell", "arp") and by.get(ch)]
        mel_ch = max(cands, key=lambda c: len(by[c]), default=None)
    mel = sorted(by.get(mel_ch, []), key=lambda n: n["s"]) if mel_ch is not None else []
    harm = [n for ch, ns in by.items() if not _is_drum(roles.get(ch)) for n in ns]
    chords, src = [], None
    support = [ch for ch, ns in by.items() if ns and roles.get(ch) in HARMONIC_ROLES and ch != mel_ch]
    if not support:                                       # only a melody here: the composed block's chords
        for bn, b in sorted(((view.get("registry") or {}).get("blocks") or {}).items(),
                            key=lambda kv: (not (kv[1] or {}).get("picked"), (kv[1] or {}).get("what") != "beat")):
            if isinstance(b, dict) and b.get("chords") and b.get("picked") is not False:
                L = max(1, int(b.get("bars") or 8)) * S
                t = 0
                while t < nb * S:
                    for st_, ln_, name in b["chords"]:
                        if t + st_ < nb * S:
                            c = _chord_or_none(str(name), key)
                            if c is None:
                                continue
                            c["name"], c["start"] = name, t + st_
                            chords.append(c)
                    t += L
                src = f"composed block '{bn}'"
                break
    if H.get("score_view") and harm and not chords:
        try:
            chords = _score_chords(view.get("src") or view["project"], a_bar, b_bar, key, view["project"])
            src = "score_view" if chords else None
        except Exception:
            chords = []
    if not chords and harm:
        src = "song notes"
        total = nb * S
        mt = _meter()                                     # chords may change on the meter's chord segments
        cuts = [8] if mt["legacy"] else [x for x in mt["seg"] if 0 < x < S]
        bounds = sorted({b0 + x for b0 in range(0, total, S) for x in [0] + cuts if b0 + x < total})
        last = None
        for i_, s in enumerate(bounds):
            seg = (bounds[i_ + 1] if i_ + 1 < len(bounds) else total) - s
            w = [0.0] * 12
            low = None
            for n in harm:
                x0, x1 = max(n["s"], s), min(n["s"] + n["l"], s + seg)
                if x1 > x0:
                    w[n["k"] % 12] += x1 - x0
                    low = n["k"] if low is None or n["k"] < low else low
            nm = fl_music.name_chord(w, low % 12 if low is not None else None)
            if nm is None:
                continue
            if last and last["name"] == nm:
                continue
            c = _chord_or_none(nm, key)
            if c is None:
                continue
            c["name"] = nm
            c["start"] = s
            chords.append(c)
            last = c
    for i, c in enumerate(chords):
        c["len"] = (chords[i + 1]["start"] if i + 1 < len(chords) else nb * S) - c["start"]
    if chords and chords[0]["start"] > 0:
        chords[0]["len"] += chords[0]["start"]
        chords[0]["start"] = 0
    occ = {}
    for ch, ns in by.items():
        r = roles.get(ch)
        if _is_drum(r) or not ns:
            continue
        ks = sorted(n["k"] for n in ns)
        rng = [ks[len(ks) // 10], ks[min(len(ks) - 1, (9 * len(ks)) // 10)]]
        o = occ.get(r)
        occ[r] = [min(o[0], rng[0]), max(o[1], rng[1])] if o else rng
    kick = sorted(n["s"] for ch, ns in by.items() if roles.get(ch) == "kick" for n in ns)
    return {"chords": chords, "chord_source": src, "melody": mel, "melody_channel": mel_ch, "occupied": occ,
            "kick": kick or None, "notes": by}


def _role_notes(ctx, roles, want, exclude=()):
    """Notes (steps) of the song's channels with these roles in a song_context."""
    out = []
    for ch, ns_ in ((ctx or {}).get("notes") or {}).items():
        r = roles.get(ch)
        if r in want and r not in exclude and ch != ctx.get("melody_channel"):
            out += ns_
    return out


def song_key(view):
    mel = [{"pos": n[0], "len": n[1], "key": n[3]} for n in view["song"] if not _is_drum(view["roles"].get(n[2]))]
    if len(mel) < 12:
        return None
    k = fl_music.detect_key(mel, 2)
    return k[0][0] if k and k[0][1] >= 0.5 else None


# ============================================================================= the batch writer
DEFAULT_REC = (0, NOTE_FLAGS, 0, 0, 0, 0, 120, 0, 0, 0, 64, 100, 128, 128)


def written_rows(rows, offsets):
    """Rows [channel, beat, beats, pitch, vel, flags] composed at sounding pitch -> the pitches to write: a pitched
    sampler's notes move by its offset (flh_score.sounding_offsets). In place; returns rows."""
    if offsets:
        for r in rows:
            o = offsets.get(int(r[0]))
            if o:
                r[3] = max(0, min(127, int(r[3]) + o))
    return rows


def _find_pattern(d, ev, pattern):
    pats = H["_patterns"](d, ev)
    if isinstance(pattern, int) or (isinstance(pattern, str) and pattern.isdigit()):
        return int(pattern) if int(pattern) in pats else None
    low = str(pattern).strip().lower()
    return next((pid for pid, p in sorted(pats.items()) if p["name"].strip().lower() == low), None)


def _new_pattern(d, name):
    ev = H["_events"](d)
    pats = H["_patterns"](d, ev)
    pid = H["_new_pattern_id"](pats)                # FL's 999-pattern limit, like the core writers
    META = H["PATTERN_META"]
    EVP = H["EV_PATTERN_NEW"]
    metas = [k for k, e in enumerate(ev) if e["id"] == EVP and k + 1 < len(ev) and ev[k + 1]["id"] in META]
    if metas:
        j = metas[-1] + 1
        while j < len(ev) and ev[j]["id"] in META:
            j += 1
    else:                                       # FL's empty template has no named pattern (PC 2026-10-20)
        j = H["_pattern_meta_anchor"](d, ev)
    meta = struct.pack("<BH", EVP, pid) + H["_data_event"](H["EV_PATTERN_NAME"], str(name).encode("utf-16-le") + b"\x00\x00")
    d = H["_splice"](d, ev, [(j, j, meta)])
    d = H["_write_pattern_notes"](d, H["_events"](d), pid, [])
    return d, pid


def _place_clip(d, pid, pos, length, track):
    try:
        return H["_place_pattern"](d, pid, pos, length, track)
    except ValueError as e:
        if "copy the clip format" not in str(e):
            raise
    import fl_blocks                                       # no pattern clip yet: FL's own clip record as template
    ev = H["_events"](d)
    CR = H["CLIP_RECORD"]
    k = next((k for k, e in enumerate(ev) if e["id"] == H["EV_PLAYLIST"] and e["len"] % CR == 0), None)
    if k is None:
        raise ValueError("this project has no playlist data yet: place any pattern in FL's playlist once")
    p = H["_payload"](d, ev[k])
    r = bytearray(fl_blocks.AUTOMATION_CLIP)               # PC-VERIFY: a pattern clip made from this template plays
    struct.pack_into("<I", r, 0, int(pos))
    struct.pack_into("<H", r, 6, H["PATTERN_BASE"] + pid)
    struct.pack_into("<I", r, 8, int(length))
    struct.pack_into("<H", r, 12, 499 - (int(track) - 1))
    struct.pack_into("<II", r, 24, 0, int(length))
    ids = [struct.unpack("<I", p[i + 32:i + 36])[0] for i in range(0, len(p), CR)]
    struct.pack_into("<I", r, 32, max(ids, default=0) + 1)
    recs = sorted([p[i:i + CR] for i in range(0, len(p), CR)] + [bytes(r)], key=lambda x: struct.unpack("<I", x[:4])[0])
    return H["_splice"](d, ev, [(k, k + 1, H["_data_event"](H["EV_PLAYLIST"], b"".join(recs)))])


PLACE_MODES = ("move", "add", "replace")


def _place_existing(d, pid, spans, track, mode):
    """place_mode for a pattern that's already in the playlist (current arrangement): spans [(tick, ticks)] wanted.
    move: each span reuses one of the pattern's clips (one at that very tick first, then the nearest), moved and
    resized (its track kept unless `track` is given); spans left over add clips; clips left over stay. replace: the
    pattern's clips go first. -> (bytes, spans still to add, {moved, removed, kept})."""
    ev = H["_events"](d)
    k, size, recs = H["_playlist_at"](d, ev)
    item = H["PATTERN_BASE"] + pid
    mine = [r for r in recs if struct.unpack("<H", bytes(r[6:8]))[0] == item]
    info = {}
    if not mine or mode == "add":
        return d, list(spans), info
    if mode == "replace":
        recs = [r for r in recs if struct.unpack("<H", bytes(r[6:8]))[0] != item]
        info["removed"] = len(mine)
        return H["_put_clips"](d, ev, recs, k, True), list(spans), info
    free = sorted(mine, key=lambda r: struct.unpack("<I", bytes(r[0:4]))[0])
    left, moved = [], 0
    for a_, L_ in spans:
        if not free:
            left.append((a_, L_))
            continue
        pos_of = lambda r: struct.unpack("<I", bytes(r[0:4]))[0]
        r = min(free, key=lambda r: (pos_of(r) != a_, abs(pos_of(r) - a_)))
        free.remove(r)
        s0, _ = H["_clip_offsets"](r)
        struct.pack_into("<I", r, 0, int(a_)); struct.pack_into("<I", r, 8, int(L_))
        struct.pack_into("<II", r, 24, int(s0), int(s0) + int(L_))
        if track:
            struct.pack_into("<H", r, 12, 499 - (H["_check_track"](track) - 1))
        moved += 1
    info["moved"] = moved
    if free:
        info["kept"] = len(free)
    return H["_put_clips"](d, ev, recs, k, True), left, info


def flp_compose_write(project, output, pattern, notes=None, place=None, track=None, clear_channels=None,
                      transform=None, length_bars=None, place_mode="move"):
    """Write into ONE named pattern (created if missing): notes as [channel, beat, beats, pitch, velocity, flags]
    (beats from the pattern start; flags 1 = FL slide note), clear_channels first, transform = in-place ops on
    the pattern's current notes (read at write time), place = [bar] or [[bar, length_bars], ...] on `track`.
    place_mode for a pattern already in the playlist: move (default: its clip moves / resizes to the place given,
    never a second copy), add (another clip: a repeat), replace (its clips go, the places given are laid out)."""
    H["writable"](output)
    place_mode = str(place_mode or "move").strip().lower()
    if place_mode not in PLACE_MODES:
        raise ValueError(f"place_mode: {' | '.join(PLACE_MODES)}")
    d = H["_load"](project)
    ev = H["_events"](d)
    P = H["_ppq"](d)
    pid = _find_pattern(d, ev, pattern)
    created = pid is None
    if created:
        if isinstance(pattern, int):
            raise ValueError(f"no pattern {pattern}")
        d, pid = _new_pattern(d, pattern)
        ev = H["_events"](d)
    res = {"pattern": pid, "name": str(pattern), "created": created}
    pats = H["_patterns"](d, ev)
    if notes is not None or clear_channels or transform:
        cur = [H["_rec_to_note"](r) for r in pats.get(pid, {"notes": []})["notes"]]
        clear = {int(c) for c in clear_channels or []}
        removed = sum(1 for n in cur if n["ch"] in clear)
        cur = [n for n in cur if n["ch"] not in clear]
        if transform:
            cur, info = apply_transform(cur, transform, P)
            res["transform"] = info
        midi_ch = {}                                      # the channel's MIDI channel (r[9]) is all a new note copies:
        for p in pats.values():                           # group, fine pitch, release, pan, mod X/Y stay default
            for r in p["notes"]:
                midi_ch.setdefault(r[2], r[9])
        new = []
        for row in notes or []:
            ch, beat, beats, key, vel, flags = (list(row) + [0, 0])[:6]
            ch = int(ch)
            r = list(DEFAULT_REC)
            r[9] = midi_ch.get(ch, 0)
            r[1] = NOTE_FLAGS | (SLIDE_FLAG if int(flags) & 1 else 0)
            new.append({"pos": max(0, int(round(float(beat) * P))), "len": max(1, int(round(float(beats) * P))),
                        "key": max(0, min(127, int(key))), "vel": max(1, min(127, int(vel))), "ch": ch, "_r": tuple(r)})
        d = H["_write_pattern_notes"](d, ev, pid, cur + new)
        res.update(added=len(new), removed=removed, slides=sum(1 for row in notes or [] if (list(row) + [0, 0])[5]))
    if place:
        ev2 = H["_events"](d)
        try:                                              # bars follow the project's time signature (and markers)
            meters = [tuple(int(x) for x in m_) for m_ in H["_meter_map"](d, ev2)] or [(0, 4, 4)]
        except Exception:
            meters = [(0, 4, 4)]
        plain = len(meters) == 1 and meters[0][1:] == (4, 4)
        bar_t = 4 * P if plain else meters[0][1] * 4 * P / meters[0][2]
        # PC-VERIFY: in a 3/4 song (and one with a time-signature marker mid-song) the clips start on the bar FL shows
        tick = (lambda b_: (float(b_) - 1) * bar_t) if plain else (lambda b_: H["_bar_to_tick"](float(b_), meters, P))
        allnotes = H["_patterns"](d, ev2).get(pid, {"notes": []})["notes"]
        natural = max((r[0] + r[3] for r in allnotes), default=4 * bar_t)
        dflt = float(length_bars) if length_bars else max(1, math.ceil(natural / bar_t - 1e-6))
        want, done = [], []
        for it in place:
            bar, ln = (list(it) + [dflt])[:2] if isinstance(it, (list, tuple)) else (it, dflt)
            a_ = tick(bar)
            L_ = float(ln) * bar_t if plain else tick(float(bar) + float(ln)) - a_
            want.append((int(round(a_)), int(round(L_)))); done.append(float(bar))
        # PC-VERIFY: compose_write place=[..] on a pattern FL already shows in the playlist moves that clip (one clip,
        # not two playing the section twice); place_mode='add' gives a second one
        have_tr = [499 - struct.unpack("<H", r[12:14])[0] + 1 for _, _, r in H["_clips"](d, H["_events"](d))
                   if struct.unpack("<H", r[6:8])[0] == H["PATTERN_BASE"] + pid]
        d, todo, pinfo = _place_existing(d, pid, want, track, place_mode)
        if track:
            tr = int(track)
        else:                                             # a moved clip keeps its track; added ones go where the
            tr = min(have_tr) if have_tr and place_mode != "add" else H["_free_track"](d)   # pattern plays (add:
                                                                                             # a free track, as before)
        for a_, L_ in todo:
            d = _place_clip(d, pid, a_, L_, tr)
        res.update(placed_at=done, track=tr, place_mode=place_mode)
        if pinfo.get("moved"):
            res["clips_moved"] = pinfo["moved"]
        if pinfo.get("removed"):
            res["clips_removed"] = pinfo["removed"]
        if pinfo.get("kept"):
            res["other_clips_kept"] = pinfo["kept"]
        if todo:
            res["clips_added"] = len(todo)
    H["_save"](d, output)
    return res


def _shift_markers(d, at, delta):
    """Playlist markers (time-signature markers too) of the current arrangement at/after tick `at` move by delta."""
    ev = H["_events"](d)
    a = H["_arrangement"](d, ev, None)
    lo, hi = (a["first"] + 1, a["end"]) if a else (0, len(ev))
    POS, MASK = H["EV_MARKER_POS"], 0x07FFFFFF
    edits, n = [], 0
    for k in range(lo, hi):
        e = ev[k]
        if e["id"] == POS and e["len"] == 4:
            x, = struct.unpack("<I", H["_payload"](d, e))
            t = x & MASK
            if t >= at:
                edits.append((k, k + 1, bytes([POS]) + struct.pack("<I", (x & ~MASK) | (max(0, t + delta) & MASK))))
                n += 1
    return (H["_splice"](d, ev, edits) if edits else d), n


def flp_compose_insert(project, output=None, at=None, bars=None, meter=None):
    """Insert `bars` empty bars at song bar `at` in any meter (the extender's batch op): clips crossing `at` split,
    tempo maps stay in line, clips and playlist markers (time-signature markers too) at/after it move later. The
    inserted bars are bars of `meter` (default: the meter in force just before `at`)."""
    # PC-VERIFY: after an extend in a 3/4 song with a time-signature marker later on, FL shows the marker moved by
    # the inserted bars and every later clip still starts on the bar line it started on before
    output = output or project
    H["writable"](output)
    d = H["_load"](project)
    ev = H["_events"](d)
    P = H["_ppq"](d)
    ms = [tuple(int(x) for x in m_) for m_ in H["_meter_map"](d, ev)] or [(0, 4, 4)]
    if at is None or bars is None:
        raise ValueError("insert needs at (a song bar) and bars (how many)")
    at, n = float(at), float(bars)
    if not (at >= 1 and n > 0):
        raise ValueError("insert needs at >= 1 and bars > 0")
    t0 = int(round(H["_bar_to_tick"](at, ms, P)))
    if meter:
        num, den = _parse_ts(meter)
    else:
        num, den = next(((n_, d_) for t_, n_, d_ in reversed(ms) if t_ < t0), tuple(ms[0][1:]))
    dt = int(round(n * P * num * 4.0 / den))
    notes = []
    d = H["_split_clips_at"](d, t0)
    d = H["_stretch_tempo_patterns"](d, t0, dt, notes)
    d = H["_shift_clips"](d, t0, dt)
    d, moved = _shift_markers(d, t0, dt)
    H["_save"](d, output)
    res = {"output": output, "at_tick": t0, "ticks": dt, "meter": f"{num}/{den}", "markers_moved": moved}
    if notes:
        res["notes"] = notes
    return res


def flp_compose_clear(project, output=None, clips=None, names=None, from_bar=None, to_bar=None):
    """Remove pattern clips by WHAT they are, found again in the file at write time - never by clip id, which FL
    renumbers whenever it saves (and the open project is saved right before every edit). clips=[[pattern, bar,
    track], ...] (pattern id or name; track may be null) and/or names=[...] limited to clips overlapping
    from_bar..to_bar. A wanted clip that isn't there any more (moved or removed in FL meanwhile) is left alone and
    counted in not_found."""
    output = output or project
    H["writable"](output)
    try:
        cl = H["flp_playlist"](project)["clips"]
    except ValueError:                                    # no playlist yet
        cl = []
    cl = [c for c in cl if c.get("kind", "pattern") == "pattern" and c.get("id") is not None]
    used, pick, missing = set(), [], []
    for w in clips or []:
        if not isinstance(w, (list, tuple, dict)):
            raise ValueError(f"clips: [pattern, bar, track] each (got {w!r})")
        pat, bar, track = (list(w) + [None, None, None])[:3] if isinstance(w, (list, tuple)) else (
            w.get("pattern"), w.get("bar"), w.get("track"))
        if pat is None or bar is None:
            raise ValueError(f"clips: [pattern, bar, track] each (got {w!r})")

        def same(c, pat=pat, bar=bar, track=track):
            if isinstance(pat, str) and not pat.strip().isdigit():
                ok = str(c.get("name") or "").strip().lower() == pat.strip().lower()
            else:
                ok = c.get("pattern") == int(pat)
            return ok and abs(float(c["bar"]) - float(bar)) < 1e-3 and (track is None or int(c["track"]) == int(track))
        c = next((c for c in cl if c["id"] not in used and same(c)), None)
        if c is None:
            missing.append(list(w) if isinstance(w, (list, tuple)) else w)
        else:
            used.add(c["id"]); pick.append(c)
    if names:
        low = {str(n).strip().lower() for n in names}
        a = float(from_bar) if from_bar is not None else float("-inf")
        b = float(to_bar) if to_bar is not None else float("inf")
        for c in cl:
            if (c["id"] not in used and str(c.get("name") or "").strip().lower() in low
                    and c["bar"] < b - 1e-6 and c["bar"] + c["length_bars"] > a + 1e-6):
                used.add(c["id"]); pick.append(c)
    res = {"output": output, "removed": len(pick)}
    if missing:
        res["not_found"] = len(missing)
        res["note"] = (f"{len(missing)} clip(s) weren't where they were planned (moved or removed in FL since): "
                       "left as they are")
    if not pick:
        if not _same_path(project, output):
            H["_save"](H["_load"](project), output)
        return res
    H["_edit_enter"]()                                      # already inside this call's open-project guard
    try:
        H["flp_playlist"](project, "remove", output, clips=sorted(c["id"] for c in pick))
    finally:
        H["_edit_exit"]()
    return res


# ============================================================================= note transforms (flp_adjust)
def _ms_ticks(ms, tempo, P):
    return float(ms) / 1000.0 * float(tempo) / 60.0 * P


def swing_of(notes, P):
    """Median swing ratio of notes on odd 16ths (0.5 = straight), None when there are none."""
    st = P / 4.0
    rs = []
    for n in notes:
        base8 = (n["pos"] // (2 * st)) * 2 * st
        off = n["pos"] - base8
        if 0.75 * st <= off <= 1.6 * st:
            rs.append(off / (2 * st))
    return round(statistics.median(rs), 3) if rs else None


def _metric(n, P, half, mt=None):
    """Where a note sits: downbeat (4/4: beats 1, 3) | backbeat (2, 4) | offbeat (16th offbeats) | eighth. mt: a
    meter_info that isn't 4/4 (bars of mt['spb'] 16ths): its strong beats are downbeats, its other beats backbeats."""
    st = P / 4.0
    pos = n["pos"]
    if mt is not None and not mt.get("legacy"):
        x = (pos % (mt["spb"] * st)) / st                 # 16th step in the bar
        near = lambda offs: any(min(abs(x - o), abs(x - o - mt["spb"])) < 1 / 3 for o in offs)
        if near(mt["strong"]):
            return "downbeat"
        if near(mt["beats"]):
            return "backbeat"
        off = (pos % (2 * st))
        return "offbeat" if 0.6 * st <= off <= 1.6 * st else "eighth"
    beat = (pos % (4 * P)) / P
    if abs(pos % P) < st / 3 or abs(P - pos % P) < st / 3:
        b = int(round(beat)) % 4
        if half:
            return "backbeat" if b == 2 else "downbeat"
        return "backbeat" if b in (1, 3) else "downbeat"
    off = (pos % (2 * st))
    if 0.6 * st <= off <= 1.6 * st:
        return "offbeat"
    return "eighth"


def metrics(notes, P, tempo=120.0, mt=None):
    if not notes:
        return {"notes": 0}
    vs = [n["vel"] for n in notes]
    st = P / 4.0
    dev = [min(n["pos"] % st, st - n["pos"] % st) for n in notes]
    bar_t = 4 * P if mt is None or mt.get("legacy") else mt["spb"] * st
    pos_bars = (max(n["pos"] for n in notes) - min(n["pos"] for n in notes)) / bar_t + 1
    acc_d = [n["vel"] for n in notes if _metric(n, P, False, mt) in ("downbeat", "backbeat")]
    acc_o = [n["vel"] for n in notes if _metric(n, P, False, mt) == "offbeat"]
    return {"notes": len(notes), "per_bar": round(len(notes) / max(1, pos_bars), 1), "vel": round(statistics.mean(vs), 1),
            "vel_sd": round(statistics.pstdev(vs), 1), "swing": swing_of(notes, P),
            "accent": round(statistics.mean(acc_d) - statistics.mean(acc_o), 1) if acc_d and acc_o else None,
            "pitch": round(statistics.mean(n["key"] for n in notes), 1),
            "len_steps": round(statistics.mean(n["len"] for n in notes) / st, 2),
            "off_grid_ms": round(statistics.mean(dev) / P * 60000 / tempo, 1)}


def _sel(spec_ch, op):
    roles = op.get("roles") or ["all"]
    parts = op.get("parts")
    only = {int(c) for c in op.get("channels") or []}
    def ok(n):
        if only and n["ch"] not in only:
            return False
        info = spec_ch.get(str(n["ch"])) or spec_ch.get(n["ch"]) or {}
        r = info.get("role", "unknown")
        drum = info.get("drum", _is_drum(r))
        hit = "all" in roles or r in roles or ("drums" in roles and drum) or ("melodic" in roles and not drum)
        if hit and parts:
            hit = r in parts or (r == "kit" and True)
        return hit
    return ok


def _by_channel(notes):
    out = {}
    for n in notes:
        out.setdefault(n["ch"], []).append(n)
    return out


def apply_transform(notes, spec, P):
    """Run adjust ops on note dicts ({pos, len, key, vel, ch, _r}); spec = {ops, channels {ch: {role, drum}},
    seed, tempo, half_time, key, strength}. -> (notes, {op: changed notes})"""
    notes = [dict(n) for n in notes]
    offs = {}                                             # pitched samplers: ops that read pitch see sounding pitch
    for k_, o_ in (spec.get("offsets") or {}).items():
        try:
            offs[int(k_)] = int(o_)
        except (TypeError, ValueError):
            pass
    for n in notes:
        if offs.get(n["ch"]):
            n["key"] -= offs[n["ch"]]
    chs = spec.get("channels") or {}
    tempo = float(spec.get("tempo", 120))
    half = bool(spec.get("half_time"))
    st = P / 4.0
    mt = meter_info(spec["meter"]) if spec.get("meter") else None    # the pattern's meter (absent: 4/4)
    if mt is not None and mt["legacy"]:
        mt = None
    if mt is not None:
        half = False                                      # half-time feel is a 4/4 idea
    _metric_ = lambda n, P_, h_: _metric(n, P_, h_, mt)
    info = {}
    for i, op in enumerate(spec.get("ops") or []):
        kind = op["op"]
        sel = _sel(chs, op)
        chosen = [n for n in notes if sel(n)]
        rest = [n for n in notes if not sel(n)]
        rnd = _rng(spec.get("seed", 1), "adjust", i, kind)
        before = [(n["pos"], n["len"], n["key"], n["vel"]) for n in chosen]
        if kind == "swing":
            for ch, grp in _by_channel(chosen).items():
                r0 = swing_of(grp, P) or 0.5
                tgt = max(0.5, min(0.75, r0 + float(op.get("delta", 0))))
                sh = (tgt - r0) * 2 * st
                for n in grp:
                    base8 = (n["pos"] // (2 * st)) * 2 * st
                    off = n["pos"] - base8
                    if 0.75 * st <= off <= 1.6 * st:
                        n["pos"] = int(round(base8 + max(0.8 * st, min(1.55 * st, off + sh))))
        elif kind == "velocity":
            for n in chosen:
                n["vel"] = _clampv(n["vel"] * float(op.get("scale", 1)) + float(op.get("add", 0)))
        elif kind == "accent":
            for n in chosen:
                m = _metric_(n, P, half)
                n["vel"] = _clampv(n["vel"] + float(op.get(m, 0)) + (float(op.get("downbeat", 0)) if m == "backbeat" and "backbeat" not in op else 0))
        elif kind == "dynamics":
            for ch, grp in _by_channel(chosen).items():
                mu = statistics.mean(n["vel"] for n in grp)
                for n in grp:
                    n["vel"] = _clampv(mu + (n["vel"] - mu) * float(op.get("range", 1)))
        elif kind == "humanize":
            t = _ms_ticks(op.get("timing_ms", 5), tempo, P)
            for n in chosen:
                n["pos"] = max(0, int(round(n["pos"] + max(-t, min(t, rnd.gauss(0, t / 2))))))
                n["vel"] = _clampv(n["vel"] + rnd.gauss(0, float(op.get("vel", 5)) / 2))
        elif kind == "tighten":
            s = float(op.get("strength", 0.6))
            for ch, grp in _by_channel(chosen).items():
                r0 = (swing_of(grp, P) or 0.5) if op.get("keep_swing") else 0.5
                for n in grp:
                    base8 = (n["pos"] // (2 * st)) * 2 * st
                    tg = min((base8, base8 + 2 * st * r0, base8 + 2 * st), key=lambda c: abs(c - n["pos"]))
                    n["pos"] = max(0, int(round(n["pos"] + (tg - n["pos"]) * s)))
        elif kind == "density":
            f = float(op.get("factor", 1))
            if f < 1:
                cand = [n for n in chosen if _metric_(n, P, half) in ("offbeat", "eighth")
                        and not (_metric_(n, P, half) == "eighth" and (chs.get(str(n["ch"])) or {}).get("role") == "kick")]
                cand.sort(key=lambda n: ({"offbeat": 0, "eighth": 1}[_metric_(n, P, half)], n["vel"], n["pos"]))
                drop = set(id(n) for n in cand[:int(round(len(chosen) * (1 - f)))])
                chosen = [n for n in chosen if id(n) not in drop]
            elif f > 1:
                add = []
                for ch, grp in _by_channel(chosen).items():
                    role = (chs.get(str(ch)) or chs.get(ch) or {}).get("role")
                    have = {int(round(n["pos"] / st)) for n in grp}
                    want = int(round(len(grp) * (f - 1)))
                    if role in ("hat", "shaker", "ride", "kit"):
                        slots = sorted(s for s in range(0, int(max(n["pos"] for n in grp) / st) + 2) if s % 2 == 1 and s not in have
                                       and (s - 1) in have)
                    elif role in ("snare", "clap"):
                        # ghost / pickup hits: the 16th before each strong beat and two before the last beat
                        # (4/4: steps 7, 15, 10)
                        bs, gh = (16, (7, 15, 10)) if mt is None else (
                            mt["spb"], tuple({(x - 1) % mt["spb"] for x in mt["strong"]} | {(mt["beats"][-1] - 2) % mt["spb"]}))
                        slots = sorted(s for s in range(0, int(max(n["pos"] for n in grp) / st) + 2)
                                       if s % bs in gh and s not in have)
                    else:
                        continue
                    rnd.shuffle(slots)
                    tm = grp[0]
                    vel = max(20, int(statistics.mean(n["vel"] for n in grp) * (0.55 if role in ("hat", "shaker", "ride", "kit") else 0.38)))
                    for s in sorted(slots[:want]):
                        src = min(grp, key=lambda n: abs(n["pos"] - s * st))
                        add.append(dict(src, pos=int(round(s * st)), len=max(1, int(st * 0.8)), vel=vel))
                chosen = chosen + add
        elif kind == "octave":
            for ch, grp in _by_channel(chosen).items():
                mu = statistics.mean(n["key"] for n in grp)
                if "only_if_above" in op and mu <= float(op["only_if_above"]) or "only_if_below" in op and mu >= float(op["only_if_below"]):
                    continue
                for n in grp:
                    n["key"] = max(0, min(127, n["key"] + 12 * int(op.get("octaves", 1))))
        elif kind == "double":
            add = []
            for ch, grp in _by_channel(chosen).items():
                mu = statistics.mean(n["key"] for n in grp)
                if "only_if_above" in op and mu <= float(op["only_if_above"]):
                    continue
                d12 = 12 * int(op.get("octaves", 1))
                have = {(n["pos"], n["key"]) for n in grp}
                add += [dict(n, key=n["key"] + d12, vel=_clampv(n["vel"] * float(op.get("vel_scale", 0.8))))
                        for n in grp if 0 <= n["key"] + d12 <= 127 and (n["pos"], n["key"] + d12) not in have]
            chosen = chosen + add
        elif kind == "length":
            for ch, grp in _by_channel(chosen).items():
                grp.sort(key=lambda n: n["pos"])
                onsets = sorted({n["pos"] for n in grp})
                for n in grp:
                    nl = int(round(n["len"] * float(op.get("scale", 1))))
                    nxt = next((o for o in onsets if o > n["pos"]), None)
                    if nxt is not None and n["pos"] + n["len"] <= nxt and n["pos"] + nl > nxt:
                        nl = nxt - n["pos"]
                    n["len"] = max(1, nl)
        elif kind == "mode":
            k = spec.get("key")
            if not k:
                info[f"{i}:mode"] = "skipped: key unknown"
                continue
            root, _, _, fam = key_info(k)
            to = op.get("to")
            if fam == to:
                info[f"{i}:mode"] = f"skipped: already {fam}"
                continue
            mp = {4: 3, 9: 8, 11: 10} if to == "minor" else {3: 4, 8: 9, 10: 11}
            for n in chosen:
                rel = (n["key"] - root) % 12
                if rel in mp:
                    n["key"] += mp[rel] - rel
        elif kind == "spread":
            for ch, grp in _by_channel(chosen).items():
                groups = {}
                for n in grp:
                    groups.setdefault(int(round(n["pos"] / (st / 2))), []).append(n)
                for g_ in groups.values():
                    if len(g_) < 3:
                        continue
                    g_.sort(key=lambda n: n["key"])
                    if op.get("spread", True):
                        n2 = g_[-2]
                        if n2["key"] - 12 >= 36 and n2["key"] - 12 not in {n["key"] for n in g_}:
                            n2["key"] -= 12
                    else:
                        lo_ = g_[0]["key"]
                        for n in g_[1:]:
                            while n["key"] - lo_ > 12:
                                n["key"] -= 12
        elif kind == "revoice":
            add = []
            for ch, grp in _by_channel(chosen).items():
                sn = [{"s": n["pos"] / st, "l": n["len"] / st, "k": n["key"], "v": n["vel"], "_src": n} for n in grp]
                rv, ri = revoice(sn, spec.get("key"), op.get("style", "triad"), op.get("register"), op.get("ext"))
                info[f"{i}:revoice:{ch}"] = ri
                for x in rv:
                    src = x.get("_src") or grp[0]
                    add.append(dict(src, pos=int(round(x["s"] * st)), len=max(1, int(round(x["l"] * st))), key=x["k"], vel=x["v"]))
            chosen = add
        else:
            raise ValueError(f"unknown adjust op '{kind}'")
        after = [(n["pos"], n["len"], n["key"], n["vel"]) for n in chosen]
        info[f"{i}:{kind}"] = sum(1 for a, b in zip(before, after) if a != b) + abs(len(after) - len(before))
        notes = rest + chosen
    for n in notes:
        n.pop("_src", None)
        if offs.get(n["ch"]):
            n["key"] = max(0, min(127, n["key"] + offs[n["ch"]]))
    return notes, info


# ============================================================================= helpers for the tools
def _resolve_genre(genre, view):
    if genre:
        return genre_data(genre), "given"
    reg = view["registry"]
    for src, val in (("song memory", reg.get("genre")), ("journal", view["journal"].get("genre"))):
        if val:
            try:
                return genre_data(val), src
            except ValueError:
                pass
    try:
        g = (H["flp_project_info"](view["project"]) or {}).get("genre")
        if g:
            return genre_data(g), "project info"
    except Exception:
        pass
    return genre_data("pop"), "guess (no genre given; pass genre=...)"


def _resolve_key(key, view, g):
    if key:
        key_info(key)
        return str(key), "given"
    if view["registry"].get("key"):
        return view["registry"]["key"], "song memory"
    k = song_key(view)
    if k:
        return k, "detected from the song"
    return (g.get("keys") or {}).get("default", "C minor"), f"{g['name']} default (guess)"


def _drum_map(view, parts, explicit=None):
    """{part: (channel, key, on_kit)} for the drum parts we have channels for, + [missing]."""
    roles = view["roles"]
    out, missing = {}, []
    if isinstance(explicit, dict):
        for p, c in explicit.items():
            if p in DRUM_PARTS:
                ck = c if isinstance(c, (list, tuple)) else (c, None)
                out[p] = (int(ck[0]), ck[1], False)
    kit = int(explicit) if isinstance(explicit, (int, str)) and str(explicit).isdigit() else \
        next((ch for ch, r in sorted(roles.items()) if r == "kit"), None)
    reg = (view["registry"].get("drum_map") or {})
    for p in parts:
        if p in out:
            continue
        if p in reg:
            c, k = reg[p][:2]
            if int(c) in view["channels"]:
                out[p] = (int(c), k, bool(reg[p][2]) if len(reg[p]) > 2 else False)
                continue
        ch = next((c for c, r in sorted(roles.items()) if r == p), None)
        if ch is None:
            for alt in PART_FALLBACK.get(p, []):
                ch = next((c for c, r in sorted(roles.items()) if r == alt), None)
                if ch is not None:
                    break
        if ch is not None:
            out[p] = (ch, None, False)
        elif kit is not None:
            out[p] = (kit, GM_DRUMS.get(p, 37), True)
        else:
            missing.append(p)
    owner = {}
    for p in list(out):                                   # two parts on one sample channel would double the hits
        ch, k, kitf = out[p]
        if kitf:
            continue
        if ch in owner:
            keep = p if roles.get(ch) == p else owner[ch]
            drop = owner[ch] if keep == p else p
            out.pop(drop, None)
            owner[ch] = keep
        else:
            owner[ch] = p
    for p, (ch, k, kitf) in list(out.items()):
        if k is None:
            ks = [r[4] for r in view["notes_by_ch"].get(ch, [])]
            k = max(set(ks), key=ks.count) if ks else 60
            out[p] = (ch, int(k), kitf)
    return out, missing


def _channel_for(view, role, explicit, taken, plan, g):
    """Channel for a melodic role: explicit > a channel with that role > a clone (planned add_channel op)."""
    if explicit is not None:
        ch = int(explicit)
        if ch not in view["channels"]:
            raise ValueError(f"no channel {ch}")
        return ch, None
    roles = view["roles"]
    for r in MELODIC_FALLBACK.get(role, (role,)):
        ch = next((c for c, rr in sorted(roles.items()) if rr == r and c not in taken), None)
        if ch is not None:
            return ch, None
    chans = view["channels"]
    if role in CLONE_FAMILY:                              # a channel named for it whose song role says otherwise
        ch = next((c for c, info in sorted(chans.items()) if c not in taken and info.get("type") in (0, 2) and
                   _role_from_text(str(info.get("name") or "")) == role), None)
        if ch is not None:
            return ch, None
    if not chans:
        raise ValueError(f"this project has no channels: add an instrument channel in FL "
                         f"({((g.get('roles') or {}).get(role) or {}).get('hint', role)}) and pass channel=")
    last = max(chans)
    fam = CLONE_FAMILY.get(role) or MELODIC_FALLBACK.get(role, (role,))
    src = [c for c, info in sorted(chans.items()) if c != last and info.get("type") == 2 and roles.get(c) in fam] or \
          [c for c, info in sorted(chans.items()) if c != last and info.get("type") == 2 and not _is_drum(roles.get(c))] or \
          [c for c, info in sorted(chans.items()) if c != last and info.get("type") in (0, 2)]
    if not src:
        raise ValueError(f"no channel for '{role}' and none to clone: add an instrument channel in FL "
                         f"({((g.get('roles') or {}).get(role) or {}).get('hint', role)}) and pass channel=")
    new = max(chans) + 1 + len(plan)
    name = ((g.get("roles") or {}).get(role) or {}).get("name") or role.title()
    plan.append({"role": role, "clone_from": src[0], "channel": new, "name": name,
                 "sound_hint": ((g.get("roles") or {}).get(role) or {}).get("hint")})
    return new, plan[-1]


def _swing8(s, r8):
    """A 16th-grid position under 8th-note swing r8: each beat's first 8th stretches to r8 of the beat."""
    b, x = divmod(s, 4.0)
    return b * 4 + (x * 2 * r8 if x < 2 else 4 * r8 + (x - 2) * (2 - 2 * r8))


def _rows(notes, ch, key, g, tempo, seed, strum_ms=0, humanize=True, swing=None, slide=True, swing8=None):
    sw = 0.5 if swing8 else float(g.get("swing", 0.5) if swing is None else swing)
    hz = g.get("humanize") or {}
    tms = float(hz.get("timing_ms", 0)) if humanize else 0.0
    hv = int(hz.get("vel", 0)) if humanize else 0
    rnd = _rng(seed, "rows", ch)
    bpm_ms = float(tempo) / 60000.0
    rows = []
    for n in notes:
        s = float(n["s"])
        if swing8 and not n.get("nos"):
            s = _swing8(s, float(swing8))
        elif not n.get("nos") and abs(s - round(s)) < 1e-6 and int(round(s)) % 2 == 1:
            s += (sw - 0.5) * 2
        beat = s / 4.0
        if strum_ms and n.get("strum"):
            beat += n["strum"] * strum_ms * bpm_ms
        if tms and not n.get("slide"):
            beat += max(-tms, min(tms, rnd.gauss(0, tms / 2))) * bpm_ms
        k = n["k"] if key is None else key + int(n.get("dk", 0))
        v = n["v"] + (rnd.randint(-hv, hv) // 2 if hv else 0)
        rows.append([int(ch), round(max(0.0, beat), 5), round(max(0.02, float(n["l"]) / 4.0), 5), int(k), _clampv(v),
                     1 if (n.get("slide") and slide) else 0])
    return rows


def _summ_drums(hits, nb):
    k = sum(1 for h in hits if h["part"] == "kick")
    rolls = len({(int(h["s"] // 1), h["part"]) for h in hits if h.get("roll")})
    return {"kicks_per_bar": round(k / max(1, nb), 1), "roll_hits": sum(1 for h in hits if h.get("roll")),
            "ghosts": sum(1 for h in hits if h["v"] < 56 and not h.get("roll")), "hits": len(hits), "_rolls": rolls}


def _var_text(s):
    bits = []
    if "progression" in s:
        bits.append(f"{s['progression']}" + (f" ({' '.join(s['chords'][:4])})" if s.get("chords") else "") +
                    (f" from {s['chords_from']}" if s.get("chords_from") else ""))
    if "drums" in s:
        d = s["drums"]
        bits.append(f"drums {d['kicks_per_bar']} kicks/bar, {d['roll_hits']} roll hits, {d['ghosts']} ghosts")
    if "bass" in s:
        bits.append(f"{s['bass'].get('type', 'bass')} {s['bass']['notes']} notes, {s['bass'].get('glides', 0)} glides")
    if "melody" in s:
        bits.append(f"melody {s['melody'].get('form')} in {s['melody'].get('range')}")
    if "counter" in s:
        bits.append(f"counter {s['counter'].get('mode')} {s['counter'].get('notes')} notes")
    if "layer" in s:
        bits.append(f"{s['layer'].get('role')} {s['layer'].get('notes')} notes in {s['layer'].get('register')}")
    for r, x in (s.get("layers") or {}).items():
        bits.append(f"{r} {x.get('notes')} notes in {x.get('register')}")
    if (s.get("musicality") or {}).get("score") is not None:
        bits.append(f"musicality {s['musicality']['score']}/100")
    return "; ".join(bits)


# ============================================================================= flp_compose
WHATS = ("drums", "chords", "bass", "melody", "counter", "voicing", "layer", "beat", "pick")
ROLE_GROUP = {"strings": "pad", "choir": "pad", "organ": "chords", "arp": "melody", "bell": "melody", "pluck": "melody",
              "brass": "chords", "unknown": "chords", "low strings": "bass"}


def _label(i, n):
    return chr(65 + i) if n > 1 else ""


def _pname(*bits):
    return " ".join(str(b) for b in bits if b)


def _part_name(role, g):
    if role == "bass":
        return ((g.get("roles") or {}).get("bass") or {}).get("name") or "Bass"
    return PART_NAMES.get(role) or str(role).title()


def _clip_starts(view):
    out = {}
    for c in view["clips"]:
        out.setdefault(str(c.get("name") or "").strip().lower(), set()).add(round(c["bar"], 3))
    return out


def _slots_for(occ, nb):
    out = []
    if not nb or nb <= 0:
        raise ValueError(f"bars must be > 0 (got {nb!r})")
    for o in occ:
        L = o["end_bar"] - o["bar"]
        if L / nb > MAX_LOOP_STEPS:
            raise ValueError(f"section '{o.get('name')}' is {L:g} bars: too long to fill in {nb:g}-bar slots")
        off = 0.0
        while off < L - 1e-6:
            out.append((o["bar"] + off, min(nb, L - off)))
            off += nb
    return out


def _edit(project, ops, steps, label="write"):
    """One batch for everything: flp_edit_auto when the server has lazy reloads (big project open in FL: the batch
    is queued and FL gets it in one save+reload later), else flp_edit."""
    t = time.time()
    if getattr(_TL, "scratch", None) and _same_path(project, _TL.scratch):
        # a scratch copy FL never had open (flh_new_song's build folder): no open-project guard, no lazy queue
        res = {"output": project, "ops": len(ops), "results": H["_run_plan"](project, H["_plan_ops"](project, ops))}
    elif H.get("flp_edit_auto"):
        res = H["flp_edit_auto"](project, ops)
    else:
        res = H["flp_edit"](project, ops)
    steps[label] = round(time.time() - t, 3)
    return res if isinstance(res, dict) else {"result": res}


_CARRY = ("reloaded_in_fl", "verify_job", "fl_shows_old_version", "WARNING", "previous_edit_held",
          "queued_ops_applied_first", "replaced_existing", "fl_has_open")


def _carry(res, er):
    """What the flp_edit batch says about FL into the composer's result: queued (lazy) or reloaded in FL, the
    background check, and FL keeping the old version (WARNING)."""
    er = er if isinstance(er, dict) else {}
    if er.get("queued") or er.get("pending_ops"):
        res["queued"] = True
        res["pending_note"] = er.get("pending_note") or "queued for FL's next reload (flh_sync applies it now)"
    for k in _CARRY:
        if k in er:
            res[k] = er[k]
    if er.get("fl_shows_old_version"):
        res["verified_in_fl"] = False
    return er


_queued = _carry


def _verify(project, written):
    """Read the written patterns back: notes per channel as planned?"""
    d = H["_load"](_src(project))
    ev = H["_events"](d)
    pats = H["_patterns"](d, ev)
    byname = {p["name"].strip().lower(): p for p in pats.values()}
    bad = []
    for w in written:
        p = byname.get(w["pattern"].strip().lower())
        have = sum(1 for r in (p or {"notes": []})["notes"] if r[2] in w["channels"])
        if p is None or have != w["notes"]:
            bad.append({"pattern": w["pattern"], "planned": w["notes"], "found": have})
    return not bad, bad


def _layer_specs(role, roles):
    """role / roles=[...] (names or {role, register, vel, where, channel}) -> [{role, register, vel, where, channel}]."""
    items = roles if roles else ([role] if role else [])
    if isinstance(items, (str, dict)):
        items = [x for x in re.split(r"[,+;]", items) if x.strip()] if isinstance(items, str) else [items]
    out = []
    for it in items:
        d = dict(it) if isinstance(it, dict) else {"role": it}
        r = layer_role(d.get("role") or d.get("name"))
        if any(x["role"] == r for x in out):
            continue

        out.append({"role": r, "register": _spec_register(d.get("register"), r),
                    "vel": _spec_vel(d.get("vel", d.get("velocity")), r),
                    "where": _spec_where(d.get("where")), "channel": d.get("channel")})
    if len(out) > 8:
        raise ValueError("at most 8 layer roles in one call")
    return out


VEL_WORDS = {"ppp": 24, "pp": 34, "p": 46, "quiet": 46, "faint": 40, "soft": 52, "mp": 60, "gentle": 56,
             "medium": 76, "normal": 80, "mf": 80, "f": 96, "loud": 100, "strong": 100, "ff": 112, "full": 112,
             "max": 120}
VEL_FLOOR = 20                                        # a layer never comes out near-silent by a scale mix-up


def _vel_value(x, what="vel"):
    """One velocity in any of the forms people write: 0..1 (a fraction of full: 0.7 -> 89), 1..127 (MIDI), '70%',
    or a word (soft / normal / loud, pp..ff). Clamped to VEL_FLOOR..127."""
    if isinstance(x, bool) or x is None:
        raise ValueError(f"{what}: 0..1, 1..127 or soft / normal / loud (got {x!r})")
    if isinstance(x, str):
        t = x.strip().lower()
        if t in VEL_WORDS:
            return VEL_WORDS[t]
        pct = t.endswith("%")
        try:
            v = float(t.rstrip("%").strip())
        except ValueError:
            raise ValueError(f"{what}: 0..1, 1..127 or one of {', '.join(sorted(VEL_WORDS))} (got {x!r})") from None
        if pct:
            v = v / 100.0
    else:
        try:
            v = float(x)
        except (TypeError, ValueError):
            raise ValueError(f"{what}: 0..1, 1..127 or soft / normal / loud (got {x!r})") from None
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"{what} must be above 0 (got {x!r}): 0..1 is a fraction of full, 1..127 MIDI velocity")
    if v <= 1.0:                                      # 0.7 = 70% of full (1 = full); MIDI 1 would be inaudible anyway
        v = v * 127.0
    return int(max(VEL_FLOOR, min(127, round(v))))


def _spec_vel(vel, role=None):
    """A layer's vel option -> [lo, hi] or None. A single value plays around it (+-6); [lo, hi] each in any form."""
    if vel is None or (isinstance(vel, str) and not vel.strip()):
        return None
    if isinstance(vel, (list, tuple)):
        if not vel:
            return None
        vs = [_vel_value(x) for x in vel[:2]]
        lo, hi = (min(vs), max(vs)) if len(vs) == 2 else (vs[0] - 6, vs[0] + 6)
        return [max(VEL_FLOOR, lo), min(127, hi)]
    if isinstance(vel, dict):
        return _spec_vel([vel.get("lo", vel.get("min")), vel.get("hi", vel.get("max"))], role)
    v = _vel_value(vel)
    return [max(VEL_FLOOR, v - 6), min(127, v + 6)]


def _spec_pitch(x):
    if isinstance(x, bool):
        raise ValueError(f"register: note names (C3) or MIDI numbers (got {x!r})")
    if isinstance(x, (int, float)):
        p = int(round(float(x)))
    else:
        t = str(x).strip()
        p = int(t) if re.fullmatch(r"-?\d+", t) else fl_music.parse_note(t)
    if not 0 <= p <= 127:
        raise ValueError(f"register: {x!r} is outside MIDI 0..127")
    return p


def _spec_register(reg, role=None):
    """register: [lo, hi] (names or MIDI, either order), 'C3-C5' / 'C3..C5', or one note (an octave around it)."""
    if reg is None or (isinstance(reg, (str, list, tuple)) and not reg):
        return None
    if isinstance(reg, str):
        bits = [b for b in re.split(r"\s*(?:\.\.|-(?=[A-Ga-g\d])|to|,)\s*", reg.strip()) if b]
        reg = bits if len(bits) > 1 else bits[0]
    if isinstance(reg, (list, tuple)):
        ps = [_spec_pitch(x) for x in reg[:2]]
        if len(ps) == 1:
            ps = [ps[0] - 6, ps[0] + 6]
    else:
        p = _spec_pitch(reg)
        ps = [p - 6, p + 6]
    lo, hi = max(12, min(ps)), min(115, max(ps))
    if hi - lo < 5:                                   # a register too narrow for chord tones: widened around it
        mid = (lo + hi) // 2
        lo, hi = max(12, mid - 6), min(115, mid + 6)
    return [lo, hi]


def _spec_where(wh):
    """where: all | climax | [[from_bar, to_bar], ...] | [from, to] | 'from-to, from-to' (song bars, both included)."""
    if wh is None:
        return None
    if isinstance(wh, str):
        t = wh.strip().lower()
        if t in ("", "all", "everywhere", "whole", "throughout"):
            return None if not t else "all"
        if t == "climax":
            return "climax"
        pairs = re.findall(r"(\d+(?:\.\d+)?)\s*(?:-|\.\.|to)\s*(\d+(?:\.\d+)?)", t)
        if not pairs or re.sub(r"[\d.\s,;\-to\[\]]", "", t):
            raise ValueError(f"where: all | climax | [[from_bar, to_bar], ...] | '9-57, 60-72' (got '{wh}')")
        wh = [[float(a), float(b)] for a, b in pairs]
    if isinstance(wh, (list, tuple)):
        if not wh:
            return None
        wh = [list(wh)] if not isinstance(wh[0], (list, tuple)) else [list(x) for x in wh]
        out = []
        for x in wh:
            if len(x) != 2:
                raise ValueError("where: [[from_bar, to_bar], ...] (song bars, both included)")
            try:
                a, b = float(x[0]), float(x[1])
            except (TypeError, ValueError):
                raise ValueError(f"where: bars are numbers (got {x!r})") from None
            if not (math.isfinite(a) and math.isfinite(b)) or min(a, b) < 1:
                raise ValueError(f"where: song bars start at 1 (got {x!r})")
            a, b = min(a, b), max(a, b)
            out.append([int(a) if a.is_integer() else a, int(b) if b.is_integer() else b])
        out.sort()
        merged = []
        for a, b in out:                                  # overlapping / touching ranges: one span
            if merged and a <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return merged
    raise ValueError(f"where: all | climax | [[from_bar, to_bar], ...] (got {wh!r})")


def _solo_texture(ctx, roles):
    """The music under the layers is a solo-ish part: no drums and at most two pitched channels in the block."""
    chs = [ch for ch, ns in (ctx or {}).get("notes", {}).items() if ns]
    return bool(chs) and not any(_is_drum(roles.get(ch)) for ch in chs) and len(chs) <= 2


def _song_level(ctx, roles):
    """Median velocity of the song's pitched notes in the block (what support layers sit under)."""
    vs = sorted(n["v"] for ch, ns in (ctx or {}).get("notes", {}).items() if not _is_drum(roles.get(ch)) for n in ns)
    return vs[len(vs) // 2] if vs else 90


def _support_vel(r, ref):
    # PC-VERIFY: layers=[low strings, strings, choir] under a solo piano sit audibly under it (velocity-to-volume
    # differs per library: the balance is for a human to judge)
    """Velocities of a support layer: the role's 'under' percent below the song's median (and never above its own)."""
    spec = LAYER_ROLES.get(r) or {"vel": [60, 80], "under": 20}
    hi = max(30, min(int(spec["vel"][1]), int(round(ref * (100 - int(spec.get("under", 20))) / 100.0))))
    return [max(20, hi - 12), hi]


def _top_voice(ctx, roles):
    """[lo, hi] (10th-90th percentile) of the top voice of the song's pitched parts: a solo piano's melody."""
    tops = {}
    for ch, ns in (ctx or {}).get("notes", {}).items():
        if _is_drum(roles.get(ch)):
            continue
        for n in ns:
            t = round(n["s"] * 2) / 2
            tops[t] = max(tops.get(t, 0), n["k"])
    ks = sorted(tops.values())
    return [ks[len(ks) // 10], ks[min(len(ks) - 1, (9 * len(ks)) // 10)]] if len(ks) >= 4 else None


def _loudest(ctx, roles, nb, S):
    """(step spans, bars) where the music is loudest (velocity^2 x sounding time per bar, smoothed over 2 bars), or
    (None, why) when the dynamics are too flat to tell."""
    e = [0.0] * nb
    for ch, ns in (ctx or {}).get("notes", {}).items():
        if _is_drum(roles.get(ch)):
            continue
        for n in ns:
            a, b = n["s"], n["s"] + n["l"]
            for bi in range(max(0, int(a // S)), min(nb, int(math.ceil(b / S)))):
                ov = min(b, (bi + 1) * S) - max(a, bi * S)
                if ov > 0:
                    e[bi] += (n["v"] / 127.0) ** 2 * ov / S
    if nb < 4 or not any(e):
        return None, "too short to find a climax"
    sm = [(e[i] + 0.5 * (e[i - 1] if i else e[i]) + 0.5 * (e[i + 1] if i + 1 < nb else e[i])) / 2 for i in range(nb)]
    mx, med = max(sm), sorted(sm)[nb // 2]
    if mx < med * 1.25:
        return None, "the dynamics are flat (no clear climax)"
    hot = [i for i in range(nb) if sm[i] >= 0.8 * mx]
    runs = []
    for i in hot:
        if runs and i <= runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    for r_ in runs:                                       # grow to the phrase around the peak
        while r_[0] > 0 and sm[r_[0] - 1] >= 0.65 * mx:
            r_[0] -= 1
        while r_[1] + 1 < nb and sm[r_[1] + 1] >= 0.65 * mx:
            r_[1] += 1
        if r_[1] == r_[0] and r_[1] + 1 < nb:
            r_[1] += 1
    merged = []
    for a, b in sorted(runs):
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a * S, (b + 1) * S) for a, b in merged], [(a + 1, b + 1) for a, b in merged]


def _bass_follow(ctx, roles, chords, total, S):
    """The song's own bass line as [(step, pitch)] at every chord change and bar line: the bass part's lowest note
    there, else the lowest note of the harmony (a piano's left hand)."""
    ns = [n for ch, ns_ in (ctx or {}).get("notes", {}).items() if roles.get(ch) in ("bass", "low strings") for n in ns_]
    if not ns:
        ns = [n for ch, ns_ in (ctx or {}).get("notes", {}).items() if not _is_drum(roles.get(ch))
              and ch != (ctx or {}).get("melody_channel") for n in ns_]
    if not ns:
        return []
    pl = _meter()["pulse"]
    out = []
    for t in sorted({c["start"] for c in chords} | set(range(0, int(total), S))):
        if t >= total:
            continue
        at = [n["k"] for n in ns if abs(n["s"] - t) <= 0.5 or (n["s"] < t and n["s"] + n["l"] > t + 1)]
        if not at:
            at = [n["k"] for n in ns if t <= n["s"] < t + pl]
        if at:
            out.append((t, min(at)))
    return out


def _music_end(v, ctx, a_bar, nb, roles):
    """Where the song's music ends (steps from the block start) when it ends inside / right after this block (no
    pitched note starts after the block), else None."""
    b_t = _bar_tick(v, a_bar + nb)
    if any(n[0] >= b_t - 1 for n in v["song"] if not _is_drum(roles.get(n[2]))):
        return None
    ends = [n["s"] + n["l"] for ch, ns in (ctx or {}).get("notes", {}).items() if not _is_drum(roles.get(ch)) for n in ns]
    return round(max(ends), 3) if ends else None


def _end_with(notes, end, chords, kind):
    # PC-VERIFY: the layers' final chord stops with the piano's last note (and a clip lengthened past the section
    # end plays its tail)
    """Layers end with the music: nothing starts after its last note; held parts hold the final chord to it."""
    if end is None or not notes:
        return False
    last = chord_at(chords, end - 0.5)
    ls = last["start"] if last else 0
    keep = [n for n in notes if n["s"] < end - 0.5]
    changed = len(keep) != len(notes)
    for n in keep:
        e = n["s"] + n["l"]
        if e > end + 1e-6 or (kind in ("sustain", "low") and n["s"] >= ls - 1e-6 and abs(end - e) > 1e-6):
            n["l"] = round(max(0.5, end - n["s"]), 3)
            changed = True
    notes[:] = keep
    return changed


def _when(s, S):
    """Step -> 'bar N beat B' words (1-based)."""
    b, x = divmod(float(s), S)
    beat = round(x / _meter()["pulse"] + 1, 2)
    if beat >= len(_meter()["beats"]) + 1 - 0.01:          # a hair before the next bar line
        return f"bar {int(b) + 2}"
    return f"bar {int(b) + 1}" + (f" beat {beat:g}" if abs(beat - 1) > 1e-6 else "")


def _grid_preview(hits, part):
    S = _spb()
    row = ["."] * S
    for h in hits:
        if h["part"] == part and h["s"] < S:
            i = int(h["s"])
            row[i] = "r" if h.get("roll") else ("X" if h["v"] >= 110 else "x" if h["v"] >= 90 else "o" if h["v"] >= 60 else "g")
    return "".join(row)


@_reads_fresh
@_meter_scope
def flp_compose(project, what, genre=None, key=None, section=None, bars=None, channel=None, variations=1, seed=None,
                dry_run=False, progression=None, motif=None, role=None, source=None, glide="slide", level=None,
                style=None, variation=None, tempo=None, variety=1.0, full=False, realism=None, roles=None):
    """Compose and write: drums | chords | bass | melody | counter | voicing | layer | beat | pick. See register().
    Any meter: bars follow the project's time signature at the block (16th steps per bar = num*16/den); drums in a
    meter the genre has no patterns for use a generic feel (data/meters.json) and say so. Tempo maps: everything is
    placed in bars and beats, so it follows tempo changes.
    roles (what=layer): several layers in ONE batch, each on its own channel - names or {role, register, vel,
    where (all | climax | [[bar, bar], ...]), channel}. level (what=layer): support = faint under the music (velocity
    under the song's, choir only on the climax) - the default for roles=[...] over a solo-ish part (no drums, <= 2
    pitched channels) | normal. With roles / support / bars='all', layers end with the music (final chord held to
    its last note).
    realism: layers on strings / choir / pad / brass / woodwind / bells channels are played like a person by
    flh_realism (None = on for what=layer/counter, off otherwise; True = every such part; False = never)."""
    T0 = time.time()
    steps = {}
    what = str(what or "").strip().lower()
    if what not in WHATS:
        raise ValueError(f"what must be one of {', '.join(WHATS)}")
    _fresh(project)
    v = project_view(project)
    steps["read"] = round(time.time() - T0, 3)
    g, gsrc = _resolve_genre(genre, v)
    key, ksrc = _resolve_key(key, v, g)
    if what == "pick":
        return _pick_variation(project, v, section, variation, dry_run, steps, T0)
    if what == "voicing":
        return _voicing(project, v, g, key, section, channel, style, dry_run, steps, T0)
    nvar = max(1, min(6, int(variations or 1)))
    if seed is None:
        seed = _seed(os.path.basename(project), what, section, len(v["registry"].get("blocks") or {})) % 100000
    seed = int(seed)
    occ = occurrences(v, section)
    a_bar = occ[0]["bar"] if occ else 1.0
    m_bar = a_bar if (occ or not v["clips"] or (what in ("layer", "counter") and v["song"])) else float(v["end_bar"])
    (mnum, mden), m_later = _meter_at(v, m_bar)           # the meter where the new bars go
    mt = _set_meter((mnum, mden))                         # every generator below writes bars of this meter
    S = mt["spb"]
    loop = int(g.get("loop_bars", 8))
    lspecs = _layer_specs(role, roles) if what == "layer" else []
    if what == "layer" and not lspecs:
        raise ValueError("layer needs role (or roles=[...]): " + ", ".join(sorted(LAYER_ROLES) + ["counter"]))
    whole = isinstance(bars, str) and bars.strip().lower() in ("all", "song", "whole", "full")
    if whole:                                             # the whole song (or section) from the block start
        if occ:
            nb = int(math.ceil(occ[0]["end_bar"] - occ[0]["bar"] - 1e-6))
        else:
            ends = [n[0] + n[1] for n in v["song"]]
            nb = int(math.ceil(_tick_bar(v, max(ends)) - 1e-6)) - 1 if ends else loop
    elif bars:
        try:
            nb = int(float(bars))
        except (TypeError, ValueError):
            raise ValueError(f"bars: a length in bars or 'all' (got {bars!r}); where it goes is section=") \
                from None
    elif occ:
        nb = int(max(1, min(occ[0]["end_bar"] - occ[0]["bar"], loop)))
    else:
        nb = loop
    follows = what in ("layer", "counter") and bool(v["song"])   # layers follow the song: any length it has
    nb = max(1, min(MAX_EXTEND_BARS if follows else 64, nb))
    guesses, notes_out = [], []
    if not mt["legacy"]:
        notes_out.append(f"written in {mt['ts']} (the project's time signature at bar {m_bar:g})")
    if [b_ for b_, _ in m_later if b_ < m_bar + nb]:
        notes_out.append(f"the meter changes inside these bars ({', '.join(f'{t} at bar {b_:g}' for b_, t in m_later if b_ < m_bar + nb)}): "
                         f"written in {mt['ts']} throughout - compose the bars after the change separately")
    if gsrc != "given":
        guesses.append(f"genre {g['name']}: {gsrc}")
    if ksrc != "given":
        guesses.append(f"key {key}: {ksrc}")
    tmin, tmax = (g.get("tempo") or {}).get("min", 0), (g.get("tempo") or {}).get("max", 999)
    tempo_eff = float(tempo) if tempo else v["tempo"]
    set_tempo = None
    if tempo:
        set_tempo = float(tempo)
    elif what == "beat" and v["empty"] and not (tmin <= v["tempo"] <= tmax):
        set_tempo = float((g.get("tempo") or {}).get("default", v["tempo"]))
        tempo_eff = set_tempo
        notes_out.append(f"tempo set to {set_tempo:g} BPM ({g['name']} range {tmin}-{tmax}) because the project was empty")
    elif not (tmin <= v["tempo"] <= tmax) and v["tempo"] * 2 <= tmax and v["tempo"] * 2 >= tmin and \
            (g.get("tempo") or {}).get("feel") == "half-time":
        notes_out.append(f"project tempo {v['tempo']:g} is the half-time feel of {g['name']}; parts are written at the project tempo")
    elif not (tmin <= v["tempo"] <= tmax):
        notes_out.append(f"project tempo {v['tempo']:g} is outside {g['name']}'s usual {tmin}-{tmax} BPM")
    ctx = None
    if v["song"] and what != "beat":
        ctx = song_context(v, a_bar, nb, key, source)
    if what == "counter" and not (ctx and ctx["melody"]):
        raise ValueError("no melody to answer here: give source=<melody channel> or a section that has a melody")
    roles_needed = {"drums": ["drums"], "chords": ["chords"], "bass": ["bass"], "melody": ["melody"],
                    "counter": ["counter"], "layer": [x["role"] for x in lspecs],
                    "beat": ["drums", "chords", "bass", "melody"]}[what]
    tempo_rows = tempo_eff                                # ms-based feel (humanize, strum, realism) at the block's tempo
    if not tempo and v.get("tempos") and len(v["tempos"]) > 1:
        lo_t, hi_t = _tempo_span(v, a_bar, a_bar + nb)
        tempo_rows = _tempo_at(v, a_bar)
        if hi_t - lo_t > 0.5:
            notes_out.append(f"the tempo changes here ({lo_t:g}-{hi_t:g} BPM): parts are placed in bars and beats, so "
                             "they follow it")
    lvl = str(level or "").strip().lower() if what == "layer" else ""
    if lvl not in ("", "auto", "support", "normal", "full"):
        raise ValueError("level for what=layer: support (faint under the music) | normal")
    support = what == "layer" and bool(ctx) and (lvl == "support" or (
        lvl in ("", "auto") and bool(roles) and _solo_texture(ctx, v["roles"])))
    ref_vel = _song_level(ctx, v["roles"]) if support else None
    ending = None
    if what == "layer" and ctx and (support or roles or whole):
        ending = _music_end(v, ctx, a_bar, nb, v["roles"])
        if ending is not None and abs(ending - nb * S) <= 1.0:   # the music fills the block: nothing to change
            ending = None
    climax = None
    if ctx and any(x["where"] == "climax" or (support and x["where"] is None and (LAYER_ROLES.get(x["role"]) or {}).get("climax"))
                   for x in lspecs):
        climax = _loudest(ctx, v["roles"], nb, S)
    occ_l = dict(ctx["occupied"]) if ctx else None
    if support and ctx and not ctx["melody"]:              # under a solo part: its top voice is the tune to stay under
        tv = _top_voice(ctx, v["roles"])
        if tv:
            occ_l["melody"] = tv
    follow = _bass_follow(ctx, v["roles"], ctx["chords"], nb * S, S) if (
        ctx and ctx["chords"] and any(x["role"] == "low strings" for x in lspecs)) else None
    t = time.time()
    variants = []
    gen_note = None
    for i in range(nvar):
        sd = seed + i * 7919
        if what == "beat":                                # the whole beat: one tested path (compose_beat)
            bt = compose_beat(g, key, nb, sd, i, progression=progression, motif=motif, style=style, variety=variety,
                              level=level or "full")
            parts, summ, chs = bt["parts"], bt["summary"], bt["chords"]
            if bt.get("key", key) != key:
                key = bt["key"]
                notes_out.append(summ.get("note") or f"wrote the beat in {key}")
            hits = parts.get("drums")
            vreg = ((g.get("harmony") or {}).get("voicing") or {}).get("register") or [52, 74]
        else:
            parts, summ, hits = {}, {}, None
            if "drums" in roles_needed:
                hits = drums(g, nb, variety, sd, level or "full", i)
                parts["drums"] = hits
                summ["drums"] = _summ_drums(hits, nb)
            chs = None
            if what in ("chords", "beat"):
                chs, hinfo = harmony(key, g, nb, progression, sd, i)
                if hinfo.get("key", key) != key:          # a minor-only genre in a major key
                    key = hinfo["key"]
                    notes_out.append(hinfo.get("note") or f"wrote the chords in {key}")
                hinfo.pop("key", None)
                summ.update(hinfo)
                cn, cinfo = chord_part(chs, g, key, sd, i, style)
                parts["chords"] = cn
                summ["voicing"] = cinfo["style"]
            elif ctx and ctx["chords"]:
                chs = ctx["chords"]
                summ["chords_from"] = ctx["chord_source"]
                summ["progression"] = "-".join(c["name"] for c in chs[:8])
            elif what in ("bass", "melody", "counter", "layer"):
                chs, hinfo = harmony(key, g, nb, progression, sd, 0)
                if hinfo.get("key", key) != key:          # a minor-only genre in a major key
                    key = hinfo["key"]
                    notes_out.append(hinfo.get("note") or f"wrote the part in {key}")
                hinfo.pop("key", None)
                summ.update(hinfo)
                gen_note = f"no chords in the song here: followed a {g['name']} progression {hinfo['progression']} in {key}"
            if "bass" in roles_needed:
                ks = [h["s"] for h in hits if h["part"] == "kick"] if hits else (ctx["kick"] if ctx else None)
                bn, binfo = bassline(chs, g, ks, sd, i, nb, key)
                parts["bass"] = bn
                summ["bass"] = dict(binfo, notes=len(bn))
            vreg = ((g.get("harmony") or {}).get("voicing") or {}).get("register") or [52, 74]
            if parts.get("chords"):                           # keys out of the bass's and the lead's way
                low = parts.get("bass") or (_role_notes(ctx, v["roles"], ("bass",)) if ctx else [])
                if low:
                    mud_guard(parts["chords"], low, int(vreg[1]) + 7, chs)
            if "melody" in roles_needed:
                acc_m = parts.get("chords") or (_role_notes(ctx, v["roles"], HARMONIC_ROLES, exclude=("bass",))
                                                if ctx else None)   # candidates are scored against the keys
                mn, minfo = melody(key, chs, g, nb, sd, i, motif, accomp=acc_m)
                if ctx and not parts.get("chords"):              # the song's own chords stay; the new line fits them
                    acc = _role_notes(ctx, v["roles"], HARMONIC_ROLES, exclude=("bass",))
                    if acc:
                        _fit_line(mn, acc, key, chs, *[int(x) for x in ((g.get("melody") or {}).get("range") or [60, 84])[:2]])
                parts["melody"] = mn
                summ["melody"] = minfo
            lead = parts.get("melody") or (ctx["melody"] if ctx else None)
            if parts.get("chords") and lead:
                mg = melody_guard(parts["chords"], lead, int(vreg[0]))
                if mg["moved"] or mg["dropped"]:
                    summ["voicing_fixes"] = mg
        if what == "counter":
            acc = _role_notes(ctx, v["roles"], HARMONIC_ROLES, exclude=("bass",))
            c2, ci2 = counter_melody(ctx["melody"], chs, key, g, sd, i, bars=nb, accomp=acc)
            parts["counter"] = c2
            summ["counter"] = ci2
        for ls in lspecs:
            r = ls["role"]
            vel_r = ls["vel"] or (_support_vel(r, ref_vel) if support else None)
            spans, where_txt = None, None
            if ls["where"] not in (None, "all", "climax"):
                spans = [((float(a_) - a_bar) * S, (float(b_) - a_bar + 1) * S) for a_, b_ in ls["where"]]
                where_txt = "bars " + ", ".join(f"{a_:g}-{b_:g}" for a_, b_ in ls["where"])
            elif climax and (ls["where"] == "climax" or (ls["where"] is None and support and (LAYER_ROLES.get(r) or {}).get("climax"))):
                if climax[0]:
                    spans = climax[0]
                    where_txt = "the climax, bars " + ", ".join(f"{a_ + a_bar - 1:g}-{b_ + a_bar - 1:g}" for a_, b_ in climax[1])
                else:
                    where_txt = f"throughout ({climax[1]})"
            ln, li = layer(r, chs, key, g, sd, i, occ_l if ctx else None, nb, tempo_eff,
                           ctx["melody"] if ctx else None, follow=follow if r == "low strings" else None,
                           register=ls["register"], vel=vel_r, where=spans)
            kind = (LAYER_ROLES.get(r) or {}).get("kind", "counter")
            if r == "counter" and (vel_r or spans):            # the counter line gets the same level / span options
                if vel_r and ln:
                    mv = sorted(n["v"] for n in ln)[len(ln) // 2] - (vel_r[0] + vel_r[1]) / 2.0
                    for n in ln:
                        n["v"] = _clampv(min(vel_r[1], max(vel_r[0], n["v"] - mv)))
                if spans:
                    ln = _clip_spans(ln, spans)
                    li["notes"] = len(ln)
            if ctx and r in LAYER_ROLES:                     # out of the way of what the song already plays
                acc = _role_notes(ctx, v["roles"], HARMONIC_ROLES + ("counter",), exclude=("bass",))
                if kind in ("sustain", "stabs"):
                    cr_ = clear_rubs(ln, acc + (ctx["melody"] or []), *li["register"])
                    if cr_["moved"] or cr_["dropped"]:
                        li["kept_off_the_other_parts"] = cr_
                elif kind != "low":                          # the bass of the harmony: it IS the song's bass
                    _fit_line(ln, (ctx["melody"] or []) + acc, key, chs, *li["register"])
            if ending is not None and _end_with(ln, ending, chs, kind):
                li["ends"] = f"with the music ({_when(ending + (a_bar - 1) * S, S)})"
                li["notes"] = len(ln)
            if vel_r and (support or ls["vel"]):
                li["vel"] = vel_r
            if where_txt:
                li["where"] = where_txt
            if follow and r == "low strings":
                li["follows"] = "the song's bass"
            if not ln and i == 0:                          # never a silent '0 notes' without a reason
                notes_out.append(f"{r}: 0 notes" + (f" in {where_txt} (the block is bars {a_bar:g}-{a_bar + nb - 1:g})"
                                                     if where_txt else " (no chords to follow here)"))
            parts[r] = ln
            if len(lspecs) == 1:
                summ["layer"] = dict(li, role=r)
            else:
                summ.setdefault("layers", {})[r] = li
        if what in ("beat", "melody", "bass", "drums", "chords", "counter") and chs:
            if what == "beat":
                summ["musicality"] = bt["musicality"]
            else:                                         # the new part with what the song already plays there
                mparts = _ctx_parts(ctx, v["roles"]) if ctx else {}
                mparts.update({r_: parts[r_] for r_ in ("drums", "bass", "chords", "melody", "counter") if parts.get(r_)})
                if any(mparts.get(x) for x in ("melody", "bass", "drums")):
                    summ["musicality"] = fl_musicality.compact(musicality(mparts, chs, g, nb))
        variants.append({"label": _label(i, nvar), "seed": sd, "parts": parts, "summary": summ,
                         "chords": chs if what in ("chords", "beat") else None})
    if gen_note:
        guesses.append(gen_note)
    elif ctx and ctx["chords"] and not occ and what != "chords":
        guesses.append(f"no section given: followed the chords of bars 1-{nb} ({ctx['chord_source']})")
    steps["generate"] = round(time.time() - t, 3)
    # ---- channels
    plan_new, chmap, taken = [], {}, set()
    drum_map, missing = {}, []
    if "drums" in roles_needed:
        dparts = sorted({h["part"] for vv in variants for h in vv["parts"]["drums"]})
        exd = channel if what == "drums" else (channel.get("drums") if isinstance(channel, dict) else None)
        if isinstance(channel, dict) and what == "beat":
            exd = {p: c for p, c in channel.items() if p in DRUM_PARTS} or exd
        drum_map, missing = _drum_map(v, dparts, exd)
        if not drum_map:
            msg = ("no drum channels: add kick/snare/hat sample channels (or a kit plugin like FPC) in FL, then "
                   "flp_compose(what='drums')")
            if what == "drums":
                raise ValueError(msg)
            notes_out.append("drums skipped - " + msg)
            roles_needed = [r for r in roles_needed if r != "drums"]
        elif missing:
            notes_out.append(f"no channel for {', '.join(missing)}: those hits were left out")
        taken |= {c for c, _, _ in drum_map.values()}
    if what == "layer" and len(lspecs) > 1 and channel is not None and not isinstance(channel, dict):
        raise ValueError("several roles: give channel={role: channel} or roles=[{role, channel}] (one channel each)")
    lch = {x["role"]: x["channel"] for x in lspecs if x.get("channel") is not None}
    for r in roles_needed:
        if r == "drums":
            continue
        ex = lch.get(r, channel.get(r) if isinstance(channel, dict) else (channel if what != "beat" else None))
        ch, _ = _channel_for(v, r, ex, taken, plan_new, g)
        chmap[r] = ch
        taken.add(ch)
    # ---- where
    base = (str(section).strip() if section else ("Beat" if what == "beat" else "Loop"))
    ft = v["free_track"]
    reg = v["registry"]
    role_tracks = {}
    for j, r in enumerate(roles_needed):
        role_tracks[r] = int((reg.get("tracks") or {}).get(r) or 0) or ft + j
    audition, new_section = None, None
    if nvar == 1:
        if occ:
            slots = [_slots_for(occ, nb)]
        elif follows and ctx and ctx["chords"]:           # a layer plays where the music it follows is
            slots = [[(1.0, nb)]]
        elif not v["clips"]:
            slots = [[(1.0, nb)]]
            new_section = {"name": base, "bar": 1.0, "end_bar": 1.0 + nb} if section else None
        else:
            slots = [[(float(v["end_bar"]), nb)]]
            new_section = {"name": base, "bar": float(v["end_bar"]), "end_bar": float(v["end_bar"] + nb)} if section else None
    else:
        start = 1.0 if not v["clips"] else float(v["end_bar"])
        slots = [[(start + i * nb, nb)] for i in range(nvar)]
        audition = {"start_bar": start, "end_bar": start + nvar * nb, "bars": nb, "base": base, "section": section,
                    "tracks": role_tracks, "variations": {}}
    ops = _clone_ops(v, plan_new)
    if set_tempo:
        ops.append({"tool": "flp_set_tempo", "bpm": set_tempo})
    starts = _clip_starts(v)
    rfam, rinfo, rexpr = _realism_plan(v, roles_needed, chmap, plan_new, g, what, role, realism), {}, {}
    written, var_out = [], []
    for vi, var in enumerate(variants):
        lab = var["label"]
        pats = {}
        for r in roles_needed:
            if r == "drums":
                rows = []
                for part, (ch, k, kit) in sorted(drum_map.items()):
                    hs = [h if not kit else dict(h, dk=0) for h in var["parts"]["drums"] if h["part"] == part]
                    rows += _rows(hs, ch, k, g, tempo_rows, var["seed"])
                pname = _pname(base, lab, "Drums")
                clear = sorted({c for c, _, _ in drum_map.values()})
            else:
                ns = var["parts"][r]
                if r == "bass":
                    ns = encode_glides(ns, glide or "slide")
                strum = float((g.get("harmony") or {}).get("strum_ms", 0)) if r == "chords" else 0.0
                rows = _rows(ns, chmap[r], None, g, tempo_rows, var["seed"], strum_ms=strum)
                if r in rfam:                             # a layer played like a person (flh_realism)
                    rows = _realism_rows(rows, ns, r, rfam[r], var, chmap, ctx, v, g, tempo_rows, nb, section or base,
                                         strum, rinfo, rexpr, lab)
                rows = written_rows(rows, v["offsets"])
                pname = _pname(base, lab, _part_name(r, g))
                clear = [chmap[r]]
            have = starts.get(pname.lower(), set())
            nb_clip = nb                                  # a final chord ringing past the block: the clip covers it
            if what == "layer" and ending is not None and ending > nb * S + 1e-6:
                nb_clip = int(math.ceil(max([nb * S] + [n["s"] + n["l"] for n in var["parts"].get(r) or []]) / S - 1e-6))
            place = [[round(b, 3), round(L if (j + 1 < len(slots[vi]) or nb_clip == nb) else max(L, nb_clip), 3)]
                     for j, (b, L) in enumerate(slots[vi]) if round(b, 3) not in have]
            if place and r != "drums":                    # another pattern already plays this channel there?
                over = [c for c in v["clips"] if c.get("kind", "pattern") == "pattern"
                        and str(c.get("name") or "").strip().lower() != pname.lower()
                        and any(rr[2] in clear for rr in (v["patterns"].get(c.get("pattern")) or {}).get("notes", []))
                        and any(c["bar"] < b + L - 1e-6 and c["bar"] + c["length_bars"] > b + 1e-6 for b, L in place)]
                if over:
                    notes_out.append(f"'{pname}' plays together with {', '.join(sorted({c['name'] for c in over}))} on "
                                     f"channel {clear[0]} in those bars (both sound): if the new part replaces it, "
                                     + flh_score.call_hint("flp_compose_clear", {"project": flh_score.Raw("project"), "clips": [
                                         [c["pattern"], c["bar"], c["track"]] for c in over if c.get("pattern") is not None]}, H))
            op = {"tool": "flp_compose_write", "pattern": pname, "notes": rows, "clear_channels": clear,
                  "track": role_tracks[r], "length_bars": nb_clip}
            if place:
                op["place"] = place; op["place_mode"] = "add"     # a section's further slots: repeats, by design
            ops.append(op)
            if rexpr.get((lab, r)):
                ops += _expression_ops(project, chmap[r], rexpr[(lab, r)], pname, slots[vi], nb, tempo_rows, rinfo,
                                       f"{lab}:{r}" if lab else r, view=v)
            written.append({"variation": lab, "role": r, "pattern": pname, "notes": len(rows), "channels": clear})
            pats[r] = pname
        if audition:
            audition["variations"][lab] = {"bar": slots[vi][0][0], "patterns": pats}
        first = slots[vi][0][0] if slots[vi] else None
        vo = {"label": lab or "-", "seed": var["seed"], "about": _var_text(var["summary"]), "patterns": list(pats.values())}
        if var["summary"].get("musicality") and nvar > 1:
            vo["musicality"] = var["summary"]["musicality"]
        if first is not None:
            vo["listen"] = {"start_bar": first, "end_bar": first + nb}
        if full:
            vo["detail"] = var["summary"]
            if var["parts"].get("drums"):
                grid = {p: _grid_preview(var["parts"]["drums"], p) for p in sorted({h["part"] for h in var["parts"]["drums"]})}
                if flh_score.wordy():                     # screen readers / plain language: words, not symbols
                    vo["grid_bar1"] = {p: flh_score.grid_words(x) for p, x in grid.items()}
                else:
                    vo["grid_bar1"] = grid
                    vo["grid_key"] = flh_score.GRID_KEY
        var_out.append(vo)
    if audition and occ:                                   # audition next to the song's own parts of that section
        o = occ[0]
        ctx_clips = [c for c in v["clips"] if c.get("kind", "pattern") == "pattern" and o["bar"] <= c["bar"] < o["bar"] + nb
                     and abs(c.get("from_bar_in_pattern", 1.0) - 1.0) < 1e-6]
        for vi in range(nvar):
            for c in ctx_clips:
                off = c["bar"] - o["bar"]
                ops.append({"tool": "flp_playlist", "action": "place", "pattern": c["pattern"], "bar": slots[vi][0][0] + off,
                            "track": c["track"], "length_bars": round(min(c["length_bars"], nb - off), 3)})
        if ctx_clips:
            notes_out.append(f"the {o['name']} section's other parts ({len(ctx_clips)} clips) were copied under each variation")
    new_ch = [{"channel": p["channel"], "role": p["role"], "name": p["name"], "cloned_from": p["clone_from"],
               "sound_needed": p["sound_hint"],
               "next": flh_score.call_hint("fl_audition", {"want": p["sound_hint"], "channel": p["channel"]}, H)
               + " or flp_instrument_sound"} for p in plan_new]
    res = {"what": what, "genre": g["name"], "key": key, "tempo": tempo_eff, "seed": seed, "bars": nb,
           "variations": var_out, "channels": dict(chmap), "drum_channels": {p: c for p, (c, _, _) in drum_map.items()}}
    mus_ = [(vv["summary"].get("musicality") or {}).get("score") for vv in variants]
    if nvar == 1 and variants[0]["summary"].get("musicality"):
        res["musicality"] = variants[0]["summary"]["musicality"]
    elif any(x is not None for x in mus_):                # the variation the craft check likes best (ears decide)
        bi = max(range(nvar), key=lambda j: (mus_[j] or -1, -j))
        res["musicality"] = dict(variants[bi]["summary"]["musicality"], best=variants[bi]["label"])
    if not mt["legacy"]:
        res["meter"] = mt["ts"]
        if "drums" in roles_needed:
            notes_out.append(drum_feel_note(g, mt))
    if what == "layer" and (roles or support or not mt["legacy"] or ending is not None):
        s0 = variants[0]["summary"]
        lay = s0.get("layers") or ({s0["layer"]["role"]: s0["layer"]} if s0.get("layer") else {})
        res["layers"] = {r: dict({k: x[k] for k in ("notes", "vel", "where", "ends", "follows") if k in x},
                                 channel=chmap.get(r), register="-".join(fl_music.note_name(p) for p in x["register"]))
                         for r, x in lay.items() if "register" in x}
        if support:
            res["level"] = (f"support: velocities under the song's (median {ref_vel}) - faint layers; a human should "
                            "check the balance")
    if new_ch:
        res["new_channels"] = new_ch
    if rinfo:
        res["realism"] = rinfo
    if guesses:
        res["guesses"] = guesses
    if notes_out:
        res["notes"] = notes_out
    if "bass" in roles_needed and (g.get("bass") or {}).get("type") == "808" and (glide or "slide") == "overlap":
        res.setdefault("notes", []).append("glides written as overlapping notes: turn the 808 channel to mono with Porta on")
    if dry_run:
        res["dry_run"] = True
        res["would_write"] = [{k: w[k] for k in ("pattern", "notes", "channels")} for w in written]
        res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
        return res
    er = _edit(project, ops, steps)
    ok, bad = _verify(project, written)
    res["verified"] = ok
    if bad:
        res["verify_problems"] = bad
    res["batch_ops"] = er.get("ops")
    _queued(res, er)
    _inserts_made(er, plan_new, res)
    for x in er.get("results") or []:                     # expression of channels this batch made: what happened
        for chs, got in ((x.get("expression") or {}).items() if isinstance(x, dict) and
                         isinstance(x.get("expression"), dict) else ()):
            if not isinstance(got, int):
                for k, ri in rinfo.items():
                    if isinstance(ri, dict) and str(chmap.get(k.split(":")[-1])) == chs:
                        ri["expression"] = got
    # ---- song memory
    comp = {"channels": {r: c for r, c in chmap.items()},
            "tracks": {r: t for r, t in role_tracks.items()}, "blocks": {}}
    if what == "beat" or not reg.get("genre"):            # the song's genre/key: set by a beat or the first part
        comp.update(genre=g["name"], key=key, tempo=tempo_eff)
    if drum_map:
        comp["drum_map"] = {p: [c, k, kit] for p, (c, k, kit) in drum_map.items()}
    for vi, var in enumerate(variants):
        bname = _pname(base, var["label"])
        prev_b = (reg.get("blocks") or {}).get(bname)
        if what in ("layer", "counter") and not var["label"] and isinstance(prev_b, dict) and prev_b.get("what") in (
                "beat", "around"):                        # a layer added to a beat block: one more part of it (the
            comp["blocks"][bname] = {"parts": {w["role"]: w["pattern"] for w in written}}   # block stays a beat)
            continue
        comp["blocks"][bname] = {"parts": {w["role"]: w["pattern"] for w in written if w["variation"] == var["label"]},
                                 "bars": nb, "seed": var["seed"], "section": section, "what": what,
                                 "variation": var["label"] or None}
        if not mt["legacy"]:
            comp["blocks"][bname]["meter"] = mt["ts"]      # flp_arrange_song checks the bars it lays it in
        if var.get("chords"):
            comp["blocks"][bname]["chords"] = [[c["start"], c["len"], c["name"]] for c in var["chords"]]
    if audition:
        comp["auditions"] = {base: audition}
    jset = {"compose": comp, "channels": {str(c): {"role": JOURNAL_ROLE.get(r, r)} for r, c in chmap.items()}}
    for p, (c, _, _) in drum_map.items():
        jset["channels"].setdefault(str(c), {"role": p})
    for p in plan_new:
        jset["channels"][str(p["channel"])] = {"role": JOURNAL_ROLE.get(p["role"], p["role"]), "name": p["name"],
                                               **({"mixer": p["insert"]} if p.get("insert") else {})}
    if new_section:
        jset["sections"] = sorted([s for s in v["journal"].get("sections") or []] + [new_section], key=lambda s: s["bar"])
    _journal_set(project, jset, res)
    # the clash check after the journal: it knows the new channels' roles (a low strings line is a bass)
    if H.get("clashes") and what in ("counter", "layer", "melody", "bass", "chords") and nvar == 1 and v["song"]:
        t = time.time()
        try:                                              # only the bars just written (the rest didn't change)
            sl = [x for x in slots[0]] or [(1.0, nb)]
            a_, b_ = min(b for b, _ in sl), max(b + L for b, L in sl)
            cr = H["clashes"](_src(project), bars=f"{a_:g}-{max(a_, b_ - 1):g}", limit=4, journal_from=project)
            mine = {f"(ch{c})" for c in chmap.values()}
            iss = [i for i in cr.get("issues") or [] if any(m_ in str(i.get("parts", "")) for m_ in mine)] or cr.get("issues") or []
            res["clashes"] = {"bars": f"{a_:g}-{max(a_, b_ - 1):g}", "counts": cr.get("counts"),
                              "top": [{k: i.get(k) for k in ("severity", "kind", "parts", "where", "fix")}
                                      for i in iss[:3]], "verdict": cr.get("verdict")}
        except Exception as e:
            res["clashes"] = {"error": f"{type(e).__name__}: {e}"}
        steps["clashes"] = round(time.time() - t, 3)
    if nvar > 1:
        res["next"] = (f"fl_listen each variation's bars (listen), then flp_compose(project, what='pick', "
                       f"section='{base}', variation='A'|'B'|...) keeps one and moves it into place")
    else:
        lb = var_out[0].get("listen") or {}
        res["next"] = f"fl_listen(start_bar={lb.get('start_bar')}, end_bar={lb.get('end_bar')}) - a human should listen"
    res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
    return res


# ----------------------------------------------------------------------------- realism for layers
REALISM_GENRE_WORDS = ("cinematic", "orchestral", "orchestra", "film score", "classical", "soundtrack", "symphonic",
                       "epic orchestral", "trailer")
RPPQ = 960                                               # ticks per beat for realism's timing


def realism_for_genre(genre):
    """True when a genre's starter should be played like a person (orchestral / cinematic genres, or a genre file
    with "realism": true), else None (flp_compose's default)."""
    try:
        gd = genre if isinstance(genre, dict) else genre_data(genre)
    except Exception:
        gd = {"name": str(genre or "")}
    if gd.get("realism") is not None:
        return True if gd["realism"] else None
    names = [_norm(gd.get("name"))] + [_norm(a) for a in gd.get("aliases") or []]
    return True if any(_norm(w) == n for w in REALISM_GENRE_WORDS for n in names) else None


def _part_family(v, r, ch, plan_new, g, layer_role=None):
    """(family, from) of the channel a part goes to: its name / preset / plugin, else the layer role, else the
    genre's sound hint for that role."""
    info = v["channels"].get(ch) or {}
    jc = ((v.get("journal") or {}).get("channels") or {}).get(str(ch)) or {}
    newp = next((p for p in plan_new if p["channel"] == ch), None) or {}
    for t in (info.get("name"), newp.get("name"), jc.get("name"), jc.get("sound"), info.get("sample"), info.get("plugin")):
        f = flh_perform.layer_family(t)
        if f:
            return f, f"channel: {t}"
    lr = (flh_perform.layer_families().get("layer_roles") or {}).get(str(layer_role or "").lower())
    if lr:
        return lr, f"layer role {layer_role}"
    gr = (g.get("roles") or {}).get(str(layer_role or r)) or {}
    f = flh_perform.layer_family(gr.get("hint"), newp.get("sound_hint"))
    if f:
        return f, f"{g.get('name')} sound hint: {gr.get('hint') or newp.get('sound_hint')}"
    return None, None


def _realism_plan(v, roles_needed, chmap, plan_new, g, what, role, realism):
    """{part role: family} of the parts realism plays this time."""
    want = realism if realism is not None else (what in ("layer", "counter"))
    if not want:
        return {}
    fams = flh_perform.realism_families(); out = {}
    for r in roles_needed:
        if r == "drums" or r not in chmap:
            continue
        f, _ = _part_family(v, r, chmap[r], plan_new, g, r if what == "layer" else None)
        if f in fams:
            out[r] = f
    return out


def _realism_rows(rows, notes, r, fam, var, chmap, ctx, v, g, tempo, nb, sec_name, strum, rinfo, rexpr, lab):
    # PC-VERIFY: flp_compose(what='layer', role='strings') with flh_realism loaded sounds played, not sequenced, and
    # no new rub against the melody / chords (result 'clashes' and your ears agree)
    """One part's rows through flh_realism (timing, dynamics, legato a player would use), clash-guarded against the
    other parts sounding there. Without flh_realism (or when it fails) the rows stay as the genre humanized them."""
    fn = H.get("realism_apply")
    key = f"{lab}:{r}" if lab else r
    if not callable(fn):
        rinfo[key] = {"family": fam, "applied": False, "why": "flh_realism isn't loaded: written with the genre's "
                      "humanize (run flp_realism on this part later)"}
        return rows
    raw = _rows(notes, chmap[r], None, g, tempo, var["seed"], strum_ms=strum, humanize=False)
    oth = []
    for r2, ns2 in var["parts"].items():
        if r2 in ("drums", r):
            continue
        oth += [[int(round(n["s"] / 4.0 * RPPQ)), int(round((n["s"] + n["l"]) / 4.0 * RPPQ)), int(n["k"]),
                 int(n.get("v", 64))] for n in ns2 if "k" in n]
    for ch, ns_ in ((ctx or {}).get("notes") or {}).items():
        if ch == chmap[r] or _is_drum(v["roles"].get(ch)):
            continue
        oth += [[int(round(n["s"] / 4.0 * RPPQ)), int(round((n["s"] + n["l"]) / 4.0 * RPPQ)), int(n["k"]),
                 int(n.get("v", 64))] for n in ns_]
    src = [[int(round(x[1] * RPPQ)), int(round((x[1] + x[2]) * RPPQ)), int(x[3]), int(x[4])] for x in raw]
    mt = _meter()
    qb = 4 if mt["legacy"] else mt["qbar"]
    cx = {"ppq": RPPQ, "bpm": float(tempo), "seed": int(var["seed"]), "genre": g.get("name"), "beats_per_bar": qb,
          "sections": [{"name": sec_name, "start": 0, "end": int(round(int(nb) * qb * RPPQ))}], "part": r,
          "source": "compose"}
    if not mt["legacy"]:
        cx["time_signature"] = [mt["num"], mt["den"]]
    try:
        got, ex, rep = flh_perform.realism_run(fn, src, fam, cx, others=oth)
    except Exception as e:
        rinfo[key] = {"family": fam, "applied": False, "why": f"realism failed ({type(e).__name__}: {str(e)[:100]}): "
                      "written with the genre's humanize"}
        return rows
    out = []
    for s_, e_, p_, v_, k in got:
        b = raw[k] if k is not None else [chmap[r], 0, 0, 0, 0, 0]
        out.append([int(b[0]), round(max(0.0, s_ / RPPQ), 5), round(max(0.02, (e_ - s_) / RPPQ), 5), int(p_),
                    _clampv(v_), int(b[5]) if k is not None else 0])
    rinfo[key] = dict(rep, applied=True)
    if ex:
        rexpr[(lab, r)] = ex
    return out


def _expression_ops(project, ch, ex, pname, place, nb, tempo, rinfo, key, view=None):
    """Expression curves for a written layer as ops in the same batch (flh_realism's writer), else a next step.
    The curves are block-relative: they're moved to every bar the block is placed at (song ticks)."""
    w = H.get("realism_expression_ops")
    r = key.split(":")[-1]
    if not callable(w):
        (rinfo.get(key) or {}).update(expression="not written by this batch: run flp_realism on this part after")
        return []
    if view is not None and not _meter()["legacy"] or view is not None and len(view.get("meters") or []) > 1:
        at = [_bar_tick(view, float(b)) * RPPQ / view["P"] for b, _ in place or [(1.0, nb)]]   # song ticks, meter map
    else:
        at = [(float(b) - 1.0) * 4 * RPPQ for b, _ in place or [(1.0, nb)]]
    ex = flh_perform.expression_at(ex, at)
    try:
        cx = {"ppq": RPPQ, "pattern": pname, "place": place, "bars": nb, "tempo": tempo, "source": "compose",
              "part": r, "song_ticks": True}
        if view is not None and int(ch) not in (view.get("channels") or {}):
            cx["new_channel"] = True                      # cloned by this batch: resolved when the batch runs
        return list(w(project, int(ch), ex, cx) or [])
    except Exception as e:
        (rinfo.get(key) or {}).update(expression=f"not written ({type(e).__name__}: {str(e)[:80]}): run flp_realism after")
        return []


def _pick_variation(project, v, section, variation, dry_run, steps, T0):
    auds = (v["registry"].get("auditions") or {})
    auds = {k: a for k, a in auds.items() if a}
    if not auds:
        raise ValueError("nothing to pick: compose with variations=2.. first")
    base = None
    if section:
        base = next((k for k in auds if k.lower() == str(section).lower()), None)
    elif len(auds) == 1:
        base = next(iter(auds))
    if base is None:
        raise ValueError(f"which audition? section= one of {sorted(auds)}")
    au = auds[base]
    lab = str(variation or "").strip().upper()
    if lab not in au["variations"]:
        raise ValueError(f"variation must be one of {sorted(au['variations'])}")
    occ = occurrences(v, au.get("section"))
    slots = _slots_for(occ, au["bars"]) if occ else [(au["start_bar"], au["bars"])]
    ops = [{"tool": "flp_arrange", "ops": [{"op": "delete_bars", "from": au["start_bar"], "to": au["end_bar"]}]}]
    chosen = au["variations"][lab]["patterns"]
    for r, pname in chosen.items():
        ops.append({"tool": "flp_compose_write", "pattern": pname, "place": [[b, L] for b, L in slots], "place_mode": "add",
                    "track": (au.get("tracks") or {}).get(r) or None, "length_bars": au["bars"]})
    unused = [p for l, x in au["variations"].items() if l != lab for p in x["patterns"].values()]
    res = {"picked": lab, "section": au.get("section") or base, "placed_at": [b for b, _ in slots], "patterns": chosen,
           "removed_audition_bars": [au["start_bar"], au["end_bar"]],
           "unused_patterns": unused, "note": "unused variation patterns stay in the project (not in the playlist)"}
    if dry_run:
        res["dry_run"] = True
        return res
    _queued(res, _edit(project, ops, steps))
    blocks = {_pname(base, l): ({"picked": True} if l == lab else {"picked": False}) for l in au["variations"]}
    blocks[base] = {"parts": dict(chosen), "bars": au["bars"], "section": au.get("section"), "picked": True,
                    "what": (v["registry"].get("blocks") or {}).get(_pname(base, lab), {}).get("what", "beat")}
    jset = {"compose": {"blocks": blocks, "auditions": {base: None}}}
    if not occ and au.get("section"):
        jset["sections"] = sorted([s for s in v["journal"].get("sections") or []] +
                                  [{"name": au["section"], "bar": au["start_bar"], "end_bar": au["start_bar"] + au["bars"]}],
                                  key=lambda s: s["bar"])
    _journal_set(project, jset, res)
    res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
    return res


def _patterns_in(v, occ, ch=None):
    """Pattern ids with notes (on channel ch) whose clips start inside the section occurrences (all if no occ)."""
    pids = []
    for pid, p in sorted(v["patterns"].items()):
        if not p["notes"] or (ch is not None and not any(r[2] == ch for r in p["notes"])):
            continue
        if occ:
            if not any(c.get("pattern") == pid and any(o["bar"] - 1e-6 <= c["bar"] < o["end_bar"] for o in occ) for c in v["clips"]):
                continue
        pids.append(pid)
    return pids


def _voicing(project, v, g, key, section, channel, style, dry_run, steps, T0):
    ch = int(channel) if channel is not None else None
    if ch is None:
        cands = [c for c, r in v["roles"].items() if r in ("chords", "pad", "organ", "strings")]
        ch = max(cands, key=lambda c: len(v["notes_by_ch"].get(c, [])), default=None)
    if ch is None:
        raise ValueError("no chord part found: say channel=")
    vo = (g.get("harmony") or {}).get("voicing") or {}
    style = style or vo.get("style", "triad")
    regn = vo.get("register")
    occ = occurrences(v, section)
    pids = _patterns_in(v, occ, ch)
    if not pids:
        raise ValueError(f"channel {ch} has no notes" + (f" in {section}" if section else ""))
    tf = {"ops": [{"op": "revoice", "style": style, "register": regn, "ext": vo.get("extensions"), "roles": ["all"],
                   "channels": [ch]}], "channels": {str(ch): {"role": "chords"}}, "key": key,
          "offsets": {str(c): o for c, o in v["offsets"].items() if c == ch}}
    pred = {}
    for pid in pids:
        _, info = apply_transform([H["_rec_to_note"](r) for r in v["patterns"][pid]["notes"]], tf, v["P"])
        pred[v["patterns"][pid]["name"] or str(pid)] = next((x for k, x in info.items() if "revoice" in k), {})
    res = {"what": "voicing", "channel": ch, "style": style, "register": regn, "key": key, "patterns": pred}
    if dry_run:
        res["dry_run"] = True
        return res
    _queued(res, _edit(project, [{"tool": "flp_compose_write", "pattern": pid, "transform": tf} for pid in pids], steps))
    res["next"] = "fl_listen the section - voicing is taste; flp_restore undoes it"
    res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
    return res


# ============================================================================= flp_arrange_song
def _materials(v):
    reg = v["registry"]
    names = {p["name"].strip().lower(): pid for pid, p in v["patterns"].items() if p.get("name")}
    blocks = {}
    for bn, b in (reg.get("blocks") or {}).items():
        if not isinstance(b, dict):
            continue
        parts = {r: pn for r, pn in (b.get("parts") or {}).items() if pn and pn.strip().lower() in names}
        if parts and b.get("picked") is not False:
            blocks[bn] = dict(b, parts=parts)
    if blocks:
        return blocks
    rx = re.compile(r"^(.*?)\s+(drums|bass|808|sub|chords|keys|melody|lead|counter|pad|strings|arp|bell|choir|pluck|brass|organ)$", re.I)
    P = v["P"]
    m0 = (v.get("meters") or [(0, 4, 4)])[0]
    bar_t = 4 * P if tuple(m0[1:]) == (4, 4) else P * m0[1] * 4.0 / m0[2]   # pattern bars: the project's meter
    for pid, p in v["patterns"].items():
        m = rx.match(p.get("name") or "")
        if m and p["notes"]:
            r = m.group(2).lower()
            r = {"808": "bass", "sub": "bass", "keys": "chords", "lead": "melody"}.get(r, r)
            bars = max(1, int(math.ceil(max(x[0] + x[3] for x in p["notes"]) / bar_t - 1e-6)))
            b = blocks.setdefault(m.group(1), {"parts": {}, "bars": bars})
            b["parts"][r] = p["name"]
            b["bars"] = max(b["bars"], bars)
    return blocks


def _parse_form(form, g):
    forms = g.get("form") or {}
    dflt = forms.get("default") or [["Intro", 4], ["Hook", 8], ["Verse", 16], ["Hook", 8], ["Outro", 4]]
    known = {str(n).lower(): int(b) for n, b in dflt}
    if form is None:
        items = dflt
    elif isinstance(form, str) and form in forms:
        items = forms[form]
    elif isinstance(form, str):
        items = []
        for tok in re.split(r"[,>|]+|\s+-\s+|\s+", form.strip()):
            if not tok:
                continue
            n, _, b = tok.partition(":")
            items.append([n, int(b) if b else known.get(n.lower(), int(g.get("loop_bars", 8)))])
    else:
        items = []
        for it in form:
            if isinstance(it, dict):
                items.append([it["name"], int(it.get("bars") or known.get(str(it["name"]).lower(), 8))])
            elif isinstance(it, (list, tuple)):
                items.append([it[0], int(it[1])])
            else:
                items.append([it, known.get(str(it).lower(), int(g.get("loop_bars", 8)))])
    return [(str(n), int(b)) for n, b in items if int(b) > 0]


def _section_spec(g, name):
    secs = g.get("sections") or {}
    low = name.lower().rstrip("0123456789 ")
    for k, s in secs.items():
        if k.lower() == low:
            return s
    for k, s in secs.items():
        if low.startswith(k.lower()) or k.lower().startswith(low):
            return s
    return {"energy": 0.7, "layers": ["drums", "bass", "chords", "melody"], "drums": "full"}


def _trans_for(g, name, key="into"):
    """The genre's transitions for a section: key 'into' (at its start) or 'after' (where it ends into the next)."""
    tr = ((g.get("transitions") or {}).get(key) or {})
    low = name.lower().rstrip("0123456789 ")
    for k, x in tr.items():
        if k.lower() == low:
            return list(x or [])
    return []


# ----------------------------------------------------------------------------- arrangement ear candy (audio FX)
# Kinds made by flh_fx.make_fx (optional module): where each sits on the section boundary's downbeat.
FX_AUDIO = ("riser", "downlifter", "impact", "reverse_crash", "sub_drop", "tape_stop", "noise_sweep", "cymbal_swell",
            "boom", "reverse_swell", "wind", "rain", "vinyl")
FX_BEDS = ("wind", "rain", "vinyl")                       # loopable textures: under whole quiet sections only
FX_NAMES = {"riser": "Riser", "downlifter": "Downlifter", "impact": "Impact", "reverse_crash": "Reverse Crash",
            "sub_drop": "Sub Drop", "tape_stop": "Tape Stop", "noise_sweep": "Noise Sweep",
            "cymbal_swell": "Cymbal Swell", "boom": "Boom", "reverse_swell": "Reverse Swell", "wind": "Wind Bed",
            "rain": "Rain Bed", "vinyl": "Vinyl Bed"}
FX_ANCHOR = {"riser": "end", "reverse_crash": "end", "tape_stop": "end", "impact": "start", "downlifter": "start",
             "sub_drop": "start", "noise_sweep": "center", "cymbal_swell": "end", "reverse_swell": "end",
             "boom": "start", "wind": "bed", "rain": "bed", "vinyl": "bed"}
FX_BEATS = {"riser": 8, "reverse_crash": 4, "tape_stop": 2, "impact": 8, "downlifter": 8, "sub_drop": 4,
            "noise_sweep": 8, "cymbal_swell": 8, "boom": 8, "reverse_swell": 8, "wind": 16, "rain": 16,
            "vinyl": 16}                                  # used when flh_fx doesn't say (FX_KINDS default_beats)
DRUM_TRANS = ("stop", "dropout", "fill")                  # transitions (transitions=): not ear candy
EC_MOVES = ("snare_build", "gap", "delay_throw")          # ear candy written as notes / a cut / automation
TRANS_KINDS = DRUM_TRANS + FX_AUDIO + EC_MOVES            # every kind the genre data may list
EC_AMOUNTS = ("subtle", "normal", "lots")
EC_MODES = ("organic", "synthetic")                       # real instruments as notes | audio FX + plugin moves
EC_OFF = ("false", "off", "none", "no", "0", "")
EC_AUTO = ("true", "yes", "on", "default", "auto")
ORGANIC_MOVES = ("string_swell", "timpani_roll", "cymbal_roll", "choir_breath", "piano_pickup")
ORG_NAMES = {"string_swell": "String Swell", "timpani_roll": "Timpani Roll", "cymbal_roll": "Cymbal Roll",
             "choir_breath": "Choir Breath", "piano_pickup": "Piano Pickup"}
ORG_FALLBACK = {"default_amount": "subtle", "when": {"genres": [], "words": []},
                "amounts": {"subtle": {"per_boundary": 1, "vel_scale": 0.85},
                            "normal": {"per_boundary": 2, "vel_scale": 1.0},
                            "lots": {"per_boundary": 2, "vel_scale": 1.0}},
                "rules": {"max_per_boundary": {"swell": 1, "roll": 1, "pickup": 1}},
                "levels": {"below_piano_vel": 10, "start_vel": 30, "default_piano_vel": 80},
                "places": [], "moves": {}, "words": {}, "roles": {}}   # data/ear_candy.json without 'organic'
DELAY_PLUGIN = "Fruity Delay 3"                           # every FL edition has it
SECTION_KINDS = (("intro", ("intro",)), ("outro", ("outro", "ending")), ("breakdown", ("breakdown", "break")),
                 ("bridge", ("bridge",)), ("build", ("build", "pre")), ("drop", ("drop",)),
                 ("hook", ("hook", "chorus", "climax", "log drum")))
EC_FALLBACK = {"default_amount": "normal", "below_beat_db": 11.0, "bed_below_music_db": 18.0, "big_energy_step": 0.15,
               "throw_wet_pct": 35.0,
               "amounts": {"subtle": {"per_boundary": 1, "climax_per_boundary": 2, "throws_per_song": 1,
                                      "beds": ["intro", "outro"], "filters": ["intro", "outro"], "level_offset_db": -6},
                           "normal": {"per_boundary": 3, "climax_per_boundary": 3, "throws_per_song": 2,
                                      "beds": ["intro", "outro", "breakdown", "bridge"], "filters": "*",
                                      "level_offset_db": 0},
                           "lots": {"per_boundary": 4, "climax_per_boundary": 5, "spread": True, "throws_per_song": 4,
                                    "beds": ["intro", "outro", "breakdown", "bridge"], "filters": "*",
                                    "level_offset_db": 2}},
               "rules": {"max_per_boundary": {"riser": 1, "hit": 1}, "bed_sections": ["intro", "outro", "breakdown",
                                                                                      "bridge"]},
               "profiles": {}, "kinds": {}}            # data/ear_candy.json missing or damaged: these, no purposes
HOOKISH = ("hook", "chorus", "drop", "climax", "log drum")
FILTER_PLUGIN = "Fruity Filter"                           # every FL edition has it


def _mod(name):
    m_ = sys.modules.get(name)
    if m_ is None:
        import importlib
        m_ = importlib.import_module(name)
    return m_


def _fx_mod():
    """flh_fx (another module, optional): None when it isn't there or has no make_fx - today's behaviour then."""
    mod = sys.modules.get("flh_fx")
    if mod is None:
        try:
            import importlib
            mod = importlib.import_module("flh_fx")
        except Exception:
            return None
    return mod if callable(getattr(mod, "make_fx", None)) else None


def _trans_item(x):
    """A transition entry -> (kind, options): 'riser', 'riser:8' (beats), {'kind': 'riser', 'beats': 8, ...}."""
    if isinstance(x, dict):
        k = str(x.get("kind") or "").strip().lower().replace(" ", "_").replace("-", "_")
        return k, {kk: vv for kk, vv in x.items() if kk != "kind"}
    k, _, b = str(x).strip().lower().partition(":")
    opt = {}
    if b:
        try:
            opt["beats"] = float(b)
        except ValueError:
            pass
    return k.replace(" ", "_").replace("-", "_"), opt


def _fx_anchor(kind, fxm):
    a = FX_ANCHOR.get(kind)
    if a:
        return a
    eo = ((getattr(fxm, "FX_KINDS", None) or {}).get(kind) or {}).get("ends_on")
    return "end" if eo == "downbeat" else "start"


def _fx_default_beats(kind, fxm):
    try:
        return float(((getattr(fxm, "FX_KINDS", None) or {}).get(kind) or {}).get("default_beats") or FX_BEATS[kind])
    except (KeyError, TypeError, ValueError):
        return 4.0


def _ear_candy_cfg(g):
    """The genre's ear-candy settings (data/genres/*.json "ear_candy"): filters (which filter moves), level_db,
    build_beats (the high-pass build before the last hook), intensity."""
    ec = g.get("ear_candy") if isinstance(g.get("ear_candy"), dict) else {}
    return {"filters": [str(x).lower() for x in (ec.get("filters") if ec.get("filters") is not None
                                                 else ["intro", "outro"])],
            "level_db": ec.get("level_db"), "build_beats": float(ec.get("build_beats") or 8),
            "intensity": float(ec.get("intensity") or 1.0),
            "amount": str(ec.get("amount") or "").lower() or None,
            "beds": [str(x).lower() for x in (ec.get("beds") or [])]}


def _ec_data():
    """data/ear_candy.json (the user's copy in the state folder first; cached by mtime), else EC_FALLBACK."""
    for d_ in _data_dirs(""):
        f = os.path.join(d_, "ear_candy.json")
        if not os.path.isfile(f):
            continue
        sig = (f, os.path.getmtime(f))
        if _CACHE.get("ec_sig") == sig:
            return _CACHE["ec"]
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            if not isinstance(d, dict) or not isinstance(d.get("amounts"), dict):
                raise ValueError("no amounts")
        except Exception:
            continue                                      # a damaged user copy: the shipped one
        out = dict(EC_FALLBACK, **d)
        _CACHE["ec"], _CACHE["ec_sig"] = out, sig
        return out
    return EC_FALLBACK


def _sec_kind(name):
    """A section's kind from its name: intro / outro / breakdown / bridge / build / drop / hook, else 'section'."""
    low = str(name or "").strip().lower()
    for kind, pre in SECTION_KINDS:
        if low.startswith(pre):
            return kind
    return "section"


def _words_in(text, words):
    low = str(text or "").lower()
    return next((w for w in words if re.search(r"(?<![a-z])" + re.escape(str(w).lower()) + r"(?![a-z])", low)), None)


EC_VALUES = "off | organic | subtle | normal | lots | synthetic (or 'organic:normal', 'synthetic:lots')"


def _ec_parse(x, strict=True):
    """An ear_candy value (argument, journal or collection profile) -> (off, mode, amount): False / 'off' -> off;
    True / 'auto' / None -> nothing asked; 'organic' | 'synthetic' (':subtle' ... sets the amount too); 'subtle' |
    'normal' | 'lots'; {mode, amount} (a dict from a journal). Anything else: ValueError (strict) or ignored."""
    if x is None or x is True:
        return False, None, None
    if x is False:
        return True, None, None
    if isinstance(x, dict):
        if x.get("off") is True or str(x.get("mode") or "").strip().lower() in ("off", "none", "no", "false"):
            return True, None, None
        md = str(x.get("mode") or "").strip().lower() or None
        am = str(x.get("amount") or "").strip().lower() or None
        ok = (md is None or md in EC_MODES) and (am is None or am in EC_AMOUNTS)
        if not ok and strict:
            raise ValueError(f"ear_candy: {EC_VALUES} (got {x!r})")
        return False, md if md in EC_MODES else None, am if am in EC_AMOUNTS else None
    s = str(x).strip().lower()
    if s in EC_OFF:
        return True, None, None
    if s in EC_AUTO:
        return False, None, None
    m_, _, a_ = s.partition(":")
    m_, a_ = m_.strip(), a_.strip()
    if m_ in EC_MODES and (not a_ or a_ in EC_AMOUNTS):
        return False, m_, a_ or None
    if m_ in EC_AMOUNTS and not a_:
        return False, None, m_
    if strict:
        raise ValueError(f"ear_candy: {EC_VALUES} (got '{x}')")
    return False, None, None


def _ec_str(mode, amount):
    """(mode, amount) -> the value the journal keeps ('organic', 'organic:normal', 'lots')."""
    return ":".join(x for x in (mode, amount) if x) or None


def _org_data(data):
    """data/ear_candy.json 'organic' over ORG_FALLBACK (key by key)."""
    o = data.get("organic") if isinstance(data.get("organic"), dict) else {}
    return dict(ORG_FALLBACK, **o)


def _ec_settings(ear_candy, g, v, project):
    """Ear candy on or off, its mode and amount: None when off. ear_candy given > the song journal's
    compose.ear_candy > the collection profile's ear_candy (each: off | organic | subtle | normal | lots | synthetic).
    The mode: asked for, else the genre's ear_candy.mode, else 'organic' for the organic genres (data
    organic.when.genres) and for songs whose journal role / style, the collection's style or the song's role / style
    in the collection says piano-led / cinematic / sound real / faint (organic.when.words), else 'synthetic'.
    The amount: asked for, else (organic) organic.default_amount, else (synthetic) the profile's - only when
    'synthetic' was asked for (data profiles, the old cinematic set) - the genre's ear_candy.amount, the default.
    -> {mode, mode_from, amount, from, cfg, profile: (name, dict, why) | None, data, given}."""
    g_off, g_mode, g_amt = _ec_parse(ear_candy)
    if g_off:
        return None
    data = _ec_data()
    amounts = data.get("amounts") or EC_FALLBACK["amounts"]
    org = _org_data(data)
    jr = v.get("journal") or {}
    try:                                                  # the collection's taste only for its own songs
        prof = _store().profile_for(project, journal=jr if v.get("journal") is not None else None) or {}
    except Exception:
        prof = {}
    song = jr.get("song") or os.path.splitext(os.path.basename(project))[0]
    sp = ((prof.get("songs") or {}).get(song) or {}) if isinstance(prof.get("songs"), dict) else {}
    jx, cx = (jr.get("compose") or {}).get("ear_candy"), prof.get("ear_candy")
    j_off, j_mode, j_amt = _ec_parse(jx, strict=False)
    c_off, c_mode, c_amt = _ec_parse(cx, strict=False)
    if not (g_mode or g_amt) and (j_off or (not (j_mode or j_amt) and c_off)):
        return None                                       # the song / the collection said no ear candy
    texts = [("the song journal's role", jr.get("role")), ("the song journal's style", jr.get("style")),
             ("the collection's style", prof.get("style")), ("the song's role in the collection", sp.get("role")),
             ("the song's style in the collection", sp.get("style"))]
    gm = str((g.get("ear_candy") or {}).get("mode") or "").strip().lower() if isinstance(g.get("ear_candy"), dict) \
        else ""
    wh = org.get("when") or {}
    hit = next(((where, w) for where, t in texts if t and (w := _words_in(t, wh.get("words") or []))), None)
    for md, src in ((g_mode, "given"), (j_mode, "song journal"), (c_mode, "collection profile"),
                    (gm if gm in EC_MODES else None, f"genre {g['name']}"),
                    ("organic" if g["name"] in (wh.get("genres") or []) else None, f"genre {g['name']}"),
                    ("organic" if hit else None, hit and f"{hit[0]} says '{hit[1]}'"),
                    ("synthetic", "default")):
        if md:
            mode, mode_from = md, src
            break
    given = _ec_str(g_mode, g_amt)
    if mode == "organic":
        oam = org.get("amounts") or ORG_FALLBACK["amounts"]
        for amt, src in ((g_amt, "given"), (j_amt, "song journal"), (c_amt, "collection profile"),
                         (org.get("default_amount"), "organic default"), ("subtle", "default")):
            if amt in oam:
                return {"mode": "organic", "mode_from": mode_from, "amount": amt, "from": src, "cfg": oam[amt],
                        "profile": None, "data": data, "given": given}
    want_prof = [x.get("profile") for x in (jx, cx) if isinstance(x, dict) and "profile" in x]
    profile = None
    profs = data.get("profiles") or {}
    if want_prof and want_prof[0] in profs:
        profile = (want_prof[0], profs[want_prof[0]], "asked for in the journal / collection")
    elif mode_from != "default" and not (want_prof and want_prof[0] in (False, None, "none", "off")):
        for name, p_ in profs.items():                    # the old synthetic sets: only when 'synthetic' was asked
            pw = p_.get("when") or {}
            if g["name"] in (pw.get("genres") or []):
                profile = (name, p_, f"genre {g['name']}")
                break
            ph = next(((where, w) for where, t in texts if t and (w := _words_in(t, pw.get("words") or []))), None)
            if ph:
                profile = (name, p_, f"{ph[0]} says '{ph[1]}'")
                break
    cfg_g = _ear_candy_cfg(g)
    base = {"mode": "synthetic", "mode_from": mode_from, "profile": profile, "data": data, "given": given}
    for amt, src in ((g_amt, "given"), (j_amt, "song journal"), (c_amt, "collection profile"),
                     (profile and profile[1].get("amount"), profile and f"profile {profile[0]}"),
                     (cfg_g["amount"], f"genre {g['name']}"), (data.get("default_amount"), "default")):
        if isinstance(amt, str) and amt.lower() in amounts:
            return dict(base, amount=amt.lower(), **{"from": src, "cfg": amounts[amt.lower()]})
    return dict(base, amount="normal", **{"from": "default",
                                          "cfg": amounts.get("normal") or EC_FALLBACK["amounts"]["normal"]})


def _ec_tags(table, i, data):
    """The places a boundary has a job for (see data/ear_candy.json about): i = the index of the section it goes
    into (len(table) = the song's last downbeat)."""
    if i >= len(table):
        return {"end"}
    into, frm = table[i], table[i - 1]
    tags = {"section", _sec_kind(into["section"])}
    if _sec_kind(frm["section"]) in ("hook", "drop"):
        tags.add("after_hook")
    en = lambda r: float(r.get("energy") or 0.0)
    if en(into) - en(frm) >= float(data.get("big_energy_step") or 0.15) - 1e-9:
        tags.add("big")
    rest = [(en(r), j) for j, r in enumerate(table) if j > 0]
    if rest and max(rest)[1] == i:                        # the highest energy, the last one when tied
        tags.add("climax")
    return tags


def _ec_pick(items, tags, ec, gname, climax=False):
    """The ear candy one boundary gets, with purpose: items [(kind, opt)] in priority order (+ the genre's other
    fitting kinds when the amount spreads). A kind only goes where its purpose is; one riser, one big hit (rules);
    at most the amount's per_boundary. -> (chosen, skipped [(kind, why)])."""
    data, cfg = ec["data"], ec["cfg"]
    kinds = data.get("kinds") or {}
    maxr = (data.get("rules") or {}).get("max_per_boundary") or {}
    items = list(items)
    if cfg.get("spread") and not ec.get("profile"):
        have = {k for k, _ in items}
        items += [(k, {}) for k, kd in kinds.items() if k not in have and gname in (kd.get("genres") or [])
                  and kd.get("role") not in ("bed", "throw")]
    budget = int(cfg.get("climax_per_boundary" if climax else "per_boundary") or 0)
    chosen, skipped, roles = [], [], {}
    for kind, opt in items:
        kd = kinds.get(kind) or {}
        role = kd.get("role")
        if role in ("bed", "throw") or kind in FX_BEDS or kind == "delay_throw":
            continue                                      # beds go under sections, throws on phrase ends
        if kd.get("purpose") and not set(kd["purpose"]) & tags:
            skipped.append((kind, "no job at this change"))
            continue
        if role and role in maxr and roles.get(role, 0) >= int(maxr[role]):
            first = next(k for k, _ in chosen if (kinds.get(k) or {}).get("role") == role)
            skipped.append((kind, f"one {'big hit' if role == 'hit' else role} per change ({first})"))
            continue
        if len(chosen) >= budget:
            skipped.append((kind, f"ear_candy '{ec['amount']}': {budget} per change"))
            continue
        chosen.append((kind, opt))
        roles[role] = roles.get(role, 0) + 1
    return chosen, skipped


def _ec_levels(ec, g, rec, reg):
    """(FX insert dB, Texture insert dB): under the beat = the loudest drum level of the template (levels_db) minus
    below_beat_db (or the genre's ear_candy.level_db), the bed under the music = the loudest music level minus
    bed_below_music_db; both + the amount's level_offset_db."""
    data, off = ec["data"], float(ec["cfg"].get("level_offset_db") or 0.0)
    lv = {str(k).lower(): float(x) for k, x in ((rec or {}).get("levels_db") or {}).items()
          if isinstance(x, (int, float))}
    have = {str(r).lower() for r in (reg.get("channels") or {})} if isinstance(reg.get("channels"), dict) else set()
    drums_ = [lv[r] for r in lv if r in DRUM_PARTS and (not have or r in have or r in ("kick", "snare"))]
    music = [lv[r] for r in lv if r in ("bass", "808", "chords", "melody", "keys", "lead", "pad", "strings", "pluck",
                                        "counter", "piano") and (not have or r in have)] or \
        [lv[r] for r in ("bass", "chords", "melody") if r in lv]
    cfg_g = _ear_candy_cfg(g)
    base = float(cfg_g["level_db"]) if cfg_g["level_db"] is not None else \
        ((max(drums_) if drums_ else -3.0) - float(data.get("below_beat_db") or 11.0))
    bed = (max(music) if music else -6.0) - float(data.get("bed_below_music_db") or 18.0)
    return round(base + off, 1), round(bed + off, 1)


def _fx_channel_plan(v, kinds, reg, fx_dir):
    """{kind: {channel, name, reuse}} - the sampler channel each FX kind plays on: the one the journal recorded
    (still a sampler with that name), or a sampler named for the kind whose sample is in an _fx folder (a template
    saved from an arranged song brings them), else a new channel (index = the next free one, as flp_compose_fx appends them in this order)."""
    N = _mod("flh_newsong")
    chans = v["channels"]
    nxt = max(chans, default=-1) + 1
    rec = (reg.get("fx_channels") or {}) if isinstance(reg.get("fx_channels"), dict) else {}
    names = {}
    for c, x in chans.items():
        names.setdefault(str(x.get("name") or "").strip().lower(), []).append(c)
    out, taken = {}, set()
    for kind in kinds:
        nm = FX_NAMES.get(kind, kind.replace("_", " ").title())
        ch = None
        try:
            c0 = int(rec.get(kind)) if rec.get(kind) is not None else None
        except (TypeError, ValueError):
            c0 = None
        if c0 in chans and chans[c0].get("type") == 0 and str(chans[c0].get("name") or "").strip().lower() == nm.lower():
            ch = c0
        if ch is None:
            for c in names.get(nm.lower(), []):
                if chans[c].get("type") != 0 or c in taken:
                    continue
                try:
                    p = N._chan_sample_path(v["d"], v["ev"], c) or ""
                except Exception:
                    p = ""
                pd = os.path.dirname(str(p).replace("\\", "/"))
                # ours: no sample yet, or one in an '_fx' folder (this song's, or the song a template was saved from)
                if not p or os.path.basename(pd).lower() == "_fx":
                    ch = c
                    break
        if ch is not None:
            taken.add(ch)
            out[kind] = {"channel": ch, "name": nm, "reuse": True}
            continue
        if names.get(nm.lower()):                         # the name is the user's own channel: ours gets 'FX' on
            nm = f"{nm} FX"
        out[kind] = {"channel": nxt, "name": nm, "reuse": False}
        nxt += 1
    return out


def _fx_crash_sample(v, dm, project):
    """The kit's crash sample file (drum map / journal role crash, a sampler channel), or None."""
    N = _mod("flh_newsong")
    cands = []
    if dm.get("crash"):
        cands.append(int(dm["crash"][0]))
    for c, x in ((v.get("journal") or {}).get("channels") or {}).items():
        if isinstance(x, dict) and str(x.get("role") or "").lower() == "crash":
            try:
                cands.append(int(c))
            except ValueError:
                pass
    cands += [c for c, r in sorted((v.get("roles") or {}).items()) if r == "crash"]   # a channel named Crash / Cymbal
    for c in dict.fromkeys(cands):
        if (v["channels"].get(c) or {}).get("type") != 0:
            continue
        try:
            p = N._chan_sample_path(v["d"], v["ev"], c)
            p = H["_expand_sample"](p, project) if p and H.get("_expand_sample") else p
        except Exception:
            p = None
        if p and os.path.isfile(p):
            return p
    return None


def _mixer_track_named(v, *words):
    """A mixer insert whose name is one of `words` (case-insensitive), else None."""
    try:
        names = H["_mixer_names"](v["d"], v["ev"])
    except Exception:
        return None
    want = {w.lower() for w in words}
    return next((t for t, n in sorted(names.items()) if t > 0 and str(n).strip().lower() in want), None)


def _filter_plan(v, g, table, s0, total, reg, cfg):
    """Filter moves (automation of a Fruity Filter on the Music bus, else the master): intro opens, a high-pass
    build before the last hook snaps open on its downbeat, outro closes. -> (ops, placed, notes)."""
    want = set(cfg["filters"]) & {"intro", "build", "outro"}
    if not want or not table:
        return [], [], [], None
    N = _mod("flh_newsong")
    try:
        ME = _mod("flh_mixeng")
        mp = ME.plugin_map(FILTER_PLUGIN) or {}
        to_norm = ME.to_norm
    except Exception:
        mp, to_norm = {}, None
    ctl = mp.get("controls") or {}
    cut = ctl.get("cutoff_hz")
    if not cut or to_norm is None:
        return [], [], [f"filter moves skipped: no {FILTER_PLUGIN} map with a cutoff control (data/plugin_maps)"], None
    host = _mixer_track_named(v, "music bus", "music") or 0
    d, ev = v["d"], v["ev"]
    fx_now = N._effects(d) or {}
    norm = lambda s_: re.sub(r"[^a-z0-9]", "", str(s_ or "").lower())
    old = reg.get("filter") if isinstance(reg.get("filter"), dict) else {}
    slot, ops = None, []
    if old and int(old.get("track", -1)) == host and \
            norm((fx_now.get((host, int(old.get("slot", -1)))) or {}).get("plugin")) == norm(FILTER_PLUGIN):
        slot = int(old["slot"])                           # ours from an earlier arrangement: automate it again
    if slot is None:
        try:
            e = N._find_plugin(FILTER_PLUGIN)
        except Exception:
            e = None
        fst = (e or {}).get("default")
        if not fst or not N._fst_ok(fst):
            return [], [], [f"filter moves skipped: {FILTER_PLUGIN} isn't in FL's plugin database on this PC (FL "
                            "builds it on its first start / plugin scan)"], None
        try:
            slots = H["_mixer_slots"](d, ev)
        except Exception:
            slots = {}
        free = sorted(s_ for (t_, s_), (first, _) in slots.items() if t_ == host and first is None)
        if not free:
            return [], [], [f"filter moves skipped: no free effect slot on {'the master' if not host else f'insert {host}'}"], None
        slot = free[0]
        ops.append({"tool": "flp_load_plugin", "preset": fst, "track": host, "slot": slot})
    where = ("Music Bus" if host else "Master") + f" slot {slot + 1}"
    n = lambda hz: round(to_norm(cut, hz), 4)
    pts, placed = [], []
    first, last = table[0], table[-1]
    end = s0 + total
    if "intro" in want and first["section"].lower().startswith("intro") and first["bars"] >= 2:
        pts.append(("intro", int(cut["index"]), [(first["bar"], n(250)), (first["bar"] + first["bars"], 1.0)]))
        placed.append({"bar": first["bar"], "kind": "filter: intro opens (low-pass 250 Hz -> open)",
                       "bars": first["bars"], "on": where})
    if "build" in want and ctl.get("low_pass_pct") and ctl.get("high_pass_pct"):
        hooks = [i for i, r in enumerate(table) if i > 0 and r["section"].lower().startswith(HOOKISH)]
        if hooks:
            j = hooks[-1]
            prev, bnd = table[j - 1], table[j]["bar"]
            qb = 4.0                                      # quarter beats per bar of the bars that build
            try:
                qb = float(meter_info(prev.get("meter") or "4/4")["qbar"])
            except Exception:
                pass
            bb = min(float(prev["bars"]), cfg["build_beats"] / qb)
            if bb >= 0.5:
                b0, eps = bnd - bb, 1.0 / 64
                lp, hp = ctl["low_pass_pct"], ctl["high_pass_pct"]
                pts.append(("build cutoff", int(cut["index"]), [(b0, n(60)), (bnd - eps, n(1500)), (bnd, 1.0)]))
                pts.append(("build hp", int(hp["index"]), [(b0, 0.0), (b0 + eps, 1.0), (bnd - eps, 1.0), (bnd, 0.0)]))
                pts.append(("build lp", int(lp["index"]), [(b0, 1.0), (b0 + eps, 0.0), (bnd - eps, 0.0), (bnd, 1.0)]))
                placed.append({"bar": round(b0, 3), "kind": f"filter: high-pass build into {table[j]['section']}, "
                               f"open on bar {bnd:g}", "bars": round(bb, 3), "on": where})
    if "outro" in want and last["section"].lower().startswith("outro") and last["bars"] >= 2 and len(table) > 1:
        pts.append(("outro", int(cut["index"]), [(last["bar"], 1.0), (end, n(300))]))
        placed.append({"bar": last["bar"], "kind": "filter: outro closes (open -> low-pass 300 Hz)",
                       "bars": last["bars"], "on": where})
    if not pts:
        return [], [], [], None
    if not any(w == "intro" for w, _, _ in pts):          # a song played from bar 1 starts open (FL holds the
        pts.insert(0, ("reset", int(cut["index"]), [(s0, 1.0), (s0 + 0.25, 1.0)]))   # last automation value)
    # PC-VERIFY: Fruity Filter's cutoff (param 0) / Low pass / High pass knobs follow these clips on the Music bus
    # (or master); the cutoff value scale matches data/plugin_maps/fruity_filter.json; after the build clips the
    # filter is fully open low-pass again; starting playback at bar 1 after the outro plays open
    for what, pp, ps in pts:
        ops.append({"tool": "flp_add_automation", "track": host, "slot": slot, "plugin_param": pp,
                    "points": [{"bar": round(b_, 4), "value": x_} for b_, x_ in ps],
                    "name": f"Filter {what} @{s0:g}", "replace": True})
    return ops, placed, [], {"track": host, "slot": slot}


def _bed_loop_bars(lens):
    """The texture loop length in bars: the largest divisor (<= 4 bars) of every bed section's length, so the clips
    tile each section exactly and nothing rings into the next one."""
    g_ = 0
    for L in lens:
        g_ = math.gcd(g_, max(1, int(round(L))))
    return next((b for b in (4, 3, 2, 1) if g_ % b == 0), 1)


def _place_fx(project, v, g, fxm, fx_plan, entries, gen, trans_done, notes_out, sec_mt, s0, akey, seed, dm, reg,
              dry_run, steps, fx_dir=None, ec=None, skipped=None):
    """Audio FX for the planned boundaries: one WAV per kind (flh_fx.make_fx at the song's tempo / key, in
    <project folder>/_fx), one sampler channel per kind (reused across boundaries and arrangements), one pattern per
    kind (a note as long as the WAV), clips placed so the sound ends on / starts on / is centred on the downbeat;
    texture beds (plan entries with bed=True) loop under their whole section on a quiet 'Texture' insert. Never two
    risers sounding at once. -> ([flp_compose_fx ops], info for the result, {kind: channel plan}, kinds placed)."""
    t0 = time.time()
    cfg = _ear_candy_cfg(g)
    skipped = skipped if skipped is not None else []
    ekinds = (ec or {}).get("data", {}).get("kinds") or {}
    role_of = lambda k_: (ekinds.get(k_) or {}).get("role") or ("bed" if k_ in FX_BEDS else
                                                                "riser" if FX_ANCHOR.get(k_) == "end" else None)
    beds = [p_ for p_ in fx_plan if p_.get("bed")]
    kinds = list(dict.fromkeys([p_["kind"] for p_ in fx_plan if not p_.get("bed")] + [p_["kind"] for p_ in beds]))
    beats_of, inten = {}, {}
    loop_bars = {}
    for k in {p_["kind"] for p_ in beds}:
        lb = _bed_loop_bars([p_["len"] for p_ in beds if p_["kind"] == k])
        m0 = sec_mt[next(p_["idx"] for p_ in beds if p_["kind"] == k)]
        loop_bars[k] = lb
        beats_of[k] = lb * float(m0["qbar"] or 4.0)
    for p_ in fx_plan:                                    # one sound per kind: the first boundary's length wins
        if p_.get("bed"):
            inten.setdefault(p_["kind"], cfg["intensity"])
            continue
        try:
            b_ = float(p_["opt"].get("beats") or _fx_default_beats(p_["kind"], fxm))
        except (TypeError, ValueError):
            b_ = _fx_default_beats(p_["kind"], fxm)
        beats_of.setdefault(p_["kind"], max(0.25, min(64.0, b_)))
        inten.setdefault(p_["kind"], float(p_["opt"].get("intensity") or 1.0) * cfg["intensity"])
    fx_dir = os.path.abspath(fx_dir) if fx_dir else os.path.join(os.path.dirname(os.path.abspath(project)), "_fx")
    bpm = float(v["tempo"])
    if len({round(float(b_), 2) for _, b_ in v.get("tempos") or []}) > 1:
        notes_out.append(f"the song changes tempo: the FX were made at {bpm:g} BPM, so the ones after a tempo change "
                         "may not end exactly on their downbeat")
    try:
        chp = _fx_channel_plan(v, kinds, reg, fx_dir)
    except Exception as e:
        notes_out.append(f"audio FX skipped: no sampler channel could be planned ({type(e).__name__}: {str(e)[:80]})")
        return [], None, {}, []
    crash = _fx_crash_sample(v, dm, project) if "reverse_crash" in kinds else None
    made = {}
    if dry_run:
        made = {k: {"path": None, "beats": beats_of[k]} for k in kinds}
    else:
        try:
            H["writable"](fx_dir)
            os.makedirs(fx_dir, exist_ok=True)
        except Exception as e:
            notes_out.append(f"audio FX skipped: can't write {fx_dir} ({type(e).__name__}: {str(e)[:80]})")
            return [], None, {}, []
        for k in kinds:
            try:
                r_ = fxm.make_fx(k, bpm, beats_of[k], fx_dir, key=akey, seed=int(seed), intensity=inten[k],
                                 sample=crash if k == "reverse_crash" else None)
                if not r_ or not os.path.isfile(r_.get("path") or ""):
                    raise RuntimeError("no file came back")
                made[k] = r_
            except Exception as e:
                notes_out.append(f"{FX_NAMES.get(k, k)} not made ({type(e).__name__}: {str(e)[:80]})")
    steps["fx"] = round(time.time() - t0, 3)
    used, placed = [], []
    why = lambda k_: (ekinds.get(k_) or {}).get("why")
    for p_ in fx_plan:
        k, idx = p_["kind"], p_["idx"]
        r_ = made.get(k)
        if not r_:
            continue
        pname = chp[k]["name"]
        if p_.get("bed"):                                 # tile the whole section: loop-length clips, none longer
            # PC-VERIFY: the bed's sampler (envelope off) plays each loop to the end of its note and stops at the
            # section's end; no click at the loop points; the 'Texture' insert sits ~18 dB under the music
            lb, bar0, L = loop_bars[k], float(p_["bar"]), float(p_["len"])
            off = 0.0
            while off < L - 1e-6:
                entries.append({"pattern": pname, "role": f"fx:{k}", "bar": round(bar0 + off, 3),
                                "len": round(min(lb, L - off), 3), "sec": idx})
                off += lb
            if k not in used:
                used.append(k)
            trans_done.append({"into": p_["into"], "bar": bar0, "kind": k, "bars": L, "loop_beats": beats_of[k],
                               "at": f"under {p_['into']} (bars {bar0:g}-{bar0 + L:g}), looped",
                               "purpose": f"under {p_['into']} at bar {bar0:g}", "why": why(k),
                               "file": os.path.basename(r_["path"]) if r_.get("path") else None,
                               "channel": chp[k]["channel"]})
            continue
        bnd = float(p_["bar"])
        beats = float(r_.get("beats") or beats_of[k])
        anc = _fx_anchor(k, fxm)
        m_ = sec_mt[idx] if anc == "start" and idx < len(sec_mt) else sec_mt[idx - 1]
        span = beats / float(m_["qbar"] or 4.0)           # bars of the meter it plays in
        if anc == "start" and p_["into"] == "end":
            continue                                      # nothing after the song's last downbeat to start on
        start = bnd - span if anc == "end" else (bnd - span / 2.0 if anc == "center" else bnd)
        if start < max(1.0, s0) - 1e-6:
            notes_out.append(f"{FX_NAMES.get(k, k)} into {p_['into']} skipped: it needs {beats:g} beats before bar "
                             f"{bnd:g} and the arrangement starts at bar {s0:g}")
            continue
        clash = [x for x in placed if x[0] == k and x[1] < start + span - 1e-6 and start < x[1] + x[2] - 1e-6]
        if clash:
            continue                                      # the same sound still playing from the last boundary
        b3 = round(start, 3)
        ln = round(bnd - b3, 3) if anc == "end" else round(span, 3)   # ends exactly on the downbeat
        rl = role_of(k)
        if rl == "riser":                                 # never two risers sounding at once (rules)
            st = [x for x in placed if x[3] == "riser" and x[1] < b3 + ln - 1e-6 and b3 < x[1] + x[2] - 1e-6]
            if st:
                skipped.append({"kind": k, "into": p_["into"], "bar": bnd, "why": f"no stacked risers ({st[0][0]} "
                                                                                    "is still rising)"})
                continue
        if k == "tape_stop":                              # the music winds down under it: cut where it starts
            for e in p_["ending"]:
                if e["len"] > ln + 1e-6:
                    e["len"] = round(e["len"] - ln, 3)
        entries.append({"pattern": pname, "role": f"fx:{k}", "bar": b3, "len": ln, "sec": idx - 1})
        placed.append((k, b3, ln, rl))
        if k not in used:
            used.append(k)
        at = {"end": f"ends on bar {bnd:g}", "start": f"starts on bar {bnd:g}", "center": f"centred on bar {bnd:g}"}[anc]
        trans_done.append({"into": p_["into"], "bar": b3, "kind": k, "beats": beats, "at": at,
                           "purpose": p_.get("purpose") or f"into {p_['into']} at bar {bnd:g}", "why": why(k),
                           "file": os.path.basename(r_["path"]) if r_.get("path") else None,
                           "channel": chp[k]["channel"]})
    if not used:
        return [], None, {}, []
    used = [k for k in kinds if k in used]               # the plan's order (fx, then beds)
    if len(used) < len(kinds):                            # a kind made but never placed: plan the channels again so
        try:                                              # the new indices follow on without a gap
            chp2 = _fx_channel_plan(v, used, reg, fx_dir)
        except Exception:
            chp2 = None
        if chp2:
            for t in trans_done:
                if t.get("kind") in chp2 and t.get("channel") == chp[t["kind"]]["channel"]:
                    t["channel"] = chp2[t["kind"]]["channel"]
            chp = chp2
    rec, prof = {}, {}
    N = _mod("flh_newsong")
    try:
        rec = N.recipe(g["name"])[0]
        prof = _store().profile_get() or {}
    except Exception:
        pass
    col = lambda k_: (N._palette_color(rec, k_, "fx", prof) if rec else "#3A3420")
    pats_by_name = {(x.get("name") or "").strip().lower(): x for x in v["patterns"].values()}
    specs = {"fx": [], "bed": []}
    for k in kinds:                                       # the channel plan's order: new indices follow it
        if k not in used:
            continue
        ch = chp[k]["channel"]
        old = pats_by_name.get(chp[k]["name"].lower()) or {}
        clear = sorted({int(r[2]) for r in old.get("notes") or []} | {ch})
        gen[chp[k]["name"]] = {"rows": [[ch, 0.0, float(made[k].get("beats") or beats_of[k]), 60, 100, 0]],
                               "clear": clear, "role": f"fx:{k}"}
        sp = {"name": chp[k]["name"], "sample": made[k].get("path"), "color": col(k), "kind": k}
        sp["channel" if chp[k]["reuse"] else "expect"] = ch
        specs["bed" if k in FX_BEDS else "fx"].append(sp)
        chp[k]["sample"] = made[k].get("path")
    if ec:
        lvl, bed_lvl = _ec_levels(ec, g, rec, reg)
    else:
        lvl = cfg["level_db"] if cfg["level_db"] is not None else (rec.get("levels_db") or {}).get("fx", -14)
        bed_lvl = (rec.get("levels_db") or {}).get("texture", -18)
    bus = _mixer_track_named(v, "music bus", "music")
    ops = []
    for what, ins_name, lv_ in (("fx", "FX", lvl), ("bed", "Texture", bed_lvl)):
        if not specs[what]:
            continue
        op = {"tool": "flp_compose_fx", "channels": specs[what], "insert": ins_name, "color": col("fx"),
              "level_db": lv_}
        if bus:
            op["bus"] = bus
        ops.append(op)
    info = {"fx_channels": {k: {"channel": chp[k]["channel"], "name": chp[k]["name"],
                                "new": not chp[k]["reuse"]} for k in used},
            "insert": "FX" + (" (-> Music Bus)" if bus else ""), "folder": fx_dir}
    if specs["fx"]:
        info["fx_level_db"] = lvl
    if specs["bed"]:
        info["texture_insert"] = "Texture" + (" (-> Music Bus)" if bus else "")
        info["texture_level_db"] = bed_lvl
    if dry_run:
        info["note"] = "dry run: no FX files were made"
    return ([] if dry_run else ops), info, {k: chp[k] for k in used}, used


def flp_compose_fx(project, output, channels, insert="FX", color=None, level_db=None, bus=None):
    """The arrangement's FX sampler channels in one write: channels [{name, sample, channel? (reuse that sampler:
    its sample is swapped) | expect? (the index a new channel must get), color?, kind?}]. New ones are copies of
    a sampler the project has (else FL's channel block); every one plays its whole sample (volume envelope off,
    no reverse / cut group / pitch / filter) into ONE mixer insert named `insert` (found by name, else a free one,
    named + colored, routed to `bus` when given)."""
    H["writable"](output)
    N, SM = _mod("flh_newsong"), _mod("flh_sampler")
    d = H["_load"](project)
    ev = H["_events"](d)
    names = H["_mixer_names"](d, ev)
    ins = next((t for t, nm_ in sorted(names.items()) if t > 0 and str(nm_).strip().lower() in
                (str(insert).lower(), str(insert).lower() + " bus")), None)
    made_ins = False
    if ins is None:
        ins = N._free_insert(d, ev)
        made_ins = ins is not None
    types = N._chan_types(d, ev)
    src = next((c for c, t in sorted(types.items()) if t == 0), None)
    done = []
    for spec in channels:
        ev = H["_events"](d)
        types = N._chan_types(d, ev)
        sample, nm = spec["sample"], str(spec["name"])
        if not os.path.isfile(sample):
            raise FileNotFoundError(sample)
        if spec.get("channel") is not None:
            ch = int(spec["channel"])
            if types.get(ch) != 0:
                raise ValueError(f"channel {ch} isn't a sampler any more: arrange again")
            d = N._chan_edit(d, ch, {H["EV_SAMPLE_PATH"]: N._txt(N._stored_sample_path(sample)),
                                     H["EV_DISPLAY_NAME"]: N._txt(nm)})
            created = False
        else:
            d, ch = N._append_channel(d, N._sampler_block(d, ev, sample, nm, color=spec.get("color") or color,
                                                          insert=ins or 0, src=src))
            if spec.get("expect") is not None and ch != int(spec["expect"]):
                raise ValueError(f"the new {nm} channel got index {ch}, not {spec['expect']}: the project changed "
                                 "since it was read - arrange again")
            created = True
        try:                                              # a one-shot: the whole file plays, whatever the note length
            d, sr = SM._apply(d, H["_events"](d), ch, {"envelope": False, "reverse": False, "cut_itself": False,
                                                       "pitch_st": 0, "fine_cents": 0, "cutoff": "100%",
                                                       "resonance_pct": 0}, absolute=True)
            skipped = [x["field"] for x in sr.get("skipped") or []]
        except Exception as e:
            skipped = [f"one-shot settings: {type(e).__name__}"]
        done.append({"channel": ch, "name": nm, "created": created, **({"kind": spec["kind"]} if spec.get("kind") else {}),
                     **({"not_set": skipped} if skipped else {})})
    # PC-VERIFY: a copied sampler with the volume envelope switched off plays the whole FX WAV from a note of any
    # length (riser ends on the downbeat, impact tail rings out), at the WAV's own pitch on C5
    H["_save"](d, output)
    res = {"output": output, "channels": done, "insert": ins or 0}
    if made_ins:
        spec = {"track": ins, "name": str(insert), "color": color or "#3A3420"}
        if level_db is not None:
            spec["volume_db"] = float(level_db)
        if bus:
            spec.update(route_to=[int(bus)], unroute=[0])
        try:
            H["flp_mixer"](output, output, [spec])
            res["insert_made"] = True
        except Exception as e:
            res["insert_note"] = f"insert {ins} not named/routed ({str(e)[:60]})"
    elif not ins:
        res["insert_note"] = "no free mixer insert: the FX channels play into the Master"
    return res


LAYER_COLOR_FAMILY = {"low strings": "strings", "organ": "chords", "brass": "chords", "unknown": "chords"}


def _role_color(role):
    """A new channel's color: the profile's palette, else the shipped (dark) palette of data/templates/_defaults."""
    N = _mod("flh_newsong")
    try:
        rec = {"palette": (N.defaults() or {}).get("palette") or {}}
    except Exception:
        rec = {"palette": {}}
    try:
        prof = _store().profile_get() or {}
    except Exception:
        prof = {}
    pal = prof.get("palette") if isinstance(prof.get("palette"), dict) else {}
    return N._palette_color(rec, role, LAYER_COLOR_FAMILY.get(role), {"palette": pal})


def _clone_ops(v, plan_new, names=None):
    """The batch ops that make the planned channels: the clones (flp_add_channel), their names and dark role colors,
    then each on its OWN free mixer insert named after it, routed to the Music bus (flp_compose_layer_inserts) - a
    clone would otherwise play through the insert of the channel it was copied from."""
    if not plan_new:
        return []
    ops = [{"tool": "flp_add_channel", "clone_from": p["clone_from"]} for p in plan_new]
    sty = []
    for p in plan_new:
        nm = (names or {}).get(p["channel"]) or p["name"]
        p["color"] = _role_color(p["role"])
        sty.append({"channel": p["channel"], "name": nm, "color": p["color"]})
    ops.append({"tool": "flp_style_channels", "channels": sty})
    ops.append({"tool": "flp_compose_layer_inserts",
                "channels": [{"channel": s["channel"], "name": s["name"], "color": s["color"]} for s in sty]})
    return ops


def _inserts_made(er, plan_new, res):
    """Read what flp_compose_layer_inserts did out of the batch results into new_channels / the plan (journal)."""
    got = {}
    for x in (er or {}).get("results") or []:
        if isinstance(x, dict) and isinstance(x.get("inserts"), list) and x.get("tool", "flp_compose_layer_inserts") \
                == "flp_compose_layer_inserts":
            for it in x["inserts"]:
                if isinstance(it, dict) and it.get("channel") is not None:
                    got[int(it["channel"])] = it
    for p in plan_new:
        it = got.get(int(p["channel"]))
        if it and it.get("insert"):
            p["insert"] = int(it["insert"])
    for nc in res.get("new_channels") or []:
        it = got.get(int(nc["channel"]))
        if it:
            nc["mixer_insert"] = it.get("insert") or 0
            if it.get("note"):
                nc["mixer_note"] = it["note"]
    return got


def flp_compose_layer_inserts(project, output, channels, bus="Music"):
    """Low-level writer the composer batches after cloning channels: each channel [{channel, name, color}] gets its
    OWN free mixer insert (one no channel plays into and that isn't a named bus / send), named and colored after it
    and routed to the `bus` track ('Music Bus' / 'Music', found by name) instead of the Master. A channel already
    alone on a free, unnamed insert keeps it. No free insert: it stays where it was (said)."""
    H["writable"](output)
    N = _mod("flh_newsong")
    d = H["_load"](project)
    ev = H["_events"](d)
    names = H["_mixer_names"](d, ev)
    bl = str(bus or "").strip().lower()
    bus_t = next((t for t, nm in sorted(names.items()) if t > 0 and bl and
                  str(nm).strip().lower() in (bl + " bus", bl)), None) if bl else None
    out, mixer = [], []
    for c in channels or []:
        ch = int(c["channel"])
        ev = H["_events"](d)
        types = N._chan_types(d, ev)
        if ch not in types:
            out.append({"channel": ch, "insert": None, "note": f"no channel {ch}"})
            continue
        cur = N._chan_props(d, ev, ch)["insert"]
        sharing = [o for o in types if o != ch and cur and N._chan_props(d, ev, o)["insert"] == cur]
        if cur and not sharing and not str(names.get(cur) or "").strip():
            ins = cur                                     # already alone on an unnamed insert
        else:
            ins = N._free_insert(d, ev)
        if not ins:
            out.append({"channel": ch, "insert": cur or 0,
                        "note": "no free mixer insert: it shares " + (f"insert {cur}" if cur else "the Master")})
            continue
        if ins != cur:
            d = N._chan_edit(d, ch, {N.EV_INSERT: struct.pack("<h", int(ins))})
        spec = {"track": ins, "name": str(c.get("name") or f"Channel {ch}")[:60]}
        if c.get("color"):
            spec["color"] = c["color"]
        if bus_t:
            spec.update(route_to=[bus_t], unroute=[0])
        mixer.append(spec)
        out.append({"channel": ch, "insert": ins, "was": cur, **({"bus": bus_t} if bus_t else {})})
    H["_save"](d, output)
    if mixer:
        try:
            H["flp_mixer"](output, output, mixer)
        except Exception as e:                            # routing refused (an insert without a routing event):
            try:                                          # name + color at least
                H["flp_mixer"](output, output, [{k: v for k, v in s.items() if k not in ("route_to", "unroute")}
                                                 for s in mixer])
                for o in out:
                    o.pop("bus", None)
                    o["note"] = f"named, not routed to the {bus} bus ({str(e)[:60]})"
            except Exception as e2:
                for o in out:
                    o["note"] = f"insert set, not named ({str(e2)[:60]})"
    return {"output": output, "tool": "flp_compose_layer_inserts", "inserts": out}


def flp_compose_drop(project, output=None, patterns=None, unplace=False):
    """Low-level writer flp_remove_fx batches: remove the named patterns (with every note in them) that no
    arrangement places any more; a pattern still placed somewhere is kept and said - unless unplace=True: then its
    clips go first (every arrangement). patterns: names, or ids (integers)."""
    if output is None:                                    # in place: the open FL project saved/reloaded around it
        return H["_with_open_project"](project, project, lambda: flp_compose_drop(project, project, patterns, unplace))
    H["writable"](output)
    d = H["_load"](project)
    ev = H["_events"](d)
    pats = H["_patterns"](d, ev)
    low = {str(n).strip().lower() for n in patterns or [] if not isinstance(n, int)}
    ids = {int(n) for n in patterns or [] if isinstance(n, int) and not isinstance(n, bool)}
    hit = {pid for pid, x in pats.items() if pid in ids or str(x.get("name") or "").strip().lower() in low}
    unplaced = 0
    if unplace and hit:                                   # their clips go first, in every arrangement
        base = H["PATTERN_BASE"]
        for a in H["_arrangements"](d, ev):
            k, _, recs = H["_playlist_at"](d, ev, a["index"])
            keep = [r for r in recs if struct.unpack("<H", bytes(r[6:8]))[0] - base not in hit]
            if k is not None and len(keep) != len(recs):
                unplaced += len(recs) - len(keep)
                d = H["_splice"](d, ev, [(k, k + 1, H["_data_event"](H["EV_PLAYLIST"], b"".join(bytes(r) for r in keep)))])
                ev = H["_events"](d)
    placed = set()
    for a in H["_arrangements"](d, ev):
        for r in H["_playlist_at"](d, ev, a["index"])[2]:
            placed.add(struct.unpack("<H", bytes(r[6:8]))[0] - H["PATTERN_BASE"])
    gone, kept = [], []
    for pid, x in sorted(pats.items()):
        nm = str(x.get("name") or "").strip()
        if pid not in hit:
            continue
        if pid in placed:
            kept.append({"pattern": nm, "why": "still placed in an arrangement"})
        else:
            gone.append(pid)
    edits = [(a, b, b"") for kind, pid, a, b in H["_pattern_groups"](d, ev) if pid in gone]
    if edits:
        H["_save"](H["_splice"](d, ev, edits), output)
    elif unplaced or not _same_path(project, output):
        H["_save"](d, output)
    return {"output": output, "removed": [pats[p]["name"] or p for p in gone], **({"kept": kept} if kept else {}),
            **({"clips_removed": unplaced} if unplaced else {})}


REMOVE_KINDS = FX_AUDIO + ORGANIC_MOVES + ("snare_build", "delay_throw", "filter")
REMOVE_GROUPS = {"audio": FX_AUDIO, "beds": FX_BEDS, "organic": ORGANIC_MOVES,
                 "synthetic": FX_AUDIO + ("snare_build", "delay_throw", "filter"), "all": REMOVE_KINDS}
REMOVE_ALIAS = {"filters": "filter", "throws": "delay_throw", "delay_throws": "delay_throw",
                "snare_builds": "snare_build", "bed": "beds", "fx": "audio"}
_SNARE_RX = re.compile(r"^snare build [\d.]+( \d+/\d+)?$", re.I)
_FILTER_RX = re.compile(r"^filter (intro|outro|reset|build cutoff|build hp|build lp) @[\d.]+$", re.I)
_THROW_RX = re.compile(r"^delay throws @[\d.]+$", re.I)
_ORG_RX = re.compile(r"^(%s) \d+$" % "|".join(re.escape(x) for x in ORG_NAMES.values()), re.I)


def _remove_want(kinds):
    if kinds in (None, "", "*") or kinds == ["all"]:
        return set(REMOVE_KINDS)
    if isinstance(kinds, str):
        kinds = [k for k in re.split(r"[,\s]+", kinds) if k]
    out = set()
    for k in kinds or []:
        kk = str(k).strip().lower().replace(" ", "_").replace("-", "_")
        kk = REMOVE_ALIAS.get(kk, kk)
        if kk in REMOVE_GROUPS:
            out |= set(REMOVE_GROUPS[kk])
        elif kk in REMOVE_KINDS:
            out.add(kk)
        else:
            raise ValueError(f"kinds: {', '.join(REMOVE_KINDS)} or a group ({', '.join(REMOVE_GROUPS)}) - got '{k}'")
    return out


def _remove_fx_plan(v, want, project):
    """What fl-headless made for the arrangement's ear candy and the kinds in `want` cover - found by the song
    journal (compose.fx_channels / organic / filter / delay_throw / fx_inserts / arrangement.generated) and the
    names it gives (a sampler named for its kind playing a WAV from an _fx folder). Never a channel, pattern or
    plugin the user made. -> plan dict."""
    N = _mod("flh_newsong")
    jr = v.get("journal") or {}
    comp = jr.get("compose") if isinstance(jr.get("compose"), dict) else {}
    arr = comp.get("arrangement") if isinstance(comp.get("arrangement"), dict) else {}
    generated = {str(n).strip().lower() for n in arr.get("generated") or []}
    chans, d, ev = v["channels"], v["d"], v["ev"]
    cname = lambda c: str((chans.get(c) or {}).get("name") or "").strip()
    fxn = {k: {FX_NAMES[k].lower(), f"{FX_NAMES[k]} fx".lower()} for k in FX_AUDIO}
    audio = {}                                            # channel -> kind
    jch = jr.get("channels") if isinstance(jr.get("channels"), dict) else {}
    cand = [(c, k) for k, c in ((comp.get("fx_channels") or {}).items() if isinstance(comp.get("fx_channels"), dict)
                                else [])]
    cand += [(c, x.get("fx_kind")) for c, x in jch.items() if isinstance(x, dict) and x.get("role") == "fx"]
    for c, x in chans.items():
        nm = cname(c).lower()
        k = next((k_ for k_, ns_ in fxn.items() if nm in ns_), None)
        if k and x.get("type") == 0:
            try:
                p_ = str(N._chan_sample_path(d, ev, c) or "").replace("\\", "/")
            except Exception:
                p_ = ""
            if p_ and os.path.basename(os.path.dirname(p_)).lower() == "_fx":
                cand.append((c, k))
    for c, k in cand:
        try:
            c = int(c)
        except (TypeError, ValueError):
            continue
        if k in want and k in FX_AUDIO and (chans.get(c) or {}).get("type") == 0 and cname(c).lower() in fxn[k]:
            audio[c] = k                                  # (still ours: a sampler with the name we gave it)
    pats = {str(x.get("name") or "").strip().lower(): (pid, x) for pid, x in v["patterns"].items()}
    drop, keep_ch, notes = [], set(), []
    for c, k in sorted(audio.items()):
        if cname(c).lower() in pats:
            drop.append(pats[cname(c).lower()][1]["name"])
    if "snare_build" in want:
        drop += [x[1]["name"] for nm, x in pats.items() if _SNARE_RX.match(nm) and nm in generated]
    org_j = comp.get("organic") if isinstance(comp.get("organic"), dict) else {}
    org_names = {str(n).strip().lower() for n in org_j.get("patterns") or []} | generated
    wl = {ORG_NAMES[k].lower() for k in want if k in ORG_NAMES}
    drop += [x[1]["name"] for nm, x in pats.items() if _ORG_RX.match(nm) and nm in org_names
             and nm.rsplit(" ", 1)[0] in wl]
    drop = list(dict.fromkeys(drop))
    low_drop = {n.lower() for n in drop}
    for c in list(audio):                                 # notes of the user's own on an FX channel: keep it
        other = [x["name"] for x in v["patterns"].values() if str(x.get("name") or "").strip().lower() not in low_drop
                 and any(int(r[2]) == c for r in x.get("notes") or [])]
        if other:
            keep_ch.add(c)
            notes.append(f"{cname(c)} kept: it also plays in your pattern '{other[0]}'")
            audio.pop(c)
    autos = []                                            # automation clips fl-headless named
    org_auto = {str(n).strip().lower() for n in org_j.get("automation") or []}
    for c, x in chans.items():
        if x.get("type") != 5:
            continue
        nm = cname(c).lower()
        if ("delay_throw" in want and _THROW_RX.match(nm)) or ("filter" in want and _FILTER_RX.match(nm)) or \
                (want & {"string_swell", "choir_breath"} and nm in org_auto):
            autos.append(c)
    fx_now = N._effects(d) or {}
    norm = lambda s_: re.sub(r"[^a-z0-9]", "", str(s_ or "").lower())
    plugins = []
    for what, plug in (("delay_throw", DELAY_PLUGIN), ("filter", FILTER_PLUGIN)):
        rec = comp.get(what) if isinstance(comp.get(what), dict) else None
        if what in want and rec and rec.get("track") is not None and rec.get("slot") is not None:
            t_, s_ = int(rec["track"]), int(rec["slot"])
            if norm((fx_now.get((t_, s_)) or {}).get("plugin")) == norm(plug):
                plugins.append({"track": t_, "slot": s_, "plugin": plug, "kind": what})
    inserts = []
    fxi = comp.get("fx_inserts") if isinstance(comp.get("fx_inserts"), dict) else {}
    if audio and fxi:
        try:
            names_ = H["_mixer_names"](d, ev)
        except Exception:
            names_ = {}
        routes = {}
        for c, x in chans.items():
            if x.get("type") in (0, 2) and c not in audio:
                try:
                    routes.setdefault(N._chan_props(d, ev, c)["insert"], []).append(c)
                except Exception:
                    pass
        for nm, t_ in fxi.items():
            t_ = int(t_)
            if str(names_.get(t_) or "").strip().lower() == str(nm).lower() and not routes.get(t_):
                inserts.append({"track": t_, "name": nm})
            elif routes.get(t_):
                notes.append(f"insert '{nm}' kept: {cname(routes[t_][0]) or 'another channel'} still plays into it")
    files = []
    fxd = os.path.join(os.path.dirname(os.path.abspath(project)), "_fx")
    for c in audio:
        try:
            p_ = N._chan_sample_path(d, ev, c)
            p_ = H["_expand_sample"](p_, project) if p_ and H.get("_expand_sample") else p_
        except Exception:
            p_ = None
        p_ = p_ or (jch.get(str(c)) or {}).get("sample") if isinstance(jch.get(str(c)), dict) else p_
        if p_ and os.path.isfile(p_) and os.path.basename(os.path.dirname(os.path.abspath(p_))).lower() == "_fx":
            files.append(os.path.abspath(p_))
    return {"audio": audio, "patterns": drop, "automation": sorted(autos), "plugins": plugins, "inserts": inserts,
            "files": sorted(set(files)), "fx_dir": fxd, "notes": notes, "kept_channels": sorted(keep_ch)}


_FX_WAV_RX = re.compile(r"^(%s) [\d.]+ beats [\d.]+ bpm\.wav$" % "|".join(re.escape(x) for x in FX_NAMES.values()),
                        re.I)


def _fx_wavs(project, plan_files):
    """(free, used): the _fx WAVs fl-headless made for this song's folder that no project there names any more once
    the removal is done (the removed channels' samples + earlier leftovers), and the ones another project uses."""
    folder = os.path.dirname(os.path.abspath(project))
    fxd = os.path.join(folder, "_fx")
    try:
        left = [os.path.join(fxd, f) for f in os.listdir(fxd) if _FX_WAV_RX.match(f)]
    except OSError:
        left = []
    me = os.path.normcase(os.path.abspath(project))
    blobs, mine = [], b""
    try:
        flps = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".flp")]
    except OSError:
        flps = []
    for f in flps:
        try:
            with open(f, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if os.path.normcase(os.path.abspath(f)) == me:
            mine = data
        else:
            blobs.append(data)
    named = lambda f, bl: any(k in x for x in bl for k in (os.path.basename(f).encode("utf-16-le"),
                                                           os.path.basename(f).encode("utf-8")))
    plan = {os.path.normcase(os.path.abspath(f)) for f in plan_files}
    free, used = [], []
    for f in sorted(set(os.path.abspath(x) for x in list(plan_files) + left)):
        if named(f, blobs):
            used.append(f)
        elif os.path.normcase(f) in plan or not named(f, [mine]):
            free.append(f)
    return free, used


def flp_remove_fx(project, kinds=None, dry_run=False, recycle_files=False, undo=False, remember=True):
    """Take the arrangement's ear candy out again in ONE reversible batch: the generated FX sampler channels (with
    their patterns and clips), the snare builds, the organic moves' patterns, the delay-throw / filter / swell
    automation clips and the plugins fl-headless loaded for them, the FX / Texture inserts it named (reset when
    nothing else plays into them). Only what fl-headless made (the song journal, the names it uses, the _fx folder).
    kinds: None = all, or kinds / groups (audio, beds, organic, synthetic, snare_build, delay_throw, filter, a move or
    FX kind). recycle_files: the _fx WAVs no project in the folder names any more go to the Recycle Bin. undo: put
    the project and its journal back as they were before the last removal."""
    T0 = time.time()
    steps = {}
    S = _store()
    if undo:
        j = S.journal_get(project)
        fr = j.get("fx_removed") if isinstance(j.get("fx_removed"), dict) else None
        if not fr or not fr.get("backup") or not os.path.isfile(fr["backup"]):
            raise ValueError("nothing to undo: no flp_remove_fx backup recorded in the song journal (flp_versions "
                             "lists every backup)")
        r = H["TOOLS"]["flp_restore"][0](path=project, backup=fr["backup"])
        before = fr.get("journal")
        if isinstance(before, dict):
            p = S.journal_path(project)
            H["writable"](p)
            with S._JLOCK:
                S._write_json(p, before)
        res = {"project": project, "restored_from": fr["backup"], "journal": "restored" if before else "kept",
               **({"files": f"{len(fr['recycled'])} WAV(s) went to the Recycle Bin with the removal: restore them "
                             "there, or the FX channels play nothing"} if fr.get("recycled") else {}),
               **({k: r[k] for k in ("warning",) if isinstance(r, dict) and k in r})}
        res["steps_s"] = {"total": round(time.time() - T0, 3)}
        return res
    want = _remove_want(kinds)
    _fresh(project)
    v = project_view(project)
    steps["read"] = round(time.time() - T0, 3)
    plan = _remove_fx_plan(v, want, project)
    names = lambda c: str((v["channels"].get(c) or {}).get("name") or f"channel {c}")
    gone_ch = sorted(set(plan["audio"]) | set(plan["automation"]))
    res = {"project": project, "kinds": "all" if kinds in (None, "", "*") else sorted(want),
           "channels": [{"channel": c, "name": names(c), "kind": plan["audio"].get(c, "automation")} for c in gone_ch],
           "patterns": plan["patterns"], "plugins": [f"{p_['plugin']} (insert {p_['track']} slot {p_['slot'] + 1})"
                                                     for p_ in plan["plugins"]],
           "inserts": [f"{x['name']} (insert {x['track']})" for x in plan["inserts"]]}
    if plan["notes"]:
        res["notes"] = list(plan["notes"])
    free, used = _fx_wavs(project, plan["files"])
    mb = lambda fs: round(sum(os.path.getsize(f) for f in fs if os.path.isfile(f)) / 1e6, 2)
    if not (gone_ch or plan["patterns"] or plan["plugins"] or plan["inserts"]):
        res["removed"] = False
        res["note"] = "no ear candy fl-headless made was found in this song (the journal lists none, no FX channels)"
        if free and recycle_files and not dry_run:
            try:
                H["files_recycle"](free)
                res["files"] = {"recycled": len([f for f in free if not os.path.exists(f)]),
                                "where": "the Recycle Bin (restorable)"}
            except Exception as e:
                res["files"] = {"note": f"not recycled ({type(e).__name__}: {str(e)[:100]})"}
        elif free:
            res["files"] = {"count": len(free), "mb": mb(free), "folder": plan["fx_dir"],
                            "offer": "recycle_files=true moves these unused _fx WAVs to the Recycle Bin"}
        return res
    if dry_run:
        res["dry_run"] = True
        if free:
            res["files"] = {"count": len(free), "mb": mb(free),
                            "how": "recycle_files=true moves them to the Recycle Bin after the removal"}
        res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
        return res
    ops = []
    if plan["patterns"]:
        ops.append({"tool": "flp_compose_clear", "names": plan["patterns"]})
        ops.append({"tool": "flp_compose_drop", "patterns": plan["patterns"]})
    for p_ in plan["plugins"]:
        ops.append({"tool": "flp_remove_effect", "track": p_["track"], "slot": p_["slot"]})
    if gone_ch:
        if "flp_remove_channels" not in H["TOOLS"]:
            raise RuntimeError("flp_remove_channels (flh_cleanup) isn't loaded: the FX channels can't be removed")
        ops.append({"tool": "flp_remove_channels", "channels": gone_ch, "force": True})
    if plan["inserts"]:
        # PC-VERIFY: an insert whose name was set to '' shows FL's default 'Insert N' name again
        ops.append({"tool": "flp_mixer", "tracks": [{"track": x["track"], "name": ""} for x in plan["inserts"]]})
    before = json.loads(json.dumps(S.journal_get(project)))
    before.pop("fx_removed", None)
    try:
        newest = lambda: ([x["backup"] for x in H["flp_versions"](project)] or [None])[-1]
        nb = newest()
    except Exception:
        newest, nb = (lambda: None), None
    with open(_src(project), "rb") as fh:
        pre = fh.read()
    H["_CALL_BACKUPS"].discard(os.path.normcase(os.path.abspath(project)))   # the batch's first write backs up
    er = _carry(res, _edit(project, ops, steps))
    bk = newest()
    if not bk or bk == nb:                                # queued for a lazy reload (or no backup made): one now
        bk = H["_backup"](project, data=pre)
    res["removed"] = True
    for x in er.get("results") or []:
        if isinstance(x, dict) and x.get("tool") == "flp_compose_drop" and x.get("kept"):
            res.setdefault("notes", []).append(f"{len(x['kept'])} pattern(s) kept: still placed in another arrangement")
    recycled, rec_note = [], None
    if free and recycle_files:
        try:
            H["files_recycle"](free)
            recycled = [f for f in free if not os.path.exists(f)]
        except Exception as e:
            rec_note = f"the WAVs weren't recycled ({type(e).__name__}: {str(e)[:100]})"
    if free and not recycle_files:
        res["files"] = {"count": len(free), "mb": mb(free), "folder": plan["fx_dir"],
                        "offer": "flp_remove_fx(project, recycle_files=true) moves these unused _fx WAVs to the "
                                 "Recycle Bin (run it again: nothing else is removed twice)"}
    elif recycled:
        res["files"] = {"recycled": len(recycled), "where": "the Recycle Bin (restorable)"}
    if used:
        res.setdefault("notes", []).append(f"{len(used)} _fx WAV(s) kept: another project in the folder uses them")
    if rec_note:
        res.setdefault("notes", []).append(rec_note)
    comp = before.get("compose") if isinstance(before.get("compose"), dict) else {}
    jset = {"compose": {}}
    if plan["audio"]:
        jset["compose"]["fx_channels"] = {k: None for k in set(plan["audio"].values())}
    if "delay_throw" in want:
        jset["compose"]["delay_throw"] = None
    if "filter" in want:
        jset["compose"]["filter"] = None
    if plan["inserts"]:
        jset["compose"]["fx_inserts"] = {x["name"]: None for x in plan["inserts"]}
    low = {n.lower() for n in plan["patterns"]}
    org_j = comp.get("organic") if isinstance(comp.get("organic"), dict) else {}
    if org_j:
        auto_names = {names(c).lower() for c in plan["automation"]}
        jset["compose"]["organic"] = {
            "patterns": [n for n in org_j.get("patterns") or [] if n.lower() not in low],
            "automation": [n for n in org_j.get("automation") or [] if n.lower() not in auto_names]}
    arr = comp.get("arrangement") if isinstance(comp.get("arrangement"), dict) else {}
    if arr.get("generated"):
        jset["compose"]["arrangement"] = {"generated": [n for n in arr["generated"] if str(n).lower() not in low]}
    if remember and kinds in (None, "", "*"):
        jset["compose"]["ear_candy"] = "off"              # the next arrangement adds none unless asked
        res["remembered"] = ("the next arrangement adds no ear candy (ear_candy='organic' / an amount brings it "
                             "back)")
    try:
        S.journal_update(project, set={"fx_removed": None})
        S.journal_update(project, set=dict(jset, fx_removed={
            "backup": bk, "when": time.strftime("%Y-%m-%d %H:%M"), "journal": before,
            **({"recycled": [os.path.basename(f) for f in recycled]} if recycled else {})}))
    except Exception as e:
        res["journal_warning"] = f"the song journal wasn't updated ({type(e).__name__}: {str(e)[:80]})"
    res["undo"] = "flp_remove_fx(project, undo=true) puts the song and its journal back (or flp_restore: " \
                  f"{os.path.basename(bk)})"
    res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
    return res


SECTION_MELODY = {"hook": "full", "chorus": "full", "drop": "full", "verse": "call", "pre": "call", "prehook": "call",
                  "pre-hook": "call", "pre-chorus": "call", "build": "call", "intro": "sparse", "outro": "sparse",
                  "breakdown": "sparse", "bridge": "call"}


def _mel_kind(name, spec):
    """How a section plays the hook melody: full | call (each phrase's first bar: the motif, room after it) | sparse
    (its on-beat notes: a teaser / a fade) | none. Data: sections.<name>.melody; else by the section's kind."""
    k = str(spec.get("melody") or "").lower()
    if k in ("full", "call", "sparse", "none"):
        return k
    low = str(spec.get("role") or name).lower().rstrip("0123456789 ")
    return SECTION_MELODY.get(low, "full")


def melody_variant(notes, kind):
    """A section's version of the hook (notes in steps of the current meter): call keeps the first bar of every
    2-bar phrase, sparse keeps its notes on the beat; lengths stay as written."""
    mt = _meter()
    S = mt["spb"]
    P = 2 * S
    first = [n for n in notes if ((n["s"] + 0.3) % P) < S]   # (played notes sit a little off the grid)
    ends = {}                                             # each phrase's landing note (its last, in the answer bar)
    for n in notes:
        ph = int((n["s"] + 0.3) // P)
        if ((n["s"] + 0.3) % P) >= S and (ph not in ends or n["s"] > ends[ph]["s"]):
            ends[ph] = n
    if kind == "sparse":
        beats = [b_ + o for b_ in range(0, P, S) for o in mt["beats"]]
        out = [n for n in first if min(abs((n["s"] % P) - x) for x in beats + [P]) <= 0.35]
        if len(out) < 2:
            out = first
    elif kind == "call":
        out = first
    else:
        return list(notes)
    return sorted(out + list(ends.values()), key=lambda n: n["s"])


def bridge_harmony(key, g, bars, hook_chords=None, seed=1):
    """A contrasting progression for a bridge: one of the genre's progressions that doesn't start on the hook's
    first chord (and shares the fewest chords with it), else the hook's progression from its middle. -> (chords,
    about)."""
    gd = genre_data(g) if isinstance(g, str) else g
    fam = key_info(key)[3]
    hook = [str(c[2]) for c in (hook_chords or [])]
    hroots = []
    for nm_ in hook:
        c_ = _chord_or_none(nm_, key)
        if c_ is not None:
            hroots.append(c_["root"])
    cands = []
    for pr in (gd.get("harmony") or {}).get("progressions") or []:
        if pr.get("mode", "any") not in (fam, "any"):
            continue
        try:
            roots = [chord_from_token(t, key)["root"] for t in pr["rn"]]
        except ValueError:
            continue
        if hroots and roots[0] == hroots[0]:
            continue
        shared = len(set(roots) & set(hroots))
        cands.append((shared, -float(pr.get("w", 1)), " ".join(pr["rn"]), pr))
    rnd = _rng(seed, "bridge", gd["name"], key)
    if cands:
        cands.sort(key=lambda x: x[:3])
        best = [c for c in cands if c[0] == cands[0][0]]
        pr = best[rnd.randrange(len(best))][3]
        prog = [f"{t}:{pr.get('bars', 1)}" for t in pr["rn"]]
        about = f"bridge on {' '.join(pr['rn'])} (the hook starts on {hook[0] if hook else '?'})"
    elif len(hook) >= 2:
        h = len(hook) // 2
        rows = list(hook_chords[h:]) + list(hook_chords[:h])
        names = [str(c[2]) for c in rows]
        prog = [f"{c[2]}:{max(0.5, float(c[1]) / _spb()):g}" for c in rows]
        about = f"bridge on the hook's chords from the middle ({' '.join(names[:4])})"
    else:
        raise ValueError("the genre has one progression and the hook one chord")
    chs, _ = harmony(key, gd, bars, prog, seed)
    return chs, about


@_reads_fresh
@_meter_scope
def snare_build_rows(ch, key, beats, vel_lo=40, vel_hi=118):
    """An accelerating snare roll over `beats` quarter beats into a downbeat: 8ths for the first half, 16ths for the
    next quarter, 32nds for the last, the velocity rising all the way. -> rows [ch, beat, length, key, vel, 0]."""
    rows, beats = [], float(beats)
    for a, b, step in ((0.0, 0.5, 0.5), (0.5, 0.75, 0.25), (0.75, 1.0, 0.125)):
        t = math.ceil(a * beats / step - 1e-9) * step
        while t < b * beats - 1e-9:
            rows.append([int(ch), round(t, 5), round(step * 0.5, 5), int(key),
                         _clampv(vel_lo + (vel_hi - vel_lo) * t / beats), 0])
            t += step
    return rows


def _lead_last_note(v, gen, entries, ch, idx, bnd, qb):
    """(bar, length in bars) of the lead channel's last note in the section before boundary `bnd` (from the clips
    laid there: a generated variant's rows or the composed pattern's notes), or None."""
    P = float(v["P"])
    by_name = {(x.get("name") or "").strip().lower(): x for x in v["patterns"].values()}
    best = None
    for e in entries:
        if e["sec"] != idx - 1 or e["role"] != "melody" or e["bar"] + e["len"] < bnd - 2.0 - 1e-6:
            continue
        if e["pattern"] in gen:
            notes = [(float(r[1]), float(r[2])) for r in gen[e["pattern"]]["rows"] if int(r[0]) == int(ch)]
        else:
            recs = (by_name.get(str(e["pattern"]).strip().lower()) or {}).get("notes") or []
            notes = [(r[0] / P, r[3] / P) for r in recs if int(r[2]) == int(ch)]
        for st, ln in notes:
            if st / qb < float(e["len"]) - 1e-6:
                nb = float(e["bar"]) + st / qb
                if nb < bnd - 1e-6 and (best is None or nb > best[0]):
                    best = (nb, min(ln / qb, float(e["bar"]) + float(e["len"]) - nb))
    return best


def _throw_plan(v, g, ec, entries, gen, cands, s0, reg, sec_mt, why=None):
    """Delay throws: the lead's last note of a phrase before a section change echoes across it. Written as
    automation of a Fruity Delay 3's wet on the lead's own mixer insert (ours from an earlier arrangement, else one
    loaded into a free slot from FL's plugin database - a delay the user put there is left alone), held at 0 between
    throws; a lead without an insert of its own (Master) or a missing delay / map gets the throw planned (said, not
    written: FL's send knobs can't be automated reliably in the .flp file). The amount sets how many (throws_per_song, the climax and the big
    changes first) and how wet. -> (ops, placed, planned, notes, {track, slot} | None)."""
    N = _mod("flh_newsong")
    cfg, data = ec["cfg"], ec["data"]
    n_max = int(cfg.get("throws_per_song") or 0)
    ch = (reg.get("channels") or {}).get("melody")
    if ch is None:
        ch = (reg.get("channels") or {}).get("lead")
    if n_max <= 0 or ch is None:
        return [], [], [], ([] if n_max <= 0 else ["delay throws skipped: no lead / melody channel is known"]), None
    pri = lambda c: (0 if "climax" in c["tags"] else 1 if "big" in c["tags"] else
                     2 if c["tags"] & {"hook", "drop"} else 3, c["bar"])
    picks = []
    for c in sorted(cands, key=pri):
        m_ = sec_mt[c["idx"] - 1]
        last = _lead_last_note(v, gen, entries, ch, c["idx"], c["bar"], 4.0 if m_["legacy"] else float(m_["qbar"]))
        if last and c["bar"] - last[0] <= 2.0 + 1e-6:     # a phrase end: the last note within the last two bars
            picks.append(dict(c, note=last))
        if len(picks) >= n_max:
            break
    if not picks:
        return [], [], [], [], None
    picks.sort(key=lambda c: c["bar"])
    wet_pct = float(data.get("throw_wet_pct") or 35.0) * 10 ** (float(cfg.get("level_offset_db") or 0.0) / 20.0)
    wet_pct = round(max(5.0, min(80.0, wet_pct)), 1)
    d, ev = v["d"], v["ev"]
    ins = None
    try:
        jch = ((v.get("journal") or {}).get("channels") or {}).get(str(ch)) or {}
        ins = jch.get("mixer") if jch.get("mixer") is not None else N._chan_props(d, ev, int(ch))["insert"]
    except Exception:
        ins = None
    ops, why_plan, slot = [], None, None
    norm = lambda s_: re.sub(r"[^a-z0-9]", "", str(s_ or "").lower())
    try:
        ME = _mod("flh_mixeng")
        wet = ((ME.plugin_map(DELAY_PLUGIN) or {}).get("controls") or {}).get("wet_pct")
        to_norm = ME.to_norm
    except Exception:
        wet, to_norm = None, None
    if not ins:
        why_plan = "the lead plays into the Master (no insert of its own for a delay)"
    elif not wet or to_norm is None:
        why_plan = f"no {DELAY_PLUGIN} map with a wet control (data/plugin_maps)"
    else:
        ins = int(ins)
        fx_now = N._effects(d) or {}
        old = reg.get("delay_throw") if isinstance(reg.get("delay_throw"), dict) else {}
        on_ins = sorted(s_ for (t_, s_), x in fx_now.items() if t_ == ins and norm(x.get("plugin")) == norm(DELAY_PLUGIN))
        if old and int(old.get("track", -1)) == ins and int(old.get("slot", -1)) in on_ins:
            slot = int(old["slot"])                       # ours from an earlier arrangement
        else:                                             # (a delay of the user's own keeps its settings: ours is
                                                          # a second one, silent between throws)
            try:
                e_ = N._find_plugin(DELAY_PLUGIN)
            except Exception:
                e_ = None
            fst = (e_ or {}).get("default")
            try:
                slots = H["_mixer_slots"](d, ev)
            except Exception:
                slots = {}
            free = sorted(s_ for (t_, s_), (first, _) in slots.items() if t_ == ins and first is None)
            if not fst or not N._fst_ok(fst):
                why_plan = f"{DELAY_PLUGIN} isn't in FL's plugin database on this PC"
            elif not free:
                why_plan = f"no free effect slot on the lead's insert {ins}"
            else:
                slot = free[0]
                ops.append({"tool": "flp_load_plugin", "preset": fst, "track": ins, "slot": slot})
    planned, placed = [], []
    for c in picks:
        nb = round(c["note"][0], 4)
        pur = f"the lead's last note before {c['into']} at bar {c['bar']:g}"
        if slot is None:
            planned.append({"bar": nb, "kind": "delay_throw", "into": c["into"], "purpose": pur,
                            "how": f"send this note to a delay (~{wet_pct:g}% wet, the echo rings into bar "
                                   f"{c['bar']:g}), then back"})
        else:
            placed.append({"bar": nb, "kind": "delay_throw", "into": c["into"], "purpose": pur, "why": why,
                           "on": f"insert {ins} slot {slot + 1} ({DELAY_PLUGIN} wet {wet_pct:g}%)",
                           "at": f"from the note on bar {nb:g} until 1 bar into {c['into']}"})
    if slot is None:
        return [], [], planned, [f"delay throws planned, not written: {why_plan}"], None
    on = round(to_norm(wet, wet_pct), 4)
    pts, eps = [], 1.0 / 64
    if picks[0]["note"][0] - eps > s0 + 1e-6:             # silent from the start (FL holds the last value)
        pts.append((s0, 0.0))
    for c in picks:
        nb, b_ = c["note"][0], float(c["bar"])
        pts += [(nb - eps, 0.0), (nb, on), (b_ + 0.5, on), (b_ + 1.0, 0.0)]
    # PC-VERIFY: Fruity Delay 3's 'Output wet' (data/plugin_maps/fruity_delay_3.json, index 23, low confidence)
    # follows the 'Delay throws' clip on the lead's insert: 0 = no echo between throws, the lead's last note before
    # each change echoes into the next section and fades within a bar; a Delay 3 loaded here keeps FL's default
    # time / feedback (check it is tempo-synced and not too long)
    ops.append({"tool": "flp_add_automation", "track": ins, "slot": slot, "plugin_param": int(wet["index"]),
                "points": [{"bar": round(b_, 4), "value": x_} for b_, x_ in pts],
                "name": f"Delay throws @{s0:g}", "replace": True})
    return ops, placed, planned, [], {"track": ins, "slot": slot, "channel": int(ch)}


# ----------------------------------------------------------------------------- organic ear candy (real instruments)
ORG_PERC_ROLES = set(DRUM_PARTS) | {"kit", "fx", "texture"}


def _has_word(text, words):
    t = " " + re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()) + " "
    return any((" " + re.sub(r"[^a-z0-9 ]", " ", w.lower()) + " ") in t or (len(w) > 4 and w.lower() in t)
               for w in words)


def _org_channels(v, od, dm=None):
    """{what: [channels]} - the song's own instruments the organic moves can play on (what: strings, low_strings,
    timpani, cymbal, choir, piano): the song journal's channel role (data roles), the role detection (compose
    registry / names), else channel / plugin / sample names (data words); best first. The arrangement's generated
    FX samplers never count; a low-strings channel isn't also 'strings' (its register differs)."""
    words, roles = od.get("words") or {}, od.get("roles") or {}
    jch = (v.get("journal") or {}).get("channels")
    jch = jch if isinstance(jch, dict) else {}
    reg = v.get("registry") or {}
    ours = set()
    for c in ((reg.get("fx_channels") or {}).values() if isinstance(reg.get("fx_channels"), dict) else []):
        try:
            ours.add(int(c))
        except (TypeError, ValueError):
            pass
    for c, x in jch.items():
        if isinstance(x, dict) and str(x.get("role") or "").lower() == "fx" and str(c).isdigit():
            ours.add(int(c))
    hits = {}
    for ch, info in sorted(v["channels"].items()):
        if info.get("type") in (4, 5) or ch in ours or v["roles"].get(ch) == "fx":
            continue
        jx = jch.get(str(ch)) if isinstance(jch.get(str(ch)), dict) else {}
        jr = str(jx.get("role") or "").lower()
        txt = " ".join(str(x or "") for x in (info.get("name"), info.get("plugin"), info.get("sample"), jx.get("name"),
                                              jx.get("preset"), os.path.basename(str(jx.get("sound") or ""))))
        vr = str(v["roles"].get(ch) or "").lower()
        for what in set(words) | set(roles):
            rk = [str(r).lower() for r in roles.get(what) or []]
            score = 0 if jr and jr in rk else 1 if vr in rk else 2 if _has_word(txt, words.get(what) or []) else None
            if score is not None:
                hits.setdefault(what, []).append((score, ch))
    if dm and dm.get("crash"):                            # the kit's crash: a cymbal the song already has
        try:
            hits.setdefault("cymbal", []).append((1, int(dm["crash"][0])))
        except (TypeError, ValueError, IndexError):
            pass
    out = {w: list(dict.fromkeys(c for _, c in sorted(x))) for w, x in hits.items()}
    low = set(out.get("low_strings") or [])
    if "strings" in out:
        out["strings"] = [c for c in out["strings"] if c not in low]
    return out


def _org_heard(v, gen, entries, sec_mt):
    """Every note the laid-out clips play, in song bars: [(start, end, channel, sounding key, velocity)]."""
    P = float(v["P"])
    offs = v.get("offsets") or {}
    by_name = {(x.get("name") or "").strip().lower(): x for x in v["patterns"].values()}
    out = []
    for e in entries:
        m_ = sec_mt[min(max(int(e["sec"]), 0), len(sec_mt) - 1)]
        qb = 4.0 if m_["legacy"] else float(m_["qbar"] or 4.0)
        if e["pattern"] in gen:
            notes = [(float(r[1]), float(r[2]), int(r[0]), int(r[3]), int(r[4])) for r in gen[e["pattern"]]["rows"]]
        else:
            recs = (by_name.get(str(e["pattern"]).strip().lower()) or {}).get("notes") or []
            notes = [(r[0] / P, r[3] / P, int(r[2]), int(r[4]), int(r[11])) for r in recs]
        b, L = float(e["bar"]), float(e["len"])
        for st, ln, ch, k, vel in notes:
            sb = st / qb
            if sb < L - 1e-6:
                out.append((b + sb, b + min(L, sb + max(ln, 1e-3) / qb), ch, k - int(offs.get(ch, 0) or 0), vel))
    return out


def _org_harmony(notes, a, z, qb, need=2):
    """The chord the music holds just before bar z: walking back a beat at a time from z, the pitch classes every
    sounding beat shares (a silent beat - a 'stop' - joins in) while at least `need` are left.
    -> (start bar, [pcs, the most heard first], bass pc or None) or None."""
    step = 1.0 / qb
    notes = [n for n in notes if n[1] > a + 1e-6 and n[0] < z - 1e-6]
    if not notes:
        return None
    common, start, last = None, z, None
    k = 0
    while z - (k + 1) * step >= a - 1e-6:
        t1 = z - k * step
        t0 = t1 - step
        k += 1
        s = {n[3] % 12 for n in notes if n[0] < t1 - 1e-6 and n[1] > t0 + 1e-6}
        if not s:
            start = t0
            continue
        new = s if common is None else (common & s)
        if len(new) < need:
            break
        if common is None:
            last = (t0, t1)
        common, start = new, t0
    if not common:
        return None
    w = {}
    for n in notes:
        ov = min(n[1], z) - max(n[0], start)
        if ov > 0 and n[3] % 12 in common:
            w[n[3] % 12] = w.get(n[3] % 12, 0.0) + ov
    pcs = sorted(common, key=lambda p: (-w.get(p, 0.0), p))
    lows = [n[3] for n in notes if n[0] < last[1] - 1e-6 and n[1] > last[0] + 1e-6 and n[3] % 12 in common]
    return start, pcs, (min(lows) % 12 if lows else None)


def _org_voices(pcs, bass, nxt, lo, hi, n):
    """Up to n keys inside [lo, hi] for a held chord: the bass pitch class lowest, a tone the next downbeat keeps
    on top when there is one, at least 3 semitones apart, rising."""
    first = [bass] if bass in pcs else []
    rest = [p for p in pcs if p not in first]
    top = next((p for p in rest if p in nxt), None) if n > 1 else None
    seq = (first + [p for p in rest if p != top])[:max(1, n - (1 if top is not None else 0))]
    if top is not None:
        seq.append(top)
    keys, cur = [], int(lo) - 1
    for pc in seq[:n]:
        k_ = cur + 1 + ((pc - (cur + 1)) % 12)
        if keys and k_ - keys[-1] < 3:
            k_ += 12
        if k_ > hi:
            break
        keys.append(k_)
        cur = k_
    return keys


def _chan_volume(v, ch):
    """A channel's volume knob (0..1 of FL's 0-12800; FL's default 10000 = 0.78) from its block, or None."""
    try:
        k0, kend = _mod("flh_newsong")._chan_range(v["d"], v["ev"], int(ch))
        for e in v["ev"][k0 + 1:kend + 1]:
            if e["id"] == 219 and e["len"] >= 8:
                return struct.unpack("<i", H["_payload"](v["d"], e)[4:8])[0] / 12800.0
    except Exception:
        pass
    return None


def _volume_links(v):
    """{channel: [automation channel names]} - automation clips that move a channel's volume knob."""
    out = {}
    try:
        names = {c: str(x.get("name") or "") for c, x in v["channels"].items()}
        for e in v["ev"]:
            if e["id"] == H["EV_REMOTE_LINK"] and e["len"] >= 12:
                p = H["_payload"](v["d"], e)
                auto = struct.unpack("<H", p[2:4])[0]
                pid, dest = struct.unpack("<HH", p[8:12])
                if pid == H["CHAN_LINK"]["volume"] and dest < 0x0FFF:
                    out.setdefault(int(dest), []).append(names.get(auto, ""))
    except Exception:
        pass
    return out


def _org_hint(md):
    """Which owned instrument would play a move (flh_rise's fl_sound_catalog helper), or None."""
    try:
        R = _mod("flh_rise")
        q = md.get("catalog") or []
        return R._catalog_hint(q[0], q[1]) if R.H and len(q) >= 2 else None
    except Exception:
        return None


def _organic_plan(v, g, ec, table, sec_mt, s0, entries, gen, akey, dm, reg):
    """Organic ear candy: real-instrument transitions written as notes on channels the song already has (data
    ear_candy.json 'organic'). Per section change: the first place it has a job for gives the moves in priority
    order; a move goes on a channel of its instrument that plays nothing there (the piano pickup: a piano barely
    moving there), in the bars before the downbeat (ending on it; a timpani roll lands on it), its velocities under
    the piano's; a move whose instrument the song doesn't have is skipped (and the owned instrument that would play it
    is named). Adds the patterns to gen / entries. -> (placed, skipped, missing, ops (volume ramps), info)."""
    od = _org_data(ec["data"])
    cfg = ec["cfg"]
    moves = od.get("moves") or {}
    gal = (g.get("ear_candy") or {}).get("organic_moves") if isinstance(g.get("ear_candy"), dict) else None
    allowed = set(gal) if gal else set(moves)
    chans = _org_channels(v, od, dm)
    perc = {c for c, r in v["roles"].items() if r in ORG_PERC_ROLES} | set(chans.get("timpani") or []) | \
        set(chans.get("cymbal") or [])
    heard = _org_heard(v, gen, entries, sec_mt)
    harm_notes = [n for n in heard if n[2] not in perc]
    mine = []
    busy = lambda ch, a, z: [n for n in heard + mine if n[2] == ch and n[0] < z - 1e-6 and n[1] > a + 1e-6]
    # the piano pickup plays only on a piano / keys channel the song has (by role, name, plugin, sound or preset) -
    # never on whatever plays the chords (a synthwave Polysynth got 'Piano Pickup' patterns)
    pch = (chans.get("piano") or [None])[0]
    lv = od.get("levels") or ORG_FALLBACK["levels"]
    pvs = sorted(n[4] for n in heard if n[2] == pch) or sorted(int(r[11]) for r in v["notes_by_ch"].get(pch) or [])
    piano_vel = pvs[len(pvs) // 2] if pvs else int(lv.get("default_piano_vel", 80))
    cap = max(24, piano_vel - int(lv.get("below_piano_vel", 10)))
    vs = float(cfg.get("vel_scale") or 1.0)
    start_vel = int(lv.get("start_vel", 30))
    maxr = (od.get("rules") or {}).get("max_per_boundary") or {}
    tempo = float(v["tempo"] or 120.0)
    root = key_info(akey)[0] if akey else None
    R = _mod("flh_rise")
    names = lambda c: str((v["channels"].get(c) or {}).get("name") or f"channel {c}")
    pats_by_name = {(x.get("name") or "").strip().lower(): x for x in v["patterns"].values()}
    per_song, ordinal = {}, {}
    placed, skipped, missing, ramps = [], [], {}, {}

    def peak_of(md):
        return int(max(start_vel + 8, min(int(md.get("peak_vel", 72)), cap) * vs))

    def swell(mv, md, kinds, a_min, bnd, qb, nxt):
        cands = [(w, c) for w in kinds for c in chans.get(w) or []]
        if not cands:
            return {"skip": f"the song has no {md.get('needs') or kinds[0]} channel", "missing": True}
        span = min(float(md.get("beats", 8)) / qb, (bnd - a_min) / 2.0)
        if span * qb < float(md.get("min_beats", 2)) - 1e-6:
            return {"skip": f"the section before is too short for a {span * qb:g}-beat swell"}
        why = None
        for w, ch in cands:
            if busy(ch, bnd - span, bnd):
                why = why or f"{names(ch)} already plays there"
                continue
            n = int(md.get("voices", 2))
            h = _org_harmony(harm_notes, bnd - span, bnd, qb, 2 if n > 1 else 1) or \
                _org_harmony(harm_notes, bnd - span, bnd, qb, 1)
            if not h:
                return {"skip": "no harmony heard before the change"}
            a = h[0]
            if (bnd - a) * qb < float(md.get("min_beats", 2)) - 1e-6:
                return {"skip": "the harmony changes too fast before the change for a held swell"}
            reg_ = (md.get("register") or {}).get(w) or (md.get("register") or {}).get(kinds[0]) or [48, 67]
            keys = _org_voices(h[1], h[2], nxt, reg_[0], reg_[1], n)
            if not keys:
                return {"skip": "no chord tone fits the instrument's register"}
            span_q = (bnd - a) * qb
            pk, rows = peak_of(md), []
            for i, k_ in enumerate(keys):
                st = math.floor(span_q * i / (len(keys) + 1) * 2 + 1e-6) / 2.0
                vel = start_vel + (pk - start_vel) * (i / (len(keys) - 1) if len(keys) > 1 else 0.5)
                rows.append([ch, st, round(span_q - st, 4), int(k_), _clampv(vel), 0])
            return {"ch": ch, "a": a, "len": bnd - a, "rows": rows, "vel": f"{rows[0][4]}->{rows[-1][4]}",
                    "at": f"ends on bar {bnd:g}", "ramp": md.get("volume_ramp")}
        return {"skip": why}

    def roll(mv, md, a_min, bnd, qb, nxt, end_pcs):
        cands = chans.get("timpani" if mv == "timpani_roll" else "cymbal") or []
        if not cands:
            return {"skip": f"the song has no {md.get('needs') or mv} channel", "missing": True}
        span = min(float(md.get("beats", 4)) / qb, (bnd - a_min) / 2.0)
        if span * qb < 1.0 - 1e-6:
            return {"skip": "the section before is too short for a roll"}
        land = bool(md.get("land")) and mv == "timpani_roll"
        land_b = float(md.get("strike_beats", 1)) / qb if land else 0.0
        ch = next((c for c in cands if not busy(c, bnd - span, bnd + land_b)), None)
        if ch is None:
            return {"skip": f"{names(cands[0])} already plays there"}
        if mv == "timpani_roll":
            lo, hi = (md.get("register") or [40, 55])[:2]
            dom = (root + 7) % 12 if root is not None else None
            if root is None:
                pc = (end_pcs or [0])[0]
            elif nxt and root not in nxt and dom in nxt:
                pc = dom
            elif root in end_pcs or not end_pcs or dom not in end_pcs:
                pc = root
            else:
                pc = dom
            mid = (lo + hi) / 2.0
            pitch = min((k_ for k_ in range(int(lo), int(hi) + 1) if k_ % 12 == pc), key=lambda k_: abs(k_ - mid),
                        default=int(lo))
        else:
            if dm and dm.get("crash") and int(dm["crash"][0]) == ch:
                pitch = int(dm["crash"][1])
            else:
                ks = [int(r[4]) for r in v["notes_by_ch"].get(ch) or []]
                pitch = max(set(ks), key=ks.count) if ks else 60
        P_ = 96
        # PC-VERIFY: a timpani roll at strokes_per_s sounds like a roll (not single hits) on the user's timpani patch
        # and lands on the downbeat; a cymbal roll on a one-shot crash sampler isn't a machine-gun (else: real sus
        # cymbal patch or kinds=['cymbal_roll'] out)
        rc = {"strokes_per_s": md.get("strokes_per_s", 10), "from_vel": md.get("from_vel", 24),
              "strike_beats": md.get("strike_beats", 1)}
        pk = peak_of(md)
        rn = R.roll_notes(int(round(span * qb * P_)), P_, span * qb, tempo, rc, pk, pitch)
        if not land:
            rn = rn[:-1]
        rows = [[ch, round(t / P_, 5), round(max(1, ln) / P_, 5), int(p_), int(vl), 0] for t, ln, p_, vl in rn]
        return {"ch": ch, "a": bnd - span, "len": span + land_b, "rows": rows, "vel": f"{rows[0][4]}->{rows[-1][4]}",
                "at": f"lands on bar {bnd:g}" if land else f"ends on bar {bnd:g}", "pitch": pitch}

    def pickup(md, a_min, bnd, qb):
        if pch is None:
            return {"skip": "the song has no piano / keys channel", "missing": True}
        beats = float(md.get("beats", 1))
        span = beats / qb
        if bnd - span < a_min - 1e-6:
            return {"skip": "the section before is too short"}
        a = bnd - span
        here = busy(pch, a, bnd)
        onsets = {round(n[0], 3) for n in here if n[0] >= a - 1e-6}
        if len(onsets) > int(md.get("max_onsets", 1)):
            return {"skip": f"{names(pch)} already moves through that beat"}
        a_h = max(a_min, bnd - 1.0)                        # the chord of the last bar (a silent last beat: before it)
        h = _org_harmony(harm_notes, a_h, bnd, qb, 2) or _org_harmony(harm_notes, a_h, bnd, qb, 1)
        if not h:
            return {"skip": "no harmony heard before the change"}
        own = [n for n in heard if n[2] == pch]
        sec_own = [n for n in own if a_min - 1e-6 <= n[0] < bnd] or own
        if not sec_own:
            return {"skip": f"{names(pch)} plays nothing to take the register from"}
        lo_p, hi_p = min(n[3] for n in sec_own), max(n[3] for n in sec_own)
        held = {n[3] for n in here}
        n_ = int(md.get("notes", 4))
        while n_ > 2 and beats * 60.0 / tempo / n_ < float(md.get("min_note_s", 0.09)):
            n_ -= 1
        base = max(held) if held else sorted(n[3] for n in sec_own)[len(sec_own) // 2]
        top = min(max(hi_p, base) + 12, 100)               # up to an octave over what the piano plays there
        pcs = set(h[1])
        ks = []
        for want_n in range(n_, 2, -1):                    # 4 notes, else 3
            for st_ in range(int(base), int(lo_p) - 1, -1):    # the highest start with room for every note
                ks, k_ = [], st_
                while len(ks) < want_n and k_ < top:
                    k_ += 1
                    if k_ % 12 in pcs and k_ not in held:
                        ks.append(k_)
                if len(ks) == want_n:
                    break
            if len(ks) == want_n:
                n_ = want_n
                break
        else:
            return {"skip": "no room for the arpeggio in the piano's register"}
        hi_v = max(20, min(110, piano_vel - int(md.get("below_piano_vel", 8)) - (4 if vs < 1.0 else 0)))
        lo_v = max(16, hi_v - int(md.get("spread_vel", 10)))
        step = beats / n_
        rows = [[pch, round(i * step, 5), round(beats - i * step, 5), int(k_),
                 _clampv(lo_v + (hi_v - lo_v) * i / max(1, n_ - 1)), 0] for i, k_ in enumerate(ks)]
        return {"ch": pch, "a": a, "len": span, "rows": rows, "vel": f"{rows[0][4]}->{rows[-1][4]}",
                "at": f"ends on bar {bnd:g}"}

    pri = lambda i_, t_: (0 if "climax" in t_ else 1 if "build" in t_ else 2 if "big" in t_ else
                          3 if t_ & {"hook", "drop"} else 4, i_)
    order = sorted(((i_, _ec_tags(table, i_, ec["data"])) for i_ in range(1, len(table))), key=lambda x: pri(*x))
    done = []                                             # the climax first: per-song caps keep its moves
    for idx, tags in order:
        into, bnd = table[idx]["section"], float(table[idx]["bar"])
        pm_ = sec_mt[idx - 1]
        qb = 4.0 if pm_["legacy"] else float(pm_["qbar"] or 4.0)
        a_min = max(float(table[idx - 1]["bar"]), float(s0))
        if bnd - a_min <= 1e-6:
            continue
        lst = next((its for place, its in od.get("places") or [] if place in tags), [])
        budget, got, roles_n = int(cfg.get("per_boundary") or 1), 0, {}
        nxt = {n[3] % 12 for n in harm_notes if n[0] <= bnd + 1e-6 < n[1] or abs(n[0] - bnd) < 1e-6}
        end_pcs = sorted({n[3] % 12 for n in harm_notes if n[0] < bnd - 1e-6 and n[1] > bnd - 1.0 / qb + 1e-6})
        for mv in lst:
            md = moves.get(mv) or {}
            if got >= budget:
                break
            if mv not in ORGANIC_MOVES or mv not in allowed or not md:
                continue
            if md.get("purpose") and not set(md["purpose"]) & tags:
                continue
            role = md.get("role") or mv
            if roles_n.get(role, 0) >= int(maxr.get(role, 1)):
                continue
            if md.get("max_per_song") is not None and per_song.get(mv, 0) >= int(md["max_per_song"]):
                continue
            if mv in ("string_swell", "choir_breath"):
                r = swell(mv, md, md.get("channels") or (["low_strings", "strings"] if mv == "string_swell"
                                                          else ["choir"]), a_min, bnd, qb, nxt)
            elif mv in ("timpani_roll", "cymbal_roll"):
                r = roll(mv, md, a_min, bnd, qb, nxt, end_pcs)
            else:
                r = pickup(md, a_min, bnd, qb)
            if r.get("skip"):
                skipped.append({"kind": mv, "into": into, "bar": bnd, "why": r["skip"]})
                if r.get("missing"):
                    missing.setdefault(mv, md)
                continue
            per_song[mv] = per_song.get(mv, 0) + 1
            roles_n[role] = roles_n.get(role, 0) + 1
            got += 1
            ch, a = int(r["ch"]), float(r["a"])
            for x in r["rows"]:
                mine.append((a + x[1] / qb, a + (x[1] + x[2]) / qb, ch, int(x[3]), int(x[4])))
            done.append((a, mv, md, r, idx, into, bnd, qb))
    for a, mv, md, r, idx, into, bnd, qb in sorted(done, key=lambda x: (x[0], x[1])):   # named in song order
        ordinal[mv] = ordinal.get(mv, 0) + 1
        name = f"{md.get('label') or ORG_NAMES[mv]} {ordinal[mv]}"
        ch, ln = int(r["ch"]), float(r["len"])
        old = pats_by_name.get(name.lower()) or {}
        gen[name] = {"rows": written_rows([list(x) for x in r["rows"]], v.get("offsets") or {}),
                     "clear": sorted({int(x[2]) for x in old.get("notes") or []} | {ch}), "role": f"fx:{mv}"}
        entries.append({"pattern": name, "role": f"fx:{mv}", "bar": round(a, 4), "len": round(ln, 4),
                        "sec": idx - 1})
        placed.append({"into": into, "bar": round(a, 4), "kind": mv, "beats": round(ln * qb, 3), "channel": ch,
                       "on": names(ch), "pattern": name, "at": r["at"], "vel": r["vel"],
                       "purpose": f"into {into} at bar {bnd:g}", "why": md.get("why")})
        if r.get("ramp"):
            ramps.setdefault(ch, []).append((a, bnd, float(r["ramp"].get("depth", 0.4))))
    skipped.sort(key=lambda x: x["bar"])
    ops, notes = [], []
    links = _volume_links(v) if ramps else {}
    for ch, ws in sorted(ramps.items()):
        label = f"{names(ch)} swell @{s0:g}"
        theirs = [x for x in links.get(ch) or [] if x != label]
        if theirs:
            notes.append(f"{names(ch)}: no volume ramp under its swell (its volume is automated already: "
                         f"{theirs[0]}) - the velocities still rise")
            continue
        cur = _chan_volume(v, ch)
        cur = 0.78125 if cur is None else cur
        pts, eps = [], 1.0 / 64
        for i, (a, z, dep) in enumerate(sorted(ws)):
            if i:
                pts.append((a - eps, cur))
            pts += [(a, cur * (1.0 - max(0.0, min(0.9, dep)))), (z, cur)]
        # PC-VERIFY: the channel's volume knob follows the swell clip (dips at the swell's start, back to the user's
        # level on the downbeat and held there); a channel whose plugin ignores FL's channel volume still swells by
        # velocity only
        ops.append({"tool": "flp_add_automation", "channel": ch, "param": "volume",
                    "points": [{"bar": round(b_, 4), "value": round(x_, 4)} for b_, x_ in pts], "name": label,
                    "replace": True})
    miss = []
    for mv, md in missing.items():
        if any(p["kind"] == mv for p in placed):
            continue
        hint = _org_hint(md)
        miss.append({"move": mv, "needs": md.get("needs") or mv,
                     "do": f"add a {md.get('needs') or mv} channel and arrange again" +
                           (f" (owned / free per fl_sound_catalog: {hint['said']})" if hint else "")})
    info = {"moves": len(placed), "piano_velocity": piano_vel, "velocity_cap": int(cap * vs),
            "channels": {w: [names(c) for c in cs[:2]] for w, cs in chans.items() if cs}}
    return placed, skipped, miss, ops, notes, info


def flp_arrange_song(project, form=None, genre=None, mode="build", start_bar=None, transitions=True, builds=True,
                     extend=None, block=None, seed=None, dry_run=False, ear_candy=True, fx_dir=None):
    """Lay the composed parts out as a song (genre form + energy curve), with transitions and builds; or extend a
    section. See register(). Any meter: bars follow the project's time-signature map (generated drums, fills and
    the 'last beat' / 'two beats' transitions are written in the meter of their bars)."""
    # PC-VERIFY: flp_arrange_song in a 3/4 project: the silent last beat / 2-beat dropout / fill land on the bar
    # lines FL shows, and generated 'Drums Light' plays 3-beat bars
    T0 = time.time()
    steps = {}
    _fresh(project)
    v = project_view(project)
    steps["read"] = round(time.time() - T0, 3)
    g, gsrc = _resolve_genre(genre, v)
    seed = int(seed) if seed is not None else _seed(os.path.basename(project), "arrange") % 100000
    if extend:
        return _extend(project, v, g, extend, seed, dry_run, steps, T0)
    blocks = _materials(v)
    if not blocks:
        raise ValueError("no composed parts to arrange: flp_compose(what='beat') first, or name patterns "
                         "'<Section> Drums/Bass/Chords/Melody'")
    reg = v["registry"]
    auds = {k: a for k, a in (reg.get("auditions") or {}).items() if a}
    if block:
        main = next((b for b in blocks if b.lower() == str(block).lower()), None)
        if main is None:
            raise ValueError(f"no block '{block}' (have {sorted(blocks)})")
    else:
        picked = [b for b, x in blocks.items() if x.get("picked")]
        beats = [b for b, x in blocks.items() if x.get("what") == "beat" and not x.get("variation")]
        main = (picked or beats or sorted(blocks, key=lambda b: -len(blocks[b]["parts"])))[-1 if (picked or beats) else 0]
        if auds and not picked:
            raise ValueError(f"pick a variation first: flp_compose(what='pick', section='{next(iter(auds))}', variation=...) "
                             f"(or block='<name>' from {sorted(blocks)})")
    sections = _parse_form(form, g)
    if not sections:
        raise ValueError(f"form {form!r} has no sections with bars: e.g. 'Intro:4, Verse:8, Hook:8' (or leave form out "
                         f"for {g['name']}'s own)")
    notes_out_pre = []
    mode = (mode or "build").lower()
    pattern_clips = [c for c in v["clips"] if c.get("kind", "pattern") == "pattern"]
    mat_names = {pn.strip().lower() for b in blocks.values() for pn in b["parts"].values()}
    if mode == "extend":
        s0 = float(v["end_bar"]) if v["clips"] else 1.0
    else:
        s0 = float(start_bar or 1)
    total = sum(L for _, L in sections)
    (mnum, mden), _ = _meter_at(v, s0)
    mt = _set_meter((mnum, mden))                         # the meter the arrangement starts in
    sec_mt, s_ = [], s0                                   # each section's meter (time-signature markers)
    for name, L in sections:
        (sn_, sd_), later_ = _meter_at(v, s_)
        sec_mt.append(meter_info((sn_, sd_)))
        inside = [f"{t_} at bar {b_:g}" for b_, t_ in later_ if s_ < b_ < s_ + L - 1e-6]
        if inside:
            notes_out_pre.append(f"{name} (bars {s_:g}-{s_ + L:g}) changes meter inside ({', '.join(inside)}): its "
                                 f"parts were laid in {sn_}/{sd_} bars - check that section by ear")
        s_ += L
    prev_arr = reg.get("arrangement") if isinstance(reg.get("arrangement"), dict) else None
    gen_prev = {str(n).strip().lower() for n in (prev_arr or {}).get("generated") or []}
    if prev_arr and not (prev_arr or {}).get("generated"):   # journals from before the list was kept
        gen_prev |= {f"drums {x}" for x in ("full", "light", "sparse", "half", "build")} | {"fill", "riser"}
    relay = mat_names | gen_prev                          # what replace may re-lay: the composed + generated parts
    cname = lambda c: str(c.get("name", "")).strip().lower()
    remaining = [c for c in pattern_clips if not (mode == "replace" and cname(c) in relay)]
    clash = [c for c in remaining if c["bar"] < s0 + total and c["bar"] + c["length_bars"] > s0 + 1e-6]
    if clash and mode != "extend":
        other = [c for c in clash if cname(c) not in relay]
        shown = ", ".join(sorted({str(c.get("name") or "?") for c in (other or clash)})[:6])
        after = f"start_bar={v['end_bar']:g} or mode='extend' appends after bar {v['end_bar']:g}"
        if mode == "replace":
            raise ValueError(f"bars {s0:g}-{s0 + total:g} have {len(clash)} clips that aren't composed parts ({shown}): "
                             "replace only re-lays the composed parts and leaves every other clip where it is, so they "
                             f"would play on top - move or remove those clips first (flp_playlist), or {after}")
        if not other:
            raise ValueError(f"bars {s0:g}-{s0 + total:g} already have {len(clash)} composed clips ({shown}): "
                             f"mode='replace' re-lays them (other clips stay), or {after}")
        raise ValueError(f"bars {s0:g}-{s0 + total:g} already have {len(clash)} clips ({shown}): {after} "
                         "(or move/remove those clips first)")
    dm = {p: tuple(x) for p, x in (reg.get("drum_map") or {}).items()}
    if not dm:
        dmap, _ = _drum_map(v, [p for p in DRUM_PARTS if p in ((g.get("drums") or {}).get("parts") or {})])
        dm = dmap
    rem_tracks = {c["track"] for c in remaining}
    ft = max(rem_tracks, default=0) + 1
    order = ["drums", "bass", "chords", "melody", "counter"] + sorted({r for b in blocks.values() for r in b["parts"]} -
                                                                      {"drums", "bass", "chords", "melody", "counter"})
    tracks = {r: ft + i for i, r in enumerate(order)}
    tracks["fx"] = ft + len(order)
    entries, gen, notes_out, table = [], {}, notes_out_pre, []
    tempo = v["tempo"]
    tsname = lambda m_: "" if m_["ts"] == mt["ts"] or (m_["legacy"] and mt["legacy"]) else f" {m_['ts']}"
    mat_ts = {b: (x.get("meter") or "%d/%d" % tuple((v.get("meters") or [(0, 4, 4)])[0][1:])) for b, x in blocks.items()}

    def gen_drums(lvl, nbars, m_):
        nm = f"Drums {lvl.title()}" + tsname(m_)          # a section in another meter gets its own drum pattern
        if nm not in gen:
            with in_meter(m_):
                hs = drums(g, nbars, 1.0, seed, lvl)
            rows = []
            for part, (ch, k, kit) in sorted(dm.items()):
                rows += _rows([h for h in hs if h["part"] == part], ch, k, g, tempo, seed)
            gen[nm] = {"rows": rows, "clear": sorted({c for c, _, _ in dm.values()}), "role": "drums"}
        return nm
    reg_ch = reg.get("channels") or {}
    akey = reg.get("key") or (g.get("keys") or {}).get("default", "C minor")
    sec_notes = []

    def gen_melody(kind, pn, m_):
        """The hook melody as a section variant (call = each phrase's first bar, sparse = its on-beat notes)."""
        nm = f"Melody {kind.title()}" + tsname(m_)
        if nm in gen:
            return nm
        pid = next((p_ for p_, x in v["patterns"].items() if (x.get("name") or "").strip().lower() == pn.strip().lower()), None)
        recs = (v["patterns"].get(pid) or {}).get("notes") or []
        if not recs:
            return None
        st = v["P"] / 4.0
        ns_ = [{"s": r[0] / st, "l": r[3] / st, "k": r[4], "v": r[11], "ch": r[2], "slide": bool(r[1] & SLIDE_FLAG)}
               for r in recs]
        with in_meter(m_):
            out = melody_variant(ns_, kind)
        if not out:
            return None
        gen[nm] = {"rows": [[n["ch"], round(n["s"] / 4.0, 5), round(n["l"] / 4.0, 5), n["k"], n["v"], 1 if n["slide"] else 0]
                            for n in out], "clear": sorted({n["ch"] for n in ns_}), "role": "melody"}
        return nm

    def gen_bridge(part, blk, nbars, m_, lvl):
        """A bridge with its own harmony: a contrasting progression of the genre, chords / bass / a new melody on it."""
        nm = f"Bridge {PART_NAMES.get(part, part.title())}" + tsname(m_)
        if nm in gen:
            return nm
        if gen.get("_bridge_failed"):
            return None
        try:
            with in_meter(m_):
                chs_b, about = bridge_harmony(akey, g, nbars, blk.get("chords"), seed)
                cn_b, _ = chord_part(chs_b, g, akey, seed, 1)
                kk = [h["s"] for h in drums(g, nbars, 1.0, seed, lvl) if h["part"] == "kick"] if lvl != "none" else []
                bn_b, _ = bassline(chs_b, g, kk, seed, 1, nbars, akey)
                vreg = ((g.get("harmony") or {}).get("voicing") or {}).get("register") or [52, 74]
                mud_guard(cn_b, bn_b, int(vreg[1]) + 7, chs_b)
                mn_b, _ = melody(akey, chs_b, g, nbars, seed + 17, 1, accomp=cn_b, candidates=4)
                melody_guard(cn_b, mn_b, int(vreg[0]))
        except Exception as e:                            # no contrast possible: the hook's own parts play
            gen["_bridge_failed"] = f"{type(e).__name__}: {e}"
            return None
        for role_, ns_ in (("chords", cn_b), ("bass", encode_glides(bn_b)), ("melody", mn_b)):
            ch_ = reg_ch.get(role_)
            if ch_ is None:
                continue
            strum = float((g.get("harmony") or {}).get("strum_ms", 0)) if role_ == "chords" else 0.0
            rows = written_rows(_rows(ns_, int(ch_), None, g, tempo, seed, strum_ms=strum), v["offsets"])
            gen[f"Bridge {PART_NAMES.get(role_, role_.title())}" + tsname(m_)] = {"rows": rows, "clear": [int(ch_)],
                                                                                 "role": role_}
        gen.setdefault("_bridge_about", about)
        return nm if nm in gen else None
    s = s0
    for idx, (name, L) in enumerate(sections):
        spec = _section_spec(g, name)
        smt = sec_mt[idx]
        blk_name = next((b for b, x in blocks.items() if (x.get("section") or b).lower() == name.lower() and
                         x.get("picked") is not False and not x.get("variation")), main)
        blk = dict(blocks[blk_name], parts=dict(blocks[main]["parts"], **blocks[blk_name]["parts"]))
        B = max(1, int(blk.get("bars") or blocks[main].get("bars") or 8))
        lay = []
        for layer_ in spec.get("layers") or []:
            if layer_ == "drums":
                lvl = spec.get("drums", "full")
                if lvl == "none":
                    continue
                if lvl == "full" and "drums" in blk["parts"]:
                    pn = blk["parts"]["drums"]
                elif dm:
                    pn = gen_drums(lvl, B, smt)
                else:
                    continue
            else:
                pn = blk["parts"].get(layer_)
                if not pn:
                    continue
                mk = _mel_kind(name, spec)
                if layer_ in ("chords", "bass", "melody") and spec.get("harmony") == "contrast":
                    pb = gen_bridge(layer_, blk, B, smt, spec.get("drums", "full"))
                    if pb is None and any(k_.startswith("Bridge ") for k_ in gen):
                        continue                          # (the hook's line would clash with the bridge's chords)
                    pn = pb or pn
                elif layer_ == "melody" and mk == "none":
                    continue
                elif layer_ == "melody" and mk in ("call", "sparse"):
                    pn = gen_melody(mk, pn, smt) or pn
            off = 0
            while off < L:
                entries.append({"pattern": pn, "role": layer_, "bar": s + off, "len": min(B, L - off), "sec": idx})
                off += B
            lay.append(layer_)
        table.append({"section": name, "bar": s, "bars": L, "energy": spec.get("energy"), "layers": lay,
                      "drums": spec.get("drums", "full"), "material": blk_name})
        if not smt["legacy"] or len(v.get("meters") or []) > 1:
            table[-1]["meter"] = smt["ts"]
            wt = mat_ts.get(blk_name) or mat_ts.get(main)
            if wt and wt != smt["ts"] and not (meter_info(wt)["legacy"] and smt["legacy"]) and lay:
                notes_out.append(f"{name} at bar {s:g} is in {smt['ts']} but its material ({blk_name}) was written in "
                                 f"{wt}: those clips don't fit the bars - compose that section in {smt['ts']} "
                                 f"(flp_compose section='{name}')")
        s += L
    trans_done = []
    fx_ch = next((c for c, r in sorted(v["roles"].items()) if r == "fx"), None)
    inserts = _inserts() if (builds or transitions) else {}
    auto_ops = []
    ec = _ec_settings(ear_candy, g, v, project)
    fxm = _fx_mod() if ec else None
    fx_plan, fx_missing, unknown = [], set(), set()      # audio FX on the boundaries: placed after the loop
    ec_skipped, throw_cands = [], []
    ekinds = (ec["data"].get("kinds") or {}) if ec else {}
    prof_ = ec["profile"][1] if ec and ec.get("profile") else None
    why_of = lambda k_: (ekinds.get(k_) or {}).get("why")
    bounds = [(i_, table[i_]["bar"], table[i_]["section"], table[i_ - 1]["section"]) for i_ in range(1, len(table))]
    if table:
        bounds.append((len(table), s0 + total, None, table[-1]["section"]))   # the song's last downbeat
    for idx, bnd, into, frm in (bounds if (transitions or ec) else []):
        pm_ = sec_mt[idx - 1]                             # the bars that end: one beat of their meter (4/4: 1/4 bar)
        beat_b = (pm_["spb"] - pm_["beats"][-1]) / pm_["spb"] if not pm_["legacy"] else 0.25
        ending = [e for e in entries if e["sec"] == idx - 1 and abs(e["bar"] + e["len"] - bnd) < 1e-6]
        parsed, seen_k = [], set()
        for x, aft in ([(x, False) for x in _trans_for(g, into)] + [(x, True) for x in _trans_for(g, frm, "after")]
                       if into else [(x, False) for x in (g.get("transitions") or {}).get("end") or []]):
            kind, opt = _trans_item(x)
            if kind not in seen_k:
                seen_k.add(kind)
                parsed.append((kind, opt, aft))
        label = into or "end"
        tags = _ec_tags(table, idx, ec["data"] if ec else EC_FALLBACK)
        purpose = f"into {into} at bar {bnd:g}" if into else f"the song's last downbeat at bar {bnd:g}"
        silence_b, filled = 0.0, False                    # bars of silence before the downbeat; a fill leads in
        for kind, opt, _a in parsed:
            if kind not in DRUM_TRANS:
                continue
            if not transitions or not into:
                continue
            if kind == "stop":
                for e in ending:
                    if e["len"] > 0.5:
                        e["len"] = round(e["len"] - beat_b, 3)
                silence_b = max(silence_b, beat_b)
                trans_done.append({"into": into, "bar": bnd, "kind": "stop (last beat silent)"})
            elif kind == "dropout":
                hit = [e for e in ending if e["role"] in ("drums", "bass") and e["len"] > 1]
                # two beats (4/4: half a bar); one dotted beat in 6/8 and the like (two would be the bar)
                two_b = 0.5 if pm_["legacy"] else (2 if 2 * pm_["pulse"] < pm_["spb"] else 1) * pm_["pulse"] / pm_["spb"]
                for e in hit:
                    e["len"] = round(e["len"] - two_b, 3)
                if hit:
                    silence_b = max(silence_b, two_b)
                    trans_done.append({"into": into, "bar": bnd, "kind": "dropout (drums and bass out for 2 beats)"})
            elif kind == "fill":
                hit = [e for e in ending if e["role"] == "drums" and e["len"] >= 2] if dm else []
                if hit:
                    for e in hit:
                        e["len"] = round(e["len"] - 1, 3)
                    fname = "Fill" + tsname(pm_)
                    if fname not in gen:
                        with in_meter(pm_):
                            hs = fill_bar(g, seed)
                        rows = []
                        for part, (ch, k, kit) in sorted(dm.items()):
                            rows += _rows([h for h in hs if h["part"] == part], ch, k, g, tempo, seed)
                        gen[fname] = {"rows": rows, "clear": sorted({c for c, _, _ in dm.values()}), "role": "drums"}
                    entries.append({"pattern": fname, "role": "drums", "bar": bnd - 1, "len": 1, "sec": idx - 1})
                    trans_done.append({"into": into, "bar": bnd - 1, "kind": "drum fill"})
                    filled = True
        for kind, _o, _a in parsed:
            if kind not in TRANS_KINDS:
                unknown.add(kind)
        if not ec or ec["mode"] == "organic":            # organic: the moves are planned after the loop
            continue
        after_k = {k for k, _o, a in parsed if a}
        if prof_ is not None:                             # the profile's effects replace the genre's (drums stay)
            pit = next((its for place, its in prof_.get("places") or [] if place in tags), [])
            cand = [_trans_item(x) for x in pit]
            after_k = set()
        else:
            cand = [(k, o) for k, o, _a in parsed if k not in DRUM_TRANS]
        if into and (any(k == "delay_throw" for k, _ in cand) or
                     g["name"] in ((ekinds.get("delay_throw") or {}).get("genres") or [])):
            throw_cands.append({"idx": idx, "bar": bnd, "into": into, "tags": tags})
        cand = [(k, o) for k, o in cand if k in TRANS_KINDS and k not in DRUM_TRANS]
        chosen, sk = _ec_pick(cand, tags, ec, g["name"], "climax" in tags)
        ec_skipped += [{"kind": k, "into": label, "bar": bnd, "why": w} for k, w in sk]
        chosen.sort(key=lambda x: 0 if x[0] == "gap" else 1)      # the silence first: the snare build stops at it
        qb = 4.0 if pm_["legacy"] else float(pm_["qbar"] or 4.0)
        for kind, opt in chosen:
            pur = f"out of {frm} into {into} at bar {bnd:g}" if kind in after_k else purpose
            if kind == "gap":
                half = beat_b / 2.0
                if not into or silence_b >= half - 1e-9:
                    ec_skipped.append({"kind": kind, "into": label, "bar": bnd,
                                       "why": "the music already stops there" if into else "nothing after the end"})
                    continue
                cut = [e for e in entries if e["sec"] == idx - 1 and abs(e["bar"] + e["len"] - bnd) < 1e-6
                       and e["len"] > half + 1e-6 and not str(e["role"]).startswith("fx")]
                for e in cut:
                    e["len"] = round(e["len"] - half, 4)
                if cut:
                    silence_b = half
                    trans_done.append({"into": into, "bar": round(bnd - half, 4), "kind": "gap",
                                       "at": f"half a beat of silence before bar {bnd:g}", "purpose": pur,
                                       "why": why_of(kind)})
            elif kind == "snare_build":
                part = next((p_ for p_ in ("snare", "clap") if p_ in dm), None)
                why_not = ("not before the song's end" if not into else "no snare / clap channel" if part is None
                           else "a drum fill leads in already" if filled else None)
                want = max(qb, min(2 * qb, float(opt.get("beats") or 2 * qb)))     # 1-2 bars
                span = min(want / qb, float(table[idx - 1]["bars"]))
                avail = math.floor((span - silence_b) * qb * 2 + 1e-6) / 2.0     # beats, to half beats
                if why_not is None and avail < 2:
                    why_not = "too little room before the silence"
                if why_not:
                    ec_skipped.append({"kind": kind, "into": label, "bar": bnd, "why": why_not})
                    continue
                ch_s, key_s = int(dm[part][0]), int(dm[part][1])
                nm = f"Snare Build {avail:g}" + tsname(pm_)
                if nm not in gen:
                    gen[nm] = {"rows": snare_build_rows(ch_s, key_s, avail), "clear": [ch_s], "role": "fx:snare_build"}
                b3 = round(bnd - span, 4)
                entries.append({"pattern": nm, "role": "fx:snare_build", "bar": b3, "len": round(avail / qb, 4),
                                "sec": idx - 1})
                trans_done.append({"into": into, "bar": b3, "kind": "snare_build", "beats": avail, "channel": ch_s,
                                   "at": (f"ends on bar {bnd:g}" if silence_b < 1e-9 else
                                          f"ends where the silence before bar {bnd:g} starts"),
                                   "purpose": pur, "why": why_of(kind)})
            elif kind in FX_AUDIO:
                if fxm is not None:
                    fx_plan.append({"kind": kind, "opt": opt, "bar": bnd, "into": label, "idx": idx, "ending": ending,
                                    "purpose": pur})
                elif kind == "riser" and into and transitions:      # no flh_fx: a note on the song's own fx channel
                    if fx_ch is not None:
                        rname = "Riser" + tsname(pm_)     # one bar of the meter it plays in
                        if rname not in gen:
                            gen[rname] = {"rows": [[fx_ch, 0.0, 4.0 if pm_["legacy"] else float(pm_["qbar"]), 60, 100, 0]],
                                          "clear": [fx_ch], "role": "fx"}
                        entries.append({"pattern": rname, "role": "fx", "bar": bnd - 1, "len": 1, "sec": idx - 1})
                        trans_done.append({"into": into, "bar": bnd - 1, "kind": f"riser note on channel {fx_ch}",
                                           "purpose": pur,
                                           "guess": "a reversed cymbal/riser sample should end on the downbeat: check its length"})
                    else:
                        notes_out.append(f"riser into {into} at bar {bnd:g}: no fx/riser channel - add a reverse "
                                         f"cymbal or riser sample ending at bar {bnd:g}")
                else:
                    fx_missing.add(kind)
    if ec and ec["mode"] != "organic":                   # the texture bed under the quiet sections
        cfg_g = _ear_candy_cfg(g)
        bed = prof_.get("bed") if prof_ is not None else next(iter(cfg_g["beds"]), None)
        if bed in FX_BEDS:
            rules_ = ec["data"].get("rules") or EC_FALLBACK["rules"]
            allowed = set(rules_.get("bed_sections") or ()) & set(ec["cfg"].get("beds") or ()) & \
                set((prof_ or {}).get("bed_sections") or (ekinds.get(bed) or {}).get("purpose") or
                    rules_.get("bed_sections") or ())
            under = [(i_, r_) for i_, r_ in enumerate(table) if _sec_kind(r_["section"]) in allowed and r_["bars"] >= 1]
            jch_ = (v.get("journal") or {}).get("channels") or {}
            own = [c for c in sorted(v["channels"]) if (jch_.get(str(c)) or {}).get("role") == "texture" or
                   v["roles"].get(c) == "texture" or
                   str((v["channels"].get(c) or {}).get("name") or "").strip().lower() in (bed, f"{bed} crackle")]
            if under and own:                             # say it: two beds would be one too many if both play
                notes_out.append(f"the song has its own texture channel ({', '.join(str((v['channels'].get(c) or {}).get('name') or c) for c in own[:2])}): "
                                 f"the generated {FX_NAMES[bed]} plays under {', '.join(dict.fromkeys(r_['section'] for _, r_ in under))} - "
                                 "mute one of them if both sound")
            if under and fxm is None:
                fx_missing.add(bed)
            for i_, r_ in (under if fxm is not None else []):
                fx_plan.append({"kind": bed, "bed": True, "bar": r_["bar"], "len": r_["bars"], "idx": i_,
                                "into": r_["section"], "opt": {}, "ending": []})
    if fx_missing:
        notes_out.append(f"audio FX not made ({', '.join(sorted(fx_missing))}): the FX generator (flh_fx) isn't "
                         "installed - fills / drop-outs / stops were still placed")
    if unknown:
        notes_out.append(f"unknown transition kind(s) in the genre data: {', '.join(sorted(unknown))}")
    org_ops, org_info, org_missing = [], None, []
    if ec and ec["mode"] == "organic":                    # real instruments: notes on the song's own channels
        try:
            placed_o, sk_o, org_missing, org_ops, notes_o, org_info = _organic_plan(
                v, g, ec, table, sec_mt, s0, entries, gen, akey, dm, reg)
        except Exception as e:                            # ear candy never breaks the arrangement
            placed_o, sk_o, org_ops, notes_o = [], [], [], [f"organic ear candy skipped ({type(e).__name__}: "
                                                            f"{str(e)[:80]})"]
        trans_done += placed_o
        ec_skipped += sk_o
        notes_out += notes_o
    fx_ops, fx_info, fx_chans, fx_kinds = [], None, {}, []
    if fx_plan:
        if fx_dir is None and getattr(_TL, "scratch", None) and _same_path(project, _TL.scratch):
            notes_out.append("audio FX skipped: the song is built in a scratch folder and no fx_dir was given "
                             "(the WAVs would be removed with it)")
        else:
            fx_ops, fx_info, fx_chans, fx_kinds = _place_fx(project, v, g, fxm, fx_plan, entries, gen, trans_done,
                                                           notes_out, sec_mt, s0, akey, seed, dm, reg, dry_run, steps,
                                                           fx_dir, ec=ec, skipped=ec_skipped)
    for i_, k_ in enumerate(fx_kinds):                    # one playlist track per FX kind, next to the fx track
        tracks[f"fx:{k_}"] = tracks["fx"] + i_
    nxt_t = tracks["fx"] + max(1, len(fx_kinds))
    for r_ in sorted({e["role"] for e in entries if str(e["role"]).startswith("fx:") and e["role"] not in tracks}):
        tracks[r_] = nxt_t                                # the snare build: a track of its own
        nxt_t += 1
    tracks["auto"] = nxt_t
    throw_ops, throw_info, throw_planned = [], None, []
    if ec and throw_cands:
        try:
            throw_ops, placed_t, throw_planned, notes_t, throw_info = _throw_plan(
                v, g, ec, entries, gen, throw_cands, s0, reg, sec_mt, why_of("delay_throw"))
        except Exception as e:                            # ear candy never breaks the arrangement
            throw_ops, placed_t, notes_t = [], [], [f"delay throws skipped ({type(e).__name__}: {str(e)[:80]})"]
        for op_ in throw_ops:
            if op_["tool"] == "flp_add_automation":
                op_["playlist_track"] = tracks["auto"] + 4
        trans_done += placed_t
        notes_out += notes_t
    filt_ops, filt_info = [], None
    if ec and ec["mode"] != "organic":
        cfg_f = _ear_candy_cfg(g)
        af = ec["cfg"].get("filters", "*")
        if af != "*":
            cfg_f["filters"] = [x for x in cfg_f["filters"] if x in {str(y).lower() for y in af or ()}]
        try:
            filt_ops, placed_f, notes_f, filt_info = _filter_plan(v, g, table, s0, total, reg, cfg_f)
        except Exception as e:                            # ear candy never breaks the arrangement
            filt_ops, placed_f, notes_f = [], [], [f"filter moves skipped ({type(e).__name__}: {str(e)[:80]})"]
        for op_ in filt_ops:
            if op_["tool"] == "flp_add_automation":           # the build's 3 lanes on their own tracks
                op_["playlist_track"] = tracks["auto"] + 1 + (1 if " build hp" in op_["name"] else
                                                              2 if " build lp" in op_["name"] else 0)
        for t in placed_f:
            t.setdefault("purpose", t["kind"].split(": ", 1)[-1])
        trans_done += placed_f
        notes_out += notes_f
    if builds:
        for row in table:
            if row["drums"] != "build":
                continue
            ch = (reg.get("channels") or {}).get("chords")
            ins = inserts.get(int(ch)) if ch is not None else None
            if ins:
                cur_db = H["_track_volume_db"](v["d"], ins)
                auto_ops.append({"tool": "flp_add_automation", "track": ins, "param": "volume",
                                 "points": [{"bar": row["bar"], "db": cur_db - 9}, {"bar": row["bar"] + row["bars"], "db": cur_db}],
                                 "name": f"{row['section']} build", "playlist_track": tracks["auto"]})
                trans_done.append({"into": "next", "bar": row["bar"], "kind": f"volume build on insert {ins} (chords)"})
            else:
                notes_out.append(f"{row['section']} at bar {row['bar']:g}: build drums written; a filter/volume sweep "
                                 "needs FL running (to know the mixer insert) - or automate a filter by hand")
    bridge_about = gen.pop("_bridge_about", None)
    bridge_fail = gen.pop("_bridge_failed", None)
    if bridge_fail:
        notes_out.append(f"the bridge plays the hook's harmony (no contrasting progression: {bridge_fail[:80]})")
    by_pat = {}
    for e in entries:
        by_pat.setdefault(e["pattern"], {"role": e["role"], "place": []})["place"].append([round(e["bar"], 3), round(e["len"], 3)])
    ops = []
    gone = []
    if mode == "replace":                                 # only the composed clips inside the re-laid bars go,
        gone_names = relay | {n.lower() for n in gen}     # picked by what they are (pattern, bar, track) when the
        gone = [[c["pattern"], c["bar"], c["track"]] for c in pattern_clips    # batch runs: FL renumbers clip ids
                if cname(c) in gone_names and c.get("pattern") is not None   # each time it saves (F4)
                and c["bar"] < s0 + total - 1e-6 and c["bar"] + c["length_bars"] > s0 + 1e-6]
        if gone:
            ops.append({"tool": "flp_compose_clear", "clips": gone})
    ops += fx_ops                                         # the FX channels first: the notes below are on them
    for pn, x in by_pat.items():
        op = {"tool": "flp_compose_write", "pattern": pn, "place": x["place"], "place_mode": "add",
              "track": tracks.get(x["role"], tracks["fx"])}
        if pn in gen:
            op.update(notes=gen[pn]["rows"], clear_channels=gen[pn]["clear"])
        ops.append(op)
    for op_ in org_ops:                                   # the swells' volume ramps: one lane
        op_["playlist_track"] = tracks["auto"] + 5
    ops += auto_ops + filt_ops + throw_ops + org_ops
    trans_done.sort(key=lambda t: float(t.get("bar") or 0))    # in song order
    for t in trans_done:                                  # every move says what it points at
        if "purpose" not in t and t.get("into") not in (None, "next"):
            t["purpose"] = f"into {t['into']} at bar {t['bar']:g}" if t["into"] != "end" else "the song's end"
    res = {"genre": g["name"], "mode": mode, "start_bar": s0, "bars": total, "sections": table, "transitions": trans_done,
           "material": main, "patterns": len(by_pat), "generated": sorted(gen)}
    placed_ec = [t for t in trans_done if t.get("kind") in FX_AUDIO + EC_MOVES + ORGANIC_MOVES]
    if ec and ec["mode"] == "organic":
        res["ear_candy"] = dict({"mode": "organic", "mode_from": ec["mode_from"], "amount": ec["amount"],
                                 "amount_from": ec["from"]}, **(org_info or {}),
                                **({"volume_ramps": len(org_ops)} if org_ops else {}),
                                **({"missing": org_missing} if org_missing else {}),
                                **({"skipped": ec_skipped[:10], **({"skipped_more": len(ec_skipped) - 10}
                                                                  if len(ec_skipped) > 10 else {})}
                                   if ec_skipped else {}),
                                listen="real-instrument moves on your own channels, kept under the piano: listen at "
                                       "the changes; flp_remove_fx(project, kinds=[...]) takes any of them out")
    elif ec and (fx_info or filt_ops or placed_ec or throw_planned or ec_skipped):
        res["ear_candy"] = dict({"mode": "synthetic", "mode_from": ec["mode_from"], "amount": ec["amount"],
                                 "amount_from": ec["from"]},
                                **({"profile": f"{ec['profile'][0]} ({ec['profile'][2]})"} if ec.get("profile") else {}),
                                **(fx_info or {}),
                                **({"filter": f"{FILTER_PLUGIN} on "
                                              f"{'the Music Bus' if filt_info['track'] else 'the Master'} slot "
                                              f"{filt_info['slot'] + 1}"} if filt_info else {}),
                                **({"delay_throws": f"{DELAY_PLUGIN} wet on insert {throw_info['track']} slot "
                                                   f"{throw_info['slot'] + 1}"} if throw_info else {}),
                                **({"planned": throw_planned} if throw_planned else {}),
                                **({"skipped": ec_skipped[:10], **({"skipped_more": len(ec_skipped) - 10}
                                                                  if len(ec_skipped) > 10 else {})}
                                   if ec_skipped else {}),
                                listen="the FX levels, the texture bed and the filter / delay moves are a first guess: "
                                       "listen at the boundaries")
    if bridge_about:
        res["bridge"] = bridge_about
    if gsrc != "given":
        res["guesses"] = [f"genre {g['name']}: {gsrc}"]
    if notes_out:
        res["notes"] = notes_out
    if dry_run:
        res["dry_run"] = True
        res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
        return res
    er = _carry(res, _edit(project, ops, steps))
    if gone:
        rr = next((x for x in er.get("results") or [] if isinstance(x, dict) and x.get("tool") == "flp_compose_clear"), {})
        res["removed_clips"] = rr.get("removed", len(gone))
        if rr.get("not_found"):
            res.setdefault("notes", []).append(rr.get("note") or f"{rr['not_found']} old clip(s) weren't found")
    try:                                                  # read back: every planned clip where it was planned?
        have = {}
        for c in H["flp_playlist"](_src(project))["clips"]:
            if c.get("kind", "pattern") == "pattern":
                have.setdefault(str(c.get("name") or "").strip().lower(), set()).add(round(c["bar"], 2))
        miss = [f"{pn} @ bar {b:g}" for pn, x in by_pat.items() for b, _ in x["place"]
                if round(b, 2) not in have.get(pn.strip().lower(), set())]
        res["verified"] = not miss
        if miss:
            res["verify_problems"] = {"missing_clips": miss[:12], "count": len(miss)}
    except Exception as e:
        res["verified"] = None
        res["verify_problems"] = f"couldn't read the playlist back: {type(e).__name__}: {str(e)[:100]}"
    secs = [{"name": r["section"], "bar": r["bar"], "end_bar": r["bar"] + r["bars"], "energy": r["energy"]} for r in table]
    old = [x for x in v["journal"].get("sections") or [] if x["bar"] + 1e-6 < s0] if mode != "replace" else []
    jset = {"sections": old + secs, "compose": {"arrangement": {
        "form": [[n, L] for n, L in sections], "start_bar": s0, "material": main,
        "generated": sorted(set(gen) | {n.title() for n in gen_prev})}}}
    if ec:
        jset["compose"]["arrangement"]["ear_candy"] = ec["amount"]
        jset["compose"]["arrangement"]["ear_candy_mode"] = ec["mode"]
        if ec.get("given"):                               # the user's choice holds for the next arrangement
            jset["compose"]["ear_candy"] = ec["given"]
    org_pats = sorted(t["pattern"] for t in trans_done if t.get("kind") in ORGANIC_MOVES and t.get("pattern"))
    if org_pats or org_ops:                               # what flp_remove_fx may take out again
        old_o = (reg.get("organic") or {}) if isinstance(reg.get("organic"), dict) else {}
        jset["compose"]["organic"] = {
            "patterns": sorted(set(old_o.get("patterns") or []) | set(org_pats)),
            "automation": sorted(set(old_o.get("automation") or []) | {op_["name"] for op_ in org_ops})}
    if fx_chans:                                          # the FX samplers by kind (detect_roles: role fx)
        got, ins_of, ins_made = {}, {}, {}
        for made_ in (x for x in er.get("results") or [] if isinstance(x, dict) and x.get("tool") == "flp_compose_fx"):
            for c in made_.get("channels") or []:
                if c.get("kind"):
                    got[c["kind"]] = c["channel"]
                    if made_.get("insert") is not None:
                        ins_of[c["kind"]] = int(made_["insert"])
            if made_.get("insert_made") and made_.get("insert"):    # an insert we named: flp_remove_fx resets it
                bed_ = any(c.get("kind") in FX_BEDS for c in made_.get("channels") or [])
                ins_made["Texture" if bed_ else "FX"] = int(made_["insert"])
        if ins_made:
            jset["compose"]["fx_inserts"] = dict(((reg.get("fx_inserts") or {}) if isinstance(reg.get("fx_inserts"),
                                                                                              dict) else {}), **ins_made)
        fxc = {k: int(got.get(k, x["channel"])) for k, x in fx_chans.items()}
        jset["compose"]["fx_channels"] = fxc
        if len(ins_of) < len(fxc):                        # queued for a lazy reload: read the shadow copy
            try:
                d_ = H["_load"](_src(project)); ev_ = H["_events"](d_)
                for k, c in fxc.items():
                    ins_of.setdefault(k, _mod("flh_newsong")._chan_props(d_, ev_, c)["insert"])
            except Exception:
                pass
        jset["channels"] = {str(c): {"role": "fx", "name": fx_chans[k]["name"], "fx_kind": k,
                                     **({"mixer": ins_of[k]} if ins_of.get(k) is not None else {}),
                                     **({"sample": fx_chans[k]["sample"]} if fx_chans[k].get("sample") else {})}
                            for k, c in fxc.items()}
    if filt_info:
        jset["compose"]["filter"] = filt_info
    if throw_info:
        jset["compose"]["delay_throw"] = throw_info
    _journal_set(project, jset, res)
    t_m = time.time()
    try:                                                  # the song as written, scored like the composer's candidates
        mu = flp_musicality(project, genre=g["name"])
        rows_ = [x for x in mu.get("sections") or [] if x.get("score") is not None]
        res["musicality"] = dict({k: x for k, x in mu["musicality"].items() if k in ("score", "song_form")},
                                 **({"weakest": "%s@%g (%s)" % (min(rows_, key=lambda x: x["score"])["section"],
                                                                min(rows_, key=lambda x: x["score"])["bar"],
                                                                min(rows_, key=lambda x: x["score"])["score"])}
                                    if rows_ else {}))
    except Exception as e:
        res["musicality"] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
    steps["musicality"] = round(time.time() - t_m, 3)
    res["next"] = f"fl_listen(start_bar={s0:g}, end_bar={s0 + total:g}) - check the transitions by ear"
    res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
    return res


MAX_EXTEND_BARS = 1024                                 # one extend at a time: more than any real section
MAX_LOOP_STEPS = 4096                                  # cap for loops whose step comes from data


def _extend(project, v, g, ext, seed, dry_run, steps, T0):
    if isinstance(ext, str):
        ext = {"section": ext}
    name = ext.get("section")
    occ = occurrences(v, name)
    if not occ:
        raise ValueError(f"no section '{name}' (have {[s['name'] for s in v['sections']]})")
    try:
        o = occ[int(ext.get("occurrence", -1))]
    except (IndexError, ValueError, TypeError):
        raise ValueError(f"section '{name}' occurs {len(occ)} time(s): occurrence is 0..{len(occ) - 1} (or -1, the last)")
    L0 = float(o["end_bar"]) - float(o["bar"])
    if not L0 > 1e-6:                                     # a zero-length section would never fill its bars (E2E-6)
        raise ValueError(f"section '{o['name']}' at bar {o['bar']:g} has no length (it ends where it starts): give it an "
                         "end in the song journal (flh_journal(project, set={'sections': [...]})) or extend another section")
    try:                                                  # bars left out: the section's own length (0 isn't that)
        n = float(ext["bars"]) if ext.get("bars") not in (None, "") else L0
    except (TypeError, ValueError):
        n = -1.0
    if not 0 < n <= MAX_EXTEND_BARS:
        raise ValueError(f"bars: extend by 1-{MAX_EXTEND_BARS} bars (got {ext.get('bars')!r})")
    if n / L0 > MAX_LOOP_STEPS:
        raise ValueError(f"section '{o['name']}' is {L0:g} bars long: repeating it {n:g} bars would take more than "
                         f"{MAX_LOOP_STEPS} copies; give fewer bars")
    at = o["end_bar"]
    clips = [c for c in v["clips"] if c.get("kind", "pattern") == "pattern" and o["bar"] - 1e-6 <= c["bar"] < o["end_bar"]
             and abs(c.get("from_bar_in_pattern", 1.0) - 1.0) < 1e-6]
    ms = v.get("meters") or [(0, 4, 4)]
    (sn_, sd_), _ = _meter_at(v, max(float(o["bar"]), float(at) - 0.5))
    smt = meter_info((sn_, sd_))                          # the new bars repeat the section: its meter
    if len(ms) == 1 and tuple(ms[0][1:]) == (4, 4):
        ops = [{"tool": "flp_arrange", "ops": [{"op": "insert_bars", "at": at, "bars": n}]}]
    else:                                                 # bars of the section's meter; markers move along
        ops = [{"tool": "flp_compose_insert", "at": at, "bars": n, "meter": smt["ts"]}]
    reg = v["registry"]
    dm = {p: tuple(x) for p, x in (reg.get("drum_map") or {}).items()}
    drum_names = {pn.strip().lower() for b in (reg.get("blocks") or {}).values() if isinstance(b, dict)
                  for r, pn in (b.get("parts") or {}).items() if r == "drums"}
    var_name = None
    placed = 0
    off = 0.0
    while off < n - 1e-6:
        for c in clips:
            b = at + off + (c["bar"] - o["bar"])
            if b >= at + n - 1e-6:
                continue
            ln = round(min(c["length_bars"], at + n - b), 3)
            is_drum = str(c.get("name", "")).strip().lower() in drum_names
            if ext.get("variation", True) and is_drum and dm:
                var_name = var_name or f"{o['name']} Drums Var"
                ops.append({"tool": "flp_compose_write", "pattern": var_name, "place": [[b, ln]], "track": c["track"],
                            "place_mode": "add"})
            else:
                ops.append({"tool": "flp_playlist", "action": "place", "pattern": c["pattern"], "bar": b, "track": c["track"],
                            "length_bars": ln})
            placed += 1
        off += L0
    if var_name:
        nbars = int(max(1, min(n, max(c["length_bars"] for c in clips))))
        with in_meter(smt):
            hs = drums(g, nbars, 1.0, seed + 101, "full", 1)
        rows = []
        for part, (ch, k, kit) in sorted(dm.items()):
            rows += _rows([h for h in hs if h["part"] == part], ch, k, g, v["tempo"], seed + 101)
        for op in ops:
            if op.get("pattern") == var_name and op["tool"] == "flp_compose_write":
                op.update(notes=rows, clear_channels=sorted({c for c, _, _ in dm.values()}))
                break
    res = {"extended": o["name"], "at_bar": at, "bars": n, "clips_placed": placed,
           "drum_variation": var_name, "note": "everything after the section moved later by the inserted bars"}
    if ops[0]["tool"] == "flp_compose_insert":
        res["meter"] = smt["ts"]
        res["note"] += " (playlist markers too, time-signature markers included)"
    if dry_run:
        res["dry_run"] = True
        return res
    _queued(res, _edit(project, ops, steps))
    secs = []
    for sct in v["journal"].get("sections") or []:
        sct = dict(sct)
        if abs(sct["bar"] - o["bar"]) < 1e-6 and sct["name"] == o["name"]:
            sct["end_bar"] = (sct.get("end_bar") or o["end_bar"]) + n
        elif sct["bar"] >= at - 1e-6:
            sct["bar"] += n
            if sct.get("end_bar"):
                sct["end_bar"] += n
        secs.append(sct)
    if secs:
        _journal_set(project, {"sections": secs}, res)
    res["next"] = f"fl_listen(start_bar={o['bar']:g}, end_bar={at + n:g})"
    res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
    return res


# ============================================================================= flp_adjust
def parse_words(words):
    """'more bounce, a bit darker and less busy' -> [(word, strength, inverted)], [unknown chunks]."""
    adj = adjectives()
    W = adj["words"]
    ints = sorted((adj.get("intensifiers") or {}).items(), key=lambda kv: -len(kv[0]))
    phrases = []
    for k, x in W.items():
        phrases.append((k, k))
        for a in x.get("aliases") or []:
            phrases.append((a.lower(), k))
    phrases.sort(key=lambda p: -len(p[0]))
    out, unknown = [], []
    for chunk in re.split(r",|;|\+|\band\b|\bbut\b|&", str(words).lower()):
        c = " " + re.sub(r"\s+", " ", chunk).strip() + " "
        if not c.strip():
            continue
        hit, phr = None, None
        for ph, k in phrases:
            if re.search(r"(?<![a-z])" + re.escape(ph) + r"(?![a-z])", c):
                hit, phr = k, ph
                break
        if hit is None:
            unknown.append(chunk.strip())
            continue
        rest = c.replace(phr, " ")
        inv = bool(re.search(r"\b(less|not as|not so|fewer)\b", rest))
        mult = 1.0
        for w, m in ints:
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", rest):
                mult = float(m)
                break
        if inv and W[hit].get("opposite") and W[hit]["opposite"] in W:
            out.append((W[hit]["opposite"], mult, False))
        else:
            out.append((hit, mult, inv))
    return out, unknown


def _scale_op(op, m, inv):
    o = dict(op)
    k = o["op"]
    if k == "swing":
        o["delta"] = float(o.get("delta", 0)) * m * (-1 if inv else 1)
    elif k == "velocity":
        sc = float(o.get("scale", 1))
        sc = 1 + (sc - 1) * m
        o["scale"] = 1 / sc if inv else sc
        o["add"] = float(o.get("add", 0)) * m * (-1 if inv else 1)
    elif k == "accent":
        for f in ("downbeat", "backbeat", "offbeat", "eighth"):
            if f in o:
                o[f] = float(o[f]) * m * (-1 if inv else 1)
    elif k == "dynamics":
        r = 1 + (float(o.get("range", 1)) - 1) * m
        o["range"] = 1 / r if inv else r
    elif k == "humanize":
        if inv:
            return {"op": "tighten", "strength": min(1.0, 0.5 * m), "roles": o.get("roles"), "keep_swing": True}
        o["timing_ms"] = float(o.get("timing_ms", 5)) * m
        o["vel"] = float(o.get("vel", 5)) * m
    elif k == "tighten":
        if inv:
            return {"op": "humanize", "timing_ms": 5 * m, "vel": 4 * m, "roles": o.get("roles")}
        o["strength"] = min(1.0, float(o.get("strength", 0.6)) * m)
    elif k == "density":
        f = 1 + (float(o.get("factor", 1)) - 1) * m
        o["factor"] = 1 / f if inv else f
    elif k == "octave":
        if inv:
            o["octaves"] = -int(o.get("octaves", 1))
            o.pop("only_if_above", None)
            o.pop("only_if_below", None)
    elif k == "double":
        if inv:
            return None
    elif k == "length":
        sc = 1 + (float(o.get("scale", 1)) - 1) * m
        o["scale"] = 1 / sc if inv else sc
    elif k == "mode":
        if inv:
            o["to"] = "major" if o.get("to") == "minor" else "minor"
    elif k == "spread":
        if inv:
            o["spread"] = not o.get("spread", True)
    return o


def _pattern_meters(v, pids):
    """{pattern: (meter 'n/d' its notes are read in, [every meter its clips start in])}: the meter at the pattern's
    first clip (time-signature markers), else the project's."""
    ms = v.get("meters") or [(0, 4, 4)]
    base = f"{ms[0][1]}/{ms[0][2]}"
    out = {}
    for pid in pids:
        if len(ms) == 1:
            out[pid] = (base, [base])
            continue
        bars = sorted(c["bar"] for c in v["clips"] if c.get("pattern") == pid)
        seen = list(dict.fromkeys("%d/%d" % _meter_at(v, b)[0] for b in bars)) or [base]
        out[pid] = (seen[0], seen)
    return out


def _group_metrics(notes_by_group, P, tempo, mt=None):
    return {g_: metrics(ns, P, tempo, mt) for g_, ns in sorted(notes_by_group.items()) if ns}


def _groups_of(notes, chspec):
    out = {}
    for n in notes:
        info = chspec.get(str(n["ch"])) or {}
        g_ = "drums" if info.get("drum") else info.get("role", "other")
        out.setdefault(g_, []).append(n)
    return out


EXPECT = {"swing": ("swing", lambda o: 1 if o.get("delta", 0) > 0 else -1),
          "velocity": ("vel", lambda o: 1 if o.get("scale", 1) * 100 + o.get("add", 0) > 100 else -1),
          "density": ("notes", lambda o: 1 if o.get("factor", 1) > 1 else -1),
          "octave": ("pitch", lambda o: 1 if o.get("octaves", 1) > 0 else -1)}


@_reads_fresh
def flp_adjust(project, words, section=None, dry_run=False, seed=None, channels=None, mix_mode="propose"):
    """Plain-English adjustment ('make it bounce more', 'darker and emptier') -> note ops on the song's patterns
    (one batch) + mix intents for the mix engineer. Before/after measured from the file. See register()."""
    T0 = time.time()
    steps = {}
    _fresh(project)
    v = project_view(project)
    parsed, unknown = parse_words(words)
    adj = adjectives()["words"]
    if not parsed:
        raise ValueError(f"no known words in '{words}' (known: {', '.join(sorted(adj))}; add more in data/adjectives.json)")
    ops, mix, sounds, sugg, guesses = [], [], {}, [], []
    for w, m, inv in parsed:
        a = adj[w]
        for op in a.get("ops") or []:
            o = _scale_op(op, m, inv)
            if o:
                o["_word"] = w
                ops.append(o)
        if not inv:
            mix += a.get("mix") or []
            sounds.update(a.get("sound") or {})
            sugg += a.get("suggest") or []
        if a.get("guess"):
            guesses.append(f"{w}: {a['guess']}")
    try:
        g = genre_data(v["registry"].get("genre") or v["journal"].get("genre") or "pop")
    except ValueError:
        g = genre_data("pop")
    half = (g.get("tempo") or {}).get("feel") == "half-time"
    key = v["registry"].get("key") or song_key(v)
    chspec = {}
    only = {int(c) for c in channels} if channels else None
    for ch, r in v["roles"].items():
        if only is not None and ch not in only:
            continue
        drum = _is_drum(r)
        chspec[str(ch)] = {"role": r if drum else ROLE_GROUP.get(r, r), "drum": drum}
    occ = occurrences(v, section)
    if section and not occ:
        raise ValueError(f"no section '{section}' (have {[s['name'] for s in v['sections']]})")
    pids = _patterns_in(v, occ)
    if not pids:
        raise ValueError("no patterns with notes to adjust" + (f" in {section}" if section else ""))
    meter_note = None
    pmeter = _pattern_meters(v, pids)                     # accents / added hits follow each pattern's meter
    spec = {"ops": [{k: x for k, x in o.items() if k != "_word"} for o in ops], "channels": chspec,
            "seed": int(seed) if seed is not None else 1, "tempo": v["tempo"], "half_time": half, "key": key,
            "offsets": {str(c): o for c, o in v["offsets"].items()}}
    specs = {pid: (dict(spec, meter=pmeter[pid][0]) if pmeter[pid][0] not in LEGACY_METERS else spec) for pid in pids}
    odd = sorted({pmeter[p_][0] for p_ in pids} - set(LEGACY_METERS))
    mixed = sorted({p_ for p_ in pids if len(pmeter[p_][1]) > 1})
    common = max((pmeter[p_][0] for p_ in pids), key=lambda t: sum(1 for q in pids if pmeter[q][0] == t))
    mt_m = None if common in LEGACY_METERS else meter_info(common)   # before/after numbers: bars of the main meter
    if odd and any(o["op"] in ("accent", "density") for o in ops):
        meter_note = (f"accents and added hits follow {', '.join(odd)}: strong beats as downbeats, the other beats as "
                      "backbeats, ghost hits before the strong beats")
    if mixed:
        meter_note = ((meter_note + "; ") if meter_note else "") + (
            f"{len(mixed)} pattern(s) play in more than one meter ({', '.join(sorted({t for p_ in mixed for t in pmeter[p_][1]}))}): "
            "their beats were read in the meter of their first clip")
    P = v["P"]
    before_n, pred_n, changes = [], [], {}
    for pid in pids:
        cur = [H["_rec_to_note"](r) for r in v["patterns"][pid]["notes"]]
        cur = [n for n in cur if str(n["ch"]) in chspec]
        new, info = apply_transform(cur, specs[pid], P)
        before_n += cur
        pred_n += new
        for k, c in info.items():
            idx, _, kind = k.partition(":")
            nm = (ops[int(idx)]["_word"] + ":" + kind) if idx.isdigit() and int(idx) < len(ops) else k
            if isinstance(c, int):
                changes[nm] = changes.get(nm, 0) + c
            else:
                changes[nm] = c
    before = _group_metrics(_groups_of(before_n, chspec), P, v["tempo"], mt_m)
    predicted = _group_metrics(_groups_of(pred_n, chspec), P, v["tempo"], mt_m)
    res = {"words": [{"word": w, "strength": m, **({"inverted": True} if inv else {})} for w, m, inv in parsed],
           "patterns": len(pids), "changes": {k: c for k, c in changes.items() if c}, "before": before}
    if meter_note:
        res["meter"] = meter_note
    if occ:                                               # patterns are shared: the same edit plays everywhere
        elsewhere = sorted({(round(c["bar"], 2), v["patterns"][c["pattern"]]["name"] or str(c["pattern"]))
                            for c in v["clips"] if c.get("pattern") in pids
                            and not any(o["bar"] - 1e-6 <= c["bar"] < o["end_bar"] for o in occ)})
        if elsewhere:
            res["also_changes"] = {"bars": sorted({b for b, _ in elsewhere})[:16], "patterns": sorted({n for _, n in elsewhere}),
                                   "why": f"these patterns also play outside {section}; to change only {section}, make "
                                          "them unique first (FL: right-click the clip > Make unique)"}
    if unknown:
        res["not_understood"] = unknown
    if sounds:
        res["sound_hints"] = sounds
    if sugg:
        res["suggestions"] = sorted(set(sugg))
    if guesses:
        res["guesses"] = guesses
    if v["journal"].get("performance"):
        res["warning"] = ("this song has an flp_perform spec: its next render rewrites the performed channels from "
                          "its source - for groove/dynamics prefer flp_perform changes")
    if dry_run:
        res.update(dry_run=True, after_predicted=predicted, mix_intents=sorted(set(mix)))
        res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
        return res
    _queued(res, _edit(project, [{"tool": "flp_compose_write", "pattern": pid, "transform": specs[pid]} for pid in pids], steps))
    v2 = project_view(project)
    after_n = []
    for pid in pids:
        after_n += [n for n in (H["_rec_to_note"](r) for r in v2["patterns"].get(pid, {"notes": []})["notes"]) if str(n["ch"]) in chspec]
    after = _group_metrics(_groups_of(after_n, chspec), P, v["tempo"], mt_m)
    res["after"] = after
    checks, moved_not = [], []
    if len(parsed) > 1:
        res["checks_note"] = "several words at once: per-word direction checks aren't separable; compare before/after"
    for o in ops if len(parsed) == 1 else []:
        if o["op"] not in EXPECT:
            continue
        field, sign = EXPECT[o["op"]][0], EXPECT[o["op"]][1](o)
        for grp in sorted(set(after) & set(before)):
            rl = o.get("roles") or ["all"]
            if not ("all" in rl or grp in rl or (grp != "drums" and "melodic" in rl)):
                continue
            b, a = before[grp].get(field), after[grp].get(field)
            if b is None or a is None:
                continue
            (checks if (a - b) * sign > 0 else moved_not).append(f"{o['_word']}: {grp} {field} {b} -> {a}")
    res["as_expected"] = checks
    if moved_not:
        res["did_not_move"] = moved_not
    mix_words = sorted(set(mix))
    if mix_words:
        res["mix_intents"] = mix_words
        pend = False
        try:                                                  # edits still on their way into FL: a live listen now
            pend = bool(H["_pending_get"](project)) or bool(H.get("_AUTOSYNC"))   # hears nothing (PC: 12 s lost)
        except Exception:
            pass
        if H.get("mix_engineer") and mix_mode != "off" and not pend:
            t = time.time()
            try:
                mr = H["mix_engineer"](goal=", ".join(mix_words), section=section, genre=g["name"], project=project,
                                       apply=(mix_mode == "apply"))
                res["mix"] = {k: mr[k] for k in list(mr)[:8] if k != "steps_s"} if isinstance(mr, dict) else str(mr)[:400]
            except Exception as e:
                res["mix"] = {"not_run": f"{type(e).__name__}: {e}"[:300],
                              "do": f"mix_engineer(goal='{', '.join(mix_words)}') once FL is open and playing"}
            steps["mix"] = round(time.time() - t, 3)
        else:
            res["mix_suggestions"] = mix_words
            if pend:
                res["mix_next"] = (f"once the edits are in FL: fl_mix_engineer(goal='{', '.join(mix_words)}') for the "
                                   "mix, or fl_sound_shape(channel, words) for one instrument's sound")
    if H.get("intent_verify"):
        res["audio_check"] = ("note-level checks are above; for a measured sound check render before/after and "
                              f"intent_verify(before, after, '{', '.join(mix_words) or str(words)}')")
    res["next"] = "fl_listen the section: the numbers moved, but only ears can say it bounces (flp_restore undoes it)"
    res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
    return res


# ============================================================================= compose_around (an idea -> a song part)
AROUND_PARTS = ("chords", "bass", "drums", "counter", "melody") + tuple(LAYER_ROLES)
AROUND_ALIASES = {"beat": ["chords", "bass", "drums"], "all": ["chords", "bass", "drums", "counter"],
                  "full": ["chords", "bass", "drums", "counter"], "harmony": ["chords"], "808": ["bass"],
                  "keys": ["chords"], "drum": ["drums"], "lead": ["melody"], "counter-melody": ["counter"]}


def _idea_notes(idea):
    """The capture hand-off ({notes: [[bar, length_bars, pitch, vel]], beats_per_bar, ...}) -> notes in 16th steps
    from bar 1 [{s, l, k, v}] and steps per bar."""
    if isinstance(idea, dict) and "idea" in idea and "notes" not in idea:
        idea = idea["idea"]
    if not isinstance(idea, dict) or not idea.get("notes"):
        raise ValueError("idea needs notes: [[bar, length_bars, pitch, velocity], ...] (bar 1 = the idea's start), "
                         "e.g. the 'idea' of fl_capture_idea / capture_from_midi_file / audio_to_midi")
    S = _spb()                                             # the caller set the idea's meter (idea_meter)
    out = []
    for row in idea["notes"]:
        if isinstance(row, dict):
            bar, ln, k, v = row.get("bar"), row.get("length_bars", row.get("len")), row.get("pitch", row.get("key")), row.get("vel", 90)
        else:
            bar, ln, k, v = (list(row) + [90])[:4]
        k = fl_music.parse_note(k) if not isinstance(k, (int, float)) else int(k)
        out.append({"s": round((float(bar) - 1) * S, 3), "l": round(max(0.25, float(ln) * S), 3), "k": int(k),
                    "v": _clampv(v)})
    out.sort(key=lambda n: (n["s"], n["k"]))
    if out[0]["s"] < 0:                                    # a pickup before bar 1: keep it, shift nothing else
        out = [n for n in out if n["s"] >= -S]
    return idea, out


def _poly(notes):
    groups = {}
    for n in notes:
        groups.setdefault(round(n["s"] * 4) / 4, []).append(n)
    return len(notes) / max(1, len(groups))


def idea_swing(notes):
    """How the idea swings: (ratio 0.5 straight .. 0.67 triplet, grid 16 | 8) - 16th swing (the off 16ths late) or
    8th swing (the off 8ths late, jazz / shuffle), or (None, None) when it has too few off-beat notes to tell."""
    r16, r8 = [], []
    for n in notes:
        x = n["s"]
        if 2.2 <= x % 4 <= 2.95:
            r8.append((x % 4) / 4.0)
        elif 0.8 <= x % 2 <= 1.5:
            r16.append((x % 2) / 2.0)
    if len(r8) >= 4 and len(r8) > len(r16):
        r8.sort()
        return round(r8[len(r8) // 2], 3), 8
    if len(r16) >= 4:
        r16.sort()
        return round(r16[len(r16) // 2], 3), 16
    return None, None


def idea_key(notes):
    """Key of a played line: Krumhansl-Schmuckler on duration x metric weight (beats 1 and 3 count double), near
    ties (within 0.12) decided by the tonic triad on the strong beats and the first / last note. -> (key, r)."""
    w = []
    for n in notes:
        w.append({"key": n["k"], "len": max(1.0, n["l"]) * (2.0 if _near_strong(n["s"]) else 1.0)})
    ks = fl_music.detect_key(w, 6)
    if not ks:
        return None, 0.0
    strong = [n["k"] % 12 for n in notes if _near_strong(n["s"])] or [n["k"] % 12 for n in notes]
    first, last = notes[0]["k"] % 12, notes[-1]["k"] % 12

    def bonus(k):
        r, mode, iv, fam = key_info(k)
        triad = {r, (r + (4 if fam == "major" else 3)) % 12, (r + 7) % 12}
        dom = {(r + 7) % 12, (r + 11) % 12, (r + 2) % 12}  # a phrase may stop on V (half cadence)
        return 0.2 * sum(1 for p in strong if p in triad) / len(strong) + 0.04 * (first in triad) + \
            0.03 * (last == r) + 0.03 * (last in dom and last != r)
    near = [k for k in ks if ks[0][1] - k[1] <= 0.12]
    best = max(near, key=lambda k: k[1] + bonus(k[0]))
    return best[0], best[1]


def guess_genre(bpm, swing=None, minor=None, poly=1.0):
    """Rank the genres for an idea from its tempo, swing and mode. -> [(name, score 0..1, why)]. A guess."""
    out = []
    for name, g in genres().items():
        if name.startswith("_"):
            continue
        t = g.get("tempo") or {}
        lo, hi, dflt = float(t.get("min", 60)), float(t.get("max", 180)), float(t.get("default", 100))
        b = float(bpm or dflt)
        why = []
        if lo <= b <= hi:
            st = 1.0 - 0.4 * abs(b - dflt) / max(8.0, hi - lo)
        elif lo <= 2 * b <= hi or lo <= b / 2 <= hi:
            st = 0.55
            why.append("half/double time")
        else:
            st = max(0.0, 0.45 - min(abs(b - lo), abs(b - hi)) / 30.0)
        gs = float(g.get("swing", 0.5))
        if swing is None:
            ss = 0.6
        else:
            ss = max(0.0, 1.0 - abs(float(swing) - gs) / 0.08)
        modes = (g.get("keys") or {}).get("modes") or {}
        tot = sum(modes.values()) or 1.0
        mn = sum(w for m_, w in modes.items() if m_ not in ("major", "lydian", "mixolydian", "major pentatonic")) / tot
        sm = 0.6 if minor is None else (mn if minor else 1.0 - mn)
        score = (2.0 * st + 1.0 * ss + 0.7 * sm + 0.4 * float(g.get("guess_weight", 0.5))) / 4.1
        out.append((name, round(score, 3), why))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


def _cands_for(key, g):
    """Chord candidates for harmonizing in a key: the diatonic triads + the genre's own chords."""
    root, mode, iv, fam = key_info(key)
    toks = (["I", "ii", "iii", "IV", "V", "vi"] if fam == "major" else ["i", "iv", "v", "V", "VI", "III", "VII", "iiø"])
    weights = {}
    for pr in (g.get("harmony") or {}).get("progressions") or []:
        if pr.get("mode", "any") not in (fam, "any"):
            continue
        for t in pr["rn"]:
            weights[t] = weights.get(t, 0.0) + float(pr.get("w", 1))
    toks += [t for t in weights if t not in toks]
    out, seen = [], set()
    for t in toks:
        try:
            c = chord_from_token(t, key)
        except ValueError:
            continue
        sig = (c["root"], c["q"])
        if sig in seen:
            continue
        seen.add(sig)
        c["_w"] = weights.get(t, 0.0)
        out.append(c)
    mx = max([c["_w"] for c in out] + [1.0])
    for c in out:
        c["_w"] /= mx
    return out


def _bigrams(g, key):
    """Chord-to-chord counts from the genre's progressions (looped), by (root, quality)."""
    _, _, _, fam = key_info(key)
    bg = {}
    for pr in (g.get("harmony") or {}).get("progressions") or []:
        if pr.get("mode", "any") not in (fam, "any"):
            continue
        try:
            seq = [chord_from_token(t, key) for t in pr["rn"]]
        except ValueError:
            continue
        for a, b in zip(seq, seq[1:] + seq[:1]):
            k = ((a["root"], a["q"]), (b["root"], b["q"]))
            bg[k] = bg.get(k, 0.0) + float(pr.get("w", 1))
    mx = max(list(bg.values()) + [1.0])
    return {k: v / mx for k, v in bg.items()}


def harmonize(notes, key, genre, bars, seg=None, prior=None, bass=False):
    """Chords for a played / hummed line (Viterbi over the key's chords + the genre's): chord tones on the strong
    beats and long notes, avoid notes punished, the genre's chord moves and falling fifths preferred, the tonic
    first. bass=True: the line is a bassline (its notes are roots or inversions). prior: chord names per bar from
    the capture's own read (a small bonus). -> chord timeline (same chord in a row = one longer chord)."""
    g = genre_data(genre)
    root, _, _, fam = key_info(key)
    cands = _cands_for(key, g)
    bg = _bigrams(g, key)
    kp = set(scale_pcs(key, "key"))
    S = _spb()
    seg = seg or S
    nseg = max(1, int(math.ceil(bars * S / seg)))
    ext7 = float(((g.get("harmony") or {}).get("voicing") or {}).get("extensions", {}).get("7", 0)) >= 0.5
    pri = []
    for i in range(nseg):
        name = (prior or [None] * 999)[int(i * seg // S)] if prior and int(i * seg // S) < len(prior) else None
        pc = None
        if name and name not in ("-", "?"):
            try:
                pc = chord_from_token(str(name).split("/")[0], key)
            except ValueError:
                pc = None
        pri.append(pc)
    emit = []
    for i in range(nseg):
        a, b = i * seg, (i + 1) * seg
        row = []
        seg_notes = [n for n in notes if n["s"] < b - 0.45 and n["s"] + n["l"] > a + 0.45]
        low = min(seg_notes, key=lambda n: (n["k"], n["s"])) if (bass and seg_notes) else None
        first = min(seg_notes, key=lambda n: (max(n["s"], a), n["k"])) if (bass and seg_notes) else None
        tot = 0.0
        for n in seg_notes:
            tot += (min(b, n["s"] + n["l"]) - max(a, n["s"])) * (1.6 if (n["s"] >= a - 0.45 and _near_strong(n["s"])) else 1.0)
        for c in cands:
            pcs = set(chord_pcs(c))
            sev = next(((c["root"] + i) % 12 for i in (10, 11) if i in c["iv"]), None)
            if sev is None:                                # the key's own 7th above this root
                sev = next(((c["root"] + i) % 12 for i in (11, 10) if (c["root"] + i) % 12 in kp), (c["root"] + 10) % 12)
            sc = 0.0
            for n in seg_notes:
                d = min(b, n["s"] + n["l"]) - max(a, n["s"])
                w = d * (1.6 if (n["s"] >= a - 0.45 and _near_strong(n["s"])) else 1.0)
                pc = n["k"] % 12
                if pc in pcs:
                    sc += w
                elif pc == sev or (ext7 and pc == (c["root"] + 2) % 12):
                    sc += (0.5 if ext7 else 0.2) * w
                elif (pc - 1) % 12 in pcs and n["l"] >= 2:
                    sc -= 0.9 * w                          # an avoid note held over it
                else:
                    sc -= 0.35 * w
            sc = sc / tot if tot else 0.0
            if bass and first is not None:
                bp = first["k"] % 12
                sc += 1.0 if bp == c["root"] else (0.25 if bp in pcs else -0.6)
                if low is not None and low["k"] % 12 == c["root"]:
                    sc += 0.2
            sc += 0.12 * c["_w"]
            p = pri[i]
            if p is not None and (p["root"], p["q"][:1] == "m") == (c["root"], c["q"][:1] == "m"):
                sc += 0.3
            if i == 0 and c["root"] == root:
                sc += 0.25
            row.append(sc)
        emit.append(row)
    n = len(cands)

    def trans(x, y):
        a, b = cands[x], cands[y]
        t = 0.45 * bg.get(((a["root"], a["q"]), (b["root"], b["q"])), 0.0)
        if x == y:
            t += 0.12
        elif (b["root"] - a["root"]) % 12 == 5:
            t += 0.15                                      # falling fifth
        elif (b["root"] - a["root"]) % 12 in (2, 10):
            t += 0.04
        return t
    T = [[trans(x, y) for y in range(n)] for x in range(n)]
    score = list(emit[0])
    back = []
    for i in range(1, nseg):
        new, bk = [], []
        for y in range(n):
            best = max(range(n), key=lambda x: score[x] + T[x][y])
            new.append(score[best] + T[best][y] + emit[i][y])
            bk.append(best)
        score, back = new, back + [bk]
    last = max(range(n), key=lambda y: score[y])
    path = [last]
    for bk in reversed(back):
        path.append(bk[path[-1]])
    path.reverse()
    out = []
    for i, ci in enumerate(path):
        c = cands[ci]
        if out and out[-1]["root"] == c["root"] and out[-1]["q"] == c["q"]:
            out[-1]["len"] += seg
            continue
        d = {k: v for k, v in c.items() if not k.startswith("_")}
        out.append(dict(copy.deepcopy(d), start=i * seg, len=seg))
    total = int(round(bars * S))
    out = [c for c in out if c["start"] < total]
    if out:
        out[-1]["len"] = total - out[-1]["start"]
    fit = []
    for c in out:                                          # how well the line sits on the chords (for the report)
        ns_ = [x for x in notes if c["start"] - 0.45 <= x["s"] < c["start"] + c["len"] - 0.45 and _near_strong(x["s"])]
        if ns_:
            fit.append(sum(1 for x in ns_ if x["k"] % 12 in set(chord_pcs(c))) / len(ns_))
    return out, {"strong_beat_chord_tones": round(sum(fit) / len(fit), 2) if fit else None}


def _chords_from_names(names, key, bars):
    """Chord names per bar ('-' = keep the previous chord) -> chord timeline, or [] when nothing usable."""
    out = []
    S = _spb()
    for i in range(int(bars)):
        nm = names[i] if i < len(names) else "-"
        c = None
        if nm and str(nm).strip() not in ("-", "?", ""):
            try:
                c = chord_from_token(str(nm).split("/")[0].strip(), key)
                c["name"] = str(nm)
            except ValueError:
                c = None
        if c is None:
            if out:
                out[-1]["len"] += S
            continue
        if out and out[-1]["name"] == c["name"]:
            out[-1]["len"] += S
            continue
        c["start"], c["len"] = i * S, S
        out.append(c)
    if out and out[0]["start"] > 0:
        out[0]["len"] += out[0]["start"]
        out[0]["start"] = 0
    return out


def _near(x, grid, tol=0.45):
    """Is a (possibly humanized) step position within tol of a multiple of grid?"""
    r = x % grid
    return r <= tol or grid - r <= tol


def clear_rubs(notes, accomp, lo=36, hi=96):
    """Sustained layer notes (pad / strings / organ) that sit a semitone (or maj7) inside an octave from a held
    accompaniment note move an octave (the way that rubs nothing), or are left out when their chord keeps 2+ notes.
    Changes notes in place. -> {moved, dropped}."""
    acc = sorted(accomp or [], key=lambda n: n["s"])
    out = {"moved": 0, "dropped": 0}
    if not acc:
        return out

    def rubs(k, n):
        for a in acc:
            if a["s"] >= n["s"] + n["l"]:
                break
            if min(a["s"] + a["l"], n["s"] + n["l"]) - max(a["s"], n["s"]) >= 2 and abs(a["k"] - k) % 12 in (1, 11) \
                    and abs(a["k"] - k) < 12:
                return True
        return False
    drop = set()
    for i, n in enumerate(notes):
        if not rubs(n["k"], n):
            continue
        mates = [x["k"] for j, x in enumerate(notes) if j != i and j not in drop and abs(x["s"] - n["s"]) < 0.5]
        for k2 in (n["k"] + 12, n["k"] - 12):
            if max(52, lo - 5) <= k2 <= hi + 7 and not rubs(k2, n) and not any(abs(k2 - m_) <= 2 for m_ in mates):
                n["k"] = k2
                out["moved"] += 1
                break
        else:
            if len(mates) >= 2:
                drop.add(i)
                out["dropped"] += 1
    if drop:
        notes[:] = [n for j, n in enumerate(notes) if j not in drop]
    return out


def _fit_line(line, accomp, key, chords, lo=0, hi=127):
    """Move notes of a generated line that grind a semitone (or b9) against the held accompaniment (the user's
    own chords/bass, which stay untouched) to the nearest chord-scale note that doesn't. -> moved count."""
    acc = sorted(accomp, key=lambda n: n["s"])
    sc = scale_pcs(key, "key")
    moved = 0
    for n in line:
        snd = [a["k"] for a in acc if a["s"] < n["s"] + n["l"] and a["s"] + a["l"] > n["s"] + 1 and
               min(a["s"] + a["l"], n["s"] + n["l"]) - max(a["s"], n["s"]) >= 2]
        if not snd or not any(abs(n["k"] - k) % 12 in (1, 11) and abs(n["k"] - k) < 24 for k in snd):
            continue
        cs = _pitches(chord_scale(sc, chord_at(chords, n["s"]), sc, True), lo, hi)
        ok = [p for p in cs if not any(abs(p - k) % 12 in (1, 11) and abs(p - k) < 24 for k in snd)]
        if ok:
            n["k"] = _nearest(ok, n["k"])
            moved += 1
    return moved


def _align_score(hits, onsets):
    """How well a drum candidate's kicks/snares land with the idea's accents (0..1)."""
    kicks = [h["s"] for h in hits if h["part"] == "kick"]
    backs = [h["s"] for h in hits if h["part"] in ("snare", "clap")]
    if not kicks or not onsets:
        return 0.0
    tot = sum(onsets.values()) or 1.0
    hit = sum(onsets.get(round(s), 0.0) for s in kicks) + 0.5 * sum(onsets.get(round(s), 0.0) for s in backs)
    return hit / tot + 0.2 * sum(1 for s in kicks if round(s) in onsets) / len(kicks)


def _find_idea_pattern(view, notes):
    """A pattern in the project that already holds the idea (same pitches at the same relative steps).
    PC-VERIFY: after fl_capture_idea in FL, capture_and_build(project=...) finds the recorded pattern (no copy)."""
    if not notes:
        return None
    st = view["P"] / 4.0
    want = [(round(n["s"] - notes[0]["s"]), n["k"]) for n in notes[:48]]
    for pid, p in sorted(view["patterns"].items()):
        rs = sorted(p["notes"], key=lambda r: (r[0], r[4]))
        if len(rs) < len(notes) * 0.9 or not rs:
            continue
        have = {(round((r[0] - rs[0][0]) / st), r[4]) for r in rs}
        hit = sum(1 for w in want if w in have)
        if hit >= 0.9 * len(want):
            chs = [r[2] for r in rs]
            return {"pattern": pid, "name": p.get("name") or str(pid), "channel": max(set(chs), key=chs.count),
                    "bars": [c["bar"] for c in view["clips"] if c.get("pattern") == pid]}
    return None


def _ticks_notes(ns_, ch, P=96):
    st = P / 4.0
    return [{"pos": int(round(max(0.0, n["s"]) * st)), "len": max(1, int(round(n["l"] * st))), "ch": ch,
             "key": int(n["k"]), "vel": int(n["v"]), "slide": bool(n.get("slide"))} for n in ns_]


def _inline(ns_, limit):
    rows = [[round(1 + n["s"] / _spb(), 4), round(n["l"] / _spb(), 4), int(n["k"]), int(n["v"])] + ([1] if n.get("slide") or n.get("glide") else [])
            for n in sorted(ns_, key=lambda n: (n["s"], n["k"]))]
    return rows[:int(limit)]


def idea_meter(idea):
    """The meter an idea was played in: time_signature / meter ('3/4'), else beats_per_bar (quarter beats: 3 -> 3/4,
    3.5 -> 7/8, 6 -> 6/4; a compound 6/8 needs time_signature), else 4/4."""
    if isinstance(idea, dict) and "idea" in idea and "notes" not in idea:
        idea = idea["idea"]
    idea = idea if isinstance(idea, dict) else {}
    ts = idea.get("time_signature") or idea.get("meter")
    return _parse_ts(ts if ts else (idea.get("beats_per_bar") or 4))


@_reads_fresh
@_meter_scope
def compose_around(idea, what=None, genre=None, project=None, **options):
    """Build parts around a played / hummed / transcribed idea (the capture hand-off: notes [[bar, length_bars,
    pitch, vel]], beats_per_bar, bpm, key, chords per bar, bars, source, part) and write them in ONE batch.
    what: chords bass drums counter melody pad strings arp pluck bell brass organ choir (or beat = chords+bass+drums,
    all = + counter). The idea's notes are never changed: the new parts fit around them (its rhythm, phrase length,
    swing; chords inferred from it when it brings none). options: key, chords / progression (use these chords),
    bars, seed, harmonic_rhythm (bar | half | 2bars), part (what the idea is: melody | chords | bass | drums),
    swing (idea | genre | 0.5-0.7), channel ({role: channel}), name (pattern base, default 'Idea'), at_bar,
    write_idea (default true), dry_run, glide, level (drums), variety, output_mid (no project: write a .mid),
    limit (inline notes per part without project/output_mid), full."""
    T0 = time.time()
    steps = {}
    mt = _set_meter(idea_meter(idea))
    mkw = {} if mt["legacy"] else {"beats_per_bar": mt["qbar"], "time_sig": (mt["num"], mt["den"])}
    idea, notes = _idea_notes(idea)
    opts = dict(options or {})
    if isinstance(opts.get("options"), dict):              # capture_and_build(options={...}) passed through twice
        opts.update(opts.pop("options"))
    want = [what] if isinstance(what, str) else list(what or ["chords", "bass", "drums"])
    parts_req = []
    for w in want:
        for x in AROUND_ALIASES.get(str(w).strip().lower(), [str(w).strip().lower()]):
            x = LAYER_ALIASES.get(x, x) if x not in AROUND_PARTS else x
            if x not in AROUND_PARTS:
                raise ValueError(f"what: {', '.join(AROUND_PARTS)} (or beat / all); got '{w}'")
            if x not in parts_req:
                parts_req.append(x)
    seed = int(opts.get("seed") if opts.get("seed") is not None else
               _seed(json.dumps(idea.get("notes")[:32]), ",".join(parts_req)) % 100000)
    end = max(n["s"] + n["l"] for n in notes)
    bars = int(opts.get("bars") or idea.get("bars") or max(1, math.ceil(end / _spb() - 0.15)))
    bars = max(1, min(64, bars))
    bpm = float(opts.get("tempo") or idea.get("bpm") or 0) or None
    poly = _poly(notes)
    src = str(idea.get("source") or "")
    part = str(opts.get("part") or idea.get("part") or "").lower() or (
        "drums" if "drum" in src else "chords" if poly >= 1.6 else
        "bass" if sorted(n["k"] for n in notes)[len(notes) // 2] < 50 else "melody")
    if part not in ("melody", "chords", "bass", "drums"):
        part = "melody"
    guesses, notes_out = [], []
    pitched = [n for n in notes if part != "drums"]
    # ---- key
    key, ksrc = opts.get("key"), "given"
    if not key and part != "drums" and idea.get("key"):
        key, ksrc = idea["key"], "from the capture's read"
    if not key and pitched and part == "chords":
        try:                                               # played chords: flh_score reads them best
            sv = H["score_view"](notes=[{"pos": int(n["s"] * 24), "len": max(1, int(n["l"] * 24)), "key": n["k"],
                                         "vel": n["v"], "ch": 0} for n in pitched], ppq=96, bpm=bpm or 120, **mkw)
            k0 = (sv.get("key") or [[None, 0]])[0]
            if k0 and k0[0]:
                key, ksrc = k0[0], f"detected (score_view, r={k0[1]:.2f})"
        except Exception:
            pass
    if not key and pitched:
        k1, r1 = idea_key(pitched)
        if k1:
            key, ksrc = k1, f"detected from the idea (r={r1:.2f}{', low confidence' if r1 < 0.6 else ''})"
    # ---- genre
    sw, sw_grid = idea_swing(notes)
    gsrc = "given"
    if genre:
        g = genre_data(genre)
    else:
        fam = key_info(key)[3] if key else None
        ranked = guess_genre(bpm or 100, sw, None if fam is None else fam == "minor", poly)
        g = genre_data(ranked[0][0])
        alts = [r[0] for r in ranked[1:4]]
        feel = (f"swung {'8ths' if sw_grid == 8 else '16ths'}" if (sw or 0.5) >= 0.55 else
                "straight" if sw is not None else "feel unknown")
        gsrc = f"guess from {bpm or '?'} BPM, {feel}, {fam or '?'} key (also fits: {', '.join(alts)})"
        guesses.append(f"genre {g['name']}: {gsrc} - pass genre= to choose")
    if not key:
        key, ksrc = (g.get("keys") or {}).get("default", "C minor"), f"{g['name']} default (guess: the idea has no pitches)"
    key_info(key)
    if ksrc != "given":
        guesses.append(f"key {key}: {ksrc}")
    if bpm is None:
        bpm = float((g.get("tempo") or {}).get("default", 120))
        guesses.append(f"tempo {bpm:g}: {g['name']} default (the idea has no tempo)")
    tmin, tmax = (g.get("tempo") or {}).get("min", 0), (g.get("tempo") or {}).get("max", 999)
    if not (tmin <= bpm <= tmax):
        notes_out.append(f"the idea's {bpm:g} BPM is outside {g['name']}'s usual {tmin}-{tmax}"
                         + (" (half/double time feel)" if tmin <= bpm * 2 <= tmax or tmin <= bpm / 2 <= tmax else ""))
    steps["analyse"] = round(time.time() - T0, 3)
    # ---- chords
    t = time.time()
    hr = str(opts.get("harmonic_rhythm") or "bar").lower()
    S, mt = _spb(), _meter()
    half = 8 if mt["legacy"] else next((x for x in mt["seg"] if x and abs(2 * x - S) < 1e-6), S)  # an even split
    seg = {"half": half, "2bars": 2 * S, "bar": S}.get(hr, S)
    chs, csrc, fit = None, None, {}
    if opts.get("chords") or opts.get("progression"):
        chs, hinfo = harmony(key, g, bars, opts.get("chords") or opts.get("progression"), seed)
        csrc = "given"
    elif part == "chords":
        try:
            sv = H["score_view"](notes=[{"pos": int(n["s"] * 24), "len": max(1, int(n["l"] * 24)), "key": n["k"],
                                         "vel": n["v"], "ch": 0} for n in notes], ppq=96, bpm=bpm, segment="auto", **mkw)
            names = ["-"] * bars
            for r in sv.get("chords") or []:
                b_ = float(r.get("bar", 0))
                if b_ == int(b_) and 1 <= b_ <= bars and r.get("chord") not in (None, "-"):
                    names[int(b_) - 1] = r["chord"]
            chs = _chords_from_names(names, key, bars)
            csrc = "read from the idea's chords (score_view)"
        except Exception:
            chs = None
        if not chs and idea.get("chords"):
            chs = _chords_from_names(idea["chords"], key, bars)
            csrc = "the capture's chord read"
    elif part == "drums":
        chs, hinfo = harmony(key, g, bars, None, seed)
        if hinfo.get("key", key) != key:
            key = hinfo["key"]
            notes_out.append(hinfo["note"])
        csrc = f"{g['name']} progression {hinfo['progression']} (the idea is drums)"
    if not chs and part in ("melody", "bass"):
        chs, fit = harmonize(notes, key, g, bars, seg, prior=idea.get("chords"), bass=(part == "bass"))
        csrc = "harmonized from the idea" + (" (bassline = roots)" if part == "bass" else "")
    if not chs:
        chs, hinfo = harmony(key, g, bars, None, seed)
        if hinfo.get("key", key) != key:
            key = hinfo["key"]
            notes_out.append(hinfo["note"])
        csrc = f"{g['name']} progression {hinfo['progression']} (nothing to read from the idea)"
        guesses.append("chords: " + csrc)
    for c in chs:
        c.setdefault("name", c.get("rn"))
    steps["chords"] = round(time.time() - t, 3)
    # ---- generate
    t = time.time()
    gen, about = {}, {}
    skip = {"chords": part == "chords", "bass": part == "bass", "drums": part == "drums", "melody": part == "melody"}
    for r in list(parts_req):
        if skip.get(r):
            parts_req.remove(r)
            notes_out.append(f"{r}: the idea is the {r}, so it was kept as is (not generated)")
    lead = notes if part == "melody" else None
    idea_bass = notes if part == "bass" else None
    idea_chords = notes if part == "chords" else None
    onsets = {}
    for n in notes:
        st_ = round(n["s"])
        onsets[st_] = onsets.get(st_, 0.0) + (1.5 if _on_beat(st_) else 1.0) * min(2.0, 0.5 + n["l"] / 4.0)
    hits = None
    if "drums" in parts_req:
        best = None
        npat = max([len((s_.get("patterns") or [])) for s_ in ((g.get("drums") or {}).get("parts") or {}).values()] + [1])
        for j in range(min(4, max(1, npat))):
            hs = drums(g, bars, float(opts.get("variety", 1.0)), seed, opts.get("level") or "full", j)
            sc = _align_score(hs, onsets)
            if best is None or sc > best[0] + 1e-9:
                best = (sc, hs, j)
        hits = best[1]
        gen["drums"] = hits
        about["drums"] = dict(_summ_drums(hits, bars), locks_to_idea=round(best[0], 2))
        about["drums"].pop("_rolls", None)
    kick = [h["s"] for h in hits if h["part"] == "kick"] if hits else (
        [n["s"] for n in notes if n["k"] in (35, 36)] if part == "drums" else None)
    if "bass" in parts_req:
        bn, bi = bassline(chs, g, kick, seed, 0, bars, key)
        if idea_chords:
            _fit_line(bn, idea_chords, key, chs, *(g.get("bass") or {}).get("register", [28, 52]))
        gen["bass"] = bn
        about["bass"] = dict(bi, notes=len(bn))
    vreg = list(((g.get("harmony") or {}).get("voicing") or {}).get("register") or [52, 74])
    if "chords" in parts_req:
        reg = list(vreg)
        if lead:
            mlo = sorted(n["k"] for n in lead)[max(0, len(lead) // 10)]
            if mlo - 1 - reg[0] >= 12:
                reg[1] = min(reg[1], mlo - 1)              # the keys stay under the idea when there's room
        cn, ci = chord_part(chs, g, key, seed, 0, opts.get("style"), reg)
        low = gen.get("bass") or idea_bass
        if low:
            mud_guard(cn, low, int(reg[1]) + 7, chs)
        mg = melody_guard(cn, lead, int(reg[0])) if lead else {"moved": 0, "dropped": 0}
        gen["chords"] = cn
        about["chords"] = {"voicing": ci["style"], "register": [fl_music.note_name(reg[0]), fl_music.note_name(reg[1])],
                           "rhythm": ci["rhythm"]}
        if mg["moved"] or mg["dropped"]:
            about["chords"]["kept_out_of_the_idea"] = mg
    if "melody" in parts_req:
        mn, mi = melody(key, chs, g, bars, seed)
        acc = idea_chords or idea_bass or []
        if acc:
            _fit_line(mn, acc, key, chs, *(g.get("melody") or {}).get("range", [60, 84]))
        gen["melody"] = mn
        about["melody"] = mi
        lead = lead or mn
    if "counter" in parts_req:
        line = lead or []
        if not line:
            raise ValueError("counter needs a melody: give a melody idea or add 'melody' to what")
        acc = gen.get("chords") or idea_chords or []
        cm, cmi = counter_melody(line, chs, key, g, seed, bars=bars, accomp=acc)
        gen["counter"] = cm
        about["counter"] = cmi
    occ = {}
    for nm_, ns_ in (("melody", lead), ("bass", gen.get("bass") or idea_bass)):
        if ns_:
            ks = sorted(n["k"] for n in ns_)
            occ[nm_] = [ks[len(ks) // 10], ks[min(len(ks) - 1, (9 * len(ks)) // 10)]]
    for r in parts_req:
        if r in LAYER_ROLES:
            low_ = LAYER_ROLES[r]["kind"] == "low"
            fl = sorted((n["s"], n["k"]) for n in (gen.get("bass") or idea_bass or [])) if low_ else None
            ln, li = layer(r, chs, key, g, seed, 0, occ, bars, bpm, lead, follow=fl or None)
            acc = (gen.get("chords") or idea_chords or []) + [x for k_, x_ in gen.items() if k_ in LAYER_ROLES for x in x_]
            if low_:                                              # the harmony's bass: nothing to move
                pass
            elif LAYER_ROLES[r]["kind"] in ("sustain", "stabs"):  # held layers: an octave away, or left out
                cr_ = clear_rubs(ln, acc + (lead or []) + (gen.get("counter") or []), *li["register"])
                if cr_["moved"] or cr_["dropped"]:
                    li["kept_off_the_other_parts"] = cr_
            elif lead or acc:                                     # moving layers: the nearest note that fits
                _fit_line(ln, (lead or []) + acc, key, chs, *li["register"])
            gen[r] = ln
            about[r] = li
    steps["generate"] = round(time.time() - t, 3)
    # ---- feel
    so = opts.get("swing", "idea")
    swing8 = None
    if isinstance(so, (int, float)) and not isinstance(so, bool):
        swing, swsrc = float(so), "given (16ths)"
    elif so == "genre" or sw is None:
        swing, swsrc = float(g.get("swing", 0.5)), f"{g['name']}" + (" (the idea's swing can't be measured)" if sw is None and so != "genre" else "")
    elif sw_grid == 8:
        swing, swing8, swsrc = 0.5, sw, "matched to the idea (it swings its 8ths)"
    else:
        swing, swsrc = sw, "matched to the idea (16ths)"
    opts["_swing8"] = swing8
    # ---- clash check (in memory: the idea + the new parts, as the song would play)
    t = time.time()
    names = {"idea": 0, "chords": 1, "bass": 2, "counter": 3, "melody": 4}
    pn, partinfo = [], {}
    idea_role = {"melody": "melody", "chords": "chords", "bass": "bass"}.get(part)
    if idea_role:
        pn += _ticks_notes(notes, 0)
        partinfo[0] = {"name": "Idea", "role": idea_role}
    chn = 5
    for r, ns_ in gen.items():
        if r == "drums":
            continue
        ch = names.get(r)
        if ch is None:
            ch, chn = chn, chn + 1
        if r == "bass":
            ns_ = encode_glides(ns_, opts.get("glide") or "slide")
        pn += _ticks_notes(ns_, ch)
        partinfo[ch] = {"name": r.title(), "role": {"counter": "counter", "melody": "melody", "bass": "bass",
                                                    "low strings": "bass"}.get(r, "pad" if r in ("pad", "strings", "choir") else "chords")}
    clash = None
    if H.get("clashes") and pn and len(partinfo) >= 2:
        try:
            cr = H["clashes"](notes=pn, ppq=96, bpm=bpm, parts=partinfo, genre=g["name"], limit=4, **mkw)
            clash = {"counts": cr.get("counts"), "tolerance": cr.get("tolerance"), "verdict": cr.get("verdict"),
                     "top": [{k: i.get(k) for k in ("severity", "kind", "parts", "notes", "where")} for i in (cr.get("issues") or [])[:3]]}
            if any("Idea" in str(i.get("parts")) and "vs" not in str(i.get("parts")) for i in cr.get("issues") or []):
                clash["note"] = "issues naming only Idea are inside the idea itself (left untouched)"
        except Exception as e:
            clash = {"error": f"{type(e).__name__}: {e}"[:200]}
    steps["clashes"] = round(time.time() - t, 3)
    if not mt["legacy"]:
        notes_out.append(f"written in {mt['ts']} bars" + (f"; {drum_feel_note(g)}" if "drums" in gen else ""))
    res = {"idea": {"part": part, "bars": bars, "notes": len(notes), "bpm": bpm, "key": key,
                    "swing": sw, "source": src or None},
           "genre": g["name"], "chords": [c.get("name") for c in chs][:16], "chords_from": csrc,
           "parts": {r: about.get(r, {}) for r in gen},
           "swing": {"value": swing8 or swing, "grid": "8ths" if swing8 else "16ths", "from": swsrc}}
    if fit.get("strong_beat_chord_tones") is not None:
        res["idea_fit"] = {"strong_beat_chord_tones": fit["strong_beat_chord_tones"]}
    if clash:
        res["clashes"] = clash
    if guesses:
        res["guesses"] = guesses
    if notes_out:
        res["notes"] = notes_out
    if not gen:
        res["nothing_generated"] = "every part asked for is the idea itself"
    # ---- write
    if project:
        _write_around(project, res, notes, gen, chs, g, key, bpm, swing, part, bars, seed, opts, steps)
    elif opts.get("output_mid"):
        res["output_mid"] = _around_mid(opts["output_mid"], notes, part, gen, bpm, swing, g, opts)
        res["next"] = "import the .mid in FL (drag it into the playlist / channel rack) and listen - a human should judge it"
    else:
        lim = int(opts.get("limit") or 48)
        res["parts_notes"] = {r: (_inline(ns_, lim) if r != "drums" else
                                  [[round(1 + h["s"] / _spb(), 4), h["part"], int(h["v"])] for h in ns_][:lim])
                              for r, ns_ in gen.items()}
        res["how"] = ("nothing written: give project=<.flp> to write the parts into FL (one batch), or "
                      "output_mid=<.mid>; parts_notes rows are [bar, length_bars, pitch, vel(, 1 = 808 slide)]")
    res["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
    return res


def _around_mid(path, notes, part, gen, bpm, swing, g, opts):
    """Idea + parts as a type-1 .mid (drums on channel 10 with GM keys)."""
    H["writable"](path)
    if not str(path).lower().endswith((".mid", ".midi")):
        raise ValueError("output_mid must end in .mid")
    div = 480
    tracks = [("tempo", [(0, b"\xff\x51\x03" + int(round(60_000_000 / bpm)).to_bytes(3, "big")),
                         (0, b"\xff\x58\x04\x04\x02\x18\x08")])]

    def track(name, rows, mch):
        ev = []
        for _, beat, beats, k, v, _f in rows:
            s = int(round(beat * div)); e = max(s + 1, int(round((beat + beats) * div)))
            ev.append((s, bytes([0x90 | mch, int(k) & 127, int(v)])))
            ev.append((e, bytes([0x80 | mch, int(k) & 127, 0])))
        tracks.append((name, ev))
    track("Idea", _rows([dict(n, nos=True) for n in notes], 0, None, g, bpm, 1, humanize=False), 0 if part != "drums" else 9)
    for i, (r, ns_) in enumerate(gen.items()):
        if r == "drums":
            rows = []
            for h in ns_:
                rows += _rows([h], 9, GM_DRUMS.get(h["part"], 37), g, bpm, 1, swing=swing, swing8=opts.get("_swing8"))
            track("Drums", rows, 9)
        else:
            src = encode_glides(ns_, "overlap") if r == "bass" else ns_
            track(r.title(), _rows(src, i + 1, None, g, bpm, 1, swing=swing, slide=False, swing8=opts.get("_swing8")),
                  (i + 1) % 9)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    H["midi_write"](path, div, tracks)
    res = {"path": path, "tracks": [t_[0] for t_ in tracks[1:]]}
    if any(n.get("glide") for n in gen.get("bass") or []):
        res["note"] = "808 slides written as overlapping notes (set the bass to mono + porta)"
    return res


def _write_around(project, res, notes, gen, chs, g, key, bpm, swing, part, bars, seed, opts, steps):
    """Write the idea (unless it is already in the project) and the new parts as '<name> <Part>' patterns at the
    idea's bar, ONE flp_edit batch, then read back."""
    t = time.time()
    _fresh(project)
    v = project_view(project)
    steps["read"] = round(time.time() - t, 3)
    mt = _meter()
    at0 = float(opts.get("at_bar") or 1.0)
    (pn_, pd_), _ = _meter_at(v, at0)
    if (pn_, pd_) != (mt["num"], mt["den"]) and pn_ * 16 // pd_ != mt["spb"]:
        raise ValueError(f"the idea is in {mt['ts']} but the project is in {pn_}/{pd_} there: pass the idea's "
                         f"time_signature='{pn_}/{pd_}' (or capture it in the project's meter)")
    base = str(opts.get("name") or "Idea").strip()
    chan_opt = opts.get("channel") if isinstance(opts.get("channel"), dict) else {}
    plan_new, chmap, taken = [], {}, set()
    drum_map, missing = {}, []
    if "drums" in gen:
        drum_map, missing = _drum_map(v, sorted({h["part"] for h in gen["drums"]}),
                                      {p: c for p, c in chan_opt.items() if p in DRUM_PARTS} or None)
        if not drum_map:
            res.setdefault("notes", []).append("drums skipped - no drum channels: add kick/snare/hat sample channels "
                                               "(or a kit plugin like FPC) in FL")
            gen.pop("drums")
        elif missing:
            res.setdefault("notes", []).append(f"no channel for {', '.join(missing)}: those hits were left out")
        taken |= {c for c, _, _ in drum_map.values()}
    existing = _find_idea_pattern(v, notes) if opts.get("write_idea", True) is not False else None
    write_idea = opts.get("write_idea", True) is not False and existing is None
    idea_role = {"melody": "melody", "chords": "chords", "bass": "bass", "drums": None}[part]
    if existing:
        taken.add(existing["channel"])
    idea_ch = None
    if write_idea and idea_role:
        idea_ch, _ = _channel_for(v, idea_role, opts.get("idea_channel", chan_opt.get("idea")), taken, plan_new, g)
        taken.add(idea_ch)
    elif write_idea and part == "drums":
        write_idea = False
        res.setdefault("notes", []).append("a drum idea isn't copied into the project (write it with audio_to_midi / "
                                           "fl_capture_idea into your kit); the parts follow its kicks")
    for r in gen:
        if r == "drums":
            continue
        role = "counter" if r == "counter" else r
        ch, _ = _channel_for(v, role, chan_opt.get(r), taken, plan_new, g)
        chmap[r] = ch
        taken.add(ch)
    at = opts.get("at_bar")
    if at is None:
        at = existing["bars"][0] if existing and existing.get("bars") else (1.0 if not v["clips"] else float(v["end_bar"]))
    at = float(at)
    reg = v["registry"]
    ft = v["free_track"]
    order = (["idea"] if write_idea else []) + list(gen)
    tracks = {r: int((reg.get("tracks") or {}).get(r) or 0) or ft + j for j, r in enumerate(order)}
    ops = _clone_ops(v, plan_new, {p["channel"]: "Idea" for p in plan_new
                                   if p["role"] == idea_role and p["channel"] == idea_ch})
    if v["empty"] and abs(v["tempo"] - bpm) > 0.01:
        ops.append({"tool": "flp_set_tempo", "bpm": bpm})
        res.setdefault("notes", []).append(f"project tempo set to the idea's {bpm:g} BPM (the project was empty)")
    elif abs(v["tempo"] - bpm) > 1:
        res.setdefault("notes", []).append(f"the project plays at {v['tempo']:g} BPM, the idea was at {bpm:g}: the parts are "
                                           "written in bars, so they follow the project tempo")
    starts = _clip_starts(v)
    written = []

    def add(role, pname, rows, clear, track):
        have = starts.get(pname.lower(), set())
        op = {"tool": "flp_compose_write", "pattern": pname, "notes": rows, "clear_channels": clear, "track": track,
              "length_bars": bars}
        if round(at, 3) not in have:
            op["place"] = [[round(at, 3), bars]]; op["place_mode"] = "add"
        ops.append(op)
        written.append({"role": role, "pattern": pname, "notes": len(rows), "channels": clear})
    offs = v["offsets"]                                    # pitched samplers: notes go in at their written pitch
    if write_idea and idea_ch is not None:
        add("idea", _pname(base, "Idea") if base.lower() != "idea" else "Idea",
            written_rows(_rows([dict(n, nos=True) for n in notes], idea_ch, None, g, bpm, seed, humanize=False), offs),
            [idea_ch], tracks["idea"])
    for r, ns_ in gen.items():
        if r == "drums":
            rows = []
            for p_, (ch, k, kit) in sorted(drum_map.items()):
                rows += _rows([h if not kit else dict(h, dk=0) for h in ns_ if h["part"] == p_], ch, k, g, bpm, seed,
                              swing=swing, swing8=opts.get("_swing8"))
            add("drums", _pname(base, "Drums"), rows, sorted({c for c, _, _ in drum_map.values()}), tracks[r])
        else:
            src = encode_glides(ns_, opts.get("glide") or "slide") if r == "bass" else ns_
            strum = float((g.get("harmony") or {}).get("strum_ms", 0)) if r == "chords" else 0.0
            add(r, _pname(base, _part_name(r, g)), written_rows(_rows(src, chmap[r], None, g, bpm, seed, strum_ms=strum,
                                                                      swing=swing, swing8=opts.get("_swing8")), offs),
                [chmap[r]], tracks[r])
    res["written"] = [{k: w[k] for k in ("pattern", "notes")} for w in written]
    res["channels"] = dict(chmap, **({"idea": idea_ch} if idea_ch is not None else {}))
    if drum_map:
        res["drum_channels"] = {p: c for p, (c, _, _) in drum_map.items()}
    res["idea"]["in_project"] = (f"already in pattern '{existing['name']}' (left as is)" if existing else
                                 f"written to pattern '{_pname(base, 'Idea') if base.lower() != 'idea' else 'Idea'}'"
                                 if write_idea and idea_ch is not None else "not written")
    if plan_new:
        res["new_channels"] = [{"channel": p["channel"], "role": p["role"], "name": p["name"], "sound_needed": p["sound_hint"],
                                "next": flh_score.call_hint("fl_audition", {"want": p["sound_hint"], "channel": p["channel"]}, H)}
                               for p in plan_new]
    res["at_bar"] = at
    if opts.get("dry_run"):
        res["dry_run"] = True
        res["would_write"] = res.pop("written")
        return
    er = _edit(project, ops, steps)
    ok, bad = _verify(project, written)
    res["verified"] = ok
    if bad:
        res["verify_problems"] = bad
    _queued(res, er)
    _inserts_made(er, plan_new, res)
    comp = {"channels": dict(chmap), "tracks": tracks, "blocks": {base: {
        "parts": {w_["role"]: w_["pattern"] for w_ in written},
        "bars": bars, "seed": seed, "what": "around", "idea_part": part,
        "chords": [[c["start"], c["len"], c.get("name")] for c in chs]}}}
    if not reg.get("genre"):
        comp.update(genre=g["name"], key=key, tempo=bpm)
    if drum_map:
        comp["drum_map"] = {p: [c, k, kit] for p, (c, k, kit) in drum_map.items()}
    jset = {"compose": comp, "channels": {str(c): {"role": JOURNAL_ROLE.get(r, r)} for r, c in chmap.items()}}
    for p in plan_new:
        if p.get("insert"):
            jset["channels"].setdefault(str(p["channel"]), {})["mixer"] = p["insert"]
    if idea_ch is not None:
        jset["channels"].setdefault(str(idea_ch), {}).update({"role": idea_role, "name": "Idea",
                                                              "melody": idea_role == "melody"})
    _journal_set(project, jset, res)
    res["next"] = f"fl_listen(start_bar={at:g}, end_bar={at + bars:g}) - a human should listen"


# ============================================================================= flh_genres
def flh_genres(genre=None, full=False):
    """List the genre data (or one genre: its tempo, keys, progressions, form, roles)."""
    if not genre:
        out = []
        for k, g in sorted(genres().items()):
            if k.startswith("_"):
                continue
            t = g.get("tempo") or {}
            out.append({"genre": k, "bpm": f"{t.get('min')}-{t.get('max')}", "feel": t.get("feel"),
                        "aliases": (g.get("aliases") or [])[:4]})
        res = {"genres": out, "count": len(out), "add_your_own": os.path.join(_data_dirs("genres")[0], "<name>.json")}
        if genres().get("_errors"):
            res["errors"] = genres()["_errors"]
        return res
    g = genre_data(genre)
    if full:
        return {k: v for k, v in g.items() if not k.startswith("_")}
    h = g.get("harmony") or {}
    return {"genre": g["name"], "about": g.get("about"), "tempo": g.get("tempo"), "swing": g.get("swing"),
            "keys": g.get("keys"), "progressions": [" ".join(p["rn"]) for p in h.get("progressions") or []],
            "voicing": (h.get("voicing") or {}).get("style"), "bass": (g.get("bass") or {}).get("type"),
            "form": g.get("form", {}).get("default"), "roles": {r: x.get("hint") for r, x in (g.get("roles") or {}).items()},
            "mix": g.get("mix")}


def compose_beat(genre, key, bars=8, seed=1, vary=0, progression=None, motif=None, style=None, variety=1.0,
                 level="full"):
    """A whole beat in the current meter, the way flp_compose(what='beat') writes it: drums -> chords -> bass on
    the kick -> keys out of the bass's way -> the hook melody (best of a few candidates, scored against the chord
    part) -> keys out of the lead's way -> a musicality score. Pure and deterministic per seed.
    -> {parts {drums, chords, bass, melody}, chords, summary, musicality, steps_s}"""
    T = time.time()
    g = genre_data(genre)
    parts, summ = {}, {}
    hits = drums(g, bars, variety, seed, level, vary)
    parts["drums"] = hits
    summ["drums"] = _summ_drums(hits, bars)
    chs, hinfo = harmony(key, g, bars, progression, seed, vary)
    key = hinfo.pop("key", key)                           # a minor-only genre in a major key: all parts in its minor
    summ.update(hinfo)
    cn, cinfo = chord_part(chs, g, key, seed, vary, style)
    parts["chords"] = cn
    summ["voicing"] = cinfo["style"]
    bn, binfo = bassline(chs, g, [h["s"] for h in hits if h["part"] == "kick"], seed, vary, bars, key)
    parts["bass"] = bn
    summ["bass"] = dict(binfo, notes=len(bn))
    vreg = ((g.get("harmony") or {}).get("voicing") or {}).get("register") or [52, 74]
    mud_guard(cn, bn, int(vreg[1]) + 7, chs)
    t_m = time.time()
    mn, minfo = melody(key, chs, g, bars, seed, vary, motif, accomp=cn)
    parts["melody"] = mn
    summ["melody"] = minfo
    t_m = time.time() - t_m
    mg = melody_guard(cn, mn, int(vreg[0]))
    if mg["moved"] or mg["dropped"]:
        summ["voicing_fixes"] = mg
    mus = fl_musicality.compact(musicality(parts, chs, g, bars))
    return {"parts": parts, "chords": chs, "summary": summ, "musicality": mus, "key": key,
            "steps_s": {"melody": round(t_m, 4), "total": round(time.time() - T, 4)}}


# ============================================================================= musicality (fl_musicality)
def bass_lock_of(g):
    """How the genre's bass relates to its kick: kick (locks to it) | offbeat (between the kicks: house pumps) |
    walk (on the beats). Data: bass.lock; else from bass.rhythm."""
    b = (genre_data(g) if isinstance(g, str) else g).get("bass") or {}
    if b.get("lock") in ("kick", "offbeat", "walk", "free"):
        return b["lock"]
    return "walk" if b.get("rhythm") == "walk" else "kick"


HOOK_KINDS = ("hook", "chorus", "drop", "climax")


def musicality(parts, chords, g, bars, sections=None, full=False, hook=True):
    """fl_musicality.score_beat for parts in the current meter (the genre's bass lock, the meter's strong beats)."""
    mt = _meter()
    return fl_musicality.score_beat(parts, chords, mt["spb"], tuple(mt["strong"]), int(bars), bass_lock_of(g),
                                    mt["pulse"], sections=sections, full=full, hook=hook)


def _ctx_parts(ctx, roles):
    """song_context notes -> {drums, bass, chords, melody, counter} for the scorer."""
    parts = {"drums": [], "bass": [], "chords": [], "melody": list(ctx.get("melody") or []), "counter": []}
    for ch, ns_ in (ctx.get("notes") or {}).items():
        r = roles.get(ch)
        if ch == ctx.get("melody_channel"):
            continue
        if _is_drum(r):
            parts["drums"] += [dict(n, part=r) for n in ns_]
        elif r == "bass":
            parts["bass"] += ns_
        elif r in HARMONIC_ROLES:
            parts["chords"] += ns_
        elif r in ("counter",):
            parts["counter"] += ns_
    return parts


@_reads_fresh
@_meter_scope
def flp_musicality(project, section=None, genre=None, key=None, full=False):
    """Score the music already in a project the way flp_compose scores its candidates (fl_musicality): per section
    and for the song (sections that build / contrast). Reads only."""
    T0 = time.time()
    _fresh(project)
    v = project_view(project)
    g, gsrc = _resolve_genre(genre, v)
    key, ksrc = _resolve_key(key, v, g)
    secs = []
    for i, x in enumerate(v["sections"] or []):
        nxt = (v["sections"][i + 1]["bar"] if i + 1 < len(v["sections"]) else None)
        end = x.get("end_bar") or nxt or v["end_bar"]
        secs.append({"name": x["name"], "bar": float(x["bar"]), "bars": max(1, int(round(float(end) - float(x["bar"]))))})
    if not secs:
        secs = [{"name": "Song", "bar": 1.0, "bars": max(1, int(v["end_bar"]) - 1)}]
    if section:
        want = [x for x in secs if x["name"].lower() == str(section).lower()]
        if not want:
            raise ValueError(f"no section '{section}' (have {sorted({x['name'] for x in secs})})")
        secs = want
    rows, song = [], []
    for x in secs:
        (mn_, md_), _ = _meter_at(v, x["bar"])
        _set_meter((mn_, md_))
        nb = min(x["bars"], 64)
        ctx = song_context(v, x["bar"], nb, key)
        parts = _ctx_parts(ctx, v["roles"])
        if not any(parts.values()):
            rows.append({"section": x["name"], "bar": x["bar"], "score": None, "note": "silent"})
            song.append({"name": x["name"], "bars": nb, "parts": {}, "kind": _section_spec(g, x["name"]).get("role")})
            continue
        kind_ = str(_section_spec(g, x["name"]).get("role") or x["name"]).lower().rstrip("0123456789 ")
        carries = len(secs) == 1 or kind_ in HOOK_KINDS or x["name"] == "Song"   # where the hook is expected
        sc = musicality(parts, ctx["chords"], g, nb, full=full, hook=carries)
        row = dict({"section": x["name"], "bar": x["bar"]}, **fl_musicality.compact(sc))
        if sc.get("note"):
            row["note"] = sc["note"]
        if full and sc.get("detail"):
            row["detail"] = sc["detail"]
        rows.append(row)
        song.append({"name": x["name"], "bars": nb, "parts": {k: v_ for k, v_ in parts.items() if v_},
                     "kind": _section_spec(g, x["name"]).get("role"),
                     "roots": [chord_at(ctx["chords"], b_ * _spb())["root"] for b_ in range(nb)] if ctx["chords"] else []})
    form = fl_musicality.section_metric(song, _meter()["spb"]) if len(song) > 1 and not section else None
    scored = [(r["score"], s_["bars"]) for r, s_ in zip(rows, song) if r.get("score") is not None]
    tot = round(sum(a * b for a, b in scored) / float(sum(b for _, b in scored))) if scored else None
    if len(rows) == 1:
        mus = {k: x for k, x in rows[0].items() if k not in ("section", "bar")}
        res = {"musicality": mus}
    else:
        res = {"musicality": {"score": tot}, "sections": rows}
    if form is not None:
        res["musicality"]["song_form"] = round(form, 2)
    res.update(genre=g["name"], key=key)
    guesses = [f"genre {g['name']}: {gsrc}"] if gsrc != "given" else []
    if ksrc != "given":
        guesses.append(f"key {key}: {ksrc}")
    if guesses:
        res["guesses"] = guesses
    res["note"] = ("craft rules measured on the notes (motif, chord tones, steps, cadences, bass on the kick, "
                   "sections that build) - not the sound: a human should listen")
    res["steps_s"] = {"total": round(time.time() - T0, 3)}
    return res


# ============================================================================= register
def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    STR, INT, NUM, BOOL, FLP = ns["_STR"], ns["_INT"], ns["_NUM"], ns["_BOOL"], ns["_FLP"]
    guard = ns["_guard_open_project"]
    ns["TOOLS"]["flp_compose_write"] = (
        guard(flp_compose_write, "project", "output"),
        "Low-level writer the composer batches through flp_edit: notes into ONE named pattern (made if missing) as "
        "[channel, beat, beats, pitch, vel, flags] (beats from the pattern start; flags 1 = FL slide note), "
        "clear_channels first, place=[bar | [bar, length_bars]] on track, or transform={ops, channels} (flp_adjust "
        "ops applied to the pattern's notes at write time).",
        S(project=FLP, output=STR("save as (inside FLH_ROOTS)"), pattern=P(["integer", "string"], "pattern name or id"),
          notes=P("array", "[[channel, beat, beats, pitch, velocity, flags]]", True, items={"type": "array"}),
          place=P("array", "bars or [bar, length_bars]", True, items={}), track=INT("playlist track", True),
          clear_channels=P("array", "channels to clear in the pattern first", True, items={"type": "integer"}),
          transform=P("object", "{ops: [...], channels: {ch: {role, drum}}, key, tempo, seed}", True),
          length_bars=NUM("clip length when place gives bars only", True),
          place_mode=STR("pattern already placed: move (default: its clip moves/resizes, no second copy) | add "
                         "(another clip, a repeat) | replace (its clips go first)", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_compose_write"] = ("project", "output")
    ns["TOOLS"]["flp_compose_clear"] = (
        guard(flp_compose_clear, "project", "output"),
        "Low-level remover the composer batches through flp_edit: pattern clips by what they are - clips=[[pattern "
        "id or name, bar, track], ...] and/or names=[...] overlapping from_bar..to_bar - found again in the file when "
        "the edit runs (stable across FL's saves, which renumber clip ids). Clips no longer there are left alone.",
        S(project=FLP, output=STR("save as (inside FLH_ROOTS)", True),
          clips=P("array", "[[pattern, bar, track], ...]", True, items={"type": "array"}),
          names=P("array", "pattern names", True, items={"type": "string"}),
          from_bar=NUM("with names: from this bar", True), to_bar=NUM("with names: up to this bar", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_compose_clear"] = ("project", "output")
    ns["TOOLS"]["flp_compose_insert"] = (
        guard(flp_compose_insert, "project", "output"),
        "Low-level bar insert the song extender batches through flp_edit: `bars` empty bars at song bar `at` in any "
        "meter (bars of `meter`, default the one before `at`); clips crossing it split, later clips, tempo maps and "
        "playlist markers (time-signature markers too) move later.",
        S(project=FLP, output=STR("save as (inside FLH_ROOTS)", True), at=NUM("song bar (1-based)"),
          bars=NUM("bars to insert"), meter=STR("e.g. '3/4' (default: the meter before `at`)", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_compose_insert"] = ("project", "output")
    ns["TOOLS"]["flp_compose_fx"] = (
        guard(flp_compose_fx, "project", "output"),
        "Low-level writer flp_arrange_song batches through flp_edit for its audio FX: sampler channels "
        "[{name, sample, channel (reuse) | expect (new index), color, kind}] that play the whole WAV (volume envelope "
        "off), all into one mixer insert (insert='FX': found by name, else a free one, named, colored, routed to bus).",
        S(project=FLP, output=STR("save as (inside FLH_ROOTS)"),
          channels=P("array", "[{name, sample, channel?, expect?, color?, kind?}]", items={"type": "object"}),
          insert=STR("mixer insert name (default FX)", True), color=STR("insert color", True),
          level_db=NUM("a new insert's fader", True), bus=INT("route a new insert to this mixer track", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_compose_fx"] = ("project", "output")
    ns["TOOLS"]["flp_compose_drop"] = (
        guard(flp_compose_drop, "project", "output"),
        "Low-level writer flp_remove_fx batches through flp_edit: remove the named patterns (their notes too) that no "
        "arrangement places any more; one still placed somewhere is kept and said (unplace=true: its clips go too, "
        "in every arrangement).",
        S(project=FLP, output=STR("save as (inside FLH_ROOTS)", True),
          patterns=P("array", "pattern names (or ids)", items={"type": ["string", "integer"]}),
          unplace=BOOL("also remove their playlist clips (default false: placed patterns are kept)", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_compose_drop"] = ("project", "output")
    ns["TOOLS"]["flp_compose_layer_inserts"] = (
        guard(flp_compose_layer_inserts, "project", "output"),
        "Low-level writer the composer batches after cloning channels for new parts: each channel [{channel, name, "
        "color}] gets its own free mixer insert (not the insert of the channel it was copied from), named and colored "
        "after it and routed to the Music bus (bus=<name>, found by name) instead of the Master.",
        S(project=FLP, output=STR("save as (inside FLH_ROOTS)"),
          channels=P("array", "[{channel, name, color?}]", items={"type": "object"}),
          bus=STR("bus track name (default Music: 'Music Bus' or 'Music')", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_compose_layer_inserts"] = ("project", "output")
    ns["TOOLS"]["flp_remove_fx"] = (
        flp_remove_fx,
        "Take the arrangement's ear candy out again in ONE reversible batch - only what fl-headless made (song "
        "journal, the names it uses, the _fx folder): the generated FX sampler channels with their patterns and clips, "
        "snare builds, the organic moves (notes it wrote on your strings / timpani / choir / piano), the delay-throw, "
        "filter and swell automation clips and the plugins it loaded for them, the FX / Texture inserts it named. "
        "kinds: all (default) or kinds / groups: audio | beds | organic | synthetic | snare_build | delay_throw | "
        "filter | riser | wind | string_swell | piano_pickup ... dry_run=true lists what would go and the unused _fx "
        "WAVs; recycle_files=true moves those WAVs to the Recycle Bin; undo=true puts the song and its journal back. "
        "Removing all remembers 'no ear candy' for the next arrangement (remember=false doesn't).",
        S(project=FLP, kinds=P(["string", "array"], "all (default) or kinds / groups", True, items={"type": "string"}),
          dry_run=BOOL("list what would go, write nothing", True),
          recycle_files=BOOL("also move the _fx WAVs nothing uses any more to the Recycle Bin", True),
          undo=BOOL("put back the song and journal from before the last flp_remove_fx", True),
          remember=BOOL("removing all: the next arrangement adds no ear candy (default true)", True)))
    if isinstance(ns.get("NOT_READERS"), set):
        ns["NOT_READERS"].add("flp_remove_fx")
    for k_ in ("flp_remove_fx", "flp_compose_drop"):
        try:
            ns["TOOL_GROUPS"][k_] = "compose"
        except Exception:
            pass
    try:
        ns["SHORT_DESC"]["flp_remove_fx"] = (
            "Remove the arrangement's ear candy (generated FX channels, patterns, clips, FX insert; organic moves) in "
            "one reversible batch; kinds= to pick, dry_run=true lists, undo=true puts it back.")
        ns["LONG_HELP"]["flp_remove_fx"] = (
            "Typical: flp_remove_fx(project, dry_run=true) -> what would go; flp_remove_fx(project) removes all and "
            "offers the unused _fx WAVs (recycle_files=true); kinds=['wind', 'reverse_swell'] only those; "
            "kinds='organic' the real-instrument moves; undo=true restores the song and journal.")
    except Exception:
        pass
    if isinstance(ns.get("LAZY_TOOLS"), set):
        ns["LAZY_TOOLS"].add("flp_compose_clear")           # selects by content, so it can wait in the lazy queue
    ns["realism_for_genre"] = realism_for_genre
    ns["compose_around"] = compose_around                 # flh_transcribe's capture_and_build hands ideas here
    ns["TOOLS"]["flp_compose"] = (
        flp_compose,
        "Compose real parts from genre knowledge (data/genres) and write them in ONE batch (reversible: flp_versions). "
        "what: beat (drums + chords + bass/808 + melody into the right channels, new patterns '<Section> Drums/Chords/"
        "808/Melody') | drums | chords | bass (follows the song's chords and kick; 808 genres glide with FL slide "
        "notes) | melody | counter (answers the existing melody) | layer (role pad|strings|low strings|choir|organ|arp|"
        "pluck|bell|brass|counter, or roles=[...] for several in one batch; follows the song's chords and bass in a "
        "free register) | voicing (re-voice a chord part, smooth voice leading, genre voicing) | pick (keep one "
        "audition variation). Any time signature (bars follow the project's meter; drums without genre patterns for it "
        "use a generic feel and say so). variations=3 writes A/B/C side by side after the song (with the section's "
        "other parts copied under each) - fl_listen each, then what='pick'. Missing channels are cloned and named, "
        "with a sound hint to load. Deterministic per seed; says what it guessed (genre, key).",
        S(project=FLP, what=STR("beat | drums | chords | bass | melody | counter | layer | voicing | pick"),
          genre=STR("trap, boombap, drill-uk, drill-ny, lofi, rnb, pop, house, techno, dnb, afrobeats, reggaeton, "
                    "ambient, cinematic, rock, jazzhop, jersey-club, phonk, hyperpop, lofi-house, synthwave, dancehall, "
                    "amapiano, gospel, kpop (k-pop / j-pop), metal (aliases ok; flh_genres lists)", True),
          key=STR("e.g. 'C# minor' (default: the song's key, else the genre's)", True),
          section=STR("section name (Hook, Verse...): where it goes and what it follows", True),
          bars=P(["integer", "string"], "length in bars (default: the section / loop); 'all' = the whole song", True),
          channel=P(["integer", "object"], "target channel, or {role|drum part: channel} for beat/drums", True),
          variations=INT("1-6 alternatives as separate patterns (default 1)", True), seed=INT("", True),
          dry_run=BOOL("plan and describe, write nothing", True),
          progression=P(["string", "array"], "'i VI III VII', 'Am F C G', ['i:2','VI:2']", True),
          motif=P("array", "melody motif [[step16, len16, pitch], ...] to build from", True, items={}),
          role=STR("for what=layer", True), source=INT("melody channel a counter answers", True),
          roles=P("array", "layer: several roles, one channel each: names or {role, register, vel, where}",
                  True, items={}),
          glide=STR("slide (FL slide notes, default) | overlap (mono + Porta)", True),
          level=STR("drums: full|light|sparse|half|build; layer: support (faint)|normal", True),
          style=STR("voicing style: triad seventh extended rootless shell power open", True),
          variation=STR("for what=pick: A, B, C...", True), tempo=NUM("set the project tempo", True),
          variety=NUM("0..2 how much random variation in drums (default 1)", True),
          full=BOOL("per-variation detail and a drum grid", True),
          realism=BOOL("play strings / choir / pad / brass / woodwind / bells parts like a person (flh_realism): "
                       "default on for layer and counter, off otherwise; false skips", True)))
    ns["TOOLS"]["flp_arrange_song"] = (
        flp_arrange_song,
        "Arrange composed parts into a song in ONE batch: the genre's form (or form='Intro:8, Hook:16, Verse:16') with "
        "its energy curve (which layers play where; light/sparse/build drums generated per section), transitions "
        "(drum fills, drop-outs and a silent last beat before hooks) with ear candy: audio FX made for the song's "
        "tempo and key (riser / reverse crash ending on a hook's downbeat, impact / sub drop / downlifter starting on "
        "it, noise sweeps, cymbal / reverse-chord swells, a soft boom, a tape stop at the end - per genre, on sampler "
        "channels into an FX insert under the beat), a quiet texture bed (wind / rain / vinyl) under intro / outro / "
        "breakdown / bridge, a snare build into hooks and drops, delay throws on the lead's last note before a change "
        "and filter moves (intro opens, a high-pass build before the last hook, outro closes: Fruity Filter "
        "automation) - only where they have a job (one riser and one big hit per change, nothing inside a section; "
        "ear_candy='subtle'|'normal'|'lots'). Piano-led, cinematic, ambient songs and collections whose profile wants "
        "the support real / faint get ear_candy='organic' instead: real-instrument transitions written as notes on "
        "channels the song has (a string swell, a soft timpani or suspended-cymbal roll, a choir breath into the "
        "climax, a piano pickup into a new section; a move whose instrument is missing is skipped and the owned one "
        "that would play it named; no generated audio). 'synthetic' forces the FX, 'off' none; flp_remove_fx takes "
        "them out again. Builds "
        "(volume automation when FL tells the mixer insert). The result's transitions list says what went where and "
        "what each effect points at (purpose). "
        "mode build (empty bars) | replace (re-lay the composed parts) | "
        "extend (append). extend={section, bars, variation} lengthens a section (later bars move, drums varied). "
        "Any time signature: bars follow the project's meter map (fills, drop-outs and generated drums in each "
        "section's meter; a section in another meter than its material is said). Writes the sections into the song "
        "journal.",
        S(project=FLP, form=P(["string", "array"], "section list or a form name from the genre", True),
          genre=STR("", True), mode=STR("build | replace | extend", True), start_bar=NUM("", True),
          transitions=BOOL("default true", True), builds=BOOL("default true", True),
          extend=P(["object", "string"], "{section, bars, occurrence, variation}", True),
          block=STR("which composed block is the main material", True), seed=INT("", True),
          dry_run=BOOL("plan only", True),
          ear_candy=P(["boolean", "string"], "off | organic | subtle | normal | lots | synthetic ('organic:normal' "
                      "sets both): organic = real-instrument moves as notes on the song's channels; the amounts set "
                      "how much (audio FX, texture bed, snare build, delay throws, filter moves in synthetic) - "
                      "default: the song journal's / collection profile's choice, else organic for piano-led / "
                      "cinematic / ambient songs, else the genre's (data/ear_candy.json); off = only the drum "
                      "transitions", True)))
    ns["TOOLS"]["flp_adjust"] = (
        flp_adjust,
        "Plain-English changes: 'bounce', 'darker', 'brighter', 'harder', 'softer', 'emptier', 'busier', 'more human', "
        "'tighter', 'wider', 'warmer', 'dustier', 'epic', 'chill', 'sad', 'happy', 'aggressive' (+ 'a bit', 'much', "
        "'less ...'; data/adjectives.json). Note ops (swing, accents, density, register, voicing, mode) on the song's "
        "patterns in ONE batch, mix intents to the mix engineer, before/after measured, honest about taste.",
        S(project=FLP, words=STR("what to change, in plain words"), section=STR("only this section", True),
          dry_run=BOOL("show predicted before/after, write nothing", True), seed=INT("", True),
          channels=P("array", "only these channels", True, items={"type": "integer"}),
          mix_mode=STR("propose (default: the mix engineer suggests moves) | apply | off", True)))
    ns["TOOLS"]["flp_musicality"] = (
        flp_musicality,
        "Score the music in a project like the composer scores its own candidates: per section and for the song - "
        "{score 0-100, hook (motif, repetition vs variation, singable), fit (chord tones, rubs), groove (bass on the "
        "kick, roots on changes, drums settle), form (cadences, phrase structure, fills, sections that build), weak}. "
        "Measures the notes, not the sound. Reads only.",
        S(project=FLP, section=STR("only this section", True), genre=STR("", True), key=STR("", True),
          full=BOOL("every sub-score", True)))
    ns["TOOLS"]["flh_genres"] = (
        flh_genres, "The composer's genre knowledge: list genres, or one genre's tempo, keys, progressions, form, sound "
                    "roles and mix hints (full=true: the whole data file).",
        S(genre=STR("", True), full=BOOL("", True)))
