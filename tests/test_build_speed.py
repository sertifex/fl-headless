"""Beat-build speed and sound taste: inside catalog.hold() the indexes are checked against their folders once (not
on every search), audio-feature measuring shares one time budget, the background warm-up fills the feature cache
so a build later reads it for free, liked sounds rank first and disliked ones last (said with flh_journal
sound_likes/sound_dislikes, or learned when a sample is swapped out). Run: python3 tests/test_build_speed.py"""
import os, sys, tempfile, wave
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
packs = os.path.join(tmp, "FL", "Data", "Patches", "Packs"); os.environ["FLH_FACTORY_PACKS"] = packs
for k in ("FLH_SPLICE_LIB", "FLH_FLEX_DIR", "FLH_FL_USERDATA"):
    os.environ[k] = os.path.join(tmp, "none_" + k)
os.environ["FLH_NO_WARM"] = "1"


def wav(path, f0, dur=0.4, noise=0.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    t = np.arange(int(44100 * dur)) / 44100.0
    x = np.sin(2 * np.pi * f0 * t) * np.exp(-t * 8) + noise * np.random.RandomState(1).randn(len(t)) * np.exp(-t * 20)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes((np.clip(x, -1, 1) * 30000).astype("<i2").tobytes())


for i in range(8):
    wav(os.path.join(packs, "Drums", "Kicks", f"Kick {i:02d}.wav"), 50 + i * 4)
    wav(os.path.join(packs, "Drums", "Snares", f"Snare {i:02d}.wav"), 200 + i * 10, noise=0.6)

import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_catalog as C  # noqa: E402
ns = vars(m)
flh_store.register(ns); C.register(ns)

# ---- inside hold(): the sample index is checked against its folders once for many searches
C.MEMO_S = 0.0                                       # (outside a hold every search would re-check)
n = {"sig": 0}
real_sig = C._samples_sig
C._samples_sig = lambda: (n.__setitem__("sig", n["sig"] + 1), real_sig())[1]
C._sample_catalog(True)
n["sig"] = 0
for _ in range(3):
    C.sample_search("kick", None, "lofi", limit=4, features=False)
assert n["sig"] == 3, n                                # no hold: every search walks the folders
n["sig"] = 0
with C.hold():
    for _ in range(5):
        C.sample_search("kick", None, "lofi", limit=4, features=False)
        C.sample_search("snare", None, "lofi", limit=4, features=False)
assert n["sig"] <= 1, n                               # one build: one check
C._samples_sig = real_sig

# ---- one feature budget for a build: past it, results rank by names (fast), never wait
C._SAMPLE_MEMO.pop("feat", None)
try:
    os.remove(C._feat_path())
except OSError:
    pass
with C.hold(feature_s=0):
    r = C.sample_search("kick", None, "lofi", limit=4)
assert "skipped" in r.get("note", "") and not any("dur" in x for x in r["results"]), r
# the warm-up measures what a plain lofi build ranks top; the build then reads features for free
w = C.warm_features(genres=["lofi"], gap_s=0)
assert w["measured"] >= 8 and not w["running"], w
assert os.path.isfile(C._feat_path())
with C.hold(feature_s=0):
    r = C.sample_search("kick", None, "lofi", limit=4)
assert "note" not in r and all("dur" in x for x in r["results"]), r
w2 = C.warm_features(genres=["lofi"], gap_s=0)
assert w2["measured"] == 0, w2                        # nothing new to measure: the second warm-up is free

# ---- numpy numbers from a decoder never break the results (PC: FL's compressed kicks left channels empty)
real_raw = C._sample_features_raw
C._sample_features_raw = lambda p: {k: (np.float64(v) if type(v) is float else np.int64(v) if type(v) is int else v)
                                     for k, v in real_raw(p).items()}
C._SAMPLE_MEMO.pop("feat", None)
try:
    os.remove(C._feat_path())
except OSError:
    pass
r = C.sample_search("kick", None, "lofi", limit=3)
assert r["results"] and all(type(x.get("dur")) is float for x in r["results"]), r
C._sample_features_raw = real_raw

# ---- taste: a disliked sound drops, a liked one rises; the newest word wins
top = C.sample_search("kick", None, "lofi", limit=8)["results"]
first, last = top[0]["path"], top[-1]["path"]
C.taste_note(dislike=[first], weight=2)
r = C.sample_search("kick", None, "lofi", limit=8)["results"]
assert r[0]["path"] != first and r[-1]["path"] == first, [x["name"] for x in r]
C.taste_note(like=[last], weight=2)
r = C.sample_search("kick", None, "lofi", limit=8)["results"]
assert r[0]["path"] == last, [x["name"] for x in r]
C.taste_note(like=[first], weight=2)                  # changed their mind
assert C._taste_key(first) not in C.taste_get()["dislike"]
assert C.taste_score("D:/other folder/" + os.path.basename(first)) > 0     # same file name, another folder

# ---- flh_journal: sound_dislikes by channel number -> remembered for every song
proj = os.path.join(roots, "Song.flp")
import shutil  # noqa: E402
shutil.copy(os.path.join(HERE, "corpus", "base.flp"), proj)
flh_store.journal_update(proj, {"channels": {"3": {"role": "snare", "name": "Snare", "sound": "D:/x/Snare 05.wav"}}})
j = flh_store.flh_journal(proj, add={"sound_dislikes": [3], "likes": "the groove"})
assert j["sound_taste"] == ["disliked snare 05"], j
assert "snare 05" in C.taste_get()["dislike"] and "the groove" in flh_store.journal_get(proj)["likes"]
j = flh_store.flh_journal(proj, add={"sound_likes": ["Snare"]})                  # by channel name
assert "snare 05" in C.taste_get()["like"] and "snare 05" not in C.taste_get()["dislike"], C.taste_get()

# ---- a sample swapped out of a sampler channel is remembered (soft) - the new one liked
try:
    import flh_newsong as NS
    NS.register(ns)
    a = os.path.join(packs, "Drums", "Snares", "Snare 01.wav"); b = os.path.join(packs, "Drums", "Snares", "Snare 02.wav")
    r = NS.flp_load_sample(proj, proj, a)
    ch = r["channel"]
    NS.flp_load_sample(proj, proj, b, channel=ch)
    t = C.taste_get()
    assert t["dislike"].get("snare 01") == 1 and t["like"].get("snare 02") == 1, t
finally:
    shutil.rmtree(tmp, True)
print("build speed ok")
