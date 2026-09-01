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
Markdown transcripts into a meetings folder. There is **no GUI but the tray icon
and a VS Code extension — no web UI, ever** — and **no automatic LLM cleanup
pass**: notes are generated lazily by the user, through a `/cleanup` slash
command run by Claude Code in the meetings folder — interactively, or headless
via `claude -p` from the VS Code extension. Notes for a meeting tagged with a
project are pushed, on request, into that project's Google Doc digests.

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

This is the *recorder's* state, deliberately not the meeting's — the meeting's
lifecycle lives in `meta.json`'s `status` and has no `paused`, because a paused
meeting is still being recorded.

**The tray tags meetings too, from step 16.** On stop it raises a toast asking
which project(s), offering recent ones and defaulting to untagged if it times
out; its menu grows a lazily built *Tag recent…* submenu of untagged meetings
from the last seven days. That toast needs a real WinRT notification —
`pystray`'s `icon.notify` is a buttonless balloon that does not persist in Action
Center — which is a new base dependency taken deliberately against the
minimal-dependencies rule, and whose activation half may not prove workable here;
if it does not, the toast degrades to a plain notification and the menu carries
the feature. **A notification that fails is a log line and never touches the stop
path.** Tray tagging is single-tag quick assignment; multi-tag editing lives in
the extension.

**Dual-stream capture** while recording, both streamed to disk continuously:

- Microphone, mono 16 kHz, via `sounddevice`. **Not "the user" — the room.**
  Referat is mostly used for meetings held in person, where everybody at the
  table arrives through this one microphone, so it is diarized exactly as the
  loopback is. It was taken as one speaker by definition once, and that
  assumption merged an entire two-person meeting into a single `ME`.
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
CUDA, degrading to CPU + medium when CUDA is unavailable. **Both channels** are
diarized with `pyannote.audio` 4 (`speaker-diarization-community-1` — the
checkpoint that matches the installed major version, *not* the 3.1 pipeline this
file used to name), which needs a Hugging Face token from an account that has
accepted that repository's conditions. Whisper segments are labeled by which
speaker turn overlaps them most, renumbered `SPEAKER_01`, `SPEAKER_02`, ... by
first appearance. The numbering runs across the **meeting**, not the channel —
`diarize.assign` takes the first free number, because two channels each starting
at `SPEAKER_01` would put two different people behind one label in the same
transcript. Both channels are merged into one chronological, timestamped
transcript.

**Hotwords are the only correction that happens before the transcript exists.**
Every transcription is handed one global list through faster-whisper's
`hotwords`, so a name the model has been told about is heard right the first
time rather than corrected afterwards. `referat/hotwords.py` merges it from
three sources: every name in the known-voices database, every project's
`glossary` in `projects.json`, and `[transcription].hotword_extras` in
`config.toml`. It is **one list for the machine and never one per project**,
because a meeting is tagged *after* it has been transcribed — at
transcription time there is nothing to select on, and the alternatives that
would create something to select on each break a rule that matters more (TODO
step 12b records all three). The merge is called from `transcribe_channel`, not
from `cli.py`: the tray never goes through the CLI, and a hotword list that
applied only to `referat rerun` would make a rerun produce a different
transcript from the recording it came from. It reads the voices database live
rather than a cached copy, so a `referat label --forget` takes that name out of
the list with it. Whisper's prompt window is 224 tokens, so the list is capped
in a fixed priority order — extras, then names, then glossaries — and whatever
is dropped is logged rather than dropped quietly.

**The owner is rendered by name, like everybody else, and `ME` means only
"nothing attributed this".** The pipeline used to rewrite a voiceprint-matched
owner to `ME` while `referat label` wrote the name it was given, so one person
could appear under two labels in a single file — `2026-09-01_2102` came out with
178 `ME:` lines and 4 `Niklas:` lines for the same voice. That rewrite is gone,
which also repairs two things that hung off it: `referat label --forget <owner>`
could not revert `ME:` lines it went looking for by name, and a `rerun` flipped a
hand-labeled name back to `ME`. So there are four kinds of label and each says one
thing: `ME` and `REMOTE` for mic and loopback speech nothing attributed —
diarization did not run, or no turn overlapped the segment — `SPEAKER_NN` for a
distinct voice nobody has named, and a name for a voice identified by match or by
hand.

`ME` must never be widened back into a name. The microphone is the room, not the
user: `2026-08-28_1152` is 258 lines of `ME` that are actually two people, and
spelling a name onto unattributed speech is the same failure as putting a name on
a `SPEAKER_NN`. `referat relabel` is the repair for the transcripts written under
the old rendering, and it is deliberately narrow — see the CLI section.

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
embedding to the database under that name and rewrites the labels. A speaker
whose snippets are already gone — named once, then forgotten — is offered with
three of their lines to read instead, rather than being skipped forever. Every meeting
is therefore its own enrollment session, and the next meeting knows more voices
than the last. The snippets are cut during the pipeline precisely because the
WAVs do not survive it, and they are deleted once their speaker has a name.

The **owner's** voiceprint is bootstrapped from a meeting whose *microphone
clustered into exactly one speaker* — measured, not inferred. The rule used to be
that the loopback channel had to hold voice, on the reasoning that a remote call
puts the far end on the loopback and leaves the mic to the owner alone; sound
reasoning about the wrong condition, since it can only fire on the calls that are
the exception here, so for the meetings Referat is actually used for the owner
voiceprint would never have been created at all. One cluster on the mic is true
of a solo recording and of a remote call, false of a room with two people in it,
and it also refuses the desk-speakerphone case the old rule was most exposed to:
voices echoing back off the speaker cluster as a second speaker rather than
quietly poisoning the owner's own voiceprint. The embedding is that cluster's
centroid, which diarization already computed, so nothing is re-embedded.

**A wrong name is worse than no name.** Matching accepts only above a similarity
threshold *and* only when the winner beats the runner-up name by a margin;
short of that the speaker stays a number. **A database holding one name is the
dangerous case**, not the easy one: there is no runner-up, so the margin would be
trivially satisfied and the threshold left deciding alone — and that is the state
the database is in immediately after `owner_name` is first set, which is also
when a false accept costs the most, since it renders somebody else's words under
the owner's own name. With no runner-up the margin is required of the score itself. This is
measured rather than argued: a different synthetic voice scored 0.7404 against a
lone stored voiceprint and was accepted at the 0.70 threshold until it was fixed. Identification degrades like
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

`torchcodec`, which `pyannote.audio` pulls in, bundles FFmpeg the same way, and
it takes **two** measures rather than one. Pyannote reaches for a *decoder* only
when handed a path; handed `{"waveform": tensor, "sample_rate": int}` it decodes
nothing, which is the same trick already played on faster-whisper. That was long
recorded here as the whole answer, on the reasoning that torchcodec "never gets
the chance to load", and it was wrong about the half that is an *import* rather
than a decode: `pyannote.audio.core.io` imports `torchcodec` at module scope
whatever it is later handed, and that import alone walks
`libtorchcodec_core{N}.dll` down six FFmpeg major versions, collecting a Windows
Security notification per refusal. Pyannote catches the failure and sets
`TORCHCODEC_AVAILABLE = False`, so this only ever cost notifications and never a
transcript — which is exactly why it went unnoticed until somebody read the
toasts. `diarize._neutralize_torchcodec` now stubs the module out before
pyannote is imported. So: **anything that ships FFmpeg should be fed decoded
audio, and stubbed out if it imports eagerly.** Never trying the real import is
the one way this stub differs from `transcribe._neutralize_pyav`, where the
attempt is the diagnosis; here the attempt is the problem, and Referat has no
use for torchcodec even where it loads cleanly.

**`scipy` is the third instance, and the one that taught what the rule actually
is.** `_resample_stream` used to call `scipy.signal.resample_poly` to bring the
48 kHz loopback down to 16 kHz. On 2026-09-01 a `referat rerun` died there: SAC
blocked `_odepack.pyd`, reached through `scipy.signal` -> `scipy.stats` ->
`scipy.integrate`. Retried minutes later it blocked `_stats_pythran`, then
`_sobol`, and then stopped blocking anything — the cloud reputation had arrived.
All 106 of scipy's `.pyd` files are unsigned, so **a block here is not a
permanent state but a window**, and the window opened on the one call standing
between a recording and its transcript.

So `transcribe._resample` is Referat's own polyphase resampler, in numpy and the
stdlib. It is contract-compatible with `resample_poly` — same output length, same
zero-phase alignment, same zero padding, same Kaiser kernel at the same default
width — so a `rerun` of an older meeting decodes to the same samples it did the
first time; measured against it on a real `system.wav` at 138 dB, which is float
rounding, with an identical -67.6 dBFS stopband. It costs about 12 seconds per
meeting-hour.

**The rule is narrower than "stub out anything that ships FFmpeg".** It is:
*nothing on the path from a WAV to a transcript may depend on unsigned native
code that is not already unavoidable.* numpy is unavoidable — nothing in
`transcribe.py` runs without it — so writing the resampler against numpy alone
adds no new way to fail, while scipy on that path added a hundred of them. That
is the same sentence as SETUP.md's "keep the critical path signed, accept
degradation elsewhere", said from the code's side.

**Diarization still imports scipy and that is left alone**, because it must:
`pyannote.audio` -> `lightning` -> `torchmetrics` -> `scipy.signal`, with no
seam to cut. When the window is open, diarization fails and `diarize.diarize`
degrades to undifferentiated labels exactly as it does for a missing token — a
meeting transcribed during one loses its speaker names and nothing else. That is
the degradation the rule *permits*, and the reason it is worth keeping the
transcript on the other side of the line.

`transcribe.DecodeError` exists for the same failure. A decode is device-neutral
and happens before any model is loaded, so a `DecodeError` on the CUDA attempt
must **not** trigger the CPU fallback: the second pass would fail in the same
place several minutes later and bury the real cause inside a duplicated
traceback, which is exactly what the scipy block produced.

**CLI** (`referat`), for the user, for Claude Code and — since step 11 — for the
VS Code extension: `config`, `list`, `rerun <id>`, `label <id>`, `status`,
`devices`, `index`, `project <verb>`, `tag`, `untag`, `state`, `promote <id>`,
`reflow [<id>]`, `relabel [<id>]`, `delete <id>`. All of them are built except
`project link-doc`, `project unlink-doc` and `project sync`, which wait for the
digests at step 13 — and which are deliberately absent from the parser rather
than present and answering "not built yet".

`reflow` and `relabel` are the two repairs, and they are the same shape: both
exist because a rendering rule changed after transcripts had already been written
whose audio has since been released, so no `rerun` can regenerate them. Both are
idempotent, both write nothing when there is nothing to change, and neither is an
exception to the immutability rule — `reflow` inserts whitespace *between* lines
and `relabel` rewrites the label field alone, through the same
`label.relabel_transcript` that `referat label` uses.

**`relabel` is deliberately narrow**, because the label it is replacing means two
things and only one of them is repairable. It spells the owner's name into `ME:`
lines **only for a meeting whose microphone clustered into nothing but the
owner** — a condition measured off the clustering, the way `bootstrap_owner`'s is,
rather than inferred from what kind of meeting it was. Everywhere else `ME` may be
somebody else in the room and stays. Even where it fires, some of those lines were
`diarize.assign`'s no-overlapping-turn fallback rather than the owner's matched
cluster, and nothing on disk records which; naming them is defensible **only**
because the gate has proved the sole voice diarization found on that microphone
was the owner's, which is a much narrower claim than "the microphone is the
owner". That is a deliberate exception to *never spell a name onto unattributed
speech*, and it is bounded by that gate.

`delete <id> [--yes]` removes a meeting folder and everything in it. It refuses
while the meeting is `recording` or `transcribing`, and its confirmation says the
two things somebody would otherwise assume the other way: a meeting inside a
**synced** meetings folder is not really gone, since a sync client keeps deleted
files and prior versions on its servers for weeks — the same fact that put
recording and transcription in a staging folder — while a staged one is; and the
**voiceprints** the meeting contributed stay in `.voices/`, because deleting a
meeting is not deleting a person and `label --forget <name>` is what is. `--yes`
is not a convenience: the prompt reads `input()` and answers *no* on EOF, so the
extension could not confirm without it, exactly as with `label --forget`.

**The CLI owns every mutation, and is the only implementation of any of this.**
The projects file, the tag logic, the lifecycle vocabulary and the rules about
what a name may be live in Python exactly once. The VS Code extension shells out
to them because it is TypeScript and has no other way in; the tray imports them,
because it is already a Python process inside this package and spawning a
subprocess of its own CLI would buy nothing. The rule is one implementation, not
one process boundary — worth saying in both directions, since a later reader
could "fix" it either way.

**Five commands answer in JSON**, and only because the extension asks. `referat
list --json` is every meeting with its duration, status, audio state, title,
unnamed speakers, `tags` and folder, across both roots, plus one top-level map of
project id to display name; `referat project list --json` is every project with
its docs, its glossary and how many meetings carry it, plus the orphaned ids no
project answers to; `referat label <id> --json` is one meeting's unnamed speakers
with their snippet paths, sample lines and the **channel** each arrived on, plus
the owner's name at document level — the two fields that let the sidebar say
*this voice came out of your own microphone* and lead the chips with the owner,
which is a hint and never a name applied on its own; and `referat status --json`, added at
step 15 for the sidebar's status bar item, is what the tray is doing — with
`elapsed` as `format_duration`'s own output, so nothing in TypeScript formats a
duration, and a `stale` flag that tells a tray which died apart from one that
never ran. The fifth is `referat project add <name> --json`, which emits the
project it just created: an id is `slugify` plus a `-2` collision suffix, so a
caller cannot work it out, and looking it back out of `project list` by display
name is wrong the moment two projects share one. All five are the same functions the
human-readable forms call, so the table, the dashboard and the sidebar cannot
drift apart — and the id-to-name map inside `list --json` comes out of the very
`ProjectsDB.name_map` that `project list --json` hands out, so one subprocess
feeds the whole sidebar and the join between a tag and its name cannot drift
either. The tables themselves are unchanged and stay the default.

`referat label` also grew the three flags that let something without a terminal
name somebody: `--speaker SPEAKER_NN --name <name>` applies one name,
`--forget <name> --yes` skips the confirmation, and `--json` dumps the meeting.
They exist because the prompt cannot be driven from a subprocess — it reads
`input()`, and `--forget`'s confirmation answers *no* on EOF, so a caller with
no terminal was told "Nothing was deleted." Each is a thin wrapper over
`label.apply_name` and `label.forget`, which the module docstring had reserved
for exactly this since step 7b. **The rules are not duplicated in the wrapper**:
`voices.name_complaint` still decides what a name may be, and the apply path
refuses a speaker who already has one, because renaming is a different operation
from naming.

`list` is the queue as much as the inventory: one row per meeting with its
duration, lifecycle status, whether the WAVs are still on disk, how many speakers
are still numbers waiting for `referat label`, and the project ids it carries.
The TAGS column prints **ids rather than display names**, because the next thing
typed after reading it is `referat untag <meeting> <id>`; an id no project
resolves gets a trailing `?` rather than being dropped, since a tag disappearing
quietly off three meetings is how you lose track of what a meeting was about. `status` reads the tray's
`status.json` and asks Windows whether that pid is still alive — through
`OpenProcess`, never `os.kill(pid, 0)`, which on Windows *terminates* the
process it is asked about. `rerun` transcribes a meeting again from the audio it
kept; it renumbers the speakers, so it clears the previous run's names and
snippets first and lets identification find the names again in the known-voices
database.

`devices` exists because `[audio].mic_device` and `[audio].loopback_device` are
**substring matches that fail silently**: one that matches nothing falls back to
the default and says so only in a log line, so the wrong microphone is recorded
and nobody finds out until the meeting is over. It lists the input devices and
the WASAPI loopback sources, marks the one the config resolves to and the one
Windows would pick on its own, and says plainly when a configured substring
matched nothing. It calls `recorder.resolve_mic_device` and
`recorder.resolve_loopback_device` rather than reimplementing their rules —
a listing that disagreed with what actually gets recorded would be worse than no
listing at all. PortAudio exposes one microphone once per host API, and the
copies differ in ways that matter: naming a device picks its WASAPI copy, while
leaving the key empty usually gets the MME one at a different sample rate.

Of the project verbs, only `project link-doc` and `project sync` need the
`digest` extra; `project add|rename|rm|list`, `tag`, `untag` and `state` are a
JSON read and a JSON write. `tag` refuses an id no project answers to, because
the one way to create an orphan should be deleting a project rather than
mistyping; `untag` asks no such question, since an orphan is exactly the tag
somebody needs to be able to take off a meeting. And a mutation is refused
outright when `projects.json` exists but will not parse: it loads as *no
projects*, which is right for reading and would silently replace the file with an
empty list on the next write. Only `rerun` needs the `transcribe` extra, and it imports it inside the
function. Everything else in the CLI is JSON reads and WAV playback: naming
somebody costs an embedding that was already computed during the pipeline, not
three gigabytes of resident torch. `rerun` also refuses while a live tray is
transcribing — `transcribe._RUN_LOCK` serializes jobs within one process and
cannot see across one, and two large-v3 models do not fit in 12 GB of VRAM.
`--force` overrides it.

## Notes and browsing (build steps 10-12)

**Steps 10, 11, 15 and 12 are built**, in that order — 12 was re-sequenced to run
after 15 because it would otherwise have packaged the TreeView step 15 deleted.
The scaffold, the `/cleanup` prompt, `referat index` and the extension
exist and have been driven on a real transcript. What is still open is the tuning
the gate was really about: the prompt has met one meeting, and one held in person
at that, so it has never been read against a call with diarized speakers in it.

**Cleanup is lazy and lives in the meetings folder, not in the code.** The
folder gets a scaffold, seeded from `templates/meetings/`: its `CLAUDE.md`, a
`.claude/commands/cleanup.md` holding the `/cleanup <meeting-id>` prompt, a
`.claude/settings.json` denying all access to `.voices/`, and a
`.vscode/settings.json` associating `*.md` with the Markdown preview editor so
Markdown opens rendered in that workspace only.

That same `.vscode/settings.json` sets **`editor.copyWithSyntaxHighlighting:
false`**, which is about getting text *out* of this folder. VS Code puts two
flavours on the clipboard when you copy from an editor — plain text and an HTML
one carrying the theme's colours and font — and Word, Google Docs and Outlook all
prefer the HTML one, so pasting part of a note arrives as dark-background
monospace. Turning that flavour off makes a paste anywhere the raw Markdown, with
its `##` and `-` intact. The pair is worth stating together: **copy from the
source for Markdown syntax, from the preview for real headings and bullets**, and
the association above is why reaching the source needs the preview's *Open
Source* action or *Open With… → Text Editor*.

**House style for the notes lives in that `CLAUDE.md` and in the prompt, not in
code.** U.S. spelling is the first such rule: the notes are written *organize*,
*analyze*, *color*, and the transcript is not touched, because it is immutable and
keeps whatever Whisper heard. Nor are **names** — a person, a product or an
institution keeps its own spelling however British or Swedish, so `Centre for
Human-Centred Computing` stays exactly that, and neither is the inside of a
quotation. That is the same shape as every other rule here: a wrong normalization
reads as authoritative, so it is bounded to the writer's own prose. It is changed
by editing Markdown in the meetings folder, which is the whole point of the
prompt being a versioned file. `/cleanup` reads a meeting's
`transcript.md` and writes `notes.md` beside it — decisions, action items,
discussion summary, `[[Wikilinks]]` for people and projects — and never touches
the verbatim transcript. The prompt is a versioned file, so it is refined by
editing Markdown rather than by changing code.

**`/cleanup` is also where transcription errors get corrected**, since by the
immutability rule they cannot be corrected where they happened. The meetings
folder's `CLAUDE.md` carries a *Known people and terms* section, and a meeting
tagged with a project carries that project's `glossary` as well; both are lists
of what things are actually called. An exact match against either is normalized
silently. A **near miss is corrected and flagged in the note itself** —
`Elmqvist (assumed transcription error: "Elmquist")`, once, at first use —
because a silently applied guess is the same failure as putting a name on a
`SPEAKER_NN`: it reads exactly as authoritative when it is wrong. That is the
existing rule *a wrong name is worse than no name*, applied to words rather than
to speakers. A word matching neither list is left as transcribed.

**Every scaffold file is seeded once and never overwritten**, by
`paths.seed_tree` walking the template directory file by file. A prompt refined
in place in the meetings folder has to survive; the repo template and the live
copy are allowed to drift, and are reconciled by hand.

**The deny rule is a `Read`/`Edit` rule and nothing else.** `Write(...)` in a
permission deny list is inert — Claude Code matches file permissions against
`Read(path)` and `Edit(path)` only, and `Edit` covers every file-writing tool.
A `Write(**/.voices/**)` line was written first and Claude Code said so out loud
on the first run; it looked like defence in depth and was decoration. Note also
that these rules bind the file tools, not `Bash`: a `cat .voices/voices.json`
would go straight past them, which is survivable only because `/cleanup` is
spawned with `--allowedTools "Read,Write,Glob"` and has no shell. Those three
are the slash command's own frontmatter, which is the authority on what the
prompt needs — `Glob` is what its wrong-meeting-id fallback lists the real ids
with, and it returns paths rather than contents, so the deny rule is untouched
by it. This file said `"Read,Write"` until step 11 went to spawn it and found
the two disagreeing. **`Bash` is the line that matters and it does not move.**

`referat index` regenerates the meetings folder's own `INDEX.md`, a table of all
meetings — date, title, duration, links to transcript and notes — also written
at the end of the transcription pipeline. With Markdown opening rendered, that
file is the dashboard. The title is the H1 of `notes.md`, falling back to the
meeting id; it is deliberately not a `meta.json` key, since the cleanup layer
must not edit the raw record. Note that this is a *different* file from this
repository's `INDEX.md`, which the standing instruction above is about.

**VS Code is the primary UI**, with a `referat-vscode/` extension. Step 11 built
it as a meetings TreeView; **step 15 replaced that with a sidebar webview** — one
row per meeting, reverse chronological, carrying date, duration, project tag
chips and a strip rendering the lifecycle state, with project CRUD, the tag
picker and speaker labeling all inside it, and a status bar item for ambient
state. **There is no web UI in this project and never will be**, so the extension
is where every graphical surface above the tray icon lives. A VS Code webview is
part of the extension and is not a web UI; a localhost server would be, and there
is none.

**The rows are grouped by project**, one collapsible section per project ordered
by its most recent meeting, then one per orphaned tag id, then *Untagged* last —
which is the queue. A meeting carrying two tags is drawn under **both**, because a
project is a label rather than a container; the flat list it replaced does not
survive a year of meetings. Grouping, the search box beside the *Untagged only*
toggle, and the newest-first order are all **presentation and live in the page**:
`referat list` stays oldest-first and learns nothing about any of it. Which
sections are folded is the one piece of page state kept across a reload, through
`vscode.setState` — expanded *rows* are not, because refilling one costs an
interpreter start.

**The page renders itself; the host only feeds it.** `sidebar.ts` sets
`webview.html` once and thereafter posts documents, because re-assigning the HTML
on every refresh would collapse an expanded row and stop a snippet mid-playback —
and transcription rewrites `meta.json` several times a meeting. The page builds
every node with `textContent` rather than `innerHTML`: a meeting title comes out
of somebody's `notes.md` and a speaker's line out of a transcript, and neither is
markup.

The one row that needs an action rather than a label is a **gate-failed**
meeting, which is stuck in staging because `promote_meeting` will not move a
folder that still holds WAVs. Its off-ramp cannot promote the meeting with its
audio — no WAV may ever reach the meetings folder — so *accept* means delete the
WAVs and then promote, irreversibly, behind a modal that says exactly that. The
verb is `referat promote <id> --release-audio`; without the flag `promote`
refuses and names the files, which is the whole safety of the bare form, and the
bare form is the retry for a promotion that failed to move the folder. Accepting
the transcript is what the flag means, so the meeting also stops being
`gate_failed` and becomes `transcribed` — the transcript was never what was in
doubt. The deletion itself is `transcribe.release_audio`, split out of
`release_audio_if_clean` so that the gate and the act are separable and
`audio_released` means one thing whoever wrote it.

Every row also carries a **Delete…** button, last and styled as danger, driving
`referat delete <id> --yes` behind a modal. The modal is the one place this
extension says something Python also says — it is shown *before* the command runs,
so there is no output yet to quote — and what it says is the pair of caveats from
the CLI: whether this deletion is real depends on whether the folder is synced,
and the voiceprints stay. A refusal still comes back through `mutate` unedited.

**The extension reimplements nothing.** It reads meetings from `referat list
--json` and makes every change through a verb: `label <id> --speaker <s> --name
<n>` and `list --json` were added for it at step 11, and step 15 gave it `tag`,
`untag`, `project add|rename|rm`, `state` and `promote --release-audio` to drive
as well. The two meeting roots, `format_duration`, `audio_state`,
`index.meeting_title`, `voices.unknown_speakers`, `voices.name_complaint`,
`projects.name_complaint`, `projects.slugify`, the lifecycle vocabulary and the
whole of `label.apply_name` stay in Python, where they already exist exactly once
and are shared by the CLI, the dashboard and the tray *so that they cannot
disagree*. Reading `meta.json` from TypeScript would have made the extension a
seventh reader of it with its own opinions about all seven. What the sidebar
shows on a refusal is therefore the sentence the CLI would have printed, passed
through unedited — `cli.ts`'s `mutate` exists to make that the only way a
mutation can fail.

It also has **no meetings-folder setting**, only `referat.repoRoot` and
`referat.claudeBinary`. Where meetings live is `[paths].meetings_dir` in the
repository's `config.toml` — the file the tray records against — and a second
place to say it is a second thing that can disagree with the recorder. That is
the mistake `voices_dir` and `format_duration` were each pulled back from
already.

**The tag picker reads the projects fresh, and diffs against the meeting.** Both
halves were bugs the first time somebody used it. It used to take the project map
out of the listing the sidebar already held, which saved a subprocess and went
stale the moment anything created a project without a refresh following — which
`editTags` itself did, on every path that returned early — so the picker offered
one of two projects. And creating a project inside the picker added its id to the
very set the picker was diffing against, so the new project looked like a tag the
meeting already carried, landed in neither `added` nor `removed`, and `referat
tag` was never called for it. Two sets now: what the meeting carries, which never
moves and is the baseline, and what is ticked, which grows on a create. Creating
also reopens the picker rather than re-rendering it, because assigning
`picker.items` makes VS Code recompute the ticked rows and setting `selectedItems`
on the next line races that.

**The extension reaches Python through the venv's own interpreter**, spawning
`<repoRoot>\.venv\Scripts\python.exe -m referat.cli` with the working directory
at the repository root. Not `uv run`, which Smart App Control blocks here, and
not `.venv\Scripts\referat.exe`, which Dropbox has deleted twice; the
interpreter is the one link that survives both. The working directory is
load-bearing rather than tidy, since `-m referat.cli` resolves only because the
repository root is on `sys.path` — the same dependency the autostart shortcut
carries.

`claude` is **not on `PATH` on this machine**: it ships inside the installed
Claude Code VS Code extension, whose directory name carries a version that
changes on every update. So the extension asks VS Code —
`extensions.getExtension("Anthropic.claude-code").extensionPath` plus
`resources/native-binary/` — which follows that extension across updates by
itself, and only then falls back to `PATH`. **The resolution happens at spawn
time and is never persisted**: a resolved absolute path stored anywhere would
still be there a week later, pointing at a directory that has been deleted, and
would fail silently at the moment somebody clicks *Generate notes*.

**The extension is packaged and installed** since step 12: `npm run package`
gives a `.vsix` of five files — `dist/extension.js`, `media/`, `package.json`,
`README.md`, `LICENSE` — and `code --install-extension` puts it in every window
instead of only in an F5 development host. `.vscodeignore` is written as `**`
plus negations rather than as a list of things to leave out, because an
exclusion list is right the day it is written and wrong the next time `media/`
gains a file. Source maps are tied to `--watch` for the same reason the payload
is: an esbuild map carries `sourcesContent`, so shipping one would put the
TypeScript back inside the package that bundling exists to keep it out of.

Installing changes exactly one thing about behaviour, and it is
**`referat.repoRoot`**. Left empty the extension searches the open workspace
folders and then falls back to the checkout its own bundle sits inside — which
is what lets F5 work in a host window opened on no folder, and which from
`~\.vscode\extensions` finds no `pyproject.toml`. So an installed build in a
window that does not have the repository open needs the setting, and the window
opened on the *meetings* folder is exactly that window. That is the fallback
behaving correctly, not a regression; do not "fix" it by teaching the extension
where meetings live, which is the second-place-to-say-it mistake this file
records `voices_dir` and `format_duration` being pulled back from.

## Per-project digests (build step 13)

Also unbuilt, and gated on step 10 rather than merely on 1-9. Its other gate,
**step 14**, is done: the projects file, the tag model and the lifecycle field
this step consumes all exist, and what remains here is the Google half — the
`digest` extra, `gdocs.py`, `digest.py`, the two `*-doc` verbs and `sync`. A
digest block is a meeting's `notes.md` translated into a Google Doc, and there is
no `notes.md` until `/cleanup` exists.

**A project is a label, not a container.** It is a thread of work spanning many
meetings — the thing `/cleanup` already writes `[[Wikilinks]]` for — and a
meeting carries **zero or more** of them, Gmail-style. *Untagged* is the computed
state of an empty tag list and is never itself a project. Each project carries
**zero or more** Google Doc references, and a meeting tagged with N projects is
written as a dated block into every doc of every one of them, each block found by
its own anchor. The baseline is that every doc gets identical `notes.md` content;
splitting it up is a later experiment (build step 17) and never a prerequisite.
The docs are the shareable artifact; the meetings folder stays local.

**Doc references belong to projects, never to meetings.** A meeting reaches a doc
only by carrying a tag whose project is linked to it. There is no per-meeting doc
picker and there should not be one — it would be a second way for a meeting to
reach a doc, and the first thing to disagree with the tags.

`<meetings_dir>/projects.json` holds the project list and the link state, owned
by `referat/projects.py` since step 14 — deliberately not by `config.py`, whose
promise never to write TOML back is about `config.toml` and stays true of it. It is JSON rather
than the TOML this file used to specify, which drops that tension instead of
managing it: it goes through the `paths.write_json_atomic` that already exists,
it is rewritten whole without apology, and it needs no header comment warning
that hand-written comments will not survive. Per project it holds an `id`, a
display `name`, a list of doc references, and a one-line `description` reserved
for step 17.

**The id is a slug fixed at creation and never changed by a rename**, which
changes the display name alone. That is the whole reason `meta.json` stores tag
ids rather than names: a rename touches one file, and no meeting record, no
transcript and no doc anchor is disturbed by it.

Each project also carries a **`glossary`**: the terms of art, product names and
people belonging to that thread of work. It is used **twice, at two different
times**. Every glossary is merged into the global hotword list, which acts
before any meeting has been tagged with anything; and once a meeting *is*
tagged, the glossaries of **all** its tags are the second list `/cleanup`
normalizes that meeting's notes against. Those two uses are why it lives here
rather than in `config.toml` beside `hotword_extras`, and why it must be readable
for a project with no docs attached — it is the one key in this file that
something outside step 13 depends on.

**Tagging is manual, and nothing is remembered on your behalf.** `referat tag
<meeting-id> <project-id>...` and `referat untag <meeting-id> <project-id>...`
write the `tags` array in `meta.json`; both are idempotent and both take several
ids at once. Nothing is inferred from the transcript and nothing is tagged at the
end of the pipeline: that is what keeps the untagged meetings a queue somebody
works through rather than a bucket of quiet mistakes. Re-routing is an `untag`
and a `tag`. The step-13 design this replaced had a single `project` key and a
remembered default that moved with every assignment; the default is gone, and
what stands in for it is the tray's on-stop toast offering recent projects and
defaulting to untagged when it times out.

**Deleting a project orphans its tags, visibly, and cascade-deletes nothing.**
`referat project rm` removes the project from `projects.json` and touches no
`meta.json`, no `notes.md` and no Google Doc. The ids left behind resolve to
nothing and are rendered as orphans rather than hidden, because a tag silently
disappearing off three meetings is how you lose track of what a meeting was
about.

**Linking is create-or-select, and the tab is the awkward half.** `referat
project link-doc <id>` *appends* a doc reference and `unlink-doc <id> <gdoc_id>`
removes one, since a project may carry several. *Create new
doc* calls `documents.create` titled `<Project> Meeting Digest` and writes into
that doc's default tab. *Select existing doc* searches Drive with `files.list`
filtered to Google Docs by name, then looks for a tab titled `Meetings` —
**tabs cannot be created through the API**, there is no `createTab` request, so
a doc without one is opened in the browser with an instruction to add it and a
re-check. Either way `gdoc_id` and `tab_id` are stored, and **every write is
located by that `tab_id`**: `documents.get` always passes
`includeTabsContent=True`, and every `batchUpdate` request carries `tabId` in
its `Location` or `Range`. A request without one silently targets the first tab,
which would write a meeting into somebody's unrelated notes with no error to
notice. Referat never writes into any other tab of a linked doc.

**Writing the doc is reconciliation, not appending.** Each block starts with an
anchor paragraph reading `[referat:2026-08-27_1400]`, small and gray; a block
runs from its anchor to the next one. `referat project sync` walks that
project's docs and, for each, reads the tab, diffs the anchors against every
meeting **whose `tags` contain this project**, inserts the missing ones in date
order, and re-renders any block whose `notes.md` has changed — judged by
comparing its `sha256` against the `digest.notes_sha256` recorded in `meta.json`
when the block was written. A block whose meeting no longer carries this tag is
reported and left alone; the doc may be shared and somebody may have written
around it, so pruning is an explicit flag. Because linking ends by running a
sync, linking an existing doc backfills it automatically.

Because a meeting now fans out, it can be **current in one doc and stale in
another**, which is why `meta.json`'s `digest` is keyed by `gdoc_id` rather than
being one flat object. A meeting reaches the `synced` lifecycle state only when
every doc of every one of its tags is current, and drops back to
`notes_written` the moment a `notes.md` sha stops matching.

Two mechanics that are easy to get wrong. Block operations are applied **in
reverse document order**, one `batchUpdate` each, because every insert and
delete shifts every index after it and working back to front keeps the indices
from the single `documents.get` valid. And a visible text anchor is used rather
than a Docs *named range*, which is the API's own mechanism and the fragile one:
named ranges are invisible to a person editing the doc, destroyed with their
content, and not carried by a copy of it. Deleting a text anchor merely makes
the reconciler re-append that block.

**No raw Markdown text may appear in the doc.** `referat/digest.py` translates
the constrained subset `/cleanup` emits — `##`/`###`, bold, italic, code,
one-level bullets, links, `[[Wikilinks]]` — into `batchUpdate` requests: one
`insertText` for the block's whole plain text, then style requests over spans
recorded while building it, which stay valid precisely because styling moves no
indices. Docs indices are **UTF-16 code units**, so offsets are
`len(s.encode("utf-16-le")) // 2` and one emoji in a note shifts everything
after it. `createParagraphBullets` converts existing paragraphs, so the inserted
text must not contain the `- ` itself. `[[Wikilinks]]` have no target outside
this machine and are rendered as bold text with the brackets stripped. The
translator is pure and imports nothing from Google, so the part carrying all the
index arithmetic is testable offline.

**Each block opens with a Heading 3 line, `YYYY-MM-DD — <title>`**, the title
being the H1 of `notes.md` falling back to the meeting id — the same rule as
`referat index`, sharing that helper. `notes.md`'s own `##` and `###` therefore
land as Heading 4 and Heading 5 beneath it. The Docs API cannot insert an
@-date smart chip; there is no request type for it. Keeping the date as a
fixed-width prefix in leading position is what would make swapping it for a chip
a one-request change to that line, leaving the ` — <title>` remainder alone, if
the API ever gains one.

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

[00:03:12] Niklas: ...

[00:03:40] Anna: ...

[00:04:05] SPEAKER_02: ...

[00:04:31] ME: ...
```

The owner is a name like any other; the trailing `ME` is a microphone line
nothing attributed, which is what that label means and all it means.

**One entry per block, separated by a blank line.** A single newline between them
is a *soft* break in Markdown and renders as a space, so a transcript written
that way came out of the preview as one unbroken paragraph hundreds of
utterances long — which is how these files are normally read, since the meetings
folder opens Markdown rendered. `transcribe.ENTRY_SEPARATOR` is the one place
that says so, shared by the renderer and by `referat reflow`, which puts the
blank lines into the transcripts written before it existed. Reflowing is not an
exception to the immutability rule below: it inserts whitespace *between* lines
and rewrites none of them.

`meta.json` keys: `id`, `started_at`, `ended_at`, `duration_seconds`, `status`
(the lifecycle, below), `pauses` (list of
`{start, end}` in elapsed seconds), `audio`, `transcription`, `tags` (the project
ids this meeting carries), `speaker_names`
(`SPEAKER_NN` to the name it was resolved to), `referat_version`. The per-channel
`speakers` block maps each `SPEAKER_NN` to its embedding, its snippet offsets,
the `name` it resolved to, and the `match` that decided — recorded even when it
was refused, because the near-misses are the only material for calibrating the
thresholds. `speaker_names` is the authority on who a label is; the per-channel
`name` follows it, and `referat label --forget` clears both.

**`status` is the meeting's lifecycle, and it is explicit rather than derived:**

```
recording -> recorded -> transcribing -> gate_failed | transcribed
                                                    -> notes_written -> synced
failed  (transcription raised — a different thing from gate_failed)
```

Every UI renders this field and **none of them infers a state from which files
exist**. That is the point of widening it: `gate_failed` used to be a three-way
inference — `status == done`, the WAVs still on disk, the folder still in staging
— computed nowhere, re-derived by every reader, and impossible to render
honestly. It is now a value the pipeline writes. `cli.audio_state` survives
unchanged, because whether the audio is still there is a real question in its own
right; what it stops doing is standing in for the lifecycle. The legacy values
map purely on load — `stopped` to `recorded`, `done` to `transcribed` — with no
look at the filesystem, since doing that inference in the loader would only hide
it. The one transition no pipeline can make is `notes_written`, because
`/cleanup` is forbidden from touching `meta.json`; whoever spawned it calls
`referat state <id> notes-written` afterwards, and that verb accepts no other
transition, and only from `transcribed`.

**The meetings already on this machine are not migrated**, deliberately. The
legacy values map on load, so every one of them reads correctly as `recorded` or
`transcribed`; the few that are really `gate_failed` keep saying `transcribed`
until their next `rerun` writes the truth. There were three of them, and a
migration would have been more code than the problem — and would have had to do
the filesystem inference this step exists to delete.

`tags` is the list of project ids this meeting carries — ids rather
than names so a rename touches one file, and absent or empty meaning untagged.
Step 13 adds `digest`, **keyed by `gdoc_id`**, each entry recording `tab_id`,
`notes_sha256` and `written_at`. Those are what make reconciliation and a
correction cheap: a sync needs `meta.json` and one `documents.get` per doc, and
never has to re-read the doc's prose to work out what changed. The folder
contract table above does not change — the digest lives in the docs and in these
keys, not in a new file.

**Speaker labels are the one editable field in a transcript.** The immutability
rule itself is in Conventions; this is its mechanical consequence for the file.
`referat label` may rewrite a label in either direction — a `SPEAKER_NN`
becoming `Anna`, or `Anna` reverting to `SPEAKER_NN` after `referat label
--forget Anna` — and it touches the label field alone, never the speech and
never the timestamp. Everything else that is wrong in a transcript stays wrong
there and is put right in `notes.md`.

The known-voices database lives beside the meetings, in `<meetings_dir>/.voices/`,
unless `[paths].voices_dir` moves it. It is biometric personal data about people
who never asked to be in it: it never leaves the machine, it is excluded from sync
and backups, the `/cleanup` pass is denied access to it, and `referat label
--forget <name>` deletes a person outright — embeddings gone, labels reverted
everywhere they appear.

**Recording never writes into the meetings folder.** A meeting is created in
`[paths].staging_dir` (`%LOCALAPPDATA%\Referat\recording` by default), recorded
there, transcribed there, and moved into `meetings_dir` by
`paths.move_meeting_dir` only once `release_audio_if_clean` has deleted the WAVs.
So the meetings folder only ever receives a meeting that is already audio-free,
and a sync client never sees a WAV at all.

That matters more than the bandwidth. Deleting a file inside a synced folder does
not delete it: Dropbox keeps deleted files and prior versions on its own servers
for weeks, so `audio_released` in `meta.json` would be a lie — recordings of
people who never asked to be recorded, retained off the machine after Referat
reported them gone. Uploading a WAV that is still being appended to is a second
hazard, against the rule that recording robustness beats everything else.

A meeting whose audio was *kept* — the quality gate refused the transcript — stays
in staging and is promoted by a later `referat rerun` that comes out clean.
Meetings therefore live in two places, and anything that lists them or resolves an
id must use `Config.meeting_roots()`; `referat list` marks the staged ones. Ids are
reserved in both roots at creation, so the move never renames a meeting.

**The meetings folder may be synced; the voiceprints may not.** Transcripts and
notes are worth having in Dropbox, and on this machine they are — so
`[paths].voices_dir` points the database somewhere local instead, resolved in one
place by `Config.voices_dir()`. That is a configuration answer rather than a
sync-client exclusion on `.voices/`, because an exclusion has to be re-applied by
hand every time the folder is recreated and fails silently when it is not.
Anything that needs the database must go through `Config.voices_dir()`; deriving
it from `meetings_dir` again would quietly put it back in Dropbox.

The meetings folder gets its own `CLAUDE.md`, seeded from
[templates/meetings/CLAUDE.md](templates/meetings/CLAUDE.md) along with the rest
of [templates/meetings/](templates/meetings/) on first run. Keep the two in sync
when the format changes — by hand, since nothing overwrites a seeded file.

## Conventions

- **Windows only.** No cross-platform abstraction layers, no `if sys.platform`
  branches for other OSes. WASAPI, `%LOCALAPPDATA%`, and Win32 calls are fair
  game and used directly.
- **One package**, `referat/`. Flat modules, no sub-packages.
- **Type hints throughout**, `from __future__ import annotations` at the top of
  every module.
- **Minimal dependencies.** Base install is tray + audio only; the ~3 GB
  transcription stack lives behind the `transcribe` extra. Step 16's WinRT toast
  library is the one deliberate exception in the base install, taken because
  `pystray` cannot raise an actionable notification and recorded as an exception
  rather than left to look like an inconsistency.
- **Nothing is inferred from a transcript.** No keyword rules, no guessing which
  project a meeting belongs to, nothing tagged at the end of the pipeline. That is
  what keeps the untagged meetings a queue somebody works through rather than a
  bucket of quiet mistakes. Step 17's notes-splitting experiment is the place this
  is most tempting and it does not bend there either: the human's tags are an
  *input* to the split, never an output of it.
- **No UI infers a meeting's state from which files exist.** `meta.json`'s
  `status` is the lifecycle, every surface renders that field, and a state that
  nothing writes is a state that does not exist.
- **Fully offline.** No telemetry, no cloud calls, no analytics. The only
  network access is downloading models on first use. Recording and transcription
  never leave the machine. There are exactly two deliberate exceptions, and both
  only run when the user asks: the cleanup pass, and step 13's digest push,
  which contacts Google only for projects that have been explicitly linked and
  only for meetings that have been explicitly tagged.
- **Nothing but notes leaves the machine.** The digest sends `notes.md` and
  nothing else — never `transcript.md`, never the audio, never `.voices/`, never
  a speaker embedding. This is its own rule rather than a detail of step 13,
  because it is the line somebody crosses by accident the first time a doc
  "should really have the exact quote".
- **The transcript is immutable; corrections live downstream.** Nothing may ever
  change a word of what was said in `transcript.md` — not `/cleanup`, not a
  person, not Referat. A misheard name, a mangled acronym, a turn split in the
  wrong place: every one of those is corrected in the *derived* artifacts,
  `notes.md` and the project digest, and never at the source. A transcript
  somebody has fixed is no longer evidence of what was said, and there is
  nothing left to check the correction against. Two things are not exceptions:
  the speaker **label** field is metadata that happens to live in that file and
  `referat label` rewrites it in both directions, and `referat rerun`
  regenerates the whole file from the audio, which is a different operation from
  editing it. Where a correction can be *prevented* instead — a name the model
  was never told exists — that is what the hotword list is for, and it acts
  before the transcript exists.
- **No web UI.** The tray icon and the VS Code extension are the only graphical
  surfaces this project has. No Flask, no FastAPI, no localhost dashboard, no
  browser front end — anything a person needs to look at or click is a tray menu
  item, a row in the extension's sidebar, or Markdown rendered in VS Code. The
  sidebar is a VS Code webview, which is part of the extension and is not a web
  UI; a localhost server would be, and there is none.
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

**Neither `uv` nor the interpreter it provisioned runs on this machine.** Smart
App Control blocks unsigned binaries whose cloud reputation does not vouch for
them, and it took uv 0.12.6 on 2026-08-31 after a week of working. It did not
stop there: the venv's own CPython was a python-build-standalone build, unsigned
file by file, and SAC went on to block `_ctypes.pyd` inside it — which kills
`status.py`, `tray.py` and `power.py`, and so every CLI subcommand, since
`cli.py` imports `status`. There is no exclusion list, so there is nothing to
allow.

The venv is therefore built on a **PSF-signed** Python 3.12 from the Python
install manager (`py install 3.12`), not on uv's. Same pinned version, different
provenance; signature is what SAC discriminates on.

**That fixes the fatal class and not the whole machine, and the difference
matters.** Measured after the migration: the base interpreter is 39 signed files
and no unsigned ones, and the venv's `python.exe` / `pythonw.exe` keep that
signature. Still unsigned are pip's console-script stubs (`referat.exe`,
`referat-tray.exe`) and the native DLLs in `site-packages` — torch alone is 26 of
38. Nothing in this project invokes the stubs, so the first costs nothing; the
second would cost *transcription* if SAC ever turned on it, which is a degraded
mode this codebase already has, rather than the total failure an unsigned
interpreter caused. **Wheels cannot be made signed** — PyPI does not Authenticode
sign, and there is no per-file allow — so the rule is to keep the *import-critical
path* signed and let everything else fail soft. When something dies with "An
Application Control policy has blocked this file", check the signature of the
file named, not the package that imported it.

Everything runs through that interpreter:

```powershell
.venv\Scripts\python.exe -m referat.cli --version
.venv\Scripts\python.exe -m referat.cli config     # show the loaded configuration
.venv\Scripts\python.exe -m referat.cli devices    # audio devices, and which ones [audio] selects
.venv\Scripts\python.exe -m pip install -e . --no-deps     # in place of `uv sync`
```

pip needs `--extra-index-url https://download.pytorch.org/whl/cu128` for the
transcribe extra, because the CUDA 12.8 wheels come from `[tool.uv.sources]`,
which only uv reads. SETUP.md section 2 has the whole procedure — including
rebuilding the venv onto a signed interpreter without re-downloading the ~5 GB of
wheels — and the repair for a `.venv` a sync client has eaten.

The `uv sync` / `uv run referat` forms in the git history and in SETUP.md are how
this is meant to work, and would work on a machine where SAC is off. Do not
reach for them here.

Configuration lives in repo-root `config.toml`, gitignored and created from
`config.example.toml` on first run. Override the location with `REFERAT_CONFIG`.
