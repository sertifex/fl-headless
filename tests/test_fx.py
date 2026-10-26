"""flh_fx: tempo-exact synthesized transition FX - exact lengths, the shape of each kind (riser builds and peaks at the
end, downlifter mirrors it, impact front-loaded, sub_drop falls an octave from the key root, reverse_crash from a
given cymbal file ends loud, noise_sweep peaks in the middle, cymbal_swell / reverse_swell rise into the end, boom is
deep and front-loaded, the wind / rain / vinyl beds are steady, dark and loop seamlessly), no clipping, determinism,
the name/params cache, and
the fl_make_fx tool on tests/corpus/base.flp (tempo from the project, key from the journal, files in <song>/_fx).
Run: python3 tests/test_fx.py (no FL needed)."""
import os, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local"); os.environ["FLH_NO_WARM"] = "1"
import numpy as np  # noqa: E402

import fl_headless_mcp as m  # noqa: E402
import fl_audio_edit as AE  # noqa: E402
import flh_store, flh_fx as FX  # noqa: E402
ns = vars(m)
for mod in (flh_store, FX):
    mod.register(ns)
assert "fl_make_fx" in ns["TOOLS"] and ns["make_fx"] is FX.make_fx and ns["FX_KINDS"] is FX.FX_KINDS
assert "flh_fx" in m.EXTENSIONS
assert ns["TOOLS"]["fl_make_fx"][2]["type"] == "object" and "kind" in ns["TOOLS"]["fl_make_fx"][2]["required"]
assert len(ns["SHORT_DESC"]["fl_make_fx"]) <= 250
for k, v in FX.FX_KINDS.items():
    assert v["ends_on"] in ("downbeat", "after", "loop") and v["default_beats"] > 0 and v["about"], k
    assert (v["ends_on"] == "loop") == (k in FX.TEXTURES), k                # only the texture beds loop

OUT = os.path.join(roots, "fx")
FS = 44100


def load(p):
    x, r, bits = AE.load(p)
    assert r == FS and x.shape[1] == 2 and bits in (16, 24), (r, x.shape, bits)
    return x


def rms_db(a):
    return 20 * np.log10(np.sqrt(np.mean(a ** 2)) + 1e-12)


def quarters(x):
    q = len(x) // 4
    return [rms_db(x[i * q:(i + 1) * q]) for i in range(4)]


def env_argmax(x, win=2048):
    e = np.sqrt(np.convolve(np.mean(x ** 2, 1), np.ones(win) / win, "same"))
    return int(np.argmax(e)) / len(x)


def zcr(a):
    """~ frequency of a sine: rising zero crossings over the time they span."""
    up = np.nonzero((a[:-1] < 0) & (a[1:] >= 0))[0]
    return (len(up) - 1) * FS / (up[-1] - up[0]) if len(up) > 2 else 0.0


# ---------------------------------------------------------------- exact lengths, peaks, no clipping
for kind in FX.FX_KINDS:
    for bpm, beats in ((140, 16), (87.5, 3), (174, 7.5)):
        t = time.time()
        r = FX.make_fx(kind, bpm, beats, OUT, key="F minor")
        took = time.time() - t
        x = load(r["path"])
        n = round(beats * 60 / bpm * FS)
        assert len(x) == n, (kind, bpm, beats, len(x), n)
        assert abs(r["seconds"] - n / FS) < 1e-5 and r["beats"] == beats and r["kind"] == kind
        assert r["ends_on"] == FX.FX_KINDS[kind]["ends_on"]
        assert os.path.basename(r["path"]) == f"{FX._NAMES[kind]} {beats:g} beats {bpm:g} bpm.wav", r["path"]
        pk = 20 * np.log10(np.max(np.abs(x)))
        assert -1.6 < pk <= -0.9, (kind, pk)
        if kind in FX.TEXTURES:                                              # a seamless loop: the last sample
            step = np.median(np.abs(np.diff(x, axis=0)))                     # runs into the first like any other
            assert np.abs(x[0] - x[-1]).max() < 12 * step + 1e-4, (kind, x[0], x[-1], step)
            assert r.get("loop") is True
        else:
            assert abs(x[0]).max() < 0.05 and abs(x[-1]).max() < 0.2, (kind, x[0], x[-1])   # faded edges: no clicks
        assert np.isfinite(x).all()
        if kind == "riser" and bpm == 140:
            assert took < 1.0, took                                          # CPU light
print("lengths ok")

# ---------------------------------------------------------------- shapes
r = FX.make_fx("riser", 140, 16, OUT, key="A minor")
x = load(r["path"]); q = quarters(x)
assert q[3] - q[0] > 6, q
assert env_argmax(x) > 0.9, env_argmax(x)
assert abs(r["peak_at_s"] - r["seconds"]) < 1e-3 and r["start_offset_beats"] == -16
lr = np.corrcoef(x[:, 0], x[:, 1])[0, 1]
assert 0.2 < lr < 0.98, lr                                                   # some stereo width, not out of phase

r = FX.make_fx("downlifter", 140, 8, OUT, key="A minor")
x = load(r["path"]); q = quarters(x)
assert q[0] - q[3] > 6, q
assert env_argmax(x) < 0.1 and r["peak_at_s"] == 0 and r["ends_on"] == "after" and r["start_offset_beats"] == 0

r = FX.make_fx("impact", 120, 8, OUT, key="A minor")
x = load(r["path"]); q = quarters(x)
assert q[0] > q[1] > q[2] > q[3] and q[0] - q[3] > 10, q
assert env_argmax(x) < 0.1 and r["peak_at_s"] < 0.1
lo = np.abs(np.fft.rfft(x[:FS // 2, 0])); f = np.fft.rfftfreq(FS // 2, 1 / FS)
assert (lo[f < 150] ** 2).sum() > (lo[f >= 150] ** 2).sum(), "the boom is low"

r = FX.make_fx("sub_drop", 140, 4, OUT, key="F minor")                     # F2 = 87.3 Hz -> F1 43.7 Hz
x = load(r["path"])[:, 0]; th = len(x) // 3
z0, z1 = zcr(x[:th]), zcr(x[-th:])
assert z1 < z0 * 0.8, (z0, z1)
f_start = zcr(x[:int(0.06 * FS)])
assert abs(f_start - 87.3) < 87.3 * 0.12, f_start
assert np.allclose(load(r["path"])[:, 0], load(r["path"])[:, 1])            # the sub is mono
x = load(FX.make_fx("sub_drop", 140, 4, OUT, key="Ab major")["path"])[:, 0]
assert abs(zcr(x[:int(0.06 * FS)]) - 103.8) < 103.8 * 0.12
x = load(FX.make_fx("sub_drop", 140, 4, OUT)["path"])[:, 0]               # default key A: A2 = 110 Hz
assert abs(zcr(x[:int(0.06 * FS)]) - 110) < 110 * 0.12

r = FX.make_fx("noise_sweep", 128, 8, OUT)
x = load(r["path"]); q = quarters(x)
assert q[1] > q[0] + 6 and q[2] > q[3] + 6, q
assert 0.4 < env_argmax(x) < 0.6 and abs(r["peak_at_s"] - r["seconds"] / 2) < 1e-6 and r["start_offset_beats"] == -4

r = FX.make_fx("tape_stop", 100, 2, OUT, key="C minor")
x = load(r["path"]); q = quarters(x)
assert q[0] > q[3] + 6 and r["ends_on"] == "downbeat", q

r = FX.make_fx("reverse_crash", 140, 4, OUT)                               # synthesized swell
x = load(r["path"]); q = quarters(x)
assert q[3] - q[0] > 12 and env_argmax(x) > 0.85, q

# a provided cymbal: bright noise, sharp attack, 3 s decay, a little silence before the hit (trimmed)
rng = np.random.default_rng(7)
nn = 3 * FS; tt = np.arange(nn) / FS
cym = rng.standard_normal((nn, 2)) * np.exp(-tt * 2.0)[:, None] * 0.5
cym = np.concatenate([np.zeros((2000, 2)), cym])
smp_dir = os.path.join(tmp, "samples"); os.makedirs(smp_dir)
crash = os.path.join(smp_dir, "crash.wav"); AE.save(crash, cym, FS, bits=16)
for beats in (4, 16):                                                       # shorter (trimmed) and longer (padded)
    r = FX.make_fx("reverse_crash", 140, beats, OUT, sample=crash)
    x = load(r["path"]); n = round(beats * 60 / 140 * FS)
    assert len(x) == n
    tail = rms_db(x[-int(0.05 * FS):])
    assert tail > rms_db(x) + 3 and tail > -12, (tail, rms_db(x))
    assert env_argmax(x) > 0.95 and r["ends_on"] == "downbeat"
# the soft kinds: cymbal_swell rises into the downbeat, boom is low and front-loaded, reverse_swell sucks in (loudest
# at the end, tonal: most energy at the triad's notes), the texture beds are steady, dark and loop seamlessly
r = FX.make_fx("cymbal_swell", 120, 8, OUT)
x = load(r["path"]); q = quarters(x)
assert q[3] - q[0] > 12 and env_argmax(x) > 0.85 and r["ends_on"] == "downbeat", q
r = FX.make_fx("boom", 120, 8, OUT, key="A minor")
x = load(r["path"]); q = quarters(x)
assert q[0] > q[1] > q[3] and env_argmax(x) < 0.1 and r["ends_on"] == "after", q
lo = np.abs(np.fft.rfft(x[:FS // 2, 0])); f = np.fft.rfftfreq(FS // 2, 1 / FS)
assert (lo[f < 120] ** 2).sum() > 3 * (lo[f >= 120] ** 2).sum(), "the boom is deep"
for key in ("A minor", "A major", None):
    r = FX.make_fx("reverse_swell", 100, 8, OUT, key=key)
    x = load(r["path"]); q = quarters(x)
    assert q[3] - q[0] > 9 and env_argmax(x) > 0.8 and r["ends_on"] == "downbeat", (key, q)
r = FX.make_fx("reverse_swell", 100, 8, OUT, key="A minor", sample=crash)
assert r["ends_on"] == "downbeat" and len(load(r["path"])) == round(8 * 60 / 100 * FS)
assert FX.key_third("A minor") == 3 and FX.key_third("C major") == 4 and FX.key_third(None) is None
for kind in FX.TEXTURES:
    r = FX.make_fx(kind, 90, 16, OUT)
    x = load(r["path"]); q = quarters(x)
    assert max(q) - min(q) < 9, (kind, q)                                   # a bed: no swell (wind gusts a little)
    sp = np.abs(np.fft.rfft(x[:, 0])) ** 2; f = np.fft.rfftfreq(len(x), 1 / FS)
    assert sp[f > 8000].sum() < 0.05 * sp.sum(), kind                        # no harsh highs
    j = np.concatenate([x[-2048:], x[:2048]])                               # across the loop point: no jump
    assert rms_db(np.diff(j, axis=0)) < rms_db(np.diff(x, axis=0)) + 3, kind
    assert r["loop"] and r["ends_on"] == "loop"
assert FX.kind_of("vinyl crackle") == "vinyl" and FX.kind_of("boom") == "boom" and FX.kind_of("swell") == "cymbal_swell"
print("shapes ok")

# ---------------------------------------------------------------- determinism + cache
d1, d2 = os.path.join(roots, "det1"), os.path.join(roots, "det2")
a = FX.make_fx("riser", 150, 8, d1, key="D minor", seed=3)
b = FX.make_fx("riser", 150, 8, d2, key="D minor", seed=3)
assert open(a["path"], "rb").read() == open(b["path"], "rb").read()
c = FX.make_fx("riser", 150, 8, d2, key="D minor", seed=4)                  # another take: same name taken -> suffix
assert c["path"] != b["path"] and os.path.isfile(b["path"]) and os.path.isfile(c["path"])
assert open(c["path"], "rb").read() != open(b["path"], "rb").read()
mt = os.path.getmtime(a["path"]); time.sleep(0.05)
a2 = FX.make_fx("riser", 150, 8, d1, key="D minor", seed=3)
assert a2["cached"] and a2["path"] == a["path"] and os.path.getmtime(a["path"]) == mt
c2 = FX.make_fx("riser", 150, 8, d2, key="D minor", seed=4)
assert c2["cached"] and c2["path"] == c["path"]
assert FX.make_fx("riser", 150, 8, d2, key="D minor", seed=3)["cached"]
n_files = len([f for f in os.listdir(d2) if f.endswith(".wav")])
assert n_files == 2, os.listdir(d2)
assert not [f for f in os.listdir(d2) if f.endswith(".tmp")]
# noise_sweep / reverse_crash ignore the key: one file for any key
s1 = FX.make_fx("noise_sweep", 150, 4, d1, key="C major"); s2 = FX.make_fx("noise_sweep", 150, 4, d1, key="E minor")
assert s2["cached"] and s1["path"] == s2["path"]
print("cache ok")

# ---------------------------------------------------------------- errors + safety
for bad in (dict(kind="laser", bpm=120, beats=4), dict(kind="riser", bpm=0, beats=4),
            dict(kind="riser", bpm=120, beats=-1), dict(kind="riser", bpm=60, beats=500)):
    try:
        FX.make_fx(out_dir=OUT, **bad); raise AssertionError(bad)
    except ValueError:
        pass
assert FX.kind_of("Reverse-Crash") == "reverse_crash" and FX.kind_of("uplifter") == "riser"
try:
    FX.make_fx("riser", 120, 4, os.path.join(tmp, "outside"))               # outside FLH_ROOTS
    raise AssertionError("wrote outside FLH_ROOTS")
except PermissionError:
    pass
assert not os.path.exists(os.path.join(tmp, "outside", "Riser 4 beats 120 bpm.wav"))
print("safety ok")

# ---------------------------------------------------------------- the tool on a corpus project
song_dir = os.path.join(roots, "My Song"); os.makedirs(song_dir)
proj = os.path.join(song_dir, "My Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), proj)
tempo = float(m.flp_info(proj)["tempo_bpm"])
tool = ns["TOOLS"]["fl_make_fx"][0]
before = open(proj, "rb").read()
r = tool(kind="riser", project=proj, beats=8)
assert "error" not in r, r
assert os.path.dirname(r["path"]) == os.path.join(song_dir, "_fx"), r["path"]
assert r["bpm"] == tempo and r["tempo_from"] == "the project's tempo"
assert len(load(r["path"])) == round(8 * 60 / tempo * FS)
assert r["load"]["sampler"]["tool"] == "flp_load_sample" and r["load"]["sampler"]["args"]["sample"] == r["path"]
assert "flp_load_sample" in r["hint"] and "BEFORE" in r["place"]
assert open(proj, "rb").read() == before                                    # the song itself is not touched
flh_store.journal_update(proj, set={"compose": {"key": "F minor"}})
r = tool(kind="sub_drop", project=proj)
assert r["key"] == "F minor" and r["key_from"] == "song journal" and r["beats"] == 4, r
assert abs(zcr(load(r["path"])[:int(0.06 * FS), 0]) - 87.3) < 87.3 * 0.12
r = tool(kind="impact", project=proj, key="C minor", bpm=90)
assert r["key"] == "C minor" and r["bpm"] == 90 and r["ends_on"] == "after" and "ON" in r["place"]
assert "error" in tool(kind="riser")                                        # no bpm, no project
r = tool(kind="noise_sweep", bpm=128)                                       # no project: fl-headless's state folder
assert os.path.isfile(r["path"]) and r["path"].startswith(os.path.dirname(m.STATE))
r = tool(kind="reverse_crash", project=proj, sample=crash)
assert r["source"] == "crash.wav"
print("tool ok")

shutil.rmtree(tmp, True)
print("ok")
