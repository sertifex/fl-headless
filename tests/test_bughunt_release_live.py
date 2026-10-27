"""Bug hunt before the release (live-FL layer): solo previews / in-FL stems vs reloads, saves landing in another
project, the proven reload route for a stale edit, guarded saves in extensions, automation channels kept, locked-file
retries, reload tags left in names, same-title twins, silent instruments. Each block is one confirmed bug's regression
test. Run: python3 tests/test_bughunt_release_live.py"""
import os, shutil, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402

ns = vars(m)
import flh_catalog  # noqa: E402
import flh_transcribe  # noqa: E402
for mod in (flh_catalog, flh_transcribe):
    if getattr(mod, "H", None) is not ns:
        mod.register(ns)

BASE = os.path.join(HERE, "corpus", "base.flp")                  # channel 3 is an automation clip (type 5)
OTHER = os.path.join(HERE, "corpus", "audio_clips.flp")
_real_sleep = m.time.sleep
NAMES = ("fl_running", "_open_project_guess", "_preview_fl_on", "fl_state", "fl_channel_set", "BRIDGE", "_reload_in_fl",
         "_render_in_fl", "_main_title", "_fl_main_window", "_fl_windows", "_fl_proc_sig", "_scaled", "_sync_before",
         "_render_method", "_live_mutes_ready", "_fl_load_file", "_dialog_ids", "_sweep_leftover_popups",
         "_fl_loading_title", "_bridge_state_light", "_verify_in_background", "_warm_scan", "_open_running_or_fail",
         "_reload_marked", "_fl_main_window", "OS_RETRY_TOTAL_S")
ORIG = {k: getattr(m, k) for k in NAMES}


def restore():
    for k, v in ORIG.items():
        setattr(m, k, v)
    m._STALE_FL.clear(); m._PREVIEW_MUTES.clear(); m._LAST_PROVEN.clear(); m._FL_HOLDS.clear()
    m._OPEN_PROOF.update(path=None, title=None, t=0.0)


def song(name, src=BASE):
    p = os.path.join(tmp, name); shutil.copy(src, p)
    return p


def write_wav(path, amp, fs=44100):
    t = np.arange(fs) / fs
    y = (amp * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs); w.writeframes(y.tobytes())


class NoBridge:
    def call(self, cmd, **k):
        return {"changed": 0} if cmd == "state" else {}


def live_fl(project, n=6):
    """A fake FL showing `project` with n channels and live mute state LIVE."""
    LIVE = {i: False for i in range(n)}
    m.fl_running = lambda: True
    m._open_project_guess = lambda: (project, [])
    m._preview_fl_on = lambda p, wait_s=None: True
    m.fl_state = lambda: {"channels": [{"index": i, "muted": LIVE[i]} for i in LIVE]}

    def chset(i, muted=None, **k):
        LIVE[int(i)] = bool(muted)
    m.fl_channel_set = chset
    m.BRIDGE = NoBridge()
    return LIVE


# ---- L1. a solo preview of a project FL holds an OLDER copy of: the mutes went on, then the export's load reloaded
#          FL (dropping them) -> the 'solo' was the whole mix. Now FL is loaded first, then muted; a load while live
#          mutes are on is refused; the mutes are re-checked right before FL's export.
proj = song("Solo.flp"); LIVE = live_fl(proj)
reloads = []


def fake_reload(project, discard=False, force_blank=False):      # FL re-reads the file: live mutes are gone
    reloads.append(1)
    for i in LIVE:
        LIVE[i] = False
    m._stale_clear(project)
    return {"proof": "marker"}


m._reload_in_fl = fake_reload
playing = []


def fake_in_fl(project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None, split=None):
    m._fl_load_file(project, "render")                           # what _render_in_fl_raw does first
    on = [i for i in LIVE if not LIVE[i]]; playing.append(on)
    f = os.path.join(out_dir, (out_stem or "song") + ".wav"); write_wav(f, 0.05 * len(on))
    return {"rendered": [f], "seconds": 1.0}


m._render_in_fl = fake_in_fl
m._state({"in_fl_mutes_ok": True})
m._stale_mark(proj, "reload not taken: test")                    # an earlier reload failed: FL holds an older copy
r = m.fl_preview(channels=[1], exact=True)
assert len(reloads) == 1 and playing == [[1, 3]], (reloads, playing)   # loaded first; channel 3 = automation, kept
assert r["channels"] == [1] and not r.get("WARNING"), r
assert not any(LIVE.values()), LIVE                               # the mutes are back
# a load while a solo render's mutes are on is refused (it would drop them)
m._PREVIEW_MUTES[m._ud_key(proj)] = {"project": proj, "channels": [0, 2], "active": True}
m._stale_mark(proj, "again")
try:
    m._fl_load_file(proj, "render"); raise SystemExit("a reload ran while live mutes were on")
except m.InFlUnavailable as e:
    assert "muted live" in str(e) and "whole mix" in str(e), e
assert len(reloads) == 1
# right before the export: the mutes must still be on in FL
LIVE.update({0: True, 2: True})
assert m._live_mutes_intact(proj) is None
LIVE[2] = False                                                   # FL dropped one (reloaded meanwhile)
why = m._live_mutes_intact(proj)
assert why and "[2]" in why and "nothing was rendered" in why, why
restore()
print("L1 solo preview: loaded first, then muted ok")

# ---- L5. solo previews / in-FL stems muted automation-clip channels too: automation stopped (sidechain ducks, sweeps)
d = m._load(BASE)
assert m._automation_channels(d) == {3} and m._automation_channels(None) == set()
proj = song("Auto.flp"); LIVE = live_fl(proj)
muted_seen = []


def fake_in_fl2(project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None, split=None):
    muted_seen.append(sorted(i for i in LIVE if LIVE[i]))
    f = os.path.join(out_dir, (out_stem or "song") + ".wav"); write_wav(f, 0.2)
    return {"rendered": [f], "seconds": 1.0}


m._render_in_fl = fake_in_fl2
m.fl_preview(channels=[0], exact=True)
assert muted_seen == [[1, 2, 4, 5]], muted_seen                   # 3 (automation) plays on
restore()
print("L5 automation channels never muted ok")

# ---- L2. fl_render(B) while FL shows A (which FL holds an OLDER copy of): FL's changed flag (A's) made the render
#          save FL - writing A's old copy over A's newer file. A save meant for B is refused while FL shows A.
A = song("Song A.flp", OTHER); B = song("Song B.flp")
new_a = open(A, "rb").read()
m.fl_running = lambda: True
m._open_project_guess = lambda: (A, [])
m._stale_mark(A, "reload unproven: test")
saves = []


class BrA:
    def call(self, cmd, **k):
        if cmd == "state":
            return {"changed": 1}                                 # A has unsaved changes in FL
        if cmd == "transport" and k.get("action") == "save":
            saves.append(1); open(A, "wb").write(open(BASE, "rb").read())
        return {}


m.BRIDGE = BrA()


def no_export(*a, **k):
    raise m.InFlUnavailable("test: export not simulated")


m._render_in_fl = no_export
try:
    m.fl_render(B, method="in_fl")
except Exception:                                                 # noqa: BLE001 - the export isn't simulated
    pass
assert not saves and open(A, "rb").read() == new_a, "FL's old copy of A was saved over A"
m._stale_clear(A)
assert m._save_in_fl_and_wait(B, 0.2) is False and not saves
assert "FL shows Song A.flp" in m._save_refused_why(), m._save_refused_why()
fp, note = m._rc_project_fp(B, live=True)                         # B renders from its file: FL not saved
assert fp and not saves, (fp, note)
m._stale_mark(A, "again")                                         # FL holds an older copy of what it shows: no save
m._open_project_guess = lambda: (None, [])                        # ... even when the target can't be pinned
m._fl_main_window = lambda: (1, "Song A - FL Studio 2026")
assert m._save_in_fl_and_wait(B, 0.2) is False and "older copy" in m._save_refused_why() and not saves
restore()
print("L2 save meant for B never lands in A ok")

# ---- L3. the edit of a project FL holds an OLDER copy of reloaded FL by the FAST route (a direct reopen FL may
#          serve from its own copy, then counted as loaded). It goes the proven route now, like fl_open.
A = song("Stale Edit.flp")
m.fl_running = lambda: True
m._open_project_guess = lambda: (A, [])
m._main_title = lambda: "Stale Edit - FL Studio 2026"
m._dialog_ids = lambda: set()
m._sweep_leftover_popups = lambda *a, **k: []
m._fl_loading_title = lambda: None
m._bridge_state_light = lambda timeout=15.0: {}
m._verify_in_background = lambda *a, **k: "verify-x"
m._warm_scan = lambda: None
m.BRIDGE = NoBridge()
ctx = []


def spy_reload(project, discard=False, force_blank=False):
    ctx.append(bool(getattr(m._RELOAD_CTX, "verify", False)))
    m._stale_clear(project); m._note_fl_holds(project)
    return {"proof": "marker"}


m._reload_in_fl = spy_reload
m._stale_mark(A, "reload unproven: earlier edit")


def writer():
    m._write_bytes(A, open(A, "rb").read()[:-4] + b"\x00\x00\x00\x00")   # a stand-in edit
    return {"output": A}


r = m._with_open_project(A, A, writer)
assert ctx == [True], ctx
assert r.get("fl_held_older_copy") and r.get("reloaded_in_fl"), r
restore()
print("L3 stale edit reloads by the proven route ok")

# ---- L4. fl_ab(label=...) and the capture's save sent 'transport save' straight to the bridge: FL holding an OLDER
#          copy (fl_save refuses) still got saved over the newer file. Both go through the guarded save now.
A = song("Labelled.flp"); newer = open(A, "rb").read()
m.fl_running = lambda: True
m._open_project_guess = lambda: (A, [])
m._state({"last_opened": A})
m._stale_mark(A, "reload unproven: test")
saves = []


class BrOld:
    def call(self, cmd, **k):
        if cmd == "transport" and k.get("action") == "save":
            saves.append(1); open(A, "wb").write(open(OTHER, "rb").read())
        return {"changed": 1} if cmd == "state" else {}


m.BRIDGE = BrOld()
r = flh_catalog.fl_ab(A, label="v2")
lab = r["label"]
assert not saves and open(A, "rb").read() == newer, "fl_ab's label saved FL's old copy over the file"
assert "not saved in FL first" in str(lab.get("saved_in_fl")) and "older copy" in str(lab["saved_in_fl"]), lab
assert open(lab["stored"], "rb").read() == newer                  # the newer file is what was stored
assert flh_transcribe._save_and_wait(A, tries=2, timeout=0.2) is None and not saves
assert "older copy" in str(flh_transcribe._SAVE_WHY["why"]), flh_transcribe._SAVE_WHY
restore()
print("L4 extension saves go through the guards ok")

# ---- L6 / K2. a project replace hit a lock (OneDrive / antivirus / FL right after a load: 'PermissionError [WinError
#          5]' on '<song>.flp.flh-tmp' -> '<song>.flp'): _save didn't retry at all, _write_bytes only ~1 s.
real_replace = m.os.replace
fails = {"n": 0}


def flaky(a, b):
    if fails["n"] > 0:
        fails["n"] -= 1
        raise PermissionError(5, "Access is denied")
    return real_replace(a, b)


out = os.path.join(tmp, "Locked.flp")
try:
    m.os.replace = flaky
    m.OS_RETRY_TOTAL_S = 5.0
    fails["n"] = 4; m._save(open(BASE, "rb").read(), out)
    assert os.path.isfile(out) and fails["n"] == 0
    fails["n"] = 4; m._write_bytes(out, open(OTHER, "rb").read())
    assert open(out, "rb").read() == open(OTHER, "rb").read()
    d0 = open(out, "rb").read()                                   # the reload marker's write right after a load
    fails["n"] = 3
    mk, why = m._reload_marker_stamp(out, d0)
    assert mk and not why, why
    m.OS_RETRY_TOTAL_S = 0.15                                     # a lock that never goes: fails, nothing left
    fails["n"] = 10 ** 6
    for fn in (lambda: m._save(open(BASE, "rb").read(), out), lambda: m._write_bytes(out, b"x" * 64)):
        try:
            fn(); raise SystemExit("a locked file was 'written'")
        except PermissionError:
            pass
    left = [f for f in os.listdir(tmp) if f.startswith("Locked.flp.")]
    assert not left, left
    mk, why = m._reload_marker_stamp(out, d0)                     # then the marker route falls back, saying why
    assert mk is None and "PermissionError" in why, why
finally:
    m.os.replace = real_replace
restore()
print("L6/K2 locked files retried for seconds ok")

# ---- L7. a reload tag left in a mixer name (its put-back timed out, then FL saved): every later fast reload looked
#          like 'no sign of re-reading' and the name stayed polluted. The tag is unique per reload, compared exactly,
#          a leftover stripped, and a failed put-back reported.
FILE = {1: "Piano"}; LIVEN = dict(FILE); FAIL = {"untag": False}


class BrTag:
    def call(self, cmd, timeout=None, **k):
        if cmd == "mixer_set":
            t = k["track"]
            if "name" in k:
                if FAIL["untag"] and not m._TAG_RE.search(str(k["name"])):
                    raise TimeoutError("FL busy")
                LIVEN[t] = k["name"]
            return {"name": LIVEN.get(t)}
        return {}


m.BRIDGE = BrTag()
t, orig = m._tag_track(); tag1 = LIVEN[1]
assert (t, orig) == (1, "Piano") and tag1.startswith("Piano" + m.RELOAD_TAG) and tag1 != "Piano" + m.RELOAD_TAG
FAIL["untag"] = True
assert m._untag(t, orig) is False                                  # reported, not swallowed
FAIL["untag"] = False
FILE.update(LIVEN)                                                 # FL saves the leftover into the file
t, orig = m._tag_track(); tag2 = LIVEN[1]
assert orig == "Piano" and tag2 != tag1 and tag2.startswith("Piano ~flh") and tag2.count("~flh") == 1, (orig, tag2)
LIVEN.update(FILE)                                                 # FL really re-reads the file (old tag in it)
m._scaled = lambda s: min(s, 0.3)
assert m._tag_gone(t, 0.5) is True                                 # this reload's tag is gone: seen
assert m._untag(t, orig) is True and LIVEN[1] == "Piano"           # and the leftover is taken out of the name
# a fast reload whose put-back fails says so (steps['tag_left'])
A = song("Tagged.flp")
m.fl_running = lambda: True
m._open_project_guess = lambda: (A, [])
m._dialog_ids = lambda: set()
m._sweep_leftover_popups = lambda *a, **k: []
m._open_running_or_fail = lambda project, on_save="no", discard_ok=None: FAIL.update(untag=True)
m._reload_marked = lambda project, prior, steps, why: steps.update(proof="marker")
LIVEN[1] = "Piano"
st = m._reload_in_fl(A)
FAIL["untag"] = False
assert st.get("proof") == "marker" and "tag_left" in st and "rename it" in st["tag_left"], st
restore()
print("L7 reload tags unique, stripped, reported ok")

# ---- K1. two .flp with the same Project-info title ('Song.flp' and 'Song (v2).flp', both 'Song'): the open project
#          couldn't be identified ('can't tell which project is open'), and a save went into a guessed file. Tools on
#          'the open project' prove it by a save through FL (the file last PROVEN open tried first); never a guess from
#          the title; refused with the fix when a twin's guards say no or nothing proves it.
S1 = song("Song.flp"); S2 = song("Song (v2).flp")
m.flp_project_info(S2, output=S2, title="Song")
assert m._flp_title_fast(S2) == "Song"
m._GUESS_CACHE.clear()
m.fl_running = lambda: True
m._fl_windows = lambda: [(1, "Song - FL Studio 2026")]
m._fl_main_window = lambda: (1, "Song - FL Studio 2026")
m._fl_proc_sig = lambda: [4242]
m._open_project_guess = ORIG["_open_project_guess"]
cur, _ = m._open_project_guess()
assert cur is None and "Fix: fl_open" in m._GUESS_WHY["why"] and "flp_project_info" in m._GUESS_WHY["why"], m._GUESS_WHY
m._note_proven_open(S2, "marker")                                  # the reload marker proved FL holds S2
assert m._proven_pick([S1, S2], "Song") == S2
assert m._open_project_guess()[0] is None                          # ... a hint, never a title-only identity
m._fl_main_window = lambda: (1, "Other - FL Studio 2026")         # FL's title changed since: no claim
assert m._proven_pick([S1, S2], "Other") is None
m._fl_main_window = lambda: (1, "Song - FL Studio 2026")
m._fl_proc_sig = lambda: [777]                                    # another FL since: no claim
assert m._proven_pick([S1, S2], "Song") is None
m._fl_proc_sig = lambda: [4242]
saves = []


class BrTwin:
    def call(self, cmd, **k):
        if cmd == "transport" and k.get("action") == "save":       # FL saves ITS file: Song (v2).flp
            saves.append(1); st_ = os.stat(S2); os.utime(S2, ns=(st_.st_atime_ns, st_.st_mtime_ns + 5 * 10 ** 9))
        return {"changed": 0} if cmd == "state" else {}


m.BRIDGE = BrTwin()
tried = []
real_prove = m._prove_open
m._prove_open = lambda p, wait_s=6.0: tried.append(p) or real_prove(p, wait_s)
assert m._open_project_identify(wait_s=2.0)[0] == S2 and tried == [S2] and len(saves) == 1   # the proven one first
m._prove_open = real_prove
m._LAST_PROVEN.clear(); m._OPEN_PROOF.update(path=None); saves.clear()
r = m.fl_save(wait_s=2.0)
assert r["saved"] == S2 and "identified_by" in r and len(saves) == 1, r
assert m._open_project_guess()[0] == S2                            # the save proved it: named from now on
m._LAST_PROVEN.clear(); m._OPEN_PROOF.update(path=None); saves.clear()
m._stale_mark(S1, "reload unproven: test")                         # one twin's file is newer than what FL holds
r = m.fl_save(wait_s=1.0)
assert r["saved"] is None and "older copy" in r["why"] and not saves, r
assert m._save_in_fl_and_wait(S2, 0.2) is False and not saves      # a save aimed at the other twin: refused too
restore()
# a working copy fl-headless makes for itself (fl_ab's [B]) never keeps the title: custom titles too
A = song("Custom.flp"); m.flp_project_info(A, output=A, title="My Album Opener")
Bc = os.path.join(tmp, "Custom [B].flp"); shutil.copyfile(A, Bc)
assert m._own_title_file(A, Bc) is None                            # a custom title is kept for versions ...
assert m._own_title_file(A, Bc, force=True) == "Custom [B]" and m._flp_title_fast(Bc) == "Custom [B]"
print("K1 same-title twins ok")

# ---- K3. in-FL stems failed mid-run ('nothing was opened: ... muted live': its export tried to open / prove the
#          project with its own mutes on) and LEFT channels muted. FL is loaded before the mutes, and a failure puts
#          every mute back (said when it can't).
proj = song("Stems.flp"); LIVE = live_fl(proj)
order = []
m._render_method = lambda *a, **k: "in_fl"
m._sync_before = lambda *a, **k: None
m._live_mutes_ready = lambda *a, **k: None


def spy_load(project, why="render"):
    order.append(("load", sorted(i for i in LIVE if LIVE[i])))
    return {"how": "already", "steps_s": {}}


m._fl_load_file = spy_load
PFO = {"off": 0}
m._preview_fl_on = lambda p, wait_s=None: (PFO.update(off=PFO["off"] - 1) is None and False) if PFO["off"] > 0 else True
n = {"stem": 0}


def stem_render(project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None, split=None):
    n["stem"] += 1
    order.append(("render", sorted(i for i in LIVE if LIVE[i])))
    if n["stem"] == 2:
        PFO["off"] = 1                                             # FL busy right then: the first put-back can't go
        raise RuntimeError("FL's export window didn't appear")
    f = os.path.join(out_dir, (out_stem or "s") + ".wav"); write_wav(f, 0.0 if n["stem"] == 1 else 0.2)
    return {"rendered": [f]}


m._render_in_fl = stem_render
out_dir = os.path.join(tmp, "Stems")
try:
    m._render_stems(proj, out_dir, None, "wav", False, False, method="in_fl"); raise SystemExit("no failure seen")
except RuntimeError as e:
    assert "export window" in str(e), e
assert order[0] == ("load", []) and order[1][0] == "render" and order[1][1], order   # loaded BEFORE muting
assert all(3 not in o[1] for o in order), order                   # the automation channel never muted
assert not any(LIVE.values()), LIVE                                # every mute back after the failure
assert not m._PREVIEW_MUTES, m._PREVIEW_MUTES
restore()
print("K3 stems: load first, mutes always restored ok")

# ---- K4. a sound that loads but renders silent (an unsupported LABS pack, a patch with no keys in range): nothing
#          warned. A silent solo preview / stem now says so, and a pack is noted for the catalog.
import flh_store  # noqa: E402
proj = song("Quiet.flp"); LIVE = live_fl(proj)
flh_store.journal_update(proj, set={"channels": {"1": {"sound": "Glass Piano / Felt", "plugin": "INSTRUMENT"}}})


def silent_render(project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None, split=None):
    f = os.path.join(out_dir, (out_stem or "song") + ".wav"); write_wav(f, 0.0)
    return {"rendered": [f], "seconds": 1.0}


m._render_in_fl = silent_render
r = m.fl_preview(channels=[1], exact=True)
assert r.get("silent") and r.get("silent_hint"), r
h = r["silent_hint"][0]
assert h.startswith("channel 1 '") and "renders silent" in h and "pick another sound" in h and "Glass Piano" in h, h
assert "Glass Piano" in (m._state().get("silent_packs") or {})
assert "Glass Piano" in flh_catalog._silent_packs()                # the catalog ranks it down from now on
r = m.fl_preview(exact=True)
assert "whole song renders silent" in str(r.get("silent_hint")), r
# a channel with no notes playing is silent for that reason: said so, and its pack is NOT noted (PC 2026-10-26: an
# empty Pad channel got 'LABS - Strings' marked as a silent pack)
d_ = m._load(proj); ev_ = m._events(d_)
empty = next((c for c in sorted(m._channel_types(d_, ev_)) if m._channel_plays(d_, c) is False), None)
if empty is not None:
    flh_store.journal_update(proj, set={"channels": {str(empty): {"sound": "LABS - Strings / X", "plugin": "INSTRUMENT"}}})
    r = m.fl_preview(channels=[empty], exact=True)
    assert "has no notes playing" in r["silent_hint"][0], r["silent_hint"]
    assert "LABS - Strings" not in (m._state().get("silent_packs") or {}), m._state().get("silent_packs")
assert m._channel_plays(d_, 1) is True
restore()
print("K4 silent sounds said and noted ok")

shutil.rmtree(tmp, True)
print("ok")
