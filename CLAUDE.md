# CLAUDE.md — Referat

## Standing instruction

**After any change to this repository, update `INDEX.md`, `CHANGELOG.md`, and
`TODO.md` before finishing.** This is not optional and applies to every session:

- `INDEX.md` — add, remove, or reword the one-line description of any file that
  was added, deleted, or repurposed.
- `CHANGELOG.md` — prepend one dated entry per session, grouping small changes.
  Reverse chronological, newest first.
- `TODO.md` — check off what got done, add tasks that surfaced. **Never delete
  completed items**; check them off.

## What Referat is

A personal system-tray application that records meetings (in person and over
Zoom/Teams), transcribes them offline with speaker diarization, and writes
Markdown transcripts into a meetings folder. There is **no GUI beyond the tray
icon** and **no automatic LLM cleanup pass**: notes are generated lazily by the
user, running Claude Code against the transcript files.

Target machine: Asus Zephyrus G14, Windows 11, RTX 5070 Ti Laptop GPU
(Blackwell / sm_120, needs CUDA 12.8 builds of PyTorch). Python 3.12, managed by
`uv` — the system Python 3.14 is too new for the torch / CTranslate2 / pyannote
wheels.

## Architecture

**Tray app** — `pystray` icon (gray idle, red recording, yellow paused, distinct
transcribing state), two global hotkeys via the `keyboard` library emitted by
programmable USB buttons, and a state machine:

```
idle -> recording -> (paused <-> recording) -> stopped -> transcribing -> idle
```

**Dual-stream capture** while recording, both streamed to disk continuously:

- Microphone, mono 16 kHz, via `sounddevice`. This channel is by definition
  the user — labeled `ME`.
- System audio output via WASAPI loopback (`PyAudioWPatch`), captured at the
  device's native mix rate and downmixed to mono, so Zoom calls are captured
  even through headphones. This channel is everyone else.

Pauses stop appending samples; pause intervals are logged to `meta.json` so the
timeline stays honest.

**Sleep prevention** — hold `SetThreadExecutionState(ES_CONTINUOUS |
ES_SYSTEM_REQUIRED)` through `ctypes` only while recording or paused, released on
stop. Transcription runs without the hold.

**Transcription**, on stop, in a background thread: `faster-whisper` large-v3 on
CUDA, degrading to CPU + medium when CUDA is unavailable. The loopback channel is
diarized with `pyannote.audio` (speaker-diarization-3.1), which needs a Hugging
Face token; without one it degrades to undifferentiated `REMOTE:` labels. Both
channels are merged into one chronological, timestamped transcript.

**CLI** (`referat`), for the user and for Claude Code: `list`, `rerun <id>`,
`status`.

## Meeting folder contract

`<meetings_dir>/YYYY-MM-DD_HHMM/` (default `~/Meetings`), collisions suffixed
`_2`, `_3`:

| File            | Contents                                                    |
| --------------- | ----------------------------------------------------------- |
| `mic.wav`       | microphone, mono 16 kHz                                      |
| `system.wav`    | WASAPI loopback, mono, native mix rate                       |
| `transcript.md` | merged timestamped transcript                                |
| `meta.json`     | duration, pause intervals, status, model and device used     |

`transcript.md`:

```
## Meeting 2026-08-27 14:00 (58 min)

[00:03:12] ME: ...
[00:03:40] SPEAKER_01: ...
```

`meta.json` keys: `id`, `started_at`, `ended_at`, `duration_seconds`, `status`
(`recording | stopped | transcribing | done | failed`), `pauses` (list of
`{start, end}` in elapsed seconds), `audio`, `transcription`, `referat_version`.

The meetings folder gets its own `CLAUDE.md`, seeded from
[templates/meetings-CLAUDE.md](templates/meetings-CLAUDE.md) on first run. Keep
the two in sync when the format changes.

## Conventions

- **Windows only.** No cross-platform abstraction layers, no `if sys.platform`
  branches for other OSes. WASAPI, `%LOCALAPPDATA%`, and Win32 calls are fair
  game and used directly.
- **One package**, `referat/`. Flat modules, no sub-packages.
- **Type hints throughout**, `from __future__ import annotations` at the top of
  every module.
- **Minimal dependencies.** Base install is tray + audio only; the ~3 GB
  transcription stack lives behind the `transcribe` extra.
- **Fully offline.** No telemetry, no cloud calls, no analytics. The only
  network access is downloading models on first use.
- **Recording robustness beats everything else.** A crash, sleep, or bug must
  never lose captured audio. Stream to disk, flush often, write metadata
  atomically (`paths.write_json_atomic`). When in doubt, flush.
- **Simple and direct over general and configurable.** This is a personal tool
  for one machine. Do not add plugin points, abstract base classes, or
  configuration knobs nobody asked for.

## Commands

```powershell
uv sync                       # base deps (tray, audio)
uv sync --extra transcribe    # plus faster-whisper, torch cu128, pyannote
uv run referat --version
uv run referat config         # show the loaded configuration
```

Configuration lives in repo-root `config.toml`, gitignored and created from
`config.example.toml` on first run. Override the location with `REFERAT_CONFIG`.
