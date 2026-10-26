"""flh_transcribe: audio -> MIDI on synthetic audio (melody with vibrato/glides, octave traps, chords, drums, bass,
auto), MIDI round trip, writing into a project, and the FL capture paths with a fake FL.
Run: python3 tests/test_transcribe.py   (prints the accuracy numbers, then 'ok')"""
import json, os, shutil, sys, tempfile, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import fl_headless_mcp as m  # noqa: E402
import flh_transcribe as T  # noqa: E402
ns = vars(m); T.register(ns)
for t in ("audio_to_midi", "fl_capture_idea", "fl_capture_probe", "capture_from_midi_file", "capture_and_build"):
    assert t in ns["TOOLS"], t
    import inspect
    sig = inspect.signature(ns["TOOLS"][t][0].__wrapped__).parameters
    sch = ns["TOOLS"][t][2]
    assert set(sch["properties"]) <= set(sig), (t, set(sch["properties"]) - set(sig))
    assert all(r in sig for r in sch["required"]), t
assert ns["pitch_track"] is T.pitch_track and ns["transcribe"] is T.audio_to_midi
FS = 44100
RNG = np.random.default_rng(7)
REPORT = {}


# ----------------------------------------------------------------------------- synthesis
def tone(f, fs=FS, amps=(1, .5, .3, .2, .1), att=0.01, rel=0.03):
    ph = 2 * np.pi * np.cumsum(f) / fs
    y = sum(a * np.sin((h + 1) * ph) for h, a in enumerate(amps))
    n = len(y); e = np.ones(n); a = int(att * fs); r = int(rel * fs)
    e[:a] = np.linspace(0, 1, a); e[n - r:] = np.linspace(1, 0, r)
    return y * e


def legato_line(notes, vib=(5.5, 0.35), glide_s=0.06, amps=(1, .5, .3, .2, .1), fs=FS, gap_after=()):
    """notes [(start, dur, midi, gain)] played as one continuous voice: glides of glide_s into each new pitch when
    the notes touch, vibrato after 150 ms, gaps where the score has them."""
    end = max(s + d for s, d, _, _ in notes) + 0.3
    n = int(end * fs); t = np.arange(n) / fs
    pitch = np.zeros(n); gain = np.zeros(n)
    for i, (s, d, k, g) in enumerate(notes):
        a, b = int(s * fs), int((s + d) * fs)
        tt = t[a:b] - s
        p = k + vib[1] * np.sin(2 * np.pi * vib[0] * tt) * np.clip((tt - 0.15) / 0.1, 0, 1)
        if i and abs(notes[i - 1][0] + notes[i - 1][1] - s) < 1e-6 and glide_s:   # touching: glide in
            prev = notes[i - 1][2]; w = np.clip(tt / glide_s, 0, 1)
            p = prev + (k - prev) * (0.5 - 0.5 * np.cos(np.pi * w)) + (p - k) * w
        pitch[a:b] = p; gain[a:b] = g
    on = gain > 0
    sm = int(0.008 * fs)                                          # 8 ms gain ramps at note edges
    gs = np.convolve(gain, np.ones(sm) / sm, mode="same")
    f = 440 * 2 ** ((np.where(on, pitch, 60) - 69) / 12)
    ph = 2 * np.pi * np.cumsum(f) / fs
    y = sum(a_ * np.sin((h + 1) * ph) for h, a_ in enumerate(amps)) * gs
    return y + 0.002 * RNG.standard_normal(len(y))


def kick(fs=FS, dur=0.3):
    t = np.arange(int(dur * fs)) / fs
    return np.sin(2 * np.pi * np.cumsum(50 + 100 * np.exp(-t / 0.03)) / fs) * np.exp(-t / 0.12)


def _filt(x, lo, hi, fs=FS):
    N = 1 << int(np.ceil(np.log2(len(x) * 2))); S = np.fft.rfft(x, N); f = np.fft.rfftfreq(N, 1 / fs)
    S[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(S, N)[:len(x)]


def snare(fs=FS, dur=0.2):
    t = np.arange(int(dur * fs)) / fs
    nz = _filt(np.random.default_rng(1).standard_normal(len(t)), 900, 9000)
    return 0.5 * np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.05) + 0.6 * nz / np.abs(nz).max() * np.exp(-t / 0.06)


def hat(fs=FS, dur=0.06):
    t = np.arange(int(dur * fs)) / fs
    nz = _filt(np.random.default_rng(2).standard_normal(len(t)), 7000, 20000)
    return 0.6 * nz / np.abs(nz).max() * np.exp(-t / 0.015)


def tom(fs=FS, dur=0.35, f0=140):
    t = np.arange(int(dur * fs)) / fs
    return np.sin(2 * np.pi * np.cumsum(f0 * (1 + 0.3 * np.exp(-t / 0.04))) / fs) * np.exp(-t / 0.15)


def drum_loop(bpm, bars, pattern=None, humanize_ms=0.0, seed=0, lead_s=0.0):
    rng = np.random.default_rng(seed)
    pattern = pattern or {"kick": "x.......x.x.....", "snare": "....x.......x...", "hat": "x.x.x.x.x.x.x.x."}
    st = 60 / bpm / 4; L = int((bars * 16 * st + 0.6 + lead_s) * FS); y = np.zeros(L); hits = []
    gen = {"kick": kick(), "snare": snare(), "hat": hat(), "perc": tom()}
    gain = {"kick": 0.9, "snare": 0.7, "hat": 0.35, "perc": 0.6}
    for b in range(bars):
        for part, p in pattern.items():
            for i, c in enumerate(p):
                if c == "x":
                    t = lead_s + (b * 16 + i) * st + (rng.normal(0, humanize_ms / 1000) if humanize_ms else 0)
                    t = max(0.0, t); a = int(t * FS); s = gen[part]; e = min(L, a + len(s))
                    y[a:e] += gain[part] * s[:e - a]; hits.append((t, part))
    return 0.5 * y, sorted(hits)


QUAL = {"": [0, 4, 7], "m": [0, 3, 7], "7": [0, 4, 7, 10], "dim": [0, 3, 6], "sus4": [0, 5, 7], "maj7": [0, 4, 7, 11],
        "m7": [0, 3, 7, 10]}


def chord_audio(prog, bpm, beats_per=4, tune_cents=0, seed=0):
    rng = np.random.default_rng(seed)
    bs = 60 / bpm; seg = beats_per * bs; y = np.zeros(int(len(prog) * seg * FS) + FS); truth = []
    for i, ch in enumerate(prog):
        root, q = T._parse_chord(ch)
        keys = [48 + root] + [60 + ((root + iv) % 12) for iv in QUAL[q]]
        n = int(seg * FS); t = np.arange(n) / FS
        for k in keys:
            f = 440 * 2 ** ((k - 69 + tune_cents / 100) / 12) * (1 + rng.normal(0, 0.0005))
            s = sum((0.6 ** h) * np.sin(2 * np.pi * f * (h + 1) * t + rng.uniform(0, 6)) for h in range(6))
            env = np.minimum(1, t / 0.01) * np.exp(-t / 1.5) * np.minimum(1, (seg - t) / 0.02)
            a = int(i * seg * FS); y[a:a + n] += 0.08 * s * env
        truth += [ch] * beats_per
    return y, truth


def match_notes(truth, got, tol=0.03):
    """truth [(start, dur, midi, ...)], got notes -> (pitch+onset hits, onset errors ms, wrong pitches)"""
    used = set(); hits = 0; errs = []; wrong = []
    for s, d, k, *_ in truth:
        c = [i for i, n in enumerate(got) if i not in used and abs(n["start"] - s) <= tol]
        if not c:
            continue
        i = min(c, key=lambda i: abs(got[i]["start"] - s)); used.add(i)
        if got[i]["pitch"] == k:
            hits += 1; errs.append(abs(got[i]["start"] - s) * 1000)
        else:
            wrong.append((k, got[i]["pitch"]))
    return hits, errs, wrong


# ----------------------------------------------------------------------------- 1. melody: vibrato, glides, repeats
mel = []; t = 0.3
seq = [(60, .5), (62, .25), (64, .25), (65, .75), (67, .25), (69, .5), (67, .5), (72, 1.0), (71, .25), (69, .25),
       (67, .5), (64, .5), (64, .5), (62, .25), (60, 1.0), (55, .5), (57, .5), (59, .5), (60, 1.0)]
spb = 60 / 96.0
for i, (k, beats) in enumerate(seq):
    d = beats * spb
    rest = 0.06 if i in (3, 7, 11, 14) else 0.0                   # a few detached notes; the rest legato with glides
    mel.append((t, d - rest, k, 0.25 + 0.2 * ((i * 7) % 5) / 4)); t += d
melody_audio = legato_line(mel)
t0 = time.time(); tr = T.pitch_track(melody_audio, FS); got = T.segment_notes(tr); ms = (time.time() - t0) * 1000
hits, errs, wrong = match_notes(mel, got)
prec = hits / max(len(got), 1); rec = hits / len(mel)
REPORT["melody (vibrato+glides)"] = f"{hits}/{len(mel)} notes right (pitch + onset<=30 ms), precision {prec:.2f}, " \
                                   f"onset err mean {np.mean(errs):.1f} ms max {max(errs):.1f} ms, {ms:.0f} ms CPU"
assert rec >= 0.9 and prec >= 0.85, (hits, len(mel), len(got), wrong, [(n['start'], n['pitch']) for n in got])
assert max(errs) <= 30, errs
# repeated same pitch (64 64) stays two notes; velocity follows level
g64 = [n for n in got if n["pitch"] == 64]
assert len(g64) >= 3, g64
loud = sorted(zip([g for *_, g in mel], [n["vel"] for n in got[:len(mel)]]))
assert np.corrcoef([a for a, _ in loud], [b for _, b in loud])[0, 1] > 0.5, loud

# ----------------------------------------------------------------------------- 2. octave traps
cases = {"weak fundamental": (1 / 0.05, (0.05, 1.0, 0.6, 0.5, 0.3)), "even harmonics strong": (1, (0.15, 1.0, 0.1, 0.8, 0.05, 0.4)),
         "pure sine": (1, (1.0,)), "bright saw": (1, tuple(1 / h for h in range(1, 13)))}
oct_ok = 0; oct_n = 0
for name, (_, amps) in cases.items():
    for k in (45, 57, 64, 76):
        sig = tone(np.full(int(0.6 * FS), 440 * 2 ** ((k - 69) / 12)), amps=amps)
        sig = np.concatenate([np.zeros(int(0.1 * FS)), sig, np.zeros(int(0.1 * FS))])
        ns_ = T.segment_notes(T.pitch_track(sig, FS, fmin=40))
        oct_n += 1
        if len(ns_) == 1 and ns_[0]["pitch"] == k:
            oct_ok += 1
        else:
            print("  octave case failed:", name, k, [(n["pitch"], n["start"]) for n in ns_])
REPORT["octave traps"] = f"{oct_ok}/{oct_n} tones at the right octave (weak fundamental, strong even harmonics, sine, saw)"
assert oct_ok >= oct_n - 1, oct_ok
# a single-frame octave flip in the middle of a note is smoothed away
sig = tone(np.full(int(0.8 * FS), 220.0), amps=(1, .6, .4))
burst = int(0.4 * FS); sig[burst:burst + 400] += 0.8 * np.sin(2 * np.pi * 440 * np.arange(400) / FS)
nt = T.segment_notes(T.pitch_track(sig, FS))
assert len(nt) == 1 and nt[0]["pitch"] == 57, nt

# ----------------------------------------------------------------------------- 3. chords
prog = ["C", "Am", "F", "G7", "Dm7", "Fmaj7", "Bdim", "Gsus4", "Em", "A7", "Dm", "G", "Cmaj7", "Am7", "Eb", "Bb"]
accs = []
for tc in (0, 35):
    ya, truth = chord_audio(prog, 100, tune_cents=tc)
    r = T.audio_to_midi((ya, FS), mode="chords", tempo=100, full=True)
    lab = r["parts"]["chords"]["per_beat"][:len(truth)]
    acc = np.mean([a == b for a, b in zip(lab, truth)]); accs.append(acc)
    assert acc > 0.85, (tc, acc, [(b, a) for a, b in zip(lab, truth) if a != b])
    assert abs(r["parts"]["chords"]["tuning_cents"] - tc) <= 8, r["parts"]["chords"]["tuning_cents"]
REPORT["chords (given tempo)"] = f"beat labels right: {accs[0]:.0%} in tune, {accs[1]:.0%} at +35 cents (16 chords, 7 qualities)"
assert r["parts"]["chords"]["chart"].startswith("| C | Am | F | G7 |"), r["parts"]["chords"]["chart"]
# voiced chord track: every chord's notes are its chord tones
vt = r["notes"]["chords"]
first_bar = sorted({k % 12 for b, l, k, v in vt if b < 1.99})
assert first_bar == [0, 4, 7], first_bar
# auto mode, tempo not given: the chart (sequence of changes) is right even if the beat grid is a guess
ya, truth = chord_audio(["Am", "F", "C", "G", "Am", "F", "C", "G"], 120, seed=3)
ra = T.audio_to_midi((ya, FS), mode="auto")
assert ra["detected"]["kind"] == "chords", ra["detected"]
seq_ = [c for c in ra["parts"]["chords"]["chart"].replace("|", " ").split() if c != "-"]
dedup = [c for i, c in enumerate(seq_) if not i or seq_[i - 1] != c]
assert dedup[:8] == ["Am", "F", "C", "G", "Am", "F", "C", "G"], (dedup, ra["tempo"])
assert any("polyphonic" in w for w in ra["warnings"]), ra.get("warnings")
REPORT["chords (auto, no tempo)"] = f"chart {' '.join(dedup[:8])} (truth Am F C G x2), tempo {ra['tempo']['bpm']}, " \
                                    f"confidence {ra['parts']['chords']['confidence']}"


# ----------------------------------------------------------------------------- 4. drums
def drum_score(hits, got, tol=0.03):
    used = set(); ok = 0; errs = []
    for t_, c in hits:
        cand = [i for i, h in enumerate(got) if h["cls"] == c and abs(h["t"] - t_) < tol and i not in used]
        if cand:
            i = min(cand, key=lambda i: abs(got[i]["t"] - t_)); used.add(i); ok += 1; errs.append(abs(got[i]["t"] - t_) * 1000)
    return ok / len(hits), ok / max(len(got), 1), errs


rows = []
for bpm, hum, pat in ((92, 0, None), (100, 0, None), (120, 0, {"kick": "x...x...x...x...", "snare": "....x.......x...",
                                                             "hat": "x.x.x.x.x.x.x.x."}),
                      (128, 4, None), (140, 0, None),
                      (110, 0, {"kick": "x.........x.....", "snare": "....x.......x...", "hat": "xxxxxxxxxxxxxxxx",
                                "perc": "..............xx"})):
    yd, hits = drum_loop(bpm, 8, pattern=pat, humanize_ms=hum, seed=bpm)
    yw, fsw = T.work_rate(yd, FS)
    r = T.drums_transcribe(yw, fsw)
    rec, prec, errs = drum_score(hits, r["hits"])
    per = {}
    for c in {c for _, c in hits}:
        sub = [h for h in hits if h[1] == c]
        per[c] = round(drum_score(sub, [h for h in r["hits"] if h["cls"] == c])[0], 2)
    rows.append((bpm, hum, r["grid"]["bpm"], rec, prec, max(errs), per, r["downbeat_beat"]))
    assert abs(r["grid"]["bpm"] - bpm) <= 1.0, (bpm, r["grid"])
    assert rec >= 0.9 and prec >= 0.9, (bpm, rec, prec, per)
    assert max(errs) <= 30, errs
    assert r["downbeat_beat"] == 0, (bpm, r["downbeat_beat"])
REPORT["drums"] = "; ".join(f"{b} BPM{' hum ' + str(h) + 'ms' if h else ''}: tempo {g}, recall {rc:.2f}, precision {p:.2f}, "
                            f"max onset err {e:.1f} ms, per class {pc}" for b, h, g, rc, p, e, pc, d in rows)
# GM notes, quantize to the grid, tempo given
yd, hits = drum_loop(120, 4)
r = T.audio_to_midi((yd, FS), mode="drums", quantize="1/16")
dn = r["notes"]["drums"]
assert {k for _, _, k, _ in dn} == {36, 38, 42}, {k for _, _, k, _ in dn}
assert all(abs((b - 1) * 16 - round((b - 1) * 16)) < 1e-6 for b, *_ in dn), dn[:5]   # on the 1/16 grid (bars)
assert r["parts"]["drums"]["counts"] == {"kick": 12, "snare": 8, "hat": 32}, r["parts"]["drums"]["counts"]
assert r["align"] == "grid" and abs(r["tempo"]["bpm"] - 120) < 0.5
# humanization kept by default (no quantize): positions off the grid survive
yd, hits = drum_loop(100, 4, humanize_ms=8, seed=5)
r2 = T.audio_to_midi((yd, FS), mode="drums", tempo=100)
offs = [abs((b - 1) * 16 - round((b - 1) * 16)) for b, *_ in r2["notes"]["drums"]]
assert max(offs) > 0.05 and r2["align"] == "audio", max(offs)
# lead-in silence + grid alignment: bar 1 lands on the first downbeat
yd, hits = drum_loop(120, 4, lead_s=0.37)
r3 = T.audio_to_midi((yd, FS), mode="drums", quantize="1/16")
assert r3["notes"]["drums"][0][0] == 1.0 and abs(r3["audio_starts_at_beat"] + 0.74) < 0.05, (r3["notes"]["drums"][:2], r3["audio_starts_at_beat"])

# ----------------------------------------------------------------------------- 5. bass line
bl = []; t = 0.2
for i, k in enumerate([28, 28, 31, 33, 35, 36, 35, 31, 28, 40, 38, 33]):
    d = 0.3 if i % 2 else 0.45
    bl.append((t, d - 0.04, k, 0.5)); t += d
bass_audio = legato_line(bl, vib=(5, 0.0), glide_s=0, amps=(1, .7, .5, .35, .25, .15, .1))
rb = T.audio_to_midi((bass_audio, FS), mode="bass", tempo=100, full=True)
gotb = [{"start": (row[0] - 1) * 4 * 0.6, "pitch": row[2]} for row in rb["notes"]["bass"]]
hb, eb, wb = match_notes(bl, gotb)
REPORT["bass"] = f"{hb}/{len(bl)} notes right (E1-E2 range), onset err max {max(eb):.1f} ms"
assert hb >= len(bl) - 1 and len(gotb) <= len(bl) + 1, (hb, wb, gotb)
ra = T.audio_to_midi((bass_audio, FS))
assert ra["detected"]["kind"] == "bass", ra["detected"]

# ----------------------------------------------------------------------------- 6. auto decides (and says how sure)
auto = {}
for name, sig, want in (("melody", melody_audio, "melody"), ("drums", drum_loop(120, 4)[0], "drums"),
                        ("bass", bass_audio, "bass"), ("chords", chord_audio(prog[:6], 100)[0], "chords")):
    d = T.classify(*T.work_rate(sig, FS))
    auto[name] = (d["kind"], d["confidence"])
    assert d["kind"] == want, (name, d)
# melody mode on chords: says it sounds polyphonic instead of passing off a virtual pitch as a tune
rp = T.audio_to_midi((chord_audio(prog[:4], 100)[0], FS), mode="melody")
assert rp["parts"]["melody"]["confidence"] <= 0.3 and any("polyphonic" in w for w in rp["warnings"]), rp
# speed: one minute of melody
long_ = np.tile(melody_audio, int(np.ceil(60 * FS / len(melody_audio))))[:60 * FS]
t0 = time.time(); rl = T.audio_to_midi((long_, FS), mode="melody", tempo=96); sp = time.time() - t0
REPORT["speed"] = f"60 s melody transcribed in {sp:.1f} s ({rl['steps_s']})"
assert sp < 20, sp
REPORT["auto"] = ", ".join(f"{k} -> {v[0]} ({v[1]})" for k, v in auto.items())
# a mix of drums + chords is called a mix and both are transcribed
ymix = chord_audio(["C", "F", "G", "C"], 120)[0][:int(8 * FS)]
ymix = ymix + drum_loop(120, 4)[0][:len(ymix)] if len(ymix) <= len(drum_loop(120, 4)[0]) else ymix
rmix = T.audio_to_midi((ymix, FS))
assert rmix["detected"]["kind"] in ("mix", "drums"), rmix["detected"]

# ----------------------------------------------------------------------------- 7. MIDI file round trip + WAV input
mid = os.path.join(tmp, "melody.mid")
wav = os.path.join(tmp, "melody.wav")
import wave
with wave.open(wav, "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(FS)
    pcm = (np.clip(melody_audio / np.abs(melody_audio).max() * 0.8, -1, 1) * 32767).astype("<i2")
    w.writeframes(np.stack([pcm, pcm], 1).tobytes())
r = ns["TOOLS"]["audio_to_midi"][0](input=wav, output_mid=mid, mode="melody", tempo=96)
json.dumps(r)                                                     # tool results are plain JSON
div, raw, tm = m.midi_read(mid)
assert tm and abs(tm[0][1] - 96) < 0.01, tm
assert len(raw) == r["parts"]["melody"]["notes"], (len(raw), r["parts"])
back = [(round(s / div, 3), k) for s, e, k, v in raw]
inl = [(round((row[0] - 1) * 4, 3), row[2]) for row in r["notes"]["melody"]]
assert all(abs(a[0] - b[0]) <= 0.002 and a[1] == b[1] for a, b in zip(back, inl)), (back[:4], inl[:4])
c = T.capture_from_midi_file(mid)
assert c["notes"] == len(raw) and c["analysis"]["key"] in ("C major", "G major", "A minor"), c["analysis"]
assert c["idea"]["bpm"] == 96 and c["idea"]["notes"][0][2] == 60, c["idea"]
try:
    T.write_midi(os.path.join(tempfile.gettempdir(), "outside_roots.mid"), {"melody": [(0, 1, 60, 90)]}, 120)
    raise SystemExit("write outside FLH_ROOTS must be refused")
except (PermissionError, ValueError, RuntimeError):
    pass

# ----------------------------------------------------------------------------- 8. into a project (one flp_edit)
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
reloads = []
real_wop = m._with_open_project
def fake_wop(project, output, fn):
    if m._EDIT_DEPTH:
        return fn()
    reloads.append(output); return fn()
m._with_open_project = fake_wop
if os.path.isfile(FIX):
    proj = os.path.join(tmp, "song.flp"); shutil.copy(FIX, proj)
    before = {p["pattern"] for p in m.flp_patterns(proj)}
    yd, _ = drum_loop(120, 2)
    r = T.audio_to_midi((yd, FS), mode="drums", quantize="1/16", project=proj, channel={"kick": 0, "snare": 1, "hat": 2})
    assert len(reloads) == 1, reloads                             # one save/reload for the whole write
    pats = {p["pattern"]: p for p in m.flp_patterns(proj)}
    new = [p for k, p in pats.items() if k not in before]
    assert len(new) == 1 and new[0]["name"] == "Transcribed drums", new
    assert new[0]["notes_per_channel"] == {0: 6, 1: 4, 2: 16}, new[0]
    assert new[0]["placed_at_bars"] == [1.0], new[0]
    r = T.audio_to_midi((melody_audio, FS), mode="melody", tempo=96, project=proj, channel=4)
    names = [p["name"] for p in m.flp_patterns(proj)]
    assert "Transcribed melody" in names
    nn = m.flp_notes(proj, pattern="Transcribed melody")
    assert nn["count"] == r["parts"]["melody"]["notes"] and nn["notes"][0]["note"] == "C4", nn["notes"][:2]
    r = T.audio_to_midi((yd, FS), mode="drums", project=proj, channel=0)       # same name again -> "... 2"
    assert "Transcribed drums 2" in [p["name"] for p in m.flp_patterns(proj)]
m._with_open_project = real_wop


# ----------------------------------------------------------------------------- 9. capture from a fake FL
class FakeFL:
    """Enough of the bridge for capture: toggles, transport, pattern/channel select; 'save' writes the project
    file with what the 'user' played into the selected pattern."""
    def __init__(self, project, played, countdown=True):
        self.project = project; self.played = played; self.calls = []
        self.st = {"metronome": False, "recording": False, "song_mode": True, "countdown": False, "loop_record": True}
        self.has_countdown = countdown; self.playing = False; self.pattern = 1; self.channel = 0
        if not countdown:                                         # an older bridge: no count-in / loop-record control
            self.st["loop_record"] = False
        self.patterns = {1: "Theme_orchestra"}; self.recorded = False; self.stop_after = None

    def call(self, cmd, timeout=15.0, **a):
        self.calls.append((cmd, a.get("action")))
        if cmd == "ping":
            return {"bridge": "fl-headless"}
        if cmd == "state":
            return {"playing": self.playing, "tempo": 120.0,
                    "patterns": [{"index": i, "name": n} for i, n in sorted(self.patterns.items())]}
        if cmd == "context":
            if self.stop_after is not None:
                self.stop_after -= 1
                if self.stop_after <= 0:
                    self.playing = False                         # the user pressed stop in FL
            return {"channel": {"index": self.channel}, "pattern": {"index": self.pattern}, "playing": self.playing}
        if cmd == "pattern_set":
            if a.get("select"):
                self.pattern = a["index"]; self.patterns.setdefault(a["index"], "")
            if a.get("name") is not None:
                assert a["index"] in self.patterns, "named before it exists"
                self.patterns[a["index"]] = a["name"]
            return {"index": a["index"]}
        if cmd == "channel_set":
            if a.get("select"):
                self.channel = a["index"]
            return {"index": a["index"]}
        if cmd == "song":
            act = a["action"]
            out = dict(self.st)
            if not self.has_countdown:
                out.pop("countdown"); out.pop("loop_record")
            key = {"metronome": "metronome", "song_mode": "song_mode", "record": "recording", "countdown": "countdown",
                   "loop_record": "loop_record"}.get(act)
            if key:
                if key in ("countdown", "loop_record") and not self.has_countdown:
                    raise RuntimeError("action: where, goto, ...")
                self.st[key] = not self.st[key]
            return out
        if cmd == "transport":
            if a["action"] == "play":
                self.playing = True
                if self.st["recording"] and not self.st["song_mode"] and not self.st["loop_record"]:
                    self.recorded = True                         # FL records what the user plays
            elif a["action"] == "stop":
                self.playing = False
            elif a["action"] == "save":
                self._save()
            return {"done": a["action"]}
        raise AssertionError(cmd)

    def _save(self):
        pid = self.pattern
        have = {p["pattern"] for p in m.flp_patterns(self.project)}
        if pid not in have:
            m.flp_new_pattern(self.project, self.patterns.get(pid) or f"Pattern {pid}")
        if self.recorded:
            m.flp_add_notes(self.project, self.played, pattern=pid, channel=self.channel)
            self.recorded = False
        st_ = os.stat(self.project).st_mtime + 1
        os.utime(self.project, (st_, st_))


if os.path.isfile(FIX):
    proj = os.path.join(tmp, "cap.flp"); shutil.copy(FIX, proj)
    played = [{"note": "A3", "bar": 1, "beat": 0, "beats": 1, "velocity": 90},
              {"note": "C4", "bar": 1, "beat": 0, "beats": 1, "velocity": 80},
              {"note": "E4", "bar": 1, "beat": 0.02, "beats": 1, "velocity": 85},
              {"note": "A4", "bar": 1, "beat": 1.5, "beats": 0.5, "velocity": 100},
              {"note": "G4", "bar": 1, "beat": 2.0, "beats": 0.5, "velocity": 96},
              {"note": "F3", "bar": 2, "beat": 0, "beats": 2, "velocity": 88},
              {"note": "A3", "bar": 2, "beat": 0, "beats": 2, "velocity": 70},
              {"note": "C4", "bar": 2, "beat": 0, "beats": 2, "velocity": 75},
              {"note": "E4", "bar": 2, "beat": 2.5, "beats": 0.5, "velocity": 99}]
    fl = FakeFL(proj, played)
    real_bridge = m.BRIDGE
    m.BRIDGE = fl; ns["BRIDGE"] = fl
    m._open_project_guess = lambda: (proj, ["cap - FL Studio 2026"])
    m._fl_main_window = lambda: (1, "cap - FL Studio 2026")
    m.fl_dialogs = lambda: []
    m._open_bpb = lambda: 4
    sleeps = []
    clock = [time.time()]                                          # virtual clock: waits cost no real time

    class FakeTime:
        @staticmethod
        def time():
            return clock[0]

        @staticmethod
        def sleep(s):
            sleeps.append(s); clock[0] += s
    T.time = FakeTime
    r = ns["TOOLS"]["fl_capture_idea"][0](bars=2, channel=3)
    json.dumps(r)
    assert r["notes"] == len(played) and r["channel"] == 3 and r["pattern"] == 2, r
    assert r["pattern_name"] == "Idea 2" and fl.patterns[2] == "Idea 2"
    a = r["analysis"]
    assert a["key"].startswith("A minor") or a["key"].startswith("C major"), a
    assert a["chords_per_bar"][:2] == ["Am7", "Fmaj7"], a["chords_per_bar"]          # A C E (+G) | F A C (+E)
    assert r["idea"]["notes"][0][:3] == [1.0, 0.25, 57], r["idea"]["notes"][0]
    # FL's toggles: pattern mode + metronome + countdown + record for the take, then all back as they were
    assert fl.st == {"metronome": False, "recording": False, "song_mode": True, "countdown": False,
                     "loop_record": True}, fl.st
    assert ("transport", "play") in fl.calls and ("transport", "stop") in fl.calls
    assert sum(sleeps) >= 2 * 2.0 + 2.0 - 0.1, sum(sleeps)        # count-in bar + 2 bars at 120 BPM
    # the user stops FL early: the take ends there and says so
    fl.stop_after = 3; fl.played = played[:2]
    t_before = clock[0]
    re_ = T.fl_capture_idea(bars=8)
    assert re_.get("stopped_by_user") and re_["notes"] == 2 and clock[0] - t_before < 8, (re_, clock[0] - t_before)
    fl.stop_after = None
    # open-ended: start, then stop
    fl.played = played[:3]
    r1 = T.fl_capture_idea()
    assert r1["recording"] and fl.st["recording"] and not fl.st["song_mode"], (r1, fl.st)
    r2 = T.fl_capture_idea(action="stop")
    assert r2["notes"] == 3 and r2["pattern"] == 4 and not fl.st["recording"] and fl.st["song_mode"], (r2, fl.st)
    # nothing played -> an honest message, not a fake success
    fl.played = []
    r3 = T.fl_capture_idea(bars=1)
    assert r3["notes"] == 0 and "no notes arrived" in r3["nothing_recorded"], r3
    # fallback: the user recorded by hand in FL (selected pattern 1 already had notes; only the new ones come back)
    fl.played = played[5:]; fl.recorded = True; fl.pattern = 1; fl.channel = 0
    r4 = T.fl_capture_idea(action="read")
    assert r4["notes"] == 4 and r4["only_new_notes"] and r4["pattern"] == 1, r4
    # recording into an existing pattern returns only the new take (FL saved first for the snapshot)
    fl.played = played[:4]
    r6 = T.fl_capture_idea(bars=1, pattern=1, channel=0)
    assert r6["notes"] == 4 and r6["pattern"] == 1, r6
    # a bridge without count-in control still records (and says so in the probe)
    fl2 = FakeFL(proj, played[:2], countdown=False); m.BRIDGE = fl2; ns["BRIDGE"] = fl2
    r5 = T.fl_capture_idea(bars=1, channel=0)
    assert r5["notes"] == 2, r5
    pr = T.fl_capture_probe()
    assert pr["checks"]["bridge"] and pr["checks"]["project_saved"] and not pr["checks"]["countdown_control"], pr
    assert any("count-in" in n for n in pr["next"]), pr
    # untitled project -> refuse with the fix
    m._fl_main_window = lambda: (1, "Untitled - FL Studio 2026")
    try:
        T.fl_capture_idea(bars=1); raise SystemExit("untitled must be refused")
    except ValueError as e:
        assert "save the project" in str(e)
    m._fl_main_window = lambda: (1, "cap - FL Studio 2026")
    T.time = time
    m.BRIDGE = real_bridge; ns["BRIDGE"] = real_bridge

    # ------------------------------------------------------------------------- 10. build around it
    _composer = ns.pop("compose_around", None)             # the fallback path: no composer installed
    b = T.capture_and_build("last", what=["chords", "bass"])
    assert b["composer"] is None and b["suggestions"]["bass"]["roots_per_bar"][0] == "A2", b
    seen = {}
    def compose_around(idea, what, genre=None, project=None, **options):
        seen.update(idea=idea, what=what, genre=genre, project=project, options=options); return {"built": what}
    ns["compose_around"] = compose_around
    out = ns["TOOLS"]["capture_and_build"][0](idea=mid, what=["drums"], genre="lofi", options={"bars": 8})
    assert out == {"built": ["drums"]} and seen["genre"] == "lofi" and seen["options"] == {"bars": 8}, (out, seen)
    assert seen["idea"]["notes"] and seen["idea"]["bpm"] == 96 and seen["idea"]["beats_per_bar"] == 4
    del ns["compose_around"]

    if _composer:
        ns["compose_around"] = _composer
# ---- M6: bright major triads chart as triads (not maj7) and their chord MIDI has no extra 7th; real 7ths survive
def _tone7(midi, dur, fs=44100, nh=6):
    t_ = np.arange(int(dur * fs)) / fs; f_ = 440 * 2 ** ((midi - 69) / 12)
    return 0.15 * sum((0.6 ** (h - 1)) * np.sin(2 * np.pi * f_ * h * t_) for h in range(1, nh + 1)) * np.exp(-t_ * 1.2)


_CH = {"C": [48, 60, 64, 67], "Am": [45, 60, 64, 69], "F": [41, 60, 65, 69], "G": [43, 59, 62, 67],
       "Cmaj7": [48, 60, 64, 67, 71], "G7": [43, 59, 62, 65, 67], "Am7": [45, 60, 64, 67, 69], "Fmaj7": [41, 60, 64, 65, 69]}
for prog, want7 in ((["C", "Am", "F", "G"] * 3, False), (["Cmaj7", "Am7", "Fmaj7", "G7"] * 3, True)):
    fs_ = 44100; beat_ = 0.6; yy = np.zeros(int(len(prog) * 4 * beat_ * fs_) + fs_)
    for i, c in enumerate(prog):
        for k in range(4):
            s0 = int((i * 4 + k) * beat_ * fs_); seg = sum(_tone7(q, beat_) for q in _CH[c]); yy[s0:s0 + len(seg)] += seg
    rr = T.audio_to_midi((np.stack([yy, yy], 1) / np.abs(yy).max() * 0.5, fs_), mode="chords", tempo=100, full=True)
    chart = rr["parts"]["chords"]["chart"]
    print("chords:", prog[:4], "->", chart[:80])
    if not want7:
        assert "maj7" not in chart and "C" in chart, chart
        cn = rr["notes"]["chords"]
        assert not any(n[2] % 12 == 11 for n in cn if n[0] < 2), [n for n in cn if n[0] < 2]   # no B under the C
    else:
        assert "Cmaj7" in chart and "G7" in chart and "Am7" in chart, chart
assert [n[2] for n in T.voice_chords([(0, 4, "Cmaj7")], sevenths=False)][1:] == [n[2] for n in T.voice_chords([(0, 4, "C")])][1:]
assert T.estimate_key(np.zeros(12)) == (None, 0.0, 0.0)                     # silence: no NaN
assert T._time_sig_meta(3) == b"\xff\x58\x04\x03\x02\x18\x08" and T._time_sig_meta(3.5)[3:5] == b"\x07\x03"   # L8
_mid = os.path.join(tmp, "waltz.mid")
T.write_midi(_mid, {"melody": [(0, 1, 60, 90), (3, 1, 62, 90)]}, 120, beats_per_bar=3)
assert b"\xff\x58\x04\x03\x02" in open(_mid, "rb").read()
for k, v in REPORT.items():
    print(f"{k}: {v}")
print("ok")
