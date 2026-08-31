r"""The tray app: icon, menu, hotkey wiring, and the process entry point.

This is the only module that knows about all the others. It owns the
:class:`referat.state.Machine`, translates the two hotkeys into transitions,
drives the :class:`referat.recorder.Recorder`, holds sleep off through
:class:`referat.power.SleepBlocker`, repaints the icon, writes `status.json`, and
hands each stopped meeting to :mod:`referat.transcribe` on a background thread.

Runs under `pythonw.exe` in production (see the `referat-tray` gui-script), so
there is no console: the rotating log in `%LOCALAPPDATA%\Referat` is the only
record of what happened. For development, `python -m referat.tray` keeps a
console attached.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from ctypes import wintypes
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from referat import paths, status, voices
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

ICON_SIZE = 64
COLORS: dict[State, tuple[int, int, int]] = {
    State.IDLE: (128, 128, 128),
    State.RECORDING: (208, 32, 32),
    State.PAUSED: (224, 176, 32),
    State.STOPPED: (232, 128, 32),
    State.TRANSCRIBING: (48, 128, 216),
}
TOOLTIPS: dict[State, str] = {
    State.IDLE: "Referat - idle",
    State.RECORDING: "Referat - recording",
    State.PAUSED: "Referat - paused",
    State.STOPPED: "Referat - stopping",
    State.TRANSCRIBING: "Referat - transcribing",
}

# --- Icons ------------------------------------------------------------------


def icon_image(state: State) -> Image.Image:
    """A filled disc in the colour of the state, for the notification area."""
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = ICON_SIZE // 8
    draw.ellipse(
        (margin, margin, ICON_SIZE - margin - 1, ICON_SIZE - margin - 1),
        fill=COLORS[state] + (255,),
    )
    return image


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
    """Owns the state machine, the icon and the hotkeys for one process."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.machine = Machine()
        self.recorder: Recorder | None = None
        self.power = SleepBlocker()
        self._images = {state: icon_image(state) for state in State}
        self.icon = pystray.Icon(
            "referat",
            icon=self._images[State.IDLE],
            title=TOOLTIPS[State.IDLE],
            menu=self._build_menu(),
        )
        self.hotkeys = Hotkeys(
            config.hotkeys,
            on_toggle_record=self.on_toggle_record,
            on_toggle_pause=self.on_toggle_pause,
        )
        # Power first, and as its own listener: Machine._notify calls
        # listeners in order and isolates their exceptions, so a repaint that
        # throws can never cost the machine its sleep hold.
        self.machine.add_listener(self._on_transition_power)
        self.machine.add_listener(self._on_transition)

    def _on_transition_power(self, transition: Transition) -> None:
        """Hold sleep off while audio is being captured, and only then."""
        self.power.set(transition.to in (State.RECORDING, State.PAUSED))

    def _on_transition(self, transition: Transition) -> None:
        """Repaint the icon and record the new state on disk."""
        self.icon.icon = self._images[transition.to]
        self.icon.title = TOOLTIPS[transition.to]
        self.icon.update_menu()
        status.write_status(self.machine)

    # --- Hotkey actions -----------------------------------------------------

    def on_toggle_record(self) -> None:
        """Start a meeting when there is none, otherwise stop the running one."""
        state = self.machine.state
        if state in (State.IDLE, State.TRANSCRIBING):
            self._start_meeting()
        elif state in (State.RECORDING, State.PAUSED):
            if self.machine.try_to(State.STOPPED):
                self._finish_meeting(self._stop_recorder())
        else:
            log.info("record hotkey ignored in state %s", state)

    def _start_meeting(self) -> None:
        """Open the streams first, then transition with the id of the real folder.

        The order matters: the transition carries the meeting id, and only
        :meth:`Recorder.start` knows it, because a folder collision can turn
        `2026-08-27_1400` into `2026-08-27_1400_2`.
        """
        recorder = Recorder(self.config)
        try:
            meeting = recorder.start()
        except RecorderError:
            # No audio device at all: stay idle rather than pretend to record.
            log.exception("could not start recording")
            return
        self.recorder = recorder
        if not self.machine.try_to(State.RECORDING, meeting_id=meeting.id):
            log.warning("recording refused by the state machine; closing %s", meeting.id)
            self._stop_recorder()

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
            # `referat rerun` can try again at build step 8.
            log.exception("transcription job for %s failed", meeting.id)
            self._notify(f"Transcription of {meeting.id} failed")
        finally:
            self.machine.end_job()
            # Ending a job while a new meeting records changes no state, but the
            # job count in status.json is still worth keeping honest.
            status.write_status(self.machine)

    # --- Notifications ------------------------------------------------------

    def _notify_transcribed(self, meeting: Meeting) -> None:
        """Say the meeting is done, and how many voices nobody has named yet.

        The count is the whole point of the message: a speaker only becomes a
        name if somebody runs `referat label`, and nothing else in Referat ever
        asks. With everybody recognised it says nothing extra.
        """
        unknown = voices.unknown_speakers(meeting)
        message = f"Transcribed {meeting.id}"
        if unknown:
            message += (
                f" ({len(unknown)} unknown voice{'s' if len(unknown) > 1 else ''} — "
                f"run: referat label {meeting.id})"
            )
        self._notify(message)

    def _notify(self, message: str) -> None:
        """A balloon from the tray icon. Never raises: a notification is not the work."""
        try:
            self.icon.notify(message, "Referat")
        except Exception:
            log.debug("could not show a notification", exc_info=True)

    # --- Menu ---------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(lambda _: f"Referat - {self.machine.state}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open meetings folder", self.open_meetings_folder, default=True),
            pystray.MenuItem("Open config", self.open_config),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.quit),
        )

    def open_meetings_folder(self) -> None:
        self._startfile(self.config.paths.meetings_dir)

    def open_config(self) -> None:
        self._startfile(self.config.source or paths.config_path())

    @staticmethod
    def _startfile(target: Path) -> None:
        try:
            os.startfile(target)
        except OSError:
            log.exception("could not open %s", target)

    def quit(self) -> None:
        """Stop the hotkeys, end any recording, and take the icon down."""
        log.info("quitting")
        self.hotkeys.stop()
        if self.machine.recording:
            # Never exit mid-meeting without closing it out first.
            log.warning("quit while %s; stopping the meeting", self.machine.state)
            self.machine.try_to(State.STOPPED)
            self._stop_recorder()
            self.machine.try_to(State.IDLE)
        self.power.close()
        status.clear_status()
        self.icon.stop()

    # --- Run ----------------------------------------------------------------

    def run(self) -> None:
        self.hotkeys.start()
        status.write_status(self.machine)
        log.info(
            "tray running; %s to record, %s to pause",
            self.config.hotkeys.toggle_record,
            self.config.hotkeys.toggle_pause,
        )
        try:
            self.icon.run()
        finally:
            self.hotkeys.stop()
            self.power.close()


def main() -> int:
    """Entry point for `referat-tray` and for `python -m referat.tray`."""
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

    try:
        App(config).run()
    except Exception:
        log.exception("tray app crashed")
        return 1
    log.info("tray exited")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
