# flh_realism (flp_realism, flp_realism_write, ns["realism_apply"]) - PC checklist

Logic is tested offline (`python3 tests/test_realism.py`); what only FL can show. Use a copy of a real song with a
piano plus BBC SO Discover and/or LABS / Splice INSTRUMENT strings, choir, a pad and (if you have one) a FLEX or
Sytrus pad. Run `flp_realism(song, dry_run=true)` first and read the per-channel lines.

1. **CC automation reaches the VST (the core assumption).** `fl_plugin_params(index=<strings channel>,
   filter="CC")` lists MIDI CC parameters around index 4096-4223 (CC1 = 4097, CC11 = 4107). If they are elsewhere,
   put the right base in `<state folder>/realism/libraries.json` as `{"wrapper": {"cc_param_base": N}}` and
   report it. Then `flp_realism(song)`; in FL play the Verse: BBC SO's / LABS's **Dynamics** and **Expression**
   knobs move with the clips "<channel> dynamics - Verse" / "<channel> expression - Verse" (PC-VERIFY in
   `_automation_target` and `_targets`: dest = channel index, param = 0x8000 | (4096 + CC)).
2. **Clips in the playlist.** The clips sit on tracks named "<channel> dynamics" and "<channel> expression"
   (PC-VERIFY `_name_track`: name event 239 after the track's 238), start just before each section's first note of
   that channel and end at its last phrase end. Double-click one: the curve swells into long notes and decays at
   phrase ends; values stay between ~0.15 and ~0.95.
3. **Channel volume fallback.** On a FLEX/Sytrus pad (no controller): the channel's volume knob follows the
   "<channel> expression - <section>" clip and never goes above where it was before the run (PC-VERIFY in
   `_channels`: event 219 volume raw/12800 = the automation value; param 0 = REC_Chan_Vol on dest = channel).
4. **Notes.** Piano roll of the strings: attacks a few ms late (the project's PPQ sets the step: 96 PPQ at 90 BPM =
   7 ms per tick), chord notes not all identical; choir: the note before each breath ends earlier; timpani rolls
   alternate velocities; the piano channel is byte-for-byte unchanged (`flp_diff` before/after).
5. **Articulation.** A strings channel loaded with "Long" playing only short notes: after the run the INSTRUMENT /
   BBC window shows the Short (Spiccato/Staccato) articulation active and plays it; `flp_realism(song, undo=true)`
   brings "Long" back.
6. **Legato.** BBC SO with a Legato articulation loaded: overlapping notes (45 ms) play legato transitions, no
   stuck notes at phrase ends.
7. **Keyswitch libraries (user data).** Add `{"libraries": [{"id": "<library id>", "keyswitches": {"long": 24,
   "short": 25}}]}` for a keyswitch patch: each section gets one keyswitch note just before (or at) its first note;
   the patch switches. At a clip's first tick the keyswitch shares the tick with the first note - check FL sends it
   first (lower key), else move that section's clip a 32nd earlier.
8. **Open project.** With the song open in FL: `flp_realism(song)` saves, writes and reloads ONCE (one flp_edit);
   in lazy mode the result says `queued` and `flh_sync()` applies it.
9. **Re-run / undo.** Edit a few strings notes in FL, save, `flp_realism(song)` again: no duplicate clips, your
   edited notes keep your edit; `flp_realism(song, undo=true)`: every realism clip is gone, your own automation
   (and the edited notes) stay, and the song sounds like before the first run (A/B with `fl_ab`).
10. **Ears.** Listen to the strings/choir/pad A/B against the version before the run (`fl_ab(song)`): the proxies
    in `checks` (movement inside notes, timing spread, lengths in range) are not a verdict. If it sounds overdone,
    `flp_realism(song, amount=0.4)`; note what you liked in the journal (`flh_journal`).
11. **New channel in the same batch.** `flp_compose(song, what="layer", role="choir")` on a song without a choir
    channel: one reload; the cloned Choir channel has its expression clips right away (flp_realism_write resolves
    the channel when the batch runs); running `flp_realism(song)` afterwards updates those clips by name, no
    duplicates (`# PC-VERIFY` in `flp_realism_write`).
