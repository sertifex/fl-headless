# flh_score (flp_score, flp_clashes) + the notes checks in flh_review - PC checklist

Everything here runs offline on the .flp, so the logic is tested on Linux. What needs a real PC with FL is
whether the facts match the real projects and libraries the users have.

1. **Score of a real song**: `flp_score(project)` on a finished multi-pattern song (sections as named pattern
   clips). Expect: sections named after the clips (or the journal), the right key (compare with what you know),
   chords that make sense per bar, under ~1500 tokens of output, `steps_s` total < 0.5 s.
2. **Single-pattern project** (one long pattern): sections fall back to 8-bar blocks named `1-8`, `9-16`...
3. **Plugin preset names** (JUCE plugins: Spitfire LABS / BBC SO Discover / Splice INSTRUMENT): load e.g. BBC SO
   Discover "Horns a4" and "Timpani" on two channels with default channel names. `flp_score` must show
   `[french horn]` / `[timpani]` (taken from the plugin state's META name). If it shows nothing, check what
   `<META name=... family=... tags=...>` holds in that library's state and extend the `match` lists in
   `data/instrument_ranges.json`.
4. **Library ranges** (# entries marked `uncertain`): in BBC SO Discover open each instrument and read its key
   range (red / blue keys). Compare with `data/instrument_ranges.json` (`range` = playable keys, sounding pitch,
   C4 = middle C = FL's C5). Correct the shipped data where it differs, especially timpani (expected ~D2-C4),
   harp, celeste, glockenspiel, piccolo, tuba, contrabass.
5. **Clashes on a song you know is clean**: `flp_clashes(project)` should report nothing high. Then move one horn /
   string note a semitone off in FL (piano roll), save, rerun: it must be the top issue, with the bar, the parts
   and `flp_edit_notes(...)` that moves it back; run that call and check the note in FL.
6. **Genre tolerance**: set the journal `genre: "lo-fi"` (or jazz) on a song with 7th/9th chords: `flp_clashes`
   says `tolerance: jazz` and doesn't flag the extensions; `tolerance=strict` does.
7. **fl_doctor**: on a project with a timpani note below C2 the doctor lists `out_of_range` (med); the clash
   summary items (`clash`, low unless a high clash) say `flp_clashes lists them all`. Doctor still < 1 s.
8. **fl_review abrupt entrances** (audio): render a song where strings come in mid-section at full velocity with no
   fade. `fl_review(project, audio=<render>)` lists `abrupt_entrance` at that time, naming the strings and giving
   the velocity-ramp / flp_add_automation fix. A section start with a big entrance is NOT listed. Check that kick /
   snare-heavy music (EDM, trap) does not produce abrupt_entrance noise on every bar; if it does, raise
   `abrupt_audio.hold_lu` / `band_hold_db` in `data/score/rules.json` and report the values.
9. **Mixer insert in the automation fix**: the fade-in fix names `track=<insert>` read from channel event 104;
   confirm that is the mixer track the channel really plays into on FL 2024 / 2025 / 2026.
10. **MIDI input**: `flp_score("<file>.mid")` on a multi-track MIDI export (FL's File > Export > MIDI): one part per
    track, named after the track.
11. **808 slide notes**: a trap beat from `flp_compose(what="beat", genre="trap")` (it writes FL slide notes):
    `flp_clashes` must not report mud/rubs between the held 808 and its slide notes (they are read as one bending
    note). In FL, confirm the slide notes really bend the held note (flag 0x08, same check as compose #2).
12. **Groove tolerance**: journal `genre: "house"` (or afrobeats / funk / rock) on a song whose bass plays root-fifth
    with chromatic approach notes into the next root: `flp_clashes` says `tolerance: groove` and lists no bass_root
    issues for them; `tolerance=strict` lists them.
13. **Sounding pitch**: a song from `flh_new_song("drill-uk")` (808 sample on a transposed channel): `flp_score`
    says the key and chords the song really plays (e.g. F minor, i - VI - V), and `flp_clashes` doesn't ask to
    move the 808 an octave. Each `fix` selects the notes FL shows (low=/high= are the written pitches).
14. **Big song speed**: `fl_doctor` on a 100+ channel project takes under 5 s; `flp_clashes` under 10 s.
15. **Time-signature marker mid-song**: a song in 4/4 with a 3/4 marker at bar 9 (FL: right-click the playlist ruler >
    Add time signature): `flp_score(song, format="json")` says `meter: "4/4 > 3/4 (bar 9)"`, one chord per 3-beat
    bar after the marker, bar numbers matching FL's ruler; a clash after the marker is reported at the bar:beat FL
    shows (`# PC-VERIFY` in `_meter_setup`).
16. **Compare to the source MIDI**: a piano rework and the .mid it came from: `flp_compare_midi(song, midi)` gives a
    `bar_map` matching where the song really plays each MIDI bar (a new intro shows as `song_bars_without_source`),
    `missing` notes FL's piano roll confirms are absent at those bars/beats, and `tempo.changes` matching FL's tempo
    lane / automation. Then `restore=true, remove=['stand_ins']` with the song open in FL: FL reloads it once, the
    restored notes sit in the right pattern on the right channel with velocities like their neighbours, and
    `write.after.missing` is 0. `flp_restore(song, write.backup)` puts the song back.
17. **Split shared patterns**: a song whose chorus pattern is placed twice and needs a different note in each
    placement: `flp_compare_midi(..., restore=true, shared='split')` makes the second placement its own pattern
    named `<name> (bar N)`; in FL both placements still play as before plus their own restored note; FL shows no
    damaged-playlist warning (`# PC-VERIFY` in `_cmp_write`). The new pattern's colour is FL's default (not copied).
