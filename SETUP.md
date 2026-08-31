# Setting Referat up on a Windows 11 machine

What you end up with: a tray icon that starts with Windows, two buttons that
start and pause a meeting, and a folder that fills up with timestamped,
speaker-named Markdown transcripts — with nothing leaving the machine.

Budget half an hour of attention plus two large downloads that run unattended.
This is a personal tool for one machine and one user account, and this guide
assumes it. [README.md](README.md) is the short version; this file is the one
that covers the parts that go wrong.

---

## 1. Before you start

- **Windows 11.** Referat is Windows-only by design — WASAPI loopback,
  `%LOCALAPPDATA%` and Win32 calls are used directly, with no portability layer.
- **An NVIDIA GPU**, for the fast path. Everything works without one; the CPU
  fallback is just slower and uses a smaller model.
- **Git**, and a clone of this repository.

Check the driver:

```powershell
nvidia-smi
```

The CUDA version in the header must be **12.8 or newer**. Referat pins torch to
the `cu128` build because the RTX 5070 Ti Laptop GPU is Blackwell (sm_120), and
the default PyPI torch wheels do not support it. An older driver is a driver
update, not a Referat problem.

---

## 2. Python 3.12 and uv

```powershell
winget install --id astral-sh.uv
```

**Then open a new terminal.** WinGet puts `uv` on the persisted *user* PATH, but
a shell that was already open never receives it. This has been diagnosed twice
here as "uv is not installed" — it always was. A new terminal is the entire fix.

```powershell
cd <your clone>
uv sync                       # tray and audio only: small and fast
uv sync --extra transcribe    # plus faster-whisper, torch cu128, pyannote (~3 GB)
uv run referat --version
```

`.python-version` pins 3.12 and `uv` provisions it for you. Do not substitute the
system Python: 3.13 and 3.14 are too new for the torch, CTranslate2 and pyannote
wheels this project needs.

The base install is deliberately kept to the tray and the audio capture, so a
plain `uv sync` gives you a working recorder in seconds. You need
`--extra transcribe` before anything is transcribed.

---

## 3. First run, and `config.toml`

```powershell
uv run referat config
```

That creates `config.toml` in the repository root from
[config.example.toml](config.example.toml), creates the meetings folder, and
prints the configuration it just loaded. `config.toml` is gitignored and is the
file you actually edit; the example file is the template and the documentation.

Referat parses TOML and never writes it back, so your comments and hand edits
survive. The one exception is that first copy.

| Thing | Where |
| --- | --- |
| Configuration | `<repo>\config.toml`, or wherever `REFERAT_CONFIG` points |
| Log | `%LOCALAPPDATA%\Referat\referat.log` |
| Tray status | `%LOCALAPPDATA%\Referat\status.json` |
| Recording in progress | `%LOCALAPPDATA%\Referat\recording` (`[paths].staging_dir`) |
| Finished meetings | `[paths].meetings_dir`, `~\Meetings` by default |

**One TOML rule worth knowing before it bites you.** In a double-quoted TOML
string a backslash starts an escape, so `"C:\Users\..."` fails to parse before
Referat ever sees it. Use single quotes for Windows paths:

```toml
meetings_dir = 'C:\Users\you\Dropbox\Research\Meetings'
```

---

## 4. Choosing the microphone

```powershell
uv run referat devices
```

This lists every input device and every WASAPI loopback source, and marks the one
`[audio]` currently resolves to with `<- config`.

**Name your microphone even when it is already the default.** Two reasons, and
both are quiet failures:

- `mic_device` and `loopback_device` are case-insensitive *substring* matches,
  and a substring that matches nothing is not an error — it falls back to the
  default and says so only in a log line nobody reads until after the meeting.
  `referat devices` says so above the table instead, which is why the command
  exists.
- PortAudio exposes the same microphone once per host API, and the copies differ
  in ways that matter. On this machine the Jabra Speak 510 appears four times;
  the *default* is its MME copy at 44100 Hz, while naming it selects the WASAPI
  copy at 16000 Hz mono — exactly `mic_samplerate`, so nothing has to resample.

```
INDEX  NAME                                  HOSTAPI              CH   RATE
    1  Microphone (Jabra SPEAK 510 USB       MME                   1  44100  (default)
   13  Microphone (Jabra SPEAK 510 USB)      Windows DirectSound   1  44100
   33  Microphone (Jabra SPEAK 510 USB)      Windows WASAPI        1  16000  <- config
   43  Microphone (Jabra SPEAK 510 USB)      Windows WDM-KS        1  16000
```

```toml
[audio]
mic_device = "Jabra"
loopback_device = ""
```

**Leave `loopback_device` empty on purpose.** Empty means "loopback of whatever
the current default output device is", so system audio follows whatever is
actually playing — which is what you want when you move between headphones and
speakers mid-day.

---

## 5. The USB buttons

Referat is driven by two global hotkeys, meant to be emitted by programmable USB
buttons:

```toml
[hotkeys]
toggle_record = "ctrl+alt+f9"    # start / stop a meeting
toggle_pause  = "ctrl+alt+f10"   # pause / resume
```

Program the buttons to send those two combos, in the
[`keyboard`](https://github.com/boppreh/keyboard) library's syntax. A held
button auto-repeats, and the hotkeys are debounced for exactly that reason — a
slightly long press will not start and immediately stop a recording.

The combos are configurable; the only rule is that the two must differ.

---

## 6. Meetings, staging, and the voiceprints

### If your meetings folder is synced

Transcripts and notes are worth having in Dropbox or OneDrive. Two things must
stay out of it.

- **`[paths].voices_dir`** — the known-voices database. Set it somewhere local.
- **`[paths].staging_dir`** — where a meeting is recorded and transcribed. It is
  outside the synced tree by default and must stay that way.

```toml
[paths]
meetings_dir = 'C:\Users\you\Dropbox\Research\Meetings'
staging_dir  = "~/.referat/recording"
voices_dir   = "~/.referat/voices"
```

**The check is `uv run referat config`**: read the paths back and confirm that
only `meetings_dir` is inside the synced folder.

```
[paths]
  meetings_dir = C:\Users\nikla\Dropbox\Research\Meetings
  hf_token_file = C:\Users\nikla\.referat\hf_token
  staging_dir = C:\Users\nikla\.referat\recording
  voices_dir = C:\Users\nikla\.referat\voices
```

Recording never writes into the meetings folder at all. A meeting is created in
staging, recorded there, transcribed there, and moved in only once its WAVs have
been deleted — so a sync client never sees a WAV. That matters more than the
bandwidth: the audio is ~460 MB an hour, and **deleting a file inside a synced
folder does not delete it.** Dropbox keeps deleted files and prior versions on
its own servers for weeks, so `audio_released` in `meta.json` would be a lie
about recordings of people who never asked to be recorded.

This is configuration rather than a per-folder sync exclusion because an
exclusion has to be re-applied by hand every time the folder is recreated, and
fails silently when it is not.

### The known-voices database is biometric personal data

`voices.json` holds a voiceprint for every person Referat has ever been told the
name of — people who never asked to be in a database.

- **Exclude it from every sync client and every backup tool on the machine.**
  Putting it outside the synced tree, as above, is the reliable way.
- It never leaves the machine. It is not sent anywhere, and the `/cleanup` pass
  is denied read access to it by `<meetings_dir>/.claude/settings.json`, seeded
  with the rest of the scaffold (section 11a). That rule binds the file tools;
  it does not bind a shell, which is why `/cleanup` is spawned without one.
- **`uv run referat label --forget <name>`** is how a person is removed, and it
  is a real deletion rather than a tombstone: the embeddings go, and their labels
  revert to `SPEAKER_NN` in every transcript that carried them.

---

## 7. The Hugging Face token

Needed only for speaker diarization — splitting the remote channel into
individual speakers. Skipping this section is a supported state: you lose speaker
names, never transcripts.

1. Create a Hugging Face account.
2. **Accept the conditions** at
   <https://hf.co/pyannote/speaker-diarization-community-1>. The checkpoint is
   gated, and the token has to belong to an account that has accepted it.
3. Create a read token.
4. Write it to the file named by `[paths].hf_token_file` — `~/.referat/hf_token`
   by default — as plain text:

```powershell
New-Item -ItemType Directory -Force "$HOME\.referat" | Out-Null
[IO.File]::WriteAllText("$HOME\.referat\hf_token", "hf_xxxxxxxxxxxxxxxx")
```

Use `WriteAllText`, not `Set-Content` or `>`. Windows PowerShell 5.1 writes
UTF-16 with a BOM through both, which is not what "plain text" usually means.
Referat sniffs the BOM and copes with it, but the file is easier to reason about
when it is ASCII.

### How you find out this went wrong: you do not, from the transcript

An unaccepted repository is a `403`, and a `403` costs speaker labels and
nothing else. The transcript still arrives, on time, with every remote line
labeled `REMOTE:` instead of `SPEAKER_01`, `SPEAKER_02`, … So does a missing
token, an empty `diarization_model`, and any other failure in the pipeline —
diarization is built so that it can never cost anything but names.

Where it says so is `meta.json`:

```
transcription -> channels -> system -> diarization -> reason
```

If a meeting comes back with undifferentiated `REMOTE:` labels, read that key
before anything else.

---

## 8. The first transcription

Stop your first meeting and expect to wait. Three things look like a hang and are
not.

- **`large-v3` downloads about 3 GB** from Hugging Face the first time a meeting
  is transcribed on the GPU, and takes a few minutes.
- **`medium` downloads separately** the first time the CPU fallback is taken.
- **The pyannote checkpoint downloads** the first time diarization runs.

You will also see a warning that the Hugging Face cache cannot use symlinks and
is storing model files duplicated. It is harmless — it costs disk, not
correctness. Enabling Windows Developer Mode removes both the warning and the
duplication.

### cuDNN and cuBLAS

There is nothing to install, and this note exists so you recognise it if it ever
breaks. faster-whisper runs on CTranslate2, which links cuDNN 9 and cuBLAS but
ships neither. The `cu128` torch wheel already carries both, and
`transcribe.ensure_cuda_dlls` puts torch's `lib` directory on the DLL search path
before the first CUDA model is built.

If that ever fails, the symptom is **not** an error. Any failure on the CUDA path
falls back to CPU, so what you actually see is a transcript that took far too
long and a `meta.json` reading:

```json
"transcription": { "model": "medium", "device": "cpu" }
```

A GPU machine that quietly transcribes on `medium` is the tell.

---

## 9. Autostart

```powershell
uv run python scripts/install_autostart.py
```

That writes `Referat.lnk` into your Startup folder, targeting this checkout's
`.venv\Scripts\pythonw.exe` with `-m referat.tray`, so the tray comes up at
sign-in with no console window. The installer prints exactly what it wrote:

```
Installed C:\Users\you\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Referat.lnk
  target:  C:\...\referat\.venv\Scripts\pythonw.exe
  args:    -m referat.tray
  workdir: C:\...\referat
  config:  C:\...\referat\config.toml
```

| Command | What it does |
| --- | --- |
| `install_autostart.py` | Installs it. Running it again when it is already current changes nothing. |
| `install_autostart.py --status` | Prints what is installed. Exit 0 when current, 1 when not. |
| `install_autostart.py --uninstall` | Removes it; the tray no longer starts at sign-in. |
| `--force` | Replaces, or removes, a shortcut that points somewhere else. |

Without `--force` the installer refuses to touch a `Referat.lnk` aimed anywhere
but this checkout, and refuses to delete one it did not write.

Four things worth knowing:

- **You can still launch the tray by hand.** A named mutex means an autostarted
  tray and a manual launch cannot fight over the hotkeys — the second one logs
  "already running" and exits.
- **Re-run the installer if you move or rename the repository.** The shortcut
  holds an absolute path into `.venv\Scripts`, and nothing detects a stale one:
  the tray simply never starts. `--status` is the check.
- **A shortcut cannot set an environment variable.** If you keep your config
  somewhere else via `REFERAT_CONFIG`, it reaches the autostarted tray only when
  it is a persisted *user* variable (`setx REFERAT_CONFIG "<path>"`), not one set
  in a shell. The installer reads both and warns you when they disagree.
- **Under `pythonw.exe` a bad `config.toml` is completely silent.** There is no
  console for the error and it happens before logging is set up, so you get no
  window, no icon, and no log line. If the tray does not appear after sign-in,
  run `uv run referat config` — that is the diagnostic.

---

## 10. Two things Referat will not change for you

### Sleep, and the lid

While recording or paused, Referat holds
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` so Windows will not
idle-sleep out from under a meeting. The hold is dropped on stop, so a long
transcription never keeps the laptop awake.

**That suppresses the idle timer and nothing else.** Closing the lid, or choosing
Sleep, still suspends the machine mid-meeting. If you record with the lid closed,
change it yourself:

```powershell
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```

Or **Settings → System → Power & battery → Lid, power and sleep button
controls**. Referat will not rewrite a system-wide power setting on your behalf.

### Smart App Control

If Smart App Control is enforcing, it blocks the unsigned FFmpeg DLLs that PyAV
and `torchcodec` bundle — which would otherwise make `import faster_whisper` fail
outright.

**You do not have to do anything.** Referat decodes its own WAVs with the stdlib
`wave` module and hands pyannote an in-memory waveform, so neither library ever
reaches for its bundled decoder. This note exists to explain why that workaround
is there.

Turning Smart App Control off is a **one-way** change — Windows cannot re-enable
it without a reinstall — so it is documented here and not recommended.

---

## 11. Checking the whole thing

```powershell
uv run referat --version                              # referat 0.1.0
uv run referat config                                 # paths, and the sync check from section 6
uv run referat devices                                # your microphone marked `<- config`
uv run referat status                                 # "Referat is not running." until you start it
uv run python scripts/install_autostart.py --status   # Installed, exit 0
uv run referat list                                   # empty until your first meeting
uv run referat index                                  # writes <meetings_dir>/INDEX.md
```

Then record something:

1. Start the tray: `uv run referat-tray`, or `uv run python -m referat.tray` when
   you want the log on screen.
2. Press `ctrl+alt+f9`. The icon turns red.
3. Talk for a minute or two, with something playing over the system audio if you
   want to exercise the loopback channel.
4. Press `ctrl+alt+f9` again. The icon goes blue while it transcribes.
5. `uv run referat list` — one row, status `done`, and an `UNNAMED` count if
   diarization found remote speakers.
6. Open the meeting folder and read `transcript.md`, then `meta.json`.
7. `uv run referat label <meeting-id>` plays each unidentified speaker and asks
   who it was. From then on Referat knows that voice.
8. Open the meetings folder in VS Code. `INDEX.md` shows the meeting, rendered.
   Run `/cleanup <meeting-id>` in a Claude Code session there to write its
   `notes.md`, then `uv run referat index` to put the title in the table — see
   section 11a.

---

## 11a. The meetings folder, and writing notes

On first run Referat seeds a small scaffold into `meetings_dir`, from
`templates/meetings/` in the repository:

```
CLAUDE.md                      what the files are, and what may not be edited
INDEX.md                       generated dashboard, one row per meeting
.claude/commands/cleanup.md    the /cleanup slash command
.claude/settings.json          denies all access to .voices/
.vscode/settings.json          opens Markdown rendered in that workspace
```

**Every one of those files is seeded once and never overwritten.** Edit them in
place — the `/cleanup` prompt in particular is meant to be refined — and Referat
will leave your version alone forever. The cost is that the repository copy and
the live copy drift apart; reconciling them is a manual `copy` when a change is
worth carrying across.

**Open the meetings folder in VS Code** and `INDEX.md` is the dashboard: because
of that `.vscode/settings.json`, Markdown opens rendered rather than as source,
so the table is a page of links. It is regenerated at the end of every
transcription; `uv run referat index` rebuilds it on demand, which is what you
want after writing notes.

**Notes are written on request, never automatically.** In a Claude Code session
opened in the meetings folder, run:

```
/cleanup 2026-08-28_1152
```

It reads that meeting's `transcript.md` and writes `notes.md` beside it — title,
decisions, action items, discussion — and touches nothing else. The transcript,
`meta.json` and the WAVs are never modified, and it will not put a real name on a
`SPEAKER_NN`: that is `referat label`'s job, and guessing from the words is how a
wrong name ends up looking authoritative. Re-running it is fine and replaces the
notes.

The H1 on the first line of `notes.md` is the meeting's title everywhere a title
appears, so `referat index` after a `/cleanup` run is what makes the dashboard
read as something other than a column of timestamps.

### If `claude` is not on your PATH

It may well not be. Installing the Claude Code **VS Code extension** does not put
a `claude` on your PATH: the binary ships inside the extension itself, under
`~/.vscode/extensions/anthropic.claude-code-<version>-win32-x64/resources/native-binary/`.
That is fine for running `/cleanup` from a Claude Code session inside VS Code,
which is the normal way to use it, and it is why nothing in Referat shells out to
`claude` yet.

If you want `claude` at a PowerShell prompt as well, the route that changes no
PATH at all is npm, because `%APPDATA%\npm` is already on your user PATH:

```powershell
npm i -g @anthropic-ai/claude-code
claude --version
```

`claude install` is the other option, and it is the one that does want a PATH
change: it installs into `~\.local\bin`, which is not on the PATH here.

**Do not hardcode the path into the extension's own copy.** It carries a version
number and the old directory is deleted on the next extension update — a path
that worked while step 10 was being built was gone two days later, when it was
committed.

---

## 12. The VS Code extension

Not built yet — it is build step 12. Until then transcripts are ordinary Markdown
files you can open in anything, `INDEX.md` is the browsing surface (section 11a),
and the CLI (`list`, `status`, `rerun`, `label`, `index`) is the whole interface
above the tray icon.

When it exists, this section becomes one command:
`code --install-extension referat-vscode-x.y.z.vsix`.
