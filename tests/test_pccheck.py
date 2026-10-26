"""fl_pc_check (flh_pccheck): the runner and the core checks against the fake FL of tests/test_e2e.py.

Covers: dry run; a full core run (every core check passes or is a 'look' warning) with a stub flh_pccheck_mod; the
user's song untouched and reopened, the sandbox recycled, report files written; the needs_you / answer flow; a run
without the module file; failure paths: FL closing mid-run, a check over its deadline, a check raising, an unsaved new
project at the start, stop=true, an interrupted earlier run recovered. Run: python3 tests/test_pccheck.py"""
import base64, json, os, shutil, struct, sys, tempfile, threading, time, types, zlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_e2e as E  # noqa: E402  (sets FLH_ROOTS / LOCALAPPDATA / the fake FL install env, imports the server)

m, ns = E.m, E.ns
import flh_pccheck as pc  # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg); print("FAIL", msg)
    return cond


def tiny_png():
    raw = b"\x00\xff\x00\x00"
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
                            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")).decode()


# ============================================================================ a fake FL that knows a bit more
class PCFakeFL(E.FakeFL):
    """The e2e fake FL plus what the PC check drives: tempo (live + while playing), the script location, piano-roll
    live notes with undo, FL's own export, untitled projects, Ctrl+N."""
    def __init__(self):
        super().__init__()
        self.history = []; self.untitled_modified = False; self.ctrl_n = 0; self.die_on = None

    def _bar(self):
        return self.b0 + (time.time() - self.t_play) * 0.2 if self.playing else self.bar

    def _held_tempo_at(self, bar):
        tmp = os.path.join(E.SYSTEMP, "held_tempo.flp")
        with open(tmp, "wb") as f:
            f.write(self.held)
        mp = m.flp_tempo_map(tmp)["map"]
        os.remove(tmp)
        cur = mp[0]["bpm"] if mp else 90.0
        for p in mp:
            if p["bar"] <= bar + 1e-6:
                cur = p["bpm"]
        return cur

    def bridge(self, cmd, timeout=15.0, **a):
        if self.running and cmd == "ping":
            self.calls.append((cmd, a))
            return {"pong": True, "version": m._script_version_shipped()}
        if self.running and cmd == "where":
            return {"file": SCRIPT}
        if self.running and cmd == "commands":
            return ["ping", "state", "piano_roll", "transport"]
        if self.running and cmd == "piano_roll":
            return {"shown": True}
        if self.running and cmd == "set_tempo" and self.held is not None:
            d = self.held; ev = m._events(d)
            k = next(k for k, e in enumerate(ev) if e["id"] == m.EV_TEMPO)
            self.held = m._splice(d, ev, [(k, k + 1, bytes([m.EV_TEMPO]) + struct.pack("<I", int(a["bpm"] * 1000)))])
            return {"tempo": a["bpm"]}
        if self.running and cmd == "transport" and a.get("action") in ("undo", "redo"):
            if a["action"] == "undo" and self.history:
                self.held = self.history.pop()
            return {"done": a["action"]}
        r = super().bridge(cmd, timeout=timeout, **a)
        if cmd == "state" and self.held is not None:
            r["tempo"] = self._held_tempo_at(self._bar())
        return r

    def fl_open(self, project):
        if self.untitled_modified:
            raise RuntimeError("FL has an unsaved new project open: save it or close it in FL first (fake)")
        self.title_override = None
        return super().fl_open(project)

    def live_notes(self, channel, pattern, notes, expect, ppq, timeout_s=6.0):
        d = self.held; ev = m._events(d); pats = m._patterns(d, ev)
        keep = [r for r in pats.get(pattern, {}).get("notes", []) if r[2] != channel]
        recs = [m._rec_to_note(r) for r in keep] + [m._rec_to_note(r) for r in notes]
        self.history.append(d)
        self.held = m._write_pattern_notes(d, ev, pattern, recs)
        return {"target": {"channel": channel, "pattern": pattern}, "receipt": "done", "steps_s": {}}

    def render_in_fl(self, project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None):
        if probe == "save_dialog":
            return {"probe": probe, "seen": {"save_dialog": {"title": "Save As", "file_name": "x"}, "cancelled": True},
                    "steps_s": {"save_dialog": 0.1}, "rendered": []}
        if probe == "export_dialog":
            return {"probe": probe, "seen": {"export_dialog": {"title": "Rendering", "class": "TExportForm"},
                                             "cancelled": True, "file_written": False}, "steps_s": {},
                    "rendered": [], "_image_png": tiny_png()}
        secs = pc._song_seconds(project)
        os.makedirs(out_dir, exist_ok=True)
        dst = os.path.join(out_dir, (out_stem or os.path.splitext(os.path.basename(project))[0]) + ".wav")
        E.wav_write(dst, E.song_audio(max(1.0, secs) + 1.0, seed=3) * 0.8)
        m._note_in_fl(True)
        return {"rendered": [dst], "seconds": 0.2, "method": "in_fl", "steps_s": {"render": 0.2}}

    def install(self):
        super().install()
        for k in ("_live_notes", "_render_in_fl", "_fl_foreground", "_send_keys"):
            self.orig[k] = ns[k]
        self.orig_tools = {k: m.TOOLS[k] for k in ("fl_snapshot", "files_recycle")}
        ns["_live_notes"] = self.live_notes
        ns["_render_in_fl"] = self.render_in_fl
        ns["_fl_foreground"] = lambda: True

        def send_keys(vk, mods=()):
            if vk == 0x4E:                                       # Ctrl+N: a new empty project
                self.ctrl_n += 1; self.open = None; self.held = None
                self.title_override = "Project 7 - FL Studio 2026"
        ns["_send_keys"] = send_keys
        fl = self

        def snap(area="window", region=None, max_width=1400, save=False, hwnd=None):
            if fl.die_on == "snapshot":
                fl.running = False
            return {"window": fl.title(), "size": [1, 1], "_image_png": tiny_png()}

        def recycle(paths):
            return fl.recycle(paths)
        m.TOOLS["fl_snapshot"] = (snap,) + tuple(m.TOOLS["fl_snapshot"][1:])
        m.TOOLS["files_recycle"] = (recycle,) + tuple(m.TOOLS["files_recycle"][1:])
        return self

    def uninstall(self):
        m.TOOLS.update(self.orig_tools)
        super().uninstall()


DOCS = E.DOCS
SCRIPT = os.path.join(DOCS, "Settings", "Hardware", "fl-headless", "device_flheadless.py")


def setup_install():
    E.build_fl_install()
    os.makedirs(os.path.dirname(SCRIPT), exist_ok=True)
    shutil.copy(os.path.join(E.REPO, "fl_script", "device_flheadless.py"), SCRIPT)
    os.makedirs(os.path.join(DOCS, "Settings", "Piano roll scripts"), exist_ok=True)


def user_song():
    d = os.path.join(E.ROOTS, "My Songs", "Groove"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "Groove.flp")
    if not os.path.isfile(p):
        shutil.copy(E.FIX, p)
    return p


# ============================================================================ stub module checks
EXTRA_CHECKS = []                                                 # added to every stub module (a test's own checks)


def stub_mod(kind="ok"):
    mod = types.ModuleType("flh_pccheck_stub")

    def ok_check(ctx):
        p = ctx.make_project("Stub Song", "small")
        ctx.open_in_fl(p)
        info = ctx.call("flp_info", path=p)
        snap = ctx.snapshot("stub")
        flag = []
        ctx.cleanup(lambda: flag.append(1))
        return {"status": "pass", "detail": f"tempo {info.get('tempo_bpm')}", "evidence": {"snap": snap},
                "checklist": "checklists/newsong.md#1"}

    def boom(ctx):
        raise ValueError("stub check blew up")

    def slow(ctx):
        while True:                                              # cooperative: ctx.say raises at the deadline
            ctx.say("busy"); time.sleep(0.2)

    def inner_deadline(ctx):
        with ctx.deadline(0.5):
            time.sleep(1.2)
        return {"status": "pass", "detail": "never"}

    def kill_fl(ctx):
        PFL.running = False
        return {"status": "pass", "detail": "FL was fine when I left"}

    def checks(ns_):
        base = [{"id": "mix.stub_ok", "title": "stub ok", "area": "mix", "risk": 2, "needs": {"fl", "bridge"},
                 "minutes": 0.2, "run": ok_check},
                {"id": "review.stub_boom", "title": "stub raises", "area": "review", "risk": 1, "needs": set(),
                 "minutes": 0.1, "run": boom}]
        if kind == "slow":
            base.append({"id": "mix.stub_slow", "title": "stub hangs", "area": "mix", "risk": 5, "needs": set(),
                         "minutes": 0.01, "run": slow})
            base.append({"id": "mix.stub_inner", "title": "stub inner deadline", "area": "mix", "risk": 5,
                         "needs": set(), "minutes": 0.01, "run": inner_deadline})
        if kind == "kill":
            base.append({"id": "mix.stub_kill", "title": "FL closes", "area": "mix", "risk": 5, "needs": {"fl"},
                         "minutes": 0.1, "run": kill_fl})
        return base + list(EXTRA_CHECKS)
    mod.checks = checks
    return mod


def use_mod(kind):
    pc.MOD_NAME = "flh_pccheck_stub"
    if kind is None:
        sys.modules.pop("flh_pccheck_stub", None)
    else:
        sys.modules["flh_pccheck_stub"] = stub_mod(kind)


# ============================================================================ helpers
def answerer(stop_ev):
    """The person at the PC: answers needs_you prompts the way the hand steps would leave FL."""
    def run():
        while not stop_ev.is_set():
            ask = pc._RUN.get("ask")
            if ask:
                if "File > New" in ask and "discard" not in ask:
                    PFL.untitled_modified = True; PFL.title_override = "Project 5 - FL Studio 2026"
                elif "discard" in ask:
                    PFL.untitled_modified = False; PFL.title_override = None
                m._flh_call("fl_pc_check", {"answer": "done"})
            time.sleep(0.1)
    t = threading.Thread(target=run, daemon=True); t.start()
    return t


def run_check(**args):
    args.setdefault("full", True)                              # these tests cover every check
    r = m._flh_call("fl_pc_check", args)
    if "job" not in r:
        return r, None
    jid = r["job"]
    t0 = time.time()
    while time.time() - t0 < 600:
        j = m.fl_job(jid, wait_s=5)
        if j.get("status") != "running":
            break
    check(j.get("status") == "done", f"job ended {j.get('status')}: {j.get('error')}")
    return r, j.get("result") or {}


def load_report(res):
    with open(res["report"], encoding="utf-8") as f:
        return json.load(f)


# ============================================================================ the tests
def t_dry_run():
    use_mod("ok")
    r = m._flh_call("fl_pc_check", {"dry_run": True, "full": True})
    ids = [c["id"] for c in r["checks"]]
    check(ids[:2] == ["core.userdata", "bridge.ping"], f"preflight first: {ids[:3]}")
    check("render.in_fl" in ids and "mix.stub_ok" in ids, f"core + module checks planned: {ids}")
    risks = [c["risk"] for c in r["checks"][2:] if not c.get("you")]
    check(risks == sorted(risks, reverse=True), f"riskiest first: {risks}")
    check(r["checks"][-1]["id"] == "core.untitled_refusal", f"checks needing the person last: {ids[-3:]}")
    check(r["estimated_minutes"]["automatic"] > 5 and r["you_will"], f"estimates + what you do: {r}")
    r = m._flh_call("fl_pc_check", {"dry_run": True, "areas": ["render"], "max_risk": 4})
    ids = [c["id"] for c in r["checks"]]
    check(ids == ["core.userdata", "bridge.ping", "render.cli_refusal"], f"areas + max_risk filter: {ids}")
    r = m._flh_call("fl_pc_check", {"dry_run": True, "skip": "files,lazy,mix,review", "unattended": True, "full": True})
    check(not [c for c in r["checks"] if c["id"].startswith(("files.", "lazy.", "mix."))], "skip works")


def t_quick_plan():
    q = m._flh_call("fl_pc_check", {"dry_run": True})
    f = m._flh_call("fl_pc_check", {"dry_run": True, "full": True})
    assert 0 < q["count"] < f["count"], (q["count"], f["count"])
    assert all(c["risk"] >= 3 or c["id"] in ("core.userdata", "bridge.ping") for c in q["checks"]), q["checks"]
    assert q["estimated_minutes"]["total"] <= 30, q["estimated_minutes"]


def t_full_run():
    use_mod("ok")
    song = user_song()
    PFL.fl_open(song)
    before = open(song, "rb").read()
    rb_before = len(os.listdir(os.path.join(E.TMP, "RecycleBin"))) if os.path.isdir(os.path.join(E.TMP, "RecycleBin")) else 0
    stop = threading.Event(); answerer(stop)
    seen_progress = []

    def watch():
        while not stop.is_set():
            jid = pc._RUN.get("job")
            p = (m.JOBS.get(jid) or {}).get("progress") if jid else None
            if p and p.get("step"):
                seen_progress.append(p["step"])
            time.sleep(0.2)
    threading.Thread(target=watch, daemon=True).start()
    try:
        r, res = run_check()
    finally:
        stop.set()
    check(r.get("estimated_minutes"), f"start answer has estimates: {r}")
    check(any(s.startswith("check ") and "/" in s for s in seen_progress), f"fl_job shows 'check i/n': "
          f"{seen_progress[:3]}")
    rep = load_report(res)
    by = {c["id"]: c for c in rep["checks"]}
    core = [c for c in rep["checks"] if c["area"] in pc.AREAS_CORE]
    for c in core:
        ok = c["status"] == "pass" or (c["status"] == "warn" and c.get("look"))
        check(ok, f"core check {c['id']}: {c['status']} {c.get('detail')} {c.get('traceback', '')[-600:]}")
    check(len(core) == len(pc.core_checks()), f"every core check ran: {len(core)}")
    check(by.get("mix.stub_ok", {}).get("status") == "pass", f"module check ran: {by.get('mix.stub_ok')}")
    boom = by.get("review.stub_boom", {})
    check(boom.get("status") == "fail" and "blew up" in boom.get("detail", "") and boom.get("traceback"),
          f"a raising check is a fail with traceback: {boom}")
    check(res.get("bug_report") and os.path.isfile(res["bug_report"]), f"bug bundle on failure: {res.get('bug_report')}")
    check(os.path.isfile(res["report_md"]) and "| core.identity_save |" in open(res["report_md"]).read(), "report.md")
    check(res["summary"]["fail"] == 1, f"summary counts: {res['summary']}")
    check(rep["restored"].get("ok") and PFL.open == song, f"the song is back in FL: {rep['restored']} {PFL.open}")
    check(open(song, "rb").read() == before, "the user's song is byte-identical")
    check(rep.get("sandbox_recycled") and not os.path.exists(rep["sandbox"]), f"sandbox recycled: {rep.get('notes')}")
    check(len(os.listdir(os.path.join(E.TMP, "RecycleBin"))) > rb_before, "sandbox went to the (fake) Recycle Bin")
    check(not ns["_state"]().get("pc_check_run"), "run marker cleared")
    check(ns["_state"]().get("reload_mode") in (None, "auto"), f"reload mode restored: {ns['_state']().get('reload_mode')}")
    check(not m._pending_all() or not any(v.get("ops") for v in m._pending_all().values()), "nothing left queued")
    check(m.fl_status().get("live_notes") is True, "live notes enabled after the probe passed")
    check(by["live_notes.probe"]["evidence"].get("undo_one_step", "").startswith("yes"), "undo one step measured")
    check(by["lazy.big_project"]["evidence"]["channels"] >= 150, "big project is big")
    lz = by["lazy.big_project"]["evidence"]
    check(lz.get("routes", {}).get("rename+icon") == "queued" and lz.get("sync", {}).get("ops") ==
          list(lz["routes"].values()).count("queued"), f"lazy: the file-only edit queued, one sync took the queue: {lz}")
    snaps = [f for f in os.listdir(os.path.dirname(res["report"])) if f.endswith(".png")]
    check(snaps, "snapshots saved as evidence next to the report")
    check(by["core.untitled_refusal"]["status"] == "pass", f"needs_you flow: {by['core.untitled_refusal']}")
    for c in rep["checks"]:
        check(c.get("seconds") is not None and c.get("detail"), f"{c['id']}: seconds + detail")
    st = m._flh_call("fl_pc_check", {"status": True})
    check(st.get("running") is False and st.get("last_report") == res["report"], f"status shows the last report: {st}")
    print(f"  full run: {res['summary']} in {res['minutes']} min; passed {len(res['passed'])}")
    return res


def t_without_module():
    use_mod(None)
    song = user_song(); PFL.fl_open(song)
    r, res = run_check(areas=["files.backups", "files.recycle"], unattended=True)
    rep = load_report(res)
    ids = [c["id"] for c in rep["checks"]]
    check(ids == ["core.userdata", "bridge.ping", "files.recycle", "files.backups"], f"core only: {ids}")
    check(any("isn't installed" in n for n in rep["notes"]), f"says the module file is missing: {rep['notes']}")
    check(res["summary"]["fail"] == 0, f"no failures: {res.get('not_passed')}")
    check(rep["restored"].get("ok"), "restored (FL kept the song)")


def t_timeouts():
    use_mod("slow")
    old = pc.MIN_DEADLINE_S; pc.MIN_DEADLINE_S = 1.5
    try:
        song = user_song(); PFL.fl_open(song)
        r, res = run_check(areas=["mix.stub_slow", "mix.stub_inner", "files.recycle"], unattended=True)
    finally:
        pc.MIN_DEADLINE_S = old
    rep = load_report(res); by = {c["id"]: c for c in rep["checks"]}
    check(by["mix.stub_slow"]["status"] == "fail" and "timed out" in by["mix.stub_slow"]["detail"],
          f"hung check times out: {by['mix.stub_slow']}")
    check(by["mix.stub_inner"]["status"] == "fail" and "timed out" in by["mix.stub_inner"]["detail"],
          f"ctx.deadline over: {by['mix.stub_inner']}")
    check(by["files.recycle"]["status"] == "pass", "the run went on after the timeouts")


def t_fl_closes():
    use_mod("kill")
    song = user_song(); PFL.fl_open(song)
    try:
        r, res = run_check(areas=["mix", "files.recycle"], unattended=True)
    finally:
        PFL.running = True; PFL.fl_open(song)
    rep = load_report(res); by = {c["id"]: c for c in rep["checks"]}
    check(by["mix.stub_kill"]["status"] == "fail" and "FL closed" in by["mix.stub_kill"]["detail"],
          f"FL closing is a failure: {by['mix.stub_kill']}")
    check(by["mix.stub_ok"]["status"] == "skip", f"FL checks after it are skipped: {by['mix.stub_ok']}")
    check("FL" in res.get("stopped", "") and "Start FL again" in res["stopped"], f"tells the user: {res.get('stopped')}")
    check(rep["restored"].get("ok") is False, "no restore attempted without FL")
    check(not os.path.exists(rep["sandbox"]), "sandbox still cleaned (files only)")


def t_untitled_start():
    use_mod(None)
    PFL.open = None; PFL.held = None
    PFL.title_override = "*Project 3 - FL Studio 2026"
    r = m._flh_call("fl_pc_check", {})
    check(r.get("started") is False and "unsaved" in r.get("you_first", ""), f"untitled + modified: ask to save: {r}")
    check(not pc._RUN["running"], "no job started")
    PFL.title_override = "Project 3 - FL Studio 2026"            # untitled, unchanged: runs, FL gets a new one back
    n0 = PFL.ctrl_n
    r, res = run_check(areas=["files.slide_flag"], unattended=True)
    rep = load_report(res)
    check(rep["your_song"].startswith("a new empty project"), f"recorded: {rep['your_song']}")
    check(rep["restored"].get("ok") and PFL.ctrl_n == n0 + 1, f"FL back on a new project: {rep['restored']}")
    check({c["id"]: c["status"] for c in rep["checks"]}.get("files.slide_flag") in ("pass", "warn"), "check ran")
    PFL.untitled_modified = True; PFL.title_override = "Project 3 - FL Studio 2026"   # FL 2026: no '*' shown
    try:
        r, res = run_check(areas=["files.slide_flag"], unattended=True)
    finally:
        PFL.untitled_modified = False
    check("unsaved" in (res.get("stopped") or ""), f"an unsaved new project found at the first open stops the run: "
          f"{res.get('stopped')}")
    PFL.fl_open(user_song())


def t_stop():
    use_mod("ok")
    song = user_song(); PFL.fl_open(song)
    r = m._flh_call("fl_pc_check", {"areas": ["core", "files"], "unattended": True})
    jid = r["job"]
    t0 = time.time()
    while time.time() - t0 < 60:
        p = (m.JOBS.get(jid) or {}).get("progress") or {}
        if p.get("check") and p["check"] not in ("core.userdata", "bridge.ping"):
            break
        time.sleep(0.05)
    s = m._flh_call("fl_pc_check", {"stop": True})
    check(s.get("stopping") == jid, f"stop answers: {s}")
    j = m.fl_job(jid, wait_s=300)
    res = j.get("result") or {}
    rep = load_report(res)
    check(any(c["status"] == "skip" and "stopped" in c.get("detail", "") for c in rep["checks"]), "rest skipped")
    check(rep["restored"].get("ok") and PFL.open == song, "song reopened after stop")
    check(not os.path.exists(rep["sandbox"]), "sandbox cleaned after stop")


def t_recover():
    use_mod(None)
    song = user_song()
    sb = os.path.join(E.ROOTS, pc.SANDBOX_DIR, "20200101-000000"); os.makedirs(os.path.join(sb, "X"), exist_ok=True)
    left = os.path.join(sb, "X", "X.flp"); shutil.copy(E.FIX, left)
    PFL.fl_open(left)                                            # a cut-off run left FL on a sandbox song
    ns["_state"]({"pc_check_run": {"original": song, "sandbox": sb, "started": "earlier"}})
    r, res = run_check(areas=["files.backups"], unattended=True)
    rec = res.get("recovered_previous_run") or {}
    check(rec.get("reopened") == song and rec.get("sandbox_recycled") and not os.path.exists(sb),
          f"interrupted run recovered: {rec}")
    check(PFL.open == song, f"FL back on the song: {PFL.open}")


def t_lock():
    """While the check runs, other FL tools are refused as busy; looking at FL still works."""
    use_mod("slow")
    old = pc.MIN_DEADLINE_S; pc.MIN_DEADLINE_S = 3
    try:
        song = user_song(); PFL.fl_open(song)
        r = m._flh_call("fl_pc_check", {"areas": ["mix.stub_slow"], "unattended": True})
        time.sleep(1.0)
        t0 = time.time(); busy = None
        try:
            m._flh_call("fl_transport", {"action": "play"})
        except Exception as e:
            busy = str(e)
        check(busy and "busy" in busy and time.time() - t0 < 5, f"FL tools refused while the check runs: {busy}")
        st = m._flh_call("fl_pc_check", {"status": True})
        check(st.get("running") and st.get("progress"), f"status while running: {st}")
        again = m._flh_call("fl_pc_check", {})
        check(again.get("already_running") == r["job"], f"no second run: {again}")
        m.fl_job(r["job"], wait_s=60)
    finally:
        pc.MIN_DEADLINE_S = old


def t_load_error():
    """FL's 'error while reading the FLP' popup during a test song's load: fails fast, presses OK, stops the run."""
    real_d, real_fd = pc._dialogs, pc.H.get("fl_dialog")
    pressed = []
    pc._dialogs = lambda: [{"hwnd": 77, "title": "Error", "class": "TMsgForm", "text": ["Error"], "buttons": []}]
    pc.H["fl_dialog"] = lambda action, button=None, hwnd=None: pressed.append((action, button, hwnd))
    try:
        t0 = time.time()
        try:
            pc._wait_loaded(os.path.join(E.TMP, "Broken.flp"), 30)
            check(False, "a load error popup must raise FLLoadFailed")
        except pc.FLLoadFailed as e:
            check("Broken.flp" in str(e) and time.time() - t0 < 5, f"fast, names the file: {e}")
        check(pressed == [("click", "enter", 77)], f"the OK-only popup is closed: {pressed}")
        pc._dialogs = lambda: [{"hwnd": 78, "title": "Save As", "class": "#32770", "text": ["File &name:"]}]
        check(pc._load_error_popup() is None, "other dialogs aren't load errors")
    finally:
        pc._dialogs = real_d
        if real_fd is not None:
            pc.H["fl_dialog"] = real_fd


def t_lazy_verdict():
    """lazy.big_project: live edits are fine (PC 2026-10-23 evidence: tempo/notes/rename all went in live), the
    queued ones must go in with one sync; an edit neither queued nor live, or a lost hand edit, fails."""
    ev = {"channels": 150, "load_s": 15.7, "tempo": 127.0, "keys": [60], "sync": {}}
    v = lambda routes, ent, untouched=False, mname="FLH Hand Edit", renamed=True: pc._lazy_verdict(
        dict(ev), routes, untouched, ent, renamed, mname)
    r = v(["queued", "live", "live"], {"ops": 1})
    check(r["status"] == "pass" and "2 went in live" in r["detail"], f"queued + live passes: {r}")
    r = v(["queued"] * 3, {"ops": 3}, untouched=True)
    check(r["status"] == "pass", f"three queued, one sync: {r}")
    r = v(["queued"] * 3, {"ops": 3}, untouched=False)
    check(r["status"] == "fail" and "file changed" in r["detail"], f"all queued: the file must stay as it was: {r}")
    r = v(["queued", "live", "direct"], {"ops": 1})
    check(r["status"] == "fail" and "neither queued nor done live" in r["detail"], f"a reload-route edit fails: {r}")
    r = v(["queued", "queued", "live"], {})
    check(r["status"] == "fail" and "didn't apply the 2" in r["detail"], f"queued edits not synced fail: {r}")
    r = v(["queued", "live", "live"], {"ops": 1}, mname="Insert 2")
    check(r["status"] == "fail" and "hand edit" in r["detail"], f"a lost hand edit fails: {r}")
    check(pc._route({"queued": True}) == "queued" and pc._route({"live": True}) == "live" and
          pc._route({"live": False}) == "direct", "routes read from the results")


def t_restore_popup():
    """The restore: a Save As for the run's recorded take (Edison) is cancelled, then the song reopens; a file dialog
    that isn't a take (or was there before the run) is named and left alone."""
    keys = ("_fl_blocking_popup", "fl_dialog", "_flh_call", "_prove_open")
    real = {k: pc.H.get(k) for k in keys}
    real_wl, real_d = pc._wait_loaded, pc._dialogs
    take = {"hwnd": 91, "title": "Save As", "class": "#32770", "kind": "save_as", "file_dialog": True,
            "text": ["untitled_2.wav", "Edison", "Data", "Samples"]}
    showing = [take]; clicks = []; opened = []

    def click(action, how, hwnd=None):
        clicks.append((how, hwnd))
        if how == "cancel":
            showing[:] = [d for d in showing if d["hwnd"] != hwnd]
        return {}
    song = os.path.join(E.TMP, "My Restore Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song)
    ctx = types.SimpleNamespace(opened=True, snapshot=lambda *a: None, dialogs_at_start=set())
    try:
        pc.H["_fl_blocking_popup"] = lambda: showing[0] if showing else None
        pc.H["fl_dialog"] = click
        pc.H["_flh_call"] = lambda name, a: opened.append(a["project"]) or PFL.fl_open(a["project"])
        pc._wait_loaded = lambda *a, **k: True
        pc._dialogs = lambda: list(showing)
        r = pc._restore(ctx, {"original": song}, None)
        check(clicks == [("cancel", 91)] and opened == [song], f"take's Save As cancelled, then fl_open: {clicks} "
                                                                  f"{opened}")
        check(r.get("cancelled_popup") and "untitled_2.wav" in r["cancelled_popup"] and r.get("reopened") == song,
              f"says what it cancelled: {r}")
        showing[:] = [dict(take, text=["My Beat.flp"], hwnd=92)]; clicks.clear(); opened.clear()
        r = pc._restore(ctx, {"original": song}, None)
        check(not clicks and not opened and r["ok"] is False and "'Save As'" in r["why"] and "My Beat.flp" in r["note"],
              f"another Save As: named, not touched: {r} {clicks}")
        showing[:] = [dict(take, hwnd=93)]; ctx.dialogs_at_start = {93}
        r = pc._restore(ctx, {"original": song}, None)
        check(not clicks and r["ok"] is False, f"a take dialog from before the run isn't cancelled: {r}")
    finally:
        for k, v in real.items():
            if v is None:
                pc.H.pop(k, None)
            else:
                pc.H[k] = v
        pc._wait_loaded, pc._dialogs = real_wl, real_d


def t_restore_retry():
    """PC 2026-10-23: the restore's first fl_open didn't take (FL stayed on the sandbox song, e.g. Ctrl+O reached
    Edison's window); FL isn't busy, so the restore doesn't sit out the whole load wait: it clears popups and runs the
    same fl_open once more, which works. A restore that never takes says so (tries, title_now)."""
    keys = ("_fl_blocking_popup", "_flh_call", "_prove_open", "_main_title", "_fl_still_working", "_scaled")
    real = {k: pc.H.get(k) for k in keys}
    real_c = (pc._wait_loaded, pc._dialogs, pc.RESTORE_SWITCH_S, pc.RESTORE_SETTLE_S)
    song = os.path.join(E.TMP, "My Retry Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), song)
    TITLE = {"t": "check vocal - FL Studio 2026"}; opened = []; works_on = {"n": 2}

    def fake_open(name, a):
        opened.append(a["project"])
        if len(opened) >= works_on["n"]:
            TITLE["t"] = "My Retry Song - FL Studio 2026"
        return {"opened": a["project"]}

    def fake_wait(path, timeout_s, t0=None):
        if "My Retry Song" not in TITLE["t"]:
            raise TimeoutError("not loaded")
        return 0.1
    ctx = types.SimpleNamespace(opened=True, snapshot=lambda *a: None, dialogs_at_start=set())
    try:
        pc.H.update(_fl_blocking_popup=lambda: None, _flh_call=fake_open, _prove_open=lambda p: p,
                    _main_title=lambda: TITLE["t"], _fl_still_working=lambda: False, _scaled=lambda s, *a: float(s))
        pc._wait_loaded = fake_wait; pc._dialogs = lambda: []
        pc.RESTORE_SWITCH_S = 0.6; pc.RESTORE_SETTLE_S = 0.0
        t0 = time.time()
        r = pc._restore(ctx, {"original": song}, None)
        check(r["ok"] is True and opened == [song, song] and len(r.get("tries") or []) == 2, f"retried once: {r}")
        check("didn't take" in r["tries"][0].get("wait", "") and time.time() - t0 < 5, f"gave up early: {r}")
        TITLE["t"] = "check vocal - FL Studio 2026"; opened.clear(); works_on["n"] = 99
        r = pc._restore(ctx, {"original": song}, None)
        check(r["ok"] is False and len(r["tries"]) == 2 and "check vocal" in r["title_now"], f"never took: {r}")
    finally:
        for k, v in real.items():
            if v is None:
                pc.H.pop(k, None)
            else:
                pc.H[k] = v
        pc._wait_loaded, pc._dialogs, pc.RESTORE_SWITCH_S, pc.RESTORE_SETTLE_S = real_c


@__import__("contextlib").contextmanager
def patched(**kw):
    real = {k: ns.get(k) for k in kw}
    ns.update(kw)
    try:
        yield
    finally:
        for k, v in real.items():
            if v is None:
                ns.pop(k, None)
            else:
                ns[k] = v


def t_empty_start_template():
    """PC 2026-10-23: the run started on FL's empty unsaved project ('FL Studio 2026') and File > New didn't take at
    the end, so FL stayed on a sandbox song and the sandbox (held by FL) wasn't recycled. Now: FL's empty template is
    opened instead, the sandbox goes once FL has left it; the next run sees the template as an empty project."""
    use_mod("ok")
    PFL.open = None; PFL.held = None; PFL.title_override = "FL Studio 2026"
    blank = os.path.abspath(os.environ["FLH_BLANK"])

    def held_refuses(paths):                                     # Windows: a folder FL holds a file of won't go
        for p in ([paths] if isinstance(paths, str) else paths):
            if PFL.open and pc._inside(PFL.open, p):
                raise RuntimeError(f"could not recycle {p} (shell code 32, fake: FL holds a file in it)")
        return PFL.recycle(paths)
    n0 = PFL.ctrl_n
    with patched(_fl_foreground=lambda: False, files_recycle=held_refuses):   # File > New can't reach FL
        r, res = run_check(areas=["mix.stub_ok"], unattended=True)
    rep = load_report(res)
    check(rep["your_song"].startswith("a new empty project"), f"recorded: {rep['your_song']}")
    rs = rep["restored"]
    check(rs.get("ok") and rs.get("how") == "empty_template" and PFL.ctrl_n == n0, f"empty template route: {rs}")
    check(PFL.open == blank, f"FL is on its empty template, not a sandbox song: {PFL.open}")
    check(rep.get("sandbox_recycled") and not os.path.exists(rep["sandbox"]) and not rep.get("sandbox_not_recycled"),
          f"sandbox recycled once FL left it: {rep.get('sandbox_not_recycled')} {rep.get('notes')}")
    # the next run starts on that template: an empty project, not 'a song outside FLH_ROOTS'
    r, res = run_check(areas=["files.recycle"], unattended=True)
    rep = load_report(res)
    check(not res.get("stopped") and rep["your_song"].startswith("a new empty project"), f"template = empty project: "
          f"{res.get('stopped')} {rep.get('your_song')}")
    PFL.fl_open(user_song())


def t_leftover_sandbox_song():
    """An earlier run left FL on its sandbox song (the benches then took it for 'your song' and reopened it): it's
    never the user's song - FL gets an empty project at the end and that old sandbox is recycled once FL left it."""
    use_mod(None)
    old = os.path.join(E.ROOTS, pc.SANDBOX_DIR, "20200102-000000")
    os.makedirs(old, exist_ok=True)
    probe = os.path.join(old, "icon probe_2.flp"); shutil.copy(E.FIX, probe)
    PFL.fl_open(probe)
    ns["_state"]({"pc_check_run": None})                         # nothing recorded (as on the PC)
    n0 = PFL.ctrl_n
    r, res = run_check(areas=["files.recycle"], unattended=True)
    rep = load_report(res)
    check(rep["your_song"].startswith("a new empty project"), f"a sandbox song isn't the user's: {rep['your_song']}")
    check(rep["restored"].get("ok") and PFL.ctrl_n == n0 + 1 and PFL.open is None, f"FL on a new project: "
          f"{rep['restored']} {PFL.open}")
    check(not os.path.exists(old) and old in (rep.get("earlier_sandboxes") or {}).get("recycled", []),
          f"the old sandbox recycled after FL left it: {rep.get('earlier_sandboxes')}")
    check(any("earlier run's sandbox" in n for n in rep["notes"]), f"said: {rep['notes']}")
    check(pc.user_song(probe) is None and pc.user_song(user_song()) == user_song(), "user_song() refuses sandboxes")
    PFL.fl_open(user_song())


def t_cant_leave_sandbox():
    """FL can't be moved off the sandbox song (File > New and the template both fail): the restore isn't 'ok', the
    sandbox stays and the report says so with its path; the next run recycles it once FL has left it."""
    use_mod("ok")
    PFL.open = None; PFL.held = None; PFL.title_override = "FL Studio 2026"

    def no_open(p, on_save="no", discard_ok=None):               # the sandbox songs open; FL's template doesn't
        if os.path.abspath(p) == os.path.abspath(os.environ["FLH_BLANK"]):
            raise RuntimeError("couldn't drive FL's Open dialog (fake)")
        return PFL.fl_open(p)
    with patched(_fl_foreground=lambda: False, _open_running_or_fail=no_open):
        r, res = run_check(areas=["mix.stub_ok"], unattended=True)
    rep = load_report(res)
    sb = rep["sandbox"]
    check(rep["restored"].get("ok") is False and pc._inside(PFL.open, sb), f"not claimed: {rep['restored']}")
    nr = rep.get("sandbox_not_recycled") or {}
    check(nr.get("path") == sb and "FL still has" in nr.get("why", "") and os.path.isdir(sb), f"honest: {nr}")
    check(any(sb in s for s in res["next_steps"]), f"next steps name the path: {res['next_steps']}")
    check(sb in (ns["_state"]().get(pc.LEFTOVERS_STATE) or []), "remembered for the next run")
    PFL.fl_open(user_song())
    r, res = run_check(areas=["files.recycle"], unattended=True)
    check(sb in (res.get("earlier_sandboxes") or {}).get("recycled", []) and not os.path.exists(sb),
          f"recycled by the next run: {res.get('earlier_sandboxes')}")
    check(not ns["_state"]().get(pc.LEFTOVERS_STATE), "leftover list cleared")


def t_recycle_retry():
    """The sandbox recycle: once more when the folder is still there; then (False, why)."""
    d = os.path.join(E.ROOTS, pc.SANDBOX_DIR, "20200103-000000"); os.makedirs(os.path.join(d, "x"), exist_ok=True)
    open(os.path.join(d, "x", "a.wav"), "wb").write(b"RIFF")
    calls = []; real = ns["files_recycle"]
    old = pc.RECYCLE_RETRY_S; pc.RECYCLE_RETRY_S = 0.0
    try:
        with patched(files_recycle=lambda paths: calls.append(paths) or ({"recycled": []} if len(calls) == 1
                                                                          else real(paths))):
            ok, why = pc._recycle_sandbox(d)
        check(ok and why is None and not os.path.exists(d) and len(calls) >= 2, f"retried once: {ok} {why} {calls}")
        os.makedirs(d)
        with patched(files_recycle=lambda paths: {"recycled": []}):
            ok, why = pc._recycle_sandbox(d)
        check(not ok and "still there" in why and os.path.isdir(d), f"said when it can't: {ok} {why}")
    finally:
        pc.RECYCLE_RETRY_S = old
        shutil.rmtree(d, ignore_errors=True)


def t_sandbox_profile():
    """Item 2 (PC 2026-10-23: pc_check's 'check final' was filed as '<collection final folder>/check final/check final
    (Piano) - FINAL.flac'): during a run the active profile is the sandbox profile - final_root inside the sandbox, no
    final_name / taste of the user's collection, the user's edition kept; the collection comes back afterwards."""
    S = sys.modules["flh_store"]; FZ = sys.modules["flh_finalize"]
    album = os.path.join(E.ROOTS, "Piano Album Finals"); os.makedirs(album, exist_ok=True)
    prev = ns["_state"]().get("collection")
    S.profile_set("Piano Album", {"final_root": album, "final_name": "{song} (Piano) - FINAL.flac",
                                  "fl_edition": "producer", "style": "piano-led", "rules": {"min_ending_s": 40}})
    pf = S._profile_path("Piano Album"); before = open(pf, "rb").read()
    seen = {}

    def prof_check(ctx):
        p = ctx.make_project("check final", "blank")
        seen.update(prof=S.profile_get(), final=FZ._plan(p, None, None, None, S.profile_get())["final"],
                    sandbox=ctx.sandbox, active=ns["_state"]().get("collection"))
        return {"status": "pass", "detail": "profile seen"}
    EXTRA_CHECKS[:] = [{"id": "finalize.stub_profile", "title": "profile", "area": "finalize", "risk": 2,
                        "needs": set(), "minutes": 0.1, "run": prof_check}]
    use_mod("ok")
    try:
        PFL.fl_open(user_song())
        r, res = run_check(areas=["finalize.stub_profile"], unattended=True)
        fin, sbx, pr = seen.get("final") or "", seen.get("sandbox") or "?", seen.get("prof") or {}
        check(pc._inside(fin, sbx) and "(Piano)" not in os.path.basename(fin), f"final inside the sandbox: {fin}")
        check(seen.get("active") == pc.CHECK_PROFILE and pr.get("fl_edition") == "producer" and not pr.get("style")
              and not pr.get("final_name") and not pr.get("rules"), f"sandbox profile: {pr}")
        check(ns["_state"]().get("collection") == "Piano Album" and open(pf, "rb").read() == before,
              "the user's collection back, its file untouched")
        check(not os.path.exists(S._profile_path(pc.CHECK_PROFILE)) and not ns["_state"]().get(pc.PROFILE_STATE),
              "temporary profile removed")
        check(os.listdir(album) == [], f"nothing in the collection's final folder: {os.listdir(album)}")
        # a crash inside a run: the user's collection comes back (server start / next run)
        ns["_state"]({"collection": pc.CHECK_PROFILE, pc.PROFILE_STATE: {"collection": "Piano Album"}})
        open(S._profile_path(pc.CHECK_PROFILE), "w").write("{}")
        back = pc.collection_recover()
        check(back == {"collection": "Piano Album"} and ns["_state"]().get("collection") == "Piano Album" and
              not os.path.exists(S._profile_path(pc.CHECK_PROFILE)), f"recovered: {back}")
    finally:
        EXTRA_CHECKS[:] = []
        ns["_state"]({"collection": prev, pc.PROFILE_STATE: None})


PFL = PCFakeFL()

if __name__ == "__main__":
    setup_install()
    PFL.install()
    try:
        for t in (t_dry_run, t_quick_plan, t_full_run, t_without_module, t_timeouts, t_fl_closes, t_untitled_start, t_stop,
                  t_recover, t_lock, t_load_error, t_lazy_verdict, t_restore_popup, t_restore_retry,
                  t_empty_start_template, t_leftover_sandbox_song, t_cant_leave_sandbox, t_recycle_retry,
                  t_sandbox_profile):
            T = time.time()
            try:
                t()
            except Exception:
                import traceback
                FAILS.append(f"{t.__name__} crashed"); traceback.print_exc()
            print(f"{t.__name__}: {time.time() - T:.1f}s")
    finally:
        PFL.uninstall()
        shutil.rmtree(E.TMP, ignore_errors=True)
    if FAILS:
        print(f"{len(FAILS)} failure(s)"); sys.exit(1)
    print("ok")
