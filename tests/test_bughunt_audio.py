"""Bug hunt (audio files): regression tests for bugs found with adversarial synthetic audio.
Run: python3 tests/test_bughunt_audio.py"""
import math, os, sys, tempfile, tracemalloc
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_review, flh_mixeng as X, flh_transcribe as TR  # noqa: E402
import fl_audio_edit as A, fl_sound, flac_lite  # noqa: E402
ns = vars(m)
flh_store.register(ns); flh_review.register(ns); X.register(ns); TR.register(ns)
rng = np.random.default_rng(11)
P = lambda name: os.path.join(tmp, name)

# ---------------------------------------------------------------- transcribe: clips shorter than ~1 s
# tempo_estimate's 1 s moving average used np.convolve(mode="same"), which returns the KERNEL's length when the
# envelope is shorter -> broadcast crash in beat_grid (audio_to_midi on any one-shot / short phrase < ~1 s)
fs = 22050
for dur in (0.25, 0.5, 0.9):
    n = int(dur * fs)
    y = np.zeros(n); y[int(0.05 * fs):int(0.05 * fs) + 400] = rng.normal(0, 0.5, 400)   # one drum hit
    g = TR.beat_grid(y, fs)
    assert g["bpm"] is None and g["detected"] is False, g
    A.save(P(f"hit{dur}.wav"), np.stack([y, y], 1), fs, 16)
    r = TR.audio_to_midi(P(f"hit{dur}.wav"), mode="drums")
    assert isinstance(r, dict), r
print("short clips ok")

# ---------------------------------------------------------------- audio_edit: float sources keep their overs
x = np.stack([1.5 * np.sin(np.arange(44100) * 0.05)] * 2, 1)
A.save(P("float.wav"), x, 44100, 32)
r = A.edit(P("float.wav"), P("float_trim.wav"), [{"op": "trim", "start": 0.1}])
y, _, bits = A.load(P("float_trim.wav"))
assert bits == 32 and abs(np.abs(y).max() - 1.5) < 1e-3, (bits, np.abs(y).max())      # was: 24-bit, clipped at 1.0
assert abs(r["peak_dbfs"] - 20 * math.log10(1.5)) < 0.05, r
r = A.join([P("float.wav"), P("float.wav")], P("float_join.wav"))
assert A.load(P("float_join.wav"))[2] == 32
A.save(P("pcm16.wav"), x * 0.5, 44100, 16)                                           # PCM stays PCM
A.edit(P("pcm16.wav"), P("pcm16_out.wav"), [{"op": "gain", "db": -1}])
assert A.load(P("pcm16_out.wav"))[2] == 16
r = A.edit(P("pcm16.wav"), P("pcm16_loud.wav"), [{"op": "gain", "db": 6}])           # overs into 16-bit: said
assert any("clip" in n for n in r.get("notes", [])), r
# a non-finite sample is written as silence, never a full-scale click
z = np.zeros((10, 1)); z[3, 0] = np.inf; z[4, 0] = -np.inf; z[5, 0] = np.nan
for b in (16, 24, 32):
    A.save(P(f"inf{b}.wav"), z, 44100, b)
    assert np.abs(A.load(P(f"inf{b}.wav"))[0]).max() == 0.0, b
print("audio_edit bits ok")

# ---------------------------------------------------------------- fl_sound.describe: very short sounds
for n in (1, 100, 441, 600):
    d = fl_sound.describe(np.full((n, 2), 0.5), 44100)
    assert isinstance(d, dict), d
print("describe short ok")

# ---------------------------------------------------------------- FLAC decode: bounded memory, exact samples
peaks = {}
_defs = flh_review._flac_frames.__defaults__
flh_review._flac_frames.__defaults__ = (32,) + _defs[1:]   # small batches: these short files span many of them
for sec in (4, 12):                                     # memory growth per extra second: the output, not 6x it
    n = 44100 * sec
    pcm = (np.sin(np.arange(n) * 0.01) * 20000).astype(np.int32)[:, None].repeat(2, 1); pcm[:, 1] //= 2
    flac_lite.encode(P(f"long{sec}.flac"), pcm, 44100, 16)
    tracemalloc.start()
    y, rate, bits = flh_review.load_audio(P(f"long{sec}.flac"))
    peaks[sec] = (tracemalloc.get_traced_memory()[1], y.nbytes); tracemalloc.stop()
    assert y.dtype == np.float32 and np.array_equal(y, (pcm / 32768.0).astype(np.float32)), "FLAC samples changed"
flh_review._flac_frames.__defaults__ = _defs
grow = (peaks[12][0] - peaks[4][0]) / (peaks[12][1] - peaks[4][1])
assert grow < 2.0, (grow, peaks)                                                    # was ~6x the float32 output
y2, _, _ = flh_review.flac_load(P("long12.flac"))                                   # default stays float64
assert y2.dtype == np.float64 and np.array_equal(y2, pcm / 32768.0)
y3 = A.load(P("long12.flac"))[0]
assert y3.dtype == np.float64 and np.array_equal(y3, pcm / 32768.0)
print("flac memory ok")

# ---------------------------------------------------------------- MP3 via Media Foundation (mocked): no float64 detour
import mf_audio  # noqa: E402
_mf = (mf_audio.decode, mf_audio.is_media)
fake = rng.uniform(-0.5, 0.5, (1000, 2)).astype(np.float32)
mf_audio.decode = lambda path, max_seconds=None: (fake, 44100)
open(P("song.mp3"), "wb").write(b"ID3\x04" + b"\0" * 64)
y, rate, bits, bad = A.load_ex(P("song.mp3"), np.float32)
assert y is fake and rate == 44100 and bad == 0                    # was: float32 -> float64 -> float32 copies
y, rate, bits, bad = A.load_ex(P("song.mp3"))
assert y.dtype == np.float64 and np.array_equal(y, fake.astype(np.float64))
mf_audio.decode, mf_audio.is_media = _mf
print("media load ok")

# ---------------------------------------------------------------- plugin maps: enum given as an option index
TYPES = ["Off", "Low pass", "Band pass", "High pass", "Notch", "Low shelf", "Peaking", "High shelf"]
FXP = {27: 6 / 7}
def fake_call(cmd, timeout=15.0, **a):
    if cmd == "plugin_params":
        return {"plugin": "Fruity Parametric EQ 2",
                "params": [{"param": 27, "name": "Band 7 type", "value": FXP[27], "text": TYPES[int(round(FXP[27] * 7))]}]}
    if cmd == "plugin_set":
        assert 0.0 <= a["value"] <= 1.0, a                     # never a knob value outside 0..1
        FXP[a["param"]] = a["value"]
        return {"param": a["param"], "value": a["value"], "text": TYPES[int(round(a["value"] * 7))]}
    if cmd == "state":
        return {"playing": False}
    raise AssertionError(cmd)
m.BRIDGE.call = fake_call
r = X.plugin_set_named(0, 0, "Fruity Parametric EQ 2", {"band7_type": 3})               # index 3 = 'High pass'
assert r["set"] and r["set"][0]["ok"] and r["set"][0]["text"] == "High pass", r
r = X.plugin_set_named(0, 0, "Fruity Parametric EQ 2", {"band7_type": 42})
assert not r["set"] and r["skipped"] and r["skipped"][0]["control"] == "band7_type", r
r = X.plugin_set_named(0, 0, "Fruity Parametric EQ 2", {"band7_type": "Wobble"})        # unknown label: reported
assert r["skipped"] and "Wobble" in r["skipped"][0]["why"] and not r["undo"], r
print("enum index ok")
print("ok")
