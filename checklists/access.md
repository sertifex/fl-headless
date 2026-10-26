# flh_access (fl_access, fl_describe, fl_visualize, fl_second_opinion, fl_safe_listening, fl_where) - PC checklist

Everything except fl_where runs offline (renders and .flp / .mid files), so the logic is tested on Linux
(`python3 tests/test_access.py`). What needs a real PC with FL, real songs and real people:

1. **fl_where on a live FL** (PC-VERIFY in `fl_where`): open a saved multi-section song, focus the playlist, select
   channel 2 in the channel rack, play to the chorus and stop, then `fl_where()`. Expect one short line each:
   project name, "Focus: the playlist", "Stopped at bar N, in Chorus (bars a-b); song mode", the selected channel
   with its instrument and mixer track, the pattern, "No popups open." Then make a change, start opening another
   project so FL shows "Save changes?", run `fl_where()` again: the popup line lists its text and the buttons.
   Repeat with the mixer and the piano roll focused.
2. **Screen reader pass**: with NVDA (free) or Narrator on, ask Claude "where am I in FL?", "describe my song",
   "show me the energy" with `fl_access(set={"vision": "blind"})`. Claude's replies must make sense read aloud: no
   tables of spaces, no ASCII art, no "see the picture". fl_visualize must say the picture was skipped and give the
   alt text.
3. **fl_describe on 3 real songs** (one pop/EDM, one orchestral or piano, one with swing): `fl_describe(project,
   audio=<render>)` and `detail="full"`. Check against what you hear: tempo and key right (or marked uncertain),
   the loudest section named correctly, intro/outro called soft, "same chords as ... but fuller" only where true,
   the swung song called swung. Timing: < 5 s for a 4-minute render (`steps_s`).
4. **What changed**: render a song, raise the chorus 3 dB and add a high shelf on the lead, render again;
   `fl_describe(project, audio=<new>, compare=[project, <old render>])`. Expect "Chorus 3 dB louder (a clear
   change)" and "brighter". With identical renders: "No measurable change".
5. **Pictures**: `fl_visualize` each kind on a real render in Claude Desktop. The image appears inline, labels are
   readable at 100% zoom, colors stay distinguishable in a color-blindness simulator (e.g. Windows color filters:
   deuteranopia / protanopia / grayscale), light theme readable. `fl_access(set={"vision": "low"})` makes them
   bigger (1400x700). `save=` writes inside FLH_ROOTS only.
6. **Hearing-aware second opinion**: on a deliberately harsh render (boost 3-4 kHz by 8 dB on the lead),
   `fl_access(set={"hearing": "reduced_highs"})`, then `fl_second_opinion("sounds fine to me", audio=...)`:
   the harsh / ringing-peak items are listed, kindly, each saying the measurement is the steadier guide there.
   On a clean commercial-style mix: nothing or only low items. Note the tone values of 5 finished songs in
   different genres: if clean ones get flagged, adjust `data/access/hearing.json` "tone" and report the numbers.
7. **Brightness / bass words**: the tilt and low-end tables in `data/access/describe.json` are rules of thumb.
   Run `fl_describe(audio=...)` on 5-10 finished songs of different genres and check "balanced / bright / dark" and
   "moderate / strong bass" match what most listeners would say; adjust the thresholds and report.
8. **FL keyboard shortcuts** in `data/access/keys.json` (F5 playlist, F6 channel rack, F7 piano roll, F9 mixer,
   Alt+F8 browser, Space, L, Ctrl+S, Ctrl+Z, Esc): check each on the current FL version and language; fix the file.
9. **Preferences**: `fl_access(set={"hearing": "reduced_highs", "language": "plain"})`, restart Claude Desktop,
   `fl_access()` shows them again; `flh_profile()` has them under `access` when a collection is active;
   `fl_access(clear="all")` removes them from both.

## Proposed README section (for the integrator)

### Accessibility - fl-headless is for every music enjoyer

You never need to hear the speakers, see the screen or use a mouse to make music with fl-headless: everything
works by chat, every result can be read as plain text, every picture comes with a description, and every
judgment comes with numbers *and* words.

Tell Claude what helps you - only if you want to, in your own words. It is remembered for next time
(`fl_access`), and you can change or clear it any time.

| If you... | Say something like | What changes |
|---|---|---|
| are deaf or hard of hearing | "I'm hard of hearing - describe what changed" / "I don't hear high sounds well" | `fl_describe` tells you in words how the song sounds and what changed between versions (louder / brighter / fuller, per section); measurements are trusted over listening where you said hearing is less reliable; "listen to check" steps get a no-listening alternative |
| use hearing aids or a cochlear implant | "I listen with a cochlear implant" | tone and pitch checks lean on the measurements; safe-listening tips for devices |
| are blind or use a screen reader | "I use a screen reader" | `fl_where` tells you where you are in FL (window, bar, section, selection, popups); results lead with text; pictures only if you ask |
| have low vision | "I have low vision" | bigger pictures with large labels, high contrast, colorblind-safe colors (`fl_visualize`) |
| can't use a mouse well | "I can't use a mouse" / "I use voice control" | Claude does it with tools; anything that says "click" comes with chat and keyboard alternatives |
| are new to music or prefer simple words | "Explain things simply" / "one step at a time" | jargon is explained in brackets (LUFS, sidechain, EQ...); multi-step tasks go one step at a time |

Tools:
- `fl_describe(project, audio, compare=...)` - the music in words: tempo feel ("about 1.7 beats a second, like an
  easy stroll"), key and mood, the parts and their roles, where it is whisper-soft and where it explodes, rhythm
  feel, low end you would feel as vibration, stereo picture - and what changed since the last version. It says what
  was measured, what was read from the notes and what is interpretation.
- `fl_visualize(kind)` - energy, loudness, spectrogram, mix balance, arrangement, piano roll as high-contrast
  pictures with alt text.
- `fl_second_opinion("sounds fine to me", audio)` - your ears and the measurements side by side, kindly.
- `fl_safe_listening()` - fl-headless measures loudness for you, so there is no need to turn it up to judge; tips on
  levels and breaks (general advice, not medical advice).
- `fl_where()` - a spoken-friendly map of the open FL project.
- `fl_access()` - see or change what you told fl-headless about what helps you.

Nobody is asked about their body; nothing is guessed. These tools are useful for everyone - "describe what changed"
is a quick check for any producer.
