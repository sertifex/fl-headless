# fx (flh_fx.py: fl_make_fx, make_fx) - PC checks

Prerequisite: `flh_fx` in `EXTENSIONS`; FL Studio open with a song inside FLH_ROOTS.

## 1. Make each kind
`fl_make_fx(kind=<kind>, project=<song.flp>)` for riser, downlifter, impact, reverse_crash, sub_drop, tape_stop,
noise_sweep, cymbal_swell, boom, reverse_swell, wind, rain, vinyl (and reverse_crash / reverse_swell with `sample=`
a crash / a rendered chord from your own library).
- Files appear in `<song folder>/_fx/` named `<Kind> <beats> beats <bpm> bpm.wav`; running the same call again
  returns `cached: true` and the file's date does not change.
- # PC-VERIFY: each WAV loads with flp_load_sample (one note on C5 plays it) and with flp_add_audio_clip; the clip
  is exactly `beats` long on FL's grid at the song's tempo (zoom in: it ends on the bar line).

## 2. Listen in the song
- riser / reverse_crash / tape_stop placed `beats` before a drop: the peak lands exactly on the drop's downbeat.
- downlifter / impact / sub_drop placed on the downbeat: they start with the hit; the sub drop starts on the song's
  key root (no clash with the bass) and falls an octave.
- noise_sweep placed `beats/2` before a section change: its brightest point is on the downbeat.
- No clicks at starts/ends; nothing clips (peak -1 dBFS); the riser sounds like a riser (noise + rising tone), the
  impact like a deep boom. These are synthesized: note any kind that sounds cheap.
- cymbal_swell: a soft mallet-roll swell that rises into the downbeat with no crash at the end; boom: a deep, soft,
  dark low hit with a long tail (softer than impact, no click); reverse_swell: the key's chord (or your sample=)
  "sucks in" backwards and lands on the downbeat, in the song's key (no clash with the chords that follow).
- wind / rain / vinyl: put one on a sampler, loop the pattern (a note every `beats`): # PC-VERIFY no click or bump
  at the loop point, no harsh highs, nothing that sounds like a fault; they must still be readable ~20 dB under
  the music (they are peak-normalised to -1 dBFS like the others: the level comes from the Texture insert).

## 3. Ear candy in flp_arrange_song (data/ear_candy.json, flh_compose; 2026-10-23)
Linux checks the file (tests/test_compose_fx.py): amounts, purposes, rules, snare build notes, delay-throw clip,
bed tiling. Only FL + ears can say it sounds right and sits under the music.

| # | Run | Expect |
|---|---|---|
| EC-A1 | `flp_arrange_song(song, mode="replace")` on a trap beat (normal) | Into each Hook: ONE riser, ONE big hit (impact; the sub drop is listed under `ear_candy.skipped` "one big hit per change"), a snare build; the result's transitions each have a `purpose` ("into Hook at bar 21"). Nothing starts or ends in the middle of a section (only delay throws sit on a phrase's last note). |
| EC-A2 | Same song with `ear_candy="subtle"`, then `"lots"` | subtle: one effect per change (two into the climax = the last highest-energy section), the FX insert 6 dB lower; lots: more of the genre's fitting kinds (reverse crashes out of hooks, a half-beat gap before the drop), FX insert +2 dB. The amount is remembered in the journal (`compose.ear_candy`) for the next arrangement. # PC-VERIFY the "FX" insert fader shows the level in `ear_candy.fx_level_db` on a NEW insert (an existing insert keeps your fader). |
| EC-A3 | Levels by ear (trap, house, lofi, cinematic) | The FX sit UNDER the beat (normal = loudest drum level - 11 dB, e.g. -14 dB under a -3 dB kick): audible, never on top of the kick/snare. Say per genre/amount if they're too loud/quiet - that's `below_beat_db` / `level_offset_db` / the genre's `ear_candy.level_db` in data. |
| EC-A4 | Snare build (trap / pop / house / dnb / techno / hyperpop) | Channel = the kit's snare (else clap); over the last 1-2 bars before the hook/drop: 8ths, then 16ths, then 32nds, getting louder; it stops where a drop-out / stop / gap starts (trap: 6 beats, then the 2-beat silence). Own playlist track "Snare Build <beats>". Sounds like a build, not a machine gun: the velocity ramp (40 -> 118) may need per-kit taste. |
| EC-A5 | Delay throw: a song whose lead plays into its own mixer insert, FL's plugin database built | A Fruity Delay 3 appears on the lead's insert (or yours is reused) and one automation clip "Delay throws @1": wet 0 everywhere, up on the lead's last note before the chosen changes (subtle 1 / normal 2 / lots 4 per song, the climax first), the echo rings into the next section and dies within a bar. # PC-VERIFY in `_throw_plan`: Fruity Delay 3's "Output wet" is param 23 and the 0..1 scale matches data/plugin_maps/fruity_delay_3.json (run `fl_plugin_map_learn` on the slot if not); the default Delay 3 time is tempo-synced and short enough; wet 0 is silent. |
| EC-A6 | Delay throw: lead into the Master, or only a "Delay" send track | Nothing written for the throws; `ear_candy.planned` lists each throw (bar, purpose, "~35% wet") and the notes say why. # PC-VERIFY (future): FL's mixer send-level automation id - send knobs can't be automated in the .flp today, so the send route stays planned only. |
| EC-A7 | Texture beds: lofi / jazzhop (vinyl), ambient / cinematic (wind) | A channel "Vinyl" / "Wind" on its own "Texture" insert (fader ~18 dB under the loudest music level, -6 dB more at subtle), clips tiling ONLY the Intro / Outro (normal/lots also Breakdown / Bridge) in 1-4-bar loops, never under a Verse/Hook. # PC-VERIFY: the loop points don't click; the bed's first note at an intro start is not an abrupt noise onset (a fade-in may be needed - say so); the sampler with its envelope off plays the loop to its note's end and nothing rings into the next section. |
| EC-A8 | Cinematic song with `ear_candy="synthetic"` (asked for: the old set) | `ear_candy.mode` = synthetic, `profile` = "synthetic_cinematic (...)", amount subtle: a cymbal swell into a Build, a reverse swell + a soft boom on the climax downbeat, wind under intro and outro. Without `"synthetic"` a cinematic / ambient / piano-led song gets the organic mode (EC-O*). |
| EC-A9 | `flp_arrange_song(song, ear_candy=false)` and `flh_new_song(..., ear_candy="subtle")` | false: no FX / bed / snare build / throws / filters; the new song is arranged at subtle (journal `compose.arrangement.ear_candy`). |

## 4. Organic ear candy (real instruments as notes; data/ear_candy.json `organic`, flh_compose `_organic_plan`)
Default for cinematic / ambient songs, songs whose journal role / style says piano-led and collections whose profile
style says the support must sound real / stay faint (or `ear_candy: "organic"` in the profile). No audio is made: the
moves are notes on channels the song already has. Linux checks the notes, the places and the ramps
(tests/test_compose_fx.py); only FL + ears can say they blend.

| # | Run | Expect |
|---|---|---|
| EC-O1 | A piano-led piece with strings, timpani, choir (`flp_arrange_song(song, mode="replace")`) | `ear_candy.mode` = organic, amount subtle: ONE move per section change, each in the bars before the change and ending on its downbeat (a timpani roll lands ON it, one beat); the climax always gets one (a string swell first). No `_fx` folder, no new instrument channel; one automation clip "<Strings> swell @1" per swelling channel. `missing` names a move whose instrument the song lacks and the owned instrument that would play it. |
| EC-O2 | Listen: string swell | The voices enter one after another on the chord the music holds and grow into the downbeat; it sounds like the section's strings breathing in, not a synth pad. # PC-VERIFY in `_organic_plan`: the channel's VOLUME knob follows "<name> swell @1" (dips ~40 % at the swell's start, back to YOUR level on the downbeat and held there); a library that ignores FL's channel volume still swells by velocity. Say if a CC1/CC11 expression ramp would be better for your library. |
| EC-O3 | Listen: timpani roll / suspended-cymbal roll | Soft, rising, landing on the downbeat (timpani on the key's tonic or dominant, in 40-55 = E2-G3); the cymbal roll on your cymbal / kit crash at the key the channel already uses (a multi-percussion instrument: key 49 / its own most-used key - check it's the cymbal). A sampler crash re-triggered fast may sound like a machine gun: say so (then `kinds=["cymbal_roll"]` out, or a real sus-cymbal patch). |
| EC-O4 | Listen: choir breath (into the climax) and piano pickup (into a new section) | The choir swells in for ~1 bar on the held chord; the pickup is 3-4 rising chord tones on the piano's own channel in the last beat (only where the piano isn't moving there), softer than the piano around it. |
| EC-O5 | Levels by ear (subtle, then `ear_candy="organic:normal"`) | Every move sits UNDER the piano: velocities stay below the piano's median (`ear_candy.piano_velocity`, cap `velocity_cap`; subtle x0.85). Normal: two moves per change (one swell + one roll / pickup). If any move is too loud / too present, that's `organic.levels` / a move's `peak_vel` in data. |
| EC-O6 | Collection profile: `flh_profile(set={"style": "piano is the star, support layers faint, sound real"})` / `{"ear_candy": "off"}` / `{"ear_candy": "organic"}` on a trap beat | style words: organic for every song of the collection (`mode_from` says which words); "off": no ear candy unless the call / song journal asks; "organic": the trap beat gets organic moves too (most skipped: it has no strings - said in `missing`). Beats without such a profile keep the synthetic FX. |

## 5. Taking ear candy out: flp_remove_fx
| # | Run | Expect |
|---|---|---|
| EC-R1 | `flp_remove_fx(song, dry_run=true)` after an arrangement with FX | Lists the FX channels, patterns (Riser, Impact, Snare Build 8 ...), automation clips, the plugins it loaded (Fruity Filter / Delay 3) and the inserts it named; `files` = the unused `_fx` WAVs (count, MB). Nothing changes. |
| EC-R2 | `flp_remove_fx(song)` with the song open in FL | ONE save + reload: the FX channels, their patterns and clips, the snare builds, throws / filter / swell clips and those plugins are gone; your own channels, patterns and effects untouched. # PC-VERIFY: the "FX" / "Texture" insert shows FL's default name again (its name set to ''), its fader and routing are left as they were. The journal says ear candy off (next arrangement adds none). |
| EC-R3 | `flp_remove_fx(song, recycle_files=true)`, then `flp_remove_fx(song, undo=true)` | The `_fx` WAVs no project in the folder uses go to the Recycle Bin (restorable); undo puts the song and its journal back (the FX channels reference the recycled WAVs: restore those from the Recycle Bin, as the result says). `kinds=["wind"]` / `kinds="organic"` remove only those. |

