# flh_sampler - PC checks (FL 2026, Windows)

Setup: a project with a Sampler channel holding a drum one-shot or an 808 (e.g. channel 0), open in FL with the
fl-headless bridge connected. Run each through `flh_call`.

1. Read: `flp_sampler_settings(project, channel=0)` - envelope off, sustain ~39 %, cutoff 100 %, filter fast_lp,
   declick as FL shows in Channel settings (SMP tab). The values match what FL shows.
2. Envelope (event 218, 2nd = volume): `flh_call` -> `sampler_shape(project, 0, {"decay_ms": "=150", "sustain_pct": 0})`
   through a small wrapper, or `flp_sampler_settings(project, channel=0, set={"decay_ms": 150, "sustain_pct": 0})`.
   FL reloads once; Channel settings > INS > Volume: the envelope LED is on, delay/attack/hold at minimum, sustain 0,
   decay shorter. The drum sounds shorter. PC-VERIFY the times: hover ATT / DEC / REL and read FL's hint - if
   `attack_ms=50` is not ~50 ms, adjust `env_time.max_ms` / `exp` in `data/sampler/curves.json`.
3. Filter (event 219 +12/+16/+20): `set={"cutoff": 2000, "resonance": 30, "filter_type": "lp"}` - INS > Filter shows
   Mod X lower, Mod Y ~30 %, type LP; it sounds darker. Hz are estimated (FL shows %): listen; tune `cutoff` in the
   curves file if 2000 Hz is far off.
4. Pitch (event 215 +100, same field as flp_audio_clip_settings' PITCH): `set={"pitch_st": 12}` - SMP > Time
   stretching PITCH shows +1200 cents; the sample plays an octave up (resample mode: shorter too).
5. Reverse (event 70 bit 2): `set={"reverse": true}` - SMP > Reverse ticked.
6. Declick (event 215 +83): `set={"declick": "transient"}` - SMP > Declicking mode shows Transient. PC-VERIFY.
7. Cut itself (event 132 u16 cut, u16 cut by): `set={"cut_itself": true}` - Misc > Cut and Cut by show the same new
   group; a new note cuts the ringing one (try an open hat). PC-VERIFY.
8. Undo: `sampler_shape` returns `undo`; `sampler_undo(project, 0, undo)` puts every changed control back exactly
   (save in FL and compare bytes with the file from before).
9. Live (bridge command `sampler_live`, script version 5): `sampler_live(0, pitch_cents=-30)` moves the channel's
   Pitch knob without a reload; `sampler_live(0, cutoff=0.5)` moves the filter Mod X knob to ~50 %. PC-VERIFY the
   REC value scale (FromMIDI_Max with REC_MIDIController) and that REC_GetValue reads it back; if not, the reply has
   cutoff/resonance None and the file route still works.
10. Refusals: a plugin channel (e.g. a VST) answers "not a Sampler or audio clip: shape a plugin with fl_sound_shape".
