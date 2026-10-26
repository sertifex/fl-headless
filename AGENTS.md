# Rules for anyone (human or agent) working on fl-headless

Read DEVMAP.md first (where everything is). Run tests with `python3 tests/run_all.py --changed` while working, the
full suite (`python3 tests/run_all.py`) once before deploy.

fl-headless is a free, public MCP server that lets Claude drive FL Studio on Windows. Expect thousands of users
with different PCs, FL versions/editions, languages, genres and skill levels. It must be correct, fast, safe and honest.

## Architecture
- `fl_headless_mcp.py` - the server: MCP loop, FL bridge (MIDI/loopMIDI), .flp parser/writers, FL window/popups,
  render, audio helpers. Tools live in `TOOLS` as name -> (fn, description, input_schema).
- Extensions `flh_*.py` - each has `register(ns)`; `ns` is the live server namespace (dict). Look server functions
  up through `ns` at call time (`ns["fl_render"]`), never import the main file. Add tools to `ns["TOOLS"]`.
  Project writers: wrap with `ns["_guard_open_project"](fn, "project", "output")` and set
  `ns["_OPEN_PROJECT_GUARD"][name] = ("project", "output")` so `flp_edit` can batch them and the open FL project is
  saved/reloaded around the write. Schemas: `ns["_schema"]`, `ns["_p"]`, `ns["_STR"]`, `_INT`, `_NUM`, `_BOOL`, `_FLP`.
- New extension modules must be added to `EXTENSIONS` in the main file (the owner of the main file does that; ask).
- Memory: `flh_store` - collection profile (house rules) + per-song journal next to the project. Read it; record
  user likes/dislikes; never hard-code one user's taste.
- Background jobs: `ns["_start_job"](kind, fn, *a)`; progress `ns["_job_progress"](step=..., pct=...)`; put a
  `steps_s` dict on results so `fl_timings` shows where time goes.
- Data, not code: genre grammars, styles, plugin maps, templates, recipes live in JSON/data tables so users and the
  community can extend them. Data folder for shipped data: `data/` next to the server (create subfolders per module).

## Hard rules
- Development is offline: an agent never drives a real PC or FL (no remote-device / computer-use tools); everything
  is built and tested on Linux without FL, and the code must still import and start on Windows/macOS/Linux. Checks
  that need a real PC are written down for a human (see PC-VERIFY below).
- Python 3.10+, stdlib + numpy only. scipy/others only as optional speed-ups with identical-result fallbacks.
- Security: writes only inside FLH_ROOTS (check with `ns["writable"](path)` before every write) plus fl-headless's
  own state folder (`os.path.dirname(ns["STATE"])`) and FL's settings folder; registry writes only under
  HKCU\Software\Image-Line; deletes only to the Recycle Bin (`ns["files_recycle"]`); no eval/exec of data; never
  start a second FL; never close FL except the render path; never answer "Save anyway" on a trial warning; no
  network calls except where a tool is explicitly about fetching (and then only to image-line.com), plus the
  weekly update look (`_release_update`: one GET to this project's GitHub releases API, nothing about the user,
  off with FLH_UPDATE_CHECK=0 / `flh_updates(enable=false)`; the test runner turns it off).
- Never redistribute copyrighted content (FL's manual text, FL's samples, third-party presets). Ship code and our
  own data; build indexes on the user's PC from what their install gives them.
- Universal: no author-specific names, paths, songs, taste or edition assumptions in code, defaults, descriptions or
  examples. Defaults are neutral; the collection profile / journal override them. Genre-aware where taste matters.
- Honest: tools report what they actually did and measured, say when something is a guess, and say when a human
  needs to listen. Never claim success that wasn't verified.
- Token-efficient results: compact JSON, ranked lists, no dumps; give `full=true` style options for detail.
- Fast: measure (steps_s), avoid repeated parsing, batch FL round trips, never sleep-poll when an event exists.
- Things that can only be verified on a real Windows PC with FL: mark the code `# PC-VERIFY: <what to check>` and
  list them in `checklists/<module>.md` (one file per module; what to run, what to expect).
- Tests: `tests/test_<module>.py`, runnable as `python3 tests/test_x.py` on Linux without FL, printing `ok` at the
  end. Set `os.environ["FLH_ROOTS"]` (and `LOCALAPPDATA`) to temp dirs before `import fl_headless_mcp as m`;
  `ns = vars(m)`; register the extensions you need; mock FL-facing functions by replacing entries in `ns`/`m`.
  Fixtures: `tests/corpus/*.flp` (public, built by `tools/make_corpus.py`); build more with the server's own
  writers. Optional private fixtures go in `tests/data/` or `FLH_TEST_DATA` (gitignored; tests skip what needs
  them). Keep the whole suite passing: `python3 tests/run_all.py` (parallel, each test in its own temp folder that is
  removed afterwards).
- Only edit the files you own in your task; read anything. Ask for changes elsewhere in your final report.

## Dev workflow
- `python3 tests/run_all.py --changed` runs only the tests a change reaches (vs git HEAD + untracked files; `--files
  a.py data/x.json` for named files; `--list` shows the selection and why; a smoke set and a registry check always
  run). `--jobs N` (default 2), `--slowest`, plain name filters (`run_all.py compose`). Every test gets its own
  TMPDIR / HOME / LOCALAPPDATA; tests in `SERIAL` run alone. The selection map and test times are cached in a local,
  gitignored dev cache (`FLH_DEV_CACHE` moves it; see `tests/run_all.py`).
- Tests that wait for a simulated FL use the virtual clock of tests/test_e2e.py (`VClock`): never add real sleeps to
  a fake; make waits injectable (module constants the tests can override) in new server code.
- Maintainers have a few dev-only helpers (`deploy_stage.py` for a test PC, its tests, the release builder; see
  DEVMAP.md) that are not in the public repo; deploying runs only when a task allows it. To try a change on your
  own Windows PC, copy the changed files over your fl-headless folder and ask Claude to run `flh_reload`.
