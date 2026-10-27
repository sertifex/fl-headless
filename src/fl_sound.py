"""Describe a recorded sound (attack, sustain, brightness, low end, width, noisiness) and score it against a wish like
'soft pad' or 'bright plucky sequencer synth, short decay, clean, not bassy'.

The wish's words become targets on a few measured dimensions (targets()): bright (spectral centroid), low_end (share of
the energy under 200 Hz), attack (ms to the peak), sustain (level a while after the peak, while the note is still
held), width (side vs mid) and dirt (distortion: the preset's name - 'DIST', 'overdrive', ... - and the spectrum's
flatness). 'not' / 'no' / 'non' / 'without' / 'less' flip the next word. Each dimension is squashed to -1..1, so one
extreme reading can't outweigh the rest; a sound that can't be what was asked (a slow swell for 'plucky') gets an
extra penalty. The preset's name nudges the order a little (shared words up, contradicting words down)."""
import math
import re

import numpy as np

DIMS = ("bright", "low_end", "attack", "sustain", "width", "dirt")
# word -> {dimension: direction} (attack +1 = slow, sustain +1 = long, dirt +1 = distorted, low_end +1 = bassy)
WANT_WORDS = {
    "soft": {"attack": +0.6, "bright": -0.4}, "gentle": {"attack": +0.6, "bright": -0.4},
    "warm": {"bright": -0.7}, "mellow": {"bright": -0.7, "dirt": -0.3}, "dark": {"bright": -1},
    "bright": {"bright": +1}, "brilliant": {"bright": +1}, "shiny": {"bright": +0.8}, "sparkly": {"bright": +0.8},
    "airy": {"bright": +0.5, "width": +0.5}, "crisp": {"bright": +0.7, "attack": -0.5},
    "pad": {"sustain": +1, "attack": +0.6}, "long": {"sustain": +1}, "sustained": {"sustain": +1},
    "sustain": {"sustain": +1}, "string": {"sustain": +1, "attack": +0.5}, "swell": {"attack": +1.5, "sustain": +0.5},
    "pluck": {"sustain": -1, "attack": -1}, "plucky": {"sustain": -1, "attack": -1}, "plucked": {"sustain": -1, "attack": -1},
    "short": {"sustain": -1}, "staccato": {"sustain": -1, "attack": -0.6}, "stab": {"attack": -1, "sustain": -0.6},
    "punchy": {"attack": -1}, "percussive": {"attack": -1, "sustain": -0.6}, "snappy": {"attack": -1, "sustain": -0.5},
    "sharp": {"attack": -0.8, "bright": +0.3},
    "sequencer": {"attack": -0.7, "sustain": -0.7}, "seq": {"attack": -0.7, "sustain": -0.7},
    "arp": {"attack": -0.7, "sustain": -0.7}, "arpeggio": {"attack": -0.7, "sustain": -0.7},
    "lead": {"width": -0.5, "sustain": +0.3}, "mono": {"width": -1}, "narrow": {"width": -0.8},
    "wide": {"width": +1}, "stereo": {"width": +0.6}, "spacious": {"width": +0.6},
    "felt": {"bright": -1, "attack": +0.5}, "bell": {"bright": +0.6, "sustain": +0.3, "attack": -0.8},
    "bassy": {"low_end": +1, "bright": -0.4}, "bass": {"low_end": +1, "bright": -0.4}, "sub": {"low_end": +1.2, "bright": -0.6},
    "deep": {"low_end": +0.8, "bright": -0.4}, "boomy": {"low_end": +1}, "thin": {"low_end": -0.7},
    "clean": {"dirt": -1.5}, "pure": {"dirt": -1, "bright": -0.2}, "smooth": {"dirt": -0.6, "attack": +0.3},
    "distorted": {"dirt": +1.2}, "distortion": {"dirt": +1.2}, "dirty": {"dirt": +1}, "gritty": {"dirt": +1},
    "crunchy": {"dirt": +1}, "overdriven": {"dirt": +1.2}, "fuzzy": {"dirt": +1}, "saturated": {"dirt": +0.6},
}
PHRASES = {("short", "decay"): {"sustain": -1}, ("fast", "decay"): {"sustain": -1}, ("quick", "decay"): {"sustain": -1},
           ("short", "release"): {"sustain": -0.5}, ("long", "decay"): {"sustain": +1}, ("long", "release"): {"sustain": +0.6},
           ("fast", "attack"): {"attack": -1}, ("quick", "attack"): {"attack": -1}, ("sharp", "attack"): {"attack": -1},
           ("hard", "attack"): {"attack": -1}, ("slow", "attack"): {"attack": +1}, ("soft", "attack"): {"attack": +0.8},
           ("low", "end"): {"low_end": +1}}
NEGATORS = ("not", "no", "non", "without", "less", "never", "nothing", "avoid")
DIM_WORDS = {"bright": ("bright", "dark"), "low_end": ("bassy", "not bassy"), "attack": ("slow attack", "fast attack"),
             "sustain": ("long", "short"), "width": ("wide", "mono"), "dirt": ("distorted", "clean")}
WEIGHTS = {"bright": 1.0, "low_end": 1.0, "attack": 1.0, "sustain": 1.0, "width": 0.5, "dirt": 1.2}
# preset-name words: what they say about a sound (the preset's own name is evidence too)
NAME_DIRT = ("dist", "distort", "distorted", "distortion", "overdrive", "overdriven", "od", "fuzz", "fuzzy", "crunch",
             "crunchy", "grit", "gritty", "dirty", "crush", "crushed", "bitcrush", "bitcrushed", "drive", "driven",
             "saturated", "metal", "heavy", "scream", "rage")
NAME_LONG = ("pad", "pads", "swell", "swells", "drone", "atmos", "atmosphere", "strings", "choir", "evolving", "texture")
NAME_SHORT = ("pluck", "plk", "stab", "seq", "arp", "staccato", "pizz", "perc", "mallet")
NAME_BASS = ("bass", "sub", "808", "reese", "bs")
NAME_GUITAR = ("guitar", "gtr", "slayer", "riff", "strat", "les paul")
SYNTH_WISH = ("synth", "analog", "analogue", "sequencer", "seq", "arp", "square", "saw", "pulse", "juno", "moog",
              "oscillator", "osc", "chiptune", "chip")
STOP = {"a", "an", "the", "and", "with", "for", "sound", "sounds", "like", "very", "quite", "bit", "some", "but", "of"}


def describe(x, sr, hold_s=None):
    """x: (n, 2) float. Returns attack_ms, sustain_db (level 1.5 s after the peak vs the peak - measured while the note
    is held when hold_s is given, so a release after note-off isn't read as a short decay), centroid_hz, low_share
    (energy under 200 Hz), flatness (spectral flatness: noise / distortion vs clean tones), level_db, width_db and a
    few words."""
    mono = x.mean(1) if x.ndim > 1 else x
    if not len(mono) or float(np.abs(mono).max()) < 1e-4:
        return {"silent": True}
    hop = max(1, int(0.01 * sr))
    env = np.array([np.sqrt(np.mean(mono[i:i + hop] ** 2)) for i in range(0, max(len(mono) - hop, 1), hop)]) + 1e-9
    onset = int(np.argmax(env > env.max() * 0.05))
    held_end = len(env) - 1
    if hold_s:                                            # frames while the key is down (the recording starts at it)
        held_end = max(onset + 1, min(len(env) - 1, onset + int(float(hold_s) * 100) - 2))
    pk = onset + int(np.argmax(env[onset:held_end + 1]))
    attack_ms = (pk - onset) * 10
    later = min(held_end, pk + 150)
    if later <= pk + 5 and hold_s:                        # still rising when the key went up: it never decayed
        sustain_db = 0.0
    else:
        sustain_db = 20 * math.log10(env[max(later, pk)] / env[pk])
    seg = mono[onset * hop: onset * hop + int(1.5 * sr)]
    if len(seg) > 64:
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        f = np.fft.rfftfreq(len(seg), 1 / sr)
    else:
        spec, f = np.zeros(2), np.zeros(2)
    tot = float(spec.sum()) + 1e-12
    cen = float((spec * f).sum() / tot)
    pw = spec ** 2
    low_share = float(pw[f < 200].sum() / (pw.sum() + 1e-12)) if len(seg) > 64 else 0.0
    band = (f > 60) & (f < min(12000.0, sr / 2 - 1))
    if len(seg) > 64 and band.sum() > 8:
        sb = spec[band] + 1e-12
        flat = float(np.exp(np.mean(np.log(sb))) / np.mean(sb))
    else:
        flat = 0.0
    if x.ndim > 1 and x.shape[1] > 1:
        side = (x[:, 0] - x[:, 1]) / 2; width = 10 * math.log10((np.mean(side ** 2) + 1e-12) / (np.mean(mono ** 2) + 1e-12))
    else:
        width = -60.0
    words = []
    words.append("slow swell" if attack_ms > 250 else "soft attack" if attack_ms > 60 else "sharp attack")
    words.append("long sustain" if sustain_db > -6 else "medium decay" if sustain_db > -18 else "short / plucky")
    words.append("bright" if cen > 2500 else "dark / warm" if cen < 900 else "balanced tone")
    if low_share > 0.5:
        words.append("bassy")
    if width > -10:
        words.append("wide")
    return {"attack_ms": int(attack_ms), "sustain_db": round(sustain_db, 1), "centroid_hz": int(cen),
            "low_share": round(low_share, 3), "flatness": round(flat, 3),
            "level_db": round(20 * math.log10(float(np.abs(mono).max())), 1), "width_db": round(width, 1),
            "sounds": ", ".join(words)}


def _tokens(text):
    return [w for w in re.findall(r"[a-z0-9]+", str(text or "").lower().replace("-", " ")) if w not in STOP]


def _lookup(w):
    if w in WANT_WORDS:
        return WANT_WORDS[w]
    for cut in ("s", "es", "y", "ed", "ing"):             # pads -> pad, plucky -> pluck, swells -> swell
        if w.endswith(cut) and w[:-len(cut)] in WANT_WORDS:
            return WANT_WORDS[w[:-len(cut)]]
    return None


def targets(want):
    """The wish's words -> {dimension: target} (positive = more of it). Negation flips the next known word / phrase."""
    toks = _tokens(want)
    out, neg, i = {}, False, 0
    while i < len(toks):
        w = toks[i]
        if w in NEGATORS:
            neg = True; i += 1; continue
        hit, step = None, 1
        if i + 1 < len(toks) and (w, toks[i + 1]) in PHRASES:
            hit, step = PHRASES[(w, toks[i + 1])], 2
        else:
            hit = _lookup(w)
        if hit:
            for k, v in hit.items():
                out[k] = out.get(k, 0.0) + (-v if neg else v)
            neg = False
        elif neg and w not in ("too", "so", "overly"):    # 'not too bassy': the negation waits for the known word
            neg = w in ("too", "so", "overly")
        i += step
    return {k: max(-2.0, min(2.0, v)) for k, v in out.items() if abs(v) > 1e-9}


def features(desc, name=""):
    """Measured (and named) dimensions of a description, each squashed to -1..1."""
    nm = set(_tokens(name))
    f = {"bright": math.tanh(math.log2(max(desc.get("centroid_hz", 1500), 50) / 1500.0) / 1.2),
         "attack": math.tanh(math.log10(max(desc.get("attack_ms", 60), 5) / 60.0) * 1.2),
         "sustain": math.tanh((desc.get("sustain_db", -12) + 12) / 9.0),
         "width": math.tanh((desc.get("width_db", -12) + 12) / 8.0)}
    if desc.get("low_share") is not None:
        f["low_end"] = math.tanh((float(desc["low_share"]) - 0.3) * 4.0)
    else:                                                 # older descriptions: from the centroid
        f["low_end"] = math.tanh(math.log2(500.0 / max(desc.get("centroid_hz", 1500), 50)))
    dirt = math.tanh((float(desc.get("flatness") or 0.0) - 0.2) * 6.0) * 0.6
    if nm & set(NAME_DIRT):
        dirt = max(dirt, 1.0)
    f["dirt"] = dirt
    return f


def score(desc, want, name="", detail=False):
    """Higher = closer to the wish. 0 when the wish has no known words (and the name says nothing). Silent: -99.
    name: the preset's name / path (its words count a little; 'DIST' etc. count as distortion). detail=True ->
    {score, why}."""
    if desc.get("silent"):
        return {"score": -99.0, "why": "silent"} if detail else -99.0
    t = targets(want)
    f = features(desc, name)
    s, fits, against, why = 0.0, [], [], []
    for k, v in t.items():
        c = WEIGHTS.get(k, 1.0) * v * f.get(k, 0.0)
        s += c
        if abs(c) >= 0.4:
            (fits if c > 0 else against).append(DIM_WORDS[k][0 if v > 0 else 1])
    if t.get("sustain", 0) < 0 and desc.get("attack_ms", 0) > 250:     # a slow swell is never plucky / short
        s -= 1.5; why.append("too slow for short")
    if t.get("attack", 0) < 0 and desc.get("attack_ms", 0) > 400:
        s -= 1.0
    if t.get("low_end", 0) < 0 and desc.get("centroid_hz", 1500) < 500:  # 'not bassy': a dull, low sound sinks
        s -= 0.8; why.append("low / bassy")
    if t.get("bright", 0) > 0 and desc.get("centroid_hz", 1500) < 600:
        s -= 0.6
    # the preset's name: shared words up a little, contradicting words down
    nm, ws = set(_tokens(name)), set(_tokens(want))
    shared = {w for w in ws & nm if len(w) > 2 and w not in NEGATORS}
    s += min(0.6, 0.2 * len(shared))
    if t.get("sustain", 0) < 0 and nm & set(NAME_LONG):
        s -= 0.4
    if t.get("sustain", 0) > 0 and nm & set(NAME_SHORT) and not ws & set(NAME_SHORT):
        s -= 0.3
    if t.get("low_end", 0) < 0 and nm & set(NAME_BASS):
        s -= 0.6
    if ws & set(SYNTH_WISH) and not ws & set(NAME_GUITAR) and (nm & set(NAME_GUITAR) or "slayer" in str(name).lower()):
        s -= 0.8; why.append("a guitar, not a synth")
    out = round(s, 3)
    txt = "; ".join(x for x in (("fits: " + ", ".join(fits)) if fits else "",
                                ("not: " + ", ".join(against)) if against else "", ", ".join(why)) if x)
    return {"score": out, "why": txt} if detail else out
