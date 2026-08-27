# TODO

The build order for Referat. Check items off as they complete; **never delete
completed items**. Add new tasks as they surface, under the step they belong to
or under "Surfaced later".

## 1. Repo skeleton

- [x] Package layout (`referat/`, flat modules)
- [x] `pyproject.toml` with base deps, `transcribe` extra, `referat` entry point
- [x] Python 3.12 pin and `cu128` torch index for the Blackwell GPU
- [x] Config loading (`config.example.toml`, `config.py`, first-run bootstrap)
- [x] `paths.py`: meeting folder contract, state dir, atomic JSON writes
- [x] `logging_setup.py`: rotating file log, console handler when stderr exists
- [x] `CLAUDE.md`, `INDEX.md`, `CHANGELOG.md`, `TODO.md`, `README.md`
- [x] `templates/meetings-CLAUDE.md` for the meetings folder
- [x] `git init` and first commit
- [x] `uv sync` and verify `uv run referat config`

## 2. Tray app with state machine and hotkeys

- [x] `state.py`: idle → recording → (paused ↔ recording) → stopped →
      transcribing → idle, with legal-transition enforcement
- [x] Log every state transition
- [x] `hotkeys.py`: register the two configurable combos via `keyboard`
- [x] `tray.py`: `pystray` icon, colors per state (gray idle, red recording,
      yellow paused, distinct transcribing state)
- [x] Tray menu: open meetings folder, open config, quit
- [x] Write `status.json` on every transition (for `referat status`)
- [x] `referat-tray` gui-script entry point in `pyproject.toml`
- [ ] Verify the programmed USB buttons drive the state machine (no audio yet)
- [x] `status.py`: write and read `status.json`, so `referat status` has a
      format to read at step 8
- [x] Single-instance mutex, so an autostarted tray and a manual launch cannot
      fight over the same hotkeys
- [x] Debounce the hotkeys — a held USB button auto-repeats and would
      otherwise start and immediately stop a recording

## 3. Dual-stream WAV recording

- [x] `meeting.py`: meeting record and `meta.json` read/write
- [x] Mic capture via `sounddevice`, mono 16 kHz, streamed to `mic.wav`
- [x] System audio via WASAPI loopback (`PyAudioWPatch`), downmixed to mono,
      streamed to `system.wav` at the device's native mix rate
- [x] Device selection by config substring, falling back to the defaults
- [x] Crash-safe writes: incremental WAV header updates, periodic flush
- [x] Pause stops appending samples; pause intervals recorded in `meta.json`
- [x] Verify a killed process mid-meeting still leaves playable WAVs
- [x] Pad silence into both channels against a recording clock — Windows
      delivers no loopback frames while nothing is playing, so without it every
      quiet stretch would vanish and shift all later speech earlier
- [x] Keep recording on one channel when the other device will not open; only
      fail the meeting when neither will

## 4. Sleep prevention

- [x] `power.py`: `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`
      via `ctypes`
- [x] Held only while recording or paused; released on stop
- [x] Transcription runs without the hold
- [x] Own the hold on a dedicated keeper thread — the flags are per-thread, and
      transitions arrive on the `keyboard` hook thread, the pystray thread and
      transcription threads, so setting on one and clearing from another would
      leave the machine awake forever
- [ ] Verify with `powercfg /requests` — needs an elevated shell, so it is still
      a manual check; see "Surfaced later" for what was verified instead

## 5. Transcription — mic channel

- [x] `transcribe.py`: `faster-whisper` large-v3 on CUDA, background thread
- [x] Replace the four-second sleep that stands in for transcription in
      `tray.App._transcribe`, keeping it on its own thread so a new meeting
      can still start while it runs
- [x] Graceful fallback to CPU + medium when CUDA is unavailable
- [x] Write `transcript.md` from the mic channel alone, `ME` labels
- [x] Timestamps as elapsed `[HH:MM:SS]`, pauses excluded
- [x] Record model, device, and compute type into `meta.json`
- [x] Delete `mic.wav` and `system.wav` once a meeting has transcribed cleanly,
      judged by faster-whisper's `avg_logprob`, `compression_ratio` and
      `no_speech_prob`; keep the audio when the transcript looks garbled or
      uncertain. Recording is ~460 MB an hour, so this is what keeps the
      meetings folder from growing without bound.
      **Written and tested, but deliberately dormant**: the gate is *every*
      channel in `meta.json`, and `system.wav` has no transcript until step 6,
      so nothing is deleted yet. It starts reclaiming disk by itself when the
      loopback channel lands — see the step 6 box below
- [x] Decode the WAVs directly rather than through faster-whisper, which only
      decodes via PyAV — blocked on this machine by Smart App Control
- [x] Serialize transcription jobs on a process-wide lock, so two overlapping
      meetings cannot try to hold two large-v3 models in 12 GB of VRAM

## 6. Loopback channel and merge

- [x] Transcribe `system.wav` (resample to 16 kHz — `transcribe.decode_wav`
      already does this, verified on a real 48 kHz `system.wav`)
- [x] `merge.py`: interleave both channels chronologically, then render through
      the existing `transcribe.render_transcript`
- [x] `REMOTE:` labels for the loopback channel at this stage
- [x] Confirm `transcribe.release_audio_if_clean` now actually fires: with both
      channels transcribed cleanly it must delete both WAVs and set
      `audio_released`. It has only ever been exercised on a synthetic meeting.
      **It fires**, on the real 2026-08-27_1906 call: 65 MB reclaimed
- [x] Watch memory on long meetings: an hour of `system.wav` decodes to ~690 MB
      of float32 before resampling. Decode in chunks if it bites — it does, so
      `decode_wav` now resamples chunk by chunk with an overlap-save margin.
      Measured on the real `system.wav`: peak 1728 MB per meeting-hour down to
      about 230 MB, output bit-identical to the one-shot call
- [x] An empty channel cannot simply block the audio release. A meeting held in
      person or over a phone leaves a `system.wav` of silence and notification
      chimes, which is most meetings — the gate is now faster-whisper's own
      `duration_after_vad`, so a channel with no voice in it at all counts as
      clean. Peak amplitude was tried first and is wrong: one chime defeats it
- [x] Keep the good channel when the other one cannot be transcribed at all —
      a mic-only `transcript.md`, status `failed`, the per-channel error in
      `meta.json`, both WAVs kept for `referat rerun`

## 7. Diarization

- [x] `diarize.py`: `pyannote.audio` on `system.wav`. **Not
      speaker-diarization-3.1**: the installed pyannote is 4.0.7, whose
      `SpeakerDiarization` eagerly loads a PLDA from
      `speaker-diarization-community-1`, so the old checkpoint would have needed
      that repository anyway. New `[transcription].diarization_model` key
- [x] Hugging Face token read from the configured file; document the setup step
- [x] Assign `SPEAKER_01`, `SPEAKER_02`, ... by aligning turns to segments —
      max overlap wins, then renumbered by first appearance in the meeting
- [x] Degrade to `REMOTE:` when no token is present, without failing the run.
      Also when diarization is off, when the model name is empty, when the
      gated repo returns 403, and on any exception at all: `diarize()` never
      raises, because an exception escaping it on the CUDA attempt would make
      `transcribe_meeting` re-transcribe the whole meeting on the CPU
- [x] Skip the pipeline on a channel that transcribed to nothing, distinguishing
      `no voice in the channel` from `nothing was transcribed`
- [x] `torchcodec`'s bundled FFmpeg was expected to hit the Smart App Control
      wall here. It does not: pyannote only decodes when handed a *path*, and it
      is handed an in-memory waveform instead
- [ ] Diarization has only been checked against two Windows TTS voices, which
      are cleanly separated and never overlap. Real speakers talk over each
      other and sound alike; check the accuracy on a real Zoom or Teams call

## 7b. Speaker identification — known voices

Step 7 gives each remote speaker a number. This step gives them a name, learned
from meetings that have already been labeled. **It replaces the enrollment
design**: there are no separate "say a few words" recordings, because every
meeting already produces exactly what an enrollment session would — a diarized
cluster per speaker, with the embedding pyannote computed in order to build it.

It is only as good as the clustering underneath it, though, so step 7's open
item — checking diarization accuracy on a real call rather than on two TTS
voices — comes first. A cluster holding two people produces an embedding of
neither.

- [ ] `referat/voices.py`: the known-voices database at
      `<meetings_dir>/.voices/voices.json` — a name mapped to a list of
      embeddings, each stamped with the meeting id, cluster label and date it
      came from, so a bad entry can be traced back and pulled out. Written
      through `paths.write_json_atomic` like everything else
- [ ] Harvest the embeddings diarization already computes.
      `DiarizeOutput.speaker_embeddings` in pyannote 4 is a
      `(num_speakers, dimension)` array of clustering centroids, ordered by
      `speaker_diarization.labels()`. Two traps: that order belongs to
      `speaker_diarization`, while `diarize.py` reads its turns out of
      `exclusive_speaker_diarization`; and the array may be `None`, or padded
      with zero rows when there were fewer centroids than labels. A zero-norm
      row is not an embedding — drop it rather than filing it under a name
- [ ] Carry the embeddings through the renumbering. `diarize.assign` renames
      pyannote's labels to `SPEAKER_01`, `SPEAKER_02`, ... by first appearance;
      the embeddings must be permuted with them, or every voice ends up filed
      under somebody else
- [ ] Store each cluster's embedding in `meta.json` under the channel's
      `speakers` block, beside the offsets of its 2-3 representative turns
- [ ] **Cut the representative snippets to disk** as `speakers/SPEAKER_01_1.wav`
      and so on, during the pipeline. Offsets alone are not enough: the WAVs are
      deleted as soon as the meeting transcribes cleanly, and `referat label`
      would have nothing left to play. Take the longest single-speaker turns,
      each capped at `snippet_seconds` — three six-second clips of 16 kHz mono
      is under 600 KB per speaker. They go when that speaker is labeled
- [ ] Match each cluster against the database: cosine similarity, the metric
      pyannote's own embeddings use, against every stored vector, keeping each
      name's best. Accept when the winner clears `match_threshold` **and** beats
      the runner-up *name* by `match_margin`. Anything less stays `SPEAKER_NN`:
      putting the wrong name on someone's words is worse than leaving a number
- [ ] Matched clusters are labeled by name in `transcript.md`
      (`[00:03:12] Anna: ...`); non-matches keep `SPEAKER_NN`. `meta.json`
      records the mapping in `speaker_names`
- [ ] New `[speakers]` config block — `identify`, `owner_name`,
      `match_threshold`, `match_margin`, `snippets_per_speaker`,
      `snippet_seconds` — in `config.example.toml` and `config.py`
- [ ] The thresholds are guesses until they meet real voices. Calibrate them on
      the first handful of labeled meetings. Too generous is the one failure
      mode that writes a lie into a transcript, so start strict
- [ ] Identification degrades exactly as diarization does: any failure costs
      names and nothing else. It must never raise into `transcribe_meeting`,
      for the same reason `diarize.diarize` must not — see `CLAUDE.md`
- [ ] The tray notification at the end of the pipeline reports the count:
      `Transcribed (2 unknown voices — run: referat label 2026-08-27_1400)`.
      With nothing unknown it says nothing extra
- [ ] `referat label` has to run without the `transcribe` extra: it reads JSON,
      compares vectors and plays WAVs. Nobody should need three gigabytes of
      torch resident to type a name

### `referat label <meeting-id>`

- [ ] `referat/label.py`: for each unlabeled speaker in the meeting, play the
      snippets through `sounddevice`, then prompt
- [ ] Prompt vocabulary: a name, a number picked from the known names listed
      above the prompt, `r` to replay, `s` to skip this speaker, `q` to stop.
      Skipping is not a decision — the speaker stays unknown and comes back
      next time
- [ ] Fuzzy completion over the existing names with `difflib.get_close_matches`,
      confirmed rather than applied silently ("Did you mean Anna? [Y/n]"), so a
      typo creates a new person only when the user insists on it. No new
      dependency for this
- [ ] Refuse `ME`, `REMOTE`, and anything matching `SPEAKER_\d+` as a name
- [ ] On a name: append that cluster's embedding to the database under it,
      record it in `meta.json`'s `speaker_names`, rewrite that speaker's labels
      in `transcript.md`, and delete that speaker's snippets
- [ ] Rewrite the label field only. The speech is never touched — see the
      immutability rule in `CLAUDE.md`
- [ ] `referat label --forget <name>`: delete the person from the database and
      revert their labels to `SPEAKER_NN` in every transcript carrying them.
      This is why `speaker_names` is kept per meeting instead of only being
      baked into the Markdown — without it there is no way back to the number
- [ ] `referat label` with no meeting id walks every meeting that still has
      unknown speakers, oldest first
- [ ] A `referat rerun` re-diarizes and renumbers, so names have to be
      re-derived from the database rather than carried over from the old
      `speaker_names`. That is automatic as long as labeling writes to the
      database first: the voice named last week matches on the way through

### Bootstrapping me

- [ ] The mic channel is one speaker by definition, so it needs no diarization
      to yield an embedding: run the embedding model over `mic.wav` and file the
      result under `owner_name`
- [ ] **Only when the loopback channel had voice in it.** A meeting held in
      person puts the whole room through my microphone and the embedding of that
      is a blend of everybody. A Zoom or Teams call is the unambiguous case —
      the remote voices arrive on the loopback, so the mic is me
- [ ] `owner_name` stays empty until the first time I label myself; it is seeded
      with "Niklas" then, and the automatic mic-channel additions start from
      that point

### The database is biometric personal data

- [ ] It lives in `<meetings_dir>/.voices/`, never leaves the machine, and is
      **excluded from sync and from backups**. `~/Meetings` is outside Dropbox
      today, but nothing enforces that it stays outside: say so in SETUP.md, and
      drop a `DO-NOT-SYNC.md` in the folder that says it there too
- [ ] `--forget <name>` is a real deletion and not a tombstone: the embeddings
      go, the labels revert, and nothing identifying the person is left behind
- [ ] The `/cleanup` pass runs `claude -p` inside the meetings folder with write
      access, so keep it out of `.voices/`: a deny rule in the scaffold's
      `.claude/settings.json` at step 10, and a line in the meetings `CLAUDE.md`
- [ ] Everyone in this database is someone who never asked to be in it. That is
      not a task, but it is the reason for the three above

## 8. CLI

- [ ] `referat list` — date, duration, status
- [ ] `referat rerun <meeting-id>` — re-run transcription and diarization
- [ ] `referat status` — read `status.json`, check PID liveness
- [ ] `referat label <meeting-id>` — name the unknown speakers; the command
      itself is specified in step 7b
- [ ] `referat list` marks meetings that still have unlabeled speakers, so the
      list doubles as the queue for `referat label`

## 9. Autostart and setup docs

- [ ] `scripts/install_autostart.py`: Startup folder shortcut via `pythonw.exe`
- [ ] `SETUP.md`: Python 3.12 + uv, CUDA 12.8 driver check, cuDNN/cuBLAS DLLs
      needed by CTranslate2, Hugging Face token and pyannote model gating,
      USB button programming, microphone selection
- [ ] SETUP.md: the token file must be plain text the loader can read, and the
      account behind it must have accepted the conditions at
      <https://hf.co/pyannote/speaker-diarization-community-1>. An unaccepted
      repo is a `403` that silently costs you speaker labels — the transcript
      still arrives, so it is easy to miss. `meta.json`'s per-channel
      `diarization.reason` is where it says so
- [ ] SETUP.md: first transcription downloads large-v3 (~3 GB) from Hugging
      Face and takes a few minutes; `medium` downloads separately the first time
      the CPU fallback is taken. Neither is a failure, but both look like a hang
- [ ] SETUP.md: `uv` is on the persisted **user** PATH via WinGet, so only
      shells opened before the install lack it (diagnosed twice already — see
      "Surfaced later")
- [ ] SETUP.md: the known-voices database in `<meetings_dir>/.voices/` holds
      voiceprints of everyone recorded. Exclude the meetings folder — or at
      minimum that directory — from every sync client and backup tool on the
      machine, and describe `referat label --forget <name>` as the way a person
      is removed

---

**Steps 10-12 are gated: do not start until steps 1-9 work end-to-end on real
meetings.** There is nothing to clean up until the loopback channel, the
speakers and the CLI are real, and the `/cleanup` prompt can only be tuned
against transcripts of actual meetings, not synthetic ones.

## 10. Meetings folder scaffold

- [ ] Restructure `templates/` into `templates/meetings/`, a directory copied
      whole into the meetings folder. `templates/meetings-CLAUDE.md` becomes
      `templates/meetings/CLAUDE.md`; `paths.MEETINGS_CLAUDE_TEMPLATE` becomes a
      directory constant. Keep the first-run seeding in `config.py` working
      across the move
- [ ] `templates/meetings/CLAUDE.md`: keep the current file conventions and add
      my name (Niklas) and the note style, so a Claude Code session opened there
      starts oriented
- [ ] `templates/meetings/.claude/commands/cleanup.md`: the `/cleanup
      <meeting-id>` slash command. Reads that folder's `transcript.md`, writes
      `notes.md` beside it — decisions, action items, discussion summary —
      `[[Wikilinks]]` for people and projects, an H1 title on the first line
      (the indexer reads it), and never a modification to `transcript.md`,
      `meta.json` or the WAVs. The prompt lives here, versioned, so it can be
      refined without touching code
- [ ] `templates/meetings/CLAUDE.md`: document that a speaker label may be a
      real name once `referat label` has run, that names are per-person and
      stable across meetings while `SPEAKER_NN` is per-meeting, and the
      immutability rule — speech is never edited, labels are metadata
- [ ] `templates/meetings/.claude/settings.json`: deny reads of `.voices/**`,
      so the `/cleanup` pass cannot pull voiceprints into a prompt. The folder
      is handed to `claude -p` with write access; this is the only thing
      standing between that and the biometric data
- [ ] `templates/meetings/.vscode/settings.json`:
      `{"workbench.editorAssociations": {"*.md":
      "vscode.markdown.preview.editor"}}`, so Markdown opens rendered in that
      workspace only
- [ ] Seed the scaffold file by file: copy anything missing, **never overwrite
      anything that exists**, so a `/cleanup` prompt refined in place survives.
      Extends the existing first-run bootstrap in `config.py`. Repo template and
      live copy will drift; reconcile by hand when a refinement is worth keeping
- [ ] `referat/index.py`: regenerate the meetings folder's own `INDEX.md` — one
      table row per meeting from `paths.list_meeting_dirs` and `Meeting.load`
      with date, title, duration, status and relative links to `transcript.md`
      and `notes.md`, written through `paths.write_text_atomic`
- [ ] Title comes from the H1 of `notes.md`, falling back to the meeting id when
      no notes exist. Deliberately *not* a new `meta.json` key: the meetings
      `CLAUDE.md` forbids editing `meta.json`, and the title is a product of the
      cleanup pass rather than of the recorder
- [ ] Regenerate `INDEX.md` at the end of the transcription pipeline, so the
      dashboard is current without being asked. With Markdown opening rendered,
      that file *is* the browsing UI
- [ ] `referat index` CLI command, beside step 8's `list` / `rerun` / `status`,
      for regenerating on demand after a `/cleanup` run
- [ ] Give the meetings `INDEX.md` a column for unlabeled speakers, so the
      dashboard also shows what is waiting for `referat label`
- [ ] Note for whoever builds this: the meetings folder's `INDEX.md` is a
      different file from this repository's `INDEX.md`. The standing instruction
      in `CLAUDE.md` is about the repo one; the meetings one is generated and
      never hand-edited

## 11. VS Code extension (`referat-vscode/`)

- [ ] TypeScript, standard `yo code` scaffold, esbuild bundling
- [ ] A "Referat" TreeView in the sidebar listing meeting folders read from
      their `meta.json`: date, duration, and a status icon per meeting
      (recorded / transcribed / notes exist)
- [ ] A file watcher on the meetings folder keeps the tree live
- [ ] Per-meeting context menu: *Open transcript*, *Open notes*, *Generate
      notes*, *Re-transcribe*
- [ ] *Generate notes* spawns the official Claude Code CLI as a child process
      with cwd = the meetings folder: `claude -p "/cleanup <meeting-id>"
      --allowedTools "Read,Write" --permission-mode acceptEdits`. Progress
      notification while it runs; on exit code 0 open the resulting `notes.md`;
      on failure surface stderr. **The extension never handles credentials** —
      the `claude` binary owns all authentication, under the subscription login
- [ ] *Re-transcribe* shells out to `referat rerun <meeting-id>`
- [ ] An **"Unknown speakers"** sub-item per meeting in the tree, listing the
      speakers step 7b could not identify and opening a labeling webview: a play
      button per snippet, and a name field completing over the known names. It
      is `referat label` with a UI, writing the same three places — the
      database, `meta.json`, `transcript.md` — so drive it through that code
      rather than reimplementing the matching in TypeScript
- [ ] Settings: meetings folder path, and path to the `claude` binary
      (default: found on PATH)

## 12. Extension packaging

- [ ] `vsce package` producing a `.vsix`
- [ ] SETUP.md paragraph on sideloading it: `code --install-extension
      referat-vscode-x.y.z.vsix`, or the Extensions view's "Install from
      VSIX..."

## Surfaced later

- [ ] **Speaker bleed.** Recording through laptop speakers rather than
      headphones puts the remote voice on *both* channels, which would duplicate
      every remote line — once as `ME`, once as `REMOTE`. Not observed in the
      2026-08-27_1906 call, whose loopback was empty anyway, so nothing is done
      about it. Revisit if duplicated lines ever show up in a real transcript.
  Step 7b gives this a second cost: bleed also poisons the owner embedding, so
  the automatic mic-channel additions are conditioned on the loopback channel
  having voice in it
- [ ] A decode failure is device-independent, but it still costs a full CPU
      retry: any exception on CUDA falls back, so an undecodable `system.wav`
      re-transcribes the *good* channel on `medium` before giving up. Correct,
      just wasteful. Only worth fixing if it ever happens outside a test
- [ ] **Whisper merged two utterances 22 seconds apart into one segment** on the
      synthetic `mic.wav` — both lines came out under a single `[00:00:08] ME:`.
      The VAD strips the silence and Whisper then sees continuous speech. It
      costs a timestamp, not any words, and it may be an artifact of unnaturally
      clean TTS audio; watch whether real meetings do it, and reach for
      `word_timestamps` only if they do
- [ ] The one real meeting with a populated loopback channel does not exist yet.
      Step 6's merge was verified on a synthetic meeting built by cutting real
      speech out of `mic.wav` and laying it on both channels in alternating
      windows. Check a real Zoom or Teams call when one happens

- [ ] Decide whether `keyboard` hotkeys need an elevated process to fire while
      an admin window has focus
- [x] Confirm `PyAudioWPatch` publishes a Python 3.12 wheel; otherwise switch the
      loopback capture to `soundcard` — 0.2.12.8 installs cleanly on 3.12
- [x] `uv` appeared to be missing from PATH. It is not: WinGet put its Packages
      directory on the persisted **user** PATH, but shells started before the
      install never received it. A new terminal fixes it — nothing to add.
      Worth a line in SETUP.md so it is not re-diagnosed a third time.
- [ ] Synthetic keystrokes cannot be injected from an automated session, so the
      hotkeys can only be verified by hand — keep the hook side callable
      directly for future checks
- [ ] The recording clock starts before the audio devices are opened, so every
      meeting begins with ~1 s of padded silence on both channels. Harmless, and
      it keeps the two channels aligned with each other; revisit only if the
      lead-in ever bothers the transcription
- [ ] `SetThreadExecutionState` only suppresses the *idle* sleep timer. Closing
      the lid or choosing Sleep still suspends the machine mid-meeting. Fixing
      that means changing the power plan's lid-close action, which is a
      system-wide setting Referat should not silently rewrite — decide whether
      SETUP.md should just tell the user to set it
- [x] `powercfg /requests` is administrator-only, so an ordinary session cannot
      confirm the hold that way. `SetThreadExecutionState` returns the previous
      state, though, which makes the call its own query: that is how step 4 was
      verified, and it also demonstrated the per-thread scoping directly. Run the
      `powercfg` check by hand in an elevated shell once
- [ ] **Smart App Control is enforcing on this machine** (confirmed:
      `VerifiedAndReputablePolicyState = 1`), and it blocks PyAV's unsigned
      FFmpeg DLLs, so `import faster_whisper` fails outright. Worked around by
      decoding the WAVs directly and stubbing `av` — see
      `transcribe._neutralize_pyav`. **This will very likely bite again at step
      7**: `pyannote.audio` pulls in `torchcodec`, which bundles FFmpeg the same
      way. Turning Smart App Control off is a one-way, system-wide change
      (Windows cannot re-enable it without a reinstall), so it is the user's
      call and not something Referat should ask for — decide at step 7 whether
      to work around it again or to document it in SETUP.md
- [ ] `torch` must be imported before `faster_whisper`. ctranslate2's converters
      import torch halfway through their own import, and torch 2.11 does not
      survive being entered that way — it reaches `torch.utils._debug_mode`
      before `torch.library` is bound. `load_model` does this on every device;
      revisit when torch is next upgraded
- [ ] The Hugging Face cache warns that symlinks are unavailable, so model files
      are stored duplicated. Harmless, fixed by enabling Windows Developer Mode;
      decide whether SETUP.md should mention it. Seen again at step 7, on the
      pyannote download
- [x] `config.hf_token()` read the token file as UTF-8 and caught only `OSError`,
      so the UTF-16-with-BOM file that `Set-Content` and `>` produce in Windows
      PowerShell 5.1 raised `UnicodeDecodeError` from inside a transcription
      job. It now picks the encoding off the BOM
- [x] The step-5 `av` stub answered `av.__file__` with `ImportError`, which
      `pyannote.audio` probes on import — so diarization died before reaching
      the network. Dunder lookups now raise `AttributeError`
- [ ] pyannote disables TF32 on import, warning that it hurts reproducibility.
      That is its call to make and it only slows matmuls; revisit if
      diarization ever feels too slow on long meetings
- [ ] Three synthetic test meetings from step 7 are still in `~/Meetings`
      (`2026-08-27_2019`, `_2022`, `_2024`), as are the step-3 ones. Delete them
      once they have been looked at
- [x] Pin the VS Code interpreter to the uv venv and set
      `python-envs.alwaysUseUv` — the extension had selected system Python 3.14
      and was failing `python -m pip list` (uv venvs have no pip)
- [ ] Step 11 spawns `claude -p` with `--permission-mode acceptEdits` and
      `--allowedTools "Read,Write"` in a folder full of private meeting
      material, which makes the `/cleanup` prompt the only thing bounding what
      gets written there. Re-read that prompt with this in mind once there are
      real meetings in the folder

- [ ] A cluster embedding is a centroid over one meeting. The same person on a
      different headset, a bad connection, or a cold will land somewhere else in
      the space, which is why the database keeps a *list* of embeddings per name
      rather than averaging them into one. Watch whether a person needs three or
      thirty before they match reliably
- [ ] If matching ever gets slow, cap the embeddings kept per person — the most
      recent twenty, say. At one meeting a day and a 256-float vector each it
      will not get slow for years, so this is a note and not a task
- [ ] Diarization can also split one person across two clusters in a single
      meeting. `referat label` will then ask for the same name twice, which is
      harmless — both embeddings are that person's — but the transcript keeps
      two differently-numbered speakers who now carry the same name. Decide
      whether that is fine (it reads fine) or whether the two lines should merge
