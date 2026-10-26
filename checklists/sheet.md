# flh_sheet (sheet_pages, sheet_import, sheet_diff, sheet_explain) - PC checklist

Logic is tested offline (`python3 tests/test_sheet.py`: decoders, PDFs, staff/system/bar-line detection on
synthetic pages, import, journal, audio check). What only a real PC, a real client and real scans can show. Use a
copy of a song (or an empty project from `flh_new_song`), a phone photo or scan of a page of piano music, a scanned
PDF from a library site (IMSLP-style), an engraved PDF exported from a notation program, and a recording of a
simple piece you also have the sheet for.

1. **One image per result reaches Claude.** `sheet_pages(<page.png>)` in Claude Desktop: the reply shows the system
   crop as an image (not base64 text), sharp enough to read accidentals and ledger lines; red ticks sit above the
   bar lines and the red numbers count the bars of that system. A long system arrives as strips (left part on
   top, pink line between) and the text says so. (PC-VERIFY in `sheet_pages`: the dispatcher's `_image_png`.)
2. **Real scans.** For the photo, the scanned PDF and a 150 dpi + a 300 dpi scan: `systems_on_page`, staves per
   system and `bars_est` match the page (bar counts may be off by one on cluttered systems - note which). A
   slightly rotated photo is straightened (`deskewed_deg`). Piano music with braces counts 2 staves per system;
   a choir score with separate bar lines per staff may come out as single staves (`note_systems` says so).
3. **PDF variants.** The scanned PDF's pages decode (most are DCT or Flate); a PDF with JBIG2 / CCITT pages (many
   library scans) gets the "export as PNG" message, an engraved PDF gets the MusicXML / MuseScore advice, a
   password-protected one the "encrypted" message. Time the first call on a 20-page scan (decode + layout of one
   page; later systems of the same page come from the cache).
4. **Progressive / CMYK JPEGs.** A progressive JPEG photo decodes (speed: a 12 MP photo takes a few seconds in pure
   Python - note the time); a CMYK JPEG from a print workflow looks right in the crop (gray approximation).
5. **Import into the open FL project.** `sheet_import(abc=..., project=<open song>, channel={...})`: FL saves once,
   reloads once (or queues in lazy mode, then `flh_sync`), and shows one pattern per voice named "<title> - <voice>",
   placed at bar 1 on free playlist tracks; the notes play at the score's rhythm. On an EMPTY project the tempo and
   time signature change to the score's (`song_settings`); on a song with notes they stay and `notes` says so.
   (PC-VERIFY in `sheet_import`: flp_new_pattern + flp_add_notes + flp_set_tempo + flp_project_info time signature
   in ONE flp_edit.)
6. **channel='new'.** New channels appear at the end of the Channel rack, named after the voices, with the sound of
   the cloned channel; the patterns use them.
7. **Pickup bars.** A piece with an upbeat: the pickup notes sit at the END of FL bar 1, so the first full bar
   of the score starts on FL bar 2 (sheet_notation's pickup="pad"). Check it against the sheet and the playback.
8. **Tempo changes.** A MusicXML with a ritardando / a new tempo mark: the tempo lane gets the changes
   (flp_tempo_write) on an empty project; FL's tempo display follows them while playing.
9. **Reference audio.** `sheet_import(abc=<your reading with one deliberately wrong bar>, reference_audio=<the
   recording>, dry_run=true)`: that bar is in `reread` with "recording disagrees"; a correct reading gets few or no
   flags (report false flags with the instrument and style - piano, guitar, voice, band). Try a recording with
   rubato and one with a different tempo from the score's Q:.
10. **Journal.** The song's `.journal.json` has a "sheet" entry (source type, file, validation counts, patterns);
    a second import keeps the first in `history`.
11. **MIDI.** `output_mid` opens in FL (drag into the playlist) with the right tempo, meter and one track per voice.
