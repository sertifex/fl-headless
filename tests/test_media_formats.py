"""AIFF samples read natively (all uncompressed kinds); MP3 / M4A go through Windows Media Foundation (mf_audio) -
off Windows they fail with a plain reason, and on Windows every reader (audio tools, edits, catalog) routes them
there. Run: python3 tests/test_media_formats.py"""
import os, sys, shutil, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_media_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import fl_audio_edit as AE, flh_catalog as C, mf_audio  # noqa: E402

try:
    aif = os.path.join(HERE, "fixtures", "tone_a4_24bit.aiff")          # 0.5 s, 440 Hz, mono, 48 kHz, 24-bit BE
    x, fs, bits = AE.aiff_load(aif)
    assert (fs, bits, x.shape) == (48000, 24, (24000, 1)), (fs, bits, x.shape)
    f = np.fft.rfftfreq(1 << 15, 1 / fs); P = np.abs(np.fft.rfft(x[:, 0], 1 << 15))
    assert abs(f[np.argmax(P)] - 440) < 2
    y, fs2, b2 = m._wav_load(aif)
    assert fs2 == 48000 and np.array_equal(y, x)
    z, fs3, b3, bad = AE.load_ex(aif, np.float32)
    assert fs3 == 48000 and b3 == 24 and np.allclose(z, x, atol=1e-6)
    ft = C.sample_features(aif)
    assert abs(ft["dur"] - 0.5) < 0.01 and ft.get("pitch") in (68, 69, 70), ft
    info = m.TOOLS["audio_info"][0](path=aif)
    assert info["rate"] == 48000 and info["channels"] == 1, info
    # MP3 off Windows: a plain reason, no crash
    mp3 = os.path.join(tmp, "ref.mp3"); open(mp3, "wb").write(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" * 100)
    ok, why = mf_audio.available()
    if sys.platform != "win32":
        assert not ok and "Windows" in why
        try:
            m._wav_load(mp3); raise SystemExit("must fail off Windows")
        except (OSError, ValueError) as e:
            assert "Windows" in str(e) or "Media Foundation" in str(e), e
    # on Windows the readers hand MP3 / M4A to Media Foundation (simulated here)
    real = mf_audio.decode, mf_audio.available
    sig = (0.3 * np.sin(2 * np.pi * 220 * np.arange(22050) / 44100)).astype(np.float32)
    mf_audio.decode = lambda p, max_seconds=None: (np.stack([sig, sig], 1), 44100)
    mf_audio.available = lambda: (True, "ok")
    try:
        y, fs4, _ = m._wav_load(mp3)
        assert fs4 == 44100 and y.shape == (22050, 2)
        z, fs5, _, _ = AE.load_ex(mp3)
        assert fs5 == 44100 and z.shape == (22050, 2)
        a = m.TOOLS["audio_analyze"][0](path=mp3)
        assert a.get("whole", {}).get("lufs") is not None or a.get("lufs") is not None, a
    finally:
        mf_audio.decode, mf_audio.available = real
finally:
    shutil.rmtree(tmp, True)
print("media formats ok")
