# Contributing to fl-headless

Found a security problem? Please report it privately, see [SECURITY.md](SECURITY.md).

Read [AGENTS.md](AGENTS.md) first: the architecture, the hard safety rules and how to test. The whole test suite runs
on Linux without FL: `python3 tests/run_all.py` (the tests run in parallel, each in its own temp folder that is
cleaned up afterwards; `--changed` runs only what a change reaches). Private test projects in `tests/data/` are
optional: the parts that need them print "skipped" and the rest runs on the public `tests/corpus/`. Anything that can
only be checked on a real Windows PC with FL is marked `# PC-VERIFY` in the code and listed in
[checklists/](checklists/).

Found a bug? Ask Claude to run `flh_bug_report` and attach the zip to a
[new issue](https://github.com/sertifex/fl-headless/issues/new/choose) - the form asks for the rest.

Much of fl-headless's knowledge is data, not code, so you can extend it without touching Python.

| Folder | What it holds |
|---|---|
| `src/data/genres/` | Genre grammars: tempo, keys, progressions, form, drum patterns, sound roles, mix hints |
| `src/data/templates/` | What `flh_new_song` builds per genre (tempo, key, kit, channels and their sounds) |
| `src/data/kits/` | Drum kit styles |
| `src/data/mixeng/`, `src/data/vocal/` | Mix-engineer genre targets and intents; vocal chains per style, comp and balance rules |
| `src/data/plugin_maps/` | Stock plugin controls with real units (send maps you learned with `fl_plugin_map_learn`) |
| `src/data/recipes/` | One-call workflows |
| `src/data/access/` | Plain-language glossary, description wording, hearing rules, FL keyboard shortcuts |
| `src/data/handbook/cards/` | Knowledge cards: shortcuts, menus, plugins, workflows (mark unconfirmed facts `verify`) |
| `src/data/handbook/quirks.json` | FL quirks and the versions they were seen on |
| `src/data/adjectives.json`, `src/data/instrument_ranges.json`, `src/data/platforms.json` | Plain-English adjustments, instrument ranges, streaming loudness |

Your own genres, templates, kits and recipes can also live in `%LOCALAPPDATA%\fl-headless\` (`genres\`,
`templates\`, `kits\`, `recipes\`); a file there with the same name as a shipped one wins. If you make something
others would enjoy, send it as a pull request into `src/data/`.

Keep everything universal: no personal names, paths, songs or taste in code, defaults or examples.
