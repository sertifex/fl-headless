"""Unit tests for fl_audio_edit. Run: python tests/test_audio_edit.py"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fl_audio_edit as A  # noqa: E402

R = 44100
T = tempfile.mkdtemp(prefix="audio_edit_test_")
rng = np.random.default_rng(1)


def p(name):
    return os.path.join(T, name)


def sine(f, sec, amp=0.5, rate=R, ch=1):
    t = np.arange(int(round(sec * rate))) / rate
    return np.repeat((amp * np.sin(2 * np.pi * f * t))[:, None], ch, axis=1)


def peak_freq(x, rate=R):
    m = x.mean(axis=1)
    m = m[len(m) // 4: 3 * len(m) // 4]
    nfft = 1 << 20
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m)), nfft))
    return np.argmax(spec) * rate / nfft


def rms_db(v):
    return 10 * np.log10(np.mean(v ** 2))


def roundtrip(x, bits):
    A.save(p(f"rt{bits}.wav"), x, R, bits)
    y, r, b = A.load(p(f"rt{bits}.wav"))
    assert r == R and b == bits and y.shape == x.shape, (r, b, y.shape)
    return y


# --- WAV round trip
x = rng.uniform(-0.99, 0.99, (5000, 2))
assert np.max(np.abs(roundtrip(x, 16) - x)) < 1 / 32768
assert np.max(np.abs(roundtrip(x, 24) - x)) < 1 / 8388608
assert np.max(np.abs(roundtrip(x, 32) - x)) < 1e-7
assert not os.path.exists(p("rt16.wav.tmp"))

# --- time strings
assert A.to_seconds("2bar", 120) == 4.0 and A.to_seconds("2 bars", 120) == 4.0
assert A.to_seconds("250ms") == 0.25 and A.to_seconds("1.5s") == 1.5 and A.to_seconds(3) == 3.0
assert A.to_seconds("3b", 120) == 1.5 and A.to_samples("250ms", 48000) == 12000
for bad, bpm in (("2bar", None), ("3 beats", None), ("abc", None), ("5 furlongs", 120)):
    try:
        A.to_seconds(bad, bpm)
        raise AssertionError(f"expected ValueError for {bad!r}")
    except ValueError:
        pass

# --- trim / cut / silence / reverse / repeat
src = sine(440, 2.0)
A.save(p("s.wav"), src, R, 24)
r = A.edit(p("s.wav"), p("trim.wav"), [{"op": "trim", "start": "0.5s", "end": 1.5}])
y = A.load(p("trim.wav"))[0]
assert len(y) == R and r["seconds_after"] == 1.0, (len(y), r)

r = A.edit(p("s.wav"), p("cut.wav"), [{"op": "cut", "start": 0.3013, "end": 0.7271}])
y = A.load(p("cut.wav"))[0][:, 0]
s0, s1 = int(round(0.3013 * R)), int(round(0.7271 * R))
assert len(y) == len(src) - (s1 - s0), len(y)
d = np.abs(np.diff(y))
join_d = d[s0 - 400:s0 + 400].max()
assert join_d < 1.6 * d[:s0 - 1000].max(), (join_d, d[:s0 - 1000].max())

y = A.load(p("s.wav"))[0]
z = A.apply_op(A.apply_op(y, R, {"op": "reverse"}), R, {"op": "reverse"})
assert np.array_equal(z, y)
z = A.apply_op(y, R, {"op": "reverse", "start": 0.5, "end": 1.0})
assert np.array_equal(z[:R // 2], y[:R // 2]) and not np.array_equal(z, y)
assert len(A.apply_op(y, R, {"op": "silence", "at": 1, "length": "250ms"})) == len(y) + R // 4
assert len(A.apply_op(y, R, {"op": "repeat", "times": 3, "crossfade": "10ms"})) == 3 * len(y) - 2 * 441
fo = A.apply_op(y, R, {"op": "fade_out", "length": 0.5})
assert abs(fo[-1, 0]) < 1e-3 and np.array_equal(fo[:R], y[:R])
for bad in ({"op": "nope"}, {"op": "gain", "dB": 3}):
    try:
        A.apply_op(y, R, bad)
        raise AssertionError(bad)
    except ValueError:
        pass

# --- pitch
y = sine(440, 2.0)
pz = A.pitch_shift(y, 12, True)
assert abs(len(pz) - len(y)) <= 1024 and abs(peak_freq(pz) - 880) / 880 < 0.01, (len(pz), peak_freq(pz))
pt = A.pitch_shift(y, 12, False)
assert abs(len(pt) - len(y) / 2) <= 1 and abs(peak_freq(pt) - 880) / 880 < 0.01
pd = A.pitch_shift(y, -5, True)
assert abs(peak_freq(pd) - 440 * 2 ** (-5 / 12)) / 440 < 0.01

# --- stretch
st = A.apply_op(sine(440, 2.0, ch=2), R, {"op": "stretch", "ratio": 1.5})
assert abs(len(st) - 1.5 * 2 * R) <= 1024 and st.shape[1] == 2
assert abs(peak_freq(st) - 440) / 440 < 0.01, peak_freq(st)
assert len(A.apply_op(y, R, {"op": "stretch", "from_bpm": 120, "to_bpm": 100})) == int(round(len(y) * 1.2))
mid = st[R // 2:-R // 2, 0]
assert abs(rms_db(mid) - rms_db(y[:, 0])) < 1.5, rms_db(mid)  # no big level dip

# --- loudness normalize
for name, sig in (("sine", sine(997, 3.0, 0.1, ch=2)), ("noise", rng.normal(0, 0.02, (3 * R, 2)))):
    A.save(p(name + ".wav"), sig, R, 24)
    r = A.edit(p(name + ".wav"), p(name + "_n.wav"), [{"op": "normalize", "lufs": -18}], bits=32)
    z = A.load(p(name + "_n.wav"))[0]
    L = A.integrated_loudness(z, R)
    assert abs(L + 18) < 0.3 and r["peak_dbfs"] <= -1.0 + 1e-6, (name, L, r)
z = A.apply_op(sine(997, 3.0, 0.1), R, {"op": "normalize", "lufs": 0}, notes=[])
assert abs(20 * np.log10(np.abs(z).max()) + 1) < 0.01
for rate in (44100, 48000, 96000):  # BS.1770 reference: 0 dBFS 997 Hz sine, one channel = -3.01 LKFS
    L = A.integrated_loudness(sine(997, 3.0, 1.0, rate=rate), rate)
    assert abs(L + 3.01) < 0.05, (rate, L)

# --- denoise
n_only = rng.normal(0, 0.01, (R, 1))  # -40 dBFS rms
sig = np.vstack([np.zeros((R, 1)), sine(440, 2.0, 0.5)]) + np.vstack([n_only, rng.normal(0, 0.01, (2 * R, 1))])
dn = A.denoise(sig, R, 12)
drop = rms_db(sig[:R]) - rms_db(dn[:R])
assert drop >= 8, drop
clean = sine(440, 2.0, 0.5)[:, 0]
change = abs(rms_db(dn[R + R // 4:-R // 4, 0]) - rms_db(clean[R // 4:-R // 4]))
assert change < 1, change
cs = A.denoise(sine(440, 2.0, 0.5), R, 12)
assert abs(rms_db(cs[R // 4:-R // 4]) - rms_db(clean[R // 4:-R // 4])) < 0.5

# --- mono / stereo / width
st2 = np.hstack([sine(440, 0.5), sine(660, 0.5)])
assert A.apply_op(st2, R, {"op": "mono"}).shape[1] == 1
assert A.apply_op(sine(440, 0.5), R, {"op": "stereo"}).shape[1] == 2
w0 = A.apply_op(st2, R, {"op": "width", "amount": 0})
assert np.allclose(w0[:, 0], w0[:, 1]) and np.allclose(A.apply_op(st2, R, {"op": "width", "amount": 1}), st2)

# --- join
a, b, c = sine(440, 1.0), sine(550, 0.5, ch=2), sine(660, 0.75, rate=22050)
A.save(p("a.wav"), a, R, 24)
A.save(p("b.wav"), b, R, 24)
A.save(p("c.wav"), c, 22050, 16)
r = A.join([p("a.wav"), p("b.wav"), p("c.wav")], p("j.wav"), crossfade="10ms")
j = A.load(p("j.wav"))[0]
assert j.shape[1] == 2 and len(j) == R + R // 2 + 2 * len(c) - 2 * 441, len(j)
r = A.join([p("a.wav"), p("b.wav")], p("jg.wav"), gap="1b", bpm=120)
assert len(A.load(p("jg.wav"))[0]) == R + R // 2 + R // 2

# --- slice
clicks = np.zeros((4 * R + R // 10, 1))
truth = [0.1 + 0.5 * k for k in range(8)]
burst = rng.normal(0, 0.6, int(0.01 * R)) * np.exp(-np.arange(int(0.01 * R)) / (0.002 * R))
for t in truth:
    i = int(t * R)
    clicks[i:i + len(burst), 0] += burst
clicks += rng.normal(0, 1e-4, clicks.shape)
A.save(p("clicks.wav"), clicks, R, 24)
r = A.slice(p("clicks.wav"), p("sl"), bpm=120)
assert r["count"] == 8, r
assert all(abs(s - t) < 0.01 for s, t in zip(r["starts_s"], truth)), r["starts_s"]
assert os.path.basename(r["files"][0]) == "clicks_01.wav" and all(os.path.exists(f) for f in r["files"])
A.save(p("four.wav"), sine(220, 4.0), R, 16)
r = A.slice(p("four.wav"), p("sb"), mode="beats", bpm=120, every="1b", prefix="beat")
assert r["count"] == 8 and r["files"][-1].endswith("beat_08.wav"), r
r = A.slice(p("four.wav"), p("se"), mode="equal", count=3)
assert r["count"] == 3


# dehum: 60 Hz hum + harmonics under a 440 Hz tone; hum drops, tone stays; clean audio is left alone
def _band_db(y, f, rate):
    w = np.hanning(len(y)); S = np.abs(np.fft.rfft(y * w)); fr = np.fft.rfftfreq(len(y), 1 / rate)
    k = np.argmin(abs(fr - f)); return 20 * np.log10(S[max(k - 3, 0):k + 4].max() + 1e-12)
rate = 44100; t = np.arange(rate * 4) / rate
tone = 0.3 * np.sin(2 * np.pi * 440 * t)
hum = sum(0.03 / k * np.sin(2 * np.pi * 60.02 * k * t) for k in (1, 2, 3, 4))
noisy = (tone + hum)[:, None]
y, info = A.dehum(noisy, rate)
assert info["hum"] and abs(info["hum"] - 60) < 0.5, info
for k in (1, 2, 3):
    assert _band_db(noisy[:, 0], 60 * k, rate) - _band_db(y[:, 0], 60 * k, rate) > 20, k
assert abs(_band_db(noisy[:, 0], 440, rate) - _band_db(y[:, 0], 440, rate)) < 0.3
clean, info2 = A.dehum(tone[:, None], rate)
assert info2["hum"] is None and np.allclose(clean[:, 0], tone)
print("audio edit ok")
