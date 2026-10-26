# Security

fl-headless runs on your own PC with your permissions, so security problems matter. Thank you for reporting them
privately.

## Reporting a problem

Please **don't open a public issue** for a security problem. Use GitHub's private report instead: on this
repository, **Security > Report a vulnerability** (GitHub's "private vulnerability reporting"). Include what you did,
what happened, the fl-headless version (`fl_status` or the release you downloaded) and, if you can, a minimal
example. Don't attach your own songs or anything personal.

You'll get an answer as soon as the maintainer can (this is a free, one-person project, so please allow a few days).
Fixes ship as a new release; the changelog credits you unless you'd rather not be named.

## What counts

- fl-headless writing, moving or deleting files outside the folders you allowed (`FLH_ROOTS`), its own state folder
  or FL's settings folder, or deleting anything other than to the Recycle Bin
- running code it got from a project file, a MIDI/audio file, a preset, a web page or a chat message
- sending anything about you over the network (its only network use is FL's manual from image-line.com and the
  weekly update look at this project's releases page)
- the installer (`install.bat` / `install.py`) or the extension changing more than it says
- a bug report zip (`flh_bug_report`) that still contains personal data after scrubbing

## Supported versions

Only the latest release gets fixes. Updating is described in the README.
