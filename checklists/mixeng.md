# flh_mixeng - PC checklist

Everything here was built and tested on Linux with a fake FL bridge. These steps need a real Windows PC with FL
open and the fl-headless bridge connected. Use a scratch copy of a song (the moves change the mix).

## 1. Plugin maps (all shipped maps are `verified: false`)
1. Put each stock effect on a mixer track: Fruity Parametric EQ 2, Fruity Limiter, Fruity Compressor, Fruity
   Reeverb 2, Fruity Delay 3, Fruity Stereo Shaper, Fruity Stereo Enhancer, Fruity Filter, Fruity Love Philter,
   Fruity Multiband Compressor (all editions); Fruity Convolver, Maximus, Soundgoodizer, Emphasizer (Producer+).
2. For each: `fl_plugin_map_learn(track, slot)`. Expect: a job that finishes with `params_learned` > 0, a map file in
   `%LOCALAPPDATA%\fl-headless\plugin_maps\<plugin>.json`, and the plugin sounding exactly as before (knobs
   restored; values come back within 1e-4 because plugin_params rounds to 4 decimals).
3. Check `named_controls_missing`: these are shipped control names that don't exist under that FL name. Send the
   learned file back so the shipped map can be corrected (indices, FL names, units, ranges).
4. PEQ2 specifically: band type options must read `Off, Low pass, Band pass, High pass, Notch, Low shelf, Peaking,
   High shelf` (fl_mix_apply relies on 3/7 = High pass); band width unit (octaves? Q?) and its range.
5. Time knobs that switch display unit inside their range (e.g. `950 ms` -> `1.2 s`): check the learned table is
   monotone in ms, and that `fl_plugin_set_named` lands on the right value before learning (display search).
6. Fruity Stereo Enhancer: which direction of "Stereo separation" widens.
7. Timing: how long one learn takes per plugin (steps_s); if slow, a `plugin_probe` bridge command (sweep N points
   in-FL, return the texts) would make it one round trip per parameter - the code already uses it when present.

## 1b. Map name check
Automatic: `fl_pc_check(areas=['mix.map_names'])` (sandbox song; your song is reopened after). By hand: put mapped
effects on a track, then `fl_plugin_maps_check(tracks=[<track>], full=true)`.
1. Every mapped stock effect the edition owns loads on 2 empty inserts of the sandbox song (7 per track) and shows
   its parameters live; effects the edition doesn't own are listed under `not_owned` (skipped, not a failure).
2. Per plugin: `matched` (exact FL name + index), `renamed` (old -> new, `how`), `reindexed`, `unmatched` (with
   `candidates` and `live_names_unused`). Check by eye that each rename is the parameter the control means
   (e.g. Fruity Delay 3 `wet_pct` -> `Output wet`, not `Input wet`; Reeverb 2 `wet_pct` -> `Wet level`).
3. Corrected maps land in `%LOCALAPPDATA%\fl-headless\plugin_maps\<plugin>.json` with `names_verified` (date);
   afterwards `fl_plugin_set_named` on e.g. Reeverb 2 `{wet_pct: 20}` reports `matched_by: name+index`.
4. Commit back: copy that folder to a repo checkout and run `python3 tools/plugin_maps_names.py --from <folder>`
   (diff) then `--write`; fix any `unmatched` control in `data/plugin_maps` by hand from the live names.

## 2. Named setting
- `fl_plugin_set_named(track, slot, {"band2_freq_hz": 3000, "band2_gain_db": -2.5, "band2_type": "Peaking"})` on
  a PEQ2, before and after learning. Expect every control `ok: true` and FL's knobs showing 3.00 kHz / -2.5 dB.
- A compressor: `{"ratio": 3, "attack_ms": 10, "release_ms": 120, "threshold_db": -18}` on Fruity Compressor.

## 3. Masking
- Render stems (`fl_render_stems`) of a song where a pad/strings sit on top of the melody, then
  `fl_masking(stems=<folder>, project=<flp>)`: findings name the right tracks and sections, and the dip suggested
  on the masker audibly frees the lead.
- `fl_masking(live=true, start_bar=<chorus>, end_bar=<chorus end>)`: each track is heard alone; afterwards all
  mutes are exactly as before and the master's effects are on again.

## 4. Reference matching
- `fl_reference_match(reference=<a song you like, WAV/FLAC>, mix=<your render>)`: differences make sense by ear.
- `apply=true`: the master gets (or reuses) a Parametric EQ 2; shelves/bells land on free bands and the user's own
  bands (non-zero gain) are left alone. `fl_mix_engineer(revert_last=true)` puts the knobs back.

## 5. Mix engineer
- `fl_mix_engineer(goal="melody clearer")` on an open song: proposals ranked sensibly; fl_mix_check's track names
  map back to the right mixer tracks (duplicate track names map to the first one).
- `apply=true`: faders/pans move live; EQ moves land on PEQ2 bands; a missing Fruity Compressor / PEQ2 is added in
  ONE save-edit-reload of the project (a backup appears in fl-headless's backups folder); the song's journal gets
  a `mix_log` entry; the result has `verify` with the goal's intents measured before/after (master FX bypassed for
  both measurements like fl_mix_check does).
- `revert_last=true` restores faders, pans and every plugin knob that was moved.

## 6. Platform preview
- `fl_platform_preview(audio=<final>)`: numbers match the LUFS/true peak that audio_info / fl_review report.
- Re-check the values in `data/platforms.json` against the platforms' current docs (dated 2026-10).

## 7. Knob safety (fix D)
- While the song plays, `fl_plugin_set_named(track, slot, {'band3_gain_db': -3})` on a Parametric EQ 2 and a
  Fruity Compressor gain/threshold: PC-VERIFY no audible spike - gain-type controls are set with small local steps
  around the map's guess, never the bridge's 0 -> 1 display search. Result `notes` show clamps (e.g. +40 dB -> +12).
- Non-gain controls (frequency, Q, attack) may use the full display search only when FL is stopped.
- `fl_plugin_map_learn` while playing: playback stops first (`stopped_playback: true`), every knob reads back at
  exactly its old value (`restored: exact (read back)`).
- PC-VERIFY: an untouched PEQ2 reads gain ~0 dB, default types Low shelf / Peaking x5 / High shelf and the default
  frequencies (data/plugin_maps band_defaults_hz). If FL's defaults differ, the engineer treats every band as "in
  use" and says "no unused EQ band": update band_default_type / band_defaults_hz.
- A user's high-pass band (0 dB gain) or a moved band is never reused by fl_mix_engineer apply.
- A fader reading -inf / unreadable: the relative move is refused (skipped with a reason), never a jump.

## Transport safety and file-only jobs (fix D2)
1. Start playback, then `fl_plugin_map_learn(track, slot)`. Expect: playback stops before any knob moves
   (`stopped_playback: true`). With a bridge that doesn't answer `state` (e.g. FL busy loading), expect a refusal
   ("couldn't read FL's transport state") and no knob movement.
2. `fl_masking(stems=<folder>, background=true)` on a long stems folder: while the job runs, `fl_status` and
   `fl_snapshot` answer at once (no "FL is busy"). Same for `fl_mix_engineer(stems=...)` without apply and
   `fl_reference_match(reference, mix=<render>)`.


## One room (`fl_room`)
Run on a song with acoustic layers (piano + strings + choir/brass, e.g. an flh_new_song cinematic starter with sounds
loaded) and FL open.
1. `fl_room()` (proposal). Expect: `style: hall` for cinematic/orchestral (chamber for acoustic/jazz, studio for
   pop/rock, `proposal: null` for EDM/hip-hop), decay and pre-delay from the tempo, a send level per layer (piano the
   driest, brass/timpani the wettest), high shelves only on mid/back tracks, seating pans only on centered orchestral
   tracks, drums/808/fx in `skipped`, bus tracks = the first free inserts after the used ones.
2. PC-VERIFY `_find_room_buses`: the chosen inserts are really empty in FL (no effects, default name); a track you
   named "Room" beforehand is reused.
3. `fl_room(apply=true)`. Expect ONE project reload (reverbs + any missing PEQ2s added together), then: tracks
   "Room" (and "Room ER" in hall/chamber) with Fruity Reeverb 2 at 100% wet / 0% dry and the proposed decay/pre-delay
   (PC-VERIFY: Reeverb 2's shipped map is unverified - run `fl_plugin_map_learn` on it once and re-apply if values
   read back off); each layer's send knob to Room/Room ER at the proposed level (PC-VERIFY `_send_levels_arg` /
   send curve: -9 dB shows as about -9 dB on the send knob); each layer still routed to the master (a send, not a
   re-route); `verify.settings` n/n; `verify.sound` wider (width up). Listen: one shared space, piano in front, brass
   behind; nothing got louder overall by more than ~1 dB.
4. `fl_room(apply=true, style="chamber")` again: no new buses (reused), decay changes on the same Reeverb.
5. `fl_room(revert_last=true)`: sends gone (routes removed), pans and bus names back; the reverbs stay in their
   slots, silent (nothing sends to them).
6. With a Producer+ edition and a learned Fruity Convolver map (pre-delay/decay controls): `reverb` auto picks it for
   hall/chamber; without the learned map the reason says how to enable it. PC-VERIFY `_reverb_choice`.

## fl_sound_shape knobs= (flh_sounddesign: value grammar and knob search)
Use a scratch song with a channel on its own mixer track holding Fruity Compressor, Fruity Delay 3, Fruity
Parametric EQ 2 and Fruity Limiter. After each step, look at the knob in FL (hover hint) and compare with the result.
1. `fl_sound_shape(ch, knobs={"Threshold": "-36 dB"}, plugin="Compressor")` from a threshold near 0 dB: the knob
   shows -36 dB (within its last digit); it never visits 0 dB on the way. Then `{"Threshold": "by -3 dB"}`: -39 dB.
2. `{"Gain": "-6 dB"}` (Compressor) and `{"Ceiling": "-1 dB"}` (Limiter): exactly those values.
3. `{"Threshold": "-80 dB"}`: the knob stops at its lowest value and the result says "clamped to the knob's range".
4. Delay 3: `{"mix": "-3%"}` from 34% shows 31%; `{"feedback": "-3%"}` moves only the feedback level (the result
   names it); `{"Wet": "x0.5"}` halves the shown percent; `{"Wet": "=0"}` is the knob's bottom.
5. PC-VERIFY `_search_knob`: when FL's own display search refuses (try a knob whose display is coarse, e.g. integer
   %), our search lands within the last digit in <= 8 round trips (`set` has no "closest FL shows" note), with no
   audible jump during playback.
6. PC-VERIFY `plugin_set_named` unit mismatch (replaces flh_sounddesign's `_eq_width_fallback`): what Parametric
   EQ 2 shows for a band width (octaves, Q or %). If %, a word with an EQ band Q (e.g. `words="warm"`) reports
   "band<N>_width=<text> (set by the plugin map's curve, display shows %: unverified, listen)" in `effects`, and the
   band's curve in the EQ window looks about that wide (Q 0.8 = ~1.7 octaves); `{"Band 2 width": "+10%"}` / `"x0.5"`
   move it; `"0.8 Q"` is refused with a reason. If FL shows octaves or Q, the width is read back and ok.
   If you can tell what PEQ2's width % means (e.g. 100% = some number of octaves), report it: it becomes a known
   conversion instead of the curve guess.
6b. PC-VERIFY a level knob in a unit the map can't convert to (a map dB knob FL shows as %): `fl_plugin_set_named`
   leaves it unchanged and says "never set by a guess"; `fl_plugin_map_learn` on that plugin then sets it normally.
6c. Reeverb 2 `decay_s` (map seconds): `fl_plugin_set_named(track, slot, {"decay_s": "600 ms"})` and `"2.5 s"` both
   land on the shown time (FL switches ms/s inside the knob range).
7. `fl_sound_shape(ch, undo=true)`: every knob above back where it was.

## Live sets that must hold: automation, late plugin values, heavy projects (PC 2026-10-23, PC-VERIFY)
Needs bridge script v7: `fl_bridge_setup(action='update_script')`, restart FL; `fl_ping` shows version 7 (an older
script: every volume set fails with "NOT set: FL runs an older bridge script ... Update it" and nothing moves).
0. Fader curve on FL 2026 (tests/test_fader_fl26.py simulates it): on a fresh project, `fl_mixer_set(1, volume_db=-17.5)`,
   then `-6`, `-4`, `0`, `+3`: each returns `volume_held: true` with that `volume_db`, FL's fader hint shows the same dB
   (+-0.1), and no `fl_db_note` (FL's own dB read-back agrees). `fl_mixer_set(0, volume_db=-1.8)`: master hint -1.8 dB,
   an export is NOT silent. `fl_mixer_set(0, reset=true)` -> `volume_db: 0.0`, `volume: 0.8`. A fader pulled to the
   bottom by hand: a plain `fl_mixer_set(t)` read shows `volume_at_zero: true`, never `volume_db: null` alone.
0b. Channels: `fl_channel_set(6, volume_db=-6)` -> volume 0.748, `volume_db: -6.0`, FL's knob hint -6.0 dB; FL's default
   knob (78 %) reads -5.18 dB (`default_db`).
0c. FL 2026's mixer size: on a new project (`fl_mixer_info()`), note trackCount and whether the last track is FL's
   'Current' track. `fl_channel_set(ch, mixer_track=<that track>)` and `fl_mixer_set(<that track>, name='x')` fail with
   "isn't an insert ... 'Current' track" and change nothing; after adding inserts in FL (if FL 2026 grows the mixer)
   the same track number routes and takes a name. PC-VERIFY `mixer.getTrackInfo(midi.TN_LastIns)` = the last insert.
0d. Reopening the open song after answering No to 'save changes?' (the server's 'mark changed' nudge on the first
   readable insert): that insert's fader is exactly where it was afterwards (same hint dB), never at +5.6 dB or zero.
1. A song with an automation clip on a mixer track's volume (e.g. a 'fade' clip): `fl_mixer_set(track, volume_db=-17)`
   fails with "NOT set: mixer track N's volume follows the automation clip '<name>' ..." and the fader is where it
   was (never at +5.6 dB, never at zero). Same track after deleting the clip in FL WITHOUT saving: the read-back
   check still refuses ("doesn't follow ... the fader was put back").
2. `if_automated='scale_clip'`: every point of that clip moves by the difference (open the clip: the curve is lower/
   higher by that many dB), FL reloads once, the track plays at the asked level where the playhead was.
3. `if_automated='insert_gain'`: Parametric EQ 2 on the track (added in a free slot if missing) shows 'Main level'
   moved by the difference; `fl_mix_engineer(revert_last=true)` puts it back.
4. A track with no automation: `fl_mixer_set(track, volume_db=-6)` -> `volume_held: true`, FL's fader shows -6.0 dB.
5. `fl_plugin_set_value(index=0, slot=<Convolver slot>, param=<Wet level>, target=-38)`: `held: true` and value !=
   before; then fl_save + reopen: Wet level is still -38 dB. If FL doesn't keep it the call fails ("NOT set ...
   Read-backs: [...]") instead of reporting the text.
6. `fl_mix_engineer(apply=true)` on a heavy orchestral song (60 s+ load) that needs effects added: no "didn't show
   up in time", no "no reply from FL within", FL stays open; while FL's title says 'Opening: ...' the job's progress
   says "waiting for FL" and no bridge call goes out. Close FL during the wait: the job ends with "FL stopped running
   ... nothing more was done".
7. The engineer on a track whose fader an automation clip drives: that fader move is skipped with the clip's name.

## Master tone (`flp_master_tone`) - PEQ2 written into the file (PC-VERIFY)
Use a scratch copy of a song. The EQ goes on the master through the plugin map's `file_state` layout
(data/plugin_maps/fruity_parametric_eq_2.json, `verified: false`); tests/test_master_tone.py covers it with a
stand-in preset.
1. Layout proof (`_tone_default`): with FL closed, `flp_master_tone(project, preset='clean')`. Expect `added: true`, a
   slot before any limiter, `read_back.ok: true`. If it fails with "doesn't read as the plugin map's file layout",
   send the message (it names what read wrong): FL's default Parametric EQ 2 state differs from the shipped layout.
2. Open the song in FL, open the master's Parametric EQ 2. Compare each band with the result's `assigned`:
   type (Peaking / High shelf), frequency (150 Hz, 300 Hz, 4.5 kHz within a few Hz), level (-1.5, -3.0, +3.0 dB) and
   width (Q 0.9 is about 1.5 octaves). Report any band whose frequency or width is off: the frequency int
   (0..65536 on the map's log 10 Hz-22 kHz curve) and the width int (0..65536 through the Q -> % `unit_bridges`
   table) are unverified. A learned map (`fl_plugin_map_learn` on that EQ) replaces the curves; re-run step 1.
3. With the song open in FL: the same call saves FL, writes the file and reloads FL once (no "save changes?"
   prompt); the EQ shows the bands; `flp_versions` lists a backup from just before; `flp_restore` with it brings
   the song back without the EQ.
4. Call again with `preset='warm'`: the SAME slot is rewritten (no second EQ), the clean bands are gone. With
   `replace=false`: a user's own Parametric EQ 2 on the master is never touched (a new one goes in a free slot).
5. Render: `fl_preview` and `fl_finalize` of the song now sound like the old "render, then EQ offline" file (null
   test against it if you have one: differences only from the finalize limiter's gain). The fl_finalize result has
   `master_tone: in the render ...`; after removing the EQ in FL (and saving) it says the EQ isn't in the project.
6. Live fallback: make the layout proof fail (e.g. a user map with a wrong `file_state`), FL open on the song: the
   tool answers `via: live`, sets every band through FL (`set` lines ok), saves in FL (`saved_in_file: true`);
   `fl_mix_engineer(revert_last=true)` puts the knobs back.
7. The EQ in a slot AFTER a master limiter (all slots before it taken): the result warns; FL plays it after the
   limiter, so move it up in FL.
