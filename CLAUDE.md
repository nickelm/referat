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
user, through a `/cleanup` slash command run by Claude Code in the meetings
folder — interactively, or headless via `claude -p` from the VS Code extension.

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

Windows stops delivering loopback packets while nothing is playing, so both
channels are written against a recording clock and padded with silence when they
fall behind it. Without that, every quiet stretch would vanish from `system.wav`
and shift all later speech earlier, breaking the merge.

Pauses stop appending samples; pause intervals are logged to `meta.json` in
wall-clock seconds since the start, so the timeline stays honest. Positions in
the WAV files are audio time, with pauses excluded; the two are inter-convertible
from the pause list.

**Sleep prevention** — hold `SetThreadExecutionState(ES_CONTINUOUS |
ES_SYSTEM_REQUIRED)` through `ctypes` only while recording or paused, released on
stop. Transcription runs without the hold.

**Transcription**, on stop, in a background thread: `faster-whisper` large-v3 on
CUDA, degrading to CPU + medium when CUDA is unavailable. The loopback channel is
diarized with `pyannote.audio` 4 (`speaker-diarization-community-1` — the
checkpoint that matches the installed major version, *not* the 3.1 pipeline this
file used to name), which needs a Hugging Face token from an account that has
accepted that repository's conditions. Whisper segments are labeled by which
speaker turn overlaps them most, renumbered `SPEAKER_01`, `SPEAKER_02`, ... by
first appearance. Both channels are merged into one chronological, timestamped
transcript.

**Diarization may never cost anything but speaker names.** No token, a gated
repo, an out-of-memory, a pyannote release that moved its API: all of them
degrade to undifferentiated `REMOTE:` labels and a `done` meeting.
`diarize.diarize` therefore never raises — an exception escaping it on the CUDA
attempt would make `transcribe_meeting` re-transcribe the whole meeting on the
CPU, unable to tell a diarization problem from a dying GPU.

**Speaker identification** turns those numbers into names, without any
enrollment step. Diarization already computes one embedding per cluster
(`DiarizeOutput.speaker_embeddings`, the clustering centroids); Referat keeps it,
cuts two or three representative snippets of that speaker to
`speakers/SPEAKER_01_1.wav`, and matches the embedding by cosine similarity
against a known-voices database of embeddings grouped by name. A confident match
is labeled by name in `transcript.md`; everything else stays `SPEAKER_NN` and
waits for `referat label`, which plays the snippets, asks who that was, adds the
embedding to the database under that name and rewrites the labels. Every meeting
is therefore its own enrollment session, and the next meeting knows more voices
than the last. The snippets are cut during the pipeline precisely because the
WAVs do not survive it, and they are deleted once their speaker has a name.

**A wrong name is worse than no name.** Matching accepts only above a similarity
threshold *and* only when the winner beats the runner-up name by a margin;
short of that the speaker stays a number. Identification degrades like
diarization does — no database, an unreadable one, an embedding pyannote did not
return — and costs names, never the transcript.

The WAVs are deleted once *every* channel has transcribed cleanly, judged by
faster-whisper's own confidence, repetition and no-speech scores; anything that
looks off keeps its audio. A channel with no voice in it at all — the normal
state of `system.wav` for a meeting held in person — counts as clean, measured
by faster-whisper's `duration_after_vad` rather than by amplitude, or an
untouched loopback would pin every recording to the disk forever.

**Smart App Control is enforcing on this machine**, so unsigned bundled DLLs are
blocked — PyAV's FFmpeg among them, which made `import faster_whisper` fail.
Referat therefore decodes its own WAVs (`transcribe.decode_wav`) and stubs `av`
out. Turning Smart App Control off is a one-way system-wide change and is the
user's call, not Referat's.

`torchcodec`, which `pyannote.audio` pulls in, bundles FFmpeg the same way — but
it never gets the chance to load it. Pyannote reaches for a decoder only when
handed a *path*; handed `{"waveform": tensor, "sample_rate": int}` it decodes
nothing, which is the same trick already played on faster-whisper. Anything else
that ships FFmpeg should be fed decoded audio for the same reason.

**CLI** (`referat`), for the user and for Claude Code: `list`, `rerun <id>`,
`label <id>`, `status`, `index`.

## Notes and browsing (build steps 10-12)

Nothing in this section is built yet, and none of it starts until steps 1-9 work
end to end on real meetings. It is written down because it constrains what the
recorder may do.

**Cleanup is lazy and lives in the meetings folder, not in the code.** The
folder gets a scaffold, seeded from `templates/meetings/`: its `CLAUDE.md`, a
`.claude/commands/cleanup.md` holding the `/cleanup <meeting-id>` prompt, a
`.claude/settings.json` denying all access to `.voices/`, and a
`.vscode/settings.json` associating `*.md` with the Markdown preview editor so
Markdown opens rendered in that workspace only. `/cleanup` reads a meeting's
`transcript.md` and writes `notes.md` beside it — decisions, action items,
discussion summary, `[[Wikilinks]]` for people and projects — and never touches
the verbatim transcript. The prompt is a versioned file, so it is refined by
editing Markdown rather than by changing code.

**Every scaffold file is seeded once and never overwritten.** A prompt refined
in place in the meetings folder has to survive; the repo template and the live
copy are allowed to drift, and are reconciled by hand.

`referat index` regenerates the meetings folder's own `INDEX.md`, a table of all
meetings — date, title, duration, links to transcript and notes — also written
at the end of the transcription pipeline. With Markdown opening rendered, that
file is the dashboard. The title is the H1 of `notes.md`, falling back to the
meeting id; it is deliberately not a `meta.json` key, since the cleanup layer
must not edit the raw record. Note that this is a *different* file from this
repository's `INDEX.md`, which the standing instruction above is about.

**VS Code is the browsing layer**, with a `referat-vscode/` extension: a
meetings TreeView, and per-meeting commands that shell out to the `claude`
binary (*Generate notes*) and to `referat rerun` (*Re-transcribe*).

## Meeting folder contract

`<meetings_dir>/YYYY-MM-DD_HHMM/` (default `~/Meetings`), collisions suffixed
`_2`, `_3`:

| File            | Contents                                                    |
| --------------- | ----------------------------------------------------------- |
| `mic.wav`       | microphone, mono 16 kHz                                      |
| `system.wav`    | WASAPI loopback, mono, native mix rate                       |
| `transcript.md` | merged timestamped transcript                                |
| `meta.json`     | duration, pause intervals, status, model and device used     |
| `speakers/`     | a few WAV snippets per unidentified speaker, for `referat label` |
| `notes.md`      | step 10: written by `/cleanup`, never by Referat itself      |

`transcript.md`:

```
## Meeting 2026-08-27 14:00 (58 min)

[00:03:12] ME: ...
[00:03:40] Anna: ...
[00:04:05] SPEAKER_02: ...
```

`meta.json` keys: `id`, `started_at`, `ended_at`, `duration_seconds`, `status`
(`recording | stopped | transcribing | done | failed`), `pauses` (list of
`{start, end}` in elapsed seconds), `audio`, `transcription`, `speaker_names`
(`SPEAKER_NN` to the name it was resolved to), `referat_version`. The per-channel
`speakers` block carries each cluster's embedding and its snippet offsets.

**The transcript's speech is immutable; its speaker labels are not.** Nothing may
ever change a word of what was said in `transcript.md` — not `/cleanup`, not a
person, not Referat. Speaker labels are metadata that happens to live in that
file, and `referat label` may rewrite them: a `SPEAKER_NN` becoming `Anna`, or
`Anna` reverting to `SPEAKER_NN` after `referat label --forget Anna`. That is the
only in-place edit anything is allowed to make to a transcript, and it touches
the label field alone. (`referat rerun` regenerates the file from the audio,
which is a different thing.)

The known-voices database lives beside the meetings, in `<meetings_dir>/.voices/`.
It is biometric personal data about people who never asked to be in it: it never
leaves the machine, it is excluded from sync and backups, the `/cleanup` pass is
denied access to it, and `referat label --forget <name>` deletes a person
outright — embeddings gone, labels reverted everywhere they appear.

The meetings folder gets its own `CLAUDE.md`, seeded from
[templates/meetings-CLAUDE.md](templates/meetings-CLAUDE.md) on first run. Keep
the two in sync when the format changes. At step 10 that template becomes a
directory, `templates/meetings/`, and the folder also gains a generated
`INDEX.md`, a `.claude/commands/cleanup.md` and a `.vscode/settings.json`.

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
  network access is downloading models on first use. Recording and transcription
  never leave the machine; the cleanup pass is the one deliberate exception, and
  it only runs when the user asks for it.
- **No Anthropic API key, ever.** All LLM work goes through the official
  `claude` binary under the user's Claude Code subscription login. No module in
  this project may call the Anthropic API directly, no key belongs in
  `config.toml`, in the environment, or in the VS Code extension, and no
  Anthropic SDK belongs in `pyproject.toml`. The extension spawns `claude` as a
  child process and never handles credentials.
- **Voiceprints never leave the machine.** The known-voices database is not
  synced, not backed up, not readable by the `/cleanup` pass, and never sent
  anywhere. Deleting a person deletes them.
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
