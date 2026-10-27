"""audio_collection_report: a collection's finals side by side, its medians, and the songs that stand out.
Run: python3 tests/test_collection_report.py"""
import os, sys, shutil, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_coll_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import flac_lite, flh_finalize  # noqa: E402
flh_finalize.register(vars(m))
SR = 44100


def song(seconds, level, ending_s, bright=False, seed=0):
    rng = np.random.default_rng(seed)
    n = int(seconds * SR); t = np.arange(n) / SR
    x = np.zeros(n)
    for k, f in enumerate((220, 277, 330, 440)):
        x += np.sin(2 * np.pi * f * t + k) * (0.5 if not bright else 0.3)
    if bright:
        x += 0.6 * rng.standard_normal(n) * 0.3
    env = np.ones(n); a = int(0.2 * SR); env[:a] = np.linspace(0, 1, a)
    e0 = n - int(ending_s * SR); env[e0:] = np.linspace(1, 0, n - e0)            # the ending fades out
    y = x * env * level
    y = np.stack([y, y * 0.97], 1)
    y = np.concatenate([y, np.zeros((int(0.8 * SR), 2))])                         # true silence at the end
    return np.round(np.clip(y, -1, 1) * 8388607).astype(np.int64)


try:
    for name, args in (("Amber Light", (100, 0.08, 70)), ("Blue Road", (100, 0.085, 72, False, 1)),
                       ("Night Song", (100, 0.5, 4, True, 2))):
        d = os.path.join(tmp, "Collection", name); os.makedirs(d)
        flac_lite.encode(os.path.join(d, f"{name} - FINAL.flac"), song(*args), SR, 24)
    open(os.path.join(tmp, "Collection", "Amber Light", "notes.txt"), "w").write("x")
    r = m.TOOLS["audio_collection_report"][0](folder=os.path.join(tmp, "Collection"))
    assert [s["song"] for s in r["songs"]] == ["Amber Light - FINAL", "Blue Road - FINAL", "Night Song - FINAL"], r
    out = {s["song"]: s["why"] for s in r["stands_out"]}
    assert "Night Song - FINAL" in out and "Amber Light - FINAL" not in out, r["stands_out"]
    why = " ".join(out["Night Song - FINAL"])
    assert "LU" in why and ("short ending" in why or "ending_length" in why), why
    assert r["medians"]["lufs"] is not None and all(s["seconds"] > 99 for s in r["songs"])
    try:
        m.TOOLS["audio_collection_report"][0](folder=os.path.join(tmp, "nothing")); raise SystemExit("must refuse")
    except ValueError:
        pass
finally:
    shutil.rmtree(tmp, True)
print("collection report ok")
