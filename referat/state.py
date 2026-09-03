"""The recorder state machine.

Pure logic: no filesystem, no audio, no UI. Everything that wants to react to a
transition registers a listener — the tray repaints its icon, `status.py` writes
`status.json`, and later build steps start and stop the audio streams.

The state describes the *recorder*, not the process as a whole. Transcription
runs in background threads counted by :meth:`Machine.begin_job` and
:meth:`Machine.end_job`, so a new meeting can start while the previous one is
still being transcribed — losing a meeting because the machine was busy would
violate the one rule that outranks everything else here.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

log = logging.getLogger(__name__)


class State(StrEnum):
    """Recorder states. The values match the `meta.json` `status` vocabulary."""

    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"
    TRANSCRIBING = "transcribing"


LEGAL: dict[State, frozenset[State]] = {
    # IDLE -> TRANSCRIBING is the resumed-job edge, and the only way into
    # TRANSCRIBING that did not just come off a recording. A meeting interrupted
    # by a kill or a lid close is re-queued when the tray next starts, and the
    # tray is idle at that moment; without this edge the machine would have to
    # either lie about its state or pretend to record something first.
    State.IDLE: frozenset({State.RECORDING, State.TRANSCRIBING}),
    State.RECORDING: frozenset({State.PAUSED, State.STOPPED}),
    State.PAUSED: frozenset({State.RECORDING, State.STOPPED}),
    # STOPPED -> IDLE covers a meeting with nothing worth transcribing.
    State.STOPPED: frozenset({State.TRANSCRIBING, State.IDLE}),
    # TRANSCRIBING -> RECORDING is the background-transcription edge: the job
    # keeps running in its own thread while the next meeting records.
    State.TRANSCRIBING: frozenset({State.IDLE, State.RECORDING}),
}


class IllegalTransition(RuntimeError):
    """Raised by :meth:`Machine.to` for an edge that is not in :data:`LEGAL`."""


@dataclass(frozen=True)
class Transition:
    """One accepted state change, handed to every listener."""

    frm: State
    to: State
    at: dt.datetime
    meeting_id: str | None


Listener = Callable[[Transition], None]


class Machine:
    """The state machine, safe to drive from any thread.

    Hotkey callbacks arrive on the `keyboard` hook thread, transcription jobs
    finish on their own threads, and Qt owns the main thread, so every mutation
    takes the lock. Listeners are called outside it, both to keep the
    lock short and so a listener may read the machine without deadlocking.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = State.IDLE
        self._meeting_id: str | None = None
        self._started_at: dt.datetime | None = None
        self._jobs = 0
        self._listeners: list[Listener] = []

    # --- Inspection ---------------------------------------------------------

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @property
    def meeting_id(self) -> str | None:
        """Id of the meeting being recorded, or None when idle."""
        with self._lock:
            return self._meeting_id

    @property
    def started_at(self) -> dt.datetime | None:
        """When the current meeting started recording, or None when idle."""
        with self._lock:
            return self._started_at

    @property
    def jobs(self) -> int:
        """How many transcription jobs are running right now."""
        with self._lock:
            return self._jobs

    @property
    def recording(self) -> bool:
        """True while audio should be captured — recording or paused."""
        with self._lock:
            return self._state in (State.RECORDING, State.PAUSED)

    # --- Listeners ----------------------------------------------------------

    def add_listener(self, listener: Listener) -> None:
        """Register a callback invoked, in registration order, after each transition."""
        with self._lock:
            self._listeners.append(listener)

    def _notify(self, transition: Transition) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(transition)
            except Exception:
                # A broken listener must never abort a recording or a stop.
                log.exception("state listener %r failed", listener)

    # --- Transitions --------------------------------------------------------

    def to(self, target: State, *, meeting_id: str | None = None) -> Transition:
        """Move to `target`, or raise :class:`IllegalTransition`.

        `meeting_id` is only honoured when entering RECORDING from IDLE or
        TRANSCRIBING; a resume from PAUSED keeps the meeting it already had.
        """
        with self._lock:
            current = self._state
            if target not in LEGAL[current]:
                raise IllegalTransition(f"{current} -> {target} is not a legal transition")

            now = dt.datetime.now()
            if target is State.RECORDING and current is not State.PAUSED:
                self._meeting_id = meeting_id
                self._started_at = now
            elif target is State.IDLE:
                self._meeting_id = None
                self._started_at = None

            self._state = target
            transition = Transition(frm=current, to=target, at=now, meeting_id=self._meeting_id)

        log.info(
            "%s -> %s%s",
            transition.frm,
            transition.to,
            f" ({transition.meeting_id})" if transition.meeting_id else "",
        )
        self._notify(transition)
        return transition

    def try_to(self, target: State, *, meeting_id: str | None = None) -> bool:
        """Like :meth:`to`, but log and return False instead of raising.

        A button pressed in the wrong state is a no-op, not a crash.
        """
        try:
            self.to(target, meeting_id=meeting_id)
        except IllegalTransition as exc:
            log.info("ignored: %s", exc)
            return False
        return True

    # --- Background transcription jobs --------------------------------------

    def begin_job(self) -> int:
        """Count one transcription job as started. Returns the new job count."""
        with self._lock:
            self._jobs += 1
            count = self._jobs
        log.debug("transcription jobs: %d", count)
        return count

    def end_job(self) -> int:
        """Count one transcription job as finished, returning to IDLE if that was the last.

        If the user started a new meeting in the meantime the machine is already
        RECORDING or PAUSED, and the finished job only decrements the count.
        """
        with self._lock:
            self._jobs = max(0, self._jobs - 1)
            count = self._jobs
            done = count == 0 and self._state is State.TRANSCRIBING
        log.debug("transcription jobs: %d", count)
        if done:
            self.to(State.IDLE)
        return count
