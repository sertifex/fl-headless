# flh_vocal: PC checklist

The offline tools (fl_vocal_comp, fl_vocal_clean, fl_vocal_double, fl_vocal_chain without apply, fl_vocal_balance
with vocal_wav + beat_wav) are covered by `python3 tests/test_vocal.py` on any OS. You need a Windows PC with FL
Studio, a microphone / audio interface and the fl-headless bridge script for everything below. Each item names the
`# PC-VERIFY` spot in `flh_vocal.py`.

## 0. Bridge commands (shipped in 2.0.0 in fl_script/device_flheadless.py)
`mixer_info`, `mixer_arm`, `mixer_peaks` and `rec_setup` are in the bridge script's `COMMANDS`; flh_vocal still falls
back when an older script answers "unknown command". The scripting API has **no** call to pick an insert's audio
input, set the playlist time selection, or load a plugin. Verify on the PC:
- After updating the script (`fl_bridge_setup(action="update_script")`), `fl_vocal_setup` reports `armed: true` and
  the insert's record button lights up; `fl_vocal_level_check` reads the insert's own peak meter (not the speaker loopback).
- PC-VERIFY `rec_setup`: FPT_PunchIn / FPT_PunchOut set the punch points at the song position in song mode. If they
  don't, report it: `rec_setup` must then be taken out of `COMMANDS` (flh_vocal reports punch as unavailable and
  records from the pre-roll) rather than shipping a no-op.

## 1. fl_vocal_setup
1. Open a song, call `fl_vocal_setup(input="In 1")`.
   - Expect: a free insert (no channel routed to it, name "Insert N", no effects) renamed "Vocal" with a dark plum
     color; `armed: true` once `mixer_arm` exists (the insert's record button lights up), else `arm_how` text.
   - PC-VERIFY `_DEFAULT_NAME`: an unnamed insert reads back as "Insert N". If FL returns "" or a localized name,
     free inserts are still found when the regex matches it.
2. `audio`: device / driver / sample rate / buffer read from `HKCU\Software\Image-Line\FL Studio NN\Devices\Audio
   output`. PC-VERIFY `audio_device_report`: the value names for ASIO and FL Studio ASIO, and that "Buffer length"
   is in samples (buffer_ms = buffer / rate). Compare with Options > Audio settings.
3. `input.how`: PC-VERIFY that the instruction (insert selected > input selector at the top right of the mixer)
   matches FL 2024/2025's mixer.
4. Run it again: it reuses the journal's track (`picked: already set up (journal)`).

## 2. fl_vocal_level_check
1. With the mic input set on the vocal insert and the fader at 0 dB, call `fl_vocal_level_check(seconds=5)` and sing
   the loudest line. PC-VERIFY `mixer.getTrackPeaks`: the insert's meter follows the input while FL is stopped
   (input monitoring), values 1.0 = 0 dBFS.
   - Too hot / clipping -> `verdict: too hot`, negative `change_db`; quiet -> `too quiet`; silent -> `no signal`.
2. `play=true, start_bar=...`: the song plays while you sing and stops afterwards.
3. Without the `mixer_peaks` command: method says "speaker loopback" (includes the fader and master).

## 3. fl_vocal_record
1. `fl_vocal_record(start_bar=9, end_bar=17, takes=3)` (background job; `fl_job` for the result).
   - PC-VERIFY at the `transport play` line: record mode + play records the armed insert. If FL shows its "what do
     you want to record" popup the tool stops and reports it (it never answers it): choose audio only in FL's
     record menu and rerun.
   - Expect 3 passes from bar 8 (one bar pre-roll), 3 new files, the new audio clip channels renamed
     "<song> Vocal take 1..3" (PC-VERIFY: each pass appears as a new audio clip channel, in order), and
     `vocal.takes` in the journal with paths and `starts_at_bar: 8` (9 when FL's punch range is set: PC-VERIFY that
     punched takes start at the punch-in bar).
   - PC-VERIFY `_recorded_dirs`: where FL writes recordings (Documents\Image-Line\FL Studio\Audio\Recorded by
     default; the project's data folder when set). If none is found the warning says so; `FLH_RECORDED_DIR`
     overrides.
2. Record mode and song/pattern mode are restored afterwards; nothing else in the project changes.
3. With `rec_setup`: `punch: "set in FL"` and FL's punch markers sit at start/end bar.
4. Edison (PC 2026-10-23: the take went into Edison and a modal Edison 'Save As' - Edison\Data\Samples,
   'untitled_2', 'Microsoft wave file' - was left open). fl_vocal_setup never loads Edison; FL does when its record
   filter is 'into Edison', or the user put Edison on the insert.
   - Load Edison on the vocal insert, run `fl_vocal_setup`: `edison_on_insert: ["Edison"]` + a warning.
     PC-VERIFY `_edison_on`: mixer_info's effects list names it 'Edison'.
   - Record 2 bars with Edison there (or FL's record filter set to 'into Edison'): if Edison asks Save As, the
     tool types `<song folder>\Recorded\<song> Vocal take N.wav` into it (`takes[0].source: "edison"`); with no
     song it cancels. Expect: no dialog left open in FL, `edison` + the EDISON_FIX warning (record filter, Edison on
     the insert). PC-VERIFY `_handle_edison_save`: what makes Edison open that Save As (FL's save shortcut reaching
     a focused Edison window?), that the typed path saves the take, and Edison's take starts at the pre-roll bar.
   - PC-VERIFY `_edison_dirs`: Edison's own folder (<user data>\Edison\Data\Samples; also one level down in FL
     2026's nested user data) - a take Edison saved there is found as a fallback.
   - With no take found the warning lists the 4 things to check (arm button, input 'In 1' + meter, record filter /
     Edison, audio device).
   - PC 2026-10-23 (2nd run): FL loaded Edison onto the insert DURING the take, no Save As, take only in Edison's
     memory, check took 620 s. Now: FL's record filter 'into Edison', no Edison on the insert before, record 2 bars.
     Expect within seconds: `takes: []`, `edison: ["Edison"]`, `edison_loaded_by_fl: true`, `stopped` ("went into
     Edison"), `next_step` (Record button > 'Audio, into the playlist as an audio clip', remove Edison). The check
     `vocal.record_take` fails with that next_step; its evidence has `record_s`, `steps_s`, `passes`,
     `person_wait_s` - PC-VERIFY where the 620 s went (person's wait vs the tool's steps).
   - Then let fl_pc_check restore your song with Edison's window still open: PC-VERIFY `_focus_main_keys` makes
     Ctrl+O open FL's project Open (not Edison's 'Load sample'); if Edison's dialog comes up anyway it's closed
     (`_audio_open_dialog`) and FL's Open is tried once more; `restored.ok` true (or `restored.tries` says why).

## 4. fl_vocal_comp / fl_vocal_clean / fl_vocal_double (offline; run on real takes)
1. `fl_vocal_comp(takes=[...3 recorded takes...], project=...)`: listen at `switch_points_s`; crossfades must be
   inaudible. Check the decision table against your ears on a few phrases. Key comes from flh_score / journal /
   the project's notes.
2. `fl_vocal_clean(wav=...)`: listen at the reported times. Watch for false clicks on hard consonants (t, k) and on
   vocal fry; tune with `amounts={"click_k": 6, "click_ratio": 2.2}` and report the values that work.
3. `fl_vocal_double(kind="double")` should sound like a second performance; `third_up` / `harmony` follow the key;
   `octave_up` sounds processed (expected; keep it low).

## 5. fl_vocal_chain(apply=true)
1. `fl_vocal_chain(style="rap", apply=true, track=<vocal insert>, project=...)`.
   - PC-VERIFY at `flp_load_plugin`: FL's plugin-database `.fst` (Presets\Plugin database\...\<Name>.fst) loads the
     stock plugin in its default state into the free slots (occupied slots are never touched).
   - PC-VERIFY at the control-setting line: through flh_mixeng's maps the EQ band types (High pass / Peaking /
     High shelf), frequencies, gains, compressor threshold/ratio/attack/release, Limiter "Comp" mode and the
     Multiband high band land on the right knobs; `by_hand` lists what's left (band slope, high band ratio,
     saturation amount).
   - Ownership: Fruity Edition users must not get Maximus / Pitcher / NewTone loaded (they are only suggested when
     owned); free tuners are only named, never downloaded.
2. `verify_listen=8`: plays the vocal alone before and after and reports flh_mixeng's intent check (less_muddy,
   less_harsh, more_compressed, clearer). Other inserts' mute states are restored afterwards.

## 6. fl_vocal_balance
1. Offline: `fl_vocal_balance(vocal_wav=<vocal stem>, beat_wav=<beat stem>, style="rap")` with stems rendered from the
   same start: per-section `vocal_over_beat_lu` and a fader move.
2. Live (no wavs): two fl_listen passes (vocal alone, beat alone). PC-VERIFY `_live_balance`: section names and
   levels match the song; mute states restored.
3. `apply=true, beat_tracks=[...]`: the vocal fader moves by `vocal_move_db`; PC-VERIFY the "Vocal duck N"
   automation clips dip the beat inserts by `duck_db` under each phrase (attack 50 ms, release 250 ms) and line up
   with the vocal clip (`vocal_start_bar`).

## Chain apply safety (fix D)
1. Put a Parametric EQ 2 on the vocal insert and dial in a band yourself. `fl_vocal_chain(apply=true)`: the result
   lists it under `kept_yours`, its settings are untouched, a new EQ goes into an empty slot.
2. `replace=true` reconfigures it; `fl_vocal_chain(revert_last=true)` puts every knob back (plugins stay loaded).
3. `fl_vocal_clean` on a take with words starting with "h" ("hello", "how"): the /h/ is kept (listed under
   `kept_as_consonants_s`); real breaths with a pause after them are reduced; `spans_s` lists exact cut spans.
