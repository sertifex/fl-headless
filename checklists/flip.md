# flip (flh_flip.py: fl_flip_sample) - PC checks

Prerequisite: `flh_flip` added to `EXTENSIONS`; FL Studio open; a soul / jazz / funk sample (WAV, MP3 or FLAC) you
are allowed to use, somewhere readable; FLH_ROOTS set.

## 1. Dry run (no FL needed)
`fl_flip_sample(sample=<file>, dry_run=true)`
- measured.tempo is the sample's tempo (or half/double: give `sample_bpm=` and re-run if wrong); measured.key is
  plausible (relative major/minor may swap).
- chops: 6 rows with from_s/to_s; open the sample in an editor (or Edison) at those times: each starts right on a
  hit (no clipped attack, no tail of the previous note), ends on the next hit or a quiet point (no cut word/note).
- Nothing was written: no new folder, the project's date unchanged.

## 2. New song (project omitted)
`fl_flip_sample(sample=<file>, genre='boombap')` (then style='soul', 'lofi', 'stutter', 'call-answer')
- A new song opens in the FL window that was already open (no second FL), at song_bpm.
- Channel rack: 'Chop 1'.. (and 'Chop N stutter') sampler channels, dark colors, each on its own named mixer
  insert. # PC-VERIFY: each chop channel loads its WAV from <song>/chops/<sample>/ and plays it as cut on C5.
- Pattern 'Flip' on its own playlist track ('Flip'), placed under the drums in the main section.
  # PC-VERIFY: the Flip clips sit under the drum block; no chop rings into the next one.
- Press play: the loop grooves with the drums; the stutter rolls land on the grid; no clicks at chop starts/ends.
- soul: chops sound sped up (brighter, 'chipmunk' voices); lofi: slowed, warmer.
- Bass (if the genre's song has one) plays each chop's root - listen for wrong notes when a chop's chord is
  ambiguous (the root is a guess).

## 3. Into the open song
`fl_flip_sample(sample=<file>, project=<the open .flp>, style='soul')`
- ONE save + reload of the open project (no FL restart, no 'save changes?' popup left open).
- The new channels/pattern appear after the song's end (or in its main section for an fl-headless song), drums
  under it when the song has kick/snare channels, otherwise the note 'no drums (...)'.
- A second flip of the same sample: a new folder 'chops/<sample> 2', pattern 'Flip 2'; the first flip still plays.
- # PC-VERIFY: a chop channel copied from an existing sampler (its envelope/filter came along) still plays the
  whole chop (if an envelope cuts it short, say so: the chop channels should then start from FL's default sampler).

## 4. Listening (a human decides)
- Are the picked bits actually the good ones? (Re-run with seed= for other picks; tell us when a clearly better
  phrase was skipped - include its start time - so the scoring can learn.)
- Pitch shift quality on big jumps (+/-5 st): no metallic artefacts (it is plain resampling: speed and pitch move
  together, like an MPC/SP-1200).
- Disk: only the used chops are written (a few small WAVs); nothing next to the source sample.

## 5. Melodic loops (keys / piano / guitar) - 'does it sound like a sample?'
`fl_flip_sample(sample=<a sparse piano/keys loop>, genre='rnb')`
- measured.kind = 'melodic', tempo 'snapped to the file length (N bars)'; flip.style = 'loop-intact' (or 'loop'
  when the loop has weak/noisy bars), pitch_st 0, style_why says why. Not chipmunk soul.
- Press play: the loop sounds like the original (no chord cut short); the roll at the end of the last bar lands
  back on the 1. With keep='chops': phrases A B A C, every chord rings into the next hit; a half-phrase + repeat
  in the last bar.
- levels: chops_over_drums_db 3-6; the chop inserts' faders in FL's mixer show chop_fader_db, drum inserts are
  lowered by drums_tucked_db when given. # PC-VERIFY: after the write the chops are clearly the loudest musical part
  (~3-6 dB over the drums) in FL's own render; a copied sampler's channel volume (not counted) may move this.
- chops[].chords / chord names are spelled in the song's key (Db, not C#, in Ab major); the bass plays each chord's
  root (or the sample's own low note under an inversion like Dbmaj7/C) - listen for rubs.
- Disk: the intact loop is one long chop WAV (~the loop's length) plus the variation's chop(s); nothing else.

## 6. Ring through (2026-10-23: 'audible gap at chop C')
`fl_flip_sample(sample=<a keys/soul loop>, genre='rnb', keep='chops')` and `style='lofi'`, tempo matched (new song)
- Press play: no chop stops before the next hit (no silence between phrases) unless the style is a stab style
  (boombap / stutter keep their gaps). A chop used in slots of two lengths shows as 'Chop 3' + 'Chop 3b' (the same
  chop cut shorter); chops[].rings_to_s when a chop rings on past its cut to the next hit.
- # PC-VERIFY: FL's sampler plays each chop WAV to its end regardless of the note length (one-shot), so the 'b' copy
  is what stops the ring at the shorter slot - if FL cuts samples at the note end, the copies are harmless extras.
- notes[] lists chops that still stop early (the sample's next hit comes sooner than the song's grid: tempo mismatch,
  no time-stretch) - listen there.
