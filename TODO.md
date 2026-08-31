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
      rule of needing no optional extra

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
      which does nothing to stop a sync client. `uv sync` repairs it; nothing
      stops it happening again. Decide between telling Dropbox to ignore
      `.venv` (right-click, "Ignore" — or the `com.dropbox.ignored` attribute)
      and moving the checkout out of Dropbox entirely. **This is the same class
      of mistake the meetings folder already hit**, and the same answer: keep the
      thing that must not be synced out of the synced tree
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
      only because step 11 spawns `/cleanup` with `--allowedTools "Read,Write"`
      and no shell — so **`Bash` must never be added to that list**, and if it
      ever is, the deny rule needs `Bash(...)` entries to match. Written into
      `CLAUDE.md`; noted here because it is a footgun in the step 11 code, not in
      this step's
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
- [ ] Settings: meetings folder path, and path to the `claude` binary. **A
      PATH-only default fails on this machine**, where there is no `claude` on
      PATH at all — it lives inside the Claude Code VS Code extension's own
      `resources/native-binary/`. Resolve in this order, at **spawn time**:
      1. `referat.claudeBinary`, when the user has set one;
      2. `vscode.extensions.getExtension("Anthropic.claude-code")`, then
         `extensionPath` + `resources/native-binary/claude.exe`, existence-checked.
         This is the answer that needs no PATH, no config and no npm, and VS Code
         resolves the versioned directory so it follows the extension across
         updates;
      3. `claude` on PATH, for a machine with an ordinary install.
- [ ] **Never persist a resolved absolute path** — not in a setting, not in a
      cache, not in `meta.json`. That is precisely what rotted between the two
      step 10 sessions (see below), and a stored path fails *silently* a week
      later, at the moment somebody clicks *Generate notes*

### The Projects section (step 13's UI lives here)

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

- [ ] `vsce package` producing a `.vsix`
- [ ] SETUP.md paragraph on sideloading it: `code --install-extension
      referat-vscode-x.y.z.vsix`, or the Extensions view's "Install from
      VSIX...". SETUP.md **section 12 already exists** as a paragraph saying
      the extension is not built yet and transcripts are Markdown until it
      is, so this is replacing one paragraph rather than deciding where a
      section goes

## 13. Per-project digests

**Gated on step 10**, not merely on 1-9: a digest block is a meeting's
`notes.md` translated into a Google Doc, and there is no `notes.md` until
`/cleanup` exists. The UI half is written up as a section of step 11.

A *project* is a thread of work spanning many meetings — the thing `/cleanup`
already writes `[[Wikilinks]]` for. Each project may be linked to one Google Doc,
its digest, into which every meeting assigned to it is written as a dated block,
in chronological order. The doc is the shareable artifact; the meetings folder
stays local.

### The projects file

- [ ] `<meetings_dir>/projects.toml`: one table per project, keyed by slug —
      `name`, `gdoc_id`, `tab_id`, `tab_name`, `linked_at`, and `default = true`
      on at most one. A project with no `gdoc_id` is configured but unlinked.
      Beside `.voices/` and the generated `INDEX.md`, so the meetings folder
      stays self-describing and the extension finds it from the one path it
      already has in its settings
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

### Linking a doc

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

- [ ] `project`: the project slug, absent when unassigned
- [ ] `digest`: `{gdoc_id, tab_id, notes_sha256, written_at}` — what was written,
      where, and from which bytes of `notes.md`. That is what makes
      reconciliation and a correction cheap: the sync reads `meta.json` and one
      `documents.get`, and never has to re-read the doc's prose to work out what
      changed
- [ ] The folder contract table does not change. No new file appears in a
      meeting folder; the digest lives in the doc and in two `meta.json` keys

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
      turning SAC off is one-way, and does not recommend it
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
- [ ] Step 11 spawns `claude -p` with `--permission-mode acceptEdits` and
      `--allowedTools "Read,Write"` in a folder full of private meeting
      material, which makes the `/cleanup` prompt the only thing bounding what
      gets written there. Re-read that prompt with this in mind once there are
      real meetings in the folder

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
      line in `list` old enough to nag
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
- [ ] **A shared doc is a much wider blast radius than a shared folder.** The
      digest is the first thing Referat has ever sent anywhere, and what it sends
      is whatever `/cleanup` decided to put in `notes.md` — about people who did
      not read the prompt. Re-read that prompt with this in mind before the first
      digest doc is shared with anybody, and keep the rule that the transcript,
      the audio and `.voices/` never leave the machine at all
