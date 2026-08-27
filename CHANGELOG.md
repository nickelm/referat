# Changelog

Newest first. One entry per work session; small changes are grouped.

## 2026-08-27 — Repository skeleton

Build step 1. Created the repository from scratch.

- Package layout: single flat `referat/` package with `__init__.py`, `config.py`,
  `paths.py`, `logging_setup.py`, and a stub `cli.py`.
- `pyproject.toml` on hatchling, `requires-python = ">=3.12,<3.13"`. Base
  dependencies are tray and audio only (`pystray`, `pillow`, `keyboard`,
  `sounddevice`, `numpy`, `PyAudioWPatch`); the transcription stack
  (`faster-whisper`, `torch`, `torchaudio`, `pyannote.audio`) sits behind a
  `transcribe` extra so a plain `uv sync` stays small.
- Pinned torch and torchaudio to the `cu128` index via `[tool.uv.sources]`: the
  RTX 5070 Ti Laptop GPU is Blackwell (sm_120) and the default PyPI wheels do
  not support it. Pinned the project to Python 3.12 in `.python-version`; the
  machine's system Python 3.14 has no wheels for torch, CTranslate2, or pyannote.
- Configuration: annotated `config.example.toml` covering hotkeys, audio devices,
  paths, transcription and logging; `config.py` loads it with stdlib `tomllib`
  into frozen dataclasses, warns on unknown keys instead of failing, validates
  the enum-ish fields, and on first run copies the example to gitignored
  `config.toml` and creates the meetings folder. `REFERAT_CONFIG` overrides the
  location.
- `paths.py` fixes the meeting folder contract (`mic.wav`, `system.wav`,
  `transcript.md`, `meta.json` under `YYYY-MM-DD_HHMM`, `_2` on collision), the
  `%LOCALAPPDATA%\Referat` state directory holding the log and the tray status
  file, and `write_json_atomic` for crash-safe metadata writes.
- Convention files: `CLAUDE.md` (context, architecture, conventions, and the
  standing rule to update INDEX/CHANGELOG/TODO after every change), `INDEX.md`
  (annotated map including planned modules), `TODO.md` (the nine-step build
  order), this changelog, and `README.md`.
- `templates/meetings-CLAUDE.md`, seeded into `~/Meetings` on first run, so
  Claude Code sessions opened there know the transcript format, the `ME` /
  `SPEAKER_NN` labels, and that the raw files are not to be edited.

No tray, audio capture, or transcription code yet — that starts at build step 2.
