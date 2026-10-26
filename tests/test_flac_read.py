"""FLAC reading: LPC-coded FLAC (FL's own export, the flac tool, ffmpeg) decodes bit exact with numpy (checked
against the MD5 the encoder stored in STREAMINFO), and every audio reader takes FLAC: the WAV loader behind the
audio_* tools, audio_mix_report folders of FLAC stems, fl_audio_edit, the sample catalog's features.
Run: python3 tests/test_flac_read.py (no FL, no ffmpeg needed)."""
import hashlib, os, shutil, sys, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_flac_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import flh_review as R, fl_audio_edit as AE, flh_catalog as C, flac_lite  # noqa: E402
FIX = {"lpc_pink_sine.flac": (44100, 24, 2, 26460), "lpc_mono16.flac": (22050, 16, 1, 8820)}


def md5_ok(path, x, bits):
    stored = open(path, "rb").read()[26:42]
    v = np.round(x * (1 << (bits - 1))).astype("<i4")
    raw = np.ascontiguousarray(v.reshape(-1)).view(np.uint8).reshape(-1, 4)[:, :(bits + 7) // 8].tobytes()
    return hashlib.md5(raw).digest() == stored


try:
    for name, (rate, bits, ch, n) in FIX.items():
        p = os.path.join(HERE, "fixtures", name)
        x, fs, b = R.flac_load(p)
        assert (fs, b, x.shape) == (rate, bits, (n, ch)), (name, fs, b, x.shape)
        assert md5_ok(p, x, b), f"{name}: decoded samples differ from the encoder's MD5"
        y, fs2, b2 = m._wav_load(p)                                   # the loader behind the audio_* tools
        assert fs2 == rate and np.array_equal(y, x)
        z, fs3, b3, bad = AE.load_ex(p, np.float32)
        assert fs3 == rate and b3 == bits and bad == 0 and np.allclose(z, x, atol=1e-6)
    # our own encoder's FLAC (fixed predictors) still round-trips
    sig = (np.sin(np.arange(20000) / 9.0)[:, None] * [0.5, -0.3] * (1 << 23)).astype(np.int64)
    fp = os.path.join(tmp, "own.flac"); flac_lite.encode(fp, sig, 48000, 24)
    x, fs, b = R.flac_load(fp)
    assert fs == 48000 and np.array_equal(np.round(x * (1 << 23)).astype(np.int64), sig)
    # the audio tools: info/analysis of a FLAC, a folder of FLAC stems
    p = os.path.join(HERE, "fixtures", "lpc_pink_sine.flac")
    a = m.TOOLS["audio_analyze"][0](path=p)
    assert isinstance(a, dict) and a, a
    stems = os.path.join(tmp, "stems"); os.makedirs(stems)
    shutil.copy(p, os.path.join(stems, "pad.flac")); shutil.copy(fp, os.path.join(stems, "lead.flac"))
    rep = m.audio_mix_report(folder=stems)
    assert sorted(s["file"] for s in rep["stems"]) == ["lead.flac", "pad.flac"], rep
    # the catalog measures FLAC samples (length, pitch of the tonal one)
    ft = C.sample_features(os.path.join(HERE, "fixtures", "lpc_mono16.flac"))
    assert abs(ft["dur"] - 0.4) < 0.01 and ft.get("pitch") in (68, 69, 70), ft         # A4 = 69
    # a truncated FLAC: a clear error
    bad_p = os.path.join(tmp, "cut.flac"); open(bad_p, "wb").write(open(p, "rb").read()[:3000])
    try:
        R.flac_load(bad_p)
        raise AssertionError("a cut-off FLAC must not pass as a short song")
    except ValueError as e:
        assert "cut off" in str(e) or "no FLAC frames" in str(e), e
finally:
    shutil.rmtree(tmp, True)
print("flac read ok")
