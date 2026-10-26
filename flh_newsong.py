"""fl-headless "first five minutes": a ready-to-play new song from a genre recipe, samples into sampler channels,
the user's own templates, and a short welcome.

    flh_new_song(genre, bpm, key, name, folder, vibe, kit, template, starter, variations, open)
        A NEW project (never an existing one): FL's own empty template (or a saved user template) + the genre recipe:
        channels with real sounds (FL's samples for drums, owned instruments for the music - the user's edition
        only, substitutions reported), each on a named, colored mixer insert, routed to Drums / Music / Vocals
        buses, Reverb + Delay send tracks, an EQ + limiter on the master, playlist tracks named by role, tempo,
        the journal (genre, key, bpm, sections, roles), optionally a starter beat (flh_compose) laid out in the
        genre's form, then opened in FL.
    flp_load_sample(project, output, sample, channel, name)   a sample into a new or existing sampler channel
    flh_save_template(project, name, keep_notes, fl_templates)  "save this as my template" (recipe + project copy)
    flh_welcome()                                              first-use message: setup state, examples, promises
    flh_icon_probe(output, start, count)                       PC-VERIFY helper to learn FL's channel icon numbers

Recipes are data: data/templates/<genre>.json ("flh-template/1") on top of data/templates/_defaults.json, drum kits
in data/kits/*.json ("flh-kit/1"); users can add/override files in <fl-headless state folder>/templates/ and
<state>/kits/ (first file wins per name, user folder first).

Recipe ("flh-template/1"): genre, name, about, tempo (null = the genre's default), key (null = genre's), kit,
  channels [{role, name, family?, sample? (sample kind for sampler roles), query? (sample words), group? (inserts
  merge by group when the mixer is short), choices? ([{sample} | {labs: kind} | {plugin, presets?: [globs]} |
  {flex: kind}], each may carry query (character words) / avoid; first owned + available wins; a plugin the FL
  edition / profile doesn't own - a demo - is skipped and said so; last resorts are kept back while a real sound is
  left: a General MIDI style FLEX preset (data/flex/quality.json tier 0), a synth's init patch when its guessed
  preset names miss, FLEX's bare default sound), character? ({query, avoid}: the part's sound for every choice that
  has no own words), level_db?, color?, bus?, notes?}],
  plus any _defaults key: palette, levels_db, buses, sends, master, playlist, playlist_names, choices.
Kit ("flh-kit/1"): name, about, roles {role: sample query words}, avoid [tags].

Saved user templates: <state>/templates/<name>/<name>.flp + template.json ("flh-user-template/1": name, genre, bpm,
key, channels [{index, name, role, type, plugin, sample}], sections, compose registry, saved, from).

File facts used (from FL 2026 projects; PC-VERIFY items are in checklists/newsong.md): channel type event 21
(0 sampler, 2 plugin, 4 audio clip, 5 automation), sample path 196 (FL writes factory samples as
%FLStudioFactoryData%\\...), channel mixer insert 104 (int16), channel filter group 145, playlist track data 238
(+ name 239), playlist markers 148 (position ticks) + 205 (name) inside the arrangement.
"""
import copy
import glob
import contextlib
import json
import os
import re
import shutil
import struct
import sys
import time

import flh_store

H = {}                                                   # the server namespace, set by register()
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
EV_TYPE, EV_INSERT, EV_GROUP, EV_FLAGS143, EV_TRACK, EV_TRACK_NAME = 21, 104, 145, 143, 238, 239
EV_MARKER_POS, EV_MARKER_NAME, EV_ARRANGEMENT, EV_ARR_NAME = 148, 205, 99, 241
EV_PATTERN_COLOR = 150
DRUM_ROLES = ("kick", "snare", "clap", "rim", "hat", "openhat", "ride", "crash", "perc", "shaker", "tom")
SAMPLER_ROLES = DRUM_ROLES + ("808", "fx", "texture", "vocal")
COMPOSE_ORDER = ["drums", "bass", "chords", "melody", "counter"]   # flp_arrange_song lays tracks in this order


def _mod(name):
    m = sys.modules.get(name)
    if m is None:
        import importlib
        m = importlib.import_module(name)
    return m


# ============================================================================= data
_CACHE = {}


def _data_dirs(sub):
    dirs = []
    try:
        if H.get("STATE"):
            dirs.append(os.path.join(os.path.dirname(H["STATE"]), sub))
    except Exception:
        pass
    dirs.append(os.path.join(DATA, sub))
    return dirs


def _json_files(sub):
    out = {}
    for d in _data_dirs(sub):
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            n = os.path.splitext(os.path.basename(f))[0]
            out.setdefault(n, f)
    return out


def _read_json(path):
    sig = (path, os.path.getmtime(path))
    hit = _CACHE.get(sig)
    if hit is None:
        with open(path, encoding="utf-8") as f:
            hit = json.load(f)
        _CACHE[sig] = hit
    return copy.deepcopy(hit)


def defaults():
    f = _json_files("templates").get("_defaults")
    return _read_json(f) if f else {}


def recipes():
    """{genre: recipe file} (user folder first)."""
    return {k: v for k, v in _json_files("templates").items() if not k.startswith("_")}


def kits():
    return _json_files("kits")


def recipe(genre):
    """The genre's recipe merged over _defaults (a genre without a recipe file gets one built from its genre data)."""
    base = defaults()
    files = recipes()
    g = _mod("flh_compose").genre_data(genre) if genre else None
    name = (g or {}).get("name") or genre
    f = files.get(name) or files.get(str(genre or "").lower())
    r = _read_json(f) if f else _recipe_from_genre(g)
    for k, v in base.items():
        if k not in r:
            r[k] = v
        elif isinstance(v, dict) and isinstance(r[k], dict) and k in ("palette", "levels_db", "choices", "playlist_names"):
            merged = dict(v); merged.update(r[k]); r[k] = merged
    r["_file"] = f or "(built from the genre data)"
    return r, g


def _recipe_from_genre(g):
    """A plain recipe for a genre file that has no template (community genres): its drum parts + bass/chords/melody."""
    if not g:
        raise ValueError("unknown genre (flh_genres lists them)")
    parts = [p for p in ((g.get("drums") or {}).get("parts") or {}) if p in DRUM_ROLES]
    names = {"openhat": "Open Hat"}
    ch = [{"role": p, "name": names.get(p, p.title())} for p in parts]
    bt = (g.get("bass") or {}).get("type")
    ch.append({"role": "bass", "name": "808" if bt == "808" else "Bass", "family": "808" if bt == "808" else "bass",
               **({"sample": "808"} if bt == "808" else {})})
    ch += [{"role": "chords", "name": "Keys", "family": "keys"}, {"role": "melody", "name": "Lead", "family": "lead"}]
    return {"format": "flh-template/1", "genre": g["name"], "name": g["name"] + " starter", "channels": ch}


def _kit(name):
    f = kits().get(str(name or "").lower())
    return _read_json(f) if f else None


# ============================================================================= FLP helpers (bytes in, bytes out)
def _enc(eid, payload):
    return H["_data_event"](eid, payload) if eid >= 192 else bytes([eid]) + payload


def _txt(s):
    return str(s).encode("utf-16-le") + b"\x00\x00"


def _rgb(color):
    c = H["_hexcolor"](color)
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def _chan_starts(d, ev):
    return [(k, struct.unpack("<H", H["_payload"](d, ev[k])[:2])[0]) for k, e in enumerate(ev) if e["id"] == 64]


def _chan_range(d, ev, ch):
    """(k0 = its ChannelNew event, k_end = its last event (inclusive): the closing 51, or - for a channel FL saved
    without one (the sampler in FL's empty template) - the event before the arrangement / pattern / mixer data."""
    for kind, idx, a, b in H["_blocks"](d, ev):
        if kind == "channel" and idx == int(ch):
            return a, b - 1
    raise ValueError(f"no channel {ch}")


def _close_block(lst):
    """A channel block list that ends the way FL 2026 ends every channel: ... 170, 51."""
    if not any(it[0] == H["EV_CHANNEL_END"] for it in lst):
        if not any(it[0] == 170 for it in lst):
            lst.append([170, b"\xff\xff\xff\xff"])
        lst.append([H["EV_CHANNEL_END"], b"\x00"])
    return lst


def _structure(d):
    """Counts of the project-level sections a channel edit must never copy: arrangements, mixer tracks, playlists."""
    ev = H["_events"](d)
    return {n: sum(1 for e in ev if e["id"] == i) for n, i in
            (("arrangements", EV_ARRANGEMENT), ("mixer tracks", H["EV_MIXER_TRACK"]), ("playlists", H["EV_PLAYLIST"]))}


def _chan_types(d, ev):
    return H["_channel_types"](d, ev)


def _block_list(raw):
    return [[eid, bytes(p)] for eid, p, _ in H["_raw_events"](raw)]


def _block_bytes(lst):
    return b"".join(_enc(eid, p) for eid, p in lst)


def _lst_set(lst, eid, payload, before=(170, 51)):
    """Replace the first event `eid` of a block list, or insert it before the first of `before`."""
    for it in lst:
        if it[0] == eid:
            it[1] = payload
            return lst
    at = next((i for i, it in enumerate(lst) if it[0] in before), len(lst))
    lst.insert(at, [eid, payload])
    return lst


def _groups(d, ev):
    """Channel filter group names in order (event 231)."""
    return [H["_text16"](H["_payload"](d, e)) for e in ev if e["id"] == 231]


def _unsorted_group(d, ev):
    gs = [g.lower() for g in _groups(d, ev)]
    for want in ("unsorted", "all"):
        if want in gs:
            return gs.index(want)
    return None


def _append_channel(d, block):
    """Add a channel (block = its events after ChannelNew) after the last one -> (data, new index). A project with
    no channels gets it before the arrangement (PC-VERIFY: FL's empty template)."""
    # PC-VERIFY: the first channel of a channel-less project, put before the arrangement events, loads in FL
    ev = H["_events"](d)
    starts = _chan_starts(d, ev)
    if starts:
        new_index = max(i for _, i in starts) + 1
        _, kend = _chan_range(d, ev, starts[-1][1])
        at = kend + 1
    else:
        new_index = 0
        at = next((k for k, e in enumerate(ev) if e["id"] in (EV_ARRANGEMENT, H["EV_PLAYLIST"], EV_TRACK,
                                                               H["EV_MIXER_TRACK"])), len(ev))
    new = bytearray(H["_splice"](d, ev, [(at, at, bytes([64]) + struct.pack("<H", new_index) + block)]))
    fmt, nch, ppq = struct.unpack("<hHH", new[8:14])
    new[8:14] = struct.pack("<hHH", fmt, nch + 1, ppq)
    return bytes(new), new_index


def _stored_sample_path(path):
    """How FL itself stores a sample path: factory samples relative to %FLStudioFactoryData%."""
    # PC-VERIFY: FL finds a factory sample stored as %FLStudioFactoryData%\Data\Patches\Packs\... (as it writes it)
    p = os.path.abspath(path)
    try:
        fl_dir = os.path.dirname(H["fl_exe"]())
    except Exception:
        fl_dir = None
    if fl_dir and os.path.normcase(p).startswith(os.path.normcase(os.path.join(fl_dir, "Data")) + os.sep):
        return "%FLStudioFactoryData%" + p[len(fl_dir):]
    return p


def _sampler_block(d, ev, sample, name, color=None, insert=None, src=None):
    """Events of a sampler channel holding `sample`: a copy of an existing sampler channel (src) when the project has
    one, else FL's own audio-clip channel block (fl_blocks) turned into a sampler (PC-VERIFY)."""
    # PC-VERIFY: a channel from fl_blocks.AUDIO_BLOCK with type 0 is a working sampler that plays its sample per note
    import fl_blocks
    if src is not None:
        k0, kend = _chan_range(d, ev, src)
        lst = _close_block(_block_list(d[ev[k0]["end"]:ev[kend]["end"]]))
    else:
        lst = _block_list(fl_blocks.AUDIO_BLOCK)
        _lst_set(lst, EV_TYPE, b"\x00")
        _lst_set(lst, H["EV_CHANNEL_ICON"], struct.pack("<I", 0))
        grp = _unsorted_group(d, ev)
        if grp is not None:
            _lst_set(lst, EV_GROUP, struct.pack("<I", grp))
    if sample:
        _lst_set(lst, H["EV_SAMPLE_PATH"], _txt(_stored_sample_path(sample)))
    else:
        lst[:] = [it for it in lst if it[0] != H["EV_SAMPLE_PATH"]]
    _lst_set(lst, H["EV_DISPLAY_NAME"], _txt(name))
    if color:
        r, g, b = _rgb(color)
        _lst_set(lst, H["EV_CHANNEL_COLOR"], bytes([r, g, b, 0]))
    if insert is not None:
        _lst_set(lst, EV_INSERT, struct.pack("<h", int(insert)))
    return _block_bytes(lst)


def _plugin_block(d, ev, fst, name, color=None, insert=None):
    """Events of a generator channel with the plugin (and its settings) from a .fst preset."""
    import fl_blocks
    blk, plug = H["_fst_block"](fst)
    base = _block_list(fl_blocks.AUDIO_BLOCK)
    fst_ev = [[eid, bytes(p)] for eid, p, _ in blk]
    state = next((it for it in fst_ev if it[0] == H["EV_PLUGIN_STATE"]), None)
    head = [it for it in fst_ev if it[0] not in (H["EV_DISPLAY_NAME"], H["EV_CHANNEL_ICON"], H["EV_CHANNEL_COLOR"], 41,
                                                 H["EV_PLUGIN_STATE"])]
    keep = {it[0]: it for it in base}
    r, g, b = _rgb(color) if color else (0x23, 0x23, 0x1F)
    lst = [[EV_TYPE, b"\x02"]] + head + [[H["EV_DISPLAY_NAME"], _txt(name)], [H["EV_CHANNEL_ICON"], struct.pack("<I", 0)],
                                         [H["EV_CHANNEL_COLOR"], bytes([r, g, b, 0])], list(keep.get(41, [41, b"\x01"]))]
    if state:
        lst.append(state)
    after41 = False
    for it in base:                                      # the channel's own settings, as FL writes them
        if after41 and it[0] != H["EV_SAMPLE_PATH"]:
            lst.append(list(it))
        if it[0] == 41:
            after41 = True
    _lst_set(lst, EV_FLAGS143, struct.pack("<I", 0x0A))  # what FL writes for plugin channels
    grp = _unsorted_group(d, ev)
    if grp is not None:
        _lst_set(lst, EV_GROUP, struct.pack("<I", grp))
    if insert is not None:
        _lst_set(lst, EV_INSERT, struct.pack("<h", int(insert)))
    return _block_bytes(lst), plug


def _chan_edit(d, ch, sets):
    """Set events in one channel's block: sets {event id: payload} (inserted before its end when missing)."""
    # PC-VERIFY: event 104 (int16) is the channel's mixer insert (FL 2026 fixture: instruments 3..6, clips 0)
    ev = H["_events"](d)
    k0, kend = _chan_range(d, ev, ch)
    lst = _close_block(_block_list(d[ev[k0]["end"]:ev[kend]["end"]]))
    for eid, p in sets.items():
        _lst_set(lst, eid, p)
    return H["_splice"](d, ev, [(k0 + 1, kend + 1, _block_bytes(lst))])


def _ensure_playlist(d):
    """A playlist event to put clips in (FL's empty template may have none; PC-VERIFY)."""
    # PC-VERIFY: an empty playlist event after the arrangement name is accepted by FL
    ev = H["_events"](d)
    if any(e["id"] == H["EV_PLAYLIST"] for e in ev):
        return d
    ka = next((k for k, e in enumerate(ev) if e["id"] == EV_ARRANGEMENT), None)
    if ka is None:
        at = next((k for k, e in enumerate(ev) if e["id"] in (EV_TRACK, H["EV_MIXER_TRACK"])), len(ev))
        new = _enc(EV_ARRANGEMENT, struct.pack("<H", 0)) + _enc(EV_ARR_NAME, _txt("Arrangement")) + \
            _enc(H["EV_PLAYLIST"], b"")
        return H["_splice"](d, ev, [(at, at, new)])
    at = ka + 1
    while at < len(ev) and ev[at]["id"] in (EV_ARR_NAME, 36):
        at += 1
    return H["_splice"](d, ev, [(at, at, _enc(H["EV_PLAYLIST"], b""))])


def _pattern_metas(d, ev):
    META = H["PATTERN_META"]
    return [k for k, e in enumerate(ev) if e["id"] == H["EV_PATTERN_NEW"] and k + 1 < len(ev) and ev[k + 1]["id"] in META]


def _new_pattern(d, name, color=None):
    """A new named (colored) empty pattern -> (data, id). Works in a project with no named pattern yet."""
    ev = H["_events"](d)
    pats = H["_patterns"](d, ev)
    pid = max(pats, default=0) + 1
    meta = struct.pack("<BH", H["EV_PATTERN_NEW"], pid) + _enc(H["EV_PATTERN_NAME"], _txt(name))
    if color:
        r, g, b = _rgb(color)
        meta += bytes([EV_PATTERN_COLOR, r, g, b, 0])
    metas = _pattern_metas(d, ev)
    if metas:
        j = metas[-1] + 1
        while j < len(ev) and ev[j]["id"] in H["PATTERN_META"]:
            j += 1
    else:                                                 # first pattern name in a blank project: where FL 2026
        j = H["_pattern_meta_anchor"](d, ev)              # keeps them (after the first channel's block)
    d = H["_splice"](d, ev, [(j, j, meta)])
    d = H["_write_pattern_notes"](d, H["_events"](d), pid, [])
    return d, pid


def _place_pattern(d, pid, bar, bars, track):
    P = H["_ppq"](d)
    pos, ln = int(round((float(bar) - 1) * 4 * P)), int(round(float(bars) * 4 * P))
    try:
        return H["_place_pattern"](d, pid, pos, ln, track)
    except ValueError as e:
        if "copy the clip format" not in str(e):
            raise
    import fl_blocks
    ev = H["_events"](d)
    CR = H["CLIP_RECORD"]
    k = next(k for k, e in enumerate(ev) if e["id"] == H["EV_PLAYLIST"] and e["len"] % CR == 0)
    p = H["_payload"](d, ev[k])
    r = bytearray(fl_blocks.AUTOMATION_CLIP)              # FL's own clip record (PC-VERIFY as a pattern clip)
    struct.pack_into("<I", r, 0, pos)
    struct.pack_into("<H", r, 6, H["PATTERN_BASE"] + pid)
    struct.pack_into("<I", r, 8, ln)
    struct.pack_into("<H", r, 12, 499 - (int(track) - 1))
    struct.pack_into("<II", r, 24, 0, ln)
    ids = [struct.unpack("<I", p[i + 32:i + 36])[0] for i in range(0, len(p), CR)]
    struct.pack_into("<I", r, 32, max(ids, default=0) + 1)
    recs = sorted([p[i:i + CR] for i in range(0, len(p), CR)] + [bytes(r)], key=lambda x: struct.unpack("<I", x[:4])[0])
    return H["_splice"](d, ev, [(k, k + 1, H["_data_event"](H["EV_PLAYLIST"], b"".join(recs)))])


def _style_tracks(d, specs):
    """Playlist track names/colors: specs [{track (1-based), name, color}] (track data 238 + name 239 in FL's order
    238 43 239: an existing name is replaced in place, a new one goes at the end of the track's group)."""
    # PC-VERIFY: color bytes 4..7 R,G,B,0 like channel colors (PC 2026-10-23: FL 2026 saved every track back with
    # 17 15 13 00, the names kept) - newsong.playlist_names
    ev = H["_events"](d)
    want = {int(s["track"]): s for s in specs}
    edits, n = [], 0
    arrs = H["_arrangements"](d, ev) or [None]
    for a in arrs:
        groups = H["_track_groups"](d, ev, a["index"]) if a else {}
        for t, g in groups.items():
            s = want.get(t)
            if not s:
                continue
            k = g["k"]; n += 1
            if s.get("color"):
                p = bytearray(H["_payload"](d, ev[k]))
                r, gg, b = _rgb(s["color"])
                p[4:8] = bytes([r, gg, b, 0])
                edits.append((k, k + 1, H["_data_event"](EV_TRACK, bytes(p))))
            if s.get("name"):
                nm = H["_data_event"](EV_TRACK_NAME, _txt(s["name"]))
                if g["name_k"] is not None:
                    edits.append((g["name_k"], g["name_k"] + 1, nm))
                else:
                    edits.append((g["end"], g["end"], nm))
    return H["_splice"](d, ev, edits) if edits else d, n


def _markers(d, marks):
    """Playlist markers [(bar, name)] in the current arrangement, where FL reads them (H['_marker_slot']: after the
    playlist event and its track groups; before the playlist event FL took them as the last pattern's markers)."""
    if not marks:
        return d, 0
    ev = H["_events"](d)
    P = H["_ppq"](d)
    at = H["_marker_slot"](d, ev)
    if at is None:
        return d, 0
    new = b"".join(H["_marker_event"](int(round((float(bar) - 1) * 4 * P)), name) for bar, name in marks)
    return H["_splice"](d, ev, [(at, at, new)]), len(marks)


def _mixer_tracks(d):
    ev = H["_events"](d)
    return [H["_payload"](d, ev[k]) for k, e in enumerate(ev) if e["id"] == H["EV_MIXER_TRACK"]]


def _insert_capacity(d):
    """Inserts a recipe may use: 1..n-1, minus FL's last 'current' track (stored like the master; PC-VERIFY)."""
    # PC-VERIFY: how many inserts FL's empty template stores, and that the last one (flags 0x0c) is 'Current'
    tr = _mixer_tracks(d)
    n = len(tr)
    if n >= 3 and len(tr[-1]) > 4 and not tr[-1][4] & 0x40 and tr[1][4] & 0x40:
        n -= 1
    return max(0, n - 1)


LIMITERS = ("limiter", "maximus", "emphasizer", "maximizer", "soundgoodizer")


def _is_limiter(name):
    return any(w in str(name or "").lower() for w in LIMITERS)


def _effects(d):
    try:
        _, _, fx = H["_structure"](d)
    except Exception:
        return {}
    return fx


# ============================================================================= sounds: presets, ownership
_PRESETS = {}


def _preset_index():
    """{normalized plugin name: {"default": .fst, "presets": [paths]}} from FL's plugin database (default state of each
    installed generator/effect) and 'Plugin presets' folders. Cached for 10 minutes."""
    if _PRESETS.get("t", 0) > time.time() - 600:
        return _PRESETS["idx"]
    C = _mod("flh_catalog")
    idx = {}

    def add(name, path, default):
        e = idx.setdefault(C._norm(name), {"name": name, "default": None, "presets": []})
        if default:
            if not e["default"] or "installed" in path.lower():
                e["default"] = path
        elif len(e["presets"]) < 3000:
            e["presets"].append(path)
    for r in C._plugin_db_roots():
        for root, _, files in os.walk(r):
            for f in files:
                if f.lower().endswith(".fst"):
                    nfo = os.path.join(root, f[:-4] + ".nfo")
                    add((C._nfo_name(nfo) if os.path.isfile(nfo) else None) or f[:-4], os.path.join(root, f), True)
    roots = [os.path.join(u, sub) for u in C._user_dirs() for sub in ("Presets", os.path.join("FL Studio", "Presets"))]
    try:
        roots.append(os.path.join(os.path.dirname(H["fl_exe"]()), "Data", "Patches"))
    except Exception:
        pass
    for r in [x for x in dict.fromkeys(roots) if os.path.isdir(x)]:
        cands = [r] if os.path.basename(r).lower() == "plugin presets" else \
            [os.path.join(r, "Plugin presets")] + glob.glob(os.path.join(r, "*", "Plugin presets"))
        for base in [c for c in cands if os.path.isdir(c)][:4]:
            for kind in ("Generators", "Effects"):
                kd = os.path.join(base, kind)
                if not os.path.isdir(kd):
                    continue
                for plug in os.listdir(kd):
                    pd = os.path.join(kd, plug)
                    if not os.path.isdir(pd):
                        continue
                    for root, _, files in os.walk(pd):
                        for f in files:
                            if f.lower().endswith(".fst"):
                                add(plug, os.path.join(root, f), False)
    _PRESETS.update(t=time.time(), idx=idx)
    return idx


_FST_OK = {}


def _fst_ok(path):
    """Can this .fst be loaded onto a channel/slot (has a plugin block)? Cached per file."""
    if path not in _FST_OK:
        try:
            H["_fst_block"](path); _FST_OK[path] = True
        except Exception:
            _FST_OK[path] = False
    return _FST_OK[path]


def _find_plugin(name):
    return _preset_index().get(_mod("flh_catalog")._norm(name))


def _owned(plugin):
    try:
        return H["sound_ok"](plugin)
    except Exception:
        return "unknown"


def _own_why(plugin):
    """The short reason a plugin can't be used ('needs All Plugins Edition', 'the collection profile lists it as
    trial'), '' when there's nothing short to say."""
    try:
        why = str(H["plugin_ownership"](plugin).get("why") or "")
        C = _mod("flh_catalog")
        need = C._native_min(plugin)
        if need and not why.startswith("the collection profile"):
            return f"needs {C.EDITION_NAMES[need]}"
        return why if len(why) <= 70 else ""
    except Exception:
        return ""


def _glob_pick(paths, patterns, kind=None, query=None, avoid=None):
    """The first pattern with a match wins; among its matches the best-named preset (quality words for the kind and
    the part's character words up, 'Init' / 'Default' and avoided words down), then by name."""
    import fnmatch
    for pat in patterns or []:
        pat = pat.lower()
        hits = sorted(p for p in paths if fnmatch.fnmatch(os.path.basename(p).lower()[:-4], pat)
                      or fnmatch.fnmatch(p.lower().replace("\\", "/"), "*/" + pat.strip("*") + "*"))
        if hits:
            if len(hits) > 1:
                hits.sort(key=lambda p: (-_name_quality(os.path.splitext(os.path.basename(p))[0], kind, query,
                                                        avoid), p.lower()))
            return hits[0]
    return None


def _wordlist(x):
    if not x:
        return []
    if isinstance(x, (list, tuple)):
        x = " ".join(str(v) for v in x)
    return [w for w in re.findall(r"[a-z0-9]+", str(x).lower()) if len(w) > 1]


def _name_quality(name, kind=None, query=None, avoid=None):
    """How good a sound's name sounds for its kind (data/flex/quality.json words) plus its character words."""
    try:                                                 # asked-for words are never 'bad' ('farfisa' when asked)
        s = _mod("flh_flex").name_quality(name, kind, asked=_wordlist(query))
    except Exception:
        try:                                             # an older flh_flex without asked=
            s = _mod("flh_flex").name_quality(name, kind)
        except Exception:
            s = 0.0
    low = " " + " ".join(re.findall(r"[a-z0-9]+", str(name or "").lower())) + " "
    s += sum(1.0 for w in _wordlist(query) if f" {w} " in low)
    s -= sum(3.0 for a in avoid or [] if " " + " ".join(_wordlist(a)) + " " in low and _wordlist(a))
    return s


def _init_patch(plugin):
    """A synth whose default patch is a raw init sound (quality.json init_patch_plugins)."""
    try:
        names = _mod("flh_flex").quality_data().get("init_patch_plugins") or []
    except Exception:
        names = ["3x Osc", "Sytrus"]
    C = _mod("flh_catalog")
    return C._norm(plugin) in {C._norm(n) for n in names}


def _kind_fits(plugin, family):
    """Does a plugin's own default sound fit the part (FL Keys = piano: keys yes, organ no)? Unknown = yes."""
    C = _mod("flh_catalog")
    k = (C.STOCK_N.get(C._norm(plugin)) or (None,))[0]
    if not k or k == "synth":
        return True
    try:
        kn = _mod("flh_flex").kind_name
    except Exception:
        kn = lambda x: x
    return kn(k) == kn(FLEX_KIND.get(family) or family)


def _catalog_pick(kind, source, words, avoid=None):
    """Best owned catalog entry (LABS pack or FLEX preset) of a kind (avoided words pushed down)."""
    try:
        r = H["TOOLS"]["fl_sound_catalog"][0](query=" ".join(words) or None, kind=kind, limit=40, type="instrument")
    except Exception:
        return None
    ok = lambda x: x.get("source") == source and x.get("owned") is True and not x.get("may_load_silent") and \
        not is_silent(*_catalog_key(x))
    hits = _by_taste(_avoid_last([x for x in r.get("results") or [] if ok(x)], avoid))
    if not hits and words:
        try:
            r = H["TOOLS"]["fl_sound_catalog"][0](kind=kind, limit=40, type="instrument")
            hits = _by_taste(_avoid_last([x for x in r.get("results") or [] if ok(x)], avoid))
        except Exception:
            hits = []
    return hits[0] if hits else None


def _catalog_key(x):
    """(source, pack, preset) of a LABS / FL instruments catalog entry, as silent_mark keys it."""
    ld = x.get("load") or {}
    if x.get("source") == "LABS":
        return "LABS", ld.get("pack") or x.get("pack"), ld.get("preset") or ""
    return "DirectWave", "", _stem(ld.get("preset")) or x.get("name")


def _stem(path):
    return os.path.splitext(os.path.basename(str(path or "").replace("\\", "/")))[0] if path else ""


# ============================================================================= sounds that made no sound on this PC
# Some presets the indexes list as owned play nothing (a FLEX preset whose content is missing / broken: PC 2026-10-23
# 'Haunted Church Organ'). The level balance finds them (a part with notes measuring silent alone) and they are kept
# per PC in state.json 'silent_sounds' {"source|pack|preset": when}, skipped by every instrument pick from then on.
# Instruments / presets only: a drum sample's silence is another matter (a missing file).
SILENT_STATE = "silent_sounds"
SILENT_SWAPS = 2                                          # swaps tried for a silent part (each = one more export)
_SWAP_AVOID = {}


def silent_key(source, pack, preset):
    return "|".join(str(x or "").strip() for x in (source, pack, preset))


def silent_sound_keys():
    """Lower-cased silent_sounds keys (an empty set when there is no state)."""
    try:
        return {str(k).strip().lower() for k in (H["_state_get"](SILENT_STATE) or {})}
    except Exception:
        return set()


def is_silent(source, pack, preset):
    return silent_key(source, pack, preset).lower() in silent_sound_keys() if (source and (pack or preset)) else False


def _silent_edit(fn):
    lock = H.get("_STATE_LOCK") or contextlib.nullcontext()
    with lock:
        cur = dict(H["_state"]().get(SILENT_STATE) or {})
        out = fn(cur)
        H["_state"]({SILENT_STATE: cur})
    return out


def silent_mark(source, pack, preset):
    """Remember a sound that made no sound on this PC (skipped by instrument picks from now on)."""
    k = silent_key(source, pack, preset)
    try:
        _silent_edit(lambda cur: cur.__setitem__(k, time.strftime("%Y-%m-%d %H:%M")))
    except Exception:                                     # noqa: BLE001 - the swap itself still happens
        pass
    return k


def silent_unmark(keys):
    low = {str(k).strip().lower() for k in keys}
    try:
        _silent_edit(lambda cur: [cur.pop(k) for k in list(cur) if k.strip().lower() in low])
    except Exception:                                     # noqa: BLE001
        pass


def silent_sounds(action="list", key=None):
    """List the sounds remembered as silent on this PC, or forget them (action='clear': all, or key= one/several)."""
    act = str(action or "list").lower()
    packs = H["_state"]().get("silent_packs") or {}
    if act == "list":
        cur = H["_state"]().get(SILENT_STATE) or {}
        return {"silent_sounds": cur, "count": len(cur), **({"silent_packs": packs} if packs else {}),
                "note": "skipped by flh_new_song / flp_balance swaps / FLEX search; silent_sounds(action='clear') "
                        "forgets them (e.g. after reinstalling a pack)" if (cur or packs) else "none remembered"}
    if act not in ("clear", "forget", "remove"):
        raise ValueError("action: 'list' or 'clear' (key= to forget only some)")
    keys = [key] if isinstance(key, str) else list(key or [])

    def drop(cur):
        if not keys:
            n = len(cur); cur.clear(); return n
        low = {k.strip().lower() for k in keys}
        gone = [k for k in list(cur) if k.strip().lower() in low or k.split("|")[-1].strip().lower() in low]
        for k in gone:
            cur.pop(k)
        return len(gone)
    n = _silent_edit(drop)
    if packs:                                              # INSTRUMENT packs a render found silent: same rule
        sp = dict(packs); n += drop(sp)
        H["_state"]({"silent_packs": sp})
    return {"forgotten": n, "left": len(H["_state"]().get(SILENT_STATE) or {}) +
            len(H["_state"]().get("silent_packs") or {})}


def _result_key(r):
    """(source, pack, preset) of a _resolve result (what silent_mark keys)."""
    if r.get("plugin") == "FLEX":
        if r.get("flex_default"):
            return "FLEX", "", "default"
        return "FLEX", r.get("pack") or "", r.get("preset") or r.get("_preset") or ""
    if r.get("kind") == "labs":
        return "LABS", r.get("pack") or "", r.get("preset") or ""
    return r.get("plugin") or "", "", _stem(r.get("fst") or r.get("path"))


def _and(xs):
    xs = list(xs)
    return " and ".join(xs) if len(xs) < 3 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _sound_desc(k):
    src, pk, pre = k
    if src == "FLEX" and pre == "default" and not pk:
        return "FLEX's default sound"
    return f"'{pre}'" if pre else (f"{src} '{pk}'" if pk else src)


def _avoid_last(hits, avoid):
    if not avoid:
        return hits
    bad = lambda x: any(" " + " ".join(_wordlist(a)) + " " in " " + " ".join(_wordlist(x.get("name"))) + " "
                        for a in avoid if _wordlist(a))
    return [x for x in hits if not bad(x)] + [x for x in hits if bad(x)]


def _by_taste(hits):
    """Liked sounds first, disliked ones last (the catalog's own order otherwise: a stable sort)."""
    try:
        C = _mod("flh_catalog"); t = C.taste_get()
        if not (t["like"] or t["dislike"]):
            return hits
        sc = {id(x): C.taste_score(x.get("name"), t) + C.taste_score((x.get("load") or {}).get("preset"), t)
              for x in hits}
        return sorted(hits, key=lambda x: -sc[id(x)])
    except Exception:
        return hits


def _instrument_fst():
    """The plugin database .fst of Splice INSTRUMENT (what LABS packs play in), if installed and owned."""
    for n in ("Splice INSTRUMENT", "INSTRUMENT"):
        e = _find_plugin(n)
        if e and e["default"] and _fst_ok(e["default"]):
            return e["default"]
    return None


# ============================================================================= sample loading (tool)
_DRUM_NAME = {"kick", "kik", "bd", "snare", "snr", "clap", "hat", "hats", "hihat", "hh", "cymbal", "crash", "ride",
              "tom", "perc", "rim", "shaker", "drum", "drums"}
SAMPLE_RMS_TARGET = {"kick": -14.0, "snare": -16.0, "clap": -17.0, "rim": -20.0, "hat": -24.0, "openhat": -24.0,
                     "perc": -22.0, "shaker": -26.0, "ride": -26.0, "crash": -26.0, "tom": -18.0, "808": -15.0,
                     "fx": -26.0, "texture": -32.0, "vinyl": -32.0}   # RMS of the sounding part, before the role's level
PITCHED_ROLES = ("bass", "808", "sub", "chords", "keys", "melody", "lead", "counter", "pad", "strings", "arp", "bell",
                 "pluck", "brass", "organ", "choir", "vocal chop", "oneshot")


def _measured_pitch(sample):
    try:
        return _mod("flh_catalog").sample_features(sample).get("pitch")
    except Exception:
        return None


def _sample_pitch(sample):
    """(MIDI pitch or None, how it was found) of a sample: measured (tonal WAVs), else from its name ('808 F#')."""
    C = _mod("flh_catalog")
    try:
        ft = C.sample_features(sample) if sample.lower().endswith(C.FEATURE_EXTS) else {}
        if ft.get("pitch") is not None:
            return int(ft["pitch"]), "measured"
    except Exception:
        pass
    root = (C.classify_sample(sample) or {}).get("root")
    if root in C.PC_NAMES:
        return 24 + C.PC_NAMES.index(root), f"from its name ({root}, octave 1 assumed)"
    return None, None


def _chan_props(d, ev, ch):
    """{name, color '#RRGGBB' or None, insert or None} read from a channel's own block."""
    k0, kend = _chan_range(d, ev, ch)
    out = {"name": None, "color": None, "insert": None}
    for e in ev[k0 + 1:kend + 1]:
        p = H["_payload"](d, e)
        if e["id"] == H["EV_DISPLAY_NAME"] and out["name"] is None:
            out["name"] = H["_text16"](p)
        elif e["id"] == H["EV_CHANNEL_COLOR"] and len(p) >= 3 and out["color"] is None:
            out["color"] = "#%02X%02X%02X" % (p[0], p[1], p[2])
        elif e["id"] == EV_INSERT and len(p) >= 2 and out["insert"] is None:
            out["insert"] = struct.unpack("<h", p[:2])[0]
    return out


def _chan_sample_path(d, ev, ch):
    """The sample file a sampler channel plays (as stored in the project), or None."""
    try:
        k0, kend = _chan_range(d, ev, ch)
        for e in ev[k0 + 1:kend + 1]:
            if e["id"] == H["EV_SAMPLE_PATH"]:
                return H["_text16"](H["_payload"](d, e)) or None
    except Exception:
        pass
    return None


def _free_insert(d, ev):
    """A mixer insert no channel plays into and that isn't a named bus/send (default 'Insert N' or unnamed), or None."""
    used = set()
    for c in _chan_types(d, ev):
        try:
            ins = _chan_props(d, ev, c)["insert"]
        except Exception:
            ins = None
        if ins is not None:
            used.add(ins)
    names = {}
    tr = [k for k, e in enumerate(ev) if e["id"] == H["EV_MIXER_TRACK"]]
    for t, k in enumerate(tr):
        a = k
        while a > 0 and ev[a - 1]["id"] in (H["EV_MIXER_COLOR"], H["EV_MIXER_HASPROPS"], H["EV_MIXER_NAME"]):
            a -= 1
        nm = next((H["_text16"](H["_payload"](d, ev[j])) for j in range(a, k) if ev[j]["id"] == H["EV_MIXER_NAME"]), "")
        names[t] = nm
    for t in range(1, _insert_capacity(d) + 1):
        nm = (names.get(t) or "").strip()
        if t not in used and (not nm or re.fullmatch(r"(insert|track)\s*\d*", nm, re.I)):
            return t
    return None


def flp_load_sample(project, output, sample, channel=None, name=None, role=None, replace_plugin=False):
    """Put a sample into a sampler channel: channel=None makes a new sampler channel (a copy of one the project already
    has, else FL's own channel block) routed to a free mixer insert, channel=N replaces the sample of that sampler
    channel; on a plugin channel replace_plugin=true turns it into a sampler (name, color, mixer insert and notes
    kept). A pitched sampler
    (role bass / 808 / keys / lead ..., given or from the song journal) gets its sounding-pitch offset measured and
    recorded, and its existing notes moved so they still sound at the same pitch with the new sample."""
    H["writable"](output)
    if not os.path.isfile(sample):
        raise FileNotFoundError(sample)
    d = H["_load"](project)
    ev = H["_events"](d)
    types = _chan_types(d, ev)
    nm = name or os.path.splitext(os.path.basename(sample))[0]
    stored = _stored_sample_path(sample)
    try:
        jr = flh_store.journal_get(project)
    except Exception:
        jr = {}
    if channel is None:
        src = next((c for c, t in sorted(types.items()) if t == 0), None)
        # a new sound gets its own clean routing: the copied channel's mixer insert (and the effects on it) stay
        # with that channel. PC-VERIFY: insert 0 = the channel plays straight into the Master
        free = _free_insert(d, ev)
        d, ch = _append_channel(d, _sampler_block(d, ev, sample, nm, src=src, insert=free or 0))
        res = {"channel": ch, "created": True,
               "insert": (f"{free} (a free mixer track, named after the channel)" if free else
                          "0 (Master): no free mixer track - pick one in FL's channel settings"),
               "from": (f"a copy of sampler channel {src} (its envelope / filter settings came along; its mixer insert "
                        "did not)") if src is not None else "FL's built-in channel block"}
        if free:
            res["_name_insert"] = (free, nm)
    else:
        ch = int(channel)
        if ch not in types:
            raise ValueError(f"no channel {ch}")
        if types[ch] == 2 and replace_plugin:                       # plugin -> sampler, same slot, notes stay
            pr = _chan_props(d, ev, ch)
            src = next((c for c, t in sorted(types.items()) if t == 0), None)
            blk = _sampler_block(d, ev, sample, name or pr["name"] or nm, color=pr["color"], insert=pr["insert"], src=src)
            k0, kend = _chan_range(d, ev, ch)
            d = H["_splice"](d, ev, [(k0 + 1, kend + 1, blk)])
            res = {"channel": ch, "created": False, "replaced_plugin": True,
                   "kept": {"name": name or pr["name"], "color": pr["color"], "insert": pr["insert"]}}
        elif types[ch] != 0:
            kind = {2: "a plugin channel", 4: "an audio clip channel", 5: "an automation clip"}.get(types[ch], "not a sampler")
            hint = (" - or replace_plugin=true turns it into a sampler with this sample (name, color, mixer insert and "
                    "notes kept)") if types[ch] == 2 else ""
            raise ValueError(f"channel {ch} is {kind}; give a sampler channel, or leave channel out for a new one{hint}")
        else:
            old_path = _chan_sample_path(d, ev, ch)
            if old_path and _mod("flh_catalog")._taste_key(old_path) != _mod("flh_catalog")._taste_key(sample):
                try:                                       # swapped out: picked lower next time; the new one higher
                    _mod("flh_catalog").taste_note(like=[sample], dislike=[old_path])
                except Exception:
                    pass
            sets = {H["EV_SAMPLE_PATH"]: _txt(stored)}
            if name:
                sets[H["EV_DISPLAY_NAME"]] = _txt(name)
            d = _chan_edit(d, ch, sets)
            res = {"channel": ch, "created": False}
    # ---- pitched sampler: its offset between written and sounding pitch
    old = _mod("flh_score").sounding_offsets(jr).get(ch, 0) if channel is not None else 0
    jrole = ((jr.get("channels") or {}).get(str(ch)) or {}).get("role") if channel is not None else None
    r_ = str(role or jrole or "").lower()
    jset = None
    if r_ in PITCHED_ROLES or old:
        pitch, how = _sample_pitch(sample)
        if pitch is None:
            res["tuning"] = (f"the new sample's pitch couldn't be read: kept the old offset ({old:+d})" if old else
                             "the sample's pitch couldn't be read: notes play it at its recorded pitch on C5 - listen")
        else:
            new = _tune_semis(pitch, None)
            C = _mod("flh_catalog")
            res["tuning"] = {"sample_pitch": C.PC_NAMES[pitch % 12] + str(pitch // 12 - 1), "found": how,
                             "written_offset": new}
            if new != old:
                moved = 0
                if channel is not None:
                    d, moved = _transpose_channel(d, ch, new - old)
                res["tuning"]["notes_moved"] = moved
                res["tuning"]["why"] = (f"notes on channel {ch} are written {new:+d} semitones from the pitch you hear "
                                        "(FL plays a sample's own pitch on C5)" +
                                        (f"; its {moved} existing notes moved {new - old:+d} so the song sounds the same"
                                         if moved else ""))
            jset = {"channels": {str(ch): {"transpose": new, **({"role": r_} if role else {})}},
                    "compose": {"transpose": {str(ch): new}}}
    elif role:
        jset = {"channels": {str(ch): {"role": r_}}}
    H["_save"](d, output)
    ni = res.pop("_name_insert", None)
    if ni:
        try:
            H["flp_mixer"](output, output, [{"track": ni[0], "name": ni[1]}])
        except Exception as e:
            res["insert_note"] = f"routed to insert {ni[0]} but its name wasn't set ({str(e)[:60]})"
    if jset:
        try:
            flh_store.journal_update(output, set=jset)
        except Exception as e:
            res["journal_warning"] = f"the song journal wasn't updated ({str(e)[:80]}): tuning offset not recorded"
    res.update(output=output, sample=os.path.abspath(sample), stored_as=stored, name=nm if (name or channel is None) else None)
    return {k: v for k, v in res.items() if v is not None}


# ============================================================================= new song
def _palette_color(rec, role, family=None, prof=None):
    pal = dict((rec.get("palette") or {}))
    up = (prof or {}).get("palette") or {}
    for key in (role, family):
        if key and isinstance(up.get(key), str):
            return up[key]
    if role in DRUM_ROLES and isinstance(up.get("drums"), str):
        return up["drums"]
    for key in (family, role):
        if key and isinstance(pal.get(key), str):
            return pal[key]
    return "#3A3A3A"


_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def _unique_dest(folder, name):
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name)).strip()
    base = base[:80].rstrip(". ")                         # Windows: no trailing dots / spaces, paths stay short
    if not base or base.split(".")[0].strip().upper() in _RESERVED_NAMES:
        base = (base + " song") if base else "New song"   # CON, NUL, COM1... are devices on Windows
    for n in range(1, 500):
        nm = base if n == 1 else f"{base} {n}"
        p = os.path.join(folder, nm, nm + ".flp")
        if not os.path.exists(os.path.dirname(p)) and not os.path.exists(p):
            return p, nm
    raise RuntimeError("couldn't find a free song name")


def _default_folder():
    try:
        prof = flh_store.profile_get() or {}
    except Exception:
        prof = {}
    for k in ("new_song_folder", "projects_root"):
        if prof.get(k):
            return prof[k]
    roots = H["roots"]()
    if not roots:
        raise PermissionError("FLH_ROOTS isn't set, so fl-headless can't create files: flh_setup() shows how")
    env = [r for r in os.environ.get("FLH_ROOTS", "").split(";") if r.strip()]
    order = env + [r for r in roots if r not in env]
    best = _projects_home(order)
    return best or (env[0] if env else roots[0])


def _projects_home(roots, cap=8000):
    """Where the user keeps FL projects: among the allowed folders (and their 'Projects' / 'My Projects' subfolders),
    the one whose song folders hold the most .flp files. None if no project is found (so a beat never lands in, say,
    a music-collection folder just because it's the first one allowed)."""
    best, best_n, seen = None, 0, 0
    cands = []
    for r in roots:
        cands.append(r)
        for sub in ("My Projects", "Projects", "FL Projects"):
            p = os.path.join(r, sub)
            if os.path.isdir(p):
                cands.append(p)
    for c in cands:
        n = 0; seen = 0                                           # a bounded look at each candidate
        try:
            with os.scandir(c) as it:
                for e in it:
                    seen += 1
                    if seen > cap:
                        break
                    if e.is_file() and e.name.lower().endswith(".flp"):
                        n += 1
                    elif e.is_dir() and not e.name.startswith(("_", ".")):
                        try:
                            with os.scandir(e.path) as it2:
                                for f in it2:                     # counted too: a huge music library stays quick
                                    seen += 1
                                    if seen > cap:
                                        break
                                    if f.name.lower().endswith(".flp") and f.is_file():
                                        n += 1
                        except OSError:
                            pass
        except OSError:
            continue
        if n > best_n:
            best, best_n = c, n
    return best


def _plan_inserts(chs, rec, cap):
    """{channel spec id: insert name} + insert list [{name, roles, color, bus}], buses, sends - fitted into `cap`."""
    buses = [b for b in rec.get("buses") or []]
    sends = [s for s in rec.get("sends") or []]
    groups = {}

    def build(merge):
        ins = []
        for c in chs:
            key = c.get("group") if merge and c.get("group") else c["name"]
            hit = next((i for i in ins if i["key"] == key), None)
            if hit:
                hit["members"].append(c)
            else:
                ins.append({"key": key, "name": key, "members": [c]})
        return ins
    used_bus = lambda: [b for b in buses if any(c["role"] in b.get("roles", []) or c.get("family") in b.get("roles", [])
                                               or c.get("bus") == b["name"] for c in chs) or b["name"] == "Vocals"]
    ins = build(False)
    bl = used_bus()
    notes = []
    if len(ins) + len(bl) + len(sends) > cap:
        ins = build(True); notes.append("channels sharing a group share an insert (the mixer is short)")
    if len(ins) + len(bl) + len(sends) > cap and any(b["name"] == "Vocals" for b in bl):
        bl = [b for b in bl if b["name"] != "Vocals"]; notes.append("no Vocals bus (no room)")
    if len(ins) + len(bl) + len(sends) > cap:
        sends = []; notes.append("no send tracks (no room)")
    if len(ins) + len(bl) > cap:
        bl = []; notes.append("no buses (no room)")
    if len(ins) > cap:
        notes.append(f"only {cap} inserts: {len(ins) - cap} channel(s) play through the master directly")
        ins = ins[:cap]
    for i in ins:
        if len(i["members"]) == 1:
            i["name"] = i["members"][0]["name"]
        groups.update({id(c): i for c in i["members"]})
    return ins, bl, sends, notes, groups


FLEX_KIND = {"keys": "keys", "chords": "keys", "piano": "keys", "pad": "pad", "strings": "strings", "brass": "brass",
             "horns": "brass", "lead": "lead", "melody": "lead", "bass": "bass", "808": "808", "choir": "choir",
             "pluck": "pluck", "bells": "bells", "bell": "bells", "guitar": "guitar", "organ": "organ",
             "woodwind": "woodwind", "flute": "woodwind", "arp": "synth", "synth": "synth"}


def _prefer_flex():
    """The collection profile's sound preference (prefer: 'flex' or prefer_flex: true)."""
    try:
        prof = flh_store.profile_get() or {}
    except Exception:
        return False
    pref = prof.get("prefer") or prof.get("prefer_sources") or []
    pref = [pref] if isinstance(pref, str) else pref
    return bool(prof.get("prefer_flex")) or any(str(x).lower() == "flex" for x in pref)


def _flex_default(kind, skipped, why, ok=True):
    """FLEX from its plugin-database file (its own default sound) when no FLEX preset file could be made: no pack
    readable on this PC, or the preset state couldn't be written. A preset of another kind never falls back here."""
    if not ok:                                           # the profile's 'prefer FLEX' pass: presets only (else
        if why not in skipped:                           # every part would get the same default sound)
            skipped.append(why)
        return None
    e = _find_plugin("FLEX")
    if not e or not e["default"] or not _fst_ok(e["default"]):
        skipped.append(f"{why}; FLEX's plugin database file has no plugin state" if e else
                       "FLEX: not in FL's plugin database"); return None
    if is_silent("FLEX", "", "default"):
        skipped.append(f"{why}; FLEX's default sound made no sound on this PC"); return None
    if _FLEX_USED.get("default"):                        # one channel per song: two parts on the same sound is
        skipped.append(f"{why} (FLEX's default sound is on another channel)"); return None    # worse than the next pick
    _FLEX_USED["default"] = True
    # PC-VERIFY: FLEX from its plugin database file plays a sound before a preset is picked (fl_pc_check
    # newsong.flex_default)
    return {"kind": "plugin", "fst": e["default"], "plugin": "FLEX", "label": "FLEX (its default sound)",
            "hint": f"{why}: in FLEX pick a {kind} preset", "skipped": skipped, "flex_default": True}


def _flex_choice(kind, spec, rec, genre, vibe_words, skipped, default_ok=True, query=None, avoid=None):
    """A FLEX preset of this kind from the installed packs (FLEX opens on it when FL loads the song), ranked by pack
    quality, the part's character words (query), the genre and taste. A General MIDI style preset comes back with
    last_resort=True: the caller keeps it only for when nothing decent of the kind is left."""
    if _owned("FLEX") is not True:
        skipped.append("FLEX: not owned / unknown"); return None
    if not H.get("flex_search") or not H.get("flex_index"):
        return _flex_default(kind, skipped, "FLEX's preset index isn't available", default_ok)
    try:
        if not H["flex_index"]():
            return _flex_default(kind, skipped, "no FLEX pack could be read on this PC", default_ok)
        vh = _VIBE.get("hints") or {}                    # the user's vibe words count like the part's character
        strong = _wordlist(query) + _wordlist(vibe_words) + _wordlist(vh.get(spec["role"]) or
                                                                       vh.get(spec.get("family") or "") or [])
        hits = H["flex_search"](None, kind=kind, genre=genre, limit=8, prefer=strong or None,
                                hint=" ".join(_words(spec, rec, genre, [])) or None, avoid=avoid)
    except Exception as e:
        skipped.append(f"FLEX: {str(e)[:60]}"); return None
    try:                                                 # packs whose sounds aren't here: left out, and said
        fl_ = H["flex_pack_flags"]() if H.get("flex_pack_flags") else {}
        k_ = H["flex_kind"](kind) if H.get("flex_kind") else kind
        gone = sorted({r["pack"] for r in H["flex_index"]() if r["pack"] in fl_ and r.get("kind") == k_})
        if gone:
            skipped.append(f"FLEX: left out {', '.join(gone[:3])} ({fl_[gone[0]][:70]})")
    except Exception:                                    # noqa: BLE001 - only the note is lost
        pass
    if hits:                                             # presets that made no sound on this PC (an older
        n0 = len(hits)                                   # flex_search may not skip them itself)
        hits = [h for h in hits if not is_silent("FLEX", h.get("pack"), h.get("preset"))]
        if not hits:
            skipped.append(f"FLEX: the {kind} presets found made no sound on this PC (silent_sounds lists them)")
            return None
        ok_ = [h for h in hits if not h.get("unavailable")]   # a pack another preset of was silent here: only
        ok_ = ok_ or [h for h in hits if h.get("installed") is not False]   # when nothing else is left (last resort)
        if not ok_:
            skipped.append(f"FLEX: the {kind} presets left are in packs that made no sound / aren't downloaded "
                           f"here ({hits[0]['pack']})")
            return None
        hits = ok_
        bad = _SWAP_AVOID.get("packs") or ()             # a silent-sound swap: another pack first
        if bad and n0:
            hits = [h for h in hits if h.get("pack") not in bad] + [h for h in hits if h.get("pack") in bad]
    if not hits:
        skipped.append(f"FLEX: no {kind} preset in the installed packs"); return None
    used = _FLEX_USED.setdefault("names", set())
    fresh = [h for h in hits if h["preset"] not in used]
    pick = next((h for h in fresh if not h.get("last_resort")), None) or (fresh or hits)[0]
    try:
        fst, hit = H["flex_fst"](pick["preset"], pick["pack"])
    except Exception as e:
        return _flex_default(kind, skipped, f"FLEX {pick['preset']} couldn't be set up ({str(e)[:60]})", default_ok)
    got = {"kind": "plugin", "fst": fst, "plugin": "FLEX", "label": f"FLEX {hit['preset']} ({hit['pack']})",
           "skipped": skipped, "alternatives": [h["preset"] for h in hits if h is not pick][:2],
           "_preset": pick["preset"], "pack": hit["pack"], "preset": hit["preset"]}
    if pick.get("last_resort"):
        # PC-VERIFY: a General MIDI style FLEX preset only when nothing better of the kind exists (newsong.md 14a)
        got["last_resort"] = True
        got["hint"] = (f"a basic General MIDI style preset - the only FLEX {kind} sound on this PC: a FLEX Core / "
                       f"store pack or LABS sounds much better")
    else:
        used.add(pick["preset"])
    return got


_FLEX_USED = {}


def _claim_flex_default(got, skipped):
    """Use the FLEX sound _resolve kept back (FLEX's default sound: one channel per song; a General MIDI preset)."""
    if got.get("flex_default"):
        _FLEX_USED["default"] = True
    if got.get("_preset"):
        _FLEX_USED.setdefault("names", set()).add(got["_preset"])
    note = got.pop("_note", None)
    if note in skipped:
        skipped.remove(note)
    return got


def _resolve(spec, rec, genre, vibe_words, kit, used_samples, edition_notes):
    """What fills a channel: {"kind": "sample", path, ...} | {"kind": "plugin", fst, plugin, ...} | {"kind": "labs", ...}
    | {"kind": "empty"} - the first owned and available choice; skipped choices are reported honestly."""
    role, family = spec["role"], spec.get("family") or spec["role"]
    choices = spec.get("choices") or (rec.get("choices") or {}).get(family) or \
        (rec.get("choices") or {}).get("sample" if role in SAMPLER_ROLES else "keys") or []
    skipped = []
    deferred = None                                       # FLEX's own default sound: only a last resort (below)
    kitq = ((kit or {}).get("roles") or {}).get(spec.get("sample") or family) or \
        ((kit or {}).get("roles") or {}).get(role) or ""
    vh = _VIBE.get("hints") or {}
    vibeq = " ".join(vh.get(role) or vh.get(family) or [])
    char = spec.get("character") or {}                   # the part's sound character: words for every choice

    def cq(ch):
        return ch.get("query") if "query" in ch else char.get("query")

    def cav(ch):
        a = ch.get("avoid") if "avoid" in ch else char.get("avoid")
        return [a] if isinstance(a, str) else list(a or [])

    def keep_back(got, rank, note):                      # last resorts, best kept: 3 = a General MIDI FLEX
        nonlocal deferred                                # preset, 2 = a synth's init patch (its preset names
        if deferred is not None and deferred.get("_rank", 0) >= rank:   # weren't found), 1 = FLEX's default
            if note not in skipped:                      # sound - used when the chain reaches 3x Osc or its end
                skipped.append(note)
            return
        if deferred is not None and deferred.get("_note") in skipped:
            skipped.remove(deferred["_note"])
            skipped.append(deferred["_note"].replace("kept only as a last resort", "passed over"))
        got["_rank"], got["_note"] = rank, note
        skipped.append(note)
        deferred = got
    GM_NOTE = "{}: a General MIDI style preset (kept only as a last resort)"
    tried = set()
    if role not in SAMPLER_ROLES and _prefer_flex() and not (choices and "sample" in choices[0] and
                                                             family != "808"):
        # the profile prefers FLEX: its presets first (each FLEX choice of the chain in order: kind + character);
        # a part whose recipe asks for a sample first (a vocal chop, a cowbell) keeps it - an 808 bass doesn't
        # (FLEX 808 presets play in key)
        fch = [c for c in choices if "flex" in c]
        if not fch and (FLEX_KIND.get(family) or FLEX_KIND.get(role)):
            fch = [{"flex": FLEX_KIND.get(family) or FLEX_KIND.get(role)}]
        for c in fch:
            tried.add(id(c))
            got = _flex_choice(c["flex"], spec, rec, genre, vibe_words, skipped, default_ok=False, query=cq(c),
                               avoid=cav(c))
            if got and not got.get("last_resort"):
                return got
            if got:
                keep_back(got, 3, GM_NOTE.format(got["label"]))
    for ch in choices:
        if "sample" in ch:
            kind = spec.get("sample") or (role if ch["sample"] == "$role" else ch["sample"])
            words = " ".join(x for x in (spec.get("query", ""), ch.get("query", ""), kitq, " ".join(vibe_words), vibeq)
                             if x)
            try:                                          # deterministic ranking (same kit on any PC / cache state)
                r = H["sample_search"](kind, words or None, genre, limit=6, exclude=used_samples)
            except Exception as e:
                skipped.append(f"samples: {str(e)[:60]}"); continue
            avoid = set((kit or {}).get("avoid") or [])
            hits = [x for x in r["results"] if not (avoid & set(x.get("tags") or []))] or r["results"]
            if kind == "808" or family == "808":           # a pitched 808: one whose pitch is known can be tuned
                hits = sorted(hits, key=lambda x: 0 if x.get("pitch") is not None else      # measured
                              1 if str(x.get("path", "")).lower().endswith((".wav", ".ogg", ".flac")) else  # measurable now
                              2 if x.get("root") else 3)                                 # name only / unknown
                for x in hits[:4]:                         # measure WAVs now: the first one with a pitch can be tuned
                    if x.get("pitch") is None and str(x.get("path", "")).lower().endswith((".wav", ".ogg", ".flac")):
                        x["pitch"] = _measured_pitch(x["path"])
                    if x.get("pitch") is not None:
                        hits = [x] + [h for h in hits if h is not x]
                        break
                tunable = [h for h in hits if h.get("pitch") is not None or (h.get("root") and not h.get("bpm"))]
                if not tunable:                            # an 808 that can't be tuned plays out of key: use
                    try:                                   # fl-headless's own 808 (its pitch is known)
                        C = _mod("flh_catalog"); own = C.own_808()
                        return {"kind": "sample", "path": own, "label": "fl-headless 808 (a clean tuned sub)",
                                "source": "made by fl-headless", "tags": ["clean"], "pitch": C.OWN_808_NOTE,
                                "root": None, "skipped": skipped,
                                "note": "no 808 sample on this PC has a readable pitch, so fl-headless made a tuned "
                                        "one: swap in your own 808 any time (fl_sound_catalog kind='808')"}
                    except Exception as ex:
                        skipped.append(f"own 808: {str(ex)[:60]}")
                    if len(choices) > 1:
                        skipped.append("808 samples: none with a readable pitch, so none could be tuned to the key")
                        continue
                hits = tunable + [h for h in hits if h not in tunable]
            if hits and role in DRUM_ROLES and used_samples:   # one kit: a near-best hit from a folder already
                dirs = {os.path.normcase(os.path.dirname(u)) for u in used_samples}   # used goes first
                top = hits[0].get("score", 0)
                same = [h for h in hits if os.path.normcase(os.path.dirname(h["path"])) in dirs
                        and h.get("score", 0) >= top - 1.5]
                if same and same[0] is not hits[0]:
                    hits = [same[0]] + [h for h in hits if h is not same[0]]
            if hits:
                x = hits[0]
                out = {"kind": "sample", "path": x["path"], "label": x["name"], "source": x.get("source"),
                       "tags": x.get("tags"), "pitch": x.get("pitch"), "root": x.get("root"), "skipped": skipped,
                       "alternatives": [h["name"] for h in hits[1:3]], "level_db": x.get("level_db")}
                if r.get("fallback"):
                    out["note"] = r["fallback"]
                return out
            skipped.append(f"no {kind} sample on this PC")
        elif "fl_instrument" in ch:                       # FL's own multisampled instruments (real recordings)
            gl = [str(x).lower() for x in ch.get("genres") or []]
            if gl and str(genre or "").lower() not in gl:
                continue
            pick = _catalog_pick(ch["fl_instrument"], "FL instruments", _wordlist(cq(ch)) +
                                 _words(spec, rec, genre, vibe_words), cav(ch))
            if not pick:
                skipped.append(f"FL's {ch['fl_instrument']} instruments: not found"); continue
            fst = pick["load"]["preset"]
            if not _fst_ok(fst):
                skipped.append(f"{pick['name']}: its preset file has no plugin state"); continue
            return {"kind": "plugin", "fst": fst, "plugin": "DirectWave", "label": pick["name"],
                    "hint": ch.get("hint"), "skipped": skipped}
        elif "labs" in ch:
            pick = _catalog_pick(ch["labs"], "LABS", _wordlist(cq(ch)) + _words(spec, rec, genre, vibe_words),
                                 cav(ch))
            fst = _instrument_fst()
            if not pick:
                skipped.append(f"LABS {ch['labs']}: no downloaded pack"); continue
            if not fst:
                skipped.append("LABS: INSTRUMENT isn't in FL's plugin database"); continue
            if not _fst_ok(fst):                          # FL 2026 lists VSTs by name only: no INSTRUMENT state
                skipped.append(f"LABS {pick['name']}: INSTRUMENT can't be set up inside the file (pick the pack in "
                               "INSTRUMENT's window instead)"); continue
            return {"kind": "labs", "fst": fst, "plugin": "INSTRUMENT", "pack": pick["load"]["pack"],
                    "preset": pick["load"].get("preset"), "label": pick["name"], "skipped": skipped}
        elif "flex" in ch:
            if id(ch) in tried:
                continue
            got = _flex_choice(ch["flex"], spec, rec, genre, vibe_words, skipped, query=cq(ch), avoid=cav(ch))
            if got and got.get("flex_default"):
                _FLEX_USED["default"] = False             # not claimed yet: a fitting sound later in the list wins
                keep_back(got, 1, f"FLEX: {got['hint'].split(':')[0]} (its default sound kept only as a last "
                          "resort)")                      # (a stock / owned plugin of the right kind beats FLEX's
                continue                                  # generic default; only the generic 3x Osc doesn't)
            if got and got.get("last_resort"):            # a General MIDI preset: any real sound later in the list
                keep_back(got, 3, GM_NOTE.format(got["label"]))   # wins; it beats only an init patch / 3x Osc
                continue
            if got:
                return got
            continue
        elif "plugin" in ch:
            plug = ch["plugin"]
            if deferred and plug == "3x Osc":
                return _claim_flex_default(deferred, skipped)
            own = _owned(plug)
            if own is not True:                           # a demo in a song: FL nags on save / the sound cuts out
                skipped.append(f"{plug}: {'trial in your FL edition' if own == 'trial' else 'ownership unknown'}"
                               + (f" ({_own_why(plug)})" if _own_why(plug) else ""))
                edition_notes.add((own, plug))
                continue
            e = _find_plugin(plug)
            if not e or not (e["default"] or e["presets"]):
                skipped.append(f"{plug}: not in FL's plugin database"); continue
            pool = [x for x in e["presets"] if not is_silent(plug, "", _stem(x))]   # made no sound here before
            if role not in DRUM_ROLES:                    # 'Kick 808' is a drum, not an 808 bass
                pool = [x for x in pool if not (_DRUM_NAME & set(_mod("flh_catalog")._tokens(
                    os.path.splitext(os.path.basename(x))[0])))] or pool
            pre = _glob_pick(pool, ch.get("presets"), FLEX_KIND.get(family) or family, cq(ch), cav(ch)) \
                if ch.get("presets") else None
            if pre and not _fst_ok(pre):
                pre = None
            dflt = e["default"] if e["default"] and _fst_ok(e["default"]) and \
                not is_silent(plug, "", _stem(e["default"])) else None
            if not pre and not dflt:                      # no Default.fst (e.g. BooBass): its first factory preset
                pre = next((x for x in sorted(e["presets"], key=str.lower)[:40] if _fst_ok(x) and
                            not is_silent(plug, "", _stem(x))), None)
            if not pre and not dflt:
                skipped.append(f"{plug}: no loadable preset file"); continue
            fst = pre or dflt
            label = f"{plug} '{os.path.splitext(os.path.basename(pre))[0]}'" if pre else f"{plug} (default patch)"
            hint = ch.get("hint")
            if ch.get("presets") and not pre:             # the preset names are a guess: say what happened
                # PC-VERIFY: the factory preset names the recipes' globs guess (checklists/newsong.md 12a)
                hint = (f"no {plug} factory preset named like {'/'.join(ch['presets'][:3])} here, so its default "
                        f"patch: pick a {FLEX_KIND.get(family) or family} preset in {plug}'s browser"
                        + (f"; {hint}" if hint else ""))
            got = {"kind": "plugin", "fst": fst, "plugin": plug, "label": label, "hint": hint, "skipped": skipped}
            if ch.get("presets") and not pre and _init_patch(plug):    # a synth's raw init patch: any real sound
                keep_back(got, 2, f"{plug}: no factory preset named like {'/'.join(ch['presets'][:3])} (its init "
                          "patch kept only as a last resort)")       # later in the chain wins
                continue
            if ch.get("presets") and not pre and deferred and deferred.get("_rank") == 3 and \
                    not _kind_fits(plug, family):                  # FL Keys' piano for an organ part: the GM
                return _claim_flex_default(deferred, skipped)      # organ (the right instrument) is better
            return got
    if deferred:
        return _claim_flex_default(deferred, skipped)
    return {"kind": "empty", "label": "empty sampler (load a sound)", "skipped": skipped}


def _vibe_hints(vibe_words):
    """{role: sound words} from data/adjectives.json for the vibe's adjectives ('dusty' -> dustier.sound.kick ...)."""
    try:
        adj = _mod("flh_compose").adjectives()
        words = adj.get("words") if isinstance(adj, dict) and "words" in adj else adj
    except Exception:
        return {}
    out = {}
    for w in vibe_words:
        for k, v in (words or {}).items():
            if not isinstance(v, dict):
                continue
            if w == k or w in (v.get("aliases") or []) or (len(w) > 3 and k.startswith(w[:-1])):
                for role, hint in (v.get("sound") or {}).items():
                    out.setdefault(role, []).append(str(hint))
    return out


def _words(spec, rec, genre, vibe_words):
    try:
        g = _mod("flh_compose").genre_data(genre)
        hint = ((g.get("roles") or {}).get(spec["role"]) or {}).get("hint", "")
    except Exception:
        hint = ""
    vh = _VIBE.get("hints") or {}
    extra = " ".join(vh.get(spec["role"]) or vh.get(spec.get("family") or "") or [])
    return [w for w in re.findall(r"[a-z]+", (hint + " " + " ".join(vibe_words) + " " + extra).lower()) if len(w) > 3]


_VIBE = {}


def _section_plan(g, rec, form=None):
    secs = form or rec.get("form") or ((g or {}).get("form") or {}).get("default") or [["A", 8]]
    out, bar = [], 1.0
    for name, bars in secs:
        out.append({"name": name, "bar": bar, "end_bar": bar + float(bars)})
        bar += float(bars)
    return out


def _main_section(g, plan):
    specs = (g or {}).get("sections") or {}
    best = max(plan, key=lambda s: ((specs.get(s["name"]) or {}).get("energy", 0.5),
                                    -plan.index(s)), default=None)
    return best["name"] if best else None


def _transpose_channel(d, ch, semis):
    """Move every note of a channel by `semis` (a pitched sample whose root isn't C: FL plays it at its recorded pitch
    on C5)."""
    ev = H["_events"](d)
    n = 0
    for pid, p in sorted(H["_patterns"](d, ev).items()):
        notes = [H["_rec_to_note"](r) for r in p["notes"]]
        if not any(x["ch"] == ch for x in notes):
            continue
        for x in notes:
            if x["ch"] == ch:
                x["key"] = max(0, min(127, x["key"] + semis)); n += 1
        d = H["_write_pattern_notes"](d, H["_events"](d), pid, notes)
        ev = H["_events"](d)
    return d, n


def _tune_semis(pitch, root):
    """Semitones to move notes written at sounding pitch so a sample recorded at `pitch` (MIDI; or `root`, a note
    name assumed in octave 1 like most 808s) sounds right: FL's sampler plays the recording's own pitch on C5 (60)."""
    if pitch is None and root:
        C = _mod("flh_catalog")
        pitch = 24 + C.PC_NAMES.index(root) if root in C.PC_NAMES else None
    if pitch is None:
        return 0
    return 60 - int(pitch)


def _work(stem):
    st = os.path.dirname(H["STATE"])
    wd = os.path.join(st, "work", f"newsong-{os.getpid()}-{int(time.time() * 1000) % 10 ** 9}")
    os.makedirs(wd, exist_ok=True)
    key = os.path.normcase(os.path.abspath(wd))
    H["_TEMP_ROOTS"].append(key)
    return wd, os.path.join(wd, stem + ".flp"), key


def _cleanup(wd, key):
    try:
        H["_TEMP_ROOTS"].remove(key)
    except ValueError:
        pass
    own = os.path.normcase(os.path.abspath(os.path.join(os.path.dirname(H["STATE"]), "work")))
    if os.path.normcase(os.path.abspath(wd)).startswith(own + os.sep):   # our own scratch folder only
        shutil.rmtree(wd, ignore_errors=True)


def user_templates():
    base = os.path.join(os.path.dirname(H["STATE"]), "templates")
    out = {}
    if os.path.isdir(base):
        for n in sorted(os.listdir(base)):
            tj = os.path.join(base, n, "template.json")
            if os.path.isfile(tj):
                out[n] = tj
    return out


def _user_template(name):
    ts = user_templates()
    hit = ts.get(name) or next((v for k, v in ts.items() if k.lower() == str(name).lower()), None)
    if not hit:
        raise ValueError(f"no saved template '{name}' (have: {sorted(ts)}); flh_save_template makes one")
    with open(hit, encoding="utf-8") as f:
        t = json.load(f)
    t["_dir"] = os.path.dirname(hit)
    return t


# ============================================================================= level balance (after the song opens)
BALANCE_ON = True
BALANCE_TARGETS = {"bass": -1.0, "808": 0.0, "sub": -1.0, "keys": -4.0, "chords": -4.0, "piano": -4.0, "guitar": -5.0,
                   "pad": -9.0, "strings": -7.0, "brass": -6.0, "lead": -3.0, "melody": -3.0, "counter": -6.0,
                   "choir": -8.0, "pluck": -5.0, "bells": -7.0, "bell": -7.0, "organ": -6.0, "arp": -6.0,
                   "synth": -5.0}           # LU against the drums; a genre's "balance": {...} overrides
BALANCE_MIX_LUFS = -14.0                    # the beat's level after balancing (the master limiter's gain makes it)
FADER_MAX_DB = 5.6
BALANCE_CEILING_DB = -1.0                   # the master limiter's ceiling once its gain is up


def _balance_units(project):
    """[(label, role, [channels], insert)]: the drums as one unit, each melodic channel on its own."""
    try:
        jr = flh_store.journal_get(project)
    except Exception:
        jr = {}
    chans = {int(k): v for k, v in (jr.get("channels") or {}).items() if str(k).isdigit()}
    drums = sorted(c for c, v in chans.items() if (v.get("role") or "") in DRUM_ROLES)
    units = [("drums", "drums", drums, None)] if drums else []
    for c, v in sorted(chans.items()):
        r = str(v.get("role") or "").lower()
        if r in BALANCE_TARGETS:
            units.append((v.get("name") or r, r, [c], v.get("mixer")))
    return units


def _measure_units(project, units, notes, steps):
    """{label: LUFS or None (silent)} for the balance units: all from ONE export with FL's 'Split mixer tracks' (or
    stems cached from one) when the server can; the units it can't separate - or all, when one-pass stems aren't
    available here - are measured alone with fl_preview (one export each, reused while unchanged).
    -> (levels, how)."""
    T = time.time(); out = {}; how = None
    todo = list(units)
    f = H.get("_stems_measure_units")
    if f and len(units) > 1:
        try:
            r = f(project, units)
        except Exception as e:                                # noqa: BLE001 - the per-part way still works
            r = {"why": f"{type(e).__name__}: {str(e)[:60]}"}
        if r.get("lufs") is not None:
            out.update(r["lufs"]); how = r.get("how")
            todo = [u for u in units if u[0] in (r.get("missing") or [])]
            if todo:
                notes.append(f"measured alone (they share a mixer track): {', '.join(u[0] for u in todo)}")
        elif r.get("why"):
            notes.append(f"one-pass stems unavailable ({r['why'][:140]}): each part measured alone")
        steps["stems"] = round(time.time() - T, 1)
    T = time.time()
    for label, role, chs, ins in todo:
        try:
            r = H["fl_preview"](channels=chs)
        except Exception as e:
            notes.append(f"{label}: {str(e)[:60]}"); continue
        out[label] = None if r.get("lufs") is None or r.get("silent") else float(r["lufs"])
    if todo:
        steps["per_part"] = round(time.time() - T, 1)
        how = (how + " + " if how else "") + f"{len(todo)} part(s) alone"
    return out, how


def _channel_sound(project, ch, jc):
    """(source, pack, preset) of the instrument on a plugin channel: FLEX's pack/preset read from the channel's own
    state in the file (the truth, even after a hand swap), else the journal's record. None: a sampler channel or
    nothing known."""
    try:
        d = H["_load"](project); ev = H["_events"](d)
        a, b = H["_channel_block"](d, ev, int(ch))
    except Exception:                                     # a sampler (no plugin block) or an unreadable file
        return None
    try:
        plug = H["_fst_text"](H["_payload"](d, ev[a]))
    except Exception:
        plug = ""
    FX = sys.modules.get("flh_flex")
    if FX is not None and hasattr(FX, "state_names"):
        try:
            got = FX.state_names(H["_payload"](d, ev[b]))
        except Exception:
            got = None
        if got and got[1]:
            return "FLEX", got[0] or "", got[1]
    jp = jc.get("plugin")
    if jp == "FLEX" and jc.get("preset"):
        return "FLEX", jc.get("pack") or "", jc["preset"]
    if jp == "INSTRUMENT" and jc.get("pack"):
        return "LABS", jc["pack"], jc.get("preset") or ""
    if jp and jc.get("sound"):
        return jp, "", _stem(jc["sound"])
    return (plug, "", plug) if plug and plug != "Fruity Wrapper" else None


def _spec_for(rec, jc):
    """The recipe channel a journal channel was built from (its index, else name + role, else role)."""
    specs = rec.get("channels") or []
    i = jc.get("spec")
    if isinstance(i, int) and 0 <= i < len(specs) and specs[i].get("name") == jc.get("name"):
        return dict(specs[i])
    for test in (lambda s: s.get("name") == jc.get("name") and s.get("role") == jc.get("role"),
                 lambda s: s.get("role") == jc.get("role")):
        s = next((s for s in specs if test(s)), None)
        if s:
            return dict(s)
    return {"role": jc.get("role") or "keys", "name": jc.get("name") or "Part"}


def _next_sound(genre, ch, jc, jr, avoid_packs):
    """The next sound for a channel's part by the same ranking flh_new_song used (the recipe channel's choices,
    character words, vibe, kit, editions, FLEX quality and taste); sounds remembered as silent are skipped by the
    ranking itself, presets on the song's other channels are left to them."""
    rec, _g = recipe(genre)
    spec = _spec_for(rec, jc)
    cr = jr.get("created") or {}
    vibe_words = [w for w in re.findall(r"[a-z0-9-]+", str(cr.get("vibe") or "").lower()) if len(w) > 2]
    kit_d = _kit(cr["kit"]) if cr.get("kit") else None
    was_v, was_u, was_a = dict(_VIBE), dict(_FLEX_USED), dict(_SWAP_AVOID)
    try:
        _VIBE["hints"] = _vibe_hints(vibe_words)
        _FLEX_USED.clear(); _FLEX_USED["names"] = set()
        for c, v in (jr.get("channels") or {}).items():
            if str(c) != str(ch) and v.get("plugin") == "FLEX":
                if v.get("preset") == "default" and not v.get("pack"):
                    _FLEX_USED["default"] = True
                elif v.get("preset"):
                    _FLEX_USED["names"].add(v["preset"])
        _SWAP_AVOID["packs"] = set(avoid_packs)
        return _resolve(spec, rec, genre, vibe_words, kit_d, [], set())
    finally:
        _VIBE.clear(); _VIBE.update(was_v)
        _FLEX_USED.clear(); _FLEX_USED.update(was_u)
        _SWAP_AVOID.clear(); _SWAP_AVOID.update(was_a)


def _put_sound(project, ch, r, jc):
    """Put a _resolve plugin / LABS result on a channel of the song (open in FL: saved and reloaded in place), its
    name and color kept, its notes untouched; the journal follows."""
    try:
        d = H["_load"](project); props = _chan_props(d, H["_events"](d), int(ch))
    except Exception:
        props = {}

    def run():
        H["flp_load_plugin"](project, project, r["fst"], channel=int(ch))
        if r["kind"] == "labs":
            H["flp_instrument_sound"](project, project, int(ch), r["pack"], r.get("preset"))
        sty = {"channel": int(ch)}
        if props.get("name") or jc.get("name"):
            sty["name"] = jc.get("name") or props["name"]
        if props.get("color"):
            sty["color"] = props["color"]
        H["flp_style_channels"](project, project, [sty])
        return {"output": project}
    # PC-VERIFY: the swap reloads the open song in place mid-balance and the channel sounds (newsong.md silent swap)
    H["_with_open_project"](project, project, run)
    k = _result_key(r)
    upd = {"sound": r.get("fst"), "plugin": r.get("plugin"), "pack": k[1] or None,
           "preset": (k[2] or None) if k[0] in ("FLEX", "LABS") else None}
    try:
        flh_store.journal_update(project, set={"channels": {str(ch): upd}})
    except Exception:                                     # noqa: BLE001 - the swap is in the song already
        pass
    jc.update(upd)


def _fix_silent(project, genre, ch, label, steps):
    """A melodic part with notes measured silent alone: its sound plays nothing on this PC (a preset whose content
    is missing). The sound is remembered as silent, the part gets the next-ranked sound for its role and is measured
    again alone - up to SILENT_SWAPS times. -> (LUFS or None, note)."""
    T = time.time()
    try:
        jr = flh_store.journal_get(project) or {}
    except Exception:
        jr = {}
    jc = dict((jr.get("channels") or {}).get(str(ch)) or {})
    cur = _channel_sound(project, ch, jc)
    if not cur:
        return None, (f"{label} is silent alone although it has notes (a sampler, or a plugin fl-headless can't "
                      "swap): check its sound")
    marked = [silent_mark(*cur)]
    dead, packs = [_sound_desc(cur)], {cur[1]} if cur[0] == "FLEX" and cur[1] else set()
    why = None
    try:
        for _ in range(SILENT_SWAPS):
            try:
                r = _next_sound(genre or jr.get("genre"), ch, jc, jr, packs)
            except Exception as e:                        # noqa: BLE001
                why = f"no next sound ({str(e)[:60]})"; break
            if not r or r.get("kind") not in ("plugin", "labs") or not r.get("fst"):
                why = "no other owned instrument for its part"; break
            k = _result_key(r)
            if is_silent(*k) or k == cur:
                why = "no other owned instrument for its part"; break
            try:
                _put_sound(project, ch, r, jc)
            except Exception as e:                        # noqa: BLE001
                why = f"{_sound_desc(k)} couldn't be put on it ({str(e)[:60]})"; break
            cur = k
            try:
                pv = H["fl_preview"](channels=[int(ch)])
            except Exception as e:                        # noqa: BLE001
                return None, (f"{label}: {_and(dead)} made no sound on this PC; swapped to "
                              f"{_sound_desc(k)} but it couldn't be measured ({str(e)[:60]}): listen")
            if pv.get("lufs") is not None and not pv.get("silent"):
                return float(pv["lufs"]), (f"{label}: {_and(dead)} made no sound on this PC; swapped to "
                                           f"{_sound_desc(k)} (silent sounds are skipped from now on: "
                                           "silent_sounds lists them)")
            marked.append(silent_mark(*k)); dead.append(_sound_desc(k))
            if k[0] == "FLEX" and k[1]:
                packs.add(k[1])
    finally:
        steps["silent_swaps"] = round(steps.get("silent_swaps", 0) + time.time() - T, 1)
    if len(dead) > 1 and len({m.split("|")[0] + "|" + m.split("|")[1] for m in marked}) > 1:
        # several sounds from different packs / plugins all silent on this one channel: the channel (its mixer
        # insert, routing, the plugin itself) is the likelier cause, so none is remembered as silent
        silent_unmark(marked)
        return None, (f"{label} has notes but stayed silent with {_and(dead)}: probably not the sounds but the "
                      f"channel (its mixer insert / routing) or the plugin itself - check it; it plays "
                      f"{dead[-1]} now, not balanced")
    return None, (f"{label} has notes but {_and(dead)} made no sound on this PC"
                  + (f" ({why})" if why else "") + ": not balanced - pick another sound for it "
                  "(fl_sound_catalog) and run flp_balance")


def balance_song(project, genre=None):
    """Measure each unit (the drums together, each melodic part) - from ONE stems export when the PC can, else each
    alone (FL's own export, others muted live) - move its mixer fader so the parts sit where the genre wants them
    against the drums, then the master limiter's gain brings the beat to BALANCE_MIX_LUFS."""
    T0 = time.time(); steps = {}
    n0 = H["_renders_here"]() if H.get("_renders_here") else 0
    g = {}
    try:
        g = _mod("flh_compose").genre_data(genre) or {}
    except Exception:
        pass
    targets = dict(BALANCE_TARGETS, **(g.get("balance") or {}))
    units = _balance_units(project)
    st = H["fl_state"]()
    ins_of = {int(c["index"]): c.get("insert") for c in st.get("channels") or []}
    meas, notes = [], []
    per = None
    try:                                                          # a part with no notes yet (the trap starter's Pad:
        per = {int(k): v for k, v in (H["flp_info"](project).get("notes_per_channel") or {}).items()}   # filled by
        empty = [u for u in units if u[1] != "drums" and not any(per.get(c) for c in u[2])]   # arrange) would cost
    except Exception:                                             # an export to learn it's silent (PC bench
        empty = []                                                # 2026-10-22: 5 exports, one for the Pad)
    if empty:
        units = [u for u in units if u not in empty]
        notes.append(f"no notes yet, not measured: {', '.join(u[0] for u in empty)}")
    levels, how = _measure_units(project, units, notes, steps)
    for label, role, chs, ins in units:
        if label not in levels:
            continue
        if levels[label] is None:
            if role != "drums" and per is not None and len(chs) == 1 and per.get(chs[0]):
                lv, msg = _fix_silent(project, genre, chs[0], label, steps)   # has notes, no sound: the next
                notes.append(msg)                                            # sound for its part
                if lv is None:
                    continue
                levels[label] = lv
            else:
                notes.append(f"{label} is silent alone (its sound didn't load, or nothing plays)"); continue
        meas.append((label, role, chs, ins if ins is not None else ins_of.get(chs[0]), float(levels[label])))
    ref = next((m[4] for m in meas if m[1] == "drums"), None)
    if ref is None and meas:
        ref = max(m[4] for m in meas)
    moves = []
    for label, role, chs, ins, lufs in meas:
        if role == "drums" or ins is None or int(ins) <= 0:
            continue
        moves.append([label, int(ins), (ref + targets.get(role, -5.0)) - lufs, lufs])
    cur = {}
    for mv in moves:
        try:
            cur[mv[1]] = float(H["BRIDGE"].call("mixer_set", track=mv[1]).get("volume_db"))
        except Exception:
            cur[mv[1]] = None
    over = max([cur[m[1]] + m[2] - FADER_MAX_DB for m in moves if cur.get(m[1]) is not None] + [0.0])
    drum_ins = sorted({ins_of.get(c) for u in units if u[1] == "drums" for c in u[2]} - {None, 0})
    done = []
    if over > 0.5 and drum_ins:                          # a part needs more than its fader has: the drums come down
        was, failed = {}, []
        for t in drum_ins:
            try:
                c0 = float(H["BRIDGE"].call("mixer_set", track=t).get("volume_db"))
                H["BRIDGE"].call("mixer_set", track=t, volume_db=max(-40.0, c0 - over))
                was[t] = c0
            except Exception as e:                       # noqa: BLE001 - said, and the parts aren't lowered for it
                failed.append(f"track {t}: {str(e)[:50]}")
        if failed:                                       # all drum tracks or none: put back the ones that moved
            for t, c0 in was.items():
                try:
                    H["BRIDGE"].call("mixer_set", track=t, volume_db=c0)
                except Exception:                        # noqa: BLE001
                    failed.append(f"track {t} not put back")
            notes.append(f"the drums couldn't come down {over:.1f} dB to make room ({'; '.join(failed)[:120]}): "
                         "the quiet parts stop at their faders' top")
        else:
            for m in moves:
                m[2] -= over
            ref -= over
            done.append(f"drums {-over:+.1f} dB (room for the quiet parts)")
    for label, t, delta, lufs in moves:
        if abs(delta) < 1.0 or cur.get(t) is None:
            continue
        new = max(-40.0, min(FADER_MAX_DB, cur[t] + max(-24.0, min(18.0, delta))))
        try:
            H["BRIDGE"].call("mixer_set", track=t, volume_db=new)
            done.append(f"{label} {new - cur[t]:+.1f} dB")
        except Exception as e:
            notes.append(f"{label}: {str(e)[:60]}")
    import math
    after = ([ref] if any(m[1] == "drums" for m in meas) else []) + \
            [ref + targets.get(m[1], -5.0) for m in meas if m[1] != "drums"]
    est = 10 * math.log10(sum(10 ** (x / 10) for x in after)) if after else None
    gain = None
    if est is not None:
        gain = max(0.0, min(18.0, BALANCE_MIX_LUFS - est))
        try:
            master = next((t for t in st.get("mixer") or [] if int(t.get("index", -1)) == 0), {})
            slot = next((e["slot"] for e in master.get("effects") or [] if "limiter" in str(e.get("name")).lower()), None)
            if slot is not None and gain >= 1.0:
                txt = _master_gain(int(slot), gain)
                if txt:
                    done.append(f"master limiter gain {txt} (beat at about {BALANCE_MIX_LUFS:g} LUFS)")
                else:
                    notes.append("the master limiter's gain couldn't be set: turn it up by hand")
        except Exception as e:
            notes.append(f"master gain: {str(e)[:60]}")
    try:
        H["fl_save"]()
    except Exception:
        pass
    steps["total"] = round(time.time() - T0, 1)
    return {"measured": {m[0]: m[4] for m in meas}, "moved": done, "notes": notes or None, "how": how,
            "renders": (H["_renders_here"]() - n0) if H.get("_renders_here") else None, "steps_s": steps,
            "took_s": round(time.time() - T0, 1)}


def _balance_pending(project, genre):
    """Record in the song journal that its level balance is still to do (the song wasn't opened in FL)."""
    try:
        flh_store.journal_update(project, {"balance_pending": {"genre": genre, "since": time.strftime("%Y-%m-%d %H:%M")}})
    except Exception:                                     # noqa: BLE001 - the song itself is fine
        pass


def _after_open_balance(project, res):
    """fl_open follow-up: a song with 'balance_pending' in its journal gets its level balance once, in the
    background (FL may still be starting: up to 5 min of waiting)."""
    if not BALANCE_ON or not project or not str(project).lower().endswith(".flp"):
        return
    pc = sys.modules.get("flh_pccheck")                    # fl_pc_check's sandbox songs: it times FL itself
    sbx = getattr(pc, "SANDBOX_DIR", None) if pc else None
    if pc and ((getattr(pc, "_RUN", None) or {}).get("running") or
               (sbx and os.sep + str(sbx) + os.sep in os.path.abspath(project))):
        return
    try:
        jr = flh_store.journal_get(project)
    except Exception:                                     # noqa: BLE001
        return
    pb = jr.get("balance_pending")
    if not isinstance(pb, dict):
        return
    key = os.path.normcase(os.path.abspath(project))
    if _PENDING_RUNS.get(key, 0) > time.time() - 600:     # one start per open burst
        return
    _PENDING_RUNS[key] = time.time()
    start_balance(project, pb.get("genre") or jr.get("genre"), wait_s=300, pending=True)


_PENDING_RUNS = {}


def _start_new_song_balance(project, genre):
    """The balance right after flh_new_song opened the song: recorded as pending first, so a song FL is slow to show
    (the wait gives up after 90 s) still gets it - said in the news, and run by the next fl_open - instead of nothing
    happening after 'being balanced in the background'."""
    _balance_pending(project, genre)
    _PENDING_RUNS[os.path.normcase(os.path.abspath(project))] = time.time()   # this open's run: no second one
    start_balance(project, genre, pending=True)


def _master_gain(slot, gain_db):
    """The master Fruity Limiter's Gain to +gain_db, found by its FL name and checked on its display -> the text FL
    shows, or None. (A plugin-map entry can be stale on a PC; the live name is the truth.)"""
    live = H["fl_plugin_params"](0, slot)
    p = next((q for q in live.get("params") or [] if str(q.get("name", "")).strip().lower() == "gain"), None)
    if p is None:
        return None
    r = H["fl_plugin_set_value"](0, int(p["param"]), float(round(gain_db, 1)), slot)
    got = _mod("flh_mixeng").parse_display(r.get("text"))[0]
    ceil = next((q for q in live.get("params") or [] if str(q.get("name", "")).strip().lower() == "limiter ceiling"), None)
    if ceil is not None:                                  # peaks stay under 0 dBFS (true peak) after the gain
        try:
            H["fl_plugin_set_value"](0, int(ceil["param"]), BALANCE_CEILING_DB, slot)
        except Exception:
            pass
    return r.get("text") if got is not None and abs(got - gain_db) < 1.5 else None


def start_balance(project, genre=None, wait_s=90, pending=False):
    """balance_song in the background, holding FL (foreground calls wait for it); its result comes as news.
    pending: the song journal's 'balance_pending' is cleared once it ran (a song made with open=false)."""
    def run():
        held = False
        try:
            if not H["_wait_open_in_fl"](project, wait_s):
                if pending:
                    H["_news_add"](project, f"{os.path.basename(project)}: the pending level balance waits for "
                                               "FL to show the song (flp_balance runs it by hand)")
                return
            t = time.time()
            while not H["_FL_LOCK"].acquire(timeout=1.0):
                if time.time() - t > 120:
                    return
            held = True
            H["_FL_HOLDER"]["what"] = "balancing the new song's levels (~40 s)"
            r = balance_song(project, genre)
            if pending:
                try:
                    flh_store.journal_update(project, {"balance_pending": None,
                                                       "balanced": time.strftime("%Y-%m-%d %H:%M")})
                except Exception:                         # noqa: BLE001
                    pass
            H["_news_add"](project, f"{os.path.basename(project)}: levels balanced in {r['took_s']} s"
                                       + (f" ({r['renders']} export(s))" if r.get("renders") is not None else "")
                                       + " - " + (", ".join(r["moved"]) or "nothing needed moving")
                                       + (f" ({'; '.join(r['notes'])})" if r.get("notes") else ""))
        except Exception as e:
            H["_news_add"](project, f"{os.path.basename(project)}: level balance stopped ({str(e)[:80]})")
        finally:
            if held:
                H["_FL_HOLDER"]["what"] = None
                H["_FL_LOCK"].release()
    import threading
    threading.Thread(target=run, daemon=True, name="flh-balance").start()


def flh_new_song(genre=None, bpm=None, key=None, name=None, folder=None, vibe=None, kit=None, template=None,
                 starter=True, variations=0, open=True, arrange=True, seed=None, balance=None, ear_candy=True,
                 collection=None):
    T0 = time.time(); steps = {}; notes = []
    balance_asked = balance is not None
    if balance is None:                                   # a render nobody asked for: only when turned on
        balance = bool(H["auto_renders"]()) if H.get("auto_renders") else False
    coll = _new_song_collection(collection)               # a new song joins a collection only when asked
    mark = lambda s, t=[T0]: (steps.__setitem__(s, round(time.time() - t[0], 3)), t.__setitem__(0, time.time()))
    cm = _mod("flh_compose")
    cm._ec_parse(ear_candy)                               # off | organic | subtle | normal | lots | synthetic
    utpl = _user_template(template) if template else None
    genre = genre or (utpl or {}).get("genre")
    if not genre:
        raise ValueError("give a genre (flh_genres lists them) or a saved template")
    rec, g = recipe(genre)
    if not g:
        raise ValueError(f"unknown genre '{genre}' (flh_genres lists them)")
    gname = g["name"]
    prof = {}
    try:                                                  # user-wide keys (palette, edition ...); the collection's
        prof = (flh_store.profile_get(coll) if coll else flh_store.profile_for(None)) or {}   # taste only when joined
    except Exception:
        pass
    tmin, tmax = (g.get("tempo") or {}).get("min", 0), (g.get("tempo") or {}).get("max", 999)
    bpm = float(bpm or (utpl or {}).get("bpm") or rec.get("tempo") or (g.get("tempo") or {}).get("default") or 120)
    if not 10 <= bpm <= 522:                              # FL's range (checked before anything is built)
        raise ValueError(f"tempo {bpm:g} BPM is outside FL's range (10-522)")
    if not tmin <= bpm <= tmax:
        notes.append(f"{bpm:g} BPM is outside {gname}'s usual {tmin}-{tmax} (kept, as asked)")
    key = key or (utpl or {}).get("key") or rec.get("key") or (g.get("keys") or {}).get("default") or "C minor"
    try:
        cm.key_info(key)
    except Exception:
        raise ValueError(f"key '{key}' not understood: e.g. 'F minor', 'C# minor', 'Eb major'")
    key, kn = cm.genre_key(key, g)                        # drill / metal in a major key: its parallel minor
    if kn:
        notes.append(kn)
    vibe_words = [w for w in re.findall(r"[a-z0-9-]+", str(vibe or "").lower()) if len(w) > 2]
    _VIBE["hints"] = _vibe_hints(vibe_words)
    _FLEX_USED.clear()                                    # one preset per channel within a song
    kit_d = _kit(kit or rec.get("kit")) if (kit or rec.get("kit")) else None
    if kit and not kit_d:
        raise ValueError(f"no kit '{kit}' (have: {sorted(kits())})")
    folder = folder or _default_folder()
    H["writable"](os.path.join(folder, "x.flp"))
    dest, stem = _unique_dest(folder, name or f"{gname.title()} {time.strftime('%Y-%m-%d')}")
    base = os.path.join(utpl["_dir"], utpl["name"] + ".flp") if utpl else H["_blank_project"]()
    if not base or not os.path.isfile(base):
        raise FileNotFoundError("FL's empty template wasn't found (flh_setup shows where it looked): set FLH_BLANK to an "
                                "empty .flp, or save one with flh_save_template")
    mark("plan")
    wd, work, wkey = _work(stem)
    _remember_genre(gname)
    try:
        C = _mod("flh_catalog")
        held = C.hold(feature_s=BUILD_FEATURE_S) if hasattr(C, "hold") else contextlib.nullcontext()
    except Exception:
        held = contextlib.nullcontext()
    try:
        with held:                                         # the indexes are checked once for the whole build
            res = _build(locals())
    finally:
        _cleanup(wd, wkey)
    return res


def _new_song_collection(collection):
    """flh_new_song's collection=: None / False -> '' (the song belongs to no collection), True -> the active
    collection (an error when there is none), a name -> that collection (a new profile when it doesn't exist)."""
    if collection is None or collection is False or (isinstance(collection, str) and
                                                     collection.strip().lower() in ("", "none", "no", "false", "off")):
        return ""
    if collection is True or (isinstance(collection, str) and collection.strip().lower() in ("true", "yes", "active")):
        name = flh_store.profile_name()
        if not name:
            raise ValueError("collection=true but no collection is active: give its name (flh_profile lists it)")
        return name
    return flh_store.safe_name(str(collection).strip())


BUILD_FEATURE_S = 6.0      # audio-feature measuring a new song may spend in total (the warm-up fills the rest later)


def _remember_genre(gname):
    """Recently built genres go first in the background feature warm-up."""
    try:
        st = H["_state"]()
        rec = [gname] + [x for x in (st.get("recent_genres") or []) if x != gname]
        H["_state"]({"recent_genres": rec[:5]})
    except Exception:
        pass


def _build(L):
    """The build itself (in a scratch copy in the state folder; the finished file goes to its folder at the end)."""
    rec, g, genre, gname, bpm, key, prof = L["rec"], L["g"], L["genre"], L["gname"], L["bpm"], L["key"], L["prof"]
    utpl, work, dest, stem, steps, notes, mark = L["utpl"], L["work"], L["dest"], L["stem"], L["steps"], L["notes"], L["mark"]
    vibe_words, kit_d, starter, variations, seed = L["vibe_words"], L["kit_d"], L["starter"], int(L["variations"] or 0), L["seed"]
    d = H["_load"](L["base"])
    d = _ensure_playlist(d)
    L["base_structure"] = _structure(d)
    ev0 = H["_events"](d)                                 # open in song mode (event 9 = FL's 'LoopActive'): pressing
    k9 = next((k for k, e in enumerate(ev0) if e["id"] == 9 and e["len"] == 1), None)   # play plays the arrangement
    if k9 is not None and H["_payload"](d, ev0[k9]) != b"\x01":   # (FL's Empty.flp opens in pattern mode)
        d = H["_splice"](d, ev0, [(k9, k9 + 1, bytes([9, 1]))])
    ev = H["_events"](d)
    base_types = _chan_types(d, ev)
    chans_out, journal_ch, registry = [], {}, {"channels": {}, "drum_map": {}, "tracks": {}}
    edition_notes = set()
    mixer_plan = None
    labs_jobs, tune = [], []
    if utpl:                                               # a saved user template: its channels stay as they are
        for c in utpl.get("channels") or []:
            journal_ch[str(c["index"])] = {k: c.get(k) for k in ("role", "name", "transpose") if c.get(k)}
            if c.get("role"):
                registry["channels"].setdefault(c["role"], c["index"])
                if c["role"] in DRUM_ROLES:
                    registry["drum_map"][c["role"]] = [c["index"], 60, False]
        registry.update({k: v for k, v in (utpl.get("compose") or {}).items()
                         if k in ("channels", "drum_map", "tracks", "transpose")})
        for k, jc in journal_ch.items():                   # pitched samplers keep their sounding-pitch offset
            if jc.get("transpose"):
                registry.setdefault("transpose", {})[k] = jc["transpose"]
        chans_out = [f"{c['index']} {c.get('name')} ({c.get('role') or '?'})" for c in utpl.get("channels") or []]
    else:
        specs = [dict(c) for c in rec.get("channels") or []]
        cap = _insert_capacity(d)
        ins, buses, sends, mnotes, ins_of = _plan_inserts(specs, rec, cap)
        notes += mnotes
        first = 1
        for i, x in enumerate(ins):
            x["track"] = first + i
        bus_track = {b["name"]: first + len(ins) + i for i, b in enumerate(buses)}
        send_track = {s["name"]: first + len(ins) + len(buses) + i for i, s in enumerate(sends)}
        used = []
        reuse = {c for c, t in base_types.items() if t == 0}   # FL template samplers (e.g. 'Basic'): reused for drums
        plain_words = _mod("flh_score").wordy()
        mark("read_base")
        tsearch = 0.0
        level_notes = []
        for si, spec in enumerate(specs):
            role = spec["role"]; family = spec.get("family") or role
            color = spec.get("color") or _palette_color(rec, role, family, prof)
            x = ins_of.get(id(spec))
            insert = x["track"] if x else 0
            t = time.time()
            r = _resolve(spec, rec, genre, vibe_words, kit_d, used, edition_notes)
            tsearch += time.time() - t
            if r.get("level_db") is not None and "level_db" not in spec:   # loud and quiet samples start balanced
                tgt = SAMPLE_RMS_TARGET.get(role, SAMPLE_RMS_TARGET.get(family))
                if tgt is not None:
                    base = (rec.get("levels_db") or {}).get(family, (rec.get("levels_db") or {}).get(role, 0)) or 0
                    adj = max(-12.0, min(12.0, 5.6 - base, tgt - float(r["level_db"])))   # a fader tops at +5.6 dB
                    if abs(adj) >= 1.5:
                        spec["level_db"] = round(base + adj, 1)
                        level_notes.append(f"{spec['name']} {adj:+.0f} dB")
            ev = H["_events"](d)
            if r["kind"] in ("sample", "empty"):
                src = None
                if reuse and r["kind"] == "sample" and role in DRUM_ROLES:
                    src = min(reuse); reuse.discard(src)
                    d = _chan_edit(d, src, {H["EV_SAMPLE_PATH"]: _txt(_stored_sample_path(r["path"])),
                                            H["EV_DISPLAY_NAME"]: _txt(spec["name"]),
                                            H["EV_CHANNEL_COLOR"]: bytes(list(_rgb(color)) + [0]),
                                            EV_INSERT: struct.pack("<h", insert)})
                    ch = src
                else:
                    srcs = sorted(c for c, tt in _chan_types(d, ev).items() if tt == 0 and c not in reuse)
                    d, ch = _append_channel(d, _sampler_block(d, ev, r.get("path"), spec["name"], color, insert,
                                                              src=srcs[0] if srcs else None))
                if r.get("path"):
                    used.append(r["path"])
                    if role in ("bass", "chords", "melody", "counter", "pad") or family == "808":
                        if r.get("pitch") is None and r.get("root") is None:
                            pm, _how = _sample_pitch(r["path"])     # not cached yet: measure it now
                            if pm is not None:
                                r["pitch"] = pm
                        semis = _tune_semis(r.get("pitch"), r.get("root"))   # pitched sampler: notes to C5 root
                        tune.append((ch, semis, r.get("pitch"), r.get("root"), spec["name"]))
            else:
                blk, plug = _plugin_block(d, ev, r["fst"], spec["name"], color, insert)
                d, ch = _append_channel(d, blk)
                if r["kind"] == "labs":
                    labs_jobs.append((ch, r, spec))
                elif r.get("plugin") == "FLEX" and not r.get("flex_default"):
                    try:                                   # FLEX 808 presets sound 2 octaves below the written note
                        fx = _mod("flh_flex")
                        off = fx.sounding_offset(r.get("preset") or r.get("_preset") or "", r.get("pack"))
                    except Exception:
                        off = 0
                    if off:
                        tune.append((ch, off, None, None, spec["name"]))
            line = f"{ch} {spec['name']}: {r['label']}"
            if plain_words:                                # colour is never the only cue: say its name too
                line += f" [colour {_mod('flh_score').color_text(color)}]"
            if x and x["track"]:
                line += f" -> insert {x['track']}"
            if r.get("hint"):
                line += f" ({r['hint']})"
            if r.get("skipped"):
                line += f" [skipped: {'; '.join(r['skipped'])}]"
            if r.get("note"):
                line += f" [{r['note']}]"
            chans_out.append(line)
            journal_ch[str(ch)] = {"role": role, "name": spec["name"], "mixer": insert or None,
                                   "sound": r.get("path") or r.get("fst") or None, "plugin": r.get("plugin"),
                                   "spec": si}           # the recipe channel: a silent sound's swap re-ranks it
            if r["kind"] in ("plugin", "labs") and (r.get("pack") or r.get("preset") or r.get("flex_default")):
                k = _result_key(r)
                journal_ch[str(ch)].update(pack=k[1] or None, preset=k[2] or None)
            if spec.get("notes"):
                journal_ch[str(ch)]["note"] = spec["notes"]
            crole = "bass" if family == "808" else role
            if role in DRUM_ROLES:
                registry["drum_map"][role] = [ch, 60, False]
                registry["channels"].setdefault(role, ch)
            elif role in ("bass", "chords", "melody", "counter", "pad", "fx", "texture", "vocal"):
                registry["channels"].setdefault(crole, ch)
        steps["sounds_search"] = round(tsearch, 3)
        for ch, semis, pitch, root, cname in tune:         # pitched samplers: the offset is known before any note
            if semis:                                      # is written, so every writer (starter or later) uses it
                C = _mod("flh_catalog")
                journal_ch[str(ch)]["transpose"] = semis
                registry.setdefault("transpose", {})[str(ch)] = semis
                if pitch is None and root is None:         # a FLEX 808 preset (no sample pitch to name)
                    what = f"the {cname} FLEX 808 preset sounds {semis // 12} octaves below the written note"
                else:
                    nm = (C.PC_NAMES[pitch % 12] + str(pitch // 12 - 1)) if pitch is not None else f"{root} (from its name)"
                    what = f"the {cname} sample is pitched {nm}"
                notes.append(f"{what}: notes on channel {ch} are written {semis:+d} "
                             f"semitones from the pitch you hear, so it plays in {key} (journal channels.{ch}.transpose;"
                             " the composer and flp_score take it into account)")
            elif pitch is None and not root:
                notes.append(f"{cname} tuning not checked (the sample's pitch couldn't be read): listen")
        mark("channels")
        if level_notes:
            notes.append("start levels set from each sample's measured loudness (so quiet samples aren't buried): "
                         + ", ".join(level_notes))
        H["_save"](d, work)
        # ---- mixer: names, colors, routing, levels (one call), then effects
        tracks = []
        lv = rec.get("levels_db") or {}
        pal_bus = (rec.get("palette") or {}).get("bus") or {}
        up = (prof.get("palette") or {}) if isinstance(prof.get("palette"), dict) else {}
        for x in ins:
            m0 = x["members"][0]
            bus = next((b["name"] for b in buses if m0.get("bus") == b["name"]), None) or \
                next((b["name"] for b in buses if m0["role"] in b.get("roles", []) or m0.get("family") in b.get("roles", [])), None)
            spec = {"track": x["track"], "name": x["name"],
                    "color": m0.get("color") or _palette_color(rec, m0["role"], m0.get("family"), prof)}
            db = m0.get("level_db", lv.get(m0.get("family") or m0["role"], lv.get(m0["role"])))
            if db is not None:
                spec["volume_db"] = db
            if bus in bus_track:
                spec.update(route_to=[bus_track[bus]], unroute=[0])
            x["bus"] = bus
            tracks.append(spec)
        for b in buses:
            tracks.append({"track": bus_track[b["name"]], "name": b["name"] + " Bus",
                           "color": up.get(b["name"].lower()) or pal_bus.get(b["name"]) or "#303030"})
        for s in sends:
            tracks.append({"track": send_track[s["name"]], "name": s["name"],
                           "color": (rec.get("palette") or {}).get("send") or "#2E2E3E"})
        if tracks:
            H["flp_mixer"](work, work, tracks)
        mark("mixer")
        fx_done, fx_skip = [], []
        have_fx = _effects(H["_load"](work))
        for t, plugs in [(0, rec.get("master") or [])] + [(send_track[s["name"]], [s]) for s in sends]:
            used_slots = {s for (tt, s) in have_fx if tt == t}
            for p in plugs:
                plug = p["plugin"]
                norm = _mod("flh_catalog")._norm
                same = [f.get("plugin") for (tt, _), f in have_fx.items() if tt == t and (
                    norm(f.get("plugin")) == norm(plug) or (_is_limiter(plug) and _is_limiter(f.get("plugin"))))]
                if same:
                    fx_done.append(f"{same[0]} (already on {'master' if t == 0 else 'track ' + str(t)}: no {plug} added)")
                    continue
                own = _owned(plug)
                e = _find_plugin(plug)
                if own is not True or not e or not e["default"]:
                    fx_skip.append(f"{plug}: {'not owned' if own is not True else 'not in FL plugin database'}"); continue
                slot = next((s for s in range(10) if s not in used_slots), None)
                if slot is None:
                    fx_skip.append(f"{plug}: no free slot"); continue
                if _is_limiter(plug):
                    free = [s for s in range(10) if s not in used_slots]
                    slot = free[-1] if t == 0 else slot                  # the limiter goes last on the master
                try:
                    H["flp_load_plugin"](work, work, e["default"], track=t, slot=slot)
                    used_slots.add(slot)
                    fx_done.append(f"{plug} on {'master' if t == 0 else 'track ' + str(t)} slot {slot + 1}")
                except Exception as ex:
                    fx_skip.append(f"{plug}: {str(ex)[:60]}")
        mark("effects")                                    # PC-VERIFY: plugin database .fst files load as effects
        for ch, r, spec in labs_jobs:
            # PC-VERIFY: flp_instrument_sound switches a fresh INSTRUMENT (plugin database state) to a LABS pack
            try:
                H["flp_instrument_sound"](work, work, ch, r["pack"], r.get("preset"))
            except Exception as ex:
                alt = dict(spec)
                fam = spec.get("family") or spec["role"]
                alt["choices"] = [c for c in (spec.get("choices") or (rec.get("choices") or {}).get(fam) or [])
                                  if "labs" not in c]
                r2 = _resolve(alt, rec, genre, vibe_words, kit_d, [], edition_notes)
                if r2.get("fst"):
                    H["flp_load_plugin"](work, work, r2["fst"], channel=ch)
                    H["flp_style_channels"](work, work, [{"channel": ch, "name": spec["name"]}])
                    why = ("FL's plugin database only lists INSTRUMENT by name, so its LABS packs can't be picked "
                           "in the file" if "no INSTRUMENT state" in str(ex) else str(ex)[:60])
                    chans_out[:] = [(f"{ch} {spec['name']}: {r2['label']} (instead of LABS {r['pack']}: {why}; "
                                     "you can switch it in INSTRUMENT's window)") if x.startswith(f"{ch} ") else x
                                    for x in chans_out]
                    k2 = _result_key(r2)
                    journal_ch[str(ch)].update(sound=r2["fst"], plugin=r2.get("plugin"), pack=k2[1] or None,
                                               preset=(k2[2] if r2.get("plugin") == "FLEX" else None))
                else:
                    notes.append(f"channel {ch}: INSTRUMENT couldn't be switched to {r['pack']} ({str(ex)[:80]}) and "
                                 "nothing else fits: pick the pack in INSTRUMENT's window")
        if labs_jobs:
            mark("labs")
        mixer_plan = {"inserts": [f"{x['track']} {x['name']}" + (f" -> {x['bus']} Bus" if x.get("bus") in bus_track else "")
                                  for x in ins],
                      "buses": [f"{bus_track[b['name']]} {b['name']} Bus" for b in buses],
                      "sends": [f"{send_track[s['name']]} {s['name']}" for s in sends], "effects": fx_done,
                      "inserts_free": cap - len(ins) - len(buses) - len(sends)}
        if fx_skip:
            mixer_plan["effects_skipped"] = fx_skip
        if sends:
            notes.append("Reverb/Delay send tracks are ready but nothing is routed to them yet (send levels at file "
                         "level aren't verified): route with the mixer's send knobs, or ask")
    # ---- tempo, info, playlist tracks
    d = H["_load"](work) if os.path.isfile(work) else d
    H["_save"](d, work)
    H["flp_set_tempo"](work, bpm)
    try:
        H["flp_project_info"](work, work, genre=gname, title=stem)
    except Exception:
        pass
    order = list(rec.get("playlist") or COMPOSE_ORDER)
    pnames = rec.get("playlist_names") or {}
    have_roles = {"drums" if r in DRUM_ROLES else ("bass" if r == "808" else r) for r in
                  [v.get("role") for v in journal_ch.values()] if r}
    order = [r for r in order if r in COMPOSE_ORDER[:4] or r in have_roles or r in ("fx", "vocal")]
    if not starter:
        order = ["sections"] + order
    tracks = []
    for i, r in enumerate(order):
        col = "#303030"
        if r == "drums":
            col = _palette_color(rec, "kick", None, prof)
        elif r != "sections":
            col = _palette_color(rec, r, None, prof)
        tracks.append({"track": i + 1, "name": pnames.get(r, r.title()), "color": col})
    registry["tracks"] = {r: i + 1 for i, r in enumerate(order) if r != "sections"}
    d = H["_load"](work)
    d, nstyled = _style_tracks(d, tracks)
    H["_save"](d, work)
    mark("tempo_playlist")
    # ---- journal (for the scratch copy: the composer reads its registry from it)
    plan = _section_plan(g, rec)
    jset = {"genre": gname, "key": key, "bpm": bpm, "channels": journal_ch, "compose": registry,
            "template": (utpl or {}).get("name") or os.path.basename(rec.get("_file", "")).replace(".json", "") or gname,
            "collection": L.get("coll") or "",           # '' = made outside any collection (its taste stays out)
            "created": {"by": "flh_new_song", "when": time.strftime("%Y-%m-%d %H:%M"), "vibe": " ".join(vibe_words) or None,
                        "kit": (kit_d or {}).get("name")}}
    flh_store.journal_update(work, set=jset)
    starter_res = None
    cm = _mod("flh_compose")
    warnings = L.setdefault("warnings", [])
    if starter:
        main = _main_section(g, plan)
        try:
            comp = H["TOOLS"]["flp_compose"][0]
            nvar = max(1, min(6, 1 + variations))
            with cm.scratch(work):                         # a new file in the build folder: FL can't have it open
                r = comp(work, "beat", genre=gname, key=key, section=main, variations=nvar, seed=seed,
                         realism=cm.realism_for_genre(gname))
            if not any((H["flp_info"](work).get("notes_per_channel") or {}).values()):
                raise RuntimeError("the beat was composed but no notes reached the file"
                                   + (f" ({str(r.get('verify_problems'))[:100]})" if r.get("verify_problems") else ""))
            if r.get("verified") is False:
                warnings.append(f"the starter beat didn't read back as planned: {str(r.get('verify_problems'))[:160]}")
            if nvar == 1:                                  # the recipe's support layers (a lofi pad...) under the beat
                for lay in rec.get("layers") or []:
                    role = str((lay or {}).get("role") or "")
                    ch = next((int(k) for k, v in (journal_ch or {}).items() if (v or {}).get("role") == role), None)
                    if not role or ch is None:
                        continue
                    try:
                        with cm.scratch(work):
                            comp(work, "layer", roles=[role], section=main, level=lay.get("level") or "support",
                                 genre=gname, key=key, seed=seed, realism=False,      # no automation
                                 channel={role: ch})                                   # clips in a starter
                    except Exception as ex:
                        warnings.append(f"no {role} part ({type(ex).__name__}: {str(ex)[:90]}) - "
                                        f"flp_compose(project, what='layer', roles=['{role}']) writes one")
            vs = r.get("variations") or []
            starter_res = {"section": main, "bars": r.get("bars"),
                           "beat": ([v.get("about") for v in vs] if nvar == 1 else
                                    [f"{v.get('label')}: {v.get('about')}" for v in vs]) or r.get("summary")}
            if nvar == 1 and isinstance(starter_res["beat"], list) and len(starter_res["beat"]) == 1:
                starter_res["beat"] = starter_res["beat"][0]
            if nvar > 1:
                starter_res["audition"] = [{"variation": v.get("label"), **(v.get("listen") or {})} for v in vs]
            mark("compose")
            if variations == 0 and L.get("arrange", True):
                try:
                    with cm.scratch(work):
                        a = H["TOOLS"]["flp_arrange_song"][0](work, genre=gname, mode="replace", seed=seed,
                                                              fx_dir=os.path.join(os.path.dirname(dest), "_fx"),
                                                              ear_candy=L.get("ear_candy", True))
                    plain = _mod("flh_score").wordy()
                    starter_res["arranged"] = ("; " if plain else " | ").join(
                        (f"{x.get('section')} at bar {x.get('bar', 0):g} ({', '.join(x.get('layers') or []) or 'silent'})"
                         if plain else f"{x.get('section')}@{x.get('bar', 0):g} ({' '.join(x.get('layers') or [])})")
                        for x in a.get("sections") or []) or True
                except Exception as ex:
                    warnings.append(f"the beat is a loop at bar 1: laying out the full form failed ({type(ex).__name__}: "
                                    f"{str(ex)[:90]}) - flp_arrange_song(project) does it")
                mark("arrange")
        except Exception as ex:
            warnings.append(f"no starter beat ({type(ex).__name__}: {str(ex)[:120]}): the project is set up with its "
                            "sounds but no notes - flp_compose(project, what='beat') writes one")
            starter_res = None
    # ---- sections + markers
    jr = flh_store.journal_get(work)
    secs = jr.get("sections") or ([] if starter_res else plan)
    d = H["_load"](work)
    if not starter_res:
        cols = (prof.get("palette") or {}).get("sections") if isinstance(prof.get("palette"), dict) else None
        cols = cols or (rec.get("palette") or {}).get("sections") or ["#3D2A5C"]
        strack = 1
        if starter:                                        # the starter failed: section blocks go on their own track
            strack = len(order) + 1
            d, _ = _style_tracks(d, [{"track": strack, "name": pnames.get("sections", "Sections"), "color": "#303030"}])
        names = {}
        for i, s in enumerate(plan):
            if s["name"] not in names:
                d, pid = _new_pattern(d, s["name"], cols[len(names) % len(cols)])
                names[s["name"]] = pid
            d = _place_pattern(d, names[s["name"]], s["bar"], s["end_bar"] - s["bar"], strack)
            s["pattern"] = names[s["name"]]
        secs = plan
    d, nmark = _markers(d, [(s["bar"], s["name"]) for s in secs])
    H["_save"](d, work)
    mark("sections")
    # ---- the finished file + its journal
    fin = H["_load"](work)
    got, want = _structure(fin), L.get("base_structure")
    if want and any(got[k] != want[k] for k in ("arrangements", "mixer tracks")):
        raise RuntimeError(f"internal error: building the song changed the project layout {want} -> {got}; "
                           "nothing was written (please send flh_bug_report)")
    H["_save"](fin, dest)
    jr = flh_store.journal_get(work)
    jr["sections"] = secs
    jr["channels"] = dict(journal_ch, **{c: x for c, x in (jr.get("channels") or {}).items()   # + the arrangement's FX
                                         if isinstance(x, dict) and x.get("fx_kind")})
    if registry.get("transpose"):
        jr.setdefault("compose", {})["transpose"] = registry["transpose"]
    for k in ("journal_file", "updated", "song"):
        jr.pop(k, None)
    flh_store.journal_update(dest, set=jr)
    if L.get("coll"):                                     # joined: the collection lists it (its taste applies)
        try:
            flh_store.profile_set(L["coll"], {"songs": {stem: {"project": dest}}}, activate=False)
        except Exception as ex:
            warnings_ = L.setdefault("warnings", [])
            warnings_.append(f"the song wasn't added to collection '{L['coll']}' ({str(ex)[:60]}): its journal "
                             "names it, so the collection's rules still apply")
    mark("save")
    out = {"project": dest, "genre": gname, "bpm": bpm, "key": key, "bars": max([s["end_bar"] for s in secs] + [1]) - 1,
           "collection": L.get("coll") or None,
           "sections": (", ".join(f"{s['name']} at bar {s['bar']:g}" for s in secs) if _mod("flh_score").wordy() else
                        " | ".join(f"{s['name']}@{s['bar']:g}" for s in secs)), "channels": chans_out}
    if mixer_plan:
        out["mixer"] = mixer_plan
    if starter_res:
        out["starter"] = starter_res
        if isinstance(starter_res.get("arranged"), str):
            out.pop("sections", None)                      # the arrangement line says the same, with layers
    trial = sorted({p for o, p in edition_notes if o == "trial"})
    unknown = sorted({p for o, p in edition_notes if o != "trial"})
    if trial:
        ed = H["flh_edition"]()[0] if H.get("flh_edition") else None
        notes.append(f"not used (trial in {ed or 'your'} edition): {', '.join(trial)}")
    if unknown:
        notes.append(f"not used because ownership is unknown: {', '.join(unknown)} - tell fl-headless your FL edition "
                     "(flh_setup(action='apply', fl_edition=...)) and it can use them")
    if (L["base"] or "").lower().endswith(".flp") and not utpl:
        out["base"] = os.path.basename(L["base"])
    left = sorted(c for c in base_types if not utpl and c not in {int(k) for k in journal_ch})
    if left:
        notes.append(f"channels kept from FL's template: {left}")
    out["file_s"] = round(time.time() - L["T0"], 2)
    want_balance = L.get("balance", True) and BALANCE_ON
    if not want_balance and not L.get("balance_asked") and L.get("open", True):
        out["levels"] = ("start levels from each sound's known loudness - no render (flp_balance(project) evens them "
                         "with one export when you want; flh_auto_renders(enable=true) does it after every new song)")
    if want_balance and not (L.get("open", True) and H.get("IS_WINDOWS")):
        _balance_pending(dest, gname)                      # runs once when the song is opened with fl_open
        out["levels"] = "balance pending: runs in the background the first time fl_open opens this song"
    if L.get("open", True):
        try:
            if H.get("IS_WINDOWS"):
                r = H["fl_open"](dest)
                out["opened"] = True
                if isinstance(r, dict) and r.get("warning"):
                    notes.append(r["warning"])
                if want_balance:
                    _start_new_song_balance(dest, gname)
                    out["levels"] = ("being balanced in the background (stems from one export where FL allows, else "
                                     "each part alone; faders set); the result comes with the next tool result")
            else:
                out["opened"] = False; notes.append("not opened: FL runs on Windows only (the file is ready)")
        except Exception as ex:
            out["opened"] = False
            warnings.append(f"the song is saved but couldn't be opened in FL ({str(ex)[:80]}): fl_open(project)")
            if want_balance:
                _balance_pending(dest, gname)
                out["levels"] = "balance pending: runs in the background when fl_open opens this song"
        mark("open")
    if notes:
        out["notes"] = notes
    out["_secs"] = secs
    out["try_next"] = _try_next(out, starter_res, variations, vibe_words, dest)
    out.pop("_secs", None)
    out["steps_s"] = steps
    if warnings:                                           # a failed step is the first thing a reader sees
        out = {"ok": False, "warning": warnings, **out}
    return out


def _try_next(out, starter_res, variations, vibe_words, dest):
    t = []
    if starter_res and variations:
        t.append("fl_listen the variations, then flp_compose(what='pick', variation='B') and flp_arrange_song")
    elif starter_res:
        sec = next((s_ for s_ in out.get("_secs") or [] if s_["name"] == starter_res.get("section")), None)
        a, b = (sec["bar"], min(sec["end_bar"], sec["bar"] + 8)) if sec else (1, 9)
        t.append(f"fl_listen(start_bar={a:g}, end_bar={b:g}) to hear the {starter_res.get('section') or 'beat'}; "
                 "flp_adjust(words='dustier' | 'harder' | 'more bounce')")
    else:
        t.append("flp_compose(project, what='beat') writes a starter beat into these channels")
    hint = _mod("flh_score").call_hint
    raw = _mod("flh_score").Raw
    t.append("swap a sound: fl_sound_catalog(kind='kick', query='punchier', genre=...) then "
             + hint("flp_load_sample", {"project": raw("project"), "output": raw("project"), "sample": raw("<its path>"),
                                  "channel": raw("N")}, H))
    t.append(hint("flh_save_template", {"project": raw("project"), "name": "My " + str(out["genre"])}, H)
             + " to reuse this setup")
    if vibe_words and _VIBE.get("hints"):
        t.append(f"flp_adjust(project, words='{' '.join(vibe_words)}') pushes the notes toward the vibe too")
    return t


# ============================================================================= user templates
def flh_save_template(project, name, keep_notes=False, fl_templates=False, overwrite=False):
    """Save a project as the user's reusable template: a copy (notes and pattern clips cleared unless keep_notes) +
    a recipe of its channels/roles, in fl-headless's state folder; fl_templates=true also puts the copy in FL's
    user Templates folder (File > New from template) when that folder is writable. An existing template of that
    name is never replaced unless overwrite=true (the old one is backed up first); a template in FL's folder that
    fl-headless didn't write is never replaced: the copy gets a free name instead."""
    if not os.path.isfile(project):
        raise FileNotFoundError(project)
    clean = re.sub(r'[<>:"/\\|?*]', "", str(name)).strip()
    if not clean:
        raise ValueError("give the template a name")
    base_dir = os.path.join(os.path.dirname(H["STATE"]), "templates", clean)
    if os.path.isfile(os.path.join(base_dir, "template.json")) and not overwrite:
        raise ValueError(f"a template named '{clean}' already exists: pick another name, or overwrite=true (the old "
                         "one is kept as a backup in its folder)")
    d = H["_load"](project)
    ev = H["_events"](d)
    cm = _mod("flh_compose")
    chans = cm.channels_of(d, ev)
    jr = flh_store.journal_get(project)
    reg = jr.get("compose") or {}
    roles = {}
    for r, c in (reg.get("channels") or {}).items():
        roles[int(c[0] if isinstance(c, list) else c)] = r
    for r, c in (reg.get("drum_map") or {}).items():
        roles[int(c[0])] = r
    for k, v in (jr.get("channels") or {}).items():
        if v.get("role") and str(k).isdigit():
            roles.setdefault(int(k), v["role"])
    offs = _mod("flh_score").sounding_offsets(jr)          # pitched samplers keep their offset in the template
    for c, info in chans.items():
        if c not in roles and info.get("type") in (0, 2):
            r = cm._role_from_text(" ".join(str(x or "") for x in (info.get("name"), info.get("plugin"), info.get("sample"))))
            if r:
                roles[c] = r
    edits = []
    cleared = {"notes": 0, "clips": 0}
    if not keep_notes:
        for k, e in enumerate(ev):
            if e["id"] == H["EV_NOTES"] and e["len"] % 24 == 0 and e["len"]:
                cleared["notes"] += e["len"] // 24
                edits.append((k, k + 1, H["_data_event"](H["EV_NOTES"], b"")))
            elif e["id"] == H["EV_PLAYLIST"] and e["len"] % H["CLIP_RECORD"] == 0 and e["len"]:
                p = H["_payload"](d, e); keep = []
                for i in range(0, len(p), H["CLIP_RECORD"]):
                    item = struct.unpack("<H", p[i + 6:i + 8])[0]
                    if item >= H["PATTERN_BASE"]:
                        cleared["clips"] += 1
                    else:
                        keep.append(p[i:i + H["CLIP_RECORD"]])
                edits.append((k, k + 1, H["_data_event"](H["EV_PLAYLIST"], b"".join(keep))))
    data = H["_splice"](d, ev, edits) if edits else d
    H["_events"](data)                                     # parse check
    base = base_dir
    os.makedirs(base, exist_ok=True)
    flp = os.path.join(base, clean + ".flp")
    backup = None
    if os.path.isfile(flp):                                # overwrite=true: keep the old one next to it
        backup = os.path.join(base, f"{clean} (before {time.strftime('%Y%m%d-%H%M%S')}).flp")
        shutil.copyfile(flp, backup)
        tj = os.path.join(base, "template.json")
        if os.path.isfile(tj):
            shutil.copyfile(tj, backup[:-4] + ".template.json")
    tmp = flp + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, flp)
    info = H["flp_info"](flp)
    recipe_ = {"format": "flh-user-template/1", "name": clean, "genre": jr.get("genre"), "bpm": info.get("tempo_bpm"),
               "key": jr.get("key"), "from": os.path.abspath(project), "saved": time.strftime("%Y-%m-%d %H:%M"),
               "keep_notes": bool(keep_notes),
               "channels": [{k: v for k, v in {"index": c, "name": i.get("name"), "role": roles.get(c),
                                               "type": i.get("type"), "plugin": i.get("plugin"), "sample": i.get("sample"),
                                               "transpose": offs.get(c)}.items() if k != "transpose" or v}
                            for c, i in sorted(chans.items())],
               "sections": jr.get("sections") or [], "compose": {k: reg.get(k) for k in ("channels", "drum_map", "tracks")
                                                                 if reg.get(k)}}
    if offs:
        recipe_["compose"]["transpose"] = {str(c): o for c, o in sorted(offs.items())}
    with open(os.path.join(base, "template.json"), "w", encoding="utf-8") as f:
        json.dump(recipe_, f, indent=1)
    out = {"template": clean, "stored": flp, "channels": len(chans), "roles": {str(k): v for k, v in sorted(roles.items())},
           "cleared": cleared if not keep_notes else None, "use": f"flh_new_song(template='{clean}')",
           "replaced": {"backup": backup} if backup else None,
           "sounding_offsets": {str(c): o for c, o in sorted(offs.items())} or None}
    if fl_templates:
        tdir = _fl_templates_dir()
        dst = os.path.join(tdir, clean, clean + ".flp") if tdir else None
        try:
            if not dst:
                raise FileNotFoundError("FL's user data folder wasn't found")
            dst, how = _fl_template_dest(dst, flp, overwrite)
            H["writable"](dst)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(flp, dst)
            with open(os.path.join(os.path.dirname(dst), ".fl-headless"), "w", encoding="utf-8") as f:
                f.write("made by fl-headless flh_save_template; may be replaced by it with overwrite=true\n")
            out["fl_template"] = dst
            if how:
                out["fl_template_note"] = how
        except PermissionError:
            out["fl_template"] = None
            out["note"] = (f"FL's Templates folder ({tdir}) isn't a writable location for fl-headless yet, so only the "
                           "fl-headless copy was saved (it works with flh_new_song)")
        except Exception as ex:
            out["fl_template"] = None
            out["note"] = f"not copied to FL's Templates folder: {str(ex)[:80]}"
    return {k: v for k, v in out.items() if v is not None or k == "fl_template"}


def _fl_template_dest(dst, src, overwrite):
    """Where the copy goes in FL's Templates folder: dst when free (or holding fl-headless's own earlier copy of
    the same name and overwrite=true, after a Recycle Bin backup); else a free '<name> 2' etc. -> (path, note)."""
    if not os.path.exists(dst):
        return dst, None
    stamp = os.path.join(os.path.dirname(dst), ".fl-headless")
    ours = os.path.isfile(stamp)
    if overwrite and ours:
        try:
            H["files_recycle"]([dst])                     # the old copy goes to the Recycle Bin (restorable)
            return dst, "the previous copy fl-headless made there went to the Recycle Bin"
        except Exception as e:
            note = f"the previous copy couldn't be moved to the Recycle Bin ({str(e)[:60]})"
    else:
        note = ("a template of that name is already in FL's Templates folder" +
                ("" if ours else " (not made by fl-headless)"))
    folder, nm = os.path.split(os.path.dirname(dst))
    for n in range(2, 200):
        alt = os.path.join(folder, f"{nm} {n}", f"{nm} {n}.flp")
        if not os.path.exists(os.path.dirname(alt)):
            return alt, note + f": saved as '{nm} {n}' instead (nothing was replaced)"
    raise RuntimeError("no free template name in FL's Templates folder")


def _fl_templates_dir():
    """FL's user templates folder: <FL user data>/Projects/Templates (FL lists it under File > New from template)."""
    # PC-VERIFY: FL 2026 lists <user data>\Projects\Templates\<name>\<name>.flp under File > New from template
    try:
        u = H["_fl_userdata"]()
    except Exception:
        u = None
    if not u:
        return None
    for sub in (("Projects", "Templates"), ("FL Studio", "Projects", "Templates")):
        p = os.path.join(u, *sub)
        if os.path.isdir(os.path.dirname(p)):
            return p
    return os.path.join(u, "Projects", "Templates")


# ============================================================================= welcome
def flh_welcome(force=False):
    """A short first-use message: is everything set up, what to try (tailored to what's installed), the promises."""
    st = H["_state"]()
    first = not st.get("welcomed")
    try:
        chk = H["TOOLS"]["flh_setup"][0]("check")
    except Exception as e:
        chk = {"ready": False, "next_steps": [f"flh_setup() failed: {str(e)[:80]}"]}
    have = {"labs": [], "flex": False, "samples": 0, "genres": []}
    try:
        cat = H["TOOLS"]["fl_sound_catalog"][0]()
        have["labs"] = (cat.get("by_source") or {}).get("LABS", 0)
        have["flex"] = bool((cat.get("by_source") or {}).get("FLEX")) or bool(_find_plugin("FLEX"))
        have["samples"] = (cat.get("samples") or {}).get("count", 0)
    except Exception:
        pass
    try:
        have["genres"] = sorted(k for k in _mod("flh_compose").genres() if not k.startswith("_"))
    except Exception:
        pass
    mic = None
    if H.get("IS_WINDOWS"):
        try:
            mic = bool(__import__("fl_capture").available()[0])
        except Exception:
            mic = None
    gs = have["genres"]
    pick = lambda *xs: next((x for x in xs if x in gs), gs[0] if gs else "lofi")
    ex = [f"Make me a {pick('lofi')} beat at 80 BPM",
          f"Set me up a {pick('trap')} template in F minor - dark vibe",
          f"Start a {pick('house')} track called 'Night Bus' with three beat variations to pick from",
          "Swap the kick for something punchier", "Make the drums dustier and give it more bounce"]
    if have["labs"]:
        ex.append("Put a LABS choir pad under the hook")
    elif have["flex"]:
        ex.append("Use FLEX for the keys and suggest a warm preset")
    if mic:
        ex.append("Set up a vocal track so I can record over the hook")
    ex.append("Mix it and make a -14 LUFS final")
    ex.append("Give me the instrumental and the vocals as separate files")
    ex.append("Save this setup as my template")
    steps = chk.get("steps") or []                           # flh_setup's guided checklist, one line per step
    setup = [f"{s['n']}. {s['step']}: {s['status']}" + (f" - {s['say']}" if s.get("say") else "")
             + (f" -> {s['next']}" if s.get("next") and s["n"] < 7 else "") for s in steps] or \
        ["ready" if chk.get("ready") else "almost: " + "; ".join((chk.get("next_steps") or [])[:2])]
    nxt = next((s for s in steps if s["n"] == 7 and s.get("next")), None)
    msg = {
        "hello": "fl-headless lets you make music in FL Studio by just saying what you want.",
        "setup": setup,
        "setup_ready": bool(chk.get("ready")),
        "installed": {"genres": len(gs), "samples": have["samples"], "LABS sounds": have["labs"] or 0,
                      "FLEX": have["flex"]},
        "try": ex[:8],
        "promises": ["new songs are new files - your existing projects are never touched without asking",
                     "every edit makes a backup first (undo: flp_versions / flp_restore)",
                     "only plugins your FL edition owns; anything swapped is said out loud",
                     "your samples stay where they are - nothing is copied or uploaded",
                     "deletes go to the Recycle Bin; only one FL ever runs"],
        "first_time": first,
    }
    if nxt:
        msg["next"] = {k: nxt[k] for k in ("next", "do", "auto") if k in nxt}
    try:
        upd = H["_release_update"]()                             # a newer fl-headless out? (weekly look, cached)
    except Exception:
        upd = None
    if upd:
        msg["update"] = {"say": upd["say"], "how": upd["how"]}
    if first or force:
        H["_state"]({"welcomed": time.strftime("%Y-%m-%d %H:%M")})
    return msg


# ============================================================================= icon probe (PC-VERIFY helper)
def flh_icon_probe(output, start=0, count=40, base=None):
    """Write a project with one sampler channel per icon number (named 'icon N') to learn FL's icon numbers: open it
    in FL, look at the channel rack, and fill data/templates/_icons.json."""
    H["writable"](output)
    src = base or H["_blank_project"]()
    if not src:
        raise FileNotFoundError("no empty FL template to start from (set FLH_BLANK)")
    d = H["_load"](src)
    for i in range(int(start), int(start) + max(1, min(120, int(count)))):
        ev = H["_events"](d)
        d, ch = _append_channel(d, _sampler_block(d, ev, None, f"icon {i}"))
        d = _chan_edit(d, ch, {H["EV_CHANNEL_ICON"]: struct.pack("<I", i)})
    H["_save"](d, output)
    return {"output": output, "icons": [int(start), int(start) + int(count) - 1],
            "next": "fl_open it, fl_snapshot the channel rack, write the numbers you recognise into "
                    "data/templates/_icons.json (map + verified: true)"}


# ============================================================================= register
def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    STR, INT, NUM, BOOL, FLP = ns["_STR"], ns["_INT"], ns["_NUM"], ns["_BOOL"], ns["_FLP"]
    guard = ns["_guard_open_project"]
    ns["TOOLS"]["flh_new_song"] = (
        flh_new_song,
        "Start a NEW song in one call (never touches existing projects): 'make a lofi beat', 'set me up a trap template'. "
        "From FL's empty template (or a saved one): channels with real sounds (FL's own samples for drums, the "
        "user's owned instruments for bass/keys/lead/pads - substitutions said honestly), each on a named, colored "
        "mixer insert routed to Drums/Music/Vocals buses, Reverb/Delay send tracks, EQ + limiter on the master, "
        "playlist tracks named by role, tempo/key, a starter beat in the genre's form (starter=false: empty, with "
        "section blocks), the song journal - then opens it in FL. vibe='dark dusty' steers the sounds; kit= picks a "
        "drum kit style; variations=2 writes 3 beats to pick from.",
        S(genre=STR("lofi, trap, boombap, drill-uk, drill-ny, house, techno, dnb, pop, rnb, afrobeats, reggaeton, "
                    "jazzhop, rock, ambient, cinematic (aliases ok)", True),
          bpm=NUM("tempo (default: the genre's)", True), key=STR("e.g. 'F minor' (default: the genre's)", True),
          name=STR("song name (default '<Genre> <date>')", True),
          folder=STR("where the song folder goes (default: the first FLH_ROOTS folder)", True),
          vibe=STR("words for the sounds: 'dark', 'dusty warm', 'hard bright'", True),
          kit=STR("drum kit style: dusty, 808, 909, acoustic, clean, dark", True),
          template=STR("a template saved with flh_save_template", True),
          starter=BOOL("write a starter beat (default true)", True),
          variations=INT("extra beat variations to audition (0-5, default 0)", True),
          open=BOOL("open it in FL (default true)", True), arrange=BOOL("lay the beat out in the genre's form "
                                                                       "(default true)", True),
          seed=INT("", True), balance=BOOL("balance the levels with one export after opening (~40 s, background; "
                                            "default: only when flh_auto_renders is on)", True),
          ear_candy=ns["_p"](["boolean", "string"], "off | organic | subtle | normal | lots | synthetic: the "
                             "arrangement's ear candy (organic = real-instrument moves on the song's own channels; "
                             "default: the collection's choice when the song joins one, else organic for piano-led / "
                             "cinematic / ambient, else the genre's)", True),
          collection=ns["_p"](["boolean", "string"], "join a collection: true = the active one, or its name. Default: "
                              "none - the collection profile's taste (style, ear candy, finish) stays out of the new "
                              "song", True)))
    def flp_balance(project, genre=None, background=True):
        if not genre:
            try:
                genre = (_mod("flh_store").journal_get(project) or {}).get("genre")
            except Exception:
                genre = None
        if background:
            start_balance(project, genre)
            return {"balancing": project, "genre": genre, "note": "runs in the background (~40 s, holding FL); the "
                    "result comes with the next tool result"}
        return balance_song(project, genre)
    ns["TOOLS"]["flp_balance"] = (
        flp_balance,
        "Balance a song's levels in the open FL: each instrument (and the drums together) measured alone, faders moved "
        "to the genre's targets against the drums, the master limiter's gain to a healthy level. The same pass "
        "flh_new_song runs after opening; use it after swapping sounds or for a song opened later. A part with notes "
        "that measures silent (a preset whose content is missing) gets the next-ranked sound for its part (up to 2 "
        "swaps, said in notes); such sounds are remembered per PC and skipped by later picks (silent_sounds lists / "
        "clears them).",
        S(project=FLP, genre=STR("genre for the targets (default: the song journal's)", True),
          background=BOOL("run in the background (default true)", True)))
    ns["TOOLS"]["silent_sounds"] = (
        silent_sounds,
        "Instrument sounds (FLEX / LABS presets, plugin patches) remembered as making no sound on this PC - found by "
        "the level balance, skipped by flh_new_song's picks, FLEX search and balance swaps. action='clear' forgets "
        "them all (e.g. after reinstalling or repairing a pack), key='FLEX|pack|preset' (or just the preset) only "
        "that one.",
        S(action=STR("list (default) | clear", True), key=STR("one sound to forget (with action='clear')", True)))
    try:
        ns["TOOL_GROUPS"]["silent_sounds"] = "sounds"
    except Exception:                                     # noqa: BLE001
        pass
    ns["silent_key"] = silent_key
    ns["silent_sound_keys"] = silent_sound_keys
    ns["silent_mark"] = silent_mark
    ns["TOOLS"]["flp_load_sample"] = (
        guard(flp_load_sample, "project", "output"),
        "Load a sample (from fl_sound_catalog's sample results, or any file) into a sampler channel: channel omitted = "
        "a new sampler channel on a free mixer insert, channel=N = replace that sampler's sample (replace_plugin=true "
        "turns a plugin channel into a sampler, keeping its name, color, insert and notes). The sample is referenced "
        "where it is (FL's factory samples as %FLStudioFactoryData%), never copied.",
        S(project=FLP, output=ns["_OUT"], sample=STR("the sample file"), channel=INT("sampler channel to replace", True),
          name=STR("channel name (default: the sample's)", True),
          role=STR("what the sound plays (bass, 808, keys, lead, pad, kick...): pitched roles get their tuning "
                   "measured and recorded (default: the role in the song journal)", True),
          replace_plugin=BOOL("channel=N is a plugin channel: make it a sampler with this sample", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_load_sample"] = ("project", "output")
    ns["TOOLS"]["flh_save_template"] = (
        flh_save_template,
        "'Save this as my template': keeps a copy of the project's setup (channels, sounds, mixer, routing; notes and "
        "pattern clips cleared unless keep_notes) plus its channel roles, for flh_new_song(template=name). "
        "fl_templates=true also puts it in FL's own Templates folder if fl-headless may write there.",
        S(project=FLP, name=STR("template name"), keep_notes=BOOL("keep notes and clips (default false)", True),
          fl_templates=BOOL("also copy into FL's user Templates folder", True),
          overwrite=BOOL("replace an existing fl-headless template of that name (backed up first; FL templates "
                         "fl-headless didn't make are never replaced)", True)))
    ns["TOOLS"]["flh_welcome"] = (
        flh_welcome,
        "First-use welcome (call once when a user starts): the setup checklist (flh_setup's steps, one line each, and "
        "the one next action), 6-8 things to try tailored to what's installed, and the safety promises. Plain, short; "
        "show it to the user in your own words; next.auto = flh_setup(action='apply') does it.",
        S(force=BOOL("mark as shown again", True)))
    ns["TOOLS"]["flh_icon_probe"] = (
        flh_icon_probe,
        "Diagnostics: write a project with one channel per icon number (named 'icon N') to learn FL's channel icon "
        "numbers on this PC (fl-headless sets no icons until they're verified).",
        S(output=STR("where to write the probe project (inside FLH_ROOTS)"), start=INT("first icon number", True),
          count=INT("how many (max 120)", True)))
    ns["flp_load_sample"] = flp_load_sample
    ns["flh_new_song"] = flh_new_song
    hooks = ns.get("_AFTER_OPEN")
    if isinstance(hooks, list):                            # a pending balance (open=false songs) runs on fl_open
        hooks[:] = [h for h in hooks if getattr(h, "__name__", "") != "_after_open_balance"] + [_after_open_balance]
