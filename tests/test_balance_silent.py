"""Level balance: a melodic part WITH notes that measures silent alone (a FLEX preset whose content is missing on
this PC) gets the next-ranked sound for its part (the same ranking flh_new_song uses), is measured again alone,
the swap is said in notes, and the silent preset is remembered per PC (state.json 'silent_sounds') and skipped by
later picks; still silent after 2 swaps -> a clear note; a part with no notes is never swapped. FL is simulated.
Run: python3 tests/test_balance_silent.py"""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local"); os.environ["FLH_NO_WARM"] = "1"
os.environ["FLH_FLEX_DIR"] = os.path.join(tmp, "FLEX")
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
for mod in ("flh_store", "flh_catalog", "flh_compose", "flh_mixeng", "flh_flex", "flh_newsong"):
    __import__(mod).register(ns)
import flh_newsong as NS, flh_store, flh_flex as F  # noqa: E402

ROWS = []
def rows(*a, **k):
    return ROWS
F.index = rows; m.flex_index = rows                                   # the installed FLEX presets (simulated)
m.sound_ok = lambda p: True                                          # FLEX owned
m._stems_measure_units = None                                        # each part measured alone
def set_rows(*pp):
    ROWS[:] = [{"preset": pre, "pack": pk, "kind": F.kind_name("organ"), "genre": None} for pk, pre in pp]

song = os.path.join(tmp, "Gospel.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song)
rec, _ = NS.recipe("gospel")
org_i = next(i for i, c in enumerate(rec["channels"]) if c["name"] == "Organ")

def put(pack, preset):                                               # what flh_new_song put on the Organ channel
    fst, hit = F.flex_fst(preset, pack)
    m.flp_load_plugin(song, song, fst, channel=2)
    m.flp_style_channels(song, song, [{"channel": 2, "name": "Organ"}])
    flh_store.journal_update(song, set={"genre": "Gospel", "channels": {
        "0": {"role": "kick", "name": "Kick", "mixer": 1},
        "2": {"role": "melody", "name": "Organ", "mixer": 5, "plugin": "FLEX", "sound": fst, "pack": pack,
              "preset": preset, "spec": org_i}}})

DEAD = set()                                                         # presets that play nothing on this "PC"
asked = []
def on_ch2():
    d = m._load(song); ev = m._events(d); a, b = m._channel_block(d, ev, 2)
    return F.state_names(m._payload(d, ev[b]))
def preview(channels=None, **k):
    asked.append(list(channels or []))
    if channels == [2]:
        nm = on_ch2()
        return {"lufs": None, "silent": True} if nm and nm[1] in DEAD else {"lufs": -27.0, "silent": False}
    return {"lufs": -20.0, "silent": False}
m.fl_preview = preview
NOTES = {0: 8, 2: 130}
_info = m.flp_info
m.flp_info = lambda p: dict(_info(p), notes_per_channel=dict(NOTES))
m.fl_state = lambda: {"channels": [{"index": 0, "insert": 1}, {"index": 2, "insert": 5}],
                      "mixer": [{"index": 0, "effects": [{"slot": 9, "name": "Fruity Limiter"}]}]}
fader = {}
class B:
    def call(self, cmd, **a):
        if cmd == "mixer_set" and "volume_db" in a:
            fader[a["track"]] = a["volume_db"]
        return {"volume_db": fader.get(a.get("track"), 0.0)}
m.BRIDGE = B()
m.fl_save = lambda: {"saved": song}
m.fl_plugin_params = lambda index, slot=-1, filter="": {"params": [{"param": 0, "name": "Gain"}]}
m.fl_plugin_set_value = lambda index, param, target, slot=-1: {"text": f"{target:.1f}dB"}

# 1) the Organ has notes and its preset plays nothing: swapped to the next-ranked organ, re-measured once, noted
set_rows(("Sonitus Obscura", "Haunted Church Organ"), ("SH-1 Jump Up Repertoire", "Woodgrain Organ"))
put("Sonitus Obscura", "Haunted Church Organ")
DEAD.add("Haunted Church Organ")
r = NS.balance_song(song, "gospel")
txt = " ".join(r["notes"] or [])
assert "Organ: 'Haunted Church Organ' made no sound on this PC; swapped to 'Woodgrain Organ'" in txt, r
assert on_ch2() == ("SH-1 Jump Up Repertoire", "Woodgrain Organ"), on_ch2()
assert r["measured"].get("Organ") == -27.0 and asked.count([2]) == 2, (r, asked)     # one re-measure, alone
assert asked.count([0]) == 1, asked                                  # nothing else exported again
d = m._load(song); assert NS._chan_props(d, m._events(d), 2)["name"] == "Organ"     # name kept on the channel
jc = flh_store.journal_get(song)["channels"]["2"]
assert jc["preset"] == "Woodgrain Organ" and jc["pack"] == "SH-1 Jump Up Repertoire" and jc["spec"] == org_i, jc
sil = NS.silent_sounds()["silent_sounds"]
assert list(sil) == ["FLEX|Sonitus Obscura|Haunted Church Organ"], sil
# ... and skipped by every later pick: FLEX search, a new song's chooser
assert "Haunted Church Organ" not in [h["preset"] for h in F.flex_search(None, kind="organ", limit=10)]
NS._FLEX_USED.clear()
got = NS._resolve(dict(rec["channels"][org_i]), rec, "gospel", [], None, [], set())
assert got.get("preset") == "Woodgrain Organ", got
# clearing it (e.g. the pack was repaired) brings it back
assert NS.silent_sounds(action="clear", key="Haunted Church Organ")["forgotten"] == 1
assert "Haunted Church Organ" in [h["preset"] for h in F.flex_search(None, kind="organ", limit=10)]
assert "silent_sounds" in m.TOOLS and "clear" in m.TOOLS["silent_sounds"][1]

# 2) still silent after 2 swaps (organs from three different packs): the channel is the likelier cause - a clear
#    note, nothing remembered as silent, 2 re-measures only
set_rows(("Pack A", "Ghost Organ"), ("Pack B", "Crypt Organ"), ("Pack C", "Tomb Organ"), ("Pack D", "Last Organ"))
put("Pack A", "Ghost Organ")
DEAD.update({"Ghost Organ", "Crypt Organ", "Tomb Organ", "Last Organ"})
del asked[:]
r = NS.balance_song(song, "gospel")
txt = " ".join(r["notes"] or [])
assert "Organ" not in r["measured"] and asked.count([2]) == 3, (r, asked)           # first + 2 swaps
assert "stayed silent" in txt and "channel" in txt and "not balanced" in txt, txt
assert not NS.silent_sounds()["silent_sounds"], NS.silent_sounds()

# 2b) all from one pack (the pack's content is missing): each remembered, the note says what to do
set_rows(("Broken Pack", "Organ One"), ("Broken Pack", "Organ Two"), ("Broken Pack", "Organ Three"))
put("Broken Pack", "Organ One")
DEAD.update({"Organ One", "Organ Two", "Organ Three"})
r = NS.balance_song(song, "gospel")
txt = " ".join(r["notes"] or [])
assert "Organ has notes but 'Organ One' and " in txt and "made no sound on this PC" in txt and "pick another sound" in txt, txt
# one silent preset puts its pack last (still tried when nothing else is left); two silent presets of one pack: the
# pack is left out (likely not downloaded), its third preset isn't tried
assert sum(f"'Organ {x}'" in txt for x in ("One", "Two", "Three")) == 2, txt
assert len(NS.silent_sounds()["silent_sounds"]) == 2, NS.silent_sounds()
# every organ preset left is silent: the chooser says so and moves on (no FLEX organ)
NS._FLEX_USED.clear()
got = NS._resolve(dict(rec["channels"][org_i]), rec, "gospel", [], None, [], set())
assert got.get("plugin") != "FLEX" or got.get("flex_default"), got
NS.silent_sounds(action="clear")
assert not NS.silent_sounds()["silent_sounds"]

# 3) no notes on the part: never exported, never swapped, nothing remembered
set_rows(("Sonitus Obscura", "Haunted Church Organ"), ("SH-1 Jump Up Repertoire", "Woodgrain Organ"))
put("Sonitus Obscura", "Haunted Church Organ")
NOTES.pop(2); del asked[:]
r = NS.balance_song(song, "gospel")
assert [2] not in asked and on_ch2()[1] == "Haunted Church Organ" and "no notes yet" in " ".join(r["notes"]), r
assert not NS.silent_sounds()["silent_sounds"]
# INSTRUMENT packs a render found silent are listed and forgotten the same way
m._state({"silent_packs": {"LABS - Strings": {"seen": "2026-10-26"}, "LABS - Glass Piano": {"seen": "2026-10-24"}}})
assert set(NS.silent_sounds()["silent_packs"]) == {"LABS - Strings", "LABS - Glass Piano"}
assert NS.silent_sounds(action="clear", key="LABS - Strings")["forgotten"] == 1
assert set(m._state()["silent_packs"]) == {"LABS - Glass Piano"}
NS.silent_sounds(action="clear")
assert not m._state().get("silent_packs") and "silent_packs" not in NS.silent_sounds()
shutil.rmtree(tmp, True)
print("balance silent ok")
