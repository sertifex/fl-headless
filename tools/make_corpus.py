"""Robustness corpus for fl-headless: many structurally different, valid .flp projects.

Built two ways, both from material already in the repo:
  * recombining real event blocks of tests/data/*.flp (FL 2026) and fl_blocks.py: channels cloned / dropped /
    renumbered, patterns and clips generated, arrangements duplicated, markers and time signatures added;
  * the server's own writers (flp_add_audio_clip, flp_add_automation, flp_tempo_write, flp_new_pattern).

Usage:
  python3 tools/make_corpus.py                 write the light corpus to tests/corpus/ (+ manifest.json)
  python3 tools/make_corpus.py --heavy OUTDIR  also the heavy projects (200 channels, 50k notes) into OUTDIR
Library (used by tests/test_corpus.py and tests/test_fuzz.py):
  build(m, workdir, heavy=False, only=None) -> {name: {"path", "bytes", "desc", "heavy", "tags"}}
`m` is the imported server module; `workdir` must be inside FLH_ROOTS (the writers check it).
Everything is deterministic (fixed seeds, neutral names and paths): no author names, paths or songs."""
import json
import os
import random
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FIXTURE = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(REPO, "tests", "data"), "audio_clip_fl2026.flp")
CORPUS_DIR = os.path.join(REPO, "tests", "corpus")
if not os.path.isfile(FIXTURE):                # a public clone: the scrubbed copy committed in the corpus
    FIXTURE = os.path.join(CORPUS_DIR, "base.flp")

NOTE = "<IHHIHHBBBBBBBB"                       # FL note record (24 bytes)
CLIP = 88                                      # FL 2024+ playlist record
PB = 0x5000                                    # playlist item of pattern 0
NEUTRAL_SAMPLE = "C:\\Samples\\Corpus\\loop.wav"
NEUTRAL_DATA = "C:\\Projects\\Corpus\\"


# ============================================================================ event level
def size_of(eid):
    if eid == 172:
        return 3
    if eid < 64:
        return 1
    if eid < 128:
        return 2
    if eid < 192:
        return 4
    return None


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7; out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def enc(eid, payload):
    n = size_of(eid)
    if n is None:
        return bytes([eid]) + varint(len(payload)) + payload
    if len(payload) != n:
        raise ValueError(f"event {eid} needs {n} bytes, got {len(payload)}")
    return bytes([eid]) + payload


def parse(d):
    """bytes -> (fmt, nch, ppq, [[id, payload], ...])"""
    hl = struct.unpack("<I", d[4:8])[0]
    fmt, nch, ppq = struct.unpack("<hHH", d[8:14])
    i = 8 + hl + 8; out = []
    while i < len(d):
        eid = d[i]; i += 1
        n = size_of(eid)
        if n is None:
            n = s = 0
            while True:
                b = d[i]; i += 1; n |= (b & 0x7F) << s; s += 7
                if not b & 0x80:
                    break
        out.append([eid, d[i:i + n]]); i += n
    return fmt, nch, ppq, out


def serialize(fmt, nch, ppq, events):
    body = b"".join(enc(e, p) for e, p in events)
    return b"FLhd" + struct.pack("<I", 6) + struct.pack("<hHH", fmt, nch, ppq) + b"FLdt" + struct.pack("<I", len(body)) + body


def t16(s):
    return str(s).encode("utf-16-le") + b"\x00\x00"


def u16(v):
    return struct.pack("<H", v)


def u32(v):
    return struct.pack("<I", v & 0xFFFFFFFF)


# ============================================================================ project model
class Proj:
    """A project cut into FL 2026's sections: head | pattern data groups | links | channel 0 | pattern name groups |
    channels 1.. | arrangements | globals | mixer.  Each part is a list of [id, payload]."""

    def __init__(self, d):
        self.fmt, self.nch, self.ppq, ev = parse(d)
        self.head, self.pdata, self.links, self.chans, self.pmeta = [], [], [], [], []
        self.arrs, self.globals_, self.mixer = [], [], []
        i, n = 0, len(ev)
        while i < n and ev[i][0] not in (65, 226, 227, 64, 99):
            self.head.append(ev[i]); i += 1
        while i < n and ev[i][0] != 99 and not (ev[i][0] == 236):
            eid = ev[i][0]
            if eid == 64:
                blk = [ev[i]]; i += 1
                while i < n and ev[i][0] not in (64, 99):
                    blk.append(ev[i]); i += 1
                    if blk[-1][0] == 51:
                        break
                self.chans.append(blk)
            elif eid == 65:
                grp = [ev[i]]; i += 1
                while i < n and ev[i][0] not in (65, 64, 99, 226, 227):
                    grp.append(ev[i]); i += 1
                (self.pdata if any(e[0] in (223, 224) for e in grp) or len(grp) == 1 and not self.chans
                 else self.pmeta).append(grp)
            elif eid in (226, 227):
                self.links.append(ev[i]); i += 1
            else:                                                  # stray event between blocks: keep it with links
                self.links.append(ev[i]); i += 1
        while i < n and ev[i][0] == 99:
            arr = [ev[i]]; i += 1
            while i < n and ev[i][0] not in (99, 236):
                arr.append(ev[i]); i += 1
            self.arrs.append(arr)
        # the globals after the last arrangement's track list (242, 100, 29, ...) belong to no arrangement
        if self.arrs:
            last = self.arrs[-1]
            cut = max((k for k, e in enumerate(last) if e[0] in (238, 43, 233, 241, 36, 99)), default=0) + 1
            self.globals_, self.arrs[-1] = last[cut:], last[:cut]
        self.mixer = ev[i:]

    def copy(self):
        q = Proj.__new__(Proj)
        q.fmt, q.nch, q.ppq = self.fmt, self.nch, self.ppq
        cp = lambda L: [[e, p] for e, p in L]
        q.head, q.links, q.globals_, q.mixer = cp(self.head), cp(self.links), cp(self.globals_), cp(self.mixer)
        q.pdata, q.chans, q.pmeta, q.arrs = ([cp(g) for g in x] for x in (self.pdata, self.chans, self.pmeta, self.arrs))
        return q

    def events(self):
        out = list(self.head) + [e for g in self.pdata for e in g] + list(self.links)
        if self.chans:
            out += self.chans[0] + [e for g in self.pmeta for e in g] + [e for c in self.chans[1:] for e in c]
        else:
            out += [e for g in self.pmeta for e in g]
        out += [e for a in self.arrs for e in a] + self.globals_ + self.mixer
        return out

    def bytes(self):
        return serialize(self.fmt, len(self.chans) if self.nch_auto else self.nch, self.ppq, self.events())

    nch_auto = True

    # ---- helpers
    def head_set(self, eid, payload):
        for e in self.head:
            if e[0] == eid:
                e[1] = payload; return
        self.head.append([eid, payload])

    def clips(self, a=0):
        for e in self.arrs[a]:
            if e[0] == 233:
                return [bytearray(e[1][i:i + CLIP]) for i in range(0, len(e[1]), CLIP)]
        return []

    def set_clips(self, recs, a=0, size=CLIP):
        if size >= 36:
            for i, r in enumerate(recs):
                struct.pack_into("<I", r, 32, i + 1)
        recs = sorted(recs, key=lambda r: struct.unpack("<I", r[:4])[0])
        pl = b"".join(bytes(r[:size]) for r in recs)
        arr = self.arrs[a]
        for e in arr:
            if e[0] == 233:
                e[1] = pl; return
        j = 1
        while j < len(arr) and arr[j][0] in (241, 36):
            j += 1
        arr.insert(j, [233, pl])

    def pattern_notes(self, pid):
        for g in self.pdata:
            if struct.unpack("<H", g[0][1])[0] == pid:
                return [struct.unpack(NOTE, e[1][i:i + 24]) for e in g if e[0] == 224 for i in range(0, len(e[1]), 24)]
        return []


def chan_index(blk):
    return struct.unpack("<H", blk[0][1])[0]


def chan_get(blk, eid):
    return next((e[1] for e in blk if e[0] == eid), None)


def chan_set(blk, eid, payload, after=(201, 21)):
    for e in blk:
        if e[0] == eid:
            e[1] = payload; return blk
    j = max((k for k, e in enumerate(blk) if e[0] in after), default=0) + 1
    blk.insert(j, [eid, payload])
    return blk


def renumber(blk, idx):
    blk = [[e, p] for e, p in blk]
    blk[0][1] = u16(idx)
    return blk


def note(pos, ch, length, key, vel=100, flags=16384, midich=0):
    return struct.pack(NOTE, pos, flags, ch, length, key, 0, 120, 0, 0, midich, 64, vel & 0xFF, 128, 128)


def pattern_data(pid, notes, ctrl=None):
    g = [[65, u16(pid)]]
    if ctrl is not None:
        g.append([223, ctrl])
    g.append([224, b"".join(notes)])
    return g


def pattern_meta(pid, name):
    return [[65, u16(pid)], [193, t16(name)]]


def clip_rec(tmpl, pos, item, length, track, start=None, end=None, muted=False):
    r = bytearray(tmpl)
    struct.pack_into("<I", r, 0, pos); struct.pack_into("<H", r, 4, PB); struct.pack_into("<H", r, 6, item)
    struct.pack_into("<I", r, 8, length); struct.pack_into("<H", r, 12, 499 - (track - 1))
    if item >= PB:
        struct.pack_into("<II", r, 24, start or 0, end if end is not None else length)
    f = struct.unpack("<H", r[18:20])[0]
    struct.pack_into("<H", r, 18, (f | 0x2000) if muted else (f & ~0x2000))
    return r


def wav(path, seconds=1.0, rate=22050):
    n = int(seconds * rate)
    data = b"".join(struct.pack("<h", int(8000 * ((i // 40) % 2 * 2 - 1))) for i in range(n))
    with open(path, "wb") as f:
        f.write(b"RIFF" + u32(36 + len(data)) + b"WAVEfmt " + u32(16) + struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
                + b"data" + u32(len(data)) + data)
    return path


# ============================================================================ the base project
def base_project():
    """The repo's FL 2026 fixture with personal names and paths replaced by neutral ones."""
    p = Proj(open(FIXTURE, "rb").read())
    for e in p.head:
        if e[0] == 207:
            e[1] = t16("Artist")
        elif e[0] == 202:
            e[1] = t16(NEUTRAL_DATA)
        elif e[0] == 231 and e[1] not in (t16("Automation"), t16("Unsorted")):
            e[1] = t16("Group")
    for g in p.pmeta:
        for e in g:
            if e[0] == 193:
                e[1] = t16("Pattern 1")
    for c in p.chans:
        for e in c:
            if e[0] == 196:
                e[1] = t16(NEUTRAL_SAMPLE)
    return p


def _tmpl_clip(p):
    return next(r for r in p.clips() if struct.unpack("<H", r[6:8])[0] >= PB)


def _drop_channels(p, keep):
    """Keep only channel indexes in `keep` (renumbered 0..n-1); notes, clips and links follow."""
    old = [chan_index(c) for c in p.chans]
    mp = {o: i for i, o in enumerate(o for o in old if o in keep)}
    p.chans = [renumber(c, mp[chan_index(c)]) for c in p.chans if chan_index(c) in mp]
    for g in p.pdata:
        for e in g:
            if e[0] == 224:
                recs = [list(struct.unpack(NOTE, e[1][i:i + 24])) for i in range(0, len(e[1]), 24)]
                recs = [r for r in recs if r[2] in mp]
                for r in recs:
                    r[2] = mp[r[2]]
                e[1] = b"".join(struct.pack(NOTE, *r) for r in recs)
    p.links = [e for e in p.links if not (e[0] == 227 and struct.unpack("<H", e[1][2:4])[0] not in mp)]
    for e in p.links:
        if e[0] == 227:
            b = bytearray(e[1]); struct.pack_into("<H", b, 2, mp[struct.unpack("<H", b[2:4])[0]]); e[1] = bytes(b)
    if not mp:
        p.links = [e for e in p.links if e[0] != 226]
    for a in range(len(p.arrs)):
        recs = p.clips(a)
        keep_r = []
        for r in recs:
            it = struct.unpack("<H", r[6:8])[0]
            if it >= PB:
                keep_r.append(r)
            elif it in mp:
                struct.pack_into("<H", r, 6, mp[it]); keep_r.append(r)
        if any(e[0] == 233 for e in p.arrs[a]):
            p.set_clips(keep_r, a)
    return p


# ============================================================================ recipes
RECIPES = []


def recipe(name, desc, heavy=False, tags=()):
    def deco(fn):
        RECIPES.append((name, desc, heavy, tuple(tags), fn)); return fn
    return deco


@recipe("base", "the FL 2026 fixture (4 VST channels, automation channel, audio clip, 1 pattern, mixer FX), neutral names")
def r_base(ctx):
    return base_project()


@recipe("empty", "no patterns, no channels, no clips: header + arrangement + mixer only", tags=("empty",))
def r_empty(ctx):
    p = base_project()
    p.pdata, p.pmeta, p.chans, p.links = [], [], [], []
    p.arrs = [[e for e in a if e[0] != 233] for a in p.arrs]
    return p


@recipe("one_channel", "a single plugin channel with its notes and one pattern clip")
def r_one(ctx):
    return _drop_channels(base_project(), {0})


@recipe("one_channel_no_notes", "a single plugin channel, one empty pattern, nothing in the playlist", tags=("empty",))
def r_one_empty(ctx):
    p = _drop_channels(base_project(), {0})
    for g in p.pdata:
        for e in g:
            if e[0] == 224:
                e[1] = b""
    p.set_clips([])
    return p


@recipe("channels_200", "200 channels (real plugin/automation/audio blocks cloned, renumbered), notes on 40 of them",
        heavy=True)
def r_200(ctx):
    p = base_project(); src = p.chans; out = []
    for i in range(200):
        b = renumber(src[i % len(src)], i)
        if chan_get(b, 203) is not None:
            chan_set(b, 203, t16(f"Ch {i:03d}"))
        out.append(b)
    p.chans = out
    rng = random.Random(200)
    notes = [note(rng.randrange(0, 96 * 64) // 24 * 24, c, 48, rng.randrange(36, 84)) for c in range(0, 200, 5)
             if c % len(src) not in (3, 5) for _ in range(8)]
    p.pdata = [pattern_data(1, sorted(notes), ctrl=chan_get(p.pdata[0], 223))]
    return p


@recipe("patterns_1000", "1000 patterns with 2 notes each, every one placed once in the playlist", tags=("many",))
def r_1000(ctx):
    p = _drop_channels(base_project(), {0, 1, 5})
    tm = _tmpl_clip(p); P = p.ppq
    p.pdata, p.pmeta, recs = [], [], [r for r in p.clips() if struct.unpack("<H", r[6:8])[0] < PB]
    for pid in range(1, 1001):
        ch = pid % 2
        p.pdata.append(pattern_data(pid, [note(0, ch, P, 48 + pid % 24), note(2 * P, ch, P, 55 + pid % 12, 90)]))
        p.pmeta.append(pattern_meta(pid, f"P{pid}"))
        recs.append(clip_rec(tm, (pid - 1) * 4 * P, PB + pid, 4 * P, 1 + pid % 8))
    p.set_clips(recs)
    return p


@recipe("notes_50k", "one pattern with 50,000 notes on 4 channels (4-note chords on a 16th grid, ~780 bars), placed once", heavy=True)
def r_50k(ctx):
    p = base_project(); P = p.ppq; rng = random.Random(50000)
    notes = sorted(note((i // 4) * (P // 4), (0, 1, 2, 4)[i % 4], P // 2, rng.randrange(30, 96), rng.randrange(1, 128))
                   for i in range(50000))
    p.pdata = [pattern_data(1, notes, ctrl=chan_get(p.pdata[0], 223))]
    recs = p.clips(); r = recs[0]; ln = 12500 * P // 4 + P
    struct.pack_into("<I", r, 8, ln); struct.pack_into("<II", r, 24, 0, ln)
    p.set_clips(recs)
    return p


@recipe("audio_clips", "3 audio clips added by flp_add_audio_clip (overlapping, same and different tracks)",
        tags=("writers", "audio"))
def r_audio(ctx):
    m, wd = ctx["m"], ctx["dir"]
    path = os.path.join(wd, "_audio.flp"); open(path, "wb").write(base_project().bytes())
    w = wav(os.path.join(wd, "_tone.wav"), 2.0)
    m.flp_add_audio_clip(path, path, w, bar=2, playlist_track=3, name="Tone A")
    m.flp_add_audio_clip(path, path, w, bar=2.5, playlist_track=3, name="Tone B")
    m.flp_add_audio_clip(path, path, w, bar=9, name="Tone C", length_bars=3)
    p = Proj(open(path, "rb").read())
    for c in p.chans:
        if chan_get(c, 196) is not None:
            chan_set(c, 196, t16(NEUTRAL_SAMPLE))
    return p


@recipe("automation_clips", "volume, pan and plugin-parameter automation clips added by flp_add_automation",
        tags=("writers", "automation"))
def r_auto(ctx):
    m, wd = ctx["m"], ctx["dir"]
    path = os.path.join(wd, "_auto.flp"); open(path, "wb").write(base_project().bytes())
    m.flp_add_automation(path, path, [{"bar": 1, "db": -30}, {"bar": 5, "db": 0}], track=0)
    m.flp_add_automation(path, path, [{"bar": 3, "pan": -1}, {"bar": 4, "pan": 1}, {"bar": 9, "pan": 0}], track=1,
                         param="pan", playlist_track=6)
    m.flp_add_automation(path, path, [{"bar": 2, "value": 0.1}, {"bar": 2.25, "value": 0.9}], track=1, slot=0,
                         plugin_param=3, name="Filter sweep")
    return Proj(open(path, "rb").read())


@recipe("tempo_automation", "a tempo lane (flp_tempo_write) plus an automation clip linked to the global tempo",
        tags=("writers", "tempo"))
def r_tempo(ctx):
    m, wd = ctx["m"], ctx["dir"]
    path = os.path.join(wd, "_tempo.flp"); open(path, "wb").write(base_project().bytes())
    m.flp_tempo_write(path, path, points=[[1, 90], [5, 120], [9, 140.5], [17, 70]])
    r = m.flp_add_automation(path, path, [{"bar": 9, "value": 0.2}, {"bar": 13, "value": 0.25}], track=0,
                             name="Tempo ramp")
    p = Proj(open(path, "rb").read())
    for e in p.links:                                              # point the new clip at FL's global tempo
        if e[0] == 227 and struct.unpack("<H", e[1][2:4])[0] == r["channel"]:
            b = bytearray(e[1]); struct.pack_into("<HH", b, 8, 5, 0x4000); e[1] = bytes(b)
    return p


@recipe("layers", "a layer channel (type 3) whose children are channels 0 and 1, name with Cyrillic")
def r_layers(ctx):
    p = base_project()
    blk = renumber(p.chans[5], len(p.chans))
    blk = [e for e in blk if e[0] not in (196,)]
    chan_set(blk, 21, bytes([3])); chan_set(blk, 203, t16("Layer Ж"))
    j = max(k for k, e in enumerate(blk) if e[0] == 203) + 1
    blk[j:j] = [[94, u16(0)], [94, u16(1)]]
    p.chans.append(blk)
    return p


@recipe("arrangements_3", "3 arrangements: the original, a shifted copy with extra clips, and an empty one",
        tags=("arrangements",))
def r_arr(ctx):
    p = base_project(); P = p.ppq
    a2 = [[e, pl] for e, pl in p.arrs[0]]; a2[0][1] = u16(1)
    for e in a2:
        if e[0] == 241:
            e[1] = t16("Alt mix")
    a3 = [[99, u16(2)], [241, t16("Empty")]] + [[e, pl] for e, pl in p.arrs[0] if e in (238, 43)]
    p.arrs += [a2, a3]
    recs = p.clips(1); tm = _tmpl_clip(p)
    for r in recs:
        struct.pack_into("<I", r, 0, struct.unpack("<I", r[:4])[0] + 8 * 4 * P)
    recs.append(clip_rec(tm, 0, PB + 1, 8 * 4 * P, 2))
    p.set_clips(recs, 1)
    return p


def _markers(P, items):
    out = []
    for tick, name, sig in items:
        if sig:
            out += [[148, u32(tick | 0x08000000)], [33, bytes([sig[0]])], [34, bytes([sig[1]])], [205, t16(name)]]
        else:
            out += [[148, u32(tick)], [205, t16(name)]]
    return out


def _add_markers(p, items, a=0):
    arr = p.arrs[a]
    j = next((k for k, e in enumerate(arr) if e[0] == 233), None)
    j = j + 1 if j is not None else 2
    arr[j:j] = _markers(p.ppq, items)


@recipe("timesig_markers", "3/4 project, markers (one with CJK), time signature changes 7/8 and 4/4, 6/8 at bar 1",
        tags=("meter",))
def r_ts(ctx):
    p = base_project(); P = p.ppq; b34 = 3 * P
    p.head_set(17, bytes([3])); p.head_set(18, bytes([4]))
    _add_markers(p, [(0, "Intro", None), (4 * b34, "7/8", (7, 8)), (4 * b34 + 4 * 7 * P // 2, "Drop 漢字", None),
                     (4 * b34 + 8 * 7 * P // 2, "4/4", (4, 4)), (4 * b34 + 8 * 7 * P // 2 + 16 * P, "Outro", None)])
    return p


@recipe("odd_meters", "time signature marker at tick 0 (6/8), a 5/4 and a 13/16, duplicate marker ticks",
        tags=("meter",))
def r_odd(ctx):
    p = base_project(); P = p.ppq
    _add_markers(p, [(0, "6/8", (6, 8)), (0, "dup at 0", None), (12 * P, "5/4", (5, 4)), (12 * P, "same tick", None),
                     (32 * P, "13/16", (13, 16)), (32 * P + 13 * P // 4 * 3, "", None)])
    return p


UNI = ["é è ü ñ", "Ж Привет", "漢字 ドラム", "🎹🥁🔥", "שלום", "e\u0301\u0302 combining", "tab\tand\nnewline",
       "quote\" back\\slash", "\u200bzero-width", "Ω≈ç√∫"]


@recipe("names_unicode", "non-ASCII names everywhere: channels, patterns, arrangement, markers, project info",
        tags=("names",))
def r_uni(ctx):
    p = base_project()
    for i, c in enumerate(p.chans):
        chan_set(c, 203, t16(UNI[i % len(UNI)]))
    for g in p.pmeta:
        for e in g:
            if e[0] == 193:
                e[1] = t16("Pattern 漢字 🎹")
    for e in p.arrs[0]:
        if e[0] == 241:
            e[1] = t16("Аранжировка é")
    p.head_set(194, t16("Titre é 漢字 🎵")); p.head_set(206, t16("Жанр"))
    p.head_set(195, t16("comment\r\nline 2 ✓"))
    _add_markers(p, [(0, UNI[2], None), (16 * p.ppq, UNI[3], None)])
    return p


@recipe("names_long", "5000-character names (channel, pattern, title, marker) and an empty channel name",
        tags=("names",))
def r_long(ctx):
    p = base_project(); L = ("LongName-" * 600)[:5000]
    chan_set(p.chans[0], 203, t16(L)); chan_set(p.chans[1], 203, t16(""))
    for g in p.pmeta:
        for e in g:
            if e[0] == 193:
                e[1] = t16(L)
    p.head_set(194, t16(L)); _add_markers(p, [(0, L, None)])
    return p


@recipe("missing_samples", "sampler/audio channels pointing at missing, relative, %FLStudioUserData%, UNC and empty paths",
        tags=("samples",))
def r_missing(ctx):
    p = base_project(); src = p.chans[5]
    paths = ["C:\\Nowhere\\gone.wav", "Samples\\relative.wav", "%FLStudioUserData%\\Audio\\x.wav",
             "%FLStudioFactoryData%\\Data\\Patches\\Packs\\none.wav", "\\\\server\\share\\net.wav", "",
             "C:\\Ünïcødé\\サンプル.wav"]
    for i, s in enumerate(paths):
        b = renumber(src, len(p.chans)); chan_set(b, 196, t16(s)); chan_set(b, 203, t16(f"Missing {i}"))
        chan_set(b, 21, bytes([0]))
        p.chans.append(b)
    return p


@recipe("unknown_plugins", "unknown plugin names with random state blobs (instrument and mixer effect), empty state",
        tags=("plugins",))
def r_unknown(ctx):
    p = base_project(); rng = random.Random(7)
    blob = bytes(rng.randrange(256) for _ in range(3000))
    for i, (name, st) in enumerate([("Totally Unknown Synth X", blob), ("Fruity Wrapper", blob[:200]),
                                    ("Fruity Wrapper", b""), ("", blob[:13]), ("Plugin Ж 漢字", b"\x37\x00\x00\x00")]):
        b = renumber(p.chans[0], len(p.chans))
        for e in b:
            if e[0] == 201:
                e[1] = t16(name)
            elif e[0] == 213:
                e[1] = st
            elif e[0] == 203:
                e[1] = t16(f"Unknown {i}")
        p.chans.append(b)
    for e in p.mixer:                                              # first mixer effect becomes an unknown plugin
        if e[0] == 201:
            e[1] = t16("Mystery FX 3000"); break
    for e in p.mixer:
        if e[0] == 213:
            e[1] = blob[:777]; break
    return p


@recipe("zero_length", "empty notes events, a pattern with no data group, zero-length clips, a 0-tick note",
        tags=("edge",))
def r_zero(ctx):
    p = base_project(); P = p.ppq; tm = _tmpl_clip(p)
    p.pdata.append(pattern_data(2, []))
    p.pdata.append(pattern_data(4, [note(0, 0, 0, 60)]))
    p.pmeta += [pattern_meta(2, "Empty"), pattern_meta(3, "Name only"), pattern_meta(4, "Zero-length note")]
    recs = p.clips()
    recs += [clip_rec(tm, 4 * P, PB + 2, 0, 4), clip_rec(tm, 8 * P, PB + 3, 4 * P, 5), clip_rec(tm, 0, PB + 4, 4 * P, 6),
             clip_rec(tm, 16 * P, PB + 1, 0, 7)]
    p.set_clips(recs)
    return p


@recipe("notes_edges", "notes at tick 0 and on the clip's last tick, velocity 0/127/128 (FL's max)/255, keys 0/127/131 (FL's top key), 1-tick notes",
        tags=("edge",))
def r_edges(ctx):
    p = base_project(); P = p.ppq
    old = p.pattern_notes(1); end = struct.unpack("<I", p.clips()[0][8:12])[0]
    extra = [note(0, 0, 1, 0, 0), note(0, 1, P, 127, 127), note(end - 1, 2, 1, 60, 255), note(end - 1, 0, P * 8, 131, 1),
             note(end // 2, 4, 1, 64, 64, flags=16384 | 8), note(end // 3, 1, P, 72, 128)]
    for g in p.pdata:
        for e in g:
            if e[0] == 224:
                e[1] = b"".join(sorted([struct.pack(NOTE, *r) for r in old] + extra))
    return p


@recipe("notes_far", "a note at tick 2^31 and a clip reaching it (a song 'millions of bars' long)", tags=("edge",))
def r_far(ctx):
    p = base_project(); far = 1 << 31
    for g in p.pdata:
        for e in g:
            if e[0] == 224:
                e[1] += note(far, 0, 96, 60)
    recs = p.clips(); struct.pack_into("<I", recs[0], 8, far + 96); struct.pack_into("<II", recs[0], 24, 0, far + 96)
    p.set_clips(recs)
    return p


@recipe("overlapping_clips", "the same pattern 6 times overlapping on one track and stacked across tracks, offset clips",
        tags=("clips",))
def r_overlap(ctx):
    p = base_project(); P = p.ppq; tm = _tmpl_clip(p); recs = p.clips()
    for i in range(6):
        recs.append(clip_rec(tm, i * 2 * P, PB + 1, 16 * P, 2))
        recs.append(clip_rec(tm, 8 * P, PB + 1, 8 * P, 3 + i, start=i * P, end=i * P + 8 * P))
    p.set_clips(recs)
    return p


@recipe("track_500", "a pattern clip on the last playlist track (500) and an automation clip on track 499", tags=("clips",))
def r_t500(ctx):
    p = base_project(); P = p.ppq; tm = _tmpl_clip(p); recs = p.clips()
    recs.append(clip_rec(tm, 8 * P, PB + 1, 8 * P, 500))
    recs.append(clip_rec(tm, 0, PB + 1, 4 * P, 499))
    p.set_clips(recs)
    return p


@recipe("muted_all", "every clip muted and every channel switched off", tags=("muted",))
def r_muted(ctx):
    p = base_project(); recs = p.clips()
    for r in recs:
        struct.pack_into("<H", r, 18, struct.unpack("<H", r[18:20])[0] | 0x2000)
    p.set_clips(recs)
    for c in p.chans:
        chan_set(c, 0, bytes([0]))
    return p


def _version(p, v):
    p.head_set(199, v.encode("ascii") + b"\x00")
    for e in p.head:
        if e[0] == 192:
            e[1] = t16("FL Studio " + v)
    return p


for _v in ("20.8.4.2576", "21.2.3.4004", "24.1.2.4430", "25.1.6.4997"):
    recipe(f"fl{_v.split('.')[0]}_header", f"the base project saved as FL {_v} (version events only; FL 2024+ "
           "clip layout kept)", tags=("version",))(lambda ctx, v=_v: _version(base_project(), v))


def _legacy_playlist(n_clips, size, ver):
    def fn(ctx):
        p = _version(_drop_channels(base_project(), {0, 1, 2}), ver); P = p.ppq; tm = _tmpl_clip(p)
        recs = [clip_rec(tm, i * 4 * P, PB + 1, 4 * P, 1 + i % 3) for i in range(n_clips)]
        p.set_clips(recs, size=size)
        return p
    return fn


recipe("legacy_playlist32", "FL 20-style 32-byte playlist records (PyFLP layout), 11 clips = 352 bytes = 4 x 88",
       tags=("version", "legacy"))(_legacy_playlist(11, 32, "20.8.4.2576"))
recipe("legacy_playlist60", "FL 21-style 60-byte playlist records (PyFLP layout), 22 clips = 1320 bytes = 15 x 88",
       tags=("version", "legacy"))(_legacy_playlist(22, 60, "21.2.3.4004"))


@recipe("ppq_960", "PPQ 960 with every note and clip position rescaled", tags=("ppq",))
def r_ppq(ctx):
    p = base_project(); k = 10; p.ppq = p.ppq * k
    for g in p.pdata:
        for e in g:
            if e[0] == 224:
                recs = [list(struct.unpack(NOTE, e[1][i:i + 24])) for i in range(0, len(e[1]), 24)]
                for r in recs:
                    r[0] *= k; r[3] *= k
                e[1] = b"".join(struct.pack(NOTE, *r) for r in recs)
            elif e[0] == 223:
                b = bytearray(e[1])
                for i in range(0, len(b) - len(b) % 12, 12):
                    struct.pack_into("<I", b, i, struct.unpack_from("<I", b, i)[0] * k)
                e[1] = bytes(b)
    recs = p.clips()
    for r in recs:
        for off in (0, 8):
            struct.pack_into("<I", r, off, struct.unpack_from("<I", r, off)[0] * k)
        if struct.unpack("<H", r[6:8])[0] >= PB:
            struct.pack_into("<II", r, 24, *(x * k for x in struct.unpack_from("<II", r, 24)))
    p.set_clips(recs)
    return p


@recipe("ppq_24", "PPQ 24 (FL's coarsest) with positions rescaled (rounded)", tags=("ppq",))
def r_ppq24(ctx):
    p = base_project(); p.ppq = 24
    for g in p.pdata:
        for e in g:
            if e[0] == 224:
                recs = [list(struct.unpack(NOTE, e[1][i:i + 24])) for i in range(0, len(e[1]), 24)]
                for r in recs:
                    r[0] //= 4; r[3] = max(1, r[3] // 4)
                e[1] = b"".join(struct.pack(NOTE, *r) for r in recs)
    recs = p.clips()
    for r in recs:
        for off in (0, 8):
            struct.pack_into("<I", r, off, struct.unpack_from("<I", r, off)[0] // 4)
        if struct.unpack("<H", r[6:8])[0] >= PB:
            struct.pack_into("<II", r, 24, *(x // 4 for x in struct.unpack_from("<II", r, 24)))
    p.set_clips(recs)
    return p


@recipe("no_tempo_event", "no project tempo event (156) and no recorded tempo", tags=("tempo",))
def r_notempo(ctx):
    p = base_project(); p.head = [e for e in p.head if e[0] != 156]
    for g in p.pdata:
        for e in g:
            if e[0] == 223:
                e[1] = b""
    return p


@recipe("split_notes", "a pattern whose notes are split over two notes events (224) and a second pattern with "
        "a controller event only", tags=("edge",))
def r_split(ctx):
    p = base_project(); g = p.pdata[0]
    k = next(i for i, e in enumerate(g) if e[0] == 224); pl = g[k][1]; half = len(pl) // 48 * 24
    g[k:k + 1] = [[224, pl[:half]], [224, pl[half:]]]
    p.pdata.append([[65, u16(2)], [223, b""]])
    return p


@recipe("channel_gaps", "channel indexes 0, 5, 17, 40, 41, 300 (non-contiguous) with notes and links following",
        tags=("edge",))
def r_gaps(ctx):
    p = base_project(); new = [0, 5, 17, 40, 41, 300]; mp = {chan_index(c): new[i] for i, c in enumerate(p.chans)}
    p.chans = [renumber(c, mp[chan_index(c)]) for c in p.chans]
    for g in p.pdata:
        for e in g:
            if e[0] == 224:
                recs = [list(struct.unpack(NOTE, e[1][i:i + 24])) for i in range(0, len(e[1]), 24)]
                for r in recs:
                    r[2] = mp.get(r[2], r[2])
                e[1] = b"".join(struct.pack(NOTE, *r) for r in recs)
    for e in p.links:
        if e[0] == 227:
            b = bytearray(e[1]); struct.pack_into("<H", b, 2, mp[struct.unpack("<H", b[2:4])[0]]); e[1] = bytes(b)
    recs = p.clips()
    for r in recs:
        it = struct.unpack("<H", r[6:8])[0]
        if it < PB:
            struct.pack_into("<H", r, 6, mp.get(it, it))
    p.set_clips(recs)
    return p


@recipe("unnamed_last_channel", "the last channel has no name event (FL leaves default names out)", tags=("names",))
def r_unnamed(ctx):
    p = base_project(); p.chans[-1] = [e for e in p.chans[-1] if e[0] != 203]
    return p


@recipe("many_patterns_writers", "6 named patterns made by flp_new_pattern and placed, plus notes via flp_add_notes",
        tags=("writers",))
def r_writers(ctx):
    m, wd = ctx["m"], ctx["dir"]
    path = os.path.join(wd, "_pats.flp"); open(path, "wb").write(base_project().bytes())
    for i, nm in enumerate(["Intro", "Verse", "Hook", "Bridge é", "Outro", "Spare"]):
        r = m.flp_new_pattern(path, nm, place_at_bar=None if nm == "Spare" else 1 + 4 * i, length_bars=4)
        if nm != "Spare":
            m.flp_add_notes(path, [{"note": 60 + i, "bar": 1, "beat": b, "beats": 0.5} for b in range(4)],
                            pattern=r["pattern"], channel=i % 3)
    return Proj(open(path, "rb").read())


@recipe("mixed_kitchen_sink", "unicode + markers + automation + audio + 3 arrangements + muted clips together",
        tags=("writers", "mixed"))
def r_sink(ctx):
    m, wd = ctx["m"], ctx["dir"]
    p = r_arr(ctx); _add_markers(p, [(0, "Ж", None), (16 * p.ppq, "3/4", (3, 4))])
    chan_set(p.chans[0], 203, t16("漢字 🎹"))
    path = os.path.join(wd, "_sink.flp"); open(path, "wb").write(p.bytes())
    m.flp_add_automation(path, path, [{"bar": 1, "db": -12}, {"bar": 3, "db": -3}], track=2)
    m.flp_add_audio_clip(path, path, wav(os.path.join(wd, "_t2.wav"), 0.5), bar=4, name="Hit")
    q = Proj(open(path, "rb").read())
    recs = q.clips()
    for r in recs[::2]:
        struct.pack_into("<H", r, 18, struct.unpack("<H", r[18:20])[0] | 0x2000)
    q.set_clips(recs)
    for c in q.chans:
        if chan_get(c, 196) is not None:
            chan_set(c, 196, t16(NEUTRAL_SAMPLE))
    return q


# ============================================================================ test harness helpers (shared by tests)
class Timeout(BaseException):
    """A tool ran longer than its budget (BaseException so a tool's `except Exception` can't swallow it)."""


class time_limit:
    """with time_limit(s): ... -> raises Timeout after s seconds (SIGALRM; a no-op where that isn't available)."""

    def __init__(self, seconds):
        self.s = seconds; self.armed = False

    def __enter__(self):
        import signal
        import threading
        if self.s and hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread():
            def fire(*_):
                raise Timeout(f"took longer than {self.s} s")
            self.old = signal.signal(signal.SIGALRM, fire)
            signal.setitimer(signal.ITIMER_REAL, self.s); self.armed = True
        return self

    def __exit__(self, *exc):
        if self.armed:
            import signal
            signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGALRM, self.old)
        return False


def limit_memory(gb=3.0):
    """Cap the address space so a runaway allocation is a MemoryError instead of the OOM killer (Linux/macOS)."""
    try:
        import resource
        lim = int(gb * 2 ** 30)
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard == resource.RLIM_INFINITY or hard > lim:
            resource.setrlimit(resource.RLIMIT_AS, (lim, hard))
        return True
    except (ImportError, ValueError, OSError):
        return False


# Python's own messages: a ValueError carrying one of these is an internal crash, not a refusal a user can act on
INTERNAL_VALUE_ERRORS = ("max() arg", "max() iterable", "min() arg", "min() iterable", "not enough values to unpack",
                         "too many values to unpack", "invalid literal for int()", "could not convert string to float",
                         "is not in list", "math domain error", "cannot convert float", "embedded null", "negative shift",
                         "must not be zero", "list.remove(x)", "substring not found", "unpack requires",
                         "bytes must be in range", "need more than", "attempt to get argmax of an empty sequence",
                         "zero-size array", "empty range for randrange", "array is too big")
CRASH_TYPES = ("struct.error", "error", "IndexError", "KeyError", "TypeError", "AttributeError", "ZeroDivisionError",
               "StopIteration", "OverflowError", "RecursionError", "MemoryError", "UnboundLocalError", "NameError",
               "AssertionError", "UnicodeEncodeError", "NotImplementedError", "Timeout", "SystemError",
               "FloatingPointError", "LookupError")


def clean_error(tname, msg=""):
    """Is (exception type name, message) a clean refusal - something the user can act on - rather than a crash?
    ValueError family, FileNotFound, Permission, and RuntimeErrors that don't wrap a crash (flp_edit's
    'op N failed: <Type>: ...')."""
    msg = msg or ""
    import re
    inner = [t for t in CRASH_TYPES if re.search(rf"(^|[^\w.]){re.escape(t)}[:(]", msg)]
    if tname in ("ValueError", "Refuse", "FileNotFoundError", "PermissionError", "UnicodeDecodeError",
                 "JSONDecodeError"):
        return not inner and not any(x in msg for x in INTERNAL_VALUE_ERRORS)
    if tname == "RuntimeError":
        return not inner and not any(x in msg for x in INTERNAL_VALUE_ERRORS)
    return False


# ============================================================================ build
def build(m, workdir, heavy=False, only=None):
    """Build the corpus into workdir -> {name: {"path", "bytes", "desc", "heavy", "tags"}}."""
    os.makedirs(workdir, exist_ok=True)
    scratch = tempfile.mkdtemp(dir=workdir, prefix="_scratch")
    ctx = {"m": m, "dir": scratch}
    out = {}
    for name, desc, hv, tags, fn in RECIPES:
        if (hv and not heavy) or (only and name not in only):
            continue
        p = fn(ctx)
        data = p.bytes() if isinstance(p, Proj) else p
        path = os.path.join(workdir, name + ".flp")
        with open(path, "wb") as f:
            f.write(data)
        out[name] = {"path": path, "bytes": len(data), "desc": desc, "heavy": hv, "tags": list(tags)}
    import shutil
    shutil.rmtree(scratch, ignore_errors=True)
    return out


def corpus_files(m, tmp, heavy=False):
    """Committed light corpus (tests/corpus) when present and complete, plus heavy projects built into tmp."""
    man = os.path.join(CORPUS_DIR, "manifest.json")
    have = {}
    if os.path.isfile(man):
        try:
            for name, meta in json.load(open(man, encoding="utf-8")).items():
                p = os.path.join(CORPUS_DIR, name + ".flp")
                if os.path.isfile(p):
                    have[name] = dict(meta, path=p)
        except (OSError, ValueError):
            have = {}
    want = [r[0] for r in RECIPES if heavy or not r[2]]
    missing = [n for n in want if n not in have]
    if missing and os.path.isfile(FIXTURE):                         # building needs the FL fixture (tests/data)
        have.update(build(m, tmp, heavy=heavy, only=set(missing)))
    return {n: have[n] for n in want if n in have}


def _import_server(roots):
    os.environ.setdefault("FLH_ROOTS", roots)
    os.environ.setdefault("LOCALAPPDATA", tempfile.mkdtemp())
    sys.path.insert(0, REPO)
    import fl_headless_mcp as m                                    # noqa: E402
    return m


def main(argv):
    heavy_dir = None
    if "--heavy" in argv:
        heavy_dir = argv[argv.index("--heavy") + 1]
    out = CORPUS_DIR
    tmp = tempfile.mkdtemp()
    m = _import_server(";".join([tmp, os.path.abspath(out)] + ([os.path.abspath(heavy_dir)] if heavy_dir else [])))
    os.makedirs(out, exist_ok=True)
    built = build(m, out, heavy=False)
    man = {n: {k: v for k, v in meta.items() if k != "path"} for n, meta in built.items()}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1, ensure_ascii=False)
    total = sum(v["bytes"] for v in built.values())
    print(f"{len(built)} light projects, {total / 1e6:.2f} MB -> {out}")
    if heavy_dir:
        hb = build(m, heavy_dir, heavy=True, only={r[0] for r in RECIPES if r[2]})
        print(f"{len(hb)} heavy projects -> {heavy_dir}")


if __name__ == "__main__":
    main(sys.argv[1:])
