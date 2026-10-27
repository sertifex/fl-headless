"""fl_audition's ranking (fl_sound.score) on synthetic feature sets: a wish's words become targets (bright -> high
centroid, plucky / short -> fast attack and low sustain, not bassy -> a low centroid / low end sinks, clean -> a
'DIST' preset sinks, lead -> mono-ish), each feature bounded so one extreme reading can't win; a slow swell is never
'plucky'. describe() measures sustain while the note is held. fl_audition keeps its previews in fl-headless's state
folder (last run only; never in a song / collection folder) and skips presets that can't go on a plugin channel.
Run: python3 tests/test_sound_rank.py"""
import os, shutil, sys, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
import fl_sound as S  # noqa: E402

T = S.targets
t = T("bright plucky analog sequencer synth, short decay, clean, not bassy")
assert t["bright"] > 0 and t["sustain"] < 0 and t["attack"] < 0 and t["dirt"] < 0 and t["low_end"] < 0, t
assert T("not too bassy")["low_end"] < 0 and T("bassy")["low_end"] > 0 and T("non-distorted")["dirt"] < 0
assert T("bright clean analog square lead")["width"] < 0 and T("slow attack")["attack"] > 0
assert T("pads")["sustain"] > 0 and T("") == {} and T("analog square") == {}


def d(attack, sustain, cen, width=-14.0, low=None, flat=0.05):
    x = {"attack_ms": attack, "sustain_db": sustain, "centroid_hz": cen, "width_db": width, "flatness": flat}
    if low is not None:
        x["low_share"] = low
    return x


def rank(want, cands):
    return [n for n, _ in sorted(cands.items(), key=lambda kv: -S.score(kv[1][0], want, kv[1][1]))]


# the reported case: a slow dark swell ranked first for a bright plucky sequencer sound
W1 = "bright plucky analog sequencer synth, short decay, clean, not bassy"
C1 = {"swell": (d(1580, -60.0, 385, low=0.55), "Warm Swell Pad"),          # (released after the hold: reads 'short')
      "sharp": (d(6, -24.0, 1500, low=0.08), "Seq Pluck 1"),
      "sharp2": (d(9, -20.0, 1650, low=0.06), "Juno Arp"),
      "bassy": (d(7, -22.0, 320, low=0.62), "Sub Pluck Bass"),
      "dist": (d(6, -22.0, 2400, flat=0.32, low=0.05), "DIST Saw Pluck"),
      "padlong": (d(400, -2.0, 1800, width=-4.0), "Bright Pad")}
r1 = rank(W1, C1)
assert r1[0] in ("sharp", "sharp2") and r1[1] in ("sharp", "sharp2"), r1
assert r1.index("swell") >= 4 and r1.index("dist") > r1.index("sharp") and r1.index("bassy") > 1, r1
sc = S.score(C1["swell"][0], W1, C1["swell"][1], detail=True)
assert sc["score"] < 0 and "too slow" in sc["why"], sc
# the second case: a distorted FL Slayer guitar first for a clean square lead
W2 = "bright clean analog square lead"
C2 = {"slayer": (d(20, -3.0, 2800, width=-15.0, flat=0.3), r"Plugin presets\Generators\FL Slayer\DIST Sololead Glide"),
      "square": (d(10, -2.0, 2000, width=-18.0, flat=0.06), "Square Lead Clean"),
      "saw": (d(12, -4.0, 2300, width=-6.0, flat=0.1), "Analog Saw Lead"),
      "pad": (d(900, -1.0, 600, width=-3.0), "Warm Pad")}
r2 = rank(W2, C2)
assert r2[0] == "square" and r2.index("slayer") > r2.index("saw") and r2[-1] in ("pad", "slayer"), r2
# features are bounded: an absurd reading moves a score a bounded amount
big = S.score(d(5, 30.0, 20000), "bright"); mid = S.score(d(5, -6.0, 5000), "bright")
assert big - mid < 1.0, (big, mid)
# the old wishes still order as before
assert S.score(d(600, -1.0, 400), "soft string pad") > S.score(d(3, -30.0, 3500), "soft string pad")
assert S.score(d(3, -30.0, 3500), "bright pluck") > S.score(d(600, -1.0, 400), "bright pluck")
assert S.score({"silent": True}, "anything") == -99.0

# describe(): sustain while held (a release after the key goes up isn't a short decay)
sr = 48000
tt = np.arange(int(4.5 * sr)) / sr
env = np.minimum(1, tt / 1.6) * np.where(tt < 2.5, 1.0, np.exp(-(tt - 2.5) / 0.1))   # 1.6 s swell, key up at 2.5 s
y = np.sin(2 * np.pi * 220 * tt) * env * 0.5
sw = S.describe(np.stack([y, y], 1), sr, hold_s=2.5)
assert sw["attack_ms"] > 1000 and sw["sustain_db"] > -6, sw
assert "low_share" in sw and "flatness" in sw

# ----------------------------------------------------------------------------- fl_audition: where previews go
tmp = tempfile.mkdtemp()
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
coll = os.path.join(tmp, "Album", "FINAL"); os.makedirs(coll)
os.environ["FLH_ROOTS"] = coll + os.pathsep + tmp if os.name == "nt" else coll + ";" + tmp
os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
import fl_capture as FC  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
os.environ["FLH_ROOTS"] = ";".join([coll, tmp])


def tone(freqs, attack, decay):
    tt2 = np.arange(int(3 * sr)) / sr
    e = np.minimum(1, tt2 / max(attack, 1e-3)) * np.exp(-tt2 / decay)
    yy = sum(np.sin(2 * np.pi * f * tt2) for f in freqs) / len(freqs) * e * 0.5
    return np.stack([yy, yy * 0.9], 1)


SOUNDS = {5: tone([220, 330, 440], 0.6, 8.0), 6: tone([880, 1760, 3520, 5000], 0.003, 0.15)}
cur = {"ch": None}


class Rec:
    def start(self):
        pass

    def stop(self):
        return sr, np.vstack([np.zeros((2400, 2)), SOUNDS[cur["ch"]]]).astype(np.float32)


FC.available = lambda: (True, "ok"); FC.LoopbackRecorder = Rec


def call(cmd, timeout=15.0, **a):
    if cmd == "play":
        cur["ch"] = a["channel"]
    return {}


m.BRIDGE.call = call; m.fl_running = lambda: True
m.time.sleep = lambda s: None
opened = []
m.fl_open = lambda p, save_current=True: opened.append(p)
m._open_project_guess = lambda: (os.path.join(tmp, "song.flp"), "song")
tpl = os.path.join(tmp, "tpl.flp"); open(tpl, "wb").write(b"x"); m._state({"selftest_template": tpl})
PRE = [r"C:\P\Instruments\Strings Section.fst", r"C:\P\Instruments\Soft Pad.fst", r"C:\P\Instruments\Glass Pluck.fst"]
m.fl_find_presets = lambda q, limit=40: {"presets": PRE}
nxt = iter([5, 6])


def add(a, b, c, preset=None):
    if "Strings" in preset:
        raise ValueError("this .fst has no plugin block (sample-based channel presets can't be loaded here)")
    return {"new_channel": next(nxt)}


m.flp_add_channel = add
old = os.path.join(coll, "_audition"); os.makedirs(os.path.join(old, "previews"))
open(os.path.join(old, "audition.flp"), "wb").write(b"x")
r = m.fl_audition(want="bright plucky short, not bassy", query="Strings", count=5)
names = [x["name"] for x in r["ranked"]]
assert names == ["Glass Pluck", "Soft Pad"], r["ranked"]
assert r["skipped"] and r["skipped"][0]["preset"] == "Strings Section", r
state_dir = os.path.dirname(m.STATE)
assert os.path.abspath(r["previews"]).startswith(os.path.abspath(state_dir)), r["previews"]
assert not os.path.abspath(r["previews"]).startswith(os.path.abspath(coll)), r["previews"]
assert all(os.path.isfile(x["preview"]) and x["preview"].startswith(state_dir) for x in r["ranked"])
assert opened[0].startswith(state_dir) and opened[0].endswith("audition.flp") and opened[-1].endswith("song.flp")
assert r.get("old_previews") and "_audition" in r["old_previews"], r      # (not Windows here: named, not deleted)
first = set(os.listdir(r["previews"]))
nxt = iter([5, 6])
r2 = m.fl_audition(want="soft pad", query="pad", count=5)                     # the next run replaces the last one
assert set(os.listdir(r2["previews"])) == first and len(first) == 2, os.listdir(r2["previews"])
assert [x["name"] for x in r2["ranked"]][0] == "Soft Pad", r2["ranked"]
m.flp_add_channel = lambda a, b, c, preset=None: (_ for _ in ()).throw(ValueError("no plugin block"))
try:
    m.fl_audition(want="pad", query="Strings")
    raise SystemExit("all presets unloadable must say so")
except FileNotFoundError as e:
    assert "Strings Section" in str(e), e
print("ok")
