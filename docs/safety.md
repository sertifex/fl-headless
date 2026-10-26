# Safety, privacy and honesty

[Back to the README](../README.md)

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

