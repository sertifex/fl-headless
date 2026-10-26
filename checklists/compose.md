# flh_compose - PC checks (Windows + FL Studio)

Linux tests (`python3 tests/test_compose.py`) cover the generators and the file writes. These need a real FL.
Use a scratch project inside FLH_ROOTS with channels named Kick, Snare, Hat, Open Hat, 808, Keys, Lead (FL's
default template + a few channels is fine).

| # | What to run | What to expect |
|---|---|---|
| 1 | `flp_compose(project, what="beat", genre="trap", key="C# minor")` with the project open in FL | One save + one reload. Patterns Beat Drums / Beat Chords / Beat 808 / Beat Melody at bar 1 on their own playlist tracks; tempo 140 if the project was empty. |
| 2 | Open "Beat 808" in the piano roll | **Slide notes** (flag 0x08 in the note record): the short notes over the held 808 show FL's slide marker, and the 808 (FL Sampler / any native generator) bends up to them without re-triggering. `# PC-VERIFY` in `SLIDE_FLAG`. |
| 3 | Same with `glide="overlap"`, 808 channel set to mono + Porta | Overlapping notes glide; no stacked doubled notes. |
| 4 | Brand-new FL project (no pattern ever placed), then `flp_compose(... what="drums")` | Either works (pattern clip made from FL's own clip template, `_place_clip`, `# PC-VERIFY`) and plays in FL, or reports "place any pattern in FL's playlist once" / "name any pattern in FL once" (`_new_pattern`). Note which. |
| 5 | Change a note in FL without saving, then run `flp_compose(what="counter")` | `_fresh` makes FL save first, so the counter answers what you see (the edit survives). `# PC-VERIFY` |
| 6 | `flp_compose(what="melody", section="Hook", variations=3)` then `fl_listen` each `listen` range | A/B/C after the song end, each with the Hook's other parts copied under it; they sound different. `what="pick", variation="B"` removes the audition bars and puts B into every Hook. |
| 7 | A project with only FPC for drums | Hits land on the GM keys (kick 36, snare 38, clap 39, closed hat 42, open hat 46, rim 37, ride 51, crash 49, tom 45) and FPC's default kit plays the right pads. |
| 8 | `flp_arrange_song(project, mode="replace")` on a house beat with FL running | Build sections get a volume automation clip on the chords' mixer insert (insert from `fl_state`). After the clip the fader is back at its old level; starting playback at bar 1 is at the old level too. `# PC-VERIFY` |
| 9 | Big project (lazy reload mode) open in FL, run `flp_compose` twice | The batches queue (`queued: true`), the second call reads the shadow copy (sees the first one's patterns); `flh_sync` applies both in one reload. |
| 10 | `flp_adjust(project, "make it bounce more")` then `fl_listen` | Odd 16ths of drums/bass later (swing measured 0.5 -> ~0.56), downbeats louder; `mix` is the mix engineer's proposal (not applied unless `mix_mode="apply"`). |
| 11 | Channel types | `channels_of` reads event 21: 0 sampler, 2 plugin, 4 audio clip, 5 automation on FL 2024/2025/2026 (no notes written to 4/5). |
| 12 | A channel cloned for a missing role (e.g. "Counter") | Named after the role; `new_channels[].next` (fl_audition with the sound hint) loads a fitting sound. |
| 13 | Lo-fi chords (`strum_ms`) and humanize | Chords strum slightly (8-18 ms), timing is loose but on the beat; nothing ahead of bar 1. |
| 14 | `flp_arrange_song` with an fx channel named "Riser" or "Reverse" and flh_fx missing | A 1-bar note ending on the hook downbeat; check the sample's length (marked as a guess). With flh_fx the arranger makes its own FX instead (EC1-EC10 below). |
| 15 | `fl_capture_idea(bars=4)` (play a melody), then `capture_and_build(project=..., what=["chords","bass","drums","counter"])` | The recorded pattern is found (`idea.in_project: already in pattern ...`, no copy); new patterns `Idea Chords/Bass/Drums/Counter` start at the recorded clip's bar; the chords fit what you played (`idea_fit`); `swing.from` says it matched your playing; one save + one reload. `# PC-VERIFY` in `_find_idea_pattern`. |
| 16 | `capture_and_build` on a hummed idea (`audio_to_midi` of a voice memo), no genre | `guesses` names the genre with alternatives and the key with its confidence; the parts follow the hummed phrase length; listen: does the harmony feel right under the tune? |
| 17 | The new genres (jersey-club, phonk, hyperpop, lofi-house, synthwave, dancehall, amapiano, gospel, kpop, metal): `flp_compose(what="beat", genre=...)` for each with FL's default kit + a few instruments | Listen for the genre signature: jersey bounce kick (triplet tail), phonk cowbell riff + sliding 808, synthwave octave bass + gated snare, amapiano log-drum bass + shakers, dancehall 3-3-2 kick, metal gallop chugs. Note anything that sounds off-genre (data/genres/<name>.json is the place to fix it). |
| 18 | Synthwave / k-pop octave bass, amapiano log drum glides | Octave pulse alternates root and octave on the 8ths; log-drum slides land late in the bar (never on the downbeat). |
| 19 | Render or `fl_finalize` (a job holding FL), and meanwhile `flp_compose(project=<the open song>, what="melody", dry_run=true)` | No "save" reaches FL during the export (FL's export isn't interrupted); the result has `read_note: "read the file on disk: FL is busy ..."`. With FL idle the same call makes FL save first (file mtime changes) and has no `read_note`. `# PC-VERIFY` in `_fresh`. |
| 20 | `flp_arrange_song(mode="replace")` on the open song after moving one composed clip by hand in FL (no save) | FL saves, the clip ids change; the composed clips in the re-laid bars are replaced (none doubled), the hand-moved clip and every non-composed clip stay; a moved clip that wasn't where planned is reported in `notes` ("weren't where they were planned"). |
| 21 | (2026-10-23) `flp_compose(what='layer', roles=[{role:'arp', where:[[9,57],[57,97]]}, {role:'pad', where:[[17,33]], vel:0.7}], bars='all')` on a song with a Music bus | The arp writes notes only in bars 9-96 (not '0 notes'); the pad's velocities read ~83-95 in the piano roll (vel 0.7 = 70% of full, 90 = MIDI 90, 'soft'/'normal'/'loud' work). Each new layer channel (Arp, Pad) has its OWN mixer insert named after it, dark coloured, routed to the Music bus (never the Bass insert it was cloned from); the bass keeps its insert. `# PC-VERIFY` FL shows the insert names/routing after the reload. |

## Layers played by flh_realism
| # | Run | Expect |
|---|-----|--------|
| R1 | `flp_compose(song, what="layer", role="strings", section="Hook")` with flh_realism loaded | `realism.strings.applied: true`, `retimed` > 0; listen: a played string line (swells, legato), no new rubs (`clashes` verdict as without realism). PC-VERIFY in `_realism_rows`. |
| R2 | Same with `realism=false` | Identical to the old behaviour (genre humanize). |
| R3 | `flp_compose(..., what="layer", role="pad", channel=<a Piano channel>)` | No realism (a piano channel keeps the piano rules). |
| R4 | `flh_new_song("cinematic", starter=true)` | Starter parts on Strings / Low Strings / Horns channels report `realism` (bass/chords strings, melody brass); pop/trap starters don't. |
| R5 | Expression | With flh_realism's writer: the batch includes the expression ops on the layer's channel (one reload); without: `realism.<part>.expression` says to run flp_realism after. |

## Meters, tempo maps and multi-role layers
| # | Run | Expect |
|---|-----|--------|
| M1 | A 3/4 song in FL (solo piano is fine): `flp_compose(song, what="layer", roles=["low strings", "strings", "choir"], bars="all")` | ONE save + reload; channels Low Strings / Strings / Choir cloned and named; patterns at bar 1 spanning the song in 3-beat bars; in the piano roll every layer chord starts on a bar line (`# PC-VERIFY` in `flp_compose_write`). |
| M2 | Same, listen | Support level: the layers sit faint under the piano (`level` in the result), the choir only in the loudest bars (`layers.choir.where`), low strings on the piano's bass notes (C/E bars play E). Balance is library-dependent: judge by ear (`# PC-VERIFY` in `_support_vel`). |
| M3 | Same, the end of the piece | The layers' final chord stops with the piano's last note; nothing sounds after it. If the piano's last note rings past the section end, the layer clip is longer so its tail plays (`# PC-VERIFY` in `_end_with`). |
| M4 | `flp_compose(what="beat", genre="pop")` in an empty 3/4 project, then in 6/8 | `notes` says "a generic 3/4 feel (waltz ...)" / 6/8 compound; listen: strong 1 + light 2/3 (3/4), two dotted-quarter beats with rolling 8ths (6/8). `genre="cinematic"` or `"rock"` say they used their own 3/4 / 6/8 patterns (`# PC-VERIFY` in `drums_meter`). |
| M5 | A song with a time-signature marker (4/4, then 3/4 at bar 9): `flp_compose(what="layer", role="pad", section=<a section after bar 9>)` | Written in 3/4 (`meter: "3/4"`), the clip starts on the right bar in FL's playlist. A block crossing the change says so in `notes`. |
| M6 | A song with tempo automation (e.g. a classical import): `flp_compose(what="layer", roles=[...], bars="all")` | `notes` says the tempo changes; while FL plays, the layers stay with the piano through every tempo change (they're placed in bars and beats). |
| M7 | `fl_capture_idea` in a 3/4 project, then `capture_and_build(project=..., what=["chords","bass"])` | Parts in 3/4 bars at the idea's bar (the idea carries beats_per_bar 3 / time_signature); a 4/4 idea into a 3/4 project is refused with a clear message. |
| M8 | 5/4 and 7/8 projects: `what="beat"` | Bars of 5 beats (felt 3+2) / 7 eighths (2+2+3); chords change on bar lines; generic drums said honestly. |
| M9 | A 3/4 song with a composed beat: `flp_arrange_song(song, form="Intro:4, Verse:8, Hook:8, Outro:4", mode="replace")` | `sections[].meter` = 3/4; FL's playlist shows each section on a 3-beat bar line; the silent last beat before a section is one quarter (1/3 bar), the drop-out two beats, a fill one 3-beat bar ending on the downbeat; `Drums Light` plays 3-beat bars (`# PC-VERIFY` in `flp_arrange_song`). |
| M10 | Same song: `flp_arrange_song(song, extend={"section": "Verse", "bars": 4})`; then a 4/4 song with a 3/4 marker later on, extend a section before the marker | The Hook moves 4 bars later and still starts on a bar line; in the marker song FL shows the time-signature marker moved by the inserted bars, everything after it on its bars (`# PC-VERIFY` in `flp_compose_insert`). |
| M11 | `flp_adjust(song, "more bounce")` in 3/4 / 6/8 | Accents land on beat 1 (downbeat) and the other beats (no 4/4 pattern); `meter` in the result says so; listen. |
| M12 | A layer onto a channel the batch creates: `flp_compose(song, what="layer", role="strings")` with no Strings channel | ONE reload; the new Strings channel already has `Strings dynamics/expression - <section>` clips (or volume on a plugin without a known CC map); no `realism.strings.expression` "run flp_realism after" line (`# PC-VERIFY` in `flp_realism_write`). |
| M13 | FL's last reload of the song failed (fl-headless marks it stale), then any `flp_compose(...)` | FL is NOT asked to save; `read_note` says the edited file on disk is newer than FL's copy and names `fl_open` (not "FL didn't save in time"). |
| M14 | A lofi song: `flp_compose(song, what="layer", role="pad", section=<the beat's section>, level="support")`, then `flp_arrange_song(song, mode="replace")` | The pad plays faint in Intro / B / Hook / Bridge / Outro, not in A / Verse; the block stays the beat (`compose.blocks.<section>.what = beat`). |

## Musicality: hook-first melodies, grooves that lock, sections that go somewhere (2026-10-22)
Linux measures the notes (`fl_musicality`, `tests/test_compose_musicality.py`); only ears can say whether it now
"rhymes". Listen on a real kit + instruments for each genre you care about (trap, lofi, boombap, house, pop, gospel,
jazzhop at least) with `flh_new_song(<genre>, starter=true)` or `flp_compose(what="beat")` + `flp_arrange_song`.

| # | Run | Listen for (yes / no) |
|---|---|---|
| MU1 | `flp_compose(project, what="beat", genre=<g>)`, play bars 1-8 | Can you hum the melody after two plays? Bars 1-2 state a motif, bars 3-4 move it to the next chord (sequence) or answer it, bars 5-6 bring the opening back, bars 7-8 end on a settled note. Phrases breathe (short rests) instead of a wall of notes. `musicality` in the result: note the score next to your verdict. |
| MU2 | Same, the bass with the kick | 808 / bass hits land with the kick (no flams); a lofi / pop bass fills kick hits that fell in its rests; house bass pumps root-octave between the kicks; chord changes start on their root. |
| MU3 | Same, bar 4 and bar 8 | An open hat (or the genre's `fill.turn`) marks bar 4, the fill at bar 8; the groove between them repeats (extra kicks / ghosts no longer change every bar). |
| MU4 | `flp_arrange_song(project, mode="replace")` on a pop / gospel / r&b / afrobeats beat | Intro / outro play a thinned teaser of the hook (`Melody Sparse`); the hook plays it full; the Bridge sits on other chords (`bridge` in the result names the progression) with its own bass and melody, and the return to the hook feels like coming home. `musicality.weakest` names the section the check liked least: is it the one you like least? |
| MU5 | Verses in genres that put the melody in the verse (data `sections.<name>.melody`) | `Melody Call`: the motif bar of each phrase + its landing note, room for a vocal after. |
| MU6 | `flp_musicality(project)` on one of your own finished songs | Plausible section scores (a dense, repetitive hook high on hook/groove; a sparse intro is not punished for having no hook). Report songs where the score disagrees with your ears - that's data for the scorer's weights. |
| MU7 | `flp_compose(what="melody", variations=3)` | `musicality.best` names one variation: does your favourite match? (The score is craft rules, not taste.) |
| MU8 | Timing | `steps_s.generate` of a beat stays well under 1 s on the PC (Linux: ~0.02-0.05 s). |

## Ear candy: audio FX + filter moves in flp_arrange_song (2026-10-23)
Linux checks the file (tests/test_compose_fx.py): the riser clip ends on the Hook's first tick, impact / sub drop
start on it, one sampler channel per kind, one FX insert, one save. Only FL + ears can say it sounds right.

| # | Run | Expect |
|---|---|---|
| EC1 | `flh_new_song("trap", starter=true)` (or `flp_arrange_song(song, mode="replace")` on a trap beat), play into each Hook | Channels Riser / Impact / Downlifter (dark colours; one big hit per change since 2026-10-23, so no Sub Drop next to the Impact - see checklists/fx.md EC-A1..A9) on ONE mixer insert "FX" (routed to the Music Bus when the song has one); the riser swells for 4 beats and stops exactly on the Hook's downbeat (no gap, no overhang); the impact hits ON the downbeat, the 2-beat drop-out before it is silent; the downlifter falls out of the Hook into the Verse. `# PC-VERIFY` in `flp_compose_fx`: the copied sampler with the volume envelope off plays the whole WAV from its note (FX files in `<song>/_fx`). |
| EC2 | Same song, FX levels | The FX sit under the beat (insert fader: the loudest drum level of the recipe - 11 dB, i.e. -14 dB for most kits, +/- the ear_candy amount, or the genre's `ear_candy.level_db`); the sub drop doesn't swamp the 808. Say which kind is too loud/quiet - that's genre data. |
| EC3 | `flp_arrange_song(song, mode="replace")` twice | Still one channel per kind (reused, sample swapped), clips not doubled, no new FX insert. |
| EC4 | House / techno: arrange a beat, play the Build into the Drop | 16-beat riser ends on the Drop; the noise sweep peaks on the downbeat; impact on it. Not on every boundary: only Drops (and a downlifter into the Breakdown). |
| EC5 | Lofi / jazzhop / r&b: arrange, play into B / Hook and the end | A reversed crash swells into B / the Hook (the kit's own Crash sample reversed when the kit has one, else a synthesized cymbal); the song ends with a tape stop into silence on the last downbeat, the music under it cut; no riser / impact anywhere. |
| EC6 | Filter moves (genre `ear_candy.filters`): any song with an Intro / Outro (trap, pop, house ...) with FL's plugin database built | A Fruity Filter appears on the Music Bus (or the master, before the limiter if a slot is free) with automation clips "Filter intro/outro @1": the intro starts muffled (low-pass ~250 Hz) and opens over the intro; the outro closes. `# PC-VERIFY` in `_filter_plan`: Fruity Filter's param 0 is the cutoff and the 0..1 scale matches data/plugin_maps/fruity_filter.json (run `fl_plugin_map_learn` on the slot if not). |
| EC7 | Same, the build before the LAST hook (trap, pop, kpop, house, techno, dnb, amapiano, hyperpop) | Over the last 8 beats (16 for house/techno/dnb) the mix thins to a rising high-pass, then snaps fully open on the hook's downbeat (clips "Filter build cutoff / hp / lp"). `# PC-VERIFY`: the Low pass / High pass knobs are params 2 / 4 and act as LP/HP mix; after the build the filter is open low-pass again. If FL doesn't move them, set `"filters": ["intro", "outro"]` in the genre data and report it. |
| EC8 | After the outro closed the filter, press play at bar 1 | The song starts open (or muffled where the intro clip says so), not stuck closed: songs without an intro get a short "Filter reset" clip at bar 1. `# PC-VERIFY` FL holds the last automation value. |
| EC9 | `flp_arrange_song(song, ear_candy=false)` | No FX channels / files / filter clips; fills, drop-outs and stops as before. |
| EC10 | Timing | First arrangement with FX: `steps_s.fx` well under 1 s on the PC (cached WAVs on later runs, ~0 s); still ONE save + reload. |
