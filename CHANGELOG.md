# Changelog

Newest first. One entry per work session; small changes are grouped.

## 2026-08-29 — The meetings folder gets a scaffold, a dashboard, and notes

Build step 10. The meetings folder stops being a pile of timestamped directories:
it now carries its own `CLAUDE.md`, a `/cleanup` slash command that writes
`notes.md` from a transcript, a permission rule keeping that pass out of the
voiceprints, and a generated `INDEX.md` that — because Markdown opens rendered in
that workspace — is the browsing UI until the extension arrives at step 11.

- **`templates/` becomes `templates/meetings/`**, a directory seeded into the
  meetings folder file by file by `paths.seed_tree`, which copies what is missing
  and **never overwrites anything that exists**. `paths.MEETINGS_CLAUDE_TEMPLATE`
  is now `MEETINGS_TEMPLATE_DIR`, and `config.ensure_meetings_dir` walks it.
- **`referat/index.py` and `referat index`.** One row per meeting — date, title,
  duration, status, unnamed speakers, links to `transcript.md` and `notes.md` —
  newest first, unlike `referat list`: a listing is read at a prompt with the
  last line nearest the cursor, a dashboard is read from the top. Regenerated at
  the end of every transcription, after `referat label` and after `--forget`, and
  on demand.
- **The title is the H1 of `notes.md`**, falling back to the meeting id.
  `index.meeting_title`, which step 13's digest blocks will share. Only a `#` on
  the first non-blank line counts — one further down is a mistake in the notes
  rather than a second title, and reading it would put a section heading in the
  dashboard.
- **`format_duration` moved from `cli.py` to `meeting.py`.** Two copies would
  have let `referat list` and `INDEX.md` disagree about the same meeting, and
  importing `cli` from `index` would have been a cycle the moment `cli` imported
  `index`. `meeting.py` is what both already import and costs nothing.

**Staged meetings get a footer line, not rows.** A meeting that kept its audio is
not in the meetings folder at all — it is in `%LOCALAPPDATA%`, waiting for a
`referat rerun` that comes out clean — so there is nothing here to link to
relatively, and naming it absolutely would put this machine's layout into a
synced file. The footer counts them and names the command, the way `referat
list`'s footer already does, so the two cannot disagree about what is waiting.

**`write_index` never raises.** Same rule that shapes `diarize.diarize`: it is
called from the transcription pipeline, and a meeting with a good transcript in
it is not a failed meeting because a table would not render. Driven with
`meetings_dir` pointing at a *file*: it logged, returned `None`, and nothing
escaped.

**The deny rule was wrong on the first try, and Claude Code said so.** The
scaffold's `.claude/settings.json` shipped with `Write(**/.voices/**)` in the
deny list, which reads like defence in depth and is decoration: file permissions
match `Read(path)` and `Edit(path)` only, and `Edit` already covers every
file-writing tool. The very first `claude -p` run printed *"`Write(**/.voices/**)`
is not matched by file permission checks"* before answering. It is gone.

That first run also proved nothing, and it took a second one to notice. The
model refused to read the decoy voiceprint — but the meetings `CLAUDE.md` sitting
beside it tells any session in that folder never to touch `.voices/`, so what was
demonstrated was that the *prose* works. The rule was re-tested in a bare folder
holding nothing but `.claude/settings.json`, a decoy and one ordinary file, with
no instructions anywhere: `File is in a directory that is denied by your
permission settings`, while `plain.txt` beside it read fine. That is the rule
biting, at the tool layer, on its own.

**And the limit of it is worth writing down:** these rules bind the file tools,
not `Bash`. A `cat .voices/voices.json` would go straight past them. It is
survivable only because step 11 spawns `/cleanup` with `--allowedTools
"Read,Write"` and therefore no shell — which makes "never add `Bash` to that
list" a real constraint on code that has not been written yet, now recorded in
`CLAUDE.md` and TODO rather than left to be rediscovered.

**`/cleanup` was run on a real transcript**, `2026-08-28_1152` — 18 minutes, 258
lines. It wrote a `notes.md` whose H1 became the title in `INDEX.md`, left
`transcript.md` and `meta.json` byte-identical, and came out entirely inside the
Markdown subset step 13's translator handles: one H1 on the first line, no `####`,
no nested bullets, no tables, no fenced code. Checked mechanically rather than by
eye.

It also did the thing the prompt exists to make it do. That meeting is the one
whose microphone was never diarized, so every line reads `ME` although it is
plainly a dialogue. The notes say so at the top, attribute turns only by
conversational flow, and **do not name the second participant** — while quietly
correcting an ASR "Jan LeCun" to Yann LeCun and declining to correct a person's
name on the same evidence. That distinction was not in the prompt; it is the
right one, and it is worth keeping an eye on whether it holds.

**What this does not do is tune the prompt.** The gate on step 10 was always
about that, and it has not lifted: one real transcript, of an in-person meeting
with no `SPEAKER_NN` in it anywhere, means every instruction about speakers went
untested. The four gate boxes stay open, and the follow-up is recorded — read the
prompt again against the first call that has diarized speakers in it.

**The live meetings folder's `CLAUDE.md` was reconciled by hand**, as the
seed-once rule intends. It was a pure unmodified seed — every line it had that
the new template does not was a line this session deliberately rewrote — so
reconciling was a copy. The scaffold's other three files were seeded fresh
alongside it, and the seeder was verified against the live folder by hashing
`CLAUDE.md` before and after: unchanged, which is exactly the failure mode the
never-overwrite rule exists to prevent.

**Found while verifying, and it belongs to step 11:** there is no `claude` on
`PATH` on this machine. The binary ships inside the VS Code extension, at
`resources/native-binary/claude.exe`, and `where claude` finds nothing — so step
11's plan to spawn `claude` off `PATH` by default would fail here on first use.
`/cleanup` was driven by absolute path instead.

*(Two days later, committing this: that absolute path is already dead. The
extension moved 2.1.247 → 2.1.251 and `~/.vscode/extensions/.obsolete` now lists
the 2.1.247 directory for deletion. The finding was weaker than the truth — this
is version rot on a scale of days, not a one-machine quirk, and it settles step
11's design: resolve the binary through
`vscode.extensions.getExtension("Anthropic.claude-code")` at spawn time and store
nothing. Written up under step 11 in TODO, and in SETUP.md section 11a for the
shell case, where `npm i -g @anthropic-ai/claude-code` needs no PATH change
because `%APPDATA%\npm` is already on it.)*

Verified by driving it, as at every step so far: the scaffold seeded into a
throwaway folder and then re-seeded with one file edited and one deleted (the
edit survived byte for byte, only the deletion came back); `INDEX.md` rendered
against synthetic meetings covering all three title cases — an H1 first line, a
`notes.md` whose first heading is further down, and no notes at all — with every
relative link resolving to a file that exists; the counts cross-checked against
`referat list`; the empty-folder case; and finally a real `referat rerun` of the
staged `2026-08-28_1104`, which kept its audio, stayed in staging, and still
rewrote `INDEX.md` at the second the pipeline finished — which is the
unconditional call doing its job.

## 2026-08-28 — The microphone is the room, not the owner

A real meeting came back with two people in it and **258 lines all labeled
`ME`**. Nothing had failed: the microphone channel was never diarized at all.
`ME` was not a decision, it was the mic channel's fixed label, and diarization
only ever ran on the loopback — which for a meeting held in person is eighteen
minutes of silence.

**The premise was wrong, not the tuning.** `mic.wav` was taken as one speaker by
definition, the owner of the laptop and nobody else. That is true of a Zoom call
and false of the meetings Referat is mostly used for, where everybody at the
table arrives through one microphone. The first line of `CLAUDE.md` has always
said this records meetings "in person and over Zoom/Teams"; only the second half
was ever implemented.

- **Both channels are diarized now.** `transcribe_channels` no longer passes
  `diarize_channel=False` for the mic.
- **Speaker numbering runs across the meeting, not the channel.**
  `diarize.assign` takes a `start`, and `transcribe_channels` threads the running
  count. Two channels each restarting at `SPEAKER_01` would put two different
  people behind one label in the same transcript.
- **`ME` now means the owner, on whichever channel their voiceprint was
  recognised** — matched exactly as everybody else is, then rendered `ME` because
  reading `Niklas:` against one's own lines is stranger than reading `ME:`. It is
  presentation only: `meta.json` keeps the real name in `speaker_names`, which is
  what `referat label --forget` needs to revert the owner like anybody else. A
  mic channel that could not be diarized still falls back to `ME`.
- **The owner bootstrap is measured instead of inferred.** It used to require the
  loopback channel to hold voice — sound reasoning about the wrong condition,
  since it can only fire on remote calls, so for the meetings this is actually
  used for the owner voiceprint would never have been created at all. The rule is
  now that the microphone clustered into **exactly one speaker**: true of a solo
  recording and a remote call, false of a room with two people in it. It also
  settles the Jabra Speak 510 worry that had been open since step 7b — far-end
  audio echoing off a desk speakerphone clusters as a second voice and refuses
  the bootstrap, rather than quietly poisoning the owner's voiceprint.
- The bootstrap files the cluster's own centroid, which diarization already
  computed, so nothing is re-embedded. `diarize.embed` had no other caller and
  was deleted, along with two constants that served only the old path.

**And the verification found a second bug, in code that predates this change.**
Naming one synthetic cluster as the owner and re-running turned *both* speakers
into `ME`. The numbers said why: the other voice scored **0.7404** against a
0.70 threshold, with `runner_up: -1.0`. **With one name in the database there is
no runner-up, so the margin — the whole safeguard against a confusable match —
was trivially satisfied and the threshold decided alone.** That is precisely the
state the database is in just after `owner_name` is first set, and precisely when
a false accept is worst, because it renders somebody else's words as `ME`. With
no runner-up the margin is now required of the score itself. The same meeting
then came back correctly: the owner's cluster `ME`, the other still `SPEAKER_02`
and waiting for `referat label`.

The thresholds themselves are still guesses, and 0.7404 between two different
Windows TTS voices is a reminder of how little headroom 0.70 has.

Verified by driving it, as always, and this time the synthetic meeting is the
exact shape of the one that failed: two TTS voices on `mic.wav`, silence on
`system.wav`. Every line correctly attributed — Hazel to `SPEAKER_01`, Zira to
`SPEAKER_02`, three snippets cut for each so `referat label` has something to
play. A hybrid meeting with one voice in the room and one on the call numbered
them `SPEAKER_01` and `SPEAKER_02` across the two channels with no collision.
`assign`'s offset, the owner-to-`ME` mapping and the new margin rule each have
their own direct checks, including the 0.7404 case that started it. The real
known-voices database was never touched: the owner test ran against a throwaway
`REFERAT_CONFIG` with its own `voices_dir`, because Hazel and Zira do not belong
in a database of real people.

**What this does not fix:** the meeting that prompted it. `2026-08-28_1152`
transcribed cleanly, so the quality gate had already released its WAVs, and no
snippets were cut because diarization never ran on that channel. There is nothing
left to re-run it from. It keeps its 258 lines of `ME`.

## 2026-08-28 — Confirmed on the machine: buttons, tray, transcription

Not a code change. Step 2's last open box is closed: **the programmed USB button
drives the autostarted tray**, with audio — press to record, press to stop, and
the meeting transcribes. That box had been open since step 2 because synthetic
keystrokes cannot be injected from an automated session, so it could only ever be
closed by a person pressing a real button.

Smart App Control was also seen doing its thing in production, and doing it
correctly: one INFO line per transcription saying PyAV is blocked and the WAVs
are being decoded directly, followed immediately by large-v3 loading on CUDA. The
blocked submodule name varies between runs — `bitstream`, `frame` — being
whichever one PyAV reaches first. The workaround from step 5 is holding.

**Still untested: a meeting with anybody else in it.** Every recording so far has
been solo, so `system.wav` has been silent every time (`silent=True` on every run
in the log) and the diarization pipeline has therefore never run outside the
synthetic two-voice tests. Speaker identification, the `0.70` threshold and the
`0.15` margin are all still guesses.

## 2026-08-28 — Autostart, and the guide for a machine that has none of this

Build step 9, which finishes the recorder. `scripts/install_autostart.py` puts a
shortcut in the Startup folder, and `SETUP.md` writes down the dozen sharp edges
that until now existed only as TODO boxes and CHANGELOG prose.

**The shortcut runs `pythonw.exe -m referat.tray`, not `referat-tray.exe`.** The
gui-script looked like the obvious target and is the wrong one: it is a uv
trampoline, which `CreateProcess`es `pythonw.exe` and then *stays resident as its
parent*, so autostarting through it would leave a stub process in the tree for
the life of the tray. `pythonw.exe` is the venv itself, where the trampoline is
an artifact `uv sync` regenerates; `tray.py` has named its logger explicitly
since step 2 precisely so the two paths log identically; and the `.lnk`
properties dialog then says what it runs. The venv is found with
`Path(sys.executable).resolve().parent` rather than by joining `.venv` onto the
repo root — `sys.executable` already answers that question, and the two answers
would disagree the first time somebody used a differently-named environment. The
repo root is cross-checked against `paths.REPO_ROOT` instead, so a script run
against a *different* checkout's package refuses rather than installing a
shortcut nobody meant.

**The `.lnk` is written by PowerShell driving `WScript.Shell.CreateShortcut`;
the Startup folder is `SHGetKnownFolderPath` through ctypes.** Those look
inconsistent and are not. The rule is about shape, not about ctypes: ctypes is
right for a single flat function with scalar arguments — `SetThreadExecutionState`
in `power.py`, `CreateMutexW` in `tray.py`, `OpenProcess` in `status.py`, and now
`SHGetKnownFolderPath` — and wrong for navigating a COM interface, where
`IShellLinkW` plus `IPersistFile` is a hundred lines of hand-declared GUID
structs and vtable-index arithmetic in which a wrong index is memory corruption
rather than an exception. pywin32 was the third option and would have put ten
megabytes of Win32 bindings into the *base* install — the one deliberately kept
to tray and audio — to write one two-kilobyte file, once.
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` stays only as the
fallback for a bad HRESULT: it is an assumption, since folder redirection moves
the Startup folder and `%APPDATA%` can be redirected on its own. The two agree
on this machine, which was checked rather than assumed.

**It refuses to repoint a shortcut aimed somewhere else**, and refuses to delete
one it did not write, in both cases printing found-against-expected and asking
for `--force`. Same rule as "every scaffold file is seeded once and never
overwritten": silently clobbering something the user may have changed on purpose
is the failure this project keeps declining to commit. Installing when the
shortcut is already current rewrites nothing at all — the mtime is the only
evidence of when this was last run.

**And it reads the `.lnk` back and compares all four fields before saying it
worked.** `Save()` reports nothing useful, and the shell normalises `TargetPath`
on the way in, so reading back is the only way to know the file on disk says what
was meant. One trap is written into the code as a comment because it is a
correctness requirement rather than an optimisation: **`CreateShortcut` on a path
that does not exist returns a blank object rather than failing**, so without the
existence check a *missing* shortcut would read as an installed-but-wrong one and
`--status` would lie in the more alarming direction.

**A shortcut cannot set an environment variable**, which makes `REFERAT_CONFIG`
the quiet failure of this step. A value set in the installing shell is not
inherited by anything Explorer starts at sign-in, so the tray would come up on
repo-root `config.toml` while the person who set it believed otherwise — the same
family of silent-wrong-thing as a `mic_device` substring that matches nothing.
The installer reads `HKCU\Environment` as well as its own environment, prints the
config file the autostarted tray will actually load, and names `setx` as the fix.
Rejected: wrapping the target in `cmd /c set ... && start ...`, which flashes a
console window — the one thing the whole `pythonw.exe` arrangement exists to
avoid.

**No icon on the shortcut, deliberately.** The tray icon is drawn at runtime by
PIL, there is no `.ico` in the repository, and a Startup-folder shortcut appears
in no Start menu and no taskbar — adding a binary artifact to decorate a file
nobody looks at is exactly the configurability the conventions forbid. It
inherits `pythonw.exe`'s icon.

**`SETUP.md` closes eleven boxes** scattered across steps 7b, 8, 9 and "Surfaced
later", and settles three things that had been left open as decisions rather than
tasks:

- **The lid.** `SetThreadExecutionState` suppresses the *idle* timer and nothing
  else, so closing the lid still suspends the machine mid-meeting. SETUP.md
  section 10 gives both the Settings path and the `powercfg` command and says
  plainly that Referat will not rewrite a system-wide power setting on your
  behalf. The distinction the old note was reaching for is that a document
  telling a person is not the program doing it silently.
- **Smart App Control.** Documented as the reason the PyAV and `torchcodec`
  workarounds exist, stated to be a one-way change Windows cannot undo without a
  reinstall, and not recommended.
- **The Hugging Face symlink warning.** One sentence, with Developer Mode as the
  fix. It appears on the very first transcription, looks alarming, and had
  already been re-diagnosed once.

The section that matters most is the token one, because its failure mode is
invisible: an unaccepted gated repo is a `403`, a `403` costs speaker labels and
nothing else, and the transcript arrives on time with every remote line reading
`REMOTE:`. SETUP.md says where to look — `meta.json` →
`transcription.channels.system.diarization.reason` — because nothing in the
transcript will ever tell you.

`README.md`'s Status section still claimed "Build step 5 of 9" and mentioned
neither diarization, nor speaker names, nor the CLI; it now describes what
actually exists and opens Quick start with a link to SETUP.md. Leaving the
repository's front door three steps stale next to a brand-new install guide would
have made it the only file in the repo that was wrong. `.gitignore` gains
`*.lnk`, since both SETUP.md and `--status` invite you to go and look at the
shortcut.

**Verified by driving it, as at every step so far.** `--status` before install
reported not-installed at exit 1; the install wrote the `.lnk` and its own
read-back agreed; **an independent PowerShell one-liner** — not the script's own
reader — confirmed all four fields exactly, which is the check that actually
proves anything. `pythonw.exe -c "import referat.tray"` imports, a genuinely
different interpreter binary from `python.exe`. Installing again printed
"already installed" and left the mtime **unchanged**. A decoy `Referat.lnk`
pointing at `notepad.exe` was refused by both `install` and `--uninstall` at exit
1 and replaced by `--force`; `--uninstall` twice in a row was idempotent.
`startup_dir()` and the `%APPDATA%` join were printed side by side and agree. The
shell-only `REFERAT_CONFIG` warning fired and named the right file, without
anything being persisted.

**The launch chain was proved once, end to end**: the shortcut's exact command
line was started, registered both hotkeys, and wrote a `status.json` that
`referat status` read back live at exit 0 with the right pid. Killing that
process then exercised `referat status`'s **stale** branch against a genuinely
dead tray — which TODO had recorded as only ever tested against a faked status
file pointing at a stand-in child process. Half of that box is now closed; the
busy-tray guard in `rerun` is not.

Every read-only command SETUP.md contains was run and its output compared against
what the document claims, and every factual claim was cross-checked against the
code rather than against memory — the hotkey defaults against `HotkeysConfig`,
the token path against `PathsConfig`, the meta.json key path against
`_transcription_meta` and `Diarization.to_json`, the staging default against
`paths.default_staging_dir`.

**And verification turned up something that has nothing to do with step 9 and
matters more than it.** `scripts/install_autostart.py` began the session
importing `referat` cleanly and ended it unable to: `.venv\Scripts\referat.exe`
and `referat-tray.exe` had disappeared, and `site-packages\referat-0.1.0.dist-info`
was an empty directory. This repository lives inside the Dropbox tree, `.venv` is
only *gitignored* — which does nothing to stop a sync client — and Dropbox is
quietly removing files out of it. `uv sync` repairs it and nothing prevents a
recurrence; the choice between a Dropbox ignore flag on `.venv` and moving the
checkout out of the synced tree is recorded in TODO rather than made here. It is
the same class of mistake the meetings folder already hit, with the same answer.

The script no longer depends on the outcome. Running a script puts the *script's*
directory on `sys.path` and not the working directory, so
`scripts/install_autostart.py` could never see the package beside it and was
relying on the editable install; it now puts the repository root on the path
itself, from `__file__`, and works from any working directory. Worth noting that
this makes the shortcut's `WorkingDirectory` load-bearing for the moment rather
than merely tidy: `-m referat.tray` currently finds the package only because
`-m` puts the working directory on `sys.path`.

**Not verified, and recorded rather than claimed:** that the shortcut fires *at
sign-in*. Nothing in an automated session can produce a logon, so the command
line is proved and the trigger is not — it needs one sign-out. Nor can SETUP.md
prove that nothing is *missing*: it was written from this laptop's history, and
the only test of that is a machine which has none of this.

## 2026-08-28 — `referat devices`, and the mic pinned to the Jabra

A Jabra Speak 510 was plugged in, which raised a question the CLI could not
answer: **what is this device called?** `[audio].mic_device` and
`[audio].loopback_device` are substring matches, and a substring that matches
nothing falls back to the default *silently* — a warning in a log nobody reads
until after the meeting. The only way to learn a device's name was to guess, or
to record something and read the log afterwards.

- `referat devices` lists every input device and every WASAPI loopback source,
  marking the one the config resolves to `<- config` and the one Windows would
  pick `(default)`. It calls `recorder.resolve_mic_device` and
  `recorder.resolve_loopback_device` rather than reimplementing their rules: a
  listing that disagreed with what actually gets recorded would be worse than no
  listing at all. When a configured substring matches nothing it says so above
  the table, which is the whole reason the command exists.
- `render_table` takes its right-aligned columns as an argument instead of
  closing over the module constant aimed at `list`'s five columns.
- `config.toml` pins `mic_device = "Jabra"`. Not only to survive Windows
  changing its mind about the default: PortAudio exposes the same microphone
  four times, and the default resolves to the **MME** copy at 44100 Hz, while
  naming it takes the WASAPI copy — natively 16 kHz mono, exactly
  `mic_samplerate`, so nothing resamples. `loopback_device` stays empty on
  purpose, so system audio follows whatever is actually playing.

The loopback marker is `(default)` rather than `<- config` when the configured
substring missed. `resolve_loopback_device` returns the default output's
loopback either way and has no way to say "I fell back", so the caller asks
`_matches` afterwards; without that the fallback row was labelled `<- config`
next to a config value it did not match.

**Not built, and deliberately:** a mic-only capture mode. The recorder still
opens both channels always, and one is absent only when its device fails to open.

## 2026-08-28 — Recording moves out of the synced folder

Follow-on from the move into Dropbox, and the more important half of it. **The
WAVs are never written into the meetings folder at all now.** A meeting is
created in `[paths].staging_dir` (`%LOCALAPPDATA%\Referat\recording` by default),
recorded there, transcribed there, and moved into `meetings_dir` only once
`release_audio_if_clean` has deleted the audio.

- `[paths].staging_dir`, and `Config.staging_dir()` / `Config.meeting_roots()`
  beside `Config.voices_dir()`.
- `paths.move_meeting_dir` — same volume in the normal case, so a rename, so
  atomic: the folder is wholly in one place or wholly in the other, never
  half-copied into a folder a sync client is watching.
- `transcribe.promote_meeting` runs on a clean release. It never raises: a
  meeting that cannot be moved is still a finished meeting with a transcript in
  it, and losing the folder to a half-handled error is far worse than leaving it
  in the wrong place.
- `Meeting.create` takes the `Config` and creates in staging.
  `meeting.load_meetings` takes it too and spans both roots;
  `paths.find_meeting_dir` takes a sequence of roots. `label`, `rerun` and `list`
  all pass `meeting_roots()`, so a staged meeting stays visible to exactly the
  commands that would resolve it.
- `referat list` says `N still local with audio kept - run: referat rerun`.

**Deleting a file in a synced folder does not delete it.** This was the argument
that decided it. Dropbox keeps deleted files and prior versions on its own servers
for 30 days, 180 on some plans — so the audio would have been uploaded, then
"deleted", then retained off-machine for weeks while `meta.json` recorded
`audio_released: true`. Recordings of people who never asked to be recorded,
surviving on somebody else's infrastructure after Referat reported them gone. The
wasted bandwidth and the hazard of syncing a file that is still being appended to
are both real, and both smaller than that.

**Ids are reserved in both roots at creation**, so the later move never renames a
meeting. `new_meeting_dir` takes an `avoid` sequence and suffixes against every
root. Without it, a meeting recorded in the same minute as one already promoted
would get its id from staging, collide on the way in, and be renamed *after* that
id was written into `meta.json`, printed by `referat label <id>` and — at step 13
— used as its digest anchor. `promote_meeting` still syncs the id if it ever
happens, and logs a warning, but it should be unreachable.

**A meeting that keeps its audio stays in staging**, which is the case the quality
gate exists for. It is listed, labelable and rerunnable where it sits, and a rerun
that comes out clean promotes it. That keeps "no WAV ever reaches the meetings
folder" an invariant rather than a usual case — the alternative, moving it anyway,
would have parked audio in Dropbox in precisely the situation where the recording
is most worth not uploading. Noted under "Surfaced later": nothing prunes staging,
so a meeting that never transcribes cleanly sits there indefinitely.

Verified on temporary folders: creation lands in staging, a staged meeting is
listed and resolvable by id, promotion moves it and is idempotent, a kept-audio
meeting stays put and stays visible, and a same-minute collision gets its `_2` at
creation and keeps that id through the move.

## 2026-08-28 — The meetings folder moves into Dropbox, the voiceprints do not

`[paths].meetings_dir` is now `C:\Users\nikla\Dropbox\Research\Meetings`, so
transcripts and notes sync. That immediately broke the assumption `.voices/` had
been resting on, so the database got a way out of the synced tree.

- **`[paths].voices_dir`**, a new optional key. Unset, it derives
  `<meetings_dir>/.voices` exactly as before; set, it wins. `~/.referat/voices`
  on this machine.
- **`Config.voices_dir()` resolves it, and is now the only way to ask where the
  voiceprints are.** `VoicesDB.load` takes the whole `Config` rather than a
  meetings folder, which is what stops a caller re-deriving the old path by
  accident — the failure that would silently put the database back in Dropbox.
- `paths.voices_dir` grew an `override` argument. `paths.voices_path` is deleted
  rather than left standing as a second way to answer the same question without
  one; nothing used it.
- `ensure_voices_dir` now takes the folder itself. It used to accept either that
  or the meetings folder and tell them apart by checking for the name `.voices`,
  which stops working the moment the folder is allowed to be called `voices`.
- `_coerce` accepts `Path | None`, so an optional path key coerces like any other.
  With `from __future__ import annotations` the declared type arrives as the
  string `"Path | None"`, which is what it now matches.

**A config key, not a Dropbox ignore flag.** Dropbox can exclude a subfolder by
setting `com.dropbox.ignored` as an alternate data stream, and that was the
smaller change. It was refused because it has to be re-applied by hand every time
the folder is recreated, it lives nowhere in this repository, and when it is
missing nothing goes wrong *visibly* — the voiceprints just quietly start
uploading. A config key is checkable: `referat config` prints the resolved path,
and it is either inside the synced tree or it is not.

Nothing had to be migrated. No `.voices/` existed yet, because `owner_name` is
still empty and nobody has been labeled, so the folder was created fresh at the
new location with its `DO-NOT-SYNC.md`. The eight meetings in `~/Meetings` were
left alone: seven are the synthetic test recordings already listed for deletion,
and `2026-08-27_1906`, the one real call, is still there to move by hand.

**Recorded under "Surfaced later": Dropbox now syncs the meetings folder while a
meeting is recording.** `mic.wav` and `system.wav` are appended continuously with
incremental header rewrites, ~460 MB an hour across both, and only deleted once
the quality gate passes — so Dropbox is repeatedly uploading a file that is still
growing and holding handles on files the recorder is writing. Nothing has gone
wrong yet, but "recording robustness beats everything else" makes it worth
watching on the first long meeting. The fallback is to record to a local staging
folder and move the meeting in once transcription is done.

## 2026-08-28 — Per-project digests planned

Planning only. **Nothing was built**: this session touched `TODO.md`,
`CLAUDE.md`, `INDEX.md` and this file, and no source, template or extension
code. The build order grows from twelve steps to thirteen. Nothing was
renumbered — step 13 is appended, the way 7b was lettered in.

The feature had never actually been written down anywhere in this repository.
The only trace of the idea on disk was `[[Wikilinks]]` *for people and projects*
in the `/cleanup` prompt spec, so this is the whole design rather than a
revision to something already recorded.

- **A project is a thread of work spanning many meetings**, linked to one Google
  Doc — its digest — into which every meeting assigned to it is written as a
  dated block. The doc is the shareable artifact; the meetings folder stays
  local. `<meetings_dir>/projects.toml` holds the project list and the link
  state.
- **Assignment is manual, with a remembered default.** `referat project assign
  <meeting-id> [<project>]`, defaulting to whichever project was assigned last.
  Nothing is inferred from the transcript, nothing is assigned at the end of the
  pipeline, and re-routing is the same command run again.
- **Linking is create-or-select.** *Create new doc* makes `<Project> Meeting
  Digest` and uses its default tab. *Select existing doc* searches Drive with
  `files.list` filtered to Google Docs, then requires a tab named `Meetings`.
- **Three new modules**, all step 13: `referat/projects.py` (the projects file
  and assignment), `referat/digest.py` (the Markdown translator, the anchor scan
  and the diff — pure, no Google import), and `referat/gdocs.py` (the client,
  behind a new `digest` extra). `project list` and `project assign` need no
  extra at all, which keeps step 8's rule that the CLI stays cheap.
- **`meta.json` gains `project` and `digest`** — the latter recording
  `gdoc_id`, `tab_id`, `notes_sha256` and `written_at` per write. The folder
  contract table is unchanged: no new file appears in a meeting folder.
- **The extension gains a Projects section**, written up under step 11: a node
  per project, *Link* / *Unlink* / *Open* / *Sync now*, a per-meeting *Assign to
  project* QuickPick, and an **Unassigned** node that is the queue. All of it
  shells out to `referat project ...`, the same rule the labeling webview
  already has.

**There is no web UI in this project, and that is now a convention.** The tray
icon and the VS Code extension are the only graphical surfaces; no Flask, no
FastAPI, no localhost dashboard. Written into `CLAUDE.md` beside the offline and
no-API-key rules, because a projects-and-digests feature is exactly the point at
which a later session reaches for a web front end without anyone deciding to.

**Reconciliation, not append.** Each block starts with an anchor paragraph
reading `[referat:2026-08-27_1400]`; `referat project sync` reads the tab, diffs
the anchors against the meetings assigned to the project, inserts what is
missing *in date order*, and re-renders any block whose `notes.md` has changed —
judged by `sha256` against the hash recorded when the block was written. A plain
append cannot survive `/cleanup` being re-run, and it cannot backfill, so
linking an existing doc now fills it in automatically because linking ends by
syncing.

**A visible text anchor rather than a Docs named range.** Named ranges are the
API's own mechanism and are the fragile choice: invisible to a person editing
the doc, destroyed along with their content, and not carried by a copy of the
document. A text anchor survives everything a person can do except deleting that
one line, and deleting it merely makes the reconciler re-append the block —
recoverable, where a silently-lost named range is not.

**Orphans are reported, never deleted.** A block whose meeting is no longer
assigned stays put; the doc may be shared and somebody may have written around
it. `sync --prune` is the explicit opt-in. This is the same instinct as keeping
the audio when a channel transcribes badly.

**Every write is located by `tab_id`.** `documents.get` always passes
`includeTabsContent=True`, and every `batchUpdate` request carries `tabId`.
A request without one silently targets the *first* tab, which would write a
meeting into an unrelated part of somebody's document with no error to notice —
the kind of failure that is found weeks later by a reader, not by the code.
Tabs also cannot be created through the API at all, which is why selecting an
existing doc hands off to the browser rather than making the tab itself.

**One `insertText`, then N style requests.** The translator builds the block's
plain text with a running offset, recording style spans as it goes, then emits a
single insert followed by the styling — style requests move no indices, so the
spans stay valid. Two traps recorded with it: Docs indices are **UTF-16 code
units**, so one emoji in a note shifts everything after it, and
`createParagraphBullets` converts *existing* paragraphs, so the inserted text
must not contain the `- ` characters itself. No raw Markdown may appear in the
doc, asserted by scanning the rendered plain text.

**Blocks open with a Heading 3 `YYYY-MM-DD — <title>` line**, the title taken
from the H1 of `notes.md` exactly as `referat index` does it. Also noted, in
`CLAUDE.md`: the Docs API cannot insert an @-date smart chip — there is no
request type for one. Keeping the date as a fixed-width prefix in leading
position is what would make swapping it for a chip a one-request change to that
line if the API ever gains one.

Two things left open under "Surfaced later". **How Referat authenticates to
Google is undecided**, deliberately: a desktop OAuth client follows the existing
`hf_token_file` precedent and lets Drive search see the user's own Drive, which
the picker needs; a service account avoids the browser flow but only sees
documents shared with it, which breaks that picker. And **a shared doc is a much
wider blast radius than a shared folder** — the digest is the first thing
Referat has ever sent anywhere, so the `/cleanup` prompt wants re-reading with
that in mind before the first doc is shared. The transcript, the audio and
`.voices/` never leave the machine at all; that is now its own convention.

## 2026-08-28 — The CLI: what is there, what is running, and running it again

Build step 8. `referat list`, `referat status` and `referat rerun <id>` are real;
`label` has been since 7b. Nothing new happens during a meeting — this is the
half of Referat that answers questions about meetings that already happened.

- `referat list`: one row per meeting, oldest first, with duration, status,
  whether the WAVs are still on disk, and how many speakers are still numbers.
  That last column is the queue for `referat label`, and the footer names the
  command only when there is a backlog. Until now the tray balloon at the end of
  a pipeline was the only thing that had ever mentioned it, and it is gone the
  moment it is dismissed.
- `referat status`: the state, the meeting, how long it has been going, the job
  count and the pid. `status.json` has been written on every transition since
  step 2 and nothing had ever read it. Exit 0 when a tray is running, 1 when
  none is, so it is usable from a script.
- `referat rerun <id>`: transcribe a meeting again from the audio it kept — the
  reason the pipeline keeps the WAVs of anything that looks unreliable, and the
  way to regenerate a transcript after the model or the diarization checkpoint
  in `config.toml` changes. New `rerun.py`, because it is the one command that
  needs the `transcribe` extra and importing it inside the function keeps three
  gigabytes of torch out of `referat list`.

**A rerun renumbers, so it clears before it runs.** Diarization clusters from
scratch, so this run's `SPEAKER_02` is not last run's: `speaker_names` and the
`speakers/` snippets are dropped before the pipeline starts. No name is lost by
that — every one of them is in the known-voices database, and identification
looks each cluster up there again on the way through. Carrying the old mapping
over is the thing that *would* lose them, by putting last week's name on this
week's numbering.

**`os.kill(pid, 0)` would have killed the tray.** The POSIX liveness probe does
not exist on Windows: CPython implements `os.kill` there with `TerminateProcess`
for every signal except `CTRL_C_EVENT` and `CTRL_BREAK_EVENT`. Asking whether the
tray is alive that way would have terminated it mid-meeting with both audio
streams open. `status.is_running` opens the process for
`PROCESS_QUERY_LIMITED_INFORMATION` and reads its exit code instead, the same
`ctypes`/`wintypes` shape as the single-instance mutex in `tray.py`.

**`rerun` refuses while a live tray is transcribing.** `transcribe._RUN_LOCK`
serializes jobs inside one process and cannot see across one, and two large-v3
models do not fit in 12 GB of VRAM. Recording is deliberately *not* in the way —
capture costs no GPU, and refusing during it would make the command unusable
through most of a working day. `--force` overrides, because the guard is about
the GPU rather than about correctness.

Verified against the eight meetings in `~/Meetings`: the listing, both refusals
(no such meeting, no audio left), the stale-status branch, and a faked live tray
for the guard and for `--force`. Then the real one — a `rerun` of
`2026-08-27_1906`, the 8-minute call from step 6. It had been recorded before the
loopback channel existed, so its `system.wav` had never been transcribed and
`release_audio_if_clean` had been sitting blocked on it ever since; the rerun
transcribed both channels, found the loopback silent as an in-person meeting
should be, and reclaimed the 65 MB.

## 2026-08-27 — Speaker identification: the numbers get names

Build step 7b, all of it. `SPEAKER_01` becomes `Anna` the second time she is in a
meeting, and the first time somebody says who she is. There is still no
enrollment step: diarization already computes one embedding per speaker cluster
in order to build the clustering, so every meeting is its own enrollment session.

- `voices.py`: the known-voices database at `<meetings_dir>/.voices/voices.json`
  — a name mapped to a *list* of embeddings, each stamped with the meeting,
  cluster and date it came from so a bad one can be traced back and pulled out.
  A list rather than an average, because the same person on a different headset
  lands somewhere else in the space and averaging would blur the one thing being
  matched on. Plus the cosine matching, the snippet cutting, and the owner
  bootstrap.
- `label.py` and `referat label`: play an unnamed speaker's snippets, ask who it
  was, and write the answer to the database, `meta.json` and the transcript's
  labels. A name, a number off the list of known names, `r` to replay, `s` to
  skip, `q` to stop; `difflib` catches typos and asks rather than silently
  creating a second person. `--forget <name>` deletes somebody outright.
- `diarize.py` harvests `DiarizeOutput.speaker_embeddings` and `assign` now
  returns the renaming map, so the vectors can be permuted with the labels.
- `[speakers]` config: `identify`, `owner_name`, `match_threshold`,
  `match_margin`, `snippets_per_speaker`, `snippet_seconds`.
- The tray says `Transcribed 2026-08-27_1400 (2 unknown voices — run: referat
  label 2026-08-27_1400)`, which is the only thing that ever mentions the
  command. Nothing extra when everybody was recognised.

**A match needs two things, not one.** The winner must clear `match_threshold`
*and* beat the runner-up **name** by `match_margin`. A voice sitting between two
known people is refused even when it comfortably clears the threshold, because
the failure mode being guarded against is not "no name" — it is *the wrong name*,
written into a transcript, where it looks exactly as authoritative as a right
one. Both numbers start strict and are still guesses.

The refusals are written to `meta.json` anyway, under each speaker's `match`, with
the score and the runner-up. That is deliberate: the near-misses are the only
material there is for calibrating the two numbers, and throwing them away would
mean tuning blind.

**Three traps in the embeddings, all confirmed against the installed pyannote
4.0.7 rather than assumed.** The rows are ordered by `speaker_diarization.labels()`
while `diarize.py` reads its turns out of `exclusive_speaker_diarization` — safe,
but only because pyannote applies the same rename mapping to both, so the order
has to come from the first and nowhere else. The array can be `None`, and it is
**zero-padded** when the clustering produced fewer centroids than the annotation
has labels; a zero-norm row is filler, not a voiceprint, and filing one under a
name would make every later meeting match against noise. And then `assign`
renumbers everything by first appearance, so the vectors have to be permuted with
the labels. Any one of the three, mishandled, files a voice under somebody else.

Also confirmed and worth writing down: the checkpoint clusters with `VBxClustering`,
whose centroids are plain means of the **raw** embeddings, compared internally
with `metric="cosine"`. So cosine against a stored centroid is the same operation
pyannote performs on itself — no PLDA transform in the way.

**Snippets are cut during the pipeline**, not on demand, because the WAVs they
come from are deleted as soon as the meeting transcribes cleanly and offsets
would point at nothing by the time anyone ran `referat label`. They are cut only
for speakers nobody could name, and deleted the moment they get a name. Measured:
about 560 KB per unknown speaker.

**The owner bootstrap declines more often than it fires.** `mic.wav` is one
speaker by definition, so it yields a voiceprint with no diarization at all —
except in a meeting held in person, where the microphone hears the whole room and
the embedding is a blend of everybody. The guard is that the loopback channel had
voice in it, which makes a Zoom or Teams call the unambiguous case. Verified both
ways, including a meeting whose loopback is silence and one Windows chime.

`owner_name` turned out not to need the "seed it the first time I label myself"
mechanism the plan described. There is no `SPEAKER_NN` for the mic channel to
label in the first place, and `config.py` never writes the TOML back, so it is
simply a key you set by hand. Left empty by default: it is a decision about your
own biometric data.

**`--forget` had a hole in it, found by reading the `meta.json` it left behind.**
Reverting the labels and deleting the embeddings was not enough — the per-channel
`speakers` block still carried `"name": "Anna"` and a `match` block naming her, so
a deletion that was supposed to leave nothing behind left her name in two more
places. It now clears both. The test greps the entire meetings folder for the name
afterwards and requires zero hits.

A smaller hole with it: the snippets are deleted when a speaker is named, so a
forgotten person leaves a `SPEAKER_NN` with no audio to play, and `referat label`
would have offered them and skipped forever. It now prints three of their lines
and lets you name them from those — the embedding is untouched, so it is only the
listening that was lost.

**Verified end to end on two synthetic meetings**, 197 checks: both speakers found
with embeddings and snippets and no names against an empty database; both named;
a second meeting with the same voices recognising them **without being asked**;
`--forget` reverting both transcripts and leaving no trace; the in-person case
declining to bootstrap the owner. Both TTS voices matched at 0.98 and 0.96 with
the runner-up around 0.5 — a separation that says the plumbing is right and
nothing at all about the thresholds, since identical synthesis of the same voice
is the easiest case there is. The 0.70 is still a guess, and step 7's open item —
diarization accuracy on a real call — still comes first.

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
