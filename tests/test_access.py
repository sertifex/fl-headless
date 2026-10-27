"""flh_access: fl_describe (notes + synthetic render), fl_visualize (PNG validity + alt text), hearing-aware notes,
fl_second_opinion, glossary / plain-language text, step pacing, opt-in preferences, fl_where (mocked FL)."""
import base64, json, math, os, struct, sys, tempfile, time, wave, zlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import numpy as np  # noqa: E402
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_review, flh_score, flh_transcribe, flh_access as A  # noqa: E402
ns = vars(m)
for mod in (flh_store, flh_review, flh_score, flh_transcribe, A):
    mod.register(ns)
for t in ("fl_access", "fl_describe", "fl_visualize", "fl_second_opinion", "fl_safe_listening", "fl_where"):
    assert t in m.TOOLS, t
assert "fl_describe" in m.CROSS_PLATFORM and "fl_where" not in m.CROSS_PLATFORM

# ---------------------------------------------------------------- a synthetic song: notes (.mid + journal) and a render
P, BPM, BPB = 96, 100, 4
BAR = P * BPB
SECS = [("Intro", 1, 5), ("Verse", 5, 13), ("Chorus", 13, 21), ("Outro", 21, 25)]
CHORDS = [(57, 60, 64), (57, 62, 65), (56, 59, 64), (57, 60, 64)]          # Am Dm E Am (A minor)
ROOTS = [33, 38, 28, 33]


def song_notes():
    out = []                                                               # (pos, len, ch, key, vel)
    for name, a, b in SECS:
        for bar in range(a, b):
            t0 = (bar - 1) * BAR; ci = (bar - 1) % 4
            vel = {"Intro": 45, "Verse": 80, "Chorus": 112, "Outro": 40}[name]
            for k in CHORDS[ci]:
                out.append((t0, BAR, 1, k, vel))                            # pad
            if name in ("Verse", "Chorus"):
                for q in range(4):
                    out.append((t0 + q * P, P, 0, ROOTS[ci], vel))           # bass
                for q, k in enumerate((69, 72, 71, 68)):
                    out.append((t0 + q * P, P // 2, 2, k + (12 if name == "Chorus" else 0), vel))   # lead
            if name == "Chorus":
                for q in range(8):
                    out.append((t0 + q * P // 2, P // 4, 3, 42, vel))        # hats
                for q in (0, 2):
                    out.append((t0 + q * P, P // 4, 3, 36, vel))             # kick
                for q in (1, 3):
                    out.append((t0 + q * P, P // 4, 3, 38, vel))             # snare
    return out


NOTES = song_notes()
mid = os.path.join(tmp, "Song.mid")
tracks = []
for ch, nm, mc in ((0, "Bass", 0), (1, "Pad", 1), (2, "Lead", 2), (3, "Drums", 9)):
    evs = [(0, b"\xff\x51\x03" + int(60_000_000 / BPM).to_bytes(3, "big"))] if ch == 0 else []
    for pos, ln, c, k, v in NOTES:
        if c == ch:
            evs += [(pos, bytes([0x90 | mc, k, v])), (pos + ln, bytes([0x80 | mc, k, 0]))]
    tracks.append((nm, evs))
m.midi_write(mid, P, tracks)
flh_store.journal_update(mid, set={"sections": [{"name": n, "bar": a, "end_bar": b} for n, a, b in SECS]})

FS = 22050
bar_s = 60.0 / BPM * BPB


def render(path, gain=None, bright=0.0, width=0.3, clip=False):
    gain = gain or {"Intro": 0.03, "Verse": 0.12, "Chorus": 0.4, "Outro": 0.03}
    n = int(SECS[-1][2] - 1) * int(bar_s * FS)
    t = np.arange(n) / FS; L = np.zeros(n); R = np.zeros(n)
    rng = np.random.default_rng(1)
    for name, a, b in SECS:
        for bar in range(a, b):
            i0 = int((bar - 1) * bar_s * FS); i1 = int(bar * bar_s * FS); tt = t[i0:i1] - t[i0]
            ci = (bar - 1) % 4; g = gain[name]
            sig = sum(np.sin(2 * np.pi * 440 * 2 ** ((k - 69) / 12) * tt) for k in CHORDS[ci]) / 3
            if name in ("Verse", "Chorus"):
                sig = sig + 0.8 * np.sin(2 * np.pi * 440 * 2 ** ((ROOTS[ci] + 12 - 69) / 12) * tt)
            if name == "Chorus":                                            # kick thumps on each beat
                for q in range(4):
                    j0 = int(q * bar_s / 4 * FS); k_ = np.arange(min(int(0.15 * FS), len(tt) - j0)) / FS
                    sig[j0:j0 + len(k_)] += 1.2 * np.sin(2 * np.pi * 55 * k_) * np.exp(-k_ * 25)
            if bright:
                sig = sig + bright * rng.normal(0, 1, len(tt)) * 0.3 + bright * np.sin(2 * np.pi * 3150 * tt)
            L[i0:i1] += g * sig; R[i0:i1] += g * (sig * (1 - width) + width * np.roll(sig, 37))
    x = np.stack([L, R], 1)
    if clip:
        x = np.clip(x * 4, -1, 1)
    q = np.clip(np.round(x * 32767), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(FS); w.writeframes(q.tobytes())
    return path


wav = render(os.path.join(tmp, "Song.wav"))

# ---------------------------------------------------------------- fl_describe: notes only
t0 = time.time()
d = m.TOOLS["fl_describe"][0](project=mid)
print("describe notes", round(time.time() - t0, 2), "s\n" + d["text"])
txt = d["text"]
assert all(32 <= ord(c) < 127 or c == "\n" for c in txt) and "|" not in txt, "screen-reader-friendly plain text"
assert "A minor" in txt and "100 BPM" in txt, txt
assert "Chorus" in txt and "peaks at Chorus" in txt, txt
assert "same chords as Verse" in txt, txt
assert "Drums" in txt and "come in" in txt or "comes in" in txt, txt
assert "read from the notes" in txt and "interpreted" in txt and "measured:" not in txt, txt
ch = next(s for s in d["sections"] if s["name"] == "Chorus")
assert ch["energy"] == max(s["energy"] for s in d["sections"]), d["sections"]

# ---------------------------------------------------------------- fl_describe: render + notes (measured loudness)
t0 = time.time()
d2 = m.TOOLS["fl_describe"][0](project=mid, audio=wav, detail="full")
print("describe render+notes", round(time.time() - t0, 2), "s\n" + d2["text"])
t2 = d2["text"]
assert "measured:" in t2 and "read from the notes" in t2, t2
secs = {s["name"]: s for s in d2["sections"]}
assert max(secs, key=lambda n: secs[n]["lufs"]) == "Chorus", secs
assert secs["Intro"]["level"] in ("soft", "whisper-soft") and secs["Chorus"]["level"] == "the loudest, biggest point", secs
assert any("explodes" in c or "builds clearly" in c for c in secs["Chorus"]["changes"]), secs["Chorus"]
assert "Sound:" in t2 and "LUFS" in t2, t2
assert d2["numbers"]["bpm"] == 100 and d2["numbers"]["key"] == "A minor", d2["numbers"]

# audio only: tempo / key / loud parts measured (no sections -> time windows)
t0 = time.time()
d3 = A.describe_music(audio=wav)
print("describe audio only", round(time.time() - t0, 2), "s\n" + d3["text"])
assert d3["numbers"]["key"] in ("A minor", "C major"), d3["numbers"]          # relative keys share their notes
loudest = max(d3["sections"], key=lambda s: s.get("lufs") or -99)["name"]
a_, b_ = loudest.split("-")
to_s = lambda x: int(x.split(":")[0]) * 60 + int(x.split(":")[1])
assert to_s(a_) >= 12 * bar_s - 6 and to_s(b_) <= 20 * bar_s + 8, loudest     # inside the chorus (28.8-48 s)
assert "measured" in d3["text"] and "from the notes" not in d3["text"].split("How this")[0], d3["text"]

# one section
d4 = A.describe_music(project=mid, audio=wav, section="chorus")
assert [s["name"] for s in d4["sections"]] == ["Chorus"] and "Chorus" in d4["text"], d4

# what changed: louder chorus, brighter, new drums part
SECS_B = None
wav2 = render(os.path.join(tmp, "Song v2.wav"), gain={"Intro": 0.03, "Verse": 0.12, "Chorus": 0.6, "Outro": 0.03},
              bright=0.15)
dc = A.describe_music(project=mid, audio=wav2, compare=[mid, wav])
print("what changed:\n" + dc["text"])
assert dc["changed"] and "Chorus" in dc["text"] and "louder" in dc["text"] and "brighter" in dc["text"], dc["text"]
same = A.describe_music(project=mid, audio=wav, compare=[mid, wav])
assert not same["changed"] and "No measurable change" in same["text"], same["text"]
mid0 = os.path.join(tmp, "Song old.mid")
m.midi_write(mid0, P, [tr for tr in tracks if tr[0] != "Drums"])
flh_store.journal_update(mid0, set={"sections": [{"name": n, "bar": a, "end_bar": b} for n, a, b in SECS]})
dn = A.describe_music(project=mid, compare=mid0)
assert "New parts: Drums" in dn["text"] and "Chorus: Drums added" in dn["text"] and "Intro" not in dn["text"], dn["text"]

# ---------------------------------------------------------------- plain language / glossary
g = A.access_text("The chorus is 2 dB louder; true peak -0.3 dBTP and LUFS -9. Sidechain the bass.", "plain")
assert "dB (decibels" in g and "true peak (the very highest" in g and "LUFS (loudness units" in g, g
assert "Sidechain (one sound briefly ducking" in g, g
assert g.count("(decibels") == 1, g                                   # first use only
st_ = A.access_text("true peak -0.3 dBTP, the bass is fine", "standard")
assert "true peak (" in st_ and "bass (" not in st_, st_
assert A.access_text("LUFS -9", "pro") == "LUFS -9"
assert A.access_text("LUFS -9") == "LUFS -9"                          # no preference: unchanged
assert "fl_review" in A.access_text("run fl_review(audio=...) for LUFS", "plain")   # tool names untouched
assert A.access_glossary("a muddy low-mid and harsh presence", "plain").keys() >= {"mud", "harsh", "low-mid", "presence"}
pd = A.describe_music(project=mid, audio=wav, language="plain")
assert "LUFS (loudness units" in pd["text"] or "BPM (beats per minute" in pd["text"], pd["text"]

# ---------------------------------------------------------------- step pacing
s1 = A.access_steps(["render", "review", "fix"], pace="step_by_step")
assert s1["step"] == 1 and s1["of"] == 3 and s1["now"] == "render" and "next" in s1["next"], s1
assert A.access_steps(["a", "b"], pace="normal")["steps"] == ["1. a", "2. b"]
assert A.access_steps(["a", "b", "c"], pace="step_by_step", at=2)["next"] == "this is the last step"

# ---------------------------------------------------------------- preferences: opt-in only
assert A.access_prefs() == {}
r0 = m.TOOLS["fl_access"][0]()
assert r0["access"] is None and "set only what the user tells you" in r0["note"]
plain_res = {"issues": [{"severity": "low", "kind": "harsh", "where": "Chorus", "what": "Chorus is harsher"}]}
assert A.access_adjust(dict(plain_res)) == plain_res                   # nothing set: unchanged
try:
    A.fl_access(set={"hearing": "none"}); raise AssertionError("ambiguous 'none' accepted")
except ValueError as e:
    assert "ambiguous" in str(e)
try:
    A.fl_access(set={"eyes": "x"}); raise AssertionError
except ValueError:
    pass
r1 = A.fl_access(set={"hearing": "high frequency loss", "language": "simple"})
assert r1["saved"] == {"hearing": "reduced_highs", "language": "plain"}, r1
assert r1["stored_in"] == ["fl-headless state"] and flh_store.profile_get() == {}, r1      # no profile created
assert m._state()["access"]["hearing"] == "reduced_highs"
flh_store.profile_set("Album", {"finish": {"lufs": -14}})
r1b = A.fl_access(set={"language": "plain"})
assert "collection profile 'Album'" in r1b["stored_in"], r1b
prof = flh_store.profile_get()
assert prof["access"] == {"hearing": "reduced_highs", "language": "plain"} and prof["finish"] == {"lufs": -14}, prof
assert A.access_prefs()["hearing"] == "reduced_highs"
A.fl_access(set={"pace": "step by step"})
assert A.access_prefs() == {"hearing": "reduced_highs", "language": "plain", "pace": "step_by_step"}
A.fl_access(clear=["pace"])
assert "pace" not in A.access_prefs() and "pace" not in flh_store.profile_get()["access"]
assert A.access_text("LUFS -9").startswith("LUFS (loudness")         # the preference now applies

# ---------------------------------------------------------------- hearing-aware notes
adj = A.access_adjust({"issues": [{"severity": "low", "kind": "harsh", "where": "Chorus", "what": "Chorus is harsher"},
                                  {"severity": "low", "kind": "mud", "where": "Verse", "what": "Verse is muddier"}]},
                      user_says="sounds fine to me")
print("adjust:", json.dumps(adj["access"]))
assert adj["access"][0].startswith("You said it sounds fine") and any("high range" in n for n in adj["access"]), adj
assert not any("Verse is muddier" in n for n in adj["access"]), adj        # lows aren't flagged for reduced_highs
assert "glossary" in adj and "harsh" in adj["glossary"], adj
ok_res = A.access_adjust({"issues": [], "note": "all good"}, user_says="not good")
assert "access" not in ok_res
A.fl_access(set={"hearing": "deaf", "motor": "keyboard_only"})
lis = A.access_adjust({"note": "listen again with fl_listen to compare; click Save in the dialog"})
assert any('flh_call("fl_describe", {"compare":' in n or "fl_describe(" in n for n in lis["access"])   # unlisted: via flh_call and any("No mouse needed" in n for n in lis["access"]), lis
A.fl_access(set={"hearing": "reduced_highs"})

# fl_second_opinion: a bright, ringing render the user says sounds fine
harsh = render(os.path.join(tmp, "Harsh.wav"), bright=0.6)
so = m.TOOLS["fl_second_opinion"][0]("sounds fine to me", audio=harsh, project=mid)
print("second opinion:", so["say"])
kinds = {i["kind"] for i in so["issues"]}
assert so["agree"] is False and kinds & {"harsh", "resonance_high", "bright", "sibilance"}, so
assert "high range" in so["say"] and so["access"][0].startswith("You said it sounds fine"), so
clean = A.second_opinion("sounds good", audio=wav, project=mid)
assert not ({i["kind"] for i in clean["issues"]} & {"harsh", "resonance_high", "clipping", "phase"}), clean["issues"]
cl = A.second_opinion("it sounds distorted", audio=render(os.path.join(tmp, "Clip.wav"), clip=True))
assert cl["agree"] is True and any(i["kind"] == "clipping" for i in cl["issues"]), cl
nh = A.second_opinion("too harsh", audio=wav)
assert nh["agree"] is False and "don't show it" in nh["say"], nh["say"]

# silence: said plainly, nothing judged or guessed
sil = os.path.join(tmp, "Silent.wav")
with wave.open(sil, "wb") as w_:
    w_.setnchannels(2); w_.setsampwidth(2); w_.setframerate(FS); w_.writeframes(b"\0" * 4 * FS * 10)
ds = A.describe_music(audio=sil)
assert "silent" in ds["text"] and "Mood" not in ds["text"] and "key" not in ds["numbers"], ds
assert A.second_opinion("sounds fine", audio=sil)["issues"] == []
assert "silent" in A.visualize("energy", audio=sil)["alt"]

# ---------------------------------------------------------------- fl_visualize: valid PNGs + alt text


def png_ok(b64):
    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", raw[16:24])
    i, data_ = 8, b""
    while i < len(raw):
        n = struct.unpack(">I", raw[i:i + 4])[0]; typ = raw[i + 4:i + 8]; body = raw[i + 8:i + 8 + n]
        assert struct.unpack(">I", raw[i + 8 + n:i + 12 + n])[0] == zlib.crc32(typ + body) & 0xFFFFFFFF
        if typ == b"IDAT":
            data_ += body
        i += 12 + n
    px = zlib.decompress(data_)
    assert len(px) == h * (1 + 3 * w)
    rows = np.frombuffer(px, np.uint8).reshape(h, 1 + 3 * w)[:, 1:].reshape(h, w, 3)
    return w, h, len(np.unique(rows.reshape(-1, 3), axis=0))


for kind, kw in (("energy", {"project": mid, "audio": wav}), ("energy", {"project": mid}), ("loudness", {"audio": wav}),
                 ("spectrogram", {"audio": wav, "project": mid}), ("mix_balance", {"audio": wav}),
                 ("arrangement", {"project": mid}), ("piano_roll", {"project": mid, "part": "Lead", "section": "Chorus"}),
                 ("loudness", {"audio": wav, "project": mid, "section": "Verse"})):
    t0 = time.time()
    v = m.TOOLS["fl_visualize"][0](kind, **kw)
    took = time.time() - t0
    w, h, ncol = png_ok(v["_image_png"])
    print(f"visualize {kind:12s} {w}x{h} colors={ncol} {took:.2f}s alt: {v['alt'][:150]}")
    assert (w, h) == (1000, 500) and ncol >= 3 and len(v["alt"]) > 60, (kind, w, h, ncol)
    assert took < 8, (kind, took)
ve = A.visualize("energy", project=mid, audio=wav)
assert "Loudest: Chorus" in ve["alt"], ve["alt"]
assert "Biggest: Chorus" in A.visualize("energy", project=mid)["alt"]
va = A.visualize("arrangement", project=mid)
assert "Drums" in va["alt"] and "Chorus bar 13" in va["alt"], va["alt"]
vp = A.visualize("piano_roll", project=mid, part="Lead", section="Chorus")
assert "Lead (" in vp["alt"] and "C6" in vp["alt"], vp["alt"]
vl = A.visualize("loudness", audio=wav, theme="light", save=os.path.join(tmp, "pics"))
assert os.path.isfile(os.path.join(tmp, "pics.png")) or os.path.isfile(vl["saved"]), vl.get("saved")
os.makedirs(os.path.join(tmp, "pics2")); vs = A.visualize("energy", project=mid, save=os.path.join(tmp, "pics2"))
assert vs["saved"].endswith(os.path.join("pics2", "energy.png")) and open(vs["saved"], "rb").read(4) == b"\x89PNG"
try:
    A.visualize("energy", project=mid, save="/outside/roots/x.png"); raise AssertionError("wrote outside FLH_ROOTS")
except PermissionError:
    pass
try:
    A.visualize("spectrogram", project=mid); raise AssertionError
except ValueError as e:
    assert "needs audio" in str(e)
# vision preferences: low -> bigger; blind -> text first, no picture unless asked
A.fl_access(set={"vision": "low"})
vb = A.visualize("energy", project=mid)
assert vb["size"] == [1400, 700] and "_image_png" in vb
A.fl_access(set={"vision": "blind"})
vn = A.visualize("energy", project=mid)
assert "_image_png" not in vn and "alt text" in vn["image"] and vn["alt"], vn
assert "_image_png" in A.visualize("energy", project=mid, images="always")
A.fl_access(clear="all")
assert A.access_prefs() == {}, A.access_prefs()
# the dispatcher turns _image_png into MCP image content
rpc = m.handle({"method": "tools/call", "params": {"name": "fl_visualize", "arguments": {"kind": "arrangement",
                                                                                          "project": mid}}})
assert rpc["content"][0]["type"] == "image" and rpc["content"][0]["mimeType"] == "image/png", rpc["content"][0].keys()
body = json.loads(rpc["content"][1]["text"])
assert "alt" in body and "_image_png" not in body

# font: every glyph is 5x7 and distinct letters differ
assert all(A._glyph(c).shape == (7, 5) for c in A._FONT_SRC)
assert not np.array_equal(A._glyph("O"), A._glyph("0")) and A._glyph("a").sum() == A._glyph("A").sum()

# ---------------------------------------------------------------- safe listening
sl = m.TOOLS["fl_safe_listening"][0](minutes=75)
assert "cannot measure" in sl["what_we_measure"] and "break" in sl["session"] and "not medical" in sl["note"], sl
assert "devices" not in sl

# ---------------------------------------------------------------- fl_where with FL mocked
ns["fl_context"] = lambda: {"focused": "playlist", "focused_caption": "Playlist - Arrangement", "playing": False,
                            "bar": 14.0, "channel": {"index": 2, "name": "Lead", "plugin": "FLEX", "mixer_track": 5},
                            "selected_channels": [2, 3], "mixer_track": {"index": 5, "name": "Lead"},
                            "pattern": {"index": 3, "name": "Chorus"}, "playlist_selection_bars": [13.0, 17.0],
                            "project": mid, "window_title": "Song - FL Studio"}
ns["fl_song"] = lambda action: {"song_mode": True, "metronome": False, "recording": False, "bar": 14.0}
ns["fl_dialogs"] = lambda: [{"title": "FL Studio", "text": ["Save changes to Song?"], "buttons": ["Yes", "No", "Cancel"]}]
wh = A.fl_where()
print("where:\n" + wh["text"])
for frag in ("Focus: the playlist", "Stopped at bar 14", "in Chorus (bars 13-20)", "song mode", "channel: 2 'Lead'",
             "instrument FLEX", "mixer track 5", "Pattern: 3 'Chorus'", "bars 13 to 17", "Save changes to Song?",
             "buttons: Yes, No, Cancel", "Also selected channels: 3"):
    assert frag in wh["text"], (frag, wh["text"])
ns["fl_dialogs"] = lambda: []
assert "No popups open." in A.fl_where()["text"]
# M5: no invented tempo / key: noise and a steady tone have no pulse; a clean triad loop still reads its key
import fl_audio_edit as _AE  # noqa: E402
_fs = 44100; _t = np.arange(_fs * 20) / _fs
_nz = os.path.join(tmp, "Noise.wav"); _AE.save(_nz, np.random.default_rng(0).normal(0, 0.1, (_fs * 20, 2)), _fs, 24)
_tn = os.path.join(tmp, "Tone.wav"); _AE.save(_tn, np.repeat((0.2 * np.sin(2 * np.pi * 220 * _t))[:, None], 2, 1), _fs, 24)
for _p in (_nz, _tn):
    _d = A.describe_music(audio=_p)
    head = _d["text"].split("\n")[0]
    assert "no steady beat" in head and "BPM" not in head and not _d["numbers"].get("bpm"), head
    assert "Mood" not in _d["text"] or _d["numbers"].get("key"), _d["text"]
print("no-guess describe ok")
# ---------------------------------------------------------------- queued (lazy mode) edits: read the queued state, say so
import shutil  # noqa: E402
qsrc = os.path.join(tmp, "Queued Song.flp"); shutil.copy(os.path.join(HERE, "corpus", "base.flp"), qsrc)
d0 = A.fl_describe(project=qsrc)
assert d0["numbers"]["bpm"] == 60.0 and "pending_ops" not in d0, d0["numbers"]
m._pending_set(qsrc, [{"tool": "flp_set_tempo", "bpm": 150}])
before = open(qsrc, "rb").read()
dq = A.fl_describe(project=qsrc)
assert dq["numbers"]["bpm"] == 150.0 and dq["pending_ops"] == 1 and "queued" in dq["pending_note"], dq
assert m._shadow_path(qsrc) not in json.dumps(dq) and open(qsrc, "rb").read() == before, "file or paths leaked"
vq = A.fl_visualize(kind="arrangement", project=qsrc, images="never")
assert vq["pending_ops"] == 1 and "queued" in vq["pending_note"], vq
m._pending_set(qsrc, [])
assert "pending_ops" not in A.fl_visualize(kind="arrangement", project=qsrc, images="never")
print("queued-state describe / visualize ok")
print("ok")
