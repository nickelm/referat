# Meetings folder

This folder holds recordings and transcripts produced by **Referat**, a local
meeting recorder. Everything here was captured and transcribed offline on this
machine. Treat it as private material: do not send its contents anywhere.

## Layout

One subfolder per meeting, named `YYYY-MM-DD_HHMM` (local start time, with a
`_2`, `_3` suffix if two meetings started in the same minute):

```
2026-08-27_1400/
  mic.wav          my microphone, mono 16 kHz
  system.wav       system audio output (WASAPI loopback) — everyone else
  transcript.md    merged, timestamped, speaker-labeled transcript
  meta.json        duration, pause intervals, model and device used
```

## transcript.md

```
## Meeting 2026-08-27 14:00 (58 min)

[00:03:12] ME: ...
[00:03:40] SPEAKER_01: ...
```

- Timestamps are `[HH:MM:SS]` elapsed from the start of the recording, not wall
  clock. Paused intervals are excluded from elapsed time; see `meta.json`.
- **`ME`** is the person whose laptop this is — the microphone channel, by
  definition one speaker.
- **`SPEAKER_01`, `SPEAKER_02`, ...** are remote or in-room participants, split
  out of the loopback channel by diarization. The numbering is per meeting and
  carries no meaning across meetings.
- **`REMOTE`** appears instead when diarization was unavailable (no Hugging Face
  token): all non-me speech, undifferentiated.
- Lines are in chronological order across both channels.

## meta.json

```json
{
  "id": "2026-08-27_1400",
  "started_at": "2026-08-27T14:00:03",
  "ended_at": "2026-08-27T14:58:11",
  "duration_seconds": 3488,
  "status": "done",
  "pauses": [{"start": 1204.5, "end": 1320.2}],
  "audio": {"mic": {...}, "system": {...}},
  "transcription": {"model": "large-v3", "device": "cuda", "diarization": true},
  "referat_version": "0.1.0"
}
```

`status` is one of `recording`, `stopped`, `transcribing`, `done`, `failed`.
A folder still marked `recording` means the app died mid-meeting; the WAV files
are still valid and `referat rerun <id>` will transcribe them.

## Working here

- **Never edit `transcript.md`, `meta.json`, or the WAV files.** They are the
  raw record. Referat may rewrite `transcript.md` on `referat rerun`.
- Write summaries, action items, and notes into **new files beside** the
  transcript, e.g. `notes.md`, `actions.md`, `summary.md`. Name them plainly and
  say at the top which transcript they came from.
- Transcripts are ASR output: names, jargon, and acronyms are often wrong, and
  speaker turns can be split or merged. Quote with that caveat and prefer
  paraphrase over verbatim quotation unless the wording matters.
- Cite moments by timestamp (`[00:12:40]`) so they can be found in the audio.
- When asked about "the last meeting" or a date, list the folders and pick by
  name; do not guess.
