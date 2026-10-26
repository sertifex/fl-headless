# flh_flex - PC checks (Windows + FL Studio with FLEX)

1. `flh_call("flex_list")`: `packs` and `total` match what FLEX's browser lists (factory packs plus any free/store
   packs you downloaded); a few preset names read right (no cut-off or garbled names).
2. `flex_list(kind="piano")` returns keys presets (recipe kind names map to FLEX kinds: piano/chords -> keys,
   horns -> brass, flute -> woodwind, bell -> bells).
3. PC-VERIFY `flex_fst`: `flp_flex_preset(project, output, preset="<a pad you own>")`, open the song: FLEX shows that
   preset's name and plays it (not FLEX's default sound).
4. `fl_pc_check` `newsong.flex_default`: passes; its evidence says whether the channel played a preset fl-headless
   picked or FLEX's default sound.
5. First FLEX index on a slow disk (delete `%LOCALAPPDATA%\fl-headless\flex_index.json`): `flh_call('flex_list')`
   while another call builds it shows `index_note` 'building the FLEX preset index (n%...)' with a partial total;
   afterwards no note and the full total, and a second server start reads every pack from the cache.
