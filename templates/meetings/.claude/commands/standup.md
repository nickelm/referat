---
description: Write a short glance over one day's meetings, from their notes
argument-hint: <YYYY-MM-DD>
allowed-tools: Read, Write, Glob
---

Write `days/$ARGUMENTS.md`: a short summary of the day **$ARGUMENTS**, from the
notes of the meetings held that day.

Use `Glob` on `$ARGUMENTS_*/notes.md` to find them. If that matches nothing, say
so and stop — do not summarize a different day, and do not fall back to reading
transcripts. If a meeting folder from that day exists but has no `notes.md`, name
it at the end as one you could not include; do not skip it silently.

Overwriting an existing `days/$ARGUMENTS.md` is fine and expected: this is re-run
whenever another meeting on that day gets notes.

## This is a glance, not a digest

Someone reads this to remember what happened — yesterday, or so far today —
before walking into the next thing. It is read in about fifteen seconds, and
everything below follows from that:

- **Bullets, not paragraphs.** One short sentence each. No `###` subsections.
- **About eight bullets, and never more than twelve.** If the day had six
  meetings, that is roughly one line each plus a couple that cut across them.
- **Lead with what changed**: what was decided, what got settled, what broke.
  Something that merely got discussed is worth a line only if nothing was
  decided all day.
- **Say who.** `[[Wikilinks]]` for people, as everywhere else in this folder.
- **Say where it came from.** Every bullet ends with the id of the meeting whose
  notes it came out of, in single brackets: `[2026-09-03_1408]`. A bullet that
  genuinely cuts across two meetings ends with both, separated by a space. See
  *Citations* below — this one is not optional, and it is not decoration.
- **Do not re-summarize each meeting.** `notes.md` already did that and is one
  click away. If a bullet is only useful with three clauses of context, it
  belongs in the meeting's own notes and not here.
- **Never invent a connection between two meetings.** A theme is worth a line
  when the notes plainly show one; a resemblance you had to reach for is not.

## Hard rules

- **Only read `*/notes.md`.** Not `transcript.md`, not `meta.json`, not a `.wav`.
  The notes are the derived, readable record and they are enough; the transcript
  is evidence, and a summary of a summary does not need it.
- **Never modify anything except `days/$ARGUMENTS.md`.** Not a `notes.md`, and
  certainly not `meta.json` or a transcript.
- **Never read `.voices/`.** It holds voiceprints of everyone who has been
  recorded — biometric data about people who never asked to be in a database. A
  permission rule denies it; do not work around it.
- **Do not put a real name on a `SPEAKER_NN`.** If a note says `SPEAKER_02`, so
  does this. Naming a speaker is `referat label <meeting-id>`.
- Do not invent decisions, owners or dates. If the day settled nothing, the
  summary says the day settled nothing.

## Citations

**Every bullet ends with `[<meeting-id>]`**, the folder name whose `notes.md`
that line came from — `[2026-09-03_1408]`, exactly as it is spelled on disk. Two
ids where a bullet really draws on two meetings, space-separated; never an id for
a meeting you did not read.

This is the one piece of provenance the summary carries, and Referat uses it for
two things when it renders the file:

- The citation becomes a **link to that meeting**, so a line can be followed back
  to the notes it came from.
- The **project** the meeting is tagged with is put in front of the bullet.

That second one is why you are not asked to name a project yourself. A meeting's
tags live in its `meta.json`, which you may not read, and a project worked out
from what a meeting sounded like would be a guess wearing the same face as a
fact. Cite the meeting — which you know, because you read its notes — and Referat
joins on the tag a person actually put there.

A bullet with no citation still renders. It renders with no project and no link,
which is the honest picture of a line that does not say where it came from.

## Action items

**Do not list them, and do not count them.** Referat parses action items out of
each meeting's `notes.md` itself and shows them on its own page, with exact
counts per person. A copy here would be a second list that goes stale the moment
one is ticked off, and a *counted* line here would be an estimate standing next
to an exact number — the first version of this prompt asked for one and got
"about 45 action items" beside a parser that knew it was 83.

Mention an action item only where it *is* the news: something the day was
blocked on, or a deadline somebody has to act on before the next meeting.

## The file to write

```
# Wednesday, September 3, 2026

- [[Vaishali]] takes the AC appointment; the living dashboards draft goes to
  [[Eddie]] tonight. [2026-09-03_0900]
- The CHI submission for the puzzle paper is unblocked — [[Gaby]] has the
  pre-registration moving again. [2026-09-03_1030]
- Two meetings landed on author order and neither settled it; it goes to
  Monday's meeting. [2026-09-03_1030] [2026-09-03_1515]
- Teaching: the TAs have Brightspace sections now, and the learning overview
  doc still needs sharing. [2026-09-03_1315]
- [[Johannes]] is blocked until every author has an ORCID in PCS.
  [2026-09-03_1515]

Four meetings. Nothing recorded for 2026-09-03_1408.
```

- First line is an H1: the weekday and the date written out — `Wednesday,
  September 3, 2026` — and nothing else. Not the ISO form: that is the file's
  name, and this line is what a person reads.
- Then the bullets. Then, only if there is something to say, one closing line —
  how many meetings, and any whose notes are missing. **No counts of action
  items**; see above.
- No second H1 and no `##` headings — this file is too short to need one.

## Spelling

**U.S. English**, as everywhere else in this folder: *organize*, *analyze*,
*color*, *center*. Names keep their own spelling however British, Swedish or
idiosyncratic — `Centre for Human-Centred Computing` stays exactly that — and
quotations are never respelled. The rule lives in this folder's `CLAUDE.md`.

## Markdown to stay inside

`**bold**` · `*italic*` · `` `code` `` · one level of `-` bullets ·
`[text](url)` · `[[Wikilinks]]` · `[<meeting-id>]` citations · ordinary
paragraphs. No headings below the H1, no nested bullets, no numbered lists, no
tables, no block quotes, no fenced code blocks.

A citation is a bare `[2026-09-03_1408]`, never a Markdown link — Referat makes
it one, and a link you wrote yourself would point at nothing.

When you are done, say which file you wrote and give the one-sentence version of
the day.
