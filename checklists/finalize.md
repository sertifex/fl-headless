# flh_finalize - PC checklist

Built and tested on Linux with a fake render. These need a real Windows PC with FL.

1. `fl_finalize(projects=[a, b])` (background). Watch `fl_job`: the steps go close_fl -> render (each song) ->
   reopen -> finish/file (each song). FL must reopen right after the last render, before the finishing starts.
2. While the job shows `step: finish`, call `fl_status` / `fl_snapshot` / `fl_play`: they work (FL isn't locked
   during the file-only finishing). During `step: render` they answer "FL is busy (job finalize-N ...)".
3. Make one song fail (e.g. a missing project file after planning, or kill the render): the result has top-level
   `ok: false`, `failed: 1`, an `error` naming the song, a `next` step, and the other song's final is filed.

## Endings that pass the collection rules on the first render (2026-10-22, PC-VERIFY)
4. A slow piece whose last chord holds flat under sustained layers (strings/choir): after `flp_extend_ending(project)`
   (rules from the profile), `fl_finalize(project)` with the profile's default fade -> `finals[0].fade.kind` is
   `hold_diminuendo`, `fade_s` spans most of the hold, `rules.pass: true` (ending_length >= min_ending_s, tail_ring,
   tail_not_cut, ends_in_silence, trail_silence). Listen: the held chord fades as a smooth decrescendo to silence,
   no audible stop, no long inaudible tail. `fade.predicted_ending_s` is within ~2 s of `rules` ending_length.
5. The same song with `fade_s=2.5` (explicit): the final's fade is exactly 2.5 s (linear), `rules.pass: false` with
   `rules.failed` and `rules.fixes` naming `flp_extend_ending(...)` / leaving the fade to the profile.
6. A song whose last chord is too short: `rules.fixes` says how many seconds longer and gives
   `flp_extend_ending(project=..., hold_s=N)`; doing exactly that and finalizing again passes.
7. A hard ending (last hit, no hold): fade stays the short decay-only fade (`fade.kind` auto_decay), last hit intact.

## Render cache in fl_finalize (2026-10-22, PC-VERIFY)
8. `fl_finalize(project)` twice without changing the song (e.g. a different target_lufs the 2nd time) -> the 2nd
   result has `renders: 0`, `steps_s.reused` names the song, notes say "unchanged since its last render: reused (FL
   wasn't closed)" - FL stays open even on the command-line route, the final is written from the kept render.
   `render_speed` doesn't change (nothing learned from a reused render).
9. Change one note and finalize again -> a real export (`renders: 1`).
10. `fl_ending_check(project)` before a finalize: `predicted: fail` + `fix` (flp_extend_ending hold_s) without any
    render when the last chord is clearly too short; doing the fix then finalizing passes the ending rules.

## Ending simulation, notes' attack, jump ride (fl_bench rework_34 / ending)
- Run fl_bench(jobs=["rework_34", "ending"]). Expect: rework_34 rules pass on the first render; ending agree=true.
- fl_finalize result `fade`: kind hold_diminuendo, `settle_db` present; if the final chord's attack was hidden by
  loud layers the notes' attack is used (PC-VERIFY: FL's render starts at song time 0, attack lines up +-50 ms).
- Listen to the final's ending: the held chord settles a few dB right after its attack, then dies away evenly.
- If `jumps_smoothed` is reported: listen at each at_s - the entrance swells in over ~1-2 s, no audible duck/pump.
- fl_ending_check(render=False) `simulated.ending_s` vs the final's ending_length: within ~3 s (the instrument model
  SIM_VEL_DB / _decay_db_s is a guess; report the two numbers if they differ more).

## Render length gate (PC 2026-10-23: a version copy's FINAL as long as the original's)
1. Copy a song's .flp to another folder under a new name (Explorer copy: same Project-info title), extend its ending
   (`flp_extend_ending seconds=20`) with FL showing the ORIGINAL, then `fl_finalize(copy)`. Expect: FL opens the copy
   (waits through 'Opening: <plugin>'; a save proves it is the copy, not the original), the render's
   `length_check.render_s` >= `song_s` (the file's length through its tempo map) and the FINAL includes the ending.
2. If the render is shorter (> ~1.5 s): one re-render after FL loaded the file (`length_check.re_rendered`), else
   `RenderMismatch` with the reason and NO final written. Report the two numbers and what FL showed.
3. With a playlist time selection / loop marker before the song's end: expect RenderMismatch "rendered twice" (FL's
   export stops early) - tell us whether FL renders only the selection.
4. A piano piece whose held chord decays: `length_check.final_s` < `song_s` with an `ending_note` "fell silent"
   (the render covered the whole song). Listen: the ending isn't cut.

## FINAL never from another project file (PC 2026-10-23: a FINAL sample-identical to the previous day's)
1. `fl_finalize(song)` twice without touching anything: the second reuses ("made from this exact project file").
2. Change anything and save in FL (even a knob, or just Ctrl+S), then `fl_finalize(song)`: a new render (`notes`:
   "the project file changed since (its hash differs): not reused for a FINAL, rendered again"); compare the two
   FINALs - they differ where the song changed.
