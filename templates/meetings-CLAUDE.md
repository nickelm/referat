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
  clock. Paused intervals are excluded from elapsed time; see `meta.json`, whose
  `pauses` are in wall-clock seconds since the start.
- **`ME`** is the person whose laptop this is — the microphone channel, by
  definition one speaker.
- **`SPEAKER_01`, `SPEAKER_02`, ...** are everyone else, split out of the
  system-audio channel by diarization. The numbering is by first appearance in
  the meeting, and it is **per meeting**: `SPEAKER_02` last week and
  `SPEAKER_02` today are not the same person. Work out who is who from what they
  say, and write the names into your notes rather than into the transcript.
- **`REMOTE`** is the fallback for that same channel: a line diarization could
  not attribute, or a whole meeting where it never ran (no Hugging Face token,
  or an error). A transcript of nothing but `ME` and `REMOTE` is a normal,
  complete transcript with the speaker names missing — check
  `transcription.channels.system.diarization` in `meta.json` for why.
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
  "transcription": {
    "status": "done",
    "model": "large-v3", "device": "cuda", "compute_type": "float16",
    "seconds": 210.4,
    "channels": {
      "mic": {"label": "ME", "language": "en", "segments": 128,
              "speech_seconds": 812.4, "avg_logprob": -0.31,
              "compression_ratio": 1.72, "no_speech_prob": 0.06,
              "voiced_seconds": 640.2, "peak": 0.33,
              "silent": false, "clean": true},
      "system": {"label": "REMOTE", "language": "en", "segments": 94, "...": "...",
                 "speakers": ["SPEAKER_01", "SPEAKER_02"],
                 "diarization": {"status": "done",
                                 "model": "pyannote/speaker-diarization-community-1",
                                 "device": "cuda", "seconds": 41.2, "turns": 88}}
    },
    "audio_released": false
  },
  "referat_version": "0.1.0"
}
```

Both WAVs run the full length of the meeting: quiet stretches are written as
real silence, so the same position in `mic.wav` and in `system.wav` is the same
moment. Either file may be missing if that device could not be opened.

`status` is one of `recording`, `stopped`, `transcribing`, `done`, `failed`.
A folder still marked `recording` means the app died mid-meeting; the WAV files
are still valid and `referat rerun <id>` will transcribe them.

## The audio does not stay

Recording costs roughly 460 MB an hour, so **the WAV files are deleted once every
channel has transcribed cleanly**, judged by the model's own confidence,
repetition and no-speech scores — the per-channel `clean` flags above. A folder
holding only `transcript.md` and `meta.json`, with `"audio_released": true`, is a
*success*: the transcript was good enough to stand in for the recording.

The audio is kept whenever anything looks off — a garbled channel, a failed run,
or a channel that had voice in it but produced no text — because that is when you
would want to listen. `clean: false` on a channel says why the files are still
there.

A channel with `"silent": true` is the exception: `voiced_seconds` near zero
means the voice-activity detector found no speech anywhere in it, so there is
nothing to lose. That is the normal state of `system.wav` for a meeting held in
person or over a phone, where the loopback records only silence and the odd
notification chime — and without it those meetings would keep their audio
forever.

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
