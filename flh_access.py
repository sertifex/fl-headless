"""fl-headless accessibility: music for every music enjoyer - deaf and hard-of-hearing people, blind and low-vision
people using screen readers, people who can't use a mouse well, people with cognitive or learning differences,
and complete beginners.

Principles (binding for this module):
  - Never assume the user can hear, see the screen, or use a mouse. Every result is understandable as plain text;
    pictures always come with alt text; numbers always come with words.
  - Respect: no pity, no medical language, no guessing about anyone's body. People opt in by saying what helps
    them (fl_access set=...); nothing here is ever inferred.
  - Honest: say what is measured, what is read from the notes, and what is interpretation.

Tools
  fl_access          opt-in preferences: hearing / vision / motor / language / pace / images / detail
  fl_describe        the music in words (tempo feel, key / mood, parts and roles, loud and quiet, rhythm, low end you
                     would feel, stereo picture, what changes between sections; compare= says what changed)
  fl_visualize       PNG pictures (energy, loudness, spectrogram, arrangement, piano_roll, mix_balance) with
                     high-contrast, colorblind-safe colors, big labels (own bitmap font) and alt text
  fl_second_opinion  "it sounds fine to me" vs the measurements, kindly (trusts numbers where the user said their
                     hearing is less reliable)
  fl_safe_listening  monitoring-level and break advice; honest that speaker SPL can't be measured
  fl_where           a text map of the open FL (focus, selection, playhead, section, popups) for screen readers

Helpers for other modules (ns)
  ns["access_prefs"]()                 the user's opt-in preferences ({} when none)
  ns["access_text"](text, level=None)  plain-language mode: explains jargon from data/access/glossary.json
  ns["access_glossary"](text, level)   {term: plain explanation} for the jargon found in a text
  ns["access_adjust"](result, tool=None, user_says=None)   adds result["access"] notes (hearing-aware trust
                                       notes, listen -> describe alternatives, keyboard alternatives, glossary)
  ns["access_steps"](steps, pace=None, at=0)   step-by-step pacing of a multi-step flow
  ns["describe_music"](...)            fl_describe as a function; ns["access_visualize"](...) fl_visualize

Data (users override any file in <state folder>/access/): data/access/glossary.json, describe.json (number ->
words tables), hearing.json (hearing-aware settings, tone rules of thumb, safe-listening text), keys.json.
Preferences live in the collection profile under "access" and, so they follow the person across collections, in
fl-headless's state file (state["access"]).

Follows the extension contract in flh_store.py: register(ns) keeps the server namespace in H; server functions are
looked up in H at call time.
"""
import base64
import json
import math
import os
import re
import sys
import time

import flh_common as C
import flh_store as S

H = {}
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data", "access")
_DATA = {}
_PREFS = [None, None]                                        # [cache key, prefs]
_T0 = [time.time()]
NAMES = "C C# D Eb E F F# G Ab A Bb B".split()
OFFLINE_TOOLS = {"fl_access", "fl_describe", "fl_visualize", "fl_second_opinion", "fl_safe_listening"}

PREF_VALUES = {
    "hearing": ("typical", "reduced", "reduced_highs", "reduced_lows", "hearing_aids", "cochlear_implant", "deaf"),
    "vision": ("typical", "low", "blind"),
    "motor": ("typical", "limited", "keyboard_only", "voice", "switch"),
    "language": ("plain", "standard", "pro"),
    "pace": ("step_by_step", "normal"),
    "images": ("auto", "always", "never"),
    "detail": ("short", "full"),
}
PREF_ALIASES = {
    "vision": {"normal": "typical", "fine": "typical", "full": "typical", "low vision": "low", "low_vision": "low",
               "partial": "low", "screen reader": "blind", "screen_reader": "blind", "no vision": "blind"},
    "motor": {"normal": "typical", "fine": "typical", "no mouse": "keyboard_only", "keyboard": "keyboard_only",
              "keyboard only": "keyboard_only", "voice control": "voice", "speech": "voice",
              "switch access": "switch", "mouse_hard": "limited", "limited mouse": "limited"},
    "language": {"simple": "plain", "beginner": "plain", "easy": "plain", "normal": "standard", "expert": "pro",
                 "technical": "pro", "jargon": "pro"},
    "pace": {"step by step": "step_by_step", "steps": "step_by_step", "slow": "step_by_step", "one at a time":
             "step_by_step", "all": "normal", "all at once": "normal", "fast": "normal"},
    "images": {"on": "always", "yes": "always", "true": "always", "off": "never", "no": "never", "false": "never"},
    "detail": {"brief": "short", "long": "full", "detailed": "full"},
}
PREF_EFFECTS = {
    "hearing": "measurements are trusted over listening where you said hearing is less reliable; 'listen to check' "
               "steps get a no-listening alternative (fl_describe / fl_visualize)",
    "vision": "low: bigger labels and thicker lines in pictures; blind: pictures are skipped unless asked, results "
              "lead with text",
    "motor": "results that say 'click' get chat and keyboard alternatives",
    "language": "plain: jargon explained in brackets; standard: deep jargon explained; pro: no explanations",
    "pace": "step_by_step: multi-step flows one step at a time",
    "images": "auto (default) | always | never include pictures",
    "detail": "default detail of fl_describe",
}


# ============================================================================= data
def _user_file(name):
    st = H.get("STATE")
    return os.path.join(os.path.dirname(st), "access", name) if st else None


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v
    return a


def data(name):
    """data/access/<name>.json merged with the user's <state folder>/access/<name>.json (theirs wins)."""
    up = _user_file(name + ".json")
    key = (name, os.path.getmtime(up) if up and os.path.isfile(up) else 0)
    hit = _DATA.get(name)
    if hit and hit[0] == key:
        return hit[1]
    with open(os.path.join(DATA_DIR, name + ".json"), encoding="utf-8") as f:
        d = json.load(f)
    if up and os.path.isfile(up):
        try:
            with open(up, encoding="utf-8") as f:
                _merge(d, json.load(f))
        except (OSError, ValueError):
            pass
    _DATA[name] = (key, d)
    return d


def _pick(table, v):
    """[[upper limit, words], ...] -> words for v (limit None = no upper limit)."""
    for lim, w in table:
        if lim is None or v < lim:
            return w
    return table[-1][1]


def _mod(name):
    """Another fl-headless module (flh_review, flh_transcribe) - already loaded by the server, else imported."""
    m = sys.modules.get(name)
    if m is None:
        try:
            import importlib
            m = importlib.import_module(name)
        except Exception:
            return None
    if getattr(m, "H", None) in (None, {}) and H:
        m.H = H                                              # used before the server registered it (tests, tools)
    return m


# ============================================================================= preferences (opt-in only)
def _state_access():
    try:
        return dict(H["_state"]().get("access") or {})
    except Exception:
        return {}


def access_prefs():
    """The user's opt-in accessibility preferences ({} when they never said anything)."""
    st = H.get("STATE") or ""
    try:
        name = S.profile_name()
        pp = os.path.join(S._dir(), name + ".json") if name else ""
        key = (os.path.getmtime(st) if os.path.isfile(st) else 0, pp, os.path.getmtime(pp) if pp and os.path.isfile(pp) else 0)
    except Exception:
        key = None
    if key is not None and _PREFS[0] == key:
        return dict(_PREFS[1])
    p = _state_access()
    try:
        p.update(S.profile_get().get("access") or {})
    except Exception:
        pass
    p = {k: v for k, v in p.items() if v not in (None, "")}
    _PREFS[0], _PREFS[1] = key, p
    return dict(p)


def _norm_pref(k, v):
    if k == "notes":
        return str(v)[:300]
    if k not in PREF_VALUES:
        raise ValueError(f"unknown preference '{k}' (have: {', '.join(list(PREF_VALUES) + ['notes'])})")
    s = str(v).strip().lower().replace("-", "_") if not isinstance(v, bool) else ("always" if v else "never")
    if k == "hearing":
        al = data("hearing").get("aliases") or {}
        s = al.get(s, al.get(s.replace("_", " "), s))
        if s == "none":
            raise ValueError("hearing 'none' is ambiguous: say 'deaf' (no hearing used for music) or 'typical' (no "
                             "hearing notes)")
    else:
        al = PREF_ALIASES.get(k, {})
        s = al.get(s, al.get(s.replace("_", " "), s))
        if s == "none" and k in ("vision", "motor"):
            raise ValueError(f"{k} 'none' is ambiguous: say 'typical' (nothing to adapt) or one of "
                             f"{', '.join(PREF_VALUES[k][1:])}")
    if s not in PREF_VALUES[k]:
        raise ValueError(f"{k}: one of {', '.join(PREF_VALUES[k])}")
    return s


def fl_access(set=None, clear=None):
    """Read (no arguments), set or clear the user's accessibility preferences. Only what the user said."""
    if set:
        if not isinstance(set, dict):
            raise ValueError("set: {hearing|vision|motor|language|pace|images|detail|notes: value}")
        upd = {k: _norm_pref(k, v) for k, v in set.items() if v is not None}
        where = _store(upd)
        now = access_prefs()
        return {"saved": upd, "access": now, "what_changes": {k: PREF_EFFECTS.get(k, "kept as a note for every "
                                                                  "session") for k in upd},
                "stored_in": where, "note": C.tool_call(H, "fl_access", {"clear": C.Raw("the keys")}) + " removes it"}
    if clear:
        keys = list(PREF_VALUES) + ["notes"] if clear in ("all", ["all"], True) else (
            [clear] if isinstance(clear, str) else list(clear))
        _store({k: None for k in keys})
        return {"cleared": keys, "access": access_prefs()}
    p = access_prefs()
    return {"access": p or None, "options": {k: list(v) for k, v in PREF_VALUES.items()},
            "what_each_does": PREF_EFFECTS,
            "note": ("nothing set: everything works by chat for everyone; set only what the user tells you helps them "
                     "(e.g. 'I'm hard of hearing' -> hearing=reduced; 'I use a screen reader' -> vision=blind)")
            if not p else "these are the user's own choices; change them only when they ask"}


def _store(upd):
    """Merge into fl-headless's state (follows the person across collections) and, when a collection profile is
    active, into its "access" block too. Never creates a profile by itself. -> where it was stored."""
    st = _state_access()
    st.update(upd)
    H["_state"]({"access": {k: v for k, v in st.items() if v is not None}})
    where = ["fl-headless state"]
    name = S.profile_name()
    if name and S.profile_get(name):
        cur = (S.profile_get(name) or {}).get("access") or {}
        new = {k: v for k, v in dict(cur, **dict(st, **upd)).items() if v is not None}
        S.profile_set(name, {"access": None}, activate=False)     # replace the block (None-merge drops keys)
        S.profile_set(name, {"access": new}, activate=False)
        where.append(f"collection profile '{name}'")
    _PREFS[0] = None
    return where


# ============================================================================= plain language
def _gloss_index():
    g = data("glossary")["terms"]
    idx = []
    for term, e in g.items():
        for t in [term] + list(e.get("aliases") or []):
            idx.append((t, term, e))
    idx.sort(key=lambda x: -len(x[0]))                       # "true peak" before "peak", "low end" before "end"
    return idx


_TERM_RX = {}


def _term_rx(t):
    rx = _TERM_RX.get(t)
    if rx is None:
        rx = _TERM_RX[t] = re.compile(r"(?<![\w_=/.-])" + re.escape(t) + r"(?![\w_=(]|-\w)", re.I)
    return rx


def _levels(level):
    return {"plain": ("basic", "pro"), "standard": ("pro",)}.get(level, ())


def access_glossary(text, level="plain", limit=12):
    """{term: plain explanation} for the jargon in `text` that `level` explains (plain: all, standard: deep jargon)."""
    want = _levels(level); out = {}
    if not want or not text:
        return out
    for t, term, e in _gloss_index():
        if term in out or e.get("level", "basic") not in want:
            continue
        if _term_rx(t).search(text):
            out[term] = e["plain"]
            if len(out) >= limit:
                break
    return out


def access_text(text, level=None):
    """Plain-language mode: the first use of each jargon word gets its explanation in brackets.
    level: plain (all jargon) | standard (deep jargon only) | pro (unchanged); None = the user's preference
    (unchanged when they set none)."""
    if level is None:
        level = access_prefs().get("language")
    want = _levels(level)
    if not want or not text:
        return text
    done = set(); spans = []
    for t, term, e in _gloss_index():
        if term in done or e.get("level", "basic") not in want:
            continue
        for mt in _term_rx(t).finditer(text):
            a, b = mt.span()
            if any(a < y and b > x for x, y in spans):
                continue
            if text[b:b + 2] == " (" or text[b:b + 1] in ("(", ":"):
                continue                                    # already explained here, or a name ("Pad: ...")
            if text.count("(", 0, a) > text.count(")", 0, a):
                continue                                    # inside brackets: no nested explanations
            spans.append((a, b)); done.add(term)
            break
    out = text
    for a, b in sorted(spans, reverse=True):
        term = next(term for t, term, e in _gloss_index() if _term_rx(t).fullmatch(out[a:b]))
        out = out[:b] + " (" + data("glossary")["terms"][term]["plain"] + ")" + out[b:]
    return out


def access_steps(steps, pace=None, at=0, title=None):
    """Pacing for multi-step flows. step_by_step: only the current step and how to go on; normal: all steps,
    numbered. pace None = the user's preference."""
    steps = [str(s) for s in (steps or [])]
    pace = pace or access_prefs().get("pace") or "normal"
    n = len(steps)
    if pace != "step_by_step" or n <= 1:
        return {"steps": [f"{i + 1}. {s}" for i, s in enumerate(steps)], **({"title": title} if title else {})}
    at = max(0, min(int(at), n - 1))
    return {**({"title": title} if title else {}), "step": at + 1, "of": n, "now": steps[at],
            "next": ("when this is done, say 'next' for step " + str(at + 2)) if at + 1 < n else "this is the last step",
            "remaining": n - at - 1, "note": "one step at a time (pace=step_by_step); 'show all steps' lists them"}


# ============================================================================= hearing-aware notes
_LISTEN_RX = re.compile(r"\b(listen|by ear|a human should listen|hear it|audition)\b", re.I)
_CLICK_RX = re.compile(r"\b(click|drag|right-click|double-click|mouse)\b", re.I)


def _strings(o, depth=0):
    if depth > 4:
        return
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            if k not in ("_image_png", "steps_s", "access"):
                yield from _strings(v, depth + 1)
    elif isinstance(o, (list, tuple)):
        for v in o[:60]:
            yield from _strings(v, depth + 1)


def _setting(prefs):
    h = prefs.get("hearing")
    if not h or h == "typical":
        return None, None
    return h, (data("hearing")["settings"].get(h) or {})


def _covers(st, kind):
    ks = st.get("kinds") or []
    return "*" in ks or kind in ks


def _says_positive(user_says):
    if not user_says:
        return None
    s = " " + str(user_says).lower().strip() + " "
    hd = data("hearing")
    if re.search(r"\b(not|n't|isn't|doesn't|dont|don't)\b", s) and not re.search(r"can'?t hear a problem", s):
        return False
    if any(re.search(r"\b" + re.escape(w) + r"\b", s) for w in hd.get("negative_words") or []):
        return False
    if any(re.search(r"\b" + re.escape(w) + r"\b", s) for w in hd.get("positive_words") or []):
        return True
    return None


def access_adjust(result, tool=None, user_says=None, prefs=None):
    """Post-process a tool result for the user's opt-in preferences: adds result["access"] = [notes] (never changes
    anything else). Hearing: issues in ranges the user said they hear less get 'trust the measurement' notes (and a
    kind flag when they said it sounds fine); 'listen to check' gets a no-listening alternative. Motor: 'click'
    gets chat/keyboard alternatives. Language: a small glossary of the jargon in the result. No prefs: unchanged."""
    if not isinstance(result, dict):
        return result
    prefs = access_prefs() if prefs is None else prefs
    if not prefs and not user_says:
        return result
    notes = []
    h, st = _setting(prefs)
    issues = [i for i in (result.get("issues") or []) if isinstance(i, dict)]
    pos = _says_positive(user_says)
    if st:
        hit = [i for i in issues if _covers(st, i.get("kind", ""))][:4]
        for i in hit:
            w = i.get("where") or i.get("section") or ""
            notes.append(f"{i.get('what') or i.get('kind')}{' (' + str(w) + ')' if w and str(w) not in str(i.get('what')) else ''}"
                         f": {st.get('say', 'measured')}")
        if pos and hit:
            notes.insert(0, "You said it sounds fine - and it may well feel fine. The measurement still shows "
                            f"{len(hit)} thing{'s' if len(hit) > 1 else ''} in a range where it's the steadier guide "
                            "for you, so it's worth a look.")
        text = " ".join(_strings(result))
        if h in ("deaf", "reduced", "cochlear_implant", "hearing_aids") and _LISTEN_RX.search(text):
            notes.append("Where this says to listen: " +
                         C.tool_call(H, "fl_describe", {"compare": C.Raw("the version before"), "...": 1}) +
                         " says in words what changed, and " +
                         C.tool_call(H, "fl_visualize", {"kind": C.Raw("energy | spectrogram"), "...": 1}) +
                         " shows it; listening is optional.")
    elif pos and issues and any(i.get("severity") == "high" for i in issues):
        notes.append("You said it sounds fine; the measurement still flags a high-priority item above - worth a look.")
    if prefs.get("motor") in ("limited", "keyboard_only", "voice", "switch"):
        if _CLICK_RX.search(" ".join(_strings(result))):
            k = data("keys")
            notes.append("No mouse needed: ask Claude to do it (" + "; ".join(f"{a}: {C.hint_text(H, b)}" for a, b in
                                                                   list(k["by_chat"].items())[:3]) +
                         "), or FL's keys: " + ", ".join(f"{a} {b}" for a, b in list(k["fl_keys"].items())[:6]))
    lang = prefs.get("language")
    if lang in ("plain", "standard"):
        g = access_glossary(" ".join(_strings(result))[:6000], lang, 8)
        if g:
            result["glossary"] = g
    if prefs.get("pace") == "step_by_step" and isinstance(result.get("next"), list) and len(result["next"]) > 1:
        notes.append(f"{len(result['next'])} next steps: do the first one, then ask for the next")
    if notes:
        result["access"] = notes
    return result


# ============================================================================= audio facts
def _load_audio(path):
    import numpy as np
    if not os.path.isfile(str(path)):
        raise ValueError(f"no such audio file: {path}")
    rv = _mod("flh_review")
    if rv is not None and hasattr(rv, "load_audio"):
        x, fs, _ = rv.load_audio(path)                         # float32, read block-wise
    else:
        x, fs, _ = H["_wav_load"](path)
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[1] == 1:
        x = rv.dup_mono(x) if rv is not None and hasattr(rv, "dup_mono") else np.repeat(x, 2, 1)
    return x[:, :2], int(fs)


def _st_curve(p100, win=30):
    """Short-term loudness (3 s centered window) per 100 ms from the K-weighted 100 ms powers -> LUFS array
    (the window shrinks at the ends instead of averaging in silence)."""
    import numpy as np
    n = len(p100)
    if not n:
        return np.zeros(0)
    c = np.concatenate([[0.0], np.cumsum(p100)])
    i = np.arange(n); a = np.maximum(0, i - win // 2); b = np.minimum(n, i + win - win // 2)
    return -0.691 + 10 * np.log10(np.maximum((c[b] - c[a]) / (b - a), 1e-12))


def _onsets(x, fs):
    import numpy as np
    tr = _mod("flh_transcribe")
    if tr is None:
        return None, None
    q = max(1, int(fs // (22050 * 0.9)))
    if q > 1:                                               # short windowed-sinc low-pass + decimate, block-wise
        k = np.arange(-8 * q, 8 * q + 1)                     # (one huge FFT over the whole song is slower, and a
        h = np.sinc(k / q * 0.9) * np.hanning(len(k)); h /= h.sum()   # full-rate float64 copy costs memory)
        y = _decimate_mono(x, h, q)
    else:
        y = np.asarray(x[:, :2].mean(1), dtype=np.float64)
    r = fs / q
    env, rate = tr.onset_envelope(y, r)
    pk = tr.pick_peaks(env, rate)
    return np.asarray(pk) / rate, (y, r, env, rate)


def _decimate_mono(x, h, q, block=1 << 20):
    """mean of the first two channels, FIR h (odd length, centered) and keep every q-th sample, block by block:
    identical to np.convolve(x.mean(1), h, 'same')[::q] without a full-rate float64 copy."""
    import numpy as np
    n = len(x); half = len(h) // 2
    out = np.empty(-(-n // q))
    for a in range(0, n, block):
        first = -(-a // q) * q                                   # first output sample index >= a (keeps the phase)
        b = min(n, a + block)
        if first >= b:
            continue
        lo, hi = max(0, first - half), min(n, b + half)
        seg = np.asarray(x[lo:hi, :2], dtype=np.float64).mean(1)
        yc = np.convolve(seg, h, "full")[half:half + len(seg)]  # 'same' alignment for this segment
        idx = np.arange(first, b, q)
        out[idx // q] = yc[idx - lo]
    return out


def _sub_db(x, fs):
    import numpy as np
    f, Sp = H["_spectrum"](x[:, 0], fs)
    Sp = Sp + H["_spectrum"](x[:, 1], fs)[1]
    tot = Sp[(f >= 20) & (f < 16000)].sum() + 1e-20
    return round(float(10 * np.log10(Sp[(f >= 20) & (f < 60)].sum() / tot + 1e-12)), 1), (f, Sp)


def _lean(x):
    import numpy as np
    l, r = float(np.sqrt(np.mean(x[:, 0] ** 2)) + 1e-12), float(np.sqrt(np.mean(x[:, 1] ** 2)) + 1e-12)
    return round(20 * math.log10(l / r), 1)


def _fmt_t(s):
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


def _fmt_dur(s):
    return "under a second" if s < 1 else f"{s:.0f} seconds" if s < 60 else _fmt_t(s)


def _audio_facts(path, spans=None, want_tempo_key=True, onsets=True):
    """Measured facts of a render: whole song + per span [(name, start_s, end_s, extra)] (None: automatic windows)."""
    import numpy as np
    t0 = time.time()
    rv = _mod("flh_review")
    x, fs = _load_audio(path)
    dur = len(x) / fs
    if rv is None:
        raise RuntimeError("flh_review (loudness measuring) isn't installed")
    P100 = rv._p100(x, fs); hop = int(0.1 * fs)
    st = _st_curve(P100)
    t1 = time.time()
    if not spans:
        lo, hi = data("describe").get("auto_window_s") or [6, 20]
        w = max(lo, min(hi, dur / 8.0))
        n = max(1, int(round(dur / w)))
        w = dur / n
        spans = [(f"{_fmt_t(i * w)}-{_fmt_t((i + 1) * w)}", i * w, (i + 1) * w, {"auto": True}) for i in range(n)]
    on_t, tk = _onsets(x, fs) if onsets or want_tempo_key else (None, None)
    rows = []
    for name, a, b, extra in spans:
        a, b = max(0.0, a), min(dur, b)
        if b - a < 0.5:
            continue
        i0, i1 = int(a * fs), int(b * fs)
        m = rv.measure(x[i0:i1], fs, p100=P100[-(-i0 // hop):i1 // hop])
        seg = P100[-(-i0 // hop):i1 // hop]
        trend = None
        if len(seg) >= 40:                                      # >= 4 s: its own first vs last fifth (no bleed)
            q = max(10, len(seg) // 5)
            trend = round(float(10 * np.log10((seg[-q:].mean() + 1e-12) / (seg[:q].mean() + 1e-12))), 1)
        m.update(name=name, start_s=round(a, 2), end_s=round(b, 2), trend=trend, lean_db=_lean(x[i0:i1]),
                 onsets_per_s=round(float(((on_t >= a) & (on_t < b)).sum()) / (b - a), 2) if on_t is not None else None,
                 **(extra or {}))
        rows.append(m)
    t2 = time.time()
    whole = rv.measure(x, fs, p100=P100) if len(x) else {}
    sub, spec = _sub_db(x, fs)
    whole.update(sub_db=sub, lean_db=_lean(x), duration_s=round(dur, 1),
                 onsets_per_s=round(len(on_t) / dur, 2) if on_t is not None and dur else None)
    tempo = key = None
    t3 = time.time()
    silent = whole.get("lufs") is None or whole["lufs"] < -70
    if want_tempo_key and tk is not None and dur >= 8 and not silent:   # shorter: tempo / key would be a guess
        tr = _mod("flh_transcribe")
        y, r, env, rate = tk
        try:
            g = tr.beat_grid(y[:int(r * 120)], r, env_rate=(env[:int(rate * 120)], rate))
            if g.get("bpm"):
                tempo = {"bpm": round(float(g["bpm"]), 1), "confidence": round(float(g.get("confidence") or 0), 2)}
        except Exception:
            pass
        cfg = data("describe").get("confidence") or {}
        dens = len(on_t) / dur if on_t is not None and dur else 0.0
        if tempo and (tempo["confidence"] < cfg.get("tempo_min", 0.5) or dens < cfg.get("onsets_per_s_min", 0.5)):
            tempo = {"bpm": None, "unclear": True, "confidence": tempo["confidence"], "onsets_per_s": round(dens, 2)}
        try:
            C, _, _ = tr.chroma(y[:int(r * 150)], r)
            if len(C):
                nm, corr, margin = tr.estimate_key(C.sum(0))
                if nm is None or margin < cfg.get("key_margin_min", 0.05) or corr < cfg.get("key_r_min", 0.5):
                    key = {"name": None, "unclear": True, "r": corr, "margin": margin}
                else:
                    key = {"name": nm, "r": corr, "margin": margin}
        except Exception:
            pass
    return {"rows": rows, "whole": whole, "st": st, "fs": fs, "x": x, "spectrum": spec, "tempo": tempo, "key": key,
            "duration_s": dur, "path": path, "silent": silent,
            "steps_s": {"load_loudness": round(t1 - t0, 3), "sections": round(t2 - t1, 3),
                        "tempo_key": round(time.time() - t3, 3)}}


# ============================================================================= score facts
def _score_facts(project, section=None):
    """Notes facts of a .flp / .mid. With edits queued for the project (lazy mode) it reads the queued state (the
    shadow copy, with the real project's journal) and says so ("queued": n)."""
    if "score_view" not in H:
        return None
    src, queued = C.shadow_read(H, project) if str(project).lower().endswith(".flp") else (project, 0)
    jf = {"journal_from": project} if queued else {}
    sv = H["score_view"](project=src, section=section, **jf)
    m = H["score_model"](src, *([project] if queued else [])) if "score_model" in H else None
    if queued:
        sv = C.unshadow(H, sv, src, project, 0)                    # (paths only; the note goes on the result)
    swing = None
    if m and m.get("notes") and "analyze_notes" in H:
        try:
            ns_ = [{"pos": n[0], "len": n[1], "key": n[3], "vel": n[4]} for n in m["notes"][:4000]]
            an = H["analyze_notes"](ns_, m["P"], bpm=sv.get("tempo"), beats_per_bar=m["bpb"])
            swing = an.get("swing")
        except Exception:
            swing = None
    return {"view": sv, "model": m, "swing": swing, "queued": queued}


def _queued_note(res, *sfs):
    """Say when the notes were read from the queued (not yet in FL) state."""
    n = max([(sf or {}).get("queued") or 0 for sf in sfs] or [0])
    if n and isinstance(res, dict):
        res["pending_ops"] = n
        res["pending_note"] = C.shadow_note(n)
    return res


def _bar_time_fn(m):
    rv = _mod("flh_review")
    if not m or rv is None:
        return None
    return rv.bar_seconds(m.get("tempos") or [(0, 120.0)], m["P"], m["bpb"])


def _spans_from_score(sf):
    if not sf:
        return None
    tf = _bar_time_fn(sf["model"])
    if tf is None:
        return None
    out = []
    for s in sf["view"].get("sections") or []:
        out.append((s["name"], tf(s["bar"]), tf(s["end_bar"]), {"bar": s["bar"], "end_bar": s["end_bar"]}))
    return out or None


def _kind_of(path):
    p = str(path).lower()
    if p.endswith((".wav", ".flac", ".wave")):
        return "audio"
    if p.endswith((".flp", ".mid", ".midi")):
        return "project"
    raise ValueError(f"{path}: give a .flp / .mid (project) or a .wav / .flac (audio)")


def _section_filter(rows, section):
    """rows (dicts with name, bar/end_bar or start_s/end_s) matching a section name, 'a-b' bars or 'm:ss-m:ss'."""
    if not section:
        return rows
    s = str(section).strip()
    mt = re.fullmatch(r"(\d+):(\d{1,2})\s*-\s*(\d+):(\d{1,2})", s)
    if mt:
        a = int(mt.group(1)) * 60 + int(mt.group(2)); b = int(mt.group(3)) * 60 + int(mt.group(4))
        hit = [r for r in rows if r.get("start_s") is not None and r["start_s"] < b and r["end_s"] > a]
        if hit:
            return hit
    mb = re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", s)
    if mb:
        a, b = float(mb.group(1)), float(mb.group(2)) + 1
        hit = [r for r in rows if r.get("bar") is not None and r["bar"] < b and r["end_bar"] > a]
        if hit:
            return hit
    low = s.lower()
    hit = [r for r in rows if str(r.get("name", "")).lower() == low] or \
          [r for r in rows if str(r.get("name", "")).lower().startswith(low)]
    if not hit:
        raise ValueError(f"no section '{section}' (have: {[r.get('name') for r in rows][:24]})")
    return hit


# ============================================================================= words
def _w(table_name, v):
    t = data("describe")[table_name]
    return _pick(t["words"] if isinstance(t, dict) else t, v)


def _bars_txt(r):
    if r.get("bar") is None:
        return ""
    a, b = r["bar"], r.get("end_bar")
    a_ = f"{a:g}"; b_ = f"{(b - 1):g}" if b else "?"
    return f"bar {a_}" if b and b - a <= 1 else f"bars {a_}-{b_}"


def _where_txt(r):
    parts = [p for p in (_bars_txt(r), f"{_fmt_t(r['start_s'])}-{_fmt_t(r['end_s'])}"
                         if r.get("start_s") is not None and not r.get("auto") else "") if p]
    return ", ".join(parts)


_BLOCK_RX = re.compile(r"\d+(\.\d+)?-\d+(\.\d+)?")


def _lab(name):
    """Display name: automatic 8-bar blocks ('9-16') read as 'bars 9-16'."""
    return f"bars {name}" if _BLOCK_RX.fullmatch(str(name)) else str(name)


def _note_name(k):
    return f"{NAMES[int(k) % 12]}{int(k) // 12 - 1}"


def _parse_note(s):
    mt = re.fullmatch(r"([A-G])([#b]?)(-?\d+)", str(s or ""))
    if not mt:
        return None
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[mt.group(1)]
    return 12 * (int(mt.group(3)) + 1) + base + {"#": 1, "b": -1, "": 0}[mt.group(2)]


def _tempo_words(bpm):
    d = data("describe")
    bps = bpm / 60.0
    return (f"{_pick(d['tempo'], bpm)} at about {bpm:.0f} BPM - about {bps:.1f} beats a second, "
            f"{_pick(d['tempo_body'], bpm)}")


def _mood(key_name, bpm, tilt):
    d = data("describe")["mood"]
    if not key_name:
        return None
    minor = "minor" in key_name
    slow = bpm is not None and bpm < d["slow_below_bpm"]
    w = d[("minor" if minor else "major") + ("_slow" if slow else "_fast")]
    if tilt is not None:
        if tilt >= d["bright_tilt"]:
            w += " " + d["bright_word"]
        elif tilt <= d["dark_tilt"]:
            w += " " + d["dark_word"]
    return w


def _part_phrase(p):
    d = data("describe")
    name = p.get("name") or (p.get("instrument") or p.get("role") or "part")
    ins = p.get("instrument")
    label = name + (f" ({ins})" if ins and ins.lower() not in name.lower() else "")
    role = d["roles"].get(p.get("role") or "other", d["roles"]["other"])
    if p.get("role") == "drums":
        return f"{label}: {role}"
    lo, hi = _parse_note(p.get("low")), _parse_note(p.get("high"))
    if lo is None or hi is None:
        return f"{label}: {role}"
    reg = _pick(d["register"], (lo + hi) / 2)
    felt = ", low enough to be felt as vibration on big speakers" if lo < 40 else ""
    return f"{label}: {reg} ({p.get('low')}-{p.get('high')}), {role}{felt}"


def _energy_rows(sf, af, section=None):
    """One row per section: name, where, level words (measured LU below the loudest, else score energy), change
    words vs the previous section, comparison with an earlier section."""
    d = data("describe")
    view = sf["view"] if sf else None
    srows = {s["name"]: s for s in (view.get("sections") or [])} if view else {}
    prog = (view.get("progression") or {}) if view else {}
    out = []
    if af:
        base = [r for r in af["rows"] if r.get("lufs") is not None and r["lufs"] > -70]
        top = max((r["lufs"] for r in base), default=None)
        for r in af["rows"]:
            lu = None if r.get("lufs") is None or r["lufs"] <= -70 or top is None else round(top - r["lufs"], 1)
            wt = _where_txt(dict(r, bar=None) if _BLOCK_RX.fullmatch(str(r["name"])) else r)
            row = {"name": r["name"], "where": wt, "lufs": r.get("lufs"), "below_top_lu": lu,
                   "level": "silent" if lu is None else _w("energy_lu", lu), "trend": r.get("trend"),
                   "bar": r.get("bar"), "end_bar": r.get("end_bar"), "start_s": r.get("start_s"), "end_s": r.get("end_s"),
                   "auto": r.get("auto")}
            if r["name"] in srows:
                row["energy"] = srows[r["name"]]["energy"]; row["parts"] = srows[r["name"]]["parts"]
            out.append(row)
    elif view:
        for s in view.get("sections") or []:
            out.append({"name": s["name"], "where": "" if _BLOCK_RX.fullmatch(s["name"]) else _bars_txt(s),
                        "energy": s["energy"], "parts": s["parts"],
                        "level": _w("energy_score", s["energy"]), "bar": s["bar"], "end_bar": s["end_bar"],
                        "notes_per_bar": s.get("notes_per_bar")})
    names = {}
    if sf:
        names = {p["ch"]: (p.get("name") or p.get("instrument") or f"ch{p['ch']}") for p in view.get("parts") or []}
    ratio = d.get("same_chords_ratio", 0.75)
    for i, row in enumerate(out):
        bits = []
        if i:
            prev = out[i - 1]
            if row.get("lufs") is not None and prev.get("lufs") is not None and prev["lufs"] > -70 and row["lufs"] > -70:
                bits.append(_pick(d["change_lu"], row["lufs"] - prev["lufs"]) + f" from {_lab(prev['name'])}")
            elif row.get("energy") is not None and prev.get("energy") is not None:
                de = row["energy"] - prev["energy"]
                bits.append(("builds" if de >= 2 else "lifts a little" if de >= 1 else "falls back" if de <= -2 else
                             "eases off a little" if de <= -1 else "stays at about the same level") + f" from {_lab(prev['name'])}")
            pa, pb = set(prev.get("parts") or []), set(row.get("parts") or [])
            if names and (pb - pa or pa - pb):
                inn = [names.get(c, f"ch{c}") for c in sorted(pb - pa)][:4]
                outp = [names.get(c, f"ch{c}") for c in sorted(pa - pb)][:4]
                if inn:
                    bits.append(", ".join(inn) + (" come in" if len(inn) > 1 else " comes in"))
                if outp:
                    bits.append(", ".join(outp) + (" drop out" if len(outp) > 1 else " drops out"))
        tr = row.get("trend"); trd = d["trend_lu"]
        if tr is not None and tr >= trd["rise"]:
            bits.append(f"builds up inside the section (+{tr:g} LU)")
        elif tr is not None and tr <= trd["fall"]:
            bits.append(f"fades down inside the section ({tr:g} LU)")
        # comparison: an earlier section with (mostly) the same chords
        seq = prog.get(row["name"])
        if seq and i:
            for j in range(i - 1, -1, -1):
                other = out[j]; sq = prog.get(other["name"])
                if not sq or other["name"] == row["name"]:
                    continue
                n = min(len(seq), len(sq))
                same = sum(1 for a, b in zip(seq, sq) if a == b) / max(1, max(len(seq), len(sq)))
                if n and same >= ratio:
                    fuller = None
                    if row.get("lufs") is not None and other.get("lufs") is not None:
                        dl = row["lufs"] - other["lufs"]
                        fuller = "louder" if dl >= 1.5 else "quieter" if dl <= -1.5 else None
                    np_, no = len(row.get("parts") or []), len(other.get("parts") or [])
                    fuller = fuller or ("fuller" if np_ > no else "thinner" if np_ < no else None)
                    if row.get("energy") is not None and other.get("energy") is not None and not fuller:
                        fuller = "bigger" if row["energy"] > other["energy"] else "smaller" if row["energy"] < other["energy"] else None
                    bits.append(f"same chords as {_lab(other['name'])}" + (f" but {fuller}" if fuller else ""))
                    break
        row["changes"] = bits
    if section:
        out = _section_filter(out, section) if out else out
    return out


def _shape_sentence(rows):
    """'Starts whisper-soft, peaks at Chorus (the loudest, biggest point), ends soft.'"""
    use = [r for r in rows if r.get("level") and r["level"] != "silent"]
    if len(use) < 2:
        return None
    key = (lambda r: -r["below_top_lu"]) if use[0].get("below_top_lu") is not None else (lambda r: r.get("energy") or 0)
    peak = max(use, key=key)
    jumps = []
    for a, b in zip(use, use[1:]):
        if a.get("lufs") is not None and b.get("lufs") is not None:
            dl = b["lufs"] - a["lufs"]
            if abs(dl) >= 6:
                jumps.append(f"{'a big jump up' if dl > 0 else 'a sharp drop'} into {_lab(b['name'])}")
        elif a.get("energy") is not None and b.get("energy") is not None and abs(b["energy"] - a["energy"]) >= 4:
            jumps.append(f"{'a big jump up' if b['energy'] > a['energy'] else 'a sharp drop'} into {_lab(b['name'])}")
    s = f"Starts {use[0]['level']}, peaks at {_lab(peak['name'])}" + (f" ({peak['where']})" if peak.get("where") else "")
    s += f", ends {use[-1]['level']}" if use[-1] is not peak else ", and ends there"
    if jumps:
        s += "; " + "; ".join(jumps[:3])
    return s + "."


def _sound_sentence(af):
    d = data("describe"); w = af["whole"]
    bits = []
    if w.get("tilt") is not None:
        bits.append(f"tone {_w('brightness_tilt', w['tilt'])}")
    bands = w.get("bands") or {}
    if bands.get("low") is not None:
        lw = _w("low_end", bands["low"])
        felt = bands["low"] >= d["low_end"]["felt_from_db"] or (w.get("sub_db") is not None and
                                                              w["sub_db"] >= d["low_end"]["sub_felt_from_db"])
        bits.append(lw + (" - on speakers or a subwoofer you'd feel it as vibration" if felt else ""))
    if w.get("corr") is not None:
        bits.append(f"stereo {_w('width_corr', w['corr'])}")
    if abs(w.get("lean_db") or 0) >= d["lean_db"]:
        bits.append(f"leans to the {'left' if w['lean_db'] > 0 else 'right'} ({abs(w['lean_db']):g} dB)")
    if w.get("crest") is not None:
        bits.append(_w("crest", w["crest"]))
    return "Sound: " + "; ".join(bits) + "." if bits else None


def _rhythm_sentence(sf, af):
    d = data("describe"); bits = []
    if sf and sf.get("swing") is not None:
        bits.append(_pick(d["swing"], sf["swing"]))
    elif sf:
        bits.append("straight (even notes)")
    if sf:
        view = sf["view"]
        pit = [p for p in view.get("parts") or [] if p.get("role") != "drums"]
        npb = sum(p.get("notes_per_bar") or 0 for p in pit)
        if pit:
            bits.append(f"{_w('density_npb', npb)} (about {npb:.0f} notes a bar)")
        drums = [p for p in view.get("parts") or [] if p.get("role") == "drums"]
        if drums:
            dn = sum(p.get("notes_per_bar") or 0 for p in drums)
            bits.append(f"drums keep the beat (about {dn:.0f} hits a bar)")
        else:
            bits.append("no drums")
    elif af and af["whole"].get("onsets_per_s") is not None:
        ops = af["whole"]["onsets_per_s"]
        bits.append(f"{_w('onsets_per_s', ops)} (about {ops:.1f} attacks a second, measured)")
    return "Rhythm: " + ", ".join(bits) + "." if bits else None


def _key_tempo(sf, af):
    """(bpm, bpm source, key name, key source, key confidence words, meter)."""
    bpm = key = meter = None; bsrc = ksrc = kconf = None
    if sf:
        v = sf["view"]
        bpm, bsrc, meter = v.get("tempo"), "project", v.get("meter")
        if v.get("key"):
            key, ksrc = v["key"][0][0], "notes"
            r = v["key"][0][1]
            kconf = "clear" if r >= 0.8 else "likely" if r >= 0.6 else "uncertain"
    if af:
        if bpm is None and af.get("tempo") and af["tempo"].get("bpm"):
            bpm, bsrc = af["tempo"]["bpm"], "measured"
        if key is None and af.get("key") and af["key"].get("name"):
            key, ksrc = af["key"]["name"], "measured"
            kconf = "likely" if af["key"]["margin"] >= 0.05 and af["key"]["r"] >= 0.6 else "uncertain"
    return bpm, bsrc, key, ksrc, kconf, meter


# ============================================================================= fl_describe
def _facts(project=None, audio=None, section=None):
    sf = _score_facts(project) if project else None
    af = _audio_facts(audio, _spans_from_score(sf), want_tempo_key=not sf) if audio else None
    return sf, af


def describe_music(project=None, audio=None, section=None, detail=None, compare=None, language=None):
    """The music in words. project: .flp / .mid (notes, parts, sections); audio: a WAV / FLAC render (measured
    sound). Either or both. section: one section (name, 'a-b' bars or 'm:ss-m:ss'). detail: short | full.
    compare: the version before (a .flp / .mid / .wav / .flac, or a list of two) -> says what changed."""
    t0 = time.time()
    if not project and not audio:
        if compare:
            raise ValueError("give the current version too: project= and/or audio=")
        try:
            project, _ = H["_open_project_guess"]()
        except Exception:
            project = None
        if not project:
            raise ValueError("give project= (.flp / .mid) and/or audio= (.wav / .flac)")
    for p in (project, audio):
        if p and not os.path.isfile(p):
            raise ValueError(f"no such file: {p}")
    prefs = access_prefs()
    detail = detail or prefs.get("detail") or "short"
    sf, af = _facts(project, audio, section)
    t1 = time.time()
    if compare:
        res = _describe_change(compare, sf, af, section, detail)
    else:
        res = _describe(sf, af, section, detail)
    lvl = language or prefs.get("language")
    if lvl in ("plain", "standard"):
        res["text"] = access_text(res["text"], lvl)
    res["steps_s"] = {"facts": round(t1 - t0, 3), "words": round(time.time() - t1, 3),
                      **({"audio": af["steps_s"]} if af else {})}
    if prefs.get("hearing") and prefs["hearing"] != "typical":
        res["access"] = ["everything above comes from measurements and the notes - no listening was needed"]
    return _queued_note(res, sf)


def _basis(sf, af):
    meas, read, interp = [], [], []
    if af:
        meas += ["loudness per section (LUFS)", "tone balance", "low end", "stereo width", "punch (peak vs average)"]
        if not sf:
            meas += ["tempo (beat tracking)", "key (pitch analysis - a best guess)", "how busy (attacks per second)"]
    if sf:
        read += ["tempo and meter", "key and chords", "parts and their roles", "sections", "how busy the notes are"]
        if not af:
            read += ["loud and quiet: from the notes (how many parts, how busy, how hard) - not measured sound"]
    interp += ["mood words (a common reading of key + tempo + tone)"]
    if af:
        interp += ["'felt as vibration' (depends on the speakers)"]
    return meas, read, interp


def _describe(sf, af, section, detail):
    view = sf["view"] if sf else None
    bpm, bsrc, key, ksrc, kconf, meter = _key_tempo(sf, af)
    tilt = af["whole"].get("tilt") if af else None
    L = []
    title = (view or {}).get("song") or (os.path.basename(af["path"]) if af else "the song")
    dur = af["duration_s"] if af else None
    if dur is None and sf and sf.get("model"):
        tf = _bar_time_fn(sf["model"])
        if tf and view.get("bars"):
            dur = tf(view["bars"] + 1)
    head = [f"{title}:"]
    if dur:
        head.append(("under a second" if dur < 1 else f"about {_fmt_dur(dur)}") + " long" +
                    (f" ({view['bars']} bars)" if view else "") + ",")
    if bpm:
        head.append(_tempo_words(float(bpm)) + ("" if bsrc == "project" else " (measured)") + ",")
    elif af and (af.get("tempo") or {}).get("unclear") and not af.get("silent"):
        head.append("no steady beat (no clear pulse to measure),")
    if not key and af and (af.get("key") or {}).get("unclear") and not af.get("silent"):
        head.append("no clear key.")
    elif key:
        head.append(f"in {key} ({kconf}{', from the notes' if ksrc == 'notes' else ', measured'})"
                    + (f", {meter} time" if meter and meter != "4/4" else "") + ".")
    else:
        head[-1] = head[-1].rstrip(",") + "."
    L.append(" ".join(head))
    if af and af.get("silent"):
        L.append(f"The audio file is silent (nothing louder than -70 LUFS) - check the render: fl_doctor finds muted "
                 f"or silent instruments.")
        tilt = None
    mood = _mood(key, bpm, tilt)
    if mood:
        L.append(f"Mood (an interpretation): {mood}.")
    if view and view.get("parts"):
        lim = 12 if detail == "full" else 6
        ps = view["parts"]
        L.append("Parts: " + "; ".join(_part_phrase(p) for p in ps[:lim]) +
                 (f"; and {len(ps) - lim} more" if len(ps) > lim else "") + ".")
    rs = _rhythm_sentence(sf, af)
    if rs:
        L.append(rs)
    rows = _energy_rows(sf, af, section)
    sh = _shape_sentence(_energy_rows(sf, af)) if not section else None
    if sh:
        L.append("Shape: " + sh)
    if af and not af.get("silent"):
        ss = _sound_sentence(af)
        if ss:
            L.append(ss)
    if view and view.get("melody") and detail == "full":
        mel = view["melody"]
        clim = re.sub(r"@(\d+):(\d+)", r" (bar \1, beat \2)", str(mel.get("climax") or ""))
        L.append(f"Tune: moves {int(100 * mel.get('steps', 0))}% in small steps and {int(100 * mel.get('leaps', 0))}% "
                 f"in leaps, range {mel.get('range')}, highest point {clim}.")
    max_s = data("describe").get("max_sections", 24)
    sec_lines = []
    for r in rows[:max_s if detail == "full" else 12]:
        line = _lab(r["name"]) + (f" ({r['where']})" if r.get("where") else "") + f": {r['level']}"
        if r.get("changes"):
            line += "; " + "; ".join(r["changes"])
        if detail == "full" and r.get("lufs") is not None:
            line += f" [{r['lufs']:g} LUFS]"
        sec_lines.append(line + ".")
    if len(rows) > len(sec_lines):
        sec_lines.append(f"(and {len(rows) - len(sec_lines)} more sections: detail='full' or section=...)")
    meas, read, interp = _basis(sf, af)
    how = "How this was made: " + "; ".join(
        x for x in (("measured: " + ", ".join(meas)) if meas else "", ("read from the notes: " + ", ".join(read)) if read
                    else "", "interpreted: " + ", ".join(interp)) if x) + "."
    text = "\n".join(L + (["Sections:"] + [" - " + s for s in sec_lines] if sec_lines else []) + [how])
    numbers = {"bpm": bpm, "key": key, "duration_s": round(dur, 1) if dur else None}
    if af:
        w = af["whole"]
        numbers.update({"lufs": w.get("lufs"), "true_peak_db": w.get("tp"), "lr_correlation": w.get("corr"),
                        "tilt_db_per_oct": w.get("tilt"), "low_20_200_db": (w.get("bands") or {}).get("low"),
                        "sub_20_60_db": w.get("sub_db"), "crest_db": w.get("crest")})
    sec_out = [{k: v for k, v in r.items() if k in ("name", "where", "level", "lufs", "energy", "changes") and v not in (None, [])}
               for r in rows] if detail == "full" or section else [{"name": r["name"], "level": r["level"],
                                                                       **({"lufs": r["lufs"]} if r.get("lufs") is not None else {}),
                                                                       **({"energy": r["energy"]} if r.get("energy") is not None else {})}
                                                                      for r in rows[:max_s]]
    return {"text": text, "sections": sec_out, "numbers": {k: v for k, v in numbers.items() if v is not None},
            "basis": {"measured": meas, "from_notes": read, "interpreted": interp},
            "next": (C.tool_call(H, "fl_visualize", {"kind": "energy", "...": 1}) +
                     " shows the loud and quiet parts as a picture; compare=<older version> says what changed")}


def _describe_change(compare, sf, af, section, detail):
    paths = compare if isinstance(compare, (list, tuple)) else [compare]
    bp = ba = None
    for p in paths:
        if not os.path.isfile(str(p)):
            raise ValueError(f"no such file: {p}")
        if _kind_of(p) == "audio":
            ba = p
        else:
            bp = p
    if sf and not bp and not ba:
        raise ValueError("compare: give the version before")
    sf0 = _score_facts(bp) if bp else None
    af0 = _audio_facts(ba, _spans_from_score(sf0) or _spans_from_score(sf), want_tempo_key=not (sf0 or sf)) if ba else None
    d = data("describe")
    L, numbers = [], {}
    b0, _, k0, _, _, m0 = _key_tempo(sf0, af0)
    b1, _, k1, _, _, m1 = _key_tempo(sf, af)
    if b0 and b1 and abs(float(b1) - float(b0)) >= 0.5:
        L.append(f"Tempo: {float(b0):g} -> {float(b1):g} BPM ({'faster' if b1 > b0 else 'slower'}).")
        numbers["bpm"] = [b0, b1]
    if k0 and k1 and k0 != k1:
        L.append(f"Key: {k0} -> {k1}.")
    if m0 and m1 and m0 != m1:
        L.append(f"Time signature: {m0} -> {m1}.")
    if af0 and af:
        w0, w1 = af0["whole"], af["whole"]
        if w0.get("lufs") is not None and w1.get("lufs") is not None:
            dl = round(w1["lufs"] - w0["lufs"], 1)
            numbers["lufs"] = [w0["lufs"], w1["lufs"]]
            if abs(dl) >= 0.5:
                L.append(f"Overall: {abs(dl):g} dB {'louder' if dl > 0 else 'quieter'} - {_pick(d['change_db_words'], abs(dl))}.")
        if w0.get("tilt") is not None and w1.get("tilt") is not None and abs(w1["tilt"] - w0["tilt"]) >= 0.7:
            dt = w1["tilt"] - w0["tilt"]
            L.append(f"Tone: {'brighter, more top end' if dt > 0 else 'darker, less top end'} "
                     f"({w0['tilt']:g} -> {w1['tilt']:g} dB/octave); now {_w('brightness_tilt', w1['tilt'])}.")
        bl0, bl1 = (w0.get("bands") or {}).get("low"), (w1.get("bands") or {}).get("low")
        if bl0 is not None and bl1 is not None and abs(bl1 - bl0) >= 1.0:
            L.append(f"Low end: {'more' if bl1 > bl0 else 'less'} bass weight ({bl1 - bl0:+.1f} dB of the whole), "
                     f"now {_w('low_end', bl1)}.")
        for band, word in (("presence", "upper-middle (2-5 kHz, clarity / harshness)"), ("air", "very top (5-16 kHz, sparkle)"),
                           ("lowmid", "low-middle (200-500 Hz, warmth / mud)")):
            v0, v1 = (w0.get("bands") or {}).get(band), (w1.get("bands") or {}).get(band)
            if v0 is not None and v1 is not None and abs(v1 - v0) >= 1.5:
                L.append(f"The {word}: {v1 - v0:+.1f} dB ({_pick(d['change_db_words'], abs(v1 - v0))}).")
        if w0.get("corr") is not None and w1.get("corr") is not None and abs(w1["corr"] - w0["corr"]) >= 0.05:
            L.append(f"Stereo: {'wider' if w1['corr'] < w0['corr'] else 'narrower'} (now {_w('width_corr', w1['corr'])}).")
        if w0.get("crest") is not None and w1.get("crest") is not None and abs(w1["crest"] - w0["crest"]) >= 1.5:
            L.append(f"Punch: {'more' if w1['crest'] > w0['crest'] else 'less'} contrast between hits and the rest "
                     f"({w0['crest']:g} -> {w1['crest']:g} dB).")
        r0 = {r["name"]: r for r in af0["rows"]}
        same = [(r0[r["name"]], r) for r in af["rows"] if r["name"] in r0]
        if not same and len(af0["rows"]) == len(af["rows"]):
            same = list(zip(af0["rows"], af["rows"]))
        rows_out = []
        for a, b in same:
            if a.get("lufs") is None or b.get("lufs") is None:
                continue
            dl = round(b["lufs"] - a["lufs"], 1)
            if abs(dl) >= 0.5:
                rows_out.append((b["name"], dl))
        if section:
            keep = {r["name"] for r in _section_filter(af["rows"], section)}
            rows_out = [x for x in rows_out if x[0] in keep]
        if rows_out:
            L.append("By section: " + "; ".join(f"{n} {abs(v):g} dB {'louder' if v > 0 else 'quieter'} "
                                                 f"({_pick(d['change_db_words'], abs(v))})" for n, v in rows_out[:12]) + ".")
    if sf0 and sf:
        v0, v1 = sf0["view"], sf["view"]
        n0 = {p.get("name") or f"ch{p['ch']}" for p in v0.get("parts") or []}
        n1 = {p.get("name") or f"ch{p['ch']}" for p in v1.get("parts") or []}
        if n1 - n0:
            L.append("New parts: " + ", ".join(sorted(n1 - n0)) + ".")
        if n0 - n1:
            L.append("Parts gone: " + ", ".join(sorted(n0 - n1)) + ".")
        s0 = {s["name"]: s for s in v0.get("sections") or []}
        ch = []
        for s in v1.get("sections") or []:
            o = s0.get(s["name"])
            if not o:
                ch.append(f"{s['name']} is new"); continue
            bits = []                                        # absolute facts only (energy E0-9 is relative to each song)
            nm0 = {p.get("name") or f"ch{p['ch']}" for p in v0.get("parts") or [] if p["ch"] in (o.get("parts") or [])}
            nm1 = {p.get("name") or f"ch{p['ch']}" for p in v1.get("parts") or [] if p["ch"] in (s.get("parts") or [])}
            if nm1 - nm0:
                bits.append(", ".join(sorted(nm1 - nm0)) + " added")
            if nm0 - nm1:
                bits.append(", ".join(sorted(nm0 - nm1)) + " gone")
            if abs((s.get("notes_per_bar") or 0) - (o.get("notes_per_bar") or 0)) >= 1:
                bits.append(f"{'busier' if s['notes_per_bar'] > o['notes_per_bar'] else 'sparser'} "
                            f"({o['notes_per_bar']:g} -> {s['notes_per_bar']:g} notes a bar)")
            if abs((s.get("vel") or 0) - (o.get("vel") or 0)) >= 6:
                bits.append(f"played {'harder' if s['vel'] > o['vel'] else 'softer'} (velocity {o['vel']} -> {s['vel']})")
            if (v1.get("progression") or {}).get(s["name"]) != (v0.get("progression") or {}).get(s["name"]):
                bits.append("chords changed")
            if bits:
                ch.append(f"{s['name']}: " + ", ".join(bits))
        for nm in s0:
            if nm not in {s["name"] for s in v1.get("sections") or []}:
                ch.append(f"{nm} was removed")
        if section:
            low = str(section).lower()
            ch = [c for c in ch if c.lower().startswith(low)] or ch
        if ch:
            L.append("Notes: " + "; ".join(ch[:12]) + ".")
    if not L:
        L.append("No measurable change: " + ("loudness within 0.5 dB, same tone and width" if af0 and af else "") +
                 ("; " if af0 and af and sf0 and sf else "") + ("same parts, sections and chords" if sf0 and sf else "") + ".")
    meas, read, _ = _basis(sf or sf0, af or af0)
    L.append("Compared: " + ", ".join(x for x in (f"renders {os.path.basename(ba)} -> {os.path.basename(af['path'])}"
                                                  if af0 and af else "",
                                                  "the notes of both versions" if sf0 and sf else "") if x) +
             ". Changes under about 1 dB are hard for anyone to hear.")
    if (af0 is None) != (af is None) or (sf0 is None) != (sf is None):
        L.append("Note: the two versions weren't given the same way (render vs project), so only what both have is compared.")
    return {"text": "\n".join(L), "changed": not L[0].startswith("No measurable"),
            "numbers": numbers, "basis": {"measured": meas, "from_notes": read}}


def fl_describe(project=None, audio=None, section=None, detail=None, compare=None, language=None):
    return describe_music(project, audio, section, detail, compare, language)


# ============================================================================= pictures: font, canvas, palette
# 5x7 bitmap font (rows as 5-bit numbers, hex pairs top to bottom) - big readable labels without an imaging library
_FONT_SRC = {
    "A": "0E11111F111111", "B": "1E11111E11111E", "C": "0E11101010110E", "D": "1C12111111121C",
    "E": "1F10101E10101F", "F": "1F10101E101010", "G": "0E11101711110F", "H": "1111111F111111",
    "I": "0E04040404040E", "J": "0702020202120C", "K": "11121418141211", "L": "1010101010101F",
    "M": "111B1515111111", "N": "11111915131111", "O": "0E11111111110E", "P": "1E11111E101010",
    "Q": "0E11111115120D", "R": "1E11111E141211", "S": "0F10100E01011E", "T": "1F040404040404",
    "U": "1111111111110E", "V": "1111111111 0A04".replace(" ", ""), "W": "1111111515150A",
    "X": "11110A040A1111", "Y": "1111110A040404", "Z": "1F01020408101F",
    "0": "0E111315191 10E".replace(" ", ""), "1": "040C040404040E", "2": "0E11010204081F", "3": "1F02040201110E",
    "4": "02060A121F0202", "5": "1F101E0101110E", "6": "0608101E11110E", "7": "1F010204080808",
    "8": "0E11110E11110E", "9": "0E11110F01020C", " ": "00000000000000", ".": "00000000000C0C",
    ",": "000000000C0408", ":": "000C0C000C0C00", "-": "0000001F000000", "+": "0004041F040400",
    "/": "00010204081000", "%": "18190204081303", "(": "02040808080402", ")": "08040202020408",
    "#": "0A0A1F0A1F0A0A", "'": "0C040800000000", "!": "04040404000004", "?": "0E110102040004",
    "_": "0000000000001F", "=": "00001F001F0000", "<": "02040810080402", ">": "08040201020408",
    "b": "10101619111 11E".replace(" ", ""), "~": "000008150200 00".replace(" ", ""),
}
_FONT = {}


def _glyph(ch):
    import numpy as np
    if ch not in _FONT_SRC:
        ch = ch.upper() if ch.upper() in _FONT_SRC else "?"
    g = _FONT.get(ch)
    if g is None:
        src = _FONT_SRC[ch]
        rows = [int(src[i:i + 2], 16) for i in range(0, 14, 2)]
        g = _FONT[ch] = np.array([[(r >> (4 - c)) & 1 for c in range(5)] for r in rows], dtype=bool)
    return g


# Okabe-Ito (colorblind-safe) on a near-black or white background; text is pure white / black for contrast
THEMES = {
    "dark": {"bg": (12, 12, 12), "fg": (255, 255, 255), "grid": (85, 85, 85), "dim": (170, 170, 170),
             "series": [(86, 180, 233), (230, 159, 0), (0, 158, 115), (240, 228, 66), (204, 121, 167),
                        (213, 94, 0), (0, 114, 178)], "mark": (240, 228, 66)},
    "light": {"bg": (255, 255, 255), "fg": (0, 0, 0), "grid": (190, 190, 190), "dim": (80, 80, 80),
              "series": [(0, 114, 178), (213, 94, 0), (0, 158, 115), (204, 121, 167), (86, 180, 233),
                         (230, 159, 0), (0, 0, 0)], "mark": (0, 0, 0)},
}
CIVIDIS = [(0.0, (0, 34, 78)), (0.25, (60, 77, 110)), (0.5, (124, 123, 120)), (0.75, (188, 175, 111)),
           (1.0, (255, 234, 70))]                              # perceptually even, readable with color blindness


class Canvas:
    def __init__(self, w, h, theme="dark", scale=2):
        import numpy as np
        self.w, self.h = int(w), int(h)
        self.t = THEMES.get(theme, THEMES["dark"])
        self.a = np.empty((self.h, self.w, 3), dtype=np.uint8); self.a[:] = self.t["bg"]
        self.s = int(scale)

    def rect(self, x0, y0, x1, y1, c):
        x0, x1 = sorted((int(round(x0)), int(round(x1)))); y0, y1 = sorted((int(round(y0)), int(round(y1))))
        x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(self.w, x1), min(self.h, y1)
        if x1 > x0 and y1 > y0:
            self.a[y0:y1, x0:x1] = c

    def frame(self, x0, y0, x1, y1, c, t=2):
        self.rect(x0, y0, x1, y0 + t, c); self.rect(x0, y1 - t, x1, y1, c)
        self.rect(x0, y0, x0 + t, y1, c); self.rect(x1 - t, y0, x1, y1, c)

    def line(self, x0, y0, x1, y1, c, t=2, dash=0):
        import numpy as np
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        xs = np.linspace(x0, x1, n); ys = np.linspace(y0, y1, n)
        if dash:
            keep = (np.arange(n) // dash) % 2 == 0; xs, ys = xs[keep], ys[keep]
        r = t // 2
        for dx in range(-r, t - r):
            for dy in range(-r, t - r):
                xi = np.clip(np.round(xs + dx).astype(int), 0, self.w - 1)
                yi = np.clip(np.round(ys + dy).astype(int), 0, self.h - 1)
                self.a[yi, xi] = c

    def polyline(self, pts, c, t=3):
        for (a, b), (x, y) in zip(pts, pts[1:]):
            self.line(a, b, x, y, c, t)

    def text_w(self, s, scale=None):
        sc = scale or self.s
        return max(0, len(s) * 6 * sc - sc)

    def text(self, x, y, s, c=None, scale=None, anchor="l", max_w=None):
        """Draw text (5x7 glyphs scaled). anchor l | c | r. max_w: cut with '.' to fit."""
        import numpy as np
        sc = scale or self.s; c = self.t["fg"] if c is None else c
        s = str(s)
        if max_w is not None:
            while s and self.text_w(s, sc) > max_w:
                s = s[:-2] + "." if len(s) > 2 else s[:-1]
        tw = self.text_w(s, sc)
        x = x - tw // 2 if anchor == "c" else x - tw if anchor == "r" else x
        for i, ch in enumerate(s):
            g = np.kron(_glyph(ch), np.ones((sc, sc), dtype=bool))
            gx, gy = int(x + i * 6 * sc), int(y)
            h, w = g.shape
            ys, xs = slice(max(0, gy), min(self.h, gy + h)), slice(max(0, gx), min(self.w, gx + w))
            sub = g[ys.start - gy:ys.stop - gy, xs.start - gx:xs.stop - gx]
            if sub.size:
                self.a[ys, xs][sub] = c
        return tw

    def png(self):
        return H["_png_encode"](self.w, self.h, self.a.tobytes())


def _cmap(v):
    """0..1 array -> (..., 3) uint8 with CIVIDIS."""
    import numpy as np
    v = np.clip(v, 0, 1)
    xs = [p for p, _ in CIVIDIS]; out = np.empty(v.shape + (3,), dtype=np.uint8)
    for c in range(3):
        out[..., c] = np.interp(v, xs, [col[c] for _, col in CIVIDIS]).astype(np.uint8)
    return out


class _Plot:
    """A chart area on a canvas: title, axes in data units, ticks with labels."""

    def __init__(self, cv, title, x0, x1, y0, y1, left=None, bottom=None):
        self.cv = cv; s = cv.s
        self.L = left if left is not None else 18 * s + 8 * 6 * s // 2
        self.T = 7 * (s + 1) + 22; self.R = 16; self.B = bottom if bottom is not None else 7 * s + 30
        self.x0, self.x1, self.y0, self.y1 = float(x0), float(x1) if x1 > x0 else x0 + 1, float(y0), float(y1) if y1 > y0 else y0 + 1
        cv.text(12, 10, title, scale=s + 1, max_w=cv.w - 24)

    @property
    def area(self):
        return self.L, self.T, self.cv.w - self.R, self.cv.h - self.B

    def X(self, v):
        a, _, b, _ = self.area
        return a + (v - self.x0) / (self.x1 - self.x0) * (b - a)

    def Y(self, v):
        _, a, _, b = self.area
        return b - (v - self.y0) / (self.y1 - self.y0) * (b - a)

    def axes(self, xticks=(), yticks=(), xlab=None, ylab=None):
        cv = self.cv; t = cv.t; s = cv.s; a, b, c, d = self.area
        for v, lab in yticks:
            y = self.Y(v)
            if b - 1 <= y <= d + 1:
                cv.line(a, y, c, y, t["grid"], 1)
                cv.text(a - 8, y - 7 * s // 2, lab, scale=s, anchor="r")
        last = -1e9
        for v, lab in xticks:
            x = self.X(v)
            if a - 1 <= x <= c + 1 and x - last >= cv.text_w(lab) + 12:
                cv.line(x, d, x, d + 6, t["fg"], 2)
                cv.text(x, d + 10, lab, scale=s, anchor="c")
                last = x
        cv.line(a, d, c, d, t["fg"], 2); cv.line(a, b, a, d, t["fg"], 2)
        if xlab:
            cv.text(c, cv.h - 7 * s - 4, xlab, c=t["dim"], scale=s, anchor="r")
        if ylab:
            cv.text(4, cv.h - 7 * s - 4, ylab, c=t["dim"], scale=s)


def _nice_step(span, target=6):
    raw = span / max(1, target)
    mag = 10 ** math.floor(math.log10(max(raw, 1e-9)))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _time_ticks(t0, t1):
    st = _nice_step(t1 - t0, 7)
    st = max(1, int(round(st))) if st >= 1 else st
    v = math.ceil(t0 / st) * st; out = []
    while v <= t1 + 1e-9:
        out.append((v, _fmt_t(v))); v += st
    return out


def _sections_overlay(p, secs, key_a="a", key_b="b"):
    """Vertical lines at section starts with their names along the top of the plot area."""
    cv = p.cv; t = cv.t; s = cv.s; a, b, c, d = p.area
    for i, sc in enumerate(secs):
        x = p.X(sc[key_a]); x2 = p.X(sc[key_b])
        if i:
            cv.line(x, b, x, d, t["dim"], 1, dash=6)
        cv.text(x + 4, b + 4 + (i % 2) * (7 * s + 4), sc["name"], c=t["fg"], scale=s, max_w=max(0, x2 - x - 6))


# ============================================================================= fl_visualize
KINDS = ("energy", "loudness", "spectrogram", "arrangement", "piano_roll", "mix_balance")


def _vis_size(prefs, width, height):
    low = prefs.get("vision") == "low"
    w = int(width or (1400 if low else 1000)); h = int(height or (700 if low else 500))
    w, h = max(320, min(2400, w)), max(200, min(1600, h))
    return w, h, (3 if low else 2) + (1 if w >= 1800 else 0), (5 if low else 3)


def _time_range(af, section):
    if not section:
        return 0.0, af["duration_s"], None
    rows = _section_filter(af["rows"], section)
    return rows[0]["start_s"], rows[-1]["end_s"], ", ".join(r["name"] for r in rows)


def _bar_range(m, view, section):
    secs = view.get("sections") or []
    if not section:
        return 1.0, float(view["bars"] + 1), None
    rows = _section_filter([dict(s) for s in secs], section)
    return rows[0]["bar"], rows[-1]["end_bar"], ", ".join(r["name"] for r in rows)


def _vis_energy(cv, sf, af, section, lw):
    t = cv.t; ser = t["series"]
    if af:
        t0, t1, lab = _time_range(af, section)
        rows = [r for r in af["rows"] if r["end_s"] > t0 and r["start_s"] < t1 and r.get("lufs") is not None]
        st = af["st"]; xs = [i / 10 for i in range(len(st))]
        vis = [v for i, v in enumerate(st) if t0 <= xs[i] <= t1 and v > -70]
        lo = math.floor((min([r["lufs"] for r in rows] + vis + [-30]) - 3) / 3) * 3
        hi = math.ceil((max([r["lufs"] for r in rows] + vis + [-6]) + 5) / 3) * 3     # room for the labels on top
        p = _Plot(cv, "LOUDNESS BY SECTION (LUFS)" + (f" - {lab.upper()}" if lab else ""), t0, t1, lo, hi)
        a, b, c, d = p.area
        top = max(rows, key=lambda r: r["lufs"]) if rows else None
        for i, r in enumerate(rows):
            x0, x1 = p.X(max(t0, r["start_s"])) + 2, p.X(min(t1, r["end_s"])) - 2
            y = p.Y(r["lufs"])
            cv.rect(x0, y, x1, d, ser[i % 2])
            if r is top:
                cv.frame(x0 - 3, y - 3, x1 + 3, d, t["mark"], lw)
            cv.text((x0 + x1) / 2, y - 7 * cv.s - 6, f"{r['lufs']:.0f}", scale=cv.s, anchor="c", max_w=x1 - x0)
        pts = [(p.X(xs[i]), p.Y(max(lo, v))) for i, v in enumerate(st) if t0 <= xs[i] <= t1][::3]
        if len(pts) > 1:
            cv.polyline(pts, t["fg"], lw - 1)
        step = 6 if hi - lo > 18 else 3
        p.axes(_time_ticks(t0, t1), [(v, f"{v:.0f}") for v in range(int(lo), int(hi) + 1, step)], "TIME (M:SS)", None)
        _sections_overlay(p, [{"name": r["name"], "a": max(t0, r["start_s"]), "b": min(t1, r["end_s"])} for r in rows
                              if not r.get("auto")])
        q = sorted([r for r in rows if r["lufs"] > -70], key=lambda r: r["lufs"])
        alt = (f"Bar chart of loudness per section over time{' (' + lab + ')' if lab else ''}: each bar spans the "
               f"section's time, its height is the section's loudness in LUFS (higher = louder); a white line traces "
               f"the 3-second loudness. ")
        if q:
            alt += (f"Loudest: {q[-1]['name']} at {q[-1]['lufs']:g} LUFS (outlined). Quietest: {q[0]['name']} at "
                    f"{q[0]['lufs']:g} LUFS. In order: " +
                    ", ".join(f"{r['name']} {r['lufs']:g}" for r in rows[:16]) + ".")
        else:
            alt += "The audio is silent here." if af.get("silent") else "The audio is too short to split into sections."
        return alt, {"sections": [{"name": r["name"], "lufs": r["lufs"]} for r in rows]}
    view = sf["view"]
    a_, b_, lab = _bar_range(sf["model"], view, section)
    secs = [s for s in view["sections"] if s["end_bar"] > a_ and s["bar"] < b_]
    p = _Plot(cv, "ENERGY BY SECTION (FROM THE NOTES, 0-9)" + (f" - {lab.upper()}" if lab else ""), a_, b_, 0, 11.5)
    a, b, c, d = p.area
    top = max(secs, key=lambda s: s["energy"]) if secs else None
    for i, s in enumerate(secs):
        x0, x1 = p.X(max(a_, s["bar"])) + 2, p.X(min(b_, s["end_bar"])) - 2
        y = p.Y(s["energy"])
        cv.rect(x0, y, x1, d, ser[i % 2])
        if s is top:
            cv.frame(x0 - 3, y - 3, x1 + 3, d, t["mark"], lw)
        cv.text((x0 + x1) / 2, y - 7 * cv.s - 6, str(s["energy"]), scale=cv.s, anchor="c")
    st = max(1, int(_nice_step(b_ - a_, 8)))
    p.axes([(v, str(int(v))) for v in range(int(a_), int(b_) + 1, st)], [(v, str(v)) for v in range(0, 10, 3)], "BAR", None)
    _sections_overlay(p, [{"name": s["name"], "a": max(a_, s["bar"]), "b": min(b_, s["end_bar"])} for s in secs])
    alt = ("Bar chart of energy per section, read from the notes (0 = silent, 9 = the biggest; it counts parts, how "
           "busy and how hard they play - not measured sound). x = bars. ")
    if secs:
        alt += (f"Biggest: {top['name']} ({top['energy']}). In order: " +
                ", ".join(f"{s['name']} {s['energy']}" for s in secs[:16]) + ".")
    return alt, {"sections": [{"name": s["name"], "energy": s["energy"]} for s in secs]}


def _vis_loudness(cv, af, section, lw):
    t = cv.t
    t0, t1, lab = _time_range(af, section)
    st = af["st"]; xs = [i / 10 for i in range(len(st))]
    vals = [(x, v) for x, v in zip(xs, st) if t0 <= x <= t1]
    real = [v for _, v in vals if v > -70]
    lo = math.floor((min(real + [-30]) - 2) / 6) * 6; hi = max(0, math.ceil((max(real + [-6]) + 2) / 6) * 6)
    p = _Plot(cv, "LOUDNESS OVER TIME (SHORT-TERM LUFS)" + (f" - {lab.upper()}" if lab else ""), t0, t1, lo, hi)
    a, b, c, d = p.area
    ref = -14.0
    if lo < ref < hi:
        cv.line(a, p.Y(ref), c, p.Y(ref), t["series"][1], 2, dash=10)
        cv.text(c - 4, p.Y(ref) + 6, "-14 STREAMING", c=t["series"][1], scale=cv.s, anchor="r")
    pts = [(p.X(x), p.Y(max(lo, v))) for x, v in vals][::2]
    if len(pts) > 1:
        cv.polyline(pts, t["series"][0], lw)
    p.axes(_time_ticks(t0, t1), [(v, f"{v}") for v in range(int(lo), int(hi) + 1, 6)], "TIME (M:SS)", None)
    rows = [r for r in af["rows"] if r["end_s"] > t0 and r["start_s"] < t1 and not r.get("auto")]
    _sections_overlay(p, [{"name": r["name"], "a": max(t0, r["start_s"]), "b": min(t1, r["end_s"])} for r in rows])
    if real:
        imax = max(range(len(vals)), key=lambda i: vals[i][1]); imin = min((i for i in range(len(vals)) if vals[i][1] > -70),
                                                                          key=lambda i: vals[i][1])
        alt = (f"Line chart of loudness over time{' (' + lab + ')' if lab else ''} (3-second window, LUFS; higher = "
               f"louder; dashed line = -14, a common streaming level). Loudest moment {vals[imax][1]:.0f} LUFS at "
               f"{_fmt_t(vals[imax][0])}; quietest {vals[imin][1]:.0f} LUFS at {_fmt_t(vals[imin][0])}; whole song "
               f"{af['whole'].get('lufs')} LUFS, true peak {af['whole'].get('tp')} dBTP.")
    else:
        alt = "Line chart of loudness over time: the audio is silent here."
    pk = [(round(x, 1), round(float(v), 1)) for x, v in vals[::50]]
    return alt, {"every_5s": pk[:80]}


def _vis_spectrogram(cv, af, section):
    import numpy as np
    t = cv.t
    t0, t1, lab = _time_range(af, section)
    x = af["x"]; fs = af["fs"]
    y = x[int(t0 * fs):int(t1 * fs)].mean(1)
    p = _Plot(cv, "SPECTROGRAM (PITCH RANGE OVER TIME)" + (f" - {lab.upper()}" if lab else ""), t0, t1,
              math.log10(30), math.log10(16000))
    a, b, c, d = p.area
    W, Hh = int(c - a), int(d - b)
    nfft = 4096
    n = len(y)
    cols = max(1, min(W, n // 512))
    starts = np.linspace(0, max(0, n - nfft), cols).astype(int)
    win = np.hanning(nfft)
    seg = np.stack([np.pad(y[s:s + nfft], (0, max(0, nfft - len(y[s:s + nfft])))) for s in starts])
    M = np.abs(np.fft.rfft(seg * win, axis=1)) ** 2
    f = np.fft.rfftfreq(nfft, 1 / fs)
    fy = 10 ** np.linspace(math.log10(16000), math.log10(30), Hh)          # top row = high
    idx = np.clip(np.searchsorted(f, fy), 1, len(f) - 1)
    Dm = 10 * np.log10(M[:, idx] + 1e-14).T                               # (rows, cols)
    top = np.percentile(Dm, 99.5); Dn = (Dm - (top - 70)) / 70.0
    img = _cmap(Dn)
    colx = np.clip(((np.arange(W) / max(1, W - 1)) * (cols - 1)).round().astype(int), 0, cols - 1)
    cv.a[int(b):int(b) + Hh, int(a):int(a) + W] = img[:, colx]
    yt = [(math.log10(v), lab_) for v, lab_ in ((50, "50"), (100, "100"), (200, "200"), (500, "500"), (1000, "1K"),
                                               (2000, "2K"), (5000, "5K"), (10000, "10K"))]
    p.axes(_time_ticks(t0, t1), yt, "TIME (M:SS)", "HZ")
    rows = [r for r in af["rows"] if r["end_s"] > t0 and r["start_s"] < t1 and not r.get("auto")]
    _sections_overlay(p, [{"name": r["name"], "a": max(t0, r["start_s"]), "b": min(t1, r["end_s"])} for r in rows])
    band = af["whole"].get("bands") or {}
    strongest = max(band.items(), key=lambda kv: kv[1])[0] if band else None
    words = {"low": "the bass (20-200 Hz)", "lowmid": "the low-middle (200-500 Hz)", "mid": "the middle (500 Hz-2 kHz)",
             "presence": "the upper-middle (2-5 kHz)", "air": "the top (5-16 kHz)"}
    alt = ("Spectrogram: time left to right, pitch range bottom (low, 30 Hz) to top (high, 16 kHz); brighter yellow = "
           "more energy, dark blue = little. ")
    if band:
        alt += (f"Most energy sits in {words.get(strongest, strongest)}; share of the whole by range: " +
                ", ".join(f"{words[k].split(' (')[0].replace('the ', '')} {v:g} dB" for k, v in band.items()) + ".")
    return alt, {"bands_db": band}


def _vis_arrangement(cv, sf, section, lw):
    import numpy as np
    t = cv.t; view, m = sf["view"], sf["model"]
    a_, b_, lab = _bar_range(m, view, section)
    parts = view.get("parts") or []
    names = [(p.get("name") or p.get("instrument") or f"ch{p['ch']}") for p in parts]
    left = min(cv.w // 3, max(cv.text_w(n[:14]) for n in names) + 24) if names else 60
    p = _Plot(cv, "ARRANGEMENT (WHO PLAYS WHEN)" + (f" - {lab.upper()}" if lab else ""), a_, b_, 0, max(1, len(parts)),
              left=left)
    A, B, C, D = p.area
    bar_t = m["bar_t"]; P = {}
    for n in m["notes"]:
        P.setdefault(n[2], {}).setdefault(int(n[0] // bar_t) + 1, []).append(n[4])
    rowh = (D - B) / max(1, len(parts))
    for i, (pt, nm) in enumerate(zip(parts, names)):
        y0 = B + i * rowh + 3; y1 = B + (i + 1) * rowh - 3
        col = t["series"][i % len(t["series"])]
        cv.text(A - 10, (y0 + y1) / 2 - 7 * cv.s / 2, nm[:14], scale=cv.s, anchor="r", max_w=left - 16)
        for bar, vels in (P.get(pt["ch"]) or {}).items():
            if a_ <= bar < b_:
                k = 0.45 + 0.55 * min(1.0, (sum(vels) / len(vels)) / 110.0)
                cc = tuple(int(v * k + t["bg"][j] * (1 - k)) for j, v in enumerate(col))
                cv.rect(p.X(bar) + 1, y0, p.X(bar + 1) - 1, y1, cc)
        cv.line(A, y1 + 3, C, y1 + 3, t["grid"], 1)
    st = max(1, int(_nice_step(b_ - a_, 8)))
    p.axes([(v, str(int(v))) for v in range(int(a_), int(b_) + 1, st)], [], "BAR", None)
    secs = [s for s in view["sections"] if s["end_bar"] > a_ and s["bar"] < b_]
    for i, s in enumerate(secs):
        if i:
            cv.line(p.X(s["bar"]), B, p.X(s["bar"]), D, t["fg"], lw - 1)
    _sections_overlay(p, [{"name": s["name"], "a": max(a_, s["bar"]), "b": min(b_, s["end_bar"])} for s in secs])
    act = []
    for pt, nm in zip(parts, names):
        act.append(f"{nm}: {pt.get('bars')}")
    alt = ("Arrangement grid: one row per part (names on the left), bars left to right; a filled block = the part plays "
           "in that bar (brighter = played harder); white lines = section starts" +
           (f" ({', '.join(s['name'] + ' bar ' + format(s['bar'], 'g') for s in secs[:12])})" if secs else "") +
           ". Who plays where: " + "; ".join(act[:12]) + ".")
    return alt, {"parts": [{"name": nm, "bars": pt.get("bars")} for pt, nm in zip(parts, names)]}


def _vis_piano_roll(cv, sf, section, part, lw):
    t = cv.t; view, m = sf["view"], sf["model"]
    a_, b_, lab = _bar_range(m, view, section)
    if not section:
        b_ = min(b_, a_ + 8)
    parts = {p["ch"]: p for p in view.get("parts") or []}
    if part is not None:
        sel = [c for c, p in parts.items() if str(part) == str(c) or str(part).lower() in str(p.get("name") or "").lower()
               or str(part).lower() in str(p.get("instrument") or "").lower()]
        if not sel:
            raise ValueError(f"no part '{part}' (parts: {[p.get('name') or c for c, p in parts.items()]})")
    else:
        sel = [c for c, p in parts.items() if p.get("role") != "drums"][:6]
    bar_t = m["bar_t"]
    notes = [n for n in m["notes"] if n[2] in sel and a_ <= n[0] / bar_t + 1 < b_]
    if not notes:
        raise ValueError("no notes there")
    lo = min(n[3] for n in notes) - 1; hi = max(n[3] for n in notes) + 2
    p = _Plot(cv, "NOTES (PIANO ROLL)" + (f" - {lab.upper()}" if lab else f" - BARS {a_:g}-{b_ - 1:g}"), a_, b_, lo, hi)
    A, B, C, D = p.area
    order = {c: i for i, c in enumerate(sel)}
    for n in notes:
        x0 = p.X(n[0] / bar_t + 1); x1 = max(x0 + 3, p.X((n[0] + n[1]) / bar_t + 1) - 1)
        col = t["series"][order[n[2]] % len(t["series"])]
        cv.rect(x0, p.Y(n[3] + 1) + 1, min(x1, C), p.Y(n[3]) - 1, col)
    st = max(1, int(_nice_step(b_ - a_, 8)))
    p.axes([(v, str(int(v))) for v in range(int(a_), int(b_) + 1, st)],
           [(k + 0.5, _note_name(k)) for k in range(lo, hi) if k % 12 == 0], "BAR", None)
    labels = [((parts[c].get("name") or f"ch{c}")[:12], t["series"][order[c] % len(t["series"])]) for c in sel]
    lx = cv.w - 12 - sum(7 * cv.s + 6 + cv.text_w(nm) + 18 for nm, _ in labels) + 18     # legend: top right
    ly = cv.h - 7 * cv.s - 4 if lx < cv.text_w("NOTES (PIANO ROLL) - BARS 00-00", cv.s + 1) + 30 else 14
    lx = max(cv.text_w("BAR") + 30, lx) if ly != 14 else lx
    for nm, col in labels:
        cv.rect(lx, ly, lx + 7 * cv.s, ly + 7 * cv.s, col)
        lx += 7 * cv.s + 6 + cv.text(lx + 7 * cv.s + 6, ly, nm, scale=cv.s) + 18
    alt = (f"Piano roll: time left to right (bars {a_:g}-{b_ - 1:g}), pitch bottom (low) to top (high); each block is "
           f"a note, its length how long it lasts; grid lines mark each C. Parts: " +
           ", ".join(f"{parts[c].get('name') or 'ch' + str(c)} ({_note_name(min(n[3] for n in notes if n[2] == c))}-"
                     f"{_note_name(max(n[3] for n in notes if n[2] == c))}, "
                     f"{sum(1 for n in notes if n[2] == c)} notes)" for c in sel if any(n[2] == c for n in notes)) + ".")
    return alt, {"notes": len(notes), "range": [_note_name(lo + 1), _note_name(hi - 2)]}


BANDS6 = [("sub", 20, 60), ("bass", 60, 250), ("low-mid", 250, 800), ("mid", 800, 2500), ("presence", 2500, 6000),
          ("air", 6000, 16000)]


def _band_balance(x, fs, t0=None, t1=None):
    """Per band: level (dB re total) and width (1 - L/R correlation of that band), from STFT frames."""
    import numpy as np
    if t0 is not None:
        x = x[int(t0 * fs):int(t1 * fs)]
    nfft = 4096; n = len(x)
    if n < nfft:
        x = np.pad(x, ((0, nfft - n), (0, 0))); n = nfft
    k = min(120, max(1, n // nfft))
    starts = np.linspace(0, n - nfft, k).astype(int); w = np.hanning(nfft)
    Lf = np.fft.rfft(np.stack([x[s:s + nfft, 0] for s in starts]) * w, axis=1)
    Rf = np.fft.rfft(np.stack([x[s:s + nfft, 1] for s in starts]) * w, axis=1)
    f = np.fft.rfftfreq(nfft, 1 / fs)
    P = (np.abs(Lf) ** 2 + np.abs(Rf) ** 2).sum(0); tot = P[(f >= 20) & (f < 16000)].sum() + 1e-20
    out = []
    for nm, a, b in BANDS6:
        sel = (f >= a) & (f < b)
        lv = 10 * np.log10(P[sel].sum() / tot + 1e-12)
        num = np.real(Lf[:, sel] * np.conj(Rf[:, sel])).sum()
        den = math.sqrt(float((np.abs(Lf[:, sel]) ** 2).sum() * (np.abs(Rf[:, sel]) ** 2).sum())) + 1e-20
        out.append({"band": nm, "hz": [a, b], "level_db": round(float(lv), 1), "corr": round(float(num / den), 2)})
    return out


def _vis_mix_balance(cv, af, section, lw):
    t = cv.t
    t0, t1, lab = _time_range(af, section)
    bands = _band_balance(af["x"], af["fs"], t0, t1)
    s = cv.s
    cv.text(12, 10, "MIX BALANCE BY PITCH RANGE" + (f" - {lab.upper()}" if lab else ""), scale=s + 1, max_w=cv.w - 24)
    top = 7 * (s + 1) + 30; rowh = (cv.h - top - 40) / len(bands)
    labw = max(cv.text_w(b["band"].upper()) for b in bands) + 24
    mid = labw + (cv.w - labw) * 0.55
    cv.text(labw, top - 4 - 7 * s + 4 + 0, "LEVEL (DB OF THE WHOLE)", c=t["dim"], scale=s, max_w=mid - labw - 10)
    cv.text(mid + 20, top - 4 - 7 * s + 4, "WIDTH", c=t["dim"], scale=s)
    lo = min(-30.0, min(b["level_db"] for b in bands) - 3)
    for i, b in enumerate(bands):
        y0 = top + 7 * s + i * rowh + 4; y1 = y0 + rowh - 10
        cv.text(labw - 12, (y0 + y1) / 2 - 7 * s / 2, b["band"].upper(), scale=s, anchor="r")
        x1 = labw + (mid - labw - 70) * max(0.0, (b["level_db"] - lo) / (0 - lo))
        cv.rect(labw, y0, x1, y1, t["series"][0])
        cv.text(x1 + 8, (y0 + y1) / 2 - 7 * s / 2, f"{b['level_db']:.0f}", scale=s)
        wd = max(0.0, min(1.0, 1 - b["corr"]))
        x2 = mid + 20 + (cv.w - mid - 110) * wd
        cv.frame(mid + 20, y0, cv.w - 90, y1, t["grid"], 2)
        cv.rect(mid + 20, y0, x2, y1, t["series"][1] if b["corr"] >= 0 else t["series"][5])
        cv.text(cv.w - 82, (y0 + y1) / 2 - 7 * s / 2, f"{b['corr']:.2f}", scale=s)
    alt = ("Two bar charts, one row per pitch range from SUB (20-60 Hz) to AIR (6-16 kHz). Left: how much of the "
           "whole sound is in that range (dB; closer to 0 = more). Right: stereo width of that range (empty = "
           "centered, full = very wide; the number is the left/right correlation, 1 = mono, below 0 = parts cancel). " +
           "; ".join(f"{b['band']} {b['level_db']:g} dB, correlation {b['corr']:g}" for b in bands) + ".")
    loud = max(bands, key=lambda b: b["level_db"])
    alt += f" Most energy: {loud['band']}."
    if bands[0]["corr"] < 0.9 or bands[1]["corr"] < 0.8:
        alt += " The low end is not centered (bass usually sits in the middle)."
    return alt, {"bands": bands}


def visualize(kind="energy", project=None, audio=None, section=None, part=None, theme="dark", width=None, height=None,
              save=None, images=None):
    """A picture plus alt text. kind: energy | loudness | spectrogram | mix_balance (need audio, energy also works
    from the notes) | arrangement | piano_roll (need a project). images: auto | always | never."""
    t0 = time.time()
    if kind not in KINDS:
        raise ValueError(f"kind: one of {', '.join(KINDS)}")
    if not project and not audio:
        try:
            project, _ = H["_open_project_guess"]()
        except Exception:
            project = None
    needs_audio = kind in ("loudness", "spectrogram", "mix_balance")
    needs_proj = kind in ("arrangement", "piano_roll")
    if needs_audio and not audio:
        raise ValueError(f"{kind} needs audio= (a WAV / FLAC render; {C.tool_name(H, 'fl_render')} makes one)")
    if needs_proj and not project:
        raise ValueError(f"{kind} needs project= (.flp / .mid)")
    if kind == "energy" and not (audio or project):
        raise ValueError("energy needs audio= or project=")
    prefs = access_prefs()
    sf = _score_facts(project) if project and (needs_proj or (kind == "energy" and not audio) or audio) else None
    af = _audio_facts(audio, _spans_from_score(sf), want_tempo_key=False, onsets=False) if audio else None
    t1 = time.time()
    w, h, scale, lw = _vis_size(prefs, width, height)
    cv = Canvas(w, h, theme if theme in THEMES else "dark", scale)
    if kind == "energy":
        alt, dat = _vis_energy(cv, sf, af, section, lw)
    elif kind == "loudness":
        alt, dat = _vis_loudness(cv, af, section, lw)
    elif kind == "spectrogram":
        alt, dat = _vis_spectrogram(cv, af, section)
    elif kind == "mix_balance":
        alt, dat = _vis_mix_balance(cv, af, section, lw)
    elif kind == "arrangement":
        alt, dat = _vis_arrangement(cv, sf, section, lw)
    else:
        alt, dat = _vis_piano_roll(cv, sf, section, part, lw)
    png = cv.png()
    res = {"kind": kind, "alt": alt, "data": dat, "size": [w, h], "theme": theme if theme in THEMES else "dark"}
    mode = images or prefs.get("images") or "auto"
    show = mode == "always" or (mode == "auto" and prefs.get("vision") != "blind")
    if show:
        res["_image_png"] = base64.b64encode(png).decode()
    else:
        res["image"] = "not included (" + ("images=never" if mode == "never" else "vision: blind") + \
                       "); the alt text above says what it shows; images='always' includes it"
    if save:
        out = os.path.abspath(save)
        if os.path.isdir(out):
            out = os.path.join(out, f"{kind}.png")
        if not out.lower().endswith(".png"):
            out += ".png"
        H["writable"](out)
        with open(out, "wb") as f:
            f.write(png)
        res["saved"] = out
    res["steps_s"] = {"facts": round(t1 - t0, 3), "draw": round(time.time() - t1, 3)}
    return _queued_note(res, sf)


def fl_visualize(kind="energy", project=None, audio=None, section=None, part=None, theme="dark", width=None,
                 height=None, save=None, images=None):
    return visualize(kind, project, audio, section, part, theme, width, height, save, images)


# ============================================================================= hearing-aware second opinion
THIRD_OCT = [31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
             2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000]


def _third_octaves(f, Sp):
    import numpy as np
    return [(c, float(10 * np.log10(Sp[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-20))) for c in THIRD_OCT]


def tone_checks(af):
    """Absolute tone checks of a render (rules of thumb in data/access/hearing.json 'tone') plus clipping / phase.
    -> issues [{severity, kind, where, what, hz}]"""
    tn = data("hearing")["tone"]; w = af["whole"]; out = []
    if af.get("silent") or w.get("lufs") is None or w["lufs"] < -50:
        return out                                          # nothing (or too little) to judge
    b = w.get("bands") or {}
    loud = [r for r in af["rows"] if r.get("lufs") is not None and r["lufs"] > -50 and r.get("bands")]
    loud.sort(key=lambda r: -r["lufs"])
    judged = loud[:max(1, len(loud) // 2)] if loud else []

    def add(sev, kind, where, what, hz):
        out.append({"severity": sev, "kind": kind, "where": where, "what": what, "hz": hz})
    for r in judged or [dict(w, name="whole song")]:
        bb = r.get("bands") or {}
        if not bb:
            continue
        if bb["presence"] - bb["mid"] > tn["harsh_presence_minus_mid"]:
            add("med", "harsh", r["name"], f"strong upper-middle (2-5 kHz is {bb['presence'] - bb['mid']:+.1f} dB vs "
                f"the middle; typical mixes sit about 4-8 dB below) - can feel piercing or tiring", [2000, 5000])
            break
    if w.get("tilt") is not None:
        if w["tilt"] >= tn["bright_tilt"]:
            add("low", "bright", "whole song", f"very bright overall (tone slope {w['tilt']:+.1f} dB/octave; most "
                "finished mixes about -3 to -5)", [2000, 16000])
        elif w["tilt"] <= tn["dull_tilt"]:
            add("low", "dull", "whole song", f"dark overall (tone slope {w['tilt']:+.1f} dB/octave; most finished "
                "mixes about -3 to -5): the top end may be missing", [5000, 16000])
    if b.get("low") is not None:
        if b["low"] >= tn["low_heavy_db"]:
            add("low", "low_heavy", "whole song", f"very heavy bass ({b['low']:+.1f} dB of the whole in 20-200 Hz)",
                [20, 200])
        elif b["low"] <= tn["low_light_db"]:
            add("low", "low_light", "whole song", f"light bass ({b['low']:+.1f} dB of the whole in 20-200 Hz)", [20, 200])
    f, Sp = af["spectrum"]
    to = _third_octaves(f, Sp)
    lo_hz, hi_hz = tn["resonance_range"]
    for i in range(3, len(to) - 3):
        c, v = to[i]
        if not lo_hz <= c <= hi_hz:
            continue
        nb = sorted(x[1] for x in to[i - 3:i] + to[i + 1:i + 4])
        base = (nb[2] + nb[3]) / 2
        if v - base >= tn["resonance_db"]:
            add("med", "resonance_high", "whole song", f"a ringing peak around {c / 1000:g} kHz ({v - base:+.1f} dB "
                "above its neighbours) - often a whistle-like or piercing tone", [round(c / 2 ** (1 / 6)), round(c * 2 ** (1 / 6))])
            break
    sib = (f >= tn["sibilance_band"][0]) & (f < tn["sibilance_band"][1]); mid = (f >= 500) & (f < 2000)
    import numpy as np
    sdb = 10 * np.log10(Sp[sib].sum() + 1e-20) - 10 * np.log10(Sp[mid].sum() + 1e-20)
    if sdb > tn["sibilance_minus_mid"]:
        add("low", "sibilance", "whole song", f"a lot of 5-9 kHz ({sdb:+.1f} dB vs the middle): 's' sounds and "
            "cymbals may hiss", tn["sibilance_band"])
    for r in af["rows"]:
        if r.get("clipped", 0) > 2 or r.get("tp", -99) > 0:
            add("high", "clipping", r["name"], f"clipping in {r['name']} (true peak {r['tp']:+.1f} dBTP)", [20, 20000])
            break
    if w.get("corr") is not None and w["corr"] < 0:
        add("high", "phase", "whole song", f"left and right partly cancel (correlation {w['corr']:.2f}): on one "
            "speaker or a phone parts can vanish", [20, 20000])
    return sorted(out, key=lambda i: {"high": 0, "med": 1, "low": 2}[i["severity"]])


def second_opinion(user_says, audio=None, project=None, section=None):
    """Compare what the user says about a mix ("sounds fine", "too harsh") with the measurements, kindly."""
    if not audio:
        raise ValueError("needs audio= (a WAV / FLAC render of the version they mean; " + C.tool_name(H, "fl_render") +
                         " makes one)")
    sf = _score_facts(project) if project else None
    af = _audio_facts(audio, _spans_from_score(sf), want_tempo_key=False, onsets=False)
    if section:
        keep = {r["name"] for r in _section_filter(af["rows"], section)}
        af = dict(af, rows=[r for r in af["rows"] if r["name"] in keep])
    issues = tone_checks(af)
    prefs = access_prefs()
    if af.get("silent"):
        return {"you_said": user_says, "agree": None, "say": "The file is silent, so there is nothing to compare - "
                "check the render (fl_doctor finds muted or silent instruments).", "issues": []}
    h, st = _setting(prefs)
    pos = _says_positive(user_says)
    said = str(user_says or "").lower()
    agree = None; say = []
    claim_kinds = {"harsh": ("harsh", "resonance_high", "bright", "sibilance"), "piercing": ("harsh", "resonance_high"),
                   "bright": ("bright", "harsh"), "muddy": ("low_heavy",), "boomy": ("low_heavy",),
                   "thin": ("low_light",), "dull": ("dull",), "dark": ("dull",), "distorted": ("clipping",),
                   "too loud": ("clipping",), "hiss": ("sibilance",)}
    if pos:
        agree = not issues
        say.append("The measurements agree: nothing stands out." if agree else
                   f"It may well feel fine - and the measurements still show {len(issues)} thing"
                   f"{'s' if len(issues) > 1 else ''} worth a look:")
    else:
        named = [k for k in claim_kinds if k in said]
        if named:
            hit = [i for i in issues if any(i["kind"] in claim_kinds[k] for k in named)]
            agree = bool(hit)
            say.append(f"The measurements back that up ({', '.join(named)})." if hit else
                       f"The measurements don't show it ({', '.join(named)} looks within the usual range); it could be "
                       "one part rather than the whole mix, the speakers or the room - " +
                       C.tool_name(H, "fl_masking") + " and " + C.tool_name(H, "fl_mix_check") + " look per track.")
        else:
            say.append("Here is what the measurements show:" if issues else "Nothing stands out in the measurements.")
    for i in issues[:5]:
        line = f"{i['what']}" + (f" ({i['where']})" if i["where"] != "whole song" else "")
        if st and (_covers(st, i["kind"]) or any(lo <= i["hz"][0] and i["hz"][1] <= hi + 1 for lo, hi in st.get("trust_bands") or [])):
            line += f" - {st.get('say')}"
        say.append(line)
    res = {"you_said": user_says, "agree": agree, "say": " ".join(say[:1]) + ("\n- " + "\n- ".join(say[1:]) if len(say) > 1 else ""),
           "issues": issues, "measured_on": os.path.basename(audio),
           "honest": "measurements describe the sound, not taste; rules of thumb vary by genre - if it's meant to "
                     "sound this way, that's a valid choice",
           "next": ("fix suggestions: " + C.tool_name(H, "fl_mix_check") + " / fl_mix_engineer; to hear it "
                    "differently: " + C.tool_name(H, "fl_describe") + ", " +
                    C.tool_call(H, "fl_visualize", {"kind": "mix_balance", "...": 1}))}
    return access_adjust(res, "fl_second_opinion", user_says=user_says, prefs=prefs)


def fl_second_opinion(user_says, audio=None, project=None, section=None):
    return second_opinion(user_says, audio, project, section)


# ============================================================================= safe listening
def fl_safe_listening(minutes=None):
    """Monitoring-level and break advice. Honest: the speaker / headphone level in the room can't be measured."""
    sl = data("hearing")["safe_listening"]
    prefs = access_prefs()
    up = round((time.time() - _T0[0]) / 60)
    mins = float(minutes) if minutes is not None else None
    res = {"what_we_measure": sl["can_measure"], "guideline": sl["guideline"], "level": sl["level_tips"],
           "breaks": sl["break_tips"],
           "session": (f"you said about {mins:g} minutes: " + ("a good moment for a short break."
                                                               if mins >= sl["break_after_min"] else "fine for now.")
                       if mins is not None else
                       f"fl-headless has been running about {up} minutes (that's the server, not necessarily how long "
                       "you've been listening)")}
    if prefs.get("hearing") in ("hearing_aids", "cochlear_implant"):
        res["devices"] = sl["aids_tips"]
    res["note"] = "general listening advice, not medical advice"
    return res


# ============================================================================= fl_where (screen readers)
def fl_where():
    """A text map of the open FL for screen-reader users: focused window, playhead and section, selection, popups."""
    t0 = time.time()
    ctx = H["fl_context"]()
    try:
        where = H["fl_song"]("where")
    except Exception:
        where = {}
    try:
        pops = H["fl_dialogs"]()
    except Exception:
        pops = None
    L = []
    proj = ctx.get("project")
    L.append(f"Project: {os.path.splitext(os.path.basename(proj))[0]}." if proj else
             f"Project: {ctx.get('window_title') or 'unknown (not saved yet?)'}.")
    foc = ctx.get("focused")
    L.append(f"Focus: the {str(foc).replace('_', ' ')}" + (f" ('{ctx['focused_caption']}')" if ctx.get("focused_caption")
                                                           and str(ctx["focused_caption"]).lower() != str(foc).replace('_', ' ')
                                                           else "") + "." if foc else
             "Focus: no main FL window (a plugin or popup may have it).")
    bar = ctx.get("bar")
    sec = None
    if proj and bar is not None:
        try:
            sec = S.section_at(S.journal_get(proj), float(bar))
        except Exception:
            sec = None
    mode = "song" if where.get("song_mode") else ("pattern" if where else None)
    pos = f"{'Playing' if ctx.get('playing') else 'Stopped'} at bar {float(bar):g}" if bar is not None else \
        ("Playing" if ctx.get("playing") else "Stopped")
    if sec:
        pos += f", in {sec['name']}" + (f" (bars {sec['bar']:g}-{(sec['end_bar'] - 1):g})" if sec.get("end_bar") else "")
    if mode:
        pos += f"; {mode} mode"
    if where.get("recording"):
        pos += "; recording is armed"
    if where.get("metronome"):
        pos += "; metronome on"
    L.append(pos + ".")
    ch = ctx.get("channel")
    if ch:
        L.append(f"Selected channel: {ch['index']} '{ch.get('name')}'" + (f", instrument {ch['plugin']}" if ch.get("plugin") else "")
                 + (f", plays on mixer track {ch['mixer_track']}" if ch.get("mixer_track") is not None else "") + ".")
    sc = [c for c in ctx.get("selected_channels") or [] if not ch or c != ch["index"]]
    if sc:
        L.append(f"Also selected channels: {', '.join(map(str, sc[:16]))}.")
    mt = ctx.get("mixer_track")
    if mt:
        L.append(f"Mixer track: {mt['index']} '{mt.get('name')}'.")
    pt = ctx.get("pattern")
    if pt:
        L.append(f"Pattern: {pt['index']} '{pt.get('name')}'.")
    if ctx.get("playlist_selection_bars"):
        a, b = ctx["playlist_selection_bars"]
        L.append(f"Playlist selection: bars {a:g} to {b:g}.")
    if pops:
        for p in pops[:3]:
            p = p if isinstance(p, dict) else {"title": str(p)}
            btn = ", ".join(str(b) for b in p.get("buttons") or [])
            tx = p.get("text")
            tx = " ".join(str(x) for x in tx) if isinstance(tx, (list, tuple)) else str(tx or "")
            L.append(f"Popup open: '{p.get('title')}'" + (f" - {tx[:160]}" if tx.strip() else "")
                     + (f" (buttons: {btn})" if btn else "") + ". fl_dialog can answer it.")
    elif pops is not None:
        L.append("No popups open.")
    res = {"text": "\n".join(L), "focused": foc, "bar": bar, "playing": ctx.get("playing"),
           "section": sec.get("name") if sec else None, "channel": ch, "mixer_track": mt, "pattern": pt,
           "popups": len(pops) if pops is not None else None,
           "keys": f"say 'show the mixer' ({C.tool_name(H, 'fl_show')}) or use FL's keys: " + ", ".join(
               f"{a} {b}" for a, b in list(data("keys")["fl_keys"].items())[:6]),
           "steps_s": {"total": round(time.time() - t0, 3)}}
    # PC-VERIFY: fl_where on FL with the playlist focused, a channel selected and a save popup open - each line true
    return res


# ============================================================================= register
def register(ns):
    global H
    H = ns
    if not S.H:
        S.H = ns
    _DATA.clear(); _PREFS[0] = None; _TERM_RX.clear()
    P, Sch = ns["_p"], ns["_schema"]
    STR, INT, BOOL = ns["_STR"], ns["_INT"], ns["_BOOL"]
    ns["access_prefs"] = access_prefs; ns["access_text"] = access_text; ns["access_glossary"] = access_glossary
    ns["access_adjust"] = access_adjust; ns["access_steps"] = access_steps
    ns["describe_music"] = describe_music; ns["access_visualize"] = visualize; ns["access_tone_checks"] = tone_checks
    T = ns["TOOLS"]
    T["fl_access"] = (
        fl_access, "Accessibility preferences, opt-in: set ONLY what the user tells you helps them, in their words - "
        "never guess from how they write. hearing: typical | reduced | reduced_highs | reduced_lows | hearing_aids | "
        "cochlear_implant | deaf (then tools trust measurements over listening in those ranges and give no-listening "
        "alternatives); vision: typical | low (bigger picture labels) | blind (text first, pictures only on request); "
        "motor: typical | limited | keyboard_only | voice | switch (chat / keyboard instead of clicking); language: "
        "plain | standard | pro (jargon explained); pace: step_by_step | normal; images: auto | always | never; "
        "detail: short | full; notes: their own words. No arguments: show them. clear=[keys] or 'all'.",
        Sch(set=P("object", "{hearing|vision|motor|language|pace|images|detail|notes: value}", True),
            clear=P(["array", "string"], "keys to remove, or 'all'", True, items={"type": "string"})))
    T["fl_describe"] = (
        fl_describe, "Describe the music in words - for deaf and hard-of-hearing users, screen-reader users, "
        "beginners, anyone: tempo feel, key and mood, the parts and their roles, where it gets loud or quiet "
        "(whisper-soft / builds / explodes) per section, rhythm feel (straight / swung, busy / sparse), what changes "
        "between sections ('same chords as the Verse but fuller'), low end you would feel as vibration, stereo "
        "picture, punch. project= (.flp / .mid: notes, parts) and/or audio= (WAV / FLAC render: measured sound); "
        "compare=<the version before> says WHAT CHANGED in plain words. Says what is measured vs read from the notes "
        "vs interpreted. section=, detail=short|full, language=plain|standard|pro.",
        Sch(project=STR("the .flp or .mid (default: the one open in FL)", True),
            audio=STR("a WAV / FLAC render to measure", True),
            section=STR("one section: name, bars 'a-b' or time 'm:ss-m:ss'", True),
            detail=STR("short (default) | full", True),
            compare=P(["string", "array"], "the version before: .flp / .mid / .wav / .flac (or [project, audio])", True,
                      items={"type": "string"}),
            language=STR("plain | standard | pro (default: the user's preference)", True)))
    T["fl_visualize"] = (
        fl_visualize, "A picture of the music WITH alt text (always): energy (loudness per section, or energy from "
        "the notes), loudness (over time, -14 LUFS line), spectrogram (pitch range over time), mix_balance (level and "
        "stereo width per pitch range), arrangement (who plays when), piano_roll (notes of a part). High-contrast "
        "colorblind-safe colors, big labels, theme dark|light; bigger for vision=low, picture skipped for "
        "vision=blind unless images='always'. save=<path.png> keeps a copy (inside FLH_ROOTS).",
        Sch(kind=STR("energy | loudness | spectrogram | mix_balance | arrangement | piano_roll"),
            project=STR("the .flp / .mid", True), audio=STR("a WAV / FLAC render", True),
            section=STR("section name, bars 'a-b' or 'm:ss-m:ss'", True),
            part=P(["string", "integer"], "piano_roll: channel or part name (default: the pitched parts)", True),
            theme=STR("dark (default) | light", True), width=INT("pixels (default 1000)", True),
            height=INT("pixels (default 500)", True), save=STR("also write the PNG here", True),
            images=STR("auto | always | never", True)))
    T["fl_second_opinion"] = (
        fl_second_opinion, "Compare what the user says about a render ('sounds fine', 'too harsh', 'muddy') with "
        "measurements - kindly and honestly. Tone (harsh 2-5 kHz, ringing peaks, hiss, bright / dark, bass weight), "
        "clipping, phase. When the user has said their hearing is less reliable in a range (fl_access), the "
        "measurement is trusted there and that is said gently. Rules of thumb, not taste.",
        Sch(user_says=STR("what the user said about the sound"), audio=STR("a WAV / FLAC render of that version"),
            project=STR("the .flp (sections)", True), section=STR("one section", True)))
    T["fl_safe_listening"] = (
        fl_safe_listening, "Safe-listening help: monitoring level and break advice, honest that fl-headless measures "
        "the music's loudness but not the speaker / headphone level in the room, so nobody needs to turn it up to "
        "judge. minutes= how long they've been listening. General advice, no medical claims.",
        Sch(minutes=ns["_NUM"]("how long they've been listening", True)))
    T["fl_where"] = (
        fl_where, "Where am I? A plain-text map of the open FL for screen-reader and keyboard users: focused window, "
        "playing / stopped, playhead bar and song section, song / pattern mode, selected channel (instrument, mixer "
        "track), mixer track, pattern, playlist selection, open popups - plus the keys / chat commands to move on.",
        Sch())
    cp = ns.get("CROSS_PLATFORM")
    if isinstance(cp, set):                                  # offline tools: no FL needed, run on any OS
        cp.update(OFFLINE_TOOLS)
