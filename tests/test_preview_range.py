"""fl_preview format='mp3' / 'flac' and bar ranges (section / start_bar-end_bar) through the real in-FL export path
with FL's windows simulated: FL's own MP3 export, '<song> preview.mp3' in '_preview' (share_path, replaced each time,
one preview at a time), numbers from the MP3 (decoder simulated) or from a WAV FL wrote beside it (export window with
both formats ticked - not taken for split tracks), measure_skipped when the MP3 can't be decoded; ranges cut from the
whole-song render: from the render cache (no export) or one fresh export, MP3 cut at frame boundaries (no re-encode),
WAV/FLAC sample-exact with a tail; sections from playlist markers, else the song journal; the cache keys on format; a
render shorter than the song (FL's time selection) refuses to cut. Run: python3 tests/test_preview_range.py"""
import os
import shutil
import struct
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="flh_prange_")
ROOT = os.path.join(TMP, "root"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = ROOT; os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
FIX = os.path.join(HERE, "corpus", "legacy_playlist32.flp")      # 11 bars of 4/4 at 60 BPM: 4 s a bar, 44 s
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import fl_audio_edit  # noqa: E402

FS = 32000
AMP = {1: 0.05, 5: 0.2, 9: 0.6}                                   # bars 1-4 quiet, 5-8 medium, 9-11 loud


def song_level(t):
    bar = int(t // 4) + 1
    return AMP[9] if bar >= 9 else AMP[5] if bar >= 5 else AMP[1]


def make_song(name, marks=True):
    p = os.path.join(ROOT, name, name + ".flp"); os.makedirs(os.path.dirname(p))
    shutil.copy(FIX, p)
    if marks:
        d = m._load(p)
        d, _ = m._set_markers(d, [(1, "Intro"), (5, "Verse"), (9, "Finale")], m._ppq(d), 4)
        m._save(d, p)
    return p


# ---- fake MP3: real Layer III frame headers (MPEG-1, 32 kHz, 64 kbps, mono: 288 bytes), one level byte per frame
HDR = bytes([0xFF, 0xFB, (5 << 4) | (2 << 2), 0xC0])
FRAME = 288


def mp3_bytes(levels, info=True):
    out = b"ID3\x03\x00\x00\x00\x00\x00\x14" + b"\0" * 20             # ID3v2, 20 bytes of tag
    if info:
        fr = bytearray(FRAME); fr[:4] = HDR; fr[21:25] = b"Info"
        out += bytes(fr)
    for lv in levels:
        fr = bytearray(FRAME); fr[:4] = HDR; fr[4] = int(round(lv * 255))
        out += bytes(fr)
    return out + b"TAG" + b"\0" * 125


DECODE = {"ok": True}


def fake_media_load(path, dtype=np.float64):                      # Windows' decoder, simulated
    if not str(path).lower().endswith(".mp3"):
        return None
    if not DECODE["ok"]:
        raise OSError("MP3/M4A decoding uses Windows Media Foundation (Media Feature Pack missing)")
    info = m._mp3_frames(open(path, "rb").read())
    data = open(path, "rb").read()
    lv = np.repeat([data[o + 4] / 255.0 for o, _ in info["frames"]], 1152)
    t = np.arange(len(lv)) / FS
    return (lv * np.sin(2 * np.pi * 220 * t))[:, None].astype(dtype), FS, 16


fl_audio_edit._media_load = fake_media_load


def wav_write(path, seconds):
    t = np.arange(int(seconds * FS)) / FS
    lv = np.array([song_level(s) for s in np.arange(int(seconds * 10)) / 10.0]).repeat(FS // 10)[:len(t)]
    y = (lv * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(FS); w.writeframes(y.tobytes())


# ---- FL: its windows and export, simulated (the server's own _render_in_fl_raw drives them)
FL = {"project": None, "dialogs": [], "alive": set(), "keys": [], "exports": [], "both": False, "short": False}
m.IS_WINDOWS = True
m.fl_running = lambda: True
m._fl_main_window = lambda: (1, os.path.splitext(os.path.basename(FL["project"]))[0] + " - FL Studio 2026")
m._open_project_guess = lambda: (FL["project"], [])
m._preview_project = lambda: (FL["project"], "FL's title")
m._preview_fl_on = lambda p, wait_s=None: True
m._fl_changed_flag = lambda: 0
m._fl_load_file = lambda p, why="render": {"how": "already"}
m._fl_foreground = lambda: True
m.BRIDGE.call = lambda cmd, timeout=15.0, **a: {"ok": True}
m.fl_dialogs = lambda: [dict(d) for d in FL["dialogs"]]
m._window_alive = lambda h: h in FL["alive"]
m._file_dialog_info = lambda h: {"edits": [(11, "x")], "combos": ["(*.mp3)"]}
m._wait_render_done = lambda path, h, timeout_s, t0: (True, "file complete (simulated)")
m.files_recycle = lambda paths, *a, **k: [shutil.rmtree(x, True) for x in paths]   # (the Recycle Bin on a PC)
ST = {}
m._state = lambda upd=None: (ST.update(upd) if upd else None) or ST


def close(h):
    FL["alive"].discard(h); FL["dialogs"] = [d for d in FL["dialogs"] if d["hwnd"] != h]


def keys(vk, mods=()):
    FL["keys"].append((vk, tuple(mods)))
    FL["dialogs"].append({"hwnd": 10, "title": "Save As", "class": "#32770", "kind": "save_as", "file_dialog": True,
                          "text": [], "buttons": [], "blocks_fl": True})
    FL["alive"].add(10)


def submit(h, path):
    FL["target"] = path; close(h)
    FL["dialogs"].append({"hwnd": 20, "title": "Rendering to " + os.path.basename(path), "class": "TWAVRenderForm",
                          "kind": "other", "text": [], "buttons": [], "blocks_fl": True})
    FL["alive"].add(20)
    return True


def start(h, target):                                             # FL renders the whole song (+1 s tail)
    secs = 20.0 if FL["short"] else 45.0
    FL["exports"].append(os.path.splitext(target)[1])
    if target.endswith(".mp3"):
        n = int(secs * FS / 1152)
        open(target, "wb").write(mp3_bytes([song_level(k * 1152 / FS) for k in range(n)]))
        if FL["both"]:
            wav_write(os.path.splitext(target)[0] + ".wav", secs)
    else:
        wav_write(target, secs)
    close(h)
    return True


m._send_keys = keys
m._file_dialog_submit = submit
m._file_dialog_cancel = lambda h: (close(h), True)[1]
m._press_vk = lambda h, vk, wait_s=1.5: (close(h), "posted")[1]
m._export_start = start


def preview(**kw):
    return m.fl_preview(**kw)


def files_in(folder):
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


try:
    # ---- the MP3 frame helpers
    tmpm = os.path.join(ROOT, "t.mp3"); open(tmpm, "wb").write(mp3_bytes([0.5] * 100))
    assert abs(m._mp3_seconds(tmpm) - 100 * 1152 / FS) < 1e-9, m._mp3_seconds(tmpm)   # Info frame + tags left out
    c = m._mp3_cut(tmpm, os.path.join(ROOT, "c.mp3"), 1.0, 2.0)
    assert c["bytes"] == FRAME * (56 - 25), c                     # frames 25 (27 - 2 pre-roll) .. 55
    assert abs(c["start_s"] - 25 * 1152 / FS) < 1e-3 and m._mp3_frames(open(os.path.join(ROOT, "c.mp3"), "rb").read())
    junk = b"\xff\xfb\x00garbage" + mp3_bytes([0.1] * 10, info=False)[30:]
    assert len(m._mp3_frames(junk)["frames"]) == 10, "junk before the frames is skipped"
    assert m._mp3_frames(b"RIFF" + b"\0" * 400) is None
    try:
        m._mp3_cut(tmpm, os.path.join(ROOT, "c.mp3"), 50.0); raise AssertionError("no error")
    except ValueError as e:
        assert "outside the MP3" in str(e), e
    try:
        preview(format="ogg"); raise AssertionError("no error")
    except ValueError as e:
        assert "wav | mp3 | flac" in str(e), e
    props = m.TOOLS["fl_preview"][2]["properties"]
    assert {"format", "section", "start_bar", "end_bar", "keep"} <= set(props), props
    assert {"format", "section"} <= set(m.ESSENTIAL_PARAMS["fl_preview"])

    # ---- A: format='mp3' of the whole song -> FL's MP3 export (Ctrl+Shift+R), '<song> preview.mp3' kept by default
    A = make_song("Song A"); FL["project"] = A; pv = os.path.join(os.path.dirname(A), "_preview")
    r = preview(format="mp3")
    assert FL["keys"][-1] == m.RENDER_KEYS["mp3"] and FL["exports"] == [".mp3"], FL
    assert r["renders"] == 1 and r["share_path"] == os.path.join(pv, "Song A preview.mp3"), r
    assert r["share_bytes"] == os.path.getsize(r["share_path"]) and r["share_mb"] < 1, r
    assert r["measured_from"] == "mp3" and "lossy" in r["measure_note"] and -30 < r["lufs"] < -5, r
    assert files_in(pv) == ["Song A preview.mp3"], files_in(pv)
    full_lufs = r["lufs"]
    # B: section 'Finale' (marker, bar 9 to the end) - the cached MP3 is cut: no export, frames, no re-encode
    r = preview(format="mp3", section="Finale")
    assert r["renders"] == 0 and FL["exports"] == [".mp3"] and "no export" in r["range_route"], r
    assert r["range"]["section"] == "Finale" and r["range"]["start_bar"] == 9 and "end_bar" not in r["range"], r
    assert r["range"]["section_from"] == "playlist marker" and r["seconds_render"] == 0.0, r
    assert r["lufs"] > full_lufs + 2 and abs(r["seconds"] - 13.0) < 0.1, (r["lufs"], full_lufs, r["seconds"])
    sh = r["share_path"]; assert sh.endswith("Song A preview.mp3") and files_in(pv) == ["Song A preview.mp3"]
    secs_cut = m._mp3_seconds(sh)
    assert abs(secs_cut - (45.0 - 32.0)) < 0.2 and 0 < r["share_cut"]["starts_s"] <= 3 * 1152 / FS, (secs_cut, r["share_cut"])
    # C: a section by prefix, any case -> bars 5-9 (+1 s tail in the file; the numbers cover the 16 s only)
    r = preview(format="mp3", section="verse")
    assert r["range"]["start_bar"] == 5 and r["range"]["end_bar"] == 9 and r["renders"] == 0, r
    assert abs(r["seconds"] - 16.0) < 0.1 and abs(m._mp3_seconds(r["share_path"]) - 17.07) < 0.1, r
    # D: WAV of bars 5-9 with the MP3 cached: the cache keys on format -> one WAV export, then cut
    r = preview(format="wav", start_bar=5, end_bar=9, keep=True)
    assert r["renders"] == 1 and FL["exports"] == [".mp3", ".wav"] and "one whole-song export" in r["range_route"], r
    assert r["share_path"].endswith("Song A preview.wav") and r["wav"] == r["share_path"], r
    assert files_in(pv) == ["Song A preview.wav"], ("one preview at a time", files_in(pv))
    x, fs, bits = m._wav_load(r["share_path"])
    assert bits == 24 and abs(len(x) / fs - 17.0) < 0.01 and abs(r["seconds"] - 16.0) < 0.01, (len(x) / fs, r)
    assert abs(x[:5]).max() < 0.01, "fade-in at the cut"
    # ... and now a WAV of the Finale comes from the cached WAV (no export); keep=false -> numbers only, no file
    r = preview(section="finale")
    assert r["renders"] == 0 and "share_path" not in r and "cached" in r["range_route"] and r["lufs"] > -12, r
    assert not os.path.isdir(pv) or files_in(pv) == ["Song A preview.wav"], files_in(pv)
    # FLAC of a range: sample-exact, lossless, from the cached... no: FLAC has its own key -> one export (FL's WAV ->
    # FLAC here), then cut and encoded
    r = preview(format="flac", start_bar=1, end_bar=3, keep=True)
    assert r["renders"] == 1 and r["share_path"].endswith("Song A preview.flac"), r
    xf, fsf, _ = m._wav_load(r["share_path"])
    assert abs(len(xf) / fsf - 9.0) < 0.01 and files_in(pv) == ["Song A preview.flac"], (len(xf) / fsf, files_in(pv))
    r2 = preview(format="flac", start_bar=1, end_bar=3, keep=True)
    assert r2["renders"] == 0 and abs(r2["lufs"] - r["lufs"]) < 0.05, r2
    # range errors: unknown section lists what there is; end before start; bars past the render
    for kw, want in (({"section": "Chorus"}, "Intro"), ({"start_bar": 9, "end_bar": 5}, "after start_bar"),
                     ({"section": "Finale", "start_bar": 2}, "not both"), ({"start_bar": 40}, "after the song's end"),
                     ({"start_bar": 0}, "1 or later")):
        try:
            preview(format="mp3", **kw); raise AssertionError(f"no error for {kw}")
        except ValueError as e:
            assert want in str(e), (kw, e)
    # whole-song MP3 again: reused from the cache, the share file is FL's MP3 (copied), numbers from the cache
    r = preview(format="mp3")
    assert r["renders"] == 0 and r["reused"] and r["share_bytes"] > 100000 and abs(r["lufs"] - full_lufs) < 0.01, r

    # ---- E: FL's export window has WAV + MP3 ticked: the WAV is measured from (exact) and never taken for split
    # tracks; nothing but the MP3 stays in '_preview'
    m._rc().clear()                                               # Songs E/F/J are copies of A: same sound key
    E = make_song("Song E"); FL["project"] = E; FL["both"] = True; pv = os.path.join(os.path.dirname(E), "_preview")
    r = preview(format="mp3", section="Finale")
    assert r["renders"] == 1 and r["measured_from"].startswith("wav") and "measure_note" not in r, r
    assert files_in(pv) == ["Song E preview.mp3"], files_in(pv)
    assert (ST.get("in_fl_split") or {}).get("on") is not True and "split_stems" not in r, ST.get("in_fl_split")
    FL["both"] = False

    # ---- F: no MP3 decoder on this PC (Windows N without the Media Feature Pack): the file still comes, honestly
    # without numbers
    m._rc().clear()
    F = make_song("Song F", marks=False); FL["project"] = F; DECODE["ok"] = False
    r = preview(format="mp3")
    assert "lufs" not in r and "format='wav'" in r["measure_skipped"] and os.path.isfile(r["share_path"]), r
    # H: no markers -> the song journal's sections
    import flh_store
    flh_store.journal_update(F, set={"sections": [{"name": "Bridge", "bar": 3, "end_bar": 5}]})
    r = preview(format="mp3", section="bridge")
    assert r["range"]["section_from"] == "song journal" and r["range"]["start_bar"] == 3 and r["renders"] == 0, r
    assert abs(m._mp3_seconds(r["share_path"]) - (8.0 + 1.0)) < 0.2 and "measure_skipped" in r, r
    DECODE["ok"] = True

    # ---- J: FL's export stopped early (a playlist time selection): no cut from it, and it isn't cached
    m._rc().clear()
    J = make_song("Song J"); FL["project"] = J; FL["short"] = True
    try:
        preview(format="mp3", section="Verse"); raise AssertionError("no error")
    except RuntimeError as e:
        assert "time selection" in str(e) and "19.9" in str(e), e
    assert files_in(os.path.join(os.path.dirname(J), "_preview")) == [], "the short export isn't left in the song"
    FL["short"] = False
    r = preview(format="mp3", section="Verse")
    assert r["renders"] == 1 and abs(r["seconds"] - 16.0) < 0.1, ("the short render wasn't reused", r)
    # ---- through the MCP dispatcher, as the assistant calls it ('formats' is taken for 'format')
    import json
    out = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "fl_preview", "arguments": {"formats": "mp3", "section": "Finale"}}})
    json.dumps(out, allow_nan=False)
    txt = out["content"][0]["text"]
    assert not out.get("isError") and "share_path" in txt and "Song J preview.mp3" in txt, out
finally:
    shutil.rmtree(TMP, True)
print("ok")
