# Changelog

## 2.1.0 - 2026-10-25
- Install: Claude Desktop extension (`fl-headless.mcpb`) - download it from the Releases page, install it in
  Claude (*Settings > Extensions > Advanced settings > Install Extension...*), pick your song folders, then say
  "set me up" in Claude. No command window and no Python
  to install: Claude sets up Python and numpy for the extension itself (uv). `flh_setup` now does the FL side from
  inside Claude: installs or updates the bridge script, connects Windows' MIDI loopback (writing FL's MIDI settings
  directly when FL is closed, or with one confirmed FL restart), and explains the one step when no port exists.
  `use_loopback` with FL closed returns at once. `install.bat` stays for manual / advanced installs: it now registers as
  `fl-headless-manual` (an entry named like the extension made Claude start neither), waits for you to quit Claude
  before changing its config (a running Claude put an uninstalled entry back), and says so when the extension is
  already installed. fl-headless warns when both are installed. A song folder Claude saves as `Documents\...`
  is read as your real Documents folder. Build: `tools/make_mcpb.py`.
- Updates: fl-headless tells you when a newer version is out (`fl_status`, `flh_welcome`, new `flh_updates`). It
  looks at this project's GitHub releases page at most once a week (once a day after a failed look) - a plain
  request, nothing about you is sent - and says how to update for your install (extension or ZIP). Off with
  `flh_updates(enable=false)` or `FLH_UPDATE_CHECK=0`.
- License: the code is GPL-3.0-or-later; the fl-headless name and logo images are reserved for use with fl-headless
  itself (NOTICE.md, data/theme/LICENSE.md).
- **Release bug hunt: project files.**
  - Inserting, deleting or duplicating bars (`flp_arrange`, `flp_compose_insert`) works on imported songs: a note
    pattern that also carries a tempo row (every FL / MIDI import) is split, trimmed and copied like any other, and
    the tempo / controller values in force at a cut are kept, so the rest of the song plays as before. A tempo
    stretch only lengthens clips that span the point.
  - Deleting bars keeps automation clips that run past the cut (their curve trimmed) and audio clips that start
    inside it (moved to the cut; the notes say the sample plays from its start). Automation crossing an insert
    stretches flat.
  - Bars are counted in the song's meter everywhere (written notes, tempo points, markers, bar times), so 3/4 and 6/8
    songs land where you asked. Writers use the template's meter when no `beats_per_bar` is given (3/4 songs no
    longer cut off) and report same-key notes that overlap on a channel.
  - Muted playlist clips are left out of "what the song plays" (as FL does); a copied clip no longer stays muted;
    splitting a pattern keeps its window, mute and track.
  - MIDI files: notes are paired per track, channel and key; notes left hanging are closed at the track's end and
    reported; a cut-off file gives a clear error; a tempo map that comes late is handled; zero-length notes are
    written with their note-off after the note-on.
- **Release bug hunt: the open FL.**
  - Solo previews and stems in the open FL load the song first, then mute; no reload or open happens while those
    mutes are on, they are checked again right before the export, and a failed stems run puts every mute back (or
    says which are left). Automation channels are never muted. A silent solo / stem says which channel and why.
  - Saves only go into the project FL actually shows: never a save meant for another song, and never over a
    same-title twin FL holds an older copy of (when titles match, a save proves which file is open). `fl_ab` and
    idea-capture saves use the same guard.
  - An edit of a project FL holds an older copy of reloads by the proven route (below); every reload gets its own
    tag, compared exactly, and a leftover tag is removed.
  - A file Windows has locked for a moment (antivirus, cloud sync) is retried before giving up: 8 s, longer for a
    big song (1.5 s per MB, up to 45 s).
  - Big songs: a reopen that is still loading is waited for (about twice the song's load time) instead of being
    taken as "FL ignored it" and loaded a second time, and the proven reload waits for a running load to finish
    before it stamps its marker (a 150-channel song's sync: 108 s -> 38 s, and no more "FL holds an older copy"
    after a lock clash).
  - A save FL drops right after a load is asked for once more after 3 s instead of waiting it out (a final after an
    edit: about 17 s faster).
  - Turning a channel into a Splice INSTRUMENT pack copies a real INSTRUMENT state, never a BBC Symphony Orchestra
    or LABS one (those stayed silent). A pack that plays silent that way is noted, and new songs rank it down.
- **Release bug hunt: music and audio tools.**
  - Genres that only have minor progressions (drill UK / NY, metal, techno) asked for in a major key now write in
    the parallel minor and say so ("drill-uk has no major progressions: used C minor") instead of spelling minor
    chords on the major scale.
  - Quantize no longer shrinks notes after the first grid step to one tick; ties at half a grid step round up.
  - `audio_finish` refuses clips under 0.4 s with a clear message, works on quiet files, caps peaks through the
    finalize limiter and says by how much it missed a target.
  - Sample names like `90bpm_Am` are read as 90 BPM.
  - Swing pairs 8ths within the felt beat: 6/8, 9/8 and 12/8 stay straight for 8th swing (with a warning; 16ths
    still swing) and the odd last unit of a 5/8 or 7/8 bar stays straight. `flp_edit_notes` passes the song's meter,
    so its swing follows it too.
  - `flp_analyze_music` on a .mid reads the file's time signature and leaves General MIDI drums (channel 10) out of
    the key and the chords.
  - `flp_compare_midi` matches song notes that play a source note merged as a doubled copy (no more false "new"
    notes).
- **Proven reloads.** Reopening the project that's already open, and the reload after an edit, are proven: a marker
  stamped into the file must be read back by FL (FL's empty template first, waited for), otherwise you get an honest
  error - and nothing ever saves FL's older copy over a newer file (`fl_finalize` included). No early retry while a
  heavy project is still loading; popups left over from an open are closed. New `fl_reload_from_disk`: load the open
  project again after a copy was put over it on disk (FL's unsaved changes are dropped).
- **Live layer fixes.**
  - `fl_open(save_current=false)` never saves and answers No to FL's prompt; no FL save ever goes over a file that
    changed on disk behind FL (remembered across restarts). `fl_open` checks that the switch happened (one retry,
    clear error) and `fl_status` says "switching" instead of showing the old project. Steadier focus before FL's
    Open dialog.
  - Markers are written in FL 2026's own layout (scrambled ones repaired on save); merged reloads are checked by
    content; renders are cached by file content, so a final never reuses a render of another file; edits to the open
    song wait for FL, save, then read.
  - Faders: FL 2026 applies mixer changes after the call, which ran faders to the ends (+5.6 dB or silent); now one
    set on FL's exact fader curve, checked afterwards. Bridge script v7: run
    `fl_bridge_setup(action="update_script")` and restart FL once. The mix engineer reports automated tracks with
    options, undoes moves that land more than 1.5 dB off, never drops the master below -12 dB, waits while FL loads
    and stops if FL closes.
  - Composer and sounds: layer `where` takes lists of ranges, velocity as 0..1, 1..127 or words, layer channels get
    their own mixer inserts; a collection's profile applies only to its own songs; FLEX packs that aren't fully
    downloaded are avoided; new `electro` genre.
- **Faster listening rounds.** `fl_preview(format='mp3')` uses FL's own MP3 export and keeps one small preview per
  song to send (`share_path`); `section` or `start_bar` / `end_bar` cut just those bars from a cached whole-song
  render. `flp_master_tone`: the song's tone curve as a Fruity Parametric EQ 2 on the master, inside the project, so
  every preview, final and stem has it (presets as data, kept in the journal, backup + undo); `fl_finalize` says
  whether the tone is in the render.
- `flp_compare_midi`: compare a song with the MIDI it came from (a rework, cover or arrangement): a bar map per
  region (new intros, cuts, beat shifts, meter changes), source notes the song lost, new notes, repeated
  "one-finger" stand-ins, near-duplicate strikes, timing, velocity and per-section tempo; restore or remove notes,
  safe with patterns placed more than once.
- PC check and sweep clean up after themselves: the sandbox is always recycled once FL has left it (a leftover is
  reported and recycled by the next run); checks and the bench run under a temporary sandbox profile, so their
  finals, previews and stems never land in your collection folders; your song is never mistaken for a sandbox file,
  and a run that started on an empty project ends on one. The drum-hit check finds each hit's real start (speaker
  recordings run late). Also: playlist markers where FL 2026 keeps them, track names in FL's order, the FPC check
  judges hits by peak and rise, INSTRUMENT packs say whether their content is downloaded.
- **Ear candy and transitions.** `flh_fx` makes tempo-exact risers, downlifters, impacts, reverse crashes, sub drops,
  tape stops and noise sweeps; `flp_arrange_song` places them per genre, adds Fruity Filter sweeps (intro, build,
  outro) and purposeful moves (cymbal swell, boom, reverse swell, beds, snare build, delay throw) with amounts.
  Piano-led, cinematic and ambient songs get real instruments instead by default (string swell, timpani / cymbal
  roll, choir breath, piano pickup); the profile's `ear_candy` setting picks; `flp_remove_fx` takes everything it
  generated back out in one reversible batch.
- `flp_build_rise`: an orchestral / piano rise into a climax (a tension ladder onto a Vsus4, the accompaniment
  re-voiced onto it, data in data/rise/) in one reversible batch.
- `flp_save_version`: save a version of a song under a new name, with its own title (FL's window shows titles) and
  journal. Finals check their length and reload a stale FL first.
- `fl_sweep`: a full health check of fl-headless on your PC as one background job (PC check, bench at normal and
  slow speed, error counts) turned into a ranked fix list and compared with the last sweep.
- `flh_usage`: tool usage counts kept on your PC only (never sent anywhere; counts only, no arguments or project
  names; `FLH_NO_USAGE=1` turns it off). Bug reports include the counts.
- Bug round: 63 fixes with tests - project files (meter-aware arrange / automation / song length, no lost backups,
  overlapping edits refused), the open FL (an autosync spin, saves over a stale copy refused, waits scaled, a cache
  lock hang, leaks), music (rise key, mode spelling, finalize level-jump floor, input checks), audio (a short-clip
  tempo crash, float overs, FLAC memory), setup (BBC SO not owned, Claude's config created when missing, accented
  paths, tidy safety, damaged journals / profile / state recovered).
- Instrument parts that render silent swap to the next sound, remembered per PC; gospel avoids eerie organs;
  Recycle Bin moves go piece by piece when Windows refuses a folder.
- Edison takes fail fast with the fix; restore retries and focuses FL's main window before Ctrl+O; `fl_preview`
  waits for a slow PC's title after a load and falls back to the last opened project while FL's title lags. In-FL
  export: one slow dialog no longer sends the following renders to FL's command line; a late Save dialog is waited
  for. Balance skips parts with no notes yet (one render less on a starter beat).
- Arguments: a JSON list sent as text for an argument that takes a list or a text is read as the list.
- **Weak PCs.** One measured speed factor per PC scales every FL wait (fast PCs unchanged); a busy FL gets one
  extension and a "still working (slow PC)" note instead of an error; the first scan of the sample and FLEX indexes
  shows progress with estimates and partial results; the server starts in about 100 ms (was 180; heavy imports load
  when needed). Arrangement never waits 15 s on a closed FL; the FLEX index rescans only when its folders change.
  `fl_bench` has a slow mode (FL limited to 2 cores, always restored); `flh_trace` profiles any tool.
- `fl_flip_sample`: flip a sample like a producer - it finds the hits, says what the sample is (melodic loop, vocal /
  soul phrase, drum loop) and picks the style from that (loop, chops, chipmunk soul, stutter, lofi...), re-pitches
  into the key, keeps tonal chops ringing to the next hit, sits them about 4 dB over the drums from measured levels
  and arranges a new loop on sampler channels with drums and bass.
- **Musical beats.** Hook motifs grow by phrase plans (repeat, sequence, rhythm repeat, answer, cadence), the bass
  locks to the kick, drum grooves stay steady with fills, sections build (sparse intro / verse, full hook,
  contrasting bridge); several candidates are scored and the best is kept. `flp_musicality` scores any song's notes
  the same way (hook, fit, groove, form, what's weak). Sound picks rank better-named presets first.
- `flp_finish_piece`: finish a piece in one call - support layers, the performance, a rule-aware ending predicted
  against the collection's rules before rendering, then the final, in one FL reload with at most one retry. The
  ending prediction simulates the render from the notes; `flp_extend_ending` grows the hold until it passes.
- `fl_beat_from_reference`: "make a beat like this song" in one call - the feel only (tempo, key, the chord loop's
  harmony, drum density, tone); every note is newly written, nothing of the reference is copied. Analysis cached.
- `fl_bench`: time a fixed set of jobs on sandbox songs on your PC (render counts, compare runs).
- Faster server: a fast path in the .flp parser, edits spliced without a full re-parse, cached project info, a
  compiled-code cache for `flh_reload`. Fixes: a journal write race, render temp leftovers, sound-shape and realism
  undo, balance moves verified, atomic writes per writer; FL 2026 knob names for Limiter gate, Multiband high band,
  Stereo Shaper.
- Editions: new songs on a bare FL of any edition (no LABS / INSTRUMENT / FLEX packs) get a real sound on every
  part - every recipe list ends with stock plugins every edition has (FL Keys, BooBass, Plucked!, DX10, 3x Osc, FL's
  own instruments); higher editions get Harmless (Signature) and Harmor / Sakura / PoiZone / Sawer / Toxic Biohazard /
  Transistor Bass (All Plugins) where they fit the genre, only when owned (demos are skipped and said so). Guessed
  preset names that don't match say "default patch". FLEX's default sound (no pack readable) is kept for when only
  3x Osc is left. Boombap / cinematic / jazzhop melodies: LABS first, FLEX right after.
- `flh_setup` edition flow: the edition comes from the profile / stored answer / `FLH_FL_EDITION`, or from the
  profile's owned + trial plugin lists when they pin one edition; otherwise ONE question with the four editions
  (narrowed by those lists, FL's registry hint shown), `apply(fl_edition='unknown')` records "not sure" so it isn't
  asked again, and the result lists what the edition includes beyond Fruity.
- **Guided first run.** `flh_setup()` returns one short checklist in plain words, in order: FL Studio (version,
  edition), your song folders, the connection to FL, your FL edition (the one question), the fl-headless look, free
  extras found (informational) and "ready" (what to ask for first). Each step is done / needs you / optional with
  ONE next action (`do` = the call, `auto` = setup does it); the detailed fields and `next_steps` stay.
  `flh_setup(action='apply')` does everything safe in one call (edition, neutral profile, FL's script when missing,
  the theme) and returns the checklist after it. `flh_welcome` shows the same steps, one line each.
- fl-headless FL theme (dark neutral, clay-orange accents, faint logo background), part of setup (no opt-out):
  `flh_setup(action='apply')` installs it into FL's user themes folder (the one FL really uses - newest
  `__current.flstheme` wins, nested `FL Studio\FL Studio\Settings\Themes` and OneDrive copies handled), first saves
  your current theme as the visible preset **My theme before fl-headless** (with a swatch thumbnail; written once,
  never overwritten), then makes fl-headless FL's theme once. Switch back = pick that preset in *Options > Theme*.
  FL open during setup: files + preset installed, one click in FL (or the switch happens on the next setup with FL
  closed, unless you picked another theme meanwhile). A theme you pick later is never switched back.
  `action='theme'` installs / switches, `restore=true` brings the old theme back (FL closed).
- `fl_sound_shape(channel, words, amount)`: make a sound better or insane from words (warm, dark, bright, airy, wide,
  punchy, soft, gritty, lofi, dreamy, huge, clean, tight, insane): the instrument's own knobs found by name on any
  plugin (stock, FLEX, third party), Sampler channels' envelope / filter / pitch (flh_sampler), and a stock-effect
  chain on its mixer track; measured before/after, level matched, `undo=true`. `knobs='list'` / `knobs={...}` sets
  any plugin's knobs by what they do ('-1 oct', '+20 ms', '30%'); `variations=3` compares versions; `layer=octave`.
- FLEX: every preset of every installed pack by name (1,960 on the test PC), `flp_flex_preset`, `flex_list`; new
  songs pick FLEX presets (first when the profile says prefer: flex).
- New songs balance their levels in the background after opening (each part measured with FL's export, faders set,
  master limiter to about -14 LUFS, ceiling -1 dB).
- `fl_plugin_maps_check` + PC check `mix.map_names`: stock effects' knob names checked against the live FL and
  corrected (14 fixed on the PC, shipped).
- PC check fixes: FL's compressed factory samples measured with numpy numbers left a new song's kick and hats empty
  (now plain numbers); FLAC from the open FL (FL's WAV, encoded losslessly here); a job started inside a job that
  holds FL runs in place (the stems check hung); exports prove which file FL holds when another song has the same
  name (one save instead of a 4-minute wait); stems asked for in the open FL never fall back to closing FL.
- Measured on the PC: new beat 5.6 s (build 0.5 s + open 5.1 s), live mixer/name change 0.02 s, note change merged
  into one reload 7.7 s, new sound 9.7 s, whole-song preview 9.3 s.
- **Speed first.** Edits to the open song no longer reload FL one by one: a burst of edits answers at once and goes
  into FL in ONE save + reload a moment after the last (`in_fl_now` in the next result says so; a reload that fails
  keeps the edits queued and says so). Mixer edits, channel names / colors and a plain tempo go into FL live - no
  file edit, no reload; FL saves them into the file a moment later. `flp_edit` made only of those runs live as one
  undo step. Windows default; `FLH_FAST=0` or `reload_mode='immediate'` restores reload-per-edit.
- New songs build faster: the sound and sample indexes are checked once per build instead of on every search, and
  audio-feature measuring shares one short budget; a background warm-up measures the samples a new song would pick
  (recent genres first) so the build only reads the cache.
- Sound taste: a sample swapped out of a channel is picked lower next time and its replacement higher; say it
  outright with `flh_journal(add={"sound_dislikes": [channel], "sound_likes": [...]})` - every future song follows.
- `fl_pc_check(areas=["speed"])` times the waits: new beat (target < 2 min), live change (< 3 s), note change
  (< 15 s), new sound (< 30 s), fl_preview (< 30 s).
- Drum samples whose names say nothing ('Kick 23') are ranked by how they measure against the genre's drum character
  (data/catalog/drum_character.json: warm / trap / club / band families - sub, tone, decay, attack ranges per drum;
  editable, a user copy wins). A user's own words still count more.
- `audio_collection_report(folder)`: a collection's finals side by side (in the folder and its song folders) -
  loudness, true peak, length, tone, width and the rule checks per song, the medians, and which songs stand out.
- `fl_finalize` says when the render itself limits the final (FL's export set to 16-bit or a low sample rate).
- `flh_reload` now also swaps the dispatcher the running server uses (argument checks, long-call handover, ...):
  updates never need a Claude restart, even for changes to how calls are handled.
- More audio formats everywhere audio is read: AIFF / AIFF-C samples natively (16/24/32-bit, little-endian 'sowt',
  float), and MP3 / M4A (AAC) / WMA through Windows' own Media Foundation - reference songs for fl_reference_match,
  audio_to_midi of a song, mp3 samples in the catalog. Nothing to install.
- Grouped stems work on any project, not only fl-headless songs: channel names in any style ('Vocals_01',
  'HH-closed', 'Bass Drum' = a drum), drum plugins (FPC, Fruity Kick...), and the channel's mixer track name (a
  recorded take on a 'Lead Vocal' track is a vocal); the song journal's roles still win.
- `fl_bridge_setup(action='use_input', port='<your MIDI keyboard>')`: the live tools without loopMIDI or a loopback
  port - FL runs the bridge script on an input it already has (the keyboard still plays), all traffic goes through
  the file mailbox; refuses an input that runs another FL script unless `replace=true`; `undo_input` restores it.
- Projects can be named loosely: `project="Late Rain"` (a song name found in the allowed folders), a path relative
  to an allowed folder or the last opened project, or one without `.flp` resolves to the real file when exactly one
  fits (the result says so under `resolved`); several matches are listed instead of guessing. A relative output
  (`output="Song v2.flp"`) goes next to the project it came from.
- Long calls never get cut off: a tool call still running after 45 s (`FLH_CALL_SOFT_S`) is handed over as a job -
  the client gets `{"job": ...}` at once and `fl_job` returns the result (or error) when it's done; calls that can't
  safely run beside it are refused with what's running, read-only and popup tools still work.
- `fl_pc_check` covers tonight's live features: fl_preview restores mutes, stems in the open FL (each stem really
  holds only its channels - checked by adding them up against the mix), FL's own FLAC export decodes bit exact
  without flac/ffmpeg, a new song's FL instrument plays, and fl_listen's timing (steps_s) for a 2 s passage.
- `fl_render_stems` method auto/in_fl/cli: stems can render in the open FL (each stem's other channels muted live,
  then put back; your own mutes kept) - FL is no longer closed for stems. `auto` uses it once `fl_pc_check`
  (render.stems_in_fl) proved FL's export follows live mutes on the PC; stems that all measure the same are flagged.
- `fl_listen`: the reverb-tail wait ends once the speakers are quiet; `steps_s` shows where the time goes.
- FLAC reading: LPC-coded FLAC (FL's own FLAC export, the flac tool, ffmpeg) now decodes bit exact with numpy
  (~1.5 s for 3 min of 24-bit stereo), so every audio tool takes FLAC finals and stems (audio_analyze, audio_*,
  audio_mix_report folders, fl_review, the sample catalog's measurements); a cut-off FLAC is an error, not a
  short song. flac/ffmpeg are no longer needed.
- `tests/run_all.py`: the suite in parallel, each test in its own removed temp folder.
- Installer: no Python (or too old) -> offers Python 3.12 via winget; numpy install falls back to `--user` and
  ensurepip; starts the server once like Claude will and reports whether it answers; `--uninstall`; the summary is
  kept as install_log.txt and goes into flh_bug_report zips; mentions when FL is open and needs its scripts updated.
- `fl_preview`: the whole song - or only some channels (others muted live, then put back; nothing saved) - through FL's
  own export in the open FL, measured on the PC (lufs, true peak, tone, optional rule check); no pile-up of WAVs.
- Arguments: common other names are accepted (path/project, from_bar/start_bar, to_bar/end_bar, wait/wait_s, ...);
  an ambiguous or doubled one is still refused with the real names.
- `fl_job` says when asking again is worth it (`check_again_s`, from the job's ETA).
- `flh_tidy`: keeps fl-headless's own folder small (old backups, check reports, bug bundles, temp work files,
  leftover preview WAVs) - a dry run with sizes, then `apply=true` to the Recycle Bin, in batches;
  `fl_backups=true` adds FL's own old autosave copies in the songs' Backup folders (the newest 3 per song stay).
- `fl_listen` records a short moment before FL plays: if something else on the PC makes sound (music, a video, a call,
  a monitored mic, a hum) the result says how loud and at what pitch, marks the measurements unreliable when it's
  within 15 dB of the music, and the take's start is found above that background.
- `flp_extend_ending`: a longer, softer ending - the last chord held `seconds` more (the piano rings out), layers
  sounding at the end hold and let go earlier so they fade under it, the clip grows, optional ritardando.
- `flp_smooth_entries`: parts that enter out of nowhere with a big level jump ramp in (first notes' velocity rises
  over a few beats); dry_run lists the jumps.
- `flp_write_notes` (and the song-length setter) no longer fail on projects with more than one playlist clip.
- `fl_finalize(finals=[{name: 'mod', lufs: -20}, {name: 'youtube', lufs: -14}])`: several finals from ONE render, each
  verified and filed as '<song> - FINAL (<name>).flac' (a profile's `finals` sets them for a whole collection);
  `format: 'wav'` makes that version a WAV (16 or 24-bit) for tools that only take WAV, like a game's converter.
- Collection rules: `rules` (or the profile's) check the render - ending length, the last chord's ring, a tail that
  dies to true silence (no hum/noise, not cut off), lead/trail silence, a click on the first note, sudden level
  jumps - reported per rule with times and kept in the journal; `rules_exempt` per song (e.g. a main-menu piece).
  `audio_rule_check` runs the same check on any WAV.
- Stems: `fl_render_stems` writes FLAC by default (24-bit), measures every stem on the PC (lufs, peak, silent - nothing
  to download), names buried stems, and can render grouped stems: `groups='roles'` (drums / bass / music / vocals),
  `'instrumental'` (everything but vocals + the vocals alone) or `[{name, channels}]`. A channel with notes that
  renders silent while playing a LABS/INSTRUMENT pack gets that pack remembered: the catalog marks it
  `may_load_silent`, new songs skip it, and `flp_instrument_sound` warns when it's picked again.
- New songs sound better out of the box: FL's own multisampled instruments (Packs/Instruments, e.g. the recorded
  upright bass) are in the catalog and preferred for bass/guitar in acoustic-leaning genres; drums stay in one kit
  folder when a near-best match exists; thin, clicky kicks lose to full ones in warm genres (measured 'thin'/'warm');
  every sampled channel starts at a level set from its measured loudness, so quiet hats aren't buried.
- `fl_save`: save the project open in FL and say which file it went to (untitled projects are reported, not saved).
- `fl_open` names the project it saved before switching (`saved_first`); `save_current=false` discards its unsaved
  changes instead (only ever for the file FL had open).
- New songs go where the user's FL projects are (the allowed folder - or its 'My Projects' / 'Projects' subfolder -
  holding the most .flp song folders), not simply the first allowed folder; a profile's new_song_folder still wins.
- `flp_write_arrangement` / `flp_write_notes` work on projects made by `flh_new_song` (its empty section blocks are
  replaced by the written sections: clips on alternating tracks, stretched over ringing notes, markers and journal
  sections rewritten). A project whose patterns hold notes is still refused, naming them and pointing to
  `flp_replace_part`.
- `flp_load_sample`: a new sampler channel is routed to a free mixer insert (named after it) instead of the Master;
  `replace_plugin=true` turns a plugin channel into a sampler with the sample, keeping its name, color, insert and notes.
- FL's compressed factory samples (WAV files holding Ogg Vorbis, fmt tag 'Og' - most of FL 2026's Packs, kits and
  demo-project sounds) and .ogg files are read now: `ogg_vorbis_lite.py`, a numpy Vorbis decoder (matches ffmpeg on
  FL's HQ Rock kit and upright bass). Pitch, level, tone and length get measured for them in the sound catalog,
  `flp_load_sample` tuning, reviews and audio edits instead of "not a WAV". Cached features are measured again.

## 2.0.0 - the production release
- First real-PC round fixes: new songs built from FL 2026's empty template now load in FL (its sampler channel has
  no end marker, and new channels copied the arrangement and mixer with it); Plugin database entries (name-only
  references in FL 2026) load as the plugin's own Default preset; `fl_pc_check` stops at once with a clear message
  when FL can't read a test song instead of failing every later check; `fl_job` waits at most 40 s per call.
- Sheet music: `sheet_pages` shows scanned pages/photos/PDF scans system by system (zoomed, bar lines marked) so
  Claude can read them; `sheet_import` takes the reading as ABC notation (or a MusicXML/.mxl export from MuseScore
  etc.), checks every bar (durations, accidentals, key, clef/octave slips, repeats/endings, hand spans), optionally
  compares it with a recording, then writes MIDI and/or the notes into FL. `sheet_diff`, `sheet_explain` (plain words).
- Realism (layers that sound played, not programmed): `flp_realism` shapes strings/choir/brass/winds/pads with
  phrase swells on the library's dynamics/expression controllers, per-family timing, breaths, legato and
  articulation choice; undo/re-run safe; used automatically by flp_perform and flp_compose layers. `fl_room`
  puts acoustic layers in one shared room (hall/chamber/studio) with stage-distance sends, darkening and seating.
- `fl_pc_check`: the automatic real-PC check (quick 26-check run ~25 min, full=true ~35 min) on sandbox copies.

fl-headless grows from "edit my project" into a full studio partner: start a song from one sentence, write and
arrange parts, mix and finish, record vocals, capture ideas, learn FL - and check all of it without needing to hear
or see it. 183 tools, of which Claude sees 45 by default. Read **Breaking changes / upgrade notes** before updating.

### Make music
- `flh_new_song(genre, bpm, key, vibe)`: a ready-to-play song in one call from **26 genre templates** (afrobeats,
  amapiano, ambient, boom bap, cinematic, dancehall, dnb, drill NY/UK, gospel, house, hyperpop, jazzhop, jersey club,
  k-pop, lofi, lofi house, metal, phonk, pop, reggaeton, R&B, rock, synthwave, techno, trap). Channels with real
  sounds (FL's factory samples for drums, your owned instruments for the rest; substitutions said out loud), named
  and colored mixer inserts on Drums / Music / Vocals buses, reverb and delay sends, master EQ + limiter, named
  playlist tracks, section markers, a starter beat and the song journal. It never touches an existing project.
  `starter=false` gives empty section blocks; `variations=2` gives three beats to pick from; `template=` builds from
  your own `flh_save_template`. Drum kits (`data/kits`), pitched 808 samples kept in key.
- `flp_compose`: beat, drums, chords, bass (follows the chords and the kick; 808 genres glide with FL slide notes),
  melody, counter-melody, layers (pad, strings, choir, arp, brass...) and re-voicing, all from genre data
  (`data/genres`, `flh_genres`) in one reversible batch. `variations=3` writes A/B/C side by side; `what="pick"`
  keeps one.
- `flp_adjust("make it bounce more")`: plain-English changes (bounce, darker, emptier, more human, dustier, sad,
  epic...; `data/adjectives.json`) as note edits, mix intents handed to the mix engineer, before/after measured.
- `flp_arrange_song`: lay composed parts out as a song (the genre's form or your own), energy curve, fills,
  drop-outs, risers, builds; `mode=build|replace|extend`.
- Build around an idea: a captured or transcribed idea becomes chords / bass / drums / counter that fit it
  (`capture_and_build`, the composer's compose-around).
- The performance engine (`flp_perform_setup` / `flp_perform` / `flp_perform_undo`) now works on any song, keeps
  slide notes and per-note pan / fine pitch / mod values, handles stacked layer patterns, writes each note back to
  its own pattern and clip, and reports honestly when its read-back finds a difference.

### Hear it without ears
- `flp_score`: read a song like a score in ~1k tokens - key and modulations, chords per bar with numerals, each part's
  role and register, sections with energy.
- `flp_clashes`: wrong and clashing notes between layers (rubs, non-chord melody notes, bass vs chords, muddy
  voicings, out-of-range notes), ranked with the bar and the call that fixes it; tolerance follows the genre (jazz,
  groove, strict).
- `fl_describe`: the music in words, and what changed between two versions. `fl_visualize`: energy, loudness,
  spectrogram, mix balance, arrangement and piano roll pictures, always with alt text.
- `fl_review` and `fl_doctor` upgrades: out-of-range notes, abrupt entrances, clash summaries, sounding pitch for
  transposed samplers, faster on big projects.
- Mix checks: `fl_masking` (which tracks fight in which band, per section), `fl_reference_match` (your render vs a
  song you like), `fl_verify_intent` ("did it actually get darker?"), `fl_platform_preview` (how a master plays on
  Spotify, Apple Music, YouTube and others).

### Mix and finish
- `fl_mix_engineer(goal="vocal clearer")`: measures the mix (live solo passes, stems or numbers), proposes
  genre-aware moves (gain, EQ, unmasking, compression, space, width, master), applies them in one undoable batch,
  re-measures the goal and logs it in the journal. `revert_last=true` undoes.
- Plugin maps for 14 stock effects (`data/plugin_maps`) so knobs are set in real units (`fl_plugin_set_named`:
  "3 kHz, -2.5 dB"), and `fl_plugin_map_learn` to learn and correct a map on your own PC. Gain-type controls are
  moved in small steps, never swept through full gain.
- Limiter honesty: a loudness target the mix can't reach is no longer forced; `audio_limit` / `fl_finalize` stop at
  what the mix allows and say so (`below_target_db`, `max_lufs`), keeping the rendered WAV.
- Render from the open FL: `fl_render(method="in_fl")` uses FL's own export without closing FL. `auto` picks it once
  it has worked on your PC (`fl_render_probe` checks it safely first); the command-line export stays as the fallback.

### Vocals
- `fl_vocal_setup` (free insert named, colored and armed, latency advice), `fl_vocal_level_check`, `fl_vocal_record`
  (takes back to back, punch in/out where FL allows, takes named and journaled), `fl_vocal_comp` (best phrase per
  take, inaudible crossfades), `fl_vocal_clean` (breaths, clicks, plosives, sibilance, noise; keeps word-initial
  /h/), `fl_vocal_chain` (a measured chain of stock plugins per style; your own plugins kept; revertable),
  `fl_vocal_balance` (vocal vs beat per section, optional ducking), `fl_vocal_double` (doubles and harmonies).

### Ideas
- `audio_to_midi`: hum, sing, whistle or play into your phone - melody, bass, chords or drums become MIDI, with honest
  confidence, optionally straight into a pattern.
- `fl_capture_idea`: play your MIDI keyboard into a new pattern in the open FL (count-in, metronome), get the notes
  back with key, chords and feel; `fl_capture_probe` checks the setup first.

### Learn FL
- `flh_handbook_build`: an index of Image-Line's FL Studio manual built **on your PC** (from FL's local help when
  present, else fetched politely from image-line.com: robots.txt, 1 page/s, resumable). `fl_help` and
  `flh_handbook_search` then answer offline.
- Knowledge cards (`flh_handbook_card`, `data/handbook/cards`): fl-headless's own notes on shortcuts, menu paths,
  every stock plugin, workflows and FL quirks. Unconfirmed facts are marked `verify`.
- Teaching mode: `flh_profile(set={"teaching": "beginner"})` and `flh_teach` explain what was just done and why, or a
  concept ("LUFS", "sidechain").
- `flh_resume`: "where were we" in one call at the start of a session. Recipes: `flh_recipe_run` / `flh_recipe_save`
  for one-call workflows (four shipped). `flh_collab_pack`: project + only the samples it uses + MIDI + stems +
  README in one folder and zip for a collaborator.

### Accessibility
- `fl_access`: opt-in preferences for hearing, vision, motor, language and pace, in the user's own words; tools then
  adapt (measurements trusted where hearing is less reliable, text first for screen readers, bigger pictures for low
  vision, chat and keyboard alternatives to clicking, jargon explained, one step at a time).
- `fl_where` (a spoken-friendly map of the open FL), `fl_second_opinion` (your ears and the measurements side by
  side, kindly), `fl_safe_listening` (level and break tips; general advice, not medical).
- Plain-language glossary notes on results (`data/access/glossary.json`). Nothing is asked about anyone's body and
  nothing is guessed.

### Big projects and speed
- Lazy reloads: on projects that take more than 8 s to load, edits of the open project queue up and reach FL in ONE
  save + reload before listening, rendering, playing or switching, or at `flh_sync()`. Hand edits made in FL
  meanwhile are kept.
- Load cost per project is measured (`fl_status`) and used to pick the fast route; a parse cache means big projects
  are read once.
- Live notes (experimental): `fl_live_notes_probe` tests writing notes into the open FL without any reload; once it
  passes on your PC, `flp_replace_part` uses it.
- Tool tiers: Claude sees 45 essential tools instead of 183 (the tool list costs about 7k tokens instead of about
  30k). `flh_tools` finds and explains the rest; `flh_call` / `flh_run` call any tool.
- `flh_run` runs read-only steps in parallel and gives a plan with time estimates (`dry_run=true`).

### Safety and reliability
- Which project is open is proven by a save, not guessed from the window title: two songs with the same name, FL's
  default names ("Project 3") and Project-info titles no longer confuse it, and unsaved work in FL is never thrown
  away.
- The reload check knows about hand edits: notes you draw in FL right after an edit are never overwritten, and an
  edit the check can't see says `verified: null` instead of claiming success.
- A save that is still being written (OneDrive, network drives) is detected; a cut file is never edited.
- Backups are kept per project (two `song.flp` in different folders no longer share a history). Deletes only go to
  the Recycle Bin; drives without one are refused instead of deleting for good.
- One FL lock: background renders and foreground tools no longer collide ("FL is busy (job ...)" in about 2 s);
  read-only tools and popup handling still answer during a job; file-only analysis never blocks FL.
- Every tool call is checked against its schema: unknown or mistyped arguments are named, with "did you mean";
  missing required ones are named with the tool's short usage; unknown tools suggest the closest ones. Recipe steps
  get the same checks. `flh_run` also takes MCP-style steps (`{"name", "arguments"}`) and args as a JSON string.
- `fl_open` with a modified, never-saved project in FL stops ("save it or close it in FL first") instead of
  answering Yes to "save changes?"; only FL's Open dialog is ever typed into, never a Save As.
- A save FL can't make because of a demo plugin's "Save anyway" warning is cancelled at once and named, instead of
  timing out with FL blocked. The save-identity wait follows a slow save while bytes keep arriving (up to 60 s), and
  FL's backup / autosave copies are never taken for the open project.
- Project-file fixes found by a fuzzer and a corpus of real-world-shaped projects: new patterns no longer copy the
  previous pattern's settings, velocity 128 is kept, FL 20/21 playlist records, the selected arrangement, time
  signatures, unnamed channels, MIDI export of odd tempos, and more. New test suites: fuzzing, the write corpus and
  end-to-end journeys through the MCP dispatcher.
- Bridge script v4: bigger replies (projects with hundreds of channels), global channel indexes even with channel
  rack filter groups, a file mailbox for replies where FL allows it, safe knob search.
- Privacy: `flh_bug_report` makes a zip with errors, versions and the setup check, user names replaced by `<user>`,
  no audio or project contents. Collab packs store sample paths relative to the pack.

### Breaking changes / upgrade notes
- **Bridge script v4.** After updating, run `fl_bridge_setup(action="update_script")` and restart FL once (FL only
  loads a changed script on start). `fl_ping` reports `script_outdated` until you do.
- **Restart Claude Desktop once.** The server's dispatcher changed, so `flh_reload` from 1.16 isn't enough this time.
  Re-run `install.bat`: the config now starts the server through `fl_headless_launch.py` (your `FLH_ROOTS` are
  offered as the default). Settings you added by hand to the `fl-headless` entry (`FLH_FL_EXE`, `FLH_TOOLSET`...)
  are kept; `FLH_ROOTS` changes only when you give new folders. `install.py --help` changes nothing, unknown options
  and relative folders are refused.
- **`FLH_ROOTS` entries must be full paths.** A relative entry (`Music`) is ignored instead of being resolved against
  Claude's working folder; `flh_setup` lists it under `ignored_not_absolute`.
- **Tools that would restart FL** (`fl_settings_set`, `fl_settings_restore`, `fl_bridge_setup(action="use_loopback")`,
  `fl_controller_script(assign=true)`) now refuse while FL is open unless called with `restart_fl=true`.
- **`fl_close` needs `confirm=true`** (only when you asked to close FL).
- **Fewer tools listed.** Only the essential tier is listed to the client. Set `FLH_TOOLSET=full` in the config's
  `env` (or `flh_profile(set={"toolset": "full"})`) for the old full list.
- **Neutral finish defaults.** Without a collection profile, finals are -14 LUFS integrated, -1 dBTP, 24-bit FLAC
  next to the project. Your profile's `finish` / `finish_by_role` still win.
- **Backups moved** into per-project folders automatically on first use. Backups that can't be matched to exactly one
  project go to `backups\legacy` and are still listed by `flp_versions` (as a name match) and restorable.
- Unknown arguments are now an error instead of being ignored.

### Known limits (need confirmation on a real Windows PC with FL)
Built and tested on Linux against a simulated FL; these are marked `# PC-VERIFY` in the code and listed in
`checklists/`:
- Rendering inside the open FL drives FL's export dialogs; until it has worked once on your PC, `auto` uses the
  command-line export.
- Live note writing is experimental and stays off until `fl_live_notes_probe` passes.
- FL slide notes (flag 0x08), Porta/Mono channel settings, the template/icon tables and the Templates folder path.
- The 14 shipped plugin maps are unverified until learned (`fl_plugin_map_learn`).
- Vocal punch in/out and arming need FL's scripting calls to behave as documented; otherwise takes record from a
  pre-roll and the tool says so.
- FL 20/21 project layouts and channel-rack group indexes; FL in other languages (popups are best effort).
- Polyphonic audio-to-MIDI is approximate and says so; tone words ("bright", "strong bass") are rules of thumb.

## 1.16.0 - ready for everyone
- `flh_setup`: first-run check in one call (FL found + version, writable folders, MIDI ports / loopMIDI / Windows MIDI
  Services, FL's script and MIDI input, numpy, speaker recording, profile, FL edition) with exact next steps;
  `apply` stores the FL edition and creates a neutral collection profile (-14 LUFS, -1 dBTP, 24-bit). It never widens
  `FLH_ROOTS` (only the MCP config does) and never guesses the edition (a registry hint is shown, the user confirms).
- Edition-aware plugin ownership: a table of native FL plugins per edition (Fruity / Producer / Signature / All
  Plugins; uncertain entries marked "verify") replaces the hand-written owned/trial lists. Order: profile
  owned/trial lists > free plugins (FLEX, INSTRUMENT/LABS, BBC SO Discover) > edition > third-party plugins in FL's
  plugin database > "unknown" (ask). With the edition unknown, All-Plugins-only synths count as trial and
  Producer+ ones as unknown. `ownership()` explains each answer.
- `flh_run(steps)`: several tool calls in one round trip, in order, `$prev.<key>` passes a value from the previous
  result, `stop_on_error`; results compacted; never recursive.
- No author defaults left: `audio_finish` / `song_target` default to -14 LUFS, -1 dBTP, 24-bit (the profile
  overrides; `audio_finish` reads it when no target is given); house rules no longer name owned plugins or colors;
  the private blank-template fallback is gone (`FLH_BLANK`, else FL's own empty templates).
- FL detection: `FLH_FL_EXE` (or `FL_EXE`), uninstall / Image-Line / App Paths registry entries, Program Files and
  Program Files (x86); the newest of FL 20, 21, 2024, 2025, 2026 wins. File version via version.dll.
- Documents via the Windows known-folder API (OneDrive, redirected, renamed folders), FL's moved user data folder
  from its registry, `FLH_FL_USERDATA`; Splice INSTRUMENT also looked for under Documents.
- Other languages: popups are recognized by structure as well as text (file dialogs, FL's message box after
  Ctrl+O, standard button ids), button captions and dialog words in the common UI languages, localized Yes
  accelerators, localized untitled-project names.
- Non-ASCII user names and paths: FL process list via the Toolhelp API (no localized `tasklist` text), UTF-8 for
  PowerShell output, the FL script stays pure ASCII, renders hand FL an 8.3 short temp path when %TEMP% has spaces
  or accents, ASCII temp copy names.
- Recycle Bin falls back to the shell API when PowerShell is missing or restricted (asks instead of deleting if
  Windows can't recycle). Windows MIDI Services A/B loopback pairs work with `fl_bridge_setup use_loopback`.
- macOS / Linux: the server imports and starts; FL-facing tools answer "Windows only" instead of failing oddly.
- README rewritten for new users (requirements, install, first run, profile and journal, safety, troubleshooting).
- Performance (any song): `flp_perform_setup(project, style=...)` builds a performance spec from the project
  itself - parts and roles detected (drums stay on the grid), sections from the journal/markers/clips, any meter,
  the current notes as the source (quantized if they look humanized; the original kept for undo). Styles: none
  (identity), tight, natural, expressive (rubato via the tempo map), swing, laid-back. Tempo edits are refused on
  songs with audio clips unless allowed; edits made in FL since setup are never overwritten.
- `flp_remove_channels`: delete channels (or every unused channel / unplaced automation clip) at file level,
  renumbering every reference (notes, automation targets, controller links, playlist clips, layers); verified
  before and after writing, refuses structures it can't renumber safely; the song journal follows the renumbering.
- `fl_review` is genre-aware: verse/chorus/drop/build/breakdown forms (several languages) and explicit peak/quiet
  flags; pop/EDM vs orchestral/piano arc rules from the journal/profile genre or style; no-profile loudness is a
  hint, not an error.
- `fl_finalize`: live render progress with an ETA learned per PC; finishing ~3x faster (one analysis pass,
  vectorized FLAC encoder with partitioned Rice coding and per-frame stereo modes, ~18% smaller files); with no
  profile, finals go next to the project at neutral defaults; 'auto' fade keeps hard endings; 88.2 kHz header fix.
- Fixes: `flp_add_channel` cloned the pattern data that sits between channel blocks; `audio_finish` keeps a 0.05 dB
  margin under the true-peak ceiling (dither); `_lufs` ~10x faster (batched blocks).

## 1.15.1
- Live bug round: `fl_doctor` plugin ownership matches whole words ('INSTRUMENT' = 'Splice INSTRUMENT');
  `flh_journal seed` gives sections real lengths (a section ends where the next begins); `flh_journal` returns a
  compact view (full=true for the review table / performance spec).
- `fl_review` tone checks: band-to-neighbour ratios on loud sections only (register changes no longer read as
  harsh/muddy); per-song consistency note against the collection's tone reference (`as_reference=true` on an
  approved song); correlation flagged below 0.05 (reverb-rich piano sits at 0.1-0.4); at most 10 issues.
- `fl_sound_catalog` finds installed FLEX packs (Documents\Image-Line\FL Studio\FLEX\Packs, .flex2pack).
- `fl_ab`: edits in [B] judged by content, not mtime (FL's re-save no longer keeps the copy forever); B is saved
  before switching back; switches wait until FL finished loading instead of failing.
- `fl_job` returns a copy of the job record (its took_s no longer sticks from an earlier poll).
- New tools show up without a Claude restart after a tool-list refresh.

## 1.15.0
- Extensions: `flh_*.py` modules next to the server register their own tools (`register(ns)`); `flh_reload`
  re-imports them, and one broken extension is reported (`flh_reload`, `fl_status`) instead of stopping the server.
- `flh_profile` / `flh_journal`: the collection's house rules and a per-song memory (sections, roles, likes and
  dislikes, performance settings, versions, final) kept next to the project.
- Jobs finish instantly: `fl_job` waits on the job's completion signal instead of polling, `fl_job()` with one job
  running waits on that one, and jobs report live progress (`_job_progress`).
- Edits no longer wait for the previous edit's background check (one `flp_edit` took 97 s that way): a newer edit
  stops the check and its own first save verifies the previous edit instead (`previous_edit_held`).
- Per-step timings: `fl_timings` shows the step breakdown of each tool's slowest and last call and of finished jobs;
  `fl_render`, `fl_render_batch` and `fl_open` report `steps_s`.
- `fl_finalize` (+ `audio_limit`): render, finish, file and reopen in one job; FL closes once for several songs.
  Targets come from the collection profile (opener -16, others -18.7, 16-bit, TP -1). A transparent 4x-oversampled
  true-peak limiter lets the opener reach its target; the old final is recycled only after the new one is written.
- `fl_doctor`: static project check (trial plugins, silent BBC/LABS MIDI channel, unused/muted channels, muted or
  missing mixer routes, automation targets, empty clips, missing samples, master wideners, dead tail).
- `fl_review`: per-section loudness/stereo/tone review of a render (WAV/FLAC) or live FL, with a ranked issue list
  (loudness arc, peak not loudest, negative correlation, mud/harsh/dull, collection target). FLAC decoding added.
- `flp_perform` / `flp_perform_setup` / `flp_perform_undo`: performance engine - tempo map, dynamics,
  humanization, legato and events as data in the journal; "slow one section down 3%" is one call that rewrites only
  what changed. Reproduces a hand-written reference arrangement exactly.
- `fl_sound_catalog`: index of owned instruments (LABS, BBC SO Discover, FL generators, FLEX, saved sounds) with
  load routes and mood tags; trial plugins hidden. `fl_ab`: switch between project versions in FL in seconds.
  `fl_watchdog`: one-call health check (FL, popups, ports, bridge, disk) that heals what's safe.
- Fixes: `_true_peak` read up to 0.7 dB high (looped FFT edges), turning finals down below target; `_lufs` skipped
  the last block; one tasklist per burst of FL checks; preset search sees OneDrive Documents; tool errors carry a
  watchdog hint.

## 1.14.3
- `fl_open` waits while FL has no window (closing after a render, or starting) instead of failing to drive its Open
  dialog - the reopen after `fl_render_batch` failed this way.

## 1.14.2
- `fl_mix_apply` EQ inserts use the fast save/edit/reload path (~32 s -> ~5 s).

## 1.14.1
- `fl_mix_check` hears one track by muting the others (then restores the exact mute states) instead of FL's solo,
  whose state can't be read back - a solo could stick between passes and garble every measurement.

## 1.14.0
- `flp_tempo_write`: replace the tempo map in the playlist's Tempo lane in place.
- `flp_plugin_settings`: BBC SO / LABS / INSTRUMENT settings without the plugin window (MIDI channel, articulation,
  dynamics); `_juce_xml_rewrite` shared with `flp_instrument_sound`.

## 1.12.6
- Reload proof: a mixer track name is tagged live before reopening; FL re-reading the file removes the tag (FL doesn't
  autoplay or go busy on a reopen, so the old signal missed it and always took the slow route). ~19 s -> ~5 s.
- `flh_call` runs any tool by name, for tools an update added before the client lists them.

## 1.12.4
- Editing the open project never closes FL any more: if FL doesn't save in time, the edit stops with nothing changed.
- Reload marks the project changed (insert fader nudge) so reopening it reloads directly; per-step times in `steps_s`.

## 1.12.0 - faster
- Reload after an edit opens the project directly (FL 2026 reloads it) and only falls back to the blank-template
  route if FL shows no sign of loading; waits poll instead of sleeping. ~25 s -> ~5-8 s.
- The "FL holds the edit" check runs in the background (`verify_job`); a failure is reported on the next tool call.
- `fl_mix_check`: hears at most 8 s per pass (`window_s`), and bypasses the master effects while measuring so a
  maximizer can't inflate quiet solo tracks.
- Every result carries `took_s`; `fl_timings` shows where the time went.

## 1.11.0
- `flh_reload`: the running server loads its updated code from disk - no Claude restart after an update. The FL
  connection and background jobs carry over; the client is told the tool list changed.
- House rule: only plugins the user owns (Sakura is a trial in this edition).

## 1.10.1
- `fl_snapshot hwnd=` pictures one of FL's popups, so painted warnings can be read.
- `fl_dialog click enter/escape/ok`: if a posted key doesn't reach the popup, it's brought to the front and the key
  pressed for real; an OK-only notice that still won't close gets WM_CLOSE (never a save question).

## 1.10.0
- `flp_instrument_sounds` lists the Splice INSTRUMENT packs/presets downloaded on the PC; `flp_instrument_sound`
  switches an INSTRUMENT channel to one of them by editing its saved state (no plugin window).
- `files_list` no longer fails on broken shortcuts (Documents\\My Pictures).

## 1.9.1
- fix: `fl_open` to a different project crashed ('list' has no strip) in the new save-first step.

## 1.9.0 - faster edits
- `flp_edit`: many file edits in one step - one save, one reload, one verify; all or nothing.
- `flp_replace_part notes=[[bar, length_bars, pitch, vel], ...]`: notes inline, no MIDI file to upload.
- `flp_add_automation replace=true`: updates the clip with that name in place (no remove + add, no stray channels).
- `flp_playlist names=[...]`: pick clips by name.

## 1.8.1
- Reload check: FL ignores a save for a few seconds after loading; the check now retries for ~20 s.

## 1.8.0
- `fl_snapshot`: a picture of FL's own window (or part of it), returned as an image - checks by eye without
  screen control. Works with FL behind other windows.
- Every reload after an edit is verified: FL saves what it loaded and headless compares notes, playlist clips and
  channels with what was written. If FL kept an old copy, the edit is restored and FL reloads once more; results
  carry `verified`.

## 1.7.2
- Every project-writing file tool (replace_part, add_automation, mixer, style_channels, load_plugin, ...) now saves,
  edits and reloads when its output is the project open in FL. Before, only some did, so FL's stale in-memory copy
  could be saved back over an edit made by the others.
- `flp_playlist` move/remove/resize/mute find their clips again after FL renumbers clip ids on that save.

## 1.7.1
- FL's own "save changes?" box (TMsgForm "Confirm", painted buttons) is now recognized as `save_changes` and answered
  by its key (Y/N), with a check that it closed.
- `fl_open` saves the current project through the bridge before switching projects; reloads after a file edit answer
  No (the file on disk is newer). Save questions are never escaped.
- `fl_dialog click` falls back to the button's key when the popup has no real buttons.

## 1.7.0 - ears, context and a mix engineer
- fl_listen: hear the song in the open FL (records the speakers via WASAPI loopback) and get loudness, peaks,
  clipping, tone and width per section - checks no longer need a render or closing FL.
- fl_mix_check / fl_mix_apply: mix engineer - hears each mixer track solo, proposes ranked fixes (fader, EQ dips
  where parts fight, high-passes, headroom), applies the chosen ones live, revert in one call.
- fl_audition: find a sound by ear - plays a chord through candidate presets, records, describes (attack,
  sustain, tone, width), ranks against a wish ('soft string pad'), keeps FLAC previews, applies the pick.
- fl_context: what's focused/selected in FL (channel, mixer track, pattern, playhead, playlist selection).
- fl_help / fl_manual_page: answers from Image-Line's official FL Studio manual (cached).
- Bridge: big replies no longer get cut off (the server listened with 16 MIDI buffers; a large project's state
  arrives as more SysEx messages than that). Calls are serialised (background helpers can't interleave).
  fl_bridge_setup update_script reinstalls the FL script, diag reports from inside FL.
- Tried a file mailbox transport (no MIDI): FL 2026's sandboxed Python refuses file I/O, so it switches itself
  off and MIDI carries everything, as before.
- fl_status: bridge transport, and once after each FL update a read check of every project.
- 3/4 fixes: fl_song goto/where and flp_playlist bars follow the project's time signature.

## 1.6.6
- flp_replace_part: rewrite one instrument's notes in an arranged song without touching anything else.
- flp_add_automation: bars follow the project's time signature (3/4 songs).

## 1.6.5
- Darker default palette for fl_organize (deep, muted shades instead of bright ones); house rules mention it.
- fl_batch accepts the natural target name on any op (channel / pattern / track / index).

## 1.6.4
- flp_write_arrangement: beats_per_bar (e.g. 3 for 3/4). Section bars, pattern lengths and the song length follow it, and the project's time signature is set to match.

## 1.6.3
- Server now sends its house rules to the AI on connect (one FL only, render once, handle popups, refresh via blank template, FL quirks).
- Never launches an .flp while FL runs: if FL's Open dialog can't be driven it clears stray popups, retries once, then reports instead of starting a second FL.
- fl_status reports how many FL copies run, the window title and open popups.
- fl_ping retries once (first ping after a restart is often cold).

## 1.6.2
- `fl_dialog`: sees FL's popups (save prompts, Save As, missing-file warnings) with their text and buttons, and
  answers them (click a button by caption, or escape / enter). `fl_close` now says which popup is in the way.
- Reloading a project FL already has open goes through an empty project and back (FL ignores a request to
  open what's already open), so edits always show up and FL never has to quit for it.
- `fl_open` with FL running uses FL's own File > Open dialog (Ctrl+O, path typed into the dialog): since FL
  2026.1.7, launching an .flp could start a second FL instead of using the open one (stray "Project 2"
  windows, closes that "failed"). Stops playback first (FL won't switch mid-playback) and warns if a second
  FL appears anyway.
- `tighten` takes grid "none" for songs whose rubato is baked into the notes: groups meet at their first note.

## 1.6.1
- `flp_playlist`: individual clips: list (id, kind, name, bar, length, track, muted), place a pattern or an
  audio / automation clip channel at a bar and track (repeat N every M bars), move (several keep their
  spacing), remove, resize / trim the start, mute / unmute (FL's own mute bit, confirmed by muting in FL).
- Fix: `flp_write_arrangement` put clips one playlist track lower than intended (track n is 500 - n).
- Fix: closing FL sends the close to FL's main window (a floating plugin/channel window could get it), and
  FL 2026's "Project 2"-style untitled names are recognised, so it no longer opens a Save As box.

## 1.6.0
- `flp_audio_clip_settings`: read / set an audio clip channel's sample settings the way FL's channel window shows
  them: stretch mode (resample, stretch, stretch pro, e3, slice, auto, e2 modes), TIME (beats, bars, or "fit" to
  follow the project tempo), pitch in cents, length multiplier, reverse, normalize, remove DC, reverse polarity,
  fade / swap stereo, fade in / out, sample start and length. Mapped by changing each control in FL 2026.1.7 and
  diffing the saved project; checked with renders (reverse, +1 octave at the same length, 2-bar time = 8 s at
  60 BPM, x2 multiplier, start at 50 %, fade curves).

## 1.5.1
- `audio_slice` beats mode can follow a project's tempo map (`project`, `start_bar`): on a test song the bar cuts
  land within ~25 ms of the real downbeats (a fixed bpm drifted up to 640 ms).
- `audio_detect` key: chroma from spectral peaks only, each frame normalised. a G-major test song now reads G (as its notes
  do; before: F major); 24/24 keys right on synthetic progressions.
- `audio_edit` op `dehum`: finds 50/60 Hz mains hum (to ~0.01 Hz, only if it's steady through the file, so low
  music notes aren't mistaken for it) and notches it and its harmonics, zero-phase; clean audio is left alone.
- Tested: rendering while FL stays open isn't possible (FL hands the command-line render to the open copy),
  so renders keep the automatic save, close, render, reopen.

## 1.5.0
- No loopMIDI needed on Windows 11: `fl_bridge_setup` runs the live bridge over the built-in "Default Basic App
  Loopback" port (Windows MIDI Services; one port both ways, requests and replies ignore each other's echoes).
  It checks the port, installs the script, sets FL's MIDI settings and confirms FL answers. `status` /
  `use_loopmidi` too. Port choice: FLH_PORT_* env > saved choice > loopMIDI pair > a loopback port.
- Audio editing (`fl_audio_edit.py`, numpy): `audio_edit` (trim, cut with crossfade, silence, fades, gain,
  normalize to peak or LUFS, reverse, repeat, pitch with or without length change, time stretch, mono / stereo
  / width, denoise), `audio_join` (crossfade or gap), `audio_slice` (transients, every N beats, equal parts).

## 1.4.3
- Editing the project that's open in FL no longer closes FL: it's saved through the bridge, edited, and loaded
  again in the same window (and FL's after-load autoplay is stopped). Close + reopen is only the fallback when
  the bridge isn't connected.

## 1.4.2
- `flp_edit_notes`: new `tighten` op. Notes that start together (any channel) move toward their shared beat
  (strength 0.8 keeps ~7 ms of natural spread); runs and arpeggios are left alone, note ends stay put, and a
  note moved earlier never overlaps the previous one on the same key.

## 1.4.1
- `flp_arrange`: delete no longer drops a tempo map (or any clip) that starts inside the cut; clips crossing an
  edit point are trimmed or split, so delete / duplicate / insert keep every note where it belongs and the
  tempo in force carries over. Checked note for note, and a render of a duplicated section matched the
  original bit for bit.
- `audio_detect`: per-band onset strength and a ~100 BPM prior (slow piano no longer reads as double time);
  `min_bpm` / `max_bpm` settle double vs half time.
- `fl_controller_script`: generated scripts now react to knobs (CC numbers were stored as text).
- `fl_song`: replies show the state after the change (FL applies it after the call); new `where`;
  `marker_jump` reports where it landed.
- `fl_render` / `fl_render_batch`: create the output folder if it doesn't exist.

## 1.4.0
- Mixing: `fl_render_stems` (one stem per playing channel, background job), `audio_mix_report` (gain staging,
  buried parts, frequency clashes between stems, summed mix vs a reference), `flp_duck` (sidechain-style pumping
  as volume automation triggered by a channel's notes), `fl_plugin_set_value` (live: set a knob to a displayed
  value such as "-3 dB" or "250 Hz").
- Arranging: `flp_arrange` (insert / delete / duplicate bars, tempo maps kept in line), `flp_pattern_tools`
  (clone, split by channel, remove empty).
- Live song controls `fl_song`: go to bar, add / jump markers, metronome, song mode, record.
- Across projects: `flp_search` (by channel, plugin, sample, pattern, title), `fl_export_all` (background job).
- FL shows a project's title (not its file name) once the title is set: open-project detection and the
  untitled check now handle that.
- Samples: `audio_detect` (BPM and key of a WAV). Controllers: `fl_controller_script` writes an FL controller
  script (knobs and pads to mixer, channels, plugin knobs, transport) and can assign it to a device.

## 1.3.0
- Pattern editing: `flp_patterns`, `flp_notes`, and `flp_edit_notes` with piano-roll operations (quantize with
  strength, humanize, transpose, fix to scale, velocity set/scale/ramp, legato, staccato, strum, arpeggiate, swing,
  chop, octave doubling, diatonic harmonize, chord inversion, dedupe, delete) on a selection (channel, bars,
  pitch range). Edits to the project open in FL save, close, write and reopen it.
- `flp_add_notes`, `flp_new_pattern` (named, optionally placed in the playlist).
- `flp_analyze_music`: key (also per section), chord progression, ranges, density; works on MIDI files too.
- `flp_drum_pattern`: 10 groove styles with ghost notes, swing, humanize and fills.
- `flp_health` (missing samples, missing VSTs, unused channels, empty/unplaced patterns, effect load) and
  `flp_collect_samples` (self-contained project folder).
- New module `fl_music.py` (theory + note operations) with `tests/test_music.py`.

## 1.2.1
- Non-ASCII text (names, dashes, accents) now arrives intact: the stdio JSON-RPC streams are read as UTF-8
  (Windows defaulted to cp1252 and garbled "–" into "â€“").
- `fl_close` no longer tries to save an untitled project (that opened a Save As dialog and blocked the close).
- `fl_settings_summary`: audio device, sample rate, buffer, MIDI devices/scripts, autosave in plain words.
- Verified live: a changed setting is picked up by FL and kept through FL's own exit; restore is exact.

## 1.2.0
- FL settings: `fl_settings` reads everything FL keeps in the registry (all of Options); `fl_settings_set`
  changes values (FL closed politely first and reopened, snapshot taken before every change);
  `fl_settings_snapshot`, `fl_settings_diff` (where does a setting live?) and `fl_settings_restore`.
  Access is limited to HKEY_CURRENT_USER\\Software\\Image-Line.
- `flp_project_info`: read/set title, artist, genre, comments, URL, data folder, time signature, show-info, pitch.

## 1.1.0
- Sound library: `flp_save_sound` saves the exact sound of any channel instrument or mixer effect (VSTs included)
  as a .fst; `flp_sounds` lists them; `fl_load_preset` finds them by name first.
- `fl_plugin_preset_find` reworked: steps the right number of times and waits for the plugin's asynchronous
  preset change to be confirmed (tested live on FLEX). The bridge's own find command was removed.
- `fl_render_batch`: several projects in one go (FL closed and reopened once). Tested: FL's command line
  doesn't render a folder of projects in one launch, so they render one after another.
- `fl_selftest`: run after FL updates; reads every project and renders a canary (notes, tempo map, audio clip,
  automation), measuring each part.
- Section renders re-cut automation clips that start before the section instead of leaving them out.
- Background jobs: `fl_render_batch` runs as a job polled with `fl_job` (remote bridges time out after ~60 s).
- `fl_close` remembers the project when FL is still loading, so the reopen after a render doesn't get lost.

## 1.0.0
- `flp_add_automation`: automation clips written into a project (mixer volume / pan, or a plugin parameter on a
  mixer slot), points in bars with values, dB or pan; placed in the playlist. Built from channel blocks FL itself
  saved (`fl_blocks.py`); a rebuilt clip is byte-identical to the one FL made.
- `flp_add_audio_clip`: a WAV/FLAC as an audio clip at a bar, length from the file and the tempo map.
- Section renders leave out audio/automation clips that start before the section (and say so).
- `audio_analyze`: sections running past the end of the file are clipped instead of failing.
- Verified in FL: a written master-volume automation fades a render to digital silence on schedule, and a written
  audio clip plays at its bar.

## 0.9.1
- `flp_mixer`: mixer track name, color, volume (dB, using FL's fader curve measured from FL), pan and routing
  written straight into a project file.

## 0.9.0
- `fl_render(start_bar, end_bar)`: render just a section (clips cut to the window, tempo at the start kept).
- Ears: `audio_analyze` (loudness, 6-band tone, stereo width, peaks, clipping; whole and per section) with a
  plain-language comparison against a reference profile; `audio_profiles`; `audio_finish(save_profile=...)`.
- Versions: every project overwrite first keeps the previous version (one backup per tool call, newest 40);
  `flp_versions`, `flp_restore`, and `flp_diff` (notes added/removed per channel with examples, tempo, channels,
  effects, patterns, playlist).
- Live audition: `fl_play` plays a chord or phrase through a channel's instrument in the open FL without writing
  anything; `fl_stop_sound`.

## 0.8.0
- `fl_load_preset`: load a .fst preset by path or name onto a channel or mixer slot, even while the project is
  open (save, close politely, write with a backup kept in %LOCALAPPDATA%\\fl-headless\\backups, reopen).
- `audio_finish`: the finishing pass on the PC: trim, fade into the ring, gain to target LUFS with true peak capped,
  FLAC out (`flac_lite.py`, a small numpy FLAC encoder, verified bit-exact against ffmpeg). Optional 16-bit with
  TPDF dither, optional recycle of the WAV.
- `files_hash`: SHA-256 / size / time, to confirm transfers.
- `fl_organize` skips the master track and automation channels; `fl_plugin_preset_find` explains when a plugin
  can't report its active preset and lists the matching preset numbers instead.

## 0.7.0 - Gopher-style project control
- Live: channel color / solo / select / route to mixer track; mixer color / solo / routing (`route_to`, `unroute`)
  / reset; `fl_playlist_track_set` and `fl_pattern_set` (names, colors); `fl_steps` (step sequencer);
  `fl_show` (open mixer, channel rack, playlist, piano roll, browser); `fl_plugin_presets` (preset names).
- `fl_batch`: many live changes in one call and one undo step (e.g. rename and recolor the whole mixer).
- `fl_organize`: one-shot tidy-up: mixer tracks named after their channels, channel + mixer + playlist track share
  a color (by role, distinct, or kept).
- `flp_style_channels`: channel colors, icons and names in a project file.
- `fl_state` now includes colors, pattern names and playlist track names.

## 0.6.0
- `flp_write_arrangement`: one named pattern per song section, laid out in the playlist (clips alternate tracks
  so ringing notes aren't cut; the tempo map lives in its own "Tempo" pattern).
- `flp_info`, `flp_export_midi`, `flp_tempo_map` read multi-pattern projects as the playlist plays them.
- Bridge: `fl_plugin_info` (current preset name and count), `fl_plugin_preset` returns the new preset name,
  `fl_plugin_preset_find` steps to the first preset whose name matches.
- `audio_info`: duration, peak, RMS and BS.1770 loudness of a WAV render, measured on the PC (needs numpy).

## 0.5.0
- `fl_render(close_fl=true)`: if FL is open it saves the project through the bridge, closes FL politely (never
  forced; if FL asks a question it stays open and the call says so), renders, then reopens the project.
- `fl_close`: the polite close on its own.
- `flp_export_midi`: a project's notes and tempo map as a MIDI file, one track per channel.
- `flp_write_notes(tempo="auto")`: the MIDI files' tempo map is written as tempo events FL plays back, so notes
  can sit on the grid while the tempo breathes. `tempo="keep"` keeps the old behavior (byte-identical output).
- `flp_tempo_map`: the tempo FL will actually play.

## 0.4.1
- `flp_set_tempo` also moves tempo events recorded in the pattern (left behind by MIDI imports). Before, those
  silently overrode the project tempo when FL played or rendered.
- Project writes create the output folder if it doesn't exist yet.
- `flp_load_plugin`: older factory presets with 8-bit plugin names are converted to UTF-16 (FL 20+ projects),
  and sample-based channel presets without a plugin block are refused instead of corrupting the channel.

## 0.4.0
- Code cleanup: named FLP event constants, one atomic save path with read-back check for every write,
  all project tools read through the same event parser. Behavior unchanged (outputs byte-identical to 0.3.0).
- Generic installer (`install.py` / `install.bat`), bridge round-trip test (`tests/test_bridge.py`).

## 0.3.0
- `flp_load_plugin`, `flp_remove_effect`, `flp_add_channel`, `fl_find_presets`: load instruments and effects
  from `.fst` presets, add channels.
- `fl_render` waits for FL to close on its own before stopping it.

## 0.2.0
- Live bridge with a fixed command list (no code execution): state, transport, tempo, channel/mixer settings,
  plugin parameters, presets. Works on its own ports or through an existing Flapi setup.
- `flp_list`, `flp_copy_state`, `flp_set_tempo`.

## 0.1.0
- Project files: `flp_info`, `flp_write_notes`, `flp_set_song_length`. Command-line rendering, file tools.
