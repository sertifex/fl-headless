# flh_review (fl_doctor / fl_review) - PC checks

Run on a Windows PC with FL Studio and the bridge set up. Each item: what to run, what to expect.

## R. Practically silent channels, struck-chord entrances (2.2.0, PC-VERIFY)

R1. **level knob at 0 in the saved state**: on a LABS / Splice INSTRUMENT channel turn Dynamics and Expression to 0
    in its window, save the song. `fl_doctor(project)` lists a high `silent_knob` for that channel naming
    "expression (CC11)" (and the dynamics knob when its state maps it), fix `flp_plugin_settings(... params={...: 1.0})`.
    Run that fix, reopen: the channel plays at a normal level. A song with every knob at normal levels shows no
    `silent_knob`.
R2. **content not on this PC**: point an INSTRUMENT channel at a pack that isn't installed (rename the pack's folder
    in the Splice INSTRUMENT library, or open a song from another PC) - FL shows 'Content Not Found'. `fl_doctor`
    lists a high `content_missing` for that channel ("isn't in the INSTRUMENT library"), naming flp_instrument_sound.
    Rename the folder back: no issue. A FLEX channel whose pack isn't installed: `content_missing` "isn't installed in
    FLEX" (only once FLEX's preset index exists - flh_call('flex_list') builds it).
R3. **live knob names**: a song open in FL with a VST synth whose Volume / Output / Gain knob is at 0 and a channel
    playing notes: `fl_doctor(project, live=true)` names that knob ('<name>' (param N) shows '...'), fix
    fl_plugin_set_value / fl_plugin_set. On a normal song with several VSTs and FL natives: no false `silent_knob`
    (if one shows up, report the plugin, the parameter name and its text; add a word to not_level_words in
    `<state folder>/doctor/level_params.json`). With another song open in FL: the doctor's `notes` say the live
    check was skipped.
R4. **struck chords are meant to jump**: render a slow solo-piano prelude (rolled chords after quiet single lines,
    e.g. a 60 BPM intro) and run `fl_review(project, audio=<render>)`: no `abrupt_entrance` for those chords; the
    review's `notes` say "N jump(s) not flagged: struck attacks out of a decaying tail". Render a song where a pad or
    strings come in mid-section at full level: still one `abrupt_entrance` there.
R5. **review hint**: a render of a song with the R1 channel (silent): fl_review's `silent_knob` issue appears among the
    issues, and a `silent_section` names the channel in its fix.
