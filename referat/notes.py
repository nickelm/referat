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

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from referat import progress
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
    binary = resolve_claude(config)
    if binary is None:
        return False, NO_BINARY

    meetings_dir = config.paths.meetings_dir
    if not (meetings_dir / meeting_id).is_dir():
        return False, f"{meeting_id} is not in {meetings_dir}; only a promoted meeting has notes"

    args = [
        binary,
        "-p",
        f"/cleanup {meeting_id}",
        "--allowedTools",
        ALLOWED_TOOLS,
        "--permission-mode",
        "acceptEdits",
    ]
    key = progress_key(meeting_id)
    log.info("running %s in %s", " ".join(args[1:]), meetings_dir)
    progress.begin(key, progress.NOTES, meeting_id, "starting claude")

    try:
        return _run(args, meetings_dir, key, meeting_id)
    except FileNotFoundError:
        return False, f"could not run {binary}: it is not there any more"
    except Exception as exc:  # pragma: no cover - a spawn that fails oddly
        log.exception("could not run claude for %s", meeting_id)
        return False, f"could not run claude: {exc}"
    finally:
        progress.end(key)


def _run(args: list[str], cwd: Path, key: str, meeting_id: str) -> tuple[bool, str]:
    """Spawn, stream the output into the progress phase, and judge the exit code.

    The last non-empty line becomes the phase, which is what makes this legible
    while it runs: a cleanup pass takes a minute or two and says what it is doing
    the whole time. The whole of the output goes to the log, because the phase is
    a glimpse and a failure needs the rest.

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

    tail: list[str] = []
    errors: list[str] = []

    def drain(stream, sink: list[str], phase: bool) -> None:
        for line in stream:
            text = line.rstrip()
            sink.append(text)
            if text:
                log.info("claude: %s", text)
                if phase:
                    progress.step(key, text[:120], None)

    threads = [
        threading.Thread(target=drain, args=(process.stdout, tail, True), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, errors, False), daemon=True),
    ]
    for thread in threads:
        thread.start()
    code = process.wait()
    for thread in threads:
        thread.join(timeout=5)

    if code != 0:
        complaint = next((line for line in reversed(errors) if line), f"exit code {code}")
        return False, f"claude could not write notes for {meeting_id}: {complaint}"
    if not (cwd / meeting_id / "notes.md").exists():
        # Exit zero and no file. Reported rather than believed: the caller is
        # about to record `notes_written`, which would then be a lie in
        # `meta.json` about a file that is not there.
        return False, f"claude exited cleanly but wrote no notes.md for {meeting_id}"
    return True, f"wrote notes for {meeting_id}"
