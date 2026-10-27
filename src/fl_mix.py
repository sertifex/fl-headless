"""Mix engineer: turns per-track measurements (each mixer track heard solo) into concrete, ranked fixes.
Pure functions, no FL access: the server measures, this decides, the server applies."""

BAND_HZ = {"sub": 40, "bass": 120, "low_mid": 400, "mid": 1400, "presence": 3500, "air": 9000}
ROLE_WORDS = [(3.0, ("lead", "melody", "vocal", "vox", "solo")),
              (2.0, ("bass", "kick", "808", "sub")),
              (1.5, ("piano", "keys", "guitar", "accomp", "chord", "rhodes", "pluck")),
              (1.0, ("pad", "string", "soft", "layer", "choir", "ambien", "fx", "texture", "swell"))]


def importance(name):
    n = (name or "").lower()
    for w, words in ROLE_WORDS:
        if any(k in n for k in words):
            return w
    return 1.5


def _band_abs(m, band):
    """Approximate absolute level of one band: loudness + that band's share of the track's spectrum."""
    if m.get("lufs") is None:
        return None
    return m["lufs"] + m["bands_db"][band]


def propose(tracks, mix=None, max_items=8):
    """tracks: {track_index: {"name", "lufs", "bands_db", "peak_dbfs", ...}} (each heard solo).
    Returns ranked fixes: [{id, track, name, action: fader|eq_cut|highpass|master, db, hz, why}]."""
    out = []
    live = {t: m for t, m in tracks.items() if m.get("lufs") is not None and m["lufs"] > -60}
    if not live:
        return []
    imp = {t: importance(m.get("name")) for t, m in live.items()}
    lead = max(live, key=lambda t: (imp[t], live[t]["lufs"]))
    lead_l = live[lead]["lufs"]
    # 1. support parts too close to the lead
    for t, m in live.items():
        if t == lead or imp[t] >= imp[lead]:
            continue
        gap = 6.0 if imp[t] <= 1.0 else (3.0 if imp[t] <= 1.5 else 0.0)
        over = m["lufs"] - (lead_l - gap)
        if gap and over > 0.75:
            cut = min(6.0, round(over * 2) / 2)
            out.append({"track": t, "name": m.get("name"), "action": "fader", "db": -cut, "score": over * imp[lead],
                        "why": f"{m.get('name')} is {m['lufs'] - lead_l:+.1f} dB vs the lead ({live[lead].get('name')}); "
                               f"a {'pad/layer' if imp[t] <= 1 else 'support part'} sits better ~{gap:g} dB under it"})
    # 2. two parts fighting in the same band: cut the less important one there
    for band in ("low_mid", "mid", "presence"):
        lv = {t: _band_abs(m, band) for t, m in live.items()}
        top = max(lv.values())
        strong = [t for t, v in lv.items() if v is not None and v > top - 4.0]
        strong.sort(key=lambda t: -imp[t])
        for loser in strong[1:]:
            winner = strong[0]
            if imp[loser] >= imp[winner]:
                continue
            if live[winner]["lufs"] - live[loser]["lufs"] > 9:    # already far under the lead: a dip only buries it
                continue
            out.append({"track": loser, "name": live[loser].get("name"), "action": "eq_cut", "hz": BAND_HZ[band],
                        "db": -2.5, "score": 2.0 + (lv[loser] - top + 4),
                        "why": f"{live[loser].get('name')} and {live[winner].get('name')} are both strong around "
                               f"{BAND_HZ[band]} Hz ({band.replace('_', '-')}): a 2.5 dB dip makes room for "
                               f"{live[winner].get('name')}"})
    # 3. low end: only bass-role parts should carry real sub/bass energy
    bass_parts = [t for t in live if imp[t] == 2.0]
    for t, m in live.items():
        if imp[t] == 2.0 or not bass_parts:
            continue
        if m["bands_db"]["sub"] > -18 or m["bands_db"]["bass"] > -6:
            out.append({"track": t, "name": m.get("name"), "action": "highpass", "hz": 120 if imp[t] <= 1 else 80,
                        "score": 1.5, "why": f"{m.get('name')} has low end that overlaps the bass part; a high-pass "
                                             f"keeps the bottom clean"})
    # 4. the whole mix
    if mix:
        tp = mix.get("true_peak_dbfs", mix.get("peak_dbfs"))
        if tp is not None and tp > -1.0:
            out.append({"track": 0, "name": "Master", "action": "master", "db": round(-1.0 - tp - 0.5, 1),
                        "score": 100.0 if tp >= 0 else 10.0,
                        "why": f"true peak {tp:+.1f} dBFS: {'clipping' if tp >= 0 else 'too close to 0'}; "
                               f"lower the master or the loudest part"})
        if mix.get("bands_db", {}).get("low_mid", -99) > -3.0:
            out.append({"track": 0, "name": "Master", "action": "note", "score": 1.0,
                        "why": f"the mix is heavy at 250-800 Hz ({mix['bands_db']['low_mid']:+.1f} dB share): "
                               f"check the eq_cut suggestions above"})
    out.sort(key=lambda p: -p["score"])
    seen, final = set(), []
    for p in out:
        k = (p["track"], p["action"], p.get("hz"))
        if k in seen:
            continue
        seen.add(k); final.append(p)
    for i, p in enumerate(final[:max_items], 1):
        p["id"] = i; p.pop("score", None)
    return final[:max_items]


def eq_band_for(hz, kind):
    """Which band of Fruity Parametric EQ 2 to use (index 0..6): band 1 = high-pass, else the band nearest hz."""
    if kind == "highpass":
        return 0
    centers = [63, 136, 294, 632, 1363, 2936, 6324]
    # band 7 (index 6) is PEQ2's high shelf: used for air / presence moves at 5 kHz and up, where a shelf is right
    cands = range(1, 7) if hz >= 5000 else range(1, 6)
    return min(cands, key=lambda i: abs(__import__("math").log(hz / centers[i])))
