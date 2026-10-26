# flh_perform (flp_perform_setup, flp_perform, flp_perform_undo) - PC checklist

Logic is tested offline (`python3 tests/test_perform.py`); what needs FL:

1. **Slides survive.** A song with 808 slide notes (FL piano roll: slide notes) and other note properties set (pan,
   fine pitch, mod X/Y on a few notes): `flp_perform_setup(song)` + `flp_perform(song)`. In FL's piano roll the
   slide notes are still slide notes and keep their pan / fine pitch / mod values; only timing and velocity moved.
2. **Layered song.** A song built as stacked layer patterns (one pattern per instrument per section, sections
   repeated): `flp_perform(song, parts="all")` with style "none" changes nothing audible (A/B in FL); with
   "natural" each layer's pattern changes, no notes jump into another layer's pattern, and the result's
   `verify.ok` is true (warnings name the repeated patterns).
3. **Queued edits.** reload_mode lazy, a big song open in FL: compose a melody (queued), then `flp_perform(song)`:
   the queued melody is applied first and is in the performed song (not thrown away).
4. **Overlapping patterns of one channel.** A song where two patterns of the same channel play at the same bars
   (a section 808 and a fill / verse 808 on one track): style "none" + `flp_perform(song, parts="all")` leaves the
   .flp byte-identical (FL shows no change); "natural" keeps every 808 note and slide in its own pattern (open
   both patterns in the piano roll), and `verify.ok` is true.
5. **unique_repeats copies.** `flp_perform(song, unique_repeats=True)` on a song with a repeated chorus: FL shows
   new patterns named "<pattern> @<bar>" placed where the repeats were (same track, same clip start/length), each
   plays its own performance, and the song still sounds like the song. PC-VERIFY: the copies made by the server's
   `_add_pattern` carry the name only - check that FL plays them like the original (pattern length / color are
   FL's defaults) and that a clip trimmed to start inside its pattern still starts at the same note.
6. **Undo after copies.** `flp_perform_undo(song)` right after (5): the notes sound as before the perform; the
   pattern copies stay in the project (say so to the user; flp_pattern_tools remove_empty does not remove them).

## Layers played by flh_realism (strings / choir / pads / brass / woodwinds / bells)
Needs flh_realism loaded (ns["realism_apply"]).
1. `flp_perform_setup(song, style="natural")` on a song with a piano and a "Strings" (or "Choir", "Horns", "Pad")
   channel: `detected.<part>.family` names the family; the piano has none.
2. Listen: the piano performs exactly as before (pedal, accents, humanize); the strings/choir layer sounds played
   (swells, breaths, legato, per-family timing) - not pedalled, not accented like a piano. PC-VERIFY in
   `_realism_parts`.
3. `realism.<part>.clash_guard_reverted` > 0 only where a legato tail would rub a semitone against another part; the
   song's clash check (`flh_score` clashes) shows no new high/med issues versus style natural without realism.
4. Expression: with flh_realism's writer present the batch includes its ops (CC / automation on the layer's channel,
   one reload); without it the result says `realism_next: run flp_realism ...`.
5. `flp_perform_setup(song, style="none")`: nothing moves, realism included (exact identity).
