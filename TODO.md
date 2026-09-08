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
- [x] Verify the programmed USB buttons drive the state machine. Confirmed on
      2026-08-28 against the autostarted tray, with audio: the button toggles
      a recording and the meeting transcribes
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
- [x] Held only while recording or paused; released on stop —
      **superseded 2026-09-03**, see below
- [x] Transcription runs without the hold — **superseded 2026-09-03.** Left here
      because it was a deliberate decision and the reversal is worth reading
      against it: right about the cost, silent about a machine idling to sleep in
      the middle of a CUDA job. The hold now covers every state but idle, and a
      notes pass holds its own reason. See *A leaked large-v3, and jobs that
      outlive a lid* under "Surfaced later"
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
      is handed an in-memory waveform instead.
      **Half right, corrected on 2026-09-01.** Nothing ever *decodes* through
      torchcodec, which is what that claim was about and it is still true. But
      `pyannote/audio/core/io.py` does `import torchcodec` at module scope
      regardless, and the import alone loads `libtorchcodec_core{N}.dll` for
      FFmpeg majors 9 down to 4 — one Windows Security toast per refusal, which
      is what the user finally reported after the 2026-09-01 meeting. Pyannote
      catches it and carries on with `TORCHCODEC_AVAILABLE = False`, so the cost
      was always notifications and never a transcript. Fixed by
      `diarize._neutralize_torchcodec`, which stubs the module out before
      pyannote is imported — unconditionally, since here the attempt is the
      problem rather than the diagnosis
- [x] Diarization has only been checked against two Windows TTS voices, which
      are cleanly separated and never overlap. Real speakers talk over each
      other and sound alike; check the accuracy on a real Zoom or Teams call.
      **Checked on a real in-person meeting instead** (2026-09-01_0900, 14 min,
      two people through one Jabra): 337 turns, exactly 2 speakers, and the two
      clusters were right people rather than right counts — the transcript reads
      as a coherent advisor/student conversation. Accuracy is not perfect at the
      turn boundaries; a handful of short interjections land on the wrong
      speaker (`[00:00:34] Or is there overlap?` and its answer are both
      SPEAKER_01). Good enough that the labels are worth having, and the failure
      mode is a misattributed one-liner rather than a merged speaker.
      **The remote call happened on 2026-09-01** (`2026-09-01_2102`, a 19-minute
      Teams call): the loopback carried 86 segments and three clusters, the mic
      182 and two, and the merged transcript reads as a coherent three-way
      conversation. Diarization split *two* of the three people across two
      clusters each - the owner on the mic, Mohammad on the loopback - so five
      clusters for three people. Harmless, both halves being theirs, and the
      known split-cluster item below has now been seen on real audio twice in one
      meeting
- [x] Route Python `warnings` into the rotating log with **Done on 2026-09-06**: `logging.captureWarnings(True)` in `setup_logging`.
      `logging.captureWarnings(True)` in `logging_setup.setup_logging`. Under
      `pythonw.exe` `sys.stderr` is None and the warnings module drops them
      silently, so pyannote's `TORCHCODEC_AVAILABLE` warning — and anything else
      a library warns about during a tray-run transcription — leaves no trace at
      all. Not urgent: it was the toast, not the missing warning, that surfaced
      the torchcodec import

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

- [x] `referat/voices.py`: the known-voices database at
      `<meetings_dir>/.voices/voices.json` — a name mapped to a list of
      embeddings, each stamped with the meeting id, cluster label and date it
      came from, so a bad entry can be traced back and pulled out. Written
      through `paths.write_json_atomic` like everything else
- [x] Harvest the embeddings diarization already computes.
      `DiarizeOutput.speaker_embeddings` in pyannote 4 is a
      `(num_speakers, dimension)` array of clustering centroids, ordered by
      `speaker_diarization.labels()`. Two traps: that order belongs to
      `speaker_diarization`, while `diarize.py` reads its turns out of
      `exclusive_speaker_diarization`; and the array may be `None`, or padded
      with zero rows when there were fewer centroids than labels. A zero-norm
      row is not an embedding — drop it rather than filing it under a name
- [x] Carry the embeddings through the renumbering. `diarize.assign` renames
      pyannote's labels to `SPEAKER_01`, `SPEAKER_02`, ... by first appearance;
      the embeddings must be permuted with them, or every voice ends up filed
      under somebody else
- [x] Store each cluster's embedding in `meta.json` under the channel's
      `speakers` block, beside the offsets of its 2-3 representative turns
- [x] **Cut the representative snippets to disk** as `speakers/SPEAKER_01_1.wav`
      and so on, during the pipeline. Offsets alone are not enough: the WAVs are
      deleted as soon as the meeting transcribes cleanly, and `referat label`
      would have nothing left to play. Take the longest single-speaker turns,
      each capped at `snippet_seconds` — three six-second clips of 16 kHz mono
      is under 600 KB per speaker. They go when that speaker is labeled
- [x] Match each cluster against the database: cosine similarity, the metric
      pyannote's own embeddings use, against every stored vector, keeping each
      name's best. Accept when the winner clears `match_threshold` **and** beats
      the runner-up *name* by `match_margin`. Anything less stays `SPEAKER_NN`:
      putting the wrong name on someone's words is worse than leaving a number
- [x] Matched clusters are labeled by name in `transcript.md`
      (`[00:03:12] Anna: ...`); non-matches keep `SPEAKER_NN`. `meta.json`
      records the mapping in `speaker_names`
- [x] New `[speakers]` config block — `identify`, `owner_name`,
      `match_threshold`, `match_margin`, `snippets_per_speaker`,
      `snippet_seconds` — in `config.example.toml` and `config.py`
- [ ] The thresholds are guesses until they meet real voices. Calibrate them on
      the first handful of labeled meetings. Too generous is the one failure
      mode that writes a lie into a transcript, so start strict.
      **One real hole found and closed on 2026-08-28**: with a single name in the
      database there is no runner-up, so the margin was trivially satisfied and
      0.70 decided alone — a different synthetic voice scored 0.7404 against a
      lone voiceprint and was accepted. The margin is now required of the score
      itself when there is nothing to compare against. The thresholds themselves
      are still guesses.
      **The material is finally readable, from 2026-09-02**: `referat show <id>`
      prints every cluster's `match` with its score, its runner-up and whether it
      was accepted, refusals included. The scores were always recorded and
      nothing had ever printed them, which is most of why this item never moved.
      Read them across the labeled meetings and set the two numbers
- [x] Identification degrades exactly as diarization does: any failure costs
      names and nothing else. It must never raise into `transcribe_meeting`,
      for the same reason `diarize.diarize` must not — see `CLAUDE.md`
- [x] The tray notification at the end of the pipeline reports the count:
      `Transcribed (2 unknown voices — run: referat label 2026-08-27_1400)`.
      With nothing unknown it says nothing extra
- [x] `referat label` has to run without the `transcribe` extra: it reads JSON,
      compares vectors and plays WAVs. Nobody should need three gigabytes of
      torch resident to type a name

### `referat label <meeting-id>`

- [x] `referat/label.py`: for each unlabeled speaker in the meeting, play the
      snippets through `sounddevice`, then prompt
- [x] Prompt vocabulary: a name, a number picked from the known names listed
      above the prompt, `r` to replay, `s` to skip this speaker, `q` to stop.
      Skipping is not a decision — the speaker stays unknown and comes back
      next time
- [x] Fuzzy completion over the existing names with `difflib.get_close_matches`,
      confirmed rather than applied silently ("Did you mean Anna? [Y/n]"), so a
      typo creates a new person only when the user insists on it. No new
      dependency for this
- [x] Refuse `ME`, `REMOTE`, and anything matching `SPEAKER_\d+` as a name
- [x] On a name: append that cluster's embedding to the database under it,
      record it in `meta.json`'s `speaker_names`, rewrite that speaker's labels
      in `transcript.md`, and delete that speaker's snippets
- [x] Rewrite the label field only. The speech is never touched — see the
      immutability rule in `CLAUDE.md`
- [x] `referat label --forget <name>`: delete the person from the database and
      revert their labels to `SPEAKER_NN` in every transcript carrying them.
      This is why `speaker_names` is kept per meeting instead of only being
      baked into the Markdown — without it there is no way back to the number
- [x] `referat label` with no meeting id walks every meeting that still has
      unknown speakers, oldest first
- [x] A `referat rerun` re-diarizes and renumbers, so names have to be
      re-derived from the database rather than carried over from the old
      `speaker_names`. That is automatic as long as labeling writes to the
      database first: the voice named last week matches on the way through

### Bootstrapping me

- [x] The mic channel is one speaker by definition, so it needs no diarization
      to yield an embedding: run the embedding model over `mic.wav` and file the
      result under `owner_name`
- [x] **Only when the loopback channel had voice in it.** A meeting held in
      person puts the whole room through my microphone and the embedding of that
      is a blend of everybody. A Zoom or Teams call is the unambiguous case —
      the remote voices arrive on the loopback, so the mic is me
- [x] `owner_name` stays empty until the first time I label myself; it is seeded
      with "Niklas" then, and the automatic mic-channel additions start from
      that point. **Resolved as a config key set by hand**: `config.py` never
      writes the TOML back, and there is no `SPEAKER_NN` for the mic channel to
      label in the first place. Set `[speakers].owner_name` and the additions
      begin with the next Zoom or Teams call
- [x] Set `[speakers].owner_name = "Niklas"` in the real `config.toml`. Left
      empty by this session because it is a decision about my own biometric data
      and the default should be off. **Set, and confirmed on 2026-09-04**
- [x] **The Jabra Speak 510 is a desk speakerphone, which weakens the "the mic
      is unambiguously me" premise above.** **Resolved by mic diarization**: the
      premise is gone, and the bootstrap now requires the microphone to have
      clustered into exactly one speaker. Echo of the far end off the
      speakerphone clusters as a second voice and refuses the bootstrap outright,
      which is the outcome the listening test below was meant to protect. Listen
      to `mic.wav` anyway before setting `owner_name` — it is one minute and it
      tells you how much the echo cancellation is really doing.
      Original note follows.
- [ ] **The Jabra Speak 510 is a desk speakerphone, which weakens the "the mic
      is unambiguously me" premise above.** Remote voices come out of its speaker
      and back into its microphone, and the condition that gates the bootstrap —
      the loopback channel had voice — is satisfied by exactly the calls where
      that echo is happening. The unit does hardware echo cancellation, so
      `mic.wav` should stay mostly me, but "mostly" is not the premise. Before
      setting `owner_name`, record a call on the speakerphone and listen to
      `mic.wav` for the far end; if it is audible, either set `owner_name` only
      while wearing a headset, or gate the bootstrap on the mic device

### The database is biometric personal data

- [x] It lives in `<meetings_dir>/.voices/`, never leaves the machine, and is
      **excluded from sync and from backups**. `~/Meetings` is outside Dropbox
      today, but nothing enforces that it stays outside: say so in SETUP.md, and
      drop a `DO-NOT-SYNC.md` in the folder that says it there too
- [x] It did not stay outside. The meetings folder moved to
      `~/Dropbox/Research/Meetings` on 2026-08-28, so `[paths].voices_dir` now
      moves the database out instead — `~/.referat/voices` on this machine,
      resolved in one place by `Config.voices_dir()`. Configuration rather than a
      Dropbox folder-ignore flag, which has to be re-applied by hand whenever the
      folder is recreated and fails silently when it is not
- [x] SETUP.md: say that a synced meetings folder needs `[paths].voices_dir` set,
      and that the check is `referat config` showing a voices path outside the
      synced tree. Same for `[paths].staging_dir`, which is outside it by default
      and must stay that way. **Section 6**, with this machine's own
      `referat config` output as the worked example — `meetings_dir` in Dropbox,
      the other two outside it
- [x] `--forget <name>` is a real deletion and not a tombstone: the embeddings
      go, the labels revert, and nothing identifying the person is left behind
- [ ] The `/cleanup` pass runs `claude -p` inside the meetings folder with write
      access, so keep it out of `.voices/`: a deny rule in the scaffold's
      `.claude/settings.json` at step 10. **The line in the meetings `CLAUDE.md`
      is written**, under Layout and again under Working here; the deny rule is
      the half that still has to wait for step 10
- [ ] Everyone in this database is someone who never asked to be in it. That is
      not a task, but it is the reason for the three above

## 8. CLI

- [x] `referat list` — date, duration, status. Plus `AUDIO`, because whether the
      WAVs are still on disk is the difference between a meeting `referat rerun`
      can do something with and one it cannot
- [x] `referat rerun <meeting-id>` — re-run transcription and diarization, in its
      own `rerun.py`: the one command that needs the `transcribe` extra, imported
      inside the function so `list` and `status` stay instant
- [x] A rerun clears `speaker_names` and the `speakers/` snippets before it
      starts. Diarization renumbers from scratch, so keeping either would put
      last run's answers on this run's numbering; the names come back out of the
      known-voices database on the way through
- [x] `referat rerun` refuses while a live tray is transcribing, because
      `transcribe._RUN_LOCK` cannot see across processes and two large-v3 models
      do not fit in 12 GB of VRAM. `--force` overrides
- [x] `referat status` — read `status.json`, check PID liveness. Through
      `OpenProcess`, **never** `os.kill(pid, 0)`: on Windows CPython implements
      `os.kill` with `TerminateProcess` for every signal but `CTRL_C_EVENT` and
      `CTRL_BREAK_EVENT`, so the POSIX liveness probe would kill the tray
      mid-meeting
- [x] `referat status` reports a stale status file rather than hiding it — a tray
      that died while transcribing is worth knowing about
- [x] `referat label <meeting-id>` — name the unknown speakers; the command
      itself is specified in step 7b
- [x] `referat list` marks meetings that still have unlabeled speakers, so the
      list doubles as the queue for `referat label`
- [x] `referat devices` — every input device and every WASAPI loopback source,
      with the one `[audio]` resolves to marked. It calls the recorder's own
      `resolve_mic_device` / `resolve_loopback_device` rather than copying their
      rules, so the listing cannot drift from what gets recorded, and it says so
      when a configured substring matches nothing — which is the failure the
      command exists to catch, since that case is otherwise a log line nobody
      reads until after the meeting
- [ ] Mic-only / system-only capture, deferred on purpose. The recorder opens
      both channels always and a channel is absent only when its device fails to
      open. The pipeline already tolerates one channel, so this is a recorder and
      config question, not a transcription one — build it if in-person meetings
      ever make the silent 345 MB/hour of `system.wav` a nuisance
- [ ] `referat status` cannot tell the tray's pid from a pid Windows has since
      reused, and short of comparing the process image or its creation time it
      never will. The printed `updated_at` is the only clue; leave it unless a
      reused pid ever actually misleads
- [ ] `referat project <verb>` is a step 13 command and specified there, not
      here. It shares this dispatcher, and `list` and `assign` share this step's
      rule of needing no optional extra. **Amended on 2026-09-01**: the verbs are
      now step 14's, `assign` is gone in favour of `tag` / `untag`, and the rule
      widened rather than moved — everything but `link-doc` and `sync` is a JSON
      read and a JSON write and needs no extra
- [ ] `referat list` grows a `TAGS` column and its `STATUS` column starts
      printing step 14's wider lifecycle vocabulary. Specified in step 14; noted
      here because this is the step that owns the table, and `HEADERS` /
      `RIGHT_ALIGNED` / `list_row` / `list_document` all have to move together or
      the text table and the JSON drift apart

## 9. Autostart and setup docs

- [x] `scripts/install_autostart.py`: Startup folder shortcut via `pythonw.exe`.
      Not via `referat-tray.exe`: the gui-script is a uv trampoline that
      `CreateProcess`es `pythonw.exe` and stays resident as its parent, so
      autostart through it would leave a stub process in the tree for the life
      of the tray. The `.lnk` is written by PowerShell driving
      `WScript.Shell.CreateShortcut` and read back to verify; the Startup folder
      itself is `SHGetKnownFolderPath(FOLDERID_Startup)` through `ctypes`
- [x] `SETUP.md`: Python 3.12 + uv, CUDA 12.8 driver check, cuDNN/cuBLAS DLLs
      needed by CTranslate2, Hugging Face token and pyannote model gating,
      USB button programming, microphone selection
- [x] SETUP.md: the token file must be plain text the loader can read, and the
      account behind it must have accepted the conditions at
      <https://hf.co/pyannote/speaker-diarization-community-1>. An unaccepted
      repo is a `403` that silently costs you speaker labels — the transcript
      still arrives, so it is easy to miss. `meta.json`'s per-channel
      `diarization.reason` is where it says so
- [x] SETUP.md: first transcription downloads large-v3 (~3 GB) from Hugging
      Face and takes a few minutes; `medium` downloads separately the first time
      the CPU fallback is taken. Neither is a failure, but both look like a hang
- [x] SETUP.md: `uv` is on the persisted **user** PATH via WinGet, so only
      shells opened before the install lack it (diagnosed twice already — see
      "Surfaced later"). It is section 2, and it was diagnosed a *third* time
      during this very session, from a shell older than the install
- [x] SETUP.md: the known-voices database in `<meetings_dir>/.voices/` holds
      voiceprints of everyone recorded. Exclude the meetings folder — or at
      minimum that directory — from every sync client and backup tool on the
      machine, and describe `referat label --forget <name>` as the way a person
      is removed
- [ ] **Sign out and back in once** to confirm the shortcut actually starts the
      tray. Everything else about it was verified from a session — the `.lnk` on
      disk read back by an outside reader, and its exact command line started by
      hand until `status.json` carried that pid — but nothing in a session can
      produce a logon, so the trigger itself is untested
- [ ] The `.lnk` holds an absolute path into `.venv\Scripts`. Recreating the
      venv keeps it working; **moving or renaming the repository does not**, and
      nothing detects it — the tray simply never starts at sign-in.
      `install_autostart.py --status` is the check, and re-running the installer
      is the fix. Worth remembering the next time this folder moves, as the
      meetings folder already has once
- [ ] **Dropbox is deleting files out of `.venv`.** Found while verifying this
      step: `.venv\Scripts\referat.exe` and `referat-tray.exe` existed at the
      start of the session and were gone by the end, and
      `site-packages\referat-0.1.0.dist-info` is now an empty directory, so
      `import referat` fails from any working directory but the repo root. The
      repository lives inside the Dropbox tree and `.venv` is only *gitignored*,
      which does nothing to stop a sync client. Nothing stops it happening again.
      Decide between telling Dropbox to ignore `.venv` (right-click, "Ignore" —
      or the `com.dropbox.ignored` attribute) and moving the checkout out of
      Dropbox entirely. **This is the same class of mistake the meetings folder
      already hit**, and the same answer: keep the thing that must not be synced
      out of the synced tree.
      **It recurred on 2026-08-31 and `uv sync` was no longer available to repair
      it** — see the Smart App Control item below — so the repair is now pip:
      remove the *empty* `referat-0.1.0.dist-info` husk, then
      `python -m pip install -e . --no-deps`. The husk has to go first or pip
      refuses with "Cannot uninstall referat None", claiming the package is
      installed while holding no record of what it installed. Documented in
      SETUP.md section 2
- [ ] Because of the above, the shortcut's `WorkingDirectory` is currently
      **load-bearing** rather than a nicety: `-m referat.tray` finds the package
      only because the working directory is the repo root and `-m` puts the
      working directory on `sys.path`. With an intact editable install it would
      work from anywhere. Re-check once `.venv` is repaired and protected
- [ ] A shortcut cannot set an environment variable, so `REFERAT_CONFIG` reaches
      the autostarted tray only when it is persisted in the user environment.
      The installer reads `HKCU\Environment` as well as its own environment and
      warns when the two disagree, but nothing enforces it
- [x] **Under `pythonw.exe` a bad `config.toml` is completely silent.** **Done on 2026-09-06**: `tray.main` sets up logging at the default level before returning, so the `ConfigError` is the log's first line.
      `tray.main` prints the `ConfigError` to a `sys.stderr` that does not exist
      and returns *before* `setup_logging`, so an autostarted tray with an
      unparseable config leaves no window, no icon and no log line. Documented
      in SETUP.md section 9 as "run `referat config`"; consider whether the
      config load should move after logging, or the error into the event log


---

**Steps 10-13 are gated: do not start until steps 1-9 work end-to-end on real
meetings.** There is nothing to clean up until the loopback channel, the
speakers and the CLI are real, and the `/cleanup` prompt can only be tuned
against transcripts of actual meetings, not synthetic ones. Step 13 is gated
twice over: a digest block is a meeting's `notes.md`, so it waits on step 10 as
well.

**Checked on 2026-08-28 and the gate does not lift yet**, so step 10 is
deliberately not started. The meetings folder holds exactly one meeting — 21
seconds, `clean=False`, still in staging with its audio kept — and every
recording so far has been solo, so `system.wav` has been silent every time.
Diarization has therefore never run on real audio, and `referat label` has never
run at all.

Concretely, the gate lifts when all four of these are true. They are a week of
ordinary use, not work items:

- [x] Two or three real meetings with **other people in them**, so the loopback
      channel has voice and diarization actually runs. `2026-09-01_0900` (two
      people through one Jabra, mic only) and `2026-09-01_2102` (a three-person
      Teams call, both channels)
- [x] At least one transcript where `referat label` has turned a `SPEAKER_NN`
      into a name, which also means the voices database is no longer empty. Four
      of them in `2026-09-01_2102`, through the sidebar rather than the terminal
- [x] Those meetings pass the quality gate and promote themselves out of
      staging, so the folder holds something `/cleanup` can be pointed at. Both
      did, and both have notes
- [ ] `mic.wav` from a speakerphone call listened to for the far end, before
      `[speakers].owner_name` is set — see the Jabra box in step 7b

**Step 10 was built on 2026-08-29 against that carve-out**, and the four boxes
above are still open. The restructure, the deny rule, the editor association and
`referat/index.py` never needed a real meeting; the `/cleanup` prompt is a
versioned Markdown file whose whole point is being refined in place, so v1 exists
and gets tuned as meetings arrive rather than being withheld until they do.

What that means for the boxes: they gate **tuning**, not building. The prompt has
now been run once, on `2026-08-28_1152` — a real transcript, but an in-person
meeting whose every line reads `ME`, so it has never been read against a call
with diarized speakers in it. See the step 10 follow-ups below.

## 10. Meetings folder scaffold

- [x] Restructure `templates/` into `templates/meetings/`, a directory copied
      whole into the meetings folder. `templates/meetings-CLAUDE.md` becomes
      `templates/meetings/CLAUDE.md`; `paths.MEETINGS_CLAUDE_TEMPLATE` becomes
      `paths.MEETINGS_TEMPLATE_DIR`. `config.ensure_meetings_dir` still does the
      first-run seeding, now through `paths.seed_tree`
- [x] `templates/meetings/CLAUDE.md`: keep the current file conventions and add
      my name (Niklas) and the note style, so a Claude Code session opened there
      starts oriented
- [x] `templates/meetings/.claude/commands/cleanup.md`: the `/cleanup
      <meeting-id>` slash command. Reads that folder's `transcript.md`, writes
      `notes.md` beside it — decisions, action items, discussion summary —
      `[[Wikilinks]]` for people and projects, an H1 title on the first line
      (the indexer reads it), and never a modification to `transcript.md`,
      `meta.json` or the WAVs. The prompt lives here, versioned, so it can be
      refined without touching code
- [x] `templates/meetings/CLAUDE.md`: document that a speaker label may be a
      real name once `referat label` has run, that names are per-person and
      stable across meetings while `SPEAKER_NN` is per-meeting, and the
      immutability rule — speech is never edited, labels are metadata. Sharpened
      further while writing it: the same person in the room and on the call is
      two numbers in one transcript, and there is now a flat prohibition on
      putting a real name onto a `SPEAKER_NN` in a note
- [x] `templates/meetings/.claude/settings.json`: deny reads of `.voices/**`,
      so the `/cleanup` pass cannot pull voiceprints into a prompt. The folder
      is handed to `claude -p` with write access; this is the only thing
      standing between that and the biometric data.
      **Verified by driving it**, in a bare folder with no `CLAUDE.md` telling
      the model anything, so the rule and not the prose was what was under test:
      `File is in a directory that is denied by your permission settings`, while
      an ordinary file beside it read fine
- [x] `templates/meetings/.vscode/settings.json`:
      `{"workbench.editorAssociations": {"*.md":
      "vscode.markdown.preview.editor"}}`, so Markdown opens rendered in that
      workspace only
- [x] Seed the scaffold file by file: copy anything missing, **never overwrite
      anything that exists**, so a `/cleanup` prompt refined in place survives.
      Extends the existing first-run bootstrap in `config.py`. Repo template and
      live copy will drift; reconcile by hand when a refinement is worth keeping.
      `paths.seed_tree` does the walk; verified by editing one seeded file and
      deleting another, then re-running — the edit survived byte for byte and
      only the deleted file came back
- [x] `referat/index.py`: regenerate the meetings folder's own `INDEX.md` — one
      table row per meeting from `paths.list_meeting_dirs` and `Meeting.load`
      with date, title, duration, status and relative links to `transcript.md`
      and `notes.md`, written through `paths.write_text_atomic`. Newest first,
      unlike `referat list`: a listing is read at a prompt with the last line
      nearest the cursor, a dashboard is read from the top. `write_index` never
      raises, for the reason `diarize.diarize` does not
- [x] Title comes from the H1 of `notes.md`, falling back to the meeting id when
      no notes exist. `index.meeting_title`, which step 13 shares. Only a `#` on
      the *first* non-blank line counts: one further down is a mistake in the
      notes rather than a second title, and picking it up would put a section
      heading in the dashboard. Deliberately *not* a new `meta.json` key: the meetings
      `CLAUDE.md` forbids editing `meta.json`, and the title is a product of the
      cleanup pass rather than of the recorder
- [x] Regenerate `INDEX.md` at the end of the transcription pipeline, so the
      dashboard is current without being asked. With Markdown opening rendered,
      that file *is* the browsing UI. After the promotion, so the meeting that
      just finished is in the folder being indexed, and unconditional, because a
      meeting that stayed in staging still changes the footer. `referat label`
      and `--forget` regenerate too: they are the only other things that move the
      Unnamed column
- [x] `referat index` CLI command, beside step 8's `list` / `rerun` / `status`,
      for regenerating on demand after a `/cleanup` run
- [x] Give the meetings `INDEX.md` a column for unlabeled speakers, so the
      dashboard also shows what is waiting for `referat label`. Through
      `voices.unknown_speakers`, the same function the tray balloon and `referat
      label` use, so the three cannot disagree
- [x] Note for whoever builds this: the meetings folder's `INDEX.md` is a
      different file from this repository's `INDEX.md`. The standing instruction
      in `CLAUDE.md` is about the repo one; the meetings one is generated and
      never hand-edited

### Correcting transcription errors downstream

Whisper mishears names, jargon and acronyms, and it mishears them
*consistently* — the same surname comes out the same wrong way every meeting.
The transcript keeps what was heard, by the immutability rule in `CLAUDE.md`;
the correction happens in the notes. Everything below is Markdown in the
meetings folder, so it is useful the moment it is written and needs no code at
all. **Do it before step 12b.**

- [x] A **Known people and terms** section in `templates/meetings/CLAUDE.md`: a **Built on 2026-09-06, generated rather than hand-maintained**: `PEOPLE.md` beside `INDEX.md`, written by `index.write_index` from the known-voices database's names — full name, short name, id, never an email — and regenerated by every path that already regenerates the index. The template's section explains it; the hand-maintained mangling column is not there, because a generated file cannot carry one and the flag rule handles a near miss without being told the wrong spelling.
      two-column table of the real spelling against what transcripts render it
      as. Maintained by hand, and the only place those spellings live
- [x] Say in that section what it does and does not do. It normalizes **Said**, in the template's *Known people and terms* section and in `PEOPLE.md`'s own header.
      **notes**, never `transcript.md`; and adding somebody to it does not name
      a `SPEAKER_NN` and puts nobody in `.voices/` — it is prose for the cleanup
      pass, not a voiceprint and not an identification
- [x] The **flag rule**, written there and again in the prompt: an exact match **Written**, in both places.
      is normalized silently, a near miss is corrected *and marked* —
      `Elmqvist (assumed transcription error: "Elmquist")` — once, at first use,
      and anything matching neither list is left exactly as transcribed. This is
      *a wrong name is worse than no name* applied to words: a silently applied
      guess reads as authoritative whether or not it happens to be right
- [x] `templates/meetings/.claude/commands/cleanup.md` gains a **Names and **Written**: a *Names and terms* section, pointing at `PEOPLE.md` and at the glossaries of the projects in `meta.json`'s `tags`.
      terms** section carrying the same three cases, plus where the lists are:
      the folder's `CLAUDE.md`, and — once step 13 exists — the glossary of the
      project named by `meta.json`'s `project` key
- [x] The flag text has to stay inside the Markdown subset step 13's translator **Checked** against `markdown.INLINE_RE`: a bare parenthesis and a double quote match nothing there, so the flag arrives in a doc as plain text.
      handles. Parentheses and quotation marks are plain characters, so it does;
      check it against that list rather than assuming it
- [x] A `[[Wikilink]]` uses the **corrected** name, with the flag beside it **Written**, with the example spelled out in both files.
      rather than inside the brackets, or the digest grows two entries for one
      person
- [x] Reconcile the repo template against the live meetings folder by hand. **Still by hand, and still open in the live folder**: the repo template changed on 2026-09-06 and the copy at the meetings folder did not. `PEOPLE.md` itself is already there, since it is generated.
      `paths.seed_tree` never overwrites a seeded file, so neither of these
      changes reaches the folder on its own. That is the whole point of the
      seeding rule and it is also the half that will be forgotten

#### Surfaced while building the correction rule (2026-09-06)

- [ ] **`PEOPLE.md` lands in a folder a sync client sees.** It carries names
      and ids and nothing else, and the transcripts beside it already carry
      the short names; the full names are new to that folder. Decided rather
      than overlooked — the list has to be where `/cleanup` runs, and that is
      the meetings folder — and it is the reason the email is not in it
- [ ] **The live `CLAUDE.md` and `cleanup.md` do not have the new sections
      yet.** `paths.seed_tree` never overwrites a seeded file, so the two
      sections are reconciled by hand, as the last box above says. Until they
      are, `/cleanup` runs without the flag rule and reads `PEOPLE.md` for nothing
- [ ] **The rule has never met a real note.** The first `/cleanup` after the
      live copy is reconciled is the one to read for a flag that should not be
      there — a name corrected that was right — which is the failure worth
      catching, and for a near miss it left alone

### Surfaced while building step 10

- [x] **The `/cleanup` prompt has met exactly one transcript**, and the wrong
      shape of one: `2026-08-28_1152` is in-person, undiarized, 258 lines of
      `ME`. The notes it produced are good and honest — it said at the top that
      attribution is missing, inferred turns from conversational flow, and
      refused to name the second participant — but every instruction about
      *speakers* went untested, because there were none. Re-read it against the
      first call that has `SPEAKER_NN` in it.
      **Done on 2026-09-01** against `2026-09-01_2102`, which has three named
      speakers across two channels. The notes are right, attribute correctly and
      use `[[Wikilinks]]` per person, and the user's verdict was that generation
      "works really well". Note the prompt never had to face a `SPEAKER_NN`,
      because every speaker had been named before `/cleanup` ran - the rule that
      is still untested is the one forbidding a real name on a number
- [ ] **A deny rule binds the file tools, not `Bash`.** `Read(**/.voices/**)`
      and `Edit(**/.voices/**)` stop the Read and Edit tools; a
      `cat .voices/voices.json` would walk straight past them. That is survivable
      only because step 11 spawns `/cleanup` with no shell — so **`Bash` must
      never be added to that list**, and if it ever is, the deny rule needs
      `Bash(...)` entries to match. Written into `CLAUDE.md`; noted here because
      it is a footgun in the step 11 code, not in this step's.
      **The list itself was wrong here and is now `Read,Write,Glob`** — the
      slash command's own frontmatter declares those three, and `Glob` is what
      its wrong-meeting-id fallback needs. It returns paths rather than contents,
      so it cannot reach past the deny rule; `Bash` is the entry that would, and
      that is the one that has not moved
- [ ] The `/cleanup` prompt says to read `meta.json` for the date and duration,
      which means the pass reads a file the folder's `CLAUDE.md` tells it never
      to *edit*. That is the intended distinction and it held on the one real
      run, but it is a fine line for a prompt to walk. Watch that no run ever
      writes to it
- [x] The bundled `claude.exe` is **not on `PATH`** — it ships inside the VS Code
      extension at
      `~/.vscode/extensions/anthropic.claude-code-<version>-win32-x64/resources/native-binary/claude.exe`,
      and `where claude` finds nothing. **And the version in that path rots
      within days.** `/cleanup` was driven through
      `anthropic.claude-code-2.1.247-...` on 2026-08-29; by 2026-08-31 the
      extension was on 2.1.251 and `~/.vscode/extensions/.obsolete` listed the
      2.1.247 directory for deletion. So this is version rot, not a one-machine
      quirk, and it is why step 11 resolves the binary at spawn time and stores
      nothing. The resolution order is written out under step 11
- [ ] For a `claude` at a **shell** rather than in the extension, the
      no-PATH-change route on this machine is
      `npm i -g @anthropic-ai/claude-code`: `%APPDATA%\npm` is already on the
      persisted user PATH and is currently empty, so nothing has to be edited.
      `claude install` is the other option and is the one that *does* want a PATH
      change — it installs into `~/.local/bin`, which is not on PATH here.
      Documented in SETUP.md section 11a; not a task, a note for when it is
      wanted
- [x] Nothing prunes a `notes.md` whose meeting was re-run. `referat rerun` **Done on 2026-09-06**, from the lifecycle rather than the file: `rerun` writes `transcribed` and touches no note, so `cli.pending` queues a `transcribed` meeting that has a `notes.md` as *notes older than the transcript*, and *Notes for all…* rewrites it. Checked first that no meeting on this machine sits at `transcribed` with notes for any other reason.
      rewrites `transcript.md` and leaves the old notes beside it, silently
      describing a transcript that no longer exists. The title in `INDEX.md`
      then comes from stale notes. Decide whether a rerun should warn, or whether
      that is the user's problem — re-running `/cleanup` is the fix either way

## 11. VS Code extension (`referat-vscode/`)

**Built on 2026-08-31.** Everything below is done except the boxes that need a
person to click, which are collected at the end of this step.

**Deleted on 2026-09-04 at step 23**, once the command center reached parity.
Nothing below is checked off or removed — it is the record of a thing that was
built, used for four days as the primary UI, and retired on the condition step 20
wrote down. The unchecked boxes here stay unchecked: they were never done, and
marking them now would be a lie about a directory that no longer exists.

- [x] TypeScript, standard `yo code` scaffold, esbuild bundling. `npm install`
      of four devDependencies (typescript, esbuild, @types/vscode, @types/node),
      `npm run typecheck` and `npm run compile` both clean, `dist/extension.js`
      at 13.5 kB
- [x] A "Referat" TreeView in the sidebar listing meeting folders with date,
      duration, status and an icon per state. **Not read from `meta.json` in
      TypeScript** — see the `list --json` box below for why
- [x] A file watcher on the meetings folder keeps the tree live. **Both roots**,
      not just the meetings folder: a recording is in staging and would
      otherwise never appear until it promoted. Debounced at 300 ms, because
      transcription rewrites `meta.json` several times a meeting and every
      refresh is a subprocess
- [x] Per-meeting context menu: *Open transcript*, *Open notes*, *Generate
      notes*, *Re-transcribe*, gated on a composed `contextValue` so *Open
      notes* does not appear on a meeting with none and *Re-transcribe* does not
      appear on one whose audio was released
- [x] *Generate notes* spawns the official Claude Code CLI as a child process
      with cwd = the meetings folder. Progress notification, cancellable; exit 0
      opens the resulting `notes.md` rendered; failure surfaces stderr with the
      full invocation one click away in the output channel. **The extension
      never handles credentials** — the `claude` binary owns all authentication
- [x] **`--allowedTools "Read,Write"` was wrong** and this is where it showed.
      The `/cleanup` command's own frontmatter declares `Read, Write, Glob`, and
      `Glob` is what its wrong-meeting-id fallback lists the real ids with, so
      the narrower pair would have broken that fallback on the first mistyped
      id. The frontmatter is the authority; `Glob` returns paths rather than
      contents and cannot reach past the `Read`/`Edit` deny rule on `.voices/`.
      `CLAUDE.md`, this file and the spawn now all say `Read,Write,Glob`.
      **`Bash` is the line that matters and it has not moved**
- [x] *Re-transcribe* shells out to `referat rerun <meeting-id>` — in a
      terminal rather than a spawn behind a progress toast, because it takes
      minutes, loads a model and logs continuously, and that log is the thing
      worth watching while it does. The tree refreshes when the terminal closes
- [x] An **"Unknown speakers"** sub-item per meeting, listing what step 7b could
      not identify and opening a labeling webview: an audio element per snippet
      and a name field. It drives `label.apply_name` through the CLI rather than
      reimplementing anything — see the two boxes below
- [x] Settings resolution for the `claude` binary, in the order specified here
      and at **spawn time**: `referat.claudeBinary`, then
      `vscode.extensions.getExtension("Anthropic.claude-code")` +
      `resources/native-binary/claude.exe` existence-checked, then `claude` on
      PATH. The first implementation globbed the extensions folder and parsed
      its `.obsolete` — it worked, verified against the real filesystem where it
      picked 2.1.251 over the obsolete 2.1.247, and asking VS Code is still
      better, because VS Code follows its own extensions and leaves nothing to
      parse
- [x] **Never persist a resolved absolute path.** Nothing does: `resolveClaude`
      runs on every spawn and stores nothing anywhere

### What step 11 had to add to Python first

- [x] **`referat label` could not be driven from a subprocess at all.** The
      prompt reads `input()`, and `--forget`'s confirmation answers *no* on EOF,
      so anything without a terminal was told "Nothing was deleted." It now
      takes `--speaker SPEAKER_NN --name <name>`, `--forget <name> --yes` and
      `--json`. All three are thin wrappers over `label.apply_name` and
      `label.forget`, which the module docstring reserved for "step 11's
      labeling webview" back at step 7b
- [x] **The wrappers duplicate none of the rules.** `voices.name_complaint`
      still decides what a name may be; `--speaker` refuses a speaker who
      already has one, because renaming is a different operation from naming;
      and `run_apply` calls `index.write_index` itself, since `apply_name`
      deliberately does not — it is a primitive the pipeline also calls, so
      every *entry point* that names somebody has to, or the dashboard's Unnamed
      column goes stale the first time the panel is used
- [x] **`referat list --json`**, because the tree needs seven things the
      extension would otherwise have re-derived: the two roots,
      `format_duration`, `audio_state`, `index.meeting_title`,
      `voices.unknown_speakers`, and whether a meeting is still staged. Every
      one exists exactly once in Python and is shared by three or more callers
      *so that they cannot disagree*; a TypeScript copy would have made the
      extension a seventh reader of `meta.json` with its own opinions about all
      of them. The step 11 bullet above said "read from their `meta.json`", and
      this is the deliberate departure from it. The ASCII tables are untouched
      and stay the default
- [x] `_label_complaint` rejects the flag combinations that do not mean
      anything, in a sentence rather than through argparse's mutually-exclusive
      groups, which name the flags without saying what the pair would have meant

### Deviations from this step as it was written

- [x] **No meetings-folder setting**, though the bullet above asked for one.
      Where meetings live is `[paths].meetings_dir` in the repository's
      `config.toml` — the file the tray records against — and a second place to
      say it is a second thing that can disagree with the recorder. This project
      has been pulled back from exactly that twice: `voices_dir` derived from
      `meetings_dir`, and `format_duration` copied into two modules.
      `referat.repoRoot` takes its place, and the extension learns both roots
      from the config the tray is using
- [x] **`uv run` was the plan and is unusable.** The plan for this step said
      `uv run --directory <repoRoot> referat`, on step 9's reasoning that
      Dropbox deletes `.venv\Scripts\referat.exe`. The first command of the
      session came back *An Application Control policy has blocked this file*:
      Smart App Control had withdrawn its benefit of the doubt from `uv.exe`
      earlier the same day. The extension spawns
      `<repoRoot>\.venv\Scripts\python.exe -m referat.cli` with cwd at the
      repository root instead — the interpreter being the one link that survives
      both SAC and Dropbox. **cwd is load-bearing**, not tidy: `-m referat.cli`
      resolves only because the repository root is on `sys.path`
- [x] The labeling panel offers **clickable chips of the known names** rather
      than a port of `difflib.get_close_matches`. The terminal asks "Did you
      mean Anna?" because it has no list to click; porting the fuzzy match would
      have been the second implementation this whole step is built to avoid

### Still to check by hand — nothing in a session can click VS Code

- [x] **Launching the tray without a terminal.** `install_autostart.py
      --start-menu` writes the same shortcut into `Start Menu\Programs`, so it
      is searchable by name and pinnable to the taskbar. One `Location` record
      per folder rather than a second script, and `--status` reports both
      locations whatever flags it is given
- [x] **Audited what is still unsigned, rather than assuming the migration
      settled it.** Base install: 39 signed, 8 unsigned, and all 8 are bundled
      tools nothing imports. Venv `python.exe`/`pythonw.exe`: `Valid`. Unsigned
      and reachable: pip's console-script stubs (`referat.exe`,
      `referat-tray.exe`), which nothing here invokes, and `site-packages` native
      DLLs — torch alone 26 of 38 — which would cost transcription and not the
      application. The rule that came out of it: **keep the import-critical path
      signed, let the rest fail soft**, since PyPI wheels cannot be signed and
      SAC has no per-file allow
- [x] **The `[audio].mic_device = "Jabra"` substring matches nothing right now**, **Dropped on 2026-09-06: the extension was deleted at step 23.**
      found while running `referat devices` after the rebuild — it falls back to
      the Realtek array and says so only in a log line. Exactly the silent
      failure `devices` exists to catch. Either the headset was unplugged or the
      substring is wrong; check before the next meeting rather than after it

- [x] **Rebuild the venv on a signed Python 3.12 — nothing below could run until
      this was done.** Smart App Control had started blocking `_ctypes.pyd` inside
      the unsigned uv-provisioned interpreter, which killed `status.py`,
      `tray.py`, `power.py`, every CLI subcommand and the extension. `py install
      3.12` fetched a PSF-signed 3.12.10 and the venv was rebuilt on it with
      `site-packages` moved aside and back. Verified: `ctypes`, `winsound`,
      `torch 2.11.0+cu128` with `cuda True`, the whole audio and transcribe
      stack, every CLI subcommand, and the tray running `idle`
- [x] **The venv `pythonw.exe` is a redirector now, not the interpreter.**
      CPython's `venv` copies `Lib\venv\scripts\nt\pythonw.exe`, which
      spawns the base interpreter and waits — so the tray is a ~6 MB stub plus a
      ~46 MB interpreter where uv's venv gave one process. Harmless (`tray.py`
      writes its own pid, so `referat status` is right), but it falsified the
      argument in `install_autostart.py`'s docstring, which now says so
- [x] Press F5 and confirm the tree lists the meetings, newest first, with the **Dropped on 2026-09-06: the extension was deleted at step 23.**
      right icons, and that a meeting recorded from the tray appears without a
      manual refresh
- [x] *Open transcript* and *Open notes* open rendered Markdown **Dropped on 2026-09-06: the extension was deleted at step 23.**
- [x] *Generate notes* on a meeting without notes: that it finds `claude.exe` **Dropped on 2026-09-06: the extension was deleted at step 23.**
      off PATH, that `/cleanup` runs with `Read,Write,Glob` and gets far enough
      to use `Glob` on a wrong id, and that `notes.md` opens afterwards
- [x] *Re-transcribe* opens a terminal and runs **Dropped on 2026-09-06: the extension was deleted at step 23.**
- [x] The **Unknown speakers** node opens the panel, a snippet actually plays **Dropped on 2026-09-06: the extension was deleted at step 23.**
      through the webview's `<audio>` element, and a name applies. **This is the
      one path with no real material to test it on**: no meeting on this machine
      has an unknown speaker, so the whole Python half was driven against a
      synthetic meeting in a scratchpad instead. It needs a real call with other
      people in it — the same gate step 10 is still waiting on

### Surfaced while building step 11

- [x] **F5 could never have started the extension host.** Three faults, each
      fatal on its own, all upstream of the extension host — the bundle itself
      was fine and loaded when required by hand. `tasks.json` had
      `"path": "referat-vscode"`: VS Code's npm task provider joins that onto
      `package.json` with no separator, looks for `referat-vscodepackage.json`,
      contributes no task, and `preLaunchTask` then fails to resolve. The same
      file's problem matcher captured a `message` and no `file`, which VS Code
      rejects, which invalidates the matcher, which means the background
      begin/end tracking never arms and F5 hangs — the exact failure that file's
      own comment was written to prevent. And
      `~/.vscode/extensions/niklas-elmqvist.referat-vscode-0.1.0` was a directory
      symlink at the live source tree, listed in `.obsolete` but absent from
      `extensions.json`, colliding with `--extensionDevelopmentPath`
- [x] **Smart App Control gave `uv` back the same day**, unchanged and still
      `NotSigned` — the reputation for that build was simply restored. Nothing
      reverted: it is the same volatility in the other direction, and the
      argument for a signed interpreter is the same argument either way
- [x] **`tsconfig.json` carried emit settings it never used.** `outDir` and
      `sourceMap` existed for a `tsc` that only ever runs with `--noEmit`, and
      went from inert to erroring when VS Code's bundled TypeScript 6 started
      requiring an explicit `rootDir` beside `outDir`. Removed both and set
      `noEmit` in the file, so the config no longer claims to emit anything
- [x] **F5 would have opened the host on no folder at all.**
      `--extensionDevelopmentPath` says which extension to load, not which
      workspace to open, and with no `referat.repoRoot` set `cli.ts` resolves the
      root by searching the open workspace folders for one holding
      `pyproject.toml` and `referat/cli.py`. So every command would have failed
      with "Cannot find the Referat repository." The launch config passes
      `${workspaceFolder}` as a second argument, **and** `repoRoot` now falls
      back to `__dirname/../..` — the checkout the bundle sits inside — so the
      tree works even in a host window opened on nothing. Installed from a
      `.vsix` that walk lands in `~/.vscode/extensions` and finds no
      `pyproject.toml`, so the real error still stands where it should
- [x] **Every context menu was inert.** The five `when` clauses read
      `/\bhasTranscript\b/`, and in JSON `\b` is a valid escape for U+0008
      backspace, not a regex word boundary — parsing the manifest shows character
      code 8 where the boundary should be. Every clause compiled to a regex that
      could never match the `contextValue` `tree.ts` composes. They need `\\b`.
      Worth remembering the shape of this: a JSON string is parsed before the
      regex engine ever sees it, so any `\b`, `\d` or `\s` in a `when`
      clause has to be doubled
- [x] **esbuild's error format cannot be matched by a VS Code problem pattern.**
      It puts `[ERROR] message` and the indented `file:line:col:` on different
      lines *with a blank line between*, and multi-line patterns match only
      consecutive lines. Rather than depend on an external matcher extension,
      `esbuild.mjs`'s `onEnd` hook now also prints one compact
      `[watch] error file:line:col: message` per error and the matcher reads
      that. esbuild's own pretty output is untouched — `logLevel: "info"` still
      prints it with the source frame

- [x] **The panel's `<audio>` element has never decoded a real snippet.** The
      WAVs are mono 16-bit PCM at 16 kHz, which Chromium handles, but that is
      reasoning rather than a test — the synthetic fixture's tones were never
      played through a webview. First thing to check when a real meeting has an
      unnamed speaker in it. **Checked on 2026-09-01**: they play
- [x] **The repo's own `.vscode/settings.json` was not valid JSON**, found while
      adding `launch.json` and `tasks.json` beside it: the interpreter path was
      written with single backslashes, so `\.` and `\S` were invalid escapes.
      VS Code's parser is error-tolerant and had been carrying it since step 1,
      which is why the interpreter pin still worked and nobody noticed. Now
      forward slashes, which VS Code accepts on Windows, with a comment saying
      why so it does not get "fixed" back
- [x] The tree calls `referat list --json` on **every** refresh, including one **Dropped on 2026-09-06: the extension was deleted at step 23.**
      per watcher burst. That is a Python interpreter start each time, roughly a
      third of a second. Fine at this scale; if a hundred meetings ever make it
      feel slow, cache the document and invalidate on the watcher rather than
      making the CLI do less
- [x] **`referat.repoRoot` empty means "search the workspace folders"**, so the **Dropped on 2026-09-06: the extension was deleted at step 23.**
      extension does nothing useful in a window that does not have the
      repository open. That is the common case for a window opened on the
      *meetings* folder, which is exactly where somebody would want it. Decide
      whether the meetings folder should be recognised too, or whether the
      setting is simply the answer for that window
- [x] A meeting that is **staged** cannot have notes written for it: `/cleanup`
      runs in the meetings folder and a staged meeting is not there. *Generate
      notes* says so and refuses rather than letting the pass fail confusingly,
      but the real fix is the one already logged under step 8 — a meeting whose
      audio was kept sits in `%LOCALAPPDATA%` forever. **Fixed at step 15**: that
      meeting now has an off-ramp, and the refusal points at it instead of only
      at `referat rerun`

### The Projects section (step 13's UI lives here)

**Superseded on 2026-09-01 by steps 14 and 15, and kept because it is the record
of what was intended.** Every box below assumes a meeting belongs to exactly one
project, that a project links to exactly one doc, and that all of it renders as
nodes in a TreeView. All three are now false: projects are labels, a project
carries zero or more doc references, and step 15 replaces the TreeView with a
sidebar webview. Read the boxes as history; build step 15.

**There is no web UI in this project.** The tray icon and this extension are the
only graphical surfaces there will ever be, so everything step 13 needs a person
to look at or click is a node in this same TreeView. Build it with step 13, not
before; it is listed here because this is the file that describes the extension.

- [x] A **"Projects"** section in the Referat view, beside the meetings: one **Dropped on 2026-09-06: the extension was deleted at step 23.**
      node per project read from `<meetings_dir>/projects.toml`, labeled with
      the project name and the linked doc's title, or *not linked*
- [x] Per-project context menu: *Link digest doc*, *Unlink*, *Open digest doc* **Dropped on 2026-09-06: the extension was deleted at step 23.**
      (in the browser), *Sync now*
- [x] *Link digest doc* offers the same two paths as `referat project link`: **Dropped on 2026-09-06: the extension was deleted at step 23.**
      **Create new doc**, or **Select existing doc** through a QuickPick that
      re-runs the Drive search on `onDidChangeValue`, debounced, so typing
      filters against Drive rather than against a list fetched once
- [x] After selecting an existing doc, the tab check: if it has no tab named **Dropped on 2026-09-06: the extension was deleted at step 23.**
      `Meetings`, open it in the browser and show a modal saying to add one,
      with a *Re-check* button. Tabs cannot be created through the API, so this
      hand-off is the only way through and it has to look deliberate rather than
      like a failure
- [x] Per-meeting *Assign to project*: a QuickPick over the configured projects, **Dropped on 2026-09-06: the extension was deleted at step 23.**
      pre-selected on the remembered default. This is also the re-route — a
      meeting already assigned shows its current project and reassigning is the
      same action run again
- [x] An **"Unassigned"** node listing every meeting with no project, the same **Dropped on 2026-09-06: the extension was deleted at step 23.**
      way "Unknown speakers" lists what is waiting for `referat label`. That
      node *is* the queue; nothing else tracks it
- [x] All of the above shells out to `referat project ...` and re-reads the **Dropped on 2026-09-06: the extension was deleted at step 23.**
      files afterwards. The projects file, the Google calls and the
      reconciliation stay in Python — do not grow a second implementation in
      TypeScript, for the same reason the labeling webview drives `referat
      label`

## 12. Extension packaging

**Built on 2026-09-01.** `referat-vscode-0.2.0.vsix`, installed as
`niklas-elmqvist.referat-vscode@0.2.0`.

**Closed on 2026-09-02, with no further `.vsix` planned.** Step 20 makes the
command center the primary UI and puts this extension into maintenance, so
packaging is no longer forward work. Two boxes were still open here and both are
gone rather than checked, since neither was done: one said an installed build
needs `referat.repoRoot` set, which is true, is correct behaviour, and is
recorded in `SETUP.md` section 12 and in the CHANGELOG entry for this step; the
other asked somebody to confirm the installed build from a window that is not
the repository, which is a hand-check nobody will now perform on a surface being
retired. **The checked boxes below stay.** `.vscodeignore`, the `LICENSE`, the
`repository` field and `vscode:prepublish` all exist in the tree, the `.vsix` was
built and installed, and the standing rule at the top of this file is that
completed items are never deleted. Read this step as finished, not as cancelled.

**Re-sequenced on 2026-09-01 to run after step 15**, which rewrites the extension
from a TreeView into a sidebar webview. Nothing in this step changes — packaging a
view that step 15 deletes is simply wasted work. **Step 15 is built, so this is
now next**, and the payload it describes is unchanged by the rewrite: `dist/`,
`media/`, `package.json` and `README.md`. Note that `media/` now carries
`sidebar.css` and `sidebar.js` rather than `label.*`, so a `.vscodeignore` written
against a file list rather than against the four names above would ship the wrong
thing. The number stays where it is
because `SETUP.md` section 12 and the CHANGELOG entries are keyed to it.

- [x] **`.vscodeignore` does not exist yet**, so `vsce package` would ship
      `src/`, `node_modules/`, `esbuild.mjs` and the source maps inside the
      `.vsix`. `dist/`, `media/`, `package.json` and `README.md` are the whole
      payload — the point of bundling was to make that true.
      **Written as `**` plus negations** — an allowlist, which is the answer to
      the warning above: a file list is right the day it is written and wrong the
      next time `media/` gains a file. `media/**` by directory; `dist/extension.js`
      by name rather than `dist/**`, so a map left by a watch build cannot creep
      in. `LICENSE` joined the payload, so it is five names and not four
- [x] `vsce package` producing a `.vsix`. Nine files, 21 KB, and the listing was
      **checked against the archive** rather than trusted: no `src/`, no
      `node_modules`, no `.map`, no `esbuild.mjs`, no `tsconfig.json`, no
      `package-lock.json`
- [x] SETUP.md paragraph on sideloading it: `code --install-extension
      referat-vscode-x.y.z.vsix`, or the Extensions view's "Install from
      VSIX...". SETUP.md **section 12 already exists** as a paragraph saying
      the extension is not built yet and transcripts are Markdown until it
      is, so this is replacing one paragraph rather than deciding where a
      section goes. Replaced, and it gained the `referat.repoRoot` consequence
      below — which is the only thing installing actually changes

### What building it settled

- [x] **The version is 0.2.0, not 0.1.0.** 0.1.0 was step 11's TreeView, which
      step 15 deleted; the first packaged build is a different thing wearing the
      same number. Nothing bumps it automatically and nothing checks it against
      the CHANGELOG — `code --install-extension` reinstalls an unchanged version
      quite happily — so it is a label, and the label has to be moved by hand
      before packaging an update
- [x] **No source map in the packaged build.** `sourcemap` is tied to `--watch`
      beside the `minify` that already was. An esbuild map carries
      `sourcesContent`, so shipping one puts the whole TypeScript inside a `.vsix`
      whose point is being one JavaScript file — and excluding a map that is still
      emitted leaves a `sourceMappingURL` pointing at nothing. F5 is `npm run
      watch`, so debugging keeps its maps where debugging happens
- [x] **`vsce` stops to ask about a missing LICENSE and a missing
      `repository`**, and `npm run package` has no terminal to answer with. Both
      added, and both truthful — the remote exists, and the LICENSE says what
      `"license": "UNLICENSED"` already said. `"private": true` was left in and
      packaged fine: it means "never `npm publish`", and `vsce` is not npm
- [x] **A relative link in the README is rewritten, not refused.** With
      `repository` set, `[Referat](../README.md)` shipped as
      `https://github.com/nickelm/referat/blob/HEAD/../README.md` — a URL with a
      literal `..` in it. Absolute now, with a comment saying why, because this
      file is the package's front page and a relative link there points outside
      the package
- [x] **`vscode:prepublish` runs the bundler**, so a `.vsix` cannot be built
      around a stale `dist/extension.js`. `@vscode/vsce` is a fifth
      devDependency rather than an `npx --yes` re-resolving latest every run

## 12b. A global hotword list

**Built on 2026-09-02.** Numbered like step 7b, and for the same reason: it
belongs *inside* the build order rather than after it. It needed step 10's prompt
work done first — that is where the decision about what to do with a misheard
name is actually written — and step 13 depends on it, because a project's
`glossary` has nowhere to go until this list exists.

This is the one correction that happens *before* the transcript is written.
Everything downstream of it corrects the notes instead; `hotwords` is the chance
not to need the correction at all. It costs nothing at transcription time — the
terms go into Whisper's prompt, not through another model.

- [x] `[transcription].hotword_extras` in `config.example.toml` and `config.py`:
      a manual list, for the terms belonging to no project and to no person.
      Empty by default
- [x] `referat/hotwords.py`: `merge(config) -> list[str]`, the union of the
      names in the known-voices database, every project's `glossary` from
      `projects.json`, and `hotword_extras`. Deduplicated case-insensitively,
      order stable, so two runs over the same meeting build the same prompt
- [x] It reads the database through `Config.voices_dir()` and the projects file
      through `projects.py`, never by deriving either path itself — the same
      rule that keeps `voices_dir` from drifting back into Dropbox
- [x] `transcribe_channel` passes the merged list to
      `model.transcribe(..., hotwords=...)`. **There, not in `cli.py`.** The
      tray reaches the pipeline through `transcribe_meeting` and never through
      the CLI, so a merge living in the CLI would apply to `referat rerun` alone
      — and a rerun would then produce a different transcript from the recording
      it came from, which is the one thing a rerun must not do. "Merge logic
      lives in the CLI" means *in Python, never re-derived in TypeScript*: the
      same rule as `list --json`
- [x] Degrade the way everything else in this pipeline does. An unreadable
      `projects.json`, a missing voices database, a `hotwords` keyword a future
      faster-whisper has renamed: all of them cost hotwords and never a
      transcript. `merge` returns `[]` rather than raising
- [x] **The 224-token cap.** `hotwords` goes into Whisper's prompt window, so a
      long enough list gets truncated by somebody else's rule at somebody else's
      boundary. Cap it here instead, in a fixed priority order —
      `hotword_extras`, then names, then glossaries — and log what was dropped.
      A cap nobody can see is how this turns into a bug report about one
      specific name that is never heard right
- [x] `referat label --forget <name>` takes that name out of the list. This is
      automatic *given* that the merge reads the database live rather than
      caching it — so it is a property to **verify**, not to assume, and worth a
      check the day `--forget` is next used. A forgotten person whose name stayed
      in a hotword list would be the privacy posture leaking out through the back
      of the transcription stack
- [x] `referat hotwords` prints the merged list with the source of each term and
      says what the cap dropped. Needs no optional extra: it is two JSON reads,
      like `list` and `label`
- [x] Until step 14 there is no `projects.json` and no `glossary`. `merge` treats
      both as absent and contributes nothing from them, so this step builds and
      runs complete on its own. **The projects file moved from step 13 to step
      14 on 2026-09-01 and became JSON**; that is the only thing this step cares
      about, and it can be built before or after 14 either way


### Surfaced while building step 12b

- [x] **The token estimate had to be measured, and the first one was 30% wrong.**
      `hotwords.py` runs without the `transcribe` extra, so there is no tokenizer
      to ask and a term's cost is estimated from its length. `ceil(len / 3) + 1`
      was written first on the reasoning that Whisper's BPE gets about four
      characters to the token and does worse on names. It does far worse: checked
      against the real large-v3 `tokenizer.json` out of the HF cache, that rule
      came out **30% under** on 40 hyphenated `Synthetic-Term-003`-shaped strings,
      13% under on people and jargon, and 19% under on acronyms — every one of
      those a list faster-whisper would have sliced mid-name. `ceil(len / 2) + 2`
      replaced it and came out between 1.10x and 2.04x the truth across eight
      lists and never under. **The direction of the error is the whole point**:
      over-estimating drops a term off the bottom of a fixed priority order and
      logs it, under-estimating truncates silently
- [x] The budget is **223**, not 224. `generate_with_fallback` in faster-whisper
      1.2.1 slices `hotwords_tokens[: self.max_length // 2 - 1]` with
      `max_length = 448`. Read out of the installed source rather than inferred
      from "Whisper's prompt window is 224 tokens", which is the number this file
      and `CLAUDE.md` had both been carrying
- [x] **The keyword is checked by signature, not by catching a `TypeError`.**
      `model.transcribe` does the VAD and the feature extraction before it hands
      back its generator, so a `TypeError` raised from inside that work must not
      be mistaken for a faster-whisper that renamed the keyword.
      `transcribe._hotword_kwargs` inspects the signature instead and drops the
      list with a log line when it is absent. Verified both ways against a stub
- [x] **Verified end to end on real audio**, since no meeting on this machine
      still has WAVs for a `rerun` to work from. A SAPI utterance — *"...about
      the DuckDuckTalk project and the pyannote diarization work"* — through the
      real `medium` model came out **`Pianet`** with the list off and
      **`pyannote`** with it on, the two runs differing in nothing but
      `hotword_extras`. Segment logprob went -0.37 to -0.20. That is the whole
      step working, once
- [x] **`--forget` really does empty the name out of the list**, which the step
      asked to be verified rather than assumed. Driven in a scratchpad config
      with its own `meetings_dir`, `staging_dir` and `voices_dir`, so the real
      voiceprints, the real `projects.json` and the running tray were never
      touched: three names in, `referat label --forget Bjorn --yes`, two names
      out on the next `referat hotwords`
- [x] Degradation checked by corrupting both files at once. A `projects.json` and
      a `voices.json` that are not JSON each log a warning and contribute
      nothing; `referat hotwords` still prints the surviving `config` terms and
      exits 0, and `merge` returns what it has rather than raising
- [ ] **`[speakers].owner_name` is deliberately not a fourth source.** The step
      specifies three, and the owner reaches the list the moment
      `bootstrap_owner` files their first voiceprint under that name — which has
      not happened yet, because `owner_name` is still empty (step 7b). Until then
      the owner's own name belongs in `hotword_extras` like anybody else's term.
      Decided rather than overlooked; revisit if setting `owner_name` and waiting
      for a one-cluster mic meeting turns out to be a long wait
- [ ] The merge runs **once per channel**, so a meeting logs the hotword list
      twice and the cap's drop line twice. Deliberate — it keeps
      `transcribe_channel` self-contained and reading live, and two JSON reads
      against a model load is nothing — but if the log ever gets noisy, hoisting
      it into `transcribe_channels` is the move, not caching it in a module global

### Rejected while deciding this

- [ ] **Per-project hotwords at transcription time.** Rejected: a meeting is
      tagged *after* it has been transcribed, by `referat tag`, so at the moment
      the model runs there is no project to select a list with. Every glossary
      goes into the one global list instead — which costs prompt budget and buys
      the property that the list is right for a meeting nobody has tagged yet.
      **Tagging becoming many-to-many strengthens this** rather than weakening
      it: there is now no single project to select a list with even afterwards
- [ ] **Prompting for a project between stop and transcribe.** Rejected: it
      would manufacture the missing information, and it violates *recording
      robustness beats everything else*. The path from stop to a written
      transcript may not acquire a step that waits for a human being — a dialog
      nobody answers is a meeting that never transcribes, and the tray would be
      holding the only copy of the audio the whole time it waited
- [ ] **Keeping the audio so a meeting could be re-run against a glossary
      learned later.** Rejected: the WAVs go as soon as the transcript is clean,
      and that is the privacy posture rather than a disk-space optimization —
      recordings of people who never asked to be recorded do not sit around
      waiting to become useful. A term learned after the fact is corrected in the
      notes, which is exactly what the immutability rule is for

## 13. Per-project digests

**Built on 2026-09-04**, and it had been the one genuinely lagging build step:
forty unchecked boxes, nothing built, and both gates met since 2026-09-01. What
was still open was the auth question below, settled here as a **desktop OAuth
client** — because *select existing doc* has to search your own Drive, which a
service account cannot do.

Three things the boxes did not say and building it found. **A style reset before
styling**, because `insertText` inherits the style at the insertion point, so a
block inserted before an existing anchor arrives small and gray. **The acceptance
check is per paragraph**, because applied flat it fires on the documented `####`
pass-through. And **`synced` needed a way back**: `cli.set_notes_written` accepts
it now and drops to `notes_written`, which is the one lifecycle edge that runs
backwards.

The consent has to be given once at a terminal, so nothing has been written into
a real doc yet — the network half type-checks, renders and passes an offline
simulator rather than having run. Same honesty as step 23's two buttons.

**Gated on step 10** and, since 2026-09-01, **on step 14 as well**: the projects
file, the tag model and the lifecycle field all move there, and this step is the
consumer of them. A digest block is a meeting's `notes.md` translated into a
Google Doc, and there is no `notes.md` until `/cleanup` exists. The UI half used
to be written up as a section of step 11 and is now step 15.

A *project* is a thread of work spanning many meetings — the thing `/cleanup`
already writes `[[Wikilinks]]` for. Each project may be linked to one Google Doc,
its digest, into which every meeting assigned to it is written as a dated block,
in chronological order. The doc is the shareable artifact; the meetings folder
stays local.

**What 2026-09-01 changed, and what it did not.** All the Docs mechanics below
stand exactly as written — text anchors rather than named ranges, every write
located by `tab_id`, block operations in reverse document order, one `insertText`
plus N style requests, UTF-16 code-unit offsets, `createParagraphBullets` over
text with no `- ` in it, the Heading 3 date line sharing `index.meeting_title`,
`[[Wikilinks]]` as bold with the brackets stripped, and the acceptance check on
the produced plain text. Four things around them changed:

- **A meeting carries zero or more project tags, not one project.** `meta.json`
  gains `tags`; the `project` key below is superseded before it was ever written
- **A project carries zero or more doc references, not one.** `link` / `unlink`
  become `link-doc` / `unlink-doc`, and a sync iterates them
- **A meeting tagged with N projects renders as a dated block in each of their
  docs**, each block found by its own `[referat:<id>]` anchor. The anchor is
  unique within a tab, so fan-out costs the scheme nothing. **Baseline: identical
  `notes.md` content in every doc**; anything cleverer is step 17
- **Doc references belong to projects, never to meetings.** There is no
  per-meeting doc picker, and none should be added — a meeting reaches a doc only
  by carrying a tag whose project is linked to it

### The projects file

**Superseded by step 14, which builds it.** The two boxes below describe a
`projects.toml` holding one project with one doc and a remembered default; step 14
builds `projects.json` instead, holding an id, a display name, a list of doc
references and a one-line description. `referat/projects.py` still owns it, and is
still not `config.py`. Left here as the record of the shape that was planned.

- [ ] `<meetings_dir>/projects.toml`: one table per project, keyed by slug —
      `name`, `glossary`, `gdoc_id`, `tab_id`, `tab_name`, `linked_at`, and
      `default = true` on at most one. A project with no `gdoc_id` is configured
      but unlinked. Beside `.voices/` and the generated `INDEX.md`, so the
      meetings folder stays self-describing and the extension finds it from the
      one path it already has in its settings
- [ ] **`glossary` is used twice, at two different times**, and it is the only
      key in this file that anything outside step 13 reads. Step 12b merges every
      project's glossary into the one global hotword list, which acts before any
      meeting has been assigned to anything; and once a meeting *is* assigned,
      its project's glossary is the second list `/cleanup` normalizes that
      meeting's notes against. So it must be readable for a project that is
      configured and unlinked, and `projects.py` must not make it conditional on
      `gdoc_id`
- [ ] `referat/projects.py` owns that file, **not `config.py`**. `config.py`'s
      docstring promises Referat parses TOML and never writes it back, so hand
      edits and comments survive; that promise is about `config.toml` and stays
      true of it. `projects.toml` is machine-written — rewritten whole through
      `paths.write_text_atomic` on every link and unlink — so it gets its own
      module, is not registered in `config._SECTIONS`, and carries a header
      comment warning that comments in it do not survive
- [ ] `referat project list` — every project, its doc, its meeting count, and
      how many of those meetings are not yet in the doc

### Assignment is manual, with a remembered default

**Superseded by step 14 except for the rule in the second box, which is the one
part of this sub-section that survives intact and gets louder.** `referat project
assign` becomes `referat tag` / `referat untag` over a list of ids; the remembered
default is gone, replaced by the tray's recent-projects toast and its
untagged-on-timeout; and "unassigned" becomes *untagged*, a computed state
meaning an empty `tags` array — never a real project id. **Nothing is ever
inferred from a transcript**, in step 14, in step 15, in step 16 or in step 17.

- [ ] `referat project assign <meeting-id> [<project>]` writes `project` into
      that meeting's `meta.json`. With no project named it uses the remembered
      default; every successful assign moves the default to that project, so the
      common case is one word
- [ ] **No meeting is ever assigned without someone asking.** No keyword rules,
      no guessing from the transcript, nothing inferred at the end of the
      pipeline. That is what keeps "unassigned" a queue somebody works through
      rather than a bucket of quiet mistakes
- [ ] Re-routing is the same command run again. `referat project assign` with a
      different project moves the meeting; the old digest keeps its block until
      a `sync --prune` (see below), because the doc may be shared
- [ ] `referat list` marks unassigned meetings, the way step 8 has it mark
      meetings with unlabeled speakers
- [ ] **Assigning a meeting changes what `/cleanup` would normalize it
      against**, since the project's `glossary` is only reachable once
      `meta.json` names the project. Re-running `/cleanup` after an assign is
      therefore worth doing and is cheap. Nothing re-runs it automatically:
      notes are lazy by design, and a pipeline that regenerated them would be
      the automatic LLM pass this project does not have

### Linking a doc

**Amended: a project holds a list of docs.** `referat project link <name>` becomes
`referat project link-doc <id>` and appends to that list rather than setting a
field; `unlink <name>` becomes `unlink-doc <id> <gdoc_id>` and removes one entry.
Both halves of the create-or-select flow, the `Meetings` tab check and the
hand-off to the browser are unchanged — they just run once per doc being
attached. Everything else in this sub-section stands.

- [x] A new `digest` extra: `google-api-python-client` and whatever the auth
      decision needs. Small — tens of megabytes, not the three gigabytes of
      `transcribe` — but still optional, because `project list` and `project
      assign` must work without it
- [x] `referat/gdocs.py`: auth, `documents.create`, `documents.get`,
      `documents.batchUpdate`, `files.list`. The only module that touches the
      network
- [x] `referat project link <name>` offers **Create new doc**:
      `documents.create(title="<Project> Meeting Digest")`. Summaries go in the
      doc's default tab; read its `tabId` back from `documents.get` and store it
      rather than relying on the default, so every later write is located
      explicitly
- [x] …or **Select existing doc**: search Drive with `files.list`,
      `q = "mimeType='application/vnd.google-apps.document' and name contains
      '<query>' and trashed=false"`, `orderBy=modifiedTime desc`, printed as a
      numbered list in the CLI and as a live QuickPick in the extension
- [x] **"Tabs cannot be created through the API" was true and is not any more.**
      Built on that assumption on 2026-09-04 and corrected the same day:
      `addDocumentTab`, `deleteTab` and `updateDocumentTabProperties` all exist.
      Found by listing the request types rather than by re-reading this box,
      which is the general lesson — a claim about somebody else's API is a fact
      with a date on it, and this one had been carried in four files.
      `--new-tab` and a checkbox in the dialog add one; a created document has
      its tab named from `[digest].new_tab_name` and given a `TITLE` line. Never
      automatic, because adding a tab is a visible change to somebody's document
      — the same rule that stops one being *picked* automatically.
      Original note follows.
- [x] After selecting an existing doc, look for a tab whose
      `tabProperties.title` is `Meetings`. **Tabs cannot be created through the
      API** — there is no `createTab` request — so if it is missing, open
      `https://docs.google.com/document/d/<id>/edit`, say to add a tab named
      `Meetings`, and re-check when told to. Store `gdoc_id` and `tab_id`
- [x] `referat project unlink <name>` clears `gdoc_id` and `tab_id` and leaves
      the doc exactly as it is. Unlinking is not deleting
- [x] Linking ends by running a sync, which is what makes linking an *existing*
      doc backfill every already-assigned meeting automatically

### Every write is located by `tab_id`

- [x] `documents.get` is always called with `includeTabsContent=True`, and every
      `batchUpdate` request carries `tabId` in its `Location` / `Range`. **A
      request with no `tabId` silently targets the first tab** — writing a
      meeting into somebody's unrelated notes tab, with no error to notice.
      Referat writes into the stored tab of a linked doc and into no other

### Reconciliation, not append

**Amended: a sync runs per doc, and the set of meetings is a tag query.** `referat
project sync <id>` iterates that project's docs and, for each, reconciles against
every meeting whose `tags` contain this project and which has a `notes.md`.
Missing / stale / orphan are decided within one doc exactly as below. Two
consequences worth stating: a meeting can be current in one doc and stale in
another, which is why `meta.json`'s `digest` becomes keyed by `gdoc_id`; and an
"orphan" is now an anchor whose meeting no longer carries *this* tag, which is
what an untag produces and is still reported rather than deleted.

- [x] Each meeting's block starts with an **anchor paragraph** whose text is
      `[referat:2026-08-27_1400]`, styled small and gray. A block runs from its
      anchor's start index to the next anchor's start, or to the end of the tab
- [x] A visible text anchor rather than a Docs **named range**: named ranges are
      the API's own mechanism and are the fragile choice — invisible to a person
      editing the doc, destroyed along with their content, and not carried by a
      copy of the document. A text anchor survives everything except deleting
      that line, and deleting it only makes the reconciler re-append the block
- [x] `referat project sync <name>`: `documents.get` the tab, scan the anchors,
      and diff them against every meeting assigned to the project that has a
      `notes.md`, sorted by meeting id — `YYYY-MM-DD_HHMM` sorts chronologically
      by construction, so no date parsing is needed to order them
- [x] **Missing** — no anchor for an assigned meeting: render and insert it
      before the first existing anchor that sorts after it, or at the end. That
      is what puts a backfilled meeting in date order instead of at the bottom
- [x] **Stale** — `sha256` of the current `notes.md` differs from the
      `digest.notes_sha256` recorded in `meta.json`: delete the block's range and
      insert the re-rendered block at that index. This is the whole reason
      `/cleanup` can be re-run
- [x] **Orphan** — an anchor for a meeting no longer assigned: leave it, and
      report it. The doc may be shared and somebody may have written around that
      block; deleting on their behalf is not Referat's call. `sync --prune` is
      the explicit opt-in
- [x] **Apply block operations in reverse document order**, one `batchUpdate`
      per block. Every insert and delete shifts every index after it, so working
      back to front keeps the indices from the single `documents.get` valid.
      This is the single most likely thing in step 13 to be gotten wrong
- [x] Write each meeting's `digest` block to `meta.json` through
      `paths.write_json_atomic` as its block lands, not in one pass at the end,
      so an interrupted sync resumes cheaply instead of rewriting the doc
- [x] Run a sync at the end of `/cleanup`'s follow-up too — or at least document
      that `notes.md` changing is what makes a block stale, so nobody expects the
      doc to update itself

### Markdown to Docs

- [x] `referat/digest.py`: the translator, the anchor scan and the diff. Pure —
      no Google import, so the part carrying all the index arithmetic is
      testable without a network or the `digest` extra
- [x] **No raw Markdown text may ever appear in the doc.** The input is the
      constrained subset `/cleanup` emits: `##` / `###`, `**bold**`, `*italic*`,
      `` `code` ``, `-` bullets one level deep, `[text](url)`, `[[Wikilinks]]`,
      paragraphs. Anything outside that is passed through as plain text rather
      than guessed at
- [x] Two passes: build the block's plain text with a running offset, recording
      `(start, end, style)` spans, then emit **one `insertText` followed by N
      style requests** in a single batch. Style requests do not move indices, so
      the spans stay valid — which is exactly why it is one insert and not one
      per span
- [x] **Docs indices are UTF-16 code units, not Python characters.** One emoji
      in a note shifts every span after it. Offsets are
      `len(s.encode("utf-16-le")) // 2`, and there should be a test with an
      emoji in it
- [x] `createParagraphBullets` converts *existing* paragraphs into a list, so
      the inserted text must not contain the `- ` itself. A second nesting level
      is a leading tab character
- [x] The block's date line is Heading 3, so `notes.md`'s own `##` becomes
      **Heading 4** and `###` becomes **Heading 5**. Its `#` H1 is consumed into
      the date line's title and not emitted twice
- [x] `[[Wikilinks]]` have no target in a Google Doc: render them as bold text
      with the brackets stripped. A reader outside this machine should see
      `Anna`, not `[[Anna]]`
- [x] Acceptance check on the produced plain text: no `**`, no leading `#`, no
      leading `- `, no `](`, no `[[`. Any hit is a translator bug, and it is
      cheap enough to assert on every render

### Date headers

- [x] Each block's first content line is Heading 3, text `YYYY-MM-DD — <title>`,
      em dash. The title is the H1 of `notes.md` falling back to the meeting id —
      the same rule as `referat index`, sharing that helper rather than growing
      a second reader of the same file
- [x] The Docs API cannot insert an @-date smart chip; there is no request type
      for it. Keep the date as a fixed-width prefix in leading position, so that
      if the API ever gains one, swapping it for a chip is a one-request change
      to this line and leaves the ` — <title>` remainder alone. Noted in
      `CLAUDE.md` too

### meta.json

- [x] ~~`project`: the project slug, absent when unassigned~~ **Superseded by step
      14's `tags`**, a list of project ids. It is a list because a meeting may
      belong to several threads of work, and it holds *ids* rather than names so
      a rename touches one file. Absent or empty means untagged
- [x] `digest`: **keyed by doc**, `{"<gdoc_id>": {tab_id, notes_sha256,
      written_at}}` — what was written, where, and from which bytes of
      `notes.md`. It was a single object when a project had one doc; a meeting
      can now be current in one doc and stale in another, and one flat object
      could not say so. That is still what makes reconciliation and a correction
      cheap: the sync reads `meta.json` and one `documents.get` per doc, and never
      has to re-read the doc's prose to work out what changed
- [x] `status` reaches `synced` when **every** doc of **every** tag is current,
      and drops back to `notes_written` the moment a `notes.md` sha stops
      matching. The lifecycle field is step 14's; this is the step that writes its
      last two transitions
- [x] The folder contract table does not change. No new file appears in a
      meeting folder; the digest lives in the doc and in these `meta.json` keys

---

**Steps 14-17 were planned on 2026-09-01** and re-sequence the work above.
Numbers 1-13 do not move: `SETUP.md`'s sections are numbered to match, CHANGELOG
entries are titled by step, and this file cross-references by number, so
renumbering would quietly falsify all three — which is also why step 12b is a
letter rather than a renumbering. The order to build in is **14, 15, 12, 16, 13,
17**, with **12b independent of all of it**: hotwords need the projects file to
exist before glossaries contribute anything, and nothing else, so it can be built
before or after 14 and slots in wherever it is wanted.

Steps 14-16 are **not** behind the steps-10-13 gate above. That gate is about
having real transcripts to tune `/cleanup` against; tagging a meeting needs
nothing of the sort. Step 13 stays gated, twice over now.

### What building it settled

- [x] **The subset had to move first.** `ui/richtext.py` has said since it was
      written that it is the manual half of this step — but it imports PySide6 at
      module scope and lives in the one sub-package, so `digest.py` could not
      reach it without inverting the layering and putting Qt behind a network
      push. `referat/markdown.py` is the grammar, extracted; each renderer kept
      its own walk, because HTML nests and Docs styles flat ranges, and
      flattening them into shared tokens would have changed what a paste
      produces. **What stops them drifting is a check and not an abstraction**:
      `scripts/digest_report.py` renders every real note through both and
      compares. Byte-identical over all fourteen before and after
- [x] **A style reset, which no box here called for.** `insertText` inherits the
      character and paragraph style at the insertion point, so a block inserted
      immediately before an existing anchor arrives small and gray and one
      inserted after a heading arrives as a heading. The batch resets everything
      it just inserted before styling any of it, which makes the result
      independent of what happened to be there
- [x] **The acceptance check is per paragraph, against the kind `blocks` gave
      it.** As specified — no `**`, no leading `#`, no leading `- `, no `](`, no
      `[[` — it is a flat scan, and a flat scan fires on a *documented*
      behaviour: `HEADING_RE` matches one to three hashes, so a `####` line falls
      through to a paragraph carrying its own hashes. A plain paragraph starting
      with `#` is explicitly not a bug; a *heading* paragraph that still has
      them is
- [x] **`synced` needed a way back and there was none.** A meeting reaches it
      when every doc of every project it carries holds the current notes, and
      re-running `/cleanup` makes that false. `cli.set_notes_written` now accepts
      `synced` and drops it to `notes_written` — written from the function that
      *knows* the notes just changed, because deciding it later by comparing a
      sha would be inferring a lifecycle from the filesystem
- [x] **The reverse-order rule is asserted where it is produced**, not hoped for
      where it is applied, and the sort key is `(at, meeting_id)` — the second
      half because two missing meetings can land at the same index and applying
      the later-sorting one first is what leaves them in date order. Twelve cases
      through a twenty-line simulator of `insertText` and `deleteContentRange`
- [x] **The fixture's first version failed six of those twelve and every failure
      was its own.** The fake `documents.get` and the simulated document built
      their text separately, so the indices the reconciler was handed did not
      describe the string they were applied to. One `block_text` serves both now,
      which is what makes a miss there mean something. Worth writing down: a
      fixture that can be wrong in the same way as the code is not a fixture
- [x] **`tabId` is checked mechanically, at the one place every write passes
      through.** A request without one silently targets the first tab, and the
      consequence is a meeting in somebody's unrelated notes with nothing to
      notice. `find_tab` recurses `childTabs` for the same class of reason: a
      missed nested tab is not an error, it is a *second* `Meetings` tab
- [x] **The `#` H1 is dropped from the block** — the Heading 3 date line carries
      that exact string already, from the same `index.meeting_title`, so every
      block would announce its title twice
- [ ] **The byline's relative `[transcript.md](transcript.md)` is left alone**,
      reversing the recommendation made while planning. It renders as the plain
      words `transcript.md`; dropping the clause would be `digest.py` editing
      somebody's prose, which is larger than an odd-looking line. Revisit if a
      shared doc's readers actually ask what it means
- [x] **The extra adds two native files and both are unsigned** —
      `google/_upb/_message.pyd` and `cryptography/hazmat/bindings/_rust.pyd`.
      Both are off the recording path, so a Smart App Control block costs a
      digest push and nothing else, which is the degradation the rule permits.
      Re-run the sweep after an upgrade, as with PySide6
- [x] **Run against a real document on 2026-09-04.** Consent given at a terminal,
      `link-doc --create` made the doc and backfilled it, and the block came out
      right: anchor, Heading 3 date line, Heading 4 sections, real bullets, no
      raw Markdown. A second sync wrote nothing, a re-render replaced in place,
      and the meeting reached `synced`. Auth refreshes silently — a Drive search
      the next hour needed no prompt. What is still unexercised is `--prune`
      against a real block, and attaching a document that already has prose in it

### Auto-sync (2026-09-04)

Asked as *"do I have to manually sync? What is the possibility of having a
checkbox per-project to turn on auto-sync?"* Built the same day.

- [x] **`Project.auto_sync`, on by default**, consulted by `cli.write_notes`
      after the cleanup pass. `cli.auto_sync_meeting` pushes that meeting into
      every linked, auto-syncing project it carries
- [x] **Per project rather than global**, because the answer differs by document:
      one shared with a room full of people is exactly the one somebody wants to
      read before it updates itself
- [x] **Absent means yes on load**, so the four projects already linked read as
      auto-syncing without a migration -- the same move `MeetingStatus` makes for
      its legacy values, and `SCHEMA_VERSION` stays at 1 because a key was added
      rather than changed in meaning
- [x] **It never fails the cleanup.** A document that did not update is a line in
      the message; `notes.md` is on disk whatever Google says, which is the rule
      `write_notes` already ran under for a lifecycle it could not record
- [x] **Silent with no `digest` extra**, since that is a machine that does not do
      digests rather than an error, and a sentence after every cleanup pass would
      be noise about a feature nobody asked for
- [x] **An archived project still auto-syncs**, because archiving hides a project
      from the tag picker and changes nothing else -- a digest quietly going stale
      would be archiving costing something real
- [x] **This amends a Convention and is recorded as one.** *Only when the user
      asks* moves from the push to the **linking**, which is a far more
      deliberate act. What does not widen is what leaves the machine
- [x] `referat project auto-sync <id> [on|off]`, `referat notes --no-sync`, and a
      checkbox that writes immediately rather than waiting for Save -- it is a
      switch, not a field somebody is part-way through typing

### Left open

- [ ] **Auto-sync has not yet run off a real `/cleanup` pass.** It was driven by
      clearing a meeting's `digest` record, which is what a re-cleanup looks like
      to the reconciler, and it re-rendered and reached `synced` correctly. What
      is unexercised is the whole of `write_notes` end to end with a document
      attached
- [ ] **`--prune` has never removed a real block.** It is the one operation here
      that destroys somebody's prose, and it is the least exercised
- [x] **Closed the same day, because it stopped being hypothetical immediately.**
      The box said to watch this the first time somebody annotated a digest; the
      first real documents settled it before that, being somebody's meeting notes
      with tabs already named for their contents and a hundred and thirty
      thousand characters of prose in one of them. A block now runs
      `[referat:<id>]` ... `[/referat:<id>]` and a sync replaces only what is
      between a matching pair. The alternative the box dismissed — a heuristic
      end — is still dismissed; a written terminator is not a heuristic. An
      unclosed block counts as **stale whatever its sha says**, so the old format
      upgrades itself on one sync rather than needing a migration
- [x] **The `Meetings` tab requirement went with it.** It assumed a document
      Referat could have to itself. `--tab` names any tab by title or by id, and
      `--doc` takes a share link whose `?tab=` says which one you were looking
      at. Still no fallback to the first tab, not even in a single-tab document:
      that is not an ambiguity about which tab, it is a question about whether a
      digest belongs in the middle of somebody's prose
- [ ] **A rename of a person goes stale in a pushed doc**, which is step 20c's
      problem and has no story here. Report it, never reach into the doc — the
      same rule as an orphaned anchor
- [ ] **Re-read the `/cleanup` prompt before the first digest doc is shared with
      anybody.** The standing item under *Surfaced later* is now live rather than
      hypothetical: three of the fourteen notes carry a `SPEAKER_02` or a
      `SPEAKER_07 (addressed as Asmus)` in their byline, and those are people who
      never read the prompt


## 14. Projects as labels: the data model and the CLI

**Built on 2026-09-01.** Everything below is done and verified against a scratch
meetings folder; what the build decided rather than merely implemented is in
"What building it settled", at the end of this section.

**Everything else renders this or calls it, so it is first.** A project stops
being a container a meeting is put into and becomes a label a meeting carries: a
meeting has zero or more of them, and *untagged* is the computed state of an
empty list, never a project called "untagged".

### `projects.json`

- [x] `<meetings_dir>/projects.json`, owned by a new `referat/projects.py`. Per
      project: `id`, `name` (what a person reads), `docs` (a list of `{gdoc_id,
      tab_id, tab_name, linked_at}`), `description` (one line, reserved for step
      17 and written by nothing until then), `created_at`
- [x] **JSON rather than the `projects.toml` step 13 specified.** That choice was
      made to keep `config.py`'s promise — Referat parses TOML and never writes it
      back, so hand edits and comments survive — while still having a
      machine-written file. JSON drops the tension instead of managing it: it goes
      through the `paths.write_json_atomic` that already exists, it is rewritten
      whole without apology, and it needs no header comment warning that comments
      do not survive. `projects.py` still owns it and is still **not** registered
      in `config._SECTIONS`; the promise is about `config.toml` and stays true
- [x] Beside `.voices/` and the generated `INDEX.md`, so the meetings folder stays
      self-describing and the extension finds it from the one path it already has
- [x] **The id is a slug made from the name at creation and immutable after
      that.** `rename` changes `name` and nothing else. That is the entire reason
      `meta.json` stores ids rather than names — a rename touches one file, and
      every transcript, every meeting record and every doc anchor is untouched by
      it. Collisions take a `-2` suffix, the way meeting ids do
- [x] What a project name may be is decided in **one** place. Reuse
      `voices.name_complaint` if its rules fit; give `projects.py` a sibling if
      they do not. Two validators would eventually disagree about the same string

### `meta.json` gains `tags`

- [x] `tags`: a list of project ids. Absent or `[]` is untagged. `Meeting.load` is
      already tolerant of missing keys, so every meeting recorded so far loads as
      untagged and there is nothing to migrate
- [x] **Deleting a project orphans its tags, visibly, and cascade-deletes
      nothing.** `referat project rm` removes the project from `projects.json` and
      touches no `meta.json`, no `notes.md` and no Google Doc. An id left behind in
      `tags` with no project to resolve it is rendered as an orphan chip rather
      than hidden, because a tag quietly vanishing off three meetings is how you
      lose track of what a meeting was about

### An explicit lifecycle, in the `status` field that already exists

- [x] Widen `MeetingStatus` in `referat/meeting.py` rather than adding a second
      field beside it:

      ```
      recording -> recorded -> transcribing -> gate_failed | transcribed
                                                          -> notes_written -> synced
      failed  (transcription raised — a different thing from gate_failed)
      ```

      One field, one authority. A parallel `state` key would be a second place to
      say what a meeting is, and this file already records `voices_dir` and
      `format_duration` each being pulled back from exactly that
- [x] **`gate_failed` is the point of the exercise.** It is today a three-way
      inference — `status == done` **and** the WAVs still on disk **and** the
      folder still in staging — computed nowhere, re-derived by every reader, and
      impossible to render honestly. It becomes a value the pipeline writes
- [x] `Meeting.load` maps the legacy values **purely**, with no filesystem
      inspection: `"stopped" -> recorded`, `"done" -> transcribed`. It must not
      look at the folder to decide a meeting was gate-failed; that is the
      inference being removed, and doing it in the loader would simply hide it
- [x] Write sites to move: `recorder.py:562` (`STOPPED` → `RECORDED`),
      `transcribe.py:874` (`DONE` → `TRANSCRIBED`), and a new `GATE_FAILED` where
      `release_audio_if_clean` comes back False. The read gate at
      `transcribe.py:788` tests `status is DONE` and must test `TRANSCRIBED`
- [x] The few `done`-with-audio-kept meetings already on this machine are **not**
      auto-corrected. Their next `rerun` writes the right value, and there are
      three of them. A migration would be more code than the problem
- [x] `cli.audio_state` stays exactly as it is. It reports *audio*, which is a
      real question a person asks; what it stops doing is standing in for the
      lifecycle
- [x] **No UI infers state from which files exist.** All three — the table, the
      dashboard, the sidebar — render this field

### The CLI owns every mutation

- [x] `referat project add <name> | rename <id> <name> | rm <id> | link-doc <id> |
      unlink-doc <id> <gdoc_id> | list [--json]`
- [x] `referat tag <meeting-id> <project-id>...` and `referat untag <meeting-id>
      <project-id>...`, both idempotent and both taking several ids at once
- [x] `referat state <meeting-id> notes-written` — the one transition no other
      process can make, because `/cleanup` is forbidden from touching `meta.json`
      and that rule is not moving. **It accepts that transition and no other**,
      and only from `transcribed`: `synced` is written by `project sync`,
      `gate_failed` and `transcribed` by the pipeline. A verb that let a caller
      claim any state would turn the field from a record into a comment
- [x] **None of these need an optional extra** — `project add|rename|rm|list`,
      `tag`, `untag` and `state` are a JSON read and a JSON write. That is step
      8's rule, widened rather than moved. Only `link-doc` and `sync` need
      `digest`, and only `rerun` needs `transcribe`
- [x] They go in the existing `argparse` subparser block in `cli.py` and the same
      `if args.command == ...` chain. `project` takes a second positional verb;
      resist growing a second dispatch mechanism for it
- [x] **The extension and the tray call these. Neither reimplements them.** The
      projects file, the tag logic, the lifecycle vocabulary and the name rules
      live in Python once, for the same reason `format_duration` and
      `voices.unknown_speakers` do

### `referat list` and `list --json`

- [x] The text table gains a `TAGS` column and prints the wider `STATUS`
      vocabulary. `HEADERS`, `RIGHT_ALIGNED`, `list_row` and `list_document` move
      together
- [x] `list_document` gains `"tags": [...]` per meeting and **one top-level
      `"projects"` block**, id to display name, produced by the same function
      `project list --json` calls. One subprocess then feeds the whole sidebar,
      and the join between a tag id and its name cannot drift
- [x] `"status"` keeps its key and simply carries more values, so the extension's
      `MeetingJson` in `src/cli.ts` widens a union rather than growing a field
- [x] `index.py`'s dashboard picks the new status values up for nothing, and
      should be read once afterwards to check they render as sentences a person
      wants to see in `INDEX.md`. Read: `notes_written` in a rendered Markdown
      table is not a sentence anybody wants. `index.status_words` prints the
      underscores as spaces — a **formatting rule, not a map** of value to
      sentence, because a map would be a second vocabulary beside `MeetingStatus`
      and the first state nobody added a row for would render as a blank. The
      terminal table keeps the underscores: its columns are space-separated, and
      a two-word cell there reads as two columns

### What building it settled

- [x] **`projects.name_complaint` is a sibling of `voices.name_complaint`, not a
      call to it.** The plan left the choice open. Three of the four speaker rules
      — `ME`/`REMOTE`, `SPEAKER_NN`, no `:` — are about the transcript and mean
      nothing for a thread of work, so reusing it would have imported four checks
      to get the use of none. What a project name actually needs is that it
      survives `slugify` and that it is not `untagged`, which is the *absence* of
      a project and would sit in the sidebar next to a filter meaning something
      else. Two validators for two kinds of name is fine; two for one kind was
      what the plan was guarding against
- [x] **A projects file that exists but will not parse refuses to be written.**
      `ProjectsDB.load` never raises — step 12b calls it from inside the pipeline
      — so a malformed file loads as *no projects*. That is right for reading and
      catastrophic for writing: the next `project add` would replace a file full
      of projects with an empty list and nothing would say so. `unreadable` is
      recorded on the object and every mutating verb refuses on it. `tag` refuses
      too, because with no ids loaded it would reject a correct id as a typo.
      **The known-voices database has the same shape and no such guard** — see
      "Surfaced later"
- [x] **`meeting.resolve_meeting` is shared rather than copied.** `tag`, `untag`
      and `state` all take a meeting id and all have to say the same two things
      about a bad one. It returns the meeting or None *and the complaint*, and
      each caller adds its own `referat <command>:` prefix — which is the only
      part they disagree about. `label._resolve_meeting` now calls it too, where
      it used to hold the second copy
- [x] **`list`'s TAGS column prints ids, not display names.** Names read better,
      but the next thing typed after reading that column is `referat untag
      <meeting> <id>`, and a table you cannot copy out of is worse. An id no
      project resolves gets a trailing `?`. `project list` is where the two are
      joined, and it also counts the orphans
- [x] **`link-doc`, `unlink-doc` and `sync` are absent from the parser**, not
      present and answering "not built yet". A verb that exists and refuses reads
      as a bug; argparse listing the four that do exist does not

## 15. The extension becomes the primary UI — DELETED (2026-09-04)

**Built on 2026-09-01**, except the doc-reference half of project CRUD, which
cannot be built until step 13 exists — see the box for it below. What the build
decided rather than merely implemented is in "What building it settled"; what
still needs somebody to press F5 is at the end.

**Superseded by step 20 on 2026-09-02 and deleted at step 23 on 2026-09-04.**
It was the primary UI for four days. The condition step 20 set — deleted once the
command center reaches parity — was met when the window grew *Re-transcribe...*
and *Promote...*; step 23 has the audit of what parity meant, action by action,
and the three lessons out of this step that outlived the code. Everything
built here keeps working and keeps being fixed when it breaks; nothing new is
added to it. Three hand-check boxes that nobody will now perform on a retiring
surface have been dropped from the end of this step — the F5 sidebar check, the
gate-failed modal and the status bar item — and none of them was ever checked,
so no completed item was deleted. **Read the reasoning as current**, though: the
one-implementation rule, the page-renders-itself split, `mutate` handing back the
CLI's own complaint and the refusal to add a meetings-folder setting are all
carried into step 20 unchanged, and are argued here better than they will be
argued again.

**Replaces the TreeView built in step 11.** `MeetingsProvider`, the three node
classes, the composed `contextValue` string, the five `view/item/context` entries
and `labelPanel.ts` all go — and all of them did. A webview inside the extension is **not a web UI** —
that rule is about Flask, FastAPI, localhost and a browser front end, and there is
still none of it.

### The sidebar

- [x] One `views` entry, `{"id": "referat.meetings", "name": "Meetings", "type":
      "webview"}`, with `activationEvents` following it
- [x] One row per meeting, **reverse chronological** — the tree already reverses
      `referat list`'s oldest-first order, and that stays a presentation choice.
      Do not reorder the CLI
- [x] Each row: date, duration, project tag chips, and a **lifecycle strip**
      rendering step 14's `status` field. Nothing in TypeScript re-derives a state
- [x] **A visible off-ramp for gate-failed meetings stuck in staging**, and this
      is the sharp one. A gate-failed meeting is in `%LOCALAPPDATA%` because
      `promote_meeting` refuses to move a folder that still holds WAVs, and that
      refusal is the invariant the staging split exists for: no WAV ever reaches
      the meetings folder, because deleting a file inside a synced folder does not
      delete it. So *accept* cannot mean "promote with the audio". It means
      **delete the WAVs and then promote**, irreversibly, behind a modal that says
      so in those words. It needs a CLI verb — `referat rerun --accept`, or a
      `referat promote <id> --release-audio` — and it closes the open item under
      "Surfaced later" about a meeting that never transcribes cleanly sitting in
      `%LOCALAPPDATA%` forever
- [x] Speaker labeling folds into an expandable section of the row. It still
      drives `referat label --speaker --name` and still reimplements no part of
      `label.apply_name`; `media/label.css` and `media/label.js` become the
      sidebar's assets
- [x] Keep the file watcher exactly as it is — `RelativePattern` over **both**
      roots, 300 ms debounce — and keep `cli.ts`'s spawn path exactly as it is:
      `<repoRoot>\.venv\Scripts\python.exe -m referat.cli` with the working
      directory at the repository root. Both were hard-won and neither changes

### Project CRUD and the tag picker

- [x] Create, rename and delete, every one of them a shell-out to `referat
      project ...`. `src/projects.ts` holds the QuickPick flows; deleting warns
      that the ids stay behind on their meetings as orphans, which is exactly
      what `project rm` does
- [x] **Attach and detach doc references — cannot be built yet, and this is the **Dropped on 2026-09-06: the extension was deleted at step 23.**
      one bullet of step 15 that is not done.** `referat project link-doc`,
      `unlink-doc` and `sync` are deliberately absent from the parser until step
      13, on step 14's reasoning that a verb which exists and answers "not built
      yet" reads as a bug. So there is nothing for the extension to shell out to.
      Build the UI half **with step 13**, in `src/projects.ts` beside the three
      verbs that do exist — noted again there in a docstring, so it is found from
      the code as well as from here
- [x] The tag picker is a `showQuickPick` with `canPickMany` over the `projects`
      block `list --json` already returned, pre-checked with the meeting's current
      tags, with **"Create project '<typed>'" as the last entry**, driven by
      `onDidChangeValue`. Applying a pick is a `tag` and an `untag` for the
      difference
- [x] Multi-tag editing lives here and only here, and so does backfilling the
      untagged: a filter that shows every meeting with an empty `tags`, worked
      through with the same picker. That view *is* the queue, the way "Unknown
      speakers" is the queue for `referat label`

### Notes, and ambient state

- [x] *Generate notes* streams `claude`'s stdout: the last non-empty line into
      `progress.report({message})`, the whole stream into the output channel. On
      exit 0 it calls `referat state <id> notes-written` and opens `notes.md`
      rendered
- [x] Unchanged and worth restating because this is the file that describes it:
      `claude` is resolved at **spawn time** and never persisted, in the order
      setting → `Anthropic.claude-code` extension path → `PATH`; it is spawned
      with `--allowedTools "Read,Write,Glob"`; and **the extension never handles
      credentials**. There is no Anthropic API key in this project, in any file,
      in any setting or in any environment variable
- [x] A **status bar item** for ambient state — "recording 12:34",
      "transcribing 2" — from `referat status`, which already reports the tray's
      state, meeting id and job count, and already refuses to be blocked by a
      broken `config.toml`
- [x] **Still no meetings-folder setting.** `referat.repoRoot` and
      `referat.claudeBinary` stay the only two; where meetings live is
      `[paths].meetings_dir` in the repository's `config.toml`, and a second place
      to say it is a second thing that can disagree with the recorder

### What building it settled

**Built on 2026-09-01.** The two Python additions the extension needed came
first, and both were driven against a scratch meetings folder before a line of
TypeScript was written.

- [x] **The off-ramp verb is `referat promote <id> [--release-audio]`**, not
      `rerun --accept`. `rerun` means "transcribe again from the audio", and a
      flag on it meaning "do not transcribe, delete the audio" would invert the
      command it is attached to. `promote` means one thing, and the flag is what
      makes the irreversible half visible at the prompt. The **bare** form is
      useful in its own right: it moves a staged meeting whose audio has already
      gone, which is the retry for a `promote_meeting` that failed to move the
      folder — and with WAVs present it refuses and names them, which is the
      whole safety of having a bare form at all
- [x] **`--release-audio` writes `transcribed`.** `gate_failed` says the *audio*
      was not trusted enough to delete; the transcript was never what was in
      doubt, so a person accepting it leaves the meeting in the state a clean run
      would have produced. Anything else would have needed a seventh lifecycle
      value meaning "transcribed, and we decided to live with it"
- [x] **`transcribe.release_audio` was split out of `release_audio_if_clean`.**
      One decides, the other acts. Two things now delete a meeting's WAVs — the
      gate and a person overruling it — and `audio_released` has to mean the same
      thing whichever wrote it. The split also fixed a latent case: the old loop
      `stat`ed each file and treated a missing one as a failure, which is wrong
      for a half-released meeting and right for nothing
- [x] **`referat status --json` is the fourth JSON document.** The status bar had
      to read the tray's state, and the alternative was parsing prose written for
      a person. `elapsed` is `format_duration`'s output rather than a number of
      seconds, deliberately: had it been seconds, the bar would have formatted it
      in TypeScript, which is the duplication this whole extension is arranged to
      avoid — and the price is that **the status bar does not tick**. It says what
      Python said when it was last asked. `stale` is the other field worth having:
      `running` false with `stale` true is a tray that died, which is a different
      thing from no tray having run
- [x] **The status bar needs a poll as well as a watcher.** The tray writes
      `status.json` on every transition, so a watcher catches all of them — and
      catches nothing at all when the tray is *killed*, because a dead process
      writes no file. Without the 30-second poll the bar would sit there claiming
      a recording was still running
- [x] **The page renders itself; the host only posts documents.** Re-assigning
      `webview.html` on every refresh is simpler to write and wrong to use:
      transcription rewrites `meta.json` several times a meeting, and every one of
      those would collapse an expanded row and stop a snippet mid-playback. So
      the HTML is set once and `media/sidebar.js` builds the DOM — with
      `textContent` and never `innerHTML`, since a meeting title comes out of
      somebody's `notes.md` and a speaker's line out of a transcript
- [x] **`localResourceRoots` cannot be set until Python has answered.** The
      snippet players need the meeting roots to be resource roots, and the roots
      are only known from the first `list --json`. The provider therefore
      re-assigns `webview.options` when the roots change and keeps the last
      listing, so a page that reloads is answered from memory instead of with
      another interpreter start
- [x] **Every mutation goes through one function, `cli.ts`'s `mutate`**, which
      hands back the CLI's own complaint. That is what makes "the refusal you see
      is the sentence Python printed" a property of the code rather than a habit:
      there is no path by which the extension can invent a reason for refusing
- [x] **The tag picker is written out rather than `showQuickPick`**, because that
      helper cannot add an item in response to what has been typed and *Create
      project "…"* is the entry that matters. Choosing it is a detour rather than
      an answer: it creates the project, checks it, and hands the picker back, so
      one accept does not silently mean both "make this" and "and that is the
      whole tag list"
- [x] **An orphaned tag is offered in the picker, checked and marked.** It is the
      one tag somebody actually needs to remove, and a picker built only from the
      known projects would have been the one place it could not be

### Still to check by hand — nothing in a session can click VS Code

**Three boxes were dropped from this list on 2026-09-02** — the F5 sidebar
check, the gate-failed modal and the status bar item. All three were hand-checks
of an extension now in maintenance, and the equivalent checks belong to step 20's
window instead. The Python half of the gate-failed off-ramp is verified and stays
verified; it is the button that is no longer worth pressing.

- [x] The **Untagged only** toggle, and the **Tags…** picker: applying a pick
      round-trips, *Create project "…"* creates and checks, and unchecking an
      orphan removes it. **Checked on 2026-09-01 and both halves were broken** -
      *Create project* created one that could not then be applied, and the picker
      showed a stale project list. Both fixed; the orphan path is still unchecked,
      since no project has been deleted yet
- [x] The **Speakers** section: it expands, a snippet actually plays through the
      `<audio>` element, and a name applies. **Done on 2026-09-01**, on
      `2026-09-01_2102` - four speakers named through the panel, the snippets
      played, and the user's verdict was that the UI "worked well". That also
      closes step 11's identical box and its separate worry about whether
      Chromium would decode 16 kHz mono PCM: it does
- [x] *Generate notes* streams into the toast, flips the row to `notes_written`
      and opens the notes rendered. Driven twice on 2026-09-01

## 16. Tagging from the tray — DECLINED (2026-09-03)

**Not being built, by decision rather than by neglect.** Tagging happens when
meetings are looked over at a spare moment, on the command center's Meetings tab,
where the picker and the untagged inbox already are — a prompt at the end of
every recording asks a question nobody has at that moment. The untagged queue on
the dashboard is what makes sure a meeting does not stay untagged, which is what
this step was really for.

Two consequences worth stating. **The WinRT toast dependency is not taken**, so
the base install keeps exactly one deliberate exception to the
minimal-dependencies rule (PySide6) rather than two. And `CLAUDE.md` described
this step in the *present tense* for some time, as though the toast and the *Tag
recent…* submenu existed; they never did, and that has been corrected. The boxes
below are left unchecked and unedited as the record of what was designed.


**Amended on 2026-09-02 by step 20, which moves the ground under it.** Two things
changed and neither cancels this step. The tray is a `QSystemTrayIcon` in a Qt
process rather than a `pystray` icon, so "Tag recent…" is a Qt menu built the
same way and on the same lazy rule; and the on-stop toast now has somewhere
better to land than a submenu, since **opening the command center on the untagged
inbox** is one click and shows what is already tagged. The toast itself is
unchanged in every respect that mattered: `QSystemTrayIcon.showMessage` is as
buttonless as `icon.notify` was, so a real WinRT toast is still the only way to
get an actionable one, `windows-toasts` is still the base dependency that costs,
and the AUMID question below is untouched by the host process changing. Build
this after step 20's phase 2, when there is an inbox to open.

### The on-stop toast

- [ ] On stop, a toast asking **"Project(s)?"** offering recent projects, with
      **untagged on timeout** — which costs nothing, being simply not acting
- [ ] **`pystray`'s `icon.notify()` cannot do this.** It is a balloon with no
      buttons that does not persist in Action Center. This needs a real WinRT
      toast, which means `windows-toasts` (or raw WinRT) as a new **base**
      dependency — against the minimal-dependencies convention, deliberately, and
      recorded here as a deliberate exception rather than discovered later as an
      inconsistency
- [ ] It needs an **AUMID**, which in practice is a Start Menu shortcut. Step 9's
      autostart shortcut is the candidate. Whether it actually carries an
      `AppUserModelID` is a thing to check, not to assume
- [ ] **The risky half is activation, not display.** While the tray process is
      alive an in-process notifier's activation callback is enough. Reaching a
      *missed* toast from Action Center after the tray has restarted needs a
      registered CLSID and a COM activator, and that is the part most likely not
      to work here. Build the fallback deliberately: if activation proves
      unreliable, the toast degrades to a buttonless notification and the menu
      below carries the whole feature
- [ ] **The toast must never block or break the stop path.** Recording robustness
      beats everything else; a notification that fails is a log line, the same
      rule diarization runs under

### "Tag recent…"

- [ ] A tray submenu of untagged meetings from the last 7 days, generated lazily
      when the menu opens — `pystray` menu entries take callables, so the list is
      built on open rather than at startup and cannot go stale
- [ ] Each meeting expands to the project list plus **"New project…"**.
      **Single-tag quick assignment only**; multi-tag editing stays in the
      extension, where there is room to show what is already on
- [ ] Native tray menus are enough. **No Qt**, no second GUI toolkit.
      **Overtaken on 2026-09-02**: step 20 makes Qt the *first* toolkit and the
      tray icon one of its widgets, so there is no second one to refuse. The
      sentence this box was really making survives intact — a tag applied from
      the tray is a menu item and never a window — and it is now free, since the
      menu and the window are the same process
- [ ] The tray **imports** `projects.py` and the tag functions rather than
      spawning a subprocess of its own CLI. The one-implementation rule is about
      there being one implementation, not one process boundary — the extension
      shells out because it is TypeScript and has no other way in. Said out loud
      so nobody later "fixes" this in either direction

## 17. Notes splitting (experimental, and clearly optional)

**Last, and gated on 13 working.** A pass in which the human's chosen tags, plus
each project's one-line `description` from `projects.json`, are handed to
`/cleanup` — or a `/split` variant of it — which partitions the notes content
among *those projects only*.

- [ ] **It never assigns projects.** The tags are an input to the split, never an
      output of it. Nothing is inferred from a transcript; that rule does not bend
      for the step that would most like it to
- [ ] **The failure mode is the baseline**: over-inclusive notes in each doc,
      which is exactly what step 13 does anyway. That is what makes this safe to
      try, and it is the reason to build the baseline first and this last. If the
      split is ever off, turn it off and nothing is lost
- [ ] The pass reads `projects.json` with the `Read` tool it already has. The
      `.voices/` deny rule is untouched, `Bash` is still not among its allowed
      tools, and it still may not modify `transcript.md`, `meta.json` or any
      `.wav`
- [ ] Keep it out of `README.md`'s roadmap. It is an experiment, and the roadmap
      is a promise

**Amended on 2026-09-08 by step 24.** If this is ever built, the section
extraction it introduces — a `## Project: [TAG]` section per tag and a
`## General` — has **one implementation**, read by the digest push and by the
recap bundle alike; a second reading of the same headings is the drift this
codebase keeps deleting. Step 24 does not wait for it, and takes each whole
`notes.md` until it exists.

## 18. Deleting a meeting

**Built on 2026-09-01**, after the first real remote call made the absence
obvious. Numbered rather than filed under "Surfaced later" because it adds a verb
and a button, which is a step's worth of work rather than a repair.

- [x] `referat delete <meeting-id> [--yes]`, beside `promote` in the one argparse
      block, in `NEEDS_CONFIG`, and in the `if args.command == ...` chain
- [x] Resolve through `_resolve_meeting` so it reaches **both** roots. A
      `gate_failed` meeting still in staging is a likely target and is the one
      holding ~460 MB of WAV per hour
- [x] Refuse while the meeting is `recording` or `transcribing`. Deleting a folder
      underneath the tray would lose audio mid-write, which is the one thing
      recording robustness says may never happen
- [x] `paths.remove_meeting_dir`, guarded on the folder holding a `meta.json` —
      the definition of a meeting `list_meeting_dirs` already uses. The one
      function in `paths.py` that logs rather than raises: a recursive delete's
      reason is worth keeping even where the caller only needs the boolean
- [x] **Two sentences in the confirmation**, because both are true and neither is
      obvious. A meeting inside a synced meetings folder is not really gone (the
      sync client keeps deleted files and prior versions for weeks — the same fact
      that created the staging folder), while a staged one is; and the voiceprints
      it contributed stay in `.voices/`, because deleting a meeting is not
      deleting a person and `label --forget <name>` is what is
- [x] `--yes` for callers with no terminal, the `label --forget` pattern: the
      prompt answers *no* on EOF
- [x] Regenerate the meetings `INDEX.md` afterwards, or the dashboard keeps
      describing a meeting that is gone
- [x] A **Delete…** button per sidebar row, last and `action-danger`, behind a
      modal carrying the same two sentences. The `onDidDelete` watcher already
      refreshes the view, so nothing new was needed there
- [ ] **Deleting a meeting does not delete its voiceprints**, deliberately: the
      embeddings it contributed stay in `voices.json` stamped with its id, and
      `--forget` is the only thing that removes a person. Decided rather than
      overlooked, and the confirmation says so — but revisit if the real reason
      for a delete usually turns out to be "this should never have been
      recorded", which is exactly the case where somebody would want both

## 19. Organizing the sidebar

**Built on 2026-09-01.** One flat reverse-chronological column was fine for five
meetings and will not survive a year of them. Nothing beyond step 15's *Untagged
only* toggle had ever been planned.

- [x] Group rows into collapsible sections **by project**: one per project ordered
      by its most recent meeting, then one per orphaned tag id, then *Untagged*
      last, which is the queue
- [x] A meeting carrying two tags is drawn under **both**. A project is a label
      rather than a container, and a grouping that had to pick one would be the
      first thing to disagree with the tags
- [x] Orphaned ids get their own section rather than being folded in anywhere, for
      the reason step 14 renders them as chips: a tag vanishing quietly off three
      meetings is how you lose track of what a meeting was about
- [x] A search box beside the toggle, matching title, id, date, status and project
      name. Empty sections hide while searching
- [x] Folded sections survive a reload through `vscode.setState`. Expanded *rows*
      deliberately do not: refilling one costs an interpreter start
- [x] **No Python change.** `list --json` already carried `tags` and the top-level
      id-to-name map, and ordering stays presentation — `referat list` is
      oldest-first on purpose and learns none of this
- [x] Keep the page portable while doing it: its only VS Code coupling is
      `acquireVsCodeApi()` and the `--vscode-*` theme variables, and the grouping
      added no VS Code API call. A property to preserve, not a plan for a second
      UI — *no web UI* is unchanged
- [x] **There are no projects yet.** `projects.json` does not exist, so every
      meeting is untagged and the grouped view is one section. Step 15's project
      CRUD and tag picker are built and have never been used; create the real
      projects and tag the existing meetings, which is also the only way to judge
      whether this grouping is the right one.
      **Done the same day**, and it found two bugs in the picker straight away -
      see "The tag picker, first time it was used" under Surfaced later.
      `aixvisxaccess` and `duckducktalk` exist and each carries a meeting
- [ ] Sections are ordered by most recent meeting, which is a guess. Watch whether
      alphabetical, or pinning, reads better once there are more than a handful

## 20. The command center

**Planned on 2026-09-02; phases 0 to 4 built the same day, phases 5 and 6 on
2026-09-03.** Phase 7 is the only one left and is gated on step 13. The primary
graphical surface becomes a **command center**: a desktop window owned by the
tray app and opened from the tray icon. The VS Code extension drops to
maintenance — bug fixes only, no new features — and is deleted once this reaches
parity with it. **That condition was met and acted on at step 23 on 2026-09-04**,
which built the last two things the sidebar could do and this could not.

- [x] **Drive a whole meeting through the new tray.** Done on 2026-09-02 within
      minutes of the first launch, from the USB button: `2026-09-02_1453`, two
      seconds, `idle -> recording -> stopped -> transcribing -> idle` with the
      sleep hold taken and released, both channels opened, large-v3 on CUDA, the
      loopback resampled, the gate clean, the audio released and the folder
      promoted out of staging. The hotkeys work under a Qt event loop, which was
      the one thing no isolated test could show
- [ ] **Still unexercised: pause and resume, the window's three buttons against a
      live recording, and a balloon naming unknown speakers.** The test meeting
      had no voice in it, so identification never ran and `App.notify` never
      carried anything but the plain line. Next real meeting settles all three
- [ ] **`2026-09-02_1453` is a two-second test meeting in the meetings folder**
      and is on the dashboard. Delete it with `referat delete` when it stops being
      useful as the first record of the Qt tray working
- [ ] **The hotkeys are registered before Qt exists and are never re-registered.**
      That is the ordering rule working, and it means a `keyboard` hook that dies
      leaves a tray that still draws an icon and still has buttons. Watch for it;
      the window's three buttons are now a second way in, which makes the failure
      survivable and also harder to notice

**Why the sidebar ran out of room.** It works, it is packaged, and it is a
column. The three things Referat is actually about — **meetings**, **projects**
and **people** — want pages: a person page unifying a voiceprint identity with
the projects and meetings that person appears in has nowhere to live in three
hundred pixels, and neither does a transcript beside its notes. The extension
also costs an interpreter start per refresh and only exists in a window that has
the repository open, which is not the window meetings are read in.

**The rule that changes, and the two that do not.** *No web UI, ever* is
relaxed: a **desktop window whose content is an embedded full-window web view**
is permitted. Still forbidden, and not negotiable: **any localhost server** —
Flask, FastAPI, anything binding a port — and **Electron**. The test is whether
something listens on a socket, not whether something renders HTML. Written into
`CLAUDE.md`'s Conventions in those words.

**Working technical direction: PySide6**, with **either native Qt widgets or
QtWebEngine** — the choice is deliberately not made here and is decided in phase
1 against criteria written down there. Qt owns the tray icon as well:
`QSystemTrayIcon` replaces `pystray`, so one process owns the icon, the hotkeys,
the recorder and the window, with one event loop and no IPC.

**The UI layer stays OS-portable; audio capture stays Windows-only.** That split
is a code-layer discipline and not a shipping target: no Win32 call and no
Windows-only Qt API inside `referat/ui/`, so the UI never becomes the reason a
port is impossible — which is not a promise that Referat runs anywhere but
Windows, because WASAPI, the sleep hold and the `keyboard` hooks say it does not.

**One implementation, restated because the process boundary moved.** The command
center **imports** — it is already a Python process inside this package, exactly
as the tray is, and `CLAUDE.md` has said since step 16 that the rule is one
implementation rather than one process boundary. It calls the same functions the
CLI calls: `cli.list_document`, `cli.project_document`, `cli.status_document`,
`cli.audio_state`, `meeting.format_duration`, `index.meeting_title`,
`voices.unknown_speakers`, `voices.name_complaint`, `voices.match`,
`projects.add_tags` / `remove_tags`, `label.apply_name`. The `--json` verbs stay
and grow anyway, because they are the shape both surfaces agree on, because the
extension still reads them while it lives, and because a document you can print
is a document you can test. **The window may not read `meta.json`, `voices.json`
or `projects.json` itself**, and the day it does is the day this stops being one
implementation.

### Phase 0 — is Qt allowed to run here at all

- [x] **This is a gate and not a formality.** Putting Qt in the tray process puts
      Qt on the recording path, and this repository's own rule — from the scipy
      paragraph in `CLAUDE.md` — is that nothing between a WAV and a transcript
      may depend on unsigned native code that is not already unavoidable. Smart
      App Control took `uv`, then `_ctypes.pyd` inside the venv's own CPython,
      then three of scipy's `.pyd` files on three separate attempts before the
      cloud reputation arrived. PySide6 ships a great many unsigned native DLLs,
      and a block on one of them means the tray does not start and **no meeting
      is recorded** — a total failure, not the degraded mode everything else here
      is built to have
- [x] **Audit the signatures rather than assuming them**, the way the venv
      migration was audited at step 11: install PySide6 into the venv, count the
      signed and unsigned files under `PySide6/` and `shiboken6/`, and record the
      numbers here. If `QtWebEngine` is a candidate, audit its bundled Chromium
      separately — it is the largest unsigned payload in the package by far and
      it is optional, which makes it the cheapest thing to give up
- [x] **Then decide, and write the decision down.** A clean audit means step 20
      proceeds as specified. A bad one means the recorded escape hatch:
      `pystray` keeps the tray, the window becomes a **child process** the tray
      spawns, and it shells out to the CLI the way the extension does — slower
      and duplicated at the process level, but a UI that cannot start is then a
      log line rather than a lost meeting. Reasoned about now so that it is a
      decision later and not a scramble
- [x] **Whatever the audit says, the window may never cost a recording.** The
      recorder, the hotkeys, the state machine and `status.json` come up first
      and independently; the window is opened after, and a window that fails to
      open is a log line and a tray that still records. Same rule diarization and
      the step 16 toast run under

### Phase 1 — a read-only browser

**The whole phase writes nothing.** It reads meetings that already exist, which
is what makes it a safe place to settle the toolkit question against real files
rather than against a prototype.

- [x] **Decide Qt widgets or QtWebEngine here**, and record why. The criteria:
      Markdown rendering fidelity for a transcript of several hundred entries and
      for `notes.md`'s headings, bullets and `[[Wikilinks]]`; whether
      QtWebEngine's bundled Chromium survives phase 0's audit; the package size
      each drags in; and whether `referat-vscode/media/sidebar.js` can be reused
      as-is, which is the one real argument for the web view — that page is
      already written, already builds every node with `textContent`, and its only
      VS Code coupling is `acquireVsCodeApi()` and the `--vscode-*` theme
      variables, a property step 19 kept deliberately.
      **Decided: Qt widgets.** `QTextBrowser` renders Markdown, holds anchors and
      answers a custom URL scheme, which is every criterion on this list; a
      several-hundred-entry transcript is one `setHtml` and scrolls fine.
      QtWebEngine needs `PySide6-Addons` — 168 MB of wheel against Essentials' 77
      — and spawns a sandboxed Chromium helper, a second process on the recording
      path. And the sidebar-reuse argument cuts the other way: `sidebar.js` draws
      a three-hundred-pixel column, which is the thing this step exists to escape,
      so reusing it would have reproduced the shape rather than saved the work
- [x] `referat/ui/` as a sub-package, against the flat-modules convention and
      recorded in `CLAUDE.md` as a deliberate exception rather than left to look
      like drift. A GUI is a dozen modules and flattening it would make the
      package unreadable
- [x] **The window opens from the tray icon** and closes to the tray rather than
      quitting. Closing the last window must not stop the recorder, which is the
      default Qt behaviour and has to be turned off explicitly
- [x] The meetings list, from `cli.list_document` — every field it already
      carries: duration, lifecycle status, audio state, title, unnamed speaker
      count, tags and the id-to-name map. Nothing re-derived, nothing inferred
      from which files exist
- [x] **A tabbed Markdown viewer** per meeting over `transcript.md` and
      `notes.md`, with a meeting that has no notes yet saying so rather than
      showing an empty tab
- [x] **Timestamp cross-links**: a `[HH:MM:SS]` reference in the notes navigates
      to that entry in the transcript tab. Note what the timestamp is and is not
      — it is **audio-elapsed with pauses excluded**, not wall clock, and the
      WAVs are normally deleted by then, so this is a scroll position and never a
      seek into audio
- [x] `referat transcript <id> --json` and the parser under it. **The parser
      goes beside `render_transcript` and `ENTRY_RE` in `transcribe.py`**, not
      into a new module, so the format is described in one file and the renderer
      and the parser cannot drift — the same reason `ENTRY_SEPARATOR` is one
      constant shared with `referat reflow`
- [x] `referat show <id> --json`: one meeting's whole record, which nothing
      exposes today. The `transcription` block, per-channel `speakers` with their
      `match` scores and runner-ups, the quality numbers, model and device. Built
      on `Meeting.to_json` and `ChannelTranscript.to_json`, which already exist
- [x] **Recording controls, calling the same methods the hotkeys call.** Record,
      pause and stop are buttons onto `App`'s existing handlers — no new CLI
      verb, no IPC, and above all no second path into the state machine. This is
      only possible because Qt owns the tray, and it is the one thing the
      extension could never do
- [x] Ambient state without polling: the window registers a `Machine` listener
      the way `status.write_status` does, so it learns transitions by callback
      rather than by watching a file. **The extension's status bar keeps its
      watcher and its 30-second poll** — that is a different process and still
      needs them

### Phase 1b — the viewer's own affordances

**Added on 2026-09-02, from reading real meetings in the window phase 1 built.**
Numbered `1b` for the reason steps 7b and 12b are: it belongs *inside* the build
order rather than after it, and it is phase 1's work rather than phase 2's.
Folding it into phase 1 would mean unchecking a built phase, which the standing
rule at the top of this file forbids and which would make the phase unreadable
besides.

**It writes nothing**, so it does not disturb the property that made phase 1 a
safe place to settle the toolkit: everything here is a scale, a selection or a
clipboard, and none of it touches `meta.json`, a tag or a name.

- [ ] **Text scale — `Ctrl+=`, `Ctrl+-`, `Ctrl+0` and `Ctrl+wheel`.**
      `QTextBrowser.zoomIn()` and `zoomOut()` already exist, so this is
      `QAction`s on `CommandCenter` beside the `refresh_action` that is already
      there, applied to `viewer.transcript` and `viewer.notes` **together**. One
      scale for the window and not one per tab: the two panes are read against
      each other, and a transcript at one size beside its notes at another is
      the arrangement nobody wants. **Check `Ctrl+wheel` before writing it**:
      `QTextEdit` is documented to zoom on it by itself when the view is
      read-only, so that half may already work and the shortcuts are the part
      that certainly does not — measure it rather than implementing over it
- [ ] **The scale survives a restart, through `QSettings`.** Called out because
      it is a *new store of state* outside `config.toml` and `meta.json`, which
      is the kind of thing that should never arrive unannounced. It is
      admissible because it is **presentation** state, which this repo already
      keeps outside its data files — the sidebar's folded sections live in
      `vscode.setState` for exactly this reason, and step 19 wrote down why.
      `QSettings` is Qt's own abstraction, so it holds `referat/ui/`'s
      portability rule; no `winreg` and no `ctypes`
- [ ] **A scale for the *whole window*, not only the two panes.** Asked for on
      2026-09-03, and it is a different feature from the two boxes above rather
      than a bigger version of them: `zoomIn` is a `QTextEdit` method, so it
      reaches the transcript and the notes and nothing else — the tabs, the
      meetings table, the queues, the day summary and every dialog keep the
      system font, which is the half that is too small on a laptop screen. The
      mechanism is the application font — `QApplication.setFont` with the point
      size scaled — or `QApplication.setStyleSheet` with a base `font-size`;
      **measure which one the tables and the tree headers actually follow**
      before writing either, since Qt's item views are the widgets most likely to
      keep a cached row height. If it lands, the pane zoom becomes a *relative*
      offset on top of it rather than a second absolute scale, or the two will
      fight. The same `QSettings` argument above applies to persisting it, and it
      is one store rather than two
- [ ] **Keyboard selection, set explicitly rather than left to a default.**
      `_browser()` sets only `setOpenLinks(False)` and
      `setOpenExternalLinks(False)`, so both panes keep Qt's default
      `TextBrowserInteraction`: mouse selection, the context menu's Copy and
      `Ctrl+C` all work, and `Shift+arrow` does not, because
      `TextSelectableByKeyboard` is not in that flag set. Add it. A behaviour
      this UI depends on should be a line in this file, not a property of
      whatever Qt happens to default to
- [x] **Copying the notes must yield Markdown source, and this repo already made
      that rule once.** The notes pane goes through `setMarkdown`, so `Ctrl+C`
      puts an HTML flavour on the clipboard and a paste into Word, Docs or
      Outlook arrives as styled rich text. That is **precisely** what
      `editor.copyWithSyntaxHighlighting: false` was set in the meetings folder's
      `.vscode/settings.json` to prevent — see *Getting text out of the meetings
      folder* below — so the window reintroduces the problem that setting exists
      to kill, on the surface that is now the primary one.
      **Done, and the complaint when it arrived was not the one predicted here.**
      `Ctrl+Shift+C` had copied the source as plain text since phase 1; what was
      wrong is that nothing said so, so right-click → Copy and `Ctrl+C` — the
      gestures people actually use — still went to Qt's. Both are routed at the
      real command now, which also sits on each pane's context menu and on a copy
      button in the corner of the tab bar
- [x] **And the honest difficulty, so it is not discovered as a surprise:**
      mapping a rendered selection back to source offsets is not free, because
      `setMarkdown` keeps no map from the document it built to the text it was
      given. So this is **two things and the cheap one is not the whole
      answer** — a *Copy notes source* action copying the whole raw `notes.md`
      as plain text, and plain-only `Ctrl+C` on a selection, which yields the
      rendered words without the styling but not the `##` and the `-`.
      **The second half of that answer is superseded**: a selection *is* the
      source now. `richtext.blocks` splits a source exactly where Qt splits it
      into `QTextBlock`s — a heading, a paragraph however many lines it wrapped
      over, one per list item — so the selected blocks are handed back as the
      Markdown they were written as, and the counts are checked before the map is
      trusted. Qt's own reconstruction is the fallback, and it is a *last* resort
      because **its Markdown writer drops `**bold**` and `*italic*` entirely**,
      measured on a round trip, and every action item's owner is bold
- [x] **The transcript pane is a different problem and does not get the same
      answer.** It is hand-built HTML from the parsed entries in
      `render_transcript_html`, not `setMarkdown`, so its rendered text is
      already close to its source. It needs plain-text copy and nothing else,
      and giving it a *copy source* action would mean handing somebody
      `transcript.md` — a file they can already open.
      **Wrong, and it was wrong about *close to*.** The pane's plain text loses
      the `## Meeting ...` header to bold text and turns the blank line between
      entries — the one thing keeping a Markdown reader from running the whole
      meeting into one paragraph — into a single newline. It keeps its source
      too, through a new `markdown` field on `transcript_document`, which is the
      file that function had already read and was throwing away. *A file they can
      already open* is also not an argument against copying it: opening it is not
      what somebody pasting a passage into Claude is doing
- [x] **The second flavour, which this box never imagined: formatted text.**
      Markdown source pastes into Claude; it pastes into a Google Doc as literal
      `##` and `-`. So *Copy as formatted text* (`Ctrl+Alt+C`) writes clean HTML
      from `referat/ui/richtext.py` with the Markdown beside it — **two commands
      rather than one clipboard carrying both**, since a rich target prefers HTML
      whenever it is offered and one copy with both flavours would decide for the
      paste. That module is also the manual half of step 13: it answers the same
      questions `digest.py` is specified to answer, so a note pasted into a doc
      and a note pushed into one read the same

### Phase 2 — tagging

- [x] **The untagged inbox is the opening queue.** New meetings land there; it is
      the computed state of an empty `tags` list and is never itself a project.
      Built as an **"Untagged only" toggle and a search box** beside the meetings
      list, matching the sidebar's, with an untagged row's Projects cell reading
      *Tag this…* instead of being blank. The toggle defaults **off**: until
      phase 6 makes the dashboard the opening screen this window is also the
      browser, and opening it with every tagged meeting hidden is worse than the
      queue being one click away
- [x] **Grouping the tree by project was considered and deferred**, deliberately.
      It is step 19's shape and it would work here, but it turns a flat tree into
      a two-level model where a two-tag meeting exists twice, headers must be
      skipped on selection, and expansion state has to survive a `refresh()` that
      fires on every transition — a second hard problem in the phase that
      performs this UI's first write. It also makes the meetings tree the
      de-facto project browser and so decides project ordering twice, before
      phase 4 has designed it once; and the ordering it would copy is the guess
      flagged under step 19. The filter is a predicate over the same document, so
      grouping later replaces the row builder and keeps it
- [x] The tag picker. **Two sets, not one** — what the meeting carries, which is
      the baseline and never moves, and what is ticked, which grows when a project
      is created inside the picker. That distinction is not a detail; it is the
      bug step 15's picker shipped with, and it is written here so it is not
      rediscovered. Built, and it holds.
      **It drives `cli.apply_tags`, not `projects.add_tags` as this box first
      said.** `add_tags` is three lines appending to a list; what makes tagging
      correct is the unreadable-file guard, the unknown-id refusal, the resolve
      and the single `meta.json` write around it, and a window calling `add_tags`
      would have had to grow all four. `apply_tags` takes both directions in one
      call — a picker is a diff — and answers with an `Outcome` whose message is
      the unprefixed sentence its rule's owner wrote
- [x] Creating a project from inside the picker, through `ProjectsDB.add` so the
      id is `slugify` plus its collision suffix and no caller ever guesses one.
      In process there is no JSON in the way at all — `cli.create_project` hands
      back the `Project` object, so `project add --json` exists for the extension
      and the window never needs it. The label stored is the project's **own**
      name and not the typed string, because Python collapses whitespace
- [x] **Create only.** Rename and delete stay in phase 4, where the projects page
      is. A picker that could delete a project would be deciding something about
      every other meeting from inside one meeting's dialog
- [x] **The UI nudges project tagging before speaker labeling.** This is a flow
      rule and the reason phase 2 comes before phase 3: the restricted gallery
      phase 3 offers only exists once the meeting has a project, so an untagged
      meeting's row leads with *Tag this* and reaches labeling past it. A nudge
      and never a gate — labeling an untagged meeting stays possible, with the
      full gallery, because refusing would make a missing tag cost a name
- [x] **Assignment stays strictly manual.** Nothing is inferred from a
      transcript, nothing is tagged at the end of the pipeline, and no default is
      remembered and moved along with the last assignment

### Phase 3 — labeling

**Built on 2026-09-02.** Two new modules — `referat/people.py` for the derivation
and `referat/ui/speakers.py` for the dialog — plus `label.label_document` and
`label.name_speaker`, which are the document and the operation the surfaces share.

- [x] Snippet playback, the known-name chips and the name field, driving
      `label.apply_name` — which writes the database, `meta.json`, the transcript
      labels and the snippet cleanup in that order, and which the UI reimplements
      no part of. `voices.name_complaint` still decides what a name may be.
      **Built, and it drives `label.name_speaker` rather than `apply_name`, which
      this box named because the layer did not exist yet.** `apply_name` is the
      primitive and the pipeline calls it too; what makes *naming* correct is the
      four things `run_apply` had wrapped around it where only the CLI could
      reach them — the reserved-name rule, the meeting lookup, the refusal to
      rename somebody who already has a name, and regenerating the dashboard.
      A dialog reaching past those would have been the second implementation of
      all four. Exactly the correction phase 2 made about `apply_tags` against
      `projects.add_tags`, one module along, and worth noticing that the same box
      was written the same wrong way twice
- [x] **Project-scoped identification, and it is the gallery only.** When a
      meeting carries a project, the names offered are the people associated with
      that project, with everybody else behind a *show all* — narrower and
      better-ordered choices for a human, and nothing more. Every name written is
      still somebody's decision, which is why a chip **fills the field rather than
      applying itself**: a name teaches every meeting after this one a voice, and
      the click that starts that should not also commit it
- [x] **The automatic match is unchanged and stays global.** `voices.identify`
      runs during the pipeline, before anything has been tagged, so there is
      nothing to scope on; the threshold-and-margin rule in `voices.match` is
      untouched, including the single-name case where the margin is asked of the
      score itself. Nothing in `people.py` is reachable from the pipeline at all
- [x] **Project membership never touches clustering.** Diarization is upstream of
      all of this and knows nothing about projects. Narrowing a gallery changes
      what a person is offered; changing the clustering would change what the
      machine decided, and that line does not move
- [x] `label <id> --json` gains a **`gallery`** field: the names this meeting's
      tags associate with, and the rest as a second list. The scoping is computed
      in Python and the UI renders it, because working out which people belong to
      a project in the UI would be a second implementation of exactly the thing
      this whole arrangement exists to have one of.
      **It carries a third key, `tags`**, which was not planned and earns its
      place: it lets a surface say *this meeting has no project, so every name is
      offered* as a fact it was **told**, rather than inferring it from `scoped`
      being short — the same shape of mistake as inferring a lifecycle from which
      files exist
- [x] The association itself has to be derived, because nothing indexes it: a
      `Voiceprint` records the meeting it was filed from, that meeting has
      `tags`, and a person appears in a meeting whose `speaker_names` names them.
      Both directions are needed and neither is stored.
      **Measured rather than argued, and the measurement is the useful part**: on
      the six meetings here, Niklas has voiceprints filed from **two** meetings
      and appears in **all six**, so five of six projects associate the owner
      through the appearances direction alone. The other direction covers the
      case a `rerun` makes — renumbered clusters that were not re-matched, whose
      `speaker_names` loses the name while the print filed from that meeting keeps
      its provenance

#### What building it settled

- [x] **The owner is scoped into every meeting**, whatever it carries. Not in the
      plan; found by reading the first gallery this produced, where the owner sat
      in `rest` for five of six projects for the reason the box above measures.
      They pressed the button, so they were in the room — and leaving them out put
      the one name a surface can honestly lead with behind a *show all*, on
      exactly the meetings where an unnamed microphone cluster is most likely to
      be them. It goes through the same intersection with the database as every
      other name, so an `[speakers].owner_name` with no voiceprint behind it yet
      is still not offered
- [x] **Playback is refused while a meeting is recording**, `paused` included.
      The one thing in this phase that is not a UI decision: this is the tray's
      process, so snippets play out of the speakers WASAPI is looping back into
      `system.wav`, and a person's earlier speech would be captured into the
      meeting being recorded, transcribed, diarized and rendered as if the far end
      had said it. The transcript is evidence of what was said, and that is the
      one way a UI could quietly write something into one. Paused too, because a
      paused meeting is still being recorded — which is why the recorder's state
      machine has a `paused` and the meeting's lifecycle does not
- [x] **The echo refusal now says what is actually wrong.** Naming an echo cluster
      through `--speaker` used to come back *"is not an unnamed speaker in …"*,
      which is what `unknown_speakers` filtering it out reduces the question to.
      That flag is typed by somebody who can see the cluster in `referat show`,
      and the real reason is the one that costs a database entry if it is ignored
- [x] **`label.concatenate` is shared by both playbacks.** `play` blocks for the
      prompt and `play_async` returns for the window, and both decode through one
      function rather than two loops. A clip that will not decode is skipped
      rather than fatal: one unreadable snippet out of three costs a third of the
      evidence, not all of it
- [ ] **Still unexercised: playing a real snippet from the window.** Every meeting
      on this machine is fully named, so the dialog was driven against a synthetic
      meetings folder and against the six real ones read-only. What has not
      happened is a person hearing a voice come out of this dialog and typing a
      name they were not already sure of. The next meeting with an unknown speaker
      in it settles that, and it is also the first test of whether the scoped
      gallery is the right list or merely a shorter one
- [x] **Two graphical surfaces now render the same `gallery`, and only one uses **Dropped on 2026-09-06: the extension was deleted at step 23.**
      it.** The extension is in maintenance, so it keeps showing every known name;
      the field is additive and nothing there broke. Worth watching only in the
      sense every `--json` change is until `referat-vscode/` goes

### Phase 4 — projects

**Amended on 2026-09-02 to carry the glossary, and the scope line below is the
one that moves.** *A tag, a name, and an optional one-line description* was
written before anybody asked where hotwords are managed; the answer is here,
because a `glossary` is already a `Project` field and editing it anywhere else
would be a second place to say what `projects.json` says. Read the first box as
widened by the boxes at the end of this phase, not as replaced — everything it says about
lightness still holds, and a glossary is a list of words rather than a feature.

**Built on 2026-09-02.** One new module, `referat/ui/projects.py`, four new
guarded functions in `cli.py`, two new CLI verbs, two setters on `ProjectsDB`,
and the window's central widget became a `QTabWidget`.

- [x] **Deliberately lightweight: a tag, a name, and an optional one-line
      description.** The description already exists as a reserved field in
      `projects.py` and is written by nothing; this is what starts writing it
- [x] Create, rename and delete through `ProjectsDB`. A rename changes the
      display name alone — the id is a slug fixed at creation, which is the whole
      reason `meta.json` stores ids. **Built, and through `cli.rename_project`
      and `cli.remove_project` rather than through `ProjectsDB` itself**, which
      this box named because the layer did not exist yet — the third time the
      same correction has been made, after `apply_tags` in phase 2 and
      `name_speaker` in phase 3, and the first time it was made *before* the
      second surface existed rather than after. `rename` and `rm` had loaded,
      checked, mutated and saved inside `run_project`, where only the CLI could
      reach any of it
- [x] **Deleting orphans its tags, visibly, and cascade-deletes nothing.** The
      orphan chips get a section of their own, as they do in the sidebar, because
      a tag vanishing quietly off three meetings is how you lose track of what a
      meeting was about. Built as a section in the project list, shown and
      **unselectable**: there is no project there to edit, and an orphan comes off
      a *meeting*. The delete modal names the count before the command runs,
      which is the one place this page says something Python also says — the same
      exception the sidebar's Delete modal is, and for the same reason: there is
      no outcome to quote yet
- [ ] **Phase 7's, now that step 13 exists.** The reason this box gave for being
      unbuilt — the CLI owns every mutation and `project link-doc` does not
      exist — stopped being true on 2026-09-04. What it needs is three buttons
      driving `cli.link_doc`, `cli.unlink_doc` and `cli.sync_project`, with the
      sync on a thread and `cli.doc_candidates` behind the picker. Note that the
      window can never *authenticate*: consent needs a terminal by construction,
      so its failure mode is a sentence naming one command.
      Original note follows.
- [ ] **One Google Doc per project in the command center's model**, linked to an
      existing doc or created new. Note the divergence and do not resolve it
      here: step 13 specifies **zero or more** docs per project and `DocRef` is
      already a list. The UI showing one is a presentation choice on a list of
      one, never a schema change — see the open questions.
      **Left unbuilt, and the reason is the rule rather than the effort**: the
      CLI owns every mutation and `project link-doc` does not exist until step
      13. The page renders the references it finds — a list, not a doc, so
      nothing here narrows the schema — and says where linking will come from
- [x] **The glossary is edited here, beside the description**, and that makes
      this page the hotword surface. It is already a `Project` field, written
      today by nothing but a hand edit to `projects.json`, and it is the one key
      in that file something outside step 13 depends on: `hotwords.merge` folds
      every glossary into the single list handed to faster-whisper, which acts
      **before** any meeting has been tagged. So editing it here is hotword
      management arriving where the terms already live, rather than a new screen
      about a config file
- [x] **`ProjectsDB` needs a setter and the CLI needs a verb, and neither is an
      extra.** `add`, `rename` and `remove` exist; nothing sets `glossary` or
      `description`. The CLI owns every mutation, so the window may not reach
      past it into `ProjectsDB` — a `referat project glossary <id>` verb, with
      the description alongside it, is a **prerequisite** of the box above.
      Cross-reference the deferred `referat glossary prune --dry-run` idea under
      *Surfaced later*: it acts on the same field and should not be designed
      twice.
      **Built as `project describe <id> [text] [--clear]` and `project glossary
      <id> [--add ...] [--remove ...] [--clear]`.** `--add` and `--remove` are
      ergonomics over a **whole-list replacement**: both read the current list and
      hand the whole of it to `set_glossary`, which is the one call that reaches
      the file. Deliberately the opposite of `apply_tags`, which is a diff and
      must be — a picker renders a *subset* of the projects, so a replacement
      there could drop a tag it never drew, while a glossary is edited as the
      whole list. Removal is case-insensitive, matching the deduplication that put
      the terms there
- [x] **`[transcription].hotword_extras` stays hand-edited, and is shown
      read-only.** This is the deliberate half rather than an omission.
      `config.py` promises never to write `config.toml` back, and a UI that
      edited one key of it would either break that promise or make `config.toml`
      a second place to say what `projects.json` says. Extras are by definition
      the terms belonging to no project and no person, which is a short and
      rarely-touched list — the cost of a text editor is low and the cost of the
      promise is not
- [x] **A read-only merged-list panel, from `hotwords.merge`**: every term with
      the source it came from, and what the 223-token cap dropped. That last
      part is the point. A cap nobody can see is how this turns into a bug
      report about one specific name that is never heard right — step 12b said
      so and answered it with a log line and a CLI table, and this page is now
      where somebody will be *adding* the terms that push the list over
- [x] **`hotwords_document(config)` splits out of `run_hotwords`, and the verb
      gains `--json`.** Step 12b deliberately gave it none, on the recorded
      reason that nothing reads this document; that stops being true here. Same
      shape as `list_document` and `transcript_document`, which the window
      already imports — one builder, a human table and a JSON form over it, and
      a document you can print is a document you can test
- [x] None of this breaches *the window may not read `meta.json`, `voices.json`
      or `projects.json` itself*. `hotwords.merge` and `ProjectsDB` are this
      package's own functions, exactly as `cli.list_document` is; the rule is
      about a second implementation, not about which files are eventually opened

#### What building it settled

- [x] **The window is tabs, and the recorder is not one of them.** `QTabWidget`
      with Meetings and Projects, People and the dashboard being phases 5 and 6 —
      but record, pause and stop stay *above* the tabs, because the recorder is
      not one of the three entities and a Stop button hidden behind a tab is a
      recording somebody cannot stop from here. `Tags…` and `Speakers…` went the
      other way and moved down into the meetings page: they act on a selected
      meeting, and on a screen about projects they would have been two dead
      buttons
- [x] **Only the page in front is refreshed.** Every page costs a scan of both
      meeting roots and `refresh` runs on every transition; a hidden one is
      brought up to date when it is switched to, which is the moment before
      anybody could read a stale figure off it. The projects page keeps whatever
      is half-typed into its form across a refresh, because reselecting the row
      that is already selected fires no change — which then made the Save button's
      state something that has to be recomputed explicitly, or it stayed lit after
      a save, offering to write again what had just been written
- [x] **An unreadable `projects.json` was saying the wrong thing to the wrong
      caller.** One sentence, three endings, and the ending is the caller's:
      `WOULD_OVERWRITE`, `CANNOT_TAG`, and a new `CANNOT_LOOK_UP` for a read.
      `referat project glossary` reads the file to print a list, and being told
      *nothing can be tagged until it is* is an answer to a question nobody asked
- [x] **`project_document` grew a `complaint`, and `referat project list` prints
      it.** An unreadable file loads as *no projects*, which is the rule that
      keeps a broken one from costing a transcript — and it means an empty list
      has two causes that draw the same picture. The listing answered both with
      *No projects yet. Create one with…*; the page must not guess either, since
      it offers to create a project into a file whose contents it cannot see, so
      a complaint disables every control that would write
- [x] **Cleaning a glossary belongs on the way into the file.**
      `projects.clean_terms` collapses whitespace, drops blanks and deduplicates
      case-insensitively with the first spelling winning — the same reduction
      `hotwords.collect` applies on the way into Whisper's prompt. Not a second
      implementation of it: `collect` deduplicates *across* three sources and
      cannot stop, and this is the file being written in the shape it will be read
      in, so `referat project glossary` prints what `referat hotwords` will merge
- [x] **Only the dirty field is written, and the form is refilled from the file
      afterwards.** Two calls rather than one composite, because they are two
      operations with two messages and each rewrites `projects.json` whole and
      atomically — so the worst an interruption between them does is leave the
      description saved and the glossary not, which the refresh then shows
      honestly. Refilling is the tag picker's rule one module along: a box still
      showing the three lines that became two would be this page holding an
      opinion about somebody else's field. A refusal is the exception, where the
      typed text stays so it can be corrected rather than retyped
- [ ] **Still unexercised: the page against a project that actually needs a
      glossary.** Every glossary on this machine is empty, so the panel was driven
      against synthetic terms and the six real projects read-only. What has not
      happened is somebody adding the name their transcripts keep mangling and a
      later meeting coming out with it spelled right — which is the only test of
      whether the merged panel is where hotword management belongs
- [ ] **The cap has never been reached in anger.** Nine names and no glossaries
      is 49 of 223 tokens; the drop list was seen only by pushing 40 synthetic
      terms through it. Watch what it drops the first time a real glossary
      approaches the budget, since the priority order — extras, names, glossaries —
      means a project's own terms are what goes first

### Phase 5 — people

**Built on 2026-09-03.** One new module, `referat/ui/people.py`, one new CLI verb,
one new guarded function in `label.py`, a `Person` record and a second reading of
the join in `people.py`, and a second kind of link in the viewer.

- [x] **A person page unifying the voiceprint identity with a lightweight tag**:
      which projects they appear in, which meetings, how many voiceprints are
      filed under them and where each came from
- [x] **Clicking a person's name in any document opens that page** — in a
      transcript's label column, in a note's `[[Wikilink]]`, in a speaker chip.
      That is the whole argument for a page rather than a row.
      **Two of the three, and the chip is a deliberate refusal** — see the box
      under *What building it settled*. A `referat-person:` scheme carries the
      name, percent-encoded because a name is whatever somebody typed
- [x] `referat people [--json]`: one entry per known name with its print count,
      the meetings its prints were filed from, the meetings it appears in and the
      projects those meetings carry. **Names, counts and ids — never an
      embedding, and never a path into `.voices/`.** The database is biometric
      personal data about people who never asked to be in it, and a listing verb
      is the first place that is easy to forget.
      **Checked by grepping the `--json` for `embedding`, `.voices` and the
      configured voices path: zero of each.** That is the verification this phase
      has, and it is worth re-running whenever the document grows a field
- [x] Appearances need a scan of `speaker_names` across `Config.meeting_roots()`,
      because `voices.json` records only where a print was *filed* and somebody
      recognised in a meeting files nothing new
- [x] **The privacy rules are unchanged and restated here because a page makes
      them easier to break.** The database never leaves the machine, is not
      synced, is not backed up, stays denied to the `/cleanup` pass, and `referat
      label --forget <name>` deletes a person outright — embeddings gone, labels
      reverted everywhere. Deleting a meeting still does not delete a person.
      Said on the page itself, under the lists, rather than only here
- [x] A **Forget this person** action, behind a modal, driving `label.forget`. It
      is the one destructive thing on this page and it is the point of the page
      being honest about what is stored.
      **Driving `label.forget_person`**, not `forget` — see below

#### What building it settled

- [x] **`forget` was a primitive with the operation wrapped around it inside
      `run_forget`**, exactly as `rename`/`rm` were before phase 4 and
      `apply_name` was before phase 3. Split into `label.forget_person`, which
      holds the refusal of a name nothing is filed under and the
      `index.write_index` that reverting a label makes necessary; `run_forget` is
      the confirmation and one line of dispatch. **The fourth time this
      correction has been made, and the second time before the second surface
      existed.** The confirmation stayed *outside* deliberately: `_confirm` reads
      `input()` and answers no on EOF, which is a question asked of a terminal,
      and a window asks the same one with a modal
- [x] **`project_people` and the new per-person read were two walks of the same
      two files.** `people.directory` is the one walk and `project_people` is
      derived from it. Same contract, checked against `gallery` on the six real
      meetings
- [x] **A name with no voiceprint behind it is a real category and nothing else
      shows it.** A transcript still calling somebody `Anna` while the database
      holds nothing under that name is drift — a `rerun` that renumbered past
      them, or a hand edit — and no other surface compares those two files. It
      gets its own section, as the orphaned tag ids do on the projects page, with
      `Forget` disabled because there is nothing filed to delete. Forced in a
      scratch config to check it renders; **empty on this machine**, which is the
      right answer rather than a missing feature
- [x] **A wikilink is linked whatever it says.** Nothing in a note distinguishes
      `[[Gaby]]` from `[[DuckDuckTalk]]`, and the alternatives were to link none
      of them or to hold a known-name list inside the viewer that goes stale
      between refreshes and costs a second scan of both roots per keystroke. A
      name nobody is filed under opens the page and is told so, which is
      information about the note. Which *transcript labels* are names is the
      opposite case and is decided in Python, by `voices.name_complaint`, and
      carried as `transcript_document`'s new `people` field
- [x] **A speaker chip does not navigate, and that is the exception to the box
      above.** A chip fills the name field rather than applying itself — two
      keystrokes on purpose, because a name teaches every later meeting a voice —
      and the dialog is modal over the page a link would go to. So a chip carries
      a **tooltip** instead: prints, meetings, projects, which is the question
      somebody has while naming. Folded in at `open_for` rather than by widening
      `label_document`, since a tooltip is presentation and `referat label --json`
      has no use for it, and a failure to read it costs the tooltip and never the
      dialog
- [x] **Arriving by a link is a row change the row handler never sees.** Both
      `ProjectsPage.select_project` and the people page's own reselect had to ask
      the unsaved-changes question and fill the form explicitly, because
      reselecting a row that is already current fires no signal — which is the
      very property that keeps a half-typed glossary across a refresh. Same fact,
      two opposite consequences, in two files
- [x] **Every navigation clears what would hide its target.** `open_meeting`
      turns off the untagged filter and empties the search box, and `select`
      clears the people search. A link landing behind a filter somebody set ten
      minutes ago looks exactly like a link that did nothing — and on this page it
      would look like *nobody is filed under that name*, which is the one message
      here that must never be shown wrongly
- [ ] **Still unexercised: the page against a meeting with an unnamed speaker in
      it.** No meeting on this machine has one, so the chip tooltips were built
      from the real people document but never seen on screen, and neither was a
      `Forget` on a person somebody actually wants gone. `forget_person` itself
      was driven end to end in a scratch config — 245 lines reverted from a name
      to `SPEAKER_02`, the embedding gone, `speaker_names` cleared, the dashboard
      regenerated — and the real `.voices/` was never the subject of a test
- [ ] **The page has never met a person with more than two voiceprints**, so the
      Voiceprints table has never needed to scroll and the *filed from* / *appears
      in* gap has never been larger than four. Watch it as the database grows;
      the gap is the thing the page exists to explain and a big one is where the
      sentence under the heading gets tested

### Feedback from the first real week of using the window

**Raised on 2026-09-03, after the command center had been lived in rather than
demonstrated.** Everything here is a complaint about the window as a *primary*
UI, which is what phases 1-5 had just made it.

- [x] **The tray died after every transcription.** Reported as a crash and it
      was: `0xc0000374`, STATUS_HEAP_CORRUPTION in `ntdll`, seconds after a
      transcript finished — and it had already happened twice on 2026-09-02
      without being noticed, because a dead tray looks like a tray somebody
      closed. `App.notify` reached straight into Qt from the `transcribe` daemon
      thread and `Shell.notify` ran `tray.showMessage` **and a full
      `window.refresh()`** there, rebuilding every row of the meetings tree off
      the GUI thread. `Bridge` existed for exactly this crossing and carried only
      transitions. **No Python traceback, because there was no Python
      exception** — the log shows a clean successful transcription and then
      stops, and that is the signature to recognise next time
- [x] **`gpu.release`'s `gc.collect()` was the same hazard**, from the other
      side: a collect destroys what it reaps on the calling thread, PySide6
      widgets included. Guarded to the main thread. It was not what was firing,
      and it would have been eventually
- [x] **`build_info` was sampling `referat/*.py` and missing `referat/ui/`
      entirely** — a third of the package and the part that changes most. A tray
      running an hour-old window reported itself current, and during the crash
      hunt that stamp very nearly exonerated the code that was crashing. `rglob`
- [x] **The meeting list needed horizontal scrolling.** Six columns never fitted
      the three-sevenths of a window a horizontal splitter gave them, and no
      resize policy fixes that. The meetings page is a **vertical** splitter now:
      the list full width on top, the viewer beneath. Title takes the slack, the
      rest size to their contents
- [x] **Zoom without a mouse.** `Ctrl++`, `Ctrl+=`, `Ctrl+-` and `Ctrl+0`, on
      **both** panes together, because they are two tabs of one document. Qt's
      Ctrl+wheel already worked and needed a mouse and moved one pane
- [x] **Notes is the first tab.** The transcript is a source; the notes are what
      somebody reads. Leading with the transcript opened every meeting on several
      hundred utterances
- [x] **A clean copy out of a Markdown pane.** `Ctrl+Shift+C` puts the **source**
      on the clipboard — the original `notes.md`, not the pane's rewritten
      `referat:` links — as **plain text only**. Qt offers an HTML flavour
      alongside and Word, Google Docs and Outlook all prefer it, which is exactly
      the failure the meetings folder's `editor.copyWithSyntaxHighlighting:
      false` answers for VS Code
- [x] **Nobody could find that copy, and it only had one flavour.** Reported the
      day after it shipped: right-click → Copy gives raw text, and there was no
      way to get *formatted* text into a Google Doc at all. Two named commands
      now — **Copy as Markdown** (`Ctrl+Shift+C`, plain text, for Claude) and
      **Copy as formatted text** (`Ctrl+Alt+C`, clean HTML from the new
      `referat/ui/richtext.py`, for Docs) — on each pane's context menu, on a copy
      button in the corner of the viewer's tab bar, and with `Ctrl+C` in a pane
      routed at the first. Bound in **one** place, the window, which dispatches
      onto the page in front: two claims on one shortcut is a shortcut Qt fires
      neither half of. A selection is sliced out of the source through
      `richtext.blocks`; Qt's reconstruction is the fallback, and a last resort,
      because its Markdown writer drops bold and italic outright
- [x] **No way to generate notes from the window**, which the sidebar had. Now a
      button, on a thread, through the new `cli.write_notes` over
      `referat/notes.py` and `cli.set_notes_written`. Finding `claude` from
      Python is its own problem — the extension asks VS Code and Python cannot,
      so it reads `~/.vscode/extensions` and honours the `.obsolete` file VS Code
      writes there, which on this machine listed 2.1.252 as obsolete beside a
      live 2.1.258
- [x] **No way to delete a meeting from the window**, which the sidebar also had.
      Now a button behind `cli.delete_warning`'s text, through the new
      `cli.delete_meeting`. That makes seven and eight guarded functions split out
      of a `run_*`, and the shape is settled: **a `run_*` that holds a rule is a
      `run_*` a second surface cannot use**
- [x] **Nothing showed what the slow things were doing.** `referat/progress.py`
      is the seam — the same shape as `App.notify`, toolkit-free, never required,
      never able to raise into the pipeline — and the window renders it as a
      permanent activity strip in the status bar. The only true fraction is
      faster-whisper's `segment.end`; everything else is a phase and `None`,
      because `None` is the honest absence of a claim and zero percent is a claim
- [x] **A transcribing meeting drew two empty panes.** It now says which of the
      two waits it is in — a recording has not finished happening, a
      transcription has finished happening and is being read — and why there is
      nothing yet: `transcript.md` is written whole at the very end, so an
      interrupted run leaves the previous one intact
- [x] **Confirmed: recording back to back with a transcription running.** The job
      is a daemon thread, `Record` is enabled in `idle` *and* `transcribing`, and
      `transcribe._RUN_LOCK` serialises the jobs so two large-v3 models never
      share the card. Answered rather than built
- [x] **`/cleanup` said "starting claude" for the whole two minutes.** Plain `-p`
      prints one blob when the pass is over — measured in the log: the 10:53:30
      run said nothing until 10:58:41 and then said everything at once. Now
      `--output-format stream-json --verbose`, and `notes._phase` turns each
      event into a sentence: *reading transcript.md*, *writing notes.md*. The
      verdict comes from the final `result` event too, which carries `is_error`,
      rather than from the exit code alone
- [x] **Queue several notes runs, and a way to launch them all.** *Notes for
      all…* queues every promoted meeting with a transcript and no notes, oldest
      first. **One worker and a FIFO**, not a thread each: every pass is a real
      subprocess against one rate limit writing into one folder, and sequential
      is also what makes a queue legible. Each id is announced to `progress` as
      *queued* the moment it is accepted, so the whole backlog shows rather than
      only the one in flight
- [x] **An Activity tab**, as suggested, and it is two kinds of truth: the queue
      from `progress` on top, and the **real rotating log** beneath rather than a
      parallel history kept in memory, which would be a worse copy of a file
      Referat already writes everything worth knowing into. The one page with a
      timer — a log grows with no event this process can see — and it runs only
      while that page is in front
- [x] **The Whisper model is not re-downloaded.** Measured: 2.9 GB cached on
      disk, ~4 s to load. The `httpx` line is one metadata call asking Hugging
      Face for the model's current revision, and it reads exactly like a download
      starting. `logging_setup.CHATTY` raises httpx and five other libraries to
      WARNING, and the Activity tab's *Referat only* filter hides them too
- [x] **Chips wrapped instead of filling the width.** `referat/ui/flow.py` is the
      classic Qt flow layout, which Qt does not ship: `QHBoxLayout` makes itself
      as wide as its contents, so nine names pushed the detail pane past the
      window and the gallery only grows. They are styled as chips as well —
      rounded, palette-coloured, sized to their text — because a row of push
      buttons reads as a row of commands and this is a gallery to pick from
- [x] **The speaker list on the left is fixed at 150 px.** It holds `SPEAKER_NN`
      and nothing else; every pixel past that was taken from the panel that has
      something to say
- [x] **Mark a speaker as noise and delete their lines.** Deferred to **step
      20b** above, because it removes entries from a transcript and inherits four
      conditions from `debleed`. **Built on 2026-09-04**
- [x] **Full names as well as short names, renaming a person with the change
      propagating into transcripts and notes, and an email on the record.**
      All three deferred to **step 20c** above, which they turned into one step
      — **built on 2026-09-04**:
      they are the same change, a person ceasing to be a string and becoming a
      record. A schema change to the voices database, where two people filed
      under one name is the worst failure this system has — and where renaming is
      an edit if the identity is an id and a migration if it is not
- [x] **"The interface just is not rich enough. I want to see tags and"** — the **Declined on 2026-09-06: not needed.**
      sentence was cut off and the rest has not been said yet. Do not guess at it;
      ask. Tags are on the meetings list and editable through the picker, so
      whatever is missing is something else
- [x] **Still unexercised: the notes button against a real meeting.**
      **Exercised on 2026-09-04**, through the activity strip, and worked.
      `referat/notes.py` resolves the binary correctly and the CLI verb parses,
      but no `/cleanup` has been driven through this path — the test was stopped
      because a meeting was recording, and then a 55-minute transcription had the
      machine. Run it once before trusting the button
- [x] **The activity strip has never been watched through a real transcription.**
      Every phase was driven against a fake model. The fractions are right in a
      unit test; what they look like over 55 minutes of audio is not known.
      **Watched on 2026-09-04** through a transcription and a notes pass; fine

### The visual pass: icons everywhere

**Raised on 2026-09-03, on the dashboard the same day it was built: "a dashboard
should also be somewhat visual… are there ways we can add icons to different
parts of the interface and dashboard? Many buttons like Record, Pause, and Stop
have obvious icon candidates."** Built the same day.

- [x] **Drawn in `QPainter`, never shipped as assets.** A dependency decision
      before an aesthetic one: an icon font or an SVG set is a package in
      `pyproject.toml`, the base install is on the recording path, and every file
      on that path is something Smart App Control can one day refuse — the whole
      argument the PySide6 audit made. Eleven glyphs are two hundred lines that
      cannot be blocked, cannot be missing at runtime, and scale to any DPI
- [x] **One style: solid, one colour, no outlines and no two-tone.** A set that
      mixes filled and stroked glyphs reads as icons borrowed from two places,
      which at sixteen pixels is the only thing anybody notices. `pulse` is the
      one exception it has to be — a trace has no inside
- [x] **Colour means a state or it means nothing.** Record, Pause and Stop carry
      the *recorder's own* colours out of `icons.COLORS`, so red means recording
      in the window and in the notification area without either being taught the
      other's vocabulary; the three Delete buttons carry one red trash. Every
      other glyph is drawn in the palette's `windowText`, which is what makes a
      dark theme get light icons — **checked by rendering the whole window under
      a synthetic dark palette** rather than by reasoning about it
- [x] **A lifecycle dot on every meeting row**, on the meetings list and on both
      of the dashboard's lists, in one colour per `status` — the two in-flight
      states borrowing the recorder's colours, the two that want somebody warm,
      and the three finished ones a green that *deepens* through `transcribed`,
      `notes_written` and `synced`, which is the only colour in this UI carrying
      an order
- [x] **A run that lost a channel hollows the dot into a ring**, which is the
      tray icon's existing rule applied one layer out rather than a second visual
      language for the same fact. Somebody who has learned what a hollow icon
      means has learned it for both places at once
- [x] **A queue heading carries the glyph of the button that drains it** — a tag
      for `Tags…`, a person for `Speakers…`, a page for `Generate notes…`. A row
      on the dashboard goes to the Meetings tab, where the thing to press next
      wears the picture you came from
- [x] **A repeated identical icon down a list is the one thing carrying no
      information**, so queue rows get the *meeting's* dot rather than the
      queue's own glyph. The exception is the people page, where *Appears in* and
      *Projects* are two lists of bare ids one under the other, and a meetings
      glyph against a tag glyph is the only thing distinguishing them without
      reading the heading above
- [ ] **The tray's context menu was left alone**, deliberately and not from
      neglect: on Windows 11 that menu is drawn dark while `QApplication.palette()`
      can report light, so palette-tinted glyphs there could come out black on
      black. It needs the menu's own palette, or fixed mid-grey icons. Worth
      doing, worth measuring first
- [ ] **Never seen on a high-DPI display or at a scale other than 100%.** They
      are drawn at 64 px and asked for at 14-16, so this should be exactly the
      case vector drawing handles — which is a prediction and not a measurement
- [ ] **The lifecycle greens are three shades of one colour** and were checked on
      a proof sheet rather than in use. `transcribed` against `notes_written` is
      the pair most likely to be indistinguishable at a glance; both sit beside
      the word for the status, so the cost is low, but watch it

### 20b. Noise clusters — marking a speaker as *not a person*

**Raised on 2026-09-03: "some of the speakers ended up being noise from the
nextdoor room. I would like to be able to mark it as noise and have the
associated words eliminated."** Deferred out of the feedback batch because it
deletes lines from a transcript, and that is the rule this project is most
careful about. **Built on 2026-09-04**: `referat/noise.py`, `referat denoise`,
and a third button in the Speakers dialog.

**The precedent exists and it is `debleed`.** `CLAUDE.md` already argues that an
echo copy is not a second utterance but a second *recording* of one, and lets
`referat debleed` remove whole entries — on four conditions, all of which this
must inherit rather than re-argue:

- [x] **Every removed entry is recorded in `meta.json` before the transcript is
      touched**, with the line kept verbatim, so an interruption leaves a record
      of a removal that did not happen rather than a removal with no record. The
      key is `transcription.noise` beside `transcription.debleed`, and it
      **accumulates** across passes for the reason debleed's does. Keyed by
      label, since one meeting can have several noise clusters marked at
      different times; each entry is `{at, channel, removed: [{at, text}]}`
- [x] **Dry by default**, `--apply` to act, and the dry run prints what would go
- [x] The removal stays **checkable**: somebody can read the record and see
      exactly what was deleted and why — and *why* here is always the same,
      that a person said so, which is why the record carries no verdict field
- [x] It is a **repair**, like `reflow`, `relabel` and `debleed`, and belongs
      beside them: `referat denoise <id> --speaker SPEAKER_NN`
- [x] **One more condition than debleed, found while writing it.** `Meeting.save`
      never raises, by a rule written for recordings — a failed metadata write
      must not take audio down with it. Here the metadata *is* the justification
      for the deletion, so `mark_noise` writes it through `write_json_atomic`
      directly and a record that fails to land refuses the deletion outright.
      `debleed` went through `save` and inherited the hole; closed the same way
      in the same session

**Where it differs from debleed, and this is the part to get right.** Debleed
decides by evidence — coverage in time and text against the other channel — and
refuses when the evidence is thin. This has no evidence at all: *that cluster is
the room next door* is a human judgement made by listening, and nothing in the
data distinguishes a quiet neighbour from a quiet participant. So:

- [x] **A person marks it and the machine never infers it.** No heuristic, no
      "clusters under N seconds are probably noise". That is the same rule as
      *nothing is inferred from a transcript*, and the cost of getting it wrong
      is deleting somebody's actual words
- [x] **The voiceprint question is separate and must be asked.** A noise cluster
      must never be offered a name — the same defence `voices.is_echo` gives
      echo clusters, so `Cluster` grows a second verdict rather than reusing
      `echo`, which means something specific and true (*this is the loopback
      coming back*). Two flags, because a later reader must be able to tell which
      judgement was made. `Cluster.noise`, `voices.is_noise`,
      `voices.mark_stored_noise`; `unknown_speakers` skips both flags, and
      `label.apply_name` and `label.name_speaker` refuse both in words
- [x] **What happens to a cluster already named?** Refuse, and say so: a named
      cluster is somebody a person identified, and marking it noise would be
      deleting the words of a person they recognised. `label --forget` first —
      and the refusal names the `--forget` command with the name filled in
- [x] Decide whether marking noise should also drop the snippets. **Yes**, by
      the argument that keeps echo snippets from being offered; the stored
      snippet list is cleared as `bleed.mark_clusters` clears it, so `meta.json`
      does not carry paths that no longer resolve

**Open question worth answering before building it.** The `speakers/` snippets
are how somebody *tells* it is noise, and they are deleted when a speaker is
named — but a noise cluster is never named, so its snippets survive until the
meeting is deleted. Good: that means the Speakers dialog can play them and offer
*This is noise, not a person* right there, which is where the judgement is
actually made. Build it as a third action in that dialog rather than as a CLI
verb somebody has to remember.

- [x] **Built as both, and the dialog is the primary surface.** The CLI verb
      exists because the CLI owns every mutation and a verb is what makes the
      operation printable and testable; the button is where the judgement is
      made. Both call `noise.mark_noise`, and the modal quotes the same
      `noise.warning` the dry run prints — `delete_warning`'s arrangement
- [x] The button is flat, on its own row under the name field, right-aligned.
      A button of equal weight beside *Name this speaker* would read as a coin
      flip between two ordinary outcomes, and this is the answer for one speaker
      in ten. It needs no embedding, so it stays enabled where the name field
      does not
- [x] `SpeakerDialog.named` became `changed`: a noise verdict and a name both
      change the transcript and the unnamed count, and the window refreshes on
      either without needing to tell them apart
- [x] The meetings-folder template tells `/cleanup` that a `SPEAKER_NN` may be
      noise, not to invent a participant out of one, and whose job removing it
      is. **The live copy is reconciled by hand**, as always
- [x] `referat relabel`'s gate refuses a microphone that also clustered noise,
      **deliberately** — diarization found a second source on that mic and the
      `ME` lines are exactly the ones it attributed to nobody, so some may be the
      same door — and says so as noise rather than as an unnamed speaker somebody
      could go and name

#### Surfaced while building it

- [x] **Nothing real has been denoised.** Every rule was driven against a
      synthetic meeting in a scratch folder and the dialog was built offscreen
      over it; `2026-09-03`'s unnamed cluster is the first candidate and is
      somebody's to listen to first. **Done the same afternoon**: every noise
      cluster on the machine was marked through the dialog, and `referat list`
      shows no unnamed speaker left
- [ ] **There is no way back.** `--forget` reverts a name; nothing un-marks
      noise. The record in `transcription.noise` holds every removed line
      verbatim, so a reversal is a paste by hand today. Decided rather than
      overlooked — the lines are recoverable, and a verb to put them back is a
      second thing that edits a transcript — but revisit if the first real
      mistake is made
- [x] **The gate also still counts an echo cluster as an unnamed speaker** in **Done on 2026-09-06**: an echo cluster on the mic is now refused in its own words, as noise is.
      `_relabel_complaint`, and that is the older version of the same misleading
      sentence: an echo cluster cannot be named either. Worth the same wording
      fix
- [x] **`Meeting.save` swallowing `OSError` is a hole under `debleed` too** — see
      the fifth condition above. Closed
- [x] **`transcription.noise` is not rendered by `referat show`'s table**, only by **Done on 2026-09-06**: `_removal_lines` prints one line per record — a count and a date; the lines themselves stay in the file.
      `--json` and by the `noise` word on the cluster's line; `debleed`'s record
      is the same. Fine until somebody wants to read a removal back at the prompt
      rather than in the file
- [ ] **A rerun drops the verdict**, as it drops `echo`, because it rebuilds the
      block whole and renumbers — which is right, since the audio is re-diarized,
      and mostly moot, since a meeting that still has its audio is the exception.
      The cluster comes back unnamed and can be marked again

### 20c. A person is a record: full name, short name, email — and renaming

**Raised on 2026-09-03: "we will need to be able to add full names as well as
short names (or nicknames) so that we can support multiple people with the same
name."** Extended the same day, after the icons went in: **"renaming should be
possible, and any update should propagate out (transcripts and notes)"** and
**"short name (default), and possibly long name and even email to be associated
with a person"**.

Deferred because all three are the *same* change — a person stops being a string
and becomes a record — and because it is a schema change to the voices database
that touches every surface rendering a name. Renaming in particular is cheap
after that change and a migration before it, which is the whole reason it is not
built yet: **build the identity first and the rename falls out of it.**
**Built on 2026-09-04**, in that order, and the rename did fall out of it:
`referat/voices.py` holds the record, `label.rename_person` is the tenth guarded
function, `referat person rename` and the people page's *Rename…* both drive
it, and `scripts/person_fixture.py` drives all of it against a synthetic
version-1 database.

**The problem is real and is already visible.** The database holds nine first
names — twenty-eight by the day this was built. The tenth person called Anna is indistinguishable from the first, and
`voices.match`'s margin compares *names* — so two people filed under one name
would silently merge into one voiceprint set, which is the single worst failure
this system has. That is not a display problem; it is an identity problem.

- [x] **The identity is the full name and the short name is a label.** A person
      is `{"name": "Anna Karlsson", "short": "Anna"}`; `voices.json` is keyed by
      the full name and the transcript renders the short one. **Keyed by an id
      rather than the full name**, per the decision below; the rest holds. Two people called
      Anna are two keys and two short names that happen to collide, which is
      fine — a transcript saying `Anna:` twice in two meetings is no worse than
      today, and `referat people` shows both in full
- [x] **Migration is one-way and must be automatic**, since the existing nine
      names have no full name. Read a bare string as `{"name": x, "short": x}`
      on load, exactly as `MeetingStatus` maps the legacy `stopped`/`done`
      values purely on load. **Do not migrate the file**: the same argument step
      14 made about the three legacy meetings, and it keeps the change
      reversible
- [x] `speaker_names` in `meta.json` stores the **full name**, because it is the
      identity and the file is a record. **The id, in the end** — see below. The transcript stores the short name,
      because it is prose. `label.forget` already reverts by looking up
      `speaker_names`, so it keeps working; `relabel_transcript` needs the short
      name and gets it from the database
- [x] **The uniqueness rule has to be decided, not defaulted.** Two people may
      share a short name; may two share a full name? **Yes, decided** — probably yes — the world
      has two John Smiths — which means the key is not the full name either and
      there has to be an id. Decide that before writing any of it, because
      changing it afterwards is a second migration

#### The decision to confirm first, and the recommendation

**A person becomes exactly what a project already is: a record with an id.** Not
a new idea to invent — `projects.json` has held one since step 14, and the
argument transfers line for line.

- [x] **`{"id": ..., "name": ..., "short": ..., "email": ...}`, keyed by `id`.**
      The id is `projects.slugify(full name)` plus the same `-2` collision
      suffix, which is a rule this codebase has exactly one implementation of.
      Two John Smiths are `john-smith` and `john-smith-2`, which is the answer to
      the uniqueness question and the reason the key cannot be either name
- [x] **The id never moves and a rename changes a display field.** That is the
      sentence step 13 already wrote about projects — *a rename touches one file,
      and no meeting record, no transcript and no doc anchor is disturbed by it*
      — and it is what turns renaming from a migration into an edit
- [x] **`speaker_names` in `meta.json` stores the id**, because `meta.json` is a
      record and records store ids; `transcript.md` stores the **short name**,
      because it is prose. Exactly the split `tags` already makes between
      `meta.json` and everything a person reads
- [x] **Migration is on load and the files are not rewritten**, the way
      `MeetingStatus` maps `stopped`/`done` and for the reason step 14 gave about
      the three legacy meetings. A bare string in `voices.json` reads as
      `{id: slugify(x), name: x, short: x}`; a name in `speaker_names` resolves
      through the same slug. Keeps the change reversible, and there is no
      migration script to get wrong
- [x] **The nine existing names all slugify distinctly** — check that before
      trusting the above, because two that collided would be silently merged on
      load, which is the exact failure this step exists to prevent. **Checked:
      twenty-eight names, twenty-eight distinct slugs**, three of them already
      two words (`lars-klein`, `maria-skytte`, `vanessa-leung`). And the load
      does not trust it either: two legacy keys resolving to one id flag the
      database unreadable, so nothing merges and nothing writes

#### Renaming, once there is an id

- [x] `referat person rename <id> --name ... --short ... --email ...`, and a
      **Rename…** button on the people page beside *Forget this person…* — the
      same arrangement the projects page already has, driving one guarded
      function in `label.py`, never a primitive. The ninth `run_*` split —
      **the tenth**, counting `cli.promote_meeting`; `label.rename_person`, with
      `run_rename` as one line of dispatch and a `RenameDialog` of three fields
- [x] **`transcript.md` is a relabel and is already solved.** `label.relabel_transcript`
      rewrites the label field and nothing else, in both directions, and is the
      one sanctioned edit to that file. A rename is a relabel across every
      meeting whose `speaker_names` carries the id
- [x] **`notes.md` is the hard half and needs its own decision.** A note is
      prose: a name appears in `[[Wikilinks]]` *and* in sentences, and a
      find-and-replace across somebody's prose is the same move as spelling a
      name onto a `SPEAKER_NN` — it reads as authoritative when it is wrong.
      Three options and a recommendation:
      **(a) rewrite `[[Wikilinks]]` only** — bounded, checkable, and the brackets
      are the one place the note is *referring* rather than *saying*;
      (b) rewrite every occurrence, which is the one nobody can check;
      (c) rewrite nothing and let the drift show. **Recommend (a)**, with the
      count of rewritten links reported, and the prose left alone. **Built as
      (a)**: `label._rewrite_wikilinks`, bounded to the meetings whose
      `speaker_names` carries the id, whitespace inside the brackets tolerated,
      the count of notes reported, and a `synced` meeting dropped to
      `notes_written` from the function that knows the notes changed
- [x] **(c) is already partly built and is the safety net either way.** The
      people page's *No voiceprint on file* section is exactly a note or a
      transcript using a name the database does not hold, so a rename that
      misses a spelling **surfaces** rather than disappearing. Worth saying in
      the rename's own confirmation
- [x] **A pushed digest goes stale on a rename** and step 13 has no story for it:
      the Google Doc holds the old short name in text somebody may have written
      around. Report it, do not reach into the doc — the same rule as an orphaned
      anchor. **Reported** in the rename's own message, and half of it heals by
      itself: a rewritten `notes.md` changes its sha, so the next sync re-renders
      that block. Only prose somebody wrote *around* a block stays old
- [x] **`referat people` and `--json` grow the record**, and the JSON must still
      carry no embedding and no path into `.voices/`. An email is the first field
      here that is contactable personal data rather than a label, so re-run
      phase 5's check on the document with it in. **Re-run**, in the fixture:
      no `embedding` and no `voices` path in the document. The table prints ID
      and SHORT and deliberately not the email — a table is what gets pasted
      into a message

#### Email, and the one rule it is easy to break

- [x] **Stored and never sent.** *Nothing but notes leaves the machine* is a
      Convention, and an address is the first thing in this database that looks
      like an invitation to mail somebody. Referat has no mail path and must not
      grow one; the field is for a person reading the page
- [x] **It must not reach the hotword list.** `hotwords.collect` takes every name
      in the voices database, and an address is not a word Whisper should be told
      to hear. Names only — check that explicitly when the record lands, because
      the merge reads the database live. **Checked** in the fixture; the merge
      reads `VoicesDB.spellings()`, which cannot return one
- [x] Optional, and blank for the nine people already filed. Nothing derives it,
      nothing guesses it from a transcript
- [x] Every surface that renders a name is affected: the transcript, `referat
      people`, the chips, the People page, the hotword list (which should
      probably carry **both** spellings, since Whisper may hear either). **Both
      spellings**, where they differ. Also `referat show`, `relabel`, `debleed`,
      the noise refusal and the prompt's numbered list, all of which render a
      stored value through the database now
- [x] Cross-reference the flag rule in step 10: the meetings folder's *Known
      people and terms* table is where a full name and its ASR mangling already
      belong, and the two should not become separate registries of the same fact.
      **Cross-referenced, and the table does not exist** — see below

#### Surfaced while building it

- [ ] **There is no *Known people and terms* section.** `CLAUDE.md` said the
      meetings folder's `CLAUDE.md` carries one and that `/cleanup` normalizes
      names against it, with a near miss flagged in the note; the live copy, the
      template and the prompt have no such section and no such rule. The claim
      is corrected in `CLAUDE.md`; whether to build it is open. If it is built,
      `referat people` is already the registry of full and short names and the
      table should be generated from it rather than kept by hand, or the two
      become the separate registries this box was about
- [ ] **Nothing derives a short name from a full one**, so somebody new typed
      as `Anna Lind` is `Anna Lind:` on every line until `person rename --short
      Anna`. Decided rather than overlooked — *Lars* out of *Lars Klein* is a
      guess about a name — but two steps where the dialog's second field makes
      it one; watch whether the field gets used
- [ ] **Two people with one short name in one meeting are refused**, by
      `voices.namesake_in`, because a label is the short name alone and a later
      forget or rename would hit both. The refusal says what to do; it has not
      been met by a real meeting yet
- [x] **`is_mine` and the Actions tab still compare owners against **Done on 2026-09-06**: `actions_document`'s `owner` is the record's short name where the owner is on file.
      `[speakers].owner_name` as typed**, which is the config's spelling and not
      the record's. Right while the owner's short name is that spelling; a
      `person rename --short` on the owner would need the config changed too.
      Resolve the owner's spellings through the record when it bites
- [ ] **A version-2 file is invisible to a version-1 reader**, which loads
      nobody. Only matters for a rollback; `backups/` holds fifteen copies and
      the first one after this change is the last version-1 file
- [ ] **The `-2` id and a legacy `speaker_names` value.** `person_id` maps a bare
      name to the plain slug, which is right for every value written before the
      namesake existed and is the one case the mapping cannot serve afterwards.
      Rename writes ids into `speaker_names` as it touches meetings, so the set
      of legacy values shrinks; a script to rewrite the rest is a migration and
      was deliberately not written
- [ ] **`match.name` in older `meta.json` files holds a bare name** and is
      rendered through `display`, so it reads right; a `rerun` writes the id.
      Nothing to do unless something starts comparing that field

### Phase 6 — the dashboard

**Built on 2026-09-03.** One new page, one new pure function in `cli.py`, one new
module holding two formatters that had a second reader, and the Dashboard as the
window's first tab.

- [x] **The opening screen**: recent meetings, pending untagged meetings, pending
      unlabeled speakers. Three queues and a list, all of them already computable
      from `cli.list_document`.
      **The third queue is *no notes yet*** — the spec named two and counted
      three, and this is the one the window had already grown a button for
      (*Notes for all…*), so it existed as a predicate before it existed as a
      queue. Ordered tag, then label, then notes, which is the flow rule this UI
      already follows
- [x] **The page writes nothing and reads nothing.** Every row opens that meeting
      on the Meetings tab, where `Tags…`, `Speakers…` and `Generate notes…`
      already are; draining a queue from here would be a second path to each of
      those three writes, and the picker and the labeling dialog are where the
      rules about what a tag and a name may be are enforced
- [x] **Themes and action items extracted from notes are a later box inside this
      phase and not its baseline.** The dashboard is worth having without them.
      **The room under the recent list is where they go**, which is why that list
      is capped at eight rather than allowed to grow into it.
      **The action items half is built (2026-09-03); themes are not.** The box
      went exactly where this said, and the cap is what made room for it
- [x] When they are built: they read `notes.md`, which is a *derived* artifact,
      so extraction does not breach *nothing is inferred from a transcript*. The
      line it may not cross is assignment — **it may never tag a meeting with a
      project**, propose one, or reorder the untagged queue by a guess. The
      human's tags stay an input everywhere they appear.
      **Held.** The one place it was tempting is a collective owner: `All
      authors` plainly includes the owner and `is_mine` still refuses to resolve
      it, because working that out is inferring an assignment
- [ ] **Themes are still unbuilt**, and are now the only thing left in this box.
      They are the harder half: an action item is a line somebody already wrote
      down, while a theme is a claim about several meetings that nothing in the
      notes states outright. Whatever does it may render what the notes say and
      may not tag, propose or reorder — the same line, which action items did not
      have to test because they never came close to it

#### What building the action items settled

- [x] **The grammar is stable enough to parse, and that was measured before it
      was relied on.** 64 items across nine files, zero deviations from
      `- **owner(s)** — text`. Re-measured at 83 across eleven once two more
      meetings landed, still zero. `scripts/actions_report.py` is how that is
      re-checked, and it reports a bullet it could not read rather than passing
      over it
- [x] **The due-date rule is the part that needed measuring twice.** Any literal
      `YYYY-MM-DD` gets five of sixty-four wrong and always in the direction that
      matters — a meeting date, a start date, or something deferred *past* the
      date, each shown as a deadline. The cue rule that replaced it: 13 dates, all
      correct, no false positives. The five counter-examples are named in
      `actions.CUE_DUE_RE` because that is the docstring somebody will simplify
- [x] **An item's identity is its wording, and the cost is named rather than
      hidden.** A re-cleanup that re-wraps a bullet keeps its key; one that
      re-words it orphans the tick. Orphans are kept, counted in the footer and
      pruned only on request — pruning on read would turn a read into a write
- [x] **Per person, because the parse gets everybody's anyway.** The picker was
      the user's correction to a *Mine only* checkbox, and it is better: the tab
      answers *what does anybody owe* and the dashboard box answers *what do I
      owe*, from one document and one predicate
- [x] **`write_text_atomic` needed a retry.** Ticking is the first operation here
      frequent enough to lose a race with Dropbox holding the file it is
      uploading; `os.replace` fails with `WinError 5` and succeeds a moment
      later. Shared machinery, so `meta.json` and `projects.json` got it too
- [ ] **The orphan-review modal is unexercised against a real re-cleanup.** It
      was driven by planting an orphan in `actions.json` by hand. Watch what it
      says the first time `/cleanup` genuinely re-words an item somebody had
      ticked
- [ ] **`SPEAKER_08` is an owner in `2026-09-03_1316`.** `/cleanup` is allowed to
      assign to an unnamed speaker and did, so the picker offers a cluster number
      as a person. That is honest — it is what the note says — but it is also a
      standing argument for labeling that meeting
- [ ] **A collective owner has no answer.** `All authors` and `All TAs` are real
      entries in the picker and belong to nobody. Leaving them literal is right;
      whether the owner's list should offer them *somewhere* is not decided
- [ ] **A wikilink in an item body is not always a person.**
      `[[design fiction]]` is a project-shaped link inside an action item, which
      is the viewer's *linked whatever it says* caveat arriving somewhere new.
      The Actions tab does not linkify bodies yet, and this is the reason to be
      careful when it does
- [ ] **The day summary has met one day.** `2026-09-03`, five meetings, and it
      came out the right length and shape. It has never been run on a day with
      one meeting, or with a meeting whose notes are missing, and the "name it
      rather than skip it" instruction is therefore untested

#### What building it settled

- [x] **A fifth tab cost no fifth scan.** Every other page reads something when it
      is switched to, and is refreshed only while in front because each read
      costs a scan of both meeting roots. This one is *handed* the
      `list_document` the window has already read for its meetings list, so it is
      exempt from that rule rather than an exception to it — and it is kept
      current unconditionally, because there is nothing to defer
- [x] **`cli.pending` is where the three predicates live**, pure over the
      document and never over a `Meeting`. `Notes for all…` had its own inline
      copy of the notes predicate, which is the fifth time a rule has been found
      living inside the one caller that needed it; a queue whose count came from
      one predicate and whose button drained another would be wrong in the way
      that is hardest to notice, because it would look right
- [x] **`LIVE` is the pair held out of every queue.** A meeting still being
      recorded or transcribed is not pending *work* — nothing to do about it but
      wait — which is the same distinction `run_list`'s footer makes when it
      refuses to suggest a `rerun` for a live meeting. Checked against a
      synthetic listing carrying one of each
- [x] **`referat/ui/rows.py`**: `status_text` and `tags_text` out of `window.py`,
      which was their only reader until this page rendered the same meetings in a
      different shape. A status cell reading `gate failed - no mic (staging)` on
      one tab and something else on another is the drift `format_duration` and
      `audio_state` keep being pulled back from, one layer up
- [x] **Opening the window at a meeting had to learn which tab it meant.** The
      window opens on the dashboard now, so `show_window` brings the Meetings tab
      forward whenever it is given a meeting id or asked for the untagged inbox —
      which is what step 16's on-stop toast does. A toast that opened a summary of
      every *other* meeting is the same failure as a link landing behind a filter:
      it looks exactly like a link that did nothing. With neither argument the tab
      is left where it was
- [x] **An empty queue says its own sentence and keeps its place**, disabled
      rather than hidden — *Nothing waiting. Every voice has a name.* The three
      sit in a splitter, because equal thirds is the right default and the wrong
      steady state: a morning of untagged meetings against two cleared queues is
      the normal shape of this page. Sizing each list to its contents instead was
      rejected — the column would jump every time a meeting was tagged, which is
      the thing that makes a queue hard to work
- [ ] **Still unexercised: the page against a machine with work on it.** This one
      has nine meetings, all tagged, all with notes, and one unnamed speaker — so
      two of the three queues have only ever been drawn empty and none has ever
      needed to scroll. Rendered against a synthetic listing carrying a staged
      `gate_failed` meeting, a live recording and a live transcription, which is
      what checked the exclusions; that is not the same as watching a real
      backlog drain
- [ ] **Nothing on this page reports the recorder.** The state label and the
      activity strip are above the tabs and in the status bar, visible from every
      page, so a dashboard saying it again would be a second thing to keep in
      step. Watch whether the opening screen wants a *right now* line anyway once
      it has been opened mid-meeting a few times

#### From reading the first day summary (2026-09-03)

Ten pieces of feedback after the day summary had been looked at once. Six were
built the same evening; the other four are below, unchecked.

- [x] **The names in it were dead.** The day pane ran `setMarkdown` over the raw
      file, so a `[[Wikilink]]` rendered as brackets and clicked as nothing,
      while the identical link in the notes pane opened the People tab. Now
      through `viewer.link_people` and a three-way `anchorClicked` of its own —
      *a name means one thing wherever it is rendered*
- [x] **The brackets came off every wikilink, everywhere.** `link_people` used to
      keep `[[…]]` on the reasoning that a link quietly dropping them would look
      like a different notation. That argued about the *file*; this is the
      rendering, and a note is mostly people, so a page of `[[…]]` reads as
      markup somebody forgot to render. Bold instead, which is what
      `richtext.py` and `digest.py` were already specified to do with one
- [x] **So `unlink` rebuilds a name from its URL rather than from its text**, and
      grew a `wiki` flag to do it. The same `referat-person:` link is a wikilink
      in the notes and a *speaker label* in the transcript, whose source notation
      is the bare `Anna:` it already renders as; the rendering cannot be read
      back to tell them apart, so the caller says which pane it is copying
- [x] **The date is written out** — `Today - Thursday, September 3, 2026`. The
      ISO form stays the file's name, the picker's `currentData` and what
      `/standup` is handed, and stops being the thing on screen. Assembled rather
      than `strftime`d: `%d` pads to `03` and `%-d` is glibc's spelling of what
      the Windows runtime calls `%#d`
- [x] **Every bullet cites its meeting, and the citation is a link.** `/standup`
      now ends each line with `[2026-09-03_1408]` and `viewer.link_meetings`
      turns it into a `referat-meeting:` link reading `14:08` — the date is
      dropped because the page is one date. A third scheme beside `referat:` and
      `referat-person:`, and the last one
- [x] **The project goes in front of the bullet, and Referat says it — not the
      prompt.** A meeting's `tags` are in `meta.json`, which `/standup` may not
      read, and a project worked out from what a meeting sounded like is exactly
      the guess this repo refuses. So the prompt cites the meeting it certainly
      read and `dashboard.day_markdown` joins on the human's tag, through
      `rows.tags_text` so an orphan gets the same trailing `?` it does elsewhere.
      **This is the citation earning its place twice**, and the reason to resist
      asking the prompt for a project directly the next time it looks simpler
- [x] **`LINKED_RE` was running through an unescaped `[`.** A transcript
      selection reconstructing as `[00:00:09] [Niklas](referat-person:Niklas): …`
      matched from the timestamp's bracket to the name's closing one and came out
      as `00:00:09] [Niklas:`. Refused now, which costs nothing since nothing
      here puts a bare `[` in a link text. Found by exercising the copy paths
      after `unlink` changed, which is the argument for exercising them
- [ ] **Every summary already on disk predates the citations**, so it renders
      with no prefix and no link — correctly, since there is nothing on the line
      saying where it came from. *Rebuild…* is what gives one provenance. Watch
      the first genuinely cited summary: whether one bullet per meeting is the
      shape it settles into, and whether a cross-cutting bullet really does cite
      both
- [ ] **A bullet's project is the meeting's, so an untagged meeting gets none.**
      No placeholder, because the untagged queue two boxes down is where that is
      said. Watch whether a page of unprefixed bullets reads as *untagged* or as
      *broken* — if it is the second, the prefix is the wrong place to be silent
- [ ] **The day summary is never rebuilt on its own**, and nothing yet says so on
      the page. It is written only when somebody presses *Summarize…* /
      *Rebuild…*, which is the no-automatic-LLM-pass rule holding, and the button
      changing its label is the whole of the hint. Watch whether *today*'s
      summary going stale as the day fills up wants something more than that —
      a *written at 14:12, two meetings since* line would be honest and is
      **not** an argument for a timer
- [x] **A speaker nobody will ever name has no way out of the queue.**
      `2026-09-03`'s unnamed cluster is noise — a door, a corridor, a cough — and
      the only way to stop the *Speakers nobody has named* queue prodding about
      it is to give it a name, which files a voiceprint for a noise. It wants a
      third answer beside *name* and *leave*: a per-meeting **dismissal** of one
      cluster, written into `meta.json` beside `speaker_names` and read by
      `voices.unknown_speakers` so both the queue and the dialog skip it. Two
      rules it must not break — it is **not** a name, so nothing goes into
      `.voices/`; and it is per meeting, because `SPEAKER_NN` numbering is per
      meeting and dismissing *a number* globally would silence a different person
      next week. `bleed`'s spared-cluster flag is the shape to copy.
      **Built as step 20b on 2026-09-04**, and it went one step further than a
      dismissal: the flag is `Cluster.noise` beside `echo` rather than a key
      beside `speaker_names`, and the lines go out of the transcript too, under
      `debleed`'s conditions. Both rules held — nothing reaches `.voices/`, and
      the verdict is per cluster of one meeting

### Phase 7 — Google Docs export

**Built on 2026-09-04**, the same day step 13 was, and the last phase of the
command center. Asked for as *"is the GDoc functionality integrated into the UI?
I would like to be able to paste a link into the UI and not use the CLI."*

- [x] **Gated on step 13**, and the gate opened the same day. *Link doc...*,
      *Unlink* and *Sync now* on the projects page, driving `cli.link_doc`,
      `cli.unlink_doc` and `cli.sync_project` — three buttons rather than a
      second implementation, which is what splitting those out as guarded
      functions was for
- [x] **Google auth was step 13's problem and stayed there.** The consequence
      for this phase is a property rather than a limitation: the paste flow needs
      a terminal, so **the window can never authenticate**, and a dialog that
      popped a browser consent from the thread that owns the recorder is
      structurally impossible rather than merely avoided
- [x] **Nothing but notes leaves the machine.** Unchanged: the UI makes linking
      convenient and changes nothing about what is sent
- [x] **The link is the input, not the id.** `referat/ui/docs.py` takes the
      address bar or the Share button's link, and the `?tab=` in it preselects
      the tab. A document id is forty-four characters out of the middle of a URL
- [x] **The tabs are fetched and shown rather than typed**, which is the one
      place a window should not copy the CLI: a prompt can refuse and list them
      in the refusal, and a dialog that could simply show them should
- [x] **The tab is never guessed.** A `?tab=` decides it, or a tab named
      `Meetings` does, and failing both nothing is selected and *Link* stays
      disabled. Defaulting to the first tab was written first and was wrong on
      the very first real document, whose tabs are `Proposal` and `Meeting Notes
      & Writing Log`. A wrong default that is *visible* is still a wrong default
- [x] **The network runs on a thread**, reporting through a queued signal and
      `referat.progress`, with no Qt on it. One job at a time: two syncs of one
      project would each hold indices from their own `documents.get`

### Left open

- [x] **Two things a running window found**, reported on 2026-09-04 after real
      use: the doc list did not redraw after a link (the `_fill_form` staleness
      above), and **selected rows were black on dark blue** -- a Qt
      `windows11` style bug that only bites views with `alternatingRowColors`
      on, which is every view here that shows data. Both fixed; the second was
      diagnosed by rendering a `QListWidget` to a pixmap and counting light and
      dark pixels, since the palette itself was correct and said nothing was
      wrong.
- [x] **And the correction was then made in the wrong place**, which is the part
      worth keeping. Set on the `QApplication`, it reached the lists that do
      *not* stripe — where the same style paints a soft grey selection and keeps
      the text dark — and turned them white-on-light-grey. `referat/ui/lists.py`
      is the fix: striping and the correction are one function, so a view cannot
      have one without the other. **A measurement that only covers the broken
      case is half a measurement**; the grid has four cells and only two were
      checked the first time.
- [ ] **Nobody has clicked these buttons in a running window.** They were driven
      offscreen — the page builds, the buttons enable and disable correctly, the
      dialog's lookup thread returns real tabs — but a `QApplication` under
      `offscreen` is not somebody using it
- [ ] **A sync that fails halfway has not been seen in the window.** The message
      comes back through `doc_job_done` like any other; what is unexercised is
      what a half-written document looks like to somebody reading the page
- [ ] **`--prune` is not in the UI at all**, deliberately for now: it is the one
      operation here that destroys somebody's prose, and it has never been run
      against a real block from anywhere

### What this needs from Python first

- [x] `referat transcript <id> --json` — phase 1, parser beside the renderer.
      `transcribe.parse_transcript` sits beside `render_transcript` and
      `parse_entry`, and returns the entries plus a count of the non-blank lines
      that did **not** parse — reported rather than swallowed, because the header
      is always one of them so it is never zero, and a large one is how a reader
      finds out the file was written by something else
- [x] `referat show <id> --json` — phase 1. The human form prints each cluster's
      match *and its runner-up, including where the match was refused*, which
      nothing had ever printed and which is the only material there is for
      calibrating `[speakers]`' two numbers. See the open item under 7b
- [x] `label <id> --json` gains `gallery` — phase 3. Built as `scoped`,
      `rest` and `tags`, out of `referat/people.py`'s two-direction join, with
      the whole document split into `label.label_document` so the window renders
      what the CLI prints
- [x] `referat project describe` and `project glossary`, and `hotwords --json`
      — phase 4. The two verbs are the prerequisite that box named: the CLI owns
      every mutation, so the projects page could not edit a glossary until
      something could. `hotwords_document` is the third `--json` this step has
      added over a document the window also renders
- [x] `referat people [--json]` — phase 5. Plus the `people` field on `transcript
      <id> --json`, which was not foreseen here and is the same move a fourth
      time: the rule for *what counts as a name* is `voices.name_complaint`'s, so
      the document names the labels rather than the viewer re-deciding
- [x] **No `--json` on the write verbs.** `tag`, `untag`, `state`, `promote`, **Dropped on 2026-09-06: the extension was deleted at step 23.**
      `delete`, `label --speaker`, `label --forget` and the project verbs keep
      answering in prose, and the window keeps the property `cli.ts`'s `mutate`
      gave the extension: a refusal reaches the user in the words of whatever
      owns the rule. In-process that is the returned complaint rather than
      stdout, and it is the same sentence

### Open questions

- [x] **Qt widgets or QtWebEngine.** Answered in phase 1: widgets, and
      QtWebEngine is not installed at all
- [x] **Does PySide6 survive Smart App Control on this machine?** Phase 0's gate,
      and the answer is the best one available: 247 of 247 native files signed by
      The Qt Company, nothing unsigned. Not a permanent answer — re-audit after an
      upgrade, since a wheel is signed by whoever built it
- [x] **Re-raised on 2026-09-02 and re-affirmed: still Qt widgets.** Worth a box
      of its own so it is not asked a third time from the same symptom. The
      symptom was that the window feels thin — no tagging, no labeling, no
      projects, no people, no Google Docs — and **every one of those is a phase
      2-7 box that is simply unbuilt**, not a thing `QTextBrowser` cannot draw.
      Three of the complaints were real gaps and not one of them is a rendering
      limit either: text scale and copying became phase 1b above, and hotword
      management became phase 4's glossary. Against that, the cost has not moved —
      QtWebEngine means `PySide6-Addons`, and phase 0's audit covered
      **Essentials only**, so Addons' bundled Chromium is unaudited native code
      that would sit in the *tray* process, on the recording path, where a Smart
      App Control block costs a meeting rather than a screen. **Rebuilding a UI
      is what you do when the toolkit is the constraint; here the constraint is
      that six phases are not written**
- [x] **When does `referat-vscode/` actually go?** "At feature parity" is a **Dropped on 2026-09-06: the extension was deleted at step 23.**
      judgment call and the phase is not fixed. Phase 5 is the earliest honest
      candidate, since that is where the window does something the sidebar never
      could.
      **Phase 5 is built and the answer was: not yet, decided on 2026-09-03.**
      The window now does the thing a column could not, but the page has been
      driven against six meetings and no unnamed speaker; parity is reassessed
      once it has met real work. The extension stays in maintenance meanwhile —
      fixed when it breaks, never extended, not packaged again
- [ ] **Automatic project-scoped re-matching**, which this step narrows to the
      gallery. A re-match of a meeting's still-unnamed speakers when it is tagged
      is the obvious next move if the gallery proves the restriction is right; it
      is deferred rather than dropped, and it would use `voices.match` unchanged
      against a filtered database
- [ ] **One doc per project in the UI, zero-or-more in the model.** Watch which
      one is wrong. The schema is the harder thing to change and should not be
      narrowed to match a screen
- [x] **Two graphical surfaces reading the same data through the overlap.** The **Dropped on 2026-09-06: the extension was deleted at step 23.**
      extension shells out, the window imports, and both must stay correct until
      the extension is deleted. A change to a `--json` document has two readers
      until then
- [ ] **Does the tray toast still work under Qt?** Step 16 needs an AUMID and a
      WinRT toast either way, but the host process is no longer `pystray`'s
- [ ] **What does the window do while a meeting is transcribing?** The state is
      already rendered; whether it should show progress, and where progress would
      come from, is not designed

## 21. Archiving a project

**Planned and built on 2026-09-04**, from: *"the ability to archive (and
unarchive) projects. Keep them in archive, keep their times, associate people
with projects so they can be set as inactive?"* Projects are the first of the
three entities that grows without bound — a thread of work finishes and its name
sits in the tag picker forever beside the four that are live — and until now the
only tool for one was `project rm`, which is the wrong tool: it cascades nothing,
so every meeting it tagged keeps the id as an orphan and the record of what that
meeting was about is lost. **Archiving is a presentation decision and the whole
of it.** Nothing is hidden from a meeting, nothing is deleted, nothing cascades.

### `projects.json` gains `archived_at`

- [x] `archived_at`, an ISO-second string beside `created_at`, `""` while the
      project is live, with an `archived` property over it so nothing else reads
      the emptiness. A timestamp and not an `archived: bool`, because `""` is
      already this file's falsy absence and a flag beside a date would be two
      places saying one thing — the mistake this repo records `voices_dir` and
      `format_duration` each being pulled back from — while a bare flag throws
      away the one fact worth keeping, which is when the work stopped
- [x] Added to `Project.to_json`'s **explicit** key list, or it does not
      round-trip. `from_json` took one tolerant line and no new tolerance: it
      already ignores unknown keys and defaults missing ones
- [x] **`SCHEMA_VERSION` stays at 1**, and its docstring now says why. There was
      nothing to migrate: a file written before this step loads with every
      project active, which is true of it. Bumping would have asserted a
      migration that does not exist, and nothing reads the value back. Move it
      when a key changes *meaning*, not when one is added
- [x] **A downgrade loses which projects were archived and nothing else.** An
      older `to_json` drops the key on its next save; no tag is orphaned, no
      glossary lost, no meeting touched. That is what the hide-only scope buys,
      and it is recorded rather than defended against in code
- [x] Verified both directions by hand: a `projects.json` with the key deleted
      loads all-active, and the next write stamps `""` onto every project

### The primitive and the operation

- [x] `ProjectsDB.set_archived(pid, archived)` beside `describe` and
      `set_glossary`: one field, no save, `None` for an unknown id. **A second
      archive does not move the date** — it is when work stopped, and re-running
      a command must not rewrite history, which is the reason `rerun` stopped
      clearing `speaker_names`. The clock is minted here beside `add`'s
      `created_at`, so this file has one
- [x] `cli.set_archived(config, pid, archived)` — **one guarded function taking a
      direction, not two.** The other five project functions are five different
      operations that do not pair; this is one flag with a direction, and the
      settled shape for that was already in this file twice: `apply_tags` takes
      both directions with `run_tag` and `run_untag` as thin wrappers, and
      `set_action_done` / `dismiss_action` are boolean-direction guarded
      functions. Two would have been two copies of the same open, mutate, save
      and report differing in one boolean
- [x] **Both no-ops come back `ok` having written nothing**, which is the case
      `Outcome` exists to describe. The already-archived message names the
      **date**, because that is what makes a no-op distinguishable from a write
      at a prompt — the reason `_tag_lines` says both halves of an idempotent
      change. Verified by hash: two archives leave `projects.json` byte-identical
- [x] The success message says the half somebody assumes the other way, as
      `remove_project`'s does about orphans. Deliberately **no meeting count**,
      unlike `rm`: that costs a `load_meetings` scan of both roots, which a
      delete pays for because it is irreversible and the orphans are the one
      thing nobody would otherwise see. Nothing is lost here, so nothing is
      counted

### The reading vocabulary, and the one thing it may not do

- [x] **`name_map()` is never narrowed**, and that sentence lives in
      `archived_ids`' docstring where the next person to have the idea will read
      it. `tags_cell` and `rows.tags_text` render an id *missing* from that map
      with a trailing `?`, so narrowing it would turn every archived project's
      tags into orphans in `referat list`, in `list --json`, in the meetings
      tree, on the dashboard, on the people page and in the extension, all at
      once. Archiving hides a project from the places that **offer** one; it may
      never unname a tag
- [x] `project_names` becomes `(names, archived, complaint)`. One caller in the
      repository, so there was no compatibility surface; its docstring already
      called it the picker's whole reading vocabulary, which grew by one word
- [x] `project_document` needed **no code at all** — it spreads
      `**project.to_json()`, so the field arrived free. Said out loud in its
      docstring, because that is the payoff of the dataclass being the schema
      rather than an accident to leave unremarked
- [x] `people_document` gains `archived` and a per-person `inactive`, and lost a
      redundant `ProjectsDB.load` on the way: it already opened the file for
      `name_map()`, so both views now come off one loader
- [x] **`list_document`, `show_document`, `pending` and `actions_document` gain
      nothing.** A document gains `archived` only where something renders a
      difference, and none of these do — `pending`'s three queues are about
      meetings, and archiving a project makes no meeting more or less untagged

### `referat tag` still adds an archived id

- [x] **Allowed, deliberately; the picker simply does not offer it.**
      `apply_tags` refuses an unknown id for a reason written beside it — an
      orphan should be made by deleting a project, not by mistyping — and an
      archived id is not an orphan. Refusing would widen a guard past its own
      recorded reason and would make a late meeting for a finished project cost
      three writes: unarchive, tag, re-archive
- [x] It is *a missing tag must never cost a name* said one entity along:
      **an archived project must never cost a tag.** Hiding is what a surface
      does, and a verb does not inherit it
- [x] `apply_tags` says so in its own message rather than `run_tag` doing it — a
      `run_*` holding a rule is a `run_*` a second surface cannot use, corrected
      seven times before this one. It costs **no new read**, being computed
      inside the existing `if add:` block where the database is already open, so
      the load-bearing asymmetry that a pure removal never opens `projects.json`
      is untouched. Keyed on what changed rather than on what was asked, so
      re-tagging an already-tagged archived project says nothing new

### A person is inactive when every project they carry is archived

- [x] Derived on every read and **stored nowhere**. There is no `inactive` key in
      any file and there must not be: it would be a fifth thing to keep in step
      with `meta.json`'s tags, the voices database and the transcripts, and the
      first one to disagree with them
- [x] Computed in `cli.people_document` and **not** in `people.directory`, for a
      harder reason than tidiness: that module deliberately never opens
      `projects.json`, and `people.gallery` is built on it through
      `project_people`, so archivedness introduced there would reach the speaker
      dialog's scoping — the one place it must never go. `people.gallery` is
      unchanged, emphatically: a missing or archived tag must never cost a name
- [x] **Three ways to be active, and all three are one principle** — an unknown
      must never be read as an ending. No tags at all is active, because no
      project is not a finished one. An **orphaned** tag is active, because that
      says the project record is gone and not that the work stopped. And somebody
      seen in an **untagged meeting** is active even when every tag they do carry
      is archived
- [x] That third one needed `Person.in_untagged`, which is why it exists: `tags`
      alone cannot tell a person seen in one archived project and one untagged
      meeting from one seen only in the archived project, since an untagged
      meeting contributes no id to compare. It lives in `people.py` because it is
      a fact about `meta.json` alone and costs that module no new file. **Found
      by driving it** — the first run called somebody inactive who had been in an
      untagged meeting that morning
- [x] `in_untagged` reads a missing meeting and an untagged one differently, or
      deleting a meeting would quietly reactivate everybody who was in it
- [x] All five boundaries verified by hand against a scratch meetings folder:
      archived-only, live, untagged-only, archived-plus-untagged, orphan-only
- [x] `referat people` gets a footer line naming them, beside the drifted one and
      for the same reason, rather than a sixth column or a second marker in the
      PROJECTS cell — that cell already has one vocabulary and a second would
      blunt the one that means something

### The window

- [x] An **Archive… / Unarchive** button in the projects page's title row,
      between Rename and the red Delete: harmless, then reversible, then
      destructive and last. One glyph and not two, with the label carrying the
      direction — Pause becomes Play because they are two different things to the
      recorder, while this is one flag whose state the section and the subheading
      already say
- [x] Archiving is behind a modal saying what it does **not** do; unarchiving
      asks nothing, because nothing is lost by it and a confirmation there would
      be a dialog for the sake of symmetry. The modal is shown before the command
      runs and so is the same exception the Delete modals are
- [x] An **Archived** section in the list, between the live projects and the
      orphans, ordered by how much of a project each thing is. Its heading is
      unselectable and **its rows are not**, unlike the orphans': you select one
      to unarchive it, and everything in the form still edits — the glossary
      above all, since it still feeds the hotword list. `_first_project` needed
      no change and is why archived rows keep their `ID_ROLE`
- [x] An archived project the meeting **carries** stays in the picker, ticked and
      removable, labelled with its real name and `(archived)` — never
      `ORPHAN_SUFFIX`, which would be a lie about a project that exists. It cost
      nothing to build: `_carried` and `_checked` do not know about archiving and
      do not need to, which is the two-sets design paying off a second time
- [x] A new `archive` glyph in `icons.py` — a plain body under a wide lid with a
      handle slot punched through, drawn deliberately far from `_trash`, which
      sits two buttons away and means the opposite. Drawn in the palette's
      **`windowText`** and not in a state colour: `LIFECYCLE` is keyed on
      `meta.json`'s `status` and is about *meetings*, so borrowing its grey for a
      project would be the drift that palette's docstring guards against
- [x] An **Only archived projects** section on the people page, above the drifted
      one, because drift is the database disagreeing with itself and is the more
      urgent of the two. The heading names the fact rather than calling somebody
      inactive, which is the register that page's privacy note is in; the
      document's field stays `inactive`
- [x] A person's Projects list marks an archived entry — there it earns its
      place, being what explains the section they are sitting in
- [x] **The Archived section folds, and starts folded.** Asked for straight after
      the feature landed: a finished project is the one somebody is least likely
      to have come here for. Done with `setHidden` on a `QListWidget` rather than
      by moving to a `QTreeWidget` — the tag picker already hides rather than
      rebuilds, and a tree would have rewritten `_fill_list`, `_first_project`,
      `select_project` and both heading kinds to fold four rows
- [x] The heading is **enabled but not selectable**, which is the one flag
      between it and the inert orphan headings: `QListWidget` delivers no
      `itemClicked` for a disabled row, so a foldable heading has to be enabled,
      and leaving `ItemIsSelectable` off is what keeps it out of the selection,
      out of arrow-key navigation and out of `_first_project`'s search
- [x] **Two overrides open it anyway, and neither is a convenience.** A project
      that has just been archived would otherwise vanish from the list while its
      form is still on screen; and with no live projects at all a folded section
      is a page that looks empty while holding four. `_archived_open` is synced to
      what was actually drawn, so the next click closes what is visible
- [x] The count is in the heading, because a folded section whose rows are hidden
      would otherwise say nothing about what it is hiding. `_toggle_text` is one
      function called from both the build and the fold, since the fold rewrites
      the label in place rather than rebuilding the row
- [x] **Found while driving it: the archive button did not flip.** `_fill_list`
      reselects the row that was already selected, which fires no change and so
      refills no form — the very property that keeps a half-typed glossary across
      a refresh — so the button still read `Archive...` on a project that had
      just been archived, which is the one control somebody would click next.
      `_fill_archive_state` is split out of `_fill_form` and refreshes those two
      parts alone; refilling the whole form would have silently discarded an
      unsaved glossary, since archiving does not save one

### Deliberately not done

- [x] **Glossaries still feed the hotword list, archived or not**, and nothing
      about the merge changed. A glossary is read twice at two different times and
      the first is *before any meeting has been tagged* — so an archived
      project's terms are exactly as likely to be said in the next meeting as
      they were last month, and dropping them would make archiving cost a
      transcription. Verified unchanged by running `referat hotwords` on both
      sides of an archive
- [ ] Worth revisiting **only when the 223-token cap actually bites**: a
      project's glossary is the lowest-priority source already, and *archived*
      would be the first honest tie-break inside that tier. The cap has to be
      seen dropping something first, which is what the projects page's hotword
      panel is for
- [x] **Meeting rows are not decorated.** `rows.tags_text` and `cli.tags_cell`
      mark an orphan with `?` because something disappeared; nothing disappears
      here, so a marker would be decoration and a second one would blunt the one
      that means something. It would also have forced an `archived` key into
      `list_document` for a decoration's sake
- [ ] Revisit that if a meeting row's tags ever start reading as stale
- [x] **Nothing cascades.** No `meta.json`, no `notes.md`, no Google Doc, no
      voiceprint. Archiving touches one field of one entry in one file, which is
      why it is reversible and why it can afford to be a button where a delete is
      a modal
- [x] **The VS Code extension needed no change at all**, and that is the
      strongest evidence the scope was drawn in the right place. TypeScript
      interfaces are structural, so `ProjectJson` without `archived_at` parses the
      new document and ignores the extra key — and nothing else could break
      precisely *because* `name_map()` was not narrowed: the sidebar's grouping,
      its chips and its picker all keep resolving every archived id. A frozen
      surface that needed zero work is what a presentation-only change looks like

### Open questions

- [x] **Two more instances of it, fixed on 2026-09-04**, which is what makes it a
      pattern rather than a coincidence: the doc list did not redraw after a
      document was linked, because `refresh()` deliberately does not refill the
      form -- refilling would eat a half-typed glossary. `_refresh_docs` is the
      same surgical shape as `_fill_archive_state`. **`_on_rename` is still the
      one left**, and the note below is still the right description of it.
      Original note follows.
- [x] **`_on_rename` has the same staleness the archive button had**, and it is **Done on 2026-09-06**, the way the archive button was: the heading is set directly after the refresh.
      pre-existing rather than introduced here: after a rename the list row shows
      the new name and the form's heading still shows the old one, until you
      select another project and come back. Left alone deliberately — the obvious
      fix is to refill the form, which is exactly what would discard a half-typed
      glossary, so it wants the same surgical treatment `_fill_archive_state` got
      rather than a one-liner
- [ ] **The window cannot tag an archived project at all**, since the picker is
      what a person tags from and it does not offer one. `referat tag` is the
      escape hatch. Recorded rather than papered over, exactly as the picker's
      refusal to open on an unreadable `projects.json` is; the alternative is a
      *show archived* toggle in the picker, which is a filter nobody has asked
      for yet
- [ ] **`inactive` may want to be visible outside the people page.** Today it is
      one section and one footer line. If it turns out to be what somebody
      actually looks for, the dashboard is where it would go — and it would have
      to stay derived
- [ ] **Whether archived projects should sort last in `referat project list`.**
      They do not: the order is `ordered()`'s by id, and sorting them down would
      put a presentation opinion into a document the window also reads, which is
      the same reason step 19's grouping lives in the page. Watch whether the
      table becomes hard to read once there are more archived projects than live
      ones — the nearest existing thought is step 19's open *or pinning* box
- [ ] **Whether a project should be archived automatically** after some months
      with no meeting. It should not, on the rule that nothing is inferred — but
      the *suggestion* is a different thing from the act, and a queue of
      "nothing since March" would be honest. Only if the list actually gets long

## 22. Kinds of recording — talks, dictation, and whatever else (design exploration)

**Nothing here is built and nothing is scheduled.** It is an idea thought through
on 2026-09-04 and written down where the next design session will find it, from:
*"multiple types of recordings: dry runs of talks, dictated paper sections, and
maybe other voice to text situations besides meeting transcription."* It is
recorded as an exploration rather than a plan, and the boxes below are what would
have to be honoured if it is ever built, not a backlog.

Referat is meeting-shaped from end to end: one pipeline, one derived artifact,
one prompt. The question is what it costs to say something else into the same
microphone.

### What the three kinds actually want

- A **dry run of a talk.** One person, one channel, twenty minutes of rehearsal.
  The product is a cleaned speaking script — what you actually said, with the
  false starts and the *ums* gone — plus the thing notes throw away entirely:
  **timing**. Where you were at ten minutes, which section ran long, how many
  times you stopped. That makes it the first derived artifact that would use
  the timeline as *data* rather than as citations, and `meta.json` already holds
  all of it: the entry timestamps and the pause list, inter-convertible from
  audio time as the folder contract says.
- A **dictated paper section.** The same capture shape and a completely different
  product: prose fit to paste into a manuscript. No speaker labels, no bullets,
  no decisions-and-action-items, and U.S. spelling arrives already specified by
  the meetings folder's `CLAUDE.md`.
- **Whatever the third turns out to be** — a voice memo, a lecture you gave,
  spoken feedback on a student's draft. The mechanism is the point and the list
  is not, which is exactly why the mechanism must not be a branch per kind.

### The thesis: a kind selects a prompt and the file it writes, and nothing else

- [ ] **Capture does not change.** A dry run is mic-only, and the loopback is
      silent — which the pipeline already handles, because `audio_is_clean`
      counts a voiceless channel as clean by `duration_after_vad` rather than by
      amplitude, precisely so an untouched `system.wav` does not pin every
      recording to the disk. The recorder carries *recording robustness beats
      everything else*, and **it must not learn what kind of thing it is
      recording**
- [ ] **Diarization does not change either.** *The microphone is the room, not
      the user*: a second voice in a dictation is a second person, not noise, and
      a kind that skipped diarization would be `2026-08-28_1152`'s 258 lines of
      `ME` all over again. A kind may change which **queues** a recording appears
      in and what a surface offers to do with it; it may never change what the
      pipeline computes
- [ ] **The derived artifact is the whole of the difference**, and it is nearly
      free. `notes._spawn` already takes `prompt` and `expect` as parameters — the
      prompt string and the file the pass must leave behind — so `/script <id>`
      writing `script.md` and `/dictate <id>` writing `draft.md` cost one command
      file each under `templates/meetings/.claude/commands/` and one
      `generate_*` wrapper. Same cwd, same `ALLOWED_TOOLS = "Read,Write,Glob"`,
      **`Bash` still the line that does not move**
- [ ] **No prompt text moves into Python.** Both prompts live in the meetings
      folder for the same reason `/cleanup` does: they are refined by editing
      Markdown in the folder they run in, and `paths.seed_tree` never overwrites
      a seeded file. A new command file is additive and appears in the existing
      folder on the next seed
- [ ] **The vocabulary stays "meeting."** `meetings_dir`, `Meeting`,
      `meta.json`, `referat list`, and the extension's whole surface. Renaming
      spans nine modules, a TypeScript client, two config keys and a folder a
      sync client has been carrying for weeks, and buys a word. A recording is a
      meeting-shaped **record** whatever was said into it; `kind` is a field on it
- [ ] **`kind` is chosen by a human or it is `"meeting"`.** Never inferred from
      the transcript — the rule does not bend for this step any more than it bends
      for step 17. `Meeting.load` ignores unknown keys and applies defaults, so
      every meeting already on disk reads as `"meeting"` by pure default, the way
      `LEGACY_STATUS` maps with no look at the filesystem. **No migration**

### Where a kind is chosen

- [ ] **Default `meeting`, always.** The one-button start is the reason this tool
      gets used at all, and a dialog between the hotkey and the recording is the
      friction step 16 was declined over
- [ ] **Settable after the fact**, on the Meetings tab beside *Tags…*, which is
      where somebody already stands when looking a recording over. That makes it
      the fourth thing the window writes into `meta.json` and it would go through
      a guarded `cli.set_kind` like every other one — **a `run_*` that holds a
      rule is a `run_*` a second surface cannot use**, for the eighth time
- [ ] **And settable before**, for when you know: a tray menu item, or a second
      entry point that calls the same `App.start_meeting` with a defaulted
      parameter. **One state machine and one `start_meeting`**, the way three
      buttons already mean two methods rather than three paths
- [ ] `referat kind <id> <kind>` at the prompt, refusing an unknown one the way
      `tag` refuses an id no project answers to

### What each consumer downstream does with it

- [ ] **`cli.list_document`** carries `kind`, or nothing downstream can see it:
      `cli.pending` is pure over the document and opens no file, which is what
      keeps the dashboard free
- [ ] **The queues become "no derived artifact yet"** rather than "no notes yet".
      `pending`'s third queue keys on a `"notes"` boolean and `dashboard.QUEUES`
      renders it; both would read whichever file the kind expects. The **heading
      and the button label** follow the kind, and the button still navigates
      rather than acting in place
- [ ] **`index.meeting_title`** reads the H1 of `notes.md`. Either every kind's
      artifact leads with an H1 — cheap, and a script and a draft both want a
      title anyway — or that function learns the per-kind filename. A **Kind
      column** in `index.COLUMNS` is one entry and one element of `_row`
- [ ] **`actions.py` needs no change at all.** It parses whatever Markdown it is
      handed and never names a file; the coupling lives in
      `cli.actions_document`'s `read_actions(folder / paths.NOTES_MD, …)`. A
      script has no action items and a draft has none, so the parse finds nothing
      and the owner's box is unchanged — the honest degradation, not a special case
- [ ] **`/standup` must be told, or it will write "the meeting decided."** A
      dictation belongs in a day summary — *drafted section 3* is exactly a
      standup line — but the prompt globs `notes.md` and would have to glob the
      others and know which is which
- [ ] **Only `kind == "meeting"` participates in step 13's digests.** *Nothing
      but notes leaves the machine* was never a licence to push a draft paper
      section into a shared project doc. This is the line somebody crosses by
      accident, and it is worth writing into the digest step before the digest
      step exists
- [ ] **The lifecycle keeps `notes_written`** and it means *the derived artifact
      exists*. Renaming a status value costs `LEGACY_STATUS` a third entry, every
      surface that renders the word, and the `referat state <id> notes-written`
      verb, to say a truer sentence in one file. Rename it in prose, not in data

### Rules this may not break

- [ ] **The transcript stays immutable, whatever the kind.** A dictated section
      is corrected in the draft and a talk in the script, never at the source.
      That rule gets *harder* here, not easier: the temptation to fix a
      transcript is proportional to how much the words are the product
- [ ] **A pass may not touch `transcript.md`, `meta.json` or any WAV**, may not
      read `.voices/`, and gets no `Bash`. Every one of those is a property of
      running Claude Code in that folder rather than of what is being written,
      which is why `notes.py` runs both existing prompts through one `_spawn`
- [ ] **The draft is the thing you ship, and that is a new kind of danger.** A
      pass that "improves" a dictated sentence is putting words into a paper
      under somebody's name. It may reflow, punctuate, drop disfluencies, join
      sentences and correct transcription errors against the glossary; it may
      **not** add a claim, a citation, a hedge, or a transition that carries an
      argument. Where it cannot parse what was dictated it leaves a marker rather
      than smoothing over it. This is *a wrong name is worse than no name*
      applied to prose, and it is the sentence somebody will soften

### Conflicts found while thinking it through

- [ ] **`notes._spawn` already has a parameter called `kind`**, and it means the
      progress namespace — `progress.NOTES`, `progress.DAY`. A recording kind
      needs a different word or the two collide in the one module that would hold
      both. The notes queue's `(kind, target)` pairs and the window worker's
      dispatch on that kind are the same namespace
- [ ] **The kind that most wants its audio is the kind that cannot be written
      up.** You would want a dry run's WAV to hear your own delivery, but
      `[transcription].keep_audio` leaves a meeting **in staging**;
      `transcribe.promote_meeting` refuses any folder holding a WAV, and enforces
      it there rather than at the call site on purpose; the passes run with cwd
      at the meetings folder and cannot reach staging; and `cli.pending` skips
      `staged` for exactly that reason. So kept audio and a derived artifact are
      mutually exclusive today. **Unresolved.** The cheap answers are all bad:
      keeping the audio somewhere else is a second place a recording lives, and
      relaxing the WAV rule is the one invariant the whole staging split exists
      for
- [ ] **A kind is the one thing known *before* transcription**, which reopens a
      question step 12b closed. Hotwords are one global list because *a meeting is
      tagged after it has been transcribed, so at transcription time there is
      nothing to select on* — and a kind chosen at `Meeting.create` time is
      something to select on. A per-kind list would be one more `take(…)` in
      `hotwords.collect` with its own source label. **But only if the kind is set
      before the recording**, and this step wants it settable after as well; a
      list keyed on a field that may still change cannot be the one Whisper was
      handed. Open, and dangerous to half-answer
- [ ] **The document schema assumes one artifact per meeting.** `list_document`'s
      `"notes"` boolean, `show_document`'s `files`, `pending`'s third queue and
      `actions_document`'s path all name it. Widening that is the largest single
      edit in this whole idea, and it is a rename of a field in a document with
      two readers while the extension lives

### Open questions

- [ ] **How many kinds are real?** Two are asked for. If the third never arrives,
      this is a `str` field with three values and a dict of prompts; if six
      arrive, it is a registry, and a registry is a plugin point this project's
      conventions refuse. Build it as the `str` and find out
- [ ] **Does a dry run want a `notes.md` as well?** A rehearsal produces both a
      script and a list of things to fix, and one artifact per meeting is an
      assumption rather than a finding
- [ ] **Is a talk even a meeting folder?** A talk is rehearsed four times, and
      four folders with four scripts is worse than one thing with four takes.
      That is the one argument in this whole idea for a different container, and
      it should be resisted until it is felt
- [ ] **Does the viewer grow a third pane, or does the tab bar become per-kind?**
      The viewer opens on the notes because the transcript is evidence; a
      dictation would open on the draft by the same argument
- [ ] **What does `referat label` do with a dictation?** Nothing changes
      mechanically, but a one-cluster mic is exactly `bootstrap_owner`'s gate —
      so **a dictation is the best owner-voiceprint source this tool will ever
      have**, and that is a small argument in favour of the whole idea

### Build order, if it is ever built

- [ ] **One kind at a time, driven against real recordings before the next.**
      `/cleanup` has met a handful of transcripts and is still being tuned; three
      untuned prompts would be three ways to read authoritative while being
      wrong, which is the failure this codebase keeps deleting
- [ ] **Whichever kind is done weekly goes first.** The one that is done twice a
      year will be tuned against two recordings forever
- [ ] **Keep it out of `README.md`'s roadmap** until a kind is actually built,
      for step 17's reason: it is an experiment, and the roadmap is a promise

## 23. Parity, and the VS Code extension is deleted

**Planned and built on 2026-09-04**, from: *"at this point, I am comfortable
deleting the VS Code extension. If we do, it should be uninstalled from my VS
Code gracefully as well."* Step 20 put the extension into maintenance with one
condition written down in three files — *deleted once the command center reaches
parity with it* — so this step is that condition being met and then acted on. It
is two halves and the order between them is the whole discipline: **close the
gap first, delete second.** Deleting while something the sidebar could do was
still missing would have been a feature removal dressed as a cleanup.

### What parity actually meant, audited rather than assumed

The sidebar's row had seven actions and the window had five equivalents. The
audit was `sidebar.ts`'s own `runAction` switch, which is the whole list there
has ever been:

- [x] `openTranscript` / `openNotes` — the viewer, since phase 1. It shows both
      documents side by side rather than opening two editors, which is more than
      parity
- [x] `generateNotes` — *Generate notes…*, 2026-09-03
- [x] `tags` — the tag picker, phase 2
- [x] `delete` — *Delete…*, 2026-09-03
- [x] Speaker labeling, which the sidebar did inside an expanding row — the
      *Speakers…* dialog, phase 3
- [x] The status bar item — the recorder buttons and the activity strip, above
      the tabs and permanent, since phase 1
- [x] *Referat > Show Output* — the Activity tab's log pane, phase 6
- [x] `referat.openMeetingsFolder` — already on the tray menu
- [x] **`reTranscribe` — missing.** Built here
- [x] **`accept`, the gate-failed off-ramp — missing.** Built here as
      *Promote…*

Two gaps out of ten, and both about the **audio** rather than about a transcript.
That is not a coincidence and it is why they were the last two: everything else
in this window acts on `transcript.md`, `notes.md` or `meta.json`, and these two
act on the WAVs — the one part of a meeting that cannot be regenerated, and the
one the whole staging split exists for.

### `Re-transcribe…`

- [x] **`rerun.check` is the split, and where the line falls is the point.** It
      holds the three questions about the *meeting* — it exists, its `meta.json`
      reads, its audio is on disk — and `run` keeps the one question about
      another *process*, `busy_tray`. That guard exists because
      `transcribe._RUN_LOCK` serializes jobs inside one process and cannot see
      across one, and two large-v3 models do not fit on this card; the window is
      *inside* the tray, where the lock does its own serializing, so asking it
      there would refuse the one caller it was never about
- [x] **The work runs on the tray's thread, through `App.rerun_meeting`.** Not a
      `cli` function, because what a rerun costs is the state machine, the job
      count and a daemon thread, and a window may touch none of the three. The
      extension opened a *terminal*, which was right for a different process and
      would be absurd from a window that owns the GPU job
- [x] **`App._queue_transcription` is the dedup that fell out of it.** Count the
      job, `try_to(TRANSCRIBING)`, start the thread — the same three lines in
      `_finish_meeting`, in `_resume` and now here, and `try_to` rather than `to`
      is exactly what makes the three interchangeable
- [x] **Snippets cleared before the pipeline, `speaker_names` not.** Unchanged
      from `referat rerun`, and unchanged for the reason 2026-09-02 recorded: the
      map describes the `transcript.md` still on disk until this run commits
- [x] **A confirmation, and it is not ceremony.** A rerun renumbers and replaces
      `speaker_names` wholesale on success, so a voice whose print no longer
      clears the threshold comes back a number. Naming that before it is paid for
      is the same rule as every other modal here
- [x] Nothing is awaited. Progress goes to `referat.progress` — the status bar
      strip and the Activity tab — and the verdict to `App.notify`, which are the
      two places a hotkey-stopped recording already reports through

### `Promote…`

- [x] **`cli.promote_meeting` is the ninth guarded function**, and it was
      `run_promote`'s entire body. *A `run_*` that holds a rule is a `run_*` a
      second surface cannot use*, for the ninth time; the shape has been settled
      since the seventh and this was simply the last one anybody needed
- [x] **`release_audio` stays a direction on one function rather than two**, as
      `set_archived` and `apply_tags` already are. The bare form is the same
      operation with the deletion refused, and two copies would be two guards
      that could drift
- [x] **`cli.promote_warning` is `delete_warning`'s counterpart**, and the one
      text the prompt's refusal and the modal both speak in. What it has to say
      that `delete_warning` does not: this is irreversible in a way deleting a
      whole meeting is *not*. Deleting takes the transcript with the audio; this
      **keeps** the transcript and destroys the only material it could ever be
      re-derived from — and it is normally pressed on exactly the meeting whose
      transcript the gate was not confident in
- [x] It splits three ways on what it finds: a failed gate is told promoting
      *accepts* the transcript, an audio kept by `[transcription].keep_audio` is
      told a rerun would simply keep it again, and a staged folder with no audio
      left is told this is only the retry for a move that failed
- [x] **Labelled `Promote...` and not `Accept`.** The CLI verb is `promote`, this
      codebase spends its vocabulary once, and the modal is where the meaning is.
      Enabled on `staged` rather than on `audio == "kept"`, which is the wider of
      the two and covers the failed move as well

### Where the two buttons went

- [x] **First in the action row, before `Tags…`**, because they come first in a
      meeting's life: everything else on that row acts on a transcript and these
      two act on the audio it was made from
- [x] **Disabled for almost every meeting, and that is honest rather than
      untidy.** The audio is normally gone; a meeting that still has it is a
      meeting waiting on exactly one of these two decisions. Disabled rather than
      hidden, for the reason the three recording buttons are: a button that moves
      is a button you have to look for
- [x] Two glyphs in `icons.py`, `_redo` and `_promote`. A circuit and
      deliberately not a triangle — `_play` already means *hear this second of
      audio* two tabs away, and the two must not look alike where one costs a
      second and the other costs two minutes of GPU. Both filled, like the rest;
      an arc in a set drawn with a brush and no pen is a pie with its middle
      punched out

### Deleting it

- [x] `code --uninstall-extension niklas-elmqvist.referat-vscode`, **before**
      removing the source, so VS Code did the deregistration itself rather than
      being left with a manifest pointing at nothing. It was gone from
      `extensions.json` immediately; the folder under `~\.vscode\extensions`
      lingered because the running editor held it, and was removed after
- [x] `referat-vscode/` deleted, tracked files and all
- [x] `.vscode/launch.json` and `.vscode/tasks.json` deleted with it. They
      existed only to run the extension from source under F5, and the tasks file
      in particular carried two comments about problem-matcher footguns that are
      now about nothing
- [x] `.gitignore`: `node_modules/`, `out/` and `*.vsix` **stay**. A `.vsix`
      appearing in this repository now would be somebody starting it again, and
      the point of deleting it was that there is one UI. The `.vscode` negations
      drop to `settings.json` alone
- [x] **No settings to clean.** `referat.repoRoot` and `referat.claudeBinary`
      were never set in user or workspace settings — checked, not assumed — and
      the extension deliberately never had a meetings-folder setting

### The prose, which was most of the work

- [x] **Every present-tense claim about the extension is now false**, and this
      codebase's own recorded failure mode is a document describing something the
      code does not do. Swept `referat/`, `README.md`, `SETUP.md` and `INDEX.md`:
      a claim about a live reader was rewritten, a *lesson* was kept and put in
      the past tense
- [x] The three argparse help strings that said "for the VS Code extension" —
      user-visible text naming a thing that does not exist
- [x] `SETUP.md` section 12 replaced rather than deleted: it is now **the command
      center**, with a short note at the end saying the extension was there and
      how to uninstall a copy still installed on some other machine
- [x] `INDEX.md` keeps a *Deleted* section rather than merely dropping the table.
      The map is also a record of what this repository has held, and three things
      the extension taught outlived it and are cited where they landed: build
      every node with `textContent`; a picker diffs against what the meeting
      carries and never against the set it mutates; and a refusal reaches the
      user in the words of whatever owns the rule

### What stays, deliberately

- [x] **The `--json` documents all stay**, every one of which was written for a
      reader that no longer exists. They are the shape a surface and the CLI
      agree on, a document you can print is a document you can test, and they are
      now what anything scripting this from outside Python reads. Deleting them
      would have been the actual mistake this step could have made
- [x] `notes.resolve_claude` still reads `~/.vscode/extensions` and still honours
      `.obsolete`. That is the **Claude Code** extension and has nothing to do
      with this one; the two are easy to confuse in a sweep and were kept apart
- [x] `label`'s `--speaker`/`--name`/`--forget --yes`/`--json` flags stay. They
      were built so a caller with no terminal could name somebody, the command
      center reaches `label.name_speaker` directly and needs none of them, and
      anything driving this from a script still does

### Left open

- [ ] **Neither new button has been driven against a real staged meeting.**
      There is none on this machine right now — every meeting is `released` and
      promoted — so *Promote…* has been exercised only through
      `promote_warning`'s no-audio branch, and *Re-transcribe…* not at all
      end-to-end. The next gate-failed meeting is the test, and until then this is
      code that type-checks and renders rather than code that has worked
- [ ] **`rerun_meeting` while the tray is recording.** `try_to` refuses
      `RECORDING -> TRANSCRIBING` and the job still runs, which is what `_resume`
      has always relied on — but it means the machine says `recording` while a
      GPU job runs, and `status.json` says so too. True and possibly confusing;
      it was true before this step and is now reachable by a button
- [ ] **A rerun from the window cannot be cancelled**, which is the standing
      *no way to abandon a running transcription* item one button closer to
      somebody's hand

## 24. Project recaps

**Planned and built on 2026-09-08.** It went
ahead of 17 and 22, which are experiments and keep their deferred status — said
here in words, since the file's order is numeric and never resequenced. Built the
same day, to this plan, with the open question below settled and the bundle put
beside the recap rather than in the system temp folder — see `CHANGELOG.md`. From:
*"Before a recurring meeting, produce a very short brief for one project: where
it stands and what needs discussing. Consumed once, at the desk, minutes before
the meeting."* It is a glance in the sense the day summary is, over the other
axis — one project across many meetings rather than one day across many
projects — and it is read rather than kept, which is what decides its length.

Two things the request said were checked against the plan before being written
in, and neither survived as asked. It named a *Recap* action on each project in
the VS Code extension's TreeView, with `--json` for the extension to read; the
extension was deleted at step 23, and the surface that exists is the command
center's projects page, which already puts *Link doc…*, *Unlink* and *Sync now*
onto guarded `cli` functions in process. And it asked that sectioned notes be
read by their `## Project: [TAG]` section and `## General` only, using the same
partition extraction the Google Docs layer uses; there is no such extraction —
sectioned notes are step 17, which is unbuilt and specifies no section headings,
and the digest pushes a whole `notes.md`. So the bundle is whole notes, and the
section rule is recorded below as conditional on 17, which this step does not
wait for.

### The artifact

- [x] **One file per project, at `<meetings_dir>/recaps/<project-id>.md`.** The
      request proposed `projects/<tag>/recap.md`; that is a per-project directory
      tree nothing else uses, beside a file called `projects.json`, and the
      layout already has the shape this needs — `days/<date>.md`, one flat
      generated folder with one Markdown file per thing. The id rather than the
      name, because a rename never moves an id. **Never inside a meeting
      folder**, and excluded by name from `paths.list_meeting_dirs` exactly as
      `days/` is, though it could not pass the `meta.json` gate either — a rule
      belongs where somebody would break it
- [x] **Two sections and no more.** `## State`: one short paragraph — where the
      project stands, the decisions already made, what was concluded most
      recently. `## Open`: a brief bullet list of what needs discussing —
      questions left unresolved, actions assigned but not reported back,
      decisions explicitly postponed to a later meeting. A **hard length cap on
      both**, stated in the prompt, because a recap that has to be scrolled has
      stopped being one
- [x] **YAML frontmatter, three keys**: `project` (the id), `generated` (an ISO
      timestamp) and `meetings` (the ordered list of meeting folder ids folded
      in). The third is the watermark the staleness rule and the deferred
      folding item both read
- [x] **A recap is stale** whenever a meeting tagged with the project has a
      `notes.md` newer than `generated`, or is tagged with the project and absent
      from `meetings`. Derived on every read and stored nowhere — no `meta.json`
      key, no lifecycle state, nothing a surface could infer from which files
      exist. **A stale recap is still shown, with the stale state visible, and
      is never deleted**: it is the last brief anybody wrote, and the button
      that rewrites it is beside it

### Generation

- [x] **Regenerated from scratch every time**, from every `notes.md` of every
      meeting carrying the tag, in chronological order. The previous `recap.md`
      is never folded into the new one — see the deferred item below, which is
      exactly that and exactly why not yet
- [x] **The prompt says two things about time.** Later meetings override earlier
      ones; and an item raised in one meeting and resolved in a later one
      belongs in State, not Open. Without the second, Open is a list of every
      question ever asked
- [x] **Which meetings, in what order, and when, are Referat's to say and never
      the prompt's.** `tags` live in `meta.json`, which nothing in that folder
      may read — the division `/standup` already runs under. So the bundle's
      header carries `project`, `generated` and the ordered `meetings`, and
      `/recap` copies them into the frontmatter rather than working them out
- [x] **If step 17 is ever built, section extraction has one implementation.**
      A sectioned `notes.md` is read for the project's `## Project: [TAG]`
      section and `## General` and nothing else, through the same function the
      digest layer partitions with — never a second reading of the same
      headings. Until 17 exists the bundle is each whole `notes.md`, which is
      what the digest sends today, and nothing here waits for it
- [x] **Every Open item cites the meeting that raised it**, as `[<meeting-id>]`
      at the end of the line — added in planning rather than asked for, and
      worth taking from the start: `/standup` already writes citations in that
      form, the dashboard already turns one into a link, and the deferred
      folding item needs them as its only defence against an item lingering

### Mechanics, on the one-implementation rule

- [x] **`referat recap <project-id>`** assembles the ordered bundle of notes
      into a temporary file, spawns `/recap` on it through `notes._spawn` — same
      cwd at the meetings folder, same three allowed tools, `Bash` still the
      line that does not move — and reports the meeting ids included. The
      request's CLI half stopped at the bundle and left the spawn to the
      extension; `referat day` already does both from one verb, and this
      follows it. `--dry-run` assembles and reports without spending a
      subprocess; `--json` prints the document, on step 23's rule that a
      document you can print is one you can test, and it is what anything
      scripting this from outside Python reads
- [x] **`referat/recap.py`** holds the bundle and the staleness predicate,
      pure over a `list_document` where it can be; `cli.write_recap` is the
      guarded operation in the shape of `cli.write_day_summary`, and
      `cli.recap_document` is what a surface renders — the recap's text, its
      frontmatter, and whether and why it is stale. A `progress` kind of its
      own, joining the notes queue's one worker: one rate limit, one folder
- [x] **`/recap <project-id>`** is a versioned slash command in the meetings
      folder beside `/cleanup` and `/standup`, seeded from the template and
      never overwritten, with `allowed-tools: Read, Write, Glob`. It reads the
      bundle, writes `recaps/<project-id>.md` and touches nothing else. The
      `.voices/` deny rule is the folder's own `.claude/settings.json` and binds
      every pass run there, so it inherits the rule with no change
- [x] **The projects page grows *Recap…***, beside *Sync now*, with the stale
      state shown next to it — the request's TreeView action, translated. A
      button onto `cli.write_recap`, on a thread, reporting through the same
      queued signal the doc jobs use, and the widget holds no rule: what is
      stale and which meetings are in is decided in Python and arrives decided.
      The written recap is rendered on the page, as the day summary is on the
      dashboard, rather than opened in an editor — a `[<meeting-id>]` citation
      becoming a link the same way
- [x] **The meetings folder's own `CLAUDE.md` and `INDEX.md` grow a `recaps/`
      section when this is built**, not before — a seeded prompt describing a
      command that does not exist is the step 16 mistake one folder over
- [x] **No automatic pass.** A recap is something somebody presses, as
      `/cleanup` and `/standup` are, and is never rebuilt on its own; staleness
      is what says it is time. **Nothing in `recaps/` leaves the machine** — it
      is derived from notes but it is not notes, and the digest push does not
      carry it

### Rejected while deciding this

- [x] **Store the recap in the most recent meeting folder and walk backward
      through the series to find it.** Rejected: the recap is a property of the
      project, not of a meeting; a meeting tagged with several projects would
      hold one recap per tag; re-transcribing that meeting would orphan a recap
      that depends on earlier ones; and the backward walk exists only to
      compensate for the placement

### Deferred, and deliberately not scheduled: incremental recap folding

- [ ] **When a project's notes no longer fit comfortably in one pass**, switch
      to folding the previous `recap.md` plus the notes newer than its
      `meetings` watermark into a new recap, rather than every note from the
      start. Known risk: resolved items linger and errors compound, because
      nothing grants the model permission to drop things it was handed as
      settled. Mitigate by keeping `## State` short and by requiring each Open
      item to cite the meeting that raised it, so a lingering item at least
      says how old it is. **Do not build until regeneration from scratch
      actually becomes slow** — the from-scratch pass is the one whose output
      can be checked against its inputs

### Open questions

- [x] **Newer by mtime, or changed by sha?** The rule as specified compares
      `notes.md`'s modification time against `generated`; a sync client
      rewrites mtimes, and the digest already answers *did this note change*
      with `notes_sha256`. A sha per id in the frontmatter would make the rule
      the same one step 13 uses. Settle at build, and record which.
      **Settled at build: sha, never mtime.** The frontmatter has a fourth key,
      `notes`, a sha per folded-in meeting, and `generated` is for a person to
      read and is compared against nothing. A stored sha may be a prefix of the
      real one, since the prompt copies it by hand and a truncated value is
      still an answer. `referat/recap.py`'s docstring records the reasoning

## Surfaced later
### Twelve minutes lost to a repetition loop (2026-09-04)

Found in `2026-09-04_1001` immediately after the Swedish support landed, and
found *only* because the quality gate refused the transcript.

- [x] **`condition_on_previous_text = False`.** Each window was decoded with the
      previous window's text as context, so 26 lines of `Ljusen.` were handed
      forward, found plausible, and became *Tack för att du har tittat på den här
      videon!* once every thirty seconds from 00:12:37 to 00:24:50. A repetition
      loop is a bad window **feeding itself**, and this flag is the feed
- [x] **Verified rather than assumed**: the same stretch decoded in isolation,
      with no poisoned context, gives 2128 words of ordinary conversation
- [x] **It was not silence.** -33.8 dBFS across the lost stretch against -34.0
      for the rest of the meeting — two people talking, replaced by one sentence
- [x] **Not a knob.** There is no meeting for which the loop is the better
      outcome. The coherence given up is not rendered anywhere here (diarized,
      per-segment, merged, read beside timestamps) and the vocabulary priming is
      already bought by the hotword list, on every window rather than only after
      somebody has said the word
- [ ] **The gate is the backstop, and it caught this one by luck of magnitude.**
      `compression_ratio` is the max over segments against a 2.4 ceiling, so a
      loop long enough to matter trips it — but a *short* loop, or a hallucination
      that is fluent rather than repetitive, trips nothing. There is no check that
      compares transcribed speech time against `voiced_seconds`, which is the
      measurement that would have caught this one in one line: 1874s of segments
      over 1869s of voiced audio looked perfect while a third of it was one
      sentence. **Worth adding — a channel whose unique-text share is far below
      its voiced share is the general form of this bug**
- [x] **Re-examine the earlier Swedish-free meetings for the same shape.** This **Re-examined on 2026-09-06**: no sentence-length loop in any English transcript. What there is are runs of `yeah` up to 26 in a row (`2026-09-03_1316` at 00:06:28) and of `bye` up to 14, which text alone cannot tell from real backchannels; a check would need the audio, which is gone. Left as a note.
      is a Swedish-flavored hallucination but the mechanism is language-neutral,
      and nothing has ever looked for it in the English transcripts already
      promoted. `referat transcript <id>` prints who spoke and how often; a
      repeated-line count beside it would answer this for the whole folder

### Meetings are not all in English (2026-09-04)

`2026-09-04_1001` is a Swedish meeting with Hugo, and
`[transcription].language = "en"` had been pinning every channel to English
since step 1. Asked about once before and deferred; the recording is what
made it concrete.

- [x] **One key, `[transcription].languages`, replacing `language`.** Three
      states and each is real: one code pins and asks Whisper nothing (what this
      was, and it still costs no encoder pass), two or more detect per channel
      and take the best *of these*, `[]` is bare autodetect. The old key is not
      kept as a fallback — `_build` warns on an unknown key and moves on, so a
      config still carrying `language` reverts to the default, which is the same
      behaviour it was asking for
- [x] **The restriction is applied to the ranking, not to the decoder.** There is
      no faster-whisper argument for "one of these"; `language` takes one code.
      So `detect_language` runs the pass for its `all_language_probs`, reads the
      allowed codes out of it, and hands the winner back as a pin. Whisper is
      never told Norwegian was a candidate
- [x] **This is why the middle state is not the empty one.** Swedish sits among
      Norwegian, Danish, German and Dutch. Verified against the stub: a channel
      scoring `no` at 0.90 and `sv` at 0.05 is still transcribed as Swedish,
      because `no` is not on the list. Unrestricted, that channel is lost whole —
      a language is chosen **once** and every segment decoded under it
- [x] **Five windows spread across the channel, not faster-whisper's one at the
      front.** The first thirty seconds are somebody sitting down and a laptop
      finding its microphone, and on an in-person loopback they are silence. Four
      extra encoder passes, under a second. On `2026-09-04_1001`'s mic: **sv 0.98
      against en 0.01**
- [x] **Per channel and never per segment.** The mic is the room and the loopback
      is the far end, and a Swedish room on an English call is the meeting this
      exists for. `multilingual=True` re-detects on every segment, which turns
      one wrong guess per channel into one anywhere
- [x] **Every failure degrades to unrestricted autodetect**, the rule diarization
      already runs under — a language Whisper picks unaided is a worse guess than
      a restricted one and an immeasurably better one than a traceback
- [x] **Unknown codes are dropped with a warning.** Swedish is `sv` and not `se`,
      the file is hand-written, and a typo that quietly narrowed the set to
      nothing would come back as bare autodetect: the failure this prevents,
      wearing the face of the fix. Membership is checked in `transcribe.py`
      against the loaded model and not in `config.py`, which must not import
      faster-whisper — `referat config` runs in the base install
- [ ] **The hotword list is still one list for one language.** Every name in it
      is fed to a Swedish channel as readily as to an English one, which is
      probably harmless for names and unexamined for the glossaries. Worth a look
      the first time a Swedish transcript comes back with an English term spelled
      into it
- [x] **The notes are English whatever the meeting was in.** The house style said
      U.S. *spelling*, which is a rule about how to write English and not about
      which language to write; nothing said what to do with a Swedish transcript,
      so each pass would have guessed. Now in the meetings folder's `CLAUDE.md`
      and in the `/cleanup` prompt, in both the repo template and the live copy —
      seeded files are never overwritten, so those two are kept in step by hand.
      It stops where the spelling rule stops: never `transcript.md`, never a name,
      and never inside a quotation, which keeps its own language with an English
      gloss after it where the point turns on the wording
- [x] **No Swedish meeting has been through `/cleanup` yet.** The rule is written;
      whether it survives contact is unmeasured, and the thing to read for is a
      note that has quietly translated a quotation or an institution's name.
      **Done on 2026-09-04**: transcript and notes both read fine
- [ ] **A per-channel language belongs in `meta.json`'s `transcription` block.**
      `ChannelTranscript.language` already records what was used and the quality
      block already carries it; nothing renders it. It is the one field that would
      let somebody see at a glance that a channel was decoded as the wrong
      language, which is the failure mode with no other symptom — the transcript
      is fluent, confident and wrong

### The Speakers dialog grew taller than the screen (2026-09-04)

Reported from the real gallery, once enough projects existed that a scoped list
was itself long: the dialog opened taller than the display, and **the name field
at the bottom could not be reached** — the one control the dialog exists for.

- [x] **The chips live in a `QScrollArea` and take the slack.** `FlowLayout`
      answers `heightForWidth`, which is what makes it wrap at all; the cost is
      that forty names are forty names' worth of *minimum* height, and a minimum
      propagates up into the dialog, which then cannot be made smaller. Measured
      on a synthetic 60-name gallery: `minimumSizeHint` was **1770 px** and is
      now **312 px**, constant however long the gallery gets. The wrapping is
      unchanged and `referat/ui/flow.py` is untouched — this is the layout above
      it deciding how much room the wrapping gets
- [x] **The lines label is hidden rather than emptied.** It says anything at all
      only for a speaker whose snippets are gone, so for every other speaker it
      was an empty widget holding spacing away from the gallery. Its stretch went
      to the chips, which is the thing that grows
- [x] **Horizontal scrolling is off.** Chips wrap, so a horizontal bar could only
      ever mean the wrapping had failed rather than that there was more to see
- [ ] **The same shape is worth checking wherever a list is unbounded.**
      `flow.wrapping` has exactly one caller today. The general form is that a
      widget with `heightForWidth` inside a dialog sets that dialog's floor, and
      a floor taller than the screen is unreachable rather than merely ugly

### A leaked large-v3, and jobs that outlive a lid (2026-09-03)

Diagnosed from `2026-09-03_1459`, whose mic diarization took **6527.8s against
58.5s** for the previous meeting of the same length.

- [x] **`transcribe.unload_model`**: hand CTranslate2's weights back before the
      `del`, through CTranslate2's own API. `gpu.release` returns *torch's* arena
      and never sees them; an idle tray was measured on **5205 MiB of 12227**
      with the log saying `released 0 MiB` all along
- [x] **Both docstrings corrected.** `gpu.py` claimed the weights "come back when
      the `WhisperModel` is destroyed, which `del model` already does", and that
      the skipped collection cost "the cycles and nothing else". Both withdrawn
- [ ] **Why the model was retained is still unknown, and the first answer was
      wrong.** The reference-cycle story — `del` not destroying it because
      `gpu.release` skips `gc.collect()` off the main thread — was written up
      confidently and then falsified: in a standalone process `del` alone returns
      the full 3788 MiB with the generator drained and cyclic GC disabled, the
      floor is flat over three jobs, and flat for pyannote on a worker thread.
      So the difference is something about the tray — Qt in the process, that
      skip, or neither. `unload_model` makes it stop mattering, which is why this
      is an open question rather than an open bug. **The way to settle it is a
      measurement inside the tray**, not more reasoning: log
      `torch.cuda.mem_get_info()` at the end of each job and watch the floor over
      three or four real meetings
- [x] **The hold covers every state but idle**, transcription included. This
      section's own `- [x] Transcription runs without the hold` above is
      **superseded** and left in place: it was right about the cost and never
      priced the hazard
- [x] **`SleepBlocker.want(reason, hold)`**, because the hold has two owners now
      — the recorder and the notes queue. A single boolean let whichever finished
      first drop it out from under the other
- [x] **`power.SuspendWatcher`**: detect a suspend from the wall clock, log how
      much time was lost and what was in flight, and heartbeat every five minutes
      while a job runs. It cannot prevent a suspend and says so
- [x] **`meeting.reconcile_interrupted`**: clamp a meeting left `transcribing` by
      a process that is gone. Closes the gap `CLAUDE.md` recorded as unsolvable
      from inside a process, from the other end — the next start, not a handler
- [ ] **`busy_tray` sees a tray and not a CLI `rerun`.** A rerun in a terminal
      writes no `status.json`, so a tray starting during one would read that
      meeting's `transcribing` as residue and clamp it. Self-healing — the rerun
      writes the real status when it finishes — and narrow, since the
      single-instance mutex already means the only other writer is a CLI. Worth
      knowing before somebody widens the guard and assumes it is airtight
- [x] **Cut the peak, not just the residue.** One model served both channels, so
      Whisper was resident through each channel's diarization — 3788 MiB beside
      pyannote's 3866, before the CUDA contexts and before the 1761 MiB the
      desktop compositor was measured holding. `transcribe_channel` is now the
      Whisper pass alone and returns its decoded audio; `transcribe_channels`
      loads a model per channel and diarizes with the card clear. **Measured end
      to end on real speech: peak 4210 MiB against 7654**, both channels still
      diarized, numbering still running across the meeting (mic `SPEAKER_01`,
      system `SPEAKER_02`). Costs one extra nine-second load per meeting, and
      reverses the function's stated "load one model and run both channels
      through it"
- [x] **Interrupted transcriptions are re-queued at startup**, not just
      reconciled — only those that still have their audio, which is also what
      keeps it from looping. `State.IDLE -> State.TRANSCRIBING` is the new edge
      and the only way into TRANSCRIBING that did not just come off a recording
- [ ] **No way to abandon a running transcription.** The 2026-09-03 job ran 109
      minutes and the only way to stop it was killing the tray. Offered and not
      taken for now; the reconcile-and-resume pair makes a kill survivable, which
      was the sharp end of it. A Stop control on the activity tab is the shape if
      it is wanted
- [ ] **The idle floor is still ~5 GB and that is the CUDA context plus whatever
      pyannote's allocator keeps.** `unload_model` returns the Whisper weights;
      nothing returns the context short of ending the process. Worth measuring
      once after a few jobs to confirm the floor is now flat rather than climbing
      — the failure this fixes is *accumulation*, and only a second measurement
      proves it stopped
- [ ] **Nothing yet fails a job that has obviously wedged.** 109 minutes for a
      58-second task ran to completion; a `rerun` on a fresh process would have
      taken ten. A ceiling — diarization taking more than N times the audio
      length — would turn that into a failure somebody can retry, but N is a
      guess until there are more measurements, and a wrong ceiling costs a
      transcript on a genuinely slow machine. Recorded, not built
- [ ] **`ES_SYSTEM_REQUIRED` does not stop a lid close**, so a job can still be
      suspended mid-CUDA. `SuspendWatcher` makes that visible and
      `reconcile_interrupted` makes it recoverable, but neither makes it survive.
      Whether a CUDA context here actually survives Modern Standby is unmeasured
      — this run suggests it does, having finished after one
- [ ] **`powercfg /requests` still unverified** for the new reasons, for the same
      reason as the original entry in this section: it needs an elevated shell


### Building the command center (2026-09-02)

- [ ] **The picker refuses to open at all when `projects.json` will not parse**,
      in the sentence `referat tag` uses, because with no names loaded every tag
      would render as an orphan. The cost is that a pure *removal* — which
      `referat untag` still allows, deliberately, so a broken file can never pin
      an orphan to a meeting — cannot be done from the window. The alternative is
      opening in orphan-only mode. Recorded rather than papered over; revisit if
      it is ever actually hit
- [ ] **The window's picker structurally answers the extension's open "create
      entry has to be ticked" box.** There, typing a name and pressing Enter
      applied the existing ticks and silently created nothing, because the create
      row was focused rather than chosen. Here Create is its own button with its
      own Enter target, and both button-box buttons have `setAutoDefault(False)`
      so Enter in the field cannot also fire Apply. The extension's stays as it is
- [x] **`refresh()` re-selecting the same row re-reads the transcript and resets **Done on 2026-09-06**, in the viewer rather than the guard: `Viewer.show_meeting` keeps both panes' scroll when the same meeting id is redrawn, so F5 still re-reads and the pane stays put.
      the viewer's scroll.** Visible after tagging: the pane jumps to the top. A
      one-line guard in `_on_row_changed` would fix it and would also change what
      F5 means, so it is surfaced rather than fixed
- [ ] **The Create button is disabled on an empty name**, which encodes
      `name_complaint`'s first rule in the UI. Presentation — not offering a
      button that will fail — and Python still refuses if it is ever reached, as
      the other three rules demonstrate. Watch that it stays the only one

- [ ] **The window rescans both meeting roots on every transition it is visible
      for**, through `cli.list_document`, which also calls
      `voices.unknown_speakers` and `index.meeting_title` per meeting. Five
      meetings is nothing; a year of them on the GUI thread is a stutter every
      time the recorder changes state. The guard that it only refreshes while
      visible is doing most of the work today. Fix it when it is felt, and the
      obvious fix — caching by folder mtime — is a cache that has to be right
      about `meta.json` being rewritten several times a meeting
- [ ] **`build_info`'s reason for not checking its own staleness has gone.** The
      obstacle was `pystray` building the Win32 menu in `update_menu()`, so
      callable menu text was evaluated on a *transition* and an idle tray would
      have kept reporting itself fresh. Qt emits `aboutToShow` and has none of
      that, so the tray could do the comparison itself on menu open. The
      docstring says so instead of keeping the dead argument; whether to build
      it is a separate question, and `referat status` still answers it correctly
- [ ] **`pystray` and `pillow` are out of `pyproject.toml` and still in the
      venv**, because `pip install -e . --no-deps` removes nothing and pillow is
      a matplotlib dependency under pyannote anyway. Nothing imports either.
      Harmless, and worth knowing before somebody reads the venv as the
      dependency list — the file is
- [x] **Two graphical surfaces now, and the new documents have one reader each.** **Dropped on 2026-09-06: the extension was deleted at step 23.**
      `show --json` and `transcript --json` are read only by the window; `list
      --json`, `status --json`, `label --json` and `project list --json` are read
      by both. A change to one of those four has two readers until the extension
      goes, and the extension is the one that fails silently
- [ ] **A `QMenu` held only by the C++ side of the tray icon gets collected.**
      `setContextMenu` does not take ownership from Python's point of view, so
      `Shell.menu` keeps the reference. Written down because the failure is a
      tray icon whose right-click does nothing, which reads as a Qt bug

### The tag picker, first time it was used (2026-09-01)

- [x] **A project created inside the picker was never applied.** `editTags` used
      one set as both the picker's tick state and the diff baseline, and the
      create path added the new id to it — so the new project was in neither
      `added` nor `removed` and `referat tag` was never called for it. Two sets
      now: `carried`, the meeting's own tags, which is the only baseline; and
      `checked`, which grows on a create
- [x] **The picker showed one of two projects.** It took the project map from the
      sidebar's cached listing, and nothing invalidated that cache — `editTags`
      itself returned early without `onChanged()` whenever the tags came out
      unchanged or the picker was dismissed. It reads `project list --json` fresh
      now, with the cached map as the fallback, and every exit path refreshes when
      a project was created
- [x] **`referat project add --json`**, so the extension learns the id from the
      command that assigned it instead of matching the display name back out of
      `project list` — which returns the wrong project when two share a name, and
      nothing at all if the two normalizations of a name ever disagree
- [x] **Creating reopens the picker rather than re-rendering it.** Assigning
      `picker.items` makes VS Code recompute the ticked rows, so setting
      `selectedItems` on the next line races it. Not the cause of the report, but
      unfixable in place
- [x] **The create entry has to be *ticked*, not just focused.** In a **Dropped on 2026-09-06: the extension was deleted at step 23.**
      `canSelectMany` QuickPick, Enter accepts the picker with whatever is
      checked; the focused row is not checked by that. So typing a new name and
      pressing Enter applies the existing ticks and silently creates nothing.
      Watch whether that is what "did not reliably" was partly about, and consider
      treating the create row as chosen when it is the active item and nothing
      else is ticked

### Getting text out of the meetings folder (2026-09-01)

- [x] **`editor.copyWithSyntaxHighlighting: false`** in the seeded
      `.vscode/settings.json`. VS Code puts plain text *and* theme-coloured HTML
      on the clipboard, and Word, Google Docs and Outlook all prefer the HTML, so
      a pasted note arrived as dark-background monospace. Off, a paste is the raw
      Markdown
- [x] The pair, said in `CLAUDE.md` because only one half is obvious: **copy from
      the source for Markdown syntax, from the preview for rendered headings and
      bullets**. Reaching the source needs the preview's *Open Source* action or
      *Open With… → Text Editor*, since `*.md` is associated with the preview
      editor in that workspace
- [ ] The association stays. Reading is what that folder is for, and a keybinding
      or a per-file override is a smaller change than giving it up — revisit only
      if opening the source by hand becomes a daily annoyance

### House style for the notes (2026-09-01)

- [x] **U.S. spelling**, in the meetings folder's `CLAUDE.md` and in the
      `/cleanup` prompt, so it changes by editing Markdown rather than code
- [x] Bounded in three directions, which is the part that matters: never
      `transcript.md`, never a **name** (a person, product, project or
      institution keeps its own spelling — `Centre for Human-Centred Computing`
      stays), and never the inside of a quotation
- [ ] **Untested against a note.** The rule is written; no `/cleanup` run has
      happened since. Read the next note for a spelling it should not have
      changed — a surname, an institution, a quoted line — which is the failure
      worth catching, not a missed *organise*
- [ ] This is the second house-style rule the notes will carry, after step 10's
      *Known people and terms* table. If a third arrives, they probably want one
      section rather than three places to look

### The owner had two labels in one transcript (2026-09-01)

- [x] **`transcribe._owner_to_me` rewrote a voiceprint-matched owner to `ME`**
      while `label.apply_name` wrote the name it was given, so one person could
      appear under two labels in one file. `2026-09-01_2102` came out with 178
      `ME:` lines and 4 `Niklas:` lines for the same voice, because the microphone
      split the owner into two clusters and only one of them matched. The rewrite
      is deleted; the owner is a name like anybody else
- [x] **`ME` now means one thing**: mic-channel speech nothing attributed —
      diarization did not run, or no turn overlapped the segment. `REMOTE` is its
      counterpart. It must never be widened back into a name; `2026-08-28_1152` is
      258 lines of it that are actually two people
- [x] **Two bugs fell out with it.** `label --forget <owner>` looked for the name
      in a file that said `ME` and silently reverted nothing, which would have
      left `SPEAKER_01` in `unknown_speakers` with no snippets (auto-matched
      clusters get none) and no sample lines (they said `ME`) — unnameable
      forever. And a `rerun` flipped a hand-labeled name back to `ME`, so the
      label of one's own voice was not stable across one
- [x] **`referat relabel [<meeting-id>]`** repairs what was already written, since
      those transcripts have released their audio. Gated on the microphone having
      clustered into nothing but the owner — measured, like `bootstrap_owner`'s
      condition. A blanket rewrite would have renamed 258 lines of
      `2026-08-28_1152`, irreversibly
- [ ] **The gate hides a deliberate exception**, written into the docstring, the
      CLI help and `CLAUDE.md` so it is not later read as a contradiction: some of
      those `ME` lines were `assign`'s no-overlapping-turn fallback rather than
      the matched cluster, and nothing records which, so `relabel` does name
      unattributed speech. It is bounded by the gate having proved the only voice
      on that microphone was the owner's. **Do not relax the gate** without
      revisiting this
- [ ] **`ME` was never mic-only**: a *system*-channel cluster matched to the owner
      also rendered `ME`, and after the merge those lines are indistinguishable
      from mic ones. `relabel`'s mic gate is still the right gate, but a meeting
      whose mic gate fails while a loopback cluster was the owner keeps those
      lines as `ME` for good. No meeting here is in that state
- [x] **`--forget` inverted a many-to-one mapping** and kept whichever label came
      last in dict order. With the owner on two clusters that would have rewritten
      all 182 mic lines to `SPEAKER_02:`. It reverts to the lowest label now and
      says so — a merge somebody already asserted by naming both
- [x] `apply_name` warns when `relabel_transcript` changes nothing, the third way
      `meta.json` and the Markdown could disagree while every write succeeded
- [x] **`label --json` carries `channel` and `owner`**, and the speaker card says
      which file a voice arrived in and leads with the owner for a mic speaker.
      Naming four speakers after one call, one of them was the person naming them,
      with nothing on screen saying so. A hint, never a name

### Transcripts rendered as one paragraph (2026-09-01)

- [x] **`transcript.md` had one newline between entries, which Markdown reads as
      a soft break**, so every transcript rendered as a single unbroken
      paragraph — 258 utterances of it for `2026-08-28_1152`. The preview was
      right and the file was wrong. `transcribe.ENTRY_SEPARATOR` is now a blank
      line and `render_transcript` joins on it
- [x] **`referat reflow [<meeting-id>]`** repairs the transcripts already
      written. A renderer fix reaches only new files, and the existing ones have
      had their audio released — no `rerun` can regenerate them, so this is the
      only way they become readable. Idempotent, writes nothing when there is
      nothing to insert, and conservative: a blank line goes in only where the
      lines on both sides match the entry shape
- [x] **Not an exception to the immutability rule.** It inserts whitespace
      *between* lines and rewrites none of them — the same narrowness `referat
      label` observes. Verified rather than asserted: the sequence of non-blank
      lines is identical before and after on all four real transcripts, and
      `label.relabel_transcript` still renames a speaker in a reflowed file while
      leaving that same name alone where it appears in the *speech*
- [x] **`markdown.preview.breaks: true` was the tempting alternative** — it would
      have fixed every existing transcript with no file touched. Rejected on a
      fact about VS Code: folder settings apply only when that folder is in the
      workspace, and the sidebar opens transcripts from whichever window is
      running, usually the repository. It would have worked in one window and not
      the one actually used
- [ ] **Reconcile the live meetings folder's `CLAUDE.md` by hand.**
      `templates/meetings/CLAUDE.md` now shows the new transcript sample and
      `paths.seed_tree` never overwrites a seeded file, so the live copy still
      shows entries with no blank line between them. One code block, and the
      standing cost of the seeding rule

- [x] **Smart App Control now blocks `uv.exe` itself**, not just PyAV's bundled
      DLLs. `uvx.exe` says it outright — `An Application Control policy has
      blocked this file. (os error 4551)` — and through bash `uv.exe` surfaces as
      `Permission denied`, exit 126. Cause: all three uv binaries are
      **NotSigned**, and SAC admits an unsigned binary only on cloud reputation,
      which is per build and can be withdrawn without anything local changing.
      uv 0.12.6 worked here for a week and stopped on 2026-08-31.
      **There is nothing to whitelist**: SAC has no exclusion list, by design,
      which is the difference between it and SmartScreen — so reinstalling from
      scoop, from Astral's installer or from PyPI gets the same bytes and the
      same block. **Resolved by not needing uv**: the venv's own Python still
      runs, `ensurepip` bootstraps pip with no network and no uv, and pip
      replaces `uv sync` given
      `--extra-index-url https://download.pytorch.org/whl/cu128` — which pip
      cannot learn from `pyproject.toml`, since the cu128 pin lives in
      `[tool.uv.sources]` and only uv reads it. SETUP.md section 2, CLAUDE.md
      Commands
- [ ] **The venv's `python.exe` is unsigned too**, and is running on exactly the
      same cloud reputation that was withdrawn from `uv.exe`. If it is ever
      withdrawn from the interpreter, the fallback above goes with it and there
      is no third layer — the environment would have to be rebuilt from a signed
      Python. Not worth acting on, worth having written down before it happens
- [ ] Worth one try if uv is wanted back: `winget upgrade astral-sh.uv` to
      0.12.7. Reputation is per build, so a newer one may pass — but it is still
      unsigned, so it can be blocked again later. Not a fix, a coin flip

- [x] **Speaker bleed. Observed 2026-09-02**, in `2026-09-02_1059` — a hybrid
      meeting whose loopback followed the default output to a SONY TV over HDMI,
      so Zoom played the far end into the room and the Jabra recorded it back off
      the air. 2200 entries, 1722 microphone segments against 478 loopback, 1239
      lines under a remote speaker's name, of which roughly **761 are the
      microphone's second copy**. Fixed by `referat/bleed.py`, one-directional:
      microphone segments may go and loopback segments never do, because room
      speech exists only on the microphone and a dropped loopback line could be
      the far end's only copy.
      **The predicted shape was wrong in a way worth keeping.** It is not `ME`
      against `REMOTE`: both channels are diarized now and both were labeled by
      hand, so the microphone clustered into four speakers — Gaby, Niklas, and
      echo clusters for Benjamin and Yvonne — and one person ended up under one
      name from two clusters on two channels, with the channel erased at render
      time. That is what made it invisible rather than obvious, and it is why the
      rule had to be built on clusters rather than on the two fallback labels.
      Original note follows.
- [ ] **Speaker bleed.** Recording through laptop speakers rather than
      headphones puts the remote voice on *both* channels, which would duplicate
      every remote line — once as `ME`, once as `REMOTE`. Not observed in the
      2026-08-27_1906 call, whose loopback was empty anyway, so nothing is done
      about it. Revisit if duplicated lines ever show up in a real transcript.
- [x] **The second cost happened, and the predicted mechanism did not.** The
      note below says bleed poisons the *automatic* mic-channel additions, and
      that the guard is conditioning them on the loopback having voice. That
      guard — now `bootstrap_owner`'s one-cluster gate — **worked exactly as
      designed and refused**: the echo clustered as a second voice on the
      microphone, which is precisely the condition it tests for. The pipeline
      never writes to the voices database at all; `voices._identify` only reads
      it. The two poisoned prints came through `referat label`, from a person
      correctly answering "Benjamin" and "Yvonne" about clusters that should
      never have been offered to them. **So the defence belongs where a human is
      offered a cluster, not where the pipeline matches one**: `Cluster.echo`,
      `unknown_speakers` skipping it and `label.apply_name` refusing it. A
      prediction right about the outcome and wrong about the path is worth
      leaving legible.
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
- [x] The one real meeting with a populated loopback channel now exists:
      `2026-09-02_1059`, four people, two in the room and two on separate Zoom
      connections, 478 loopback segments. The merge itself was correct — every
      line is in the right place on the timeline. What it exposed is that a
      correct merge of two channels holding the same speech is still wrong, which
      is what `referat/bleed.py` now handles. Original note follows.
- [ ] The one real meeting with a populated loopback channel does not exist yet.
      Step 6's merge was verified on a synthetic meeting built by cutting real
      speech out of `mic.wav` and laying it on both channels in alternating
      windows. Check a real Zoom or Teams call when one happens

- [ ] **`[bleed].cluster_time` and `cluster_text` are reasoned, not measured.**
      The other three knobs — `contain`, `back_contain`, `min_tokens` — were
      fitted against `2026-09-02_1059`'s rendered transcript and have a
      false-pairing count behind them. These two cannot be: the cluster rule needs
      both channels' segment lists, which exist only during transcription, and
      that meeting released its audio. **Record the next hybrid meeting with
      `[transcription].keep_audio = true`**, run `scripts/bleed_report.py` and
      `scripts/bleed_fixture.py` against it, fix the numbers, then `referat
      promote <id> --release-audio`. Same sentence `keep_audio`'s own docstring
      makes about `match_threshold`, new thresholds
- [ ] **`2026-09-02_1059` was left un-applied on purpose.** `referat debleed`'s
      dry run finds 66 removable entries against ~761 duplicates, because 764 of
      the 1227 entries under a two-channel name are below `min_tokens` and can
      never be judged from text. Removing 66 of 761 leaves a file still mostly
      duplicated but *looking* deduplicated, and a reader who sees no obvious
      repeats stops looking for them. Its `notes.md` carries the explanation
      instead. Revisit only if a future transcript has a better ratio
- [x] **`referat debleed` converges rather than being idempotent**, unlike the
      two repairs it is modelled on, because pairing is greedy and disjoint and
      removing a line can make two others adjacent. 66, then 1, then none on
      `2026-09-02_1059`. Found by running `--apply` three times against a copy in
      a scratch meetings root, which also caught the real bug behind it: the
      record in `transcription.debleed` was being **replaced** on the second
      pass, leaving 67 lines gone from the file and 1 recorded. It accumulates
      now, and the file and the record were checked to agree at 67 and 67. The
      whole argument for allowing this command to delete anything is that the
      deletion stays readable, so that was not a cosmetic bug
- [ ] `voices._identify` cuts snippets for a cluster before the echo verdict
      exists, because identification runs per channel with the microphone first
      and the loopback has not been transcribed yet. `bleed.mark_clusters` then
      deletes them. Wasteful rather than wrong, and the only fix is deferring
      identification until both channels are done — which costs holding both
      decoded arrays alive or decoding twice, and splits a function that is
      currently one readable pass. Not worth it while the waste is three WAVs
- [ ] `transcribe.parse_entry` is correct only because `voices.name_complaint`
      refuses a name containing `:`, on the stated grounds that it goes into a
      transcript label. Two files, one invariant: relax that refusal and the
      parser silently starts cutting names in half. Said in both docstrings
- [ ] **Do not pin `[audio].loopback_device`** as a bleed mitigation, which is
      the obvious-looking hardening and is a trap. The loopback decides where
      Referat *listens*, not where sound *comes out*: pinned to the Jabra while
      a call plays through a television it records silence and loses the far end
      entirely, which is worse than recording it twice. Written into SETUP.md §4a
      where somebody would otherwise reach for it

- [ ] Decide whether `keyboard` hotkeys need an elevated process to fire while
      an admin window has focus
- [x] Confirm `PyAudioWPatch` publishes a Python 3.12 wheel; otherwise switch the
      loopback capture to `soundcard` — 0.2.12.8 installs cleanly on 3.12
- [x] `uv` appeared to be missing from PATH. It is not: WinGet put its Packages
      directory on the persisted **user** PATH, but shells started before the
      install never received it. A new terminal fixes it — nothing to add.
      Worth a line in SETUP.md so it is not re-diagnosed a third time.
      **The line is SETUP.md section 2**, written in the same session that hit
      it for the third time.
- [ ] Synthetic keystrokes cannot be injected from an automated session, so the
      hotkeys can only be verified by hand — keep the hook side callable
      directly for future checks
- [ ] The recording clock starts before the audio devices are opened, so every
      meeting begins with ~1 s of padded silence on both channels. Harmless, and
      it keeps the two channels aligned with each other; revisit only if the
      lead-in ever bothers the transcription
- [x] `SetThreadExecutionState` only suppresses the *idle* sleep timer. Closing
      the lid or choosing Sleep still suspends the machine mid-meeting. Fixing
      that means changing the power plan's lid-close action, which is a
      system-wide setting Referat should not silently rewrite — decide whether
      SETUP.md should just tell the user to set it.
      **Resolved: yes.** SETUP.md section 10 gives both the Settings path and the
      `powercfg /setacvalueindex ... LIDACTION 0` command, and says plainly that
      Referat will not rewrite a system-wide power setting on your behalf. A
      document telling a person is not the program doing it silently, which is
      the distinction the original note was reaching for
- [x] `powercfg /requests` is administrator-only, so an ordinary session cannot
      confirm the hold that way. `SetThreadExecutionState` returns the previous
      state, though, which makes the call its own query: that is how step 4 was
      verified, and it also demonstrated the per-thread scoping directly. Run the
      `powercfg` check by hand in an elevated shell once
- [x] **Smart App Control is enforcing on this machine** (confirmed:
      `VerifiedAndReputablePolicyState = 1`), and it blocks PyAV's unsigned
      FFmpeg DLLs, so `import faster_whisper` fails outright. Worked around by
      decoding the WAVs directly and stubbing `av` — see
      `transcribe._neutralize_pyav`. **This will very likely bite again at step
      7**: `pyannote.audio` pulls in `torchcodec`, which bundles FFmpeg the same
      way. Turning Smart App Control off is a one-way, system-wide change
      (Windows cannot re-enable it without a reinstall), so it is the user's
      call and not something Referat should ask for — decide at step 7 whether
      to work around it again or to document it in SETUP.md.
      **Seen in production on 2026-08-28** and behaving exactly as designed: the
      log carries one INFO line per transcription — `PyAV is unavailable (DLL
      load failed while importing frame: An Application Control policy has
      blocked this file.); decoding WAVs directly instead` — and the model loads
      on CUDA a second later. The blocked submodule varies between runs
      (`bitstream`, `frame`), being whichever PyAV imports first. It reads more
      alarming than it is; consider softening the wording.
      **Resolved: both.** Step 7 worked around it again — pyannote is handed an
      in-memory waveform and never reaches for `torchcodec`'s FFmpeg — and
      SETUP.md section 10 documents why the workaround exists, states that
      turning SAC off is one-way, and does not recommend it.
      **The prediction above was right and the resolution was half a
      resolution.** Handing pyannote a waveform stops it *decoding* through
      torchcodec; it does not stop it *importing* torchcodec, which it does at
      module scope and which loads the DLLs anyway. Corrected on 2026-09-01 —
      see the step 7 item on this, and `diarize._neutralize_torchcodec`
- [ ] `torch` must be imported before `faster_whisper`. ctranslate2's converters
      import torch halfway through their own import, and torch 2.11 does not
      survive being entered that way — it reaches `torch.utils._debug_mode`
      before `torch.library` is bound. `load_model` does this on every device;
      revisit when torch is next upgraded
- [x] The Hugging Face cache warns that symlinks are unavailable, so model files
      are stored duplicated. Harmless, fixed by enabling Windows Developer Mode;
      decide whether SETUP.md should mention it. Seen again at step 7, on the
      pyannote download. **Resolved: mention it** — one sentence in SETUP.md
      section 8, beside the model downloads. It appears on the very first
      transcription and looks alarming, and a warning nobody has been warned
      about is a warning that gets re-diagnosed
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
- [x] `2026-08-27_1906` had been recorded before the loopback channel existed,
      so its `system.wav` had never been transcribed and `release_audio_if_clean`
      had been blocked on it ever since. The step-8 `referat rerun` of it
      transcribed both channels and reclaimed the 65 MB — which is also the first
      time a rerun has been run on anything real
- [ ] The busy-tray guard and the live `referat status` branches were verified
      against a faked `status.json` pointing at a real child process, not against
      the tray itself: a tray started from an automated session has no console to
      quit from and its hotkeys cannot be driven. Check them by hand once, the
      same way the hotkeys still need checking.
      **Half of this closed at step 9.** Proving the autostart command line meant
      starting a real tray under `pythonw.exe`: `referat status` reported it live
      at exit 0 with the right pid, and killing that process exercised the stale
      branch against a genuinely dead tray rather than a stand-in. The busy-tray
      guard in `rerun` is still only tested against a faked file
- [x] Pin the VS Code interpreter to the uv venv and set
      `python-envs.alwaysUseUv` — the extension had selected system Python 3.14
      and was failing `python -m pip list` (uv venvs have no pip)
- [x] **And then take `python-envs.alwaysUseUv` back out.** Smart App Control
      blocks `uv.exe` at `CreateProcess`, so the setting turned every package
      refresh into `Running: uv --version` followed by `Error refreshing packages
      A system error occurred (spawn UNKNOWN)` — a spawn failure rather than an
      exit code, which is why it reads like a VS Code bug rather than a blocked
      binary. Both halves of its justification are gone: pip is bootstrapped, and
      the venv is built by `py -m venv` now, which seeds pip itself
- [ ] Step 11 spawns `claude -p` with `--permission-mode acceptEdits` and
      `--allowedTools "Read,Write,Glob"` in a folder full of private meeting
      material, which makes the `/cleanup` prompt the only thing bounding what
      gets written there. **Now built and therefore live**, so this is no longer
      hypothetical: re-read that prompt with this in mind once there are real
      meetings in the folder, and remember that *Generate notes* is one click

- [ ] **`--forget` cannot bring the snippets back.** They are deleted the moment
      a speaker is named, so a person who is forgotten leaves a `SPEAKER_NN`
      with no audio to play. `referat label` now falls back to printing three of
      that speaker's lines and still lets you name them — the embedding in
      `meta.json` is untouched, so it is only the listening that is lost.
      Keeping snippets until a `--forget` becomes impossible was the
      alternative, and it costs disk on every meeting to serve a rare case.
      **A second way into the same state arrived on 2026-09-01**: when one name
      covered two clusters, `--forget` reverts every line to the lowest label, so
      the *other* label keeps its embedding and ends up with no lines and no
      snippets. `_label_speaker` already prints "no audio and no lines left for
      this speaker; skipping", so it degrades the way it should — but it is a
      label that can never be named again, and the sidebar shows it as a card with
      nothing in it. Worth deciding whether such a label should simply be dropped
      from `unknown_speakers`
- [ ] The two voices this was verified against are both female Windows TTS
      voices, and they matched at 0.98 and 0.96 with the runner-up around 0.5.
      That separation is not real: identical synthesis of the same voice is the
      easiest possible case. Do not read the 0.70 threshold as validated by it
- [x] Speaker identification adds a second pyannote pipeline load to any meeting
      that qualifies for the owner bootstrap — the weights are cached, so it is
      a few seconds. Fold it into the diarization run if it ever matters.
      **Gone**: the bootstrap now files the mic cluster's own centroid, which
      diarization already computed, so nothing is re-embedded and
      `diarize.embed` was deleted along with its only caller
- [ ] A cluster embedding is a centroid over one meeting. The same person on a
      different headset, a bad connection, or a cold will land somewhere else in
      the space, which is why the database keeps a *list* of embeddings per name
      rather than averaging them into one. Watch whether a person needs three or
      thirty before they match reliably
- [ ] If matching ever gets slow, cap the embeddings kept per person — the most
      recent twenty, say. At one meeting a day and a 256-float vector each it
      will not get slow for years, so this is a note and not a task
- [ ] **Every meeting now runs pyannote twice**, once per channel, where it used
      to run once. On a meeting held in person the loopback is silent and skips
      cheaply, so the real cost is a hybrid meeting; the weights are cached and
      it was 22s on a 50s synthetic. Watch it on a long meeting
- [ ] **A person in the room and the same person on the call are two clusters**,
      one per channel, and `referat label` will ask for that name twice. Harmless
      -- both embeddings are theirs -- but the transcript then carries the same
      name on two numbers. Same question as the split-cluster item below
- [ ] `2026-08-28_1152` keeps its 258 lines of `ME` and always will: it
      transcribed cleanly, so its WAVs were released before the mic channel was
      ever diarized, and no snippets were cut. Worth remembering that the quality
      gate judges *transcription*, not attribution -- a meeting can be released
      as clean while its speakers are wrong
- [ ] Diarization can also split one person across two clusters in a single
      meeting. `referat label` will then ask for the same name twice, which is
      harmless — both embeddings are that person's — but the transcript keeps
      two differently-numbered speakers who now carry the same name. Decide
      whether that is fine (it reads fine) or whether the two lines should merge.
      **Observed on real audio, twice in one meeting** (`2026-09-01_2102`: the
      owner as SPEAKER_01 and SPEAKER_02 on the mic, Mohammad as SPEAKER_03 and
      SPEAKER_05 on the loopback). It does read fine. It is not free, though: it
      is what made `--forget`'s many-to-one revert a real bug, fixed on
      2026-09-01 by reverting to the lowest label - so the question is now
      *whether the merge should happen at naming time* rather than only when
      somebody is forgotten

- [x] **Dropbox would have synced the meetings folder while a meeting recorded.**
      `mic.wav` and `system.wav` are appended continuously with incremental header
      rewrites, ~460 MB an hour across both, and only deleted once the quality gate
      passes. Fixed before it ever ran: `[paths].staging_dir` records and
      transcribes outside the synced tree, and the meeting moves in only once its
      audio is gone. The deletion mattered more than the upload — Dropbox keeps
      deleted files and old versions on its servers for weeks, so `audio_released`
      would have been a lie about recordings of people who never consented
- [x] A meeting whose audio is kept now stays in staging indefinitely. `referat
      list` marks it and says to rerun, but nothing prunes: a meeting that never
      transcribes cleanly sits in `%LOCALAPPDATA%` forever. Decide whether that
      wants a `referat rerun --accept` that promotes it with the audio, or just a
      line in `list` old enough to nag. **Answered on 2026-09-01, and half of it
      was the wrong question**: step 15 gives it a visible off-ramp in the
      sidebar, and step 14 gives it a `gate_failed` status to be found by — but
      *accept* cannot promote it "with the audio", because no WAV may ever reach
      the meetings folder. It has to delete the WAVs first, which makes it an
      irreversible action behind a modal rather than a convenience.
      **Built at step 15** as `referat promote <id> --release-audio`, with the
      modal in the sidebar and the bare `promote` refusing while the WAVs are
      there
- [ ] **SETUP.md has never been followed on a machine that did not already have
      all of this.** It was written from this laptop's history — every command in
      it was run, and every claim cross-checked against the code — so the one
      thing it cannot prove is that nothing is *missing*. Read it against a clean
      Windows install the first time there is one
- [x] **How step 13 authenticates to Google was unresolved** and was settled at
      implementation on 2026-09-04, as this box said it would be: a **desktop
      OAuth client**, for the reason below — the picker has to see your own
      Drive. Two scopes and no more: `documents`, and
      `drive.metadata.readonly` for the search, which returns names and ids and
      no content. `drive.file` was refused as insufficient rather than chosen
      as minimal: it sees only files this application created, so on a fresh
      install the picker would find nothing at all. The one thing this box did
      not foresee is that the standard flow **binds a socket** and Conventions
      forbid one, so consent is a URL you open and a redirect you paste back.
      `.gitignore` gained both files in the same change.
      Original note follows.
- [ ] A **desktop OAuth client** means one browser
      consent and a refresh token cached to a file — which follows the
      `hf_token_file` precedent in `config.py` exactly — and Drive search sees
      the user's own Drive, which the "select existing doc" picker needs to be
      worth anything. A **service account** needs no browser flow, but its Drive
      search only sees documents shared with the robot, which breaks that picker
      and makes every digest doc owned by a machine identity. Settle it at
      implementation; `.gitignore` gains the token file in the same change
- [ ] **Deferred, and deliberately not scheduled: `referat glossary prune
      --dry-run`.** A hotword list only grows — every name ever labeled, every
      glossary term ever added — and terms belonging to finished projects and to
      people who no longer come to meetings go on spending prompt budget under
      step 12b's 224-token cap. A command listing the terms that appear in no
      transcript for some long period would be the way to find them. Recorded as
      an idea and nothing more: the cap may never actually bite, the scan is
      every transcript in the folder, and pruning a list that quietly changes how
      audio is transcribed is not obviously a thing to automate. Do nothing until
      a real list is really too long
- [ ] **There are now two lists of names, and they overlap on purpose.** The
      known-voices database holds who a *voice* is; the meetings folder's *Known
      people and terms* section holds how a name is *spelled* when Whisper
      mangles it. Somebody can be in either without being in the other — a
      voiceprint with no spelling problem, or a name constantly misheard that
      belongs to somebody who has never been recorded. Only the database feeds
      hotwords: the `CLAUDE.md` table is prose for `/cleanup` and is deliberately
      **not** a fourth hotword source, because that would put a machine-read list
      inside a hand-written Markdown document. Watch whether keeping the two in
      step by hand actually becomes annoying before unifying anything
- [ ] **A shared doc is a much wider blast radius than a shared folder.** The
      digest is the first thing Referat has ever sent anywhere, and what it sends
      is whatever `/cleanup` decided to put in `notes.md` — about people who did
      not read the prompt. Re-read that prompt with this in mind before the first
      digest doc is shared with anybody, and keep the rule that the transcript,
      the audio and `.voices/` never leave the machine at all. **This gets wider
      with step 14, not narrower**: one meeting now fans out to every doc of every
      tag it carries, so the question "who can see this" has more than one answer

- [x] **`scipy` came off the transcription critical path** on 2026-09-01, after
      Smart App Control blocked three of its DLLs in five minutes and then
      stopped. `transcribe._resample` is Referat's own polyphase resampler in
      numpy, contract-compatible with the `resample_poly` it replaces and
      validated against it at 138 dB. `DecodeError` stops a decode failure from
      triggering the CUDA-to-CPU retry. See the CHANGELOG entry and CLAUDE.md

- [ ] **Diarization can still be taken out by an SAC window**, through
      `pyannote -> lightning -> torchmetrics -> scipy.signal`. There is no seam to
      cut there and it already degrades to unnamed speakers, so nothing was done —
      but it means a meeting transcribed while the window is open silently loses
      its speaker names, and `referat rerun` is the only way to get them back. If
      that turns out to happen often, the answer is probably for
      `diarize.diarize` to record *why* it failed distinctly enough that
      `referat list` could suggest a rerun, rather than to fight scipy

- [x] **Fixed on 2026-09-04, and it was worse than this box said.** The box named
      `label.apply_name`; the dangerous caller is `voices.bootstrap_owner`, which
      runs **inside the transcription pipeline** on a background thread with
      nobody watching — so the failure was not "somebody names a speaker and
      loses the database", it was "a meeting transcribes overnight and the
      database is gone by morning". `VoicesDB.unreadable` now mirrors
      `ProjectsDB`'s, five write paths refuse, and `save` refuses as a backstop
      so a check forgotten later cannot cost the file either.
      **And the guard alone was not enough**, which is the part this box did not
      reach: it stops a *wrong* write and does nothing about a correct one, and
      the reason there was no way back is a rule this project is right about —
      `.voices/` is excluded from sync and from backup because it is biometric
      data about people who never asked to be in a database. So `save` now copies
      the current file into `<voices_dir>/backups/` first, fifteen kept, **inside
      the voices folder**, where they inherit that same exclusion. Anywhere else
      would have solved the recovery problem by breaking the privacy one.
      `scripts/voices_guard_fixture.py` proves both, and proves it did not touch
      the real database while doing so.
      Original note follows.
- [ ] **The known-voices database has no unreadable-file guard.**
      `VoicesDB.load` returns an empty database when `voices.json` will not parse,
      exactly as `ProjectsDB.load` does — and then `label.apply_name` calls
      `db.save()`, which would replace every voiceprint on this machine with one
      new entry, silently. Step 14 gave the projects file a `unreadable` flag and
      made every mutating verb refuse on it; the same three lines belong here, and
      the stakes are higher, since a voiceprint cannot be re-created by typing it
      in again. Not fixed in step 14 because it is not step 14's file and a
      half-tested fix to the biometric database is worse than a known one

### The first multi-speaker recording (2026-09-02)

A ten-person meeting held in person, and the first time anything here has met a
room rather than one or two people. Everything below was verification; the one
code change exists because the pipeline's default is to **delete** the audio that
would make the rest of it answerable.

- [x] **`[transcription].keep_audio`** keeps the WAVs even when the quality gate
      accepts the transcript. Off by default, on for today, and to be turned off
      again once the calibration below has its dataset
- [x] **`transcribe.audio_is_clean`**, the gate's verdict split out of
      `release_audio_if_clean`, which now pairs it with `release_audio`. Without
      the split, a `keep_audio` guard would have made the combined function return
      `False` and the caller would have written `gate_failed` — a lie, since the
      gate passed and somebody chose to keep the audio. The gate is now asked
      first and unconditionally, so `keep_audio` costs the deletion and never the
      verdict. Verified both ways: gate refused with `keep_audio` on still writes
      `gate_failed` and no `audio_kept`
- [x] **A meeting kept this way stays in staging**, as `transcribed`, and is
      released with `referat promote <id> --release-audio`. It is *not* promoted
      with its audio: `meetings_dir` is inside Dropbox, and a delete in a synced
      folder does not delete. `run_promote` already accepted a non-`gate_failed`
      staged meeting, so no CLI change was needed for the off-ramp
- [x] **`meta.json` records why**, as `transcription.audio_kept` —
      `{reason, gate, at}` — beside the `audio_released: false` that stays honest
- [x] **Two messages that assumed kept audio meant a failed gate.** `referat
      list`'s footer said `run: referat rerun` for every staged meeting, and
      `promote`'s bare refusal offered a rerun "to try for a transcript the gate
      accepts". Both now split on `audio_kept`: a rerun for the meetings the gate
      refused, `promote --release-audio` for the ones kept on request
- [x] **No cap on speaker count anywhere.** `diarize._run` passes no
      `num/min/max_speakers`, so pyannote defaults `max_speakers` to `inf`,
      clamped only by the embedding count; `assign`'s numbering is
      `start + len(renamed)` and `:02d` is padding, not width. Nothing to raise
- [x] **Snippets are cut for every unnamed cluster**, no top-N and no break in
      `voices._identify`. So `referat label` has material however many speakers
      there turn out to be
- [x] **CUDA and the model cache verified offline.** With `HF_HUB_OFFLINE=1`,
      `large-v3` loads on CUDA in 7.7 s and the pyannote pipeline in 4.9 s.
      community-1 is self-contained — segmentation, embedding and PLDA in the one
      repo — so nothing about today depends on a download
- [ ] **Calibrate the similarity threshold and margin against real multi-speaker
      audio.** `[speakers].match_threshold` (0.70) and `match_margin` (0.15) were
      set strict and have only ever met a database of four names and meetings of
      one or two people. Today's recording is the first dataset that can answer
      what they should be: ten voices on one microphone, with the `match` block
      recorded in `meta.json` for every cluster including the refused ones, which
      is the material this was always waiting for. **Not started** — and the audio
      has to survive until it is, which is what `keep_audio` is for. A wrong name
      is worse than no name, so any loosening is measured against the near-misses
      rather than argued
- [ ] **The loopback device is the Realtek headphones**, not the Jabra, because
      `[audio].loopback_device` is empty and takes the Windows default. Harmless
      for a meeting held wholly in person; if somebody dials in and comes out of
      the Jabra speaker, that audio is not captured at all. Decide whether the
      loopback should follow the mic when the mic is a speakerphone
- [ ] **A speaker only ever heard in overlap gets no label at all.** `diarize`
      reads `exclusive_speaker_diarization`, and `assign` is winner-take-all per
      Whisper segment, so a cluster that never dominates a segment receives no
      `SPEAKER_NN`, no snippets and no `meta.json` entry. Structurally the most
      likely way a ten-person room loses a person, and it was deliberately not
      touched today — it would mean changing clustering on the morning of the
      recording. Check the transcript against who was actually in the room

### Knowing which code the tray is running (2026-09-02)

- [x] **`referat/build_info.py`** and a build line in the tray menu:
      `0.1.0 - code 2026-09-02 09:06, started 09:12`. There is no build to date —
      an editable install means the `.py` files are the program — so the stand-in
      is the newest mtime across `referat/*.py`, sampled **once at import**,
      because a later read would describe the code on disk rather than the code
      loaded. Git was rejected: a commit date is wrong in exactly the case this
      exists for, code edited and not committed
- [x] **`status.json` carries `code_mtime`**, and `referat status` does the
      comparison, because only a process that started *after* the edit can see it.
      The menu deliberately carries no staleness warning: pystray builds the Win32
      menu in `update_menu()` and reuses the handle on right-click, so callable
      text refreshes on state transitions and an idle tray — the one somebody
      would be checking — would keep reporting itself current
- [x] `build_info.outdated`, not `stale`, since `referat status` already uses that
      word for the file a dead tray leaves behind
- [x] Two bugs the tests caught: microsecond precision made every fresh tray
      report itself out of date against a `status.json` storing whole seconds, and
      `code or CODE_MTIME` rendered the wrong branch for an explicit `None`
- [ ] **Step 20 replaces `pystray` with `QSystemTrayIcon`**, so the two lines in
      `tray.py` will be rewritten. `build_info.py` itself carries over unchanged,
      and the `update_menu()` reasoning above stops applying — a Qt menu is built
      when it is shown, so the command center *can* carry the live warning the
      tray menu cannot. Worth doing there rather than porting the limitation
- [ ] **`referat --version` still prints the bare version.** It could print the
      build line too, and probably should, but it is the one command deliberately
      kept instant and this would add a directory glob to it. Measure before
      deciding

Open questions from the 2026-09-01 planning session. Recorded rather than
decided, because guessing at them during implementation is how they become
someone's surprise later.

- [ ] **Where do step 17's split notes live?** The folder contract says `notes.md`
      and step 13 says the contract does not change. Either per-project `##`
      sections inside one `notes.md`, with `digest.py` selecting the section for
      each doc — contract intact, translator grows a concern — or
      `notes.<project-id>.md` files, which is honest and changes the contract.
      Undecided, and it decides how much of `digest.py` step 17 touches
- [ ] **Who cleans up orphan tags?** `referat project rm` leaves ids behind in
      `meta.json` on purpose, and step 14 renders them as orphans rather than
      hiding them. Is there ever a `referat tag --prune` that clears them, or is
      deleting a project simply permanent visible debt? Leaving them is the safe
      default and may also be the right one
- [ ] **Does `synced` regress every time somebody edits `notes.md` by hand?**
      Proposed yes, through the `notes_sha256` compare that step 13 needs anyway —
      but that makes the lifecycle field mutable by a text editor, which is worth
      saying out loud before it surprises somebody who fixed a typo and watched a
      meeting change state
- [ ] **Does `claude -p` emit incremental stdout** under the default output
      format, or does step 15's streaming progress need `--output-format
      stream-json --verbose`? Check it before building the progress indicator
      rather than after
- [ ] **Does step 9's autostart shortcut carry an `AppUserModelID`?** If it does
      not, step 16's toast cannot even display as Referat until something
      registers one, and that is a prerequisite rather than a detail
- [ ] **Can a toast activation reach a running `pystray` message loop**, and can a
      missed toast be reactivated from Action Center without a registered COM
      server? The answer decides whether step 16's on-stop prompt is a feature or
      just a notification, and it should be answered with a spike before the rest
      of the step is built

### The tray kept the GPU, and a Ctrl+C kept the lie (2026-09-02)

One morning's use, and one story told twice. `2026-09-02_0936` transcribed
perfectly at 09:49:43 and was re-transcribed anyway, because the surfaces
explaining why a `keep_audio` meeting cannot have notes written for it both led
with a rerun. The rerun stalled, because the idle tray still held the card. The
Ctrl+C that ended it destroyed the record of the run that had worked.

- [x] **Measured first.** `nvidia-smi` with the tray idle since its last meeting:
      pid 100100 holding **8484 MiB of 12227**. The stall was inside
      `ctranslate2.models.Whisper(...)` with 3.7 GB left, which is roughly what
      large-v3 in float16 needs before any workspace
- [x] **`referat/gpu.py`**: `gc.collect()` then `torch.cuda.empty_cache()`, and a
      log line giving what came back and what the driver now sees free. `del`
      returns the Python reference; torch's caching allocator keeps the arena for
      the life of the process. The two docstrings that claimed the VRAM was given
      back were describing an intention
- [x] **`gc.collect()` is not decoration.** On a failure path the release runs
      while an exception propagates, and a traceback holds frames which hold
      tensors — a frame/traceback cycle, reachable only by the cycle collector
- [x] **`torch.cuda.is_initialized()`, not `is_available()`**, or the release
      would create a CUDA context on a run that had deliberately stayed on the CPU
- [x] **Four call sites**, the last of them `tray._transcribe`'s `finally` and
      deliberately outside its `except`: by then the exception is handled and the
      cycles are collectable, so it is the one that gets the complete sweep
- [x] **The `transcription` block is merged, not replaced**, when a run starts, so
      an interrupted rerun stops destroying the previous run's `model`, `seconds`,
      `audio_released` and `audio_kept`
- [x] **`except BaseException` restores the prior status** and records
      `transcription.interrupted`. Not `failed`, which `meeting.py` defines as
      *transcription raised*: an interrupt is a verdict on the run, not on the
      meeting, and nothing on disk changed. `transcribing` is the one value never
      restored — it is what a hard kill leaves — and clamps to `recorded`
- [x] **`rerun.clear_speakers` became `clear_snippets`** and touches no metadata.
      Nothing in the pipeline reads `speaker_names`, the success path replaces it
      wholesale, and until then it is the map that describes the `transcript.md`
      still on disk
- [x] **`index.render_index` splits the staged footer** on `audio_kept`, the way
      `referat list` and `promote` already did. The meetings dashboard was the last
      surface hardcoding "the transcript did not come out clean"
- [x] **The extension's *Generate notes* warning leads with the release.** Source
      only — it is in maintenance and is not packaged again
- [x] **`keep_audio` off again**, which its own comment in `config.toml` had been
      asking for since the calibration recording
- [ ] **How much of the residue is CTranslate2's?** The new log line prints the
      driver's free bytes beside torch's, so the split is now measurable. If
      CT2 turns out to hold multiple GB across an idle tray, the thing to try is
      `CT2_CUDA_ALLOCATOR=cuda_malloc_async` set in `load_model` *before*
      `from faster_whisper import WhisperModel`, since ctranslate2 reads it when
      its allocator is first used. Measure before changing anything
- [ ] **The CUDA context is never returned** while the process lives. The only
      complete answer is running the job in a child process, which is recorded
      here and deliberately not built: it would put an interpreter start and an
      IPC channel on the path between a recording and its transcript, for a few
      hundred MiB
- [ ] **A hard kill still leaves `transcribing` behind.** No handler inside a
      process can promise otherwise. The sweeper, if it is ever wanted, has the
      pieces already: `rerun.busy_tray` asks `status.json` and Windows whether a
      pid is alive. The cheaper half of it is relaxing `referat delete`'s refusal
      at `cli.py` so that `transcribing` blocks only while a live tray is actually
      working — its stated reason is about a live writer, not about a field
- [ ] **`diarize._embed` has no callers.** Its `gpu.release` is therefore dead
      code kept for symmetry, and the day something in `label.py` or `voices.py`
      calls it is the day that release starts mattering
- [ ] **`rerun`'s guard asks the wrong question.** It refuses while the tray is
      *transcribing*, which is right about the intent — two large-v3 models do not
      fit — and blind to an idle tray holding the card, which is exactly what
      happened. Now that the release exists the guard is adequate; a free-VRAM
      preflight would make the refusal exact rather than inferential, and would
      have turned a four-minute stall into one sentence

### A meeting recorded with the microphone off (2026-09-02)

**`2026-09-02_1001` was not recorded.** Twenty-four minutes, and the only thing on
disk is a silent 134 MB `system.wav` and a 37-byte `transcript.md` holding its own
header. Found while chasing something else, which is the worrying part: nothing
reported it, and the meeting is not recoverable.

```
10:01:20 INFO    mic device: Microphone (2- Jabra SPEAK 510 USB) (WASAPI)
10:01:20 WARNING mic rejected 16000 Hz; falling back to 16000 Hz
10:01:20 ERROR   microphone capture unavailable
                 PortAudioError: Error opening InputStream: Insufficient memory [PaErrorCode -9992]
10:01:21 INFO    recording 2026-09-02_1001 (mic=off, system=on)
```

- [x] **`recorder.start` proceeds on one channel and said so in a log line.** It
      raises `RecorderError` only when *both* fail, which is right for a remote
      call and wrong for the meetings Referat is actually used for: in a room the
      microphone **is** the recording and the loopback is the silent one. A
      recording with `mic=off` is not a degraded meeting, it is no meeting, and
      the rule this breaks is the first one in Conventions — *recording robustness
      beats everything else*
- [x] **Decided: record anyway, and make it impossible not to notice.** Refusing
      outright would make a Zoom-only call unrecordable and would lose a meeting to
      a mic that would have come back. So: a hollow tray icon for the whole run
      rather than a filled one, a toast at the top of it, `missing_channels` in
      `meta.json`, and the STATUS cell in `referat list`. Four places, because
      anything that is only a log line has already failed once
- [x] **The fallback was wrong twice over.** It answers a
      failure of any kind with a *sample-rate* retry, and `-9992` is not a rate
      problem; and it takes the new rate from the device's `default_samplerate`,
      which for this Jabra is 16000 — the rate that had just been rejected — so
      the retry was bound to fail identically. "falling back to 16000 Hz" from
      16000 Hz should never have been printable. Guard the retry on the rates
      actually differing, and keep the original exception as the cause
- [ ] **Why `-9992` at all?** PortAudio reports "Insufficient memory" on WASAPI for
      states that are not memory — a device held in exclusive mode by another
      process is the usual one. The endpoints had just been reshuffled: the
      loopback moved from `Headphones (Realtek(R) Audio)` at 09:36 to `Speakers
      (Realtek(R) Audio)` at 10:01, so something was unplugged between the two
      meetings. Worth reproducing by taking the Jabra in a call and starting a
      recording, rather than guessing
- [x] **`referat list` can say it now**, in the STATUS cell, off the written
      `missing_channels` rather than off an absent key. Formerly: The row reads `transcribed`,
      `kept`, no unnamed speakers — a meeting that looks finished. A channel that
      never opened is in `meta.json` only as an *absent* key under `audio`, which
      is exactly the "infer it from what is missing" shape this codebase refuses
      everywhere else. Record it as a written fact

### `referat rerun` interrupted by the VS Code terminal (2026-09-02)

Three reruns started from the extension's terminal died 6 seconds in with
`KeyboardInterrupt`, inside `ctranslate2.models.Whisper(...)`; the identical
command in a plain PowerShell completed in 161 seconds. `reTranscribe` in
`extension.ts` sends no interrupt — it opens a terminal and one `sendText`.

- [x] **`cli.log_interrupts`** logs when a `SIGINT` *arrives*, with the elapsed
      time, rather than leaving only the traceback's account of where it
      surfaced. Windows delivers `CTRL_C_EVENT` to every process on the console
      and Python raises it at the next bytecode boundary, so a long call into C
      hides the delay completely — and the arrival time is the one number that
      separates a person pressing Ctrl+C from a terminal injecting something
- [x] **The leading suspect is `python.terminal.activateEnvInCurrentTerminal`** in **Dropped on 2026-09-06: the extension was deleted at step 23.**
      `.vscode/settings.json`. The Python extension injects an activation command
      into a new terminal on its own schedule, seconds after the terminal opens
      and therefore after `reTranscribe`'s command is already running. Unproven:
      confirm with the arrival time above, then by running the same command in a
      hand-opened VS Code terminal, then with the setting off
- [x] **If it is confirmed, the fix is not to fight the terminal.** Either wait **Dropped on 2026-09-06: the extension was deleted at step 23.**
      for the shell before sending, or drop the terminal and spawn the CLI the way
      every other extension action does, keeping the streamed output in the
      extension's own output channel. The terminal was chosen so the log could be
      watched, and an output channel does that too

- [x] **`Meeting.missing_channels`** is the written fact, loaded tolerantly so
      every meeting recorded before it existed reads as `[]` rather than needing a
      migration — the same choice the lifecycle widening made
- [ ] **Still unproven: why `-9992`.** The retry now survives it, which is the
      point, but the cause is worth knowing. Reproduce by taking the Jabra in a
      Zoom call and starting a recording; if it is exclusive-mode contention the
      WASAPI candidate will fail and the MME one will not, and the log now says
      exactly that
- [ ] **The candidate list is not verified to be the same microphone.** A
      substring match across host APIs is a good assumption and not a guarantee,
      and the final `None` is explicitly a different device. Recording the laptop
      array when you meant the Jabra is its own kind of wrong — quieter than
      silence, and harder to notice. The toast says which device was opened;
      whether that is enough is a question for the first time it happens
