"""fl-headless performance: a song's whole "performance" (tempo map, rubato, dynamics, humanization, swing, legato,
doublings, pauses, booms, final ring) as DATA in the song's journal, rendered from a clean source MIDI in one call.
Section tweaks ("Chorus 3% slower", "less humanization in the Verse", "Drop louder") are one flp_perform call: the
spec is edited, re-rendered (well under a second) and only what changed is written to the project in ONE flp_edit
batch (flp_replace_part per changed part + flp_tempo_write when the tempo map changed).
Extension contract: see flh_store.

Two ways in:
  flp_perform_setup(project)                     AUTO: reads the project itself (any genre, tempo, meter): parts =
                                                 the instrument channels with notes (roles detected), source = the
                                                 project's current notes (snapped to their grid if they already look
                                                 humanized; the original stays as the first history entry), sections
                                                 = journal sections, else playlist markers, else section pattern
                                                 clips, else 8-bar blocks; meter and tempo map from the project.
                                                 style="none" is an exact identity: nothing in the song moves.
  flp_perform_setup(project, spec_file=...)      a hand-written spec (JSON, see normalize())

Styles (STYLES below; data only - a style just fills spec values, every value stays editable per section):
  none        identity: the notes exactly as they are (also: no quantize, no tempo map)
  tight       pop / EDM / hip-hop: velocity variation, near-zero timing
  natural     default: light human timing and velocity, phrase-level dynamic swells, meter-aware accents
  expressive  classical / solo piano: rubato through the tempo map (phrase breathing, broadening into section ends,
              final ritardando), dynamic swells, melody voiced over the accompaniment, melody legato
  swing       swung 8ths (options={"swing": 0.62, "swing_grid": 16} for 16ths; 0.5 straight, 0.667 triplet)
  laid-back   melodic parts a little late against a tight rhythm section (options={"late": 14,
              "late_parts": ["Rhodes"]} picks the parts and how late, in ticks at 480 PPQ)
Genre safety: drum parts never get pitch logic (lift, slope, doublings, pedal, legato, thinning) and their timing is
only what a spec names for "drums" (or the part) - never the "*" default - clipped to humanize.drum_clip; tempo
shaping is a global tempo map, so a groove bends as one. "none"/"tight"/"natural"/"swing"/"laid-back" write no tempo
map. Songs with audio clips: tempo maps are not written (audio would drift) unless allow_tempo_with_audio=True.

Spec (journal["performance"], JSON). Engine defaults are neutral (a spec with nothing but parts + sections plays
the source exactly as written); a spec only lists what differs.
  source        source .mid (relative = the project's folder). Notes stay in source ticks: humanization and note
                lengths are tick offsets, so a tempo-only change rewrites only the tempo map.
  tick_ppq      the PPQ the spec's tick values are written in (auto specs: 480, scaled to the source's PPQ when
                rendering, so a spec reads the same at 96 or 960 PPQ). Absent: values are source ticks.
  first_bar     number of the source's first bar (-3 = a 4-bar intro numbered -3..0). Bars follow the source's
                time signatures (3/4, 6/8, 7/8 and changes); "bars": [[tick, length], ...] overrides them. All bar
                positions in the spec use this numbering (bar 27.5 = halfway through bar 27).
  song_start_bar  FL song bar where source tick 0 lands (default 1).   tempo_pattern  FL tempo lane (Tempo).
  base_bpm      "source" (default): the source's own tempo map times the spec's shaping (an unshaped spec leaves
                the song's tempo alone); a number: the tempo at stretch 1.0 (the map is generated from scratch).
  allow_tempo_with_audio  write tempo maps even though the song has audio clips (default false).
  seed, rng     "section" (default): every random draw comes from a stream seeded by (seed, section, part,
                purpose), so editing one section never moves another's notes. "stream": one random.Random(seed)
                drawn in note order (reproduces scripts written that way); amounts never change which draws happen,
                rng_skip_extra lists the sections where that script drew no extra velocity jitter.
  roles         how source notes get their role (mel / mid / bass / drums / pad): {"method": "tracks"} (default:
                each part takes the source track in parts[..].track and plays parts[..].role) or {"method":
                "skyline", mel_min, window (beats), tol, bass_max, bass_long_below, bass_long_beats, bass_always}.
  parts         {"Piano": {"track": 1, "role": "mid", "channel": 2, "split": "skyline", "voicing": {"mid": -6}}}:
                FL channel per output part; split=skyline gives one keyboard part mel/mid/bass voices (voicing:
                velocity offset per voice). "family": strings | choir | pad | brass | woodwind | bells (setup
                fills it from the channel / journal names): such a layer is played by flh_realism (expression,
                per-family timing, breaths, legato) instead of the piano rules (pedal, thinning, accents,
                humanize); only the section dynamics (dyn, vel_scale, vel_offset), swing, pauses and the final ring
                still apply. A clash guard gives a note its written timing back when realism's timing would make it
                rub (semitone / major 7th) against another part. "realism": false on a part (or the spec) turns it
                off; without flh_realism loaded the part is performed as before.
  lift_below    pitches below this go up an octave (after roles are found).
  tempo         {resolution (integration steps per beat), tail_beats, points_per_beat, min_change (bpm),
                 decimals, map_tail_beats, breath_bars}: tempo points come from integrating
                 stretch * curve(x) * tempo events, plus the phrase breath, and are written at points_per_beat.
  thin          {offbeat, within, ring_gap} (ticks): in sections with thin=true an off-beat repeat of a pitch played
                within `within` ticks is dropped; the kept note rings through the gap.
  pedal         {down, up (ticks from each pedal change), tail_beats, min_len, hold_window_beats, hold_beats,
                 change_on (role)}: pedal changes at every bass onset (else each bar start); note ends snap to the
                 pedal release; hold_beats > 0: the last chord holds that long past the last onset.
  overlap       {gap, min_len, scope ("part" | "pitch": across parts), mode ("moved": only overlaps the engine made |
                 "all")}: same-pitch notes never overlap.
  defaults      section parameters every section starts from; tight  extra defaults for sections with tight=true.
  sections      [{"name", "bar", ...}] in order. Per-section parameters (all optional, from defaults):
                  stretch   time stretch (0.9 = 10% faster); tools take "tempo" as a speed change
                  curve     [[bar, stretch], ...] tempo shape inside the section (rubato, rits, fermatas)
                  breath    phrase breathing depth (number or [[bar, depth], ...] steps)
                  dyn       velocity curve: number, [start, end] or [[bar, vel], ...]; absent: the notes' own
                            velocities (times vel_scale, plus vel_offset)
                  role_vel  {"mid": -11, ...} velocity offset per role
                  accent    {window, downbeat, beat, offbeat: {role: add}, drive, pulse ("quarter" | "meter":
                            dotted quarters in 6/8 9/8 12/8, downbeats only in 5/8 7/8)}
                  pitch_slope {"mel": [0.5, 66]}   vel += a * (pitch - centre)
                  swell     {depth, bars, phase, roles}  sine arcs (phrase swells)
                  humanize  {vel, vel_extra, time: {"*": sd, "bass": sd, "<part>": sd}, lead: {"mel": ticks},
                             clip, drum_clip}  (ticks; keys are part names, roles, then "*" - never for drums)
                  swing     {ratio (0.5 straight .. 0.667 triplet), grid (8 | 16), roles, parts}
                  vel_range [lo, hi];  thin, pedal, tight (bools);  legato {"gap", "roles", "max_gap"} or true
  events        [{"type": ...}] at bar positions ("bar" + "beat" in quarter beats, or "from"/"to" bars):
                  double   {from, to, roles, interval, as, vel, min_vel}   octave doubling
                  legato   {from, to, roles, gap, max_gap}                 each note held into the next
                  pause    {bar, beat, beats, release}                     silence (grand pause)
                  boom     {bar, beat, note (pitch class), vel, count, low, high, beats, role}   low octave hits
                  ring     {window, beats, add: [[pitch, vel]], offset, role}  final chord rings, notes added under it
                  rit      {from, to, amount}                              stretch ramps 1 -> amount (tempo)
                  fermata  {bar, beat, beats, stretch}                     stretch held (tempo)

Where notes go (auto specs): setup keeps a note map (journal "performance_map", per source .mid): for every source
note the playlist clip it is heard through (position, track, pattern), its record in that pattern and FL's own note
fields. The engine carries each note's source index, and flp_perform writes every performed note back into that
pattern at its clip-relative position (flp_perform_write, one flp_edit op): layered patterns of one channel, repeats,
clip offsets, short and muted clips keep their notes; a note stays inside the stretch of the pattern the same clips
play. A pattern several clips play holds one performance: the first clip (song order) decides, the other clips'
differences are reported, or unique_repeats=True gives each of those clips its own pattern copy. Style none writes
nothing (byte-exact). The read-back compares the project's records per (pattern, channel) with the intended ones
(count, positions, keys, lengths, velocities, slides, FL fields) and what the song plays; any difference: ok false.
Hand-written specs (no note map) write through flp_replace_part.

Tools: flp_perform_setup (auto from the project, or validate + store a spec), flp_perform (relative section edits,
render, one batch), flp_perform_undo (previous spec back, re-applied).
"""
import bisect
import copy
import hashlib
import json
import math
import os
import random
import re
import struct
import time

H = {}                                                   # the server namespace, set by register()
FORMAT = "flh-perform/1"
HISTORY = 10
INF = float("inf")
TICK_REF = 480                                           # auto specs and STYLES write tick values at 480 PPQ

# Engine defaults: neutral (a spec only needs what differs). With these, a render is the source note for note.
GLOBAL_DEFAULTS = {
    "first_bar": 1, "song_start_bar": 1, "base_bpm": "source", "seed": 1, "rng": "section", "rng_skip_extra": [],
    "tempo_pattern": "Tempo", "lift_below": 0, "tick_ppq": None, "allow_tempo_with_audio": False,
    "roles": {"method": "tracks", "mel_min": 59, "window": 0.9, "tol": 2, "bass_max": 47, "bass_long_below": 52,
              "bass_long_beats": 1, "bass_always": 45},
    "tempo": {"resolution": 12, "tail_beats": 12, "points_per_beat": 2, "min_change": 0.4, "decimals": 1,
              "map_tail_beats": 4, "breath_bars": 4},
    "thin": {"offbeat": 20, "within": 330, "ring_gap": 10},
    "pedal": {"down": 25, "up": -10, "tail_beats": 8, "min_len": 1, "hold_window_beats": 1, "hold_beats": 0,
              "change_on": "bass"},
    "overlap": {"gap": 0, "min_len": 1, "scope": "part", "mode": "moved"},
    "tight": {},
}
SECTION_DEFAULTS = {
    "stretch": 1.0, "curve": None, "breath": 0.0, "dyn": None, "vel_offset": 0, "vel_scale": 1.0,
    "thin": False, "pedal": False, "tight": False, "legato": False,
    "role_vel": {},
    "accent": {"window": 20, "downbeat": 0, "beat": 0, "offbeat": {}, "drive": 0, "pulse": "quarter"},
    "pitch_slope": {},
    "swell": None, "swing": None,
    "humanize": {"vel": 0.0, "vel_extra": 0.0, "time": {}, "lead": {}, "clip": 14, "drum_clip": 3},
    "vel_range": [1, 127],
}
EVENT_TYPES = ("double", "legato", "pause", "boom", "ring", "rit", "fermata")
ROLES = ("mel", "mid", "bass")                           # skyline voices
PART_ROLES = ROLES + ("drums", "pad")
ROLE_WORDS = {"mel": "melody", "mid": "accompaniment", "bass": "bass", "drums": "drums", "pad": "pad"}

# Style presets: DATA only. "defaults" become the spec's section defaults; "keys_voicing" goes on split keyboard
# parts; "phrase_end" broadens the last pulse of each section ([stretch]); "final_rit" = [bars, amount];
# "tempo" merges into spec.tempo. Tick values are at 480 PPQ (spec tick_ppq = 480).
STYLES = {
    "none": {"about": "identity: the notes exactly as they are", "defaults": {}},
    "tight": {"about": "pop / EDM / hip-hop: velocity variation, near-zero timing",
              "defaults": {"humanize": {"vel": 3.0, "time": {"*": 1.5, "drums": 1.0}, "clip": 4}}},
    "natural": {"about": "light human timing and velocity, phrase-level dynamics",
                "defaults": {"humanize": {"vel": 4.0, "time": {"*": 5.0, "bass": 3.5, "drums": 2.0}, "clip": 16},
                             "accent": {"downbeat": 2, "beat": 1, "pulse": "meter"},
                             "swell": {"depth": 3, "bars": 4, "phase": 1, "roles": ["mel", "mid", "pad", "bass"]}}},
    "expressive": {"about": "classical / solo piano: rubato, phrase breathing, swells, voiced melody, legato",
                   "defaults": {"humanize": {"vel": 4.5, "time": {"*": 8.0, "bass": 5.0, "drums": 0}, "lead": {"mel": -6},
                                             "clip": 22},
                                "accent": {"downbeat": 3, "beat": 1.5, "pulse": "meter"},
                                "swell": {"depth": 6, "bars": 4, "phase": 1, "roles": ["mel", "mid", "pad"]},
                                "legato": {"roles": ["mel"], "gap": 8, "max_gap": 240},
                                "breath": 0.03},
                   "keys_voicing": {"mid": -6, "bass": -3},
                   "phrase_end": [1.06], "final_rit": [2, 1.3], "tempo": {"breath_bars": 4}},
    "swing": {"about": "swung 8ths (options swing ratio / swing_grid 16 for 16ths)",
              "defaults": {"humanize": {"vel": 3.5, "time": {"*": 3.0, "drums": 1.5}, "clip": 8},
                           "swing": {"ratio": 0.6, "grid": 8}}},
    "laid-back": {"about": "melodic parts slightly behind a tight rhythm section",
                  "defaults": {"humanize": {"vel": 3.5, "time": {"*": 4.0, "drums": 1.5}, "clip": 14,
                                            "lead": {"mel": 12, "mid": 9, "pad": 6, "bass": 3}}}},
}


# ============================================================================= helpers
def _merge(a, b):
    """deep merge b over a copy of a (None in b deletes)."""
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _interp(tab, x):
    if x <= tab[0][0]:
        return tab[0][1]
    for (a, va), (b, vb) in zip(tab, tab[1:]):
        if x < b:
            return va + (vb - va) * (x - a) / (b - a)
    return tab[-1][1]


def _step(tab, x):
    v = [val for a, val in tab if a <= x]
    return v[-1] if v else tab[0][1]


_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _pitch(x):
    if isinstance(x, (int, float)):
        return int(x)
    m = re.fullmatch(r"\s*([A-Ga-g])([#b]?)(-?\d+)\s*", str(x))
    if not m:
        raise ValueError(f"note '{x}': use a MIDI number or a name like C4 (middle C), F#3, Bb1")
    return 12 * (int(m.group(3)) + 1) + _PC[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]


def _pc(x):
    if isinstance(x, (int, float)):
        return int(x) % 12
    m = re.fullmatch(r"\s*([A-Ga-g])([#b]?)\s*", str(x))
    if not m:
        return _pitch(x) % 12
    return (_PC[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12


def _digest(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


# ============================================================================= realism for non-piano layers
# Strings / choir / pads / brass / woodwinds / bells are played like a person on that instrument by flh_realism
# (ns["realism_apply"], built separately; looked up at call time, everything degrades gracefully without it).
# The piano rules (pedal, thinning, skyline voicing, accents, humanize) are not used on those parts.
_FAM_CACHE = {}


def layer_families():
    """data/perform/layer_families.json (words per family, which families get realism)."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "perform", "layer_families.json")
    st = os.stat(p)
    if _FAM_CACHE.get("m") != st.st_mtime:
        with open(p, encoding="utf-8") as f:
            _FAM_CACHE.update(m=st.st_mtime, d=json.load(f))
    return _FAM_CACHE["d"]


def _tok(s):
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split()) + " "


def layer_family(*texts):
    """Instrument family from names (first text with a match wins): strings, choir, pad, brass, woodwind, bells,
    piano, organ, drums, guitar, bass, synth, lead - or None."""
    words = layer_families()["words"]
    for t in texts:
        if not t:
            continue
        tt = _tok(os.path.splitext(os.path.basename(str(t).replace("\\", "/")))[0] if str(t).lower().endswith(
            (".fst", ".wav", ".flac", ".mp3", ".ogg")) else t)
        for fam, ws in words:
            if any(_tok(w) in tt for w in ws):
                return fam
    return None


def realism_families():
    return tuple(layer_families()["realism"])


def _note4(n):
    """A returned note in any of the shapes realism might use -> (start, end, pitch, vel)."""
    if isinstance(n, dict):
        s = n.get("start", n.get("s", n.get("tick")))
        e = n.get("end", n.get("e"))
        if e is None:
            ln = n.get("len", n.get("length", n.get("ticks", n.get("l"))))
            e = None if ln is None or s is None else s + ln
        p = n.get("pitch", n.get("p", n.get("key", n.get("k"))))
        v = n.get("vel", n.get("v", n.get("velocity")))
        return s, e, p, v
    return n[0], n[1], n[2], n[3]


def _ic_clash(a, b):
    return abs(int(a) - int(b)) % 12 in (1, 11)


def realism_run(fn, notes, family, context, others=(), tol=None):
    """Run one part's notes through realism and keep it honest. notes: [[start, end, pitch, vel]] (ticks at
    context["ppq"]); others: the other parts' [[start, end, pitch, vel]] at the same moment. -> (notes
    [[start, end, pitch, vel, k]] (k = the input note it came from, None for a note realism added), expression spec,
    report). Clash guard: a note whose new timing makes it overlap another part a semitone / major 7th away where the
    written note didn't gets its written timing back (added notes that clash are left out)."""
    ppq = int(context.get("ppq") or 96)
    tol = max(1, int(tol if tol is not None else ppq // 8))
    inp = [{"start": int(s), "end": int(e), "pitch": int(p), "vel": int(v), "k": i}
           for i, (s, e, p, v) in enumerate(notes)]
    ctx = dict(context, others=[[int(o[0]), int(o[1]), int(o[2]), int(o[3]) if len(o) > 3 else 64] for o in others])
    r = fn(copy.deepcopy(inp), family, ctx)
    if isinstance(r, list):
        r = {"notes": r}
    if not isinstance(r, dict) or not isinstance(r.get("notes"), list):
        raise ValueError("realism_apply gave no notes")
    out, used = [], set()
    pend, parent = [], {}                                     # re-articulated (split) notes: their source note
    for j, n in enumerate(r["notes"]):
        s, e, p, v = _note4(n)
        if s is None or p is None:
            continue
        s = int(round(float(s))); e = int(round(float(e))) if e is not None else s + ppq
        p = max(0, min(127, int(round(float(p))))); v = max(1, min(127, int(round(float(v if v is not None else 64)))))
        split = n.get("realism_split_of") if isinstance(n, dict) else None
        split = int(split) if isinstance(split, (int, float)) and 0 <= int(split) < len(notes) else None
        k = None if split is not None else (n.get("k") if isinstance(n, dict) else (n[4] if len(n) > 4 else None))
        k = int(k) if isinstance(k, (int, float)) and 0 <= int(k) < len(notes) and int(k) not in used else None
        if k is not None:
            used.add(k)
        out.append([max(0, s), max(max(0, s) + 1, e), p, v, k])
        parent[id(out[-1])] = split
        if k is None and split is None:
            pend.append(out[-1])
    for o in pend:                                            # no index back: the nearest unused same-pitch note
        cand = [i for i, x in enumerate(notes) if i not in used and int(x[2]) == o[2] and abs(int(x[0]) - o[0]) <= ppq]
        if cand:
            k = min(cand, key=lambda i: abs(int(notes[i][0]) - o[0])); used.add(k); o[4] = k
    oth = sorted((int(o[0]), int(o[1]), int(o[2])) for o in others)
    starts = [o[0] for o in oth]

    def hits(s, e, p):
        i = bisect.bisect_left(starts, s - 64 * ppq)
        got = set()
        for j in range(i, len(oth)):
            os_, oe, op = oth[j]
            if os_ >= e:
                break
            if min(e, oe) - max(s, os_) > tol and _ic_clash(p, op):
                got.add(j)
        return got
    reverted = dropped = moved = 0
    keep = []
    for o in out:
        new = hits(o[0], o[1], o[2])
        if o[4] is None:
            sp = parent.get(id(o))                            # a split of a held note: only NEW rubs count
            if new and (sp is None or new - hits(int(notes[sp][0]), int(notes[sp][1]), int(notes[sp][2]))):
                dropped += 1; continue
            keep.append(o); continue
        b = notes[o[4]]
        if new and new - hits(int(b[0]), int(b[1]), o[2]):
            o[0], o[1] = int(b[0]), int(b[1]); reverted += 1
        if o[0] != int(b[0]) or o[1] != int(b[1]):
            moved += 1
        keep.append(o)
    keep.sort(key=lambda x: (x[0], x[2], -1 if x[4] is None else x[4]))
    rep = {"family": family, "notes": len(keep), "retimed": moved}
    if reverted:
        rep["clash_guard_reverted"] = reverted
    if dropped:
        rep["clash_guard_dropped"] = dropped
    return keep, r.get("expression"), rep


def expression_at(ex, offsets):
    """An expression spec (flh_realism: {"clips": [{"section", "control", "points": [[tick, value], ...]}], ...}) moved
    to song ticks: one copy of every clip per offset (ticks). Other shapes come back unchanged."""
    if not isinstance(ex, dict) or not isinstance(ex.get("clips"), list) or not offsets:
        return ex
    if len(offsets) == 1 and not offsets[0]:
        return ex
    out = dict(ex); clips = []
    for off in offsets:
        for cl in ex["clips"]:
            if isinstance(cl, dict) and isinstance(cl.get("points"), list):
                clips.append(dict(cl, points=[[p[0] + off] + list(p[1:]) for p in cl["points"]]))
            else:
                clips.append(cl)
    out["clips"] = clips
    return out


def realism_fn():
    """flh_realism's entry point when it's loaded (None otherwise)."""
    f = H.get("realism_apply") if H else None
    return f if callable(f) else None


# ============================================================================= source MIDI
def _vlq(d, i):
    v = 0
    while True:
        b = d[i]; i += 1; v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def read_midi(path):
    """-> (div, tracks [[(tick, kind, status, bytes)]]): a plain SMF reader (running status, meta, sysex)."""
    d = open(path, "rb").read()
    return _parse_midi(d, path)


def _parse_midi(d, path="MIDI"):
    if d[:4] != b"MThd":
        raise ValueError(f"{path} is not a MIDI file")
    hl = struct.unpack(">I", d[4:8])[0]
    _, ntr, div = struct.unpack(">HHH", d[8:14])
    if div & 0x8000:
        raise ValueError("SMPTE-timed MIDI files aren't supported")
    i = 8 + hl; tracks = []
    for _ in range(ntr):
        if d[i:i + 4] != b"MTrk":
            break
        ln = struct.unpack(">I", d[i + 4:i + 8])[0]
        t = d[i + 8:i + 8 + ln]; i += 8 + ln; j = 0; tick = 0; run = None; ev = []
        while j < len(t):
            dt, j = _vlq(t, j); tick += dt; b = t[j]
            if b == 0xFF:
                typ = t[j + 1]; n, j = _vlq(t, j + 2); ev.append((tick, "meta", typ, t[j:j + n])); j += n
            elif b in (0xF0, 0xF7):
                n, j = _vlq(t, j + 1); ev.append((tick, "sysex", b, t[j:j + n])); j += n
            else:
                if b & 0x80:
                    run = b; j += 1
                st = run; n = 1 if st & 0xF0 in (0xC0, 0xD0) else 2
                ev.append((tick, "ch", st, bytes(t[j:j + n]))); j += n
        tracks.append(ev)
    return div, tracks


_SRC_CACHE = {}


def load_source(path):
    """The source's notes [{"s", "e", "p", "v", "t" (track)}] sorted by (start, pitch), meters, tempo map and track
    names (cached by content)."""
    raw = open(path, "rb").read(); key = (os.path.abspath(path), hashlib.sha1(raw).hexdigest())
    if key in _SRC_CACHE:
        return _SRC_CACHE[key]
    div, tracks = _parse_midi(raw, path)
    events = [(e[0], e[1], e[2], e[3], ti) for ti, tr in enumerate(tracks) for e in tr]
    names = {}
    for t, k, s, b, ti in events:
        if k == "meta" and s == 0x03 and ti not in names:
            names[ti] = b.decode("latin-1", "replace")

    def off_first(e):
        return (e[0], 0 if (e[1] == "ch" and (e[2] & 0xF0 == 0x80 or (e[2] & 0xF0 == 0x90 and e[3][1] == 0))) else 1)
    on, notes = {}, []
    for t, k, st_, b, ti in sorted(events, key=off_first):
        if k == "ch" and st_ & 0xF0 in (0x80, 0x90):
            key_ = (ti, st_ & 0x0F, b[0])
            if st_ & 0xF0 == 0x90 and b[1] > 0:
                on.setdefault(key_, []).append((t, b[1]))
            elif on.get(key_):
                s0, v0 = on[key_].pop(0)
                notes.append({"s": s0, "e": t, "p": b[0], "v": v0, "t": ti})
    if not notes:
        raise ValueError(f"{path} has no notes")
    notes.sort(key=lambda n: (n["s"], n["p"]))
    meters = sorted((t, b[0], 2 ** b[1]) for t, k, s, b, ti in events if k == "meta" and s == 0x58)
    tempos = []
    for t, k, s, b, ti in sorted((e for e in events if e[1] == "meta" and e[2] == 0x51 and len(e[3]) == 3),
                                 key=lambda e: e[0]):
        bpm = round(60_000_000 / int.from_bytes(b, "big"), 3)
        if tempos and tempos[-1][0] == t:
            tempos[-1] = (t, bpm)
        elif not tempos or tempos[-1][1] != bpm:
            tempos.append((t, bpm))
    src = {"div": div, "notes": notes, "meters": meters or [(0, 4, 4)], "track_names": names, "path": path,
           "tempos": tempos, "sha": key[1]}
    if len(_SRC_CACHE) > 8:
        _SRC_CACHE.clear()
    _SRC_CACHE[key] = src
    return src


def write_source(path, div, tracks, meters=None, tempos=None):
    """A type-1 source .mid: track 0 = tempo map + time signatures, then one track per (name, [[s, e, key, vel]]).
    Same-pitch notes that overlap go on different MIDI channels, so every note reads back exactly."""
    H["writable"](path)
    t0 = [(int(t), b"\xff\x51\x03" + int(round(60_000_000 / float(b))).to_bytes(3, "big")) for t, b in tempos or []]
    for t, num, den in meters or [(0, 4, 4)]:
        t0.append((int(t), b"\xff\x58\x04" + bytes([int(num), int(round(math.log2(int(den)))), 24, 8])))
    out = [("tempo", t0)]
    for name, notes in tracks:
        evs, busy = [], {}                                 # (channel, key) -> end of the last note there
        for s, e, key, vel in sorted(notes):
            ch = next((c for c in range(16) if busy.get((c, key), -1) <= s), 15)
            busy[(ch, key)] = e
            evs.append((int(s), bytes([0x90 | ch, int(key), max(1, min(127, int(vel)))])))
            evs.append((int(e), bytes([0x80 | ch, int(key), 0])))
        out.append((str(name)[:60], evs))
    H["midi_write"](path, int(div), out)
    return path


# ============================================================================= spec normalisation
def normalize(spec):
    """Spec with engine defaults filled in (a new dict); raises ValueError on a bad spec."""
    if not isinstance(spec, dict):
        raise ValueError("the performance spec must be a JSON object")
    g = _merge(GLOBAL_DEFAULTS, {k: v for k, v in spec.items() if k not in ("sections", "events", "parts")})
    g["parts"] = copy.deepcopy(spec.get("parts") or {})
    if not g["parts"]:
        raise ValueError("spec.parts is empty: give {part: {role|track, channel}}")
    for name, p in g["parts"].items():
        if g["roles"]["method"] == "skyline" and p.get("role") not in ROLES:
            raise ValueError(f"part {name}: role must be one of {ROLES} (skyline roles)")
        p.setdefault("role", "mel" if "mel" in name.lower() else "bass" if "bass" in name.lower() else "mid")
        if p["role"] not in PART_ROLES:
            raise ValueError(f"part {name}: role must be one of {PART_ROLES}")
    if not (isinstance(g["base_bpm"], (int, float)) or g["base_bpm"] == "source"):
        raise ValueError("base_bpm: a number, or \"source\" (the source's own tempo map)")
    secs = spec.get("sections") or []
    if not secs:
        raise ValueError("spec.sections is empty")
    for i, s in enumerate(secs):
        if "name" not in s or "bar" not in s:
            raise ValueError(f"section {i + 1} needs name and bar")
        if i and float(s["bar"]) <= float(secs[i - 1]["bar"]):
            raise ValueError(f"sections must be in bar order: {s['name']} (bar {s['bar']}) comes after "
                             f"{secs[i - 1]['name']} (bar {secs[i - 1]['bar']})")
    names = [s["name"].lower() for s in secs]
    if len(set(names)) != len(names):
        raise ValueError("section names must be unique")
    defaults = _merge(SECTION_DEFAULTS, spec.get("defaults") or {})
    out = []
    for s in secs:
        eff = _merge(defaults, g["tight"]) if s.get("tight") else defaults
        eff = _merge(eff, {k: v for k, v in s.items()})
        out.append(eff)
    g["sections"] = out
    evs = copy.deepcopy(spec.get("events") or [])
    for i, e in enumerate(evs):
        if e.get("type") not in EVENT_TYPES:
            raise ValueError(f"event {i + 1}: type must be one of {EVENT_TYPES}")
    g["events"] = evs
    if g["rng"] not in ("section", "stream"):
        raise ValueError("rng must be 'section' or 'stream'")
    return g


def _scale_ticks(g, f):
    """tick-valued spec numbers written at tick_ppq -> source ticks (factor f)."""
    def ri(x, lo=None):
        v = int(round(float(x) * f))
        return max(lo, v) if lo is not None else v

    def sd(d):
        return {k: float(v) * f for k, v in d.items()} if isinstance(d, dict) else float(d or 0) * f
    for k in ("offbeat", "within", "ring_gap"):
        g["thin"][k] = ri(g["thin"][k])
    g["pedal"]["down"] = ri(g["pedal"]["down"]); g["pedal"]["up"] = ri(g["pedal"]["up"])
    g["pedal"]["min_len"] = ri(g["pedal"]["min_len"], 1)
    g["overlap"]["gap"] = ri(g["overlap"]["gap"], 0); g["overlap"]["min_len"] = ri(g["overlap"]["min_len"], 1)
    for s in g["sections"]:
        s["accent"]["window"] = ri(s["accent"].get("window", 20), 1)
        hz = s["humanize"]
        hz["time"] = sd(hz.get("time") or {}); hz["lead"] = sd(hz.get("lead") or {})
        hz["clip"] = float(hz.get("clip", 14)) * f; hz["drum_clip"] = float(hz.get("drum_clip", 3)) * f
        lg = s.get("legato")
        if isinstance(lg, dict):
            for k in ("gap", "max_gap"):
                if lg.get(k) is not None:
                    lg[k] = ri(lg[k])
    for e in g["events"]:
        for k in ("gap", "max_gap", "release", "window", "offset"):
            if e.get(k) is not None and e["type"] in ("legato", "pause", "ring"):
                e[k] = ri(e[k])


# ============================================================================= the engine
class _Ctx:
    def __init__(self, g, src):
        self.g = g; self.div = div = src["div"]
        notes = src["notes"]; self.end_tick = end_tick = max(n["e"] for n in notes)
        self.meter = None; self.swing_skipped = set()
        if g.get("bars"):
            self.bars = [(int(b[0]), int(b[1])) for b in g["bars"]]
        else:
            bars, t, mi, meters, mt = [], 0, 0, src["meters"], []
            while t < end_tick:
                while mi + 1 < len(meters) and meters[mi + 1][0] <= t:
                    mi += 1
                beat = div * 4 // meters[mi][2]; bars.append((t, meters[mi][1] * beat)); t += meters[mi][1] * beat
                mt.append((meters[mi][1], meters[mi][2]))
            self.bars = bars; self.meter = mt
        self.bstart = [b[0] for b in self.bars]; self.nbars = len(self.bars)
        self.fb = int(g["first_bar"])
        self.secs = g["sections"]; self.sstart = [float(s["bar"]) for s in self.secs]
        for i, s in enumerate(self.secs):
            s["_end"] = self.sstart[i + 1] if i + 1 < len(self.secs) else INF
            s["_i"] = i
            s["_curve"] = self._points(s.get("curve"), s, 1.0, "curve")
            s["_dyn"] = None if s.get("dyn") is None else self._points(s.get("dyn"), s, None, "dyn")
            br = s.get("breath") or 0
            s["_breath"] = [(float(a), float(v)) for a, v in br] if isinstance(br, list) else [(-INF, float(br))]

    def _points(self, v, s, default, what):
        if v is None:
            if default is None:
                raise ValueError(f"section {s['name']}: no {what} (give a number, [start, end] or [[bar, value], ...])")
            return [(float(s["bar"]), default)]
        if isinstance(v, (int, float)):
            return [(float(s["bar"]), v)]
        if len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            end = s["_end"] if s["_end"] != INF else self.nbars + self.fb
            return [(float(s["bar"]), v[0]), (float(end), v[1])]
        pts = [(a, b) for a, b in v]
        if any(b2[0] < a2[0] for a2, b2 in zip(pts, pts[1:])):
            raise ValueError(f"section {s['name']}: {what} points must be in bar order")
        return pts

    # positions
    def bar_of(self, x):
        return max(0, bisect.bisect_right(self.bstart, x) - 1)

    def bpos(self, x):
        i = self.bar_of(x)
        return i + self.fb + (x - self.bars[i][0]) / self.bars[i][1]

    def tick(self, bar, beat=0.0):
        """source tick of a bar position (fractional bars ok) plus quarter beats."""
        b = float(bar); i = int(math.floor(b)) - self.fb; frac = b - math.floor(b)
        if i < 0:
            return int(round(beat * self.div))
        if i >= self.nbars:
            last = self.bars[-1]
            return int(round(last[0] + last[1] * (i - self.nbars + 1 + frac) + beat * self.div))
        b0, bl = self.bars[i]
        return int(round(b0 + frac * bl + float(beat) * self.div))

    def pulse(self, bi):
        """the felt beat of bar bi in ticks: dotted quarters in 6/8 9/8 12/8, the bar in 5/8 7/8, halves in x/2."""
        if not self.meter:
            return self.div
        num, den = self.meter[bi]; div = self.div
        if den == 8:
            return div * 3 // 2 if num % 3 == 0 else self.bars[bi][1]
        if den == 16:
            return div * 3 // 4 if num % 3 == 0 else div
        if den == 2:
            return div * 2
        return div

    def swing(self, t, sw):
        """swing time warp: the off-beat of each 8th (16th) pair moves to `ratio` of the pair. Pairs are counted
        inside the meter's felt beat (pulse): a compound beat (6/8's dotted quarter = three 8ths) can't pair its
        8ths, so 8th swing leaves those bars straight (noted in swing_skipped; swing_grid=16 swings them); an odd
        bar (7/8) pairs from its downbeat and its last odd unit stays straight."""
        u = self.div * 4.0 / int(sw.get("grid", 8)); pair = 2 * u; r = float(sw.get("ratio", 0.5))
        bi = self.bar_of(t); bs, bl = self.bars[bi]
        pl = self.pulse(bi)
        if pl % pair and pl != bl:                         # compound beat: no 8th (16th) pairs inside it
            if self.meter:
                self.swing_skipped.add("%d/%d" % self.meter[bi])
            return t
        ps = bs + ((t - bs) // pl) * pl if t >= bs else bs
        pe = min(ps + pl, bs + bl)
        p = (t - ps) % pair; base = t - p
        if base + pair > pe + 1e-9:                        # the unpaired last unit of an odd beat / bar
            return t
        mid = r * pair
        return base + (p * mid / u if p < u else mid + (p - u) * (pair - mid) / u)

    def sec(self, x):
        return self.secs[max(0, bisect.bisect_right(self.sstart, x) - 1)]

    def sec_range_ticks(self, s):
        a = self.tick(s["bar"]) if float(s["bar"]) > self.fb else 0
        b = self.tick(s["_end"]) if s["_end"] != INF else None
        return a, b


def _rng_factory(g):
    seed = g["seed"]; streams = {}

    def local(*key):
        k = "|".join(str(x) for x in (seed,) + key)
        if k not in streams:
            streams[k] = random.Random(k)
        return streams[k]
    if g["rng"] == "stream":
        main = random.Random(seed)
        skip = {str(x).lower() for x in g.get("rng_skip_extra") or []}

        def draw(sec, role, purpose):
            if purpose == "vel_extra" and sec["name"].lower() in skip:
                return None                                  # this script drew nothing here
            return main.gauss(0, 1)
    else:
        def draw(sec, role, purpose):
            return local(sec["name"], role, purpose).gauss(0, 1)
    return draw, local


def _hz(hz, key, role, part):
    """a humanize value for a note: part name, then role, then "*" (drums: part or "drums" only)."""
    t = hz.get(key)
    if isinstance(t, dict):
        if part is not None and part in t:
            return t[part]
        if role == "drums":
            return t.get("drums", 0)
        return t.get(role, t.get("*", 0))
    if role == "drums":
        return 0
    return t or 0


def tempo_shaped(g):
    """True when the spec changes the song's tempo (a numeric base_bpm always generates its own map)."""
    if not isinstance(g["base_bpm"], str):
        return True
    for s in g["sections"]:
        if float(s.get("stretch", 1.0)) != 1.0:
            return True
        c = s.get("curve")
        if c is not None:
            vals = [c] if isinstance(c, (int, float)) else [p[1] if isinstance(p, (list, tuple)) else p for p in c]
            if any(float(v) != 1.0 for v in vals):
                return True
        br = s.get("breath") or 0
        if any(float(v) for _, v in br) if isinstance(br, list) else float(br):
            return True
    return any(e["type"] in ("rit", "fermata") for e in g["events"])


def _warp(ctx):
    """W: source tick -> performance tick (stretch * curve * tempo events, plus the phrase breath)."""
    g = ctx.g; tp = g["tempo"]; div = ctx.div; end_tick = ctx.end_tick
    tevs = [e for e in g["events"] if e["type"] in ("rit", "fermata")]
    spans = []
    for e in tevs:
        if e["type"] == "rit":
            a = float(e["from"]); b = float(e["to"]); spans.append(("rit", a, b, float(e.get("amount", 1.2))))
        else:
            a = ctx.bpos(ctx.tick(e["bar"], e.get("beat", 0)))
            b = ctx.bpos(ctx.tick(e["bar"], e.get("beat", 0) + e.get("beats", 1)))
            spans.append(("hold", a, b, float(e.get("stretch", 1.5))))

    def mult(x):
        m = 1.0
        for kind, a, b, amt in spans:
            if a <= x < b:
                m *= (1 + (amt - 1) * (x - a) / (b - a)) if kind == "rit" else amt
        return m
    STEP = div // int(tp["resolution"])
    cum, acc = [0.0], 0.0
    for x in range(0, end_tick + div * int(tp["tail_beats"]), STEP):
        xb = ctx.bpos(x) if x < end_tick else INF
        s = ctx.sec(xb)
        f = STEP * s["stretch"] * _interp(s["_curve"], xb)
        if spans:
            f *= mult(xb)
        acc += f; cum.append(acc)
    N = int(tp["breath_bars"]); bars = ctx.bars; nbars = ctx.nbars
    depth = [_step(ctx.sec(i + ctx.fb)["_breath"], i + ctx.fb) for i in range(nbars)]

    def T0(x):
        i = min(len(cum) - 2, max(0, int(x // STEP)))
        return cum[i] + (cum[i + 1] - cum[i]) * ((x - i * STEP) / STEP)

    def lean(x):
        bi = ctx.bar_of(x); ph0 = bi - bi % N
        ps = bars[ph0][0]; pe = bars[ph0 + N][0] if ph0 + N < nbars else end_tick
        L = max(1, pe - ps); p = min(1.0, max(0.0, (x - ps) / L))
        return -(depth[bi] / math.pi) * math.sin(math.pi * p) * L

    def W(x):
        return T0(x) + lean(x)
    return W


def _tempo(ctx, src=None):
    """[(tick, bpm)] tempo points (half-beat by default)."""
    g = ctx.g; tp = g["tempo"]; div = ctx.div; end_tick = ctx.end_tick
    if isinstance(g["base_bpm"], str):
        return _tempo_src(ctx, src)
    W = _warp(ctx)
    ppb = int(tp["points_per_beat"]); Hh = div // ppb; BPM = float(g["base_bpm"]); tmap = []
    num = 60.0 / ppb; dec = int(tp["decimals"]); minc = float(tp["min_change"])
    for x in range(0, end_tick + div * int(tp["map_tail_beats"]), Hh):
        dur = (W(x + Hh) - W(x)) / div * 60 / BPM
        bpm = round(num / max(dur, 1e-3), dec)
        if not tmap or abs(bpm - tmap[-1][1]) >= minc:
            tmap.append((x, bpm))
    return tmap


def _tempo_src(ctx, src):
    """The source's tempo map, times the spec's shaping (unshaped: the source map itself)."""
    g = ctx.g; tp = g["tempo"]; div = ctx.div; end_tick = ctx.end_tick
    sm = [(int(t), float(b)) for t, b in (src or {}).get("tempos") or []]
    if not sm:
        sm = [(0, 120.0)]
    if sm[0][0] > 0:
        sm.insert(0, (0, sm[0][1]))
    if not tempo_shaped(g):
        return sm
    W = _warp(ctx)
    tail = end_tick + div * int(tp["map_tail_beats"]); Hh = max(1, div // int(tp["points_per_beat"]))
    changes = {t for t, _ in sm if t < tail}
    xs = sorted(set(range(0, tail, Hh)) | changes)
    dec = int(tp["decimals"]); minc = float(tp["min_change"]); out = []
    for a, b in zip(xs, xs[1:] + [xs[-1] + Hh]):
        f = (W(b) - W(a)) / (b - a)
        bpm = round(_step(sm, a) / max(f, 1e-3), dec)
        if not out or abs(bpm - out[-1][1]) >= minc or (a in changes and bpm != out[-1][1]):
            out.append((a, bpm))
    return out


def _skyline(ctx, notes, R):
    div = ctx.div
    groups = {}
    for n in notes:
        groups.setdefault(n["s"], []).append(n)
    onsets = sorted(groups); w = int(div * R["window"])
    for s in onsets:
        gr = sorted(groups[s], key=lambda n: -n["p"])
        win = [m["p"] for s2 in onsets[max(0, bisect.bisect_left(onsets, s - w)):bisect.bisect_left(onsets, s)]
               for m in groups[s2]]
        top = gr[0]
        for n in gr:
            n["role"] = "mid"
        if top["p"] >= R["mel_min"] and (not win or top["p"] >= max(win) - R["tol"]):
            top["role"] = "mel"
        low = gr[-1]
        if low["p"] <= R["bass_max"] or (low["p"] < R["bass_long_below"] and low["e"] - low["s"] >= div * R["bass_long_beats"]
                                         and len(gr) > 1):
            low["role"] = "bass"
        for n in gr:
            if n["p"] <= R["bass_always"]:
                n["role"] = "bass"
    return notes


def _roles(ctx, src, notes):
    g = ctx.g; R = g["roles"]
    if R["method"] == "tracks":
        tracks = {}
        for name, p in g["parts"].items():
            t = p.get("track")
            if isinstance(t, str):
                t = next((i for i, nm in src["track_names"].items() if nm.lower() == t.lower()), None)
                if t is None:
                    raise ValueError(f"part {name}: no source track named {p.get('track')!r} "
                                     f"(have {sorted(src['track_names'].values())})")
            tracks[int(t)] = (name, p["role"])
        kept = []
        for n in notes:
            if n["t"] in tracks:
                n["part"], n["role"] = tracks[n["t"]]; kept.append(n)
        for name, p in g["parts"].items():
            if p.get("split") == "skyline" and p["role"] != "drums":
                _skyline(ctx, [n for n in kept if n["part"] == name], R)
        return kept
    return _skyline(ctx, notes, R)


def _in_range(x, e):
    return float(e.get("from", -INF)) <= x < float(e.get("to", INF))


def perform(spec, source_notes=None, base_dir=None):
    """Render a spec: {"parts": {part: [[start, end, pitch, vel], ...]} (source ticks), "tempo": [[tick, bpm], ...],
    "tempo_points": [[bar, bpm], ...] (4/4 song bars), "ppq", "sections": [...]}. source_notes: a load_source() dict."""
    g = normalize(spec)
    src = source_notes or load_source(_source_path(spec, base_dir))
    if g.get("tick_ppq") and int(g["tick_ppq"]) != src["div"]:
        _scale_ticks(g, src["div"] / float(g["tick_ppq"]))
    ctx = _Ctx(g, src); div = ctx.div; bars = ctx.bars; end_tick = ctx.end_tick
    draw, local = _rng_factory(g)
    stream = g["rng"] == "stream"
    # o: the source note a rendered note comes from; i: its index in the source (provenance: its pattern and clip)
    notes = [dict(n, o=(n["s"], n["p"]), i=k) for k, n in enumerate(src["notes"])]
    notes = _roles(ctx, src, notes)
    part_of_role = {}
    for name, p in g["parts"].items():
        part_of_role.setdefault(p["role"], name)
    if g["roles"]["method"] != "tracks":
        for n in notes:
            n["part"] = part_of_role.get(n["role"])
    for n in notes:
        n["x"] = ctx.bpos(n["s"]); n["sec"] = ctx.sec(n["x"])
    # non-piano layers (strings, choir, pads, brass, ...): played by flh_realism instead of the piano rules
    RF = realism_fn(); real = {}
    if RF and g.get("realism", True) is not False and g["roles"]["method"] == "tracks" \
            and (g.get("auto") or {}).get("style") != "none":     # style none stays an exact identity
        fams = realism_families()
        real = {name: p["family"] for name, p in g["parts"].items() if p.get("family") in fams
                and p.get("role") != "drums" and p.get("realism", True) is not False}

    # ---- thinning: off-beat quick repeats dropped in thin sections; the kept note rings through the gap
    th = g["thin"]; drop = set(); recent = {}
    for n in sorted(notes, key=lambda n: (n["s"], -n["p"])):
        bi = ctx.bar_of(n["s"]); off = (n["s"] - bars[bi][0]) % div
        rk = n["p"] if g["roles"]["method"] != "tracks" else (n.get("part"), n["p"])
        last = recent.get(rk)
        if n["sec"]["thin"] and off > th["offbeat"] and last is not None and n["s"] - last <= th["within"] \
                and n["role"] not in ("bass", "drums") and not (real and n.get("part") in real):
            drop.add(id(n))
        recent[rk] = n["s"]
    thinned = len(drop)
    if drop:
        byp = {}
        for n in notes:
            byp.setdefault(n["p"] if g["roles"]["method"] != "tracks" else (n.get("part"), n["p"]), []).append(n)
        for seq in byp.values():
            for i, n in enumerate(seq):
                if id(n) in drop:
                    continue
                nxt, gap = None, False
                for m in seq[i + 1:]:
                    if m["s"] <= n["s"]:
                        continue
                    if id(m) in drop:
                        gap = True; continue
                    nxt = m; break
                if nxt is not None and gap:
                    gap = any(id(m) in drop and n["s"] < m["s"] < nxt["s"] for m in seq)
                    if gap:
                        n["e"] = max(n["e"], nxt["s"] - th["ring_gap"])
        notes = [n for n in notes if id(n) not in drop]
    lifted = 0
    for n in notes:
        if n["p"] < g["lift_below"] and n["role"] != "drums":
            n["p"] += 12; lifted += 1

    # ---- dynamics
    voicing = {name: p.get("voicing") or {} for name, p in g["parts"].items()}
    for n in notes:
        x = n["x"]; s = n["sec"]; role = n["role"]; bi = ctx.bar_of(n["s"]); bs = bars[bi][0]
        rk = role if stream else (n.get("part") or role)
        ac = s["accent"]
        pulse = ctx.pulse(bi) if ac.get("pulse") == "meter" else div
        inbeat = (n["s"] - bs) % pulse
        if s["_dyn"] is None:                                # the notes' own velocities
            v = float(n["v"])
            if s.get("vel_scale", 1.0) != 1.0:
                v *= float(s["vel_scale"])
        else:
            v = _interp(s["_dyn"], x)
        if s.get("vel_offset"):
            v += float(s["vel_offset"])
        v += (s["role_vel"] or {}).get(role, 0)
        if voicing.get(n.get("part")):
            v += voicing[n["part"]].get(role, 0)
        if inbeat < ac["window"]:
            v += ac["downbeat"] if (n["s"] - bs) < ac["window"] else ac["beat"]
        elif role in (ac.get("offbeat") or {}):
            v += ac["offbeat"][role]
        sl = (s.get("pitch_slope") or {}).get(role)
        if sl and role != "drums":
            v += sl[0] * (n["p"] - sl[1])
        if ac.get("drive") and inbeat < ac["window"]:
            v += ac["drive"]
        hz = s["humanize"]
        z = draw(s, rk, "vel")
        v += z * hz["vel"]
        sw = s.get("swell")
        if sw and sw.get("depth") and role in sw.get("roles", ["mel"]):
            v += sw["depth"] * math.sin(math.pi * ((x - sw.get("phase", 1)) % sw.get("bars", 2)) / sw.get("bars", 2))
        z2 = draw(s, rk, "vel_extra")
        if z2 is None and hz.get("vel_extra"):
            z2 = local(s["name"], role, "vel_extra").gauss(0, 1)
        if z2 is not None:
            v += z2 * hz.get("vel_extra", 0)
        lo, hi = s["vel_range"]
        if real and n.get("part") in real:                   # a layer: only the section's dynamics, no keyboard
            v = float(n["v"]) if s["_dyn"] is None else _interp(s["_dyn"], x)   # accents / jitter (realism plays it)
            if s["_dyn"] is None and s.get("vel_scale", 1.0) != 1.0:
                v *= float(s["vel_scale"])
            v += float(s.get("vel_offset") or 0)
        n["v"] = int(max(lo, min(hi, round(v))))

    # ---- doublings
    have = {(n["s"], n["p"]) for n in notes}; extra = []
    for e in (e for e in g["events"] if e["type"] == "double"):
        roles = e.get("roles", ["mel"]); iv = int(e.get("interval", -12)); as_ = e.get("as", "mid")
        for n in notes:
            if n["role"] in roles and n["role"] != "drums" and _in_range(n["x"], e) and (n["s"], n["p"] + iv) not in have \
                    and not (real and n.get("part") in real):
                extra.append(dict(n, o=None, i=None, p=n["p"] + iv, role=as_, part=e.get("part") or part_of_role.get(as_),
                                  v=max(int(e.get("min_vel", 30)), n["v"] + int(e.get("vel", -14)))))
    notes += extra

    # ---- pedal points
    pdl = g["pedal"]
    changes = sorted({n["s"] for n in notes if n["role"] == pdl["change_on"]})
    pts = []
    for bs, bl in bars:
        lo_ = bisect.bisect_left(changes, bs); hi_ = bisect.bisect_left(changes, bs + bl)
        pts.extend(changes[lo_:hi_] if hi_ > lo_ else [bs])
    pts = sorted(set(pts))

    # ---- timing: swing warp + humanization (ticks; the rubato lives in the tempo map)
    for n in notes:
        s = n["sec"]; hz = s["humanize"]; role = n["role"]; part = n.get("part")
        rk = role if stream else (part or role)
        sd = _hz(hz, "time", role, part); lead = _hz(hz, "lead", role, part); clip = hz.get("clip", 14)
        if role == "drums":
            clip = min(clip, hz.get("drum_clip", 3))
        z = draw(s, rk, "time")
        j = max(-clip, min(clip, z * sd))
        if real and part in real:                            # realism times a layer; the swing grid still applies
            lead = j = 0
        sw = s.get("swing")
        if sw and sw.get("ratio") not in (None, 0.5) and role in sw.get("roles", PART_ROLES) \
                and (not sw.get("parts") or part in sw["parts"]):
            bs_ = int(round(ctx.swing(n["s"], sw))); be_ = max(bs_ + 1, int(round(ctx.swing(n["e"], sw))))
            n["s2"] = max(0, int(round(bs_ + lead + j)))
            n["e1"] = be_ + (n["s2"] - bs_)
        else:
            n["s2"] = max(0, int(round(n["s"] + lead + j)))
            n["e1"] = int(round(n["e"])) + (n["s2"] - n["s"])
    ped = []
    for i, c in enumerate(pts):
        down = int(c) + pdl["down"]
        up = int(pts[i + 1]) + pdl["up"] if i + 1 < len(pts) else int(end_tick) + div * pdl["tail_beats"]
        if up > down:
            ped.append((down, up))
    pd = [p[0] for p in ped]
    last_on = max(n["s2"] for n in notes)
    hold_w = div * pdl["hold_window_beats"]; hold_to = last_on + div * pdl["hold_beats"]
    for n in notes:
        e = n["e1"]; dr = n["role"] == "drums" or bool(real and n.get("part") in real)
        if pdl["hold_beats"] and not dr and n["s2"] >= last_on - hold_w:
            e = max(e, hold_to)
        if n["sec"]["pedal"] and not dr:
            k = bisect.bisect_right(pd, e) - 1
            if k >= 0 and ped[k][0] <= e < ped[k][1]:
                e = ped[k][1]
        n["e2"] = max(n["s2"] + pdl["min_len"], e)

    tmap = _tempo(ctx, src)

    # ---- legato (section flags, then events), per part
    legs = []
    for s in ctx.secs:
        lg = s.get("legato")
        if lg:
            lg = lg if isinstance(lg, dict) else {}
            legs.append(dict(lg, **{"from": s["bar"], "to": s["_end"]}))
    legs += [e for e in g["events"] if e["type"] == "legato"]
    for e in legs:
        roles = e.get("roles", ["mel"]); gap = e.get("gap", 8); mg = e.get("max_gap")
        sel = sorted((n for n in notes if n["role"] in roles and n["role"] != "drums" and _in_range(n["x"], e)
                      and not (real and n.get("part") in real)),
                     key=lambda n: n["s2"])
        byp = {}
        for n in sel:
            byp.setdefault(n.get("part"), []).append(n)
        for seq in byp.values():
            for a, b in zip(seq, seq[1:]):
                if mg is None or b["s2"] - a["e2"] <= mg:
                    a["e2"] = max(a["e2"], b["s2"] - gap)
    ov = g["overlap"]; seqs = {}; moved_only = ov.get("mode") == "moved"
    for n in sorted(notes, key=lambda n: n["s2"]):
        seqs.setdefault(n["p"] if ov.get("scope") == "pitch" else (n.get("part"), n["p"]), []).append(n)
    for seq in seqs.values():
        for a, b in zip(seq, seq[1:]):
            if a["e2"] > b["s2"] - ov["gap"]:
                if moved_only and a["s"] <= b["s"] and a["e"] > b["s"] - ov["gap"]:
                    continue                                 # the source already had this overlap: keep it
                a["e2"] = max(a["s2"] + ov["min_len"], b["s2"] - ov["gap"])

    # ---- pauses, booms, final ring
    for e in (e for e in g["events"] if e["type"] == "pause"):
        PS = ctx.tick(e["bar"], e.get("beat", 0)); PE = ctx.tick(e["bar"], e.get("beat", 0) + e.get("beats", 1))
        kept = []
        for n in notes:
            if PS <= n["s2"] < PE:
                continue
            if n["s2"] < PS < n["e2"]:
                n["e2"] = PS - e.get("release", 5)
            kept.append(n)
        notes = kept
    for e in (e for e in g["events"] if e["type"] == "boom"):
        t = ctx.tick(e["bar"], e.get("beat", 0)); pc = _pc(e.get("note", e.get("pc", 0)))
        L = int(round(div * e.get("beats", 6))); role = e.get("role", "bass")
        lo = [p for p in range(int(e.get("low", 21)), int(e.get("high", 35)) + 1) if p % 12 == pc]
        for p in lo[:int(e.get("count", 2))]:
            notes.append({"s": t, "e": t + L, "p": p, "role": role, "part": e.get("part") or part_of_role.get(role),
                          "v": int(e.get("vel", 90)), "s2": t, "e2": t + L, "x": ctx.bpos(t), "sec": ctx.sec(ctx.bpos(t))})
    for e in (e for e in g["events"] if e["type"] == "ring"):
        last = max(n["s2"] for n in notes); fin = [n for n in notes if n["s2"] >= last - e.get("window", 30)]
        end_ring = last + int(round(div * e.get("beats", 10)))
        for n in fin:
            n["e2"] = max(n["e2"], end_ring)
        have = {n["p"] for n in fin}; role = e.get("role", "mid")
        for p, v in e.get("add") or []:
            p = _pitch(p)
            if p not in have:
                notes.append({"s": last, "e": end_ring, "p": p, "role": role, "v": int(v),
                              "part": e.get("part") or part_of_role.get(role),
                              "s2": last + int(e.get("offset", 6)), "e2": end_ring, "x": ctx.bpos(last),
                              "sec": ctx.sec(ctx.bpos(last))})

    rz = _realism_parts(real, RF, notes, g, ctx, src) if real else None
    if rz:
        notes = rz["notes"]
    parts = {name: [] for name in g["parts"]}; pairs = {name: [] for name in g["parts"]}
    for n in notes:
        if n.get("part") in parts:
            pairs[n["part"]].append(([n["s2"], n["e2"], n["p"], n["v"]], n.get("o"), n.get("i")))
    origin, src_i = {}, {}
    for name, v in pairs.items():
        v.sort(key=lambda x: (x[0], x[1] or (-1, -1), -1 if x[2] is None else x[2]))
        parts[name] = [x[0] for x in v]; origin[name] = [x[1] for x in v]; src_i[name] = [x[2] for x in v]
    secs = [{"name": s["name"], "bar": s["bar"], "start": ctx.sec_range_ticks(s)[0],
             "end": ctx.sec_range_ticks(s)[1]} for s in ctx.secs]
    out = {"ppq": div, "parts": parts, "origin": origin, "src": src_i, "tempo": [[t, b] for t, b in tmap],
           "tempo_points": [[round(1 + t / (4 * div), 6), b] for t, b in tmap], "sections": secs,
           "end_tick": end_tick, "first_bar": ctx.fb, "bars": bars, "tempo_shaped": tempo_shaped(g),
           "stats": {"notes": sum(len(v) for v in parts.values()), "thinned": thinned, "lifted": lifted,
                     "doubled": len(extra), "tempo_points": len(tmap)}}
    if rz:
        out["realism"] = rz["report"]
        if rz["expression"]:
            out["expression"] = rz["expression"]
    if ctx.swing_skipped:
        out["swing_note"] = (f"no 8th swing in {', '.join(sorted(ctx.swing_skipped))}: its beat is three 8ths, so "
                             "those bars stay straight (options swing_grid=16 swings their 16ths)")
    return out


def _realism_parts(real, RF, notes, g, ctx, src):
    # PC-VERIFY: listen to a strings / choir / pad layer after flp_perform with flh_realism loaded - it should sound
    # played (swells, breaths, legato), the piano exactly as before; the expression curves land on the right channel
    """Run each layer part (strings, choir, pads, brass, ...) through flh_realism after everything else is placed
    (so the clash guard sees every other part's final notes). -> {notes, expression {part: spec}, report}."""
    div = ctx.div
    bpm = float((src.get("tempos") or [[0, 120.0]])[0][1]) if src.get("tempos") else 120.0
    secs = []
    for s in ctx.secs:
        a, b = ctx.sec_range_ticks(s)
        secs.append({"name": s["name"], "start": int(a), "end": int(ctx.end_tick if b is None else min(b, ctx.end_tick))})
    bpb = round(ctx.bars[0][1] / float(div), 3) if ctx.bars else 4
    expr, report = {}, {}
    for name in sorted(real):
        mine = [n for n in notes if n.get("part") == name]
        rest = [n for n in notes if n.get("part") != name]
        if not mine:
            continue
        mine.sort(key=lambda n: (n["s2"], n["p"]))
        cx = {"ppq": div, "bpm": bpm, "seed": int(g.get("seed", 1)) * 1000003 + int(_digest(name)[:6], 16),
              "genre": g.get("genre"), "beats_per_bar": bpb, "sections": secs, "part": name, "source": "perform"}
        try:
            got, ex, rep = realism_run(RF, [[n["s2"], n["e2"], n["p"], n["v"]] for n in mine], real[name], cx,
                                       others=[[n["s2"], n["e2"], n["p"], n["v"]] for n in rest])
        except Exception as e:                               # realism failed: the part plays as written
            report[name] = {"family": real[name], "error": f"{type(e).__name__}: {e}"[:160]}
            continue
        new = []
        for s_, e_, p_, v_, k in got:
            b = mine[k] if k is not None else None
            n = dict(b) if b is not None else {"o": None, "i": None, "part": name, "role": mine[0]["role"],
                                                "x": ctx.bpos(s_), "sec": ctx.sec(ctx.bpos(s_))}
            n.update(s2=s_, e2=e_, p=p_, v=v_)
            if b is not None and p_ != b["p"]:
                n["o"] = None                                # a different pitch is not the source note any more
            new.append(n)
        notes = rest + new
        report[name] = rep
        if ex:
            expr[name] = ex
    return {"notes": notes, "expression": expr, "report": report}


def _source_path(spec, base_dir=None):
    p = spec.get("source")
    if not p:
        raise ValueError("spec.source is missing (the source .mid)")
    if not os.path.isabs(p) and base_dir:
        p = os.path.join(base_dir, p)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"performance source {p} not found")
    return p


# ============================================================================= stats and diffs
def section_stats(render):
    """{section: {"bpm": average bpm, "vel": average velocity, "notes": count}} from a render."""
    div = render["ppq"]; tm = render["tempo"]; secs = render["sections"]
    starts = [s["start"] for s in secs]
    end_all = max([render["end_tick"]] + [n[0] + 1 for v in render["parts"].values() for n in v])
    out = {s["name"]: {"vel": 0, "notes": 0} for s in secs}
    held = {s["name"]: [] for s in secs}
    for part, v in sorted(render["parts"].items()):
        for n in v:
            s = secs[max(0, bisect.bisect_right(starts, n[0]) - 1)]["name"]
            out[s]["vel"] += n[3]; out[s]["notes"] += 1; held[s].append([part] + n)
    tt = [t for t, _ in tm]
    for s in secs:
        a, b = s["start"], s["end"] if s["end"] is not None else end_all
        secs_ = 0.0; x = a
        while x < b:
            k = max(0, bisect.bisect_right(tt, x) - 1)
            nx = min(b, tt[k + 1] if k + 1 < len(tt) else b)
            secs_ += (nx - x) / div * 60.0 / tm[k][1]; x = nx
        o = out[s["name"]]
        o["bpm"] = round((b - a) / div / (secs_ / 60.0), 1) if secs_ else None
        o["vel"] = round(o["vel"] / o["notes"], 1) if o["notes"] else None
        o["seconds"] = round(secs_, 1)
        o["_h"] = _digest([held[s["name"]], [p for p in tm if a <= p[0] < b]])   # any change at all
    return out


def _table(before, after, names, changed, limit=16):
    """before/after rows; long songs show only the changed sections (plus a count of the rest)."""
    rows = [f"{'section':<11} {'bpm':>13} {'avg vel':>13} {'notes':>9}"]
    show = names if len(names) <= limit else [n for n in names if n in changed][:limit]
    for nm in show:
        b, a = before.get(nm, {}), after.get(nm, {})

        def pair(k):
            x, y = b.get(k), a.get(k)
            return f"{x}" if x == y else f"{x}->{y}"
        rows.append(f"{(nm + (' *' if nm in changed else '')):<11} {pair('bpm'):>13} {pair('vel'):>13} "
                    f"{pair('notes'):>9}")
    if len(show) < len(names):
        rows.append(f"(+{len(names) - len(show)} more sections{' unchanged' if len(show) == len(changed) else ''})")
    return rows


# ============================================================================= relative edits
_PCT = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*%\s*$")
_ADD = re.compile(r"^\s*([+-]\d+(?:\.\d+)?)\s*$")
_MUL = re.compile(r"^\s*[x*×]\s*(\d+(?:\.\d+)?)\s*$")
ALIASES = {"speed": "tempo", "dynamics": "dyn", "velocity": "dyn", "vel": "dyn", "loudness": "dyn",
           "humanization": "humanize", "human": "humanize", "rubato": "curve"}
NO_SCALE = ("lead", "clip", "window", "phase", "bars", "roles", "grid", "pulse", "parts", "drum_clip", "max_gap")


def _rel(cur, ch):
    """apply one change to a number: absolute number, '+n'/'-n', '+x%'/'-x%', 'x1.2'."""
    if isinstance(ch, bool) or not isinstance(ch, (str, int, float)):
        return ch
    if isinstance(ch, (int, float)):
        return ch
    m = _PCT.match(ch)
    if m:
        return cur * (1 + float(m.group(1)) / 100.0)
    m = _ADD.match(ch)
    if m:
        return cur + float(m.group(1))
    m = _MUL.match(ch)
    if m:
        return cur * float(m.group(1))
    raise ValueError(f"change '{ch}': use a number, '+4', '-3%', 'x1.2'")


def _is_rel(ch):
    return isinstance(ch, str) and bool(_PCT.match(ch) or _ADD.match(ch) or _MUL.match(ch))


def _r(x):
    return round(x, 6) if isinstance(x, float) else x


def _rel_tree(cur, ch, key=None):
    """a relative change applied to every number in cur (dicts/lists; point lists change their values only)."""
    if isinstance(cur, bool):
        return cur
    if isinstance(cur, (int, float)):
        return _r(_rel(cur, ch))
    if isinstance(cur, dict):
        return {k: (v if k in NO_SCALE else _rel_tree(v, ch, k)) for k, v in cur.items()}
    if isinstance(cur, list):
        if cur and all(isinstance(p, list) and len(p) == 2 for p in cur):   # [[bar, value], ...]
            return [[p[0], _r(_rel(p[1], ch))] for p in cur]
        return [_rel_tree(v, ch, key) for v in cur]
    return cur


def _find_section(spec, name):
    low = str(name).lower()
    secs = spec["sections"]
    hit = [s for s in secs if s["name"].lower() == low] or [s for s in secs if s["name"].lower().startswith(low)]
    if len(hit) != 1:
        return None
    return hit[0]


def apply_changes(spec, changes, render_fn=None):
    """A copy of spec with `changes` applied: {section name (or '*'): {param: change}} or {global key: value}.
    Returns (new spec, [changed section names])."""
    if isinstance(changes, str):
        changes = json.loads(changes)
    new = copy.deepcopy(spec); touched = []
    g = normalize(spec)
    eff = {s["name"]: s for s in g["sections"]}
    for key, chg in (changes or {}).items():
        targets = new["sections"] if key in ("*", "all") else [_find_section(new, key)]
        if targets == [None]:
            if key in GLOBAL_DEFAULTS or key in ("events", "parts", "defaults", "source", "bars"):
                if _is_rel(chg) and isinstance(new.get(key, GLOBAL_DEFAULTS.get(key)), (int, float)):
                    new[key] = _r(_rel(new.get(key, GLOBAL_DEFAULTS.get(key)), chg))
                elif isinstance(chg, dict) and isinstance(new.get(key), dict):
                    new[key] = _merge(new[key], chg)
                else:
                    new[key] = copy.deepcopy(chg)
                touched.append("(" + key + ")")
                continue
            if key == "style":
                raise ValueError("a style is chosen at setup: flp_perform_setup(project, style='expressive') "
                                 "(section edits stay relative on top of it)")
            raise ValueError(f"no section '{key}' (have: {[s['name'] for s in new['sections']]}); use '*' for all")
        if not isinstance(chg, dict):
            raise ValueError(f"changes for {key}: give {{param: change}}, e.g. {{\"tempo\": \"-3%\"}}")
        for sec in targets:
            e = eff[sec["name"]]
            for param, c in chg.items():
                param = ALIASES.get(param, param)
                if param == "tempo":                     # speed: "-3%" = 3% slower; 1.02 = speed factor; "+2" = bpm
                    if _is_rel(c) and _ADD.match(c) and render_fn:
                        st = render_fn(new)[sec["name"]]["bpm"]
                        c = st + float(c)
                        param = "bpm"
                    elif _is_rel(c):
                        sp = _rel(1.0, c); sec["stretch"] = _r(e["stretch"] / sp)
                        continue
                    else:
                        sec["stretch"] = _r(1.0 / float(c)); continue
                if param == "bpm":                       # target average bpm for the section
                    if not render_fn:
                        raise ValueError("bpm changes need a render")
                    target = float(c) if not _is_rel(c) else _rel(render_fn(new)[sec["name"]]["bpm"], c)
                    for _ in range(4):
                        cur = render_fn(new)[sec["name"]]["bpm"]
                        if not cur or abs(cur - target) < 0.05:
                            break
                        sec["stretch"] = _r(sec.get("stretch", e["stretch"]) * cur / target)
                    continue
                if param == "dyn" and sec.get("dyn", e.get("dyn")) is None and not isinstance(c, (list, dict)):
                    # the notes' own velocities: '+4' / '-3' shift, '+10%' / 'x1.1' scale, a number = new average
                    if _is_rel(c) and _ADD.match(c):
                        sec["vel_offset"] = _r(float(sec.get("vel_offset", e["vel_offset"])) + float(c))
                    elif _is_rel(c):
                        sec["vel_scale"] = _r(_rel(float(sec.get("vel_scale", e["vel_scale"])), c))
                    elif isinstance(c, (int, float)) and not isinstance(c, bool):
                        if not render_fn:
                            raise ValueError("an average-velocity target needs a render")
                        cur = render_fn(new)[sec["name"]]["vel"] or c
                        sec["vel_offset"] = _r(float(sec.get("vel_offset", e["vel_offset"])) + float(c) - cur)
                    else:
                        raise ValueError(f"dyn change '{c}': use '+4', '-10%', 'x1.1' or an average velocity")
                    continue
                if param == "dyn" and isinstance(c, (int, float)) and not isinstance(c, bool):
                    pts = e["dyn"]
                    if isinstance(pts, list) and pts and isinstance(pts[0], list):
                        mean = sum(p[1] for p in pts) / len(pts)
                        sec["dyn"] = [[p[0], _r(p[1] + c - mean)] for p in pts]
                    else:
                        sec["dyn"] = c
                    continue
                if param not in e and param not in SECTION_DEFAULTS:
                    raise ValueError(f"unknown section parameter '{param}' (have: tempo, bpm, "
                                     f"{', '.join(sorted(k for k in SECTION_DEFAULTS))})")
                cur = sec.get(param, e.get(param))
                if isinstance(c, dict) and isinstance(cur, dict):
                    val = copy.deepcopy(e.get(param) or {})
                    for k2, c2 in c.items():
                        val[k2] = _rel_tree(val.get(k2, 0), c2) if _is_rel(c2) else copy.deepcopy(c2)
                    sec[param] = val
                elif _is_rel(c):
                    if cur is None:
                        raise ValueError(f"{sec['name']}: no {param} to change relatively; give a value")
                    sec[param] = _rel_tree(copy.deepcopy(cur), c, param)
                else:
                    sec[param] = copy.deepcopy(c)
            if sec["name"] not in touched:
                touched.append(sec["name"])
    normalize(new)
    return new, touched


# ============================================================================= reading a project
def _project_read(project):
    """What auto setup needs from a project file: ppq, notes per channel [[s, e, key, vel]] (song time, as FL plays
    them), tempo map, channels {index: {name, plugin, type, sample}}, meters, markers, section clips, audio clips."""
    d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
    if "_song_x" in H:                                   # notes with FL's own fields (slide flag, pan, ...)
        notes_x, tempos = H["_song_x"](d, ev)
    else:
        notes_x, tempos = [n + (False, None, None) for n in H["_song"](d, ev)[0]], H["_song"](d, ev)[1]
    if notes_x and "_sane_span" in H:                    # a corrupt far note must not make setup run for minutes
        H["_sane_span"](max(n[0] + n[1] for n in notes_x), P, "a note")
    _, chans, _ = H["_structure"](d)
    try:
        types = H["_channel_types"](d, ev)
    except Exception:
        types = {}
    samples, cur = {}, None
    for e in ev:
        if e["id"] == 64:
            cur = struct.unpack("<H", H["_payload"](d, e)[:2])[0]
        elif e["id"] == 196 and cur is not None and cur not in samples:
            samples[cur] = os.path.basename(H["_text16"](H["_payload"](d, e)).replace("\\", "/"))
    per, fl, prov = {}, {}, {}
    heard = _heard_notes(d, ev)
    if heard is None:                                    # no pattern clips to trace notes to: the plain song view
        heard = [(int(n[0]), int(n[1]), int(n[2]), int(n[3]), int(n[4]), None, n[7]) for n in notes_x]
    rows = {}
    for pos, ln, ch, key, vel, pv, rec in heard:
        row = [pos, pos + max(1, ln), key, max(1, min(127, vel))]
        if pv is not None:                               # where the note lives: clip, record, FL's own fields
            pv = dict(pv, ext=int(rec[3]) - (row[1] - row[0]), fl=H["_fl_fields"](rec),
                      **({"vr": int(rec[11])} if int(rec[11]) != row[3] else {}))
        rows.setdefault(ch, []).append((row, pv))
        if rec is not None:                              # FL's fields of that note, by (song tick, key)
            fl.setdefault(ch, {}).setdefault((pos, key), []).append(H["_fl_fields"](rec))
    for ch, v in rows.items():
        v.sort(key=lambda x: (x[0], (x[1] or {}).get("clip") or [], (x[1] or {}).get("rid", -1)))
        per[ch] = [x[0] for x in v]; prov[ch] = [x[1] for x in v]
    try:
        info = H["flp_project_info"](project)
    except Exception:
        info = {}
    num = int(info.get("time_sig_num") or 4); den = int(info.get("time_sig_beat") or 4)
    markers = _markers(d, ev)
    meters = [(0, num, den)]
    for m in markers:
        if m["signature"] and m["num"] and m["den"]:
            meters = [x for x in meters if x[0] != m["tick"]] + [(m["tick"], m["num"], m["den"])]
    meters.sort()
    pats = H["_patterns"](d, ev); clips, audio = [], []
    try:
        for _, _, r in H["_clips"](d, ev):
            pos, = struct.unpack("<I", r[0:4]); item, = struct.unpack("<H", r[6:8]); ln, = struct.unpack("<I", r[8:12])
            flags, = struct.unpack("<H", r[18:20])
            pid = item - 0x5000; nm = (pats.get(pid) or {}).get("name") or ""
            if not flags & 0x2000 and nm.strip().lower() != "tempo" and (pats.get(pid) or {}).get("notes"):
                clips.append((pos, ln, nm or f"Pattern {pid}"))
    except Exception:
        pass
    try:
        audio = [c["name"] for c in H["flp_playlist"](project)["clips"] if c.get("kind") == "audio" and not c.get("muted")]
    except Exception:
        audio = []
    ch_info = {}
    for i, c in chans.items():
        ch_info[i] = {"name": c.get("name"), "plugin": c.get("plugin"), "type": types.get(i), "sample": samples.get(i)}
    for i in per:
        ch_info.setdefault(i, {"name": None, "plugin": None, "type": types.get(i), "sample": samples.get(i)})
    try:
        qbar = H["_qbar"](info)
    except Exception:
        qbar = num * 4.0 / den
    return {"ppq": P, "notes": per, "prov": prov, "fl": fl, "muted_only": max(0, len(notes_x) - len(heard)),
            "tempos": [(int(t), float(b)) for t, b in tempos],
            "channels": ch_info,
            "meters": meters, "markers": markers, "clips": sorted(clips), "audio": audio, "qbar": qbar,
            "time_sig": (num, den)}


# ----------------------------------------------------------------------------- provenance: where each note lives
# Every note the song plays comes from one record of one pattern, heard through one playlist clip. Auto setup keeps
# that link for every source note (clip, record id, FL's own fields), the engine keeps it for every performed note,
# and the write puts each performed note back into the exact pattern and clip-relative position it came from. A
# pattern several clips play (a repeated chorus, a layer stacked twice) holds ONE copy: the first clip that plays a
# record decides it; the other clips' performances of that record are reported (or get their own pattern copy with
# unique_repeats=True).
def _clip_table(d, ev):
    """The current arrangement's pattern clips: [{"i" (record index), "pos", "len", "pid", "ws", "we" (window in
    the pattern), "muted", "track", "ref" [pos, track, pid, k]}] (k: its place among clips at the same pos/track)."""
    try:
        k_, size, recs = H["_playlist_at"](d, ev)
    except Exception:
        return None
    PB = H["PATTERN_BASE"]; mute = H.get("CLIP_MUTE", 0x2000); out = []; seen = {}
    for i, r in enumerate(recs):
        item, = struct.unpack("<H", r[6:8])
        if struct.unpack("<H", r[4:6])[0] != PB or item < PB:
            continue
        pos, = struct.unpack("<I", r[0:4]); ln, = struct.unpack("<I", r[8:12]); tr, = struct.unpack("<H", r[12:14])
        ws = H["_clip_offsets"](r)[0]; pid = item - PB
        k = seen.get((pos, tr), 0); seen[(pos, tr)] = k + 1
        out.append({"i": i, "pos": pos, "len": ln, "pid": pid, "ws": ws, "we": ws + ln,
                    "muted": bool(struct.unpack("<H", r[18:20])[0] & mute), "track": tr, "ref": [pos, tr, pid, k]})
    return out


def _heard_notes(d, ev):
    """[(song tick, length, channel, key, vel, {"clip": ref, "rid": record index}, FL record)] of every note an
    unmuted pattern clip plays (the same window rule as the server's song reader); None without pattern clips."""
    clips = _clip_table(d, ev)
    if not clips:
        return None
    pats = H["_patterns"](d, ev); out = []
    for c in clips:
        pat = pats.get(c["pid"])
        if c["muted"] or not pat:
            continue
        for j, n in enumerate(pat["notes"]):
            if c["ws"] <= n[0] < c["we"]:
                out.append((c["pos"] + n[0] - c["ws"], min(n[3], c["we"] - n[0]), n[2], n[4], n[11],
                            {"clip": list(c["ref"]), "rid": j}, n))
    return out


def _pack_where(prov):
    """Provenance of a part's source notes (in source order) -> compact JSON: clips table, run-length clip and
    field indexes, record ids, and the rare length extension / raw velocity by note index."""
    clips, cidx, fls, fidx = [], {}, [], {}
    c_rle, f_rle, rid, ext, vr, dq = [], [], [], {}, {}, {}
    for i, pv in enumerate(prov):
        if pv is None:
            raise ValueError("a source note without provenance")
        ck = tuple(pv["clip"])
        if ck not in cidx:
            cidx[ck] = len(clips); clips.append(list(ck))
        fk = tuple(pv["fl"])
        if fk not in fidx:
            fidx[fk] = len(fls); fls.append(list(fk))
        for rle, v in ((c_rle, cidx[ck]), (f_rle, fidx[fk])):
            if rle and rle[-1][0] == v:
                rle[-1][1] += 1
            else:
                rle.append([v, 1])
        rid.append(int(pv["rid"]))
        if pv.get("ext"):
            ext[str(i)] = int(pv["ext"])
        if pv.get("vr") is not None:
            vr[str(i)] = int(pv["vr"])
        if pv.get("dq"):
            dq[str(i)] = int(pv["dq"])
    return {"n": len(prov), "clips": clips, "c": c_rle, "rid": rid, "fls": fls, "f": f_rle, "ext": ext, "vr": vr,
            **({"dq": dq} if dq else {})}


def _unpack_where(w):
    """-> [{"clip", "rid", "fl", "ext", "vr"}] per source note of the part."""
    def runs(rle):
        out = []
        for v, n in rle:
            out += [v] * int(n)
        return out
    cs, fs = runs(w["c"]), runs(w["f"])
    if not (len(cs) == len(fs) == len(w["rid"]) == int(w["n"])):
        raise ValueError("the performance's note map is damaged: run flp_perform_setup(project) again")
    dq = w.get("dq") or {}
    return [{"clip": w["clips"][c], "rid": r, "fl": w["fls"][f], "ext": int(w["ext"].get(str(i), 0)),
             "vr": w["vr"].get(str(i)), "dq": int(dq.get(str(i), 0))} for i, (c, f, r) in enumerate(zip(cs, fs, w["rid"]))]


def _map_source(path, part_notes):
    """Match the notes of a written source .mid back to their provenance. part_notes: {track: [(note [s, e, key,
    vel], provenance)]} -> {track: packed provenance in load_source order} (None if any note can't be matched)."""
    src = load_source(path); queues = {}
    for t, rows in part_notes.items():
        q = queues.setdefault(t, {})
        for row, pv in rows:
            q.setdefault(tuple(int(x) for x in row), []).append(pv)
    got = {t: [] for t in part_notes}
    for n in src["notes"]:
        if n["t"] not in queues:
            continue
        stack = queues[n["t"]].get((n["s"], n["e"], n["p"], n["v"]))
        if not stack:
            return None
        got[n["t"]].append(stack.pop(0))
    if any(v for q in queues.values() for v in q.values()):
        return None
    try:
        return {t: _pack_where(v) for t, v in got.items()}, src["sha"]
    except ValueError:                                     # a note the song reader couldn't trace to a clip
        return None


def _markers(d, ev):
    """Playlist (arrangement) markers: [{"tick", "name", "signature", "num", "den", "action"}]. Markers live after
    the first ArrangementNew event (99); a time signature marker has bit 0x08000000 set in its position."""
    if "_markers" in H:                                  # the server's parser (same rules), shared by every tool
        return H["_markers"](d, ev)
    out, inarr, cur = [], False, None
    for e in ev:
        i = e["id"]
        if i == 99:
            if inarr:
                break                                    # only the first arrangement
            inarr = True; continue
        if not inarr:
            continue
        p = H["_payload"](d, e)
        if i == 148 and len(p) == 4:
            v, = struct.unpack("<I", p)
            cur = {"tick": v & 0x07FFFFFF, "signature": bool(v & 0x08000000), "action": v >> 28, "name": "",
                   "num": None, "den": None}
            out.append(cur)
        elif cur is not None and i == 33:
            cur["num"] = p[0]
        elif cur is not None and i == 34:
            cur["den"] = p[0]
        elif cur is not None and i == 205:
            cur["name"] = H["_text16"](p)
    return out


def _sounding(notes, tol):
    """average number of notes sounding at each onset (held notes count): the part's real polyphony."""
    st = sorted(n[0] for n in notes); en = sorted(n[1] for n in notes); onsets, last = [], None
    for s_ in st:
        if last is None or s_ - last > tol:
            onsets.append(s_); last = s_
    return sum(bisect.bisect_right(st, t + tol) - bisect.bisect_right(en, t) for t in onsets) / max(1, len(onsets))


def _chord_size(notes, tol):
    if not notes:
        return 0
    groups, last = 0, None
    for s, *_ in sorted(notes):
        if last is None or s - last > tol:
            groups += 1; last = s
    return len(notes) / groups


DRUM_WORDS = ("kick", "snare", "hat", "hihat", "hi-hat", "clap", "perc", "drum", "tom", "cymbal", "crash", "ride",
              "shaker", "rim", "snap", "conga", "bongo", "tamb", "cowbell", "break", "kit", "fpc", "hh")
DRUM_PLUGINS = ("fpc", "drumaxx", "drumpad", "drumsynth", "battery", "ezdrummer", "superior drummer", "addictive drums",
                "ssd5", "slicex", "fruity slicer", "drumgod", "xo", "drum")
BASS_WORDS = ("bass", "sub", "808")
PAD_WORDS = ("pad", "string", "choir", "atmos", "drone", "ambient", "texture", "ensemble", "warmth")
MEL_WORDS = ("lead", "melody", "vocal", "vox", "solo", "flute", "whistle", "sax", "trumpet", "violin", "oboe",
             "clarinet", "hook", "topline")
ACC_WORDS = ("accomp", "chord", "comp", "arp", "rhythm", "harmon", "stab", "backing")
KEYS_WORDS = ("piano", "keys", "rhodes", "wurli", "grand", "upright", "epiano", "e-piano", "harp", "celest")
GM_CORE = {35, 36, 37, 38, 39, 40, 42, 44, 46, 49, 51, 57}


def _words(text, words):
    return [w for w in words if re.search(r"(^|[^a-z])" + re.escape(w), text)]


def detect_role(info, notes, P):
    """(role word, engine role, split, why) for one channel: melody / accompaniment / bass / drums / pad / keys
    (one keyboard part playing melody, harmony and bass, voiced by skyline)."""
    text = " ".join(str(x or "") for x in (info.get("name"), info.get("plugin"), info.get("sample"))).lower()
    pitches = sorted(n[2] for n in notes); lens = sorted(n[1] - n[0] for n in notes); N = len(notes)
    med_p = pitches[N // 2]; med_len = lens[N // 2]; chord = _chord_size(notes, P // 16); poly = _sounding(notes, P // 16)
    distinct = len(set(pitches))
    p10, p90 = pitches[N // 10], pitches[min(N - 1, (9 * N) // 10)]
    drum_w = _words(text, DRUM_WORDS); bass_w = _words(text, BASS_WORDS)
    melodic_w = _words(text, PAD_WORDS + MEL_WORDS + KEYS_WORDS)
    if drum_w and not (bass_w and "kick" not in drum_w and distinct > 3) and not melodic_w:
        return "drums", "drums", False, f"name: {drum_w[0]}"
    if any(w in text for w in ("fpc", "drumaxx", "battery", "ezdrummer", "superior drummer", "addictive drums",
                                "drumgod", "slicex", "fruity slicer")):
        return "drums", "drums", False, "drum plugin"
    gm = sum(1 for p in pitches if p in GM_CORE) / N
    if not melodic_w and not bass_w and 35 <= pitches[0] and pitches[-1] <= 81 and gm >= 0.6 and 2 <= distinct <= 16 \
            and med_len <= P // 2:
        return "drums", "drums", False, "GM drum notes"
    if info.get("type") == 0 and distinct <= 2 and med_len <= P and not bass_w and not melodic_w:
        return "drums", "drums", False, "one-shot sampler"
    if bass_w and not _words(text, KEYS_WORDS):
        return "bass", "bass", False, f"name: {bass_w[0]}"
    if med_p <= 47 and chord <= 1.3:
        return "bass", "bass", False, "low and monophonic"
    if _words(text, PAD_WORDS):
        return "pad", "pad", False, f"name: {_words(text, PAD_WORDS)[0]}"
    if med_len >= 2 * P and chord >= 2:
        return "pad", "pad", False, "long sustained chords"
    low = sum(1 for p in pitches if p < 55) / N; high = sum(1 for p in pitches if p >= 60) / N
    keys_w = _words(text, KEYS_WORDS)
    if poly >= 1.8 and p90 - p10 >= 19 and low >= 0.1 and high >= 0.2 or keys_w and poly >= 1.5 and p90 - p10 >= 15:
        return "keys", "mid", True, "wide polyphonic keyboard part (melody voiced over harmony)"
    if _words(text, MEL_WORDS):
        return "melody", "mel", False, f"name: {_words(text, MEL_WORDS)[0]}"
    if _words(text, ACC_WORDS):
        return "accompaniment", "mid", False, f"name: {_words(text, ACC_WORDS)[0]}"
    if chord <= 1.25 and med_p >= 57:
        return "melody", "mel", False, "high and monophonic"
    return "accompaniment", "mid", False, "chords / inner parts"


QUANT_GRIDS = ((4, "1/16"), (3, "1/8 triplet"), (8, "1/32"), (6, "1/16 triplet"))
SLIDE = 0x08                                             # FL note flag: slide note (the server's NOTE_SLIDE)


def quantize_plan(notes, P, bars, aligned=False):
    """Does a part look humanized (onsets near, but mostly not on, a grid)? -> (snapped notes, report) or
    (None, reason). Grid positions count from each bar start (so 7/8 bars work); far-off notes stay where they are.
    aligned=True: the snapped notes in the input's order (note i -> snapped note i), not sorted."""
    if len(notes) < 8:
        return None, "too few notes"
    bst = [b[0] for b in bars]

    def off(t, u):
        bs = bst[max(0, bisect.bisect_right(bst, t) - 1)] if bst else 0
        r = (t - bs) % u
        return -r if r <= u / 2 else u - r                    # signed move to the nearest grid point
    for per_q, label in QUANT_GRIDS:
        u = P / per_q; tol = min(P / 12.0, u / 4.0)
        ds = [off(n[0], u) for n in notes]
        exact = sum(1 for d in ds if abs(d) < 0.5) / len(ds)
        near = sum(1 for d in ds if abs(d) <= tol) / len(ds)
        if exact >= 0.9:
            return None, f"already on a {label} grid"
        if near >= 0.9:
            if exact > 0.75:
                return None, f"close enough to a {label} grid"
            out, moved = [], 0
            for n, d in zip(notes, ds):
                dd = int(round(d)) if abs(d) <= tol else 0
                moved += bool(dd)
                out.append([max(0, n[0] + dd), max(max(0, n[0] + dd) + 1, n[1] + dd), n[2], n[3]])
            return (out if aligned else sorted(out)), {"grid": label, "moved": moved,
                                 "avg_offset_ms_at_120": round(sum(abs(d) for d in ds) / len(ds) / P * 500, 1)}
    return None, "not grid-based (free timing kept)"


def _bars_for(meters, end_tick, P):
    bars, t, mi = [], 0, 0
    while t < end_tick:
        while mi + 1 < len(meters) and meters[mi + 1][0] <= t:
            mi += 1
        bl = meters[mi][1] * (P * 4 // meters[mi][2]); bars.append((t, bl)); t += bl
    return bars or [(0, 4 * P)]


def _bpos(bars, t, fb=1):
    bst = [b[0] for b in bars]; i = max(0, bisect.bisect_right(bst, t) - 1)
    return i + fb + (t - bars[i][0]) / bars[i][1]


def _uniq(names):
    out, seen = [], {}
    for n in names:
        k = n.lower(); seen[k] = seen.get(k, 0) + 1
        out.append(n if seen[k] == 1 else f"{n} {seen[k]}")
    return out


def detect_sections(project, pr, bars, journal, given=None):
    """[(tick, name)] and where they came from: given > journal > playlist markers > section clips > 8-bar blocks."""
    P = pr["ppq"]; qb = pr["qbar"]
    end = max(n[1] for v in pr["notes"].values() for n in v)
    if isinstance(given, (int, float)) and not isinstance(given, bool):
        block, given = int(given), None
    else:
        block = 8
    if given:
        secs = [(int(round((float(s["bar"]) - 1) * qb * P)) if isinstance(s, dict) else int(round((float(s) - 1) * qb * P)),
                s.get("name") if isinstance(s, dict) else f"Bar {s}", s if isinstance(s, dict) else {}) for s in given]
        return secs, "given"
    js = sorted(journal.get("sections") or [], key=lambda s: float(s["bar"]))
    if len(js) >= 1 and all(s.get("name") for s in js):
        return [(int(round((float(s["bar"]) - 1) * qb * P)), s["name"], {}) for s in js], "journal sections"
    mk = [m for m in pr["markers"] if not m["signature"] and not m["action"] and m["tick"] < end]
    if len(mk) >= 2 or (mk and mk[0]["tick"] > 0):
        return [(m["tick"], m["name"] or f"Marker {i + 1}", {}) for i, m in enumerate(mk)], "playlist markers"
    clips = [c for c in pr["clips"] if c[0] < end]
    if len({c[2] for c in clips}) >= 2:
        union, last_end, total = 0, -1, 0
        for pos, ln, _ in clips:
            total += ln; a = max(pos, last_end); b = pos + ln
            if b > a:
                union += b - a
            last_end = max(last_end, b)
        if union and total / union <= 1.35:                  # one section clip at a time (not stacked loops)
            out = []
            for pos, ln, nm in clips:
                if not out or pos > out[-1][0]:
                    out.append((pos, nm, {}))
            return out, "section pattern clips"
    out, i = [], 0
    while i < len(bars) and bars[i][0] < end:
        j = min(len(bars), i + block)
        out.append((bars[i][0], "Song" if len(bars) <= block else f"Bar {j}" if j == i + 1 else f"Bars {i + 1}-{j}", {}))
        i = j
    return out, f"{block}-bar blocks"


def build_auto_spec(project, style="natural", parts=None, sections=None, quantize=None, options=None,
                    allow_tempo_with_audio=False, reuse=None):
    """-> (spec, original_spec or None, report): a performance spec read from the project itself (see docstring)."""
    style = style or "natural"
    if style not in STYLES:
        raise ValueError(f"style must be one of {sorted(STYLES)}")
    opts = dict(options or {})
    pr = _project_read(project); P = pr["ppq"]
    chans = {c: v for c, v in sorted(pr["notes"].items()) if v and pr["channels"].get(c, {}).get("type") not in (4, 5)}
    if not chans:
        if pr.get("muted_only"):
            raise ValueError(f"nothing to perform: the song's {pr['muted_only']} notes are all in muted clips "
                             "(unmute them with flp_playlist action='unmute' first)")
        raise ValueError("the project has no notes on instrument channels")
    want, force_role = None, {}
    if isinstance(parts, dict):
        for k, r in parts.items():
            force_role[str(k).lower()] = r
        want = list(parts)
    elif parts:
        want = [parts] if isinstance(parts, (str, int)) else list(parts)
    if want is not None:
        sel = {}
        for w in want:
            hit = [c for c in chans if str(c) == str(w) or str(pr["channels"][c].get("name") or "").lower() == str(w).lower()]
            if not hit:
                raise ValueError(f"part {w!r}: no channel with notes by that index or name (have: "
                                 f"{ {c: pr['channels'][c].get('name') for c in chans} })")
            sel[hit[0]] = chans[hit[0]]
        chans = dict(sorted(sel.items()))
    end = max(n[1] for v in chans.values() for n in v)
    bars = _bars_for(pr["meters"], end, P)
    names = _uniq([pr["channels"][c].get("name") or pr["channels"][c].get("plugin") or f"Channel {c}" for c in chans])
    preset = STYLES[style]
    detected, part_spec, tracks, orig_tracks, qrep, orig_fl = {}, {}, [], [], {}, {}
    jch = (_journal(project).get("channels") or {}) if H else {}
    src_rows, orig_rows = {}, {}                             # track -> [(note, provenance)] for the note maps
    for i, (c, notes) in enumerate(sorted(chans.items())):
        name = names[i]
        word, role, split, why = detect_role(pr["channels"][c], notes, P)
        fr = force_role.get(str(c)) or force_role.get(name.lower())
        if fr:
            word = {"mel": "melody", "mid": "accompaniment"}.get(str(fr).lower(), str(fr).lower())
            if word not in ("melody", "accompaniment", "bass", "drums", "pad", "keys"):
                raise ValueError(f"role {fr!r}: use melody, accompaniment, bass, drums, pad or keys")
            role = {"melody": "mel", "accompaniment": "mid", "keys": "mid"}.get(word, word)
            split, why = word == "keys", "given"
        p = {"track": i + 1, "role": role, "channel": int(c)}
        fam = None if role == "drums" else _part_family(pr["channels"][c], jch.get(str(c)) or {})
        if fam in realism_families():                         # a layer: flh_realism plays it (when it's loaded)
            p["family"] = fam
        if split:
            p["split"] = "skyline"
            if preset.get("keys_voicing"):
                p["voicing"] = copy.deepcopy(preset["keys_voicing"])
        part_spec[name] = p
        detected[name] = {"channel": int(c), "role": word, "why": why, "notes": len(notes)}
        if p.get("family"):
            detected[name]["family"] = p["family"]
        clean = notes
        if quantize is True or (quantize is None and style != "none"):
            q, rep = quantize_plan(notes, P, bars, aligned=True)
            if q is not None:
                clean = sorted(q); qrep[name] = rep
        else:
            q = None
        # FL's own note fields (slide flag, pan, fine pitch, ...) by source note, so a written performance keeps them
        have = {k: list(v) for k, v in (pr.get("fl") or {}).get(c, {}).items()}
        by_src, by_orig = {}, {}
        for i, nt in enumerate(notes):
            stack = have.get((nt[0], nt[2]))
            f = stack.pop(0) if stack else None
            if f is not None:
                by_orig[(nt[0], nt[2])] = f
                by_src[((q[i] if q is not None else nt)[0], nt[2])] = f
        p["fl"] = _fl_pack(by_src); orig_fl[name] = _fl_pack(by_orig)
        if reuse and (reuse.get("fl") or {}).get(int(c)):
            p["fl"] = reuse["fl"][int(c)]                    # the project holds a performance: keep the setup's fields
        pv = (pr.get("prov") or {}).get(c) or [None] * len(notes)
        if q is not None:                                    # dq: how far the grid snap moved it from its record
            pv = [dict(x, dq=a[0] - b[0]) if x is not None and a[0] != b[0] else x for x, a, b in zip(pv, notes, q)]
        src_rows[p["track"]] = sorted(zip(q if q is not None else notes, pv), key=lambda x: (
            x[0], (x[1] or {}).get("clip") or [], (x[1] or {}).get("rid", -1)))
        orig_rows[p["track"]] = [(a, {k: v for k, v in x.items() if k != "dq"} if x else x) for a, x in zip(notes, pv)]
        tracks.append((name, clean)); orig_tracks.append((name, notes))
    lines = [(sorted(n[2] for n in chans[x["channel"]])[len(chans[x["channel"]]) // 2], nm)
             for nm, x in detected.items() if x["why"] == "high and monophonic"]
    for _, nm in sorted(lines)[:-1]:                         # several monophonic lines: the highest is the tune
        part_spec[nm]["role"] = "mid"
        detected[nm].update(role="accompaniment", why="monophonic line under the melody")
    secs, sec_from = detect_sections(project, pr, bars, _journal(project), sections)
    secs = sorted(secs, key=lambda s: s[0])
    if secs and secs[0][0] > 0:
        if any(n[0] < secs[0][0] for v in chans.values() for n in v):
            secs.insert(0, (0, "Start", {}))
        else:
            secs[0] = (0,) + secs[0][1:]
    spec_secs, last = [], None
    for (t, nm, extra), un in zip(secs, _uniq([s[1] for s in secs])):
        b = round(_bpos(bars, t), 6)
        if last is not None and b <= last:
            continue
        sec = {"name": un, "bar": b}
        st = extra.get("style") if isinstance(extra, dict) else None
        if st:
            if st not in STYLES:
                raise ValueError(f"section {un}: style must be one of {sorted(STYLES)}")
            sec.update(copy.deepcopy(STYLES[st].get("defaults") or {}))
        spec_secs.append(sec); last = b
    stem = os.path.splitext(os.path.basename(project))[0]; pdir = os.path.dirname(os.path.abspath(project))
    src_name = stem + ".perform_source.mid"; orig_name = stem + ".perform_original.mid"
    maps = {}                                                 # source file -> its note map (see _map_source)
    if reuse and reuse["channels"] == [p["channel"] for p in part_spec.values()] \
            and os.path.isfile(os.path.join(pdir, reuse["source"])) and (reuse.get("maps") or {}).get(reuse["source"]):
        src_name = reuse["source"]; qrep = {}                 # the project holds a render of this source
        quantized = bool(reuse.get("original")) and os.path.isfile(os.path.join(pdir, reuse["original"]))
        if quantized:
            orig_name = reuse["original"]
        maps = {k: v for k, v in reuse["maps"].items() if k in (src_name, orig_name)}
    else:
        reuse = None
        write_source(os.path.join(pdir, src_name), P, tracks, pr["meters"], pr["tempos"])
        quantized = bool(qrep)
        if quantized:
            write_source(os.path.join(pdir, orig_name), P, orig_tracks, pr["meters"], pr["tempos"])
        names_by_track = {p["track"]: nm for nm, p in part_spec.items()}
        for fname, rows in ((src_name, src_rows),) + (((orig_name, orig_rows),) if quantized else ()):
            got = _map_source(os.path.join(pdir, fname), rows)
            if got:
                maps[fname] = {"sha": got[1], "parts": {names_by_track[t]: w for t, w in got[0].items()}}
    spec = {"format": FORMAT, "auto": {"style": style, "sections_from": sec_from, "options": opts,
                                       **({"original": orig_name} if quantized else {})},
            "source": src_name, "tick_ppq": TICK_REF, "base_bpm": "source", "first_bar": 1, "song_start_bar": 1,
            "roles": {"method": "tracks"}, "parts": part_spec,
            "defaults": copy.deepcopy(preset.get("defaults") or {}), "sections": spec_secs, "events": []}
    if allow_tempo_with_audio:
        spec["allow_tempo_with_audio"] = True
    if preset.get("tempo"):
        spec["tempo"] = copy.deepcopy(preset["tempo"])
    d = spec["defaults"]
    if opts.get("swing") is not None or opts.get("swing_grid"):
        sw = d.setdefault("swing", {"ratio": 0.6, "grid": 8})
        if opts.get("swing") is not None:
            sw["ratio"] = float(opts["swing"])
        if opts.get("swing_grid"):
            sw["grid"] = int(opts["swing_grid"])
    if opts.get("late_parts") or opts.get("late") is not None:
        late = float(opts.get("late", 12)); hz = d.setdefault("humanize", {})
        if opts.get("late_parts"):
            lp = [str(x).lower() for x in opts["late_parts"]]
            hz["lead"] = {nm: late for nm, p in part_spec.items()
                          if nm.lower() in lp or str(p["channel"]) in lp}
        else:
            hz["lead"] = {r: late for r in ("mel", "mid", "pad")}
    # expressive: broaden into each section's last pulse, final ritardando
    end_bar = round(_bpos(bars, end), 6)
    if preset.get("phrase_end"):
        amt = float(preset["phrase_end"][0])
        for i, s in enumerate(spec_secs[:-1]):
            nb = spec_secs[i + 1]["bar"]; t_end = _tick_of(bars, nb)
            bi = max(0, bisect.bisect_right([b[0] for b in bars], max(0, t_end - 1)) - 1)
            pre = round(_bpos(bars, max(0, t_end - min(P, bars[bi][1] // 2))), 6)
            if pre > s["bar"]:
                s["curve"] = [[s["bar"], 1.0], [pre, 1.0], [nb, amt]]
    if preset.get("final_rit"):
        nb, amt = preset["final_rit"]
        a = max(spec_secs[-1]["bar"], round(end_bar - float(nb), 6))
        if end_bar > a:
            spec["events"].append({"type": "rit", "from": a, "to": end_bar, "amount": float(amt)})
    orig = None
    if quantized:
        orig = copy.deepcopy(spec); orig["source"] = orig_name; orig["defaults"] = {}; orig["events"] = []
        orig["auto"] = dict(orig["auto"], style="none", note="the original notes, before the grid snap")
        orig["auto"].pop("original", None)
        orig["sections"] = [{"name": s["name"], "bar": s["bar"]} for s in spec_secs]
        orig.pop("tempo", None)
        for nm_, p in orig["parts"].items():
            p.pop("voicing", None)
            p["fl"] = orig_fl.get(nm_) or p.get("fl")
    drums = [n for n, x in detected.items() if x["role"] == "drums"]
    warn = []
    if style == "expressive" and drums:
        warn.append(f"expressive rubato bends the whole song's tempo, drums ({', '.join(drums)}) included; their "
                    "own timing stays exact. For a steady groove use natural, tight, swing or laid-back")
    report = {"detected": detected, "sections_from": sec_from, "quantized": qrep, "warnings": warn,
              "meters": [f"{n}/{d_}" + (f" @bar {round(_bpos(bars, t), 3)}" if t else "") for t, n, d_ in pr["meters"]],
              "audio_clips": pr["audio"], "tempo": _tempo_summary(pr["tempos"]), "reused_source": bool(reuse),
              "maps": maps}
    return spec, orig, report


def _part_family(info, jc):
    """A channel's instrument family from its name, the journal's name for it, its preset and plugin."""
    return layer_family(info.get("name"), jc.get("name"), jc.get("sound"), info.get("sample"), info.get("plugin"),
                        jc.get("role") if jc.get("role") in ("strings", "choir", "pad", "brass", "bell") else None)


def _fl_pack(by_note):
    """{(source tick, key): FL fields} -> {"default": the most common fields, "notes": {"tick:key": fields} for the
    notes that differ} (compact: most notes share their channel's fields)."""
    if not by_note:
        return None
    count = {}
    for f in by_note.values():
        count[tuple(f)] = count.get(tuple(f), 0) + 1
    dflt = max(count, key=lambda k: (count[k], k))
    return {"default": list(dflt), "notes": {f"{t}:{k}": list(f) for (t, k), f in sorted(by_note.items())
                                             if tuple(f) != dflt}}


def _fl_of(fl, origin):
    """FL fields for a rendered note that came from source note `origin` (tick, key); None for new notes."""
    if not fl:
        return None
    if origin is not None:
        f = (fl.get("notes") or {}).get(f"{origin[0]}:{origin[1]}")
        if f is not None:
            return f
    f = list(fl.get("default") or [])
    if f and origin is None:
        f[0] = f[0] & ~SLIDE                             # an added note (doubling, boom) never inherits a slide
    return f or None


def _tick_of(bars, b, fb=1):
    i = int(math.floor(b)) - fb; frac = b - math.floor(b)
    if i >= len(bars):
        return bars[-1][0] + bars[-1][1] * (i - len(bars) + 1 + frac)
    return int(round(bars[max(0, i)][0] + frac * bars[max(0, i)][1]))


def _tempo_summary(tm):
    bpms = [b for _, b in tm] or [120.0]
    return f"{bpms[0]:g} bpm" if len(set(bpms)) == 1 else f"tempo map {min(bpms):g}-{max(bpms):g} bpm ({len(bpms)} points)"


# ============================================================================= tools
def _store():
    import sys
    return sys.modules.get("flh_store") or __import__("flh_store")


_RENDER_CACHE = {}


def _render(spec, base_dir):
    path = _source_path(spec, base_dir); src = load_source(path)
    key = (_digest(spec), os.path.abspath(path), src["sha"], id(realism_fn()))
    if key not in _RENDER_CACHE:
        if len(_RENDER_CACHE) > 6:
            _RENDER_CACHE.pop(next(iter(_RENDER_CACHE)))
        _RENDER_CACHE[key] = perform(spec, src)
    return _RENDER_CACHE[key]


def _project_grid(project, div):
    """(project ppq, quarter beats per bar) for converting source ticks to FL song bars."""
    try:
        P = H["_ppq"](H["_load"](project))
    except Exception:
        P = div
    try:
        bpb = H["_qbar"](H["flp_project_info"](project))
    except Exception:
        bpb = 4
    return P, bpb


def _maps_valid(project, maps):
    """The journal's note maps whose source .mid is still next to the project, unchanged (sha)."""
    out, pdir = {}, os.path.dirname(os.path.abspath(project))
    for name, mp in (maps or {}).items():
        try:
            if isinstance(mp, dict) and load_source(os.path.join(pdir, name))["sha"] == mp.get("sha"):
                out[name] = mp
        except Exception:
            pass
    return out


def _map_for(project, spec):
    """The note map of spec's source (None for a hand-written spec); ValueError for an auto spec without one."""
    if not spec.get("auto"):
        return None
    mp = _maps_valid(project, _journal(project).get("performance_map")).get(spec.get("source"))
    if not mp:
        raise ValueError("this performance has no note map for its source (set up by an older fl-headless, or the "
                         "source .mid changed): run flp_perform_setup(project) again - style and sections are kept")
    return mp


def _prov_parts(render, spec, write_parts, project, pmap):
    """flp_perform_write parts: every performed note with the clip and record it came from (see _perform_plan)."""
    div = render["ppq"]; P, bpb = _project_grid(project, div); g = normalize(spec)
    t0 = int(round((float(g["song_start_bar"]) - 1) * bpb * P))
    src = load_source(_source_path(spec, os.path.dirname(os.path.abspath(project))))
    out = []
    for name in write_parts:
        part = g["parts"][name]; track = part.get("track")
        if name not in (pmap.get("parts") or {}):
            raise ValueError(f"part {name} isn't in this performance's note map: run flp_perform_setup(project) again")
        where = _unpack_where(pmap["parts"][name])
        loc = {}
        for gi, n in enumerate(src["notes"]):
            if n["t"] == track:
                loc[gi] = len(loc)
        if len(loc) != len(where):
            raise ValueError("this performance's note map doesn't fit its source: run flp_perform_setup(project) again")
        added = _fl_of(part.get("fl"), None)                  # a doubling / boom: the part's usual fields, no slide
        rows = []
        for (s, e, p, v), gi in zip(render["parts"][name], render["src"][name]):
            qs = s * P // div; qe = e * P // div; dur = max(1, qe - qs)
            if gi is not None and gi in loc:
                w = where[loc[gi]]; sn = src["notes"][gi]
                vel = int(w["vr"]) if w["vr"] is not None and v == sn["v"] else v   # FL's raw 0 / 128 kept
                home = t0 + (sn["s"] + w["dq"]) * P // div      # where its record sits now (song tick)
                rows.append([t0 + qs, max(0, dur + w["ext"]), p, vel, w["fl"], w["clip"], w["rid"], home])
            else:
                rows.append([t0 + qs, dur, p, v, added, None, None, None])
        out.append({"channel": int(part["channel"]), "notes": rows})
    return out


def _ops(render, spec, write_parts, write_tempo, project):
    div = render["ppq"]; P, bpb = _project_grid(project, div); g = normalize(spec)
    base = float(g["song_start_bar"]); per_bar = bpb * P
    t0 = int(round((base - 1) * per_bar))
    ops = []
    for name in write_parts:
        notes = []
        fl = (spec.get("parts") or {}).get(name, {}).get("fl")
        origin = (render.get("origin") or {}).get(name) or [None] * len(render["parts"][name])
        for (s, e, p, v), o in zip(render["parts"][name], origin):
            qs = s * P // div; qe = e * P // div              # same rounding as a MIDI import of these ticks
            f = _fl_of(fl, o)                                 # slide flag and FL's other note fields, kept
            n = {"tick": t0 + qs, "ticks": max(1, qe - qs), "key": p, "vel": v}
            if f is not None:
                n.update(fl=f, slide=bool(f[0] & SLIDE))
            notes.append(n)
        ops.append({"tool": "flp_replace_part", "channel": int(g["parts"][name]["channel"]), "notes": notes})
    if write_tempo:
        pts = [[round(base + int(round(t * P / div)) / per_bar, 7), b] for t, b in render["tempo"]]
        ops.append({"tool": "flp_tempo_write", "points": pts, "pattern": g["tempo_pattern"]})
    return ops


def _digests(render):
    return {"parts": {k: _digest(v) for k, v in render["parts"].items()}, "tempo": _digest(render["tempo"])}


def _view_digests(project, spec):
    """digests of what the project holds now, per part (its channel) and its tempo map, in render format."""
    try:
        d = H["_load"](project); ev = H["_events"](d); notes, tempos = H["_song"](d, ev)
    except Exception:
        return None
    per = {}
    for pos, ln, ch, key, vel in notes:
        per.setdefault(ch, []).append([int(pos), int(pos) + max(1, int(ln)), int(key), max(1, min(127, int(vel)))])
    out = {name: _digest(sorted(per.get(int(p["channel"]), []))) for name, p in (spec.get("parts") or {}).items()}
    return {"parts": out, "tempo": _digest([[int(t), float(b)] for t, b in tempos])}


def _song_view(project):
    """{channel: sorted [(tick, key, vel, slide)]} as the playlist plays it (None if unreadable)."""
    try:
        d = H["_load"](project); ev = H["_events"](d)
        notes = H["_song_x"](d, ev)[0] if "_song_x" in H else [n + (False,) for n in H["_song"](d, ev)[0]]
    except Exception:
        return None
    out = {}
    for n in notes:
        out.setdefault(int(n[2]), []).append((int(n[0]), int(n[3]), int(n[4]), bool(n[5])))
    return {k: sorted(v) for k, v in out.items()}


def _read_back(target, ops, before, spec, result=None):
    """After the write: per performed part, how many sent notes (onset, key, velocity, slide) the song now plays and
    how many it can't (repeated pattern clips play their first copy; notes outside clips aren't written); a written
    channel the edit reports as unverified, a slide lost, or any other channel that changed is a problem.
    -> {"ok", "parts", "problems"}."""
    after = _song_view(target)
    if after is None:
        return {"ok": False, "problems": ["couldn't read the project back after writing"], "parts": {}}
    written = {int(o["channel"]): o for o in ops if o.get("tool") == "flp_replace_part"}
    names = {int(p["channel"]): nm for nm, p in (spec.get("parts") or {}).items()}
    rs = [x for x in ((result or {}).get("results") or []) if isinstance(x, dict) and x.get("tool") == "flp_replace_part"]
    probs, parts = [], {}
    from collections import Counter
    for i, (ch, o) in enumerate(written.items()):
        nm = names.get(ch, f"channel {ch}")
        want = Counter((n["tick"], n["key"], n["vel"], bool(n.get("slide"))) for n in o["notes"])
        got = Counter(after.get(ch, []))
        miss = sum((want - got).values()); extra = sum((got - want).values())
        rr = rs[i] if i < len(rs) else {}
        skipped = int(rr.get("in_repeated_patterns_skipped") or 0) + int(rr.get("outside_clips") or 0)
        parts[nm] = {"sent": sum(want.values()), "heard": sum(got.values()), "not_heard": miss, "other": extra}
        if rr.get("verified") is not None and not (rr["verified"] or {}).get("read_back"):
            probs.append(f"{nm}: the write wasn't verified")
        if miss > skipped:
            probs.append(f"{nm}: {miss - skipped} sent note(s) aren't heard after the write")
        if extra > 0 and extra > skipped:
            probs.append(f"{nm}: {extra} heard note(s) weren't sent")
        slides_sent = sum(v for k, v in want.items() if k[3]); slides_now = sum(v for k, v in got.items() if k[3])
        if slides_now < slides_sent - skipped or (slides_sent and not slides_now):
            probs.append(f"{nm}: {slides_sent - slides_now} of its {slides_sent} slide note(s) are gone after the write")
    if before is not None:
        moved = [c for c in set(before) | set(after) if c not in written and before.get(c) != after.get(c)]
        if moved:
            probs.append(f"channel(s) {sorted(moved)} changed although they weren't performed")
    return {"ok": not probs, "parts": parts, "problems": probs}


# ----------------------------------------------------------------------------- the per-pattern write
def _rec_digest(recs):
    return hashlib.sha1(json.dumps(sorted(list(map(int, r)) for r in recs)).encode()).hexdigest()[:16]


def _heard_of(clips, recs_of, chs):
    """What the playlist plays of channels chs given pattern records recs_of(pid) (every unmuted clip, as the
    server's song reader does): {channel: sorted [(tick, length, key, vel, slide)]}."""
    out = {c: [] for c in chs}
    for c in clips:
        if c.get("muted"):                                    # a muted clip plays nothing
            continue
        for r in recs_of(c["pid"]):
            if r[2] in out and c["ws"] <= r[0] < c["we"]:
                out[r[2]].append((c["pos"] + r[0] - c["ws"], min(r[3], c["we"] - r[0]), r[4], r[11],
                                  bool(r[1] & SLIDE)))
    return {k: sorted(v) for k, v in out.items()}


def _perform_plan(d, ev, parts, unique_repeats=False):
    """Where each performed note goes, as FL records per (pattern, channel). parts: [{"channel", "notes": [[song
    tick, length, key, vel, FL fields (9) or None, clip ref [pos, track, pid, k] or None, record id or None]]}] (a
    note with a clip ref goes back to that clip's pattern at its clip-relative position; one without (a doubling, a
    boom) into the clip of this part that covers it). A pattern several clips play holds one copy: the first clip
    (song order) that plays a record decides it; the other clips' notes that differ are counted (unique_repeats:
    those clips get their own copy of the pattern). Only a part's own clips' windows are rewritten; records outside
    them stay. -> (new project bytes or None if nothing changes, report with "intended" {(pid, ch): records})."""
    from collections import Counter
    P = H["_ppq"](d); pats = H["_patterns"](d, ev); clips = _clip_table(d, ev) or []
    NR = H["NOTE_RECORD"]; PB = H["PATTERN_BASE"]
    byref = {}
    for c in clips:
        byref.setdefault((c["pos"], c["track"]), []).append(c)

    def resolve(ref):
        cands = byref.get((int(ref[0]), int(ref[1]))) or []
        if len(cands) == 1:
            return cands[0]
        same = [c for c in cands if c["pid"] == int(ref[2])]
        if len(same) == 1:
            return same[0]
        k = int(ref[3]) if len(ref) > 3 else 0
        return cands[k] if k < len(cands) else None

    try:
        meters = H["_meter_map"](d, ev)
        bar = lambda t: round(H["_tick_to_bar"](t, meters, P), 2)
    except Exception:
        bar = lambda t: round(t / (4.0 * P) + 1, 2)
    pname = lambda pid: (pats.get(pid) or {}).get("name") or f"Pattern {pid}"
    order = lambda c: (c["muted"], c["pos"], c["i"])          # "first": the earliest unmuted clip
    EARLY = max(1, P // 8)
    occ = []                                                  # (ch, clip, rid or None, record)
    part_clips = {}                                           # ch -> {clip index: clip}
    moved = outside = 0
    chs = []; edges = {}
    for p in parts:
        ch = int(p["channel"]); chs.append(ch)
        midi_ch = next((r[9] for v in pats.values() for r in v["notes"] if r[2] == ch), 0)
        rows = []
        for row in p["notes"]:
            t, ln, key, vel, fl, ref, rid, home = (list(row) + [None] * 8)[:8]
            c = None
            if ref is not None:
                c = resolve(ref)
                if c is None or c["pid"] not in pats:
                    raise ValueError(f"the clip at bar {bar(int(ref[0]))} that held this part when the performance "
                                     "was set up isn't in the playlist any more; run flp_perform_setup(project) again "
                                     "(style and sections are kept)")
                part_clips.setdefault(ch, {})[c["i"]] = c
            rows.append((int(t), int(ln), int(key), int(vel), fl, c, rid, home))
        mine = sorted(part_clips.get(ch, {}).values(), key=order)
        for t, ln, key, vel, fl, c, rid, home in rows:
            if c is None:                                     # a note the performance added: the clip covering it
                cov = [x for x in mine if x["pos"] <= t < x["pos"] + x["len"]]
                if not cov:
                    cov = [x for x in mine if t < x["pos"] <= t + EARLY]
                if not cov:
                    outside += 1
                    continue
                c = min(cov, key=order)
            o = t - c["pos"] + c["ws"]
            lo, hi = c["ws"], c["we"]
            if home is not None:                              # the same clips must keep playing it: stay inside the
                h = int(home) - c["pos"] + c["ws"]            # stretch between window edges its record sits in now
                ed = edges.setdefault(c["pid"], sorted({x for c2 in clips if c2["pid"] == c["pid"]
                                                        for x in (c2["ws"], c2["we"])}))
                j = bisect.bisect_right(ed, h)
                lo = max(lo, ed[j - 1] if j > 0 else lo); hi = min(hi, ed[j] if j < len(ed) else hi)
            if not lo <= o < hi:                              # nudged past an edge: kept at the edge
                o = min(max(o, lo), hi - 1); moved += 1
            f = tuple(int(x) for x in fl) if fl else None
            rec = tuple(H["_note_record"](o, max(0, ln), ch, key, max(0, min(255, vel)),
                                          bool(f[0] & SLIDE) if f else False, f, midi_ch))
            occ.append((ch, c, rid, rec))
    # ---- who decides each record: the first clip that plays it
    groups = {}
    for x in occ:
        ch, c, rid, rec = x
        k = (c["pid"], ch, "r", rid) if rid is not None else (c["pid"], ch, "n", rec[0], rec[4])
        groups.setdefault(k, []).append(x)
    write = {}                                                # (pid, ch) -> [records]
    differ = {}                                               # clip index -> notes that differ from the first copy
    owner_rec = {}
    for k, xs in groups.items():
        xs.sort(key=lambda x: order(x[1]))
        first = xs[0]
        if k[2] == "n":                                       # an added note: only where its clip is the first to
            pid, o = k[0], first[3][0]                        # play that stretch of the pattern
            cover = [c for c in part_clips.get(first[0], {}).values() if c["pid"] == pid and c["ws"] <= o < c["we"]]
            if cover and min(cover, key=order)["i"] != first[1]["i"]:
                for x in xs:
                    differ[x[1]["i"]] = differ.get(x[1]["i"], 0) + 1
                continue
        write.setdefault((k[0], k[1]), []).append(first[3]); owner_rec[k] = first[3]
        for x in xs[1:]:
            if x[3] != first[3] and not x[1]["muted"]:
                differ[x[1]["i"]] = differ.get(x[1]["i"], 0) + 1
    # ---- unique copies for the repeats that play differently (unique_repeats)
    clones, kept_shared = [], []
    if unique_repeats:
        for ci in sorted(differ):
            c = next(c for c in clips if c["i"] == ci)
            if pats[c["pid"]]["ctrl"]:
                kept_shared.append(f"{pname(c['pid'])} at bar {bar(c['pos'])} (the pattern holds automation)")
                continue
            clones.append(c)
    clone_of = {c["i"]: c for c in clones}
    # ---- the new records per pattern: other channels as they are, this part's windows rewritten
    intended, touched, chg = {}, set(), set()
    for (pid, ch) in sorted({(c["pid"], ch) for ch, cs in part_clips.items() for c in cs.values()} | set(write)):
        wins = [(c["ws"], c["we"]) for c in part_clips.get(ch, {}).values() if c["pid"] == pid]
        cur = [r for r in pats[pid]["notes"] if r[2] == ch]
        keep = [r for r in cur if not any(a <= r[0] < b for a, b in wins)]
        new = keep + write.get((pid, ch), [])
        intended[(pid, ch)] = sorted(new)
        if Counter(cur) != Counter(new):
            touched.add(pid); chg.add(ch)
    clone_recs = {}
    for c in clones:                                          # this clip's own notes of every performed part
        recs = [r for r in pats[c["pid"]]["notes"] if r[2] not in chs]
        for ch in chs:
            own = [x[3] for x in occ if x[0] == ch and x[1]["i"] == c["i"]]
            outw = [r for r in pats[c["pid"]]["notes"] if r[2] == ch and not c["ws"] <= r[0] < c["we"]]
            recs += outw + own
        clone_recs[c["i"]] = recs
    if clones:
        chg |= set(chs)
    rep = {"moved_into_clip": moved, "outside_clips": outside, "patterns": len(touched), "changed_channels": sorted(chg),
           "written": sum(len(v) for v in write.values()), "sent": len(occ) + outside}
    nd = None
    if touched or clones:
        note_ks = H["_pattern_note_events"](d, ev); edits = []; missing = []
        for pid in sorted(touched):
            recs = [r for r in pats[pid]["notes"] if (pid, r[2]) not in intended] + \
                [r for ch in chs for r in intended.get((pid, ch), [])]
            body = b"".join(struct.pack(NR, *r) for r in sorted(recs, key=lambda r: (r[0], r[2], r[4])))
            if note_ks.get(pid):
                edits += H["_notes_event_edits"](ev, note_ks[pid], body)
            else:
                missing.append((pid, body))
        nd = H["_splice"](d, ev, edits) if edits else d
        for pid, body in missing:                             # (a pattern without a notes event: give it one)
            e2 = H["_events"](nd); nd = H["_write_pattern_notes"](nd, e2, pid, [])
            e2 = H["_events"](nd); nd = H["_splice"](nd, e2, H["_notes_event_edits"](e2, H["_pattern_note_events"](nd, e2)[pid], body))
        made = []
        for c in clones:                                      # PC-VERIFY: FL plays a copy like the original
            nd, npid = H["_add_pattern"](nd, f"{pname(c['pid'])} @{bar(c['pos'])}")
            e2 = H["_events"](nd); ks = H["_pattern_note_events"](nd, e2)[npid]
            body = b"".join(struct.pack(NR, *r) for r in sorted(clone_recs[c["i"]], key=lambda r: (r[0], r[2], r[4])))
            nd = H["_splice"](nd, e2, H["_notes_event_edits"](e2, ks, body))
            e2 = H["_events"](nd); kpl, _, recs_ = H["_playlist_at"](nd, e2)
            recs_[c["i"]][6:8] = struct.pack("<H", PB + npid)
            nd = H["_splice"](nd, e2, [(kpl, kpl + 1, H["_data_event"](H["EV_PLAYLIST"], b"".join(bytes(r) for r in recs_)))])
            for ch in chs:
                intended[(npid, ch)] = sorted(r for r in clone_recs[c["i"]] if r[2] == ch)
            made.append({"pattern": f"{pname(c['pid'])} @{bar(c['pos'])}", "copy_of": pname(c["pid"]),
                         "bar": bar(c["pos"])})
            differ.pop(c["i"], None)
        rep["made_unique"] = made
    if kept_shared:
        rep["not_made_unique"] = kept_shared
    if differ:
        by = {}
        for ci, n in differ.items():
            c = next(c for c in clips if c["i"] == ci); nm = pname(c["pid"])
            by.setdefault(nm, {"notes": 0, "bars": []}); by[nm]["notes"] += n; by[nm]["bars"].append(bar(c["pos"]))
        rep["repeats"] = {k: {"notes": v["notes"], "at_bars": sorted(v["bars"])[:8]} for k, v in by.items()}
        rep["repeat_notes_not_performed"] = sum(differ.values())
    rep["intended"] = intended
    if nd is not None:                                        # check the new bytes before anyone saves them
        _verify_plan(d, ev, nd, rep, chs)
    return nd, rep


def _verify_plan(d, ev, nd, rep, chs):
    """The written bytes hold exactly the intended records per (pattern, channel), every other channel plays as before
    and the performed channels play what the intended patterns play through the clips; RuntimeError otherwise."""
    ne = H["_events"](nd); np_ = H["_patterns"](nd, ne); op = H["_patterns"](d, ev)
    bad = _compare_intended(np_, rep["intended"])
    if bad:
        raise RuntimeError("the performance write didn't produce the intended notes, so nothing was saved (" +
                           "; ".join(bad[:3]) + "); please report this project")
    for pid, v in op.items():                                 # other channels' records: untouched
        if sorted(r for r in v["notes"] if r[2] not in chs) != sorted(r for r in np_.get(pid, {"notes": []})["notes"]
                                                                     if r[2] not in chs):
            raise RuntimeError("the performance write would have changed other channels' notes, so nothing was saved "
                               "(please report this project)")
    b = sorted(n[:6] for n in H["_song_x"](d, ev)[0] if n[2] not in chs)
    a_all = H["_song_x"](nd, ne)[0]
    if b != sorted(n[:6] for n in a_all if n[2] not in chs):
        raise RuntimeError("the performance write would have changed what other channels play, so nothing was saved "
                           "(please report this project)")
    want = _heard_of(_clip_table(nd, ne) or [], lambda pid: (np_.get(pid) or {"notes": []})["notes"], chs)
    got = {c: sorted((n[0], n[1], n[3], n[4], bool(n[5])) for n in a_all if n[2] == c) for c in chs}
    if want != got:
        raise RuntimeError("the performance write reads back differently from what its patterns hold, so nothing "
                           "was saved (please report this project)")
    rep["heard"] = {c: _rec_digest(v) for c, v in want.items()}


def _compare_intended(pats, intended):
    """[problem] where the project's records of (pattern, channel) differ from the intended ones: counts, keys,
    positions, lengths, velocities, slides, other FL fields."""
    from collections import Counter
    out = []
    for (pid, ch), want in sorted(intended.items()):
        got = sorted(r for r in (pats.get(pid) or {"notes": []})["notes"] if r[2] == ch)
        if got == sorted(want):
            continue
        w, g = Counter(map(tuple, want)), Counter(map(tuple, got))
        miss, extra = w - g, g - w
        what = []
        for nm, f in (("positions", lambda r: (r[0], r[4])), ("keys", lambda r: r[4]), ("lengths", lambda r: r[3]),
                      ("velocities", lambda r: r[11]), ("slides", lambda r: bool(r[1] & SLIDE))):
            if Counter(map(f, miss.elements())) != Counter(map(f, extra.elements())):
                what.append(nm)
        out.append(f"pattern {pid} channel {ch}: {sum(g.values())} notes, intended {sum(w.values())}; "
                   f"{sum(miss.values())} missing, {sum(extra.values())} unexpected" +
                   (f" ({', '.join(what)} differ)" if what else " (other FL note fields differ)"))
    return out


def flp_perform_write(project, parts, output=None, unique_repeats=False):
    """flp_perform's note write (an flp_edit op): each performed note back into the exact pattern and clip-relative
    position it came from (see _perform_plan). Verified before saving; nothing is written when nothing changes."""
    output = output or project
    H["writable"](output)
    d = H["_load"](project); ev = H["_events"](d)
    nd, rep = _perform_plan(d, ev, parts, unique_repeats)
    if nd is not None:
        H["_save"](nd, output)
    elif os.path.normcase(os.path.abspath(output)) != os.path.normcase(os.path.abspath(project)):
        H["_save"](d, output)
    out = {k: v for k, v in rep.items() if k != "intended"}
    out["intended"] = {f"{pid}:{ch}": [len(v), _rec_digest(v)] for (pid, ch), v in rep["intended"].items()}
    out["changed"] = nd is not None
    return out


def _read_back_plan(target, plan, before, chs, names):
    """After the write: the project's records per (pattern, channel) are exactly the intended ones, the performed
    channels play what those patterns play, every other channel plays as before. -> {"ok", "parts", "problems"}."""
    try:
        d = H["_load"](target); ev = H["_events"](d); pats = H["_patterns"](d, ev); sx = H["_song_x"](d, ev)[0]
    except Exception as e:
        return {"ok": False, "problems": [f"couldn't read the project back after writing ({e})"], "parts": {}}
    probs = _compare_intended(pats, plan["intended"])
    after = {}
    for n in sx:
        after.setdefault(int(n[2]), []).append((int(n[0]), int(n[3]), int(n[4]), bool(n[5])))
    after = {k: sorted(v) for k, v in after.items()}
    want = _heard_of(_clip_table(d, ev) or [], lambda pid: (pats.get(pid) or {"notes": []})["notes"], chs)
    for c in chs:
        got = sorted((n[0], n[1], n[3], n[4], bool(n[5])) for n in sx if n[2] == c)
        if got != want.get(c, []):
            probs.append(f"{names.get(c, c)}: what the song plays doesn't match its patterns")
        if plan.get("heard") and plan["heard"].get(c) not in (None, _rec_digest(want.get(c, []))):
            probs.append(f"{names.get(c, c)}: the song plays something else than the write intended")
    if before is not None:
        moved = [c for c in set(before) | set(after) if c not in chs and before.get(c) != after.get(c)]
        if moved:
            probs.append(f"channel(s) {sorted(moved)} changed although they weren't performed")
    parts = {}
    for c in chs:
        recs = [r for (pid, ch), v in plan["intended"].items() if ch == c for r in v]
        parts[names.get(c, f"channel {c}")] = {
            "notes_in_patterns": len(recs), "heard": len(after.get(c, [])),
            "slides_heard": sum(1 for n in after.get(c, []) if n[3])}
    return {"ok": not probs, "parts": parts, "problems": probs}


def _sync(project):
    """Queued (lazy) edits of this project go into the file first, so perform reads and writes the song as it is."""
    try:
        if H.get("_pending_get") and H["_pending_get"](project):
            H["_sync_pending"](project)
    except Exception as e:                                # never perform on a stale file
        raise RuntimeError(f"couldn't apply the edits queued for this project first ({e}); run flh_sync, then retry")


def _journal(project):
    return _store().journal_get(project)


def _save(target, spec, history, applied, pmap=None):
    st = _store()
    st.journal_update(target, set={"performance": None, "performance_history": None, "performance_applied": None,
                                   **({"performance_map": None} if pmap else {})})
    st.journal_update(target, set={"performance": spec, "performance_history": history,
                                   "performance_applied": applied, **({"performance_map": pmap} if pmap else {})})


def _tempo_lane(project, pattern):
    """True when the project has a pattern named `pattern` holding recorded tempo data (flp_tempo_write's lane)."""
    try:
        d = H["_load"](project); ev = H["_events"](d)
        return any(v.get("name", "").strip().lower() == str(pattern).lower() and v.get("ctrl")
                   for v in H["_patterns"](d, ev).values())
    except Exception:
        return False


def _audio_clips(project):
    try:
        return [c["name"] for c in H["flp_playlist"](project)["clips"] if c.get("kind") == "audio" and not c.get("muted")]
    except Exception:
        return []


def _plan_warnings(plan, warn, unique_repeats):
    n = plan.get("repeat_notes_not_performed")
    if n:
        where = ", ".join(f"{k} (bars {', '.join(str(b) for b in v['at_bars'][:4])})"
                          for k, v in list(plan["repeats"].items())[:4])
        warn.append(f"{n} notes in repeated clips play the first appearance's performance, because those clips share "
                    f"one pattern: {where}. unique_repeats=True gives each of them its own pattern copy" +
                    (" (patterns holding automation are never copied)" if unique_repeats else ""))
    if plan.get("moved_into_clip"):
        warn.append(f"{plan['moved_into_clip']} notes humanized past the edge of their clip (or of the stretch the same "
                    "clips play) were kept at that edge, so every clip still plays them")
    if plan.get("outside_clips"):
        warn.append(f"{plan['outside_clips']} added notes (doublings, booms, ring) fall outside every clip of their "
                    "part and were not written")


def _apply(project, old, new, history, changed, sections=None, parts=None, dry_run=False, output=None, t0=None,
           note=None, allow_tempo_with_audio=None, unique_repeats=False):
    base = os.path.dirname(os.path.abspath(project))
    t0 = t0 or time.time()
    rb = _render(old, base) if old else None
    ra = _render(new, base)
    sa = section_stats(ra); sb = section_stats(rb) if rb else sa
    compute_ms = int((time.time() - t0) * 1000)
    j = _journal(project); applied = j.get("performance_applied") or {}
    dg = _digests(ra); warn = []
    asked_tempo = False
    if parts in ("all", ["all"]):
        wparts, wtempo, asked_tempo = list(ra["parts"]), True, True
    elif parts:
        want = [parts] if isinstance(parts, str) else list(parts)
        bad = [p for p in want if p not in ra["parts"] and p != "tempo"]
        if bad:
            raise ValueError(f"unknown part(s) {bad} (have: {sorted(ra['parts'])} or 'tempo')")
        wparts, wtempo = [p for p in want if p != "tempo"], "tempo" in want
        asked_tempo = wtempo
    else:
        ap = applied.get("parts") or {}
        wparts = [p for p in ra["parts"] if ap.get(p) != dg["parts"][p]]
        wtempo = applied.get("tempo") != dg["tempo"]
    if new.get("auto") and not dry_run and not parts:
        vd = _view_digests(project, new); av = applied.get("view")
        if vd and av:
            edited = [p for p in new["parts"] if av.get("parts", {}).get(p) not in (None, vd["parts"].get(p))]
            if edited or (av.get("tempo") and av["tempo"] != vd["tempo"] and wtempo):
                what = (f"the notes of {', '.join(edited)}" if edited else "") + \
                       (" and " if edited and av.get("tempo") != vd["tempo"] else "") + \
                       ("the tempo" if av.get("tempo") != vd["tempo"] else "")
                raise ValueError(f"{what} changed in the project since the performance was set up, and flp_perform "
                                 "would overwrite that edit. Run flp_perform_setup(project) again to take the new "
                                 "notes (style and sections are kept), or pass parts=[...] to overwrite on purpose")
    allow = allow_tempo_with_audio if allow_tempo_with_audio is not None else bool(normalize(new).get("allow_tempo_with_audio"))
    if wtempo:
        audio = _audio_clips(project)
        if audio and not allow:
            wtempo = False
            warn.append(f"tempo map NOT written: the song has audio clips ({', '.join(audio[:4])}) that would drift out "
                        "of sync with a new tempo map; pass allow_tempo_with_audio=True to write it anyway")
        elif not _tempo_lane(project, normalize(new)["tempo_pattern"]):
            wtempo = False
            warn.append(f"tempo map NOT written: the song has no '{normalize(new)['tempo_pattern']}' tempo lane "
                        "(flp_write_arrangement makes one)" + (" - asked for" if asked_tempo else ""))
    names = [s["name"] for s in new["sections"]]
    if sections:
        want = [sections] if isinstance(sections, str) else list(sections)
        names = [s["name"] for s in (_find_section(new, w) for w in want) if s]
    real = [n for n in names if sb.get(n) != sa.get(n)]
    # auto specs: every performed note goes back to the pattern and clip it came from (the note map)
    pmap = _map_for(project, new) if wparts else None
    plan = pparts = None; shown = wparts
    if pmap is not None:
        pparts = _prov_parts(ra, new, wparts, project, pmap)
        d0 = H["_load"](project)
        nd0, plan = _perform_plan(d0, H["_events"](d0), pparts, unique_repeats)
        _plan_warnings(plan, warn, unique_repeats)
        chg = set(plan.get("changed_channels") or [])
        shown = [p for p in wparts if int(new["parts"][p]["channel"]) in chg]
    res = {"project": output or project, "changed": changed or [], "sections_changed": real,
           "table": _table(sb, sa, names, set(real)), "write": {"parts": shown, "tempo": wtempo},
           "compute_ms": compute_ms}
    if plan is not None and plan.get("made_unique"):
        mu = [x["pattern"] for x in plan["made_unique"]]
        res["write"]["unique_copies"] = f"{len(mu)} pattern copies: " + ", ".join(mu[:6]) + (", ..." if len(mu) > 6 else "")
    if ra.get("realism"):
        res["realism"] = ra["realism"]
    if warn:
        res["warnings"] = warn
    if dry_run:
        res["dry_run"] = True
        return res
    xops = _expression_ops(ra, new, wparts, project, res, warn)
    if warn:
        res["warnings"] = warn
    if pmap is not None:
        ops = ([{"tool": "flp_perform_write", "parts": pparts, "unique_repeats": bool(unique_repeats)}]
               if shown or plan.get("made_unique") else []) + (_ops(ra, new, [], True, project) if wtempo else [])
    else:
        ops = _ops(ra, new, wparts, wtempo, project) if (wparts or wtempo) else []
    ops += xops
    target = output or project
    if ops:
        before = _song_view(project)
        r = H["flp_edit"](project, ops, output)
        if plan is not None:                                  # the file now holds exactly the intended patterns?
            chs = [int(p["channel"]) for p in pparts]
            res["verify"] = _read_back_plan(target, plan, before, chs,
                                            {int(p["channel"]): nm for nm, p in new["parts"].items()})
            got = next((x for x in ((r or {}).get("results") or []) if isinstance(x, dict)
                        and x.get("tool") == "flp_perform_write"), None)
            if got is not None and got.get("intended") not in (None, {f"{pid}:{ch}": [len(v), _rec_digest(v)] for
                                                                    (pid, ch), v in plan["intended"].items()}):
                res["verify"]["ok"] = False
                res["verify"]["problems"].append("the project changed between planning and writing (FL saved "
                                                 "something else); check it and run flp_perform again")
        else:
            res["verify"] = _read_back(target, ops, before, new, r)  # what the file now holds vs what was sent
        if not res["verify"]["ok"]:
            warn.extend(res["verify"]["problems"])
        res["edit"] = {"ops": r.get("ops", len(ops)) if isinstance(r, dict) else len(ops),
                       "results": [{k: v for k, v in x.items() if k in ("tool", "added", "removed", "tempo_points",
                                                                              "bpm_range", "unplaced", "outside_clips",
                                                                              "in_repeated_patterns_skipped", "patterns",
                                                                              "written", "moved_into_clip",
                                                                              "repeat_notes_not_performed")
                                    and (v or k == "tool")}
                                   for x in (r.get("results") or [])] if isinstance(r, dict) else r}
        rs = r.get("results") or [] if isinstance(r, dict) else []
        rep_ = sum(int(x.get("in_repeated_patterns_skipped") or 0) for x in rs)
        out_ = sum(int(x.get("outside_clips") or 0) for x in rs)
        if rep_:
            warn.append(f"{rep_} notes fall in repeated pattern clips: every repeat plays the first appearance's "
                        "performance (make the repeats unique patterns to perform them separately)")
        if out_:
            warn.append(f"{out_} notes start outside every pattern clip and were not written")
        if warn:
            res["warnings"] = warn
    else:
        res["edit"] = "nothing to write: the project already has this performance"
        res["changed"], res["sections_changed"] = [], []      # nothing was written: no before/after to show
        res.pop("table", None)
    ap = dict(applied.get("parts") or {})
    for p in ra["parts"]:
        if p in wparts or ap.get(p) == dg["parts"][p]:
            ap[p] = dg["parts"][p]
    newapp = {"parts": ap, "tempo": dg["tempo"] if (wtempo or applied.get("tempo") == dg["tempo"]) else applied.get("tempo"),
              "when": time.strftime("%Y-%m-%d %H:%M"), **({"note": note} if note else {})}
    if new.get("auto"):
        vd = _view_digests(target, new)
        if vd:
            newapp["view"] = vd
    same = os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(project))
    _save(target, new, history, newapp, None if same else j.get("performance_map"))
    res["total_ms"] = int((time.time() - t0) * 1000)
    return res


def _expression_ops(render, spec, write_parts, project, res, warn):
    """The layers' expression curves (from flh_realism) as ops for the same flp_edit batch, through the realism
    module's writer when it has one (ns["realism_expression_ops"]); else the result says to run flp_realism after."""
    ex = {k: v for k, v in (render.get("expression") or {}).items() if k in write_parts and v}
    if not ex:
        return []
    w = H.get("realism_expression_ops")
    if not callable(w):
        res["realism_next"] = (f"expression curves for {', '.join(sorted(ex))} aren't written by this batch: run "
                               "flp_realism on those parts after (it writes them)")
        return []
    P, bpb = _project_grid(project, render["ppq"]); g = normalize(spec)
    t0 = int(round((float(g["song_start_bar"]) - 1) * bpb * P))
    ops = []
    for name, e in sorted(ex.items()):
        e = expression_at(e, [t0 * render["ppq"] / float(P)])         # song start bar -> song ticks (source PPQ)
        try:
            got = w(project, int(g["parts"][name]["channel"]), e, {
                "ppq": render["ppq"], "project_ppq": P, "tick_offset": t0, "beats_per_bar": bpb, "part": name,
                "song_start_bar": float(g["song_start_bar"]), "source": "perform"})
            ops += list(got or [])
        except Exception as er:
            warn.append(f"{name}: expression curves not written ({type(er).__name__}: "
                                                  f"{str(er)[:100]}): run flp_realism after")
    return ops


def flp_perform(project, changes=None, sections=None, parts=None, dry_run=False, output=None,
                allow_tempo_with_audio=None, unique_repeats=False):
    """Re-render the song's performance from its journal spec with relative section edits and write what changed
    in ONE flp_edit batch. changes: {"<section>": {"tempo": "-3%"}, "<another section>": {"humanize":
    "-30%", "dyn": "+4"}, "*": {...}} (section names as flp_perform_setup listed them). dry_run: only the before/after table."""
    t0 = time.time()
    _sync(project)
    j = _journal(project); spec = j.get("performance") or {}
    if not spec.get("sections"):
        raise ValueError("this song's journal has no performance spec yet: run flp_perform_setup(project) first "
                         "(it reads the song itself; style='natural' by default)")
    base = os.path.dirname(os.path.abspath(project))
    if changes:
        new, changed = apply_changes(spec, changes, render_fn=lambda s: section_stats(_render(s, base)))
    else:
        new, changed = copy.deepcopy(spec), []
    hist = list(j.get("performance_history") or [])
    if changes and _digest(new) != _digest(spec):
        hist = (hist + [{"when": time.strftime("%Y-%m-%d %H:%M"), "changes": changes, "spec": spec}])[-HISTORY:]
    return _apply(project, spec, new, hist, changed, sections, parts, dry_run, output, t0,
                  note=json.dumps(changes)[:200] if changes else None, allow_tempo_with_audio=allow_tempo_with_audio,
                  unique_repeats=unique_repeats)


def flp_perform_undo(project, dry_run=False, output=None, allow_tempo_with_audio=None, unique_repeats=False):
    """Put the previous performance spec back (from journal history) and re-apply it."""
    t0 = time.time()
    _sync(project)
    j = _journal(project); hist = list(j.get("performance_history") or []); spec = j.get("performance") or {}
    applied = j.get("performance_applied") or {}
    if spec.get("sections") and str(applied.get("note") or "").startswith("setup"):
        return {"project": output or project, "undone": None,
                "note": "nothing to undo: no flp_perform since flp_perform_setup (the project still has its own "
                        "notes, unchanged)"}
    if not hist:
        raise ValueError("no earlier performance spec in this song's journal history")
    prev = hist.pop()
    r = _apply(project, spec, prev["spec"], hist, ["(undo " + str(prev.get("when")) + ")"], None, None, dry_run,
               output, t0, note="undo", allow_tempo_with_audio=allow_tempo_with_audio, unique_repeats=unique_repeats)
    r["undone"] = prev.get("changes")
    return r


def _check_project(project, g, shaped=True):
    warn = []
    try:
        chans = {c["index"]: c.get("name") for c in H["flp_list"](project)["channels"]}
    except Exception as e:
        raise ValueError(f"can't read the project's channels: {e}")
    used = {}
    for name, p in g["parts"].items():
        if "channel" not in p:
            raise ValueError(f"part {name}: no FL channel")
        c = int(p["channel"])
        if c not in chans:
            raise ValueError(f"part {name}: channel {c} isn't in the project (have {sorted(chans)})")
        if c in used:
            raise ValueError(f"parts {used[c]} and {name} both use channel {c}: each part needs its own channel")
        used[c] = name
    if shaped:
        try:
            pats = H["flp_patterns"](project)
            pats = pats.get("patterns") if isinstance(pats, dict) else pats
            if not any(str(p.get("name", "")).strip().lower() == str(g["tempo_pattern"]).lower() for p in pats or []):
                warn.append(f"no '{g['tempo_pattern']}' pattern: the tempo map can't be written until the song has one "
                            "(flp_write_arrangement makes it)")
        except Exception:
            pass
    return {name: chans[int(p["channel"])] for name, p in g["parts"].items()}, warn


def flp_perform_setup(project, spec=None, spec_file=None, auto=None, style=None, parts=None, sections=None,
                      quantize=None, options=None, allow_tempo_with_audio=False):
    """Store a song's performance spec in its journal: auto (default when no spec is given) builds it from the
    project itself; spec / spec_file validates a hand-written one. The previous spec is kept in history."""
    t0 = time.time()
    _sync(project)
    if auto is None:
        auto = spec is None and spec_file is None
    if not auto and (spec is None) == (spec_file is None):
        raise ValueError("give spec ({...}) or spec_file (path to a .json spec), or auto=True")
    j = _journal(project); old = j.get("performance") or {}
    hist = list(j.get("performance_history") or []); applied = j.get("performance_applied") or {}
    pdir = os.path.dirname(os.path.abspath(project))
    report = None
    if auto:
        prev_auto = old.get("auto") if isinstance(old.get("auto"), dict) else None
        style = style or (prev_auto or {}).get("style") or "natural"
        reuse = None
        if prev_auto and prev_auto.get("style") != "none" and applied.get("view"):
            vd = _view_digests(project, old)                 # the project still holds our last performance:
            if vd and vd == applied["view"]:                 # re-reading it would stack humanization on humanization
                reuse = {"source": old.get("source"), "original": prev_auto.get("original"),
                         "channels": [int(p["channel"]) for p in (old.get("parts") or {}).values()],
                         "fl": {int(p["channel"]): p.get("fl") for p in (old.get("parts") or {}).values()},
                         "maps": _maps_valid(project, j.get("performance_map"))}
        spec, orig, report = build_auto_spec(project, style, parts, sections, quantize,
                                             options or (prev_auto or {}).get("options"), allow_tempo_with_audio,
                                             reuse=reuse)
        found = os.path.join(pdir, spec["source"])
    else:
        sdir = None
        if spec_file:
            with open(spec_file, encoding="utf-8") as f:
                spec = json.load(f)
            sdir = os.path.dirname(os.path.abspath(spec_file))
        elif isinstance(spec, str):
            spec = json.loads(spec)
        spec = copy.deepcopy(spec); orig = None
        src = spec.get("source")
        if not src:
            raise ValueError("spec.source is missing (the clean, grid-quantized source .mid)")
        cands = [src] if os.path.isabs(src) else [os.path.join(d, src) for d in (sdir, pdir) if d]
        found = next((c for c in cands if os.path.isfile(c)), None)
        if not found:
            raise FileNotFoundError(f"source {src} not found (looked in {cands})")
        found = os.path.abspath(found)
        try:
            rel = os.path.relpath(found, pdir)
            spec["source"] = rel if not rel.startswith("..") else found
        except ValueError:                                   # another drive
            spec["source"] = found
        spec.setdefault("format", FORMAT)
        if allow_tempo_with_audio:
            spec["allow_tempo_with_audio"] = True
    g = normalize(spec)
    render = perform(spec, load_source(found))
    channels, warn = _check_project(project, g, render["tempo_shaped"])
    st = section_stats(render)
    drop_old = False
    if auto and not (report or {}).get("reused_source"):     # earlier auto specs that read the source files this
        written = {spec["source"], spec["auto"].get("original")}   # setup just rewrote can't be played back any more

        def stale(sp):
            return isinstance(sp, dict) and bool(sp.get("auto")) and sp.get("source") in written
        hist = [h for h in hist if not stale(h.get("spec"))]
        drop_old = stale(old)
    if old.get("sections") and _digest(old) != _digest(spec) and not drop_old:
        hist = (hist + [{"when": time.strftime("%Y-%m-%d %H:%M"), "changes": "setup", "spec": old}])[-HISTORY:]
    if auto:
        # what the project holds right now = the original notes and tempo, as the engine sees them
        base_spec = orig or dict(copy.deepcopy(spec), defaults={}, events=[],
                                 sections=[{"name": s["name"], "bar": s["bar"]} for s in spec["sections"]])
        if not orig:
            base_spec.pop("tempo", None)
            for p in base_spec["parts"].values():
                p.pop("voicing", None)
        now = perform(base_spec, load_source(os.path.join(pdir, base_spec["source"])))
        if orig and not (report or {}).get("reused_source"):
            hist = (hist + [{"when": time.strftime("%Y-%m-%d %H:%M"), "changes": "original notes (before the grid snap)",
                             "spec": orig}])[-HISTORY:]
        elif not orig and spec["auto"]["style"] != "none" and not (report or {}).get("reused_source"):
            # the project's own notes, so flp_perform_undo after the first flp_perform brings them back
            hist = (hist + [{"when": time.strftime("%Y-%m-%d %H:%M"), "changes": "the project's own notes",
                             "spec": base_spec}])[-HISTORY:]
        nd = _digests(now)
        applied = {"parts": nd["parts"], "tempo": nd["tempo"], "when": time.strftime("%Y-%m-%d %H:%M"),
                   "note": "setup: the project's own notes"}
        vd = _view_digests(project, spec)
        if vd:
            applied["view"] = vd
        if report and report.get("reused_source"):
            applied = j.get("performance_applied") or applied
    st_ = _store()
    pmap = _maps_valid(project, j.get("performance_map")) or {}
    if report is not None:
        pmap.update(report.get("maps") or {})
    st_.journal_update(project, set={"performance": None, "performance_history": None, "performance_applied": None,
                                     "performance_map": None})
    st_.journal_update(project, set={"performance": spec, "performance_history": hist,
                                     "performance_applied": applied, **({"performance_map": pmap} if pmap else {})})
    names = [s["name"] for s in spec["sections"]]
    out = {"project": project, "stored": True, "source": spec["source"],
           "parts": {k: {"channel": int(g["parts"][k]["channel"]), "channel_name": channels[k],
                         "notes": len(render["parts"][k]),
                         **({"role": report["detected"][k]["role"]} if report else {})} for k in render["parts"]}}
    if report:
        out.update({"style": spec["auto"]["style"], "about": STYLES[spec["auto"]["style"]]["about"],
                    "sections_from": report["sections_from"], "meter": report["meters"], "tempo": report["tempo"],
                    "writes_tempo_map": render["tempo_shaped"]})
        if report["quantized"]:
            out["quantized"] = report["quantized"]
            out["original"] = "kept: flp_perform_undo after flp_perform brings the original notes back"
        if report["reused_source"]:
            out["source_note"] = "kept the earlier source (the project still holds the last performance)"
        if report["audio_clips"] and render["tempo_shaped"] and not spec.get("allow_tempo_with_audio"):
            warn.append(f"the song has audio clips ({', '.join(report['audio_clips'][:3])}): this style's tempo map "
                        "won't be written (audio would drift) unless allow_tempo_with_audio=True")
        warn = report["warnings"] + warn
    if render.get("swing_note"):
        warn.append(render["swing_note"])
    out.update({"sections": _table(st, st, names, set(), limit=12) if len(names) <= 12 else
                f"{len(names)} sections: {', '.join(names[:6])}, ... {names[-1]}",
                "tempo_points": len(render["tempo"]), **({"warnings": warn} if warn else {}),
                "compute_ms": int((time.time() - t0) * 1000),
                "next": "flp_perform(project) writes it (dry_run=True to preview); flp_perform(project, "
                        "changes={'<section>': {'humanize': '+20%', 'dyn': '+4', 'tempo': '-3%'}}) edits"})
    return out


def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    # flp_perform / flp_perform_undo write only through flp_edit, which already saves/reloads the open project once;
    # wrapping them in _guard_open_project as well would nest two save/reload cycles.
    ns["TOOLS"]["flp_perform"] = (
        flp_perform, "Re-render the song's performance (tempo map, rubato, dynamics, humanization, swing, legato, "
        "...) from the spec in its journal (flp_perform_setup makes it from any song), with natural relative section "
        "edits, and write only what changed in ONE batch. changes: {\"<section>\": {\"tempo\": \"-3%\", "
        "\"humanize\": \"-30%\", \"dyn\": \"+4\"}} - section names are the song's own (flp_perform_setup's "
        "table and flh_journal show them; an unknown name's error lists them); '*' = every section (tempo: speed "
        "change or factor, '+2' = bpm; bpm: target average; dyn: '+n' velocity, '+x%', or an average; humanize/"
        "breath/curve/accent/swell/swing/... any section parameter). Every note goes back into the pattern and clip "
        "it came from; a pattern played in several places holds one performance (the first), unique_repeats=true "
        "gives the other places their own pattern copy. dry_run: the before/after table only. Songs with audio clips "
        "get no tempo map unless allow_tempo_with_audio. Undo: flp_perform_undo.",
        S(project=ns["_FLP"], changes=P("object", "{section (the song's own names): {param: change}}", True),
          sections=P("array", "only report these sections", True, items={"type": "string"}),
          parts=P(["array", "string"], "write these parts (and/or 'tempo'), or 'all'; default: whatever changed",
                  True, items={"type": "string"}),
          dry_run=ns["_BOOL"]("only show the before/after table, write nothing", True),
          output=ns["_STR"]("save as (default: the project itself)", True),
          allow_tempo_with_audio=ns["_BOOL"]("write tempo maps even though the song has audio clips", True),
          unique_repeats=ns["_BOOL"]("a pattern played in several places that would play differently: give each "
                                     "place its own pattern copy (default false: they share the first one's)", True)))
    ns["TOOLS"]["flp_perform_write"] = (
        ns["_guard_open_project"](flp_perform_write, "project", "output"),
        "flp_perform's note write (flp_edit op; flp_perform builds it): parts [{channel, notes: [[song tick, length, "
        "key, vel, FL fields, clip ref, record id]]}] go back into the patterns and clip positions they came from.",
        S(project=ns["_FLP"], output=ns["_STR"]("save as (default: the project itself)", True),
          parts=P("array", "[{channel, notes}]", items={"type": "object"}),
          unique_repeats=ns["_BOOL"]("copy patterns whose repeats play differently", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_perform_write"] = ("project", "output")
    ns["TOOLS"]["flp_perform_setup"] = (
        flp_perform_setup, "Set up a song's performance. With no spec it reads the song itself (any genre, tempo, "
        "meter): parts = instrument channels with notes (melody/accompaniment/bass/drums/pad/keys detected), source "
        "= the current notes (snapped to their grid if already humanized; the original is kept for undo), sections = "
        "journal / markers / section clips / 8-bar blocks. style: none (exact identity), tight (pop/EDM/hip-hop), "
        "natural (default), expressive (classical/solo piano rubato), swing, laid-back. Or give spec / spec_file "
        "(format in flh_perform's docstring). Then flp_perform writes it.",
        S(project=ns["_FLP"], spec=P("object", "a hand-written spec", True),
          spec_file=ns["_STR"]("a .json spec file (relative source paths resolve next to it)", True),
          auto=ns["_BOOL"]("build the spec from the project (default when no spec is given)", True),
          style=ns["_STR"]("none | tight | natural | expressive | swing | laid-back (default natural, or the "
                           "previous one)", True),
          parts=P(["array", "object"], "channels (index or name) to perform, or {channel: role} to set roles", True,
                  items={"type": ["integer", "string"]}),
          sections=P(["array", "integer"], "[{name, bar[, style]}] in song bars, or a block size in bars", True,
                     items={"type": ["object", "number"]}),
          quantize=ns["_BOOL"]("snap already-humanized notes to their grid for the clean source (default: auto; "
                               "never for style none)", True),
          options=P("object", "style options: swing (0.5-0.75), swing_grid (8|16), late (ticks at 480 PPQ), "
                    "late_parts ([names])", True),
          allow_tempo_with_audio=ns["_BOOL"]("allow tempo maps although the song has audio clips", True)))
    ns["TOOLS"]["flp_perform_undo"] = (
        flp_perform_undo, "Undo the last flp_perform change: the previous spec comes back from the journal history "
        "and is written to the project.",
        S(project=ns["_FLP"], dry_run=ns["_BOOL"]("only show the table", True),
          output=ns["_STR"]("save as (default: the project itself)", True),
          allow_tempo_with_audio=ns["_BOOL"]("write tempo maps even though the song has audio clips", True),
          unique_repeats=ns["_BOOL"]("give repeats that would play differently their own pattern copy", True)))
