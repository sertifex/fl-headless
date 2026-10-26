# flh_extras: PC checklist

`python3 tests/test_extras.py` covers everything offline (FL renders and live tools mocked). These need a Windows
PC with FL Studio and the fl-headless bridge. Each item names the `# PC-VERIFY` spot in `flh_extras.py` where there
is one.

## 0. Main-file hooks (shipped in 2.0.0)
- `flh_extras` is in `EXTENSIONS`; `handle()` and `flh_run` log every call through `flh_recent_note`, and
  `_RECENT_CALLS` survives `flh_reload`. Verify: run two tools, then `flh_teach()` explains them in the order they ran;
  after `flh_reload` it still lists them.
- Not wired: the optional per-result teaching hint (`flh_teach_line` -> `res["teach"]`). Teaching text comes from
  `flh_teach` only.

## 1. Collaborator pack
1. Pick a project with audio clips / sampler channels, at least one third-party plugin and a journal with sections.
   Run `flh_collab_pack(project, notes="test")`. Expect a job; `fl_job()` waits for it. FL closes (saved) for the
   stems and reopens the project. **PC-VERIFY (stems call):** FL reopened the same project, the `Stems` files line
   up from bar 1 and, dropped together into a new project, sound like a full render.
2. The original project file and folder are unchanged (compare the .flp's modified time; nothing in the Recycle Bin).
3. `<song> - collab/Samples` holds exactly the samples the project uses; FL factory samples
   (`%FLStudioFactoryData%`) are not copied and README says so.
4. Copy the zip to another folder (ideally another PC), unzip, open the `.flp`. **PC-VERIFY (_collect_samples):** FL
   loads every sample from the pack's `Samples` folder, or its missing-sample dialog finds them by pointing it at
   `Samples` once. Note which happens in the issue/changelog.
5. README.txt: tempo, key, sections, each plugin with a sensible "what you need" line (native ones name the
   edition; third-party ones say the collaborator needs their own copy), stem format matches the files.
6. Run it again: a new `... (2)` folder and `... (2).zip`; the first pack untouched.
7. With FL closed: the same works (stems render via the command line path of `fl_render_stems`).

## 2. Recipes
1. `flh_recipe_list()` shows the four shipped recipes with no `missing_tools`.
2. `flh_recipe_run("clean_and_check", project=...)` runs in the background (fl_review listens/renders), and the
   result has fl_doctor's issues, the dry-run removal list (nothing removed) and the review.
3. `flh_recipe_run("quick_master", project=...)` is refused with `needs_confirm` (fl_finalize); `dry_run=true` shows
   the plan; with `confirm=true` the review, the mix proposal (nothing applied) and the final come back in one
   `fl_job` result.
4. `flh_recipe_run("vocal_session", project=...)` with a mic: the vocal insert is named/armed and the level check
   listens on the same track (`$prev.track`).
5. Save your own: `flh_recipe_save("my_bounce", [{"tool": "fl_render", "args": {"project": "${project}"}}],
   "render a check bounce")`; it lands in `%LOCALAPPDATA%\fl-headless\recipes\my_bounce.json` and runs.

## 3. Teaching mode
1. `flh_profile(set={"teaching": "beginner"})`, do a few edits/reviews, then `flh_teach()`: short plain explanations
   of the last calls in order (needs the dispatcher hook; without it the answer says "order approximate").
2. `flh_teach(topic="LUFS")`, `flh_teach(topic="sidechain", level="pro")`: short, no jargon for beginner.

## 4. Where were we
1. Open a song in FL that has a journal, run `flh_resume()` with no arguments: it picks the open project.
2. The summary names the song, key, BPM, sections, last mix/version/final, open review issues, queued lazy edits
   (if any) and three next steps; it stays short (well under a screen).

## Collab pack privacy (fix D)
- PC-VERIFY: unzip a pack on another PC (or another folder) and open its .flp: FL finds the samples through the
  RELATIVE paths "Samples\<file>" (or its missing-sample search of the project folder finds them in one click).
  The .flp must not contain C:\Users\<name> (search the file for your user name).
- samples=false: the .flp names every sample as Samples\<file>; FL's own %FLStudioFactoryData% paths stay.
