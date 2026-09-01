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
      mode is a misattributed one-liner rather than a merged speaker. **A remote
      call is still unchecked**, and is a different test: it puts the far end on
      the loopback channel
- [ ] Route Python `warnings` into the rotating log with
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
      are still guesses
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
- [ ] Set `[speakers].owner_name = "Niklas"` in the real `config.toml`. Left
      empty by this session because it is a decision about my own biometric data
      and the default should be off
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
- [ ] **Under `pythonw.exe` a bad `config.toml` is completely silent.**
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

- [ ] Two or three real meetings with **other people in them**, so the loopback
      channel has voice and diarization actually runs
- [ ] At least one transcript where `referat label` has turned a `SPEAKER_NN`
      into a name, which also means the voices database is no longer empty
- [ ] Those meetings pass the quality gate and promote themselves out of
      staging, so the folder holds something `/cleanup` can be pointed at
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

- [ ] A **Known people and terms** section in `templates/meetings/CLAUDE.md`: a
      two-column table of the real spelling against what transcripts render it
      as. Maintained by hand, and the only place those spellings live
- [ ] Say in that section what it does and does not do. It normalizes
      **notes**, never `transcript.md`; and adding somebody to it does not name
      a `SPEAKER_NN` and puts nobody in `.voices/` — it is prose for the cleanup
      pass, not a voiceprint and not an identification
- [ ] The **flag rule**, written there and again in the prompt: an exact match
      is normalized silently, a near miss is corrected *and marked* —
      `Elmqvist (assumed transcription error: "Elmquist")` — once, at first use,
      and anything matching neither list is left exactly as transcribed. This is
      *a wrong name is worse than no name* applied to words: a silently applied
      guess reads as authoritative whether or not it happens to be right
- [ ] `templates/meetings/.claude/commands/cleanup.md` gains a **Names and
      terms** section carrying the same three cases, plus where the lists are:
      the folder's `CLAUDE.md`, and — once step 13 exists — the glossary of the
      project named by `meta.json`'s `project` key
- [ ] The flag text has to stay inside the Markdown subset step 13's translator
      handles. Parentheses and quotation marks are plain characters, so it does;
      check it against that list rather than assuming it
- [ ] A `[[Wikilink]]` uses the **corrected** name, with the flag beside it
      rather than inside the brackets, or the digest grows two entries for one
      person
- [ ] Reconcile the repo template against the live meetings folder by hand.
      `paths.seed_tree` never overwrites a seeded file, so neither of these
      changes reaches the folder on its own. That is the whole point of the
      seeding rule and it is also the half that will be forgotten

### Surfaced while building step 10

- [ ] **The `/cleanup` prompt has met exactly one transcript**, and the wrong
      shape of one: `2026-08-28_1152` is in-person, undiarized, 258 lines of
      `ME`. The notes it produced are good and honest — it said at the top that
      attribution is missing, inferred turns from conversational flow, and
      refused to name the second participant — but every instruction about
      *speakers* went untested, because there were none. Re-read it against the
      first call that has `SPEAKER_NN` in it
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
- [ ] Nothing prunes a `notes.md` whose meeting was re-run. `referat rerun`
      rewrites `transcript.md` and leaves the old notes beside it, silently
      describing a transcript that no longer exists. The title in `INDEX.md`
      then comes from stale notes. Decide whether a rerun should warn, or whether
      that is the user's problem — re-running `/cleanup` is the fix either way

## 11. VS Code extension (`referat-vscode/`)

**Built on 2026-08-31.** Everything below is done except the boxes that need a
person to click, which are collected at the end of this step.

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
- [ ] **The `[audio].mic_device = "Jabra"` substring matches nothing right now**,
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
- [ ] Press F5 and confirm the tree lists the meetings, newest first, with the
      right icons, and that a meeting recorded from the tray appears without a
      manual refresh
- [ ] *Open transcript* and *Open notes* open rendered Markdown
- [ ] *Generate notes* on a meeting without notes: that it finds `claude.exe`
      off PATH, that `/cleanup` runs with `Read,Write,Glob` and gets far enough
      to use `Glob` on a wrong id, and that `notes.md` opens afterwards
- [ ] *Re-transcribe* opens a terminal and runs
- [ ] The **Unknown speakers** node opens the panel, a snippet actually plays
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

- [ ] **The panel's `<audio>` element has never decoded a real snippet.** The
      WAVs are mono 16-bit PCM at 16 kHz, which Chromium handles, but that is
      reasoning rather than a test — the synthetic fixture's tones were never
      played through a webview. First thing to check when a real meeting has an
      unnamed speaker in it
- [x] **The repo's own `.vscode/settings.json` was not valid JSON**, found while
      adding `launch.json` and `tasks.json` beside it: the interpreter path was
      written with single backslashes, so `\.` and `\S` were invalid escapes.
      VS Code's parser is error-tolerant and had been carrying it since step 1,
      which is why the interpreter pin still worked and nobody noticed. Now
      forward slashes, which VS Code accepts on Windows, with a comment saying
      why so it does not get "fixed" back
- [ ] The tree calls `referat list --json` on **every** refresh, including one
      per watcher burst. That is a Python interpreter start each time, roughly a
      third of a second. Fine at this scale; if a hundred meetings ever make it
      feel slow, cache the document and invalidate on the watcher rather than
      making the CLI do less
- [ ] **`referat.repoRoot` empty means "search the workspace folders"**, so the
      extension does nothing useful in a window that does not have the
      repository open. That is the common case for a window opened on the
      *meetings* folder, which is exactly where somebody would want it. Decide
      whether the meetings folder should be recognised too, or whether the
      setting is simply the answer for that window
- [ ] A meeting that is **staged** cannot have notes written for it: `/cleanup`
      runs in the meetings folder and a staged meeting is not there. *Generate
      notes* says so and refuses rather than letting the pass fail confusingly,
      but the real fix is the one already logged under step 8 — a meeting whose
      audio was kept sits in `%LOCALAPPDATA%` forever

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

- [ ] A **"Projects"** section in the Referat view, beside the meetings: one
      node per project read from `<meetings_dir>/projects.toml`, labeled with
      the project name and the linked doc's title, or *not linked*
- [ ] Per-project context menu: *Link digest doc*, *Unlink*, *Open digest doc*
      (in the browser), *Sync now*
- [ ] *Link digest doc* offers the same two paths as `referat project link`:
      **Create new doc**, or **Select existing doc** through a QuickPick that
      re-runs the Drive search on `onDidChangeValue`, debounced, so typing
      filters against Drive rather than against a list fetched once
- [ ] After selecting an existing doc, the tab check: if it has no tab named
      `Meetings`, open it in the browser and show a modal saying to add one,
      with a *Re-check* button. Tabs cannot be created through the API, so this
      hand-off is the only way through and it has to look deliberate rather than
      like a failure
- [ ] Per-meeting *Assign to project*: a QuickPick over the configured projects,
      pre-selected on the remembered default. This is also the re-route — a
      meeting already assigned shows its current project and reassigning is the
      same action run again
- [ ] An **"Unassigned"** node listing every meeting with no project, the same
      way "Unknown speakers" lists what is waiting for `referat label`. That
      node *is* the queue; nothing else tracks it
- [ ] All of the above shells out to `referat project ...` and re-reads the
      files afterwards. The projects file, the Google calls and the
      reconciliation stay in Python — do not grow a second implementation in
      TypeScript, for the same reason the labeling webview drives `referat
      label`

## 12. Extension packaging

**Re-sequenced on 2026-09-01 to run after step 15**, which rewrites the extension
from a TreeView into a sidebar webview. Nothing in this step changes — packaging a
view that step 15 deletes is simply wasted work. The number stays where it is
because `SETUP.md` section 12 and the CHANGELOG entries are keyed to it.

- [ ] **`.vscodeignore` does not exist yet**, so `vsce package` would ship
      `src/`, `node_modules/`, `esbuild.mjs` and the source maps inside the
      `.vsix`. `dist/`, `media/`, `package.json` and `README.md` are the whole
      payload — the point of bundling was to make that true
- [ ] `vsce package` producing a `.vsix`
- [ ] SETUP.md paragraph on sideloading it: `code --install-extension
      referat-vscode-x.y.z.vsix`, or the Extensions view's "Install from
      VSIX...". SETUP.md **section 12 already exists** as a paragraph saying
      the extension is not built yet and transcripts are Markdown until it
      is, so this is replacing one paragraph rather than deciding where a
      section goes

## 12b. A global hotword list

Numbered like step 7b, and for the same reason: it belongs *inside* the build
order rather than after it. It needs step 10's prompt work done first — that is
where the decision about what to do with a misheard name is actually written —
and step 13 depends on it, because a project's `glossary` has nowhere to go
until this list exists.

This is the one correction that happens *before* the transcript is written.
Everything downstream of it corrects the notes instead; `hotwords` is the chance
not to need the correction at all. It costs nothing at transcription time — the
terms go into Whisper's prompt, not through another model.

- [ ] `[transcription].hotword_extras` in `config.example.toml` and `config.py`:
      a manual list, for the terms belonging to no project and to no person.
      Empty by default
- [ ] `referat/hotwords.py`: `merge(config) -> list[str]`, the union of the
      names in the known-voices database, every project's `glossary` from
      `projects.json`, and `hotword_extras`. Deduplicated case-insensitively,
      order stable, so two runs over the same meeting build the same prompt
- [ ] It reads the database through `Config.voices_dir()` and the projects file
      through `projects.py`, never by deriving either path itself — the same
      rule that keeps `voices_dir` from drifting back into Dropbox
- [ ] `transcribe_channel` passes the merged list to
      `model.transcribe(..., hotwords=...)`. **There, not in `cli.py`.** The
      tray reaches the pipeline through `transcribe_meeting` and never through
      the CLI, so a merge living in the CLI would apply to `referat rerun` alone
      — and a rerun would then produce a different transcript from the recording
      it came from, which is the one thing a rerun must not do. "Merge logic
      lives in the CLI" means *in Python, never re-derived in TypeScript*: the
      same rule as `list --json`
- [ ] Degrade the way everything else in this pipeline does. An unreadable
      `projects.json`, a missing voices database, a `hotwords` keyword a future
      faster-whisper has renamed: all of them cost hotwords and never a
      transcript. `merge` returns `[]` rather than raising
- [ ] **The 224-token cap.** `hotwords` goes into Whisper's prompt window, so a
      long enough list gets truncated by somebody else's rule at somebody else's
      boundary. Cap it here instead, in a fixed priority order —
      `hotword_extras`, then names, then glossaries — and log what was dropped.
      A cap nobody can see is how this turns into a bug report about one
      specific name that is never heard right
- [ ] `referat label --forget <name>` takes that name out of the list. This is
      automatic *given* that the merge reads the database live rather than
      caching it — so it is a property to **verify**, not to assume, and worth a
      check the day `--forget` is next used. A forgotten person whose name stayed
      in a hotword list would be the privacy posture leaking out through the back
      of the transcription stack
- [ ] `referat hotwords` prints the merged list with the source of each term and
      says what the cap dropped. Needs no optional extra: it is two JSON reads,
      like `list` and `label`
- [ ] Until step 14 there is no `projects.json` and no `glossary`. `merge` treats
      both as absent and contributes nothing from them, so this step builds and
      runs complete on its own. **The projects file moved from step 13 to step
      14 on 2026-09-01 and became JSON**; that is the only thing this step cares
      about, and it can be built before or after 14 either way

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

- [ ] A new `digest` extra: `google-api-python-client` and whatever the auth
      decision needs. Small — tens of megabytes, not the three gigabytes of
      `transcribe` — but still optional, because `project list` and `project
      assign` must work without it
- [ ] `referat/gdocs.py`: auth, `documents.create`, `documents.get`,
      `documents.batchUpdate`, `files.list`. The only module that touches the
      network
- [ ] `referat project link <name>` offers **Create new doc**:
      `documents.create(title="<Project> Meeting Digest")`. Summaries go in the
      doc's default tab; read its `tabId` back from `documents.get` and store it
      rather than relying on the default, so every later write is located
      explicitly
- [ ] …or **Select existing doc**: search Drive with `files.list`,
      `q = "mimeType='application/vnd.google-apps.document' and name contains
      '<query>' and trashed=false"`, `orderBy=modifiedTime desc`, printed as a
      numbered list in the CLI and as a live QuickPick in the extension
- [ ] After selecting an existing doc, look for a tab whose
      `tabProperties.title` is `Meetings`. **Tabs cannot be created through the
      API** — there is no `createTab` request — so if it is missing, open
      `https://docs.google.com/document/d/<id>/edit`, say to add a tab named
      `Meetings`, and re-check when told to. Store `gdoc_id` and `tab_id`
- [ ] `referat project unlink <name>` clears `gdoc_id` and `tab_id` and leaves
      the doc exactly as it is. Unlinking is not deleting
- [ ] Linking ends by running a sync, which is what makes linking an *existing*
      doc backfill every already-assigned meeting automatically

### Every write is located by `tab_id`

- [ ] `documents.get` is always called with `includeTabsContent=True`, and every
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

- [ ] Each meeting's block starts with an **anchor paragraph** whose text is
      `[referat:2026-08-27_1400]`, styled small and gray. A block runs from its
      anchor's start index to the next anchor's start, or to the end of the tab
- [ ] A visible text anchor rather than a Docs **named range**: named ranges are
      the API's own mechanism and are the fragile choice — invisible to a person
      editing the doc, destroyed along with their content, and not carried by a
      copy of the document. A text anchor survives everything except deleting
      that line, and deleting it only makes the reconciler re-append the block
- [ ] `referat project sync <name>`: `documents.get` the tab, scan the anchors,
      and diff them against every meeting assigned to the project that has a
      `notes.md`, sorted by meeting id — `YYYY-MM-DD_HHMM` sorts chronologically
      by construction, so no date parsing is needed to order them
- [ ] **Missing** — no anchor for an assigned meeting: render and insert it
      before the first existing anchor that sorts after it, or at the end. That
      is what puts a backfilled meeting in date order instead of at the bottom
- [ ] **Stale** — `sha256` of the current `notes.md` differs from the
      `digest.notes_sha256` recorded in `meta.json`: delete the block's range and
      insert the re-rendered block at that index. This is the whole reason
      `/cleanup` can be re-run
- [ ] **Orphan** — an anchor for a meeting no longer assigned: leave it, and
      report it. The doc may be shared and somebody may have written around that
      block; deleting on their behalf is not Referat's call. `sync --prune` is
      the explicit opt-in
- [ ] **Apply block operations in reverse document order**, one `batchUpdate`
      per block. Every insert and delete shifts every index after it, so working
      back to front keeps the indices from the single `documents.get` valid.
      This is the single most likely thing in step 13 to be gotten wrong
- [ ] Write each meeting's `digest` block to `meta.json` through
      `paths.write_json_atomic` as its block lands, not in one pass at the end,
      so an interrupted sync resumes cheaply instead of rewriting the doc
- [ ] Run a sync at the end of `/cleanup`'s follow-up too — or at least document
      that `notes.md` changing is what makes a block stale, so nobody expects the
      doc to update itself

### Markdown to Docs

- [ ] `referat/digest.py`: the translator, the anchor scan and the diff. Pure —
      no Google import, so the part carrying all the index arithmetic is
      testable without a network or the `digest` extra
- [ ] **No raw Markdown text may ever appear in the doc.** The input is the
      constrained subset `/cleanup` emits: `##` / `###`, `**bold**`, `*italic*`,
      `` `code` ``, `-` bullets one level deep, `[text](url)`, `[[Wikilinks]]`,
      paragraphs. Anything outside that is passed through as plain text rather
      than guessed at
- [ ] Two passes: build the block's plain text with a running offset, recording
      `(start, end, style)` spans, then emit **one `insertText` followed by N
      style requests** in a single batch. Style requests do not move indices, so
      the spans stay valid — which is exactly why it is one insert and not one
      per span
- [ ] **Docs indices are UTF-16 code units, not Python characters.** One emoji
      in a note shifts every span after it. Offsets are
      `len(s.encode("utf-16-le")) // 2`, and there should be a test with an
      emoji in it
- [ ] `createParagraphBullets` converts *existing* paragraphs into a list, so
      the inserted text must not contain the `- ` itself. A second nesting level
      is a leading tab character
- [ ] The block's date line is Heading 3, so `notes.md`'s own `##` becomes
      **Heading 4** and `###` becomes **Heading 5**. Its `#` H1 is consumed into
      the date line's title and not emitted twice
- [ ] `[[Wikilinks]]` have no target in a Google Doc: render them as bold text
      with the brackets stripped. A reader outside this machine should see
      `Anna`, not `[[Anna]]`
- [ ] Acceptance check on the produced plain text: no `**`, no leading `#`, no
      leading `- `, no `](`, no `[[`. Any hit is a translator bug, and it is
      cheap enough to assert on every render

### Date headers

- [ ] Each block's first content line is Heading 3, text `YYYY-MM-DD — <title>`,
      em dash. The title is the H1 of `notes.md` falling back to the meeting id —
      the same rule as `referat index`, sharing that helper rather than growing
      a second reader of the same file
- [ ] The Docs API cannot insert an @-date smart chip; there is no request type
      for it. Keep the date as a fixed-width prefix in leading position, so that
      if the API ever gains one, swapping it for a chip is a one-request change
      to this line and leaves the ` — <title>` remainder alone. Noted in
      `CLAUDE.md` too

### meta.json

- [ ] ~~`project`: the project slug, absent when unassigned~~ **Superseded by step
      14's `tags`**, a list of project ids. It is a list because a meeting may
      belong to several threads of work, and it holds *ids* rather than names so
      a rename touches one file. Absent or empty means untagged
- [ ] `digest`: **keyed by doc**, `{"<gdoc_id>": {tab_id, notes_sha256,
      written_at}}` — what was written, where, and from which bytes of
      `notes.md`. It was a single object when a project had one doc; a meeting
      can now be current in one doc and stale in another, and one flat object
      could not say so. That is still what makes reconciliation and a correction
      cheap: the sync reads `meta.json` and one `documents.get` per doc, and never
      has to re-read the doc's prose to work out what changed
- [ ] `status` reaches `synced` when **every** doc of **every** tag is current,
      and drops back to `notes_written` the moment a `notes.md` sha stops
      matching. The lifecycle field is step 14's; this is the step that writes its
      last two transitions
- [ ] The folder contract table does not change. No new file appears in a
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

## 14. Projects as labels: the data model and the CLI

**Everything else renders this or calls it, so it is first.** A project stops
being a container a meeting is put into and becomes a label a meeting carries: a
meeting has zero or more of them, and *untagged* is the computed state of an
empty list, never a project called "untagged".

### `projects.json`

- [ ] `<meetings_dir>/projects.json`, owned by a new `referat/projects.py`. Per
      project: `id`, `name` (what a person reads), `docs` (a list of `{gdoc_id,
      tab_id, tab_name, linked_at}`), `description` (one line, reserved for step
      17 and written by nothing until then), `created_at`
- [ ] **JSON rather than the `projects.toml` step 13 specified.** That choice was
      made to keep `config.py`'s promise — Referat parses TOML and never writes it
      back, so hand edits and comments survive — while still having a
      machine-written file. JSON drops the tension instead of managing it: it goes
      through the `paths.write_json_atomic` that already exists, it is rewritten
      whole without apology, and it needs no header comment warning that comments
      do not survive. `projects.py` still owns it and is still **not** registered
      in `config._SECTIONS`; the promise is about `config.toml` and stays true
- [ ] Beside `.voices/` and the generated `INDEX.md`, so the meetings folder stays
      self-describing and the extension finds it from the one path it already has
- [ ] **The id is a slug made from the name at creation and immutable after
      that.** `rename` changes `name` and nothing else. That is the entire reason
      `meta.json` stores ids rather than names — a rename touches one file, and
      every transcript, every meeting record and every doc anchor is untouched by
      it. Collisions take a `-2` suffix, the way meeting ids do
- [ ] What a project name may be is decided in **one** place. Reuse
      `voices.name_complaint` if its rules fit; give `projects.py` a sibling if
      they do not. Two validators would eventually disagree about the same string

### `meta.json` gains `tags`

- [ ] `tags`: a list of project ids. Absent or `[]` is untagged. `Meeting.load` is
      already tolerant of missing keys, so every meeting recorded so far loads as
      untagged and there is nothing to migrate
- [ ] **Deleting a project orphans its tags, visibly, and cascade-deletes
      nothing.** `referat project rm` removes the project from `projects.json` and
      touches no `meta.json`, no `notes.md` and no Google Doc. An id left behind in
      `tags` with no project to resolve it is rendered as an orphan chip rather
      than hidden, because a tag quietly vanishing off three meetings is how you
      lose track of what a meeting was about

### An explicit lifecycle, in the `status` field that already exists

- [ ] Widen `MeetingStatus` in `referat/meeting.py` rather than adding a second
      field beside it:

      ```
      recording -> recorded -> transcribing -> gate_failed | transcribed
                                                          -> notes_written -> synced
      failed  (transcription raised — a different thing from gate_failed)
      ```

      One field, one authority. A parallel `state` key would be a second place to
      say what a meeting is, and this file already records `voices_dir` and
      `format_duration` each being pulled back from exactly that
- [ ] **`gate_failed` is the point of the exercise.** It is today a three-way
      inference — `status == done` **and** the WAVs still on disk **and** the
      folder still in staging — computed nowhere, re-derived by every reader, and
      impossible to render honestly. It becomes a value the pipeline writes
- [ ] `Meeting.load` maps the legacy values **purely**, with no filesystem
      inspection: `"stopped" -> recorded`, `"done" -> transcribed`. It must not
      look at the folder to decide a meeting was gate-failed; that is the
      inference being removed, and doing it in the loader would simply hide it
- [ ] Write sites to move: `recorder.py:562` (`STOPPED` → `RECORDED`),
      `transcribe.py:874` (`DONE` → `TRANSCRIBED`), and a new `GATE_FAILED` where
      `release_audio_if_clean` comes back False. The read gate at
      `transcribe.py:788` tests `status is DONE` and must test `TRANSCRIBED`
- [ ] The few `done`-with-audio-kept meetings already on this machine are **not**
      auto-corrected. Their next `rerun` writes the right value, and there are
      three of them. A migration would be more code than the problem
- [ ] `cli.audio_state` stays exactly as it is. It reports *audio*, which is a
      real question a person asks; what it stops doing is standing in for the
      lifecycle
- [ ] **No UI infers state from which files exist.** All three — the table, the
      dashboard, the sidebar — render this field

### The CLI owns every mutation

- [ ] `referat project add <name> | rename <id> <name> | rm <id> | link-doc <id> |
      unlink-doc <id> <gdoc_id> | list [--json]`
- [ ] `referat tag <meeting-id> <project-id>...` and `referat untag <meeting-id>
      <project-id>...`, both idempotent and both taking several ids at once
- [ ] `referat state <meeting-id> notes-written` — the one transition no other
      process can make, because `/cleanup` is forbidden from touching `meta.json`
      and that rule is not moving. **It accepts that transition and no other**,
      and only from `transcribed`: `synced` is written by `project sync`,
      `gate_failed` and `transcribed` by the pipeline. A verb that let a caller
      claim any state would turn the field from a record into a comment
- [ ] **None of these need an optional extra** — `project add|rename|rm|list`,
      `tag`, `untag` and `state` are a JSON read and a JSON write. That is step
      8's rule, widened rather than moved. Only `link-doc` and `sync` need
      `digest`, and only `rerun` needs `transcribe`
- [ ] They go in the existing `argparse` subparser block in `cli.py` and the same
      `if args.command == ...` chain. `project` takes a second positional verb;
      resist growing a second dispatch mechanism for it
- [ ] **The extension and the tray call these. Neither reimplements them.** The
      projects file, the tag logic, the lifecycle vocabulary and the name rules
      live in Python once, for the same reason `format_duration` and
      `voices.unknown_speakers` do

### `referat list` and `list --json`

- [ ] The text table gains a `TAGS` column and prints the wider `STATUS`
      vocabulary. `HEADERS`, `RIGHT_ALIGNED`, `list_row` and `list_document` move
      together
- [ ] `list_document` gains `"tags": [...]` per meeting and **one top-level
      `"projects"` block**, id to display name, produced by the same function
      `project list --json` calls. One subprocess then feeds the whole sidebar,
      and the join between a tag id and its name cannot drift
- [ ] `"status"` keeps its key and simply carries more values, so the extension's
      `MeetingJson` in `src/cli.ts` widens a union rather than growing a field
- [ ] `index.py`'s dashboard picks the new status values up for nothing, and
      should be read once afterwards to check they render as sentences a person
      wants to see in `INDEX.md`

## 15. The extension becomes the primary UI

**Replaces the TreeView built in step 11.** `MeetingsProvider`, the three node
classes, the composed `contextValue` string, the five `view/item/context` entries
and `labelPanel.ts` all go. A webview inside the extension is **not a web UI** —
that rule is about Flask, FastAPI, localhost and a browser front end, and there is
still none of it.

### The sidebar

- [ ] One `views` entry, `{"id": "referat.meetings", "name": "Meetings", "type":
      "webview"}`, with `activationEvents` following it
- [ ] One row per meeting, **reverse chronological** — the tree already reverses
      `referat list`'s oldest-first order, and that stays a presentation choice.
      Do not reorder the CLI
- [ ] Each row: date, duration, project tag chips, and a **lifecycle strip**
      rendering step 14's `status` field. Nothing in TypeScript re-derives a state
- [ ] **A visible off-ramp for gate-failed meetings stuck in staging**, and this
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
- [ ] Speaker labeling folds into an expandable section of the row. It still
      drives `referat label --speaker --name` and still reimplements no part of
      `label.apply_name`; `media/label.css` and `media/label.js` become the
      sidebar's assets
- [ ] Keep the file watcher exactly as it is — `RelativePattern` over **both**
      roots, 300 ms debounce — and keep `cli.ts`'s spawn path exactly as it is:
      `<repoRoot>\.venv\Scripts\python.exe -m referat.cli` with the working
      directory at the repository root. Both were hard-won and neither changes

### Project CRUD and the tag picker

- [ ] Create, rename, delete, attach and detach doc references, every one of them
      a shell-out to `referat project ...`
- [ ] The tag picker is a `showQuickPick` with `canPickMany` over the `projects`
      block `list --json` already returned, pre-checked with the meeting's current
      tags, with **"Create project '<typed>'" as the last entry**, driven by
      `onDidChangeValue`. Applying a pick is a `tag` and an `untag` for the
      difference
- [ ] Multi-tag editing lives here and only here, and so does backfilling the
      untagged: a filter that shows every meeting with an empty `tags`, worked
      through with the same picker. That view *is* the queue, the way "Unknown
      speakers" is the queue for `referat label`

### Notes, and ambient state

- [ ] *Generate notes* streams `claude`'s stdout: the last non-empty line into
      `progress.report({message})`, the whole stream into the output channel. On
      exit 0 it calls `referat state <id> notes-written` and opens `notes.md`
      rendered
- [ ] Unchanged and worth restating because this is the file that describes it:
      `claude` is resolved at **spawn time** and never persisted, in the order
      setting → `Anthropic.claude-code` extension path → `PATH`; it is spawned
      with `--allowedTools "Read,Write,Glob"`; and **the extension never handles
      credentials**. There is no Anthropic API key in this project, in any file,
      in any setting or in any environment variable
- [ ] A **status bar item** for ambient state — "recording 12:34",
      "transcribing 2" — from `referat status`, which already reports the tray's
      state, meeting id and job count, and already refuses to be blocked by a
      broken `config.toml`
- [ ] **Still no meetings-folder setting.** `referat.repoRoot` and
      `referat.claudeBinary` stay the only two; where meetings live is
      `[paths].meetings_dir` in the repository's `config.toml`, and a second place
      to say it is a second thing that can disagree with the recorder

## 16. Tagging from the tray

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
- [ ] Native tray menus are enough. **No Qt**, no second GUI toolkit
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

## Surfaced later

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
      alternative, and it costs disk on every meeting to serve a rare case
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
      whether that is fine (it reads fine) or whether the two lines should merge

- [x] **Dropbox would have synced the meetings folder while a meeting recorded.**
      `mic.wav` and `system.wav` are appended continuously with incremental header
      rewrites, ~460 MB an hour across both, and only deleted once the quality gate
      passes. Fixed before it ever ran: `[paths].staging_dir` records and
      transcribes outside the synced tree, and the meeting moves in only once its
      audio is gone. The deletion mattered more than the upload — Dropbox keeps
      deleted files and old versions on its servers for weeks, so `audio_released`
      would have been a lie about recordings of people who never consented
- [ ] A meeting whose audio is kept now stays in staging indefinitely. `referat
      list` marks it and says to rerun, but nothing prunes: a meeting that never
      transcribes cleanly sits in `%LOCALAPPDATA%` forever. Decide whether that
      wants a `referat rerun --accept` that promotes it with the audio, or just a
      line in `list` old enough to nag. **Answered on 2026-09-01, and half of it
      was the wrong question**: step 15 gives it a visible off-ramp in the
      sidebar, and step 14 gives it a `gate_failed` status to be found by — but
      *accept* cannot promote it "with the audio", because no WAV may ever reach
      the meetings folder. It has to delete the WAVs first, which makes it an
      irreversible action behind a modal rather than a convenience
- [ ] **SETUP.md has never been followed on a machine that did not already have
      all of this.** It was written from this laptop's history — every command in
      it was run, and every claim cross-checked against the code — so the one
      thing it cannot prove is that nothing is *missing*. Read it against a clean
      Windows install the first time there is one
- [ ] **How step 13 authenticates to Google is unresolved**, deliberately, and
      recorded rather than decided. A **desktop OAuth client** means one browser
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
