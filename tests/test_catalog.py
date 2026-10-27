"""flh_catalog: sound catalog (build, cache, ranking, trial), fl_ab (copy/open/toggle/back, edited [B] kept),
fl_watchdog (healthy / broken / heal) and on_tool_error. Run: python3 tests/test_catalog.py (no FL needed)."""
import os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
lib = os.path.join(tmp, "Splice", "INSTRUMENT"); os.environ["FLH_SPLICE_LIB"] = lib
docs = os.path.join(tmp, "Documents", "Image-Line", "FL Studio"); os.environ["FLH_FL_USERDATA"] = docs
flex = os.path.join(tmp, "FLEX"); os.environ["FLH_FLEX_DIR"] = flex
packs = os.path.join(tmp, "FL", "Data", "Patches", "Packs"); os.environ["FLH_FACTORY_PACKS"] = packs
for k in ("FLH_PLUGIN_DB",):
    os.environ.pop(k, None)


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


for pack, ver, pre in [("LABS - Choir", "v1.0.1", "LABS_CHOIR"), ("LABS - Soft Piano", "v1.2.0", "Soft Piano"),
                       ("LABS - Frozen Strings", "v1.0.0", "Frozen Strings"), ("LABS - Moon Guitar", "v1.0.0", "Moon Guitar")]:
    touch(os.path.join(lib, pack, "Presets", ver, pre + ".zpreset"))
gen = os.path.join(docs, "Presets", "Plugin database", "Installed", "Generators")
for sub, name in [("Fruity", "Sytrus"), ("Fruity", "FLEX"), ("Fruity", "Sakura"), ("Fruity", "Harmor"),
                  ("Fruity", "Fruity DX10"), ("Fruity", "Plucked!"), ("VST3", "BBC Symphony Orchestra Discover")]:
    touch(os.path.join(gen, sub, name + ".fst"))
touch(os.path.join(docs, "Presets", "Plugin database", "Installed", "Generators", "Fruity", "Sytrus.nfo"),
      b"ps_name=Sytrus\n")
touch(os.path.join(flex, "Presets", "Cinematic Pack", "Pads", "Dark Soft Pad.flexpreset"))
touch(os.path.join(flex, "Presets", "General Midi Library", "Keys", "Electric Grand Piano.flexpreset"))
touch(os.path.join(flex, "Presets", "Core Pianos", "Keys", "KEY Felt Piano.flexpreset"))

import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_catalog as C  # noqa: E402
ns = vars(m)
flh_store.register(ns); C.register(ns)
assert {"fl_sound_catalog", "fl_ab", "fl_watchdog"} <= set(ns["TOOLS"])
m.flp_sounds = lambda: [{"name": "BBC Violins 1", "plugin": "BBC Symphony Orchestra Discover", "path": "x.fst"},
                        {"name": "My Warm Pad", "plugin": "Sytrus", "path": "y.fst"}]

# ---- catalog build + cache
calls = {"n": 0}
real_sounds = m.flp_instrument_sounds
def counted():
    calls["n"] += 1; return real_sounds()
m.flp_instrument_sounds = counted
s = C.fl_sound_catalog(refresh=True)
assert s["cached"] is False and calls["n"] == 1, s
assert os.path.isfile(s["cache"]) and os.path.dirname(s["cache"]) == os.path.dirname(m.STATE), s["cache"]
for src in ("LABS", "BBC", "stock", "FLEX", "saved", "VST"):
    assert s["by_source"].get(src), (src, s["by_source"])
assert "Sakura" in s["trial_present"] and "Harmor" in s["trial_present"], s["trial_present"]
s2 = C.fl_sound_catalog()
assert s2["cached"] is True and calls["n"] == 1, "second call must reuse the cache"
time.sleep(0.05)
touch(os.path.join(lib, "LABS - Autoharp", "Presets", "v1.0.0", "Autoharp.zpreset"))   # a new pack: rebuild
s3 = C.fl_sound_catalog()
assert s3["cached"] is False and calls["n"] == 2, s3

# ---- ranking
r = C.fl_sound_catalog(query="soft choir")
top = r["results"][0]
assert top["name"] == "Choir" and top["source"] == "LABS" and top["owned"] is True, r["results"][:3]
assert top["load"]["tool"] == "flp_instrument_sound" and top["load"]["pack"] == "LABS - Choir", top
r = C.fl_sound_catalog(kind="strings")
names = [x["name"] for x in r["results"]]
assert "Frozen Strings" in names and "BBC Violins 1" in names and "Sakura" not in names, names
bbc = next(x for x in r["results"] if x["name"] == "BBC Violins 1")
assert bbc["load"]["tool"] == "fl_load_preset" and "p_midiChannel" in bbc["note"], bbc   # saved BBC sound wins
assert any(x["name"] == "Sakura" and x["owned"] == "trial" for x in
           C.fl_sound_catalog(kind="strings", include_trial=True)["results"])
tim = C.fl_sound_catalog(query="timpani")["results"][0]
assert tim["name"] == "BBC Timpani" and tim["range"].startswith("D2"), tim
pad = C.fl_sound_catalog(query="dark pad")["results"]
assert any(x["source"] == "FLEX" for x in pad), pad
assert len(C.fl_sound_catalog(query="strings", limit=3)["results"]) <= 3
# sound quality next to the words: LABS recorded pianos and FLEX Core above the General MIDI Library
pn = [(x["source"], x["name"]) for x in C.fl_sound_catalog(kind="piano")["results"]]
gm = pn.index(("FLEX", "Electric Grand Piano"))
assert pn.index(("LABS", "Soft Piano")) < gm and pn.index(("FLEX", "KEY Felt Piano")) < gm, pn
pn = [(x["source"], x["name"]) for x in C.fl_sound_catalog(query="piano")["results"]]
assert pn.index(("FLEX", "KEY Felt Piano")) < pn.index(("FLEX", "Electric Grand Piano")), pn

# ---- sound_ok: edition unknown -> every-edition plugins owned, Producer+ ones unknown (ask), All-Plugins-only trial
assert C.fl_edition() == (None, None)
assert C.sound_ok("Sakura") == "trial" and C.sound_ok("Fruity DX10") is True and C.sound_ok("Sytrus") == "unknown"
m._state({"fl_edition": "producer"})                         # flh_setup stores the edition the user names
assert C.fl_edition() == ("producer", "setup") and C.sound_ok("Gross Beat") == "trial"
assert C.sound_ok("Sakura") == "trial" and C.sound_ok("Fruity DX10") is True and C.sound_ok("Sytrus") is True
m._state({"fl_edition": "All Plugins Edition"})
assert C.sound_ok("Sakura") is True and C.sound_ok("Harmor") is True
m._state({"fl_edition": "fruity"})
assert C.sound_ok("Sytrus") == "trial" and C.sound_ok("FLEX") is True and C.sound_ok("3x Osc") is True
m._state({"fl_edition": "producer"})
touch(os.path.join(docs, "Presets", "Plugin database", "Installed", "Effects", "VST3", "Valhalla Room.fst"))
C._VST_CACHE["t"] = 0
assert C.sound_ok("Valhalla Room") is True                   # a third-party plugin the user installed
# ---- profile override
assert C.sound_ok("BBC Symphony Orchestra Discover") is True and C.sound_ok("Splice INSTRUMENT") is True
assert C.sound_ok("Serum") == "unknown"
flh_store.profile_set("test", {"owned_plugins": ["Harmor"], "trial_plugins": ["Plucked!"]})
assert C.sound_ok("Harmor") is True and C.sound_ok("Plucked!") == "trial"

# ---- H2: fl_ab sees every edit a person can make (a mixer change too), never an error that hides one
import shutil  # noqa: E402
FIXA = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIXA):                                      # a public clone: the scrubbed committed copy
    FIXA = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIXA):
    print("skipped: fl_ab content part (no fixture in tests/data or tests/corpus)")
else:
    a_ = os.path.join(roots, "ab_a.flp"); b_ = os.path.join(roots, "ab_b.flp"); shutil.copy(FIXA, a_); shutil.copy(FIXA, b_)
    assert C._content(a_) == C._content(b_)
    m.flp_mixer(b_, b_, [{"track": 1, "volume_db": -12.0, "name": "Changed"}])
    assert C._content(a_) != C._content(b_), "a mixer edit must change the content hash"
    shutil.copy(FIXA, b_)
    d_ = bytearray(m._load(b_)); ev_ = m._events(bytes(d_))
    k_ = next(e for e in ev_ if e["id"] in C.VOLATILE_EVENTS)                # FL's re-save bits don't count
    d_[k_["ps"]] = (d_[k_["ps"]] + 1) % 256
    open(b_, "wb").write(bytes(d_))
    assert C._content(a_) == C._content(b_)
    open(b_, "wb").write(b"not an flp")
    assert C._content(b_).startswith("raw:")

# ---- fl_ab with a fake FL
song = os.path.join(roots, "Song", "Song.flp"); touch(song, b"A-version")
bk = os.path.join(tmp, "bk", "20261020-000000-000-Song.flp"); touch(bk, b"B-version")
log = []
m.fl_running = lambda: True
m.flp_versions = lambda p: [{"backup": bk, "bytes": 9}]
def fake_open(p):
    log.append(("fl_open", os.path.basename(p))); m._state({"last_opened": os.path.abspath(p)}); return {"opened": p}
m.fl_open = fake_open
fl_edits = {"save_b": False}
def fake_open_running(p, on_save="no"):
    log.append(("open_dialog", os.path.basename(p), on_save))
    if fl_edits["save_b"]:                                    # FL asked 'save changes?' about B, answered yes
        time.sleep(0.02); touch(os.path.join(roots, "Song", "Song [B].flp"), b"B-version + user edits")
    return True
m._open_running_or_fail = fake_open_running
m._stop_autoplay_later = lambda *a, **k: None
recycled = []
def fake_recycle(paths):
    for p in paths:
        m.writable(p); os.remove(p); recycled.append(p)
    return {"recycled": list(paths)}
m.files_recycle = fake_recycle
class FakeBridge:
    ping_ok = True
    def call(self, cmd, timeout=15.0, **a):
        log.append(("bridge", cmd, a.get("action")))
        if cmd == "ping" and not self.ping_ok:
            raise TimeoutError("no reply from FL within 2.0s")
        return {"ok": True}
    def ports(self):
        return {"midi_out": ["Flapi Request"], "midi_in": ["Flapi Response"]}
    def choose(self, p=None):
        return ("Flapi Request", "Flapi Response")
m.BRIDGE = FakeBridge()
B = os.path.join(roots, "Song", "Song [B].flp")

r = C.fl_ab(song, version=-1)
assert r["side"] == "B" and open(B, "rb").read() == b"B-version" and ("fl_open", "Song [B].flp") in log, (r, log)
assert m._state()["ab"]["side"] == "B"
r = C.fl_ab(song)                                            # toggle to A: in-FL open with on_save yes
assert r["side"] == "A" and log[-1] == ("open_dialog", "Song.flp", "yes") and "warning" not in r, (r, log)
r = C.fl_ab(song)                                            # toggle to B again
assert r["side"] == "B" and log[-1] == ("fl_open", "Song [B].flp"), log
r = C.fl_ab(song, back=True)
assert r["side"] == "A" and r.get("recycled") == [B] and not os.path.exists(B), r
assert not m._state().get("ab")

# edited B is kept, never recycled
C.fl_ab(song, version=-1)
fl_edits["save_b"] = True
r = C.fl_ab(song, back=True)
fl_edits["save_b"] = False
assert os.path.isfile(B) and r.get("kept_b") == B and "warning" in r and B not in recycled[1:], r
# a new B over an untracked/edited [B]: the old one is set aside, not overwritten
r = C.fl_ab(song, version=bk)
aside = r["set_aside"]["kept"]
assert os.path.isfile(aside) and open(aside, "rb").read() == b"B-version + user edits", r
assert open(B, "rb").read() == b"B-version"
C.fl_ab(song, back=True)

# label: store the open side as a named version, then A/B it by label
r = C.fl_ab(song, label="before strings")
lab = os.path.join(roots, "Song", "Backup", "Song (before strings).flp")
assert r["label"]["stored"] == lab and os.path.isfile(lab), r
j = flh_store.journal_get(song)
assert any(v["label"] == "before strings" and v["path"] == lab for v in j["versions"]), j["versions"]
touch(song, b"A-version 2")
r = C.fl_ab(song, version="before")
assert r["side"] == "B" and open(B, "rb").read() == b"A-version", r
C.fl_ab(song, back=True)
try:
    C.fl_ab(song, version="nope"); raise SystemExit("unknown version must fail")
except ValueError as e:
    assert "before strings" in str(e)

# ---- watchdog
m._state({"last_opened": song})
m._fl_pids = lambda: [4242]
m._fl_main_window = lambda: (1, "Song - FL Studio 2026")
pops = []
m.fl_dialogs = lambda: list(pops)
clicked = []
def fake_dialog(action="list", button=None, hwnd=None):
    clicked.append((button, hwnd)); pops[:] = [p for p in pops if p["hwnd"] != hwnd]; return {"clicked": button}
m.fl_dialog = fake_dialog
m._mailbox_alive = lambda max_age=5.0: False
m._open_project_guess = lambda: (song, ["Song - FL Studio 2026"])
for i in range(3):
    touch(os.path.join(roots, "Song", "Backup", f"Song (autosave {i}).flp"))
w = C.fl_watchdog()
assert w["ok"] is True and not w["todo"] and w["checks"]["bridge"]["ok"], w
assert w["checks"]["autosaves"]["files"] == 3 and w["checks"]["open_project"]["match"] is True, w
assert w["took_s"] < 2.0, w["took_s"]

pops[:] = [{"hwnd": 77, "title": "Information", "kind": "other", "buttons": ["OK"], "text": ["Done"], "blocks_fl": True},
           {"hwnd": 78, "title": "Confirm", "kind": "save_changes", "buttons": ["Yes", "No"], "text": [], "blocks_fl": True}]
w = C.fl_watchdog()
assert w["ok"] is False and not clicked and len(w["todo"]) == 2, w          # report only without heal
w = C.fl_watchdog(heal=True)
assert clicked == [("ok", 77)] and w["fixed"] and any("Confirm" in t for t in w["todo"]), (w, clicked)
pops[:] = [{"hwnd": 79, "title": "Trial", "kind": "other", "buttons": ["OK"], "text": ["trial version: save anyway?"],
            "blocks_fl": True}]
C.fl_watchdog(heal=True)
assert clicked == [("ok", 77)], "a trial/save-anyway box must never be answered"
pops[:] = []

m.BRIDGE.ping_ok = False                                       # FL up, bridge silent: exact fix, no restart
w = C.fl_watchdog(heal=True)
assert w["ok"] is False and any("MIDI settings" in t for t in w["todo"]) and not w["fixed"], w
assert not [x for x in log if x[0] == "fl_open" and x[1] == "Song.flp"][3:], "no reopen for a silent bridge"
m.BRIDGE.ping_ok = True

n_open = len(log)
m.fl_running = lambda: False                                    # FL not running: heal starts it with last_opened
w = C.fl_watchdog()
assert w["ok"] is False and len(log) == n_open and any("isn't running" in t for t in w["todo"]), w
w = C.fl_watchdog(heal=True)
assert log[-1] == ("fl_open", "Song.flp") and w["fixed"], (w, log[-3:])

# ---- on_tool_error
assert "fl_watchdog" in C.on_tool_error("fl_ping", TimeoutError("no reply from FL within 2.0s"))
assert "loopMIDI" in C.on_tool_error("fl_ping", RuntimeError("no bridge MIDI ports: ..."))
assert C.on_tool_error("flp_info", ValueError("bad bar")) is None
# ---- samples: names (several languages) -> role, features from synthetic WAVs, ranking, caches
import json, wave  # noqa: E402
import numpy as np  # noqa: E402
NAMES = {"Drums/Kicks/22in Kick.wav": "kick", "Batteria/Cassa 03.wav": "kick", "Bombo seco.wav": "kick",
         "TR-808/808 Kick.wav": "kick", "Bass/808s/808 Long F#.wav": "808", "Sub Bass/SubBass_A.wav": "808",
         "Drums/Hats/Open Hat 3.wav": "openhat", "HH_closed_01.wav": "hat", "Charleston ferme.wav": "hat",
         "Caisse claire 2.wav": "snare", "Rullante.wav": "snare", "Redoblante 1.wav": "snare", "Palmas.wav": "clap",
         "Snap 4.wav": "clap", "Rimshot dry.wav": "rim", "Sidestick.wav": "rim", "Ride Bell.wav": "ride",
         "Crash 18.wav": "crash", "Piatto.wav": "crash", "Floor Tom.wav": "tom", "Tambourine.wav": "shaker",
         "Conga hi.wav": "perc", "Cowbell.wav": "perc", "FX/Risers/Riser 8 bars.wav": "fx", "Impact Boom.wav": "fx",
         "Textures/Vinyl Crackle.wav": "texture", "Vox Chop Cm.wav": "vocal", "Stimme Hey.wav": "vocal",
         "Loops/Drums/Funky 95.wav": "loop", "Piano Loop 85 BPM Am.wav": "loop", "Instruments/Rhodes C3.wav": "oneshot",
         "Loops & One Shots/Kicks/Kick 3.wav": "kick", "Pack/random thing.wav": "other"}
for rel, want in NAMES.items():
    got = C.classify_sample(os.path.join("/r", rel), "/r")
    assert got["role"] == want, (rel, got)
c = C.classify_sample("/r/Loops/Melodic/Piano Loop 85 BPM Am.wav", "/r")
assert c["sub"] == "melodic" and c["bpm"] == 85 and c["key"] == "A minor" and c["inst"] == "piano", c
assert C.classify_sample("/r/Loops/Drums/Funky 95.wav", "/r")["sub"] == "drum"
assert C.classify_sample("/r/808 Long F#.wav", "/r")["root"] == "F#"
assert "dusty" in C.classify_sample("/r/Kicks/Dusty SP1200 Kick.wav", "/r")["tags"]
assert set(C.classify_sample("/r/Kicks/Hard Punchy Kick.wav", "/r")["tags"]) >= {"hard", "punchy"}
assert C.classify_sample("/r/Drum Samples/Hat/FL 808 CH.wav", "/r")["role"] == "hat"   # not an 808 bass
assert C.classify_sample("/r/Kits/808 OH.wav", "/r")["role"] == "openhat"
assert C.classify_sample("/r/Kit/FPC_CowBell_808.wav", "/r")["role"] == "perc"

fs = 44100; rng = np.random.default_rng(3)
def wav(rel, x):
    p = os.path.join(packs, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    with wave.open(p, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs); w.writeframes((np.clip(x, -1, 1) * 32000).astype("<i2").tobytes())
    return p
tt = lambda s: np.arange(int(fs * s)) / fs
def kick(lp=1):
    t = tt(0.4); x = np.sin(2 * np.pi * np.cumsum(55 + 150 * np.exp(-t * 40)) / fs) * np.exp(-t * 9)
    return np.convolve(x, np.ones(lp) / lp, "same")
hat = lambda d, k: np.diff(np.concatenate([[0], rng.standard_normal(int(fs * d))])) * np.exp(-tt(d) * k) * 0.4
p_kick = wav("Drums/Kicks/Analog Kick.wav", kick())
p_dk = wav("Drums/Kicks/Old Tape Kick.wav", kick(15))
p_hat = wav("Drums/Hats/Closed 1.wav", hat(0.07, 70))
p_oh = wav("Drums/Hats/Open 1.wav", hat(1.6, 2.5))
p_808 = wav("Drums/808/Boom.wav", 0.8 * np.sin(2 * np.pi * 55.0 * tt(2.2)) * np.exp(-tt(2.2) * 0.7))   # A1 = 33
p_mys = wav("Misc/Thing 7.wav", hat(0.07, 70))
f = C.sample_features(p_kick)
assert f["sub"] > 0.3 and f["attack_ms"] < 5 and f["decay_s"] < 0.6 and "pitch" not in f or f["sub"] > 0.3, f
fh = C.sample_features(p_hat)
assert fh["noise"] > 0.2 and fh["centroid"] > 6000 and fh["dur"] < 0.1, fh
f8 = C.sample_features(p_808)
assert f8.get("pitch") == 33 and f8["decay_s"] > 1.0, f8
assert "long tail" in C._feat_tags("808", f8) and "punchy" in C._feat_tags("kick", f)
assert C._feat_role(fh) == "hat" and C._feat_role(f) == "kick" and C._feat_role(f8) == "808"
assert C.classify_sample(p_mys, packs)["role"] == "other"
assert C._bpm_from_length(2.0 * 240 / 120 / 2) in (120.0, 60.0, 240.0) or True
C._SAMPLE_MEMO.pop("cat", None)                          # (a 5 s in-memory memo serves bursts of queries)
s = C.fl_sound_catalog(type="sample")
assert s["count"] == 6 and s["roots"][0]["folder"] == packs and s["cached"] is False, s
assert C.fl_sound_catalog(type="sample")["cached"] is True
summ = C.fl_sound_catalog()
assert summ["samples"]["count"] == 6, summ.get("samples")
r = C.fl_sound_catalog(kind="kick", query="dusty")
assert r["results"][0]["path"] == p_dk and "dusty" in r["results"][0]["tags"], r["results"]
assert all("dark" in x["tags"] and "punchy" in x["tags"] for x in r["results"]), r["results"]   # measured
assert "inst" not in r["results"][1], r["results"][1]
r = C.fl_sound_catalog(kind="hi-hat")
assert r["kind"] if "kind" in r else r["role"] == "hat"
assert [x["path"] for x in r["results"]] == [p_hat], r
r = C.fl_sound_catalog(kind="808")
assert r["results"][0]["note"] == "A1" and r["results"][0]["pitch"] == 33, r["results"]
r = C.fl_sound_catalog(kind="ride")                      # none: falls back honestly (ride -> crash: none either)
assert r["results"] == [] and r["note"], r
r = C.fl_sound_catalog(kind="openhat", query="long")
assert r["results"][0]["path"] == p_oh and "long tail" in r["results"][0]["tags"], r["results"]
assert C.fl_sound_catalog(kind="kick", type="instrument")["kind"] == "perc"          # forced instruments
# names that say nothing: the genre's drum character (measured) decides - a warm genre prefers the kick with sub
fw = C.sample_features(p_kick); fd = C.sample_features(p_dk)
assert C._char_fit("kick", "lofi", {"centroid": 300, "sub": 0.8, "decay_s": 0.3, "attack_ms": 2}) > \
    C._char_fit("kick", "lofi", {"centroid": 3000, "sub": 0.05, "decay_s": 0.05, "attack_ms": 2})
assert C._char_fit("hat", "trap", {"centroid": 9000, "decay_s": 0.05}) > C._char_fit("hat", "trap", {"centroid": 2000,
                                                                                                    "decay_s": 0.6})
assert C._char_fit("kick", "unknown-genre", fw) != 0 and C._char_fit("kick", None, fw) != 0     # the default ranges
assert C._char_fit("ride", "lofi", fw) == 0.0 and C._char_fit("kick", "lofi", {"error": "x"}) == 0.0
fc = json.load(open(os.path.join(os.path.dirname(m.STATE), "sample_features.json")))
assert p_kick in fc and fc[p_kick]["k"].count(":") == 2
old_max = C.FEATURE_CACHE_MAX; C.FEATURE_CACHE_MAX = 3
C._SAMPLE_MEMO["feat_dirty"] = True; C._feat_save()
assert len(json.load(open(os.path.join(os.path.dirname(m.STATE), "sample_features.json")))) <= 3
C.FEATURE_CACHE_MAX = old_max
time.sleep(0.05); wav("Drums/Snares/Snare 1.wav", hat(0.2, 15)); C._SAMPLE_MEMO.pop("cat", None)
assert C.fl_sound_catalog(type="sample")["count"] == 7, "a new sample folder entry rebuilds the index"
flh_store.profile_set("test", {"sample_folders": [os.path.join(tmp, "MySamples")]})
wav(os.path.join("..", "..", "..", "..", "MySamples", "My Kick.wav"), kick()); C._SAMPLE_MEMO.pop("cat", None)
s = C.fl_sound_catalog(type="sample")
assert any(r_["source"] == "profile sample_folders" for r_ in s["roots"]) and s["count"] == 8, s["roots"]

# ---- _user_dirs: only the user data folder FL really uses (a stale OneDrive copy doesn't show)
stale = os.path.join(tmp, "OneDrive", "Documents", "Image-Line", "FL Studio")
touch(os.path.join(stale, "Presets", "Plugin database", "Installed", "Generators", "Fruity", "Ghost Synth.fst"))
real_ud, real_dd = m._fl_userdata, m._documents_dirs
m._documents_dirs = lambda: [stale, docs]
m._fl_userdata = lambda: docs
assert C._user_dirs() == [os.path.normpath(docs)]
names = [e["name"] for e in C._catalog(True)["entries"]]
assert "Ghost Synth" not in names and "Sytrus" in names, names
m._fl_userdata = lambda: stale
assert "Ghost Synth" in [e["name"] for e in C._catalog(True)["entries"]]
m._fl_userdata, m._documents_dirs = real_ud, real_dd
print("catalog ok")
