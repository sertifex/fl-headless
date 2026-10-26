"""Same-title twins, long loads, copies' own titles, flp_save_version, and FL holding an older copy than the file
(PC 2026-10-23: '<Song> Rework.flp' copied from '<Song>.flp' kept its title '<Song> (Piano)'; FL's window showed the
same title for both, the final's open waited 372 s while FL was still loading and failed, and the FINAL came out as
long as the original's). Run: python3 tests/test_open_twins.py"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="flh_twins_")
ROOT = os.path.join(TMP, "root"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = ROOT
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
os.environ["FLH_NO_WARM"] = "1"
FIX = os.path.join(HERE, "corpus", "base.flp")
import fl_headless_mcp as m  # noqa: E402
import flh_store  # noqa: E402
ns = vars(m)
flh_store.register(ns)

_real_sleep = m.time.sleep
m.time.sleep = lambda s: _real_sleep(min(s, 0.02))
m.OPEN_IDLE_S = 0.6
m.OPEN_PROOF_GAP_S = (0.05, 0.1, 0.3)


def copy(name, sub="", title=None):
    d = os.path.join(ROOT, sub); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name); shutil.copy(FIX, p)
    if title is not None:
        run_off(m.flp_project_info, p, output=p, title=title)
    m._GUESS_CACHE.clear()
    return p


def run_off(fn, *a, **k):                                        # a file edit with FL closed
    r0 = m.fl_running; m.fl_running = lambda: False
    try:
        return fn(*a, **k)
    finally:
        m.fl_running = r0


class FL:
    open = None; held = None; title = "Other"; loading_until = 0.0; loading = []; saves = 0; opened = []
    answers = []


def shows():
    if time.time() < FL.loading_until:
        nm = FL.loading[int(time.time() * 10) % len(FL.loading)] if FL.loading else "Fruity Wrapper"
        return f"Opening: {nm}"
    return f"{FL.title} - FL Studio 2026"


def load(p):
    FL.open = p; FL.held = open(p, "rb").read() if p else None
    FL.title = (m._flp_title_fast(p) or os.path.splitext(os.path.basename(p))[0]) if p else "Other"


def bridge(cmd, timeout=15.0, **a):
    if cmd == "transport" and a.get("action") == "save":
        if time.time() < FL.loading_until:
            raise RuntimeError("busy loading")
        FL.saves += 1
        if FL.open and FL.held is not None:
            with open(FL.open, "wb") as f:
                f.write(FL.held)
        return {"done": "save"}
    if cmd == "state":
        return {"playing": False, "changed": 0}
    if cmd == "mixer_set":                                        # FL's mixer track names: what FL holds (a reload's
        t = int(a["track"]); ev = m._events(FL.held)              # marker is read back, then renamed back live)
        if a.get("name") is not None:
            FL.held = m._mixer_name_bytes(FL.held, ev, t, a["name"]); ev = m._events(FL.held)
        return {"name": m._mixer_names(FL.held, ev).get(t) or f"Insert {t}"}
    return {}


def open_dialog(p, on_save="no", discard_ok=None):
    FL.answers.append(on_save); FL.opened.append(p); load(p)
    return True


m.BRIDGE.call = bridge
m.fl_running = lambda: True
m._fl_pids = lambda: [1]
m._fl_windows = lambda: [(1, shows())]
m._fl_main_window = lambda: (1, shows())
m.fl_dialogs = lambda: []
m._open_running_or_fail = open_dialog
m._tag_track = lambda: (3, "Insert 3")
m._tag_gone = lambda t, w, **k: True
m._blank_project = lambda: None
m._fl_load_seen = lambda *a, **k: True
m._verify_in_background = lambda *a, **k: "verify-x"
m._stop_autoplay_later = lambda *a, **k: None
m._measure_load_later = lambda *a, **k: None
m._fl_still_working = lambda: False
m._fl_foreground = lambda: True

try:
    # ---- the twins: '<Song>.flp' and its copy '<Song> Rework.flp' (another folder) share the title 'Song (Piano)'
    orig = copy("Song.flp", "album", title="Song (Piano)")
    twin = os.path.join(ROOT, "rework", "Song Rework.flp"); os.makedirs(os.path.dirname(twin)); shutil.copy(orig, twin)
    m._GUESS_CACHE.clear()
    assert m._flp_title_fast(twin) == "Song (Piano)" and m._title_twins(twin) == [orig]
    w = m._title_warning(twin)
    assert w and "Song Rework (Piano)" in w["fix"], w                 # the fix keeps the ' (Piano)' pattern

    # 1. FL loads the copy for longer than the deadline ('Opening: <plugin>' moving on), then shows the shared
    #    title: the wait goes on while FL loads, a save proves which file FL holds - and an older proof that names
    #    the ORIGINAL (same title) doesn't make it 'not opened'
    load(orig); m._save_in_fl_and_wait(orig, 2.0)                     # proof: FL held the original a moment ago
    assert m._open_state(twin) == "not_open"                          # (that proof alone says: not the twin)
    load(twin); FL.loading = ["Fruity Wrapper", "BBC Symphony Orchestra", "FLEX"]
    FL.loading_until = time.time() + 2.0
    t0 = time.time()
    assert m._wait_open_in_fl(twin, 1) is True, m._OPEN_WAIT
    took = time.time() - t0
    assert took >= 1.9, took                                          # waited through the whole load (deadline 1 s)
    assert m._proven_open(twin) and m._open_state(twin) == "open"
    print("loading title waited through, twin proven by a save ok", round(took, 2))

    # 2. FL idle on another project: fails after the deadline + idle time, saying what it saw
    load(orig); FL.title = "Other"; FL.open = None; FL.held = None; FL.loading_until = 0.0
    m._OPEN_PROOF.update(path=None)
    t0 = time.time()
    assert m._wait_open_in_fl(twin, 1) is False
    took = time.time() - t0
    assert 0.9 <= took < 6.0 and "waited" in m._OPEN_WAIT["why"], (took, m._OPEN_WAIT)
    # FL loaded and then holds the ORIGINAL (the open didn't take): its save lands there - said, not 'opened'
    FL.loading_until = time.time() + 0.5; load(orig)
    assert m._wait_open_in_fl(twin, 1) is False and "Song.flp" in m._OPEN_WAIT["why"], m._OPEN_WAIT
    assert "Opening:" in m._OPEN_WAIT["why"], m._OPEN_WAIT
    print("idle without the target: fails with the reason ok")

    # 3. what FL holds vs the file: an edit written while FL holds the file (no reload) -> FL is behind -> stale;
    #    putting the same bytes back is not 'newer'
    load(twin); m._save_in_fl_and_wait(twin, 2.0)
    assert not m._fl_behind_file(twin)
    held = open(twin, "rb").read()
    run_off(m.flp_set_tempo, twin, 97)                                # written behind FL's back (FL 'closed')
    assert m._fl_behind_file(twin)
    m._write_bytes(twin, held)                                        # an all-or-nothing restore: same bytes
    assert not m._fl_behind_file(twin)
    run_off(m.flp_set_tempo, twin, 97)
    assert m._stale_get(twin) and m._stale_block(twin)                # no save may put FL's older copy over it
    # _fl_load_file: FL holds an older copy -> reloaded from the file (No to 'save changes?'), never saved first
    s0 = FL.saves; FL.answers.clear()
    r = m._fl_load_file(twin)
    assert r["how"] == "reloaded" and FL.saves == s0 and FL.answers == ["no"], (r, FL.saves, FL.answers)
    assert FL.held == open(twin, "rb").read() and not m._stale_get(twin)
    # FL holds it as on disk: nothing to do (proven by a save when the title is shared)
    m._OPEN_PROOF.update(path=None)
    r = m._fl_load_file(twin)
    assert r["how"] == "already", r
    # FL shows another project: opened and waited for (twin title: proven by a save)
    load(orig); m._OPEN_PROOF.update(path=None); FL.opened.clear()
    r = m._fl_load_file(twin)
    assert r["how"] == "opened" and FL.opened == [twin] and FL.open == twin, (r, FL.opened)
    # a write while FL shows another file forgets what FL held: a later load by hand isn't judged 'behind'
    load(orig); m._OPEN_PROOF.update(path=None)
    m._save_in_fl_and_wait(orig, 2.0)
    m.TOOLS["flp_set_tempo"][0](path=twin, bpm=99)                    # FL's save lands in orig: twin written directly
    load(twin)                                                        # opened by hand in FL (fl-headless didn't see)
    assert not m._fl_behind_file(twin)
    print("FL behind the file: detected, reloaded, never saved over ok")

    # 4. a copy written under another stem gets its own title (central: the guard + _save)
    m.fl_running = lambda: False
    out1 = os.path.join(ROOT, "rework", "Song v3.flp")
    m.TOOLS["flp_project_info"][0](path=orig, output=out1, genre="Piano")
    assert m._flp_title_fast(out1) == "Song v3 (Piano)", m._flp_title_fast(out1)
    assert m._flp_title_fast(orig) == "Song (Piano)"                  # the source keeps its title
    out2 = os.path.join(ROOT, "rework", "Song v4.flp")
    m.TOOLS["flp_project_info"][0](path=orig, output=out2, title="Song (Piano)")   # the user's explicit title: kept
    assert m._flp_title_fast(out2) == "Song (Piano)"
    custom = copy("Alpha.flp", "album", title="My Album Opener")
    out3 = os.path.join(ROOT, "album", "Alpha 2.flp")
    m.TOOLS["flp_project_info"][0](path=custom, output=out3, genre="x")
    assert m._flp_title_fast(out3) == "My Album Opener"               # a custom title: kept
    beta = copy("Beta.flp", "album", title="Beta")
    out4 = os.path.join(ROOT, "album", "Beta v2.flp")
    m.flp_edit(beta, [{"tool": "flp_set_tempo", "bpm": 101}], output=out4)
    assert m._flp_title_fast(out4) == "Beta v2" and m.flp_info(out4)["tempo_bpm"] == 101
    m.TOOLS["flp_set_tempo"][0](path=out4, bpm=102)                   # an edit in place: the title stays
    assert m._flp_title_fast(out4) == "Beta v2"
    assert m._derived_title("Song (Piano)", "Song", "Rework") == "Rework (Piano)"
    assert m._derived_title("Song - Live", "Song", "Song 2") == "Song 2 - Live"
    assert m._derived_title("Songbird", "Song", "X") is None          # 'Songbird' isn't 'Song' + a suffix
    assert m._derived_title("Song (Piano)", "Song", "Song (Piano) v2") == "Song (Piano) v2"   # the suffix once
    assert m._derived_title("", "Song", "X") is None and m._derived_title("Song", "Song", "song") is None
    print("copies get their own title ok")

    # 5. flp_save_version: copy + journal (song renamed, final/review dropped, a note), its own title, the load cost
    flh_store.journal_update(orig, set={"song": "Song", "final": {"path": "x.flac", "length": "3:41.40"},
                                        "review": {"when": "now"}, "sections": [{"name": "A", "bar": 1}],
                                        "notes": ["the user likes the soft intro"]})
    m._note_load_cost(orig, 60.0, "test")
    v = flh_store.flp_save_version(orig, "Song Rework 2")
    p2 = v["path"]
    assert os.path.isfile(p2) and os.path.dirname(p2) == os.path.dirname(orig) and v["title"] == "Song Rework 2 (Piano)", v
    assert m._flp_title_fast(p2) == "Song Rework 2 (Piano)" and m._title_twins(p2) == []
    j = flh_store.journal_get(p2)
    assert j["song"] == "Song Rework 2" and not j.get("final") and not j.get("review"), j
    assert j["sections"] == [{"name": "A", "bar": 1}] and j["version_of"] == orig
    assert j["notes"][0] == "the user likes the soft intro" and "version of Song.flp" in j["notes"][-1], j["notes"]
    assert flh_store.journal_get(orig)["final"]["path"] == "x.flac"   # the source keeps its own
    assert m._load_cost_guess(p2)[0] == 60.0 and v.get("load_cost_s") == 60.0
    sub = os.path.join(ROOT, "versions")
    v2 = flh_store.flp_save_version(orig, "Song Final Mix", folder=sub, title="Final Mix")
    assert v2["path"] == os.path.join(sub, "Song Final Mix.flp") and m._flp_title_fast(v2["path"]) == "Final Mix"
    try:
        flh_store.flp_save_version(orig, "Song Rework 2"); raise SystemExit("an existing file was overwritten")
    except FileExistsError:
        pass
    v3 = flh_store.flp_save_version(custom, "Alpha 3")                # a custom title: kept, and said
    assert m._flp_title_fast(v3["path"]) == "My Album Opener" and "title_warning" in v3, v3
    assert "flp_save_version" in m.TOOLS
    print("flp_save_version ok")
finally:
    shutil.rmtree(TMP, True)
print("ok")
