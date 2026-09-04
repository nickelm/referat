# Meetings folder

This folder holds recordings and transcripts produced by **Referat**, a local
meeting recorder. Everything here was captured and transcribed offline on this
machine, and it belongs to **Niklas**, whose meetings these are. Treat it as
private material: do not send its contents anywhere.

## Layout

One subfolder per meeting, named `YYYY-MM-DD_HHMM` (local start time, with a
`_2`, `_3` suffix if two meetings started in the same minute):

```
INDEX.md           the dashboard: one row per meeting. Generated — see below
projects.json      the projects a meeting can be tagged with. Machine-written
actions.json       what has been done about the action items. Machine-written
days/              one YYYY-MM-DD.md per day, from `/standup`. Generated
2026-08-27_1400/
  mic.wav          my microphone — and the room — mono 16 kHz
  system.wav       system audio output (WASAPI loopback) — everyone on the call
  transcript.md    merged, timestamped, speaker-labeled transcript
  meta.json        duration, pause intervals, model and device used
  speakers/        WAV snippets of speakers nobody has named yet
  notes.md         written by `/cleanup`. The only file here you write
.claude/           the `/cleanup` and `/standup` commands, and the `.voices/` deny rule
.vscode/           opens Markdown rendered in this workspace
```

**`INDEX.md` is generated** by `referat index`, and again at the end of every
transcription. Hand edits to it are lost on the next run — change what it says by
changing a meeting's `notes.md` title, not by editing the table.

There may also be a `.voices/` folder. **Do not read it, and do not open any file
inside it.** It holds voiceprints of everyone who has been recorded — biometric
data about people who never asked to be in a database. It never leaves this
machine and it is no part of any note.

## transcript.md

```
## Meeting 2026-08-27 14:00 (58 min)

[00:03:12] Niklas: ...

[00:03:40] Anna: ...

[00:04:05] SPEAKER_02: ...

[00:04:31] ME: ...
```

- Timestamps are `[HH:MM:SS]` elapsed from the start of the recording, not wall
  clock. Paused intervals are excluded from elapsed time; see `meta.json`, whose
  `pauses` are in wall-clock seconds since the start.
- **`ME`** is the fallback for the microphone channel: a line nothing
  attributed. It names nobody. It is emphatically **not** Niklas and not a
  synonym for "the microphone's owner" — the microphone hears the whole room in a
  meeting held in person, and it is diarized exactly as the system channel is, so
  a run of `ME` lines is as likely to be two people as one. A transcript of
  nothing but `ME` on the microphone means diarization did not run on that
  channel — check `meta.json`.
- **Niklas is a name like any other.** He is recognised by voiceprint and written
  out, so his lines read `Niklas:`. They used to read `ME:`, which is why some
  older transcripts in this folder still do; `referat relabel` repaired the ones
  it safely could and left the rest alone. Where you see `ME`, treat it as
  unattributed, not as Niklas.
- **A plain name** — `Anna` — is a speaker Referat recognised by voice, or one
  somebody named with `referat label`. A name is **per person and stable across
  every meeting in this folder**: `Anna` in March and `Anna` today are the same
  voice, matched against the same voiceprint. Use the name as given; it is more
  reliable than anything you could infer from the text.
- **`SPEAKER_01`, `SPEAKER_02`, ...** are the ones nobody has named yet. The
  numbering is by first appearance and it is **per meeting**: `SPEAKER_02` last
  week and `SPEAKER_02` today are not the same person, and the same person in the
  room and on the call is two different numbers in one transcript. A number means
  something only inside the meeting you found it in.
- **Never write a real name onto a `SPEAKER_NN`**, in the transcript or in a
  note. Working out who somebody probably was from what they said is exactly the
  guess that puts the wrong name on someone's words, where it then looks as
  authoritative as a right one. `referat label <meeting-id>` is the answer: it
  plays their voice and asks. `meta.json`'s `speaker_names` is where a real name
  belongs, and only `referat label` puts it there.
- **`REMOTE`** is the fallback for the system-audio channel: a line diarization
  could not attribute, or a whole meeting where it never ran (no Hugging Face
  token, or an error). It is `ME`'s counterpart and means the same thing on the
  other channel: nothing attributed this. A transcript of nothing but `ME` and
  `REMOTE` is a normal, complete transcript with the speaker names missing — check
  `transcription.channels.system.diarization` in `meta.json` for why.
- Lines are in chronological order across both channels.
- **An older hybrid meeting may record the same person twice.** When a call's
  audio came out of a loudspeaker standing in the room, the microphone heard it
  as well as the loopback did, so the same sentence appears twice a second or two
  apart — usually under one name, one copy punctuated and one a lowercase run-on
  keeping the stutters. Referat suppresses this during transcription now, and
  `transcription.bleed` in `meta.json` says what it removed; transcripts written
  before that still carry it. **Summarise such a pair once.** Do not read a
  repetition as emphasis, as somebody agreeing with themselves, or as two people
  saying the same thing — and do not edit the transcript to remove it, which is
  `referat debleed`'s job and not yours. If a meeting is visibly full of these,
  say so once in the note rather than silently working around it.

**The speech is immutable. The labels are not.** Nothing may ever change a word
of what was said. A speaker label is metadata that happens to live in this file,
and `referat label` rewrites it in both directions — a `SPEAKER_NN` becoming a
name, or a name reverting after `referat label --forget`. That is the only edit
anything is permitted to make to an existing transcript, and it is not yours to
make.

## meta.json

```json
{
  "id": "2026-08-27_1400",
  "started_at": "2026-08-27T14:00:03",
  "ended_at": "2026-08-27T14:58:11",
  "duration_seconds": 3488,
  "status": "transcribed",
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
                 "speakers": {
                   "SPEAKER_01": {"embedding": [0.017, "..."], "snippets": [],
                                  "name": "Anna",
                                  "match": {"name": "Anna", "score": 0.81,
                                            "runner_up": 0.44, "accepted": true}},
                   "SPEAKER_02": {"embedding": [0.004, "..."],
                                  "snippets": [{"file": "SPEAKER_02_1.wav",
                                                "start": 612.4, "end": 618.4}]}
                 },
                 "diarization": {"status": "done",
                                 "model": "pyannote/speaker-diarization-community-1",
                                 "device": "cuda", "seconds": 41.2, "turns": 88}}
    },
    "audio_released": false
  },
  "tags": ["kundprojekt"],
  "speaker_names": {"SPEAKER_01": "Anna"},
  "referat_version": "0.1.0"
}
```

Both WAVs run the full length of the meeting: quiet stretches are written as
real silence, so the same position in `mic.wav` and in `system.wav` is the same
moment. Either file may be missing if that device could not be opened.

`status` is the meeting's lifecycle, and it is the only place that lifecycle is
written down — nothing works it out from which files happen to exist:

```
recording -> recorded -> transcribing -> gate_failed | transcribed
                                                    -> notes_written -> synced
failed  (transcription raised)
```

A folder still marked `recording` means the app died mid-meeting; the WAV files
are still valid and `referat rerun <id>` will transcribe them. `gate_failed` is a
transcript that came out but was not trusted enough to delete the audio for, so
that meeting is still in the staging folder and not in this one. `transcribed` and
`done` mean the same thing — `done` is what older meetings say, and it is read as
`transcribed`.

`notes_written` is the one value nothing here writes for itself. **You may not set
it**, because you may not touch `meta.json` at all; whoever ran `/cleanup` calls
`referat state <id> notes-written` afterwards.

`tags` is the list of project ids this meeting carries — zero or more, and an
empty list means untagged. They are ids rather than names so that renaming a
project touches one file; `projects.json` in this folder maps them to what a
person reads. An id in `tags` with no entry in `projects.json` is an *orphan*, left
behind by a deleted project, and is worth mentioning rather than ignoring.

**`speaker_names` is the authority on who a label is** — the mapping from this
meeting's `SPEAKER_NN` to a real person. A speaker missing from it is one nobody
has named yet, and their `snippets` are the clips `referat label` will play when
somebody does. The `embedding` beside them is the voiceprint that matched, or
would have; `match` records the best candidate even when it was refused, which is
what the thresholds get tuned on. None of that belongs in a note.

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

A meeting whose audio was *kept* has not moved into this folder yet: it stays in
a local staging folder until a `referat rerun` comes out clean. `INDEX.md` counts
those in its footer rather than listing them, because they are not here to link
to; `referat list` shows them alongside everything else.

## notes.md, and `/cleanup`

`notes.md` beside a transcript is the readable version of that meeting:
decisions, action items, and enough discussion that somebody who missed it does
not have to read the transcript. It is written by **`/cleanup <meeting-id>`**, a
slash command living in `.claude/commands/` — run it rather than writing notes
freehand, so every meeting's notes come out the same shape. The prompt is a file:
if the notes keep coming out wrong, improve `.claude/commands/cleanup.md` rather
than working around it.

Three things depend on the shape it produces, so keep them if you ever write
notes by hand:

- **The first line is an H1**, a short specific title. `INDEX.md` reads it as the
  meeting's title, and so will the per-project digests; a meeting with no
  `notes.md` falls back to showing its id.
- **The Markdown stays in a narrow subset** — `##`/`###`, bold, italic, code, one
  level of `-` bullets, links and `[[Wikilinks]]`. Notes are translated into
  Google Docs later by a converter that handles exactly that much.
- **The notes are written in English, whatever language the meeting was in.**
  Meetings here are held in English and in Swedish, and `transcript.md` is in
  whichever one was spoken — Referat detects it per channel, so a transcript may
  even be Swedish on the microphone and English on the far end. The notes are
  not a translation of the transcript, they are the writing-up of it, and they
  are always in English: they are what gets pasted into a shared document, read
  months later, and assembled into a project digest, and a notes folder that
  switches language by meeting is one nobody can skim.

  This is the same boundary as the spelling rule below and it stops in the same
  places. It never reaches `transcript.md`, which is immutable and keeps what was
  actually said. It never reaches a **quotation**: quote in the language it was
  said in, and put the English gloss after it in parentheses where the point
  turns on the wording. And it never reaches a **name** — a person, a product, a
  project or an institution keeps its own name, so `Institutionen för
  datavetenskap` is not *the Department of Computer Science* unless that is what
  it actually calls itself in English.

  Action item owners are names and follow the name rule; the item's own text is
  English like the rest.
- **U.S. spelling and conventions.** Write *organize*, *analyze*, *color*,
  *center*, *defense*, *program*, *toward*; not *organise*, *colour*, *centre*,
  *defence*, *programme*, *towards*. This is a house style, changed by editing
  this file, and it applies to the **notes only**.

  It never reaches `transcript.md`, which is immutable and keeps whatever
  spelling Whisper heard, and it never reaches a **name**: a person, a product, a
  project or an institution keeps its own spelling however British, Swedish or
  idiosyncratic it is. `Centre for Human-Centred Computing` stays exactly that,
  and so does anybody's surname. Nor is it a licence to change what somebody
  said inside a quotation — quote verbatim and spell your own prose in U.S.
  English around it.

`[[Wikilinks]]` mark people and projects: `[[Anna]]`, `[[Intake pipeline]]`. They
have no target on disk — they are what connects notes to each other, and what a
project digest is assembled around.

### The action items are parsed, so their shape matters

Referat reads the `## Action items` section of every `notes.md` and shows the
items on its own page, with an owner, a due date and the meeting each came from.
So that section is **not free prose** — keep the shape `/cleanup` writes:

```
- **[[Anna]]** — do the thing, by 2026-09-04 [00:12:40]
- **[[Anna]]** and **[[Bo]]** — do the other thing, no date
- **Unassigned** — nobody agreed to this one
```

One item per `- ` bullet at the left margin; wrapped lines are indented and are
joined back together, so never use a nested bullet. Owners are **bold**, one or
several, wikilinked or not, separated by commas or `and`; `Unassigned` where the
meeting named nobody. Then a space, an **em dash**, a space. Anything after the
bold owners that is not a separator — `*(absent)*`, a parenthetical saying who
somebody reports to — is kept and shown, so it is worth writing.

A date is read as a **deadline** only where a cue word makes it one: as the last
clause (`..., 2026-09-07`), or after `by`, `before`, `due`, `deadline` or `on`.
Write `at the 2026-09-09 meeting` or `starting 2026-09-04` for a date that is not
a deadline and it will correctly not become one. `no date` is right where the
meeting gave none — do not invent one to fill the column.

**The wording of an item is its identity.** Referat keys what has been done —
ticked, corrected, dropped — on the sentence itself, in `actions.json`. Re-running
`/cleanup` and producing the same item with the same words keeps its state; a
genuine re-wording orphans it, and Referat says so and offers to forget it. That
is a normal consequence of re-running, not a bug, but it is a reason not to
rephrase an action item gratuitously when regenerating notes.

**Never edit `actions.json`.** It is Referat's, written by `referat actions` and
by the command center. It is not a to-do list you add to; the notes are.

## days/, and `/standup`

`/standup <YYYY-MM-DD>` reads that day's `*/notes.md` and writes
`days/<YYYY-MM-DD>.md`: a **glance**, about eight bullets of one short sentence
each, saying what changed rather than what was discussed. It is read in fifteen
seconds before walking into the next thing, so it is deliberately not a digest —
`notes.md` already is one, and a paragraph here defeats the point.

It lists no action items and **counts none**: Referat parses those itself and
knows the exact number, and an estimate beside an exact number is worse than
neither.

**Every bullet ends with the id of the meeting it came from**, in single
brackets: `[2026-09-03_1408]`, or two of them where a bullet really draws on two
meetings. That citation is the only provenance the file carries, and Referat uses
it twice when it renders the summary — the id becomes a link back to that
meeting, and the **project** the meeting is tagged with is put in front of the
bullet. Which is why the prompt never asks for a project by name: `tags` live in
`meta.json`, which nothing here may read, and a project worked out from what a
meeting sounded like would be a guess wearing the face of a fact.

**Generated — do not hand-edit one.** Re-running `/standup` for that day
overwrites it, which is how you fold in a meeting that has since been written up.

## Working here

- **Never edit `transcript.md`, `meta.json`, or the WAV files.** They are the
  raw record. Referat may rewrite `transcript.md` on `referat rerun`, and
  `referat label` may rewrite a speaker label in it — those are the only writes.
- **Never read `.voices/`.** See the note under Layout.
- **Never hand-edit `INDEX.md`.** It is regenerated by `referat index` and at the
  end of every transcription, so an edit survives until the next meeting finishes
  and then vanishes.
- Write summaries, action items and notes into **`notes.md` beside the
  transcript** — through `/cleanup` where you can. Anything else you write goes in
  a new file beside it, named plainly, saying at the top which transcript it came
  from.
- Transcripts are ASR output: names, jargon, and acronyms are often wrong, and
  speaker turns can be split or merged. Quote with that caveat and prefer
  paraphrase over verbatim quotation unless the wording matters.
- Cite moments by timestamp (`[00:12:40]`) so they can be found in the audio.
- When asked about "the last meeting" or a date, list the folders and pick by
  name; do not guess.
- If a meeting still has `SPEAKER_NN` in it and you are asked who they are, the
  answer is `referat label <meeting-id>` — it plays their voice and asks. Do not
  guess a name into a note on the strength of the words alone.
- Everything here is offline and stays that way. Nothing in this folder is sent
  anywhere except by a command Niklas runs on purpose.
