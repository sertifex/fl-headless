"""fl-headless catalog, A/B and watchdog.

    fl_sound_catalog(query, kind, refresh, type, genre, bpm, key)
                                             one cached index of every sound the user has (LABS / INSTRUMENT packs,
                                             BBC SO Discover, FL generators from the plugin database, FLEX packs,
                                             saved sounds) + the samples on this PC, each with how to load it and
                                             whether it's owned
    fl_ab(project, version, back, label)     hear another version of a song in the running FL and switch back
    fl_watchdog(heal)                        one fast health check of FL + bridge (+ safe fixes)
Helpers for other modules (also in the server namespace): sound_ok(plugin_name) -> True / "trial" / "unknown",
ownership(plugin_name) -> {status, why}, fl_edition() -> (key, source), on_tool_error(tool, exc) -> hint.
Ownership order: collection profile owned/trial lists > free plugins > FL edition table (NATIVE_MIN) > third-party
plugins in FL's plugin database > "unknown".

Source roots (env overrides, used by the tests):
    FLH_SPLICE_LIB   Splice INSTRUMENT library  (default ~/Splice/INSTRUMENT; same as flp_instrument_sounds)
    FLH_PLUGIN_DB    FL's "Plugin database" folder (default <FL user data>/Presets/Plugin database)
    FLH_FL_USERDATA  FL's user data folder (Documents/Image-Line/FL Studio), as the main server uses it
    FLH_FLEX_DIR     FLEX preset library (default: searched under Documents/Image-Line)
    FLH_FACTORY_PACKS  FL's factory sample packs (default <FL install>/Data/Patches/Packs)

Samples (fl_sound_catalog kind=kick|808|snare|clap|rim|hat|openhat|ride|crash|perc|shaker|tom|fx|texture|vocal|
loop|oneshot, or type="sample"): an index of the samples on this PC, referenced in place (never copied):
FL's factory packs, the user data folder's Packs and Audio (not Recorded/Rendered), the browser's extra search
folders from FL's registry settings when readable (PC-VERIFY), and the profile's sample_folders. Each sample gets a
role and character tags from its file/folder names (several languages), and - lazily, for the candidates a query
looks at - from quick audio features (duration, attack, decay, spectral centroid, roll-off, sub energy, noisiness,
pitch for tonal one-shots/808s), cached in the state folder (samples.json, sample_features.json; size-capped).
Loops get BPM/key from their names (or a BPM guess from their length, marked as a guess).
Helper for other modules: sample_search(role, query, genre, bpm, key, limit, exclude) -> ranked list.
"""
import contextlib
import fnmatch
import glob
import functools
import json
import math
import os
import re
import shutil
import struct
import sys
import threading
import time

import flh_store

H = {}


def _hint(tool, args=None):
    """tool(...) when the client lists it, else flh_call("tool", {...}) (see flh_score.call_hint)."""
    import flh_score
    return flh_score.call_hint(tool, args, H)


def _raw(text):
    import flh_score
    return flh_score.Raw(text)
CACHE_VERSION = 6                                # 3: FL's own multisampled instruments (Packs\Instruments)
                                                 # 5: BBC sections only when BBC SO Discover is installed
                                                 # 6: FLEX presets carry flh_flex's kind (one kind everywhere)
MAX_AGE_S = 7 * 86400
FLEX_MAX_FILES = 4000
FLEX_EXTS = (".fst", ".flexpreset", ".nmsv", ".flex", ".flex2pack", ".flexpack")

# ----------------------------------------------------------------------------- ownership
# Which native FL plugins each edition includes (FL 20.8 .. 2026). Everything not included runs as a demo
# ("trial": a project saved with it shows a demo warning / goes silent or noisy). Minimum edition per plugin;
# entries marked "verify" are from memory of Image-Line's edition chart and may differ by FL version.
# FL Cloud's plugin subscription unlocks the All Plugins set (verify).
EDITIONS = ("fruity", "producer", "signature", "all_plugins")
EDITION_NAMES = {"fruity": "Fruity Edition", "producer": "Producer Edition", "signature": "Signature Bundle",
                 "all_plugins": "All Plugins Edition"}
EDITION_ALIASES = {"fruity": "fruity", "fruity edition": "fruity", "fe": "fruity", "producer": "producer",
                   "producer edition": "producer", "pe": "producer", "signature": "signature",
                   "signature bundle": "signature", "signature edition": "signature", "sig": "signature",
                   "all plugins": "all_plugins", "all plugins edition": "all_plugins", "all plugins bundle": "all_plugins",
                   "all_plugins": "all_plugins", "ape": "all_plugins", "apb": "all_plugins",
                   "fl cloud plugins": "all_plugins", "cloud": "all_plugins"}
NATIVE_MIN = {
    # generators in every edition
    "3x Osc": "fruity", "Autogun": "fruity", "BassDrum": "fruity", "BeepMap": "fruity", "BooBass": "fruity",
    "Channel sampler": "fruity", "Audio clip": "fruity", "Automation clip": "fruity", "Dashboard": "fruity",
    "FL Keys": "fruity", "FPC": "fruity", "Fruit Kick": "fruity", "Fruity Kick": "fruity",
    "Fruity DrumSynth Live": "fruity", "Fruity DX10": "fruity", "Fruity Granulizer": "fruity",
    "Fruity Slicer": "fruity", "Fruity Envelope Controller": "fruity", "Fruity Keyboard Controller": "fruity",
    "Layer": "fruity", "MIDI Out": "fruity", "Patcher": "fruity", "Plucked!": "fruity", "SimSynth": "fruity",
    "Soundfont Player": "fruity", "Fruity Soundfont Player": "fruity", "Wave Traveller": "fruity",
    "FLEX": "fruity",                                   # free for everyone, packs included
    "MiniSynth": "fruity",                              # verify
    "FL Slayer": "fruity",                              # verify
    "Groove Machine Synth": "fruity",                   # verify
    # Producer Edition and up
    "Sytrus": "producer", "Slicex": "producer", "Edison": "producer", "Maximus": "producer",   # Sytrus: confirmed
    "Emphasizer": "producer", "Soundgoodizer": "producer",   # Emphasizer confirmed on Producer; verify Fruity
    "Vocodex": "producer",                              # verify
    "Fruity Convolver": "producer",                     # verify
    "FL Studio Mobile": "producer",                     # verify
    "Transient Processor": "producer",                  # verify
    # Signature Bundle and up
    "Gross Beat": "signature", "Harmless": "signature", "NewTone": "signature", "NewTime": "signature",
    "Pitcher": "signature", "Hardcore": "signature", "Fruity Video Player": "signature",
    "DirectWave": "signature",                          # verify (legacy; a player version was in every edition)
    # All Plugins Edition only (also sold separately)
    "Harmor": "all_plugins", "Sakura": "all_plugins", "Ogun": "all_plugins", "Morphine": "all_plugins",
    "PoiZone": "all_plugins", "Sawer": "all_plugins", "Toxic Biohazard": "all_plugins", "Drumaxx": "all_plugins",
    "Transistor Bass": "all_plugins",                   # verify
    "Kepler": "all_plugins", "Kepler Exo": "all_plugins",   # verify
    "Vintage Chorus": "all_plugins", "Vintage Phaser": "all_plugins",   # verify
}
# Free third-party instruments (owned by anyone who installed them)
FREE_PLUGINS = ["FLEX", "INSTRUMENT", "Splice INSTRUMENT", "LABS", "Spitfire Audio - LABS",
                "BBC Symphony Orchestra Discover", "BBC SO Discover"]
NATIVE_MIN_N = {}
DEFAULT_OWNED = [k for k, v in NATIVE_MIN.items() if v == "fruity"] + FREE_PLUGINS + ["Fruity *"]   # any edition
DEFAULT_TRIAL = [k for k, v in NATIVE_MIN.items() if v == "all_plugins"]   # sold separately: demo unless owned
# the instruments (generators) among the edition-tier natives; every other tier entry is an effect / tool
NATIVE_GENERATORS = {"Sytrus", "Slicex", "FL Studio Mobile", "DirectWave", "Harmless", "Harmor", "Sakura", "Ogun",
                     "Morphine", "PoiZone", "Sawer", "Toxic Biohazard", "Drumaxx", "Transistor Bass", "Kepler",
                     "Kepler Exo"}


def edition_adds(ed):
    """{"instruments": [...], "effects": [...]}: the natives that come with exactly this edition's tier."""
    out = {"instruments": [], "effects": []}
    for k, v in NATIVE_MIN.items():
        if v == ed and ed != "fruity":
            out["instruments" if k in NATIVE_GENERATORS else "effects"].append(k)
    return out


def edition_unlocks(ed):
    """{"instruments": [...], "effects": [...]}: what `ed` includes beyond the Fruity Edition (from NATIVE_MIN);
    {"not_included": n} counts the higher-tier natives that stay demos (FL installs them all as trials)."""
    if ed not in EDITIONS:
        return {}
    have = EDITIONS[1:EDITIONS.index(ed) + 1]
    out = {"instruments": [], "effects": []}
    for t in have:
        a = edition_adds(t)
        out["instruments"] += a["instruments"]; out["effects"] += a["effects"]
    out["not_included"] = sum(1 for v in NATIVE_MIN.values() if EDITIONS.index(v) > EDITIONS.index(ed))
    return out


def edition_from_plugins(prof=None):
    """What the collection profile's own owned_plugins / trial_plugins lists say about the FL edition (named
    entries only, no wildcards): an owned Producer/Signature native means at least that edition, a trial native means
    below its edition. All-Plugins-only natives are also sold separately, so owning one proves nothing.
    -> {"at_least", "at_most", "edition" (only when both agree), "evidence": [...], "conflict"?} or None."""
    if prof is None:
        try:
            prof = flh_store.profile_get() if H else {}
        except Exception:
            prof = {}
    lo, hi, ev = 0, len(EDITIONS) - 1, []
    for key, owned in (("owned_plugins", True), ("trial_plugins", False)):
        names = prof.get(key) or []
        for name in [x.strip() for x in re.split(r"[;,\n]", names)] if isinstance(names, str) else names:
            if not isinstance(name, str) or any(c in name for c in "*?"):
                continue
            need = _native_min(name)
            if not need or need == "fruity":
                continue
            i = EDITIONS.index(need)
            if owned and need != "all_plugins" and i > lo:
                lo = i; ev.append(f"owns {name} ({EDITION_NAMES[need]}+)")
            elif not owned and i - 1 < hi:
                hi = i - 1; ev.append(f"{name} is a trial (below {EDITION_NAMES[need]})")
    if not ev:
        return None
    out = {"at_least": EDITIONS[lo], "at_most": EDITIONS[max(hi, 0)], "evidence": ev[:4]}
    if lo > hi:
        out["conflict"] = True
    elif lo == hi:
        out["edition"] = EDITIONS[lo]
    return out


def _norm(s):
    s = str(s or "").lower().replace("symphony orchestra", "so")
    s = re.sub(r"^(spitfire audio|splice|image-line|fruity wrapper)\s*[-:]?\s*", "", s)
    s = re.sub(r"\s*\((vst3?|x64|64[- ]?bit|clap)\)\s*$", "", s)
    return re.sub(r"[^a-z0-9*?]+", " ", s).strip()


def _matches(name, patterns):
    n = _norm(name)
    if isinstance(patterns, str):                                    # owned_plugins: "Fruity *" (one string, not a
        patterns = [x for x in re.split(r"[;,\n]", patterns)]        # list) - never its single characters ('*'!)
    for p in patterns or []:
        q = _norm(p)
        if q and (fnmatch.fnmatchcase(n, q) or n == q or ("*" not in q and "?" not in q and f" {q} " in f" {n} ")):
            return True
    return False


def edition_key(text):
    """'Producer Edition' / 'ape' / 'all_plugins' -> canonical key, None when unknown."""
    t = re.sub(r"[^a-z_ ]+", " ", str(text or "").lower()).strip()
    t = re.sub(r"^fl studio\s+", "", t).strip()
    return EDITION_ALIASES.get(t) or EDITION_ALIASES.get(t.replace("_", " ")) or (t if t in EDITIONS else None)


def fl_edition():
    """(edition key or None, where it came from): the collection profile's fl_edition, then what flh_setup stored
    for this PC, then FLH_FL_EDITION, then the profile's own owned/trial plugin lists when they pin exactly one
    edition (edition_from_plugins; source "plugins"). Never guessed."""
    try:
        prof = flh_store.profile_get() if H else {}
    except Exception:
        prof = {}
    if edition_key(prof.get("fl_edition")):
        return edition_key(prof["fl_edition"]), "profile"
    try:
        st = H["_state"]() if H else {}
    except Exception:
        st = {}
    if edition_key(st.get("fl_edition")):
        return edition_key(st["fl_edition"]), "setup"
    if edition_key(os.environ.get("FLH_FL_EDITION")):
        return edition_key(os.environ["FLH_FL_EDITION"]), "FLH_FL_EDITION"
    ev = edition_from_plugins(prof) if prof else None
    if ev and ev.get("edition"):
        return ev["edition"], "plugins"
    return None, None


def _native_min(name):
    if not NATIVE_MIN_N:
        NATIVE_MIN_N.update({_norm(k): v for k, v in NATIVE_MIN.items()})
    return NATIVE_MIN_N.get(_norm(name))


_VST_CACHE = {"t": 0.0, "names": set()}


def installed_vsts():
    """Third-party plugins (VST / VST3 / CLAP) FL's plugin database lists as installed - the user installed them,
    so they count as owned. Cached for a minute."""
    if time.time() - _VST_CACHE["t"] < 60:
        return _VST_CACHE["names"]
    names = set()
    for r in _plugin_db_roots():
        for kind in ("Generators", "Effects"):
            base = os.path.join(r, "Installed", kind)
            if not os.path.isdir(base):
                continue
            for top in os.listdir(base):
                if not re.match(r"(vst|clap)", top, re.I):
                    continue
                for root, _, files in os.walk(os.path.join(base, top)):
                    for f in files:
                        if f.lower().endswith(".fst"):
                            stem = f[:-4]
                            nfo = os.path.join(root, stem + ".nfo")
                            names.add(_norm((_nfo_name(nfo) if os.path.isfile(nfo) else None) or stem))
    _VST_CACHE.update(t=time.time(), names=names)
    return names


def ownership(plugin_name):
    """{"status": True | "trial" | "unknown", "why": ...} for a plugin. Order: the collection profile's
    trial_plugins / owned_plugins, free plugins, the FL edition's native set (fl_edition), third-party plugins
    installed on this PC (FL's plugin database), else unknown (ask the user)."""
    try:
        prof = flh_store.profile_get() if H else {}
    except Exception:
        prof = {}
    if _matches(plugin_name, prof.get("trial_plugins")):
        return {"status": "trial", "why": "the collection profile lists it as trial"}
    if _matches(plugin_name, prof.get("owned_plugins")):
        return {"status": True, "why": "the collection profile lists it as owned"}
    if _matches(plugin_name, FREE_PLUGINS):
        return {"status": True, "why": "free plugin"}
    need = _native_min(plugin_name)
    ed, src = fl_edition()
    if need:
        if need == "fruity":
            return {"status": True, "why": "in every FL edition"}
        if ed:
            have = EDITIONS.index(ed) >= EDITIONS.index(need)
            return {"status": True if have else "trial",
                    "why": f"{'included in' if have else 'not in'} {EDITION_NAMES[ed]} (needs {EDITION_NAMES[need]}+)"}
        if need == "all_plugins":                                     # sold separately: demo unless proven owned
            return {"status": "trial", "why": "only in the All Plugins Edition (or bought separately); FL edition "
                    "unknown: set it with flh_setup(action='apply', fl_edition=...) or list it in owned_plugins"}
        return {"status": "unknown", "why": f"needs {EDITION_NAMES[need]} or higher; FL edition unknown (ask the user, "
                "then flh_setup(action='apply', fl_edition=...))"}
    if _norm(plugin_name).startswith("fruity "):
        return {"status": True, "why": "stock Fruity plugin (every edition)"}
    try:
        if _norm(plugin_name) in installed_vsts():
            return {"status": True, "why": "third-party plugin installed on this PC (FL's plugin database)"}
    except Exception:
        pass
    return {"status": "unknown", "why": "not a known FL plugin and not in FL's plugin database: ask the user"}


def sound_ok(plugin_name):
    """Is a plugin usable? True (owned/free), "trial" (never use: a project saved with it shows a demo warning),
    "unknown" (ask the user). See ownership() for the order of sources."""
    return ownership(plugin_name)["status"]


# ----------------------------------------------------------------------------- words -> kind / tags
KIND_WORDS = [
    ("choir", ("choir", "choral", "vocal", "vocals", "voice", "voices", "chant", "aah", "ooh", "singer")),
    ("piano", ("piano", "keys", "rhodes", "wurli", "wurlitzer", "epiano", "e-piano", "clav", "harpsichord",
               "celeste", "celesta", "upright", "grand")),
    ("organ", ("organ", "harmonium", "accordion")),
    ("strings", ("string", "strings", "violin", "violins", "viola", "violas", "cello", "celli", "cellos", "quartet",
                 "ensemble", "orchestra", "orchestral", "basses", "fiddle")),
    ("brass", ("brass", "horn", "horns", "trumpet", "trumpets", "trombone", "trombones", "tuba", "cornet")),
    ("woodwind", ("woodwind", "woodwinds", "flute", "flutes", "piccolo", "clarinet", "clarinets", "oboe", "oboes",
                  "bassoon", "bassoons", "recorder", "whistle", "sax", "saxophone")),
    ("perc", ("drum", "drums", "perc", "percussion", "kit", "kick", "snare", "hat", "hats", "timpani", "cymbal",
              "cymbals", "taiko", "loop", "beat", "fpc", "slicer", "slicex", "clap")),
    ("bells", ("bell", "bells", "music box", "musicbox", "glock", "glockenspiel", "xylophone", "marimba",
               "vibraphone", "vibes", "kalimba", "mallet", "mallets", "chime", "chimes")),
    ("pluck", ("pluck", "plucked", "harp", "autoharp", "dulcimer", "zither", "pizzicato", "pizz", "koto", "banjo",
               "mandolin", "ukulele")),
    ("guitar", ("guitar", "guitars", "nylon", "acoustic guitar")),
    ("bass", ("bass", "sub", "808", "boobass")),
    ("pad", ("pad", "pads", "swell", "swells", "drone", "drones", "atmos", "atmosphere", "texture", "textures",
             "ambient", "ambience", "evolving")),
    ("lead", ("lead", "leads")),
    ("fx", ("fx", "sfx", "riser", "risers", "impact", "noise", "sweep", "glitch", "granular", "granulizer",
            "transition", "foley")),
    ("synth", ("synth", "synths", "saw", "analog", "analogue", "osc", "fm", "modular", "arp", "sytrus", "wavetable",
               "poly", "chip", "retro")),
]
TAG_WORDS = {
    "soft": ("soft", "gentle", "felt", "quiet", "tender", "hushed", "lullaby"),
    "warm": ("warm", "felt", "tape", "vintage", "lofi", "lo-fi", "mellow", "cozy", "round"),
    "dark": ("dark", "scary", "horror", "haunted", "eerie", "sinister", "night", "shadow", "gritty", "doom"),
    "airy": ("airy", "air", "breath", "breathy", "frozen", "arctic", "ice", "moon", "cloud", "ethereal", "space"),
    "cold": ("frozen", "arctic", "ice", "icy", "glass", "winter", "cold", "crystal"),
    "bright": ("bright", "glass", "crystal", "shiny", "sparkle", "bell", "glock"),
    "epic": ("epic", "cinematic", "trailer", "huge", "big", "massive", "heroic", "orchestra"),
    "intimate": ("intimate", "close", "solo", "felt", "small", "private"),
    "dreamy": ("dream", "dreamy", "moon", "lullaby", "nostalgia", "nostalgic", "music box", "haze"),
    "lo-fi": ("tape", "lofi", "lo-fi", "cassette", "vhs", "dusty", "worn"),
    "eerie": ("scary", "eerie", "horror", "frozen", "haunted", "ghost", "creepy"),
    "delicate": ("delicate", "glass", "music box", "celeste", "kalimba", "harp", "fragile"),
}


def _words(text):
    return re.findall(r"[a-z0-9!'+-]+", str(text or "").lower().replace("_", " "))


def _guess(*texts):
    """(kind, tags) from names: first kind word found wins (in KIND_WORDS order of the earliest text)."""
    blob = " ".join(str(t or "") for t in texts).lower().replace("_", " ")
    ws = set(_words(blob)); kind = None
    for t in texts:
        tl = str(t or "").lower().replace("_", " "); tw = set(_words(tl))
        for k, keys in KIND_WORDS:
            if any((" " in w and w in tl) or w in tw for w in keys):
                kind = k; break
        if kind:
            break
    tags = sorted(tag for tag, keys in TAG_WORDS.items() if any((" " in w and w in blob) or w in ws for w in keys))
    return kind or "other", tags


# ----------------------------------------------------------------------------- curated descriptions
# LABS packs: only what's well known about each; anything else gets tags from its name.
LABS = {
    "soft piano": ("piano", ["soft", "intimate", "warm", "felt"], "felted upright recorded close: quiet, hushed"),
    "strings": ("strings", ["warm", "lush", "cinematic"], "string ensemble, longs and shorts"),
    "frozen strings": ("strings", ["cold", "airy", "eerie", "ambient"], "icy, brittle string texture"),
    "choir": ("choir", ["soft", "airy", "ethereal", "sacred"], "choir 'aah'/'ooh' pads"),
    "moon guitar": ("guitar", ["dreamy", "airy", "soft", "ambient"], "ambient electric guitar"),
    "amplified cello quartet": ("strings", ["dark", "gritty", "edgy"], "four amplified celli: raw, distorted edge"),
    "arctic swells": ("pad", ["cold", "airy", "cinematic", "swelling"], "slow orchestral swells"),
    "autoharp": ("pluck", ["bright", "warm", "folk", "gentle"], "strummed/plucked autoharp"),
    "music box": ("bells", ["delicate", "dreamy", "nostalgic"], "music box"),
    "kalimba": ("bells", ["delicate", "warm", "gentle"], "thumb piano"),
    "cello moods": ("strings", ["intimate", "warm", "melancholic"], "solo cello"),
    "tape orchestra": ("strings", ["lo-fi", "warm", "nostalgic"], "orchestra through worn tape"),
    "drums": ("perc", ["punchy"], "drum kit"),
    "electric piano": ("piano", ["warm", "mellow"], "electric piano"),
    "opia": ("pad", ["dark", "eerie", "ambient"], "strange evolving textures"),
}

# BBC Symphony Orchestra Discover. Ranges are typical sounding ranges of the instrument (Discover's sampled range
# can be narrower): verify by ear. "verify": the instrument's presence in Discover itself isn't certain.
BBC_ARTICS = {"strings": ["Long", "Spiccato", "Pizzicato"], "woodwind": ["Long", "Short"], "brass": ["Long", "Short"],
              "perc": ["Hits"], "bells": ["Hits"], "pluck": ["Plucked"]}
BBC = [  # name, kind, range, tags, sure
    ("Violins 1", "strings", "G3-C7", ["warm", "lyrical", "epic"], True),
    ("Violins 2", "strings", "G3-C7", ["warm", "lyrical"], True),
    ("Violas", "strings", "C3-E6", ["warm", "dark", "mellow"], True),
    ("Celli", "strings", "C2-A5", ["warm", "dark", "lyrical"], True),
    ("Basses", "strings", "E1-G3 (sounding)", ["dark", "deep"], True),
    ("Piccolo", "woodwind", "D5-C8", ["bright", "piercing"], False),
    ("Flutes", "woodwind", "C4-C7", ["airy", "soft", "bright"], True),
    ("Oboes", "woodwind", "Bb3-A6", ["intimate", "plaintive"], True),
    ("Clarinets", "woodwind", "D3-Bb6 (sounding)", ["warm", "soft", "mellow"], True),
    ("Bassoons", "woodwind", "Bb1-E5", ["dark", "warm", "comic"], True),
    ("Horns", "brass", "B1-F5 (sounding)", ["warm", "epic", "noble"], True),
    ("Trumpets", "brass", "E3-C6 (sounding)", ["bright", "epic", "heroic"], True),
    ("Tenor Trombones", "brass", "E2-F5", ["epic", "dark", "powerful"], True),
    ("Bass Trombones", "brass", "Bb1-Bb4", ["dark", "epic"], False),
    ("Tuba", "brass", "D1-F4", ["dark", "deep", "epic"], True),
    ("Timpani", "perc", "D2-C4 (pedal timpani set)", ["epic", "dark", "rolls"], True),
    ("Untuned Percussion", "perc", "hits mapped across keys", ["epic", "punchy"], True),
    ("Glockenspiel", "bells", "G5-C8 (sounding)", ["bright", "delicate"], False),
    ("Xylophone", "bells", "F4-C8 (sounding)", ["bright", "dry"], False),
    ("Harp", "pluck", "C1-G7", ["delicate", "airy", "soft"], False),
    ("Celeste", "bells", "C4-C8 (sounding)", ["delicate", "dreamy", "bright"], False),
]

STOCK = {  # FL generators: kind, tags, one-line description
    "sytrus": ("synth", ["versatile", "warm", "bright"], "FM/RM synth: pads, keys, basses, bells - huge preset bank"),
    "flex": ("synth", ["versatile"], "preset player: piano, strings, pads, leads from FLEX packs"),
    "fpc": ("perc", ["punchy"], "drum pads with kits"),
    "3x osc": ("synth", ["retro", "simple"], "3-oscillator synth: simple leads, basses, chip tones"),
    "boobass": ("bass", ["warm", "round"], "bass guitar"),
    "fruit kick": ("perc", ["punchy"], "kick drum synth"),
    "fruity kick": ("perc", ["punchy"], "kick drum synth"),
    "fruity dx10": ("synth", ["bright", "retro"], "FM synth: e-piano, bells, basses"),
    "fruity granulizer": ("fx", ["eerie", "airy"], "granular sampler: textures, drones"),
    "fruity slicer": ("perc", [], "beat slicer"),
    "slicex": ("perc", [], "loop slicer"),
    "fl keys": ("piano", ["simple"], "basic piano / e-piano / clav"),
    "plucked!": ("pluck", ["bright", "simple"], "plucked string model"),
    "autogun": ("synth", [], "additive preset synth"),
    "bassdrum": ("perc", ["punchy"], "kick drum synth"),
    "simsynth": ("synth", ["retro"], "analog-style synth"),
    "minisynth": ("synth", ["retro"], "mono analog-style synth"),
    "wave traveller": ("fx", [], "scratch / pitch-bend sampler"),
    "sakura": ("strings", ["soft", "warm"], "physical-model strings/plucks (All Plugins Edition)"),
    "harmor": ("synth", ["versatile"], "additive/resynthesis synth"),
    "ogun": ("pad", ["bright", "metallic", "airy"], "metallic additive pads"),
    "kepler": ("synth", ["warm"], "dual-oscillator synth"),
    "toxic biohazard": ("synth", ["bright"], "FM synth"),
    "morphine": ("synth", [], "additive synth"),
    "poizone": ("synth", ["bright"], "subtractive synth"),
    "sawer": ("synth", ["retro", "warm"], "vintage saw synth"),
    "transistor bass": ("bass", ["acid"], "303-style bass"),
}


STOCK_N = {_norm(k): v for k, v in STOCK.items()}
LABS_N = {_norm(k): v for k, v in LABS.items()}


# ----------------------------------------------------------------------------- source folders
def _user_dirs():
    """FL's user data folder - only the one FL really uses (_fl_userdata), so stale copies (an old OneDrive
    Documents, a second Documents) never show up as sources."""
    try:
        u = H["_fl_userdata"]()
        if u:
            return [os.path.normpath(u)]
    except Exception:
        pass
    env = os.environ.get("FLH_FL_USERDATA")
    return [env or os.path.join(os.path.expanduser("~"), "Documents", "Image-Line", "FL Studio")]


def _plugin_db_roots():
    if os.environ.get("FLH_PLUGIN_DB"):
        return [os.environ["FLH_PLUGIN_DB"]]
    out = []
    for docs in _user_dirs():
        out += [os.path.join(docs, "Presets", "Plugin database"), os.path.join(docs, "FL Studio", "Presets",
                                                                              "Plugin database")]
    return [r for r in dict.fromkeys(out) if os.path.isdir(r)]


def _flex_roots():
    if os.environ.get("FLH_FLEX_DIR"):
        return [os.environ["FLH_FLEX_DIR"]]
    out = []
    for docs in _user_dirs():
        il = os.path.dirname(docs)                                   # Documents\Image-Line
        out += [os.path.join(docs, "FLEX"), os.path.join(il, "FLEX"), os.path.join(il, "Data", "FLEX"), os.path.join(il, "Data", "FL Studio", "FLEX"),
                os.path.join(docs, "Presets", "Plugin presets", "Generators", "FLEX"),
                os.path.join(docs, "FL Studio", "Presets", "Plugin presets", "Generators", "FLEX")]
    return [r for r in dict.fromkeys(out) if os.path.isdir(r)]


def _splice_root():
    try:
        return H["_splice_lib"]()
    except Exception:
        return os.environ.get("FLH_SPLICE_LIB") or os.path.join(os.path.expanduser("~"), "Splice", "INSTRUMENT")


def _sounds_root():
    return H.get("SOUNDS") or ""


def _dir_sig(root, depth):
    """{dir: mtime} for root and its subfolders down to `depth` (a new pack/preset changes one of them)."""
    sig = {}
    if not root or not os.path.isdir(root):
        return {root or "?": None}
    stack = [(root, 0)]
    while stack and len(sig) < 600:
        d, lv = stack.pop()
        try:
            sig[d] = round(os.path.getmtime(d), 2)
            if lv < depth:
                for e in os.scandir(d):
                    if e.is_dir():
                        stack.append((e.path, lv + 1))
        except OSError:
            sig[d] = None
    return sig


def _signature():
    sig = {}
    sig.update(_dir_sig(_splice_root(), 3))
    for r in _plugin_db_roots() or ["(plugin database)"]:
        sig.update(_dir_sig(r, 4))
    for r in _flex_roots() or ["(flex)"]:
        sig.update(_dir_sig(r, 2))
    sig.update(_dir_sig(_sounds_root(), 0))
    fi = _fl_instruments_root()
    if fi:
        sig.update(_dir_sig(fi, 1))
    return sig


def _cache_path():
    return os.path.join(os.path.dirname(H["STATE"]), "catalog.json")


def _own_write(path, data):
    """Write a file: inside fl-headless's own state folder, or inside FLH_ROOTS (writable check)."""
    own = os.path.normcase(os.path.abspath(os.path.dirname(H["STATE"])))
    if not os.path.normcase(os.path.abspath(path)).startswith(own + os.sep):
        H["writable"](path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)


OWN_808_NOTE = 24                        # C1: fl-headless's own 808 (made here, so its pitch is always known)


def own_808():
    """Path of fl-headless's own 808 one-shot (a tuned sine with a short pitch drop and soft drive, C1), made on first
    use in its state folder. For PCs whose 808 samples have no readable pitch: this one can always be tuned."""
    import numpy as np
    path = os.path.join(os.path.dirname(H["STATE"]), "sounds", "fl-headless 808 C1.wav")
    if os.path.isfile(path):
        return path
    fs = 44100; t = np.arange(int(fs * 2.4)) / fs
    f0 = 440.0 * 2 ** ((OWN_808_NOTE - 69) / 12.0)
    f = f0 * (1 + (2 ** (7 / 12.0) - 1) * np.exp(-t / 0.035))   # starts a fifth up, settles on C1 in ~100 ms
    x = np.sin(2 * np.pi * np.cumsum(f) / fs)
    x = np.tanh(1.6 * x) / np.tanh(1.6)                           # soft drive: harmonics you hear on small speakers
    env = np.exp(-t / 0.9) * np.minimum(1.0, t / 0.002)
    env *= np.clip((2.4 - t) / 0.15, 0, 1)                        # fade out at the end, no click
    pcm = (x * env * 0.89 * 32767).astype("<i2").tobytes()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
                 struct.pack("<IHHIIHH", 16, 1, 1, fs, fs * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)
    os.replace(tmp, path)
    return path


# ----------------------------------------------------------------------------- builders
def _e(name, kind, source, load, tags=(), note=None, plugin=None, **extra):
    d = {"name": name, "kind": kind, "source": source, "load": load, "tags": sorted(set(tags))}
    if note:
        d["note"] = note
    if plugin:
        d["plugin"] = plugin
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


def _family(pack):
    return re.sub(r"^(LABS|Spitfire Audio|Splice)\s*[-:]?\s*", "", pack, flags=re.I).strip()


def _from_splice(sources):
    out = []
    try:
        lib = H["flp_instrument_sounds"]()
    except Exception as e:
        sources["LABS"] = {"found": False, "root": _splice_root(), "why": str(e)[:120]}
        return out
    sources["LABS"] = {"found": True, "root": lib.get("library"), "packs": len(lib.get("packs", []))}
    for pk in lib.get("packs", []):
        fam = _family(pk["pack"]); cur = LABS_N.get(_norm(fam))
        presets = pk.get("presets") or []
        for pre in presets or [None]:
            label = fam if len(presets) <= 1 or not pre else f"{fam} / {pre}"
            kind, tags = _guess(fam, pre)
            desc = None
            if cur:
                kind, desc = cur[0], cur[2]; tags = sorted(set(tags) | set(cur[1]))
            out.append(_e(label, kind, "LABS", {"tool": "flp_instrument_sound", "pack": pk["pack"], "preset": pre,
                                                "channel": "<an INSTRUMENT channel: clone one with flp_add_channel>"},
                          tags, (desc + "; " if desc else "") + "check by ear (unsupported packs load silent)",
                          plugin="INSTRUMENT", version=pk.get("version")))
    return out


def _nfo_name(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                k, _, v = line.partition("=")
                if k.strip().lower() in ("ps_name", "name", "ps_file_name_0") and v.strip():
                    return v.strip()
    except OSError:
        pass
    return None


def _from_plugin_db(sources):
    roots = _plugin_db_roots(); found = {}
    sources["stock"] = {"found": bool(roots), "roots": roots}
    for r in roots:
        for base in (os.path.join(r, "Installed", "Generators"), os.path.join(r, "Generators")):
            if not os.path.isdir(base):
                continue
            installed = "Installed" in os.path.relpath(base, r).split(os.sep)
            for root, _, files in os.walk(base):
                cat = os.path.relpath(root, base)
                for f in files:
                    if not f.lower().endswith(".fst"):
                        continue
                    p = os.path.join(root, f); stem = f[:-4]
                    nfo = os.path.join(root, stem + ".nfo")
                    name = (_nfo_name(nfo) if os.path.isfile(nfo) else None) or stem
                    key = _norm(name)
                    top = cat.split(os.sep)[0] if cat != "." else ""
                    vst = top.lower().startswith("vst")
                    prev = found.get(key)
                    if prev:                                         # keep one per plugin: Installed path, VST3 > VST
                        if not installed:
                            prev["category"] = prev.get("category") or cat
                            continue
                        if prev["_installed"] and not (top.lower() == "vst3" and prev["_top"] != "vst3"):
                            continue
                    found[key] = {"name": name, "path": p, "_installed": installed, "_top": top.lower(),
                                  "vst": vst, "category": None if installed else cat}
    out = []
    for key, x in sorted(found.items()):
        cur = STOCK_N.get(key)
        if cur:
            kind, tags, desc = cur
        else:
            kind, tags = _guess(x["name"], x.get("category")); desc = None
        if x.get("category"):
            ck, ct = _guess(x["category"]); tags = sorted(set(tags) | set(ct))
            kind = kind if kind != "other" else ck
        src = "VST" if x["vst"] else "stock"
        out.append(_e(x["name"], kind, src, {"tool": "flp_add_channel", "preset": x["path"],
                                             "clone_from": "<any plugin channel>"}, tags, desc, plugin=x["name"]))
    return out


def _from_flex(sources):
    roots = _flex_roots(); out = []; n = 0
    sources["FLEX"] = {"found": bool(roots), "roots": roots}
    for r in roots:
        base = os.path.join(r, "Presets") if os.path.isdir(os.path.join(r, "Presets")) else r
        for root, dirs, files in os.walk(base):
            rel = os.path.relpath(root, base)
            if rel.count(os.sep) >= 3:
                dirs[:] = []
            parts = [] if rel == "." else rel.split(os.sep)
            for f in sorted(files):
                ext = os.path.splitext(f)[1].lower()
                if ext not in FLEX_EXTS:
                    continue
                n += 1
                if n > FLEX_MAX_FILES:
                    sources["FLEX"]["truncated"] = True
                    return out
                stem = os.path.splitext(f)[0]; pack = parts[0] if parts else os.path.basename(r)
                kind, tags = _guess(stem, *reversed(parts))
                if ext in (".flex2pack", ".flexpack"):              # an installed pack (presets are inside it)
                    kind, tags = _guess(stem, "flex")
                    load = {"tool": "fl_plugin_preset_find", "name": stem, "note": "add a FLEX channel, pick pack "
                            f"'{stem}' in FLEX's browser, then choose a preset"}
                    out.append(_e(f"FLEX - {stem}", kind, "FLEX", load, tags + ["pack"], f"FLEX pack '{stem}' "
                                  "(installed)", plugin="FLEX", pack=stem))
                    continue
                if ext == ".fst":
                    load = {"tool": "flp_add_channel", "preset": os.path.join(root, f), "clone_from": "<any plugin channel>"}
                else:
                    load = {"tool": "fl_plugin_preset_find", "name": stem, "index": "<a FLEX channel with pack "
                            f"'{pack}' selected>"}
                out.append(_e(stem, kind, "FLEX", load, tags, f"FLEX pack {pack}", plugin="FLEX", pack=pack))
    FX = sys.modules.get("flh_flex")
    if FX is not None and FX.H is not None:                  # every preset inside the installed packs, by name
        try:
            rows = FX.index()
        except Exception as e:
            rows = []; sources["FLEX"]["presets_error"] = str(e)[:80]
        for r in rows:                                       # one kind for a FLEX preset everywhere: flh_flex's
            _k, tags = _guess(r["preset"], r["pack"])         # (flp_flex_preset reports the same)
            kind = FX.catalog_kind(r["kind"]) if hasattr(FX, "catalog_kind") else (_k or r["kind"])
            out.append(_e(r["preset"], kind, "FLEX", {"tool": "flp_flex_preset", "preset": r["preset"],
                                                      "pack": r["pack"]}, tags + ["flex"],
                          f"FLEX preset ({r['pack']})", plugin="FLEX", pack=r["pack"],
                          installed=False if r.get("installed") is False else None))
        sources["FLEX"]["presets"] = len(rows)
    return out


def _fl_instruments_root():
    p = _factory_packs()
    p = os.path.join(p, "Instruments") if p else None
    return p if p and os.path.isdir(p) else None


FL_INST_KIND = {"bass": "bass", "guitar": "guitar", "piano": "piano", "keys": "piano", "organ": "organ",
                "strings": "strings", "string": "strings", "brass": "brass", "woodwind": "woodwind", "winds": "woodwind",
                "flute": "woodwind", "choir": "choir", "vocal": "choir", "voice": "choir", "bell": "bells",
                "bells": "bells", "mallet": "bells", "mallets": "bells", "percussion": "perc", "synth": "synth",
                "pad": "pad", "lead": "lead"}


def _from_fl_instruments(sources):
    """FL's own multisampled instruments: <Packs>/Instruments/<family>/<Name>.fst (DirectWave presets of real
    recordings, e.g. Bass/Acoustic.fst - an upright bass). They load with fl_load_preset and play in tune."""
    root = _fl_instruments_root(); out = []
    sources["FL instruments"] = {"found": bool(root), "root": root}
    if not root:
        return out
    try:
        fams = sorted(e for e in os.listdir(root) if os.path.isdir(os.path.join(root, e)))
    except OSError:
        return out
    for fam in fams:
        fdir = os.path.join(root, fam)
        try:
            files = sorted(f for f in os.listdir(fdir) if f.lower().endswith(".fst"))
        except OSError:
            continue
        kind = FL_INST_KIND.get(fam.lower())
        if not kind:
            kind, _t = _guess(fam)
        for f in files:
            stem = f[:-4]
            _k, tags = _guess(stem, fam)
            out.append(_e(f"{stem} {fam}" if fam.lower() not in stem.lower() else stem, kind, "FL instruments",
                          {"tool": "fl_load_preset", "preset": os.path.join(fdir, f), "channel": "<N>"},
                          list(tags) + ["recorded", "multisampled", fam.lower()],
                          f"FL's own multisampled {fam.lower()} (DirectWave, real recordings)", plugin="DirectWave",
                          family=fam))
    sources["FL instruments"]["count"] = len(out)
    return out


def _from_bbc(sources, stock):
    have = next((e for e in stock if "bbc" in _norm(e["name"])), None)
    sources["BBC"] = {"found": bool(have), "plugin_db": have["load"]["preset"] if have else None,
                      "note": None if have else "not seen in FL's plugin database (verify it's installed)"}
    out = []
    for name, kind, rng, tags, sure in BBC:
        out.append(_e("BBC " + name, kind, "BBC",
                      {"tool": "flp_add_channel", "clone_from": "<a BBC SO Discover channel>",
                       "then": {"tool": "flp_plugin_settings", "settings": {"p_midiChannel": 0},
                                "articulation": BBC_ARTICS.get(kind, ["Long"])[0]},
                       "instrument": "pick it once in BBC's window, then flp_save_sound it as 'BBC " + name + "'"},
                      tags, "BBC: set p_midiChannel 0 after copying or it's silent", plugin="BBC Symphony Orchestra Discover",
                      range=rng + ("" if sure else " (verify)"), articulations=BBC_ARTICS.get(kind),
                      verify=None if sure else "instrument may not be in Discover"))
    return out


def _from_saved(sources):
    try:
        sounds = H["flp_sounds"]()
    except Exception as e:
        sources["saved"] = {"found": False, "why": str(e)[:120]}
        return []
    sources["saved"] = {"found": True, "root": _sounds_root(), "count": len(sounds)}
    out = []
    for s in sounds:
        kind, tags = _guess(s["name"], s.get("plugin"))
        out.append(_e(s["name"], kind, "saved", {"tool": "fl_load_preset", "preset": s["name"]}, tags,
                      f"saved {s.get('plugin') or '?'} sound", plugin=s.get("plugin")))
    return out


# ---- first builds on a slow disk: one build at a time, progress with an estimate, waiting callers told --------------
_BUILDS = {}                                                      # name -> the running build (see index_build)
_BUILDS_LOCK = threading.Lock()
_NOTE_TL = threading.local()                                      # this thread's last index note (waited / partial)
PROGRESS_EVERY_S = 0.5
CATALOG_WAIT_S = 120.0                                            # the instrument index: callers wait this long
SAMPLES_WAIT_S = 8.0                                              # sample / FLEX index: then the partial index


def _eta(b):
    pct = b.get("pct") or 0
    if pct < 2:
        return None
    el = time.time() - b["t0"]
    return int(round(el * (100 - pct) / pct))


def build_note(b, partial=False):
    """'building the sound index (42%, ~1 min left)' for a running build."""
    eta = _eta(b); pct = int(b.get("pct") or 0)
    left = "" if eta is None else (f", ~{eta} s left" if eta < 90 else f", ~{round(eta / 60)} min left")
    return f"building the {b['label']} ({pct}%{left})" + (
        ": these results come from the part indexed so far; ask again in a moment for all of them" if partial else "")


def build_tick(b, pct, step=None, force=False):
    """A build's progress: kept on the build (waiting callers read it) and shown by fl_job when the building thread
    is a background job (H['_job_progress'] is a no-op elsewhere). Throttled."""
    if b is None:
        return
    b["pct"] = max(b.get("pct") or 0, min(99.0, float(pct)))
    now = time.time()
    if not force and now - b.get("_shown", 0.0) < PROGRESS_EVERY_S:
        return
    b["_shown"] = now
    try:
        H["_job_progress"](step=step or build_note(b), pct=int(b["pct"]), eta_s=_eta(b))
    except Exception:
        pass


def take_note():
    """The note the last index call in this thread left (waited for / used a partial index), cleared."""
    n = getattr(_NOTE_TL, "note", None)
    _NOTE_TL.note = None
    return n


def index_build(name, label, fn, wait_s=None):
    """Run fn(build) once even when several threads (the start-up warm-up, a tool call, a song build) need the index
    at the same time. The builder reports progress with build_tick(build, pct). Another caller waits (showing the
    builder's progress as its own) -> (result, None); still running after wait_s -> (None, build) so the caller
    can use build['partial'] (what's indexed so far) or say it's still building."""
    try:                                          # the job's own step comes back after (PC 2026-10-24: fl_pc_check
        prev = H["_job_progress_get"]()           # showed 'building the sample index (47%, ~1 s left)' for minutes
    except Exception:                             # after the index call had returned)
        prev = None
    try:
        return _index_build(name, label, fn, wait_s)
    finally:
        if prev and prev.get("step"):
            try:
                H["_job_progress"](**{k: v for k, v in prev.items() if k != "at"})
            except Exception:
                pass


def _index_build(name, label, fn, wait_s=None):
    with _BUILDS_LOCK:
        b = _BUILDS.get(name)
        mine = b is None
        if mine:
            b = _BUILDS[name] = {"name": name, "label": label, "t0": time.time(), "pct": 0.0, "partial": None,
                                 "done": threading.Event(), "result": None, "error": None}
    if mine:
        try:
            b["result"] = fn(b)
            return b["result"], None
        except BaseException as e:
            b["error"] = e
            raise
        finally:
            with _BUILDS_LOCK:
                _BUILDS.pop(name, None)
            b["done"].set()
    t0 = time.time(); until = t0 + (1e9 if wait_s is None else float(wait_s))
    while not b["done"].wait(min(PROGRESS_EVERY_S, max(0.01, until - time.time()))):
        try:
            H["_job_progress"](step=build_note(b) + " (another task started it)", pct=int(b["pct"]), eta_s=_eta(b))
        except Exception:
            pass
        if time.time() >= until:
            return None, b
    if b["error"] is not None:                                    # the other build failed: this caller tries itself
        return _index_build(name, label, fn, wait_s)
    if time.time() - t0 >= 1.0:
        _NOTE_TL.note = f"waited {time.time() - t0:.0f} s for the {label} being built (first run on this PC)"
    return b["result"], None


def building():
    """{name: {pct, eta_s}} of the index builds running now (for status lines)."""
    return {n: {"pct": int(b["pct"]), "eta_s": _eta(b)} for n, b in list(_BUILDS.items())}


def _build(b=None):
    sources = {}
    steps = (("Splice", None), ("plugin database", None), ("BBC", None), ("FLEX", None), ("saved sounds", None),
             ("FL instruments", None))
    tick = (lambda i: build_tick(b, 100.0 * i / len(steps), f"building the sound index: {steps[i][0]} "
                                 f"({int(100 * i / len(steps))}%)")) if b is not None else (lambda i: None)
    tick(0)
    entries = _from_splice(sources)
    tick(1)
    stock = _from_plugin_db(sources)
    tick(2)
    bbc = _from_bbc(sources, stock)
    tick(3)
    flex = _from_flex(sources)
    tick(4)
    saved_e = _from_saved(sources)
    tick(5)
    if not sources["BBC"]["found"]:                                  # not installed: its sections aren't the user's
        saved_bbc = any("bbc" in _norm(e.get("plugin") or "") for e in saved_e)
        if saved_bbc:                                                # a saved BBC sound proves it's on this PC
            sources["BBC"].update(found=True, note="found through a saved BBC sound (not in FL's plugin database)")
        else:
            bbc = []
            sources["BBC"]["note"] = ("not installed (not in FL's plugin database, no saved BBC sound): its sections "
                                      "aren't listed")
    entries += bbc + stock + flex + saved_e + _from_fl_instruments(sources)
    saved = {_norm(e["name"]): e for e in entries if e["source"] == "saved"}
    for e in entries:                                                # a saved BBC instrument loads by name
        if e["source"] == "BBC" and _norm(e["name"]) in saved:
            e["load"] = {"tool": "fl_load_preset", "preset": saved[_norm(e["name"])]["name"],
                         "then": e["load"]["then"]}
    return {"version": CACHE_VERSION, "built": time.time(), "sig": _signature(), "sources": sources,
            "entries": entries}


MEMO_S = 5.0                                                      # an index checked against its folders this
_CAT_MEMO = {}                                                    # recently is used as is (the folder walk costs ~1 s)


_HOLD = {"n": 0}                                                  # inside hold(): indexes are reused as is
_HOLD_TL = threading.local()                                      # the build's feature budget (its thread only)


@contextlib.contextmanager
def hold(feature_s=None):
    """One build (a new song, a batch of searches): the sound and sample indexes are checked against their folders
    once and reused until it ends (a folder walk is ~1 s each time), and audio-feature measuring shares one time
    budget of feature_s seconds (later searches rank by names and tags; the background warm-up fills the cache)."""
    outer = getattr(_HOLD_TL, "n", 0) == 0
    _HOLD["n"] += 1; _HOLD_TL.n = getattr(_HOLD_TL, "n", 0) + 1
    if outer:
        _HOLD_TL.until = (time.time() + float(feature_s)) if feature_s is not None else None
    try:
        yield
    finally:
        _HOLD["n"] = max(0, _HOLD["n"] - 1); _HOLD_TL.n = max(0, _HOLD_TL.n - 1)
        if _HOLD_TL.n == 0:
            _HOLD_TL.until = None


def _memo_fresh(memo):
    return bool(memo) and (_HOLD["n"] > 0 or time.time() - memo[0] < MEMO_S)


def _catalog(refresh=False):
    _NOTE_TL.note = None
    memo = _CAT_MEMO.get("cat")                                   # outside a build: checked every call
    if not refresh and memo and _HOLD["n"] > 0:
        memo[1]["_cached"] = True
        return memo[1]
    cat = _catalog_load(refresh)                                  # (checked against its folders)
    _CAT_MEMO["cat"] = (time.time(), cat)
    return cat


def _catalog_load(refresh=False):
    p = _cache_path(); cat = None
    if not refresh and os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                cat = json.load(f)
        except (OSError, ValueError):
            cat = None
        why = None
        if not cat or cat.get("version") != CACHE_VERSION:
            why = "format"
        elif time.time() - cat.get("built", 0) > MAX_AGE_S:
            why = "older than 7 days"
        elif cat.get("sig") != _signature():
            why = "a source folder changed"
        if not why:
            cat["_cached"] = True
            return cat
        cat = None
    def run(b):
        c = _build(b)
        _own_write(p, c)
        c["_cached"] = False
        return c
    cat, still = index_build("catalog", "sound index", run, CATALOG_WAIT_S)
    if cat is None:
        raise RuntimeError(build_note(still) + ": it's the first scan of your sound folders on this PC; try again "
                           "in a minute")
    return cat


# ----------------------------------------------------------------------------- query
def _kind_of(word):
    w = str(word or "").lower().strip()
    for k, keys in KIND_WORDS:
        if w == k or w in keys or w.rstrip("s") == k:
            return k
    return w


def _score(e, words, owned):
    name = " ".join(_words(e["name"])); nameset = set(name.split()); tags = set(e.get("tags") or [])
    s = 0.0
    full = " ".join(words)
    if full and full in name:
        s += 4
    for w in words:
        k = _kind_of(w)
        if w in nameset or (len(w) > 3 and w in name):
            s += 3
        if k == e["kind"]:
            s += 3
        if w in tags:
            s += 2
        if w == str(e["source"]).lower() or w == str(e.get("plugin") or "").lower():
            s += 1.5
    if s and owned is True:
        s += 0.5
    if s and e["source"] in ("LABS", "BBC", "saved", "FL instruments"):
        s += 0.3                                                     # real recorded instruments first on ties
    return s


_QB = {}


def _quality_bonus(e):
    """Likely sound quality next to the words: LABS / BBC recorded acoustic instruments up, FLEX by its pack's tier
    (Core / Essential up, General MIDI style packs well down: data/flex/quality.json)."""
    key = (e.get("source"), e.get("name"), e.get("pack"), e.get("kind"))
    if key in _QB:
        return _QB[key]
    b = 0.0
    try:
        FX = sys.modules.get("flh_flex")
        if FX is None:
            import flh_flex as FX
        q = FX.quality_data()
        if e.get("source") in ("LABS", "BBC"):
            b = 1.0 if e.get("kind") in (q.get("labs_acoustic") or []) else 0.5
        elif e.get("source") == "FLEX" and e.get("pack") and "pack" not in (e.get("tags") or []):
            tier, _ = FX.pack_tier(e["pack"], e["name"], q)
            b = (tier - 2) * 1.0 if tier > 0 else -3.0
            b += 0.5 * FX.name_quality(e["name"], e.get("kind"), q)
    except Exception:
        b = 0.0
    _QB[key] = b
    return b


def _flex_flags():
    """{FLEX pack: why} for packs whose sounds aren't on this PC / made no sound here (flh_flex.pack_flags)."""
    try:
        FX = sys.modules.get("flh_flex")
        return FX.pack_flags() if FX is not None and FX.H is not None and hasattr(FX, "pack_flags") else {}
    except Exception:
        return {}


def _silent_packs():
    try:
        return (H["_state"]() or {}).get("silent_packs") or {}
    except Exception:
        return {}


def _compact(e, owned, silent=None):
    d = {k: v for k, v in e.items() if k not in ("plugin",) and v not in (None, [], "")}
    d["owned"] = owned
    pk = (e.get("load") or {}).get("pack") or e.get("pack")
    if silent and pk and pk in silent:
        d["may_load_silent"] = f"rendered silent on this PC ({silent[pk].get('seen')}): pack probably unsupported"
    return d


def fl_sound_catalog(query=None, kind=None, refresh=False, limit=15, include_trial=False, type=None, genre=None,
                     bpm=None, key=None, full=False):
    """Every sound the user has, with how to load it and whether it's owned. No query/kind: a summary.
    Samples (one-shots/loops) when kind is a sample role (kick, 808, hat, loop ...) or type='sample'."""
    t = str(type or "").lower().rstrip("s")
    skind = _sample_kind(kind)
    if not skind and not kind and t == "sample" and query:     # 'HQ Rock snare': the role named in the query filters
        named = [k for k in (_sample_kind(w) for w in _tokens(query)) if k]
        if len(set(named)) == 1:
            skind = named[0]
    if refresh:
        _SAMPLE_MEMO.pop("cat", None)
    if t == "sample" or (skind and t != "instrument" and kind not in ("perc", "fx", "vocal")):
        if refresh:
            _sample_catalog(True)
        if not skind and not query and not bpm and not key:
            return dict(_samples_summary(), hint="kind='kick' query='dusty punchy' genre='lofi' for ranked samples")
        r = sample_search(skind, query, genre, bpm, key, limit=int(limit or 15), strict=True, full=bool(full))
        r.update(type="sample", query=query)
        if not r["results"]:
            r["note"] = ("no matching samples on this PC: fl_sound_catalog(type='sample') shows the folders searched; "
                         "add your own with flh_profile(set={'sample_folders': ['D:\\Samples']})")
        return r
    cat = _catalog(bool(refresh)); ents = cat["entries"]
    index_note = take_note()
    status = {}
    def own(e):
        if e["source"] == "FL instruments":                   # PC-VERIFY: FL's factory instrument presets play in
            return True                                       # DirectWave's player mode in every edition
        key = e.get("plugin") or e["name"]
        if key not in status:
            status[key] = sound_ok(key)
        return status[key]
    if not query and not kind:
        by_src, by_kind = {}, {}
        for e in ents:
            by_src[e["source"]] = by_src.get(e["source"], 0) + 1
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        trial = sorted({e["name"] for e in ents if own(e) == "trial"})
        try:
            samples = _samples_summary()
            samples = {**({"index_note": samples["index_note"]} if samples.get("index_note") else {}),
                       "count": samples["count"], "by_role": samples["by_role"],
                       "folders": [r["folder"] for r in samples["roots"]]}
        except Exception as e:
            samples = {"error": str(e)[:100]}
        return {"sounds": len(ents), "samples": samples, "by_source": by_src, "by_kind": by_kind, "sources": cat["sources"],
                "trial_present": trial[:20], "cache": _cache_path(), "cached": cat["_cached"],
                "built": time.strftime("%Y-%m-%d %H:%M", time.localtime(cat["built"])),
                "hint": "query='soft choir' or kind='strings' for a ranked list; kind='kick' (or type='sample') for samples",
                **({"index_note": index_note} if index_note else {})}
    want_kind = _kind_of(kind) if kind else None
    words = [w for w in _words(query) if w not in ("a", "an", "the", "and", "with", "for", "sound", "sounds")]
    hits = []
    for e in ents:
        if want_kind and e["kind"] != want_kind:
            continue
        o = own(e)
        if o == "trial" and not include_trial:
            continue
        sc = _score(e, words, o) if words else (1.0 + (0.5 if o is True else 0))
        if sc > 0:
            sc += _quality_bonus(e)
            if o == "trial":
                sc -= 3
            elif o == "unknown":
                sc -= 0.5
            hits.append((sc, e, o))
    sil = _silent_packs()
    if sil:                                                       # packs that rendered silent here go last
        hits = [(sc - (5 if ((e.get("load") or {}).get("pack") or e.get("pack")) in sil else 0), e, o)
                for sc, e, o in hits]
    fxf = _flex_flags() if any(e.get("source") == "FLEX" for _, e, _ in hits) else {}
    unav = lambda e: bool(fxf) and e.get("source") == "FLEX" and e.get("pack") in fxf
    if fxf:                                                       # FLEX packs not downloaded / silent here: last
        hits = [(sc - (6 if unav(e) else 0), e, o) for sc, e, o in hits]
    hits.sort(key=lambda h: (unav(h[1]), -h[0], h[1]["name"].lower()))
    n = int(limit or 15)

    def row(sc, e, o):
        d = dict(_compact(e, o, sil), score=round(sc, 1))
        if e.get("source") == "FLEX" and e.get("pack") in fxf:
            d["installed"] = False
            d["may_load_silent"] = fxf[e["pack"]]
        return d
    res = {"query": query, "kind": want_kind, "found": len(hits),
           "results": [row(sc, e, o) for sc, e, o in hits[:n]], "cached": cat["_cached"]}
    if not include_trial:
        res["note"] = "trial plugins left out (include_trial=true shows them)"
    if index_note:
        res["index_note"] = index_note
    if skind and t != "instrument":                                   # perc / fx / vocal: samples too
        try:
            sr = sample_search(skind, query, genre, bpm, key, limit=min(8, n), strict=True, full=bool(full))
            res["samples"] = sr["results"]
            if sr.get("index_note"):
                res["samples_index_note"] = sr["index_note"]
        except Exception:
            pass
    return res


# ----------------------------------------------------------------------------- samples (one-shots, loops)
SAMPLE_EXTS = (".wav", ".flac", ".ogg", ".mp3", ".aif", ".aiff")
MEDIA_FEATURE_EXTS = (".mp3", ".m4a") if sys.platform == "win32" else ()     # Windows Media Foundation decodes
FEATURE_EXTS = (".wav", ".ogg", ".flac", ".aif", ".aiff") + MEDIA_FEATURE_EXTS   # measured (pitch, length, tone, level)
SAMPLE_CACHE_VERSION = 2                 # 2: alphabetical walk + not_indexed packs
SAMPLE_MAX_FILES = 60000                 # per index (a huge sample drive is cut off, and the result says so)
SAMPLE_MAX_PER_ROOT = 30000
FEATURE_CACHE_MAX = 12000                # cached audio features (least recently used dropped first)
FEATURE_VER = 4                          # bump when sample_features changes (3: Ogg-in-WAV/.ogg read, 4: rms_db)
FEATURE_READ_S = 3.0                     # seconds of audio read per sample for its features
SAMPLE_KINDS = ("kick", "808", "snare", "clap", "rim", "hat", "openhat", "ride", "crash", "perc", "shaker", "tom",
                "fx", "texture", "vocal", "loop", "oneshot")
SAMPLE_ALIASES = {"hihat": "hat", "hi-hat": "hat", "hh": "hat", "closed hat": "hat", "closedhat": "hat",
                  "hats": "hat", "open hat": "openhat", "ohh": "openhat", "open": "openhat", "sub": "808",
                  "sub bass": "808", "808s": "808", "cymbal": "crash", "cymbals": "crash", "kicks": "kick",
                  "snares": "snare", "claps": "clap", "rimshot": "rim", "toms": "tom", "percussion": "perc",
                  "vinyl": "texture", "noise": "texture", "crackle": "texture", "riser": "fx", "impact": "fx",
                  "sweep": "fx", "sfx": "fx", "vox": "vocal", "vocals": "vocal", "chop": "vocal",
                  "vocal chop": "vocal", "loops": "loop", "drum loop": "loop", "melodic loop": "loop",
                  "one-shot": "oneshot", "one shot": "oneshot", "instrument": "oneshot", "tamb": "shaker"}
_W = lambda s: tuple(s.split("|"))
# role -> words (single tokens or phrases), several languages; order = priority inside one name
SAMPLE_WORDS = [
    ("openhat", _W("open hat|openhat|open hh|open hihat|open hi hat|ohh|ohat|hat open|hh open|oh hat|openhh")),
    ("rim", _W("rim|rimshot|rims|sidestick|side stick|cross stick|crossstick|rimclick")),
    ("clap", _W("clap|claps|clp|handclap|handclaps|palmas|aplauso|klatsch|snap|snaps|fingersnap|clapping")),
    ("snare", _W("snare|snares|snr|sd|caisse claire|rullante|redoblante|tarola|caja|schnarre|snaredrum")),
    ("kick", _W("kick|kicks|kik|kck|bd|bassdrum|bass drum|kickdrum|cassa|bombo|grosse caisse|grancassa")),
    ("hat", _W("hat|hats|hh|ch|hihat|hihats|hi hat|chh|closed hat|closedhat|charleston|hatclosed|tss")),
    ("ride", _W("ride|rides|ridebell|ride bell")),
    ("crash", _W("crash|crashes|cymbal|cymbals|cym|splash|china|becken|piatto|platillo")),
    ("tom", _W("tom|toms|floortom|floor tom|tomtom|rack tom")),
    ("shaker", _W("shaker|shakers|shk|tamb|tambourine|tamborine|tambourin|maraca|maracas|cabasa|pandeiro")),
    ("perc", _W("perc|percs|percussion|conga|congas|bongo|bongos|cowbell|cow bell|woodblock|wood block|clave|claves|"
                "timbale|timbales|agogo|guiro|triangle|tabla|djembe|cajon|block|metal hit|metallic")),
    ("808", _W("808|808s|sub|subbass|sub bass|bass808|reese|subs")),
    ("fx", _W("fx|sfx|riser|risers|rise|uplifter|upsweep|downlifter|downsweep|sweep|sweeps|impact|impacts|whoosh|"
              "swoosh|reverse|reversed|transition|zap|laser|explosion|drop fx|noise sweep|uplift")),
    ("texture", _W("vinyl|crackle|crackles|hiss|noise|tape noise|texture|textures|atmos|atmosphere|ambience|"
                   "room tone|roomtone|foley|field|rain|static|dust|lofi noise")),
    ("vocal", _W("vocal|vocals|vox|voice|voices|chop|chops|acapella|adlib|adlibs|ad lib|chant|shout|hey|yeah|"
                 "phrase|stimme|voix|voz|voce|vocalchop")),
]
DRUM_CONTEXT = _W("drum|drums|kit|kits|beat|beats|break|breaks|breakbeat|top|tops|groove|percussion|perc|batteria|"
                  "bateria|schlagzeug|batterie")
LOOP_WORDS = _W("loop|loops|lp|break|breaks|breakbeat|groove|grooves|bpm")
ONESHOT_WORDS = _W("one shot|oneshot|one shots|oneshots|one-shot|hit|hits|single")
INST_WORDS = [  # instrument family -> words (one-shots and melodic loops)
    ("piano", _W("piano|pianos|keys|rhodes|epiano|e piano|wurli|wurlitzer|clav|upright|grand")),
    ("organ", _W("organ|organs|harmonium")),
    ("guitar", _W("guitar|guitars|gtr|nylon|strum|acoustic guitar")),
    ("bass", _W("bass|basses|bassline|slap bass|upright bass")),
    ("synth", _W("synth|synths|saw|square|analog|fm|stab|stabs|chip|arp|arps")),
    ("pluck", _W("pluck|plucks|plucked|harp|pizz|pizzicato|koto|marimba|kalimba|mallet|mallets")),
    ("lead", _W("lead|leads")),
    ("pad", _W("pad|pads|drone|drones|swell|swells")),
    ("bell", _W("bell|bells|glock|glockenspiel|chime|chimes|music box|celesta|vibes|vibraphone")),
    ("brass", _W("brass|horn|horns|trumpet|trombone|sax|saxophone|tuba")),
    ("strings", _W("string|strings|violin|viola|cello|orchestral|orchestra|ensemble")),
    ("flute", _W("flute|flutes|woodwind|clarinet|oboe|recorder|whistle|pan flute")),
    ("chord", _W("chord|chords|progression")),
]
SAMPLE_TAG_WORDS = {
    "punchy": _W("punch|punchy|tight|knock|knocking|slap|slappy|thump|snappy"),
    "dusty": _W("dusty|dust|lofi|lo fi|vinyl|tape|crunchy|crunch|grit|gritty|bitcrush|bitcrushed|sp1200|sp 1200|"
                "mpc|vintage|old|worn|cassette|sampled|chopped"),
    "clean": _W("clean|crisp|clear|pure|hifi|hi fi|polished"),
    "dark": _W("dark|deep|low|sub|dull|muffled|warm"),
    "bright": _W("bright|crisp|shiny|air|airy|sizzle|sizzly|high|sparkle"),
    "hard": _W("hard|heavy|aggressive|distorted|dist|dirty|big|fat|slam|smash|loud|banger|hit hard|clipped"),
    "soft": _W("soft|light|gentle|brushed|brush|quiet|ghost|mellow|subtle|smooth"),
    "long tail": _W("long|tail|boom|decay|trail|verb|reverb|sustain|sustained|ring|ringing"),
    "acoustic": _W("acoustic|live|real|organic|natural|recorded"),
    "electronic": _W("909|808|707|606|analog|analogue|electro|edm|synth|synthetic|digital"),
}
TAG_OPPOSITE = {"punchy": "soft", "soft": "hard", "hard": "soft", "bright": "dark", "dark": "bright",
                "clean": "dusty", "dusty": "clean", "long tail": "short", "warm": "thin", "full": "thin",
                "deep": "thin", "fat": "thin"}
NOTE_PCS = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
PC_NAMES = "C C# D Eb E F F# G Ab A Bb B".split()
_SAMPLE_MEMO = {}                        # {"cat": (sig_time, cat), "feat": {...}, "feat_dirty": bool}


@functools.lru_cache(maxsize=200000)
def _tokens_t(text):
    return tuple(_tokens_raw(text))


def _tokens(text):
    """Words and numbers of a name (cached: the same folder names come up thousands of times a search)."""
    return list(_tokens_t(str(text or "")))


def _tokens_raw(text):
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text or ""))
    t = re.sub(r"(\d)([A-Za-z])", r"\1 \2", t)
    t = re.sub(r"([A-Za-z])(\d)", r"\1 \2", t).lower()
    return re.findall(r"[a-z]+|\d+", t)


def _has(words, toks, joined):
    for w in words:
        if " " in w:
            if f" {w} " in joined:
                return w
        elif w in toks:
            return w
    return None


@functools.lru_cache(maxsize=200000)
def _level_role(text):
    """(role or None, loop?, oneshot?, drum context?, instrument family or None) of one name level (cached)."""
    return _level_role_raw(text)


def _level_role_raw(text):
    toks = _tokens(text); ts = set(toks); j = " " + " ".join(toks) + " "
    loop = bool(_has(LOOP_WORDS, ts, j))
    one = bool(_has(ONESHOT_WORDS, ts, j))
    drum = bool(_has(DRUM_CONTEXT, ts, j))
    role = None
    for r, words in SAMPLE_WORDS:
        if _has(words, ts, j):
            if r == "808" and any(_has(w, ts, j) for rr, w in SAMPLE_WORDS[:11] if rr not in ("808",)):
                continue                                  # "808 Kick" / "TR-808 Snare": the drum word wins
            role = r
            break
    if role == "808" and ts & {"oh", "ohh", "oht"}:
        role = "openhat"                                  # '808 OH' = the 808's open hat
    if role == "hat" and ("open" in ts or "oh" in ts):
        role = "openhat"
    inst = next((fam for fam, words in INST_WORDS if _has(words, ts, j)), None)
    if role == "808" and inst == "bass":
        inst = None
    if role is None and inst == "bass" and ("sub" in ts):
        role = "808"
    return role, loop, one, drum or role in ("kick", "snare", "clap", "hat", "openhat", "rim", "perc", "shaker",
                                             "tom", "ride", "crash"), inst


def _bpm_key(stem):
    """(bpm or None, key or None) from a sample's file name: '90bpm', '90 BPM', '_140_', 'Cm', 'F#min', 'A minor'."""
    s = str(stem)
    bpm = None
    m = re.search(r"(?<!\d)(\d{2,3})\s*[-_ ]?\s*bpm(?![a-z])", s, re.I) or \
        re.search(r"(?<![a-z])bpm\s*[-_ ]?\s*(\d{2,3})(?!\d)", s, re.I)   # '90bpm_Am': '_' is a word char
    if m and 50 <= int(m.group(1)) <= 220:
        bpm = int(m.group(1))
    elif re.search(r"(?:^|[ _-])(\d{2,3})(?:[ _-]|$)", s):
        for g in re.findall(r"(?:^|[ _-])(\d{2,3})(?=[ _-]|$)", s):
            if 60 <= int(g) <= 200 and not re.search(r"(?:^|[ _-])" + g + r"\s*(hz|k|ms|in|inch)\b", s, re.I):
                bpm = int(g)
                break
    key = None
    m = re.search(r"(?:^|[\s_\-(\[])([A-G])\s?(#|b|sharp|flat)?\s?(maj|major|min|minor|m)?(?=$|[\s_\-)\].])", s)
    if m and (m.group(2) or m.group(3)):
        pc = (NOTE_PCS[m.group(1).lower()] + (1 if m.group(2) in ("#", "sharp") else -1 if m.group(2) in ("b", "flat")
                                               else 0)) % 12
        mode = "minor" if (m.group(3) or "").lower() in ("m", "min", "minor") else "major"
        key = f"{PC_NAMES[pc]} {mode}" if m.group(3) else PC_NAMES[pc]
    if key is None:                                        # a one-shot's root note: 'Rhodes C3', 'Sub_A', '808 F#1'
        m = re.search(r"(?:^|[\s_\-(\[])([A-G])(#|b)?(-?\d)?\s*[)\]]?$", s)
        if m:
            key = PC_NAMES[(NOTE_PCS[m.group(1).lower()] + (1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0)) % 12]
    return bpm, key


_CLASSIFY_MEMO = {}                                               # (path, root) -> result: 40k samples were classified
                                                                  # again on every lookup (PC 2026-10-22: ~10 s a song)


def classify_sample(path, root=None):
    """{role, sub, inst, tags, bpm, key} for a sample from its file name and folders (no audio read)."""
    k = (path, root)
    got = _CLASSIFY_MEMO.get(k)
    if got is None:
        if len(_CLASSIFY_MEMO) > 400000:
            _CLASSIFY_MEMO.clear()
        got = _CLASSIFY_MEMO[k] = _classify_sample(path, root)
    return dict(got, tags=list(got["tags"])) if isinstance(got.get("tags"), list) else dict(got)


def _classify_sample(path, root=None):
    stem = os.path.splitext(os.path.basename(path))[0]
    rel = os.path.relpath(os.path.dirname(path), root) if root else os.path.dirname(path)
    folders = [] if rel in (".", "") else [f for f in re.split(r"[\\/]", rel) if f][::-1]
    levels = [stem] + folders[:4]
    role = inst = None; loop = one = drum = False
    for i, lv in enumerate(levels):
        r, lp, on, dr, ins = _level_role(lv)
        drum = drum or dr; inst = inst or ins
        if on:
            one = True
        if lp and not one and (i <= 1 or role is None):
            loop = True
        if role is None and r:
            role = r
        if role and i >= 1:
            break
    if role == "808":                                       # 'Drum Samples/Hat/FL 808 CH.wav' is a hat, not a bass
        for lv in folders[:3]:
            fr = _level_role(lv)[0]
            if fr in ("kick", "snare", "clap", "rim", "hat", "openhat", "ride", "crash", "perc", "shaker", "tom"):
                role = fr
                break
    if role == "hat" and {"open", "oh", "ohh"} & set(_tokens(stem)):
        role = "openhat"                                    # 'Hats/Open 1.wav'
    bpm, key = _bpm_key(stem)
    if bpm and not one and role not in ("fx", "texture"):
        loop = True if role is None or "loop" in _tokens(stem) or "bpm" in stem.lower() else loop
    blob = " " + " ".join(_tokens(" ".join(levels))) + " "
    ts = set(blob.split())
    tags = sorted(t for t, words in SAMPLE_TAG_WORDS.items() if _has(words, ts, blob))
    sub = None
    if loop:
        sub = "drum" if (drum or role in ("kick", "snare", "clap", "hat", "openhat", "perc", "shaker", "tom", "rim",
                                           "ride", "crash")) and not (inst and role is None and not drum) else \
            ("vocal" if role == "vocal" else "melodic" if inst or key else "drum" if drum else None)
        role = "loop"
    elif role is None and inst:
        role = "oneshot"
    if role not in (None, "oneshot", "loop"):
        inst = None                                        # 'Analog Kick' is a kick, not a synth
    root = None
    if role != "loop" and key:                             # a one-shot names its pitch, not a key
        root, key = key.split()[0], None
    if role == "fx":
        sub = next((w for w in ("riser", "impact", "sweep", "reverse", "downlifter") if w in ts or w + "s" in ts),
                   "uplifter" if "uplifter" in ts else None)
    return {"role": role or "other", "sub": sub, "inst": inst, "tags": tags, "bpm": bpm, "key": key, "root": root}


def _factory_packs():
    # PC-VERIFY: FL 2026's factory samples are under <install>\Data\Patches\Packs (Drums\Kicks ...)
    if os.environ.get("FLH_FACTORY_PACKS"):
        return os.environ["FLH_FACTORY_PACKS"]
    try:
        return os.path.join(os.path.dirname(H["fl_exe"]()), "Data", "Patches", "Packs")
    except Exception:
        return None


def _registry_sample_folders():
    """Folders from FL's browser 'extra search folders' setting (Windows registry, read-only; PC-VERIFY: the value
    names). Any Image-Line value whose name mentions search/browser/folder/path and whose text is an existing folder."""
    if os.name != "nt":
        return []
    # PC-VERIFY: which registry value holds Options > File settings > Browser extra search folders (fl_settings_diff)
    try:
        dump = H["_reg_dump"]("")
    except Exception:
        return []
    out = []
    for key, vals in dump.items():
        for name, (typ, val) in vals.items():
            if typ not in (1, 2, 7) or not re.search(r"search|browser|extra|folder|path", f"{key} {name}", re.I):
                continue
            for v in (val if isinstance(val, list) else re.split(r"[;\r\n|]+", str(val))):
                v = os.path.expandvars(str(v).strip().strip('"'))
                if len(v) > 3 and (":\\" in v or v.startswith("\\\\")) and os.path.isdir(v):
                    out.append(os.path.normpath(v))
    return list(dict.fromkeys(out))[:30]


def sample_roots():
    """[(label, folder)] where samples are looked for (existing folders only)."""
    roots = []
    fac = _factory_packs()
    if fac:
        roots.append(("FL factory", fac))
    for u in _user_dirs():
        roots += [("FL user packs", os.path.join(u, "Packs")), ("FL user audio", os.path.join(u, "Audio"))]
    roots += [("FL browser folder", p) for p in _registry_sample_folders()]
    try:
        prof = flh_store.profile_get() if H else {}
    except Exception:
        prof = {}
    for p in prof.get("sample_folders") or []:
        roots.append(("profile sample_folders", os.path.expandvars(os.path.expanduser(str(p)))))
    seen, out = set(), []
    for lab, p in roots:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen and os.path.isdir(p):
            seen.add(k); out.append((lab, p))
    return out


SKIP_DIRS = {"recorded", "rendered", "rendered audio", "recordings", "backup", "backups", "__macosx", ".git"}


def _walk_samples(root, cap, missed=None, tick=None):
    """Sample files under root, folders in alphabetical order (depth first), at most cap. missed: a list that gets
    the folders left unread when the cap is hit (so the result can say which packs are missing). tick(out, frac):
    called after each folder with the files so far and the part of the walk done (by top-level folders, or by the
    cap when that's further)."""
    out = []; stack = [(root, 0, 0)]; n_top = [1]
    while stack and len(out) < cap:
        d, depth, top = stack.pop()
        try:
            it = list(os.scandir(d))
        except OSError:
            continue
        subs = []
        for e in sorted(it, key=lambda e: e.name.lower()):
            try:
                if e.is_dir(follow_symlinks=False):
                    if depth < 8 and e.name.lower() not in SKIP_DIRS:
                        subs.append((e.path, depth + 1, len(subs) if depth == 0 else top))
                elif e.name.lower().endswith(SAMPLE_EXTS):
                    st = e.stat()
                    out.append((e.path, st.st_size, round(st.st_mtime, 1)))
                    if len(out) >= cap:
                        if missed is not None:
                            missed.append(d)
                        break
            except OSError:
                continue
        if len(out) >= cap:
            if missed is not None:
                missed += [x[0] for x in subs]
            break
        if depth == 0:
            n_top[0] = max(1, len(subs))
        stack += subs[::-1]                                     # popped in alphabetical order
        if tick is not None:
            tick(out, max(min(1.0, len(out) / max(1, cap)), (top if depth else 0) / n_top[0]))
    if missed is not None:
        missed += [x[0] for x in stack]
    return out


def _samples_sig():
    sig = {}
    for _, r in sample_roots():
        sig.update(_dir_sig(r, 3))
    return sig or {"(no sample folders)": None}


def _samples_cache_path():
    return os.path.join(os.path.dirname(H["STATE"]), "samples.json")


def _sample_entry(p, size, mt, r, lab):
    c = classify_sample(p, r)
    rel = os.path.relpath(p, r)
    pack = rel.split(os.sep)[0] if os.sep in rel else ""
    e = {"p": p, "n": os.path.splitext(os.path.basename(p))[0], "r": c["role"], "src": lab, "pk": pack,
         "z": size, "m": mt}
    for k, kk in (("sub", "s"), ("inst", "i"), ("bpm", "b"), ("key", "k"), ("root", "o")):
        if c.get(k):
            e[kk] = c[k]
    if c["tags"]:
        e["t"] = c["tags"]
    return e


def _build_samples(b=None):
    """The sample index. b (index_build's build): progress per folder walked (with an estimate) and the entries so
    far in b['partial'] (classified as they're found), so a search during a first scan can use what's there."""
    t0 = time.time(); entries = []; roots = []; truncated = False
    rl = sample_roots(); n = max(1, len(rl))
    if b is not None:
        b["partial"] = entries
    for i, (lab, r) in enumerate(rl):
        left = SAMPLE_MAX_FILES - len(entries)
        if left <= 0:
            truncated = True; break
        missed = []; done = [0]

        def tick(files, frac, i=i, r=r, lab=lab, done=done):
            for p, size, mt in files[done[0]:]:
                entries.append(_sample_entry(p, size, mt, r, lab))
            done[0] = len(files)
            build_tick(b, 100.0 * (i + frac) / n)
        files = _walk_samples(r, min(SAMPLE_MAX_PER_ROOT, left), missed, tick if b is not None else None)
        if len(files) >= min(SAMPLE_MAX_PER_ROOT, left):
            truncated = True
        roots.append({"source": lab, "folder": r, "files": len(files)})
        if missed:                                         # which packs (top folders) weren't (fully) indexed
            packs = sorted({os.path.relpath(x, r).split(os.sep)[0] for x in missed if x != r})
            roots[-1]["not_indexed"] = packs[:12] + ([f"+{len(packs) - 12} more"] if len(packs) > 12 else [])
        for p, size, mt in files[done[0]:]:
            entries.append(_sample_entry(p, size, mt, r, lab))
        done[0] = len(files)
    return {"version": SAMPLE_CACHE_VERSION, "built": time.time(), "sig": _samples_sig(), "roots": roots,
            "truncated": truncated, "entries": entries, "took_s": round(time.time() - t0, 2)}


def _sample_catalog(refresh=False):
    _NOTE_TL.note = None
    p = _samples_cache_path(); memo = _SAMPLE_MEMO.get("cat")
    if not refresh and _memo_fresh(memo):
        memo[1]["_cached"] = True
        return memo[1]                                 # several queries in one go: no re-check of the folders
    cat = None
    if not refresh and os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                cat = json.load(f)
        except (OSError, ValueError):
            cat = None
        if not cat or cat.get("version") != SAMPLE_CACHE_VERSION or time.time() - cat.get("built", 0) > MAX_AGE_S \
                or cat.get("sig") != _samples_sig():
            cat = None
        else:
            cat["_cached"] = True
    if cat is None:
        def run(b):
            c = _build_samples(b)
            _own_write(p, c)
            c["_cached"] = False
            _SAMPLE_MEMO["cat"] = (time.time(), c)
            return c
        cat, still = index_build("samples", "sample index", run, SAMPLES_WAIT_S)
        if cat is None:                                   # a first scan still running: what's indexed so far
            part = list(still.get("partial") or [])
            note = build_note(still, partial=True)
            _NOTE_TL.note = note
            return {"version": SAMPLE_CACHE_VERSION, "built": time.time(), "sig": None, "roots": [], "truncated": True,
                    "entries": part, "partial": True, "index_note": note, "_cached": False}
        return cat
    _SAMPLE_MEMO["cat"] = (time.time(), cat)
    return cat


# ---- audio features (lazy, cached) ------------------------------------------------------------------------------
def _feat_path():
    return os.path.join(os.path.dirname(H["STATE"]), "sample_features.json")


def _feat_cache():
    fc = _SAMPLE_MEMO.get("feat")
    if fc is None:
        try:
            with open(_feat_path(), encoding="utf-8") as f:
                fc = json.load(f)
        except (OSError, ValueError):
            fc = {}
        _SAMPLE_MEMO["feat"] = fc
    return fc


_FEAT_LOCK = threading.RLock()                     # the background warm-up and a search share the cache


def _feat_save():
    with _FEAT_LOCK:
        _feat_save_locked()


def _feat_save_locked():
    if not _SAMPLE_MEMO.get("feat_dirty"):
        return
    fc = _SAMPLE_MEMO.get("feat") or {}
    if len(fc) > FEATURE_CACHE_MAX:                    # size cap: drop the least recently used
        keep = sorted(fc.items(), key=lambda kv: -kv[1].get("u", 0))[:int(FEATURE_CACHE_MAX * 0.8)]
        fc = dict(keep); _SAMPLE_MEMO["feat"] = fc
    try:
        _own_write(_feat_path(), fc)
    except Exception:
        pass
    _SAMPLE_MEMO["feat_dirty"] = False


def _is_ogg(path):
    """FL Studio's compressed WAVs (fmt tag 'Og', an Ogg Vorbis stream inside) and plain .ogg files."""
    try:
        import ogg_vorbis_lite as OV
        return OV.is_ogg_wav(path)
    except Exception:
        return False


def _wav_head(path, seconds=FEATURE_READ_S):
    """(mono float array, rate, total seconds) from the first `seconds` of a WAV (16/24/32-bit PCM, 32-bit float)."""
    import numpy as np
    if _is_ogg(path):                                         # FL's compressed factory samples / .ogg files
        import ogg_vorbis_lite as OV
        y, rate = OV.decode(path, max_seconds=seconds)
        try:
            frames = OV.info(path)["frames"]
        except Exception:
            frames = None
        total = (frames / float(rate)) if frames and rate else len(y) / float(rate or 1)
        return y.astype(np.float64).mean(axis=1), rate, total
    with open(path, "rb") as f:
        head = f.read(1 << 16)
    if (head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC")) or \
            (head[:4] not in (b"RIFF", b"RF64", b"BW64", b"fLaC") and path.lower().endswith(MEDIA_FEATURE_EXTS)):
        if os.path.getsize(path) > 40 << 20:
            raise ValueError("file too long to analyse")
        import fl_audio_edit as AE                            # AIFF here; MP3 / M4A with Windows' own decoders
        y, rate, _b, _n = AE.load_ex(path)
        total = len(y) / float(rate or 1)
        return y[:int(seconds * rate)].astype(np.float64).mean(axis=1), rate, total
    if head[:4] == b"fLaC":                                   # FLAC samples: decoded whole (one-shots are short)
        if os.path.getsize(path) > 40 << 20:
            raise ValueError("FLAC too long to analyse")
        import flh_review
        y, rate, _ = flh_review.flac_load(path)
        total = len(y) / float(rate or 1)
        return y[:int(seconds * rate)].astype(np.float64).mean(axis=1), rate, total
    with open(path, "rb") as f:
        head = f.read(1 << 16)
        if head[:4] not in (b"RIFF", b"RF64", b"BW64") or head[8:12] != b"WAVE":
            raise ValueError("not a WAV")
        i, fmt, data_off, data_len, ds64_len = 12, None, None, None, None
        while i + 8 <= len(head):
            cid, n = head[i:i + 4], struct.unpack("<I", head[i + 4:i + 8])[0]
            if cid == b"fmt ":
                fmt = struct.unpack("<HHIIHH", head[i + 8:i + 24])
            elif cid == b"ds64" and n >= 24:                  # RF64: the real data size
                ds64_len = struct.unpack("<Q", head[i + 16:i + 24])[0]
            elif cid == b"data":
                data_off, data_len = i + 8, (ds64_len if n == 0xFFFFFFFF and ds64_len else n); break
            i += 8 + n + (n & 1)
        if not fmt or data_off is None:
            raise ValueError("no fmt/data chunk")
        tag, ch, rate, _, block, bits = fmt
        if tag == 0xFFFE:
            tag = 3 if bits == 32 and b"\x03\x00\x00\x00\x00\x00\x10\x00" in head[:200] else 1
        ch = max(1, ch); block = block or ch * bits // 8
        total = data_len / float(block * rate) if rate else 0.0
        f.seek(data_off)
        raw = f.read(min(data_len, int(seconds * rate) * block))
    if tag == 3 and bits == 32:
        x = np.frombuffer(raw[:len(raw) // 4 * 4], "<f4").astype(np.float64)
    elif bits == 16:
        x = np.frombuffer(raw[:len(raw) // 2 * 2], "<i2") / 32768.0
    elif bits == 24:
        b = np.frombuffer(raw[:len(raw) // 3 * 3], np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v) / float(1 << 23)
    elif bits == 32:
        x = np.frombuffer(raw[:len(raw) // 4 * 4], "<i4") / 2147483648.0
    elif bits == 8:
        x = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128) / 128.0
    else:
        raise ValueError(f"{bits}-bit")
    x = x[:len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    return x, rate, total


def sample_features(path):
    """Quick features of a sample as plain numbers (see _sample_features_raw)."""
    return _plain(_sample_features_raw(path))


def _plain(d):
    """numpy scalars -> Python numbers (a numpy number compared with [] raises 'truth value of an empty array';
    PC 2026-10-21: FL's compressed factory kicks/hats left channels empty)."""
    out = {}
    for k, v in d.items():
        if hasattr(v, "item") and not isinstance(v, (str, bytes)):
            try:
                v = v.item()
            except (ValueError, TypeError):
                v = None
        out[k] = v
    return out


def _sample_features_raw(path):
    """Quick features of a sample (WAV): dur, attack_ms, decay_s, centroid, rolloff, sub (energy share < 120 Hz),
    noise (spectral flatness 0..1), floor_db, pitch (MIDI note, tonal sounds only) -> also used for tags."""
    import numpy as np
    x, fs, total = _wav_head(path)
    out = {"dur": round(total, 3)}
    if len(x) < 64 or not fs:
        return out
    a = np.abs(x); peak = float(a.max())
    if peak <= 1e-6:
        out["silent"] = True
        return out
    out["peak_db"] = round(20 * math.log10(peak), 1)
    act = x[np.abs(x) > peak * 0.05]                       # loudness of the sounding part (not its silence)
    out["rms_db"] = round(20 * math.log10(max(1e-9, float(np.sqrt(np.mean(act * act))) if len(act) else peak)), 1)
    hop = max(1, int(fs * 0.01))                           # 10 ms peak envelope (a 50 Hz cycle fits in it)
    nfr = len(x) // hop
    env = a[:nfr * hop].reshape(nfr, hop).max(axis=1) if nfr else np.array([peak])
    on_s = int(np.argmax(a > peak * 0.01))
    pk_s = on_s + int(np.argmax(a[on_s:on_s + int(fs * 0.5)]))
    out["attack_ms"] = round((pk_s - on_s) / fs * 1000.0, 1)
    on, pk = on_s // hop, min(len(env) - 1, pk_s // hop)
    thr = env[pk] * 0.01                                   # -40 dB
    below = np.nonzero(env[pk:] < thr)[0]
    out["decay_s"] = round((below[0] if len(below) else len(env) - pk) * hop / fs, 3) if total <= FEATURE_READ_S \
        or len(below) else round(total - pk * hop / fs, 3)
    out["floor_db"] = round(20 * math.log10(max(1e-9, float(env[-max(1, len(env) // 10):].mean()) / env[pk])), 1)
    s0 = on * hop; seg = x[s0:s0 + int(fs * 0.25)]
    n = 1 << max(8, min(14, int(math.log2(max(256, len(seg))))))
    seg = np.pad(seg, (0, max(0, n - len(seg))))[:n] * np.hanning(n)
    P = np.abs(np.fft.rfft(seg)) ** 2 + 1e-18
    f = np.fft.rfftfreq(n, 1.0 / fs)
    tot = float(P.sum())
    out["centroid"] = round(float((f * P).sum() / tot))
    c = np.cumsum(P)
    out["rolloff"] = round(float(f[min(len(f) - 1, int(np.searchsorted(c, 0.95 * c[-1])))]))
    out["sub"] = round(float(P[f < 120].sum() / tot), 3)
    band = P[(f > 40) & (f < min(16000, fs / 2 - 1))]
    out["noise"] = round(float(np.exp(np.mean(np.log(band))) / np.mean(band)), 3) if len(band) else 0.0
    if out["noise"] < 0.2 or out["sub"] > 0.4:              # tonal (or a distorted 808): pitch on the steady body
        f0s = []                                             # several windows: an 808's attack sweeps down, so the
        rms_pk = None                                        # body (what a held note sounds like) decides
        for st in (0.04, 0.3, 0.55, 0.8):
            t0 = s0 + int(fs * st); y = x[t0:t0 + int(fs * 0.25)]
            if len(y) <= fs * 0.06:
                break
            rms = float(np.sqrt(np.mean(y * y)))
            rms_pk = rms_pk or rms
            if rms < rms_pk * 0.1:                           # -20 dB: the tail is too quiet to trust
                break
            y = y - y.mean(); r = np.fft.irfft(np.abs(np.fft.rfft(y, 2 * len(y))) ** 2)[:len(y)]
            lo, hi = int(fs / 1000.0), int(fs / 28.0)
            neg = np.nonzero(r[:hi] < 0)[0]                  # skip the zero-lag lobe
            lo = max(lo, int(neg[0])) if len(neg) else hi
            if lo < hi < len(r) and r[0] > 0:
                lag = lo + int(np.argmax(r[lo:hi]))
                if r[lag] / r[0] > (0.5 if out["noise"] < 0.2 else 0.35):
                    f0s.append(fs / float(lag))
        if f0s:
            body = f0s[1:] or f0s                            # past the attack when the sample is long enough
            f0 = float(np.median(body))
            out["pitch"] = int(round(69 + 12 * math.log2(f0 / 440.0)))
            out["pitch_hz"] = round(f0, 1)
    return out


def _feat_tags(role, ft):
    """Character tags measured from audio features (relative to the role)."""
    tags = set()
    if not ft or ft.get("silent"):
        return tags
    cen, att, dec, roll = ft.get("centroid", 0), ft.get("attack_ms", 0), ft.get("decay_s", 0), ft.get("rolloff", 0)
    lo, hi = {"kick": (500, 2500), "808": (200, 900), "snare": (1800, 4500), "clap": (1800, 4800), "hat": (6000, 10000),
              "openhat": (6000, 10000), "ride": (4000, 8000), "crash": (4000, 8000), "rim": (1500, 4500),
              "perc": (1200, 5000), "shaker": (5000, 10000), "tom": (300, 1500)}.get(role, (1000, 4000))
    if cen and cen < lo:
        tags.add("dark")
    if cen and cen > hi:
        tags.add("bright")
    hits = ("kick", "snare", "clap", "rim", "perc", "tom", "hat", "shaker", "808")
    if role in hits + ("openhat", "crash", "ride"):
        if role in hits and att <= 4 and dec < (1.2 if role == "808" else 0.45):
            tags.add("punchy")
        if att > 12:
            tags.add("soft")
        long_ = {"808": 1.4, "kick": 0.6, "openhat": 1.0, "crash": 2.0, "ride": 2.0}.get(role, 0.5)
        if dec >= long_:
            tags.add("long tail")
        elif dec < long_ / 3:
            tags.add("short")
    sub = ft.get("sub", 0)
    if role == "kick" and sub < 0.25 and cen > 700:          # little low end, mostly click: thin
        tags.add("thin")
    elif role in ("kick", "808", "tom") and sub > 0.6:
        tags.add("warm")
    if roll and roll < 9000 and role not in ("808", "kick", "tom"):
        tags.add("dusty")
    elif roll and roll > 15000 and ft.get("floor_db", 0) < -60:
        tags.add("clean")
    return tags


def _feat_role(ft):
    """A role guess from audio alone (for samples whose names say nothing)."""
    if not ft or ft.get("silent"):
        return None
    d, sub, nz, cen, dec = ft.get("dur", 0), ft.get("sub", 0), ft.get("noise", 0), ft.get("centroid", 0), ft.get("decay_s", 0)
    if d > 2.5:
        return "texture" if nz > 0.25 else None
    if sub > 0.45 and dec < 0.6:
        return "kick"
    if sub > 0.45 and "pitch" in ft:
        return "808"
    if nz > 0.2 and cen > 6000 and dec < 0.35:
        return "hat"
    if nz > 0.2 and cen > 6000:
        return "openhat"
    if nz > 0.15 and 1500 < cen < 6000 and dec < 0.5:
        return "snare"
    if "pitch" in ft and d < 2.5:
        return "oneshot"
    return "perc" if d < 0.6 else None


def _features_for(e, budget):
    """Cached features for a catalog entry (computed now if the time budget allows). None for non-WAV / errors."""
    if not e["p"].lower().endswith(FEATURE_EXTS):
        return None
    fc = _feat_cache(); k = e["p"]; sig = f"{FEATURE_VER}:{e.get('z')}:{e.get('m')}"
    hit = fc.get(k)
    if hit and hit.get("k") == sig:
        if time.time() - hit.get("u", 0) > 3600:
            hit["u"] = round(time.time()); _SAMPLE_MEMO["feat_dirty"] = True
        return hit.get("f")
    if time.time() > budget:
        return None
    try:
        ft = (getattr(_MEASURE, "f", None) or sample_features)(k)
    except Exception as ex:
        ft = {"error": str(ex)[:60]}
    with _FEAT_LOCK:
        _feat_cache()[k] = {"k": sig, "f": ft, "u": round(time.time())}
        _SAMPLE_MEMO["feat_dirty"] = True
    return ft


def _sample_kind(kind):
    k = str(kind or "").strip().lower()
    k = SAMPLE_ALIASES.get(k, k)
    return k if k in SAMPLE_KINDS else None


def _genre_words(genre, role):
    """Extra query words from the genre's data (roles[<role>].hint, e.g. lofi kick -> 'soft dusty kick')."""
    try:
        cm = __import__("flh_compose")
        g = cm.genre_data(genre)
    except Exception:
        return [], None
    if not g:
        return [], None
    hint = ((g.get("roles") or {}).get({"808": "bass"}.get(role, role)) or {}).get("hint") or ""
    words = [w for w in _tokens(hint) if len(w) > 2 and w not in ("or", "and", "the", "with")]
    return words, g


ROLE_FALLBACK = {"rim": ["perc", "snare"], "shaker": ["perc", "hat"], "perc": ["shaker", "rim"], "ride": ["crash"],
                 "crash": ["ride"], "openhat": ["hat"], "clap": ["snare"], "snare": ["clap"], "tom": ["perc"],
                 "808": [], "kick": [], "hat": ["shaker"]}


_DRUM_CHAR = {}


def _drum_char():
    """data/catalog/drum_character.json (a user copy in the state folder's catalog/ wins): genre families and the
    measured ranges a drum one-shot should fall in."""
    if "d" not in _DRUM_CHAR:
        d = {}
        for base in ([os.path.join(os.path.dirname(H["STATE"]), "catalog")] if H and H.get("STATE") else []) + \
                [os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "catalog")]:
            try:
                with open(os.path.join(base, "drum_character.json"), encoding="utf-8") as f:
                    d = json.load(f)
                break
            except (OSError, ValueError):
                continue
        _DRUM_CHAR["d"] = d
    return _DRUM_CHAR["d"]


def _char_fit(role, genre, ft):
    """How well a drum one-shot's measured features fit its genre's character (0 when nothing is known)."""
    dc = _drum_char()
    if not dc or not ft or "error" in ft:
        return 0.0
    g = str(genre or "").lower()
    fam = next((f for f, gs in (dc.get("families") or {}).items() if g in gs), "default")
    tg = (dc.get("targets") or {})
    t = (tg.get(fam) or {}).get(role) or (tg.get("default") or {}).get(role)
    if not t:
        return 0.0
    w = float(dc.get("weight", 0.4)); s = 0.0
    for k, rng_ in t.items():
        v = ft.get(k)
        if v is None or not isinstance(rng_, (list, tuple)) or len(rng_) != 2:
            continue
        lo, hi = float(rng_[0]), float(rng_[1])
        if lo <= v <= hi:
            s += w
        else:
            s -= w * min(2.0, ((lo - v) if v < lo else (v - hi)) / max(hi - lo, 1e-9))
    return s


# ---- sound taste: liked sounds are picked first, bad picks are remembered ------------------------------------------
TASTE_MAX = 400                                       # remembered sounds per side (oldest dropped)


def _taste_key(x):
    """A sound's key: a file's name without folder or extension (the same sample in two packs matches), or a
    preset / instrument name."""
    t = str(x or "").replace("\\", "/").rstrip("/").split("/")[-1].strip().lower()
    return re.sub(r"\.(wav|ogg|flac|aif|aiff|mp3|m4a|fst|nmsv)$", "", t)


def taste_get():
    try:
        t = H["_state"]().get("sound_taste") or {}
    except Exception:
        t = {}
    return {"like": dict(t.get("like") or {}), "dislike": dict(t.get("dislike") or {})}


def taste_note(like=(), dislike=(), weight=1):
    """Remember sounds the user kept (like) or swapped out / turned down (dislike). weight 2 = they said so."""
    if isinstance(like, str):
        like = [like]
    if isinstance(dislike, str):
        dislike = [dislike]
    t = taste_get(); changed = []
    for side, other, items in (("like", "dislike", like or ()), ("dislike", "like", dislike or ())):
        for it in items:
            k = _taste_key(it)
            if not k:
                continue
            t[side][k] = min(4, t[side].get(k, 0) + int(weight))
            t[other].pop(k, None)                     # a change of mind: the newest word wins
            changed.append(f"{side}d {k}")
        if len(t[side]) > TASTE_MAX:
            t[side] = dict(list(t[side].items())[-TASTE_MAX:])
    if changed:
        try:
            H["_state"]({"sound_taste": t})
        except Exception:
            pass
    return changed


def taste_score(x, t=None):
    """Ranking bonus for a sound: + for liked, - for disliked (0 when unknown)."""
    t = t if t is not None else taste_get()
    k = _taste_key(x)
    if not k:
        return 0.0
    return 1.0 * min(3, t["like"].get(k, 0)) - 1.5 * min(3, t["dislike"].get(k, 0))


def sample_search(role=None, query=None, genre=None, bpm=None, key=None, limit=10, exclude=(), features=True,
                  fallback=True, budget_s=8.0, strict=False, full=False):
    """Ranked samples: role filter (with honest fallbacks), query/genre words matched against name and character
    tags (from names, and measured audio features for the top candidates), loops ranked by BPM/key fit.
    Deterministic: features are measured for a fixed top N by name score - all of them, or (past budget_s, a safety
    cap) none - so the same request picks the same samples on a cold or warm cache and on any PC."""
    cat = _sample_catalog()
    index_note = take_note()
    want = _sample_kind(role) if role else None
    if role and not want:
        raise ValueError(f"sample kinds: {', '.join(SAMPLE_KINDS)}")
    words = [w for w in _tokens(query) if w not in ("a", "an", "the", "and", "with", "for", "sample", "samples")]
    gwords, g = _genre_words(genre, want) if genre else ([], None)
    gname = _tokens((g or {}).get("name") or genre or "")
    galias = [_tokens(a) for a in ((g or {}).get("aliases") or [])]
    excl = {os.path.normcase(os.path.abspath(p)) for p in exclude or ()}
    ents = cat["entries"]
    used_role = want
    pool = [e for e in ents if (not want or e["r"] == want)]
    if want and not pool and fallback:
        for alt in ROLE_FALLBACK.get(want, []):
            pool = [e for e in ents if e["r"] == alt]
            if pool:
                used_role = alt; break
    tag_words = set(words) | set(gwords)
    phrase = {"long tail"} if ("long" in tag_words and "tail" in tag_words) or "long" in tag_words else set()
    if "lofi" in tag_words or "lo" in tag_words or "fi" in tag_words:
        tag_words.add("dusty")

    tst = taste_get(); has_taste = bool(tst["like"] or tst["dislike"])

    def score(e, ft_tags=()):
        tags = set(e.get("t") or []) | set(ft_tags)
        nm = set(_tokens(e["n"])); ctx = set(_tokens(e.get("pk", ""))) | set(_tokens(os.path.dirname(e["p"])[-80:]))
        s = 1.0 + (taste_score(e["p"], tst) if has_taste else 0.0)
        for w in words:
            if w in tags:
                s += 2.0
            if w in nm:
                s += 1.5
            elif w in ctx:
                s += 0.5
        for w in gwords:
            if w in tags:
                s += 1.0
            elif w in nm:
                s += 0.4
        for t in phrase:
            if t in tags:
                s += 1.5
        for w in list(tag_words) + list(phrase):
            opp = TAG_OPPOSITE.get(w)
            if opp and opp in tags and w not in tags:
                s -= 1.0
        if gname and (set(gname) <= ctx | nm or any(a and set(a) <= ctx | nm for a in galias)):
            s += 1.5
        if bpm and e.get("b"):
            b = float(e["b"]); r = min(abs(b - bpm), abs(b * 2 - bpm), abs(b / 2 - bpm)) / float(bpm)
            s += 2.0 if r < 0.03 else (-1.0 if r > 0.08 else 0)
        if key and e.get("k"):
            s += 1.5 if str(e["k"]).lower() == str(key).lower() else -0.5
        if g and e["r"] == "loop" and e.get("b") and not bpm:
            t = g.get("tempo") or {}
            if t.get("min") and t.get("max"):
                s += 0.8 if t["min"] <= e["b"] <= t["max"] else -0.5
        return s

    strict_note = None
    if strict and words:                                       # a user's query: only samples it actually matches
        def hit(e):
            hay = set(_tokens(e["n"])) | set(e.get("t") or []) | set(_tokens(e.get("pk", ""))) | \
                set(_tokens(os.path.dirname(e["p"])[-80:]))
            return any(w in hay for w in words)
        matched = [e for e in pool if hit(e)]
        if len(matched) >= min(3, int(limit or 10)):            # enough real matches: unrelated sounds stay out
            pool = matched
        elif not matched:
            strict_note = f"nothing matched '{' '.join(words)}': showing the best {used_role or 'samples'} instead"
    ranked = sorted(((score(e), e) for e in pool if os.path.normcase(os.path.abspath(e["p"])) not in excl),
                    key=lambda x: (-x[0], x[1]["n"].lower()))
    top = ranked[:max(12, int(limit) * 3)]
    feat_note = None
    if features:
        budget = time.time() + float(budget_s if budget_s is not None else 8.0); res = []; late = False
        until = getattr(_HOLD_TL, "until", None)
        if until is not None:                                  # one shared budget for a whole build
            budget = min(budget, until)
        for s, e in top:
            ft = _features_for(e, budget)
            if ft is None and e["p"].lower().endswith(FEATURE_EXTS):
                late = True                                      # out of time: features for none (determinism)
                break
            ftags = _feat_tags(e["r"], ft) if ft and "error" not in ft else set()
            fit = _char_fit(e["r"], (g or {}).get("name") or genre, ft) if genre and ft else 0.0
            res.append(((score(e, ftags) + fit) if ft else s, e, ft, ftags))
        _feat_save()
        if late:
            res = [(s, e, None, set()) for s, e in top]
            feat_note = "audio features skipped (measuring them took too long): ranked by names and tags only"
    else:
        res = [(s, e, None, set()) for s, e in top]
    res.sort(key=lambda x: (-x[0], x[1]["n"].lower()))
    out = []; seen = set()                                     # one file listed by two packs: shown once
    res = [x for x in res if not (os.path.normcase(x[1]["p"]) in seen or seen.add(os.path.normcase(x[1]["p"])))]
    for s, e, ft, ftags in res[:int(limit or 10)]:
        d = {"name": e["n"], "role": e["r"], "path": e["p"], "pack": e.get("pk") or None,
             "tags": sorted(set(e.get("t") or []) | set(ftags)), "score": round(s, 1)}
        if full:
            d.update(source=e["src"], load={"tool": "flp_load_sample", "sample": e["p"]})
        for k, kk in (("s", "sub"), ("i", "inst"), ("b", "bpm"), ("k", "key"), ("o", "root")):
            if e.get(k):
                d[kk] = e[k]
        if ft and "error" in ft:
            d["unreadable"] = ft["error"]                      # said, not hidden: why no length / pitch
        if ft and "error" not in ft:
            d["dur"] = ft.get("dur")
            if "pitch" in ft:
                d["pitch"] = ft["pitch"]; d["note"] = PC_NAMES[ft["pitch"] % 12] + str(ft["pitch"] // 12 - 1)
            if ft.get("rms_db") is not None:
                d["level_db"] = ft["rms_db"]
        if e["r"] == "loop" and not e.get("b") and ft and ft.get("dur"):
            guess = _bpm_from_length(ft["dur"], g)
            if guess:
                d["bpm"] = guess; d["bpm_guess"] = "from the loop's length (assumes whole 4/4 bars)"
        out.append({k: v for k, v in d.items() if not (v is None or (isinstance(v, (list, str)) and not v))})
    info = {"role": want, "found": len(pool), "results": out}
    if not full and out:
        info["load_with"] = "flp_load_sample(project, output, sample=<a result's path>, channel=N)"
    if strict_note:
        info["note"] = strict_note
    if feat_note:
        info["note"] = feat_note
    if used_role != want:
        info["fallback"] = f"no {want} samples found: showing {used_role} instead"
    if index_note:
        info["index_note"] = index_note
    return info


def _bpm_from_length(dur, g=None):
    lo, hi = 70, 180
    if g and (g.get("tempo") or {}).get("min"):
        lo, hi = g["tempo"]["min"], g["tempo"]["max"]
    best = None
    for bars in (1, 2, 4, 8, 16):
        b = 240.0 * bars / dur
        if lo <= b <= hi:
            cand = round(b, 1)
            if best is None or abs(cand - round(cand)) < abs(best - round(best)):
                best = cand
    return best


def _samples_summary():
    cat = _sample_catalog()
    note = take_note()
    by = {}
    for e in cat["entries"]:
        by[e["r"]] = by.get(e["r"], 0) + 1
    out = {"count": len(cat["entries"]), "by_role": dict(sorted(by.items(), key=lambda kv: -kv[1])),
           "roots": cat["roots"], "truncated": cat.get("truncated") or None, "cached": cat.get("_cached")}
    if note:
        out["index_note"] = note
    return out


# ----------------------------------------------------------------------------- A/B versions
def _ab_state():
    return H["_state"]().get("ab") or {}


def _same(a, b):
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _b_path(project):
    stem = os.path.splitext(os.path.basename(project))[0]
    return os.path.join(os.path.dirname(os.path.abspath(project)), stem + " [B].flp")


# Events FL rewrites on every save without anyone changing the song: version / build, save time, registration,
# plugin window geometry. Everything else (notes, mixer levels and names, routing, plugin states, channel settings,
# patterns, clips, automation) is part of the content hash.
# PC-VERIFY: open a project in FL, save without touching it, and _content() is unchanged (checklists/catalog.md)
VOLATILE_EVENTS = {199, 159, 237, 28, 231, 212}


def _content(p):
    """Hash of what a person could have changed (every event payload except VOLATILE_EVENTS) - FL's re-save of an
    untouched project keeps it, unlike the file's mtime/size. A file that doesn't parse hashes its raw bytes, so a
    change is never hidden."""
    import hashlib
    try:
        with open(p, "rb") as f:
            d = f.read()
    except OSError:
        return None
    try:
        if d[:4] != b"FLhd" or b"FLdt" not in d[:64]:
            raise ValueError("not an FL project")
        ev = H["_events"](d)
        h = hashlib.sha256(d[:d.index(b"FLdt")])
        for e in ev:
            if e["id"] in VOLATILE_EVENTS:
                continue
            h.update(bytes([e["id"]])); h.update(d[e["off"]:e["end"]])
        return h.hexdigest()[:16]
    except Exception:                                            # not parseable: the bytes themselves
        return "raw:" + hashlib.sha256(d).hexdigest()[:16]


def _raw_hash(p):
    import hashlib
    try:
        with open(p, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def _wait_ready(timeout_s=15.0):
    """FL answers the bridge and shows no popup (it's busy for a few seconds after loading a project)."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            H["BRIDGE"].call("ping", timeout=2.0)
        except Exception:
            time.sleep(0.3); continue
        try:
            pops = H["fl_dialogs"]() or []
        except Exception:
            pops = []
        if not pops:
            return True
        time.sleep(0.3)
    return False


def _stat(p):
    try:
        st = os.stat(p)
        return [round(st.st_mtime, 3), st.st_size]
    except OSError:
        return None


def _resolve_version(project, version):
    if isinstance(version, str) and version.strip().lstrip("-").isdigit():
        version = int(version.strip())
    if isinstance(version, int) and not isinstance(version, bool):
        vs = H["flp_versions"](project)
        if not vs:
            raise ValueError("no backups of this project yet (flp_versions is empty); label one with "
                             + _hint("fl_ab", {"project": _raw("project"), "label": _raw("...")}))
        try:
            v = vs[version]
        except IndexError:
            raise ValueError(f"version index {version} out of range: {len(vs)} backups (-1 = newest)")
        return v["backup"], f"backup {version}: {os.path.basename(v['backup'])}"
    v = str(version)
    if os.path.isfile(v):
        return os.path.abspath(v), os.path.basename(v)
    vers = flh_store.journal_get(project).get("versions") or []
    low = v.lower()
    hit = next((x for x in vers if str(x.get("label", "")).lower() == low), None) or \
        next((x for x in vers if str(x.get("label", "")).lower().startswith(low)), None)
    if hit and os.path.isfile(hit.get("path", "")):
        return hit["path"], f"'{hit['label']}'"
    raise ValueError(f"no version '{version}': give a path, an index into flp_versions (-1 = newest backup) or a "
                     f"label from the journal ({[x.get('label') for x in vers]})")


def _stop():
    try:
        H["BRIDGE"].call("transport", action="stop", timeout=2.0)
    except Exception:
        pass


def _open_a(project):
    """Leave B for A in the running FL. FL's own 'save changes?' is answered Yes (as fl_open does), so edits made
    in B are saved into the [B] file - they're never lost; the caller then sees B's mtime changed and keeps it."""
    if not H["fl_running"]():
        return {"opened": project, "via": "fl_open (FL wasn't running)", **_quiet(H["fl_open"](project))}
    _stop()
    t = time.time()
    _wait_ready()
    try:                                                         # save B first: then FL doesn't ask, and edits
        H["_save_in_fl_and_wait"](_ab_state().get("b") or project, 4.0)   # made in B are in its file
    except Exception:
        pass
    H["_open_running_or_fail"](project, on_save="yes")
    H["_state"]({"last_opened": os.path.abspath(project)})
    try:
        H["_stop_autoplay_later"]()
    except Exception:
        pass
    return {"opened": project, "via": "FL's Open dialog", "s": round(time.time() - t, 1)}


def _quiet(r):
    return {k: v for k, v in (r or {}).items() if k in ("warning", "steps_s")} if isinstance(r, dict) else {}


def _set_aside(b, why):
    """Keep an edited [B] file under a new name so a fresh [B] can be made (nothing is overwritten)."""
    H["writable"](b)
    stem = os.path.splitext(b)[0]
    dst = f"{stem[:-4] if stem.endswith(' [B]') else stem} [B edited {time.strftime('%Y%m%d-%H%M%S')}].flp"
    os.replace(b, dst)
    return {"kept": dst, "why": why}


def _read(p):
    with open(p, "rb") as f:
        return f.read()


def _label(project, label, ab):
    """Store the open side as a named version: <project dir>/Backup/<stem> (<label>).flp + journal entry."""
    side_b = ab.get("side") == "B" and _same(ab.get("project"), project) and os.path.isfile(ab.get("b", ""))
    src = ab["b"] if side_b else os.path.abspath(project)
    saved = None
    if not side_b and H["fl_running"]() and _same(H["_state"]().get("last_opened"), project):
        # through the server's save guards (FL holding an older copy, a preview's live mutes, FL showing another
        # project, the file changed behind FL): a refused save keeps the file on disk as it is - and that is stored
        try:
            if H["_save_in_fl_and_wait"](project, 5.0):
                saved = True
            else:
                why = H["_save_refused_why"]() if H.get("_save_refused_why") else None
                saved = (f"not saved in FL first ({why or 'FL did not write the file in 5 s'}): the file on disk was "
                         "used")
        except Exception as e:
            saved = f"not saved in FL first ({str(e)[:60]}): the file on disk was used"
    clean = "".join(c for c in str(label) if c not in '<>:"/\\|?*').strip()
    if not clean:
        raise ValueError("give the version a label")
    stem = os.path.splitext(os.path.basename(project))[0]
    bdir = os.path.join(os.path.dirname(os.path.abspath(project)), "Backup")
    dst = os.path.join(bdir, f"{stem} ({clean}).flp"); n = 2
    while os.path.isfile(dst) and _read(dst) != _read(src):
        dst = os.path.join(bdir, f"{stem} ({clean} {n}).flp"); n += 1
    H["writable"](dst)
    os.makedirs(bdir, exist_ok=True)
    shutil.copyfile(src, dst)
    vers = [v for v in flh_store.journal_get(project).get("versions") or [] if not _same(v.get("path"), dst)
            and v.get("label") != clean]
    vers.append({"label": clean, "path": dst, "when": time.strftime("%Y-%m-%d %H:%M"),
                 "note": "the [B] side" if side_b else None})
    flh_store.journal_update(project, set={"versions": [{k: v for k, v in x.items() if v is not None} for x in vers]})
    r = {"label": clean, "stored": dst, "from": "B" if side_b else "A"}
    if saved is not None:
        r["saved_in_fl"] = saved
    if side_b:
        r["note"] = "B was stored as it is on disk (unsaved changes in FL aren't in it)"
    return r


def fl_ab(project, version=None, back=False, label=None):
    """A/B two versions of a song by ear in the running FL. version: path, journal label, or flp_versions index
    (-1 = newest backup); none = toggle A<->B. back: reopen the real project and recycle the [B] copy.
    label: store the open side as a named version first."""
    project = os.path.abspath(project)
    if not os.path.isfile(project):
        raise FileNotFoundError(project)
    ab = _ab_state()
    if ab and not _same(ab.get("project"), project):
        if ab.get("side") == "B":
            raise ValueError(f"another song is in A/B ({ab.get('project')}): "
                             + _hint("fl_ab", {"project": _raw("that song"), "back": True}) + " first")
        ab = {}
    out = {"project": project}; notes = []
    if label:
        out["label"] = _label(project, label, ab)
        if version is None and not back:
            return out
    b = ab.get("b") or _b_path(project)

    def leave_b(open_a=True):
        """Go from B to A; returns True when B kept edits."""
        if open_a:
            out["switch"] = _open_a(project)
        else:
            out["switch"] = "FL isn't running: nothing opened"
        now = _content(b)
        edited = bool(now and ab.get("b_hash") and now != ab["b_hash"])
        if edited:
            ab["b_edited"] = True
            notes.append(f"the [B] copy was modified while it was open (edits saved into it): kept at {b}")
        ab["side"] = "A"; ab["b_hash"] = now
        return edited

    if back:
        if ab.get("side") == "B":
            leave_b(open_a=bool(H["fl_running"]()))
        out["side"] = "A"
        if os.path.isfile(b):
            changed = ab.get("b_hash") and _content(b) != ab["b_hash"]
            if ab.get("b_edited") or changed or not ab:
                why = "it was modified" if (ab.get("b_edited") or changed) else "fl-headless didn't make it"
                out["kept_b"] = b; notes.append(f"[B] not recycled ({why}): {b}")
            else:
                out["recycled"] = H["files_recycle"]([b])["recycled"]
        H["_state"]({"ab": None})
        if notes:
            out["warning"] = notes
        return out

    if version is None and ab.get("side") == "B":                     # toggle B -> A
        leave_b()
        H["_state"]({"ab": ab}); out.update(side="A", b=b, version=ab.get("version"))
        if notes:
            out["warning"] = notes
        out["next"] = _hint("fl_ab", {"project": _raw("project")}) + " again for B; back=true when done"
        return out
    if version is None and ab.get("side") == "A" and os.path.isfile(b):   # toggle A -> B (same B file)
        _wait_ready()
        r = H["fl_open"](b)
        ab["side"] = "B"; H["_state"]({"ab": ab})
        out.update(side="B", b=b, version=ab.get("version"), **_quiet(r))
        out["next"] = _hint("fl_ab", {"project": _raw("project")}) + " to hear A; back=true when done"
        return out

    # a new B
    src, desc = _resolve_version(project, -1 if version is None else version)
    if _same(src, b):
        raise ValueError("that version is the [B] copy itself; give another version")
    if ab.get("side") == "B":
        leave_b()
    if os.path.isfile(b):                                         # never overwrite a [B] that holds someone's edits
        same_bytes = bool(ab.get("b_raw")) and _raw_hash(b) == ab["b_raw"]
        ours = same_bytes or (ab.get("b_hash") and _content(b) == ab["b_hash"] and not ab.get("b_edited"))
        if not ours:
            out["set_aside"] = _set_aside(b, "edited [B]" if ab else "a [B] file fl-headless didn't track")
            notes.append(f"previous [B] kept as {out['set_aside']['kept']}")
    H["writable"](b)
    shutil.copyfile(src, b)
    try:                                                          # its own title: FL's window shows titles, so a
        H["_own_title_file"](project, b, force=True)              # [B] titled like A can't be told apart from A
    except Exception:                                             # noqa: BLE001 - the copy still plays the same
        pass
    _wait_ready()
    r = H["fl_open"](b)
    ab = {"project": project, "b": b, "source": src, "version": desc, "side": "B", "b_hash": _content(b),
          "b_raw": _raw_hash(b), "when": time.strftime("%Y-%m-%d %H:%M")}
    H["_state"]({"ab": ab})
    out.update(side="B", b=b, version=desc, **_quiet(r))
    if notes:
        out["warning"] = notes
    out["next"] = _hint("fl_ab", {"project": _raw("project")}) + " toggles A/B; back=true returns to A and recycles the copy"
    return out


# ----------------------------------------------------------------------------- watchdog
NOTICE_BUTTONS = {"ok", "close"}
NEVER_TOUCH = ("trial", "anyway", "save", "demo", "register", "unlock", "delete", "overwrite", "replace",
               # the same in other UI languages (a notice mentioning any of these is left to the user)
               "démo", "testversion", "prueba", "prova", "avaliação", "пробн", "демо", "体験版", "试用", "試用", "체험",
               "speichern", "enregistrer", "guardar", "salva", "salvar", "opslaan", "zapisz", "сохран", "保存", "儲存", "저장",
               "löschen", "supprimer", "eliminar", "elimina", "excluir", "удал", "削除", "删除", "삭제",
               "überschreiben", "écraser", "sobrescrib", "sovrascriv", "substitu", "перезапис", "上書き", "覆盖", "덮어")


def _is_notice(d):
    btns = [b.replace("&", "").strip().lower() for b in d.get("buttons") or []]
    text = " ".join([d.get("title", "")] + list(d.get("text") or [])).lower()
    return (d.get("kind") in ("other", "missing_file") and btns and set(btns) <= NOTICE_BUTTONS
            and not any(w in text for w in NEVER_TOUCH))


def _autosaves(folder):
    bdir = os.path.join(folder, "Backup")
    if not os.path.isdir(bdir):
        return {"folder": bdir, "files": 0}
    auto = [f for f in os.listdir(bdir) if f.lower().endswith(".flp") and
            ("autosave" in f.lower() or "overwritten at" in f.lower())]
    size = sum(os.path.getsize(os.path.join(bdir, f)) for f in auto)
    return {"folder": bdir, "files": len(auto), "mb": round(size / 1e6, 1)}


def _bridge_fix(ports, mailbox):
    if mailbox is False and ports and not ports.get("pair"):
        return ("bridge ports missing: on Windows 11 with Windows MIDI Services, "
                + _hint("fl_bridge_setup", {"action": "use_loopback"}) + "; otherwise install loopMIDI and create "
                "ports 'Flapi Request' + 'Flapi Response', then enable them in FL Options > MIDI settings. flh_setup() "
                "lists exactly what's missing")
    return ("FL is up but the bridge doesn't answer: in FL Options > MIDI settings, Input 'Flapi Request' (or the "
            "loopback port) must be enabled with controller type 'fl-headless' and a port number, Output 'Flapi Response' "
            "on the same port number; if the script is old: " + _hint("fl_bridge_setup", {"action": "update_script"})
            + " then reload scripts there. " + _hint("fl_bridge_setup", {"action": "status"})
            + " confirms. Don't restart FL for this.")


def fl_watchdog(heal=False):
    """One fast health check: FL, window, popups, bridge ports + ping, open project, autosave bloat, disk."""
    t0 = time.time(); checks = {}; fixed = []; todo = []; ok = True
    real_pids = H.get("_fl_pids"); memo = {}
    if callable(real_pids):                                         # one tasklist call for the whole check
        def pids_once():
            if "p" not in memo or time.time() - memo["t"] > 2.0:
                memo["p"] = real_pids(); memo["t"] = time.time()
            return list(memo["p"])
        H["_fl_pids"] = pids_once
    try:
        st = H["_state"](); last = st.get("last_opened")
        try:
            running = bool(H["fl_running"]())
        except Exception as e:
            running = False; checks["fl_running_error"] = str(e)[:80]
        checks["fl_running"] = running
        if running:
            try:
                n = len(H["_fl_pids"]()); checks["instances"] = n
                if n > 1:
                    ok = False; todo.append(f"{n} FL copies are running: close the extra one (only one FL)")
            except Exception:
                pass
            try:
                main = H["_fl_main_window"]()
            except Exception as e:
                main = None; checks["window_error"] = str(e)[:80]
            checks["window"] = main[1] if main else None
            if not main:
                ok = False; todo.append("FL runs but has no main window yet (starting/closing, or hung): wait, then check again")
            try:
                pops = H["fl_dialogs"]()
            except Exception as e:
                pops = []; checks["popups_error"] = str(e)[:80]
            checks["popups"] = [{"hwnd": d["hwnd"], "title": d.get("title"), "kind": d.get("kind"),
                                 "buttons": d.get("buttons"), "notice": _is_notice(d),
                                 "blocks_fl": d.get("blocks_fl")} for d in pops]
            for d in pops:
                if heal and _is_notice(d):
                    try:
                        H["fl_dialog"]("click", "ok", d["hwnd"]); fixed.append(f"closed notice '{d.get('title')}'")
                        continue
                    except Exception as e:
                        todo.append(f"couldn't close '{d.get('title')}': {str(e)[:60]}")
                elif d.get("kind") in ("save_changes", "save_as"):
                    todo.append(f"FL asks '{d.get('title')}' ({d.get('kind')}): the user decides - answer with fl_dialog")
                elif _is_notice(d):
                    todo.append(f"notice '{d.get('title')}' open: fl_watchdog(heal=true) closes it")
                else:
                    todo.append(f"popup '{d.get('title')}' {d.get('buttons')}: read it (fl_snapshot hwnd) and answer")
                if d.get("blocks_fl", True):
                    ok = False
        # ports + bridge
        ports = None; mailbox = None
        try:
            mailbox = bool(H["_mailbox_alive"]())
        except Exception:
            pass
        checks["mailbox_alive"] = mailbox
        try:
            p = H["BRIDGE"].ports()
            ports = {"flapi_request": "Flapi Request" in p.get("midi_out", []),
                     "flapi_response": "Flapi Response" in p.get("midi_in", []),
                     "pair": list(H["BRIDGE"].choose(p) or []) or None}
        except Exception as e:
            ports = {"error": f"can't list MIDI ports here ({type(e).__name__})"}
        checks["ports"] = ports
        if running:
            tb = time.time()
            try:
                H["BRIDGE"].call("ping", timeout=2.0)
                checks["bridge"] = {"ok": True, "ms": round((time.time() - tb) * 1000)}
            except Exception as e:
                ok = False; checks["bridge"] = {"ok": False, "error": str(e)[:100]}
                closed = heal and fixed
                blocked = any(d.get("blocks_fl") and not (closed and d.get("notice")) for d in checks.get("popups") or [])
                todo.append("answer FL's popup first: FL doesn't answer the bridge while a popup blocks it" if blocked
                            else _bridge_fix(ports, mailbox))
        # open project
        try:
            guess = H["_open_project_guess"]()[0] if running else None
        except Exception:
            guess = None
        ab = st.get("ab") or {}
        checks["open_project"] = {"fl_shows": guess, "last_opened": last,
                                  "match": (guess is None or _same(guess, last)) if running else None}
        if ab:
            checks["ab"] = {"side": ab.get("side"), "b": ab.get("b"), "project": ab.get("project")}
        if running and guess and last and not _same(guess, last):
            todo.append(f"FL shows {os.path.basename(guess)} but tools think {os.path.basename(last)} is open: "
                        "fl_open the one you mean")
        # FL not running: start it with the last project (the one safe case where FL is started)
        if not running:
            ok = False
            if heal and last and os.path.isfile(last):
                try:
                    H["fl_open"](last); fixed.append(f"started FL with {os.path.basename(last)}")
                except Exception as e:
                    todo.append(f"couldn't start FL: {str(e)[:80]}")
            else:
                todo.append("FL isn't running: fl_watchdog(heal=true) starts it with " +
                            (os.path.basename(last) if last else "the last project (none recorded: use fl_open)"))
        # autosave bloat + disk
        folder = os.path.dirname(last) if last else None
        if folder and os.path.isdir(folder):
            a = _autosaves(folder); checks["autosaves"] = a
            if a["files"] > 40 or a.get("mb", 0) > 300:
                todo.append(f"{a['files']} FL autosave/backup files ({a.get('mb')} MB) in {a['folder']}: recycle old "
                            "ones with files_recycle if the user agrees")
        try:
            base = folder or next(iter(H["roots"]()), None) or os.path.expanduser("~")
            free = shutil.disk_usage(base).free / 1e9
            checks["disk_free_gb"] = round(free, 1)
            if free < 5:
                ok = False; todo.append(f"only {free:.1f} GB free on the projects drive")
        except Exception:
            pass
        try:
            if H.get("_desktop_locked") and H["_desktop_locked"]():
                checks["desktop_locked"] = True
                todo.append(H.get("LOCKED_NOTE") or "Windows is locked: FL's dialogs can't be driven until it's unlocked")
        except Exception:
            pass
    finally:
        if callable(real_pids):
            H["_fl_pids"] = real_pids
    return {"ok": ok, "checks": checks, "fixed": fixed, "todo": todo, "took_s": round(time.time() - t0, 2)}


def on_tool_error(tool_name, exc):
    """A short next-step hint for a failed tool call (None when there's nothing useful to add)."""
    msg = f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, TimeoutError) or "no reply from fl" in msg or "not answering" in msg:
        return "FL bridge not answering: run fl_watchdog(heal=true)"
    if "no bridge midi ports" in msg:
        return "bridge ports missing: flh_setup() shows what's missing (loopMIDI ports or Windows MIDI loopback)"
    if "open dialog" in msg or "popup" in msg:
        return "a popup or a busy FL got in the way: fl_watchdog(heal=true), then retry"
    if "outside flh_roots" in msg:
        return "path outside FLH_ROOTS: write inside the project folders (fl_status lists them)"
    if "fl64.exe not found" in msg:
        return "FL wasn't found: flh_setup() shows where it looked; set FLH_FL_EXE to the full path of FL64.exe"
    if "windows only" in msg:
        return "this needs FL Studio on Windows; the flp_* / audio_* tools work here"
    if "trial" in msg:
        return "a trial plugin is involved: replace it (fl_sound_catalog lists owned sounds); never 'Save anyway'"
    if "isn't downloaded" in msg or "no splice instrument library" in msg:
        return "fl_sound_catalog(kind=...) lists the sounds that are on this PC"
    if "second fl" in msg or "copies of fl" in msg:
        return "more than one FL is running: close the extra one, then fl_watchdog"
    if tool_name and tool_name.startswith("fl_") and ("fl isn't" in msg or "not running" in msg):
        return "FL isn't running: fl_watchdog(heal=true) starts it with the last project"
    return None


# ----------------------------------------------------------------------------- register
def _warm_later(delay_s=3.0):
    """Load (or build) the sound and sample indexes in the background after the server starts, so the first new
    song or sound search doesn't pay for the folder walk (PC 2026-10-20: 1.6 s on the first flh_new_song)."""
    def run():
        time.sleep(delay_s)
        for fn in (_catalog, _sample_catalog):
            try:
                fn()
            except Exception:
                pass
        try:
            ns_mod = sys.modules.get("flh_newsong")
            if ns_mod is not None and hasattr(ns_mod, "_preset_index"):
                ns_mod._preset_index()
        except Exception:
            pass
        warm_features()
    if os.name == "nt" and not os.environ.get("FLH_NO_WARM"):
        threading.Thread(target=run, name="flh-warm-catalog", daemon=True).start()


WARM_ROLES = ("kick", "snare", "clap", "hat", "openhat", "808", "perc", "rim", "shaker", "crash", "ride")
WARM_MAX_S = 900.0                                    # the warm-up stops after this long (it resumes next start)
WARM_GAP_S = 0.03                                     # a pause between files: FL keeps the CPU it needs
_WARM = {"running": False, "done": 0, "measured": 0, "took_s": 0.0}


def warm_features(genres=None, max_s=None, gap_s=None):
    """Measure the audio features a new song's sample picks need, ahead of time, so the build itself only reads them
    from the cache. Recently used genres first (state 'recent_genres'), then every genre; per genre, the drum and 808
    candidates a plain request ranks top. Already measured files cost nothing. Returns a small report."""
    if _WARM["running"]:
        return dict(_WARM)
    _WARM.update(running=True, done=0, measured=0, pct=0, eta_s=None)
    t0 = time.time(); stop = t0 + float(max_s if max_s is not None else WARM_MAX_S)
    gap = WARM_GAP_S if gap_s is None else float(gap_s)
    try:
        if genres is None:
            try:
                cm = sys.modules.get("flh_compose") or __import__("flh_compose")
                names = sorted(k for k in cm.genres() if not k.startswith("_"))
            except Exception:
                names = []
            try:
                recent = [g for g in (H["_state"]().get("recent_genres") or []) if isinstance(g, str)]
            except Exception:
                recent = []
            genres = recent + [g for g in names if g not in recent]
        orig = sample_features

        def slow(path):                               # measure, then give the CPU back for a moment
            w = time.time()
            while _HOLD["n"] > 0 and time.time() - w < 120:   # a build is running: it goes first
                time.sleep(0.2)
            _WARM["measured"] += 1
            r = orig(path)
            if gap:
                time.sleep(gap)
            return r
        ng = max(1, len(genres))
        for gi, g in enumerate(genres):
            _WARM["pct"] = int(100 * gi / ng)
            el = time.time() - t0
            _WARM["eta_s"] = int(el * (ng - gi) / gi) if gi else None
            try:                                      # fl_job shows it when the warm-up runs as a background job
                H["_job_progress"](step=f"measuring sample sounds: genre {gi + 1}/{ng} ({g})", pct=_WARM["pct"],
                                   eta_s=_WARM["eta_s"])
            except Exception:
                pass
            for role in WARM_ROLES:
                left = stop - time.time()
                if left <= 0:
                    break
                try:
                    _sample_search_with(slow, role, None, g, limit=6, budget_s=left)
                except Exception:
                    pass
            else:
                _WARM["done"] += 1
                continue
            break
    finally:
        _WARM.update(running=False, took_s=round(time.time() - t0, 1))
        try:
            _feat_save()
        except Exception:
            pass
    return dict(_WARM)


def _sample_search_with(measure, *a, **kw):
    """sample_search with another feature measurer (the warm-up's paced one) - only in this thread's call."""
    _MEASURE.f = measure
    try:
        return sample_search(*a, **kw)
    finally:
        _MEASURE.f = None


_MEASURE = threading.local()


def register(ns):
    global H
    H = ns
    _warm_later()
    P, S, STR, INT, BOOL = ns["_p"], ns["_schema"], ns["_STR"], ns["_INT"], ns["_BOOL"]
    ns["TOOLS"]["fl_sound_catalog"] = (
        fl_sound_catalog, "Every instrument/sound the user has - LABS/INSTRUMENT packs, BBC SO Discover sections, FL "
        "generators, FLEX presets, saved sounds - with kind, mood tags, owned/trial status and the exact tool call "
        "that loads it. query='soft choir' or kind='strings' -> ranked top list (trial plugins left out). Samples too: "
        "FL's factory packs + the user's sample folders, by role with character tags (punchy, dusty, clean, dark, "
        "bright, hard, soft, long tail - from names and measured audio): kind='kick' query='dusty punchy' "
        "genre='lofi'; loops with BPM/key. Load one with flp_load_sample. Cached; refresh=true rebuilds.",
        S(query=STR("words to match: instrument/sample + character, e.g. 'warm dark strings', 'dusty punchy'", True),
          kind=STR("instruments: piano|strings|choir|pad|perc|brass|woodwind|bells|pluck|guitar|bass|synth|fx|organ|"
                   "lead; samples: kick|808|snare|clap|rim|hat|openhat|ride|crash|perc|shaker|tom|fx|texture|vocal|"
                   "loop|oneshot", True),
          type=STR("instrument | sample (default: from kind)", True),
          genre=STR("rank samples for a genre (its sound hints, tempo range for loops)", True),
          bpm=ns["_NUM"]("loops: prefer this tempo", True), key=STR("loops/one-shots: prefer this key", True),
          refresh=BOOL("rebuild the index now", True), limit=INT("results (default 15)", True),
          include_trial=BOOL("also list trial plugins (default false)", True),
          full=BOOL("samples: every field (source, a load call per result); default compact", True)))
    ns["TOOLS"]["fl_ab"] = (
        fl_ab, "Compare two versions of a song by ear in the running FL (~3 s switch, no restart, nothing lost): "
        "version (path, journal label, or flp_versions index, -1 = newest backup) is copied to '<song> [B].flp' and "
        "opened; call again with no version to toggle A/B; back=true reopens the real project and recycles the copy "
        "(kept if it was edited). label='before strings' stores the open side as a named version.",
        S(project=ns["_FLP"], version=P(["string", "integer"], "path, journal label or backup index", True),
          back=BOOL("return to the real project and recycle [B]", True),
          label=STR("store the open side as this named version", True)))
    ns["TOOLS"]["fl_watchdog"] = (
        fl_watchdog, "One fast health check: FL running, main window, popups, bridge ports + ping, open project, "
        "autosave pile-up, disk space -> {ok, checks, fixed, todo}. heal=true closes OK-only notices and starts FL "
        "(with the last project) only when none runs; never restarts FL or answers save/trial questions.",
        S(heal=BOOL("fix what's safe", True)))
    ns["sound_ok"] = sound_ok
    ns["sample_search"] = sample_search
    ns["taste_note"] = taste_note
    ns["taste_score"] = taste_score
    ns["sample_features"] = sample_features
    ns["classify_sample"] = classify_sample
    ns["plugin_ownership"] = ownership
    ns["flh_edition"] = fl_edition
    ns["on_tool_error"] = on_tool_error
