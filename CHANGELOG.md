# Changelog

Newest first. One entry per work session; small changes are grouped.

## 2026-09-01 — First use of the tag picker, and the two bugs in it

Reported after creating the first real projects. Both were in the tag picker, and
neither was a race: one was a mutated set used as its own baseline, the other a
cache with no path that invalidated it.

### A project created in the picker was never applied to the meeting

`editTags` built one set, `current`, from the meeting's tags and handed it to the
picker as the tick state. Creating a project inside the picker did `current.add(id)`
so it would come back ticked. Then the caller computed
`added = [...picked].filter(id => !current.has(id))` — **against the same set**.
The new id was in it, so it was in neither `added` nor `removed`, `referat tag`
was never called, and the project existed while the meeting stayed untagged.
Exactly the report: "it was created but could then not be used to tag the
meeting."

Two sets now. `carried` is what the meeting has on it, never moves, and is the
only baseline the diff uses; `checked` is the picker's tick state, starts as a
copy, and grows on a create. Demonstrated on the real CLI: the same inputs give
`added = []` against the old baseline and `added = ["<new-id>"]` against the new
one, and a replay of the whole flow leaves the meeting carrying the project.

### The picker showed one of two projects

`editTags` took its project map from the listing the sidebar already held, to
save a subprocess. Nothing invalidated that cache when a project was created
without a refresh following — and `editTags` itself was the worst offender, since
it returned early **without calling `onChanged()`** whenever the tags came out
unchanged or the picker was dismissed. Create a project, dismiss, reopen: the new
project is gone.

The picker now reads `project list --json` fresh, keeping the cached map only as
the fallback for a failed call. A third of a second at the moment somebody opens
a picker is worth less than a picker that cannot be trusted. And every exit path
refreshes the sidebar when a project was created, whatever happened to the tags —
a project made here is a project the next picker and the next group heading have
to know about.

### Two smaller things found while in there

**`referat project add <name> --json`** emits the project it just created. The
extension used to run `project add`, throw the output away, then find the project
back in `project list` **by display name**. Two projects are allowed to share a
name, so `find` would hand back the older one's id — the meeting tagged with the
wrong project — and any disagreement between Python's `" ".join(name.split())`
and TypeScript's `trim().replace(/\s+/g, " ")` would have returned nothing at all.
An id is `slugify` plus a `-2` collision suffix; only the command that made it
knows which one it made. That is a fifth JSON document, added for the same reason
as the first four.

**Creating reopens the picker rather than re-rendering it.** Assigning
`picker.items` makes VS Code recompute which rows are ticked, so setting
`selectedItems` on the next line races that recomputation. It was not the cause of
the reported bug — that one was deterministic — but it is a real race and the
`items`/`selectedItems` pair cannot be made safe. A fresh picker built from the
updated maps has no selection to preserve, and costs one repaint.

### Copying out of a note now pastes as Markdown

The seeded `.vscode/settings.json` sets `editor.copyWithSyntaxHighlighting: false`.
VS Code puts two flavours on the clipboard when you copy from an editor — plain
text, and an HTML one carrying the theme's colours and font — and Word, Google
Docs and Outlook all prefer the HTML one, which is why a pasted chunk of a note
arrived as dark-background monospace. With the HTML flavour off, a paste anywhere
is the raw Markdown with its `##` and `-` intact.

Worth stating as a pair, since the two halves answer different questions: **copy
from the source for Markdown syntax, from the preview for real headings and
bullet lists.** Reaching the source at all takes the preview's *Open Source*
action or *Open With… → Text Editor*, because `*.md` is associated with the
preview editor in that workspace — which stays, since reading is what that folder
is for.

### U.S. spelling in the notes

A house style, written in the meetings folder's `CLAUDE.md` and again in the
`/cleanup` prompt, so it is changed by editing Markdown rather than code — which
is what the prompt being a versioned file is for.

It is bounded in three directions, and the bounds are the point. It never touches
`transcript.md`, which is immutable and keeps whatever Whisper heard. It never
touches a **name**: a person, a product, a project or an institution keeps its own
spelling however British, Swedish or idiosyncratic, so `Centre for Human-Centred
Computing` stays exactly that. And it never rewrites the inside of a quotation.
Same shape as every other rule here — a normalization that reads as authoritative
has to be kept off anything it could be wrong about.

## 2026-09-01 — One label per person, a delete verb, and a sidebar grouped by project

The first real remote meeting. `2026-09-01_2102` is a 19-minute Teams call with
three people in it, and it closed most of what the steps-10-to-15 gates were
waiting on at once: the loopback channel finally had voice in it, diarization ran
on a real call, `referat label` named four speakers, the webview's `<audio>`
element played real snippets, and `/cleanup` met a transcript with diarized
speakers. All of that worked. Three things it surfaced did not.

### The owner appeared under two names in one transcript

`meta.json` shows the microphone diarized into **two** clusters: `SPEAKER_01`
matched the owner's voiceprint at 0.857, and `SPEAKER_02` matched nothing at all
(0.035) and was named by hand. The file came out with **178 `ME:` lines and 4
`Niklas:` lines for the same voice**.

The cause was `transcribe._owner_to_me`, which rewrote a voiceprint-matched owner
to the literal `ME` for presentation while `label.apply_name` wrote the name it
was given. Two paths, two renderings, one person. **The rewrite is deleted**, and
the owner is now written by name like everybody else.

Two bugs hanging off it went with it, both of which had to be found by reading
rather than by watching:

- **`referat label --forget <owner>` could not revert those lines.** It builds
  its reverse mapping out of `speaker_names`, so it went looking for `Niklas:` in
  a file that said `ME:` and silently reverted nothing — while `clear_stored_name`
  and `speaker_names` forgot the cluster anyway. `SPEAKER_01` would then have been
  offered again by `unknown_speakers` with no snippets (an auto-matched cluster is
  cut none) and no sample lines (they say `ME`), i.e. permanently unnameable.
- **A `rerun` flipped a hand-labeled name back to `ME`**, since it clears
  `speaker_names` and lets the pipeline re-derive from the database. The label of
  one's own voice was not stable across a rerun.

**`ME` now means exactly one thing and it is true wherever it appears**: the
microphone recorded this line and nothing attributed it — diarization did not run
on the channel, or no turn overlapped the segment. `REMOTE` is its counterpart on
the loopback. It must never be widened back into a name: `2026-08-28_1152` is 258
lines of `ME` that are actually two people, and spelling a name onto unattributed
speech is the same failure as putting a name on a `SPEAKER_NN`. Pleasingly, the
sentence the meetings `CLAUDE.md` already carried — *"a transcript of nothing but
`ME` on the microphone means diarization did not run on that channel"* — was
misleading before this change and is exactly true after it.

### `referat relabel [<meeting-id>]`

The same shape of repair as last session's `reflow`, and for the same reason: the
transcripts written under the old rendering have released their audio, so no
`rerun` can regenerate them. It rewrites `ME:` to the owner's name **only for a
meeting whose microphone clustered into nothing but the owner** — measured off the
clustering the way `bootstrap_owner`'s condition is, rather than inferred from
what kind of meeting it was.

That gate is the whole design, and a blanket rewrite would have been a disaster:
`2026-08-28_1152` has no mic `speakers` block at all and 258 `ME:` lines, and
renaming them would have been the exact failure `CLAUDE.md` records about merging
a two-person meeting into one speaker, in reverse and irreversibly. The gate sorts
the five real meetings correctly — three refused for never having diarized the
mic, `2026-09-01_0900` refused because `Duosi` is a mic cluster there, and only
`2026-09-01_2102` rewritten.

**A deliberate, bounded exception is buried in it and is written down rather than
left to be discovered.** Some of those 178 lines were `diarize.assign`'s
no-overlapping-turn fallback rather than the owner's matched cluster, and nothing
on disk records which. Naming them is defensible *only* because the gate has
proved the sole voice diarization found on that microphone was the owner's —
which is a much narrower claim than "the microphone is the owner", and is not a
licence to relax the gate later.

Verified the way `reflow` was: backed up first, then 182 `Niklas` / 71
`Mohammad` / 15 `Gaby` and no `ME`, **zero** lines differing outside the label
field across all 271 lines, and a second run writing nothing.

### `--forget` no longer collapses a split person onto an arbitrary label

Pre-existing, and the fix above made it both more likely and much worse. When one
name covers two labels in a meeting — which is what happened here, and what
`TODO.md` has an open item about — `forget`'s `{n: label for label, n in ...}`
inverts many-to-one and silently keeps whichever came **last** in dict order.
After this session's change all 182 mic lines say `Niklas`, so a
`--forget Niklas` would have rewritten all 182 to `SPEAKER_02:`.

It now reverts to the **lowest** label and logs that it did. Deterministic rather
than dict-ordered, and honest: the two labels share a name because a person said
they are the same voice, so collapsing them asserts nothing nobody asserted
already. What it costs is the other label, which keeps its embedding and loses its
lines — recorded under "Surfaced later" rather than fixed here.

`apply_name` also warns when `relabel_transcript` changes nothing, which is the
third way `meta.json` and the Markdown could drift apart while every write
reported success.

### `referat delete <meeting-id> [--yes]`

The one deletion Referat did not have. `promote --release-audio` deletes the WAVs,
`project rm` a project, `label --forget` a person; a recording that should never
have been kept had to be removed in Explorer, which also left the meetings
`INDEX.md` describing it.

Modeled on `promote` throughout: resolve through `_resolve_meeting` so it reaches
both roots, refuse while the meeting is `recording` or `transcribing`, print the
folder and its size, confirm, delete through the new `paths.remove_meeting_dir`,
then regenerate the dashboard. `--yes` is not a convenience — the prompt reads
`input()` and answers *no* on EOF, so the extension could not confirm without it,
exactly as with `label --forget`.

**Two sentences the confirmation carries**, because both are true and neither is
obvious. A meeting inside a **synced** meetings folder is not really gone — Dropbox
keeps deleted files and prior versions on its servers for weeks, which is the same
fact that put recording and transcription in a staging folder — while a staged one
is; and the **voiceprints** the meeting contributed stay in `.voices/`, because
deleting a meeting is not deleting a person.

`remove_meeting_dir` refuses a folder holding no `meta.json`, which is the
definition of a meeting `list_meeting_dirs` already uses. It is also the one
function in `paths.py` that logs instead of raising: a recursive delete's reason is
worth keeping even where the caller only needs to know it did not happen.

### The sidebar groups by project

One flat reverse-chronological column does not survive a year of meetings. Rows
are now in collapsible sections: one per project, ordered by its most recent
meeting; then one per **orphaned** tag id, shown rather than hidden, because a tag
vanishing quietly off three meetings is how you lose track of what a meeting was
about; then *Untagged* last, which is the queue. A meeting carrying two tags is
drawn under **both** — a project is a label, not a container.

A search box joins the *Untagged only* toggle, matching title, id, date, status
and project name. Which sections are folded is kept across a reload through
`vscode.setState`; expanded *rows* deliberately are not, since refilling one costs
an interpreter start.

**None of this reached Python.** `list --json` already carried `tags` and the
top-level id-to-name map, and ordering stays presentation: `referat list` is still
oldest-first. The page also stays plain DOM over a JSON document — its only VS Code
coupling is `acquireVsCodeApi()` and the `--vscode-*` theme variables, and the
grouping did not deepen it.

Every row gained a **Delete…** button, last and styled as danger, behind a modal
carrying the CLI's two caveats. The modal is the one place the extension says
something Python also says, because it is shown *before* the command runs and
there is no output yet to quote; a refusal still comes back through `mutate`
unedited.

### Naming yourself was unguided

Four speakers to name after one call, and one of them was the person doing the
naming, with nothing on screen saying which cluster came out of their own
microphone. `referat label --json` now carries `channel` per speaker and `owner`
at document level; the sidebar's speaker card says *from your microphone* or
*from the call*, and puts the owner's name first among the chips for a mic
speaker. The interactive prompt says it too, so the two surfaces cannot describe
the same speaker differently.

A hint and never a name applied on its own — the microphone hears the whole room —
and `owner` crosses the wire as a string rather than as a pre-sorted list, because
what the owner is *called* is Python's to know and the order chips appear in is
the page's to decide.

### Documentation

`CLAUDE.md`'s `ME` paragraph is inverted, its transcript sample shows a named
owner beside an unattributed `ME` line, and its CLI list — which had lost `reflow`
already — now carries `config`, `reflow`, `relabel` and `delete`. Both copies of
the meetings `CLAUDE.md` were rewritten and, while there, **reconciled**: the live
copy still had the pre-reflow sample block, which was the outstanding hand-merge
from last session. They now diff clean. `config.example.toml` and the live
`config.toml` no longer promise that `owner_name` renders your lines as `ME`.

## 2026-09-01 — Transcripts rendered as one paragraph, which was the Markdown

`transcript.md` put one utterance per line separated by a single newline, and in
CommonMark a newline inside a block is a *soft* break — rendered as a space. So
every transcript came out of the Markdown preview as one unbroken paragraph:
2026-08-28_1152 is 258 utterances and rendered as a single wall of text. The
preview was right and the file was wrong, which matters because the meetings
folder opens Markdown rendered, so that is how these files are normally read.

**`transcribe.ENTRY_SEPARATOR` is a blank line**, and `render_transcript` joins
on it. Two trailing spaces are the other hard break and were rejected: they are
two characters nobody can see, in a file plenty of editors would strip them out
of on save. Making each entry a `- ` list item was rejected too — it reads well
and breaks both of the line-anchored patterns in `label.py`, which is a real cost
for a cosmetic gain.

**`referat reflow [<meeting-id>]`** repairs the transcripts already written,
because a renderer fix reaches only new ones and the existing transcripts have
had their audio released — no `rerun` can ever regenerate them. `reflow` is the
only way they become readable. It is a permanent verb rather than a throwaway
migration, since it is also the repair for a transcript mangled by hand.

**It is not an exception to the immutability rule.** `reflow_transcript` inserts
whitespace *between* lines and rewrites none of them, which is the same
narrowness `referat label` observes when it rewrites the label field. It is
conservative about where: a blank line goes in only where the lines on **both**
sides match the entry shape, so a hand-annotated transcript keeps that part
exactly as it is instead of being reformatted on a guess. And it is idempotent —
a second run inserts nothing and writes no file.

`markdown.preview.breaks: true` in the meetings folder's `.vscode/settings.json`
was the tempting alternative, since it would have fixed every existing transcript
with no file touched at all. It was rejected on a fact about VS Code: folder
settings apply only when that folder is in the workspace, and the sidebar opens
transcripts from whichever window is running — usually the repository. It would
have worked in one window and not the one actually used.

**Verified before anything was written**: over all four real transcripts, the
reflow is idempotent and the sequence of non-blank lines is identical before and
after; `reflow_transcript(render_transcript(...))` is a no-op, which is what
keeps the renderer and the repair from drifting; and `label.relabel_transcript`
still renames a speaker in a reflowed transcript while leaving that same name
untouched where it appears *in the speech*. Then run for real against a backup
taken first: 257 and 318 blank lines inserted, byte growth exactly the inserted
newlines, every non-blank line preserved, and a second run rewriting nothing.

**The live meetings folder's `CLAUDE.md` still shows the old sample.**
`paths.seed_tree` never overwrites a seeded file, so the repo template and the
live copy have drifted by exactly this one code block and it has to be
reconciled by hand — the standing cost of the seeding rule, and the half that
gets forgotten.

## 2026-09-01 — Build step 15: the extension becomes the primary UI

The TreeView from step 11 is gone. `MeetingsProvider`, its three node classes,
the composed `contextValue` string, the five `view/item/context` entries and
`labelPanel.ts` all went with it, and what replaces them is one webview view:
a row per meeting, newest first, carrying its title, duration, start time, the
project tag chips step 14 made possible and a strip printing `meta.json`'s
`status` field. A webview inside an extension is **not a web UI** — that rule is
about Flask, FastAPI, localhost and a browser front end, and there is still none
of it.

**The Python half came first, because the extension may not reimplement
anything.** Two additions and one split:

**`referat promote <id> [--release-audio]`** is the off-ramp for a meeting the
quality gate stranded in staging, and it is the sharp one. `promote_meeting`
refuses to move a folder still holding WAVs, and that refusal *is* the invariant
the staging split exists for: deleting a file inside a synced folder does not
delete it, so a WAV that reached the meetings folder would be retained on
Dropbox's servers for weeks after `audio_released` said it was gone. So accepting
a gate-failed transcript cannot mean "promote with the audio". It means delete
the recordings and then promote, and `--release-audio` is what says so at the
prompt; without it `promote` refuses and names the files. The flag also writes
`transcribed`, because `gate_failed` was always a statement about the *audio* not
having been trusted enough to delete — the transcript was never in doubt, and a
person overruling the gate leaves the meeting where a clean run would have.

The bare form is not a courtesy: it moves a staged meeting whose audio has
already gone, which is the retry for a `promote_meeting` that failed to move the
folder.

**`transcribe.release_audio`** is the deletion, split out of
`release_audio_if_clean`, which keeps the whole gate and now only decides. Two
things delete a meeting's WAVs — the gate, and a person overruling it — and
`audio_released` has to mean the same thing whichever wrote it. The split also
corrected a latent case: the old loop `stat`ed each file and read a missing one
as a failure, which is wrong for a half-released meeting and right for nothing.

**`referat status --json`** is the fourth JSON document, after `list`, `label`
and `project list`, and exists for the same reason as the first three: the status
bar had to read the tray's state, and the alternative was parsing prose written
for a person. `elapsed` is `format_duration`'s own output rather than a number of
seconds — had it been seconds, the bar would have formatted it in TypeScript,
which is the duplication this extension is arranged to avoid. The price is that
**the status bar does not tick**: it says what Python said when it was last
asked. `stale` is the other field worth having, since `running` false with
`stale` true is a tray that *died*, which is a different thing from no tray
having run.

**The sidebar.** `src/sidebar.ts` sets `webview.html` once and thereafter posts
documents to it. Re-rendering on every refresh would have been simpler to write
and wrong to use: transcription rewrites `meta.json` several times a meeting, and
every one of those would collapse an expanded row and stop a snippet
mid-playback. `media/sidebar.js` — step 11's `label.js`, grown — builds every
node with `textContent` and never `innerHTML`, because a meeting title comes out
of somebody's `notes.md` and a speaker's line out of a transcript. Speaker
labeling folded into an expandable section of the row it belongs to, and fetches
`label --json` only for the meeting somebody actually opened. The file watcher is
untouched: both roots, `RelativePattern`, 300 ms debounce, all of it hard-won.

The one thing that had to be learned about `WebviewView`: the snippet players
need the meeting roots to be `localResourceRoots`, and the roots are only known
once `list --json` has answered. So the provider re-assigns `webview.options`
when the roots change and keeps the last listing, and a page that reloads is
answered from memory rather than with another interpreter start.

**Projects and tags.** `src/projects.ts` holds both flows as native QuickPicks —
VS Code already has a multi-select with a filter box, and a webview copy would be
a worse one. The tag picker is pre-checked with what the meeting carries and
applies the difference as a `tag` and an `untag`; *Create project "…"* appears as
you type, and choosing it is a **detour rather than an answer** — it creates the
project, checks it and hands the picker back, so one accept cannot silently mean
both "make this" and "and that is the whole tag list". An orphaned tag is offered
too, checked and marked as one: it is the tag somebody actually needs to remove,
and a picker built only from the known projects would have been the one place it
could not be.

**Attaching a project's Google Docs is the one bullet of step 15 that is not
built**, and deliberately: `project link-doc`, `unlink-doc` and `sync` are absent
from the parser until step 13, because a verb that exists and answers "not built
yet" reads as a bug. There is nothing to shell out to. It is recorded in `TODO.md`
and in `projects.ts`'s own docstring rather than left looking forgotten.

**Every mutation goes through one function**, `cli.ts`'s `mutate`, which hands
back the CLI's own complaint. That is what makes "the refusal you see is the
sentence Python printed" a property of the code rather than a habit: there is no
path by which the extension can invent a reason for refusing.

*Generate notes* now streams — the last non-empty line into the progress toast,
the whole stream into the channel — and on exit 0 calls `referat state <id>
notes-written`, the transition no pipeline can make because `/cleanup` is
forbidden from touching `meta.json`.

**Verified**: the Python half end to end against a scratch meetings folder — a
synthetic `gate_failed` meeting refused a bare `promote`, then lost both WAVs and
moved on `--release-audio`, with `list --json` showing `staged`, `status` and
`audio` all moving together, and a second run saying "already promoted"; and all
three shapes of `status --json` (no file, stale, live). `npm run typecheck` and
`npm run compile` are clean at 16.7 kB. Everything past that needs somebody to
press F5, and those checks are listed under step 15 in `TODO.md`.

## 2026-09-01 — Smart App Control took scipy, so the resampler is Referat's own

A `referat rerun` from the extension died in `decode_wav`: SAC blocked scipy's
`_odepack.pyd`, reached through `scipy.signal` -> `scipy.stats` ->
`scipy.integrate` from the one line that brought the 48 kHz loopback down to
16 kHz. Retried, it blocked `_stats_pythran`; retried again, `_sobol`; and then
it stopped, the cloud reputation having arrived. **A block here is a window, not
a verdict** — which is worse than a permanent failure, because it lands
unpredictably and it landed on the only thing standing between a recording and
its transcript. All 106 of scipy's `.pyd` files are unsigned and always will be.

**`transcribe._resample` replaces `resample_poly`, in numpy and the stdlib.** A
polyphase kernel — one windowed sinc per output phase, so 44.1 kHz costs no more
than 48 kHz — gathered in 64k-sample blocks so the temporaries stay around 8 MB.
It is deliberately contract-compatible with what it replaces: same output length,
same zero-phase alignment, same zero padding past the ends, same Kaiser window at
the same default width, so a `rerun` of an older meeting decodes to the samples
it decoded the first time. Validated against `resample_poly` while it was still
importable — **138 dB agreement on a real `system.wav`, identical -67.6 dBFS
stopband, and the 60-second chunked path byte-identical to a 0.5-second one**, so
the overlap-save seams are exact. It costs ~12 s per meeting-hour.

**The rule this settles** is narrower than the "stub out anything that ships
FFmpeg" that PyAV and torchcodec produced: *nothing on the path from a WAV to a
transcript may depend on unsigned native code that is not already unavoidable*.
numpy is unavoidable, so a numpy resampler adds no new way to fail; scipy on that
path added a hundred. Diarization still reaches scipy through
`pyannote -> lightning -> torchmetrics` and is **left alone**, because there is no
seam to cut and because it already degrades to unnamed speakers — which is the
degradation this rule exists to permit.

**`DecodeError`**, a `TranscriptionError` subclass, is the second half. The
original failure re-ran the entire meeting on the CPU and failed in exactly the
same place minutes later, because `transcribe_meeting` reads any exception on the
CUDA attempt as a GPU problem. Decoding touches no device and happens before a
model is loaded, so a `DecodeError` now propagates instead — one traceback, the
real cause at the bottom of it, and no wasted CPU pass.

Verified by re-running 2026-08-28_1104 end to end with scipy artificially blocked
in the manner SAC blocks it, and then for real: both channels transcribed on
CUDA, no CPU fallback, `system.wav` resampled 48000 -> 16000. That rerun also put
step 14's `gate_failed` on disk for the first time from the pipeline rather than
from a test — the mic channel is 21 seconds of near-silence, the gate refused it,
and `referat list` now says `gate_failed` where it used to say `transcribed` and
leave you to work the rest out.

## 2026-09-01 — Build step 14: projects become labels, and the lifecycle becomes a field

Two things a meeting can now say about itself that it could not before: what work
it belongs to, and where it is in its life. Both are single fields with a single
owner, which was the whole point.

**`referat/projects.py` and `<meetings_dir>/projects.json`.** One entry per
project: an immutable `id` slugged from the name at creation, a display `name`, a
list of Google Doc references for step 13, a `glossary` for step 12b and
`/cleanup`, a `description` reserved for step 17, and `created_at`. `referat
project add | rename | rm | list [--json]` maintains it, `referat tag` and
`referat untag` write `meta.json`'s new `tags` array, and every one of them is a
JSON read and a JSON write with no optional extra behind it. `link-doc`,
`unlink-doc` and `sync` are **absent from the parser** rather than present and
answering "not built yet" — they arrive with the digests.

**`rm` orphans, and the orphans are visible.** Deleting a project touches no
`meta.json`, no `notes.md` and no doc; the ids it leaves behind get a trailing
`?` in `referat list`'s new TAGS column and their own counted line under `referat
project list`. That column prints **ids rather than display names**, because the
next thing typed after reading it is `referat untag <meeting> <id>` and a table
you cannot copy out of would be worse than a slightly less readable one.
Correspondingly, `tag` refuses an id no project answers to — a typo should not be
able to manufacture an orphan — and `untag` refuses nothing, since an orphan is
precisely the tag somebody needs to be able to remove.

**`MeetingStatus` widened rather than joined by a second field:** `recording ->
recorded -> transcribing -> gate_failed | transcribed -> notes_written ->
synced`, with `failed` beside them for a transcription that raised. `gate_failed`
is what earned it — it used to be a three-way inference (status `done`, the WAVs
still on disk, the folder still in staging) that lived in no function and was
re-derived by every reader — and it is now one line in `transcribe_meeting`. The
legacy values map **purely** on load, `stopped` to `recorded` and `done` to
`transcribed`, with no look at the filesystem: doing that inference in the loader
would only have hidden it. The three meetings on this machine that are really
gate-failed still say `transcribed` and will until their next `rerun`, which is
cheaper than a migration and does not reintroduce the thing being deleted.

**`referat state <id> notes-written`** is the one transition no pipeline can
make, because `/cleanup` may not touch `meta.json`. It accepts that transition
and no other — argparse's `choices` sees to the vocabulary, and the command
checks that the meeting is `transcribed` first — and it regenerates the dashboard
afterwards, since the notes it is recording are also where the title comes from.

**A projects file that will not parse refuses to be written.** `ProjectsDB.load`
never raises, because step 12b will call it from inside the transcription
pipeline, so a malformed file loads as *no projects*. That is right for reading
and catastrophic for writing: the next `project add` would replace a file full of
projects with an empty list and say nothing. The object records `unreadable` and
every mutating verb refuses on it. Writing that guard turned up the same hazard
in `voices.py`, where `apply_name` would overwrite every voiceprint on the
machine after a failed parse — recorded under "Surfaced later" rather than fixed
here, because it is not this step's file.

**Three shared things instead of new copies.** `meeting.resolve_meeting` returns
a meeting or None *and the complaint*, so `tag`, `untag`, `state` and `referat
label` share the lookup and disagree only about the `referat <command>:` prefix —
`label` held the second copy and now calls it. The id-to-name map in `list
--json` comes out of the same `ProjectsDB.name_map` that `project list --json`
hands out, so one subprocess feeds the whole sidebar. And `projects.name_complaint`
is deliberately a **sibling** of `voices.name_complaint` rather than a call to
it: three of that function's four rules are about transcript labels and mean
nothing for a thread of work, so reusing it would have imported four checks to
get the use of none. What a project name needs is that it survives `slugify` and
that it is not `untagged`.

**`index.status_words`** prints the lifecycle with its underscores as spaces, so
the rendered dashboard says "notes written". A formatting rule and not a map of
value to sentence — a map would be a second vocabulary beside `MeetingStatus`,
and the first state nobody added a row for would render as a blank. The terminal
table keeps the underscores, because its columns are space-separated.

The extension's `MeetingJson` gained `tags` and a real `MeetingStatus` union,
`ListJson` gained the `projects` map, and the doomed TreeView learned to draw
`gate_failed` as a warning rather than as a finished meeting. Nothing else in it
moved; step 15 replaces it. `templates/meetings/CLAUDE.md` was updated for the
new vocabulary and for `tags` — **the live copy in the meetings folder is seeded
once and never overwritten, so it needs the same edit by hand.**

## 2026-09-01 — Projects become labels, state becomes a field: planning only, no code

Four decisions arrived together and none of them fit the plan as written. A
meeting belongs to *zero or more* projects rather than one; a project carries
*zero or more* Google Docs rather than one; the extension's surface is a sidebar
webview rather than a TreeView; and a meeting's lifecycle is an explicit field
rather than something every reader works out for itself. Steps 14 through 17 were
written for them, steps 11, 12, 12b and 13 were amended around them, and no code
was touched.

**The lifecycle field was the decision with something already underneath it.**
`meta.json` has had a `status` key since step 1 — `recording | stopped |
transcribing | done | failed` — and the obvious move was to leave it alone and add
a UI-facing `state` beside it. That would have been the fourth time this project
put the same fact in two places, after `voices_dir`, `format_duration` and the
meetings-folder setting the extension does not have. `MeetingStatus` is widened
instead: `recorded`, `gate_failed`, `transcribed`, `notes_written`, `synced`, with
`stopped` and `done` mapping onto the new names on load.

**`gate_failed` is the one that earns the change.** A meeting whose transcript
failed the quality gate keeps its audio, stays in staging, and is written to disk
as `status: done` — indistinguishable, in the file, from a meeting that finished
cleanly. Everything that wants to tell them apart has to ask three questions at
once: is the status `done`, are the WAVs still there, is the folder still in
`%LOCALAPPDATA%`. That inference lives in no function; every reader does it again,
and the sidebar was about to become the fourth. Writing the value down costs one
enum and one line in `transcribe.py`.

**Which then broke the off-ramp, in a useful way.** The sidebar is supposed to
give a gate-failed meeting a visible way out of staging, and "Surfaced later" has
carried an open item since step 8 wondering whether that wants a `referat rerun
--accept` that "promotes it with the audio". It cannot. `promote_meeting` refuses
to move a folder that still holds WAVs, and that refusal is the entire reason the
staging split exists — deleting a file inside a synced folder does not delete it,
so a WAV that reaches Dropbox is retained on somebody else's servers for weeks
after Referat has reported it gone. So *accept* has to mean **delete the audio and
then promote**, irreversibly, behind a modal that says so. The old item was
phrased as a convenience; it is a destructive action, and it was worth finding
that out while writing prose rather than while writing the button.

**`notes.md` cannot advance its own meeting's state.** The `/cleanup` prompt's
hard rule is that it never modifies `transcript.md`, `meta.json` or any `.wav`,
and `referat index`'s title helper reads the H1 of `notes.md` precisely so the
cleanup layer never has to edit the raw record. `notes_written` is therefore the
one transition no pipeline can make. It gets a CLI verb — `referat state <id>
notes-written` — called by whoever spawned the pass, and that verb accepts that
transition and no other, because a verb that let a caller claim `synced` would
turn the field back into a comment. The alternative was to let one reconciling
sweep infer the state from `notes.md` existing, which is exactly the inference
being removed, and would have removed it from six readers by moving it into one.

**`projects.toml` becomes `projects.json`, and the reasoning it was chosen for
goes with it.** Step 13 picked TOML and then spent a paragraph explaining why the
file could not live in `config.py` — that `config.py` promises to parse TOML and
never write it back, so hand edits and comments survive, and a machine-written
TOML file would need a header comment warning that comments do not survive.
That is a tension managed rather than resolved. JSON has none of it:
`paths.write_json_atomic` already exists, the file is rewritten whole without
apology, and nobody expects to find comments in it. `referat/projects.py` still
owns it and still is not `config.py`.

**Ids, not names, and a rename that touches one file.** A project's id is a slug
fixed at creation; `rename` changes the display name alone. `meta.json` stores
ids in a `tags` array, so renaming a project leaves every meeting record, every
transcript and every doc anchor untouched. Deleting one is the interesting case:
it orphans its tags **visibly**, and cascade-deletes no meeting, no note and no
doc. A tag quietly disappearing off three meetings is how you lose track of what a
meeting was about, and an orphan chip is a smaller problem than that.

**Fan-out made `digest` a map.** Step 13 was going to record one `{gdoc_id,
tab_id, notes_sha256, written_at}` per meeting, which was right when a meeting
reached one doc. A meeting tagged with two projects, each linked to two docs, can
be current in one and stale in another, and one flat object cannot say so. It is
keyed by `gdoc_id` now, which is also what makes `synced` computable: every doc of
every tag current, and back to `notes_written` the moment a `notes.md` sha stops
matching. None of the Docs mechanics moved — the text anchors, the reverse
document order, the UTF-16 offsets, the `tabId` on every request are all as they
were.

**The tray toast is the one item planned knowing it may not work.** "A toast
asking Project(s)?, with recent projects, still reachable in Action Center" is
not something `pystray` can raise: `icon.notify` is a buttonless balloon that
does not persist. It needs a real WinRT notification, which is a new base
dependency taken deliberately against the minimal-dependencies rule, an AUMID
that step 9's autostart shortcut may or may not already carry, and — the part
most likely to fail here — an activation path back into a running tray, or a
registered COM server to reach a toast that was missed. So the fallback is
planned alongside it rather than after it: if activation does not hold up, the
toast degrades to a plain notification and the *Tag recent…* submenu carries the
whole feature. Either way a notification that fails is a log line and never
touches the stop path.

**Step 11's Projects section is superseded and kept.** Seven boxes describing
project nodes, an *Assign to project* QuickPick and an "Unassigned" node, all of
it TreeView, none of it built. It stays in the file under a note saying what is
now false about it, the way this file keeps everything else it got wrong.
`referat project assign` and its remembered default go the same way — replaced by
`referat tag` / `untag` over a list of ids, with the tray's untagged-on-timeout
standing in for the default. The rule inside that sub-section is the one part that
survives untouched and gets louder: **nothing is inferred from a transcript.**
Step 17's notes-splitting experiment is where that will be most tempting, and it
does not bend there either — the human's tags are an input to the split, never an
output of it, and its failure mode is over-inclusive notes, which is the baseline.

**Numbers 1 to 13 did not move.** `SETUP.md`'s sections are numbered to match,
CHANGELOG entries are titled by step, and `TODO.md` cross-references by number, so
renumbering would have quietly falsified all three at once. New work is appended
as 14 to 17 and existing steps amended in place — the same reason the hotword step
earlier today became 12b rather than a renumbering. Step 12 keeps its number and
gains one sentence saying it now runs *after* step 15, because packaging a
TreeView that step 15 deletes is work done twice.

**Seven open questions were written down rather than answered**, which is the
part of this session most likely to pay for itself: where step 17's split notes
live given the folder contract says `notes.md`; whether orphan tags are ever
pruned; whether `synced` really should regress every time somebody fixes a typo
in a note; whether `claude -p` emits incremental stdout without
`--output-format stream-json`; whether step 9's shortcut carries an
`AppUserModelID`; and whether a toast activation can reach a running `pystray`
loop at all. The last two are a spike, and step 16 should not start before it.

## 2026-09-01 — Where a misheard name gets fixed: planning only, no code

Reading the first real transcript raised a question the documents had only half
an answer to. `CLAUDE.md` said the transcript's *speech* is immutable, but it
said so as a detail of the meeting folder contract, and nothing anywhere said
where a correction is supposed to go instead. This session settles that and
writes it down. **No code, no config and no template changed** — every item
below is a decision recorded in `CLAUDE.md` and `TODO.md`.

**The immutability rule is now a rule.** It moved into Conventions, where it
reads *the transcript is immutable; corrections live downstream* — a misheard
name, a mangled acronym, a turn split in the wrong place, all of them corrected
in `notes.md` and the digest and never at the source, because a transcript
somebody has fixed is no longer evidence of what was said and there is nothing
left to check the correction against. The paragraph in the folder contract was
rewritten down to what is actually contract detail — the speaker label is the
one editable field — rather than left restating the rule in a second place.

**Corrections happen in the notes, and near misses get flagged.** The meetings
folder's `CLAUDE.md` gains a *Known people and terms* section, and `/cleanup`
normalizes against it: an exact match is rewritten silently, a near miss is
corrected **and marked** — `Elmqvist (assumed transcription error: "Elmquist")`
— and a word matching neither list is left as transcribed. The flag is the whole
point. A silently applied guess about a word is the same failure as putting a
name on a `SPEAKER_NN`: it reads exactly as authoritative when it is wrong, and
this project already has a rule about that. Both files are Markdown in the
meetings folder, so this is the half that needs no code, and it is sequenced
first as **step 10's new subsection**.

**One global hotword list, as new step 12b.** Whisper hears a name right when it
has been told the name exists, so `referat/hotwords.py` will merge every name in
the voices database, every project's `glossary` and a manual
`[transcription].hotword_extras` into one list passed to
`model.transcribe(hotwords=...)`. Two things are load-bearing and both were
decided rather than assumed. The merge is called from `transcribe_channel` and
not from `cli.py` — the tray never goes through the CLI, and a list that applied
only to `referat rerun` would make a rerun produce a different transcript from
the recording it came from; *merge logic lives in the CLI* means in Python,
never re-derived in TypeScript, the same rule as `list --json`. And the list is
capped at Whisper's 224-token prompt window in a stated order — extras, names,
glossaries — with the drop logged, because a cap nobody can see becomes a bug
report about one specific name that is never heard right. A `label --forget`
purges a name from the list for free, given the merge reads the database live;
that is written down as a property to verify rather than to assume.

**Three alternatives were rejected and are recorded as rejected**, so they are
not re-proposed: per-project hotwords at transcription time (a meeting is
assigned to a project *after* it is transcribed, so there is nothing to select
on); prompting for a project between stop and transcribe (it would manufacture
that information, at the cost of putting a step that waits for a human being
into the path from stop to transcript — against *recording robustness beats
everything else*, with the tray holding the only copy of the audio while it
waits); and retaining audio so a meeting could be re-run against a glossary
learned later (the WAVs go as soon as the transcript is clean, and that is the
privacy posture, not a disk-space optimization).

**A project's `glossary` lands with step 13, not before**, and is used twice —
merged into the hotword list, which acts before anything is assigned, and handed
to `/cleanup` once a meeting *is* assigned. It is the one key in `projects.toml`
that something outside step 13 reads, so it has to work for a project that is
configured and unlinked.

**Deferred and deliberately unscheduled:** `referat glossary prune --dry-run`,
which would list terms unseen in any transcript for a long period. The cap may
never bite, and pruning a list that quietly changes how audio is transcribed is
not obviously a thing to automate. Also noted: there are now two lists of names,
the voices database and the `CLAUDE.md` table, overlapping on purpose — one says
who a voice is, the other how a name is spelled — and only the database feeds
hotwords, because a machine-read list does not belong inside a hand-written
Markdown document.

## 2026-09-01 — The first real meeting, two names in the database, and a wall that did arrive

The tray recorded a 14-minute in-person meeting between two people through one
Jabra, and the whole pipeline worked without being touched: 319 segments,
`clean=True`, then 337 diarized turns resolving to exactly **2 speakers** with
an embedding each, then 106 MB of audio released and the meeting moved into
Dropbox. Nothing failed. Three things came out of reading it afterwards.

**The speakers were nearly filed backwards.** They came out unnamed for the
ordinary reason — an empty voices database has nobody to match against — and the
first reading of who was who had `SPEAKER_01` as the owner. The transcript says
otherwise: SPEAKER_01 announces a draft and a paper count, SPEAKER_02
recommends restructuring the research questions. Naming them the other way round
would have filed the wrong centroid under the owner's name **permanently**, and
mislabeled every later meeting from it. This is what "a wrong name is worse than
no name" looks like from the outside: the guard rails in `voices.py` are all
about the machine's confidence and none of them can catch a human being
confidently wrong. Checking the content against the labels before running
`referat label` is the only defence, and `--speaker/--name` — which skips the
prompt, and therefore skips the snippet playback that would have settled it in
ten seconds — is the flag that makes it easy to skip.

Both are now named, which puts the first two voiceprints in the database.

**`config.toml` had no `[speakers]` section at all.** It was copied from
`config.example.toml` before that section existed and never caught up, so
`owner_name` was `""` — and with it empty, `voices.bootstrap_owner` and
`transcribe._owner_to_me` are both no-ops. Identification had been running all
along; the `ME` label simply could not ever be produced. Nothing reported this,
because an empty `owner_name` is also the legitimate not-yet-configured state.
The section is now present in full, comments copied across so the two files
agree. The very next `rerun` proved it live, refusing the bootstrap for the
right reason: `the microphone holds 0 voices, so it is not unambiguously
Niklas`.

**The `torchcodec` wall did arrive.** A step 7 entry below records that it
"never arrived", and TODO.md said the same in two places. Those stay as written
— this file is append-only — and this entry is the correction. That was half
right in a way worth keeping as a lesson: pyannote never *decodes* through
torchcodec, because it is always handed `{"waveform": ..., "sample_rate": ...}`,
and that reasoning was sound. But `pyannote/audio/core/io.py` imports
`torchcodec` at module scope whatever it will later be handed, and the import
alone walks `libtorchcodec_core{N}.dll` down FFmpeg majors 9 to 4, collecting a
Smart App Control refusal each time. Pyannote catches the failure, sets
`TORCHCODEC_AVAILABLE = False` and carries on — so the bill was never a
transcript, only a Windows Security toast per run, and under `pythonw.exe` even
pyannote's own warning about it is dropped because `sys.stderr` is None. The
toasts were the *only* visible symptom, which is why this survived four
transcriptions and a "resolved" checkbox before the user photographed one.

The lesson is narrower than "check your claims": the claim was about *decoding*
and the failure was in *importing*, and the two were close enough to look like
one thing. `diarize._neutralize_torchcodec` now stubs the module out before
pyannote is imported, mirroring `transcribe._neutralize_pyav` with one
deliberate difference — it never attempts the real import first. For PyAV the
attempt is the diagnosis and costs one failed load; for torchcodec the attempt
*is* the problem. There is no configuration knob, because Referat has no use for
torchcodec even on a machine where it loads perfectly; deleting the two call
sites restores the real import.

Verified directly rather than reasoned about: with the stub installed,
`from pyannote.audio import Pipeline` succeeds, `TORCHCODEC_AVAILABLE` is False,
`torchaudio` still loads (its own torchcodec use is lazy, inside function
bodies), and `sys.modules` holds **no** real `torchcodec.*` submodule — so no
DLL was probed. A full `referat rerun` then diarized on CUDA as before.

Also noted for later, not done: under `pythonw.exe` Python warnings vanish
entirely, so `logging.captureWarnings(True)` in `setup_logging` would be worth
having. It was the toast and not the missing warning that surfaced this one.

## 2026-08-31 — Smart App Control takes the interpreter too, and F5 never started

The VS Code extension would not launch. Two unrelated faults were stacked under
that one symptom, and the noisier one was not the fatal one.

**The workaround recorded earlier today is dead.** That entry ends by saying the
venv's own Python "is what actually runs everything and is still admitted". It is
not, any more. The interpreter uv provisioned is a python-build-standalone build,
`NotSigned` file by file exactly like `uv.exe`, and SAC moved on to the files
inside it:

```
ImportError: DLL load failed while importing _ctypes:
    An Application Control policy has blocked this file.
```

`_ctypes.pyd` and `winsound.pyd` blocked; every other stdlib extension module
still loading; no file modified since 2026-08-27. That is per-file cloud
reputation, and there is nothing stable about the two files it happened to pick.
It is fatal rather than annoying because `ctypes` is imported by `status.py`,
`tray.py` and `power.py`, and `cli.py` imports `status` — so **every** subcommand
died at import, `referat --version` included, and the tray with it, and the
extension, which drives the same interpreter.

The real lesson is that the earlier entry named the wrong culprit. The problem
was never *uv*; it is *unsigned binaries*, and it propagates to everything uv
provisioned. **The fix is provenance, not version:** a PSF-signed Python 3.12
from the Python install manager (`py install 3.12`), whose `python312.dll` and
every `.pyd` are Authenticode-signed individually — verified against the 3.14
already installed, which imports `ctypes` without complaint where the uv build
cannot. Still 3.12, for the same torch / CTranslate2 / pyannote reason as always.
The venv is rebuilt on it with `site-packages` moved aside and moved back rather
than reinstalled: ~5 GB of wheels, the same `cp312-win_amd64` ABI on both sides,
and an unchanged venv path, so the console shims and `.pth` files stay valid.
SETUP.md section 2 is rewritten around this, and now opens by pointing at it.

`"python-envs.alwaysUseUv": true` came out of `.vscode/settings.json`. It is what
produced `Running: uv --version` and then `Error refreshing packages A system
error occurred (spawn UNKNOWN)` on every package refresh — a *spawn* failure
rather than an exit code, because the block lands at `CreateProcess`. Its
justification (uv venvs ship without pip) is now false twice: pip was
bootstrapped this morning, and `py -m venv` seeds it anyway.

**Separately, F5 could never have worked.** Three faults, each fatal on its own,
all of them upstream of the extension host — the bundle itself was fine and
loaded when required by hand.

- `.vscode/tasks.json` had `"path": "referat-vscode"`. VS Code's npm task
  provider joins that onto `package.json` with no separator, looks for
  `referat-vscodepackage.json`, and contributes no task — so `preLaunchTask`
  could not resolve. One character: a trailing slash.
- The same file's problem matcher captured a `message` and no `file`. VS Code
  rejects a message-only pattern, which invalidates the matcher, which means the
  `beginsPattern`/`endsPattern` tracking never arms and F5 hangs waiting for a
  steady state it cannot detect — the exact failure the file's own comment was
  written to prevent. Fixed in `esbuild.mjs` rather than by taking a dependency
  on an external matcher extension: the `onEnd` hook now also prints one compact
  `[watch] error file:line:col: message` line per error, because esbuild's own
  format puts the message and the location on different lines *with a blank line
  between them*, and a multi-line VS Code pattern matches only consecutive lines.
- `~/.vscode/extensions/niklas-elmqvist.referat-vscode-0.1.0` was a directory
  symlink pointing at the live source tree — the very path
  `--extensionDevelopmentPath` targets — listed in `.obsolete` but absent from
  `extensions.json`. VS Code was trying to reap an extension it had never
  registered while the dev-path load presented the same publisher, name and
  version. Removed with `rmdir` on the link, never `Remove-Item -Recurse`, which
  would have followed it into the source.

**And every context menu in the extension was inert.** All five `when` clauses
read `/\bhasTranscript\b/`. In JSON `\b` is a valid escape for **U+0008
backspace**, not a regex word boundary — parsing the manifest and printing the
strings shows character code 8 sitting where the boundary was meant to be. Every
clause compiled to a regex that could never match the space-joined `contextValue`
`tree.ts` builds, so right-clicking a meeting offered nothing. They need `\\b`.
Not a launch blocker, but the extension is useless without it.

CLAUDE.md's Commands section is inverted to match: the venv interpreter is the
documented path, `uv sync` is the historical one, and the note explains that the
signature is what SAC discriminates on.

**Then it was actually done, and measured.** `py install 3.12` fetched a signed
3.12.10; the venv was rebuilt on it with `site-packages` moved aside and moved
back, and the ~5 GB of wheels transferred intact — `torch 2.11.0+cu128` with
`cuda True`, `faster_whisper`, `pyannote.audio`, `sounddevice` and
`pyaudiowpatch` all import, and so do `ctypes` and `winsound`. Every subcommand
works again. The `site-packages` swap needed three attempts: Dropbox held handles
open on the freshly written `pip/_internal` for a few seconds, which is worth a
retry loop rather than a diagnosis.

**Two things changed that were not predicted.**

`.venv\Scripts\pythonw.exe` is no longer the interpreter. CPython's `venv`
copies its *redirector* (`Lib\venv\scripts\nt\pythonw.exe`, 263 kB
against the real 104 kB) which spawns the base interpreter and waits on it, so
the tray is now a ~6 MB stub plus a ~46 MB interpreter. uv's venv was a genuine
copy, and `install_autostart.py`'s docstring argued the shortcut target on
exactly that — "`pythonw.exe` is the venv itself" — so the reasoning was
corrected rather than left to read as still true. Nothing depends on the process
count: `tray.py` writes its own pid, so `referat status` reports the interpreter.
Verified by launching the tray, which came up `idle` at pid 1176.

And **a signed Python does not clear the machine of unsigned binaries** — the
question worth being precise about, since the last two entries have each been
too optimistic. Measured with `Get-AuthenticodeSignature`: the base install is 39
signed files and 8 unsigned, and all 8 are bundled tools nothing imports — pip's
`t64.exe`/`w64.exe` launcher templates, a tcl mingw helper, `tix84.dll`. The venv
`python.exe` and `pythonw.exe` are `Valid`. Unsigned and load-bearing are only
the console-script stubs pip stamps from those templates (`referat.exe`,
`referat-tray.exe`) and the native DLLs in `site-packages` — torch alone is 26 of
38. The stubs cost nothing, because everything here already uses `python.exe -m`
for the unrelated Dropbox reason; the DLLs would cost transcription, which is a
degraded mode this codebase has, not the total failure an unsigned interpreter
caused. So the fix is real but bounded: **keep the import-critical path signed,
let the rest fail soft.** PyPI wheels cannot be signed and there is no per-file
allow, so there is no third option short of turning SAC off. SETUP.md carries
this as a table.

**And then Smart App Control gave `uv` back.** Later the same day, nothing
reinstalled, still `NotSigned`: `uv --version` answers again, and the Python
extension's log shows `uv pip list` succeeding against the new venv. The
reputation for that exact build was restored the way it had been withdrawn.
Recorded because it is the same mechanism in the other direction and it is the
best evidence for the rule this entry started with: a build that runs today is
not one to depend on tomorrow, in either direction, with nothing local to tell
you which way it went. Nothing is reverted — the interpreter stays signed, and
the tray, the CLI and the extension keep routing through it.

**`tsconfig.json` lost its emit settings.** VS Code's bundled TypeScript is
ahead of the pinned 5.9.3 that `npm run typecheck` runs, and TypeScript 6
requires an explicit `rootDir` wherever `outDir` is set — so the editor reported
"The common source directory of 'tsconfig.json' is './src'. The 'rootDir'
setting must be explicitly set" while `tsc --noEmit` passed cleanly on the
command line. Adding `rootDir` would have silenced it. Deleting `outDir` and
`sourceMap` is the better answer: esbuild does every emit here, `out/` has never
existed, and the file was describing an output layout for files this project
does not write. It now sets `noEmit` and nothing about output, so the class of
error cannot come back.

**The tray got a Start menu entry.** `install_autostart.py --start-menu` writes
the same shortcut into `Start Menu\Programs`, so pressing Start and typing
"Referat" launches it, and it can be pinned from there — the answer to "the tray
is not running and I do not want a terminal". The two locations are one `Location`
record each rather than a second script: the target, the read-back verification
and the refusal to overwrite a shortcut Referat did not write are the same
operation in both folders. `--status` now reports both regardless of flags,
because "is the tray set up" is one question and answering half of it is how
somebody concludes the Start menu entry is missing when it is the autostart one
that is. SETUP.md section 9 covers both, and its `uv run` commands — fifteen of
them, all unrunnable here — are now `.venv\Scripts\python.exe -m referat.cli`.

## 2026-08-31 — Smart App Control takes uv, and the venv is repaired without it

No feature work. `uv` stopped running on this machine, and the tool that would
have repaired the damage was the tool that stopped running.

**`uv.exe` is unsigned, and Smart App Control withdrew its benefit of the
doubt.** `uvx.exe` says so in as many words — `An Application Control policy has
blocked this file. (os error 4551)` — while through bash `uv.exe` surfaces as the
much less informative `Permission denied`, exit 126. All three binaries report
`NotSigned`, SAC is still enforcing, and uv 0.12.6 had been working here for a
week. Nothing local changed: SAC admits an unsigned binary only on Microsoft's
cloud reputation for that exact build, and reputation can be withdrawn.

**There is nothing to whitelist.** SAC has no exclusion list, deliberately —
that is the difference between it and SmartScreen — so there is no per-file
allow, and reinstalling uv from scoop, from Astral's installer or from PyPI
fetches the same unsigned bytes and hits the same wall. This is the third time
SAC has shaped this project, after PyAV's FFmpeg at step 5 and the `torchcodec`
scare at step 7, and the answer is the same one both times: work around it rather
than turn it off, because turning it off is one-way, system-wide and the user's
call.

**The workaround is to stop needing uv.** The venv's own Python is what actually
runs everything and is still admitted, so `.venv/Scripts/python.exe -m
referat.cli ...` replaces `uv run referat ...`, and `pythonw.exe -m referat.tray`
is what autostart has been doing since step 9 anyway. For dependencies,
`ensurepip` — which ships inside Python and needs neither network nor uv —
bootstraps pip, and pip replaces `uv sync`. With one thing pip cannot infer:
**the CUDA 12.8 torch pin lives in `[tool.uv.sources]`, which only uv reads**, so
the transcribe extra needs an explicit
`--extra-index-url https://download.pytorch.org/whl/cu128` or pip installs a
torch that does not support this GPU.

**And the venv turned out to be already broken, silently.** The Dropbox problem
recorded at step 9 had recurred: no console scripts at all — `referat.exe`,
`referat-tray.exe`, `pip.exe`, all gone — and `referat-0.1.0.dist-info` reduced to
a *completely empty directory*. The symptom is nasty because it is not obviously
a broken install: `import referat` still works from the repository root, because
the working directory is on `sys.path`, and fails everywhere else. Everything
this session and the last had run from the repo root, so it never showed.

Repairing it needed the husk removed first. `pip install -e .` refuses while an
empty `dist-info` is present — *"Cannot uninstall referat None: no RECORD file
was found"* — the directory claiming the package is installed while holding no
record of what it installed. `rmdir` then `pip install -e . --no-deps` restored
both the import and the console scripts, without touching the three gigabytes of
torch already on disk. Verified from outside the repository root, which is the
case that was broken.

**Written down and not fixed:** the venv's `python.exe` is unsigned too, and is
running on exactly the reputation that was withdrawn from `uv.exe`. If it is ever
withdrawn from the interpreter, the fallback above goes with it and there is no
third layer. Also unfixed, and now more expensive than it was: the checkout still
lives inside the Dropbox tree, so this will happen again — the repair is cheaper
than it was an hour ago, but the cause is untouched.

SETUP.md gains both procedures under section 2 — the SAC fallback and the
eaten-venv repair — because the document tells you to run `uv sync` a dozen times
and would otherwise be wrong on the machine it was written from. `CLAUDE.md`'s
Commands block says the same in three lines.

## 2026-08-31 — The browsing layer: a VS Code extension

Build step 11. Referat gets the surface it has been deferring to `INDEX.md` since
step 10: a `referat-vscode/` extension with a meetings tree, the four things you
do to a meeting, and a panel for naming the speakers identification could not
place. With the tray icon it is now the whole of Referat above the command line,
and it is the last graphical surface this project will grow — a webview is part
of an extension; a localhost server would be a web UI, and there is none.

**The extension reimplements nothing, and that decided its shape.** The tree
needs seven things: the two meeting roots, `format_duration`, `audio_state`,
`index.meeting_title`, `voices.unknown_speakers`, and whether a meeting is still
in staging. Every one exists exactly once in Python and is shared by three or
more callers *precisely so they cannot disagree*, so reading `meta.json` from
TypeScript would have made the extension a seventh reader of it with its own
opinions about all of them. Instead:

- **`referat list --json`** — every meeting with its duration, status, audio
  state, title, unnamed speakers and folder, across both roots, built from the
  same helpers the ASCII table is built from. The table is untouched and stays
  the default.
- **`referat label <id> --json`** — one meeting's unnamed speakers with their
  snippet paths, sample lines and whether an embedding was stored. That last
  flag is the case naming cannot repair, and the panel shows it as unnameable
  rather than offering a field that is guaranteed to be refused.

**`referat label` could not be driven from a subprocess at all**, which was the
one real prerequisite this step had. The prompt reads `input()`, and `--forget`'s
confirmation answers *no* on EOF — so anything without a terminal was told
"Nothing was deleted." It now takes `--speaker SPEAKER_NN --name <name>`,
`--forget <name> --yes`, and the `--json` above. All three are thin wrappers over
`label.apply_name` and `label.forget`, which the module docstring reserved for
"step 11's labeling webview" back at step 7b; the primitives were right and only
the CLI surface was missing.

**The wrappers duplicate none of the rules.** `voices.name_complaint` still
decides what a name may be, so `--name ME` is refused by the same code the prompt
uses. `--speaker` refuses a speaker who already has a name, because renaming is a
different operation from naming and should not be reachable by accident. And
`run_apply` calls `index.write_index` itself: `apply_name` deliberately does not
— it is a primitive the pipeline also calls — so every *entry point* that names
somebody has to, or the dashboard's Unnamed column goes stale the first time the
panel is used. `_label_complaint` rejects the flag combinations that do not mean
anything, in a sentence rather than through argparse's mutually-exclusive groups,
which name the flags without saying what the pair would have meant.

**`--allowedTools "Read,Write"` was wrong, and step 11 is where it showed.**
`CLAUDE.md` and `TODO.md` both specified those two for the `/cleanup` spawn, but
the slash command's own frontmatter declares `Read, Write, Glob` — `Glob` is what
its wrong-meeting-id fallback lists the real ids with. Spawning with the narrower
pair would have broken that fallback on the extension's very first mistyped id.
The command's frontmatter is the authority on what the prompt needs, `Glob`
returns paths rather than contents and cannot reach past the `Read`/`Edit` deny
rule on `.voices/**`, so the docs were corrected to match it. **`Bash` is the
line that actually matters and it has not moved**: having no shell is what makes
a file-tool deny rule sufficient, and `claude.ts` says so where the constant is
defined.

**`claude` is not on `PATH` on this machine**, which step 10 found and step 11
had to answer. The binary ships inside the installed Claude Code VS Code
extension, in a directory whose name carries a version that changes on every
update — 2.1.247 was current four days ago and is now listed in that folder's
`.obsolete` beside 2.1.87, while 2.1.251 is live. The first implementation
globbed that folder and parsed `.obsolete` itself, and it worked; asking VS Code
for the extension and reading `extensionPath` is better, because VS Code already
follows its own extensions across updates and there is then no version to parse
and no `.obsolete` to read. `PATH` stays as the fallback for a machine with an
ordinary install.

**Nothing caches that path.** It is resolved on every spawn, because a resolved
absolute path stored anywhere would still be there a week later pointing at a
directory that has been deleted — and would fail at the moment somebody clicks
*Generate notes*, which is the worst moment to find out. That is the same rot
that happened between the two step 10 sessions.

**It runs the venv's interpreter, and that was decided twice.** The plan for
this step said `uv run --directory <repoRoot> referat`, on step 9's reasoning
that Dropbox deletes `.venv\Scripts\referat.exe` while `uv run` re-materializes
it. Then the first command this session ran came back *An Application Control
policy has blocked this file*, because **Smart App Control had withdrawn its
benefit of the doubt from `uv.exe` earlier the same day** — the entry above this
one. An extension built on `uv run` would have failed on the only machine this
project targets. It spawns `<repoRoot>\.venv\Scripts\python.exe -m referat.cli`
instead, with `cwd` at the repository root: the interpreter is the one link in
the chain that survives both SAC and Dropbox. `cwd` is load-bearing there rather
than tidy — `-m referat.cli` resolves only because the working directory is on
`sys.path`, the same dependency the autostart shortcut has carried since step 9.

**No meetings-folder setting**, despite step 11's own bullet asking for one. The
meetings folder is `[paths].meetings_dir` in the repository's `config.toml`,
which is the file the tray records against; a second place to say where meetings
live is a second thing that can disagree with the recorder, and this project has
been pulled back from exactly that twice already (`voices_dir` derived from
`meetings_dir`, `format_duration` copied into two modules). `referat.repoRoot`
takes its place — point the extension at the repository and it learns both roots
from the config the tray is using.

Smaller decisions worth their line. The tree is **newest first**, matching the
meetings `INDEX.md` rather than `referat list`, for the reason step 10 already
recorded: a listing is read at a prompt, a dashboard is read from the top. The
watcher is **debounced at 300 ms**, because transcription rewrites `meta.json`
several times a meeting and every refresh is a subprocess. *Re-transcribe* opens a
**terminal** rather than spawning behind a progress toast, since `referat rerun`
takes minutes and loads a model and that log is the thing worth watching.
*Generate notes* refuses on a **staged** meeting with an explanation, because
`/cleanup` runs in the meetings folder and a staged meeting is not in it. And the
panel's known-name **chips** are what stop a typo creating a second person —
`difflib`'s "Did you mean Anna?" exists in the terminal because there is no list
to click there, and porting it would have been the second implementation this
whole step is built to avoid.

**One thing found in passing.** The repository's own `.vscode/settings.json`
has never been valid JSON: the interpreter path was written with single
backslashes, making `\.` and `\S` invalid escapes. VS Code's parser is
error-tolerant, so the pin worked and nothing ever complained; it turned up only
because `launch.json` and `tasks.json` went in beside it and got parse-checked
as a set. Forward slashes now, which VS Code accepts on Windows.

**Verified as far as a session can.** The Python half was driven end to end
against a throwaway config pointing at the scratchpad, so nothing real was
touched: a synthetic meeting with two unnamed speakers was listed, dumped,
named, and forgotten again, checking each time that `transcript.md`,
`meta.json`'s `speaker_names`, the per-channel mirror, `voices.json`, the
snippets and the meetings `INDEX.md` all moved — and moved back. The relabeling
left a `SPEAKER_01` *inside another speaker's sentence* alone, which is the
immutability rule holding. Every refusal was exercised: a reserved name, an
unknown meeting, an unknown speaker, a speaker who already has a name, and all
five bad flag combinations. The TypeScript half typechecks and bundles, and the
`claude` resolver was run against the real extensions folder; the parts that need
a person to click — the tree, the watcher, the panel, `/cleanup` — are step 11's
open box in `TODO.md`.

## 2026-08-29 — The meetings folder gets a scaffold, a dashboard, and notes

Build step 10. The meetings folder stops being a pile of timestamped directories:
it now carries its own `CLAUDE.md`, a `/cleanup` slash command that writes
`notes.md` from a transcript, a permission rule keeping that pass out of the
voiceprints, and a generated `INDEX.md` that — because Markdown opens rendered in
that workspace — is the browsing UI until the extension arrives at step 11.

- **`templates/` becomes `templates/meetings/`**, a directory seeded into the
  meetings folder file by file by `paths.seed_tree`, which copies what is missing
  and **never overwrites anything that exists**. `paths.MEETINGS_CLAUDE_TEMPLATE`
  is now `MEETINGS_TEMPLATE_DIR`, and `config.ensure_meetings_dir` walks it.
- **`referat/index.py` and `referat index`.** One row per meeting — date, title,
  duration, status, unnamed speakers, links to `transcript.md` and `notes.md` —
  newest first, unlike `referat list`: a listing is read at a prompt with the
  last line nearest the cursor, a dashboard is read from the top. Regenerated at
  the end of every transcription, after `referat label` and after `--forget`, and
  on demand.
- **The title is the H1 of `notes.md`**, falling back to the meeting id.
  `index.meeting_title`, which step 13's digest blocks will share. Only a `#` on
  the first non-blank line counts — one further down is a mistake in the notes
  rather than a second title, and reading it would put a section heading in the
  dashboard.
- **`format_duration` moved from `cli.py` to `meeting.py`.** Two copies would
  have let `referat list` and `INDEX.md` disagree about the same meeting, and
  importing `cli` from `index` would have been a cycle the moment `cli` imported
  `index`. `meeting.py` is what both already import and costs nothing.

**Staged meetings get a footer line, not rows.** A meeting that kept its audio is
not in the meetings folder at all — it is in `%LOCALAPPDATA%`, waiting for a
`referat rerun` that comes out clean — so there is nothing here to link to
relatively, and naming it absolutely would put this machine's layout into a
synced file. The footer counts them and names the command, the way `referat
list`'s footer already does, so the two cannot disagree about what is waiting.

**`write_index` never raises.** Same rule that shapes `diarize.diarize`: it is
called from the transcription pipeline, and a meeting with a good transcript in
it is not a failed meeting because a table would not render. Driven with
`meetings_dir` pointing at a *file*: it logged, returned `None`, and nothing
escaped.

**The deny rule was wrong on the first try, and Claude Code said so.** The
scaffold's `.claude/settings.json` shipped with `Write(**/.voices/**)` in the
deny list, which reads like defence in depth and is decoration: file permissions
match `Read(path)` and `Edit(path)` only, and `Edit` already covers every
file-writing tool. The very first `claude -p` run printed *"`Write(**/.voices/**)`
is not matched by file permission checks"* before answering. It is gone.

That first run also proved nothing, and it took a second one to notice. The
model refused to read the decoy voiceprint — but the meetings `CLAUDE.md` sitting
beside it tells any session in that folder never to touch `.voices/`, so what was
demonstrated was that the *prose* works. The rule was re-tested in a bare folder
holding nothing but `.claude/settings.json`, a decoy and one ordinary file, with
no instructions anywhere: `File is in a directory that is denied by your
permission settings`, while `plain.txt` beside it read fine. That is the rule
biting, at the tool layer, on its own.

**And the limit of it is worth writing down:** these rules bind the file tools,
not `Bash`. A `cat .voices/voices.json` would go straight past them. It is
survivable only because step 11 spawns `/cleanup` with `--allowedTools
"Read,Write"` and therefore no shell — which makes "never add `Bash` to that
list" a real constraint on code that has not been written yet, now recorded in
`CLAUDE.md` and TODO rather than left to be rediscovered.

**`/cleanup` was run on a real transcript**, `2026-08-28_1152` — 18 minutes, 258
lines. It wrote a `notes.md` whose H1 became the title in `INDEX.md`, left
`transcript.md` and `meta.json` byte-identical, and came out entirely inside the
Markdown subset step 13's translator handles: one H1 on the first line, no `####`,
no nested bullets, no tables, no fenced code. Checked mechanically rather than by
eye.

It also did the thing the prompt exists to make it do. That meeting is the one
whose microphone was never diarized, so every line reads `ME` although it is
plainly a dialogue. The notes say so at the top, attribute turns only by
conversational flow, and **do not name the second participant** — while quietly
correcting an ASR "Jan LeCun" to Yann LeCun and declining to correct a person's
name on the same evidence. That distinction was not in the prompt; it is the
right one, and it is worth keeping an eye on whether it holds.

**What this does not do is tune the prompt.** The gate on step 10 was always
about that, and it has not lifted: one real transcript, of an in-person meeting
with no `SPEAKER_NN` in it anywhere, means every instruction about speakers went
untested. The four gate boxes stay open, and the follow-up is recorded — read the
prompt again against the first call that has diarized speakers in it.

**The live meetings folder's `CLAUDE.md` was reconciled by hand**, as the
seed-once rule intends. It was a pure unmodified seed — every line it had that
the new template does not was a line this session deliberately rewrote — so
reconciling was a copy. The scaffold's other three files were seeded fresh
alongside it, and the seeder was verified against the live folder by hashing
`CLAUDE.md` before and after: unchanged, which is exactly the failure mode the
never-overwrite rule exists to prevent.

**Found while verifying, and it belongs to step 11:** there is no `claude` on
`PATH` on this machine. The binary ships inside the VS Code extension, at
`resources/native-binary/claude.exe`, and `where claude` finds nothing — so step
11's plan to spawn `claude` off `PATH` by default would fail here on first use.
`/cleanup` was driven by absolute path instead.

*(Two days later, committing this: that absolute path is already dead. The
extension moved 2.1.247 → 2.1.251 and `~/.vscode/extensions/.obsolete` now lists
the 2.1.247 directory for deletion. The finding was weaker than the truth — this
is version rot on a scale of days, not a one-machine quirk, and it settles step
11's design: resolve the binary through
`vscode.extensions.getExtension("Anthropic.claude-code")` at spawn time and store
nothing. Written up under step 11 in TODO, and in SETUP.md section 11a for the
shell case, where `npm i -g @anthropic-ai/claude-code` needs no PATH change
because `%APPDATA%\npm` is already on it.)*

Verified by driving it, as at every step so far: the scaffold seeded into a
throwaway folder and then re-seeded with one file edited and one deleted (the
edit survived byte for byte, only the deletion came back); `INDEX.md` rendered
against synthetic meetings covering all three title cases — an H1 first line, a
`notes.md` whose first heading is further down, and no notes at all — with every
relative link resolving to a file that exists; the counts cross-checked against
`referat list`; the empty-folder case; and finally a real `referat rerun` of the
staged `2026-08-28_1104`, which kept its audio, stayed in staging, and still
rewrote `INDEX.md` at the second the pipeline finished — which is the
unconditional call doing its job.

## 2026-08-28 — The microphone is the room, not the owner

A real meeting came back with two people in it and **258 lines all labeled
`ME`**. Nothing had failed: the microphone channel was never diarized at all.
`ME` was not a decision, it was the mic channel's fixed label, and diarization
only ever ran on the loopback — which for a meeting held in person is eighteen
minutes of silence.

**The premise was wrong, not the tuning.** `mic.wav` was taken as one speaker by
definition, the owner of the laptop and nobody else. That is true of a Zoom call
and false of the meetings Referat is mostly used for, where everybody at the
table arrives through one microphone. The first line of `CLAUDE.md` has always
said this records meetings "in person and over Zoom/Teams"; only the second half
was ever implemented.

- **Both channels are diarized now.** `transcribe_channels` no longer passes
  `diarize_channel=False` for the mic.
- **Speaker numbering runs across the meeting, not the channel.**
  `diarize.assign` takes a `start`, and `transcribe_channels` threads the running
  count. Two channels each restarting at `SPEAKER_01` would put two different
  people behind one label in the same transcript.
- **`ME` now means the owner, on whichever channel their voiceprint was
  recognised** — matched exactly as everybody else is, then rendered `ME` because
  reading `Niklas:` against one's own lines is stranger than reading `ME:`. It is
  presentation only: `meta.json` keeps the real name in `speaker_names`, which is
  what `referat label --forget` needs to revert the owner like anybody else. A
  mic channel that could not be diarized still falls back to `ME`.
- **The owner bootstrap is measured instead of inferred.** It used to require the
  loopback channel to hold voice — sound reasoning about the wrong condition,
  since it can only fire on remote calls, so for the meetings this is actually
  used for the owner voiceprint would never have been created at all. The rule is
  now that the microphone clustered into **exactly one speaker**: true of a solo
  recording and a remote call, false of a room with two people in it. It also
  settles the Jabra Speak 510 worry that had been open since step 7b — far-end
  audio echoing off a desk speakerphone clusters as a second voice and refuses
  the bootstrap, rather than quietly poisoning the owner's voiceprint.
- The bootstrap files the cluster's own centroid, which diarization already
  computed, so nothing is re-embedded. `diarize.embed` had no other caller and
  was deleted, along with two constants that served only the old path.

**And the verification found a second bug, in code that predates this change.**
Naming one synthetic cluster as the owner and re-running turned *both* speakers
into `ME`. The numbers said why: the other voice scored **0.7404** against a
0.70 threshold, with `runner_up: -1.0`. **With one name in the database there is
no runner-up, so the margin — the whole safeguard against a confusable match —
was trivially satisfied and the threshold decided alone.** That is precisely the
state the database is in just after `owner_name` is first set, and precisely when
a false accept is worst, because it renders somebody else's words as `ME`. With
no runner-up the margin is now required of the score itself. The same meeting
then came back correctly: the owner's cluster `ME`, the other still `SPEAKER_02`
and waiting for `referat label`.

The thresholds themselves are still guesses, and 0.7404 between two different
Windows TTS voices is a reminder of how little headroom 0.70 has.

Verified by driving it, as always, and this time the synthetic meeting is the
exact shape of the one that failed: two TTS voices on `mic.wav`, silence on
`system.wav`. Every line correctly attributed — Hazel to `SPEAKER_01`, Zira to
`SPEAKER_02`, three snippets cut for each so `referat label` has something to
play. A hybrid meeting with one voice in the room and one on the call numbered
them `SPEAKER_01` and `SPEAKER_02` across the two channels with no collision.
`assign`'s offset, the owner-to-`ME` mapping and the new margin rule each have
their own direct checks, including the 0.7404 case that started it. The real
known-voices database was never touched: the owner test ran against a throwaway
`REFERAT_CONFIG` with its own `voices_dir`, because Hazel and Zira do not belong
in a database of real people.

**What this does not fix:** the meeting that prompted it. `2026-08-28_1152`
transcribed cleanly, so the quality gate had already released its WAVs, and no
snippets were cut because diarization never ran on that channel. There is nothing
left to re-run it from. It keeps its 258 lines of `ME`.

## 2026-08-28 — Confirmed on the machine: buttons, tray, transcription

Not a code change. Step 2's last open box is closed: **the programmed USB button
drives the autostarted tray**, with audio — press to record, press to stop, and
the meeting transcribes. That box had been open since step 2 because synthetic
keystrokes cannot be injected from an automated session, so it could only ever be
closed by a person pressing a real button.

Smart App Control was also seen doing its thing in production, and doing it
correctly: one INFO line per transcription saying PyAV is blocked and the WAVs
are being decoded directly, followed immediately by large-v3 loading on CUDA. The
blocked submodule name varies between runs — `bitstream`, `frame` — being
whichever one PyAV reaches first. The workaround from step 5 is holding.

**Still untested: a meeting with anybody else in it.** Every recording so far has
been solo, so `system.wav` has been silent every time (`silent=True` on every run
in the log) and the diarization pipeline has therefore never run outside the
synthetic two-voice tests. Speaker identification, the `0.70` threshold and the
`0.15` margin are all still guesses.

## 2026-08-28 — Autostart, and the guide for a machine that has none of this

Build step 9, which finishes the recorder. `scripts/install_autostart.py` puts a
shortcut in the Startup folder, and `SETUP.md` writes down the dozen sharp edges
that until now existed only as TODO boxes and CHANGELOG prose.

**The shortcut runs `pythonw.exe -m referat.tray`, not `referat-tray.exe`.** The
gui-script looked like the obvious target and is the wrong one: it is a uv
trampoline, which `CreateProcess`es `pythonw.exe` and then *stays resident as its
parent*, so autostarting through it would leave a stub process in the tree for
the life of the tray. `pythonw.exe` is the venv itself, where the trampoline is
an artifact `uv sync` regenerates; `tray.py` has named its logger explicitly
since step 2 precisely so the two paths log identically; and the `.lnk`
properties dialog then says what it runs. The venv is found with
`Path(sys.executable).resolve().parent` rather than by joining `.venv` onto the
repo root — `sys.executable` already answers that question, and the two answers
would disagree the first time somebody used a differently-named environment. The
repo root is cross-checked against `paths.REPO_ROOT` instead, so a script run
against a *different* checkout's package refuses rather than installing a
shortcut nobody meant.

**The `.lnk` is written by PowerShell driving `WScript.Shell.CreateShortcut`;
the Startup folder is `SHGetKnownFolderPath` through ctypes.** Those look
inconsistent and are not. The rule is about shape, not about ctypes: ctypes is
right for a single flat function with scalar arguments — `SetThreadExecutionState`
in `power.py`, `CreateMutexW` in `tray.py`, `OpenProcess` in `status.py`, and now
`SHGetKnownFolderPath` — and wrong for navigating a COM interface, where
`IShellLinkW` plus `IPersistFile` is a hundred lines of hand-declared GUID
structs and vtable-index arithmetic in which a wrong index is memory corruption
rather than an exception. pywin32 was the third option and would have put ten
megabytes of Win32 bindings into the *base* install — the one deliberately kept
to tray and audio — to write one two-kilobyte file, once.
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` stays only as the
fallback for a bad HRESULT: it is an assumption, since folder redirection moves
the Startup folder and `%APPDATA%` can be redirected on its own. The two agree
on this machine, which was checked rather than assumed.

**It refuses to repoint a shortcut aimed somewhere else**, and refuses to delete
one it did not write, in both cases printing found-against-expected and asking
for `--force`. Same rule as "every scaffold file is seeded once and never
overwritten": silently clobbering something the user may have changed on purpose
is the failure this project keeps declining to commit. Installing when the
shortcut is already current rewrites nothing at all — the mtime is the only
evidence of when this was last run.

**And it reads the `.lnk` back and compares all four fields before saying it
worked.** `Save()` reports nothing useful, and the shell normalises `TargetPath`
on the way in, so reading back is the only way to know the file on disk says what
was meant. One trap is written into the code as a comment because it is a
correctness requirement rather than an optimisation: **`CreateShortcut` on a path
that does not exist returns a blank object rather than failing**, so without the
existence check a *missing* shortcut would read as an installed-but-wrong one and
`--status` would lie in the more alarming direction.

**A shortcut cannot set an environment variable**, which makes `REFERAT_CONFIG`
the quiet failure of this step. A value set in the installing shell is not
inherited by anything Explorer starts at sign-in, so the tray would come up on
repo-root `config.toml` while the person who set it believed otherwise — the same
family of silent-wrong-thing as a `mic_device` substring that matches nothing.
The installer reads `HKCU\Environment` as well as its own environment, prints the
config file the autostarted tray will actually load, and names `setx` as the fix.
Rejected: wrapping the target in `cmd /c set ... && start ...`, which flashes a
console window — the one thing the whole `pythonw.exe` arrangement exists to
avoid.

**No icon on the shortcut, deliberately.** The tray icon is drawn at runtime by
PIL, there is no `.ico` in the repository, and a Startup-folder shortcut appears
in no Start menu and no taskbar — adding a binary artifact to decorate a file
nobody looks at is exactly the configurability the conventions forbid. It
inherits `pythonw.exe`'s icon.

**`SETUP.md` closes eleven boxes** scattered across steps 7b, 8, 9 and "Surfaced
later", and settles three things that had been left open as decisions rather than
tasks:

- **The lid.** `SetThreadExecutionState` suppresses the *idle* timer and nothing
  else, so closing the lid still suspends the machine mid-meeting. SETUP.md
  section 10 gives both the Settings path and the `powercfg` command and says
  plainly that Referat will not rewrite a system-wide power setting on your
  behalf. The distinction the old note was reaching for is that a document
  telling a person is not the program doing it silently.
- **Smart App Control.** Documented as the reason the PyAV and `torchcodec`
  workarounds exist, stated to be a one-way change Windows cannot undo without a
  reinstall, and not recommended.
- **The Hugging Face symlink warning.** One sentence, with Developer Mode as the
  fix. It appears on the very first transcription, looks alarming, and had
  already been re-diagnosed once.

The section that matters most is the token one, because its failure mode is
invisible: an unaccepted gated repo is a `403`, a `403` costs speaker labels and
nothing else, and the transcript arrives on time with every remote line reading
`REMOTE:`. SETUP.md says where to look — `meta.json` →
`transcription.channels.system.diarization.reason` — because nothing in the
transcript will ever tell you.

`README.md`'s Status section still claimed "Build step 5 of 9" and mentioned
neither diarization, nor speaker names, nor the CLI; it now describes what
actually exists and opens Quick start with a link to SETUP.md. Leaving the
repository's front door three steps stale next to a brand-new install guide would
have made it the only file in the repo that was wrong. `.gitignore` gains
`*.lnk`, since both SETUP.md and `--status` invite you to go and look at the
shortcut.

**Verified by driving it, as at every step so far.** `--status` before install
reported not-installed at exit 1; the install wrote the `.lnk` and its own
read-back agreed; **an independent PowerShell one-liner** — not the script's own
reader — confirmed all four fields exactly, which is the check that actually
proves anything. `pythonw.exe -c "import referat.tray"` imports, a genuinely
different interpreter binary from `python.exe`. Installing again printed
"already installed" and left the mtime **unchanged**. A decoy `Referat.lnk`
pointing at `notepad.exe` was refused by both `install` and `--uninstall` at exit
1 and replaced by `--force`; `--uninstall` twice in a row was idempotent.
`startup_dir()` and the `%APPDATA%` join were printed side by side and agree. The
shell-only `REFERAT_CONFIG` warning fired and named the right file, without
anything being persisted.

**The launch chain was proved once, end to end**: the shortcut's exact command
line was started, registered both hotkeys, and wrote a `status.json` that
`referat status` read back live at exit 0 with the right pid. Killing that
process then exercised `referat status`'s **stale** branch against a genuinely
dead tray — which TODO had recorded as only ever tested against a faked status
file pointing at a stand-in child process. Half of that box is now closed; the
busy-tray guard in `rerun` is not.

Every read-only command SETUP.md contains was run and its output compared against
what the document claims, and every factual claim was cross-checked against the
code rather than against memory — the hotkey defaults against `HotkeysConfig`,
the token path against `PathsConfig`, the meta.json key path against
`_transcription_meta` and `Diarization.to_json`, the staging default against
`paths.default_staging_dir`.

**And verification turned up something that has nothing to do with step 9 and
matters more than it.** `scripts/install_autostart.py` began the session
importing `referat` cleanly and ended it unable to: `.venv\Scripts\referat.exe`
and `referat-tray.exe` had disappeared, and `site-packages\referat-0.1.0.dist-info`
was an empty directory. This repository lives inside the Dropbox tree, `.venv` is
only *gitignored* — which does nothing to stop a sync client — and Dropbox is
quietly removing files out of it. `uv sync` repairs it and nothing prevents a
recurrence; the choice between a Dropbox ignore flag on `.venv` and moving the
checkout out of the synced tree is recorded in TODO rather than made here. It is
the same class of mistake the meetings folder already hit, with the same answer.

The script no longer depends on the outcome. Running a script puts the *script's*
directory on `sys.path` and not the working directory, so
`scripts/install_autostart.py` could never see the package beside it and was
relying on the editable install; it now puts the repository root on the path
itself, from `__file__`, and works from any working directory. Worth noting that
this makes the shortcut's `WorkingDirectory` load-bearing for the moment rather
than merely tidy: `-m referat.tray` currently finds the package only because
`-m` puts the working directory on `sys.path`.

**Not verified, and recorded rather than claimed:** that the shortcut fires *at
sign-in*. Nothing in an automated session can produce a logon, so the command
line is proved and the trigger is not — it needs one sign-out. Nor can SETUP.md
prove that nothing is *missing*: it was written from this laptop's history, and
the only test of that is a machine which has none of this.

## 2026-08-28 — `referat devices`, and the mic pinned to the Jabra

A Jabra Speak 510 was plugged in, which raised a question the CLI could not
answer: **what is this device called?** `[audio].mic_device` and
`[audio].loopback_device` are substring matches, and a substring that matches
nothing falls back to the default *silently* — a warning in a log nobody reads
until after the meeting. The only way to learn a device's name was to guess, or
to record something and read the log afterwards.

- `referat devices` lists every input device and every WASAPI loopback source,
  marking the one the config resolves to `<- config` and the one Windows would
  pick `(default)`. It calls `recorder.resolve_mic_device` and
  `recorder.resolve_loopback_device` rather than reimplementing their rules: a
  listing that disagreed with what actually gets recorded would be worse than no
  listing at all. When a configured substring matches nothing it says so above
  the table, which is the whole reason the command exists.
- `render_table` takes its right-aligned columns as an argument instead of
  closing over the module constant aimed at `list`'s five columns.
- `config.toml` pins `mic_device = "Jabra"`. Not only to survive Windows
  changing its mind about the default: PortAudio exposes the same microphone
  four times, and the default resolves to the **MME** copy at 44100 Hz, while
  naming it takes the WASAPI copy — natively 16 kHz mono, exactly
  `mic_samplerate`, so nothing resamples. `loopback_device` stays empty on
  purpose, so system audio follows whatever is actually playing.

The loopback marker is `(default)` rather than `<- config` when the configured
substring missed. `resolve_loopback_device` returns the default output's
loopback either way and has no way to say "I fell back", so the caller asks
`_matches` afterwards; without that the fallback row was labelled `<- config`
next to a config value it did not match.

**Not built, and deliberately:** a mic-only capture mode. The recorder still
opens both channels always, and one is absent only when its device fails to open.

## 2026-08-28 — Recording moves out of the synced folder

Follow-on from the move into Dropbox, and the more important half of it. **The
WAVs are never written into the meetings folder at all now.** A meeting is
created in `[paths].staging_dir` (`%LOCALAPPDATA%\Referat\recording` by default),
recorded there, transcribed there, and moved into `meetings_dir` only once
`release_audio_if_clean` has deleted the audio.

- `[paths].staging_dir`, and `Config.staging_dir()` / `Config.meeting_roots()`
  beside `Config.voices_dir()`.
- `paths.move_meeting_dir` — same volume in the normal case, so a rename, so
  atomic: the folder is wholly in one place or wholly in the other, never
  half-copied into a folder a sync client is watching.
- `transcribe.promote_meeting` runs on a clean release. It never raises: a
  meeting that cannot be moved is still a finished meeting with a transcript in
  it, and losing the folder to a half-handled error is far worse than leaving it
  in the wrong place.
- `Meeting.create` takes the `Config` and creates in staging.
  `meeting.load_meetings` takes it too and spans both roots;
  `paths.find_meeting_dir` takes a sequence of roots. `label`, `rerun` and `list`
  all pass `meeting_roots()`, so a staged meeting stays visible to exactly the
  commands that would resolve it.
- `referat list` says `N still local with audio kept - run: referat rerun`.

**Deleting a file in a synced folder does not delete it.** This was the argument
that decided it. Dropbox keeps deleted files and prior versions on its own servers
for 30 days, 180 on some plans — so the audio would have been uploaded, then
"deleted", then retained off-machine for weeks while `meta.json` recorded
`audio_released: true`. Recordings of people who never asked to be recorded,
surviving on somebody else's infrastructure after Referat reported them gone. The
wasted bandwidth and the hazard of syncing a file that is still being appended to
are both real, and both smaller than that.

**Ids are reserved in both roots at creation**, so the later move never renames a
meeting. `new_meeting_dir` takes an `avoid` sequence and suffixes against every
root. Without it, a meeting recorded in the same minute as one already promoted
would get its id from staging, collide on the way in, and be renamed *after* that
id was written into `meta.json`, printed by `referat label <id>` and — at step 13
— used as its digest anchor. `promote_meeting` still syncs the id if it ever
happens, and logs a warning, but it should be unreachable.

**A meeting that keeps its audio stays in staging**, which is the case the quality
gate exists for. It is listed, labelable and rerunnable where it sits, and a rerun
that comes out clean promotes it. That keeps "no WAV ever reaches the meetings
folder" an invariant rather than a usual case — the alternative, moving it anyway,
would have parked audio in Dropbox in precisely the situation where the recording
is most worth not uploading. Noted under "Surfaced later": nothing prunes staging,
so a meeting that never transcribes cleanly sits there indefinitely.

Verified on temporary folders: creation lands in staging, a staged meeting is
listed and resolvable by id, promotion moves it and is idempotent, a kept-audio
meeting stays put and stays visible, and a same-minute collision gets its `_2` at
creation and keeps that id through the move.

## 2026-08-28 — The meetings folder moves into Dropbox, the voiceprints do not

`[paths].meetings_dir` is now `C:\Users\nikla\Dropbox\Research\Meetings`, so
transcripts and notes sync. That immediately broke the assumption `.voices/` had
been resting on, so the database got a way out of the synced tree.

- **`[paths].voices_dir`**, a new optional key. Unset, it derives
  `<meetings_dir>/.voices` exactly as before; set, it wins. `~/.referat/voices`
  on this machine.
- **`Config.voices_dir()` resolves it, and is now the only way to ask where the
  voiceprints are.** `VoicesDB.load` takes the whole `Config` rather than a
  meetings folder, which is what stops a caller re-deriving the old path by
  accident — the failure that would silently put the database back in Dropbox.
- `paths.voices_dir` grew an `override` argument. `paths.voices_path` is deleted
  rather than left standing as a second way to answer the same question without
  one; nothing used it.
- `ensure_voices_dir` now takes the folder itself. It used to accept either that
  or the meetings folder and tell them apart by checking for the name `.voices`,
  which stops working the moment the folder is allowed to be called `voices`.
- `_coerce` accepts `Path | None`, so an optional path key coerces like any other.
  With `from __future__ import annotations` the declared type arrives as the
  string `"Path | None"`, which is what it now matches.

**A config key, not a Dropbox ignore flag.** Dropbox can exclude a subfolder by
setting `com.dropbox.ignored` as an alternate data stream, and that was the
smaller change. It was refused because it has to be re-applied by hand every time
the folder is recreated, it lives nowhere in this repository, and when it is
missing nothing goes wrong *visibly* — the voiceprints just quietly start
uploading. A config key is checkable: `referat config` prints the resolved path,
and it is either inside the synced tree or it is not.

Nothing had to be migrated. No `.voices/` existed yet, because `owner_name` is
still empty and nobody has been labeled, so the folder was created fresh at the
new location with its `DO-NOT-SYNC.md`. The eight meetings in `~/Meetings` were
left alone: seven are the synthetic test recordings already listed for deletion,
and `2026-08-27_1906`, the one real call, is still there to move by hand.

**Recorded under "Surfaced later": Dropbox now syncs the meetings folder while a
meeting is recording.** `mic.wav` and `system.wav` are appended continuously with
incremental header rewrites, ~460 MB an hour across both, and only deleted once
the quality gate passes — so Dropbox is repeatedly uploading a file that is still
growing and holding handles on files the recorder is writing. Nothing has gone
wrong yet, but "recording robustness beats everything else" makes it worth
watching on the first long meeting. The fallback is to record to a local staging
folder and move the meeting in once transcription is done.

## 2026-08-28 — Per-project digests planned

Planning only. **Nothing was built**: this session touched `TODO.md`,
`CLAUDE.md`, `INDEX.md` and this file, and no source, template or extension
code. The build order grows from twelve steps to thirteen. Nothing was
renumbered — step 13 is appended, the way 7b was lettered in.

The feature had never actually been written down anywhere in this repository.
The only trace of the idea on disk was `[[Wikilinks]]` *for people and projects*
in the `/cleanup` prompt spec, so this is the whole design rather than a
revision to something already recorded.

- **A project is a thread of work spanning many meetings**, linked to one Google
  Doc — its digest — into which every meeting assigned to it is written as a
  dated block. The doc is the shareable artifact; the meetings folder stays
  local. `<meetings_dir>/projects.toml` holds the project list and the link
  state.
- **Assignment is manual, with a remembered default.** `referat project assign
  <meeting-id> [<project>]`, defaulting to whichever project was assigned last.
  Nothing is inferred from the transcript, nothing is assigned at the end of the
  pipeline, and re-routing is the same command run again.
- **Linking is create-or-select.** *Create new doc* makes `<Project> Meeting
  Digest` and uses its default tab. *Select existing doc* searches Drive with
  `files.list` filtered to Google Docs, then requires a tab named `Meetings`.
- **Three new modules**, all step 13: `referat/projects.py` (the projects file
  and assignment), `referat/digest.py` (the Markdown translator, the anchor scan
  and the diff — pure, no Google import), and `referat/gdocs.py` (the client,
  behind a new `digest` extra). `project list` and `project assign` need no
  extra at all, which keeps step 8's rule that the CLI stays cheap.
- **`meta.json` gains `project` and `digest`** — the latter recording
  `gdoc_id`, `tab_id`, `notes_sha256` and `written_at` per write. The folder
  contract table is unchanged: no new file appears in a meeting folder.
- **The extension gains a Projects section**, written up under step 11: a node
  per project, *Link* / *Unlink* / *Open* / *Sync now*, a per-meeting *Assign to
  project* QuickPick, and an **Unassigned** node that is the queue. All of it
  shells out to `referat project ...`, the same rule the labeling webview
  already has.

**There is no web UI in this project, and that is now a convention.** The tray
icon and the VS Code extension are the only graphical surfaces; no Flask, no
FastAPI, no localhost dashboard. Written into `CLAUDE.md` beside the offline and
no-API-key rules, because a projects-and-digests feature is exactly the point at
which a later session reaches for a web front end without anyone deciding to.

**Reconciliation, not append.** Each block starts with an anchor paragraph
reading `[referat:2026-08-27_1400]`; `referat project sync` reads the tab, diffs
the anchors against the meetings assigned to the project, inserts what is
missing *in date order*, and re-renders any block whose `notes.md` has changed —
judged by `sha256` against the hash recorded when the block was written. A plain
append cannot survive `/cleanup` being re-run, and it cannot backfill, so
linking an existing doc now fills it in automatically because linking ends by
syncing.

**A visible text anchor rather than a Docs named range.** Named ranges are the
API's own mechanism and are the fragile choice: invisible to a person editing
the doc, destroyed along with their content, and not carried by a copy of the
document. A text anchor survives everything a person can do except deleting that
one line, and deleting it merely makes the reconciler re-append the block —
recoverable, where a silently-lost named range is not.

**Orphans are reported, never deleted.** A block whose meeting is no longer
assigned stays put; the doc may be shared and somebody may have written around
it. `sync --prune` is the explicit opt-in. This is the same instinct as keeping
the audio when a channel transcribes badly.

**Every write is located by `tab_id`.** `documents.get` always passes
`includeTabsContent=True`, and every `batchUpdate` request carries `tabId`.
A request without one silently targets the *first* tab, which would write a
meeting into an unrelated part of somebody's document with no error to notice —
the kind of failure that is found weeks later by a reader, not by the code.
Tabs also cannot be created through the API at all, which is why selecting an
existing doc hands off to the browser rather than making the tab itself.

**One `insertText`, then N style requests.** The translator builds the block's
plain text with a running offset, recording style spans as it goes, then emits a
single insert followed by the styling — style requests move no indices, so the
spans stay valid. Two traps recorded with it: Docs indices are **UTF-16 code
units**, so one emoji in a note shifts everything after it, and
`createParagraphBullets` converts *existing* paragraphs, so the inserted text
must not contain the `- ` characters itself. No raw Markdown may appear in the
doc, asserted by scanning the rendered plain text.

**Blocks open with a Heading 3 `YYYY-MM-DD — <title>` line**, the title taken
from the H1 of `notes.md` exactly as `referat index` does it. Also noted, in
`CLAUDE.md`: the Docs API cannot insert an @-date smart chip — there is no
request type for one. Keeping the date as a fixed-width prefix in leading
position is what would make swapping it for a chip a one-request change to that
line if the API ever gains one.

Two things left open under "Surfaced later". **How Referat authenticates to
Google is undecided**, deliberately: a desktop OAuth client follows the existing
`hf_token_file` precedent and lets Drive search see the user's own Drive, which
the picker needs; a service account avoids the browser flow but only sees
documents shared with it, which breaks that picker. And **a shared doc is a much
wider blast radius than a shared folder** — the digest is the first thing
Referat has ever sent anywhere, so the `/cleanup` prompt wants re-reading with
that in mind before the first doc is shared. The transcript, the audio and
`.voices/` never leave the machine at all; that is now its own convention.

## 2026-08-28 — The CLI: what is there, what is running, and running it again

Build step 8. `referat list`, `referat status` and `referat rerun <id>` are real;
`label` has been since 7b. Nothing new happens during a meeting — this is the
half of Referat that answers questions about meetings that already happened.

- `referat list`: one row per meeting, oldest first, with duration, status,
  whether the WAVs are still on disk, and how many speakers are still numbers.
  That last column is the queue for `referat label`, and the footer names the
  command only when there is a backlog. Until now the tray balloon at the end of
  a pipeline was the only thing that had ever mentioned it, and it is gone the
  moment it is dismissed.
- `referat status`: the state, the meeting, how long it has been going, the job
  count and the pid. `status.json` has been written on every transition since
  step 2 and nothing had ever read it. Exit 0 when a tray is running, 1 when
  none is, so it is usable from a script.
- `referat rerun <id>`: transcribe a meeting again from the audio it kept — the
  reason the pipeline keeps the WAVs of anything that looks unreliable, and the
  way to regenerate a transcript after the model or the diarization checkpoint
  in `config.toml` changes. New `rerun.py`, because it is the one command that
  needs the `transcribe` extra and importing it inside the function keeps three
  gigabytes of torch out of `referat list`.

**A rerun renumbers, so it clears before it runs.** Diarization clusters from
scratch, so this run's `SPEAKER_02` is not last run's: `speaker_names` and the
`speakers/` snippets are dropped before the pipeline starts. No name is lost by
that — every one of them is in the known-voices database, and identification
looks each cluster up there again on the way through. Carrying the old mapping
over is the thing that *would* lose them, by putting last week's name on this
week's numbering.

**`os.kill(pid, 0)` would have killed the tray.** The POSIX liveness probe does
not exist on Windows: CPython implements `os.kill` there with `TerminateProcess`
for every signal except `CTRL_C_EVENT` and `CTRL_BREAK_EVENT`. Asking whether the
tray is alive that way would have terminated it mid-meeting with both audio
streams open. `status.is_running` opens the process for
`PROCESS_QUERY_LIMITED_INFORMATION` and reads its exit code instead, the same
`ctypes`/`wintypes` shape as the single-instance mutex in `tray.py`.

**`rerun` refuses while a live tray is transcribing.** `transcribe._RUN_LOCK`
serializes jobs inside one process and cannot see across one, and two large-v3
models do not fit in 12 GB of VRAM. Recording is deliberately *not* in the way —
capture costs no GPU, and refusing during it would make the command unusable
through most of a working day. `--force` overrides, because the guard is about
the GPU rather than about correctness.

Verified against the eight meetings in `~/Meetings`: the listing, both refusals
(no such meeting, no audio left), the stale-status branch, and a faked live tray
for the guard and for `--force`. Then the real one — a `rerun` of
`2026-08-27_1906`, the 8-minute call from step 6. It had been recorded before the
loopback channel existed, so its `system.wav` had never been transcribed and
`release_audio_if_clean` had been sitting blocked on it ever since; the rerun
transcribed both channels, found the loopback silent as an in-person meeting
should be, and reclaimed the 65 MB.

## 2026-08-27 — Speaker identification: the numbers get names

Build step 7b, all of it. `SPEAKER_01` becomes `Anna` the second time she is in a
meeting, and the first time somebody says who she is. There is still no
enrollment step: diarization already computes one embedding per speaker cluster
in order to build the clustering, so every meeting is its own enrollment session.

- `voices.py`: the known-voices database at `<meetings_dir>/.voices/voices.json`
  — a name mapped to a *list* of embeddings, each stamped with the meeting,
  cluster and date it came from so a bad one can be traced back and pulled out.
  A list rather than an average, because the same person on a different headset
  lands somewhere else in the space and averaging would blur the one thing being
  matched on. Plus the cosine matching, the snippet cutting, and the owner
  bootstrap.
- `label.py` and `referat label`: play an unnamed speaker's snippets, ask who it
  was, and write the answer to the database, `meta.json` and the transcript's
  labels. A name, a number off the list of known names, `r` to replay, `s` to
  skip, `q` to stop; `difflib` catches typos and asks rather than silently
  creating a second person. `--forget <name>` deletes somebody outright.
- `diarize.py` harvests `DiarizeOutput.speaker_embeddings` and `assign` now
  returns the renaming map, so the vectors can be permuted with the labels.
- `[speakers]` config: `identify`, `owner_name`, `match_threshold`,
  `match_margin`, `snippets_per_speaker`, `snippet_seconds`.
- The tray says `Transcribed 2026-08-27_1400 (2 unknown voices — run: referat
  label 2026-08-27_1400)`, which is the only thing that ever mentions the
  command. Nothing extra when everybody was recognised.

**A match needs two things, not one.** The winner must clear `match_threshold`
*and* beat the runner-up **name** by `match_margin`. A voice sitting between two
known people is refused even when it comfortably clears the threshold, because
the failure mode being guarded against is not "no name" — it is *the wrong name*,
written into a transcript, where it looks exactly as authoritative as a right
one. Both numbers start strict and are still guesses.

The refusals are written to `meta.json` anyway, under each speaker's `match`, with
the score and the runner-up. That is deliberate: the near-misses are the only
material there is for calibrating the two numbers, and throwing them away would
mean tuning blind.

**Three traps in the embeddings, all confirmed against the installed pyannote
4.0.7 rather than assumed.** The rows are ordered by `speaker_diarization.labels()`
while `diarize.py` reads its turns out of `exclusive_speaker_diarization` — safe,
but only because pyannote applies the same rename mapping to both, so the order
has to come from the first and nowhere else. The array can be `None`, and it is
**zero-padded** when the clustering produced fewer centroids than the annotation
has labels; a zero-norm row is filler, not a voiceprint, and filing one under a
name would make every later meeting match against noise. And then `assign`
renumbers everything by first appearance, so the vectors have to be permuted with
the labels. Any one of the three, mishandled, files a voice under somebody else.

Also confirmed and worth writing down: the checkpoint clusters with `VBxClustering`,
whose centroids are plain means of the **raw** embeddings, compared internally
with `metric="cosine"`. So cosine against a stored centroid is the same operation
pyannote performs on itself — no PLDA transform in the way.

**Snippets are cut during the pipeline**, not on demand, because the WAVs they
come from are deleted as soon as the meeting transcribes cleanly and offsets
would point at nothing by the time anyone ran `referat label`. They are cut only
for speakers nobody could name, and deleted the moment they get a name. Measured:
about 560 KB per unknown speaker.

**The owner bootstrap declines more often than it fires.** `mic.wav` is one
speaker by definition, so it yields a voiceprint with no diarization at all —
except in a meeting held in person, where the microphone hears the whole room and
the embedding is a blend of everybody. The guard is that the loopback channel had
voice in it, which makes a Zoom or Teams call the unambiguous case. Verified both
ways, including a meeting whose loopback is silence and one Windows chime.

`owner_name` turned out not to need the "seed it the first time I label myself"
mechanism the plan described. There is no `SPEAKER_NN` for the mic channel to
label in the first place, and `config.py` never writes the TOML back, so it is
simply a key you set by hand. Left empty by default: it is a decision about your
own biometric data.

**`--forget` had a hole in it, found by reading the `meta.json` it left behind.**
Reverting the labels and deleting the embeddings was not enough — the per-channel
`speakers` block still carried `"name": "Anna"` and a `match` block naming her, so
a deletion that was supposed to leave nothing behind left her name in two more
places. It now clears both. The test greps the entire meetings folder for the name
afterwards and requires zero hits.

A smaller hole with it: the snippets are deleted when a speaker is named, so a
forgotten person leaves a `SPEAKER_NN` with no audio to play, and `referat label`
would have offered them and skipped forever. It now prints three of their lines
and lets you name them from those — the embedding is untouched, so it is only the
listening that was lost.

**Verified end to end on two synthetic meetings**, 197 checks: both speakers found
with embeddings and snippets and no names against an empty database; both named;
a second meeting with the same voices recognising them **without being asked**;
`--forget` reverting both transcripts and leaving no trace; the in-person case
declining to bootstrap the owner. Both TTS voices matched at 0.98 and 0.96 with
the runner-up around 0.5 — a separation that says the plumbing is right and
nothing at all about the thresholds, since identical synthesis of the same voice
is the easiest case there is. The 0.70 is still a guess, and step 7's open item —
diarization accuracy on a real call — still comes first.

## 2026-08-27 — Speaker identification, redesigned (documentation only)

No code. The plan for putting names on `SPEAKER_01` was an enrollment step —
sit down, record a sample of each person, build a database from that. It is
replaced by harvesting the same material out of meetings that already happened,
because diarization computes it anyway and an enrollment session is a chore that
would never actually get done for the eighth person.

**Every meeting is its own enrollment session.** pyannote hands back one
embedding per cluster (`DiarizeOutput.speaker_embeddings`, the clustering
centroids); Referat will keep it, cut two or three representative snippets of
that speaker to `speakers/`, and match the embedding by cosine similarity — the
metric those embeddings are built for — against a database of embeddings grouped
by name. Confident matches are labeled by name in `transcript.md`, everything
else stays `SPEAKER_NN`.

**`referat label <meeting-id>`** closes the loop: play the snippets, ask who that
was, add the embedding under that name, rewrite that speaker's labels. Fuzzy
completion over the names already known, `--forget <name>` to delete a person
entirely. The tray notification at the end of a meeting says how many voices went
unrecognized and names the command to run.

Three things that fell out of writing it down:

- **The snippets must be cut during the pipeline, not read on demand.** The WAVs
  are deleted as soon as a meeting transcribes cleanly, so offsets into
  `system.wav` would point at nothing by the time anyone ran `referat label`.
  Three six-second clips of 16 kHz mono is under 600 KB per speaker, and they go
  once that speaker has a name.
- **`meta.json` has to keep the `SPEAKER_NN` → name mapping**, not just the
  rewritten Markdown. Without it `--forget` has no way back to the right number,
  and a `referat rerun` — which re-diarizes and renumbers from scratch — has
  nothing to re-derive the names from.
- **The embeddings are ordered by `speaker_diarization.labels()`**, while
  `diarize.py` reads its turns out of `exclusive_speaker_diarization`, and the
  array can be `None` or zero-padded when there were fewer centroids than
  labels. Then `assign` renumbers everything by first appearance. Any one of
  those, mishandled, files a voice under somebody else's name.

**Two rules were made explicit in `CLAUDE.md`, both about what may be changed
and by whom.**

The first is immutability. Speech in `transcript.md` is never modified by
anything — but a speaker label is metadata that happens to live in that file, so
`referat label` may rewrite it, in both directions. That is the only edit
anything is permitted to make to an existing transcript.

The second is that the known-voices database is biometric personal data about
people who never asked to be in it. It lives in `<meetings_dir>/.voices/`, never
leaves the machine, is excluded from sync and backups, and `--forget` is a real
deletion. It also has to be kept away from the `/cleanup` pass, which runs
`claude -p` in that folder with write access — step 10's scaffold gains a
`.claude/settings.json` denying `.voices/**` for exactly that reason.

**Bootstrapping the owner is a special case with a trap in it.** The mic channel
is one speaker by definition, so it yields an embedding with no diarization at
all — except in a meeting held in person, where the microphone picks up the whole
room and the embedding is a blend of everybody. The automatic addition is
therefore conditioned on the loopback channel having had voice in it: a Zoom or
Teams call is the unambiguous case.

Written into `TODO.md` as **step 7b**, between diarization and the CLI, with the
labeling UI added as a sub-item of the extension step. Deliberately not
renumbered around: `CHANGELOG.md` and `CLAUDE.md` already refer to steps 8
through 12 by number.

## 2026-08-27 — Diarization: the remote channel gets names

Build step 7. `system.wav` is split per speaker, so a transcript now reads `ME`,
`SPEAKER_01`, `SPEAKER_02` instead of `ME` and an undifferentiated `REMOTE`.

- `diarize.py`: `pyannote.audio` over the loopback channel. `diarize()` returns
  turns, `assign()` gives each transcribed segment the speaker whose turns
  overlap it most, and the labels are **renumbered by first appearance** to
  `SPEAKER_01`, `SPEAKER_02`, ... pyannote's own are zero-based and ordered by
  nothing in particular, while the folder contract promises labels that read
  down the transcript in order. A segment no turn overlaps keeps `REMOTE`, which
  is the honest answer rather than the nearest speaker's name.
- The module takes an **already-decoded array**, not a path. That keeps it free
  of any import of `transcribe` — the dependency runs the other way — and lets
  `transcribe_channel` diarize the array it already has in scope, so each WAV is
  still read exactly once. `merge.py` prefers a segment's own speaker over its
  channel's label, which is the one line its docstring has been reserving since
  step 6.
- Only the loopback channel is diarized, and only when it transcribed to
  something. An empty channel is skipped with a reason that distinguishes the
  two cases — `no voice in the channel`, the ordinary in-person meeting, from
  `nothing was transcribed`, which is the one worth looking into.
- `meta.json` gains `speakers` and a `diarization` block per channel — status,
  reason, model, device, seconds, turns. The mic channel has neither: it is one
  speaker by definition and was never asked.

**Nothing in diarization may cost anything but speaker names**, and that shapes
the module more than the algorithm does. `diarize()` catches everything and
reports failure in its return value. An exception escaping it during the CUDA
attempt would reach `transcribe_meeting`, which cannot tell a diarization
problem from a dying GPU and would dutifully re-transcribe the entire meeting on
the CPU. Verified by watching a real failure do exactly the right thing: the
meeting finished `done`, the transcript kept its `REMOTE` labels, and the log
shows one model load, not two.

**pyannote.audio is 4.0.7 here, not the 3.x this repo assumed**, so the
checkpoint is `speaker-diarization-community-1` rather than
`speaker-diarization-3.1`, behind a new `[transcription].diarization_model`.
This is not a preference: 4.x's `SpeakerDiarization.__init__` eagerly loads a
PLDA from community-1, so even the old checkpoint would need that repository.
`pyproject.toml` now asks for `pyannote.audio>=4.0` rather than `>=3.3`, since
3.x spells the argument `use_auth_token=`; the lock was already on 4.0.7 and
nothing else moved. Both checkpoints are gated, and the token has to belong to
an account that has accepted the conditions — a `403 GatedRepoError`, which is what an unaccepted repo looks
like, degrades to `REMOTE` like any other failure.

**The `torchcodec` wall predicted at step 5 never arrived.** `pyannote.audio`
does bundle FFmpeg through `torchcodec`, and Smart App Control would block it —
but it only reaches for a decoder when handed a *path*. Handed
`{"waveform": tensor, "sample_rate": int}` it decodes nothing, which is the same
trick already played on faster-whisper. Smart App Control stays on.

**Two bugs found by running it, both of them older than this step.**

- `config.hf_token()` read the token file as UTF-8 and caught only `OSError`.
  The token file written here is UTF-16 with a BOM, because that is what
  `Set-Content` and `>` produce in Windows PowerShell 5.1 — so the very first
  real call raised `UnicodeDecodeError` from inside a transcription job. It now
  reads bytes and picks the encoding off the BOM, dropping anything undecodable
  rather than raising: a token is ASCII whatever wrote it.
- **The `av` stub from step 5 broke `import pyannote.audio`.** `_Absent`
  answered every attribute with `ImportError`, including `av.__file__`, which
  pyannote probes on the way in. An `ImportError` there is not a refusal to
  decode, it is a crash in code that was only looking, so diarization died at
  import before it ever reached the network. Dunder lookups now raise
  `AttributeError` — `hasattr` is merely False — while any real API access still
  fails loudly, which was the point of the stub.

Verified by driving the code, as at steps 1-6. Alignment in isolation: a segment
inside a turn, one straddling two turns with the majority winning, one
overlapping nothing falling back to `REMOTE`, renumbering that turns pyannote's
`SPEAKER_03`-first into `SPEAKER_01`, and an exact tie broken the same way five
times running. End to end on a synthetic meeting built the way step 6's was —
two Windows TTS voices, Hazel and Zira, alternating on a 48 kHz `system.wav`
with my own lines on `mic.wav` — **every line correctly attributed**: Hazel to
`SPEAKER_01` on lines one and three, Zira to `SPEAKER_02` on lines two and four,
`ME` on the microphone, 5 turns found in 27.5 s on the GPU. The real
`2026-08-27_1906` call skipped the pipeline as it should, its silent loopback
reported `no voice in the channel`, and it still released its 65 MB. Degradation
was forced four ways — diarization off, no token file, an empty model name, and
the real gated-repo `403` — and every one of them returned rather than raised.
Base imports still pull in no torch, pyannote or faster-whisper.

Also: the live `~/Meetings/CLAUDE.md` was a stale step-5 seed rather than a hand
refinement — it is seeded once and never overwritten — so it was reconciled by
hand against the template, as that rule intends.

Left in `~/Meetings`: three synthetic test meetings from this session
(`2026-08-27_2019`, `_2022`, `_2024`). `_2024` is the successful diarization and
is worth a look; all three are safe to delete.

## 2026-08-27 — The loopback channel, and the audio starts being deleted

Build step 6. `system.wav` is transcribed, both channels are interleaved into one
chronological `transcript.md`, and `release_audio_if_clean` — written at step 5
and dormant ever since, because it gates on *every* channel — fired for the first
time on a real meeting and reclaimed 65 MB.

- `merge.py`: `merge()` flattens both channels into ordered
  `(start, label, text)` entries and hands them to the existing
  `transcribe.render_transcript`. Sorting is by start, then channel, then end, so
  people talking over each other order the same way on every run instead of by
  whichever channel a dict listed first. `transcribe.entries_from` moved here.
  The module has no runtime import of `transcribe` — the type comes in under
  `TYPE_CHECKING` — so `transcribe` can import it at top level and importing
  `merge` stays free of the `transcribe` extra.
- `transcribe_channels` runs both WAVs through one loaded model, `ME` for the
  mic and `REMOTE` for the loopback until step 7 diarizes it. A channel whose
  file is missing is skipped, as before.
- **A failing channel no longer costs you the other one.** The CUDA attempt still
  lets any exception propagate, so the CPU fallback covers a GPU that is having a
  bad day; the final attempt keeps whatever worked and records the rest. A
  corrupt `system.wav` now yields a mic-only `transcript.md`, status `failed`,
  the per-channel error in `meta.json`, and both WAVs kept for `referat rerun`.
  `transcribe_meeting` still raises, because the meeting really did fail.

**Zero segments cannot simply mean "keep the audio".** Step 5 said it did, which
was fine while nothing was ever deleted. It is not fine now: the first real
meeting to run through this had a `system.wav` that was 96.7% digital silence —
the call was on a phone, not the laptop — and under the old rule that channel
would have pinned both WAVs to the disk forever, for every in-person meeting.

- Tried peak amplitude first, and it is the wrong measure. That file peaks at
  0.2965, comfortably "audible", entirely because of three notification chimes.
- The right measure is faster-whisper's own `duration_after_vad`: how much of the
  file its voice-activity detector took for voice at all. Zero segments *and*
  nothing voiced is a channel that held no speech, so there is nothing to lose;
  zero segments with voiced audio in it is exactly the case worth keeping. Both
  numbers land in `meta.json`, along with `peak`, which is now reported rather
  than judged on.

**`decode_wav` decodes in chunks.** An hour of 48 kHz `system.wav` is 345 MB on
disk and 691 MB the moment it becomes float32, and the whole-file path peaked at
1.7 GB per meeting-hour before faster-whisper allocated anything. It now reads a
minute at a time and resamples each chunk with a large overlap-save margin,
trimming the margins back off, which brings the peak down to about 230 MB per
meeting-hour — the array Whisper needs anyway. Verified against the old one-shot
implementation on the real 48 kHz `system.wav`: same length, maximum difference
0.0. `mic.wav` is already at 16 kHz and is still read in one go.

Verified end to end on the real 2026-08-27_1906 call (both channels clean, both
WAVs deleted, `audio_released: true`), on a synthetic meeting built by cutting
real speech out of `mic.wav` and laying it on both channels in alternating
windows (`ME` and `REMOTE` lines correctly interleaved, one `ME` line sitting
between two `REMOTE` ones), and on a meeting with an undecodable `system.wav`.

**Unrelated, and worth knowing.** Both WAVs of `2026-08-27_1906` were deleted to
the Recycle Bin at 19:38 during this session by something outside Referat — the
log shows the tray had quit at 19:16 and every release decision up to then was
"keeping the audio", and Referat's own deletion is `unlink`, which does not use
the Recycle Bin. They were restored and verified frame-for-frame against
`meta.json`.

## 2026-08-27 — Browsing and cleanup layer planned

Planning only. **Nothing was built**: this session touched `TODO.md`,
`CLAUDE.md`, `INDEX.md` and this file, and no source, template or extension
code. The build order grows from nine steps to twelve.

- **Steps 10-12 are gated on steps 1-9 working end to end on real meetings.**
  The gate is not ceremony: there is nothing to clean up until the loopback
  channel and the speakers are real, and a `/cleanup` prompt tuned against
  synthetic test meetings would be tuned against the wrong thing.
- **Step 10, the meetings folder scaffold.** `templates/` becomes
  `templates/meetings/`, a directory copied whole into the meetings folder: its
  `CLAUDE.md`, a `.claude/commands/cleanup.md` carrying the `/cleanup
  <meeting-id>` prompt, and a `.vscode/settings.json` associating `*.md` with
  the Markdown preview editor so Markdown opens rendered in that workspace only.
  Plus `referat/index.py` and a `referat index` command generating the meetings
  folder's own `INDEX.md` — the dashboard, which only works as one because of
  that preview association.
- **Step 11, a `referat-vscode/` extension**: a meetings TreeView off each
  `meta.json`, kept live by a file watcher, with per-meeting *Open transcript*,
  *Open notes*, *Generate notes* and *Re-transcribe*. **Step 12** packages it
  with `vsce` and documents sideloading in SETUP.md.
- **No Anthropic API key, ever — now a convention in `CLAUDE.md`.** Cleanup runs
  by spawning the official `claude` binary under the subscription login; no
  module here calls the API directly and the extension never handles
  credentials. This is exactly the decision a later session would undo by
  accident while reaching for the obvious library, so it is written where every
  session reads it rather than left in a plan.

Two design questions were settled rather than deferred.

- **The meeting title in `INDEX.md` comes from the H1 of `notes.md`**, falling
  back to the meeting id when a meeting has no notes yet. The alternative was a
  `title` key in `meta.json`, which would have meant the cleanup layer writing
  into the one file the meetings `CLAUDE.md` declares off limits. Keeping the
  title on the LLM side of the line leaves the raw record raw, and leaves the
  folder contract unchanged.
- **The scaffold is seeded once and never overwritten**, file by file: copy
  what is missing, leave what exists. The point of versioning the `/cleanup`
  prompt is refining it, and a refinement that gets clobbered on the next run is
  worse than no scaffold at all. Repo template and live copy will drift; that is
  accepted, and reconciled by hand when a change is worth keeping.

Also noted under "Surfaced later": the extension will spawn `claude -p` with
`--permission-mode acceptEdits` and `--allowedTools "Read,Write"` in a folder
holding private meeting material, which leaves the `/cleanup` prompt as the only
bound on what gets written there. Worth re-reading once the folder holds real
meetings.

## 2026-08-27 — Transcription of the mic channel

Build step 5. Meetings now produce a `transcript.md`. The four-second sleep that
stood in for transcription since step 2 is gone, replaced by a real
`faster-whisper` run on the GPU.

- `transcribe.py`: `large-v3` on CUDA in `float16`, resolved from
  `[transcription]` in the config, with `transcribe_meeting()` as the one entry
  point — the tray's background thread now, `referat rerun` at step 8. The mic
  channel only; `system.wav` waits for step 6.
- **Falls back to the CPU on anything at all going wrong on the GPU**, not just
  on a missing device: a failed model load, an out-of-memory, a driver hiccup.
  Dropping to the CPU also drops to `medium` and `int8`, because large-v3 on
  this CPU would outlast the meeting. `meta.json` records the backend that
  actually ran, not the one that was asked for.
- Segment times are positions in the WAV, which is already audio time with
  pauses excluded — the recorder simply stops appending samples while paused —
  so `[HH:MM:SS]` needs no conversion from anything. `render_transcript` takes
  ordered `(start, label, text)` entries rather than mic segments, so step 6's
  `merge.py` interleaves two channels and writes through the same renderer.
- Quality scoring per channel from faster-whisper's own signals: `avg_logprob`
  and `no_speech_prob` weighted by segment duration, `compression_ratio` taken
  from the *worst* segment, since one repetition loop is enough to distrust a
  transcript. Zero segments is explicitly not clean.
- **The audio deletion is written, tested, and deliberately dormant.** Recording
  costs ~460 MB an hour so something must reclaim it, but the gate is *every*
  channel in `meta.json`, not just the ones a given build step transcribes. At
  step 5 `system.wav` has no transcript, so nothing is ever deleted and steps 6
  and 7 still have their input; the same code starts reclaiming disk by itself
  the moment the loopback channel lands. The `audio` block survives deletion —
  frames, duration and device stay on record — and `audio_released` says the
  files went on purpose.
- Jobs are serialized on a process-wide lock and the model is dropped after each
  meeting rather than held warm. Two meetings really can overlap —
  `TRANSCRIBING -> RECORDING` has been a legal edge since step 2 — and two
  large-v3 models will not fit in 12 GB together. Recording is never what waits.
- `tray.py` hands the `Meeting` that `Recorder.stop()` already returned to the
  background job instead of dropping it. A job that throws is logged and the
  meeting is left marked `failed`, never stuck at `transcribing`.
- `paths.write_text_atomic`, with `write_json_atomic` now written in terms of
  it, so a failed re-transcription leaves the previous `transcript.md` intact
  rather than a truncated one.

**Two import-level landmines, both found by running the thing.**

- **Smart App Control is enforcing on this machine, and it blocks PyAV.** Its
  bundled FFmpeg DLLs are unsigned, so `import av` fails, and faster-whisper
  imports PyAV at module scope — which meant `import faster_whisper` failed
  outright. Turning Smart App Control off is a one-way, system-wide change
  Windows cannot undo without a reinstall, so it is not something a personal
  recorder should ask for. It is also unnecessary: PyAV exists in faster-whisper
  purely to decode media, and `transcribe()` skips it entirely when handed a
  numpy array. Referat's files are its own 16-bit PCM WAVs, so `decode_wav`
  reads them with the stdlib `wave` module and resamples with
  `scipy.signal.resample_poly`, which low-pass filters as it decimates —
  something step 6 needs for the 48 kHz `system.wav` regardless. A stub in
  `sys.modules` satisfies the leftover import and raises loudly on any actual
  attribute access, so a future faster-whisper that really needs PyAV fails
  visibly instead of quietly reading the wrong audio. **This will probably
  recur at step 7**: `pyannote.audio` pulls in `torchcodec`, which bundles
  FFmpeg the same way.
- **`torch` must be imported before `faster_whisper`.** ctranslate2's converters
  import torch halfway through their own import, and torch 2.11 does not survive
  being entered that way — it reaches `torch.utils._debug_mode` before
  `torch.library` is bound and dies with a circular-import `AttributeError`.
  Importing torch cleanly first makes it a no-op. It only showed up on the CPU
  path, where nothing else touches torch, so it would have broken every
  CPU-fallback run while the GPU path looked fine.

Also: `ensure_cuda_dlls` puts torch's bundled cuDNN and cuBLAS on the DLL search
path before the first CUDA model, because CTranslate2 links both and ships
neither; the handles are held module-level, since letting one be collected
removes the directory again.

Verified by driving the code directly, as at steps 1-4. `uv sync --extra
transcribe` installed torch 2.11.0+cu128; torch and CTranslate2 both see the
5070 Ti at compute capability (12, 0), and a `WhisperModel` really does
construct on `cuda`, which is what proves the DLL path fix. Known-speech test:
two sentences synthesized offline with Windows TTS, laid into a `mic.wav` at
2.0 s and 25.4 s with twenty seconds of silence between them, came back word
perfect at `[00:00:01]` and `[00:00:25]` — VAD trims a little lead-in, hence the
sub-second early bias. Forced `device = "cpu"` used `medium`/`int8` and
transcribed correctly; a simulated CUDA failure was caught and retried on the
CPU, with `meta.json` recording `medium on cpu`; an 8-bit WAV left the folder
`failed` with the reason recorded, no `transcript.md`, and the audio kept.
End to end through the tray's handlers, with the TTS clips played out of the
speakers and picked up acoustically by the microphone array: a meeting recorded
with a real six-second pause transcribed both sentences, the last line landing at
8 s inside 19.8 s of wall clock — the pause excluded, exactly as the folder
contract promises — while a second meeting started mid-transcription, got its own
folder, and both jobs drained through the lock back to `idle`. The two step-3 test
folders in `~/Meetings` transcribed cleanly too, and every run kept its audio,
declining with `system not transcribed`. `referat.tray` still imports without
pulling in `faster_whisper`, `torch` or `ctranslate2`, so a base install is
unaffected.

Not verified here: the real USB buttons, still the last unchecked box of step 2.
The deletion path has only ever fired on a synthetic meeting with both channels
marked clean — it cannot fire for real until step 6.

## 2026-08-27 — Sleep prevention

Build step 4. Windows no longer idle-sleeps out from under a meeting.

- `power.py`: `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`
  through `ctypes`, held while the recorder is `recording` or `paused` and
  dropped on stop. Deliberately no `ES_DISPLAY_REQUIRED` — the screen may blank,
  the machine may not sleep — and deliberately not held during transcription,
  which can outlive its meeting by many minutes and is no reason to keep the
  laptop awake.
- **The flags are per-thread, and that shapes the whole module.** They belong to
  the thread that set them and die with it. Referat changes state from at least
  three: the `keyboard` hook thread on a hotkey, the pystray thread on a menu
  click, and a transcription thread finishing a job in `Machine.end_job`. A hold
  taken on one and cleared from another would silently leave the machine awake
  for the rest of the session. So `SleepBlocker` owns one daemon keeper thread
  and every call to the API is made there; `set()` only records what is wanted
  and wakes it. Measured directly rather than taken on faith: the main thread
  read back `ES_CONTINUOUS|ES_SYSTEM_REQUIRED`, a second thread read back plain
  `ES_CONTINUOUS`, and clearing from that second thread left the first still
  holding.
- The keeper records what it last asked of Windows *before* checking whether the
  call succeeded, so a failing API cannot spin the loop; a failure is a warning
  in the log and never an exception, on the same rule as `status.write_status`.
  `close()` is idempotent and drops the hold.
- `tray.py` wires it as its own state listener, registered *before* the icon
  repaint. `Machine._notify` runs listeners in order and isolates their
  exceptions, so a repaint that throws can never cost the machine its sleep
  hold. `quit()` and the `finally` in `run()` both close the blocker.

Verified by driving the code, as at steps 1-3. `powercfg /requests` turns out to
be administrator-only, so it could not be used from an ordinary session; instead
the state was read back out of Windows directly, which `SetThreadExecutionState`
supports by returning the previous state — a stronger check, since it reports the
calling thread rather than the process. Through the tray, driving the two
hotkey handlers: hold taken on `idle -> recording`, still held across
`paused` and the resume, released the moment the state reached `stopped`, still
released through the four-second simulated transcription and back to `idle`, and
released again by `quit()` called mid-meeting. Exactly four syscalls for two
meetings, all of them on the `power` thread — the pause/resume cycle collapses to
nothing, as intended. The `powercfg /requests` cross-check is left as a manual
step in an elevated shell, noted in TODO.

Not verified here: the real USB buttons, still the last unchecked box of step 2.

## 2026-08-27 — Dual-stream WAV recording

Build step 3. Meetings are now real: a folder on disk, two WAV files written
continuously while recording, and a `meta.json` that survives the process being
killed. Nothing is transcribed yet.

- `meeting.py`: the `Meeting` record and `meta.json` I/O. `MeetingStatus` is
  deliberately separate from `state.State` — the state machine describes the
  *recorder*, which has a `paused` state, while `meta.json` describes what is on
  disk, where a paused meeting is still `recording`. Folder creation goes through
  the existing `paths.new_meeting_dir`, so the id carries any `_2` collision
  suffix rather than the two disagreeing. `load()` ignores unknown keys and
  defaults missing ones, so a folder written by a later version still reads.
- `recorder.py`: mic through `sounddevice` (mono, 16 kHz) and system audio
  through `PyAudioWPatch` WASAPI loopback (native mix rate, downmixed to mono in
  the writer thread). Audio callbacks only put bytes on a queue; a writer thread
  per channel does all disk I/O, so a slow disk can never stall PortAudio.
- **Windows delivers no loopback frames at all while nothing is playing** —
  measured here as exactly zero frames in four seconds of silence. Written
  naively, `system.wav` would have held only the noisy stretches back to back:
  every silence gone, everything after it shifted earlier, and the step-6 merge
  quietly wrong for every meeting with a quiet moment in it, which is all of
  them. Both channels are therefore written against a `RecordingClock` and padded
  with zeros when they fall behind, so a position in either WAV always means the
  same moment. It costs about 345 MB an hour for `system.wav` plus 115 MB for
  `mic.wav`; keeping the WAVs only when a transcript comes out badly is now a
  task under step 5.
- Crash safety: `WavWriter` writes the canonical 44-byte header up front and
  rewrites the two size fields in place every two seconds, then fsyncs. The
  `wave` module only fixes its header on `close()`, which is exactly what a kill
  skips.
- One channel failing does not cost the meeting. A device that will not open is
  logged and the other channel records alone; a device that refuses 16 kHz is
  reopened at its own default rate and the real rate goes into `meta.json`. Only
  when neither channel opens does `start()` raise, and that folder is marked
  `failed`. Device selection is a case-insensitive substring against the config,
  with WASAPI winning when several host APIs expose the same microphone, and an
  unmatched substring warns and falls back to the default rather than failing.
- Two clocks, both recorded honestly: `pauses` in `meta.json` are wall-clock
  seconds since the start, positions in the WAVs are audio time with pauses
  excluded, and either converts to the other from the pause list alone.
  `duration_seconds` stays wall clock. `meta.json` is rewritten on every pause
  and resume, so a crash mid-pause still says what was happening.
- `tray.py` now drives the recorder. The streams open *before* the state
  transition, because the transition carries the meeting id and only the
  recorder knows it once the folder exists — this replaces the invented id that
  step 2 left behind. A failure to open any device leaves the tray idle instead
  of pretending to record, and quitting mid-meeting closes the meeting out.

Verified by driving the code directly, as at steps 1 and 2 — the repo still has
no test framework. Round trip: 11.75 s of wall clock produced 11.63 s of
`mic.wav` and 11.71 s of `system.wav`. Alignment: staying silent for the first
five seconds and then playing a tone put the tone into `system.wav` at 6.74 s
against a measured playback start of 6.47 s — the silence really is padded, and
the residual is output-device latency. Pause: a three-second pause left one
interval in `meta.json` and made both channels three seconds shorter than wall
clock while `duration_seconds` stayed wall clock. Kill: `taskkill /F` six seconds
into a meeting left both WAVs opening cleanly in `wave` at ~7 s with a
`meta.json` still marked `recording`, exactly as the meetings template promises.
Degradation was forced on each channel in turn and on both at once. The tray was
driven end to end through its handlers: folder created, `status.json` meeting id
matching the folder name, a second meeting starting while the first still
transcribed and correctly landing in `..._2`, and quit-while-recording closing
that meeting out.

Not verified here: the real USB buttons, still the last unchecked box of step 2.

## 2026-08-27 — Tray app, state machine, hotkeys

Build step 2. The shell the rest of the app hangs off: nothing records yet, but
every state the recorder will move through now exists, is enforced, is logged,
and is visible in the tray and in `status.json`.

- `state.py`: the five states (`idle`, `recording`, `paused`, `stopped`,
  `transcribing`, matching the `meta.json` `status` vocabulary) and a table of
  legal transitions. `to()` raises `IllegalTransition` on an illegal edge,
  `try_to()` logs and returns False instead — a button pressed in the wrong
  state is a no-op, not a crash. An `RLock` guards every mutation, because the
  hotkey callbacks arrive on the `keyboard` hook thread while pystray owns the
  main thread. Listeners are notified outside the lock and an exception in one
  is logged and swallowed, so a broken listener can never abort a recording.
- **A new meeting can start while the previous one is still transcribing.** The
  state describes the recorder; transcription jobs are counted separately by
  `begin_job()` / `end_job()`, and `TRANSCRIBING -> RECORDING` is a legal edge.
  A job finishing returns the machine to `IDLE` only if it is still transcribing
  — if the user has started recording again, it only decrements the count.
  Blocking a meeting on background work would be the one failure this project
  cares about most.
- `hotkeys.py`: registers the two configured combos through `keyboard`, with a
  400 ms per-combo debounce. Holding a key auto-repeats, and the programmable USB
  buttons are no different; without it a slightly long press would start and
  immediately stop a recording. A combo that will not register is logged and
  skipped rather than raising, so a typo in `config.toml` leaves the tray usable.
- `tray.py`: `pystray` icon as a 64x64 disc, one colour per state — gray idle,
  red recording, amber paused, orange stopping, blue transcribing — with the
  state also in the tooltip, so it does not depend on hue alone. Menu shows the
  current state as a disabled header, then open meetings folder (also the
  double-click default), open config, and quit. Quitting stops the hotkeys and
  closes out a running meeting rather than abandoning it. A named mutex
  (`CreateMutexW`) keeps a manual launch from fighting an autostarted tray over
  the same hotkeys.
- `status.py`: `status.json` written atomically on every transition with the
  state, pid, meeting id, start time and job count. `referat status` reads it at
  build step 8; the pid is recorded now so that command can tell a live tray from
  a file left behind by a crash.
- `referat-tray` added under `[project.gui-scripts]`, so autostart can launch it
  through `pythonw.exe` with no console window.
- Stopping a meeting currently sleeps four seconds in a background thread in
  place of transcription, marked `BUILD STEP 5`. It exists so the stopped and
  transcribing icons, the transition log and the status writes could all be
  exercised before there is any audio to work on.

Verified by driving the code directly, as at step 1 — the repo still has no test
framework. All legal edges accepted and all illegal ones refused without moving
the machine; meeting id set on record, kept across a pause, cleared on idle;
listeners ordered and a raising listener contained; overlapping transcription
jobs returning to idle only after the last one. The tray's own handlers were
driven end to end: icon, tooltip and `status.json` agreed at every step,
including recording again mid-transcription and the old job then finishing
without stealing the state. Menu items were invoked the way pystray invokes
them, quit-while-recording closed the meeting and removed `status.json`, a
second instance exited with "already running", and the transition log showed the
full `idle -> recording -> paused -> recording -> stopped -> transcribing ->
idle` sequence.

Not verified here: synthetic keystrokes cannot be injected from an automated
session, so the hotkey path was tested by calling into the hook side directly.
Pressing the real USB buttons, and looking at the icon, are left to check on the
machine — the last unchecked box of step 2.

Also settled: `uv` was thought to be missing from PATH. WinGet had in fact put
its Packages directory on the persisted user PATH all along; only shells started
before the install lack it, and a new terminal is the whole fix. Inside an
activated venv `python -m referat.tray` needs no `uv` at all.

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

Verified: `uv sync` provisions CPython 3.12.14 and installs 10 base packages
with no torch pulled in; `uv run referat config` prints the loaded config;
`pyaudiowpatch` 0.2.12.8 has a working 3.12 wheel, so the loopback capture
library needs no substitute. Config bootstrap, meeting-folder collision
suffixing, atomic JSON writes, unknown-key warnings, and enum validation were
all exercised directly. `git init` plus a first commit; `uv.lock` committed.

Also added `.vscode/settings.json` (tracked; `.gitignore` now excludes the rest
of `.vscode/`). VS Code had auto-selected the system Python 3.14 for this
workspace before the venv existed, and the Python Envs extension was failing
`python -m pip list` because uv creates venvs without pip. The settings pin
the interpreter to `.venv\Scripts\python.exe` and set
`python-envs.alwaysUseUv`.

No tray, audio capture, or transcription code yet — that starts at build step 2.
