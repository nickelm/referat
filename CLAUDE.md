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
Markdown transcripts into a meetings folder. Its graphical surfaces are the tray
icon and a **command center** the tray owns and opens — **no web server and no
Electron, ever**, a rule stated exactly in Conventions. A VS Code extension was
the primary UI from step 15 to step 20 and was deleted at step 23; there is one
graphical surface again. There is **no automatic
LLM cleanup pass**: notes are generated lazily by the user, through a `/cleanup`
slash command run by Claude Code in the meetings folder — interactively, or
headless via `claude -p` from the command center. Notes for a meeting tagged with
a project are pushed, on request, into that project's Google Doc digests.

Target machine: Asus Zephyrus G14, Windows 11, RTX 5070 Ti Laptop GPU
(Blackwell / sm_120, needs CUDA 12.8 builds of PyTorch). Python 3.12, managed by
`uv` — the system Python 3.14 is too new for the torch / CTranslate2 / pyannote
wheels.

## Architecture

**Tray app** — a `QSystemTrayIcon` (gray idle, red recording, yellow paused,
distinct transcribing state, and hollowed into a ring for a run that lost a
channel), two global hotkeys via the `keyboard` library emitted by programmable
USB buttons, and a state machine:

```
idle -> recording -> (paused <-> recording) -> stopped -> transcribing -> idle
```

This is the *recorder's* state, deliberately not the meeting's — the meeting's
lifecycle lives in `meta.json`'s `status` and has no `paused`, because a paused
meeting is still being recorded.

**The tray is two halves and the seam is deliberate.** `referat/tray.py` is the
recorder core — the machine, the hotkeys, the recorder, the sleep hold, the
transcription jobs and `status.json` — and it imports no toolkit at all;
`referat/ui/shell.py` is the icon, the menu and the event loop. `main` starts the
core first and imports Qt second, so the rule that *the recorder comes up before
the window* is a fact about the order of four lines rather than an intention. The
one seam between them is `App.notify`, which the shell replaces with a real
balloon and which logs when nothing has. Transitions arrive on the `keyboard`
hook thread and on transcription threads, so the shell reaches Qt through a
queued signal (`ui.shell.Bridge`) and registers its listener **last** — after the
sleep hold's and the status write's, which `Machine._notify` therefore runs
first and which no Qt failure can cost.

**Everything that reaches Qt from another thread goes through `Bridge`, and
that is the rule this project paid for.** Transitions did from the start;
`App.notify` did not, and `Shell.notify` ran `tray.showMessage` *and a full
`window.refresh()`* — a rebuild of every row in the meetings tree — on the
`transcribe` daemon thread, at the end of every single job. It killed the tray
three times before anybody caught it: `0xc0000374`, **STATUS_HEAP_CORRUPTION**,
in `ntdll`, a couple of seconds after a transcript finished, on 2026-09-02 twice
and on 2026-09-03 once. There was no Python traceback in the log because there
was no Python exception — the log shows a clean, successful transcription and
then simply stops, which is the signature to recognise. The transcript was never
at risk, since it is written, released and promoted before `notify` runs; the
*recorder* was, because a dead tray records no meeting. It arrived with Qt at
step 20's phase 1 and hid until the window had been opened, because with no
window there is nothing but the balloon to get wrong. So: **`Bridge` now carries
transitions, notifications and progress**, and the general form is that a
`0xc0000374` with no traceback means a C++ object was destroyed on the wrong
thread.

`gpu.release` is the same hazard from the other side and is guarded the same
way. `gc.collect()` destroys whatever it reaps *on the thread that called it*,
including the C++ half of an unreachable PySide6 widget, and the tray calls that
function from the transcription thread — so it collects only on the main thread
now. Skipping it costs the reference cycles and the frames of a propagating
traceback and nothing else: `empty_cache` still returns everything the preceding
`del` made unreachable, which is the overwhelming majority and the reason that
module exists. A CLI `rerun` is on the main thread and still collects.

**The tray does not tag meetings, and step 16 is declined.** This file used to
describe an on-stop toast asking which project(s) and a lazily built *Tag
recent…* submenu, in the present tense, as though both existed. Neither was ever
built: every box under step 16 in `TODO.md` is unchecked, there is no toast code,
and the WinRT notification library it would have needed is not in
`pyproject.toml`. Somebody reading this file would have gone looking for a
feature that was never there — which is the same failure as a comment claiming a
resource was released, one section further out.

It is declined rather than merely unbuilt. Tagging happens when meetings are
looked over at a spare moment, on the command center's Meetings tab, where the
picker and the untagged inbox already are; a prompt at the end of every recording
answers a question nobody was asking at that moment. **So the WinRT dependency is
not taken**, and the deliberate exception to the minimal-dependencies rule that
step 16 would have required is not spent — leaving PySide6 as the only one. What
step 16 was really for is covered: the untagged queue on the dashboard is the
thing that makes sure a meeting does not stay untagged.

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

**The two channels are not disjoint, and `referat/bleed.py` is what makes them
behave as if they were.** When a call's audio comes out of a loudspeaker standing
in the same room as the microphone — a television over HDMI, on 2026-09-02 —
every remote utterance is recorded twice, and because both channels are diarized
and both get labeled by hand, twice under *one name*. That is what makes it
invisible rather than obvious: `2026-09-02_1059` has 1239 lines under a remote
speaker's name against 478 loopback segments, so roughly **761 of them are the
microphone's second copy**. TODO.md predicted this and declined to act until it
was observed; it now has been.

**Suppression is one-directional, and the reason is not audio quality.** The
loopback copy is better — it is a tap on the render mix before the DAC — but what
decides is that a conferencing application does not loop your own capture back
into what it plays, so **speech from the room can only ever exist on the
microphone**. Dropping a microphone segment risks a second copy; dropping a
loopback segment risks the only copy of the far end. So microphone segments may
go and loopback segments never do, which `bleed.apply` states as an invariant it
has no code path to violate.

Two mechanisms answering different questions. The **cluster rule** convicts a
microphone cluster whose speech time sits under loopback speech *and* whose text
the loopback matches — both, because a room microphone is voiced almost
continuously, so time alone convicts anybody who talks *over* the far end, while
text alone misses a cluster that is mostly "Yeah." The **per-segment rule** is
the complement, for echo that diarization filed under a room person instead. Below
`[bleed].min_tokens` nothing is ever dropped by text: more than half of a hybrid
meeting is backchannels, and nothing distinguishes a remote person's second "Yeah"
from the microphone's copy of their first — those go only when the cluster rule
takes the whole cluster, which decides by *whose voice they are*.

**A refusal has to hold at both levels or it is not a refusal.** Two clusters are
spared rather than merely cleared — the owner's, and every cluster when the rule
convicted the entire channel, which is likelier to be a bug than a meeting — and
`spared` exists as a separate flag because the per-segment rule was measured
undoing both refusals one line at a time. A cleared cluster stays eligible; a
spared one is untouchable.

An echo cluster is also **never offered a name**. `voices.unknown_speakers` skips
it and `label.apply_name` refuses it outright, the second because
`--speaker`/`--name` reaches that function directly and asks nothing about who is
unknown. This is the second cost the same TODO item predicted, and it predicted
the right outcome by the wrong mechanism: the pipeline never writes to the
voices database — `bootstrap_owner`'s one-cluster gate worked exactly as designed
and refused — so the two poisoned voiceprints arrived through `referat label`,
from a person correctly naming a cluster that should never have been shown to
them. **The defence belongs where a human is offered a cluster, not where the
pipeline matches one.**

Prevention is a routing habit and cannot be a config knob. `[audio].loopback_device`
stays empty: it decides where Referat *listens*, not where sound *comes out*, so
pinning it to a headset while the call plays through a television records silence
and loses the far end entirely — a worse failure than the one it would be
mitigating. SETUP.md section 4a is the habit, and `transcription.bleed` in
`meta.json` is how you find out the habit lapsed.

**Sleep prevention** — hold `SetThreadExecutionState(ES_CONTINUOUS |
ES_SYSTEM_REQUIRED)` through `ctypes` while the recorder is in any state but
idle, and while a notes pass is running. This file used to say *transcription
runs without the hold*, on the reasoning that a job outliving its meeting is no
reason to keep the laptop awake; that priced the cost correctly and never priced
the hazard. On 2026-09-03 a meeting stopped at 15:26, diarization began at 15:29,
and the machine entered Modern Standby at 16:01 with the job still in flight.

The hold is **keyed by reason** because it has two owners — the recorder while
capturing and the notes queue while `claude` runs, a cleanup pass being exactly
the idle timer's case: a subprocess thinking for minutes with nobody touching the
keyboard. A single boolean let whichever finished first drop the hold out from
under the other.

**None of it survives the lid closing**, and nothing in this process can:
`ES_SYSTEM_REQUIRED` suppresses the *idle* timer, and the lid is a power-plan
action. So two things cover what happens anyway. `power.SuspendWatcher` detects a
suspend from the wall clock — a tick that should have taken twenty seconds and
took forty minutes — and records how much time was lost and what was in flight,
plus a heartbeat every five minutes while a job runs, because *a log that simply
stops* is this codebase's most expensive signature and a suspend drew the same
picture as the heap corruption. And `meeting.reconcile_interrupted` clamps a
meeting left `transcribing` by a process that is gone, which is the sticky-lie
gap recorded below, closed from the next start rather than from a handler.

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

**Both models are dropped when the job ends, and dropping them is not what frees
the card.** `transcribe_channels` and `diarize._run` have always ended with a
`del`, and both said in their docstrings that this was so a resident large-v3
would not occupy the GPU for the rest of the session. It did anyway: `del`
returns the *Python reference*, while torch's caching allocator keeps every block
it has taken from the driver so the next allocation of that size is free. Nothing
but `torch.cuda.empty_cache()` gives the arena back, and nothing was calling it —
measured on 2026-09-02, an idle tray holding **8484 MiB of 12227** and a `referat
rerun` in a second process stalling inside `ctranslate2.models.Whisper(...)` with
what was left. `referat/gpu.py` is the fix and it is one function, called from
each `finally` that has just dropped a model and last from the tray's own job
thread — outside its `except`, because a release running while an exception
propagates is partial: the traceback holds the frames that hold the tensors, and
only `gc.collect()` reaches that cycle. It reclaims **torch's** share and says so
in its log line: CTranslate2 allocates outside torch entirely, and the CUDA
context lives as long as the process does.

**And a second, larger measurement on 2026-09-03 says the accounting is still
not complete.** The Whisper weights are CTranslate2's, so `gpu.release` never
sees them; a tray *idle between jobs* was measured holding **5205 MiB of 12227**
while this function reported *released 0 MiB*, and the next job reached **11424
MiB dedicated with 468 MiB of non-local usage** — memory the WDDM driver had
migrated to system RAM, because Windows does not fail an oversubscribed
allocation, it pages the overflow across PCIe. `nvidia-smi` read 100% busy while
diarization ran **6527.8s against 58.5s** for the previous meeting of the same
length. Whisper measures 3788 MiB and pyannote peaks near 3866, so the
arithmetic says a finished job left its model resident and the next loaded a
second.

**Why it was retained is not established, and the guess should not be written
down as if it were.** In a standalone process it does not reproduce: `del` alone
returns the full 3788 MiB with the generator drained and cyclic GC disabled, and
the floor is flat across three consecutive jobs, pyannote on a worker thread
included. So the difference is something about the tray — Qt in the process, the
skipped `gc.collect()`, or neither. `transcribe.unload_model` is the fix
precisely because it does not depend on the answer: CTranslate2's own API, called
before the `del`, on whatever thread asks. **Between the two functions both
allocators are covered, and neither covers the other's.**

**The standing structural risk is peak, not residue.** `transcribe_channels`
loads one model for both channels, so Whisper stays resident through each
channel's diarization — 3788 MiB alongside pyannote's 3866, before the CUDA
contexts and before whatever the desktop compositor is holding, which was
measured at 1761 MiB. That is most of a 12 GB card by design, and it is why
anything else on the card tips this into paging.

The target is room for a second model,The target is room for a second model,
not zero. This is the same shape of lesson as the Smart App Control paragraphs
below — a documented intention that the code did not deliver — and the general
form is that *a comment claiming a resource was released is worth measuring once*.

**The language is chosen per channel, out of a named set, and that set is the
whole design.** `[transcription].languages` is one key with three meanings: one
code pins it and asks Whisper nothing, two or more run a detection pass per
channel and take the best *of these*, `[]` is bare autodetect. It is `["en",
"sv"]` here since 2026-09-04, when the first Swedish meeting met a pipeline that
had pinned English since step 1.

**The restriction is applied to the ranking and not to the decoder**, because
faster-whisper has no argument for "one of these" — `language` takes exactly one
code. So `transcribe.detect_language` runs the pass for its
`all_language_probs`, reads the allowed codes out of that ranking, and hands the
winner back as a pin; Whisper is never told Norwegian was a candidate. That is
worth the code because a language is chosen **once per channel** and every
segment is decoded under it: Swedish sits among Norwegian, Danish, German and
Dutch, so an unrestricted guess that lands one country over does not cost a word,
it costs the half hour. A channel scoring `no` at 0.90 against `sv` at 0.05 is
still transcribed as Swedish, which is the entire point of naming the set.

Five windows spread across the channel rather than faster-whisper's one at the
front, which is the wrong end of a meeting — somebody sitting down, and on an
in-person loopback, silence. **Per channel and never per segment**: the
microphone is the room and the loopback is the far end, while `multilingual=True`
re-detects on every segment and turns one wrong guess per channel into one wrong
guess anywhere. Every failure degrades to unrestricted autodetect, the rule
diarization runs under. Unknown codes are dropped with a warning — Swedish is
`sv` and not `se`, and a typo that quietly narrowed the set to nothing would come
back as bare autodetect, which is this feature's own failure mode wearing the
face of the fix. Membership is checked against the loaded model in
`transcribe.py` and never in `config.py`, which must not import faster-whisper
because `referat config` runs in the base install.

**Each window is decoded without the previous one's text, and that is a
correction rather than a default.** faster-whisper conditions every 30-second
window on the text of the one before it; on 2026-09-04 that cost
`2026-09-04_1001` **twelve of its thirty-two minutes**. Twenty-six lines of
`Ljusen.` were handed forward as context, found plausible, and became *Tack för
att du har tittat på den här videon!* -- YouTube subtitle boilerplate, Whisper's
best-known Swedish hallucination -- repeated once every thirty seconds until
00:24:50. **A repetition loop is not a bad window, it is a bad window feeding
itself**, and `condition_on_previous_text` is the feed. The stretch decoded in
isolation gives 2128 words of ordinary conversation, and it measures -33.8 dBFS
against -34.0 for the rest of the meeting: not the silence case anybody would
forgive, but two people talking, replaced with one sentence.

Referat gives up less than most by turning it off. What conditioning buys is
prose coherence across a window boundary, and a transcript here is diarized,
per-segment, relabeled, merged with a second channel and read beside timestamps
rather than as paragraphs; the vocabulary priming is already bought by the
hotword list, which acts on **every** window rather than only after somebody has
already said the word. It is not a knob, because there is no meeting for which
the loop is the better outcome.

**The quality gate is the backstop and was never the fix**, and it is the only
reason this was found: `compression_ratio` 8.11 against a 2.4 ceiling refused the
transcript, kept the audio and made the repair possible. It caught this one by
luck of magnitude, though -- the gate compares the worst segment's repetition
against a ceiling and never compares transcribed speech against `voiced_seconds`,
so 1874s of segments over 1869s of voiced audio looked perfect while a third of
it was one sentence.

**Hotwords are the only correction that happens before the transcript exists.**
Every transcription is handed one global list through faster-whisper's
`hotwords`, so a name the model has been told about is heard right the first
time rather than corrected afterwards. `referat/hotwords.py` merges it from
three sources: every name in the known-voices database, every project's
`glossary` in `projects.json` — edited since step 20's phase 4 by `referat
project glossary` and by the command center's projects page, which is what makes
that page the hotword surface — and `[transcription].hotword_extras` in
`config.toml`, which stays hand-edited because `config.py` promises never to
write that file back. It is **one list for the machine and never one per project**,
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
is dropped is logged rather than dropped quietly. `referat hotwords` prints the
merged list with the source of each term and names what the cap dropped.

**The cap counts tokens by estimate, and the estimate is measured.** There is no
tokenizer to ask: `hotwords.py` must run without the `transcribe` extra, because
nobody should need three gigabytes of resident torch to see what Whisper will be
told. So a term is assumed to cost `ceil(len / 2) + 2` tokens, which errs
upwards on purpose — over-estimating drops a term off the bottom of a fixed
priority order and says so, while under-estimating hands faster-whisper a list
it slices at token 223, mid-name, silently. `ceil(len / 3) + 1` was written
first and was **30% under** on 40 hyphenated `Synthetic-Term-003`-shaped
strings, measured against the real large-v3 tokenizer; the rule that replaced it
came out between 1.10x and 2.04x the true count across eight lists and never
once under. The budget itself is read out of faster-whisper rather than
inferred: `generate_with_fallback` slices at `max_length // 2 - 1` with
`max_length = 448`, so it is 223.

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

**`[transcription].keep_audio` is the one exception, and it costs the deletion
rather than the verdict.** Off by default, because the WAVs are recordings of
people who never asked to be recorded and `audio_released` is a promise; on, the
pipeline keeps them even when the gate accepts the transcript. It exists because
the audio is also the only material that can calibrate `[speakers]`'
`match_threshold` and `match_margin` against real voices, and once it is gone no
`rerun` brings it back. The gate is therefore **asked first and
unconditionally** — `transcribe.audio_is_clean` is the verdict, split out of
`release_audio_if_clean`, which still pairs it with `release_audio` — so a
garbled transcript is still `gate_failed` whether or not somebody asked to keep
the audio. A meeting kept on request is not `gate_failed`: it stays
`transcribed`, records `transcription.audio_kept` in `meta.json` saying why, and
stays **in staging**, because `promote_meeting` refuses a folder holding WAVs and
no WAV may ever reach the meetings folder. `referat promote <id>
--release-audio` is its off-ramp. The distinction matters to two messages that
used to assume kept audio meant a failed gate: `referat list`'s footer and
`promote`'s bare refusal both split on `audio_kept`, offering a `rerun` for the
meetings the gate refused and `promote --release-audio` for the ones kept on
purpose.

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

**Qt is the fourth instance, it was the first where a block would have cost a
recording, and it is the one the audit cleared.** PyAV, torchcodec and scipy each
threatened a transcript and each degrades; step 20 put PySide6 in the tray
process, which puts native code in front of the recorder itself, and a block
there means the tray does not start and **no meeting is recorded** — a total
failure, which is the thing this codebase does not otherwise have. So it was
gated on measurement rather than on reasoning, the way the venv migration was:
**all 247 native files under `PySide6/` and `shiboken6/` are Authenticode-signed
by The Qt Company Oy**, timestamped, on a DigiCert chain valid to 2028, with
nothing unsigned. Compare torch, which is 26 unsigned DLLs out of 38, and scipy,
all 106 of whose `.pyd` files are unsigned. Qt is by a wide margin the
best-signed native code in this venv, and putting it on the recording path
lowered that path's exposure rather than raising it.

Two things follow and neither is cancelled by a clean audit. The scipy rule is
still applied one layer earlier — **the recorder, the hotkeys, the state machine
and `status.json` come up first and independently, and the window is opened
after**, a window that fails to open being a log line and a tray that still
records, the same degradation diarization and the step 16 toast run under. And
**QtWebEngine stays out**: it lives in `PySide6-Addons`, is 168 MB of wheel
against Essentials' 77, and spawns a sandboxed Chromium helper process — a second
process on the recording path buying nothing `QTextBrowser` does not already do.
The escape hatch, unused: put the icon back on `pystray` and make the window a
child process that shells out to the CLI.

**The measurement is the reusable part, not the verdict.** A wheel is signed or
not by whoever built it, so the audit is `Get-AuthenticodeSignature` over every
`.dll`, `.pyd` and `.exe` in the package, grouped by `Status` — and it is worth
re-running after a PySide6 upgrade, because nothing guarantees the next release
is signed the way this one is.

**CLI** (`referat`), for the user, for Claude Code, and for the command center,
which reads its documents by calling the same functions rather than by spawning:
`config`, `list`, `show <id>`,
`transcript <id>`, `rerun <id>`,
`label <id>`, `status`,
`devices`, `hotwords`, `people`, `person rename <id>`, `actions [<verb>]`, `day [<date>]`, `notes`, `index`,
`project <verb>`, `tag`, `untag`, `state`, `recap <project-id>`,
`promote <id>`,
`reflow [<id>]`, `relabel [<id>]`, `debleed [<id>]`, `denoise <id> --speaker
<label>`, `delete <id>`. **All of
them are built**, `project link-doc`, `project unlink-doc` and `project sync`
included since step 13 — they were deliberately absent from the parser until
then rather than present and answering "not built yet".

The project verbs are `add`, `rename`, `describe`, `glossary`, `archive`,
`unarchive`, `rm`, `link-doc`, `unlink-doc`, `sync`, `auto-sync` and `list`, and **each
mutating one is a line of dispatch onto a guarded function** — `create_project`, `rename_project`, `set_description`,
`set_glossary`, `set_archived`, `remove_project` — which the command center's
projects page calls too. `archive` and `unarchive` are **one** guarded function
taking a direction rather than two, which is what `apply_tags` and
`set_action_done` already are: two would be two copies of the same guard, mutate,
save and report differing in one boolean. `describe`
and `glossary` arrived at phase 4 as its prerequisite: the CLI owns every
mutation, so nothing could edit a glossary from a window until something could
edit one at all. `glossary --add` and `--remove` are ergonomics over a
**whole-list replacement**, both reading the current list and handing the whole
of it back, which is deliberately the opposite of `apply_tags` — a tag picker
renders a subset of the projects, so a replacement there could drop a tag it
never drew, while a glossary is edited as the whole list.

`reflow`, `relabel`, `debleed` and `denoise` are the four repairs. The first
three are the same shape: all exist because a rendering rule changed after
transcripts had already been written whose audio has since been released, so no
`rerun` can regenerate them. All four write nothing when there is nothing to
change. `reflow` and `relabel`
are idempotent; **`debleed` is convergent instead**, and that is a real
difference rather than a quibble — its pairing is greedy and disjoint, so
removing one line can leave two others adjacent that were not before. On
`2026-09-02_1059` the passes go 66, then 1, then none. Which is why
`transcription.debleed.removed` **accumulates across passes rather than being
replaced**: the first draft overwrote it, and a second pass left 67 lines gone
from the file and 1 recorded — precisely the unreviewable deletion the record
exists to prevent.

`reflow` and `relabel` are not exceptions to the immutability rule — `reflow`
inserts whitespace *between* lines and `relabel` rewrites the label field alone,
through the same `label.relabel_transcript` that `referat label` uses.

**`debleed` is a genuine exception and is treated as one.** It deletes whole
entries, which is larger than either, and filing it under their exemption would
be dishonest. The argument for it is that an echo copy is not a second utterance
but a second *recording* of one — which Referat already asserts every time it
merges, since `merge.py` puts both channels on one timeline on the claim that the
same position in each WAV is the same moment. The argument against is the rule's
own stated reason, checkability: a reflow is verifiable by eye because every word
is still there, while a deletion removes the evidence that the deleted line was a
duplicate. So the resolution is neither to delete freely nor to refuse: **it
deletes only where the removal stays checkable**, recording every removed entry
under `transcription.debleed.removed` with the line kept in its place, and writing
`meta.json` before the transcript so an interruption leaves a record of a removal
that did not happen rather than a removal with no record. Same move
`speaker_names` makes for `--forget`.

It is dry by default, gated on a name resolving to clusters on **both** channels —
the one piece of provenance a rendered transcript still has, since `meta.json`
records which cluster came out of which channel — and it keeps whichever copy
*contains* the other. Deliberately not the better-punctuated one, which is the
obvious rule and was measured across 67 pairs at 24 better, 20 worse, 23 tied: a
coin flip. Containment is the only tie-break that cannot lose a word.

**What it cannot do is the reason `2026-09-02_1059` was left alone.** A rendered
line does not say which channel it came from, and no `meta.json` key records
per-segment provenance, so the repair has only text and time; against ~761 echo
lines it reaches 66, because 764 of the two-channel entries are under
`min_tokens` and permanently ineligible. Removing 66 of 761 leaves a file that is
still mostly duplicated but *looks* deduplicated, and a reader who sees no obvious
repeats stops looking for them. That meeting's correction belongs in its
`notes.md`, which is where the immutability rule points corrections anyway. The
command still earns its place: the dry run is the only way to see the problem in a
rendered file, and the next such transcript may have a far better ratio.

**`denoise` is the fourth repair and the second exception, built at step 20b on
2026-09-04.** A room microphone hears the corridor, a door and the meeting next
door, and diarization gives that a `SPEAKER_NN` like anybody else — `2026-09-03`
had one — so until then the only way out of the *Speakers nobody has named* queue
for a cluster nobody will ever name was to give it a name, which files a
voiceprint of a door. `referat denoise <id> --speaker SPEAKER_NN [--apply]` marks
one cluster as **not a person**: it is never offered a name again, no voiceprint
is filed, its snippets go, and every line under that label is removed from
`transcript.md`. It inherits `debleed`'s four conditions rather than re-arguing
them — dry by default, `meta.json` first with every removed line kept verbatim
under `transcription.noise`, accumulating across passes, a repair beside the
other three — and `referat/noise.py` is where they live.

**Where it differs from `debleed` is the part to hold onto: there is no evidence.**
`debleed` decides by coverage in time and text against the other channel and
refuses when the evidence is thin; nothing in the data distinguishes a quiet
neighbour from a quiet participant, so **a person marks it and the machine never
infers it** — no heuristic, no "clusters under N seconds", and there must never
be one, because the cost of being wrong is deleting somebody's actual words.
That is *nothing is inferred from a transcript* applied to the one operation
where a shortcut would be most tempting. It follows that the judgement is made by
listening, and the snippets are how: they survive exactly as long as a cluster is
unnamed, so the command center's Speakers dialog carries the button — *Not a
person: mark as noise…*, behind a modal quoting the same `noise.warning` the CLI
prints as its dry run — and the verb at the prompt is the same `noise.mark_noise`
printed.

**Two flags, not one.** `Cluster.noise` sits beside `Cluster.echo` rather than
widening it: `echo` means something specific and true — *this is the loopback
coming back* — and was reached by measurement, while `noise` is a human
judgement, and a later reader must be able to tell which was made. They share
the defence and nothing else: `voices.unknown_speakers` skips both and
`label.apply_name` refuses both. **A named cluster is refused**, since that is a
person somebody identified and marking them noise would delete a recognised
person's words — `label --forget <name>` comes first. And it is per meeting,
always, because `SPEAKER_NN` numbering is per meeting and dismissing *a number*
globally would silence a different person next week. `meta.json` is written
through `write_json_atomic` directly rather than `Meeting.save`, which never
raises: that is right for a recording, where the audio is the part that cannot
be reconstructed, and wrong here, where the record *is* the justification for
the deletion — a record that failed to land refuses the deletion outright.
`debleed` had gone through `save` and inherited the hole; it was closed the same
way the same day.

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
is not a convenience: the prompt reads `input()` and answers *no* on EOF, so a
caller with no terminal cannot confirm without it, exactly as with `label
--forget`.

**The CLI owns every mutation, and is the only implementation of any of this.**
The projects file, the tag logic, the lifecycle vocabulary and the rules about
what a name may be live in Python exactly once. The tray and the command center
import them, because they are one Python process inside this package already and
spawning a subprocess of its own CLI would buy nothing but an interpreter start
per refresh; the deleted VS Code extension shelled out to the same commands
because it was TypeScript and had no other way in, which is the same rule reached
the other way. The rule is one implementation, not one process
boundary — worth saying in both directions, since a later reader could "fix" it
either way.

**Eleven commands answer in JSON**, and the first five only because the VS Code
extension asked. They outlived it, on the rule step 23 wrote down: a document you
can print is a document you can test, and they are what anything scripting this
from outside Python reads. Step 20 added `transcript <id> --json` and `show <id> --json`, phase
3 added the `gallery` field on `label <id> --json`, phase 4 added `hotwords
--json`, and phase 5 added `people
--json` and the `people` field on `transcript <id> --json`; they exist even
though the command center calls the functions directly, because the document is
the shape both surfaces agree on and one you can print is one you can test.
`hotwords --json` is the one that had been *refused* on that reasoning: step 12b
gave the verb no JSON form on the recorded ground that nothing read the document,
and phase 4's read-only hotword panel is where that stopped being true.
`referat
list --json` is every meeting with its duration, status, audio state, title,
unnamed speakers, `tags` and folder, across both roots, plus one top-level map of
project id to display name; `referat project list --json` is every project with
its docs, its glossary and how many meetings carry it, plus the orphaned ids no
project answers to; `referat label <id> --json` is one meeting's unnamed speakers
with their snippet paths, sample lines and the **channel** each arrived on, plus
the owner's name at document level — the two fields that let a labeling surface
say *this voice came out of your own microphone* and lead the chips with the owner,
which is a hint and never a name applied on its own, and since phase 3 a
**`gallery`** of `scoped`, `rest` and the `tags` that scoped them — the last of
those so a surface can say *this meeting has no project, so every name is
offered* as something it was told rather than something it inferred from a short
list; and `referat status --json`, added at
step 15 for the sidebar's status bar item, is what the tray is doing — with
`elapsed` as `format_duration`'s own output, so nothing outside Python ever grew a
second duration formatter, and a `stale` flag that tells a tray which died apart
from one that never ran. The fifth is `referat project add <name> --json`, which emits the
project it just created: an id is `slugify` plus a `-2` collision suffix, so a
caller cannot work it out, and looking it back out of `project list` by display
name is wrong the moment two projects share one. The sixth and seventh arrived
with the command center. `referat show <id> --json` is one meeting's whole
record: `meta.json` itself under `meta`, through `Meeting.to_json` so the
pipeline's own serializer decides what a record is, wrapped in the four derived
answers every surface already asks Python for — title, formatted duration, audio
state, staged. Its point is the `transcription` block, which nothing had ever
printed: the model and device a run used, the per-channel quality numbers the
gate judged, and each cluster's `match` **with its runner-up, recorded even where
the match was refused** — the near-misses being the only material there is for
calibrating `[speakers].match_threshold` and `match_margin` against real voices.
And `referat transcript <id> --json` is `transcript.md` read back through
`transcribe.parse_transcript`, which lives beside `render_transcript` so the
format is described in one file; each entry carries its timestamp twice, as
seconds and as the `HH:MM:SS` the file renders, because a cross-link is matched
on the number and a reader sees the string — and its `people` names which of the
labels are somebody's name, from `voices.name_complaint`, so a surface can link a
name without a second copy of the rule that `ME`, `REMOTE` and `SPEAKER_NN` are
not one. It also carries `markdown`, the file itself, which costs nothing because
that function has already read it and is what a **copy** out of the transcript
pane hands over — the alternative being a window that opens `transcript.md` for
itself, which is the one thing it may not do. The ninth is `referat people --json`: every name there is, with the
voiceprints filed under it and where each came from, the meetings the name
appears in and the projects those carry. **Names, counts and meeting ids and
nothing else** — no embedding, and not even the path the database lives at. That
is not a formality about a listing verb; it is the whole reason the rule is
restated wherever a person is the subject. All nine are the same functions the
human-readable forms call, so the table, the dashboard and the window cannot
drift apart — and the id-to-name map inside `list --json` comes out of the very
`ProjectsDB.name_map` that `project list --json` hands out, so the join between a
tag and its name cannot drift either. The tables themselves are unchanged and stay the default: `referat show`
prints the record for a person, and `referat transcript` prints **who spoke, how
often and what share of the words** rather than the transcript itself — which is
a file you can already open, is the document here most likely to hold a character
this console's code page cannot encode, and answers that one question worst.

The eleventh is `referat recap <project-id> --json`, step 24's, which prints
`cli.recap_document`: the recap's text and frontmatter, the series of tagged
meetings a pass would read now and which of them have notes, and whether the
file is stale and why — the same document the projects page renders, so the
state line beside *Recap…* and the JSON cannot disagree about what stale means.

`referat label` also grew the three flags that let something without a terminal
name somebody: `--speaker SPEAKER_NN --name <name>` applies one name,
`--forget <name> --yes` skips the confirmation, and `--json` dumps the meeting.
They exist because the prompt cannot be driven from a subprocess — it reads
`input()`, and `--forget`'s confirmation answers *no* on EOF, so a caller with
no terminal was told "Nothing was deleted." Each is a thin wrapper over
`label.name_speaker`, `label.forget` and `label.label_document`, which the module
docstring had reserved for exactly this since step 7b. **The rules are not
duplicated in the wrapper**: `voices.name_complaint` still decides what a name may
be, and the apply path refuses a speaker who already has one, because renaming is
a different operation from naming. Both of those live in `name_speaker` rather
than in `run_apply`, so the command center gets them without a second copy — and
so does the sentence a refusal is said in, which is returned unprefixed because
the CLI says `referat label:` and a dialog says nothing at all. `apply_name`
underneath it is the primitive that knows the order of writes and that the
pipeline calls too.

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
kept; it renumbers the speakers, so it clears the previous run's **snippets**
first and lets identification find the names again in the known-voices database.
Deliberately the snippets and not the `speaker_names`, which it also used to
clear. Nothing in the pipeline reads that map, the success path replaces it
wholesale from this run's transcripts, and until then it is the map that
describes the `transcript.md` still on disk — so clearing it up front bought
nothing and cost a meeting's names to a Ctrl+C on 2026-09-02. **A rerun must
leave a meeting it did not finish exactly as it found it**, which is the same
reason `transcript.md` is written atomically at the end rather than streamed.

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

## Notes and browsing (build steps 10-12, and 20)

**Steps 10, 11, 15 and 12 are built**, in that order — 12 was re-sequenced to run
after 15 because it would otherwise have packaged the TreeView step 15 deleted.
The scaffold, the `/cleanup` prompt and `referat index` exist and have been
driven on real transcripts; the extension built at 11 and packaged at 12 was
deleted at step 23. What is still open is the tuning
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
immutability rule they cannot be corrected where they happened. Two lists say
what things are actually called. **`PEOPLE.md`**, beside `INDEX.md` in the
meetings folder, is everybody Referat knows by name — full name, the short name
a transcript label uses, and the id — and it is **generated**, by
`index.write_index`, which is already the one function every change to a name
calls: the pipeline after a transcription, `label` after a naming, a forget and
a rename. Generated rather than hand-maintained because step 20c made the
record the registry, and a table kept by hand beside it would be the second
registry of the same fact. It carries names and nothing else — never an email,
which is contactable personal data where a name is a label, and never anything
out of `.voices/` — because it lands in a folder a sync client may see. (This
paragraph claimed a hand-kept *Known people and terms* table from step 10
onward; the table never existed, which was found on 2026-09-04 and built as
this on 2026-09-06.) The second list is the `glossary` of each project in a
meeting's `tags`. The rule is written in the template's `CLAUDE.md` under
*Known people and terms* and again in the prompt's *Names and terms* section,
and the live copies are reconciled by hand. An exact match against either is
normalized silently. A **near miss is corrected and flagged in the note itself** —
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

**The command center is the primary UI** since step 20 — a desktop window the
tray app owns and opens from the tray icon. It is **PySide6 with native Qt
widgets**, and the toolkit half of that was decided by measurement (see the Smart
App Control paragraphs above) while the widgets half was decided in phase 1
against the criteria step 20 wrote down: `QTextBrowser` renders Markdown, holds
anchors and answers a custom URL scheme, which is every criterion this UI has,
and the one argument for QtWebEngine — reusing the VS Code sidebar's own page
— is an argument for keeping the three-hundred-pixel column this step exists to
escape. Qt owns the tray icon too: `QSystemTrayIcon` replaces `pystray`, so one
process holds the icon, the hotkeys, the recorder and the window, with one event
loop and no IPC between them. That is also what lets the window carry **record,
pause and stop buttons calling the same methods the hotkey handlers call** — no
new CLI verb and, more to the point, no second path into the state machine.
`on_toggle_record` was split into `App.start_meeting` and `App.stop_meeting` for
exactly that: one button has to mean both, three buttons each know which they
mean, and there is still one implementation of each.

It is built on **three entities**. *Meetings*: new ones land in an untagged
inbox, each row shows its lifecycle stage, and a tabbed Markdown viewer puts
`transcript.md` beside `notes.md` with timestamp references in the notes
navigating to that point in the transcript — a scroll position, since the
timestamp is audio-elapsed with pauses excluded and the audio is normally gone.
*Projects*: deliberately lightweight, a tag and a name and an optional one-line
description, with assignment strictly manual. *People*: a page unifying the
voiceprint identity with which projects and meetings that person appears in,
opened by clicking a name in any document — which is the thing a three-hundred
pixel column could never hold and the real argument for the window. The opening
screen is a **dashboard**: recent meetings, pending untagged meetings, pending
unlabeled speakers, the **action items** owed by whoever is looking, and a
**day summary**. Themes are still later.

**All seven phases are built.** Phase 7 arrived on 2026-09-04, the same day step
13 did, and is the projects page growing *Link doc...*, *Unlink* and *Sync now* —
three buttons onto the three guarded functions, which is what splitting them out
was for. `referat/ui/docs.py` is the dialog: it takes a pasted **link** rather
than a document id, fetches the document's tabs on a thread and shows them, and
**never guesses which one** — a `?tab=` in the link decides it, or a tab named
`Meetings` does, and failing both nothing is selected and *Link* stays disabled.
Defaulting to the first tab was written first and was wrong on the first real
document, whose tabs are `Proposal` and `Meeting Notes & Writing Log`; a wrong
default that is *visible* is still a wrong default, because the point of a dialog
is that somebody clicks through it. The window **cannot authenticate**, which is
a property of the paste flow rather than a limitation: consent needs a terminal,
so a browser consent can never be started from the thread that owns the recorder. Phase 1 was deliberately read-only — the
meetings list, the viewer, the cross-links, the recording buttons and the ambient
state — so the toolkit was settled against real meetings rather than against a
prototype, with nothing at risk. Phase 2 is the first thing this window writes,
and it writes **tags and only tags**: an untagged inbox behind an *Untagged only*
toggle, a search box, and a tag picker that also creates a project inline. Phase
3 is the second, and it writes **names and only names**, through a *Speakers…*
button beside *Tags…* — until step 20b, when the same dialog grew the third
answer beside *name* and *leave*, a cluster marked as **noise** through
`noise.mark_noise`, which is a second verdict on a cluster and the lines under
it and still no other key. Neither dialog implements a rule: the picker drives
`cli.apply_tags` and the speaker dialog drives `label.name_speaker` and
`noise.mark_noise`.

**Phase 4 is the third, and it writes no `meta.json` key at all** — it writes
`projects.json`, through the five guarded functions above, and it is where the
window became **tabs**: Meetings and Projects, with People arriving at phase 5
and the dashboard at 6. The recorder's three buttons stay *above* the tabs, because the
recorder is not one of the three entities and a Stop button hidden behind a tab
is a recording somebody cannot stop from here; *Tags…* and *Speakers…* went the
other way, down into the meetings page, since on a screen about projects they
would be two dead buttons. Only the page in front is refreshed — each costs a
scan of both meeting roots — and a hidden one is brought up to date when it is
switched to.

**Phase 6 is the dashboard, and it is the fourth thing that writes nothing at
all.** It is the *first* tab, so it is what the window opens on: the meetings
list is an inventory, and the question somebody opens this window to ask is
whether anything is waiting for them. Recent meetings on the left, capped so it
stays a glance, and three queues on the right — untagged, speakers nobody has
named, no notes yet — in the order the work is done in, which is the flow rule
this UI already follows. The notes queue also holds a meeting at `transcribed` that already has a
`notes.md`: `rerun` writes `transcribed` and touches no note, so those notes
describe a transcript that no longer exists, and the row says so. Read off the
lifecycle, never off the file. Every row **navigates** and nothing acts in place: a
queue row opens that meeting on the Meetings tab where *Tags…*, *Speakers…* and
*Generate notes…* already are, because draining a queue from here would be a
second path to each of those three writes, and the picker and the labeling dialog
are exactly where the rules about what a tag and a name may be are enforced.
Which meetings are in each queue is `cli.pending`, a pure function over a
`list_document` — so *Notes for all…*, which had been carrying its own copy of
the notes predicate, now asks the same question the queue answers; a count and a
button that disagreed would look right while being wrong. And the page is
**exempt from the refresh-the-front-page-only rule rather than an exception to
it**: it reads nothing, is handed the document the window has already read for
its meetings list, and so costs a redraw and no I/O whether it is in front or
not. Because the window now opens on it, `show_window` brings the Meetings tab
forward whenever it is given a meeting id or asked for the untagged inbox —
which is what step 16's on-stop toast does, and a toast that opened a summary of
every *other* meeting would be a link that visibly did nothing. Themes and action
items extracted from notes are a later box inside this phase and not its
baseline; when they arrive they may render what a note says and may never tag a
meeting, propose a tag, or reorder the untagged queue by a guess.

**Every icon is drawn, and colour means a state or it means nothing.**
`referat/ui/icons.py` grew from the tray's one icon into all of them, and the
reason they are `QPainter` rather than an icon font or an SVG set is the same
argument the PySide6 audit made: an asset pack is a package in
`pyproject.toml`, the base install is on the recording path, and every file on
that path is something Smart App Control can one day refuse. Two rules carry
across from the tray. **Colour carries the state** — Record, Pause and Stop wear
the recorder's own colours, so red means recording in the window and in the
notification area without either being taught the other's vocabulary, and every
meeting row carries a dot in its lifecycle's colour. **Shape carries a run that
lost a channel** — that dot is hollowed into a ring by exactly the condition that
hollows the tray's, because a ring reads as wrong at sixteen pixels where a
slightly different red does not. Everything that means no state — the tabs, the
queue headings, `Tags…`, `Speakers…` — is drawn in the palette's `windowText`, so
a dark theme gets light icons rather than black ones on a dark tab bar. Two
things follow that are easy to get wrong in the other direction: a queue heading
wears the glyph of the **button that drains it**, so a row followed to the
Meetings tab finds the thing to press next wearing the picture it came from; and
a list's rows do **not** repeat their heading's glyph, because the same icon
down twelve rows is the one thing on a page carrying no information — they carry
the meeting's dot instead.

**The projects page is where a glossary is edited, and that is hotword
management.** A glossary is read twice at two different times — merged into the
one global list handed to faster-whisper before anything has been tagged, and
used again by `/cleanup` once a meeting *is* tagged — so the terms are already
here and a separate screen about hotwords would be a screen about a file rather
than about the work. Underneath the page sits a **read-only** panel of the merged
list from `cli.hotwords_document`: every term with its source, what it costs
against the 223-token budget, and what the cap dropped, by name. That last part
is the point, and it is step 12b's own sentence: a cap nobody can see is how this
turns into a bug report about one specific name that is never heard right — and
this is the page somebody stands on while adding the term that pushes the list
over. What the page does **not** do is link a Google Doc: `project link-doc`
arrives with step 13, so it renders the references it finds, which is a list of
zero and not a schema narrowed to one.

**An unreadable `projects.json` gets one sentence with three endings**, and the
ending belongs to the caller: a mutation is told writing would replace everything
in the file, the tag path is told nothing can be tagged, and a *read* — which is
what `referat project glossary` and the page both do — is told no project can be
looked up. Phase 4 is what made that a real distinction. `project_document`
carries the complaint for the same reason and `referat project list` prints it:
an unreadable file loads as *no projects*, which is the rule that keeps a broken
one from costing a transcript, and it means an empty list has two causes that
draw the same picture. The page must not guess between them either, since it
offers to create a project into a file whose contents it cannot see, so a
complaint disables every control that would write.

**Phase 5 is the third entity and the reason there is a window at all.** A
person unifies three things living in three files — a voiceprint identity in
`.voices/`, the meetings whose `speaker_names` call somebody that, and the
projects those meetings carry — and none of them has anywhere to sit beside the
others in three hundred pixels. `referat/ui/people.py` is that page, and
**clicking a name in any document is what opens it**: a speaker label in a
transcript, and a `[[Wikilink]]` in a note. Its own rows navigate the other way,
to a meeting or to a project, and every one of those hops clears whatever filter
would hide what it was asked to show — a link landing behind a stale search box
looks exactly like a link that did nothing.

**The two lists on that page differ on purpose and it says so.** A voiceprint
records the meeting it was *filed* from; `speaker_names` records where a name
appears. `people.directory` joins both, because neither subsumes the other — a
person recognised automatically files nothing new, and a `rerun` renumbers past
somebody whose print keeps the provenance the meeting record lost. Appearing in
more meetings than you have prints from is therefore the normal state, and a page
that showed the two counts without saying so would read as a discrepancy report.
`project_people` is now expressed *on top of* `directory` rather than beside it:
two walks of the same two files that had to agree would be the first two things
to disagree.

**A name with no voiceprint behind it is shown rather than filtered out.** A
transcript still calling somebody `Anna` while the database holds nothing under
that name is drift, and nothing else on this machine compares those two files. It
gets a section of its own, the way orphaned tag ids get one on the projects page,
and for the same reason.

**Which labels are names is `voices.name_complaint`'s answer and nothing else's.**
`transcript_document` carries it as a `people` field, so the viewer links a name
without a second copy of the rule that `ME` and `REMOTE` are channel labels and
`SPEAKER_NN` is a number. A `[[Wikilink]]`, by contrast, is linked **whatever it
says**: nothing in a note distinguishes a person from a project, and the two
alternatives were to link none of them or to hold a name list inside a widget
that goes stale between refreshes. A name nobody is filed under opens the page
and is told so plainly, which is information about the note rather than a dead
end.

**The one thing the page writes is a deletion**, through `label.forget_person` —
the operation, holding the refusal of a name nothing is filed under and the
dashboard regeneration — and never through `label.forget` beneath it, which is
the primitive. The same split as `name_speaker` over `apply_name`, made for the
fourth time. The confirmation is deliberately *outside* the operation: `--forget`
reads `input()` and answers no on EOF, which is a question asked of a terminal,
and a window asks the same question with a modal. Neither asks it twice.

**A person is a record, from build step 20c on 2026-09-04: an id, a full name,
a short name and an email.** The database used to be keyed by a bare name, and
the problem was already on the shelf: the tenth person called Anna would have
been indistinguishable from the first, and `voices.match` compares *keys* — two
people filed under one name would silently merge into one voiceprint set, which
is the single worst failure this system has. So a person became exactly what a
project already is: `{id, name, short, email}` keyed by `id`, the id being
`projects.slugify` of the full name plus the same `-2` collision suffix, which
is a rule this codebase has one implementation of. **Two John Smiths are two
ids**, and that is the answer to whether names must be unique — they need not
be, because they are not the key. `SCHEMA_VERSION` moved to 2, because the key
changed meaning; a version-1 file is read without being rewritten — a bare name
loads as `{id: slugify(name), name, short: name}`, exactly as `MeetingStatus`
maps `stopped` and `done` — and the next save writes version 2. Two legacy
names that slugify alike are refused as unreadable rather than merged, which is
the one failure the whole change exists to prevent, and the twenty-eight names
on this machine were checked to be distinct before any of it was trusted.

**Which file holds which half is the same split `tags` already makes.**
`voices.json`, `meta.json`'s `speaker_names` and the per-channel `name` and
`match.name` hold the **id**, because they are records; `transcript.md` holds
the **short name**, because it is prose. `voices.person_id` is how a value in
`speaker_names` is read — `slugify` is idempotent on its own output, so a legacy
`Lars Klein` and an id `lars-klein` resolve alike, and no meeting on disk was
migrated. `voices.identify` therefore returns the short name for the renderer
while stamping the id onto the cluster, and `bleed.suppress` is handed the
owner's id rather than the config's spelling.

**Renaming fell out of the id, which is why it was not built before it.** *The
id never moves and a rename changes a display field* — the sentence step 13
wrote about projects, transferred line for line. `label.rename_person` is the
tenth guarded function, `referat person rename <id> --name --short --email` is
it printed and *Rename…* on the people page is it in process, and every rule
lives in it: what a name may be (both go through `name_complaint`, since both
end up in a label), what an email may be (`email_complaint`, shallow on
purpose), and **what propagates**. A changed short name is relabeled into every
`transcript.md` whose `speaker_names` carries the id — `relabel_transcript`, the
one sanctioned edit to that file, in both directions as `--forget` uses it — and
in those same meetings' `notes.md` **the `[[Wikilinks]]` to the old spelling
are rewritten and nothing else is.** The brackets are the one place a note is
*referring* rather than *saying*; a find-and-replace across somebody's prose is
the same move as spelling a name onto a `SPEAKER_NN`, authoritative when wrong.
Prose still using the old name surfaces on the people page as a name nothing is
filed under, which is the safety net either way. A rewritten note drops a
`synced` meeting to `notes_written` from the function that *knows* the notes
changed, as `cli.set_notes_written` does, so the next sync re-renders the block;
a Google Doc's own prose is never reached into.

**Two refusals hold the transcript honest.** A short name is the whole of a
label, so two people one file calls `Anna` are one label and every later edit
to it — a forget, a rename — would hit both. `voices.namesake_in` is the
question, and it is asked in two places: `name_speaker` refuses the second Anna
into a meeting that already has one, and `rename_person` refuses a short name
that would create the same situation retroactively. And a name two people on
file share is refused *by id* rather than resolved to the first — `VoicesDB.resolve`
tries id, then full name, then short name, and an ambiguous hit names the
candidates. A chip in the speaker dialog therefore fills the id for a shared
full name, since the name alone would be a refusal the chip offered.

**The email is stored and never sent, and the one place it must not reach is
the hotword list.** `hotwords.collect` now reads `VoicesDB.spellings()` — the
full name and the short name where they differ, because Whisper may hear either
— and that method does not return the address, so the merge that reads the
database live cannot be told to listen for one. The `people` table does not
print it either; `--json` and the people page do, for a person reading about one
person. Nothing derives a short name from a full one: *Lars* out of *Lars
Klein* is a guess about a name on every line of a transcript, so somebody new is
typed as a full name with an optional `--short`, and the record is edited
afterwards. `scripts/person_fixture.py` drives all of it against a synthetic
version-1 database and three synthetic meetings, and never reads the real one.

**The window writes notes and deletes meetings, from 2026-09-03.** Those were
two of the four things the sidebar could do and the command center could not, so
anybody living in the window had to keep the extension open for them — which is
the
opposite of what a primary UI is. *Generate notes…* drives `cli.write_notes` and
*Delete…* drives `cli.delete_meeting`, both new guarded functions over machinery
that already existed inside `run_notes`' and `run_delete`'s printing. The same
correction as `apply_tags`, `name_speaker`, the five project functions and
`forget_person`; step 23 added `cli.promote_meeting` for the ninth time. The
shape is settled — **a `run_*` that
holds a rule is a `run_*` a second surface cannot use.**

Notes run **on a thread**, because a cleanup pass takes a minute or two and this
process owns the recorder: blocking the GUI thread here would freeze the window
somebody stops a meeting from. What comes back comes back through `App.notify`,
which is now the safe crossing.

**The window re-transcribes and promotes, from 2026-09-04.** Those were the other
two, and building them is what let the extension be deleted at step 23 — the
audit of what parity meant is in that section below. *Re-transcribe…* and
*Promote…* lead the action row, **before** *Tags…*, because they come first in a
meeting's life: everything else there acts on a transcript and these two act on
the audio it was made from. Both are disabled for almost every meeting, which is
honest rather than untidy — the audio is normally gone, and a meeting that still
has it is a meeting waiting on exactly one of those two decisions. *Promote…* is
enabled on `staged` and not on `audio == "kept"`, which is the wider of the two
and covers a promotion that failed to move the folder as well as a failed gate.

**`referat notes <id>` is that verb at the prompt, and `referat/notes.py` is the
only implementation.** It spawns the official `claude` binary with `-p "/cleanup
<id>"`, `--allowedTools "Read,Write,Glob"` and cwd at the **meetings folder** —
never the repository, because the prompt, that folder's `CLAUDE.md` and the
`.voices/` deny rule all live in its `.claude/`, and a pass run anywhere else
would have none of them, the deny rule included. `Bash` is still the line that
does not move. Afterwards the caller records `notes-written` through
`cli.set_notes_written`, because `/cleanup` is forbidden from touching
`meta.json` — and `cli.write_notes` is the operation over both halves, so no
surface sequences them itself. A pass that wrote the notes but could not record
the state is reported as a partial success, not a failure: `notes.md` is on
disk, and calling that a failure sends somebody to fix the wrong thing.

**Finding `claude` from Python was not the same problem the VS Code extension
solved.** It asked VS Code, which follows its own extensions; Python has no VS
Code to ask, so `notes.resolve_claude` reads `~/.vscode/extensions` and **honours
the `.obsolete` file VS Code writes there** — on 2026-09-03 that listed 2.1.252
as obsolete beside a live 2.1.258, so taking the highest version number would
have picked a directory about to be deleted. Then `PATH`. Resolved at spawn time
and **never persisted**, for the reason the extension never persisted it: a
stored absolute path is still there a week later pointing at nothing.
`[cleanup].claude_binary` is the escape hatch and is **not a credential** — no
API key belongs in `config.toml`, and `claude` owns all authentication.

**A speaker chip is the deliberate exception to the click-a-name rule.** A chip
fills the name field rather than applying itself, that rule is load-bearing, and
the dialog is modal over the very page a link would navigate to. So a chip
carries a *tooltip* saying who somebody already is — prints, meetings, projects —
which is the question a person actually has while naming, answered without moving
them anywhere.

**The picker is two sets, and that is the whole of its design.** `carried` is
what the meeting already has — the baseline, never mutated — and `checked` is the
tick state, which *grows* when a project is created inside the dialog. The VS
Code picker shipped with one set serving as both, so a project created in it was
already in the baseline when the diff ran, landed in neither `added` nor
`removed`, and was never applied: the project existed and the meeting stayed
untagged. `checked` is also the only truth the dialog has — filtering hides rows
rather than rebuilding, creating appends one, and nothing reads the check states
back in bulk, because rebuilding a checkable list and then restoring its states
races the widget. The reopen-the-picker workaround the extension needed has no
counterpart in Qt and must not be reinvented.

**A refusal reaches the window as `cli.Outcome`, and the message is
unprefixed.** The sentence its rule's owner wrote, never a paraphrase — which is
what the deleted extension got by reading the CLI's stderr and could get no other
way, and which this gets in process. The guarantee is the part that mattered
rather than the mechanism, and the
prefix is left to the caller because it is direction-dependent: `referat tag`
says one thing, `referat untag` another, and a picker calling both directions at
once has no command to name. `meeting.resolve_meeting` already returns its
complaint for exactly that reason, which is why this is the same pattern rather
than a new one.

**`cli.apply_tags` opens `projects.json` only when something is being added**,
and the asymmetry is load-bearing. `remove_tags` validates nothing on purpose,
because an orphan is precisely the tag somebody needs to take off a meeting;
guarding an untag on the file being readable would make a broken `projects.json`
the one thing that pins an orphan to a meeting forever. So a pure removal never
reads it, exactly as `referat untag` never has.

**The slow things say what they are doing, through `referat/progress.py`.** A
transcription is two minutes of a GPU and a cleanup pass is a subprocess that
could be thinking or could be hung, and both used to be legible only by tailing
the log. That module is a registry of running jobs — a key, a title, a phase and
a fraction — with listeners, and it is the same shape as `App.notify`: **the work
reports, and something else decides whether anybody is looking.** No toolkit is
imported there, nothing is required (a job that never calls `begin` simply does
not appear, which is what keeps `referat rerun` at a prompt exactly as it was),
and a listener that raises is logged rather than allowed to reach the pipeline.
It is **live state and not history** — a finished job is removed, because what
happened is the log's business.

The one real progress *figure* in the pipeline is faster-whisper's, and it is why
the segment generator is drained in a loop rather than a comprehension:
`segment.end` against the decoded length is a true fraction. Everything else —
loading a model, diarizing, waiting on `claude` — reports a phase and `None`,
which a surface draws as a busy indicator. **`None` is the honest absence of a
claim and zero percent is a claim**, so they are not interchangeable. The window
renders it in the status bar, permanent so it shows from every tab, and hides it
when nothing is running rather than parking an idle bar nobody will read.

**A cleanup pass reports what it is doing, and getting that required changing
how `claude` is invoked.** Plain `-p` prints one blob when the whole pass is
over — measured: a run that started at 10:53:30 said nothing until 10:58:41 and
then said everything — so the phase read *starting claude* for two minutes and a
hang looked exactly like work. `--output-format stream-json --verbose` emits one
JSON object per event instead, and `notes._phase` turns the useful ones into a
sentence naming a file. The verdict comes from the final `result` event, which
carries `is_error`, rather than from the exit code alone.

**Notes runs are a queue with one worker**, not a thread each. Every pass is a
real subprocess against one rate limit writing into one folder, and sequential
is also what makes the queue legible. *Notes for all…* fills it with every
promoted meeting that has a transcript and no notes, oldest first; a staged
meeting is skipped, because `/cleanup` runs with cwd at the meetings folder and
cannot reach one. Each id is announced to `progress` as *queued* the moment it
is accepted, so the whole backlog is visible rather than only the job in flight.

**The Activity tab is two kinds of truth and neither is a copy.** The queue is
live state out of `progress`; the log pane is the **real rotating log file**,
because Referat already writes every transition, model load, gate verdict and
refusal there and a parallel in-memory history would be a worse version of it
that disagrees the first time something is logged from a thread the window never
hears about. It is **the one page with a timer** — a deliberate exception to
*refresh on show, on F5, on a transition*, since a log grows with no event this
process can see — and the timer runs only while that page is in front.

**The Whisper model is cached and is not re-downloaded**, which needed saying
because the log said otherwise. `faster_whisper` asks Hugging Face for the
model's current revision on every load, and that one `httpx` line reads exactly
like a 3 GB download starting; the model is 2.9 GB on disk and loads in about
four seconds. `logging_setup.CHATTY` raises httpx and five other libraries to
WARNING. **A log line that reliably misleads is a bug in the log line.**

**The viewer opens on the notes, not the transcript.** That ordering is a claim
about which document is the point: the transcript is evidence and a source, and
leading with it opened every meeting on several hundred utterances. `Ctrl+±` and
`Ctrl+0` zoom **both** panes together — they are two tabs of one document, and
Qt's own Ctrl+wheel was not enough because it needs a mouse and moves one pane.

**A copy out of the viewer is the source, not the rendering, and it comes in two
flavours because there are two places it goes.** A note leaves this window for
Claude or for a Google Doc, so *Copy as Markdown* (`Ctrl+Shift+C`) writes the
Markdown as plain text and nothing else, and *Copy as formatted text*
(`Ctrl+Alt+C`) writes clean HTML with that same Markdown beside it. **Two
commands rather than one clipboard carrying both**, because Word, Google Docs and
Outlook all *prefer* an HTML flavour when one is there — which is how a paste
used to arrive as theme-coloured monospace, and exactly the reason the meetings
folder sets `editor.copyWithSyntaxHighlighting: false` for VS Code. Offering both
at once would decide for the paste; offering them by name leaves the choice with
the person. The HTML is `referat/ui/richtext.py`'s and never the pane's, and it
carries no colour, font or size at all: a rendering of *this window* is the thing
being escaped, not the thing to send.

**Reaching them is the part that was wrong**, and the complaint was not that the
commands did not exist. They were on `Ctrl+Shift+C` and nowhere else, so the
gesture somebody actually uses — right-click, Copy, or `Ctrl+C` — went to Qt's
own copy of the *rendering* and arrived without its `##`, its `-` or its `**`.
Both now route to the same two commands, which also sit on each pane's context
menu and on a copy button in the corner of the viewer's tab bar. The keys are
bound in **one** place, the window, which dispatches them onto whichever page is
in front — the Actions tab copies its own list with them — because two claims on
one shortcut in one window is a shortcut Qt fires neither half of.

**The source is what is copied, selection included, and that took a map.**
`setMarkdown` keeps no way back from the document it built to the text it was
given, so `richtext.blocks` splits a source exactly where Qt splits it into
`QTextBlock`s — a heading, a paragraph however many lines it wrapped over, one
per list item — and a selection is turned back into the Markdown it was written
as. The counts are checked before the map is trusted; where they disagree, Qt
reconstructs the fragment instead and `viewer.unlink` takes this window's
`referat:` links back out of it. That order is measured rather than tidy:
**Qt's Markdown writer drops `**bold**` and `*italic*` entirely**, and the notes
put every action item's owner in bold, so the reconstruction is a last resort.
A whole-document copy needs none of it, which is why the transcript pane now
keeps its source too — `transcript_document`'s `markdown`.

**A redraw of the same meeting keeps the viewer's place.** `Viewer.show_meeting`
remembers the id it is showing and, when handed the same one again — a refresh
after tagging, F5, a transition — restores both panes' scroll after rebuilding
them. It used to jump to the top on every redraw, which after tagging a meeting
looked like the window losing its place. Done in the viewer rather than by
skipping the redraw, so F5 still re-reads a transcript whose labels may just
have changed.

**A meeting with no transcript yet says which of the two waits it is in.** A
recording has not finished happening; a transcription has finished happening and
is being read. `transcript.md` is written whole at the very end of the pipeline —
deliberately, so an interrupted run leaves the previous one intact — so there is
genuinely nothing to show, and two empty panes said that in the same voice as a
broken window.

**The meetings list is full width and the viewer is under it**, which is the one
layout question the six columns settled: three-sevenths of a window never fitted
them, and no resize policy makes six columns fit in four hundred pixels. Title
takes the slack and the rest size to their contents.

**The transcript pane is built from the parsed entries, and the notes pane is
not.** The transcript is rendered as HTML from `cli.transcript_document`, which
is what makes a timestamp addressable: every entry gets an anchor, so a
`[HH:MM:SS]` in the notes has something to scroll to. Rendering the raw Markdown
through `setMarkdown` would be one line and would leave nothing to navigate to.
The notes are the opposite and do go through `setMarkdown`, because they are
somebody's prose; the one thing done to them first is rewriting each timestamp
into a `referat:` link this widget answers. Both browsers run with
`setOpenLinks(False)`, or an unknown scheme would be *loaded into the pane* and
blank it. The cross-link resolves to the **nearest entry at or before** the
target, because a note's timestamp is approximate and an exact hit would usually
miss — leaving a click that visibly did nothing. Every node is escaped: the text
is whatever Whisper heard and the label is whatever somebody typed into `referat
label`, which is the same reason the VS Code sidebar built its nodes with
`textContent`.

**A `[[Wikilink]]` renders as a bold link with the brackets taken off**, and the
argument that used to keep them was about the wrong file. It was that `[[Anna]]`
is how `/cleanup` spells a person, so a link quietly dropping the brackets would
look like a different notation — true of `notes.md`, which is unchanged, and not
true of a *rendering* somebody reads, where a note that is mostly people becomes
a page of markup nobody rendered. Bold is what `richtext.py` and `digest.py` were
already specified to do with one, so the window, a paste into Google Docs and a
future digest now agree. A timestamp keeps its brackets for the opposite reason:
they are the whole of its notation, and a bare `00:12:30` in prose is a duration.

That is why **a person's link is put back from its URL and not from its text**
when something is copied out. The rendering says `Anna` and the source said
`[[Anna]]`, so the brackets have to come from somewhere, and the URL is the one
place still holding the name as written. `unlink` therefore takes a `wiki` flag,
because the identical `referat-person:` link means two different source
notations: a wikilink in the notes, and a **speaker label** in the transcript,
whose source is the bare `Anna:` it already renders as. The rendering cannot be
read back to tell those apart, so the caller says which pane it is copying rather
than the function guessing.

**There are three `referat*` schemes and no more is intended.** `referat:` is a
timestamp, `referat-person:` a name, `referat-meeting:` a meeting id — the third
arriving with the day summary, whose bullets cite the meeting each came from.
They are parsed in one module beside each other, so the two panes that answer
them cannot come to spell one two ways.

**Action items are parsed out of the notes, and never re-extracted.** Every
`notes.md` carries a `## Action items` section that `/cleanup` wrote and nothing
read; `referat/actions.py` is the parser and `referat actions` is the verb. It is
a parse rather than a guess because it was audited before it was chosen: across
the nine files then on disk, **64 items with no deviations** from
`- **owner(s)** — text`, every one leading with a bold span and containing an em
dash. Reading `notes.md` is allowed where reading a transcript would not be —
the note is *derived*, so this does not breach *nothing is inferred from a
transcript* — and the line it may not cross is the one the dashboard already
records: **it may never tag a meeting, propose a tag, or reorder a queue.**

**The due date comes from a cue clause and never from a bare date in the
sentence**, and that distinction is measured rather than argued. Taking any
literal `YYYY-MM-DD` gets five of sixty-four wrong, and every one of them wrong
in the direction that matters — showing a deadline that is not one: *deferred
until after 2026-09-04*, *at the 2026-09-09 meeting* twice, *this afternoon
(2026-09-02)*, *starting Friday morning 2026-09-04*. So a date counts only as the
item's own last clause, or after `by`, `before`, `due`, `deadline` or `on` with at
most two alphabetic tokens and no comma in between — which real items need
(`by midday 2026-09-03`) and which stops a match running through a clause
boundary. Prose dates never become a `due`, because a guessed deadline is the
same failure as a guessed name. **This is the sentence somebody will simplify
back into a bug.**

**Nothing about this writes `notes.md`.** Ticking, correcting and dropping are
facts about the *reader*, and they live in `<meetings_dir>/actions.json`. Dropping
an item removes it from a list and never from the note, which stays the record of
what the meeting produced. **An item's identity is its wording** — a hash of the
note's own sentence and never of a correction typed over it — so an edit is
stable and revertible, and a re-cleanup that merely re-wraps a bullet keeps its
key because the normalization collapses whitespace. One that genuinely re-words it
orphans the tick, which is **kept and shown rather than pruned**: pruning on read
would make a read into a write, and state that vanishes on its own is state
nobody trusts. The same treatment a deleted project's orphaned tags get.
`ActionsDB` mirrors `ProjectsDB` including its `unreadable` flag, and here the
degradation cuts the dangerous way — with no state every item reads as *open*, so
a broken file makes the dashboard's box **longer**, which is why every surface
shows the complaint instead of quietly drawing the longer list.

**They are tracked per person, and you choose whose list you are looking at.**
`/cleanup` names an owner on every item, so the parse gets everybody's for free
and discarding the rest would be throwing away something already paid for. The
Actions tab has a picker over every owner actually found; the **dashboard box is
always and only the owner's**, which is the split that keeps this from being two
pages. `Unassigned` is its own entry rather than folded into anybody's list, and
a collective like `All authors` is **not** resolved to include the owner even
where it plainly does — working that out would be inferring an assignment. The
tab is third, after Meetings, because an action item is a derivative of one
meeting's notes rather than a fourth entity, and a row goes one tab left.

**`referat/ui/actions.py` is fed rather than self-refreshing**, deliberately
unlike the projects and people pages: `cli.actions_document` is pure over a
`list_document` when handed one, exactly as `cli.pending` is, so the window reads
once per refresh and hands the result to the tab, the dashboard box and the
people page's count. It is therefore absent from `_refresh_page`, and putting it
there would silently buy a sixth scan of both meeting roots.

**The day summary is a glance, and `/standup` is its prompt.** `referat day
[<date>]` spawns `claude` with `/standup <YYYY-MM-DD>` in the meetings folder,
which reads that day's `notes.md` files and writes `<meetings_dir>/days/<date>.md`
— about eight bullets, one short sentence each, what *changed* rather than what
was discussed. Explicitly not a digest, because `notes.md` already is one; it is
read in fifteen seconds before walking into the next thing, and pressing the
button again rebuilds it once another meeting has been written up. It lists no
action items and **counts none**: the first draft of the prompt asked for a count
and produced *"about 45"* beside a parser that knew it was 83, and an estimate
standing next to an exact number is the drift this codebase keeps deleting.

`notes.py` runs both prompts through one `_spawn` — same cwd, same three allowed
tools, `Bash` still the line that does not move, same streamed events — because
all of that is a property of running Claude Code in that folder rather than of
what is being written. The day version guards differently: the date is validated
as exactly `%Y-%m-%d` **before** it reaches either the prompt or a path, zero
padding included, since `strptime` accepts `2026-9-3` and would then name a second
file for a day that already had one; and a day whose meetings have no notes is
refused rather than spending a subprocess to write an empty file. There is still
**no automatic LLM pass** — both are things somebody presses.

**The summary is rendered rather than shown, and every bullet carries the id of
the meeting it came from.** `/standup` ends each line with `[2026-09-03_1408]`,
which is the one piece of provenance the file has, and
`dashboard.day_markdown` spends it twice: the citation becomes a link that opens
that meeting, and the **project the meeting is tagged with is put in front of the
bullet**. The division is the point. A meeting's `tags` are in `meta.json`, which
`/standup` is forbidden to read, and a project worked out from what a meeting
sounded like is precisely *nothing is inferred from a transcript* being broken
one layer downstream — so the prompt cites the meeting it certainly read and
Python joins on the tag a human actually applied. Rendered through
`rows.tags_text`, so an orphaned id gets the same trailing `?` it does in every
other list, and an untagged meeting gets no prefix rather than a placeholder,
because the untagged queue two boxes down is where that is said. A summary
written before the prompt asked for citations still renders, with no prefix and
no link, which is the honest picture of a line that does not say where it came
from.

`progress.DAY` is a third kind rather than a second `notes` job, because
`progress.begin` *replaces* whatever is under a key and `notes:<date>` beside
`notes:<meeting-id>` would stay distinct only because meeting ids carry `_HHMM`.
The command center's notes queue carries `(kind, target)` pairs and the day
summary joined it rather than growing a worker beside it: one rate limit, one
folder, which is the argument that made it a queue at all.

**A project recap is the day summary's shape turned ninety degrees — build
step 24, planned and built on 2026-09-08.** One project across every meeting
that carries its tag, rather than one day across every project, read once at
the desk minutes before the recurring meeting it is for. `referat recap
<project-id>` assembles that project's `notes.md` files in chronological order
into a bundle at `recaps/<project-id>.bundle.md`, spawns `/recap` on it through
the same `_spawn` the other two prompts use, and removes the bundle in a
`finally`; the prompt writes `<meetings_dir>/recaps/<project-id>.md`: `## State`,
one short paragraph on where the project stands and what was concluded most
recently, and `## Open`, at most eight one-sentence bullets on what needs
discussing — questions left unresolved, actions assigned but not reported back,
decisions postponed to a later meeting — each ending with the id of the meeting
that raised it, which the projects page turns into a link exactly as the
dashboard does for a day. Both sections are capped hard in the prompt, because
a brief that has to be scrolled has stopped being one. It lives beside `days/`
and never inside a meeting folder, because it is a property of the project: a
recap kept in the most recent meeting's folder would be one recap per tag on a
multi-tag meeting, would be orphaned by re-transcribing that meeting, and would
need a backward walk through the series that exists only to compensate for the
placement. `referat/recap.py` is the bundle, the frontmatter and the staleness
rule; `cli.write_recap` is the guarded operation in `write_day_summary`'s shape
and `cli.recap_document` is what a surface renders, `--json` printing it.

**It is regenerated from scratch every time and never folded.** Every pass reads
every note in the series, with the prompt told that later meetings override
earlier ones and that an item raised in one meeting and resolved in a later one
belongs in State, not Open. The bundle reads the same way it did on the day it
was built: the whole of each `notes.md`, which is what the digest sends — if
step 17's sectioned notes are ever built, the section read here is the digest
layer's partition and never a second reading of the same headings. The
frontmatter — `project`, `generated`, the ordered `meetings`, and `notes`, a
sha of each meeting's `notes.md` at assembly — is the watermark. Which meetings
carry the tag is `meta.json`'s to say and the prompt may not read it, so the
bundle's header carries all four and **`/recap` copies them verbatim**, the
division `/standup` already runs under; `write_recap` reads the file back and
compares what was copied with what was given, reporting a copy that disagrees
as a partial success with the disagreement named rather than leaving a recap
that shows as stale for a reason nobody can see. **Stale is decided by sha and
never by mtime**, which is the open question the plan left and the answer step
13 already gave: the meetings folder is in Dropbox, a sync client rewrites
modification times, and the digest already answers *did this note change* with
`notes_sha256`. A recap is stale whenever a tagged meeting with notes is missing
from `meetings`, one of those meetings' notes no longer matches its sha, or a
meeting in `meetings` no longer carries the tag; that is derived on every read,
stored nowhere, shown beside the recap with its reasons, and never a reason to
delete the file. There is still no automatic pass: a recap is pressed, from the
projects page or the prompt, and staleness is what says it is time — the page's
*Recap…* joins the window's one-worker `claude` queue as a third kind, one rate
limit and one folder, and *Rebuild…* is the same button once a recap exists.
Nothing in `recaps/` leaves the machine — it is derived from notes and is not
notes, and the digest push does not carry it. Incremental folding of the
previous recap is recorded as deferred, with its risk named, and is not built
until regeneration is actually slow.

**Two flow rules.** *Project-scoped identification*: when a meeting carries a
project, the names the labeling UI offers are the people associated with that
project, everybody else behind a *show all*. It narrows and orders what a human
is offered and nothing else — the automatic match during the pipeline stays
global and stays exactly the threshold-and-margin rule in `voices.match`, because
nothing is tagged yet when it runs, and **project membership never touches
clustering**. And *the UI nudges project tagging before speaker labeling*, so the
restricted gallery exists at label time. A nudge and never a gate: labeling an
untagged meeting stays possible with the full gallery, since refusing would make
a missing tag cost a name.

**That association is derived every time and stored nowhere**, in
`referat/people.py`. There is no membership list in `projects.json` and there
must not be: it would be a fourth thing to keep in step with `meta.json`'s
`tags`, the voices database and the transcripts, and the first one to disagree
with them. It is a join in **two directions, and neither subsumes the other** — a
`Voiceprint` records the meeting it was filed from, and a meeting's
`speaker_names` records who appears in it. The first misses everyone
`voices.identify` recognised automatically, because a recognition files nothing
new; the second misses everyone a `rerun` renumbered past without re-matching,
whose print keeps the provenance the meeting record lost. Measured on the six
meetings here: the owner has prints filed from two of them and appears in all
six. `people.directory` is that same join read per person and is what phase 5's
page and `referat people` render; `project_people` is now derived *from* it, so
there is one walk of those two files rather than two that have to agree.

**The owner is scoped into every meeting**, whatever it carries, because they
pressed the button and so were in the room. That is not an exception bolted onto
the join — it is the one honest thing this UI can say about an unnamed
*microphone* cluster, which is what `channel` is carried for, and leaving it to
the join put that name behind a *show all* on five of six projects. An untagged
meeting gets the whole gallery, which is the same rule from the other end: a
missing tag must never cost a name.

**A chip fills the name field rather than applying itself.** Two keystrokes
instead of one, deliberately. Naming somebody does not merely fix one transcript
— it files a voiceprint every later meeting is matched against, so a wrong name
spreads by itself and the way back reverts that person's labels *everywhere*. The
click that starts a name should not also be the click that commits it. The owner
leads the chips for a microphone speaker, which is presentation and lives in the
dialog; *which* people a project has met is Python's and arrives decided.

**Snippet playback is refused while a meeting is recording**, paused included,
and that one is not a UI decision. The window lives in the tray's process, so the
snippets play out of the same speakers WASAPI is looping back into `system.wav`:
a person's earlier speech would be captured into the meeting being recorded,
transcribed, diarized and rendered as if the far end had said it. The transcript
is evidence of what was said, and this is the one way a UI could quietly write
something into one. Paused as well as recording, because a paused meeting is
still being recorded — which is exactly why the recorder's state machine has a
`paused` and the meeting's lifecycle does not.

**The UI layer stays OS-portable; capture stays Windows-only.** That is a
code-layer discipline rather than a shipping target — no Win32 call and no
Windows-only Qt API inside `referat/ui/` — and it is not a promise that Referat
runs anywhere else, because WASAPI, the sleep hold and the `keyboard` hooks say
it does not.

**The command center imports; it does not shell out.** It is already a Python
process inside this package, exactly as the tray is, so it calls the same
functions the CLI calls — `cli.list_document`, `cli.show_document`,
`cli.transcript_document`, `cli.status_document`, `cli.pending`, `cli.project_names`,
`cli.project_document`, `cli.hotwords_document`, `cli.people_document`,
`cli.apply_tags`,
`cli.create_project`, `cli.rename_project`, `cli.set_description`,
`cli.set_glossary`, `cli.set_archived`, `cli.remove_project`, `cli.write_notes`,
`cli.write_day_summary`, `cli.write_recap`, `cli.recap_document`,
`cli.actions_document`, `cli.actions_markdown`,
`cli.select_actions`, `cli.owner_counts`, `cli.is_mine`, `cli.set_action_done`,
`cli.dismiss_action`, `cli.edit_action`, `cli.prune_actions`,
`cli.delete_meeting`, `cli.delete_warning`, `cli.promote_meeting`,
`cli.promote_warning`, `cli.link_doc`, `cli.unlink_doc`, `cli.sync_project`,
`cli.doc_candidates`, `audio_state`,
`meeting.format_duration`, `index.meeting_title`, `voices.unknown_speakers`,
`label.label_document`, `label.name_speaker`, `label.forget_person`,
`label.rename_person` — and spawning a subprocess of its
own CLI would buy nothing
but an interpreter start per refresh.

**The write verbs are reached through the guarded function and never through the
primitive**, and that distinction is the rule rather than a preference.
`projects.add_tags` is three lines that append to a list; what makes tagging
*correct* is everything around it — the unreadable-file guard, the unknown-id
refusal, the resolve and the single `meta.json` write — and that is
`cli.apply_tags`. A window calling `add_tags` directly would have to grow all
four, which is the second implementation the whole arrangement exists to prevent.
`label.name_speaker` is the same rule one module along, and the same box in
`TODO.md` was written the wrong way twice before it was: `label.apply_name` is a
primitive that knows the order of writes and that the *pipeline* also calls,
while the reserved-name rule, the meeting lookup, the refusal to rename somebody
who already has a name and the dashboard regeneration are the operation, and they
lived inside `run_apply` where only the CLI could reach them. The rule is one implementation, not one process boundary, which is
the same sentence said above about the tray. The `--json` documents stay and grow
anyway: they are the shape a surface and the CLI agree on, and a document you can
print is a document you can test — which is why they survived the reader they were
written for. **The
window may not read `meta.json`, `voices.json` or `projects.json` itself**, and
the day it does is the day this stops being one implementation.

**The VS Code extension was deleted at build step 23**, on 2026-09-04. Step 11
built it as a meetings TreeView, step 15 replaced that with a sidebar webview and
made it the primary UI, step 20 took that title away and put it into maintenance
on one written condition — *deleted once the command center reaches parity with
it* — and step 23 is that condition being met and then acted on. Nothing about it
runs, nothing in `pyproject.toml` ever mentioned it, and `referat-vscode/`,
`.vscode/launch.json` and `.vscode/tasks.json` are gone.

**Parity was audited against its own `runAction` switch**, not asserted. Ten row
actions; eight already had a home here — the viewer for both documents, the tag
picker, the speaker dialog, *Generate notes…*, *Delete…*, the recorder buttons
and the activity strip in place of its status bar item, the Activity tab's log
pane in place of *Show Output*, and *Open meetings folder* already on the tray
menu. The two that did not were **re-transcribing a meeting** and the
**gate-failed off-ramp**, and both are about the *audio* rather than about a
transcript, which is why they were last: everything else in this window acts on
`transcript.md`, `notes.md` or `meta.json`, and those two act on the one part of
a meeting that cannot be regenerated. They are *Re-transcribe…* and *Promote…*.

**A rerun from the window runs on the tray's own thread and not through a `cli`
function**, which is the one place this arrangement's usual answer is the wrong
one. What a rerun costs is the state machine, the job count and a daemon thread,
and a window may touch none of the three — so `App.rerun_meeting` is the
operation and `App._queue_transcription` is the one path onto the GPU that
`_finish_meeting`, `_resume` and it all share. The extension opened a *terminal*
for this, which was right for a different process and would be absurd from the
window that owns the job.

**`rerun.check` is the split, and where the line falls is the point.** It holds
the three questions about the *meeting* — it exists, its `meta.json` reads, its
audio is on disk — while `rerun.run` keeps the one about another *process*:
`busy_tray`, which exists because `transcribe._RUN_LOCK` serializes jobs inside
one process and cannot see across one, and two large-v3 models do not fit in 12
GB. The command center **is** the tray's process, where that lock serializes a
queued rerun by itself, so asking the GPU guard there would refuse the one caller
it was never about.

**`cli.promote_meeting` is the ninth guarded function**, and `cli.promote_warning`
beside it is `delete_warning`'s counterpart. `release_audio` stays a *direction*
on one function rather than becoming two, as `set_archived` and `apply_tags`
already are: the bare form is the same operation with the deletion refused. What
the warning has to say that `delete_warning` does not is that promoting is
irreversible in a way deleting a whole meeting is **not** — deleting takes the
transcript with the audio, and this keeps the transcript and destroys the only
material it could ever be re-derived from, on exactly the meeting whose transcript
the gate was not confident in.

**Three things it taught outlived it**, and each is cited where it landed rather
than left in a deleted file: build every node with `textContent`, which is
`referat/ui/viewer.py`'s escaping rule and the reason a transcript's text and a
speaker's typed name are data; **a picker diffs against what the meeting carries
and never against the set it mutates**, which is `referat/ui/tags.py`'s two sets
and which the sidebar's picker got wrong the first time somebody used it; and **a
refusal reaches the user in the words of whatever owns the rule**, which its
`mutate` achieved by reading the CLI's stderr and which `cli.Outcome` now does in
process. The guarantee was the part that mattered, not the mechanism.

**Every `--json` document stays**, and each was written for a reader that no
longer exists. They are the shape a surface and the CLI agree on, a document you
can print is a document you can test, and they are what anything scripting this
from outside Python reads — as are `label`'s four non-interactive flags, which
the command center does not need because it calls `label.name_speaker` directly.
Deleting them would have been the mistake this step could have made.

**`notes.resolve_claude` still reads `~/.vscode/extensions`**, and that is the
**Claude Code** extension rather than this one. The two are easy to confuse in a
sweep and must not be: `claude` is not on `PATH` on this machine and ships inside
that extension under a version that changes weekly, which is why that lookup
honours the `.obsolete` file and resolves at spawn time.

## Per-project digests (build step 13)

**Built on 2026-09-04**, and it had been the one genuinely lagging build step:
forty unchecked boxes and both gates met since 2026-09-01. A digest block is a
meeting's `notes.md` translated into a Google Doc. `referat/digest.py` is the
translator and the diff, `referat/gdocs.py` is the client, and
`referat/markdown.py` is the subset both it and `ui/richtext.py` read.

**The consent was given on 2026-09-04**, at a terminal through the paste flow,
and the token lives at `[paths].google_token_file`; four projects carry a doc and
three meetings had reached `synced` by that afternoon. This paragraph said the
opposite for half a day after it stopped being true — a written claim about
what has run is a fact with a date on it, like the `createTab` note below.

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

**Auth binds no socket, and that was a rules question rather than a technical
one.** The standard desktop OAuth flow is `InstalledAppFlow.run_local_server`,
which starts an HTTP server on loopback to catch the redirect — and Conventions
forbid exactly that, with the test stated as *whether something binds a socket*.
Google's out-of-band flow was shut off in 2022, so `gdocs._consent` builds the
consent URL, opens it, and reads back the address the browser fails to load; the
code is in its query string. Ten lines instead of one, and no exception spent on
a rule this codebase has been careful about. It bought a second property worth
keeping: the flow needs a terminal, so **the command center can never
authenticate**, and a window that popped a browser consent from the thread that
owns the recorder is structurally impossible rather than merely avoided.

Two scopes and no more: `documents`, and `drive.metadata.readonly` for the *select
existing doc* search, which returns names and ids and no file content.
**`drive.file` is not sufficient** and was refused rather than chosen — it sees
only files this application created, so on a fresh install the picker would find
nothing at all. `[paths].google_client_secret_file` and `google_token_file`
follow the `hf_token_file` precedent, and the difference between them is worth
keeping: the first describes the *application* and the second is a credential of
yours, which is why deleting the second is how you sign out.

**`referat/markdown.py` exists because two things render a `notes.md` and they
must not disagree.** A person pastes part of one into a Google Doc and
`project sync` pushes the whole of one into the same kind of doc; if those ever
answered a question differently — whether a `[[Wikilink]]` keeps its brackets,
whether a relative link stays a link — the same note would arrive in the same
document looking like two notes depending on how it got there. `ui/richtext.py`
imports PySide6 at module scope and lives in the one sub-package, so `digest.py`
could not reach it without inverting the layering. The *grammar* moved to a flat
module; each renderer kept its own walk over it, because HTML nests and Docs
styles flat ranges and flattening them into shared tokens would have changed what
a paste produces. **What stops them drifting is a check rather than an
abstraction**: `scripts/digest_report.py` renders every real note through both
and compares, and the extraction was verified byte-identical over all fourteen.

**Three things the design did not foresee.** `insertText` **inherits** the style
at the insertion point, so every batch resets what it just inserted before
styling any of it — without that, a block inserted before an existing anchor
arrives small and gray. The **acceptance check is per paragraph** against the
kind `blocks` assigned it, not a flat scan: `HEADING_RE` matches one to three
hashes, so a `####` line is documented to fall through to a paragraph carrying
its own hashes, and a flat "no leading `#`" would turn that documented behaviour
into an error. And **`synced` needed a way back**: `cli.set_notes_written`
accepts it and drops to `notes_written`, which is the one edge in the lifecycle
that runs backwards, written from the function that *knows* the notes just
changed rather than derived later from a sha, since that would be inferring a
lifecycle from the filesystem.

**The note's own `#` H1 is dropped from the block** — the Heading 3 date line
carries that exact string already, from the same `index.meeting_title` that
`referat index` uses, so every block would otherwise announce its title twice.
The byline's relative `[transcript.md](transcript.md)` renders as plain words and
is otherwise **left alone**: dropping the clause would be `digest.py` editing
somebody's prose, which is a larger thing than an odd-looking line.

**`tabId` is checked mechanically**, in `gdocs._require_tab_ids`, at the one
place every write passes through — a request without one silently targets the
first tab, which is a meeting written into somebody's unrelated notes with no
error to notice. `find_tab` recurses `childTabs` for the same class of reason: a
nested `Meetings` tab missed by a flat scan does not produce an error, it
produces a *second* `Meetings` tab.

`<meetings_dir>/projects.json` holds the project list and the link state, owned
by `referat/projects.py` since step 14 — deliberately not by `config.py`, whose
promise never to write TOML back is about `config.toml` and stays true of it. It is JSON rather
than the TOML this file used to specify, which drops that tension instead of
managing it: it goes through the `paths.write_json_atomic` that already exists,
it is rewritten whole without apology, and it needs no header comment warning
that hand-written comments will not survive. Per project it holds an `id`, a
display `name`, a list of doc references, and a one-line `description` — reserved
for step 17's notes splitting, and written since step 20's phase 4 by `referat
project describe`, the field having arrived before its consumer.

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
something outside step 13 depends on. `referat project glossary` writes it, and
so does the command center's projects page through that verb's own function; the
terms are cleaned on the way *in* — whitespace collapsed, blanks dropped,
deduplicated case-insensitively with the first spelling winning — so the file is
written in the shape `hotwords.collect` will read it in.

**A tab is found by its id, so renaming one is safe.** `DocRef` stores `tab_id`,
which Docs mints once and never moves; the `tab_name` beside it is display text
and a sync **corrects it** when it has changed rather than going on saying what
the tab was called the day it was linked. Only *deleting* a tab breaks a link,
and that is what the refusal says. The correction is suppressed under
`--dry-run`, which was a real bug for one commit: a dry run recorded the new name
while reporting that it had changed nothing, and a flag that is trusted before a
network write has to be true for the harmless-looking write too.

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

**A block is bracketed, and that is what lets it live in a document somebody
writes in.** Each one runs `[referat:<id>]` ... `[/referat:<id>]`, both small and
gray, and a sync only ever replaces what is between a matching pair. The closing
anchor was added on 2026-09-04, the day this met real documents, and it is worth
saying why the original design was wrong rather than merely amending it: a block
that ran from its own anchor to the *next* one meant the last block in a tab
owned everything down to the end of it, so anything a person wrote underneath a
digest was deleted on the next re-render. That is survivable only in a tab
Referat owns outright — and the documents here are the opposite, being somebody's
meeting notes, already tabbed, already named for their contents, with a hundred
and thirty thousand characters of prose in one of them. A block with no closing
anchor is therefore treated as **stale whatever its sha says**, so one sync
upgrades it and the old format converges rather than needing a migration.

**The `Meetings` tab is a default and not a requirement.** It assumed a document
Referat could have to itself; the real ones are called `Fall 2026` and
`Meeting Notes` and one has ten tabs, none of them `Meetings`. So `--tab` names
any tab by title or by id, and `--doc` takes a **share link** — the address bar
or the Share button, whose `?tab=` says which tab you were looking at, which is
the shortest correct way to answer both questions at once. What has **not** moved
is that the tab is chosen and never guessed: there is still no fallback to the
first tab, not even in a single-tab document, because that is not an ambiguity
about which tab but a question about whether a digest belongs in the middle of
somebody's prose. A refusal lists the tabs that exist, since naming one is only
reasonable if something says what they are called.

**Linking is create-or-select, and the tab is the awkward half.** `referat
project link-doc <id>` *appends* a doc reference and `unlink-doc <id> <gdoc_id>`
removes one, since a project may carry several. *Create new
doc* calls `documents.create` titled `<Project> Meeting Digest`, **renames its
tab** to `[digest].new_tab_name` and gives it a `TITLE` line — the one case where
writing a heading is not touching somebody else's prose, since the document is a
second old and Referat made it. *Select existing doc* searches Drive with
`files.list` filtered to Google Docs by name, or takes a pasted link, and then
picks a tab.

**"Tabs cannot be created through the API" was true and stopped being true.**
Four files said so, on the strength of there being no `createTab` request; the
API has since gained `addDocumentTab`, `deleteTab` and
`updateDocumentTabProperties`, which was found by listing the request types
rather than by reading the note again. So a document with no suitable tab is no
longer sent to a browser: `--new-tab`, or a checkbox in the dialog, adds one.
Never automatically — adding a tab is a visible change to somebody's document,
which is the same rule that stops one being *picked* automatically. This is the
general case of a claim about somebody else's API being a fact with a date on it. Either way `gdoc_id` and `tab_id` are stored, and **every write is
located by that `tab_id`**: `documents.get` always passes
`includeTabsContent=True`, and every `batchUpdate` request carries `tabId` in
its `Location` or `Range`. A request without one silently targets the first tab,
which would write a meeting into somebody's unrelated notes with no error to
notice. Referat never writes into any other tab of a linked doc.

**A sync happens by itself unless a project says not to.** `auto_sync` is on
every project, defaults on, and is what `cli.write_notes` consults after a
cleanup pass: `cli.auto_sync_meeting` pushes that meeting into every linked,
auto-syncing project it carries. It is the **third step** of an operation whose
first two are spawning `/cleanup` and recording `notes_written`, and it runs
under that function's existing rule — a document that did not update is a line in
the message, never a failed cleanup, because `notes.md` is on disk whatever
Google says. Silent when the `digest` extra is not installed, since that is a
machine that does not do digests rather than an error. An **archived** project
still auto-syncs: archiving hides a project from the tag picker and changes
nothing else, and a digest that quietly went stale would be archiving costing
something real. `referat notes --no-sync` and `project auto-sync <id> off` are
the two escapes, and *Sync now* works while it is off.

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

**A block's heading level is `[digest].heading_level` and defaults to 1**, so
the date line is Heading 1 and `notes.md`'s own `##` and `###` land as Heading 2
and Heading 3. It was Heading 3 unconditionally until 2026-09-04, on the
reasoning that a block sits inside a document with its own outline above it —
true of a doc where Referat is a guest, and wrong for the ordinary case where it
has a tab to itself and the result was a document whose outline started three
levels down. `[digest].newest_first` is the other presentation knob: it decides
where a **new** block is inserted and never moves an existing one, because
rearranging a document somebody may have written between two blocks is a larger
thing than adding to one — a sync that finds them running the other way says so
and leaves them. `sync --rerender` is how a change to either reaches blocks
already written, since none of their notes moved and nothing else would notice.

**Each block opens with a heading line, `YYYY-MM-DD — <title>`**, the title
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

Four more live at the **folder** level rather than inside a meeting, beside
`projects.json` and the generated `INDEX.md`; the last row arrived with build
step 24 on 2026-09-08, the same day it was planned:

| File            | Contents                                                    |
| --------------- | ----------------------------------------------------------- |
| `actions.json`  | what has been done about the action items in the notes       |
| `days/`         | `YYYY-MM-DD.md`, one glance per day, written by `/standup`   |
| `PEOPLE.md`     | who Referat knows by name, for `/cleanup`. Generated with `INDEX.md` |
| `recaps/`       | `<project-id>.md`, one brief per project, written by `/recap`; `<project-id>.bundle.md` beside it only while a pass runs |

None of them is ever inside a meeting folder. `actions.json`, `days/` and
`recaps/` are excluded by name from `paths.list_meeting_dirs` even though none
of them holds a `meta.json` and so none could pass its gate — `.voices/` is
named there for the same reason, which is that a rule belongs where somebody
would break it.

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
(`SPEAKER_NN` to the **id** of the person it was resolved to — the bare name, in
a meeting written before build step 20c, which resolves to the same id), `referat_version`. The per-channel
`speakers` block maps each `SPEAKER_NN` to its embedding, its snippet offsets,
the `name` it resolved to, and the `match` that decided — recorded even when it
was refused, because the near-misses are the only material for calibrating the
thresholds. `speaker_names` is the authority on who a label is; the per-channel
`name` follows it, and `referat label --forget` clears both. Both hold the
person's id since step 20c, and `match.name` does too; `transcript.md` holds the
short name, because it is prose.

The `transcription` block also carries `bleed`, what `referat/bleed.py` decided:
the status, and **every** microphone cluster with the two coverages that judged
it, kept ones included. The kept ones are the point — they are the only material
that will ever calibrate `[bleed].cluster_time` and `cluster_text`, exactly as
each speaker's `match` is recorded even when it was refused. The settings are
recorded beside the verdict, so a transcript suppressed under old thresholds is
distinguishable from one suppressed under new. Nothing about it is written into
`transcript.md`: that file is prose about what was said, and this is the pipeline
saying what it did.

Two more keys in that block are what the *repairs* did rather than the pipeline,
and both are records of deletions kept so the deletion can be read back:
`debleed` — `{at, settings, removed: [...]}` — and, since step 20b, `noise`,
keyed by label, each `{at, channel, removed: [{at, text}, ...]}`. Both
`removed` lists accumulate across passes. The per-channel cluster a `noise`
entry refers to also carries `noise: true` beside where `echo: true` would be,
and both flags mean the cluster is never offered a name.

`digest` is what has been pushed into which Google Doc, **keyed by `gdoc_id`**,
each entry `{tab_id, notes_sha256, written_at}`. Keyed by doc rather than being
one flat object because a meeting fans out across every doc of every project it
carries and can be current in one and stale in another, which one object could
not say. `notes_sha256` is what makes reconciliation cheap: a sync needs
`meta.json` plus one `documents.get` per doc and never re-reads the doc's prose
to work out what changed. `digest.is_synced` is the predicate behind the `synced`
state, derived on every read and stored nowhere — an orphaned tag contributes no
docs and is vacuously satisfied, and an **archived** project's docs count exactly
as a live one's, because hiding is presentation and never a constraint.

The `transcription` block gains `interrupted` — `{at, why, after_seconds}` —
when a run was abandoned rather than finished, and a clean run drops it again by
rebuilding the block whole. Note also that starting a run **merges** into that
block rather than replacing it: the previous run's `model`, `seconds`,
`audio_released` and `audio_kept` stay true of the files in the folder until this
run overwrites them, and discarding them up front was how an interrupted rerun
destroyed the record of a run that had succeeded.

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

**An interrupt is not a state.** A Ctrl+C out of a transcription restores the
status the meeting arrived with and records `transcription.interrupted`; it does
not write `failed`, which means *the transcription raised* and is a verdict on
the material. Nothing on disk changed — the transcript, the names and both WAVs
are the ones that were there when the run began — so the state that described the
folder before is the state that describes it now: a first run goes back to
`recorded`, an interrupted `rerun` back to `transcribed`. This needed saying
because `KeyboardInterrupt` is a `BaseException` and walked straight past the
failure handler until 2026-09-02, leaving a good meeting stuck at `transcribing`
with an emptied `speaker_names`, which `referat delete` then refused to touch and
every listing rendered as a lie. The one value never restored is `transcribing`
itself — it is what a process killed outright leaves behind — and it clamps to
`recorded`. **A hard kill can still leave it**, and no handler inside a process
can promise otherwise — so since 2026-09-03 the promise is made from the other
end instead. `meeting.reconcile_interrupted` runs at tray startup: jobs are
threads inside this process, so a meeting still claiming `transcribing` when no
live tray is transcribing was abandoned by a kill, a crash, a power loss, or a
resume the CUDA context did not survive, and is clamped to `recorded` with
`transcription.interrupted` recording why. Guarded on `rerun.busy_tray` — the
existing answer to *is another process working on this*, reused rather than
restated — and run **before** the tray writes its own `status.json`, or this
process would be the one answering that question.

The residue was worth closing because the lie is sticky: a meeting stuck at
`transcribing` is refused by `referat delete`, drawn as in-flight by every
surface that renders the lifecycle, and counted as busy by `busy_tray` itself, so
the one command that would repair it is the one it blocks.

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

## Archiving a project (build step 21)

**A project can be finished, and that is different from being a mistake.** Until
step 21 the only tool for a thread of work that had ended was `project rm`, which
cascades nothing — so every meeting it tagged kept the id as an orphan, and the
record of what those meetings were about was lost to make a picker shorter.
`archived_at` in `projects.json` is the other answer, and **it is a presentation
decision and the whole of it**: the entry stays in the file, every meeting keeps
its tag, that tag still resolves to its display name everywhere, the glossary
still feeds the hotword list, and `referat tag` will still add it. What stops is
the *offering*.

**A timestamp and not a flag**, because `""` is already this file's falsy absence
— `created_at`, `description`, `DocRef.tab_id` — so `bool(project.archived_at)`
is the predicate and there is no second field to disagree with the first. A flag
beside a date would be two places saying one thing; a flag instead of one throws
away when the work stopped. **Re-archiving does not move the date**, which is the
same rule that stopped `rerun` clearing `speaker_names`: re-running a command may
not rewrite history.

`SCHEMA_VERSION` stayed at 1. `Project.from_json` already defaulted a missing key,
so a file written before this step loads with every project active — which is
*true* of it, and a version bump would have asserted a migration that does not
exist. The one lossy direction is an older `to_json` dropping the key on its next
save, which loses which projects were archived and nothing else: no orphan, no
glossary, no meeting. **Move the version when a key changes meaning, not when one
is added.**

**`name_map()` is never narrowed, and that is the rule this feature is built
around.** It is tempting to have archived projects simply fall out of the map and
let every surface hide them for free; it would be a bug, and a quiet one.
`cli.tags_cell` and `ui.rows.tags_text` render an id *missing* from that map with
a trailing `?`, because that is what an orphan is — so a narrowed map would turn
every archived project's tags into orphans in `referat list`, in `list --json`,
in the meetings list, on the dashboard and on the people page, all at once,
which is precisely the quiet disappearance this codebase keeps
designing against. Archivedness therefore travels *beside* the map, as
`ProjectsDB.archived_ids()`, and that sentence lives in that method's docstring
where the next person to have the idea will read it.

The consequence was that **the VS Code extension needed no change at all** — a
frozen surface that needed zero work is what a presentation-only change looks
like from the outside. That was the last thing ever said about it; it was deleted
the next day.

**Only two documents changed, and one of them for free.** `project_document`
spreads `**project.to_json()`, so `archived_at` arrived with no code; that is the
payoff of the dataclass being the schema rather than an accident to leave
unremarked. `people_document` gained `archived` and a per-person `inactive`.
`list_document`, `show_document`, `pending` and `actions_document` gained
nothing, on the rule that **a document gains a field only where something renders
a difference** — `pending`'s queues are about meetings, and archiving a project
makes no meeting more or less untagged.

**`referat tag` still adds an archived id, deliberately.** `apply_tags` refuses
an *unknown* id for a reason written beside it — an orphan should be made by
deleting a project rather than by mistyping — and an archived id is not an
orphan. Refusing would widen that guard past its own recorded reason and would
make a late meeting for a finished thread of work cost three writes: unarchive,
tag, re-archive. The picker simply does not offer it, which is a different thing.
The note naming the archived tag lives in `apply_tags` rather than in `run_tag`,
because a `run_*` holding a rule is a `run_*` a second surface cannot use, and it
is computed inside the existing `if add:` block so that a pure removal still
never opens `projects.json`.

**A person is inactive when every project they carry is archived, and it is
derived on every read and stored nowhere.** There is no `inactive` key in any
file and there must not be: it would be a fifth thing to keep in step with
`meta.json`'s tags, the voices database and the transcripts, and the first one to
disagree with them. It is computed in `cli.people_document`, which already loads
the projects file for its `name_map()`, and **not** in `people.directory` — that
module deliberately never opens `projects.json`, and `people.gallery` is built on
it through `project_people`, so archivedness introduced there would reach the
speaker dialog's scoping, which is the one place it must never go. `gallery` is
unchanged: a missing or archived tag must never cost a name.

**There are three ways to be active and all three are one principle** — an
unknown must never be read as an ending. No tags at all is active, because no
project is not a finished one. An **orphaned** tag is active, because that says
the project record is gone rather than that the work stopped. And somebody seen
in an **untagged meeting** is active even when every tag they do carry is
archived. That last one is why `people.Person.in_untagged` exists: `tags` alone
cannot tell a person seen in one archived project and one untagged meeting from
one seen only in the archived project, since an untagged meeting contributes no
id to compare. It lives in `people.py` because it is a fact about `meta.json`
alone and costs that module no new file. It was found by driving the feature —
the first run called somebody inactive who had been in an untagged meeting that
morning — and it reads a *missing* meeting differently from an untagged one, or
deleting a meeting would quietly reactivate everybody who was in it.

**In the window, archiving is a button and deleting is still a modal.** The
projects page's title row runs Rename, Archive…, Delete — harmless, reversible,
destructive and last. Archiving asks first, in a modal saying what it does *not*
do; unarchiving asks nothing, because nothing was lost. The list grows an
**Archived** section between the live projects and the orphans, ordered by how
much of a project each thing is, and its heading is unselectable while **its rows
are not** — you select one to unarchive it, and everything in the form still
edits it, the glossary above all, since that still feeds the hotword list.

**That section folds and starts folded**, since a finished project is the one
somebody is least likely to have come to the page for — done with `setHidden` on
the `QListWidget` rather than by moving to a `QTreeWidget`, which is the tag
picker's own rule about hiding rather than rebuilding. Its heading is *enabled but
not selectable*, the one flag between it and the inert orphan headings, because
`QListWidget` sends no click to a disabled row. It opens itself in the two cases
where staying folded would lie about the page: when the selected project has just
been archived, and when there are no live projects at all.

The tag picker hides an archived project it would *offer* and keeps one the meeting
**carries**, ticked and removable, under its real name and `(archived)` rather
than the orphan suffix, which would be a lie about a project that exists. That
cost nothing to build: `_carried` and `_checked` do not know about archiving and
do not need to. The people page grows an **Only archived projects** section above
the drifted one, since drift is the database disagreeing with itself and is the
more urgent of the two; the heading names the fact rather than calling somebody
inactive, which is the register that page's privacy note is in.

**Glossaries still feed the hotword list, archived or not.** A glossary is read
twice at two different times, and the first is *before any meeting has been
tagged* — so an archived project's terms are exactly as likely to be said in the
next meeting as they were last month, and dropping them would make archiving cost
a transcription. Worth revisiting only when the 223-token cap is actually seen
dropping something, which is what the projects page's hotword panel is for.
Meeting rows are likewise **not** decorated: `?` marks that something
disappeared, and nothing disappears here.

## Conventions

- **Windows only, except that the UI layer stays portable.** No cross-platform
  abstraction layers, no `if sys.platform` branches for other OSes; WASAPI,
  `%LOCALAPPDATA%` and Win32 calls are fair game in the recorder, the sleep hold
  and the hotkeys, and used directly. The one part held to a different standard
  is `referat/ui/`, which carries no Win32 call and no Windows-only Qt API. That
  is a code-layer discipline and **not a shipping target**: it keeps the UI from
  ever being the reason a port is impossible, and it is not a promise that
  Referat runs anywhere but Windows, because everything below it says otherwise.
- **One package**, `referat/`. Flat modules, and one sub-package: `referat/ui/`,
  the command center. That is a deliberate exception taken at step 20 rather than
  drift — a GUI is a dozen modules and flattening it would make the package
  unreadable — and it is the only one. Nothing else grows a sub-package.
- **Type hints throughout**, `from __future__ import annotations` at the top of
  every module.
- **Minimal dependencies.** Base install is tray + audio + UI; the ~3 GB
  transcription stack lives behind the `transcribe` extra. There is one
  deliberate exception in the base install, recorded as an exception rather than
  left to look like an inconsistency. (Step 16's WinRT toast library would have
  been a second; step 16 is declined, so it is not taken — see Architecture.)
  Step 20's **PySide6-Essentials**, which is the command center and, because Qt
  owns the tray icon, is now on the recording path — see the Smart App Control
  paragraphs in Architecture for the audit that gated it and cleared it.
  *Essentials* and not the full `PySide6`: QtWebEngine lives in `PySide6-Addons`
  and is refused there on its merits, so the base install never carries it. It
  replaced `pystray` and `pillow`, which are gone — the exception is a
  substitution rather than an addition, and the base install got one dependency
  smaller in count while getting larger on disk. Step 13's **`digest`** extra is
  the second optional one after `transcribe`: tens of megabytes rather than three
  gigabytes, needed by exactly three verbs, and it adds two unsigned native files
  that sit **off the recording path**, so a Smart App Control block there costs a
  digest push and nothing else.
- **Nothing is inferred from a transcript.** No keyword rules, no guessing which
  project a meeting belongs to, nothing tagged at the end of the pipeline. That is
  what keeps the untagged meetings a queue somebody works through rather than a
  bucket of quiet mistakes. Step 17's notes-splitting experiment is the place this
  is most tempting and it does not bend there either: the human's tags are an
  *input* to the split, never an output of it.
- **Hiding is presentation and never a constraint.** A surface may narrow what
  it *offers*; nothing may narrow what is stored, what resolves to a name, or
  what a verb will accept. The speaker gallery narrows what a human is offered
  and never what the pipeline matches; an archived project drops out of the tag
  picker and stays in `projects.json`, keeps resolving through `name_map`, keeps
  feeding the hotword list, and is still accepted by `referat tag`. A missing tag
  must never cost a name, and an archived project must never cost a tag.
- **No UI infers a meeting's state from which files exist.** `meta.json`'s
  `status` is the lifecycle, every surface renders that field, and a state that
  nothing writes is a state that does not exist.
- **Fully offline.** No telemetry, no cloud calls, no analytics. The only
  network access is downloading models on first use. Recording and transcription
  never leave the machine. There are exactly two deliberate exceptions: the
  cleanup pass, and step 13's digest push, which contacts Google only for
  projects that have been explicitly linked and only for meetings that have been
  explicitly tagged. **The second is now standing rather than per-push**, since
  `Project.auto_sync` defaults on and a push follows a cleanup pass by itself.
  That is an amendment to *only run when the user asks*, recorded as one: the
  asking moved to linking the document, which is a far more deliberate act than
  pressing sync afterwards, and nobody links a project to a digest doc and then
  wants the doc to be stale. What it does **not** widen is what leaves the
  machine, which is `notes.md`, for explicitly tagged meetings, into explicitly
  linked docs, and nothing else. Per project rather than global, because the
  answer differs by document — one shared with a room full of people is exactly
  the one somebody wants to read before it updates itself.
- **Nothing but notes leaves the machine.** The digest sends `notes.md` and
  nothing else — never `transcript.md`, never the audio, never `.voices/`, never
  a speaker embedding. This is its own rule rather than a detail of step 13,
  because it is the line somebody crosses by accident the first time a doc
  "should really have the exact quote". Step 24's `recaps/` is derived from
  notes and is still not notes: it never leaves the machine, and the digest
  push does not carry it.
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
- **No web server, and no Electron.** This rule used to read *no web UI, ever*
  and was relaxed at step 20; the two things it was actually aimed at have not
  moved an inch. Still forbidden: **any localhost server** — Flask, FastAPI,
  anything binding a port — and **Electron**. Now permitted: a **desktop window
  whose content is an embedded full-window web view**, a window this machine owns
  that serves nothing and listens on nothing. The test is whether something binds
  a socket, not whether something renders HTML — which is why a VS Code webview
  was always on the right side of the line, and why the command center is too.
  Anything a person needs to look at or click is a tray menu item, something in
  the command center, or Markdown rendered in an editor. **Step 13 honoured this
  rather than taking an exception to it**: Google's desktop OAuth flow wants a
  loopback HTTP server, so Referat uses a paste flow that binds nothing — see the
  digests section. The rule cost ten lines and bought the property that no window
  can ever ask for consent.
- **No Anthropic API key, ever.** All LLM work goes through the official
  `claude` binary under the user's Claude Code subscription login. No module in
  this project may call the Anthropic API directly, no key belongs in
  `config.toml`, in the environment, or in the command center, and no Anthropic
  SDK belongs in `pyproject.toml`. Referat spawns `claude` as a child process and
  handles no credential.
- **Voiceprints never leave the machine.** The known-voices database is not
  synced, not backed up, not readable by the `/cleanup` pass, and never sent
  anywhere. Deleting a person deletes them. **That protection is also why it
  needed its own backups**, added on 2026-09-04: excluded from sync and from
  every backup means a bad write has nothing to restore from, so `VoicesDB.save`
  keeps the last fifteen copies in `<voices_dir>/backups/` — inside the folder,
  where they inherit the same exclusion, and never anywhere a sync client can
  see. It also carries the `unreadable` flag `ProjectsDB` and `ActionsDB` have
  had since step 14: an unparseable file loads as *nobody is known*, which is
  right for reading and would make the next save replace everybody with one
  entry. Five write paths refuse and `save` refuses as a backstop, because the
  worst caller was never a person at a prompt — `bootstrap_owner` runs inside
  the transcription pipeline with nobody watching.
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
