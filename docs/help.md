# Troubleshooting and FAQ

[Back to the README](../README.md)

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

