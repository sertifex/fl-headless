"""Round-trip test of the live bridge without FL Studio or MIDI ports.

A fake FL scripting API is installed, the real device script runs inside it, and the MCP server's bridge
client talks to it through an in-memory loop (both the direct and the Flapi-relay reply paths).
Run:  python tests/test_bridge.py
"""
import math
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def install_fake_fl(sent, relay):
    def mod(name, **kw):
        m = types.ModuleType(name); m.__dict__.update(kw); sys.modules[name] = m

    flapi = bytes([0x7D, 0x46, 0x6C, 0x61, 0x70, 0x69])

    def dispatch(i, status, msg):                                 # behaves like the Flapi Response script
        d = bytes(msg); body = d[len(flapi) + 1:]
        assert d[1:len(flapi) + 1] == flapi and body[0] == 2
        sent.append(bytes([0xF0]) + flapi + bytes([1]) + body[1:])

    mod("device", getPortNumber=lambda: 10, dispatchReceiverCount=lambda: 1 if relay else 0,
        dispatch=dispatch, midiOutSysex=lambda b: sent.append(bytes(b)))
    mod("midi", FPN_Preset=6, SONGLENGTH_S=1, FPT_Save=92, REC_Tempo=1, REC_Control=1, REC_UpdateControl=2, REC_UpdateValue=4, FPT_Metronome=110, FPT_Loop=15, FPT_Record=12)
    vol = {0: 0.8, 3: 0.8}
    db = lambda v: 20 * math.log10(max(v, 1e-6) / 0.8) * 3
    names, colors, routes, grid, played = {}, {}, set(), {}, []
    install_fake_fl.played = played
    mod("mixer", trackCount=lambda: 20, getCurrentTempo=lambda: 60000, getTrackName=lambda t: names.get(("m", t), f"Insert {t}"),
        getTrackVolume=lambda t, m=0: db(vol.get(t, 0.8)) if m else vol.get(t, 0.8), setTrackVolume=lambda t, v: vol.__setitem__(t, v),
        getTrackPan=lambda t: 0.0, setTrackPan=lambda t, p: None, isTrackMuted=lambda t: False,
        setTrackName=lambda t, n: names.__setitem__(("m", t), n),
        getTrackColor=lambda t: colors.get(("m", t), 0x555555), setTrackColor=lambda t, c: colors.__setitem__(("m", t), c),
        setRouteTo=lambda a, b, v, u=False: (routes.add((a, b)) if v else routes.discard((a, b))),
        getRouteSendActive=lambda a, b: (a, b) in routes, afterRoutingChanged=lambda: None)
    nch = install_fake_fl.nch = [2]; flags = install_fake_fl.flags = []
    def chname(i, g=False):
        flags.append(g); return names.get(("c", i), f"Piano {i}")
    mod("channels", channelCount=lambda g=False: nch[0], getTargetFxTrack=lambda i: 3 + i, getChannelName=chname,
        getChannelVolume=lambda i, m=0: 0.78, getChannelPan=lambda i: 0.0, isChannelMuted=lambda i: False,
        getChannelColor=lambda i: colors.get(("c", i), 0x5F6A70), setChannelColor=lambda i, c: colors.__setitem__(("c", i), c),
        setGridBit=lambda i, p, v: grid.__setitem__((i, p), v),
        midiNoteOn=lambda i, n, v, *a: played.append((i, n, v)))
    mod("playlist", getTrackName=lambda t: names.get(("p", t), f"Track {t}"), setTrackName=lambda t, n: names.__setitem__(("p", t), n),
        getTrackColor=lambda t: colors.get(("p", t), 0x444444), setTrackColor=lambda t, c: colors.__setitem__(("p", t), c))
    mod("patterns", patternCount=lambda: 2, getPatternName=lambda p: names.get(("pat", p), f"Pattern {p}"),
        setPatternName=lambda p, n: names.__setitem__(("pat", p), n), getPatternColor=lambda p: 0x333333,
        setPatternColor=lambda p, c: None, patternNumber=lambda: 1, jumpToPattern=lambda p: None)
    mod("ui", showWindow=lambda w: None, setFocused=lambda w: None, isMetronomeEnabled=lambda: True)
    install_fake_fl.store = (names, colors, routes, grid)
    presets = ["Init", "Felt Piano", "Grand Piano", "Warm Pad"]; cur = [0]; knob = [0.5]
    songpos = [0]; markers = []
    mod("arrangement", addAutoTimeMarker=lambda t, n: markers.append((t, n)), jumpToMarker=lambda d, s: None)
    install_fake_fl.markers = markers
    def step(d): cur[0] = (cur[0] + d) % len(presets)
    mod("plugins", isValid=lambda i, s=-1: s in (-1, 0), getPluginName=lambda i, s=-1: "Piano" if s == -1 else "Reverb",
        getName=lambda i, s=-1, flag=0, p=-1: (presets[cur[0]] if p < 0 else presets[p]) if flag == 6 else "x",
        getPresetCount=lambda i, s=-1: len(presets),
        nextPreset=lambda i, s=-1: step(1), prevPreset=lambda i, s=-1: step(-1),
        getParamCount=lambda i, s=-1: 2, getParamName=lambda p, i, s=-1: ["Reverb", "Expression"][p],
        getParamValue=lambda p, i, s=-1: knob[0], getParamValueString=lambda p, i, s=-1: disp[0](knob[0]),
        setParamValue=lambda v, p, i, s=-1: (knob.__setitem__(0, v), visits.append(v)))
    disp = install_fake_fl.disp = [lambda x: "%+.1f dB" % (x * 36 - 18)]
    visits = install_fake_fl.visits = []; install_fake_fl.knob = knob
    mod("transport", isPlaying=lambda: False, getSongPos=lambda m=0: songpos[0] if m == 2 else 12.5,
        setSongPos=lambda p, m=0: songpos.__setitem__(0, p), globalTransport=lambda f, v: None,
        isRecording=lambda: False, getLoopMode=lambda: 1)
    mod("general", getRecPPQ=lambda: 96)


def run(relay):
    for m in ("device", "device_flheadless", "fl_headless_mcp"):
        sys.modules.pop(m, None)
    sent = []
    install_fake_fl(sent, relay)
    sys.path[:0] = [os.path.join(ROOT, "src", "fl_script"), os.path.join(ROOT, "src")]
    import device_flheadless as script
    import fl_headless_mcp as server

    class Event:
        def __init__(self, b):
            self.sysex, self.handled = b, False

    inbox = []
    server.BRIDGE.open = lambda: None
    server.BRIDGE._send = lambda data: (script.OnSysEx(Event(data)), inbox.extend(sent), sent.clear())
    server.BRIDGE._drain = lambda: [inbox.pop(0) for _ in range(len(inbox))]

    pong = server.fl_ping()
    assert pong == {"bridge": "fl-headless", "port": 10, "version": script.SCRIPT_VERSION}, pong   # current: no outdated note
    st = server.fl_state()
    assert st["tempo"] == 60.0 and len(st["channels"]) == 2 and st["mixer"][1]["effects"][0]["name"] == "Reverb"
    assert server.fl_plugin_params(0, filter="rev")["params"][0]["name"] == "Reverb"
    assert server.fl_mixer_set(3, volume_db=-6)["volume_db"] == -6.0
    names, colors, routes, grid = install_fake_fl.store
    r = server.fl_mixer_set(3, name="Piano", color="blue", route_to=[5, 6])
    assert r["color"] == "#4A80D8" and r["routes_to"] == [5, 6] and names[("m", 3)] == "Piano", r
    assert server.fl_mixer_set(3, unroute=[6])["routes_to"] == [5]
    assert server.fl_channel_set(1, color="#112233")["color"] == "#112233"
    assert server.fl_playlist_track_set(2, name="Bass", color="green")["name"] == "Bass"
    assert server.fl_pattern_set(1, name="Intro")["name"] == "Intro"
    server.fl_steps(0, "x...x...x...x..."); assert grid[(0, 4)] == 1 and grid[(0, 5)] == 0
    assert server.fl_show("mixer") == {"shown": "mixer"}
    b = server.fl_batch([{"op": "mixer", "track": 7, "name": "Pad", "color": "purple"}, {"op": "bogus"}][:1])
    assert b["done"] == 1 and names[("m", 7)] == "Pad"
    o = server.fl_organize()
    assert o["done"] == len(o["results"]) and names[("m", 4)] == "Piano 1" and names[("p", 1)] == "Piano 0", o
    assert colors[("m", 4)] == colors[("c", 1)] == colors[("p", 2)]
    played = install_fake_fl.played
    r = server.fl_play(1, chord="G3 D4 Bb4", length_ms=0)
    assert r["notes"] == 3 and (1, 55, 90) in played and (1, 55, 0) in played, played
    script.OnIdle(); server.fl_play(0, notes=[{"note": "C5", "start": 0, "length": 0}]); assert (0, 72, 0) in played
    assert server.fl_stop_sound() == {"stopped": True}
    r = server.fl_plugin_set_value(0, 0, -3.0, slot=0)
    assert r["text"] == "-3.0 dB", r
    assert server.fl_song("goto", bar=5)["bar"] == 5.0
    assert server.fl_song("where")["bar"] == 5.0
    assert server.fl_song("metronome")["metronome"] is False      # fake starts enabled; reply shows the toggled state
    assert server.fl_song("marker_jump", delta=1)["done"] == "marker_jump"
    server.fl_song("marker_add", bar=9, name="Drop"); assert install_fake_fl.markers[-1] == (8 * 4 * 96, "Drop")
    assert server.fl_plugin_info(0)["preset"] == "Init"
    assert server.fl_plugin_preset(0)["preset"] == "Felt Piano"
    assert server.fl_plugin_preset_find(0, "grand")["preset"] == "Grand Piano"
    assert server.fl_plugin_preset_find(0, "init")["confirmed"]          # wraps around backwards
    r = server.fl_plugin_preset_find(0, "nothing like this")
    assert not r["found"] and "Warm Pad" in r["available"], r
    long_name = "x" * 5000                                        # multi-chunk request
    assert server.fl_mixer_set(3, name=long_name)["track"] == 3
    for bad, args in (("__import__('os')", {}), ("state", {"x": "y"})):
        try:
            server.BRIDGE.call(bad, **args)
            raise AssertionError("should have been refused: " + bad)
        except RuntimeError as e:
            assert str(e).startswith("FL:")
    # global channel indexes: the flag goes to FL (a filter group can't shift the index)
    install_fake_fl.flags.clear(); server.fl_channel_set(1, color="#223344")
    assert install_fake_fl.flags and all(install_fake_fl.flags), install_fake_fl.flags
    # a big project: the state reply needs more than 127 chunks -> 14-bit framing; the light state stays tiny
    install_fake_fl.nch[0] = 400
    st = server.fl_state()
    assert st["channel_count"] == 400 and len(st["channels"]) == script.STATE_MAX_CHANNELS and st["channels_capped"]
    assert len(base64_len(st)) > 127 * script.CHUNK, "the test must exercise the long framing"
    lite = server._bridge_state_light()
    assert lite["light"] and lite["channel_count"] == 400 and "channels" not in lite, lite
    install_fake_fl.nch[0] = 2
    # setting a knob by its display never sweeps to the extremes and puts the knob back on failure
    knob, visits = install_fake_fl.knob, install_fake_fl.visits
    knob[0] = 0.3; visits.clear()
    r = server.fl_plugin_set_value(0, 0, 0.0, slot=0)                # 0.3 shows -7.2 dB; 0 dB is at 0.5
    assert r["text"] in ("+0.0 dB", "-0.0 dB") and min(visits) >= 0.3 - 0.021 and max(visits) <= 0.65, visits
    assert 0.0 not in visits and 1.0 not in visits
    install_fake_fl.disp[0] = lambda x: "-INF dB" if x > 0.45 else "%+.1f dB" % (x * 36 - 18)
    knob[0] = 0.3; visits.clear()
    try:
        server.fl_plugin_set_value(0, 0, 3.0, slot=0); raise AssertionError("should fail")
    except RuntimeError:
        pass
    assert knob[0] == 0.3 and max(visits) < 0.9, (knob, visits)       # restored, never near the top
    install_fake_fl.disp[0] = lambda x: "%+.1f dB" % (x * 36 - 18)
    knob[0] = 0.9; visits.clear()                                     # +18 dB needs the very top: refused
    try:
        server.fl_plugin_set_value(0, 0, 18.0, slot=0); raise AssertionError("should refuse the maximum")
    except RuntimeError as e:
        assert "maximum" in str(e) or "never reached" in str(e), e
    assert knob[0] == 0.9, knob
    knob[0] = 0.5
    print("bridge ok (%s path)" % ("Flapi relay" if relay else "direct"))


def base64_len(obj):
    import base64
    return base64.b64encode(("OK" + ascii(obj)).encode())


if __name__ == "__main__":
    run(relay=False)
    run(relay=True)
