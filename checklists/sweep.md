# flh_sweep (fl_sweep) - PC checklist

Built and tested on Linux with fl_pc_check / fl_bench / the usage log simulated (tests/test_sweep.py). These need the
real Windows PC with FL open on a saved song (or its empty new project), no popup, nobody using FL during the run.

1. Plan: `flh_call("fl_sweep", {"dry_run": true})` -> steps pc_check (full, unattended), bench_normal, bench_slow,
   usage, report; `estimated_minutes` = pc_check's dry-run total + the bench estimate + the slow estimate (x1.6,
   rough). `you_must_do` is empty. Nothing is written (no new folder under `%LOCALAPPDATA%\fl-headless\sweeps`).
2. Run: `flh_call("fl_sweep", {})` -> a job `sweep-N`. `fl_job(<job>)` shows `sweep 1/5: pc_check` with the check's own
   `check i/n` step, then `sweep 2/5: bench (normal)` with `job i/n` (the `sweep` field stays while the bench writes its
   progress - PC-VERIFY), then the slow bench, usage, report. Real minutes vs the estimate: write both down.
3. While it runs: `fl_sweep(status=true)` answers at once (it doesn't wait for FL's lock); `fl_pc_check()` and
   `fl_bench()` refuse to start; `fl_sweep(stop=true)` stops at the current check's next FL step, FL gets your song
   back, the sandbox is recycled, the remaining steps are 'stopped before it ran' and the report is still written.
   Afterwards a plain `fl_pc_check(dry_run=true)` / `fl_bench()` works (their stop flag was cleared).
4. Afterwards: FL shows your song; FL has all its CPU cores again (Task Manager > Details > Set affinity); folder
   `sweeps\<time>\` holds sweep.json, sweep.md (short: counts, top 10, compare, next) and share.zip; the pc_check report
   and the bench results are in their usual folders (pc_check\<time>, bench\<time>.json).
5. share.zip: open it and read sweep.md / sweep.json / pc_check/report.md / bench/*.json / bug_report/*: no Windows
   user name, no song or collection name, no `C:\...` paths (only `<path>`, `<state>/pc_check/...`, code file names
   like `<path>/flh_render.py`), no pictures. `fl_sweep(share_pictures=true)` adds `pictures/` (FL window pictures:
   they can show file names). No extra `flh-bug-*.zip` is left in `bug_reports` by the sweep itself.
6. Second sweep: `fl_sweep()` again without changes -> compare against the first; bench rows within noise (15 % / 2 s)
   are summed up as "every job within noise"; fix items carry `first_seen` = the first sweep's time.
7. After 11 sweeps only the newest 10 `sweeps\<time>` folders are left; anything else you put in `sweeps\` stays.
8. 2026-10-23 fixes: during the bench steps `fl_pc_check`'s running flag is off (it also switches the render cache
   off): bench `mix_tweak` passes inside the sweep as it does alone (its second preview reused). `fl_pc_check()` /
   `fl_bench()` started by hand during the bench steps say "fl_sweep is running".
9. A bench step that stops before its first job (e.g. FL still loading the song the step before gave back) waits for
   FL to settle (`SETTLE_S`, scaled) and tries once more; sweep.json `bench.<speed>.retry` has the first reason. If it
   still runs no jobs: summary `bench.<speed>` = {jobs: 0, stopped}, a `bench:<speed>:not_run` fix item and a 'next'
   line say why (PC-VERIFY: the slow run after the normal one runs its 5 jobs).

## Your live notes / reload mode after a sweep (PC 2026-10-23, PC-VERIFY)
1. With live notes ON (`fl_status` live_notes: true) and reload mode set (`flh_sync(mode='immediate')`), run
   `fl_sweep()` (unattended). Afterwards `fl_status` still shows live_notes: true and the same reload mode, even though
   `live_notes.probe` was skipped ("needs the hand step"). sweep.json has `settings_restored` when anything was put back.
2. Same with `fl_pc_check(unattended=true)`: its report has `settings_restored`; a live-notes check that PASSED leaves
   live notes on.
3. Cut a sweep off (restart the server mid-run): on the next start live notes / reload mode are back as before.

## The user's song and the sandboxes after a sweep (PC 2026-10-23, PC-VERIFY)
1. Restart FL (an empty, unsaved project: title "FL Studio 2026"), run `fl_sweep()`. Afterwards FL is on an empty
   project (File > New's untitled project, or FL's empty template if File > New didn't take) - never a song from
   `<first FLH_ROOTS folder>\_fl-headless-check\...`. The bench results say `your_song: "a new empty project"`.
2. `<first FLH_ROOTS folder>\_fl-headless-check\` is gone (or empty) after the sweep: pc_check's and both benches'
   sandboxes are in the Recycle Bin. If one couldn't go, its report / bench result has `sandbox_not_recycled`
   {path, why}, sweep.json has a `cleanup:<step>` fix item, and the next fl_pc_check / fl_bench recycles it once FL has
   left it (`earlier_sandboxes.recycled`).
3. With a collection active whose profile has `final_root` (and e.g. `final_name: "{song} (Piano) - FINAL.flac"`):
   nothing new appears in that final folder after `fl_sweep()` / `fl_pc_check(full=true)`; pc_check's final is
   `<sandbox>\finals\check final\check final - FINAL.flac` while it runs; the collection is active again afterwards
   (`flh_profile()`), its profile file unchanged.
4. Start a sweep from one of your songs: every step's restore reopens THAT song (bench results `your_song` = its path),
   even when a step before left FL on another project.
