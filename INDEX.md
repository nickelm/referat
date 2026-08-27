# INDEX.md — repository map

Referat is a personal, fully offline meeting recorder for one Windows 11 laptop.
A system tray app captures the microphone and the system audio output as two
separate WAV streams, then transcribes them with `faster-whisper` and diarizes
the loopback channel with `pyannote.audio`, producing one chronological,
timestamped, speaker-labeled `transcript.md` per meeting in `~/Meetings`. A small
`referat` CLI lists meetings, re-runs transcription, names the speakers it could
not identify, and reports what the tray app is doing. This file maps every source file in the repository; keep it current
whenever files are added, removed, or repurposed.

## Repository root

| File                                            | Description                                                                 |
| ----------------------------------------------- | --------------------------------------------------------------------------- |
| [CLAUDE.md](CLAUDE.md)                           | Project context, architecture summary, conventions, and the standing rule to update INDEX/CHANGELOG/TODO after every change. |
| [INDEX.md](INDEX.md)                             | This file: overview plus an annotated map of every source file.             |
| [CHANGELOG.md](CHANGELOG.md)                     | Reverse-chronological dated entries, one group per work session.            |
| [TODO.md](TODO.md)                               | The build plan as checkboxes; completed items are checked, never deleted.    |
| [README.md](README.md)                           | Short user-facing description and quick start.                              |
| [pyproject.toml](pyproject.toml)                 | Package metadata, base and `transcribe` dependency sets, the `referat` and `referat-tray` entry points, and the `uv` index pinning torch to CUDA 12.8. |
| [config.example.toml](config.example.toml)       | Annotated configuration template; copied to gitignored `config.toml` on first run. |
| [.python-version](.python-version)               | Pins the project to Python 3.12 for `uv`.                                   |
| [.gitignore](.gitignore)                         | Excludes `config.toml`, tokens, logs, and any stray audio files.            |
| `uv.lock`                                        | Resolved dependency lock, committed deliberately — one machine, reproducible env. |
| `.vscode/settings.json`                          | Pins the VS Code interpreter to the uv venv and tells the Python Envs extension to use uv rather than pip. |

## Package — [referat/](referat/)

| File                                                  | Description                                                            |
| ----------------------------------------------------- | ---------------------------------------------------------------------- |
| [referat/__init__.py](referat/__init__.py)             | Package docstring and `__version__`.                                   |
| [referat/config.py](referat/config.py)                 | Frozen dataclasses mirroring `config.toml`, `tomllib` loading with unknown-key warnings, validation, first-run bootstrap of the config file and meetings folder, and Hugging Face token lookup. |
| [referat/paths.py](referat/paths.py)                   | Every filesystem location Referat uses: repo and template paths, `%LOCALAPPDATA%\Referat` state dir, log and status file, meeting-id parsing, meeting folder creation and listing, and the atomic JSON writer. |
| [referat/logging_setup.py](referat/logging_setup.py)   | Rotating file log in the state dir, plus a console handler when stderr exists (absent under `pythonw.exe`). |
| [referat/cli.py](referat/cli.py)                       | The `referat` command line entry point. Currently `--version` and `config`; `list`, `rerun`, `status` and `label` arrive at build step 8. |
| [referat/state.py](referat/state.py)                   | The recorder state machine: the five states, the legal-transition table, thread-safe transitions, listener callbacks, and the count of background transcription jobs. |
| [referat/status.py](referat/status.py)                 | Reads and writes `status.json` in the state dir — what the tray is doing, its pid, the current meeting. Written on every transition, read by `referat status`. |
| [referat/hotkeys.py](referat/hotkeys.py)               | Registers the two configured global combos through `keyboard`, debounced against auto-repeat from a held USB button. |
| [referat/tray.py](referat/tray.py)                     | The tray app and process entry point: `pystray` icon coloured per state, menu, hotkey wiring, single-instance mutex, and the recorder it starts and stops. |
| [referat/meeting.py](referat/meeting.py)               | The meeting record and `meta.json` read/write: status vocabulary, pause intervals, per-channel audio info, folder creation through `paths.new_meeting_dir`. |
| [referat/power.py](referat/power.py)                   | The sleep hold: `SetThreadExecutionState(ES_CONTINUOUS \| ES_SYSTEM_REQUIRED)` owned by a single keeper thread, because the flags are per-thread while Referat's transitions arrive on several. |
| [referat/recorder.py](referat/recorder.py)             | Dual-stream capture: `sounddevice` mic and WASAPI loopback to two mono WAVs, device selection by config substring, a recording clock with silence padding, incrementally updated WAV headers, and pause handling. |
| [referat/transcribe.py](referat/transcribe.py)         | `faster-whisper` over both of a meeting's WAVs: chunked decoding and resampling, backend selection with a CUDA-to-CPU fallback, the transcript renderer, the `transcription` block of `meta.json`, and the quality gate that decides whether the audio may be deleted. |
| [referat/merge.py](referat/merge.py)                   | Interleaving the two channels into one chronological list of `(start, label, text)` entries, with a fixed tie-break so simultaneous speech orders the same way every run. A segment's diarized speaker wins over its channel's label here. |
| [referat/diarize.py](referat/diarize.py)               | `pyannote.audio` over the loopback channel: who spoke when, aligned to the transcribed segments by overlap and renumbered `SPEAKER_01`, `SPEAKER_02`, ... Reports failure rather than raising, so a diarization problem never costs the transcript. |

## Templates — [templates/](templates/)

| File                                                          | Description                                                        |
| ------------------------------------------------------------- | ------------------------------------------------------------------ |
| [templates/meetings-CLAUDE.md](templates/meetings-CLAUDE.md)   | Dropped into the meetings folder on first run so Claude Code sessions opened there understand the file conventions, speaker labels, and what may not be edited. |

At build step 10 this becomes `templates/meetings/CLAUDE.md`: the template turns
into a directory copied whole into the meetings folder, gaining the `/cleanup`
slash command and the workspace settings that make Markdown open rendered.

## Planned modules

Not written yet; listed so the map reflects the whole intended shape. See
[TODO.md](TODO.md) for the build order.

| File                     | Build step | Purpose                                                                  |
| ------------------------ | ---------- | ------------------------------------------------------------------------ |
| `SETUP.md`               | 9          | Python/CUDA install, Hugging Face token and the gated pyannote repo, button programming, mic selection; extension sideloading at step 12. |
| `scripts/install_autostart.py` | 9    | Creates the Startup folder shortcut launching the tray via `pythonw.exe`. |
| `referat/voices.py`      | 7b         | The known-voices database in `<meetings_dir>/.voices/`: embeddings grouped by name, cosine matching of a diarized cluster against them, snippet extraction, and forgetting a person. Biometric data — never synced, never sent anywhere. |
| `referat/label.py`       | 7b         | The interactive side of `referat label`: plays a speaker's snippets, prompts for a name with fuzzy completion over the known ones, then writes the database, `meta.json` and the transcript's labels. |
| `referat/index.py`       | 10         | Regenerates the meetings folder's own `INDEX.md` dashboard — one row per meeting, title taken from the H1 of `notes.md`. |
| `templates/meetings/`    | 10         | The scaffold copied into the meetings folder: its `CLAUDE.md`, `.claude/commands/cleanup.md`, `.claude/settings.json` (denying access to `.voices/`), `.vscode/settings.json`. Seeded once, never overwritten. |
| `referat-vscode/`        | 11-12      | VS Code extension: meetings TreeView, and commands shelling out to the `claude` binary and to `referat rerun`. |
