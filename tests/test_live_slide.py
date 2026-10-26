"""Slide notes through the live-notes route (PC check 2026-10-23 files.slide_flag: a slide turned ON went live, the
edit turning it OFF again never reached FL's save). Covered: the job sends slide False explicitly, flp_edit_notes /
flp_add_notes go live like flp_replace_part (also with merged reloads on), a piano roll that keeps a slide the job
cleared is caught (the fingerprint doesn't hash note flags) and the edit goes in by file + reload instead, and the
PC check's save puts queued edits into FL before reading. FL is simulated as in test_core_live_notes.
Run: python3 tests/test_live_slide.py"""
import json, os, shutil, struct, sys, tempfile, time, types
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
if not os.path.isfile(FIX):
    print("skipped: no fixture tests/corpus/base.flp"); sys.exit(0)
proj = os.path.join(tmp, "Song.flp"); shutil.copy(FIX, proj)
mem = os.path.join(tmp, "fl_memory.flp"); shutil.copy(FIX, mem)          # what FL holds in memory
scripts = os.path.join(tmp, "Piano roll scripts"); os.makedirs(scripts)
m._pr_scripts_dir = lambda: scripts
S = m.NOTE_SLIDE


class Note:
    def __init__(self):
        self.number = 60; self.time = 0; self.length = 0; self.group = 0; self.pan = 0.5; self.velocity = 0.8
        self.release = 0.5; self.color = 0; self.fcut = 0.5; self.fres = 0.5; self.pitchofs = 0; self.slide = False
        self.porta = False; self.muted = False; self.selected = False


class Score:
    def __init__(self, ppq, notes):
        self.PPQ = ppq; self.notes = list(notes)

    @property
    def noteCount(self):
        return len(self.notes)

    def getNote(self, i):
        return self.notes[i]

    def addNote(self, n):
        self.notes.append(n)

    def clearNotes(self, all=False):
        self.notes = []


flp = types.ModuleType("flpianoroll"); flp.Note = Note
sys.modules["flpianoroll"] = flp
FL = {"pr": None, "reloads": 0, "saves": 0, "sticky_slide": False, "runs": 0}


def run_script(score):
    flp.score = score
    path = os.path.join(scripts, m.LIVE_SCRIPT_NAME + ".pyscript")
    ns = {"__name__": "pyscript"}
    exec(compile(open(path, encoding="ascii").read(), path, "exec"), ns)   # noqa: S102 - our generated script
    ns["apply"](None)


def set_channel_notes(path, pid, ch, recs):
    d = m._load(path); ev = m._events(d)
    k = m._pattern_events(d, ev)[pid]
    pl = m._payload(d, ev[k])
    keep = [struct.unpack(m.NOTE_RECORD, pl[i:i + 24]) for i in range(0, len(pl), 24)]
    allr = sorted([r for r in keep if r[2] != ch] + list(recs), key=lambda r: (r[0], r[2], r[4]))
    body = b"".join(struct.pack(m.NOTE_RECORD, *r) for r in allr)
    open(path, "wb").write(m._splice(d, ev, [(k, k + 1, m._data_event(m.EV_NOTES, body))]))


def bridge(cmd, timeout=15.0, **a):
    if cmd == "commands":
        return ["ping", "piano_roll", "where"]
    if cmd == "piano_roll":
        FL["pr"] = (a["pattern"], a["channel"])
        return {"pattern": a["pattern"], "channel": a["channel"], "piano_roll_focused": True}
    return {}


def keys(vk, mods=()):                                            # Ctrl+Alt+Y: FL runs the piano-roll script
    FL["runs"] += 1
    pid, ch = FL["pr"]
    cur = m._channel_notes(mem, ch).get(pid, [])
    had = {(r[0], r[4]) for r in cur if r[1] & S}
    sc = Score(96, [])
    for r in cur:
        n = Note(); n.time, n.number, n.length, n.slide = r[0], r[4], r[3], bool(r[1] & S); sc.notes.append(n)
    run_script(sc)
    out = []
    for n in sc.notes:                                            # FL writes its note record (slide = flag 0x08)
        sl = bool(n.slide) or (FL["sticky_slide"] and (n.time, n.number) in had)   # a piano roll that can't clear it
        out.append((n.time, 16384 | (S if sl else 0), ch, n.length, n.number, 0, 120 + int(n.pitchofs), 0,
                    int(round(n.release * 128)), int(n.color), int(round(n.pan * 128)), int(round(n.velocity * 128)),
                    int(round(n.fcut * 256)), int(round(n.fres * 256))))
    set_channel_notes(mem, pid, ch, out)


def save(project, wait_s=8.0, stop=None):
    FL["saves"] += 1; shutil.copyfile(mem, project); return True


def reload(project):
    FL["reloads"] += 1; shutil.copyfile(project, mem); return {"load": 0.5}


m.IS_WINDOWS = True
m.fl_running = lambda: True
m._open_project_guess = lambda: (proj, ["Song - FL Studio 2026"])
m.fl_dialogs = lambda: []
m._fl_foreground = lambda: True
m.BRIDGE.call = bridge
m._send_keys = keys
m._save_in_fl_and_wait = save
m._reload_in_fl = reload
m._verify_in_background = lambda *a: "verify-x"
m._hot_reload = lambda project, fn: (_ for _ in ()).throw(AssertionError("expected the live route"))
m._state({"live_notes": {"ok": True}})
T = m.TOOLS


def slides(path, ch=0):
    return sorted((r[0], r[4]) for recs in m._channel_notes(path, ch).values() for r in recs if r[1] & S)


# ---- the job says slide False explicitly (an old script on the PC sets it too: it sets every field it's given)
r0 = (0, 16384, 0, 96, 60, 0, 120, 0, 64, 0, 64, 100, 128, 128)
assert m._pr_note(r0, 96)["slide"] is False and m._pr_note((0, 16384 | S) + r0[2:], 96)["slide"] is True
print("job fields ok")

for fast in (None, True):                                         # immediate edits / merged reloads (Windows default)
    m.FAST_FORCE = fast
    shutil.copy(FIX, proj); shutil.copyfile(proj, mem); FL.update(reloads=0, runs=0)
    r = T["flp_replace_part"][0](project=proj, output=proj, channel=0,
                                 notes=[[1, 1, "C2", 100], [1.5, 0.25, "G2", 100, {"slide": True}]])
    assert r["live"] is True and FL["reloads"] == 0, r
    assert slides(mem) == slides(proj) and [k for _p, k in slides(mem)] == [43], slides(mem)
    pid = r.get("pattern") or next(p for p, recs in m._channel_notes(proj, 0).items() if recs)
    # slide OFF with flp_edit_notes: live too, and FL's save holds no slide
    e = T["flp_edit_notes"][0](path=proj, output=proj, channel=0, pattern=pid, ops=[{"op": "slide", "on": False}])
    assert e.get("live") is True and e.get("verified") and not e.get("queued") and FL["reloads"] == 0, e
    assert slides(mem) == [] and slides(proj) == [], (slides(mem), slides(proj))
    assert not m._pending_get(proj)
    # and ON again with flp_edit_notes
    e = T["flp_edit_notes"][0](path=proj, output=proj, channel=0, pattern=pid, ops=[{"op": "slide", "on": True}])
    assert e.get("live") is True and len(slides(mem)) == 2, (e, slides(mem))
    # flp_add_notes: a slide note added live
    a = T["flp_add_notes"][0](path=proj, output=proj, channel=0, pattern=pid,
                              notes=[{"note": "A2", "bar": 1, "beat": 3, "beats": 0.5, "slide": True}])
    assert a.get("live") is True and (int(3 * m._ppq(m._load(proj))), 45) in slides(mem), (a, slides(mem))
    print(f"live slide on/off ok (fast={fast})")

# ---- a piano roll that keeps a slide the job cleared: caught, the edit goes in by file + reload
m.FAST_FORCE = None
FL["sticky_slide"] = True; FL["reloads"] = 0
e = T["flp_edit_notes"][0](path=proj, output=proj, channel=0, pattern=pid, ops=[{"op": "slide", "on": False}])
assert e.get("live") is False and "slide" in e.get("live_note", "") and FL["reloads"] == 1, e
assert slides(mem) == [] and slides(proj) == [], (slides(mem), slides(proj))
FL["sticky_slide"] = False
print("sticky slide fallback ok")

# ---- the PC check's save: queued edits go into FL before FL's save is read
import flh_pccheck as PC  # noqa: E402
calls = []
H = {"_pending_get": lambda p: [{"tool": "flp_edit_notes"}], "_sync_before": lambda why, p: calls.append(("sync", p)),
     "_save_in_fl_and_wait": lambda p, w=8.0: calls.append(("save", p)) or True,
     "_VERIFY_LOCK": None, "_VERIFY_STOP": None}
old_H = PC.H; PC.H = H
try:
    assert PC._save_read(proj) is True and calls == [("sync", proj), ("save", proj)], calls
    calls.clear(); H["_pending_get"] = lambda p: []
    assert PC._save_read(proj) is True and calls == [("save", proj)], calls
finally:
    PC.H = old_H
print("check save flushes the queue ok")
print("ok")
