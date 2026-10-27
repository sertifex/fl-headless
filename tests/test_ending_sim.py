"""The ending predicted from the notes agrees with the render: flh_finalize.simulate_ending (fl_ending_check's
prediction, flp_extend_ending's check) against fl_finalize's real finishing pass + rule_check on audio synthesized
from the same notes (struck notes decay, sustained layers hold). Reproduces the PC mismatch (layers as loud as the
final chord: the old note-length prediction said pass, the final failed ending_length) and a level jump (a part
entering loud), and shows agreement after the fix. Run: python3 tests/test_ending_sim.py (no FL needed)."""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_end_sim_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
tempfile.tempdir = os.path.join(tmp, "systemp"); os.makedirs(tempfile.tempdir)
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_finalize as F, flh_shape  # noqa: E402
ns = vars(m)
flh_store.register(ns); F.register(ns); flh_shape.register(ns)
FS = 22050
R = {"min_ending_s": 20, "min_tail_s": 5}
PROG = [(48, [60, 64, 67]), (52, [60, 64, 67]), (41, [60, 65, 69]), (43, [59, 62, 67]), (45, [60, 64, 69]),
        (40, [59, 64, 67]), (41, [60, 65, 69]), (43, [59, 62, 67]), (45, [60, 64, 69]), (41, [60, 65, 69]),
        (48, [60, 64, 67]), (43, [59, 62, 67]), (41, [60, 65, 69]), (43, [59, 62, 67]), (48, [60, 64, 67]),
        (36, [60, 64, 67])]


def bpm(bar):
    return 60 if bar < 9 else (66 if bar < 13 else 58)                 # the bench's 3/4 tempo map


def bar_s(b):
    return sum(3 * 60 / bpm(i) for i in range(1, int(b)))


def waltz(hold_main, hold_lay, lay_vel=44, entry=None):
    """The bench fixture as notes in seconds (piano decays, strings + low strings hold), layers under it at lay_vel;
    entry=(bar, vel): a loud sustained part enters there for 4 bars (the climax layer)."""
    out = []
    for b, (bass, tri) in enumerate(PROG):
        bar = b + 1; t0 = bar_s(bar); beat = 60 / bpm(bar)
        lv, rv = (92, 104) if 8 <= b < 12 else (56, 64)
        if b == 15:
            out += [(t0, t0 + hold_main, lv if k != 72 else rv, k, "decay") for k in [bass] + tri + [72]]
            out += [(t0, t0 + hold_lay, lay_vel + 2 if k == 36 else lay_vel, k, "sustain") for k in (36, 55, 60, 64)]
            continue
        out.append((t0, t0 + beat, lv, bass, "decay"))
        out += [(t0 + j * beat, t0 + 1.9 * beat * 0.5 + j * beat, lv - 6, k, "decay") for j in (1, 2) for k in tri]
        out.append((t0, t0 + 3 * beat, lay_vel + 2, bass, "sustain"))
        out += [(t0, t0 + 3 * beat, lay_vel, k - 12 if k > 64 else k, "sustain") for k in tri]
        if entry and entry[0] <= bar < entry[0] + 4:
            out += [(t0, t0 + 3 * beat, entry[1], k + 12, "sustain") for k in tri]
    return out


def synth(notes):
    """A 'render' of the notes with the simulation's instrument model, as real samples (sines, random phases)."""
    end = max(n[1] for n in notes) + 5
    y = np.zeros(int(end * FS))
    for on, off, vel, key, kind in notes:
        a0 = 10 ** (F.SIM_VEL_DB * np.log10(vel / 100) / 20)
        i0 = int(on * FS); i1 = min(len(y), int((off + 4) * FS)); tt = np.arange(i1 - i0) / FS; ta = tt + on
        if kind == "sustain":
            env = 10 ** (np.where(ta <= off, 0, -F.SIM_RELEASE_DB_S * (ta - off)) / 20) * np.sqrt(
                np.clip(tt / F.SIM_ATTACK_S, 0, 1))
        else:
            db = -np.minimum(4, 8 * tt) - F._decay_db_s(key) * tt - F.SIM_DAMP_DB_S * np.maximum(0, ta - off)
            env = 10 ** (db / 20) * np.minimum(1, tt / 0.003)
        ph = np.random.default_rng(key * 1000 + int(on * 10)).random() * 6.28
        y[i0:i1] += a0 * np.sqrt(2) * env * np.sin(2 * np.pi * 440 * 2 ** ((key - 69) / 12) * tt + ph)
    y *= 0.3 / np.abs(y).max()
    return np.stack([y, y * 0.99], 1).astype(np.float32)


def final(notes, hint=True, **kw):
    info = F.finish(synth(notes), FS, os.path.join(tmp, "x.flac"), -16, -1, fade_s="auto", rules=R,
                    ending_attack_s=F.final_attack_s(notes) if hint else None, **kw)
    return info, {c["rule"]: c for c in info["rules"]["checks"]}


try:
    need, _mg = F.ending_hold_for_rules(R)
    # ---- the PC case: layers as loud as the final chord. The old prediction (the chord's note length >= need) says
    # pass; the audio hides the final chord's attack (the peak doesn't double), so without the notes' attack the final
    # gets no diminuendo and ending_length fails - the mismatch the bench saw
    pc = waltz(need, need - 2.9, lay_vel=90)
    assert need >= need                                                    # old rule: hold >= need -> "pass"
    info0, by0 = final(pc, hint=False)
    print("PC case, old (no attack from the notes):", by0["ending_length"]["value"], (info0.get("fade") or {}).get("kind"))
    assert not by0["ending_length"]["pass"], by0["ending_length"]
    info1, by1 = final(pc)                                                  # fixed: fl_finalize gets the notes' attack
    sim = F.simulate_ending(pc, R, -16)
    print("PC case, fixed: render", by1["ending_length"]["value"], by1["tail_ring"]["value"], "simulated",
          sim["ending_s"], sim["tail_s"], info1["fade"])
    assert info1["fade"]["kind"] == "hold_diminuendo" and info1["rules"]["pass"], [c for c in by1.values() if not c["pass"]]
    assert abs(sim["ending_s"] - by1["ending_length"]["value"]) < 2.5, (sim, by1["ending_length"])
    # ---- the soft case (layers under the piano) and a short hold: simulation and render agree on pass / fail
    for hm, hl, want in ((need, need - 2.9, True), (2.0, 2.0, False), (14.0, 12.0, False)):
        nts = waltz(hm, hl)
        info, by = final(nts)
        sim = F.simulate_ending(nts, R, -16)
        sp = sim["ending_s"] >= R["min_ending_s"] and sim["tail_s"] >= R["min_tail_s"]
        rp = by["ending_length"]["pass"] and by["tail_ring"]["pass"]
        print(f"hold {hm:g}/{hl:g}: render {by['ending_length']['value']} / {by['tail_ring']['value']}, simulated "
              f"{sim['ending_s']} / {sim['tail_s']}")
        assert sp == rp == want, (hm, sim, by["ending_length"], by["tail_ring"])
        assert abs(sim["ending_s"] - by["ending_length"]["value"]) < 3.0, (sim, by["ending_length"])
        assert by["trail_silence"]["pass"], by["trail_silence"]                # the buried tail is cut
    # ---- a level jump: a loud layer enters at bar 9 (the climax) -> predicted, and the final rides it in
    jn = waltz(need, need - 2.9, lay_vel=30, entry=(9, 110))
    x = synth(jn)
    raw = F.rule_check(x, FS, R)
    rj = next(c for c in raw["checks"] if c["rule"] == "level_jumps")
    sim = F.simulate_ending(jn, R, -16)
    print("jump: raw", rj["value"], "simulated", sim["jumps"])
    assert not rj["pass"] and sim["jumps"], (rj, sim["jumps"])
    assert abs(sim["jumps"][0]["at_s"] - rj["value"][0]["at_s"]) < 1.0, (sim["jumps"], rj["value"])
    info, by = final(jn)
    print("jump ridden:", info.get("jumps_smoothed"), by["level_jumps"]["value"])
    assert by["level_jumps"]["pass"] and info.get("jumps_smoothed"), (by["level_jumps"], info.get("jumps_smoothed"))
    assert info["rules"]["pass"], [c for c in by.values() if not c["pass"]]
    lv = F._level_curve(synth(jn), FS)                                     # only the entrance moved
    assert abs(info["lufs"] + 16) < 0.06, info["lufs"]

    # ---- end to end on a project: flp_extend_ending (rules from the profile, simulated) -> fl_ending_check
    # (render=False) -> the 'render' of its notes through fl_finalize's pass: they agree, the first final passes
    src = os.path.join(HERE, "corpus", "base.flp")
    p = os.path.join(tmp, "waltz.flp")
    m.flp_write_notes(src, p, [{"channel": 0, "notes": [[b + j / 3, 1 / 3, k, 60] for b in range(1, 16) for j in range(3)
                                                        for k in (48 if j == 0 else 64,)] + [[16, 2 / 3, k, 56] for k in (36, 60, 64, 67)]},
                               {"channel": 4, "notes": [[b, 1, k, 70] for b in range(1, 17) for k in (55, 60)]}],
                      tempo="keep")
    m.flp_project_info(p, output=p, time_sig_num=3, time_sig_beat=4)
    m.flp_tempo_write(p, p, points=[[1, 60], [9, 66], [13, 58]])
    flh_store.journal_update(p, set={"channels": {"0": {"role": "piano"}, "4": {"role": "strings"}}})
    flh_store.profile_set("Bench-like", {"rules": dict(R)})
    m.fl_running = lambda: False; ns["fl_running"] = m.fl_running
    pre = m.fl_ending_check(p, rules=dict(R), render=False)
    assert pre["predicted"] == "fail" and "flp_extend_ending" in pre.get("fix", ""), pre
    r = ns["TOOLS"]["flp_extend_ending"][0](project=p, output=p)
    assert r["mode"] == "rules" and r.get("simulated"), r
    assert flh_store.journal_get(p)["ending_hold"]["main_s"] == r["hold"]["main_s"], flh_store.journal_get(p)
    chk = m.fl_ending_check(p, rules=dict(R), render=False)
    print("project: extend", r["hold"], r["simulated"], "-> fl_ending_check", chk["predicted"], chk["simulated"])
    assert chk["predicted"] == "pass" and chk["renders"] == 0, chk
    nts = F.ending_notes_s(p)
    assert {n[4] for n in nts} == {"decay", "sustain"}, {n[4] for n in nts}
    info, by = final(nts)
    print("project 'render':", by["ending_length"]["value"], by["tail_ring"]["value"])
    assert by["ending_length"]["pass"] and by["tail_ring"]["pass"], (by["ending_length"], by["tail_ring"])  # agree
    assert abs(chk["simulated"]["ending_s"] - by["ending_length"]["value"]) < 3.0, (chk["simulated"], by["ending_length"])
finally:
    shutil.rmtree(tmp, True)
print("ending sim ok")
