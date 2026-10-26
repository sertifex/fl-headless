# flh_transcribe: PC checks

Audio to MIDI runs and is tested offline (`python3 tests/test_transcribe.py`). The checks below need a Windows PC
with FL Studio, a saved project open in FL and the fl-headless bridge running. Code spots are marked
`# PC-VERIFY` in `flh_transcribe.py`.

## 0. Probe first
- Run `fl_capture_probe`. Expect `checks.bridge: true`, `project_saved: true`, your keyboard's name in
  `checks.keyboards` (loopMIDI / fl-headless ports are filtered out), no `popups`.
- The bridge has the `countdown` and `loop_record` song actions (shipped in 2.0.0): with an updated bridge script,
  `countdown_control` / `loop_record` are reported. With an older script `next` says to update it.
- The names in `checks.midi_inputs` should match FL's Options > MIDI settings > Input list.

## 1. Timed capture (`fl_capture_idea(bars=4)`)
- A new pattern "Idea N" (one past the last pattern) appears and is selected. Check that FL creates the pattern when
  `jumpToPattern` gets a number past the last one, and that the name shows (it's set after the pattern is selected).
- FL switches to pattern mode, the metronome comes on, playback starts at the pattern start, and recording is armed.
  If FL shows a "what would you like to record?" box, note its title: the result's `popups` should list it.
- Play 4 bars on the keyboard into the selected channel (the `channel` argument, or the channel that was selected).
  The notes should land in the new pattern on that channel. A keyboard set to FL's generic controller plays the
  selected channel; a keyboard linked to one channel by MIDI channel won't follow the selection, so check that case too.
- The tool waits for count-in + 4 bars, then stops. The metronome, pattern/song mode and record arm go back to how they
  were (`restored`). The result has the notes, `notes_per_channel`, an `analysis` (key, chords per bar, feel) and an
  `idea` block.
- The count-in: by default the tool waits one bar for it (`count_in_bars`). If FL's count-in is 2 bars, the last bar of
  the take is cut, so pass `count_in_bars=2` and record which value FL uses by default.
- Loop recording: with FL's Loop recording on, check whether a 4-bar take into a new (empty, 1-bar) pattern stacks up
  in bar 1. If it does, the bridge's `loop_record` action is needed (capture switches it off for the take). Until the
  bridge has it, turn Loop recording off by hand.
- Pressing Stop in FL partway through should end the take early, with `stopped_by_user: true` and the notes so far.

## 2. Open-ended capture
- `fl_capture_idea()` with no bars or seconds should start recording and return `recording: true`. Play, then call
  `fl_capture_idea(action="stop")`. Expect the notes. Restarting the server between the two calls should still work,
  because the session is kept in state.json.
- `fl_capture_idea(action="cancel")` should stop, restore everything and read nothing.

## 3. Read fallback (you record by hand)
- In FL, record something into any pattern yourself (or draw notes), leave that pattern selected, then call
  `fl_capture_idea(action="read")`. FL saves, and the tool returns the notes added since the last save
  (`only_new_notes: true`), or the whole pattern if it was empty before.
- Check that pattern numbers in the .flp match FL's numbering. The result's `pattern` should be the number FL shows.

## 4. Saving and safety
- Untitled project: capture should refuse with "save the project in FL once first" and change nothing.
- After a stop, FPT_Save should write the file without any prompt. If FL is busy and doesn't save within 8 s, the
  result has `error` plus the pattern to read later. Try it with a heavy project.
- A project outside FLH_ROOTS should be refused (it raises PermissionError from `writable`).

## 5. Audio to MIDI on real recordings (listen to every result)
- A sung or whistled melody (phone recording, WAV): `audio_to_midi(input=..., output_mid=..., mode="auto")`. Expect
  `detected.kind: melody`, notes in the right octave, and vibrato/slides not split into extra notes. Drag the .mid into
  FL and play it against the audio.
- Bass DI or synth bass: expect `kind: bass` and notes from E1 up.
- A drum loop exported from FL (with the tempo known): tempo within 1 BPM, kick/snare/hat on the right steps. With
  `quantize="1/16"` the hits sit on the grid. Then try a live drum recording and write down the per-class misses.
- A piano or guitar chord progression: the chord chart should mostly match. Write down which qualities get confused
  (maj7 vs m/3rd-less voicings, sus vs add). The result carries a "polyphonic transcription is approximate" warning.
- A full mix: expect `kind: mix`, drums plus chords, and modest confidence.
- FLAC input: needs flh_review's decoder (flac.exe / ffmpeg on PATH). Without it, the message should say to use WAV.
- Into a project: `audio_to_midi(..., project=<open .flp>, channel={"kick": 0, "snare": 1, "hat": 2})`. This should
  run ONE save/reload in FL, add a "Transcribed drums" pattern at bar 1 with the hits on those channels, and keep
  the open FL window (no second FL).

## 6. Build around it
- `capture_and_build()` after a capture. Without flh_compose it returns suggestions (chords per bar, bass roots, a
  drum style). With flh_compose installed, it calls `compose_around(idea=..., what=..., genre=..., project=...)`.
