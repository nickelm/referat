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
via `claude -p` from the VS Code extension. Notes for meetings assigned to a
project are pushed, on request, into that project's Google Doc digest.

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

`ME` is therefore no longer "the microphone channel" but **the owner, on
whichever channel their voiceprint was recognised**. A mic channel that could not
be diarized at all still falls back to it: one channel of unattributed speech
reads better as the owner than as nobody.

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
when a false accept costs the most, since it renders somebody else's words as
`ME`. With no runner-up the margin is required of the score itself. This is
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

`torchcodec`, which `pyannote.audio` pulls in, bundles FFmpeg the same way — but
it never gets the chance to load it. Pyannote reaches for a decoder only when
handed a *path*; handed `{"waveform": tensor, "sample_rate": int}` it decodes
nothing, which is the same trick already played on faster-whisper. Anything else
that ships FFmpeg should be fed decoded audio for the same reason.

**CLI** (`referat`), for the user, for Claude Code and — since step 11 — for the
VS Code extension: `list`, `rerun <id>`, `label <id>`, `status`, `devices`,
`index`, `project <verb>`. All of them are built except `project`, which arrives
with the digests at step 13.

**Two commands answer in JSON**, and only because the extension asks. `referat
list --json` is every meeting with its duration, status, audio state, title,
unnamed speakers and folder, across both roots; `referat label <id> --json` is
one meeting's unnamed speakers with their snippet paths and sample lines. Both
are the same functions the human-readable forms call, so the table, the
dashboard and the tree cannot drift apart. The tables themselves are unchanged
and stay the default.

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
duration, status, whether the WAVs are still on disk, and how many speakers are
still numbers waiting for `referat label`. `status` reads the tray's
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

Of the step 13 verbs, only `project link` and `project sync` need the `digest`
extra; `project list` and `project assign` are a TOML read and a `meta.json`
write. Only `rerun` needs the `transcribe` extra, and it imports it inside the
function. Everything else in the CLI is JSON reads and WAV playback: naming
somebody costs an embedding that was already computed during the pipeline, not
three gigabytes of resident torch. `rerun` also refuses while a live tray is
transcribing — `transcribe._RUN_LOCK` serializes jobs within one process and
cannot see across one, and two large-v3 models do not fit in 12 GB of VRAM.
`--force` overrides it.

## Notes and browsing (build steps 10-12)

**Step 10 is built**; 11 and 12 are not. The scaffold, the `/cleanup` prompt and
`referat index` exist and have been driven on a real transcript. What is still
open is the tuning the gate was really about: the prompt has met one meeting, and
one held in person at that, so it has never been read against a call with
diarized speakers in it.

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

**VS Code is the browsing layer**, with a `referat-vscode/` extension: a
meetings TreeView, and per-meeting commands that shell out to the `claude`
binary (*Generate notes*) and to `referat rerun` (*Re-transcribe*). At step 13
it also grows a Projects section — **there is no web UI in this project and
never will be**, so the extension is where every graphical surface above the
tray icon lives. A VS Code webview is part of the extension and is not a web UI;
a localhost server would be, and there is none.

**The extension reimplements nothing.** It reads meetings from `referat list
--json` and names speakers through `referat label <id> --speaker <s> --name
<n>`, both added for it at step 11. The two meeting roots, `format_duration`,
`audio_state`, `index.meeting_title`, `voices.unknown_speakers`,
`voices.name_complaint` and the whole of `label.apply_name` stay in Python,
where they already exist exactly once and are shared by the CLI, the dashboard
and the tray *so that they cannot disagree*. Reading `meta.json` from TypeScript
would have made the extension a seventh reader of it with its own opinions about
all seven. Step 13's Projects section shells out to `referat project` for the
same reason.

It also has **no meetings-folder setting**, only `referat.repoRoot` and
`referat.claudeBinary`. Where meetings live is `[paths].meetings_dir` in the
repository's `config.toml` — the file the tray records against — and a second
place to say it is a second thing that can disagree with the recorder. That is
the mistake `voices_dir` and `format_duration` were each pulled back from
already.

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

## Per-project digests (build step 13)

Also unbuilt, and gated on step 10 rather than merely on 1-9: a digest block is
a meeting's `notes.md` translated into a Google Doc, and there is no `notes.md`
until `/cleanup` exists.

A *project* is a thread of work spanning many meetings — the thing `/cleanup`
already writes `[[Wikilinks]]` for. Each project may be linked to one Google
Doc, its digest, into which every meeting assigned to it is written as a dated
block in chronological order. The doc is the shareable artifact; the meetings
folder stays local. `<meetings_dir>/projects.toml` holds the project list and
the link state, owned by `referat/projects.py` — deliberately not by
`config.py`, whose promise never to write TOML back is about `config.toml` and
stays true of it. `projects.toml` is machine-written and rewritten whole, and
says so in a header comment.

**Assignment is manual, with a remembered default.** `referat project assign
<meeting-id> [<project>]` writes `project` into `meta.json`, defaulting to
whichever project was assigned last. Nothing is inferred from the transcript and
nothing is assigned at the end of the pipeline: that is what keeps the
unassigned meetings a queue somebody works through rather than a bucket of quiet
mistakes. Re-routing is the same command run again.

**Linking is create-or-select, and the tab is the awkward half.** *Create new
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
runs from its anchor to the next one. `referat project sync` reads the tab,
diffs the anchors against every meeting assigned to the project, inserts the
missing ones in date order, and re-renders any block whose `notes.md` has
changed — judged by comparing its `sha256` against the `digest.notes_sha256`
recorded in `meta.json` when the block was written. A block whose meeting is no
longer assigned is reported and left alone; the doc may be shared and somebody
may have written around it, so pruning is an explicit flag. Because linking ends
by running a sync, linking an existing doc backfills it automatically.

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

[00:03:12] ME: ...
[00:03:40] Anna: ...
[00:04:05] SPEAKER_02: ...
```

`meta.json` keys: `id`, `started_at`, `ended_at`, `duration_seconds`, `status`
(`recording | stopped | transcribing | done | failed`), `pauses` (list of
`{start, end}` in elapsed seconds), `audio`, `transcription`, `speaker_names`
(`SPEAKER_NN` to the name it was resolved to), `referat_version`. The per-channel
`speakers` block maps each `SPEAKER_NN` to its embedding, its snippet offsets,
the `name` it resolved to, and the `match` that decided — recorded even when it
was refused, because the near-misses are the only material for calibrating the
thresholds. `speaker_names` is the authority on who a label is; the per-channel
`name` follows it, and `referat label --forget` clears both.

Step 13 adds two more: `project`, the slug of the project this meeting is
assigned to, absent when it is unassigned; and `digest`, recording the last
write into that project's Google Doc — `gdoc_id`, `tab_id`, `notes_sha256` and
`written_at`. Those four are what make reconciliation and a correction cheap: a
sync needs `meta.json` and one `documents.get`, and never has to re-read the
doc's prose to work out what changed. The folder contract table above does not
change — the digest lives in the doc and in these keys, not in a new file.

**The transcript's speech is immutable; its speaker labels are not.** Nothing may
ever change a word of what was said in `transcript.md` — not `/cleanup`, not a
person, not Referat. Speaker labels are metadata that happens to live in that
file, and `referat label` may rewrite them: a `SPEAKER_NN` becoming `Anna`, or
`Anna` reverting to `SPEAKER_NN` after `referat label --forget Anna`. That is the
only in-place edit anything is allowed to make to a transcript, and it touches
the label field alone. (`referat rerun` regenerates the file from the audio,
which is a different thing.)

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
  transcription stack lives behind the `transcribe` extra.
- **Fully offline.** No telemetry, no cloud calls, no analytics. The only
  network access is downloading models on first use. Recording and transcription
  never leave the machine. There are exactly two deliberate exceptions, and both
  only run when the user asks: the cleanup pass, and step 13's digest push,
  which contacts Google only for projects that have been explicitly linked and
  only for meetings that have been explicitly assigned.
- **Nothing but notes leaves the machine.** The digest sends `notes.md` and
  nothing else — never `transcript.md`, never the audio, never `.voices/`, never
  a speaker embedding. This is its own rule rather than a detail of step 13,
  because it is the line somebody crosses by accident the first time a doc
  "should really have the exact quote".
- **No web UI.** The tray icon and the VS Code extension are the only graphical
  surfaces this project has. No Flask, no FastAPI, no localhost dashboard, no
  browser front end — anything a person needs to look at or click is a tray menu
  item, a node in the extension's TreeView, or Markdown rendered in VS Code.
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
