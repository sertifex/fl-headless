# Sheet music into FL, fast

[Back to the guide](guide.md)

The slow part of "here's a PDF, put it in FL" is reading the notes. fl-headless can't read notes by itself: Claude
reads them from sharp crops, writes them down as **score data** (one small JSON entry per bar), and fl-headless
checks every bar exactly and writes MIDI or FL patterns. Long scores go faster when the pages are split across
several helper agents that read at the same time.

## 1. Get page images

`sheet_pages` reads PNG / JPEG pages, a folder of them, or a PDF of **scanned** pages (the embedded page images are
decoded: JPEG, Flate, LZW). It can't draw **engraved (vector) PDFs** - the kind a notation program exports - nor
JBIG2 / CCITT / JPEG 2000 scans. For those, turn each page into a PNG first (about 300 dpi):

- `pdftoppm -r 300 -png score.pdf page` (poppler), or your PDF viewer's *Export as image*, or
- open the PDF in MuseScore and export MusicXML instead - then skip the reading: `sheet_import(musicxml=...)`.

Then pass the PNG files (or their folder) as `source`.

## 2. Find the systems, crop what you read

All coordinates are in a **page frame**: the page scaled to `frame` px wide (default 1000), after a small deskew.

| Call | Gives |
|---|---|
| `sheet_pages(source, page=4, systems=true)` | every system: `box`, `staff_y` (top/bottom line of each staff), `bar_x` (detected bar lines), `bars_est`, `first_bar_est` - text only |
| `sheet_pages(source, page=4, rows=2)` | system 2 as one sharp image with red bar numbers |
| `sheet_pages(source, page=4, rows=2, bars="3-5")` | bars 3-5 of system 2, zoomed (up to 4x); `next` = the following bars |
| `sheet_pages(source, page=4, crop=[120, 300, 560, 420])` | any region in frame coordinates |
| add `save="C:/.../crop.png"` | also writes the PNG (inside your song folders) |

Bar lines and counts are estimates from the picture: trust the music and the printed bar numbers.

## 3. Split the pages across helpers

`sheet_pages(source, split=3, parts=["mel", "piano_rh", "piano_lh", "strings"])` analyses every page, splits the
pages into 3 ranges with about the same number of bars, and returns one **brief** per reader (pages, estimated
bars, output file, the part names, the format, how to check). Paste each brief into a helper agent; read one range
yourself. Agree on the part names before you start - `sheet_merge` reports a part only one reader used (e.g.
`melody` vs `mel`). The brief text lives in `data/sheet/reader_brief.md`; a copy in fl-headless's state folder
under `sheet/reader_brief.md` replaces it.

## 4. The score data format (`flh-score/1`)

```json
{"format": "flh-score/1", "title": "Study", "meter": "4/4", "key": "F", "tempo": 90,
 "parts": {"cello": {"clef": "bass", "program": 42}},
 "bars": {
  "1": {"section": "A", "chord": "F", "page": 1, "system": 1,
        "mel": [[0, 1, "A4"], [1, 1, "Bb4"], [2, 2, "C5", "tie"]],
        "parts": {"cello": [[0, 4, "F2"]], "piano_rh": [[0, 2, "F4"], [0, 2, "A4"], [2, 2, "G4"]]}},
  "2": {"chord": [[0, "Dm"], [2, "C"]], "dyn": "p",
        "mel": [[0, 1, "C5"], [1, 0, "D5"], [1, 1, "C5"], [2, "2/3", "A4"], ["8/3", "2/3", "G4"], ["10/3", "2/3", "F4"]],
        "cello": [[0, 2, "D2"], [2, 2, "C2"]]}}}
```

- **beat**: where the note starts, from 0, in quarter notes. **length** in quarter notes: eighth 0.5, dotted quarter
  1.5, 16th 0.25, triplet eighth `"1/3"` (write thirds as fractions). In 6/8 a bar is 3 quarters long.
- **pitch**: the sounding note with the key signature and accidentals applied: `"Bb4"`, `"C#5"`, `"F##3"`, `"Cb4"`
  (C4 = middle C). Ottava passages: write the sounding pitch.
- A **chord** is several entries at the same beat. **Rests** are gaps. A **grace note** has length 0 (it plays as a
  short note on the beat and never overlaps the same key). A **tie** into the next bar: `"tie"` as a 4th item, and
  the next bar starts with the same pitch. Other 4th items: `accent`, `staccato`, `staccatissimo`, `marcato`.
- Parts: `mel` / `mel_low` and any other note list at the top of a bar, or under `parts`. Overlapping notes of
  different lengths inside one part (two voices on a staff) are fine: they stay one part, one FL pattern.
- Per bar, optional: `section`, `chord` (`"Dm"` or `[[beat, "Dm"], ...]`), `meter` / `key` / `tempo` (a change from
  that bar), `beats` (this bar's length: a pickup is bar `0` with `"beats": 1`), `dyn` (`"p"` or `{part: "p"}`),
  `repeat_start` / `repeat_end` / `ending`, `page` + `system` (re-read hints then name them), `text`.
- An empty bar `{}` is a bar of rests; a bar number that's missing is reported.

## 5. Check every bar (the beat sums)

`sheet_import(score=<file or JSON>, dry_run=true)` builds the score and checks it exactly like ABC: every entry's
position and length against its bar (`overflow`: a note runs past the bar line; `position`: starts outside it),
pitch names, the 1/48-beat grid, missing bars, then ABC's own checks (octave slips, ranges for the clef,
accidentals against the key, ties to nothing). Problems come back as `reread`: bar, page/system and why. A line that
covers less than its bar is only reported with `strict=true` (it's usually a rest). `score_data.beat_check` sums it
up: `lines`, `full`, `gaps` (+ a few as `gap_list`), `over`. Parts named after an instrument (`flute`, `cello`,
`piano_rh`) are also checked against its range (`outside_instrument_range`: an octave misread?).

## 6. Merge, compare

- `sheet_merge(readings=[".../score_p1-3.json", ".../score_p4-6.json", ...], output=".../score.json")` - or a
  folder - joins the readings: bars one reader has are taken; in a bar two readers both have, parts only one has are
  joined and a part both have must agree. Disagreements are listed as `conflicts` (the differing notes) and the file
  isn't written until you fix one reading or pass `prefer=<0-based index>`. It also reports overlaps, missing bars,
  meter/key/tempo mismatches, part names only one reader used, and checks the merged score (`check.reread`).
- `sheet_diff(a=<reading>, b=<reading>)` lists the bars where two readings of the same bars disagree, part by part
  (`beat 2: C5 vs D5`, `(octave)`, `length 1 vs 2`). Useful when two helpers read the same hard page.

## 7. Import

`sheet_import(score=".../score.json", project="Song.flp", channel={"mel": 0, "cello": 3})` writes one pattern per
part in ONE edit of the project (the open FL project is saved and reloaded once). Score data lands at its own bar
numbers: bar 29 of the data starts at FL bar 29 (`place_at_bar` moves it). Parts without a channel aren't read; or
pick parts with `parts=[...]`. `output_mid=` writes a MIDI file instead or as well (one track per part).
`channel="new"` makes a channel per part. On an empty project the tempo and meter come from the data. Sections and
chord symbols go into the song journal for the next steps (arranging, composing).
