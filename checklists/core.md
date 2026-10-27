# Core (fl_headless_mcp.py, flh_setup.py, fl_script/) - checks to run on a Windows PC with FL Studio

The order matters: step 0 is needed for everything after it, then the riskiest new parts come first. Every step
says what to run and what you should see. If a step fails, stop and run `flh_bug_report()`: it makes a zip with the
error and its traceback (user names replaced by `<user>`).

## 0. Prepare (5 min)
1. Restart the MCP client (or `flh_reload`), then `fl_status`. Expect: no `extension_errors` for the core modules,
   `reload_mode: {mode: auto, ...}`, `render_in_fl: false`, `live_notes: false`.
2. **User data folder (the OneDrive bug).** `flh_setup()` and look at `fl.user_data`:
   - Expect `path` = `C:\Users\<you>\Documents\Image-Line\FL Studio` (the local one FL is set to), NOT the OneDrive copy.
   - `layer` says why. Best: `fl_setting` (FL's own registry value). Note which `why` text you get.
   - `others` lists the OneDrive copy with its evidence.
   - If `layer` is `activity` or `first` (a guess): run `fl_settings(query="image-line")` and
     `fl_settings(query="user data")` and note which registry value holds FL's User data folder (Options > File
     settings). Send that key/name: the registry scan should catch it by name. Meanwhile fix it with
     `flh_setup(action="apply", user_data="C:\\Users\\<you>\\Documents\\Image-Line")` and check `layer: setup`.
3. `fl_bridge_setup(action="update_script")` then restart FL (FL only loads a changed MIDI script on start). Expect the
   script path inside the folder from step 2 (`...\Documents\Image-Line\FL Studio\Settings\Hardware\...`).
4. `flh_setup()` again with FL open and the bridge answering. Expect `fl.user_data.layer` now `fl_setting` or
   `script_loaded` (the script reported where FL loaded it from). PC-VERIFY: if `script_loaded` never appears, FL
   doesn't define `__file__` for MIDI scripts - report it (`flh_call("fl_ping")` still works, nothing breaks).

## 1. Render from the open FL (riskiest: drives FL's export dialogs)
Use a small saved project first.
1. `fl_render_probe()` (stage save_dialog). Expect `ok: true`, `seen.save_dialog.file_name` = the project name,
   `cancelled: true`, and no dialog left in FL (`fl_dialog()` lists none). FAIL modes to report: `why` text,
   a Save dialog still open (close it by hand), FL showing something else after Ctrl+R.
2. `fl_render_probe(stage="export_dialog")`. Expect `ok: true`, a picture of FL's export settings window
   (`seen.export_dialog.title/class`), `cancelled: true`, `file_written: false`. Note the title and class it shows.
3. `fl_render(project, method="in_fl")` on that small project. Expect `method: in_fl`, a WAV next to the project,
   `steps_s` with save_dialog / export_dialog / start / render, FL never closed. Listen to the WAV once: same as an
   export by hand? PC-VERIFY: Enter starts the export (if `didn't start rendering on Enter`: say which button starts it).
4. Same with a big project (30 s+ load): `fl_render(big, method="in_fl")`. Expect no reload/close at all, render
   time roughly FL's own export time. Then `fl_render(big)` (auto) must also pick `in_fl` now.
5. Failure path: leave a modal FL message box open (e.g. File > New on a changed project shows 'save changes?'), then
   `fl_render(project, method="in_fl")` -> refused up front ("FL shows a popup"), nothing pressed. Answer it by hand.
6. `fl_finalize(project=small, background=false)` -> `render_method` is auto/in_fl, FL stays open, final written.
7. mp3: `fl_render(project, formats="mp3", method="in_fl")` (Shift+Ctrl+R). PC-VERIFY: that shortcut is MP3 export.

## 2. Live notes without reload (experimental: piano-roll script + Ctrl+Alt+Y)
Use a COPY of a song, open in FL and saved.
1. `fl_live_notes_probe()`. First time expected: `live_notes_enabled: false` and `next` asking you to run
   Tools > Scripting > fl_headless_notes once in the piano roll (it does nothing by itself). Do it, then probe again.
   - If the script isn't in FL's script list: check `steps.scripts_folder` is `...\FL Studio\Settings\Piano roll scripts`.
2. Probe again. Expect `steps.add_probe_note.fl_holds_it: true`, `steps.restore.fl_holds_it: true`,
   `live_notes_enabled: true`, and the pattern looking exactly as before.
   Note `receipt` in both steps: `done` = FL lets piano-roll scripts write files; `null` = it doesn't (fine).
   PC-VERIFY items it answers: apply() runs without a dialog; FL re-reads the .pyscript on Ctrl+Alt+Y (the restore
   step uses a new job - if the add worked but restore says `fl_holds_it: false`, FL ran a cached old script);
   Ctrl+Alt+Y reaches the piano roll after the bridge focused it; channel indexes (global) match.
3. `flp_replace_part(project=<open copy>, output=<same>, channel=<n>, notes=[[1, 1, "C4", 90], [2, 1, "E4", 90]])`
   Expect `live: true, verified: true`, no reload (FL doesn't flicker/reload), the notes visible in the piano roll.
4. Run some OTHER piano-roll script by hand (any built-in), then step 3 again. Expect `live: false` with
   `live_note` (its dialog was cancelled, or the notes didn't match), the edit still correct via a reload, and
   `fl_status().live_notes: false` until the probe passes again.
5. Ctrl+Z in FL after step 3: is the live write one undo step? (report yes/no)

## 3. Lazy reloads (big project, load > 8 s)
1. `fl_open(big)` then wait; `fl_status()` -> `open_project.load_cost.seconds` ~ the real load time (stopwatch).
   PC-VERIFY: within ~20% of the stopwatch.
2. Two edits: `flp_set_tempo(path=big, bpm=<x>)`, `flp_replace_part(project=big, output=big, channel=..., notes=...)`
   (with live notes off: `flh_sync(mode="lazy")` is not needed, auto is lazy above 8 s). Expect `queued: true` each,
   instantly, FL untouched. `flp_notes(path=big, channel=...)` shows the new notes + `pending_ops: 2`.
3. In FL, by hand, rename a mixer track (an edit FL holds in memory only).
4. `flh_sync()` -> ONE reload. In FL: tempo changed, new notes there, AND your mixer rename still there.
   `verify_job` -> `fl_job(<id>)` -> `verified: true`.
5. Queue an edit, then `fl_listen(start_bar=1, seconds=5)` -> it syncs first (one reload), then plays.
6. Queue an edit, then `fl_open(other)` -> no reload of big; afterwards `flp_info(big)` shows the edit in the file.
7. `flh_sync(mode="immediate")` -> edits reload at once again; `flh_sync(mode="auto")` to go back.

### 3b. Live changes + queued edits, tempo lanes, snapshots (2.2.0, PC-VERIFY)
1. Live change kept by a queued edit: on a big song in lazy mode, `fl_plugin_set` a mixer effect knob (e.g. a master
   reverb's wet), `fl_channel_set(index=..., volume_db=...)`, then `flp_edit(project, ops=[{tool: flp_replace_part,
   ...}])` (queued) and `flh_sync()`. In FL afterwards: the new notes AND the knob / channel volume as set live.
2. The same after a reload that couldn't be proven (`steps_s.content_check: "can't tell"` or a WARNING 'isn't
   proven'): `fl_plugin_set` again, queue an edit, `flh_sync()`. Expect either the edit in FL with the knob kept
   (`steps_s.live_probe` says the live changes were kept) or a clear refusal naming the live tools, with the file
   unchanged and the edit still queued (`fl_status().pending_ops`); never a silent loss of the knob.
3. FL's 'changed' flag after a live `fl_plugin_set`: does FL show the project as modified (title '*')? (fl-headless
   no longer relies on it: it logs live changes itself.)
4. Tempo lane: a song with `flp_tempo_write` points (bar 1 on), stop playback mid-song, queue a replace_part,
   `flh_sync()`, then another edit: no 'an earlier edit didn't stick ... tempo' alert and no 'changed by hand' note.
   Then stop the playhead somewhere else and repeat: still no alert. FL's master tempo in the file follows the
   playhead (that is expected and ignored).
5. `flp_edit` with `flp_tempo_write` + several `flp_replace_part` ops, `flh_sync()`: `flp_tempo_map(project)` and
   FL's tempo display at a few bars show the NEW map; `fl_job(verify_job)` -> verified.
6. A song that has a tempo AUTOMATION clip (drawn in FL): `flp_tempo_write` returns a WARNING naming the bars the
   automation covers.
7. `flp_remove_channels(project, channels=[...])` and `flp_write_arrangement(template, output=<open song>, ...)` in
   lazy mode: `queued: true`, FL untouched until `flh_sync()` (one reload), then both in FL; the song's journal
   channel roles renumbered once.
8. `fl_snapshot(region=[0, 0, 0.5, 0.3])` right after a `flh_sync()` reload: a picture (or `note: ... taken again`),
   not 'came back blank'. A region over a black part of FL still returns the picture.

## 4. Bug report
`flh_bug_report()` -> a zip path in `%LOCALAPPDATA%\fl-headless\bug_reports`. Open it: no user name, no audio, no
project contents; errors.json has the last errors with tracebacks.

## 5. Wave 2 (core): bridge script v3, file-format additions, tool tiers
Prepare: `fl_bridge_setup(action="update_script")`, then RESTART FL (FL only loads a changed MIDI script on start).
1. `fl_ping()` -> `version: 4` (3 before the review fixes) and no `script_outdated`. With the old script still loaded you should see
   `script_outdated` telling you to update + restart.
2. **plugin_probe.** Put Fruity Parametric EQ 2 on insert 1 slot 0. `fl_plugin_params(1, 0, "gain")` -> note one
   band's `param` and `value` (now full precision, not 4 decimals). `fl_plugin_probe(index=1, slot=0, param=<p>,
   points=9)` -> `texts` from about -18 dB to +18 dB, `restored == before`, and the band sounds/looks unchanged.
3. **Send levels** (FL 2024+, API 36). `fl_mixer_set(track=1, send_levels={"2": 0.5})` -> insert 1 routes to 2 and
   the send knob moves; the reply's `sends` shows `{"to": 2, "level": 0.5}`. Then `send_db={"2": -6}` -> hover the
   send knob: about -6 dB. PC-VERIFY that send knobs use the fader curve (0.8 = 0 dB); report the dB FL shows.
   On FL 21 (older API) expect a clear "setRouteToLevel is not available" error, nothing changed.
4. **mixer_info / mixer_arm / mixer_peaks** (used by fl_vocal_*). `fl_mixer_info()` lists tracks with `used` true for
   inserts a channel plays into. `flh_call("fl_vocal_setup")` arms an insert: its round record button lights;
   calling it again doesn't disarm it. With a mic on that insert and FL stopped, `fl_vocal_level_check` peaks move
   (PC-VERIFY: getTrackPeaks follows the input while stopped).
5. **rec_setup.** `fl_vocal_record(start_bar=9, end_bar=17, takes=1, punch=true)` -> FL's punch in/out points
   appear at bars 9 and 17 (PC-VERIFY that FPT_PunchIn/Out set them at the song position in song mode; if not, tell
   us - rec_setup then gets removed so vocal recording falls back to pre-roll takes). The song position is put back.
6. **countdown / loop_record.** `flh_call("fl_song", {"action": "where"})` shows `countdown` and `loop_record` equal to
   FL's countdown and loop-record buttons (Options > recording / transport panel). `fl_song(action="countdown")`
   flips the button; the reply shows the new state. Same for `loop_record`. fl_capture_idea uses both.
7. **pattern_new.** `flh_call("fl_pattern_new", {"name": "Idea 9"})` makes the next empty pattern current with NO
   name prompt, named "Idea 9" (FFNEP_DontPromptName). With > 64 patterns `fl_state()` shows `pattern_count` > 64 and
   `patterns_capped: true`.
8. **Channel insert from the file.** On any song: `flp_list(project)` channels carry `insert`; compare with
   `fl_state()` channels' `insert` (FL's channels.getTargetFxTrack). All must match (event 104 in FL 2020-2026; event
   22 in older files). Report any mismatch with the FL version.
9. **Slide notes.** On a COPY of a song with a bass channel: `flp_replace_part(project=<copy>, output=<copy>,
   channel=<bass>, notes=[[1, 1, "C2", 100], [1.5, 0.25, "G2", 100, {"slide": true}]])` -> FL's piano roll shows the
   second note as a slide note (slide triangle) and it bends the held C2 up to G2 (PC-VERIFY flag 0x08).
   `flp_edit_notes(..., ops=[{"op": "slide", "on": false}])` turns them back into plain notes. With live notes on,
   both edits say `live: true` (no reload) and the piano roll loses the slide triangle at once; if FL's piano-roll
   script can't clear a slide the result says `live: false` + `live_note` "...didn't take the slide flags" and the
   edit went in by reload instead (fl_pc_check `files.slide_flag` shows `slide_off_route`).
10. **Porta / mono.** `flh_call("flp_channel_glide", {"project": <copy>, "output": <copy>, "channel": <bass>})` ->
    the channel's settings (Misc functions > Polyphony) show Mono and Porta ticked, Slide unchanged; overlapping notes
    glide. PC-VERIFY event 221 = max voices u32, slide u32, flags (1 mono, 2 porta).
11. **Empty projects.** File > New (empty template), save as `empty.flp`, close it in FL. `flp_new_pattern(path=empty,
    name="Verse", place_at_bar=1)` then `flp_add_notes(path=empty, pattern="Verse", notes=[{"note": "C4", "bar": 1}])`
    -> open it: pattern "Verse" in the playlist at bar 1 with the note. PC-VERIFY the clip record template
    (fl_blocks.AUTOMATION_CLIP turned into a pattern clip) and where the name group is inserted (after the first
    channel). If the new project had no playlist event at all, the clip still appears.
12. **Markers and meters.** A song with playlist markers incl. a time-signature marker (e.g. 3/4 at bar 9):
    `flp_info` -> `markers` bars and names as FL shows them, `time_signature_changes` bar 9 = 3/4.
    PC-VERIFY (2026-10-23 sweep, files.markers_meter failed): markers fl-headless writes now go after the
    arrangement's playlist event and its track groups (`_marker_slot`). After FL's load + Ctrl+S they must still be
    on the ruler and `flp_info` must list them; `markers_note` saying they sit on a pattern means FL moved them
    (then send the saved .flp: where does FL itself put a marker you add on the ruler?).
    `flp_export_midi` -> import the .mid into FL (or any DAW): the time signature changes at bar 9.
13. **Tempo lane.** On a COPY of a song with no "Tempo" pattern: `flh_call("flp_tempo_write", {"project": <copy>,
    "output": <copy>, "points": [[1, 100], [9, 120]]})` -> `created` (pattern + clip track); FL's tempo display jumps
    to 120 at bar 9 while playing. `moved_tempo_events` > 0 means tempo events recorded in other patterns (MIDI
    imports) were removed so they can't fight the new lane - check the song still starts at the right tempo.
14. **Tempo automation.** A song where you automate FL's tempo (right-click the tempo > Create automation clip, ramp
    100 -> 140 BPM): `flh_call("flp_tempo_map", {"path": song})` -> `tempo_automation_clips: 1` and the map ramps
    100 -> 140 over the clip. PC-VERIFY two guesses: the clip's link ids (dest 0x4000, param 5 = REC_Tempo) and the
    value mapping 0..1 -> 10..522 BPM. If the clip isn't found or the numbers are off, send the project.
15. **Tool tiers.** Restart Claude: the client lists 45 tools (of 183). `flh_tools()` shows every group;
    `flh_tools(name="fl_render")` the full help; `flh_call(tool="fl_render", args={...})` runs an unlisted tool.
    `flh_profile(set={"toolset": "full"})` (or env FLH_TOOLSET=full) + client restart lists everything.
16. **Parse cache vs FL saves.** Open a song, `flp_info(song)`, change the tempo in FL, save (Ctrl+S), then
    `flp_info(song)` again at once -> the new tempo (the cache is keyed by mtime/size and re-reads files saved in the
    last 2 s).

## 6. Review fixes (core): open-project identity, verify vs hand edits, safety rules, bridge v4
Prepare: `fl_bridge_setup(action="update_script")`, restart FL, `fl_ping()` -> `version: 4`.
1. **Exact open project (C1).** Open `Beat.flp` through Claude (`fl_open`). In FL: File > Save as `Beat 2`, change
   something, DON'T save. Ask Claude to `fl_open(Beat.flp)` again. Expect: FL asks "save changes to Beat 2?" and
   fl-headless does NOT answer No for it (it answers Yes/Cancel or stops with a message) - your Beat 2 changes
   survive. Also float a plugin window titled "Beat - Sytrus": `fl_status().open_project` must still be the project
   in the MAIN window title (or none), never Beat.flp because of the plugin window.
2. **Still loading = refuse (C1 reverse).** Open a big project and, while FL's title doesn't show its name yet, run
   `flp_set_tempo(path=<that project>, bpm=...)`. Expect "can't tell for sure whether FL has ... open", file unchanged.
3. **Hand edits after a reload (C2).** Edit the open project (`flp_set_tempo`), and within 2 s draw a few notes in FL.
   Wait 30 s, `fl_job(<verify_job>)`: expect `verified: false, hand_edits: true` (or `verified: true` with
   `hand_edits: [...]`) and your drawn notes STILL in FL; `flp_versions` lists the edit as a backup. Never an
   "FL kept an old copy" restore over your notes.
4. **Truncated save (C3).** Put a 5 MB+ project on a OneDrive / network folder; edit it while open. No "file
   incomplete" error after FL finished saving; `steps_s.save` is ~0.3 s longer than FL's save. PC-VERIFY that a
   slow save never leads to an edit of a cut file (the file size after the edit >= the size FL saved).
5. **Big projects (C4).** A project with 150+ channels: `flp_set_tempo` on it while open -> reloads (no "FL didn't
   save in time"); `fl_state()` returns (`channels_capped` true over 256). PC-VERIFY replies over 127 SysEx chunks
   arrive (14-bit framing, direction 3) on loopMIDI and on the Windows MIDI Services loopback.
6. **Global channel indexes (H8).** In the channel rack pick a filter group that hides channel 0..3. Run
   `fl_channel_set(index=5, muted=true)` -> global channel 5 (as in the .flp / `flp_list`) is muted, not the 6th
   channel of the group. `fl_state()` lists ALL channels. `fl_play(5, chord="C4 E4 G4")` plays channel 5.
   PC-VERIFY on FL 20/21 (API < 33): the scripts fall back to group indexes - say which FL version.
7. **Setting a knob by its display (H3 audio).** Put Fruity Limiter on insert 1, play a loop, and
   `fl_plugin_set_named` / `flh_call("fl_plugin_set_value", ...)` its gain to +3 dB. PC-VERIFY: no audible jump to
   full gain at any moment; the knob moves only between where it was and +3 dB. Ask for +40 dB (out of range) ->
   error, knob back where it was.
8. **Recycle Bin only (H6).** `files_recycle([<file on a USB stick or network share inside FLH_ROOTS>])` -> refused
   ("no Recycle Bin"), the file is still there. On C: -> it lands in the Recycle Bin. PC-VERIFY that
   SHQueryRecycleBinW fails / GetDriveTypeW isn't 3 for those drives, and that a file too big for the bin shows
   Windows' "delete permanently?" question (answer No -> "could not recycle", file kept).
9. **No second FL, no surprise closing (H5).** `fl_render(project, allow_while_running=true)` -> refused.
   `fl_settings_set([...])` with FL open -> "Call again with restart_fl=true"; with `restart_fl=true` FL saves,
   closes politely and reopens. Same for `fl_bridge_setup(action="use_loopback")` and
   `fl_controller_script(..., assign=true)`. `fl_load_preset(preset, channel=2)` on the open project -> FL reloads
   in place, never closes. `fl_close()` without `confirm=true` -> refused.
10. **FL lock (H10).** Start `fl_render_stems(project)` (a job) and right away `fl_transport("play")` -> "FL is busy
    (job stems-N ...)" within ~2 s, nothing pressed in FL. `fl_open(song)` then within 40 s `fl_listen(...)` ->
    the listen is not stopped by the autoplay stopper.
11. **FL 20 (32-bit FL.exe) (M14).** `fl_status().fl_running` is true while FL.exe runs; `fl_open` with FL closed
    starts the FL from `fl_exe()` (not whatever the .flp association points to).
12. **Templates folder.** `flh_save_template(project, "My Base", fl_templates=true)` -> `fl_template` is
    `<user data>\Projects\Templates\My Base\My Base.flp`; FL lists it under File > New from template. PC-VERIFY the
    folder FL 2026 reads user templates from.
13. **Snapshot alt text.** `fl_snapshot()` -> `alt` names the window title, the focused FL window and any popup.
14. **Installer.** `python install.py "D:\Music; D:\FL Projects"` -> config `FLH_ROOTS` = `D:\Music;D:\FL Projects`,
    `args` = `<folder>\fl_headless_launch.py`, `claude_desktop_config.json.bak` refreshed on every run (and
    `.original.bak` from the first run). Claude starts the server through the launcher. Then:
    - `python install.py --help` prints the usage and changes nothing (config's modified time unchanged); `python
      install.py --roots x` and `python install.py Music` are refused (exit 2), nothing written.
    - Add `"FLH_TOOLSET": "full"` to the entry's `env` by hand, run `install.bat` again and press Enter at the
      folders question: `FLH_TOOLSET` and `FLH_ROOTS` are still there; only `command` / `args` were refreshed.

17. **Project file structure (fix B).** On COPIES of real songs:
    - *Pattern settings:* a song whose pattern 1 has a custom length / time signature: `flp_new_pattern(copy, "New",
      output=copy)`, open in FL -> pattern 1 keeps its length and time signature; "New" exists and is empty.
    - *Unnamed channel:* a channel you never renamed: `flp_style_channels(copy, copy, [{"channel": N, "name": "X",
      "color": "#203040", "icon": 12}])` -> FL shows the name, color and icon on that channel; the mixer is unchanged
      (the name/color/icon events are inserted after the plugin header when FL left them out).
    - *Arrangements:* a song with 2+ arrangements, the second one selected in FL, saved: `flp_info` lists them and
      the clip count is the selected one's; `flp_playlist(copy, "place", ...)` puts the clip in the selected
      arrangement (PC-VERIFY: event 100 = the selected arrangement).
    - *FL 20 / FL 21 projects:* `flp_playlist(old.flp)` lists the same clips (bars, tracks) FL 20/21 shows; mute one
      clip and reopen in that FL version -> only that clip is muted (PC-VERIFY: 32-byte / 60-byte clip records).
    - *Pattern clips without stored offsets:* a clip whose start/end offsets are unset (-1) plays from the pattern
      start (flp_info's notes match what FL plays).
    - *flp_replace_part on a layered song* (kick/808/chords/melody as separate patterns stacked at the same bars,
      some sections repeated): replace the 808 part -> only the 808 patterns change; the result names the repeated
      patterns; FL plays slides on the slide notes it was given.
    - *flp_add_channel:* clone the LAST channel -> the copy appears at the end of the channel rack and plays.

18. **Which file FL has open (fix A2: F1/F5/F6/F12).** FL's title shows only a name, so fl-headless proves the
    open file with a save through the bridge (the file whose mtime/size changes is the one FL holds).
    - *Same name twice:* copy a song to `album\Beat.flp` and `album\old takes\Beat.flp`. Open the second one by hand
      (File > Recent), change something without saving. `flp_set_tempo(album\Beat.flp, 101)` -> result has
      `fl_has_open` naming `old takes`, FL keeps its unsaved change (it was saved, not discarded), the first file
      has tempo 101. `flp_set_tempo(old takes\Beat.flp, 102)` -> `reloaded_in_fl: true`, FL shows 102.
    - *FL's default name:* save a new project as `Project 3.flp`, open it with fl_open, then edit it with any flp_*
      writer -> it works (`reloaded_in_fl: true`). With a NEW unsaved project named "Project 3" in FL and a saved
      `Project 3.flp` elsewhere: an edit of the file opens FL's Save As for a moment, fl-headless presses Esc
      (PC-VERIFY: FPT_Save on a never-saved project opens Save As, and Esc closes it without saving) and the file is
      written directly (`fl_has_open: a new, never-saved project ...`).
    - *Project-info title:* a project saved as `mysong_v3.flp` with Project info title "My Song", opened by hand ->
      FL's title shows "My Song" (PC-VERIFY) and edits work.
    - *Trial warning:* a project with a demo/trial plugin: fl_open of another project -> FL's demo warning
      (PC-VERIFY: its exact text and buttons; `fl_dialog list` shows them) is answered with Esc/Cancel, nothing is
      saved, the refusal says so. A project NAMED "Demo 2" or "trial run": edits reload normally.
    - *Trial warning on the identity save (M3):* the same demo-plugin project open in FL: `flp_set_tempo(<it>, 101)`
      and `fl_open(<it>)` -> within ~1 s FL's warning is closed with Cancel and the refusal names the demo plugin
      problem ("Save anyway" never pressed, the file unchanged, no Open dialog driven afterwards). PC-VERIFY: the
      bridge save (FPT_Save) shows the same warning as Ctrl+S.
    - *Unsaved new project + fl_open (H2):* File > New, add a note (don't save; FL shows "Project N"). `fl_open(<a
      saved song>)` -> refused with "FL has an unsaved new project open: save it or close it in FL first"; FL's
      "save changes?" was cancelled, no Save As box is left open, the song file is unchanged (modified time) and FL
      still shows the new project with its note. PC-VERIFY in another FL UI language (where "Project N" may not be
      recognized): Yes is answered, Save As appears, fl-headless presses Esc and refuses the same way; the target
      path is never typed into Save As.
    - *Slow saves (M2):* a 20 MB+ project on a OneDrive / network folder: edits while it's open still reload
      (the save wait follows the file while it keeps growing, up to 60 s) instead of "FL didn't save in time".
    - *During a render job:* start `fl_render_batch` (FL renders), then `fl_dialog(action='list')`,
      `fl_snapshot()`, `fl_context()` -> they answer at once (not "FL is busy"); `fl_play` says busy within 2 s.

## fl_listen timing (added overnight, PC-VERIFY)
- `fl_listen(start_bar=1, seconds=2)` -> result has `steps_s` {sync_queued_edits, fl_setup, preroll, play, tail,
  analyze}. Expect play ~2 s, preroll 0.6 s, tail 0.35-1.5 s (shorter on a dry song), analyze < 0.5 s. Whatever
  makes the total much longer than ~4-5 s shows here: report the numbers (that's the "12 s for 2 s" case).
- A song ending in a long reverb: tail stays near 1.5 s and the recording keeps the ring.

## Stems in the open FL (PC-VERIFY)
- After one working in-FL export (`fl_render(project, method="in_fl")` or `fl_preview()`), run
  `fl_render_stems(project, groups="instrumental")` with FL open -> `method: "in_fl"`, FL never closes, two FLAC
  stems; the vocals stem has only the vocal channels; channel mute buttons in FL look exactly as before.
- With a channel muted by you before the run: it stays muted and is absent from every stem.

## Long calls handed over as jobs (PC-VERIFY)
- `fl_render(project, method="cli", close_fl=true)` on a song whose render takes over a minute -> after ~45 s the
  call answers `{"job": "fl_render-N", "status": "running"}`; `fl_job("fl_render-N")` later returns the rendered
  file. Meanwhile `fl_open(...)` is refused with "fl_render is still running in the background", `fl_state` works.
- Through the remote bridge (Claude on the web/phone) no call ends in a client timeout any more.

## Bridge on a keyboard input, no virtual port (PC-VERIFY)
- With a MIDI keyboard connected and loopMIDI closed: `fl_bridge_setup(action="use_input")` lists
  inputs; `fl_bridge_setup(action="use_input", port="<keyboard>", restart_fl=true)` -> FL restarts once, `done: true`.
  Then `fl_ping` answers, the keyboard still plays notes into the selected channel, its knobs still move what they
  moved before (generic linking). `undo_input` -> the keyboard's MIDI settings exactly as before.

## flh_tidy batches (PC-VERIFY)
- `flh_tidy()` then `flh_tidy(apply=true)` with 100+ old backups: one Recycle Bin operation (one undo entry), all of
  them restorable from the bin; `removed` matches the dry run's count.

## MP3 / M4A via Windows Media Foundation (PC-VERIFY)
- `fl_pc_check(areas=["render.mp3_read"])` (after render.in_fl passed): FL's MP3 export decodes, loudness within
  1 LU of the WAV. Then `audio_info(path="<any .m4a from your music folder>")` -> rate/channels/LUFS, no error.

## Edits that call writers inside, failed reloads, merged-edit speed (2026-10-22, PC-VERIFY)
- Nested writers: `flp_extend_ending(project)` on the song open in FL -> `steps_s.edit` is about a second (not ~31 s),
  ONE save and ONE reload in FL, `reloaded_in_fl: true`, FL shows the held chord and the longer clip. `flh_bug_report`
  lists no `verify_leaks`.
- Failed reload: make the reload fail (e.g. open a modal FL dialog right after the edit's save, so FL's Open dialog
  can't come up) -> result `fl_shows_old_version: true`. Then `fl_save()` answers `saved: null` with "FL holds an older
  copy", the .flp's modified time does NOT change (also not ~10 s later: no background check, no merged-edit flush
  saves). `fl_open(project)` loads the file without saving first (mtime unchanged before the load) and FL then shows
  the edit; after that `fl_save()` works again. Alternatively the next edit of that project: no FL save first
  (`fl_held_older_copy` in its result), FL reloads, both edits are in FL.
- Switching away: with the stale project in FL, `fl_open(other)` answers No to FL's 'save changes?' (note says the
  stale file kept its newer edits) - the stale .flp's mtime doesn't change.
- Speed: send `flp_compose_clear` (or any lazy edit) while the merged reload of earlier edits is running (fl_status
  says "putting the last edits into FL") -> the call returns in well under a second (`queued: true`), and its own
  merged reload takes the normal ~1 s (it no longer waits for the previous reload's background check).

## Render cache, stems once, render counts (2026-10-22, PC-VERIFY)
Goal: a work session needs 2-3 FL exports (a draft, a final); level / fader tweaks need none. Measure it: start a
session, work as usual, then `fl_timings()["renders"]` (exports, export_s, reused, offline, by_route) and
`fl_render_cache()` (cache size, entries by kind, split export state).
1. Changed flag: open a song, `fl_preview()` twice -> the 2nd says `reused render (unchanged since HH:MM)`,
   `renders: 0`, no export window. Then move a fader BY HAND in FL and `fl_preview()` again -> `cache_note: saved FL
   first...` and a real export (FL's general.getChangedFlag saw the change). Right after: `fl_preview()` -> reused,
   and no second save (FL cleared the flag on the bridge save; if it saves every time, the flag isn't cleared).
2. Volatile events: save an unchanged song twice in FL (Ctrl+S), `fl_preview()` between -> still `reused`. If not:
   find what FL rewrote (`flp_diff` of two saves) and add the event id to fl_render_cache.VOLATILE_EVENTS.
3. Live fader through the bridge: `flp_mixer` (live route) on a song with a stems set -> `fl_preview()` gives
   `offline: mixed from cached stems ...`, `renders: 0`; `fl_preview(exact=true)` renders: lufs within ~0.3 LU and
   true peak within ~0.5 dB of the offline numbers when the master has a limiter ("approximate"), within 0.05 LU
   without master effects ("exact").
4. Split mixer tracks: tick 'Split mixer tracks' once in FL's export window (FL keeps it), then `fl_render(song,
   method='in_fl')` -> one export, the user's file is the master, `fl_render_cache()["split_export"]["on"]` is true
   with `tracks` = the mixer tracks that have sound and `unmatched` = 0. Check FL's file names: the code expects
   '<file>_<track name>.wav' (and 'Insert N' / 'Master'); write down FL's real pattern (two tracks with the same
   name: 'Kick', 'Kick 2'?). Check the track files are after fader + pan (move a fader, export again: that file's
   level moves by the same dB) and carry no master effects with 'Enable master effects' off (sum of the files of the
   tracks routed to the master == the master before its chain).
5. Teaching the checkbox: `fl_render_probe(stage='export_dialog')`, read the picture, `fl_render_cache(split_click=
   [x, y])` with the box's position as fractions of the window. With split unticked, `fl_render_stems(song,
   method='split')` -> FL ticks it (one click), ONE export, one stem per channel's mixer track, `renders: 1`. The next
   plain fl_render of ours unticks it again (check FL's export window afterwards). Until then the user's own exports
   split: say so if it stays ticked.
6. Pan law: pan a track live, compare `fl_preview()` (offline, says "pan law assumed") with `fl_preview(exact=true)`
   -> left/right levels agree within 0.5 dB, else fix fl_render_cache.pan_gains (FL's mixer pan law).
7. fl_render reuse: `fl_render(song)` twice -> the 2nd `reused`, FL not closed (cli route) / no export window
   (in_fl); delete the WAV and render again -> a real export. A bar range `fl_render(song, start_bar=9, end_bar=17,
   method='cli')` right after a full render -> `cut from the cached full render`, no export.
8. Cache size: `fl_render_cache(gb=0.2)` then preview/render a few versions -> `used_mb` stays under ~200; evicted
   folders are in the Recycle Bin (one operation per eviction, no 'delete permanently?' question for 50 MB files).
   `fl_render_cache(action='clear')` empties it. Nothing is ever written into song folders except the renders the
   user asked for (fl_preview's WAV only with keep=true).
9. fl_pc_check bypasses the cache (every check really exports): run `fl_pc_check(areas=["render"])` twice -> each run
   shows export windows.
10. Partial ranges: `fl_ending_check(song)` on a song with a long held last chord -> `predicted: pass`, `renders: 0`.
   On a borderline song with FL closed -> one command-line render of only the last ~45 s (`source: range render`,
   2 bars of pre-roll: the reverb at the cut sounds right); with FL open -> one full in-FL render, reused by every
   later check (`cached full render ... cropped`). A song with a tempo change before the ending: the cut lands on the
   right bar.

## Server overhead caches (2026-10-22, PC-VERIFY)
1. state.json memo on Windows: `flh_sync(mode='lazy')`, then edit state from a second Claude window (two servers) with
   `flh_sync(mode='immediate')` -> the first window's next `flh_sync()` / edit uses immediate within one call (the
   file's replace changes mtime/size/file index, so the memo can't hide it).
2. flh_reload after an update: result `steps_s.code_from` = 'compiled' the first time (~0.25 s), 'code_cache' on a
   second reload of the same code or in a second Claude window (< 0.06 s). The state folder holds one
   `code_cache\fl_headless_mcp.code` (~1 MB, overwritten, never more files).
3. Big project (10k+ notes): a burst of 5 `flp_add_notes` + `flp_info` is noticeably faster than before
   (`parse_cache_stats()` via flh_call shows `seeded` > 0: spliced projects aren't re-parsed).

## Slow PCs - speed factor (2026-10-22, PC-VERIFY)
1. Fast PC: `fl_status` -> `pc_speed: 1.0` after a few renders / loads / pings (the bases render 0.5 s per song
   second, load 10 s, ping 250 ms must leave a fast PC at 1.0; if not, raise `SPEED_BASE`). `fl_timings().pc_speed`
   lists the sources (`render` / `load` / `bridge` / `late`).
2. Weak PC (or FL with a heavy CPU load running beside it): the factor rises (`pc_speed.factor` 2..6) and edits,
   reloads, opens and renders that used to fail with 'no reply from FL within 15s' / 'Save dialog didn't open' /
   'no complete file after N s' now succeed; results carry `slow_pc: ["FL is still working (slow PC): ..."]` when a
   wait was extended once.
3. `_fl_cpu_busy()` (via `flh_call`): True while FL loads a big project, False with FL idle and stopped.
   `_fl_still_working()`: True while FL is loading (heartbeat stops), False after FL was closed.
4. FL frozen by a modal popup (e.g. a plugin's own dialog): a bridge call fails after its scaled wait (plus one
   extension if FL looks busy) with '... waited N s: a fast PC's 15 s x this PC's speed factor F', and a foreground
   call never waits past ~50 s (MCP clients give up at ~60 s).
5. The state folder holds one small `pc_speed.json` (overwritten; slow evidence older than 14 days is forgotten).

## Jobs holding FL, foreign file dialogs (2026-10-23, PC-VERIFY)
1. `fl_pc_check(areas=["lazy"])`: `lazy.big_project` passes; evidence `routes` shows `rename+icon: queued` and
   `sync.ops` equals the number of queued routes; edits that went live count as fine (no reload).
2. During `fl_pc_check` / `fl_bench` no alert "queued edits couldn't be put into FL automatically (... stayed busy
   ...)": a merged edit made by the job goes into FL in the job itself (`reload: now (a job holds FL ...)`). A
   "stayed busy" alert (only after 10 min of a non-job holder) names what held FL.
3. Record a take in Edison and leave its 'Save As' open: `fl_open` refuses at once naming 'Save As' and the .wav
   (no Ctrl+O typed); at the end of `fl_pc_check` the restore cancels the run's own take dialog
   (`restored.cancelled_popup`) and reopens the song. A Save As that isn't a take is named in `restored.note`.

## Same-title twins, long loads, FL behind the file (PC 2026-10-23)
1. Two .flp with the same Project-info title in FLH_ROOTS (a copy). `fl_open(copy)` with FL on the original, then
   `fl_render(copy, method='in_fl')`: the wait follows FL through 'Opening: <plugin>' (no failure while the title
   keeps changing), then one save through the bridge proves the copy (steps_s `identity_save`); the render is the
   copy's. A heavy project (60 s+ load): no "FL didn't open" while FL is still loading.
2. `flp_save_version(project, 'X Rework')`: a new .flp whose FL title is 'X Rework' (or 'X Rework (Piano)' when the
   source was titled 'X (Piano)'), its journal copied (song renamed, no final/review); FL shows the new title.
3. A guarded writer with output to a new file name (e.g. flp_project_info(path=a, output=b, genre=...)): FL shows b's
   own title after opening b.
4. Edit the open song while its reload fails (or FL showed another file and you then open it by hand): an in-FL
   render first reloads the file (`fl_loaded: reloaded`) - FL never exports its older copy - and FL's save is refused
   meanwhile ("FL holds an older copy").

## fl_open safety, merged-reload check, preview mutes, markers (PC 2026-10-23 regressions, PC-VERIFY)
1. Restore over the open song: with FL showing B, copy another version over B.flp on disk (Explorer), then
   `fl_open(B, save_current=false)`: steps_s has NO `identity_save`; B.flp's modified time is unchanged until FL has
   loaded it; FL shows the restored version (`via` says "save_current=false: not saved first"). Repeat with the
   default `fl_open(B)`: FL with unsaved changes -> refused ("isn't what FL last loaded ... save_current=false");
   FL with nothing unsaved -> reloaded, no save. An edit of B in that state refuses the same way (nothing written).
2. `fl_open(C, save_current=false)` while FL shows a changed project whose name fl-headless can't pin (two files with
   that name, or opened by hand from outside FLH_ROOTS): FL's 'save changes?' is answered No (not Cancel); C opens.
   FL 2026's own 'Save as' box for a changed 'Project_N' gets Skip.
3. `fl_open(other)` normally: the result comes back after FL's title names the project (or `loading` while a heavy
   project is still loading). If FL ever ignores the Open, steps_s has `first_open_ignored_s` and the second Open
   lands; twice ignored -> "FL didn't switch ... its title still shows ...".
4. Merged edit on a heavy project (load 10-60 s, measured): fl_status says "in FL now (one reload, N s)" only when
   FL really reloaded; a reload far faster than the load cost shows "checked by FL's save" (or the WARNING / "NOT
   confirmed" text). Then `fl_finalize`: the FINAL contains the edits (listen; `notes` say "rendered again" when the
   project file changed since a kept render).
5. `fl_preview(channels=[...])` then `fl_open(other)` right away: the preview refuses while FL is still switching;
   after any channel preview `fl_state` shows every channel unmuted again; if one stays muted the result has a
   WARNING and fl_save / the next whole-song preview don't save it into the file (they unmute it first when FL
   shows the song).
6. Markers: a song written by an older fl-headless (flp_info `markers_note` "scrambles"): any edit (e.g.
   flp_set_tempo to the same tempo) -> FL's playlist ruler shows every marker at its bar with its own name, and
   still does after FL's own Ctrl+S + reopen. New markers sit right after the playlist in the file (233 | 148 ...).
7. Focus (PC-VERIFY `_fl_foreground`): open the emoji panel (Win + .) or leave the PC idle ~10 min, then
   `fl_open(other)`: FL's Open dialog is driven without a click; `fl_timings` / the error names the way that worked
   (`_FG_WHY.how`: alt_tap, attach_input, switch_to_this_window, minimize_restore, title_click). title_click: the mouse
   pointer is back where it was and nothing in FL was clicked but its title bar (FL 2026's title bar must answer
   HTCAPTION at its top edge; if not, the click is skipped and the old error text shows).
8. `fl_open(heavy song)` then `fl_status` at once: `switching: {to: <song>}` (and `still_shown`), never the old song as
   `open_project`; once FL shows the song, `open_project` is it. A heavy load with an unchanged title is not "ignored"
   (no second Open dialog while FL loads).
9. Restart the MCP server (Claude Desktop) with FL left open on a song, copy another version over that song's file,
   then `fl_open(song)` (default save_current): refused ("isn't what FL last loaded") - the record survived the restart
   (state folder `fl_holds.json`).
10. Merged reload on a heavy song: `steps_s.load` is the real load time (seconds, not ~1 s); a mixer whose track 1 name
   FL cuts short still works (the tag falls back to `t1 ~flh`). If FL never shows the file's channel count, the
   result says "NOT confirmed"/WARNING instead of "in FL now".
11. `fl_render_stems(groups=..., method='in_fl', close_fl=false)` then `fl_open(other)` straight after: the stems refuse
   while FL switches; after any stems run every channel is unmuted (fl_state); no save writes stem mutes into the file.
12. Change something in FL (unsaved), start a merged edit (so a "checking that FL holds the last edit" runs), then
   `flp_compose(...)` right away: it waits a few seconds, FL saves, the composed part AND the hand change are both in
   FL after the reload (no `read_note` "FL is busy"). During a render job: `flp_compose` refuses ("did nothing").
13. Copy another version over the open song's file on disk, FL with nothing unsaved, then `fl_render(method='in_fl')`
   (or `fl_finalize` in FL): the file is reloaded first (`fl_loaded: reloaded`) and the render is of the copied
   version; with unsaved changes in FL it refuses ("FL's export would render FL's copy"). `fl_close(confirm=true)`
   in that state doesn't save FL over the copied file (`saved` names why; FL may ask about unsaved changes itself).
14. fl_open reload proof (PC 2026-10-23: a 'reload' of 1.2 s on a 10-26 s song kept FL's old copy; then the empty
   template route 'worked' by FL's title, FL still held the OLD note lengths and fl_finalize's 'saved FL first' wrote
   them over the file - data loss): with a heavy song open, put a version that differs only in note lengths /
   velocities over its file (a copy edited elsewhere, copied over it in Explorer), then
   `fl_reload_from_disk(song)` (and once more with `fl_open(song, save_current=false)`): FL's title shows the empty
   template, the template finishes loading (no 'Opening: ...'), then the song; `proof` says "marker", `steps_s`
   has `marker_wait`; `fl_save()` then `flp_notes` shows the NEW lengths (byte-check: staged file == the copy put
   over it), FL's mixer shows the original name of the marked track (an unnamed track: 'Insert N' again, nothing like
   'flh-xxxxxx' left; else `marker_left` says which track to rename) and the file on disk is byte-identical to the
   copy put over it before the save. `fl_preview` differs from the render before. If FL can't be made to load it (FL
   ignores the template, or brings its old copy back) the call FAILS ("FL didn't load ... OLDER copy", "without the
   marker") and then `fl_finalize` / `fl_preview` / `fl_save` refuse ("older") - the file keeps the new notes
   (staged byte check). A rename of a pattern on disk + an edit by fl-headless: the fast reopen is used and
   `steps_s.content_check` = "file" (no extra load).
   - PC-VERIFY 'reload marker': the bridge reads a mixer track's name set in the file (`mixer_set(track)` without a
     name) within a second of FL finishing the load; `mixer_set(track, name="")` puts FL's default name back.
   - PC-VERIFY '_template_loaded': after Ctrl+O on FL's empty template, `state(light=1)` gives the template's channel
     count and `title` (ui.getProgTitle) names the template - never the song - only once the template is in.
15. Retry leftovers (PC 2026-10-23: 'first_open_ignored_s 9.8 / 20.4', then a 'Confirm' and an Open dialog left in
   FL): `fl_open(v4, save_current=false)` then `fl_open(v5, save_current=false)` on 15-26 s songs: no
   `first_open_ignored_s` (the wait follows the load cost; a silent bridge / busy CPU counts as loading); afterwards
   `fl_dialog()` lists no popup. If a retry does happen, `popups_closed` names what was closed (save prompt: No when
   save_current=false, else Cancel; Open dialog: Cancel) and FL shows no popup.

## fl_preview format='mp3' and bar ranges (2026-10-23, PC-VERIFY)
Why: previews go to the user's phone; a 66 MB whole-song WAV had to be copied off the PC and converted (slow, one
upload failed), and every preview rendered the whole song even when one section changed.
1. MP3 export in the open FL: with a song open, `fl_preview(format='mp3')` -> FL's MP3 export runs (Ctrl+Shift+R,
   `RENDER_KEYS["mp3"]`; the export window names '<song>.mp3'), FL never closes; `share_path` is
   `<song folder>\_preview\<song> preview.mp3`, `share_mb` a few MB for a 4-minute song (FL's last MP3 bitrate),
   `measured_from: "mp3"` and `lufs` within ~0.3 LU of `fl_preview(format='wav')` of the same song. The file plays
   on a phone. Report the export seconds (`seconds_render`) of both formats.
2. FL's export window with WAV AND MP3 ticked (the user's own last choice): `fl_preview(format='mp3')` ->
   `measured_from` starts with "wav", no WAV left in `_preview`, `fl_render_cache()` / state `in_fl_split.on` NOT
   switched on (the extra WAV isn't taken for split mixer tracks). Report whether FL names the extra file
   '<name>.wav' next to '<name>.mp3' (`_also_formats_aside`).
3. No MP3 decoder (Windows N without the Media Feature Pack): `fl_preview(format='mp3')` -> the file still comes,
   `measure_skipped` says format='wav' gives the numbers (`mf_audio.available()`).
4. Ranges: `fl_preview(format='mp3', section='<a playlist marker>')` right after (1) -> `renders: 0`,
   `range_route` "cut from the cached whole-song render", the file starts at that marker (listen: within ~0.1 s,
   at most a click at the cut; it runs ~1 s past the next marker) and `share_cut.how` "whole MP3 frames, no
   re-encode". Change one note in that section, preview the section again -> `renders: 1`, "cut from one whole-song
   export", the change is audible. `fl_preview(start_bar=9, end_bar=17, keep=true)` -> '<song> preview.wav' of
   8 bars + 1 s, the MP3 preview gone from `_preview` (one preview at a time).
5. Bars line up with FL: on a song with a tempo change and a 3/4 marker, `fl_preview(format='wav', section=X)`
   starts exactly where FL's playlist ruler shows marker X (compare with `fl_listen(start_bar=..)`).
6. A playlist time selection left in FL (FL's export then renders only the selection): a range preview refuses with
   "a playlist time selection or a loop / stop marker ... clear it in FL" and caches nothing; whole-song previews are
   unchanged (no check there yet). Tell us whether FL's export renders only the selection.
7. Research (decides whether a faster 'selection' route is worth building): FL's export can't be given a bar range
   from fl-headless today (the export window's settings are FL's last ones; no range field is driven). If FL
   renders ONLY a playlist time selection AND the bridge can both set one (`rec_setup` punch in/out, or
   `arrangement.jumpToMarker(delta, select=True)`) and CLEAR it again afterwards, an export of just the section is
   possible: report (a) whether `rec_setup(punch_in_bar=9, punch_out_bar=17)` shows as a time selection in the
   playlist, (b) whether FL's export then renders only bars 9-17, (c) any way to remove the selection from a script.
   Until (c) is known the cut route stays: a selection left behind would silently shorten later full renders/finals.

## flp_arrange across tempo rows, automation and audio clips (2026-10-24, PC-VERIFY)
1. A MIDI-imported song (its pattern has a tempo row at tick 0): `flp_arrange` insert_bars at 9 (+4), then
   delete_bars 9-13, then duplicate 9-13. FL shows the notes after bar 9 moved / cut / repeated, the song tempo
   unchanged, and the clip after a split starts at the tempo in force there (tempo display while playing from it).
2. A whole-song volume automation clip: insert_bars at 3 (+2) -> FL shows a flat stretch of 2 bars at bar 3 and the
   rest of the curve 2 bars later; delete_bars 3-5 -> the curve jumps (1 tick) from its bar-3 value to its bar-5
   value and goes on from there. `ARR-AUTO`
3. An audio clip starting inside a deleted range: delete_bars -> the clip starts at the cut, shorter by the cut part,
   and the result's notes say it still plays the sample from its start (no sample offset is written). An audio clip
   crossing an insert/delete point is left as is and named in the notes. Report whether FL's playlist clip record has
   a start offset for audio clips that could be set safely (it would let the tool trim instead).

## Release bug hunt: live layer (2026-10-24, PC-VERIFY)
Tests: tests/test_bughunt_release_live.py. Use a small song with 4+ channels, one of them an automation clip (e.g. a
sidechain duck or a filter sweep), saved inside FLH_ROOTS.
1. **solo renders** (fl_preview channels / in-FL stems). Edit the song's file behind FL so FL holds an older copy
   (e.g. `flp_set_tempo` while a reload is refused, or copy an older version over it), then
   `fl_preview(channels=[<one>])`. Expect `steps_s.load_first` (FL reloaded BEFORE muting), the file holds only that
   channel (listen), and afterwards no channel is left muted in FL. While FL's export runs, unmute one of the muted
   channels by hand in FL right before Ctrl+R if you can: the preview must refuse ("no longer muted in FL ... nothing
   was rendered") rather than hand back the whole mix. Report whether fl_state reports the live mutes right before
   the export (`_live_mutes_intact`).
2. **automation kept**: in (1) and in `fl_render_stems(method='in_fl')` the automation clip channel is never muted -
   the duck / sweep is audible in the solo and in each stem it moves.
3. **stems never leave mutes**: `fl_render_stems(method='in_fl')`, and while it runs click another FL window / open a
   popup in FL so one stem's export fails. Expect the error, and every channel unmuted in FL afterwards (or the error
   saying which channels couldn't be confirmed unmuted). It must not say "nothing was opened: ... muted live".
4. **saves only into the project FL shows**: FL shows song A with an unsaved knob change; `fl_render(<song B>,
   method='in_fl')`. A's file must not change before FL switches (its mtime), and B's render is B. `fl_save` while a
   reload of the open song is unproven still refuses; `fl_ab(<song>, label='x')` in that state says "not saved in FL
   first (... older copy ...)" and stores the file on disk.
5. **locks**: put the song in a OneDrive folder (or keep it open in another program that locks it briefly) and run
   an edit right after `fl_open` / `fl_save`. Expect no "PermissionError [WinError 5]" for '<song>.flp.flh-tmp' ->
   '<song>.flp' (the replace is retried up to OS_RETRY_TOTAL_S = 8 s), the reload marker written, and no
   '*.flh-tmp' / '*.tmp' left next to the song.
6. **reload tag**: after an edit's reload, mixer track names carry no ' ~flh....' in FL or in the file. Rename a mixer
   track by hand to '<name> ~flh' and save in FL, then edit the song: the next reload is fast (no 'no sign of
   re-reading' escalation) and the name comes back without the tag (`steps_s.tag_cleaned`).
7. **same-title twins**: copy 'Song.flp' to 'Song (v2).flp' in Explorer (both titled 'Song'), `fl_open('Song (v2)')`,
   then `fl_status` / `fl_save` / an edit of 'Song (v2)': each names 'Song (v2).flp' (no "can't tell which project is
   open"). With nothing proven (restart FL, open the twin by hand): `fl_save` saves and reports where it landed
   (`identified_by`), or refuses with the fix (fl_open / flp_project_info title) when either twin's file is newer than
   FL's copy. `fl_ab(<song>, version=...)`: the '[B]' copy shows its own title in FL's window.
8. **silent sounds**: give a channel a LABS pack the installed INSTRUMENT doesn't support (or a BBC patch with no
   keys in the notes' range) and `fl_preview(channels=[<it>])` -> `silent_hint` "channel N '<name>' renders silent
   ... pick another sound", and `fl_sound_catalog` ranks that pack down afterwards. A whole song that renders silent
   says "the whole song renders silent".

## Note / playlist writers 2.2: meter, new song from a project, pattern names, replace_part (PC-VERIFY)
1. **flp_meter**: on a copy of a 4/4 song (FL closed or the song open), `flp_meter(song, num='3/4')` then
   `flp_meter(song, num=6, den=8, at_bar=5)`. FL (after the reload) shows 3/4 in Song settings and a 6/8 marker on the
   playlist ruler at bar 5; bars after it follow 6/8; FL's own save keeps both (`flp_info` after an fl_save:
   `time_signature` 3/4, `time_signature_changes` [{bar 5, 6/8}]). `remove=true at_bar=5` takes the marker out in FL.
   On a project saved without a time signature (events 17/18 missing): `flp_meter` adds them and FL shows it.
2. **flp_new_from**: `flp_new_from(<arranged song>, 'Second Song')`, then `fl_open('Second Song')`: same channels,
   plugins (sound the same on a test note), mixer inserts and FX; the playlist is empty (no clips, no markers, no
   Tempo lane), FL's window shows the title 'Second Song', the pattern picker has only 'Pattern 1'; no FL error popup.
   The source song is unchanged (open it: same clips). `flp_meter(<new>, num=3, den=4, at_bar=3)` on it (its
   arrangement had no playlist event: one is added) shows the marker in FL.
3. **flp_patterns_set**: rename + recolor a pattern of the open song; after the reload FL's pattern picker and its
   playlist clips show the new name and color.
4. **flp_write_notes on an arranged song**: refused without clear_song; with `clear_song=true` the output has the
   template's channels/mixer and only the new notes (one clip at bar 1). A MIDI file with 3/4 + a 6/8 change and a
   tempo map: FL shows 3/4, the 6/8 marker where the MIDI changes, and plays the tempo changes (fresh section songs:
   a Tempo lane on its own track with every point).
5. **sections on one track**: `flp_write_arrangement` / `flp_write_notes` into a fresh song: every section clip on
   playlist track 1; a clip stretched over a ringing last note overlaps the next section's clip on that track and
   FL plays BOTH (the tail is heard, the next section starts on time).
6. **flp_replace_part into='covering_clip'**: on a song with one pattern per section, write a part for a channel no
   section holds yet (no placeholder notes): the notes land in each section's pattern and play; a tempo lane clip
   over the song never gets notes. Humanized notes a few ticks before a section start play with that section (no
   'moved_to_clip_start' in the result); a short grace note before a downbeat stays audible before it.

## Sounds, plugin knobs and argument names (2.2.0, PC-VERIFY)
1. **instrument sound refusals**: `flp_instrument_sound(song, channel=<INSTRUMENT ch>, pack='<a LABS pack>',
   preset='<its display name with spaces>')` loads that preset (`preset_matched` says how), not the pack's first.
   A preset the pack doesn't have (or one without a matching patch) is refused with the pack's preset names, and
   after `flh_sync` FL's channel still plays the sound it had (no 'Content Not Found'). `force=true` writes it.
2. **plugin set_value units**: Fruity Reeverb 2 (or any reverb with a low cut that shows 'Off' at 0):
   `fl_plugin_set_value(track=<t>, slot=<s>, param=<low cut>, target='Off')` and `target=0` land on Off; from Off
   `target=120` reaches ~120 Hz. A stepped pre-delay asked `30` reports `nearest_step` with what it shows (e.g.
   '31ms'), held=true. A decay / release knob that shows '194ms' ... '2.00s': `target='1.2 s'` and `target=1200`
   both end at '1.20s'. Plugins that update their display later (Splice INSTRUMENT): the result's text matches
   the knob in the plugin window.
3. **plugin not ready**: right after `fl_open` / a reload, `fl_plugin_params(index=<ch>)` waits (`ready_after_s`)
   instead of "TypeError: 'NoneType' object cannot be interpreted as an integer"; a slot with no plugin fails at once.
4. **track / index**: `fl_plugin_params(track=<t>, slot=0)` = `fl_plugin_params(index=<t>, slot=0)`;
   `fl_plugin_params(track=<t>)` without slot is refused (never channel t's instrument); `fl_mixer_set(index=<t>,
   volume_db=-3)` sets mixer track t. `fl_plugin_params(index=<t>, slot=0, filter='attack|release')` lists both.
5. **new channel insert**: `flp_add_channel(song, clone_from=<ch>, insert='new', name='Celeste')` -> in FL the new
   channel plays through a free insert named 'Celeste' (not the cloned channel's insert or its effects).
