"""fl_finalize + audio_limit: render -> limit -> finish -> file, FL closed/reopened once, journal updated (no FL)."""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
os.environ["FLH_RENDER_CACHE_GB"] = "0"                 # every render here really runs (the cache: test_render_cache)
tempfile.tempdir = os.path.join(tmp, "systemp"); os.makedirs(tempfile.tempdir)   # private: parallel runs make flh_* dirs too
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_finalize  # noqa: E402
import fl_audio_edit as AE  # noqa: E402
import flac_lite  # noqa: E402
import flh_review as R  # noqa: E402
import shutil, subprocess, threading  # noqa: E402
ns = vars(m)
flh_store.register(ns); flh_finalize.register(ns)


def piano(sec, fs, seed=0):
    """Piano-like: decaying harmonic tones with hammer clicks; quiet body, sharp peaks (needs a limiter)."""
    rng = np.random.default_rng(seed); n = int(sec * fs); y = np.zeros((n, 2))
    L = int(3.0 * fs); tt = np.arange(L) / fs; bank = []
    for k in range(24):
        f0 = 110 * 2 ** (k * 1.5 / 12)
        tone = sum((0.6 / h) * np.sin(2 * np.pi * f0 * h * tt + h) * np.exp(-tt * (1.5 + 0.8 * h)) for h in range(1, 7))
        tone[:int(0.002 * fs)] += rng.normal(0, 0.6, int(0.002 * fs))
        bank.append(tone)
    t = 0.0
    while t < sec - 3.5:
        tone = bank[rng.integers(0, 24)] * rng.uniform(0.35, 1.0); s = int(t * fs); pan = rng.uniform(0.3, 0.7)
        y[s:s + L, 0] += tone * (1 - pan); y[s:s + L, 1] += tone * pan
        t += float(rng.choice([0.125, 0.25, 0.5, 0.75]))
    return y / np.abs(y).max() * 10 ** (-0.3 / 20)


def tp_db(x):
    return 20 * np.log10(flh_finalize.tp_envelope(x).max())


FS = 44100
proj = os.path.join(tmp, "proj"); os.makedirs(proj)
A = os.path.join(proj, "Northwind (Direct).flp"); B = os.path.join(proj, "Ballad.flp")
for p in (A, B):
    open(p, "wb").write(b"FLhd fake")
finals = os.path.join(tmp, "finals")
flh_store.profile_set("Test", {"final_root": finals, "final_name": "{song} (Piano) - FINAL.flac",
                               "finish": {"lufs": -18.7, "true_peak": -1.0, "bits": 16, "fade_s": 2.0},
                               "finish_by_role": {"opener": {"lufs": -16.0}},
                               "songs": {"Northwind": {"project": A, "role": "opener", "order": 1}}})
final_a = os.path.join(finals, "Northwind", "Northwind (Piano) - FINAL.flac")
os.makedirs(os.path.dirname(final_a)); open(final_a, "wb").write(b"OLD FINAL")

ev, encoded = [], {}
ns["fl_running"] = lambda: not any(e[0] == "close" for e in ev)
ns["fl_close"] = lambda save=True: (ev.append(("close", save)), {"closed": True, "project": A})[1]
ns["fl_open"] = lambda project: (ev.append(("open", project)), {"opened": project})[1]


def fake_render(project, out_dir, formats, timeout_s, data=None, out_stem=None):
    assert not ns["fl_running"](), "render while FL is open"
    ev.append(("render", os.path.basename(project)))
    out = os.path.join(out_dir, (out_stem or "x") + ".wav")
    AE.save(out, piano(40, FS, seed=len(ev)), FS, 24)
    return {"rendered": [out], "seconds": 0.1}


bin_ = os.path.join(tmp, "bin"); os.makedirs(bin_)


def fake_recycle(paths):
    for p in paths:
        m.writable(p)
        if os.path.normcase(p) == os.path.normcase(final_a):
            assert os.path.isfile(final_a[:-5] + ".new.flac"), "old final recycled before the new one was written"
        ev.append(("recycle", os.path.basename(p)))
        os.replace(p, os.path.join(bin_, f"{len(ev)}_{os.path.basename(p)}"))
    return {"recycled": paths}


ns["_render"] = fake_render; ns["files_recycle"] = fake_recycle
real_encode = flac_lite.encode


def spy_encode(path, q, rate, bps):
    encoded[os.path.basename(path)] = (q.copy(), rate, bps)
    return real_encode(path, q, rate, bps)


flac_lite.encode = spy_encode

# ---- two songs: FL closes once, renders both, reopens once
r = flh_finalize.fl_finalize(projects=[A, B], background=False)
print("finalize:", r)
kinds = [e[0] for e in ev]
assert kinds.count("close") == 1 and kinds.count("open") == 1 and kinds[0] == "close", ev
assert kinds.count("render") == 2, ev
assert kinds.index("open") > max(i for i, k in enumerate(kinds) if k == "render"), ev     # reopen after all renders,
assert kinds.index("open") < kinds.index("recycle"), ev                                  # before the file phase
assert r["ok"] is True and "failed" not in r, r
assert ("open", A) in ev
fa, fb = r["finals"]
assert "error" not in fa and "error" not in fb, r
assert fa["song"] == "Northwind" and fa["path"] == final_a, fa
assert fb["path"] == os.path.join(finals, "Ballad", "Ballad (Piano) - FINAL.flac"), fb
assert open(final_a, "rb").read(4) == b"fLaC", "new final not in place"
assert not os.path.exists(final_a[:-5] + ".new.flac")
assert any(e == ("recycle", os.path.basename(final_a)) for e in ev), "old final not recycled"
recycled_old = [f for f in os.listdir(bin_) if f.endswith("FINAL.flac")]
assert open(os.path.join(bin_, recycled_old[0]), "rb").read() == b"OLD FINAL"
assert not any(e[0] == "recycle" and e[1].startswith("flh_final_") for e in ev), "own temp WAVs into the Recycle Bin"
assert not [d for d in os.listdir(tempfile.tempdir) if d.startswith("flh_final_")], "temp WAV folder left behind"
for name, target in (("Northwind (Piano) - FINAL.flac", -16.0), ("Ballad (Piano) - FINAL.flac", -18.7)):
    q, rate, bps = encoded[name[:-5] + ".new.flac"]; y = q / float((1 << (bps - 1)) - 1)
    L, tp = m._lufs(y, rate), tp_db(y)
    print(f"  {name}: {L:.2f} LUFS (target {target}), true peak {tp:.2f} dBTP, {bps}-bit")
    assert abs(L - target) <= 0.1, (name, L)
    assert tp <= -1.0, (name, tp)
    y2, r2, b2 = R.flac_load(os.path.join(finals, name.split(" (")[0], name))   # FLAC round trip, bit exact
    assert r2 == rate and b2 == bps and (np.round(y2 * (1 << (bps - 1))).astype(np.int64) == q).all(), name
assert fa["true_peak"] <= -1.0 and abs(fa["lufs"] - -16.0) <= 0.1, fa
assert fa["limited_db_max"] > 0, "test signal should need the limiter"
j = flh_store.journal_get(A)
assert j["final"]["path"] == final_a and j["final"]["lufs"] == fa["lufs"], j["final"]
assert j["versions"][-1]["label"] == "final" and j["song"] == "Northwind", j["versions"]
assert set(r["steps_s"]) >= {"close_fl", "render", "finish", "file", "reopen"} and "Ballad" in r["steps_s"]["render"]

# ---- background job, FL not running: no close, no reopen; second final replaces the first
ev.clear(); ev.append(("close", True))                # FL now "closed"
job = flh_finalize.fl_finalize(project=B, background=True)
m._job_wait(job["job"], 120); res = m.JOBS[job["job"]]
assert res["status"] == "done", res
assert [e[0] for e in ev].count("open") == 0 and [e[0] for e in ev].count("close") == 1, ev
assert any(e == ("recycle", "Ballad (Piano) - FINAL.flac") for e in ev)
assert len(flh_store.journal_get(B)["versions"]) == 2

# ---- F11: the finish step runs with the FL lock free (a background job holds FL only to render)
import threading  # noqa: E402
lock_seen = {}
real_finish = flh_finalize.finish


def spy_finish(*a, **k):
    def probe():
        got = m._FL_LOCK.acquire(blocking=False)
        lock_seen["free"] = got
        if got:
            m._FL_LOCK.release()
    th = threading.Thread(target=probe); th.start(); th.join()
    lock_seen["holder"] = m._FL_HOLDER.get("what")
    return real_finish(*a, **k)


def lock_render(project, out_dir, formats, timeout_s, data=None, out_stem=None):
    def probe():
        lock_seen["render_locked"] = not m._FL_LOCK.acquire(blocking=False)
        if not lock_seen["render_locked"]:
            m._FL_LOCK.release()
    th = threading.Thread(target=probe); th.start(); th.join()
    return fake_render(project, out_dir, formats, timeout_s, data, out_stem)


flh_finalize.finish = spy_finish; ns["_render"] = lock_render
job = flh_finalize.fl_finalize(project=B, background=True)
m._job_wait(job["job"], 120); res = m.JOBS[job["job"]]
assert res["status"] == "done" and res["result"]["ok"], res
assert lock_seen == {"render_locked": True, "free": True, "holder": None}, lock_seen
flh_finalize.finish = real_finish; ns["_render"] = fake_render

# ---- a failed song: top-level ok false + error summary + next, per-song details kept
def bad_render(project, out_dir, formats, timeout_s, data=None, out_stem=None):
    if "Ballad" in project:
        raise RuntimeError("FL didn't write the WAV")
    return fake_render(project, out_dir, formats, timeout_s, data, out_stem)


ns["_render"] = bad_render
rf = flh_finalize.fl_finalize(projects=[A, B], background=False)
assert rf["ok"] is False and rf["failed"] == 1 and "Ballad" in rf["error"] and "didn't write" in rf["error"], rf
assert rf["next"] and "Ballad" in rf["next"], rf
assert [f.get("error") is None for f in rf["finals"]] == [True, False], rf["finals"]
ns["_render"] = fake_render

# ---- refused before FL closes: final outside FLH_ROOTS
flh_store.profile_set("Test", {"final_root": os.path.join(os.path.dirname(tmp), "elsewhere")})
ev.clear()
try:
    flh_finalize.fl_finalize(project=A, background=False); raise SystemExit("must refuse")
except PermissionError:
    pass
assert not ev, ev
flh_store.profile_set("Test", {"final_root": finals})

# ---- audio_limit tool + speed on a 4-minute 48 kHz stereo file
src = os.path.join(tmp, "in.wav"); out = os.path.join(tmp, "out.wav")
x = piano(240, 48000, seed=7); AE.save(src, x, 48000, 24)
t = time.time(); info = flh_finalize.audio_limit(src, out, -16.0, -1.0); took = time.time() - t
y, fs, _ = AE.load(out)
L, tp = m._lufs(y, fs), tp_db(y)
print(f"audio_limit 4 min 48 kHz stereo: {took:.1f} s total ({info['steps_s']}); in {info['lufs_in']} LUFS / "
      f"{info['true_peak_in']} dBTP -> {L:.2f} LUFS / {tp:.2f} dBTP; max GR {info['gain_reduction_max_db']} dB, "
      f"{info['ms_over_1db']} ms over 1 dB ({info['ms_over_1db'] / 2400:.1f}% of the song)")
assert abs(L + 16) <= 0.1 and tp <= -1.0, (L, tp)


def ffmpeg_pcm(path, ch, bps):
    """Decode with ffmpeg (CRC checks on) -> int samples, or None when ffmpeg isn't installed."""
    if not shutil.which("ffmpeg"):
        return None
    r = subprocess.run(["ffmpeg", "-v", "error", "-err_detect", "crccheck+bitstream+explode", "-i", path, "-f", "s32le",
                        "-"], capture_output=True)
    assert r.returncode == 0 and not r.stderr, r.stderr[:300]
    return np.frombuffer(r.stdout, "<i4").reshape(-1, ch).astype(np.int64) >> (32 - bps)


# ---- flac_lite: edge cases decode bit-exact (review decoder, ffmpeg if present), every frame's CRC-16 checked
def crc_ok(path):
    data = open(path, "rb").read()
    fr = R._frame_list(data, 4 + 4 + 34, 16)
    ends = [f["off"] for f in fr[1:]] + [len(data)]
    return len(fr) and all(flac_lite._crc16(data[f["off"]:e - 2]) == int.from_bytes(data[e - 2:e], "big")
                           for f, e in zip(fr, ends))


rng = np.random.default_rng(0)
flac_lite._POW.clear()                                       # tables rebuilt while chunks encode in threads
for bps in (16, 24):
    top = (1 << (bps - 1)) - 1
    p_ = np.round(piano(25, 44100, seed=3) * top).astype(np.int64)
    cases = {"noise": rng.integers(-top - 1, top + 1, (10000, 2)), "mono": p_[:9000, :1],
             "silence+click": np.zeros((4096 * 3 + 17, 2), np.int64), "one sample": np.array([[1, -1]]),
             "three": rng.integers(-100, 100, (3, 2)), "piano": p_, "full scale": np.where(rng.random((8192, 2)) > .5, top, -top - 1),
             "3 channels": rng.integers(-1000, 1000, (5000, 3)), "left only": np.stack([p_[:, 0], 0 * p_[:, 0]], 1)}
    cases["silence+click"][5000:5003] = 7
    for (name, xq), rate in zip(cases.items(), (44100, 48000, 88200, 96000, 22050, 32000, 192000, 11025, 44100)):
        fp = os.path.join(tmp, f"edge {name} {bps}.flac")
        info = real_encode(fp, xq, rate, bps)
        y2, r2, b2 = R.flac_load(fp)
        assert r2 == rate and b2 == bps and (np.round(y2 * (1 << (bps - 1))).astype(np.int64) == xq).all(), (name, bps)
        z = ffmpeg_pcm(fp, xq.shape[1], bps)
        assert z is None or (z.shape == xq.shape and (z == xq).all()), (name, bps, "ffmpeg")
        assert crc_ok(fp), (name, bps, "crc")
    print(f"flac_lite {bps}-bit: {len(cases)} edge cases bit-exact, CRCs ok; last {info['bytes']} B")
try:
    real_encode(os.path.join(tmp, "bad.flac"), np.array([[40000, 0]]), 44100, 16); raise SystemExit("range")
except ValueError:
    assert not os.path.exists(os.path.join(tmp, "bad.flac.part"))

# ---- the finishing pass on its own: every sample rate, mono, 16/24-bit, decoded back bit-exact
spied = {}
def spy2(path, q, rate, bps):
    spied[path] = q.copy(); return real_encode(path, q, rate, bps)
flac_lite.encode = spy2
for fs_, ch_, bits_, tgt in ((44100, 2, 16, -14.0), (48000, 2, 24, -9.0), (88200, 2, 24, -16.0), (96000, 1, 16, -14.0),
                             (48000, 1, 24, -23.0)):
    x = piano(20, fs_, seed=fs_ + ch_)[:, :ch_]
    out = os.path.join(tmp, f"fin_{fs_}_{ch_}_{bits_}.flac")
    t = time.time(); info = flh_finalize.finish(x, fs_, out, tgt, -1.0, bits=bits_); took = time.time() - t
    q = spied[out]; y2, r2, b2 = R.flac_load(out)
    assert r2 == fs_ and b2 == bits_ and y2.shape == (len(q), ch_), (r2, b2, y2.shape)
    assert (np.round(y2 * (1 << (bits_ - 1))).astype(np.int64) == q).all(), "review decoder mismatch"
    z = ffmpeg_pcm(out, ch_, bits_)
    assert z is None or (z == q).all(), "ffmpeg decode mismatch"
    L, tp = m._lufs(y2, fs_), tp_db(y2)
    print(f"finish {fs_} Hz {ch_} ch {bits_}-bit -> {L:.3f} LUFS (target {tgt}) {tp:.2f} dBTP, limited "
          f"{info['gain_reduction_max_db']} dB, {took:.2f} s, ffmpeg {'ok' if z is not None else 'absent'}")
    assert abs(L - tgt) <= 0.1 and tp <= -1.0, (L, tp)
    assert info["lufs"] == round(L, 2) or abs(info["lufs"] - L) < 0.011, (info["lufs"], L)

# ---- hard ending: auto fade only touches the decay, the last hit stays at full level (a fixed 2.5 s fade would not)
fs_ = 48000; n_ = 12 * fs_; tt = np.arange(n_) / fs_
x = np.zeros((n_ + fs_, 2)); x[:n_, 0] = x[:n_, 1] = 0.5 * np.sin(2 * np.pi * 220 * tt)   # abrupt stop, then silence
out = os.path.join(tmp, "hard_end.flac")


def last_hit_db(info):
    y2, _, _ = R.flac_load(out); s0 = n_ - int(info["trim_start_s"] * fs_)
    seg, ref = y2[s0 - int(0.5 * fs_):s0 - int(0.1 * fs_)], y2[2 * fs_:3 * fs_]
    return 20 * np.log10(np.sqrt(np.mean(seg ** 2)) / np.sqrt(np.mean(ref ** 2)))


info = flh_finalize.finish(x, fs_, out, -14.0, -1.0, fade_s="auto", bits=24); auto_db = last_hit_db(info)
info25 = flh_finalize.finish(x, fs_, out, -14.0, -1.0, fade_s=2.5, bits=24); fixed_db = last_hit_db(info25)
print(f"hard ending: auto fade {info['fade_s']} s -> last hit {auto_db:+.2f} dB; fixed 2.5 s fade -> {fixed_db:+.2f} dB")
assert info["fade_s"] <= 1.6 and auto_db > -0.2 and info25["fade_s"] == 2.5 and fixed_db < -2, (info, auto_db, fixed_db)

# ---- limiter off: plain gain, capped by the true peak
info = flh_finalize.finish(piano(20, 44100, seed=5), 44100, os.path.join(tmp, "plain.flac"), -14.0, -1.0, limiter=False)
assert not info["limited"] and info["true_peak_dbfs"] <= -1.0 and info.get("below_target_db", 0) > 0, info
info = flh_finalize.finish(piano(20, 44100, seed=5) * 0.02, 44100, os.path.join(tmp, "quiet.flac"), -30.0, -1.0)
assert not info["limited"] and abs(info["lufs"] + 30) <= 0.05, info
try:
    flh_finalize.finish(np.zeros((44100, 2)), 44100, os.path.join(tmp, "silent.flac"), -14.0); raise SystemExit("silent")
except ValueError:
    pass

# ---- no profile: neutral defaults, next to the project, non-ASCII path with spaces, note says how to set one
m._state({"collection": None})
uproj = os.path.join(tmp, "Mes projets — été", "Ünïcødé Sóng 1.flp"); os.makedirs(os.path.dirname(uproj))
open(uproj, "wb").write(b"FLhd fake")
ev.clear(); ev.append(("close", True))
r = flh_finalize.fl_finalize(project=uproj, background=False)
f0 = r["finals"][0]
assert f0["path"] == os.path.join(os.path.dirname(uproj), "Ünïcødé Sóng 1 - FINAL.flac") and os.path.isfile(f0["path"]), f0
assert f0["target"] == {"lufs": -14.0, "true_peak": -1.0, "bits": 24} and abs(f0["lufs"] + 14) <= 0.1, f0
assert "flh_profile" in f0["note"], f0
assert flac_lite.encode is spy2 and spied[f0["path"][:-5] + ".new.flac"].max() > 1 << 16, "not 24-bit"
print("no profile:", os.path.basename(f0["path"]), f0["lufs"], "LUFS", f0["true_peak"], "dBTP")
m._state({"collection": "Test"})

# ---- song length from the project and its tempo map
real = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(real):                                      # a public clone: the scrubbed committed copy
    real = os.path.join(HERE, "corpus", "base.flp")
assert abs(flh_finalize.song_seconds(real) - 310.0) < 0.5, flh_finalize.song_seconds(real)
orig_song = ns["_song"]
d0 = m._load(real); P0 = m._ppq(d0)
ns["_song"] = lambda d, e: (orig_song(d, e)[0], [(0, 60.0), (10 * 4 * P0, 120.0)])   # 10 bars at 60, then 120
assert abs(flh_finalize.song_seconds(real) - (40 + 67.5 * 4 * 0.5)) < 0.5, flh_finalize.song_seconds(real)
ns["_song"] = orig_song
assert flh_finalize.song_seconds(A) is None                 # not a real project: no estimate, no crash

# ---- live render progress: estimate from length x learned speed, and the WAV FL is writing
sp = flh_finalize._speed_path()
if os.path.exists(sp):
    os.remove(sp)
pproj = os.path.join(proj, "Progress Song.flp"); shutil.copy(real, pproj)


def slow_render(project, out_dir, formats, timeout_s, data=None, out_stem=None):
    work = tempfile.mkdtemp(prefix="flh_"); os.makedirs(os.path.join(work, "out"))
    wav = os.path.join(work, "out", "x.wav"); rate = 8000
    with open(wav, "wb") as f:                              # header first, then audio as FL renders it
        f.write(b"RIFF\0\0\0\0WAVEfmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little") +
                (1).to_bytes(2, "little") + rate.to_bytes(4, "little") + (rate * 2).to_bytes(4, "little") +
                (2).to_bytes(2, "little") + (16).to_bytes(2, "little") + b"data\0\0\0\0")
        for k in range(31):                                 # 310 s of audio in ~6.2 s
            f.write(b"\0" * (rate * 2 * 10)); f.flush(); time.sleep(0.2)
    out = os.path.join(out_dir, (out_stem or "x") + ".wav")
    AE.save(out, piano(20, 44100, seed=11), 44100, 24)
    shutil.rmtree(work)
    return {"rendered": [out], "seconds": 6.2}


ns["_render"] = slow_render
ev.clear(); ev.append(("close", True))
job = flh_finalize.fl_finalize(project=pproj, background=True)
assert job.get("eta_s"), job
seen = []
while m.JOBS[job["job"]]["status"] == "running":
    pr = m.JOBS[job["job"]].get("progress")
    if pr and (not seen or pr != seen[-1]):
        seen.append(dict(pr))
    time.sleep(0.25)
assert m.JOBS[job["job"]]["status"] == "done", m.JOBS[job["job"]]
rp = [p for p in seen if p.get("step") == "render" and p.get("render_pct") is not None]
print("progress:", [(p.get("render_pct"), p.get("eta_s"), p.get("source")) for p in rp])
assert len(rp) >= 3 and rp[-1]["render_pct"] > rp[0]["render_pct"] >= 0, rp
assert any(p.get("source") == "rendered audio" and p.get("rendered_s", 0) > 0 for p in rp), rp
assert all(p.get("eta_s") is not None for p in rp), rp
spd = json.load(open(sp))
assert spd["renders"] == 1 and 0.005 < spd["ratio"] < 0.1, spd    # ~6.5 s for a 310 s song
job = flh_finalize.fl_finalize(project=pproj, background=True)
assert abs(job["eta_s"] - (spd["ratio"] * 310 + flh_finalize._finish_est(310) + 5)) < 1, job
m._job_wait(job["job"], 120)
assert json.load(open(sp))["renders"] == 2


def quiet_render(project, out_dir, formats, timeout_s, data=None, out_stem=None):   # FL that writes only at the end
    time.sleep(4.5)
    out = os.path.join(out_dir, (out_stem or "x") + ".wav"); AE.save(out, piano(20, 44100, seed=12), 44100, 24)
    return {"rendered": [out], "seconds": 4.5}


ns["_render"] = quiet_render
job = flh_finalize.fl_finalize(project=pproj, background=True); seen = []
while m.JOBS[job["job"]]["status"] == "running":
    pr = m.JOBS[job["job"]].get("progress")
    if pr and pr.get("step") == "render" and pr.get("source"):
        seen.append(dict(pr))
    time.sleep(0.25)
assert m.JOBS[job["job"]]["status"] == "done" and seen and all(p["source"] == "estimate" for p in seen), seen
assert seen[-1]["render_pct"] > 0 and seen[-1]["elapsed_s"] >= 2, seen
ns["_render"] = fake_render

# ---- speed of the finishing pass: 4-minute 48 kHz stereo song, 24-bit (load + limit + trim/fade + FLAC + verify)
x = piano(240, 48000, seed=7); src = os.path.join(tmp, "render.wav"); AE.save(src, x, 48000, 24)
t = time.time(); x, fs, _ = flh_finalize.load_wav(src); tl = time.time() - t
t = time.time(); info = flh_finalize.finish(x, fs, os.path.join(tmp, "speed.flac"), -14.0, -1.0, bits=24); tf = time.time() - t
print(f"finish 4 min 48 kHz stereo: {tl + tf:.1f} s (load {tl:.2f}, {info['steps_s']}), {info['lufs']} LUFS, "
      f"{info['true_peak_dbfs']} dBTP, {info['bytes'] / 1e6:.1f} MB FLAC ({os.cpu_count()} CPUs)")
assert abs(info["lufs"] + 14) <= 0.05 and info["true_peak_dbfs"] <= -1.0, info
# ---- H1: unreachable loudness targets on a dense mix: never a crash, honest ceiling, render kept
def dense(sec, fs, seed=0):
    rng = np.random.default_rng(seed); n = int(fs * sec)
    W = np.fft.rfft(rng.standard_normal((n, 2)), axis=0); f = np.fft.rfftfreq(n, 1 / fs); f[0] = 1
    pink = np.fft.irfft(W / np.sqrt(f)[:, None], n, axis=0); kick = np.zeros(n)
    for s0 in np.arange(0, sec, 0.5):
        i = int(s0 * fs); L = min(int(0.25 * fs), n - i); tt = np.arange(L) / fs
        kick[i:i + L] += np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 15)
    x = pink / np.abs(pink).max() * 0.3 + 0.6 * kick[:, None]
    return x / np.abs(x).max()


xd = dense(20, 44100)
for tgt in (-8.0, -4.0):
    y, info = flh_finalize.limit(xd, 44100, tgt, -1.0)
    print(f"dense mix -> {tgt}: {info['lufs']} LUFS (max {info.get('max_lufs')}), TP {info['true_peak']}, "
          f"GR {info['gain_reduction_max_db']} dB")
    assert info.get("max_lufs") is not None and info["below_target_db"] > 0 and "mix changes" in info["note"], info
    assert -12.5 < info["lufs"] < -9.5 and info["true_peak"] <= -1.0, info
    assert info["gain_reduction_max_db"] <= flh_finalize.MAX_RED_DB + 0.5, info
info = flh_finalize.finish(xd, 44100, os.path.join(tmp, "dense.flac"), -6.0, -1.0, bits=24)
assert info.get("max_lufs") is not None and info["true_peak_dbfs"] <= -1.0 and info["lufs"] > -12.5, info
dc = np.full((44100 * 3, 2), 0.5)                                            # DC only: a clear error, no TypeError
for fn in (lambda: flh_finalize.limit(dc, 44100, -14), lambda: flh_finalize.finish(dc, 44100, os.path.join(tmp, "dc.flac"), -14)):
    try:
        fn(); raise SystemExit("DC must refuse")
    except ValueError as e:
        assert "silent" in str(e) or "DC" in str(e), e
dproj = os.path.join(proj, "Dense.flp"); open(dproj, "wb").write(b"FLhd fake")


def dense_render(project, out_dir, formats, timeout_s, data=None, out_stem=None):
    out = os.path.join(out_dir, (out_stem or "x") + ".wav"); AE.save(out, dense(15, 44100, 3), 44100, 24)
    return {"rendered": [out], "seconds": 0.1}


ns["_render"] = dense_render; ev.clear()
r = flh_finalize.fl_finalize(project=dproj, target_lufs=-6, background=False)
fd = r["finals"][0]
assert "error" not in fd and fd.get("max_lufs") is not None and os.path.isfile(fd["kept_wav"]), fd
assert "audio_limit" in fd["next"] and os.path.isfile(fd["path"]), fd
assert flh_store.journal_get(dproj)["final"]["max_lufs"] == fd["max_lufs"]
job = flh_finalize.fl_finalize(project=dproj, target_lufs=-6, background=True)   # warned up front from the journal
assert job.get("warnings") and "unreachable" in job["warnings"][0], job
m._job_wait(job["job"], 120)
real_finish = flh_finalize.finish
flh_finalize.finish = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
r = flh_finalize.fl_finalize(project=dproj, target_lufs=-14, background=False)  # finishing fails: render kept
flh_finalize.finish = real_finish
fd = r["finals"][0]
assert "boom" in fd["error"] and os.path.isfile(fd["kept_wav"]) and "audio_limit" in fd["next"], fd
ns["_render"] = fake_render

# ---- several finals from one render (a game mod + YouTube) and the collection's rules
r = flh_finalize.fl_finalize(project=B, background=False, finals=[{"name": "mod", "lufs": -22}, {"name": "youtube", "lufs": -14}],
                             rules={"min_ending_s": 1.0, "min_tail_s": 0.5})
fv = [f for f in r["finals"] if f.get("version")]
assert [f["version"] for f in fv] == ["mod", "youtube"] and all(os.path.isfile(f["path"]) for f in fv), r
assert abs(fv[0]["lufs"] + 22) < 0.1 and abs(fv[1]["lufs"] + 14) < 0.1, fv
assert fv[0]["path"].endswith("FINAL (mod).flac") and "rules" in fv[0] and "checks" in fv[0]["rules"], fv[0]
assert sum(1 for e in ev if e[0] == "render") >= 1
jb = flh_store.journal_get(B)
assert set(jb["finals"]) == {"mod", "youtube"} and jb["rules_check"]["checks"], jb.get("finals")
# a WAV final (a game's audio converter wants WAV), 16-bit, next to a FLAC one
r = flh_finalize.fl_finalize(project=B, background=False, finals=[{"name": "game", "lufs": -20, "format": "wav",
                                                                   "bits": 16}, {"name": "youtube", "lufs": -14}])
fw = next(f for f in r["finals"] if f.get("version") == "game")
assert fw["path"].endswith("FINAL (game).wav") and open(fw["path"], "rb").read(4) == b"RIFF", fw
import fl_audio_edit as _AE
xw, rw, bw, _ = _AE.load_ex(fw["path"])
assert bw == 16 and abs(_AE.integrated_loudness(xw, rw) + 20) < 0.3, (bw, _AE.integrated_loudness(xw, rw))
try:
    flh_finalize.fl_finalize(project=B, background=False, finals=[{"name": "x", "lufs": -14, "format": "mp3"}])
    raise SystemExit("mp3 finals must be refused")
except ValueError as e:
    assert "flac (default) or wav" in str(e), e

# rule_check on made-up audio: a hard start, a jump, a cut-off end vs a soft start, a long decay to silence
fs = 44100; tt = np.arange(int(fs * 40)) / fs
good = np.sin(2 * np.pi * 220 * tt) * np.minimum(1, tt / 0.05) * np.where(tt < 8, 0.5, 0.5 * np.exp(-(tt - 8) / 5))
good[-int(fs * 1.2):] = 0
rc = flh_finalize.rule_check(np.stack([good, good], 1), fs, {"min_ending_s": 25})
by = {c["rule"]: c for c in rc["checks"]}
assert by["ending_length"]["pass"] and by["ends_in_silence"]["pass"] and by["tail_not_cut"]["pass"], by
assert by["start_click"]["pass"] and by["level_jumps"]["pass"], by
bad = 0.05 * np.sin(2 * np.pi * 220 * tt); bad[int(fs * 10):] *= 10; bad[0] = 0.9   # jump at 10 s, click, no ending
rc = flh_finalize.rule_check(np.stack([bad, bad], 1), fs)
by = {c["rule"]: c for c in rc["checks"]}
assert not rc["pass"] and not by["ending_length"]["pass"] and not by["tail_not_cut"]["pass"], by
assert not by["level_jumps"]["pass"] and abs(by["level_jumps"]["value"][0]["at_s"] - 10) < 1 and not by["start_click"]["pass"], by
hum = np.stack([good + 0.003 * np.sin(2 * np.pi * 60 * tt)] * 2, 1)          # a hum under the whole song
assert not {c["rule"]: c for c in flh_finalize.rule_check(hum, fs)["checks"]}["ends_in_silence"]["pass"]
print("ok")
