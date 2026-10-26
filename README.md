# fl-headless

**A patient studio partner for FL Studio: make music more easily by talking to Claude, and understand what's happening as you go.**

fl-headless is not built to make your music for you. It's built to help you make it: to explain what's going on in
your project, take care of the fiddly steps, show you what it changed and why, and open FL Studio up to people who
find everyday studio tasks hard - beginners, and people who can't hear the speakers well, can't see the screen or
can't use a mouse. Your ideas and your taste stay in charge; it says honestly when only your ears can decide.

Under the hood it's a free [MCP](https://modelcontextprotocol.io) server for **FL Studio on Windows**. It reads and
writes `.flp` project files directly, drives the open FL through a small script that runs inside FL, listens to what
FL plays, and renders with FL's own export. Everything it does is backed up, checked and reported honestly -
including what it couldn't verify. FL stays open on your screen and your mouse stays yours.

Things you can say:

- *"Make me a lofi beat, around 80 BPM, dusty and warm."*
- *"Give me three melody ideas for the hook and let me pick one."*
- *"The chorus feels flat - make it bounce more."*
- *"Is anything clashing? My bass and chords sound muddy around bar 17."*
- *"Mix it so the vocal is clearer, then make the final at streaming loudness."*
- *"I'm hard of hearing - tell me in words what changed since the last version."*
- *"What did you just change, and why? I want to learn how to do it myself."*

**Beta:** version 2.1.0 is the first public release, tested on one setup so far - expect some rough edges, and
please [report what breaks](https://github.com/sertifex/fl-headless/issues). See [CHANGELOG.md](CHANGELOG.md) for
what's new.

---

## Requirements

- **Windows 10 or 11** (FL Studio on macOS isn't supported).
- **FL Studio 20.8 or later**, any edition. Tested on FL Studio 2026 so far; on an older FL, run `fl_selftest`
  once and please report what you find.
- **Claude Desktop** (or another MCP client that runs local servers).
- **One MIDI input in FL** for the live tools: Windows 11's built-in MIDI loopback,
  [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) (free), or your MIDI keyboard. Editing song
  files, rendering and audio work without it.
- **Python 3.10+ with numpy**: only for Option B. The extension (Option A) brings its own.

Not needed, but they unlock extra tools: a microphone or audio interface (record vocals), a MIDI keyboard (play
in ideas).

Audio it reads: WAV, FLAC, Ogg and AIFF, plus MP3 / M4A / WMA through Windows' own decoders (no ffmpeg).

Also works if your Documents folder is synced to OneDrive or moved, if your Windows user name has accents, or if FL
is set to another language (answering FL's own popups in other languages is best effort). None of these are
needed.

## Install

Two ways, both free. Option A is the easiest; Option B is the classic installer and works the same.

### Option A - Claude Desktop extension (recommended)

1. Download **`fl-headless.mcpb`** from the [Releases page](https://github.com/sertifex/fl-headless/releases/latest).
2. In Claude Desktop: **Settings > Extensions > Advanced settings > Install Extension...**, pick the file, then
   click **Install**.
3. Pick your **Song folders** with the folder icons (your Music folder, your FL projects folder), then **Save**.
4. Start a new chat and say: **"I'm new to fl-headless - set me up."**

No command window and nothing else to install: Claude sets up Python by itself (the first start takes about a
minute). Dragging the file onto the Extensions page works too. Leave *FL Studio program* empty unless setup says it
can't find FL; you can change the folders any time in *Settings > Extensions*.

Don't use both options at once. If you installed with `install.bat` before: **fully quit Claude** (right-click its
icon near the clock > *Quit*; closing the window isn't enough, Claude would put the old entry back), run
`install.bat --uninstall` in the old folder, start Claude, then install the extension. fl-headless warns you when
it sees both.

Then connect FL once (below).

### Option B - install.bat (manual / advanced)

1. **Get the files.** Download the latest release ZIP from the
   [Releases page](https://github.com/sertifex/fl-headless/releases/latest) (or *Code > Download ZIP*). Right-click
   the ZIP > *Properties* > tick **Unblock** > *OK*, then unzip it somewhere permanent, e.g. `Documents\fl-headless`.
   (Unblocking stops Windows from treating every file inside as "from the internet".)
2. Fully quit Claude Desktop (tray icon > Quit).
3. Double-click **`install.bat`**. It:
   - finds Python 3.10+ (no Python, or too old: offers to install Python 3.12 for your user with `winget`,
     Windows' own installer) and checks numpy (and offers to install it)
   - asks which folders fl-headless may write to: **`FLH_ROOTS`**, e.g. your Music folder and your FL projects
     folder (semicolon-separated; press Enter to keep the suggestion or your previous setting)
   - writes `fl_headless_launch.py` and adds fl-headless to Claude Desktop's config, keeping
     `claude_desktop_config.json.bak` (and `.original.bak` from the very first run)
   - finds FL Studio and installs the bridge script into FL (`Settings\Hardware\fl-headless`)
   - checks your MIDI ports, asks your FL edition (optional) and checks that speaker recording works
   - starts the server once exactly as Claude will and checks it answers, so a broken install shows up here
     instead of as "server disconnected" in Claude (the summary is kept in `%LOCALAPPDATA%\fl-headless\install_log.txt`)

   If Windows shows *Windows protected your PC* (or a security warning): click **More info > Run anyway**.
   fl-headless is free and not code-signed; the installer is plain text you can read first (`install.bat` and
   `install.py`).
4. Open Claude Desktop again.

### Connect FL (one time, both options)

**Easiest: you can skip this section.** "Set me up" checks your PC and tells you the one step that fits it. The
details, for reference:

FL's live tools need one MIDI input that runs fl-headless's script. Pick one:

- **loopMIDI** ([free download](https://www.tobias-erichsen.de/software/loopmidi.html)): create two ports named
  exactly `Flapi Request` and `Flapi Response`. In FL, *Options > MIDI
  settings*: under *Input* select `Flapi Request`, enable it, set *Controller type* to `fl-headless` and give it a
  port number; under *Output* give `Flapi Response` the same port number.
- **Windows 11 without extra apps:** create the default loopback in the Windows *MIDI Settings* app, then ask Claude
  to *"run fl_bridge_setup use_loopback"*. It sets FL's MIDI settings for you; with FL open it asks before
  restarting FL once (`restart_fl=true`).
- **No extra app at all, with a MIDI keyboard:** ask Claude to *"run fl_bridge_setup use_input"* with your
  keyboard's name. FL runs the bridge script on the keyboard's input (its notes still play) and everything goes
  through a file mailbox; `undo_input` puts the keyboard's old setting back.

### Manual config (Option B)

`claude_desktop_config.json`, under `"mcpServers"`:

```json
"fl-headless-manual": {
  "command": "C:\\Path\\To\\python.exe",
  "args": ["C:\\Users\\you\\Documents\\fl-headless\\fl_headless_launch.py"],
  "env": { "FLH_ROOTS": "C:\\Users\\you\\Music;D:\\FL Projects" }
}
```

(Without the launcher, point `args` at `fl_headless_mcp.py`.) Optional `env` settings:

| Setting | What it does |
|---|---|
| `FLH_FL_EXE` | Full path to `FL64.exe` if it isn't found |
| `FLH_FL_USERDATA` | FL's user data folder, if you moved it |
| `FLH_FL_EDITION` | `fruity`, `producer`, `signature` or `all_plugins` (or let `flh_setup` store it) |
| `FLH_TOOLSET` | `full` lists every tool to the client instead of the essentials |
| `FLH_BLANK` | An empty project used when FL must re-read the open one |
| `FLH_PORT_OUT` / `FLH_PORT_IN` | Force MIDI port names |
| `FLH_RECORDED_DIR` | Where FL writes recordings, if not the default |
| `FLH_FL_HELP` | A folder with FL's local help pages, if your install keeps them somewhere unusual |
| `FLH_FAST` | `0` turns the speed paths off (every edit reloads FL at once, nothing goes in live) |
| `FLH_COALESCE_S` | Seconds of quiet after the last edit before the merged edits reload in FL (default 2.5) |
| `FLH_NO_WARM` | `1` skips the background warm-up of the sound indexes and sample measurements |

The installer adds itself to Claude's config as `fl-headless-manual` (the extension is called `fl-headless`;
two entries with one name make Claude start neither). Quit Claude before running it - it asks you to.
Running the installer again keeps the settings you added by hand to the `fl-headless-manual` entry (`FLH_FL_EXE`,
`FLH_TOOLSET`...): it refreshes only `command` and `args`, and changes `FLH_ROOTS` only when you give new folders.
Folders must be full paths (`D:\FL Projects`, not `FL Projects`). `python install.py --help` shows the options and
changes nothing.

### Updating

- **Extension (Option A):** download the new `fl-headless.mcpb` from the
  Releases page and install it the same way (*Settings > Extensions > Advanced settings > Install Extension...*);
  Claude Desktop replaces the old version and keeps your settings.
- **ZIP (Option B):** download the new release ZIP, **Unblock** it, and unzip it over the old folder (replace the
  files). Your settings, songs and fl-headless's notes live elsewhere and are kept.
- **git:** `git pull` in the folder.

Then ask Claude to run **`flh_reload`**: it picks up the new and changed files without restarting Claude. If the
bridge script inside FL changed (`fl_ping` tells you), run `fl_bridge_setup(action="update_script")` and restart FL
once. Some updates need a Claude restart; the [changelog](CHANGELOG.md) says so.

### Uninstall

- **Extension:** remove fl-headless in Claude Desktop's *Settings >
  Extensions*.
- **install.bat:** fully quit Claude first, then `install.bat --uninstall` removes fl-headless from Claude's config
  (keeping a `.bak`) and, after asking, moves the bridge script folder in FL to the Recycle Bin. It waits while
  Claude is still running.

Your songs and fl-headless's notes are left alone either way.

## Getting started

After installing, open Claude and say: **"I'm new to fl-headless - set me up."** Claude shows a short checklist and
walks you through it, one step at a time:

| # | Step | What happens |
|---|---|---|
| 1 | **FL Studio** | Found, with its version. If not, Claude asks where FL is installed. |
| 2 | **Your song folders** | Where new songs are saved (the song folders you picked) and the default finish (-14 LUFS, 24-bit; change any time). |
| 3 | **Connection to FL** | Lets Claude play, change and listen to your song in FL. Needs loopMIDI's two ports, Windows 11's MIDI loopback, or a MIDI keyboard (see [Connect FL](#connect-fl-one-time-both-options)). Claude tells you the one thing to do. |
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

**After updating fl-headless:** see [Updating](#updating) (`flh_reload`, no Claude restart needed).

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

## Accessibility - for every music lover

fl-headless is built so that you shouldn't need to hear the speakers, see the screen or use a mouse to make music:
everything works by chat, every result can be read as plain text, every picture comes with a description, and every
judgment comes with numbers *and* words. The one-time setup (installing it, and FL's MIDI settings) may still need a
hand from someone else.

Honest note: these features were designed with these needs in mind, but they haven't been tested by disabled
musicians yet. If you use fl-headless this way, please tell us what works and what doesn't
([open an issue](https://github.com/sertifex/fl-headless/issues)) - that feedback shapes what comes next.

Tell Claude what helps you - only if you want to, in your own words. It is remembered for next time (`fl_access`), and
you can change or clear it any time.

| If you... | Say something like | What changes |
|---|---|---|
| are deaf or hard of hearing | "I'm hard of hearing - describe what changed" / "I don't hear high sounds well" | `fl_describe` tells you in words how the song sounds and what changed between versions, section by section. Where you said your hearing is less reliable, measurements are trusted over listening, and "listen to check" steps come with a no-listening alternative. |
| use hearing aids or a cochlear implant | "I listen with a cochlear implant" | Tone and pitch checks lean on the measurements; safe-listening tips for your device. |
| are blind or use a screen reader | "I use a screen reader" | `fl_where` tells you where you are in FL (window, bar, section, selection, popups). Results lead with text; pictures only if you ask. |
| have low vision | "I have low vision" | Bigger pictures with large labels, high contrast and colorblind-safe colors (`fl_visualize`). |
| can't use a mouse well | "I can't use a mouse" / "I use voice control" | Claude does it with tools; anything that says "click" comes with a chat or keyboard alternative. |
| are new to music or prefer simple words | "Explain things simply" / "one step at a time" | Jargon is explained in brackets (LUFS, sidechain, EQ...); multi-step tasks go one step at a time. |

Tools that help:
- `fl_describe` - the music in words: tempo feel, key and mood, the parts and their roles, where it is whisper-soft
  and where it explodes, rhythm feel, low end you'd feel as vibration, the stereo picture - and what changed since the
  last version. It says what was measured, what was read from the notes and what is interpretation.
- `fl_visualize` - energy, loudness, spectrogram, mix balance, arrangement and piano roll as high-contrast pictures
  with alt text.
- `fl_second_opinion` - your ears and the measurements side by side, kindly.
- `fl_safe_listening` - fl-headless measures loudness for you, so there's no need to turn it up to judge; tips on
  levels and breaks (general advice, not medical advice).
- `fl_where` - a spoken-friendly map of the open FL project.
- `fl_access` - see or change what you told fl-headless about what helps you.

Nobody is asked about their body and nothing is guessed. These tools are useful for everyone: "describe what changed"
is a quick check for any producer.

## Safety and privacy

- **Writes only where you allow.** fl-headless writes only inside your `FLH_ROOTS` folders. The only other places:
  its own folder in `%LOCALAPPDATA%\fl-headless` (state, backups, learned plugin maps, your recipes, the manual
  index, bug reports), FL's own settings folder (the bridge script, its mailbox and the optional live-notes piano-roll script), and FL's
  Templates folder when you
  save a template there. No tool can widen `FLH_ROOTS`; only your config can. Reading is allowed anywhere.
- **Nothing is deleted for good.** Deletes go to the Windows Recycle Bin. On a drive without one (USB stick, network
  share) the delete is refused.
- **Backups of every edit.** Each project keeps its own backups (`flp_versions`, `flp_restore`). A file is parsed
  back before it replaces the original, and a save FL is still writing is never edited.
- **Its own folder stays small.** `flh_tidy` lists old backups (beyond the newest 20 per project and older than two
  weeks; backups of deleted projects after 2 days), old check reports, bug bundles and temp files with their sizes,
  and moves them to the Recycle Bin when you say `apply=true`.
- **Your unsaved work is safe.** fl-headless proves which project FL has open before acting on it, and never answers
  "No" to FL's "save changes?" for a project it can't identify. A new project you never saved is never saved for
  you: `fl_open` stops and asks you to save or close it first.
- **Some tools save the open project in FL.** Editing the project that's open in FL, `fl_open` of it, and reads that
  need FL's latest state (compose / score) first ask FL to save it, like pressing Ctrl+S: changes you were still
  trying out in FL are then in the file. The edit that follows makes a backup (`flp_versions`), but that save
  itself doesn't. Save a copy in FL first if you want to keep the version before your experiments.
- **One FL only.** It never starts a second FL and never closes FL unless you asked (`fl_close` needs your OK;
  tools that must restart FL ask first).
- **Never "Save anyway".** A "trial/demo version" warning on save means that plugin gets removed, never saved over.
- **Only what you own.** Sounds and plugins come from your FL edition, free plugins and what you've installed;
  anything swapped is said out loud. Your samples stay where they are.
- **No code runs from messages.** The script inside FL runs a fixed list of named commands with typed values; the
  server never evaluates code either.
- **Settings are reversible.** Only FL's own registry key (`HKCU\Software\Image-Line`) is changed, after a full
  snapshot.
- **No telemetry.** fl-headless sends nothing about you anywhere. It uses the network for two things only: FL's
  manual, fetched from image-line.com to your PC when you ask an FL question or build the manual index, and a look
  at this project's GitHub releases page at most once a week to tell you when a new version is out (a plain
  request with nothing about you in it; turn it off with `flh_updates(enable=false)` or `FLH_UPDATE_CHECK=0`).
  fl-headless doesn't ship Image-Line's manual or any copyrighted content.
- **Bug reports are scrubbed.** `flh_bug_report` makes a zip on your PC with versions, the setup check and recent
  errors - user names replaced by `<user>`, no audio, no project contents. You decide whether to share it.

## Honesty

fl-headless says what it actually did and measured. An edit counts as done only when it was checked; if it couldn't
be checked, you're told (`verified: null`) instead of being told it worked. Guesses are marked as guesses (key,
genre, tone words), an unreachable loudness target is reported instead of forced, and when only your ears can
decide, Claude says so - and offers words or a picture instead.

## Troubleshooting

Start with **`fl_watchdog`** (or *"is everything OK?"*): it checks FL, popups, the bridge, the open project and disk
space, and fixes what's safe (`heal=true`). **`flh_setup`** re-checks the whole setup with next steps.

- **FL doesn't answer (`fl_ping` fails).** Ask for `fl_bridge_setup` status. The script must be picked on a MIDI
  input with *Controller type* `fl-headless`. After an update run `fl_bridge_setup(action="update_script")` and
  restart FL.
- **FL isn't found.** Set `FLH_FL_EXE` to the full path of `FL64.exe` in the config's `env`.
- **Wrong FL user data folder** (e.g. an old OneDrive copy). `flh_setup` shows which one is used and why; set the
  right one with `flh_setup(action="apply", user_data="...")`.
- **`fl_listen` hears nothing.** It records the Windows default playback device. Make FL play through that device
  (FL Studio ASIO or the primary sound driver). An audio interface's own ASIO driver bypasses Windows and can't be
  recorded this way.
- **"FL is busy (job ...)".** A render or recording is running; `fl_job` waits for it.
- **A call answered with `{"job": ...}`.** Any tool call still running after 45 s (a long render, a big project's
  reload) keeps running in the background instead of being cut off by the client; `fl_job` returns its result.
  Meanwhile only read-only tools run beside it. `FLH_CALL_SOFT_S` changes the 45 s (0 turns this off).
- **FL seems stuck.** A popup is probably waiting. `fl_watchdog` lists it; answer it in FL or let Claude answer.
- **A second FL window appeared.** Don't open `.flp` files from Explorer while Claude works; close the extra copy.
- **Tools don't show up in Claude.** Fully quit Claude Desktop from the tray and reopen it; check the `fl-headless`
  entry and the Python path in `claude_desktop_config.json`.
- **After an FL update.** Run `fl_selftest`.
- **Something failed.** Run **`flh_bug_report`** and attach the zip to a GitHub issue with what you did.

## FAQ

**Which FL editions work?** All of them. Editions bundle different plugins; fl-headless only uses what yours owns
(plus free plugins like FLEX and Splice INSTRUMENT / LABS). A bare Fruity Edition still gets full songs (FL Keys,
BooBass, Plucked!, DX10, 3x Osc, FL's samples); higher editions get Sytrus (Producer), Harmless (Signature), Harmor,
Sakura, PoiZone, Sawer, Toxic Biohazard and Transistor Bass (All Plugins) where they fit the genre - never a demo. Fruity Edition can't place audio clips in the playlist,
so audio-clip tools don't apply there.

**What about trial plugins?** A project saved with a demo plugin shows FL's warning. fl-headless never picks
plugins you don't own, `fl_doctor` flags them, and when ownership is unknown Claude asks you and remembers the answer.

**Which FL versions?** FL 20.8 or later should work (it needs FL's MIDI scripting). It's developed and tested on
FL Studio 2026 only so far; run `fl_selftest` on older versions and tell us how it went.

**macOS?** Not supported for driving FL. The file and audio tools run on macOS and Linux.

**Does it need the internet?** No. Only FL manual lookups go online (to image-line.com), and after
`flh_handbook_build` even those work offline.

**How is this different from FL's built-in Gopher?** They work well side by side: Gopher for quick help inside FL,
fl-headless when you want Claude to do bigger jobs on your projects - writing, arranging, mixing, finishing.

**Does it take over my screen?** No. It never moves your mouse. It looks at FL only through pictures of FL's own
window (`fl_snapshot`).

## Contributing

Found a security problem? Please report it privately, see [SECURITY.md](SECURITY.md).

Read [AGENTS.md](AGENTS.md) first: the architecture, the hard safety rules and how to test. The whole test suite runs
on Linux without FL: `python3 tests/run_all.py` (the tests run in parallel, each in its own temp folder that is
cleaned up afterwards; `--changed` runs only what a change reaches). Private test projects in `tests/data/` are
optional: the parts that need them print "skipped" and the rest runs on the public `tests/corpus/`. Anything that can
only be checked on a real Windows PC with FL is marked `# PC-VERIFY` in the code and listed in
[checklists/](checklists/).

Found a bug? Ask Claude to run `flh_bug_report` and attach the zip to a
[new issue](https://github.com/sertifex/fl-headless/issues/new/choose) - the form asks for the rest.

Much of fl-headless's knowledge is data, not code, so you can extend it without touching Python:

| Folder | What it holds |
|---|---|
| `data/genres/` | Genre grammars: tempo, keys, progressions, form, drum patterns, sound roles, mix hints |
| `data/templates/` | What `flh_new_song` builds per genre (tempo, key, kit, channels and their sounds) |
| `data/kits/` | Drum kit styles |
| `data/mixeng/`, `data/vocal/` | Mix-engineer genre targets and intents; vocal chains per style, comp and balance rules |
| `data/plugin_maps/` | Stock plugin controls with real units (send maps you learned with `fl_plugin_map_learn`) |
| `data/recipes/` | One-call workflows |
| `data/access/` | Plain-language glossary, description wording, hearing rules, FL keyboard shortcuts |
| `data/handbook/cards/` | Knowledge cards: shortcuts, menus, plugins, workflows (mark unconfirmed facts `verify`) |
| `data/handbook/quirks.json` | FL quirks and the versions they were seen on |
| `data/adjectives.json`, `data/instrument_ranges.json`, `data/platforms.json` | Plain-English adjustments, instrument ranges, streaming loudness |

Your own genres, templates, kits and recipes can also live in `%LOCALAPPDATA%\fl-headless\` (`genres\`,
`templates\`, `kits\`, `recipes\`); a file there with the same name as a shipped one wins. If you make something
others would enjoy, send it as a pull request into `data/`.

Keep everything universal: no personal names, paths, songs or taste in code, defaults or examples.

## License

fl-headless is free and open source: the code is under the **GNU GPL v3 or later**, see [LICENSE](LICENSE) and
[NOTICE.md](NOTICE.md). Use it for anything, including selling the music you make with it. If you share a changed
version, it has to stay free and open with its source.

The **name "fl-headless" and its logo** are not covered by the GPL: they're only for use with fl-headless itself
(a fork needs its own name and logo; see [NOTICE.md](NOTICE.md) and
[data/theme/LICENSE.md](data/theme/LICENSE.md)). The theme's colors are GPL like the code.

Like it? Donations keep it going: [Ko-fi](https://ko-fi.com/F3B5292D61).
Made by [sertifex](https://github.com/sertifex). Not affiliated with Image-Line; FL Studio is a trademark of Image-Line.
