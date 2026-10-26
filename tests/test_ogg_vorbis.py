"""ogg_vorbis_lite: FL Studio's compressed factory WAVs (fmt tag 'Og', Ogg Vorbis inside) and .ogg files decode,
and every audio reader in fl-headless (catalog features, audio_edit, the WAV loader) uses it."""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import ogg_vorbis_lite as OV                                    # noqa: E402

FIX = os.path.join(HERE, "fixtures", "fl_ogg_tone.wav")              # 0.6 s, 220 Hz sine (lavfi 1/8 amplitude, -6 dB), stereo, faded out


def main():
    assert OV.is_ogg_wav(FIX)
    inf = OV.info(FIX)
    assert inf["rate"] == 44100 and inf["channels"] == 2 and abs(inf["frames"] - 26460) <= 1, inf
    x, fs = OV.decode(FIX)
    assert fs == 44100 and x.shape[1] == 2 and abs(len(x) - 26460) <= 1, x.shape
    pk = float(np.abs(x).max())
    assert 0.03 < pk < 0.08, pk                                   # lavfi sine (1/8) at -6 dB
    seg = x[2000:12000, 0].astype(float)
    P = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 16)); f = np.fft.rfftfreq(1 << 16, 1 / fs)
    assert abs(f[np.argmax(P)] - 220) < 2, f[np.argmax(P)]
    assert np.abs(x[-200:]).max() < 0.05                         # the fade-out survives
    y, _ = OV.decode(FIX, max_seconds=0.25)                      # analysis limit
    assert len(y) == int(0.25 * fs) and np.allclose(y, x[:len(y)], atol=1e-6)
    # plain .ogg: the same stream outside the WAV
    data = open(FIX, "rb").read()
    tmp = tempfile.mkdtemp(prefix="flh_ogg_")
    try:
        p = os.path.join(tmp, "tone.ogg")
        open(p, "wb").write(OV.ogg_stream(data))
        z, _ = OV.decode(p)
        assert np.allclose(z, x)
        # the readers that used to say "not a WAV"
        import fl_audio_edit as AE
        a, rate, bits, bad = AE.load_ex(FIX)
        assert rate == 44100 and a.shape == x.shape and bad == 0
        import flh_catalog as C
        C.H = getattr(C, "H", None) or {}
        feat = C.sample_features(FIX)
        assert abs(feat["dur"] - 0.6) < 0.01 and feat.get("pitch") in (56, 57, 58), feat   # A3 = 57
        # garbage in: an honest error, no crash
        bad_p = os.path.join(tmp, "bad.wav")
        open(bad_p, "wb").write(data[:200])
        try:
            OV.decode(bad_p)
            raise AssertionError("truncated stream should fail")
        except OV.VorbisError:
            pass
    finally:
        import shutil
        shutil.rmtree(tmp, True)
    print("ogg vorbis ok")


if __name__ == "__main__":
    main()
