# flh_handbook + fl_help - PC checklist

Logic is tested offline (`tests/test_handbook.py`, `tests/test_help.py`) with a fake Image-Line site. What needs a
real Windows PC with FL and internet:

1. **Full online build**: `flh_handbook_build()` -> job id + `estimate_min`. `fl_job(<id>)` shows progress
   (`fetching manual pages`, done/total). Expect: `complete: true`, `pages_in_toc` in the hundreds or more,
   `failed` 0 (or a handful), `robots_blocked` 0, `manual_version` = the version Image-Line's manual names (e.g.
   `2025`), `size_mb` reported (expect tens of MB at most; report the number). Note the real duration and page count
   so `estimate_min` (1 s/page, ~1500 pages assumed) can be corrected.
2. **Politeness**: while it runs, requests are one at a time, about 1 s apart (more if robots.txt has a
   Crawl-delay). Only `www.image-line.com` is contacted (check with Resource Monitor / a proxy if unsure).
3. **Resume**: start a build, `flh_handbook_build(action="stop")` after ~50 pages (or pull the network), then run
   `flh_handbook_build()` again: `cached` ~ the pages already fetched, only the rest are requested, result complete.
4. **Conditional GETs**: `flh_handbook_build(refresh=true, background=false)` on a finished index: most pages come
   back `not_modified` (304). If `fetched` is ~all pages instead, Image-Line's server ignores ETag/Last-Modified -
   report it (the build still works, just re-downloads).
5. **Search quality** (after the build): `flh_handbook_search` for each and check the top manual hit is the right
   page/section and URL `#anchor` jumps to the section in a browser:
   'sidechain kick to bass', 'render stems', 'step sequencer swing', 'piano roll arpeggiator', 'audio settings
   buffer latency', 'automation clip curves', 'time signature', 'slicex auto slicing', 'edison noise reduction',
   'quantise' (British), 'sidechian' (typo). Cards (`src: fl-headless note`) come first; results JSON stays under
   ~3 KB for k=5.
6. **fl_help uses the index**: `fl_help("how do I sidechain a kick")` -> `source: local manual index (...)`, pages'
   text present, `notes` with the sidechain card. With the network off it still answers. `fl_manual_page
   ("mixer.htm#...")` works offline for stored pages.
7. **Version**: on an FL older than the online manual (e.g. FL 21), the build result has a `note` saying the online
   manual is for a newer FL. If Image-Line hosts archived manuals per version, add the confirmed base URL to
   `data/handbook/sources.json` `by_major` (e.g. `"21": "https://.../"`), rebuild: index key `online-21`, and
   searches on that PC use it. Report the archive URLs if they exist.
8. **Local help**: check whether your FL install has local help pages (FL folder `Help`, `System\Help`, or
   elsewhere). If it does, `flh_handbook_build(source="local")` indexes them without network; check that result
   links (online URLs built from the local file names) open the right online pages. If help is elsewhere, set
   `FLH_FL_HELP` to that folder and report the path so the default list can include it.
9. **Cards vs real FL** (the `verify: true` entries): open `flh_handbook_card` for each shortcuts card and try the
   keys marked verify in your FL version; for plugin cards marked verify, check the plugin exists in your edition
   (Image-Line's edition comparison page) - correct `data/handbook/cards/*.json` and drop `verify` where confirmed.
   Especially: F8/F11, Ctrl+P countdown, Ctrl+Z behaviour, piano-roll octave keys, Alt+T marker, Ctrl+L /
   Shift+Ctrl+L routing, stem export "Split mixer tracks" + master-effects option, consolidate/render-to-audio
   wording, stem separation menu (FL 2024+), Fruity Edition audio-clip/recording limits, editions of Luxeverb,
   Distructor, Frequency Shifter/Splitter, Pitch Shifter, Effector, ZGameEditor Visualizer, Fruity Vocoder,
   Fruity Love Philter, Fruity Delay 3/Delay Bank.
10. **Quirks file**: `data/handbook/quirks.json` - re-check each quirk on the current FL build and add that version to
    `verified_on` (they are recorded as `2026` / `2026.1.7`).
