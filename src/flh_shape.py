"""flh_shape: shape a song's ending and its entrances in the file (no FL needed; reversible with flp_versions).

flp_extend_ending  the last chord rings long enough: by default its length IN SECONDS comes from the collection's rules
                   (min_ending_s, min_tail_s; through the tempo map, with a margin) so the first render passes them
                   once fl_finalize fades the hold as a diminuendo; or hold_s / seconds. The background layers
                   sounding at the end hold too but let go earlier so their release fades under the main part, the
                   playlist clip grows to fit (with the release), and an optional gentle ritardando slows the final
                   bars. For music a game or a playlist may start and fade at any moment (long, soft endings).
flp_smooth_entries where a part enters out of nowhere (no notes in the bars before) and the song's level jumps, its
                   first notes ramp in (velocity from `start` % up to full over `ramp_beats`); dry_run lists the jumps.
"""
import bisect
import math
import os

H = None
LAYER_ROLES = ("strings", "string pad", "pad", "choir", "celli", "cello", "violas", "violins", "bass", "low strings",
               "organ", "brass", "woodwind", "soft piano layer", "echo")
DRUM_WORDS = ("kick", "snare", "clap", "hat", "openhat", "rim", "perc", "shaker", "ride", "crash", "tom", "timpani",
              "cymbal", "drums")


LAYER_NAME_WORDS = ("string", "pad", "choir", "cello", "viola", "violin", "contrabass", "bass", "organ", "brass", "horn",
                    "flute", "oboe", "clarinet", "bassoon", "woodwind", "ensemble", "voices", "vox", "aah", "ooh",
                    "drone", "atmos", "warm layer", "echo")
ENDING_PAD_S = 3.0                         # seconds of clip after the last note-off (release, reverb) so render keeps it


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _roles(project):
    try:
        import flh_store
        ch = (flh_store.journal_get(project) or {}).get("channels") or {}
        return {int(k): str((v or {}).get("role") or "").lower() for k, v in ch.items() if str(k).isdigit()}
    except Exception:
        return {}


def _bpm_at(tempos, tick, default):
    bpm = default
    for t, b in tempos:
        if t <= tick:
            bpm = b
        else:
            break
    return bpm or default


def _secs(tempos, a, b, P):
    """Seconds from tick a to tick b through the tempo map [(tick, bpm)] (the last tempo holds after its point)."""
    if b <= a:
        return 0.0
    tm = sorted((t, x) for t, x in tempos if x and x > 0) or [(0, 120.0)]
    s, t, i = 0.0, a, 0
    bpm = tm[0][1]
    for tk, x in tm:
        if tk <= a:
            bpm = x
    for tk, x in tm:
        if tk <= t:
            continue
        if tk >= b:
            break
        s += (tk - t) / P * 60.0 / bpm; t, bpm = tk, x
    return s + (b - t) / P * 60.0 / bpm


def _profile_rules(project):
    """(rules dict with the defaults filled in, the profile's own rules) for this song - (None, None) when the active
    collection profile has no rules or exempts the song."""
    try:
        import flh_store
        prof = flh_store.profile_get() or {}
    except Exception:
        return None, None
    rl = prof.get("rules")
    if not isinstance(rl, dict) or not rl:
        return None, None
    try:
        j = flh_store.journal_get(project) or {}
    except Exception:
        j = {}
    stem = os.path.splitext(os.path.basename(project))[0]
    songs = prof.get("songs") or {}
    names = {stem, j.get("song")} | {k for k, v in songs.items() if isinstance(v, dict) and v.get("project")
                                     and os.path.normcase(os.path.abspath(v["project"])) ==
                                     os.path.normcase(os.path.abspath(project))}
    if any(n in (rl.get("exempt") or []) or (songs.get(n) or {}).get("rules_exempt") for n in names if n):
        return None, None
    R = {"min_ending_s": 30.0, "min_tail_s": 6.0}
    for k in R:
        try:
            if rl.get(k) is not None:
                R[k] = float(rl[k])
        except (TypeError, ValueError):
            pass
    return R, rl


def hold_for_rules(R, margin_s=None):
    """Seconds the final chord must sound from its attack so fl_finalize's diminuendo over the hold passes the rules:
    ending_length (last full-level moment -> silence) comes out at about HOLD_FADE_EFF of the faded span, so the hold
    is min_ending_s / that share (and at least min_tail_s), plus a margin. -> (hold_s, margin_s).
    (flh_finalize registers its own figure as ns['ending_hold_for_rules']; this is the same formula.)"""
    f = H.get("ending_hold_for_rules") if H else None
    if callable(f):
        try:
            return f(R, margin_s)
        except Exception:
            pass
    need = max(float(R.get("min_ending_s") or 0) / HOLD_FADE_EFF + 1.0, float(R.get("min_tail_s") or 0))
    m = max(2.0, 0.1 * need) if margin_s is None else float(margin_s)
    return need + m, m


HOLD_FADE_EFF = 0.8                        # ending_length / faded span of fl_finalize's hold diminuendo (measured)


def _bar_starts(d, ev, P, end):
    """Start ticks of the song's bars through its time-signature map, to two bars past `end`."""
    try:
        meters = [(int(t), int(n_), int(d_)) for t, n_, d_ in H["_meter_map"](d, ev) if n_ and d_] or [(0, 4, 4)]
    except Exception:
        meters = [(0, 4, 4)]
    out = []
    for i, (t0, num, den) in enumerate(meters):
        bl = P * num * 4.0 / den
        t1 = meters[i + 1][0] if i + 1 < len(meters) else end + 2 * bl
        t = float(t0)
        while t < t1 - 1e-6 and len(out) < 100000:
            out.append(int(round(t))); t += bl
    return out or [0]


def _layer_words(d, ev):
    """{channel: lower-case 'name plugin'} - the fallback when the journal has no role for a channel."""
    try:
        _ev, chans, _fx = H["_structure"](d)
        return {int(c["index"]): f"{c.get('name') or ''} {c.get('plugin') or ''}".lower() for c in chans.values()}
    except Exception:
        return {}


DECAY_WORDS = ("piano", "keys", "rhodes", "e.piano", "bell", "pluck", "harp", "guitar", "celesta", "marimba",
               "vibraphone", "glock", "music box", "kalimba")


def channel_kinds(project, d, ev):
    """channel -> 'sustain' (layers: strings, pads, choir... from the journal roles or the channel names) | 'drum' |
    'decay' (piano and other struck / plucked parts): what flh_finalize's ending simulation needs."""
    roles = _roles(project)
    words = _layer_words(d, ev)

    def kind(c):
        r = roles.get(c) or ""
        w = words.get(c, "")
        if r in DRUM_WORDS or (not r and any(x in w.split() for x in DRUM_WORDS)):
            return "drum"
        if any(x in (r or w) for x in DECAY_WORDS):
            return "decay"
        if r in LAYER_ROLES or (not r and any(x in w for x in LAYER_NAME_WORDS)):
            return "sustain"
        return "decay"
    return kind


def _sim_check(H_, notes, tm, P, changed_len, R, kind):
    """flh_finalize.simulate_ending on the notes as they will be after the extend -> its result or None."""
    f = H_.get("simulate_ending") if H_ else None
    if not callable(f):
        return None
    out = []
    memo = {}
    for n in notes:
        pos, ln, ch, key, vel = n[:5]
        ln = changed_len.get(id(n), ln)
        if ch not in memo:
            memo[ch] = kind(ch)
        on = _secs(tm, 0, pos, P)
        out.append((on, on + _secs(tm, pos, pos + max(1, ln), P), float(vel), int(key), memo[ch]))
    try:
        tl = None
        try:
            import flh_store
            tl = ((flh_store.profile_get() or {}).get("finish") or {}).get("lufs")
        except Exception:
            pass
        return f(out, R, float(tl) if tl is not None else -16.0)
    except Exception:
        return None


def _audible_gain(project, d, ev, notes, tm, P, lens, R):
    """How much later the song's SOUND ends with the held notes, simulated from the notes (flh_finalize's ending
    simulation: struck / plucked notes - piano, harp... - decay by themselves; sustained layers hold to their note-off):
    {end_s_before, end_s_after, added_s, estimate, note?}. A held piano chord that has already died away adds
    (almost) nothing however long its keys are held - said, so 'added_s' isn't read as seconds of music
    (PC 2026-10-23: +20 s written, the FINAL as long as before). None without the simulation."""
    try:
        kind = channel_kinds(project, d, ev)
        s0 = _sim_check(H, notes, tm, P, {}, R, kind)
        s1 = _sim_check(H, notes, tm, P, lens, R, kind)
        if not s0 or not s1:
            return None
        e0, e1 = float(s0["end_s"]), float(s1["end_s"])
        written = max(_secs(tm, n[0] + n[1], n[0] + lens[id(n)], P) for n in notes if id(n) in lens)
    except Exception:                                             # noqa: BLE001 - a report only
        return None
    out = {"end_s_before": round(e0, 1), "end_s_after": round(e1, 1), "added_s": round(e1 - e0, 1),
           "estimate": "simulated from the notes (the render is the proof)"}
    if written >= 4.0 and e1 - e0 < 0.5 * written:
        out["note"] = (f"the notes are held {written:.0f} s longer, but the sound is expected to last only "
                       f"~{max(0.0, e1 - e0):.0f} s longer: the held parts decay by themselves (piano and other struck / "
                       "plucked sounds), so the FINAL ends where they fall silent. For a longer ending let a sustained "
                       "part hold (strings, pad, choir) or slow the last bars (ritard)")
    return out


def flp_extend_ending(project, output=None, seconds=None, layers_release=0.6, ritard=0.0, dry_run=False, hold_s=None,
                      layers_gap_s=None, margin_s=None):
    """Make the song's last chord long enough. Three ways:
    - default (no seconds / hold_s) with collection rules (min_ending_s, min_tail_s): the final chord sounds long
      enough IN SECONDS from its attack (through the tempo map) for fl_finalize's diminuendo to pass them, plus a
      margin; without rules: 20 s more (as `seconds`).
    - hold_s: the final chord sounds this many seconds from its attack (main parts; layers end layers_gap_s earlier).
    - seconds: hold it `seconds` longer than now; layers hold `layers_release` of the added time.
    Layers (strings, pads, choir... from the journal roles or the channel names) let go first so the main part
    (e.g. piano) rings alone last. The clips holding the chord grow so nothing is cut at render. ritard: slow the
    last two bars by that share (0.1 = 10 % slower)."""
    output = output or project
    H["writable"](output)
    R = rl = None
    if hold_s is None and seconds is None:
        R, rl = _profile_rules(project)
        if R is None:
            seconds = 20.0
    if seconds is not None:
        seconds = float(seconds)
        if not 0 < seconds <= 120:
            raise ValueError("seconds: 0..120")
    if hold_s is not None and not 0 < float(hold_s) <= 240:
        raise ValueError("hold_s: 0..240 (seconds the final chord sounds from its attack)")
    if layers_gap_s is not None and not 0 <= float(layers_gap_s) <= 30:
        raise ValueError("layers_gap_s: 0..30 (seconds the layers end before the main part)")
    if not 0 <= float(layers_release) <= 1:
        raise ValueError("layers_release: 0..1 (the share of the added time the layers hold)")
    if not 0 <= float(ritard or 0) <= 0.5:
        raise ValueError("ritard: 0..0.5 (0.1 = the last two bars 10 % slower)")
    margin = None
    if R is not None:
        hold_s, margin = hold_for_rules(R, margin_s)

    def run():
        d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
        notes, tempos = H["_song_x"](d, ev)
        if not notes:
            raise ValueError("the song has no notes")
        H["_sane_span"](max(n[0] + n[1] for n in notes), P, "a note")   # a corrupt far note: not seconds of GBs
        main = next((b for _, b in tempos), None) or 120.0
        roles = _roles(project)
        words = _layer_words(d, ev)
        role_of = lambda c: roles.get(c) or ""
        is_drum = lambda c: role_of(c) in DRUM_WORDS or (not role_of(c) and any(w in words.get(c, "").split()
                                                                                 for w in DRUM_WORDS))
        pitched = [n for n in notes if not is_drum(n[2])] or notes
        last_on = max(n[0] for n in pitched)
        last_end = max(n[0] + n[1] for n in notes)
        drum = {n[2] for n in notes if is_drum(n[2])}
        # the final chord: what starts within half a beat of the last onset or is still sounding then
        chord = [n for n in notes if n[2] not in drum and (n[0] >= last_on - P // 2 or n[0] + n[1] > last_on)]
        if not chord:
            raise ValueError("no pitched notes sound at the end (only drums?)")
        r_ = float(ritard or 0)
        flat = len({round(b, 3) for _, b in tempos}) <= 1
        tm = [(0, main), (last_on, main * (1 - r_))] if r_ > 0 and flat else tempos   # the ritard about to be written
        is_layer = lambda c: role_of(c) in LAYER_ROLES or (not role_of(c) and any(w in words.get(c, "")
                                                                                 for w in LAYER_NAME_WORDS))
        tfs = H["_ticks_for_seconds"]
        before_s = round(_secs(tm, last_on, last_end, P), 1)
        hs = None
        if hold_s is not None:                                  # seconds from the attack, through the tempo map
            hs = float(hold_s)
            gap = float(layers_gap_s) if layers_gap_s is not None else min(5.0, max(1.5, 0.1 * hs))
            new_end = last_on + tfs(tm, last_on, hs, P)
            lay_end = last_on + tfs(tm, last_on, max(0.5, hs - gap), P)
            lay_now = max([n[0] + n[1] for n in chord if is_layer(n[2])] + [0])
            if lay_now > lay_end:                               # layers already ring longer: never shortened - the
                new_end = max(new_end, lay_now + tfs(tm, lay_now, gap, P))   # main part outlasts them instead
        else:
            extra = tfs(tm, last_end, seconds, P)
            new_end = last_end + extra
            lay_end = last_end + int(extra * float(layers_release))
        pats = H["_patterns"](d, ev)
        clips = H["_clips"](d, ev)
        uses = {}
        for _, _, r in clips:
            pid = int.from_bytes(r[6:8], "little") - H["PATTERN_BASE"]
            uses[pid] = uses.get(pid, 0) + 1
        sim = None
        if R is not None:                                       # the render-side rules, simulated: grow until they pass
            kind = channel_kinds(project, d, ev)
            tol = max(1.5, 0.08 * R["min_ending_s"]) + 0.5
            for _ in range(5):
                lens = {id(n): max(n[1], (lay_end if is_layer(n[2]) else new_end) - n[0]) for n in chord}
                sim = _sim_check(H, notes, tm, P, lens, R, kind)
                if sim is None:
                    break
                tol_t = max(1.5, 0.08 * R["min_tail_s"]) + 0.5
                short = max(R["min_ending_s"] + tol - float(sim["ending_s"]), R["min_tail_s"] + tol_t - float(sim["tail_s"]))
                if short <= 0 or hs >= 240:
                    break
                hs = min(240.0, hs + short / 0.9 + 0.5)
                new_end = last_on + tfs(tm, last_on, hs, P)
                lay_end = last_on + tfs(tm, last_on, max(0.5, hs - gap), P)
        changed = {}                                                # pid -> {record bytes: new length}
        held = []
        wl = {}                                                     # id(note) -> its new length (the simulation)
        for n in chord:
            pos, ln, ch, key, vel, _sl, pid, rec = n
            target = (lay_end if is_layer(ch) else new_end) - pos
            if target <= ln:
                continue
            changed.setdefault(pid, {})[bytes(rec) if isinstance(rec, (bytes, bytearray)) else tuple(rec)] = target
            held.append({"channel": ch, "key": key, "layer": is_layer(ch)})
            wl[id(n)] = target
        meters = H["_meter_map"](d, ev)
        bar_of = lambda t: round(H["_tick_to_bar"](t, meters, P), 2)
        pad = tfs(tm, new_end, ENDING_PAD_S, P)                     # release / reverb after the last note-off
        res = {"output": output, "held_notes": len(held), "channels": sorted({h["channel"] for h in held}),
               "end_bar_before": bar_of(last_end), "end_bar_after": bar_of(max(new_end, last_end))}
        end_of = lambda lay: max([max(n[0] + n[1], (lay_end if lay else new_end)) for n in chord
                                  if bool(is_layer(n[2])) == lay] + [0])
        m_end, l_end = end_of(False), end_of(True)
        main_s = round(_secs(tm, last_on, m_end, P), 1) if m_end else None
        lay_s = round(_secs(tm, last_on, l_end, P), 1) if l_end else None
        res["hold"] = {"main_s": main_s, "layers_s": lay_s,
                       "main_beats": round((m_end - last_on) / P, 1) if m_end else None,
                       "layers_beats": round((l_end - last_on) / P, 1) if l_end else None,
                       "before_s": before_s, "bpm_at_end": round(_bpm_at(tm, last_on, main), 1),
                       "attack_bar": bar_of(last_on)}
        if hold_s is not None:
            res["mode"] = "rules" if R is not None else "hold_s"
        else:
            res["mode"] = "seconds"; res["added_s"] = round(seconds, 1)
        if R is not None:
            res["rules"] = {"min_ending_s": R["min_ending_s"], "min_tail_s": R["min_tail_s"], "margin_s": round(margin, 1),
                            "from": "collection profile"}
            res["why"] = (f"the final chord sounds {main_s:g} s from its attack (min_ending_s {R['min_ending_s']:g} / "
                          f"{HOLD_FADE_EFF:g} - fl_finalize's diminuendo over the hold - plus {margin:.1f} s margin), "
                          "computed through the tempo map")
        if sim is not None:
            res["simulated"] = {"ending_s": sim["ending_s"], "tail_s": sim["tail_s"], "fade": sim["fade"],
                                "why": "fl_finalize + rule_check simulated from the notes (flh_finalize.simulate_ending)"}
            if R is not None and abs(hs - float(hold_s)) > 0.05:
                res["hold_grown_s"] = round(hs - float(hold_s), 1)
        if held:                                                    # what the longer hold adds to the SOUND
            aud = _audible_gain(project, d, ev, notes, tm, P, wl, R)
            if aud:
                res["audible"] = aud
        if not held:
            res["note"] = (f"the final chord already sounds {before_s:g} s from its attack - nothing to hold longer")
        rep = [pid for pid in changed if uses.get(pid, 0) > 1]
        if rep:
            res["note"] = (f"pattern(s) {rep} play more than once: their other copies hold these notes too - make the "
                           "last one unique first (flp_pattern_tools clone) if that's not wanted")
        if dry_run:
            res["dry_run"] = True
            return res
        for pid, want in changed.items():
            ns_ = []
            for r in pats[pid]["notes"]:
                nd = H["_rec_to_note"](r)
                k = bytes(r) if isinstance(r, (bytes, bytearray)) else tuple(r)
                if k in want:
                    nd = dict(nd, len=want[k])
                ns_.append(nd)
            d = H["_write_pattern_notes"](d, H["_events"](d), pid, ns_)
            ev = H["_events"](d)
        if changed:
            H["_save"](d, output)
        elif not _same(project, output):
            H["_save"](d, output)
        # clips holding the final chord grow past the new end (+ the release), so the render doesn't cut the ring
        # PC-VERIFY: FL's render of the extended song keeps the whole hold and its release (nothing cut at the end)
        grown = 0
        if changed:
            lst = H["flp_playlist"](output, "list")
            bar_t = H["_song_bar_ticks"](d, ev, P)                  # flp_playlist's resize unit (the time signature)
            want_end = max(new_end, lay_end) + pad
            for c in lst.get("clips", []):
                if c.get("kind") != "pattern" or c.get("pattern") not in changed:
                    continue
                start = H["_bar_to_tick"](float(c["bar"]), meters, P)
                end = H["_bar_to_tick"](float(c["bar"]) + float(c["length_bars"]), meters, P)
                if start <= last_on < end + 1 and end < want_end:
                    H["flp_playlist"](output, "resize", output=output, clips=[c["id"]],
                                      length_bars=round((want_end - start) / bar_t + 0.01, 3))
                    grown += 1
        res["clips_grown"] = grown
        if changed and main_s:                                  # remembered: a later pass that undoes it is spotted
            try:
                import flh_store
                flh_store.journal_update(output, set={"ending_hold": {"main_s": main_s, "layers_s": lay_s,
                                                                       "mode": res["mode"]}})
            except Exception:
                pass
        if ritard and float(ritard) > 0:
            if flat:
                b0 = max(1.0, bar_of(last_on) - 2)
                H["flp_tempo_write"](output, output, points=[[round(b0, 3), main], [bar_of(last_on),
                                                                                    round(main * (1 - float(ritard)), 2)]])
                res["ritard"] = f"{main:g} -> {main * (1 - float(ritard)):.1f} BPM over the last two bars"
            else:
                res["ritard_note"] = "the song already has a tempo map: no ritardando added (shape it with flp_tempo_write)"
        res["next"] = ("finish with fl_finalize (fade_s auto or the profile's default): the held chord fades as a long "
                       "diminuendo; its rules check says what still fails" if hold_s is not None else
                       "render and run audio_rule_check (ending_length, tail_ring, ends_in_silence)")
        return res
    if dry_run:
        return run()
    return H["_with_open_project"](project, output, run)


def flp_smooth_entries(project, output=None, jump_db=9.0, start=0.55, ramp_beats=4.0, dry_run=False):
    """Soften entrances that jump: a part that starts after 2+ silent bars while the song's note energy jumps by more
    than jump_db gets its first ramp_beats of notes ramped from `start` x velocity up to full."""
    output = output or project
    H["writable"](output)
    if not 0 < float(start) <= 1:
        raise ValueError("start: 0..1 (the first note's share of its velocity, e.g. 0.55)")
    if not 0 < float(ramp_beats) <= 64:
        raise ValueError("ramp_beats: more than 0 (beats to reach full velocity)")
    if not float(jump_db) > 0:
        raise ValueError("jump_db: more than 0 dB")

    def run():
        d = H["_load"](project); ev = H["_events"](d); P = H["_ppq"](d)
        notes, _ = H["_song_x"](d, ev)
        if not notes:
            raise ValueError("the song has no notes")
        roles = _roles(project)
        starts = _bar_starts(d, ev, P, max(n[0] + n[1] for n in notes))   # bars of the song's meter map
        nb = len(starts)
        blen = [starts[i + 1] - starts[i] for i in range(nb - 1)] + [starts[-1] - starts[-2] if nb > 1 else 4 * P]
        bar_of = lambda t: max(0, bisect.bisect_right(starts, t) - 1)
        energy = [1e-9] * nb
        for n in notes:
            if roles.get(n[2], "") in DRUM_WORDS:
                continue
            b = bar_of(n[0])
            energy[b] += (n[4] / 128.0) ** 2 * min(n[1], blen[b]) / blen[b]
        L = [10 * math.log10(e) for e in energy]
        first = {}
        for n in sorted(notes):
            first.setdefault(n[2], []).append(n)
        found, edits = [], {}
        for ch, ns_ in first.items():
            if roles.get(ch, "") in DRUM_WORDS:
                continue
            ons = sorted({bar_of(n[0]) for n in ns_})
            prev = None
            for b in ons:
                gap = b - prev if prev is not None else b
                if (prev is None or gap > 2) and b > 0:
                    jump = L[b] - max(L[max(0, b - 2):b])
                    if jump > jump_db:
                        t0 = min(n[0] for n in ns_ if bar_of(n[0]) == b)      # the ramp starts at the entry
                        span = ramp_beats * P
                        hit = [n for n in ns_ if t0 <= n[0] < t0 + span]
                        found.append({"channel": ch, "bar": b + 1, "jump_db": round(jump, 1), "notes": len(hit)})
                        for n in hit:
                            f = start + (1 - start) * ((n[0] - t0) / span)
                            k = bytes(n[7]) if isinstance(n[7], (bytes, bytearray)) else tuple(n[7])
                            edits.setdefault(n[6], {})[k] = max(1, int(round(n[4] * f)))
                prev = b
        res = {"output": output, "entries": found}
        clips = H["_clips"](d, ev); uses = {}
        for _, _, r in clips:
            pid = int.from_bytes(r[6:8], "little") - H["PATTERN_BASE"]
            uses[pid] = uses.get(pid, 0) + 1
        rep = sorted(pid for pid in edits if uses.get(pid, 0) > 1)
        if rep:                                                    # the notes live in the pattern: every copy ramps
            res["note"] = (f"pattern(s) {rep} play more than once: their other copies ramp in too - make the entry's "
                           "copy unique first (flp_pattern_tools clone) if that's not wanted")
        if dry_run or not edits:
            res["dry_run"] = bool(dry_run)
            if not found:
                res["note"] = f"no entrance jumps over {jump_db:g} dB"
            return res
        pats = H["_patterns"](d, ev)
        for pid, want in edits.items():
            ns2 = []
            for r in pats[pid]["notes"]:
                nd = H["_rec_to_note"](r)
                k = bytes(r) if isinstance(r, (bytes, bytearray)) else tuple(r)
                if k in want:
                    nd = dict(nd, vel=want[k])
                ns2.append(nd)
            d = H["_write_pattern_notes"](d, H["_events"](d), pid, ns2)
        H["_save"](d, output)
        res["changed_notes"] = sum(len(v) for v in edits.values())
        return res
    if dry_run:
        return run()
    return H["_with_open_project"](project, output, run)


def register(ns):
    global H
    H = ns
    S, P = ns["_schema"], ns["_p"]
    STR, NUM, BOOL = ns["_STR"], ns["_NUM"], ns["_BOOL"]
    ns["_ending_channel_kinds"] = channel_kinds
    ns["TOOLS"]["flp_extend_ending"] = (
        ns["_guard_open_project"](flp_extend_ending, "project", "output"),
        "Make the ending long and soft: the last chord sounds long enough IN SECONDS (through the tempo map) on every "
        "part playing at the end - by default from the collection's rules (min_ending_s, min_tail_s) so fl_finalize's "
        "diminuendo over the hold passes them on the first render; hold_s sets it; seconds adds that much (no rules: "
        "20 s more). Layers (strings/pads/choir) end a little earlier so the main part rings alone last; the clips grow "
        "so the render keeps the ring; optional ritard. Reports the seconds and beats it computed.",
        S(project=ns["_FLP"], output=ns["_OUT"],
          seconds=NUM("seconds added to the last chord (default: from the rules; no rules: 20)", True),
          hold_s=NUM("the final chord sounds this many seconds from its attack (default: from the rules)", True),
          layers_gap_s=NUM("layers end this many seconds before the main part (default 10 % of the hold, 1.5-5 s)", True),
          margin_s=NUM("extra seconds over what the rules need (default 10 %, at least 2 s)", True),
          layers_release=NUM("with seconds: layers let go at this share of the added time (default 0.6)", True),
          ritard=NUM("slow the last two bars by this share, e.g. 0.1 (default 0: none)", True),
          dry_run=BOOL("report what would change", True)))
    ns["TOOLS"]["flp_smooth_entries"] = (
        ns["_guard_open_project"](flp_smooth_entries, "project", "output"),
        "Soften parts that enter out of nowhere: where a part starts after 2+ empty bars and the song's note energy "
        "jumps more than jump_db, its first notes ramp in (velocity start% -> full over ramp_beats). dry_run lists them.",
        S(project=ns["_FLP"], output=ns["_OUT"], jump_db=NUM("energy jump that counts (default 9 dB)", True),
          start=NUM("first note's share of its velocity (default 0.55)", True),
          ramp_beats=NUM("beats to reach full velocity (default 4)", True), dry_run=BOOL("only list them", True)))
    for t in ("flp_extend_ending", "flp_smooth_entries"):
        ns.setdefault("_OPEN_PROJECT_GUARD", {})[t] = ("project", "output")
        ns.setdefault("TOOL_GROUPS", {})[t] = "compose"
