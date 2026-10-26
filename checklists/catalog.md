# catalog (flh_catalog: fl_ab, sample walk): PC checklist

1. **fl_ab content hash** (PC-VERIFY in `flh_catalog.VOLATILE_EVENTS`): open a project in FL, Ctrl+S without
   changing anything, then compare `_content()` before/after (or run `fl_ab(project, version=-1)`, toggle to A and
   back, `fl_ab(back=true)`): an untouched [B] is recycled. Then change only a mixer fader in the [B] and toggle:
   the [B] is kept (set aside as `[B edited ...]`), never overwritten.
2. A big sample library (> 30k files in one root): `fl_sound_catalog(type="sample", refresh=true)` lists
   `roots[].not_indexed` with the packs left out (alphabetically last ones).
4. First-run scan progress (old hard drive, or a big sample drive with the cache deleted: remove
   `%LOCALAPPDATA%\fl-headless\samples.json` and `catalog.json`): start the server and right away run
   `flh_new_song(...)` as a background job: `fl_job` shows 'building the sample index (n%, ~N s left)' rising, not a
   frozen job. A `fl_sound_catalog(kind='kick')` during the scan answers within ~10 s with `index_note` 'building the
   sample index (n%...): these results come from the part indexed so far'; the same call after the scan has no
   note and more results. The scan runs once (not twice when the start-up warm-up and a tool call overlap: one
   `samples.json` write, check its date). The estimate is rough on the first folders, then within ~2x.

## fl_audition ranking and previews (2026-10-23, PC-VERIFY)
1. `fl_audition(want="bright plucky analog sequencer synth, short decay, clean, not bassy", query="pluck")`: the top
   picks have a fast attack (`attack_ms` well under 50) and a high `centroid_hz` (above ~1 kHz); a slow swell / dark
   pad sinks with `why` 'too slow' / 'too dark'. **Listen** to the top 3 previews.
2. `fl_audition(want="bright clean analog square lead", query="lead")`: no 'DIST ...' / distorted guitar preset first.
3. `fl_audition(want="warm strings", query="Strings")`: sample-based channel presets are listed in `skipped`, the
   rest are auditioned (no error for the whole run).
4. `previews` is under `%LOCALAPPDATA%\fl-headless\audition\previews` (never a song / collection folder); a second
   audition replaces the first run's FLACs. An old `<first FLH_ROOTS folder>\_audition` from earlier versions goes
   to the Recycle Bin once (`old_previews`).
