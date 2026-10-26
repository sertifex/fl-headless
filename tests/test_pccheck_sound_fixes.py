"""PC sweep 2026-10-23 sound findings:
  newsong.fpc_gm_keys - 'FPC pads on GM keys [46] made no sound': the render (audio/fpc.flac) had the open hat at
    1.28 s (beat 3 at 140 BPM), peak ~-29 dBFS after silence, a long soft decay: its 150 ms RMS (-46.5 dB) was under
    the check's absolute -45 dB line. Not choked (42 and 46 don't overlap) and FPC's kit has the pad: the CHECK was
    wrong. _key_hits judges each hit against what played just before it.
  newsong.instrument_convert - 'LABS - Glass Piano' made no sound: a pack with only presets on disk. The tool says
    it (downloaded=false + a warning), the check prefers downloaded packs and skips with that reason on silence.
Run: python3 tests/test_pccheck_sound_fixes.py (no FL needed)."""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
roots = os.path.join(tmp, "roots"); os.makedirs(roots)
os.environ["FLH_ROOTS"] = roots; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
lib = os.path.join(tmp, "Splice", "INSTRUMENT"); os.environ["FLH_SPLICE_LIB"] = lib
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import flh_pccheck_mod as PM  # noqa: E402
ns = vars(m)
PM.H = ns

# ---------------------------------------------------------------- FPC: the evidence render's shape, synthesized
fs, bpm = 48000, 140.0
beat = 60.0 / bpm
x = np.zeros(int(fs * 2.3))
rng = np.random.default_rng(1)


def hit(t, peak_db, decay_s, noise=True):
    n = int(fs * (2.3 - t)); tt = np.arange(n) / fs
    src = rng.standard_normal(n) ** 3 if noise else np.sin(2 * np.pi * 55 * tt)   # spiky like a hat (crest ~17 dB)
    env = np.exp(-tt / decay_s)
    s = src * env
    s *= 10 ** (peak_db / 20) / np.max(np.abs(s))
    a = int(fs * t); x[a:a + n] += s
hit(0 * beat, -14.7, 0.08, noise=False)                           # kick (36)
hit(1 * beat, -13.5, 0.04)                                        # snare (38)
hit(2 * beat, -19.7, 0.03)                                        # closed hat (42), choked before the open hat
x[int(fs * (2 * beat + 0.16)):int(fs * 3 * beat)] = 0.0
hit(3 * beat, -29.3, 0.35)                                        # open hat (46): soft, long
hits = PM._key_hits(x, fs, [i * beat for i in range(4)])
levels = [h["level_db"] for h in hits]
assert levels[3] < -45, ("the old absolute rule (150 ms RMS < -45 dB) called this silent", levels)
assert all(h["sounds"] for h in hits), hits
# a key that made no sound at all
y = x.copy(); y[int(fs * 3 * beat):] = 0.0
assert not PM._key_hits(y, fs, [3 * beat])[0]["sounds"]
z = np.zeros(int(fs * 1.5)); z[:int(fs * 0.4)] = x[:int(fs * 0.4)]   # the kick still ringing into key 2's window
assert not PM._key_hits(z, fs, [0.2])[0]["sounds"], "a decaying tail is no new hit"
assert PM.QUIET_PAD_DB > (-13.5) - (-29.3), "FPC's soft open hat isn't flagged as a fault either"

# ---------------------------------------------------------------- INSTRUMENT: a pack with only presets
def touch(p, size=0):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"\0" * size)
touch(os.path.join(lib, "LABS - Glass Piano", "Presets", "v1.0.0", "Glass Piano.zpreset"), 900)
touch(os.path.join(lib, "LABS - Soft Piano", "Presets", "v1.2.0", "Soft Piano.zpreset"), 900)
touch(os.path.join(lib, "LABS - Soft Piano", "Samples", "v1.2.0", "a.ch1"), 3_000_000)
packs = {p["pack"]: p for p in m.flp_instrument_sounds()["packs"]}
assert packs["LABS - Glass Piano"]["downloaded"] is False and packs["LABS - Glass Piano"]["content_mb"] == 0.0
assert packs["LABS - Soft Piano"]["downloaded"] is True and packs["LABS - Soft Piano"]["content_mb"] == 3.0, packs
c = m._instrument_pack_content(os.path.join(lib, "LABS - Soft Piano"))
assert c["parts"] == {"Samples": 3.0} and c["files"] == 1, c
msg = m._instrument_not_downloaded("LABS - Glass Piano", {"mb": 0.0, "files": 0})
assert "not downloaded" in msg and "INSTRUMENT" in msg

# the check: a downloaded pack first; silence with a not-downloaded pack -> skip with the reason, never 'fail'
CALLS = []


class Ctx:
    ns = vars(m)
    sandbox = os.path.join(roots, "sbx")

    def call(self, tool, **a):
        CALLS.append((tool, a))
        if tool == "flp_instrument_sounds":
            return m.flp_instrument_sounds()
        if tool == "fl_status":
            return {}
        if tool == "flp_instrument_sound":
            return {"made_instrument_from": "INSTRUMENT (from project)",
                    **({"warning": "looks not downloaded"} if "Glass" in a["pack"] else {})}
        raise AssertionError(tool)


SILENT = {"heard": False, "whole": {"lufs": None}}
PM._solo_song = lambda ctx, genre, pick, notes, label, **k: ("song.flp", 3, {}, 1, 120)
PM._open = lambda ctx, path: {}
PM._listen = lambda ctx, a, b, save=None: LISTEN["r"]
LISTEN = {"r": SILENT}
r = PM.c_instrument_convert(Ctx())                                # Soft Piano (downloaded) is picked; silent: a fault
assert r["status"] == "fail" and "look downloaded" in r["detail"] and r["evidence"]["pack"] == "LABS - Soft Piano", r
os.remove(os.path.join(lib, "LABS - Soft Piano", "Samples", "v1.2.0", "a.ch1"))
try:                                                              # nothing downloaded: still tried, silence = skip
    PM.c_instrument_convert(Ctx())
    raise AssertionError("expected a skip")
except PM.Skip as e:
    assert "look not downloaded" in e.detail and "Glass Piano" in e.detail, e.detail
    assert "download" in (e.how or "") and e.evidence.get("downloaded") is False, (e.how, e.evidence)
LISTEN["r"] = {"heard": True, "whole": {"lufs": -18.0}}           # plays although it looked not downloaded: said
r = PM.c_instrument_convert(Ctx())
assert r["status"] == "pass" and r["evidence"].get("content_test_wrong") and "download test is wrong" in r["detail"], r
assert "LABS - Soft Piano" in (m._state().get("silent_packs") or {}), "a downloaded pack that played silent is noted"

# PC 2026-10-24: one downloaded pack silent, the next plays -> the conversion works: a warn naming the silent pack
m._state({"silent_packs": {}})
open(os.path.join(lib, "LABS - Soft Piano", "Samples", "v1.2.0", "a.ch1"), "wb").write(b"x" * 3_000_000)
seq = iter([SILENT, {"heard": True, "whole": {"lufs": -17.0}}])
PM._listen = lambda ctx, a, b, save=None: next(seq)
r = PM.c_instrument_convert(Ctx())
assert r["status"] == "warn" and "LABS - Soft Piano" in r["detail"] and r["evidence"]["pack"] == "LABS - Glass Piano", r
assert "LABS - Soft Piano" in (m._state().get("silent_packs") or {})
print("ok")


# ---- _key_hits finds each hit's real onset: a speaker capture that runs 70 ms late (PC 2026-10-23, FPC's open hat
# failed 'no rise' at its nominal time) still passes, and a missing hit still fails
def _late_kit(lat, drop_last=False):
    import numpy as np
    fs = 44100; beat = 60 / 140; x = np.zeros(int(fs * 3)); rng = np.random.default_rng(1)
    for i, (amp, dec) in enumerate([(0.6, 0.2), (0.3, 0.15), (0.2, 0.05), (0.05, 0.6)]):
        if drop_last and i == 3:
            continue
        a = int((i * beat + lat) * fs); n = int(fs * 0.8); t = np.arange(n) / fs
        x[a:a + n] += amp * rng.standard_normal(n) * np.exp(-t / dec)
    return x, fs, [i * beat for i in range(4)]


_m = __import__("flh_pccheck_mod")
x_, fs_, st_ = _late_kit(0.07)
assert all(h["sounds"] for h in _m._key_hits(x_, fs_, st_)), _m._key_hits(x_, fs_, st_)
x_, fs_, st_ = _late_kit(0.07, drop_last=True)
assert [h["sounds"] for h in _m._key_hits(x_, fs_, st_)] == [True, True, True, False]
print("key hits with latency ok")
