r"""The tray app: the recorder core, and the process entry point.

This is the only module that knows about all the others. It owns the
:class:`referat.state.Machine`, translates the two hotkeys into transitions,
drives the :class:`referat.recorder.Recorder`, holds sleep off through
:class:`referat.power.SleepBlocker`, writes `status.json`, and hands each stopped
meeting to :mod:`referat.transcribe` on a background thread.

**Nothing in here is Qt, and that is the point.** Until build step 20 this
module also drew a `pystray` icon and built its menu; the icon is now a
`QSystemTrayIcon` in :mod:`referat.ui.shell`, and what is left here is the part
that must come up whether or not any of that works. :func:`main` starts this
core first — hotkeys registered, `status.json` written — and only then imports
the Qt shell, so the ordering rule step 20 wrote down is a fact about this file
rather than an intention: *the recorder, the hotkeys, the state machine and
`status.json` come up first and independently, and the window is opened after.*

The one seam between the two is :attr:`App.notify`, which the shell replaces
with a real balloon. Its default logs, so every message this class raises still
lands somewhere when there is no UI at all.

Runs under `pythonw.exe` in production (see the `referat-tray` gui-script), so
there is no console: the rotating log in `%LOCALAPPDATA%\Referat` is the only
record of what happened. For development, `python -m referat.tray` keeps a
console attached.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes

from referat import gpu, status, voices
from referat.config import Config, ConfigError, load_config
from referat.hotkeys import Hotkeys
from referat.logging_setup import setup_logging
from referat.meeting import Meeting
from referat.power import SleepBlocker
from referat.recorder import Recorder, RecorderError
from referat.state import Machine, State, Transition
from referat.transcribe import transcribe_meeting

# Named explicitly so the log reads the same whether this ran as referat-tray
# or as `python -m referat.tray`, where __name__ would be "__main__".
log = logging.getLogger("referat.tray")

MUTEX_NAME = r"Local\ReferatTraySingleInstance"
ERROR_ALREADY_EXISTS = 183


# --- Single instance --------------------------------------------------------


def acquire_single_instance() -> wintypes.HANDLE | None:
    """Take the named mutex, or return None when another tray already holds it.

    Autostart plus a manual launch would otherwise leave two processes fighting
    over the same hotkeys and both writing `status.json`. The handle is returned
    so the caller can keep it alive for the lifetime of the process.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, wintypes.BOOL(True), MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


# --- The app ----------------------------------------------------------------


class App:
    """The recorder core: the state machine, the hotkeys and the meetings.

    Owns no widget and imports no toolkit. :mod:`referat.ui.shell` attaches to an
    instance of this — registering a listener on :attr:`machine` and replacing
    :attr:`notify` — and everything it drives goes through the public methods
    below, which are the same ones the hotkeys call.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.machine = Machine()
        self.recorder: Recorder | None = None
        self.power = SleepBlocker()
        self.notify: Callable[[str], None] = self._log_notification
        self._stopped = False
        self.hotkeys = Hotkeys(
            config.hotkeys,
            on_toggle_record=self.on_toggle_record,
            on_toggle_pause=self.on_toggle_pause,
        )
        # Power first, and as its own listener: Machine._notify calls listeners
        # in order and isolates their exceptions, so nothing registered later --
        # the status write, and the whole of the Qt shell -- can cost the machine
        # its sleep hold.
        self.machine.add_listener(self._on_transition_power)
        self.machine.add_listener(self._on_transition_status)

    @property
    def missing_channels(self) -> list[str]:
        """What this run failed to capture, for whatever is drawing the state."""
        return list(self.recorder.missing_channels) if self.recorder else []

    def _on_transition_power(self, transition: Transition) -> None:
        """Hold sleep off while audio is being captured, and only then."""
        self.power.set(transition.to in (State.RECORDING, State.PAUSED))

    def _on_transition_status(self, _transition: Transition) -> None:
        """Record the new state on disk, for `referat status` and the extension."""
        status.write_status(self.machine)

    # --- Recording ----------------------------------------------------------

    def on_toggle_record(self) -> None:
        """Start a meeting when there is none, otherwise stop the running one.

        The record hotkey is one button, so it has to mean both. The two halves
        are :meth:`start_meeting` and :meth:`stop_meeting`, which the command
        center's buttons call directly — the buttons know which one they mean,
        and there is still exactly one path into the state machine.
        """
        state = self.machine.state
        if state in (State.IDLE, State.TRANSCRIBING):
            self.start_meeting()
        elif state in (State.RECORDING, State.PAUSED):
            self.stop_meeting()
        else:
            log.info("record hotkey ignored in state %s", state)

    def start_meeting(self) -> None:
        """Open the streams first, then transition with the id of the real folder.

        The order matters: the transition carries the meeting id, and only
        :meth:`Recorder.start` knows it, because a folder collision can turn
        `2026-08-27_1400` into `2026-08-27_1400_2`.
        """
        if self.machine.state not in (State.IDLE, State.TRANSCRIBING):
            log.info("start ignored in state %s", self.machine.state)
            return
        recorder = Recorder(self.config)
        try:
            meeting = recorder.start()
        except RecorderError:
            # No audio device at all: stay idle rather than pretend to record.
            log.exception("could not start recording")
            self.notify("Could not start recording - no audio device would open.")
            return
        self.recorder = recorder
        if not self.machine.try_to(State.RECORDING, meeting_id=meeting.id):
            log.warning("recording refused by the state machine; closing %s", meeting.id)
            self._stop_recorder()
            return
        # After the transition, so the toast lands on a tray that is already
        # showing the hollow icon. A meeting held in a room *is* the microphone
        # channel, so this is the difference between recording and only looking
        # like it -- and on 2026-09-02 that difference was an INFO line nobody
        # read until the meeting was over and gone.
        if "mic" in recorder.missing_channels:
            self.notify(
                f"NO MICROPHONE - {meeting.id} is recording system audio only. "
                f"Stop, fix the mic, and start again."
            )
        elif recorder.missing_channels:
            self.notify(
                f"No system audio - {meeting.id} is recording the microphone only."
            )

    def stop_meeting(self) -> None:
        """End the running meeting and hand it to a background transcription job."""
        if self.machine.state not in (State.RECORDING, State.PAUSED):
            log.info("stop ignored in state %s", self.machine.state)
            return
        if self.machine.try_to(State.STOPPED):
            self._finish_meeting(self._stop_recorder())

    def on_toggle_pause(self) -> None:
        """Pause a running meeting, or resume a paused one."""
        state = self.machine.state
        if state is State.RECORDING:
            if self.machine.try_to(State.PAUSED) and self.recorder is not None:
                self.recorder.pause()
        elif state is State.PAUSED:
            if self.machine.try_to(State.RECORDING) and self.recorder is not None:
                self.recorder.resume()
        else:
            log.info("pause hotkey ignored in state %s", state)

    def _stop_recorder(self) -> Meeting | None:
        """Close the streams and finalize `meta.json`. Safe when nothing is recording.

        Returns the meeting so the caller can transcribe it; None when there was
        nothing recording, or when stopping failed badly enough that there is no
        meeting to hand on.
        """
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return None
        try:
            return recorder.stop()
        except Exception:
            # Whatever went wrong, the WAVs on disk are already playable.
            log.exception("could not stop the recorder cleanly")
            return recorder.meeting

    def _finish_meeting(self, meeting: Meeting | None) -> None:
        """Hand the stopped meeting to a background transcription job."""
        if meeting is None:
            # Nothing captured worth transcribing; STOPPED -> IDLE exists for this.
            log.warning("no meeting to transcribe")
            self.machine.try_to(State.IDLE)
            return
        self.machine.begin_job()
        self.machine.try_to(State.TRANSCRIBING)
        threading.Thread(
            target=self._transcribe, args=(meeting,), name="transcribe", daemon=True
        ).start()

    def _transcribe(self, meeting: Meeting) -> None:
        """The background job. Deliberately survives a new recording starting."""
        try:
            self._notify_transcribed(transcribe_meeting(meeting, self.config))
        except Exception:
            # Already recorded as `failed` in meta.json; the tray carries on and
            # `referat rerun` can try again.
            log.exception("transcription job for %s failed", meeting.id)
            self.notify(f"Transcription of {meeting.id} failed")
        finally:
            self.machine.end_job()
            # Ending a job while a new meeting records changes no state, but the
            # job count in status.json is still worth keeping honest.
            status.write_status(self.machine)
            # The last word on the GPU, after the pipeline's own release and
            # deliberately after the `except` above rather than inside it: a
            # release that runs while an exception is still propagating is
            # partial, because the traceback holds the frames that hold the
            # tensors. By here the exception is handled and the cycle collector
            # can reach them. This is the process the whole thing is about — an
            # idle tray sitting on 8.4 GB of a 12 GB card is what made a
            # `referat rerun` in a second process stall.
            gpu.release(f"the {meeting.id} job")

    # --- Notifications ------------------------------------------------------

    def _notify_transcribed(self, meeting: Meeting) -> None:
        """Say the meeting is done, and how many voices nobody has named yet.

        The count is the whole point of the message: a speaker only becomes a
        name if somebody names it, and nothing else in Referat ever asks. With
        everybody recognised it says nothing extra.
        """
        unknown = voices.unknown_speakers(meeting)
        message = f"Transcribed {meeting.id}"
        if unknown:
            message += (
                f" ({len(unknown)} unknown voice{'s' if len(unknown) > 1 else ''} — "
                f"run: referat label {meeting.id})"
            )
        self.notify(message)

    @staticmethod
    def _log_notification(message: str) -> None:
        """What :attr:`notify` does before a UI has replaced it, and if none ever does."""
        log.info("notification: %s", message)

    # --- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Register the hotkeys and publish the initial state. The recorder is live after this."""
        self.hotkeys.start()
        status.write_status(self.machine)
        log.info(
            "recorder running; %s to record, %s to pause",
            self.config.hotkeys.toggle_record,
            self.config.hotkeys.toggle_pause,
        )

    def shutdown(self) -> None:
        """Stop the hotkeys, close out any recording, and release the sleep hold.

        Idempotent: it is called by the tray's Quit item and again from
        :func:`main`'s `finally`, and a second pass must not try to stop a
        recorder that is already stopped.
        """
        if self._stopped:
            return
        self._stopped = True
        log.info("shutting down")
        self.hotkeys.stop()
        if self.machine.recording:
            # Never exit mid-meeting without closing it out first.
            log.warning("quit while %s; stopping the meeting", self.machine.state)
            self.machine.try_to(State.STOPPED)
            self._stop_recorder()
            self.machine.try_to(State.IDLE)
        self.power.close()
        status.clear_status()


def main() -> int:
    """Entry point for `referat-tray` and for `python -m referat.tray`.

    The order is the rule: config, logging, the single-instance mutex, then the
    **recorder core**, and only then the Qt shell. Everything a meeting needs is
    running before anything graphical is imported, so a Qt that will not load is
    an exception with a stack trace in the log rather than a silent failure to
    start — and the escape hatch step 20 wrote down, putting the icon back on
    `pystray`, is a change to these last four lines and to nothing else.

    A Qt that will not load still ends the process, because a tray with no icon
    is one nobody can see, quit, or tell apart from a crash. What *is* survivable
    is the command center failing to open, and that is guarded one layer down in
    :meth:`referat.ui.shell.Shell.open_window`.
    """
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"referat-tray: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.app.log_level)

    # Held for the lifetime of the process; releasing it would let a second
    # tray start and take the hotkeys.
    mutex = acquire_single_instance()
    if mutex is None:
        log.warning("another Referat tray is already running; exiting")
        print("referat-tray: already running", file=sys.stderr)
        return 1
    log.debug("single-instance mutex held (handle %s)", mutex)

    app = App(config)
    try:
        app.start()
    except Exception:
        log.exception("the recorder core would not start")
        return 1

    try:
        from referat.ui import shell

        return shell.run(app)
    except Exception:
        log.exception("the Qt shell would not start; there is no tray icon")
        print("referat-tray: the Qt shell would not start; see the log", file=sys.stderr)
        return 1
    finally:
        app.shutdown()
        log.info("tray exited")


if __name__ == "__main__":
    raise SystemExit(main())
