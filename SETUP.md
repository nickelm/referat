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

> **On this machine, read "If Smart App Control blocks uv" below first.** Both
> `uv.exe` and the interpreter uv provisions are unsigned, and Smart App Control
> blocked both on 2026-08-31 — then gave `uv` back hours later, unchanged. The
> venv here is built on a *signed* Python for that reason and is not the one
> `uv sync` would create. What follows is how it is meant to go; that subsection
> is what actually happened.

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

### If Smart App Control blocks uv — and then blocks Python itself

It will, and it does not stop at uv. What Smart App Control actually refuses is
**unsigned binaries** whose exact build Microsoft's cloud reputation does not
vouch for — a verdict that can change tomorrow with nothing on your machine
changing. `uv.exe` is unsigned, so it went first, on 2026-08-31, having worked
for the previous week:

```
error: An Application Control policy has blocked this file. (os error 4551)
/usr/bin/bash: .../uv.exe: Permission denied
```

**There is no way to allow one file.** Smart App Control has no exclusion list —
that is deliberate in its design, and it is the difference between it and
SmartScreen. Reinstalling uv from scoop, from the Astral installer or from PyPI
gets you the same unsigned bytes and the same block. Upgrading to a newer uv is
worth one try, since reputation is per build, but it is a coin flip that can flip
back.

The trap is that "just use the venv's Python instead" is not a workaround, because
**the interpreter uv provisioned is unsigned too**. It is a
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
build, and every file in it — `python.exe`, `python312.dll`, every `.pyd` —
reports `NotSigned`. On the same day, Smart App Control started blocking
individual extension modules inside it:

```
ImportError: DLL load failed while importing _ctypes:
    An Application Control policy has blocked this file.
```

`_ctypes.pyd` and `winsound.pyd` went; every other stdlib extension module still
loaded, and no file had been modified. That is per-file reputation, and it is not
a stable state — any other `.pyd` can go next.

It is fatal here rather than inconvenient. `ctypes` is imported by `status.py`,
`tray.py` and `power.py`, and `cli.py` imports `status`, so **every** `referat`
subcommand dies at import — `referat --version` included, and so does the tray,
and so does the VS Code extension, which drives the same interpreter.

**The fix is a signed interpreter.** Signature is precisely what Smart App
Control discriminates on, and the python.org builds are Authenticode-signed by
the Python Software Foundation, file by file — `python312.dll` and every `.pyd`
individually. Install one through the Python install manager, which ships with
Windows as `py`:

```powershell
py install 3.12          # signed PSF build, PythonCore channel
py list                  # confirm 3.12 is there
```

Still 3.12, and for the original reason: 3.13 and 3.14 are too new for the torch,
CTranslate2 and pyannote wheels. You are changing the *provenance* of the
interpreter, not the version.

Then rebuild the venv on it. **Move `site-packages` aside rather than
reinstalling it** — with the `transcribe` extra it is roughly 5 GB, pip's cache
holds a fraction of that, and uv's cache is not in a format pip can read. The
wheels transfer because both builds are CPython 3.12 for `win_amd64` against the
same stable ABI, and because the venv path does not change, so the console-script
shims and the `.pth` files stay valid:

```powershell
Move-Item .venv\Lib\site-packages $env:TEMP\referat-site-packages
Remove-Item -Recurse -Force .venv

py -3.12 -m venv .venv                   # seeds pip itself; no ensurepip step
Remove-Item -Recurse -Force .venv\Lib\site-packages
Move-Item $env:TEMP\referat-site-packages .venv\Lib\site-packages

.venv\Scripts\python.exe -m pip install -e . --no-deps
```

Do not delete `$env:TEMP\referat-site-packages` until `import torch` succeeds.
It is the only copy of a multi-gigabyte download.

Check the block is gone, in this order — the first line is the whole point:

```powershell
.venv\Scripts\python.exe -c "import ctypes; print('ctypes ok')"
.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
.venv\Scripts\python.exe -m referat.cli --version
.venv\Scripts\python.exe -m referat.cli devices
```

From here on, drive everything through the venv's Python. Whether uv happens to
be running today is beside the point — nothing needs it:

```powershell
.venv\Scripts\python.exe -m referat.cli list        # instead of `uv run referat list`
.venv\Scripts\pythonw.exe -m referat.tray           # what autostart already does
```

Use the `-m` forms, not `.venv\Scripts\referat.exe`. Those console-script stubs
are the one part of a signed Python install that is *not* signed — pip stamps
them from bundled `t64.exe`/`w64.exe` templates — and Dropbox has deleted them
twice besides. `python.exe -m` depends on neither.

For a fresh install of the dependencies, use pip in uv's place:

```powershell
.venv\Scripts\python.exe -m pip install -e . --no-deps
.venv\Scripts\python.exe -m pip install -e ".[transcribe]" --extra-index-url https://download.pytorch.org/whl/cu128
```

That `--extra-index-url` is not optional and is the one thing pip will not learn
from the project file: the CUDA 12.8 torch wheels come from
`[tool.uv.sources]` in `pyproject.toml`, which **only uv reads**. Without it pip
installs the default PyPI torch, which does not support this GPU.

`--no-deps` on the first command is what repairs a broken editable install
without touching the gigabytes already downloaded — see the next heading.

One VS Code setting has to go with this. `"python-envs.alwaysUseUv": true` in
`.vscode/settings.json` made the Python extension reach for uv on every package
refresh, which now fails at `CreateProcess` rather than with an exit code, and so
surfaces as `Error refreshing packages A system error occurred (spawn UNKNOWN)`.
It was there because uv's venvs ship without pip; a `py -m venv` venv has pip, so
the setting has lost both its purpose and its safety.

### Reputation comes back, too — and that is not a reason to relax

Hours after the block, on the same day and with nothing reinstalled,
`uv --version` started answering again. Same unsigned bytes, still
`NotSigned`; Microsoft's cloud reputation for that exact build had simply been
restored.

Do not read that as "the problem went away". It is the same mechanism running in
the other direction, and it is the strongest evidence for the rule above: a
build that works today is not a build you can depend on tomorrow, in either
direction, and nothing local tells you which way it has gone. The venv stays on
the signed interpreter. `uv` working again means you *may* use `uv sync` for a
bulk dependency install if you like it better than pip — it does not mean the
tray, the CLI or the extension should route through anything unsigned again.

### Does a signed Python fix this for good?

It fixes the class of failure that is fatal, and it does not clear the machine
of unsigned binaries. Those are different claims and the difference is the whole
answer. Measured after the migration, with `Get-AuthenticodeSignature`:

| Surface | Signed? | If SAC turns on it |
| --- | --- | --- |
| Base interpreter — `python312.dll`, every stdlib `.pyd` | **39 Valid, 0 unsigned** | — |
| `.venv\Scripts\python.exe`, `pythonw.exe` | **Valid** (the redirector copies keep the base signature) | — |
| `referat.exe`, `referat-tray.exe`, `pip.exe` | NotSigned — distlib stubs stamped from pip's bundled `t64.exe`/`w64.exe` templates, themselves the only unsigned files in the whole Python install | Nothing breaks. Every path in this project already uses `python.exe -m referat.cli` and `pythonw.exe -m referat.tray` instead, for the unrelated reason that Dropbox eats these stubs |
| `site-packages` native DLLs — torch alone is 26 unsigned of 38 | NotSigned | **Transcription breaks; recording and the CLI do not.** Degraded, not dead |

So the critical path — everything that must import before `referat --version`
can print — is now entirely signed, and that is what changed. What was fatal
before was that the *interpreter* was unsigned: one revoked `.pyd` took the CLI,
the tray and the extension with it, and no amount of care elsewhere could route
around it.

**There is no way to make the rest signed.** PyPI wheels are not Authenticode
signed, will not be, and there is no per-file allow to grant them. Three honest
options remain, in order of how much they actually buy:

1. **Keep the critical path signed, accept degradation elsewhere.** Where this
   now is. A future block costs a feature, in a component that already has a
   documented failure mode, instead of costing the whole application.
2. **Turn Smart App Control off.** The only thing that removes the exposure
   completely — and it also retires the PyAV/`torchcodec` workarounds from steps
   5 and 7, which exist solely because of it. One-way, system-wide, needs a
   Windows reinstall to get back: your call, not this project's. See section 10.
3. Sign the wheels yourself. Not real: it means a code-signing certificate and
   re-signing every DLL on every dependency upgrade, for one laptop.

The thing to take from this is the diagnostic, not the fix. **When something
here dies with "An Application Control policy has blocked this file", check the
signature of the file named, not the package that imported it** — SAC blocks per
file, by reputation, with nothing modified on disk and no warning first.

**And check whether it still fails.** On 2026-09-01 a `referat rerun` was killed
by a block on scipy's `_odepack.pyd`; a retry minutes later was killed on
`_stats_pythran`, then on `_sobol`, and a retry after that succeeded and has
succeeded since. Nothing was installed or changed in between — the cloud
reputation lookup simply finished. So a block is often a **window rather than a
verdict**, which cuts both ways: a failure may not reproduce five minutes later,
and a component that has worked for a month can still fail the first time it
reaches a `.pyd` nobody on this machine has run before. That is why the
resampler was moved off scipy (section 5) rather than wrapped in a retry: the
window is unpredictable and lands wherever it lands.

Turning Smart App Control off would also fix all of this. It is a one-way,
system-wide change that Windows cannot undo without a reinstall, and it is your
call, not this project's: see section 10.

### If the checkout lives in Dropbox, the venv will be eaten

`.venv` is *gitignored*, which does nothing to stop a sync client. If this
repository sits inside a Dropbox, OneDrive or iCloud tree, that client will
quietly delete files out of it. It has happened here twice: the console scripts
`referat.exe` and `referat-tray.exe` disappeared, and
`site-packages\referat-0.1.0.dist-info` was reduced to an empty directory —
which leaves `import referat` working *only* from the repository root, so the
failure looks like a `PATH` or working-directory problem rather than a missing
install.

The check, run from anywhere except the repository root:

```powershell
.venv\Scripts\python.exe -c "import referat; print(referat.__file__)"
```

`ModuleNotFoundError` there means the editable install is gone. The repair:

```powershell
Remove-Item -Recurse .venv\Lib\site-packages\referat-0.1.0.dist-info   # only if it is empty
.venv\Scripts\python.exe -m pip install -e . --no-deps
```

An empty `dist-info` has to go first, or pip refuses with *"Cannot uninstall
referat None — no RECORD file was found"*: the husk claims the package is
installed while carrying no record of what it installed.

The real fix is to keep the checkout out of the synced tree, or to mark `.venv`
ignored by the sync client. That is the same rule the meetings folder already
follows for the voiceprints and the staging folder — keep the thing that must not
be synced out of the synced tree.

---

## 3. First run, and `config.toml`

```powershell
.venv\Scripts\python.exe -m referat.cli config
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
.venv\Scripts\python.exe -m referat.cli devices
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

**The check is `.venv\Scripts\python.exe -m referat.cli config`**: read the paths back and confirm that
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
- **`.venv\Scripts\python.exe -m referat.cli label --forget <name>`** is how a person is removed, and it
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

## 9. Autostart, and launching the tray by hand

```powershell
.venv\Scripts\python.exe scripts\install_autostart.py               # at sign-in
.venv\Scripts\python.exe scripts\install_autostart.py --start-menu  # by name
```

Install both. The first writes `Referat.lnk` into your Startup folder so the
tray comes up at sign-in; the second writes the same shortcut into
`Start Menu\Programs`, so **pressing Start and typing "Referat" launches it** —
and right-clicking that result pins it to the taskbar. That is the answer to
"the tray is not running and I do not want to open a terminal". They do not
conflict: a named mutex means a second tray logs "already running" and exits.

Both target this checkout's `.venv\Scripts\pythonw.exe` with `-m referat.tray`,
so the tray comes up with no console window. The installer prints exactly what
it wrote:

```
Installed C:\Users\you\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Referat.lnk
  target:  C:\...\referat\.venv\Scripts\pythonw.exe
  args:    -m referat.tray
  workdir: C:\...\referat
  config:  C:\...\referat\config.toml
```

| Command | What it does |
| --- | --- |
| `install_autostart.py` | Installs the Startup-folder shortcut. Running it again when it is already current changes nothing. |
| `install_autostart.py --start-menu` | Installs the Start menu entry instead. Same shortcut, different folder. |
| `install_autostart.py --status` | Prints **both** locations. Exit 0 when both are current, 1 when either is not. |
| `install_autostart.py --uninstall` | Removes the shortcut from the selected location; add `--start-menu` for that one. |
| `--force` | Replaces, or removes, a shortcut that points somewhere else. |

Without `--force` the installer refuses to touch a `Referat.lnk` aimed anywhere
but this checkout, and refuses to delete one it did not write. `--status`
deliberately reports both locations whatever flags it is given: "is the tray set
up" is one question, and answering half of it is how somebody concludes the
Start menu entry is missing when it is the autostart one that is.

Four things worth knowing:

- **The tray is two processes, and that is expected.**
  `.venv\Scripts\pythonw.exe` is a copy of CPython's venv *redirector*, which
  spawns the base interpreter and waits on it. So you will see a ~6 MB stub and
  a ~46 MB interpreter. `referat status` reports the interpreter, because
  `tray.py` writes its own pid. (This changed when the venv moved off uv's
  interpreter — see section 2.)
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
  run `.venv\Scripts\python.exe -m referat.cli config` — that is the diagnostic.

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
and `torchcodec` bundle. The two cost different things: PyAV's block makes
`import faster_whisper` fail outright, while torchcodec's merely raised a
Windows Security notification — *"Part of this app has been blocked... we can't
confirm who published `libtorchcodec_core6.dll`"* — during a transcription that
then succeeded anyway, because pyannote catches the failure itself.

**You do not have to do anything.** Referat decodes its own WAVs with the stdlib
`wave` module and hands pyannote an in-memory waveform, so neither library ever
reaches for its bundled *decoder*; and it stubs both `av` and `torchcodec` out
in `sys.modules` before the libraries that want them are imported, so neither
bundled DLL is ever *loaded* either. If you see a Windows Security notification
naming some other DLL during a run, that is a third library doing the same
thing, and the fix is the same shape — see
`transcribe._neutralize_pyav` and `diarize._neutralize_torchcodec`. This note
exists to explain why those workarounds are there.

Turning Smart App Control off is a **one-way** change — Windows cannot re-enable
it without a reinstall — so it is documented here and not recommended.

---

## 11. Checking the whole thing

```powershell
.venv\Scripts\python.exe -m referat.cli --version                              # referat 0.1.0
.venv\Scripts\python.exe -m referat.cli config                                 # paths, and the sync check from section 6
.venv\Scripts\python.exe -m referat.cli devices                                # your microphone marked `<- config`
.venv\Scripts\python.exe -m referat.cli status                                 # "Referat is not running." until you start it
.venv\Scripts\python.exe scripts\install_autostart.py --status   # Installed, exit 0
.venv\Scripts\python.exe -m referat.cli list                                   # empty until your first meeting
.venv\Scripts\python.exe -m referat.cli index                                  # writes <meetings_dir>/INDEX.md
```

Then record something:

1. Start the tray: `.venv\Scripts\pythonw.exe -m referat.tray`, or `.venv\Scripts\pythonw.exe -m referat.tray` when
   you want the log on screen.
2. Press `ctrl+alt+f9`. The icon turns red.
3. Talk for a minute or two, with something playing over the system audio if you
   want to exercise the loopback channel.
4. Press `ctrl+alt+f9` again. The icon goes blue while it transcribes.
5. `.venv\Scripts\python.exe -m referat.cli list` — one row, status `done`, and an `UNNAMED` count if
   diarization found remote speakers.
6. Open the meeting folder and read `transcript.md`, then `meta.json`.
7. `.venv\Scripts\python.exe -m referat.cli label <meeting-id>` plays each unidentified speaker and asks
   who it was. From then on Referat knows that voice.
8. Open the meetings folder in VS Code. `INDEX.md` shows the meeting, rendered.
   Run `/cleanup <meeting-id>` in a Claude Code session there to write its
   `notes.md`, then `.venv\Scripts\python.exe -m referat.cli index` to put the title in the table — see
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
transcription; `.venv\Scripts\python.exe -m referat.cli index` rebuilds it on demand, which is what you
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

Build it and install it:

```powershell
cd referat-vscode
npm install
npm run package                                    # referat-vscode-0.2.0.vsix
code --install-extension referat-vscode-0.2.0.vsix
```

`npm run package` compiles first — `vscode:prepublish` runs the bundler — so the
`.vsix` can never be built around a stale `dist/extension.js`. It is nine files
and about 21 KB: the bundle, `media/`, the manifest, the README and the LICENSE.
`code --uninstall-extension niklas-elmqvist.referat-vscode` takes it off again.

**Set `referat.repoRoot` after installing.** This is the one thing that changes
when the extension stops being run from source. Left empty, it looks through the
open workspace folders for the repository and, failing that, falls back to the
checkout its own bundle sits inside — which is how F5 works in a development host
opened on no folder at all. Installed from a `.vsix` that fallback lands in
`~\.vscode\extensions\niklas-elmqvist.referat-vscode-0.2.0` and finds no
`pyproject.toml`, so a window without the repository open reports the repository
missing until the setting is filled in. The window opened on the *meetings*
folder is exactly that window, and it is the normal one.

Restart VS Code and there is a **Referat** icon in the activity bar: your meetings,
newest first, one row each with its title, duration, project tags and lifecycle
state, and buttons for *Transcript*, *Notes*, *Generate notes*, *Re-transcribe*
and *Tags…*. A meeting with speakers nobody has named yet has a **Speakers**
button that expands in place, plays their snippets and takes a name. **Untagged
only** at the top leaves the meetings that still need a project, and the view's
**Projects** button creates, renames and deletes them.

A meeting whose transcript the quality gate refused shows `gate_failed` and an
**Accept & delete audio** button. That one is irreversible and says so in a
modal: `mic.wav` and `system.wav` are deleted for good and the meeting moves into
the meetings folder, because no WAV may ever be written there. Use it when the
transcript is good enough despite the gate; use *Re-transcribe* when it is not.

A status bar item on the left says what the tray is doing. It appears once you
have opened the sidebar in that window, and it does not count up second by
second — it re-reads `referat status` when the tray writes its status file, and
every 30 seconds otherwise.

Two settings, both optional:

| Setting | Leave it empty and… |
| --- | --- |
| `referat.repoRoot` | it looks through the open workspace folders for the one holding `pyproject.toml` and `referat/cli.py`, then at the checkout its own bundle sits inside. **Installed from a `.vsix` that second half cannot answer**, so set it in any window that does not have the repository open. |
| `referat.claudeBinary` | it asks VS Code where it installed the Claude Code extension and uses the binary inside it, falling back to `PATH`. **On this machine `claude` is not on `PATH` at all** — `where claude` finds nothing — so the extension lookup is the one that actually answers. It happens fresh on every run, so an update that moves the binary cannot break it. |

There is deliberately no meetings-folder setting: the extension reads
`[paths].meetings_dir` out of the repository's `config.toml`, which is the same
file the tray records against, so the two cannot disagree about where meetings
live.

Everything it shows comes from `referat list --json` and `referat status --json`,
and every change it makes is a `referat` verb — `label --speaker --name`, `tag`,
`untag`, `project`, `state`, `promote` — so if the sidebar looks wrong, run those
commands yourself and you will see exactly what it saw. A refusal it shows you is
the sentence the CLI printed, passed through unedited.
**Referat > Show Output** has every command it ran, with its full command line.

It runs them through the venv's own interpreter,
`.venv\Scripts\python.exe -m referat.cli`, with the working directory at the
repository — the fallback from section 2, for the same reason: `uv` does not run
on this machine. So if the sidebar is empty and an error appears, the usual causes
are a `referat.repoRoot` pointing somewhere that is not the repository, or a
`.venv` that Dropbox has eaten again (section 2 has the repair).

To work on the extension rather than use it, press **F5** in the repository:
that starts an Extension Development Host running it from source, with esbuild
watching and source maps on. The packaged build deliberately has none — a source
map carries the whole TypeScript inside it, and the point of bundling was that
the `.vsix` is one JavaScript file.

Updating it is the same three commands with the version bumped in
`referat-vscode/package.json` first. Nothing bumps it for you, and
`code --install-extension` on an unchanged version number does reinstall, so the
version is a label rather than a check.
