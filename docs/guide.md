# Using fl-headless

[Back to the README](../README.md)

## Getting started

After installing, open Claude and say: **"I'm new to fl-headless - set me up."** Claude shows a short checklist and
walks you through it, one step at a time:

| # | Step | What happens |
|---|---|---|
| 1 | **FL Studio** | Found, with its version. If not, Claude asks where FL is installed. |
| 2 | **Your song folders** | Where new songs are saved (the song folders you picked) and the default finish (-14 LUFS, 24-bit; change any time). |
| 3 | **Connection to FL** | Lets Claude play, change and listen to your song in FL. Needs loopMIDI's two ports, Windows 11's MIDI loopback, or a MIDI keyboard (see [Connect FL](install.md#connect-fl-one-time-both-options)). Claude tells you the one thing to do. |
| 4 | **Your FL edition** | One question: Fruity, Producer, Signature or All Plugins (*Help > About* in FL), or "not sure". Never guessed. |
| 5 | **The fl-headless look** | fl-headless's FL theme (dark, clay-orange accents). Your current theme is saved first as the preset **My theme before fl-headless**: to switch back, pick it in FL's *Options > Theme*. |
| 6 | **Free extras** | What free sounds it found (FLEX, LABS through Splice INSTRUMENT, BBC SO Discover, other plugins). None are required. |
| 7 | **Ready** | What to ask for first. |

Each step says **done**, **needs you** (with one thing to do) or **optional**. Say **"set it up"** and Claude does
everything it can by itself in one go: it saves your edition, makes your default song settings, puts fl-headless's
script into FL and switches on the fl-headless look. If FL is open at that moment, the look is installed and you click
*Options > Theme > fl-headless* once (or setup switches it the next time it runs with FL closed). If you later pick another
theme in FL, setup leaves it alone.

Then try *"make me a lofi beat"* and press play in FL.

**After updating fl-headless:** see [Updating](install.md#updating).

## How it works

- **Project files.** Most edits are made to the `.flp` file itself. If the project is open, FL saves first, the edit
  is written and checked, and FL reloads it in place - one save and one reload for a whole batch of changes. Every
  write keeps a backup.
- **The live bridge.** A small script inside FL (loaded as a MIDI controller) handles live things: transport,
  mixer, plugin knobs, what's selected, recording. It only runs a fixed list of named commands.
- **Listening.** `fl_listen` plays a bar range in the open FL and records the speakers to measure loudness, peaks,
  tone and width. Renders use FL's own export, inside the open FL where that works on your PC, else FL's command-line
  export.
- **Memory.** The **collection profile** (`flh_profile`) holds your house rules: FL edition, owned and trial plugins,
  colors, loudness targets per song role, where finals go. The **song journal** (`flh_journal`) is a small
  `<song>.journal.json` next to each project: sections, channel roles, what you liked and disliked, versions, the
  final. Each session starts with `flh_resume` ("where were we") so nothing has to be re-explained.
- **Essential tools.** fl-headless has over 200 tools, but Claude sees only about 50 essentials (about 8k tokens of
  context instead of about 39k for all of them). `flh_tools` finds and explains everything else, and `flh_call` / `flh_run` call any tool.
  You never need tool names - just say what you want.
- **Big projects.** When a project takes more than 8 seconds to load, edits queue up and reach FL in one reload right
  before you listen, render, play or switch projects (or at `flh_sync`). Anything you changed in FL meanwhile is
  kept.

## What you can ask for

You don't need tool names; they're shown in brackets so you can look them up with `flh_tools(name=...)`.

**Start and write**
- *"Make a drill beat in F minor"*, *"set me up an empty pop template"* (`flh_new_song`: 26 genres, real sounds you
  own, routed and colored mixer, starter beat)
- *"Write a bassline that follows the chords"*, *"add a counter-melody"*, *"put strings under the chorus"*
  (`flp_compose`)
- *"Three hook ideas, I'll pick"* (`flp_compose` with variations, then pick)
- *"Make it darker / emptier / more human / bounce more"* (`flp_adjust`)
- *"Turn this into a full song: intro, verse, hook..."*, *"make the second verse 8 bars longer"* (`flp_arrange_song`)
- *"Save this as my template"* (`flh_save_template`)

**Ideas**
- *"Here's me humming the melody (voice memo)"* (`audio_to_midi`)
- *"Record what I play on my keyboard and build chords and drums around it"* (`fl_capture_idea`,
  `capture_and_build`)

**Check it**
- *"What's in this song?"* (`flp_score`), *"any wrong notes?"* (`flp_clashes`)
- *"Is the project OK before I render?"* (`fl_doctor`), *"review the song"* (`fl_review`)
- *"How loud is the drop?"* (`fl_listen`)
- *"Compare it with the version from this morning"* (`fl_ab`)

**Perform**
- *"Make the bridge 3% slower and the last chorus a bit more human"* (`flp_perform_setup`, `flp_perform`)

**Mix and sound**
- *"Find me a soft string pad"* (`fl_sound_catalog`, `fl_audition`)
- *"Make the vocal clearer"*, *"undo that"* (`fl_mix_engineer`)
- *"What's masking the lead?"* (`fl_masking`), *"make it sound more like this reference"* (`fl_reference_match`)
- *"Sidechain the bass to the kick"* (`flp_duck`)

**Vocals**
- *"Set me up to record vocals"* (`fl_vocal_setup`, `fl_vocal_level_check`)
- *"Record three takes of bars 9-17"* (`fl_vocal_record`), then *"comp the best one"* (`fl_vocal_comp`)
- *"Clean up breaths and clicks"* (`fl_vocal_clean`), *"give me a rap vocal chain"* (`fl_vocal_chain`)

**Finish and share**
- *"Make the final"* (`fl_finalize`: render, limit, trim, fade, FLAC, filed and verified)
- *"How will this sound on Spotify?"* (`fl_platform_preview`)
- *"Export stems"*, *"give me the instrumental and the vocals"* (`fl_render_stems`, `groups='instrumental'`),
  *"pack this for my collaborator"* (`flh_collab_pack`)
- *"How does the drum part sound on its own?"* (`fl_preview`: rendered by FL, faster than real time)
- *"Make the ending longer for the game"* (`flp_extend_ending`), *"the strings come in too hard"*
  (`flp_smooth_entries`), *"a mod version and a YouTube version"* (`fl_finalize` with `finals`)

**Learn FL**
- *"How do I sidechain in FL?"* (`fl_help`, `flh_handbook_search`)
- *"Build the FL manual index"* (`flh_handbook_build`, once; then answers work offline)
- *"Explain what you just did"*, *"explain as we go"* (`flh_teach`, teaching mode in the profile: what each step
  did and why)

**Housekeeping**
- *"Undo the last edit"* (`flp_versions`, `flp_restore`), *"remove the channels I'm not using"*
  (`flp_remove_channels`), *"is everything OK?"* (`fl_watchdog`), *"clean up old backups"* (`flh_tidy`)

