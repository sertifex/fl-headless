"""fl_preview's live mutes never leak into a project (PC 2026-10-23: FL switched projects during a channel preview,
the unmutes went to the other project, and a later whole-song preview 'saved FL first' with those mutes):
- no preview while an fl_open hasn't landed / FL is loading / a merged reload is going in; FL must show the song
  before muting and before unmuting (never into another project);
- unmutes are read back through fl_state (retried) and reported when they didn't stick;
- no save through FL while the mutes are on (preview running, or left over while FL shows that song); a leftover is
  put back first once FL shows the song again. Run: python3 tests/test_preview_guard.py"""
import os
import shutil
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="flh_pguard_")
ROOT = os.path.join(TMP, "root"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = ROOT
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
FIX = os.path.join(HERE, "corpus", "base.flp")
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402

_real_sleep = m.time.sleep
m.time.sleep = lambda s: _real_sleep(min(s, 0.01))
m.PREVIEW_LOADING_WAIT_S = 0.3; m.PREVIEW_TITLE_WAIT_S = 0.2

A = os.path.join(ROOT, "Song A.flp"); B = os.path.join(ROOT, "Song B.flp")
for p in (A, B):
    shutil.copy(FIX, p)
FL = {"open": A, "loading": None, "stuck": False, "saves": [], "switch_during_render": None,
      "mutes": {A: {i: False for i in range(5)}, B: {i: False for i in range(5)}}}


def title():
    return f"{os.path.splitext(os.path.basename(FL['open']))[0]} - FL Studio 2026"


def channel_set(i, muted=None, **k):
    if muted is not None and not (FL["stuck"] and muted is False):
        FL["mutes"][FL["open"]][i] = bool(muted)                 # live: always the project FL shows NOW
    return {"index": i}


def fake_render(project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None):
    if FL["switch_during_render"]:
        FL["open"] = FL["switch_during_render"]                   # an fl_open that only lands now
    f = os.path.join(out_dir, "x.wav"); fs = 44100
    y = (0.3 * np.sin(2 * np.pi * 220 * np.arange(fs) / fs) * 32767).astype("<i2")
    with wave.open(f, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs); w.writeframes(y.tobytes())
    return {"rendered": [f], "seconds": 0.5}


def bridge(cmd, timeout=15.0, **a):
    if cmd == "transport" and a.get("action") == "save":
        FL["saves"].append((FL["open"], dict(FL["mutes"][FL["open"]])))
    if cmd == "state":
        return {"playing": False, "changed": 1}
    return {}


m.BRIDGE.call = bridge
m.fl_running = lambda: True
m._fl_pids = lambda: [1]
m._fl_windows = lambda: [(1, FL["loading"] or title())]
m._fl_main_window = lambda: (1, title())
m._fl_loading_title = lambda: FL["loading"]
m._open_project_guess = lambda: (FL["open"], [title()])
m.fl_state = lambda: {"channels": [{"index": i, "muted": v} for i, v in FL["mutes"][FL["open"]].items()]}
m.fl_channel_set = channel_set
m._render_in_fl = fake_render
m._rc_project_fp = lambda p, live=False, **k: (None, None)


def muted(p):
    return sorted(i for i, v in FL["mutes"][p].items() if v)


# ---- the normal case: muted, rendered, put back and read back
r = m.fl_preview(channels=[1, 2])
assert muted(A) == [] and not m._PREVIEW_MUTES and "WARNING" not in r, (muted(A), r)

# ---- an fl_open that hasn't landed (FL still shows A): no preview, nothing muted
m._OPEN_PENDING.update(project=B, t=m.time.time())
try:
    m.fl_preview(channels=[1]); raise SystemExit("must refuse")
except RuntimeError as e:
    assert "still switching to Song B.flp" in str(e), e
assert muted(A) == [] and muted(B) == []
m._OPEN_PENDING.update(project=None, t=0.0)
# ... nor while FL is loading, nor while queued edits are going in
FL["loading"] = "Opening: Fruity Wrapper"
try:
    m.fl_preview(channels=[1]); raise SystemExit("must refuse")
except RuntimeError as e:
    assert "still loading" in str(e), e
FL["loading"] = None
m._INFLIGHT[m._ud_key(A)] = ["op"]
try:
    m.fl_preview(channels=[1]); raise SystemExit("must refuse")
except RuntimeError as e:
    assert "merged reload" in str(e), e
m._INFLIGHT.clear()

# ---- FL switches to B during the render: the unmutes never go to B; A's mutes are reported and block saves of A
FL["switch_during_render"] = B
r = m.fl_preview(channels=[0])
FL["switch_during_render"] = None
assert muted(B) == [] and muted(A) == [1, 2, 4], (muted(A), muted(B))   # 3 is an automation clip: never muted
assert "no longer shows this project" in r["WARNING"] and m._PREVIEW_MUTES[m._ud_key(A)]["channels"] == [1, 2, 4], r
assert m._preview_mutes_on(B) is None and m._preview_mutes_on() is None   # FL shows B: its saves are fine
# FL back on A (with the mutes still in it): a save first puts them back (read back), then saves - no mutes in the file
FL["open"] = A
assert m._save_in_fl_and_wait(A, wait_s=0.2) in (True, False)
assert FL["saves"] and FL["saves"][-1] == (A, {i: False for i in range(5)}), FL["saves"]
assert muted(A) == [] and not m._PREVIEW_MUTES

# ---- unmutes that don't stick: retried, reported, and FL is never saved with them
FL["saves"].clear(); FL["stuck"] = True
r = m.fl_preview(channels=[0, 1])
assert "still reports them muted" in r["WARNING"] and muted(A) == [2, 4], r   # (3: automation, never muted)
assert m._save_in_fl_and_wait(A, wait_s=0.2) is False and not FL["saves"]
s = m.fl_save()
assert s["saved"] is None and "fl_preview" in s["why"] and not FL["saves"], s
try:
    m._prove_open(A); raise SystemExit("no identity save with the mutes on")
except RuntimeError as e:
    assert "muted" in str(e), e
try:
    m.fl_open(B); raise SystemExit("'Yes' to 'save changes?' would write the mutes")
except RuntimeError as e:
    assert "save_current=false" in str(e) and "muted" in str(e), e
assert not FL["saves"]
FL["stuck"] = False                                               # the user unmutes / FL takes it: healed on next look
assert m._preview_mutes_on(A) is None and muted(A) == [] and not m._PREVIEW_MUTES
# ---- while a preview's mutes are on, nothing saves (the render step tries)
seen = {}


def render_and_try_save(project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None):
    seen["save"] = m._save_in_fl_and_wait(A, wait_s=0.2); seen["mutes"] = muted(A)
    return fake_render(project, out_dir)


m._render_in_fl = render_and_try_save
FL["saves"].clear()
m.fl_preview(channels=[3])
assert seen == {"save": False, "mutes": [0, 1, 2, 4]} and not FL["saves"] and muted(A) == [], (seen, FL["saves"])
print("preview ok")

# ---- fl_render_stems(method='in_fl', close_fl=false) mutes live too: the same guards (PC 2026-10-23)
m._render_in_fl = fake_render
m._render_method = lambda p, f, meth="auto": "in_fl"
m._sync_before = lambda *a, **k: None
stems_dir = os.path.join(ROOT, "Stems")
d_ = m._load(A); ev_ = m._events(d_)
playing = sorted({r[2] for v in m._patterns(d_, ev_).values() for r in v["notes"]} & set(range(5)))
assert len(playing) >= 2, playing
run_stems = lambda: m._render_stems(A, stems_dir, playing[:2], "wav", False, False, measure=False, method="in_fl")
r = run_stems()                                                   # the normal case: every mute put back
assert len(r["stems"]) == 2 and muted(A) == [] and not m._PREVIEW_MUTES and "WARNING" not in r, (r, muted(A))
m._OPEN_PENDING.update(project=B, t=m.time.time())                # an fl_open not landed: nothing muted
try:
    run_stems(); raise SystemExit("must refuse")
except RuntimeError as e:
    assert "still switching" in str(e), e
m._OPEN_PENDING.update(project=None, t=0.0)
m._INFLIGHT[m._ud_key(A)] = ["op"]                                # a merged reload going in: nothing muted
try:
    run_stems(); raise SystemExit("must refuse")
except RuntimeError as e:
    assert "merged reload" in str(e), e
m._INFLIGHT.clear()
assert muted(A) == [] and muted(B) == []
FL["switch_during_render"] = B                                    # FL switches mid-way: B never gets unmutes, A's
try:                                                              # leftover is reported and blocks saves of A
    run_stems(); raise SystemExit("the 2nd stem must stop: FL shows another project")
except RuntimeError as e:
    assert "stems stopped after" in str(e) and "no longer shows this project" in str(e), e
FL["switch_during_render"] = None
assert muted(B) == [] and muted(A) and m._PREVIEW_MUTES[m._ud_key(A)]["active"] is False, (muted(A), m._PREVIEW_MUTES)
FL["open"] = A                                                    # back on A: the leftover is put back before a save
FL["saves"].clear()
m._save_in_fl_and_wait(A, wait_s=0.2)
assert muted(A) == [] and not m._PREVIEW_MUTES and all(not any(v.values()) for _, v in FL["saves"]), FL["saves"]
FL["stuck"] = True                                                # unmutes that don't stick: WARNING, no save
try:
    run_stems(); raise SystemExit("must stop: the next stem would play with a channel muted")
except RuntimeError as e:
    assert "stems stopped after" in str(e) and "still reports them muted" in str(e), e
assert m._save_in_fl_and_wait(A, wait_s=0.2) is False
FL["stuck"] = False
assert m._preview_mutes_on(A) is None and muted(A) == []
shutil.rmtree(TMP, True)
print("ok")
