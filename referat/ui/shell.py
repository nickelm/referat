"""The Qt shell: the application object, the tray icon, and the event loop.

`QSystemTrayIcon` replaces `pystray` here, which is the point of step 20's
process model: **one process owns the icon, the hotkeys, the recorder and the
window, with one event loop and no IPC between them.** That is what lets the
window carry record, pause and stop buttons calling the same methods the hotkey
handlers call — no new CLI verb and, more to the point, no second path into the
state machine.

**Two things must never cost a recording, and they are separated here.** The
tray icon is required — a tray with no icon is a process nobody can see or quit —
and the *window* is not: it is built the first time somebody asks for it, inside
a `try`, and a window that fails to open is a log line, a balloon, and a tray
that still records. Same degradation diarization runs under.

**Transitions arrive on other threads.** :class:`referat.state.Machine` calls its
listeners on whichever thread made the transition — the `keyboard` hook thread
for a hotkey, a transcription thread for a job ending — and Qt widgets may only
be touched on the GUI thread. :class:`Bridge` is the crossing: a plain `QObject`
whose signal is emitted from the listener and delivered, queued, on the GUI
thread. Everything below :meth:`Bridge.transitioned` is single-threaded.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from referat import build_info, paths, progress
from referat.state import State, Transition
from referat.ui import icons

log = logging.getLogger(__name__)


class Bridge(QObject):
    """Carries a state transition or a notification to the GUI thread.

    **Both** cross, and the second one had to be added after the first: a
    transition went through this from the beginning, while `App.notify` reached
    straight into Qt from whatever thread called it — which is the transcription
    thread, every time a job ends. See :meth:`Shell.notify`.
    """

    transitioned = Signal(object)
    notified = Signal(str)
    progressed = Signal(object)

    def on_transition(self, transition: Transition) -> None:
        """The :class:`referat.state.Machine` listener. Called on any thread.

        Emitting is all it does. A queued signal is the one thing that is safe to
        do to Qt from a foreign thread, and this listener runs *after* the two
        the tray registers for itself, so a Qt failure here cannot cost the sleep
        hold or the `status.json` write.
        """
        self.transitioned.emit(transition)

    def on_progress(self, jobs: list[Any]) -> None:
        """The :mod:`referat.progress` listener. Called on any thread.

        Emitting is all it does, for the reason :meth:`on_transition` does no
        more: this arrives on a transcription thread and on the reader thread of
        a `claude` subprocess, several times a second while a channel is being
        transcribed, and every one of those would otherwise be a widget touched
        from the wrong place.
        """
        self.progressed.emit(jobs)


SELECTED_TEXT_FIX = (
    "QAbstractItemView::item:selected { color: palette(highlighted-text); }"
)
"""Selected rows are unreadable without this, and it is a Qt style bug rather than ours.

Qt 6's `windows11` style, **when a view has `alternatingRowColors` on**, paints a
selected row with the palette's `Highlight` -- a strong blue -- and then draws
its text in `Text` rather than `HighlightedText`. The result is black on dark
blue. With striping off the same style paints a soft grey selection and the text
stays readable, which is why this went unseen: every list here that shows data
sets striping, and every list that does not is a heading.

Measured rather than guessed, by rendering a `QListWidget` to a pixmap and
counting pixels: striping off gives 3,275 near-white pixels in the selected row
and 72 dark ones; striping on gives **zero** near-white and 491 dark. With this
rule: zero dark, 291 light. The row's background, the stripes and the selection
shape are pixel-identical either way, so this changes the one thing that was
wrong and nothing else.

`palette(highlighted-text)` rather than a colour, so a dark theme still gets a
readable one -- the same rule `referat.ui.icons` follows for glyphs. Set on the
`QApplication` because nine views across five pages have this problem and it is
one bug, not nine.
"""


class Shell:
    """The tray icon and, on demand, the command center behind it."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.qt = QApplication.instance() or QApplication([])
        self.qt.setApplicationName("Referat")
        self.qt.setOrganizationName("Referat")
        # Closing the command center must not end the process, because the
        # process is the recorder. The window refuses the close as well; both
        # halves are needed and neither is redundant.
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setStyleSheet(SELECTED_TEXT_FIX)

        self.window: Any | None = None
        self.tray = QSystemTrayIcon()
        self.state_action = QAction("Referat")
        self.state_action.setEnabled(False)
        self._build_menu()
        self.tray.activated.connect(self._on_activated)
        self._paint(self.app.machine.state)
        self.tray.show()

        self.bridge = Bridge()
        self.bridge.transitioned.connect(self._on_transition, Qt.ConnectionType.QueuedConnection)
        # Auto rather than Queued, unlike the transition above: `notify` is
        # called from the GUI thread too — `open_window`'s own failure path does
        # it — and a queued connection there would defer the message behind the
        # very event loop turn that is failing. Auto is direct on this thread and
        # queued from any other, which is exactly the rule.
        self.bridge.notified.connect(self._on_notified, Qt.ConnectionType.AutoConnection)
        self.bridge.progressed.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self.app.machine.add_listener(self.bridge.on_transition)
        progress.add_listener(self.bridge.on_progress)
        self.app.notify = self.notify

    # --- The menu -----------------------------------------------------------

    def _build_menu(self) -> None:
        menu = QMenu()
        menu.addAction(self.state_action)
        # Constant text, unlike the state line above: both timestamps are sampled
        # once at import and cannot change while this process lives. That is the
        # whole point of them -- see :mod:`referat.build_info`.
        build_action = menu.addAction(build_info.describe())
        build_action.setEnabled(False)
        menu.addSeparator()
        self.open_action = menu.addAction("Open command center")
        # Through a lambda, not bound directly: QAction.triggered carries a
        # `checked` bool, which would arrive as `open_window`'s meeting_id.
        self.open_action.triggered.connect(lambda: self.open_window())
        menu.setDefaultAction(self.open_action)
        menu.addAction("Open meetings folder").triggered.connect(
            lambda: self._open(self.app.config.paths.meetings_dir)
        )
        menu.addAction("Open config").triggered.connect(
            lambda: self._open(self.app.config.source or paths.config_path())
        )
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self.quit)
        # Kept on the instance: QMenu is parentless here, and a menu only
        # referenced by the C++ side of the tray icon is collected out from
        # under it by Python.
        self.menu = menu
        self.tray.setContextMenu(menu)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """A click on the icon opens the window; the menu is the right button.

        The command center rather than the meetings folder, which is what
        `pystray`'s default action opened until step 20 made the window the
        primary surface. The folder is still one menu item away.
        """
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_window()

    @staticmethod
    def _open(target: Any) -> None:
        """Hand a folder or a file to the desktop.

        `QDesktopServices.openUrl` rather than `os.startfile`, which is the call
        this replaced: the portability rule for :mod:`referat.ui` is that no
        module here makes a Windows-only call, and Qt has this one.
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            log.warning("could not open %s", target)

    # --- The window ---------------------------------------------------------

    def open_window(self, meeting_id: str | None = None, untagged: bool = False) -> None:
        """Show the command center, building it the first time. Never raises.

        This is the guarded half of step 20's rule that the window may never cost
        a recording. Everything above this line is up and recording before
        anything below it is imported, and a failure here leaves a tray that
        still works.

        The two arguments are passed straight through to
        :meth:`referat.ui.window.CommandCenter.show_window`, and exist so step
        16's on-stop toast has one call to make when it wants the untagged inbox
        at the meeting that just stopped.
        """
        try:
            if self.window is None:
                from referat.ui.window import CommandCenter

                self.window = CommandCenter(self.app)
            self.window.show_window(meeting_id=meeting_id, untagged=untagged)
        except Exception:
            log.exception("the command center would not open")
            self.window = None
            self.notify("The command center would not open - see the log.")

    # --- Reacting to the recorder -------------------------------------------

    def _on_transition(self, transition: Transition) -> None:
        """On the GUI thread, by the time this runs. See :class:`Bridge`."""
        self._paint(transition.to)
        if self.window is not None:
            self.window.on_state(transition.to)

    def _on_progress(self, jobs: list[Any]) -> None:
        """On the GUI thread, by the time this runs. See :class:`Bridge`.

        The tray tooltip is left alone: it says what the *recorder* is doing, and
        a transcription is not the recorder — the machine is `idle` throughout
        one, which is exactly what lets a new meeting start while it runs.
        """
        if self.window is not None:
            self.window.on_progress(jobs)

    def _paint(self, state: State) -> None:
        missing = self.app.missing_channels
        self.tray.setIcon(icons.state_icon(state, bool(missing)))
        self.tray.setToolTip(icons.tooltip(state, missing))
        self.state_action.setText(f"Referat - {state}")

    def notify(self, message: str) -> None:
        """`App.notify`, from any thread. Hands the message to the GUI thread.

        **This crossing is the whole of this method and it was missing.** Every
        transcription ends on the `transcribe` daemon thread and calls
        `App.notify` from there, and the body below — a tray balloon and a full
        rebuild of the meetings tree — ran on that thread, touching Qt from
        outside the GUI thread on every single job. It cost three trays: the
        process died with a `0xc0000374` heap corruption in `ntdll` seconds after
        finishing a transcript on 2026-09-02 twice and again on 2026-09-03,
        leaving no Python traceback because there was no Python exception.

        The transcript was never at risk — it is written, released and promoted
        before this runs — but the *recorder* was, since a dead tray records no
        meeting. The bug arrived with Qt at phase 1 and hid until the window had
        been opened, because with no window there was nothing but the balloon to
        get wrong.

        `Bridge` already existed for exactly this and carried only transitions.
        """
        self.bridge.notified.emit(message)

    def _on_notified(self, message: str) -> None:
        """A balloon from the tray icon, and a nudge to the list behind it.

        On the GUI thread, by the time this runs — see :meth:`notify`.

        Never raises: a notification is not the work. It is also the one place a
        finished transcription reaches the window, because ending a job while a
        new meeting is recording changes no state and so fires no transition —
        the list would otherwise keep describing a meeting as `transcribing`
        until somebody pressed F5.
        """
        try:
            self.tray.showMessage("Referat", message, icons.state_icon(State.IDLE))
        except Exception:
            log.debug("could not show a notification", exc_info=True)
        try:
            if self.window is not None and self.window.isVisible():
                self.window.refresh()
        except Exception:
            log.debug("could not refresh the command center", exc_info=True)

    # --- Running ------------------------------------------------------------

    def quit(self) -> None:
        self.app.shutdown()
        self.tray.hide()
        self.qt.quit()

    def run(self) -> int:
        log.info("Qt shell running; %s", build_info.describe())
        return int(self.qt.exec())


def run(app: Any) -> int:
    """Build the shell around an already-running :class:`referat.tray.App`, and loop."""
    return Shell(app).run()
