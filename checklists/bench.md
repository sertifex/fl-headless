# flh_bench (fl_bench) - PC checklist

Built and tested on Linux against a simulated FL (tests/test_bench.py). These need the real Windows PC with FL open on
a saved song (or its empty new project), no popup, and nobody using FL during the run.

1. Plan: `flh_call("fl_bench", {"dry_run": true})` -> 5 jobs in order beat, rework_34, ending, mix_tweak, knob, about
   9 estimated minutes. Write down the estimate so it can be compared with the real minutes (step 2).
2. Run: `flh_call("fl_bench", {})` -> a job; `fl_job(<job>)` shows `job i/n: <title>` while it runs. Expect about
   9-12 minutes. The result: summary, per job status / seconds / renders / detail, and `file`
   (`%LOCALAPPDATA%\fl-headless\bench\<time>.json`). Afterwards FL shows your song again, the folder
   `<first FLH_ROOTS>\_fl-headless-check\bench-<time>` is in the Recycle Bin, `flh_profile()` shows your own active
   collection (not `_fl-headless bench`) and its file is unchanged; nothing new in your collection's final_root.
3. beat: a new trap song opens in FL and its level balance runs while the bench waits (flh-balance thread, the bench
   lets go of FL's lock). `renders` 1-2 (the balance export), `balance_news` says "levels balanced". If it reports
   "the background level balance didn't start" or "still running after 300 s": send the JSON.
4. rework_34 fixture: in the kept sandbox (`fl_bench(jobs=['rework_34'], keep=true)`) open
   `songs\Bench Waltz\Bench Waltz.flp`: 3/4, tempo 60 -> 66 at bar 9 -> 58 at bar 13, one 16-bar piano pattern on the
   cinematic template's keys channel, audible (a piano-ish sound, not silence), no leftover section clips.
5. rework_34 chain: `Bench Waltz rework` gets low strings / strings / choir layers (each on its own channel),
   flp_perform runs (expressive), the ending is extended, fl_finalize renders once; `rules_first_time: true` with
   rules min_ending_s 20 / min_tail_s 5. The final is inside the sandbox song folder (not your final_root).
   If the render path closed FL (command-line route), FL reopens and the run continues (not 'FL wasn't running').
6. ending: `predicted` pass/fail with `predict_renders: 0` (from the notes only) and `agree: true` against the final's
   ending_length / tail_ring. A disagreement is a real finding for fl_ending_check / ending_hold_for_rules.
7. mix_tweak: the fader of the first channel's mixer track moves 2 dB down in FL; preview 1 renders (or mixes offline
   from the balance's cached stems: then `renders` 0 and `previews_offline` 1), preview 2 `renders: 0`.
8. knob: a trap song without a compressor gets Fruity Compressor on that track (setup_s), its Threshold shows
   -36.0 dB in FL's plugin window, `landed: true`, `readback` is FL's own text. If the readback text has no number,
   note it (the job then falls back to fl_sound_shape's own report: `landed_from`).
9. Compare: after an update, `fl_bench(compare='last')` -> `compare.rows` per job with before/after seconds, renders,
   deltas; `fl_bench(compare=['<a>.json', '<b>.json'])` works without FL. Rows of jobs that didn't pass in both runs
   are marked and left out of the totals.
10. Interrupt: restart the server mid-run, then `fl_bench(dry_run=true)` is fine and the next real `fl_bench()`
    reports `recovered_previous_run` (your song reopened if FL showed a bench song, the old sandbox recycled, your
    collection active again).
11. Slow mode (PC-VERIFY in `flh_bench._proc_mask` / `_pick_mask`): `fl_bench(jobs=['beat', 'mix_tweak'],
    slow=true)`. During the run, Task Manager > Details > FL64.exe > Set affinity shows exactly 2 CPUs ticked (on a
    hyperthreaded CPU: CPU 0 and CPU 2, i.e. two real cores). No admin prompt; no error in `slow.couldnt_limit`. If
    the render path restarts FL (command-line render), the new FL64.exe is limited too within ~1 s (`slow.limited`
    lists both pids). After the run (also after `fl_pc_check(stop=true)` or a failing job): all CPUs ticked again,
    `slow.restored.restored` lists the pids. The result says `speed: 'slow (2 cores)'`.
12. Slow vs normal: `fl_bench(jobs=['beat', 'mix_tweak'])` then `fl_bench(jobs=['beat', 'mix_tweak'], slow=true,
    compare='last_normal')` -> `compare.speed` ['normal', 'slow (2 cores)'], per row `x` (times slower) and
    `total.x`. Expect the slow run to be clearly slower (renders, balance); note the numbers.
13. Crash during a slow run: kill the server process (Task Manager) mid-run; FL stays on 2 cores. Start Claude again
    (the server restarts): within a few seconds FL has all its CPUs again (state `bench_affinity` cleared). A mask you
    changed yourself in Task Manager meanwhile is left alone.
