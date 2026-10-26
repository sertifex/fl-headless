# newsong (flh_newsong + the sample side of flh_catalog): PC checklist

Run on Windows with FL Studio 2026 (and once on an older 2024/2025 if available). Work in a folder inside FLH_ROOTS.
Each item: what to run, what to expect. Report the result next to the item number.

## A. Sample catalog (flh_catalog)
1. `fl_sound_catalog(type="sample")` - expect `roots` to list `FL factory` = `<FL install>\Data\Patches\Packs`,
   a few thousand samples, `by_role` with kick/snare/clap/hat/... all non-zero. Took < 3 s the first time,
   `cached: true` the second time. (PC-VERIFY: factory packs path.)
2. FL Options > File settings > Browser extra search folders: add a folder with samples, then
   `fl_sound_catalog(type="sample", refresh=true)` - expect a root with source `FL browser folder`. If not:
   `fl_settings_snapshot`, add the folder in FL, close FL, `fl_settings_diff` -> tell us the key/value name
   (PC-VERIFY: registry value of the extra search folders).
3. `fl_sound_catalog(kind="kick", query="punchy", genre="trap")`, `(kind="808")`, `(kind="hat", query="dusty",
   genre="lofi")`, `(kind="loop", genre="boombap")` - listen to the top 3 of each (fl_play / drag in): do the
   roles and tags (punchy, dusty, dark, bright, long tail) match what you hear? 808 results show `note` - check
   the pitch against FL's sampler (or a tuner) for 3 of them.
4. Only the user data folder FL uses is indexed: with an old OneDrive "Documents\Image-Line" copy present, its
   plugin database / packs must not show up in `fl_sound_catalog()` sources.

## B. flp_load_sample (file level)
5. FL closed or another project open. Copy a project, then
   `flp_load_sample(project, output, sample="<FL>\Data\Patches\Packs\Drums\Kicks\<any>.wav")` (new channel).
   Open it in FL: a new sampler channel named after the sample, the sample loaded, plays on C5 at its pitch.
   (PC-VERIFY: sampler from the audio-clip block; %FLStudioFactoryData% path is found.)
6. Same with `channel=<that channel>` and another sample: the sample is replaced, name/color/routing kept.
7. A project with no sampler but plugin channels, and FL's own **Empty** template copied into FLH_ROOTS
   (no channels at all): `flp_load_sample` works and FL opens the result without an error.
   (PC-VERIFY: first channel inserted before the arrangement events.)

## C. flh_new_song
8. FL open on an untitled project. `flh_new_song(genre="lofi")`. Expect within ~5 s of file work (`file_s`)
   plus FL's open: FL shows the new song (title = the song name), stopped.
   Check in FL:
   - Channel rack: Kick, Snare, Hat, Open Hat, Perc, Bass, Keys, Guitar, Pad, Vinyl with **dark** colors;
     drum channels hold FL factory samples (names as in the result's `channels`).
   - Each channel routed to its own mixer insert (the number in the result). (PC-VERIFY: channel event 104.)
   - Mixer: inserts named + colored, drums routed to "Drums Bus", music to "Music Bus" (not to the master),
     buses to the master; "Vocals Bus", "Reverb" (Fruity Reeverb 2) and "Delay" (Fruity Delay 3) exist;
     master has Fruity Parametric EQ 2 and a limiter last (no second limiter if the template had one).
     (PC-VERIFY: plugin database .fst files load as effects.)
   - Playlist tracks 1-4 named Drums / Bass / Chords / Melody (+ FX/Pad/Texture...) with colors.
     (PC-VERIFY: track name event 239 at the end of the track's group 238 43 239, FL's own order; color byte order
     R,G,B - PC 2026-10-23: FL's save wrote every track's color back as 17 15 13 00 while keeping the names: do
     the colors show at all?)
   - Playlist markers at every section start (Intro, A, B, ...) on the playlist ruler, and still there after Ctrl+S
     (pc_check newsong.playlist_names). (PC-VERIFY: markers 148 + 205 after the arrangement's playlist event and its
     track groups (`_marker_slot`); before the playlist event FL 2026 kept them as the LAST PATTERN's markers.)
   - Tempo 82, song plays: drums, bass, chords, melody in the genre's form; Song info shows genre and title.
   - FL asks nothing odd on load; no "unknown data" / corrupted project warning.
9. Mixer size: count FL 2026's inserts in its Empty template; result `mixer.inserts_free` must match the free
   inserts you see. (PC-VERIFY: last stored track = 'Current'.)
10. `flh_new_song(genre="trap", key="F minor", starter=false)` - empty section blocks on track "Sections" with
    section colors and markers; the 808 channel holds an 808 sample.
11. `flh_new_song(genre="drill-uk")` - the 808 plays **in key**: its notes are written `+transpose` semitones up
    (result note "the 808 sample is pitched ..."); check with the piano roll + by ear.
12. Edition: with `flh_setup(action='apply', fl_edition='fruity')`, `flh_new_song(genre="pop")` must not use
    Sytrus (result says "trial in fruity edition") and FL must show no demo/trial warning when saving.
    With `producer`, Sytrus presets are used where the recipe asks (check one sounds like its name).
12a. Edition tiers (PC-VERIFY: preset globs are guesses at factory names). On an All Plugins PC
    (`fl_edition='all_plugins'`): `flh_new_song` for lofi (melody Sakura), ambient (melody Sakura, pad Harmor),
    synthwave (bass/chords Sawer, melody Toxic Biohazard), hyperpop (chords Toxic Biohazard, melody PoiZone), techno
    (bass Transistor Bass, chords PoiZone), house (bass Transistor Bass, melody Sakura), dnb (bass Harmor). For each
    channel line: a preset name means the glob matched; "(default patch)" + "no <plugin> factory preset named like
    ..." means it didn't - note the real factory preset folder / file names (Plugin presets\Generators\<plugin>)
    so the globs in data/templates can be fixed. Same on a Signature PC: pads / leads use Harmless. FL must show no
    demo warning on save in any edition (`fruity`, `producer`, `signature`).
12b. Edition demos skipped: on a Producer PC (FL installs Harmor / Sakura ... as demos), every line that would have
    used one says "<plugin>: trial in your FL edition (needs All Plugins Edition)" in `[skipped: ...]` and `notes`
    says "not used (trial in producer edition)". Save the song in FL: no demo warning.
12c. Bare Fruity: `fl_edition='fruity'`, LABS / INSTRUMENT and FLEX packs absent (or a fresh FL): every genre's
    channels play a real sound (FL Keys, BooBass, Plucked!, Fruity DX10, 3x Osc, FL's own instruments, FL's samples);
    check 3x Osc's factory presets for "*pad*" / "*lead*" / "*string*" names. FLEX without packs: its default sound
    is used only where the rest of the list is just 3x Osc (PC-VERIFY: FLEX's default sound plays).
13. LABS: with LABS Soft Piano downloaded, `flh_new_song(genre="lofi")` - Keys = INSTRUMENT with Soft Piano,
    playing. (PC-VERIFY: flp_instrument_sound on a fresh INSTRUMENT from the plugin database.) If it says it fell
    back, report the message.
13a. INSTRUMENT packs not downloaded (2026-10-23: 'LABS - Glass Piano' had only presets and played silent):
    `flp_instrument_sounds()` -> each pack has `downloaded` and `content_mb` (MB besides its Presets folder).
    PC-VERIFY `_instrument_pack_content`: a pack that plays shows downloaded=true and a pack you never downloaded
    shows false; if a playing pack says false, write down where its sound files are (the check's evidence `folder`).
    `flp_instrument_sound` with a not-downloaded pack writes the channel and says so (`pack_downloaded: false`,
    warning). pc_check newsong.instrument_convert picks a downloaded pack first and skips (not fails) on silence
    when the pack looks not downloaded.
13b. pc_check newsong.fpc_gm_keys: every GM key is judged against what played just before it (a soft, long open hat
    is a sound); `soft_pads` lists pads far under the kick. Listen: 36 kick, 38 snare, 42 closed hat, 46 open hat.
14. FLEX: on a PC without LABS/Sytrus presets matching, a FLEX channel is made: does FLEX play a sound before a
    preset is picked? (PC-VERIFY.) Is the preset hint findable in FLEX's browser?
14a. FLEX sound quality (2026-10-22, PC-VERIFY; data/flex/quality.json, template "character" words). With many
    FLEX packs installed (Core, Essential, General Midi Library, store packs):
    - `flh_call("flex_list", {"kind": "keys", "limit": 30})`: Core / Essential presets on top, every
      'General Midi Library' preset at the bottom (`last_resort: true`, `tier: 0`). Check the real pack names:
      any GM-style pack not called 'General Midi Library' (e.g. a 'GM ...' pack) or a premium pack not called
      'Core ...' / 'Essential ...'? Report the names so the `packs` globs in quality.json can be fixed.
    - With `flh_profile(set={"prefer_flex": true})`: `flh_new_song` for lofi, jazzhop, boombap, trap, gospel, house,
      rnb, cinematic. No channel line may name a 'General Midi Library' preset. **Listen** (fl_preview /
      play): lofi Keys = a felt / rhodes / soft piano (not 'Electric Grand Piano'), jazzhop = rhodes + upright bass,
      gospel Organ = a B3-style organ (not 'Organ 1') + choir pad, trap Bells = dark bells / plucks, house Stabs =
      a stab / chord sound, boombap Keys = dusty keys. Report any part that sounds wrong-kind or cheap, with the
      preset name (the fix is a word in the genre's template `character` or quality.json).
    - Without prefer_flex: LABS (if downloaded) still wins piano / strings / choir / guitar; FLEX Core wins over
      Sytrus / FL Keys; a Sytrus / Harmless choice whose guessed preset names miss is passed over (said in
      `skipped`) for the next real sound instead of its init patch.
    - `vibe="dark"` on trap: the picks move toward 'dark' presets.
    - fl_sound_catalog(kind="piano"): LABS / FLEX Core above the General MIDI presets.
14b. FLEX packs not downloaded / collection scope (2026-10-23, PC-VERIFY). A FLEX store pack that is listed but whose
    content isn't on the PC (e.g. a free pack never downloaded in FLEX's browser):
    - `fl_sound_catalog(query="lead", kind="lead")`: its presets come last with `installed: false`,
      `may_load_silent: true`; `flh_call("flex_list", {"kind": "lead"})` leaves them out (`unavailable_packs` names it).
      If a pack that plays here is flagged, or one that loads silent is not: report the pack's `.flexpack` size and
      the matching entry of FLEX's `cache.json` (which field says "not downloaded" - flh_flex `_store_installed`).
    - `flh_new_song("synthwave")` with that pack present: no channel uses it; `skipped` says it was left out.
    - The catalog and `flp_flex_preset` give the same kind for one preset (e.g. a Core Synths preset is 'synth' in both).
    - With a collection active (`flh_profile`) whose style is e.g. "piano-led, sound real": `flh_new_song("synthwave")`
      -> result `collection: null`, journal `collection: ""`, no 'Piano Pickup' patterns, ear candy synthetic.
      `flh_new_song(..., collection=true)` -> the song is listed in the profile's `songs` and follows its style.
15. FL's "Basic" template as FLH_BLANK (has Kick/Clap/Hat/Snare samplers): the recipe reuses those channels for
    drums (no duplicate drum channels); any left over are listed in `notes`.
16. `variations=2`: three beat variations side by side; `flp_compose(what='pick', variation='B')` works after.
17. Never overwrites: run the same call twice - the second song is "<name> 2".
18. Timing: `steps_s` - report `sounds_search` (first run with cold feature cache and a warm one).

## D. Templates, icons, welcome
19. `flh_save_template(project, name="My Lofi")` then `flh_new_song(template="My Lofi", bpm=78)`: same channels,
    sounds, mixer and routing, no old notes, a new starter beat, tempo 78.
20. `fl_templates=true`: if FL's `<user data>\Projects\Templates` is allowed for writing, FL's File > New from
    template lists "My Lofi" and it opens. (PC-VERIFY: folder FL scans for user templates.)
21. Icons: `flh_icon_probe(output="<root>\icon probe.flp", start=0, count=120)`, open it, `fl_snapshot` the
    channel rack (scroll), write the numbers you can name into `data/templates/_icons.json` (`map`, `verified`).
22. `flh_welcome()` on a fresh state: short, plain, examples fit what's installed (LABS/FLEX/mic), the second
    call says `first_time: false`.
23. **Pitched sampler offset (808)**: `flh_new_song("trap", starter=False)` - the journal has
    `channels.<808>.transpose` (e.g. +30 for an F#1 808) before any note exists. Then `flp_compose(what="bass")`:
    in FL the 808 plays the bassline in the song's key (not 2.5 octaves low), and `flp_score` lists the 808 around
    F1-F3. Swap the sample with `flp_load_sample(channel=<808>, sample=<an A1 808>)`: the result's `tuning` says
    A1 / offset +27 and the existing notes still sound at the same pitches.
    fl_pc_check `newsong.808_pitch` / `newsong.slide_glide`: the evidence now carries `peak_dbfs`, `voiced_all`,
    `onset_s`, `spread_semitones` (and `band_db` when the reading is an octave away). Expect most frames voiced even
    at a low Windows volume; an octave reading is judged by the spectrum (tracker error -> pass; really an octave
    off -> fail naming the sample offset or the plugin preset). If the 808 is a plugin (`plugin` set, offset 0) and it
    reads ~1 semitone off, listen: the preset itself may be tuned off the written note.
24. `flp_load_sample` without `channel`: the new sampler channel's "Mixer track" in FL's channel settings is
    the Master (0), not the copied channel's insert, and no effect of that insert is heard. (PC-VERIFY: event
    104 = 0 routes to the Master.)
25. `flh_save_template(name=X, fl_templates=true)` when `<user data>\Projects\Templates\X\X.flp` exists and was
    not made by fl-headless: that file is untouched, the copy goes to `X 2`. A second save of the same name is
    refused unless `overwrite=true` (then a dated backup stays in the template folder).
26. `flh_new_song(genre="trap")` right after starting FL, while its title bar still reads only "FL Studio <ver>" (no
    project name): the song still has its starter beat (built in fl-headless's scratch folder, which FL never has
    open). If any step fails (starter, arrangement, opening in FL), the result starts with `ok: false` and a
    `warning` list that says what to run next.

## E. Speed
`fl_pc_check(areas=["speed"])` times the waits the user feels, on a sandbox lofi song: build + open (target under
2 minutes), a mixer + channel-name change (live: under 3 s, no reload), a note change merged into one reload
(under 15 s), adding a sample (under 30 s) and fl_preview of the whole song (under 30 s). A fail names the slow
step; `build_steps` in the evidence and `fl_timings()` show where the seconds went.

## F. Sound shaping
`fl_pc_check(areas=["sound"])`: a sandbox song's keys get `fl_sound_shape(words="dark dreamy", amount="heavy")` -
instrument controls found by name, Parametric EQ 2 / Reeverb 2 / Delay 3 added on its mixer track in one edit, the
channel measured alone before and after (darker achieved), the fader takes the level difference, then undo puts the
knobs and fader back and removes the added effects. PC-VERIFY: Stereo Enhancer's separation sign (negative = wider?).

## G. Level balance from one export, pending balance (2026-10-22, PC-VERIFY)
1. With 'Split mixer tracks' on in FL's export window (see checklists/core.md "Render cache"), `flh_new_song("trap")`
   -> the balance news says "levels balanced in N s (1 export(s))"; `flp_balance(project, background=false)` ->
   `renders: 1`, `how` names the split export, every part measured (11-channel song: 1 export instead of 11, well
   under the old ~67 s). A 2nd `flp_balance` right after -> `renders: 0` (stems reused, faders read from the file).
2. Without split: the same as before - each part alone (`notes`: "one-pass stems unavailable (...)"), one export per
   part; a 2nd run with nothing changed -> `renders: 0` (each part's solo render reused).
3. Balance targets: compare the parts' levels after a split balance with the old per-part way on one song: within
   ~1 LU (split stems are measured before bus / master effects).
4. `flh_new_song("lofi", open=false)` -> `levels: "balance pending..."`, the journal has `balance_pending`. Later
   `fl_open(project)` -> the balance runs once in the background (news "levels balanced..."), the journal's
   `balance_pending` is gone and `balanced` has the time; a 2nd fl_open doesn't balance again. Sandbox songs of
   fl_pc_check never get it.

## Organ pick (2026-10-23: a gospel song got 'H Electronic Organ Buzz')
`flh_new_song('gospel')` (and boombap's organ keys) with your FLEX packs: the Organ channel is a B3 / drawbar /
tonewheel / Hammond / gospel preset when one is installed, else a church organ, else another organ - never a buzzy
'Electronic' / reed / Farfisa / transistor organ unless the part asks for one. # PC-VERIFY: listen - the pick sounds
like a gospel organ (data/flex/quality.json organ words; data/templates/gospel.json Organ character).

## Silent sound swap (2026-10-23: FLEX 'Haunted Church Organ' played nothing, 130 notes)
1. A song whose melodic part has notes but a FLEX preset that plays nothing (e.g. a pack whose content is missing):
   after the background balance the news says "<Part>: '<preset>' made no sound on this PC; swapped to '<next>'",
   the channel keeps its name/colour/notes and now sounds (fl_preview channels=[ch] not silent), the journal's
   channels.<ch> has the new pack/preset, and only that part was exported again (renders = parts + swaps).
   # PC-VERIFY: the in-place swap (flp_load_plugin + reload) while FL has the song open, during the balance.
2. `flh_call('silent_sounds')` lists `FLEX|<pack>|<preset>`; a new song of the same genre doesn't pick it again;
   `silent_sounds(action='clear')` forgets it.
3. Three sounds silent in a row on one channel from different packs -> the note blames the channel (insert /
   routing), nothing is remembered as silent.
