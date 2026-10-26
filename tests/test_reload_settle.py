"""A reload is only 'in FL' once FL really holds the file (PC 2026-10-23: 'merged edits are in FL now (one reload,
1.2 s)' / '2.5 s' on a song that loads in 10-60 s, and the FINAL had none of them):
- the reload tag is read back: a name FL doesn't keep as sent (cut, trimmed) would look 'gone' at once and prove a
  reload that never happened -> a short tag, else no tag (the slower checked route), the name put back;
- the tag vanishing only says FL started loading: the load counts once FL's title names the song, nothing loads and
  FL reports the file's channel count; never settling -> 'unsettled', which is always suspect (checked by FL's save);
- the reference a reload is judged by is the project's slowest recent load: fake fast 'reloads' recorded earlier
  don't hide the next one;
- re-opening the open project after its file changed on disk (fl_open save_current=false / fl_reload_from_disk; FL
  'reloads' from memory in ~1 s, or brings its OLD copy back after the empty template - PC 2026-10-23, the edit was
  then lost to fl_finalize's save) goes the empty template first (seen LOADED) and is proven by a marker stamped into
  the file that the bridge must read back; else an honest error with FL marked as holding an older copy, the record
  of what FL holds unmoved (no save over the file - finalize / render cache / fl_save - no render-cache key from FL).
  Run: python3 tests/test_reload_settle.py"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="flh_rsettle_")
ROOT = os.path.join(TMP, "root"); os.makedirs(ROOT)
os.environ["FLH_ROOTS"] = ROOT
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
import fl_headless_mcp as m  # noqa: E402
REAL = {k: getattr(m, k) for k in ("_tag_track", "_tag_gone")}

_real_sleep = m.time.sleep
m.time.sleep = lambda s: _real_sleep(min(s, 0.005))
P = os.path.join(ROOT, "Big Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), P)
N_CH = m._fingerprint(P)["channels"]


class Br:
    def __init__(self, keep=None):
        self.names = {1: "Strings Long Section Name", 2: "Bass"}; self.keep = keep; self.calls = []
        self.count = N_CH; self.answers = 0; self.loading = 0

    def call(self, cmd, timeout=15.0, **a):
        self.calls.append((cmd, a))
        if cmd == "mixer_set":
            t = a["track"]
            if a.get("name") is not None:
                self.names[t] = self.keep(a["name"]) if self.keep else a["name"]
            return {"name": self.names.get(t, f"Insert {t}")}
        if cmd == "state":
            self.answers += 1
            if self.loading:
                self.loading -= 1
                raise RuntimeError("FL busy")
            return {"channel_count": self.count, "changed": 0}
        return {}


# 1) the tag is read back
b = Br(); m.BRIDGE.call = b.call
assert m._tag_track() == (1, "Strings Long Section Name") and m._TAG_RE.search(b.names[1])   # a unique tag per reload
b = Br(keep=lambda n: n[:12]); m.BRIDGE.call = b.call             # FL cuts names: the long one loses the tag ...
assert m._tag_track() == (1, "Strings Long Section Name") and b.names[1].startswith("t1 ~flh") \
    and len(b.names[1]) <= 12                                       # ... the short one holds
b = Br(keep=lambda n: n.replace(m.RELOAD_TAG, "").strip()); m.BRIDGE.call = b.call   # FL keeps no tag at all
assert m._tag_track() == (None, None) and b.names[1] == "Strings Long Section Name", b.names

# 2) the load settles by content
m.fl_running = lambda: True
m._main_title = lambda: "Big Song - FL Studio 2026"
m._fl_loading_title = lambda: None
b = Br(); b.loading = 3; m.BRIDGE.call = b.call                   # FL busy for a while, then the file's channels
assert m._load_settled(P, 40) is not None and b.answers == 4
b = Br(); b.count = N_CH + 7; m.BRIDGE.call = b.call              # FL holds other content (the old song)
m.RELOAD_SETTLE_S = 0.05
assert m._load_settled(P, None) is None
m._main_title = lambda: "Other Song - FL Studio 2026"             # another song on screen: not settled either
b = Br(); m.BRIDGE.call = b.call
assert m._load_settled(P, None) is None and b.answers == 0
m._main_title = lambda: "Big Song - FL Studio 2026"

# ... inside _reload_in_fl: the tag vanished at once, FL never got the file's content -> 'unsettled' = suspect
m._open_state = lambda p: "open"
m._open_running_or_fail = lambda p, on_save="no", discard_ok=None: True
m._tag_track = lambda: (1, "Bass"); m._tag_gone = lambda t, w, **k: True
b = Br(); b.count = N_CH + 1; m.BRIDGE.call = b.call
m._blank_project = lambda: None                                   # (no empty template on this 'PC')
m._fl_load_seen = lambda *a, **k: False
m.MARKER_GRACE_S = 0.05
P0 = open(P, "rb").read()
try:                                                              # unsettled = suspect: the marker route is tried (the
    m._reload_in_fl(P); raise SystemExit("an unsettled reload must not pass")   # file reopened: FL never shows the
except RuntimeError as e:                                         # marker) -> an honest error
    assert "didn't load" in str(e) and "OLDER copy" in str(e) and "FLH_BLANK" in str(e) and "marker" in str(e), e
assert m._stale_get(P) and m._stale_block(P), "FL holds an older copy: nothing may save FL over the file"
assert open(P, "rb").read() == P0, "the marker is taken out of the file again"
assert m.project_load_cost(P)["seconds"] is None, "an unproven 'reload' isn't recorded as the load time"
m._STALE_FL.clear()
b = Br(); m.BRIDGE.call = b.call                                  # settles: a real load, recorded
steps = m._reload_in_fl(P)
assert "unsettled" not in steps and m._reload_suspect(None, steps) is None, steps

# 3) the reference: the slowest recent load, not an average dragged down by fast fake 'reloads'
m._json_write(m.LOAD_COSTS, {})
for s_ in (40, 1.2, 1.2, 1.2, 1.2):
    m._note_load_cost(P, s_, "reload")
assert m.project_load_cost(P)["seconds"] < m.RELOAD_SUSPECT_MIN_COST_S and m._load_cost_ref(P) == 40.0
assert m._reload_suspect(m._load_cost_ref(P), {"open_dialog": 0.4, "load": 0.8}) == (1.2, 40.0)
print("settle ok")

# 4) PC 2026-10-23: the open song was rewritten on disk (velocities only), fl_open(<it>, save_current=false) said
# 'reloaded' (open 1.4 s + load 1.2 s, the song loads in 10-26 s) - FL had kept its old copy (the live tag vanished,
# title and channel count the same). A simulated FL that 'reloads' the open project from memory:
import struct  # noqa: E402


def read(p):
    with open(p, "rb") as f:
        return f.read()


def with_velocity(src, dst, vel):
    """dst = src with every note's velocity set to vel (nothing the bridge can read changes)."""
    d = read(src); ev = m._events(d); edits = []
    for k, e in enumerate(ev):
        if e["id"] == m.EV_NOTES and e["len"] % 24 == 0:
            p = bytearray(m._payload(d, e))
            for i in range(0, len(p), 24):
                p[i + 21] = vel
            edits.append((k, k + 1, m._data_event(m.EV_NOTES, bytes(p))))
    assert edits, "the fixture has notes"
    with open(dst, "wb") as f:
        f.write(m._splice(d, ev, edits))


BLANK = os.path.join(ROOT, "Empty.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), BLANK)


class SimFL:
    """FL with one open project: the bytes it loaded (held) + live changes (mixer renames through the bridge).
    reopen_ignored: reopening the open project only reverts FL's live changes (the tag goes, nothing is read from
    disk); blank_ignored: FL ignores the empty template's Open (its title stays on the song); restore_old: after the
    template, opening the song brings FL's OLD copy back (an in-memory copy / autosave: FL's title is the file's, its
    notes the old ones) - the PC case of 2026-10-23; silent: the bridge never answers."""
    def __init__(self, path):
        self.reopen_ignored = self.blank_ignored = self.restore_old = self.silent = False
        self.opened = []; self.song = None; self.changed = 0
        self.load(path)

    def load(self, p):
        self.cur = p; self.held = read(p); self.live = {}

    def content(self):                                            # what FL holds now (a save would write this)
        d = self.held
        for t, nm in self.live.items():
            d = m._mixer_name_bytes(d, m._events(d), t, nm)
        return d

    @property
    def vis(self):                                                # what the bridge reads of it
        d = self.content(); ev = m._events(d)
        out = {"channels": sum(1 for e in ev if e["id"] == m.EV_CHANNEL)}
        tempo = next((struct.unpack("<I", m._payload(d, e))[0] for e in ev if e["id"] == m.EV_TEMPO and e["len"] == 4),
                     None)
        if tempo:
            out["tempo"] = round(tempo / 1000.0, 2)
        for pid, p in m._patterns(d, ev).items():
            if p.get("name"):
                out[f"pattern {int(pid)}"] = str(p["name"]).strip()
        return out

    def name(self, t):
        d = self.content()
        return m._mixer_names(d, m._events(d)).get(t) or f"Insert {t}"

    def open(self, p, on_save="no", discard_ok=None):
        self.opened.append(os.path.basename(p))
        same = os.path.abspath(p) == os.path.abspath(self.cur)
        if same and self.reopen_ignored:
            self.live = {}                                        # live changes reverted, the file NOT read
            return True
        if not same and self.blank_ignored:
            return True
        if os.path.abspath(p) == os.path.abspath(BLANK):
            self.song = (self.cur, self.held)                     # FL lets go of the song (a copy stays in memory)
        elif self.restore_old and self.song and os.path.abspath(p) == os.path.abspath(self.song[0]):
            self.cur = p; self.held = self.song[1]; self.live = {}   # the OLD copy back, under the file's title
            return True
        self.load(p)
        return True

    def title(self):
        return os.path.splitext(os.path.basename(self.cur))[0] + " - FL Studio 2026"

    def bridge(self, cmd, timeout=15.0, **a):
        if self.silent:
            raise RuntimeError("no answer from FL's bridge")
        if cmd == "mixer_set":
            t = int(a["track"])
            if a.get("name") is not None:
                self.live[t] = a["name"]; self.changed = 1
            return {"name": self.name(t)}
        if cmd == "state":
            v = self.vis
            return {"channel_count": v["channels"], "tempo": v.get("tempo"), "changed": self.changed,
                    "playing": False, "title": self.title(),
                    "patterns": [{"index": int(k.split()[1]), "name": n} for k, n in v.items()
                                 if k.startswith("pattern ")],
                    "mixer": [{"index": t, "name": self.name(t)} for t in range(1, 5)]}
        if cmd == "transport" and a.get("action") == "save":
            with open(self.cur, "wb") as f:
                f.write(self.content())
            self.changed = 0
            return {"done": "save"}
        return {}


def sim_install(fl):
    m.BRIDGE.call = fl.bridge
    m._open_running_or_fail = fl.open
    m._main_title = fl.title
    m._fl_windows = lambda: [(1, fl.title())]
    m._open_state = lambda p: "open" if os.path.abspath(p) == os.path.abspath(fl.cur) else "not_open"
    m._title_is_one_of = lambda ps: any(os.path.abspath(x) == os.path.abspath(fl.cur) for x in ps)
    m._open_project_guess = lambda: (fl.cur, [fl.title()])
    m._fl_main_window = lambda: (1, fl.title())
    m._fl_pids = lambda: [1]
    m.fl_dialogs = lambda: []
    m._stop_autoplay_later = lambda *a, **k: None
    m._measure_load_later = lambda *a, **k: None


for k, v in REAL.items():                                         # the real tag route / FL helpers from here on
    setattr(m, k, v)
m._fl_load_seen = lambda *a, **k: True
m._blank_project = lambda: BLANK
m._STALE_FL.clear(); m._json_write(m.LOAD_COSTS, {})
S = os.path.join(ROOT, "Score v5.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), S)
m._note_load_cost(S, 20.0, "open"); m._note_load_cost(S, 26.0, "open")   # a heavy song: loads in 20-26 s
fl = SimFL(S); sim_install(fl); m._note_fl_holds(S)
m.MARKER_GRACE_S = 0.05; m.BLANK_LEAVE_S = 0.3; m.BLANK_LEAVE_COST_FRAC = 0.0; m.BLANK_LOAD_S = 0.2
OLD = read(S)
with_velocity(S, S, 33)                                           # new velocities written on disk, behind FL
NEW = read(S)
assert NEW != OLD and m._fl_copy_older(S) and m._file_visible(S) == fl.vis, "only velocities differ"


def no_save_over(p, new):
    """FL holds an older copy: no automatic save (fl_finalize / render cache 'saved FL first', autosync) and no
    fl_save lands in the file; the record of what FL holds isn't the new file."""
    fl.changed = 1
    fp, why = m._rc_project_fp(p, live=True)
    assert fp is None and "older" in why, (fp, why)
    assert m._save_in_fl_and_wait(p, wait_s=0.3) is False
    s = m.fl_save(wait_s=0.3)
    assert s["saved"] is None and "older" in s["why"], s
    assert read(p) == new and m._stale_block(p) and m._fl_held(p) != m._crc(new)
    fl.changed = 0


# 4a) the PC case: fl_open(<open song>, save_current=false), FL 'reloads' from memory, and the template route works
#     by FL's title - but FL brings its OLD copy back. No marker -> an honest error, FL marked stale, nothing saved
fl.reopen_ignored = True; fl.restore_old = True
try:
    m.fl_open(S, save_current=False); raise SystemExit("must fail: FL kept its old notes")
except RuntimeError as e:
    assert "didn't load" in str(e) and "without the marker" in str(e) and "OLDER copy" in str(e), e
assert fl.opened == ["Empty.flp", "Score v5.flp"], fl.opened     # the template first (seen loaded), then the song
assert fl.content() == OLD and read(S) == NEW, "FL kept its old copy; the file keeps the edit (marker out again)"
assert m._fl_held(S) == m._crc(OLD), "what FL holds is only moved on the marker's proof"
no_save_over(S, NEW)

# 4b) the marker route: FL really loads the file after the template -> proven; FL's track name and the file are put
#     back exactly; what FL holds is recorded; a save keeps the edit
fl.restore_old = False; fl.opened.clear()
r = m.TOOLS["fl_reload_from_disk"][0](project=S)
assert "marker" in r["proof"] and fl.opened == ["Empty.flp", "Score v5.flp"] and not r.get("reload_unproven"), r
assert fl.content() == NEW and read(S) == NEW and m._fl_held(S) == m._crc(NEW) and not m._stale_get(S), r
assert m._save_in_fl_and_wait(S, wait_s=1.0) and read(S) == NEW
# ... fl_open(<the open song>, save_current=false) is the same route
with_velocity(S, S, 34); NEW = read(S); fl.opened.clear()
r = m.fl_open(S, save_current=False)
assert fl.content() == NEW and "marker" in r["note"] and fl.opened == ["Empty.flp", "Score v5.flp"], (r, fl.opened)

# 4c) no record of what FL holds (a server restart), the fast route (fl-headless's own reload) 'reloads' in ~0 s for a
#     20-26 s song: suspect -> content check (can't tell: velocities only) -> the marker route; never 'done' on the
#     fast one, and that fake time isn't recorded as the load cost
fl.load(S); fl.held = OLD                                         # FL holds the old copy again (title / vis the same)
with_velocity(S, S, 45); NEW = read(S); m._FL_HOLDS.clear(); m._HOLDS_DISK["loaded"] = True
fl.opened.clear()
steps = m._reload_in_fl(S, discard=True)
assert fl.content() == NEW and fl.opened == ["Score v5.flp", "Empty.flp", "Score v5.flp"], fl.opened
assert steps["content_check"] == "can't tell" and steps["proof"] == "marker" and "fast_load" in steps, steps
assert "load" not in steps and m._fl_held(S) == m._crc(NEW) and not m._stale_get(S), steps
# ... and the same with FL bringing its old copy back after the template: an error, FL stale, the record unmoved
fl.load(S); fl.held = OLD; m._note_fl_holds(S, OLD); fl.restore_old = True
with_velocity(S, S, 46); NEW = read(S); fl.opened.clear()
try:
    m._reload_in_fl(S, discard=True); raise SystemExit("must fail: FL kept its old notes")
except RuntimeError as e:
    assert "without the marker" in str(e), e
assert fl.content() == OLD and read(S) == NEW
no_save_over(S, NEW)
fl.restore_old = False

# 4d) FL ignores the empty template too (its title stays on the song): an honest error before the song is opened
#     again; FL marked as holding an OLDER copy, what FL holds unchanged
m._STALE_FL.clear(); fl.load(S); fl.held = OLD; m._note_fl_holds(S, OLD); fl.blank_ignored = True; fl.opened.clear()
with_velocity(S, S, 50); NEW = read(S)
try:
    m.fl_open(S, save_current=False); raise SystemExit("must fail: FL never loaded the file")
except RuntimeError as e:
    assert "didn't load" in str(e) and "didn't show the empty template loaded" in str(e) and "OLDER copy" in str(e), e
assert fl.content() == OLD and fl.opened == ["Empty.flp"] and read(S) == NEW
no_save_over(S, NEW)
# ... fl_open of it again goes the same route (FL holds an older copy), and once FL takes it, all is clear
fl.blank_ignored = False; fl.opened.clear()
r = m.fl_open(S, save_current=False)
assert fl.content() == NEW and fl.opened == ["Empty.flp", "Score v5.flp"] and not m._stale_get(S), (fl.opened, r)
assert "marker" in r["proof"], r

# 4e) fl-headless's own fast reload whose content CAN be told apart (the tempo changed on disk): proven by content,
#     no extra load; with FL keeping its old copy, the content says 'old copy' -> the marker route
fl.load(S); fl.reopen_ignored = False; fl.opened.clear()
m.flp_set_tempo(S, 133); m._FL_HOLDS.clear()
steps = m._reload_in_fl(S, discard=True)
assert steps.get("proof") == "content" and fl.opened == ["Score v5.flp"] and fl.vis["tempo"] == 133.0, steps
fl.reopen_ignored = True; fl.opened.clear(); m.flp_set_tempo(S, 141)
steps = m._reload_in_fl(S, discard=True)
assert steps["content_check"] == "old copy" and steps["proof"] == "marker", steps
assert fl.vis["tempo"] == 141.0 and fl.opened == ["Score v5.flp", "Empty.flp", "Score v5.flp"], fl.opened

# 4f) no empty template on the PC: the file is reopened directly, still proven by the marker - FL 'reloading' from
#     memory fails honestly (pointing at FLH_BLANK), FL really reading the file passes
m._blank_project = lambda: None
fl.load(S); fl.reopen_ignored = True; m._note_fl_holds(S); with_velocity(S, S, 60); NEW = read(S); fl.opened.clear()
try:
    m.fl_open(S, save_current=False); raise SystemExit("must fail: FL served its own copy")
except RuntimeError as e:
    assert "didn't load" in str(e) and "FLH_BLANK" in str(e) and "marker" in str(e), e
assert fl.content() != NEW and m._stale_get(S) and read(S) == NEW
fl.reopen_ignored = False; fl.opened.clear()
r = m.fl_open(S, save_current=False)
assert fl.content() == NEW and fl.opened == ["Score v5.flp"] and "marker" in r["proof"] and not m._stale_get(S), r

# 4g) the bridge never answers (no script in FL): nothing can be proven -> said (reload_unproven), FL marked as
#     holding an older copy (no save over the file), the record unmoved, the file untouched (no marker written)
m._blank_project = lambda: BLANK
fl.load(S); fl.held = OLD; m._note_fl_holds(S, OLD); with_velocity(S, S, 62); NEW = read(S); fl.opened.clear()
fl.silent = True
r = m.fl_open(S, save_current=False)
assert r.get("reload_unproven") and "isn't proven" in r["note"] and "bridge" in r["reload_unproven"], r
assert read(S) == NEW and m._stale_get(S) and m._fl_held(S) == m._crc(OLD)
assert m._save_in_fl_and_wait(S, wait_s=0.3) is False and read(S) == NEW
fl.silent = False
shutil.rmtree(TMP, True)
print("ok")

