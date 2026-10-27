"""fl-headless memory: the collection profile (house rules for a set of songs) and a journal per song.

Extension contract (every flh_*.py extension follows it):
    register(ns)   ns is the server's live namespace (dict). Keep it in a module global, look functions up in it
                   at call time (ns["fl_render"]) so flh_reload's fresh copy is used, and add tools to ns["TOOLS"]
                   as name -> (fn, description, input_schema). Project writers: wrap with
                   ns["_guard_open_project"](fn, "project", "output") and add ns["_OPEN_PROJECT_GUARD"][name].

Profile:  %LOCALAPPDATA%/fl-headless/collections/<name>.json   (the active one is state["collection"])
Journal:  <project folder>/<project stem>.journal.json          (travels with the project)

Profile keys (all optional; flh_setup(action="apply") writes a neutral starting profile):
    fl_edition      "fruity" | "producer" | "signature" | "all_plugins"   (which native FL plugins are owned)
    owned_plugins   ["Sytrus", "FLEX", "INSTRUMENT", "Fruity *", ...]   beats the edition table
    trial_plugins   ["Harmor", ...]                                       never use / flag
    palette         {"melody": "#3E6A9E", "sections": ["#3D2A5C", ...], ...}
    finish          {"lufs": -14.0, "true_peak": -1.0, "bits": 24, "fade_s": 2.5}   (these are the defaults)
    finish_by_role  {"opener": {"lufs": -13.0}}
    final_root      "C:\\Users\\you\\Music\\My Album"
    final_name      "{song} - FINAL.flac"   (file goes in <final_root>/<song>/)
    songs           {"My Song": {"project": "D:\\...\\My Song.flp", "role": "opener", "order": 1}}
    final_subfolder true/false: finals in <final_root>/<song>/ (default) or straight in final_root
    folder          the collection's song folder: projects under it belong to the collection (as do the songs
                    listed in 'songs', their own folders, and songs whose journal names the collection)
  Scope: the collection's taste (style, ear_candy, finish, references ...) applies only to its own songs
  (profile_for / collection_of); user-wide keys (USER_WIDE_KEYS: edition, owned plugins, palette, access ...) apply
  to every song. A song's journal 'collection' ('' = none) wins over the lists.
    style           free text rules ("vocals up front; pads quiet and wide"); words like "piano-led", "sound real",
                    "support layers faint" make flp_arrange_song's ear candy organic (flh_compose)
    ear_candy       "off" | "organic" | "subtle" | "normal" | "lots" | "synthetic" ("organic:normal" sets both): the
                    arrangement ear candy for every song of the collection (a song's journal / the call win)
    tone_reference  a reference track for tone/mix comparisons (path)
  read by other modules:
    sample_folders  ["D:\\Samples", ...]       extra sample folders for the sample catalog (flh_catalog)
    new_song_folder where flh_new_song puts new songs (also accepted: projects_root) (flh_newsong)
    palette         {"<role or family>": "#RRGGBB", "drums": "#..", "sections": [...], "bus": {...}}  channel /
                    section colors for new songs (flh_newsong); darker shades read best in FL
    toolset         "essential" | "full" | "mix,score,..."   which tools are listed to the client (server)
    access          {"hearing": ..., "motor": ..., "language": ..., "pace": ...}   opt-in accessibility (flh_access)
    teaching        "off" | "beginner" | ...  how much flh_teach explains (flh_extras)
    reload_mode     "auto" | "lazy" | "immediate"  edits of the project open in FL (server; this PC's flh_sync
                    mode=... wins)
Collection names are file names: anything but path separators, '..', < > : " | ? *, control characters, reserved
Windows names and a trailing dot (at most 64 characters).
Background jobs: inside a job, report progress with ns["_job_progress"](step="render", pct=40) (no-op elsewhere).
"""
import json
import os
import re
import threading
import time

H = {}                                                   # the server namespace, set by register()

DEFAULT_JOURNAL = {"song": None, "collection": None, "role": None,   # role: opener / track / closer
                   "sections": [],          # [{"name", "bar", "end_bar", "pattern", "color"}]
                   "channels": {},          # {"5": {"role": "choir", "name": "Choir", "mixer": 7, "level_db": -22}}
                   "likes": [], "dislikes": [], "notes": [],
                   "performance": {},       # owned by flh_perform (sources, per-section tempo/dyn/humanize)
                   "performance_history": [], "performance_applied": {},
                   "versions": [],          # [{"label", "path", "when", "note"}]
                   "final": {},             # {"path", "lufs", "true_peak", "length", "when"}
                   "updated": None}


def _dir():
    return os.path.join(os.path.dirname(H["STATE"]), "collections")


def safe_name(name):
    """A collection name is a file name inside the collections folder. Normal names work ("Mike's Songs",
    "Album (2025)", "Rock & Roll", "Lo-Fi, Vol 2", "Beats #3"); refused only: path separators, '..', characters a
    Windows file name can't hold, control characters, reserved Windows names, a trailing dot (ValueError says
    which). Returns it stripped."""
    f = H.get("safe_file_name")
    if f:
        return f(name, "collection name")
    n = str(name or "").strip()                          # (no server namespace: the same core rules)
    if not n or len(n) > 64 or ".." in n or n.endswith(".") or any(c in n for c in '/\\<>:"|?*') or \
            any(ord(c) < 32 for c in n):
        raise ValueError(f"collection name {str(name)[:60]!r} can't be used as a file name")
    return n


def _profile_path(name):
    n = safe_name(name)
    d = os.path.abspath(_dir()); p = os.path.abspath(os.path.join(d, n + ".json"))
    if os.path.normcase(os.path.dirname(p)) != os.path.normcase(d):
        raise ValueError("collection name must not point outside the collections folder")
    return p


LAST_REFUSED = {"name": None, "why": None}               # a stored name that can't be read (shown by flh_profile)


_JLOCK = threading.RLock()                               # journal / profile read-modify-write: background jobs
                                                         # (finalize's file phase, the balance) write beside tools


def _write_json(path, obj, indent=1):
    """Temp file + os.replace: an interrupted write never leaves a half profile / journal."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"     # per thread: two writers never share one
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent)
    try:                                                 # OneDrive / an antivirus holding the old file a moment
        (H.get("_retry_os") or (lambda fn: fn()))(lambda: os.replace(tmp, path))
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _deep_merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _deep_merge(a[k], v)
        elif v is None and k in a:
            a.pop(k)                                     # None deletes a key
        else:
            a[k] = v
    return a


# ----------------------------------------------------------------------------- collection profile
def profile_name(name=None):
    return name or H["_state"]().get("collection")


def profile_get(name=None):
    """The collection profile dict ({} when none is set)."""
    name = profile_name(name)
    if not name:
        return {}
    try:
        p = _profile_path(name)
    except ValueError as e:                              # a name no file can have: say so (flh_profile shows it)
        LAST_REFUSED.update(name=str(name)[:80], why=str(e))
        return {}
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8-sig") as f:
            prof = json.load(f)
    except ValueError as e:                              # a damaged file never breaks every tool that reads it
        LAST_REFUSED.update(name=str(name)[:80], why=f"its file {os.path.basename(p)} is damaged ({str(e)[:60]}); "
                            "defaults are used until it's fixed or set again (the damaged file is kept aside)")
        return {}
    return prof if isinstance(prof, dict) else {}


def profile_set(name, updates, activate=True):
    name = safe_name(name)
    p = _profile_path(name)
    with _JLOCK:
        prof = profile_get(name) or {"name": name}
        _keep_damaged(p)                                 # an unreadable profile is kept aside, not overwritten
        _deep_merge(prof, updates or {})
        prof["name"] = name
        _write_json(p, prof)
    if activate:
        H["_state"]({"collection": name})
    return prof


# Profile keys that are about the user / this PC, not about a set of songs: they apply to every song. Everything else
# in a collection profile (style, ear_candy, finish, references, layers ...) is the collection's taste and applies only
# to songs that belong to it (profile_for).
USER_WIDE_KEYS = ("name", "fl_edition", "owned_plugins", "trial_plugins", "palette", "toolset", "access", "teaching",
                  "reload_mode", "sample_folders", "new_song_folder", "projects_root", "prefer", "prefer_sources",
                  "prefer_flex", "language")


def _pkey(p):
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(str(p))))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(str(p)))


def _inside(p, folder):
    f = _pkey(folder).rstrip("\\/")
    return p == f or p.startswith(f + os.sep)


def collection_of(project, name=None, journal=None):
    """(collection name or None, why) for a song: its journal's collection (a name, or '' = explicitly in none) wins;
    else it belongs to the active (or named) collection when the profile's songs list it (by project path, or by name
    when no path is recorded), when it sits in a listed song's own folder, or under the profile's 'folder'."""
    if not project and journal is None:
        return None, "no song"
    try:
        j = journal if isinstance(journal, dict) else journal_get(project)
    except Exception:
        j = {}
    jc = j.get("collection")
    if isinstance(jc, str) and jc.strip():
        return jc.strip(), "the song journal's collection"
    if jc is not None:                                   # '' / False: made outside any collection
        return None, "the song journal says it belongs to no collection"
    cname = profile_name(name)
    if not cname:
        return None, "no active collection"
    prof = profile_get(cname)
    if not prof:
        return None, "the collection has no profile"
    if not project:
        return None, "no song file"
    pk = _pkey(project)
    stem = os.path.splitext(os.path.basename(project))[0].strip().lower()
    songs = prof.get("songs") if isinstance(prof.get("songs"), dict) else {}
    for sname, sx in songs.items():
        sx = sx if isinstance(sx, dict) else {}
        sp = sx.get("project")
        if sp and _pkey(sp) == pk:
            return cname, "listed in the collection's songs"
        if not sp and str(sname).strip().lower() == stem:
            return cname, "listed in the collection's songs (by name)"
        sdir = os.path.dirname(_pkey(sp)) if sp else ""
        if sp and os.path.basename(sdir).lower() == os.path.splitext(os.path.basename(str(sp)))[0].strip().lower() \
                and _inside(pk, sdir):                   # the song's own folder (versions, bounces of it)
            return cname, "in the folder of a song of the collection"
    for k in ("folder", "songs_folder"):
        f = prof.get(k)
        if isinstance(f, str) and f.strip() and _inside(pk, f):
            return cname, f"under the collection's {k}"
    return None, "not one of the collection's songs"


def profile_for(project=None, name=None, journal=None):
    """The profile a tool should follow for this song: the whole collection profile when the song belongs to the
    collection (collection_of), else only its user-wide keys (edition, owned plugins, palette, accessibility ...):
    a collection's taste (style words, ear candy, finish, references) never leaks into unrelated songs."""
    cname, _why = collection_of(project, name, journal) if (project or journal is not None) else (None, "no song")
    if cname:
        prof = profile_get(cname)
        if prof:
            return prof
    base = profile_get(name)
    return {k: v for k, v in base.items() if k in USER_WIDE_KEYS}


EAR_CANDY_MODES = ("organic", "synthetic")           # the profile's 'ear_candy' (read by flh_compose._ec_settings)
EAR_CANDY_AMOUNTS = ("subtle", "normal", "lots")


def _ear_candy_ok(v):
    if v is False:
        return True
    if not isinstance(v, str):
        return False
    m, _, a = v.strip().lower().partition(":")
    return (m in EAR_CANDY_MODES and a in EAR_CANDY_AMOUNTS + ("",)) or (m in EAR_CANDY_AMOUNTS + ("off",) and not a)


FINISH_DEFAULT = {"lufs": -14.0, "true_peak": -1.0, "bits": 24}   # neutral: the common streaming reference


def song_target(profile, song_role):
    """Loudness/format target for a song from the profile: {"lufs", "true_peak", "bits"}; the profile's finish /
    finish_by_role win, else FINISH_DEFAULT (-14 LUFS integrated, -1 dBTP, 24-bit)."""
    t = {k: v for k, v in (profile.get("finish") or {}).items() if v is not None}
    for k, v in FINISH_DEFAULT.items():
        t.setdefault(k, v)
    by_role = (profile.get("finish_by_role") or {}).get(song_role or "")
    if by_role:
        t.update(by_role)
    return t


# ----------------------------------------------------------------------------- per-song journal
def journal_path(project):
    project = os.path.abspath(project)
    return os.path.join(os.path.dirname(project), os.path.splitext(os.path.basename(project))[0] + ".journal.json")


def _damaged(p):
    """None when the JSON file at p is missing or a readable object, else why it can't be read."""
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
    except ValueError as e:                              # (UnicodeDecodeError too)
        return f"{type(e).__name__}: {str(e)[:80]}"
    except OSError:
        return None
    return None if isinstance(d, dict) else "not a JSON object"


def _keep_damaged(p):
    """A damaged profile / journal is copied aside (<name>.damaged-<time>.json) before it's replaced, never lost."""
    if _damaged(p) is None:
        return None
    import shutil
    dst = f"{os.path.splitext(p)[0]}.damaged-{time.strftime('%Y%m%d-%H%M%S')}.json"
    try:
        shutil.copy2(p, dst)
        return dst
    except OSError:
        return None


def journal_get(project):
    p = journal_path(project)
    j = json.loads(json.dumps(DEFAULT_JOURNAL))
    bad = _damaged(p)
    if bad:                                              # a damaged journal never breaks every tool that reads it
        j["journal_damaged"] = (f"{os.path.basename(p)} can't be read ({bad}): starting from an empty journal; the "
                                "damaged file is copied aside (.damaged-<time>.json) when the journal is next written")
    elif os.path.isfile(p):
        with open(p, encoding="utf-8-sig") as f:
            _deep_merge(j, json.load(f))
    if not j.get("song"):
        j["song"] = os.path.splitext(os.path.basename(project))[0]
    return j


def journal_update(project, set=None, add=None):
    """set: deep-merged into the journal (None deletes a key). add: {list key: item or [items]} appended (no dups)."""
    p = journal_path(project)
    H["writable"](p)
    with _JLOCK:
        j = journal_get(project)
        if j.pop("journal_damaged", None):
            _keep_damaged(p)
        if set:
            _deep_merge(j, set)
        for k, items in (add or {}).items():
            lst = j.setdefault(k, [])
            for it in items if isinstance(items, list) else [items]:
                if it not in lst:
                    lst.append(it)
        j["updated"] = time.strftime("%Y-%m-%d %H:%M")
        _write_json(p, j)
    return j


def section_at(journal, bar):
    """The journal section holding `bar` (or None)."""
    secs = sorted(journal.get("sections") or [], key=lambda s: s["bar"])
    hit = None
    for i, s in enumerate(secs):
        end = s.get("end_bar") or (secs[i + 1]["bar"] if i + 1 < len(secs) else 1e9)
        if s["bar"] <= bar < end:
            hit = s
    return hit


def section_range(journal, name):
    """(start_bar, end_bar) of a section by name (case-insensitive prefix ok)."""
    secs = sorted(journal.get("sections") or [], key=lambda s: s["bar"])
    low = name.lower()
    for i, s in enumerate(secs):
        if s["name"].lower() == low or s["name"].lower().startswith(low):
            end = s.get("end_bar") or (secs[i + 1]["bar"] if i + 1 < len(secs) else None)
            return s["bar"], end
    raise ValueError(f"no section '{name}' in the journal (have: {[s['name'] for s in secs]})")


_GENERIC_PATTERN = re.compile(r"^\s*pattern\s*\d*\s*$", re.I)
_SECTION_WORDS = re.compile(r"\b(intro|verse|pre[- ]?chorus|chorus|hook|refrain|bridge|break(down)?|drop|build(up)?|"
                            r"outro|interlude|solo|middle|section|part|coda|tag|vamp|[AB]\s*\d*$)", re.I)


def sections_from_project(project):
    """Sections from the project's playlist markers or section pattern clips (for seeding a journal). One section
    per start bar: when several clips start on the same bar, a section-like name (Intro, Verse...) beats a named
    pattern, which beats a generic 'Pattern N'; then the longest clip, then the top track. Each name once (its first
    start). A section ends where the next begins (the last at its clip's end); none has zero length."""
    H["_load"](project)                                   # not a readable .flp: say so (as before)
    out = []
    try:
        pl = H["flp_playlist"](project)
        clips = [c for c in pl.get("clips", []) if c.get("kind", "pattern") == "pattern"]
        names = H["flp_patterns"](project)
        pats = {p.get("index"): p for p in (names.get("patterns") if isinstance(names, dict) else names) or []}
        by_bar = {}
        for c in clips:
            nm = c.get("name") or pats.get(c.get("pattern"), {}).get("name")
            if not nm or str(nm).strip().lower() == "tempo":
                continue
            by_bar.setdefault(round(float(c["bar"]), 3), []).append((str(nm), c))

        def rank(item):
            nm, c = item
            kind = 0 if _SECTION_WORDS.search(nm) and not _GENERIC_PATTERN.match(nm) else (
                2 if _GENERIC_PATTERN.match(nm) else 1)
            return (kind, -float(c.get("length_bars") or c.get("bars") or 0), int(c.get("track") or 0))
        used = set()
        for bar in sorted(by_bar):
            pick = next(((nm, c) for nm, c in sorted(by_bar[bar], key=rank) if nm not in used), None)
            if pick is None:
                continue
            nm, c = pick
            used.add(nm)
            ln = float(c.get("length_bars") or c.get("bars") or 0)
            out.append({"name": nm, "bar": c["bar"], "pattern": c.get("pattern"),
                        **({"end_bar": round(c["bar"] + ln, 3)} if ln > 0 else {})})
    except Exception:
        pass
    out.sort(key=lambda s: s["bar"])
    for a, b in zip(out, out[1:]):                       # a section ends where the next begins (clip tails overlap)
        a["end_bar"] = b["bar"]
    return [s for s in out if s.get("end_bar") is None or s["end_bar"] > s["bar"] + 1e-6]


# ----------------------------------------------------------------------------- tools
def flh_profile(name=None, set=None, activate=True):
    """Read (no set) or update the collection profile; the named one becomes active."""
    if set:
        if not (name or profile_name()):
            raise ValueError("give a name for the new collection profile")
        if isinstance(set, dict) and set.get("ear_candy") is not None and not _ear_candy_ok(set["ear_candy"]):
            raise ValueError("ear_candy: off | organic | subtle | normal | lots | synthetic (or 'organic:normal') - "
                             f"got {set['ear_candy']!r}")
        return profile_set(name or profile_name(), set, activate)
    if name and activate and profile_get(name):
        H["_state"]({"collection": name})
    LAST_REFUSED.update(name=None, why=None)
    prof = profile_get(name)
    if not prof:
        have = sorted(f[:-5] for f in os.listdir(_dir()) if f.endswith(".json")) if os.path.isdir(_dir()) else []
        r = {"profile": None, "active": profile_name(), "available": have,
             "note": "no profile yet: flh_profile(name, set={...})"}
        if LAST_REFUSED["why"]:
            r["note"] = (f"the collection name {LAST_REFUSED['name']!r} can't be read ({LAST_REFUSED['why']}); pick "
                         "one of 'available' or make a new one: flh_profile(name, set={...})")
        return r
    return prof


def _compact(j):
    """What a reader needs, not the bulky machine parts (full=true returns everything)."""
    j = dict(j)
    rv = j.get("review")
    if isinstance(rv, dict):
        j["review"] = {k: rv.get(k) for k in ("when", "mode", "issues", "top") if k in rv}
        if rv.get("song"):
            j["review"]["song"] = rv["song"]
    pf = j.get("performance")
    if isinstance(pf, dict) and pf:
        j["performance"] = {"sections": [s.get("name") for s in pf.get("sections") or []],
                            "parts": sorted((pf.get("parts") or {}).keys()), "source": pf.get("source"),
                            "note": "flp_perform edits it; full=true shows the spec"}
    j.pop("performance_applied", None)
    if j.get("performance_history"):
        j["performance_history"] = f"{len(j['performance_history'])} earlier spec(s) (flp_perform_undo)"
    else:
        j.pop("performance_history", None)
    return j


def flh_journal(project, set=None, add=None, seed=False, full=False):
    """A song's journal: sections, channel roles, likes/dislikes, performance settings, versions, final.
    seed: fill sections from the project's playlist when the journal has none."""
    j = journal_get(project)
    if seed and not j.get("sections"):
        set = dict(set or {}); set["sections"] = sections_from_project(project)
    taste = None; as_note = None
    if isinstance(add, str):                      # add='the choir is too loud' - a plain note (PC 2026-10-27: this
        t = add.strip()                           # was an error); a JSON object written as text is that object
        if t[:1] == "{":
            try:
                add = json.loads(t)
            except ValueError:
                add = {"notes": t}; as_note = "the text was stored as a note (add={notes: ...})"
        else:
            add = {"notes": t} if t else None
            as_note = "the text was stored as a note (add={notes: ...})" if t else None
    elif isinstance(add, list):                   # a list of plain texts: notes too
        if all(isinstance(x, str) for x in add):
            add = {"notes": [x.strip() for x in add if x.strip()]} or None
            as_note = "the texts were stored as notes"
        else:
            raise ValueError("add: {likes|dislikes|notes|versions|sound_likes|sound_dislikes: items}, or a plain "
                             "text (stored as a note)")
    if add is not None and not isinstance(add, dict):
        raise ValueError("add: {likes|dislikes|notes|versions|sound_likes|sound_dislikes: items}, or a plain text "
                         "(stored as a note)")
    if isinstance(add, dict) and ("sound_likes" in add or "sound_dislikes" in add):
        add = dict(add); taste = _sound_taste(j, add.pop("sound_likes", None), add.pop("sound_dislikes", None))
    if set or add:
        j = journal_update(project, set, add)
    j["journal_file"] = journal_path(project)
    out = j if full else _compact(j)
    if taste is not None:
        out["sound_taste"] = taste
    if as_note:
        out["added"] = as_note
    return out


def _sound_taste(j, likes, dislikes):
    """sound_likes / sound_dislikes: channel numbers or names (the journal knows their sound), or sound / file names
    -> remembered for every future song (liked sounds are picked first, disliked ones last)."""
    chans = j.get("channels") or {}

    def sounds(items):
        if items is None:
            return []
        out = []
        for it in (items if isinstance(items, (list, tuple)) else [items]):
            c = chans.get(str(it)) if isinstance(it, (int, str)) else None
            if c is None and isinstance(it, str):
                c = next((v for v in chans.values() if str(v.get("name") or "").lower() == it.strip().lower()), None)
            snd = (c or {}).get("sound") if c else it
            if snd:
                out.append(str(snd))
        return out
    note = H.get("taste_note")
    if not note:
        return "not available"
    return note(like=sounds(likes), dislike=sounds(dislikes), weight=2) or "nothing matched (give channel numbers)"


VERSION_DROP = ("final", "finals", "review", "rules_check", "versions", "finish_piece")   # the source's, not the copy's


def flp_save_version(project, name, folder=None, title=None):
    """Save a version of a song under a new name: the project is copied (with edits still queued for it, and FL's
    unsaved changes when FL has it open), the copy gets its own Project-info title (FL's window shows the title, so
    two files titled alike can't be told apart - PC 2026-10-23), and its journal is copied too: the song renamed,
    the source's final / review / versions dropped, 'version_of' and a note added. -> {path, title, song, journal}."""
    project = os.path.abspath(project)
    if not os.path.isfile(project) or not project.lower().endswith(".flp"):
        raise FileNotFoundError(f"project: an existing .flp ({project})")
    nm = str(name or "").strip()
    if not nm:
        raise ValueError("name: the version's name, e.g. 'My Song (Rework)' (or a .flp file name / path)")
    if nm.lower().endswith(".flp") or os.sep in nm or "/" in nm:
        dst = nm if os.path.isabs(nm) else os.path.join(folder or os.path.dirname(project), nm)
        if not dst.lower().endswith(".flp"):
            dst += ".flp"
    else:
        f = H.get("safe_file_name")
        stem = f(nm, "version name") if f else nm
        dst = os.path.join(folder or os.path.dirname(project), stem + ".flp")
    dst = os.path.abspath(dst)
    if os.path.normcase(dst) == os.path.normcase(project):
        raise ValueError("name: the version needs another file name than the project")
    if os.path.exists(dst):
        raise FileExistsError(f"{dst} exists already: pick another name (nothing was changed)")
    H["writable"](dst)
    song = os.path.splitext(os.path.basename(dst))[0]
    notes = []
    try:                                                 # edits queued for the project (lazy mode) go in first
        if H.get("_pending_get") and H["_pending_get"](project):
            H["_sync_before"]("save_version", project)
    except Exception as e:                               # noqa: BLE001 - said, the file on disk is copied
        notes.append(f"queued edits not applied first ({type(e).__name__}: {e})")
    try:                                                 # FL's unsaved changes of this very project: saved first
        if H["fl_running"]() and H["_open_state"](project) == "open" and H["_fl_changed_flag"]() not in (0, None) \
                and not H["_stale_get"](project):
            if H["_save_in_fl_and_wait"](project, 8.0):
                notes.append("FL had unsaved changes: saved into the project first, so the version has them")
    except Exception:                                    # noqa: BLE001
        pass
    with open(project, "rb") as f:
        d = f.read()
    k, t_old = H["_title_of_bytes"](d)
    if title is not None and str(title).strip():
        new_title = str(title).strip()
        if k is not None:
            ev = H["_events"](d)
            d = H["_splice"](d, ev, [(k, k + 1, H["_data_event"](H["PROJECT_TEXT"]["title"],
                                                                 new_title.encode("utf-16-le") + b"\x00\x00"))])
    else:
        d = H["_own_title_bytes"](d, project, dst)
        new_title = H["_title_of_bytes"](d)[1]
    H["_save"](d, dst)
    if title is not None and str(title).strip() and k is None:
        try:
            H["flp_project_info"](dst, output=dst, title=new_title)
        except Exception as e:                           # noqa: BLE001
            notes.append(f"title not set ({type(e).__name__}: {e})")
    out = {"path": dst, "song": song, "version_of": project, "title": new_title or None}
    if t_old and new_title and t_old.strip().lower() == new_title.strip().lower():
        out["title_warning"] = (f"the version kept the title '{t_old}' (a custom title): FL's window shows titles, so "
                                f"it can't tell the two apart - flp_project_info(path={dst!r}, output={dst!r}, "
                                f"title={song!r}) gives it its own")
    j = journal_get(project) if os.path.isfile(journal_path(project)) else None
    if j is not None:
        j = {k_: v for k_, v in j.items() if k_ not in VERSION_DROP and k_ not in ("updated", "journal_damaged")}
        j["song"] = song
        j["version_of"] = project
        j["notes"] = list(j.get("notes") or []) + [
            f"version of {os.path.basename(project)} (saved {time.strftime('%Y-%m-%d %H:%M')}); its finals and review "
            "stay with it"]
        jp = journal_path(dst)
        H["writable"](jp)
        j["updated"] = time.strftime("%Y-%m-%d %H:%M")
        with _JLOCK:
            _write_json(jp, j)
        out["journal"] = jp
    else:
        journal_update(dst, set={"song": song, "version_of": project,
                                 "notes": [f"version of {os.path.basename(project)}"]})
        out["journal"] = journal_path(dst)
    try:                                                 # the version loads like its source: the same load cost
        lc = H["project_load_cost"](project).get("seconds")
        if lc:
            out["load_cost_s"] = lc
    except Exception:                                    # noqa: BLE001
        pass
    if notes:
        out["notes"] = notes
    out["next"] = f"fl_open({dst!r}) to work on the version (the source stays as it was)"
    return out


def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    ns["TOOLS"]["flp_save_version"] = (
        flp_save_version, "Save a version of a song under a new name (e.g. a rework): the .flp copied (queued edits "
        "and FL's unsaved changes included), its own Project-info title (FL's window shows titles: twins can't be told "
        "apart), its journal copied with the song renamed, the source's final/review dropped and a note added. "
        "Returns the new path.",
        S(project=ns["_FLP"], name=ns["_STR"]("the version's name ('My Song (Rework)') or a .flp file name / path"),
          folder=ns["_STR"]("folder for it (default: the project's folder)", True),
          title=ns["_STR"]("its Project-info title (default: from its name, keeping a suffix like ' (Piano)')", True)))
    ns.setdefault("TOOL_GROUPS", {})["flp_save_version"] = "compose"
    ns["TOOLS"]["flh_profile"] = (
        flh_profile, "The collection profile: house rules every tool follows (FL edition, owned/trial plugins, palette, "
        "loudness by song role, final folder/name pattern, bit depth, style text, ear_candy: off | organic | subtle | "
        "normal | lots | synthetic for every arrangement). Read it at the start of a session; "
        "set={...} to change. No profile yet: flh_setup(action='apply') makes a neutral one.",
        S(name=ns["_STR"]("collection name (default: the active one)", True),
          set=P("object", "fields to merge in (None deletes)", True),
          activate=ns["_BOOL"]("make it the active collection (default true)", True)))
    ns["TOOLS"]["flh_journal"] = (
        flh_journal, "A song's memory, kept next to the project: sections, channel roles, likes/dislikes, "
        "performance settings, versions, final. READ IT before working on a song; add what the user liked or "
        "disliked (add={dislikes: '...'}) so no session has to re-learn it.",
        S(project=ns["_FLP"], set=P("object", "fields to merge in (None deletes)", True),
          add=P(["object", "string", "array"], "{likes|dislikes|notes|versions|sound_likes|sound_dislikes: items}; "
                "a plain text (or a list of texts) is stored as a note", True, items={"type": "string"}),
          seed=ns["_BOOL"]("fill sections from the playlist if empty", True),
          full=ns["_BOOL"]("include the full review table and performance spec", True)))
