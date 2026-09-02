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

from referat import build_info, paths
from referat.state import State, Transition
from referat.ui import icons

log = logging.getLogger(__name__)


class Bridge(QObject):
    """Carries a state transition from whatever thread made it to the GUI thread."""

    transitioned = Signal(object)

    def on_transition(self, transition: Transition) -> None:
        """The :class:`referat.state.Machine` listener. Called on any thread.

        Emitting is all it does. A queued signal is the one thing that is safe to
        do to Qt from a foreign thread, and this listener runs *after* the two
        the tray registers for itself, so a Qt failure here cannot cost the sleep
        hold or the `status.json` write.
        """
        self.transitioned.emit(transition)


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
        self.app.machine.add_listener(self.bridge.on_transition)
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

    def _paint(self, state: State) -> None:
        missing = self.app.missing_channels
        self.tray.setIcon(icons.state_icon(state, bool(missing)))
        self.tray.setToolTip(icons.tooltip(state, missing))
        self.state_action.setText(f"Referat - {state}")

    def notify(self, message: str) -> None:
        """A balloon from the tray icon, and a nudge to the list behind it.

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
