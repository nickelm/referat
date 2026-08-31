# INDEX.md — repository map

Referat is a personal, fully offline meeting recorder for one Windows 11
laptop. A system tray app captures the microphone and the system audio output
as two separate WAV streams, then transcribes them with `faster-whisper` and
diarizes the loopback channel with `pyannote.audio`, producing one
chronological, timestamped, speaker-labeled `transcript.md` per meeting in the
configured meetings folder. Speakers it recognises from earlier meetings are
labeled by name; the rest wait for `referat label`, which plays their voice,
asks who it was, and remembers the answer. A small `referat` CLI lists
meetings, re-runs transcription, names those speakers, and reports what the
tray app is doing, and regenerates the meetings folder's own `INDEX.md`
dashboard. The folder itself carries a small scaffold: a `/cleanup` slash
command that writes `notes.md` beside a transcript when asked, and the
permission rule that keeps that pass out of the voiceprints. A later step adds
per-project digests that push those notes into a Google Doc. This file maps
every source file in the repository; keep it current whenever files are added,
removed, or repurposed.

## Repository root

| File                                            | Description                                                                 |
| ----------------------------------------------- | --------------------------------------------------------------------------- |
| [CLAUDE.md](CLAUDE.md)                           | Project context, architecture summary, conventions, and the standing rule to update INDEX/CHANGELOG/TODO after every change. |
| [INDEX.md](INDEX.md)                             | This file: overview plus an annotated map of every source file.             |
| [CHANGELOG.md](CHANGELOG.md)                     | Reverse-chronological dated entries, one group per work session.            |
| [TODO.md](TODO.md)                               | The build plan as checkboxes; completed items are checked, never deleted.    |
| [README.md](README.md)                           | Short user-facing description and quick start.                              |
| [SETUP.md](SETUP.md)                             | Install and configuration guide for a fresh Windows 11 machine: Python 3.12 and uv, the CUDA 12.8 driver, the Hugging Face token and the gated pyannote repo, button programming, microphone selection, the rules for a synced meetings folder, autostart, and the two things (lid sleep, Smart App Control) Referat deliberately leaves to the user. |
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
| [referat/config.py](referat/config.py)                 | Frozen dataclasses mirroring `config.toml`, `tomllib` loading with unknown-key warnings, validation, first-run bootstrap of the config file and of the meetings folder scaffold, Hugging Face token lookup, and the resolvers `voices_dir()`, `staging_dir()` and `meeting_roots()` — the one answer each to where the voiceprints live, where recording happens, and where a meeting may be found. |
| [referat/paths.py](referat/paths.py)                   | Every filesystem location Referat uses: repo and template paths, `%LOCALAPPDATA%\Referat` state dir, log and status file, meeting-id parsing, meeting folder creation and listing, the staging folder and the move out of it, the known-voices folder and its `[paths].voices_dir` override, `seed_tree` copying the meetings-folder scaffold without ever overwriting a file, and the atomic JSON writer. |
| [referat/logging_setup.py](referat/logging_setup.py)   | Rotating file log in the state dir, plus a console handler when stderr exists (absent under `pythonw.exe`). |
| [referat/cli.py](referat/cli.py)                       | The `referat` command line entry point: `--version`, `config`, `list`, `status`, `devices`, `rerun`, `label` and `index`, plus the table renderer. `devices` lists the input and loopback devices and marks the ones `[audio]` resolves to, asking `recorder`'s own resolvers rather than copying their rules. Dispatch only for the three heavy commands, which are imported inside the branch that runs them. |
| [referat/state.py](referat/state.py)                   | The recorder state machine: the five states, the legal-transition table, thread-safe transitions, listener callbacks, and the count of background transcription jobs. |
| [referat/status.py](referat/status.py)                 | Reads and writes `status.json` in the state dir — what the tray is doing, its pid, the current meeting. Written on every transition, read by `referat status`, and `is_running` asks Windows whether that pid is still there. |
| [referat/hotkeys.py](referat/hotkeys.py)               | Registers the two configured global combos through `keyboard`, debounced against auto-repeat from a held USB button. |
| [referat/tray.py](referat/tray.py)                     | The tray app and process entry point: `pystray` icon coloured per state, menu, hotkey wiring, single-instance mutex, and the recorder it starts and stops. |
| [referat/meeting.py](referat/meeting.py)               | The meeting record and `meta.json` read/write: status vocabulary, pause intervals, per-channel audio info, the duration formatter `referat list` and the meetings dashboard share, folder creation in the staging folder through `paths.new_meeting_dir`, and `load_meetings` across both roots. |
| [referat/power.py](referat/power.py)                   | The sleep hold: `SetThreadExecutionState(ES_CONTINUOUS \| ES_SYSTEM_REQUIRED)` owned by a single keeper thread, because the flags are per-thread while Referat's transitions arrive on several. |
| [referat/recorder.py](referat/recorder.py)             | Dual-stream capture: `sounddevice` mic and WASAPI loopback to two mono WAVs, device selection by config substring, a recording clock with silence padding, incrementally updated WAV headers, and pause handling. |
| [referat/transcribe.py](referat/transcribe.py)         | `faster-whisper` over both of a meeting's WAVs: chunked decoding and resampling, backend selection with a CUDA-to-CPU fallback, the transcript renderer, the `transcription` block of `meta.json`, and the quality gate that decides whether the audio may be deleted. Calls out to diarization and then to identification, relabels the segments with whatever names came back, and regenerates the meetings dashboard once the meeting has been promoted. |
| [referat/merge.py](referat/merge.py)                   | Interleaving the two channels into one chronological list of `(start, label, text)` entries, with a fixed tie-break so simultaneous speech orders the same way every run. A segment's diarized speaker wins over its channel's label here. |
| [referat/diarize.py](referat/diarize.py)               | `pyannote.audio` over **both** channels — the microphone hears the whole room in a meeting held in person: who spoke when, aligned to the transcribed segments by overlap and renumbered `SPEAKER_01`, `SPEAKER_02`, ... across the meeting rather than per channel, so two channels cannot put two people behind one label. Harvests the clustering centroid per speaker and hands back the renaming map so the two stay together. Reports failure rather than raising, so a diarization problem never costs the transcript. |
| [referat/voices.py](referat/voices.py)                 | The known-voices database in `<meetings_dir>/.voices/`, or wherever `[paths].voices_dir` puts it: embeddings grouped by name, cosine matching of a diarized cluster against them under a threshold *and* a margin, the snippets cut for whoever is left over, and the owner bootstrap from a meeting whose microphone clustered into exactly one speaker. A lone name in the database is held to the margin on its own score, since there is no runner-up to beat. Never raises into the pipeline. Biometric data — never synced, never sent anywhere. |
| [referat/label.py](referat/label.py)                   | `referat label`: plays an unnamed speaker's snippets, prompts for a name with fuzzy completion over the known ones, then writes the database, `meta.json` and the transcript's labels — the label field alone. `--forget` deletes a person and reverts them everywhere; both paths regenerate the meetings dashboard, since naming somebody is the only thing outside the pipeline that changes its Unnamed column. Needs no part of the `transcribe` extra. |
| [referat/rerun.py](referat/rerun.py)                   | `referat rerun`: transcribe a meeting again from the WAVs it kept. Refuses when there is no audio left or a live tray is already on the GPU, clears the previous run's speaker names and snippets because diarization renumbers from scratch, then hands the meeting to the pipeline. The only CLI command that needs the `transcribe` extra. |
| [referat/index.py](referat/index.py)                   | The meetings folder's own generated `INDEX.md` — one row per meeting with its title, duration, status, unnamed speakers and links to transcript and notes, newest first, with the staged meetings counted in a footer rather than linked. `meeting_title` reads the H1 of `notes.md` and is shared with step 13's digest. Rewritten at the end of every transcription, after `referat label`, and on demand by `referat index`; never raises, because a dashboard may not cost a transcript. A **different file** from this repository's `INDEX.md`. |

## Scripts — [scripts/](scripts/)

| File                                                            | Description                                                      |
| --------------------------------------------------------------- | ---------------------------------------------------------------- |
| [scripts/install_autostart.py](scripts/install_autostart.py)     | Creates, inspects and removes the Startup-folder shortcut that launches the tray under `pythonw.exe` at sign-in. Resolves the Startup folder with `SHGetKnownFolderPath` and writes the `.lnk` through `WScript.Shell` from PowerShell, then reads it back to verify the save. Refuses to repoint — or delete — a shortcut aimed somewhere else without `--force`. Not part of the wheel. |

## Templates — [templates/](templates/)

`templates/meetings/` is the scaffold copied into the meetings folder on first
run, file by file, **never overwriting anything that is already there** — so a
prompt refined in place survives, and the repo copy and the live copy are
reconciled by hand.

| File                                                                                            | Description                                                        |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| [templates/meetings/CLAUDE.md](templates/meetings/CLAUDE.md)                                     | Orients a Claude Code session opened in the meetings folder: the layout, the speaker-label vocabulary and what a number does and does not mean, the `meta.json` walkthrough, why the audio disappears, how notes are written, and what may never be edited. |
| [templates/meetings/.claude/commands/cleanup.md](templates/meetings/.claude/commands/cleanup.md) | The `/cleanup <meeting-id>` prompt: read a transcript, write `notes.md` beside it, never touch anything else, never put a name on a `SPEAKER_NN`, and stay inside the Markdown subset step 13's translator handles. Versioned as Markdown so it is refined by editing rather than by changing code. |
| [templates/meetings/.claude/settings.json](templates/meetings/.claude/settings.json)             | Denies the `/cleanup` pass every read of `.voices/**`. That pass runs `claude -p` in the folder with write access, so this is the only thing standing between it and the voiceprints. `Read` and `Edit` rules only — a `Write` rule in a deny list does nothing. |
| [templates/meetings/.vscode/settings.json](templates/meetings/.vscode/settings.json)             | Associates `*.md` with the Markdown preview editor in that workspace only, which is what makes the generated `INDEX.md` a dashboard rather than a table of pipes. |

## Planned modules

Not written yet; listed so the map reflects the whole intended shape. See
[TODO.md](TODO.md) for the build order.

| File                     | Build step | Purpose                                                                  |
| ------------------------ | ---------- | ------------------------------------------------------------------------ |
| `referat-vscode/`        | 11-12      | VS Code extension: meetings TreeView, a Projects section, and commands shelling out to the `claude` binary, to `referat rerun` and to `referat project`. The only graphical surface above the tray icon — there is no web UI. |
| `referat/projects.py`    | 13         | `<meetings_dir>/projects.toml` — the project list and each project's linked digest doc — plus assignment of a meeting to a project and the remembered default. The one TOML file Referat writes back, which is why it is not in `config.py`. |
| `referat/digest.py`      | 13         | The translator from the constrained `notes.md` Markdown to Google Docs `batchUpdate` requests, the anchor scan, and the diff that reconciles a doc against the meetings assigned to its project. Pure — no Google import, so the index arithmetic is testable offline. |
| `referat/gdocs.py`       | 13         | The Google client behind the `digest` extra: auth, `documents.create` / `get` / `batchUpdate`, and the Drive `files.list` search behind "select existing doc". The only module that touches the network. |
