"""fl_preview: whole song or some channels through FL's own export (mocked), others muted live and put back,
measured on the PC; the WAV doesn't pile up. fl_save names the file it saved. (no FL needed)"""
import os, shutil, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_prev_"); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
REAL_UNTITLED = m._is_untitled
try:
    proj = os.path.join(tmp, "song.flp"); open(proj, "wb").write(b"x")
    calls = []
    m.fl_running = lambda: True
    m._open_project_guess = lambda: (proj, [])
    m.fl_state = lambda: {"channels": [{"index": i, "muted": i == 3} for i in range(5)]}
    m.fl_channel_set = lambda i, **k: calls.append((i, k.get("muted")))
    def fake_in_fl(project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None):
        f = os.path.join(out_dir, "song.wav"); fs = 44100; t = np.arange(fs * 2) / fs
        y = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
        with wave.open(f, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs); w.writeframes(y.tobytes())
        return {"rendered": [f], "seconds": 1.0}
    m._render_in_fl = fake_in_fl
    r = m.fl_preview(channels=[1, 2], rules={})
    assert sorted(i for i, mu in calls if mu) == [0, 4] and sorted(i for i, mu in calls if mu is False) == [0, 4], calls
    assert -16 < r["lufs"] < -12 and not r["silent"] and "rules" in r and r["channels"] == [1, 2], r
    assert not os.path.exists(os.path.join(tmp, "_preview")), "preview WAV (and its empty folder) removed after measuring"
    r = m.fl_preview(keep=True)
    assert r["channels"] == "all" and os.path.isfile(r["wav"]), r
    m._save_in_fl_and_wait = lambda p, wait_s=8.0, **k: True
    m._fl_main_window = lambda: (1, "song - FL Studio")
    m._is_untitled = lambda t: False
    assert m.fl_save()["saved"] == proj
    # ---- PC 2026-10-23 (sound.shape): FL's title couldn't be mapped to a file right after a check opened its song
    m.PREVIEW_TITLE_WAIT_S = 0.6; m.PREVIEW_LOADING_WAIT_S = 2.0
    m._is_untitled = REAL_UNTITLED
    m._fl_still_working = lambda: False
    sb = os.path.join(tmp, "sb"); os.makedirs(sb); song2 = os.path.join(sb, "check sound.flp")
    open(song2, "wb").write(b"x")
    st = {}
    m._state = lambda upd=None: (st.update(upd) if upd else None) or st
    TITLE = {"t": "check sound - FL Studio 2026"}
    m._main_title = lambda: TITLE["t"]
    m._fl_main_window = lambda: (1, TITLE["t"])
    m._project_names = lambda p: {os.path.splitext(os.path.basename(p))[0].lower()}
    m._open_project_guess = lambda: (None, [])                    # e.g. two 'check sound' files: the guess can't tell
    m._GUESS_WHY["why"] = "FL shows 'check sound' and 2 project files have that name"
    # 1) guess fails, the project fl-headless last opened matches FL's title -> preview proceeds with it
    st["last_opened"] = song2
    r = m.fl_preview(keep=True)
    assert os.path.normcase(r["project"]) == os.path.normcase(os.path.abspath(song2)) and "last opened" in r["project_from"], r
    assert os.path.isfile(r["wav"]) and os.path.dirname(r["wav"]).startswith(sb), r
    # 2) FL's title says untitled for a while (still loading), then the song -> proceeds
    import threading, time as _t
    TITLE["t"] = "Untitled - FL Studio 2026"
    threading.Timer(1.0, lambda: TITLE.__setitem__("t", "check sound - FL Studio 2026")).start()
    t0 = _t.time(); r = m.fl_preview(measure=True)
    assert r["project"].endswith("check sound.flp") and 0.9 < _t.time() - t0 < 6, (r, _t.time() - t0)
    # a title naming another song never maps to the last-opened one
    TITLE["t"] = "Other song - FL Studio 2026"
    try:
        m.fl_preview(); raise AssertionError("no error")
    except RuntimeError as e:
        assert "Other song" in str(e) and "no file yet" in str(e), e
    # 3) a real untitled project -> the same clear error, saying what FL's title shows
    TITLE["t"] = "Untitled - FL Studio 2026"; st.pop("last_opened")
    m._GUESS_WHY["why"] = None
    t0 = _t.time()
    try:
        m.fl_preview(); raise AssertionError("no error")
    except RuntimeError as e:
        assert "no file yet (save it once)" in str(e) and "Untitled - FL Studio 2026" in str(e), e
    assert _t.time() - t0 < 1.5, "an untitled project FL isn't busy with ends after the short wait"
finally:
    shutil.rmtree(tmp, True)
print("preview ok")
