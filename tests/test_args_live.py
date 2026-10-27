"""Argument consistency + live plugin tools (PC 2026-10-26/27): the same mixer track is 'track' in fl_mixer_set and
'index' (+ slot) in the plugin tools - both work everywhere; fl_plugin_params filter 'a|b' / regex and a short wait
while a plugin is still loading; fl_plugin_set_value with units ('30 ms', '2.00s' vs '194ms'), words ('Off') and
stepped knobs (nearest step, reported); flp_info fields; flh_journal add as a plain note; flp_add_channel
insert='new'. Simulated knobs, no FL. Run: python3 tests/test_args_live.py"""
import math, os, re, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import flh_store  # noqa: E402
ns = vars(m); flh_store.register(ns)
T = m.TOOLS
m.SET_VERIFY_S = 0.0
m.PLUGIN_READY_STEP_S = 0.01; m.PLUGIN_READY_WAIT_S = 0.2


def refused(fn, *words, err=ValueError):
    try:
        fn()
    except err as e:
        assert all(w in str(e) for w in words), e
        return str(e)
    raise SystemExit(f"not refused: {words}")


# ---------------------------------------------------------------- 3. track / index for the same mixer track
ca = lambda name, args: m._check_args(name, args, T[name])
assert ca("fl_plugin_params", {"track": 3, "slot": 1}) == {"index": 3, "slot": 1}
assert ca("fl_plugin_set", {"mixer_track": 3, "slot": 0, "param": 2, "value": 0.5})["index"] == 3
assert ca("fl_plugin_set_value", {"track": 2, "slot": 4, "param": 1, "target": "Off"})["index"] == 2
assert ca("fl_plugin_params", {"channel": 5}) == {"index": 5}
assert ca("fl_mixer_set", {"index": 4, "volume_db": -3})["track"] == 4
assert ca("fl_mixer_set", {"track": 4, "muted": True})["track"] == 4
refused(lambda: ca("fl_plugin_params", {"track": 3}), "names a mixer track", "slot")      # never channel 3's synth
refused(lambda: ca("fl_plugin_params", {"track": 3, "slot": -1}), "slot")
refused(lambda: ca("fl_plugin_params", {"channel": 3, "slot": 2}), "channel=")
refused(lambda: ca("fl_plugin_params", {"track": 3, "index": 3, "slot": 0}), "not both")
for t in ("fl_plugin_params", "fl_plugin_set", "fl_plugin_set_value", "fl_mixer_set"):
    assert "works too" in T[t][1], t                              # documented in the descriptions

# ---------------------------------------------------------------- flp_info fields, flh_journal plain notes
song = os.path.join(tmp, "Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song)
fi = m.flp_info(song, fields=["tempo_bpm", "time_signature"])
assert fi == {"path": song, "tempo_bpm": 60.0, "time_signature": "4/4"}, fi
fi = m.flp_info(song, fields="ppq, nope")
assert fi["ppq"] == 96 and fi["unknown_fields"] == ["nope"] and "tempo_bpm" in fi["fields_here"], fi
assert "notes_per_channel" in m.flp_info(song)
assert ca("flp_info", {"path": song, "fields": ["ppq"]})["fields"] == ["ppq"]
j = flh_store.flh_journal(song, add="the choir is too loud in the climax")
assert "the choir is too loud in the climax" in flh_store.journal_get(song)["notes"] and "note" in j["added"], j
flh_store.flh_journal(song, add=["keep the piano dry", "no strings"])
flh_store.flh_journal(song, add='{"dislikes": "the delay on the melody"}')
jj = flh_store.journal_get(song)
assert {"keep the piano dry", "no strings"} <= set(jj["notes"]) and "the delay on the melody" in jj["dislikes"], jj
assert ca("flh_journal", {"project": song, "add": "a plain note"})["add"] == "a plain note"
r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "flh_journal", "arguments": {"project": song, "add": "via the dispatcher"}}})
assert not r.get("isError") and "via the dispatcher" in flh_store.journal_get(song)["notes"], r

# ---------------------------------------------------------------- simulated plugin knobs behind the bridge
KNOBS = {}


class Knob:
    def __init__(self, name, show, pos=0.5, async_text=False):
        self.name, self.show, self.pos = name, show, pos


def num_in(text):                                                 # the bridge script's bare-number reading
    t = (text or "").replace(",", ""); m_ = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m_:
        return None
    return float(m_.group(0)) * (1000 if "khz" in t.lower() else 1)


state = {"not_ready": 0, "calls": []}


def bridge(cmd, timeout=15.0, **a):
    state["calls"].append(cmd)
    if state["not_ready"] > 0:
        state["not_ready"] -= 1
        raise RuntimeError("TypeError: 'NoneType' object cannot be interpreted as an integer")
    if cmd == "plugin_params":
        ps = [{"param": k, "name": kn.name, "value": kn.pos, "text": kn.show(kn.pos)} for k, kn in sorted(KNOBS.items())]
        f = str(a.get("filter") or "").lower()
        return {"plugin": "Some Verb", "count": len(ps), "params": [p for p in ps if not f or f in p["name"].lower()]}
    kn = KNOBS[a["param"]]
    if cmd == "plugin_get":
        return {"param": a["param"], "name": kn.name, "value": kn.pos, "text": kn.show(kn.pos)}
    if cmd == "plugin_set":
        kn.pos = max(0.0, min(1.0, float(a["value"])))
        return {"param": a["param"], "value": kn.pos, "text": kn.show(kn.pos)}
    if cmd == "plugin_probe":
        n = int(a["points"]); vals = [k / (n - 1) for k in range(n)]
        return {"values": vals, "texts": [kn.show(v) for v in vals], "before": kn.pos, "restored": kn.pos}
    if cmd == "plugin_set_value":                                  # what the script does, on bare numbers
        tgt = float(a["target"]); before = kn.pos
        if num_in(kn.show(before)) is None:
            raise RuntimeError("ValueError: this parameter doesn't show a number")
        grid = [(q / 1000, num_in(kn.show(q / 1000))) for q in range(1001)]
        grid = [(q, v) for q, v in grid if v is not None]
        best = min(grid, key=lambda t: abs(t[1] - tgt))
        bracket = any(v < tgt for _, v in grid) and any(v >= tgt for _, v in grid)
        if not bracket and abs(best[1] - tgt) > 0.02 * max(1.0, abs(tgt)):
            raise RuntimeError("ValueError: FL's display never reached %g on this knob (closest it showed: %g); left "
                               "unchanged" % (tgt, best[1]))
        kn.pos = best[0]
        return {"param": a["param"], "name": kn.name, "text": kn.show(kn.pos), "value": kn.pos, "before": before,
                "set_to": kn.pos}
    raise RuntimeError(f"unknown command {cmd}")


m.BRIDGE.call = bridge
m._driven_by = lambda key, project=None: []
predelay = lambda q: f"{int(round(round(q * 64) * 3.125))}ms"             # 3.125 ms steps: 28, 31, 34 ...
lowcut = lambda q: "Off" if q < 0.05 else f"{20 + (q - 0.05) * 1000:.0f} Hz"
decay = lambda q: (f"{q * 2000:.0f}ms" if q * 2000 < 1000 else f"{q * 2:.2f}s")   # '194ms' ... '2.00s'
gain = lambda q: "-inf dB" if q <= 0 else f"{20 * math.log10(q / 0.8):.1f}dB"
KNOBS.update({0: Knob("Pre-delay", predelay, 0.5), 1: Knob("Low cut", lowcut, 0.0), 2: Knob("Decay", decay, 0.097),
              3: Knob("Wet level", gain, 0.0), 4: Knob("Attack", lambda q: f"{q * 100:.0f}ms", 0.2),
              5: Knob("Release", lambda q: f"{q * 500:.0f}ms", 0.3)})

# 5. a plugin still loading answers 'NoneType' errors for a moment: waited for, not failed
state["not_ready"] = 3
r = m.fl_plugin_params(0, slot=1)
assert r["params"] and r.get("ready_after_s") is not None, r
state["not_ready"] = 999
try:
    m.fl_plugin_params(0, slot=1); raise SystemExit("never ready: must fail")
except RuntimeError as e:
    assert "isn't ready in FL" in str(e) and "mixer track 0 slot 1" in str(e), e
state["not_ready"] = 0
try:
    bridge_err = m.BRIDGE.call
    m.BRIDGE.call = lambda cmd, **a: (_ for _ in ()).throw(RuntimeError("Plugin not valid"))
    m.fl_plugin_params(0, 1); raise SystemExit("other errors: raised at once")
except RuntimeError as e:
    assert "Plugin not valid" in str(e) and "ready" not in str(e), e
finally:
    m.BRIDGE.call = bridge_err

# 3. filter: 'a|b' (one filtered read per word), a regex, a literal with regex signs, a list
names = lambda r: [p["name"] for p in r["params"]]
assert names(m.fl_plugin_params(0, 1, "attack|release")) == ["Attack", "Release"]
assert names(m.fl_plugin_params(0, 1, ["decay", "wet"])) == ["Decay", "Wet level"]
assert names(m.fl_plugin_params(0, 1, "^(pre|low)")) == ["Pre-delay", "Low cut"]
assert names(m.fl_plugin_params(0, 1, "pre-delay")) == ["Pre-delay"]
assert names(m.fl_plugin_params(0, 1, "")) == [k.name for _, k in sorted(KNOBS.items())]

# 4. set_value: a stepped knob lands on the nearest step and says so (asked 30 ms, it shows 31 ms)
r = m.fl_plugin_set_value(1, 0, 30, slot=1)
assert r["held"] and r["text"] == "31ms" and "nearest step" in r["nearest_step"] and "31ms" in r["nearest_step"], r
r = m.fl_plugin_set_value(1, 0, "34 ms", slot=1)
assert r["held"] and r["text"] == "34ms" and "nearest_step" not in r, r
# a knob showing 'Off' (no number): set by its word, and a number counts 'Off' as its bottom
KNOBS[1].pos = 0.5
r = m.fl_plugin_set_value(1, 1, "Off", slot=1)
assert r["held"] and r["text"] == "Off" and KNOBS[1].pos < 0.05, r
r = m.fl_plugin_set_value(1, 1, 120, slot=1)                      # from Off: the script can't read it, we can
assert r["held"] and abs(float(r["text"].split()[0]) - 120) <= 2.4, r
assert any("bridge script" in n for n in r.get("notes") or []), r
r = m.fl_plugin_set_value(1, 1, 0, slot=1)
assert r["held"] and r["text"] == "Off", r
refused(lambda: m.fl_plugin_set_value(1, 1, "Bypass", slot=1), "NOT set", "never shows 'Bypass'", "Off",
        err=RuntimeError)
# mixed units: '194ms' ... '2.00s' - 1200 (read in the knob's ms) and '1.2 s' both reach 1.20s
KNOBS[2].pos = 0.097
r = m.fl_plugin_set_value(1, 2, 1200, slot=1)
assert r["held"] and r["text"] == "1.20s", r
assert any("as ms" in n for n in r.get("notes") or []), r
KNOBS[2].pos = 0.097
r = m.fl_plugin_set_value(1, 2, "1.5 s", slot=1)
assert r["held"] and r["text"] == "1.50s", r
r = m.fl_plugin_set_value(1, 2, "300ms", slot=1)
assert r["held"] and r["text"] == "300ms", r
# dB from -inf (no number for the script): searched here; a plain dB target still goes through the script
r = m.fl_plugin_set_value(1, 3, -6, slot=1)
assert r["held"] and abs(float(r["text"][:-2]) + 6) <= 0.15, r
state["calls"].clear()
r = m.fl_plugin_set_value(1, 3, -3, slot=1)
assert r["held"] and abs(float(r["text"][:-2]) + 3) <= 0.15 and "plugin_set_value" in state["calls"], r
assert state["calls"].count("plugin_set") == 0, state["calls"]          # one script search, no extra sets
refused(lambda: m.fl_plugin_set_value(1, 4, "5 kHz", slot=1), "NOT set", "nothing comparable", err=RuntimeError)
refused(lambda: m.fl_plugin_set_value(1, 4, float("nan"), slot=1), "target")
assert KNOBS[4].pos == 0.2                                             # refused: the knob is back where it was
# the dispatcher: string targets with units pass the schema; track= works
ns["_windows_only"] = lambda name: False
r = m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "fl_plugin_set_value", "arguments": {
    "track": 1, "slot": 1, "param": 0, "target": "40 ms"}}})
assert not r.get("isError") and "41ms" in r["content"][0]["text"] and "nearest_step" in r["content"][0]["text"], r

# ---------------------------------------------------------------- 7. flp_add_channel insert='new'
out = os.path.join(tmp, "Added.flp")
ins0 = m._channel_inserts(m._load(song))
r = m.flp_add_channel(song, out, 0, insert="new", name="Celeste")
d = m._load(out); ev = m._events(d); ins = m._channel_inserts(d, ev)
assert r["insert"] not in ins0.values() and ins[r["new_channel"]] == r["insert"], (r, ins0, ins)
assert m._mixer_names(d, ev).get(r["insert"]) == "Celeste" and r["insert_name"] == "Celeste", r
assert any(c["index"] == r["new_channel"] and c["name"] == "Celeste" for c in m.flp_list(out)["channels"])
r2 = m.flp_add_channel(out, out, 0)                                    # default: the cloned channel's insert
assert r2["insert"] == ins0[0] and "insert_shared_with" in r2, r2
r3 = m.flp_add_channel(out, out, 1, insert=7)
assert m._channel_inserts(m._load(out))[r3["new_channel"]] == 7
refused(lambda: m.flp_add_channel(out, out, 1, insert="sideways"), "insert:")
assert "insert" in T["flp_add_channel"][2]["properties"]
shutil.rmtree(tmp, True)
print("ok")
