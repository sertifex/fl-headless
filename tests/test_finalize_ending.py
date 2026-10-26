"""fl_finalize's ending: a final chord that holds FLAT and then stops (sustained layers under a piano) is faded from
its attack as a long diminuendo (fade auto or the profile's default number), so the collection's min_ending_s passes
on the first render; explicit fade_s numbers stay exact; the FINAL is checked against the rules and every failed rule
comes with a concrete fix (e.g. flp_extend_ending(..., hold_s=...)). Run: python3 tests/test_finalize_ending.py"""
import os, re, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_fin_end_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
tempfile.tempdir = os.path.join(tmp, "systemp"); os.makedirs(tempfile.tempdir)
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_finalize as F, flh_shape  # noqa: E402
import fl_audio_edit as AE  # noqa: E402
ns = vars(m)
flh_store.register(ns); F.register(ns); flh_shape.register(ns)
FS = 44100
RULES = {"min_ending_s": 30, "min_tail_s": 6}


def piece(hold_s, fs=FS, music_s=24.0, seed=1):
    """music_s of piano-ish notes, then the final chord at music_s: a piano tone decaying under strings that hold a
    steady level for hold_s and stop (1 s release), then 2 s of silence."""
    rng = np.random.default_rng(seed)
    n = int((music_s + hold_s + 3.0) * fs); y = np.zeros(n)
    L = int(3 * fs); tt = np.arange(L) / fs
    t = 0.2
    while t < music_s - 1.0:
        f0 = 220 * 2 ** (rng.integers(0, 12) / 12)
        tone = 0.3 * np.sin(2 * np.pi * f0 * tt) * np.exp(-tt * 1.2) * np.minimum(1, tt / 0.004)
        s = int(t * fs); y[s:s + L] += tone[:len(y[s:s + L])]
        t += 0.5
    a = int(music_s * fs); H_ = int(hold_s * fs); T = np.arange(n - a) / fs
    piano = 0.35 * np.sin(2 * np.pi * 130.8 * T) * np.exp(-T / 5.0) * np.minimum(1, T / 0.004)
    env = np.where(T < hold_s, 1.0, np.exp(-(T - hold_s) / 0.25))           # strings: flat, then let go
    strings = 0.12 * env * (np.sin(2 * np.pi * 196 * T) + np.sin(2 * np.pi * 261.6 * T)) * np.minimum(1, T / 0.3)
    y[a:] += piano + strings
    y[a + H_ + int(1.0 * fs):] = 0
    y /= np.abs(y).max() / 0.7
    return np.stack([y, y * 0.98], 1).astype(np.float32)


try:
    out = os.path.join(tmp, "x.flac")
    # ---- a long flat hold: the diminuendo over it passes min_ending_s on the first render
    x = piece(44.0)
    sh = F.ending_shape(x, FS)
    assert sh["flat"] and abs(sh["attack_s"] - 24.0) < 0.1 and 42 < sh["hold_s"] < 46, {k: v for k, v in sh.items()
                                                                                       if k not in ("t", "L")}
    info = F.finish(x, FS, out, -18.0, -1.0, fade_s=2.5, bits=24, hold_fade=True, rules=RULES)
    by = {c["rule"]: c for c in info["rules"]["checks"]}
    print("flat 44 s hold, profile fade 2.5 ->", info["fade"], "ending", by["ending_length"]["value"],
          "tail", by["tail_ring"]["value"])
    assert info["fade"]["kind"] == "hold_diminuendo" and info["fade_s"] > 40, info["fade"]
    assert by["ending_length"]["pass"] and by["tail_ring"]["pass"] and by["tail_not_cut"]["pass"], by
    assert info["rules"]["pass"], info["rules"]
    pred = info["fade"]["predicted_ending_s"]
    assert pred is not None and abs(pred - by["ending_length"]["value"]) < 4, (pred, by["ending_length"])
    ratio = by["ending_length"]["value"] / info["fade_s"]
    assert ratio >= F.HOLD_FADE_EFF - 0.05, ratio                    # the figure flp_extend_ending sizes holds by
    # the same audio with an EXPLICIT 2.5 s: exact and linear (the flat hold stops: the rule fails, with a fix)
    info2 = F.finish(x, FS, out, -18.0, -1.0, fade_s=2.5, bits=24, hold_fade=False, rules=RULES)
    by2 = {c["rule"]: c for c in info2["rules"]["checks"]}
    assert info2["fade_s"] == 2.5 and "fade" not in info2 and not by2["ending_length"]["pass"], (info2.get("fade"), by2)
    fx = F.rule_fixes(info2["rules"], "/songs/a.flp", info2.get("fade"), RULES, explicit_fade=True)
    assert any(f["rule"] == "ending_length" and "diminuendo" in f["fix"] for f in fx), fx
    # auto (no number) behaves as the profile default: the hold fades too
    info3 = F.finish(x, FS, out, -18.0, -1.0, fade_s="auto", bits=24, rules=RULES)
    assert info3["fade"]["kind"] == "hold_diminuendo" and info3["rules"]["pass"], info3["fade"]
    # ---- a short flat hold (17.7 s, as on the PC): fails, and the fix names the hold that passes
    xs = piece(17.7)
    info4 = F.finish(xs, FS, out, -18.0, -1.0, fade_s=2.5, bits=24, hold_fade=True, rules=RULES)
    by4 = {c["rule"]: c for c in info4["rules"]["checks"]}
    assert info4["fade"]["kind"] == "hold_diminuendo" and not by4["ending_length"]["pass"], (info4["fade"], by4)
    fx = F.rule_fixes(info4["rules"], "/songs/a.flp", info4["fade"], RULES)
    f_end = next(f for f in fx if f["rule"] == "ending_length")["fix"]
    assert "flp_extend_ending" in f_end and "hold_s" in f_end, f_end
    import re
    want = int(re.search(r'hold_s"?[=:] ?(\d+)', f_end).group(1))
    print("short hold fix:", f_end)
    assert want >= F.ending_hold_for_rules(RULES)[0] - 6, want
    x5 = piece(want - 1.0)                                             # following the fix passes
    info5 = F.finish(x5, FS, out, -18.0, -1.0, fade_s=2.5, bits=24, hold_fade=True, rules=RULES)
    assert info5["rules"]["pass"], [c for c in info5["rules"]["checks"] if not c["pass"]]
    # flp_extend_ending's rule-sized hold (what it writes) passes as well
    hs, _mg = F.ending_hold_for_rules(RULES)
    info6 = F.finish(piece(hs - 1.5), FS, out, -18.0, -1.0, fade_s=2.5, bits=24, hold_fade=True, rules=RULES)
    assert info6["rules"]["pass"], ([c for c in info6["rules"]["checks"] if not c["pass"]], hs)
    assert abs(flh_shape.hold_for_rules(RULES)[0] - hs) < 1e-9          # one formula, both modules
    # ---- a hard ending is untouched (no flat hold): the old decay-only auto fade
    hard = piece(44.0)[:int(24.6 * FS)].copy(); hard[-int(0.05 * FS):] *= np.linspace(1, 0, int(0.05 * FS))[:, None]
    info7 = F.finish(hard, FS, out, -18.0, -1.0, fade_s="auto", bits=24)
    assert info7["fade_s"] <= 4.0 and (info7.get("fade") or {}).get("kind") != "hold_diminuendo", info7.get("fade")

    # ---- fl_finalize: the profile's fade number is a default (the hold fades), its rules check the FINAL
    proj = os.path.join(tmp, "Slow Piece.flp"); open(proj, "wb").write(b"FLhd fake")
    flh_store.profile_set("Game", {"final_root": os.path.join(tmp, "finals"), "finish": {"lufs": -18, "fade_s": 2.5},
                                   "rules": RULES})
    ns["fl_running"] = lambda: False
    ns["files_recycle"] = lambda paths: [os.remove(p) for p in paths if os.path.isfile(p)] and None

    def fake_render(project, out_dir, formats, timeout_s, data=None, out_stem=None):
        o = os.path.join(out_dir, (out_stem or "x") + ".wav"); AE.save(o, piece(44.0), FS, 24)
        return {"rendered": [o], "seconds": 0.1}
    ns["_render"] = fake_render
    r = F.fl_finalize(project=proj, background=False, method="cli")
    f0 = r["finals"][0]
    assert r["ok"] and f0["fade"]["kind"] == "hold_diminuendo" and f0["rules"]["pass"], f0
    assert "fixes" not in f0["rules"] and flh_store.journal_get(proj)["rules_check"]["pass"], f0["rules"]
    r = F.fl_finalize(project=proj, background=False, method="cli", fade_s=2.5)      # explicit: exact
    f1 = r["finals"][0]
    assert f1["fade_s"] == 2.5 and not f1["rules"]["pass"] and f1["rules"]["fixes"], f1
    assert "ending_length" in f1["rules"]["failed"], f1["rules"]
    # audio_rule_check gives fixes too
    wav = os.path.join(tmp, "short.wav"); AE.save(wav, piece(10.0), FS, 24)
    ar = F.audio_rule_check(wav, RULES)
    assert not ar["pass"] and any("flp_extend_ending" in f["fix"] for f in ar["fixes"]), ar
finally:
    shutil.rmtree(tmp, True)
print("finalize ending ok")
