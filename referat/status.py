"""The tray status file, written on every transition and read by `referat status`.

One small JSON document in the state directory saying what the tray app is doing
right now. The tray writes it; the CLI reads it. Written through
:func:`referat.paths.write_json_atomic`, so a reader never catches a half-written
file, and the `pid` is recorded so `referat status` can tell a live tray from a
stale file left behind by a crash.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import json
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

from referat import __version__, paths
from referat.state import Machine, State

log = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5


@dataclass(frozen=True)
class Status:
    """A parsed `status.json`."""

    state: State
    pid: int
    meeting_id: str | None
    started_at: str | None
    jobs: int
    updated_at: str
    referat_version: str


def write_status(machine: Machine) -> None:
    """Snapshot the machine to `status.json`. Never raises — status is not worth a crash."""
    started_at = machine.started_at
    payload: dict[str, object] = {
        "state": str(machine.state),
        "pid": os.getpid(),
        "meeting_id": machine.meeting_id,
        "started_at": started_at.isoformat(timespec="seconds") if started_at else None,
        "jobs": machine.jobs,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "referat_version": __version__,
    }
    try:
        paths.write_json_atomic(paths.status_path(), payload)
    except OSError:
        log.exception("could not write %s", paths.status_path())


def clear_status() -> None:
    """Remove `status.json` on a clean exit, so `referat status` reports nothing running."""
    try:
        paths.status_path().unlink(missing_ok=True)
    except OSError:
        log.exception("could not remove %s", paths.status_path())


def read_status() -> Status | None:
    """Read `status.json`, or None when it is missing, unreadable, or malformed."""
    try:
        raw: Any = json.loads(paths.status_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return Status(
            state=State(raw["state"]),
            pid=int(raw["pid"]),
            meeting_id=raw.get("meeting_id"),
            started_at=raw.get("started_at"),
            jobs=int(raw.get("jobs", 0)),
            updated_at=raw.get("updated_at", ""),
            referat_version=raw.get("referat_version", ""),
        )
    except (KeyError, TypeError, ValueError):
        log.warning("ignoring malformed %s", paths.status_path())
        return None


def is_running(pid: int) -> bool:
    """Is that process still alive? Asked of the pid `status.json` recorded.

    A tray killed by a crash, a reboot or Task Manager leaves its status file
    behind, so the file alone cannot say whether anything is running. This can:
    open the process for the least privilege that answers the question and ask
    for its exit code.

    **Not `os.kill(pid, 0)`.** The POSIX liveness probe does not exist on
    Windows: CPython implements `os.kill` there with `TerminateProcess` for
    every signal except `CTRL_C_EVENT` and `CTRL_BREAK_EVENT`, so asking whether
    the tray is alive that way would kill it — mid-meeting, with both audio
    streams open. This is the whole reason the function is written out by hand.

    Access denied still means a process is there and counts as alive. Never
    raises; anything unexpected is reported as not running, which is the
    conservative answer for the only caller that acts on it (`referat rerun`
    refuses while a tray is transcribing).
    """
    if pid <= 0:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, wintypes.BOOL(False), wintypes.DWORD(pid)
        )
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except OSError:
        log.debug("could not query pid %d", pid, exc_info=True)
        return False
