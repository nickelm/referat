# INDEX.md — repository map

Referat is a personal, fully offline meeting recorder for one Windows 11 laptop.
A system tray app captures the microphone and the system audio output as two
separate WAV streams, then transcribes them with `faster-whisper` and diarizes
the loopback channel with `pyannote.audio`, producing one chronological,
timestamped, speaker-labeled `transcript.md` per meeting in `~/Meetings`. A small
`referat` CLI lists meetings, re-runs transcription, and reports what the tray
app is doing. This file maps every source file in the repository; keep it current
whenever files are added, removed, or repurposed.

## Repository root

| File                                            | Description                                                                 |
| ----------------------------------------------- | --------------------------------------------------------------------------- |
| [CLAUDE.md](CLAUDE.md)                           | Project context, architecture summary, conventions, and the standing rule to update INDEX/CHANGELOG/TODO after every change. |
| [INDEX.md](INDEX.md)                             | This file: overview plus an annotated map of every source file.             |
| [CHANGELOG.md](CHANGELOG.md)                     | Reverse-chronological dated entries, one group per work session.            |
| [TODO.md](TODO.md)                               | The build plan as checkboxes; completed items are checked, never deleted.    |
| [README.md](README.md)                           | Short user-facing description and quick start.                              |
| [pyproject.toml](pyproject.toml)                 | Package metadata, base and `transcribe` dependency sets, the `referat` entry point, and the `uv` index pinning torch to CUDA 12.8. |
| [config.example.toml](config.example.toml)       | Annotated configuration template; copied to gitignored `config.toml` on first run. |
| [.python-version](.python-version)               | Pins the project to Python 3.12 for `uv`.                                   |
| [.gitignore](.gitignore)                         | Excludes `config.toml`, tokens, logs, and any stray audio files.            |
| `uv.lock`                                        | Resolved dependency lock, committed deliberately — one machine, reproducible env. |

## Package — [referat/](referat/)

| File                                                  | Description                                                            |
| ----------------------------------------------------- | ---------------------------------------------------------------------- |
| [referat/__init__.py](referat/__init__.py)             | Package docstring and `__version__`.                                   |
| [referat/config.py](referat/config.py)                 | Frozen dataclasses mirroring `config.toml`, `tomllib` loading with unknown-key warnings, validation, first-run bootstrap of the config file and meetings folder, and Hugging Face token lookup. |
| [referat/paths.py](referat/paths.py)                   | Every filesystem location Referat uses: repo and template paths, `%LOCALAPPDATA%\Referat` state dir, log and status file, meeting-id parsing, meeting folder creation and listing, and the atomic JSON writer. |
| [referat/logging_setup.py](referat/logging_setup.py)   | Rotating file log in the state dir, plus a console handler when stderr exists (absent under `pythonw.exe`). |
| [referat/cli.py](referat/cli.py)                       | The `referat` command line entry point. Currently `--version` and `config`; `list`, `rerun`, `status` arrive at build step 8. |

## Templates — [templates/](templates/)

| File                                                          | Description                                                        |
| ------------------------------------------------------------- | ------------------------------------------------------------------ |
| [templates/meetings-CLAUDE.md](templates/meetings-CLAUDE.md)   | Dropped into the meetings folder on first run so Claude Code sessions opened there understand the file conventions, speaker labels, and what may not be edited. |

## Planned modules

Not written yet; listed so the map reflects the whole intended shape. See
[TODO.md](TODO.md) for the build order.

| File                     | Build step | Purpose                                                                  |
| ------------------------ | ---------- | ------------------------------------------------------------------------ |
| `referat/state.py`       | 2          | The recorder state machine: states, legal transitions, listener callbacks. |
| `referat/hotkeys.py`     | 2          | Global hotkey registration via the `keyboard` library.                    |
| `referat/tray.py`        | 2          | `pystray` icon, per-state icon rendering, tray menu, app entry point.     |
| `referat/recorder.py`    | 3          | Dual-stream capture: `sounddevice` mic and WASAPI loopback, streamed to WAV with pause handling. |
| `referat/power.py`       | 4          | `SetThreadExecutionState` sleep hold, scoped to recording and paused.     |
| `referat/transcribe.py`  | 5-6        | `faster-whisper` transcription of both channels with CPU fallback.       |
| `referat/diarize.py`     | 7          | `pyannote.audio` diarization of the loopback channel.                    |
| `referat/merge.py`       | 6-7        | Merging both channels into the chronological `transcript.md`.            |
| `referat/meeting.py`     | 3          | `meta.json` read/write and the meeting record dataclass.                 |
| `SETUP.md`               | 9          | Python/CUDA install, Hugging Face token, button programming, mic selection. |
| `scripts/install_autostart.py` | 9    | Creates the Startup folder shortcut launching the tray via `pythonw.exe`. |
