# DEVMAP - where things are in fl-headless (read this before searching)

Anchors are function / constant names, not line numbers: `grep -n "def name" file.py` finds them.
A dev-only check (not in the public repo) fails when a function cited here as `fn()` no longer exists - update this
file then.

## Working loop
- While working: `python3 tests/run_all.py --changed` (only the tests your change reaches, 2 at a time) or
  `--files flh_x.py data/genres/trap.json`; `--list` shows the selection and why without running; `--slowest`.
- Once before a deploy / hand-off: `python3 tests/run_all.py` (the whole suite, `--jobs 2`).
- Deploy to a test PC: maintainers use a dev-only stager (`deploy_stage.py`, not in the public repo; see the end).
  Without it: copy the changed files over your fl-headless folder and run `flh_reload`.
- Dev cache (selection map, test times, deploy marker): a local, gitignored folder (`FLH_DEV_CACHE` moves it).

## Rules (AGENTS.md in brief)
- stdlib + numpy only (scipy etc. only as optional speed-ups with identical results). Python 3.10+, must import on
  Windows / macOS / Linux. Never call `mcp__remote-devices*` tools; build and test offline here.
- Writes only inside FLH_ROOTS (`writable()` before every write) + fl-headless's state folder + FL's settings
  folder; deletes only via `files_recycle()`; no eval/exec of data; never start a second FL; never answer
  "Save anyway"; network only in tools about fetching, only image-line.com.
- No author-specific names, paths, songs or taste anywhere; data over code (`data/<module>/`); honest results
  (say what was measured, what's a guess, when a human must listen); compact JSON; measure (`steps_s`).
- PC-only behaviour: `# PC-VERIFY: <what>` in code + `checklists/<module>.md`.
- Only edit the files your task owns; never git stash / checkout / reset / commit unless the owner asks.

## Layout (2.2.1+)
All code and shipped data live in `src/`: `src/fl_headless_mcp.py`, the `src/flh_*.py` extensions and helpers,
`src/data/` and `src/fl_script/`. Paths in this file are relative to `src/` unless they start with `tests/`,
`tools/`, `docs/`, `checklists/` or `mcpb/`. The repo root keeps the docs, `install.py` / `install.bat` and
`fl_headless_launch.py` (puts `src/` on the path and starts the server). Tests and tools put `src/` on `sys.path`;
`tests/run_all.py` maps `src/x` to its module the same as before.

## The server: fl_headless_mcp.py (~17k lines, sections marked `# =====`)
| section | what | entry points |
|---|---|---|
| paths and safety | allowed folders, write guard | `roots()`, `writable()` |
| MIDI reader / FLP low level | .flp event stream parse/write, load cache; playlist markers / track groups in FL 2026's layout | `_load()`, `_events()`, `_save()`, `_marker_slot()`, `_track_groups()` |
| FLP: tools | read/write tools on files (notes, patterns, playlist, automation, mixing, arranging) | `flp_edit_notes()` ... |
| FL Studio process | find/open/close FL, popups, save-and-reload of the open project; a reopen of a file changed behind FL (fl_open same project / fl_reload_from_disk) = the empty template LOADED first, then the file, proven by a marker stamped into the file and read back through the bridge, else it fails / says unproven and FL is marked stale (what FL holds is recorded only on proof); an open's leftover popups are closed | `fl_open()`, `fl_reload_from_disk()`, `_fl_open_raw()`, `_await_switch()`, `_reload_in_fl()`, `_reload_marked()`, `_template_loaded()`, `_await_marker()`, `_reload_content_check()`, `_reload_via_blank()`, `_sweep_leftover_popups()`, `_hot_reload()`, `_wait_saved_any()` |
| background jobs | long work off the call | `_start_job()`, `_job_progress()`, `fl_job()` |
| FL settings / project info, self-test | registry reads, `fl_selftest` | `_state()` (state.json; damaged -> `_state_recover()` from state.json.bak), `STATE` |
| live bridge | MIDI/mailbox bridge to the FL script `fl_script/device_flheadless.py` | `BRIDGE.call(cmd, ...)`, `fl_listen()` |
| files | file tools | `files_recycle()`, `_write_bytes()` |
| MCP tool table | `TOOLS = {name: (fn, description, input_schema)}` + schema helpers | `_p()`, `_schema()`, `_STR/_INT/_NUM/_BOOL/_FLP` |
| live-first (big projects) | lazy edits queued while FL holds the project, live routes | `_lazy_tool()`, `LIVE_ROUTES`, `LAZY_TOOLS`, `flh_sync()` |
| extensions | `EXTENSIONS` tuple, loaded at import | `_load_extensions()` |
| tool surface | tiers + discovery | `ESSENTIAL`, `GROUPS`, `LONG_HELP`, `flh_tools()` |
| MCP stdio loop | dispatcher, arg aliases, code reload, soft deadline | `handle()`, `_check_args()`, `_live()`, `flh_reload()`, `_soft_deadline_call()`, `main()` |
Also: `flh_run()` (batched steps), `flp_edit()` (batched file edits), `fl_render()` / `fl_timings()`.

### How a tool gets registered and called
1. Core: `TOOLS["name"] = (fn, "description", _schema(arg=_STR("...", True), ...))` in the main file.
2. Extension `flh_x.py`: `register(ns)` - `ns` is the server's globals (dict). Add `ns["TOOLS"]["name"] = (...)`;
   look server functions up at call time (`ns["fl_render"]`, often kept as a module global `H = ns`); never
   `import fl_headless_mcp`. Export helpers for other modules with `ns["name"] = fn`. New module -> its name in
   `EXTENSIONS` (owner of the main file adds it). Helper (non-extension) modules: `HELPER_MODULES` (reloaded too).
3. Project writers: wrap with `ns["_guard_open_project"](fn, "project", "output")` and set
   `ns["_OPEN_PROJECT_GUARD"][name] = ("project", "output")` -> `flp_edit()` can batch it, the open FL project
   is saved/reloaded around it, and `_lazy_tool()` can queue it (lazy mode, `LAZY_TOOLS`) or route it live
   (`LIVE_ROUTES`).
4. After registration `_install_fl_lock()` wraps every FL-driving tool with `_fl_locked()` (one FL user at a time;
   `FL_LOCK_FREE` tools only look). Sets the dispatcher reads: `CROSS_PLATFORM`, `PARALLEL_SAFE`, `NOT_READERS`.
5. Calls: client -> `handle()` (tools/call) -> `_check_args()` (aliases, typos) -> `_soft_deadline_call()` runs it in a
   worker thread (long calls become jobs). Long work: `ns["_start_job"](kind, fn, *a)`; progress
   `ns["_job_progress"](step=..., pct=...)`; put `steps_s` on results.

## Extensions and helpers (one line each; tools are registered in each module's `register()`)
- fl_music (theory, note names) · fl_musicality (does a part "rhyme") · fl_blocks (FL-saved channel templates)
- fl_audio_edit (offline WAV edit) · fl_capture (WASAPI loopback) · fl_sound (describe a sound) · fl_mix (fix list)
- fl_render_cache (render cache, stems-once remix, ending prediction) · fl_help (online manual search)
- flac_lite (FLAC enc/dec) · ogg_vorbis_lite (Ogg decoder) · mf_audio (Media Foundation decode) · sheet_notation
- flh_common (shared helpers, no tools) · flh_store (collection profile + per-song journal: read it, record taste)
- flh_compose (genre grammars from `data/genres`: `genres()`, `genre_data()`, `_data_dirs()`, `flp_compose()`)
- flh_newsong (`flh_new_song()` from `data/templates`, `data/kits`) · flh_perform (performance) · flh_realism
- flh_score ("musical eyes", clashes) · flh_review (`fl_doctor`, `fl_review`) · flh_finalize (FINAL renders)
- flh_finish (finish a piece in one call) · flh_shape (endings/entrances) · flh_rise (orchestral rise) · flh_fx (FX)
- flh_mixeng (plugin maps `data/plugin_maps`, masking, references) · flh_sounddesign · flh_sampler · flh_flex (FLEX)
- flh_vocal (record/comp/clean vocals) · flh_transcribe (audio->MIDI, capture) · flh_sheet (sheet music in)
- flh_flip (sample flips) · flh_refbeat (beat from a reference) · flh_catalog (catalog, A/B, watchdog)
- flh_setup (first run, theme) · flh_handbook (local manual index + cards) · flh_access (accessibility)
- flh_extras (collab packs, recipes, teaching) · flh_cleanup (remove channels) · flh_tidy (state folder size)
- flh_pccheck (`fl_pc_check()` runner, `Ctx`, `core_checks()`, `module_checks()`, `plan()`) + flh_pccheck_mod
  (the music/audio checks: `checks()`, `_check()`) · flh_bench (`fl_bench()`, `JOBS`) · flh_sweep (`fl_sweep()`)
- install.py (installer; writes fl_headless_launch.py) · fl_script/device_flheadless.py (runs inside FL)
- mcpb/ - the Claude Desktop extension (MCPB v0.4, server type uv): `manifest.json` (Song folders picker -> args),
  `pyproject.toml` (numpy for uv), `launch.py` (prepare_env: folder arguments -> FLH_ROOTS, FLH_INSTALLED_BY=mcpb).
  Built by tools/make_mcpb.py (`build()`, `check_manifest()`: release files minus tests/tools/installer). In
  the extension 'set me up' = flh_setup apply (`_connect()`: script + MIDI over Windows' loopback, `_ext()` wording)

## Where to hook common changes
- New tool: core -> `TOOLS[...]` in the right section; else `register()` of the owning extension. Long help ->
  `LONG_HELP[name]`; tier/group -> `ESSENTIAL` / `GROUPS` (else module/prefix rules). Writer -> step 3 above.
- New genre / style data: `data/genres/<name>.json` (`flh-genre/1`, read by `genres()` / `genre_data()`); new
  song recipes `data/templates/<genre>.json`, kits `data/kits/`; users override in the state folder. Tests:
  test_compose*, test_newsong, test_e2e (j1/j5 new songs), test_pccheck_mod (newsong.*).
- New PC check: a `c_<area>_<what>(ctx)` function + one `_check(...)` line in `checks()` (flh_pccheck_mod) or
  `core_checks()` (flh_pccheck); the id is `<area>.<name>`, the result `{status, detail, evidence, fix_hint,
  checklist}`; the checklist anchor must exist in `checklists/`. Test: tests/test_pccheck_mod.py (schema + fake FL).
- New bench job: a `job_<name>(ctx)` + a dict in `JOBS` (flh_bench): id, title, minutes, deadline_s, run, renders.
- Arrangement ear candy (modes organic / synthetic, amounts subtle/normal/lots, purposes, one riser / one big hit
  per change, beds, snare build, delay throws, the old synthetic cinematic profile only on ear_candy='synthetic'):
  `data/ear_candy.json` (+ its `organic` block: real-instrument moves as notes) + each genre's `ear_candy` (`mode`,
  `organic_moves`) / `transitions`, read by flh_compose `_ec_settings()` / `_ec_pick()` / `_organic_plan()`; the
  collection profile's `ear_candy` / style words; sounds in flh_fx `FX_KINDS`; taking it out: `flp_remove_fx()`.
  Test: tests/test_compose_fx.py (+ test_fx for the sounds); PC checks checklists/fx.md EC-A*, EC-O*, EC-R*.
- New data folder: `data/<module>/` read with a user override from the state folder; ship only our own data.
- Master tone (a song's tone curve as a Parametric EQ 2 on the master, in the file): flh_mixeng `flp_master_tone()`
  (writer, guarded) / `master_tone()` (the tool: read, file, or live fallback), PEQ2 state via the map's `file_state`
  (`peq_decode()`, `_tone_default()` proves the layout on FL's default preset), presets `data/mixeng/master_tone.json`,
  journal `master_tone`, checked by fl_finalize (`master_tone_check()`). Test: tests/test_master_tone.py.

## Tests
- `tests/test_<module>.py`, runnable alone, last stdout line `ok`. Before `import fl_headless_mcp as m`: set
  `os.environ["FLH_ROOTS"]` and `LOCALAPPDATA` to temp dirs (the runner also gives every test its own TMPDIR,
  HOME, LOCALAPPDATA and sets `FLH_NO_WARM=1`); `ns = vars(m)`; mock FL-facing entries in `ns` (e.g.
  `m.fl_running = lambda: False`). Fixtures: `tests/corpus/*.flp` (built by tools/make_corpus.py), `tests/fixtures/`.
- Simulated FL: tests/test_e2e.py `FakeFL` (bridge, open/save/reload, renders, speakers recorder) +
  `build_fl_install()` (fake FL install, plugin database, packs). tests/test_pccheck_mod.py `SynthFL` extends it
  (plays the notes as sines, plugin knobs from the maps). `VClock` (test_e2e) is the virtual clock: sleeps cost no
  wall time while no other thread works; `FLH_REAL_WAITS=1` waits for real. Fake-FL files get `vstamp()`ed mtimes.
- Parallel inside tests: test_e2e (`FLH_E2E_JOBS`) splits journeys, test_fuzz (`FLH_FUZZ_JOBS`) splits mutants
  over processes (default 2 each). `python3 tests/test_e2e.py j2 j6` runs single journeys.
- Runner: tests/run_all.py (`select()`, `build_map()`, `registry_check()`); `SERIAL` = tests that must run alone
  (test_install writes into the repo); `SMOKE` always runs with --changed.

| area | tests |
|---|---|
| .flp format, parse, robustness | test_flp_format, test_corpus, test_fuzz, test_bughunt, test_core_*, test_release_fixes, test_markers_fl26 (markers / track names in FL 2026's layout) |
| editing, playlist, batches | test_edit_batch, test_playlist*, test_aliases, test_audio_clip, test_sample_vars, test_note_writers (replace_part into / hidden, compose_write place_mode, write_notes clear_song + MIDI meter, `flp_meter()`, `flp_new_from()`, `flp_patterns_set()`) |
| open / save / reload / lazy | test_bughunt_release_live (guarded saves `_save_guard_why()`, same-title twins `_open_project_identify()`, solo renders load first), test_open_*, test_reload* (test_reload_settle / test_open_safety: reload proven by content or the marker; FL keeping its old copy blocks saves), test_focus (FL to the front), test_fresh_read (composer reads after FL saves), test_save_prompt, test_verify_snapshot, test_job_autosync, test_core_lazy, test_core_live_notes, test_live_slide |
| render / listen / preview | test_core_render_in_fl, test_render_cache, test_listen, test_preview*, test_stem_groups, test_slow_pc, test_soft_deadline, test_preview_range (format='mp3' / flac, section / bar ranges cut from the whole-song render, MP3 frame cuts `_mp3_cut()`) |
| compose / new song / perform | test_compose*, test_newsong, test_perform*, test_realism, test_refbeat, test_flip, test_rise, test_fx, test_shape, test_ending_sim |
| finals / review / mix | test_finalize*, test_finish*, test_review, test_balance*, test_mix, test_mixeng*, test_master_tone, test_score, test_map_names |
| sound / audio io | test_sound*, test_sampler, test_flex, test_instrument_sound, test_audio_edit, test_flac_read, test_ogg_vorbis, test_media_formats, test_capture, test_transcribe, test_vocal |
| setup / help / access / misc | test_setup*, test_install, test_mcpb (extension bundle + first run in Claude), test_help, test_handbook, test_access, test_theme, test_extras, test_catalog, test_cleanup, test_tidy, test_usage, test_mailbox, test_sheet*, test_collection_report, test_collection_scope (a collection's taste only on its own songs), test_editions, test_controller, test_bridge* |
| PC checks / bench / sweep | test_pccheck, test_pccheck_mod, test_pccheck_sound_fixes, test_bench, test_sweep, test_speed, test_build_speed, test_index_progress |
| journeys (everything through `handle()`) | test_e2e |
| dev tools (dev-only, not in the public repo) | test_dev_tools (deploy stager, run_all selection) |

## Deploy (dev-only, not in the public repo)
- Maintainers stage changed files for a test PC with a dev-only `deploy_stage.py` (it ships what the release builder
  ships, minus tests and tools, and says whether the server must reload; `--help` lists its options). It only runs when a task allows touching
  that PC; offline development never does.
- Anyone else: copy the changed files over the fl-headless folder on the PC, then `flh_reload` (or restart Claude
  when the changelog says so) and `fl_bridge_setup(action="update_script")` if `fl_script/` changed.
