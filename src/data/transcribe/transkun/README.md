# Transkun piano add-on (optional)

`audio_to_midi mode="piano"` uses **Transkun V2** when these files are here - the most accurate piano model
fl-headless can run (notes, velocities, sustain and soft pedal). Without them it falls back to the smaller
basic-pitch model that ships with fl-headless.

The easy way: the first time you ask for a piano transcription, Claude asks whether to install it (59 MB, once);
say yes and fl-headless downloads the 7 files from Hugging Face, checks each one's fingerprint and keeps them in
%LOCALAPPDATA%\fl-headless\models\transkun (kept across fl-headless updates). Say no and it won't ask again
(you can still install it later: "install the better piano model").

By hand (developers, or offline PCs): download these 7 files from
https://huggingface.co/TuesdayCrowd/transkun-onnx/tree/main and put them in that folder or in this one:

    transkun.onnx  transkun-heads.onnx  freq2mels.f32  windows.f32  symbols.i32  params.json  LICENSE.transkun

Optional, for the developer tests: ref3b_audio.f32, ref3b_features.f32, ref3c_S.f32, ref3c_intervals.json.

Licence: MIT (c) 2021 Yujia Yan (LICENSE.transkun). Model: Yujia Yan, Frank Cwitkowitz, Zhiyao Duan, "Skipping
the Frame-Level: Event-Based Piano Transcription With Neural Semi-CRFs" (NeurIPS 2021) and Yan & Duan,
"Scoring Time Intervals using Non-Hierarchical Transformer for Automatic Piano Transcription" (ISMIR 2024).
ONNX export: TuesdayCrowd/transkun-onnx v2.0.0 (independent export, not by the authors). It runs on numpy;
installing onnxruntime makes it several times faster with the same notes.
