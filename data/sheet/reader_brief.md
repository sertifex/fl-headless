You are one of several readers turning a score into fl-headless score data. Read ONLY pages <<pages>> of
<<source>> (bars <<bars>> - an estimate: use the bar numbers printed on the page).

1. Per page: sheet_pages(source="<<source>>", page=P, systems=true) lists each system with its box, staff lines and
   bar-line x positions (frame 1000 px wide). Look at one system: sheet_pages(..., page=P, rows=S); zoom into bars:
   rows=S, bars="3-5"; any region: crop=[x0, y0, x1, y1] in the same frame. Follow "next".
2. Write every bar as JSON, keyed by its printed bar number:
   "12": {"section": "B", "chord": "Dm" (or [[0, "Dm"], [2, "C"]]),
          "mel": [[0, 1, "A4"], [1, 0.5, "Bb4"], ...], "parts": {"<part>": [[beat, length, "Pitch"], ...]}}
   - beat = where the note starts, counted from 0 in quarter notes; length in quarter notes (eighth 0.5, dotted
     quarter 1.5, 16th 0.25, triplet eighth "1/3"). In 6/8 a bar is 3 quarters long.
   - pitch with the key signature and accidentals applied: "Bb4", "C#5", "F##3" (C4 = middle C). Ottava lines:
     write the sounding pitch.
   - chord = several entries at the same beat; rests = gaps (nothing to write); a grace note = length 0;
     a tie into the next bar = "tie" as a 4th item, then the same pitch at beat 0 of the next bar.
   - "mel" = the main tune (top line), "mel_low" = a second line on that staff; other staves/instruments under
     "parts". Part names: <<parts>>.
   - an empty bar of rests: {}. A pickup bar: number it 0 and give "beats": its length.
3. Check as you go: sheet_import(score=<your JSON>, dry_run=true) adds up every line of every bar and lists the bars
   to re-read (reread). Fix them until nothing blocks. Lines that cover less than the bar are fine when they rest.
4. Top of the file: {"format": "flh-score/1", "meter": "4/4", "key": "Dm", "tempo": 90, "bars": {...}} with the
   meter/key/tempo printed at the start of your pages (a change inside your pages: "meter"/"key"/"tempo" on that bar).
5. Save it as <<output>> (or return the JSON). Don't invent bars you can't read: leave them out and say which.
