"""Core review fixes (internal write-up): open-project identity, verify vs hand edits, truncated saves, state.json under
load, per-project backups, flp_edit output safety, name validation, one FL / no closing FL, Recycle Bin only, FL lock,
dispatcher hygiene (NaN, numpy, hooks, compact JSON, bad JSON lines), snapshot alt text, sample name clashes,
render names, reply parsing, schemas. Linux, no FL. Run: python3 tests/test_core_fixes.py"""
import io, json, math, os, shutil, struct, subprocess, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, os.path.join(ROOT, "src"))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = os.path.join(tmp, "root") + " ; " + '"' + os.path.join(tmp, "root2") + '"'
os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.makedirs(os.path.join(tmp, "root")); os.makedirs(os.path.join(tmp, "root2"))
import fl_headless_mcp as m  # noqa: E402
import flh_store  # noqa: E402
ns = vars(m)
R = os.path.join(tmp, "root")


def raises(fn, *words, exc=Exception):
    try:
        fn()
    except exc as e:
        assert all(w.lower() in str(e).lower() for w in words), (str(e), words)
        return e
    raise AssertionError(f"should have raised ({words})")


def mini_flp(extra=b""):
    """A tiny valid project: header + FLdt with a tempo event (+ extra events)."""
    body = bytes([m.EV_TEMPO]) + struct.pack("<I", 120000) + extra
    return b"FLhd" + struct.pack("<IhHH", 6, 0, 1, 96) + b"FLdt" + struct.pack("<I", len(body)) + body


# ---- M1: roots are stripped (spaces / quotes after ';'), links can't lead outside, FL's Templates folder (.flp)
assert m.roots()[1] == os.path.normcase(os.path.abspath(os.path.join(tmp, "root2"))), m.roots()
assert m.writable(os.path.join(tmp, "root2", "a.flp"))
outside = os.path.join(tmp, "outside"); os.makedirs(outside)
os.symlink(outside, os.path.join(R, "link"))
raises(lambda: m.writable(os.path.join(R, "link", "x.mid")), "outside FLH_ROOTS", exc=PermissionError)
ud = os.path.join(tmp, "ud", "FL Studio"); os.makedirs(os.path.join(ud, "Projects", "Templates"))
os.environ["FLH_FL_USERDATA"] = ud
_pick = m._userdata_pick; m._userdata_pick = lambda: {"path": ud, "candidates": []}
assert m.writable(os.path.join(ud, "Projects", "Templates", "Mine", "Mine.flp"))
raises(lambda: m.writable(os.path.join(ud, "Projects", "Templates", "evil.py")), exc=PermissionError)
raises(lambda: m.writable(os.path.join(ud, "Settings", "x.flp")), exc=PermissionError)
m._userdata_pick = _pick
print("writable ok")

# ---- C3: a save that is only partly on disk is refused (never edited and written back truncated)
p = os.path.join(R, "t.flp"); full = mini_flp(bytes([m.EV_TEMPO]) + struct.pack("<I", 100000))
open(p, "wb").write(full)
assert m.flp_info(p)
open(p, "wb").write(full[:-5])                                  # cut at an event boundary: parses, but incomplete
m._forget_file(p)
raises(lambda: m._load(p), "incomplete", exc=ValueError)
raises(lambda: m._events(full[:-5]), "incomplete", exc=ValueError)
# the save wait: mtime/size must move AND settle, and the file must be complete
open(p, "wb").write(full); before = m._file_sig(p)


def slow_writer():                                              # FL writes the file in two steps
    time.sleep(0.2); open(p, "wb").write(full[:-5]); os.utime(p, (time.time() + 3, time.time() + 3))
    time.sleep(0.5); open(p, "wb").write(full); os.utime(p, (time.time() + 6, time.time() + 6))


th = threading.Thread(target=slow_writer); th.start(); t0 = time.time()
assert m._wait_saved(p, before, 5.0) and open(p, "rb").read() == full and time.time() - t0 > 0.7
th.join()
assert not m._wait_saved(p, m._file_sig(p), 0.5)                # nothing saved: False after the wait
print("truncated saves ok")

# ---- H1: state.json - no lost updates across threads and processes, atomic
m._state({"last_opened": "keep", "fl_edition": "producer"})


def bump(k):
    for i in range(60):
        m._state({f"{k}_{i % 3}": i})


ths = [threading.Thread(target=bump, args=(f"t{k}",)) for k in range(4)]
code = ("import os,sys; sys.path.insert(0, %r); os.environ['FLH_ROOTS']=%r; os.environ['LOCALAPPDATA']=%r\n"
        "import fl_headless_mcp as m\nfor i in range(60): m._state({'proc_%%d' %% (i %% 3): i})\n") % (
    os.path.join(ROOT, "src"), R, os.environ["LOCALAPPDATA"])
procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(2)]
[t.start() for t in ths]; [t.join() for t in ths]; [pr.wait() for pr in procs]
st = m._state()
assert st["last_opened"] == "keep" and st["fl_edition"] == "producer", st
assert all(st.get(f"t{k}_{j}") is not None for k in range(4) for j in range(3)) and st.get("proc_0") is not None, st
print("state ok")

# ---- H2: backups per project (full path), capped per project, never another project's
a_dir, b_dir = os.path.join(R, "A"), os.path.join(R, "B"); os.makedirs(a_dir); os.makedirs(b_dir)
sa, sb = os.path.join(a_dir, "song.flp"), os.path.join(b_dir, "song.flp")
open(sa, "wb").write(mini_flp()); open(sb, "wb").write(mini_flp())
m.flp_set_tempo(sa, 90); m._CALL_BACKUPS.clear(); m.flp_set_tempo(sb, 99)
va, vb = m.flp_versions(sa), m.flp_versions(sb)
assert len(va) == 1 and len(vb) == 1 and va[0]["backup"] != vb[0]["backup"], (va, vb)
raises(lambda: m.flp_restore(sa, vb[0]["backup"]), "same project", exc=ValueError)
for i in range(6):
    m._CALL_BACKUPS.clear(); m._backup(sb, keep=3)
assert len(m.flp_versions(sb)) == 3 and len(m.flp_versions(sa)) == 1        # B's rotation never touches A
m._CALL_BACKUPS.clear(); m.flp_restore(sa, va[0]["backup"])
assert m.flp_info(sa)["tempo_bpm"] == 120
print("backups ok")

# ---- H3: flp_edit(output=<another existing file>): backed up first; a failing op puts THAT file back
other = os.path.join(R, "other.flp"); open(other, "wb").write(mini_flp()); m.flp_set_tempo(other, 77)
orig_other = open(other, "rb").read(); m._CALL_BACKUPS.clear()
raises(lambda: m.TOOLS["flp_edit"][0](project=sa, output=other, ops=[{"tool": "flp_set_tempo", "bpm": "x"}]))
assert open(other, "rb").read() == orig_other and m.flp_info(other)["tempo_bpm"] == 77
m._CALL_BACKUPS.clear()
r = m.TOOLS["flp_edit"][0](project=sa, output=other, ops=[{"tool": "flp_set_tempo", "bpm": 101}])
assert r["replaced_existing"]["backup"] and m.flp_info(other)["tempo_bpm"] == 101, r
assert any(open(v["backup"], "rb").read() == orig_other for v in m.flp_versions(other))
print("flp_edit output ok")

# ---- H4: profile / collection / sound-profile names can't leave their folders
flh_store.register(ns)
for bad in ("../../evil", os.path.join(tmp, "abs"), "a/b", "..", "x" * 80, "a\\b", "bad\nname"):
    raises(lambda: flh_store.profile_set(bad, {"style": "x"}), exc=ValueError)
assert not os.path.exists(os.path.join(tmp, "evil.json")) and not os.path.exists(os.path.join(tmp, "abs.json"))
assert flh_store.profile_set("My Songs 2", {"style": "x"})["style"] == "x"
assert flh_store.profile_get("../../evil") == {}
raises(lambda: m._profile_file("../x"), exc=ValueError)
import flh_setup  # noqa: E402
flh_setup.register(ns)
raises(lambda: flh_setup.apply(collection="../../x"), exc=ValueError)
print("names ok")

# ---- H5: never a second FL, never closing FL without an explicit restart_fl / confirm
m.fl_running = lambda: True
raises(lambda: m.fl_render(sa, allow_while_running=True), "second FL", exc=ValueError)
raises(lambda: m._run_fl(sa, R, "wav", 1, 5), "second FL", exc=RuntimeError)
m._reg_dump = lambda *a: {"K": {"N": [4, 1]}}
closed = []
m.fl_close = lambda save=True, timeout_s=25: closed.append(1) or {"closed": True}
raises(lambda: m.fl_settings_set([{"key": "K", "name": "N", "value": 2}]), "restart_fl=true", exc=RuntimeError)
raises(lambda: m.fl_settings_restore(), exc=Exception)
assert not closed
raises(lambda: m.TOOLS["fl_close"][0](), "confirm=true", exc=ValueError)
assert not closed
for bad in ("MPK\nimport os", "a\\b", "x\x00y", "pad line"):
    raises(lambda: m._safe_device(bad), "device", exc=ValueError)
assert m._safe_device("MPK mini 3 (USB) #2") == "MPK mini 3 (USB) #2"
m.fl_running = lambda: False
print("one FL / restart ok")

# ---- H6: files_recycle never deletes permanently: no Recycle Bin on the drive -> refused, nothing touched
victim = os.path.join(R, "victim.wav"); open(victim, "wb").write(b"x")
m.IS_WINDOWS = True
m._has_recycle_bin = lambda path: (False, "it's on a network share (no Recycle Bin there)")
shell = []
m._recycle_shell = lambda path: shell.append(path) or (0, False)
raises(lambda: m.files_recycle([victim]), "not recycled", "permanently", exc=RuntimeError)
assert os.path.exists(victim) and not shell
m._has_recycle_bin = lambda path: (True, "")
m._recycle_shell = lambda path: (os.remove(path), (0, False))[1]
assert m.files_recycle([victim])["recycled"] == [victim]
# a whole folder refused (shell code 120) goes piece by piece: children first, then the folder
deep = os.path.join(R, "deep"); os.makedirs(os.path.join(deep, "songs", "a")); os.makedirs(os.path.join(deep, "songs", "b"))
open(os.path.join(deep, "songs", "a", "x.flp"), "wb").write(b"x")
def _shell(path):
    if os.path.normcase(path) in (os.path.normcase(deep), os.path.normcase(os.path.join(deep, "songs"))) and \
            any(os.scandir(path)):
        return 120, False                                       # Windows refuses the full tree
    shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    return 0, False
m._recycle_shell = _shell
assert m.files_recycle([deep])["recycled"] == [deep] and not os.path.exists(deep)
m.IS_WINDOWS = False
assert "powershell" not in open(os.path.join(ROOT, "src", "fl_headless_mcp.py"), encoding="utf-8").read().split(
    "def files_recycle")[1].split("\ndef ")[0].lower()
print("recycle ok")

# ---- C1: which project FL has open: main window only, exact name, never a plugin window or a prefix
proj = os.path.join(R, "Beat.flp"); open(proj, "wb").write(mini_flp())
proj2 = os.path.join(R, "Beat 2.flp"); open(proj2, "wb").write(mini_flp())
m._state({"last_opened": proj})
wins = {"main": (1, "Beat 2 - FL Studio 2026"), "all": [(1, "Beat 2 - FL Studio 2026"), (2, "Beat - Sytrus")]}
m._fl_windows = lambda: wins["all"]; m._fl_main_window = lambda: wins["main"]; m.fl_running = lambda: True
assert m._title_stem("*Beat 2.flp - FL Studio 21") == "Beat 2" and m._title_stem("FL Studio 2026") is None
cur, _ = m._open_project_guess()
assert cur == proj2, cur                                        # never Beat.flp (prefix / plugin window)
assert m._open_state(proj) == "not_open" and m._open_state(proj2) == "open"
wins["main"] = (1, "FL Studio 2026"); wins["all"] = [wins["main"]]   # loading: no name yet
assert m._open_state(proj) == "unknown"
ran = []
raises(lambda: m._with_open_project(proj, proj, lambda: ran.append(1)), "can't tell", exc=RuntimeError)
assert not ran                                                  # nothing written behind FL's back
raises(lambda: m._reload_in_fl(proj), "not reloading", exc=RuntimeError)
wins["main"] = (1, "Beat 2 - FL Studio 2026"); wins["all"] = [wins["main"]]
assert m._title_is_one_of([proj2]) and not m._title_is_one_of([proj])
m.fl_running = lambda: False
print("open project identity ok")

# ---- H12: the reload fails after the file was edited -> an honest result, not an error; FL lock + verify lock
m.fl_running = lambda: True
m._open_project_guess = lambda: (proj, [])
saves = {"n": 0}


def fake_call(cmd, timeout=15.0, **a):
    if cmd == "transport" and a.get("action") == "save":
        saves["n"] += 1
        threading.Timer(0.1, lambda: os.utime(proj, (time.time() + 10 + saves["n"],) * 2)).start()
    return {"playing": False}


m.BRIDGE.call = fake_call
m._reload_in_fl = lambda p: (_ for _ in ()).throw(RuntimeError("couldn't drive FL's Open dialog"))
r = m._with_open_project(proj, proj, lambda: (m.flp_set_tempo(proj, 133), {"output": proj})[1])
assert r["reloaded_in_fl"] is False and r["fl_shows_old_version"] and "File > Open" in r["WARNING"], r
assert m.flp_info(proj)["tempo_bpm"] == 133
assert m._PENDING_VERIFY[os.path.normcase(os.path.abspath(proj))]["want"]
# a background job holding FL: an FL tool is refused fast, an edit of the open project too
hold = threading.Event(); release = threading.Event()
m._start_job("render", lambda: (hold.set(), release.wait(10)))
hold.wait(3)
m.TOOLS["fl_zz_probe"] = (lambda: {"ok": 1}, "", {"type": "object", "properties": {}})
m._install_fl_lock(ns)
t0 = time.time(); e = raises(lambda: m.TOOLS["fl_zz_probe"][0](), "busy", "job render", exc=RuntimeError)
assert time.time() - t0 < 5
raises(lambda: m._hot_reload(proj, lambda: {}), "busy", exc=RuntimeError)
release.set(); time.sleep(0.3)
assert m.TOOLS["fl_zz_probe"][0]() == {"ok": 1}
# the autoplay stopper stands down once a tool starts playback on purpose
stops = []
m.BRIDGE.call = lambda cmd, timeout=15.0, **a: stops.append(a.get("action")) or {}
m.time_sleep = time.sleep
m._stop_autoplay_later(wait_s=1)
m.TOOLS["fl_zz_play"] = (lambda: {"playing": True}, "", {"type": "object", "properties": {}}); m._install_fl_lock(ns)
m.TOOLS["fl_zz_play"][0]()
time.sleep(3.5)
assert "stop" not in stops, stops
m.fl_running = lambda: False
print("reload honesty / FL lock ok")

# ---- dispatcher: NaN/inf -> null + note, numpy -> numbers, compact JSON, hooks, bad JSON lines
import numpy as np  # noqa: E402
seen = {"access": [], "recent": []}
m.TOOLS["zz_nan"] = (lambda: {"lufs": float("nan"), "peak": np.float32(-1.5), "arr": np.arange(3), "n": np.int64(7),
                              "nested": [{"x": float("-inf")}]}, "", {"type": "object", "properties": {}})
m.access_adjust = lambda res, tool=None: (seen["access"].append(tool), dict(res, access=["note"]))[1]
m.flh_recent_note = lambda name, args, result=None, error=None, took=None: seen["recent"].append((name, error is not None))
out = m.handle({"method": "tools/call", "params": {"name": "zz_nan", "arguments": {}}})
txt = out["content"][0]["text"]
assert ": " not in txt and "\n" not in txt, txt                   # compact separators
res = json.loads(txt)
assert res["lufs"] is None and res["nested"][0]["x"] is None and res["peak"] == -1.5 and res["arr"] == [0, 1, 2]
assert res["n"] == 7 and "note_not_finite" in res and res["access"] == ["note"], res
assert seen["access"] == ["zz_nan"] and seen["recent"][-1] == ("zz_nan", False)
m.TOOLS["zz_err"] = (lambda: 1 / 0, "", {"type": "object", "properties": {}})
assert m.handle({"method": "tools/call", "params": {"name": "zz_err", "arguments": {}}})["isError"]
assert seen["recent"][-1] == ("zz_err", True)
r = m.flh_run([{"tool": "zz_nan"}])
assert r["ok"] and r["steps"][0]["result"]["lufs"] is None and seen["recent"][-1] == ("zz_nan", False), r
# after flh_reload the live namespace keeps the recent-calls log, the locks and the hooks still run
m._RECENT_CALLS = ["x"]
L0 = m._FL_LOCK
m.flh_reload()
L = sys._flh_live
assert L["_RECENT_CALLS"] is m._RECENT_CALLS and L["_FL_LOCK"] is L0 and L["_STATE_LOCK"] is m._STATE_LOCK
L["TOOLS"]["zz_nan"] = m.TOOLS["zz_nan"]
L["flh_recent_note"] = m.flh_recent_note; L["access_adjust"] = m.access_adjust
n0 = len(seen["recent"])
json.loads(m.handle({"method": "tools/call", "params": {"name": "zz_nan", "arguments": {}}})["content"][0]["text"])
assert len(seen["recent"]) == n0 + 1
sys._flh_live = None
# the stdio loop survives JSON lines that aren't request objects
lines = "5\ntrue\n[1, 2]\n\"x\"\n{bad json\n" + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n"
old_in, old_out = sys.stdin, sys.stdout
sys.stdin, sys.stdout = io.StringIO(lines), io.StringIO()
try:
    m.main()
    got = sys.stdout.getvalue()
finally:
    sys.stdin, sys.stdout = old_in, old_out
assert json.loads(got.strip().splitlines()[-1]) == {"jsonrpc": "2.0", "id": 9, "result": {}}, got
print("dispatcher ok")

# ---- snapshot alt text, schemas, bridge reply parsing / framing
alt = m._snapshot_alt("Song - FL Studio 2026", [1400, 800], "mixer", "Mixer - Master",
                      [{"title": "Confirm", "text": ["Save changes to Song?"], "buttons": ["Yes", "No"]}])
assert "Song - FL Studio 2026" in alt and "mixer" in alt and "Confirm" in alt and "Yes, No" in alt, alt
assert "no popups" in m._snapshot_alt("x", None)
bad = []


def walk(s, path):
    if isinstance(s, dict):
        if (s.get("type") == "array" or (isinstance(s.get("type"), list) and "array" in s["type"])) and "items" not in s:
            bad.append(path)
        for k, v in s.items():
            walk(v, path + "." + k)
    elif isinstance(s, list):
        for v in s:
            walk(v, path)


for n, ent in m.TOOLS.items():
    walk(ent[2], n)
assert not bad, bad
assert m._Bridge._reply("OK{'name': 'Pad inf', 'v': -inf, 'x': nan}") == {"name": "Pad inf", "v": -math.inf, "x": None}
F = m.FRAME
assert m._parse_reply_frame(F + bytes([1, 5, 0, 2, 3]) + b"abc\xF7", 5) == (2, 3, b"abc")
assert m._parse_reply_frame(F + bytes([3, 5, 0, 0x10, 0x01, 0x30, 0x02]) + b"q\xF7", 5) == (0x90, 0x130, b"q")
assert m._parse_reply_frame(F + bytes([1, 6, 0, 0, 1]) + b"\xF7", 5) is None
print("alt / schemas / bridge parsing ok")

# ---- M4: two different samples with one name stay different; M11: renders never overwrite a user's file
smp = os.path.join(R, "smp"); os.makedirs(os.path.join(smp, "a")); os.makedirs(os.path.join(smp, "b"))
dest = os.path.join(R, "Samples"); os.makedirs(dest)
ka, kb = os.path.join(smp, "a", "kick.wav"), os.path.join(smp, "b", "kick.wav")
open(ka, "wb").write(b"900hz"); open(kb, "wb").write(b"50hz")
shutil.copy(ka, os.path.join(dest, "kick.wav"))
assert m._collect_target(dest, ka) == os.path.join(dest, "kick.wav")
assert m._collect_target(dest, kb) == os.path.join(dest, "kick (2).wav")
mine = os.path.join(R, "Song.wav"); open(mine, "wb").write(b"user's own bounce")
d1 = m._render_dest(R, "Song", ".wav")
assert d1 == os.path.join(R, "Song (render).wav"), d1
open(d1, "wb").write(b"r"); m._note_render(d1)
assert m._render_dest(R, "Song", ".wav") == d1                  # our own earlier render: overwritten (no pile-up)
print("samples / renders ok")
print("ok")
