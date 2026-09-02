"""The command center window: the meetings list, the viewer, and the controls.

Phase 1 of build step 20, and **the whole of it reads**. Nothing here writes a
tag, a name or a `meta.json` key; the only thing it changes is the recorder, and
that goes through the very methods the two hotkeys call. Tagging is phase 2 and
labeling is phase 3, and keeping the first phase read-only is what makes it a
safe place to settle the toolkit question against real meetings.

**It calls the CLI's functions, never its subprocess.** `list_document`,
`show_document` and `transcript_document` are the same builders `referat list
--json` and friends print, so the window and the extension cannot come to
disagree about a meeting. It reads exactly one file for itself — `notes.md`,
which is prose to render and not a record to interpret — and no `meta.json`, no
`voices.json`, no `projects.json`.

**Qt widgets rather than QtWebEngine.** Decided here against the criteria step 20
wrote down, and the reasoning is in `TODO.md`; the short form is that
`QTextBrowser` renders Markdown, holds anchors and answers a custom URL scheme,
which is every criterion this phase had, and that the one argument for the web
view — reusing `referat-vscode/media/sidebar.js` — is an argument for keeping the
column this step exists to escape.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from referat import cli, paths
from referat.state import State
from referat.ui import tags
from referat.ui.viewer import Viewer

log = logging.getLogger(__name__)

COLUMNS = ("Meeting", "Title", "Duration", "Status", "Unnamed", "Projects")
ID_ROLE = Qt.ItemDataRole.UserRole
"""The meeting id, carried on the row so selection never parses a cell back."""

TAG_NUDGE = "Tag this..."
"""What an untagged row shows where its projects would be.

Text and not a button: `setItemWidget` fights `setUniformRowHeights(True)`, and
the row is already double-clickable. It is also why this lives here rather than
inside :func:`tags_text`, which stays a pure formatter — a call to action is not
a rendering of a tag list.
"""


def tags_text(tags: list[str], known: dict[str, str]) -> str:
    """A meeting's tags as a person reads them: display names, orphans marked.

    The counterpart of :func:`referat.cli.tags_cell`, which prints *ids* because
    the next thing typed after reading that table is `referat untag <meeting>
    <id>`. Here there is nothing to type, so the names win — and an id no project
    resolves still gets its trailing `?` rather than being dropped, for the
    reason it does everywhere else: a tag disappearing quietly off three meetings
    is how you lose track of what a meeting was about.

    The map comes from the document, which got it from `ProjectsDB.name_map`.
    Nothing here opens `projects.json`.
    """
    return ", ".join(known.get(tag, f"{tag}?") for tag in tags)


class CommandCenter(QMainWindow):
    """One window over the whole meetings folder, owned by the tray app."""

    hidden = Signal()
    """Emitted when the window is closed to the tray, so the shell can retitle a menu."""

    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self.setWindowTitle("Referat")
        self.resize(1180, 760)

        self._document: dict[str, Any] = {"meetings": [], "projects": {}}
        self._selected: str | None = None

        self.record_button = QPushButton("Record")
        self.pause_button = QPushButton("Pause")
        self.stop_button = QPushButton("Stop")
        self.state_label = QLabel()
        self.refresh_action = QAction("Refresh", self)
        self.refresh_action.setShortcut("F5")
        self.refresh_action.triggered.connect(self.refresh)
        self.addAction(self.refresh_action)

        self.record_button.clicked.connect(self.app.start_meeting)
        self.pause_button.clicked.connect(self.app.on_toggle_pause)
        self.stop_button.clicked.connect(self.app.stop_meeting)

        self.tag_button = QPushButton("Tags...")
        self.tag_button.setEnabled(False)
        self.tag_button.clicked.connect(self._on_tag)

        self.meetings = QTreeWidget()
        self.meetings.setColumnCount(len(COLUMNS))
        self.meetings.setHeaderLabels(list(COLUMNS))
        self.meetings.setRootIsDecorated(False)
        self.meetings.setAlternatingRowColors(True)
        self.meetings.setUniformRowHeights(True)
        self.meetings.currentItemChanged.connect(self._on_row_changed)
        self.meetings.itemActivated.connect(self._on_tag)

        # Filtering is presentation and lives here: `cli.list_document` keeps
        # `referat list`'s oldest-first order and learns none of it, exactly as
        # the sidebar's grouping stayed in the page at step 19.
        self.untagged_only = QCheckBox("Untagged only")
        self.untagged_only.toggled.connect(self._rebuild)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search meetings")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._rebuild)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.addWidget(self.untagged_only)
        filters.addWidget(self.search, 1)

        # The filter row belongs to the list rather than to the window, which is
        # also the shape phase 6 wants when this pane grows a dashboard beside it.
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addLayout(filters)
        left.addWidget(self.meetings, 1)
        left_pane = QWidget()
        left_pane.setLayout(left)

        self.viewer = Viewer()
        self.viewer.external_requested.connect(self._on_external_link)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_pane)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)

        controls = QHBoxLayout()
        for button in (self.record_button, self.pause_button, self.stop_button):
            button.setMinimumSize(QSize(92, 30))
            controls.addWidget(button)
        controls.addSpacing(12)
        controls.addWidget(self.state_label)
        controls.addStretch(1)
        # Last, at the right edge, and the space beside it is where phase 3's
        # "Label speakers..." goes. The order is the flow rule made visible: tag
        # first, then label. A nudge and never a gate.
        controls.addWidget(self.tag_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 6)
        layout.addLayout(controls)
        layout.addWidget(splitter, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.on_state(self.app.machine.state)

    # --- Closing ------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802  (Qt's own spelling)
        """Close to the tray, never quit.

        Qt's default is that closing the last window ends the application, which
        here would end the *recorder*. `QApplication.setQuitOnLastWindowClosed`
        is turned off in :mod:`referat.ui.shell` as well; both are needed, and
        this half is also what keeps the window's state so reopening is instant.
        """
        event.ignore()
        self.hide()
        self.hidden.emit()

    # --- Reading the meetings -----------------------------------------------

    def refresh(self) -> None:
        """Re-read the meetings and redraw them, keeping whatever row was selected.

        Called when the window is shown, on F5, on a state transition — see
        :meth:`on_state` — and after anything is written. Never on a timer: the
        tray registers this window as a :class:`referat.state.Machine` listener,
        so it learns transitions by callback. The extension's status bar keeps
        its poll because it is a different process with no callback to register.

        Split from :meth:`_rebuild` so the filter widgets can redraw without
        rescanning both meeting roots on every keystroke.
        """
        self._reload()
        self._rebuild()

    def _reload(self) -> None:
        """Re-read `list_document`, keeping the previous list if it will not read."""
        try:
            self._document = cli.list_document(self.app.config)
        except Exception:
            # A meetings folder that has gone away, a meta.json mid-write. The
            # window keeps the list it had rather than emptying itself.
            log.exception("could not read the meetings")

    def _matches(self, meeting: dict[str, Any], needle: str, known: dict[str, str]) -> bool:
        """The filter: the untagged toggle, then the search box.

        *Untagged* is the computed state of an empty `tags` list, here as
        everywhere — never a project, and never a value anything writes.

        The search matches the same five things step 19's does: the id (which
        carries the date), the title, the lifecycle text and the project display
        names. Matching the rendered strings rather than the raw fields is the
        point — what somebody types is what they can see.
        """
        if self.untagged_only.isChecked() and meeting["tags"]:
            return False
        if not needle:
            return True
        haystack = " ".join(
            (
                meeting["id"],
                meeting["title"],
                self._status_text(meeting),
                tags_text(meeting["tags"], known),
            )
        )
        return needle in haystack.casefold()

    def _rebuild(self) -> None:
        """Fill the tree from `self._document` through the filter. Reads no file."""
        known = self._document["projects"]
        needle = self.search.text().strip().casefold()
        wanted = self._selected
        previous = self.meetings.indexOfTopLevelItem(self.meetings.currentItem())

        self.meetings.clear()
        chosen: QTreeWidgetItem | None = None
        # Newest first: `referat list` is oldest-first so the newest meeting
        # lands next to the prompt, which is a property of a console and not of
        # a window. Ordering is presentation, exactly as it is in the sidebar.
        for meeting in reversed(self._document["meetings"]):
            if not self._matches(meeting, needle, known):
                continue
            carried = tags_text(meeting["tags"], known)
            item = QTreeWidgetItem(
                [
                    meeting["id"],
                    "" if meeting["title"] == meeting["id"] else meeting["title"],
                    meeting["duration"],
                    self._status_text(meeting),
                    str(len(meeting["unnamed"])) if meeting["unnamed"] else "",
                    carried or TAG_NUDGE,
                ]
            )
            if not carried:
                # The nudge, and in phase 2 the whole of it: an untagged row asks
                # to be tagged rather than showing a blank cell. Quiet, because it
                # is a suggestion and not a warning -- labeling an untagged
                # meeting stays possible and always will.
                item.setForeground(5, self.palette().placeholderText())
            item.setData(0, ID_ROLE, meeting["id"])
            for column in (2, 4):
                item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight)
            self.meetings.addTopLevelItem(item)
            if meeting["id"] == wanted:
                chosen = item
        for column in range(len(COLUMNS)):
            self.meetings.resizeColumnToContents(column)

        count = self.meetings.topLevelItemCount()
        if chosen is not None:
            self.meetings.setCurrentItem(chosen)
        elif count:
            # The selected meeting is gone from the view -- filtered out, or just
            # tagged while the untagged toggle is on, which is the inbox working.
            # Hold the position rather than jumping to the top, so working a
            # queue down advances to the next item.
            self.meetings.setCurrentItem(self.meetings.topLevelItem(min(max(previous, 0), count - 1)))
        else:
            self._selected = None
            self.viewer.clear_meeting(
                "Nothing matches." if self._document["meetings"] else "No meetings yet."
            )
        self._update_summary()

    @staticmethod
    def _status_text(meeting: dict[str, Any]) -> str:
        """The lifecycle, plus the one thing that is not it and earns its place.

        A run that lost a channel says so here. `2026-09-02_1001` was twenty-four
        minutes with no microphone in it and read `transcribed`, which is true of
        the transcription and worthless as a description of the meeting. The same
        sentence :func:`referat.cli.list_row` makes.
        """
        lost = meeting["missing_channels"]
        text = str(meeting["status"]).replace("_", " ")
        if lost:
            text += f" - no {'/'.join(lost)}"
        if meeting["staged"]:
            text += " (staging)"
        return text

    def _update_summary(self) -> None:
        meetings = self._document["meetings"]
        untagged = sum(1 for m in meetings if not m["tags"])
        unnamed = sum(1 for m in meetings if m["unnamed"])
        self.statusBar().showMessage(
            f"{len(meetings)} meetings   -   {untagged} untagged   -   "
            f"{unnamed} with speakers nobody has named"
        )

    def _on_row_changed(self, item: QTreeWidgetItem | None, _previous: object) -> None:
        self.tag_button.setEnabled(item is not None)
        if item is None:
            return
        self._selected = str(item.data(0, ID_ROLE))
        self._show(self._selected)

    # --- Tagging ------------------------------------------------------------

    def _on_tag(self, *_args: object) -> None:
        """Open the tag picker on the selected meeting. The one thing this window writes.

        Takes the id off `self._selected` rather than off a held
        `QTreeWidgetItem`: `_rebuild` destroys every item, and a transition
        arriving through the shell's `Bridge` can run it while the dialog is
        open, leaving a dangling reference to a deleted C++ object.

        Every exit refreshes, including a dismissed picker that created a
        project — the project exists whether or not it was applied, and a list
        that did not show it would be stale in exactly the way the sidebar's was.
        """
        meeting_id = self._selected
        if meeting_id is None:
            return
        carried = next(
            (m["tags"] for m in self._document["meetings"] if m["id"] == meeting_id), []
        )
        if tags.open_for(self, self.app.config, meeting_id, list(carried)):
            self.refresh()

    def _show(self, meeting_id: str) -> None:
        """Fill the viewer from the CLI's own documents, plus `notes.md` itself."""
        from referat.meeting import resolve_meeting

        meeting, why = resolve_meeting(self.app.config, meeting_id)
        if meeting is None:
            self.viewer.clear_meeting(why)
            return
        try:
            document = cli.transcript_document(self.app.config, meeting)
        except Exception:
            log.exception("could not read the transcript of %s", meeting_id)
            self.viewer.clear_meeting(f"Could not read the transcript of {meeting_id}.")
            return
        notes_path = meeting.dir / paths.NOTES_MD
        try:
            notes = notes_path.read_text(encoding="utf-8")
        except OSError:
            notes = None
        self.viewer.show_meeting(document, notes)
        self.setWindowTitle(f"Referat - {document['title']}")

    def _on_external_link(self, url: QUrl) -> None:
        """A link in the notes that is not a timestamp. Hand it to the desktop.

        `notes.md` is written by `/cleanup` and may carry a real URL. `[[Wikilinks]]`
        are not links at all in CommonMark and render as the text they are, which
        is what they should do until phase 5 gives a person somewhere to go.
        """
        QDesktopServices.openUrl(url)

    # --- Ambient state ------------------------------------------------------

    def on_state(self, state: State) -> None:
        """Render the recorder's state: the label, and which buttons mean anything.

        The three buttons are the *same* methods the hotkeys call — see
        :meth:`referat.tray.App.start_meeting` — so there is one path into the
        state machine and this only decides which of them is currently legal.
        Disabling rather than hiding, because a button that moves is a button you
        have to look for; the machine refuses an illegal transition anyway and
        logs it.
        """
        missing = self.app.missing_channels
        text = str(state)
        if missing and state in (State.RECORDING, State.PAUSED):
            text += f"  -  NO {'/'.join(missing).upper()}"
        self.state_label.setText(text)
        self.record_button.setEnabled(state in (State.IDLE, State.TRANSCRIBING))
        self.pause_button.setEnabled(state in (State.RECORDING, State.PAUSED))
        self.pause_button.setText("Resume" if state is State.PAUSED else "Pause")
        self.stop_button.setEnabled(state in (State.RECORDING, State.PAUSED))
        if self.isVisible():
            # A transition means a meeting was created, stopped or transcribed,
            # so the list is out of date. Only while visible: refreshing costs a
            # scan of both meeting roots and there is nobody to show it to.
            self.refresh()

    def show_window(self, meeting_id: str | None = None, untagged: bool = False) -> None:
        """Bring the window up from the tray, refreshed and in front.

        The two arguments are the hook step 16's on-stop toast needs: it wants to
        open the command center *on the untagged inbox*, at the meeting that just
        stopped. Both default to today's behaviour, so nothing that calls this
        without them changes.

        `untagged` only ever turns the filter **on**. Opening the window and
        silently hiding every tagged meeting because a previous caller asked for
        the inbox would be the window remembering a decision nobody made.
        """
        if untagged and not self.untagged_only.isChecked():
            # Signals blocked, or the toggle refreshes here and again below.
            self.untagged_only.blockSignals(True)
            self.untagged_only.setChecked(True)
            self.untagged_only.blockSignals(False)
        if meeting_id is not None:
            self._selected = meeting_id
        self.refresh()
        self.show()
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive
        )
        self.raise_()
        self.activateWindow()
