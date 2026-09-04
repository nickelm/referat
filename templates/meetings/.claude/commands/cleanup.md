---
description: Write notes.md for one meeting from its transcript
argument-hint: <meeting-id>
allowed-tools: Read, Write, Glob
---

Write `notes.md` for the meeting **$ARGUMENTS**, from its transcript.

Read `$ARGUMENTS/transcript.md`. If that folder or that file does not exist, say
so and stop — do not guess at a nearby meeting id. Use `Glob` on `*/meta.json`
to list the meetings if you need to show what is there.

Write `$ARGUMENTS/notes.md`. Overwriting an existing `notes.md` is fine and
expected: this command is meant to be re-run after a transcript is re-generated
or a speaker gets a name.

## Hard rules

- **Never modify `transcript.md`, `meta.json`, or any `.wav` file.** They are the
  raw record. `notes.md` is the only file you write.
- **Never read `.voices/`.** It holds voiceprints of everyone who has been
  recorded — biometric data about people who never asked to be in a database. It
  is no part of any note. A permission rule denies it; do not work around it.
- **Never put a real name on a `SPEAKER_NN`.** If the transcript says
  `SPEAKER_02`, your notes say `SPEAKER_02`. Naming a speaker is
  `referat label <meeting-id>`, which plays their voice and asks — it is not
  something to infer from the words. If the speaker names themselves, or someone
  addresses them by name, you may write `SPEAKER_02 (introduced themselves as
  Anna)` once, and nothing stronger.
- The transcript is ASR output. Names, jargon and acronyms are often wrong, and
  speaker turns can be split or merged. Prefer paraphrase; quote verbatim only
  when the exact wording matters, and mark it as a quote.
- Do not invent decisions, owners or dates. If the meeting did not settle
  something, the notes say it did not.

## The file to write

First line is an H1: a short, specific title naming what the meeting was about —
not "Meeting notes", not the date. `INDEX.md` and the per-project digests both
read that line as the meeting's title, so it stands alone out of context.

Then, in this order, dropping any section that would be empty:

```
# Weekly sync on the intake pipeline

**2026-08-27** · 58 min · [[Niklas]], [[Anna]], SPEAKER_02 · transcript: [transcript.md](transcript.md)

## Decisions

- ...

## Action items

- **[[Anna]]** — ..., by 2026-09-04
- **Unassigned** — ...

## Discussion

### ...

## Open questions

- ...
```

- The date and duration come from `meta.json`'s `started_at` and
  `duration_seconds` — read it, do not compute them from timestamps.
- **Action items name an owner and a date when the meeting gave one**, and say
  `Unassigned` or `no date` when it did not. A guessed owner is worse than none.
- **Discussion** is the substance: what was actually said and why, in a few
  paragraphs or short `###` subsections. Not a transcript in miniature and not a
  bare bullet list — someone who missed the meeting should be able to read it
  instead of the transcript.
- Cite a moment by its timestamp, `[00:12:40]`, wherever a reader might want to
  go back to it.

## Wikilinks

Write `[[Anna]]` for a person and `[[Intake pipeline]]` for a project or an
ongoing thread of work. That is what makes a note connect to the others in this
folder, and at build step 13 it is what a project digest is assembled around.
Use the exact name the transcript uses for a person; do not wikilink a
`SPEAKER_NN`.

## Language and spelling

**Write the notes in English, whatever language the meeting was in.** Meetings
here are held in English and in Swedish, and `transcript.md` is in whichever was
spoken -- Referat detects the language per channel, so one transcript may be
Swedish on the microphone and English on the far end. The notes are not a
translation of it, they are the writing-up of it, and they are always English:
they are what gets pasted into a shared document, read months later, and
assembled into a project digest, and a notes folder that switches language by
meeting is one nobody can skim.

Quote in the language the thing was said in, and put an English gloss after it in
parentheses where the point turns on the wording.

Write that English as **U.S. English**: *organize*, *analyze*, *color*, *center*,
*defense*, *program*, *toward*.

This applies to your own prose and nothing else. It never touches
`transcript.md`, which is immutable. It never touches a **name** — a person, a
product, a project or an institution keeps its own spelling, however British,
Swedish or idiosyncratic: `Centre for Human-Centred Computing` stays exactly
that, and so does anybody's surname. And it never rewrites the inside of a
quotation: quote verbatim, and spell your own prose around it.

The rule lives in this folder's `CLAUDE.md` under *notes.md, and `/cleanup`*.
Change it there if the house style changes.

## Markdown to stay inside

These notes are translated into Google Docs later, by a converter that handles
exactly this subset. Anything outside it is written into the doc as raw
characters, so stay in it:

`##` and `###` headings · `**bold**` · `*italic*` · `` `code` `` · one level of
`-` bullets · `[text](url)` · `[[Wikilinks]]` · ordinary paragraphs.

No `####`, no nested bullets, no numbered lists, no tables, no block quotes, no
horizontal rules, no fenced code blocks. The one H1 is the title line and there
is never a second one.

When you are done, say which file you wrote and give a one-line summary of the
meeting.
