"""flh_flip: fl_flip_sample on numpy-synthesized audio (chord stabs + drum hits at 90 BPM + a noisy section): tempo and
onsets found, chop cuts on onsets / the beat grid, the noisy section never picked, chops distinct, fades (no clicks),
pitch shift ratio (frequency and length), loop deterministic per seed, styles from data, dry_run writes nothing,
files only in the song folder, project writing through the server's writers (sampler channels, Flip pattern, drums,
journal) on tests/corpus/base.flp, a new song through flh_new_song (mocked to a base.flp copy), the open-project
guard registration. Run: python3 tests/test_flip.py (no FL needed)."""
import json, os, re, shutil, struct, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import numpy as np  # noqa: E402

import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_catalog, flh_score, flh_compose, flh_newsong, flh_flip as F  # noqa: E402
ns = vars(m)
for mod in (flh_store, flh_catalog, flh_score, flh_compose, flh_newsong, F):
    mod.register(ns)
assert "fl_flip_sample" in ns["TOOLS"] and ns["_OPEN_PROJECT_GUARD"]["fl_flip_sample"] == ("project", "output")
assert getattr(ns["TOOLS"]["fl_flip_sample"][0], "__wrapped__", None) is F.fl_flip_sample
assert "fl_flip_sample" not in m.ESSENTIAL                                   # unlisted: one call away (flh_tools)
BASE = os.path.join(HERE, "corpus", "base.flp")

# ---------------------------------------------------------------- synthesized sample: 90 BPM, 12 bars
fs = 44100; BPM = 90.0; beat = 60 / BPM; rng = np.random.default_rng(3)
CHORDS = [[48, 52, 55, 59], [45, 48, 52, 55], [41, 45, 48, 52], [43, 47, 50, 53],   # Cmaj7 Am7 Fmaj7 G7
          [50, 53, 57, 60], [40, 43, 47, 50]]                                        # Dm7 Em7
hz = lambda n: 440.0 * 2 ** ((n - 69) / 12)
n_all = int(fs * beat * 4 * 12) + fs
mix = np.zeros(n_all)


def add(at_s, y):
    a = int(round(at_s * fs)); b = min(n_all, a + len(y)); mix[a:b] += y[:b - a]


def chord(notes, dur):
    tt = np.arange(int(fs * dur)) / fs
    y = sum(np.sin(2 * np.pi * hz(n) * tt) + 0.3 * np.sin(4 * np.pi * hz(n) * tt) for n in notes)
    env = np.minimum(1, tt / 0.004) * np.exp(-tt * 1.2) * (tt < dur - 0.01)
    return 0.12 * y * env


def kick():
    tt = np.arange(int(fs * 0.25)) / fs
    return 0.5 * np.sin(2 * np.pi * np.cumsum(50 + 90 * np.exp(-tt * 40)) / fs) * np.exp(-tt * 14)


def snare():
    tt = np.arange(int(fs * 0.15)) / fs
    return 0.25 * rng.standard_normal(len(tt)) * np.exp(-tt * 30)


ci = 0
for bar in range(12):
    t0 = bar * 4 * beat
    for b in range(4):
        add(t0 + b * beat, kick() if b % 2 == 0 else snare())
    if 6 <= bar < 8:                                     # the noisy section: loud sustained noise, no tones
        add(t0, 0.45 * rng.standard_normal(int(4 * beat * fs)))
        continue
    for half in (0, 2):                                  # a chord every 2 beats, a stab of the next on the and-of-2
        add(t0 + half * beat, chord(CHORDS[ci % len(CHORDS)], 1.5 * beat)); ci += 1
        add(t0 + (half + 1.5) * beat, chord(CHORDS[(ci + 2) % len(CHORDS)], 0.5 * beat))
mix = mix / np.abs(mix).max() * 0.9
st = np.stack([mix, np.roll(mix, 3)], axis=1)            # stereo


def wav(path, x):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    y = (np.clip(x, -1, 1) * 32000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1] if x.ndim > 1 else 1); w.setsampwidth(2); w.setframerate(fs); w.writeframes(y.tobytes())


samples = os.path.join(tmp, "samples"); SAMPLE = os.path.join(samples, "Soul Thing.wav"); wav(SAMPLE, st)
NOISE = (6 * 4 * beat, 8 * 4 * beat)

# ---------------------------------------------------------------- analysis
x, rate, _ = m._wav_load(SAMPLE)
A = F.analyze(x, rate)
assert abs(A["bpm"] - BPM) < 1.5 or abs(A["bpm"] - 2 * BPM) < 3, A["bpm"]
A = F.analyze(x, rate, sample_bpm=BPM)
ons = [o["t"] for o in A["onsets"]]
true_on = sorted({round(bar * 4 * beat + b * beat, 4) for bar in range(12) if not 6 <= bar < 8 for b in range(4)} |
                 {round(bar * 4 * beat + h * beat, 4) for bar in range(12) if not 6 <= bar < 8 for h in (1.5, 3.5)})
hit = sum(1 for t in true_on if any(abs(t - o) < 0.016 for o in ons))   # (hits buried in the noise don't count)
assert hit >= 0.9 * len(true_on), (hit, len(true_on), ons[:20])
gp = A["phase"] % beat
assert min(gp, beat - gp) < 0.03, A["phase"]                # the beat grid starts on the beats

# ---------------------------------------------------------------- good bits
cands, rej = F.candidates(A, [beat, 1.5 * beat, 0.5 * beat])
assert rej["noisy"] > 0, rej
assert not any(NOISE[0] - 0.01 <= c["start"] < NOISE[1] - 0.05 for c in cands), "noisy section offered as a chop"
pk = F.pick(cands, 6)
assert len(pk) == 6, len(pk)
for c in pk:
    assert any(abs(c["start"] - t) < 0.016 for t in true_on), c["start"]          # cut at an onset
    gd = ((c["start"] - A["phase"]) / (beat / 2)) % 1
    assert min(gd, 1 - gd) * beat / 2 < 0.03, c["start"]                          # on the 8th grid
    assert not (NOISE[0] - 0.01 <= c["start"] < NOISE[1] - 0.02), c
    assert c["end"] - c["start"] > 0.2
for i, a in enumerate(pk):                                                         # distinct, no overlaps
    for b in pk[i + 1:]:
        assert not (a["start"] < b["end"] - 0.005 and b["start"] < a["end"] - 0.005), (a["start"], b["start"])
        assert float(np.dot(a["chroma"], b["chroma"])) <= 0.985
assert len({c["root"] for c in pk}) >= 3, [c["root"] for c in pk]

# ---------------------------------------------------------------- pitch shift + fades
tt = np.arange(fs) / fs; sine = np.stack([0.5 * np.sin(2 * np.pi * 440 * tt)] * 2, 1)
y = F.render_chop(sine, fs, 0.1, 0.9, 3)
want_len = 0.8 * fs / 2 ** (3 / 12)
assert abs(len(y) - want_len) <= 2, (len(y), want_len)
mid = y[len(y) // 4: 3 * len(y) // 4, 0]
spec = np.abs(np.fft.rfft(mid * np.hanning(len(mid)))); f0 = np.argmax(spec) * fs / len(mid)
assert abs(f0 / 440 - 2 ** (3 / 12)) < 0.01, f0
assert abs(y[0, 0]) < 1e-3 and abs(y[-1, 0]) < 1e-3 and np.abs(y).max() > 0.4      # faded: no click at either end
y0 = F.render_chop(sine, fs, 0.1, 0.9, 0, max_s=0.25)
assert len(y0) == int(0.25 * fs) and abs(y0[-1, 0]) < 1e-3
lo = F.render_chop(sine, fs, 0.1, 0.9, -2)
assert abs(len(lo) - 0.8 * fs / 2 ** (-2 / 12)) <= 2

# ---------------------------------------------------------------- pitch decisions + styles
st_soul = F._style("soul", None)[1]
s_, k_, _ = F.decide_pitch("key", st_soul, "C major", None); assert s_ == 4 and k_ == "E major", (s_, k_)
s_, k_, _ = F.decide_pitch("key", st_soul, "C major", "D major"); assert s_ == 2 or 3 <= s_ <= 5, s_
s_, k_, _ = F.decide_pitch("key", F._style("boombap", None)[1], "C major", "A minor"); assert s_ == 0, s_
s_, _, _ = F.decide_pitch("key", F._style("boombap", None)[1], "C major", "D minor"); assert s_ == 5 - 12 + 12 or s_ in (5, -7, 1, -11) or s_ == 5, s_
assert F.decide_pitch("-2", st_soul, "C major", None)[0] == -2 and F.decide_pitch("down", st_soul, None, None)[0] == -2
assert F._style(None, "lofi")[0] == "lofi" and F._style(None, "rnb")[0] == "soul" and F._style(None, "house")[0] == "boombap"
assert F._style("chipmunk", None)[0] == "soul"
for nm, s in F.data()["styles"].items():                                           # every shipped style arranges
    plan = F.arrange(pk, s, 4, BPM, 1.0, 7)
    assert plan["trig"] and all(0 <= t["at"] < 16 for t in plan["trig"]), nm
    ats = [t["at"] for t in plan["trig"]]
    assert len(ats) == len(set(ats)), (nm, ats)                                    # no two hits at once
try:
    F._style("polka", None); raise AssertionError("unknown style accepted")
except ValueError:
    pass

# ---------------------------------------------------------------- dry run: plan, nothing written
song = os.path.join(roots, "Beat", "Beat.flp"); os.makedirs(os.path.dirname(song)); shutil.copy(BASE, song)
before = sorted(os.listdir(os.path.dirname(song))); mt = os.path.getmtime(song)
r = F.fl_flip_sample(SAMPLE, project=song, genre="boombap", sample_bpm=BPM, dry_run=True, seed=11)
assert r["dry_run"] and sorted(os.listdir(os.path.dirname(song))) == before and os.path.getmtime(song) == mt
assert r["measured"]["onsets"] > 40 and r["chops"] and r["flip"]["loop"], r
assert any("responsibility" in n for n in r["notes"])
r2 = F.fl_flip_sample(SAMPLE, project=song, genre="boombap", sample_bpm=BPM, dry_run=True, seed=11)
assert r2["chops"] == r["chops"] and r2["flip"]["loop"] == r["flip"]["loop"]                 # deterministic per seed
r3 = F.fl_flip_sample(SAMPLE, project=song, genre="boombap", sample_bpm=BPM, dry_run=True)   # seed from the file
r4 = F.fl_flip_sample(SAMPLE, project=song, genre="boombap", sample_bpm=BPM, dry_run=True)
assert r3["flip"]["seed"] == r4["flip"]["seed"] and r3["flip"]["loop"] == r4["flip"]["loop"]
assert len(json.dumps(r)) < 6000, len(json.dumps(r))                                         # compact

# ---------------------------------------------------------------- write into an existing project
ns["IS_WINDOWS"] = False; m.IS_WINDOWS = False
for nm in ("Kick", "Snare", "Hat"):                                  # drum channels for flp_compose(what='drums')
    p_ = os.path.join(roots, "Beat", nm + ".wav"); wav(p_, (kick() if nm == "Kick" else snare())[:, None])
    flh_newsong.flp_load_sample(song, song, p_, name=nm, role=nm.lower())
n_before = len(m.flp_list(song)["channels"])
r = ns["TOOLS"]["fl_flip_sample"][0](sample=SAMPLE, project=song, genre="boombap", style="soul", sample_bpm=BPM,
                                     seed=5)
w = r["written"]
cdir = w["chops_folder"]
assert os.path.dirname(os.path.dirname(cdir)) == os.path.dirname(song) and os.path.basename(os.path.dirname(cdir)) == "chops"
used_files = sorted(os.listdir(cdir))
assert used_files == sorted(w["files"]) and all(f.endswith(".wav") for f in used_files)
n_used = len({t["chop"] for t in []}) or len(w["channels"])
assert len(used_files) == n_used
for f in used_files:                                                  # every chop: faded at both ends, not silent
    y, rr, _ = m._wav_load(os.path.join(cdir, f))
    assert rr == fs and y.shape[1] == 2 and abs(y[0]).max() < 2e-3 and abs(y[-1]).max() < 2e-3 and abs(y).max() > 0.05, f
chs = m.flp_list(song)["channels"]
assert len(chs) == n_before + len(w["channels"])
names = [c["name"] for c in w["channels"]]
assert names[0] == "Chop 1" and any("stutter" in n for n in names), names                  # soul has a stutter
d = m._load(song); ev = m._events(d)
cinfo = flh_compose.channels_of(d, ev)
for c in w["channels"]:
    assert cinfo[c["channel"]]["type"] == 0, cinfo[c["channel"]]
    rgb = [int(c["color"][i:i + 2], 16) for i in (1, 3, 5)]
    assert max(rgb) <= 0x60, c["color"]                                                     # dark colors
paths = [m._text16(m._payload(d, e)) for e in ev if e["id"] == m.EV_SAMPLE_PATH]
assert all(os.path.join(cdir, f) in paths for f in used_files)
pats = {p["name"]: p for p in m._patterns(d, ev).values()}
assert "Flip" in pats, list(pats)
fl_notes = [m._rec_to_note(x_) for x_ in pats["Flip"]["notes"]]
chop_ch = {c["channel"] for c in w["channels"]}
trig = [n_ for n_ in fl_notes if n_["ch"] in chop_ch]
assert trig and all(n_["key"] == F.ROOT_KEY for n_ in trig)
P = m._ppq(d)
assert all(n_["pos"] % (P // 4) == 0 for n_ in trig), sorted({n_["pos"] for n_ in trig})   # on the 16th grid
assert max(n_["pos"] + n_["len"] for n_ in trig) <= 2 * 4 * P                               # fits the 2 bars
by_pos = {}
for n_ in trig:
    by_pos.setdefault(n_["pos"], []).append(n_)
assert all(len(v) == 1 for v in by_pos.values())                                            # no collisions
assert w["drums"] and set(w["drums"]["channels"]) >= {"kick", "snare"}, w.get("drums")
assert not chop_ch & set(w["drums"]["channels"].values()), "a chop channel was used as a drum"
jr = flh_store.journal_get(song)
assert all(jr["channels"][str(c)]["role"] == "chop" for c in chop_ch)
assert r["flip"]["pitch_st"] in (3, 4, 5) and r["flip"]["style"] == "soul"
assert r["steps_s"]["total"] < 30, r["steps_s"]
# writes only inside the song folder (+ the journal next to it): nothing next to the sample
assert sorted(os.listdir(samples)) == ["Soul Thing.wav"]
# a second flip of the same sample: its own folder, its own pattern; the first chops stay
r5 = F.fl_flip_sample(SAMPLE, project=song, genre="boombap", style="lofi", sample_bpm=BPM, seed=5, drums=False)
assert r5["written"]["chops_folder"] != cdir and sorted(os.listdir(cdir)) == used_files and r5["written"]["pattern"] == "Flip 2"
assert r5["flip"]["pitch_st"] == -2 and r5["chops"][0]["shift_st"] == -2

# the open-project guard: a write goes through _with_open_project(project, project) once; a dry run never does
calls = []
real_wop = m._with_open_project
m._with_open_project = lambda pr, out, fn, *a, **k: (calls.append((pr, out)), fn())[1]
try:
    ns["TOOLS"]["fl_flip_sample"][0](sample=SAMPLE, project=song, sample_bpm=BPM, dry_run=True)
    assert calls == [], calls
    ns["TOOLS"]["fl_flip_sample"][0](sample=SAMPLE, project=song, sample_bpm=BPM, drums=False, bars=1)
    assert calls == [(song, song)], calls
finally:
    m._with_open_project = real_wop

# outside FLH_ROOTS: refused before anything is written
out_side = os.path.join(tmp, "elsewhere", "x.flp"); os.makedirs(os.path.dirname(out_side)); shutil.copy(BASE, out_side)
try:
    F.fl_flip_sample(SAMPLE, project=out_side, sample_bpm=BPM, drums=False); raise AssertionError("wrote outside roots")
except PermissionError:
    pass
assert os.listdir(os.path.dirname(out_side)) == ["x.flp"]

# ---------------------------------------------------------------- new song (flh_new_song mocked to a base.flp copy)
made = {}


def fake_new_song(genre=None, bpm=None, key=None, name=None, starter=True, open=True, balance=True, seed=None, **k):
    made.update(genre=genre, bpm=bpm, key=key, name=name, starter=starter, open=open)
    dest = os.path.join(roots, name, name + ".flp"); os.makedirs(os.path.dirname(dest)); shutil.copy(BASE, dest)
    for nm in ("Kick", "Snare", "Hat"):
        p_ = os.path.join(roots, name, nm + ".wav"); wav(p_, snare()[:, None])
        flh_newsong.flp_load_sample(dest, dest, p_, name=nm, role=nm.lower())
    tb = np.arange(fs) / fs; p_ = os.path.join(roots, name, "Bass.wav"); wav(p_, (0.5 * np.sin(2 * np.pi * 65.41 * tb))[:, None])
    made["bass_ch"] = flh_newsong.flp_load_sample(dest, dest, p_, name="Bass", role="bass")["channel"]
    flh_store.journal_update(dest, set={"genre": genre, "bpm": bpm, "key": key,
                                        "sections": [{"name": "Intro", "bar": 1, "end_bar": 5},
                                                     {"name": "Verse", "bar": 5, "end_bar": 13}]})
    return {"project": dest, "genre": genre, "bpm": bpm, "key": key}


ns["flh_new_song"] = fake_new_song
r = ns["TOOLS"]["fl_flip_sample"][0](sample=SAMPLE, genre="lofi", sample_bpm=BPM, seed=2, bars=4)
assert made["starter"] is False and made["open"] is False and made["genre"] == "lofi", made
assert abs(made["bpm"] - BPM * 2 ** (-2 / 12)) < 0.6, made                       # lofi: -2 st -> slower song
assert r["written"]["new_song"]["project"].endswith("Soul Thing Flip.flp")
assert r["written"]["start_bar"] == 5.0, r["written"]                             # in the Verse, under its drums
assert "bars 5-13" in r["written"]["placed"], r["written"]["placed"]
song2 = r["written"]["project"]; d = m._load(song2); ev = m._events(d)
fl_notes = [m._rec_to_note(x_) for x_ in {p["name"]: p for p in m._patterns(d, ev).values()}["Flip"]["notes"]]
bnotes = [n_ for n_ in fl_notes if n_["ch"] == made["bass_ch"]]
assert r["written"]["bass"]["channel"] == made["bass_ch"] and len(bnotes) == r["written"]["bass"]["notes"] > 0
P = m._ppq(d); off = flh_score.sounding_offsets(flh_store.journal_get(song2)).get(made["bass_ch"], 0)
chop_ch = {c["channel"] for c in r["written"]["channels"]}


def name_pcs(nm):
    """Pitch classes a bass may play under a chord name: its root and a slash bass ('Dbmaj7/C' -> {Db, C})."""
    head = nm.split("/")[0]; out = {F._pc(head[:2] if len(head) > 1 and head[1] in "#b" else head[0])}
    if "/" in nm:
        out.add(F._pc(nm.split("/")[1]))
    return out


allowed = {}
for row in r["chops"]:
    if row.get("name"):
        pcs = {F._pc(row["root"])}
        for nm in ((row.get("chords") or "") + " " + (row.get("chord") or "")).split():
            if nm != "...":
                pcs |= name_pcs(nm)
        allowed[row["name"]] = pcs
chan_name = {c["channel"]: re.sub(r"^(Chop \d+)[b-p]$", r"\1", c["name"])    # 'Chop 4b' = Chop 4 cut shorter
             for c in r["written"]["channels"]}
spans = [(n_["pos"], n_["pos"] + n_["len"], chan_name[n_["ch"]]) for n_ in fl_notes
         if n_["ch"] in chop_ch and "stutter" not in chan_name[n_["ch"]]]
for n_ in bnotes:                                     # bass = the root of the chord sounding in the chop at that point
    owner = [nm for a_, b_, nm in spans if a_ <= n_["pos"] < b_]
    assert owner and (n_["key"] - off) % 12 in allowed[owner[0]], (n_, owner)
assert all(28 <= n_["key"] - off <= 40 for n_ in bnotes), [n_["key"] for n_ in bnotes]
assert max(n_["pos"] + n_["len"] for n_ in fl_notes) <= 4 * 4 * P                          # the 4-bar loop
assert r["flip"]["bars"] == 4 and r["flip"]["pitch_st"] == -2
# ---------------------------------------------------------------- a sparse melodic keys loop (ringing chord hits)
# 72 BPM, 8 bars, Ab major: a chord every 2 beats (a few held 4 beats), each ringing out ~18 dB/s from -18 dB
# (the ring IS the sample): it must be read as a melodic loop, keep pitch 0, keep each ring, and sit over the drums.
BPM2 = 72.0; beat2 = 60 / BPM2
PROG = [(41, [53, 56, 60]), (49, [53, 56, 60, 61]), (44, [56, 60, 63, 67]), (51, [55, 58, 63])]   # Fm Db Ab Eb
hits2 = []
t_ = 0.0
for bar in range(8):
    for half in (0, 2):
        if bar in (3, 6) and half == 2:                  # a held chord: rings 4 beats
            continue
        hits2.append((bar * 4 * beat2 + half * beat2, PROG[len(hits2) % 4]))
n2 = int(8 * 4 * beat2 * fs)
keys = np.zeros(n2)
for at, (bn, ns_) in hits2:
    a = int(at * fs); L = n2 - a; tt = np.arange(L) / fs
    y = sum(np.sin(2 * np.pi * hz(n) * tt) * (1 + 0.35 * np.exp(-tt * 3)) for n in ns_) + 1.3 * np.sin(2 * np.pi * hz(bn) * tt)
    keys[a:] += y * np.minimum(1, tt / 0.003) * np.exp(-tt * 18 / 8.686)
keys = keys / np.abs(keys).max() * 10 ** (-12 / 20) + 1e-3 * rng.standard_normal(n2)     # quiet-ish, with a floor
KEYS = os.path.join(samples, "Keys Loop.wav"); wav(KEYS, np.stack([keys, keys], 1))
hit_t = [h[0] for h in hits2]
x2, _, _ = m._wav_load(KEYS)
A2 = F.analyze(x2, fs)
assert abs(A2["bpm"] - BPM2) < 0.3 and "snapped" in A2["bpm_how"], (A2["bpm"], A2["bpm_how"])
assert F.classify(A2)["kind"] == "melodic", F.classify(A2)
assert F.classify(A)["kind"] == "melodic"                                 # chords over drums: still tonal
assert F._key_name(8, "major") == "Ab major" and F._spelling("Ab major")[1] == "Db"

# genre rnb used to give chipmunk soul (+4 st): a melodic loop now gets a loop style at its own pitch
d1 = F.fl_flip_sample(KEYS, genre="rnb", dry_run=True)
assert d1["flip"]["style"] in ("loop-intact", "loop") and d1["flip"]["pitch_st"] == 0, d1["flip"]
assert d1["measured"]["kind"] == "melodic" and "melodic" in d1["flip"]["style_why"], d1["flip"]["style_why"]
assert d1["flip"]["style"] == "loop-intact" and d1["flip"]["bars"] == 8, d1["flip"]     # musical all through: intact
loop_row = next(c for c in d1["chops"] if c.get("chop") == "L")
assert loop_row["from_s"] < 0.01 and abs(loop_row["to_s"] - 30 * beat2) < 0.05, loop_row    # 7.5 bars as recorded
assert all("C#" not in (c.get("chords", "") + c.get("chord", "")) for c in d1["chops"])        # spelled in Ab major
assert F.fl_flip_sample(KEYS, genre="rnb", style="soul", dry_run=True)["flip"]["pitch_st"] == 4   # style= still wins

# keep='chops': rearranged phrases (A B A C), every chop keeps >= 80 % of its last hit's ring
d2 = F.fl_flip_sample(KEYS, genre="rnb", keep="chops", dry_run=True)
assert d2["flip"]["style"] == "loop" and d2["flip"]["pitch_st"] == 0 and d2["flip"]["cut"] == "ring", d2["flip"]
assert d2["flip"]["bars"] == 4 and d2["flip"]["loop"].startswith("0:A 4:B 8:A 12:C"), d2["flip"]["loop"]
used = [c for c in d2["chops"] if not c.get("unused")]
assert any("hits (a phrase)" in c["why"] for c in used), used                           # phrase-length chops
for c in d2["chops"]:
    last = max(t for t in hit_t if t <= c["from_s"] + 0.02 or t < c["to_s"] - 0.1)
    nxt = min([t for t in hit_t if t > last + 0.1] + [n2 / fs])
    assert any(abs(c["from_s"] - t) < 0.02 for t in hit_t), c                            # starts on a hit
    assert c["to_s"] - last >= 0.8 * (nxt - last) - 0.02, (c, last, nxt)                 # the ring kept
    assert c["plays_beats"] * beat2 >= 0.8 * (nxt - last) - 0.02 or c["to_s"] - c["from_s"] > 2.5 * beat2, c

# boombap (a stab style) still cuts short: the ring rule is per style
d3 = F.fl_flip_sample(KEYS, genre="boombap", dry_run=True, style="boombap")
assert d3["flip"]["cut"] == "short" and max(c["to_s"] - c["from_s"] for c in d3["chops"]) < 1.9 * beat2

# written into a new song: the chops are the loudest musical part (~4 dB over the drums), WAVs keep the ring
made.clear()
r6 = ns["TOOLS"]["fl_flip_sample"][0](sample=KEYS, genre="rnb", keep="chops", seed=3)
lv = r6["levels"]
assert 3.0 <= lv["chops_over_drums_db"] <= 6.0, lv
assert lv.get("bass_lufs") is None or lv["bass_lufs"] <= lv["chops_lufs"] - 3.0 + 0.1, lv
song6 = r6["written"]["project"]; d6 = m._load(song6)
for t, v in lv["chop_fader_db"].items():
    assert abs(m._track_volume_db(d6, int(t)) - v) < 0.3, (t, v, m._track_volume_db(d6, int(t)))
    assert v <= F.data()["chop_fader_max_db"] + 0.01
cdir6 = r6["written"]["chops_folder"]
for row in r6["chops"]:
    if row.get("name"):
        y, _, _ = m._wav_load(os.path.join(cdir6, row["name"] + ".wav"))
        assert abs(len(y) / fs - min(row["to_s"] - row["from_s"], row["plays_beats"] * beat2)) < 0.02, row
        tail = np.abs(y[-int(0.04 * fs):-int(0.035 * fs), 0]).max()                    # a 40 ms fade at the end
        assert abs(y[-1, 0]) < 2e-3 and tail > abs(y[-1, 0]), row
# the bass plays the root of the chord sounding at that point of the source
d6 = m._load(song6); ev6 = m._events(d6); P6 = m._ppq(d6)
pats6 = {p["name"]: p for p in m._patterns(d6, ev6).values()}
fn6 = [m._rec_to_note(x_) for x_ in pats6["Flip"]["notes"]]
bch6 = made["bass_ch"]; off6 = flh_score.sounding_offsets(flh_store.journal_get(song6)).get(bch6, 0)
cname6 = {c["channel"]: c["name"] for c in r6["written"]["channels"]}
src_of = {row["name"]: row["from_s"] for row in r6["chops"] if row.get("name")}
bn6 = [n_ for n_ in fn6 if n_["ch"] == bch6]
assert bn6, "no bass"
for n_ in bn6:
    owner = [c_ for c_ in fn6 if c_["ch"] in cname6 and "stutter" not in cname6[c_["ch"]]
             and c_["pos"] <= n_["pos"] < c_["pos"] + c_["len"]]
    assert owner, n_
    ts_ = src_of[cname6[owner[0]["ch"]]] + (n_["pos"] - owner[0]["pos"]) / P6 * beat2 + 0.05
    want = max((h for h in hits2 if h[0] <= ts_), key=lambda h: h[0])[1][0] % 12
    assert (n_["key"] - off6) % 12 == want, (n_, want, ts_)
assert r6["flip"]["pitch_st"] == 0 and r6["steps_s"]["total"] < 30


# ---------------------------------------------------------------- ring through: no hole after a phrase chop
# (PC report: an audible gap at chop C - a chop that doesn't ring through to the next hit). Causes fixed: a ring
# wobble taken for a hit ended a chop mid-ring; a chop that ended where its ring decayed stopped there although the
# source keeps ringing to the next hit; a chop used in slots of two lengths was trimmed to the shorter for both; an
# 8-bar flip left its last 4 bars empty.
def holes(res, song_bpm):
    """[(beat, ms)] where no chop sounds between a main trigger and the next trigger (from the written pattern + WAVs)."""
    sp = res["written"]["project"]; d_ = m._load(sp); ev_ = m._events(d_); P_ = m._ppq(d_)
    pat_ = {p["name"]: p for p in m._patterns(d_, ev_).values()}[res["written"]["pattern"]]
    names_ = {c["channel"]: c["name"] for c in res["written"]["channels"]}
    cd_ = res["written"]["chops_folder"]
    secs = {}
    for ch_, nm in names_.items():
        fn = next(f for f in os.listdir(cd_) if os.path.splitext(f)[0] == nm or f.startswith(nm + " 1-"))
        y_, _, _ = m._wav_load(os.path.join(cd_, fn)); secs[ch_] = len(y_) / fs
    nts = sorted((m._rec_to_note(x_) for x_ in pat_["notes"]), key=lambda n_: n_["pos"])
    nts = [n_ for n_ in nts if n_["ch"] in names_]
    loop_b = res["flip"]["bars"] * 4
    out = []
    for i_, n_ in enumerate(nts):
        if "stutter" in names_[n_["ch"]]:
            continue
        nxt = nts[i_ + 1]["pos"] / P_ if i_ + 1 < len(nts) else loop_b
        gap = (nxt - n_["pos"] / P_) * 60 / song_bpm - secs[n_["ch"]]
        assert gap > -0.005, ("a chop rings into the next hit", n_, gap)
        if gap > 0.03:
            out.append((n_["pos"] / P_, round(gap * 1000)))
    return out


st_ = [o["t"] for o in F._strong(A2["onsets"])]
assert all(min(abs(t - h) for h in hit_t) < 0.03 for t in st_), [t for t in st_ if min(abs(t - h) for h in hit_t) >= 0.03]
for sd in (0, 1, 3):                                       # the phrase-chop style (loop: A B A C + C roll), tempo matched
    made.clear()
    rr_ = F.fl_flip_sample(KEYS, genre="rnb", keep="chops", seed=sd, name=f"Ring {sd}", levels=False, drums=False)
    assert rr_["flip"]["style"] == "loop" and holes(rr_, rr_["flip"]["song_bpm"]) == [], (sd, holes(rr_, rr_["flip"]["song_bpm"]))
    assert not any("stop before the next trigger" in n_ for n_ in rr_["notes"]), rr_["notes"]
made.clear()
r8 = F.fl_flip_sample(KEYS, genre="rnb", keep="chops", seed=1, bars=8, name="Ring 8", levels=False, drums=False)
pos8 = [float(x_.split(":")[0]) for x_ in r8["flip"]["loop"].split()]
assert r8["flip"]["bars"] == 8 and max(pos8) >= 24 and holes(r8, r8["flip"]["song_bpm"]) == [], r8["flip"]["loop"]
for style_ in ("lofi", "call-answer"):                    # other ring styles: a chop in two slot lengths -> 'Chop Nb'
    made.clear()
    rs_ = F.fl_flip_sample(KEYS, genre="rnb", style=style_, pitch=0, seed=2, bars=4, name=f"Ring {style_}",
                           levels=False, drums=False)
    assert holes(rs_, rs_["flip"]["song_bpm"]) == [], (style_, holes(rs_, rs_["flip"]["song_bpm"]), rs_["flip"]["loop"])
# the variant numbering: the same chop cut to another length shares its number
pl_ = {"trig": [{"chop": 0, "div": None}, {"chop": 1, "div": None}, {"chop": 2, "div": None}, {"chop": 0, "div": 0.25}],
       "variant_of": {2: 0}}
k_, n_ = F.numbering(pl_)
assert [F._chan_name(k, k_, n_) for k in k_] == ["Chop 1", "Chop 1b", "Chop 2", "Chop 1 stutter"], k_
# 8 bars: the loop three times, then the turn
assert F._events({"loop": [{"at": 0, "chop": "A"}], "turn": [{"at": 0, "chop": "B"}]}, 8)[-1] == {"at": 24.0, "chop": "B"}

print("ok")
