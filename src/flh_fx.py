"""flh_fx: transition / ear-candy sounds made offline with numpy, tempo-exact, for every user (no sample pack needed).

    make_fx(kind, bpm, beats, out_dir, key=None, seed=1, intensity=1.0, sample=None)
      -> {"path", "kind", "seconds", "beats", "peak_at_s", "ends_on": "downbeat"|"after", ...}
    fl_make_fx(kind, bpm=None, beats=None, project=None, key=None, intensity=1.0, seed=1, sample=None, out_dir=None)

Kinds (FX_KINDS): riser, downlifter, impact, reverse_crash, sub_drop, tape_stop, noise_sweep, cymbal_swell (soft
  orchestral swell), boom (soft deep cinematic hit), reverse_swell (reversed-reverb chord: sample= or the key's tonic
  triad), and the loopable texture beds wind, rain, vinyl (ends_on 'loop': the last sample runs into the first).
  - length is exact: samples = round(beats * 60 / bpm * 44100); 44.1 kHz 16-bit stereo WAV, peak -1 dBFS, short
    fades (no clicks), deterministic per seed, decorrelated noise for width (sub layers stay mono).
  - ends_on 'downbeat': the file's END is the target downbeat (start it `beats` earlier); 'after': it STARTS on the
    downbeat. noise_sweep is centred on the downbeat (start beats/2 earlier). `start_offset_beats` in the result says
    where the file starts relative to the target downbeat.
  - files: <out_dir>/<Kind> <beats:g> beats <bpm:g> bpm.wav; a sidecar <out_dir>/_fx.json keeps a hash of the
    parameters so an identical request reuses the file (same mtime); a different request for the same name gets a
    ' <hash6>' suffix instead of overwriting a file a song may already reference.
DSP: noise and tones are shaped by one vectorised STFT filter (2048 / hop 512, a time-varying gain per frame);
tones are polyBLEP saws (band-limited) or sines from a phase accumulator. A 16-beat riser at 140 BPM is ~0.1 s.
Honest: these are synthesized stand-ins for sample-pack FX - a person should listen to the result in the song.
"""
import hashlib
import json
import math
import os
import sys
import threading

H = None
FS = 44100
BITS = 16
PEAK = 10 ** (-1.0 / 20)              # -1 dBFS
VERSION = 1                           # bump when the sound of a kind changes (the cache key includes it)
MAX_SECONDS = 120.0
_LOCK = threading.Lock()

FX_KINDS = {
    "riser": {"ends_on": "downbeat", "default_beats": 16, "uses_key": True,
              "about": "noise with a rising band-pass + a rising supersaw, exponential build; its END is the drop"},
    "downlifter": {"ends_on": "after", "default_beats": 8, "uses_key": True,
                   "about": "the riser's mirror: starts loud on the downbeat, falls in pitch and level"},
    "impact": {"ends_on": "after", "default_beats": 8, "uses_key": True,
               "about": "deep cinematic boom on the downbeat: low sine drop + noise burst + dark tail (beats = tail)"},
    "reverse_crash": {"ends_on": "downbeat", "default_beats": 4, "uses_key": False,
                      "about": "reversed cymbal swell (your crash sample= reversed, else synthesized); ends on the hit"},
    "sub_drop": {"ends_on": "after", "default_beats": 4, "uses_key": True,
                 "about": "808-like sine falling an octave from the key's root, soft saturation; starts on the downbeat"},
    "tape_stop": {"ends_on": "downbeat", "default_beats": 2, "uses_key": True,
                  "about": "a pulsing chord slowing down to silence (tape/vinyl stop), the gap before a drop"},
    "noise_sweep": {"ends_on": "after", "default_beats": 8, "uses_key": False,
                    "about": "white-noise band-pass sweeping up then down, centred on the downbeat"},
    "cymbal_swell": {"ends_on": "downbeat", "default_beats": 8, "uses_key": False,
                     "about": "soft orchestral cymbal swell (mallet roll), rising gently; its END is the downbeat"},
    "boom": {"ends_on": "after", "default_beats": 8, "uses_key": True,
             "about": "deep soft cinematic boom on the downbeat: rounded low sine + long dark tail (softer than impact)"},
    "reverse_swell": {"ends_on": "downbeat", "default_beats": 8, "uses_key": True,
                      "about": "reversed-reverb swell of your chord/piano sample= (else the key's tonic triad); ends "
                               "on the downbeat"},
    "wind": {"ends_on": "loop", "default_beats": 16, "uses_key": False,
             "about": "loopable wind bed (wandering band-pass, gusts, dark); keep it very quiet under intros/outros"},
    "rain": {"ends_on": "loop", "default_beats": 16, "uses_key": False,
             "about": "loopable rain bed (soft noise + droplets, no harsh highs); very quiet, intros/outros/breakdowns"},
    "vinyl": {"ends_on": "loop", "default_beats": 16, "uses_key": False,
              "about": "loopable vinyl bed (soft hiss, crackle, pops, rumble); very quiet, under lofi intros/outros"},
}
_NAMES = {"riser": "Riser", "downlifter": "Downlifter", "impact": "Impact", "reverse_crash": "Reverse Crash",
          "sub_drop": "Sub Drop", "tape_stop": "Tape Stop", "noise_sweep": "Noise Sweep",
          "cymbal_swell": "Cymbal Swell", "boom": "Boom", "reverse_swell": "Reverse Swell", "wind": "Wind",
          "rain": "Rain", "vinyl": "Vinyl"}
_ALIASES = {"uplifter": "riser", "rise": "riser", "build": "riser", "downer": "downlifter", "fall": "downlifter",
            "hit": "impact", "reverse": "reverse_crash", "reversecrash": "reverse_crash",
            "reverse cymbal": "reverse_crash", "subdrop": "sub_drop", "808 drop": "sub_drop", "tapestop": "tape_stop",
            "stop": "tape_stop", "sweep": "noise_sweep", "noisesweep": "noise_sweep", "whoosh": "noise_sweep",
            "cymbal": "cymbal_swell", "cymbal roll": "cymbal_swell", "swell": "cymbal_swell", "cymbalswell": "cymbal_swell",
            "low boom": "boom", "cinematic boom": "boom", "reverse reverb": "reverse_swell",
            "reverseswell": "reverse_swell", "reverse chord": "reverse_swell", "air": "wind",
            "crackle": "vinyl", "vinyl crackle": "vinyl", "record": "vinyl"}


def _np():
    import numpy as np
    return np


def _mod(name):
    m = sys.modules.get(name)
    if m is None:
        import importlib
        m = importlib.import_module(name)
    return m


def kind_of(kind):
    k = str(kind or "").strip().lower().replace("-", "_")
    k = _ALIASES.get(k, _ALIASES.get(k.replace("_", " "), k))
    if k not in FX_KINDS:
        raise ValueError(f"unknown fx kind '{kind}' (known: {', '.join(FX_KINDS)})")
    return k


def key_root(key):
    """'F minor' / 'Ab major' / 'C#m' / None -> root pitch class (default A = 9)."""
    if not key:
        return 9
    try:
        return int(_mod("fl_music").parse_key(str(key))[0]) % 12
    except Exception:
        pass
    s = str(key).strip()
    letters = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    if not s or s[0].upper() not in letters:
        raise ValueError(f"can't read the key '{key}' (like 'F minor' or 'Ab major')")
    pc = letters[s[0].upper()]
    for ch in s[1:]:
        if ch == "#":
            pc += 1
        elif ch == "b":
            pc -= 1
        else:
            break
    return pc % 12


def _note_in(pc, lo):
    """The MIDI note of pitch class pc in [lo, lo + 12)."""
    return lo + (pc - lo) % 12


def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12.0)


# ============================================================================= DSP building blocks
def _tv_filter(x, gain, N=2048, hop=1024):
    """Time-varying filter: x (n,) or (n, ch) -> same shape; gain(t_s (F,1), f_hz (1,B)) -> (F,B) magnitude per
    STFT frame (computed once for all channels). sqrt-Hann analysis + synthesis at hop N/2 (sum of w^2 = 1), all
    frames of a channel in one rfft."""
    np = _np()
    if x.ndim == 2:
        cols = [x[:, c] for c in range(x.shape[1])]
    else:
        cols = [x]
    n = len(cols[0])
    F = int(math.ceil((n + N) / hop)) + 1
    w = np.sin(np.pi * (np.arange(N) + 0.5) / N)
    t = ((np.arange(F) * hop - N // 2) / FS)[:, None]       # frame k is centred on signal sample k*hop - N/2
    f = (np.arange(N // 2 + 1) * FS / N)[None, :]
    G = gain(t, f)
    outs = []
    for col in cols:
        xp = np.zeros((F - 1) * hop + N)
        xp[N:N + n] = col
        fr = np.lib.stride_tricks.sliding_window_view(xp, N)[::hop][:F] * w
        Y = np.fft.irfft(np.fft.rfft(fr, axis=1) * G, n=N, axis=1) * w
        out = np.zeros(len(xp) + N)
        step = N // hop
        for k in range(step):                               # frames k, k+step, ... don't overlap: one add each
            blk = Y[k::step].reshape(-1)
            a = k * hop
            out[a:a + blk.size] += blk
        outs.append(out[N:N + n] / (np.sum(w ** 2) / hop))
    return np.stack(outs, 1) if x.ndim == 2 else outs[0]


def _tv2(a, b, gain):
    """Two channels through one _tv_filter call (the gain is computed once)."""
    y = _tv_filter(_np().stack([a, b], 1), gain)
    return y[:, 0], y[:, 1]


def _bandpass(fc, sigma_oct):
    """Gaussian band-pass on a log-frequency axis; fc(t) and sigma(t) are callables of time (s) or numbers."""
    np = _np()

    def g(t, f):
        c = fc(t) if callable(fc) else fc
        s = sigma_oct(t) if callable(sigma_oct) else sigma_oct
        return np.exp(-0.5 * (np.log2(np.maximum(f, 10.0) / c) / s) ** 2)
    return g


def _lowpass(fc, order=2, hp=0.0):
    """Butterworth-like magnitude: lowpass at fc(t) (order = poles/2) times a fixed 2-pole highpass at hp Hz."""
    np = _np()

    def g(t, f):
        c = fc(t) if callable(fc) else fc
        m = 1.0 / np.sqrt(1.0 + (f / c) ** (2 * order))
        if hp:
            m = m * (1.0 / np.sqrt(1.0 + (hp / np.maximum(f, 1.0)) ** 4))
        return m
    return g


def _saw(freq, phase0=0.0):
    """Band-limited (polyBLEP) saw from a per-sample frequency array."""
    np = _np()
    dt = np.clip(freq / FS, 1e-9, 0.45)
    ph = (phase0 + np.cumsum(dt) - dt[0]) % 1.0
    y = 2.0 * ph - 1.0
    m = ph < dt
    u = ph[m] / dt[m]
    y[m] -= u + u - u * u - 1.0
    m = ph > 1.0 - dt
    u = (ph[m] - 1.0) / dt[m]
    y[m] -= u * u + u + u + 1.0
    return y


def _sine(freq, phase0=0.0):
    np = _np()
    return np.sin(2 * np.pi * (np.cumsum(freq) / FS - freq[0] / FS) + phase0)


def _stereo_noise(rng, n, width=0.6):
    """Two noise channels with correlation 1 - width (decorrelated = wide), unit variance each."""
    np = _np()
    c = rng.standard_normal(n)
    a, b = math.sqrt(1 - width), math.sqrt(width)
    return a * c + b * rng.standard_normal(n), a * c + b * rng.standard_normal(n)


def _u(tt, T):
    """time -> 0..1 position (STFT frame times run a little past both ends)."""
    return _np().clip(tt / T, 0.0, 1.0)


def _db(v):
    return 10 ** (v / 20.0)


def _fades(y, fin_s, fout_s):
    np = _np()
    n = len(y)
    a = max(1, min(n // 2, int(round(fin_s * FS))))
    b = max(1, min(n // 2, int(round(fout_s * FS))))
    y[:a] *= (0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a))[:, None]
    y[n - b:] *= (0.5 + 0.5 * np.cos(np.pi * (np.arange(b) + 1) / b))[:, None]
    return y


def _width(L, R, amount):
    """Scale the side signal (amount 0 = mono, 1 = as is); amount may be a per-sample array."""
    m, s = 0.5 * (L + R), 0.5 * (L - R) * amount
    return m + s, m - s


# ============================================================================= the kinds
def _riser(n, root, rng, inten, flip=False):
    np = _np()
    T = n / FS
    t = np.arange(n) / FS
    u = t / T
    # noise: rising band-pass (400 Hz -> 12 kHz, accelerating), narrowing, plus a little 'air' above the band
    f0, f1 = 400.0, min(12000.0, 9000.0 + 3000.0 * inten)
    fc = lambda tt: f0 * (f1 / f0) ** (_u(tt, T) ** 1.3)
    sg = lambda tt: 1.3 - 0.5 * _u(tt, T)
    bp = _bandpass(fc, sg)
    air = lambda tt, f: bp(tt, f) + 0.15 * _u(tt, T) * (1.0 / np.sqrt(1.0 + (2.0 * fc(tt) / np.maximum(f, 1.0)) ** 4))
    nl, nr = _stereo_noise(rng, n, 0.7)
    nL, nR = _tv2(nl, nr, air)
    env_n = _db(-22.0 + 22.0 * u ** 1.6)
    # tone: 5-voice supersaw from the root (octave 3) rising 12..24+ semitones, opening lowpass, synced gate
    base = _note_in(root, 48)
    rise = 12.0 * (1.0 + min(1.5, inten))
    semis = rise * u ** 2.0
    tL, tR = np.zeros(n), np.zeros(n)
    for i, (det, pan) in enumerate(((0.0, 0.0), (-0.18, -0.8), (0.18, 0.8), (-0.09, 0.45), (0.11, -0.45))):
        v = _saw(_hz(base + semis + det), rng.random())
        tL += v * math.sqrt(0.5 * (1 - pan))
        tR += v * math.sqrt(0.5 * (1 + pan))
    oct_ = 12.0 if inten >= 0.6 else 0.0                     # an octave-down sine anchors the pitch
    sub = 0.6 * _sine(_hz(base - oct_ + semis))
    lp = _lowpass(lambda tt: 500.0 * (14000.0 / 500.0) ** (_u(tt, T) ** 1.5), 2, hp=90.0)
    tL, tR = _tv2(tL + sub, tR + sub, lp)
    env_t = _db(-26.0 + 26.0 * u ** 2.0)
    return nL, nR, env_n, tL, tR, env_t, u


def _gate(u, T, depth, rate0=2.0, rate1=16.0):
    """Amplitude gate accelerating from rate0 to rate1 Hz (a 'roll' feel), depth 0..1."""
    np = _np()
    rate = rate0 * (rate1 / rate0) ** (u ** 1.5)
    ph = np.cumsum(rate) / FS
    return 1.0 - depth * (0.5 - 0.5 * np.cos(2 * np.pi * ph))


def fx_riser(n, bpm, root, rng, inten):
    np = _np()
    nL, nR, env_n, tL, tR, env_t, u = _riser(n, root, rng, inten)
    beat = 60.0 / bpm
    g = _gate(u, n / FS, min(0.45, 0.25 * inten), 1.0 / beat, 8.0 / beat)       # quarter notes -> 32nds
    tl, tr = 0.55 * tL * env_t * g, 0.55 * tR * env_t * g
    L = 0.5 * nL * env_n + tl
    R = 0.5 * nR * env_n + tr
    L, R = _width(L, R, 0.45 + 0.55 * u)                                     # widens as it builds
    y = np.stack([L, R], 1)
    y = np.tanh(1.2 * y / max(1e-9, float(np.max(np.abs(y))))) / math.tanh(1.2)
    return _fades(y, 0.02, 0.003), n / FS


def fx_downlifter(n, bpm, root, rng, inten):
    np = _np()
    nL, nR, env_n, tL, tR, env_t, u = _riser(n, root, rng, inten)
    L = 0.55 * nL * env_n + 0.5 * tL * env_t
    R = 0.55 * nR * env_n + 0.5 * tR * env_t
    L, R = _width(L, R, 0.45 + 0.55 * u)
    y = np.stack([L, R], 1)[::-1].copy()                                     # the mirror: loud start, falling
    y = np.tanh(1.2 * y / max(1e-9, float(np.max(np.abs(y))))) / math.tanh(1.2)
    return _fades(y, 0.002, min(0.25, 0.08 * n / FS)), 0.0


def fx_impact(n, bpm, root, rng, inten):
    np = _np()
    T = n / FS
    t = np.arange(n) / FS
    low = _hz(_note_in(root, 28))                                            # E1..D#2: 41..78 Hz
    f = low + (160.0 + 60.0 * inten - low) * np.exp(-t / 0.05)
    tau = min(1.4, max(0.25, 0.3 * T))
    boom = _sine(f) * np.exp(-t / tau) * np.minimum(1.0, t / 0.002)
    boom = np.tanh((1.5 + inten) * boom) / math.tanh(1.5 + inten)            # harmonics so it reads on small speakers
    body = 0.35 * _sine(2 * f) * np.exp(-t / (0.35 * tau))
    nl, nr = _stereo_noise(rng, n, 0.5)
    burst_lp = _lowpass(lambda tt: 600.0 + 7400.0 * np.exp(-np.maximum(tt, 0) / 0.08), 2, hp=60.0)
    benv = np.exp(-t / 0.05) * np.minimum(1.0, t / 0.001)
    bL, bR = (c * benv for c in _tv2(nl, nr, burst_lp))
    ml, mr = _stereo_noise(rng, n, 0.9)                                      # the dark, wide tail ('room')
    tail_f = _lowpass(lambda tt: 250.0 + 1800.0 * np.exp(-np.maximum(tt, 0) / (0.25 * T)), 2, hp=35.0)
    tenv = np.minimum(1.0, t / 0.03) * np.exp(-t * 6.9 / T)                 # -60 dB at the end
    rL, rR = (c * tenv for c in _tv2(ml, mr, tail_f))
    mono = boom + body
    L = mono + 0.45 * inten * bL + 0.35 * rL
    R = mono + 0.45 * inten * bR + 0.35 * rR
    y = np.stack([L, R], 1)
    y = _fades(y, 0.0005, min(0.5, 0.15 * T))
    return y, float(np.argmax(np.abs(y[:, 0]))) / FS


def _crash_forward(n, rng):
    """A synthesized crash: bright decaying noise + metallic inharmonic partials, darkening as it rings."""
    np = _np()
    T = n / FS
    t = np.arange(n) / FS
    nl, nr = _stereo_noise(rng, n, 0.8)
    shape = lambda tt, f: (1.0 / np.sqrt(1.0 + (3000.0 / np.maximum(f, 1.0)) ** 4)) * \
        (1.0 / np.sqrt(1.0 + (f / (5000.0 + 11000.0 * np.exp(-np.maximum(tt, 0) / (0.35 * T)))) ** 2)) * \
        (1.0 + 0.8 * np.exp(-0.5 * (np.log2(np.maximum(f, 10.0) / 7000.0) / 0.4) ** 2))
    L, R = _tv2(nl, nr, shape)
    met = np.zeros(n)
    for a, b in ((3150.0, 4410.0), (5230.0, 1687.0), (6870.0, 2311.0), (8120.0, 977.0)):   # ring-modulated pairs
        met += 0.08 * _sine(np.full(n, a * (1 + 0.004 * rng.standard_normal())), rng.random() * 6.28) * \
            _sine(np.full(n, b * (1 + 0.004 * rng.standard_normal())), rng.random() * 6.28)
    met = _tv_filter(met, _lowpass(9000.0, 2, hp=2500.0))
    env = np.exp(-t * 5.2 / T) * np.minimum(1.0, t / 0.001)                  # ~-45 dB by the end
    menv = np.exp(-t * 9.0 / T)
    return np.stack([(L + met * 0.8) * env + met * menv * 0.3, (R + met * 0.8) * env - met * menv * 0.1], 1)


def _load_sample(path):
    np = _np()
    loader = H["_wav_load"] if H and "_wav_load" in H else _mod("fl_audio_edit").load
    x, r, _ = loader(path)
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    x = x[:, :2]
    if int(r) != FS:
        x = _mod("fl_audio_edit").resample_to(x, max(1, int(round(len(x) * FS / r))))
    return x


def fx_reverse_crash(n, bpm, root, rng, inten, sample=None):
    np = _np()
    if sample:
        x = _load_sample(sample)
        pk = float(np.max(np.abs(x))) if x.size else 0.0
        if pk <= 1e-6:
            raise ValueError(f"{sample}: silent")
        loud = np.nonzero(np.max(np.abs(x), 1) > pk * _db(-50))[0]
        x = x[loud[0]:loud[-1] + 1]                                          # the hit starts at the very end
        y = x[::-1]
        if len(y) >= n:
            y = y[len(y) - n:].copy()                                        # trim from the start
        else:
            y = np.concatenate([np.zeros((n - len(y), 2)), y])
        fin = min(0.25 * n / FS, 0.5)
    else:
        y = _crash_forward(n, rng)[::-1].copy()
        fin = min(0.1 * n / FS, 0.2)
    if inten != 1.0:                                                         # more = a steeper, later swell
        u = np.arange(n) / max(1, n - 1)
        y *= (u ** max(0.0, (inten - 1.0) * 1.5))[:, None]
    return _fades(y, fin, 0.0015), n / FS


def fx_sub_drop(n, bpm, root, rng, inten):
    np = _np()
    T = n / FS
    t = np.arange(n) / FS
    u = t / T
    f0 = _hz(_note_in(root, 38))                                             # D2..C#3: 73..139 Hz
    g = np.clip((u - 0.04) / 0.8, 0.0, 1.0)
    glide = g * g * (3.0 - 2.0 * g)                                          # holds the root, then eases down
    f = f0 * 2 ** (-glide)
    y = _sine(f)
    env = np.minimum(1.0, t / 0.004) * (1.0 - 0.6 * u ** 1.5)
    drive = 1.0 + 0.6 * inten
    y = np.tanh(drive * y * env) / math.tanh(drive)
    y = np.stack([y, y], 1)                                                  # subs stay mono
    return _fades(y, 0.003, min(0.3, 0.2 * T)), 0.0


def fx_tape_stop(n, bpm, root, rng, inten):
    np = _np()
    T = n / FS
    t = np.arange(n) / FS
    u = t / T
    speed = (1.0 - u) ** (1.0 + 0.5 * inten)
    pos = np.cumsum(speed) - speed[0]                                        # read position in source samples
    m = int(pos[-1]) + 4
    st = np.arange(m) / FS
    r = _note_in(root, 45)
    chord = sum(_saw(np.full(m, _hz(r + iv)), rng.random()) * a for iv, a in ((0, 1.0), (7, 0.7), (12, 0.5)))
    chord = _tv_filter(chord, _lowpass(3500.0, 2, hp=60.0)) + 0.8 * _sine(np.full(m, _hz(r - 12)))
    beat = 60.0 / bpm
    pulse = (st % (beat / 2)) / (beat / 2)                                   # 8th-note pulses that slow down too
    chord *= np.exp(-pulse * 3.0) * np.minimum(1.0, pulse / 0.02)
    nl, nr = _stereo_noise(rng, m, 0.6)
    wh = _bandpass(2500.0, 1.0)
    wl, wr = _tv2(nl, nr, wh)
    src_l, src_r = chord + 0.25 * wl, chord + 0.25 * wr
    L, R = np.interp(pos, np.arange(m), src_l), np.interp(pos, np.arange(m), src_r)
    env = speed ** 0.35
    y = np.stack([L * env, R * env], 1)
    return _fades(y, 0.003, min(0.15, 0.2 * T)), 0.0


def fx_noise_sweep(n, bpm, root, rng, inten):
    np = _np()
    T = n / FS
    t = np.arange(n) / FS
    v_of = lambda tt: 1.0 - np.abs(2.0 * _u(tt, T) - 1.0)
    f0, f1 = 250.0, min(14000.0, 8000.0 + 3000.0 * inten)
    bp = _bandpass(lambda tt: f0 * (f1 / f0) ** (v_of(tt) ** 1.2), lambda tt: 0.95 - 0.3 * v_of(tt))
    nl, nr = _stereo_noise(rng, n, 0.75)
    v = v_of(t)
    env = _db(-26.0 + 26.0 * v ** 1.5)
    L, R = (c * env for c in _tv2(nl, nr, bp))
    L, R = _width(L, R, 0.4 + 0.6 * v)
    y = np.stack([L, R], 1)
    return _fades(y, 0.03, 0.03), T / 2.0


def fx_cymbal_swell(n, bpm, root, rng, inten):
    """A soft orchestral cymbal swell (a mallet roll on a suspended cymbal): dark-ish noise opening slowly, a gentle
    roll flutter and a little metallic shimmer, rising exponentially to the downbeat - no crash at the end."""
    np = _np()
    T = n / FS
    t = np.arange(n) / FS
    u = t / T
    nl, nr = _stereo_noise(rng, n, 0.85)
    lp = lambda tt: 1600.0 * ((5000.0 + 1500.0 * min(1.0, inten)) / 1600.0) ** (_u(tt, T) ** 1.4)
    shape = lambda tt, f: (1.0 / np.sqrt(1.0 + (350.0 / np.maximum(f, 1.0)) ** 4)) * \
        (1.0 / np.sqrt(1.0 + (f / lp(tt)) ** 4)) * \
        (1.0 + 0.4 * np.exp(-0.5 * (np.log2(np.maximum(f, 10.0) / 3200.0) / 0.6) ** 2))
    L, R = _tv2(nl, nr, shape)
    met = np.zeros(n)
    for a in (2630.0, 3810.0, 5170.0):                                       # soft shimmer partials
        met += _sine(np.full(n, a * (1 + 0.003 * rng.standard_normal())), rng.random() * 6.28)
    met = met * (0.04 + 0.04 * u)
    rate = 11.0 + 5.0 * u                                                    # the mallet roll, a little faster
    roll = 1.0 - 0.10 * (0.5 - 0.5 * np.cos(2 * np.pi * np.cumsum(rate) / FS))
    env = _db(-32.0 + 32.0 * u ** (1.3 + 0.3 * (inten - 1.0))) * roll
    L, R = (L + met) * env, (R - 0.5 * met) * env
    L, R = _width(L, R, 0.6 + 0.4 * u)
    y = np.stack([L, R], 1)
    return _fades(y, min(0.1 * T, 0.2), 0.002), T


def fx_boom(n, bpm, root, rng, inten):
    """A deep, soft cinematic boom on the downbeat: a clean low sine with a gentle pitch settle, a rounded attack
    (no click, no noise burst) and a long dark tail - softer and darker than 'impact'."""
    np = _np()
    T = n / FS
    t = np.arange(n) / FS
    low = _hz(_note_in(root, 28))                                            # E1..D#2: 41..78 Hz
    f = low * (1.0 + 0.45 * np.exp(-t / 0.07))
    att = 0.012
    a_env = np.where(t < att, 0.5 - 0.5 * np.cos(np.pi * np.minimum(t, att) / att), 1.0)
    tau = min(2.2, max(0.4, 0.38 * T))
    boom = _sine(f) * np.exp(-t / tau) * a_env
    boom = boom + 0.18 * _sine(2 * f) * np.exp(-t / (0.4 * tau)) * a_env      # a little 2nd harmonic: small speakers
    boom = np.tanh((0.6 + 0.3 * inten) * boom) / math.tanh(0.6 + 0.3 * inten)
    ml, mr = _stereo_noise(rng, n, 0.9)                                      # the dark, wide room
    tail_f = _lowpass(lambda tt: 120.0 + 380.0 * np.exp(-np.maximum(tt, 0) / (0.3 * T)), 2, hp=30.0)
    tenv = np.minimum(1.0, t / 0.08) * np.exp(-t * 6.9 / T)
    rL, rR = (c * tenv for c in _tv2(ml, mr, tail_f))
    L, R = boom + 0.3 * rL, boom + 0.3 * rR
    y = np.stack([L, R], 1)
    y = _fades(y, 0.001, min(0.6, 0.2 * T))
    return y, float(np.argmax(np.abs(y[:, 0]))) / FS


def _fftconv(a, b):
    np = _np()
    m = len(a) + len(b) - 1
    N = 1 << max(1, (m - 1).bit_length())
    return np.fft.irfft(np.fft.rfft(a, N) * np.fft.rfft(b, N), N)[:m]


def _reverb_ir(rng, secs):
    """A stereo reverb-like impulse response: decorrelated noise decaying -60 dB over `secs`, darkening as it rings."""
    np = _np()
    m = max(64, int(secs * FS))
    t = np.arange(m) / FS
    a, b = _stereo_noise(rng, m, 0.95)
    a, b = _tv2(a, b, _lowpass(lambda tt: 900.0 + 5100.0 * np.exp(-np.maximum(tt, 0) / (0.35 * secs)), 1, hp=90.0))
    env = np.exp(-t * 6.9 / secs) * np.minimum(1.0, t / 0.01)
    a, b = a * env, b * env
    g = 1.0 / math.sqrt(max(1e-12, float(np.sum(a * a) + np.sum(b * b)) / 2))
    return a * g, b * g


def _triad_source(root, third, dur, rng):
    """The tonic triad (third = 3 minor / 4 major / None = an open fifth) as a soft piano-ish chord: a few partials
    (1/k^2), slightly detuned per side, a soft attack and a natural decay."""
    np = _np()
    m = max(64, int(dur * FS))
    t = np.arange(m) / FS
    r = _note_in(root, 55)                                                   # G3..F#4
    ivs = [0, 7, 12] if third is None else [0, third, 7, 12]
    L, R = np.zeros(m), np.zeros(m)
    for i, iv in enumerate(ivs):
        f0 = _hz(r + iv)
        amp = 0.6 if iv == 12 else 1.0
        for k in range(1, 6):
            if f0 * k > 6000.0:
                break
            a = amp / (k * k)
            dec = np.exp(-t / (dur / (1.5 + 0.6 * k)))
            L += a * dec * _sine(np.full(m, f0 * k * (1 - 0.0008 * (i + 1))), rng.random() * 6.28)
            R += a * dec * _sine(np.full(m, f0 * k * (1 + 0.0008 * (i + 1))), rng.random() * 6.28)
    att = np.minimum(1.0, t / 0.008)
    return np.stack([L * att, R * att], 1)


def fx_reverse_swell(n, bpm, root, rng, inten, sample=None, third=None):
    """A reversed-reverb swell into the downbeat: a short tonal source (your rendered chord / piano sample=, else the
    key's tonic triad with soft partials) smeared through a long dark reverb, mostly wet, then reversed - the
    harmony 'sucks in' and lands on the downbeat."""
    np = _np()
    T = n / FS
    if sample:
        x = _load_sample(sample)
        pk = float(np.max(np.abs(x))) if x.size else 0.0
        if pk <= 1e-6:
            raise ValueError(f"{sample}: silent")
        loud = np.nonzero(np.max(np.abs(x), 1) > pk * _db(-50))[0]
        x = x[loud[0]:loud[0] + int(min(3.0, max(0.3, 0.5 * T)) * FS)].copy()   # the attack and body
        k = max(1, min(len(x) // 4, int(0.05 * FS)))
        x[len(x) - k:] *= np.linspace(1.0, 0.0, k)[:, None]
        src = x / pk
    else:
        src = _triad_source(root, third, min(2.0, max(0.4, 0.45 * T)), rng)
    ir_s = min(6.0, max(0.5, 0.8 * T))
    il, irr = _reverb_ir(rng, ir_s)
    wl, wr = _fftconv(src[:, 0], il), _fftconv(src[:, 1], irr)
    dry = np.zeros((len(wl), 2))
    dry[:len(src)] = src
    w = np.stack([wl, wr], 1)
    w *= (float(np.max(np.abs(dry))) + 1e-9) / (float(np.max(np.abs(w))) + 1e-9)
    y = (0.85 * w + 0.25 * dry)[::-1]
    if len(y) >= n:
        y = y[len(y) - n:].copy()
    else:
        y = np.concatenate([np.zeros((n - len(y), 2)), y])
    if inten != 1.0:                                                         # more = a steeper, later swell
        u = np.arange(n) / max(1, n - 1)
        y *= (u ** max(0.0, (inten - 1.0) * 1.5))[:, None]
    return _fades(y, min(0.25 * T, 0.5), 0.0015), T


# ---------------------------------------------------------------------------- loopable texture beds
_LOOP_MARGIN = 4096


def _looped(n, make):
    """A seamless loop of n samples: make(m, t) -> (m, 2) for the times t (s, loop time: 0 = the loop start, the
    margins run outside it) is made longer, its overrun crossfaded (equal power) into its start, so the last sample
    runs into the first. Modulations should be periodic in the loop length (integer cycles)."""
    np = _np()
    X = int(min(0.5 * FS, n // 4))
    M = _LOOP_MARGIN
    m = n + X + 2 * M
    y = make(m, (np.arange(m) - M) / FS)[M:M + n + X]
    a = y[:n].copy()
    if X > 0:
        w = np.sin(0.5 * np.pi * (np.arange(X) + 0.5) / X)[:, None]
        a[:X] = y[:X] * w + y[n:n + X] * np.sqrt(1.0 - w * w)
    return a


def _periodic(rng, T, k_max=3):
    """A smooth random modulation, periodic in T: f(t) in about -1..1 (integer cycles 1..k_max)."""
    np = _np()
    ph = rng.random(k_max) * 2 * np.pi
    w = np.array([1.0 / (k + 1) for k in range(k_max)])
    w /= w.sum()
    return lambda tt: sum(w[k] * np.sin(2 * np.pi * (k + 1) * tt / T + ph[k]) for k in range(k_max))


def _shift(g, dt):
    """A _tv_filter gain in loop time: frame times from _tv_filter start at the array start (= -margin)."""
    return lambda tt, f: g(tt + dt, f)


def _impulses(rng, m, rate, scale=1.0):
    """A Poisson train of clicks (rate per second) with random signs and heavy-tailed sizes."""
    np = _np()
    x = np.zeros(m)
    k = rng.poisson(rate * m / FS)
    if k:
        pos = rng.integers(0, m, k)
        x[pos] += rng.choice([-1.0, 1.0], k) * scale * rng.lognormal(0.0, 0.6, k)
    return x


def fx_wind(n, bpm, root, rng, inten):
    """Wind: noise through a slowly wandering band-pass (300 Hz - 1.2 kHz) with gusts and a soft low rumble; no
    harsh highs. A seamless loop of `beats` (modulations periodic in its length)."""
    np = _np()
    T = n / FS
    dt = -_LOOP_MARGIN / FS
    gust_f, wan_f = _periodic(rng, T), _periodic(rng, T, 2)

    def make(m, t):
        nl, nr = _stereo_noise(rng, m, 0.6)
        fc = lambda tt: 600.0 * 2.0 ** (1.0 * wan_f(tt))
        bp = _bandpass(fc, 0.85)
        g = lambda tt, f: bp(tt, f) + 0.35 / np.sqrt(1.0 + (f / 160.0) ** 4)
        L, R = _tv2(nl, nr, _shift(g, dt))
        gust = 0.6 + 0.4 * gust_f(t) * min(1.0, inten)
        return np.stack([L * gust, R * gust], 1)
    return _looped(n, make), 0.0


def fx_rain(n, bpm, root, rng, inten):
    """Rain: a soft filtered noise bed (250 Hz - 2.2 kHz) with dense small droplets, gently low-passed; no harsh
    highs. A seamless loop of `beats`."""
    np = _np()
    T = n / FS
    dt = -_LOOP_MARGIN / FS
    swell_f = _periodic(rng, T, 2)

    def make(m, t):
        nl, nr = _stereo_noise(rng, m, 0.8)
        bed = _lowpass(2200.0, 2, hp=250.0)
        L, R = _tv2(nl, nr, bed)
        sh = _impulses(rng, m, 25.0 * inten, 6.0)
        dl, dr = sh + _impulses(rng, m, 20.0 * inten, 6.0), sh + _impulses(rng, m, 20.0 * inten, 6.0)
        drops = lambda tt, f: _bandpass(1400.0, 0.8)(tt, f) * (1.0 / np.sqrt(1.0 + (f / 3500.0) ** 4))
        dl, dr = _tv2(dl, dr, _shift(drops, dt))
        sw = 0.85 + 0.15 * swell_f(t)
        return np.stack([(0.5 * L + dl) * sw, (0.5 * R + dr) * sw], 1)
    return _looped(n, make), 0.0


def fx_vinyl(n, bpm, root, rng, inten):
    """Vinyl: soft surface hiss (low-passed 2.2 kHz) breathing with the record's turn, sparse crackle and the odd
    pop (band-passed, soft) and a faint rumble. A seamless loop of `beats` (the turn is periodic in its length)."""
    np = _np()
    T = n / FS
    dt = -_LOOP_MARGIN / FS
    turns = max(1, int(round(T * 33.333 / 60.0)))                             # 33 1/3 rpm

    def make(m, t):
        hl, hr = _stereo_noise(rng, m, 0.4)
        hl, hr = _tv2(hl, hr, _lowpass(2200.0, 2, hp=180.0))
        breathe = 1.0 + 0.15 * np.sin(2 * np.pi * turns * t / T)
        rum = _tv_filter(rng.standard_normal(m), _lowpass(70.0, 2, hp=25.0))
        c = _impulses(rng, m, 9.0 * inten, 1.0) + _impulses(rng, m, 0.6 * inten, 3.0)
        cl, cr = c + _impulses(rng, m, 3.0 * inten, 0.7), c + _impulses(rng, m, 3.0 * inten, 0.7)
        crk = lambda tt, f: _bandpass(1200.0, 0.9)(tt, f) * (1.0 / np.sqrt(1.0 + (f / 3500.0) ** 4))
        cl, cr = _tv2(cl, cr, _shift(crk, dt))
        return np.stack([0.08 * hl * breathe + 0.05 * rum + cl, 0.08 * hr * breathe + 0.05 * rum + cr], 1)
    return _looped(n, make), 0.0


_GEN = {"riser": fx_riser, "downlifter": fx_downlifter, "impact": fx_impact, "reverse_crash": fx_reverse_crash,
        "sub_drop": fx_sub_drop, "tape_stop": fx_tape_stop, "noise_sweep": fx_noise_sweep,
        "cymbal_swell": fx_cymbal_swell, "boom": fx_boom, "reverse_swell": fx_reverse_swell, "wind": fx_wind,
        "rain": fx_rain, "vinyl": fx_vinyl}
SAMPLE_KINDS = ("reverse_crash", "reverse_swell")
TEXTURES = ("wind", "rain", "vinyl")


def key_third(key):
    """The key's third: 3 (minor), 4 (major), or None (no key given: reverse_swell plays an open fifth)."""
    if not key:
        return None
    try:
        iv = {int(i) % 12 for i in _mod("fl_music").parse_key(str(key))[2]}
        return 3 if (3 in iv and 4 not in iv) else (4 if 4 in iv else None)
    except Exception:
        s = str(key).strip()[1:].lstrip("#b").strip().lower()
        return 3 if (s.startswith("m") and not s.startswith("maj")) else 4


# ============================================================================= files + cache
def _check_write(path):
    """FLH_ROOTS (H['writable']) or fl-headless's own state folder."""
    if not (H and "writable" in H):
        return
    try:
        st = os.path.normcase(os.path.abspath(os.path.dirname(H["STATE"])))
        if os.path.normcase(os.path.abspath(path)).startswith(st + os.sep):
            return
    except Exception:
        pass
    H["writable"](path)


def _sidecar(out_dir):
    return os.path.join(out_dir, "_fx.json")


def _side_read(out_dir):
    try:
        with open(_sidecar(out_dir), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _side_write(out_dir, name, entry):
    p = _sidecar(out_dir)
    _check_write(p)
    d = _side_read(out_dir)
    d[name] = entry
    for k in [k for k in d if not os.path.isfile(os.path.join(out_dir, k))]:   # forget files that were deleted
        d.pop(k, None)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=0, sort_keys=True)
    os.replace(tmp, p)


def _fmt(v):
    return f"{float(v):g}"


def _sample_sig(sample):
    try:
        st = os.stat(sample)
        return [os.path.abspath(sample), st.st_size, int(st.st_mtime)]
    except OSError:
        raise ValueError(f"sample not found: {sample}")


def make_fx(kind, bpm, beats, out_dir, key=None, seed=1, intensity=1.0, sample=None):
    """Make (or reuse) one tempo-exact FX WAV. See the module docstring for the result."""
    import time as _t
    np = _np()
    t0 = _t.time()
    k = kind_of(kind)
    bpm = float(bpm)
    if not 20.0 <= bpm <= 999.0:
        raise ValueError(f"bpm {bpm:g} is out of range (20-999)")
    beats = float(FX_KINDS[k]["default_beats"] if beats is None else beats)
    if beats <= 0:
        raise ValueError("beats must be > 0")
    n = int(round(beats * 60.0 / bpm * FS))
    if n / FS > MAX_SECONDS:
        raise ValueError(f"{beats:g} beats at {bpm:g} BPM is {n / FS:.0f} s (at most {MAX_SECONDS:g} s)")
    if n < int(0.02 * FS):
        raise ValueError(f"{beats:g} beats at {bpm:g} BPM is too short ({n / FS * 1000:.0f} ms)")
    inten = max(0.25, min(2.0, float(1.0 if intensity is None else intensity)))
    seed = int(1 if seed is None else seed)
    root = key_root(key) if FX_KINDS[k]["uses_key"] else None
    smp = sample if (k in SAMPLE_KINDS and sample) else None
    third = key_third(key) if (k == "reverse_swell" and not smp) else None
    params = {"v": VERSION, "kind": k, "n": n, "bpm": round(bpm, 4), "root": root, "seed": seed,
              "intensity": round(inten, 3), "sample": _sample_sig(smp) if smp else None}
    if k == "reverse_swell":
        params["third"] = third
    h = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()
    out_dir = os.path.abspath(out_dir)
    base = f"{_NAMES[k]} {_fmt(beats)} beats {_fmt(bpm)} bpm"
    info = FX_KINDS[k]
    res = {"kind": k, "seconds": round(n / FS, 6), "beats": beats, "ends_on": info["ends_on"],
           "start_offset_beats": -beats if info["ends_on"] == "downbeat" else (-beats / 2 if k == "noise_sweep" else 0.0),
           "samples": n, "bpm": bpm}
    if root is not None:
        res["key_root"] = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"][root]
    if info["ends_on"] == "loop":
        res["loop"] = True
    with _LOCK:
        side = _side_read(out_dir)
        names = [base + ".wav", f"{base} {h[:6]}.wav"]
        for nm in names:
            e, p = side.get(nm) or {}, os.path.join(out_dir, nm)
            if e.get("hash") == h and os.path.isfile(p) and os.path.getsize(p) == e.get("bytes"):
                res.update(path=p, peak_at_s=e.get("peak_at_s", 0.0), cached=True,
                           seconds_to_make=round(_t.time() - t0, 3))
                return res
        nm = next((x for x in names if not os.path.isfile(os.path.join(out_dir, x)) or
                   (side.get(x) or {}).get("hash") in (None, h)), names[1])
        path = os.path.join(out_dir, nm)
        _check_write(path)
        rng = np.random.default_rng([seed, list(FX_KINDS).index(k), 0x46584658])
        gen = _GEN[k]
        kw = {"sample": smp} if k in SAMPLE_KINDS else {}
        if k == "reverse_swell":
            kw["third"] = third
        y, peak_at = gen(n, bpm, root if root is not None else 9, rng, inten, **kw)
        y = np.nan_to_num(np.asarray(y, dtype=np.float64))
        assert y.shape == (n, 2), y.shape
        pk = float(np.max(np.abs(y)))
        if pk > 1e-9:
            y *= PEAK / pk
        os.makedirs(out_dir, exist_ok=True)
        _mod("fl_audio_edit").save(path, y, FS, bits=BITS, clip=True)
        peak_at = round(float(peak_at), 4)
        _side_write(out_dir, nm, {"hash": h, "bytes": os.path.getsize(path), "peak_at_s": peak_at, "params": params})
    res.update(path=path, peak_at_s=peak_at, cached=False, seconds_to_make=round(_t.time() - t0, 3))
    return res


# ============================================================================= the tool
def _project_key(project):
    """(key, where from) from the song journal, else detected from the notes, else (None, None)."""
    try:
        jr = _mod("flh_store").journal_get(project) or {}
    except Exception:
        jr = {}
    k = (jr.get("compose") or {}).get("key") or jr.get("key")
    if k:
        return str(k), "song journal"
    try:
        CM = _mod("flh_compose")
        k = CM.song_key(CM.project_view(project))
        if k:
            return str(k), "detected from the song's notes (a guess)"
    except Exception:
        pass
    return None, None


def fl_make_fx(kind, bpm=None, beats=None, project=None, key=None, intensity=1.0, seed=1, sample=None, out_dir=None):
    k = kind_of(kind)
    tempo_from = "given" if bpm else None
    key_from = "given" if key else None
    if project:
        project = os.path.abspath(project)
        if not os.path.isfile(project):
            return {"error": f"project not found: {project}"}
        if not bpm:
            bpm = float(H["flp_info"](project).get("tempo_bpm") or 0) or None
            tempo_from = "the project's tempo"
        if not key and FX_KINDS[k]["uses_key"]:
            key, key_from = _project_key(project)
    if not bpm:
        return {"error": "give bpm= (or project= to use its tempo)"}
    if FX_KINDS[k]["uses_key"] and not key:
        key_from = "default A (no key given or recorded)"
    if not out_dir:
        out_dir = os.path.join(os.path.dirname(project), "_fx") if project else \
            os.path.join(os.path.dirname(H["STATE"]), "fx")
    out_dir = os.path.abspath(out_dir)
    _check_write(os.path.join(out_dir, "x.wav"))
    r = make_fx(k, bpm, beats, out_dir, key=key, seed=seed, intensity=intensity, sample=sample)
    r["tempo_from"] = tempo_from
    if key_from and FX_KINDS[k]["uses_key"]:
        r["key"] = key or "A"
        r["key_from"] = key_from
    if k == "reverse_crash":
        r["source"] = os.path.basename(sample) if sample else "synthesized cymbal (give sample= for your own crash)"
    if k == "reverse_swell":
        r["source"] = os.path.basename(sample) if sample else \
            ("the key's tonic triad (give sample= for a rendered chord / piano)" if key else
             "an open fifth on the root (no key: give key= or sample=)")
    r["about"] = FX_KINDS[k]["about"]
    where = {"downbeat": f"start it {r['beats']:g} beats BEFORE the target downbeat so it ends on it",
             "after": "start it ON the target downbeat",
             "loop": f"loop it under a section (a note every {r['beats']:g} beats; it loops seamlessly) and keep it "
                     "very quiet (~20 dB under the music)"}[r["ends_on"]]
    if k == "noise_sweep":
        where = f"start it {r['beats'] / 2:g} beats before the target downbeat (its peak is on it)"
    r["place"] = where
    proj = project or "<song.flp>"
    # PC-VERIFY: the WAV loads in a sampler / as a playlist clip, its length is exactly `beats` on FL's grid
    r["load"] = {"sampler": {"tool": "flp_load_sample",
                             "args": {"project": proj, "output": proj, "sample": r["path"], "name": _NAMES[k]}},
                 "audio_clip": {"tool": "flp_add_audio_clip",
                                "args": {"project": proj, "output": proj, "audio": r["path"], "bar": "<bar>"}}}
    r["hint"] = ("load it into a sampler channel (flp_load_sample, one note at C5 plays it as made) or put it on the "
                 "playlist as an audio clip (flp_add_audio_clip); " + where + ". Synthesized: listen in the song.")
    return r


def register(ns):
    global H
    H = ns
    S = ns["_schema"]
    STR, INT, NUM = ns["_STR"], ns["_INT"], ns["_NUM"]
    kinds = ", ".join(FX_KINDS)
    ns["TOOLS"]["fl_make_fx"] = (
        fl_make_fx,
        "Make a tempo-exact transition / ear-candy sound offline (no sample pack needed): " + kinds + ". Riser = "
        "noise with a rising band-pass + a rising supersaw (exponential build, its END lands on the drop); "
        "downlifter = its mirror from the downbeat; impact = deep boom (sine drop + noise burst + dark tail); "
        "reverse_crash = your crash sample= reversed and fitted (or a synthesized cymbal swell); sub_drop = 808-like "
        "sine falling an octave from the song's key root; tape_stop = a pulsing chord slowing to silence; "
        "noise_sweep = band-pass up then down, centred on the downbeat; cymbal_swell = a soft orchestral cymbal swell "
        "ending on the downbeat; boom = a softer, darker cinematic low hit with a long tail; reverse_swell = a "
        "reversed-reverb swell of sample= (a rendered chord/piano) or the key's tonic triad, ending on the downbeat; "
        "wind / rain / vinyl = seamless texture loops (keep them ~20 dB under the music). Length = beats at the song's tempo exactly "
        "(44.1 kHz WAV, -1 dBFS) in <song folder>/_fx/; tempo and key come from the project (key: song journal, "
        "else detected, else A). Identical requests reuse the file. Returns where to place it and how to load it "
        "(flp_load_sample / flp_add_audio_clip). Synthesized: listen in the song.",
        S(kind=STR(kinds),
          bpm=NUM("tempo (default: the project's)", True),
          beats=NUM("length in beats (default per kind: riser 16, downlifter 8, impact 8 = tail, reverse_crash 4, "
                    "sub_drop 4, tape_stop 2, noise_sweep 8, cymbal_swell 8, boom 8, reverse_swell 8, wind / rain / "
                    "vinyl 16 = the loop)", True),
          project=ns["_p"]("string", "the .flp whose tempo/key to use; files go to its folder's _fx/", True),
          key=STR("key like 'F minor' / 'Ab major' (default: the song's, else A)", True),
          intensity=NUM("0.25-2 (default 1): pitch range, drive, gate depth", True),
          seed=INT("another seed = another take of the noise (default 1)", True),
          sample=STR("reverse_crash: your crash/cymbal WAV/FLAC/Ogg to reverse; reverse_swell: a rendered chord / "
                     "piano WAV to smear and reverse", True),
          out_dir=STR("write here instead of <song folder>/_fx (inside FLH_ROOTS)", True)))
    ns["fl_make_fx"] = fl_make_fx
    ns["make_fx"] = make_fx
    ns["FX_KINDS"] = FX_KINDS
    ns.setdefault("CROSS_PLATFORM", set()).add("fl_make_fx")
    try:
        ns["TOOL_GROUPS"]["fl_make_fx"] = "sound"
    except Exception:
        pass
    try:
        ns["SHORT_DESC"]["fl_make_fx"] = ("Make tempo-exact transition FX / texture loops offline (riser, impact, "
                                          "reverse_crash, cymbal_swell, boom, reverse_swell, tape_stop, wind, vinyl...) "
                                          "at the song's tempo/key; says where to place it.")
    except Exception:
        pass
    try:
        ns["LONG_HELP"]["fl_make_fx"] = (
            "Typical: fl_make_fx(kind='riser', project=<song.flp>, beats=16) -> <song>/_fx/Riser 16 beats 140 bpm.wav; "
            "load it with flp_load_sample or place it with flp_add_audio_clip so its end is on the drop (riser, "
            "reverse_crash, tape_stop end on the downbeat; downlifter, impact, sub_drop start on it; noise_sweep is "
            "centred on it - see 'place' and 'start_offset_beats'). intensity=1.5 = bigger; seed=2 = another take.")
    except Exception:
        pass
