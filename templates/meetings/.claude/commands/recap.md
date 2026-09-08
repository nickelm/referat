---
description: Write a short brief on one project - where it stands, what needs discussing - from the notes of every meeting tagged with it
argument-hint: <project-id>
allowed-tools: Read, Write, Glob
---

Write `recaps/$ARGUMENTS.md`: a brief on the project **$ARGUMENTS**, from the
bundle Referat has assembled at `recaps/$ARGUMENTS.bundle.md`.

Read that bundle and **nothing else**. If it is not there, say so and stop — do
not go looking for the project's meetings yourself, do not read any `notes.md`
directly, and do not write a recap of a project you were not asked about.
Referat assembles the bundle from the meetings a person tagged with this
project, in order, and the bundle is the whole of what you know about it.

Overwriting an existing `recaps/$ARGUMENTS.md` is fine and expected: this is
re-run whenever another tagged meeting gets notes, and every run writes the
recap from scratch. Do not read the old recap and do not fold it in.

## What the bundle is

It opens with a frontmatter block between two `---` lines — `project`,
`generated`, `meetings`, `notes` — then a title, then the `notes.md` of every
meeting tagged with this project, **oldest first**. Each meeting's notes begin
with a comment line naming it:

```
<!-- meeting 2026-09-01_0900: 2026-09-01 09:00, 13:50 -->
```

Everything from that line to the next such line is one meeting's notes, exactly
as written by `/cleanup`, with their own `#` title and `##` sections. The comment
lines are the only boundaries; a `#` heading inside is a note's own title and
never a new meeting.

## This is a brief, not a digest

Someone reads this at their desk **minutes before the recurring meeting for this
project**, to remember where things stand and what has to be raised. It is read
once, in under a minute, and it is never kept — the next run replaces it. So:

- **`## State`: one short paragraph.** Where the project stands now: the
  decisions already made, and what was concluded most recently. At most five
  sentences. No bullets, no sub-headings.
- **`## Open`: a brief bullet list.** What needs discussing: questions raised and
  not resolved, actions assigned and not reported back on, decisions explicitly
  postponed to a later meeting. **At most eight bullets, one sentence each.** If
  more than eight things are open, keep the eight most recent and most
  consequential; a brief that has to be scrolled has stopped being one.
- **Nothing else.** No third section, no summary of each meeting, no history.
  `notes.md` already holds that and is one click away.

## Two things about time

- **Later meetings override earlier ones.** Where two meetings say different
  things about the same question, the later one is what stands, and the earlier
  view is not mentioned unless the reversal itself is the news.
- **Something raised in one meeting and resolved in a later one belongs in
  State, not Open.** Read the whole series before writing a single Open item,
  and check each candidate against every later meeting. Without this rule Open
  is a list of every question ever asked, which is the failure this brief
  exists to avoid.

## Citations

**Every Open bullet ends with `[<meeting-id>]`** — the id from the comment line
of the meeting that raised it, spelled exactly as it is there, in single
brackets: `[2026-09-03_1101]`. Where an item was raised in one meeting and last
discussed in another, cite the one that raised it. Never cite a meeting that is
not in the bundle.

Referat turns each citation into a link to that meeting when it renders the
recap, and the citation is what says how old an open item is. A bullet without
one renders with no link, which is the honest picture of a line that does not
say where it came from.

## Hard rules

- **Read only `recaps/$ARGUMENTS.bundle.md`.** Not `*/notes.md`, not
  `transcript.md`, not `meta.json`, not the old recap.
- **Never modify anything except `recaps/$ARGUMENTS.md`.**
- **Never read `.voices/`.** It holds voiceprints of everyone who has been
  recorded — biometric data about people who never asked to be in a database. A
  permission rule denies it; do not work around it.
- **Do not put a real name on a `SPEAKER_NN`.** If a note says `SPEAKER_02`, so
  does this. Naming a speaker is `referat label <meeting-id>`.
- **Do not invent decisions, owners or dates.** If the notes leave something
  unresolved, it is open; if they settle nothing, State says the series has
  settled nothing yet.
- **Do not count action items** and do not list them as a block. Referat parses
  them itself and knows the exact number. An action item belongs in Open only
  where it is genuinely something to discuss — assigned and not reported back,
  or blocking something — and then as a sentence, not a copy of the bullet.

## The file to write

```
---
project: aixvisxaccess
generated: 2026-09-08T14:22:10
meetings:
  - 2026-09-01_0900
  - 2026-09-03_1101
notes:
  2026-09-01_0900: 3fa9c2e1b7d4a0f1c8e2b6d5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5
  2026-09-03_1101: 9b1e4d7a2c5f8e0b3d6a9c2f5e8b1d4a7c0f3e6b9d2a5c8f1e4b7d0a3c6f9e2
---

# AIxVISxAccess

## State

The review is a resubmission of the March paper, and its research questions
have been restructured into two, one on the needs of PWDs for data sensemaking
and one on AI's role, with [[Duosi]] synthesizing the corpus rather than
adopting a single definition. The Dashboard QA prototype was abandoned on
2026-09-03 in favor of a D3 dashboard with real data access, to be restarted
after the CHI deadline.

## Open

- Whether the four research questions are written as four or as two with
  sub-parts is still [[Duosi]]'s call and has not been made. [2026-09-01_0900]
- The data sensemaking table was delayed and no meeting has picked it back up.
  [2026-09-01_0900]
- [[Niklas]] owes the survey paper's full 80-participant analysis before the
  draft goes out. [2026-09-03_1101]
```

- **The frontmatter first, copied verbatim from the top of the bundle** — every
  line between and including the two `---` lines, the shas included, character
  for character. Referat reads it back to tell whether the recap is still
  current, and it compares what you wrote with what it gave you. Do not add
  keys, drop keys, reorder the meetings or shorten a sha.
- Then an H1: the project's name as the bundle's title gives it, and nothing
  else.
- Then `## State`, then `## Open`, and nothing after.
- `[[Wikilinks]]` for people and projects, as everywhere else in this folder,
  spelled as the notes spell them.

## Spelling

**U.S. English**, as everywhere else in this folder: *organize*, *analyze*,
*color*, *center*. Names keep their own spelling however British, Swedish or
idiosyncratic — `Centre for Human-Centred Computing` stays exactly that — and
quotations are never respelled. The rule lives in this folder's `CLAUDE.md`.

## Markdown to stay inside

`**bold**` · `*italic*` · `` `code` `` · one level of `-` bullets under `## Open`
only · `[text](url)` · `[[Wikilinks]]` · `[<meeting-id>]` citations · one
paragraph under `## State`. No headings other than the H1, `## State` and
`## Open`; no nested bullets, no numbered lists, no tables, no block quotes, no
fenced code blocks.

A citation is a bare `[2026-09-03_1101]`, never a Markdown link — Referat makes
it one, and a link you wrote yourself would point at nothing.

When you are done, say which file you wrote and give the one-sentence version of
where the project stands.
