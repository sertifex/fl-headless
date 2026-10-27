"""flp_master_tone (flh_mixeng): the song's tone curve as a Fruity Parametric EQ 2 on the master, written into the .flp
through the plugin map's file_state layout - added on the master, bands written and decoded back from the file,
replace / merge / take-over semantics, journal entry, backup + restore, presets, flp_edit batching, the layout proof
(refuses when FL's default preset doesn't read right) and the live fallback routing. Run: python3 tests/test_master_tone.py"""
import math, os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
os.environ.setdefault("FLH_NO_WARM", "1")
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_mixeng as X  # noqa: E402
ns = vars(m)
flh_store.register(ns); X.register(ns)
assert "flp_master_tone" in m.TOOLS and m._OPEN_PROJECT_GUARD["flp_master_tone"] == ("project", "output")
TOOL = m.TOOLS["flp_master_tone"][0]
PEQ = X.PEQ
MP = X.plugin_map(PEQ)
assert MP["file_state"]["verified"] is False and MP["unit_bridges"][0]["controls"] == "band*_width"

# ---------------------------------------------------------------- fixtures: a song, FL's default PEQ2 preset
SRC = os.path.join(HERE, "corpus", "base.flp")
d0 = m._load(SRC); ev0 = m._events(d0)
_, _, fx0 = m._structure(d0)
v1 = fx0[(0, 1)]                                                  # FL 2026's older 'Fruity Parametric EQ' on the master
assert v1["plugin"] == "Fruity Parametric EQ"
# the evidence the shipped layout rests on: the older EQ's saved ints read as levels / types through the PEQ2 map
vals, raw = X.peq_decode(m._payload(d0, ev0[v1["state"]]), MP)
assert [vals[f"band{b}_type"] for b in range(1, 8)] == MP["band_default_type"], vals
assert abs(vals["band7_gain_db"] - 1.5) < 1e-9 and raw["band7_gain_db"] == 150
d3 = m._load(SRC); s3 = m._structure(d3)[2][(3, 0)]
assert abs(X.peq_decode(m._payload(d3, m._events(d3)[s3["state"]]), MP)[0]["band2_gain_db"] + 2.0) < 1e-9
assert not X._is_peq("Fruity Parametric EQ") and X._is_peq("Fruity parametric EQ 2")

k_name = v1["first"]
blk_end = v1["state"]


def make_fst(path, state):
    """A stand-in for FL's 'Fruity Parametric EQ 2.fst': the corpus EQ block, renamed, with `state`."""
    body = m._data_event(m.EV_PLUGIN_NAME, PEQ.encode("utf-16-le") + b"\x00\x00")
    for e in ev0[k_name + 1:blk_end]:
        body += m._raw(d0, e)
    body += m._data_event(m.EV_PLUGIN_STATE, state)
    with open(path, "wb") as f:
        f.write(b"FLhd" + struct.pack("<I", 6) + struct.pack("<hHH", 0, 0, 96) + b"FLdt" + struct.pack("<I", len(body))
                + body)
    return path


def default_state(types=(5, 6, 6, 6, 6, 6, 7), gains=(0,) * 7, extra=(0, 0, 0, 0, 0, 0, 0, 0)):
    fr = [int(round(X.to_norm(MP["controls"][f"band{b}_freq_hz"], hz) * 65536))
          for b, hz in enumerate(MP["band_defaults_hz"], 1)]
    return struct.pack("<36i", *(list(gains) + fr + [17500] * 7 + list(types) + list(extra)))


PDIR = os.path.join(tmp, "presets")
for sub in ("good", "bad"):
    os.makedirs(os.path.join(PDIR, sub))
GOOD = make_fst(os.path.join(PDIR, "good", PEQ + ".fst"), default_state())
BAD = make_fst(os.path.join(PDIR, "bad", PEQ + ".fst"), default_state(gains=(0, 0, 0, 0, 0, 0, 150)))
PRESET = {"path": GOOD}
m.fl_find_presets = lambda q, limit=40: {"presets": [PRESET["path"]] if q == PEQ else []}


def use_preset(p):
    PRESET["path"] = p; X._TONE_PROOF.clear()


def song(name):
    p = os.path.join(tmp, name + ".flp"); shutil.copyfile(SRC, p); return p


def call(**kw):
    m._CALL_BACKUPS.clear()                                       # one tool call each (the dispatcher does this)
    return TOOL(**kw)


def master_peqs(path):
    d = m._load(path); ev = m._events(d)
    return {s: m._payload(d, ev[k]) for s, (f, mk, nm, k) in X._tone_slots(d, ev).items() if X._is_peq(nm)}


def band(path, slot, b):
    st = master_peqs(path)[slot]
    v, _ = X.peq_decode(st, MP)
    return {"type": v[f"band{b}_type"], "hz": v[f"band{b}_freq_hz"], "gain_db": v[f"band{b}_gain_db"],
            "q": X.oct_to_q(v[f"band{b}_width"])}


TODAY = [{"type": "peak", "hz": 300, "gain_db": -3, "q": 0.9}, {"type": "peak", "hz": 150, "gain_db": -1.5, "q": 0.9},
         {"type": "high_shelf", "hz": 4500, "gain_db": 3}]

# ---------------------------------------------------------------- input checks (nothing written)
p = song("Checks"); orig = open(p, "rb").read()
for bad, why in (([{"type": "peak", "hz": 300, "gain_db": 15}], "outside"), ([{"type": "shelf", "hz": 300}], "use one of"),
                 ([{"type": "peak", "hz": 5}], "outside"), ([{"type": "peak", "hz": 300, "q": 40}], "outside"),
                 ([{"type": "peak", "hz": 100 + i} for i in range(8)], "has 7"), ([{"type": "peak"}], "hz")):
    try:
        call(project=p, bands=bad); raise AssertionError(bad)
    except ValueError as e:
        assert why in str(e), (bad, e)
for kw, why in (({"bands": TODAY, "preset": "warm"}, "not both"), ({"preset": "nope"}, "no master tone preset"),
                ({"bands": TODAY, "slot": 0}, "Fruity Convolver"), ({"bands": TODAY, "slot": 42}, "no effect slot")):
    try:
        call(project=p, **kw); raise AssertionError(kw)
    except ValueError as e:
        assert why in str(e), (kw, e)
assert open(p, "rb").read() == orig
assert X.tone_bands([{"type": "HPF", "hz": 30, "gain_db": 4}])[0] == {"type": "high_pass", "hz": 30.0, "gain_db": 0.0,
                                                                     "q": None}
assert sorted(X.tone_presets()) == ["bright", "clean", "warm"]
a = X.tone_assign(X.tone_bands(TODAY))
assert {b: (x["type"], x["hz"]) for b, x in a.items()} == {2: ("peak", 150), 3: ("peak", 300), 7: ("high_shelf", 4500)}
a = X.tone_assign(X.tone_bands([{"type": "peak", "hz": 100 * (i + 1)} for i in range(6)] + [{"type": "high_pass", "hz": 30}]))
assert a[1]["type"] == "high_pass" and [a[b]["hz"] for b in range(2, 8)] == [100, 200, 300, 400, 500, 600]
print("inputs ok")

# ---------------------------------------------------------------- read: nothing of ours yet
r = call(project=p)
assert r["master_eqs"] == [] and r["journal"] is None, r                 # the older 'Fruity Parametric EQ' isn't PEQ2

# ---------------------------------------------------------------- write: added on the master, bands in the file
p = song("Song A"); orig = open(p, "rb").read()
r = call(project=p, bands=TODAY)
assert r["added"] and r["slot"] == 2, r                                    # slots 0 (Convolver), 1 (old EQ) are taken
assert r["read_back"]["ok"], r["read_back"]
assert m._structure(m._load(p))[2][(0, 2)]["plugin"] == PEQ               # header points at master slot 2
assert list(master_peqs(p)) == [2]
b2, b3, b7 = band(p, 2, 2), band(p, 2, 3), band(p, 2, 7)
assert b2["type"] == "Peaking" and abs(b2["hz"] - 150) / 150 < 0.002 and abs(b2["gain_db"] + 1.5) < 1e-9, b2
assert b3["type"] == "Peaking" and abs(b3["hz"] - 300) / 300 < 0.002 and abs(b3["gain_db"] + 3.0) < 1e-9, b3
assert abs(b2["q"] - 0.9) < 0.01 and abs(b3["q"] - 0.9) < 0.01, (b2, b3)  # Q -> % (unit_bridges) -> octaves -> Q
assert b7["type"] == "High shelf" and abs(b7["hz"] - 4500) / 4500 < 0.002 and abs(b7["gain_db"] - 3) < 1e-9, b7
for b in (1, 4, 5, 6):                                                      # unused bands: FL's default, flat
    x = band(p, 2, b)
    assert x["gain_db"] == 0 and x["type"] == MP["band_default_type"][b - 1], (b, x)
assert "unit_bridges" not in r.get("width_note", "") and "table" in r["width_note"], r.get("width_note")
assert r["unverified"] and "PC-VERIFY" in r["unverified"][0]
assert [x["band"] for x in r["read_back"]["eq"]] == [2, 3, 7]
# the rest of the project is untouched: same events except the inserted block
d1 = m._load(p)
assert len(m._events(d1)) == len(ev0) + (blk_end - k_name + 1)
assert m._structure(d1)[2][(0, 1)]["plugin"] == "Fruity Parametric EQ"
# journal + backup (reversible)
j = flh_store.journal_get(p)["master_tone"]
assert j["slot"] == 2 and j["via"] == "file" and j["plugin"] == PEQ and len(j["bands"]) == 3 and "preset" not in j, j
assert any("master tone" in str(x) for x in flh_store.journal_get(p).get("mix_log", []))
vers = m.flp_versions(p)
assert r["backup"] == vers[-1]["backup"] and open(r["backup"], "rb").read() == orig and "flp_restore" in r["undo"]
# read mode now matches the journal
rd = call(project=p)
assert rd["matches_journal"] is True and rd["master_eqs"][0]["slot"] == 2 and "layout" not in rd, rd
assert X.master_tone_check(p) == {"ok": True, "slot": 2, "bands": 3}
import flh_finalize as F  # noqa: E402
F.register(ns)
assert F._plan(p, None, None, None, {})["tone"]["ok"] is True                # fl_finalize sees the tone in the song
assert F._plan(song("No Tone"), None, None, None, {})["tone"] is None      # no journal tone: no check, no note
print("write ok")

# ---------------------------------------------------------------- replace: our EQ is rewritten, never a second one
r = call(project=p, preset="warm")
assert not r["added"] and r["slot"] == 2 and "took_over" not in r, r
assert list(master_peqs(p)) == [2]
assert band(p, 2, 1)["type"] == "Low shelf" and abs(band(p, 2, 1)["gain_db"] - 2) < 1e-9
assert band(p, 2, 3)["gain_db"] == 0                                        # the old 300 Hz cut is gone
assert abs(band(p, 2, 2)["hz"] - 3500) / 3500 < 0.002 and abs(band(p, 2, 7)["gain_db"] + 1.5) < 1e-9
j = flh_store.journal_get(p)["master_tone"]
assert j["preset"] == "warm" and [b["hz"] for b in j["bands"]] == [120, 3500, 9000], j

# replace=false: merged into our own EQ (same type within 1/3 octave replaces, others stay)
r = call(project=p, bands=[{"type": "peak", "hz": 3200, "gain_db": -2, "q": 1.2}, {"type": "high_pass", "hz": 25}],
         replace=False)
assert r["slot"] == 2 and not r["added"]
hz = sorted(b["hz"] for b in r["bands"])
assert hz == [25, 120, 3200, 9000], r["bands"]
assert band(p, 2, 1)["type"] == "High pass" and band(p, 2, 2)["type"] == "Low shelf"
assert abs(band(p, 2, 3)["gain_db"] + 2) < 1e-9 and abs(band(p, 2, 3)["q"] - 1.2) < 0.01
assert "preset" not in flh_store.journal_get(p)["master_tone"]             # the record is replaced, not merged
# flat: bands=[] keeps our EQ, every band back to default
r = call(project=p, bands=[])
assert r["read_back"]["eq"] == [] and all(band(p, 2, b)["gain_db"] == 0 for b in range(1, 8))
print("replace ok")

# ---------------------------------------------------------------- a user's own PEQ2 on the master
p = song("Song B")
call(project=p, bands=TODAY)                                               # stands in for the user's EQ ...
os.remove(flh_store.journal_path(p))                                      # ... nothing says fl-headless put it there
user_state = master_peqs(p)[2]
r = call(project=p, preset="bright", replace=False)                        # never touched: a new EQ in a free slot
assert r["added"] and r["slot"] == 3 and master_peqs(p)[2] == user_state, r
try:
    call(project=p, preset="clean", replace=False, slot=2); raise AssertionError("slot of the user's EQ")
except ValueError as e:
    assert "replace=true" in str(e)
r = call(project=p, preset="clean", replace=False)                         # ours (slot 3) is reused
assert r["slot"] == 3 and not r["added"] and master_peqs(p)[2] == user_state
p = song("Song C")
call(project=p, bands=TODAY); os.remove(flh_store.journal_path(p))
r = call(project=p, preset="warm")                                         # replace=true takes it over
assert r["slot"] == 2 and not r["added"] and "took_over" in r and list(master_peqs(p)) == [2], r
print("take over ok")

# ---------------------------------------------------------------- a limiter on the master: the EQ goes before it
p = song("Song D")
d = m._load(p); ev = m._events(d)
_, marker = m._mixer_slots(d, ev)[(0, 4)]
lim = m._data_event(m.EV_PLUGIN_NAME, "Fruity Limiter".encode("utf-16-le") + b"\x00\x00")
for e in ev0[k_name + 1:blk_end + 1]:
    if e["id"] == m.EV_PLUGIN_HEADER:
        h = bytearray(m._payload(d0, e)); h[0:8] = struct.pack("<ii", 0, 4); lim += m._data_event(e["id"], bytes(h))
    else:
        lim += m._raw(d0, e)
m._save(m._splice(d, ev, [(marker, marker, lim)]), p)
d = m._load(p); ev = m._events(d)
for s in (2, 3):                                                            # fill the free slots before it
    _, marker = m._mixer_slots(d, ev)[(0, s)]
    m._save(m._splice(d, ev, [(marker, marker, lim.replace(struct.pack("<ii", 0, 4), struct.pack("<ii", 0, s)))]), p)
    d = m._load(p); ev = m._events(d)
r = call(project=p, bands=TODAY)
assert r["slot"] == 5 and "after Fruity Limiter" in r["warning"], r
print("placement ok")

# ---------------------------------------------------------------- reversible: flp_restore brings the song back
p = song("Song E"); orig = open(p, "rb").read()
r = call(project=p, bands=TODAY)
m._CALL_BACKUPS.clear()
m.flp_restore(p, r["backup"])
assert open(p, "rb").read() == orig and master_peqs(p) == {}
assert X.master_tone_check(p)["ok"] is False and "isn't in the project" in X.master_tone_check(p)["note"]

# ---------------------------------------------------------------- flp_edit batches it (one FL save / reload)
p = song("Song F")
r = m.flp_edit(p, [{"tool": "flp_master_tone", "preset": "clean"}])
assert r["results"][0]["tool"] == "flp_master_tone" and r["results"][0]["slot"] == 2, r
assert abs(band(p, 2, 7)["gain_db"] - 3) < 1e-9
# output= writes a copy; the copy's journal gets the record
src = song("Song G"); out = os.path.join(tmp, "Song G v2.flp")
r = call(project=src, bands=TODAY, output=out)
assert r["output"] == out and master_peqs(out) and not master_peqs(src)
assert flh_store.journal_get(out)["master_tone"]["slot"] == 2
print("batch ok")

# ---------------------------------------------------------------- layout proof fails: nothing written (FL closed)
use_preset(BAD)
p = song("Song H"); orig = open(p, "rb").read()
try:
    call(project=p, bands=TODAY); raise AssertionError("unproven layout")
except X.ToneLayoutUnknown as e:
    assert "band7_gain_db reads 1.5" in str(e) and "Nothing was changed" in str(e), e
assert open(p, "rb").read() == orig and not m.flp_versions(p)
try:                                                                         # in a batch: the op fails, file kept
    m.flp_edit(p, [{"tool": "flp_master_tone", "bands": TODAY}]); raise AssertionError("batch")
except RuntimeError as e:
    assert "layout" in str(e)
assert open(p, "rb").read() == orig
assert "layout" in call(project=p)                                          # read says the layout is unconfirmed

# ---------------------------------------------------------------- ... with FL open on the song: set live instead
calls = {}
orig_fns = {k: getattr(X, k) for k in ("_state_for", "_add_effects", "plugin_set_named", "_fl_gone_check")}
X._fl_gone_check = lambda *a: None
X._state_for = lambda tracks: {"mixer": [{"index": 0, "effects": [{"slot": 0, "name": "Fruity Convolver"},
                                                                    {"slot": 1, "name": "Fruity Parametric EQ"}]}]}
X._add_effects = lambda adds: calls.setdefault("add", adds)


def fake_set(track, slot, plugin, values, tolerance=None):
    calls["set"] = (track, slot, plugin, dict(values))
    return {"ok": True, "set": [{"control": k, "text": str(v), "ok": True} for k, v in values.items()], "skipped": [],
            "undo": [{"track": track, "slot": slot, "param": 0, "before": 0.5}]}
X.plugin_set_named = fake_set
m.fl_running = lambda: True
m._project_is_open = lambda path: os.path.normcase(path) == os.path.normcase(p)
m._save_in_fl_and_wait = lambda project, wait_s=8.0, **k: calls.setdefault("saved", project) is not None
m._CALL_BACKUPS.clear()
r = TOOL(project=p, bands=TODAY)
assert r["via"] == "live" and r["slot"] == 2 and r["added"] and r["saved_in_file"], r
assert calls["add"] == [(0, 2, PEQ)] and calls["saved"] == p
t_, s_, plug, vals = calls["set"]
assert (t_, s_, plug) == (0, 2, PEQ)
assert vals["band2_type"] == "Peaking" and vals["band2_freq_hz"] == 150 and vals["band2_width"] == "0.9 Q"
assert vals["band7_type"] == "High shelf" and vals["band4_gain_db"] == 0.0 and vals["band1_type"] == "Low shelf"
assert flh_store.journal_get(p)["master_tone"]["via"] == "live" and m.flp_versions(p)    # backup before the change
for k, f in orig_fns.items():
    setattr(X, k, f)
print("layout proof + live ok")
print("ok")
