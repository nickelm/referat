r"""Writing `notes.md`, by running `/cleanup` in the meetings folder.

**Referat does not summarise anything.** It spawns the official `claude` binary
under the user's own Claude Code subscription login, in the meetings folder,
where `.claude/commands/cleanup.md` is the prompt and `.claude/settings.json` is
the rule that keeps that pass out of `.voices/`. This module knows how to find
that binary and how to start it, and nothing about what a note should say — the
prompt is a versioned Markdown file precisely so it is refined by editing rather
than by changing code.

**No API key, ever.** `claude` owns all authentication; nothing here reads a
credential, and no Anthropic SDK is a dependency of this project. That rule is in
`CLAUDE.md`'s Conventions and this is one of the two modules it is about.

**`Bash` is not in the allowed tools and must never be.** The deny rule on
`.voices/**` binds the file tools only — a `cat .voices/voices.json` would walk
straight past it — so the pass is given `Read,Write,Glob` and no shell. `Glob`
returns paths rather than contents and cannot reach past the deny rule; it is
there because the prompt's wrong-meeting-id fallback lists the real ids with it.
The slash command's own frontmatter is the authority on that list.

**Finding the binary is the awkward half, and it is why this exists at all.**
`claude` is not on `PATH` on this machine: it ships inside the installed Claude
Code VS Code extension, in a directory whose name carries a version that changes
every few days. The extension asks VS Code, which follows its own extensions;
Python has no VS Code to ask, so it reads the same directory and honours the
same `.obsolete` file VS Code writes there — on 2026-09-03 that file listed
`2.1.252` as obsolete beside a live `2.1.258`, and a resolver that took the
newest directory would have picked a deleted one soon after.

**Resolved at spawn time and never persisted.** An absolute path stored anywhere
would still be there a week later pointing at a directory that has been removed,
and would fail at the moment somebody clicks *Generate notes*.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from referat import paths, progress
from referat.config import Config

log = logging.getLogger(__name__)

ALLOWED_TOOLS = "Read,Write,Glob"
"""What `/cleanup` may use. `Bash` is the line that does not move — see above."""

EXTENSIONS_DIR = Path.home() / ".vscode" / "extensions"
EXTENSION_PREFIX = "anthropic.claude-code-"
BINARY_SUFFIX = Path("resources") / "native-binary" / "claude.exe"

NO_BINARY = (
    "Could not find the claude binary. It is not on PATH on this machine and "
    "ships inside the Claude Code VS Code extension; set [cleanup].claude_binary "
    "in config.toml to its full path, or install it with "
    "`npm i -g @anthropic-ai/claude-code`."
)


def progress_key(meeting_id: str) -> str:
    """The :mod:`referat.progress` key for one meeting's cleanup pass."""
    return f"{progress.NOTES}:{meeting_id}"


def _obsolete() -> set[str]:
    """The extension directories VS Code has marked for deletion.

    Honoured rather than ignored because both live on disk at once: VS Code
    writes the new version beside the old, records the old one here, and removes
    it later. Taking the highest version number without reading this picks a
    directory that is about to disappear.
    """
    try:
        raw = json.loads((EXTENSIONS_DIR / ".obsolete").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {name for name, gone in raw.items() if gone} if isinstance(raw, dict) else set()


def resolve_claude(config: Config) -> str | None:
    """Where `claude` is right now, or None. Never cached — see the module docstring.

    In order: the configured path, the newest non-obsolete Claude Code extension,
    then `PATH`. The configured path is taken as given and not existence-checked
    away — somebody who set it deliberately is owed the failure they configured
    rather than a silent fallback to a different binary.
    """
    configured = config.cleanup.claude_binary.strip()
    if configured:
        return configured

    obsolete = _obsolete()
    candidates = []
    try:
        for folder in EXTENSIONS_DIR.iterdir():
            if not folder.name.startswith(EXTENSION_PREFIX) or folder.name in obsolete:
                continue
            binary = folder / BINARY_SUFFIX
            if binary.exists():
                candidates.append((_version(folder.name), binary))
    except OSError:
        log.debug("could not read %s", EXTENSIONS_DIR, exc_info=True)
    if candidates:
        return str(max(candidates)[1])

    return shutil.which("claude")


def _version(name: str) -> tuple[int, ...]:
    """`anthropic.claude-code-2.1.258-win32-x64` as `(2, 1, 258)`, for ordering.

    Compared as numbers rather than as a string, or `2.1.9` would sort above
    `2.1.258`. Anything unparseable sorts lowest rather than raising: a directory
    this cannot read a version out of is still a candidate, just the last one.
    """
    middle = name[len(EXTENSION_PREFIX) :].split("-")[0]
    parts = []
    for piece in middle.split("."):
        parts.append(int(piece) if piece.isdigit() else 0)
    return tuple(parts) or (0,)


def generate_notes(config: Config, meeting_id: str) -> tuple[bool, str]:
    """Run `/cleanup <meeting-id>` and report what happened. The only implementation.

    `referat notes <id>` is this printed and the command center's button is this
    in process, which is the arrangement every other write in this project has —
    see :func:`referat.label.name_speaker`. Returns the value-and-unprefixed-
    complaint pair :func:`referat.meeting.resolve_meeting` returns.

    **cwd is the meetings folder and never the repository.** The slash command,
    that folder's `CLAUDE.md` and the `.voices/` deny rule all live in its
    `.claude/`, and a pass run anywhere else would have none of them — including
    the deny rule, which is the one that matters.

    The lifecycle is **not** written here. `/cleanup` is forbidden from touching
    `meta.json`, and whoever spawned it records `notes-written` afterwards; that
    is :func:`referat.cli.set_notes_written`, and the caller does it so that the
    refusal it can return is the caller's to show.

    Blocking, and deliberately so: it reports through :mod:`referat.progress`
    while it runs, and a surface that must not block runs it on a thread. The
    tray already has that thread for transcription.
    """
    meetings_dir = config.paths.meetings_dir
    if not (meetings_dir / meeting_id).is_dir():
        return False, f"{meeting_id} is not in {meetings_dir}; only a promoted meeting has notes"
    return _spawn(
        config,
        prompt=f"/cleanup {meeting_id}",
        key=progress_key(meeting_id),
        kind=progress.NOTES,
        title=meeting_id,
        expect=meetings_dir / meeting_id / paths.NOTES_MD,
        what=f"notes for {meeting_id}",
    )


def day_progress_key(day: str) -> str:
    """The :mod:`referat.progress` key for one day's summary pass.

    A namespace of its own rather than `notes:<day>`, which would only stay
    distinct from a meeting's key because meeting ids carry `_HHMM` — and
    :func:`referat.progress.begin` *replaces* whatever is under a key, so a
    collision would silently erase a running job rather than fail.
    """
    return f"{progress.DAY}:{day}"


def generate_day_summary(config: Config, day: str) -> tuple[bool, str]:
    """Run `/standup <YYYY-MM-DD>` over that day's notes. The only implementation.

    The second caller of :func:`_spawn`, and the reason it exists. Everything
    about the invocation is the same as a cleanup's — the meetings folder as cwd,
    the same three allowed tools, no `Bash`, the same streamed events — because
    all of that is a property of *running Claude Code in that folder* rather than
    of what is being written.

    **Two guards, and neither is `generate_notes`'.** The date is validated as
    exactly `%Y-%m-%d` before it reaches either the prompt or a path: it is
    interpolated into both, which makes it the one injection-shaped surface here.
    And the day has to *have* notes — a `/standup` over nothing would spend a
    subprocess to write an empty file. Reusing the is-this-a-meeting-folder check
    would have silently accepted `days` as a meeting.

    No lifecycle is recorded afterwards, unlike a cleanup: a day is not a meeting
    and has no `meta.json` to hold a status. See
    :func:`referat.cli.write_day_summary`.
    """
    try:
        parsed = dt.datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return False, f"{day!r} is not a date; write it as YYYY-MM-DD"
    if parsed.strftime("%Y-%m-%d") != day:
        # `strptime` is lenient about zero padding, so `2026-9-3` parses happily
        # and would then name a *second* file for a day that already has one.
        # The canonical spelling is the file name, so it is the only one accepted.
        return False, f"write the date zero-padded: {parsed:%Y-%m-%d}, not {day!r}"

    meetings_dir = config.paths.meetings_dir
    noted = [
        folder
        for folder in sorted(meetings_dir.glob(f"{day}_*"))
        if (folder / paths.NOTES_MD).exists()
    ]
    if not noted:
        return False, (
            f"no meeting on {day} has notes yet; write some with `referat notes <id>` "
            f"and a summary will have something to read"
        )

    return _spawn(
        config,
        prompt=f"/standup {day}",
        key=day_progress_key(day),
        kind=progress.DAY,
        title=day,
        expect=meetings_dir / paths.DAYS_DIR / f"{day}.md",
        what=f"a summary of {day} ({len(noted)} meeting{'s' if len(noted) != 1 else ''})",
    )


def recap_progress_key(project_id: str) -> str:
    """The :mod:`referat.progress` key for one project's recap pass.

    Its own namespace for the reason :func:`day_progress_key` has one, and one
    more: a project id is a slug, and nothing stops a slug from being spelled
    like a meeting id.
    """
    return f"{progress.RECAP}:{project_id}"


def generate_recap(
    config: Config, project_id: str, *, bundle: Path, expect: Path, meetings: int
) -> tuple[bool, str]:
    """Run `/recap <project-id>` over a bundle already on disk. The spawn and nothing else.

    The third caller of :func:`_spawn`. Everything that makes this a *recap* —
    which meetings, in what order, with which shas, and whether the file that
    comes back matches the bundle it was given — is :mod:`referat.recap`'s and
    :func:`referat.cli.write_recap`'s, and this function knows only that the
    bundle must be there before the pass starts. That is checked rather than
    assumed, because a `/recap` handed no bundle is told to stop, and a clean
    exit with no file would then be reported as though claude had refused —
    when the caller had.
    """
    if not bundle.is_file():
        return False, f"no bundle at {bundle} for /recap to read; nothing was run"
    return _spawn(
        config,
        prompt=f"/recap {project_id}",
        key=recap_progress_key(project_id),
        kind=progress.RECAP,
        title=project_id,
        expect=expect,
        what=f"a recap of {project_id} ({meetings} meeting{'s' if meetings != 1 else ''})",
    )


def _spawn(
    config: Config,
    *,
    prompt: str,
    key: str,
    kind: str,
    title: str,
    expect: Path,
    what: str,
) -> tuple[bool, str]:
    """Run one slash command in the meetings folder and report what happened.

    Everything both passes share, which is everything but the prompt and what
    they are expected to leave behind. Factored out when the day summary arrived
    rather than copied, because the parts worth getting right — the deny rule
    reaching the pass, `Bash` staying out of `--allowedTools`, the streamed events
    that make a hang distinguishable from work — are exactly the parts a second
    copy would eventually differ on.

    **cwd is the meetings folder and never the repository.** The slash commands,
    that folder's `CLAUDE.md` and the `.voices/` deny rule all live in its
    `.claude/`, and a pass run anywhere else would have none of them — including
    the deny rule, which is the one that matters.

    Blocking, and deliberately so: it reports through :mod:`referat.progress`
    while it runs, and a surface that must not block runs it on a thread.
    """
    binary = resolve_claude(config)
    if binary is None:
        return False, NO_BINARY

    meetings_dir = config.paths.meetings_dir
    args = [
        binary,
        "-p",
        prompt,
        "--allowedTools",
        ALLOWED_TOOLS,
        "--permission-mode",
        "acceptEdits",
        # Without these two the pass is a black box: plain `-p` prints one blob
        # when it is finished, so the progress phase said "starting claude" for
        # the whole two minutes and there was no way to tell work from a hang.
        # `stream-json` emits one JSON object per event as it happens.
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    log.info("running %s in %s", " ".join(args[1:]), meetings_dir)
    progress.begin(key, kind, title, "starting claude")

    try:
        return _run(args, meetings_dir, key, what, expect)
    except FileNotFoundError:
        return False, f"could not run {binary}: it is not there any more"
    except Exception as exc:  # pragma: no cover - a spawn that fails oddly
        log.exception("could not run claude for %s", title)
        return False, f"could not run claude: {exc}"
    finally:
        progress.end(key)


TOOL_PHRASES = {
    "Read": "reading",
    "Write": "writing",
    "Edit": "editing",
    "Glob": "looking for",
}
"""How a tool call reads as a sentence. Only the three `/cleanup` may use, plus
`Edit`, which it may not — shown rather than hidden if it ever appears, because
a pass doing something it was not allowed is worth seeing rather than silently
rendering as a bare tool name."""


def _phase(event: dict[str, Any]) -> str:
    """One streamed event as a phase somebody can read, or `""` to ignore it.

    Tool calls first, because they are what a cleanup pass spends its time on and
    they name a file — *reading transcript.md* is the sentence that makes the
    difference between watching work and watching a spinner. Prose second, since
    the model narrates as it goes. Everything else — the init blob, rate-limit
    notices, usage accounting — is noise on a progress bar.

    A basename rather than a path: the pane is one line wide and the folder is
    the same for every file in the pass.
    """
    if event.get("type") == "result":
        return "finishing up"
    if event.get("type") != "assistant":
        return ""
    phase = ""
    for block in event.get("message", {}).get("content", []) or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            name = str(block.get("name", ""))
            target = block.get("input", {}) or {}
            what = target.get("file_path") or target.get("pattern") or target.get("path") or ""
            verb = TOOL_PHRASES.get(name, name.lower())
            phase = f"{verb} {Path(str(what)).name}".strip() if what else verb
        elif block.get("type") == "text" and not phase:
            # Only when no tool call is in the same message: what it is *doing*
            # beats what it is saying about it.
            if text := " ".join(str(block.get("text", "")).split()):
                phase = text[:100]
    return phase


def _run(args: list[str], cwd: Path, key: str, what: str, expect: Path) -> tuple[bool, str]:
    """Spawn, turn each streamed event into a progress phase, and judge the result.

    **The events are the point.** Plain `-p` prints one blob when the whole pass
    is over, so the phase read "starting claude" for two solid minutes and a hang
    looked exactly like work. `--output-format stream-json --verbose` emits one
    JSON object per event instead, and :func:`_phase` turns the interesting ones
    into a sentence — *reading transcript.md*, *writing notes.md* — so the
    activity strip tracks what the pass is actually doing.

    The verdict comes from the final `result` event rather than only from the
    exit code, because that event carries `is_error` and the message. The exit
    code is still checked: a `claude` that dies without emitting a result has no
    event to read.

    Every line goes to the log whatever it is, because a phase is a glimpse and a
    failure needs the rest.

    `windowsHide` has no Python equivalent, so `CREATE_NO_WINDOW` does the same
    job — without it the tray, which runs under `pythonw.exe` and owns no
    console, flashes one up for the life of the pass.
    """
    process = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env={**os.environ, "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"},
    )

    errors: list[str] = []
    result: dict[str, Any] = {}

    def read_events(stream) -> None:
        for line in stream:
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                # Not every line is an event — a warning on stdout, say. Logged
                # and skipped rather than allowed to end the stream.
                log.info("claude: %s", text[:400])
                continue
            if event.get("type") == "result":
                result.update(event)
            log.debug("claude event: %s", text[:400])
            if phase := _phase(event):
                progress.step(key, phase, None)

    def read_errors(stream) -> None:
        for line in stream:
            if text := line.rstrip():
                errors.append(text)
                log.info("claude stderr: %s", text)

    threads = [
        threading.Thread(target=read_events, args=(process.stdout,), daemon=True),
        threading.Thread(target=read_errors, args=(process.stderr,), daemon=True),
    ]
    for thread in threads:
        thread.start()
    code = process.wait()
    for thread in threads:
        thread.join(timeout=5)

    if result.get("is_error") or (result and result.get("subtype") != "success"):
        complaint = str(result.get("result") or result.get("subtype") or "it reported an error")
        return False, f"claude could not write {what}: {complaint[:300]}"
    if code != 0:
        complaint = next((line for line in reversed(errors) if line), f"exit code {code}")
        return False, f"claude could not write {what}: {complaint}"
    if not expect.exists():
        # Exit zero and no file. Reported rather than believed: after a cleanup
        # the caller is about to record `notes_written`, which would then be a lie
        # in `meta.json` about a file that is not there.
        return False, f"claude exited cleanly but wrote no {expect.name} for {what}"
    return True, f"wrote {what}"
