# flh_refbeat (fl_beat_from_reference) - PC checklist

Built and tested on Linux with synthetic references (numpy kick/snare/hat + chord tones) and FL mocked
(tests/test_refbeat.py). These need the real Windows PC with FL open. Use your own audio files (any song you have);
the tool is unlisted, so call it through `flh_call`.

1. Plan from an MP3: `flh_call("fl_beat_from_reference", {"reference": "<a 3-4 min .mp3>", "dry_run": true})`.
   Expect `analysis: measured`, `steps_s.analysis_decode` a few seconds (Windows' own MP3 decoder, ONE decode),
   the whole analysis well under a minute, `measured.tempo.bpm` within 1 BPM of the song's real tempo (or exactly
   half/double), `measured.key` the song's key or its relative major/minor in `or`. No temporary WAV is left
   anywhere; `%LOCALAPPDATA%\fl-headless\refbeat\` holds one small .json (tens of KB).
2. Cache: run step 1 again (also with another `genre=`): `analysis: cached`, `steps_s.analysis_cached` a few ms,
   total well under a second.
3. Half-time choice: a trap/drill song usually reads ~70-75 BPM: `plan.bpm` is the doubled value (e.g. 150) and
   `plan.tempo` says "half-time feel". The same file with `genre: "lofi"` keeps the read tempo ("as read").
4. Genre guess (no genre=): try a trap, a lofi, a house and a boom-bap song. `plan.genre` should be right, or the
   right one should be in `plan.genre_ranking`. Note the misses (send the measured block): the `guess` table is data
   (DEFAULTS in flh_refbeat.py, override in data/refbeat/refbeat.json) and gets tuned from these.
5. Chord loop by ear: play the reference's chorus; `measured.chord_loop` for the loud section (Hook / Drop / B)
   should be its chords (simplified to triads, maybe starting on another chord of the cycle). Low
   `chords_confidence` (< 0.6) is expected on busy mixes: say so if the loop is wrong.
6. Sections: a song with quiet verses and loud choruses gives two sections (e.g. Hook + Verse) with their own loops
   and densities (`measured.drums`: kicks/snares/hats per bar, more in the loud part).
7. Vibe words: `plan.vibe` ('dark', 'bright', 'heavy', 'dusty', 'aggressive', 'warm') should sound right for the
   reference. Note any that are clearly wrong with the `measured.tone` line.
8. Build: the same call without dry_run. Expect: a NEW song in your first FLH_ROOTS folder named
   "<file> Type Beat", FL opens it ONCE (no second FL window, no reload after it opened), the playlist has the
   genre's form with markers, the Hook (and Verse when found) patterns play the detected loop at the planned tempo
   and key. `built.batch` says one flp_edit; `built.levels` says balancing in the background and the next tool
   result carries "levels balanced in N s". `renders: 0` for the call itself (the balance's exports come after).
9. Feel, not notes: listen to the new song next to the reference. Tempo / key / chord colour / drum busyness / tone
   should feel related; the melody, bassline and drum pattern must NOT be the reference's (they're newly written).
10. Density: when `built.density` says "busier" or "emptier", the Hook's drums are visibly denser / sparser in the
    piano roll than the genre's usual starter.
11. open=false: `built.opened` false, `built.levels` "pending"; opening the song later with fl_open starts the
    balance once.
12. Failure: point it at a damaged or silent file -> a clear error, nothing built, nothing cached.
