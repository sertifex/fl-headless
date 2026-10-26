# flh_setup: checks to run on a Windows PC with FL Studio

## FL edition (first run)
1. Fresh state, no `fl_edition` anywhere: `flh_setup()` - `next_steps` has ONE edition question and
   `fl_edition.ask.options` lists the four editions (+ "unknown = not sure"). If FL's registry names the edition,
   `fl_edition.registry_hint` shows it (PC-VERIFY: which FL versions write it, and where).
2. Answer it: `flh_setup(action='apply', fl_edition='<yours>')` - `done` says what the edition includes beyond Fruity
   and `fl_edition.unlocks` lists it; compare with Image-Line's edition chart for your FL version (entries marked
   "verify" in flh_catalog.NATIVE_MIN: Transistor Bass, Kepler, DirectWave, Vocodex, Emphasizer ...). Note any
   plugin FL shows as a demo that the table says your edition has (or the other way round).
3. `flh_setup(action='apply', fl_edition='unknown')`: the question isn't asked again; new songs use only
   every-edition plugins.
4. Profile lists: `flh_profile(set={'owned_plugins': ['Sytrus'], 'trial_plugins': ['Gross Beat']})` with no
   edition stored: `flh_setup()` shows `fl_edition.edition = producer`, `source = plugins`, no question.

## Guided checklist (first run)
1. Fresh PC (no edition, no profile, FL closed): `flh_setup()` - `steps` has 7 entries in order (FL Studio, Song
   folders, Connection to FL, FL edition, fl-headless look, Free extras, Ready); each `needs you` step has ONE plain
   `next` (no tool names) and, where Claude can act, `do` / `auto`. Read them as a first-time user would: is each one
   clear without knowing fl-headless? Note anything confusing.
2. `flh_welcome()`: `setup` shows the same 7 steps as one line each and `next` the one action.
3. `flh_setup(action="apply", fl_edition="<yours>")` with FL closed: `done` lists the edition, the profile, the
   script put into FL (only when it was missing), the theme preset saved and "made fl-headless FL's theme";
   `switch_back` is one line. Steps 2, 4, 5 are `done`.
4. Connection step: with loopMIDI ports + FL's MIDI input set to fl-headless and FL closed, step 3 says "Set up: I
   connect when FL is open"; open FL: "Connected"; with a popup blocking FL: `needs you` with `do: fl_watchdog()`.
5. Free extras (step 6): the counts match what you have (Splice INSTRUMENT / LABS packs, FLEX presets, BBC SO
   Discover, other plugins). Always `optional`. Right after the server starts on a PC with many packs (first scan
   still running): step 6 says "Still scanning your sounds for the first time (N%)" and `flh_setup()` answers at
   once instead of waiting for the scan. Without BBC SO Discover installed, `fl_sound_catalog(kind='strings')` lists
   no "BBC ..." entries.

## fl-headless's FL theme

FL 20.x (no Options > Theme yet): step 5 is `optional` ("FL's themes need FL Studio 21 or newer") and setup writes no
theme files (PC-VERIFY on an FL 20.8 install that themes really came with FL 21).

The theme files ship in `data/theme/` (`fl-headless.flstheme`, `fl-headless background.jpg`, `thmfl-headless.png`).
Setup copies them into FL's USER themes folder (never FL's install) and always makes the theme FL's theme once -
after saving the user's current theme as the visible preset `My theme before fl-headless.flstheme` (+ a swatch
thumbnail `thmMy theme before fl-headless.png`) in the same folder. There's no opt-out; switching back is one pick.

1. **Folder found.** `flh_setup()` and look at `theme`: `folder` is the Themes folder FL really uses (on the test PC
   `Documents\Image-Line\FL Studio\FL Studio\Settings\Themes`, not a stale OneDrive copy), `why` names the newest
   `__current.flstheme`. Compare with the folder FL's Options > Theme > Save preset writes to.
2. **FL starts with the theme (PC-VERIFY: FL loads `__current.flstheme` at start).** Close FL. Run
   `flh_setup(action="apply")`. Expect `done` to list "saved your current theme as the preset 'My theme before
   fl-headless' (with a thumbnail)" and "made fl-headless FL's theme". Start FL: dark neutral UI with clay-orange
   accents.
3. **The backup preset is listed (PC-VERIFY).** FL's *Options > Theme* lists **My theme before fl-headless** next to
   your other presets, with its thumbnail (a swatch drawn from the theme's colors: window gray, accent line, step
   buttons, notes, meter - check it looks roughly like your old theme and isn't broken / black). Pick it: your old
   look comes back exactly (including its background picture, if it had one). Pick fl-headless again.
   Note whether FL also lists the old dev-build backup `__before fl-headless` (it's kept, never written any more).
4. **Background picture (PC-VERIFY: relative `BackPicFilename` works).** The faint logo background shows behind the
   windows (the theme names `fl-headless background.jpg`, next to the theme file). If it's missing, look at
   Options > Theme > background settings and note what FL expects.
5. **Thumbnail.** Options > Theme lists "fl-headless" with its thumbnail (`thmfl-headless.png`).
6. **Signature match (PC-VERIFY).** With fl-headless picked in FL, close FL, then `flh_setup()`: `theme.active` is
   true (FL's own rewrite of `__current.flstheme` keeps the same values).
7. **FL open at setup.** Reset (`theme` in `%LOCALAPPDATA%\fl-headless\state.json` removed, your own theme picked),
   FL open: `flh_setup(action="apply")`. `__current.flstheme` is not touched, the preset + thumbnail ARE written, the
   note says the one click (*Options > Theme > fl-headless*) and step 5 is `needs you` with that click.
   a) Click it: `flh_setup()` step 5 is `done`.
   b) Instead close FL without clicking (PC-VERIFY: FL rewrites `__current.flstheme` on exit with the SAME values,
      so the waiting switch still happens): `flh_setup(action="apply")` switches it; start FL: fl-headless.
   c) Instead pick some other theme in FL, close FL, `flh_setup(action="apply")`: kept ("while the switch waited").
   d) PC-VERIFY when FL writes `__current.flstheme`: at theme change or only at exit (both are handled; note which).
8. **Next FL start (only once the server calls `_BEFORE_FL_START` hooks).** As 7, then close FL and let fl-headless
   start it (`fl_open` a song with FL closed, or `fl_watchdog(heal=true)`): FL comes up with fl-headless.
9. **Your choice stands.** Pick another theme in FL, close FL, run `flh_setup(action="apply")` again: FL keeps your
   theme (note "you picked another theme").
10. **Restore.** Close FL, `flh_setup(action="theme", restore=true)`, start FL: the theme from before is back. With
    FL open it says which preset to pick instead.
11. **FL's default theme.** On a fresh FL user (no `__current.flstheme` yet), setup saves nothing and step 5 says to
    pick FL's default theme to switch back (PC-VERIFY: that FL with no `__current.flstheme` shows its default theme,
    and what the default preset is called in Options > Theme).
