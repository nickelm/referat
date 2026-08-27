r"""Keeping Windows awake for the length of a meeting.

A meeting that nobody types through is exactly the case the idle sleep timer
was built for, and a suspended process records nothing. So Referat holds
``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`` while the
recorder is RECORDING or PAUSED, and drops it on stop. Transcription runs
without the hold: a job can outlive its meeting by many minutes, and none of
that is a reason to keep the laptop awake.

**Why there is a thread in here.** ``SetThreadExecutionState`` is per *thread*:
the flags belong to the thread that called it and die with that thread. Referat
changes state from at least three — the ``keyboard`` hook thread on a hotkey,
the pystray thread on a menu click, and a transcription thread finishing a job
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

    :meth:`set` is the whole interface; it records what is wanted and wakes the
    keeper, which makes the actual call. Nothing here raises — failing to take
    the hold is worth a warning in the log, never an aborted recording.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
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

    def set(self, hold: bool) -> None:
        """Ask for the hold to be taken or dropped. Safe from any thread, idempotent."""
        with self._cond:
            if self._closed or self._wanted == hold:
                return
            self._wanted = hold
            self._cond.notify_all()

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
        log.info("sleep hold %s", "taken" if hold else "released")
