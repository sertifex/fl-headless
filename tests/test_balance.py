"""New-song level balance: each melodic channel (and the drums together) measured alone, faders moved so the parts
sit at their targets against the drums, the drums come down when a quiet part runs out of fader, the master
limiter's gain brings the beat up; the background runner holds FL and reports as news. FL is simulated.
Run: python3 tests/test_balance.py"""
import os, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local"); os.environ["FLH_NO_WARM"] = "1"
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
for mod in ("flh_store", "flh_catalog", "flh_compose", "flh_mixeng", "flh_newsong"):
    __import__(mod).register(ns)
import flh_newsong as NS, flh_store, flh_mixeng as MX  # noqa: E402

song = os.path.join(tmp, "Beat.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song)
flh_store.journal_update(song, {"channels": {"0": {"role": "kick", "name": "Kick", "mixer": 1},
                                             "1": {"role": "snare", "name": "Snare", "mixer": 2},
                                             "5": {"role": "bass", "name": "Bass", "mixer": 6},
                                             "6": {"role": "keys", "name": "Keys", "mixer": 7},
                                             "8": {"role": "pad", "name": "Pad", "mixer": 9}}})
level = {0: -20.0, 1: -22.0, 5: -21.0, 6: -41.0, 8: -40.0}          # what each channel measures alone (LUFS)
fader = {1: 0.0, 2: 0.0, 6: 0.0, 7: 0.0, 9: 0.0}
gains = []
def lufs_of(chs):
    import math
    return 10 * math.log10(sum(10 ** ((level[c] + fader[{0: 1, 1: 2, 5: 6, 6: 7, 8: 9}[c]]) / 10) for c in chs))
m.fl_preview = lambda channels=None, **k: {"lufs": round(lufs_of(channels), 2), "silent": False}
NOTES = {0: 8, 1: 4, 5: 8, 6: 16, 8: 4}                              # notes per channel in the song
_info = m.flp_info
m.flp_info = lambda p: dict(_info(p), notes_per_channel=dict(NOTES))
m.fl_state = lambda: {"channels": [{"index": c, "insert": t} for c, t in {0: 1, 1: 2, 5: 6, 6: 7, 8: 9}.items()],
                      "mixer": [{"index": 0, "effects": [{"slot": 0, "name": "Fruity parametric EQ 2"},
                                                         {"slot": 9, "name": "Fruity Limiter"}]}]}
class B:
    def call(self, cmd, **a):
        if cmd == "mixer_set":
            if "volume_db" in a:
                fader[a["track"]] = a["volume_db"]
            return {"volume_db": fader.get(a["track"], 0.0)}
        return {}
m.BRIDGE = B()
m.fl_save = lambda: {"saved": song}
m.fl_plugin_params = lambda index, slot=-1, filter="": {"plugin": "Fruity Limiter", "params": [
    {"param": 0, "name": "Gain", "value": 0.5, "text": "0.0dB"}, {"param": 2, "name": "Limiter ceiling"}]}
m.fl_plugin_set_value = lambda index, param, target, slot=-1: gains.append((index, slot, param, target)) or {
    "text": f"{target:.1f}dB"}

r = NS.balance_song(song, "lofi")
assert set(r["measured"]) == {"drums", "Bass", "Keys", "Pad"}, r
drums = lufs_of([0, 1])
keys, pad, bass = lufs_of([6]), lufs_of([8]), lufs_of([5])
assert abs(keys - (drums - 4)) < 0.6 and abs(pad - (drums - 9)) < 0.6 and abs(bass - (drums - 1)) < 0.6, \
    (drums, keys, pad, bass, r)
assert fader[1] < 0 and fader[2] < 0 and any("drums" in x for x in r["moved"]), (fader, r)   # keys needed > +5.6 dB
assert all(v <= NS.FADER_MAX_DB for v in fader.values()), fader
assert gains and gains[0][:3] == (0, 9, 0) and gains[0][3] > 0 and any("master" in x for x in r["moved"]), gains

# a part that's silent alone is reported, not chased
level[8] = -200.0
m.fl_preview = lambda channels=None, **k: ({"lufs": None, "silent": True} if channels == [8] else
                                           {"lufs": round(lufs_of(channels), 2), "silent": False})
r = NS.balance_song(song, "lofi")
assert "Pad" not in r["measured"] and "silent" in " ".join(r["notes"]), r

# a part with no notes yet isn't exported at all (it would only measure silence)
asked = []
_pv = m.fl_preview
m.fl_preview = lambda channels=None, **k: (asked.append(list(channels or [])), _pv(channels=channels, **k))[1]
NOTES.pop(8)
r = NS.balance_song(song, "lofi")
assert [8] not in asked and "Pad" not in r["measured"] and "no notes yet" in " ".join(r["notes"]), (asked, r)
NOTES[8] = 4; m.fl_preview = _pv

# the background runner: holds FL, reports with the next result
m._wait_open_in_fl = lambda p, t=240: True
del m._AUTOSYNC_NEWS[:]
NS.start_balance(song, "lofi")
t0 = time.time()
while not m._AUTOSYNC_NEWS and time.time() - t0 < 10:
    time.sleep(0.05)
assert m._AUTOSYNC_NEWS and "levels balanced" in m._AUTOSYNC_NEWS[0], m._AUTOSYNC_NEWS
assert m._FL_HOLDER.get("what") is None and m._FL_LOCK.acquire(blocking=False)
m._FL_LOCK.release()
shutil.rmtree(tmp, True)
print("balance ok")
