"""flh_pccheck_mod: every module check runs against the fake FL of tests/test_e2e.py (extended here so FL "plays"
the notes of the open project as sine tones at their sounding pitch, emulates plugin knobs from the shipped maps and
keeps mixer sends/mutes) with a ctx that follows the fl_pc_check contract (flh_pccheck's own ctx when it offers one,
else the stub below). Every check must pass or skip cleanly here, every Check and result must match the contract's
schema, and nothing may be written outside the sandbox (plus fl-headless's state folder and what FL itself writes).
Run: python3 tests/test_pccheck_mod.py (no FL, no network; the listening and save waits run on tests/test_e2e.py's
virtual clock, so they cost no wall time; FLH_REAL_WAITS=1 waits for real, about 3 min)."""
import contextlib, json, os, shutil, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(REPO, "src"))
import test_e2e as E  # noqa: E402  (sets FLH_ROOTS / LOCALAPPDATA / fake FL install paths to a temp tree)
import numpy as np  # noqa: E402

m, ns = E.m, E.ns
VERBOSE = bool(os.environ.get("FLH_TEST_VERBOSE"))
import flh_pccheck_mod as P  # noqa: E402
P.POPUP_WAIT_S = 0.0                                              # the fake FL never shows popups: don't wait for them

AREAS = {"core", "render", "live_notes", "lazy", "bridge", "files", "newsong", "mix", "finalize", "review", "vocal",
         "midi", "handbook", "catalog", "speed", "sound"}
NEEDS = {"fl", "bridge", "mic", "midi_keyboard", "network", "user"}
STATUSES = {"pass", "fail", "warn", "skip"}

# ============================================================================ the FL install + extras for these checks
E.build_fl_install()
# FL's own multisampled instruments (Packs/Instruments/<family>/<name>.fst, DirectWave): an upright bass
_bb = os.path.join(E.DOCS, "Presets", "Plugin database", "Installed", "Generators", "Fruity", "BooBass.fst")
_body = open(_bb, "rb").read()[22:]
_body = _body.replace(m._data_event(201, "BooBass".encode("utf-16-le") + b"\x00\x00"),
                      m._data_event(201, "DirectWave".encode("utf-16-le") + b"\x00\x00"))
os.makedirs(os.path.join(E.PACKS, "Instruments", "Bass"), exist_ok=True)
open(os.path.join(E.PACKS, "Instruments", "Bass", "Acoustic.fst"), "wb").write(E.make_flp(_body))
PDB = os.path.join(E.DOCS, "Presets", "Plugin database", "Installed", "Generators", "Fruity")
shutil.copyfile(os.path.join(PDB, "FL Keys.fst"), os.path.join(PDB, "FPC.fst"))   # FPC in the plugin database
# FLEX's packs (file table only, as tests/test_flex.py builds them): new songs pick FLEX presets from these, so
# newsong.flex_default checks a FLEX channel with a preset fl-headless picked (not only FLEX's bare default)
def _flex_pack(path, names):
    import struct
    ents = [("presetsIndex.presetsIndex", b"\x13\x37" * 50)] + [(n, b"P" * 64) for n in names]
    base = 8 + sum(4 + len(n) + 1 + 16 for n, _ in ents); tab = body = b""
    for n, data in ents:
        raw = n.encode() + b"\0"
        tab += struct.pack(">I", len(raw)) + raw + struct.pack(">QQ", base + len(body), len(data)); body += data
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(struct.pack(">II", 2, len(ents)) + tab + body)


_flex_pack(os.path.join(os.environ["FLH_FLEX_DIR"], "Packs", "Check Pads.flexpack"),
           ["Warm Haze Pad.flexpreset", "Glass Pad.flexpreset", "Soft Guitar.flexpreset", "Tine Keys.flexpreset"])
REC = os.path.join(E.DOCS, "Audio", "Recorded"); os.makedirs(REC, exist_ok=True)       # where FL records takes
os.environ["FLH_RECORDED_DIR"] = REC
os.environ["FLH_FL_EDITION"] = "producer"                         # what the installer's edition question stores
# the PC's registry audio settings and Windows MIDI inputs, as FL / Windows would report them
ns["fl_settings_summary"] = lambda *a, **k: {"audio": {"device": {"value": "Fake Interface ASIO"},
                                                       "sample_rate": {"value": 48000},
                                                       "buffer_length": {"value": 256}}}
import flh_transcribe  # noqa: E402
flh_transcribe._winmm_inputs = lambda: ["Fake Keys 49", "loopMIDI Port"]
SR = E.SR


def _fmt(entry, v):
    u = str(entry.get("unit") or "")
    if entry.get("curve") == "enum" or u == "enum":
        return str(v)
    if u == "Hz":
        return f"{v / 1000:.2f} kHz" if v >= 1000 else f"{v:.1f} Hz"
    if u == "ratio":
        return "Inf:1" if v == float("inf") else f"{v:.1f}:1"
    return f"{v:.2f} {u}".strip()


class SynthFL(E.FakeFL):
    """The e2e fake FL, plus: playback at the song's tempo, a 'speaker' recording that renders the open project's
    notes (sounding pitch = written - the journal's transpose; FL slide notes bend the held note), plugin knobs
    emulated from the shipped plugin maps, mixer sends and mutes."""
    def __init__(self):
        super().__init__()
        self.knobs = {}; self.sends = {}; self.muted = {}; self.play_bpm = 120.0; self.chmute = {}

    def _bpm(self):
        try:
            return float(m._project_tempo(self.held))
        except Exception:
            return 120.0

    def _bar(self):
        return self.b0 + (time.time() - self.t_play) * self.play_bpm / 240.0 if self.playing else self.bar

    def _plugin(self, index, slot):
        try:
            fx = {(f["track"], f["slot"]): f.get("plugin") for f in m.flp_list(self.open).get("effects") or []}
        except Exception:
            fx = {}
        name = fx.get((int(index), int(slot)))
        X = sys.modules["flh_mixeng"]
        mp = X.plugin_map(name) if name else None
        if not mp or mp.get("source") == "learned":               # emulate from the shipped map, always
            path = os.path.join(REPO, "src", "data", "plugin_maps", X._slug(name or "") + ".json")
            mp = json.load(open(path)) if os.path.isfile(path) else None
        ctl = {int(e["index"]): (k, e) for k, e in ((mp or {}).get("controls") or {}).items() if e.get("index") is not None}
        for k, e in ((mp or {}).get("controls") or {}).items():   # controls the map has no index for: after the rest
            if e.get("index") is None:
                ctl[max(list(ctl) + [-1]) + 1] = (k, e)
        st =self.knobs.setdefault((int(index), int(slot)), {})
        for p, (k, e) in ctl.items():
            if p not in st:
                X_ = X.to_norm(e, e.get("options", [None])[0]) if e.get("curve") == "enum" else \
                    X.to_norm(e, 0.0 if str(e.get("unit")) == "dB" and float(e.get("min", 0)) < 0 else e.get("min", 0))
                st[p] = round(float(X_), 6)
        return name, ctl, st

    def _ptext(self, ctl, p, v):
        k, e = ctl[p]
        return _fmt(e, sys.modules["flh_mixeng"].from_norm(e, v))

    def bridge(self, cmd, timeout=15.0, **a):
        if cmd in ("plugin_params", "plugin_set", "plugin_info", "plugin_probe", "plugin_set_value") and self.running:
            self.calls.append((cmd, a))
            name, ctl, st = self._plugin(a.get("index"), a.get("slot", -1))
            if cmd == "plugin_info":
                return {"plugin": name, "params": len(ctl)}
            if cmd == "plugin_params":
                f = str(a.get("filter") or "").lower()
                return {"plugin": name, "count": len(ctl), "params": [
                    {"param": p, "name": e.get("fl_name"), "value": st[p], "text": self._ptext(ctl, p, st[p])}
                    for p, (k, e) in sorted(ctl.items()) if not f or f in str(e.get("fl_name")).lower()]}
            p = int(a.get("param"))
            if p not in ctl:
                raise RuntimeError(f"no parameter {p}")
            if cmd == "plugin_set":
                st[p] = max(0.0, min(1.0, float(a.get("value"))))
                return {"param": p, "value": st[p], "text": self._ptext(ctl, p, st[p])}
            if cmd == "plugin_probe":
                n = int(a.get("points", 17)); v0 = st[p]
                texts = [self._ptext(ctl, p, k / (n - 1)) for k in range(n)]
                return {"param": p, "name": ctl[p][1].get("fl_name"), "values": [k / (n - 1) for k in range(n)],
                        "texts": texts, "restored": v0, "before": v0}
            raise RuntimeError("unknown command plugin_set_value (fake)")
        if cmd == "mixer_set" and self.running:
            r = super().bridge(cmd, timeout, **a)
            t = a.get("track")
            if a.get("muted") is not None:
                self.muted[t] = bool(a["muted"])
            for part in str(a.get("send_levels") or "").split(","):
                if ":" in part:
                    dst, lv = part.split(":")
                    self.sends.setdefault(t, {})[int(dst)] = float(lv)
            r.update(muted=self.muted.get(t, False), sends=[{"to": d, "level": v} for d, v in self.sends.get(t, {}).items()])
            return r
        if cmd == "mixer_info" and self.running:
            r = super().bridge(cmd, timeout, **a)
            one = a.get("tracks", "all") not in ("all", "", None)
            for t in r.get("tracks") or []:
                t["muted"] = self.muted.get(t["index"], False)
                t["sends"] = [{"to": d, "level": v} for d, v in self.sends.get(t["index"], {}).items()] if one else None
            return r
        if cmd == "transport" and a.get("action") == "play" and self.running:
            self.play_bpm = self._bpm()
        if cmd == "channel_set" and self.running and a.get("muted") is not None:
            self.chmute[int(a["index"])] = bool(a["muted"])
        if cmd == "state" and self.running:
            r = super().bridge(cmd, timeout, **a)
            if self.held:                                         # the open project's channels, with live mutes
                chans = m._structure(self.held)[1]
                own = [{"index": i, "name": (c or {}).get("name"), "muted": self.chmute.get(i, False)}
                       for i, c in sorted(chans.items())]
                takes = [dict(c, index=len(own) + k) for k, c in
                         enumerate(c for c in (r.get("channels") or []) if str(c.get("name", "")).startswith("Insert 2_"))]
                r["channels"] = own + takes                         # + recorded takes (the vocal checks)
            return r
        return super().bridge(cmd, timeout, **a)

    def render_in_fl(self, project, out_dir, formats="wav", timeout_s=900, out_stem=None, probe=None):
        """FL's own export of the open project: the notes of the unmuted channels (live mutes count)."""
        if probe:
            return {"probe": probe, "ok": True}
        fmt = str(formats).split(",")[0].strip().lower()
        self.play_bpm = self._bpm(); self.b0 = 1.0
        try:                                                       # the whole song, like FL's export
            bars = float(m.flp_info(self.open).get("song_length_bars") or 2)
        except Exception:
            bars = 2.0
        y = self.render_notes(min(60.0, max(1.0, bars * 240.0 / self.play_bpm)), norm=False)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, (out_stem or os.path.splitext(os.path.basename(project))[0]) + "." + fmt)
        x = np.stack([y, y * 0.98], 1)
        if fmt == "flac":
            import flac_lite
            flac_lite.encode(out, np.round(np.clip(x, -1, 1) * 8388607).astype(np.int64), SR, 24)
            E.vstamp(out)
        else:
            E.wav_write(out, x)
        self.renders.append(project)
        return {"rendered": [out], "seconds": 0.2, "method": "in_fl"}

    def fl_open(self, project):
        r = super().fl_open(project); self.knobs = {}; self.sends = {}; self.muted = {}; self.chmute = {}
        return r

    def install(self):
        super().install()
        import fl_capture
        fl = self

        class Speakers:
            def start(self):
                self.t0 = time.time()

            def stop(self):
                import fl_headless_mcp as core
                pre = float(getattr(core, "PREROLL_S", 0.0))           # FL is stopped during fl_listen's pre-roll
                sec = max(0.5, time.time() - self.t0 - pre)
                fl.listens.append(round(sec, 2))
                y = np.concatenate([np.zeros(int(pre * SR)), fl.render_notes(sec)])
                return SR, np.stack([y, y * 0.98], 1).astype(np.float32)
        fl_capture.LoopbackRecorder = Speakers
        self.orig["_render_in_fl"] = ns["_render_in_fl"]; ns["_render_in_fl"] = self.render_in_fl
        return self

    def render_notes(self, sec, norm=True):
        """What FL would play from bar b0 for `sec` seconds: each note a sine at its sounding pitch."""
        n = int(sec * SR); y = np.zeros(n)
        try:
            d = self.held; ev = m._events(d); P_ = m._ppq(d)
            notes, _ = m._song_x(d, ev)
        except Exception:
            return y
        offs = sys.modules["flh_score"].sounding_offsets(sys.modules["flh_store"].journal_get(self.open)) \
            if self.open and not getattr(self, "ignore_transpose", False) else {}
        spt = 60.0 / self.play_bpm / P_
        t0 = (self.b0 - 1) * 4 * P_
        by_ch = {}
        for nt in notes:
            by_ch.setdefault(nt[2], []).append(nt)
        for ch, lst in by_ch.items():
            if self.chmute.get(ch) and not getattr(self, "ignore_mutes", False):
                continue                                                # muted live in FL: silent
            off = offs.get(ch, 0)
            for nt in lst:
                if nt[5]:
                    continue                                            # slide notes bend the held note below
                a = int(round((nt[0] - t0) * spt * SR)); b = int(round((nt[0] + nt[1] - t0) * spt * SR))
                if b <= 0 or a >= n:
                    continue
                L = b - a
                slides = tuple((int(round((sl[0] - nt[0]) * spt * SR)), int(round(sl[1] * spt * SR)), sl[3] - off)
                               for sl in lst if sl[5] and nt[0] <= sl[0] < nt[0] + nt[1])
                key = (L, nt[3] - off, nt[4], slides)              # the same note sounds the same: computed once
                x = _NOTE_CACHE.get(key)
                if x is None:
                    semi = np.full(L, float(nt[3] - off))
                    for sa, sl_len, to in slides:
                        sb = min(L, sa + sl_len)
                        cur = semi[sa] if sa < L else semi[-1]
                        semi[sa:sb] = np.linspace(cur, to, max(1, sb - sa))
                        semi[sb:] = to
                    f = 440.0 * 2 ** ((semi - 69) / 12)
                    x = 0.25 * (nt[4] / 128.0) * np.sin(2 * np.pi * np.cumsum(f) / SR)
                    env = np.minimum(1.0, np.minimum(np.arange(L) / (0.004 * SR), (L - np.arange(L)) / (0.01 * SR)))
                    x *= env
                    if _NOTE_CACHE.get("_n", 0) + L <= 8_000_000:   # at most 64 MB of cached notes
                        _NOTE_CACHE[key] = x; _NOTE_CACHE["_n"] = _NOTE_CACHE.get("_n", 0) + L
                lo, hi = max(0, a), min(n, b)
                y[lo:hi] += x[lo - a:hi - a]
        pk = float(np.abs(y).max()) if n else 0.0
        if not norm:                                               # an export: levels add up like FL's mixer
            return y * 0.3
        return y * (0.7 / pk) if pk > 0.7 else y                   # a fixed master level: never clips


_NOTE_CACHE = {}                                                  # (samples, pitch, velocity, slides) -> waveform
FL = SynthFL()
FL.rec_dir = REC

# ============================================================================ a fake image-line.com (no network)
BASE = "https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/"
HB_PAGES = {BASE + f"html/page{i}.htm": f"<html><head><title>Mixer Page {i}</title></head><body><div id='idcontent'>"
            f"<h1>Mixer Page {i}</h1><h2 id='a'>Routing</h2><p>Mixer routing sends and sidechain number {i}, "
            f"with plenty of words about inserts and effects so the index has text.</p></div></body></html>"
            for i in range(8)}
HB_TOC = "<html><head><title>FL Studio 2026 Reference Manual</title></head><body><ul>" + "".join(
    f'<li><a href="{u}">Mixer Page {i}</a></li>' for i, u in enumerate(HB_PAGES)) + "</ul></body></html>"
HB_CALLS = []


def fake_http(url, headers):
    HB_CALLS.append(url)
    import flh_handbook as B
    if url == B.ROBOTS_URL:
        return 200, {}, b"User-agent: *\nAllow: /\n"
    html = HB_TOC if url == BASE + "Index_Frame_Left.html" else HB_PAGES.get(url)
    if html is None:
        return 404, {}, b""
    etag = '"%x"' % (len(html) * 7919)
    if headers.get("If-None-Match") == etag:
        return 304, {"etag": etag}, b""
    return 200, {"etag": etag, "content-type": "text/html; charset=utf-8"}, html.encode()


import flh_handbook  # noqa: E402
flh_handbook._default_http = fake_http
time = E.VCLOCK.install()          # the e2e virtual clock in every module loaded by now (waits cost no wall time)


# ============================================================================ a contract-conformant ctx
class StubCtx:
    """The fl_pc_check ctx, exactly as the contract describes it (used when flh_pccheck offers no ctx factory)."""
    def __init__(self, ns_, unattended=True, answers=None):
        self.ns = ns_; self.unattended = unattended; self.answers = list(answers or [])
        root = m.roots()[0]
        self.sandbox = os.path.join(root, "_fl-headless-check", time.strftime("%Y%m%d-%H%M%S") + f"-{id(self) % 1000}")
        os.makedirs(self.sandbox, exist_ok=True)
        self._cleanups = []; self._orig = None; self.said = []; self.asked = []; self.snaps = []

    def make_project(self, name, kind="small"):
        src = {"blank": os.environ.get("FLH_BLANK"), "small": E.FIX}.get(kind) or E.FIX
        dst = os.path.join(self.sandbox, "projects", name + ".flp"); os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        return dst

    def copy_user_song(self):
        return None

    def open_in_fl(self, path):
        if self._orig is None:
            self._orig = FL.open or ""
        return self.call("fl_open", project=path)

    def call(self, tool, **args):
        raw = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        text = next((c["text"] for c in raw.get("content", []) if c.get("type") == "text"), "")
        if raw.get("isError"):
            raise RuntimeError(text)
        return json.loads(text)

    def say(self, text):
        self.said.append(text)
        if VERBOSE:
            print("   say:", text)

    def ask_user(self, prompt):
        self.asked.append(prompt)
        if self.unattended:
            return None
        return self.answers.pop(0) if self.answers else "go"

    def cleanup(self, fn):
        self._cleanups.append(fn)

    def run_cleanups(self):
        while self._cleanups:
            fn = self._cleanups.pop()
            try:
                fn()
            except Exception as e:                                       # noqa: BLE001
                print("cleanup failed:", e)

    @contextlib.contextmanager
    def deadline(self, seconds):
        t0 = time.time()
        yield
        if time.time() - t0 > seconds:
            raise TimeoutError(f"took {time.time() - t0:.0f} s > {seconds} s")

    def snapshot(self, label):
        try:
            r = self.call("fl_snapshot", area="window", save=True)
            self.snaps.append(r.get("path"))
            return r.get("path")
        except Exception:
            return None


def make_ctx(unattended=True, answers=None):
    """flh_pccheck's own Ctx when it's there (the person's answers scripted, the run's report folder in the state
    folder), else the stub."""
    try:
        import flh_pccheck as core
        Base = core.Ctx
    except (ImportError, AttributeError):
        return StubCtx(ns, unattended=unattended, answers=answers)

    class TestCtx(Base):
        def __init__(self):
            stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{len(CTXS)}"
            sb = os.path.join(m.roots()[0], core.SANDBOX_DIR, stamp)
            os.makedirs(sb, exist_ok=True)
            super().__init__(ns, sb, os.path.join(os.path.dirname(m.STATE), "pc_check", stamp), unattended)
            self.answers = list(answers or []); self.asked = []

        def ask_user(self, prompt, timeout_s=None):
            self.asked.append(prompt)
            if self.unattended:
                return None
            a = self.answers.pop(0) if self.answers else "done"
            return None if a.lower() in ("skip", "no", "cancel", "n") else a
    c = TestCtx()
    CTXS.append(c)
    return c


CTXS = []


# ============================================================================ schema checks
def check_schema(c):
    assert set(c) >= {"id", "title", "area", "risk", "needs", "minutes", "run"}, sorted(c)
    assert isinstance(c["id"], str) and c["id"].count(".") == 1 and c["id"].split(".")[0] == c["area"], c["id"]
    assert c["area"] in AREAS, c["area"]
    assert isinstance(c["title"], str) and 10 < len(c["title"]) <= 110, c["title"]
    assert isinstance(c["risk"], int) and 1 <= c["risk"] <= 5, c
    assert set(c["needs"]) <= NEEDS, c["needs"]
    assert isinstance(c["minutes"], (int, float)) and 0 < c["minutes"] <= 10, c["minutes"]
    assert callable(c["run"])


def check_result(cid, r):
    assert isinstance(r, dict) and r.get("status") in STATUSES, (cid, r)
    assert isinstance(r.get("detail"), str) and len(r["detail"]) > 10, (cid, r)
    assert isinstance(r.get("evidence"), dict), (cid, r)
    blob = json.dumps(r["evidence"], allow_nan=False, default=None)
    assert len(blob) < 2500, (cid, len(blob))
    assert isinstance(r.get("fix_hint"), str), (cid, r)
    cl = r.get("checklist") or ""
    assert cl.startswith("checklists/") and os.path.isfile(os.path.join(REPO, cl.split("#")[0])), (cid, cl)
    if r["status"] in ("fail", "warn"):
        assert r["fix_hint"], (cid, "a fail/warn needs a fix hint for the developer")


# ============================================================================ run
def tree():
    out = {}
    for base in (E.TMP, REPO):
        for dp, dn, fns in os.walk(base):
            dn[:] = [d for d in dn if d not in ("__pycache__", ".git", "review")]
            for f in fns:
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p); out[p] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
    return out


def run_all(ctx, only=None):
    res = {}
    lst = sorted(P.checks(ns), key=lambda c: -c["risk"])
    for c in lst:
        if only and c["id"] not in only:
            continue
        t0 = time.time()
        try:
            r = c["run"](ctx)
        finally:
            ctx.run_cleanups()
        check_result(c["id"], r)
        res[c["id"]] = r
        print(f"  {c['id']:28s} {r['status']:5s} {time.time() - t0:5.1f}s  {r['detail'][:110]}")
        if VERBOSE:
            print("     ", json.dumps(r["evidence"])[:400])
    return res


T0 = time.time(); SKIP0 = E.VCLOCK.skipped
cs = P.checks(ns)
ids = [c["id"] for c in cs]
assert len(ids) == len(set(ids)), "check ids are unique"
for c in cs:
    check_schema(c)
assert len(cs) >= 25
# every module area of the task is covered
assert {c["area"] for c in cs} >= {"newsong", "mix", "finalize", "review", "vocal", "midi", "handbook", "catalog"}

FL.install()
before = tree()
templates = sys.modules["flh_newsong"]._fl_templates_dir()
tpl_before = set(os.listdir(templates)) if templates and os.path.isdir(templates) else set()
hb_home = flh_handbook.HOME
try:
    print("unattended run:")
    ctx = make_ctx(unattended=True)
    res = run_all(ctx)
    bad = {k: v for k, v in res.items() if v["status"] not in ("pass", "skip")}
    assert not bad, json.dumps(bad, indent=1)[:3000]
    # what must have really run (not skipped) in the fake FL
    must_pass = {"mix.knob_safety", "mix.map_learn_restore", "newsong.fl_opens_clean", "newsong.samples_resolve",
                 "newsong.mixer_readback", "newsong.plugin_db_effects", "newsong.808_pitch", "newsong.slide_glide",
                 "newsong.compose_beat", "newsong.perform_undo", "newsong.playlist_names", "newsong.flex_default",
                 "newsong.fpc_gm_keys", "newsong.template_folder", "mix.send_levels", "mix.masking_mutes",
                 "mix.engineer_live", "mix.platform_preview", "review.final_review", "review.access_describe",
                 "vocal.setup_readback", "midi.capture_probe", "handbook.small_fetch", "catalog.refresh",
                 "catalog.icon_probe"}
    assert must_pass <= {k for k, v in res.items() if v["status"] == "pass"}, \
        {k: res[k]["detail"] for k in must_pass if res[k]["status"] != "pass"}
    # people-only checks skip with a how-to when nobody is there
    for k in ("vocal.level_check", "vocal.record_take", "midi.capture_idea", "finalize.tiny_final"):
        assert res[k]["status"] == "skip" and "How to run it" in res[k]["detail"], (k, res[k])
    assert res["newsong.labs_instrument"]["status"] == "skip" and "LABS" in res["newsong.labs_instrument"]["detail"]
    assert res["render.preview_mutes"]["status"] == "pass", res["render.preview_mutes"]
    assert res["newsong.fl_instrument"]["status"] == "pass", res["newsong.fl_instrument"]
    for k in ("render.stems_in_fl", "render.flac_export_read", "render.mp3_read"):   # need FL's export confirmed first
        assert res[k]["status"] == "skip" and "render.in_fl" in res[k]["detail"], res[k]
    m._state({"in_fl_render": {"ok": True}})                         # as after a working in-FL export on the PC
    try:
        rf = run_all(ctx, only={"render.stems_in_fl", "render.flac_export_read"})
        rm = run_all(ctx, only={"render.mp3_read"})["render.mp3_read"]   # (the fake export writes WAV data as .mp3)
        assert rm["status"] == "pass" and rm["evidence"]["mp3_lufs"] is not None, rm
        assert all(v["status"] == "pass" for v in rf.values()), {k: v["detail"] for k, v in rf.items()}
        assert rf["render.stems_in_fl"]["evidence"]["method"] == "in_fl"
        # a fault: FL's export ignores live mutes -> every stem is the whole mix, and the check says so
        FL.ignore_mutes = True
        try:
            r = run_all(ctx, only={"render.stems_in_fl"})["render.stems_in_fl"]
            assert r["status"] == "fail" and "whole song" in r["detail"], r
        finally:
            FL.ignore_mutes = False
    finally:
        m._state({"in_fl_render": None})
    # measured facts in the evidence
    ev = res["newsong.808_pitch"]["evidence"]
    assert ev["should_sound"] == ev["heard"] == "C#2" and ev["offset"] == 30, ev
    ev = res["newsong.slide_glide"]["evidence"]
    assert ev["start"] == "C#2" and ev["end"] == "E2" and ev["frames_in_between"] >= 3, ev
    ev = res["mix.knob_safety"]["evidence"]
    assert ev["knob_moves_sent"] > 0 and not ev["gain_knob_at_max"] and not ev["display_searches"], ev
    assert res["mix.send_levels"]["evidence"]["read_db"] == -6.0, res["mix.send_levels"]
    assert res["handbook.small_fetch"]["evidence"]["refresh_not_modified"] == 5, res["handbook.small_fetch"]
    assert all(u.startswith("https://www.image-line.com/") for u in HB_CALLS) and HB_CALLS
    assert flh_handbook.HOME == hb_home, "the handbook folder is put back"
    assert not os.path.isdir(os.path.join(hb_home, "online")), "the user's handbook folder got no index"

    # the checks catch real faults (not vacuous): a gain knob sent to its maximum, an 808 offset FL doesn't apply
    print("fault injection:")
    X = sys.modules["flh_mixeng"]; orig_psn = X.plugin_set_named

    def spiky(track, slot, plugin, values):
        ns["BRIDGE"].call("plugin_set", index=track, slot=slot, param=2, value=1.0)     # band 3 level to +18 dB
        return orig_psn(track, slot, plugin, values)
    X.plugin_set_named = spiky
    try:
        cf = make_ctx(unattended=True)
        r = run_all(cf, only={"mix.knob_safety"})["mix.knob_safety"]
        assert r["status"] == "fail" and "maximum" in r["detail"] and r["evidence"]["gain_knob_at_max"], r
    finally:
        X.plugin_set_named = orig_psn
    FL.ignore_transpose = True
    try:
        r = run_all(cf, only={"newsong.808_pitch"})["newsong.808_pitch"]
        assert r["status"] == "fail" and r["evidence"]["diff_semitones"] > 6, r
    finally:
        FL.ignore_transpose = False
    # the runner loads these checks as they are
    import flh_pccheck as core
    mods, err = core.module_checks()
    assert err is None and sorted(c["id"] for c in mods) == sorted(ids), err
    sel, err = core.plan(areas=["newsong", "mix"])
    assert not err and any(c["id"] == "mix.knob_safety" for c in sel)

    print("attended run (a person answers 'go' / 'yes'):")
    # PC 2026-10-23: the tiny final went to the active collection's final folder as 'check final (Piano) - FINAL.flac'.
    # The check works under the sandbox profile: the final stays in the sandbox, the collection is put back after.
    S_ = sys.modules["flh_store"]; coll_before = m._state().get("collection")
    ALBUM = os.path.join(E.ROOTS, "Piano Album Finals"); os.makedirs(ALBUM, exist_ok=True)
    S_.profile_set("Piano Album", {"final_root": ALBUM, "final_name": "{song} (Piano) - FINAL.flac",
                                   "fl_edition": "producer"})
    ctx2 = make_ctx(unattended=False, answers=["yes", "go", "go"])
    try:
        res2 = run_all(ctx2, only={"finalize.tiny_final", "vocal.level_check", "vocal.record_take"})
        assert m._state().get("collection") == "Piano Album", m._state().get("collection")
    finally:
        m._state({"collection": coll_before})
    assert all(v["status"] == "pass" for v in res2.values()), {k: v["detail"] for k, v in res2.items()}
    assert os.listdir(ALBUM) == [] and "(Piano)" not in res2["finalize.tiny_final"]["evidence"]["final"], \
        (os.listdir(ALBUM), res2["finalize.tiny_final"]["evidence"])
    os.rmdir(ALBUM)
    assert res2["vocal.record_take"]["evidence"]["in_recorded_folder"] is True
    assert "record_s" in res2["vocal.record_take"]["evidence"], res2["vocal.record_take"]["evidence"]
    assert ctx2.asked and all(len(a) > 30 for a in ctx2.asked)
    # PC 2026-10-23: FL recorded into Edison (its record mode) -> the check says exactly that, with the fix
    T_ = ns["TOOLS"]; orig_vr = T_["fl_vocal_record"]
    import flh_vocal as V_
    T_["fl_vocal_record"] = (lambda **k: {"takes": [], "edison": ["Edison"], "edison_loaded_by_fl": True,
                                          "stopped": V_.EDISON_INTO, "next_step": V_.EDISON_NEXT.format(t=k.get("track")),
                                          "steps_s": {"record": 4.0, "edison_check": 0.8}, "passes": []},) + orig_vr[1:]
    try:
        ctxE = make_ctx(unattended=False, answers=["go"])
        rE = run_all(ctxE, only={"vocal.record_take"})["vocal.record_take"]
    finally:
        T_["fl_vocal_record"] = orig_vr
    assert rE["status"] == "fail" and "went into Edison" in rE["detail"], rE
    assert "into the playlist as an audio clip" in rE["next_step"] and "remove Edison" in rE["next_step"], rE
    assert rE["evidence"]["edison"] == ["Edison"] and rE["evidence"]["steps_s"]["edison_check"] == 0.8, rE
    ctx3 = make_ctx(unattended=False, answers=["skip"])
    r = run_all(ctx3, only={"midi.capture_idea"})["midi.capture_idea"]
    assert r["status"] == "skip" and ("skipped by the person" in r["detail"] or "no answer" in r["detail"]), r
finally:
    FL.uninstall()

# ============================================================================ nothing written outside the sandbox
after = tree()
sandboxes = [c_.sandbox for c_ in CTXS]
allowed = sandboxes + [E.LOCAL, E.SYSTEMP, os.path.join(E.TMP, "RecycleBin"), REC,
                       os.path.join(E.DOCS, "Settings")] + [os.path.join(templates, n) for n in
                                                            set(os.listdir(templates)) - tpl_before]
outside = []
for p, sig in after.items():
    if before.get(p) == sig:
        continue
    if p.startswith(REPO + os.sep) and not p.startswith(E.TMP):
        if p in before or p.endswith((".pyc",)):
            continue
    if any(p.startswith(a + os.sep) for a in allowed):
        continue
    outside.append(p)
assert not outside, f"written outside the sandbox: {outside[:8]}"
if templates and os.path.isdir(templates):
    new = set(os.listdir(templates)) - tpl_before                # the .flp went to the Recycle Bin; the folder with
    assert all(os.listdir(os.path.join(templates, n)) == [".fl-headless"] for n in new), new   # only the marker stays
    assert len(new) <= 1 and (not new or "by hand" in res["newsong.template_folder"]["detail"])
left = [p for p in after if p.startswith(E.SYSTEMP + os.sep) and p not in before]
assert not left, f"temp files left behind: {left[:4]}"
assert not any(p.startswith(os.path.join(E.ROOTS, "")) and "_fl-headless-check" not in p and p not in before
               for p in after), "only the sandbox grows inside FLH_ROOTS"
print(f"virtual clock: {E.VCLOCK.skipped - SKIP0:.0f} s of waiting skipped (FLH_REAL_WAITS=1 waits for real)")
print(f"{len(cs)} checks; ctx: {type(ctx).__mro__[1].__module__}.{type(ctx).__mro__[1].__name__ if not isinstance(ctx, StubCtx) else 'StubCtx'}; {time.time() - T0 - (E.VCLOCK.skipped - SKIP0):.0f}s")
shutil.rmtree(E.TMP, ignore_errors=True)
print("ok")
