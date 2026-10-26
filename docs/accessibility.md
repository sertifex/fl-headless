# Accessibility

[Back to the README](../README.md)

## Accessibility - for every music lover

fl-headless is built so that you shouldn't need to hear the speakers, see the screen or use a mouse to make music:
everything works by chat, every result can be read as plain text, every picture comes with a description, and every
judgment comes with numbers *and* words. The one-time setup (installing it, and FL's MIDI settings) may still need a
hand from someone else.

Honest note: these features were designed with these needs in mind, but they haven't been tested by disabled
musicians yet. If you use fl-headless this way, please tell us what works and what doesn't
([open an issue](https://github.com/sertifex/fl-headless/issues)) - that feedback shapes what comes next.

Tell Claude what helps you - only if you want to, in your own words. It is remembered for next time (`fl_access`), and
you can change or clear it any time.

| If you... | Say something like | What changes |
|---|---|---|
| are deaf or hard of hearing | "I'm hard of hearing - describe what changed" / "I don't hear high sounds well" | `fl_describe` tells you in words how the song sounds and what changed between versions, section by section. Where you said your hearing is less reliable, measurements are trusted over listening, and "listen to check" steps come with a no-listening alternative. |
| use hearing aids or a cochlear implant | "I listen with a cochlear implant" | Tone and pitch checks lean on the measurements; safe-listening tips for your device. |
| are blind or use a screen reader | "I use a screen reader" | `fl_where` tells you where you are in FL (window, bar, section, selection, popups). Results lead with text; pictures only if you ask. |
| have low vision | "I have low vision" | Bigger pictures with large labels, high contrast and colorblind-safe colors (`fl_visualize`). |
| can't use a mouse well | "I can't use a mouse" / "I use voice control" | Claude does it with tools; anything that says "click" comes with a chat or keyboard alternative. |
| are new to music or prefer simple words | "Explain things simply" / "one step at a time" | Jargon is explained in brackets (LUFS, sidechain, EQ...); multi-step tasks go one step at a time. |

Tools that help:

- `fl_describe` - the music in words: tempo feel, key and mood, the parts and their roles, where it is whisper-soft
  and where it explodes, rhythm feel, low end you'd feel as vibration, the stereo picture - and what changed since the
  last version. It says what was measured, what was read from the notes and what is interpretation.
- `fl_visualize` - energy, loudness, spectrogram, mix balance, arrangement and piano roll as high-contrast pictures
  with alt text.
- `fl_second_opinion` - your ears and the measurements side by side, kindly.
- `fl_safe_listening` - fl-headless measures loudness for you, so there's no need to turn it up to judge; tips on
  levels and breaks (general advice, not medical advice).
- `fl_where` - a spoken-friendly map of the open FL project.
- `fl_access` - see or change what you told fl-headless about what helps you.

Nobody is asked about their body and nothing is guessed. These tools are useful for everyone: "describe what changed"
is a quick check for any producer.
