"""flh_refbeat: fl_beat_from_reference on synthetic references (numpy kick/snare/hat at a tempo + chord tones):
analysis -> plan (tempo, key, chord loop), the half-time choice (75 -> 150 for trap, 75 kept for lofi), the genre
guess, the cache (a second call decodes nothing), loud/quiet sections with their own loops, ONE flp_edit batch +
one open + one balance (FL mocked), a real build on FL's empty-template stand-in (the chords really are the loop),
dry_run, copy= refusal, given sections, and a failing analysis step degrading gracefully.
Run: python3 tests/test_refbeat.py (no FL needed)."""
import json
import os
import shutil
import struct
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_refbeat_")
roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ["FLH_FL_USERDATA"] = os.path.join(tmp, "Documents", "Image-Line", "FL Studio")
os.environ["FLH_FACTORY_PACKS"] = os.path.join(tmp, "FL", "Data", "Patches", "Packs")
os.environ["FLH_SPLICE_LIB"] = os.path.join(tmp, "Splice", "INSTRUMENT")
os.environ["FLH_FLEX_DIR"] = os.path.join(tmp, "FLEX")
for k in ("FLH_PLUGIN_DB", "FLH_FL_EDITION", "FLH_TOOLSET"):
    os.environ.pop(k, None)
import numpy as np  # noqa: E402

os.environ["FLH_AUTO_RENDERS"] = "1"                 # these parts test the automatic renders
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_catalog, flh_compose, flh_newsong, flh_transcribe, flh_refbeat as R  # noqa: E402
ns = vars(m)
for mod in (flh_store, flh_catalog, flh_compose, flh_newsong, flh_transcribe, R):
    mod.register(ns)
assert "fl_beat_from_reference" in m.TOOLS and m.tool_group("fl_beat_from_reference") == "compose"
assert "fl_beat_from_reference" not in [t["name"] for t in m.handle({"method": "tools/list"})["tools"]]  # unlisted
assert not m._windows_only("fl_beat_from_reference")                                # flh_call works on any OS
m.fl_running = lambda: False
FIX = os.path.join(HERE, "corpus", "base.flp")


def make_blank(src, dst):
    """FL's empty template stand-in: the fixture without channels, notes, clips or remote links."""
    d = m._load(src); ev = m._events(d); keep = []; inside = False
    for e in ev:
        if e["id"] == 64:
            inside = True
        if not inside and e["id"] != 227:
            if e["id"] == m.EV_NOTES:
                keep.append(m._data_event(m.EV_NOTES, b""))
            elif e["id"] == m.EV_PLAYLIST:
                keep.append(m._data_event(m.EV_PLAYLIST, b""))
            else:
                keep.append(m._raw(d, e))
        if inside and e["id"] == 51:
            inside = False
    hl = struct.unpack("<I", d[4:8])[0]; head = bytearray(d[:8 + hl]); fmt, _, ppq = struct.unpack("<hHH", head[8:14])
    head[8:14] = struct.pack("<hHH", fmt, 0, ppq); body = b"".join(keep)
    with open(dst, "wb") as f:
        f.write(bytes(head) + b"FLdt" + struct.pack("<I", len(body)) + body)


blank = os.path.join(tmp, "Empty.flp"); make_blank(FIX, blank); os.environ["FLH_BLANK"] = blank

# ---------------------------------------------------------------- synthetic references
FS = 44100
NOTE = lambda p: 440.0 * 2 ** ((p - 69) / 12.0)
AM, F_, C_, G_, DM = (57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62), (50, 53, 57)


def synth(parts, bpm=75.0, seed=1):
    """parts: [(bars, chords cycle, loud)] -> mono float. loud: kick (1, 2.5, 3.5... beats), snare 2 & 4, 16th hats,
    a heavy sub under the chords; quiet: soft chords + 8th hats only."""
    beat = 60.0 / bpm
    total = sum(b for b, _, _ in parts)
    n = int(total * 4 * beat * FS) + FS
    y = np.zeros(n); rng = np.random.default_rng(seed)
    t = np.arange(int(0.3 * FS)) / FS
    kick = np.sin(2 * np.pi * (50 + 80 * np.exp(-t * 30)) * t) * np.exp(-t * 12)
    snare = rng.standard_normal(len(t)) * np.exp(-t * 25) * 0.5 + 0.3 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 20)
    th = t[:int(0.05 * FS)]; hat = np.diff(rng.standard_normal(len(th) + 1)) * np.exp(-th * 90) * 0.15

    def add(sig, at):
        a = int(at * FS); b = min(n, a + len(sig)); y[a:b] += sig[:b - a]
    bar = 0
    for bars, prog, loud in parts:
        for j in range(bars):
            b0 = bar * 4 * beat
            if loud:
                for k in (0, 1.5, 2.5):
                    add(kick, b0 + k * beat)
                add(snare, b0 + beat); add(snare, b0 + 3 * beat)
            for h in range(16 if loud else 8):
                add(hat * (1 if loud else 0.5), b0 + h * beat * (0.25 if loud else 0.5))
            ch = prog[j % len(prog)]; tt = np.arange(int(4 * beat * FS)) / FS
            pad = sum(np.sin(2 * np.pi * NOTE(p) * tt) for p in ch) * (0.08 if loud else 0.05)
            if loud:
                pad = pad + 0.25 * np.sin(2 * np.pi * NOTE(ch[0] - 12) * tt)
            add(pad * np.minimum(1, tt * 20), b0)
            bar += 1
    return y / (np.abs(y).max() * 1.1)


def write_wav(path, y):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(FS)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    return path


def chord_names(prog):
    return [t.split(":")[0] for t in prog.split()]


def same_cycle(got, want):
    """got is want rotated (the loop may start on another chord of the cycle)."""
    return len(got) == len(want) and any(got == want[i:] + want[:i] for i in range(len(want)))


ref = write_wav(os.path.join(roots, "Ref One.wav"), synth([(16, (AM, F_, C_, G_), True)]))

# ---------------------------------------------------------------- 1) dry run: measured + plan, trap guessed, half time
r = m.TOOLS["fl_beat_from_reference"][0](reference=ref, dry_run=True)
if os.environ.get("FLH_TEST_VERBOSE"):
    print(json.dumps(r, indent=1))
assert r["dry_run"] and r["analysis"] == "measured" and "built" not in r, r
me, pl = r["measured"], r["plan"]
assert abs(me["tempo"]["bpm"] - 75) <= 1.0 and me["tempo"]["confidence"] >= 0.5, me
assert me["key"]["key"] in ("A minor", "C major") and pl["key"] == "A minor", (me, pl)     # Am is where it sits
assert pl["genre"] == "trap" and pl["bpm"] == 150 and "half-time" in pl["tempo"], pl
assert any("genre trap: guessed" in g for g in r["guessed"]), r
s0 = pl["sections"][0]
assert s0["section"] == "Hook" and same_cycle(chord_names(s0["progression"]), ["Am", "F", "C", "G"]), s0
assert s0["progression"].split()[0].endswith(":2"), s0                     # one 75-BPM bar = two 150-BPM bars
assert "dark" in (pl["vibe"] or "") and "heavy" in pl["vibe"], pl          # 957 Hz centroid, a loud sub
assert r["estimate_s"]["total"] > 0 and any("flp_compose" in w for w in r["would"]), r
assert "not reproduced note-for-note" in r["copy"] and "kicks" in json.dumps(me["drums"]), r
steps = r["steps_s"]
assert all(k in steps for k in ("analysis_decode", "analysis_drums", "analysis_chords", "analysis_tone")), steps
assert len(json.dumps(r)) < 4000, len(json.dumps(r))                       # compact

via = m._flh_call("fl_beat_from_reference", {"reference": ref, "dry_run": "true", "genre": "boom bap"})   # flh_call
assert via["plan"]["genre"] == "boombap" and via["analysis"].startswith("cached"), via["plan"]
assert 84 <= via["plan"]["bpm"] <= 98 or "outside" in via["plan"]["tempo"], via["plan"]
help_ = m.flh_tools(name="fl_beat_from_reference")
assert help_["listed"] is False and "flh_call" in help_["call"], help_

# ---------------------------------------------------------------- 2) cache: second call decodes nothing
cache_dir = os.path.join(os.path.dirname(m.STATE), "refbeat")
assert len([f for f in os.listdir(cache_dir) if f.endswith(".json")]) == 1
real_load = ns["_wav_load"]
ns["_wav_load"] = lambda *a, **k: (_ for _ in ()).throw(AssertionError("decoded again"))
r2 = R.fl_beat_from_reference(ref, genre="lofi", dry_run=True)
ns["_wav_load"] = real_load
assert r2["analysis"].startswith("cached") and "analysis_cached" in r2["steps_s"], r2
assert r2["steps_s"]["total"] < 0.5, r2["steps_s"]
assert r2["plan"]["genre"] == "lofi" and r2["plan"]["bpm"] == 75 and r2["plan"]["tempo"] == "as read", r2["plan"]
assert chord_names(r2["plan"]["sections"][0]["progression"]) and r2["plan"]["sections"][0]["progression"].split()[0].endswith(":1")
assert ns["_wav_load"] is real_load

# ---------------------------------------------------------------- 3) refusals
for bad in ("notes", "melody"):
    try:
        R.fl_beat_from_reference(ref, copy=bad, dry_run=True); raise SystemExit("copy must be refused")
    except ValueError as e:
        assert "never copies" in str(e), e
try:
    R.fl_beat_from_reference(os.path.join(roots, "nope.wav"), dry_run=True); raise SystemExit("missing file")
except FileNotFoundError:
    pass

# ---------------------------------------------------------------- 4) build with FL mocked: ONE batch, one open, one balance
calls = {"new_song": [], "edit": 0, "open": [], "balance": []}
real_ns_song, real_edit = m.TOOLS["flh_new_song"], m.TOOLS["flp_edit"]
real_balance = m.TOOLS["flp_balance"]


def spy_song(**kw):
    calls["new_song"].append(kw)
    return real_ns_song[0](**kw)


def spy_edit(**kw):
    if os.path.basename(sys._getframe(1).f_code.co_filename) == "flh_refbeat.py":   # this tool's own batches
        calls["edit"] += 1
        calls.setdefault("ops", []).append([o["tool"] for o in kw["ops"]])
    return real_edit[0](**kw)


m.TOOLS["flh_new_song"] = (spy_song,) + real_ns_song[1:]
m.TOOLS["flp_edit"] = (spy_edit,) + real_edit[1:]
m.TOOLS["flp_balance"] = (lambda project, genre=None, background=True:
                          calls["balance"].append((project, genre, background)) or {"balancing": project},) + real_balance[1:]
real_open = ns["fl_open"]; ns["fl_open"] = lambda p: calls["open"].append(p) or {"opened": p}
ns["IS_WINDOWS"] = True
try:
    b = R.fl_beat_from_reference(ref, name="Night Ref")
finally:
    ns["IS_WINDOWS"] = False; ns["fl_open"] = real_open
if os.environ.get("FLH_TEST_VERBOSE"):
    print(json.dumps(b, indent=1))
assert b.get("ok", True), b
assert len(calls["new_song"]) == 1 and calls["edit"] == 1, calls
assert calls["ops"][0][0] == "flp_compose" and calls["ops"][0][-1] == "flp_arrange_song", calls["ops"]
kw = calls["new_song"][0]
assert kw["open"] is False and kw["balance"] is False and kw["bpm"] == 150 and kw["key"] == "A minor", kw
assert kw["genre"] == "trap" and "dark" in kw["vibe"], kw
proj = b["built"]["project"]
assert calls["open"] == [proj] and len(calls["balance"]) == 1 and calls["balance"][0][2] is True, calls
assert b["built"]["opened"] is True and "background" in b["built"]["levels"] and b["renders"] == 0, b["built"]
assert "Hook@" in b["built"]["form"] and "one flp_edit" in b["built"]["batch"], b["built"]
assert b["built"]["beats"] and b["built"]["beats"][0].startswith("Hook:"), b["built"]
assert all(" -> insert" not in s for s in b["built"]["sounds"]), b["built"]["sounds"]
for k in ("new_song", "batch", "open", "balance_start", "total"):
    assert k in b["steps_s"], b["steps_s"]
# the Hook's chords really are the loop: Am F C G, two bars each, at 150 BPM
pats = {p["name"]: p for p in m.flp_patterns(proj)}
assert "Hook Chords" in pats and "Hook Drums" in pats, sorted(pats)
d = m._load(proj); ev = m._events(d)
j = flh_store.journal_get(proj)
chords_ch = j["compose"]["channels"]["chords"]
hook_pid = pats["Hook Chords"]["index"] if "index" in pats["Hook Chords"] else pats["Hook Chords"].get("pattern")
pnotes = None
for pid, p in m._patterns(d, ev).items():
    if p.get("name") == "Hook Chords" or pid == hook_pid:
        pnotes = [m._rec_to_note(rr) for rr in p["notes"]]
        break
assert pnotes, "Hook Chords notes"
ppq = m.flp_info(proj)["ppq"]; bar = 4 * ppq
want = [{9, 0, 4}, {5, 9, 0}, {0, 4, 7}, {7, 11, 2}]                       # Am F C G pitch classes
starts = []
for k in range(4):                                                         # each chord: 2 bars
    pcs = {n["key"] % 12 for n in pnotes if n["ch"] == chords_ch and k * 2 * bar <= n["pos"] < (k + 1) * 2 * bar}
    starts.append(pcs)
rot = next((i for i in range(4) if all(starts[q] and starts[q] <= want[(q + i) % 4] | {2, 5, 11, 7} and
                                       len(starts[q] & want[(q + i) % 4]) >= 2 for q in range(4))), None)
assert rot is not None, (starts, want)
assert j["reference"]["file"] == "Ref One.wav" and j["reference"]["copy"] == "feel" and j["reference"]["loops"], j["reference"]
assert abs(m.flp_info(proj)["tempo_bpm"] - 150) < 1e-6

# ---------------------------------------------------------------- 5) loud / quiet sections: their own loops (dry run)
two = write_wav(os.path.join(roots, "Two Parts.wav"),
                synth([(4, (AM, DM), False), (8, (AM, F_, C_, G_), True), (8, (AM, DM), False),
                       (8, (AM, F_, C_, G_), True)], seed=3))
t2 = R.fl_beat_from_reference(two, genre="trap", dry_run=True)
if os.environ.get("FLH_TEST_VERBOSE"):
    print(json.dumps(t2, indent=1))
secs = {s["section"]: s for s in t2["plan"]["sections"]}
assert set(secs) == {"Hook", "Verse"}, t2["plan"]
assert same_cycle(chord_names(secs["Hook"]["progression"]), ["Am", "F", "C", "G"]), secs
assert same_cycle(chord_names(secs["Verse"]["progression"]), ["Am", "Dm"]), secs
assert sum(1 for w in t2["would"] if w.startswith("flp_edit: flp_compose")) == 2, t2["would"]
dens = t2["measured"]["drums"]
assert float(dens["Hook"].split()[0]) > float(dens["Verse"].split()[0]), dens  # kicks only in the loud part

# given sections override the loudness split
t3 = R.fl_beat_from_reference(two, genre="trap", dry_run=True,
                              sections=[{"name": "Verse", "start_s": 3.2 * 12, "end_s": 3.2 * 20}])
s3 = t3["plan"]["sections"]
assert len(s3) == 1 and s3[0]["section"] == "Verse" and same_cycle(chord_names(s3[0]["progression"]), ["Am", "Dm"]), s3
try:
    R.fl_beat_from_reference(two, sections="Verse", dry_run=True); raise SystemExit("bad sections")
except ValueError:
    pass

# ---------------------------------------------------------------- 6) a failing analysis step degrades gracefully
third = write_wav(os.path.join(roots, "Third.wav"), synth([(8, (AM, F_, C_, G_), True)], seed=5))
real_a2m = m.TOOLS["audio_to_midi"]


def flaky(**kw):
    if kw.get("mode") == "chords":
        raise RuntimeError("chord tracker exploded")
    return real_a2m[0](**kw)


m.TOOLS["audio_to_midi"] = (flaky,) + real_a2m[1:]
try:
    f = R.fl_beat_from_reference(third, genre="trap", dry_run=True)
finally:
    m.TOOLS["audio_to_midi"] = real_a2m
assert "chords" in f["measured"]["not_measured"] and "exploded" in f["measured"]["not_measured"]["chords"], f
assert f["plan"]["bpm"] == 150 and f["plan"]["key"] and not f["plan"]["sections"][0].get("progression"), f["plan"]
assert any("genre's own progressions" in g for g in f["guessed"]), f["guessed"]
assert not any("flp_compose" in w for w in f["would"]), f["would"]          # the starter beat stands
# ... and building still works: new_song's own starter beat, no batch needed
m.TOOLS["audio_to_midi"] = (flaky,) + real_a2m[1:]
cache_f = [os.path.join(cache_dir, x) for x in os.listdir(cache_dir)]
try:
    fb = R.fl_beat_from_reference(third, genre="trap", open=False)
finally:
    m.TOOLS["audio_to_midi"] = real_a2m
assert os.path.isfile(fb["built"]["project"]) and fb["built"]["beats"] and "Hook@" in fb["built"]["form"], fb["built"]
assert "pending" in fb["built"]["levels"], fb["built"]                      # balance waits for the first fl_open
assert calls["edit"] == 1, calls                                            # no batch for it

# every step failing: a clear error, nothing cached
m.TOOLS["audio_to_midi"] = (lambda **kw: (_ for _ in ()).throw(RuntimeError("no")),) + real_a2m[1:]
real_det, real_an = ns["audio_detect"], ns["audio_analyze"]
ns["audio_detect"] = ns["audio_analyze"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no"))
dead = write_wav(os.path.join(roots, "Dead.wav"), synth([(2, (AM,), True)], seed=9))
try:
    R.fl_beat_from_reference(dead, dry_run=True); raise SystemExit("must fail")
except RuntimeError as e:
    assert "every analysis step failed" in str(e), e
finally:
    m.TOOLS["audio_to_midi"] = real_a2m; ns["audio_detect"], ns["audio_analyze"] = real_det, real_an

n_cache = len(os.listdir(cache_dir))
silent = write_wav(os.path.join(roots, "Silent.wav"), np.zeros(FS * 3))
try:
    R.fl_beat_from_reference(silent, dry_run=True); raise SystemExit("silent must fail")
except ValueError as e:
    assert "silent" in str(e), e
assert len(os.listdir(cache_dir)) == n_cache                                # nothing cached for it
r_t = R.fl_beat_from_reference(ref, copy="tempo", genre="trap", dry_run=True)
assert not any("flp_compose" in w for w in r_t["would"]) and r_t["plan"]["bpm"] == 150, r_t

# ---------------------------------------------------------------- 7) loop finder + helpers on their own
loop, agree, notes = R.find_loop([(9, "m"), (9, "m"), (5, ""), (5, ""), (0, ""), (0, ""), (0, ""), (7, "")] * 2, "A minor")
assert [n for n, _ in loop] == ["Am", "F", "C", "G"] and all(b == 2 for _, b in loop) and notes, (loop, notes)
loop, agree, _ = R.find_loop([(9, "m"), (9, "m"), (5, ""), (5, ""), (9, "m"), (9, "m"), (5, ""), (6, "dim")] * 2,
                            "A minor")                                                      # a passing misread
assert loop == [("Am", 2), ("F", 2)], loop
loop, _, notes = R.find_loop([(9, "m"), (9, "m"), (6, "dim"), (6, "dim")] * 4, "A minor")    # a real borrowed chord
assert loop == [("Am", 2), ("F#dim", 2)] and any("outside the key" in n for n in notes), (loop, notes)
loop, _, _ = R.find_loop(([(4, "")] * 4 + [(9, "m")] * 4) * 2, "A minor")                   # V stays major in minor
assert loop == [("E", 4), ("Am", 4)], loop
loop, agree, _ = R.find_loop([(9, "m")] * 8, "A minor")
assert loop == [("Am", 1)] and agree == 1.0, loop
assert R._parse_chart("| Am | - | F G |", 120)[:2] == [[0.0, 4.0, "Am"], [4.0, 5.0, "F"]]
assert R._name_from("C:/x/My Song (Live)!.mp3") == "My Song (Live) Type Beat"
shutil.rmtree(tmp, ignore_errors=True)
print("ok")
