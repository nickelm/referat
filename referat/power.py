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


# --- Power events: the lid, and sleep --------------------------------------

PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013
DEVICE_NOTIFY_CALLBACK = 0x0002

LID_CLOSED = 0
LID_OPEN = 1


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


GUID_LIDSWITCH_STATE_CHANGE = _GUID(
    0xBA3E0F4D, 0xB817, 0x4094, (ctypes.c_ubyte * 8)(0xA2, 0xD1, 0xD5, 0x63, 0x79, 0xE6, 0xA0, 0xF3)
)
"""`{BA3E0F4D-B817-4094-A2D1-D56379E6A0F3}`: the lid switch, delivered as a power setting."""


class _POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [
        ("PowerSetting", _GUID),
        ("DataLength", ctypes.c_uint32),
        ("Data", ctypes.c_ubyte * 1),
    ]


_NOTIFY_CALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p
)


class _DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS(ctypes.Structure):
    _fields_ = [("Callback", _NOTIFY_CALLBACK), ("Context", ctypes.c_void_p)]


class PowerEvents:
    """Hear the lid close and the machine sleep, and say so to whoever asked.

    **Why this exists.** On 2026-09-10 a meeting ended at about 13:35, the lid
    was closed, and the recording ran on until somebody remembered it at 13:49:
    fourteen minutes of a closed laptop, the corridor and whoever spoke near it,
    transcribed and filed as the meeting. The rule that fell out of it is the
    one a person would state: *a closed laptop is not recording a meeting.* The
    sleep hold (:class:`SleepBlocker`) is about the other direction — keeping
    the machine awake for a meeting — and neither it nor anything else in this
    process can veto the lid; what this does is *notice*, in time for the
    recorder to stop cleanly rather than be found still running afterwards.

    **Three signals, in the order they arrive, and a fourth that is not here.**
    The lid switch comes first and by itself — `GUID_LIDSWITCH_STATE_CHANGE`
    through `PowerSettingRegisterNotification`, which fires on the *change* and
    so leaves a recording started with the lid already closed on a docked
    laptop alone. `PBT_APMSUSPEND` follows seconds later when the lid action is
    Sleep, and arrives on its own when Sleep was chosen from the menu or the
    power button pressed; Windows gives about two seconds after it, which is
    enough to close two WAV files whose headers are already synced every two
    seconds anyway. `PBT_APMRESUMEAUTOMATIC` (and `PBT_APMRESUMESUSPEND`, which
    is the same moment when a person caused the wake) says the machine is back,
    and is what the tray waits for before loading a model — a CUDA context
    that a suspend interrupted is the thing `reconcile_interrupted` exists to
    clean up after. The fourth signal is :class:`SuspendWatcher`'s wall-clock
    gap, which needs no registration and so is the fallback when none of these
    is delivered; the tray wires it to the same handler.

    **Callbacks rather than a window.** Both registrations take
    `DEVICE_NOTIFY_CALLBACK`, so no window handle is needed and no message loop
    is involved: Windows calls :attr:`_callback` on a thread of its own, exactly
    as the `keyboard` hook calls the hotkey handlers on one of theirs, and the
    handlers handed in here are the same methods those call. The callback holds
    the GIL for the few lines it runs and returns zero; everything it triggers
    happens through the state machine, which is safe from any thread.

    **Failing to register costs nothing but the feature**, and says so once in
    the log. `powrprof.dll` has had both calls since Windows 8, so this is
    defensive rather than expected — but the recorder must come up whether or
    not the lid can be heard, on the rule every optional thing in the tray runs
    under. The ctypes callback and the parameter struct are kept on the
    instance because Windows holds a raw pointer to them for as long as the
    registration lives, and a garbage-collected callback is a crash in the
    system's thread the next time the lid moves.
    """

    def __init__(
        self,
        *,
        on_lid_closed: Callable[[], None],
        on_suspend: Callable[[], None],
        on_resume: Callable[[], None],
    ) -> None:
        self._on_lid_closed = on_lid_closed
        self._on_suspend = on_suspend
        self._on_resume = on_resume
        self._lid: int | None = None
        self._suspend_handle = ctypes.c_void_p()
        self._lid_handle = ctypes.c_void_p()
        self._registered = False
        # Kept alive for the life of the registration - see the class docstring.
        self._callback = _NOTIFY_CALLBACK(self._dispatch)
        self._params = _DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS(self._callback, None)
        try:
            self._powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
        except OSError:
            log.warning("powrprof.dll would not load; the lid and sleep will not stop a meeting")
            self._powrprof = None
            return
        self._register()

    @property
    def registered(self) -> bool:
        """Whether Windows is delivering at least one of the two notifications."""
        return self._registered

    @property
    def lid(self) -> int | None:
        """The last lid state heard: :data:`LID_OPEN`, :data:`LID_CLOSED`, or None before any."""
        return self._lid

    def _register(self) -> None:
        dll = self._powrprof
        assert dll is not None
        dll.PowerRegisterSuspendResumeNotification.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        dll.PowerRegisterSuspendResumeNotification.restype = ctypes.c_uint32
        dll.PowerSettingRegisterNotification.argtypes = [
            ctypes.POINTER(_GUID),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        dll.PowerSettingRegisterNotification.restype = ctypes.c_uint32
        dll.PowerUnregisterSuspendResumeNotification.argtypes = [ctypes.c_void_p]
        dll.PowerUnregisterSuspendResumeNotification.restype = ctypes.c_uint32
        dll.PowerSettingUnregisterNotification.argtypes = [ctypes.c_void_p]
        dll.PowerSettingUnregisterNotification.restype = ctypes.c_uint32

        recipient = ctypes.cast(ctypes.pointer(self._params), ctypes.c_void_p)
        error = dll.PowerRegisterSuspendResumeNotification(
            DEVICE_NOTIFY_CALLBACK, recipient, ctypes.byref(self._suspend_handle)
        )
        if error:
            log.warning("could not register for suspend notifications (error %d)", error)
            self._suspend_handle = ctypes.c_void_p()
        error = dll.PowerSettingRegisterNotification(
            ctypes.byref(GUID_LIDSWITCH_STATE_CHANGE),
            DEVICE_NOTIFY_CALLBACK,
            recipient,
            ctypes.byref(self._lid_handle),
        )
        if error:
            log.warning("could not register for lid notifications (error %d)", error)
            self._lid_handle = ctypes.c_void_p()
        self._registered = bool(self._suspend_handle.value or self._lid_handle.value)
        if self._registered:
            log.info(
                "listening for %s",
                " and ".join(
                    name
                    for name, handle in (("the lid", self._lid_handle), ("sleep", self._suspend_handle))
                    if handle.value
                ),
            )

    def close(self) -> None:
        """Unregister both. Safe to call twice, and never raises."""
        dll = self._powrprof
        if dll is None:
            return
        try:
            if self._suspend_handle.value:
                dll.PowerUnregisterSuspendResumeNotification(self._suspend_handle)
                self._suspend_handle = ctypes.c_void_p()
            if self._lid_handle.value:
                dll.PowerSettingUnregisterNotification(self._lid_handle)
                self._lid_handle = ctypes.c_void_p()
        except Exception:
            log.debug("could not unregister the power notifications", exc_info=True)
        self._registered = False

    def _dispatch(self, _context: int, kind: int, setting: int) -> int:
        """Windows' entry point, on a thread of its own. Returns 0 whatever happens."""
        try:
            if kind == PBT_APMSUSPEND:
                log.info("the machine is going to sleep")
                self._on_suspend()
            elif kind in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                log.info("the machine is awake again")
                self._on_resume()
            elif kind == PBT_POWERSETTINGCHANGE and setting:
                self._on_setting(setting)
        except Exception:
            # Nothing may raise into Windows' thread; a handler that fails is a
            # log line, and the recorder is no worse off than before this existed.
            log.exception("a power event handler failed")
        return 0

    def _on_setting(self, setting: int) -> None:
        block = ctypes.cast(setting, ctypes.POINTER(_POWERBROADCAST_SETTING)).contents
        if bytes(block.PowerSetting) != bytes(GUID_LIDSWITCH_STATE_CHANGE) or block.DataLength < 1:
            return
        state = int(block.Data[0])
        previous, self._lid = self._lid, state
        if previous is None:
            # Windows reports the current state on registration; that is a fact
            # about now and not a change, and a docked laptop registering with
            # its lid shut must not stop anything.
            log.debug("lid is %s", "closed" if state == LID_CLOSED else "open")
            return
        if state == LID_CLOSED:
            log.info("the lid closed")
            self._on_lid_closed()
        else:
            log.info("the lid opened")
