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

Build step 1 of 9 — repository skeleton. See [TODO.md](TODO.md) for the plan and
[CHANGELOG.md](CHANGELOG.md) for what has landed.

## Quick start

```powershell
uv sync
uv run referat config
```

`uv sync` provisions Python 3.12 and installs the tray and audio dependencies.
Add `--extra transcribe` for the transcription stack (~3 GB, torch built for
CUDA 12.8). The first run creates `config.toml` from
[config.example.toml](config.example.toml) and the meetings folder.

## Layout

See [INDEX.md](INDEX.md) for an annotated map of every file, and
[CLAUDE.md](CLAUDE.md) for the architecture and conventions.
