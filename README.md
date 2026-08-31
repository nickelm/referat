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

Build step 9 of 9 for the recorder itself. The tray app runs and autostarts, the
two hotkeys drive the state machine, and a meeting is recorded as `mic.wav` plus
`system.wav` written crash-safely — left playable even if the process is killed
mid-meeting — and Windows will not idle-sleep out from under it.

Stopping a meeting transcribes it: `faster-whisper` large-v3 on the GPU, falling
back to CPU + medium, on a background thread that a new meeting can start
straight through. Both channels go through it, and `transcript.md` interleaves
them chronologically with elapsed `[HH:MM:SS]` timestamps.

The system-audio channel is diarized with `pyannote.audio`, and its speakers are
given **names** rather than numbers: each one is matched against a database of
voices learned from meetings already labeled, so there is no enrollment step —
`referat label` plays a voice, asks who it was, and every meeting after that one
knows it. A name is only written when the match clears both a similarity
threshold and a margin over the runner-up, because a wrong name is worse than
`SPEAKER_02`.

The `referat` CLI is the rest of it: `list` (the inventory and the queue),
`status`, `devices`, `rerun`, `label` and `config`.

Steps 10-13 are next, and gated on all of the above working on real meetings:
the lazy `/cleanup` pass that writes `notes.md`, a VS Code browsing layer, and
per-project digests pushed into a Google Doc. See [TODO.md](TODO.md) for the
plan and [CHANGELOG.md](CHANGELOG.md) for what has landed.

## Quick start

Installing on a machine that has none of this: **[SETUP.md](SETUP.md)**, which
covers the CUDA driver, the Hugging Face token and the gated pyannote model,
microphone selection, the synced-folder rules, and autostart. The short version:

```powershell
uv sync
uv run referat config
uv run python scripts/install_autostart.py
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
