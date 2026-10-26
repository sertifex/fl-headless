# install.bat / install.py on a real Windows PC

1. **Python found, version checked.** On a PC with Python 3.12 on PATH: double-click install.bat → it runs install.py
   (no winget question). With only an old Python (3.8/3.9) installed: the bat says "Python 3.10 or newer was not
   found" and offers winget. No error text from the Python probes (they write to a temp file, no for /f).
2. **winget route** (a test account or VM without Python): answer `y` → winget installs Python 3.12 for the user
   (`%LOCALAPPDATA%\Programs\Python\Python312`), the bat finds it and continues into install.py in the same window.
   Answer `n` → the python.org text and a pause.
3. **numpy**: on a fresh Python, `y` installs numpy; on a Python in Program Files (no write rights) the `--user`
   retry succeeds and the summary shows "installed <version>".
4. **[server] line**: "starts and answers (N tools)". Rename fl_headless_mcp.py temporarily → re-run → the line says
   PROBLEM with the error and how to see it. Rename back.
5. **FL open while installing**: the [fl script] line mentions Options > MIDI settings > Update MIDI scripts.
   Check that button exists with that name in FL 2026 (PC-VERIFY), and that the script then appears.
6. **install_log.txt** in %LOCALAPPDATA%\fl-headless after a run; `flh_bug_report` zip contains install_log.json
   with the user name scrubbed.
7. **--uninstall**: `install.bat --uninstall` → entry gone from claude_desktop_config.json (.bak kept), asked before
   the bridge script folder goes to the Recycle Bin, songs and %LOCALAPPDATA%\fl-headless untouched. Run install.bat
   again → everything back.
8. **Claude Desktop started once, never 'Edit Config'** (fresh account): `%APPDATA%\Claude` exists but has no
   claude_desktop_config.json → the [claude] line says "created ...\Claude\claude_desktop_config.json" (no "add
   this by hand" text) and Claude shows fl-headless's tools after a full quit + reopen. Microsoft Store Claude: the
   file is created under `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude` instead (PC-VERIFY that
   Store Claude reads it there).
9. **User name with accents** (e.g. a test account `José`) and Python installed per user by winget: install.bat
   finds `C:\Users\<accented name>\AppData\Local\Programs\Python\Python312\python.exe` (the probes run with `-X utf8`), no
   "is not recognized" error.

# Claude Desktop extension (fl-headless.mcpb) on a real Windows PC

Build it first (Linux or Windows): `python3 tools/make_mcpb.py` -> `dist/fl-headless-<version>.mcpb` (`--name
fl-headless.mcpb` for the Releases file). Test on a **second Windows account** (or a VM) that has never had
fl-headless, Python or uv: that is the non-technical user's PC. FL Studio installed and started once.

1. **Install from Claude.** Settings > Extensions > Advanced settings > Install Extension... > pick the .mcpb ->
   Claude Desktop shows an install page with the fl-headless logo, name, author, description and the "Windows only"
   note. No command window, no SmartScreen prompt. PC 2026-10-25 (Store-installed Claude 3.6556): double-clicking
   the .mcpb opened nothing (no file association) - the README says Install Extension; the page warns that the
   extension "has not been verified by Anthropic" (unsigned).
   PC 2026-10-25: Claude shows the defaults as `${HOME}/Music` / `${DOCUMENTS}/Image-Line` and saved the second as
   `Documents\Image-Line` (relative): launch.py maps a path starting with Documents / Music / Desktop / Downloads to
   that real folder. An old install.bat entry named `fl-headless` made Claude start the extension with that
   entry's arguments (uv "unrecognized subcommand", neither ran); install.py now registers as `fl-headless-manual`
   and waits for Claude to be quit (a running Claude wrote the uninstalled entry back).
2. **Folder picker.** The install page (or Settings > Extensions > fl-headless > Configure) shows **Song folders**
   with the defaults filled in as real paths (`C:\Users\<you>\Music`, `C:\Users\<you>\Documents\Image-Line`;
   OneDrive Documents: the OneDrive path). PC-VERIFY: the defaults show expanded, not as `${HOME}`; adding a second
   folder with the picker works; the setting is required (the extension can't start with no folder). Optional
   **FL Studio program** file picker is empty.
3. **Python and numpy came from uv.** Enable the extension and wait for it to show as running (the first start can
   take a minute: uv downloads Python and numpy). Then in a terminal: `dir "%APPDATA%\Claude\Claude Extensions"`
   (PC-VERIFY the folder name) -> a `fl-headless` folder (or similar id) with `manifest.json`, `pyproject.toml`,
   `mcpb\launch.py` and a `.venv` folder made by uv; `.venv\Scripts\python.exe -c "import numpy, sys;
   print(sys.version, numpy.__version__)"` prints Python 3.10+ and numpy 1.24+/2.x. `where python` may still find
   nothing - that's the point. Claude's extension log (Settings > Extensions > fl-headless, or `%APPDATA%\Claude\
   logs\mcp-server-fl-headless.log`) shows the server starting; no traceback.
4. **"I'm new to fl-headless - set me up."** In a new chat. Expect: Claude calls flh_welcome / flh_setup and shows
   the 7 steps in plain words. Step 2 says "Songs go in <your Music folder>". Nothing mentions install.bat, MCP or
   config files. Claude asks the FL edition once (step 4).
   - **No virtual MIDI port:** the reply says the one step: Windows 11's MIDI Settings app loopback, or loopMIDI's two
     ports, or your keyboard. The bridge script is already in
     `Documents\Image-Line\FL Studio\Settings\Hardware\fl-headless\device_flheadless.py`.
   - **Windows MIDI Services loopback present, FL closed:** "set it up" writes FL's MIDI settings at once (answer in
     a few seconds, no 45-second wait). Start FL: `fl_ping` answers without touching Options > MIDI settings
     (PC-VERIFY: FL loads the script on the loopback input; the Input row shows Controller type fl-headless).
   - **Loopback present, FL open:** Claude asks before FL closes and reopens once; after "OK" FL restarts with the
     song saved and reopened and step 3 says "Connected".
   - **loopMIDI ports:** the reply gives the one FL click (Options > MIDI settings: pick Flapi Request, Controller
     type fl-headless, same port number for Flapi Response).
5. **Folders are enforced.** Ask Claude to make a new song: it lands in the first song folder. Remove that folder in
   the extension settings, save -> PC-VERIFY Claude restarts the extension by itself (else toggle it off/on);
   `flh_setup` now shows only the remaining folder, and writes elsewhere are refused.
6. **Nothing written into the extension folder** by fl-headless: after a session, the extension folder holds only
   what was installed plus `.venv`, `uv.lock` and `__pycache__` (uv / Python's own); notes, backups, sounds and the
   mailbox are in `%LOCALAPPDATA%\fl-headless`.
7. **Old manual install too** (the main account, where install.bat ran before): with both present, step 1 of
   `flh_setup` says fl-headless is installed twice and to run `install.bat --uninstall`. After that and a Claude
   restart, only the extension's tools are there.
8. **Update.** Build a version with a bumped SERVER version, double-click it -> Claude offers to update; after it,
   "set me up" says it updated the script in FL (step 3), and the song folders setting is kept.
9. **Uninstall.** Settings > Extensions > fl-headless > Uninstall (PC-VERIFY the wording). The tools are gone from
   Claude; `%LOCALAPPDATA%\fl-headless` (notes, backups) and the bridge script in FL's Hardware folder stay (delete
   the `fl-headless` folder in `Settings\Hardware` by hand if wanted); your songs are untouched.
