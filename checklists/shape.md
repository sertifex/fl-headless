# flh_shape - PC checklist

Built and tested on Linux on project files (no FL). These need a real Windows PC with FL.

1. Collection profile with `rules: {min_ending_s: 30, min_tail_s: 6}`; a slow 3/4 piece with an expressive tempo map
   (perform tempo / tempo automation ~20-35 BPM) and string/choir layers under the piano. `flp_extend_ending(project)`
   -> `mode: rules`, `hold.main_s` about 42 s, `hold.layers_s` a few seconds less, beats reported (about 19-20 piano
   beats at ~27 BPM). In FL: every note of the final chord on every part is held that long, the layers end first,
   the playlist clip reaches past the last note-off by ~3 s.
2. Render (fl_finalize with the profile's default fade): the whole hold and its release are in the render (nothing
   cut at the song end; FL's tempo after the last tempo point stays at that tempo), and the rules pass on the first
   render (see checklists/finalize.md 4).
3. A second `flp_extend_ending(project)` changes nothing (`held_notes: 0`, no reload in FL).
4. `flp_extend_ending(project, seconds=10)` still adds 10 s (old behaviour), `hold_s=50` holds 50 s from the attack.

## flp_extend_ending (rules mode, simulated)
- After flp_perform + flp_extend_ending: result `simulated` (ending_s >= min_ending_s + ~2 s) and maybe `hold_grown_s`.
- The journal gets `ending_hold`; a later flp_perform that shortens the hold makes fl_ending_check report
  `ending_undone` - run flp_extend_ending again after the last perform pass.
