"""fl_finalize measures every render against the file's song length BEFORE it becomes a FINAL (PC 2026-10-23: a
version copy's FINAL came out as long as the original's - the extended ending missing). A render more than ~1.5 s
shorter than the song is rendered once more after FL loaded the file (only without unsaved changes in FL), then
refused; the command line (which reads the file itself) is refused at once; length_check=false makes it anyway.
Run: python3 tests/test_finalize_length.py"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_finlen_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ["FLH_RENDER_CACHE_GB"] = "0"; os.environ["FLH_NO_WARM"] = "1"
tempfile.tempdir = os.path.join(tmp, "systemp"); os.makedirs(tempfile.tempdir)
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_finalize  # noqa: E402
import fl_audio_edit as AE  # noqa: E402
ns = vars(m)
flh_store.register(ns); flh_finalize.register(ns)

FS = 44100
try:
    proj = os.path.join(tmp, "proj"); os.makedirs(proj)
    song = os.path.join(proj, "Song Rework.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song)
    SONG_S = 12.0
    m._song_seconds = lambda p: (SONG_S, 6.0, 4.0)                 # the file's song through its tempo map
    st = {"route": "in_fl", "flag": 0, "lens": [], "loads": [], "renders": 0, "sound_s": None}

    def audio(sec, sound_s=None):
        n = int(sec * FS); t = np.arange(n) / FS
        y = 0.3 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * t))
        if sound_s is not None:
            y[int(sound_s * FS):] = 0.0                            # the sound died away before the song's end
        return np.stack([y, 0.9 * y], 1)

    def fake_cached(project, out_dir, formats="wav", timeout_s=900, out_stem=None, method="auto", close_fl=True,
                    store="exact", strict_file=False):
        assert strict_file, "a FINAL only reuses a render of this exact project file"
        st["renders"] += 1
        sec = st["lens"].pop(0)
        out = os.path.join(out_dir, f"{out_stem or 'render'}_{st['renders']}.wav")
        AE.save(out, audio(sec, st["sound_s"]), FS, 24)
        return {"rendered": [out], "seconds": 0.1, "method": st["route"], "closed_fl": None, "renders": 1}

    ns["_render_cached"] = fake_cached
    ns["_render_method"] = lambda p, f="wav", meth="auto", start_bar=None: st["route"]
    ns["fl_running"] = lambda: True
    ns["fl_close"] = lambda save=True: {"closed": True, "project": song}
    ns["fl_open"] = lambda project, save_current=True: {"opened": project}
    ns["_open_project_guess"] = lambda: (song, [])
    ns["_fl_changed_flag"] = lambda: st["flag"]
    ns["_fl_load_fresh"] = lambda p: st["loads"].append(p) or {"how": "loaded_fresh"}
    ns["files_recycle"] = lambda paths: {"recycled": paths}

    def run(lens, **kw):
        st["lens"] = list(lens); st["loads"].clear(); st["renders"] = 0
        r = flh_finalize.fl_finalize(project=song, song="Song Rework", background=False, reopen=False, **kw)
        return r, r["finals"][0]

    # 1. FL held another state: the first render is 10 s for a 12 s song -> FL loads the file, rendered again (OK)
    r, it = run([10.0, 12.6])
    assert r["ok"] and it.get("path") and os.path.isfile(it["path"]), r
    assert st["loads"] == [song] and st["renders"] == 2, (st, r)
    lc = it["length_check"]
    assert lc["render_s"] >= 12.5 and lc["first_render_s"] == 10.0 and "rendered again" in lc["re_rendered"], lc
    os.remove(it["path"])
    print("short render re-rendered after FL loaded the file ok")

    # 2. still short the second time: refused with the reason, no FINAL written
    r, it = run([10.0, 10.0])
    assert not r["ok"] and "RenderMismatch" in it["error"] and "rendered twice" in it["error"], r
    assert "time selection" in it["error"] and "length_check=false" in it["error"]
    assert not it.get("path") and st["renders"] == 2
    print("still short: refused ok")

    # 3. FL has unsaved changes: never loaded over them - refused at once (one render)
    st["flag"] = 1
    r, it = run([10.0])
    assert not r["ok"] and "unsaved changes" in it["error"] and st["loads"] == [] and st["renders"] == 1, r
    st["flag"] = 0
    print("unsaved changes in FL: not loaded over ok")

    # 4. the command line read the file itself: another render would be the same -> refused at once
    st["route"] = "cli"
    r, it = run([10.0], method="cli")
    assert not r["ok"] and "command-line render read the file itself" in it["error"] and st["renders"] == 1, r
    st["route"] = "in_fl"
    print("cli short: refused ok")

    # 5. length_check=false (after listening): made anyway
    r, it = run([10.0], length_check=False)
    assert r["ok"] and os.path.isfile(it["path"]) and st["renders"] == 1, r
    os.remove(it["path"])
    print("length_check=false ok")

    # 6. a full-length render whose sound ends early: a FINAL, with the honest note why it is shorter than the song
    st["sound_s"] = 4.0
    r, it = run([12.8])
    assert r["ok"] and it["length_check"]["final_s"] < 6.0 and "fell silent" in (it.get("ending_note") or ""), it
    st["sound_s"] = None
    print("full render, sound ended early: noted ok")
finally:
    shutil.rmtree(tmp, True)
print("ok")
