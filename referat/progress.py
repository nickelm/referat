r"""What the slow things are doing right now, for whatever wants to show it.

Referat's two long jobs are invisible while they run. A transcription is two
minutes of a GPU with nothing on screen but a tray icon that has gone a different
colour, and generating notes is a `claude` subprocess that could be thinking or
could be hung. Both were legible only by tailing the log, which is not a user
interface.

This is the seam they report through, and it is the same shape as
:attr:`referat.tray.App.notify`: **the work reports, and something else decides
whether anybody is looking.** Nothing here imports a toolkit, nothing here draws,
and nothing here is required — a job that never calls :func:`begin` simply does
not appear, which is what keeps `referat rerun` at a prompt exactly as it was.

**It never raises into the pipeline.** A progress report is worth nothing beside
a transcript, so every listener is called inside a `try` and a listener that
throws is logged and dropped from nothing — it stays registered, because a UI
that failed once will usually fail again and unregistering it would hide that.

**Listeners are called on the reporting thread**, which is a transcription
thread or the `claude` reader thread. A Qt listener must therefore marshal, and
:class:`referat.ui.shell.Bridge` is what does it — the lesson of the
2026-09-03 heap corruption, where `App.notify` did not.

The registry is *live state and not history*: a finished job is removed. What
happened is the log's business, and keeping a list of completed jobs here would
be a second, worse log that grows for the life of the process.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

log = logging.getLogger(__name__)

TRANSCRIBE = "transcribe"
NOTES = "notes"
SYNC = "sync"
"""A `referat project sync` pushing one project into its Google Docs.

Its own kind for the reason DAY is: keys are a namespace and :func:`begin`
replaces what is under one, and the Activity tab prints this value as a column
where two different jobs both saying the same word is a column carrying nothing.
A sync is also the only kind here that is network-bound rather than CPU- or
subprocess-bound, which is worth being able to tell apart when one is slow.
"""

DAY = "day"
"""A `/standup` pass over one day's notes.

A third kind rather than a second `notes` job, for two reasons. Keys are a
namespace and :func:`begin` *replaces* what is under one, so `notes:<date>`
beside `notes:<meeting-id>` would stay distinct only because meeting ids
happen to carry `_HHMM`. And the Activity tab prints this value as a column,
where two different jobs both saying `notes` is a column carrying nothing.
"""

RECAP = "recap"
"""A `/recap` pass over every note a project's meetings carry.

A fourth kind for the same two reasons as :data:`DAY`, and one more: its key
is `recap:<project-id>`, and a project id is a slug that could be spelled
exactly like a meeting id never is but also exactly like nothing forbids — so
sharing the `notes:` namespace would be relying on two id schemes staying
disjoint by accident. It joins the command center's one-worker queue rather than
growing a thread beside it: one rate limit, one folder.
"""
"""The kinds of job. A surface may group by these; nothing here does."""


@dataclass(frozen=True)
class Job:
    """One slow thing, and how far into it we are.

    `fraction` is `None` for a phase with no measurable end — loading a model,
    waiting on `claude` — and a surface renders that as a busy indicator rather
    than as zero percent. Zero percent is a claim about progress; `None` is the
    honest absence of one.
    """

    key: str
    kind: str
    title: str
    """What a person reads: a meeting id, normally."""
    phase: str
    fraction: float | None = None
    started_at: float = 0.0

    @property
    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)


_lock = threading.Lock()
_jobs: dict[str, Job] = {}
_listeners: list[Callable[[list[Job]], None]] = []


def add_listener(listener: Callable[[list[Job]], None]) -> None:
    """Register something that wants to know. Called on the reporting thread."""
    with _lock:
        if listener not in _listeners:
            _listeners.append(listener)


def remove_listener(listener: Callable[[list[Job]], None]) -> None:
    with _lock:
        if listener in _listeners:
            _listeners.remove(listener)


def active() -> list[Job]:
    """Every job running right now, oldest first. A copy, so a caller may hold it."""
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j.started_at)


def begin(key: str, kind: str, title: str, phase: str) -> None:
    """Announce a job. Replaces any job already under `key`.

    Replacing rather than refusing: a key is a kind and a meeting id, so a second
    `begin` under one means the first ended without saying so — a crash, or a
    path that forgot its `finally`. The new job is the true one and the stale
    entry must not outlive it on somebody's screen.
    """
    with _lock:
        _jobs[key] = Job(key=key, kind=kind, title=title, phase=phase, started_at=time.monotonic())
    _publish()


def step(key: str, phase: str | None = None, fraction: float | None = None) -> None:
    """Move a job on. Silently does nothing for a key nothing began.

    Silently, and that is deliberate: reporting is optional everywhere, so a
    caller that reports a step for a job it never began has made a mistake worth
    exactly nothing at runtime. `phase` left out keeps the phase, so a loop can
    report a fraction alone.
    """
    with _lock:
        job = _jobs.get(key)
        if job is None:
            return
        _jobs[key] = replace(
            job,
            phase=job.phase if phase is None else phase,
            fraction=fraction,
        )
    _publish()


def end(key: str) -> None:
    """Take a job off the list. Safe to call for a key that is not there."""
    with _lock:
        existed = _jobs.pop(key, None) is not None
    if existed:
        _publish()


def _publish() -> None:
    """Hand the current list to every listener. Never raises, whatever they do."""
    with _lock:
        listeners = list(_listeners)
        jobs = sorted(_jobs.values(), key=lambda j: j.started_at)
    for listener in listeners:
        try:
            listener(jobs)
        except Exception:
            # Logged and kept. A listener that failed once will usually fail
            # again, and dropping it here would make the failure invisible at
            # exactly the moment somebody is wondering why nothing updates.
            log.debug("a progress listener raised", exc_info=True)
