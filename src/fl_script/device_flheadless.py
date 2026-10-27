# name=fl-headless
# url=https://github.com/sertifex/fl-headless
"""
fl-headless bridge (runs inside FL Studio as a MIDI controller script).

Receives commands from the fl-headless MCP server as SysEx and runs them with FL Studio's scripting API.
Only the commands in COMMANDS below can run: no code is ever evaluated from a message.

Request frame: F0 7D 46 48 00 <id lo> <id hi> <part> <parts> <payload> F7
Payload (ASCII):  command;arg=value;arg=value     values are typed: i:<int> f:<float> b:<0|1> s:<base64 utf-8>
Reply: "OK" + repr(value) or "ER" + message, base64-encoded, sent back the same way with direction byte 1
(via a linked dispatch receiver such as the Flapi Response script if present, else this script's own output port).
"""
import base64
import json
import math
import time

import channels
import device
import general
import midi
import mixer
import transport

try:                                                           # optional modules: one missing on an older FL must
    import arrangement                                         # not take the whole bridge down
except ImportError:
    arrangement = None
try:
    import patterns
except ImportError:
    patterns = None
try:
    import playlist
except ImportError:
    playlist = None
try:
    import plugins
except ImportError:
    plugins = None
try:
    import ui
except ImportError:
    ui = None

SCRIPT_VERSION = 7                                             # bump on every change; the server compares it (fl_ping)
TEMPO_MAX = 522                                                # FL's highest tempo

# Folder of the file mailbox (filled in when the server installs this script). Requests/replies would go through
# two small JSON files instead of MIDI - but FL 2026's sandboxed Python refuses file I/O (SystemError from FileIO),
# so the script turns the mailbox off on the first failure and MIDI carries everything. Kept for FL versions that
# allow it.
MAILBOX = None

HDR = bytes([0xF0, 0x7D, 0x46, 0x48])
FLAPI = bytes([0xF0, 0x7D, 0x46, 0x6C, 0x61, 0x70, 0x69])
CHUNK = 384
DEBUG = False
_parts = {}


def _log(*a):
    if DEBUG:
        print("[fl-headless]", *a)


# ------------------------------------------------------------------ global channel indexes
# FL's channels.* (and plugins.* for channel instruments) count channels inside the channel rack's selected filter
# group unless asked for global indexes (API v33+). The server, the .flp file and the piano roll use GLOBAL indexes,
# so every call goes through these proxies, which add the flag positionally (some FL functions take no keyword
# arguments), filling the defaults in front of it. Older FL (no flag): the call is retried without it.
# PC-VERIFY: with a channel-rack filter group selected, fl_channel_set(index=N) changes global channel N.
_CH_FLAG = {  # name: [defaults of the arguments before the flag] (None = required)
    "channelCount": [], "selectedChannel": [False, 0],
    "getChannelName": [None], "setChannelName": [None, None], "getChannelColor": [None], "setChannelColor": [None, None],
    "isChannelMuted": [None], "muteChannel": [None, -1], "isChannelSolo": [None], "soloChannel": [None],
    "getChannelVolume": [None, False], "setChannelVolume": [None, None, 0], "getChannelPan": [None],
    "setChannelPan": [None, None, 0], "getChannelType": [None], "isChannelSelected": [None],
    "selectOneChannel": [None], "selectChannel": [None, -1], "getTargetFxTrack": [None],
    "setTargetFxTrack": [None, None], "getGridBit": [None, None], "setGridBit": [None, None, None],
    "getChannelMidiInPort": [None], "focusEditor": [None], "getActivityLevel": [None],
    "setChannelPitch": [None, None, 0], "getChannelPitch": [None, 0], "getRecEventId": [None]}
# plugins: (position of slotIndex, defaults before the flag) - the flag only matters for channels (slot -1)
_PL_FLAG = {"isValid": (1, [None, -1]), "getPluginName": (1, [None, -1, False]), "getParamCount": (1, [None, -1]),
            "getParamName": (2, [None, None, -1]), "getParamValue": (2, [None, None, -1]),
            "setParamValue": (3, [None, None, None, -1, 0]), "getParamValueString": (2, [None, None, -1, 0]),
            "getName": (1, [None, -1, 0, 0]), "getPresetCount": (1, [None, -1]), "nextPreset": (1, [None, -1]),
            "prevPreset": (1, [None, -1]), "getColor": (1, [None, -1, 0])}


def _with_flag(fn, defaults, args):
    a = list(args)
    if len(a) <= len(defaults) and all(d is not None for d in defaults[len(a):]):
        try:
            return fn(*(a + defaults[len(a):] + [True]))
        except TypeError:                                          # older FL: no global-index flag
            pass
    return fn(*a)


class _Global(object):
    def __init__(self, mod, table, plugin=False):
        self._mod, self._table, self._plugin = mod, table, plugin
        self.__name__ = getattr(mod, "__name__", "module") if mod is not None else "missing module"

    def __getattr__(self, name):
        if self._mod is None:
            raise AttributeError(name)
        fn = getattr(self._mod, name)
        spec = self._table.get(name)
        if spec is None:
            return fn
        if not self._plugin:
            return lambda *a: _with_flag(fn, spec, a)
        slot_pos, defaults = spec

        def call(*a):
            slot = a[slot_pos] if len(a) > slot_pos else -1
            if slot != -1:                                         # a mixer effect: indexes are mixer tracks
                return fn(*a)
            return _with_flag(fn, defaults, a)
        return call


CH = _Global(channels, _CH_FLAG)
PL = _Global(plugins, _PL_FLAG, plugin=True)


def _nch():
    """Number of channels, all groups (global)."""
    return CH.channelCount()


# ------------------------------------------------------------------ commands
def _r2(v):
    return None if v is None else round(v, 2)


def _numf(v):
    """float(v), or None when FL's read-back isn't a number (None, text, nan). -inf (a fader at zero) stays."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


# ------------------------------------------------------------------ FL's volume curves (exact; v7)
# Mixer fader: amplitude = (20^v - 1) / (20^0.8 - 1): 0.8 = 0 dB, 1.0 = +5.59 dB, 0 = -inf. Fits every point FL's own
# dB read-back gave on older FL (-60 .. +5.6 dB) within 0.01 dB. Channel volume knob (FL's utils.VolTodB): amplitude
# = (11^v - 1) / 10: 1.0 = 0 dB, the default 0.78125 = -5.18 dB (PC 2026-10-23: -6 asked landed on 0.748 = -6.00).
# Faders are set by these curves in ONE set: FL 2026 applies mixer.setTrackVolume after the script's callback, so a
# read-back inside the callback shows the OLD value and the old binary search over FL's read-back ran to an end of
# the fader (PC 2026-10-23: a silent master, tracks at +5.59 dB). The server reads back in a later call.
_MIX_B = 20.0
_MIX_UNITY = _MIX_B ** 0.8 - 1.0
_CH_B = 11.0
MIX_TOP_DB = 20 * math.log10((_MIX_B - 1.0) / _MIX_UNITY)        # +5.59


def fader_db(v):
    v = _numf(v)
    if v is None:
        return None
    if v <= 0:
        return float("-inf")
    return 20 * math.log10((_MIX_B ** min(1.0, v) - 1.0) / _MIX_UNITY)


def fader_pos(db):
    if db is None or db <= -150:
        return 0.0
    return max(0.0, min(1.0, math.log(1.0 + _MIX_UNITY * 10 ** (min(float(db), MIX_TOP_DB) / 20.0)) / math.log(_MIX_B)))


def chan_db(v):
    v = _numf(v)
    if v is None:
        return None
    if v <= 0:
        return float("-inf")
    return 20 * math.log10((_CH_B ** min(1.0, v) - 1.0) / (_CH_B - 1.0))


def chan_pos(db):
    if db is None or db <= -150:
        return 0.0
    return max(0.0, min(1.0, math.log(1.0 + (_CH_B - 1.0) * 10 ** (min(float(db), 0.0) / 20.0)) / math.log(_CH_B)))


def _r6(v):
    return None if v is None else round(v, 6)


def _db2(v):
    return None if v is None else (v if v == float("-inf") else round(v, 2))


def _vol_set(setter, getter, pos):
    """One set at a computed 0..1 position (never a search, never an end of the fader the caller didn't ask for).
    -> {"set_to", "before", "read_now"} (read_now may still show the old value on FL 2026: the server re-reads)."""
    before = _numf(_try(getter))
    pos = max(0.0, min(1.0, float(pos)))
    setter(pos)
    return {"set_to": _r6(pos), "before": _r6(before), "read_now": _r6(_numf(_try(getter)))}


# ------------------------------------------------------------------ mixer track layout
# FL's last mixer track is its 'Current' track (it follows the selected channel), never a real insert: FL <= 2024
# had 0 = master, 1..125 inserts, 126 = Current; FL 2026 grows the mixer as needed (master + N inserts + Current, so a
# new project's track 17 is Current - PC 2026-10-23: routing a channel to 17 left it on 16, a name on 17 read '').
def _last_insert():
    n = mixer.trackCount()
    tn = getattr(midi, "TN_LastIns", None)
    li = _try(_g(mixer, "getTrackInfo"), tn) if tn is not None else None
    if isinstance(li, int) and 0 < li < n - 1:
        return li                                                  # PC-VERIFY: getTrackInfo(TN_LastIns) on FL 2026
    return n - 2


def _free_inserts(limit=4):
    used = set()
    for c in range(_nch()):
        t = _try(CH.getTargetFxTrack, c)
        if t is not None:
            used.add(t)
    out = []
    for t in range(1, _last_insert() + 1):
        if t not in used and not any(_try(PL.isValid, t, s) for s in range(10)):
            out.append(t)
            if len(out) >= limit:
                break
    return out


def _not_insert(track):
    li = _last_insert()
    free = _free_inserts()
    return ("mixer track %d isn't an insert: this project's mixer has inserts 1-%d and track %d is FL's 'Current' "
            "track. Add inserts in FL's mixer first (FL 2026 adds tracks as you need them)%s" %
            (track, li, mixer.trackCount() - 1, (", or use a free one: %s" % ", ".join(map(str, free))) if free else ""))


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def _hex(c):
    return None if c is None else "#%06X" % (c & 0xFFFFFF)


def _color(v):
    """'#RRGGBB' or int -> FL color int (0xRRGGBB)."""
    if isinstance(v, str):
        v = int(v.lstrip("#"), 16)
    return int(v) & 0xFFFFFF


def _need(mod, name):
    fn = getattr(mod, name, None) if mod is not None else None
    if fn is None:
        raise ValueError("%s.%s is not available in this FL version" % (getattr(mod, "__name__", "module"), name))
    return fn


def _undo(label):
    try:
        general.saveUndo("fl-headless: " + label, getattr(midi, "UF_None", 0))
    except Exception:
        pass


def cmd_ping():
    return {"bridge": "fl-headless", "port": device.getPortNumber(), "version": SCRIPT_VERSION}


def cmd_where():
    """Where FL loaded this script from: tells the server which user data folder FL really uses.
    PC-VERIFY: FL defines __file__ for MIDI scripts (None otherwise)."""
    f = globals().get("__file__")
    return {"file": str(f) if f else None, "api": _try(general.getVersion)}


def cmd_commands():
    """The commands this copy of the script knows (the server checks for newer ones before using them)."""
    return sorted(COMMANDS)


def cmd_piano_roll(channel, pattern=0, show=True):
    """Point FL's piano roll at one channel (global index) of one pattern, for a piano-roll script to edit.
    pattern 0 keeps the current pattern. The server checks the pattern exists first (jumpToPattern would create
    it). Returns what FL now shows. PC-VERIFY: the piano roll follows the selected channel + current pattern."""
    if pattern:
        _need(patterns, "jumpToPattern")(int(pattern))
    CH.selectOneChannel(int(channel))                              # global index (API v33+)
    if show:
        w = getattr(midi, "widPianoRoll", 3)
        ui.showWindow(w); _try(ui.setFocused, w)
    ch = _try(CH.selectedChannel)                                  # global (indexGlobal flag)
    if ch is None:
        ch = _try(CH.channelNumber)
    return {"pattern": _try(patterns.patternNumber), "channel": ch,
            "channel_name": _try(CH.getChannelName, int(channel)),
            "focused_caption": _try(_g(ui, "getFocusedFormCaption")),
            "piano_roll_focused": bool(_try(_g(ui, "getFocused"), getattr(midi, "widPianoRoll", 3)))}


STATE_MAX_CHANNELS = 256                                       # the full state is capped (counts say how many)
STATE_MAX_MIXER = 128


def cmd_state(light=False):
    """Project state. light=1: transport only (playing, position, tempo, FL's title, changed flag, channel / mixer
    counts) - a few bytes, used on every edit. Full: channels, mixer, patterns, playlist tracks (capped)."""
    s = {"playing": bool(transport.isPlaying()), "song_pos_s": round(transport.getSongPos(midi.SONGLENGTH_S), 2),
         "tempo": mixer.getCurrentTempo() / 1000}
    if light:
        s.update(light=True, title=_try(_g(ui, "getProgTitle")), changed=_try(_g(general, "getChangedFlag")),
                 channel_count=_try(_nch), mixer_count=_try(mixer.trackCount))
        return s
    s["channels"] = []; s["mixer"] = []
    used = {0}
    nch = _nch(); s["channel_count"] = nch; s["channels_capped"] = nch > STATE_MAX_CHANNELS
    for i in range(min(nch, STATE_MAX_CHANNELS)):
        tgt = CH.getTargetFxTrack(i)
        used.add(tgt)
        c = {"index": i, "name": CH.getChannelName(i), "volume": round(CH.getChannelVolume(i), 3),
             "pan": round(CH.getChannelPan(i), 3), "muted": bool(CH.isChannelMuted(i)), "insert": tgt,
             "color": _hex(_try(CH.getChannelColor, i))}
        try:
            if PL.isValid(i):
                c["plugin"] = PL.getPluginName(i)
        except Exception:
            pass
        s["channels"].append(c)
    s["mixer_capped"] = len(used) > STATE_MAX_MIXER
    for t in sorted(used)[:STATE_MAX_MIXER]:
        v = _numf(_try(mixer.getTrackVolume, t))
        tr = {"index": t, "name": mixer.getTrackName(t), "volume": _r6(v), "volume_db": _db2(fader_db(v)),
              "pan": round(mixer.getTrackPan(t), 3), "muted": bool(mixer.isTrackMuted(t)), "effects": [],
              "color": _hex(_try(mixer.getTrackColor, t))}
        for slot in range(10):
            try:
                if PL.isValid(t, slot):
                    tr["effects"].append({"slot": slot, "name": PL.getPluginName(t, slot)})
            except Exception:
                pass
        s["mixer"].append(tr)
    npat = _try(_g(patterns, "patternCount")) or 0
    s["pattern_count"] = npat                                      # patterns below are capped at 64 (reply size)
    s["patterns"] = [{"index": p, "name": _try(_g(patterns, "getPatternName"), p),
                      "color": _hex(_try(_g(patterns, "getPatternColor"), p))} for p in range(1, min(npat, 64) + 1)]
    s["patterns_capped"] = npat > 64
    s["playlist_tracks"] = [{"index": t, "name": _try(_g(playlist, "getTrackName"), t),
                             "color": _hex(_try(_g(playlist, "getTrackColor"), t))} for t in range(1, 33)]
    return s


def cmd_transport(action):
    acts = {"play": transport.start, "stop": transport.stop, "record": transport.record,
            "rewind": lambda: transport.setSongPos(0),
            "save": lambda: transport.globalTransport(midi.FPT_Save, 1),
            "undo": general.undoUp, "redo": general.undoDown}
    if action not in acts:
        raise ValueError("unknown action %s" % action)
    acts[action]()
    return {"done": action}


def cmd_set_tempo(bpm):
    if not 10 <= bpm <= TEMPO_MAX:
        raise ValueError("tempo out of range (10-%d BPM)" % TEMPO_MAX)
    general.processRECEvent(midi.REC_Tempo, int(bpm * 1000),
                            midi.REC_Control | midi.REC_UpdateControl | midi.REC_UpdateValue)
    return {"tempo": mixer.getCurrentTempo() / 1000}


def cmd_channel_set(index, volume_db=None, pan=None, muted=None, name=None, color=None, solo=None, select=None,
                    mixer_track=None, volume=None):
    """volume: the knob's 0..1 position; volume_db: on FL's channel scale (0 dB = the knob's top, the default 78 % =
    -5.18 dB), set by FL's curve in one step. mixer_track must be an insert (never FL's 'Current' track) and is
    checked after the set."""
    if not 0 <= index < _nch():
        raise ValueError("no channel %d" % index)
    if mixer_track is not None and not 0 <= int(mixer_track) <= _last_insert():
        raise ValueError(_not_insert(int(mixer_track)) + "; nothing was changed")
    fit = None
    if volume is not None or volume_db is not None:
        fit = _vol_set(lambda v: CH.setChannelVolume(index, v), lambda: CH.getChannelVolume(index),
                       volume if volume is not None else chan_pos(volume_db))
    if pan is not None:
        CH.setChannelPan(index, max(-1.0, min(1.0, pan)))
    if muted is not None and bool(CH.isChannelMuted(index)) != muted:
        CH.muteChannel(index)
    if name is not None:
        CH.setChannelName(index, name)
    if color is not None:
        _need(CH, "setChannelColor")(index, _color(color))
    if solo is not None and bool(_try(CH.isChannelSolo, index)) != solo:
        _need(CH, "soloChannel")(index)
    if select:
        _need(CH, "selectOneChannel")(index)
    if mixer_track is not None:
        if getattr(channels, "setTargetFxTrack", None):
            CH.setTargetFxTrack(index, int(mixer_track))
        else:
            _need(mixer, "linkChannelToTrack")(index, int(mixer_track))
    row = _channel_row(index)
    if fit is not None:
        row["volume_fit"] = fit
    if mixer_track is not None and row.get("insert") is not None and int(row["insert"]) != int(mixer_track):
        row["insert_not_set"] = ("FL kept the channel on insert %s, not %d" % (row["insert"], int(mixer_track)))
    return row


def _channel_row(i):
    tgt = _try(CH.getTargetFxTrack, i)
    v = _numf(CH.getChannelVolume(i))
    c = {"index": i, "name": CH.getChannelName(i), "volume": _r6(v), "volume_db": _db2(chan_db(v)),
         "pan": round(CH.getChannelPan(i), 3), "muted": bool(CH.isChannelMuted(i)), "insert": tgt,
         "color": _hex(_try(CH.getChannelColor, i))}
    try:
        if PL.isValid(i):
            c["plugin"] = PL.getPluginName(i)
    except Exception:
        pass
    return c


def cmd_steps(index, steps, pattern=0):
    """Step sequencer: steps is a string like 'x...x...x...x...' (x = on). pattern 0 = current pattern."""
    if pattern:
        _need(patterns, "jumpToPattern")(int(pattern))
    setb = _need(CH, "setGridBit")
    for pos, ch in enumerate(steps):
        setb(index, pos, 1 if ch in "xX1" else 0)
    return {"channel": index, "steps": steps, "pattern": pattern or _try(patterns.patternNumber)}


def _sends(track):
    """[{to, level}] of a track's active routes; level 0..1 (0.8 = 0 dB) when this FL has getRouteToLevel (API 36)."""
    lvl = getattr(mixer, "getRouteToLevel", None); out = []
    for d in range(mixer.trackCount()):
        if d != track and _try(mixer.getRouteSendActive, track, d):
            out.append({"to": d, "level": (_try(lvl, track, d) if lvl else None)})
    return out


def _pairs(spec):
    """'3:0.8,5:0.6' -> [(3, 0.8), (5, 0.6)] (the wire carries scalars only)."""
    out = []
    for part in str(spec or "").split(","):
        if ":" in part:
            a, b = part.split(":", 1)
            out.append((int(a), max(0.0, min(1.0, float(b)))))
    return out


def cmd_mixer_set(track, volume_db=None, pan=None, muted=None, name=None, effects_enabled=None, color=None,
                  solo=None, route_to=None, unroute=None, reset=None, send_levels=None, volume=None):
    """volume: the fader's 0..1 position (0.8 = 0 dB); volume_db: set by FL's exact fader curve in ONE step (no
    search over FL's read-back: on FL 2026 that read-back lags the set). The server re-reads in a later call
    (volume, volume_db from the 0..1 position; fl_db = FL's own dB read-back, for comparison)."""
    if not 0 <= track < mixer.trackCount():
        raise ValueError("no mixer track %d" % track)
    if isinstance(route_to, str):
        route_to = [int(x) for x in route_to.split(",") if x.strip()]
    if isinstance(unroute, str):
        unroute = [int(x) for x in unroute.split(",") if x.strip()]
    changes = any(x is not None for x in (volume_db, volume, pan, muted, name, effects_enabled, color, solo, reset,
                                          send_levels)) or bool(route_to or unroute)
    li = _last_insert()
    if changes and track > li:
        raise ValueError(_not_insert(track) + "; nothing was changed")
    bad = [d for d in (route_to or []) + [a for a, _b in _pairs(send_levels)] if not 0 <= int(d) <= li]
    if bad:
        raise ValueError(_not_insert(int(bad[0])) + "; nothing was changed")
    if reset:
        mixer.setTrackName(track, ""); _try(mixer.setTrackColor, track, -1)
        mixer.setTrackVolume(track, 0.8); mixer.setTrackPan(track, 0.0)
        if mixer.isTrackMuted(track):
            mixer.muteTrack(track)
    fit = None
    if volume is not None or volume_db is not None:
        fit = _vol_set(lambda v: mixer.setTrackVolume(track, v), lambda: mixer.getTrackVolume(track),
                       volume if volume is not None else fader_pos(volume_db))
    if pan is not None:
        mixer.setTrackPan(track, max(-1.0, min(1.0, pan)))
    if muted is not None and bool(mixer.isTrackMuted(track)) != muted:
        mixer.muteTrack(track)
    if name is not None:
        mixer.setTrackName(track, name)
    if effects_enabled is not None:
        mixer.enableTrackSlots(track, effects_enabled)
    if color is not None:
        _need(mixer, "setTrackColor")(track, _color(color))
    if solo is not None and bool(_try(mixer.isTrackSolo, track)) != solo:
        _need(mixer, "soloTrack")(track)
    routed = False
    for dst, val in ([(d, 1) for d in (route_to or [])] + [(d, 0) for d in (unroute or [])]):
        _need(mixer, "setRouteTo")(track, int(dst), val); routed = True
    levels = _pairs(send_levels)
    if levels:
        setl = _need(mixer, "setRouteToLevel")                     # API 36+ (FL 2024+); older FL: a clear error
        for dst, lv in levels:
            if not _try(mixer.getRouteSendActive, track, dst):
                _need(mixer, "setRouteTo")(track, dst, 1); routed = True   # a level needs the route first
        if routed:
            _try(mixer.afterRoutingChanged)
        for dst, lv in levels:
            setl(track, dst, lv)                                   # PC-VERIFY: level 0.8 = 0 dB, shows on the send knob
    if routed:
        _try(mixer.afterRoutingChanged)
    v = _numf(_try(mixer.getTrackVolume, track))
    out = {"track": track, "name": mixer.getTrackName(track), "volume": _r6(v), "volume_db": _db2(fader_db(v)),
           "fl_db": _db2(_numf(_try(mixer.getTrackVolume, track, 1))),
           "pan": round(mixer.getTrackPan(track), 3), "muted": bool(mixer.isTrackMuted(track)),
           "color": _hex(_try(mixer.getTrackColor, track)),
           "routes_to": [d for d in range(mixer.trackCount()) if d != track and _try(mixer.getRouteSendActive, track, d)],
           "sends": _sends(track)}
    if v is not None and v <= 0:
        out["volume_at_zero"] = True                               # the fader is all the way down (-inf)
    if track > li:
        out["current_track"] = True                                # FL's 'Current' track, not an insert
    if fit is not None:
        out["volume_fit"] = fit
    return out


def cmd_playlist_track_set(index, name=None, color=None):
    if name is not None:
        _need(playlist, "setTrackName")(index, name)
    if color is not None:
        _need(playlist, "setTrackColor")(index, _color(color))
    return {"index": index, "name": _try(playlist.getTrackName, index), "color": _hex(_try(playlist.getTrackColor, index))}


def cmd_pattern_set(index, name=None, color=None, select=None):
    if name is not None:
        _need(patterns, "setPatternName")(index, name)
    if color is not None:
        _need(patterns, "setPatternColor")(index, _color(color))
    if select:
        _need(patterns, "jumpToPattern")(index)
    return {"index": index, "name": _try(patterns.getPatternName, index), "color": _hex(_try(patterns.getPatternColor, index))}


WINDOWS = {"mixer": "widMixer", "channel_rack": "widChannelRack", "playlist": "widPlaylist",
           "piano_roll": "widPianoRoll", "browser": "widBrowser"}


def cmd_show(window):
    if window not in WINDOWS:
        raise ValueError("window must be one of " + ", ".join(WINDOWS))
    w = getattr(midi, WINDOWS[window], list(WINDOWS).index(window))
    ui.showWindow(w); _try(ui.setFocused, w)
    return {"shown": window}


_queue = []                                                    # [(due_time, channel, note, velocity)]
_held = set()


def _note(ch, note, vel):
    CH.midiNoteOn(ch, note, vel)
    if vel:
        _held.add((ch, note))
    else:
        _held.discard((ch, note))


def cmd_play(channel, notes, velocity=90):
    """Audition notes live through a channel's instrument (nothing is written to the project).
    notes: JSON [[midi_note, start_ms, length_ms, velocity?], ...]"""
    if not 0 <= channel < _nch():
        raise ValueError("no channel %d" % channel)
    items = json.loads(notes); now = time.time(); end = 0
    if len(items) > 512:
        raise ValueError("at most 512 notes per audition")
    for it in items:
        n, st, ln = int(it[0]), float(it[1]), float(it[2])
        v = int(it[3]) if len(it) > 3 else int(velocity)
        if not 0 <= n <= 127:
            continue
        _queue.append((now + st / 1000.0, channel, n, max(1, min(127, v))))
        _queue.append((now + (st + ln) / 1000.0, channel, n, 0))
        end = max(end, st + ln)
    _queue.sort(key=lambda q: q[0])
    OnIdle()
    return {"channel": channel, "notes": len(items), "seconds": round(end / 1000.0, 2)}


def cmd_stop_sound():
    del _queue[:]
    for ch, n in list(_held):
        _note(ch, n, 0)
    return {"stopped": True}


def OnIdle():
    now = time.time()
    while _queue and _queue[0][0] <= now:
        _, ch, n, v = _queue.pop(0)
        try:
            _note(ch, n, v)
        except Exception as e:
            _log("note", e)
    global MAILBOX
    _mb["idles"] += 1
    if MAILBOX:
        try:
            _mailbox(now)
        except SystemError as e:                                   # FL's sandboxed Python refuses file I/O (FL 2026):
            _mb["err"] = "%s: %s" % (type(e).__name__, e); MAILBOX = None   # stop trying, MIDI carries everything
        except Exception as e:                                     # never let the mailbox break FL's idle loop
            _mb["err"] = "%s: %s" % (type(e).__name__, e); _log("mailbox", e)


_mb = {"last": None, "beat": 0.0, "pending": None, "idles": 0, "err": None}


def _mb_file(name):
    return MAILBOX.rstrip("\\/") + "/" + name


def _mb_write(name, obj):
    with open(_mb_file(name), "w") as f:
        f.write(json.dumps(obj))


def cmd_diag():
    """Bridge self-check from inside FL: mailbox folder, idle calls seen, last mailbox error, a test write."""
    out = {"mailbox": MAILBOX, "idles": _mb["idles"], "last_error": _mb["err"], "write_test": None,
           "python": "%d.%d" % tuple(__import__("sys").version_info[:2])}
    if MAILBOX:
        try:
            _mb_write("diag.json", {"t": time.time()}); out["write_test"] = "ok"
        except Exception as e:
            out["write_test"] = "%s: %s" % (type(e).__name__, e)
    return out


def _mailbox(now):
    """Heartbeat every second (the server checks it to pick this transport), then answer a new request if any.
    Requests the server already gave up on (past 'until', e.g. left over from before FL restarted) are dropped;
    a reply that couldn't be written (file busy) is retried on the next idle tick."""
    if now - _mb["beat"] > 1.0:
        try:
            _mb_write("alive.json", {"t": now}); _mb["beat"] = now
        except OSError:
            pass
    if _mb["pending"] is not None:
        try:
            _mb_write("resp.json", _mb["pending"]); _mb["pending"] = None
        except OSError:
            return
    try:
        with open(_mb_file("req.json")) as f:
            text = f.read()
    except OSError:
        return
    if not text:
        return
    try:
        req = json.loads(text)
    except ValueError:                                             # caught mid-write: next idle tick reads it
        return
    if req.get("id") == _mb["last"]:
        return
    _mb["last"] = req.get("id")
    if now > float(req.get("until", 0) or 0):                      # stale: the server isn't waiting any more
        return
    out = {"id": req.get("id"), "reply": _run(str(req.get("payload", "")))}
    try:
        _mb_write("resp.json", out)
    except OSError:
        _mb["pending"] = out


BATCH_OPS = {"channel": cmd_channel_set, "mixer": cmd_mixer_set, "playlist_track": cmd_playlist_track_set,
             "pattern": cmd_pattern_set, "steps": cmd_steps}


def cmd_batch(ops, label="batch"):
    """Many changes in one round trip and one undo step. ops: JSON list of {"op": kind, ...arguments}."""
    items = json.loads(ops)
    _undo(label)
    out = []
    for it in items:
        kind = it.pop("op", None)
        fn = BATCH_OPS.get(kind)
        if fn is None:
            out.append({"error": "unknown op %s" % kind}); continue
        if any(not str(k).isidentifier() for k in it):
            out.append({"error": "bad argument name"}); continue
        try:
            fn(**it); out.append("ok")
        except Exception as e:
            out.append({"error": "%s: %s" % (type(e).__name__, e)})
    return {"done": sum(1 for o in out if o == "ok"), "results": out}


def cmd_plugin_params(index, slot=-1, filter=""):
    out = []
    n = PL.getParamCount(index, slot)
    for p in range(n):
        name = PL.getParamName(p, index, slot)
        if not name or (filter and filter.lower() not in name.lower()):
            continue
        out.append({"param": p, "name": name, "value": PL.getParamValue(p, index, slot),
                    "text": PL.getParamValueString(p, index, slot)})        # full precision: exact restores
        if len(out) >= 200:
            break
    return {"plugin": PL.getPluginName(index, slot), "count": n, "params": out}


def cmd_plugin_get(index, param, slot=-1):
    """One parameter as FL has it now (a separate call: the server reads back after a set, in a later FL callback -
    PC 2026-10-23: Fruity Convolver 'Wet level' showed the new text at once while its value was still the old one)."""
    return {"param": param, "name": PL.getParamName(param, index, slot), "value": PL.getParamValue(param, index, slot),
            "text": PL.getParamValueString(param, index, slot)}


def cmd_plugin_set(index, param, value, slot=-1):
    PL.setParamValue(max(0.0, min(1.0, value)), param, index, slot)
    return {"param": param, "value": PL.getParamValue(param, index, slot),
            "text": PL.getParamValueString(param, index, slot)}


def cmd_plugin_probe(index, param, points=17, slot=-1):
    """Sweep one parameter over `points` evenly spaced knob positions inside FL and return the display text at each,
    then put the knob back exactly (one round trip instead of one per point). PC-VERIFY: the plugin's sound is
    unchanged afterwards (value read back == before)."""
    n = max(2, min(129, int(points)))
    before = PL.getParamValue(param, index, slot)
    texts, values = [], []
    try:
        for k in range(n):
            v = k / float(n - 1)
            PL.setParamValue(v, param, index, slot)
            values.append(v); texts.append(PL.getParamValueString(param, index, slot))
    finally:
        PL.setParamValue(before, param, index, slot)
    return {"param": param, "name": PL.getParamName(param, index, slot), "values": values, "texts": texts,
            "restored": PL.getParamValue(param, index, slot), "before": before}


def _num_in(text):
    """First number in a display string like '-3.5 dB', '1,200 Hz', '2.4 kHz' (k = x1000)."""
    t = (text or "").replace(",", "").strip(); num = ""; seen = False
    for ch in t:
        if ch.isdigit() or ch == "." or (ch == "-" and not num):
            num += ch; seen = seen or ch.isdigit()
        elif seen:
            break
    if not seen:
        return None
    v = float(num)
    return v * 1000 if "khz" in t.lower() else v


def cmd_plugin_set_value(index, param, target, slot=-1):
    """Set a parameter so FL displays `target` (e.g. 3 for '+3.0 dB', 250 for '250 Hz'). The search starts at the
    knob's current position and only moves toward the target (secant steps of at most 0.15, then bisection inside
    the bracket): it never sweeps to 0.0 or 1.0, so a gain knob can't jump to its extremes while FL plays. On any
    failure the original value is put back; a result that needs the knob at its very top is refused (restored).
    PC-VERIFY: no audible jump when setting a compressor gain during playback."""
    target = float(target)
    get = lambda: _num_in(PL.getParamValueString(param, index, slot))
    before = PL.getParamValue(param, index, slot)
    done = False
    try:
        v0 = get()
        if v0 is None:
            raise ValueError("this parameter doesn't show a number")
        pts = [(before, v0)]; best = (abs(v0 - target), before, v0)
        lo = hi = None                                             # bracket: positions on each side of the target
        step = 0.02 if before <= 0.98 else -0.02
        p = min(1.0, max(0.0, before + step))
        for _ in range(48):
            if best[0] <= 1e-9:
                break
            PL.setParamValue(p, param, index, slot); v = get()
            if v is None:
                break
            pts.append((p, v))
            if abs(v - target) < best[0]:
                best = (abs(v - target), p, v)
            below = [(q, w) for q, w in pts if w < target]; above = [(q, w) for q, w in pts if w >= target]
            if below and above:                                    # bracketed: bisect between the closest pair
                lo = min(below, key=lambda t: abs(t[0] - p)); hi = min(above, key=lambda t: abs(t[0] - lo[0]))
                if abs(hi[0] - lo[0]) < 1e-6:
                    break
                p = (lo[0] + hi[0]) / 2.0
                continue
            (p1, w1), (p2, w2) = pts[-2], pts[-1]
            if w2 != w1 and p2 != p1:
                nxt = p2 + (target - w2) * (p2 - p1) / (w2 - w1)
            else:
                nxt = p2 + (0.05 if p2 >= p1 else -0.05)
            nxt = max(p2 - 0.15, min(p2 + 0.15, nxt))
            nxt = max(0.0, min(1.0, nxt))
            if abs(nxt - p2) < 1e-9:                                # at an end of the knob: target out of range
                break
            p = nxt
        if lo is None and best[0] > 0.02 * max(1.0, abs(target)):
            raise ValueError("FL's display never reached %g on this knob (closest it showed: %g); left unchanged" %
                             (target, best[2]))
        if best[1] >= 0.999 and before < 0.999:
            raise ValueError("reaching %g would need the knob at its maximum; left unchanged" % target)
        PL.setParamValue(best[1], param, index, slot)
        done = True
    finally:
        if not done:
            try:
                PL.setParamValue(before, param, index, slot)       # never leave a knob somewhere it wasn't asked
            except Exception:
                pass
    return {"param": param, "name": PL.getParamName(param, index, slot),
            "text": PL.getParamValueString(param, index, slot), "value": round(PL.getParamValue(param, index, slot), 5),
            "before": round(before, 5), "set_to": round(best[1], 5)}   # the server reads back later (plugin_get)


def cmd_song(action, bar=None, name=None, delta=1, bpb=4):
    """Song position, markers, metronome, record arm, pattern/song mode. bpb: beats per bar (3 for 3/4)."""
    ppq = general.getRecPPQ(); bpb = float(bpb or 4)
    abst = getattr(midi, "SONGLENGTH_ABSTICKS", 2)
    # FL applies transport changes after this callback returns, so report the state we asked for
    st = {"bar": round(1 + transport.getSongPos(abst) / (float(bpb) * ppq), 3),
          "metronome": bool(_try(ui.isMetronomeEnabled)), "recording": bool(_try(transport.isRecording)),
          "song_mode": _try(transport.getLoopMode) == 1}
    if hasattr(ui, "isPrecountEnabled"):                           # count-in ("countdown before recording")
        st["countdown"] = bool(_try(ui.isPrecountEnabled))
    if hasattr(ui, "isLoopRecEnabled"):
        st["loop_record"] = bool(_try(ui.isLoopRecEnabled))
    if action == "where":
        pass
    elif action == "goto":
        transport.setSongPos(int((float(bar) - 1) * bpb * ppq), abst)
        st["bar"] = round(float(bar), 3)
    elif action == "marker_add":
        pos = int((float(bar) - 1) * bpb * ppq) if bar is not None else transport.getSongPos(getattr(midi, "SONGLENGTH_ABSTICKS", 2))
        _need(arrangement, "addAutoTimeMarker")(pos, name or "Marker")
    elif action == "marker_jump":
        _need(arrangement, "jumpToMarker")(int(delta), False)
    elif action in ("metronome", "song_mode", "record", "countdown", "loop_record"):
        fpt = {"metronome": "FPT_Metronome", "song_mode": "FPT_Loop", "record": "FPT_Record",
               "countdown": "FPT_CountDown", "loop_record": "FPT_LoopRecord"}[action]
        if not hasattr(midi, fpt) or (action in ("countdown", "loop_record") and action not in st):
            raise ValueError("%s is not available in this FL version" % action)
        transport.globalTransport(getattr(midi, fpt), 1)            # toggles: report the state we asked for
        key = {"metronome": "metronome", "song_mode": "song_mode", "record": "recording", "countdown": "countdown",
               "loop_record": "loop_record"}[action]
        st[key] = not st[key]
    else:
        raise ValueError("action: where, goto, marker_add, marker_jump, metronome, song_mode, record, countdown, "
                         "loop_record")
    st["done"] = action
    return st


def _g(mod, name):
    """A module function if this FL version has it, else a no-op (older FL APIs lack some calls)."""
    return getattr(mod, name, lambda *a, **k: None)


def cmd_context(bpb=4):
    """What the user is looking at / has selected right now, so 'this' and 'these' can be resolved."""
    wins = {getattr(midi, "widMixer", 0): "mixer", getattr(midi, "widChannelRack", 1): "channel_rack",
            getattr(midi, "widPlaylist", 2): "playlist", getattr(midi, "widPianoRoll", 3): "piano_roll",
            getattr(midi, "widBrowser", 4): "browser"}
    focused = [n for w, n in wins.items() if _try(_g(ui, "getFocused"), w)]
    ppq = general.getRecPPQ(); bpb = float(bpb or 4)
    pos = _try(_g(transport, "getSongPos"), getattr(midi, "SONGLENGTH_ABSTICKS", 2)) or 0
    ch = _try(_g(CH, "selectedChannel"))
    sel_ch = [i for i in range(_nch()) if _try(_g(CH, "isChannelSelected"), i)]
    tr = _try(_g(mixer, "trackNumber"))
    sel_tr = [i for i in range(mixer.trackCount()) if _try(_g(mixer, "isTrackSelected"), i)]
    pat = _try(_g(patterns, "patternNumber"))
    out = {"focused": focused[0] if focused else None, "focused_caption": _try(_g(ui, "getFocusedFormCaption")),
           "playing": bool(_try(_g(transport, "isPlaying"))), "bar": round(1 + pos / (float(bpb) * ppq), 3),
           "channel": None, "selected_channels": sel_ch[:64], "mixer_track": None, "selected_mixer_tracks": sel_tr[:64],
           "pattern": None}
    if ch is not None and ch >= 0:
        out["channel"] = {"index": ch, "name": _try(_g(CH, "getChannelName"), ch),
                          "plugin": _try(_g(PL, "getPluginName"), ch) if _try(_g(PL, "isValid"), ch) else None,
                          "mixer_track": _try(_g(CH, "getTargetFxTrack"), ch)}
    if tr is not None and tr >= 0:
        out["mixer_track"] = {"index": tr, "name": _try(_g(mixer, "getTrackName"), tr)}
    if pat is not None and pat > 0:
        out["pattern"] = {"index": pat, "name": _try(_g(patterns, "getPatternName"), pat)}
    s0, s1 = _try(getattr(arrangement, "selectionStart", lambda: -1)), _try(getattr(arrangement, "selectionEnd", lambda: -1))
    if s0 is not None and s1 is not None and s0 >= 0 and s1 > s0:
        out["playlist_selection_bars"] = [round(1 + s0 / (float(bpb) * ppq), 3), round(1 + s1 / (float(bpb) * ppq), 3)]
    return out


def _preset_name(index, slot, k=-1):
    """Name of preset k; k=-1 asks for the current one (supported by some plugins only)."""
    try:
        return PL.getName(index, slot, getattr(midi, "FPN_Preset", 6), k)
    except Exception:
        return None


def _preset_count(index, slot):
    try:
        return PL.getPresetCount(index, slot)
    except Exception:
        return None


def cmd_plugin_preset(index, direction="next", slot=-1):
    (PL.nextPreset if direction == "next" else PL.prevPreset)(index, slot)
    return {"plugin": PL.getPluginName(index, slot), "moved": direction,
            "preset": _preset_name(index, slot), "count": _preset_count(index, slot)}


def cmd_plugin_presets(index, slot=-1, start=0, limit=300):
    n = _preset_count(index, slot) or 0
    names = [_preset_name(index, slot, k) for k in range(int(start), min(n, int(start) + int(limit)))]
    return {"plugin": PL.getPluginName(index, slot), "count": n, "start": int(start), "names": names,
            "current": _preset_name(index, slot, -1)}


def cmd_plugin_info(index, slot=-1):
    n = _preset_count(index, slot) or 0
    return {"plugin": PL.getPluginName(index, slot), "preset": _preset_name(index, slot),
            "count": n, "params": PL.getParamCount(index, slot),
            "first_presets": [_preset_name(index, slot, k) for k in range(min(n, 6))]}


def _tracks_arg(tracks):
    n = mixer.trackCount()
    if tracks in ("", "all", None):
        return range(n)
    return [t for t in (int(x) for x in str(tracks).split(",") if x.strip()) if 0 <= t < n]


def cmd_mixer_info(tracks="all"):
    """Per mixer track: name, armed, recording file, used by a channel, effects, sends (one pass over channels)."""
    targets = set()
    for c in range(_nch()):
        t = _try(CH.getTargetFxTrack, c)
        if t is not None:
            targets.add(t)
    armed = getattr(mixer, "isTrackArmed", None); recf = getattr(mixer, "getTrackRecordingFileName", None)
    out = []
    for t in _tracks_arg(tracks):
        slots = [sl for sl in range(10) if _try(PL.isValid, t, sl)]
        out.append({"index": t, "name": mixer.getTrackName(t), "armed": bool(_try(armed, t)) if armed else None,
                    "recording_file": (_try(recf, t) or "") if recf else "", "used": t in targets,
                    "effects": [PL.getPluginName(t, sl) for sl in slots], "slots": slots,   # v6: slot of each effect
                    "muted": bool(_try(mixer.isTrackMuted, t)),
                    "sends": _sends(t) if tracks not in ("", "all", None) else None})
        if len(out) >= 128:
            break
    return {"tracks": out}


def cmd_mixer_arm(track, armed=True):
    """Arm / disarm a mixer track for audio recording (armTrack toggles: only flipped when needed)."""
    if not 0 <= track < mixer.trackCount():
        raise ValueError("no mixer track %d" % track)
    is_armed = _need(mixer, "isTrackArmed")
    if bool(is_armed(track)) != bool(armed):
        _need(mixer, "armTrack")(track)
    recf = getattr(mixer, "getTrackRecordingFileName", None)
    return {"track": track, "armed": bool(is_armed(track)), "recording_file": (_try(recf, track) or "") if recf else ""}


def cmd_mixer_peaks(tracks="0"):
    """Current peak meter per track: [left, right] (1.0 = 0 dBFS, > 1 = clipping). PC-VERIFY: follows the input while
    FL is stopped (input monitoring on an armed insert)."""
    pk = _need(mixer, "getTrackPeaks")
    return {"peaks": dict((t, [round(pk(t, 0), 4), round(pk(t, 1), 4)]) for t in _tracks_arg(tracks))}


def cmd_rec_setup(punch_in_bar=None, punch_out_bar=None, loop_record=None, bpb=4):
    """Punch range: moves the song position to each bar and presses FL's punch in / out (PC-VERIFY: FPT_PunchIn/Out
    set the punch points at the song position in song mode). loop_record: the wanted loop-record state."""
    ppq = general.getRecPPQ(); abst = getattr(midi, "SONGLENGTH_ABSTICKS", 2); bpb = float(bpb or 4)
    punch = None
    if punch_in_bar is not None and punch_out_bar is not None:
        if not (hasattr(midi, "FPT_PunchIn") and hasattr(midi, "FPT_PunchOut")):
            raise ValueError("punch in/out is not available in this FL version")
        if float(punch_out_bar) <= float(punch_in_bar):
            raise ValueError("punch_out_bar must be after punch_in_bar")
        back = transport.getSongPos(abst)
        transport.setSongPos(int((float(punch_in_bar) - 1) * bpb * ppq), abst)
        transport.globalTransport(midi.FPT_PunchIn, 1)
        transport.setSongPos(int((float(punch_out_bar) - 1) * bpb * ppq), abst)
        transport.globalTransport(midi.FPT_PunchOut, 1)
        transport.setSongPos(back, abst)
        punch = [float(punch_in_bar), float(punch_out_bar)]
    if loop_record is not None:
        if not (hasattr(ui, "isLoopRecEnabled") and hasattr(midi, "FPT_LoopRecord")):
            raise ValueError("loop record is not available in this FL version")
        if bool(_try(ui.isLoopRecEnabled)) != bool(loop_record):
            transport.globalTransport(midi.FPT_LoopRecord, 1)
    return {"punch": punch, "loop_record": bool(loop_record) if loop_record is not None else
            (bool(_try(ui.isLoopRecEnabled)) if hasattr(ui, "isLoopRecEnabled") else None)}


def cmd_pattern_new(name=None):
    """Select the first empty pattern after the current one (FL's 'find next empty', no name prompt) and name it."""
    flags = getattr(midi, "FFNEP_DontPromptName", 2)
    _need(patterns, "findFirstNextEmptyPat")(flags)
    p = _try(patterns.patternNumber)
    if name and p:
        _try(patterns.setPatternName, p, name)
    return {"pattern": p, "name": _try(patterns.getPatternName, p) if p else None,
            "pattern_count": _try(patterns.patternCount)}


def _rec_get(eid):
    return general.processRECEvent(eid, 0, midi.REC_GetValue)


def cmd_sampler_live(index, pitch_cents=None, cutoff=None, resonance=None):
    """A channel's Pitch knob (cents) and filter cutoff / resonance (0..1) - the only Sampler settings FL's API
    reaches (no envelope / reverse / declick setters; plugins.* sees nothing on a Sampler). Reads them back."""
    # PC-VERIFY: setChannelPitch(i, cents, 1) moves the channel Pitch knob; REC_Chan_FCut/FRes values are 0..
    # midi.FromMIDI_Max with REC_MIDIController (read back with REC_GetValue)
    if not 0 <= index < _nch():
        raise ValueError("no channel %d" % index)
    out = {"index": index}
    if pitch_cents is not None:
        _need(CH, "setChannelPitch")(index, float(pitch_cents), 1)
    gp = getattr(CH, "getChannelPitch", None)
    if gp:
        out["pitch_cents"] = _try(gp, index, 1)
        out["pitch_range_st"] = _try(gp, index, 2)
    base = _try(CH.getRecEventId, index) if getattr(CH, "getRecEventId", None) else None
    top = float(getattr(midi, "FromMIDI_Max", 1 << 30))
    for key, const, val in (("cutoff", "REC_Chan_FCut", cutoff), ("resonance", "REC_Chan_FRes", resonance)):
        off = getattr(midi, const, None)
        if base is None or off is None:
            out[key] = None
            continue
        if val is not None:
            general.processRECEvent(base + off, int(round(max(0.0, min(1.0, val)) * top)),
                                    midi.REC_MIDIController | midi.REC_UpdateControl | midi.REC_UpdateValue)
        v = _try(_rec_get, base + off)
        out[key] = None if v is None else round(v / top, 4)
    return out


COMMANDS = {"diag": cmd_diag, "sampler_live": cmd_sampler_live, "context": cmd_context, "ping": cmd_ping, "state": cmd_state, "transport": cmd_transport, "set_tempo": cmd_set_tempo,
            "channel_set": cmd_channel_set, "mixer_set": cmd_mixer_set, "plugin_params": cmd_plugin_params,
            "plugin_set": cmd_plugin_set, "plugin_preset": cmd_plugin_preset, "plugin_info": cmd_plugin_info, "plugin_presets": cmd_plugin_presets,
            "playlist_track_set": cmd_playlist_track_set, "pattern_set": cmd_pattern_set, "steps": cmd_steps,
            "show": cmd_show, "batch": cmd_batch, "play": cmd_play, "stop_sound": cmd_stop_sound,
            "plugin_set_value": cmd_plugin_set_value, "song": cmd_song, "commands": cmd_commands, "where": cmd_where,
            "piano_roll": cmd_piano_roll, "plugin_probe": cmd_plugin_probe, "mixer_info": cmd_mixer_info,
            "mixer_arm": cmd_mixer_arm, "mixer_peaks": cmd_mixer_peaks, "rec_setup": cmd_rec_setup,
            "pattern_new": cmd_pattern_new, "plugin_get": cmd_plugin_get}


# ------------------------------------------------------------------ wire format
def _value(tok):
    kind, _, raw = tok.partition(":")
    if kind == "i":
        return int(raw)
    if kind == "f":
        return float(raw)
    if kind == "b":
        return raw == "1"
    if kind == "s":
        return base64.b64decode(raw).decode("utf-8")
    raise ValueError("bad value type")


def _dispatch(payload):
    name, *pairs = payload.split(";")
    fn = COMMANDS.get(name)
    if fn is None:
        raise ValueError("unknown command %s" % name)
    kwargs = {}
    for p in pairs:
        k, _, v = p.partition("=")
        if not k.isidentifier():
            raise ValueError("bad argument name")
        kwargs[k] = _value(v)
    return fn(**kwargs)


def _out(body):
    if device.dispatchReceiverCount() > 0:
        msg = FLAPI + bytes([2]) + body + b"\xF7"
        for i in range(device.dispatchReceiverCount()):
            device.dispatch(i, 0xF0, msg)
    else:
        device.midiOutSysex(bytes([0xF0, 0x7D]) + body + b"\xF7")


MAX_CHUNKS = 16383                                             # 14-bit counts: ~6 MB of reply


def _send(mid, text):
    """Reply in SysEx chunks. Up to 127 chunks: direction 1 with 7-bit part/count (what every server reads); more:
    direction 3 with 14-bit part/count (servers 1.17+), so a big project's state still fits."""
    data = base64.b64encode(text.encode("utf-8"))
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
    if len(chunks) > MAX_CHUNKS:
        data = base64.b64encode(("ER" + ascii("reply too large (%d bytes)" % len(text))).encode("utf-8"))
        chunks = [data]
    n_all = len(chunks)
    for n, c in enumerate(chunks):
        if n_all <= 127:
            head = bytes([1, mid & 0x7F, (mid >> 7) & 0x7F, n, n_all])
        else:
            head = bytes([3, mid & 0x7F, (mid >> 7) & 0x7F, n & 0x7F, (n >> 7) & 0x7F, n_all & 0x7F, (n_all >> 7) & 0x7F])
        _out(HDR[2:] + head + c)


def OnInit():
    print("fl-headless bridge ready (port %d, dispatch receivers %d)" % (device.getPortNumber(),
                                                                         device.dispatchReceiverCount()))


def OnSysEx(event):
    try:
        d = bytes(event.sysex)
    except Exception:
        return
    if d[:4] != HDR or len(d) < 10 or d[4] != 0:
        return
    event.handled = True
    mid = d[5] | (d[6] << 7)
    part, parts = d[7], d[8]
    now = time.time()
    for k in [k for k, v in _parts.items() if now - v.get("_t", now) > 30]:
        _parts.pop(k, None)                                        # requests the server gave up on
    ent = _parts.setdefault(mid, {"_t": now})
    ent[part] = d[9:-1]
    if len(ent) - 1 < parts:
        return
    got = _parts.pop(mid)
    try:
        try:
            payload = b"".join(got[i] for i in range(parts)).decode("ascii")
        except Exception as e:
            _send(mid, "ER" + ascii("%s: %s" % (type(e).__name__, e))); return
        reply = _run(payload)
        _log("cmd", mid, reply[:80])
        _send(mid, reply)
    except Exception as e:                                         # never let a reply problem break FL's MIDI loop
        _log("send", e)


def _finite(v):
    """FL reports a fader at zero as -inf; keep replies parseable (None for inf/nan)."""
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


def _run(payload):
    try:
        return "OK" + ascii(_finite(_dispatch(payload)))
    except Exception as e:
        return "ER" + ascii("%s: %s" % (type(e).__name__, e))
