"""A merged reload FL didn't really do is caught, and a FINAL never reuses a render of another project file
(PC 2026-10-23: 'merged edits are in FL now (one reload, 2.5 s)' on a song that loads in 10-60 s; the FINAL was then
sample-identical to the previous day's).
- a reload implausibly fast for the project (_reload_suspect) is checked by FL's save: still the version from before
  the edit -> the edit goes back into the file, FL loads it through an empty project, checked again; FL ignoring both
  -> not claimed, stale-marked (nothing saves FL over the file), the background flush message says so;
- the render cache keeps the project file's hash with a render; fl_finalize (strict_file) reuses only a render of
  that exact file and says why it rendered again. Run: python3 tests/test_reload_check.py"""
import os
import shutil
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="flh_rchk_")
ROOT = os.path.join(TMP, "root"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = ROOT
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
FIX = os.path.join(HERE, "corpus", "base.flp")
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402

_real_sleep = m.time.sleep
m.time.sleep = lambda s: _real_sleep(min(s, 0.01))


def read(p):
    with open(p, "rb") as f:
        return f.read()


song = os.path.join(ROOT, "Heavy.flp"); shutil.copy(FIX, song)
FL = {"held": read(song), "ignore": 0, "reloads": [], "saves": 0}


def fl_save(p, wait_s=8.0, stop=None, explicit=False):          # FL writes what it holds
    if m._stale_block(p):
        return False
    FL["saves"] += 1
    with open(p, "wb") as f:
        f.write(FL["held"])
    m._note_open_proof(p)
    return True


def reload_(p, discard=False, force_blank=False):
    FL["reloads"].append("blank" if force_blank else "fast")
    if FL["ignore"]:
        FL["ignore"] -= 1                                         # FL 'reloads' in no time and keeps its copy
    else:
        FL["held"] = read(p)
    m._stale_clear(p); m._note_fl_holds(p)
    return {"open_dialog": 0.4, "load": 1.4} if not force_blank else {"fallback_load": 20.0}


m._save_in_fl_and_wait = fl_save
m._reload_in_fl = reload_
m._ping_ok = lambda: True
m._verify_in_background = lambda *a, **k: "verify-x"
m.BRIDGE.call = lambda cmd, timeout=15.0, **a: {"pong": True} if cmd == "ping" else {}
m.fl_running = lambda: True
m._open_project_guess = lambda: (song, [])


def edit(bpm):
    """What _hot_reload_locked does around a writer: FL's save (orig), the edit, then _reload_edited."""
    fl_save(song)
    orig = read(song); pre = m._fingerprint(song)
    getattr(m.flp_set_tempo, "__wrapped__", m.flp_set_tempo)(song, bpm)
    return m._reload_edited(song, {"output": song}, {}, orig, pre, False, None), orig


# ---- no load cost measured: nothing to judge, no extra save
r, _ = edit(101)
assert r["reloaded_in_fl"] is True and "reload_check" not in r and FL["reloads"] == ["fast"], r

# ---- the project loads in ~30 s here; FL 'reloads' in 1.8 s but really did load it: checked by its save, kept
for _ in range(3):
    m._note_load_cost(song, 30.0, "test")
FL["reloads"].clear(); s0 = FL["saves"]
r, _ = edit(102)
assert r["reloaded_in_fl"] is True and r["reload_check"]["verified"] is True, r
assert r["reload_check"]["reload_s"] == 1.8 and r["reload_check"]["load_cost_s"] == 30.0, r
assert m.flp_info(song)["tempo_bpm"] == 102 and FL["saves"] == s0 + 2 and "verify_job" not in r, r
assert m.project_load_cost(song)["seconds"] < 30.0, "a proven fast reload is averaged in"

# ---- FL ignored the fast reload (the PC case): caught, the edit back in the file, loaded through an empty project
for _ in range(5):
    m._note_load_cost(song, 30.0, "test")
FL["reloads"].clear(); FL["ignore"] = 1
cost_before = m.project_load_cost(song)["seconds"]
r, orig = edit(103)
assert r["reloaded_in_fl"] is True and r["reload_check"]["verified"] is True, r
assert "second load" in r["reload_check"]["note"] and FL["reloads"] == ["fast", "blank"], (r, FL["reloads"])
assert m.flp_info(song)["tempo_bpm"] == 103 and FL["held"] == read(song)
assert m.project_load_cost(song)["seconds"] == cost_before, "the bogus 1.8 s 'reload' isn't averaged in"
assert any(m._versions(song)), "FL's (old) save was backed up before the edit went back"

# ---- FL ignores both loads: not claimed, the file keeps the edit, nothing saves FL over it
FL["reloads"].clear(); FL["ignore"] = 2
r, orig = edit(104)
assert r["reloaded_in_fl"] is False and r["fl_shows_old_version"] and r["reload_check"]["verified"] is False, r
assert "didn't load this edit" in r["WARNING"] and m.flp_info(song)["tempo_bpm"] == 104, r
assert m._stale_block(song) and not fl_save(song), "no save over the edit while FL holds the old copy"
news = m._merged_news(song, {"ops": 2, **{k: r[k] for k in ("reloaded_in_fl", "WARNING", "reload_check")}}, 2, 2.5)
assert "not confirmed in FL" in news and "in FL now" not in news, news
ok_news = m._merged_news(song, {"ops": 2, "reloaded_in_fl": True, "reload_check": {"verified": True, "reload_s": 2.5,
                                                                                    "load_cost_s": 30.0}}, 2, 2.6)
assert "in FL now" in ok_news and "checked by FL's save" in ok_news, ok_news
m._stale_clear(song)
print("reload check ok")

# ---- the render cache: a FINAL reuses only a render of this exact project file
os.environ.pop("FLH_RENDER_CACHE_GB", None)
m.fl_running = lambda: False                                      # the command-line route, no FL to save
m._project_is_open = lambda p: False
renders = []


def fake_render_any(project, out_dir, fmt, timeout_s, out_stem=None, method="auto", close_fl=True):
    renders.append(project)
    os.makedirs(out_dir, exist_ok=True)
    f = os.path.join(out_dir, (out_stem or "render") + ".wav"); fs = 44100
    y = (0.2 * np.sin(2 * np.pi * 220 * np.arange(fs * 30) / fs) * 32767).astype("<i2")
    with wave.open(f, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs); w.writeframes(y.tobytes())
    return {"rendered": [f], "seconds": 0.1, "method": "cli"}


m._render_any = fake_render_any
m._render_short = lambda p, f: None
out = os.path.join(ROOT, "out")
r1 = m._render_cached(song, out, "wav", store="exact", strict_file=True)
r2 = m._render_cached(song, out, "wav", store="exact", strict_file=True)
assert len(renders) == 1 and "exact project file" in r2.get("reused", ""), (renders, r2)
assert m._render_cache_peek(song, "wav", "cli", strict_file=True)
# a byte change the sound key leaves out (the project title is a save-time / text event): same sound key ...
m.flp_project_info(song, output=song, title="Heavy (renamed)")
assert m._render_cache_peek(song, "wav", "cli") and not m._render_cache_peek(song, "wav", "cli", strict_file=True)
r3 = m._render_cached(song, out, "wav", store="exact", strict_file=True)   # ... a FINAL renders again, says why
assert len(renders) == 2 and not r3.get("reused") and "hash differs" in r3.get("cache_note", ""), r3
r4 = m._render_cached(song, out, "wav", store="external")                   # other renders keep reusing by sound
assert len(renders) == 2 and r4.get("reused"), r4
# the key moves with every sound-relevant change of the file (tempo, mixer)
RC = m._rc()
k0 = RC.mix_key(RC.fingerprint(read(song)), formats="wav")
getattr(m.flp_set_tempo, "__wrapped__", m.flp_set_tempo)(song, 77)
k1 = RC.mix_key(RC.fingerprint(read(song)), formats="wav")
getattr(m.flp_mixer, "__wrapped__", m.flp_mixer)(song, song, [{"track": 1, "volume_db": -6.0}])
k2 = RC.mix_key(RC.fingerprint(read(song)), formats="wav")
assert len({k0, k1, k2}) == 3, "every sound-relevant edit changes the render key"
r5 = m._render_cached(song, out, "wav", store="exact", strict_file=True)
assert len(renders) == 3 and not r5.get("reused"), r5
shutil.rmtree(TMP, True)
print("ok")
