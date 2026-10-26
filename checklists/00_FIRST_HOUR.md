# First session on a real PC: upgrading 1.16.0 -> 2.0.0 and checking it

For a Windows PC with FL Studio 2026 running and fl-headless 1.16.0 installed. **Total: about 35 minutes** -
10 min install (A, B), then **one automatic check (C) of about 25 min** that runs the riskiest real-PC checks by
itself on sandbox copies, with about 2 min of your time. The step-by-step list in E is only a fallback.

**Ground rules:** test on COPIES in a scratch folder inside FLH_ROOTS (e.g. `<root>\flh-test\`), never on a
finished song. Keep FL's window visible. If a step fails, stop that step, note what you saw, and move on only if the
next step doesn't depend on it.

## A. Install (10 min)
1. Save your work in FL. Fully quit Claude Desktop (tray > Quit).
2. Copy the 2.0.0 files over the existing fl-headless folder (the one your Claude config points to), overwriting.
   New files include `flh_*.py`, `data\`, `fl_script\`, `install.py`, `install.bat`.
3. Run `install.bat`. Press Enter at the folders question to keep your FLH_ROOTS; answer the edition question
   (`p` for Producer). Expect `[fl script] installed ...` under your **local** `Documents\Image-Line\FL Studio\Settings\Hardware`.
   The installer refreshes only `command` / `args` of the fl-headless entry: extra `env` settings you added by hand
   (FLH_FL_EXE, FLH_TOOLSET...) and your FLH_ROOTS are kept (`claude_desktop_config.json.bak` has the old file).
4. Start Claude Desktop (a full restart is required once: the dispatcher changed, so 1.16's `flh_reload` can't load
   2.0). Ask for `fl_status()`. Expect: version 2.0.0, no `extension_errors`, `reload_mode` auto,
   `render_in_fl: false`, `live_notes: false`. Claude lists about 50 tools, not 220 (core.md 0.1, 5.15).

## B. Bridge and setup (10 min)
5. `fl_bridge_setup(action="update_script")`, then close FL yourself (save) and start it again: FL only loads a
   changed MIDI script on start. `fl_ping()` -> `version: 4`, no `script_outdated` (core.md 0.3, 5.1).
6. `flh_setup()`. Check `fl.user_data.path` is the local `C:\Users\<you>\Documents\Image-Line\FL Studio`, **not**
   the OneDrive copy; `layer` should be `fl_setting` or `script_loaded`, and `others` lists the OneDrive copy.
   If it picks OneDrive: `flh_setup(action="apply", user_data="C:\\Users\\<you>\\Documents\\Image-Line")` and send
   the `fl_settings(query="user data")` output (core.md 0.2, 0.4). Edition shows Producer.
7. `flp_versions(path=<a song you edited with 1.16>)`: old backups were migrated (per-project folder, or listed as
   `legacy: name match only`). Nothing lost.

## C. The automatic check (about 25 min, ~2 min of you)
8. Have FL open with a saved song (or a fresh empty project). `fl_pc_check(dry_run=true)` shows the plan.
9. `fl_pc_check()` - the quick run: 26 checks, riskiest first (which file FL has open, render inside FL, live notes,
   big-project reloads, file details FL must accept, plugin knob safety, new songs from templates, samples, 808
   pitch and slides, mixer readback, vocal setup, finals). It works only on copies in `<root>\_fl-headless-check\`,
   saves and reopens your song at the end, and recycles the sandbox. Leave FL alone while it runs.
10. `fl_job(<job>)` shows "check i/n". When it says **needs_you**, do the step it describes (e.g. File > New, or
    run the notes script once in the piano roll) and answer `fl_pc_check(answer="done")` (or `"skip"`).
11. At the end you get pass/warn/fail per check, pictures to look at for the "look:" warnings, and a bug-report zip
    if anything failed - send that and the report.md path. `fl_pc_check(full=true)` (about 35 min) adds the
    lower-risk checks, the mic and MIDI-keyboard ones; any day later is fine.

### C2. Realism check (5 min) - before rebuilding songs
12. On the check's sandbox song (or a COPY with a BBC SO / LABS strings channel): `fl_plugin_params(index=<that
    channel>, filter="CC")` must list "CC 1" / "CC 11" style parameters (realism.md 1 - the core assumption).
    Then `flp_realism(project=<copy>)`, play the strings bars: the dynamics knob in the plugin should move with
    the "<channel> dynamics" clip. If it doesn't, stop and send the output - the parameter base is fixed in data.

## D. Manual fallback - only if fl_pc_check can't run (40-50 min)
8. **Which project is open (proof by save)** (core.md 18, 6.1-6.2). Copy a song to `flh-test\Beat.flp` and
   `flh-test\old takes\Beat.flp`. Open the second by hand, change something, don't save.
   `flp_set_tempo(path=<flh-test\Beat.flp>, bpm=101)` -> result names `old takes` in `fl_has_open`, FL keeps your
   unsaved change. *If FL loses the change or the wrong file is edited: stop all live editing, send the bug report.*
9. **Render inside the open FL** (core.md 1.1-1.3, 1.5). Small saved project open. `fl_render_probe()` then
   `fl_render_probe(stage="export_dialog")` -> `ok: true`, `cancelled: true`, no dialog left in FL. Then
   `fl_render(project, method="in_fl")` -> a WAV, FL never closed, `steps_s` filled. *If a dialog stays open: close
   it by hand, note its title; `fl_render` keeps using the command-line export, nothing else is affected.*
10. **Live notes** on a COPY of a song, open and saved (core.md 2.1-2.5). `fl_live_notes_probe()`; the first time,
    run Tools > Scripting > fl_headless_notes once in the piano roll as `next` says, probe again ->
    `live_notes_enabled: true`, pattern unchanged. Then `flp_replace_part(... notes=[[1, 1, "C4", 90]])` -> `live:
    true, verified: true`, no reload. *If it fails, it stays off (`live_notes: false`); edits keep using reloads.*
11. **Lazy reload on a big project** (load > 8 s) (core.md 3.1-3.4, 6.5). `fl_open(big)`; `fl_status()` load cost
    within ~20% of a stopwatch. Two edits -> `queued: true`, FL untouched. Rename a mixer track by hand, then
    `flh_sync()` -> ONE reload, both edits there AND your rename kept. *If your rename is lost: report it and use
    `flh_sync(mode="immediate")` until fixed.*
12. **Plugin knob safety** - transport STOPPED first (mixeng.md 7, "Transport safety" 1; core.md 6.7). On a scratch
    copy with Fruity Parametric EQ 2 and Fruity Limiter on insert 1: `fl_plugin_map_learn(track=1, slot=0)` ->
    knobs restored exactly. Only then, playing quietly: `fl_plugin_set_named(1, 0, {"band3_gain_db": -3})` -> no
    audible jump. Asking +40 dB is clamped or refused. *Any loud spike: turn monitors down, stop, report.*
13. **New song from a template** (newsong.md 8, 12-14, 23; compose.md 1). `flh_new_song(genre="lofi")` -> FL opens
    the new song: dark-colored channels with factory drum samples, buses, sends, master EQ + limiter, markers, plays.
    Check: no demo/trial warning on save (Producer); Keys uses LABS if installed, else a FLEX channel - does FLEX make
    sound before a preset is picked?; `flh_new_song(genre="trap", starter=false)` 808 is in key. Then
    `flp_load_sample` on a copy (newsong.md 5).
14. **Slide notes (flag 0x08)** on a COPY with a bass (core.md 5.9-5.10; compose.md 2). The second note shows FL's
    slide marker and bends the held C2 up. *If not: 808 glides in compose come out as plain notes; report it.*
15. **Vocals with a mic** (vocal.md 1-3; core.md 5.4-5.5). `fl_vocal_setup(input="In 1")` -> a free insert named
    Vocal and armed; `fl_vocal_level_check(seconds=5)` while singing; `fl_vocal_record(start_bar=9, end_bar=17,
    takes=2, punch=true)` -> takes named and journaled, punch markers at 9 and 17 (or a clear "punch unavailable").
16. **MIDI capture with a keyboard** (transcribe.md 0-1; compose.md 15). `fl_capture_probe()` lists the keyboard;
    `fl_capture_idea(bars=4)`, play -> notes in a new "Idea N" pattern, metronome/mode restored. Then
    `capture_and_build(project=<song>, what=["chords", "bass", "drums"])`.
17. **Manual index** (handbook.md 1, 5, 6). `flh_handbook_build()` -> a background job (can run while you do other
    things); `fl_job(<id>)` until `complete: true`. Note duration, page count and `size_mb`. Then
    `fl_help("how do I sidechain a kick")` -> `source: local manual index`.

## E. When something fails
Run **`flh_bug_report()`** and send the zip path plus the step number and what you saw. It holds errors with
tracebacks, versions and the setup check, with user names replaced by `<user>` - no audio, no project contents.
