"""flh_vocal: synthetic vocals (harmonic tones with vibrato + noise, injected clicks / plosive bursts / sibilant
bursts / breaths) -> detection counts and times, cleanup amounts, comp picks the in-tune / on-time take per phrase
with hysteresis and click-free crossfades, chain derivation reacts to measured sibilance / mud / tempo / edition,
doubles and harmonies have the expected pitch and timing offsets, balance verdicts and duck automation, and the live
tools (setup, level check, record, chain apply) against a fake FL bridge - old bridge (fallbacks) and new commands.
Run: python3 tests/test_vocal.py"""
import json
import os
import shutil
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
os.environ.pop("FLH_FL_EDITION", None)
import fl_audio_edit as AE                                       # noqa: E402
import fl_headless_mcp as m                                      # noqa: E402
import flh_store                                                 # noqa: E402
import flh_vocal as V                                            # noqa: E402

ns = vars(m)
flh_store.register(ns); V.register(ns)
TOOL = lambda name: m.TOOLS[name][0]
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
SR = 44100
T0 = time.time()


# ----------------------------------------------------------------------------- synthetic voice
def voice(notes, total, sr=SR, seed=0, detune=None, vib=30.0, noise_db=-66, amp=0.3, formant=None, jitter=0.0):
    """notes: [(start_s, dur_s, midi)]; detune: cents per note."""
    rng = np.random.default_rng(seed)
    n = int(total * sr); y = np.zeros(n)
    for i, (s, d, k) in enumerate(notes):
        a = int(s * sr); L = int(d * sr); t = np.arange(L) / sr
        cents = (detune[i] if detune else 0.0) + vib * np.sin(2 * np.pi * 5.5 * t + rng.uniform(0, 6))
        if jitter:
            cents = cents + np.convolve(rng.normal(0, jitter, L), np.ones(200) / 200, mode="same") * 14
        f = 440 * 2 ** ((k - 69 + cents / 100) / 12)
        ph = 2 * np.pi * np.cumsum(f) / sr
        sig = np.zeros(L)
        for h in range(1, 25):
            fh = f * h
            g = (1.0 / h) * (1 + 1.5 * np.exp(-((fh - 700) / 400) ** 2) + 0.8 * np.exp(-((fh - 2500) / 600) ** 2))
            if formant:
                hz, db, w = formant
                g = g * (1 + (10 ** (db / 20) - 1) * np.exp(-((fh - hz) / w) ** 2))
            sig += np.where(fh < sr * 0.45, g, 0) * np.sin(h * ph)
        sig *= np.clip(np.minimum(t / 0.03, (d - t) / 0.05), 0, 1)
        y[a:a + L] += amp * sig / max(1e-9, np.abs(sig).max())
    return y + 10 ** (noise_db / 20) * rng.normal(0, 1, n)


def bandnoise(L, lo, hi, seed):
    z = np.random.default_rng(seed).normal(0, 1, L); Z = np.fft.rfft(z); f = np.fft.rfftfreq(L, 1 / SR)
    Z[(f < lo) | (f > hi)] = 0
    z = np.fft.irfft(Z, L); return z / np.abs(z).max()


def add_sib(y, at, dur=0.12, amp=0.25, seed=3):
    a = int(at * SR); L = int(dur * SR); y[a:a + L] += amp * bandnoise(L, 5500, 8500, seed) * np.hanning(L); return y


def add_breath(y, at, dur=0.3, amp=0.02, seed=5):
    a = int(at * SR); L = int(dur * SR); y[a:a + L] += amp * bandnoise(L, 400, 6000, seed) * np.hanning(L); return y


def add_plosive(y, at, amp=0.6):
    a = int(at * SR); L = int(0.06 * SR); t = np.arange(L) / SR
    y[a:a + L] += amp * np.sin(2 * np.pi * 60 * t) * np.exp(-t / 0.015) * np.minimum(1, t / 0.002); return y


def add_click(y, at, amp=0.15):
    a = int(at * SR); L = int(0.0008 * SR)
    y[a:a + L] += amp * np.hanning(L) * np.sign(np.sin(np.arange(L) * 2.5)); return y


def wav(name, y, sr=SR):
    p = os.path.join(tmp, name); AE.save(p, y if y.ndim == 2 else y[:, None], sr); return p


def band_db(sig, a, b, lo=0, hi=22050):
    seg = sig[int(a * SR):int(b * SR)]; S = np.abs(np.fft.rfft(seg)) ** 2; f = np.fft.rfftfreq(len(seg), 1 / SR)
    return 10 * np.log10(S[(f >= lo) & (f < hi)].sum() + 1e-20)


def near(times, want, tol=0.03):
    return all(any(abs(t - w) <= tol for t in times) for w in want) and len(times) == len(want)


def cents_of(y, a, b):
    f0, _ = V.yin(y, SR)
    return float(np.nanmedian(1200 * np.log2(f0[int(a / 0.01):int(b / 0.01)] / 440.0)))


# ----------------------------------------------------------------------------- registration
for t in ("fl_vocal_setup", "fl_vocal_level_check", "fl_vocal_record", "fl_vocal_comp", "fl_vocal_clean",
          "fl_vocal_chain", "fl_vocal_balance", "fl_vocal_double"):
    fn, desc, sch = m.TOOLS[t]
    assert callable(fn) and len(desc) > 80 and sch["type"] == "object", t
    for r in sch["required"]:
        assert r in sch["properties"], (t, r)
assert V.OFFLINE_TOOLS <= m.CROSS_PLATFORM, "offline vocal tools must run on any OS"
assert not m._windows_only("fl_vocal_comp") and (m.IS_WINDOWS or m._windows_only("fl_vocal_record"))

# ----------------------------------------------------------------------------- YIN
y = voice([(0.5, 1.5, 69), (2.5, 1.0, 57)], 4.0, vib=0)
assert abs(cents_of(y, 0.7, 1.9)) < 3 and abs(cents_of(y, 2.7, 3.4) + 1200) < 3
yv = voice([(0.5, 1.5, 64)], 2.2, vib=30)
assert abs(cents_of(yv, 0.7, 1.9) + 500) < 6                     # vibrato centre found
f0, _ = V.yin(np.random.default_rng(0).normal(0, 0.1, SR), SR)
assert np.isfinite(f0).mean() < 0.05, "noise is unvoiced"

# ----------------------------------------------------------------------------- detection + cleanup
y = voice([(0.5, 1.4, 69), (2.5, 1.2, 64), (4.3, 1.2, 67)], 6.0, noise_db=-58)
y = add_sib(y, 1.95); y = add_sib(y, 3.75, seed=9)
y = add_breath(y, 2.05); y = add_breath(y, 3.95, dur=0.25, seed=7)
y = add_plosive(y, 2.45); y = add_plosive(y, 4.25)
y = add_click(y, 1.0); y = add_click(y, 3.0); y = add_click(y, 5.0)
src = wav("dirty.wav", y)
r = TOOL("fl_vocal_clean")(wav=src)
F = r["found"]
assert F["clicks"]["count"] == 3 and near(F["clicks"]["at_s"], [1.0, 3.0, 5.0], 0.005), F["clicks"]
assert F["plosives"]["count"] == 2 and near(F["plosives"]["at_s"], [2.45, 4.25]), F["plosives"]
assert F["sibilance"]["count"] == 2 and near(F["sibilance"]["at_s"], [1.96, 3.76], 0.04), F["sibilance"]
assert 6000 < F["sibilance"]["center_hz"] < 8000, F["sibilance"]
assert F["breaths"]["count"] == 2 and near(F["breaths"]["at_s"], [2.1, 3.99], 0.06), F["breaths"]
assert F["noise"]["reduced_db"] > 0, F["noise"]
assert set(r["steps_s"]) >= {"analyze", "clicks", "spectral", "noise", "write"}
x2, _, _ = AE.load(r["output"]); x2 = x2[:, 0]
assert os.path.isfile(src) and r["output"] != src and len(x2) == len(y)
assert band_db(x2, 1.95, 2.07, 5000, 9000) - band_db(y, 1.95, 2.07, 5000, 9000) < -5, "de-essed"
assert band_db(x2, 2.12, 2.3) - band_db(y, 2.12, 2.3) < -8, "breath reduced"
assert band_db(x2, 2.45, 2.5, 0, 150) - band_db(y, 2.45, 2.5, 0, 150) < -10, "pop's low band cut"
assert abs(band_db(x2, 0.7, 0.95) - band_db(y, 0.7, 0.95)) < 0.5, "the voice itself is untouched"
assert band_db(x2, 5.6, 5.9) - band_db(y, 5.6, 5.9) < -4, "room noise down"
c = int(1.0 * SR); seg = slice(c - 40, c + 80)
assert np.abs(x2[seg]).max() < np.abs(y[seg]).max() * 0.8, "click repaired"
assert np.abs(x2[seg]).max() > 0.1, "...and the voice under it kept"
try:
    TOOL("fl_vocal_clean")(wav=src, output=src); raise AssertionError("overwrote the take")
except ValueError:
    pass
keep = TOOL("fl_vocal_clean")(wav=src, output=os.path.join(tmp, "keep.wav"), breaths="keep", clicks=False,
                              plosives=False, sibilance=False, noise=False)
xk, _, _ = AE.load(keep["output"])
assert np.abs(xk[:, 0] - y).max() < 1e-4, "everything off = identical audio"
assert keep["found"]["breaths"]["count"] == 2 and keep["found"]["breaths"]["action"] == "keep"
rem = TOOL("fl_vocal_clean")(wav=src, output=os.path.join(tmp, "rem.wav"), breaths="remove", noise=False)
xr, _, _ = AE.load(rem["output"])
assert band_db(xr[:, 0], 2.12, 2.3) - band_db(y, 2.12, 2.3) < -25, "breath removed"
# no false positives on clean singing: vibrato + jitter, a low male voice, a high voice
for notes, kw in (([(0.3, 2.0, 69), (2.6, 1.5, 72)], {"jitter": 8}), ([(0.3, 2.0, 45), (2.6, 1.5, 43)], {}),
                  ([(0.3, 2.0, 81)], {"vib": 50})):
    A = V.analyze(voice(notes, 4.4, **kw)[:, None], SR)
    cl = V.detect_clicks(A["m"], SR)
    assert not cl and not A["plosives"] and not A["sibilance"], (notes, cl, A["plosives"], A["sibilance"])

# ----------------------------------------------------------------------------- comping
MEL = [(0.5, 0.9, 69), (2.0, 0.9, 72), (3.5, 0.9, 76), (5.0, 0.9, 74)]       # A C E D (A minor)


def take(detune, seed, total=6.5, shift_s=0.0):
    yy = np.zeros(int(total * SR))
    for i, (s, d, k) in enumerate(MEL):
        yy += voice([(s + shift_s, d, k)], total, seed=seed * 10 + i, detune=[detune[i]], noise_db=-120)
    return yy + 10 ** (-66 / 20) * np.random.default_rng(seed).normal(0, 1, len(yy))


tk = [wav("t1.wav", take([0, 45, 0, 40], 1)), wav("t2.wav", take([40, 0, 45, 0], 2)), wav("t3.wav", take([30] * 4, 3))]
r = TOOL("fl_vocal_comp")(takes=tk, key="A minor")
picks = [d["pick"] for d in r["decisions"]]
assert r["phrases"] == 4 and picks == [1, 2, 1, 2], r["decisions"]
assert all("pitch" in d["why"] for d in r["decisions"]), r["decisions"]
assert r["key_source"] == "given" and len(r["switch_points_s"]) == 3
cx, _, _ = AE.load(r["output"])
assert len(cx) == int(6.5 * SR)
for i, (s, d, k) in enumerate(MEL):                              # each phrase came from the in-tune take
    assert abs(cents_of(cx[:, 0], s + 0.2, s + d - 0.2) - (k - 69) * 100) < 8, i
# click-free: the comp's biggest sample step near each switch is no bigger than the takes' own steps there
src_x = [AE.load(p)[0][:, 0] for p in tk]
for sp in r["switch_points_s"]:
    a, b = int((sp - 0.05) * SR), int((sp + 0.05) * SR)
    js = np.abs(np.diff(cx[a:b, 0])).max(); ref = max(np.abs(np.diff(x[a:b])).max() for x in src_x)
    assert js <= ref * 1.1 + 1e-6, (sp, js, ref)
# hysteresis: small differences don't make it hop takes
tk2 = [wav("h1.wav", take([0, 9, 0, 9], 4)), wav("h2.wav", take([9, 0, 9, 0], 5))]
r2 = TOOL("fl_vocal_comp")(takes=tk2, key="A minor", output=os.path.join(tmp, "h comp.wav"))
assert len(set(d["pick"] for d in r2["decisions"])) == 1, r2["decisions"]
assert any("avoid a switch" in d["why"] or "kept" in d["why"] or "tie" in d["why"] or d["pick"] == 1
           for d in r2["decisions"])
# one take only: comp == take
r1 = TOOL("fl_vocal_comp")(takes=tk[:1], key="A minor", output=os.path.join(tmp, "single.wav"))
xs, _, _ = AE.load(r1["output"]); assert np.abs(xs[:, 0] - src_x[0]).max() < 1e-5
# reference melody catches an in-scale wrong note the key can't see (take 1 sings B instead of C in phrase 2)
wrong = [wav("w1.wav", np.sum([voice([(s, d, 71 if i == 1 else k)], 6.5, seed=60 + i, noise_db=-120)
                               for i, (s, d, k) in enumerate(MEL)], axis=0) + 1e-4 * np.random.default_rng(6).normal(0, 1, int(6.5 * SR))),
         wav("w2.wav", take([12, 12, 12, 12], 7))]
ref = [{"note": k, "start_s": s, "end_s": s + d} for s, d, k in MEL]
rk = TOOL("fl_vocal_comp")(takes=wrong, key="A minor", output=os.path.join(tmp, "wk.wav"))
rm_ = TOOL("fl_vocal_comp")(takes=wrong, reference_melody=ref, output=os.path.join(tmp, "wm.wav"))
assert rk["decisions"][1]["pick"] == 1, "by key alone the wrong note looks in tune"
assert rm_["decisions"][1]["pick"] == 2 and rm_["key_source"] == "reference melody", rm_["decisions"]
# timing (rap): take 2 is 45 ms late against the 1/16 grid at 120 BPM (notes start on beats)
tt = [wav("r1.wav", take([0] * 4, 8)), wav("r2.wav", take([0] * 4, 9, shift_s=0.045))]
rt = TOOL("fl_vocal_comp")(takes=tt, key="A minor", style="rap", bpm=120, output=os.path.join(tmp, "rt.wav"))
assert all(d["pick"] == 1 for d in rt["decisions"]) and rt["tempo_bpm"] == 120, rt["decisions"]
# explicit phrases + journal registration
if not os.path.isfile(FIX):
    print("skipped: vocal project parts (no fixture in tests/data or tests/corpus)"); sys.exit(0)
proj = os.path.join(tmp, "Song.flp"); shutil.copy(FIX, proj)
rp = TOOL("fl_vocal_comp")(takes=tk, project=proj, key="A minor", phrases=[[0.4, 1.5], [1.9, 3.0], [3.4, 4.5], [4.9, 6.0]],
                           output=os.path.join(tmp, "jcomp.wav"))
assert rp["phrase_source"] == "given" and [d["pick"] for d in rp["decisions"]] == [1, 2, 1, 2]
assert flh_store.journal_get(proj)["vocal"]["comp"]["path"] == rp["output"]
rpk = TOOL("fl_vocal_comp")(takes=tk[:2], project=proj, output=os.path.join(tmp, "pk.wav"))
assert rpk["key_source"] in ("score_view", "journal", "project notes"), rpk["key_source"]
re_ = TOOL("fl_vocal_comp")(takes=tk[:2], output=os.path.join(tmp, "est.wav"))
assert re_["key"] and "estimated" in re_["key_source"], re_["key_source"]

# ----------------------------------------------------------------------------- vocal chain
MEL2 = [(0.3 + i * 0.6, 0.5, k) for i, k in enumerate([57, 60, 62, 64, 65, 67, 64, 62, 60, 59])]
plain = wav("plain.wav", voice(MEL2, 6.5))
muddy = wav("muddy.wav", voice(MEL2, 6.5, formant=(300, 9, 70)))
ys = voice(MEL2, 6.5)
for t in (0.7, 1.9, 3.1, 4.3, 5.5):
    ys = add_sib(ys, t, seed=int(t * 10))
sibby = wav("sibby.wav", ys)
cp = TOOL("fl_vocal_chain")(take=plain, style="pop", bpm=100)
cm_ = TOOL("fl_vocal_chain")(take=muddy, style="pop", bpm=100)
cs = TOOL("fl_vocal_chain")(take=sibby, style="pop", bpm=100)
stage = lambda plan, name: next(c for c in plan["chain"] if c["stage"] == name)
mudcut = lambda plan: min([b.get("gain_db", 0) for b in stage(plan, "corrective EQ")["bands"] if b["band"] == "bell"] or [0])
assert cm_["measured"]["mud"]["excess_db"] > cp["measured"]["mud"]["excess_db"] + 1.5, (cm_["measured"]["mud"], cp["measured"]["mud"])
assert mudcut(cm_) < mudcut(cp) - 0.9, (mudcut(cm_), mudcut(cp))
assert stage(cp, "de-esser")["plugin"] is None, "no sibilance measured -> no de-esser"
de = stage(cs, "de-esser")
assert de["plugin"] == "Fruity Multiband Compressor" and 4000 <= de["settings"]["crossover_hz"] <= 6000, de
assert cs["measured"]["sibilance"]["per_min"] > 20 and 6000 < cs["measured"]["sibilance"]["center_hz"] < 8000
air = lambda plan: sum(b["gain_db"] for c in plan["chain"] if c["stage"] == "tone EQ" for b in c["bands"] if b["band"] == "high shelf")
assert air(cs) < air(cp), "less air boost on a sibilant take"
hp = stage(cp, "corrective EQ")["bands"][0]
assert hp["band"] == "high-pass" and hp["hz"] <= 0.75 * cp["measured"]["f0_low_hz"] + 5, hp
low = wav("low.wav", voice([(0.3, 1.5, 38), (2.0, 1.5, 40)], 4.0))         # D2-E2: HPF must stay under it
cl_ = TOOL("fl_vocal_chain")(take=low, style="rock", bpm=100)
assert stage(cl_, "corrective EQ")["bands"][0]["hz"] < 70, stage(cl_, "corrective EQ")["bands"][0]
dl = next(s for s in cp["sends"] if s["bus"] == "Vocal Delay")
assert dl["settings"]["time_ms"] == round(0.75 * 600) and dl["settings"]["time_note"] == "1/8 dotted", dl
rv = next(s for s in cp["sends"] if s["bus"] == "Vocal Reverb")
assert abs(rv["settings"]["decay_s"] - 1.8) < 0.01 and 10 <= rv["settings"]["predelay_ms"] <= 80, rv
c1 = stage(cp, "compressor 1")["settings"]
assert c1["threshold_db"] < cp["measured"]["p95_db"] and c1["ratio"] == 3.0
pod = TOOL("fl_vocal_chain")(take=plain, style="podcast")
assert not pod["sends"] and pod["tempo_from"].startswith("assumed")
try:
    TOOL("fl_vocal_chain")(take=plain, style="polka"); raise AssertionError("bad style accepted")
except ValueError:
    pass
os.environ["FLH_FL_EDITION"] = "fruity"
fr = TOOL("fl_vocal_chain")(take=plain, style="pop", bpm=100)
assert fr["tuning"]["plugin"] is None and fr["tuning"]["free_options"], fr["tuning"]
os.environ["FLH_FL_EDITION"] = "signature"
sg = TOOL("fl_vocal_chain")(take=plain, style="pop", bpm=100)
assert sg["tuning"]["plugin"] == "Pitcher", sg["tuning"]
os.environ.pop("FLH_FL_EDITION")
nd = TOOL("fl_vocal_chain")(style="rnb", bpm=80, project=None)
assert nd["measured"] is False and stage(nd, "de-esser")["plugin"]

# ----------------------------------------------------------------------------- doubles / harmonies
yd = voice([(0.3, 1.5, 69), (2.3, 1.5, 64)], 4.0, vib=10)
dsrc = wav("lead.wav", yd)
r = TOOL("fl_vocal_double")(wav=dsrc, kind="double", voices=1, delay_ms=20, drift_cents=8)
xd, _, _ = AE.load(r["output"]); xd = xd[:, 0]
a, b = int(0.4 * SR), int(1.6 * SR)
lag = np.argmax(np.correlate(xd[a:b], yd[a:b], "full")) - (b - a - 1)
assert abs(lag / SR * 1000 - 20) < 3, lag / SR * 1000
f0d, _ = V.yin(xd, SR); f0o, _ = V.yin(yd, SR)
dc = 1200 * np.log2(f0d[50:170] / f0o[50:170])
assert np.nanmax(np.abs(dc)) < 15 and np.nanstd(dc) > 0.5, (np.nanmax(np.abs(dc)), np.nanstd(dc))
r2 = TOOL("fl_vocal_double")(wav=dsrc, kind="double", width=1.0, output=os.path.join(tmp, "dbl2.wav"))
x2, _, _ = AE.load(r2["output"])
assert x2.shape[1] == 2 and r2["stereo"] and abs(np.corrcoef(x2[a:b, 0], x2[a:b, 1])[0, 1]) < 0.9
r = TOOL("fl_vocal_double")(wav=dsrc, kind="octave_up")
xo, _, _ = AE.load(r["output"])
assert abs(cents_of(xo[:, 0], 0.5, 1.6) - 1200) < 15 and abs(cents_of(xo[:, 0], 2.5, 3.6) - 700) < 15
r = TOOL("fl_vocal_double")(wav=dsrc, kind="octave_down", output=os.path.join(tmp, "od.wav"))
xo, _, _ = AE.load(r["output"])
assert abs(cents_of(xo[:, 0], 0.5, 1.6) + 1200) < 15
r = TOOL("fl_vocal_double")(wav=dsrc, kind="third_up", key="A minor")            # A->C, E->G: minor thirds
xh, _, _ = AE.load(r["output"])
assert abs(cents_of(xh[:, 0], 0.5, 1.6) - 300) < 15 and abs(cents_of(xh[:, 0], 2.5, 3.6) - (-500 + 300)) < 15
yc = voice([(0.3, 1.5, 60), (2.3, 1.5, 64)], 4.0, vib=10)                       # C major: C->E (4), E->G (3)
r = TOOL("fl_vocal_double")(wav=wav("cmaj.wav", yc), kind="third_up", key="C major")
xh, _, _ = AE.load(r["output"])
assert abs(cents_of(xh[:, 0], 0.5, 1.6) - (-900 + 400)) < 15 and abs(cents_of(xh[:, 0], 2.5, 3.6) - (-500 + 300)) < 15
assert r["layers"]["third up"]["semitones_used"].keys() == {3, 4}
r = TOOL("fl_vocal_double")(wav=dsrc, kind="harmony", key="A minor", steps=[2, 4], output=os.path.join(tmp, "harm.wav"))
assert set(r["layers"]) >= {"third up", "fifth up"} and r["stereo"]
assert r["layers"]["fifth up"]["semitones_used"] == {7: r["layers"]["fifth up"]["semitones_used"][7]}   # A->E, E->B: fifths

# ----------------------------------------------------------------------------- balance (offline + duck apply)
vb = voice([(0.5, 3.0, 69), (4.5, 3.0, 64), (8.5, 3.0, 67)], 12.0, amp=0.3)
rng = np.random.default_rng(11)
beat = np.zeros(len(vb)); kick = np.sin(2 * np.pi * 55 * np.arange(int(0.15 * SR)) / SR) * np.exp(-np.arange(int(0.15 * SR)) / SR / 0.05)
for k in range(0, len(beat) - len(kick), int(0.5 * SR)):
    beat[k:k + len(kick)] += 0.6 * kick
beat += 0.45 * bandnoise(len(beat), 200, 6000, 12)
for gain, want in ((0.1, "buried"), (1.0, "on top"), (4.0, "too loud")):
    rb = TOOL("fl_vocal_balance")(vocal_wav=wav(f"bv{gain}.wav", vb * gain), beat_wav=wav("beat.wav", beat), style="rap")
    sts = {s["status"] for s in rb["sections"] if s.get("vocal")}
    if want:
        assert sts == {want}, (gain, rb["sections"])
    if want == "buried":
        assert rb["vocal_move_db"] > 3
    if want == "too loud":
        assert rb["vocal_move_db"] < -3
    assert rb["norm_lu"] == [2.0, 5.0] and rb["phrases_s"], rb
ref_diff = TOOL("fl_vocal_balance")(vocal_wav=wav("bv1.wav", vb), beat_wav=wav("beat.wav", beat))["sections"][0]["vocal_over_beat_lu"]
pod = TOOL("fl_vocal_balance")(vocal_wav=wav("bv1.wav", vb), beat_wav=wav("beat.wav", beat), style="podcast")
assert pod["norm_lu"] == [10.0, 20.0] and abs(pod["sections"][0]["vocal_over_beat_lu"] - ref_diff) < 0.01
m.fl_running = lambda: False
proj2 = os.path.join(tmp, "Duck.flp"); shutil.copy(FIX, proj2)
rb = TOOL("fl_vocal_balance")(project=proj2, vocal_track=1, beat_tracks=[2], vocal_wav=wav("bvq.wav", vb * 0.1),
                              beat_wav=wav("beat.wav", beat), style="rap", apply=True, vocal_start_bar=2)
assert rb["duck_db"] > 0 and any("ducked inserts [2]" in a for a in rb["applied"]), rb["applied"]
assert any("vocal insert 1" in a for a in rb["applied"]), rb["applied"]
auto = [c for c in m.flp_playlist(proj2)["clips"] if c["kind"] == "automation" and c["name"] == "Vocal duck 2"]
assert len(auto) == 1 and abs(auto[0]["bar"] - (2 + (0.5 - 0.05) / 4)) < 0.02, auto     # 60 BPM: 4 s per bar
pts = V._duck_points([(0.5, 1.5), (1.6, 2.0)], 1.0, 120, 4, 2.0, -3.0, 0.05, 0.25)
assert len(pts) == 4 and pts[1]["db"] == -5.0 and pts[0]["db"] == -3.0, pts               # close phrases merge

# ----------------------------------------------------------------------------- live tools against a fake bridge
rec_dir = os.path.join(tmp, "Recorded"); os.makedirs(rec_dir)
os.environ["FLH_RECORDED_DIR"] = rec_dir
FL = {"bar": 1.0, "playing": False, "recording": False, "song_mode": True, "calls": [], "new_cmds": False,
      "names": {0: "Master", 1: "Kick", 2: "Insert 2", 3: "Bass", 4: "Insert 4"}, "armed": set(),
      "channels": [{"index": 0, "name": "Kick", "insert": 1}, {"index": 1, "name": "Bass", "insert": 3}],
      "peaks": [0.3], "takes": 0, "set": []}


def fake_call(cmd, timeout=15.0, **a):
    FL["calls"].append((cmd, a.get("action") or a.get("track")))
    newc = {"mixer_info", "mixer_arm", "mixer_peaks", "rec_setup"}
    if cmd in newc and not FL["new_cmds"]:
        raise RuntimeError(f"FL: ValueError: unknown command {cmd}")
    if cmd == "state":
        return {"tempo": 120.0, "playing": False, "channels": FL["channels"],
                "mixer": [{"index": t, "name": FL["names"][t], "muted": False, "effects": []} for t in (0, 1, 3)]}
    if cmd == "mixer_set":
        t = a["track"]
        if t not in FL["names"]:
            raise RuntimeError(f"FL: ValueError: no mixer track {t}")
        if a.get("name") is not None:
            FL["names"][t] = a["name"]
        return {"track": t, "name": FL["names"][t], "color": a.get("color"), "volume_db": 0.0, "routes_to": [0]}
    if cmd == "mixer_info":
        ts = FL["names"] if a["tracks"] == "all" else {int(a["tracks"]): FL["names"][int(a["tracks"])]}
        return {"tracks": [{"index": t, "name": n, "armed": t in FL["armed"], "used": t in (1, 3), "effects": FL.get("fx", {}).get(t, [])}
                           for t, n in ts.items()]}
    if cmd == "mixer_arm":
        FL["armed"].add(a["track"]); return {"track": a["track"], "armed": True, "recording_file": ""}
    if cmd == "mixer_peaks":
        v = FL["peaks"][len(FL["calls"]) % len(FL["peaks"])]; return {"peaks": {int(a["tracks"]): [v, v * 0.9]}}
    if cmd == "rec_setup":
        return {"punch": [a.get("punch_in_bar"), a.get("punch_out_bar")]}
    if cmd == "transport":
        if a["action"] == "play":
            FL["playing"], FL["t"], FL["b0"] = True, time.time(), FL["bar"]
        elif a["action"] == "stop" and FL["playing"]:
            FL["bar"] = FL["b0"] + (time.time() - FL["t"]) * 8; FL["playing"] = False
            if FL["recording"] and FL.get("edison_mode"):        # FL records into Edison: no playlist clip;
                FL["save_dialog"] = True                         # Edison's Save As pops up
            elif FL["recording"]:                                # FL writes the take and adds an audio clip channel
                FL["takes"] += 1
                AE.save(os.path.join(rec_dir, f"Insert 2_{FL['takes']:02d}.wav"), np.zeros((4410, 1)), SR)
                FL["channels"].append({"index": len(FL["channels"]), "name": f"Insert 2_{FL['takes']:02d}", "insert": 2})
        return {"done": a["action"]}
    if cmd == "song":
        act = a["action"]
        if act == "goto":
            FL["bar"] = a["bar"]
        elif act == "record":
            FL["recording"] = not FL["recording"]
        elif act == "song_mode":
            FL["song_mode"] = not FL["song_mode"]
        b = FL["b0"] + (time.time() - FL["t"]) * 8 if FL["playing"] else FL["bar"]       # 8 bars per second
        return {"bar": round(b, 3), "recording": FL["recording"], "song_mode": FL["song_mode"]}
    if cmd == "channel_set":
        FL["set"].append((a["index"], a.get("name"))); return {"index": a["index"], "name": a.get("name")}
    if cmd == "plugin_params":
        names = ["Band 1 level", "Band 1 freq", "Band 1 width", "Band 2 level", "Band 2 freq", "Threshold", "Ratio",
                 "Attack", "Release", "Gain", "High threshold", "High ratio", "High freq"]
        return {"plugin": "x", "count": len(names), "params": [{"param": i, "name": n, "value": 0.5, "text": ""}
                                                               for i, n in enumerate(names) if a.get("filter", "") in n]}
    if cmd == "plugin_set_value":
        FL["set"].append(("param", a["index"], a.get("slot"), a["param"], a["target"]))
        FL.setdefault("knobs", {})[(a["index"], a.get("slot"), a["param"])] = str(a["target"])
        return {"param": a["param"], "text": str(a["target"])}
    if cmd == "plugin_get":                                       # fl_plugin_set_value reads back in a later call
        return {"param": a["param"], "value": 0.5,
                "text": FL.get("knobs", {}).get((a["index"], a.get("slot"), a["param"]), "")}
    raise AssertionError(cmd)


m.BRIDGE.call = fake_call; m.fl_running = lambda: True; m._open_bpb = lambda: 4
m.SET_VERIFY_S = 0.0                                              # the fake keeps every set at once
m.fl_dialogs = lambda: []
m._open_project_guess = lambda: (None, [])
m.fl_settings_summary = lambda: {"audio": {"device": {"value": "Primary Sound Driver"}, "sample_rate": {"value": 44100},
                                           "buffer_length": {"value": 1024}}}
m._fl_key = lambda: "FL Studio 25"; m._reg_dump = lambda sub="": {}
proj3 = os.path.join(tmp, "Live Song.flp"); shutil.copy(FIX, proj3)
# old bridge: no arm / info / peaks commands -> clear fallbacks
s = TOOL("fl_vocal_setup")(project=proj3, input="In 1")
assert s["track"] == 2 and FL["names"][2] == "Vocal" and s["armed"] is None and "record-arm" in s["arm_how"], s
assert not s["input"]["set"] and "input" in s["input"]["how"]
assert s["audio"]["driver"] == "DirectSound/WASAPI" and s["audio"]["buffer_ms"] == 23.2 and len(s["audio"]["advice"]) >= 2
assert flh_store.journal_get(proj3)["vocal"]["track"] == 2 and flh_store.journal_get(proj3)["vocal"]["input"] == "In 1"
assert s["mixer"]["color"] == V.VOCAL_COLOR
s2 = TOOL("fl_vocal_setup")(project=proj3)
assert s2["track"] == 2 and s2["picked"].startswith("already"), s2
lc = TOOL("fl_vocal_level_check")(seconds=2, project=proj3)
assert lc.get("measured") is False and "mixer_peaks" in lc["why"], lc                     # no peaks, no loopback here
# new bridge commands
FL["new_cmds"] = True
FL["names"][2] = "Insert 2"
del_j = flh_store.journal_update(proj3, set={"vocal": None})
s = TOOL("fl_vocal_setup")(project=proj3)
assert s["track"] == 2 and s["armed"] is True and 2 in FL["armed"] and s["scanned_with"] == "mixer_info", s
for peaks, verdict in (([1.05, 0.9], "too hot"), ([0.35, 0.4], "good"), ([0.05, 0.06], "too quiet"), ([0.001], "no signal")):
    FL["peaks"] = peaks
    lc = TOOL("fl_vocal_level_check")(seconds=2, project=proj3)
    assert lc["verdict"] == verdict, (peaks, lc)
    if verdict == "too hot":
        assert lc["change_db"] < 0 and lc["clipped_reads"] > 0
    if verdict == "too quiet":
        assert lc["change_db"] > 10
rr = TOOL("fl_vocal_record")(start_bar=5, end_bar=9, takes=2, project=proj3, background=False)
assert [p["take"] for p in rr["passes"]] == [1, 2] and len(rr["takes"]) == 2, rr
assert rr["takes"][0]["name"] == "Live Song Vocal take 1" and rr["takes"][1]["starts_at_bar"] == 5.0, rr["takes"]
assert rr["punch"] == "set in FL" and not FL["recording"] and FL["song_mode"], (rr["punch"], FL["recording"])
assert ("Live Song Vocal take 2" in [n for _, n in FL["set"][:4]]), FL["set"]
jt = flh_store.journal_get(proj3)["vocal"]["takes"]
assert [t["name"] for t in jt] == ["Live Song Vocal take 1", "Live Song Vocal take 2"] and os.path.isfile(jt[0]["path"])
# a second recording goes on with the numbering (journal + Recorded folder), never a second 'Vocal take 1'
rr2 = TOOL("fl_vocal_record")(start_bar=5, end_bar=9, takes=1, project=proj3, background=False)
assert [t["name"] for t in rr2["takes"]] == ["Live Song Vocal take 3"], rr2["takes"]
jt = flh_store.journal_get(proj3)["vocal"]["takes"]
assert [t["name"] for t in jt] == [f"Live Song Vocal take {i}" for i in (1, 2, 3)], [t["name"] for t in jt]
proj_n = os.path.join(tmp, "numbering", "Other Song.flp"); os.makedirs(os.path.join(tmp, "numbering", "Recorded"))
shutil.copy(FIX, proj_n)
assert V._next_take_no(proj_n, "Other Song") == 1 and V._next_take_no(None, "Song") == 1
for f in ("Other Song Vocal take 7.wav", "Another Vocal take 12.wav", "notes.txt"):     # only this song's files count
    open(os.path.join(tmp, "numbering", "Recorded", f), "wb").close()
assert V._next_take_no(proj_n, "Other Song") == 8, V._next_take_no(proj_n, "Other Song")
# popup during recording is reported, never answered
m.fl_dialogs = lambda: [{"kind": "question", "title": "Recording"}]
rp_ = TOOL("fl_vocal_record")(start_bar=5, end_bar=6, project=proj3, background=False)
assert rp_.get("popup") and "record" in rp_["stopped"] and not FL["recording"], rp_
m.fl_dialogs = lambda: []
# ---- Edison stole the take (PC 2026-10-23): setup flags Edison on the insert; record answers Edison's Save As into
# the song's folder (or cancels it for a test take) - never leaves the modal dialog; the warning says what to check
FL["fx"] = {2: ["Fruity parametric EQ 2", "Edison"]}; FL["edison_mode"] = True
SAVE_DLG = {"hwnd": 4242, "title": "Save As", "class": "#32770", "kind": "save_as", "file_dialog": True, "text": [],
            "buttons": ["Save", "Cancel"], "blocks_fl": True}
OLD_DLG = {"hwnd": 99, "title": "Project Save As", "class": "#32770", "kind": "save_as", "file_dialog": True}
m.fl_dialogs = lambda: ([SAVE_DLG] if FL.get("save_dialog") else []) + ([OLD_DLG] if FL.get("old_dialog") else [])
DLG = {"submitted": [], "cancelled": []}
m._file_dialog_info = lambda h: ({"edits": [(1, "untitled_2")], "combos": ["Microsoft wave file (*.wav)"]} if h == 4242
                                 else {"edits": [(1, "My Song")], "combos": ["FL Studio project (*.flp)"]})


def _submit(h, path):
    DLG["submitted"].append((h, path)); AE.save(path, np.zeros((4410, 1)), SR); FL["save_dialog"] = False; return True


def _cancel(h):
    DLG["cancelled"].append(h)
    if h == 4242:
        FL["save_dialog"] = False
    return True


m._file_dialog_submit = _submit; m._file_dialog_cancel = _cancel
se = TOOL("fl_vocal_setup")(project=proj3)
assert se["edison_on_insert"] == ["Edison"] and any("Edison" in w_ for w_ in se["warnings"]), se
assert "into the playlist" in se["record_mode"]
FL["old_dialog"] = True                                           # FL's own project Save As: never answered by us
re_ = TOOL("fl_vocal_record")(start_bar=2, end_bar=4, takes=1, project=proj3, background=False)
assert not FL.get("save_dialog") and DLG["submitted"] and DLG["submitted"][0][0] == 4242, (DLG, re_)
tp = re_["takes"][0]["path"]
assert os.path.dirname(tp) == os.path.normcase(os.path.join(os.path.dirname(os.path.abspath(proj3)), "Recorded"))
assert os.path.basename(tp).startswith("Live Song Vocal take") and os.path.isfile(tp) and re_["takes"][0]["source"] == "edison"
assert 99 not in DLG["cancelled"] and re_.get("edison") and any("into the playlist" in w_ for w_ in re_["warnings"]), re_
assert re_["edison_on_insert"] == ["Edison"] and not re_.get("popup"), re_
FL["old_dialog"] = False
# a test take (no song): Edison's Save As is cancelled, and the warning says exactly what to check
m._open_project_guess = lambda: (None, [])
rt_ = V._record(start_bar=2, end_bar=3, takes=1, project=None, track=2)
assert 4242 in DLG["cancelled"] and not FL.get("save_dialog") and rt_["takes"] == [], (DLG, rt_)
ww = " ".join(rt_["warnings"])
assert "cancelled Edison's Save As" in ww and "into Edison" in ww and rt_.get("edison"), ww
assert "went into Edison" in rt_["stopped"] and "into the playlist as an audio clip" in rt_["next_step"], rt_
assert "Edison" in V._save_dialogs.__doc__ and V._save_dialogs(set()) == []          # nothing open: nothing to do
# ---- PC 2026-10-23 (2nd run): FL loaded Edison onto the insert DURING the recording, no Save As at all: the take is
# only in Edison's memory -> fail fast (seconds) with the exact fix, no wait for files that will never come
FL["fx"] = {}; FL["edison_mode"] = False; m.fl_dialogs = lambda: []
_orig_fake = m.BRIDGE.call


def _edison_fake(cmd, timeout=15.0, **a):
    was_rec = FL["recording"] and FL["playing"]
    r = _orig_fake(cmd, timeout=timeout, **a)
    if cmd == "transport" and a.get("action") == "stop" and was_rec:
        FL["fx"] = {2: ["Edison"]}                               # FL's 'into Edison' record mode loads it
    return r


FL["edison_mode"] = True                                          # no file, no clip; and no Save As shows up
m.BRIDGE.call = _edison_fake
t0_ = time.time()
rf_ = V._record(start_bar=2, end_bar=3, takes=1, project=proj3, track=2)
took_ = time.time() - t0_
m.BRIDGE.call = _orig_fake; FL["fx"] = {}; FL["edison_mode"] = False; FL["save_dialog"] = False
assert rf_["takes"] == [] and rf_.get("edison") == ["Edison"] and rf_.get("edison_loaded_by_fl"), rf_
assert "went into Edison" in rf_["stopped"] and "remove Edison from the Vocal insert" in rf_["next_step"], rf_
assert took_ < 10, f"Edison take must fail fast, took {took_:.1f} s"
assert "edison_check" in rf_["steps_s"] and "scan" in rf_["steps_s"], rf_["steps_s"]
# Edison's own data folder is searched as a fallback
ud_ = os.path.join(tmp, "userdata"); os.makedirs(os.path.join(ud_, "Edison", "Data", "Samples"))
_ud_old = m._fl_userdata; m._fl_userdata = lambda: ud_
assert os.path.join(ud_, "Edison", "Data", "Samples") in V._recorded_dirs(proj3)
assert V._is_edison_dir(os.path.join(ud_, "Edison", "Data", "Samples", "untitled.wav"))
m._fl_userdata = _ud_old
# ---- H5: a word-initial /h/ (noise running straight into the vowel) is not a breath; a real breath (pause after) is
def _bandnoise(lo, hi, sec, db, seed):
    g = np.random.default_rng(seed); n = int(sec * SR)
    F = np.fft.rfft(g.standard_normal(n)); f = np.fft.rfftfreq(n, 1 / SR); F[(f < lo) | (f > hi)] = 0
    y = np.fft.irfft(F, n); y *= 10 ** (db / 20) / np.sqrt(np.mean(y ** 2))
    k = int(0.015 * SR); w = np.ones(n); w[:k] = np.linspace(0, 1, k); w[-k:] = np.linspace(1, 0, k)
    return y * w


def _vowel(f0, sec, amp=0.3):
    tt = np.arange(int(sec * SR)) / SR
    y = sum((1 / h) * np.sin(2 * np.pi * f0 * h * tt) for h in range(1, 12)) * np.exp(-((np.arange(1, 2) - 1)))
    y = amp * y / np.abs(y).max(); k = int(0.01 * SR); y[:k] *= np.linspace(0, 1, k); y[-k:] *= np.linspace(1, 0, k)
    return y


segs, marks = [], {}
def _add(name, y):
    a = sum(len(q) for q in segs); segs.append(y); marks[name] = (a, a + len(y))
_add("s0", np.zeros(int(0.6 * SR))); _add("h1", _bandnoise(300, 6000, 0.16, -32, 1)); _add("v1", _vowel(196, 0.5))
_add("s1", np.zeros(int(0.5 * SR))); _add("h2", _bandnoise(300, 6000, 0.12, -32, 2)); _add("v2", _vowel(220, 0.5))
_add("s2", np.zeros(int(0.4 * SR))); _add("br", _bandnoise(500, 5000, 0.35, -36, 3)); _add("s3", np.zeros(int(0.4 * SR)))
_add("v3", _vowel(220, 0.6)); _add("s4", np.zeros(int(0.5 * SR)))
hx = np.concatenate(segs) + np.random.default_rng(9).normal(0, 10 ** (-70 / 20), sum(len(q) for q in segs))
hp = wav("h_vowel.wav", hx)
for mode in ("reduce", "remove"):
    rc = TOOL("fl_vocal_clean")(wav=hp, output=os.path.join(tmp, f"h_clean_{mode}.wav"), noise=False, breaths=mode)
    hy = V._load_wav(rc["output"])[0][:, 0]
    ch = {k: 10 * np.log10(np.mean(hy[a:b] ** 2) / np.mean(hx[a:b] ** 2)) for k, (a, b) in marks.items() if not k.startswith("s")}
    print(f"h+vowel ({mode}):", {k: round(v, 1) for k, v in ch.items()}, rc["found"]["breaths"].get("spans_s"))
    assert ch["h1"] > -3.5 and ch["h2"] > -3.5, ("/h/ cut", ch)            # the consonant survives
    assert ch["br"] < -8, ("real breath not reduced", ch)
    assert all(abs(ch[k]) < 0.5 for k in ("v1", "v2", "v3")), ch
# L4: DC is never "voiced"; YIN tracks a take with no silence (a sustained note)
fy_dc, _ = V.yin(np.full(SR, 0.5), SR)
assert not np.isfinite(fy_dc).any(), "DC read as a pitch"
_tt = np.arange(int(1.5 * SR)) / SR
fy_s, _ = V.yin(0.3 * sum(np.sin(2 * np.pi * 220 * h * _tt) / h for h in range(1, 6)), SR)
assert np.isfinite(fy_s).mean() > 0.8 and abs(1200 * np.log2(np.nanmedian(fy_s) / 220)) < 20, np.isfinite(fy_s).mean()
if "pitch_track" in ns:
    trdc = ns["pitch_track"](np.full(SR, 0.5), SR)
    assert not np.any(np.asarray(trdc["f0"]) > 0), "transcribe tracker: DC voiced"
# L5: low staccato sung notes (steady 90 Hz pitch after silence) are not plosives
def _lowv(f0, dur, amp=0.3):
    tt = np.arange(int(dur * SR)) / SR; y = sum(np.sin(2 * np.pi * h * f0 * tt) / h ** 1.5 for h in range(1, 30))
    y = amp * y / np.abs(y).max(); k = int(0.008 * SR); y[:k] *= np.linspace(0, 1, k); y[-k:] *= np.linspace(1, 0, k)
    return y
_seg = []
for _i in range(4):
    _seg += [np.zeros(int(0.4 * SR)), _lowv(90, 0.12)]
_seg += [np.zeros(int(0.3 * SR)), _lowv(90, 1.0), np.zeros(int(0.3 * SR))]
_lx = np.concatenate(_seg) + np.random.default_rng(5).normal(0, 10 ** (-70 / 20), sum(map(len, _seg)))
_rl = TOOL("fl_vocal_clean")(wav=wav("low_staccato.wav", _lx), noise=False, breaths="keep", sibilance=False, clicks=False)
assert _rl["found"]["plosives"]["count"] == 0, _rl["found"]["plosives"]
# chain apply: loads missing stock plugins from FL's plugin database into free slots, sets controls by name
pdb = os.path.join(tmp, "Plugin database", "Installed", "Effects", "Fruity"); os.makedirs(pdb)
for nm in ("Fruity Parametric EQ 2", "Fruity Compressor", "Fruity Limiter", "Fruity Multiband Compressor",
           "Fruity Blood Overdrive"):
    open(os.path.join(pdb, nm + ".fst"), "wb").write(b"x")
os.environ["FLH_PLUGIN_DB"] = os.path.join(tmp, "Plugin database")
edits = []
FL["slot_plugin"] = {}


def fake_edit(project, ops, output=None):
    edits.append((project, ops))
    for o in ops:
        FL["slot_plugin"][o["slot"]] = os.path.splitext(os.path.basename(o["preset"]))[0]
    return {"ops": len(ops)}


def plugin_controls(name):
    mp = m.plugin_map(name) if "plugin_map" in ns else None
    return (mp or {}).get("controls", {})


base_call = fake_call


def fake_call2(cmd, timeout=15.0, **a):
    if cmd == "plugin_params":
        name = FL["slot_plugin"].get(a.get("slot"), "Fruity Parametric EQ")
        ctl = plugin_controls(name)
        names = sorted({e["fl_name"] for e in ctl.values() if e.get("fl_name")}) or ["Band 1 freq", "Threshold"]
        return {"plugin": name, "count": len(names),
                "params": [{"param": i, "name": n, "value": 0.5, "text": "0"} for i, n in enumerate(names)
                           if a.get("filter", "").lower() in n.lower()]}
    if cmd == "plugin_set":
        name = FL["slot_plugin"].get(a.get("slot"))
        names = sorted({e["fl_name"] for e in plugin_controls(name).values() if e.get("fl_name")})
        e = next((e for e in plugin_controls(name).values() if e.get("fl_name") == names[a["param"]]), {})
        opts = e.get("options") or ["?"]
        FL["set"].append(("enum", a.get("slot"), names[a["param"]], a["value"]))
        return {"param": a["param"], "value": a["value"], "text": opts[min(len(opts) - 1, int(round(a["value"] * (len(opts) - 1))))]}
    if cmd == "plugin_set_value":
        FL["set"].append(("param", a["index"], a.get("slot"), a["param"], a["target"]))
        FL.setdefault("knobs", {})[(a["index"], a.get("slot"), a["param"])] = f"{a['target']:g}"
        return {"param": a["param"], "text": f"{a['target']:g}"}
    return base_call(cmd, timeout, **a)


m.BRIDGE.call = fake_call2
m.flp_edit = fake_edit
proj4 = os.path.join(tmp, "Apply.flp"); shutil.copy(FIX, proj4)
plan = TOOL("fl_vocal_chain")(take=sibby, style="pop", bpm=100, apply=True, track=0, project=proj4)
ap = plan["applied"]
ops = edits[-1][1]
slots = [o["slot"] for o in ops]
assert all(o["tool"] == "flp_load_plugin" and o["track"] == 0 for o in ops) and not {0, 1, 9} & set(slots), ops
assert len(ops) == 6 and any(o["preset"].endswith("Fruity Multiband Compressor.fst") for o in ops), ops
assert "plugin_map" in ns, "flh_mixeng is loaded in the full server"
assert all(x["via"] == "plugin_set_named" for x in ap["set"]) and sum(x.get("set", 0) for x in ap["set"]) >= 10, ap["set"]
eq_sets = [x for x in FL["set"] if x[0] == "enum" and x[1] == slots[0] and x[2] == "Band 1 type"]
assert eq_sets, "the high-pass band type was set through the map's enum"
assert any("slope" in b_ for b_ in ap["by_hand"]) and "intent_check" in ap, ap
# without flh_mixeng: param_hints fallback; enums are left to the user
saved = {k: ns.pop(k) for k in ("plugin_set_named", "intent_verify") if k in ns}
FL["set"].clear(); edits.clear(); FL["slot_plugin"].clear()
proj5 = os.path.join(tmp, "Apply2.flp"); shutil.copy(FIX, proj5)
ap2 = TOOL("fl_vocal_chain")(take=sibby, style="pop", bpm=100, apply=True, track=0, project=proj5)["applied"]
assert all(x["via"] == "fl_plugin_set_value" for x in ap2["set"]), ap2["set"]
assert any(x[0] == "param" for x in FL["set"]) and not any(x[0] == "enum" for x in FL["set"])
assert any("band1_type = High pass" in b_ for b_ in ap2["by_hand"]), ap2["by_hand"]
ns.update(saved)
# a slot holding another plugin is never written by guesswork
FL["slot_plugin"] = {k: "Fruity Delay 3" for k in range(10)}
m.flp_edit = lambda project, ops, output=None: {"ops": len(ops)}
ap3 = TOOL("fl_vocal_chain")(take=sibby, style="pop", bpm=100, apply=True, track=0, project=proj5)["applied"]
assert all("error" in x for x in ap3["set"]), ap3["set"]
# intents are checked with flh_mixeng intent_verify on before/after listens of the vocal alone
FL["slot_plugin"].clear(); m.flp_edit = fake_edit
listens = []
m.fl_listen = lambda start_bar=None, seconds=None, **k: listens.append(seconds) or {"heard": True, "whole": {"lufs": -20.0}}
got = {}
m.intent_verify = lambda before, after, intents: got.update(before=before, after=after, intents=intents) or {"all_ok": True}
ap4 = TOOL("fl_vocal_chain")(take=sibby, style="pop", bpm=100, apply=True, track=0, project=proj4, verify_listen=6)["applied"]
assert listens == [6, 6] and got["intents"][:2] == ["less_muddy", "less_harsh"] and ap4["intent_check"] == {"all_ok": True}, (listens, got)
# M7: a same-named plugin the USER put on the insert is left alone (new one into an empty slot), unless replace=true
FL["slot_plugin"] = {3: "Fruity Parametric EQ 2"}; FL["set"].clear(); edits.clear()
proj6 = os.path.join(tmp, "Apply3.flp"); shutil.copy(FIX, proj6)
real_sp = V._slot_plugins
V._slot_plugins = lambda project, track: {k: FL["slot_plugin"].get(k) for k in range(10)}
ap5 = TOOL("fl_vocal_chain")(take=sibby, style="pop", bpm=100, apply=True, track=0, project=proj6)["applied"]
assert ap5.get("kept_yours") and "slot 3" in ap5["kept_yours"][0], ap5
assert not any(x[1] == 3 for x in FL["set"] if x[0] == "enum"), "the user's EQ in slot 3 was written"
assert "revert_last" in ap5.get("undo", ""), ap5.get("undo")
FL["set"].clear()
rv_ = TOOL("fl_vocal_chain")(revert_last=True)
assert rv_["reverted"] > 0 and FL["set"], rv_
ap6 = TOOL("fl_vocal_chain")(take=sibby, style="pop", bpm=100, apply=True, track=0, project=proj6, replace=True)["applied"]
assert not ap6.get("kept_yours"), ap6
V._slot_plugins = real_sp
ns.update(saved)
real_pt = ns.get("pitch_track")
m.pitch_track = lambda x, sr: {"times": np.arange(0, len(x) / sr, 0.01), "f0": np.full(int(len(x) / sr / 0.01) + 1, 220.0)}
f0s, _, srcname = V.pitch_track(np.zeros(SR), SR)
assert srcname == "flh_transcribe" and np.nanmedian(f0s) == 220.0
if real_pt:                                                       # the real flh_transcribe tracker through the adaptor
    m.pitch_track = real_pt
    f0r, _, srcname = V.pitch_track(voice([(0.2, 1.0, 69)], 1.5), SR)
    assert srcname == "flh_transcribe" and abs(1200 * np.log2(np.nanmedian(f0r[30:100]) / 440)) < 5, srcname
else:
    del m.pitch_track

print(f"{time.time() - T0:.1f}s")
# ---- F11: long offline vocal jobs (comp / clean / double) don't hold the FL lock
import threading  # noqa: E402
import flh_vocal as _V  # noqa: E402
_probe = []


def _lockprobe(**kw):
    def p():
        got = m._FL_LOCK.acquire(blocking=False)
        _probe.append(got)
        if got:
            m._FL_LOCK.release()
    th = threading.Thread(target=p); th.start(); th.join()
    return {"ok": True}


_saved = (_V.vocal_comp, _V.vocal_clean, _V.vocal_double)
_V.vocal_comp = _V.vocal_clean = _V.vocal_double = _lockprobe
for j in (_V.fl_vocal_comp(takes=[], background=True), _V.fl_vocal_clean(wav="x.wav", background=True),
          _V.fl_vocal_double(wav="x.wav", background=True)):
    r_ = m.fl_job(j["job"], wait_s=20)
    assert r_["status"] == "done" and _probe[-1] is True, (j, r_, _probe)
_V.vocal_comp, _V.vocal_clean, _V.vocal_double = _saved
print("offline vocal jobs ok")
print("ok")
