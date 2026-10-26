# Demo: a first session (video script, about 2 minutes)

**Story:** someone who just installed fl-headless makes a lofi beat by talking to Claude, tells it they don't hear
high sounds well, and finishes the track - while FL Studio stays open on screen and nobody touches the mouse.

**Setup before recording**
- Fresh install from the extension: `fl-headless.mcpb` installed (Settings > Extensions > Advanced
  settings > Install Extension), song folders picked - no
  collection profile yet, so the welcome and setup show. Optional cold open (about 15 s before Act 1): Install
  Extension, Claude's Install screen, the folder picker - "that's the whole install".
- The Windows 11 MIDI loopback present (Windows MIDI Services), so "set me up" connects FL by itself. Rehearse that
  once; for the take, start from a state where `fl_ping` already answers or keep the one-time FL restart in the
  cut (it's short and shows that setup happens inside Claude).
- FL Studio open on an empty project. Claude Desktop on the left (about a third of the screen), FL on the right.
- Windows playback set to the device FL plays through, so `fl_listen` can hear it.
- Rehearse once, then recycle the rehearsal song folder so the take starts clean. Numbers in Claude's replies will
  differ from run to run; show the real ones.

## Act 1 - a beat from one sentence

| # | You type to Claude | What happens (tools) |
|---|---|---|
| 1 | *Hi, I just installed this - set me up. What can it do?* | `flh_welcome` + `flh_setup` (bridge script into FL, MIDI connected, or the one step it needs): Claude says it's ready, lists a few things to try that fit this PC (e.g. "make a lofi beat"), states the safety promises in a sentence, and asks one question: which FL edition? |
| 2 | *Producer.* | `flh_setup(action="apply", fl_edition="producer")`: edition stored, a neutral profile created (-14 LUFS, -1 dBTP, 24-bit FLAC). |
| 3 | *Make me a chill lofi beat, dusty and warm, around 80 BPM.* | `flh_new_song(genre="lofi", bpm=80, vibe="dusty warm")`: a new song folder; FL opens it in the same window. Channel rack: Kick, Snare, Hat, Open Hat, Perc, Bass, Keys, Guitar, Pad, Vinyl in dark colors, each on a named mixer insert routed to Drums / Music buses; markers Intro, A, B, A, B, Outro. Press play: the starter beat. |
| 4 | *Give me three melody ideas for the B part.* | `flp_compose(what="melody", section="B", variations=3)`: A/B/C written after the song end with the B section's other parts under each. `fl_listen` plays each one briefly; Claude says how they differ (busier, higher, more space). |
| 5 | *The second one. And make the drums bounce more.* | `flp_compose(what="pick", variation="B")` puts it into every B section; `flp_adjust(words="bounce more", channels=[...drums...])` swings the 16ths and lifts the downbeats. Claude reports the measured swing before/after and how to undo (`flp_versions`). |
| 6 | *Any wrong notes?* | `flp_clashes`: ranked list, e.g. one melody note rubbing against the chord in bar 21, with the fix. Claude applies it through `flh_call("flp_edit_notes", ...)` after you say yes. |

## Act 2 - making it work for you

| # | You type to Claude | What happens (tools) |
|---|---|---|
| 7 | *By the way, I don't hear high sounds very well. Can you tell me in words?* | `fl_access(set={"hearing": "reduced_highs", "language": "plain"})`: remembered for next time. Claude says what changes: measurements are trusted for the highs, and "listen to check" steps come with a no-listening alternative. |
| 8 | *What changed since we started?* | `fl_describe(project=..., compare=<the first version from flp_versions>)`: plain words, per section - drums swung more, B now has a melody, rhythm feel and density - with what was read from the notes vs interpreted. |
| 9 | *Show me the energy of the song.* | `fl_visualize(kind="energy")`: a high-contrast picture inline, with alt text ("quiet intro, B sections the loudest, soft outro"). |

## Act 3 - finishing

| # | You type to Claude | What happens (tools) |
|---|---|---|
| 10 | *Mix it so the keys sit on top, and check the highs aren't harsh.* | `fl_mix_engineer(goal="keys clearer", apply=true)`: listens to each track, moves faders and EQ live in FL's mixer, re-measures the goal and says what it achieved. Harshness is judged by measurement (as set in step 7). One call undoes it all (`revert_last=true`). |
| 11 | *Is it ready?* | `fl_doctor` (static check, under a second) and `fl_review` (section loudness arc, true peak, tone): no high issues, or a short ranked list with fixes. |
| 12 | *Make the final.* | `fl_finalize`: renders (inside the open FL where that works), limits, trims, fades and writes a verified 24-bit FLAC at -14 LUFS next to the project; FL stays or comes back open; the journal records the final. If the mix can't reach the target, Claude says so instead of forcing it. |
| 13 | *How will it sound on Spotify?* | `fl_platform_preview(audio=<final>)`: playback gain per platform and whether anything risks clipping after encoding. |
| 14 | *(next day)* *Where were we?* | `flh_resume`: song, key, BPM, sections, the final, what you liked, three next steps. |

**Tips**
- Speed up waits (listening, rendering) 2-4x in editing, but keep a few seconds of real audio in steps 3 and 4.
- Zoom in on Claude's replies in steps 5, 8 and 12 so the words and numbers are readable.
- Keep the mouse still: nobody touches FL.
- End card: "fl-headless - free, GPL-3.0 - github.com/sertifex/fl-headless".
