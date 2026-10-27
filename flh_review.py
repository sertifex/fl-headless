"""fl-headless review: fl_doctor (static .flp check, no FL) and fl_review (loudness / tone / stereo per section and
the song's loudness arc, from a render or live from FL). Every issue is {severity, kind, where, what, fix}, ranked
most severe first; fixes name the tool to use.

Follows the extension contract in flh_store.py: register(ns) keeps the server namespace in H and adds the tools;
functions are looked up in H at call time (flh_reload swaps them).
"""
import fnmatch
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time

import flh_common as C
import flh_store as S

H = {}

SEV = {"high": 0, "med": 1, "low": 2}
KIND_ORDER = ["silent_instrument", "content_missing", "silent_knob", "muted_track", "master_muted", "trial_plugin", "arc_peak_not_loudest",
              "arc_quiet_section_loud", "correlation", "clipping", "true_peak", "loudness_target", "missing_sample",
              "vst_missing", "automation_target", "out_of_range", "abrupt_entrance", "clash", "arc_no_build", "arc_flat", "arc_wide", "arc_early_peak",
              "silent_section", "master_widener", "muted_with_notes", "route_missing", "balance", "mud", "harsh",
              "dull", "not_owned", "dead_tail", "unused_channel", "muted_clip_only", "automation_unplaced",
              "empty_pattern", "empty_clip", "unplaced_pattern", "unknown_plugin", "info"]

# fallback when neither the profile nor flh_catalog.sound_ok (edition-aware) can say: natives that are trial-only in
# the cheaper FL editions. Only used without sound_ok, and then only as a hint for unknown editions.
TRIAL_DEFAULT = ["Sakura", "Ogun", "Harmor", "Kepler", "Kepler Exo", "Toxic Biohazard", "Morphine", "Poizone", "Sawer",
                 "Harmless", "Transistor Bass"]
FREE_VST_DEFAULT = ["Splice INSTRUMENT", "LABS", "Spitfire Audio - LABS", "BBC Symphony Orchestra*"]
WIDENERS = ["stereo enhancer", "stereo shaper", "imager", "widen", "wider", "stereo expander", "haas", "microshift",
            "stereo spread", "dimension expander", "spreader"]
# section names -> role in the loudness arc (explicit journal flags peak / quiet / build: true win over names).
# build first ("Pre-Chorus", "Build-up" are not peaks), then a name with both quiet and peak words is left neutral.
BUILD_RE = re.compile(r"\b(pre[\s_-]*(chorus|drop|hook|refrain|coro|estribillo|refr[aã]o)|pre\b|build[\s_-]*up|build|"
                      r"buildup|riser|rise|lift|ramp|aufbau|subida|mont[eé]e|Bメロ)", re.I)
PEAK_RE = re.compile(r"\b(peak|climax|finale|apex|summit|chorus|refrain|hook|drop(?!\s*out)|estribillo|coro|"
                     r"refr[aã]o|ritornello|h[oö]hepunkt|cl[ií]max|cumbre|sommet|culmine|припев|кульминация|サビ)",
                     re.I)
QUIET_RE = re.compile(r"\b(intro|heart\s*beat|ending|end|outro|coda|quiet|calm|soft|prelude|interlude|lull|breath|"
                      r"hush|still|lullaby|breakdown|break|introducci[oó]n|introdu[cç][aã]o|einleitung|vorspiel|"
                      r"zwischenspiel|ausklang|schluss|interludio|fin|cierre|вступление|проигрыш|концовка|кода|"
                      r"аутро|イントロ|アウトロ|間奏)", re.I)
# genre -> how far apart sections sit. dense: pop / EDM / rock / hip hop (sections ~3-8 LU apart, chorus or drop
# loudest); dynamic: orchestral / piano / film (8-14 LU). Unknown genre: lenient middle ground, low-severity hints.
DENSE_WORDS = ("edm", "electronic", "electro", "dance", "house", "techno", "trance", "dubstep", "drum and bass",
               "drum & bass", "dnb", "d&b", "trap", "hip hop", "hip-hop", "hiphop", "rap", "pop", "rock", "metal",
               "punk", "r&b", "rnb", "reggaeton", "future bass", "hardstyle", "garage", "grime", "drill", "phonk",
               "synthwave", "disco", "funk", "afrobeat", "dancehall", "club", "big room", "bass music", "hyperpop",
               "grunge", "emo", "lofi", "lo-fi", "ambient", "drone", "chillout")
DYNAMIC_WORDS = ("orchestral", "orchestra", "classical", "piano", "film", "cinematic", "score", "soundtrack", "jazz",
                 "acoustic", "folk", "chamber", "choral", "neoclassical", "post-rock", "string quartet",
                 "symphon\\w*", "ballad", "lullaby")
ARC_STYLE = {"dense": {"flat": 2.0, "wide": 12.0, "aim": "3-8 LU", "flat_sev": "low", "build_lu": 1.0,
                       "build_sev": "low", "early_peak": False},
             "dynamic": {"flat": 6.0, "wide": 18.0, "aim": "8-14 LU", "flat_sev": "med", "build_lu": 3.0,
                         "build_sev": "med", "early_peak": True},
             None: {"flat": 4.0, "wide": 18.0, "aim": "4-14 LU (set the song's genre/style for a tighter check)",
                    "flat_sev": "low", "build_lu": 2.0, "build_sev": "low", "early_peak": False}}
BANDS_R = [("low", 20, 200), ("lowmid", 200, 500), ("mid", 500, 2000), ("presence", 2000, 5000), ("air", 5000, 16000)]
EV_CHAN_ENABLED, EV_CHAN_TYPE, EV_CHAN_INSERT = 0, 21, 104      # 104 (i16): mixer insert, mapped on FL 2026.1.7
MIXER_ENABLED = 8                                                # flags bit in event 236 payload[4:8]


def _profile():
    try:
        if not S.H:
            S.H = H
        return S.profile_get() or {}
    except Exception:
        return {}


def _call(name, args=None):
    """A fix hint naming a tool: direct when the client lists it, else flh_call("name", {...})."""
    return C.tool_call(H, name, args)


def _iss(sev, kind, where, what, fix):
    return {"severity": sev, "kind": kind, "where": where, "what": what, "fix": fix}


def rank(issues):
    """Most severe first, then by kind (arc and silence before tone), stable."""
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    return sorted(issues, key=lambda i: (SEV.get(i["severity"], 3), order.get(i.get("kind"), 99)))


def _match(name, patterns, prefix=True):
    n = (name or "").strip().lower()
    for p in patterns or []:
        q = str(p).strip().lower()
        if not q:
            continue
        if fnmatch.fnmatchcase(n, q) or n == q:
            return True
        if prefix and "*" not in q and (n.startswith(q + " ") or q.startswith(n + " ")
                                        or f" {q} " in f" {re.sub(r'[^a-z0-9]+', ' ', n)} "):
            return True                                  # whole words: 'INSTRUMENT' matches 'Splice INSTRUMENT'
    return False


# ============================================================================= arc (pure, unit-testable)
def section_kind(s):
    """'peak' | 'quiet' | 'build' | None for a section: explicit flags (peak / quiet / build: true, or level / mark)
    first, then the name (common song-form words, some non-English). A name with both quiet and peak words
    ('Outro Chorus') stays neutral."""
    mark = str(s.get("level", s.get("mark", "")) or "").lower()
    if s.get("peak") is True or mark in ("peak", "climax", "chorus", "drop", "high", "loud"):
        return "peak"
    if s.get("quiet") is True or mark in ("quiet", "soft", "low", "calm"):
        return "quiet"
    if s.get("build") is True or mark in ("build", "rise", "riser"):
        return "build"
    name = s.get("name") or ""
    if BUILD_RE.search(name):
        return "build"
    q, p = bool(QUIET_RE.search(name)) and s.get("quiet") is not False, \
        bool(PEAK_RE.search(name)) and s.get("peak") is not False
    if q and p:
        return None
    return "peak" if p else ("quiet" if q else None)


def style_class(text):
    """'dense' (pop / EDM / rock / hip hop...), 'dynamic' (orchestral / piano / film...) or None from free text."""
    t = (text or "").lower()
    if not t.strip():
        return None
    hit = lambda words: sum(1 for w in words if re.search(r"(?<![a-z])" + w + r"(?![a-z])", t))
    dn, dy = hit(DENSE_WORDS), hit(DYNAMIC_WORDS)
    return "dense" if dn > dy else ("dynamic" if dy > dn else None)


def arc_style(jr=None, prof=None):
    """Arc thresholds for a song: journal arc {flat_lu, wide_lu, build} > journal genre/style > the profile's song
    entry > profile genre/style. -> dict (ARC_STYLE entry + 'class')."""
    jr, prof = jr or {}, prof or {}
    song = ((prof.get("songs") or {}).get(jr.get("song") or "") or {}) if isinstance(prof.get("songs"), dict) else {}
    cls = None
    for src in (jr, song, prof):
        txt = " ".join(str(src.get(k) or "") for k in ("genre", "style"))
        cls = style_class(txt)
        if cls or txt.strip():
            break
    st = dict(ARC_STYLE[cls]); st["class"] = cls
    arc = jr.get("arc") if isinstance(jr.get("arc"), dict) else {}
    for k_in, k in (("flat_lu", "flat"), ("wide_lu", "wide")):
        if isinstance(arc.get(k_in), (int, float)):
            st[k] = float(arc[k_in])
    if arc.get("build") is True:                                  # this song must build, whatever the genre
        st.update(build_lu=ARC_STYLE["dynamic"]["build_lu"], build_sev="med", must_build=True)
    elif arc.get("build") is False:
        st["no_build_check"] = True
    return st


def arc_check(sections_measured, role=None, style=None):
    """Loudness-arc problems from per-section loudness. sections_measured: [{name, lufs, peak?, quiet?, build?, bar?}]
    in song order; role: opener / track / closer; style: arc_style() dict, 'dense' / 'dynamic', or None (genre
    unknown: lenient). Peak sections (journal peak: true, else chorus / drop / climax... by name) must be the loudest,
    quiet ones quiet; range and build rules follow the genre. -> [issue] (ranked). No I/O."""
    if not isinstance(style, dict):
        st = dict(ARC_STYLE[style if style in ARC_STYLE else None]); st["class"] = style if style in ARC_STYLE else None
    else:
        st = style
    secs = [s for s in sections_measured if s.get("lufs") is not None and s["lufs"] > -60]
    out = []
    if len(secs) < 2:
        return out
    kind = {id(s): section_kind(s) for s in secs}
    fix_down = ("lower the support layers there (flp_mixer / fl_mixer_set volume_db on their tracks, or "
                "flp_edit_notes velocity scale), then fl_review again")
    explicit = [s for s in secs if s.get("peak") is True or
                str(s.get("level", s.get("mark", "")) or "").lower() in ("peak", "climax", "chorus", "drop")]
    peaks = explicit or [s for s in secs if kind[id(s)] == "peak"]
    loudest = max(secs, key=lambda s: s["lufs"])
    top = max(peaks, key=lambda s: s["lufs"]) if peaks else None
    reported = set()
    if top is not None:
        many = len(peaks) > 1
        louder = []
        for s in secs:
            if s in peaks:
                continue
            k = kind[id(s)]; d = s["lufs"] - top["lufs"]
            tol = 0.5 if k == "quiet" else (1.5 if k == "build" else 1.0)
            if d > tol:
                louder.append((d, s, k))
        for d, s, k in sorted(louder, key=lambda x: -x[0])[:3]:
            reported.add(id(s))
            sev = "high" if k == "quiet" or d > (3.0 if k == "build" else 2.0) else "med"
            q = " (a quiet section!)" if k == "quiet" else ""
            out.append(_iss(sev, "arc_peak_not_loudest", _sec_where(s),
                            f"{s['name']}{q} is {d:.1f} LU louder than the {'loudest ' if many else ''}peak section "
                            f"{top['name']} ({s['lufs']:.1f} vs {top['lufs']:.1f} LUFS)",
                            f"bring {s['name']} at least {d + 2:.0f} dB down: {fix_down} - or, if {s['name']} is "
                            f"meant to be the high point, mark it peak: true in the journal sections"))
    for i, s in enumerate(secs):
        if id(s) in reported or kind[id(s)] != "quiet":
            continue
        nb = [secs[j] for j in (i - 1, i + 1) if 0 <= j < len(secs)]
        if nb and all(s["lufs"] > n["lufs"] + 1.0 for n in nb):
            d = s["lufs"] - max(n["lufs"] for n in nb)
            out.append(_iss("high" if d > 3 else "med", "arc_quiet_section_loud", _sec_where(s),
                            f"quiet-labelled {s['name']} is {d:.1f} LU louder than its neighbours "
                            f"({', '.join(n['name'] for n in nb)})", f"bring it down {d + 2:.0f} dB: {fix_down}"))
    genre = {"dense": " for a dense genre (pop / EDM / rock)", "dynamic": " for a dynamic genre"}.get(st.get("class"), "")
    if len(secs) >= 3:
        lo = min(secs, key=lambda s: s["lufs"]); spread = loudest["lufs"] - lo["lufs"]
        if spread < st["flat"]:
            out.append(_iss(st["flat_sev"], "arc_flat", "whole song",
                            f"dynamic range between sections is only {spread:.1f} LU ({lo['name']} {lo['lufs']:.1f} "
                            f"to {loudest['name']} {loudest['lufs']:.1f}){genre}; the song may feel flat",
                            f"thin out / lower layers in the calmer sections and let the peaks open up (flp_edit_notes "
                            f"velocity, flp_add_automation volume rides per section); aim for {st['aim']}"))
        elif spread > st["wide"]:
            out.append(_iss("med", "arc_wide", "whole song",
                            f"{spread:.1f} LU between {lo['name']} and {loudest['name']}{genre}: quiet parts will "
                            f"vanish at normal listening level", f"raise {lo['name']} (flp_add_automation volume, or "
                            f"velocities with flp_edit_notes) to within ~{st['wide'] - 4:.0f} LU of the loudest section"))
    if ((role or "").lower() == "opener" or st.get("must_build")) and not st.get("no_build_check"):
        p = top or loudest; pi = secs.index(p); who = "the opener" if (role or "").lower() == "opener" else "the song"
        if pi == 0:
            out.append(_iss(st["build_sev"], "arc_no_build", _sec_where(p), f"{who} starts at its loudest ({p['name']})",
                            "fine if it's meant to open on the hook; to build instead: a thinner first section "
                            "(fewer layers, lower velocities via flp_edit_notes)"))
        else:
            first = secs[0]
            if first["lufs"] > p["lufs"] - st["build_lu"] and id(p) not in reported:
                out.append(_iss(st["build_sev"], "arc_no_build", _sec_where(first),
                                f"{who} barely builds: {first['name']} is within {p['lufs'] - first['lufs']:.1f} "
                                f"LU of the peak {p['name']}", f"make {first['name']} {st['build_lu'] + 1:.0f}+ dB "
                                f"quieter than the peak ({fix_down})"))
            if st.get("early_peak") and len(secs) >= 4 and pi < 0.4 * (len(secs) - 1):
                out.append(_iss("low", "arc_early_peak", _sec_where(p),
                                f"{who} peaks early ({p['name']}, section {pi + 1} of {len(secs)})",
                                "move the biggest moment later (flp_arrange) or calm the early sections"))
    return rank(out)


def _sec_where(s):
    return f"section {s.get('name')}" + (f" (bar {s['bar']:g})" if isinstance(s.get("bar"), (int, float)) else "")


# ============================================================================= .flp reading (read-only)
def _juce_xml(state):
    """The XML inside a Spitfire / Splice (JUCE) plugin state, or None (same VC2! block _juce_xml_rewrite edits)."""
    k = state.find(b"VC2!")
    if k < 0:
        return None
    n = struct.unpack("<I", state[k + 4:k + 8])[0]
    return state[k + 8:k + 8 + n].decode("utf-8", "replace")


def _scan(d, ev):
    """Per channel (ChannelNew .. 51): enabled, type, insert, name. Mixer tracks: flags, name."""
    pay = H["_payload"]; chans = {}; cur = None
    for e in ev:
        i = e["id"]
        if i == H["EV_CHANNEL"]:
            cur = struct.unpack("<H", pay(d, e)[:2])[0]; chans[cur] = {"index": cur}
        elif cur is None:
            continue
        elif i == H["EV_CHANNEL_END"]:
            cur = None
        elif i == EV_CHAN_ENABLED:
            chans[cur]["enabled"] = bool(pay(d, e)[0])
        elif i == EV_CHAN_TYPE:
            chans[cur]["type"] = pay(d, e)[0]
        elif i == EV_CHAN_INSERT:
            chans[cur]["insert"] = struct.unpack("<h", pay(d, e)[:2])[0]
        elif i == H["EV_DISPLAY_NAME"] and "name" not in chans[cur]:
            chans[cur]["name"] = H["_text16"](pay(d, e))
        elif i == 94 and e["len"] == 2:                                # layer channel: one event per child
            chans[cur].setdefault("children", []).append(struct.unpack("<H", pay(d, e))[0])
    tracks = []; pend = {}
    for e in ev:
        i = e["id"]
        if i == H["EV_MIXER_NAME"]:
            pend["name"] = H["_text16"](pay(d, e))
        elif i == H["EV_MIXER_TRACK"]:
            p = pay(d, e); fl = struct.unpack("<I", p[4:8])[0] if len(p) >= 8 else MIXER_ENABLED
            tracks.append({"index": len(tracks), "flags": fl, "muted": not fl & MIXER_ENABLED, "name": pend.get("name")})
            pend = {}
    return chans, tracks


def channel_usage(d, ev=None):
    """What the song uses, over every arrangement's playlist: {played: {channel: notes in unmuted clips},
    muted_only: {channel: notes in muted clips}, clip_chans: channels with a placed audio / automation clip}."""
    ev = ev or H["_events"](d); pay = H["_payload"]; pats = H["_patterns"](d, ev)
    PB, CM, CR = H["PATTERN_BASE"], H["CLIP_MUTE"], H["CLIP_RECORD"]
    played, muted_only, clip_chans = {}, {}, set()
    for e in ev:
        if e["id"] != H["EV_PLAYLIST"] or not e["len"] or e["len"] % CR:
            continue
        p = pay(d, e)
        for o in range(0, len(p), CR):
            r = p[o:o + CR]
            item, ln = struct.unpack("<H", r[6:8])[0], struct.unpack("<I", r[8:12])[0]
            if item < PB:
                clip_chans.add(item); continue
            pat = pats.get(item - PB)
            if not pat:
                continue
            start = H["_clip_offsets"](r)[0]; tgt = muted_only if struct.unpack("<H", r[18:20])[0] & CM else played
            for n in pat["notes"]:
                if start <= n[0] < start + ln:
                    tgt[n[2]] = tgt.get(n[2], 0) + 1
    return {"played": played, "muted_only": muted_only, "clip_chans": clip_chans}


def cleanup_candidates(d, ev=None, chans=None, use=None):
    """Channels a cleanup may delete (fl_doctor's unused_channel / automation_unplaced, and what
    flp_remove_channels unused=true / unplaced_automation=true pick): {unused: [...], unplaced_automation: [...]}.
    Never picked as unused: automation clips (their own rule), layer children of a used layer, controller channels
    (sources of internal links) and channels an automation clip targets."""
    ev = ev or H["_events"](d); pay = H["_payload"]
    if chans is None:
        chans = _scan(d, ev)[0]
    use = use or channel_usage(d, ev)
    used = set(use["played"]) | set(use["muted_only"]) | set(use["clip_chans"])
    keep = set()
    for e in ev:
        if e["id"] == H["EV_REMOTE_LINK"]:
            p = pay(d, e)
            if len(p) >= 12:
                src, dest = struct.unpack("<H", p[2:4])[0], struct.unpack("<H", p[10:12])[0]
                if chans.get(src, {}).get("type") != 5:
                    keep.add(src)                                  # a controller generator drives something
                if dest < 0x0FFF:
                    keep.add(dest)
    todo = [c for c in used if chans.get(c, {}).get("children")]
    while todo:                                                    # children of used layers (nested too)
        for ch in chans.get(todo.pop(), {}).get("children", []):
            if ch not in used:
                used.add(ch); todo.append(ch)
    unused, unplaced = [], []
    for i, c in sorted(chans.items()):
        typ = c.get("type")
        if typ == 5:
            if i not in use["clip_chans"]:
                unplaced.append(i)
        elif i not in used and i not in keep:
            unused.append(i)
    return {"unused": unused, "unplaced_automation": unplaced}


def _plugin_label(d, ev, blk):
    if blk.get("plugin") == "Fruity Wrapper" and "state" in blk:
        return H["_wrapper_name"](H["_payload"](d, ev[blk["state"]])) or "VST (unknown)"
    return blk.get("plugin")


def _sound_ok(label):
    """flh_catalog's edition-aware ownership (True | 'trial' | 'unknown'), or None when it isn't loaded / fails."""
    fn = H.get("sound_ok")
    if not callable(fn):
        return None
    try:
        r = fn(label)
    except Exception:
        return None
    return r if r is True or r in ("trial", "unknown") else None


def _ownership(label, prof, is_vst):
    """-> (severity, kind, text) or None. Order: the profile's trial_plugins / owned_plugins (the user's own words),
    then flh_catalog.sound_ok (knows the FL edition), then the built-in fallback lists."""
    trial = list(prof.get("trial_plugins") or [])
    owned = prof.get("owned_plugins") or []
    trial_txt = f"'{label}' is a trial plugin here: saves show a trial warning and it may go silent / noisy in renders"
    if trial and _match(label, trial):
        return "high", "trial_plugin", trial_txt
    if owned and _match(label, owned):
        return None
    ok = _sound_ok(label)
    if ok is True:
        return None
    if ok == "trial":
        return "high", "trial_plugin", trial_txt
    if ok is None and not trial and _match(label, TRIAL_DEFAULT):     # no edition info at all: the old list
        return "high", "trial_plugin", trial_txt + " (assumed: FL editions below All Plugins; set owned_plugins " \
                                                   "in the profile if yours has it)"
    if ok == "unknown" and _match(label, TRIAL_DEFAULT):
        return "low", "not_owned", f"'{label}' is trial-only in some FL editions; check yours has it"
    if owned:
        if is_vst:
            return "med", "not_owned", f"'{label}' isn't in the collection's owned plugins"
        return "low", "not_owned", f"native '{label}' isn't in owned_plugins (check your FL edition has it)"
    if is_vst and not _match(label, FREE_VST_DEFAULT):
        return "low", "unknown_plugin", f"'{label}': ownership unknown (no owned_plugins in the profile) - ask"
    return None


# ============================================================================= practically silent channels
LEVEL_DEFAULT = {"level_words": ["volume", "vol", "gain", "level", "output", "master", "amp", "amplitude", "dynamics",
                                 "dynamic", "expression", "expr", "loudness"],
                 "weak_words": ["velocity amount", "vel amount", "velocity sensitivity", "velocity sens"],
                 "not_level_words": ["send", "reverb", "verb", "delay", "echo", "chorus", "lfo", "mod", "env",
                                     "envelope", "attack", "decay", "sustain", "release", "hold", "filter", "cutoff",
                                     "resonance", "drive", "pan", "width", "noise", "fx", "eq", "comp", "mix", "sub",
                                     "pitch", "tune", "fine", "semi", "boost", "saturation", "threshold", "sidechain",
                                     "duck", "glide", "porta", "osc", "voice", "unison", "detune"],
                 "midi_cc": {"7": "volume (CC7)", "11": "expression (CC11)"},
                 "zero_value": 0.001, "silent_db": -60.0, "max_params_live": 400}
_LEVEL = {}


def level_rules():
    """Which plugin parameters set how loud an instrument plays (data/doctor/level_params.json, merged with the
    user's <state folder>/doctor/level_params.json: lists add up, dicts / numbers override). Words are matched
    against the plugin's OWN parameter names (any plugin), never a vendor list."""
    if _LEVEL:
        return _LEVEL
    cfg = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in
           LEVEL_DEFAULT.items()}
    paths = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "doctor", "level_params.json")]
    try:
        paths.append(os.path.join(os.path.dirname(H["STATE"]), "doctor", "level_params.json"))
    except Exception:
        pass
    import json
    for i, p in enumerate(paths):
        try:
            with open(p, encoding="utf-8") as f:
                u = json.load(f)
        except (OSError, ValueError):
            continue
        for k, v in u.items():
            if k.startswith("_"):
                continue
            if i == 0:                                             # the shipped table: replaces the built-in copy
                cfg[k] = list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v
            elif isinstance(v, list) and isinstance(cfg.get(k), list):   # the user's: adds to it
                cfg[k] = cfg[k] + [x for x in v if x not in cfg[k]]
            elif isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    _LEVEL.update(cfg)
    return _LEVEL


def _words_of(name):
    """'outputGain' / 'Out_Level 2' -> ['output', 'gain'] / ['out', 'level', '2'] (camelCase and separators)."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name or ""))
    return [w for w in re.split(r"[^a-z0-9]+", s.lower()) if w]


def level_kind(name, cfg=None):
    """'level' when a parameter name says it sets the instrument's level (volume / gain / dynamics / expression ...),
    'weak' for velocity-amount style names (0 = no velocity response on some plugins, silent on others), else None."""
    cfg = cfg or level_rules(); ws = _words_of(name)
    if not ws or set(ws) & set(cfg["not_level_words"]):
        return None
    if set(ws) & set(cfg["level_words"]):
        return "level"
    flat = " ".join(ws)
    if any(w in flat for w in cfg["weak_words"]):
        return "weak"
    return None


def _shows_silent(value, text, cfg):
    """A level knob that sits at silence by FL's display text (-inf / <= silent_db dB / 0 % / 0 / Off), else by its
    0..1 value when the text says nothing."""
    t = str(text or "").strip().lower()
    if t:
        if "inf" in t or "∞" in t or t in ("off", "mute", "muted", "silent", "none"):
            return True
        m = re.search(r"-?\d+(?:[.,]\d+)?", t)
        if m:
            v = float(m.group(0).replace(",", "."))
            if "db" in t:
                return v <= float(cfg["silent_db"])
            return v <= 0.0
    try:
        return float(value) <= float(cfg["zero_value"])
    except (TypeError, ValueError):
        return False


def quiet_knobs_static(state, cfg=None):
    """Level-type parameters at 0 in a saved plugin state that FL can't show us (no FL): JUCE-style XML <PARAM>s
    whose values are 0..1 (they carry a MIDI mapping ccFrom/ccTo - a gain stored in dB, where 0 = unity, is never
    read as silent). By the param's own id words, or its MIDI CC (7 = volume, 11 = expression, the MIDI standard).
    -> [{param, name, value, why}]
    PC-VERIFY: a LABS / INSTRUMENT channel saved with Dynamics + Expression at 0 is flagged; one at normal levels
    isn't (checklists/review.md R1)."""
    cfg = cfg or level_rules()
    xml = _juce_xml(state or b"")
    if not xml:
        return []
    out = []
    for tag in re.findall(r"<PARAM\s[^>]*>", xml):
        a = dict(re.findall(r'([A-Za-z_:]+)="([^"]*)"', tag))
        if "ccFrom" not in a and "ccTo" not in a:                    # units unknown: can't tell 0 from unity
            continue
        try:
            v = float(a.get("value", "1"))
        except ValueError:
            continue
        if v > float(cfg["zero_value"]):
            continue
        pid = a.get("id", "")
        cc = str(a.get("cc", "-1")).split(".")[0]
        kind = level_kind(pid, cfg)
        why = cfg["midi_cc"].get(cc) if cc in cfg["midi_cc"] else (pid if kind else None)
        if why:
            out.append({"param": pid, "name": why, "value": v, "kind": kind or "level"})
    return out


def quiet_knobs_live(index, cfg=None):
    """Level-type parameters at silence on a channel's instrument in the open FL, by the plugin's own parameter names
    and FL's display text. -> ({plugin, params: [{param, name, value, text, kind}]}) or raises (bridge errors).
    PC-VERIFY: fl_doctor(live=true) on a song with a synth whose Volume / Output knob is at 0 names that knob; no
    false flags on a normal song with a few VSTs and FL natives (checklists/review.md R3)."""
    cfg = cfg or level_rules()
    r = H["fl_plugin_params"](index=int(index), slot=-1)
    out = []
    for p in (r or {}).get("params") or []:
        kind = level_kind(p.get("name"), cfg)
        if kind and _shows_silent(p.get("value"), p.get("text"), cfg):
            out.append({"param": p.get("param"), "name": p.get("name"), "value": p.get("value"), "text": p.get("text"),
                        "kind": kind})
    return {"plugin": (r or {}).get("plugin"), "params": out}


def _quiet_issues(project, d, ev, chans, played, cname, live, notes):
    """practically silent channels: content the state points at isn't on this PC (CONTENT_CHECKS), or a level knob
    (volume / gain / dynamics / expression ... by the plugin's own names) at 0 on a channel that plays notes."""
    issues = []; cfg = level_rules(); pay = H["_payload"]
    status = H.get("plugin_content_status")
    for i, c in sorted(chans.items()):
        if c.get("type") not in (None, 2) or "state" not in c:
            continue
        n = played.get(i, 0); st = pay(d, ev[c["state"]]); label = _plugin_label(d, ev, c)
        cs = status(label, st) if callable(status) else None
        if cs and cs.get("problem"):
            issues.append(_iss(("med" if cs.get("soft") else "high") if n else "low", "content_missing", cname(i),
                               f"{cs.get('family')} '{cs.get('sound')}'" + (f" ({n} notes)" if n else "") + ": "
                               + cs["problem"], cs.get("fix") or "switch the channel to a sound you have"))
        if not n:
            continue
        for q in quiet_knobs_static(st, cfg):
            issues.append(_iss("high" if q["kind"] == "level" else "low", "silent_knob", cname(i),
                               f"{label}: its {q['name']} is at {q['value']:g} in the saved state - the channel plays "
                               f"{n} notes practically silent",
                               _call("flp_plugin_settings", {"project": C.Raw("project"), "output": C.Raw("output"),
                                                             "channel": i, "params": {q["param"]: 1.0}}) +
                               " (or turn it up in the plugin; live: fl_plugin_params index=" + str(i) + ")"))
    if live:
        try:
            path, _ = H["_open_project_guess"]()
        except Exception:
            path = None
        same = path and os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(project))
        if not same:
            notes.append("live knob check skipped: FL doesn't have this project open")
        else:
            for i, c in sorted(chans.items()):
                if c.get("type") not in (None, 2) or not c.get("plugin") or not played.get(i):
                    continue
                try:
                    r = quiet_knobs_live(i, cfg)
                except Exception as e:                             # noqa: BLE001 - one channel never stops the check
                    notes.append(f"live knob check of channel {i} failed: {str(e)[:80]}")
                    continue
                for q in r["params"]:
                    issues.append(_iss("high" if q["kind"] == "level" else "low", "silent_knob", cname(i),
                                       f"{r.get('plugin') or _plugin_label(d, ev, c)}: '{q['name']}' (param "
                                       f"{q['param']}) shows '{q.get('text')}' in FL - the channel plays "
                                       f"{played[i]} notes practically silent" +
                                       ("" if q["kind"] == "level" else " (if 0 means 'no velocity' here, ignore)"),
                                       _call("fl_plugin_set_value", {"index": i, "param": q["param"],
                                                                     "target": C.Raw("a normal level")}) +
                                       " or " + _call("fl_plugin_set", {"index": i, "param": q["param"],
                                                                        "value": 0.8}) + ", then fl_save"))
    return issues


def fl_doctor(project, live=False):
    """Static check of a .flp (no FL needed): trial / not-owned plugins, Spitfire instruments listening on one MIDI
    channel (silent), practically silent channels (content not on this PC, a level knob at 0 - live=true also asks
    FL for every playing channel's knobs by their own names), unused or muted channels, routing to missing / muted
    mixer tracks, automation targets, empty patterns on the playlist, missing samples, master stereo widening, long
    dead tail."""
    t0 = time.time()
    d = H["_load"](project); ev, blocks, fx = H["_structure"](d); P = H["_ppq"](d); pay = H["_payload"]
    try:
        bpb = float(H["_qbar"](H["flp_project_info"](project)))
    except Exception:
        bpb = 4.0
    bar_t = bpb * P
    prof = _profile()
    try:
        jr = S.journal_get(project)
    except Exception:
        jr = {}
    roles = {str(k): v for k, v in (jr.get("channels") or {}).items()}
    chans, tracks = _scan(d, ev)
    for i, b in blocks.items():
        chans.setdefault(i, {"index": i}).update({k: b[k] for k in ("plugin", "state") if k in b})
    pats = H["_patterns"](d, ev)
    try:
        _, recs = H["_playlist_event"](d, ev)
    except ValueError:
        recs = []
    PB = H["PATTERN_BASE"]; CM = H["CLIP_MUTE"]; issues = []

    def cname(i):
        c = chans.get(i, {}); r = roles.get(str(i), {})
        nm = c.get("name") or r.get("name") or f"Channel {i}"
        return f"channel {i} '{nm}'" + (f" ({r['role']})" if r.get("role") else "")

    # ---- what actually plays (unmuted clips), per channel
    song_end, last_sound = 0, 0
    for r in recs:
        pos, item, ln = struct.unpack("<I", r[0:4])[0], struct.unpack("<H", r[6:8])[0], struct.unpack("<I", r[8:12])[0]
        muted = bool(struct.unpack("<H", r[18:20])[0] & CM)
        if not muted:
            song_end = max(song_end, pos + ln)
        if item < PB:                                             # audio / automation clip of a channel
            if not muted and chans.get(item, {}).get("type") == 4:
                last_sound = max(last_sound, pos + ln)
            continue
        pid = item - PB; pat = pats.get(pid)
        start = H["_clip_offsets"](r)[0]
        bar = round(1 + pos / bar_t, 2)
        if pat is None or (not pat["notes"] and not pat["ctrl"]):
            issues.append(_iss("low", "empty_pattern", f"playlist bar {bar:g}",
                               f"empty pattern {pid} '{(pat or {}).get('name', '')}' is placed in the playlist",
                               "remove the clip (" + _call("flp_playlist", {"project": C.Raw("project"),
                                                                             "action": "remove", "...": 1}) +
                               ") or " + _call("flp_pattern_tools", {"project": C.Raw("project"),
                                                                     "action": "remove_empty"})))
            continue
        inside = [n for n in pat["notes"] if start <= n[0] < start + ln]
        if pat["notes"] and not inside and not muted:
            issues.append(_iss("low", "empty_clip", f"playlist bar {bar:g}",
                               f"clip of '{pat['name'] or pid}' plays an empty part of the pattern (no notes inside)",
                               _call("flp_playlist", {"project": C.Raw("project"), "action": "resize", "...": 1}) +
                               " (or trim_start_bars), or remove it"))
        for n in inside:
            if not muted:
                last_sound = max(last_sound, pos + n[0] - start + n[3])
    # who plays what, over every arrangement (the same numbers flp_remove_channels uses)
    use = channel_usage(d, ev); played, muted_only, clip_chans = use["played"], use["muted_only"], use["clip_chans"]
    cand = cleanup_candidates(d, ev, chans, use)
    # ---- plugins: ownership, Spitfire MIDI channel
    plugins = set()
    for i, c in sorted(chans.items()):
        if not c.get("plugin"):
            continue
        is_vst = c["plugin"] == "Fruity Wrapper"; label = _plugin_label(d, ev, c); plugins.add(label)
        own = _ownership(label, prof, is_vst)
        if own:
            sev, kind, txt = own
            if sev == "high" or played.get(i):
                issues.append(_iss(sev, kind, cname(i), txt, "swap it for an owned instrument: flp_load_plugin / "
                                   "flp_instrument_sound (or flh_profile set owned_plugins if you own it)"))
        if "state" in c:
            xml = _juce_xml(pay(d, ev[c["state"]]))
            m = xml and re.search(r'<SETTING id="p_midiChannel" value="([^"]*)"', xml)
            if m and m.group(1).strip() not in ("0", "0.0", ""):
                meta = re.search(r'<META [^>]*name="([^"]*)"', xml)
                issues.append(_iss("high", "silent_instrument", cname(i),
                                   f"{label}{' (' + meta.group(1) + ')' if meta else ''} listens only on MIDI channel "
                                   f"{m.group(1)}: it stays SILENT for the notes FL sends",
                                   _call("flp_plugin_settings", {"project": C.Raw("project"), "output": C.Raw("output"),
                                                                 "channel": i, "settings": {"p_midiChannel": 0}})))
    for (t, sl), f in sorted(fx.items(), key=lambda kv: (kv[0][0] or 0, kv[0][1] or 0)):
        label = _plugin_label(d, ev, f); is_vst = f.get("plugin") == "Fruity Wrapper"; plugins.add(label)
        own = _ownership(label, prof, is_vst)
        if own:
            sev, kind, txt = own
            issues.append(_iss(sev, kind, f"mixer track {t} slot {sl}", txt,
                               _call("flp_remove_effect", {"project": C.Raw("project"), "output": C.Raw("output"),
                                                           "track": t, "slot": sl}) + " or swap it for an owned one"))
        if t == 0 and _match(label, ["*" + w + "*" for w in WIDENERS], prefix=False):
            bad = [r for r in ((jr.get("review") or {}).get("sections") or []) if (r.get("corr") or 1) < 0.2]
            issues.append(_iss("high" if bad else "med", "master_widener", f"master slot {sl}",
                               f"'{label}' widens the whole mix on the master: L/R correlation risk (dull, phasey; "
                               f"mono collapse)" + (f" - last review measured corr {min(r['corr'] for r in bad):.2f}"
                                                    if bad else ""),
                               _call("flp_remove_effect", {"project": C.Raw("project"), "output": C.Raw("output"),
                                                           "track": 0, "slot": sl}) +
                               ", or widen single layers instead; confirm with fl_review (corr > 0.3)"))
    # ---- practically silent: content not on this PC, level knobs at 0 (by the plugin's own names)
    doc_notes = []
    try:
        issues += _quiet_issues(project, d, ev, chans, played, cname, live, doc_notes)
    except Exception as e:                                         # never let this check break the doctor
        doc_notes.append(f"silent-channel check failed: {str(e)[:100]}")
    # ---- channels: unused, muted, routing
    for i, c in sorted(chans.items()):
        typ = c.get("type"); n_pl = played.get(i, 0); n_mu = muted_only.get(i, 0)
        if typ == 5:
            continue                                               # automation: below
        if typ == 4:
            if i in cand["unused"]:
                issues.append(_iss("low", "unused_channel", cname(i), "audio clip channel isn't in the playlist",
                                   _call("flp_remove_channels", {"project": C.Raw("project"), "channels": [i]}) +
                                   " (unused=true removes every unused channel; dry_run=true shows first), or place "
                                   "it (" + _call("flp_playlist", {"project": C.Raw("project"), "action": "place",
                                                                    "channel": i}) + ")"))
            continue
        if c.get("enabled") is False and n_pl:
            issues.append(_iss("med", "muted_with_notes", cname(i), f"channel is muted but has {n_pl} notes in the song",
                               "unmute it (" + _call("fl_channel_set", {"index": i, "muted": False}) +
                               ") or delete its notes if it's leftover"))
        elif not n_pl and n_mu:
            issues.append(_iss("low", "muted_clip_only", cname(i), f"its {n_mu} notes are only in muted playlist clips",
                               "unmute the clips (" + _call("flp_playlist", {"project": C.Raw("project"),
                                                                              "action": "unmute"}) +
                               ") or delete the channel in FL"))
        elif i in cand["unused"]:
            issues.append(_iss("low", "unused_channel", cname(i), "no notes anywhere in the song (leftover)",
                               _call("flp_remove_channels", {"project": C.Raw("project"), "channels": [i]}) +
                               " (unused=true removes every unused channel; dry_run=true shows first)"))
        ins = c.get("insert")
        if n_pl and c.get("enabled") is not False and ins is not None and ins > 0:
            if tracks and ins >= len(tracks):
                issues.append(_iss("med", "route_missing", cname(i), f"routed to mixer track {ins}, which isn't in the "
                                   f"project's mixer ({len(tracks)} tracks saved)",
                                   "route it to a real track: " + _call("fl_channel_set", {"index": i,
                                                                                           "mixer_track": C.Raw("N")})))
            elif tracks and tracks[ins]["muted"]:
                issues.append(_iss("high", "muted_track", cname(i), f"plays {n_pl} notes into mixer track {ins} "
                                   f"'{tracks[ins]['name'] or ins}', which is MUTED (silent)",
                                   _call("fl_mixer_set", {"track": ins, "muted": False}) +
                                   ", or route the channel elsewhere"))
    if tracks and tracks[0]["muted"]:
        issues.append(_iss("high", "master_muted", "master", "the master track is muted: renders are silent",
                           _call("fl_mixer_set", {"track": 0, "muted": False})))
    # ---- automation clips
    autos = []
    links = {}
    for e in ev:
        if e["id"] == H["EV_REMOTE_LINK"]:
            p = pay(d, e)
            if len(p) >= 12:
                links[struct.unpack("<H", p[2:4])[0]] = (struct.unpack("<H", p[8:10])[0], struct.unpack("<H", p[10:12])[0])
    for i, c in sorted(chans.items()):
        if c.get("type") != 5:
            continue
        name = c.get("name") or f"Channel {i}"; tgt, problem = _auto_target(links.get(i), fx, tracks, chans, d, ev, name)
        autos.append({"channel": i, "name": name, "target": tgt})
        if problem:
            issues.append(_iss("med", "automation_target", f"automation channel {i} '{name}'", problem,
                               "re-point it: " + _call("flp_add_automation", {"project": C.Raw("project"),
                                                                              "name": C.Raw("same name"),
                                                                              "replace": True, "...": 1}) +
                               " with the right track/slot, or delete the clip"))
        if i in cand["unplaced_automation"]:
            issues.append(_iss("low", "automation_unplaced", f"automation channel {i} '{name}'",
                               "automation clip isn't in the playlist (does nothing)",
                               _call("flp_remove_channels", {"project": C.Raw("project"), "channels": [i]}) +
                               " (unplaced_automation=true removes all of them), or place it (" +
                               _call("flp_playlist", {"project": C.Raw("project"), "action": "place", "channel": i}) +
                               ")"))
    # ---- health: samples, VSTs, unplaced patterns (reuse flp_health)
    try:
        h = H["flp_health"](project)
    except Exception:
        h = {}
    for sm in h.get("samples") or []:
        if not sm.get("found"):
            ch = sm.get("channel"); used = ch in clip_chans or played.get(ch)
            issues.append(_iss("high" if used else "low", "missing_sample", cname(ch),
                               f"sample file missing: {sm.get('path')}",
                               "point it at the file (re-add with flp_add_audio_clip) or flp_collect_samples once "
                               "the file is found"))
    for v in h.get("vst_plugins") or []:
        if v.get("installed") is False:
            issues.append(_iss("high", "vst_missing", cname(v["channel"]), f"VST '{v.get('vst')}' isn't installed "
                               f"({v.get('file')})", "install it or swap the channel's sound (flp_load_plugin)"))
    for txt in h.get("issues") or []:
        if "VST effect not found" in txt:
            issues.append(_iss("high", "vst_missing", "mixer", txt, "install it or flp_remove_effect"))
        elif "isn't in the playlist" in txt:
            issues.append(_iss("low", "unplaced_pattern", "patterns", txt,
                               "place it (" + _call("flp_playlist", {"project": C.Raw("project"), "action": "place",
                                                                     "...": 1}) +
                               ") or delete it (" + C.tool_name(H, "flp_pattern_tools") + ")"))
    # ---- dead tail
    if song_end and last_sound and (song_end - last_sound) / bar_t > 4:
        tail = (song_end - last_sound) / bar_t
        issues.append(_iss("med" if tail > 8 else "low", "dead_tail", f"bars {1 + last_sound / bar_t:.0f}-"
                           f"{1 + song_end / bar_t:.0f}", f"{tail:.1f} bars of song after the last note/audio ends "
                           f"(a long clip, e.g. the Tempo lane, keeps the song running)",
                           "shorten the last clips (" + _call("flp_playlist", {"project": C.Raw("project"),
                                                                              "action": "resize", "...": 1}) +
                           "); " + C.tool_name(H, "audio_finish") + " trims the tail of renders"))
    # ---- the notes themselves (flh_score): instrument ranges, and a quick clash summary
    score = {}
    sc = H.get("score_doctor")
    if callable(sc):
        try:
            r = sc(project)
            for i in r["range"][:4]:
                issues.append(_iss("med", "out_of_range", f"{i['where']} {i['parts']}", f"{i['notes']}: {i['why']}",
                                   i["fix"]))
            for i in r["clashes"]:
                issues.append(_iss("med" if i["severity"] == "high" else "low", "clash", f"{i['where']} {i['parts']}",
                                   f"{i['kind']} {i['notes']}: {i['why']}", i["fix"] + " (flp_clashes lists them all)"))
            score = {"clashes": r["clash_counts"], "out_of_range": len(r["range"])}
        except Exception as e:                                     # never let the music check break the doctor
            score = {"error": f"score check failed: {str(e)[:120]}"}
    ranked = rank(issues)
    used_ch = sorted(set(played) | {c for c in clip_chans if chans.get(c, {}).get("type") == 4})
    return {"project": project, "issues": ranked[:15], "more": max(0, len(ranked) - 15),
            "counts": {s: sum(1 for x in ranked if x["severity"] == s) for s in SEV},
            "summary": {"channels": len(chans), "channels_playing": len(used_ch), "patterns": len(pats),
                        "mixer_tracks": len(tracks), "plugins": sorted(p for p in plugins if p),
                        "automation": autos[:20], "song_bars": round(song_end / bar_t, 2),
                        "last_sound_bar": round(1 + last_sound / bar_t, 2),
                        **({"notes_check": score} if score else {})},
            **({"notes": doc_notes} if doc_notes else {}),
            "took_s": round(time.time() - t0, 2)}


def _auto_target(link, fx, tracks, chans, d, ev, name):
    """(target text, problem or None) of an automation clip's remote link (param id, destination)."""
    if not link:
        return None, "no target link found (the clip automates nothing)"
    pid, dest = link
    if dest >= 0x7000:
        t, sl = (dest - 0x7000) >> 6, (dest - 0x7000) & 63
        tn = "Master" if t == 0 else f"Insert {t}"
        if tracks and t >= len(tracks):
            return f"{tn}", f"targets mixer track {t}, which isn't in the project"
        if pid & 0x8000:
            f = fx.get((t, sl))
            if not f:
                return f"{tn} slot {sl} param {pid & 0x7FFF}", f"targets an empty slot ({tn} slot {sl}): the plugin " \
                                                                f"it automated was removed or moved"
            label = _plugin_label(d, ev, f); tgt = f"{tn} slot {sl} {label} param {pid & 0x7FFF}"
            head = name.split(" - ")[0].strip()
            if " - " in name and head and head.lower() not in (label or "").lower() and \
                    head.lower() not in (f.get("name") or "").lower():
                return tgt, f"named for '{head}' but now drives '{label}' in {tn} slot {sl} (target changed)"
            return tgt, None
        what = {0x1FC0: "volume", 0x1FC1: "pan"}.get(pid, f"param {pid:#x}")
        tgt = f"{tn} {what}"
        m = re.search(r"\b(Master|Insert\s*(\d+))\b", name)
        if m and (0 if m.group(1) == "Master" else int(m.group(2) or -1)) != t:
            return tgt, f"named for '{m.group(1)}' but drives {tn} (target changed)"
        return tgt, None
    if dest in chans:
        return f"channel {dest} param {pid & 0x7FFF}", None
    return f"destination {dest:#x}", f"targets channel {dest}, which no longer exists"


# ============================================================================= FLAC decoding (numpy)
class _NeedExternal(Exception):
    pass


def _bits(data, p, n):
    b = p >> 3; off = p & 7; nb = (off + n + 7) >> 3
    return (int.from_bytes(data[b:b + nb], "big") >> (nb * 8 - off - n)) & ((1 << n) - 1)


def _sbits(data, p, n):
    v = _bits(data, p, n)
    return v - (1 << n) if n and v >> (n - 1) else v


_SZ = {1: 8, 2: 12, 4: 16, 5: 20, 6: 24, 7: 32}
_BS = {1: 192, 2: 576, 3: 1152, 4: 2304, 5: 4608}


def _frame_header(data, off, sbps):
    """A frame header at byte `off` if it's valid (CRC-8 checked): {off, bits, bs, ca, bps, num, var} or None."""
    import flac_lite
    try:
        b1, b2, b3 = data[off + 1], data[off + 2], data[off + 3]
        bsc, src, ca, ssc = b2 >> 4, b2 & 15, b3 >> 4, (b3 >> 1) & 7
        if bsc == 0 or src == 15 or ca > 10 or ssc == 3 or b3 & 1:
            return None
        i = off + 4; b0 = data[i]
        if b0 < 0x80:
            num = b0; i += 1
        else:
            n, mask = 0, 0x80
            while b0 & mask:
                n += 1; mask >>= 1
            if not 2 <= n <= 7:
                return None
            num = b0 & (mask - 1)
            for j in range(1, n):
                c = data[i + j]
                if c & 0xC0 != 0x80:
                    return None
                num = (num << 6) | (c & 0x3F)
            i += n
        if bsc in _BS:
            bs = _BS[bsc]
        elif bsc == 6:
            bs = data[i] + 1; i += 1
        elif bsc == 7:
            bs = (data[i] << 8 | data[i + 1]) + 1; i += 2
        else:
            bs = 256 << (bsc - 8)
        i += 1 if src == 12 else (2 if src in (13, 14) else 0)
        if flac_lite._crc8(data[off:i]) != data[i]:
            return None
        return {"off": off, "bits": (i + 1 - off) * 8, "bs": bs, "ca": ca, "bps": _SZ.get(ssc, sbps), "num": num,
                "var": b1 & 1}
    except IndexError:
        return None


def _frame_list(data, p0, sbps):
    """Every frame's header, found by sync code + CRC-8 + consecutive frame numbers (no decoding needed)."""
    import numpy as np
    a = np.frombuffer(data, np.uint8)
    cand = np.flatnonzero((a[p0:-1] == 0xFF) & ((a[p0 + 1:] & 0xFE) == 0xF8)) + p0
    frames, want = [], 0
    for off in cand.tolist():
        if frames and off < frames[-1]["off"] + 4:
            continue
        h = _frame_header(data, off, sbps)
        if h and h["num"] == want:
            frames.append(h); want = h["num"] + (h["bs"] if h["var"] else 1)
    return frames


def flac_load(path, dtype=None):
    """-> (samples float64 (n, ch) in -1..1, rate, bits). Any FLAC up to 32-bit: fixed-predictor (flac_lite's own)
    and LPC-coded (FL's export, the flac tool, ffmpeg) decode here with numpy, bit exact; flac/ffmpeg are only a
    fallback for streams this reader refuses. dtype (float32): the output is built in it batch by batch (memory:
    the output plus one batch, also for hour-long files)."""
    data = open(path, "rb").read()
    if data[:4] != b"fLaC":
        raise ValueError(f"{path}: not a FLAC file")
    p = 4; rate = ch = bps = total = None
    while True:
        hdr = data[p]; n = int.from_bytes(data[p + 1:p + 4], "big")
        if hdr & 0x7F == 0:
            v = int.from_bytes(data[p + 14:p + 22], "big")
            rate, ch, bps, total = v >> 44, ((v >> 41) & 7) + 1, ((v >> 36) & 31) + 1, v & ((1 << 36) - 1)
        p += 4 + n
        if hdr & 0x80:
            break
    try:
        return _flac_frames(data, p, ch, bps, total, dtype=dtype), rate, bps
    except _NeedExternal:
        return _flac_external(path)


def _flac_frames(data, p0, nch, sbps, total, batch=512, dtype=None):
    import numpy as np
    frames = _frame_list(data, p0, sbps)
    if not frames:
        raise ValueError("no FLAC frames found")
    have = sum(f["bs"] for f in frames)
    if total and have < total:                                       # STREAMINFO says how long it should be
        raise ValueError(f"the FLAC is cut off: {have} of {total} samples (an unfinished copy or a broken download)")
    n = min(have, total) if total else have
    x = np.empty((n, nch), dtype=dtype or np.float64)               # filled batch by batch: no list of int64
    scale = 1.0 / float(1 << (sbps - 1))                             # batches + full-length copies
    a = 0
    for g in range(0, len(frames), batch):
        grp = frames[g:g + batch]
        end = frames[g + batch]["off"] if g + batch < len(frames) else len(data)
        y = _decode_batch(data, grp, end, nch)
        k = min(len(y), n - a)
        x[a:a + k] = y[:k] * scale
        a += k
    return x[:a]


_FIRST1 = None


def _first1():
    """[byte * 8 + o] -> offset of the first 1 bit at or after bit o (MSB first) in the byte, 8 if none."""
    global _FIRST1
    if _FIRST1 is None:
        import numpy as np
        t = np.full(256 * 8, 8, np.int64)
        for v in range(256):
            for o in range(8):
                for j in range(o, 8):
                    if v >> (7 - j) & 1:
                        t[v * 8 + o] = j; break
        _FIRST1 = t
    return _FIRST1


def _decode_batch(data, frames, end, nch):
    """Decode consecutive frames together: per stage (channel, partition) the Rice chains of all frames advance in
    lockstep, one vectorized step per residual index (next 1 bit found by byte tables, no per-bit table)."""
    import numpy as np
    a0 = frames[0]["off"]; nbytes = end - a0
    buf = np.zeros(nbytes + 16, np.uint8); buf[:nbytes] = np.frombuffer(data, np.uint8, nbytes, a0)
    TAB = _first1().reshape(256, 8).astype(np.int8); TAB0 = TAB[:, 0].astype(np.int64)
    nzb = np.flatnonzero(buf)                                       # FB[i]: first 1 bit at or after byte i
    FB = np.full(len(buf) + 1, (len(buf) - 1) * 8, np.int64)
    if len(nzb):
        first = (nzb * 8 + TAB0[buf[nzb]]).astype(np.int64)
        ln = np.empty(len(nzb), np.int64); ln[0] = nzb[0] + 1; ln[1:] = np.diff(nzb)
        FB[:nzb[-1] + 1] = np.repeat(first, ln)
    LIM = nbytes * 8; base = a0 * 8; F = len(frames)
    pos = [(f["off"] - a0) * 8 + f["bits"] for f in frames]

    def read(p, k):
        """Unsigned k-bit values (k <= 32, array or scalar) at relative bit positions p (array)."""
        b = p >> 3; w = buf[b].astype(np.int64)
        for j in range(1, 5):
            w = (w << 8) | buf[b + j]
        return (w >> (40 - (p & 7) - k)) & ((np.int64(1) << k) - 1)

    def gather(p, n, count):
        if n > 32:
            raise _NeedExternal("33-bit samples")
        return read(p + np.arange(count, dtype=np.int64) * n, n)

    chans = [[None] * nch for _ in range(F)]
    for c in range(nch):
        info = []
        for fi, f in enumerate(frames):
            ca, bps = f["ca"], f["bps"]
            if (ca < 8 and ca + 1 != nch) or (ca >= 8 and nch != 2):
                raise ValueError("FLAC channel layout changes between frames")
            bps += 1 if (ca == 8 and c == 1) or (ca == 9 and c == 0) or (ca == 10 and c == 1) else 0
            p = pos[fi] + 1; typ = _bits(data, base + p, 6); p += 6; wasted = 0
            if _bits(data, base + p, 1):
                p += 1; wasted = 1
                while not _bits(data, base + p, 1):
                    wasted += 1; p += 1
            p += 1; bps -= wasted; bs = f["bs"]
            if typ == 0:
                chans[fi][c] = np.full(bs, _sbits(data, base + p, bps), np.int64) << wasted; p += bps
                info.append(None)
            elif typ == 1:
                v = gather(p, bps, bs); p += bs * bps
                chans[fi][c] = np.where(v >> (bps - 1), v - (1 << bps), v) << wasted; info.append(None)
            elif 8 <= typ <= 12:
                o = typ - 8; warm = []
                for _ in range(o):
                    warm.append(_sbits(data, base + p, bps)); p += bps
                method = _bits(data, base + p, 2); po = _bits(data, base + p + 2, 4); p += 6
                if method > 1:
                    raise ValueError("unknown FLAC residual coding")
                info.append({"o": o, "warm": warm, "wasted": wasted, "po": po, "pb": 4 if method == 0 else 5,
                             "parts": [], "bs": bs})
            elif typ >= 32:                                           # LPC: order, warm-up, quantized coefficients
                o = (typ & 31) + 1; warm = []
                for _ in range(o):
                    warm.append(_sbits(data, base + p, bps)); p += bps
                prec = _bits(data, base + p, 4) + 1; p += 4
                if prec == 16:
                    raise ValueError("invalid FLAC LPC precision")
                shift = _sbits(data, base + p, 5); p += 5
                if shift < 0:
                    raise _NeedExternal("negative LPC shift")
                coefs = []
                for _ in range(o):
                    coefs.append(_sbits(data, base + p, prec)); p += prec
                method = _bits(data, base + p, 2); po = _bits(data, base + p + 2, 4); p += 6
                if method > 1:
                    raise ValueError("unknown FLAC residual coding")
                info.append({"o": o, "warm": warm, "wasted": wasted, "po": po, "pb": 4 if method == 0 else 5,
                             "parts": [], "bs": bs, "lpc": coefs, "shift": shift})
            else:
                raise ValueError(f"reserved FLAC subframe type {typ}")
            pos[fi] = p
        maxp = max((1 << i["po"] for i in info if i), default=0)
        for part in range(maxp):
            group = []
            for fi, i in enumerate(info):
                if not i or part >= 1 << i["po"]:
                    continue
                n = (i["bs"] >> i["po"]) - (i["o"] if part == 0 else 0); p = pos[fi]
                k = _bits(data, base + p, i["pb"]); p += i["pb"]
                if k == (1 << i["pb"]) - 1:                          # escape: raw signed values
                    w = _bits(data, base + p, 5); p += 5
                    if w:
                        v = gather(p, w, n); p += n * w
                        i["parts"].append(np.where(v >> (w - 1), v - (1 << w), v))
                    else:
                        i["parts"].append(np.zeros(n, np.int64))
                    pos[fi] = p
                elif n > 0:
                    group.append((fi, p, k, n))
                else:
                    pos[fi] = p
            if not group:
                continue
            G = len(group); fis = [g[0] for g in group]
            s = np.array([g[1] for g in group], np.int64); k = np.array([g[2] for g in group], np.int64)
            N = np.array([g[3] for g in group], np.int64); maxN = int(N.max())
            T = np.empty((G, maxN), np.int64); cur = s.copy(); step = k + 1
            for j in range(maxN):                                    # unary part: find each code's 1 bit
                b = cur >> 3; r = TAB[buf[b], cur & 7]
                t = np.where(r < 8, cur - (cur & 7) + r, FB[b + 1])
                T[:, j] = t
                cur = np.minimum(t + step, LIM)
            last = T[np.arange(G), N - 1]
            if (last >= LIM).any():
                raise ValueError("FLAC residual runs past the frame")
            for r0 in range(0, G, 128):                              # q, low bits -> values (row chunks)
                Tr = T[r0:r0 + 128]; sr = step[r0:r0 + 128, None]
                q = np.empty_like(Tr); q[:, 0] = Tr[:, 0] - s[r0:r0 + 128]; q[:, 1:] = Tr[:, 1:] - Tr[:, :-1] - sr
                u = (q << k[r0:r0 + 128, None]) | read(np.minimum(Tr + 1, LIM), k[r0:r0 + 128, None])
                vals = (u >> 1) ^ -(u & 1)
                for gi in range(len(Tr)):
                    fi = fis[r0 + gi]; info[fi]["parts"].append(vals[gi, :N[r0 + gi]])
                    pos[fi] = int(last[r0 + gi] + step[r0 + gi])
        lpc = [fi for fi, i in enumerate(info) if i and "lpc" in i]
        if lpc:
            _lpc_restore(info, lpc, chans, c)
        for fi, i in enumerate(info):
            if not i or "lpc" in i:
                continue
            e = np.concatenate(i["parts"]) if i["parts"] else np.zeros(0, np.int64)
            o = i["o"]
            if o:
                wv = np.asarray(i["warm"], np.int64); D = e
                for lv in range(o, 0, -1):
                    D = np.cumsum(np.concatenate([[np.diff(wv[:lv], n=lv - 1)[-1]], D]))
                e = D
            chans[fi][c] = e << i["wasted"] if i["wasted"] else e
    out = []
    for fi, f in enumerate(frames):
        ch = chans[fi]; ca = f["ca"]
        if ca == 8:
            ch = [ch[0], ch[0] - ch[1]]
        elif ca == 9:
            ch = [ch[0] + ch[1], ch[1]]
        elif ca == 10:
            mid = (ch[0] << 1) | (ch[1] & 1); ch = [(mid + ch[1]) >> 1, (mid - ch[1]) >> 1]
        out.append(np.stack(ch, 1))
    return np.concatenate(out)


def _lpc_restore(info, which, chans, c):
    """LPC subframes of one channel, all frames of the batch at once: one vectorized step per sample index
    (s[n] = residual[n] + (sum coef[i] * s[n-1-i]) >> shift, exact integer maths; frames are independent)."""
    import numpy as np
    G = len(which); I = [info[fi] for fi in which]
    O = np.array([i["o"] for i in I], np.int64); W = int(O.max())
    BS = np.array([i["bs"] for i in I], np.int64); L = int(BS.max())
    S = np.zeros((G, W + L), np.int64); R = np.zeros((G, L), np.int64)
    C = np.zeros((G, W), np.int64)                                   # C[:, W-1-i] multiplies s[n-1-i]
    for g, i in enumerate(I):
        o = i["o"]; S[g, W:W + o] = i["warm"]
        e = np.concatenate(i["parts"]) if i["parts"] else np.zeros(0, np.int64)
        R[g, o:o + len(e)] = e
        C[g, W - o:] = i["lpc"][::-1]
    SH = np.array([i["shift"] for i in I], np.int64)
    for j in range(int(O.min()), L):
        pred = np.einsum("ij,ij->i", S[:, j:j + W], C) >> SH
        v = R[:, j] + pred
        S[:, W + j] = np.where(j >= O, v, S[:, W + j])
    for g, fi in enumerate(which):
        x = S[g, W:W + I[g]["bs"]]
        chans[fi][c] = x << I[g]["wasted"] if I[g]["wasted"] else x.copy()


def _flac_external(path):
    exe = shutil.which("flac") or shutil.which("ffmpeg")
    if not exe:
        raise ValueError("this FLAC uses a feature the built-in reader doesn't decode (33-bit samples or a negative "
                         "LPC shift) and no flac/ffmpeg is installed: review the WAV render instead (fl_render)")
    work = tempfile.mkdtemp(prefix="flh_review_")
    try:
        out = os.path.join(work, "x.wav")
        cmd = [exe, "-d", "-s", "-o", out, path] if os.path.basename(exe).lower().startswith("flac") else \
            [exe, "-v", "quiet", "-i", path, out]
        subprocess.run(cmd, check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import fl_audio_edit
        return fl_audio_edit.load_ex(out)[:3]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def load_audio(path):
    """-> (float32 (n, ch), rate, bits). WAV read block-wise (8..32-bit PCM, 32/64-bit float; NaN/inf samples are a
    clear error), FLAC decoded here. float32 halves the memory of long / high-rate renders."""
    import numpy as np
    if path.lower().endswith(".flac"):
        x, fs, bits = flac_load(path, np.float32)
        return x, fs, bits
    import fl_audio_edit as AE
    x, fs, bits, _ = AE.load_ex(path, np.float32, "raise")
    return x, fs, bits


def integrated_lufs(x, fs):
    """BS.1770 integrated loudness from the block-wise K-weighted 100 ms powers (400 ms blocks, 75 % overlap, both
    gates; mono sources once). Same maths as the server's meter (agrees within ~0.03 LU), bounded memory."""
    import numpy as np
    p = _p100(x, fs)
    if len(p) < 4:
        return None
    return _gated(np.convolve(p, np.ones(4) / 4, "valid"))


def stats2(L, R, block=1 << 20):
    """Block-wise (no full-length float64 temporaries): -> (corr, peak, rms) of a stereo pair."""
    import numpy as np
    n = len(L); sl = sr = sll = srr = slr = 0.0; pk = 0.0
    for a in range(0, n, block):
        l = np.asarray(L[a:a + block], dtype=np.float64); r = np.asarray(R[a:a + block], dtype=np.float64)
        sl += l.sum(); sr += r.sum(); sll += l @ l; srr += r @ r; slr += l @ r
        if len(l):
            pk = max(pk, float(np.abs(l).max()), float(np.abs(r).max()))
    if not n:
        return 1.0, 0.0, 0.0
    vl, vr = sll / n - (sl / n) ** 2, srr / n - (sr / n) ** 2
    cov = slr / n - (sl / n) * (sr / n)
    corr = float(cov / np.sqrt(vl * vr)) if vl > 1e-18 and vr > 1e-18 else 1.0
    return max(-1.0, min(1.0, corr)), pk, float(np.sqrt((sll + srr) / (2 * n)))


# ============================================================================= measuring
_KW = {}


def _kweighted(x, fs, sink=None):
    """K-weighted copy: zero-phase FFT filtering in 2^18-sample blocks with 0.5 s overlap (loudness only needs
    power, so phase doesn't matter). sink(a, y): called per block instead of building the full-length copy
    (bounded memory on long / high-rate audio)."""
    import numpy as np
    n = len(x); pad = int(0.5 * fs); nc = min(2, x.shape[1])
    out = np.empty((n, nc)) if sink is None else None
    nfft = 1 << 18 if n + 2 * pad > 1 << 18 else 1 << int(np.ceil(np.log2(n + 2 * pad)))
    while nfft - 2 * pad < nfft // 2:                                # very high rates: keep the core >= half a block
        nfft *= 2
    C = max(1, nfft - 2 * pad)
    g = _KW.get((nfft, fs))
    if g is None:
        g = _KW[(nfft, fs)] = np.sqrt(H["_k_weight"](np.fft.rfftfreq(nfft, 1 / fs), fs))[:, None]
    for a in range(0, n, C):
        lo, hi = max(0, a - pad), min(n, a + C + pad)
        seg = np.asarray(x[lo:hi, :nc], dtype=np.float64)
        y = np.fft.irfft(np.fft.rfft(seg, nfft, axis=0) * g, nfft, axis=0)
        k = min(C, n - a)
        if sink is None:
            out[a:a + k] = y[a - lo:a - lo + k]
        else:
            sink(a, y[a - lo:a - lo + k])
    return out


def _gated(mom):
    import numpy as np
    L = -0.691 + 10 * np.log10(np.maximum(mom, 1e-12)); keep = mom[L > -70]
    if not len(keep):
        return None
    rel = -0.691 + 10 * np.log10(keep.mean()) - 10
    k2 = keep[(-0.691 + 10 * np.log10(keep)) > rel]
    return float(-0.691 + 10 * np.log10(k2.mean())) if len(k2) else None


def _p100(x, fs):
    """Channel-summed K-weighted mean square per 100 ms (the building block of momentary / short-term loudness).
    Mono sources (dup_mono) count once, as BS.1770 says. Block-wise: no full-length float64 copy."""
    import numpy as np
    x = mono_src(x); hop = int(0.1 * fs); nb = len(x) // hop
    acc = np.zeros(nb + 1)
    if not nb:
        return np.zeros(0)

    def sink(a, y):
        e = np.einsum("ij,ij->i", y, y)
        idx = (a + np.arange(len(e))) // hop
        i0 = int(idx[0]); acc[i0:i0 + int(idx[-1]) - i0 + 1] += np.bincount(idx - i0, weights=e)
    _kweighted(x, fs, sink)
    return acc[:nb] / hop


def _tp(x, m=None):
    """4x-oversampled true peak (dBTP): the shared overlapped-frame detector (flh_finalize.true_peak_db), so review
    and the finals can never disagree. (The old 64-sample periodic-FFT version wrapped at the window edges and read up
    to ~1.5 dB high.)"""
    import numpy as np
    if not len(x):
        return -120.0
    try:
        import flh_finalize
        return float(flh_finalize.true_peak_db(x))
    except ImportError:
        return float(H["_true_peak"](np.asarray(x)))


_MD = []


def _mono_cls():
    if not _MD:
        import numpy as np

        class MonoDup(np.ndarray):
            """Two identical channels made from a MONO source. BS.1770 meters a mono file's one channel once (dual-mono
            playback is +3 dB): loudness code meters column 0 only (mono_src), stereo metrics see both columns."""
        _MD.append(MonoDup)
    return _MD[0]


def dup_mono(x):
    """(n,) or (n, 1) -> (n, 2) marked as mono (see MonoDup); anything else unchanged."""
    import numpy as np
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[1] != 1:
        return x
    return np.repeat(x, 2, 1).view(_mono_cls())


def is_mono(x):
    """True when x came from a mono source (dup_mono) and its two columns are still identical."""
    import numpy as np
    if getattr(x, "ndim", 0) == 1 or (getattr(x, "ndim", 0) == 2 and x.shape[1] == 1):
        return True
    return (isinstance(x, _mono_cls()) and x.ndim == 2 and x.shape[1] >= 2
            and np.array_equal(np.asarray(x[::4099, 0]), np.asarray(x[::4099, 1])))


def mono_src(x):
    """What a BS.1770 meter should see: the one channel of a mono source, else x itself (plain ndarray)."""
    import numpy as np
    if getattr(x, "ndim", 0) == 1:
        return np.asarray(x)[:, None]
    return np.asarray(x[:, :1]) if is_mono(x) else np.asarray(x)


def lufs(x, fs):
    """Integrated loudness with the server's meter, mono sources metered once (BS.1770)."""
    return H["_lufs"](mono_src(x), fs)


def _st2(x):
    if x.ndim == 1:
        x = x[:, None]
    return dup_mono(x) if x.shape[1] == 1 else x


def measure(x, fs, p100=None, spectrum=True):
    """Section stats: lufs (gated integrated), peak_st (max 3 s short-term), tp, corr, bands (dB re total), crest,
    tilt (dB/octave, 63 Hz-8 kHz). p100: the slice's precomputed 100 ms K-weighted powers (else computed)."""
    import numpy as np
    x = _st2(x)
    if p100 is None:
        p100 = _p100(x, fs)
    nb = len(p100)
    mom = np.convolve(p100, np.ones(4) / 4, "valid") if nb >= 4 else p100
    st = np.convolve(p100, np.ones(30) / 30, "valid") if nb >= 30 else (np.array([mom.mean()]) if len(mom) else mom)
    lufs = _gated(mom) if len(mom) else None
    pst = float(-0.691 + 10 * np.log10(max(float(st.max()), 1e-12))) if len(st) else None
    L, R = x[:, 0], x[:, 1]
    corr, pk, rms = stats2(L, R)
    bands, tilt = None, None
    if spectrum:
        f, Sp = H["_spectrum"](L, fs); Sp = Sp + H["_spectrum"](R, fs)[1]      # L+R power: polarity-proof
        tot = Sp[(f >= 20) & (f < 16000)].sum() + 1e-20
        bands = {nm: round(10 * np.log10(Sp[(f >= a) & (f < b)].sum() / tot + 1e-12), 1) for nm, a, b in BANDS_R}
        cs = [63, 125, 250, 500, 1000, 2000, 4000, 8000]
        lv = [10 * np.log10(Sp[(f >= c / 1.414) & (f < c * 1.414)].sum() + 1e-20) for c in cs]
        tilt = round(float(np.polyfit(np.log2(cs), lv, 1)[0]), 1)
    pk += 1e-12; rms += 1e-12
    clipped = 0
    for a in range(0, len(L), 1 << 20):
        clipped += int(np.count_nonzero((np.abs(L[a:a + (1 << 20)]) >= 0.9999) | (np.abs(R[a:a + (1 << 20)]) >= 0.9999)))
    return {"lufs": None if lufs is None else round(lufs, 1), "peak_st": None if pst is None else round(pst, 1),
            "tp": round(float(_tp(x)), 2), "corr": round(corr, 2), "bands": bands,
            "crest": round(20 * np.log10(pk / rms), 1), "tilt": tilt, "clipped": clipped}


# ============================================================================= sections and time
def bar_seconds(tempos, ppq, bpb):
    """bar (1-based, fractional) -> seconds, from a step tempo map [(tick, bpm)]."""
    tm = sorted(tempos) or [(0, 120.0)]
    if tm[0][0] > 0:
        tm.insert(0, (0, tm[0][1]))
    acc = [0.0]
    for (t0, b0), (t1, _) in zip(tm, tm[1:]):
        acc.append(acc[-1] + (t1 - t0) / ppq * 60.0 / b0)

    def f(bar):
        tick = (float(bar) - 1) * bpb * ppq; i = 0
        while i + 1 < len(tm) and tm[i + 1][0] <= tick:
            i += 1
        return acc[i] + (tick - tm[i][0]) / ppq * 60.0 / tm[i][1]
    return f


def _time_fn(project, jr):
    tm = jr.get("bar_times") or jr.get("time_map")
    if tm:
        pts = sorted((float(b), float(s)) for b, s in tm)

        def f(bar):
            bar = float(bar)
            if len(pts) == 1:
                return pts[0][1]
            for (b0, s0), (b1, s1) in zip(pts, pts[1:]):
                if bar <= b1 or (b1, s1) == pts[-1]:
                    return s0 + (bar - b0) * (s1 - s0) / ((b1 - b0) or 1)
        return f
    if not project:
        return None
    d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
    _, tempos = H["_song"](d, ev)
    bpb = jr.get("beats_per_bar")
    if not bpb:
        try:
            bpb = H["_qbar"](H["flp_project_info"](project))
        except Exception:
            bpb = 4
    return bar_seconds(tempos, P, float(bpb))


def _sections(project, jr, sections):
    """[{name, bar?, end_bar?, start_s?, end_s?, peak?, quiet?}] from the argument, the journal or the playlist."""
    secs = sections or jr.get("sections") or []
    if not secs and project:
        try:
            secs = S.sections_from_project(project)
        except Exception:
            secs = []
    out = []
    for s in secs:
        if isinstance(s, str):
            continue
        q = dict(s)
        lv = str(q.get("level", q.get("mark", ""))).lower()
        if lv in ("peak", "climax", "chorus", "drop", "high", "loud"):
            q["peak"] = True
        elif lv in ("quiet", "soft", "low", "calm"):
            q["quiet"] = True
        elif lv in ("build", "rise", "riser"):
            q["build"] = True
        out.append(q)
    if all("bar" in s for s in out):
        out.sort(key=lambda s: s["bar"])
        for i, s in enumerate(out):
            if not s.get("end_bar") and i + 1 < len(out):
                s["end_bar"] = out[i + 1]["bar"]
    return out


def _project_for_audio(audio, prof):
    stem = os.path.basename(audio).lower(); folder = os.path.basename(os.path.dirname(os.path.abspath(audio))).lower()
    for song, info in (prof.get("songs") or {}).items():
        if song.lower() in stem or song.lower() == folder:
            pr = (info or {}).get("project")
            if pr and os.path.isfile(pr):
                return pr, song
    return None, None


# ============================================================================= review
def fl_review(project=None, audio=None, sections=None, mode="auto", balance=False, final=None, offset_s="auto",
              background=None, as_reference=False):
    """Loudness / tone / stereo per section with ranked issues (arc, correlation, peaks, mud, harshness, dullness,
    collection loudness target for finals). audio mode: a WAV/FLAC render sliced at the journal's section bars
    (tempo map aware). live mode: the project open in FL, ~7 s heard per section with fl_listen (background job).
    Stores a compact table in the journal (journal.review)."""
    prof = _profile()
    if mode == "auto":
        mode = "audio" if audio else "live"
    if mode == "audio":
        if not audio:
            raise ValueError("audio mode needs audio= (a WAV or FLAC render)")
        if not project:
            project, _ = _project_for_audio(audio, prof)
        res = _review_audio(project, audio, sections, prof, final, offset_s)
        q = _queued(project)
        if q:                                                       # the render is what FL had: the real file (not
            res["pending_ops"] = q                                  # the queued state) matches it - say so
            res["pending_note"] = (f"{q} queued edit(s) aren't in this render (FL renders what it has; sections and "
                                   "timing were read from the matching saved file). flh_sync() sends them to FL; "
                                   "render again to review them")
        norm = res.pop("_tone_norm", None)
        if as_reference and norm:
            name = S.profile_name()
            if not name:
                raise ValueError("no collection profile to store the reference in (flh_profile)")
            song = (S.journal_get(project).get("song") if project else None) or os.path.basename(audio)
            S.profile_set(name, {"tone_reference": {**norm, "song": song, "source": audio}})
            res["tone_reference"] = f"saved {song} as the collection's tone reference"
        return res
    if mode != "live":
        raise ValueError("mode: auto | audio | live")
    if not H.get("IS_WINDOWS"):                                     # live listening drives FL: Windows only
        raise RuntimeError("fl_review's live mode listens to FL Studio, which fl-headless drives on Windows only (FL on "
                           "macOS isn't supported); here, pass audio= (a WAV/FLAC render) for an audio review")
    if not project:
        project, _ = H["_open_project_guess"]()
        if not project:
            raise ValueError("no project given and the one open in FL couldn't be identified")
    if not H["fl_running"]():
        raise RuntimeError("live mode needs the project open in FL; or render it and pass audio=")
    if H.get("_sync_before"):                                       # FL must hold queued edits before we listen
        H["_sync_before"]("fl_review")
    if background is False:
        return _review_live(project, sections, prof, balance)
    return H["_start_job"]("review", _review_live, project, sections, prof, balance)


def _queued(project):
    """Number of edits queued (lazy mode) for this project, 0 when none / unknown."""
    try:
        return len(H["_pending_get"](project)) if project and "_pending_get" in H and os.path.isfile(project) else 0
    except Exception:
        return 0


def _context(project, prof):
    jr = S.journal_get(project) if project else {}
    role = jr.get("role")
    if not role and project:
        song = jr.get("song")
        role = ((prof.get("songs") or {}).get(song) or {}).get("role")
    return jr, role


def _review_audio(project, audio, sections, prof, final, offset_s):
    import numpy as np
    t0 = time.time()
    jr, role = _context(project, prof)
    x, fs, _ = load_audio(audio)
    if x.ndim == 1:
        x = x[:, None]
    dur = len(x) / fs
    secs = _sections(project, jr, sections); tf = None; notes = []
    if any("start_s" not in s for s in secs):
        tf = _time_fn(project, jr)
        if tf is None:
            secs = []
            notes.append("no project / time map: sections can't be placed; whole file in 20 s windows")
    shift = 0.0
    if tf and offset_s == "auto" and project:
        shift = _auto_offset(project, x, fs, tf)
        if abs(shift) > 0.05:
            notes.append(f"audio starts {shift:.2f} s into the song (trimmed lead-in): sections shifted")
    elif isinstance(offset_s, (int, float)):
        shift = float(offset_s)
    spans = []
    for s in secs:
        if "start_s" not in s and ("bar" not in s or tf is None):
            continue
        a = float(s["start_s"]) if "start_s" in s else tf(s["bar"]) - shift
        b = float(s["end_s"]) if s.get("end_s") is not None else (tf(s["end_bar"]) - shift if s.get("end_bar") else dur)
        a, b = max(0.0, a), min(dur, b)
        if b - a >= 1.0:
            spans.append((s, a, b))
    if not spans:
        spans = [({"name": f"{int(t // 60)}:{int(t % 60):02d}"}, t, min(dur, t + 20)) for t in range(0, int(dur), 20)
                 if min(dur, t + 20) - t >= 1.0]
    x = _st2(x); P100 = _p100(x, fs); hop = int(0.1 * fs); rows = []
    for s, a, b in spans:
        i0, i1 = int(a * fs), int(b * fs)
        m = measure(x[i0:i1], fs, p100=P100[-(-i0 // hop):i1 // hop])
        m.update(name=s.get("name"), bar=s.get("bar"), peak=s.get("peak"), quiet=s.get("quiet"), build=s.get("build"),
                 start_s=round(a, 1))
        rows.append(m)
    whole = measure(x, fs, p100=P100, spectrum=False) if len(x) else {}
    if whole:
        tl = [r["tilt"] for r in rows if r.get("tilt") is not None]
        whole["tilt"] = round(float(np.median(tl)), 1) if tl else None
    if final is None:
        fpath = (jr.get("final") or {}).get("path")
        final = bool(fpath and os.path.normcase(os.path.abspath(fpath)) == os.path.normcase(os.path.abspath(audio))) \
            or "final" in os.path.basename(audio).lower()
    issues = _issues(rows, whole, role, prof, final, project, jr, live=False)
    t_ab = time.time()
    try:
        issues = rank(issues + abrupt_issues(x, fs, P100, [a for _, a, _ in spans], whole.get("lufs"), project, tf,
                                             shift, notes))
    except Exception as e:
        notes.append(f"abrupt-entrance check failed: {str(e)[:120]}")
    notes_ab = round(time.time() - t_ab, 2)
    res = _finish(project, rows, whole, issues, {"source": audio, "mode": "audio", "final": bool(final),
                                                  "role": role, "notes": notes, "took_s": round(time.time() - t0, 2),
                                                  "steps_s": {"abrupt": notes_ab}})
    if isinstance(res, dict):
        res["_tone_norm"] = _tone_norm([r for r in rows if r.get("lufs") is not None and r["lufs"] > -50])[1]
    return res


def _auto_offset(project, x, fs, tf):
    """Seconds of song cut from the start of the audio (audio_finish trims the lead-in)."""
    import numpy as np
    try:
        d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
        notes, _ = H["_song"](d, ev)
        if not notes:
            return 0.0
        first_tick = notes[0][0]
        try:
            q = float(H["_qbar"](H["flp_project_info"](project)))
        except Exception:
            q = 4.0
        note_s = tf(1 + first_tick / (q * P))
        loud = np.abs(x).max(1); hit = np.flatnonzero(loud > 10 ** (-50 / 20))
        if not len(hit):
            return 0.0
        onset = hit[0] / fs
        return max(0.0, note_s - onset) if note_s - onset > 0.25 else 0.0
    except Exception:
        return 0.0


def _ratios(r):
    b = r["bands"]
    return {"mud": b["lowmid"] - b["mid"], "harsh": b["presence"] - b["mid"], "dull": b["air"] - b["presence"]}


def _tone_norm(usable):
    """(loud sections, median band ratios over them) - the song's tone fingerprint."""
    import numpy as np
    usable = [r for r in usable if r.get("bands")]
    if not usable:
        return [], {}
    loudest = max(r["lufs"] for r in usable)
    judged = [r for r in usable if r["lufs"] >= loudest - 8]
    if len(judged) < 3:
        judged = sorted(usable, key=lambda r: -r["lufs"])[:3]
    return judged, {k: round(float(np.median([_ratios(r)[k] for r in judged])), 2) for k in ("mud", "harsh", "dull")}


def _issues(rows, whole, role, prof, final, project, jr, live):
    import numpy as np
    out = list(arc_check(rows, role, arc_style(jr, prof)))
    widener = None; quiet = []
    if project:
        try:
            dr = fl_doctor(project, live=live)
            widener = next((i for i in dr["issues"] if i["kind"] == "master_widener"), None)
            quiet = [i for i in dr["issues"] if i["kind"] in ("silent_knob", "content_missing") and
                     i["severity"] in ("high", "med")]
        except Exception:
            pass
    out += quiet                                  # a channel that can't be heard: say which and why (fl_doctor's find)
    for r in rows:
        w = _sec_where(r)
        if r.get("lufs") is None or r["lufs"] < -50:
            out.append(_iss("med", "silent_section", w, f"{r['name']} is nearly silent ({r.get('lufs')} LUFS)",
                            ("practically silent channel(s): " + "; ".join(f"{q['where']}: {q['what'][:80]}"
                                                                          for q in quiet[:2]) if quiet else
                             "check for a silent instrument / muted track there: fl_doctor")))
            continue
        if r["corr"] < 0.05:                    # wide reverbs / pads sit ~0.1-0.4; below 0 is phase trouble (always high)
            fix = (f"{widener['what'].split(':')[0]} - {widener['fix']}" if widener else
                   "find the widener / out-of-phase layer: fl_doctor (master chain), fl_mix_check per track; "
                   "narrow it (fl_plugin_set) or remove it (flp_remove_effect)")
            out.append(_iss("high" if r["corr"] < 0 else "low", "correlation", w,
                            f"L/R correlation {r['corr']:.2f} in {r['name']}: phasey, collapses in mono", fix))
        if r.get("clipped", 0) > 2 or r["tp"] > 0.0:
            out.append(_iss("high", "clipping", w, f"clipping in {r['name']} (true peak {r['tp']:+.1f} dBTP, "
                            f"{r.get('clipped', 0)} samples at full scale)",
                            "lower the master / loudest tracks (fl_mixer_set volume_db) or audio_finish (caps peaks)"))
    usable = [r for r in rows if r.get("lufs") is not None and r["lufs"] > -50]
    if len(usable) >= 3:
        # tone: one band against its neighbour, loud sections only (quiet passages: barely audible; loud playing is
        # naturally brighter). Per section: outliers against the song's own loud sections (low-priority hints - the
        # arrangement legitimately changes tone). Whole song: one consistency note against the collection's
        # reference song (songs differ in register and instrument, so never per section).
        judged, med = _tone_norm(usable)
        for r in judged:
            w = _sec_where(r); q = _ratios(r)
            if q["mud"] - med["mud"] > 6:
                out.append(_iss("low", "mud", w, f"{r['name']} is muddier than the song's other loud sections "
                                f"(200-500 Hz {q['mud'] - med['mud']:+.1f} dB)", "cut 200-500 Hz 2-4 dB on the layers "
                                "playing there (fl_mix_check proposes the EQ; fl_mix_apply), or thin the low-mid parts (bass, "
                                "pads, low chords) there"))
            if q["harsh"] - med["harsh"] > 6:
                out.append(_iss("low", "harsh", w, f"{r['name']} is harsher than the song's other loud sections "
                                f"(2-5 kHz {q['harsh'] - med['harsh']:+.1f} dB)", "dip 2-5 kHz 2-3 dB on the brightest "
                                "layer (fl_mix_check / fl_mix_apply) or lower its velocities"))
            if med["dull"] - q["dull"] > 7:
                out.append(_iss("low", "dull", w, f"{r['name']} is duller than the song's other loud sections "
                                f"(>5 kHz {q['dull'] - med['dull']:+.1f} dB)", "a gentle high shelf on the lead / melody track "
                                "(fl_mix_check proposes it), or less low layering there"))
        ref = prof.get("tone_reference") or {}
        if med and all(k in ref for k in ("mud", "harsh", "dull")) and not live:
            d = {k: med[k] - ref[k] for k in ("mud", "harsh", "dull")}
            words = [w for w, on in (("muddier", d["mud"] > 6), ("harsher", d["harsh"] > 6),
                                     ("darker", d["dull"] < -7), ("brighter", d["dull"] > 7)) if on]
            if words:
                out.append(_iss("low", "consistency", "whole song", f"{' and '.join(words)} than the collection "
                                f"reference {ref.get('song')} (200-500 Hz {d['mud']:+.1f}, 2-5 kHz {d['harsh']:+.1f}, "
                                f">5 kHz {d['dull']:+.1f} dB vs mids/presence) - fine if intended (register, instrumentation, genre)",
                                "if they should match: a gentle master EQ (flp_mix / fl_mix_apply), else ignore"))
    if whole and not live:
        tgt = S.song_target(prof, role)
        if final:
            L = whole.get("lufs")
            has_target = bool(prof.get("finish") or prof.get("finish_by_role"))
            if L is not None and abs(L - tgt["lufs"]) > 0.5:
                if has_target:
                    sev = "high" if abs(L - tgt["lufs"]) > 1 else "med"
                    what = f"final is {L:.1f} LUFS, the collection target for a {role or 'track'} is {tgt['lufs']:.1f}"
                else:                                         # no profile: only a reference point, not a rule
                    sev = "low"
                    what = (f"final is {L:.1f} LUFS; the default streaming reference is {tgt['lufs']:.1f} (set your own "
                            "target: flh_profile set={finish: {lufs: ...}})")
                out.append(_iss(sev, "loudness_target", "whole song", what, "fl_finalize (renders and hits the "
                                "target) or " + _call("audio_finish", {"input": C.Raw("the WAV render"),
                                                                       "output": C.Raw("output"),
                                                                       "target_lufs": tgt["lufs"],
                                                                       "max_true_peak": tgt["true_peak"]})))
            if whole["tp"] > tgt["true_peak"] + 0.05:
                out.append(_iss("high", "true_peak", "whole song", f"true peak {whole['tp']:+.2f} dBTP > "
                                f"{tgt['true_peak']:+.1f} limit for finals",
                                _call("audio_finish", {"input": C.Raw("the WAV render"), "output": C.Raw("output"),
                                                       "max_true_peak": tgt["true_peak"]})))
        elif whole.get("tp", -99) > 0 and not any(i["kind"] == "clipping" for i in out):
            out.append(_iss("high", "clipping", "whole song", f"true peak {whole['tp']:+.2f} dBTP (overs)",
                            "lower the master or let audio_finish cap peaks"))
        if whole.get("corr", 1) < 0.1 and not any(i["kind"] == "correlation" for i in out):
            out.append(_iss("high" if whole["corr"] < 0 else "low",
                            "correlation", "whole song",
                            f"L/R correlation {whole['corr']:.2f} for the whole song",
                            widener["fix"] if widener else "fl_doctor (master widener?) / fl_mix_check"))
    return rank(out)


def _finish(project, rows, whole, issues, meta):
    table = [{"section": r["name"], "lufs": r["lufs"], "peak_st": r["peak_st"], "corr": r["corr"], "tilt": r["tilt"]}
             for r in rows]
    ok = [r["lufs"] for r in rows if r.get("lufs") is not None and r["lufs"] > -60]
    song = {k: whole.get(k) for k in ("lufs", "tp", "corr", "tilt")} if whole else {}
    if ok:
        song["range_lu"] = round(max(ok) - min(ok), 1)
    res = {"issues": issues[:10], "more": max(0, len(issues) - 10),
           "verdict": "fix the high ones first" if any(i["severity"] == "high" for i in issues) else
           ("minor issues only" if issues else "no problems found"),
           "sections": table, "song": song}
    res.update({k: v for k, v in meta.items() if v not in (None, [], "")})
    if project:
        try:
            S.journal_update(project, set={"review": {"when": time.strftime("%Y-%m-%d %H:%M"), "mode": meta.get("mode"),
                                                      "source": meta.get("source"), "sections": table, "song": song,
                                                      "issues": len(issues),
                                                      "top": [i["what"] for i in issues[:3]]}})
            res["journal"] = "review stored in the journal"
        except Exception as e:
            res["journal"] = f"not stored: {e}"
    return res


def _review_live(project, sections, prof, balance):
    t0 = time.time()
    prog = H.get("_job_progress", lambda **k: None)
    jr, role = _context(project, prof)
    secs = [s for s in _sections(project, jr, sections) if "bar" in s]
    if not secs:
        raise ValueError("no sections with bars (flh_journal(project, seed=true) fills them from the playlist)")
    try:
        tf = _time_fn(project, jr)
    except Exception:
        tf = None
    tmp = os.path.join(os.path.dirname(os.path.abspath(project)), "_flh_review_listen.flac")
    H["writable"](tmp)
    rows = []; notes = []
    try:
        for i, s in enumerate(secs):
            prog(section=s["name"], pct=int(100 * i / (len(secs) + (1 if balance else 0))))
            length = (s["end_bar"] - s["bar"]) if s.get("end_bar") else 8
            start = s["bar"] + (1 if length >= 3 else 0)
            win = 7.0
            if tf and s.get("end_bar"):
                win = max(2.0, min(7.0, tf(s["end_bar"]) - tf(start)))
            r = H["fl_listen"](start_bar=start, seconds=win, save=tmp)
            if not r.get("heard"):
                notes.append(f"{s['name']}: nothing heard ({r.get('why', '')[:80]})"); continue
            m = None
            if os.path.isfile(tmp):
                try:
                    x, fs, _ = load_audio(tmp); m = measure(x, fs)
                except Exception as e:
                    notes.append(f"{s['name']}: couldn't read the recording ({e}); used fl_listen's numbers")
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            if m is None:
                w = r.get("whole") or {}
                b = w.get("bands_db") or {}
                m = {"lufs": w.get("lufs"), "peak_st": w.get("lufs"), "tp": w.get("true_peak_dbfs", w.get("peak_dbfs", -99)),
                     "corr": w.get("lr_correlation", 1.0), "crest": w.get("crest_db"), "tilt": None, "clipped": 0,
                     "bands": {"low": b.get("bass", 0), "lowmid": b.get("low_mid", 0), "mid": b.get("mid", 0),
                               "presence": b.get("presence", 0), "air": b.get("air", 0)}}
            m.update(name=s["name"], bar=s["bar"], peak=s.get("peak"), quiet=s.get("quiet"), build=s.get("build"))
            rows.append(m)
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    issues = _issues(rows, {}, role, prof, False, project, jr, live=True)
    try:
        issues = rank(issues + abrupt_issues(None, None, None, [], None, project, None, 0.0, notes))
    except Exception as e:
        notes.append(f"abrupt-entrance check failed: {str(e)[:120]}")
    if balance and rows:
        prog(section="balance", pct=90)
        issues = rank(issues + _balance(rows, jr, notes))
    return _finish(project, rows, {}, issues, {"source": "live (fl_listen)", "mode": "live", "role": role,
                                                "notes": notes, "took_s": round(time.time() - t0, 1)})


# ============================================================================= abrupt entrances
AB_DEFAULT = {"jump_lu": 8.0, "hold_lu": 5.0, "band_db": 12.0, "band_hold_db": 9.0, "section_guard_s": 0.6,  # PC-VERIFY: no noise on drum-heavy renders
              # a struck / plucked attack (piano, harp, mallets, guitar...) out of a dying tail jumps by nature: not
              # flagged when the level falls >= struck_decay_db from the attack's peak within ~1.3 s, never swells
              # back over it, and the second before it fell >= tail_fall_db from its loudest to its end
              "struck_decay_db": 4.0, "tail_fall_db": 2.0}
AB_BANDS = [("low", 20, 250), ("mid", 250, 2000), ("high", 2000, 8000)]


def _ab_cfg():
    cfg = dict(AB_DEFAULT)
    try:
        cfg.update({k: v for k, v in (H["score_rules"]()["abrupt_audio"] or {}).items() if not k.startswith("_")})
    except Exception:
        pass
    return cfg


def struck_entry(p100, t, cfg=None):
    """Is the jump at t s a struck / plucked attack out of a decaying tail (a piano chord after the last one died
    away, a rolled chord in a prelude) rather than a layer slamming in? From the level alone (any instrument): after
    the attack's peak (first 0.4 s) the level falls >= struck_decay_db by 0.8-1.3 s and never swells back over the
    peak, and the second before it fell >= tail_fall_db from its loudest to its last 0.3 s (or was near-silent).
    PC-VERIFY: a slow solo-piano prelude render (rolled chords after quiet single lines) gives no abrupt_entrance;
    strings / a pad slamming in mid-section still does (checklists/review.md R4)."""
    import numpy as np
    cfg = cfg or _ab_cfg()
    p = np.asarray(p100, float); b = int(round(t / 0.1))
    if b < 3 or b + 13 > len(p):
        return False
    dB = lambda v: 10 * np.log10(max(float(v), 1e-20))
    peak = float(p[b:b + 4].max())
    late = float(p[b + 8:b + 13].mean())
    after = p[b + 4:b + 13]
    decays = dB(late) <= dB(peak) - float(cfg["struck_decay_db"]) and float(after.max()) <= peak * 10 ** 0.1
    pre = p[max(0, b - 10):b]
    tail = dB(pre.max()) - dB(pre[-3:].mean()) >= float(cfg["tail_fall_db"]) or dB(pre.mean()) <= dB(peak) - 30
    return bool(decays and tail)


def abrupt_events(x, fs, p100, starts_s, song_lufs, cfg=None, skipped=None):
    """Sudden sustained level jumps in a render: [(t_s, jump_dB, 'loudness' | band name, held_dB)].
    Loudness: the next 0.3 s is >= jump_lu above the 0.5 s before and the next 1 s stays >= hold_lu above it.
    Bands (low / mid / high, 100 ms FFT blocks): the same with band_db / band_hold_db, when that band is a real
    part of the sound afterwards (within 15 dB of the total). Not within section_guard_s of a section start (an
    entrance is expected there), not in the first second, not out of near-silence (a stop / restart), not a struck
    attack out of a decaying tail (struck_entry: those go to `skipped`, a list the caller may pass)."""
    import numpy as np
    cfg = cfg or _ab_cfg()
    p = np.asarray(p100, float); nb = len(p)
    if nb < 20:
        return []
    Lf = lambda v: -0.691 + 10 * np.log10(np.maximum(v, 1e-12))
    c = np.concatenate([[0.0], np.cumsum(p)])
    i = np.arange(5, nb - 10)
    prev, fast, hold = (c[i] - c[i - 5]) / 5, (c[i + 3] - c[i]) / 3, (c[i + 10] - c[i]) / 10
    ref = song_lufs if song_lufs is not None else float(Lf(p.mean()))
    ok = (Lf(fast) >= ref - 12) & (Lf(prev) >= ref - 30)
    jump, held = Lf(fast) - Lf(prev), Lf(hold) - Lf(prev)

    def onset(series, k, pv, fa):                                  # first block past the geometric middle
        thr = (pv * fa) ** 0.5
        for q in range(max(0, i[k] - 1), min(len(series), i[k] + 4)):
            if series[q] >= thr:
                return q * 0.1
        return i[k] * 0.1
    cand = [(onset(p, k, prev[k], fast[k]), float(jump[k]), "loudness", float(held[k])) for k in
            np.flatnonzero(ok & (jump >= cfg["jump_lu"]) & (held >= cfg["hold_lu"]))]
    if x is not None and fs:
        hop = int(0.1 * fs); mono = x.mean(1) if x.ndim > 1 else x
        f = np.fft.rfftfreq(hop, 1 / fs); bp = np.zeros((nb, len(AB_BANDS))); tot = np.zeros(nb)
        win = np.hanning(hop)
        for a in range(0, nb, 256):                                # chunked: bounded memory on long songs
            b = min(nb, a + 256)
            blk = mono[a * hop:b * hop].reshape(b - a, hop) * win
            S2 = np.abs(np.fft.rfft(blk, axis=1)) ** 2
            tot[a:b] = S2[:, (f >= 20) & (f < 16000)].sum(1)
            for j, (_, lo, hi) in enumerate(AB_BANDS):
                bp[a:b, j] = S2[:, (f >= lo) & (f < hi)].sum(1)
        cb = np.vstack([np.zeros((1, len(AB_BANDS))), np.cumsum(bp, 0)]); ct = np.concatenate([[0.0], np.cumsum(tot)])
        dB = lambda v: 10 * np.log10(np.maximum(v, 1e-20))
        for j, (name, _, _) in enumerate(AB_BANDS):
            pv, fa, ho = (cb[i, j] - cb[i - 5, j]) / 5, (cb[i + 3, j] - cb[i, j]) / 3, (cb[i + 10, j] - cb[i, j]) / 10
            share = dB(ho) - dB((ct[i + 10] - ct[i]) / 10)
            bj, bh = dB(fa) - dB(pv), dB(ho) - dB(pv)
            for k in np.flatnonzero(ok & (bj >= cfg["band_db"]) & (bh >= cfg["band_hold_db"]) & (share >= -15)):
                cand.append((onset(bp[:, j], k, pv[k], fa[k]), float(bj[k]), name, float(bh[k])))
    guard = float(cfg["section_guard_s"]); out = []
    for t, j, kind, h in sorted(cand, key=lambda e: (e[0], -e[1])):
        if t < 1.0 or any(abs(t - s) <= guard for s in starts_s):
            continue
        if out and t - out[-1][0] < 1.5:                           # one event per jump: loudness, else the biggest band
            if (kind == "loudness" and out[-1][2] != "loudness") or (kind == out[-1][2] == "loudness" and j > out[-1][1]) \
                    or (out[-1][2] != "loudness" and j > out[-1][1]):
                out[-1] = (min(t, out[-1][0]), j, kind, h)
            continue
        out.append((t, j, kind, h))
    keep = []
    for e in out:
        if struck_entry(p, e[0], cfg):                              # a struck chord out of a dying tail: by nature
            if skipped is not None:
                skipped.append(e)
            continue
        keep.append(e)
    return keep


def _bar_at(tf, t, hi_bar):
    lo, hi = 1.0, float(hi_bar)
    for _ in range(40):
        mid = (lo + hi) / 2
        if tf(mid) < t:
            lo = mid
        else:
            hi = mid
    return lo


def abrupt_issues(x, fs, p100, starts_s, song_lufs, project, tf, shift, notes):
    """abrupt_entrance issues: audio jumps (render) named after the layer that enters there (flh_score's symbolic
    entrances), plus symbolic entrances that start much louder than they continue with no ramp."""
    ent = []
    fn = H.get("score_entrances")
    if project and callable(fn):
        try:
            ent = fn(project, all=True)
        except Exception as e:
            notes.append(f"symbolic entrance check skipped: {str(e)[:100]}")
    struck = []
    ev = abrupt_events(x, fs, p100, starts_s, song_lufs, skipped=struck) if p100 is not None else []
    out, used = [], set()
    bar_of = None
    if tf and ent:
        hi_bar = max(e["bar"] for e in ent) + 64
        bar_of = lambda t: _bar_at(tf, t + shift, hi_bar)
    if struck:                                                     # the notes' entrance at a struck attack: not
        if tf and ent:                                             # an abrupt layer either
            for t, *_ in struck:
                for k, e in enumerate(ent):
                    if abs(tf(e["bar"]) - shift - t) <= 0.4:
                        used.add(k)
        notes.append(f"{len(struck)} jump(s) not flagged: struck attacks out of a decaying tail (a piano / plucked "
                     "chord is meant to jump) - at " + ", ".join(f"{int(t // 60)}:{t % 60:04.1f}" for t, *_ in
                                                                 struck[:6]))
    for t, j, kind, h in ev[:8]:
        who = []
        if bar_of:
            b = bar_of(t)
            for k, e in enumerate(ent):
                if abs(tf(e["bar"]) - shift - t) <= 0.4:
                    who.append(e); used.add(k)
        where = f"{int(t // 60)}:{t % 60:04.1f}" + (f" (bar {bar_of(t):.1f})" if bar_of else "")
        band = "" if kind == "loudness" else f" in the {kind} band"
        what = f"level jumps +{j:.1f} dB{band} within ~0.3 s and stays up (+{h:.1f} dB over 1 s), mid-section"
        if who:
            w = max(who, key=lambda e: e["first_vel"] - e["next_vel"])
            what += f": {w['part']} enters there (v{w['first_vel']}, then v{w['next_vel']}" +                 (", no ramp)" if not w["ramp"] else ")")
            fix = w["fix"]
        else:
            fix = ("find the layer that comes in there (flp_score bars=... shows who plays) and ramp it in: velocity "
                   "ramp (flp_edit_notes op velocity ramp:[from,to]) or a volume fade-in (flp_add_automation)")
        sev = "med" if j >= _ab_cfg()["jump_lu"] + 4 or (who and any(e["flagged"] for e in who)) else "low"
        out.append(_iss(sev, "abrupt_entrance", where, what, fix + "; confirm by listening"))
    for k, e in enumerate(ent):
        if k in used or not e["flagged"]:
            continue
        heard = p100 is not None and len(starts_s) > 0
        sev = "low" if heard else (e["severity"] or "low")          # the render showed no jump there
        out.append(_iss(sev, "abrupt_entrance", f"bar {e['bar']:g} {e['part']}" + (f" ({e['section']})" if e["section"] else ""),
                        f"{e['why']}, no velocity ramp" + (" (the render shows no loudness jump there; other layers "
                                                            "may mask it)" if heard else ""), e["fix"]))
    return out[:6]


MELODY_ROLES = ("melody", "lead", "vocal", "vox", "topline", "hook", "main", "solo")
MELODY_FALLBACK = ("piano", "keys", "star")                     # solo-instrument songs: used when nothing else matches


def _balance(rows, jr, notes):
    """Melody vs support layers in the loudest section (fl_mix_check; slow, opt-in)."""
    chans = jr.get("channels") or {}
    has_mixer = [c for c in chans.values() if isinstance(c, dict) and c.get("mixer") is not None]
    mel = []
    for roles in (None, MELODY_ROLES, MELODY_FALLBACK):        # explicit melody: true, then role words
        mel = sorted({int(c["mixer"]) for c in has_mixer if (c.get("melody") is True if roles is None else
                      any(r in str(c.get("role", "")).lower() for r in roles))})
        if mel:
            break
    lay = sorted({int(c["mixer"]) for c in has_mixer} - set(mel))
    if not mel or not lay:
        notes.append("balance: the journal needs channel roles with mixer tracks (melody + layers)")
        return []
    top = max((r for r in rows if r.get("lufs") is not None), key=lambda r: r["lufs"], default=None)
    if top is None:
        return []
    res = H["fl_mix_check"](start_bar=top["bar"] + 1, seconds=8, tracks=mel + lay, window_s=8)
    per = res.get("tracks") or {}
    try:
        names = {m["index"]: m["name"] for m in H["fl_state"]()["mixer"]}
    except Exception:
        names = {}
    get = lambda t: per.get(names.get(t, t)) or per.get(str(t)) or per.get(t)
    mvals = [get(t)["lufs"] for t in mel if get(t) and get(t).get("lufs") is not None]
    if not mvals:
        notes.append("balance: the melody track wasn't heard in fl_mix_check"); return []
    ml = max(mvals); out = []
    role_of = {int(c["mixer"]): c.get("role") or c.get("name") for c in has_mixer}
    dense = arc_style(jr, _profile()).get("class") == "dense"    # drums / bass legitimately sit near the lead there
    near, high_at, below = (-1.0, 3.0, 2) if dense else (-3.0, 0.0, 6)
    for t in lay:
        m = get(t)
        if not m or m.get("lufs") is None:
            continue
        d = m["lufs"] - ml
        if d > near:
            out.append(_iss("high" if d >= high_at else "med", "balance", f"{_sec_where(top)} mixer track {t} "
                            f"({role_of.get(t)})", f"{role_of.get(t)} is {'louder than' if d >= 0 else 'too close to'} "
                            f"the lead / melody ({d:+.1f} LU); support layers usually sit {below}+ LU below",
                            _call("fl_mixer_set", {"track": t, "volume_db": C.Raw(f"{d + below:.0f} dB lower")}) +
                            " (or flp_mixer offline)"))
    return out


# ============================================================================= registration
def register(ns):
    global H
    H = ns
    P, Sch = ns["_p"], ns["_schema"]
    try:                                       # fl_review syncs queued edits itself, only in live mode (listening);
        ns["SYNC_FIRST_TOOLS"].discard("fl_review")   # audio mode reads a render + files: no FL save/reload for it
    except (KeyError, AttributeError):
        pass
    ns["TOOLS"]["fl_doctor"] = (
        fl_doctor, "Static check of a .flp in under a second, no FL needed: trial / not-owned plugins, Spitfire/BBC/"
        "LABS instruments listening on one MIDI channel (silent), practically silent channels (the sound's content "
        "isn't on this PC, or a volume / gain / dynamics / expression knob at 0 - by the plugin's own parameter "
        "names; live=true also reads them from the open FL), unused / muted channels, routing to missing or muted "
        "mixer tracks, automation targets, empty patterns on the playlist, missing samples, master stereo widener, "
        "dead tail. Ranked issues with the tool to fix each. Run it before every render.",
        Sch(project=ns["_FLP"], live=ns["_BOOL"]("also read every playing channel's level knobs from the open FL "
                                                 "(one bridge call per channel; default false)", True)))
    ns["TOOLS"]["fl_review"] = (
        fl_review, "Review a song per section: loudness (LUFS, max short-term), true peak, L/R correlation, tone "
        "balance, crest; ranked issues: loudness arc (peak sections - journal peak:true, else chorus / drop / climax "
        "by name - must be loudest, quiet ones (intro / breakdown / outro) quiet; range and build rules follow the "
        "song's genre/style from the journal or profile: pop/EDM ~3-8 LU, orchestral/piano ~8-14 LU), phase, "
        "clipping, mud/harsh/dull, collection loudness target for finals. "
        "audio=<WAV/FLAC render> (fast, preferred) or live from the open FL (background job, fl_job for the result). "
        "Stores the table in the song's journal.",
        Sch(project=P("string", "the .flp (sections + tempo map come from it and its journal)", True),
            audio=ns["_STR"]("WAV/FLAC render or final to analyse", True),
            sections=P("array", "override: [{name, bar, end_bar} | {name, start_s, end_s}]", True,
                       items={"type": "object"}),
            mode=ns["_STR"]("auto (audio if given, else live) | audio | live", True),
            balance=ns["_BOOL"]("live: also check melody vs layers in the loudest section with fl_mix_check (slow)",
                                True),
            as_reference=ns["_BOOL"]("audio: store this song's tone as the collection reference (a song the user "
                                     "approved); later reviews judge mud/harshness against it", True),
            final=ns["_BOOL"]("treat as a final (check the collection loudness target); default: auto", True),
            offset_s=P(["number", "string"], "seconds of song cut from the audio's start; default auto", True),
            background=ns["_BOOL"]("live: run as a background job (default true)", True)))
