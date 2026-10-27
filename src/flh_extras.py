"""fl-headless workflow extras: collaborator packs, saved recipes, teaching mode and "where were we".

    flh_collab_pack   one folder (+ zip) for a collaborator: project + its samples, MIDI per part, stems,
                      README.txt (tempo, key, sections, plugins and what the collaborator needs, render settings),
                      the journal's public summary. Never deletes; writes only inside FLH_ROOTS.
    flh_recipe_save / flh_recipe_run / flh_recipe_list
                      saved one-call workflows: flh_run-style steps with ${param} placeholders. Shipped examples in
                      data/recipes/, the user's own in fl-headless's state folder (recipes/). Recipes only call
                      fl-headless tools (never flh_run/flh_call/recipes themselves), destructive steps need
                      confirm=true, dry_run shows the plan (with ns["estimate"] when another module provides it).
    flh_teach         plain-language explanation of what the last tool calls did and why (profile key teaching =
                      "off" | "beginner" | "pro"); concepts via ns["handbook_explain"] when present.
    flh_resume        short session-start summary of the current / last project and 3 suggested next steps.

Recent-calls log: ns["_RECENT_CALLS"] (a list, newest last, at most RECENT_MAX). The dispatcher can feed it with
ns["flh_recent_note"](name, args, result=None, error=None, took=None); recipe steps are always logged here.
ns["flh_teach_line"](name, args, result) gives a one-line hint for a result when the profile's teaching is on.
"""
import json
import os
import re
import shutil
import sys
import time
import wave

import flh_common as C

H = {}                                                   # the server namespace, set by register()
HERE = os.path.dirname(os.path.abspath(__file__))
SHIPPED_RECIPES = os.path.join(HERE, "data", "recipes")
TEACH_FILE = os.path.join(HERE, "data", "extras", "teach.json")
RECENT_MAX = 40
TEACH_LEVELS = ("off", "beginner", "pro")


def _store():
    S = sys.modules.get("flh_store")
    if S is None:
        import flh_store as S                            # noqa: N811
    return S


def _base(p):
    return os.path.basename(str(p)) if p else p


def _mb(n):
    return round(n / 1048576, 2)


def _dir_bytes(path):
    tot = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                tot += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return tot


def _safe(text):
    f = H.get("_safe_name")
    s = f(text) if f else "".join(c for c in str(text) if c not in '<>:"/\\|?*').strip()
    return s or "untitled"


# ============================================================================= recent-calls log
def _args_summary(args):
    """Short, privacy-light view of tool arguments: file names not full paths, list/dict sizes, short strings."""
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            v = os.path.basename(v) if (os.sep in v or "/" in v or "\\" in v) else v
            out[k] = v if len(v) <= 40 else v[:37] + "..."
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list):
            out[k] = f"[{len(v)} item(s)]"
        elif isinstance(v, dict):
            out[k] = "{" + ",".join(list(v)[:4]) + ("..." if len(v) > 4 else "") + "}"
        else:
            out[k] = type(v).__name__
    return out


RESULT_KEYS = ("status", "verdict", "summary", "issues", "job", "output", "folder", "queued", "note", "done", "ok")


def _result_summary(res):
    if not isinstance(res, dict):
        return (f"{len(res)} item(s)" if isinstance(res, list) else str(res)[:60]) if res is not None else None
    bits = []
    for k in RESULT_KEYS:
        if k in res and res[k] not in (None, "", [], {}):
            v = res[k]
            if isinstance(v, list):
                v = f"{len(v)}"
            elif isinstance(v, dict):
                v = f"{len(v)} key(s)"
            elif isinstance(v, str):
                v = _base(v) if (os.sep in v or "/" in v) else (v if len(v) <= 60 else v[:57] + "...")
            bits.append(f"{k}={v}")
        if len(bits) >= 3:
            break
    return "; ".join(bits) or None


def note_call(name, args=None, result=None, error=None, took=None):
    """Append one call to ns["_RECENT_CALLS"] (the dispatcher's hook; recipes use it for their steps)."""
    if not H:
        return
    log = H.get("_RECENT_CALLS")
    if not isinstance(log, list):
        log = H["_RECENT_CALLS"] = []
    rec = {"tool": name, "when": time.strftime("%H:%M:%S"), "args": _args_summary(args)}
    if error is not None:
        rec["error"] = (f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error))[:120]
    else:
        s = _result_summary(result)
        if s:
            rec["result"] = s
    if took is not None:
        rec["took_s"] = took
    log.append(rec)
    del log[:-RECENT_MAX]


# ============================================================================= 1. collaborator pack
def _unique_dir(base):
    """base, or 'base (2)', 'base (3)'... when it already exists with something in it (never overwrite/delete)."""
    cand, i = base, 2
    while os.path.exists(cand) and (not os.path.isdir(cand) or os.listdir(cand)):
        cand = f"{base} ({i})"; i += 1
    return cand


def _unique_file(path):
    stem, ext = os.path.splitext(path)
    cand, i = path, 2
    while os.path.exists(cand):
        cand = f"{stem} ({i}){ext}"; i += 1
    return cand


FACTORY_VAR = "%FLStudioFactoryData%"


def _collect_samples(project, flp_out, dest):
    """flp_collect_samples' logic for a pack: copy every sample the project references (only those) into `dest`
    and write the copy pointing at them. FL's own factory samples stay referenced, not copied (every FL install has
    them, and they aren't ours to hand out). Two different files with the same name get distinct names."""
    d = H["_load"](project); ev = H["_events"](d)
    EV = H["EV_SAMPLE_PATH"]; edits = []; done = {}; used = {}; missing = []; factory = set(); renamed = []
    for k, e in enumerate(ev):
        if e["id"] != EV:
            continue
        pay = H["_payload"](d, e)
        if not pay.strip(b"\x00"):
            continue
        raw = H["_text16"](pay)
        if raw.startswith(FACTORY_VAR):
            factory.add(os.path.basename(raw.replace("\\", "/"))); continue
        full = H["_expand_sample"](raw, project)
        if not os.path.isfile(full):                     # missing here: no local path in the pack either
            missing.append(raw)
            rel = "Samples\\" + os.path.basename(raw.replace("\\", "/"))
            edits.append((k, k + 1, H["_data_event"](EV, rel.encode("utf-16-le") + b"\x00\x00")))
            continue
        key = os.path.normcase(os.path.abspath(full))
        target = done.get(key)
        if not target:
            name = os.path.basename(full); stem, ext = os.path.splitext(name); i = 2
            while name.lower() in used:
                name = f"{stem} ({i}){ext}"; i += 1
            if name != os.path.basename(full):
                renamed.append(f"{os.path.basename(full)} -> {name}")
            target = os.path.join(dest, name); used[name.lower()] = key; done[key] = target
            os.makedirs(dest, exist_ok=True)
            H["writable"](target)
            shutil.copy2(full, target)                   # dest is a fresh pack folder: nothing is overwritten
        # PC-VERIFY: FL resolves the RELATIVE path "Samples\<name>" against the .flp's folder (on any PC the pack
        # is unzipped to); if an FL version doesn't, its missing-sample search of the project folder finds them.
        # Relative on purpose: an absolute path would ship this PC's user name (C:\Users\<name>\...) in the pack.
        rel = os.path.relpath(target, os.path.dirname(os.path.abspath(flp_out))).replace("/", "\\")
        edits.append((k, k + 1, H["_data_event"](EV, rel.encode("utf-16-le") + b"\x00\x00")))
    H["_save"](H["_splice"](d, ev, edits), flp_out)
    return {"collected": sorted(os.path.basename(t) for t in done.values()), "missing": missing,
            "factory": sorted(factory), "renamed": renamed}


def _scrub_sample_paths(project, flp_out, sub="Samples"):
    """samples=false: the pack's .flp must not carry this PC's absolute sample paths (user name, folder names). Every
    non-variable sample path becomes "Samples\\<file name>" (where the collaborator can drop the files); FL's own
    %FLStudioFactoryData% / %FLStudioUserData% paths stay (no personal data in them). -> count rewritten."""
    d = H["_load"](project); ev = H["_events"](d)
    EV = H["EV_SAMPLE_PATH"]; edits = []
    for k, e in enumerate(ev):
        if e["id"] != EV:
            continue
        pay = H["_payload"](d, e)
        if not pay.strip(b"\x00"):
            continue
        raw = H["_text16"](pay)
        if raw.startswith("%FLStudio"):
            continue
        rel = sub + "\\" + os.path.basename(raw.replace("\\", "/"))
        edits.append((k, k + 1, H["_data_event"](EV, rel.encode("utf-16-le") + b"\x00\x00")))
    H["_save"](H["_splice"](d, ev, edits), flp_out)
    return len(edits)


def _sample_refs(project):
    """[(raw path, resolved path)] of every sample the project references (for checks)."""
    d = H["_load"](project); ev = H["_events"](d); out = []
    for e in ev:
        if e["id"] != H["EV_SAMPLE_PATH"]:
            continue
        p = H["_payload"](d, e)
        if not p.strip(b"\x00"):
            continue
        raw = H["_text16"](p)
        out.append((raw, H["_expand_sample"](raw, project)))
    return out


NEEDS_RE = re.compile(r"needs ((?:Fruity|Producer|Signature|All Plugins) (?:Edition|Bundle))")


def _plugin_need(name):
    """What a collaborator needs for one plugin, in plain words (from plugin_ownership's reasons)."""
    own = H.get("plugin_ownership")
    if not own:
        return "check: plugin info unavailable (flh_catalog not loaded)"
    try:
        r = own(name) or {}
    except Exception as e:                               # noqa: BLE001
        return f"check: {type(e).__name__}"
    why = str(r.get("why") or "")
    low = why.lower()
    if "free plugin" in low:
        return "free plugin (free download if they don't have it)"
    if "every fl edition" in low or "stock fruity" in low:
        return "comes with every FL Studio edition"
    m = NEEDS_RE.search(why)
    if m:
        return f"native FL plugin: needs {m.group(1).strip()} or higher"
    if "all plugins edition" in low:
        return "native FL plugin: All Plugins Edition (or bought separately)"
    if "third-party" in low:
        return "third-party plugin: they need their own licensed copy (else use the stems / MIDI)"
    if r.get("status") == "trial":
        return "used as a trial/demo here: they need their own copy (stems carry the sound)"
    if "profile" in low:
        return "check: listed as owned in this collection; may be third-party - they may need their own copy"
    return "unknown plugin: ask before opening (the stems carry the sound)"


def _wav_settings(path):
    try:
        with wave.open(path, "rb") as w:
            return {"sample_rate": w.getframerate(), "bits": w.getsampwidth() * 8, "channels": w.getnchannels()}
    except Exception:                                    # noqa: BLE001  (float WAV / FLAC: not readable by wave)
        return None


def _public_journal(project, private=False):
    S = _store(); j = S.journal_get(project)
    pub = {"song": j.get("song"), "role": j.get("role"), "key": j.get("key"), "bpm": j.get("bpm"),
           "sections": [{k: s.get(k) for k in ("name", "bar", "end_bar") if s.get(k) is not None}
                        for s in j.get("sections") or []],
           "channels": {c: {k: v.get(k) for k in ("role", "name") if v.get(k)}
                        for c, v in (j.get("channels") or {}).items() if isinstance(v, dict)},
           "versions": [v.get("label") for v in j.get("versions") or [] if isinstance(v, dict) and v.get("label")],
           "final": {k: (j.get("final") or {}).get(k) for k in ("lufs", "true_peak", "length", "when")
                     if (j.get("final") or {}).get(k) is not None},
           "review": {k: (j.get("review") or {}).get(k) for k in ("when", "issues", "top")
                      if isinstance(j.get("review"), dict) and j["review"].get(k) is not None}}
    if private:
        pub.update({k: j.get(k) or [] for k in ("likes", "dislikes", "notes")})
    return {k: v for k, v in pub.items() if v not in (None, [], {}, "")}


def _await_job(jid, limit_s, step=None):
    """Wait for a background job (its completion event), keeping our own job's progress current."""
    J = H["JOBS"]; ev = H.get("_JOB_EVENTS", {}).get(jid); t0 = time.time()
    while J.get(jid, {}).get("status") == "running" and time.time() - t0 < limit_s:
        left = max(0.0, limit_s - (time.time() - t0))
        if ev is not None:
            ev.wait(min(5.0, left))
        else:
            time.sleep(min(0.5, left))
        if step:
            pr = J.get(jid, {}).get("progress") or {}
            H["_job_progress"](step=step, waiting_on=jid, **({"pct": pr["pct"]} if "pct" in pr else {}))
    return dict(J.get(jid) or {"status": "missing"})


def _readme(ctx):
    L = [f"{ctx['song']} - collaboration pack", "=" * (len(ctx['song']) + 21), ""]
    if ctx.get("notes"):
        L += ["Notes from the producer", "-----------------------", str(ctx["notes"]).strip(), ""]
    L += ["Song", "----", f"Tempo: {ctx['tempo']}", f"Time signature: {ctx['time_sig']}", f"Key: {ctx['key']}",
          f"Length: {ctx['length']}", ""]
    if ctx["sections"]:
        L += ["Sections (bar numbers as in FL's playlist)", "-------------------------------------------"]
        for s in ctx["sections"]:
            end = f"-{s['end_bar']}" if s.get("end_bar") else ""
            L.append(f"  {s['name']}: bar {s['bar']}{end}")
        L.append("")
    L += ["What's inside", "-------------"]
    for line in ctx["inside"]:
        L.append(f"  {line}")
    L.append("")
    if ctx["plugins"]:
        L += ["Plugins used and what you need", "------------------------------"]
        for p, need in ctx["plugins"]:
            L.append(f"  {p}: {need}")
        L += ["  (If a plugin is missing on your side, FL opens the project without it: use the stems or the",
              "  MIDI for that part.)", ""]
    L += ["Render settings", "---------------"]
    for line in ctx["render"]:
        L.append(f"  {line}")
    L += ["", "Opening the project",  "-------------------",
          "  Unzip the whole folder, keep Samples next to the .flp and open the .flp in FL Studio.",
          "  If FL asks for a sample, point it at the Samples folder.", "",
          f"Made with fl-headless on {time.strftime('%Y-%m-%d')}.", ""]
    return "\n".join(L)


def _zip_folder(folder, zip_path):
    stored = (".wav", ".flac", ".mp3", ".ogg", ".zip", ".aif", ".aiff", ".m4a")
    top = os.path.basename(folder.rstrip("\\/"))
    import zipfile
    with zipfile.ZipFile(zip_path, "w") as z:
        for root, _dirs, files in os.walk(folder):
            for f in sorted(files):
                full = os.path.join(root, f)
                arc = os.path.join(top, os.path.relpath(full, folder))
                comp = zipfile.ZIP_STORED if f.lower().endswith(stored) else zipfile.ZIP_DEFLATED
                z.write(full, arc, compress_type=comp)
    return os.path.getsize(zip_path)


def _build_pack(project, out, stems, midi, samples, do_zip, notes, private, stem_format):
    steps = {}; T0 = time.time(); warn = []
    def mark(k):
        nonlocal T0
        steps[k] = round(time.time() - T0, 2); T0 = time.time()
    prog = H.get("_job_progress") or (lambda **k: None)
    S = _store()
    song_stem = os.path.splitext(os.path.basename(project))[0]
    try:
        H["_sync_before"]("flh_collab_pack", project)    # queued lazy edits go into the file first
    except Exception as e:                               # noqa: BLE001
        raise RuntimeError(f"queued edits for this project couldn't be applied first, nothing was packed: {e}")
    mark("sync")
    os.makedirs(out, exist_ok=True)
    flp_out = os.path.join(out, os.path.basename(project))
    res = {"folder": out, "project": os.path.basename(flp_out)}

    # ---- project (+ samples)
    prog(step="project", pct=5)
    if samples:
        r = _collect_samples(project, flp_out, os.path.join(out, "Samples"))
        miss = sorted({os.path.basename(m.replace("\\", "/")) for m in r["missing"]})
        res["samples"] = {"collected": len(r["collected"]), "missing": miss}
        if r["factory"]:
            res["samples"]["fl_factory"] = len(r["factory"])
        if r["renamed"]:
            res["samples"]["renamed"] = r["renamed"][:10]
            warn.append(f"{len(r['renamed'])} different sample(s) shared a file name: renamed in the pack "
                        f"({'; '.join(r['renamed'][:3])}); the pack's project points at the renamed copies")
        if miss:
            warn.append(f"{len(miss)} sample(s) the project uses are missing on this PC and aren't in the pack: "
                        + ", ".join(miss[:5]))
    else:
        n_sc = _scrub_sample_paths(project, flp_out)
        res["samples"] = ("not included (samples=false); the pack's project names them as Samples\\<file> "
                          f"({n_sc} path(s), no local folders)")
    mark("project")

    # ---- song facts
    info = H["flp_info"](project)
    try:
        pinfo = H["flp_project_info"](project)
    except Exception:                                    # noqa: BLE001
        pinfo = {}
    j = S.journal_get(project)
    key = j.get("key")
    if not key:
        try:
            k = (H["flp_analyze_music"](project) or {}).get("key")
            top = k[0][0] if isinstance(k, list) and k and isinstance(k[0], (list, tuple)) else k
            key = f"{top} (estimated from the notes - check by ear)" if top else "unknown"
        except Exception:                                # noqa: BLE001
            key = "unknown"
    bpm = info.get("tempo_bpm")
    tempo = f"{bpm:g} BPM" if bpm else "unknown"
    if info.get("tempo_events"):
        tempo += f" (with {info['tempo_events']} tempo changes: the MIDI files carry the tempo map)"
    sections = j.get("sections") or []
    if not sections:
        try:
            sections = S.sections_from_project(project)
        except Exception:                                # noqa: BLE001
            sections = []
    chans = {c["index"]: c for c in (H["flp_list"](project).get("channels") or [])}
    jch = j.get("channels") or {}

    def part_name(ch):
        jc = jch.get(str(ch)) or {}
        return jc.get("name") or chans.get(ch, {}).get("name") or jc.get("role") or chans.get(ch, {}).get("plugin") \
            or f"channel {ch}"
    mark("facts")

    # ---- MIDI per part
    midi_files = []
    if midi:
        prog(step="midi", pct=15)
        mdir = os.path.join(out, "MIDI"); os.makedirs(mdir, exist_ok=True)
        parts = sorted(int(c) for c in (info.get("notes_per_channel") or {}))
        if parts:
            try:
                H["flp_export_midi"](project, os.path.join(mdir, f"{song_stem} - all parts.mid"))
                midi_files.append(f"{song_stem} - all parts.mid")
            except Exception as e:                       # noqa: BLE001
                warn.append(f"MIDI (all parts): {e}")
        for ch in parts:
            fn = f"{song_stem} - {ch:02d} {_safe(part_name(ch))}.mid"
            try:
                H["flp_export_midi"](project, os.path.join(mdir, fn), channels=[ch])
                midi_files.append(fn)
            except Exception as e:                       # noqa: BLE001
                warn.append(f"MIDI channel {ch}: {e}")
        res["midi"] = len(midi_files)
    mark("midi")

    # ---- stems (FL render job; the render path is the only one allowed to close FL)
    stem_files = []
    if stems:
        prog(step="stems", pct=25)
        if H["_windows_only"]("fl_render_stems"):
            res["stems"] = "skipped: stems are rendered by FL (Windows only)"
            warn.append(res["stems"])
        else:
            try:
                # PC-VERIFY: FL closes (saved) for the stem renders and reopens the project afterwards; the stems
                # line up from bar 1 and summed sound like the full render
                r = H["fl_render_stems"](project, output_dir=os.path.join(out, "Stems"), formats=stem_format)
                if isinstance(r, dict) and r.get("job") and r.get("status") == "running":
                    rec = _await_job(r["job"], 3 * 3600, step="stems")
                    if rec.get("status") != "done":
                        raise RuntimeError(rec.get("error") or f"stem job {rec.get('status')}")
                    r = rec.get("result") or {}
                stem_files = [os.path.basename(s) for s in (r.get("stems") or [])]
                res["stems"] = len(stem_files)
            except Exception as e:                       # noqa: BLE001
                res["stems"] = f"failed: {type(e).__name__}: {e}"
                warn.append(f"stems weren't rendered ({e}); the rest of the pack is complete")
    mark("stems")

    # ---- plugins + README + journal summary
    prog(step="readme", pct=90)
    plugins = []
    try:
        doc = H["TOOLS"]["fl_doctor"][0](project=project) if "fl_doctor" in H["TOOLS"] else {}
        names = (doc.get("summary") or {}).get("plugins") or []
    except Exception:                                    # noqa: BLE001
        names = []
    if not names:
        lst = H["flp_list"](project)
        names = sorted({x.get("plugin") for x in (lst.get("channels") or []) + (lst.get("effects") or [])
                        if x.get("plugin") and x.get("plugin") != "Fruity Wrapper"})
    plugins = [(p, _plugin_need(p)) for p in names]
    render = []
    if stem_files:
        ws = _wav_settings(os.path.join(out, "Stems", stem_files[0])) if stem_format == "wav" else None
        fmt = (f"{ws['sample_rate']} Hz, {ws['bits']}-bit, {'stereo' if ws['channels'] == 2 else 'mono'}"
               if ws else stem_format.upper())
        render.append(f"Stems: one file per channel that plays ({len(stem_files)}), {fmt}, all from bar 1 so they "
                      "line up when dropped in at the start.")
        render.append("Each stem is that channel alone through the full mixer (its insert, bus and master effects on, "
                      "automation kept): summed together they sound like the song, not like dry tracks.")
    else:
        render.append("No stems in this pack.")
    try:
        tgt = S.song_target(S.profile_get(), j.get("role"))
        render.append(f"Final master target of this project: {tgt['lufs']:g} LUFS integrated, {tgt['true_peak']:g} "
                      f"dBTP, {tgt['bits']}-bit.")
    except Exception:                                    # noqa: BLE001
        pass
    if midi_files:
        render.append(f"MIDI: type-1 .mid files ({len(midi_files)}), one per part plus 'all parts', with the tempo map.")
    inside = [f"{os.path.basename(flp_out)} - the FL Studio project"]
    if isinstance(res.get("samples"), dict):
        inside.append(f"Samples/ - the {res['samples']['collected']} sample(s) the project uses")
        if res["samples"].get("fl_factory"):
            inside.append(f"  ({res['samples']['fl_factory']} more come with FL Studio itself and aren't included)")
    if midi_files:
        inside.append(f"MIDI/ - {len(midi_files)} MIDI file(s)")
    if stem_files:
        inside.append(f"Stems/ - {len(stem_files)} audio stem(s)")
    inside.append("journal.json - the song's structure and notes for collaborators")
    ts = f"{pinfo.get('time_sig_num', 4)}/{pinfo.get('time_sig_beat', 4)}" if pinfo else "4/4"
    length = f"{info.get('song_length_bars') or info.get('last_note_end_bars') or '?'} bars"
    ctx = {"song": j.get("song") or song_stem, "notes": notes, "tempo": tempo, "time_sig": ts, "key": key,
           "length": length, "sections": sections, "inside": inside, "plugins": plugins, "render": render}
    with open(os.path.join(out, "README.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(_readme(ctx))
    pub = _public_journal(project, private)
    pub.setdefault("key", key); pub.setdefault("bpm", bpm)
    if sections and not pub.get("sections"):
        pub["sections"] = [{k: s.get(k) for k in ("name", "bar", "end_bar") if s.get(k) is not None} for s in sections]
    with open(os.path.join(out, "journal.json"), "w", encoding="utf-8") as f:
        json.dump(pub, f, indent=1)
    res["journal"] = "public summary" + (" + likes/dislikes/notes (private=true)" if private else " (no private notes)")
    res["plugins_needed"] = [f"{p}: {n}" for p, n in plugins if not n.startswith("comes with every")][:12]
    mark("readme")

    # ---- sizes + zip
    sizes = {"project": _mb(os.path.getsize(flp_out))}
    for sub in ("Samples", "MIDI", "Stems"):
        p = os.path.join(out, sub)
        if os.path.isdir(p):
            sizes[sub.lower()] = _mb(_dir_bytes(p))
    sizes["folder"] = _mb(_dir_bytes(out))
    if do_zip:
        prog(step="zip", pct=95)
        zp = _unique_file(out.rstrip("\\/") + ".zip"); H["writable"](zp)
        sizes["zip"] = _mb(_zip_folder(out, zp))
        res["zip"] = zp
        mark("zip")
    res["sizes_mb"] = sizes
    if warn:
        res["warnings"] = warn
    res["note"] = ("pack built; nothing was deleted or changed in the original project. Open the copy once on this "
                   "PC to check it loads from the pack's Samples folder before sending.")
    res["steps_s"] = steps
    return res


def flh_collab_pack(project, out_dir=None, stems=True, midi=True, samples=True, zip=True, notes=None, private=False,
                    stem_format="wav", background=None):
    """One folder (and zip) to send a collaborator. Runs as a background job when stems are rendered."""
    project = os.path.abspath(project)
    if not os.path.isfile(project):
        raise FileNotFoundError(project)
    if stem_format not in ("wav", "mp3", "flac", "ogg"):
        raise ValueError("stem_format: wav | mp3 | flac | ogg")
    stem = os.path.splitext(os.path.basename(project))[0]
    base = os.path.abspath(out_dir) if out_dir else os.path.join(os.path.dirname(project), f"{stem} - collab")
    out = _unique_dir(base)
    H["writable"](out)
    if zip:
        H["writable"](out.rstrip("\\/") + ".zip")
    args = (project, out, bool(stems), bool(midi), bool(samples), bool(zip), notes, bool(private), stem_format)
    if background is None:
        background = bool(stems) and not H["_windows_only"]("fl_render_stems")
    if background:
        r = H["_start_job"]("collab", _build_pack, *args)
        r["folder"] = out
        r["note"] = "building the pack (stems render in FL): fl_job('%s') for the result" % r["job"]
        return r
    return _build_pack(*args)


# ============================================================================= 2. recipes
BANNED = {"flh_run", "flh_call", "flh_recipe_run", "flh_recipe_save", "flh_recipe_list", "flh_reload",
          "flh_bug_report"}
ALWAYS_DESTRUCTIVE = {"files_recycle", "flp_restore", "fl_close", "fl_settings_set", "fl_settings_restore",
                      "fl_finalize", "fl_mix_apply", "flp_perform_undo", "fl_vocal_record"}
LONG_TOOLS = {"fl_render", "fl_render_batch", "fl_render_stems", "fl_finalize", "fl_review", "fl_mix_engineer",
              "fl_listen", "fl_export_all", "fl_vocal_level_check", "fl_vocal_record", "flh_collab_pack",
              "fl_reference_match", "fl_masking", "fl_platform_preview", "fl_ab", "fl_mix_check"}
PARAM_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,59}$")
MAX_STEPS = 30
RESERVED = {"name", "params", "confirm", "dry_run", "stop_on_error", "background"}   # flh_recipe_run's own args
STEP_WAIT_S = 5                                          # inline runs wait this long for a step's job (then:
                                                         # 'still running', fl_job for its result)
INLINE_MAX_S = 8                                         # a recipe (or step) estimated longer never runs inline
JOB_WAIT_S = 3 * 3600                                    # background runs: a step's job may take this long


def _user_dir():
    return os.path.join(os.path.dirname(H["STATE"]), "recipes")


def _norm_name(name):
    n = re.sub(r"[\s]+", "_", str(name or "").strip().lower())
    if not NAME_RE.match(n):
        raise ValueError("recipe name: letters, digits, '_' or '-' (max 60), e.g. 'quick_master'")
    return n


def _read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _all_recipes():
    """{name: recipe} - shipped first, the user's own (state folder) override them."""
    out = {}
    for src, folder in (("shipped", SHIPPED_RECIPES), ("user", _user_dir())):
        if not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder)):
            if not f.endswith(".json"):
                continue
            r = _read_json(os.path.join(folder, f))
            if isinstance(r, dict) and isinstance(r.get("steps"), list):
                try:                                     # a hand-made file with an odd name never hides the rest
                    nm = _norm_name(r.get("name") or f[:-5])
                except ValueError:
                    try:
                        nm = _norm_name(re.sub(r"[^A-Za-z0-9_-]+", "_", str(r.get("name") or f[:-5])).strip("_"))
                    except ValueError:
                        continue
                r = dict(r); r["name"] = nm; r["source"] = src
                if src == "user" and r["name"] in out:
                    r["overrides"] = "shipped"
                out[r["name"]] = r
    return out


def _placeholders(v, found):
    if isinstance(v, str):
        found.update(PARAM_RE.findall(v))
    elif isinstance(v, dict):
        for x in v.values():
            _placeholders(x, found)
    elif isinstance(v, list):
        for x in v:
            _placeholders(x, found)
    return found


def _check_steps(steps):
    if not isinstance(steps, list) or not steps:
        raise ValueError('steps: [{"tool": "...", "args": {...}}, ...]')
    if len(steps) > MAX_STEPS:
        raise ValueError(f"at most {MAX_STEPS} steps")
    clean = []
    for i, st in enumerate(steps):
        if not isinstance(st, dict) or not isinstance(st.get("tool"), str):
            raise ValueError(f"step {i + 1}: needs a 'tool' name")
        if st["tool"] in BANNED:
            raise ValueError(f"step {i + 1}: {st['tool']} can't run inside a recipe (recipes call tools directly)")
        if not re.match(r"^[a-z][a-z0-9_]*$", st["tool"]):
            raise ValueError(f"step {i + 1}: '{st['tool']}' isn't a tool name")
        args = st.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"step {i + 1}: args must be an object")
        c = {"tool": st["tool"], "args": args}
        for k in ("note", "destructive", "wait"):
            if k in st:
                c[k] = st[k]
        clean.append(c)
    return clean


def _missing(recipe):
    T = H["TOOLS"]
    return sorted({s["tool"] for s in recipe.get("steps") or [] if s.get("tool") not in T})


def _fill(v, params):
    """${param} placeholders: a whole-string placeholder takes the value as is (number, list...), else text."""
    if isinstance(v, str):
        m = PARAM_RE.fullmatch(v.strip())
        if m:
            return params.get(m.group(1))
        return PARAM_RE.sub(lambda mm: "" if params.get(mm.group(1)) is None else str(params.get(mm.group(1))), v)
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            y = _fill(x, params)
            if y is None and isinstance(x, str) and PARAM_RE.fullmatch(x.strip()):
                continue                                 # optional param not given: the tool's own default applies
            out[k] = y
        return out
    if isinstance(v, list):
        return [_fill(x, params) for x in v]
    return v


def _truthy(v):
    """Booleans the way a tool would read them: True, 1, 'true', 'yes', 'on' (recipes fill ${param} as strings)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "y", "on")


def _same_file(a, b):
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def _destructive(name, args, step=None):
    """Why a step changes or removes something that's hard to take back (None: safe to run without confirm).
    Booleans are judged the way the tool reads them ("true" strings count), and any write onto an existing file
    (in place or over another file) needs confirm."""
    args = args or {}
    if step and step.get("destructive"):
        return "marked destructive in the recipe"
    if name in ALWAYS_DESTRUCTIVE:
        return {"files_recycle": "moves files to the Recycle Bin", "flp_restore": "replaces the project with a backup",
                "fl_close": "closes FL", "fl_finalize": "renders and replaces the song's final file",
                "fl_vocal_record": "records over the armed track"}.get(name, "changes settings / the mix")
    if _truthy(args.get("dry_run")):
        return None
    if name == "flp_remove_channels":
        return "deletes channels from the project (backup kept)"
    if name == "flh_sync" and _truthy(args.get("discard")):
        return "drops queued edits"
    if _truthy(args.get("apply")):
        return "applies changes (apply=true)"
    if _truthy(args.get("replace")) or _truthy(args.get("overwrite")):
        return "replaces existing settings or files (replace/overwrite=true)"
    writers = set(H.get("LAZY_TOOLS") or ()) | set(H.get("_OPEN_PROJECT_GUARD") or {})
    if name in writers:
        ro = H.get("_read_only_call")
        try:
            if ro and ro(name, args):
                return None
        except Exception:                                # noqa: BLE001
            pass
        src = args.get("project") or args.get("path")
        out = args.get("output")
        if not out or (src and os.path.normcase(os.path.abspath(str(out))) == os.path.normcase(os.path.abspath(str(src)))):
            return "edits the project in place (fl-headless keeps a backup: " + C.tool_name(H, "flp_versions") + ")"
    ins = [args.get(k) for k in ("input", "wav", "path", "project", "audio", "take", "source")]
    ins += list(args.get("inputs") or []) if isinstance(args.get("inputs"), list) else []
    for k in ("output", "out", "output_path", "dest", "destination", "to"):
        o = args.get(k)
        if not isinstance(o, str) or not o.strip():
            continue
        if any(isinstance(i, str) and i and _same_file(o, i) for i in ins):
            return f"overwrites its own input in place ({os.path.basename(o)})"
        if os.path.exists(o):
            return f"overwrites the existing file {os.path.basename(o)}"
    return None


def _estimate(name, args):
    fn = H.get("estimate")
    if not fn:
        return None
    try:
        try:
            e = fn(name, args)
        except TypeError:
            e = fn(tool=name, args=args)
    except Exception:                                    # noqa: BLE001
        return None
    if isinstance(e, (int, float)):
        return {"seconds": round(float(e), 1)}
    return e if isinstance(e, dict) else None


def _arg_problems(name, args):
    sch = (H["TOOLS"].get(name) or (None, None, {}))[2] or {}
    props = sch.get("properties") or {}
    unknown = [k for k in args if props and k not in props]
    missing = [k for k in sch.get("required") or [] if k not in args]
    out = []
    if unknown:
        out.append(f"unknown argument(s) {unknown}")
    if missing:
        out.append(f"missing required argument(s) {missing}")
    return out


def flh_recipe_save(name, steps, description, params=None, overwrite=False):
    """Save a user recipe (state folder). params: {name: {"description", "required", "default"}}; placeholders
    found in the steps are added as required params."""
    nm = _norm_name(name)
    clean = _check_steps(steps)
    if not description or not str(description).strip():
        raise ValueError("give a short description (what the recipe does, in a sentence)")
    ps = {}
    for k, v in (params or {}).items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(k)):
            raise ValueError(f"param name '{k}': letters, digits, '_'")
        if k in RESERVED:
            raise ValueError(f"param name '{k}' is reserved ({sorted(RESERVED)}); pick another")
        v = v if isinstance(v, dict) else {"description": str(v)}
        ps[k] = {kk: v[kk] for kk in ("description", "required", "default") if kk in v}
    for k in sorted(_placeholders(clean, set())):
        if k in RESERVED:
            raise ValueError(f"placeholder ${{{k}}} is reserved ({sorted(RESERVED)}); pick another name")
        ps.setdefault(k, {"description": "", "required": True})
    folder = _user_dir(); os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, nm + ".json")
    if os.path.isfile(path) and not overwrite:
        raise FileExistsError(f"a recipe '{nm}' exists; pass overwrite=true to replace it")
    rec = {"name": nm, "description": str(description).strip(), "params": ps, "steps": clean,
           "saved": time.strftime("%Y-%m-%d %H:%M")}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, path)
    res = {"saved": nm, "file": path, "steps": len(clean), "params": sorted(ps)}
    miss = _missing(rec)
    if miss:
        res["missing_tools"] = miss
        res["note"] = "these tools aren't loaded now; the recipe won't run until they are"
    if os.path.isfile(os.path.join(SHIPPED_RECIPES, nm + ".json")):
        res["overrides"] = "a shipped recipe of the same name (yours wins)"
    bad = []
    for i, s in enumerate(clean):
        if s["tool"] in H["TOOLS"]:
            unk = [p for p in _arg_problems(s["tool"], s["args"]) if p.startswith("unknown")]
            if unk:
                bad.append(f"step {i + 1} {s['tool']}: {'; '.join(unk)}")
    if bad:
        res["warnings"] = bad
    return res


def flh_recipe_list(name=None):
    """Recipes (shipped + yours) with params, tools and what's missing / destructive."""
    allr = _all_recipes()
    if name:
        r = allr.get(_norm_name(name))
        if not r:
            raise ValueError(f"no recipe '{name}' (have: {sorted(allr)})")
        return dict(r, missing_tools=_missing(r))
    rows = []
    for n, r in sorted(allr.items()):
        row = {"name": n, "source": r["source"], "description": r.get("description"),
               "params": {k: ("required" if (v or {}).get("required") else "optional")
                          for k, v in (r.get("params") or {}).items()},
               "tools": [s.get("tool") for s in r["steps"]]}
        miss = _missing(r)
        if miss:
            row["missing_tools"] = miss
        if any(_destructive(s.get("tool"), s.get("args"), s) for s in r["steps"]):
            row["needs_confirm"] = True
        if r.get("overrides"):
            row["overrides"] = r["overrides"]
        rows.append(row)
    return {"recipes": rows, "user_folder": _user_dir(),
            "run": C.tool_call(H, "flh_recipe_run", {"name": C.Raw("name"), "params": C.Raw("params"),
                                                     "dry_run": True}) + " shows the plan first"}


def _run_steps(steps, stop_on_error, wait_s, label):
    T = H["TOOLS"]; out = []; prev = None; ok = True
    subst = H.get("_subst_prev") or (lambda v, p: v)
    compact = H.get("_compact_result") or (lambda r: r)
    for i, st in enumerate(steps):
        name = st["tool"]; t0 = time.time(); args = {}
        H["_job_progress"](step=f"{i + 1}/{len(steps)} {name}", pct=int(100 * i / len(steps)))
        try:
            if name in BANNED:
                raise ValueError(f"{name} can't run inside a recipe")
            if name not in T:
                raise ValueError(f"no tool '{name}'")
            if H["_windows_only"](name):
                raise RuntimeError(f"{name}: {H['WINDOWS_ONLY_NOTE']}")
            args = subst(dict(st["args"]), prev)
            if H.get("_check_args"):                         # the same checks / coercion as a direct call
                args = H["_check_args"](name, args, T[name])
            res = T[name][0](**args)
            if isinstance(res, dict) and res.get("job") and res.get("status") == "running" and st.get("wait", True):
                rec = _await_job(res["job"], wait_s)
                if rec.get("status") == "running":
                    out.append({"step": i + 1, "tool": name, "job": res["job"], "status": "still running",
                                "note": f"fl_job('{res['job']}') for its result"})
                    note_call(name, args, res, took=round(time.time() - t0, 2))
                    if i + 1 < len(steps):
                        out[-1]["stopped"] = (f"later steps need its result; {len(steps) - i - 1} step(s) not run - "
                                              "run the recipe with background=true to wait for it")
                        ok = False
                    break
                if rec.get("status") != "done":
                    raise RuntimeError(rec.get("error") or f"job {res['job']} {rec.get('status')}")
                res = rec.get("result")
            try:
                H["_timing_note"](name, round(time.time() - t0, 2), res)
            except Exception:                            # noqa: BLE001
                pass
            note_call(name, args, res, took=round(time.time() - t0, 2))
            prev = res
            out.append({"step": i + 1, "tool": name, "result": compact(res)})
        except Exception as e:                           # noqa: BLE001
            ok = False
            note_call(name, args, error=e)
            try:
                H["_note_tool_error"](f"recipe {label}:{name}", e)
            except Exception:                            # noqa: BLE001
                pass
            out.append({"step": i + 1, "tool": name, "error": f"{type(e).__name__}: {e}"})
            if stop_on_error:
                if i + 1 < len(steps):
                    out[-1]["stopped"] = f"{len(steps) - i - 1} step(s) not run"
                break
    return {"recipe": label, "ok": ok, "ran": len(out), "of": len(steps), "steps": out}


def flh_recipe_run(name, params=None, confirm=False, dry_run=False, stop_on_error=True, background=None, **kw):
    """Run a saved recipe: params as params={...} or as plain arguments (project=..., genre=...)."""
    allr = _all_recipes()
    nm = _norm_name(name)
    r = allr.get(nm)
    if not r:
        raise ValueError(f"no recipe '{name}' (have: {sorted(allr)}); {C.tool_call(H, 'flh_recipe_list')} shows them")
    given = dict(params or {}); given.update(kw)
    spec = r.get("params") or {}
    vals = {k: (v or {}).get("default") for k, v in spec.items()}
    vals.update(given)
    unknown = sorted(set(given) - set(spec) - _placeholders(r["steps"], set()))
    need = sorted(k for k, v in spec.items() if (v or {}).get("required") and vals.get(k) in (None, ""))
    need += sorted(k for k in _placeholders(r["steps"], set()) if k not in spec and vals.get(k) is None)
    miss = _missing(r)
    steps = [{**s, "args": _fill(s.get("args") or {}, vals)} for s in r["steps"]]
    plan = []
    for i, s in enumerate(steps):
        row = {"step": i + 1, "tool": s["tool"], "args": _args_summary(s["args"])}
        if s["tool"] not in H["TOOLS"]:
            row["missing"] = True
        else:
            probs = _arg_problems(s["tool"], s["args"])
            if probs:
                row["problems"] = probs
        why = _destructive(s["tool"], s["args"], s)
        if why:
            row["destructive"] = why
        est = _estimate(s["tool"], s["args"])
        if est:
            row["estimate"] = est
        if s.get("note"):
            row["note"] = s["note"]
        plan.append(row)
    head = {"recipe": nm, "description": r.get("description")}
    if unknown:
        head["ignored_params"] = unknown
    secs = [p["estimate"].get("seconds") for p in plan if isinstance(p.get("estimate"), dict)]
    if secs and all(isinstance(x, (int, float)) for x in secs):
        head["estimate_s"] = round(sum(secs), 1)
    blockers = []
    if need:
        blockers.append(f"missing param(s): {need}")
    if miss:
        blockers.append(f"tool(s) not available here: {miss} (their extension isn't loaded or it's another version)")
    if dry_run or blockers:
        res = dict(head, plan=plan, dry_run=bool(dry_run))
        if blockers:
            res["ok"] = False; res["cannot_run"] = blockers
        destr = [p["step"] for p in plan if p.get("destructive")]
        if destr:
            res["needs_confirm"] = destr
        return res
    destr = [p for p in plan if p.get("destructive")]
    if destr and not confirm:
        return dict(head, ok=False, refused="destructive step(s) need confirm=true",
                    needs_confirm=[{"step": p["step"], "tool": p["tool"], "why": p["destructive"]} for p in destr],
                    plan=plan)
    long_steps = [p for p, s in zip(plan, steps) if s["tool"] in LONG_TOOLS or
                  (isinstance(p.get("estimate"), dict) and (p["estimate"].get("seconds") or 0) > INLINE_MAX_S)]
    long_total = head.get("estimate_s", 0) > INLINE_MAX_S
    forced = None
    if background is False and (long_steps or long_total):     # an inline call that waits minutes gets cut off by
        why = (f"step {long_steps[0]['step']} ({long_steps[0]['tool']}) can take long" if long_steps else
               f"the steps take about {head['estimate_s']:g} s")    # the client (~60 s): run it as a job anyway
        forced = (f"background=false ignored: {why}, and a call that blocks more than a few seconds gets cut off by "
                  "MCP clients; it runs as a job instead")
        background = True
    if background is None:
        background = bool(long_steps) or long_total
    if background:
        j = H["_start_job"]("recipe", _run_steps, steps, stop_on_error, JOB_WAIT_S, nm)
        j.update(head); j["steps"] = len(steps)
        j["note"] = f"running in the background: fl_job('{j['job']}') returns the moment it finishes"
        if forced:
            j["background_ignored"] = forced
        return j
    return dict(_run_steps(steps, stop_on_error, STEP_WAIT_S, nm), description=r.get("description"))


# ============================================================================= 3. teaching mode
_TEACH_CACHE = {}


def _teach_data():
    try:
        mt = os.path.getmtime(TEACH_FILE)
    except OSError:
        return {"tools": {}, "concepts": {}, "prefixes": {}}
    if _TEACH_CACHE.get("mt") != mt:
        _TEACH_CACHE.update(mt=mt, data=_read_json(TEACH_FILE) or {})
    return _TEACH_CACHE["data"]


def _teach_level(level=None):
    if level:
        if level not in TEACH_LEVELS:
            raise ValueError("level: off | beginner | pro")
        return level
    try:
        lv = _store().profile_get().get("teaching")
    except Exception:                                    # noqa: BLE001
        lv = None
    return lv if lv in TEACH_LEVELS else "off"


def _first_sentence(text):
    t = str(text or "").strip()
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    return (m.group(1) if m else t)[:200]


def _explain_tool(name, level):
    D = _teach_data(); t = (D.get("tools") or {}).get(name)
    if not t:
        for pre, v in sorted((D.get("prefixes") or {}).items(), key=lambda kv: -len(kv[0])):
            if name.startswith(pre):
                t = {"beginner": v.get("beginner"), "pro": v.get("pro"), "generic": True}
                break
    desc = _first_sentence((H["TOOLS"].get(name) or (None, ""))[1])
    if not t:
        return {"did": desc or name, "why": None}
    did = t.get(level) or t.get("beginner") or desc
    if t.get("generic") and desc:
        did = f"{did} {desc}" if level == "beginner" else desc
    return {"did": did, "why": t.get("why") if level == "beginner" else t.get("why_pro") or None,
            "next": t.get("next")}


def _concept(topic, level):
    fn = H.get("handbook_explain")
    if fn:
        try:
            r = fn(topic) if level != "pro" else fn(topic, level="pro")
        except TypeError:
            try:
                r = fn(topic)
            except Exception:                            # noqa: BLE001
                r = None
        except Exception:                                # noqa: BLE001
            r = None
        if r:
            if isinstance(r, dict):
                r = r.get("text") or r.get("explain") or r.get("summary") or json.dumps(r)[:400]
            return {"topic": topic, "explain": str(r)[:600], "source": "handbook"}
    D = _teach_data(); c = (D.get("concepts") or {})
    key = str(topic).strip().lower()
    hit = c.get(key) or next((v for k, v in c.items() if key in k or k in key), None)
    if hit:
        return {"topic": topic, "explain": hit.get(level) or hit.get("beginner"), "source": "fl-headless"}
    if topic in H["TOOLS"]:
        e = _explain_tool(topic, level if level != "off" else "beginner")
        return {"topic": topic, "explain": e["did"], **({"why": e["why"]} if e.get("why") else {})}
    return {"topic": topic, "explain": None,
            "note": "no explanation for that here; fl_help(topic) searches FL's own help"}


def _fallback_calls(project=None):
    """Without the dispatcher's log: what this session's timings, jobs and the journal show (order partly unknown)."""
    out = []
    for jid, v in list((H.get("JOBS") or {}).items())[-6:]:
        if jid.startswith("verify-"):
            continue
        out.append({"tool": jid.rsplit("-", 1)[0], "job": jid, "result": v.get("status")})
    if project:
        try:
            j = _store().journal_get(project)
            ml = (j.get("mix_log") or [])[-1:]
            for m in ml:
                out.append({"tool": "fl_mix_engineer", "when": m.get("when"), "result": "; ".join(m.get("done") or [])[:80]})
            if isinstance(j.get("review"), dict):
                out.append({"tool": "fl_review", "when": j["review"].get("when"),
                            "result": f"issues={j['review'].get('issues')}"})
            if j.get("performance_history"):
                out.append({"tool": "flp_perform", "result": f"{len(j['performance_history'])} earlier spec(s)"})
        except Exception:                                # noqa: BLE001
            pass
    seen = {o["tool"] for o in out}
    tim = H.get("_TIMINGS") or {}
    for k in sorted(tim, key=lambda k: -tim[k].get("calls", 0))[:6]:
        if k not in seen and not k.startswith("flh_teach"):
            out.append({"tool": k, "result": f"{tim[k].get('calls')} call(s) this session"})
    return out


def flh_teach(topic=None, last=True, n=4, level=None, project=None):
    """Explain the last few tool calls (and/or a concept) in plain producer language."""
    lv = _teach_level(level)
    use = "beginner" if lv == "off" else lv
    res = {"level": use}
    if lv == "off" and not level:
        res["teaching"] = "off (explaining anyway because you asked); flh_profile(set={'teaching': 'beginner'}) " \
                          "turns on short hints"
    if topic:
        res["concept"] = _concept(topic, use)
    if last:
        log = [c for c in (H.get("_RECENT_CALLS") or []) if not str(c.get("tool", "")).startswith(("flh_teach",
                                                                                                    "flh_resume"))]
        src = "recent calls"
        if not log:
            log = _fallback_calls(project or _guess_project(quiet=True)); src = "session timings / journal (order approximate)"
        rows = []
        for c in log[-max(1, min(int(n or 4), 10)):]:
            e = _explain_tool(c["tool"], use)
            row = {"tool": c["tool"], "did": e["did"]}
            if c.get("args"):
                on = c["args"].get("project") or c["args"].get("path") or c["args"].get("audio")
                if on:
                    row["on"] = on
            if c.get("error"):
                row["outcome"] = "it failed: " + c["error"][:100]
            elif c.get("result"):
                row["outcome"] = c["result"]
            if e.get("why"):
                row["why"] = e["why"]
            rows.append(row)
        res["calls"] = rows; res["from"] = src
        nxt = next((_explain_tool(r["tool"], use).get("next") for r in reversed(rows)
                    if _explain_tool(r["tool"], use).get("next")), None)
        if nxt:
            res["next"] = nxt
        if not rows:
            res["calls_note"] = "nothing called yet this session"
    if use == "beginner":
        res["tip"] = ("Ask 'why?' about anything here - every change can be undone (backups: " +
                      C.tool_name(H, "flp_versions") + ").")
    return res


def teach_line(name, args=None, result=None):
    """One short hint for a tool result when the profile's teaching is on (for the dispatcher), else None."""
    try:
        lv = _teach_level()
        if lv == "off" or name.startswith("flh_teach"):
            return None
        e = _explain_tool(name, lv)
        line = e["did"] if lv == "pro" else (e["did"] + (" " + e["why"] if e.get("why") else ""))
        return line[:220] if line else None
    except Exception:                                    # noqa: BLE001
        return None


# ============================================================================= 4. where were we
def _guess_project(quiet=False):
    if H.get("IS_WINDOWS"):
        try:
            p = H["_open_project_guess"]()[0]
            if p and os.path.isfile(p):
                return p
        except Exception:                                # noqa: BLE001
            pass
    try:
        last = H["_state"]().get("last_opened")
        if last and os.path.isfile(last):
            return last
    except Exception:                                    # noqa: BLE001
        pass
    S = _store()
    try:
        songs = (S.profile_get().get("songs") or {}).values()
        best = None
        for s in songs:
            p = (s or {}).get("project")
            if p and os.path.isfile(p):
                jp = S.journal_path(p)
                t = os.path.getmtime(jp) if os.path.isfile(jp) else os.path.getmtime(p)
                if best is None or t > best[0]:
                    best = (t, p)
        if best:
            return best[1]
    except Exception:                                    # noqa: BLE001
        pass
    if quiet:
        return None
    raise ValueError("no project open or remembered: give project")


def _when(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _parse_when(s):
    try:
        return time.mktime(time.strptime(str(s), "%Y-%m-%d %H:%M"))
    except (ValueError, TypeError):
        return None


def flh_resume(project=None):
    """Session start: where this song is and what to do next (short)."""
    project = os.path.abspath(project) if project else _guess_project()
    if not os.path.isfile(project):
        raise FileNotFoundError(project)
    S = _store(); j = S.journal_get(project); prof = S.profile_get()
    r = {"song": j.get("song"), "project": os.path.basename(project)}
    try:
        info = H["flp_info"](project)
    except Exception:                                    # noqa: BLE001
        info = {}
    key = j.get("key")
    if not key:
        try:
            k = (H["flp_analyze_music"](project) or {}).get("key")
            top = k[0][0] if isinstance(k, list) and k and isinstance(k[0], (list, tuple)) else k
            key = f"{top} (estimated)" if top else None
        except Exception:                                # noqa: BLE001
            key = None
    r["key"] = key
    r["bpm"] = info.get("tempo_bpm") or j.get("bpm")
    if info.get("song_length_bars"):
        r["bars"] = info["song_length_bars"]
    secs = j.get("sections") or []
    r["sections"] = " ".join(f"{s.get('name')}({s.get('bar')})" for s in secs[:12]) or None
    # ---- what changed last time
    ch = []
    mtime = os.path.getmtime(project)
    r["project_saved"] = _when(mtime)
    upd = _parse_when(j.get("updated"))
    if j.get("updated"):
        ch.append(f"journal updated {j['updated']}")
    if upd and mtime > upd + 120:
        ch.append("the project was saved after the journal's last entry (edits in FL since)")
    vers = [v for v in j.get("versions") or [] if isinstance(v, dict)]
    if vers:
        v = vers[-1]
        ch.append(f"last version: {v.get('label') or _base(v.get('path'))} {v.get('when') or ''}".strip())
    try:
        bk = H["flp_versions"](project)
        if bk:
            b = os.path.basename(bk[-1]["backup"])
            when = f"{b[0:4]}-{b[4:6]}-{b[6:8]} {b[9:11]}:{b[11:13]}" if re.match(r"\d{8}-\d{6}", b) else b[:15]
            ch.append(f"{len(bk)} backup(s) by fl-headless, newest {when} ({C.tool_name(H, 'flp_versions')})")
    except Exception:                                    # noqa: BLE001
        pass
    ml = [m for m in j.get("mix_log") or [] if isinstance(m, dict)]
    if ml:
        m = ml[-1]
        ch.append(f"mix {m.get('when', '')}: " + "; ".join(str(x) for x in (m.get("done") or [])[:3]))
    pf = j.get("performance") or {}
    if pf:
        style = (pf.get("auto") or {}).get("style")
        ch.append(f"performance spec ({style or 'custom'}), {len(j.get('performance_history') or [])} earlier")
    fin = j.get("final") or {}
    if fin:
        ch.append(f"final {fin.get('when', '')}: {fin.get('lufs')} LUFS")
    r["last_time"] = ch[:6]
    rv = j.get("review") if isinstance(j.get("review"), dict) else None
    if rv:
        r["review"] = {"when": rv.get("when"), "issues": rv.get("issues"), "top": (rv.get("top") or [])[:3]}
    try:
        pend = H["_pending_get"](project)
    except Exception:                                    # noqa: BLE001
        pend = []
    if pend:
        r["pending_edits"] = f"{len(pend)} queued: " + ", ".join(sorted({str(o.get('tool') or o.get('op'))
                                                                          for o in pend})[:4])
    keep = [f"+ {x}" for x in (j.get("likes") or [])[-3:]] + [f"- {x}" for x in (j.get("dislikes") or [])[-3:]]
    if prof.get("style"):
        keep.append(f"house style: {str(prof['style'])[:120]}")
    if keep:
        r["keep_in_mind"] = keep
    # ---- next steps
    nx = []
    if pend:
        nx.append("flh_sync() to put the queued edits into FL")
    if not secs:
        nx.append("flh_journal(project, seed=true) to name the sections")
    rv_t = _parse_when(rv.get("when")) if rv else None
    if rv and rv.get("issues") and (rv.get("top") or []):
        nx.append(f"fix the top review issue: {str(rv['top'][0])[:90]}")
    if not rv or (rv_t and mtime > rv_t + 120):
        nx.append("fl_review(project) - " + ("no review yet" if not rv else "the project changed since the last review"))
    if not pf and (info.get("notes_per_channel") or {}):
        nx.append("flp_perform_setup(project) for a more human performance")
    fin_t = _parse_when(fin.get("when")) if fin else None
    if not fin and rv and not rv.get("issues"):
        nx.append("fl_finalize(project) - the review is clean")
    elif fin and fin_t and mtime > fin_t + 120:
        nx.append("fl_finalize(project) again - the project changed after the final")
    if not ml:
        nx.append("fl_mix_engineer(project=...) for a mix proposal (nothing applied until you say)")
    nx.append(f"listen through once ({C.tool_call(H, 'fl_play')}) and tell me what to like or change")
    r["next"] = nx[:3]
    # ---- token budget: ~600 tokens (~2400 chars of JSON)
    for k in ("keep_in_mind", "last_time", "sections"):
        while len(json.dumps(r)) > 2400 and r.get(k):
            if isinstance(r[k], list) and len(r[k]) > 1:
                r[k] = r[k][:-1]
            elif isinstance(r[k], str) and len(r[k]) > 80:
                r[k] = r[k][:80] + "..."
            else:
                break
    return {k: v for k, v in r.items() if v not in (None, [], {}, "")}


# ============================================================================= registration
def register(ns):
    global H
    H = ns
    P, S = ns["_p"], ns["_schema"]
    if not isinstance(ns.get("_RECENT_CALLS"), list):
        ns["_RECENT_CALLS"] = []
    ns["flh_recent_note"] = note_call
    ns["flh_teach_line"] = teach_line
    ns["TOOLS"]["flh_collab_pack"] = (
        flh_collab_pack, "Make one folder (and a zip) to send a collaborator: a copy of the project with every sample "
        "it uses (only those), MIDI per part + all parts with the tempo map, stems rendered by FL (one per playing "
        "channel, background job), a README.txt (tempo, key, sections, each plugin and what the collaborator needs "
        "to own, render settings) and the journal's public summary (private=true adds likes/dislikes/notes). "
        "Default folder '<song> - collab' next to the project; never overwrites, never deletes; size report.",
        S(project=ns["_FLP"], out_dir=ns["_STR"]("pack folder (inside FLH_ROOTS; default next to the project)", True),
          stems=ns["_BOOL"]("render stems (default true; FL renders them, Windows)", True),
          midi=ns["_BOOL"]("MIDI per part (default true)", True),
          samples=ns["_BOOL"]("collect the samples the project uses (default true)", True),
          zip=ns["_BOOL"]("also a .zip next to the folder (default true)", True),
          notes=ns["_STR"]("a message for the collaborator (top of the README)", True),
          private=ns["_BOOL"]("include the journal's likes/dislikes/notes (default false)", True),
          stem_format=ns["_STR"]("wav (default) | flac | mp3 | ogg", True),
          background=ns["_BOOL"]("run as a job (default: when stems are rendered)", True)))
    ns["TOOLS"]["flh_recipe_save"] = (
        flh_recipe_save, "Save a one-call workflow (recipe): steps like flh_run's [{tool, args}], with ${param} "
        "placeholders and $prev.<key> from the previous step. Only fl-headless tools (no flh_run/flh_call/recipes "
        "inside). Saved in fl-headless's state folder; same name as a shipped recipe = yours wins.",
        S(name=ns["_STR"]("recipe name (letters, digits, _ -)"),
          steps=P("array", '[{"tool": "fl_review", "args": {"project": "${project}"}}, ...]', items={"type": "object"}),
          description=ns["_STR"]("what it does, one sentence"),
          params=P("object", '{name: {"description", "required", "default"}} (placeholders are added)', True),
          overwrite=ns["_BOOL"]("replace your recipe of that name", True)))
    ns["TOOLS"]["flh_recipe_run"] = (
        flh_recipe_run, "Run a recipe in one call: flh_recipe_run(name, project=..., <other params>). dry_run=true "
        "shows the plan (args, destructive steps, time estimates) without running. Steps that change or remove "
        "things (in-place project edits, apply=true, finalize, recycle, restore) need confirm=true. Missing tools "
        "are reported, nothing runs. Long steps (renders, listening) run as a background job (fl_job).",
        S(name=ns["_STR"]("recipe name (flh_recipe_list)"),
          params=P("object", "parameter values (or pass them as plain arguments)", True),
          confirm=ns["_BOOL"]("allow destructive steps", True), dry_run=ns["_BOOL"]("only show the plan", True),
          stop_on_error=ns["_BOOL"]("stop at the first failing step (default true)", True),
          background=ns["_BOOL"]("run as a job (default: when a step renders or listens; false is ignored for "
                                  "those: they always run as a job)", True)))
    ns["TOOLS"]["flh_recipe_list"] = (
        flh_recipe_list, "List recipes (shipped + yours): description, params, tools, missing tools, whether they "
        "need confirm. name=... shows one in full.",
        S(name=ns["_STR"]("one recipe in full", True)))
    ns["TOOLS"]["flh_teach"] = (
        flh_teach, "Teaching mode: explains in plain producer language what the last few tool calls did and why, "
        "and/or a concept (topic='LUFS', 'sidechain', a tool name). Level from the profile's teaching key "
        "(off | beginner | pro; flh_profile set={'teaching': 'beginner'}) or level=.",
        S(topic=ns["_STR"]("a concept or tool to explain", True),
          last=ns["_BOOL"]("explain the recent calls (default true)", True),
          n=ns["_INT"]("how many recent calls (default 4)", True),
          level=ns["_STR"]("beginner | pro (default: profile)", True),
          project=ns["_STR"]("project for the journal fallback", True)))
    ns["TOOLS"]["flh_resume"] = (
        flh_resume, "'Where were we': a short session-start summary of the current/last project - song, key, BPM, "
        "sections, what changed last time (versions, mix log, performance, final), open review issues, queued "
        "edits, likes/dislikes to keep in mind, and 3 next steps. Call it at the start of a session.",
        S(project=ns["_STR"]("the project (default: open / last opened)", True)))
