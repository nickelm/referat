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

The base install is deliberately kept to the tray, the audio capture and the
command center, so a plain `uv sync` gives you a working recorder in about a
minute — `PySide6-Essentials` is a 77 MB wheel and the rest are small. You need
`--extra transcribe` before anything is transcribed.

**Essentials, never the full `PySide6`.** The full package pulls in
`PySide6-Addons`, which is another 168 MB and carries QtWebEngine — a bundled
Chromium that Referat does not use and that would put a second process on the
recording path. If a `pip install PySide6` ever creeps in, take it back out.

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
subcommand dies at import — `referat --version` included — and so does the tray,
and with it the command center, which lives in the tray's process.

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
tray, the CLI or the window should route through anything unsigned again.

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
before was that the *interpreter* was unsigned: one revoked `.pyd` took the CLI
and the tray with it, and no amount of care elsewhere could route around it.

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
speakers mid-day. Pinning it to a device name is the obvious-looking hardening
and it is a **trap**: the loopback decides where Referat *listens*, not where the
sound *comes out*, so a pinned loopback that disagrees with what the call is
actually playing through records silence and loses the far end completely. That
is a worse failure than the one section 4a is about.

---

## 4a. Hybrid meetings: where the remote audio comes out

A meeting with people in the room *and* people on a call is the one setup that
can record the same speech twice. Referat captures two channels — the microphone
and a loopback of what the machine is playing — and if the call's audio comes out
of a **loudspeaker standing in the same room as the microphone**, the room
microphone hears it too. The far end then lands in `transcript.md` twice: once
from the loopback, clean, and once off the air, degraded.

**So send the call's output to a headset, or to the Jabra.** The Jabra Speak 510
does hardware echo cancellation and will not feed its own playback back into its
own capture, which is exactly what it is for. Anything else in the room — laptop
speakers, a monitor, a television over HDMI — will.

**The trap is that setting the Windows default output is not enough.** Zoom and
Teams each keep their *own* speaker selection, independent of the system default,
and Referat cannot see it: `referat devices` reports the device Referat will
record from, not the device the meeting app will play through. Check it inside
the call, in Zoom's audio settings, not in Windows'.

This happened on 2026-09-02. The default output had become a television over
HDMI, Zoom followed it, and 56 minutes of a four-person hybrid meeting came out
with roughly 761 duplicated lines. What it looks like afterwards:

- a remote person appears twice under one name, once punctuated and once as a
  lowercase run-on with the stutters left in;
- `referat label` offers more speakers than were in the meeting, because the
  microphone clustered the echo as people of its own;
- the microphone channel's `voiced_seconds` is nearly its whole duration, because
  the room mic was hearing the loudspeaker continuously.

**Referat suppresses this during transcription anyway**, and records what it did
under `transcription.bleed` in `meta.json` — which is also how you find out it
happened. The suppression is not a reason to stop caring where the audio comes
out: it recovers the transcript, and it cannot recover a recording made through a
loudspeaker.

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

**Do not hardcode the path into the Claude Code extension's own copy.** It
carries a version number and the old directory is deleted on the next update — a
path that worked while step 10 was being built was gone two days later, when it
was committed. `referat/notes.py` re-resolves it at every spawn and honours the
`.obsolete` file VS Code writes beside those folders, which is why it survives an
update that would break a stored path.

---

## 12. The command center

**The window is the UI.** The tray icon opens it — left-click, or *Open command
center* on its menu — and closing it hides it back to the tray rather than
quitting, because that process is the recorder. There is nothing to install: it
is PySide6, it comes with the base dependencies, and it lives in the same process
as the hotkeys.

It opens on a **dashboard**, because the question you open it to ask is whether
anything is waiting for you rather than what happened in March. Recent meetings
and your open action items on the left; on the right, today's day summary and
three queues in the order the work is done in — meetings with no project,
speakers nobody has named, meetings with no notes. Every row is a link that opens
the meeting on the tab where the button that acts on it lives; nothing on the
dashboard writes anything.

The other tabs:

| Tab | What it is for |
| --- | --- |
| **Meetings** | The inventory, with an *Untagged only* toggle and a search box, and the transcript beside its notes. A `[HH:MM:SS]` in the notes scrolls the transcript there; a speaker's name, and any `[[Wikilink]]`, opens that person. |
| **Actions** | One owner's action items at a time, parsed out of the notes. Ticking, editing and dropping are recorded in `actions.json` and never in `notes.md`. |
| **Projects** | Create, rename, describe, archive and delete a project, and edit its glossary — which is hotword management, so the merged list Whisper will be handed sits underneath, read-only, with whatever the 223-token cap dropped named. |
| **People** | A person: their voiceprints, where each was filed from, the meetings their name appears in and the projects those carry. *Forget this person* is the one thing this page writes. |
| **Activity** | What is running now, and the live rotating log. *Referat only* hides library chatter. |

The buttons on the Meetings tab, left to right, are the order a meeting moves
through: **Re-transcribe…** and **Promote…** act on the *audio* and are disabled
for almost every meeting, because the audio is normally gone; then **Tags…**,
**Speakers…**, **Generate notes…**, **Notes for all…**, and **Delete…** on its own
at the end.

**Re-transcribe…** runs the pipeline again from the WAVs a meeting kept. It runs
on the tray's own transcription thread, so the progress appears in the status bar
and on the Activity tab and nothing blocks; the current `transcript.md` stays
until the new one is written whole. Diarization renumbers from scratch, so the
speakers are looked up again in the known-voices database — a voice whose print
no longer matches comes back a number.

**Promote…** is the off-ramp for a meeting stuck in staging: one whose transcript
the quality gate refused, or one `[transcription].keep_audio` kept on purpose.
Either way it is irreversible and the modal says exactly what goes: the WAVs are
deleted, the folder moves into `meetings_dir`, and `transcript.md` is all that is
left — no WAV may ever be written into a synced folder, which is what makes
releasing the audio the price of promoting. Use it when the transcript is good
enough despite the gate; use *Re-transcribe…* when it is not. `referat promote
<id> --release-audio` is the same thing at the prompt.

Nothing here is a second implementation. Every page calls the same functions
`referat list`, `referat show`, `referat label` and the rest call, so a refusal
you see in a dialog is the sentence the CLI would have printed, word for word,
and anything that looks wrong can be reproduced at a prompt.

### There used to be a VS Code extension here

`referat-vscode/` was the primary UI from build step 15 until step 20, and was
**deleted at step 23** — the window can now re-transcribe a meeting and release a
staged one's audio, which were the last two things the sidebar could do and it
could not. If you are coming back to a machine that still has it installed:

```powershell
code --uninstall-extension niklas-elmqvist.referat-vscode
```

Nothing else has to be undone. It had two settings, `referat.repoRoot` and
`referat.claudeBinary`, and both are inert once it is gone; there was never a
meetings-folder setting, deliberately, because `[paths].meetings_dir` in
`config.toml` is the one place that says where meetings live.

## 13. Google Docs digests

Optional, and the only part of Referat that talks to anything but your own
machine. A project can carry one or more Google Docs, and `referat project sync`
writes every meeting tagged with that project into each of them as a dated block.
What it sends is `notes.md` and nothing else — never `transcript.md`, never a
WAV, never `.voices/`. Skip this whole section if you do not want it; nothing
else depends on it.

### 13a. The extra

```powershell
.venv\Scripts\python.exe -m pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

Tens of megabytes, unlike the transcribe stack's three gigabytes. Only
`project link-doc`, `project unlink-doc` and `project sync` need it; every other
project verb, and `tag`, `untag` and `state`, keep working without it and say so
plainly if you try one of the three.

It adds exactly two unsigned native files, `google\_upb\_message.pyd` and
`cryptography\hazmat\bindings\_rust.pyd`. Both are **off the recording path** —
nothing between a WAV and a transcript imports either — so if Smart App Control
ever blocks one you lose a digest push and nothing else. That is the degradation
section 2's rule permits, and it is worth re-running the signature sweep after an
upgrade the same way you would for PySide6.

### 13b. The Google Cloud side, once

This is the part nobody can do for you.

1. Go to <https://console.cloud.google.com/> and create a project. `Referat` is a
   fine name; an existing personal project is fine too.
2. **APIs & Services → Library**, and enable both:
   - **Google Docs API** — creating and writing the digest documents.
   - **Google Drive API** — used for *one* thing, searching your Drive by name so
     *select existing doc* has something to offer.
3. **OAuth consent screen** → *External*. App name `Referat`, your own address
   for both contact fields.
4. Add exactly two scopes:
   - `https://www.googleapis.com/auth/documents`
   - `https://www.googleapis.com/auth/drive.metadata.readonly`

   The second one returns names, ids and modification times and **no file
   content at all**. Do not be tempted by `drive.readonly`, which reads every
   byte of everything you own; and note that the narrower-sounding `drive.file`
   does not work here — it grants access only to files this application itself
   created, so on a fresh install the document search would return an empty list.
5. **Set the publishing status to "In production", not "Testing".** This is the
   one setting that will otherwise bite you weeks later: Google expires a testing
   app's refresh tokens after **seven days**, so the browser consent would come
   back roughly weekly. In production an unverified app shows a *"Google hasn't
   verified this app"* interstitial once — *Advanced → Go to Referat (unsafe)* —
   and the token then persists. Verification is not required for your own
   account.
6. **Credentials → Create credentials → OAuth client ID**, application type
   **Desktop app**. Not "Web application".
7. Download the JSON and save it as `~/.referat/google_client_secret.json`,
   beside `hf_token`. Nothing goes into the repository, and `.gitignore` names
   both files anyway because a browser download lands in `Downloads` and the
   repository root is where somebody would drop it.

`[paths].google_client_secret_file` and `[paths].google_token_file` in
`config.toml` move either file if you want them elsewhere.

### 13c. The consent, once

The first command that needs Google will print a URL, open your browser, and ask
you to paste something back:

```powershell
.venv\Scripts\python.exe -m referat.cli project link-doc <project-id> --create
```

Approve it. **The browser will then fail to load a page at `localhost` — that is
expected.** Nothing is listening there, deliberately: the usual way to do this is
to run a small web server on a loopback port to catch the redirect, and Referat
has a standing rule against binding a port at all. Copy the whole address out of
the address bar (it looks like `http://localhost/?code=4/0Ab...&scope=...`) and
paste it at the prompt. The code is in it.

The refresh token is cached in `~/.referat/google_token.json`. Delete that file
to sign out.

**You should not be asked again.** Referat refreshes the access token silently
from then on. If it *does* ask a second time it says why, and the usual cause is
step 5 above: an OAuth app left in **Testing** has its refresh tokens expired by
Google every seven days, which looks exactly like Referat asking every week. Set
the publishing status to *In production* and it stops. The other causes -- access
revoked at myaccount.google.com, the client deleted, a badly wrong system clock
-- are named in the message too. Because the flow needs a terminal, **the command center can never
ask for consent** — do this once at a prompt and every later sync, from the
window included, uses the stored token.

### 13d. Linking, in the window

The projects page in the command center has **Open in browser**, **Link doc...**,
**Unlink** and **Sync now** under the Google Docs list, which is the easy path and the one to
use. *Link doc...* asks whether to create a document or attach one you already
have; for the second, paste its link, press *Look up its tabs*, and pick the tab.
Nothing is preselected unless the link carried a `?tab=` or the document has a
tab called `Meetings` — Referat will not choose a tab for you.

The consent in 13c is the one thing the window cannot do, by design: it needs a
terminal. Do that once at a prompt and the window uses the stored token from then
on.

Each row shows the **document's** title with its tab in brackets, and
double-clicking one opens it in your browser.

The rest of this section is the same thing at the prompt.

### 13e. Linking, and which tab

```powershell
referat project link-doc my-project --create
referat project link-doc my-project --doc "https://docs.google.com/document/d/<id>/edit?tab=t.0"
referat project link-doc my-project --doc "<link>" --tab "Meeting Notes"
referat project link-doc my-project --doc "<link>" --new-tab
referat project link-doc my-project --search "Weekly notes"
```

**Paste the link, not the id.** `--doc` takes the Share button's link, the
address bar, or a bare document id. If the address carries `?tab=t.something` --
which it does whenever the document has more than one tab and you are looking at
one of them -- that tab is the one Referat will use. So the shortest correct way
to link an existing document is to open it, click the tab you want, and copy the
address.

Otherwise `--tab` names one by title or by id, and `--new-tab` adds a fresh one
called `[digest].new_tab_name`. With none of those, Referat looks for a tab named
`Meetings` and, finding none, **lists the tabs the document actually has** and
stops without writing anything. It will not fall back to the first tab, not even
when there is only one: that is not an ambiguity about *which* tab, it is a
question about whether you want a digest written into the middle of your own
prose.

**Renaming things afterwards is safe.** Referat stores the document id and the
tab id, and Google mints both once and never moves them. So you can rename the
document, rename the tab, and **move the document anywhere in Drive** -- into a
folder, into a shared drive, out of one -- and the link still works. The titles
Referat shows are display text and a sync corrects them when they have changed.
Only *deleting* the tab breaks a link, and it says so.

**You can write in the same tab.** Each block is bracketed by a small gray
`[referat:<id>]` ... `[/referat:<id>]` pair, and Referat only ever replaces what
is between a matching pair. Anything you write above, below or between blocks is
yours and stays. Do not delete one of those markers: a block missing its closing
marker falls back to the older rule -- it owns everything down to the next block
-- and the next sync re-renders it to put the marker back.

Linking ends by running a sync, which backfills every meeting already tagged.

### 13f. How a block is laid out

Three settings under `[digest]` in `config.toml`, all presentation and none of
them changing what is sent:

- **`newest_first`** puts each new meeting at the top of the tab rather than in
  date order. It decides where a *new* block goes and never moves an existing
  one -- Referat does not rearrange a document you may have written around, so a
  sync that finds the blocks running the other way says so and leaves them.
  Worth setting before a doc fills up.
- **`heading_level`** is where a block's date line sits, and defaults to `1`: the
  date line is Heading 1 and your notes' `##` and `###` become Heading 2 and
  Heading 3. Set it to `3` if a block is going into a tab that already has an
  outline of its own.
- **`new_tab_name`** is what a tab Referat *creates* is called. It never renames
  a tab you made.

Changing either of the first two only affects blocks written afterwards, since
nothing about the existing notes has moved. `referat project sync <id>
--rerender` redraws every block, which is how the change reaches them.

### 13g. Syncing, and syncing by itself

**You normally do not have to.** A project pushes into its documents on its own
as soon as a meeting's notes are written — that is the *Sync automatically*
checkbox on the projects page, on by default, and `referat project auto-sync <id>
on|off` at the prompt. Turn it off for a document other people read and you want
to look over first; *Sync now* still works while it is off, and `referat notes
<id> --no-sync` skips it for one pass.

A push that fails never fails the cleanup: the notes are on disk whatever Google
says, and what you get is a line saying the document did not update.

By hand:


```powershell
referat project sync my-project --dry-run   # say what would change, write nothing
referat project sync my-project
referat project sync my-project --prune     # also remove blocks for untagged meetings
```

Use `--dry-run` first on any document somebody else can see. A sync inserts what
is missing in date order, re-renders in place anything whose `notes.md` has
changed since it was written, and **reports rather than removes** a block whose
meeting no longer carries the tag — the document may be shared and somebody may
have written around it. `referat project list` shows a `PENDING` count so you can
see a sync is owed without making a network call.

`referat project unlink-doc my-project <gdoc-id>` stops writing there. It does
not delete anything: every block stays exactly where it is.

### 13h. Before you share a digest doc with anybody

Read the `notes.md` files that will land in it first. A shared document is a much
wider blast radius than a synced folder, what goes into it is whatever `/cleanup`
decided to write about people who never read the prompt, and some notes carry a
`SPEAKER_02` in their participant line for somebody nobody has named yet. The
transcript, the audio and the voiceprints never leave this machine at all — but
the notes are not nothing.

