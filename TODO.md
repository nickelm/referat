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

- [ ] `state.py`: idle → recording → (paused ↔ recording) → stopped →
      transcribing → idle, with legal-transition enforcement
- [ ] Log every state transition
- [ ] `hotkeys.py`: register the two configurable combos via `keyboard`
- [ ] `tray.py`: `pystray` icon, colors per state (gray idle, red recording,
      yellow paused, distinct transcribing state)
- [ ] Tray menu: open meetings folder, open config, quit
- [ ] Write `status.json` on every transition (for `referat status`)
- [ ] `referat-tray` gui-script entry point in `pyproject.toml`
- [ ] Verify the programmed USB buttons drive the state machine (no audio yet)

## 3. Dual-stream WAV recording

- [ ] `meeting.py`: meeting record and `meta.json` read/write
- [ ] Mic capture via `sounddevice`, mono 16 kHz, streamed to `mic.wav`
- [ ] System audio via WASAPI loopback (`PyAudioWPatch`), downmixed to mono,
      streamed to `system.wav` at the device's native mix rate
- [ ] Device selection by config substring, falling back to the defaults
- [ ] Crash-safe writes: incremental WAV header updates, periodic flush
- [ ] Pause stops appending samples; pause intervals recorded in `meta.json`
- [ ] Verify a killed process mid-meeting still leaves playable WAVs

## 4. Sleep prevention

- [ ] `power.py`: `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`
      via `ctypes`
- [ ] Held only while recording or paused; released on stop
- [ ] Transcription runs without the hold
- [ ] Verify with `powercfg /requests`

## 5. Transcription — mic channel

- [ ] `transcribe.py`: `faster-whisper` large-v3 on CUDA, background thread
- [ ] Graceful fallback to CPU + medium when CUDA is unavailable
- [ ] Write `transcript.md` from the mic channel alone, `ME` labels
- [ ] Timestamps as elapsed `[HH:MM:SS]`, pauses excluded
- [ ] Record model, device, and compute type into `meta.json`

## 6. Loopback channel and merge

- [ ] Transcribe `system.wav` (resample to 16 kHz)
- [ ] `merge.py`: interleave both channels chronologically
- [ ] `REMOTE:` labels for the loopback channel at this stage

## 7. Diarization

- [ ] `diarize.py`: `pyannote.audio` speaker-diarization-3.1 on `system.wav`
- [ ] Hugging Face token read from the configured file; document the setup step
- [ ] Assign `SPEAKER_01`, `SPEAKER_02`, ... by aligning turns to segments
- [ ] Degrade to `REMOTE:` when no token is present, without failing the run

## 8. CLI

- [ ] `referat list` — date, duration, status
- [ ] `referat rerun <meeting-id>` — re-run transcription and diarization
- [ ] `referat status` — read `status.json`, check PID liveness

## 9. Autostart and setup docs

- [ ] `scripts/install_autostart.py`: Startup folder shortcut via `pythonw.exe`
- [ ] `SETUP.md`: Python 3.12 + uv, CUDA 12.8 driver check, cuDNN/cuBLAS DLLs
      needed by CTranslate2, Hugging Face token and pyannote model gating,
      USB button programming, microphone selection

## Surfaced later

- [ ] Decide whether `keyboard` hotkeys need an elevated process to fire while
      an admin window has focus
- [x] Confirm `PyAudioWPatch` publishes a Python 3.12 wheel; otherwise switch the
      loopback capture to `soundcard` — 0.2.12.8 installs cleanly on 3.12
- [ ] `uv` installed to a WinGet Packages path that is not on this shell's PATH;
      note the full path in SETUP.md or add it to PATH
