"""flh_extras: collaborator pack (samples / MIDI / stems mocked / README / zip / never overwrite), recipes (save, list,
placeholders, $prev, destructive confirm, dry run + estimate, missing tools, banned tools, jobs), teaching mode
(recent-calls log, fallbacks, levels, handbook hook) and flh_resume (summary, next steps, size budget)."""
import json, os, shutil, sys, tempfile, threading, time, wave, zipfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
FIX = os.path.join(os.environ.get("FLH_TEST_DATA") or os.path.join(HERE, "data"), "audio_clip_fl2026.flp")
if not os.path.isfile(FIX):                                       # a public clone: the scrubbed committed copy
    FIX = os.path.join(HERE, "corpus", "base.flp")
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "la")
import fl_headless_mcp as m  # noqa: E402
import flh_store, flh_extras as X  # noqa: E402
if not os.path.isfile(FIX):
    print("skipped: no fixture (tests/data/audio_clip_fl2026.flp or tests/corpus/base.flp)"); sys.exit(0)
ns = vars(m)
for mod in m.EXTENSIONS:
    if mod in sys.modules:
        sys.modules[mod].register(ns)
X.register(ns)
for t in ("flh_collab_pack", "flh_recipe_save", "flh_recipe_run", "flh_recipe_list", "flh_teach", "flh_resume"):
    assert t in m.TOOLS, t
    assert m.TOOLS[t][2]["type"] == "object"


def wav(path, seconds=0.2, rate=44100):
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * 2 * int(rate * seconds))
    return path


def song(name="My Song"):
    d = os.path.join(tmp, name); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".flp"); shutil.copy(FIX, p)
    return p


# ============================================================================ collab pack
p = song()
smp_dir = os.path.join(tmp, "elsewhere"); os.makedirs(smp_dir, exist_ok=True)
hit = wav(os.path.join(smp_dir, "hit.wav"))
m.flp_add_audio_clip(p, p, hit, bar=1)
unrelated = wav(os.path.join(smp_dir, "not_used.wav"))
flh_store.journal_update(p, set={"sections": [{"name": "Intro", "bar": 1, "end_bar": 9}, {"name": "Verse", "bar": 9}],
                                 "key": "G minor"},
                         add={"likes": "warm piano", "dislikes": "bright hats", "notes": "PRIVATE-NOTE-123"})

stem_calls = []


def fake_stems(project, output_dir=None, channels=None, formats="wav", close_fl=True, reopen=True):
    stem_calls.append((project, output_dir, formats))

    def job():
        os.makedirs(output_dir, exist_ok=True)
        out = [wav(os.path.join(output_dir, f"stem {i}.wav"), 0.1, 48000) for i in range(3)]
        return {"stems": out, "folder": output_dir}
    return ns["_start_job"]("stems", job)


orig_wo = ns["_windows_only"]
ns["_windows_only"] = lambda n: False
ns["fl_render_stems"] = fake_stems
orig_mtime = os.path.getmtime(p); orig_bytes = open(p, "rb").read()

r = X.flh_collab_pack(p, notes="Bass is a placeholder - replace freely.", background=False)
out = r["folder"]
assert os.path.basename(out) == "My Song - collab" and os.path.isdir(out), r
assert open(p, "rb").read() == orig_bytes, "original project untouched"
names = sorted(os.listdir(out))
for want in ("My Song.flp", "README.txt", "journal.json", "MIDI", "Samples", "Stems"):
    assert want in names, (want, names)
assert sorted(os.listdir(os.path.join(out, "Samples"))) == ["hit.wav"], "only referenced samples"
assert m.flp_collect_samples  # same logic as the core tool (factory-aware here)
assert "not_used.wav" not in json.dumps(r)
assert r["samples"]["collected"] == 1 and r["samples"]["missing"] == ["loop.wav"], r["samples"]
assert any("missing" in w for w in r["warnings"])
mids = sorted(os.listdir(os.path.join(out, "MIDI")))
assert "My Song - all parts.mid" in mids and len(mids) == 1 + len(m.flp_info(p)["notes_per_channel"]), mids
assert all(open(os.path.join(out, "MIDI", f), "rb").read(4) == b"MThd" for f in mids)
assert r["stems"] == 3 and stem_calls[0][1] == os.path.join(out, "Stems")
readme = open(os.path.join(out, "README.txt"), encoding="utf-8").read()
for want in ("Tempo: 60 BPM", "Key: G minor", "Intro: bar 1-9", "Verse: bar 9", "Splice INSTRUMENT", "Fruity Compressor",
             "48000 Hz, 16-bit, stereo", "Bass is a placeholder", "Samples/ - the 1 sample(s)", "LUFS"):
    assert want in readme, (want, readme)
assert "comes with every FL Studio edition" in readme
assert tmp not in readme and "D:\\" not in readme, "no local paths in the README"
pub = json.load(open(os.path.join(out, "journal.json")))
assert pub["song"] == "My Song" and [s["name"] for s in pub["sections"]] == ["Intro", "Verse"]
assert "notes" not in pub and "likes" not in pub and "PRIVATE-NOTE-123" not in json.dumps(pub)
zp = r["zip"]; assert zp == out + ".zip" and os.path.isfile(zp)
with zipfile.ZipFile(zp) as z:
    zn = z.namelist()
    assert "My Song - collab/My Song.flp" in zn and "My Song - collab/Samples/hit.wav" in zn
    assert z.getinfo("My Song - collab/Stems/stem 0.wav").compress_type == zipfile.ZIP_STORED
assert set(r["sizes_mb"]) >= {"project", "samples", "midi", "stems", "folder", "zip"}
assert "steps_s" in r and "stems" in r["steps_s"]
# the copy points at the collected sample
refs = X._sample_refs(os.path.join(out, "My Song.flp"))
assert any(os.path.normcase(os.path.join(out, raw.replace("\\", "/"))) == os.path.normcase(os.path.join(out, "Samples", "hit.wav"))
           for raw, _ in refs)
# M8: the pack's project stores RELATIVE sample paths (no user/folder names of this PC)
assert all(not os.path.isabs(raw.replace("\\", "/")) and ":" not in raw for raw, _ in refs if not raw.startswith("%")), refs
assert any(raw == "Samples\\hit.wav" for raw, _ in refs), refs
assert tmp.encode("utf-16-le") not in open(os.path.join(out, "My Song.flp"), "rb").read(), "local path in the pack"

# second pack: a new folder, the first is never overwritten; private adds notes; background job; no stems/zip
first_files = sorted(os.listdir(out))
r2 = X.flh_collab_pack(p, stems=False, zip=False, private=True, midi=False)
assert r2["folder"] == out + " (2)" and sorted(os.listdir(out)) == first_files
assert "zip" not in r2 and not os.path.exists(out + " (2).zip") and not os.path.isdir(os.path.join(r2["folder"], "Stems"))
pub2 = json.load(open(os.path.join(r2["folder"], "journal.json")))
assert pub2["notes"] == ["PRIVATE-NOTE-123"] and pub2["likes"] == ["warm piano"]
assert "No stems in this pack." in open(os.path.join(r2["folder"], "README.txt"), encoding="utf-8").read()
j = X.flh_collab_pack(p)                       # stems -> background job by default
assert j["status"] == "running" and j["folder"] == out + " (3)"
done = m.fl_job(j["job"], wait_s=30)
assert done["status"] == "done", done
assert done["result"]["stems"] == 3 and os.path.isfile(out + " (3).zip")
# stems failing never sinks the pack
ns["fl_render_stems"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("FL busy"))
r4 = X.flh_collab_pack(p, background=False, zip=False)
assert r4["stems"].startswith("failed") and os.path.isfile(os.path.join(r4["folder"], "README.txt"))
# Windows-only stems on this OS: skipped, said so
ns["_windows_only"] = lambda n: n.startswith("fl_")
r5 = X.flh_collab_pack(p, zip=False, midi=False, samples=False)
assert "skipped" in r5["stems"] and r5["samples"].startswith("not included"), r5
refs5 = X._sample_refs(os.path.join(r5["folder"], "My Song.flp"))
assert refs5 and all(raw.startswith("Samples\\") or raw.startswith("%") for raw, _ in refs5), refs5
# outside FLH_ROOTS: refused before anything is written
try:
    X.flh_collab_pack(p, out_dir=os.path.join(os.path.dirname(tmp), "nope-outside-roots"), stems=False)
    raise AssertionError("should refuse")
except PermissionError:
    pass
# sample file-name clashes: distinct names in the pack, each channel keeps its own sound
d2 = os.path.join(tmp, "a"); d3 = os.path.join(tmp, "b"); os.makedirs(d2); os.makedirs(d3)
q = song("Clash")
m.flp_add_audio_clip(q, q, wav(os.path.join(d2, "kick.wav")), bar=1)
m.flp_add_audio_clip(q, q, wav(os.path.join(d3, "kick.wav"), 0.3), bar=2)
rc = X.flh_collab_pack(q, stems=False, zip=False, midi=False)
assert any("shared a file name" in w for w in rc["warnings"]) and rc["samples"]["collected"] == 2, rc
assert sorted(os.listdir(os.path.join(rc["folder"], "Samples"))) == ["kick (2).wav", "kick.wav"]
got = {os.path.basename(raw.replace("\\", "/")): os.path.getsize(os.path.join(rc["folder"], raw.replace("\\", "/")))
       for raw, _ in X._sample_refs(os.path.join(rc["folder"], "Clash.flp"))
       if os.path.isfile(os.path.join(rc["folder"], raw.replace("\\", "/")))}
assert got["kick.wav"] != got["kick (2).wav"], got
# FL's own factory samples stay referenced, never copied into the pack
f = song("Factory")
m.flp_add_audio_clip(f, f, wav(os.path.join(d2, "snare.wav")), bar=1)
d = m._load(f); ev = m._events(d)
k = next(k for k, e in enumerate(ev) if e["id"] == m.EV_SAMPLE_PATH and "snare" in m._text16(m._payload(d, e)))
fac = "%FLStudioFactoryData%\\Data\\Patches\\Packs\\Drums\\Snares\\FL Snare.wav"
m._save(m._splice(d, ev, [(k, k + 1, m._data_event(m.EV_SAMPLE_PATH, fac.encode("utf-16-le") + b"\x00\x00"))]), f)
rf = X.flh_collab_pack(f, stems=False, zip=False, midi=False)
sd = os.path.join(rf["folder"], "Samples")
assert rf["samples"]["fl_factory"] == 1 and (not os.path.isdir(sd) or "FL Snare.wav" not in os.listdir(sd)), rf
assert any(raw == fac for raw, _ in X._sample_refs(os.path.join(rf["folder"], "Factory.flp")))
assert "come with FL Studio itself" in open(os.path.join(rf["folder"], "README.txt"), encoding="utf-8").read()
ns["_windows_only"] = lambda n: False

# ============================================================================ recipes
L = X.flh_recipe_list()
shipped = {r_["name"]: r_ for r_ in L["recipes"]}
for nm in ("quick_master", "vocal_session", "clean_and_check", "share_with_collaborator"):
    assert nm in shipped and shipped[nm]["source"] == "shipped", nm
    assert not shipped[nm].get("missing_tools"), (nm, shipped[nm])
assert shipped["quick_master"].get("needs_confirm") and not shipped["clean_and_check"].get("needs_confirm")
# every shipped recipe references only real tools with real argument names
for f in os.listdir(X.SHIPPED_RECIPES):
    rec = json.load(open(os.path.join(X.SHIPPED_RECIPES, f)))
    for s in rec["steps"]:
        assert s["tool"] in m.TOOLS, (f, s["tool"])
        props = m.TOOLS[s["tool"]][2].get("properties") or {}
        assert all(k in props for k in s["args"]), (f, s)

calls = []


def mk(name, ret=None):
    def fn(**kw):
        calls.append((name, kw))
        return ret(kw) if callable(ret) else (ret or {"ok": True, "tool": name})
    return fn


saved = {k: m.TOOLS[k] for k in ("fl_doctor", "flp_remove_channels", "fl_review", "fl_mix_engineer", "fl_finalize")}
m.TOOLS["fl_doctor"] = (mk("fl_doctor", {"issues": [1, 2]}),) + saved["fl_doctor"][1:]
m.TOOLS["flp_remove_channels"] = (mk("flp_remove_channels", {"removed": []}),) + saved["flp_remove_channels"][1:]
m.TOOLS["fl_review"] = (mk("fl_review", {"verdict": "fine", "issues": []}),) + saved["fl_review"][1:]
m.TOOLS["fl_mix_engineer"] = (mk("fl_mix_engineer", {"proposal": []}),) + saved["fl_mix_engineer"][1:]
m.TOOLS["fl_finalize"] = (mk("fl_finalize", {"final": "x.flac"}),) + saved["fl_finalize"][1:]

def run_forced(*a, **k):
    """background=False with a long step: runs as a job anyway (says so); wait for its result."""
    t = time.time()
    j = X.flh_recipe_run(*a, **k)
    assert time.time() - t < 3, "background=False blocked"
    assert j.get("job") and "background=false ignored" in j["background_ignored"], j
    res_ = m.fl_job(j["job"], wait_s=30)
    assert res_["status"] == "done", res_
    return res_["result"]


# clean_and_check has a long step (fl_review): background=false is ignored, it runs as a job
r = run_forced("clean_and_check", project=p, background=False)
assert r["ok"] and r["ran"] == 3, r
assert [c[0] for c in calls] == ["fl_doctor", "flp_remove_channels", "fl_review"]
assert calls[1][1] == {"project": p, "unused": True, "dry_run": True}
# missing required param
r = X.flh_recipe_run("clean_and_check")
assert r["ok"] is False and "missing param" in r["cannot_run"][0], r
# destructive needs confirm; dry run shows the plan (+ estimate when ns["estimate"] exists)
calls.clear()
r = X.flh_recipe_run("quick_master", params={"project": p}, background=False)
assert r["ok"] is False and r["needs_confirm"][0]["tool"] == "fl_finalize" and not calls, r
ns["estimate"] = lambda tool, args: {"fl_review": 30, "fl_mix_engineer": 70, "fl_finalize": 120}.get(tool)
r = X.flh_recipe_run("quick_master", project=p, dry_run=True)
assert r["dry_run"] and r["estimate_s"] == 220 and r["needs_confirm"] == [3] and not calls, r
assert "goal" not in json.dumps(r["plan"][1]["args"]), "an optional param not given is dropped"
del ns["estimate"]
r = run_forced("quick_master", project=p, goal="vocals clearer", confirm=True, background=False)
assert r["ok"] and [c[0] for c in calls] == ["fl_review", "fl_mix_engineer", "fl_finalize"], (r, calls)
assert calls[1][1] == {"project": p, "goal": "vocals clearer", "apply": False}
# long steps -> background job by default; steps' own jobs are awaited
calls.clear()


def slow_review(kw):
    return ns["_start_job"]("review", lambda: (time.sleep(0.3), {"verdict": "ok", "issues": 0})[1])


m.TOOLS["fl_review"] = (mk("fl_review", slow_review),) + saved["fl_review"][1:]
j = X.flh_recipe_run("clean_and_check", project=p)
assert j["status"] == "running" and j["job"].startswith("recipe"), j
res = m.fl_job(j["job"], wait_s=30)
assert res["status"] == "done" and res["result"]["ok"], res
assert res["result"]["steps"][2]["result"]["verdict"] == "ok", "job result replaces the job handle"
# inline: a job still running after the wait stops later steps, honestly
assert X.STEP_WAIT_S <= 5                                       # an inline run never waits long on a step's job
X.STEP_WAIT_S = 0.05
doc0 = m.TOOLS["fl_doctor"]
m.TOOLS["fl_doctor"] = (mk("fl_doctor", lambda kw: ns["_start_job"]("doctor", lambda: time.sleep(1.0) or {})),) \
    + saved["fl_doctor"][1:]
X.flh_recipe_save("doc_then_doc", [{"tool": "fl_doctor", "args": {"project": "${project}"}},
                                    {"tool": "flp_remove_channels", "args": {"project": "${project}", "dry_run": True}}],
                  "doctor then dry run")
r = X.flh_recipe_run("doc_then_doc", project=p, background=False)
assert r["ok"] is False and r["steps"][0]["status"] == "still running" and "not run" in r["steps"][0]["stopped"], r
X.STEP_WAIT_S = 5
m.TOOLS["fl_doctor"] = doc0
# background=False on a recipe estimated long (no LONG tool): a job too
ns["estimate"] = lambda tool, args: {"fl_doctor": 30}.get(tool)
r = run_forced("doc_then_doc", project=p, background=False)
assert r["ok"], r
del ns["estimate"]
m.TOOLS["fl_review"] = (mk("fl_review", {"verdict": "fine"}),) + saved["fl_review"][1:]

# recipe steps get the same argument checks as a direct call: "${track}" given as "5" reaches the tool as 5
m.TOOLS["flp_t_int"] = (mk("flp_t_int"), "test tool", ns["_schema"](project=ns["_FLP"], track=ns["_INT"]("a track")))
X.flh_recipe_save("int_arg", [{"tool": "flp_t_int", "args": {"project": "${project}", "track": "${track}"}}], "x")
calls.clear(); r = X.flh_recipe_run("int_arg", project=p, track="5", background=False)
assert r["ok"] and calls[-1] == ("flp_t_int", {"project": p, "track": 5}), (r, calls)
calls.clear(); r = X.flh_recipe_run("int_arg", project=p, track="five", background=False)
assert not r["ok"] and "expects" in r["steps"][0]["error"] and not calls, r
del m.TOOLS["flp_t_int"]

# save: placeholders become params, $prev passes through, banned / bad names refused, overwrite guarded
s = X.flh_recipe_save("My Check", [{"tool": "fl_doctor", "args": {"project": "${song}"}},
                                   {"tool": "fl_review", "args": {"project": "$prev.project", "mode": "${mode}"}}],
                      "doctor then review", params={"mode": {"default": "auto"}})
assert s["saved"] == "my_check" and s["params"] == ["mode", "song"] and os.path.isfile(s["file"])
assert os.path.dirname(s["file"]) == os.path.join(os.path.dirname(m.STATE), "recipes")
m.TOOLS["fl_doctor"] = (mk("fl_doctor", lambda kw: {"project": kw["project"], "issues": []}),) + saved["fl_doctor"][1:]
calls.clear()
r = run_forced("my_check", song=p, background=False)
assert r["ok"] and calls[1] == ("fl_review", {"project": p, "mode": "auto"}), calls
for bad in ([{"tool": "flh_run", "args": {}}], [{"tool": "flh_recipe_run", "args": {}}], [{"tool": "rm -rf", "args": {}}],
            [{"tool": "fl_doctor", "args": "x"}], []):
    try:
        X.flh_recipe_save("bad", bad, "x"); raise AssertionError(bad)
    except ValueError:
        pass
for res_ in ({"confirm": {}}, None):
    try:
        X.flh_recipe_save("bad", [{"tool": "fl_doctor", "args": {"project": "${name}"}}], "x", params=res_)
        raise AssertionError(res_)
    except ValueError:
        pass
for f in os.listdir(X.SHIPPED_RECIPES):                    # shipped recipes never use reserved param names
    rec = json.load(open(os.path.join(X.SHIPPED_RECIPES, f)))
    assert not (set(rec.get("params") or {}) | X._placeholders(rec["steps"], set())) & X.RESERVED, f
for badname in ("../evil", "a/b", "", "x" * 80):
    try:
        X.flh_recipe_save(badname, [{"tool": "fl_doctor", "args": {}}], "x"); raise AssertionError(badname)
    except ValueError:
        pass
try:
    X.flh_recipe_save("my_check", [{"tool": "fl_doctor", "args": {}}], "again"); raise AssertionError
except FileExistsError:
    pass
assert X.flh_recipe_save("my_check", [{"tool": "fl_doctor", "args": {"project": "${p}"}}], "again", overwrite=True)
# a user recipe naming a tool that doesn't exist: saved with a warning, refused at run time (nothing runs)
s = X.flh_recipe_save("future", [{"tool": "fl_doctor", "args": {"project": "${project}"}},
                                 {"tool": "fl_not_here_yet", "args": {}}], "needs a newer fl-headless")
assert s["missing_tools"] == ["fl_not_here_yet"]
calls.clear()
r = X.flh_recipe_run("future", project=p, background=False)
assert r["ok"] is False and "not available" in r["cannot_run"][0] and not calls, r
# a user recipe shadows a shipped one
s = X.flh_recipe_save("clean_and_check", [{"tool": "fl_doctor", "args": {"project": "${project}"}}], "mine")
assert "overrides" in s
assert X.flh_recipe_list("clean_and_check")["source"] == "user"
# in-place project writers and apply=true count as destructive; dry runs / outputs elsewhere don't
assert X._destructive("flp_edit", {"project": p, "ops": []})
assert not X._destructive("flp_edit", {"project": p, "ops": [], "output": os.path.join(tmp, "copy.flp")})
assert X._destructive("fl_mix_engineer", {"apply": True}) and not X._destructive("fl_mix_engineer", {"apply": False})
assert X._destructive("flp_remove_channels", {"project": p}) and not X._destructive("flp_remove_channels",
                                                                                    {"project": p, "dry_run": True})
# M4: string booleans (recipes fill ${apply} as text) and writes over existing / input files need confirm
assert X._destructive("fl_mix_engineer", {"apply": "true"}) and X._destructive("fl_mix_engineer", {"apply": "Yes"})
assert not X._destructive("fl_mix_engineer", {"apply": "false"}) and not X._destructive("fl_mix_engineer", {"apply": ""})
assert X._destructive("fl_mix_engineer", {"apply": "true", "dry_run": "false"})
wv = os.path.join(tmp, "in.wav"); open(wv, "wb").write(b"RIFF")
assert "in place" in X._destructive("audio_edit", {"input": wv, "output": wv, "ops": []})
assert "in place" in X._destructive("audio_join", {"inputs": [wv], "output": wv})
assert "overwrites" in X._destructive("audio_limit", {"input": os.path.join(tmp, "x.wav"), "output": wv, "target_lufs": -14})
assert not X._destructive("audio_limit", {"input": wv, "output": os.path.join(tmp, "new.wav"), "target_lufs": -14})
import fl_audio_edit as _AE  # noqa: E402
try:
    _AE.edit(wv, wv, []); raise SystemExit("in-place edit allowed")
except ValueError as e:
    assert "input file" in str(e)
assert not X._destructive("flp_project_info", {"path": p}) and X._destructive("files_recycle", {"paths": []})
# a hand-written user recipe file with a banned tool is refused at run time too
with open(os.path.join(X._user_dir(), "sneaky.json"), "w") as f:
    json.dump({"name": "sneaky", "description": "x", "steps": [{"tool": "flh_run", "args": {"steps": []}}]}, f)
r = X.flh_recipe_run("sneaky", background=False)
assert r["ok"] is False and "can't run inside a recipe" in r["steps"][0]["error"], r
# vocal_session: optional params dropped, defaults used, $prev.track feeds the level check
for k in ("fl_vocal_setup", "fl_vocal_level_check"):
    saved[k] = m.TOOLS[k]
m.TOOLS["fl_vocal_setup"] = (mk("fl_vocal_setup", {"track": 7, "armed": True}),) + saved["fl_vocal_setup"][1:]
m.TOOLS["fl_vocal_level_check"] = (mk("fl_vocal_level_check", {"peak_dbfs": -9}),) + saved["fl_vocal_level_check"][1:]
calls.clear()
r = run_forced("vocal_session", background=False)
assert r["ok"] and calls == [("fl_vocal_setup", {"name": "Vocal"}), ("fl_vocal_level_check", {"track": 7})], calls
for k, v in saved.items():
    m.TOOLS[k] = v

# ============================================================================ teaching
ns["_RECENT_CALLS"].clear()
r = X.flh_teach(project=p)
assert r["level"] == "beginner" and "teaching" in r and r["from"].startswith("session"), r
flh_store.profile_set("demo", {"teaching": "pro"})
X.note_call("fl_review", {"project": p}, {"verdict": "fine", "issues": [1, 2], "took_s": 3})
X.note_call("fl_mix_engineer", {"project": p, "apply": False}, {"status": "running", "job": "mixeng-1"})
X.note_call("flp_edit", {"project": p, "ops": [1, 2, 3]}, error=ValueError("bad op"))
r = X.flh_teach()
assert r["level"] == "pro" and r["from"] == "recent calls" and len(r["calls"]) == 3, r
assert r["calls"][0]["did"].startswith("Per-section") and r["calls"][0]["on"] == "My Song.flp"
assert r["calls"][2]["outcome"].startswith("it failed")
assert tmp not in json.dumps(r), "args summaries hold file names, not paths"
r = X.flh_teach(level="beginner", topic="LUFS", n=1)
assert r["level"] == "beginner" and "-14" in r["concept"]["explain"] and len(r["calls"]) == 1 and "tip" in r
assert r["calls"][0]["tool"] == "flp_edit" and "why" in r["calls"][0]
ns["handbook_explain"] = lambda topic, **k: {"text": f"handbook says {topic}"}
assert X.flh_teach(topic="sidechain", last=False)["concept"] == {"topic": "sidechain", "explain": "handbook says sidechain",
                                                                "source": "handbook"}
del ns["handbook_explain"]
assert X.flh_teach(topic="zzz-unknown", last=False)["concept"]["explain"] is None
assert X.teach_line("fl_review", {}, {}).startswith("Per-section")
flh_store.profile_set("demo", {"teaching": "off"})
assert X.teach_line("fl_review", {}, {}) is None
# unknown tool: falls back to its prefix + description
X.note_call("flp_info", {"path": p}, {"ppq": 96})
assert X.flh_teach(n=1, level="beginner")["calls"][0]["did"].startswith("Worked on the project file")
for i in range(60):
    X.note_call("fl_ping", {})
assert len(ns["_RECENT_CALLS"]) == X.RECENT_MAX
try:
    X.flh_teach(level="expert"); raise AssertionError
except ValueError:
    pass

# ============================================================================ resume
flh_store.profile_set("demo", {"style": "keep the low end warm"})
flh_store.journal_update(p, set={"review": {"when": "2020-01-01 10:00", "issues": 2, "top": ["chorus not louder than verse",
                                                                                             "mud in the verse"]}},
                         add={"mix_log": {"when": "2020-01-01 11:00", "by": "mix", "done": ["piano -2 dB", "bass +1"]},
                              "versions": {"label": "v1 rough", "when": "2020-01-01 12:00"}})
m._pending_set(p, [{"tool": "flp_set_tempo", "bpm": 90}])
r = X.flh_resume(p)
assert r["song"] == "My Song" and r["key"] == "G minor" and r["bpm"] == 60.0 and "Intro(1)" in r["sections"], r
assert r["review"]["issues"] == 2 and any("mix" in x for x in r["last_time"]) and any("v1 rough" in x for x in r["last_time"])
assert r["pending_edits"].startswith("1 queued") and len(r["next"]) == 3 and r["next"][0].startswith("flh_sync")
assert any("chorus not louder" in x for x in r["next"])
assert "+ warm piano" in r["keep_in_mind"] and "- bright hats" in r["keep_in_mind"]
assert any("house style" in x for x in r["keep_in_mind"])
assert len(json.dumps(r)) <= 2400, len(json.dumps(r))
m._pending_set(p, [])
# no journal key -> estimated from the notes; no project given -> last opened
q2 = song("Fresh")
m._state({"last_opened": q2})
r = X.flh_resume()
assert r["project"] == "Fresh.flp" and "estimated" in r["key"] and any("seed" in x for x in r["next"]), r
assert any("fl_review" in x for x in r["next"])
# a huge journal stays inside the budget
flh_store.journal_update(q2, set={"sections": [{"name": "Section with a long name %d" % i, "bar": i * 4 + 1}
                                               for i in range(40)]},
                         add={"likes": ["like %d " % i + "x" * 100 for i in range(10)],
                              "dislikes": ["dislike %d " % i + "y" * 100 for i in range(10)]})
r = X.flh_resume(q2)
assert len(json.dumps(r)) <= 2400, len(json.dumps(r))
m._state({"last_opened": None})
try:
    X.flh_resume(); raise AssertionError
except ValueError:
    pass
ns["_windows_only"] = orig_wo
print("ok")
