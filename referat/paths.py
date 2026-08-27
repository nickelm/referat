"""Filesystem locations Referat uses, and helpers for meeting folders.

Nothing here reads the config file; callers pass in the meetings directory they
got from :mod:`referat.config`. That keeps this module import-safe and free of
circular imports.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path

# --- Repository and packaged files -----------------------------------------

PACKAGE_DIR = Path(__file__).resolve().parent
"""The installed `referat/` package directory."""

REPO_ROOT = PACKAGE_DIR.parent
"""Repository root. Valid because Referat is installed editable on this machine."""

EXAMPLE_CONFIG_PATH = REPO_ROOT / "config.example.toml"
"""Template copied to `config.toml` on first run."""

MEETINGS_CLAUDE_TEMPLATE = REPO_ROOT / "templates" / "meetings-CLAUDE.md"
"""Template dropped into the meetings folder so Claude Code sessions start oriented."""

CONFIG_ENV_VAR = "REFERAT_CONFIG"


def config_path() -> Path:
    """Path to the active config file: `$REFERAT_CONFIG`, else repo-root config.toml."""
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "config.toml"


# --- Per-user state ---------------------------------------------------------


def state_dir() -> Path:
    """`%LOCALAPPDATA%\\Referat` — logs and the tray status file live here."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "Referat"


def status_path() -> Path:
    """Status file the tray app writes and `referat status` reads."""
    return state_dir() / "status.json"


def log_path() -> Path:
    """Rotating log file for the tray app and CLI."""
    return state_dir() / "referat.log"


# --- Meeting folders --------------------------------------------------------

MEETING_ID_FORMAT = "%Y-%m-%d_%H%M"

MIC_WAV = "mic.wav"
SYSTEM_WAV = "system.wav"
TRANSCRIPT_MD = "transcript.md"
META_JSON = "meta.json"


def meeting_id(started_at: dt.datetime) -> str:
    """Meeting id for a start time, e.g. `2026-08-27_1400`."""
    return started_at.strftime(MEETING_ID_FORMAT)


def parse_meeting_id(mid: str) -> dt.datetime:
    """Inverse of :func:`meeting_id`; ignores any `_2`-style collision suffix."""
    base = mid[: len(dt.datetime.now().strftime(MEETING_ID_FORMAT))]
    return dt.datetime.strptime(base, MEETING_ID_FORMAT)


def new_meeting_dir(meetings_dir: Path, started_at: dt.datetime) -> Path:
    """Create and return a fresh meeting folder, suffixing `_2`, `_3`, ... on collision."""
    base = meeting_id(started_at)
    candidate = meetings_dir / base
    n = 2
    while candidate.exists():
        candidate = meetings_dir / f"{base}_{n}"
        n += 1
    candidate.mkdir(parents=True)
    return candidate


def find_meeting_dir(meetings_dir: Path, mid: str) -> Path | None:
    """Look up an existing meeting folder by id."""
    candidate = meetings_dir / mid
    return candidate if candidate.is_dir() else None


def list_meeting_dirs(meetings_dir: Path) -> list[Path]:
    """All meeting folders, oldest first. A folder counts if it has a meta.json."""
    if not meetings_dir.is_dir():
        return []
    dirs = [p for p in meetings_dir.iterdir() if p.is_dir() and (p / META_JSON).exists()]
    return sorted(dirs, key=lambda p: p.name)


# --- Crash-safe writes ------------------------------------------------------


def write_text_atomic(path: Path, text: str) -> None:
    """Write text via a temp file plus `os.replace`, so readers never see a partial file.

    Referat's guiding rule is that a crash must never lose captured audio or
    corrupt what describes it, so every write of a file the user or Claude Code
    will read goes through here rather than truncating the target in place. It
    also makes `referat rerun` safe: a failed re-transcription leaves the
    previous `transcript.md` intact rather than a truncated one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    """Write JSON through :func:`write_text_atomic`."""
    write_text_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
