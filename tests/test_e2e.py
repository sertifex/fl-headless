"""End-to-end journeys through the MCP dispatcher, the way a Claude client drives fl-headless.

Everything goes through m.handle(): initialize -> tools/list -> tools/call (and flh_call / flh_run inside it). Two FL
variants: OFFLINE (FL not running, Linux: file and audio workflows; FL tools must refuse clearly) and FAKE FL (a fake
FL process + bridge: open project, save through the bridge, reload, listen, render produce synthetic data) so the
live paths run too. Each journey is one function; every result is JSON-checked (valid JSON, no NaN/numpy, size).

Known bugs are xfailed so the suite stays green: a failing check with a KNOWN id prints 'KNOWN BUG E2E-n'; a new
failure fails the run. FLH_NO_KNOWN=1 reports known bugs as failures (use after fixing). FLH_E2E_VERBOSE=1 prints
each step; FLH_E2E_DUMP=<file.jsonl> keeps every full result. `python3 tests/test_e2e.py j2 j6` runs only those
journeys. Run: python3 tests/test_e2e.py (no FL needed, about 2 min, most of it waiting)."""
import json, os, shutil, struct, sys, tempfile, time, traceback, types, wave, zipfile
ENV0 = dict(os.environ)                                           # as started (journey shards start from it)
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE); sys.path.insert(0, os.path.join(REPO, "src"))
T_START = time.time()
TMP = tempfile.mkdtemp(prefix="flh_e2e_")
ROOTS = os.path.join(TMP, "roots"); OUTSIDE = os.path.join(TMP, "outside"); LOCAL = os.path.join(TMP, "local")
HOME = os.path.join(TMP, "home")
DOCS = os.path.join(TMP, "Documents", "Image-Line", "FL Studio")
PACKS = os.path.join(TMP, "FL", "Data", "Patches", "Packs")
SYSTEMP = os.path.join(TMP, "systemp")                            # the OS temp folder, watched for leftovers
for d in (ROOTS, OUTSIDE, LOCAL, HOME, DOCS, PACKS, SYSTEMP):
    os.makedirs(d, exist_ok=True)
os.environ["TMPDIR"] = os.environ["TEMP"] = os.environ["TMP"] = SYSTEMP; tempfile.tempdir = SYSTEMP
os.environ.update({"FLH_ROOTS": ROOTS, "LOCALAPPDATA": LOCAL, "HOME": HOME, "USERPROFILE": HOME,
                   "FLH_FL_USERDATA": DOCS, "FLH_FACTORY_PACKS": PACKS,
                   "FLH_SPLICE_LIB": os.path.join(TMP, "Splice", "INSTRUMENT"), "FLH_FLEX_DIR": os.path.join(TMP, "FLEX")})
for k in ("FLH_PLUGIN_DB", "FLH_FL_EDITION", "FLH_TOOLSET", "FLH_BLANK"):
    os.environ.pop(k, None)
import numpy as np  # noqa: E402

FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
CORPUS = os.path.join(HERE, "corpus")
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)

import fl_headless_mcp as m  # noqa: E402
ns = vars(m)
VERBOSE = bool(os.environ.get("FLH_E2E_VERBOSE"))
NO_KNOWN = bool(os.environ.get("FLH_NO_KNOWN"))
DUMP = os.environ.get("FLH_E2E_DUMP")             # a .jsonl file: every call's full result (for review)

# ============================================================================ known bugs (ids from an internal write-up)
KNOWN = {
}
SEEN_KNOWN = set()
FAILS = []                      # (journey, step, message)
SIZES = []                      # (bytes, journey, step, tool)
JOURNEY = {"name": "setup"}
STATUS = {}                     # journey -> "pass" | "pass (known bugs)" | "FAIL"


def check(cond, msg, bug=None):
    """A check; bug='E2E-n' = a known bug (xfail). Returns cond."""
    if cond:
        return True
    if bug and bug in KNOWN and not NO_KNOWN:
        if bug not in SEEN_KNOWN:
            print(f"KNOWN BUG {bug}: {KNOWN[bug]}")
        SEEN_KNOWN.add(bug)
        return False
    FAILS.append((JOURNEY["name"], JOURNEY.get("step"), f"{msg}" + (f" [{bug}]" if bug else "")))
    print(f"FAIL [{JOURNEY['name']} / {JOURNEY.get('step')}] {msg}" + (f" [{bug}]" if bug else ""))
    return False


# ============================================================================ MCP client
_RID = [0]


def rpc(method, params=None):
    _RID[0] += 1
    req = {"jsonrpc": "2.0", "id": _RID[0], "method": method, "params": params or {}}
    res = m.handle(req)
    text = json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": res}, allow_nan=False)   # what main() sends
    return res, text


class Res:
    """One tools/call result as the client sees it."""
    def __init__(self, tool, args, raw, wire):
        self.tool, self.args, self.raw, self.wire = tool, args, raw, wire
        self.error = bool(raw.get("isError"))
        self.images = [c for c in raw.get("content", []) if c.get("type") == "image"]
        texts = [c["text"] for c in raw.get("content", []) if c.get("type") == "text"]
        self.text = texts[0] if texts else ""
        try:
            self.data = json.loads(self.text) if not self.error else None
        except ValueError:
            self.data = None
        self.bytes = len(self.text.encode("utf-8"))

    def get(self, *path, default=None):
        cur = self.data
        for p in path:
            if isinstance(cur, dict):
                cur = cur.get(p)
            elif isinstance(cur, list) and isinstance(p, int) and -len(cur) <= p < len(cur):
                cur = cur[p]
            else:
                return default
        return default if cur is None else cur

    def __repr__(self):
        return f"<{self.tool} {'ERROR ' if self.error else ''}{self.text[:300]}>"


def _has_bare_token(text):
    import re
    return bool(re.search(r"[:\[,](-?Infinity|NaN)[,\]}]", text))


def call(_tool, step=None, max_kb=40, expect_error=None, **args):
    tool = _tool
    """tools/call through the dispatcher; checks the wire format, JSON, NaN, size. expect_error: True/False/None."""
    JOURNEY["step"] = step or tool
    t0 = time.time()
    try:
        raw, wire = rpc("tools/call", {"name": tool, "arguments": args})
    except Exception as e:                                        # handle() must never raise for tools/call
        check(False, f"{tool}: handle() raised {type(e).__name__}: {e}\n{traceback.format_exc()}")
        r = Res(tool, args, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}, "")
        return r
    r = Res(tool, args, raw, wire)
    if tool != "fl_job" and _handed_over(r, tool):
        r = _await_handover(r, tool, args)
    took = time.time() - t0
    SIZES.append((r.bytes, JOURNEY["name"], JOURNEY["step"], tool))
    check(not _has_bare_token(r.text), f"{tool}: NaN/Infinity in the result text")
    if not r.error:
        check(r.data is not None, f"{tool}: result is not JSON: {r.text[:200]}")
        check(r.bytes <= max_kb * 1024, f"{tool}: result {r.bytes} bytes > {max_kb} KB")
    if expect_error is not None and r.error != expect_error:
        check(False, f"{tool}({_short(args)}) {'should fail' if expect_error else 'failed'}: {r.text[:600]}")
    if DUMP:
        with open(DUMP, "a", encoding="utf-8") as f:
            f.write(json.dumps({"journey": JOURNEY["name"], "step": JOURNEY["step"], "tool": tool, "args": args,
                                "error": r.error, "bytes": r.bytes, "s": round(took, 2), "text": r.text},
                               default=str) + "\n")
    if VERBOSE:
        print(f"  [{JOURNEY['name']}] {JOURNEY['step']}: {tool} {r.bytes}B {took:.2f}s "
              f"{'ERR ' if r.error else ''}{r.text[:400]}")
    return r


def _handed_over(r, tool):
    """A call that passed the dispatcher's soft deadline (FLH_CALL_SOFT_S, real seconds) comes back as
    {"job", "status": "running", "tool": <this tool>} - only on a loaded machine. Not a tool's own background job."""
    d = r.data
    return (not r.error and isinstance(d, dict) and d.get("tool") == tool and d.get("status") == "running"
            and str(d.get("job", "")).startswith(tool + "-") and "folder" not in d)


def _await_handover(r, tool, args, rounds=12):
    """Wait for a handed-over call the way a client does (fl_job), and give back its result as if the call had
    returned it, so a slow machine never fails a journey; the handover itself is printed (not hidden)."""
    jid = r.get("job"); t0 = time.time()
    for _ in range(rounds):                                       # fl_job returns the moment the job is done
        raw, wire = rpc("tools/call", {"name": "fl_job", "arguments": {"job": jid, "wait_s": 40}})
        j = Res("fl_job", {"job": jid}, raw, wire)
        if j.error or j.get("status") != "running":
            break
    print(f"  [{JOURNEY['name']}] {JOURNEY['step']}: {tool} passed the soft deadline under load -> job {jid}, "
          f"waited {time.time() - t0:.0f}s more ({j.get('status') if not j.error else 'fl_job error'})")
    if j.error:
        return j
    st = j.get("status")
    if st == "done":
        res = j.get("result")
        text = res if isinstance(res, str) else json.dumps(res)
        return Res(tool, args, {"content": [{"type": "text", "text": text}]}, wire)
    if st == "failed":
        return Res(tool, args, {"content": [{"type": "text", "text": str(j.get("error"))}], "isError": True}, wire)
    check(False, f"{tool}: handed-over job {jid} still {st} after {time.time() - t0:.0f}s: {j.text[:300]}")
    return r


def _short(a):
    s = json.dumps(a, default=str)
    return s if len(s) < 200 else s[:200] + "..."


def journey(fn):
    """Run one journey: a crash is a failure of that journey, the others still run."""
    name = fn.__name__
    JOURNEY.update(name=name, step=None)
    n0 = len(FAILS); k0 = set(SEEN_KNOWN); t0 = time.time(); sk0 = VCLOCK.skipped
    before = _tree_state()
    try:
        fn()
    except Exception as e:
        FAILS.append((name, JOURNEY.get("step"), f"crashed: {type(e).__name__}: {e}"))
        print(f"FAIL [{name} / {JOURNEY.get('step')}] crashed:\n{traceback.format_exc()}")
    JOURNEY["step"] = "filesystem"
    _check_writes(before)
    STATUS[name] = ("FAIL" if len(FAILS) > n0 else "pass (known bugs)" if SEEN_KNOWN - k0 else "pass")
    print(f"{name}: {STATUS[name]} ({time.time() - t0:.1f}s, {time.time() - t0 - VCLOCK.skipped + sk0:.1f}s wall)")


# ============================================================================ filesystem guard
ALLOWED_WRITE = [ROOTS, LOCAL, SYSTEMP, os.path.join(TMP, "RecycleBin")]   # + the fake Recycle Bin
TEST_WRITES = set()
TEMP_BUG = {}                                                     # journey -> known bug id for temp leftovers                                               # files the test itself puts outside the roots                                    # FLH_ROOTS + fl-headless's state folder


REPO_IGNORE = ("fl_headless_launch.py",)                         # written by install.py (tests/test_install.py)


def _tree_state():
    """The test's own temp tree, plus the repo (only NEW files there count: someone editing the repo while the suite
    runs, or another test, must not fail this one - L9)."""
    out = {}
    for base in (TMP, REPO):
        for dp, dn, fns in os.walk(base):
            dn[:] = [d for d in dn if d not in ("__pycache__", ".git", "review")]
            for f in fns:
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p); out[p] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
    return out


def _check_writes(before):
    after = _tree_state()
    bad = []
    for p, sig in after.items():
        if before.get(p) == sig or (p in before and not p.startswith(TMP + os.sep)):
            continue                                              # repo files: only new ones count
        if not p.startswith(TMP + os.sep) and (os.path.basename(p) in REPO_IGNORE or p.endswith((".tmp", ".pyc"))):
            continue
        if p == os.path.abspath(__file__) or p.startswith(os.path.join(REPO, "review")) or p in TEST_WRITES:
            continue
        if any(p.startswith(a + os.sep) for a in ALLOWED_WRITE):
            continue
        if p.startswith(os.path.join(DOCS, "Settings")):                # FL's settings folder (bridge script)
            continue
        bad.append(p)
    check(not bad, f"files written outside FLH_ROOTS / state: {bad[:6]}")
    left = sorted(p for p in after if p.startswith(SYSTEMP + os.sep) and p not in before)
    check(not left, f"temp files left behind in the OS temp folder: {len(left)}: {left[:4]}", bug=TEMP_BUG.get(
        JOURNEY["name"]))


# ============================================================================ a simulated FL install (no FL)
def raw_events(d, ev, a, b):
    return b"".join(m._raw(d, e) for e in ev[a:b])


def make_flp(events_bytes, nch=0, ppq=96):
    return b"FLhd" + struct.pack("<I", 6) + struct.pack("<hHH", 0, nch, ppq) + b"FLdt" + \
        struct.pack("<I", len(events_bytes)) + events_bytes


def make_blank(src, dst):
    """FL's empty template stand-in: the fixture without channels, notes, clips or remote links."""
    d = m._load(src); ev = m._events(d)
    keep = []; inside = False
    for e in ev:
        if e["id"] == 64:
            inside = True
        if not inside and e["id"] != 227:
            if e["id"] == m.EV_NOTES:
                keep.append(m._data_event(m.EV_NOTES, b""))
            elif e["id"] == m.EV_PLAYLIST:
                keep.append(m._data_event(m.EV_PLAYLIST, b""))
            else:
                keep.append(m._raw(d, e))
        if inside and e["id"] == 51:
            inside = False
    hl = struct.unpack("<I", d[4:8])[0]
    head = bytearray(d[:8 + hl]); fmt, _, ppq = struct.unpack("<hHH", head[8:14])
    head[8:14] = struct.pack("<hHH", fmt, 0, ppq)
    body = b"".join(keep)
    with open(dst, "wb") as f:
        f.write(bytes(head) + b"FLdt" + struct.pack("<I", len(body)) + body)


SR = 44100
RNG = np.random.default_rng(7)


def wav_write(path, x, sr=SR):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    x = np.asarray(x, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    y = (np.clip(x, -1, 1) * 32000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1]); w.setsampwidth(2); w.setframerate(sr); w.writeframes(y.tobytes())
    return vstamp(path)


def vstamp(path):
    """A file the fake FL writes gets the (virtual) clock's time as its mtime, like a real FL writing it 'now': the
    server compares mtimes with time.time() (a take is new when it's younger than the recording's start)."""
    v = globals().get("VCLOCK")
    if v is not None and v._off:
        t = v.time()
        os.utime(path, (t, t))
    return path


def _t(s, sr=SR):
    return np.arange(int(sr * s)) / sr


_SONG_BASE = {}                                                   # the last length's seed-independent parts


def song_audio(seconds=24.0, sr=SR, seed=0, loud=1.0):
    """A synthetic 'song': kick + bass + chords + hats, quiet first third, loud middle, medium end. Stereo."""
    g = np.random.default_rng(seed); n = int(seconds * sr)
    bpm = 90.0; beat = 60 / bpm
    key = (n, sr, int(seconds / beat))
    if _SONG_BASE.get("key") != key:                              # kick + bass + hat gate: the same for every seed
        t = np.arange(n) / sr
        y = np.zeros(n)
        for k in range(int(seconds / beat)):                          # kick on every beat
            a = int(k * beat * sr); tt = np.arange(min(int(0.3 * sr), n - a)) / sr
            y[a:a + len(tt)] += 0.8 * np.sin(2 * np.pi * (50 + 80 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 10)
        y += 0.25 * np.sin(2 * np.pi * 55 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * t / (4 * beat)))
        _SONG_BASE.update(key=key, t=t, y=y, gate=np.sin(2 * np.pi * t / (beat / 2)) > 0.6)
    t = _SONG_BASE["t"]; y = _SONG_BASE["y"].copy()
    for f in (220, 277.2, 329.6):
        y += 0.08 * np.sin(2 * np.pi * f * t + g.uniform(0, 6))
    hats = g.standard_normal(n) * 0.05 * _SONG_BASE["gate"]
    y += np.diff(np.concatenate([[0], hats]))
    env = np.interp(t, [0, seconds / 3, seconds / 3 + 0.5, 2 * seconds / 3, 2 * seconds / 3 + 0.5, seconds],
                    [0.3, 0.3, 1.0, 1.0, 0.6, 0.6])
    y = y * env * loud
    L = y + 0.02 * g.standard_normal(n); R = 0.9 * y + 0.02 * g.standard_normal(n)
    st = np.stack([L, R], 1)
    return st / np.abs(st).max() * 0.7


def hum(notes, sr=SR, seed=0, noise=0.01, step=0.4):
    """A sung/hummed melody: harmonic voice with vibrato, short gaps between notes."""
    g = np.random.default_rng(seed); out = []
    for midi in notes:
        f = 440 * 2 ** ((midi - 69) / 12); tt = _t(step * 0.85, sr)
        ph = 2 * np.pi * np.cumsum(f * (1 + 0.004 * np.sin(2 * np.pi * 5.5 * tt))) / sr
        x = sum(a * np.sin(k * ph) for k, a in ((1, 0.6), (2, 0.25), (3, 0.12), (4, 0.05)))
        a = np.minimum(1, np.minimum(tt / 0.03, (tt[-1] - tt) / 0.05 + 1e-9))
        out += [x * a * 0.5, np.zeros(int(step * 0.15 * sr))]
    y = np.concatenate(out)
    return y + noise * g.standard_normal(len(y))


def build_fl_install():
    """FL's empty template, plugin database, presets and factory samples, as an FL install would have them."""
    blank = os.path.join(TMP, "FL", "Empty.flp"); os.makedirs(os.path.dirname(blank), exist_ok=True)
    open(FAKE_EXE, "wb").write(b"MZ fake")
    make_blank(FIX, blank); os.environ["FLH_BLANK"] = blank
    fd = m._load(FIX); fev = m._events(fd)
    k201 = next(k for k, e in enumerate(fev) if e["id"] == 201)
    k213 = next(k for k in range(k201, len(fev)) if fev[k]["id"] == 213)
    gen_state = m._raw(fd, fev[k213]); gen_head = m._raw(fd, fev[k201 + 1])
    fx_k = [k for k, e in enumerate(fev) if e["id"] == 201 and m._text16(m._payload(fd, e)) == "Fruity Parametric EQ"][0]
    fx_end = next(k for k in range(fx_k, len(fev)) if fev[k]["id"] == 213)
    fx_body = raw_events(fd, fev, fx_k + 1, fx_end + 1)

    def fst(path, plugin, effect=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        name = m._data_event(201, plugin.encode("utf-16-le") + b"\x00\x00")
        with open(path, "wb") as f:
            f.write(make_flp(name + (fx_body if effect else gen_head + gen_state)))
    pdb = os.path.join(DOCS, "Presets", "Plugin database", "Installed")
    for p in ("FL Keys", "BooBass", "3x Osc", "Sytrus", "FLEX", "Fruity Kick", "Plucked!", "Fruity DX10"):
        fst(os.path.join(pdb, "Generators", "Fruity", p + ".fst"), p)
    for p in ("Fruity Parametric EQ 2", "Fruity Limiter", "Fruity Reeverb 2", "Fruity Delay 3", "Fruity Compressor",
              "Fruity Limiter"):
        fst(os.path.join(pdb, "Effects", "Fruity", p + ".fst"), p, effect=True)
    pp = os.path.join(DOCS, "Presets", "Plugin presets", "Generators")
    for sub, n in (("Keys", "Rhodes Classic"), ("Pads", "Warm Pad"), ("Leads", "Bell Lead")):
        fst(os.path.join(pp, "Sytrus", sub, n + ".fst"), "Sytrus")

    def kick(lp=None):
        tt = _t(0.35); f = 55 + 120 * np.exp(-tt * 40)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 9)

    def noise(dur, decay):
        return RNG.standard_normal(int(SR * dur)) * np.exp(-_t(dur) * decay) * 0.5
    wav_write(os.path.join(PACKS, "Drums", "Kicks", "Clean Kick 01.wav"), kick())
    wav_write(os.path.join(PACKS, "Drums", "Kicks", "Dusty Kick SP.wav"), kick() * 0.9)
    wav_write(os.path.join(PACKS, "Drums", "Snares", "Snare Crack.wav"), noise(0.25, 18))
    wav_write(os.path.join(PACKS, "Drums", "Claps", "Clap Big.wav"), noise(0.3, 14))
    wav_write(os.path.join(PACKS, "Drums", "Hats", "Hat Tight.wav"), noise(0.08, 60))
    wav_write(os.path.join(PACKS, "Drums", "Hats", "Open Hat 1.wav"), noise(0.6, 6))
    wav_write(os.path.join(PACKS, "Drums", "Percussion", "Shaker 2.wav"), noise(0.15, 30))
    wav_write(os.path.join(PACKS, "Drums", "808", "808 Long.wav"), 0.8 * np.sin(2 * np.pi * 46.25 * _t(2.0)))
    wav_write(os.path.join(PACKS, "FX", "Risers", "Riser White.wav"), noise(2.5, 0.2))
    return blank


# ============================================================================ the fake FL (variant 2)
class FakeFL:
    """One FL process with one open project. FL 'holds' the bytes it loaded; a save through the bridge writes them
    back (so an edit that FL didn't reload would be undone, like the real thing). Renders write synthetic WAVs."""
    def __init__(self):
        self.running = False; self.open = None; self.held = None; self.calls = []; self.playing = False
        self.renders = []; self.listens = []; self.reloads = []; self.saved = []
        self.armed = set(); self.names = {}; self.recording = False; self.takes = 0; self.bar = 1.0
        self.title_override = None; self.fail_render = False; self.silent = False; self.no_save = False
        self.rec_dir = None; self.b0 = 1.0; self.t_play = 0.0
        self.names = {0: "Master", 1: "Kick", 2: "Insert 2", 3: "Bass", 4: "Insert 4", 5: "Insert 5"}
        self.channels = [{"index": 0, "name": "Kick", "insert": 1}, {"index": 1, "name": "Bass", "insert": 3}]
        self.orig = {}

    def title(self):
        if self.title_override:
            return self.title_override
        return (os.path.splitext(os.path.basename(self.open))[0] if self.open else "Untitled") + " - FL Studio 2026"

    # ---- bridge
    def _bar(self):
        return self.b0 + (time.time() - self.t_play) * 8 if self.playing else self.bar     # 8 bars a second

    def bridge(self, cmd, timeout=15.0, **a):
        self.calls.append((cmd, a))
        if not self.running:
            raise RuntimeError("FL doesn't answer (fake: not running)")
        if cmd == "ping":
            return {"pong": True}
        if cmd == "transport":
            act = a.get("action")
            if act == "save" and self.open and self.held is not None and not self.no_save:
                with open(self.open, "wb") as f:
                    f.write(self.held)
                vstamp(self.open)
                self.saved.append(self.open)
            elif act == "play":
                self.playing, self.t_play, self.b0 = True, time.time(), self.bar
            elif act == "stop" and self.playing:
                self.bar = self._bar(); self.playing = False
                if self.recording and self.rec_dir:          # FL writes the take and adds an audio clip channel
                    self.takes += 1
                    wav_write(os.path.join(self.rec_dir, f"Insert 2_{self.takes:02d}.wav"),
                              hum([60, 62, 64, 65, 67, 65], seed=self.takes) * 0.5)
                    self.channels.append({"index": len(self.channels), "name": f"Insert 2_{self.takes:02d}",
                                          "insert": 2})
            return {"done": act}
        if cmd == "state":
            return {"playing": self.playing, "tempo": 90.0, "title": self.title(), "channels": self.channels,
                    "mixer": [{"index": t, "name": n, "muted": False, "effects": []} for t, n in self.names.items()]}
        if cmd == "song":
            act = a.get("action")
            if act == "goto":
                self.bar = a.get("bar", 1)
                if self.playing:
                    self.b0, self.t_play = self.bar, time.time()
            elif act == "record":
                self.recording = not self.recording
            return {"bar": round(self._bar(), 3), "recording": self.recording, "song_mode": True}
        if cmd == "mixer_set":
            t = a.get("track")
            if a.get("name") is not None:
                self.names[t] = a["name"]
            return {"track": t, "name": self.names.get(t, f"Insert {t}"), "color": a.get("color"), "volume_db": 0.0,
                    "routes_to": [0]}
        if cmd == "mixer_info":
            ts = self.names if a.get("tracks", "all") == "all" else {int(a["tracks"]): self.names.get(int(a["tracks"]))}
            return {"tracks": [{"index": t, "name": n, "armed": t in self.armed, "used": t in (1, 3), "effects": []}
                               for t, n in ts.items()]}
        if cmd == "mixer_arm":
            self.armed.add(a.get("track")); return {"track": a.get("track"), "armed": True, "recording_file": ""}
        if cmd == "mixer_peaks":
            return {"peaks": {int(a.get("tracks", 2)): [0.35, 0.3]}}
        if cmd == "rec_setup":
            return {"punch": [a.get("punch_in_bar"), a.get("punch_out_bar")]}
        if cmd == "channel_set":
            return {"index": a.get("index"), "name": a.get("name")}
        return {}

    def install(self):
        self.orig = {k: ns[k] for k in ("fl_running", "_fl_pids", "_fl_windows", "_fl_main_window", "fl_dialogs",
                                        "_open_project_guess", "_open_running_or_fail", "_reload_in_fl", "fl_close", "_render",
                                        "IS_WINDOWS", "_stop_autoplay_later", "_measure_load_later", "fl_exe",
                                        "subprocess", "files_recycle")
                     if k in ns}
        self.orig["BRIDGE.call"] = m.BRIDGE.call
        m.BRIDGE.call = self.bridge
        ns["IS_WINDOWS"] = True
        sys.modules.setdefault("winreg", _fake_winreg())
        ns["fl_running"] = lambda: self.running
        ns["_fl_pids"] = lambda: [4242] if self.running else []
        ns["_fl_windows"] = lambda: [(1, self.title())] if self.running else []
        ns["_fl_main_window"] = lambda: (1, self.title()) if self.running else None
        ns["fl_dialogs"] = lambda: []
        ns["_open_project_guess"] = lambda: ((self.open, [self.title()]) if self.running and self.open
                                             and not self.title_override else (None, [self.title()]))
        ns["_open_running_or_fail"] = lambda p, on_save="no", discard_ok=None: self.fl_open(p)
        ns["_reload_in_fl"] = self.reload
        ns["fl_close"] = self.fl_close
        ns["_render"] = self.render
        import fl_capture
        self.orig_capture = (fl_capture.available, fl_capture.LoopbackRecorder)
        fl = self

        class Recorder:                                       # the speakers: the song as FL plays it
            def start(self):
                self.t0 = time.time()

            def stop(self):
                sec = max(0.5, time.time() - self.t0)
                fl.listens.append(round(sec, 2))
                if fl.silent:
                    return SR, np.zeros((int(sec * SR), 2), np.float32)
                return SR, song_audio(sec, seed=len(fl.listens)).astype(np.float32)
        fl_capture.available = lambda: (True, "fake loopback")
        fl_capture.LoopbackRecorder = Recorder
        ns["fl_exe"] = lambda: FAKE_EXE
        ns["subprocess"] = _FakeSubprocess(self)
        ns["files_recycle"] = self.recycle
        ns["_stop_autoplay_later"] = lambda *a, **k: None
        ns["_measure_load_later"] = lambda *a, **k: None
        self.running = True
        return self

    def uninstall(self):
        import fl_capture
        fl_capture.available, fl_capture.LoopbackRecorder = self.orig_capture
        m.BRIDGE.call = self.orig.pop("BRIDGE.call")
        for k, v in self.orig.items():
            ns[k] = v
        self.running = False

    def recycle(self, paths):
        """The Windows Recycle Bin: moved into TMP/RecycleBin (only inside the writable roots / temp)."""
        paths = [paths] if isinstance(paths, str) else list(paths)
        bin_ = os.path.join(TMP, "RecycleBin"); os.makedirs(bin_, exist_ok=True)
        for p in paths:
            ns["writable"](p)
            shutil.move(p, os.path.join(bin_, f"{len(os.listdir(bin_))}_{os.path.basename(p)}"))
        self.recycled = getattr(self, "recycled", []) + paths
        return {"recycled": paths}

    def fl_open(self, project):
        if not os.path.isfile(project):
            raise FileNotFoundError(project)
        self.running = True; self.open = os.path.abspath(project)
        with open(project, "rb") as f:
            self.held = f.read()
        try:
            ns["_state"]({"last_opened": self.open})
        except Exception:
            pass
        return {"opened": self.open, "via": "fake FL"}

    def reload(self, project, **kw):                              # discard / force_blank: the same for the fake
        with open(project, "rb") as f:
            self.held = f.read()
        self.reloads.append(project)
        return {"load": 0.05}

    def fl_close(self, save=True, timeout_s=25):
        if save and self.open and self.held is not None:
            with open(self.open, "wb") as f:
                f.write(self.held)
            vstamp(self.open)
        was = self.open; self.running = False
        return {"closed": True, "project": was}

    def render(self, project, out_dir, formats, timeout_s, data=None, out_stem=None):
        self.renders.append(project)
        if self.fail_render:
            raise RuntimeError("FL's export stopped: render failed (fake)")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, (out_stem or os.path.splitext(os.path.basename(project))[0]) + ".wav")
        try:                                                      # FL exports the whole song (fl_finalize checks)
            secs = max(4.0, float(m._song_seconds(project)[0]) + 1.0)
        except Exception:
            secs = 20.0
        wav_write(out, song_audio(secs, seed=len(self.renders)) * 0.9)
        return {"rendered": [out], "seconds": 0.1}



def _fake_winreg():
    """A Windows registry with nothing of FL in it (the fake FL is found through the mocks, not the registry)."""
    import types
    w = types.ModuleType("winreg")
    for i, n in enumerate(("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE", "HKEY_CLASSES_ROOT", "HKEY_USERS")):
        setattr(w, n, 0x80000000 + i)
    w.KEY_READ, w.KEY_WRITE, w.KEY_WOW64_64KEY, w.KEY_WOW64_32KEY, w.KEY_ALL_ACCESS = 0x20019, 0x20006, 0x100, 0x200, 0xF003F
    w.REG_SZ, w.REG_DWORD, w.REG_BINARY, w.REG_EXPAND_SZ, w.REG_MULTI_SZ, w.REG_QWORD = 1, 4, 3, 2, 7, 11

    def nope(*a, **k):
        raise FileNotFoundError(2, "fake registry: no such key")
    for n in ("OpenKey", "OpenKeyEx", "QueryValueEx", "EnumValue", "EnumKey", "QueryInfoKey", "CreateKey",
              "CreateKeyEx", "SetValueEx", "DeleteValue", "DeleteKey", "ConnectRegistry", "QueryValue"):
        setattr(w, n, nope)
    w.CloseKey = lambda *a: None
    w.ExpandEnvironmentStrings = lambda s: os.path.expandvars(s)
    return w


FAKE_EXE = os.path.join(TMP, "FL", "FL64.exe")


class _FakeSubprocess:
    """subprocess for the fake Windows box: starting FL64.exe starts the fake FL; nothing else exists here."""
    def __init__(self, fl):
        import subprocess as real
        self.fl, self.real = fl, real
        for k in ("PIPE", "DEVNULL", "STDOUT", "TimeoutExpired", "CalledProcessError", "CompletedProcess"):
            setattr(self, k, getattr(real, k))

    def Popen(self, cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and cmd and os.path.basename(str(cmd[0])).lower() == "fl64.exe":
            if self.fl.running:
                raise AssertionError("a second FL was started")
            self.fl.running = True
            if len(cmd) > 1:
                self.fl.fl_open(cmd[1])
            return types.SimpleNamespace(pid=4242, poll=lambda: None, wait=lambda *a, **k: 0)
        raise FileNotFoundError(f"fake Windows: no program {cmd!r}")

    def run(self, cmd, *a, **k):
        raise FileNotFoundError(f"fake Windows: no program {cmd!r}")
    check_output = call = run


# ============================================================================ helpers
def flp_ok(path):
    """The file reparses with the server's own reader and flp_info works (through the dispatcher)."""
    try:
        d = m._load(path); m._events(d); m._check_complete(d)
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return None


def as_list(x):
    return x if isinstance(x, list) else [x] if x is not None else []


def walk_strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield str(k); yield from walk_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk_strings(v)


def has_ascii_art(text):
    """Box/bar drawings or piano-roll grids: lines of block characters or long runs of '#', '=', '|' patterns."""
    import re
    return bool(re.search(r"[█▇▆▅▄▃▂▁░▒▓]{4,}|(\|[ .#xX*o-]{8,}\|)|#{8,}|={10,}", text))


# ============================================================================ virtual waits (test speed only)
class VClock:
    """Stand-in for the `time` module of the server, its extensions and these tests. A sleep costs no wall time when
    every OTHER thread is idle (blocked indefinitely - a join, an Event / queue wait with no or a long timeout - or
    itself in a VClock sleep): the clock jumps instead, so the fake FL, which plays and records by this same clock,
    sees exactly the playback, pre-roll, tails and save waits it would see in real time. While another thread works
    (CPU, a short timed wait such as Event.wait(1.0), anything else) sleeps are real, in short slices, so nothing that
    waits for real work ever sees a deadline pass early. Every thread sees the same clock (one global offset).
    The checks are unchanged; only the wall time spent waiting goes. FLH_REAL_WAITS=1 turns it off."""
    LONG_WAIT_S = 5.0                     # a blocked wait at least this long is a deadline guard (join(45)), not a poll

    def __init__(self):
        import threading
        self._t, self._lock, self._off, self.skipped = __import__("time"), threading.Lock(), 0.0, 0.0
        self._sleep_code = VClock.sleep.__code__
        self.on = os.environ.get("FLH_REAL_WAITS") != "1"

    def __getattr__(self, k):                                     # strftime, localtime, struct_time ...: the real ones
        return getattr(self._t, k)

    def time(self):
        return self._t.time() + self._off

    def time_ns(self):
        return self._t.time_ns() + int(self._off * 1e9)

    def monotonic(self):
        return self._t.monotonic() + self._off

    def perf_counter(self):
        return self._t.perf_counter() + self._off

    def _others_idle(self):
        me = __import__("threading").get_ident()
        for ident, f in sys._current_frames().items():
            if ident == me:
                continue
            c = f.f_code
            if c is self._sleep_code:
                continue
            base = os.path.basename(c.co_filename)
            if base in ("threading.py", "queue.py") and c.co_name in ("wait", "join", "_wait_for_tstate_lock", "get",
                                                                       "acquire"):
                to = f.f_locals.get("timeout")
                if to is None or (isinstance(to, (int, float)) and (to < 0 or to >= self.LONG_WAIT_S)):
                    continue
            return False
        return True

    def sleep(self, s):
        s = float(s)
        if not self.on or s <= 0:
            return self._t.sleep(max(0.0, s))
        end = self.time() + s
        while True:
            left = end - self.time()
            if left <= 0:
                return None
            if self._others_idle():
                with self._lock:
                    self._off += left; self.skipped += left
                self._t.sleep(0)                                  # let the others run first
                return None
            self._t.sleep(min(left, 0.005))

    def install(self):
        """Be `time` in every loaded fl-headless module and test module (idempotent; call again after imports)."""
        real = self._t
        for name, mod in list(sys.modules.items()):
            if mod is None or getattr(mod, "time", None) is not real:
                continue
            f = getattr(mod, "__file__", None) or ""
            if os.path.abspath(f).startswith(REPO + os.sep):
                mod.time = self
        return self


VCLOCK = VClock()
if __name__ == "__main__":                 # run as the test: the virtual clock everywhere (this module = the fake
    time = VCLOCK.install()                # FL's clock too). Importers opt in with E.VCLOCK.install() (pc-check mod).

# ============================================================================ the journeys
BLANK = None
FL = FakeFL()


def j0_protocol():
    """initialize + tools/list: the essential tier is coherent and affordable."""
    JOURNEY["step"] = "initialize"
    init, wire = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "e2e"}})
    instr = init.get("instructions") or ""
    check(init.get("serverInfo", {}).get("name") and instr, "initialize: serverInfo + instructions")
    check(init.get("capabilities", {}).get("tools", {}).get("listChanged") is True, "listChanged capability")
    JOURNEY["step"] = "tools/list"
    tl, wire = rpc("tools/list")
    tools = {t["name"]: t for t in tl["tools"]}
    SIZES.append((len(wire), "j0_protocol", "tools/list", "tools/list"))
    print(f"  tools/list: {len(tools)} tools listed of {len(m.TOOLS)}, {len(wire)} bytes; instructions "
          f"{len(instr)} chars")
    check(len(wire) < 40000, f"tools/list {len(wire)} bytes (>40 KB costs every session ~10k tokens)")
    for n, t in tools.items():
        check(isinstance(t.get("description"), str) and 20 <= len(t["description"]) <= 400,
              f"{n}: listing description length {len(t.get('description') or '')}")
        sch = t.get("inputSchema") or {}
        check(sch.get("type") == "object", f"{n}: inputSchema type object")
        for p in sch.get("required") or []:
            check(p in (sch.get("properties") or {}), f"{n}: required '{p}' not in the listed properties")
    import re
    mentioned = set(re.findall(r"\b((?:flh|flp|fl|audio|files|capture)_[a-z_0-9]+)", instr))
    mentioned = {x for x in mentioned if x in m.TOOLS or x.rstrip("_") in m.TOOLS}
    unlisted = sorted(x for x in mentioned if x not in tools)
    JOURNEY["unlisted_mentioned"] = unlisted
    print(f"  INSTRUCTIONS mention {len(mentioned)} tools; not listed (reached via flh_call): {unlisted}")
    for x in sorted(set(re.findall(r"\b((?:flh|flp|fl|audio|files|capture)_[a-z_0-9]+)\(", instr))):
        check(x in m.TOOLS, f"INSTRUCTIONS name a tool that doesn't exist: {x}")
    for x in unlisted:                                           # every mentioned tool is reachable
        r = call("flh_tools", step=f"flh_tools name={x}", name=x)
        check(not r.error and r.get("call", default="").startswith("flh_call"), f"{x} not reachable via flh_tools: {r}")
    # the tier names tools that exist
    for n in m.ESSENTIAL:
        check(n in m.TOOLS, f"ESSENTIAL names a missing tool {n}")
    check("placeholder" not in instr, "INSTRUCTIONS still contain the integrator placeholder", bug="E2E-1")
    # first-use tools a model needs at session start
    for n in ("flh_welcome",):
        check(n in tools or n in instr, f"{n} ('call once when a user starts') is neither listed nor in INSTRUCTIONS",
              bug="E2E-2")
    JOURNEY["listed"] = sorted(tools)


def _journey1(variant):
    """welcome -> setup -> lofi beat -> melody x3 -> pick -> bounce -> score -> clashes -> doctor -> describe ->
    visualize -> perform -> undo -> finalize -> review -> collab pack -> resume."""
    live = variant == "fake_fl"
    r = call("flh_welcome", expect_error=False)
    check(r.get("try") and r.get("promises"), "welcome: things to try + promises")
    check("FLH_ROOTS folders don't exist" not in r.text, f"welcome tells a fresh install its roots are missing: "
          f"{r.get('setup')}", bug="E2E-3")
    r = call("flh_setup", step="flh_setup check", expect_error=False)
    check(isinstance(r.get("next_steps"), list), "setup: next_steps list")
    check(not r.get("writable_roots", "missing"), f"setup: internal folder reported as a missing FLH_ROOTS entry: "
          f"{r.get('writable_roots')}", bug="E2E-3")
    # "make a lofi beat"
    r = call("flh_new_song", step="new lofi song", genre="lofi", name=f"Lofi {variant}", expect_error=False, max_kb=12)
    song = r.get("project")
    check(song and os.path.isfile(song) and song.startswith(ROOTS), f"new song inside FLH_ROOTS: {song}")
    check(not flp_ok(song), f"new song reparses: {flp_ok(song)}")
    if live:
        check(FL.open == song, f"new song opened in the (fake) FL: {FL.open}")
        check(r.get("opened") or r.get("open") or "open" in r.text, "new song: says it opened in FL")
    else:
        check("open" in r.text.lower(), "new song: says FL wasn't there to open it")
    chans = {c["index"]: c for c in m.flp_list(song)["channels"]}
    # melody x3 then pick
    r = call("flp_compose", step="compose melody x3", project=song, what="melody", variations=3, expect_error=False)
    labels = [v.get("label") for v in r.get("variations", default=[])]
    check(labels == ["A", "B", "C"], f"3 variations A/B/C: {labels}")
    check(all(v.get("listen") for v in r.get("variations", default=[])), "each variation says where to listen")
    check(not flp_ok(song), f"after compose: {flp_ok(song)}")
    if live:
        check(FL.reloads and FL.reloads[-1] == song and FL.held == open(song, "rb").read(),
              "compose on the open song reloads FL and FL holds the edit")
        check(r.get("reloaded_in_fl") is not None or r.get("queued") is not None or r.get("verify_job"),
              f"compose result says whether FL got the edit (reloaded_in_fl / verify_job): {sorted(r.data or {})}",
              bug="E2E-10")
    r = call("flp_compose", step="pick B", project=song, what="pick", variation="B", expect_error=False)
    check(r.get("picked") == "B", f"picked B: {r}")
    r = call("flp_adjust", step="more bounce", project=song, words="more bounce", expect_error=False)
    check(r.get("changes") and r.get("before") and r.get("after"), "adjust: changes + before/after measured")
    if live:
        check(r.get("reloaded_in_fl") is not None or r.get("verify_job") or r.get("queued") is not None,
              f"adjust result says whether FL got the edit: {sorted(r.data or {})}", bug="E2E-10")
    check(not flp_ok(song), f"after adjust: {flp_ok(song)}")
    r = call("flp_score", project=song, expect_error=False, max_kb=8)
    check("SCORE" in str(r.get("score")) and "SECTIONS" in str(r.get("score")), "score text with sections")
    r = call("flp_clashes", project=song, expect_error=False, max_kb=8)
    check(isinstance(r.get("issues"), list), "clashes: issues list")
    for i in r.get("issues", default=[]):
        check(i.get("fix"), f"clash without a fix: {i}")
    r = call("fl_doctor", project=song, expect_error=False, max_kb=8)
    for i in r.get("issues", default=[]):
        check(i.get("fix") and i.get("severity"), f"doctor issue without fix/severity: {i}")
    r = call("fl_describe", project=song, expect_error=False, max_kb=8)
    txt = r.get("text", default="")
    check(len(txt) > 100 and "BPM" in txt, "describe: plain words with tempo")
    check(not has_ascii_art(r.text), "describe: no ASCII art by default")
    r = call("fl_visualize", step="visualize arrangement", kind="arrangement", project=song, expect_error=False)
    check(r.get("alt") and len(r.images) == 1, f"visualize: one image + alt text (images={len(r.images)})")
    if r.images:
        import base64
        png = base64.b64decode(r.images[0]["data"])
        check(png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) < 400_000, f"visualize png {len(png)} bytes")
    # performance
    r = call("flp_perform_setup", project=song, style="natural", expect_error=False, max_kb=12)
    parts = r.get("parts", default={})
    check(not [k for k in parts if k.startswith("Channel ")], f"perform parts named after channels: {list(parts)}",
          bug="E2E-5")
    if not live:
        r = call("flp_perform_undo", step="undo with no perform yet", project=song, expect_error=False)
        check(r.get("undone") is None and "nothing to undo" in str(r.get("note")) and not r.get("sections_changed")
              and not r.get("table"), f"undo with nothing to undo says so (no changed sections, no table): "
              f"{r.text[:200]}")
        r = call("flh_call", step="flp_list names", tool="flp_list", args={"path": song}, expect_error=False)
        names = [c.get("name") for c in r.get("channels", default=[])]
        check("Kick" in names and "Vinyl" in names, f"flp_list names sampler channels: {names}", bug="E2E-5")
        r = call("flp_remove_channels", step="remove sampler channel by name (dry run)", project=song,
                 channels=["Vinyl"], dry_run=True)
        check(not r.error, f"remove a sampler channel by its name: {r.text[:200]}")
    r = call("flp_perform", step="perform Hook +20% humanize", project=song, changes={"Hook": {"humanize": "+20%"}})
    if r.error:                                                      # lofi's form has no 'Hook': the model retries
        check("have:" in r.text, f"unknown section error lists the sections: {r.text}")
        import re
        secs = re.findall(r"'([^']+)'", r.text.split("have:")[1])
        target = next((x for x in secs if x.lower() not in ("intro", "outro")), secs[0])
        r = call("flp_perform", step=f"perform {target} +20% humanize", project=song,
                 changes={target: {"humanize": "+20%"}}, expect_error=False)
    check(r.get("changed") or r.get("sections_changed"), f"perform changed something: {r.text[:200]}")
    check(not flp_ok(song), f"after perform: {flp_ok(song)}")
    r = call("flp_perform_undo", project=song, expect_error=False)
    check(not flp_ok(song), f"after undo: {flp_ok(song)}")
    # finalize
    r = call("fl_finalize", project=song, background=False, max_kb=10)
    final = None
    if not live:
        check(r.error and "Windows" in r.text, f"finalize offline: a clear Windows-only answer: {r.text[:200]}")
    else:
        check(not r.error, f"finalize with fake FL: {r.text[:500]}")
        fins = r.get("finals", default=[])
        final = fins[0].get("path") if fins and isinstance(fins[0], dict) else None
        check(final and os.path.isfile(final) and final.startswith(ROOTS), f"final written inside roots: {fins}")
        check(FL.running and FL.open == song, f"FL reopened the song after the render: {FL.running} {FL.open}")
    # review
    if final:
        r = call("fl_review", step="review final", project=song, audio=final, expect_error=False, max_kb=12)
        check(r.get("issues") is not None and r.get("sections"), f"review: sections + issues: {r.text[:300]}")
    else:
        r = call("fl_review", step="review (no render)", project=song)
        check(r.error and ("audio=" in r.text or "render" in r.text), f"review offline says how: {r.text[:200]}")
    r = call("flh_collab_pack", project=song, stems=live, background=False, max_kb=10)
    if not r.error:
        folder = r.get("folder")
        check(folder and os.path.isdir(folder) and folder.startswith(ROOTS), f"collab folder inside roots: {r.text[:300]}")
        folder = folder or os.path.join(ROOTS, "-no collab folder-")            # one failure above, not a crash
        check(os.path.isfile(os.path.join(folder, "README.txt")), "collab README")
        z = r.get("zip")
        if z:
            check(zipfile.is_zipfile(z), f"collab zip valid: {z}")
        packed = [os.path.join(dp, f) for dp, _, fs in os.walk(folder) for f in fs if f.endswith(".flp")]
        for p in packed:
            check(not flp_ok(p), f"collab project copy reparses: {p}")
    else:
        check(False, f"collab pack failed: {r.text[:300]}")
    r = call("flh_resume", project=song, expect_error=False, max_kb=6)
    check(r.get("next") and r.get("song"), "resume: song + next steps")
    return song


def j1_new_user_offline():
    _journey1("offline")


def j1_new_user_fake_fl():
    FL.install()
    try:
        _journey1("fake_fl")
    finally:
        FL.uninstall()


def _copy_in(src, name):
    d = os.path.join(ROOTS, name); os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, name + ".flp"); shutil.copy(src, dst)
    return dst


def _hangs_extend(song, section, wait_s=12):
    """Run flp_arrange_song(extend=zero-length section, dry_run) in a child process: True if it doesn't return."""
    import subprocess
    tmpd = tempfile.mkdtemp(dir=ROOTS); cp = os.path.join(tmpd, "hang.flp"); shutil.copy(song, cp)
    jf = os.path.splitext(song)[0] + ".journal.json"
    if os.path.isfile(jf):
        shutil.copy(jf, os.path.splitext(cp)[0] + ".journal.json")
    code = ("import sys, json; sys.path.insert(0, %r); import fl_headless_mcp as m; "
            "r = m.handle({'method': 'tools/call', 'params': {'name': 'flp_arrange_song', 'arguments': "
            "{'project': %r, 'mode': 'extend', 'extend': {'section': %r, 'bars': 4}, 'dry_run': True}}}); "
            "print('RETURNED')") % (os.path.join(REPO, "src"), cp, section)
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=wait_s,
                             env=dict(os.environ))
        return "RETURNED" not in out.stdout
    except subprocess.TimeoutExpired:
        return True
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def j2_producer_existing_song():
    """An existing song: open it, seed the journal, clean unused channels, extend a section, mix engineer on given
    measurements, reference match, platform preview, the 'clean and check' recipe."""
    FL.install()
    try:
        song = _copy_in(os.path.join(CORPUS, "many_patterns_writers.flp"), "Producer Song")
        r = call("fl_open", project=song, expect_error=False)
        check(FL.open == song, "fl_open opened it in the fake FL")
        r = call("flh_journal", step="journal seed", project=song, seed=True, expect_error=False, max_kb=6)
        secs = r.get("sections", default=[])
        check(secs, f"journal seed: sections from the playlist: {r.text[:300]}")
        n_before = len(m.flp_list(song)["channels"])
        r = call("flp_remove_channels", step="remove unused (dry run)", project=song, unused=True, dry_run=True,
                 expect_error=False)
        would = r.get("removed") or r.get("would_remove") or r.get("remove") or []
        check(open(song, "rb").read() == open(song, "rb").read() and len(m.flp_list(song)["channels"]) == n_before,
              "dry run changed nothing")
        nr = len(FL.reloads)
        r2 = call("flp_remove_channels", step="remove unused", project=song, unused=True, expect_error=False)
        if not r2.get("removed"):
            check(len(FL.reloads) == nr, "nothing removed -> FL not saved/reloaded", bug="E2E-8")
        n_after = len(m.flp_list(song)["channels"])
        check(not flp_ok(song), f"after remove: {flp_ok(song)}")
        check(n_before - n_after == len(as_list(would)) if would else n_after <= n_before,
              f"dry run promised {would}, real run removed {n_before - n_after}: {r2.text[:300]}")

        zero = [x.get("name") for x in secs if isinstance(x, dict) and x.get("end_bar", 0) - x.get("bar", 0) <= 0]
        check(not zero, f"journal seed made zero-length sections {zero}: {secs}", bug="E2E-7")
        if zero:
            check(not _hangs_extend(song, zero[0]), f"flp_arrange_song(extend) on zero-length section '{zero[0]}' "
                  "never returns", bug="E2E-6")
        real = [x.get("name") for x in secs if isinstance(x, dict) and x.get("end_bar", 0) - x.get("bar", 0) > 0]
        sec0 = "Hook" if "Hook" in real else (real[0] if real else "A")
        r = call("flp_arrange_song", step="extend a section", project=song, mode="extend",
                 extend={"section": sec0, "bars": 4})
        if r.error:
            check(False, f"arrange extend {sec0}: {r.text[:400]}")
        else:
            check(not flp_ok(song), f"after extend: {flp_ok(song)}")
            check(r.get("reloaded_in_fl") is not None or r.get("verify_job") or r.get("queued") is not None,
                  f"extend result says whether FL got the edit: {sorted(r.data or {})}", bug="E2E-10")
        meas = {"tracks": [{"name": "Kick", "track": 1, "lufs": -16.0, "crest_db": 14.0, "pan": 0,
                            "bands_db": {"sub": -14, "bass": -10, "low_mid": -14, "mid": -20, "high_mid": -26,
                                         "presence": -30, "air": -40}},
                           {"name": "Bass", "track": 2, "lufs": -15.0, "crest_db": 8.0, "pan": 0,
                            "bands_db": {"sub": -12, "bass": -9, "low_mid": -11, "mid": -22, "high_mid": -30,
                                         "presence": -36, "air": -45}},
                           {"name": "Vocal", "track": 3, "lufs": -20.0, "crest_db": 12.0, "pan": 0,
                            "bands_db": {"sub": -40, "bass": -24, "low_mid": -12, "mid": -14, "high_mid": -18,
                                         "presence": -20, "air": -28}}],
                "mix": {"lufs": -12.0, "true_peak": -0.2, "crest_db": 9.0, "correlation": 0.4}}
        r = call("fl_mix_engineer", step="mix engineer on measurements", measurements=meas, apply=False,
                 project=song, goal="vocal clearer", expect_error=False, max_kb=12)
        moves = r.get("moves") or r.get("proposals") or []
        check(moves, f"mix engineer proposes moves: {r.text[:300]}")
        check(not r.get("applied"), "apply=False applied nothing")
        mix = wav_write(os.path.join(ROOTS, "Producer Song", "mix.wav"), song_audio(20, seed=4))
        ref = wav_write(os.path.join(ROOTS, "refs", "reference.wav"), song_audio(20, seed=9, loud=1.0) * 1.3)
        r = call("fl_reference_match", reference=ref, mix=mix, expect_error=False, max_kb=10)
        check(r.get("differences") or r.get("moves") or r.get("issues"), f"reference match ranks differences: "
              f"{r.text[:200]}")
        r = call("fl_platform_preview", audio=mix, expect_error=False, max_kb=6)
        check("Spotify" in r.text, "platform preview names Spotify")
        r = call("flh_recipe_run", step="recipe clean and check (dry run)", name="clean and check", project=song,
                 dry_run=True, expect_error=False)
        check(r.get("plan") or r.get("steps"), f"recipe dry run plan: {r.text[:300]}")
        r = call("flh_recipe_run", step="recipe clean and check (confirm)", name="clean and check", project=song,
                 confirm=True, max_kb=20)
        check(not r.error and r.get("job"), f"recipe with a live review runs as a job: {r.text[:300]}")
        for _ in range(4):                                         # fl_job returns after <= 40 s; ask again
            if r.error or r.get("status") not in ("running", None) or not r.get("job"):
                break
            r = call("fl_job", step="recipe job", job=r.get("job"), max_kb=20)
        res = r.get("result") or r.data or {}
        steps = res.get("steps") or []
        check(res.get("ok") is True and len(steps) == 3, f"recipe finished ok with 3 steps: {r.text[:300]}")
        rv = steps[2] if len(steps) == 3 else {}
        check(isinstance(rv.get("result"), dict) and "sections" in rv["result"],
              f"recipe's review step holds the review (not a job id): {json.dumps(rv)[:300]}")
        check(not flp_ok(song), f"after recipe: {flp_ok(song)}")
    finally:
        FL.uninstall()
    # the same recipe with FL closed: the live review can't run -> the recipe must not say ok
    r = call("flh_recipe_run", step="recipe with FL closed", name="clean_and_check", project=song, confirm=True,
             background=False)
    if not r.error and r.get("job"):                  # a long step: background=false is ignored, it runs as a job
        check(r.get("background_ignored"), f"recipe says background=false was ignored: {r.text[:300]}")
        r = call("fl_job", step="recipe with FL closed (job)", job=r.get("job"), max_kb=20)
        res = r.get("result") or {}
    else:
        res = r.data or {}
    check(r.error or res.get("ok") is False, f"recipe reports the failed review step: {r.text[:400]}")


def j3_singer():
    """Vocal setup, level check, recording (fake bridge), comp of 3 takes, clean, chain plan, balance, double."""
    FL.install()
    rec = os.path.join(ROOTS, "Recorded"); os.makedirs(rec, exist_ok=True)
    os.environ["FLH_RECORDED_DIR"] = rec; FL.rec_dir = rec
    try:
        song = _copy_in(FIX, "Singer Song")
        call("fl_open", project=song, expect_error=False)
        r = call("fl_vocal_setup", project=song, input="In 1", expect_error=False)
        check(r.get("track") and r.get("audio"), f"vocal setup: track + audio advice: {r.text[:300]}")
        r = call("fl_vocal_level_check", seconds=1, project=song, expect_error=False)
        check(r.get("verdict") or r.get("measured") is False, f"level check verdict: {r.text[:300]}")
        r = call("fl_vocal_record", start_bar=5, end_bar=6, takes=2, project=song, background=False, max_kb=10)
        if r.get("job"):
            r = call("fl_job", step="record job", job=r.get("job"))
        check(not r.error and len(r.get("takes", default=[])) == 2, f"2 takes recorded: {r.text[:400]}")
        # three takes of the same phrase: one in tune, one flat, one noisy
        line = [64, 65, 67, 69, 67, 65, 64, 62]
        t1 = wav_write(os.path.join(rec, "take1.wav"), hum(line, seed=1) * 0.6)
        t2 = wav_write(os.path.join(rec, "take2.wav"), hum([n - 0.4 for n in line], seed=2) * 0.6)
        t3 = wav_write(os.path.join(rec, "take3.wav"), hum(line, seed=3, noise=0.08) * 0.6)
        r = call("fl_vocal_comp", takes=[t1, t2, t3], project=song, key="C major", expect_error=False, max_kb=12)
        if r.get("job"):
            r = call("fl_job", step="comp job", job=r.get("job"))
        comp = r.get("output") or r.get("comp")
        check(comp and os.path.isfile(comp) and comp.startswith(ROOTS), f"comp written inside roots: {comp}")
        picks = json.dumps(r.data)
        check("take2" not in picks or "take1" in picks, f"comp prefers the in-tune take: {picks[:300]}")
        r = call("fl_vocal_clean", wav=comp or t1, expect_error=False, max_kb=10)
        if r.get("job"):
            r = call("fl_job", step="clean job", job=r.get("job"))
        clean = r.get("output")
        check(clean and os.path.isfile(clean), f"clean wrote a new file: {r.text[:200]}")
        r = call("fl_vocal_chain", take=clean or t1, style="pop", apply=False, project=song, expect_error=False)
        check(r.get("chain") or r.get("plugins") or r.get("plan"), f"chain plan: {r.text[:300]}")
        beat = wav_write(os.path.join(rec, "beat.wav"), song_audio(len(hum(line)) / SR, seed=5)[:, 0] * 0.5)
        r = call("fl_vocal_balance", project=song, vocal_wav=clean or t1, beat_wav=beat, style="pop",
                 expect_error=False)
        if r.get("job"):
            r = call("fl_job", step="balance job", job=r.get("job"))
        check(r.get("sections") or r.get("verdict") or r.get("vocal_minus_beat_lu") is not None,
              f"balance verdict: {r.text[:300]}")
        r = call("fl_vocal_double", wav=clean or t1, kind="double", expect_error=False)
        if r.get("job"):
            r = call("fl_job", step="double job", job=r.get("job"))
        dbl = r.get("output")
        check(dbl and os.path.isfile(dbl) and dbl.startswith(ROOTS), f"double written: {r.text[:200]}")
    finally:
        os.environ.pop("FLH_RECORDED_DIR", None)
        FL.uninstall()


def j4_musician_idea():
    """A MIDI idea -> capture -> build chords/bass/drums (rnb); a hummed melody -> MIDI; build into a project."""
    d = os.path.join(ROOTS, "Ideas"); os.makedirs(d, exist_ok=True)
    mid = os.path.join(d, "idea.mid"); div = 480
    melody = [(0, 64), (1, 67), (2, 69), (3, 67), (4, 72), (5, 71), (6, 69), (7, 67)]
    evs = [(0, b"\xff\x51\x03" + (int(60e6 / 92)).to_bytes(3, "big"))]
    for beat, n in melody:
        evs += [(beat * div, bytes([0x90, n, 96])), (beat * div + div - 20, bytes([0x80, n, 0]))]
    m.midi_write(mid, div, [("Idea", evs)])
    r = call("capture_from_midi_file", path=mid, expect_error=False)
    check(r.get("notes") == 8 or len(as_list(r.get("notes"))) == 8, f"capture: 8 notes: {r.text[:300]}")
    check(abs((r.get("bpm") or r.get("tempo") or 0) - 92) < 1, f"capture tempo 92: {r.get('bpm')}")
    r = call("capture_and_build", what=["chords", "bass", "drums"], genre="rnb", expect_error=False, max_kb=15)
    check(r.get("parts") or r.get("suggestions") or r.get("built"), f"build around the idea: {r.text[:300]}")
    hummed = wav_write(os.path.join(d, "hummed.wav"), hum([60, 62, 64, 65, 67, 65, 64, 62], seed=4, step=0.5))
    r = call("audio_to_midi", input=hummed, mode="melody", output_mid=os.path.join(d, "hummed.mid"),
             expect_error=False)
    got = [n[2] for n in (r.get("notes", "melody", default=[]) or []) if isinstance(n, list) and len(n) >= 3]
    check(got[:8] == [60, 62, 64, 65, 67, 65, 64, 62], f"hummed melody transcribed: {got} / {r.text[:300]}")
    check(r.get("confidence") is not None or "confiden" in r.text, "transcription says how sure it is")
    check(os.path.isfile(os.path.join(d, "hummed.mid")), "hummed.mid written")
    # compose around it, into a new project
    r = call("flh_new_song", step="new rnb song", genre="rnb", name="Idea Song", starter=False, open=False,
             expect_error=False)
    song = r.get("project")
    r = call("capture_and_build", step="build into the project", idea=mid, what=["chords", "bass", "drums"],
             genre="rnb", project=song, expect_error=False, max_kb=15)
    check(not flp_ok(song), f"after build: {flp_ok(song)}")
    info = m.flp_info(song)
    check(sum((info.get("notes_per_channel") or {}).values()) > 0 if isinstance(info.get("notes_per_channel"), dict)
          else "notes" in json.dumps(info), f"notes written into the project: {json.dumps(info)[:300]}")


def j5_accessibility():
    """Hearing + plain language prefs carry into results; vision=blind -> alt text, no picture."""
    try:
        r = call("fl_access", step="set hearing+plain", set={"hearing": "reduced_highs", "language": "plain"},
                 expect_error=False)
        r = call("flh_new_song", step="new lofi song", genre="lofi", name="Access Song", open=False, expect_error=False)
        song = r.get("project")
        r = call("fl_doctor", project=song, expect_error=False)
        check(r.get("glossary") or r.get("access"), f"doctor carries plain-language notes (glossary/access): "
              f"{list((r.data or {}).keys())}")
        r = call("fl_describe", project=song, expect_error=False)
        check(not has_ascii_art(r.text), "describe: no ASCII art")
        check(r.get("glossary") or r.get("access") or "plain" in r.text, "describe carries plain language")
        r = call("flp_score", project=song, expect_error=False)
        check(not has_ascii_art(r.text), "score: no piano-roll art by default")
        audio = wav_write(os.path.join(ROOTS, "Access Song", "render.wav"), song_audio(24, seed=11))
        harsh = audio.replace("render", "harsh")
        x = song_audio(24, seed=12); hiss = RNG.standard_normal(x.shape) * 0.08
        hiss = np.diff(np.concatenate([np.zeros((1, 2)), hiss]), axis=0)
        wav_write(harsh, np.clip(x + hiss * 2, -1, 1))
        r = call("fl_review", step="review render (hearing prefs)", project=song, audio=harsh, expect_error=False)
        kinds = [i.get("kind") for i in r.get("issues", default=[])]
        check(r.get("access") or not kinds, f"review with reduced_highs: access notes for {kinds}: "
              f"{list((r.data or {}).keys())}")
        r = call("fl_visualize", step="visualize (prefs, sighted)", kind="energy", project=song, expect_error=False)
        check(len(r.images) == 1 and r.get("alt"), "visualize: picture + alt for a sighted user")
        r = call("fl_access", step="set vision blind", set={"vision": "blind"}, expect_error=False)
        r = call("fl_visualize", step="visualize (blind)", kind="arrangement", project=song, expect_error=False)
        check(not r.images and r.get("alt"), f"blind: alt text, no picture (images={len(r.images)})")
        r = call("fl_access", step="bad pref value", set={"hearing": "superb"})
        check(r.error and "reduced" in r.text, f"bad value lists the options: {r.text[:200]}")
        r = call("fl_access", step="read prefs", expect_error=False)
        check("blind" in r.text and "reduced_highs" in r.text, f"prefs kept: {r.text[:200]}")
    finally:
        call("fl_access", step="clear prefs", clear="all")
    r = call("fl_doctor", step="doctor after clear", project=song, expect_error=False)
    check(not r.get("access") and not r.get("glossary"), "no access notes once prefs are cleared")


def j6_big_project_lazy():
    """A big project open in FL: edits queue (lazy), reads say so, flh_sync applies them in ONE reload; plans."""
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import make_corpus
    work = os.path.join(ROOTS, "Big"); os.makedirs(work, exist_ok=True)
    built = make_corpus.build(m, work, heavy=True, only=["channels_200"])
    big = built["channels_200"]["path"]
    FL.install()
    try:
        call("fl_open", project=big, expect_error=False)
        m._note_load_cost(big, 45, "reload")                         # FL took 45 s to load it
        r = call("flh_sync", step="mode lazy", mode="lazy", expect_error=False)
        n0 = len(FL.reloads); orig = open(big, "rb").read()
        r1 = call("flp_set_tempo", step="queued tempo", path=big, bpm=128)
        r2 = call("flp_replace_part", step="queued notes", project=big, channel=0,
                  notes=[[1, 1, "C4", 90], [2, 0.5, 64, 80]])
        r3 = call("flp_edit", step="queued batch", project=big,
                  ops=[{"tool": "flp_style_channels", "channels": [{"channel": 1, "name": "Lazy Renamed"}]}])
        for r in (r1, r2, r3):
            check(not r.error and r.get("queued") is True, f"{r.tool} queued: {r.text[:300]}")
        check(len(FL.reloads) == n0 and open(big, "rb").read() == orig, "nothing reached FL or the file yet")
        r = call("flp_info", step="read while queued", path=big, expect_error=False)
        check("pending" in r.text or "queued" in r.text, f"reads report pending edits: {r.text[:300]}")
        check(abs(r.get("tempo_bpm", default=0) - 128) < 1e-6, f"reads see the queued state: {r.get('tempo_bpm')}")
        r = call("flp_score", step="score while queued", project=big, expect_error=False)
        check("128" in str(r.get("score")) or r.get("pending_note"),
              f"score reads the queued tempo or says it didn't: {str(r.get('score'))[:120]}")
        r = call("fl_doctor", step="doctor while queued", project=big, expect_error=False)
        check("pending" in r.text or "queued" in r.text, f"doctor says it read queued edits: {r.text[:200]}")
        r = call("fl_status", step="status shows queue")
        check("pending" in r.text or "queued" in r.text, "status shows the queue")
        r = call("flh_run", step="plan (dry run)", dry_run=True,
                 steps=[{"tool": "flp_info", "args": {"path": big}}, {"tool": "fl_doctor", "args": {"project": big}},
                        {"tool": "fl_finalize", "args": {"project": big}}], expect_error=False)
        check(r.get("plan") and (r.get("eta_s") is not None or r.get("estimated_s") is not None
                                 or "est" in r.text), f"dry run plan with eta: {r.text[:400]}")
        r = call("flh_sync", step="sync once", expect_error=False)
        check(len(FL.reloads) == n0 + 1, f"ONE reload for 3 queued edits: {len(FL.reloads) - n0}")
        check(not flp_ok(big) and abs(m.flp_info(big)["tempo_bpm"] - 128) < 1e-6, "file holds the edits")
        names = {c["index"]: c.get("name") for c in m.flp_list(big)["channels"]}
        check(names.get(1) == "Lazy Renamed" or "Lazy Renamed" in json.dumps(m.flp_list(big)),
              "rename applied")
        check(not m._pending_get(big), "queue empty after sync")
        # a queued edit reaches FL before listening (one reload, then the listen)
        r = call("flp_set_tempo", step="queue before listen", path=big, bpm=126)
        check(r.get("queued") is True, "queued again")
        n1, l1 = len(FL.reloads), len(FL.listens)
        r = call("fl_listen", step="listen syncs first", start_bar=1, end_bar=3, expect_error=False)
        check(len(FL.reloads) == n1 + 1 and len(FL.listens) == l1 + 1 and not m._pending_get(big),
              f"listen applied the queue first: reloads +{len(FL.reloads) - n1}, listens +{len(FL.listens) - l1}")
        r = call("flh_sync", step="mode auto", mode="auto")
    finally:
        FL.uninstall()


def j7_robustness():
    raw, _ = rpc("tools/call", {"name": "fl_nonexistent_tool", "arguments": {}})
    JOURNEY["step"] = "unknown tool"
    check(raw.get("isError") and "unknown tool" in raw["content"][0]["text"], "unknown tool: isError")
    check("flh_tools" in raw["content"][0]["text"], f"unknown tool error points to flh_tools: "
          f"{raw['content'][0]['text']}", bug="E2E-11")
    song = _copy_in(FIX, "Robust Song")
    r = call("flp_info", step="bad arg type", path=12345)
    check(r.error and "Traceback" not in r.text, f"bad type: clean error: {r.text[:200]}")
    check("path" in r.text, f"bad type: the error names the argument: {r.text[:200]}", bug="E2E-12")
    r = call("flp_compose", step="bad enum value", project=song, what="symphony", dry_run=True)
    check(r.error and "beat" in r.text, f"bad 'what' lists the valid values: {r.text[:200]}")
    r = call("flp_info", step="unexpected arg", path=song, bogus=1)
    check(r.error and "bogus" in r.text, f"unexpected arg named: {r.text[:200]}")
    r = call("flp_compose", step="missing required arg", project=song)
    check(r.error and "what" in r.text, f"missing required arg named: {r.text[:200]}")
    r = call("flp_info", step="missing project", path=os.path.join(ROOTS, "nope", "missing.flp"))
    check(r.error and "missing.flp" in r.text, f"missing file named: {r.text[:200]}")
    # outside FLH_ROOTS
    out = os.path.join(OUTSIDE, "Out Song.flp"); shutil.copy(FIX, out); before = open(out, "rb").read()
    TEST_WRITES.add(out)
    r = call("flp_set_tempo", step="write outside roots", path=out, bpm=99)
    check(r.error and "FLH_ROOTS" in r.text and open(out, "rb").read() == before, f"outside write refused: {r.text[:200]}")
    r = call("flp_set_tempo", step="output outside roots", path=song, bpm=99, output=os.path.join(OUTSIDE, "x.flp"))
    check(r.error and not os.path.exists(os.path.join(OUTSIDE, "x.flp")), f"output outside refused: {r.text[:200]}")
    r = call("flh_new_song", step="new song outside roots", genre="lofi", folder=OUTSIDE, open=False)
    check(r.error and not os.listdir(OUTSIDE) == [] or r.error, f"new song outside refused: {r.text[:200]}")
    r = call("fl_visualize", step="picture outside roots", kind="arrangement", project=song,
             save=os.path.join(OUTSIDE, "pic.png"))
    check(r.error and not os.path.exists(os.path.join(OUTSIDE, "pic.png")), f"save outside refused: {r.text[:200]}")
    r = call("flp_edit", step="flp_edit outside", project=out, ops=[{"tool": "flp_set_tempo", "bpm": 91}])
    check(r.error and open(out, "rb").read() == before, f"flp_edit outside refused: {r.text[:200]}")
    r = call("flp_info", step="read outside roots (allowed)", path=out, expect_error=False)
    check(sorted(os.listdir(OUTSIDE)) == ["Out Song.flp"], f"nothing new outside: {os.listdir(OUTSIDE)}")
    # Windows-only tools on Linux
    r = call("fl_transport", step="windows-only tool", action="play")
    check(r.error and "Windows" in r.text, f"windows-only: clear message: {r.text[:200]}")
    r = call("flh_call", step="flh_call windows-only", tool="fl_render", args={"project": song})
    check(r.error and "Windows" in r.text, f"flh_call windows-only: {r.text[:200]}")
    # discovery
    r = call("flh_tools", step="flh_tools query vocal", query="vocal", expect_error=False)
    names = [t[0] for t in r.get("tools", default=[])]
    check(sum(n.startswith("fl_vocal") for n in names) >= 5, f"query vocal finds the vocal tools: {names}")
    r = call("flh_tools", step="flh_tools group vocal", group="vocal", expect_error=False)
    check(len(r.get("tools", default=[])) >= 8, f"group vocal lists the vocal tools: {r.text[:200]}")
    r = call("flh_tools", step="flh_tools groups", expect_error=False)
    r = call("flh_call", step="flh_call non-essential", tool="flp_list", args={"path": song}, expect_error=False)
    check(r.get("channels"), "flh_call flp_list works")
    r = call("flh_call", step="flh_call bad tool", tool="nope_tool", args={})
    check(r.error and "nope_tool" in r.text, "flh_call unknown tool")
    r = call("flh_run", step="flh_run parallel", parallel=True, expect_error=False,
             steps=[{"tool": "flp_info", "args": {"path": song}, "parallel": True},
                    {"tool": "fl_doctor", "args": {"project": song}, "parallel": True},
                    {"tool": "flp_score", "args": {"project": song}, "parallel": True},
                    {"tool": "flp_set_tempo", "args": {"path": song, "bpm": 101}},
                    {"tool": "flp_info", "args": {"path": "$prev.path"}}])
    check(r.get("ok") is True, f"flh_run ok: {r.text[:400]}")
    check(abs(m.flp_info(song)["tempo_bpm"] - 101) < 1e-6, "flh_run writer applied")
    r = call("flh_run", step="flh_run with an error", steps=[{"tool": "flp_info", "args": {"path": "/nope.flp"}},
                                                            {"tool": "flp_info", "args": {"path": song}}])
    check((r.error or r.get("ok") is False) and "success" not in r.text.lower(), f"flh_run reports the failure: "
          f"{r.text[:300]}")
    r = call("flh_run", step="flh_run nested", steps=[{"tool": "flh_run", "args": {"steps": []}}])
    check(r.error or r.get("ok") is False, f"nested flh_run refused: {r.text[:200]}")
    # bug report scrubbed
    os.environ["USERNAME"] = os.environ["USER"] = "e2euser"
    call("flp_info", step="error with a private path", path="C:\\Users\\Alice Smith\\Music\\secret song.flp")
    call("flp_info", step="error with home path", path="/home/e2euser/Music/secret2.flp")
    call("flh_new_song", step="error with an email", genre="no-such-genre alice@example.com", open=False)
    r = call("flh_bug_report", expect_error=False)
    z = r.get("zip")
    check(z and zipfile.is_zipfile(z) and z.startswith(LOCAL), f"bug report zip in the state folder: {z}")
    if z and zipfile.is_zipfile(z):
        blob = "".join(zipfile.ZipFile(z).read(n).decode("utf-8", "replace") for n in zipfile.ZipFile(z).namelist())
        for secret in ("Alice", "alice@example.com", "e2euser"):
            check(secret not in blob, f"bug report leaks '{secret}'")
        check("secret song" in blob or r.get("errors_included", 0) >= 1, "the errors are in the report")
    # FL just started (its title names no project): a new song still gets its starter beat
    FL.install(); FL.title_override = "FL Studio 2026"
    try:
        r = call("flh_new_song", step="new song, FL title without a project", genre="trap", name="Fresh FL",
                 expect_error=False)
        p = r.get("project")
        n = sum((m.flp_info(p).get("notes_per_channel") or {}).values()) if p else 0
        check(n > 0, f"starter beat written while FL's title names no project ({n} notes; notes: "
              f"{[x for x in r.get('notes', default=[]) if 'starter' in x]})", bug="E2E-9")
        check(n > 0 or r.get("warning") or r.get("WARNING"), "a missing starter is a top-level warning, not a note",
              bug="E2E-9")
    finally:
        FL.title_override = None; FL.uninstall()
    # failures injected into the fake FL: results must say so, never 'success'
    FL.install()
    try:
        fsong = _copy_in(FIX, "Failing Song")
        call("fl_open", step="open failing song", project=fsong, expect_error=False)
        FL.fail_render = True
        r = call("fl_finalize", step="finalize, render fails", project=fsong, background=False)
        fins = r.get("finals", default=[])
        check(r.error or r.get("failed") or (fins and all(f.get("error") for f in fins)) or
              (not fins and "fail" in r.text.lower()), f"failed render reported as a failure: {r.text[:400]}")
        check(not [f for f in os.listdir(os.path.dirname(fsong)) if f.endswith(".flac")], "no final claimed/written")
        check(FL.running, "FL reopened after the failed render")
        FL.fail_render = False
        FL.silent = True
        r = call("fl_listen", step="listen hears silence", start_bar=1, end_bar=2)
        check(not r.error and r.get("heard") is False and r.get("why"), f"silence -> heard false + why: {r.text[:300]}")
        FL.silent = False
        FL.no_save = True
        before = open(fsong, "rb").read()
        r = call("flp_set_tempo", step="write while FL won't save", path=fsong, bpm=97)
        check(r.error and "nothing was changed" in r.text and open(fsong, "rb").read() == before,
              f"FL didn't save -> nothing changed, said so: {r.text[:300]}")
        FL.no_save = False
    finally:
        FL.fail_render = FL.silent = FL.no_save = False
        FL.uninstall()
    # FLH_ROOTS unset: setup must say so
    saved = os.environ.pop("FLH_ROOTS")
    try:
        r = call("flh_setup", step="setup without FLH_ROOTS", expect_error=False)
        check("FLH_ROOTS isn't set" in r.text, f"setup says FLH_ROOTS isn't set: {r.get('next_steps')}", bug="E2E-3")
        r = call("flp_set_tempo", step="write without FLH_ROOTS", path=song, bpm=77)
        check(r.error, f"no roots -> writes refused: {r.text[:200]}")
    finally:
        os.environ["FLH_ROOTS"] = saved


# journeys split over processes (FLH_E2E_JOBS, default 2 on a multi-core PC): each shard is a full run of its
# journeys with its own temp tree, fake FL install and state folder - the same as `test_e2e.py j2 j6` - and the parent
# merges the verdicts. Rough wall seconds per journey (fake FL, virtual clock) balance the shards.
COST = {"j1_new_user_fake_fl": 40, "j2_producer_existing_song": 8, "j6_big_project_lazy": 3, "j5_accessibility": 2,
        "j1_new_user_offline": 1, "j3_singer": 1, "j7_robustness": 1, "j4_musician_idea": 0.5, "j0_protocol": 0.5}


def _run_shards(names, n_sh):
    import subprocess
    groups = [[] for _ in range(n_sh)]; load = [0.0] * n_sh
    for nm in sorted(names, key=lambda x: -COST.get(x, 5)):
        k = load.index(min(load)); groups[k].append(nm); load[k] += COST.get(nm, 5)
    outs, procs = [], []
    for k, g in enumerate(groups):
        if not g:
            continue
        out = os.path.join(TMP, f"shard_{k}.json"); outs.append(out)
        procs.append(subprocess.Popen([sys.executable, os.path.abspath(__file__), "--journeys", ",".join(g),
                                       "--report", out], env=ENV0, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
    texts = [p.communicate()[0] for p in procs]
    for t in texts:
        sys.stdout.write(t)
    status, seen, sizes, skipped, nfail = {}, set(), [], 0.0, 0
    for o, p in zip(outs, procs):
        try:
            r = json.load(open(o, encoding="utf-8"))
        except (OSError, ValueError):
            nfail += 1; print(f"FAIL a journey shard crashed (exit {p.returncode})"); continue
        status.update(r["status"]); seen |= set(r["seen"]); sizes += [tuple(x) for x in r["sizes"]]
        skipped += r["skipped"]; nfail += r["fails"]
    return {nm: status.get(nm, "FAIL") for nm in names}, seen, sizes, skipped, nfail


if __name__ == "__main__":
    argv = sys.argv[1:]
    exact = report = None
    if "--journeys" in argv:
        exact = argv[argv.index("--journeys") + 1].split(","); argv = [a for a in argv if a not in exact]
    if "--report" in argv:
        report = argv[argv.index("--report") + 1]; argv.remove(report)
    only = [a for a in argv if not a.startswith("-")]
    ALL = (j0_protocol, j1_new_user_offline, j1_new_user_fake_fl, j2_producer_existing_song, j3_singer,
           j4_musician_idea, j5_accessibility, j6_big_project_lazy, j7_robustness)
    jobs = int(os.environ.get("FLH_E2E_JOBS") or min(2, os.cpu_count() or 1))
    if exact is None and not only and jobs > 1:
        STATUS, seen, sizes, skipped, nfail = _run_shards([j.__name__ for j in ALL], jobs)
        SEEN_KNOWN |= seen; SIZES[:] = sizes; FAILS[:] = [("shards", None, "")] * nfail
        VCLOCK.skipped = skipped
    else:
        BLANK = build_fl_install()
        for j in ALL:
            if (exact is not None and j.__name__ in exact) or (exact is None and (not only or any(o in j.__name__ for o in only))):
                journey(j)
    SIZES.sort(reverse=True)
    if report:
        with open(report, "w", encoding="utf-8") as f:
            json.dump({"status": STATUS, "seen": sorted(SEEN_KNOWN), "sizes": [list(x[:3]) + [None] for x in SIZES[:6]],
                       "skipped": VCLOCK.skipped, "fails": len(FAILS)}, f)
        shutil.rmtree(TMP, ignore_errors=True)
        sys.exit(1 if FAILS else 0)
    print("largest results:", [(b, j, s) for b, j, s, _ in SIZES[:6]])
    for k in sorted(set(KNOWN) - SEEN_KNOWN) if not only else []:
        print(f"known bug {k} not seen")
    print(f"journeys: {STATUS}  ({time.time() - VCLOCK._off - T_START:.0f}s wall" + (f", {jobs} processes" if exact is None and not only and jobs > 1 else "")
          + f"; virtual clock skipped {VCLOCK.skipped:.0f}s of waiting)")
    shutil.rmtree(TMP, ignore_errors=True)
    if FAILS:
        print(f"{len(FAILS)} failure(s)"); sys.exit(1)
    print("ok")
