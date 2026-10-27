"""fl-headless cleanup: flp_remove_channels - delete channels from a .flp file (no FL needed), renumbering every
reference to the channels after them.

What names a channel in an FL project (rack index = the ChannelNew id, 0..n-1 in rack order):
    64  ChannelNew                 the channel's own block runs from it to its end marker (51)
    94  layer child                inside a Layer channel's block: one event per child channel
    224 pattern notes              24-byte records, bytes 6-7 = channel (step-sequencer steps are notes too)
    216 / 223 / 225                12-byte controller records (recorded / per pattern / initial values):
                                   bytes 4-5 parameter, 6-7 destination
    226 / 227                      MIDI / internal controller links: bytes 8-9 parameter, 10-11 destination;
                                   227 bytes 2-3 = the controller channel (automation clip)
    233 playlist                   88-byte clips, bytes 6-7 = item (< 0x5000: a channel's audio / automation clip)
    header                         channel count (bytes 10-11)
Destinations: < 0x0FFF a channel, 0x0FFF none, 0x4xxx global (tempo, main volume), >= 0x7000 a mixer track/slot.
Anything else (an unknown destination or link layout, legacy note events, odd record sizes) makes it refuse:
a channel it can't renumber with confidence is never removed.

Follows the extension contract in flh_store.py.
"""
import bisect
import json
import os
import struct
import sys
import time

H = {}

EV_LAYER_CHILD = 94
EV_LEGACY_NOTES = 222                       # FL 3-era "automated note events" (channel inside, layout unknown)
REC12 = (216, 223, 225)
LINKS = (226, 227)
CLIP = 88
STRUCTURAL = {65, 99, 216, 222, 223, 224, 225, 226, 227, 233, 236, 238}     # never inside a channel block


class Refuse(ValueError):
    pass


def _u16(b, i):
    return struct.unpack_from("<H", b, i)[0]


def _dest_kind(dest):
    if dest < 0x0FFF:
        return "channel"
    if dest == 0x0FFF:
        return "none"
    if 0x4000 <= dest < 0x5000:
        return "global"
    if dest >= 0x7000:
        return "mixer"
    return None


def _version(d, ev):
    for e in ev:
        if e["id"] == 199:
            t = H["_payload"](d, e).split(b"\x00")[0].decode("ascii", "replace")
            try:
                return tuple(int(x) for x in t.split(".")[:2])
            except ValueError:
                return None
    return None


# ----------------------------------------------------------------------------- model
def _model(d):
    """Every channel reference in the file -> dict; raises Refuse on anything it doesn't understand."""
    pay = H["_payload"]; ev = H["_events"](d)
    EV_CH, EV_END = H["EV_CHANNEL"], H["EV_CHANNEL_END"]
    ver = _version(d, ev)
    if ver is None or ver < (12, 0):
        raise Refuse(f"FL version {ver or 'unknown'}: only projects saved by FL 12 or newer are supported")
    blocks, owner, k = {}, {}, 0
    while k < len(ev):
        if ev[k]["id"] != EV_CH:
            k += 1; continue
        if ev[k]["len"] != 2:
            raise Refuse(f"channel event at {k} has an unexpected size")
        iid = _u16(pay(d, ev[k]), 0)
        if iid in blocks:
            raise Refuse(f"channel {iid} appears twice in the file")
        j = k + 1
        while j < len(ev) and ev[j]["id"] != EV_END:
            if ev[j]["id"] == EV_CH or ev[j]["id"] in STRUCTURAL:
                break                                     # FL's empty template: its sampler has no end marker -
            j += 1                                        # the block ends before the next project section
        if j >= len(ev):
            raise Refuse(f"channel {iid}'s block has no end marker")
        if ev[j]["id"] != EV_END:
            j -= 1
            if j <= k:
                raise Refuse(f"channel {iid}'s block is empty; this file layout isn't supported")
        blocks[iid] = (k, j)
        for x in range(k, j + 1):
            owner[x] = iid
        k = j + 1
    info = {}
    for iid, (a, b) in blocks.items():
        c = {"index": iid, "type": None, "name": None, "children": []}
        for x in range(a + 1, b + 1):
            e = ev[x]
            if e["id"] == 21 and c["type"] is None:
                c["type"] = pay(d, e)[0]
            elif e["id"] == H["EV_DISPLAY_NAME"] and c["name"] is None:
                c["name"] = H["_text16"](pay(d, e))
            elif e["id"] == EV_LAYER_CHILD:
                if e["len"] != 2:
                    raise Refuse("a layer-child event has an unexpected size")
                c["children"].append((x, _u16(pay(d, e), 0)))
        info[iid] = c
    notes, recs, links, clips = [], [], [], []           # (event index, ...) lists
    cur_pat = None
    for k, e in enumerate(ev):
        i = e["id"]
        if i == EV_LAYER_CHILD and k not in owner:
            raise Refuse("a layer-child reference outside any channel block")
        if k in owner:
            continue
        p = pay(d, e)
        if i == 65:
            cur_pat = _u16(p, 0) if len(p) >= 2 else None
        elif i == 224:
            if len(p) % 24:
                raise Refuse("pattern notes in an unknown record format (not 24-byte records)")
            notes.append((k, cur_pat, [bytes(p[r:r + 24]) for r in range(0, len(p), 24)]))
        elif i in REC12:
            if len(p) % 12:
                raise Refuse(f"controller records (event {i}) in an unknown format")
            rr = [bytes(p[r:r + 12]) for r in range(0, len(p), 12)]
            for r in rr:
                if _dest_kind(_u16(r, 6)) is None:
                    raise Refuse(f"controller record (event {i}) with an unknown destination {_u16(r, 6):#06x}")
            recs.append((k, i, rr))
        elif i in LINKS:
            if len(p) < 12:
                raise Refuse(f"controller link (event {i}) shorter than expected")
            dest = _u16(p, 10)
            if _dest_kind(dest) is None:
                raise Refuse(f"controller link (event {i}) with an unknown destination {dest:#06x}")
            src = None
            if i == 227:
                if p[0:2] != b"\x00\x00" or p[4:8] != b"\x00" * 4:
                    raise Refuse("an internal controller link of a kind this tool doesn't know (not an automation "
                                 "clip / channel controller): can't renumber it safely")
                src = _u16(p, 2)
                if src not in blocks:
                    raise Refuse(f"an internal controller link names controller {src}, which isn't a channel")
            links.append((k, i, bytes(p), src, dest))
        elif i == 233:
            if not len(p):
                continue
            try:                                         # 88-byte records (FL 2024+), 60 (FL 21), 32 (FL 20)
                size = H["_clip_size"](d, ev, p) if "_clip_size" in H else CLIP
            except ValueError as e:
                raise Refuse(str(e))
            if len(p) % size:
                raise Refuse("playlist in an unknown clip format")
            rr = [bytes(p[r:r + size]) for r in range(0, len(p), size)]
            if any(r[4:6] != b"\x00\x50" for r in rr):
                raise Refuse("a playlist clip of an unknown kind")
            clips.append((k, rr))
        elif i == EV_LEGACY_NOTES and len(p):
            raise Refuse("legacy note-automation data (event 222): can't renumber it safely")
    return {"ev": ev, "blocks": blocks, "info": info, "notes": notes, "recs": recs, "links": links, "clips": clips,
            "version": ver}


def _sketches(M):
    """{channel: {pattern id: notes}} for patterns no arrangement places (sketches, unpicked variations)."""
    PB = H["PATTERN_BASE"]
    used = {_u16(r, 6) - PB for _, rr in M["clips"] for r in rr if _u16(r, 6) >= PB}
    out = {}
    for _, pid, rr in M["notes"]:
        if pid in used:
            continue
        for n in rr:
            c = _u16(n, 6); out.setdefault(c, {}); out[c][pid] = out[c].get(pid, 0) + 1
    return out


def _played(d, M):
    """{channel: notes inside placed clip windows (any arrangement, muted or not)}, {channel: placed clips}."""
    PB = H["PATTERN_BASE"]; by_pat = {}
    for _, pid, rr in M["notes"]:
        by_pat.setdefault(pid, []).extend(rr)
    heard, placed = {}, {}
    for _, rr in M["clips"]:
        for r in rr:
            item = _u16(r, 6)
            if item < PB:
                placed[item] = placed.get(item, 0) + 1; continue
            pos, ln = struct.unpack_from("<I", r, 0)[0], struct.unpack_from("<I", r, 8)[0]
            s = H["_clip_offsets"](r)[0]
            for n in by_pat.get(item - PB, []):
                t = struct.unpack_from("<I", n, 0)[0]
                if s <= t < s + ln:
                    c = _u16(n, 6); heard[c] = heard.get(c, 0) + 1
    return heard, placed


# ----------------------------------------------------------------------------- rewrite
def _rewrite(d, M, dele, force):
    """-> (new bytes, report). dele: set of channel ids to remove."""
    ev = M["ev"]; ds = sorted(dele)
    mp = lambda c: None if c in dele else c - bisect.bisect_left(ds, c)
    rep = {"note_records_renumbered": 0, "notes_dropped": {}, "controller_records_renumbered": 0,
           "controller_records_dropped": 0, "links_renumbered": 0, "links_dropped": [], "clips_renumbered": 0,
           "clips_dropped": {}, "layer_refs_renumbered": 0, "layer_refs_dropped": 0}
    blockers = []
    edits = []
    for iid, (a, b) in M["blocks"].items():
        if iid in dele:
            edits.append((a, b + 1, b"")); continue
        if mp(iid) != iid:
            edits.append((a, a + 1, bytes([H["EV_CHANNEL"]]) + struct.pack("<H", mp(iid))))
        for x, child in M["info"][iid]["children"]:
            if mp(child) is None:
                edits.append((x, x + 1, b"")); rep["layer_refs_dropped"] += 1
            elif mp(child) != child:
                edits.append((x, x + 1, bytes([EV_LAYER_CHILD]) + struct.pack("<H", mp(child))))
                rep["layer_refs_renumbered"] += 1
    for k, pid, rr in M["notes"]:
        out, changed = [], False
        for r in rr:
            c = _u16(r, 6); n = mp(c)
            if n is None:
                rep["notes_dropped"][pid] = rep["notes_dropped"].get(pid, 0) + 1; changed = True; continue
            if n != c:
                r = r[:6] + struct.pack("<H", n) + r[8:]; rep["note_records_renumbered"] += 1; changed = True
            out.append(r)
        if changed:
            edits.append((k, k + 1, H["_data_event"](224, b"".join(out))))
    for k, eid, rr in M["recs"]:
        out, changed = [], False
        for r in rr:
            dest = _u16(r, 6)
            if _dest_kind(dest) == "channel":
                n = mp(dest)
                if n is None:
                    rep["controller_records_dropped"] += 1; changed = True; continue
                if n != dest:
                    r = r[:6] + struct.pack("<H", n) + r[8:]; rep["controller_records_renumbered"] += 1; changed = True
            out.append(r)
        if changed:
            edits.append((k, k + 1, H["_data_event"](eid, b"".join(out))))
    for k, eid, p, src, dest in M["links"]:
        chan_dest = _dest_kind(dest) == "channel"
        if src is not None and mp(src) is None:
            edits.append((k, k + 1, b"")); rep["links_dropped"].append(f"automation channel {src}'s target link")
            continue
        if chan_dest and mp(dest) is None:
            if eid == 227:
                blockers.append(f"automation channel {src} ('{M['info'][src]['name']}') automates channel {dest}, "
                                f"which would be removed: remove that automation channel too (add {src}) or force=true "
                                f"(its link is dropped and it automates nothing)")
            edits.append((k, k + 1, b"")); rep["links_dropped"].append(
                f"{'MIDI' if eid == 226 else 'automation'} link to channel {dest}"); continue
        q = bytearray(p); changed = False
        if src is not None and mp(src) != src:
            q[2:4] = struct.pack("<H", mp(src)); changed = True
        if chan_dest and mp(dest) != dest:
            q[10:12] = struct.pack("<H", mp(dest)); changed = True
        if changed:
            edits.append((k, k + 1, H["_data_event"](eid, bytes(q)))); rep["links_renumbered"] += 1
    PB = H["PATTERN_BASE"]
    for k, rr in M["clips"]:
        out, changed = [], False
        for r in rr:
            item = _u16(r, 6)
            if item < PB:
                n = mp(item)
                if n is None:
                    rep["clips_dropped"][item] = rep["clips_dropped"].get(item, 0) + 1; changed = True; continue
                if n != item:
                    r = r[:6] + struct.pack("<H", n) + r[8:]; rep["clips_renumbered"] += 1; changed = True
            out.append(r)
        if changed:
            edits.append((k, k + 1, H["_data_event"](233, b"".join(out))))
    new = bytearray(H["_splice"](d, ev, edits))
    fmt, nch, ppq = struct.unpack("<hHH", bytes(new[8:14]))
    new[8:14] = struct.pack("<hHH", fmt, max(0, nch - len(dele)), ppq)
    rep["renumbered_channels"] = {str(c): mp(c) for c in sorted(M["blocks"]) if c not in dele and mp(c) != c}
    return bytes(new), rep, blockers


# ----------------------------------------------------------------------------- verification
def _canon(d, mp):
    """The project's meaning with every channel reference passed through mp (None = drop): channel blocks, notes per
    pattern, controller records, links, clips and every other event in order. Built by decoding, not by the
    rewrite's byte edits, so comparing before/after catches a rewrite mistake."""
    M = _model(d); ev = M["ev"]; pay = H["_payload"]; owner = {}
    for iid, (a, b) in M["blocks"].items():
        for x in range(a, b + 1):
            owner[x] = iid
    special = {k for k, *_ in M["notes"]} | {k for k, *_ in M["recs"]} | {k for k, *_ in M["links"]} | \
        {k for k, _ in M["clips"]}
    out = []; PB = H["PATTERN_BASE"]
    note_of = {k: (pid, rr) for k, pid, rr in M["notes"]}
    rec_of = {k: (eid, rr) for k, eid, rr in M["recs"]}
    link_of = {k: (eid, p, s, dst) for k, eid, p, s, dst in M["links"]}
    clip_of = dict(M["clips"])
    for k, e in enumerate(ev):
        if k in owner:
            iid = owner[k]; n = mp(iid)
            if n is None:
                continue
            if e["id"] == H["EV_CHANNEL"]:
                out.append(("channel", n))
            elif e["id"] == EV_LAYER_CHILD:
                c = mp(_u16(pay(d, e), 0))
                if c is not None:
                    out.append(("layer", n, c))
            else:
                out.append(("in", n, e["id"], bytes(pay(d, e))))
            continue
        if k not in special:
            out.append(("ev", e["id"], bytes(pay(d, e)))); continue
        if k in note_of:
            pid, rr = note_of[k]
            out.append(("notes", pid, tuple((r[:6] + r[8:], mp(_u16(r, 6))) for r in rr if mp(_u16(r, 6)) is not None)))
        elif k in rec_of:
            eid, rr = rec_of[k]; keep = []
            for r in rr:
                dst = _u16(r, 6)
                if _dest_kind(dst) == "channel":
                    if mp(dst) is None:
                        continue
                    keep.append((r[:6] + r[8:], "ch", mp(dst)))
                else:
                    keep.append((r, None, None))
            out.append(("recs", eid, tuple(keep)))
        elif k in link_of:
            eid, p, s, dst = link_of[k]
            if s is not None and mp(s) is None:
                continue
            ch_dst = _dest_kind(dst) == "channel"
            if ch_dst and mp(dst) is None:
                continue
            out.append(("link", eid, (p[:10] if s is None else p[:2] + p[4:10]) + p[12:], None if s is None else mp(s),
                        mp(dst) if ch_dst else ("raw", dst)))
        else:
            keep = []
            for r in clip_of[k]:
                it = _u16(r, 6)
                if it < PB:
                    if mp(it) is None:
                        continue
                    keep.append((r[:6] + r[8:], "ch", mp(it)))
                else:
                    keep.append((r, None, None))
            out.append(("clips", tuple(keep)))
    return out


def _verify(before, after, dele):
    ds = sorted(dele)
    mp_old = lambda c: None if c in dele else c - bisect.bisect_left(ds, c)
    same = lambda c: c
    H["_events"](after); H["_structure"](after)                   # reparses
    a, b = _canon(before, mp_old), _canon(after, same)
    if a != b:
        diff = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        raise RuntimeError(f"verification failed at item {diff} ({a[diff][:2] if diff < len(a) else 'end'} vs "
                           f"{b[diff][:2] if diff < len(b) else 'end'}); nothing was written")
    nb = struct.unpack("<H", before[10:12])[0]; na = struct.unpack("<H", after[10:12])[0]
    if na != max(0, nb - len(dele)):
        raise RuntimeError("verification failed: channel count in the header")
    # the main file's snapshot, channels and notes mapped: every remaining channel plays the same notes
    sb, sa = H["_snapshot"](before), H["_snapshot"](after)
    chb = {mp_old(c): v for c, v in sb["channels"].items() if mp_old(c) is not None}
    if chb != sa["channels"]:
        raise RuntimeError("verification failed: channel names / plugins changed")
    nb_ = sorted((p, ln, mp_old(c), key, v) for p, ln, c, key, v in sb["notes"] if mp_old(c) is not None)
    if nb_ != sorted(sa["notes"]) or sb["tempos"] != sa["tempos"] or sb["patterns"] != sa["patterns"]:
        raise RuntimeError("verification failed: the song's notes / tempo / patterns changed")
    return {"reparsed": True, "channels": len(sa["channels"]), "notes_in_song": len(sa["notes"]),
            "method": "every channel reference decoded before and after (renumbering applied) and compared; "
                      "song notes, tempo map, patterns and channel names/plugins compared via _snapshot"}


# ----------------------------------------------------------------------------- the tool
def _review():
    R = sys.modules.get("flh_review")
    if R is None:
        import flh_review as R
    if not R.H:
        R.H = H
    return R


def _resolve(M, channels):
    out = []
    for c in channels or []:
        if isinstance(c, bool):
            raise ValueError(f"bad channel {c!r}")
        if isinstance(c, int) or (isinstance(c, str) and c.strip().isdigit()):
            c = int(c)
            if c not in M["blocks"]:
                raise ValueError(f"no channel {c} (channels: {sorted(M['blocks'])})")
            out.append(c); continue
        hits = [i for i, v in M["info"].items() if (v["name"] or "").lower() == str(c).strip().lower()]
        if len(hits) != 1:
            raise ValueError(f"channel name '{c}' matches {len(hits)} channels; give the index")
        out.append(hits[0])
    return out


def flp_remove_channels(project, output=None, channels=None, unused=False, unplaced_automation=False, dry_run=False,
                        force=False):
    """Delete channels from a .flp (file-level, no FL). channels: indexes or names; unused: every channel with no
    notes in the song and no placed clip (fl_doctor's unused_channel); unplaced_automation: automation clips that
    aren't on the playlist. Renumbers every later reference; refuses rather than risk a broken file."""
    output = output or project
    if not dry_run:
        H["writable"](output)
    d = H["_load"](project)
    M = _model(d)
    pick = _resolve(M, channels); why = {c: "asked" for c in pick}
    if unused or unplaced_automation:
        cand = _review().cleanup_candidates(d)
        for c in cand["unused"] if unused else []:
            why.setdefault(c, "unused")
        for c in cand["unplaced_automation"] if unplaced_automation else []:
            why.setdefault(c, "unplaced automation")
    dele = set(why)
    res = {"project": project, "output": None if dry_run else output, "dry_run": bool(dry_run)}
    if not dele:
        res.update(removed=[], note="nothing to remove")
        return res
    if len(dele) >= len(M["blocks"]):
        raise Refuse("that would remove every channel; a project keeps at least one")
    heard, placed = _played(d, M); sketch = _sketches(M)
    names = {}
    try:
        names = {pid: v["name"] for pid, v in H["_patterns"](d, M["ev"]).items()}
    except Exception:
        pass
    blockers = []
    for c in sorted(dele):
        nm = M["info"][c]["name"] or f"Channel {c}"
        if heard.get(c):
            blockers.append(f"channel {c} '{nm}' has {heard[c]} notes on the playlist")
        if placed.get(c):
            blockers.append(f"channel {c} '{nm}' has {placed[c]} clip(s) placed in the playlist")
        if sketch.get(c):                                   # not in the song, but someone's work (a sketch, a variation)
            pats = ", ".join(f"'{names.get(p_) or f'pattern {p_}'}'" for p_ in sorted(sketch[c])[:4])
            blockers.append(f"channel {c} '{nm}' has {sum(sketch[c].values())} notes in pattern(s) {pats} that "
                            "aren't in the playlist")
    new, rep, more = _rewrite(d, M, dele, force)
    blockers += more
    kinds = {4: "audio clip", 5: "automation clip"}
    res["removed"] = [{"channel": c, "name": M["info"][c]["name"], "kind": kinds.get(M["info"][c]["type"], "instrument"),
                       "why": why[c], "notes_on_playlist": heard.get(c, 0), "placed_clips": placed.get(c, 0)}
                      for c in sorted(dele)]
    res["references"] = {k: v for k, v in rep.items() if v}
    res["channels_after"] = len(M["blocks"]) - len(dele)
    if blockers and not force:
        res.update(refused=blockers, fix="remove those notes/clips first, leave the channel out, or force=true "
                                         "(its notes and clips go with it)")
        if dry_run:
            return res
        raise Refuse("; ".join(blockers) + " - nothing was changed (force=true removes them with their notes/clips)")
    if blockers:
        res["forced"] = blockers
    res["verified"] = _verify(d, new, dele)
    if dry_run:
        res["note"] = "dry run: nothing written"
        return res
    H["_save"](new, output)
    fix = H.get("_marker_layout_fix")                    # _save lays markers out where FL keeps them (an older write
    try:                                                 # left them where FL's save scrambles them): compared alike
        d_cmp = fix(d)[0] if fix else d
    except Exception:                                    # noqa: BLE001
        d_cmp = d
    res["verified"] = _verify(d_cmp, H["_load"](output), dele)
    res["journal"] = _renumber_journal(output, dele)
    return res


# journal entries that hold channel indexes (every module's): renumbered together after a removal
def _renumber_journal(project, dele):
    """Keep the song's journal in step with the renumbered channels, in ONE write: the channel roles
    (journal.channels), the compose registry (compose.channels / drum_map / transpose) and the performance parts
    (performance and its history). Entries of removed channels go. Playlist tracks (compose.tracks) and mixer inserts
    (vocal.track) aren't channel numbers and stay."""
    try:
        import flh_store as S
        p = S.journal_path(project)
        if not os.path.isfile(p):
            return None
        j = S.journal_get(project)
        if j.get("journal_damaged"):                     # never written over: renumbering needs the real journal
            return "journal not updated: " + j["journal_damaged"].split(":")[0] + " is damaged (left as it is)"
        ds = sorted(dele)
        new_idx = lambda c: None if c in dele else c - bisect.bisect_left(ds, c)
        moved = {"roles": 0, "compose": 0, "performance": 0, "dropped": 0}

        def as_int(x):
            try:
                return int(x)
            except (TypeError, ValueError):
                return None

        def rekey(dct, what):                            # {"<channel>": ...} -> renumbered keys
            out = {}
            for k, v in (dct or {}).items():
                c = as_int(k)
                if c is None:
                    out[k] = v; continue
                n = new_idx(c)
                if n is None:
                    moved["dropped"] += 1; continue
                moved[what] += n != c; out[str(n)] = v
            return out
        if isinstance(j.get("channels"), dict):
            j["channels"] = rekey(j["channels"], "roles")
        comp = j.get("compose")
        if isinstance(comp, dict):
            chs = comp.get("channels")
            if isinstance(chs, dict):                    # {role: channel} or {role: [channels]}
                out = {}
                for role, c in chs.items():
                    if isinstance(c, list):
                        keep = [new_idx(x) for x in c if as_int(x) is not None and new_idx(int(x)) is not None]
                        moved["compose"] += keep != c
                        if keep:
                            out[role] = keep
                        else:
                            moved["dropped"] += 1
                    elif as_int(c) is not None:
                        n = new_idx(int(c))
                        if n is None:
                            moved["dropped"] += 1
                        else:
                            moved["compose"] += n != c; out[role] = n
                    else:
                        out[role] = c
                comp["channels"] = out
            dm = comp.get("drum_map")
            if isinstance(dm, dict):                     # {part: [channel, key, kit]}
                out = {}
                for part, v in dm.items():
                    if isinstance(v, list) and v and as_int(v[0]) is not None:
                        n = new_idx(int(v[0]))
                        if n is None:
                            moved["dropped"] += 1; continue
                        moved["compose"] += n != v[0]; v = [n] + list(v[1:])
                    out[part] = v
                comp["drum_map"] = out
            if isinstance(comp.get("transpose"), dict):
                comp["transpose"] = rekey(comp["transpose"], "compose")

        def parts_of(spec):
            parts = (spec or {}).get("parts") if isinstance(spec, dict) else None
            if not isinstance(parts, dict):
                return
            for name in list(parts):
                part = parts[name]
                if isinstance(part, dict) and as_int(part.get("channel")) is not None:
                    n = new_idx(int(part["channel"]))
                    if n is None:
                        parts.pop(name); moved["dropped"] += 1; continue
                    moved["performance"] += n != part["channel"]; part["channel"] = n
        parts_of(j.get("performance"))
        for h in j.get("performance_history") or []:
            parts_of(h.get("spec") if isinstance(h, dict) else None)
        j["updated"] = time.strftime("%Y-%m-%d %H:%M")
        H["writable"](p)
        tmp = p + ".tmp"                                 # one atomic write (a crash can't leave half the renumbering)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(j, f, indent=1)
        os.replace(tmp, p)
        return (f"journal renumbered: {moved['roles']} channel role(s), {moved['compose']} compose registry "
                f"entr(ies), {moved['performance']} performance part(s) moved; {moved['dropped']} entr(ies) of removed "
                "channels dropped")
    except Exception as e:
        return f"journal not updated: {type(e).__name__}: {e}"


def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    guarded = ns["_guard_open_project"](flp_remove_channels, "project", "output")

    def tool(**kw):
        if kw.get("dry_run"):
            return flp_remove_channels(**kw)                 # read-only: no FL save / reload
        kw["output"] = kw.get("output") or kw["project"]
        return guarded(**kw)
    tool.__wrapped__ = flp_remove_channels
    ns["TOOLS"]["flp_remove_channels"] = (
        tool, "Delete channels from a project file without FL (rack, notes in every pattern, playlist clips, "
        "automation links and layers renumbered; verified after writing). channels: [index or name]; unused=true: "
        "channels with no notes in the song and no placed clip; unplaced_automation=true: automation clips not on "
        "the playlist. Refuses channels whose notes/clips are on the playlist unless force=true, never removes the "
        "last channel, and refuses any file structure it can't renumber safely. dry_run=true lists what would go.",
        S(project=ns["_FLP"], output=ns["_STR"]("where to save (default: the project itself)", True),
          channels=P("array", "channel indexes or names", True, items={"type": ["integer", "string"]}),
          unused=ns["_BOOL"]("also every unused channel (fl_doctor unused_channel)", True),
          unplaced_automation=ns["_BOOL"]("also every automation clip that isn't on the playlist", True),
          dry_run=ns["_BOOL"]("only report what would be removed / renumbered", True),
          force=ns["_BOOL"]("remove channels even with notes / clips on the playlist", True)))
    ns["_OPEN_PROJECT_GUARD"]["flp_remove_channels"] = ("project", "output")
