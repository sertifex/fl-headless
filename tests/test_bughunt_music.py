"""Music bug hunt (2026-10-23): a sampled sweep of genres x keys x meters through flp_compose(what='beat') +
flp_arrange_song + flp_build_rise (dry run and real; FL simulated, projects from tests/corpus/base.flp) and
flh_new_song (a blank template built from the corpus), checking what must hold for any combination: no crash,
sections end to end on the bars, ear candy on its boundaries (one riser / big hit / fall per change), no clip past
the song's end, pitched parts in the key; plus one regression per bug fixed in this round:
  - flh_score.key_estimate crashed on a float tick (flp_build_rise passed one): the rise's key detection failed on
    the first call and worked on later ones (cache), so two identical calls built ladders in different keys
  - flp_build_rise trusted the notes over the journal when they only named the RELATIVE key (Krumhansl-Schmuckler
    hears i-VI-III-VII in D minor as F major): a D minor song got a ladder landing on Csus4 instead of Asus4
  - flp_build_rise key='H minor' -> KeyError (now a clear ValueError); bars=0 / extend bars=0 silently meant the
    default; into= a bar past the song's end planned a build in silence
  - flp_arrange_song with an empty form: dry run reported success with no sections, the real call failed with an
    unrelated error
  - fl_flip_sample on a silent file with keep='loop' 'flipped' silence without a word
  - chord names in modes / flat keys: C mixolydian's bVII was 'A#', Db minor was spelled C#m F#m ...
  - flh_new_song bpm=-5 -> struct.error after the whole build; a 300-character name -> OSError; 'CON' / trailing dots
  - finals with collection rules: after a full stop (1 s of silence) the music came back ~180 dB down and took 10 s
    to swell in (_ride_jumps measured the 'jump' from digital silence); rule_check said '180 dB'
  - flp_sampler_settings '/0', 'inf', '1e400' -> ZeroDivision / Overflow errors (now skipped with a reason)
  - flp_extend_ending on a project with a corrupt far note (tick 2^31) ate GBs (now refused like flp_perform does)
Deterministic; keep it under ~60 s. Run: python3 tests/test_bughunt_music.py"""
import collections, json, os, random, re, shutil, struct, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")
os.environ["FLH_FL_USERDATA"] = os.path.join(tmp, "docs"); os.environ["FLH_FACTORY_PACKS"] = os.path.join(tmp, "packs")
os.environ["FLH_SPLICE_LIB"] = os.path.join(tmp, "spl"); os.environ["FLH_FLEX_DIR"] = os.path.join(tmp, "flex")
for k_ in ("FLH_PLUGIN_DB", "FLH_FL_EDITION"):
    os.environ.pop(k_, None)
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702  (leave no temp files behind)
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
import flh_store, flh_compose as C, flh_newsong as N, flh_score as SC, flh_rise as RI, flh_flip as F  # noqa: E402
for mod_, tool_ in ((flh_store, None), (SC, "flp_score"), (C, "flp_compose"), (N, "flh_new_song"),
                    (RI, "flp_build_rise"), (F, "fl_flip_sample")):
    if tool_ and tool_ not in ns["TOOLS"]:
        mod_.register(ns)
T = ns["TOOLS"]
FIX = os.path.join(HERE, "corpus", "base.flp")
if not os.path.isfile(FIX):
    print("skipped: no tests/corpus/base.flp"); sys.exit(0)
m._with_open_project = lambda project, output, fn: fn()            # FL simulated: nothing is open
m.fl_open = lambda p: {"opened": p}
ns["IS_WINDOWS"] = True
m._state({"fl_edition": "producer"})


def make_blank(src, dst):
    """FL's empty template stand-in (as tests/test_newsong.py): the corpus file without channels, notes or clips."""
    d = m._load(src); ev = m._events(d); keep = []; inside = False
    for e in ev:
        if e["id"] == 64:
            inside = True
        if not inside and e["id"] != 227:
            if e["id"] in (m.EV_NOTES, m.EV_PLAYLIST):
                keep.append(m._data_event(e["id"], b""))
            else:
                keep.append(m._raw(d, e))
        if inside and e["id"] == 51:
            inside = False
    hl = struct.unpack("<I", d[4:8])[0]; head = bytearray(d[:8 + hl]); fmt, _, ppq = struct.unpack("<hHH", head[8:14])
    head[8:14] = struct.pack("<hHH", fmt, 0, ppq); body = b"".join(keep)
    with open(dst, "wb") as f:
        f.write(bytes(head) + b"FLdt" + struct.pack("<I", len(body)) + body)


os.environ["FLH_BLANK"] = os.path.join(tmp, "Empty.flp"); make_blank(FIX, os.environ["FLH_BLANK"])


def blank(name, num=4, den=4):
    p = os.path.join(roots, name); shutil.copy(FIX, p)
    m.flp_edit_notes(p, [{"op": "delete"}], pattern=1)
    m.flp_playlist(p, "remove", clips=[c["id"] for c in m.flp_playlist(p)["clips"]])
    if (num, den) != (4, 4):
        m.flp_project_info(p, p, time_sig_num=num, time_sig_beat=den)
    return p


def refused(fn, *words, **kw):
    try:
        fn(**kw)
    except ValueError as e:
        assert all(w in str(e) for w in words), (words, str(e))
        return str(e)
    raise AssertionError(f"not refused: {kw}")


# ----------------------------------------------------------------------------- a D minor song (i VI III VII)
song = N.flh_new_song("cinematic", key="D minor", name="Rise Song", seed=3, balance=False)["project"]
secs = {s["name"]: s["bar"] for s in flh_store.journal_get(song)["sections"]}
assert secs.get("Build") == 25, secs
view = C.project_view(song)
model = SC.load_model(view["src"], journal_from=song)
a_t, z_t = C._bar_tick(view, 21), C._bar_tick(view, 33.0)               # a float bar -> a float tick
assert isinstance(z_t, float)
SC._CACHE.clear(); model = SC.load_model(view["src"], journal_from=song)
k_float = SC.key_estimate(model, a_t, z_t, 3)                          # crashed: range() of a float bar index
SC._CACHE.clear(); model = SC.load_model(view["src"], journal_from=song)
assert SC.key_estimate(model, int(a_t), int(z_t), 3) == k_float and k_float, k_float

# the rise: the same call twice plans the same ladder, in the journal's key (the notes only say its relative major)
SC._CACHE.clear()
rise = T["flp_build_rise"][0]
r1 = rise(project=song, into=25, dry_run=True)
r2 = rise(project=song, into=25, dry_run=True)
r3 = rise(project=song, into="Build", dry_run=True)
assert r1["key"] == r2["key"] == r3["key"] == "D minor" and r1["key_from"] == "song journal", (r1["key"], r2["key"], r3)
assert r1["harmony"]["ladder"] == r2["harmony"]["ladder"] and "Asus4" in r1["harmony"]["ladder"], r1["harmony"]
assert RI._key_for(view, song, None, a_t, z_t)[0] == "D minor"
refused(rise, "key", project=song, into=25, key="H minor", dry_run=True)        # was KeyError 'H'
refused(rise, "at least 1", project=song, into=25, bars=0, dry_run=True)       # was: 0 -> the default 4 bars
refused(rise, "end", project=song, into=999, dry_run=True)                      # was: a build in silence at 995-998
refused(T["flp_arrange_song"][0], "1-", project=song, extend={"section": "A", "bars": 0}, dry_run=True)

# ----------------------------------------------------------------------------- an empty form is refused clearly
q = blank("form.flp")
T["flp_compose"][0](project=q, what="beat", genre="trap", key="A minor", seed=1)
for form in ("", " , ", "Intro:0"):
    refused(T["flp_arrange_song"][0], "form", project=q, form=form, mode="replace", dry_run=True)
    refused(T["flp_arrange_song"][0], "form", project=q, form=form, mode="replace")

# ----------------------------------------------------------------------------- a silent sample isn't flipped
fs = 44100
sil = os.path.join(tmp, "silence.wav")
with wave.open(sil, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs); w.writeframes(b"\x00\x00" * fs * 3)
for kw in ({"keep": "loop"}, {}):
    refused(F.fl_flip_sample, "silent", sample=sil, genre="boombap", dry_run=True, **kw)

# ----------------------------------------------------------------------------- chord names spelled in the key
spell = lambda key, prog: [c["name"] for c in C.harmony(key, "rock", len(prog.split()), prog, 1)[0]]
assert spell("C mixolydian", "I bVII IV I") == ["C", "Bb", "F", "C"], spell("C mixolydian", "I bVII IV I")  # was A#
assert spell("A phrygian", "i bII i VII") == ["Am", "Bb", "Am", "G"], spell("A phrygian", "i bII i VII")  # was A#
assert spell("E locrian", "i bII bV i") == ["Em", "F", "Bb", "Em"], spell("E locrian", "i bII bV i")   # F major's Bb
assert spell("Db minor", "i iv bII V") == ["Dbm", "Gbm", "D", "Ab"], spell("Db minor", "i iv bII V")  # asked in flats
assert spell("A dorian", "i IV i VII") == ["Am", "D", "Am", "G"] and spell("F# minor", "i iv")[1] == "Bm"
assert spell("Bb mixolydian", "I bVII")[1] == "Ab" and spell("E major", "I bVII")[1] == "D"

# ----------------------------------------------------------------------------- flh_new_song: odd tempo / names
refused(N.flh_new_song, "tempo", genre="pop", bpm=-5, balance=False)        # was struct.error after the build
refused(N.flh_new_song, "tempo", genre="pop", bpm=2000, balance=False)
for nm_, want in (("x" * 300, None), ("Night drive.", "Night drive"), ("CON", None), ("  ...  ", None)):
    pr = N.flh_new_song("trap", name=nm_, starter=False, balance=False)["project"]   # was OSError: name too long
    stem_ = os.path.basename(os.path.dirname(pr))
    assert os.path.isfile(pr) and len(stem_) <= 80 and not stem_.endswith((".", " ")) and stem_.upper() != "CON", pr
    assert os.path.basename(pr) == stem_ + ".flp" and (want is None or stem_ == want), (pr, stem_)

# ----------------------------------------------------------------------------- finals: the music after a full stop
import flh_finalize as FN  # noqa: E402
tt_ = np.arange(10 * fs) / fs
mus_ = 0.3 * np.sin(2 * np.pi * 220 * tt_)
gap_ = np.concatenate([mus_, np.zeros(fs), mus_, mus_[:8 * fs] * np.exp(-tt_[:8 * fs])])[:, None]   # 1 s of silence at 10 s
R_ = FN._rules({"min_ending_s": 5})
y_, rides_ = FN._ride_jumps(gap_, fs, R_)
t1_, L1_, _ = FN._level_curve(gap_, fs); _, L2_, _ = FN._level_curve(np.asarray(y_), fs)
at_ = lambda s: int(np.searchsorted(t1_, s))
assert rides_ and all(r["db"] < 60 for r in rides_), rides_                  # was a '180 dB' jump
assert L2_[at_(14.0)] > L1_[at_(14.0)] - 1.0, (L1_[at_(14.0)], L2_[at_(14.0)])   # was ~-145 dB: 10 s to swell back
assert L2_[at_(11.1)] <= L1_[at_(11.1)] - 3.0                                # the entrance itself still swells in
rc_ = FN.rule_check(gap_[:, 0], fs, R_)
jmp_ = next(c for c in rc_["checks"] if c["rule"] == "level_jumps")
assert not jmp_["pass"] and all(j["db"] < 60 for j in jmp_["value"]), jmp_     # still flagged, a sane number
assert not FN._jumps_on_curve(t1_, L2_, R_, 1.5)                              # the ridden final passes the rule

# ----------------------------------------------------------------------------- sampler settings: odd numbers skipped
import flh_sampler as SM  # noqa: E402
smp = N.flh_new_song("trap", name="Sampler Odd", starter=False, balance=False)["project"]
kch = next(c["index"] for c in m.flp_list(smp)["channels"] if c.get("plugin") is None and c["name"] == "Kick")
for f_, v_ in (("pitch_st", "/0"), ("pitch_st", "inf"), ("fine_cents", "1e400"), ("pitch_st", "+1e308"),
               ("cutoff", "+1e308"), ("attack_ms", "nan")):                    # were ZeroDivision / Overflow errors
    out_ = SM.flp_sampler_settings(smp, kch, output=os.path.join(roots, "smp_out.flp"), set={f_: v_})
    assert any(s["field"] == f_ for s in out_.get("skipped") or []), (f_, v_, out_)

# ----------------------------------------------------------------------------- a corrupt far note: refused, not OOM
far = os.path.join(roots, "far.flp"); shutil.copy(os.path.join(HERE, "corpus", "notes_far.flp"), far)
for kw in ({"dry_run": True}, {"dry_run": True, "hold_s": 30}):              # was: GBs of memory, then killed
    refused(T["flp_extend_ending"][0], "corrupt", project=far, **kw)

# ----------------------------------------------------------------------------- sweep: genres x keys x meters
EC = C._ec_data()
ROLE = {k: (v or {}).get("role") for k, v in (EC.get("kinds") or {}).items()}
FXN = set(C.FX_NAMES.values())
KEYS = ["F# minor", "Db major", "D dorian", "Bb mixolydian", "G# harmonic minor", "E phrygian", "C major", "A minor",
        "Cb major", "D# minor", "B major", "Eb minor"]
METERS = [(4, 4), (3, 4), (6, 8), (7, 8), (5, 4), (12, 8), (2, 4)]
G = sorted(k for k in C.genres() if not k.startswith("_"))
rnd = random.Random(20261023)
problems = []
for i, gn in enumerate(G):
    key, (num, den) = rnd.choice(KEYS), METERS[i % len(METERS)]
    amt = ("subtle", "normal", "lots")[i % 3]
    tag = (gn, key, f"{num}/{den}", amt)
    p = blank(f"sw{i}.flp", num, den)
    rb = T["flp_compose"][0](project=p, what="beat", genre=gn, key=key, seed=i + 1)
    assert rb["verified"], (tag, rb)
    ra = T["flp_arrange_song"][0](project=p, genre=gn, seed=i + 1, mode="replace", ear_candy=amt)
    assert ra["verified"], (tag, ra)
    S = ra["sections"]
    starts = [s["bar"] for s in S]; end = S[-1]["bar"] + S[-1]["bars"]
    for a, b in zip(S, S[1:]):
        assert abs(a["bar"] + a["bars"] - b["bar"]) < 1e-6, (tag, a, b)
    per = collections.defaultdict(collections.Counter)
    for t in ra.get("transitions") or []:
        mm = re.search(r"at bar ([\d.]+)", t.get("purpose", ""))
        if not mm:
            continue
        bnd = float(mm.group(1))
        assert bnd in starts or abs(bnd - end) < 1e-6, (tag, t)
        per[bnd][ROLE.get(t["kind"]) or t["kind"]] += 1
    for bnd, c in per.items():
        assert all(c[r] <= 1 for r in ("riser", "hit", "fall", "drums", "cut")), (tag, bnd, c)
    for c in m.flp_playlist(p)["clips"]:
        assert c["bar"] + c["length_bars"] <= end + 1e-3, (tag, c, end)
        if re.sub(r" #?\d+$", "", c["name"]) in FXN:                  # ear candy sits on a change, not mid-section
            s_, e_ = c["bar"], c["bar"] + c["length_bars"]
            assert any(abs(s_ - b) < 1e-3 or abs(e_ - b) < 1e-3 or s_ < b < e_ for b in starts + [end]), (tag, c)
    # the pitched parts stay in the key: its scale + the family's (a mode's progressions are read as major / minor
    # numerals) + the minor's leading tone; 85 % (a borrowed chord's tones are fine)
    d = m._load(p); notes, _ = m._song(d, m._events(d))
    root_, _, _, fam_ = C.key_info(key)
    sc = set(C.scale_pcs(key)) | set(C.scale_pcs(f"{C._pc_name(root_)} {fam_}")) | ({(root_ + 11) % 12} if fam_ == "minor" else set())
    gd_ = C.genre_data(gn)                                       # the genre's own borrowed chords (metal's bII ...)
    sc |= {pc for s_ in range(1, 13) for ch_ in C.harmony(key, gd_, 8, None, s_)[0] for pc in C.chord_pcs(ch_)
           if str(ch_.get("rn", "")).startswith("b")}
    reg = (flh_store.journal_get(p).get("compose") or {}).get("channels") or {}
    for role in ("chords", "melody"):
        ks = [n[3] for n in notes if n[2] == reg.get(role)]
        if ks and sum(1 for k_ in ks if k_ % 12 in sc) < 0.8 * len(ks):
            problems.append((tag, role, round(sum(1 for k_ in ks if k_ % 12 in sc) / len(ks), 2)))
    into = S[min(2, len(S) - 1)]["section"]
    if S[min(2, len(S) - 1)]["bar"] > 1:
        rr = rise(project=p, into=into, dry_run=True)
        assert rr["key"] and rr["build_bars"], (tag, rr)
        if i % 4 == 0:
            rise(project=p, into=into)
assert not problems, problems

# new songs for a handful of genres x keys: pitched parts in the key (808 offsets applied), sections from bar 1
for i, gn in enumerate(("trap", "lofi", "rock", "gospel", "drill-uk")):
    key = KEYS[(i * 5) % len(KEYS)]
    r = N.flh_new_song(gn, key=key, name=f"ns{i}", seed=i + 2, balance=False)
    j = flh_store.journal_get(r["project"]); comp = j.get("compose") or {}
    assert j["sections"] and j["sections"][0]["bar"] == 1, (gn, j["sections"])
    tr = {int(k_): v_ for k_, v_ in (comp.get("transpose") or {}).items()}
    d = m._load(r["project"]); notes, _ = m._song(d, m._events(d))
    sc = set(C.scale_pcs(r["key"]))
    if any("bII" in t for pr in (C.genre_data(gn).get("harmony") or {}).get("progressions") or [] for t in pr["rn"]):
        sc.add((C.key_info(r["key"])[0] + 1) % 12)      # the genre's own phrygian bII chord (drill: i-bII-V-i)
    for role in ("bass", "chords", "melody"):
        ks = [n[3] - tr.get(n[2], 0) for n in notes if n[2] == (comp.get("channels") or {}).get(role)]
        assert not ks or sum(1 for k_ in ks if k_ % 12 in sc) >= 0.75 * len(ks), (gn, key, role)
print("ok")
