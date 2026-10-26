# flh_finish - PC checklist

Built and tested on Linux with the sub-tools mocked (tests/test_finish.py) and once with the real composer / performer /
ending / prediction on tests/corpus/base.flp (FL closed). These need a real Windows PC with FL.

Setup: a collection profile with `rules: {min_ending_s: 30, min_tail_s: 6}` and optionally
`finish_piece: {layers: ["low strings", "strings", "choir"], perform_style: "expressive"}`; a classical 3/4 solo-piano
piece imported from sheet music (sheet_import), with channels for strings / choir / low strings already in the project
(journal roles) so no new channels are cloned. The song is open in FL.

1. `flh_call("flp_finish_piece", {"project": P, "dry_run": true})`: the plan lists layers, perform (setup + perform),
   ending, predict, final with estimates; `fl_reloads: 1`, `renders: 1`; the estimate is within ~30 % of the real run.
2. `flh_call("flp_finish_piece", {"project": P})` -> a job; `fl_job` shows the steps (layers, perform, ending,
   predict, final). Expect: FL saves ONCE and reloads ONCE for all the edits (watch FL's title / the reload), then
   ONE render. Result: `renders: 1`, `rules.pass: true`, `final` with path / LUFS / TP / length, `steps_s` with
   save_reload, `listen` lists what to hear.
3. In FL after the reload: the layer patterns are there (faint, under the piano), the piano has the expressive
   tempo map / dynamics, the layers did NOT get the piano's humanization (they keep their realism) but follow the
   tempo map, the final chord holds ~42 s with the layers ending a few seconds earlier.
4. While the job runs, FL popups can still be answered (`fl_dialog`), other FL tools say 'FL is busy (job finish-N)'.
5. Make the prediction fail on purpose: shorten the final chord, run with `layers: [], perform: false`. The result's
   predict step shows `predicted: fail` then `fixed_before_render` with `hold_s` and `predicted_after_fix: pass`;
   still one reload and one render.
6. Force a rule fail after the render (e.g. a profile `min_ending_s: 40` with `ending: 35`): the final fails
   ending_length, the fix (hold_s) is applied in one more reload and fl_finalize runs exactly once more
   (`retry.finalized_again`, `renders: 2`); never a third render.
7. A project with no channel for a layer role: the layers step clones channels (`new_channels`), the final is skipped
   with the reason and the next call; `final: "force"` renders anyway.
8. FL closed: the edits go into the file (no reload), the final renders by the command line; same result shape.
9. A failing step (e.g. a song with no notes for perform): the result has `ok: false`, the error, the steps that
   worked, and FL was still reloaded once (FL never holds an older copy than the file).
10. (PC 2026-10-23) FL showing another song (or a same-title copy) when flp_finish_piece(ending=true, final=true)
    runs: the result has a step `fl_load` (`how: opened` / `reloaded`) BEFORE `final`; the FINAL includes the new
    ending (`final.length` longer than before by about the added hold, unless the sound decays - then an
    `ending_note`). If FL can't load it: `fl_load` error, the final skipped (no FINAL of an older copy).
