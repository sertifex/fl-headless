"""flh_flex: every FLEX preset the user has, from every installed pack, ready to load into a song by name.

FLEX packs (.flexpack / .flex2pack) start with a file table whose entries are the presets ('Pad Dreams.flexpreset');
fl-headless reads only that table (a few KB of each pack, cached by size/date) and FLEX's store list (FLEX/cache.json:
each pack's genre and instrument tags). A FLEX channel's plugin state names its preset in two fixed text fields
(pack, preset - 1025 bytes each after a 16-byte header whose third number is their end, 2066); writing another
pack/preset there makes FLEX load that sound when FL opens the song (PC 2026-10-21: 'Core Brass' / 'BRS Horns Sustain'
written into a song, FLEX opened on it). The state's knob values (FLEX's macros, all neutral) are kept.

flex_search(query, kind, genre, limit, prefer, hint, avoid)
                                         ranked presets {preset, pack, kind, genre, tier, score, last_resort?}:
                                         quality tiers per pack + name words per kind (data/flex/quality.json),
                                         General MIDI style packs only as a last resort
flex_fst(preset, pack=None)              a .fst of FLEX on that preset (made from a template, cached)
flp_flex_preset(project, output, preset, pack, channel, name)
                                         put FLEX with that preset on a channel (new one by default)
The template: data/flex/flex_template.fst (FLEX's own state, nothing personal) or a FLEX sound the user saved.
"""
import json
import os
import re
import struct
import sys
import threading
import time

H = None
EXTS = (".flexpack", ".flex2pack")
PRESET_EXTS = (".flexpreset", ".flex2preset")
FIELD = 1025                        # pack / preset name fields in FLEX's state
HEAD = 16
NAMES_END = HEAD + 2 * FIELD        # 2066: the state's third header number
INDEX_VERSION = 2                   # 2: per pack its file-table end (a pack whose table claims more than the file has)
_MEMO = {}

# kind words: preset/pack name words and FLEX's own short prefixes (BRS = brass, STR = strings ...)
KINDS = [("808", ["808"]), ("bass", ["bass", "sub", "reese", "bs"]), ("keys", ["piano", "pno", "keys", "key", "rhodes",
         "ep", "epiano", "wurli", "clav"]), ("organ", ["organ", "org"]), ("strings", ["strings", "string", "str",
         "violin", "violins", "viola", "cello", "cellos", "orchestra", "ensemble"]), ("brass", ["brass", "brs", "horn",
         "horns", "trumpet", "trumpets", "trombone", "trombones", "tuba", "tubas", "flugel"]), ("woodwind", ["winds",
         "wind", "wnd", "flute", "flutes", "clarinet", "oboe", "bassoon", "sax", "saxophone"]), ("choir", ["choir",
         "vox", "voice", "voices", "aah", "aahs", "ooh", "vocal"]), ("pad", ["pad", "pads", "atmos", "atmosphere",
         "ambient", "drone", "texture"]), ("pluck", ["pluck", "plk", "pizz", "pizzicato", "harp", "kalimba", "marimba",
         "mallet"]), ("bells", ["bell", "bells", "chime", "celesta", "glock", "music box"]), ("guitar", ["guitar",
         "gtr", "acoustic", "electric"]), ("lead", ["lead", "ld", "saw", "solo"]), ("synth", ["synth", "syn", "arp",
         "synths", "chord", "stab", "seq", "sequencer", "arp", "poly", "analog", "analogue"]), ("drums", ["kick", "snare", "hat", "drum", "drums", "perc", "clap", "kit"]),
         ("fx", ["fx", "sfx", "riser", "impact", "sweep", "noise"])]


# kind names other modules / recipes use for a FLEX kind (templates ask {"flex": "piano"}; FLEX's kinds say keys)
KIND_ALIASES = {"piano": "keys", "epiano": "keys", "rhodes": "keys", "chords": "keys", "melody": "lead",
                "horn": "brass", "horns": "brass", "flute": "woodwind", "winds": "woodwind", "sax": "woodwind",
                "bell": "bells", "arp": "synth", "vocal": "choir", "vox": "choir", "sub": "bass", "pads": "pad"}


def kind_name(kind):
    k = str(kind or "").strip().lower()
    return KIND_ALIASES.get(k, k) or None


def _words(t):
    return re.findall(r"[a-z0-9]+", str(t or "").lower().replace("_", " "))


def kind_of(preset, pack="", pack_instruments=""):
    """The preset's kind: its own name decides first, then the pack's name, then FLEX's store tags for the pack -
    those only when they name ONE kind (a pack tagged 'Bass;Synth;Pads' says nothing about one preset), else 'synth'.
    The catalog shows the same kind (catalog_kind), so fl_sound_catalog and flp_flex_preset agree."""
    w = _words(preset)
    for k, keys in KINDS:                                  # the preset's own name decides first
        if any(x in w for x in keys):
            return k
    w2 = _words(pack)
    for k, keys in KINDS:
        if any(x in w2 for x in keys):
            return k
    tags = [t for t in re.split(r"[;,/]+", str(pack_instruments or "")) if t.strip()]
    hits = {k for t in tags for k, keys in KINDS if any(x in _words(t) for x in keys)}
    if len(hits) == 1:
        return hits.pop()
    return "synth"


# FLEX kinds -> the sound catalog's kind names (fl_sound_catalog uses piano / perc where FLEX says keys / drums)
CATALOG_KIND = {"keys": "piano", "drums": "perc", "808": "bass"}


def catalog_kind(kind):
    return CATALOG_KIND.get(kind, kind)


# FLEX's 808 presets sound two octaves below the written note (measured on FL 2026: Core 808s 'Heavy Atom' and
# 'Sub Below', Mobile Tuned 808 Bass '808 Clean' - a written C2 sounds C0); FLEX bass and keys presets play at pitch.
# Notes on such a channel are written this many semitones above the pitch you hear (the song journal's
# channels.N.transpose, like a pitched sampler's offset), so the composer, flp_score and every writer agree.
FLEX_808_OFFSET = 24


def sounding_offset(preset, pack=None):
    """Semitones a FLEX preset's notes are written above the pitch it sounds at (0 = plays at pitch).
    preset: a name, or an index row ({preset, pack, kind})."""
    hit = preset if isinstance(preset, dict) else find(preset, pack)
    return FLEX_808_OFFSET if hit and str(hit.get("kind") or "").lower() == "808" else 0


def _roots():
    out = []
    try:
        out += list(__import__("sys").modules["flh_catalog"]._flex_roots())
    except Exception:
        pass
    try:
        fl = H["fl_exe"]()
        out.append(os.path.join(os.path.dirname(fl), "Data"))
    except Exception:
        pass
    seen, res = set(), []
    for r in out:
        k = os.path.normcase(os.path.abspath(r))
        if k not in seen and os.path.isdir(r):
            seen.add(k); res.append(r)
    return res


def _pack_files():
    files = []
    for r in _roots():
        for root, dirs, fs in os.walk(r):
            if os.path.relpath(root, r).count(os.sep) >= 4:
                dirs[:] = []
            files += [os.path.join(root, f) for f in fs if f.lower().endswith(EXTS)]
    return sorted(set(files))


def read_pack(path, info=None):
    """Preset names from a pack's file table (reads only the start of the file). info (a dict): filled with
    'entries' and 'end' - the furthest byte the table says the pack's content reaches (offset + size)."""
    with open(path, "rb") as f:
        head = f.read(1 << 18)
        if len(head) < 8:
            return []
        n = struct.unpack(">I", head[4:8])[0]
        if n > 20000:
            raise ValueError("not a FLEX pack table")
        i, out = 8, []
        for _ in range(n):
            while i + 4 > len(head) or i + 4 + struct.unpack(">I", head[i:i + 4])[0] + 16 > len(head):
                more = f.read(1 << 18)
                if not more:
                    return out
                head += more
            L = struct.unpack(">I", head[i:i + 4])[0]
            name = head[i + 4:i + 4 + L].rstrip(b"\0").decode("utf-8", "replace")
            if info is not None:
                off, size = struct.unpack(">QQ", head[i + 4 + L:i + 4 + L + 16])
                info["entries"] = info.get("entries", 0) + 1
                if off + size < (1 << 48):                 # (a garbled entry never claims petabytes)
                    info["end"] = max(info.get("end", 0), off + size)
            i += 4 + L + 16
            low = name.lower()
            if low.endswith(PRESET_EXTS):
                out.append(name.rsplit(".", 1)[0].strip())
        return out


def _store_meta():
    """FLEX's own store list: pack name -> {genre, instrument}."""
    meta = {}
    for r in _roots():
        p = os.path.join(r, "cache.json")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8-sig") as f:
                rows = json.load(f)
        except (OSError, ValueError):
            continue
        for x in rows if isinstance(rows, list) else []:
            if not isinstance(x, dict):                  # FLEX's own file: never trusted to be well formed
                continue
            stem = os.path.splitext(os.path.basename(str(x.get("filename") or "")))[0]
            inst = _store_installed(x)
            for key in {stem, str(x.get("productName") or "")}:
                if key:
                    meta[key.lower()] = {"genre": x.get("genre") or "", "instrument": x.get("instrument") or "",
                                         **({"installed": inst} if inst is not None else {})}
    return meta


STORE_INSTALLED_KEYS = ("installed", "isInstalled", "is_installed", "downloaded", "isDownloaded", "is_downloaded")
STORE_STATE_KEYS = ("state", "status", "installState", "downloadState")
STORE_NOT_INSTALLED = ("notinstalled", "not installed", "not_installed", "available", "notdownloaded",
                       "not downloaded", "purchased", "owned", "cloud", "remote", "pending", "downloading")


def _store_installed(row):
    """FLEX's store row says the pack is installed (True), isn't (False) or says nothing (None).
    PC-VERIFY: which field FLEX's cache.json uses for a pack that is listed / owned but not downloaded."""
    for k in STORE_INSTALLED_KEYS:
        if k in row and isinstance(row[k], (bool, int, str)):
            v = row[k]
            if isinstance(v, str):
                v = v.strip().lower() in ("1", "true", "yes")
            return bool(v)
    for k in STORE_STATE_KEYS:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            low = v.strip().lower()
            if low in STORE_NOT_INSTALLED:
                return False
            if low in ("installed", "downloaded", "ready", "local"):
                return True
    return None


def _cache_path():
    return os.path.join(os.path.dirname(H["STATE"]), "flex_index.json")


INDEX_RESCAN_S = 60.0                                             # a full rescan at most this often...


def _quick_sig():
    """...unless a pack root or one of its folders changed (a pack installed or removed): their dates, cheap."""
    out = []
    for r in _roots():
        try:
            out.append((r, os.stat(r).st_mtime_ns, sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(r)
                                                          if e.is_dir())))
        except OSError:
            out.append((r, None, None))
    return out


def index(refresh=False):
    """[{preset, pack, kind, genre, file}] for every installed pack (cached by each pack's size and date)."""
    _NOTE_TL.note = None
    memo = _MEMO.get("idx")
    quick = _quick_sig()
    if memo and not refresh and memo[3] == quick and time.time() - memo[2] < INDEX_RESCAN_S:   # a song asks a dozen
        return memo[1]                                # times: walking every pack folder each time took 17 s (PC)
    files = _pack_files()
    sig = [[f, os.path.getsize(f), int(os.path.getmtime(f))] for f in files]
    if memo and memo[0] == sig and not refresh:
        _MEMO["idx"] = (sig, memo[1], time.time(), quick)
        return memo[1]
    C = sys.modules.get("flh_catalog")
    if C is None or not hasattr(C, "index_build"):
        return _index_build(sig, quick, refresh, None)
    out, still = C.index_build("flex", "FLEX preset index", lambda b: _index_build(sig, quick, refresh, b),
                               C.SAMPLES_WAIT_S)
    if out is None:                                   # a first scan still running (another thread): what's read so far
        _NOTE_TL.note = C.build_note(still, partial=True)
        return list(still.get("partial") or [])
    _NOTE_TL.note = C.take_note()                     # 'waited n s for ...' (index_build left it in this thread)
    return out


_NOTE_TL = threading.local()


def take_note():
    """The note the last index() call in this thread left (waited for / partial index), cleared."""
    n = getattr(_NOTE_TL, "note", None)
    _NOTE_TL.note = None
    return n


def _index_build(sig, quick, refresh, b):
    """index()'s rebuild: packs whose size and date are cached cost nothing; new ones are read (their file tables
    only). b: progress (packs read / packs to read, with an estimate) and the presets so far in b['partial']."""
    cache = {}
    if not refresh:
        try:
            with open(_cache_path(), encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, ValueError):
            cache = {}
    if cache.get("version") != INDEX_VERSION:
        cache = {}
    packs = cache.get("packs") or {}
    meta = _store_meta()
    out, keep, changed = [], {}, False
    todo = sum(1 for f, size, mt in sig if f"{f}|{size}|{mt}" not in packs); read = 0
    tick = getattr(sys.modules.get("flh_catalog"), "build_tick", None) if b is not None else None
    if b is not None:
        b["partial"] = out
    for f, size, mt in sig:
        key = f"{f}|{size}|{mt}"
        row = packs.get(key)
        if row is None:
            info = {}
            try:
                row = {"presets": read_pack(f, info), "end": info.get("end", 0)}
            except Exception as e:
                row = {"presets": [], "error": str(e)[:80]}
            changed = True; read += 1
            if tick is not None:
                tick(b, 100.0 * read / max(1, todo))
        keep[key] = row
        pack = os.path.splitext(os.path.basename(f))[0]
        m = meta.get(pack.lower()) or {}
        why = _not_installed(row, size, m)
        for p in row["presets"]:
            out.append({"preset": p, "pack": pack, "kind": kind_of(p, pack, m.get("instrument")),
                        "genre": m.get("genre") or None, "file": f,
                        **({"installed": False, "why": why} if why else {})})
    if changed or len(keep) != len(packs):
        try:
            os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
            with open(_cache_path(), "w", encoding="utf-8") as fh:
                json.dump({"version": INDEX_VERSION, "packs": keep}, fh)
        except OSError:
            pass
    _MEMO["idx"] = (sig, out, time.time(), quick)
    return out


def _not_installed(row, size, meta):
    """Why a pack's sounds are most likely not on this PC (None when nothing says so): its file is shorter than its
    own file table says (a download that didn't finish / a placeholder), or FLEX's store list says it isn't
    installed. Presets of such a pack load silent (PC 2026-10-23: two presets of a store pack rendered silent while a
    Core pack played). PC-VERIFY: a pack FLEX lists but hasn't downloaded is caught here (checklists/newsong.md 14b)."""
    end = int(row.get("end") or 0)
    if end and size and end > size:
        return (f"its file holds {size / 1e6:.1f} MB of the {end / 1e6:.1f} MB its table lists: not (fully) "
                "downloaded - download it in FLEX's browser")
    if meta.get("installed") is False:
        return "FLEX's store list says it isn't installed: download it in FLEX's browser"
    return None


def pack_flags(rows=None):
    """{pack: why} for FLEX packs not to pick: not (fully) downloaded (index rows 'installed': False), or a preset of
    it made no sound on this PC (flh_newsong's silent_sounds) - the rest of that pack likely won't either."""
    rows = index() if rows is None else rows
    out = {}
    for r in rows:
        if r.get("installed") is False and r["pack"] not in out:
            out[r["pack"]] = r.get("why") or "not downloaded"
    try:
        sil = H["silent_sound_keys"]() if H and H.get("silent_sound_keys") else set()
    except Exception:
        sil = set()
    for k in sil:
        parts = str(k).split("|")
        if len(parts) == 3 and parts[0] == "flex" and parts[1] and parts[1] not in {p.lower() for p in out}:
            pk = next((r["pack"] for r in rows if r["pack"].lower() == parts[1]), parts[1])
            out.setdefault(pk, f"a preset of it ('{parts[2]}') made no sound on this PC: the pack is probably "
                               "not downloaded / not supported")
    return out


# ----------------------------------------------------------------------------- quality (data/flex/quality.json)
# PC-VERIFY: the pack names / tiers in quality.json match the packs FLEX installs, and the ranking sounds right by
# ear (checklists/newsong.md 14a)
QUALITY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flex", "quality.json")


def _norm_name(t):
    return " ".join(_words(t))


def quality_data():
    """data/flex/quality.json, with a user's <state folder>/flex/quality.json on top (top-level keys replaced)."""
    paths = [QUALITY_FILE]
    try:
        paths.append(os.path.join(os.path.dirname(H["STATE"]), "flex", "quality.json"))
    except Exception:
        pass
    sig = tuple((p, os.path.getmtime(p)) for p in paths if os.path.isfile(p))
    memo = _MEMO.get("quality")
    if memo and memo[0] == sig:
        return memo[1]
    q = {}
    for p, _ in sig:
        try:
            with open(p, encoding="utf-8-sig") as f:
                d = json.load(f)
            if isinstance(d, dict):
                q.update(d)
        except (OSError, ValueError):
            continue
    gm = set()
    for n in q.get("gm_style_names") or []:
        gm.add(_norm_name(n))
    q["_gm"] = gm
    _MEMO["quality"] = (sig, q)
    return q


def _w(q, key, dflt):
    try:
        return float((q.get("weights") or {}).get(key, dflt))
    except (TypeError, ValueError):
        return dflt


def gm_style(name, q=None):
    """Does a preset name look like a General MIDI program ('Choir Aahs', 'Fretless Bass Mellow', 'Organ 1')?"""
    q = q or quality_data(); n = _norm_name(name)
    if n in q["_gm"]:
        return True
    return any(" " in g and n.startswith(g + " ") for g in q["_gm"])


def pack_tier(pack, preset=None, q=None):
    """(tier, why) of a FLEX pack: the first glob in quality.json's 'packs' that matches; an unrated pack gets
    default_tier, lowered to gm_style_tier when the preset's name is a General MIDI program name."""
    import fnmatch
    q = q or quality_data(); low = _norm_name(pack)
    raw = str(pack or "").lower()
    for rule in q.get("packs") or []:
        pats = rule.get("match") or []
        pats = [pats] if isinstance(pats, str) else pats
        if any(fnmatch.fnmatch(low, str(p).lower()) or fnmatch.fnmatch(raw, str(p).lower()) for p in pats):
            return int(rule.get("tier", 2)), rule.get("why") or ""
    t = int(q.get("default_tier", 2))
    if preset and gm_style(preset, q):
        return min(t, int(q.get("gm_style_tier", 1))), "a General MIDI style name"
    return t, ""


def name_quality(name, kind=None, q=None, asked=None):
    """Quality words in a preset name for its kind ('Felt', 'Rhodes', 'B3' up; 'Honky', 'Init', a buzzy 'Electronic
    Organ' down). asked = words the part asked for: a 'bad' word that was asked for ('farfisa', 'reed') isn't bad."""
    q = q or quality_data(); n = " " + _norm_name(name) + " "
    words = q.get("words") or {}
    ask = {_norm_name(a) for a in (asked or ()) if _norm_name(a)}
    good, bad = 0.0, 0.0
    for k in ("*", kind_name(kind) or ""):
        e = words.get(k) or {}
        good += sum(_w(q, "good_word", 0.6) for x in e.get("good") or [] if " " + _norm_name(x) + " " in n)
        bad += sum(_w(q, "bad_word", -1.5) for x in e.get("bad") or [] if " " + _norm_name(x) + " " in n
                   and not set(_norm_name(x).split()) <= ask)
    return min(good, _w(q, "good_cap", 1.2)) + bad


def genre_words(genre, q=None):
    q = q or quality_data(); g = str(genre or "").strip().lower()
    if not g:
        return []
    gw = q.get("genre_words") or {}
    out = list(gw.get(g) or [])
    return out or [re.sub(r"[^a-z0-9]", "", g)]


def _genre_hit(text, gws):
    toks = _words(text); compact = "".join(toks)
    toks += [re.sub(r"[^a-z0-9]", "", c) for c in re.split(r"[;,/\s]+", str(text or "").lower())]   # 'Lo-Fi' = lofi
    return any(w in toks or (len(w) >= 5 and w in compact) for w in gws)


def quality(preset, pack, kind=None, genre=None, store_genre=None, q=None, asked=None):
    """{tier, score, last_resort, why}: how good this preset probably sounds for the kind and genre."""
    q = q or quality_data()
    tier, why = pack_tier(pack, preset, q)
    s = _w(q, "tier", 1.2) * tier + name_quality(preset, kind, q, asked)
    gws = genre_words(genre, q)
    if gws and store_genre and _genre_hit(str(store_genre).replace(";", " "), gws):
        s += _w(q, "store_genre", 1.5)
    elif gws and _genre_hit(pack, gws):
        s += _w(q, "pack_genre", 1.0)
    return {"tier": tier, "score": round(s, 2), "last_resort": tier <= 0, "why": why}


def _wordlist(x):
    if not x:
        return []
    if isinstance(x, (list, tuple, set)):
        x = " ".join(str(v) for v in x)
    return [w for w in _words(x) if len(w) > 1]


def flex_search(query=None, kind=None, genre=None, limit=10, exclude=(), prefer=None, hint=None, avoid=None,
                rank_quality=True, include_unavailable=False):
    """Ranked FLEX presets: kind (pad, strings, brass, 808, keys ...), words matched against preset and pack names
    (query = a filter: presets matching none of its words are left out), the pack's quality tier and name quality
    (data/flex/quality.json), the genre (FLEX's store tags, pack names), prefer = character words (a strong
    preference, never a filter; the first words count most), hint = weaker words, avoid = phrases that push a preset down; liked sounds first,
    disliked last (sound taste). General MIDI style packs (tier 0) come after every other preset of the kind -
    unless the query names that preset. Packs whose sounds aren't on this PC (pack_flags: not downloaded) are left
    out (include_unavailable=true lists them, marked); a pack with one preset that made no sound here goes last,
    with two it's left out too."""
    rows = index()
    flags = pack_flags(rows)
    sil_n = {}
    words = [w for w in _words(query) if len(w) > 1]
    phrase = " ".join(_words(query))
    want = kind_name(kind)
    pref, weak = _wordlist(prefer), [w for w in _wordlist(hint) if len(w) > 2]
    av = [" " + _norm_name(a) + " " for a in ([avoid] if isinstance(avoid, str) else avoid or []) if _norm_name(a)]
    q = quality_data()
    asked = set(words) | set(pref)                       # words the part asked for are never 'bad' words
    try:
        C = __import__("sys").modules.get("flh_catalog"); taste = C.taste_get() if C else None
    except Exception:
        C, taste = None, None
    ex = {str(x).lower() for x in exclude or ()}
    try:                                                 # presets that made no sound on this PC (flh_newsong's
        sil = H["silent_sound_keys"]() if H.get("silent_sound_keys") else set()   # level balance found them)
        skey = H["silent_key"]
    except Exception:
        sil = set()
    scored = []
    W = {k: _w(q, k, d) for k, d in (("character_name", 1.5), ("character_pack", 0.6), ("hint_name", 0.4),
                                      ("hint_pack", 0.15), ("avoid", -3.0))}
    for k in sil:
        parts = str(k).split("|")
        if len(parts) == 3 and parts[0] == "flex":
            sil_n[parts[1]] = sil_n.get(parts[1], 0) + 1
    for r in rows:
        if r["preset"].lower() in ex or (sil and skey("FLEX", r["pack"], r["preset"]).lower() in sil):
            continue
        down = r.get("installed") is False or sil_n.get(r["pack"].lower(), 0) >= 2
        if down and not include_unavailable:
            continue
        s = 0.0
        if want:
            if r["kind"] != want:
                continue
            s += 1.0
        nm, pk = set(_words(r["preset"])), set(_words(r["pack"]))
        qs = 0.0
        for w in words:
            qs += 2.0 if w in nm else (0.8 if w in pk else 0.0)
        if words and qs <= 0:
            continue
        s += qs
        for i, w in enumerate(pref):                     # character words in order of importance
            f = max(0.5, 1.0 - 0.1 * i)
            s += f * (W["character_name"] if w in nm else (W["character_pack"] if w in pk else 0.0))
        for w in weak:
            s += W["hint_name"] if w in nm else (W["hint_pack"] if w in pk else 0.0)
        if av:
            low = " " + _norm_name(r["preset"]) + " "
            s += sum(W["avoid"] for a in av if a in low)
        last = False
        if rank_quality:
            qq = quality(r["preset"], r["pack"], r["kind"], genre, r.get("genre"), q, asked=asked)
            s += qq["score"]
            last = qq["last_resort"] and not (phrase and phrase in _norm_name(r["preset"]))
            tier = qq["tier"]
        else:
            tier = None
            if genre and r.get("genre") and _genre_hit(str(r["genre"]).replace(";", " "), genre_words(genre, q)):
                s += 0.7
        if taste is not None:
            s += C.taste_score(r["preset"], taste)
        if r["pack"] in flags:                           # unavailable / silent here: after every other choice
            last = True
            s -= 5.0
        scored.append((last, s, r, tier))
    scored.sort(key=lambda x: (x[0], -x[1], x[2]["pack"].lower(), x[2]["preset"].lower()))
    out = []
    for last, s, r, tier in scored[:int(limit or 10)]:
        d = dict(r, score=round(s, 2))
        if tier is not None:
            d["tier"] = tier
        if last:
            d["last_resort"] = True
        if r["pack"] in flags:
            d["unavailable"] = flags[r["pack"]]
            d.pop("why", None)
        out.append(d)
    return out


def find(preset, pack=None):
    p, k = str(preset).strip().lower(), str(pack or "").strip().lower()
    rows = [r for r in index() if r["preset"].lower() == p and (not k or r["pack"].lower() == k)]
    if not rows:
        rows = [r for r in index() if p in r["preset"].lower() and (not k or k in r["pack"].lower())]
    return rows[0] if rows else None


def _template():
    """FLEX .fst bytes to patch: a FLEX sound the user saved (newest), else the shipped template."""
    cands = []
    try:
        sd = H.get("SOUNDS")
        for f in os.listdir(sd) if sd and os.path.isdir(sd) else []:
            if f.lower().endswith(".fst"):
                p = os.path.join(sd, f)
                try:
                    if (H["_fst_label"](p) or "").upper().startswith("FLEX"):
                        cands.append((os.path.getmtime(p), p))
                except Exception:
                    pass
    except Exception:
        pass
    cands.sort(reverse=True)
    paths = [p for _, p in cands] + [os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flex",
                                                   "flex_template.fst")]
    for p in paths:
        try:
            b = open(p, "rb").read()
            if _state_span(b):
                return b
        except OSError:
            continue
    raise RuntimeError("no FLEX template: put FLEX on any channel once and save it with flp_save_sound")


def _state_span(fst):
    """(start, end) of FLEX's plugin state inside a .fst, checked against its layout; None when it isn't one."""
    d = fst; ev = H["_events"](d)
    for e in ev:
        if e["id"] == H["EV_PLUGIN_STATE"]:
            p = H["_payload"](d, e)
            if len(p) > NAMES_END and struct.unpack("<I", p[8:12])[0] == NAMES_END:
                return e
    return None


def state_names(state):
    """(pack, preset) named in FLEX plugin state bytes, or None when it isn't a FLEX state this version knows."""
    st = bytes(state or b"")
    if len(st) <= NAMES_END or struct.unpack("<I", st[8:12])[0] != NAMES_END:
        return None
    txt = lambda off: st[off:off + FIELD].split(b"\0")[0].decode("latin-1", "replace").strip()
    return txt(HEAD), txt(HEAD + FIELD)


def patch_state(state, pack, preset):
    """FLEX state bytes with another pack/preset in its name fields (same length; knob values kept)."""
    st = bytearray(state)
    if len(st) <= NAMES_END or struct.unpack("<I", bytes(st[8:12]))[0] != NAMES_END:
        raise ValueError("not a FLEX state this version of fl-headless knows (pack/preset fields not found)")
    for off, txt in ((HEAD, pack), (HEAD + FIELD, preset)):
        raw = str(txt).encode("latin-1", "replace")[:FIELD - 1]
        st[off:off + FIELD] = raw + b"\0" * (FIELD - len(raw))
    return bytes(st)


def flex_fst(preset, pack=None):
    """Path of a .fst that loads FLEX on this preset (made once, kept in the state folder)."""
    hit = find(preset, pack)
    if not hit:
        raise ValueError(f"no FLEX preset '{preset}'" + (f" in '{pack}'" if pack else "") +
                         " among the installed packs (flex_search lists them)")
    out_dir = os.path.join(os.path.dirname(H["STATE"]), "flex_presets")
    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r'[<>:"/\\|?*]+', " ", f"{hit['pack']} - {hit['preset']}").strip()
    out = os.path.join(out_dir, safe + ".fst")
    if os.path.isfile(out):
        return out, hit
    tpl = _template()
    e = _state_span(tpl)
    new_state = patch_state(H["_payload"](tpl, e), hit["pack"], hit["preset"])
    ev = H["_events"](tpl)
    k = next(i for i, x in enumerate(ev) if x["off"] == e["off"])
    b = H["_splice"](tpl, ev, [(k, k + 1, H["_data_event"](H["EV_PLUGIN_STATE"], new_state))])
    ev = H["_events"](b)                                   # the channel's display name: the preset
    for i, x in enumerate(ev):
        if x["id"] == H["EV_DISPLAY_NAME"]:
            b = H["_splice"](b, ev, [(i, i + 1, H["_data_event"](H["EV_DISPLAY_NAME"], _txt16(hit["preset"])))])
            break
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        f.write(_fix_len(b))
    os.replace(tmp, out)
    return out, hit


def _txt16(s):
    return str(s).encode("utf-16-le") + b"\0\0"


def _fix_len(b):
    """A .fst whose FLdt length matches its data after an edit."""
    hl = struct.unpack("<I", b[4:8])[0]
    at = 8 + hl
    if b[at:at + 4] != b"FLdt":
        return b
    return b[:at + 4] + struct.pack("<I", len(b) - at - 8) + b[at + 8:]


def flp_flex_preset(project, output=None, preset=None, pack=None, channel=None, name=None):
    """FLEX on a channel with one of the installed presets. channel=None: a new channel (a copy of a plugin channel
    the song has, routed like it) - else that channel becomes FLEX on the preset (its notes stay)."""
    if not preset:
        raise ValueError("give preset (flex_search / fl_sound_catalog(kind=...) lists them)")
    output = output or project
    fst, hit = flex_fst(preset, pack)
    res = {"preset": hit["preset"], "pack": hit["pack"], "kind": catalog_kind(hit["kind"])}
    why = pack_flags().get(hit["pack"])
    if why:                                               # still written (asked for by name): said up front
        res["warning"] = f"'{hit['pack']}' {why} - it will most likely load silent"
    new_off = sounding_offset(hit)
    try:
        jr = sys.modules["flh_store"].journal_get(project)
        score = sys.modules.get("flh_score")
        old_off = score.sounding_offsets(jr).get(int(channel), 0) if (score and channel is not None) else 0
    except Exception:
        old_off = 0

    def run():
        ch = channel
        NS = sys.modules.get("flh_newsong")
        if ch is None:
            d = H["_load"](project); ev = H["_events"](d)
            types = NS._chan_types(d, ev) if NS else {}
            src = next((c for c, t in sorted(types.items()) if t == 2), None)
            if src is None:
                src = min(types) if types else 0
            ch = H["flp_add_channel"](project, output, src)["new_channel"]
            res["new_channel"] = ch
        H["flp_load_plugin"](output if channel is None else project, output, fst, channel=int(ch))
        res["channel"] = int(ch)
        if channel is not None and new_off != old_off and NS:   # notes written for the old offset: moved to the new
            d = H["_load"](output)
            d, moved = NS._transpose_channel(d, int(ch), new_off - old_off)
            if moved:
                H["_save"](d, output)
                res["notes_moved"] = moved
        return {"output": output}
    H["_with_open_project"](output, output, run)
    if new_off or old_off:
        ch = res["channel"]
        try:
            sys.modules["flh_store"].journal_update(output, set={
                "channels": {str(ch): {"transpose": new_off or None}},
                "compose": {"transpose": {str(ch): new_off or None}}})
        except Exception as e:
            res["journal_warning"] = f"the song journal wasn't updated ({str(e)[:80]}): tuning offset not recorded"
        if new_off:
            res["tuning"] = (f"FLEX 808 presets sound {new_off // 12} octaves below the written note: notes on channel "
                             f"{ch} are written {new_off:+d} semitones from the pitch you hear (journal channels.{ch}"
                             ".transpose; the composer and flp_score take it into account)")
        else:
            res["tuning"] = f"this preset plays at pitch: channel {ch}'s {old_off:+d} offset was removed"
        if res.get("notes_moved"):
            res["tuning"] += (f"; its {res['notes_moved']} existing notes moved {new_off - old_off:+d} so they sound "
                              "at the pitch they were written for")
    res["note"] = "FLEX opens on this preset when FL loads the song" + (f"; named '{name}'" if name else "")
    return res


def _flex_list(query=None, kind=None, genre=None, limit=20):
    presets = flex_search(query, kind, genre, limit)
    note = take_note()
    rows = index()
    note = note or take_note()
    out = {"presets": presets, "packs": len({r["pack"] for r in rows}), "total": len(rows)}
    fl = pack_flags(rows)
    if fl:                                                # left out of the picks: said, never silently
        out["unavailable_packs"] = fl
    if note:
        out["index_note"] = note
    return out


def register(ns):
    global H
    H = ns
    ns["flex_search"] = flex_search
    ns["flex_fst"] = flex_fst
    ns["flex_index"] = index
    ns["flex_pack_flags"] = pack_flags
    ns["flex_kind"] = kind_name
    ns["flex_quality"] = quality
    S, STR, INT = ns["_schema"], ns["_STR"], ns["_INT"]
    ns["TOOLS"]["flp_flex_preset"] = (
        flp_flex_preset, "FLEX with any installed preset on a channel, by name - every pack (Core, free, store): "
        "preset='BRS Horns Sustain' (pack optional). channel omitted = a new channel. fl_sound_catalog(kind=...) or "
        "flh_call('flex_list') finds presets.",
        S(project=ns["_FLP"], output=ns["_OUT"], preset=STR("FLEX preset name"), pack=STR("its pack (if names repeat)", True),
          channel=INT("channel to turn into FLEX (default: a new one)", True), name=STR("channel name", True)))
    ns["TOOLS"]["flex_list"] = (
        _flex_list,
        "FLEX presets installed on this PC, ranked: kind (pad, strings, brass, 808, keys, lead...), words, genre, pack "
        "quality (Core / Essential packs up, General MIDI style packs last; data/flex/quality.json).",
        S(query=STR("words, e.g. 'warm epic'", True), kind=STR("pad | strings | brass | 808 | keys | lead ...", True),
          genre=STR("genre", True), limit=INT("results (default 20)", True)))
    try:
        ns["TOOL_GROUPS"]["flp_flex_preset"] = "sounds"; ns["TOOL_GROUPS"]["flex_list"] = "sounds"
    except Exception:
        pass
