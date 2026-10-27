# Installing fl-headless

[Back to the README](../README.md)

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

- **loopMIDI** (works on Windows 10 and 11). Step by step below.
- **Windows 11 without extra apps:** create the default loopback in the Windows *MIDI Settings* app, then ask Claude
  to *"run fl_bridge_setup use_loopback"*. It sets FL's MIDI settings for you; with FL open it asks before
  restarting FL once (`restart_fl=true`).
- **No extra app at all, with a MIDI keyboard:** ask Claude to *"run fl_bridge_setup use_input"* with your
  keyboard's name. FL runs the bridge script on the keyboard's input (its notes still play) and everything goes
  through a file mailbox; `undo_input` puts the keyboard's old setting back.

#### loopMIDI, step by step

loopMIDI makes two virtual MIDI cables. One carries Claude's requests **into** FL, the other carries FL's answers
**back** to Claude. The names say which way they go:

| Port name | Direction | Where it goes in FL's MIDI settings |
|---|---|---|
| `Flapi Request` | Claude -> FL | **Input** list (FL *receives* requests) |
| `Flapi Response` | FL -> Claude | **Output** list (FL *sends* answers) |

The most common mistake is swapping them. Request goes in Input, Response goes in Output.

**1. Make the two ports in loopMIDI**

1. Download loopMIDI from [tobias-erichsen.de](https://www.tobias-erichsen.de/software/loopmidi.html), install it
   and open it.
2. In the *New port-name* box at the bottom, type `Flapi Request` exactly (capital F and R, one space) and click
   **+**.
3. Type `Flapi Response` and click **+** again. Both now show in the list.
4. So the ports exist every time you start your PC: right-click loopMIDI's icon in the taskbar tray and tick
   **Autostart loopMIDI**. loopMIDI has to be running *before* FL starts.

**2. Install fl-headless's FL script**

Ask Claude *"I'm new to fl-headless - set me up"*. It copies the script into FL's settings, so FL lists
`fl-headless` as a controller type. Restart FL after this step, or FL won't see the new ports or the script.

**3. Set FL's MIDI settings** (*Options > MIDI settings*, or F10)

Output (top list):

1. Click `Flapi Response`.
2. Set its **Port** number to a free number, for example `10`. Don't use one your keyboard or controller already
   uses.

Input (bottom list):

1. Click `Flapi Request`.
2. Turn on **Enable** (the switch lights up).
3. Set **Controller type** to `fl-headless`.
4. Set **Port** to the **same number** as the output (`10` in this example).

Leave `Flapi Response` *disabled* in the Input list, and don't pick a controller type for it.

**4. Check it**

Ask Claude *"check the FL connection"* (`fl_bridge_setup status`). It should say FL answers.

**If it doesn't work**

| What you see | What to do |
|---|---|
| The Flapi ports aren't in FL's lists | Start loopMIDI first, then restart FL. |
| `fl-headless` isn't in *Controller type* | Run "set me up" again, then restart FL. |
| "no bridge MIDI ports" / FL doesn't answer | Check that Request is the enabled **input** with controller type `fl-headless`, Response is the **output**, and both have the same port number. |
| It worked, then stopped after a reboot | loopMIDI wasn't running when FL started: tick Autostart (step 1.4) and restart FL. |
| Two different port names | Spelling matters: `Flapi Request` / `Flapi Response`. To use other names, set `FLH_PORT_OUT` (into FL) and `FLH_PORT_IN` (out of FL); see *Manual config* below. |

### Manual config (Option B)

`claude_desktop_config.json`, under `"mcpServers"`:

```json
"fl-headless-manual": {
  "command": "C:\\Path\\To\\python.exe",
  "args": ["C:\\Users\\you\\Documents\\fl-headless\\fl_headless_launch.py"],
  "env": { "FLH_ROOTS": "C:\\Users\\you\\Music;D:\\FL Projects" }
}
```

(Without the launcher, point `args` at `src\fl_headless_mcp.py`.) Optional `env` settings:

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
once. Some updates need a Claude restart; the [changelog](../CHANGELOG.md) says so.

### Uninstall

- **Extension:** remove fl-headless in Claude Desktop's *Settings >
  Extensions*.
- **install.bat:** fully quit Claude first, then `install.bat --uninstall` removes fl-headless from Claude's config
  (keeping a `.bak`) and, after asking, moves the bridge script folder in FL to the Recycle Bin. It waits while
  Claude is still running.

Your songs and fl-headless's notes are left alone either way.
