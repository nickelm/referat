r"""Keeping Windows awake for the length of a meeting.

A meeting that nobody types through is exactly the case the idle sleep timer
was built for, and a suspended process records nothing. So Referat holds
``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`` while the
recorder is RECORDING or PAUSED — and, since 2026-09-03, while a transcription
job or a cleanup pass is running.

**That second half reverses a deliberate decision, so here is what changed it.**
This file used to say "Transcription runs without the hold: a job can outlive its
meeting by many minutes, and none of that is a reason to keep the laptop awake."
The reasoning was about *cost* and it was right about the cost; what it had not
priced is that the machine idling to sleep in the middle of a CUDA job is not a
pause but a hazard. On 2026-09-03 a meeting stopped at 15:26, diarization began
at 15:29, and the machine entered Modern Standby at 16:01 with the job still in
flight; it was still unfinished at 17:04. Keeping a laptop awake for the five
minutes a transcription takes is a smaller price than a wedged GPU job and a
meeting stuck in `transcribing`.

**The hold is therefore keyed by reason** — :meth:`SleepBlocker.want` — because
it now has two independent owners. The recorder wants it while capturing and a
notes pass wants it while `claude` runs, and with a single boolean the one that
finished first would drop the hold out from under the other.

**None of this makes a job survive the lid closing**, and nothing in this process
can: see *What it does not do* below. What it removes is the case that actually
happened, which is the machine putting itself to sleep because nobody was typing.
:class:`SuspendWatcher` is the other half — it cannot prevent a suspend either,
but it makes one visible in the log afterwards.

**Why there is a thread in here.** ``SetThreadExecutionState`` is per *thread*:
the flags belong to the thread that called it and die with that thread. Referat
changes state from at least three — the ``keyboard`` hook thread on a hotkey,
the Qt GUI thread on a menu click or a window button, and a transcription
thread finishing a job
in :meth:`referat.state.Machine.end_job`. Setting the hold on one and clearing
it from another would silently leave the machine awake forever, so
:class:`SleepBlocker` owns a single keeper thread and every call to the API is
made there.

**What it does not do.** ``ES_SYSTEM_REQUIRED`` suppresses the *idle* timer and
nothing else. Closing the lid or choosing Sleep still suspends the machine
mid-meeting; that is a power-plan setting, not something this process can veto.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
"""Make the flags stick until they are cleared, rather than resetting the idle timer once."""

ES_SYSTEM_REQUIRED = 0x00000001
"""Do not let the system sleep. Deliberately without ES_DISPLAY_REQUIRED: the screen may blank."""

HOLD = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
RELEASE = ES_CONTINUOUS

CLOSE_TIMEOUT_SECONDS = 2.0

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
_kernel32.SetThreadExecutionState.restype = ctypes.c_uint32


class SleepBlocker:
    """The sleep hold, owned by one keeper thread and driven from any other.

    :meth:`want` is the whole interface; it records which reasons are asking and
    wakes the keeper, which makes the actual call. Nothing here raises — failing
    to take the hold is worth a warning in the log, never an aborted recording.

    :meth:`set`, :meth:`hold` and :meth:`release` remain as the single-reason
    shorthand they always were, and now name the reason `recording` explicitly.
    Nothing in the package calls them since the tray moved to :meth:`want`; they
    are kept because a one-owner caller is a reasonable thing to be, not because
    anything depends on them.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._reasons: set[str] = set()
        self._wanted = False
        self._applied = False
        self._held = False
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="power", daemon=True)
        self._thread.start()

    # --- Driven by the tray --------------------------------------------------

    @property
    def held(self) -> bool:
        """Whether the hold is believed to be in force right now."""
        with self._cond:
            return self._held

    @property
    def reasons(self) -> set[str]:
        """Every reason currently asking for the hold, for whatever is drawing state."""
        with self._cond:
            return set(self._reasons)

    def want(self, reason: str, hold: bool) -> None:
        """Register or drop one *reason* to stay awake. Safe from any thread, idempotent.

        The hold is in force while any reason is registered, which is what lets
        the recorder and the notes queue ask independently. A single boolean was
        enough while only the recorder asked; with two owners it would let
        whichever finished first drop the hold out from under the other, and the
        failure would be invisible — the machine simply sleeping mid-job, which
        is the thing this module exists to prevent.

        Reasons are deduplicated by name rather than counted, so asking twice for
        the same one and dropping it once does the obvious thing. Every caller
        here holds a reason for a whole phase, not per item.
        """
        with self._cond:
            if self._closed:
                return
            if hold:
                self._reasons.add(reason)
            else:
                self._reasons.discard(reason)
            wanted = bool(self._reasons)
            if wanted == self._wanted:
                return
            self._wanted = wanted
            self._cond.notify_all()

    def set(self, hold: bool) -> None:
        """Take or drop the recorder's hold. Kept as the one-reason shorthand."""
        self.want("recording", hold)

    def hold(self) -> None:
        self.set(True)

    def release(self) -> None:
        self.set(False)

    def close(self) -> None:
        """Drop the hold and stop the keeper. Safe to call twice."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._reasons.clear()
            self._wanted = False
            self._cond.notify_all()
        self._thread.join(timeout=CLOSE_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            log.warning("the power keeper thread did not exit in time")

    # --- The keeper thread ---------------------------------------------------

    def _run(self) -> None:
        with self._cond:
            while True:
                while self._wanted == self._applied and not self._closed:
                    self._cond.wait()
                if self._closed:
                    break
                self._apply(self._wanted)
            if self._held:
                # Exiting with the flags still set would outlive this thread
                # only by accident; clear them deliberately.
                self._apply(False)

    def _apply(self, hold: bool) -> None:
        """Call the API. Runs on the keeper thread with the condition lock held."""
        flags = HOLD if hold else RELEASE
        # Recorded before the call succeeds, so a failing API cannot spin the
        # keeper: it is what was last asked of Windows, not what Windows did.
        self._applied = hold
        previous = _kernel32.SetThreadExecutionState(ctypes.c_uint32(flags))
        if previous == 0:
            log.warning(
                "SetThreadExecutionState(0x%08X) failed (error %d)",
                flags,
                ctypes.get_last_error(),
            )
            return
        self._held = hold
        # The reasons are in the line because this is the only record of *why*
        # the machine stayed awake, and "held for 40 minutes" is a question
        # somebody asks of the log long after the job that asked for it is gone.
        log.info(
            "sleep hold %s%s",
            "taken" if hold else "released",
            f" ({', '.join(sorted(self._reasons))})" if self._reasons else "",
        )


class SuspendWatcher:
    """Notice that the machine was suspended, and say so in the log.

    **Why this is worth a thread.** On 2026-09-03 a transcription job went into
    diarization at 15:29 and the log's next line never came; the machine entered
    Modern Standby at 16:01 and was opened again at 17:04, and reconstructing
    that afterwards took the Windows event log. `CLAUDE.md` already names "a
    clean log that simply stops" as the signature of the heap corruption, and a
    suspend produces exactly the same picture. One line saying *the machine was
    away for 63 minutes* separates the two in a second.

    **It detects, and it cannot prevent.** ``ES_SYSTEM_REQUIRED`` suppresses the
    idle timer; closing the lid is a power-plan action no process can veto. So a
    job interrupted that way is still interrupted — what changes is that it stops
    being invisible, and that whatever the tray does about it can be driven from
    a real event rather than from somebody noticing hours later.

    **The wall clock is the signal, deliberately.** A suspend freezes this thread
    and ``time.time()`` comes back from the RTC having advanced anyway, so a tick
    that should have taken :data:`TICK_SECONDS` and took far longer is the whole
    detector. It cannot tell a suspend from the clock being set forward, and does
    not try: both are "this process lost time", which is what a reader needs to
    know. Modern Standby throttles rather than freezes, so a long enough standby
    trips it for the same reason and reads correctly.

    `describe` is asked what is in flight, and returns `""` when nothing is. It
    is also what makes the heartbeat free: the same callback that annotates a
    resume is logged periodically while it has anything to say, so a long job
    leaves a trail instead of a silence.
    """

    TICK_SECONDS = 20.0
    """How often to sample. Short enough to bound the heartbeat, long enough to cost nothing."""

    GAP_SECONDS = 60.0
    """Lost time beyond the tick before it counts as a suspend rather than scheduling noise."""

    HEARTBEAT_SECONDS = 300.0
    """How often a job in flight says it is still there."""

    def __init__(self, describe: Callable[[], str], on_gap: Callable[[float], None] | None = None):
        self._describe = describe
        self._on_gap = on_gap
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="suspend-watch", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Stop the watcher. Safe to call twice."""
        self._stop.set()

    def _run(self) -> None:
        last = time.time()
        spoke = last
        while not self._stop.wait(self.TICK_SECONDS):
            now = time.time()
            gap = now - last
            last = now
            if gap > self.TICK_SECONDS + self.GAP_SECONDS:
                # Nothing here may raise into the thread: this is diagnostics,
                # and a watcher that dies takes the next resume's record with it.
                self._say_gap(gap - self.TICK_SECONDS)
                spoke = now
            elif now - spoke >= self.HEARTBEAT_SECONDS:
                if busy := self._safe_describe():
                    log.info("still working: %s", busy)
                spoke = now

    def _say_gap(self, lost: float) -> None:
        busy = self._safe_describe()
        log.warning(
            "the machine lost %.0f minutes to sleep or a clock change%s",
            lost / 60.0,
            f"; {busy}" if busy else "",
        )
        if self._on_gap is None:
            return
        try:
            self._on_gap(lost)
        except Exception:
            log.debug("the suspend listener raised", exc_info=True)

    def _safe_describe(self) -> str:
        try:
            return self._describe()
        except Exception:
            log.debug("could not describe what is in flight", exc_info=True)
            return ""
