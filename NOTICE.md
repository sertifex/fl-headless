# fl-headless: copyright and licenses

Copyright (C) 2026 sertifex

## The code: GPL-3.0-or-later

fl-headless is free software: you can redistribute it and/or modify it under the terms of the GNU General Public
License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later
version.

fl-headless is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with fl-headless (the file `LICENSE`). If
not, see <https://www.gnu.org/licenses/>.

In plain words: it's free for everyone, including producers who sell the music they make with it. You can change it
and share it, but anything you share that's based on it must stay free and open under the same license, with the full
source.

## The name and the logo: not covered by the GPL

The name "fl-headless" and the fl-headless logo are the owner's and are **not** licensed under the GPL. The logo files
(`src/data/theme/logo.png`, `src/data/theme/fl-headless background.jpg`, `src/data/theme/thmfl-headless.png`, and the
README banner `docs/banner.png`) are provided only
for use with fl-headless itself (they ship as its default FL Studio theme). You may not modify them, use them for
another product or project, or use the name or logo in a way that suggests your project is fl-headless or made by its
owner. No trademark license is granted. A fork must use its own name and logo.

The theme's color settings (`src/data/theme/fl-headless.flstheme`) are part of the code and are GPL-3.0-or-later.

## Bundled third-party model: basic-pitch (Apache-2.0)

`src/data/transcribe/nmp.onnx` is the note-transcription model from Spotify's basic-pitch
(<https://github.com/spotify/basic-pitch>), Copyright 2022 Spotify AB, licensed under the Apache License 2.0 (full
text: `src/data/transcribe/LICENSE-basic-pitch.txt`). It is shipped unmodified. fl-headless's `fl_piano_tx.py`
re-implements basic-pitch's note post-processing in numpy. The Apache-2.0 model can be combined with GPL-3.0 code;
the model itself stays under Apache-2.0.

## Optional add-on: Transkun V2 (MIT)

When installed (not shipped: the user is asked, and on a yes fl-headless downloads the files from Hugging Face into
its own folder; see `src/data/transcribe/transkun/README.md`), piano transcription uses
Transkun V2 by Yujia Yan, Frank Cwitkowitz and Zhiyao Duan (https://github.com/Yujia-Yan/Transkun), MIT licence,
Copyright (c) 2021 Yujia Yan, in the ONNX export published at huggingface.co/TuesdayCrowd/transkun-onnx.
`fl_piano_tk.py` re-implements its front end, semi-CRF decoding and segment stitching in numpy.

Both piano models were trained by their authors on the MAESTRO dataset (CC BY-NC-SA 4.0); fl-headless is free and
non-commercial, and the model licences themselves are MIT / Apache-2.0.

## Not affiliated

fl-headless is an independent project for FL Studio. It is not made, endorsed or supported by Image-Line. FL Studio
and FLEX are trademarks of Image-Line Software. It ships no Image-Line files.
