"""Live notes through a piano-roll script (experimental): the generated .pyscript (run here against a fake
flpianoroll), its safety checks (fresh job, exact expected notes), _live_notes driving the open FL, flp_replace_part
writing live without a reload and verified by FL's save, the fallback that leaves no live leftovers, the probe.
FL is simulated: its 'memory' is a file the fake piano roll edits. Run: python3 tests/test_core_live_notes.py"""
import json, os, shutil, struct, sys, tempfile, time, types
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
proj = os.path.join(tmp, "Song.flp"); shutil.copy(FIX, proj)
mem = os.path.join(tmp, "fl_memory.flp"); shutil.copy(FIX, mem)          # what FL holds in memory
scripts = os.path.join(tmp, "Piano roll scripts"); os.makedirs(scripts)
m._pr_scripts_dir = lambda: scripts


# ---- a fake flpianoroll module
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


def run_script(score):
    flp.score = score
    path = os.path.join(scripts, m.LIVE_SCRIPT_NAME + ".pyscript")
    src = open(path, encoding="ascii").read()                    # must be pure ASCII for FL
    ns = {"__name__": "pyscript"}
    exec(compile(src, path, "exec"), ns)                          # noqa: S102 - our own generated test script
    assert "createDialog" not in ns                               # no dialog: FL runs apply() at once
    ns["apply"](None)
    return json.load(open(os.path.join(scripts, m.LIVE_SCRIPT_NAME + ".receipt.json")))


def rec(pos, key, ln=96, vel=100, ch=0, pan=64, color=0):
    return (pos, 16384, ch, ln, key, 0, 120, 0, 64, color, pan, vel, 128, 128)


# ---- the script itself
P = 96
expect = [rec(0, 60), rec(96, 62)]
new = [rec(0, 64, vel=64, pan=32, color=3), rec(192, 67, ln=48)]
job = {"id": "j1", "until": time.time() + 60, "ppq": P, "expect": sorted([r[0], r[4]] for r in expect),
       "notes": [m._pr_note(r, P) for r in new]}
assert job["notes"][0] == {"pos": 0, "len": 96, "key": 64, "vel": 0.5, "pan": 0.25, "rel": 0.5, "color": 3,
                           "fcut": 0.5, "fres": 0.5, "pitchofs": 0, "slide": False}, job["notes"][0]


def score_of(recs, ppq=P):
    out = []
    for r in recs:
        n = Note(); n.time = r[0] * ppq // P; n.number = r[4]; n.length = r[3] * ppq // P; out.append(n)
    return Score(ppq, out)


m._write_pr_script(job)
sc = score_of(expect)
assert run_script(sc)["status"] == "done"
assert [(n.time, n.number, n.length, n.velocity, n.pan, n.color) for n in sc.notes] == \
    [(0, 64, 96, 0.5, 0.25, 3), (192, 67, 48, 0.78125, 0.5, 0)]
sc = score_of(expect, ppq=192)                                    # a piano roll at another PPQ: times scale
assert run_script(sc)["status"] == "done" and [(n.time, n.length) for n in sc.notes] == [(0, 192), (384, 96)]
sc = score_of([rec(0, 60)])                                       # not the notes the job expects: hands off
r = run_script(sc)
assert r["status"] == "target mismatch" and [n.number for n in sc.notes] == [60], r
m._write_pr_script(dict(job, until=time.time() - 1))
assert run_script(score_of(expect))["status"] == "expired"
m._write_pr_script(dict(job, done=True, notes=[]))
sc = score_of(expect); assert run_script(sc)["status"] == "no job" and len(sc.notes) == 2
# FL may not let the script read files: then the job embedded in the script is used
m._write_pr_script(job); os.remove(os.path.join(scripts, m.LIVE_SCRIPT_NAME + ".job.json"))
assert run_script(score_of(expect))["status"] == "done"
print("script ok")

# ---- a simulated FL around it
m.IS_WINDOWS = True
m.fl_running = lambda: True
m._open_project_guess = lambda: (proj, ["Song - FL Studio 2026"])
m.fl_dialogs = lambda: list(FL["dialogs"])
m._fl_foreground = lambda: True
FL = {"pr": None, "dialogs": [], "other_script": False, "wrong_channel": False, "reloads": 0, "saves": 0}


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
        ch = a["channel"] + (1 if FL["wrong_channel"] else 0)
        FL["pr"] = (a["pattern"], ch)
        return {"pattern": a["pattern"], "channel": ch, "focused_caption": "Piano roll - x", "piano_roll_focused": True}
    if cmd == "transport" and a.get("action") == "stop":
        return {"done": "stop"}
    return {}


m.BRIDGE.call = bridge


def keys(vk, mods=()):                                            # Ctrl+Alt+Y: FL runs its last piano-roll script
    assert (vk, tuple(mods)) == (0x59, (0x11, 0x12)), (vk, mods)
    if FL["other_script"]:                                        # the user ran another script since: its dialog
        FL["dialogs"].append({"hwnd": 77, "title": "Some script", "class": "TScriptDialog", "kind": "other",
                              "text": [], "buttons": []})
        return
    pid, ch = FL["pr"]
    cur = m._channel_notes(mem, ch).get(pid, [])
    sc = Score(P, [])
    for r in cur:
        n = Note(); n.time, n.number, n.length = r[0], r[4], r[3]; sc.notes.append(n)
    run_script(sc)
    recs = [(n.time, 16384, ch, n.length, n.number, 0, 120 + int(n.pitchofs), 0, int(round(n.release * 128)),
             int(n.color), int(round(n.pan * 128)), int(round(n.velocity * 128)), int(round(n.fcut * 256)),
             int(round(n.fres * 256))) for n in sc.notes]
    set_channel_notes(mem, pid, ch, recs)


m._send_keys = keys
m._dismiss = lambda d: FL["dialogs"].remove(d) or True


def save(project, wait_s=8.0):                                    # FL saves what it holds
    FL["saves"] += 1; shutil.copyfile(mem, project); return True


m._save_in_fl_and_wait = save


def reload(project):                                              # FL re-reads the file
    FL["reloads"] += 1; shutil.copyfile(project, mem); return {"open_dialog": 0.5, "load": 30}


m._reload_in_fl = reload
m._verify_in_background = lambda *a: "verify-x"
m._hot_reload = lambda project, fn: (_ for _ in ()).throw(AssertionError("the live path must not hot-reload"))

# ---- _live_notes directly
pats = m._channel_notes(mem, 0); pid = sorted(pats)[0]; old = pats[pid]
add = sorted(old + [rec(9600, 72)])
r = m._live_notes(0, pid, add, old, P)
assert r["receipt"] == "done" and m._note_keys(m._channel_notes(mem, 0)[pid]) == m._note_keys(add), r
job_now = open(os.path.join(scripts, m.LIVE_SCRIPT_NAME + ".pyscript")).read()
assert "'done': True" in job_now                                  # a later manual run does nothing
m._live_notes(0, pid, old, add, P)                                # back
assert m._note_keys(m._channel_notes(mem, 0)[pid]) == m._note_keys(old)
print("live notes ok")

# ---- flp_replace_part on the open project: live, verified, no reload
T = m.TOOLS
assert m._live_replace_part(proj, {"project": proj, "output": proj, "channel": 0, "notes": [[1, 1, 60, 80]]}) is None
FL["reloads"] = 0; shutil.copyfile(proj, mem)
m._state({"live_notes": {"ok": True}})
notes = [[1, 1, "C4", 80], [2, 0.5, 64, 70], [3, 2, "G3", 60]]
r = T["flp_replace_part"][0](project=proj, output=proj, channel=0, notes=notes)
assert r["live"] is True and r["verified"] is True and r["added"] == 3 and FL["reloads"] == 0, r
assert open(proj, "rb").read() == open(mem, "rb").read()          # the file is FL's own save of the edit
want = os.path.join(tmp, "want.flp"); shutil.copy(FIX, want); m.flp_replace_part(want, want, 0, notes=notes)
assert m._fingerprint(proj) == m._fingerprint(want)
print("replace_part live ok")

# ---- the piano roll shows the wrong channel: nothing is left behind, the edit goes in by file + reload
shutil.copy(FIX, proj); shutil.copyfile(proj, mem); FL["wrong_channel"] = True
r = T["flp_replace_part"][0](project=proj, output=proj, channel=0, notes=notes)
assert r["live"] is False and "didn't check out" in r["live_note"] and FL["reloads"] == 1, r
assert m._fingerprint(proj) == m._fingerprint(want) and m._fingerprint(mem) == m._fingerprint(want)
FL["wrong_channel"] = False
# another script is FL's 'last script': its dialog is cancelled, live notes switch off until the probe passes again
shutil.copy(FIX, proj); shutil.copyfile(proj, mem); FL["other_script"] = True
r = T["flp_replace_part"][0](project=proj, output=proj, channel=0, notes=notes)
assert r["live"] is False and not FL["dialogs"] and m._state()["live_notes"]["ok"] is False, r
assert m._fingerprint(proj) == m._fingerprint(want)
FL["other_script"] = False
print("fallbacks ok")

# ---- the probe: add a note, check, restore; switches live notes on
shutil.copy(FIX, proj); shutil.copyfile(proj, mem); before = m._fingerprint(proj)
p = m.fl_live_notes_probe()
assert p["live_notes_enabled"] is True and p["steps"]["add_probe_note"]["fl_holds_it"], p
assert p["steps"]["restore"]["fl_holds_it"] and m._fingerprint(mem) == before and m._state()["live_notes"]["ok"]
FL["other_script"] = True; FL["reloads"] = 0
p = m.fl_live_notes_probe()
assert p["live_notes_enabled"] is False and "cleanup" in p["steps"] and FL["reloads"] == 1, p
assert m._fingerprint(mem) == before
FL["other_script"] = False
m.BRIDGE.call = lambda cmd, timeout=15.0, **a: (_ for _ in ()).throw(RuntimeError("FL: unknown command commands"))
p = m.fl_live_notes_probe()
assert "update_script" in p["next"], p
print("ok")
