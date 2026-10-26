"""Pitch of low 808s (40-80 Hz): flh_transcribe.pitch_track on synthetic 808-like signals (sine + harmonics from
soft clipping, a downward 'punch' sweep in the attack, a slide glide, quiet speaker-capture levels), and the PC
check helpers that judge the 808 (attack skipped, octave decided by the spectrum).
PC check 2026-10-23: a held C#2 808 measured 'C3' from 7 voiced frames, a slide had 0 - the tracker's -55 dBFS
silence gate kept only the attack of a quiet capture. Run: python3 tests/test_bass_pitch.py"""
import os, sys, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import flh_transcribe as T  # noqa: E402
import flh_pccheck_mod as PM  # noqa: E402

FS = 44100


def s808(f0, dur=1.3, drive=3.0, punch=1.5, decay=0.6, glide=None, h2=0.0, gain=0.7):
    """An 808: a sine whose pitch starts (1 + punch) x higher and falls onto f0 in ~25 ms, soft-clipped (odd
    harmonics), optional 2nd harmonic, exponential decay; glide=(t0, t1, f1) bends to f1 between t0 and t1."""
    t = np.arange(int(FS * dur)) / FS
    f = f0 * (1 + punch * np.exp(-t / 0.025))
    if glide:
        a, b, f1 = glide
        f = f * (f1 / f0) ** np.clip((t - a) / (b - a), 0, 1)
    ph = 2 * np.pi * np.cumsum(f) / FS
    x = np.tanh(drive * (np.sin(ph) + h2 * np.sin(2 * ph))) / np.tanh(drive)
    return gain * x * np.exp(-t / decay) * np.minimum(1, t / 0.003)


def midi_of(hz):
    return 69 + 12 * np.log2(hz / 440.0)


def track(x, fmin=25.0, fmax=300.0):
    tr = T.pitch_track(x, FS, fmin=fmin, fmax=fmax)
    ok = tr["voiced"] & (tr["f0"] > 0)
    return tr, ok, midi_of(tr["f0"][ok]) if ok.any() else np.array([])


# ---- the tracker: right note, right octave, across the 808 range, loud and quiet
for f0 in (41.2, 46.2, 55.0, 61.7, 69.3, 77.8):
    want = midi_of(f0)
    for kw in ({}, {"drive": 8}, {"h2": 1.2}, {"drive": 1.0, "punch": 3}):
        for gain in (0.7, 0.01, 0.003):                          # 0.003: peaks near -50 dBFS (speaker capture)
            tr, ok, m = track(s808(f0, gain=gain, **kw))
            assert ok.sum() > 0.8 * len(ok), (f0, kw, gain, int(ok.sum()), len(ok))
            sus = m[tr["t"][ok] > 0.1]
            assert abs(np.median(sus) - want) < 0.15, (f0, kw, gain, np.median(sus) - want)
            assert np.mean(np.abs(sus - want) > 6) < 0.02, (f0, kw, gain, "octave jumps")
print("808 tracking ok")

# a weak fundamental under a strong 2nd harmonic still reads the fundamental
for f0 in (41.2, 69.3):
    t = np.arange(int(FS * 1.2)) / FS; ph = 2 * np.pi * f0 * t
    x = 0.4 * (0.15 * np.sin(ph) + np.sin(2 * ph) + 0.5 * np.sin(3 * ph)) * np.exp(-t / 0.8)
    _, ok, m = track(x)
    assert abs(np.median(m) - midi_of(f0)) < 0.15, (f0, np.median(m))
print("weak fundamental ok")

# a slide: C#2 held, bends up 3 semitones; the glide frames are voiced and in between
for gain in (0.7, 0.003):
    f0 = 69.3; x = s808(f0, gain=gain, glide=(0.45, 0.65, f0 * 2 ** (3 / 12)))
    tr, ok, m = track(x)
    tt = tr["t"][ok]
    a, b = np.median(m[(tt > 0.1) & (tt < 0.4)]), np.median(m[(tt > 0.75) & (tt < 1.1)])
    mid = ((m > a + 0.5) & (m < b - 0.5)).sum()
    assert abs(a - 37) < 0.2 and abs(b - 40) < 0.2 and mid >= 10, (gain, a, b, mid)
print("slide glide ok")

# normal levels: the gate is what it was (absolute -55 dBFS when the take reaches -20 dBFS): silence stays silent
x = np.concatenate([np.zeros(FS // 2), s808(55.0, dur=0.6), np.zeros(FS // 2)])
tr, ok, _ = track(x)
assert not ok[tr["t"] < 0.45].any() and not ok[tr["t"] > 1.2].any()
noise = np.random.default_rng(3).standard_normal(FS) * 1e-6     # -120 dBFS hiss alone: never a pitch
assert not T.pitch_track(noise, FS, fmin=25, fmax=300)["voiced"].any()
print("gate ok")

# ---- the PC check helpers
info = {}
t, m = PM._pitch(s808(69.3, gain=0.003, punch=3), FS, 25.0, 300.0, info=info, skip_attack_s=PM.ATTACK_SKIP_S)
assert t.min() >= info["onset_s"] + PM.ATTACK_SKIP_S - 1e-9 and abs(np.median(m) - 37) < 0.1, (info, np.median(m))
assert info["peak_dbfs"] < -45 and info["voiced_all"] >= len(m), info
# a slow pitch envelope (an 808 preset's pitch decay ~40 ms): its attack frames read well above the held note;
# without the skip they pull the measurement up, with it the held note is what's judged
tt = np.arange(int(FS * 1.3)) / FS; ff = 69.3 * (1 + 1.0 * np.exp(-tt / 0.04))
slow = 0.003 * np.sin(2 * np.pi * np.cumsum(ff) / FS) * np.exp(-tt / 0.6)
t_all, m_all = PM._pitch(slow, FS, 25.0, 300.0)
t_s, m_s = PM._pitch(slow, FS, 25.0, 300.0, skip_attack_s=PM.ATTACK_SKIP_S)
assert m_all.max() > 40 and len(m_all) > len(m_s) and m_s.max() < m_all.max(), (m_all.max(), m_s.max())
assert abs(np.median(m_s) - 37) < 0.3 and np.percentile(m_s, 90) < np.percentile(m_all, 90), np.median(m_s)
# octave decided by the spectrum: a real C#2 808 has its fundamental; a sound an octave up has nothing there
x = s808(69.3, h2=1.5)
e1, e2 = PM._band_db(x, FS, 69.3, 0.1), PM._band_db(x, FS, 138.6, 0.1)
assert e1 is not None and e2 is not None and e1 >= e2 - 15, (e1, e2)
x = s808(138.6)
e1, e2 = PM._band_db(x, FS, 69.3, 0.1), PM._band_db(x, FS, 138.6, 0.1)
assert e1 < e2 - 15, (e1, e2)
print("check helpers ok")
print("ok")
