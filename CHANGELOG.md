# Changelog

Newest first. One entry per work session; small changes are grouped.

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
