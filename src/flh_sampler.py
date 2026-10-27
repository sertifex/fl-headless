"""flh_sampler: shape FL's built-in Sampler (and audio clip) channels the way a producer does in Channel settings -
volume envelope, the channel filter, pitch, reverse, declicking, cut itself - so 'tighter', 'longer tail', 'softer
attack', 'darker' change the sample channel itself (effect chains are flh_sounddesign's job).

Two routes, both checked against the FL 2026 API / file format:
  FILE (what sampler_shape uses): the channel block of the .flp. Every edit replaces an event's payload in place
       (same length), so undo puts the exact bytes back. On the project open in FL it goes through
       _with_open_project: FL saves, the file is edited, FL reloads once.
  LIVE (sampler_live): FL's Python API has no setter for a Sampler's envelope, reverse, declick or cut groups, and the
       `plugins` module sees nothing on a Sampler (it isn't a plugin: plugins.isValid(ch) is False, slot -1 has no
       params). What it has: channels.setChannelPitch / getChannelPitch (the channel's Pitch knob, cents) and the
       channel filter through REC events (channels.getRecEventId(ch) + midi.REC_Chan_FCut / REC_Chan_FRes). The
       bridge command `sampler_live` uses only those; PC-VERIFY the REC value scale.

Where the settings live in the file (layouts from PyFLP's ChannelID / EnvelopeLFO / Levels / Parameters events; every
default value in FL 2026 corpus files matches them):
  218 x5 envelope+LFO per target in order pan, volume, mod X, mod Y, pitch. int32s: flags 0, enabled 4, delay 8,
         attack 12, hold 16, decay 20, sustain 24 (0..128), release 28, amount 32, LFO 36..55, attack tension 56,
         decay tension 60, release tension 64 (-128..128). Times are raw 100..65536 (knob position).
  219    levels: pan 0, volume 4, pitch 8 (cents), mod X = filter cutoff 12 (0..256), mod Y = resonance 16 (0..256),
         filter type 20 (Fast LP, LP, BP, HP, BS, LPx2, SVF LP, SVF LPx2).
  215    parameters: +83 declick mode (u8), +100 PITCH cents (mapped on FL 2026 by diffing, see flp_audio_clip_settings)
  70     effect flags: 2 = reverse (mapped on FL 2026)
  132    cut groups: u16 cut, u16 cut by (same non-zero group in both = the channel cuts itself)
"""
import base64
import json
import math
import os
import struct

H = None
EV_ENV, EV_LEVELS, EV_PARAMS, EV_FX_FLAGS, EV_CUT, EV_NAME = 218, 219, 215, 70, 132, 203
ENV_TARGETS = ("pan", "volume", "mod_x", "mod_y", "pitch")
VOL = 1                                                         # the volume envelope is the 2nd 218 event
SAMPLER_TYPES = {0: "sampler", 4: "audio clip"}

ENV_TIME = {"predelay_ms": 8, "attack_ms": 12, "hold_ms": 16, "decay_ms": 20, "release_ms": 28}
ENV_TENSION = {"attack_tension": 56, "decay_tension": 60, "release_tension": 64}
FIELDS = (tuple(ENV_TIME) + ("sustain_pct",) + tuple(ENV_TENSION) +
          ("envelope", "cutoff", "resonance_pct", "filter_type", "pitch_st", "fine_cents", "reverse", "declick",
           "cut_itself"))
ALIASES = {"delay_ms": "predelay_ms", "cutoff_hz": "cutoff", "resonance": "resonance_pct", "sustain": "sustain_pct",
           "pitch": "pitch_st", "fine": "fine_cents", "volume_envelope": "envelope", "cut": "cut_itself",
           "attack": "attack_ms", "decay": "decay_ms", "release": "release_ms", "hold": "hold_ms"}
ESTIMATED = ("predelay_ms", "attack_ms", "hold_ms", "decay_ms", "release_ms", "cutoff_hz")
_CURVES = None


def curves():
    """data/sampler/curves.json (a user copy in fl-headless's state folder wins)."""
    global _CURVES
    if _CURVES is None:
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sampler", "curves.json")
        paths = ([os.path.join(os.path.dirname(H["STATE"]), "sampler", "curves.json")] if H and H.get("STATE") else [])
        for p in paths + [here]:
            try:
                with open(p, encoding="utf-8") as f:
                    _CURVES = json.load(f)
                break
            except (OSError, ValueError):
                continue
    return _CURVES


# ----------------------------------------------------------------------------- unit conversions (curves.json)
def _ms(raw):
    c = curves()["env_time"]
    x = min(1.0, max(0.0, (raw - c["min_raw"]) / float(c["max_raw"] - c["min_raw"])))
    return c["max_ms"] * x ** c["exp"]


def _raw_ms(ms):
    # PC-VERIFY: FL's hint for attack/decay/release matches _ms() within ~20 % (tune data/sampler/curves.json)
    c = curves()["env_time"]
    x = min(1.0, max(0.0, float(ms) / c["max_ms"])) ** (1.0 / c["exp"])
    return int(round(c["min_raw"] + x * (c["max_raw"] - c["min_raw"])))


def _hz(raw):
    c = curves()["cutoff"]
    return c["min_hz"] * (c["max_hz"] / c["min_hz"]) ** (min(c["max_raw"], max(0, raw)) / float(c["max_raw"]))


def _raw_hz(hz):
    # PC-VERIFY: Mod X (cutoff) raw 0..256 maps log-like to ~20 Hz..20 kHz (FL shows % only; listen to check)
    c = curves()["cutoff"]
    hz = min(c["max_hz"], max(c["min_hz"], float(hz)))
    return int(round(c["max_raw"] * math.log(hz / c["min_hz"]) / math.log(c["max_hz"] / c["min_hz"])))


def _st(cents):
    """Whole semitones of a cents value (nearest; the rest is fine tune -50..+49)."""
    return int(math.floor(cents / 100.0 + 0.5))


def _num(s):
    x = float(str(s).strip().replace(",", "."))
    if not math.isfinite(x):
        raise ValueError(f"not a usable number: {s!r}")
    return x


def _parse(v, absolute):
    """A move -> (kind, number): kind 'delta' | 'ratio' | 'abs' | 'abs_ms' | 'abs_hz' | 'abs_pct'."""
    if isinstance(v, bool):
        raise ValueError("needs a number or a string like 'x2', '+20', '=500', '500 ms', '2000 Hz', '40%'")
    if isinstance(v, (int, float)):
        return ("abs" if absolute else "delta"), float(v)
    s = str(v).strip().lower().replace(" ", "")
    if s[:1] in ("x", "*"):
        return "ratio", _num(s[1:])
    if s[:1] == "/":
        x = _num(s[1:])
        if not x:
            raise ValueError("can't divide by 0")
        return "ratio", 1.0 / x
    if s[:1] == "=":
        return "abs", _num(s[1:])
    if s.endswith("khz") or (s.endswith("k") and s[:-1].replace(".", "").isdigit()):
        return "abs_hz", _num(s.rstrip("hz").rstrip("k")) * 1000
    if s.endswith("hz"):
        return "abs_hz", _num(s[:-2])
    if s.endswith("ms"):
        return "abs_ms", _num(s[:-2])
    if s.endswith("s") and s[:-1].replace(".", "").isdigit():
        return "abs_ms", _num(s[:-1]) * 1000
    if s.endswith("%"):
        return "abs_pct", _num(s[:-1])
    if s.endswith("st"):
        s = s[:-2]
    elif s.endswith("oct"):
        s = s[:-3]
    return ("delta" if s[:1] in "+-" or not absolute else "abs"), _num(s)


def _bool(v):
    if isinstance(v, str):
        if v.strip().lower() in ("1", "true", "on", "yes"):
            return True
        if v.strip().lower() in ("0", "false", "off", "no", "none"):
            return False
        raise ValueError("needs true/false")
    return bool(v)


def _pick(name, options):
    n = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    n = {"lowpass": "lp", "low_pass": "lp", "highpass": "hp", "high_pass": "hp", "bandpass": "bp", "band_pass": "bp",
         "notch": "bs", "bandstop": "bs", "band_stop": "bs", "fast": "fast_lp", "svf": "svf_lp", "off": "out_only",
         "default": "out_only", "none": "out_only"}.get(n, n)
    if n not in options:
        raise ValueError("one of " + ", ".join(options))
    return options.index(n)


# ----------------------------------------------------------------------------- channel block access
class _Block:
    """The sampler-related events of one channel: payloads to edit in place, the originals for undo."""

    def __init__(self, d, ev, ch):
        self.d, self.ev, self.ch = d, ev, ch
        rng = H["_channel_ranges"](d, ev).get(ch)
        if rng is None:
            raise ValueError(f"no channel {ch}")
        self.idx = {}
        for k in range(*rng):
            self.idx.setdefault(ev[k]["id"], []).append(k)
        self.cur, self.orig = {}, {}

    def has(self, eid, n=0, size=0):
        ks = self.idx.get(eid) or []
        return len(ks) > n and self.ev[ks[n]]["len"] >= size

    def p(self, eid, n=0):
        if (eid, n) not in self.cur:
            raw = bytes(H["_payload"](self.d, self.ev[self.idx[eid][n]]))
            self.orig[(eid, n)] = raw
            self.cur[(eid, n)] = bytearray(raw)
        return self.cur[(eid, n)]

    def get(self, eid, n, fmt, off):
        return struct.unpack_from(fmt, self.p(eid, n), off)[0]

    def put(self, eid, n, fmt, off, v):
        struct.pack_into(fmt, self.p(eid, n), off, v)

    def changed(self):
        return {key: bytes(b) for key, b in self.cur.items() if bytes(b) != self.orig[key]}

    def write(self):
        """-> new project bytes with the changed payloads (same lengths: nothing else moves)."""
        edits = []
        for (eid, n), b in self.changed().items():
            k = self.idx[eid][n]
            edits.append((k, k + 1, _enc(eid, b)))
        return H["_splice"](self.d, self.ev, sorted(edits)) if edits else self.d


def _enc(eid, payload):
    return bytes([eid]) + payload if eid < 192 else H["_data_event"](eid, payload)


def _names(d, ev):
    out = {}
    for ch, (a, b) in H["_channel_ranges"](d, ev).items():
        k = next((k for k in range(a, b) if ev[k]["id"] == EV_NAME), None)
        if k is not None:
            out[ch] = H["_text16"](H["_payload"](d, ev[k]))
    return out


def _resolve(d, ev, channel):
    if isinstance(channel, int) or str(channel).strip().lstrip("-").isdigit():
        return int(channel)
    names = _names(d, ev)
    want = str(channel).strip().lower()
    hits = [c for c, n in names.items() if n.lower() == want] or [c for c, n in names.items() if want in n.lower()]
    if len(hits) != 1:
        raise ValueError(f"channel {channel!r}: " + ("no such name" if not hits else f"matches {sorted(hits)}"))
    return hits[0]


def _check_type(d, ev, ch):
    t = H["_channel_types"](d, ev).get(ch)
    if t is None:
        raise ValueError(f"no channel {ch}")
    if t not in SAMPLER_TYPES:
        kind = {2: "plugin", 3: "layer", 5: "automation"}.get(t, f"type {t}")
        raise ValueError(f"channel {ch} is a {kind} channel, not a Sampler or audio clip: shape a plugin with "
                         "fl_sound_shape (its own controls + effects)")
    return SAMPLER_TYPES[t]


# ----------------------------------------------------------------------------- read
def _read(blk):
    out, c = {}, curves()
    if blk.has(EV_ENV, VOL, 68):
        g = lambda off: blk.get(EV_ENV, VOL, "<i", off)
        out["envelope"] = bool(g(4))
        for f, off in ENV_TIME.items():
            out[f] = round(_ms(g(off)), 1)
        out["sustain_pct"] = round(100.0 * g(24) / c["sustain"]["max_raw"], 1)
        for f, off in ENV_TENSION.items():
            out[f] = round(100.0 * g(off) / c["tension"]["max_raw"], 1)
        out["tempo_sync"] = bool(g(0) & 1)
    if blk.has(EV_LEVELS, 0, 24):
        cut = blk.get(EV_LEVELS, 0, "<i", 12)
        out["cutoff_pct"] = round(100.0 * cut / c["cutoff"]["max_raw"], 1)
        out["cutoff_hz"] = int(round(_hz(cut)))
        out["resonance_pct"] = round(100.0 * blk.get(EV_LEVELS, 0, "<i", 16) / c["resonance"]["max_raw"], 1)
        ft = blk.get(EV_LEVELS, 0, "<i", 20)
        out["filter_type"] = c["filter_types"][ft] if 0 <= ft < len(c["filter_types"]) else ft
        out["channel_pitch_cents"] = blk.get(EV_LEVELS, 0, "<i", 8)
    if blk.has(EV_PARAMS, 0, 104):
        cents = blk.get(EV_PARAMS, 0, "<i", 100)
        out["pitch_st"] = _st(cents); out["fine_cents"] = cents - 100 * _st(cents)
        dm = blk.p(EV_PARAMS)[83]
        out["declick"] = c["declick_modes"][dm] if dm < len(c["declick_modes"]) else dm
    if blk.has(EV_FX_FLAGS, 0, 2):
        out["reverse"] = bool(blk.get(EV_FX_FLAGS, 0, "<H", 0) & 2)
    if blk.has(EV_CUT, 0, 4):
        cut, by = struct.unpack("<HH", bytes(blk.p(EV_CUT)[:4]))
        out["cut_itself"] = bool(cut and cut == by)
        out["cut_group"] = [cut, by]
    return out


# ----------------------------------------------------------------------------- apply moves
def _canon(moves):
    out, bad = {}, []
    for k, v in (moves or {}).items():
        f = ALIASES.get(str(k).strip().lower(), str(k).strip().lower())
        if f in FIELDS:
            out[f] = v
        else:
            bad.append({"field": k, "why": "unknown field; known: " + ", ".join(FIELDS)})
    return out, bad


def _apply(d, ev, ch, moves, absolute=False):
    """Apply moves to channel ch -> (new bytes, result). absolute=True: plain numbers are values, not steps."""
    kind = _check_type(d, ev, ch)
    c = curves()
    moves, skipped = _canon(moves)
    blk = _Block(d, ev, ch)
    before = _read(blk)
    done, notes = [], []

    def skip(f, why):
        skipped.append({"field": f, "why": why})

    env_fields = [f for f in moves if f in ENV_TIME or f in ENV_TENSION or f == "sustain_pct"]
    if (env_fields or "envelope" in moves) and not blk.has(EV_ENV, VOL, 68):
        for f in env_fields + (["envelope"] if "envelope" in moves else []):
            skip(f, "this channel has no volume envelope data in the file (older FL project?)")
        env_fields = []
        moves.pop("envelope", None)
    if blk.has(EV_ENV, VOL, 68):
        on = bool(blk.get(EV_ENV, VOL, "<i", 4))
        want = None
        if "envelope" in moves:
            try:
                want = _bool(moves["envelope"])
            except ValueError as e:
                skip("envelope", str(e))
        elif env_fields and not on:
            want = True
            # neutral stages (what 'off' sounded like), so only the asked-for moves change the sound; steps start there
            for f, off, val in (("predelay_ms", 8, c["env_time"]["min_raw"]), ("attack_ms", 12, c["env_time"]["min_raw"]),
                                ("hold_ms", 16, c["env_time"]["min_raw"]), ("sustain_pct", 24, c["sustain"]["max_raw"])):
                blk.put(EV_ENV, VOL, "<i", off, int(val))
            before.update({k: v for k, v in _read(blk).items() if k in ("predelay_ms", "attack_ms", "hold_ms",
                                                                         "sustain_pct")})
            notes.append("the volume envelope was off (the sample played out in full); it is on now, so the sound "
                         "stops at the note's end + release: short notes cut long samples - lengthen the notes or the "
                         "release if the tail goes missing")
        if want is not None and want != on:
            blk.put(EV_ENV, VOL, "<i", 4, 1 if want else 0)
            done.append({"field": "envelope", "before": on, "after": want})
        for f in env_fields:
            v = moves[f]
            try:
                kind_, x = _parse(v, absolute)
                if f in ENV_TIME:
                    off = ENV_TIME[f]; ms0 = _ms(blk.get(EV_ENV, VOL, "<i", off))
                    ms = {"delta": ms0 + x, "ratio": max(ms0, 2.0) * x, "abs": x, "abs_ms": x}.get(kind_)
                    if ms is None:
                        raise ValueError("a time: +20 (ms), 'x2', '=150', '150 ms', '1.2 s'")
                    raw = _raw_ms(max(0.0, ms)); blk.put(EV_ENV, VOL, "<i", off, raw)
                    after = round(_ms(raw), 1)
                elif f == "sustain_pct":
                    mx = c["sustain"]["max_raw"]; p0 = 100.0 * blk.get(EV_ENV, VOL, "<i", 24) / mx
                    p = {"delta": p0 + x, "ratio": p0 * x, "abs": x, "abs_pct": x}.get(kind_)
                    if p is None:
                        raise ValueError("a percentage: -20, 'x0.5', '=60', '60%'")
                    raw = int(round(min(100.0, max(0.0, p)) * mx / 100.0)); blk.put(EV_ENV, VOL, "<i", 24, raw)
                    after = round(100.0 * raw / mx, 1)
                else:
                    off = ENV_TENSION[f]; mx = c["tension"]["max_raw"]; p0 = 100.0 * blk.get(EV_ENV, VOL, "<i", off) / mx
                    p = {"delta": p0 + x, "ratio": p0 * x, "abs": x, "abs_pct": x}.get(kind_)
                    if p is None:
                        raise ValueError("a percentage -100..100")
                    raw = int(round(min(100.0, max(-100.0, p)) * mx / 100.0)); blk.put(EV_ENV, VOL, "<i", off, raw)
                    after = round(100.0 * raw / mx, 1)
                done.append({"field": f, "before": before.get(f), "after": after})
            except (ValueError, ZeroDivisionError, OverflowError) as e:
                skip(f, str(e))

    for f in ("cutoff", "resonance_pct", "filter_type"):
        if f not in moves:
            continue
        if not blk.has(EV_LEVELS, 0, 24):
            skip(f, "this channel has no filter data in the file (older FL project?)")
            continue
        try:
            if f == "cutoff":
                mx = c["cutoff"]["max_raw"]; r0 = blk.get(EV_LEVELS, 0, "<i", 12)
                kind_, x = _parse(moves[f], absolute and not isinstance(moves[f], (int, float)))
                if absolute and isinstance(moves[f], (int, float)):
                    kind_ = "abs_hz"                            # settings: a plain number is Hz
                if kind_ == "delta":
                    raw = _raw_hz(_hz(r0) * 2 ** x)             # octaves
                elif kind_ == "ratio":
                    raw = _raw_hz(_hz(r0) * x)
                elif kind_ in ("abs_hz", "abs"):
                    raw = _raw_hz(x)
                elif kind_ == "abs_pct":
                    raw = int(round(min(100.0, max(0.0, x)) * mx / 100.0))
                else:
                    raise ValueError("cutoff: octaves (-1.0), 'x0.5', '2000 Hz', '2k', '60%'")
                raw = max(0, min(mx, raw)); blk.put(EV_LEVELS, 0, "<i", 12, raw)
                done.append({"field": "cutoff", "before": f"{before['cutoff_hz']} Hz ({before['cutoff_pct']}%)",
                             "after": f"{int(round(_hz(raw)))} Hz ({round(100.0 * raw / mx, 1)}%)"})
            elif f == "resonance_pct":
                mx = c["resonance"]["max_raw"]; p0 = 100.0 * blk.get(EV_LEVELS, 0, "<i", 16) / mx
                kind_, x = _parse(moves[f], absolute)
                p = {"delta": p0 + x, "ratio": p0 * x, "abs": x, "abs_pct": x}.get(kind_)
                if p is None:
                    raise ValueError("a percentage: +10, '=30', '30%'")
                raw = int(round(min(100.0, max(0.0, p)) * mx / 100.0)); blk.put(EV_LEVELS, 0, "<i", 16, raw)
                done.append({"field": f, "before": before["resonance_pct"], "after": round(100.0 * raw / mx, 1)})
            else:
                t = _pick(moves[f], c["filter_types"]); blk.put(EV_LEVELS, 0, "<i", 20, t)
                done.append({"field": f, "before": before["filter_type"], "after": c["filter_types"][t]})
        except (ValueError, ZeroDivisionError, OverflowError) as e:
            skip(f, str(e))

    if "pitch_st" in moves or "fine_cents" in moves:
        if not blk.has(EV_PARAMS, 0, 104):
            for f in ("pitch_st", "fine_cents"):
                if f in moves:
                    skip(f, "this channel has no sample pitch data in the file")
        else:
            cents0 = cents = blk.get(EV_PARAMS, 0, "<i", 100); ok = []
            for f, unit in (("pitch_st", 100), ("fine_cents", 1)):
                if f not in moves:
                    continue
                prev = cents
                try:
                    kind_, x = _parse(moves[f], absolute)
                    if kind_ == "delta":
                        cents += x * unit
                    elif kind_ == "abs":
                        cents = (x * unit + (cents - 100 * _st(cents)) if f == "pitch_st"
                                 else 100 * _st(cents) + x)
                    else:
                        raise ValueError("semitones / cents as a number: +12, -5, '=0'")
                    if not abs(cents) < 1e9:                       # '1e308': refused, not an OverflowError below
                        cents = prev
                        raise ValueError("semitones / cents as a number: +12, -5, '=0'")
                    ok.append(f)
                except ValueError as e:
                    skip(f, str(e))
            lim = c["pitch_cents_max"]; want = int(round(cents)); cents = max(-lim, min(lim, want))
            if want != cents:
                notes.append(f"pitch is limited to +-{lim // 100} semitones on the sample (asked {want / 100:+g})")
            if ok:
                blk.put(EV_PARAMS, 0, "<i", 100, cents)
                done.append({"field": "+".join(ok), "before": f"{cents0 / 100:+g} st", "after": f"{cents / 100:+g} st"})

    if "reverse" in moves:
        if not blk.has(EV_FX_FLAGS, 0, 2):
            skip("reverse", "this channel has no sample-effect flags in the file")
        else:
            try:
                want = _bool(moves["reverse"]); fl = blk.get(EV_FX_FLAGS, 0, "<H", 0)
                blk.put(EV_FX_FLAGS, 0, "<H", 0, (fl | 2) if want else (fl & ~2))
                done.append({"field": "reverse", "before": bool(fl & 2), "after": want})
            except ValueError as e:
                skip("reverse", str(e))

    if "declick" in moves:
        if not blk.has(EV_PARAMS, 0, 84):
            skip("declick", "this channel has no sample parameters in the file")
        else:
            try:
                m = _pick(moves["declick"], c["declick_modes"])
                # PC-VERIFY: 215 +83 is FL's Declicking mode list (Channel settings > SMP > Precomputed / Playback)
                blk.p(EV_PARAMS)[83] = m
                done.append({"field": "declick", "before": before.get("declick"), "after": c["declick_modes"][m]})
            except ValueError as e:
                skip("declick", str(e))

    if "cut_itself" in moves:
        if not blk.has(EV_CUT, 0, 4):
            skip("cut_itself", "this channel has no cut-group data in the file")
        else:
            try:
                want = _bool(moves["cut_itself"])
                cut, by = struct.unpack("<HH", bytes(blk.p(EV_CUT)[:4]))
                is_self = bool(cut and cut == by)
                if want and not is_self:
                    used = set()                                # a group no other channel uses (no cross-choking)
                    for k, e in enumerate(ev):
                        if e["id"] == EV_CUT and e["len"] >= 4 and k not in blk.idx.get(EV_CUT, []):
                            used.update(struct.unpack("<HH", bytes(H["_payload"](d, e)[:4])))
                    g = next(i for i in range(1, 65536) if i not in used)
                    # PC-VERIFY: Channel settings > Misc shows Cut = Cut by = g and a new note cuts the ringing one
                    struct.pack_into("<HH", blk.p(EV_CUT), 0, g, g)
                    done.append({"field": "cut_itself", "before": False, "after": True, "group": g})
                elif not want and is_self:
                    struct.pack_into("<HH", blk.p(EV_CUT), 0, 0, 0)
                    done.append({"field": "cut_itself", "before": True, "after": False})
            except ValueError as e:
                skip("cut_itself", str(e))

    ch_ = blk.changed()
    undo = {"channel": ch, "events": [{"id": eid, "n": n, "was": base64.b64encode(blk.orig[(eid, n)]).decode(),
                                       "now": base64.b64encode(b).decode()} for (eid, n), b in sorted(ch_.items())]}
    res = {"channel": ch, "kind": kind, "done": done, "skipped": skipped, "undo": undo}
    if any(x["field"] in ESTIMATED or x["field"] == "cutoff" for x in done):
        res["estimate"] = "ms / Hz are estimates from data/sampler/curves.json (FL stores knob positions): listen"
    if notes:
        res["notes"] = notes
    return blk.write(), res


# ----------------------------------------------------------------------------- public API
def sampler_shape(project, channel, moves, dry_run=False):
    """Shape a Sampler / audio clip channel in the project file (the open FL project: saved, edited, reloaded once).
    moves: {"attack_ms": +20 | "x2" | "=5" | "5 ms", "hold_ms", "decay_ms", "release_ms", "predelay_ms",
    "sustain_pct": -20 | "40%", "attack/decay/release_tension": -100..100, "envelope": True/False,
    "cutoff": -1.0 (octaves) | "2000 Hz" | "60%", "resonance_pct", "filter_type": "lp"|"hp"|"bp"|...,
    "pitch_st": +12, "fine_cents": -10, "reverse": True, "declick": "transient"|..., "cut_itself": True}
    -> {done: [{field, before, after}], skipped: [{field, why}], undo: {...}, notes?}.
    Envelope moves turn the volume envelope on when it was off (with neutral delay/attack/hold/sustain)."""
    H["writable"](project)
    known, bad = _canon(moves)
    if not known:
        return {"channel": channel, "done": [], "skipped": bad, "undo": {"channel": channel, "events": []}}

    def run():
        d = H["_load"](project); ev = H["_events"](d)
        new, res = _apply(d, ev, _resolve(d, ev, channel), moves)
        if res["undo"]["events"] and not dry_run:
            H["_save"](new, project)
        if dry_run:
            res["dry_run"] = True
        return res
    if dry_run:
        return run()
    return H["_with_open_project"](project, project, run)


def sampler_undo(project, channel, undo):
    """Put back what one sampler_shape changed (exact bytes). An event changed again since (in FL or by another
    tool) is left alone and reported."""
    H["writable"](project)
    evs = (undo or {}).get("events") or []
    if not evs:
        return {"channel": channel, "restored": [], "note": "nothing to undo"}

    def run():
        d = H["_load"](project); ev = H["_events"](d)
        ch = _resolve(d, ev, (undo or {}).get("channel", channel))
        blk = _Block(d, ev, ch); restored, left = [], []
        for u in evs:
            eid, n = int(u["id"]), int(u["n"])
            was, now = base64.b64decode(u["was"]), base64.b64decode(u["now"])
            if not blk.has(eid, n) or len(blk.p(eid, n)) != len(was):
                left.append({"event": eid, "why": "not in the file any more"}); continue
            cur = bytes(blk.p(eid, n))
            if cur == was:
                continue
            if cur != now:
                left.append({"event": eid, "why": "changed since the shape (in FL or by another tool): kept"}); continue
            blk.cur[(eid, n)] = bytearray(was); restored.append(eid)
        if blk.changed():
            H["_save"](blk.write(), project)
        r = {"channel": ch, "restored": restored}
        if left:
            r["kept"] = left
        return r
    return H["_with_open_project"](project, project, run)


def sampler_read(project, channel):
    d = H["_load"](project); ev = H["_events"](d); ch = _resolve(d, ev, channel)
    kind = _check_type(d, ev, ch)
    return {"channel": ch, "kind": kind, **_read(_Block(d, ev, ch)),
            "estimated": [f for f in ESTIMATED]}


def flp_sampler_settings(project, channel, output=None, set=None):
    """Read a Sampler / audio clip channel's envelope, filter, pitch, reverse, declick, cut-itself; with set= write
    absolute values (same field names as sampler_shape; plain numbers are values: attack_ms=5, cutoff=2000 Hz)."""
    if not set:
        return sampler_read(project, channel)
    output = output or project
    H["writable"](output)

    def run():
        d = H["_load"](project); ev = H["_events"](d)
        new, res = _apply(d, ev, _resolve(d, ev, channel), set, absolute=True)
        if res["undo"]["events"] or os.path.abspath(output) != os.path.abspath(project):
            H["_save"](new, output)
        res["output"] = output
        res["now"] = _read(_Block(new, H["_events"](new), res["channel"]))
        return res
    return H["_with_open_project"](project, output, run)


def sampler_live(channel, pitch_cents=None, cutoff=None, resonance=None):
    """LIVE (no reload) through the bridge: the channel's Pitch knob in cents and the filter cutoff / resonance as
    0..1 knob positions. Reads them back. The envelope, reverse, declick and cut groups have no API setter: use
    sampler_shape. PC-VERIFY: see the bridge command sampler_live."""
    return H["BRIDGE"].call("sampler_live", index=int(channel),
                            pitch_cents=None if pitch_cents is None else float(pitch_cents),
                            cutoff=None if cutoff is None else float(cutoff),
                            resonance=None if resonance is None else float(resonance))


def register(ns):
    global H, _CURVES
    H = ns
    _CURVES = None
    ns["sampler_shape"] = sampler_shape
    ns["sampler_undo"] = sampler_undo
    ns["sampler_read"] = sampler_read
    ns["sampler_live"] = sampler_live
    S, STR, P = ns["_schema"], ns["_STR"], ns["_p"]
    ns["TOOLS"]["flp_sampler_settings"] = (
        ns["_guard_open_project"](flp_sampler_settings, "project", "output"),
        "Sampler / audio clip channel settings in the file: volume envelope (attack/hold/decay/sustain/release ms, "
        "on/off), filter cutoff/resonance/type, pitch, reverse, declick, cut itself. Reads them; set={...} writes "
        "values (ms/Hz estimated).",
        S(project=ns["_FLP"], output=(ns["_OUT"][0], True), channel=STR("channel number or name"),
          set=P("object", "values to write, e.g. {'attack_ms': 5, 'cutoff': '2000 Hz'}", True)))
    ns.setdefault("_OPEN_PROJECT_GUARD", {})["flp_sampler_settings"] = ("project", "output")
    ns.setdefault("TOOL_GROUPS", {})["flp_sampler_settings"] = "sound"
