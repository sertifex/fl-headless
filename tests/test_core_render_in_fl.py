"""Render from the open FL (method in_fl): Ctrl+R -> Save dialog -> export window -> Start -> file complete, the
probes (nothing rendered), clean fallback to the command line, auto only after it worked here, fl_render /
_render_any wiring. FL's windows are simulated. Run: python3 tests/test_core_render_in_fl.py"""
import os, shutil, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
proj = os.path.join(tmp, "My Song.flp"); shutil.copy(FIX, proj)
out = os.path.join(tmp, "renders")

FL = {"dialogs": [], "alive": set(), "keys": [], "fail": None, "project": proj, "opened": []}
m.IS_WINDOWS = True
m.IN_FL_WAITS.update(save_dialog=1.0, save_dialog_late=0.5, export_dialog=1.0)
m.fl_running = lambda: True
m._fl_main_window = lambda: (1, os.path.splitext(os.path.basename(FL["project"]))[0] + " - FL Studio 2026")
m._open_project_guess = lambda: (FL["project"], [])
m.fl_dialogs = lambda: [dict(d) for d in FL["dialogs"]]
m._window_alive = lambda h: h in FL["alive"]
m._fl_foreground = lambda: True
m.BRIDGE.call = lambda cmd, timeout=15.0, **a: {"ok": True}
m.fl_snapshot = lambda hwnd=None, **k: {"_image_png": "PNGDATA"}


def keys(vk, mods=()):
    FL["keys"].append((vk, tuple(mods)))
    if FL["fail"] == "no_save_dialog":
        return
    FL["dialogs"].append({"hwnd": 10, "title": "Save As", "class": "#32770", "kind": "save_as", "file_dialog": True,
                          "text": [], "buttons": [], "blocks_fl": True})
    FL["alive"].add(10)


m._send_keys = keys
m._file_dialog_info = lambda h: {"edits": [(11, "My Song.wav")], "combos": ["Wave file (*.wav)"]}


def close(h):
    FL["alive"].discard(h); FL["dialogs"] = [d for d in FL["dialogs"] if d["hwnd"] != h]


def submit(h, path):
    FL["target"] = path; close(h)
    if FL["fail"] != "no_export":
        name = os.path.basename(path) if FL["fail"] != "wrong_name" else "My Song.wav"   # FL shows the name it got
        FL["dialogs"].append({"hwnd": 20, "title": "Rendering to " + name, "class": "TWAVRenderForm", "kind": "other",
                              "text": [], "buttons": [], "blocks_fl": True})
        FL["alive"].add(20)
    return True


m._file_dialog_submit = submit
m._file_dialog_cancel = lambda h: (close(h), True)[1]
m._press_vk = lambda h, vk, wait_s=1.5: (close(h), "posted")[1]


def start(h, target):
    if FL["fail"] == "no_start":
        return False

    def write():                                      # FL writes the WAV for a while, then closes its window
        with open(target, "wb") as f:
            for _ in range(4):
                f.write(b"RIFF" + b"\0" * 4000); f.flush(); time.sleep(0.15)
        close(h)
    threading.Thread(target=write, daemon=True).start()
    return True


m._export_start = start

# ---- method choice: auto stays on the command line until an in-FL export has worked here
assert m._render_method(proj, "wav", "auto") == "cli"
assert m._render_method(proj, "wav", "in_fl") == "in_fl" and m._render_method(proj, "wav", "cli") == "cli"
try:
    m._render_method(proj, "wav", "fast"); raise SystemExit("bad method accepted")
except ValueError:
    pass

# ---- probes: nothing rendered, dialogs closed again
r = m.fl_render_probe()
assert r["ok"] and r["probe"] == "save_dialog" and r["seen"]["save_dialog"]["file_name"] == "My Song.wav", r
assert r["seen"]["cancelled"] and not FL["dialogs"] and FL["keys"][-1] == (0x52, (0x11,)), (r, FL)
r = m.fl_render_probe("export_dialog")
assert r["ok"] and r["seen"]["export_dialog"]["class"] == "TWAVRenderForm" and r["_image_png"] == "PNGDATA", r
assert r["seen"]["cancelled"] and not r["seen"]["file_written"] and not FL["dialogs"], r
print("probes ok")

# ---- a real in-FL render of the open project: no close, no load
m.fl_close = lambda **k: (_ for _ in ()).throw(AssertionError("must not close FL"))
m.fl_open = lambda p: (_ for _ in ()).throw(AssertionError("must not reopen the open project"))
r = m.fl_render(proj, output_dir=out, method="in_fl")
assert r["method"] == "in_fl" and r["rendered"] == [os.path.join(out, "My Song.wav")], r
assert os.path.getsize(r["rendered"][0]) == 4 * 4004 and set(r["steps_s"]) >= {"save_dialog", "export_dialog",
                                                                                  "start", "render"}, r
assert not os.path.exists(os.path.dirname(os.path.dirname(FL["target"])))     # temp folder cleaned
assert m._state()["in_fl_render"]["ok"] is True and m._render_method(proj, "wav", "auto") == "in_fl"
assert m._render_method(proj, "wav,mp3", "auto") == "cli" and m._render_method(proj, "flac", "auto") == "in_fl"
# FLAC from the open FL: FL's WAV, encoded losslessly here, the WAV removed (PC: FL's export has no FLAC shortcut)
import wave as _wave, numpy as _np  # noqa: E402
wv = os.path.join(out, "flac test.wav")
_sig = (_np.sin(_np.arange(4410) / 7.0) * 20000).astype("<i2")
with _wave.open(wv, "wb") as w_:
    w_.setnchannels(2); w_.setsampwidth(2); w_.setframerate(44100)
    w_.writeframes(_np.repeat(_sig, 2).tobytes())
fl_ = m._wav_to_flac(wv)
assert fl_.endswith(".flac") and not os.path.exists(wv)
_x, _fs, _b = m._wav_load(fl_)
assert _fs == 44100 and _b == 16 and _np.array_equal(_np.round(_x[:, 0] * 32768).astype(int), _sig.astype(int))
os.remove(fl_)
assert m._render_method(proj, "wav", "auto", start_bar=5) == "cli"
# mp3 uses Shift+Ctrl+R
r = m.fl_render(proj, output_dir=out, formats="mp3", method="in_fl")
assert FL["keys"][-1] == (0x52, (0x11, 0x10)) and r["rendered"][0].endswith(".mp3"), FL["keys"]
print("in-FL render ok")

# ---- another project: FL opens it first (one load), then exports
other = os.path.join(tmp, "Other.flp"); shutil.copy(FIX, other)
m.fl_open = lambda p: (FL["opened"].append(p), FL.__setitem__("project", p))
r = m._render_in_fl(other, out, "wav", 60)
assert FL["opened"] == [other] and "open" in r["steps_s"] and r["rendered"][0].endswith("Other.wav"), r
FL["project"] = proj
print("open-then-render ok")

# ---- failures before rendering: clean InFlUnavailable, fallback to cli only when close_fl allows it
rendered_cli = []
m._render = lambda project, out_dir, formats, timeout_s, data=None, out_stem=None: (
    rendered_cli.append(project), {"rendered": [os.path.join(out_dir, "x.wav")], "seconds": 1.0})[1]
for fail in ("no_save_dialog", "no_export", "no_start", "wrong_name"):
    FL["fail"] = fail
    try:
        m.fl_render(proj, output_dir=out, method="in_fl"); raise SystemExit(f"{fail}: no error")
    except RuntimeError as e:
        assert "didn't start" in str(e) and "close_fl=true" in str(e), (fail, e)
    assert not FL["dialogs"], (fail, FL["dialogs"])                            # nothing left open in FL
assert m._state()["in_fl_render"]["ok"] is False and m._state()["in_fl_render"]["failures"] == 4
assert m._render_method(proj, "wav", "auto") == "cli"                        # auto backs off after a failure
m._note_in_fl(True); m._note_in_fl(False, "slow dialog")                     # ONE failure after it worked: still
assert m._render_method(proj, "wav", "auto") == "in_fl"                      # in FL (no closing FL for the cli)
m._note_in_fl(False, "x"); m._note_in_fl(False, "x")
assert m._render_method(proj, "wav", "auto") == "cli"                        # 3 in a row: the command line
closes = []
m.fl_close = lambda save=True, **k: (closes.append(save), {"closed": True, "project": proj})[1]
m.fl_open = lambda p: FL["opened"].append(p)
r = m.fl_render(proj, output_dir=out, method="in_fl", close_fl=True)
assert r["method"] == "cli" and "in_fl_fallback" in r and closes == [True] and rendered_cli == [proj], r
# a blocking popup: refused up front, nothing pressed
FL["fail"] = None; FL["dialogs"] = [{"hwnd": 5, "title": "Confirm", "class": "TMsgForm", "kind": "save_changes",
                                     "text": [], "buttons": [], "blocks_fl": True}]
nkeys = len(FL["keys"])
try:
    m._render_in_fl(proj, out, "wav", 60); raise SystemExit("rendered over a popup")
except m.InFlUnavailable as e:
    assert "popup" in str(e) and len(FL["keys"]) == nkeys, e
FL["dialogs"] = []
try:
    m._render_in_fl(proj, out, "wav,mp3", 60); raise SystemExit("two formats accepted")
except m.InFlUnavailable:
    pass
print("fallback ok")

# ---- _render_any (fl_finalize's route): in_fl keeps FL open; a failed start falls back with FL closed once
closes.clear(); rendered_cli.clear()
r = m._render_any(proj, out, "wav", 60, out_stem="render", method="in_fl")
assert r["method"] == "in_fl" and r["closed_fl"] is None and r["rendered"][0].endswith("render.wav"), r
FL["fail"] = "no_export"
r = m._render_any(proj, out, "wav", 60, out_stem="render", method="in_fl")
assert r["method"] == "cli" and r["closed_fl"]["closed"] and closes == [True] and "in_fl_fallback" in r, r
try:
    m._render_any(proj, out, "wav", 60, method="in_fl", close_fl=False); raise SystemExit("no error")
except m.InFlUnavailable:
    pass
print("ok")
