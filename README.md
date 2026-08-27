# Referat

A personal meeting recorder for Windows 11. It sits in the system tray, records
your microphone and your system audio as two separate streams, and — entirely
offline — transcribes them with `faster-whisper` and separates the remote
speakers with `pyannote.audio`. Each meeting lands in `~/Meetings` as a folder
with the raw audio, a timestamped speaker-labeled `transcript.md`, and a
`meta.json`.

There is no GUI beyond the tray icon and no automatic summarization. Notes are
written afterwards, on demand, by running Claude Code against the transcripts.

## Status

Build step 5 of 9 — the tray app runs, the two hotkeys drive the state machine,
and meetings are recorded into a folder holding `mic.wav`, `system.wav` and a
`meta.json`, written crash-safely and left playable even if the process is killed
mid-meeting. Windows will not idle-sleep out from under a meeting either — the
hold is taken while recording or paused and dropped on stop, so a long
transcription never keeps the laptop awake.

Stopping a meeting now transcribes it: `faster-whisper` large-v3 on the GPU,
falling back to CPU + medium, on a background thread that a new meeting can start
straight through. Both channels go through it, and `transcript.md` interleaves
them chronologically with elapsed `[HH:MM:SS]` timestamps — the microphone
labeled `ME`, the system-audio channel `REMOTE`. Step 7 splits `REMOTE` into
individual speakers.
See [TODO.md](TODO.md) for the plan and [CHANGELOG.md](CHANGELOG.md) for what
has landed.

## Quick start

```powershell
uv sync
uv run referat config
```

`uv sync` provisions Python 3.12 and installs the tray and audio dependencies.
Add `--extra transcribe` for the transcription stack (~3 GB, torch built for
CUDA 12.8). The first run creates `config.toml` from
[config.example.toml](config.example.toml) and the meetings folder.

Start the tray with `uv run referat-tray`, which launches through `pythonw.exe`
with no console window; use `uv run python -m referat.tray` when you want the log
on screen as well. `ctrl+alt+f9` starts and stops a meeting, `ctrl+alt+f10`
pauses and resumes it, and both combos are configurable. The icon is gray when
idle, red recording, amber paused and blue while transcribing. Closing the lid
still sleeps the machine: the hold suppresses the idle timer and nothing else.

Both WAVs run the full length of the meeting — quiet stretches are written as
real silence so the two channels stay aligned — which costs roughly 460 MB an
hour. They are deleted once every channel has transcribed cleanly, judged by the
model's own confidence and repetition scores, and kept whenever anything looks
off — a garbled channel, a failed run, or one that had voice in it but produced
no text. A channel with no voice in it at all counts as clean, so an in-person
meeting whose loopback recorded nothing does not keep its audio forever.

The first transcription downloads large-v3 (~3 GB) from Hugging Face and takes a
few minutes; the CPU fallback downloads `medium` separately the first time it is
used. Neither is a hang.

## Layout

See [INDEX.md](INDEX.md) for an annotated map of every file, and
[CLAUDE.md](CLAUDE.md) for the architecture and conventions.
