"""flh_rise: the orchestral / piano RISE - the film-score build into a climax, made of the music itself (flh_fx makes
the EDM risers). flp_build_rise(project, into, bars=4, ...) writes, in ONE reversible flp_edit batch (automatic backup:
flp_versions / flp_restore):
  1. harmony - a tension ladder over the build bars: diminished-7th chords a minor third / step apart that land on a
     Vsus4 holding the breath before the new section's tonic (data/rise/rise.json "ladders": minor #vii°7 iv°7 ii°7
     Vsus4 = in B minor A#dim7 Edim7 C#dim7 F#sus4; major vii°7 ii°7 #iv°7 Vsus4), spread evenly over the bars.
     The song's accompaniment there is re-voiced onto the ladder: its rhythm, lengths and velocities stay, each note
     moves to the nearest ladder tone (climbing a minor third per chord), notes held across a chord change are split,
     bass notes (bass / low-strings channels, the low notes of a piano part) take the chord's root - or the
     inversion whose bass doesn't rub against the melody. When nothing accompanies in those bars, a pad / strings /
     chords channel gets held voicings. Ladder tones a semitone (or major 7th) from a melody note sounding with them
     are left out or moved, and voicings stay under the melody.
  2. tempo - an accelerando in the tempo map (the 'Tempo' lane flp_tempo_write keeps, the same map flp_perform
     writes): from push% under the tempo to push% over it across the build, one step per beat, back to the new
     section's own tempo on its downbeat. Not written when the song has audio clips (they would drift; tempo='force'
     writes anyway) or tempo automation clips.
  3. dynamics - a crescendo (v50 -> v95 at intensity 1) on the parts already there: each note is scaled by the
     target over its bar's average velocity, so accents and the human variation stay (scaled, not flattened).
  4. melody - never rewritten (pitch, timing and velocity stay; melody_dynamics=True lets its velocities rise too):
     the result says where its top note is and suggests a peak in the last bar before the release.
  5. release - a timpani roll (else a soft low-tom / orchestral-drum roll on such a channel, else a reversed-cymbal
     swell made by flh_fx on a new sampler) swelling over the last 1-2 beats with its peak ON the downbeat; low
     strings (a celli / basses channel by journal role / name) enter sustained on the downbeat; a song without one
     gets a new 'Low Strings' channel with the owned instrument flh_new_song's ranking picks (LABS / FLEX / FL's own
     strings / Sytrus / 3x Osc, edition-aware), else a copy of its strings channel, else a note saying what to add.
     Timpani hits inside the build are retuned to the ladder's bass notes.
Patterns that also play outside the build (or several times inside it) get their own copy for the clip that is
changed ('<pattern> @<bar>'), so the rest of the song plays as before. One build per downbeat: build again after
flp_restore. dry_run=True returns the plan and writes nothing (no FX file is made).
Extension contract: see flh_store. Tools: flp_build_rise, flp_rise_write (the batch writer it uses).
"""
import json
import math
import os
import random
import re
import statistics
import struct
import sys
import time

H = {}                                                   # the server namespace, set by register()
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "rise")
DEFAULTS = {
    "ladders": {"minor": ["#vii°7", "iv°7", "ii°7", "Vsus4"], "major": ["vii°7", "ii°7", "#iv°7", "Vsus4"]},
    "climb_semitones": 3,
    "tempo": {"push": 0.083},
    "dynamics": {"from": 50, "to": 95},
    "roll": {"beats": 2, "short_beats": 1, "strokes_per_s": 12, "from_vel": 34, "strike_over": 12, "strike_beats": 1},
    "low_strings": {"bars": 2, "vel": 0.9, "register": [36, 47], "name": "Low Strings", "recipe": "cinematic"},
    "words": {"timpani": ["timpani", "timp", "kettle"],
              "drum": ["taiko", "tom", "toms", "orch drum", "orchestral drum", "war drum", "surdo", "gran cassa",
                       "frame drum"],
              "low_strings": ["low string", "cello", "celli", "contrabass", "double bass", "basses", "bass string",
                              "string bass"]},
    "roles": {"timpani": ["timpani"], "drum": ["tom", "perc drum"], "low_strings": ["low strings", "celli", "cello"]},
}
REMAP_ROLES = {"chords", "pad", "strings", "organ", "choir", "brass", "arp", "pluck", "unknown"}
BASS_ROLES = {"bass", "low strings"}
LINE_ROLES = {"melody", "counter", "bell"}               # lines: never re-voiced (velocity only)
HOLD_ROLES = ("strings", "pad", "choir", "organ", "brass", "chords")   # write-mode preference (silent channels)
SUSTAIN_FAMILY = {"strings", "pad", "choir", "organ", "brass"}
_CACHE = {}


def _mod(name):
    m_ = sys.modules.get(name)
    if m_ is None:
        import importlib
        m_ = importlib.import_module(name)
    return m_


def _C():
    return _mod("flh_compose")


def _SC():
    return _mod("flh_score")


def _merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def rise_data():
    """data/rise/rise.json under the user's <state folder>/rise/rise.json (key by key), over built-in defaults."""
    paths = []
    try:
        if H.get("STATE"):
            paths.append(os.path.join(os.path.dirname(H["STATE"]), "rise", "rise.json"))
    except Exception:
        pass
    paths.append(os.path.join(DATA, "rise.json"))
    sig = tuple((p, os.path.getmtime(p)) for p in paths if os.path.isfile(p))
    if _CACHE.get("sig") == sig and "data" in _CACHE:
        return _CACHE["data"]
    out = dict(DEFAULTS)
    for p, _ in reversed(sig):                           # shipped first, the user's file on top
        try:
            with open(p, encoding="utf-8") as fh:
                out = _merge(out, json.load(fh))
        except Exception:
            pass
    _CACHE.update(sig=sig, data=out)
    return out


# ============================================================================= pure music helpers (tested directly)
_LETTERS = "CDEFGAB"
_NATURAL = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def spell_root(key, rn, pc):
    """The root's name spelled from the scale degree (B minor #vii = A#, F# minor #vii = E#, D minor #vii = C#), so a
    raised leading tone never reads as a flat; falls back to the key's sharps / flats when the key has no letter."""
    C = _C()
    m_ = re.match(r"^(b|#)?(vii|vi|iv|v|iii|ii|i)", str(rn), re.I)
    k = str(key).strip()
    if m_ and k[:1].upper() in _NATURAL:
        letter = _LETTERS[(_LETTERS.index(k[0].upper()) + {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5,
                                                            "vii": 6}[m_.group(2).lower()]) % 7]
        diff = ((pc - _NATURAL[letter] + 6) % 12) - 6
        if abs(diff) <= 2:
            return letter + ("#" * diff if diff > 0 else "b" * -diff)
    return C._pc_name(pc, C._flats_for(key))


def ladder_chords(key, data=None):
    """The ladder for a key: [{rn, root, q, pcs, name}] from data 'ladders' (minor / major family)."""
    C = _C()
    data = data or rise_data()
    _, _, _, fam = C.key_info(key)
    out = []
    for rn in data["ladders"].get(fam) or DEFAULTS["ladders"][fam]:
        c = C.chord_from_token(rn, key)
        pcs = sorted({(c["root"] + i) % 12 for i in c["iv"]})
        out.append({"rn": rn, "root": c["root"], "q": c["q"], "iv": list(c["iv"]), "pcs": pcs,
                    "name": spell_root(key, rn, c["root"]) + c["q"]})
    return out


def segments(bar_starts, beat_ticks, end_t, n):
    """Where n ladder chords start and end: on bar lines when there are at least n bars (floor(i * bars / n), so the
    last chord holds at least a bar), else spread over the beats. -> [(start tick, end tick)]."""
    nb = len(bar_starts)
    if nb >= n:
        st = [bar_starts[int(math.floor(i * nb / n))] for i in range(n)]
    else:
        k = len(beat_ticks)
        st = [beat_ticks[min(k - 1, int(round(i * k / n)))] for i in range(n)]
    st = sorted(set(st))
    return [(a, st[i + 1] if i + 1 < len(st) else end_t) for i, a in enumerate(st)]


def _semi(a, b):
    """True when two pitch classes / pitches are a semitone (or major 7th / compound) apart."""
    return (a - b) % 12 in (1, 11)


def _over(mel, t, ln):
    """Melody notes sounding in [t, t + ln): [(key, overlap ticks)]."""
    out = []
    for n in mel:
        ov = min(t + ln, n["t"] + n["len"]) - max(t, n["t"])
        if ov > 0:
            out.append((n["key"], ov))
    return out


def bass_pcs(chords, segs, mel):
    """The bass pitch class per ladder chord: its root, unless the melody rubs against it (a semitone away for more
    than a fifth of the chord) - then the chord tone (inversion) that rubs least (dim7: any tone; sus4: root or 5th)."""
    out = []
    for ch, (a, b) in zip(chords, segs):
        L = max(1, b - a)
        over = _over(mel, a, L)
        w = lambda pc: sum(ov for k, ov in over if _semi(k, pc)) / L
        if ch["q"] == "dim7":
            order = [(ch["root"] + 3 * j) % 12 for j in range(4)]
        else:
            order = [ch["root"], (ch["root"] + 7) % 12]
        if w(order[0]) <= 0.2:
            out.append(order[0])
        else:
            out.append(min(order, key=lambda pc: (round(w(pc), 3), order.index(pc))))
    return out


def _nearest_pc(pc, ref, lo=0, hi=127):
    best = None
    for p in range(max(0, ref - 12), min(127, ref + 12) + 1):
        if p % 12 == pc and lo <= p <= hi:
            if best is None or abs(p - ref) < abs(best - ref) or (abs(p - ref) == abs(best - ref) and p < best):
                best = p
    return best if best is not None else ref - ((ref - pc) % 12)


def remap_pieces(pieces, chords, segs, mel, shifts, bpc, reg):
    """Re-voice accompaniment pieces onto the ladder. pieces: [{t, len, key, bass (bool), ...}] (sounding pitch);
    -> the same dicts with 'new' (pitch or None = leave this piece out). Upper notes go to the nearest ladder tone
    of (old pitch + the chord's climb), never a semitone / major 7th from a melody note sounding with them when
    another tone works, and under that melody note when they were under it; the group's notes stay distinct."""
    lo, hi = reg
    groups = {}
    for p in pieces:
        groups.setdefault(p["t"], []).append(p)
    for t in sorted(groups):
        grp = sorted(groups[t], key=lambda p: p["key"])
        i = max(j for j, (a, _) in enumerate(segs) if a <= t) if any(a <= t for a, _ in segs) else 0
        pcs = set(chords[i]["pcs"])
        used = set()
        for p in grp:
            over = _over(mel, p["t"], p["len"])
            if p.get("bass"):
                new = _nearest_pc(bpc[i], p["key"], 24, 64)
                p["new"] = new
                used.add(new)
                continue
            bad = {k for k, _ in over}
            under = min(bad) if bad else None
            target = p["key"] + shifts[i]
            cands = [x for x in range(target - 8, target + 9) if x % 12 in pcs and x not in used and 0 <= x <= 127]

            def cost(x):
                rub = any(_semi(x, k) for k in bad)
                cross = under is not None and p["key"] < under and x >= under
                out_reg = x > hi + 7 or x < lo - 5
                return (cross, rub, out_reg, abs(x - target), x)
            cands.sort(key=cost)
            new = cands[0] if cands else None
            if new is not None and cost(new)[0] and new - 12 >= lo - 12 and (new - 12) not in used:
                new -= 12                                  # still over the melody: an octave down
            if new is None and len(grp) == 1:
                new = _nearest_pc(min(pcs, key=lambda pc: abs(((pc - target) + 6) % 12 - 6)), target)
            p["new"] = new
            if new is not None:
                used.add(new)
    return pieces


def voicings(ch, lo, hi):
    """Close voicings of a ladder chord inside [lo, hi]: dim7 = 4 stacked minor thirds from each tone (and the
    3-note voicings without one tone); sus4 = R 4 5 (+ R on top). -> [[pitches]]"""
    pcs = ch["pcs"]
    out = []
    if ch["q"] == "dim7":
        for start in pcs:
            for base in range(lo, hi + 1):
                if base % 12 != start:
                    continue
                v = [base + 3 * j for j in range(4)]
                if v[-1] <= hi:
                    out.append(v)
                    out += [v[:j] + v[j + 1:] for j in range(4)]
    else:
        r = ch["root"]
        tones = [0, 5, 7]
        for base in range(lo, hi + 1):
            if base % 12 != r:
                continue
            v = [base + x for x in tones]
            for inv in range(3):                          # root position and both inversions
                w = sorted(v[inv:] + [x + 12 for x in v[:inv]])
                if w[-1] <= hi:
                    out.append(w)
                    if w[-1] + 5 <= hi and (w[0] + 12) not in w:
                        out.append(w + [w[0] + 12])
    return out


def pick_voicings(chords, segs, mel, lo, hi):
    """One voicing per ladder chord: under the melody, no rubs against it, the top voice climbing chord by chord."""
    out, prev_top = [], None
    for i, (ch, (a, b)) in enumerate(zip(chords, segs)):
        over = _over(mel, a, max(1, b - a))
        under = min((k for k, _ in over), default=None)
        best = None
        for v in voicings(ch, lo, hi) or voicings(ch, lo - 12, hi + 12):
            rub = sum(ov for x in v for k, ov in over if _semi(x, k)) / max(1, b - a)
            cross = sum(1 for x in v if under is not None and x >= under - 1)
            tgt = (lo + 0.45 * (hi - lo)) if prev_top is None else prev_top + 2
            c = 6 * cross + 4 * rub + 0.35 * abs(v[-1] - tgt) + (0.8 if len(v) < len(ch["pcs"]) else 0) + \
                (2 if prev_top is not None and v[-1] < prev_top else 0)
            if best is None or c < best[0]:
                best = (c, v)
        v = best[1] if best else sorted(_nearest_pc(pc, (lo + hi) // 2) for pc in ch["pcs"])
        out.append(v)
        prev_top = v[-1]
    return out


def crescendo(x, lo, hi):
    """Target velocity at x in [0, 1] of the build."""
    return lo + (hi - lo) * max(0.0, min(1.0, x))


def tempo_ramp(beat_ticks, base, push, intensity):
    """[(tick, bpm)] one step per beat from base*(1 - push*i) up to base*(1 + push*i) (monotonic)."""
    i = max(0.25, min(2.0, float(intensity)))
    lo, hi = base * (1 - push * i), base * (1 + push * i)
    n = len(beat_ticks)
    return [(t, round(max(10.0, min(522.0, lo + (hi - lo) * (k / (n - 1) if n > 1 else 1.0))), 2))
            for k, t in enumerate(beat_ticks)]


def roll_notes(z_t, P, beats_q, bpm, cfg, peak, pitch):
    """A roll into tick z_t: strokes evenly spaced over beats_q quarter notes before it, velocities rising from
    cfg.from_vel to just under the peak, then the strike ON z_t at the peak. -> [(tick, len ticks, pitch, vel)]"""
    sps = float(cfg.get("strokes_per_s", 12))
    per_q = sps * 60.0 / max(20.0, bpm)
    per_q = min((4, 6, 8, 12, 16, 24), key=lambda n: abs(n - per_q))
    n = max(2, int(round(beats_q * per_q)))
    span = beats_q * P
    lo = int(cfg.get("from_vel", 34))
    out = []
    for k in range(n):
        t = z_t - span + span * k / n
        x = k / max(1, n - 1)
        v = lo + (peak - 6 - lo) * (x ** 1.6)
        out.append((int(round(t)), max(1, int(round(span / n))), pitch, int(round(max(1, min(126, v))))))
    out.append((int(z_t), int(round(P * float(cfg.get("strike_beats", 1)))), pitch, int(min(127, peak))))
    return out


# ============================================================================= reading the song
def _clip_table(d, ev):
    """Pattern clips of the current arrangement: [{i, pos, len, pid, ws, we, muted, track}]."""
    try:
        _, _, recs = H["_playlist_at"](d, ev)
    except Exception:
        return []
    PB = H["PATTERN_BASE"]
    mute = H.get("CLIP_MUTE", 0x2000)
    out = []
    for i, r in enumerate(recs):
        item, = struct.unpack("<H", r[6:8])
        if struct.unpack("<H", r[4:6])[0] != PB or item < PB:
            continue
        pos, = struct.unpack("<I", r[0:4])
        ln, = struct.unpack("<I", r[8:12])
        ws = H["_clip_offsets"](r)[0]
        out.append({"i": i, "pos": pos, "len": ln, "pid": item - PB, "ws": ws, "we": ws + ln,
                    "muted": bool(struct.unpack("<H", r[18:20])[0] & mute), "track": struct.unpack("<H", r[12:14])[0]})
    return out


def _heard(d, ev, clips=None, pats=None):
    """Every note an unmuted clip plays: [{t, len, ch, key (written), vel, ci, rid, pid}]."""
    clips = clips if clips is not None else _clip_table(d, ev)
    pats = pats if pats is not None else H["_patterns"](d, ev)
    out = []
    for c in clips:
        pat = pats.get(c["pid"])
        if c["muted"] or not pat:
            continue
        for j, r in enumerate(pat["notes"]):
            if c["ws"] <= r[0] < c["we"]:
                out.append({"t": c["pos"] + r[0] - c["ws"], "len": max(1, min(r[3], c["we"] - r[0])), "ch": r[2],
                            "key": r[4], "vel": r[11], "ci": c["i"], "rid": j, "pid": c["pid"]})
    out.sort(key=lambda n: (n["t"], n["ch"], n["key"]))
    return out


def _text_of(v, ch):
    info = v["channels"].get(ch) or {}
    jc = ((v["journal"].get("channels") or {}).get(str(ch)) or {}) if isinstance(v["journal"].get("channels"), dict) else {}
    return " ".join(str(x or "") for x in (info.get("name"), info.get("plugin"), info.get("sample"),
                                          jc.get("name") if isinstance(jc, dict) else None)).lower()


def _journal_role(v, ch):
    jc = (v["journal"].get("channels") or {}).get(str(ch)) if isinstance(v["journal"].get("channels"), dict) else None
    return str((jc or {}).get("role") or "").lower() if isinstance(jc, dict) else ""


def _has_word(text, words):
    t = " " + re.sub(r"[^a-z0-9 ]", " ", text) + " "
    return any((" " + w + " ") in t or (len(w) > 4 and w in t) for w in words)


def find_channel(v, what, data=None):
    """The channel of a role in the song: the compose registry, the journal's channel roles, then channel / plugin /
    sample names (data 'words'). what: timpani | drum | low_strings -> channel or None."""
    data = data or rise_data()
    words = data["words"].get(what) or []
    roles = [r.lower() for r in (data.get("roles") or {}).get(what) or []]
    reg = (v.get("registry") or {}).get("channels") or {}
    for r in roles:
        c = reg.get(r)
        if isinstance(c, list):
            c = c[0] if c else None
        try:
            if c is not None and int(c) in v["channels"]:
                return int(c)
        except (TypeError, ValueError):
            pass
    cands = []
    for ch, info in sorted(v["channels"].items()):
        if info.get("type") in (4, 5):
            continue
        jr = _journal_role(v, ch)
        txt = _text_of(v, ch)
        if what == "low_strings" and v["roles"].get(ch) == "low strings":
            cands.append((0, ch))
        elif jr and jr in roles:
            cands.append((1, ch))
        elif _has_word(txt, words):
            cands.append((2, ch))
        elif what == "drum" and v["roles"].get(ch) == "tom":
            cands.append((3, ch))
    if what == "drum":                                    # a timpani channel is the timpani, not a tom
        tim = find_channel(v, "timpani", data)
        cands = [x for x in cands if x[1] != tim]
    return min(cands)[1] if cands else None


def _catalog_hint(query, kind):
    """The best owned instrument for a role from fl_sound_catalog (a name and how to load it), or None."""
    try:
        r = H["TOOLS"]["fl_sound_catalog"][0](query=query, kind=kind, limit=5, type="instrument")
    except Exception:
        return None
    for x in (r or {}).get("results") or []:
        if x.get("owned") is True:                        # unknown / trial: not offered as owned
            # owned = owned or free per the catalog, which can list a free library before it's installed: say so
            src = str(x.get("source") or "")
            said = f"{x.get('name')} ({src}" + ("; check it's installed" if src in ("BBC", "LABS") else "") + ")"
            return {"name": x.get("name"), "source": src, "said": said,
                    **({"load": x["load"]} if x.get("load") else {})}
    return None


def _new_low_strings(v, cfg, roles, after):
    """A new low-strings channel when the song has none: the owned instrument flh_new_song's ranking picks for a
    'Low Strings' part (data/templates: LABS / FLEX / FL's own strings / Sytrus / 3x Osc - edition-aware, every
    third-party choice optional), else a copy of the song's strings channel (its sound, played an octave or two
    lower). -> {channel, name, ops, sound, journal} or {skipped: why, tried: [...]}.
    PC-VERIFY: the new channel loads the chosen preset and sounds (a LABS / FLEX pick shows its sound in FL)."""
    lc = cfg.get("low_strings") or {}
    name = str(lc.get("name") or "Low Strings")
    idx = max([int(c) for c in v["channels"]] + [int(a) for a in after], default=-1) + 1
    tried = []
    try:
        r = _mod("flh_newsong")._next_sound(lc.get("recipe") or "cinematic", idx, {"role": "bass", "name": name},
                                            v.get("journal") or {}, ())
    except Exception as e:
        r = {"kind": "empty", "skipped": [f"the sound choice failed ({type(e).__name__}: {str(e)[:60]})"]}
    tried = list((r or {}).get("skipped") or [])[:5]
    chans = v["channels"]
    usable = lambda c: (chans.get(c) or {}).get("type") not in (4, 5) and roles.get(c) != "fx"
    gens = [c for c in sorted(chans) if (chans[c] or {}).get("type") == 2 and usable(c)]
    samplers = [c for c in sorted(chans) if (chans[c] or {}).get("type") == 0 and usable(c)]
    strings = [c for c in sorted(chans) if usable(c) and (roles.get(c) == "strings" or
                                                           _has_word(_text_of(v, c), ["strings", "string", "violins"]))]
    ops, sound = [], None
    kind = (r or {}).get("kind")
    if kind in ("plugin", "labs") and r.get("fst") and (gens or samplers):
        ops.append({"tool": "flp_add_channel", "clone_from": (strings + gens + samplers)[0], "preset": r["fst"]})
        if kind == "labs":
            ops.append({"tool": "flp_instrument_sound", "channel": idx, "pack": r["pack"], "preset": r.get("preset")})
        sound = r.get("label") or r.get("plugin") or os.path.basename(str(r["fst"]))
    elif kind == "sample" and r.get("path") and samplers:
        ops += [{"tool": "flp_add_channel", "clone_from": samplers[0]},
                {"tool": "flp_load_sample", "channel": idx, "sample": r["path"]}]
        sound = os.path.basename(str(r["path"]))
    elif strings:
        ops.append({"tool": "flp_add_channel", "clone_from": strings[0]})
        sound = f"a copy of {(chans[strings[0]] or {}).get('name') or 'channel ' + str(strings[0])} (its sound, lower)"
    else:
        return {"skipped": "no celli / low-strings channel in the song, no owned strings instrument to load and no "
                           "strings channel to copy", "tried": tried}
    ops.append({"tool": "flp_style_channels", "channels": [{"channel": idx, "name": name}]})
    jc = {"role": "bass", "name": name}
    if kind in ("plugin", "labs", "sample"):
        jc.update({k: r[k] for k in ("plugin", "pack", "preset") if r.get(k)})
    return {"channel": idx, "name": name, "ops": ops, "sound": sound, "tried": tried, "journal": jc}


# ============================================================================= the batch writer
def _rec_with(r, pos=None, ln=None, key=None, vel=None):
    r = list(r)
    if pos is not None:
        r[0] = int(pos)
    if ln is not None:
        r[3] = max(1, int(ln))
    if key is not None:
        r[4] = max(0, min(127, int(key)))
    if vel is not None:
        r[11] = max(1, min(127, int(vel)))
    return tuple(r)


def _heard_sig(notes):
    from collections import Counter
    return Counter((n["t"], n["len"], n["ch"], n["key"], n["vel"]) for n in notes)


def flp_rise_write(project, output, edits):
    """flp_build_rise's note write (an flp_edit op): edits [{t (song tick), ch, k (written pitch), parts [[dt, len,
    key, vel], ...]}] - the note the playlist plays at song tick t becomes these parts (dt from its start; [] = the
    note goes). Notes are found by what the playlist plays; a clip whose pattern also plays elsewhere gets its own
    copy '<pattern> @<bar>'. Verified before saving: everything else plays exactly as before."""
    H["writable"](output)
    d = H["_load"](project)
    ev = H["_events"](d)
    P = H["_ppq"](d)
    clips = _clip_table(d, ev)
    pats = H["_patterns"](d, ev)
    want = {}
    for e in edits or []:
        want[(int(e["t"]), int(e["ch"]), int(e["k"]))] = [[int(x) for x in p_[:4]] for p_ in e.get("parts") or []]
    hits, found = {}, set()
    for c in clips:
        pat = pats.get(c["pid"])
        if c["muted"] or not pat:
            continue
        for j, r in enumerate(pat["notes"]):
            if c["ws"] <= r[0] < c["we"]:
                k = (c["pos"] + r[0] - c["ws"], r[2], r[4])
                if k in want:
                    hits.setdefault(c["i"], {})[j] = want[k]
                    found.add(k)
    miss = set(want) - found
    if miss:
        raise ValueError(f"the project changed since the rise was planned ({len(miss)} of {len(want)} notes aren't "
                         "where they were): run flp_build_rise again")
    by_pid = {}
    for c in clips:
        by_pid.setdefault(c["pid"], []).append(c)
    cl = {c["i"]: c for c in clips}
    before = _heard(d, ev, clips, pats)
    try:
        meters = H["_meter_map"](d, ev)
        bar_of = lambda t: round(H["_tick_to_bar"](t, meters, P), 2)
    except Exception:
        bar_of = lambda t: round(t / (4.0 * P) + 1, 2)

    def applied(recs, h, c):
        out, added = [], []
        for j, r in enumerate(recs):
            if j not in h:
                out.append(r)
                continue
            for n_, (dt, ln, key, vel) in enumerate(h[j]):
                if r[0] + dt >= c["we"]:
                    continue                              # past the clip's end: it wouldn't play
                rr = _rec_with(r, pos=r[0] + dt, ln=ln, key=key, vel=vel)
                (out if n_ == 0 else added).append(rr)
        return sorted(out + added, key=lambda r: (r[0], r[2], r[4]))
    in_place, clones, kept_ci = {}, [], set()
    order = sorted(hits, key=lambda i: (cl[i]["pos"], i))
    for ci in order:
        c = cl[ci]
        pid = c["pid"]
        new = applied(list(pats[pid]["notes"]), hits[ci], c)
        if len(by_pid[pid]) == 1:
            in_place[pid] = new                           # only this clip plays the pattern
        elif pats[pid]["ctrl"]:
            kept_ci.add(ci)                               # a copy would lose the pattern's automation
        elif pid not in in_place and all(x["i"] in hits for x in by_pid[pid]):
            in_place[pid] = new                           # every clip is rebuilt: the first keeps the pattern
        else:
            clones.append((c, new))
    NR = H["NOTE_RECORD"]
    PB = H["PATTERN_BASE"]
    nd = d
    if in_place:
        note_ks = H["_pattern_note_events"](nd, ev)
        edits_b = []
        for pid, recs in in_place.items():
            body = b"".join(struct.pack(NR, *r) for r in recs)
            if not note_ks.get(pid):
                raise ValueError(f"pattern {pid} has no notes event to rewrite")
            edits_b += H["_notes_event_edits"](ev, note_ks[pid], body)
        nd = H["_splice"](nd, ev, sorted(edits_b))
    made = []
    for c, recs in clones:                                # PC-VERIFY: FL plays a pattern copy like the original
        nm = f"{pats[c['pid']]['name'] or 'Pattern ' + str(c['pid'])} @{bar_of(c['pos']):g}"
        nd, npid = H["_add_pattern"](nd, nm)
        e2 = H["_events"](nd)
        ks = H["_pattern_note_events"](nd, e2)[npid]
        nd = H["_splice"](nd, e2, H["_notes_event_edits"](e2, ks, b"".join(struct.pack(NR, *r) for r in recs)))
        e2 = H["_events"](nd)
        kpl, _, recs_ = H["_playlist_at"](nd, e2)
        recs_[c["i"]][6:8] = struct.pack("<H", PB + npid)
        nd = H["_splice"](nd, e2, [(kpl, kpl + 1, H["_data_event"](H["EV_PLAYLIST"],
                                                                     b"".join(bytes(r) for r in recs_)))])
        made.append({"pattern": nm, "bar": bar_of(c["pos"])})
    # ---- verify: the edited notes became their parts, everything else plays as before
    after = _heard(nd, H["_events"](nd))
    exp = _heard_sig(before)
    for n in before:
        k = (n["t"], n["ch"], n["key"])
        if k in want and n["ci"] in hits and n["ci"] not in kept_ci:
            exp[(n["t"], n["len"], n["ch"], n["key"], n["vel"])] -= 1
            c = cl[n["ci"]]
            end = c["pos"] + c["len"]
            for dt, ln, key, vel in want[k]:
                t2 = n["t"] + dt
                if t2 >= end:
                    continue
                exp[(t2, max(1, min(ln, end - t2)), n["ch"], max(0, min(127, key)), max(1, min(127, vel)))] += 1
    exp = +exp
    got = _heard_sig(after)
    if exp != got:
        diff = sum(((exp - got) + (got - exp)).values())
        raise RuntimeError(f"the rise write reads back differently from its plan ({diff} notes differ), so nothing was "
                           "saved; please report this project")
    H["_save"](nd, output)
    res = {"output": output, "notes_changed": sum(len(h) for ci, h in hits.items() if ci not in kept_ci),
           "patterns_in_place": len(in_place)}
    if made:
        res["made_unique"] = made
    if kept_ci:
        res["not_changed"] = [f"{pats[cl[ci]['pid']]['name'] or 'Pattern ' + str(cl[ci]['pid'])} at bar "
                              f"{bar_of(cl[ci]['pos']):g} (the pattern holds automation and also plays elsewhere)"
                              for ci in sorted(kept_ci)]
    return res


# ============================================================================= planning
def _resolve_into(v, into, project):
    """(label, bar, end bar or None) of the section / bar the rise leads into."""
    C = _C()
    if isinstance(into, bool) or into is None:
        raise ValueError("give into= a section name (as the song journal / markers name it) or a bar number")
    if isinstance(into, (int, float)):
        bar = float(into)
        name = None
    else:
        s = str(into).strip()
        mm = re.fullmatch(r"(?:bar\s*)?(\d+(?:\.\d+)?)", s, re.I)
        if mm:
            bar, name = float(mm.group(1)), None
        else:
            occ = C.occurrences(v, s)
            if not occ:
                secs = list(v["sections"])
                if not secs:
                    try:
                        secs = _C()._store().sections_from_project(project) or []
                        v["sections"] = [x for x in secs if x.get("name") and x.get("bar") is not None]
                        occ = C.occurrences(v, s)
                    except Exception:
                        secs = []
                if not occ:
                    have = sorted({x["name"] for x in v["sections"]}) if v["sections"] else []
                    raise ValueError(f"no section '{s}' in the song" + (f" (have: {', '.join(have)})" if have else
                                     " (no sections in the journal or markers): give into= a bar number"))
            occ = [o for o in occ if o["bar"] > 1.0] or occ
            o = occ[0]
            others = [f"{x['bar']:g}" for x in occ[1:]]
            return o["name"], float(o["bar"]), float(o["end_bar"]), others
    sec = None
    for x in sorted(v["sections"], key=lambda x: x["bar"]):
        if abs(float(x["bar"]) - bar) < 1e-6:
            sec = x
    nxt = min((float(x["bar"]) for x in v["sections"] if float(x["bar"]) > bar + 1e-6), default=None)
    return (sec["name"] if sec else name or f"Bar {bar:g}"), bar, (float(sec["end_bar"]) if sec and sec.get("end_bar")
                                                                   else nxt), []


def _beats_in(v, a_bar, z_bar):
    """(bar start ticks, beat ticks, [(bar, meter)]) of bars [a, z)."""
    C = _C()
    P = v["P"]
    bars, beats, mts = [], [], []
    for b in range(int(a_bar), int(z_bar)):
        t0 = C._bar_tick(v, b)
        (num, den), _ = C._meter_at(v, b)
        try:
            mt = C.meter_info(f"{num}/{den}")
        except ValueError:
            mt = C.meter_info("4/4")
        bars.append(t0)
        mts.append((b, mt))
        beats += [int(round(t0 + s * P / 4.0)) for s in mt["beats"]]
    return bars, beats, mts


def _key_for(v, project, key, a_t, end_t):
    C = _C()
    if key:
        try:
            C.key_info(key)
        except Exception:
            raise ValueError(f"key '{key}' not understood: e.g. 'B minor', 'Eb major', 'F# dorian'") from None
        return str(key), "given"
    jk = v["registry"].get("key") or v["journal"].get("key")
    est = []
    try:
        m = _SC().load_model(v["src"], journal_from=project)
        est = _SC().key_estimate(m, a_t, end_t, 3) or []
    except Exception:
        est = []
    if jk:
        try:
            C.key_info(jk)
        except Exception:
            jk = None
    if jk:
        same = lambda a, b: (C.key_info(a)[0] == C.key_info(b)[0] and C.key_info(a)[3] == C.key_info(b)[3]) or \
            set(C.scale_pcs(a)) == set(C.scale_pcs(b))      # the relative key: the notes can't tell them apart
        if est and est[0][1] >= 0.75 and not any(same(jk, x[0]) for x in est[:2]):
            return est[0][0], f"the notes around the climax (the journal says {jk})"
        return str(jk), "song journal"
    if est and est[0][1] >= 0.5:
        return est[0][0], "detected from the notes (a guess)"
    k = C.song_key(v)
    if k:
        return k, "detected from the song (a guess)"
    raise ValueError("can't tell the song's key from its notes: pass key= (like 'B minor')")


def _melody(v, project, a_t, z_t):
    """(channel, skyline?, melody line notes [{t, len, key (sounding), ch}] around the build, protected
    {(t, ch, written key)})."""
    try:
        m = _SC().load_model(v["src"], journal_from=project)
        mi = m.get("melody")
        if not mi:
            return None, False, [], set()
        SC = _SC()
        line = SC.melody_line(m)
        offs = v.get("offsets") or {}
        mel = [{"t": int(n[SC.POS]), "len": int(n[SC.LEN]), "key": int(n[SC.KEY]), "ch": int(n[SC.CH])}
               for n in line if n[SC.POS] < z_t + 4 * v["P"] and n[SC.POS] + n[SC.LEN] > a_t - 4 * v["P"]]
        prot = {(n["t"], n["ch"], n["key"] + offs.get(n["ch"], 0)) for n in mel}
        return int(mi["ch"]), bool(mi.get("skyline")), mel, prot
    except Exception:
        return None, False, [], set()


def _register_of(v, ch, offs):
    ks = sorted(n["key"] - offs.get(ch, 0) for n in v["_heard"] if n["ch"] == ch)
    if len(ks) >= 4:
        return [ks[len(ks) // 10], ks[min(len(ks) - 1, (9 * len(ks)) // 10)]]
    return None


def _tempo_plan(v, a_t, z_t, beat_ticks, intensity, cfg, tempo):
    """{points [[flat bar, bpm]], ramp, lo, hi, back} or {skipped: why}."""
    d, ev, P = v["d"], v["ev"], v["P"]
    try:
        if H["_tempo_automation"](d, ev, P):
            return {"skipped": "the song automates its tempo with an automation clip, which overrides tempo events: "
                               "draw the accelerando in that clip (bars of the build)"}
    except Exception:
        pass
    audio = [c.get("name") for c in v["clips"] if c.get("kind") == "audio" and not c.get("muted")]
    if audio and tempo != "force":
        return {"skipped": f"the song has audio clips ({', '.join(str(a) for a in audio[:3])}) that would drift out of "
                           "sync with a tempo change; tempo='force' writes it anyway"}
    lane = [pid for pid, p in v["patterns"].items() if str(p.get("name") or "").strip().lower() == "tempo"]
    TC = H["TEMPO_CTRL"]
    others = [pid for pid, p in v["patterns"].items() if pid not in lane and p.get("ctrl") and
              any((r[4], r[7]) == TC for r in H["_ctrl_records"](p["ctrl"]))]
    if lane:
        cl = [c for c in v["_clips"] if c["pid"] == lane[0]]
        if len(cl) != 1 or cl[0]["pos"] != 0 or cl[0]["ws"] != 0 or cl[0]["pos"] + cl[0]["len"] <= z_t:
            return {"skipped": "the song's Tempo lane isn't one clip from the song start past the climax, so its "
                               "events can't be placed by song position: lengthen / move it to bar 1, then build again"}
        if others:
            return {"skipped": "tempo events sit in other patterns besides the Tempo lane and would fight the "
                               "accelerando: keep the tempo map in one lane (flp_tempo_write), then build again"}
    old = sorted((int(t), float(b)) for t, b in (v.get("tempos") or [(0, v["tempo"])]))
    if not old or old[0][0] > 0:
        old.insert(0, (0, float(v["tempo"])))
    at = lambda t: next((b for x, b in reversed(old) if x <= t), old[0][1])
    base, back = at(a_t), at(z_t)
    ramp = tempo_ramp(beat_ticks, base, float(cfg.get("push", 0.083)), intensity)
    inside = sum(1 for t, _ in old if a_t <= t < z_t and t != 0)
    new = [x for x in old if x[0] < a_t or x[0] >= z_t] + ramp
    if not any(t == z_t for t, _ in new):
        new.append((z_t, back))
    new = sorted(dict(new).items())
    bpb = float(v.get("bpb") or 4)
    pts = [[round(1 + t / (bpb * P), 6), b] for t, b in new]
    return {"points": pts, "ramp": ramp, "lo": ramp[0][1], "hi": ramp[-1][1], "back": back, "base": base,
            "replaced_events": inside, "lane": "Tempo" + ("" if lane else " (new)"), "map": new}


def _seconds(tmap, a, b, P):
    """Seconds the tempo map plays song ticks [a, b) in."""
    s, pts = 0.0, sorted(tmap)
    for i, (t, bpm) in enumerate(pts):
        t1 = pts[i + 1][0] if i + 1 < len(pts) else b
        lo, hi = max(a, t), min(b, t1)
        if hi > lo:
            s += (hi - lo) / P * 60.0 / bpm
    return s


def _pieces(n, cuts, a_t, z_t, min_tail):
    """A note cut at the ladder's chord changes inside the build: [(dt, len)] pieces from its start; the part
    before the build stays (dt 0); nothing past the climax's downbeat."""
    t, e = n["t"], min(n["t"] + n["len"], z_t)
    marks = sorted({x for x in cuts if t < x < e})
    if t < a_t:
        if e - a_t < min_tail:                             # barely reaches in: just ends at the build
            return [(0, a_t - t, False)]
        marks = sorted(set(marks) | {a_t})
    edges = [t] + marks + [e]
    out = []
    for x, y in zip(edges, edges[1:]):
        if y - x >= 1:
            out.append((x - t, y - x, x >= a_t))
    if t + n["len"] > z_t and e == z_t:                    # held into the new section: that part plays as written
        out.append((z_t - t, t + n["len"] - z_t, False))
    return out


def _bb(v, t):
    C = _C()
    b = C._tick_bar(v, t)
    bar = int(math.floor(b + 1e-6))
    beat = round((t - C._bar_tick(v, bar)) / v["P"] + 1, 2)
    return f"bar {bar} beat {beat:g}"


# ============================================================================= the tool
def flp_build_rise(project, into, bars=4, intensity=1.0, harmony=True, tempo=True, dynamics=True, roll=True,
                   low_strings=True, dry_run=False, key=None, melody_dynamics=False):
    """See the module docstring and register()."""
    T0 = time.time()
    steps = {}
    C = _C()
    project = os.path.abspath(str(project))
    if not os.path.isfile(project):
        raise FileNotFoundError(f"project not found: {project}")
    C._fresh(project)
    v = C.project_view(project)
    steps["read"] = round(time.time() - T0, 3)
    cfg = rise_data()
    intensity = max(0.25, min(2.0, float(intensity if intensity is not None else 1.0)))
    P = v["P"]
    offs = v.get("offsets") or {}
    label, z_bar, z_end, other_occ = _resolve_into(v, into, project)
    if abs(z_bar - round(z_bar)) > 1e-6:
        raise ValueError(f"into bar {z_bar:g} isn't a downbeat: the rise lands on a bar line")
    z_bar = int(round(z_bar))
    bars = int(bars) if bars is not None else 4
    notes = []
    if bars < 1:
        raise ValueError("bars must be at least 1")
    if v["clips"] and z_bar > float(v["end_bar"]) + 1e-6:
        raise ValueError(f"bar {z_bar} is after the song's end (bar {v['end_bar']:g}): a rise leads into music that's "
                         "there - give a section or a bar inside the song")
    if z_bar - bars < 1:
        if z_bar < 2:
            raise ValueError(f"{label} starts the song: there are no bars before it to build in")
        notes.append(f"only {z_bar - 1} bar(s) before {label}: the build uses those")
        bars = z_bar - 1
    a_bar = z_bar - bars
    a_t, z_t = C._bar_tick(v, a_bar), C._bar_tick(v, z_bar)
    bar_starts, beat_ticks, mts = _beats_in(v, a_bar, z_bar)
    end_bar = z_end if z_end and z_end > z_bar else z_bar + 4
    key, key_from = _key_for(v, project, key, a_t, C._bar_tick(v, end_bar))
    v["_clips"] = _clip_table(v["d"], v["ev"])
    v["_heard"] = _heard(v["d"], v["ev"], v["_clips"], v["patterns"])
    span = [n for n in v["_heard"] if a_t <= n["t"] < z_t]
    span_by_k = {(n["t"], n["ch"], n["key"]): n for n in span}
    names = {c: (v["channels"].get(c) or {}).get("name") or f"channel {c}" for c in v["channels"]}
    roles = dict(v["roles"])
    fx_chs = {c for c, r in roles.items() if r == "fx"}
    # ---- refuse a second build into the same downbeat (velocities would compound)
    ours = {f"{label} Rise Ladder".lower(), f"{label} Rise Roll".lower(), f"{label} Rise Swell".lower(),
            f"{label} Low Strings".lower()}
    for c in v["_clips"]:
        nm = str((v["patterns"].get(c["pid"]) or {}).get("name") or "").strip().lower()
        if nm in ours and a_t - P <= c["pos"] <= z_t:
            raise ValueError(f"a rise into {label} (bar {z_bar}) is already in the song ('{nm}'): flp_restore the version "
                             "before it (flp_versions) and build again, so the crescendo isn't applied twice")
    mel_ch, skyline, mel, protected = _melody(v, project, a_t, z_t)
    if isinstance(roll, int) and not isinstance(roll, bool):         # the user's channel: a drum by its name / role
        drumish = _has_word(_text_of(v, roll), cfg["words"]["drum"]) or v["roles"].get(roll) in ("tom", "perc", "kit")
        tim_ch, drum_ch = (None, roll) if drumish else (roll, None)
    else:
        tim_ch = find_channel(v, "timpani", cfg)
        drum_ch = None if tim_ch is not None else find_channel(v, "drum", cfg)
    ls_ch = low_strings if isinstance(low_strings, int) and not isinstance(low_strings, bool) else \
        find_channel(v, "low_strings", cfg)
    for c in (tim_ch, ls_ch):
        if c is not None and c not in v["channels"]:
            raise ValueError(f"channel {c} isn't in the project")
    perc_chs = {c for c in (tim_ch, drum_ch) if c is not None}
    plan = {"into": {"section": label, "bar": z_bar}, "build_bars": f"{a_bar}-{z_bar - 1}",
            "meter": ", ".join(sorted({mt["ts"] for _, mt in mts})), "key": key, "key_from": key_from}
    if other_occ:
        plan["into"]["also_at_bars"] = other_occ[:6]
    edits = {}                                            # (t, ch, written key) -> {"n": note, "parts": [...]}
    ops, new_pats, did = [], [], []
    S_lo = float(cfg["dynamics"].get("from", 50))
    S_hi = float(cfg["dynamics"].get("to", 95))
    v_lo, v_hi = S_lo, max(S_lo + 10, min(122.0, S_lo + (S_hi - S_lo) * intensity))
    xpos = lambda t: (t - a_t) / max(1, z_t - a_t)
    rnd = random.Random(1000 + z_bar)
    # ================================================================ 1. harmony
    lad = ladder_chords(key, cfg)
    segs = segments(bar_starts, beat_ticks, z_t, len(lad))
    if len(segs) < len(lad):                              # fewer slots than chords: keep the landing ones
        lad = lad[-len(segs):]
    mel_s = mel                                           # sounding pitch already
    hinfo = {"ladder": " -> ".join(f"{c['name']} ({_bb(v, a)})".replace(" beat 1)", ")") for c, (a, _) in
                                   zip(lad, segs)),
             "numerals": [c["rn"] for c in lad]}
    if harmony:
        bpc = bass_pcs(lad, segs, mel_s)
        inv = []
        for c, b in zip(lad, bpc):
            if b != c["root"]:
                inv.append(f"{c['name']} with {C._pc_name(b, C._flats_for(key))} in the bass (its root rubs against the melody)")
        climb = float(cfg.get("climb_semitones", 3)) * min(1.33, intensity)
        shifts = [int(round(i * climb)) for i in range(len(lad))]
        cuts = [a for a, _ in segs[1:]]
        acc_chs = sorted({n["ch"] for n in span} | {n["ch"] for n in v["_heard"] if n["t"] < a_t < n["t"] + n["len"]})
        remap, bass_chs = [], []
        for ch in acc_chs:
            r = roles.get(ch)
            if ch in fx_chs or ch in perc_chs or C._is_drum(r):
                continue
            if ch == mel_ch and not skyline:
                continue
            if r in BASS_ROLES or ch == ls_ch:
                bass_chs.append(ch)
            elif r in REMAP_ROLES or (ch == mel_ch and skyline):
                remap.append(ch)
        changed_h = 0
        mel_by_ch = {}
        for ch in remap + bass_chs:
            notes_ch = [n for n in v["_heard"] if n["ch"] == ch and n["t"] < z_t and n["t"] + n["len"] > a_t
                        and (n["t"], n["ch"], n["key"]) not in protected]
            reg = _register_of(v, ch, offs) or [48, 76]
            pcs_all = []
            for n in notes_ch:
                for dt, ln, inside in _pieces(n, cuts, a_t, z_t, P):
                    if not inside:
                        continue
                    pcs_all.append({"t": n["t"] + dt, "len": ln, "key": n["key"] - offs.get(ch, 0), "src": n, "dt": dt,
                                    "bass": ch in bass_chs})
            if ch not in bass_chs:                        # the low note of a piano chord is its bass
                by_t = {}
                for p in pcs_all:
                    by_t.setdefault(p["t"], []).append(p)
                for t, grp in by_t.items():
                    lowp = min(grp, key=lambda p: p["key"])
                    if lowp["key"] < 55 and (len(grp) >= 2 or reg[0] < 50):
                        lowp["bass"] = True
            remap_pieces(pcs_all, lad, segs, mel_s, shifts, bpc, reg)
            by_src = {}
            for p in pcs_all:
                by_src.setdefault(id(p["src"]), []).append(p)
            for n in notes_ch:
                parts = []
                for dt, ln, inside in _pieces(n, cuts, a_t, z_t, P):
                    if not inside:
                        parts.append([dt, ln, n["key"], n["vel"]])
                ps = sorted(by_src.get(id(n), []), key=lambda p: p["dt"])
                for p in ps:
                    if p.get("new") is not None:
                        parts.append([p["dt"], p["len"], p["new"] + offs.get(ch, 0), n["vel"]])
                parts.sort()
                same = len(parts) == 1 and parts[0] == [0, n["len"], n["key"], n["vel"]]
                if not same:
                    edits[(n["t"], ch, n["key"])] = {"n": n, "parts": parts}
                    changed_h += 1
            mel_by_ch[ch] = len(notes_ch)
        if tim_ch is not None:                            # timpani hits in the build: tuned to the ladder's bass
            tun = 0
            for n in span:
                if n["ch"] != tim_ch:
                    continue
                i = max([j for j, (a, _) in enumerate(segs) if a <= n["t"]] or [0])
                snd = n["key"] - offs.get(tim_ch, 0)
                new = _nearest_pc(bpc[i], snd, 38, 57)
                if new != snd:
                    edits[(n["t"], tim_ch, n["key"])] = {"n": n, "parts": [[0, n["len"], new + offs.get(tim_ch, 0),
                                                                             n["vel"]]]}
                    tun += 1
            if tun:
                hinfo["timpani"] = f"{tun} timpani hit(s) in the build retuned to the ladder's bass notes"
        if remap or bass_chs:
            hinfo["revoiced"] = {names[c]: mel_by_ch.get(c, 0) for c in remap + bass_chs}
            hinfo["how"] = ("the accompaniment's own rhythm, lengths and velocities moved onto the ladder (bass notes "
                            "on the chord roots)") if remap else "the bass line moved onto the ladder's roots"
            if skyline and mel_ch in remap:
                hinfo["melody_kept"] = f"{names[mel_ch]}'s top voice (the melody) kept as written"
            if inv:
                hinfo["inversions"] = inv
            did.append(f"re-voiced {changed_h} note(s) of {', '.join(names[c] for c in remap + bass_chs)} onto the ladder")
        else:
            cand = sorted((c for c, r in roles.items() if r in HOLD_ROLES and c != mel_ch and c not in fx_chs
                           and c not in perc_chs and (v["channels"].get(c) or {}).get("type") not in (4, 5)),
                          key=lambda c: (HOLD_ROLES.index(roles[c]), c))
            if cand:
                ch = cand[0]
                reg = _register_of(v, ch, offs) or list((C.LAYER_ROLES.get(roles[ch]) or {}).get("register") or [52, 76])
                vs = pick_voicings(lad, segs, mel_s, int(reg[0]), int(reg[1]))
                rows = []
                pat_t = C._bar_tick(v, a_bar)
                hold = roles[ch] in SUSTAIN_FAMILY
                for c, (a, b), voi in zip(lad, segs, vs):
                    strikes = [a] if hold else sorted({a} | {t for t in bar_starts if a < t < b})
                    for i_, s in enumerate(strikes):
                        e = strikes[i_ + 1] if i_ + 1 < len(strikes) else b
                        vel = crescendo(xpos(s), v_lo, v_hi)
                        for p in voi:
                            rows.append([ch, (s - pat_t) / P, max(0.25, (e - s - P / 16) / P), p,
                                         int(round(max(1, min(127, vel * (1 + rnd.uniform(-0.05, 0.05)))))), 0])
                C.written_rows(rows, offs)
                pname = f"{label} Rise Ladder"
                new_pats.append(pname)
                ops.append({"tool": "flp_compose_write", "pattern": pname, "notes": rows,
                            "clear_channels": [ch], "place": [[a_bar, bars]]})
                hinfo["written_on"] = names[ch]
                hinfo["voicings"] = [[_C().fl_music.note_name(p) for p in voi] for voi in vs]
                did.append(f"wrote the ladder as held chords on {names[ch]}")
            else:
                hinfo["skipped"] = ("no accompaniment plays in those bars and there is no pad / strings / chords channel "
                                    "to hold the ladder: add one (flp_compose what='layer' role='strings'), then build "
                                    "again")
        others = sorted({names[n["ch"]] for n in span if roles.get(n["ch"]) in LINE_ROLES and n["ch"] != mel_ch})
        if others:
            hinfo["lines_kept"] = (f"{', '.join(others)} kept their notes (lines aren't re-voiced): check them against "
                                   "the ladder by ear")
    else:
        hinfo = {"skipped": "harmony=False"}
    plan["harmony"] = hinfo
    # ================================================================ 3. dynamics (on what plays, after re-voicing)
    dinfo = {}
    if dynamics:
        rows_d = []                                       # (piece tick, vel, edit key, part index or None)
        for n in span:
            ch = n["ch"]
            if ch in fx_chs:
                continue
            k = (n["t"], ch, n["key"])
            if ch == mel_ch and not melody_dynamics and (not skyline or k in protected):
                continue
            if k in edits:
                for j, p in enumerate(edits[k]["parts"]):
                    if a_t <= n["t"] + p[0] < z_t:
                        rows_d.append((n["t"] + p[0], p[3], k, j))
            else:
                rows_d.append((n["t"], n["vel"], k, None))
        for n in v["_heard"]:                             # held notes cut into the build: their re-voiced pieces
            k = (n["t"], n["ch"], n["key"])
            if n["t"] < a_t and k in edits:
                for j, p in enumerate(edits[k]["parts"]):
                    if a_t <= n["t"] + p[0] < z_t:
                        rows_d.append((n["t"] + p[0], p[3], k, j))
        bar_of = lambda t: max(i for i, b in enumerate(bar_starts) if b <= t)
        by_bar, by_bar_all = {}, {}                       # a bar's average: the pitched parts (one loud drum hit
        for t, vel, k, _ in rows_d:                       # would pull the whole bar down), drums scale with it
            by_bar_all.setdefault(bar_of(t), []).append(vel)
            if k[1] not in perc_chs:
                by_bar.setdefault(bar_of(t), []).append(vel)
        mean = {b: statistics.mean(by_bar.get(b) or x) for b, x in by_bar_all.items()}
        top = 0
        for t, vel, k, j in rows_d:
            nv = int(round(max(1, min(127, vel * crescendo(xpos(t), v_lo, v_hi) / max(1.0, mean[bar_of(t)])))))
            top += nv >= 127
            if j is None:
                n = span_by_k[k]
                edits[k] = {"n": n, "parts": [[0, n["len"], n["key"], nv]]}
            else:
                edits[k]["parts"][j][3] = nv
        chs_d = sorted({k[1] for _, _, k, _ in rows_d})
        if rows_d:
            dinfo = {"from": int(v_lo), "to": int(v_hi), "notes": len(rows_d), "parts": [names[c] for c in chs_d],
                     "how": "each note scaled by the target over its bar's average: accents and variation kept"}
            if top:
                dinfo["ceiling"] = f"{top} note(s) reached velocity 127 (the top of the range)"
            did.append(f"a crescendo v{int(v_lo)} -> v{int(v_hi)} on {len(chs_d)} part(s)")
        else:
            dinfo = {"skipped": "no notes to swell in the build bars"}
        if mel_ch is not None and not melody_dynamics:
            mv = [n["vel"] for n in span if n["ch"] == mel_ch and (not skyline or (n["t"], n["ch"], n["key"]) in protected)]
            if mv:
                dinfo["melody"] = (f"{names[mel_ch]}'s melody kept its velocities (avg v{int(statistics.mean(mv))}); "
                                   "melody_dynamics=True lets it swell with the rest")
    else:
        dinfo = {"skipped": "dynamics=False"}
    plan["dynamics"] = dinfo
    # ================================================================ 2. tempo
    tinfo, tplan = {}, None
    if tempo:
        tplan = _tempo_plan(v, a_t, z_t, beat_ticks, intensity, cfg["tempo"], tempo)
        if tplan.get("skipped"):
            tinfo = {"skipped": tplan["skipped"]}
            tplan = None
        else:
            tinfo = {"from": tplan["lo"], "to": tplan["hi"], "back_to": tplan["back"], "steps": len(tplan["ramp"]),
                     "lane": tplan["lane"]}
            if tplan["replaced_events"]:
                tinfo["replaced"] = f"{tplan['replaced_events']} tempo event(s) in the build bars"
            if (v["journal"].get("performance") or {}).get("sections"):
                tinfo["note"] = ("this song has an flp_perform spec: its next tempo write replaces the map - set "
                                 "the build's tempo there too if you perform again")
            ops.append({"tool": "flp_tempo_write", "points": tplan["points"], "pattern": "Tempo"})
            did.append(f"an accelerando {tplan['lo']:g} -> {tplan['hi']:g} BPM, back to {tplan['back']:g} on bar {z_bar}")
    else:
        tinfo = {"skipped": "tempo=False"}
    plan["tempo"] = tinfo
    tmap = tplan["map"] if tplan else sorted((int(t), float(b)) for t, b in (v.get("tempos") or [(0, v["tempo"])]))
    bpm_end = next((b for t, b in reversed(tmap) if t < z_t), v["tempo"])
    # ================================================================ 4. melody (report only)
    minfo = {}
    if mel_ch is not None:
        mb = [n for n in mel if a_t <= n["t"] < z_t]
        if mb:
            tn = max(mb, key=lambda n: (n["key"], n["t"]))
            last_t = bar_starts[-1]
            in_last = tn["t"] >= last_t
            fl = C.fl_music.note_name
            minfo = {"channel": names[mel_ch], "top": f"{fl(tn['key'])} at {_bb(v, tn['t'])}", "peak_in_last_bar": in_last}
            if not in_last:
                pcs = set(lad[-1]["pcs"])
                tgt = next((k for k in range(tn["key"] + 1, tn["key"] + 13) if k % 12 in pcs), tn["key"] + 2)
                lb = [n for n in mb if n["t"] >= last_t]
                where = (f"raise the last bar's top note ({fl(max(lb, key=lambda n: n['key'])['key'])} at "
                         f"{_bb(v, max(lb, key=lambda n: n['key'])['t'])})") if lb else f"add a note in bar {z_bar - 1}"
                minfo["suggest"] = (f"the melody peaks before the last bar; for the arch that lands the release, {where} "
                                    f"to {fl(tgt)} (a tone of {lad[-1]['name']}, above the current peak) - by hand: "
                                    "the melody is yours")
        else:
            minfo = {"channel": names[mel_ch], "note": "the melody rests in the build bars"}
    else:
        minfo = {"note": "no melody found in the song"}
    plan["melody"] = minfo
    # ================================================================ 5. roll / swell
    rinfo, swell = {}, None
    last_mt = mts[-1][1]
    beat_q = (last_mt["pulse"] / 4.0) if not last_mt["legacy"] else 1.0
    nbeats = float(cfg["roll"].get("beats", 2) if intensity >= 0.75 else cfg["roll"].get("short_beats", 1))
    roll_q = min(nbeats * beat_q, (z_t - a_t) / P)
    peak = int(min(127, v_hi + float(cfg["roll"].get("strike_over", 12))))
    try:
        into_ch = None
        with C.in_meter(C._meter_at(v, z_bar)[0]):
            cx = C.song_context(v, float(z_bar), 1, key)
        if cx.get("chords"):
            into_ch = cx["chords"][0]
    except Exception:
        into_ch = None
    root_pc = (into_ch.get("bass") if into_ch and into_ch.get("bass") is not None else
               (into_ch["root"] if into_ch else C.key_info(key)[0]))
    if roll:
        rch = tim_ch if tim_ch is not None else drum_ch
        sb = int(math.floor(C._tick_bar(v, z_t - roll_q * P) + 1e-6))
        st_t = C._bar_tick(v, sb)
        if rch is not None:
            if rch == tim_ch:
                pitch = _nearest_pc(root_pc, 46, 38, 57)
                kind = "timpani roll"
            else:
                ks = [n["key"] - offs.get(rch, 0) for n in v["_heard"] if n["ch"] == rch]
                pitch = statistics.mode(ks) if ks else 60
                kind = "soft low-drum roll"
                peak = int(min(127, v_hi))
            rn = roll_notes(z_t, P, roll_q, bpm_end, cfg["roll"], peak, pitch)
            r0 = rn[0][0]
            gone = 0
            for n in v["_heard"]:                         # its own notes under the roll make way for it
                if n["ch"] == rch and r0 - P // 16 <= n["t"] < z_t - P // 16:
                    edits[(n["t"], rch, n["key"])] = {"n": n, "parts": []}
                    gone += 1
            hit = [n for n in v["_heard"] if n["ch"] == rch and abs(n["t"] - z_t) <= P // 16]
            if hit:                                       # the climax already hits there: that hit is the peak
                rn = rn[:-1]
                for n in hit:
                    edits[(n["t"], rch, n["key"])] = {"n": n, "parts": [[0, n["len"], n["key"], max(n["vel"], peak)]]}
            rows = [[rch, (t - st_t) / P, ln / P, k, vel, 0] for t, ln, k, vel in rn]
            C.written_rows(rows, offs)
            pname = f"{label} Rise Roll"
            new_pats.append(pname)
            ops.append({"tool": "flp_compose_write", "pattern": pname, "notes": rows, "clear_channels": [rch],
                        "place": [[sb, z_bar - sb + 1]]})
            rinfo = {"kind": kind, "channel": names[rch], "pitch": C.fl_music.note_name(pitch),
                     "beats": round(roll_q / beat_q, 2), "strokes": len(rn) - (0 if hit else 1),
                     "peak": f"v{peak} on the downbeat of bar {z_bar}" + (" (its own hit there)" if hit else "")}
            if gone:
                rinfo["replaced"] = f"{gone} of its own note(s) under the roll"
            did.append(f"a {kind} on {names[rch]} peaking on bar {z_bar}'s downbeat")
        else:
            fxm = C._fx_mod()
            if fxm is None:
                hint = _catalog_hint("timpani", "perc")
                rinfo = {"skipped": "no timpani / drum channel and the FX generator (flh_fx) isn't installed",
                         "do": "add a channel named Timpani, then build again" + (
                             f" (owned / free per fl_sound_catalog: {hint['said']})" if hint else "")}
            else:
                secs = _seconds(tmap, z_t - int(round(roll_q * P)), z_t, P)
                bpm_eff = round(roll_q * 60.0 / max(1e-6, secs), 3)
                fx_dir = os.path.join(os.path.dirname(project), "_fx")
                chn = f"Reverse Swell {label}"
                exist = [c for c, x in v["channels"].items() if str(x.get("name") or "").strip().lower() == chn.lower()
                         and x.get("type") == 0]
                sch = exist[0] if exist else max(v["channels"], default=-1) + 1
                path = None
                if not dry_run:
                    H["writable"](os.path.join(fx_dir, "x.wav"))
                    os.makedirs(fx_dir, exist_ok=True)
                    crash = None
                    try:
                        crash = C._fx_crash_sample(v, {}, project)
                    except Exception:
                        crash = None
                    r_ = fxm.make_fx("reverse_crash", bpm_eff, roll_q, fx_dir, key=key, seed=1,
                                     intensity=intensity, sample=crash)
                    path = r_["path"]
                    roll_q = float(r_.get("beats") or roll_q)
                spec = {"name": chn, "sample": path, "kind": "reverse_crash"}
                spec["channel" if exist else "expect"] = sch
                if not dry_run:
                    ops.append({"tool": "flp_compose_fx", "channels": [spec], "insert": "FX", "level_db": -14})
                pname = f"{label} Rise Swell"
                new_pats.append(pname)
                ops.append({"tool": "flp_compose_write", "pattern": pname,
                            "notes": [[sch, (z_t - roll_q * P - st_t) / P, roll_q, 60, 100, 0]],
                            "clear_channels": [sch], "place": [[sb, z_bar - sb]]})
                swell = {"channel": sch, "name": chn, "new": not exist}
                rinfo = {"kind": "reversed-cymbal swell (flh_fx, synthesized" + (" from the kit's crash)" if not dry_run and crash else ")"),
                         "channel": chn + (" (new sampler)" if not exist else ""), "beats": round(roll_q / beat_q, 2),
                         "peak": f"ends on the downbeat of bar {z_bar}", "made_at_bpm": bpm_eff}
                if path:
                    rinfo["file"] = os.path.basename(path)
                hint = _catalog_hint("timpani", "perc")
                rinfo["why"] = "no timpani or orchestral-drum channel in the song" + (
                    f"; owned / free per fl_sound_catalog: {hint['said']} would play a real roll - add it as a "
                    "channel named Timpani and build again" if hint else
                    "; a timpani (or orchestral drum) channel named Timpani would play a real roll: add one and build "
                    "again")
                did.append(f"a reversed-cymbal swell ending on bar {z_bar}'s downbeat")
    else:
        rinfo = {"skipped": "roll=False"}
    plan["roll"] = rinfo
    # ================================================================ 5b. low strings on the downbeat
    linfo, new_ls = {}, None
    if low_strings:
        if ls_ch is None:
            new_ls = _new_low_strings(v, cfg, roles, [swell["channel"]] if swell and swell["new"] else [])
            if new_ls.get("skipped"):
                hint = _catalog_hint("cello low strings", "strings")
                linfo = {"skipped": new_ls["skipped"],
                         "do": "add an instrument channel named Celli (or flp_compose(project, what='layer', "
                               f"role='low strings', section='{label}')), then build again" +
                               (f"; owned / free per fl_sound_catalog: {hint['said']}" if hint else "")}
                if new_ls.get("tried"):
                    linfo["tried"] = new_ls["tried"]
                new_ls = None
            else:
                ls_ch = new_ls["channel"]
                names[ls_ch] = new_ls["name"]
        if ls_ch is not None:
            already = [n for n in v["_heard"] if n["ch"] == ls_ch and z_t - P // 8 <= n["t"] < C._bar_tick(v, z_bar + 1)]
            if already:
                linfo = {"channel": names[ls_ch], "kept": f"already plays from bar {z_bar}: left as it is"}
            else:
                L = int(cfg["low_strings"].get("bars", 2))
                if z_end:
                    L = max(1, min(L, int(round(z_end - z_bar))))
                regl = cfg["low_strings"].get("register") or [36, 47]
                lo_p = _nearest_pc(root_pc, (int(regl[0]) + int(regl[1])) // 2, int(regl[0]), int(regl[1]))
                ln_b = (C._bar_tick(v, z_bar + L) - z_t) / P
                vel = int(round(max(1, min(127, v_hi * float(cfg["low_strings"].get("vel", 0.9))))))
                rows = [[ls_ch, 0.0, ln_b, lo_p, vel, 0], [ls_ch, 0.0, ln_b, lo_p + 12, max(1, vel - 8), 0]]
                C.written_rows(rows, offs)
                pname = f"{label} Low Strings"
                new_pats.append(pname)
                if new_ls:
                    ops += new_ls["ops"]
                ops.append({"tool": "flp_compose_write", "pattern": pname, "notes": rows, "clear_channels": [ls_ch],
                            "place": [[z_bar, L]]})
                fl = C.fl_music.note_name
                linfo = {"channel": names[ls_ch], "notes": f"{fl(lo_p)} + {fl(lo_p + 12)} held {L} bar(s) from bar {z_bar}",
                         "vel": vel}
                if new_ls:
                    linfo["new_channel"] = {"channel": ls_ch, "sound": new_ls["sound"]}
                    if new_ls.get("tried"):
                        linfo["new_channel"]["skipped_sounds"] = new_ls["tried"][:3]
                    linfo["why"] = "the song had no celli / low-strings channel"
                did.append(f"{names[ls_ch]} enter on bar {z_bar}" + (" (a new channel)" if new_ls else ""))
    else:
        linfo = {"skipped": "low_strings=False"}
    plan["low_strings"] = linfo
    # ================================================================ the batch
    ed = [{"t": k[0], "ch": k[1], "k": k[2], "parts": e["parts"]} for k, e in sorted(edits.items())]
    if ed:
        ops.insert(0, {"tool": "flp_rise_write", "edits": ed})
    plan["notes"] = notes
    a_l, z_l = max(1, a_bar - 1), z_bar + 2
    plan["listen"] = f"fl_listen(start_bar={a_l}, end_bar={z_l})"
    if not ops:
        plan["did"] = "nothing to write: " + "; ".join(x.get("skipped", "") for x in (hinfo, dinfo, tinfo, rinfo, linfo)
                                                         if isinstance(x, dict) and x.get("skipped"))
        plan["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
        return plan
    where = f"bar {z_bar}" if label == f"Bar {z_bar}" else f"{label} (bar {z_bar})"
    summary = (f"rise into {where} over bars {a_bar}-{z_bar - 1} in {key}: " + "; ".join(did))
    if dry_run:
        plan.update(dry_run=True, did="would build a " + summary,
                    would_write=[{"tool": o["tool"], **({"pattern": o["pattern"]} if o.get("pattern") else {}),
                                  **({"notes": len(o["edits"])} if o.get("edits") else {})} for o in ops])
        plan["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
        return plan
    er = C._edit(project, ops, steps)
    C._carry(plan, er)
    for r in (er.get("results") or []) if isinstance(er, dict) else []:
        if r.get("tool") == "flp_rise_write":
            if r.get("made_unique"):
                plan["harmony"]["own_copies"] = [x["pattern"] for x in r["made_unique"]][:8]
            if r.get("not_changed"):
                plan["not_changed"] = r["not_changed"]
    # ---- journal: what changed, and the swell sampler's role
    jset = {"rises": None}
    jr = v["journal"]
    rises = dict(jr.get("rises") or {}) if isinstance(jr.get("rises"), dict) else {}
    rises[str(z_bar)] = {"into": label, "bars": [a_bar, z_bar - 1], "key": key, "ladder": [c["name"] for c in lad],
                         "tempo": [tinfo.get("from"), tinfo.get("to"), tinfo.get("back_to")] if "from" in tinfo else None,
                         "crescendo": [int(v_lo), int(v_hi)] if "from" in dinfo else None,
                         "roll": rinfo.get("kind"), "when": time.strftime("%Y-%m-%d %H:%M")}
    jset["rises"] = rises
    if swell and swell["new"]:
        jset["compose"] = {"fx_channels": {f"rise_swell_{z_bar}": swell["channel"]}}
        jset["channels"] = {str(swell["channel"]): {"role": "fx", "name": swell["name"], "fx_kind": "reverse_crash",
                                                    "instrument": "reverse cymbal"}}   # score: unpitched, not a C4
    if new_ls:                                            # read as a bass by score / mixer, found again by role
        jset.setdefault("compose", {})["channels"] = {"low strings": new_ls["channel"]}
        jset.setdefault("channels", {})[str(new_ls["channel"])] = new_ls["journal"]
    try:
        C._store().journal_update(project, set=jset, add={"notes": f"{time.strftime('%Y-%m-%d')} {summary}"})
    except Exception as e:
        plan["journal_warning"] = f"the song journal wasn't updated ({type(e).__name__}: {str(e)[:80]})"
    # ---- measured after the write
    try:
        v2 = C.project_view(project)
        h2 = _heard(v2["d"], v2["ev"])
        aff = {k[1] for k in edits} - perc_chs - ({mel_ch} if not (skyline or melody_dynamics) else set())
        fb =[n["vel"] for n in h2 if n["ch"] in aff and beat_ticks[0] <= n["t"] < (beat_ticks[1] if len(beat_ticks) > 1 else z_t)]
        lb = [n["vel"] for n in h2 if n["ch"] in aff and beat_ticks[-1] <= n["t"] < z_t]
        if fb and lb and "from" in dinfo:
            plan["dynamics"]["measured"] = f"first beat avg v{round(statistics.mean(fb))}, last beat avg v{round(statistics.mean(lb))}"
        if H.get("clashes") and harmony:
            cr = H["clashes"](C._src(project), bars=f"{a_bar}-{z_bar - 1}", limit=3, journal_from=project)
            plan["clashes"] = {"bars": f"{a_bar}-{z_bar - 1}", "counts": cr.get("counts"),
                               "top": [f"{i.get('parts')}: {i.get('notes')} ({i.get('where')})"
                                       for i in (cr.get("issues") or [])[:2]]}
    except Exception as e:
        plan["measure_note"] = f"couldn't measure the result ({type(e).__name__})"
    plan["did"] = "built a " + summary
    plan["undo"] = "flp_versions(project) then flp_restore(project, <the backup before this>)"
    plan["next"] = (f"{plan['listen']} - a human should listen: the ladder's voicing, the tempo push and the roll's "
                    "level are first guesses")
    plan["steps_s"] = dict(steps, total=round(time.time() - T0, 3))
    return plan


def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    STR, INT, NUM, BOOL, FLP = ns["_STR"], ns["_INT"], ns["_NUM"], ns["_BOOL"], ns["_FLP"]
    guard = ns["_guard_open_project"]
    C = _C()
    entry = C._reads_fresh(flp_build_rise) if hasattr(C, "_reads_fresh") else flp_build_rise
    ns["TOOLS"]["flp_build_rise"] = (
        entry,
        "Build an orchestral / piano RISE into a climax (the film-score build, not an EDM riser) over the bars before "
        "into= (a section name or a bar), in ONE reversible batch: (1) harmony - a tension ladder of diminished 7ths a "
        "minor third / step apart landing on a Vsus4 (minor: #vii°7 iv°7 ii°7 Vsus4; data/rise/rise.json), the "
        "song's accompaniment re-voiced onto it (its rhythm kept; tones that rub against the melody left out; held "
        "chords on a pad / strings channel when nothing accompanies); (2) tempo - an accelerando ~8% under -> ~8% "
        "over the tempo, back to the new section's tempo on its downbeat (tempo events in the Tempo lane; not with "
        "audio clips unless tempo='force'); (3) dynamics - a crescendo (v50 -> v95) on the parts there, scaled so "
        "accents and human variation stay; (4) the melody is never changed - its top note is reported with a "
        "suggestion; (5) a timpani roll (else a low-drum roll, else a reversed-cymbal swell from flh_fx) peaking on "
        "the downbeat, and low strings entering sustained on it (a new channel with an owned strings sound when the "
        "song has none). Any meter. dry_run=true: the plan only. Undo: "
        "flp_versions / flp_restore. Listen after (fl_listen on the bars it says).",
        S(project=FLP,
          into=P(["string", "number"], "the section the rise leads into (as the journal / markers name it) or its bar"),
          bars=INT("bars of build before it (default 4)", True),
          intensity=NUM("0.25-2 (default 1): tempo push, crescendo top, the ladder's climb, roll length", True),
          harmony=BOOL("write the tension ladder (default true)", True),
          tempo=P(["boolean", "string"], "the accelerando (default true; 'force' = even with audio clips)", True),
          dynamics=BOOL("the crescendo on the parts already there (default true)", True),
          roll=P(["boolean", "integer"], "the roll / swell into the downbeat (default true; a channel number = "
                                         "roll on it)", True),
          low_strings=P(["boolean", "integer"], "low strings enter on the downbeat (default true; a channel number = "
                                                "that channel)", True),
          melody_dynamics=BOOL("let the melody's velocities swell too (default false: the melody stays as written)",
                               True),
          key=STR("the key (default: the song journal's, else detected from the notes)", True),
          dry_run=BOOL("plan and describe, write nothing", True)))
    ns["TOOLS"]["flp_rise_write"] = (
        guard(flp_rise_write, "project", "output"),
        "Low-level writer flp_build_rise batches through flp_edit: edits [{t (song tick), ch, k (written pitch), parts "
        "[[dt, len, key, vel], ...]}] - the note the playlist plays at t becomes these parts ([] = it goes); a clip "
        "whose pattern also plays elsewhere gets its own copy. Verified before saving.",
        S(project=FLP, output=STR("save as (inside FLH_ROOTS)"),
          edits=P("array", "[{t, ch, k, parts: [[dt, len, key, vel], ...]}]", items={"type": "object"})))
    ns["_OPEN_PROJECT_GUARD"]["flp_rise_write"] = ("project", "output")
    if isinstance(ns.get("LAZY_TOOLS"), set):
        ns["LAZY_TOOLS"].add("flp_rise_write")            # finds its notes by content at write time
    if isinstance(ns.get("NOT_READERS"), set):
        ns["NOT_READERS"].add("flp_build_rise")
    ns["flp_build_rise"] = flp_build_rise
    ns["ladder_chords"] = ladder_chords
    for k_, g_ in (("flp_build_rise", "compose"), ("flp_rise_write", "compose")):
        try:
            ns["TOOL_GROUPS"][k_] = g_
        except Exception:
            pass
    try:
        ns["SHORT_DESC"]["flp_build_rise"] = (
            "Orchestral / piano rise into a climax over the bars before into=: a dim7 ladder to Vsus4 on the "
            "accompaniment, an accelerando, a crescendo, a timpani roll (or swell) and low strings on the downbeat; "
            "the melody untouched. One batch, dry_run=true plans.")
    except Exception:
        pass
    try:
        ns["LONG_HELP"]["flp_build_rise"] = (
            "Typical: flp_build_rise(project, into='Climax') -> 4 bars before it; dry_run=true first to read the plan. "
            "intensity=1.5 = a bigger push; harmony=false keeps the chords; tempo=false keeps the tempo; roll=7 rolls "
            "on channel 7; low_strings=false skips the celli. Rerunning on the same downbeat is refused (the "
            "crescendo would compound): flp_restore first. The ladder and other settings are data: "
            "<state folder>/rise/rise.json overrides data/rise/rise.json key by key.")
    except Exception:
        pass
